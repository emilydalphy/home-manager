"""
"Sauces this week" and "Pantry staples this week" show the week's own
amount, never a spice-rack teaspoon reading of it (overnight defect hunt,
2026-10-21).

The rows share the spice machinery, and the spice rows show a measured
total in tsp/tbsp (spices.week_total). That reading belongs to rack things
only — the lines the ingest measures in teaspoons and buys as one jar. Read
the same way, a recipe's "2 cups" of rice showed "32 tbsp" on the Shop tab
and an old recipe's "1 box" of pasta "about ¼ tsp".
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools


def _monday():
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


def _rows():
    return {sp["item"]: sp for sp in tools.list_spices_this_week()["items"]}


def _approve(ingredients):
    tools.add_member("Emily")
    tools.add_member("Jamie")
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.add_recipe("Weeknight Bowl", default_servings=2, ingredients=ingredients)
    tools.plan_meal(_monday().isoformat(), "Weeknight Bowl", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    return _rows()


def test_pantry_and_sauce_rows_show_the_lines_amount_not_teaspoons():
    rows = _approve([
        {"item": "Chicken breast", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Rice", "qty": "2 cups", "category": "pantry"},
        {"item": "Pasta", "qty": "1 box", "category": "pantry"},
        {"item": "Ketchup", "qty": "1 bottle", "category": "pantry"},
    ])
    assert rows["Rice"]["section"] == "pantry"
    assert rows["Rice"]["quantity"] == "2 cups"
    assert rows["Rice"]["total"] == ""          # was "32 tbsp"
    assert rows["Pasta"]["quantity"] == "1 box"
    assert rows["Pasta"]["total"] == ""         # was "about ¼ tsp"
    assert rows["Ketchup"]["section"] == "sauces"
    assert rows["Ketchup"]["total"] == ""       # was "about ¼ tsp"


def test_a_rack_thing_in_the_pantry_section_still_totals_in_teaspoons():
    """Baking soda is on the pantry list AND a rack thing: its line buys
    one box and its row still shows the measured week."""
    rows = _approve([
        {"item": "Chicken breast", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Baking soda", "qty": "1 tsp", "category": "pantry"},
        {"item": "Ground cumin", "qty": "2 tsp", "category": "pantry"},
    ])
    assert rows["Baking soda"]["section"] == "pantry"
    assert rows["Baking soda"]["total"] == "1 tsp"
    assert rows["Ground cumin"]["section"] == "spices"
    assert rows["Ground cumin"]["total"] == "2 tsp"
