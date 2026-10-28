"""
Route gaps, 2026-10-28: the routes the 2026-10-27 merges added to
app/main.py (`git diff c50a6725 4c51e9ce -- app/main.py`).

How these were picked. Every new route was matched against the client
calls in tests/. Two kinds of gap:

  1. GET /api/whats-home/{item_id} (the Kitchen item sheet) was the one new
     route no test drove over HTTP at all — the sheet's data was only ever
     read through tools.get_whats_home_item.
  2. Only POST /api/whats-home/undo had a household-B test. The isolation
     attack (test_isolation_attack.py) enumerates app.routes, so every new
     route IS attacked by id — but A owns no frozen row with a freeze
     record, no eaten-dinner receipt, no going-off card or swap, no kitchen
     add receipt and no use-up resolution, and every `undo: dict` body is
     sent as {}. So each new route's deeper lookup was never aimed at
     anything of A's. Here A builds each of those for real, through its
     own routes, and B replays A's real ids and tokens.

One bug came out of it, in app/tools/use_up.py (not in a file another
builder owns): the Undo on "Couldn't fit these → Freeze it" read WHICH ROW
to thaw from the client's token, not from the week's own record. A token
whose `freeze` was not a dict answered 500, and one naming another of the
household's freezer rows thawed THAT row while the week said the frozen
one was back on the list. It now thaws the row the record names.
"""
from __future__ import annotations

import datetime
import json

import pytest
from fastapi.testclient import TestClient

from conftest import household_today

from app import households, tools
from app.db import get_conn
from app.main import app

import test_going_off_on_today as going
import test_mark_eaten_names_it as eaten
from test_freezer_portion_planned import household, stub_model  # noqa: F401  (fixtures used by name)
from test_kitchen_tab import _row as _kitchen_row
from test_now_in_the_kitchen import _bought
from test_use_it_all_draft import _draft, _seed, _week_with
from test_freezer_portion_planned import _monday

B_PASSPHRASE = "route-gaps-1028-b-passphrase"


