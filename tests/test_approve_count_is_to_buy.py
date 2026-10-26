"""
"Week approved · N things to buy." counts what is to buy (defect hunt,
2026-10-21).

Found driving a first week end to end: approving put nine lines on the list
and the oats in "Pantry staples this week" (status 'spice', unticked — the
list assumes a pantry), and the toast said "10 things to buy". Today's
first-open count and the week's receipt both count 'needed' only, so three
screens gave two numbers for one list. approve_weekly_plan now leaves the
lines that landed in an assumed-at-home section out of groceries_added, and
the draft's preview leaves them out the same way.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.tools import spices


def _next_monday() -> str:
    today = household_today()
    return (today + datetime.timedelta(days=7 - today.weekday())).isoformat()


def _week_with_pantry_and_spice() -> tuple[str, int]:
    assert spices.is_assumed("oats") and spices.is_assumed("cumin")
    tools.add_recipe("Spiced Oat Bake", ingredients=[
        {"item": "oats", "qty": "2 cups"},
        {"item": "cumin", "qty": "1 tsp"},
        {"item": "black beans", "qty": "1 tin"},
        {"item": "onion", "qty": "1"},
    ])
    week = _next_monday()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(week, "Spiced Oat Bake", slot="dinner", weekly_plan_id=plan_id)
    return week, plan_id


def _needed() -> set[str]:
    return {r["item"].lower() for r in tools.list_grocery_list("needed")}


def test_the_toast_count_is_the_lines_to_buy(signed_in):
    week, plan_id = _week_with_pantry_and_spice()
    assert tools.preview_plan_grocery_impact(plan_id)["would_add_count"] == 2

    res = signed_in.post(f"/api/week/{week}/approve", json={})

    assert res.status_code == 200
    assert _needed() == {"black beans", "onion"}
    assert res.json()["groceries_added"] == 2, "oats and cumin wait in their own sections, unticked"


def test_a_households_own_line_a_plan_merged_onto_still_counts():
    tools.add_grocery_item("oats", "1 cup")          # typed by a person: a thing to buy
    _, plan_id = _week_with_pantry_and_spice()

    result = tools.approve_weekly_plan(plan_id)

    assert "oats" in _needed()
    assert result["groceries_added_count"] == 3


def test_a_week_of_only_pantry_lines_says_nothing_to_buy():
    tools.add_recipe("Porridge", ingredients=[{"item": "oats", "qty": "1 cup"}])
    week = _next_monday()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(week, "Porridge", slot="breakfast", weekly_plan_id=plan_id)

    result = tools.approve_weekly_plan(plan_id)

    assert result["groceries_added_count"] == 0
    assert tools.approve_weekly_plan(plan_id)["was_already_approved"] is True
