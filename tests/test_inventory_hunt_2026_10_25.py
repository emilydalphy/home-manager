"""
Kitchen inventory defect hunt, 2026-10-25 (branch overnight/inventory-hunt).

1. The item sheet's "+" answered 500 on any item with no amount — every
   item added from the Kitchen form with the amount box left blank — and on
   "½ cup"; "−" on "1½ cups" read "0 ½ cups" and "1,5 kg" read "0 ,5 kg".
2. The Kitchen form's add sent no category and the route defaulted it to
   "other": adding milk the list had filed as dairy re-filed it under Other,
   and every new row was Other, so the planner's on-hand perishables list
   never saw chicken typed in by hand.
3. Moving a row across the freezer door kept its fridge date: chicken
   taken out of the freezer read days past; chicken put in read "use soon".
   A location outside the three shelves was saved and the row vanished.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import inventory


def _row(item_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row)


def _ids(client) -> dict:
    data = client.get("/api/inventory/by-location").json()
    return {i["item"]: i for loc in data["locations"] for i in loc["items"]}


# ---------- 1. the quantity stepper ----------

@pytest.mark.parametrize("raw,delta,expected", [
    ("", 1, "1"),
    ("some", 1, "1 some"),
    ("½ cup", 1, "1 1/2 cup"),
    ("1½ cups", -1, "1/2 cups"),
    ("1 ½ cups", 1, "2 1/2 cups"),
    ("1,5 kg", -1, "0,5 kg"),
    ("1,000 g", 1, "1001 g"),
    ("3 gal", -1, "2 gal"),
    ("2/3 cup", 1, "1 2/3 cup"),
])
def test_the_stepper_reads_every_way_an_amount_is_written(raw, delta, expected):
    assert inventory._step_quantity_text(raw, delta) == expected


def test_plus_on_an_item_added_with_no_amount_is_not_a_500(signed_in):
    client = signed_in
    res = client.post("/api/inventory/update", json={"item": "basil", "action": "add", "location": "fridge"})
    assert res.status_code == 200
    basil = _ids(client)["basil"]
    res = client.post(f"/api/inventory/{basil['id']}/quantity", json={"delta": 1})
    assert res.status_code == 200, res.text
    assert res.json()["quantity"] == "1"


# ---------- 2. the Kitchen form's category ----------

def test_adding_from_kitchen_keeps_the_category_the_list_gave(signed_in):
    client = signed_in
    added = client.post("/api/grocery-list/add", json={"item": "milk", "quantity": "1 gallon", "category": "dairy"}).json()
    client.post(f"/api/grocery-list/{added['item_id']}/status", json={"status": "purchased"})
    assert _ids(client)["milk"]["category"] == "dairy"
    # Exactly what static/inventory.html's Add button posts: no category.
    res = client.post("/api/inventory/update", json={"item": "Milk", "action": "add", "quantity": "1 gallon", "location": "fridge"})
    assert res.status_code == 200
    assert _ids(client)["milk"]["category"] == "dairy"


def test_a_new_row_from_kitchen_is_filed_by_its_name(signed_in):
    client = signed_in
    for name in ("chicken thighs", "spinach", "Crème fraîche"):
        client.post("/api/inventory/update", json={"item": name, "action": "add", "quantity": "1", "location": "fridge"})
    rows = _ids(client)
    assert rows["chicken thighs"]["category"] == "meat/seafood"
    assert rows["spinach"]["category"] == "produce"
    # A name the guess doesn't know stays Other (two weeks), not "pantry"
    # (six months) — the guess's own fallback.
    assert rows["Crème fraîche"]["category"] == "other"
    fresh = {r["item"] for r in tools.get_fresh_perishable_inventory(near_expiring_days=0)}
    assert "chicken thighs" in fresh


def test_a_blank_name_is_refused(signed_in):
    res = signed_in.post("/api/inventory/update", json={"item": "   ", "action": "add", "location": "pantry"})
    assert res.status_code == 400
    assert _ids(signed_in) == {}


# ---------- 3. moving across the freezer door ----------

def test_taking_chicken_out_of_the_freezer_restarts_its_date(signed_in):
    client = signed_in
    today = household_today()
    old = (today - timedelta(days=12)).isoformat()
    client.post("/api/inventory/update", json={
        "item": "chicken thighs", "action": "add", "quantity": "2 lb",
        "category": "meat/seafood", "location": "freezer", "expiration_date": old,
    })
    chicken = _ids(client)["chicken thighs"]
    res = client.post(f"/api/inventory/{chicken['id']}/location", json={"location": "fridge"})
    assert res.status_code == 200
    # Chicken's own fridge life (two days), from today — not 12 days past.
    assert _row(chicken["id"])["expiration_date"] == (today + timedelta(days=2)).isoformat()
    soon = {e["item"]: e["status"] for e in tools.get_expiring_soon()}
    assert soon.get("chicken thighs") == "expiring_soon"


def test_a_thawed_cooked_portion_keeps_three_days(signed_in):
    client = signed_in
    client.post("/api/inventory/update", json={
        "item": "Lentil soup (frozen)", "action": "add", "quantity": "2 servings",
        "category": "frozen", "location": "freezer",
    })
    soup = _ids(client)["Lentil soup (frozen)"]
    client.post(f"/api/inventory/{soup['id']}/location", json={"location": "fridge"})
    assert _row(soup["id"])["expiration_date"] == (household_today() + timedelta(days=inventory.THAWED_KEEPS_DAYS)).isoformat()


def test_putting_food_in_the_freezer_is_not_use_soon(signed_in):
    client = signed_in
    added = client.post("/api/grocery-list/add", json={"item": "ground beef", "quantity": "1 lb", "category": "meat/seafood"}).json()
    client.post(f"/api/grocery-list/{added['item_id']}/status", json={"status": "purchased"})
    beef = _ids(client)["ground beef"]
    assert beef["location"] == "fridge"
    client.post(f"/api/inventory/{beef['id']}/location", json={"location": "freezer"})
    frozen_until = (household_today() + timedelta(days=90)).isoformat()
    assert _row(beef["id"])["expiration_date"] == frozen_until
    assert "ground beef" not in {e["item"] for e in tools.get_expiring_soon()}


def test_fridge_to_pantry_leaves_the_date_alone(signed_in):
    client = signed_in
    client.post("/api/inventory/update", json={"item": "bbq sauce", "action": "add", "location": "fridge", "expiration_date": "2027-03-01"})
    sauce = _ids(client)["bbq sauce"]
    client.post(f"/api/inventory/{sauce['id']}/location", json={"location": "pantry"})
    assert _row(sauce["id"])["expiration_date"] == "2027-03-01"


def test_a_row_added_straight_to_the_freezer_gets_the_frozen_date(signed_in):
    signed_in.post("/api/inventory/update", json={"item": "salmon", "action": "add", "quantity": "1 lb", "location": "freezer"})
    salmon = _ids(signed_in)["salmon"]
    assert salmon["expiration_date"] == (household_today() + timedelta(days=90)).isoformat()


def test_a_shelf_that_isnt_one_is_refused_and_a_capital_is_folded(signed_in):
    client = signed_in
    client.post("/api/inventory/update", json={"item": "bread", "action": "add", "location": "pantry"})
    bread = _ids(client)["bread"]
    assert client.post(f"/api/inventory/{bread['id']}/location", json={"location": "garage"}).status_code == 400
    assert client.post("/api/inventory/999999/location", json={"location": "fridge"}).status_code == 404
    assert client.post("/api/inventory/update", json={"item": "rye", "action": "add", "location": "counter"}).status_code == 400
    assert client.post(f"/api/inventory/{bread['id']}/location", json={"location": " Fridge "}).status_code == 200
    assert _row(bread["id"])["location"] == "fridge"


def test_an_old_odd_location_still_shows_on_kitchen(signed_in):
    client = signed_in
    client.post("/api/inventory/update", json={"item": "rice", "action": "add", "location": "pantry"})
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET location = 'Cupboard' WHERE item = 'rice'")
    conn.commit()
    conn.close()
    assert "rice" in _ids(client)


# ---------- 4. "turns soon" agrees with the name ----------

@pytest.mark.parametrize("name,title", [
    ("chicken thighs", "Your chicken thighs turn soon"),
    ("milk", "Your milk turns soon"),
    ("hummus", "Your hummus turns soon"),
])
def test_the_use_it_up_line_agrees_with_the_name(signed_in, name, title):
    tomorrow = (household_today() + timedelta(days=1)).isoformat()
    signed_in.post("/api/inventory/update", json={
        "item": name, "action": "add", "quantity": "1", "location": "fridge", "expiration_date": tomorrow,
    })
    bell = [n for n in tools.get_active_notifications() if n["type"] == "expiring_soon"]
    assert [n["title"] for n in bell] == [title]
    from app.tools import digest
    kitchen = dict(digest._digest_parts(digest.datetime.now(), digest.morning_text_parts())).get("kitchen", "")
    assert kitchen.startswith(title)


def test_one_bad_shelf_in_a_batch_does_not_fail_the_rest():
    out = tools.update_inventory_items([
        {"item": "rice", "quantity": "1 bag", "location": "pantry"},
        {"item": "bread", "quantity": "1 loaf", "location": "counter"},
        {"item": "oats", "quantity": "1 bag", "location": "Pantry"},
    ])
    assert [bool(r.get("error")) for r in out["updated"]] == [False, True, False]
    names = {r["item"] for r in tools.get_inventory()}
    assert names == {"rice", "oats"}
