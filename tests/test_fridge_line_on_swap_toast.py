"""
The fridge line reaches the swap / nights-trade / night-off toast.

Loop Board "A fridge move's sentence is computed on every swap and shown on
no screen", decided by Emily 2026-10-12: "add the fridge line to the swap /
drag / night-off toast, as the Move sheet already does". The sentence is
defrost.thaw_move_sentence's (`thaw_now`); until this, the Plan screen's
Swap went through swap_in_place.apply_pick, which dropped it, and no screen
read it from the other two answers either.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app import tools
from app.tools import swap_in_place as sip
from conftest import household_today


def _d(offset: int) -> str:
    return (household_today() + datetime.timedelta(days=offset)).isoformat()


@pytest.fixture
def frozen_kitchen():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.set_dinner_window("6_8")
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday",
                         "friday", "saturday"])
    for name, item, qty, cat in (("Chicken Skewers", "Chicken thighs", "2 lbs", "meat"),
                                 ("Bean Chili", "Black beans", "1 can", "pantry")):
        tools.add_recipe(name, ingredients=[{"item": item, "qty": qty, "category": cat}],
                         prep_time_minutes=10, cook_time_minutes=20, default_servings=2)
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs",
                           category="meat", location="freezer")


def _plan_with(day: str, meal: str) -> tuple[int, int]:
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    entry_id = tools.plan_meal(day, meal, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.sync_defrost_tasks(plan_id)
    return plan_id, entry_id


def test_the_plan_screens_swap_carries_the_fridge_line(frozen_kitchen):
    """The Swap sheet's door (apply_pick) hands back what the swap said."""
    plan_id, entry_id = _plan_with(_d(2), "Bean Chili")

    out = sip.apply_pick(plan_id, sip._entry(plan_id, entry_id),
                         {"meal_name": "Chicken Skewers", "food_groups": [], "reason": "Something warm."},
                         correct_title=False)

    assert out["thaw_now"] == ["Move the Chicken thighs to the fridge today."]


def test_a_swap_with_nothing_to_thaw_carries_no_line(frozen_kitchen):
    plan_id, entry_id = _plan_with(_d(2), "Chicken Skewers")

    out = sip.apply_pick(plan_id, sip._entry(plan_id, entry_id),
                         {"meal_name": "Bean Chili", "food_groups": [], "reason": "Lighter."},
                         correct_title=False)

    assert "thaw_now" not in out


def test_all_four_toasts_add_the_line_and_hold_it_longer():
    src = Path(__file__).resolve().parents[1].joinpath("static", "shell.js").read_text()
    assert "function thawToast(said, out, holdMs)" in src
    # The Swap sheet's pick, the in-place swap, tonight traded with another
    # night (the nights swap the Plan drag used to make), and the night off.
    for call in ("thawToast(out.said || savedLine(picked.meal, 'swapped in'), out, SWAP_UNDO_MS)",
                 "thawToast(data.said || savedLine(mealDisplayName(",
                 "thawToast(said, out, 6000)",
                 "thawToast(tonightNightOffSaid(out), out, 6000)"):
        assert call in src, call