@pytest.fixture
def household_b():
    """A second household, signed in. 500s come back as responses, so a
    crash reads as a status, never as a passing exception."""
    households.create_household("Route Gaps 1028 B", B_PASSPHRASE)
    c = TestClient(app, raise_server_exceptions=False)
    res = c.post("/login", data={"password": B_PASSPHRASE, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return c


def _inv(item_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _all_of_as(table: str) -> list[dict]:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE household_id = 1 ORDER BY id")]
    finally:
        conn.close()


# ---------- 1. the Kitchen item sheet had no HTTP test ----------

def test_the_kitchen_item_sheet_reads_over_http(signed_in):
    item = _kitchen_row("Greek yogurt", days=2, category="dairy")
    r = signed_in.get(f"/api/whats-home/{item}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == tools.get_whats_home_item(item)
    assert body["id"] == item and body["item"] == "Greek yogurt" and body["location"] == "fridge"


def test_the_kitchen_item_sheet_refuses_a_gone_row_and_a_junk_id(signed_in):
    item = _kitchen_row("Greek yogurt")
    signed_in.post(f"/api/whats-home/{item}/used-up")
    assert signed_in.get(f"/api/whats-home/{item}").status_code == 404
    assert signed_in.get("/api/whats-home/987654").status_code == 404
    assert signed_in.get("/api/whats-home/yogurt").status_code == 422


# ---------- 2. household B against A's kitchen rows ----------

@pytest.mark.parametrize("method,path,body", [
    ("get", "", None),
    ("post", "/amount", {"level": "almost_gone"}),
    ("post", "/used-up", None),
    ("post", "/thrown-out", None),
    ("post", "/move", {"location": "pantry"}),
])
def test_household_b_cannot_read_or_change_as_kitchen_row(household_b, method, path, body):
    item = _kitchen_row("Zqs raspberries", days=1)
    before = _inv(item)
    url = f"/api/whats-home/{item}{path}"
    r = household_b.get(url) if method == "get" else household_b.post(url, json=body)
    assert r.status_code == 404, r.text
    assert "Zqs" not in r.text
    assert _inv(item) == before
    assert _all_of_as("grocery_items") == [], "an almost-gone on A's row put nothing on A's list"


def test_household_b_cannot_freeze_as_row_or_thaw_it_with_as_own_undo(signed_in, household_b):
    fridge = _kitchen_row("Zqs chicken thighs", days=1, category="meat/seafood")
    r = household_b.post(f"/api/inventory/{fridge}/freeze")
    assert r.status_code == 404 and "Zqs" not in r.text
    assert _inv(fridge)["location"] == "fridge"

    frozen = signed_in.post(f"/api/inventory/{fridge}/freeze").json()
    before = _inv(fridge)
    assert before["location"] == "freezer"
    r = household_b.post("/api/inventory/unfreeze", json={"undo": frozen["undo"]})
    assert r.status_code == 200 and r.json()["undone"] is False
    assert "Zqs" not in r.text
    assert _inv(fridge) == before
    # A's own Undo still lands.
    back = signed_in.post("/api/inventory/unfreeze", json={"undo": frozen["undo"]}).json()
    assert back["undone"] is True and _inv(fridge)["location"] == "fridge"


def test_household_b_cannot_undo_as_kitchen_add(signed_in, household_b):
    added = signed_in.post("/api/whats-home/add", json={
        "kind": "type", "items": [{"item": "Zqs oat milk", "location": "fridge"}]}).json()
    rows = _all_of_as("inventory_items")
    assert [r["item"] for r in rows] == ["Zqs oat milk"]
    r = household_b.post("/api/whats-home/add/undo", json={"undo": added["undo"]})
    assert r.status_code == 400 and "Zqs" not in r.text
    assert _all_of_as("inventory_items") == rows


def test_household_bs_quick_check_neither_lists_nor_touches_as_rows(household_b):
    old = _kitchen_row("Zqs hummus", days=-3, category="dairy")
    other = _kitchen_row("Zqs cheddar", days=10, category="dairy")
    before = _all_of_as("inventory_items")
    for loc in ("", "?location=fridge"):
        r = household_b.get(f"/api/inventory/check{loc}")
        assert r.status_code == 200 and r.json() == {"items": []}
    r = household_b.post("/api/inventory/check", json={"gone": [old], "confirmed": [other]})
    assert r.status_code == 200 and r.json() == {"removed": [], "confirmed": []}
    assert _all_of_as("inventory_items") == before


def test_household_bs_kitchen_and_shop_card_show_nothing_of_as(household_b):
    _kitchen_row("Zqs feta", days=1, category="dairy")
    _bought("Zqs lemons", "produce", "3")
    home = household_b.get("/api/whats-home").json()
    assert home["count"] == 0 and "Zqs" not in json.dumps(home)
    now = household_b.get("/api/grocery-list/now-in-kitchen").json()
    assert now == {"total": 0, "places": [], "use_first": []}


# ---------- 3. household B against A's eaten dinner ----------

def test_household_b_cannot_undo_as_eaten_dinner(signed_in, household_b):
    eaten._lemon_chicken()
    eaten._stock()
    entry = eaten._entry()
    took = signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"}).json()
    assert took["kitchen_took"], "premise: the tick took something"
    stock = _all_of_as("inventory_items")
    meals = _all_of_as("meal_plan_entries")

    r = household_b.post("/api/cooker/undo-eaten", json={"entry_id": entry})
    assert r.status_code == 404
    assert "chicken" not in r.text.lower()
    assert _all_of_as("inventory_items") == stock
    assert _all_of_as("meal_plan_entries") == meals


def test_undo_eaten_refuses_a_missing_entry_and_a_junk_body(signed_in):
    assert signed_in.post("/api/cooker/undo-eaten", json={"entry_id": 987654}).status_code == 404
    assert signed_in.post("/api/cooker/undo-eaten", json={"entry_id": "soon"}).status_code == 422
    assert signed_in.post("/api/cooker/undo-eaten", json={}).status_code == 422


# ---------- 4. household B against A's going-off card and swap ----------

def test_household_b_cannot_take_leave_or_undo_as_going_off_swap(signed_in, household_b):
    plan, pasta, cook, reheat, spinach = going._mockup_scene()
    tools.answer_yesterday(pasta, "skipped", household_today())
    card = signed_in.get("/api/today/going-off").json()["card"]
    assert card and card["item_id"] == spinach and card["entry_id"] == pasta

    # B never sees it, and its taps on A's ids change nothing of A's.
    seen = household_b.get("/api/today/going-off").json()
    assert seen["card"] is None and seen["line"] is None
    meals = _all_of_as("meal_plan_entries")
    r = household_b.post("/api/today/going-off/swap", json={"item_id": spinach, "entry_id": pasta})
    assert r.status_code == 409 and "Spinach" not in r.text
    r = household_b.post("/api/today/going-off/leave", json={"item_id": spinach})
    assert r.status_code == 200 and r.json()["card"] is None
    assert _all_of_as("meal_plan_entries") == meals
    assert signed_in.get("/api/today/going-off").json()["card"] == card, "B's Leave it is B's alone"

    # A swaps; B replays A's undo.
    out = signed_in.post("/api/today/going-off/swap", json={"item_id": spinach, "entry_id": pasta}).json()
    assert out["status"] == "swapped"
    swapped = _all_of_as("meal_plan_entries")
    r = household_b.post("/api/today/going-off/undo", json={"undo_entry_id": out["undo_entry_id"]})
    assert r.status_code == 200 and r.json()["status"] == "refused"
    assert "Spinach" not in r.text and "Chili" not in r.text
    assert _all_of_as("meal_plan_entries") == swapped
    back = signed_in.post("/api/today/going-off/undo", json={"undo_entry_id": out["undo_entry_id"]}).json()
    assert back["status"] == "restored"


def test_going_off_routes_answer_odd_input_with_4xx(signed_in):
    assert signed_in.post("/api/today/going-off/swap", json={"item_id": 987654, "entry_id": 987654}).status_code == 409
    assert signed_in.post("/api/today/going-off/swap", json={"item_id": "x", "entry_id": 1}).status_code == 422
    assert signed_in.post("/api/today/going-off/leave", json={}).status_code == 422
    assert signed_in.post("/api/today/going-off/undo", json={"undo_entry_id": 987654}).json()["status"] == "refused"
    assert signed_in.post("/api/today/going-off/undo", json={"undo_entry_id": "x"}).status_code == 422


# ---------- 5. household B against A's "Use these up" ----------

def _drafted_week(stub_model):
    week = _monday(1)
    d, ids = _seed(week)
    plan_id, _ = _draft(stub_model, week, _week_with(week, ids, d))
    return week, plan_id, ids


def _use_up_record(plan_id: int) -> str:
    conn = get_conn()
    try:
        return conn.execute("SELECT use_up_json FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()[0]
    finally:
        conn.close()


def test_household_b_cannot_read_or_settle_as_use_up(household, stub_model, signed_in, household_b):
    week, plan_id, ids = _drafted_week(stub_model)
    assert signed_in.get(f"/api/week/{week}/use-up").json()["items"], "premise: A has things to use up"
    seen = household_b.get(f"/api/week/{week}/use-up")
    assert seen.status_code == 200 and seen.json() == {"items": [], "off": []}

    record = _use_up_record(plan_id)
    cabbage = _inv(ids["cabbage"])
    for action in ("freeze", "side", "let_go"):
        r = household_b.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}", json={"action": action})
        assert r.status_code == 400 and "cabbage" not in r.text.lower()
    assert _use_up_record(plan_id) == record and _inv(ids["cabbage"]) == cabbage

    # A freezes it; B replays A's undo against A's plan.
    body = signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}", json={"action": "freeze"}).json()
    frozen_record, frozen_row = _use_up_record(plan_id), _inv(ids["cabbage"])
    r = household_b.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}/undo", json={"undo": body["undo"]})
    assert r.status_code == 400
    assert _use_up_record(plan_id) == frozen_record and _inv(ids["cabbage"]) == frozen_row


# ---------- 6. the Use-up Undo read which row to thaw from the client ----------

@pytest.mark.parametrize("token", [
    {},
    {"freeze": "cabbage"},
    {"freeze": 7},
    {"freeze": ["x"]},
])
def test_a_junk_freeze_token_is_not_a_500_and_the_week_record_wins(household, stub_model, signed_in, token):
    """CATCH — undo_resolve handed the client's `freeze` straight to
    unfreeze_item, which calls .get on it: a str/int/list answered 500. An
    empty token answered 200 and thawed nothing, so the row sat in the
    freezer while "Couldn't fit these" offered "Freeze it" for it again.
    The week's own record says what was frozen; Undo thaws that."""
    week, plan_id, ids = _drafted_week(stub_model)
    signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}", json={"action": "freeze"})
    assert _inv(ids["cabbage"])["location"] == "freezer"
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies = signed_in.cookies
    r = c.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}/undo", json={"undo": token})
    assert r.status_code == 200, r.text
    assert _inv(ids["cabbage"])["location"] == "fridge"
    assert "Half a cabbage" in [x["item"] for x in tools.get_week_menu(plan_id)["use_up"]["couldnt_fit"]]


