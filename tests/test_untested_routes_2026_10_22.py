"""
Routes no test drove over HTTP until 2026-10-22 (standing work: close test
gaps). Found by matching every @app route in app/main.py, method and path
shape, against the client calls in tests/. Each one gets: the happy path and
the effect it claims, one bad input answered 4xx (never a 500), and household
isolation with a VALID body — the isolation attack (test_isolation_attack.py)
throws A's ids at every route, but mostly with shapes that 422 before any
lookup, so it never reaches these routes' real work.

Pinned here:
  PATCH/GET /api/preferences/meal-planning
  POST      /api/notifications/dismiss
  GET       /api/inventory
  GET       /api/inventory/expiring      (days out of range was a 500)
  GET       /api/inventory/duplicates
  GET       /api/stores
  GET       /api/member-needs
  POST      /api/inventory/scan-receipt  (model stubbed)
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from conftest import household_date

from app import households, main, tools
from app.db import get_conn
from app.main import app
from app.tools._shared import DEFAULT_HOUSEHOLD_ID

A = DEFAULT_HOUSEHOLD_ID
B_PASSPHRASE = "untested-routes-b-passphrase"


def _sign_in(client, password):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client


@pytest.fixture
def two():
    """(a, b, b_id): two signed-in clients on two households, own cookie jars."""
    b_id = households.create_household("Untested Routes B", B_PASSPHRASE)
    with TestClient(app) as a, TestClient(app) as b:
        _sign_in(a, "test-password")
        _sign_in(b, B_PASSPHRASE)
        yield a, b, b_id


def _seed_inventory(hid, item, location, expiration_date=None):
    with tools.use_household(hid):
        tools.update_inventory(item, "add", quantity="1", expiration_date=expiration_date,
                               category="dairy", location=location, source="manual")


def _names(payload_items):
    return {i["item"].lower() for i in payload_items}


# ---------- /api/preferences/meal-planning ----------

def test_meal_planning_preferences_patch_writes_and_reads_back(two):
    a, b, _ = two
    res = a.patch("/api/preferences/meal-planning", json={"field": "dinners_per_week", "value": 4})
    assert res.status_code == 200
    assert res.json()["meal_counts"]["dinners_per_week"] == 4
    assert a.get("/api/preferences/meal-planning").json()["meal_counts"]["dinners_per_week"] == 4


@pytest.mark.parametrize("body", [
    {"field": "dinners_per_week", "value": "lots"},
    {"field": "dinners_per_week", "value": 99},
    {"field": "weeknight_max_minutes", "value": {"a": 1}},
    {"field": "no_such_field", "value": 1},
    {"value": 4},
])
def test_meal_planning_preferences_patch_refuses_bad_input_without_writing(two, body):
    a, _, _ = two
    before = a.get("/api/preferences/meal-planning").json()
    res = a.patch("/api/preferences/meal-planning", json=body)
    assert res.status_code in (400, 422), res.text
    assert a.get("/api/preferences/meal-planning").json() == before


def test_meal_planning_preferences_are_per_household(two):
    a, b, _ = two
    a_before = a.get("/api/preferences/meal-planning").json()
    res = b.patch("/api/preferences/meal-planning", json={"field": "dinners_per_week", "value": 2})
    assert res.status_code == 200
    assert b.get("/api/preferences/meal-planning").json()["meal_counts"]["dinners_per_week"] == 2
    assert a.get("/api/preferences/meal-planning").json() == a_before


# ---------- /api/notifications/dismiss ----------

def _dismissed(hid):
    conn = get_conn()
    rows = conn.execute("SELECT key FROM notification_dismissals WHERE household_id = ?", (hid,)).fetchall()
    conn.close()
    return {r["key"] for r in rows}


def test_notification_dismiss_records_the_key_for_this_household_only(two):
    a, b, b_id = two
    res = b.post("/api/notifications/dismiss", json={"key": "weekly_plan_ready:42"})
    assert res.status_code == 200
    assert res.json() == {"key": "weekly_plan_ready:42", "dismissed": True}
    assert "weekly_plan_ready:42" in _dismissed(b_id)
    assert "weekly_plan_ready:42" not in _dismissed(A)
    # Twice is a no-op, not a uniqueness 500.
    assert b.post("/api/notifications/dismiss", json={"key": "weekly_plan_ready:42"}).status_code == 200


def test_notification_dismiss_without_a_key_is_a_422(two):
    a, _, _ = two
    assert a.post("/api/notifications/dismiss", json={}).status_code == 422
    assert _dismissed(A) == set()


# ---------- /api/inventory (+ expiring, duplicates) ----------

def test_inventory_lists_this_households_items_only(two):
    a, b, b_id = two
    _seed_inventory(A, "Greek yogurt", "fridge")
    _seed_inventory(b_id, "Oat milk", "fridge")
    a_items = [i for s in a.get("/api/inventory").json()["sections"] for i in s["items"]]
    b_res = b.get("/api/inventory")
    assert b_res.status_code == 200
    b_items = [i for s in b_res.json()["sections"] for i in s["items"]]
    assert _names(a_items) == {"greek yogurt"}
    assert _names(b_items) == {"oat milk"}


def test_inventory_expiring_lists_what_goes_bad_soon_for_this_household(two):
    a, b, b_id = two
    _seed_inventory(A, "Cream", "fridge", expiration_date=household_date(1))
    _seed_inventory(A, "Hot sauce", "fridge", expiration_date=household_date(60))
    _seed_inventory(b_id, "Sour cream", "fridge", expiration_date=household_date(1))
    res = a.get("/api/inventory/expiring?days=4")
    assert res.status_code == 200
    assert _names(res.json()["items"]) == {"cream"}
    assert _names(b.get("/api/inventory/expiring").json()["items"]) == {"sour cream"}


@pytest.mark.parametrize("days", ["abc", "99999999999", "-3"])
def test_inventory_expiring_answers_a_bad_window_with_a_422_not_a_500(two, days):
    # Red before 2026-10-22: days=99999999999 overflowed timedelta in
    # get_expiring_soon and answered 500. The only caller sends days=4.
    a, _, _ = two
    assert a.get(f"/api/inventory/expiring?days={days}").status_code == 422


def test_inventory_duplicates_finds_an_item_in_two_places_for_this_household(two):
    a, b, b_id = two
    _seed_inventory(A, "BBQ sauce", "fridge")
    _seed_inventory(A, "BBQ sauce", "pantry")
    _seed_inventory(b_id, "BBQ sauce", "fridge")
    res = a.get("/api/inventory/duplicates")
    assert res.status_code == 200
    dups = res.json()["duplicates"]
    assert len(dups) == 1 and "bbq sauce" in str(dups[0]).lower()
    # B has one BBQ sauce in one place: not a duplicate, and A's two don't count.
    assert b.get("/api/inventory/duplicates").json()["duplicates"] == []


# ---------- /api/stores ----------

def test_stores_lists_the_stores_this_households_list_uses(two):
    a, b, b_id = two
    item = a.post("/api/grocery-list/add", json={"item": "Paper towels"})
    assert item.status_code == 200
    assert a.post(f"/api/grocery-list/{item.json()['item_id']}/store", json={"store": "Costco"}).status_code == 200
    res = a.get("/api/stores")
    assert res.status_code == 200
    assert "Costco" in {s["name"] for s in res.json()["stores"]}
    assert "Costco" not in {s["name"] for s in b.get("/api/stores").json()["stores"]}


def test_store_assignment_on_another_households_line_does_not_move_it(two):
    a, b, _ = two
    item_id = a.post("/api/grocery-list/add", json={"item": "Dish soap"}).json()["item_id"]
    res = b.post(f"/api/grocery-list/{item_id}/store", json={"store": "Walmart"})
    assert res.status_code < 500, res.text
    conn = get_conn()
    store = conn.execute("SELECT store FROM grocery_items WHERE id = ?", (item_id,)).fetchone()["store"]
    conn.close()
    assert (store or "") != "Walmart"
    assert "Walmart" not in {s["name"] for s in a.get("/api/stores").json()["stores"]}


# ---------- /api/member-needs (GET) ----------

def test_member_needs_lists_this_households_people_only(two):
    a, b, b_id = two
    conn = get_conn()
    conn.execute("INSERT INTO members (household_id, name) VALUES (?, ?)", (A, "Adaline"))
    conn.execute("INSERT INTO members (household_id, name) VALUES (?, ?)", (b_id, "Bartholomew"))
    conn.commit()
    conn.close()
    res = a.get("/api/member-needs")
    assert res.status_code == 200
    a_names = {m["name"] for m in res.json()["members"]}
    b_names = {m["name"] for m in b.get("/api/member-needs").json()["members"]}
    assert "Adaline" in a_names and "Bartholomew" not in a_names
    assert "Bartholomew" in b_names and "Adaline" not in b_names


# ---------- /api/inventory/scan-receipt ----------

def _inventory_count():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS c FROM inventory_items").fetchone()["c"]
    conn.close()
    return n


def test_scan_receipt_returns_a_draft_and_writes_nothing(two, monkeypatch):
    a, _, _ = two
    seen = {}

    def fake(b64, media_type):
        seen["media_type"] = media_type
        return [{"item": "Milk", "quantity": "2 L", "category": "dairy"}]

    monkeypatch.setattr(main, "scan_receipt_image", fake)
    res = a.post("/api/inventory/scan-receipt", files={"photo": ("r.jpg", b"\xff\xd8\xff fake", "image/jpeg")})
    assert res.status_code == 200, res.text
    assert res.json()["items"] == [{"item": "Milk", "quantity": "2 L", "category": "dairy"}]
    assert seen["media_type"] == "image/jpeg"
    assert _inventory_count() == 0, "a scan is a draft; only confirm-scan writes"


@pytest.mark.parametrize("files,expect", [
    (None, 422),
    ({"photo": ("r.jpg", b"", "image/jpeg")}, 400),
    ({"photo": ("r.txt", b"hello", "text/plain")}, 400),
])
def test_scan_receipt_refuses_a_missing_empty_or_non_image_upload(two, monkeypatch, files, expect):
    a, _, _ = two
    monkeypatch.setattr(main, "scan_receipt_image", lambda *_: pytest.fail("model must not be called"))
    res = a.post("/api/inventory/scan-receipt", files=files) if files else a.post("/api/inventory/scan-receipt")
    assert res.status_code == expect, res.text
