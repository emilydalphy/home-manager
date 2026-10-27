"""
Junk input at the routes, 2026-10-25 (standing work, third route pass after
test_untested_routes_2026_10_22.py and _10_24.py on their own branches).

How these were picked. Every route on main already has a test that reaches
it by path, and test_isolation_attack.py throws another household's ids at
all of them — so this pass threw JUNK instead: each declared field of each
authenticated route, one at a time, set to a number past 2**63, a negative,
a blank, a 20,000-character string, an impossible date. What answered 5xx:

  * ~60 routes taking an id or a count (path, query or body): a number
    SQLite can't bind raised OverflowError inside the query, which every
    route reported as "something went wrong on my side" and filed as
    breakage in the morning report. One rule in main's two error handlers
    now answers 400 for all of them, a route written tomorrow included.
  * POST /api/needs-you/dinner: a date that isn't one ("", "2026-02-30")
    reached plan_meal's fromisoformat and came back as a recorded 500.

And the two routes no test on main or any branch drove at all:
POST /api/inventory/scan-fridge and /scan-pantry (model stubbed).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import household_date

from app import main, tools
from app.db import get_conn
from app.main import app

BIG = 10**20
_TINY_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


def _server_errors():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT where_, detail FROM error_events WHERE kind = 'server'").fetchall()]
    finally:
        conn.close()


@pytest.fixture
def lenient():
    """Signed in, with a server error answered rather than raised into the
    test — the response a browser would actually get, which is what an
    unhandled exception's handler is for."""
    with TestClient(app, raise_server_exceptions=False) as c:
        res = c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
        assert res.status_code == 303
        yield c


# ---------- a number too big to be an id ----------

@pytest.mark.parametrize("method,url,body", [
    # Unhandled in the route (it catches only ValueError).
    ("POST", f"/api/staples/{BIG}/pause", None),
    # Caught by the route's own `except Exception` and re-raised as a 500.
    ("POST", f"/api/grocery-list/{BIG}/remove", None),
    ("GET", f"/api/cooker-view?weekly_plan_id={BIG}", None),
    # In a body, as a count rather than an id.
    ("POST", "/api/shopping-trips/close", {"store": "", "item_count": BIG}),
])
def test_a_number_past_what_sqlite_holds_is_a_400_not_recorded_breakage(lenient, method, url, body):
    res = lenient.request(method, url, json=body)
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == main.NUMBER_TOO_BIG_LINE
    assert _server_errors() == []


def test_an_overflow_that_is_not_a_bound_number_is_still_a_server_error(lenient, monkeypatch):
    """The rule reads SQLite's bind message only. Date arithmetic running
    off the calendar could be a stored date gone wrong — a real bug, which
    must keep reaching the morning report."""
    def _off_the_calendar(*a, **k):
        raise OverflowError("date value out of range")
    monkeypatch.setattr(main.tools, "get_cooker_view", _off_the_calendar)
    res = lenient.get("/api/cooker-view")
    assert res.status_code == 500
    assert [e["where_"] for e in _server_errors()] == ["/api/cooker-view"]


# ---------- POST /api/needs-you/dinner ----------

@pytest.mark.parametrize("bad", ["", "soon", "2026-02-30"])
def test_needs_you_dinner_refuses_a_date_that_is_not_one(signed_in, bad):
    tools.add_recipe("Weeknight Dal", ingredients=[{"item": "red lentils", "qty": "1 cup"}])
    res = signed_in.post("/api/needs-you/dinner", json={"date": bad, "meal": "Weeknight Dal"})
    assert res.status_code == 400
    assert "ISO date" in res.json()["detail"]
    conn = get_conn()
    planned = conn.execute("SELECT COUNT(*) AS c FROM meal_plan_entries").fetchone()["c"]
    conn.close()
    assert planned == 0
    assert _server_errors() == []


def test_needs_you_dinner_still_plans_a_real_day(signed_in):
    tools.add_recipe("Weeknight Dal", ingredients=[{"item": "red lentils", "qty": "1 cup"}])
    day = household_date(1)
    res = signed_in.post("/api/needs-you/dinner", json={"date": day, "meal": "Weeknight Dal"})
    assert res.status_code == 200, res.text
    conn = get_conn()
    row = conn.execute(
        "SELECT date FROM meal_plan_entries WHERE slot = 'dinner'").fetchone()
    conn.close()
    assert row["date"] == day


# ---------- POST /api/inventory/scan-fridge, /scan-pantry ----------

@pytest.mark.parametrize("path,scanner", [
    ("/api/inventory/scan-fridge", "scan_fridge_photo"),
    ("/api/inventory/scan-pantry", "scan_pantry_photo"),
])
def test_shelf_scans_return_a_draft_and_refuse_what_is_not_a_photo(signed_in, monkeypatch, path, scanner):
    calls = []

    def fake(b64, media_type):
        calls.append(media_type)
        return [{"item": "Yogurt", "quantity": "1 tub", "location": "fridge"}]

    monkeypatch.setattr(main, scanner, fake)

    ok = signed_in.post(path, files={"photo": ("shelf.jpg", _TINY_JPEG, "image/jpeg")})
    assert ok.status_code == 200
    # UPDATED 2026-10-27 (Loop Board C2): the draft is shaped for the review
    # on the way out — each row gains `sure` (and a default category and
    # confidence) — and is otherwise what the scan read.
    [row] = ok.json()["items"]
    assert {k: row[k] for k in ("item", "quantity", "location")} == {"item": "Yogurt", "quantity": "1 tub", "location": "fridge"}
    assert row["sure"] is True
    # A draft only — /api/inventory/confirm-scan is what writes.
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) AS c FROM inventory_items").fetchone()["c"] == 0
    conn.close()

    not_a_photo = signed_in.post(path, files={"photo": ("notes.txt", b"milk", "text/plain")})
    empty = signed_in.post(path, files={"photo": ("shelf.jpg", b"", "image/jpeg")})
    assert not_a_photo.status_code == 400
    assert empty.status_code == 400
    # Neither refusal spent a model call.
    assert calls == ["image/jpeg"]