def test_the_use_up_undo_thaws_the_row_it_froze_never_one_the_token_names(household, stub_model, signed_in):
    """CATCH — a token naming ANOTHER of the household's freezer rows (one
    frozen through the Kitchen tab, with its own freeze record) thawed that
    row, and the cabbage stayed in the freezer while the week offered it
    again as if it were still in the fridge."""
    week, plan_id, ids = _drafted_week(stub_model)
    peas = _kitchen_row("Peas", days=2)
    signed_in.post(f"/api/inventory/{peas}/freeze")
    body = signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}", json={"action": "freeze"}).json()
    forged = {"freeze": {**body["undo"]["freeze"], "item_id": peas}}
    r = signed_in.post(f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}/undo", json={"undo": forged})
    assert r.status_code == 200
    assert _inv(peas)["location"] == "freezer", "the peas were never this card's"
    assert _inv(ids["cabbage"])["location"] == "fridge"


def test_use_up_routes_answer_odd_input_with_4xx(household, stub_model, signed_in):
    week, plan_id, ids = _drafted_week(stub_model)
    url = f"/api/week-plan/{plan_id}/use-up/{ids['cabbage']}"
    assert signed_in.post(url, json={"action": "boil"}).status_code == 400
    assert signed_in.post(url, json={}).status_code == 422
    assert signed_in.post(f"/api/week-plan/{plan_id}/use-up/987654", json={"action": "side"}).status_code == 400
    assert signed_in.post(f"/api/week-plan/987654/use-up/{ids['cabbage']}", json={"action": "side"}).status_code == 400
    # Nothing settled yet: an Undo has nothing to take back, and is not a 500.
    assert signed_in.post(f"{url}/undo", json={"undo": None}).status_code < 500
    assert signed_in.get("/api/week/not-a-week/use-up").status_code == 400
    assert signed_in.get(f"/api/week/{week}/use-up?day_count=0").status_code == 400
    assert signed_in.get(f"/api/week/{week}/use-up?day_count=lots").status_code == 422


