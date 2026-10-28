"""
The shopping trip handing food to the kitchen — defect hunt 2026-10-28.

1. "Yes, freezing it" under a just-ticked meat line booked the fridge move
   and left the pack the tick wrote in the Fridge, on a fridge date: "Now
   in the kitchen" said Fridge and "use first", the Kitchen said going off.
   Now the pack the tick created moves to the Freezer; Put back moves it
   back; an untick takes it out exactly AND cancels the move it booked
   (which used to stay on Today for chicken still on the list).
2. Two ticks merged onto ONE kitchen row (two weeks' lines behind one Shop
   row, or a person's "12" eggs beside a recipe's "2 cups"): unticking both
   left the first amount in the kitchen for good, in either order.
3. "Use these first" named a dinner after the food's own date ("in
   Saturday's dinner" on chicken good till Tuesday), and a bare weekday a
   week out read as this week's.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import defrost
from app.tools._shared import household_id
from conftest import household_date, household_today

TODAY = household_today()


# ---------- helpers ----------

def _plan_chicken(day_offset: int = 7, recipe="Chicken Skewers", item="Chicken thighs", qty="2 lb"):
    """A plan with one chicken dinner `day_offset` days ahead, on the list."""
    monday = TODAY - datetime.timedelta(days=TODAY.weekday()) + datetime.timedelta(days=7)
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.add_recipe(recipe, ingredients=[{"item": item, "qty": qty, "category": "meat/seafood"}],
                     prep_time_minutes=10, cook_time_minutes=15)
    on = (TODAY + datetime.timedelta(days=day_offset)).isoformat()
    tools.plan_meal(on, recipe, slot="dinner", weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True)
    line = next(l for l in tools.list_grocery_list("needed") if l["item"].lower() == item.lower())
    return plan_id, line["id"]


def _kitchen(name="Chicken thighs"):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, quantity, location, expiration_date FROM inventory_items "
        "WHERE household_id = ? AND LOWER(item) = LOWER(?)",
        (household_id(), name),
    ).fetchall()]
    conn.close()
    return rows


def _moves():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, status, description FROM prep_tasks WHERE household_id = ? AND task_type = 'defrost'",
        (household_id(),),
    ).fetchall()]
    conn.close()
    return rows


# ---------- 1. "Yes, freezing it" puts the pack in the Freezer ----------

def test_yes_freezing_it_moves_the_ticked_pack_to_the_freezer_and_the_card_says_so(signed_in):
    _plan, line = _plan_chicken()
    signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
    fridge_date = _kitchen()[0]["expiration_date"]
    res = signed_in.post(f"/api/grocery-list/{line}/freezing", json={"answer": "freezer"})
    assert res.status_code == 200 and res.json()["freezing"] is True

    [row] = _kitchen()
    assert row["location"] == "freezer"
    assert row["expiration_date"] > fridge_date, "a freezer date, not the two-day fridge one"
    now = signed_in.get("/api/grocery-list/now-in-kitchen").json()
    assert now["places"] == [{"place": "freezer", "count": 1}]
    assert now["use_first"] == [], "a frozen pack is not one to use first"


def test_untick_after_yes_takes_the_pack_out_and_cancels_its_move(signed_in):
    _plan, line = _plan_chicken()
    signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
    signed_in.post(f"/api/grocery-list/{line}/freezing", json={"answer": "freezer"})
    assert len(_moves()) == 1

    res = signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "needed"}).json()
    assert res["inventory_restored"] is True
    assert _kitchen() == []
    assert _moves() == [], "no pack, nothing to move to the fridge"
    # Bought after all: the question is askable again, and the tick adds once.
    view = signed_in.get("/api/grocery-list/by-store?status=needed").json()
    again = [it for s in view["stores"] for sec in s["sections"] for it in sec["items"] if it["id"] == line]
    assert again and "freezing" in again[0]
    signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
    assert len(_kitchen()) == 1 and _kitchen()[0]["location"] == "fridge"


def test_put_back_returns_the_pack_to_the_fridge_with_its_date(signed_in):
    _plan, line = _plan_chicken()
    signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
    [before] = _kitchen()
    signed_in.post(f"/api/grocery-list/{line}/freezing", json={"answer": "freezer"})
    signed_in.post(f"/api/grocery-list/{line}/freezing", json={"answer": "fridge"})
    [row] = _kitchen()
    assert (row["location"], row["expiration_date"]) == (before["location"], before["expiration_date"])
    assert _moves() == []
    # And the untick still reverses exactly.
    signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "needed"})
    assert _kitchen() == []


def test_a_tick_merged_into_chicken_already_in_the_fridge_leaves_the_shelf_alone(signed_in):
    tools.update_inventory("Chicken thighs", "add", quantity="1 lb", category="meat/seafood", location="fridge")
    _plan, line = _plan_chicken()
    signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
    signed_in.post(f"/api/grocery-list/{line}/freezing", json={"answer": "freezer"})
    [row] = _kitchen()
    assert row["location"] == "fridge", "which half is the new pack is not known: nothing guessed"
    assert len(_moves()) == 1


def test_a_resync_after_the_yes_books_no_second_move(signed_in):
    plan_id, line = _plan_chicken()
    signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
    signed_in.post(f"/api/grocery-list/{line}/freezing", json={"answer": "freezer"})
    defrost.sync_defrost_tasks(plan_id)
    assert len(_moves()) == 1


# ---------- 2. two ticks on one kitchen row ----------

def _two_egg_lines():
    a = tools.add_grocery_item("Eggs", "12", category="dairy")["item_id"]
    conn = get_conn()
    b = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, status) "
        "VALUES (?, 'Eggs', '2 cups', 'dairy', 'needed')",
        (household_id(),),
    ).lastrowid
    conn.commit()
    conn.close()
    return a, b


@pytest.mark.parametrize("order", ["first-ticked first", "last-ticked first"])
def test_unticking_both_lines_on_one_row_empties_it_in_either_order(order):
    a, b = _two_egg_lines()
    tools.mark_grocery_item(a, "purchased")
    tools.mark_grocery_item(b, "purchased")
    assert [r["quantity"] for r in _kitchen("Eggs")] == ["12 + 2 cups"]
    first, second = (a, b) if order == "first-ticked first" else (b, a)

    assert tools.mark_grocery_item(first, "needed")["inventory_restored"] is True
    left = "2 cups" if first == a else "12"
    assert [r["quantity"] for r in _kitchen("Eggs")] == [left], "only the other line's eggs"
    assert tools.mark_grocery_item(second, "needed")["inventory_restored"] is True
    assert _kitchen("Eggs") == []
    # Re-ticked: each adds again, once.
    tools.mark_grocery_item(a, "purchased")
    tools.mark_grocery_item(b, "purchased")
    assert [r["quantity"] for r in _kitchen("Eggs")] == ["12 + 2 cups"]


def test_stock_already_there_comes_back_to_what_it_was():
    tools.update_inventory("Eggs", "add", quantity="6", category="dairy")
    a, b = _two_egg_lines()
    tools.mark_grocery_item(a, "purchased")
    tools.mark_grocery_item(b, "purchased")
    tools.mark_grocery_item(a, "needed")
    tools.mark_grocery_item(b, "needed")
    assert [r["quantity"] for r in _kitchen("Eggs")] == ["6"]


def test_a_row_written_since_is_still_left_alone():
    a, b = _two_egg_lines()
    tools.mark_grocery_item(a, "purchased")
    tools.mark_grocery_item(b, "purchased")
    tools.update_inventory("Eggs", "set", quantity="10")
    assert tools.mark_grocery_item(a, "needed")["inventory_restored"] is False
    assert [r["quantity"] for r in _kitchen("Eggs")] == ["10"], "the household's count is the truer record"


# ---------- 3. "Use these first" never names a meal after the food's date ----------

def _bought_for(item, qty, category, meal_offset, expires_offset):
    line = tools.add_grocery_item(item, quantity=qty, category=category)["item_id"]
    tools.mark_grocery_item(line, "purchased")
    conn = get_conn()
    entry = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, freeform_meal, slot_state) "
        "VALUES (?, ?, 'dinner', 'Dinner', 'planned')",
        (household_id(), household_date(meal_offset)),
    ).lastrowid
    conn.execute(
        "INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
        "VALUES (?, ?, ?, ?, ?)",
        (household_id(), entry, line, item, qty),
    )
    conn.execute("UPDATE inventory_items SET expiration_date = ? WHERE household_id = ? AND item = ?",
                 (household_date(expires_offset), household_id(), item))
    conn.commit()
    conn.close()


def test_a_dinner_after_the_foods_date_is_not_its_plan():
    _bought_for("Chicken thighs", "2 lb", "meat/seafood", meal_offset=5, expires_offset=2)
    [u] = tools.kitchen_from_this_shop()["use_first"]
    gone_by = (TODAY + datetime.timedelta(days=2)).strftime("%A")
    assert u["plan"] == f"by {gone_by}"


def test_a_dinner_a_week_out_says_next():
    _bought_for("Salmon", "1 lb", "meat/seafood", meal_offset=7, expires_offset=7)
    [u] = tools.kitchen_from_this_shop()["use_first"]
    assert u["plan"] == f"in next {TODAY.strftime('%A')}’s dinner"


# ---------- 4. the card counts what the Kitchen tab shows ----------

def test_a_staple_bought_on_this_shop_is_not_counted_where_the_kitchen_never_shows_it(signed_in):
    for item, category in (("Coffee", "pantry"), ("Chicken thighs", "meat/seafood")):
        line = tools.add_grocery_item(item, quantity="1", category=category)["item_id"]
        signed_in.post(f"/api/grocery-list/{line}/status", json={"status": "purchased"})
    now = signed_in.get("/api/grocery-list/now-in-kitchen").json()
    home = signed_in.get("/api/whats-home").json()
    assert now["places"] == [{"place": "fridge", "count": 1}], "the coffee is a staple now (its tick made it one)"
    assert {p["place"]: p["count"] for p in now["places"]} == {k: v for k, v in home["counts"].items() if v}
