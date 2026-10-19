"""
A meal planned in chat onto a night of the week lands IN the week.

The chat's plan_meal tool has no weekly_plan_id in its schema, and the
system prompt and the change card both send the model to it for an open
night ("plan_meal is still right for an OPEN or EMPTY night"; the card's
row `problem` says "plan it with plan_meal instead"). Driven on main
(overnight hunt, 2026-10-19): Friday open on an approved week,
agent.TOOL_FUNCTIONS["plan_meal"](meal_date=Friday, meal="Bean Chili")
answered with an entry id, and a standalone row (weekly_plan_id NULL) was
written beside the open one — Plan still read "I'd like your call on this
one", Today still "Dinner needs your call", and the chili showed nowhere.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.db import get_conn


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)


def _recipe(name):
    tools.add_recipe(name, ingredients=[{"item": f"{name} beans", "qty": "1 can", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)


def _rows(day: str, slot: str = "dinner") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.weekly_plan_id, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _week(approve: bool) -> int:
    _recipe("Bean Chili")
    _recipe("Lentil Soup")
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.plan_meal(MON, "Lentil Soup", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_open(plan, FRI, "dinner", "Friday is up to you")
    tools.plan_slot_empty(plan, THU, "dinner", "You're out — nothing planned")
    if approve:
        tools.approve_weekly_plan(plan)
    return plan


@pytest.mark.parametrize("approve", [False, True])
def test_chat_planning_an_open_night_settles_it_on_the_plan(approve):
    plan = _week(approve)

    out = agent.TOOL_FUNCTIONS["plan_meal"](meal_date=FRI, meal="Bean Chili", slot="dinner")

    rows = _rows(FRI)
    assert len(rows) == 1, rows
    assert rows[0] == {"id": out["entry_id"], "weekly_plan_id": plan,
                       "slot_state": "planned", "meal": "Bean Chili"}
    friday = next(d for d in tools.get_week_menu(plan)["days"] if d["date"] == FRI)
    assert friday["dinner"]["title"] == "Bean Chili"


def test_an_approved_week_buys_for_the_settled_night():
    _week(approve=True)

    agent.TOOL_FUNCTIONS["plan_meal"](meal_date=FRI, meal="Bean Chili", slot="dinner")

    items = [g["item"] for g in tools.list_grocery_list(status="needed")]
    assert "Bean Chili beans" in items


def test_a_night_nobody_is_home_is_refused_not_planned_beside():
    plan = _week(approve=False)

    with pytest.raises(tools.SlotRefused):
        agent.TOOL_FUNCTIONS["plan_meal"](meal_date=THU, meal="Bean Chili", slot="dinner")

    assert [r["slot_state"] for r in _rows(THU)] == ["planned_empty"]
    assert all(r["weekly_plan_id"] == plan for r in _rows(THU))


def test_a_day_with_no_plan_is_still_a_one_off():
    _recipe("Bean Chili")
    later = (_monday() + datetime.timedelta(days=21)).isoformat()

    out = agent.TOOL_FUNCTIONS["plan_meal"](meal_date=later, meal="Bean Chili", slot="dinner")

    assert _rows(later) == [{"id": out["entry_id"], "weekly_plan_id": None,
                             "slot_state": "planned", "meal": "Bean Chili"}]


def test_a_slot_with_no_row_on_a_covered_day_joins_the_plan():
    plan = _week(approve=False)

    out = agent.TOOL_FUNCTIONS["plan_meal"](meal_date=WED, meal="Bean Chili", slot="lunch")

    assert _rows(WED, "lunch") == [{"id": out["entry_id"], "weekly_plan_id": plan,
                                    "slot_state": "planned", "meal": "Bean Chili"}]


# ------------------------------------------- review follow-up, 2026-10-19

def _needed(item: str) -> list[dict]:
    return [g for g in tools.list_grocery_list(status="needed") if g["item"] == item]


def test_a_no_row_slot_on_a_draft_buys_nothing_even_when_chat_was_told_yes():
    """A draft buys at approval. Buying here first left a line tied to a
    week that discard_draft_plan could then retire under it."""
    plan = _week(approve=False)

    agent.TOOL_FUNCTIONS["plan_meal"](meal_date=WED, meal="Bean Chili", slot="dinner",
                                      add_ingredients_to_grocery_list=True)
    assert _needed("Bean Chili beans") == []

    tools.discard_draft_plan(plan)
    assert _needed("Bean Chili beans") == []


def test_a_no_row_slot_on_an_approved_week_is_bought_once_with_the_default_flag():
    _week(approve=True)

    agent.TOOL_FUNCTIONS["plan_meal"](meal_date=WED, meal="Bean Chili", slot="dinner")

    lines = _needed("Bean Chili beans")
    assert len(lines) == 1 and lines[0]["quantity"] == "1 can", lines


def test_a_meal_the_household_switched_off_says_where_to_turn_it_on():
    plan = _week(approve=False)
    tools.plan_slot_empty(plan, WED, "breakfast", "You’ve asked me not to plan breakfasts — I’ve left this to you.",
                          derived_from={"constraint": "breakfasts_per_week:0"})

    with pytest.raises(tools.SlotRefused) as refused:
        agent.TOOL_FUNCTIONS["plan_meal"](meal_date=WED, meal="Bean Chili", slot="breakfast")

    assert str(refused.value) == (
        "You’ve asked me not to plan breakfasts, so nothing is planned or bought for this one. "
        "You can turn them back on in Preferences, under Your rhythm."
    )
    assert [r["slot_state"] for r in _rows(WED, "breakfast")] == ["planned_empty"]