# ---------- 7. odd input on the kitchen writes ----------

def test_kitchen_writes_answer_odd_input_with_4xx(signed_in):
    item = _kitchen_row("Greek yogurt")
    before = _inv(item)
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies = signed_in.cookies
    for url, body in [
        (f"/api/whats-home/{item}/move", {"location": "garage"}),
        (f"/api/whats-home/{item}/move", {"location": ""}),
        (f"/api/whats-home/{item}/amount", {"level": ""}),
        (f"/api/whats-home/{item}/amount", {}),
        ("/api/inventory/unfreeze", {"undo": {"item_id": "yogurt"}}),
        ("/api/inventory/unfreeze", {"undo": "x"}),
        ("/api/whats-home/undo", {"undo": {"kind": "removed", "item_id": "x", "removal_id": "y"}}),
        ("/api/whats-home/add", {"kind": "type", "items": [{"item": 5}]}),
        ("/api/whats-home/add", {"kind": "type", "items": "milk"}),
        ("/api/inventory/check", {"gone": ["x"], "confirmed": []}),
    ]:
        r = c.post(url, json=body)
        assert 400 <= r.status_code < 500, (url, body, r.status_code, r.text)
    assert c.get("/api/inventory/check?location=garage").status_code == 400
    assert _inv(item) == before
