"""
Chat runs on Haiku; recipes are written by the recipe writer (Emily,
2026-10-25, Option A of "replies come back quickly").

What this pins:
  * every chat round, and the warm-up, ask for agent.CHAT_MODEL, and the
    request carries no effort (Haiku 4.5 refuses the parameter);
  * an overloaded chat model falls back once to CHAT_FALLBACK_MODEL (MODEL),
    which does get its effort back;
  * seconds per round and per tool reach the home_manager logger;
  * the four chat tools that used to take whole recipes from the model
    (add_recipe, update_recipe_details, propose_plan_changes,
    set_big_meal_dish) hand the writing to generate_recipe_details_llm when
    the model gives intent only, keep the allergen check on what the writer
    wrote, and keep a person's own dictated lines working as before.

No real API calls: the client and the writer are stubbed throughout.
"""
from __future__ import annotations

import json
import logging
import types

import httpx2
import pytest
from anthropic import APIStatusError

from app import agent, tools
from app.tools import allergen_gate
from app.tools import usage as _usage


class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _text(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _reply(content, stop="end_turn"):
    return types.SimpleNamespace(content=content, stop_reason=stop, usage=_Usage())


class _FakeMessages:
    def __init__(self, responses, raises=None):
        self._responses = list(responses)
        self._raises = list(raises or [])
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self._raises:
            raise self._raises.pop(0)
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses, raises=None):
    fake = _FakeMessages(responses, raises)
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))
    return fake


