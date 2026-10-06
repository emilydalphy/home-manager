"""
Approve must not wait on recipe web research (Loop Board card, 2026-10-06).

Production, the morning of 2026-10-06: approving a plan sat on "Writing up
the recipes…" for 37 minutes while the background pass researched dish
after dish (56s-1544s and up to 558K input tokens each, snacks included),
and approval only went ahead after giving up on it at 180s. Pinned here:
  * approval never waits on a web search — it tells the background pass
    to stop researching and write, and its own writes never search;
  * a dish's research stops at its wall-clock cap and the dish is written
    without it;
  * snacks, breakfasts, simple assemblies are never researched, and a plan
    researches at most RECIPE_RESEARCH_MAX_DISHES dishes;
  * a dish swapped off the plan mid-research stops being researched;
  * fetched pages are capped before they reach the model.
Every model call is stubbed; nothing reaches the network.
"""
import threading
import time

import pytest

from app import agent, tools
from app.tools import recipe_research as rr

from test_background_recipe_pass import background  # noqa: F401  (fixture)
from test_menu_first_generation import (  # noqa: F401  (fixtures)
    _menu_week, _week_start, household, menu_model, recipe_model,
)


@pytest.fixture(autouse=True)
def _research_on(monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH", "on")
    monkeypatch.setattr(rr, "start_link_check", lambda research: False)
    agent._RESEARCHED_PER_PLAN.clear() if hasattr(agent, "_RESEARCHED_PER_PLAN") else None


def _slow_research(monkeypatch, seconds: float):
    """research_dish_llm that sits on the 'web' until released or `seconds`."""
    entered, release = threading.Event(), threading.Event()
    calls = []

    def _llm(name, *a, **k):
        calls.append(name)
        entered.set()
        release.wait(seconds)
        return {"used": {"web_search": 1, "web_fetch": 0}}

    monkeypatch.setattr(agent, "research_dish_llm", _llm)
    return entered, release, calls


def _new_dinners(week):
    days = []
    for i, d in enumerate(_menu_week(week)):
        if d["slot"] == "dinner":
            d = {**d, "meal_name": f"Slow Dinner {i}"}
        days.append(d)
    return days


def test_approve_does_not_wait_on_the_background_passs_research(
    household, menu_model, recipe_model, background, monkeypatch,
):
    monkeypatch.setenv("RECIPE_RESEARCH_DISH_SECONDS", "30")
    entered, release, _calls = _slow_research(monkeypatch, 20)
    week = _week_start()
    recipe_model()
    menu_model(_new_dinners(week))
    plan = agent.generate_weekly_plan(week)
    try:
        assert entered.wait(5), "the background pass started researching"
        started = time.monotonic()
        result = tools.approve_weekly_plan(plan["weekly_plan_id"])
        took = time.monotonic() - started
    finally:
        release.set()
    assert result["status"] == "approved"
    assert took < 5, f"approval waited {took:.1f}s on a web search"
    assert tools.pending_recipes_for_plan(plan["weekly_plan_id"]) == []


def test_approvals_own_writes_never_search(household, menu_model, recipe_model, monkeypatch):
    # Background pass off (conftest): approval writes every dish itself.
    _entered, release, calls = _slow_research(monkeypatch, 0)
    week = _week_start()
    recipe_model()
    menu_model(_new_dinners(week))
    plan = agent.generate_weekly_plan(week)
    result = tools.approve_weekly_plan(plan["weekly_plan_id"])
    release.set()
    assert result["status"] == "approved"
    assert calls == [], "approval searched the web"


def _pending_dinner(name="Slow Curry"):
    tools.add_recipe(name=name, ingredients=[], details_pending=True,
                     dish_note="a slow curry", cuisine="Indian")
    return {**tools.get_recipe(name), "slot": "dinner", "cook_time_minutes": 40}


def _writer(spec):
    return {
        "ingredients": [{"item": "Chickpeas", "qty": "2 cans", "category": "pantry"},
                        {"item": "Onion", "qty": "1", "category": "produce"}],
        "instructions": ["Soften the onion.", "Add the chickpeas and simmer 20 minutes."],
        "default_servings": 4,
    }


def test_a_dishs_research_stops_at_its_time_cap(household, monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH_DISH_SECONDS", "0.3")
    _entered, release, calls = _slow_research(monkeypatch, 5)
    specs = []
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: specs.append(spec) or _writer(spec))
    started = time.monotonic()
    out = agent._write_one_pending_recipe(_pending_dinner(), "dinner", {"serves": 4, "must_not_contain": []}, [])
    took = time.monotonic() - started
    release.set()
    assert out["ok"] is True
    assert took < 2, f"the dish waited {took:.1f}s on research"
    assert calls and "research" not in specs[0], "written from the model's own knowledge"


def test_a_dish_swapped_off_the_plan_stops_being_researched(household, monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH_DISH_SECONDS", "30")
    monkeypatch.setattr(agent, "_RESEARCH_PLAN_CHECK_SECONDS", 0.0)
    monkeypatch.setattr(tools, "pending_recipes_for_plan", lambda plan_id: [])  # swapped away
    _entered, release, calls = _slow_research(monkeypatch, 5)
    started = time.monotonic()
    found = agent._research_capped("Slow Curry", _pending_dinner(), search=True, weekly_plan_id=999)
    release.set()
    assert found is None
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("recipe,worth", [
    ({"name": "Apple Slices with Cheese", "slot": "snack"}, False),
    ({"name": "Hummus with Carrot and Cucumber Sticks", "slot": "snack_pm"}, False),
    ({"name": "Masala Omelette", "slot": "breakfast", "cook_time_minutes": 10}, False),
    ({"name": "Caprese Plate", "slot": "lunch", "cook_time_minutes": 0}, False),
    ({"name": "Toast and Jam", "slot": "dinner", "ingredients": [1, 2, 3]}, False),
    ({"name": "Granola", "slot": "dinner", "tags": ["Snack"]}, False),
    ({"name": "Chana Masala", "slot": "dinner", "cook_time_minutes": 35}, True),
    ({"name": "Rajma", "slot": "lunch"}, True),
])
def test_snacks_breakfasts_and_simple_assemblies_are_not_researched(recipe, worth):
    assert rr.worth_researching(recipe) is worth


def test_a_plan_researches_at_most_max_dishes(household, monkeypatch):
    monkeypatch.setenv("RECIPE_RESEARCH_MAX_DISHES", "2")
    dinner = {"slot": "dinner", "cook_time_minutes": 30}
    granted = [agent._grant_research(4242, {**dinner, "id": i}) for i in range(4)]
    assert granted == [True, True, False, False]
    assert agent._grant_research(4242, {"slot": "snack", "id": 9}) is False


def test_the_default_budget_is_one_search_and_two_reads_and_pages_are_capped(monkeypatch):
    assert agent._dish_research_budget() == {"web_search": 1, "web_fetch": 2}
    monkeypatch.setenv("RECIPE_RESEARCH_SEARCHES", "3")
    monkeypatch.setenv("RECIPE_RESEARCH_FETCHES", "4")
    assert agent._dish_research_budget() == {"web_search": 3, "web_fetch": 4}


def test_fetched_pages_are_trimmed_on_the_tool_and_when_sent_back(household, monkeypatch):
    import types
    page = "x" * 60_000
    fetched = {"type": "web_fetch_tool_result", "tool_use_id": "g1",
               "content": {"type": "web_fetch_result", "url": "https://a.example/r",
                           "content": {"type": "document", "source": {"type": "text", "data": page}}}}
    calls = []

    class _Client:
        def __init__(self):
            self.messages = self
            self.turns = [([fetched], "pause_turn"), ([], "end_turn")]

        def create(self, **kwargs):
            calls.append(kwargs)
            content, stop = self.turns.pop(0)
            usage = types.SimpleNamespace(input_tokens=0, cache_read_input_tokens=0,
                                          cache_creation_input_tokens=0, output_tokens=0,
                                          server_tool_use=None)
            return types.SimpleNamespace(content=content, stop_reason=stop, usage=usage, model="m")

    monkeypatch.setattr(agent, "_client", lambda: _Client())
    agent.research_dish_llm("Chana Masala", budget={"web_search": 1, "web_fetch": 2})
    fetch_tool = next(t for t in calls[0]["tools"] if t.get("name") == "web_fetch")
    assert fetch_tool["max_content_tokens"] == 3000
    resent = calls[1]["messages"][-1]["content"][0]
    assert len(resent["content"]["content"]["source"]["data"]) == 3000 * 4
