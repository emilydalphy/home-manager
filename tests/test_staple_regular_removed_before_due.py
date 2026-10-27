"""
Taking off a regular you added yourself, before the staple is due, moves
nothing on the staple.

Found 2026-10-27 driving Shop over HTTP: Coffee, a staple just bought
("due in 3 weeks"), ticked as a regular on "Before you shop" and then taken
off again with the row's Remove. remove_grocery_item counted that as "not
this trip" (staples.note_line_removed -> _skip), so the due date was pulled
IN from three weeks to one — coffee came back as "probably running low"
two weeks early — and three such removes auto-paused a staple the
household buys. The toast's Undo then made it due today.

MUTATIONS RUN (read off the run):
  1. the not-due branch in note_line_removed removed      -> 2 failed
  2. the rhythm_kept early return in reverse_last_answer  -> 1 failed
"""
from __future__ import annotations

from app.db import get_conn
from conftest import household_today


def _staple(sid: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM staples WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return dict(row)


def _line(item_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM grocery_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row)


def _add_regular(client, name: str) -> int:
    r = client.post("/api/grocery-list/before-shop/regulars", json={"items": [name]})
    assert r.status_code == 200, r.text
    return r.json()["added"][0]["item_id"]


def _coffee_not_due(client) -> tuple[int, str]:
    r = client.post("/api/staples/add", json={"item": "Coffee"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["next_due_at"] > household_today().isoformat()
    return s["id"], s["next_due_at"]


def test_removing_a_regular_before_it_is_due_keeps_the_rhythm(signed_in):
    sid, due = _coffee_not_due(signed_in)
    for _ in range(3):
        line_id = _add_regular(signed_in, "Coffee")
        assert signed_in.post(f"/api/grocery-list/{line_id}/remove").status_code == 200
        s = _staple(sid)
        assert s["next_due_at"] == due
        assert s["skip_streak"] == 0
        assert s["paused"] == 0


def test_undo_puts_the_line_back_and_leaves_the_due_date(signed_in):
    sid, due = _coffee_not_due(signed_in)
    line_id = _add_regular(signed_in, "Coffee")
    r = signed_in.post(f"/api/grocery-list/{line_id}/remove")
    assert r.json()["staple_id"] == sid

    u = signed_in.post(f"/api/staples/{sid}/undo")
    assert u.status_code == 200, u.text
    assert u.json()["undone"] is True
    assert _line(line_id)["status"] == "needed"
    assert _staple(sid)["next_due_at"] == due


def test_removing_a_due_staples_own_line_is_still_not_this_trip(signed_in):
    r = signed_in.post("/api/staples/add", json={"item": "Dish soap", "category": "household", "running_low": True})
    sid = r.json()["id"]
    view = signed_in.get("/api/grocery-list?status=needed").json()
    line = [it for s in view["sections"] for it in s["items"] if it["staple_id"] == sid]
    assert len(line) == 1
    assert signed_in.post(f"/api/grocery-list/{line[0]['id']}/remove").status_code == 200
    s = _staple(sid)
    assert s["skip_streak"] == 1
    assert s["next_due_at"] > household_today().isoformat()