def _overloaded():
    resp = httpx2.Response(529, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return APIStatusError("overloaded", response=resp, body=None)


@pytest.fixture(autouse=True)
def _clean_refusals():
    allergen_gate._RECIPE_REFUSALS.clear()
    agent._REFUSED_DRAFTS.clear()
    # run_agent_turn sets this ContextVar in the test thread, and it would
    # otherwise outlive the test: a later /api/chat with a stubbed turn
    # records whatever this left behind (its model included).
    token = agent.LAST_TURN_USAGE.set({})
    yield
    agent.LAST_TURN_USAGE.reset(token)
    allergen_gate._RECIPE_REFUSALS.clear()
    agent._REFUSED_DRAFTS.clear()


@pytest.fixture
def writer(monkeypatch):
    """The recipe writer, stubbed: hands back each scripted recipe in turn
    (the last one repeats) and records every spec it was given."""
    state = {"specs": [], "recipes": [_recipe()]}

    def fake(spec):
        state["specs"].append(spec)
        recipes = state["recipes"]
        return recipes.pop(0) if len(recipes) > 1 else recipes[0]

    monkeypatch.setattr(agent, "generate_recipe_details_llm", fake)
    # The amounts check can call the model to repair a recipe; these
    # recipes are not under test for their arithmetic.
    monkeypatch.setattr(agent, "_settle_recipe_amounts",
                        lambda name, ings, steps, servings: (steps, ings, {"ok": True}))
    return state


def _recipe(*items, steps=("Sear the chicken in a hot pan, 5 minutes.", "Add the tomatoes and simmer 10 minutes.")):
    items = items or ("Chicken thighs", "Tomatoes", "Cucumber")
    return {
        "ingredients": [{"item": i, "qty": "1 lb", "category": "produce"} for i in items],
        "instructions": list(steps),
        "default_servings": 4,
        "prep_time_minutes": 10,
        "cook_time_minutes": 20,
    }


def _run_one_tool(monkeypatch, name, tool_input):
    fake = _stub_client(monkeypatch, [
        _reply([_tool(name, tool_input)], stop="tool_use"),
        _reply([_text("Done.")]),
    ])
    _reply_text, conversation = agent.run_agent_turn([], "(test)")
    result = conversation[-2]["content"][0]
    return fake, result


# --------------------------------------------------------------------------
# The model, the fallback, the timing lines
# --------------------------------------------------------------------------


def test_chat_runs_on_haiku_and_sends_it_no_effort(monkeypatch):
    fake = _stub_client(monkeypatch, [_reply([_text("Hi.")])])

    agent.run_agent_turn([], "hello")

    assert agent.CHAT_MODEL.startswith("claude-haiku-4-5")
    request = fake.requests[0]
    assert request["model"] == agent.CHAT_MODEL
    assert "effort" not in (request.get("output_config") or {}), (
        "Haiku 4.5 refuses output_config.effort — every chat round would 400"
    )
    # The turn says which model it ran on, for pricing, and that model has a rate.
    assert agent.LAST_TURN_USAGE.get()["model"] == agent.CHAT_MODEL
    assert agent.CHAT_MODEL in _usage._RATES_PER_MTOK


def test_chat_cost_is_priced_per_row_at_the_model_it_ran_on():
    """Rows from before the model column (NULL) ran on Sonnet; new rows name
    their model; a model with no rate is reported unpriced, never a crash."""
    tok = {"rounds": 1, "input_tokens": 1_000_000, "output_tokens": 0}
    tools.record_chat_turn(tok)                                   # pre-switch: Sonnet
    tools.record_chat_turn({**tok, "model": agent.CHAT_MODEL})    # Haiku
    assert tools.get_usage_summary(days=7)["cost"]["input"] == round(2.00 + 1.00, 6)

    tools.record_chat_turn({**tok, "model": "claude-not-in-the-table"})
    cost = tools.get_usage_summary(days=7)["cost"]
    assert cost["unpriced"] is True and cost["input"] == round(2.00 + 1.00, 6)


def test_the_warm_up_warms_the_chat_models_cache(monkeypatch):
    fake = _stub_client(monkeypatch, [_reply([])])

    assert agent.warm_chat_cache() is True
    assert fake.requests[0]["model"] == agent.CHAT_MODEL


def test_an_overloaded_chat_model_falls_back_once_to_sonnet(monkeypatch):
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    fake = _stub_client(monkeypatch, [_reply([_text("Still here.")])],
                        raises=[_overloaded(), _overloaded(), _overloaded()])

    reply, _ = agent.run_agent_turn([], "hello")

    assert reply == "Still here."
    assert [r["model"] for r in fake.requests] == [agent.CHAT_MODEL] * 3 + [agent.MODEL]
    assert agent.CHAT_FALLBACK_MODEL == agent.MODEL != agent.CHAT_MODEL
    # Sonnet takes effort, so the fallback round gets its chat effort back.
    assert fake.requests[-1]["output_config"] == agent._effort_config("chat")


def test_seconds_per_round_and_per_tool_are_logged(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="home_manager")
    _run_one_tool(monkeypatch, "list_recipes", {})

    lines = [r.getMessage() for r in caplog.records if r.name == "home_manager"]
    assert any(l.startswith("run_agent_turn round 1 took ") and agent.CHAT_MODEL in l for l in lines)
    assert any(l.startswith("run_agent_turn tool list_recipes took ") and "(round 1)" in l for l in lines), lines
    assert any(l.startswith("run_agent_turn finished: total=") for l in lines)


# --------------------------------------------------------------------------
# add_recipe
# --------------------------------------------------------------------------


def test_add_recipe_from_a_name_and_brief_is_written_by_the_recipe_writer(monkeypatch, writer):
    _fake, result = _run_one_tool(monkeypatch, "add_recipe", {
        "name": "Greek Chicken Skewers", "brief": "with tomato and cucumber, weeknight-quick",
        "cuisine": "Greek",
    })

    assert not result.get("is_error"), result["content"]
    spec = writer["specs"][0]
    assert spec["name"] == "Greek Chicken Skewers"
    assert spec["dish_note"] == "with tomato and cucumber, weeknight-quick"
    assert spec["cuisine"] == "Greek"
    assert "must_not_contain" in spec and "serves" in spec, "the household facts Approve's writer is told"
    saved = tools.get_recipe("Greek Chicken Skewers")
    assert [i["item"] for i in saved["ingredients"]][:3] == ["Chicken thighs", "Tomatoes", "Cucumber"]
    assert saved["instructions"] == _recipe()["instructions"]


def test_a_dictated_recipe_is_saved_as_given_and_the_writer_is_not_called(writer):
    agent.TOOL_FUNCTIONS["add_recipe"](
        name="Nana's Soup", ingredients=[{"item": "Carrots", "qty": "2"}],
        instructions=["Simmer the carrots 20 minutes."],
    )

    assert writer["specs"] == []
    assert tools.get_recipe("Nana's Soup")["instructions"] == ["Simmer the carrots 20 minutes."]


def test_the_writers_recipe_is_held_to_the_allergy_and_retried_once(writer):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    writer["recipes"] = [_recipe("Flour", "Buttermilk"), _recipe("Flour", "Oat milk")]

    agent.TOOL_FUNCTIONS["add_recipe"](name="Fluffy Pancakes", brief="weekend pancakes", slot="breakfast")

    assert len(writer["specs"]) == 2
    assert writer["specs"][1].get("previous_attempt_included"), "the retry names what to leave out"
    items = [i["item"].lower() for i in tools.get_recipe("Fluffy Pancakes")["ingredients"]]
    assert "buttermilk" not in items and "oat milk" in items


def test_a_writer_that_keeps_writing_the_allergen_saves_nothing(writer):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    writer["recipes"] = [_recipe("Flour", "Buttermilk")]

    with pytest.raises(tools.SlotRefused, match="Not saved"):
        agent.TOOL_FUNCTIONS["add_recipe"](name="Fluffy Pancakes", brief="weekend pancakes")

    assert len(writer["specs"]) == 2
    assert tools.existing_recipe_named("Fluffy Pancakes") is None


def test_a_name_that_names_the_allergen_costs_no_writer_call(writer):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])

    with pytest.raises(tools.SlotRefused):
        agent.TOOL_FUNCTIONS["add_recipe"](name="Buttermilk Pancakes", brief="weekend pancakes")

    assert writer["specs"] == []


