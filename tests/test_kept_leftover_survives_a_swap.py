"""
"Keep" on last week's leftover survives this week's meal changing
(2026-10-12, overnight Shop hunt).

    Last week's 2 lbs of chicken thighs went unbought. This week also has a
    stir fry wanting 2 lbs, so Shop asks "Still on the list from last week —
    keep or drop?". Keep: the line reads 4 lbs. Swap the stir fry for pasta
    and the chicken was gone from the list altogether — the 2 lbs the
    household had just said they still wanted with it.

keep_carried_over_item merged the kept amount onto this week's PLAN-owned
line, and a plan line is recomputed from its meals' ledger (and deleted
with the last of them). The merged line is now the household's own
(source NULL), as any person's amount on a plan line already is
(add_grocery_item's keep_standing, consolidate's `standing`), and the
keep's undo hands the line back to its plan (kept_into_plan_id).
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from conftest import household_today


@pytest.fixture
def weeks():
    tools.add_recipe("Stir Fry", ingredients=[{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"}])
    tools.add_recipe("Pasta", ingredients=[{"item": "Spaghetti", "qty": "1 lb", "category": "pantry"}])
    today = household_today()
    last = tools.create_weekly_plan((today - datetime.timedelta(days=8)).isoformat(), day_count=7)["weekly_plan_id"]
    tools.plan_meal((today - datetime.timedelta(days=5)).isoformat(), "Stir Fry", slot="dinner", weekly_plan_id=last)
    tools.approve_weekly_plan(last, approved_by="Emily")
    this = tools.create_weekly_plan(today.isoformat(), day_count=7)["weekly_plan_id"]
    night = (today + datetime.timedelta(days=2)).isoformat()
    tools.plan_meal(night, "Stir Fry", slot="dinner", weekly_plan_id=this)
    carried = tools.approve_weekly_plan(this, approved_by="Emily")["carried_over"]
    assert [c["item"] for c in carried] == ["Chicken thighs"]
    return {"this": this, "night": night, "carried_id": carried[0]["item_id"]}


def _chicken() -> list[dict]:
    return [r for r in tools.list_grocery_list("needed") if r["item"] == "Chicken thighs"]


def test_catch_swapping_the_meal_leaves_the_kept_amount(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    assert [r["quantity"] for r in _chicken()] == ["4 lbs"]
    tools.swap_meal_in_plan(weeks["this"], weeks["night"], "Pasta", slot="dinner")
    assert [r["quantity"] for r in _chicken()] == ["2 lbs"], "the kept 2 lbs were asked for out loud"


def test_catch_the_kept_line_is_the_households(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    assert _chicken()[0]["source_weekly_plan_id"] is None


def test_undo_hands_the_line_back_to_its_week(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    tools.undo_carried_over_decision(weeks["carried_id"])
    line = _chicken()
    assert [(r["quantity"], r["source_weekly_plan_id"]) for r in line] == [("2 lbs", weeks["this"])]
    # Plan-owned again, so the meal going takes the line with it.
    tools.swap_meal_in_plan(weeks["this"], weeks["night"], "Pasta", slot="dinner")
    assert _chicken() == []


def test_undo_after_the_meal_was_swapped_leaves_no_blank_line(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    tools.swap_meal_in_plan(weeks["this"], weeks["night"], "Pasta", slot="dinner")
    tools.undo_carried_over_decision(weeks["carried_id"])
    assert _chicken() == []
    assert [c["item"] for c in tools.list_carried_over_items()] == ["Chicken thighs"], "asked again"
