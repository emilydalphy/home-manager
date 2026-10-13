"""
The Monday ask (Loop Board "When a week ends with unbought items and next
week was already approved, ask keep-or-drop on Monday", Emily 2026-10-03;
rebuilt on main 2026-10-13).

A household that approved next week while this week was still running was
never asked about this week's leftovers: approval only sets aside weeks
that have already ENDED. On Monday the old lines simply stayed on the list.
Now the first read of Today or Shop after a week's last day sets them aside
into the same "Still on the list from last week" step
(grocery.set_aside_ended_weeks).
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery


def _today() -> datetime.date:
    return _grocery._household_today()


def _plan(start: datetime.date, days: int = 7, approved: bool = False) -> int:
    pid = tools.create_weekly_plan(start.isoformat(), day_count=days)["weekly_plan_id"]
    if approved:
        conn = get_conn()
        conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (pid,))
        conn.commit()
        conn.close()
    return pid


def _line(item: str, plan: int | None, status: str = "needed", excluded: int = 0) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, status, excluded_from_list) "
        "VALUES (1, ?, '1', 'produce', 'Emily', ?, ?, ?)",
        (item, plan, status, excluded),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _status(line_id: int) -> str:
    conn = get_conn()
    row = conn.execute("SELECT status FROM grocery_items WHERE id = ?", (line_id,)).fetchone()
    conn.close()
    return row["status"] if row else "gone"


@pytest.fixture
def week_ended_with_next_approved():
    """Last week ended yesterday; this week was approved early and runs now."""
    today = _today()
    ended = _plan(today - datetime.timedelta(days=7), days=7)
    _plan(today, days=7, approved=True)
    return ended


def test_the_first_open_after_the_last_day_asks_about_the_unbought_lines(signed_in, week_ended_with_next_approved):
    ended = week_ended_with_next_approved
    left = _line("Leeks", ended)
    ticked = _line("Lemons", ended, status="purchased")
    mine = _line("Oat milk", None)
    aside = _line("Saffron", ended, excluded=1)
    assert signed_in.get("/api/today/moves").status_code == 200
    assert _status(left) == "carried"
    # Ticked lines, a person's own line and one being bought elsewhere are
    # never asked about.
    assert (_status(ticked), _status(mine), _status(aside)) == ("purchased", "needed", "needed")
    carried = signed_in.get("/api/grocery-list/carried-over").json()["items"]
    assert [c["item"] for c in carried] == ["Leeks"]


def test_answers_are_not_asked_again_and_decide_later_still_waits(signed_in, week_ended_with_next_approved):
    ended = week_ended_with_next_approved
    keep, drop, later = _line("Leeks", ended), _line("Shallots", ended), _line("Fennel", ended)
    signed_in.get("/api/grocery-list/carried-over")
    assert signed_in.post(f"/api/grocery-list/{keep}/carried-over", json={"decision": "keep"}).status_code == 200
    assert signed_in.post(f"/api/grocery-list/{drop}/carried-over", json={"decision": "drop"}).status_code == 200
    # The same day, opened again — Today, then Shop.
    signed_in.get("/api/today/moves")
    signed_in.get("/api/grocery-list/by-store?status=needed")
    carried = signed_in.get("/api/grocery-list/carried-over").json()["items"]
    assert [c["item"] for c in carried] == ["Fennel"]
    assert (_status(keep), _status(drop), _status(later)) == ("needed", "removed", "carried")


def test_a_week_on_its_last_day_is_never_asked_about(signed_in):
    today = _today()
    running = _plan(today - datetime.timedelta(days=6), days=7)  # last day is today
    _plan(today + datetime.timedelta(days=1), days=7, approved=True)
    line = _line("Leeks", running)
    signed_in.get("/api/grocery-list/by-store?status=needed")
    assert _status(line) == "needed"


def test_with_no_later_week_approved_approval_asks_as_it_always_has(signed_in):
    today = _today()
    ended = _plan(today - datetime.timedelta(days=7), days=7)
    _plan(today, days=7)  # a draft, not approved
    line = _line("Leeks", ended)
    signed_in.get("/api/today/moves")
    signed_in.get("/api/grocery-list/carried-over")
    assert _status(line) == "needed"
