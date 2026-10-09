"""
The Move sheet names a night off as a night off (defect hunt, 2026-10-09).

Walked on a throwaway database: an approved week, tonight called off from
Today ("Night off" — the household is home, just not cooking), then Plan →
Saturday → Move. The sheet's Friday row read "Not planned" / "Nobody’s home
for dinner" while Today read "Night off — enjoy." and Plan's own Friday row
carried the night off's reason. `meal_move._empty_reason` knew two
constraints by name (already_past, skipped_day) and called every other
empty slot nobody-home — so a night off was reported as a trip nobody
took. (A meal the household's usual week has off — usual_week_off — falls
through the same way; not changed here, named so it isn't news.)

The refusal itself is right and unchanged: a planned_empty slot is never
offered as a decision (CLAUDE.md). Only its words change.
"""
from __future__ import annotations

import datetime

from app import tools
from app.tools import meal_move


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


# Next week, so no day is "already gone by".
WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)

_DISHES = ["Chicken Stew", "Sheet-Pan Gnocchi", "Black Bean Tacos", "Salmon Rice Bowls",
           "Turkey Chili", "Sunday Roast", "Lentil Soup"]


def _week() -> tuple[int, dict]:
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {}
    for day, name in zip((MON, TUE, WED, THU, FRI, SAT, SUN), _DISHES):
        tools.add_recipe(name, ingredients=[{"item": f"{name} onion", "qty": "1", "category": "produce"}],
                         default_servings=4)
        ids[day] = tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.approve_weekly_plan(plan)
    return plan, ids


def _row(options: dict, day: str) -> dict:
    return next(d for d in options["days"] if d["date"] == day)


def test_a_night_off_reads_as_a_night_off_on_the_move_sheet():
    plan, ids = _week()
    # Every night is planned, so there is nowhere to move Friday's dish:
    # the night off drops it and leaves Friday planned_empty.
    off = tools.tonight_night_off(FRI)
    assert off["status"] == "night_off" and off["kind"] == "drop"

    friday = _row(meal_move.move_options(plan, ids[SAT]), FRI)

    assert friday["ok"] is False, "a night off is still never offered as a place to cook"
    # The row's label stays "Not planned" (as a skipped day's does); the
    # line under it is where the why goes, and saying "Night off" twice
    # read as a stutter on the sheet.
    assert friday["meal"] == "Not planned"
    assert friday["reason"] == "Night off"
    assert "Nobody" not in friday["reason"]


def test_a_nobody_home_night_still_says_nobody_is_home():
    """The edge the change must not touch: a real away night keeps its words."""
    plan, ids = _week()
    tools.clear_plan_slot(plan, FRI, "dinner")
    tools.plan_slot_empty(plan, FRI, "dinner", reason="Out at the cottage.")

    friday = _row(meal_move.move_options(plan, ids[SAT]), FRI)

    assert friday["ok"] is False
    assert friday["meal"] == "Not planned"
    assert friday["reason"] == "Nobody’s home for dinner"


def _empty_row(constraint):
    import json
    return {"derived_from_json": json.dumps({"constraint": constraint})}


def test_a_meal_the_household_asked_not_to_plan_never_says_nobody_is_home():
    # The review of this branch found two more empty slots where people are
    # home but asked for no plan: the usual week's day off, and "no dinners
    # at all" (agent._finish_week_slots). Both said "Nobody’s home".
    for constraint in ("usual_week_off", "dinners_per_week:0"):
        assert meal_move._empty_reason(_empty_row(constraint), "dinner") == "No dinner planned that day"
        assert meal_move._nobody_home_row(_empty_row(constraint)) is False
    # The genuine one is untouched.
    assert meal_move._empty_reason(_empty_row("nobody_home"), "dinner") == "Nobody’s home for dinner"
    assert meal_move._nobody_home_row(_empty_row("nobody_home")) is True
