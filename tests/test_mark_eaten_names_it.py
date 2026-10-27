"""
Marking dinner eaten says what came out of the kitchen, and Undo puts it back
(inventory card A2, Emily 2026-10-26, OptionA screen 2 "After dinner").

Before: the tick took the ingredients out in silence and an un-tick put
nothing back (check_off_meal's old docstring: no record, so any restore was
a guess). Now the depletion keeps each row as it was
(tools.eaten_receipt), the tick reports `kitchen_took`, and
POST /api/cooker/undo-eaten writes the rows back exactly.
"""
from __future__ import annotations

from conftest import household_date

from app import tools
from app.db import get_conn
from app.tools import eaten_receipt

DAY = household_date(0)


def _lemon_chicken():
    tools.add_recipe("Lemon chicken", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lbs"},
        {"item": "Baby spinach", "qty": "1 bag"},
        {"item": "Jasmine rice", "qty": "1 cup"},
        {"item": "Lemons", "qty": "2"},
    ], default_servings=4)


def _stock():
    tools.update_inventory("Chicken thighs", "add", quantity="3 lbs", category="meat/seafood", location="fridge")
    tools.update_inventory("Baby spinach", "add", quantity="2 bag", category="produce", location="fridge")
    tools.update_inventory("Jasmine rice", "add", quantity="2 cup", category="pantry", location="pantry")
    tools.update_inventory("Lemons", "add", quantity="2", category="produce", location="fridge")


def _rows():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM inventory_items WHERE household_id = ? ORDER BY id",
                        (tools.household_id(),)).fetchall()
    conn.close()
    # `rev` is the write counter (a trigger bumps it on every update and it
    # must only ever go up, so a grocery untick's proof stays honest): a
    # restore is exact in everything but how many times the row was written.
    return [{k: v for k, v in dict(r).items() if k != "rev"} for r in rows]


def _entry(recipe="Lemon chicken"):
    return tools.plan_meal(DAY, recipe, slot="dinner")["entry_id"]


# ---------- the sentence ----------

def test_short_names_follow_the_mockup():
    assert eaten_receipt.short_name("Chicken thighs") == "chicken"
    assert eaten_receipt.short_name("Baby spinach") == "spinach"
    assert eaten_receipt.short_name("Jasmine rice") == "rice"
    assert eaten_receipt.short_name("Lemons") == "lemons"
    assert eaten_receipt.short_name("Thighs") == "thighs"


def test_the_line_joins_with_and_and_one_the():
    assert eaten_receipt.took_line(["chicken", "spinach", "rice", "lemons"]) == \
        "Took the chicken, spinach, rice and lemons out of the kitchen."
    assert eaten_receipt.took_line(["chicken"]) == "Took the chicken out of the kitchen."
    assert eaten_receipt.took_line(["a", "b"]) == "Took the a and b out of the kitchen."


def test_past_four_names_it_says_and_n_more():
    assert eaten_receipt.took_line(["a", "b", "c", "d", "e", "f"]) == \
        "Took the a, b, c, d and 2 more out of the kitchen."


# ---------- the tick reports what came out ----------

def test_the_tick_names_what_came_out_of_the_kitchen():
    _lemon_chicken()
    _stock()
    result = tools.check_off_meal(_entry(), "done")

    assert result["kitchen_took"]["names"] == ["chicken", "spinach", "rice", "lemons"]
    assert result["kitchen_took"]["line"] == "Took the chicken, spinach, rice and lemons out of the kitchen."
    # The stored rows are the app's business, not the chat model's.
    assert all("before" not in d for d in result["inventory_depleted"])


def test_a_meal_with_nothing_tracked_reports_nothing():
    _lemon_chicken()  # nothing stocked
    result = tools.check_off_meal(_entry(), "done")
    assert "kitchen_took" not in result


def test_a_leftovers_night_takes_nothing_and_says_nothing():
    _lemon_chicken()
    _stock()
    plan = tools.create_weekly_plan(household_date(0))["weekly_plan_id"]
    cook = tools.plan_meal(household_date(0), "Lemon chicken", slot="dinner", weekly_plan_id=plan)["entry_id"]
    left = tools.plan_meal(household_date(1), "Lemon chicken", slot="dinner", weekly_plan_id=plan,
                           derived_from={"links_to": f"{household_date(0)}:dinner"})["entry_id"]
    tools.repair_leftover_chains(plan)
    before = _rows()

    result = tools.check_off_meal(left, "done")

    assert "kitchen_took" not in result
    assert _rows() == before
    assert cook  # the cook night itself would have named things


# ---------- Undo ----------

def test_undo_puts_every_row_back_exactly_and_unmarks_the_meal():
    _lemon_chicken()
    _stock()
    before = _rows()
    entry = _entry()
    tools.check_off_meal(entry, "done")
    assert _rows() != before

    out = tools.undo_eaten_meal(entry)

    assert out["restored"] == 4
    assert _rows() == before
    conn = get_conn()
    row = conn.execute("SELECT cooked_status, inventory_depleted_at FROM meal_plan_entries WHERE id = ?",
                       (entry,)).fetchone()
    conn.close()
    assert row["cooked_status"] == "pending"
    assert row["inventory_depleted_at"] is None


def test_undo_brings_back_a_row_that_was_used_up_and_deleted():
    tools.add_recipe("Lemon chicken", ingredients=[{"item": "Lemons", "qty": "2"}], default_servings=4)
    tools.update_inventory("Lemons", "add", quantity="2", category="produce", location="fridge")
    before = _rows()
    assert len(before) == 1
    entry = _entry()
    tools.check_off_meal(entry, "done")
    assert _rows() == [], "the row was used up"

    tools.undo_eaten_meal(entry)

    assert _rows() == before, "same id, quantity, location and dates"


def test_a_tick_after_undo_takes_the_ingredients_again():
    _lemon_chicken()
    _stock()
    entry = _entry()
    tools.check_off_meal(entry, "done")
    after_first = _rows()
    tools.undo_eaten_meal(entry)

    result = tools.check_off_meal(entry, "done")

    assert result["kitchen_took"]["names"] == ["chicken", "spinach", "rice", "lemons"]
    assert [r["quantity"] for r in _rows()] == [r["quantity"] for r in after_first]


def test_undo_twice_restores_nothing_the_second_time():
    _lemon_chicken()
    _stock()
    entry = _entry()
    tools.check_off_meal(entry, "done")
    tools.undo_eaten_meal(entry)
    assert tools.undo_eaten_meal(entry)["restored"] == 0


# ---------- over HTTP ----------

def test_the_routes_carry_the_receipt_and_undo(signed_in):
    _lemon_chicken()
    _stock()
    before = _rows()
    entry = _entry()

    r = signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"})
    assert r.status_code == 200
    assert r.json()["kitchen_took"]["line"] == "Took the chicken, spinach, rice and lemons out of the kitchen."

    u = signed_in.post("/api/cooker/undo-eaten", json={"entry_id": entry})
    assert u.status_code == 200
    assert "kitchen_took" not in u.json()
    assert _rows() == before


def test_todays_move_tick_carries_the_receipt(signed_in):
    _lemon_chicken()
    _stock()
    entry = _entry()
    r = signed_in.post(f"/api/today/moves/cook:{entry}/done", json={"done": True})
    assert r.status_code == 200
    assert r.json()["kitchen_took"]["names"][0] == "chicken"