# --------------------------------------------------------------------------
# "Save it anyway" (review, 2026-10-25: it used to re-write with no check)
# --------------------------------------------------------------------------


@pytest.fixture
def egg_and_dairy():
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["egg allergy"])
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["dairy free"])


def test_save_it_anyway_saves_exactly_the_refused_draft(writer, egg_and_dairy):
    writer["recipes"] = [_recipe("Flour", "Buttermilk")]
    allergen_gate.begin_chat_turn()
    with pytest.raises(tools.SlotRefused, match="buttermilk"):
        agent.TOOL_FUNCTIONS["add_recipe"](name="Fluffy Pancakes", brief="weekend")
    # Were the dish written again now, this is what would come back.
    writer["recipes"] = [_recipe("Flour", "Buttermilk", "Eggs")]

    allergen_gate.begin_chat_turn()
    agent.TOOL_FUNCTIONS["add_recipe"](name="Fluffy Pancakes", brief="weekend", override=True)

    assert len(writer["specs"]) == 2, "the accepted draft is saved, not re-written"
    items = [i["item"] for i in tools.get_recipe("Fluffy Pancakes")["ingredients"]]
    assert "Buttermilk" in items and "Eggs" not in items


def test_save_it_anyway_on_a_refused_name_still_checks_every_other_allergy(writer, egg_and_dairy):
    allergen_gate.begin_chat_turn()
    with pytest.raises(tools.SlotRefused):
        agent.TOOL_FUNCTIONS["add_recipe"](name="Buttermilk Pancakes", brief="weekend")
    writer["recipes"] = [_recipe("Flour", "Buttermilk", "Eggs"), _recipe("Flour", "Buttermilk")]

    allergen_gate.begin_chat_turn()
    agent.TOOL_FUNCTIONS["add_recipe"](name="Buttermilk Pancakes", brief="weekend", override=True)

    told = writer["specs"][0]["must_not_contain"]
    assert any("egg" in str(m).lower() for m in told), "the egg allergy is still told to the writer"
    assert not any("dairy" in str(m).lower() for m in told), "the accepted one is not"
    assert writer["specs"][1].get("previous_attempt_included"), "the egg draft was caught and re-written"
    items = [i["item"] for i in tools.get_recipe("Buttermilk Pancakes")["ingredients"]]
    assert "Eggs" not in items and "Buttermilk" in items


def test_override_with_nothing_refused_is_not_honoured(writer, egg_and_dairy):
    writer["recipes"] = [_recipe("Flour", "Buttermilk")]
    allergen_gate.begin_chat_turn()
    with pytest.raises(tools.SlotRefused):
        agent.TOOL_FUNCTIONS["add_recipe"](name="Fluffy Pancakes", brief="weekend", override=True)
    assert tools.existing_recipe_named("Fluffy Pancakes") is None


# --------------------------------------------------------------------------
# update_recipe_details
# --------------------------------------------------------------------------


def test_changing_a_recipes_steps_goes_to_the_writer_and_keeps_its_ingredients(writer):
    tools.add_recipe("Chili", [{"item": "Ground beef", "qty": "1 lb"}, {"item": "Kidney beans", "qty": "1 can"}],
                     instructions=["Brown the beef.", "Add beans, simmer 30 minutes."])
    before = tools.get_recipe("Chili")["ingredients"]
    writer["recipes"] = [_recipe("Something else", steps=("Brown the beef in the pressure cooker.",
                                                          "Add beans, pressure-cook 10 minutes."))]

    agent.TOOL_FUNCTIONS["update_recipe_details"](recipe_name="Chili", change="do it in the pressure cooker")

    spec = writer["specs"][0]
    assert spec["change_requested"] == "do it in the pressure cooker"
    assert spec["current_recipe"]["instructions"] == ["Brown the beef.", "Add beans, simmer 30 minutes."]
    after = tools.get_recipe("Chili")
    assert after["instructions"][0] == "Brown the beef in the pressure cooker."
    assert after["ingredients"] == before, "this door never changes what the dish is made of"


