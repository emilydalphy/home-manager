"""
One food, one Kitchen row, whichever way its name is spelled (defect hunt,
2026-11-03).

THE BUG. inventory._find_row_by_name matched a row by LOWER(item) = LOWER(?)
only. The grocery list merges "Onion" and "Onions" into one line
(grocery._merge_key), and so do the Kitchen's Say/Type it and a cooked
dinner's take-off — but ticking "Onions" off the list onto a Kitchen that
held "Onion" wrote a second row, and "used the chicken thigh" found
nothing on "Chicken thighs".

THE RULE. Every name-only path (add, set, use, a grocery tick, "Already
have it") matches by grocery._merge_key. "Pepper" and "Peppers" stay two
things, as on the list.
"""

from __future__ import annotations

from app import tools
from app.db import get_conn


def _rows() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, location FROM inventory_items WHERE household_id = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_ticking_onions_off_the_list_lands_on_the_kitchens_onion_row():
    tools.update_inventory("Onion", "add", quantity="1", location="pantry")
    line = tools.add_grocery_item("Onions", quantity="3", category="produce")
    tools.mark_grocery_item(line["item_id"], status="purchased")
    rows = _rows()
    assert len(rows) == 1, rows
    assert rows[0]["item"] == "Onion"
    assert rows[0]["quantity"] == "4"
    # And the untick takes back exactly what the tick added.
    tools.mark_grocery_item(line["item_id"], status="needed")
    rows = _rows()
    assert len(rows) == 1 and rows[0]["quantity"] == "1", rows


def test_chat_add_set_and_use_find_the_plural_row():
    tools.update_inventory("Chicken thighs", "add", quantity="2 lb", location="fridge")
    tools.update_inventory("chicken thigh", "add", quantity="1 lb")
    assert [(r["item"], r["quantity"]) for r in _rows()] == [("Chicken thighs", "3 lbs")]
    used = tools.update_inventory("chicken thigh", "use", quantity="1 lb")
    assert used.get("found") is not False
    assert [(r["item"], r["quantity"]) for r in _rows()] == [("Chicken thighs", "2 lbs")]
    tools.update_inventory("CHICKEN THIGH", "set", quantity="1 lb")
    assert [(r["item"], r["quantity"]) for r in _rows()] == [("Chicken thighs", "1 lb")]


def test_already_have_it_is_a_merge_onto_the_plural_row_not_a_fresh_one():
    tools.update_inventory("Lemon", "add", quantity="1", location="fridge")
    line = tools.add_grocery_item("Lemons", quantity="2", category="produce")
    tools.move_grocery_item_to_inventory(line["item_id"])
    rows = _rows()
    assert len(rows) == 1 and rows[0]["item"] == "Lemon", rows
    # Not recorded as a row this tap created: Undo deletes that row, and
    # here it is the household's own lemon.
    conn = get_conn()
    stamped = conn.execute(
        "SELECT already_have_inventory_id FROM grocery_items WHERE id = ?", (line["item_id"],)
    ).fetchone()[0]
    conn.close()
    assert stamped is None


def test_pepper_and_peppers_stay_two_things():
    tools.update_inventory("Pepper", "add", quantity="1 jar", location="pantry")
    tools.update_inventory("Peppers", "add", quantity="3", location="pantry")
    assert sorted(r["item"] for r in _rows()) == ["Pepper", "Peppers"]


def test_onion_in_the_pantry_and_onions_in_the_fridge_are_a_two_places_pair():
    tools.update_inventory("Onion", "add", quantity="1", location="pantry")
    tools.update_inventory("Onions", "add", quantity="2", location="fridge")
    dupes = tools.get_cross_location_duplicates()
    assert len(dupes) == 1 and len(dupes[0]["entries"]) == 2, dupes


# ---- review, 2026-11-03: "Have it" before the shop, then Undo ----

def _have_it_then_undo(client, kitchen_name: str, list_name: str) -> list[dict]:
    r = client.post("/api/inventory/update",
                    json={"item": kitchen_name, "action": "add", "quantity": "2", "location": "fridge"})
    assert r.status_code == 200, r.text
    line = client.post("/api/grocery-list/add",
                       json={"item": list_name, "quantity": "3", "category": "produce"}).json()
    r = client.post(f"/api/grocery-list/{line['item_id']}/pre-shop", json={"decision": "drop"})
    assert r.status_code == 200, r.text
    # "Have it" confirms the row; it does not add the list's amount.
    assert [(x["item"], x["quantity"]) for x in _rows()] == [(kitchen_name, "2")]
    r = client.post(f"/api/grocery-list/{line['item_id']}/pre-shop-undo", json={})
    assert r.status_code == 200, r.text
    return _rows()


def test_have_it_on_yellow_onion_then_undo_keeps_the_kitchens_onions(signed_in):
    rows = _have_it_then_undo(signed_in, "Onions", "Yellow onion")
    assert [(x["item"], x["quantity"]) for x in rows] == [("Onions", "2")]


def test_have_it_on_tomato_then_undo_keeps_the_kitchens_tomatoes(signed_in):
    rows = _have_it_then_undo(signed_in, "Tomatoes", "Tomato")
    assert [(x["item"], x["quantity"]) for x in rows] == [("Tomatoes", "2")]


def test_freezer_chip_on_a_size_noted_name_only_confirms_the_pack():
    from app.tools import inventory as inv
    tools.update_inventory("Chicken thighs", "add", quantity="2 lb", location="freezer")
    conn = get_conn()
    try:
        got = inv.record_already_home(conn, "Chicken thighs (about 2 lb)", "2 lb", category="meat/seafood",
                                      location="freezer", source=inv.FREEZER_STEP_SOURCE, item_key="chicken thigh")
        receipt = conn.execute("SELECT id FROM already_home_receipts ORDER BY id DESC LIMIT 1").fetchone()["id"]
        removed = inv.release_already_home(conn, [receipt])
        conn.commit()
    finally:
        conn.close()
    assert got["fresh"] is False
    assert removed == 0
    assert [(x["item"], x["quantity"]) for x in _rows()] == [("Chicken thighs", "2 lb")]


def test_a_tin_of_diced_tomatoes_is_not_the_fresh_tomatoes():
    tools.update_inventory("Tomatoes", "add", quantity="4", location="fridge")
    tools.update_inventory("Tomatoes, diced", "add", quantity="1 can", location="fridge")
    tools.update_inventory("Tomatoes, crushed", "add", quantity="1 can", location="fridge")
    assert sorted((x["item"], x["quantity"]) for x in _rows()) == [
        ("Tomatoes", "4"), ("Tomatoes, crushed", "1 can"), ("Tomatoes, diced", "1 can"),
    ]
