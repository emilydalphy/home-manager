"""
A staple answer from a stale screen changes nothing once the line is bought.

Found 2026-10-20 driving the Grocery tab over HTTP with two signed-in
phones on one household: phone A ticked "Dish soap" (a 3-week staple, due
today), phone B — still showing the line with its "We have plenty" / "Not
this trip" buttons — tapped "Not this trip". decide_staple_line never looked
at the line's status, so the just-bought staple was pulled from "due in
three weeks" to "ask again next week" and a skip was counted toward the
auto-pause. Phone B's toast offered Undo, and undo_staple_decision made the
staple due TODAY: a second "Dish soap" line on the list beside the one just
bought. Two phones both tapping "Not this trip" counted two skips.

MUTATIONS RUN (read off the run):
  1. the line-status guard in decide_staple_line removed     -> 3 failed
  2. the bought-since guard in undo_staple_decision removed  -> 1 failed
"""
from __future__ import annotations

import datetime as _dt

import pytest

from app.db import get_conn
from app.tools import staples as st
from conftest import household_pin

pytestmark = pytest.mark.today(household_pin(10, on=_dt.date(2026, 10, 21)))


def _needed(client) -> list[dict]:
    view = client.get("/api/grocery-list?status=needed").json()
    return [it for s in view["sections"] for it in s["items"]]


def _staple(sid: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM staples WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return dict(row)


def _soap_on_the_list(client) -> tuple[int, int]:
    r = client.post("/api/staples/add", json={
        "item": "Dish soap", "category": "household", "every_days": 21, "running_low": True,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    line = [it for it in _needed(client) if it["staple_id"] == sid]
    assert len(line) == 1
    return sid, line[0]["id"]


def test_not_this_trip_after_the_other_phone_bought_it_changes_nothing(signed_in):
    sid, line_id = _soap_on_the_list(signed_in)
    assert signed_in.post(f"/api/grocery-list/{line_id}/status", json={"status": "purchased"}).status_code == 200
    bought = _staple(sid)
    assert bought["skip_streak"] == 0

    r = signed_in.post(f"/api/grocery-list/{line_id}/staple", json={"decision": "skip"})
    assert r.status_code == 200
    assert r.json()["unchanged"] is True and r.json()["line_status"] == "purchased"
    after = _staple(sid)
    # Still three weeks out from the purchase, no skip counted.
    assert after["next_due_at"] == bought["next_due_at"]
    assert after["skip_streak"] == 0
    conn = get_conn()
    kinds = [r["kind"] for r in conn.execute(
        "SELECT kind FROM staple_events WHERE staple_id = ? ORDER BY id", (sid,))]
    conn.close()
    assert "skipped" not in kinds


def test_the_stale_toasts_undo_does_not_put_a_second_line_on_the_list(signed_in, monkeypatch):
    sid, old_line = _soap_on_the_list(signed_in)
    # Three weeks ago: "We have plenty" — the older answer an undo would
    # wrongly reverse. Today it is due again and back on the list.
    monkeypatch.setattr(st, "_TODAY_OVERRIDE", st._today() - _dt.timedelta(days=21))
    signed_in.post(f"/api/grocery-list/{old_line}/staple", json={"decision": "plenty"})
    monkeypatch.setattr(st, "_TODAY_OVERRIDE", None)
    conn = get_conn()  # that answer's removed line is from three weeks ago too
    conn.execute("UPDATE grocery_items SET removed_at = datetime('now', '-21 days') WHERE id = ?", (old_line,))
    conn.commit()
    conn.close()
    lines = [it["id"] for it in _needed(signed_in) if it["staple_id"] == sid]
    assert len(lines) == 1
    line_id = lines[0]

    signed_in.post(f"/api/grocery-list/{line_id}/status", json={"status": "purchased"})
    before = _staple(sid)
    signed_in.post(f"/api/grocery-list/{line_id}/staple", json={"decision": "skip"})
    u = signed_in.post(f"/api/staples/{sid}/undo")
    assert u.status_code == 200 and u.json()["undone"] is False
    assert [it for it in _needed(signed_in) if it["staple_id"] == sid] == []
    assert _staple(sid)["next_due_at"] == before["next_due_at"]


def test_two_phones_tapping_not_this_trip_count_one_skip(signed_in):
    sid, line_id = _soap_on_the_list(signed_in)
    first = signed_in.post(f"/api/grocery-list/{line_id}/staple", json={"decision": "skip"}).json()
    second = signed_in.post(f"/api/grocery-list/{line_id}/staple", json={"decision": "skip"}).json()
    assert first["skip_streak"] == 1 and "unchanged" not in first
    assert second["unchanged"] is True and second["skip_streak"] == 1
    assert _staple(sid)["skip_streak"] == 1


def test_an_unticked_line_answers_normally_again(signed_in):
    # Put back after a tick: the line is on the list to buy, so its
    # buttons mean what they say, and Undo works.
    sid, line_id = _soap_on_the_list(signed_in)
    signed_in.post(f"/api/grocery-list/{line_id}/status", json={"status": "purchased"})
    signed_in.post(f"/api/grocery-list/{line_id}/status", json={"status": "needed"})
    r = signed_in.post(f"/api/grocery-list/{line_id}/staple", json={"decision": "skip"}).json()
    assert "unchanged" not in r and r["skip_streak"] == 1
    u = signed_in.post(f"/api/staples/{sid}/undo").json()
    assert u["undone"] is True
    assert [it["id"] for it in _needed(signed_in) if it["staple_id"] == sid] == [line_id]
