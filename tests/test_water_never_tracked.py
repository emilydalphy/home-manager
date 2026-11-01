"""
Tap water is never tracked (Emily, 2026-11-01): "it's also currently
counting water as an ingredient that could expire. new rule that water
should never be asked if there are 'leftovers' of it or something to be
tracked since everyone has access to it."

One predicate, pantry.is_tap_water, read in four places: a plan never puts
water on the list, a ticked "Water" line never becomes a kitchen row, and
use_up.is_staple (the filter "going off", "use it up", "still have these?"
and the Kitchen tab all share) treats it as always there.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import pantry, use_up


@pytest.mark.parametrize("name", [
    "water", "Water", "cold water", "2 cups water", "1/2 cup water", "warm water (110°F)",
    "boiling water, divided", "water as needed", "water for boiling", "salted water",
    "reserved pasta water", "cooking water",
    "ice", "ice cubes", "crushed ice",
])
def test_plain_water_and_ice_are_tap_water(name):
    assert pantry.is_tap_water(name)


@pytest.mark.parametrize("name", [
    "coconut water", "rose water", "tonic water", "sparkling water", "soda water",
    "water chestnuts", "watercress", "watermelon", "24-pack water", "tuna in water",
    "ice cream", "iceberg lettuce", "",
])
def test_things_bought_with_water_in_the_name_are_not(name):
    assert not pantry.is_tap_water(name)


def test_water_is_never_going_off_or_asked_about_in_any_household():
    assert use_up.is_staple("Cold water", [])
    assert not use_up.is_staple("Coconut water", [])


def _kitchen_rows(name: str) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
        (tools.household_id(), name),
    ).fetchone()[0]
    conn.close()
    return n


def test_ticking_a_water_line_puts_nothing_in_the_kitchen():
    line = tools.add_grocery_item("Water", "2 L")["item_id"]
    tools.mark_grocery_item(line, "purchased")
    assert _kitchen_rows("Water") == 0
    # The guard: an ordinary line still goes in.
    milk = tools.add_grocery_item("Milk", "1 L")["item_id"]
    tools.mark_grocery_item(milk, "purchased")
    assert _kitchen_rows("Milk") == 1


def test_a_plan_never_puts_water_on_the_list():
    tools.add_recipe(
        name="Rice pot",
        ingredients=[{"item": "white rice", "qty": "2 cups"}, {"item": "water", "qty": "4 cups"},
                     {"item": "cold water", "qty": "1 tbsp"}],
        instructions=["Cook it."],
        default_servings=4,
    )
    monday = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    plan_id = tools.create_weekly_plan(monday)["weekly_plan_id"]
    tools.plan_meal(monday, "Rice pot", "dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Julia")
    conn = get_conn()
    items = {r["item"].lower() for r in conn.execute(
        "SELECT item FROM grocery_items WHERE household_id = ?", (tools.household_id(),)).fetchall()}
    conn.close()
    assert not any("water" in i for i in items), items
    assert any("rice" in i for i in items), "the rest of the recipe still lands"