EGG_STEPS = ("Dip the chicken in beaten egg, then the breadcrumbs.", "Air-fry at 200C for 14 minutes.")
CLEAN_STEPS = ("Brush the chicken with oil, then the breadcrumbs.", "Air-fry at 200C for 14 minutes.")


@pytest.fixture
def pan_chicken():
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["egg allergy"])
    tools.add_recipe("Pan Chicken", [{"item": "Chicken thighs", "qty": "1 lb"}, {"item": "Breadcrumbs", "qty": "1 cup"}],
                     instructions=["Coat the chicken in breadcrumbs.", "Pan-fry 6 minutes a side."])


def test_a_rewrite_that_reaches_for_an_allergen_is_retried_then_refused(writer, pan_chicken):
    writer["recipes"] = [_recipe(steps=EGG_STEPS)]

    with pytest.raises(tools.SlotRefused, match="egg"):
        agent.TOOL_FUNCTIONS["update_recipe_details"](recipe_name="Pan Chicken", change="do it in the air fryer")

    assert len(writer["specs"]) == 2 and writer["specs"][1].get("previous_attempt_included")
    assert tools.get_recipe("Pan Chicken")["instructions"][0] == "Coat the chicken in breadcrumbs."


def test_a_rewrite_caught_once_saves_the_clean_steps(writer, pan_chicken):
    writer["recipes"] = [_recipe(steps=EGG_STEPS), _recipe(steps=CLEAN_STEPS)]

    agent.TOOL_FUNCTIONS["update_recipe_details"](recipe_name="Pan Chicken", change="do it in the air fryer")

    assert tools.get_recipe("Pan Chicken")["instructions"] == list(CLEAN_STEPS)


def test_writing_steps_for_a_recipe_with_none_carries_the_change_and_the_gate(writer, pan_chicken):
    tools.add_recipe("Plain Chicken", [{"item": "Chicken thighs", "qty": "1 lb"}])
    writer["recipes"] = [_recipe(steps=EGG_STEPS)]

    with pytest.raises(tools.SlotRefused):
        agent.TOOL_FUNCTIONS["update_recipe_details"](recipe_name="Plain Chicken", change="air fryer, crispy")

    assert writer["specs"][0]["change_requested"] == "air fryer, crispy"
    assert tools.get_recipe("Plain Chicken")["instructions"] == []


# --------------------------------------------------------------------------
# propose_plan_changes — through the card's real gate
# --------------------------------------------------------------------------

from conftest import household_today  # noqa: E402
import datetime as _dt  # noqa: E402

DAY1 = household_today().isoformat()
DAY2 = (household_today() + _dt.timedelta(days=1)).isoformat()


