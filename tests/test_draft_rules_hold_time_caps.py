"""
The draft's household-rule re-picks hold the slot's time limit (hunt,
2026-10-28).

tools/draft_rules.py re-picks a third dinner of one protein ("not chicken
again this week") and a hot lunch for a child who needs it cold packed.
Both run AFTER cap_enforce in generation, so nothing later puts a slow pick
right — and the pick was gated on protein (or coldness) only. The picker
is TOLD the cap (`max_minutes` in the swap context); told is not
prevented. On main a 75-minute braise landed on a 20-minute rush
Wednesday, and a 45-minute cold lunch on a 20-minute weekday lunch.

The same shape as the first week's gap fill (Decision log, 2026-10-06 —
"The first week's gap fill holds the household's time limits").
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.tools import allergen_gate

from conftest import household_today
from test_rush_cap_enforced import (  # noqa: E402 — the fixtures this is about
    _dinners, _filler, _over_cap, _recipe, _week,
    capped, picker, run, stub_model,  # noqa: F401 — pytest fixtures
)
from test_draft_rules import _meals, _slot


def _monday(offset_weeks: int = 1) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _dish(name, minutes, protein, **extra):
    return {"meal_name": name, "main_protein": protein, "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 0, "cook_time_minutes": minutes,
            "ingredients": [{"item": f"{name} stuff", "qty": "1"}],
            "instructions": [f"Cook the {name}.", "Serve it."], **extra}


@pytest.fixture
def slow_then_quick(monkeypatch):
    """The re-pick's model: a slow dish first, a quick one once the slow
    one is on `avoid` — so a pass that holds the cap ends on the quick one,
    and one that doesn't takes the slow one."""
    asked = []

    def pick(context):
        asked.append(context)
        avoid = {a.lower() for a in context.get("avoid") or []}
        cold = context.get("slot") == "lunch"
        slow = "Grain Salad" if cold else "Slow Beef Braise"
        if slow.lower() not in avoid:
            return _dish(slow, 75, "beef")
        return _dish("Hummus Wrap" if cold else "Lentil Bowl", 10, "lentils")

    monkeypatch.setattr(allergen_gate, "quick_pick", pick)
    return asked


@pytest.fixture
def only_slow(monkeypatch):
    def pick(context):
        n = len(context.get("avoid") or [])
        return _dish(f"Slow Beef Braise {n}", 75, "beef")

    monkeypatch.setattr(allergen_gate, "quick_pick", pick)


def _chicken_week():
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    for name in ("Chicken A", "Chicken B", "Chicken C"):
        _recipe(name, 15, main_protein="chicken")
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[2]: ["rush"]})
    return week, dates


def test_a_third_chicken_dinner_on_a_rush_night_is_repicked_inside_its_time(
        capped, slow_then_quick, stub_model, run):
    """CATCH (main: Slow Beef Braise, 75 minutes, on the 20-minute rush Wednesday)."""
    week, dates = _chicken_week()
    stub_model(_week(dates, ["Chicken A", "Chicken B", "Chicken C"] + ["Quick Eggs"] * 4))

    plan_id, _ = run(week, None)

    wednesday = {r["date"]: r for r in _dinners(plan_id)}[dates[2]]
    assert wednesday["meal"] == "Lentil Bowl", wednesday["meal"]
    assert _over_cap(plan_id, week) == []
    assert any("again this week" in (c.get("replacing_because") or "") for c in slow_then_quick)


def test_when_nothing_quick_comes_back_the_drafted_dinner_stands(capped, only_slow, stub_model, run):
    """GUARD: the rule's own fallback — a slot nothing suitable came back
    for stands as drafted, rather than taking a pick over its time."""
    week, dates = _chicken_week()
    stub_model(_week(dates, ["Chicken A", "Chicken B", "Chicken C"] + ["Quick Eggs"] * 4))

    plan_id, _ = run(week, None)

    assert {r["date"]: r for r in _dinners(plan_id)}[dates[2]]["meal"] == "Chicken C"
    assert _over_cap(plan_id, week) == []


def test_a_cold_packed_lunch_repick_holds_the_weekday_lunch_limit(monkeypatch, slow_then_quick):
    """CATCH (main: a 75-minute Grain Salad on a 20-minute weekday lunch)."""
    from app import agent

    tools.add_member("Emily")
    tools.add_member("Mia")
    tools.save_member_needs(lunch_needs={"Mia": {"needs": ["cold_packed"]}})
    week = _monday()
    dates = tools._week_dates(week)
    days = []
    for d in dates:
        days += [_slot(d, "breakfast", "Oats"), _slot(d, "lunch", "Rice skillet", "chicken"),
                 _slot(d, "dinner", "Pasta primavera", "vegetarian")]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    lunches = [m["meal"] for m in _meals(plan_id) if m["slot"] == "lunch" and m["date"] in dates[:5]]
    assert lunches and "Grain Salad" not in lunches, lunches
    assert "Hummus Wrap" in lunches, lunches
