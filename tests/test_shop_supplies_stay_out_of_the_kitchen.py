"""
Dish soap ticked off on Shop is bought, not put in the kitchen (defect hunt
2026-10-29).

Walking a shop end to end — a planned week plus hand lines, two stores,
everything ticked — the dish soap landed in the kitchen as a pantry food:
the Kitchen tab drew "Dish soap · Pantry · bought today · best by Nov 11"
and Shop's finished moment counted it in "Now in the kitchen · Pantry".
mark_grocery_item now leaves a household supply out of inventory_items
(grocery._is_household_supply), says so (inventory_added False), and its
untick has nothing to take back. Food is untouched: a name only counts as
a supply by its whole phrase or its last word, and never when a planned
meal is buying it.
"""
from __future__ import annotations

from app import tools
from app.db import get_conn


def _kitchen(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity FROM inventory_items WHERE household_id = 1 AND lower(item) = lower(?)", (item,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _tick(item: str, quantity: str = "1", category: str = "other") -> tuple[int, dict]:
    line = tools.add_grocery_item(item, quantity, category=category)
    return line["item_id"], tools.mark_grocery_item(line["item_id"], status="purchased")


def test_dish_soap_is_bought_but_never_lands_in_the_kitchen(signed_in):
    item_id, result = _tick("Dish soap")
    assert result["status"] == "purchased"
    assert result["inventory_added"] is False
    assert _kitchen("Dish soap") == []

    # Neither the Kitchen tab nor Shop's "Now in the kitchen" counts it.
    home = signed_in.get("/api/whats-home").json()
    assert all(r["item"] != "Dish soap" for rows in home["locations"].values() for r in rows)
    assert signed_in.get("/api/grocery-list/now-in-kitchen").json()["total"] == 0
    # It is still a bought line on the checklist.
    assert "Dish soap" in [i["item"] for i in tools.list_grocery_list(status="bought")]

    # Untick and re-tick: nothing to take back, nothing added.
    undo = tools.mark_grocery_item(item_id, status="needed")
    assert undo["status"] == "needed" and "inventory_restored" not in undo
    assert "Dish soap" not in [i["item"] for i in tools.list_grocery_list(status="bought")]
    again = tools.mark_grocery_item(item_id, status="purchased")
    assert again["inventory_added"] is False
    assert _kitchen("Dish soap") == []


def test_supplies_by_phrase_or_last_word_food_by_its_own_name():
    for supply in ("Toilet paper", "AA batteries", "Laundry detergent", "Paper towels"):
        _tick(supply)
        assert _kitchen(supply) == [], supply
    # A household word that is not the thing itself is food.
    for food in ("Sponge cake", "Pad thai noodles", "Milk"):
        _, result = _tick(food, category="pantry")
        assert result["inventory_added"] is True, food
        assert len(_kitchen(food)) == 1, food


def test_cake_floss_and_foil_wrapped_food_are_food():
    # Review, 2026-10-29: "sponge", "floss" and "foil" end real food names
    # too; those three only count as supplies when the name says so.
    for food in ("Victoria sponge", "Candy floss", "Salmon in foil", "Sponge"):
        _, result = _tick(food, category="pantry")
        assert result["inventory_added"] is True, food
    for supply in ("Sponges", "Dental floss", "Tin foil", "Kitchen sponge"):
        _, result = _tick(supply)
        assert result["inventory_added"] is False, supply


def test_a_line_a_planned_meal_buys_goes_in_the_kitchen_whatever_its_name():
    """The ledger wins: if a recipe needs it, it is food."""
    line = tools.add_grocery_item("Dish soap", "1")
    conn = get_conn()
    plan = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (1, '2026-10-26', 'approved')"
    ).lastrowid
    entry = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, freeform_meal, weekly_plan_id) "
        "VALUES (1, '2026-10-30', 'dinner', 'Odd dinner', ?)", (plan,),
    ).lastrowid
    conn.execute(
        "INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
        "VALUES (1, ?, ?, 'Dish soap', '1')", (entry, line["item_id"]),
    )
    conn.commit()
    conn.close()
    assert tools.mark_grocery_item(line["item_id"], status="purchased")["inventory_added"] is True
    assert len(_kitchen("Dish soap")) == 1