def _a_week():
    for name in ("Pork Chops", "Chili Night"):
        tools.add_recipe(name, ingredients=[{"item": name.split()[0], "qty": "1 lb", "category": "meat/seafood"}])
    plan_id = tools.create_weekly_plan(DAY1)["weekly_plan_id"]
    tools.plan_meal(DAY1, "Pork Chops", slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    tools.plan_meal(DAY2, "Chili Night", slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    return plan_id


def _rows(*names_per_day):
    return [{"date": day, "slot": "dinner", "action": "change",
             "candidates": [{"meal_name": n, "reason": "Fits the week", "brief": n.lower()} for n in names]}
            for day, names in zip((DAY1, DAY2), names_per_day)]


def _by_name(monkeypatch, recipes: dict, seen: list):
    def fake(spec):
        seen.append({"name": spec["name"], "household": tools.household_id(),
                     "must_not_contain": list(spec.get("must_not_contain") or [])})
        return recipes[spec["name"]]
    monkeypatch.setattr(agent, "generate_recipe_details_llm", fake)


def test_the_change_card_writes_new_dishes_and_its_own_gate_holds_a_clash(monkeypatch, writer):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    plan_id = _a_week()
    seen: list = []
    _by_name(monkeypatch, {"Lemon Cod": _recipe("Cod", "Lemon"),
                           "Fish Pie": _recipe("Haddock", "Heavy cream")}, seen)
    rows = _rows(["Lemon Cod"], ["Fish Pie"])

    card = agent.TOOL_FUNCTIONS["propose_plan_changes"](weekly_plan_id=plan_id, rows=rows)

    first, second = card["rows"]
    assert [c["meal_name"] for c in first["candidates"]] == ["Lemon Cod"]
    assert first["problem"] is None
    assert second["candidates"] == [] and "Fish Pie" in second["problem"], second
    assert "ingredients" not in rows[0]["candidates"][0], "the model's rows are not mutated"


def test_the_change_card_writes_in_the_households_own_context(monkeypatch, writer):
    from app import households
    hid2 = households.create_household("Beta", "beta-passphrase-xyz")
    seen: list = []
    _by_name(monkeypatch, {n: _recipe("Chicken", "Rice") for n in ("Dish A", "Dish B", "Dish C")}, seen)
    with tools.use_household(hid2):
        tools.add_member("Bea")
        tools.set_member_dietary_restrictions("Bea", ["peanut allergy"])
        plan_id = _a_week()
        card = agent.TOOL_FUNCTIONS["propose_plan_changes"](
            weekly_plan_id=plan_id, rows=_rows(["Dish A", "Dish B"], ["Dish C"]))

    assert sorted(s["name"] for s in seen) == ["Dish A", "Dish B", "Dish C"]
    # Each writer thread ran as household 2 and was told household 2's allergy.
    assert {s["household"] for s in seen} == {hid2}
    assert all(any("peanut" in str(m).lower() for m in s["must_not_contain"]) for s in seen)
    assert len(card["rows"][0]["candidates"]) == 2


def test_nothing_is_written_for_a_plan_or_slot_the_card_would_drop(monkeypatch, writer):
    plan_id = _a_week()
    seen: list = []
    _by_name(monkeypatch, {}, seen)

    out = agent.TOOL_FUNCTIONS["propose_plan_changes"](weekly_plan_id=999999, rows=_rows(["Lemon Cod"]))
    assert "error" in out
    far = [{"date": "2031-01-06", "slot": "dinner", "action": "change",
            "candidates": [{"meal_name": "Lemon Cod", "reason": "r"}]}]
    agent.TOOL_FUNCTIONS["propose_plan_changes"](weekly_plan_id=plan_id, rows=far)
    assert seen == []


def test_the_card_writes_no_more_candidates_than_it_can_show(monkeypatch, writer):
    plan_id = _a_week()
    seen: list = []
    names = [f"Dish {i}" for i in range(6)]
    _by_name(monkeypatch, {n: _recipe("Chicken", "Rice") for n in names}, seen)

    agent.TOOL_FUNCTIONS["propose_plan_changes"](weekly_plan_id=plan_id, rows=_rows(names))

    from app.tools import proposals
    assert len(seen) == proposals._MAX_CANDIDATES


# --------------------------------------------------------------------------
# set_big_meal_dish
# --------------------------------------------------------------------------


def test_a_new_big_meal_dish_is_written_for_the_whole_table(monkeypatch, writer):
    monkeypatch.setattr(tools, "get_big_meal", lambda d: {"hosting": True, "eaters": 10,
                                                          "guest_notes": "one guest is nut-free"})
    seen = {}
    monkeypatch.setattr(tools, "set_big_meal_dish",
                        lambda date_str, name, role="side", ingredients=None, **kw:
                        seen.update(ingredients=ingredients, **kw) or {"ok": True})

    agent.TOOL_FUNCTIONS["set_big_meal_dish"](date_str="2026-12-25", name="Roasted Carrots",
                                              role="side", brief="honey-glazed")

    spec = writer["specs"][0]
    assert spec["serves"] == 10 and spec["guests_cannot_have"] == "one guest is nut-free"
    assert seen["ingredients"][0]["item"] == "Chicken thighs" and seen["instructions"]


def test_a_big_meal_refusal_offers_no_override_it_does_not_have(monkeypatch, writer):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["dairy free"])
    monkeypatch.setattr(tools, "get_big_meal", lambda d: {"hosting": True, "eaters": 6, "guest_notes": ""})
    writer["recipes"] = [_recipe("Potatoes", "Heavy cream")]

    with pytest.raises(tools.SlotRefused) as refused:
        agent.TOOL_FUNCTIONS["set_big_meal_dish"](date_str="2026-12-25", name="Mash", role="side")
    assert "anyway" not in str(refused.value)
