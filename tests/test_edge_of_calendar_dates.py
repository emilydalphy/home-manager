"""
A date at the very edge of the calendar is refused, not a server error.

Found 2026-10-25: 9999-12-31 (and, for intake, 0001-01-01) is a perfectly
good ISO date, so it walked straight past each route's "must be an ISO
date" check into the date maths behind it -- a week's Monday, a holiday
window, a lookback -- and Python's date range ends there, so the answer
was an OverflowError surfaced as a 500 and an error_events row. Each route
now goes through main._route_date, which refuses a year outside
1900-2999 the same way it refuses a garbled date: a plain 400, nothing
written, nothing recorded as broken.
"""
from __future__ import annotations

import pytest

from app.db import get_conn


def _row_counts() -> dict[str, int]:
    conn = get_conn()
    try:
        names = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


EDGE_REQUESTS = [
    ("GET", "/api/holidays?start=9999-12-31", None, "start"),
    ("GET", "/api/today/moves?date=9999-12-31", None, "date"),
    ("GET", "/api/week/9999-12-31/intake", None, "week_start"),
    ("GET", "/api/week/0001-01-01/intake", None, "week_start"),
    ("POST", "/api/week/9999-12-31/intake", {}, "week_start"),
    ("GET", "/api/week/9999-12-31/attendance", None, "week_start"),
    # The siblings the review found: the period check both generate routes
    # share (the stream used to answer 200 and then an error frame), the
    # trip range, and a tick's timeline day -- refused before the tick.
    ("POST", "/api/week/9999-12-31/generate", {"day_count": 7}, "week_start"),
    ("POST", "/api/week/9999-12-31/generate/stream", {"day_count": 7}, "week_start"),
    ("POST", "/api/week/2026-10-26/generate",
     {"day_count": 7, "period_start": "9999-12-31"}, "period_start"),
    ("POST", "/api/week/2026-10-26/away-stretch",
     {"from_date": "9999-12-31", "to_date": "9999-12-31"}, "from_date"),
    ("POST", "/api/today/moves/cook:1/done?date=9999-12-31", {"done": True}, "date"),
    ("POST", "/api/today/moves/cook:1/done", {"done": True, "date": "0001-01-01"}, "date"),
]


@pytest.mark.parametrize("method,path,body,name", EDGE_REQUESTS)
def test_an_edge_of_calendar_date_is_a_plain_refusal(signed_in, method, path, body, name):
    before = _row_counts()
    res = signed_in.request(method, path, json=body)
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == f"{name} has to fall between 1900 and 2999."
    # Nothing written -- and in particular no error_events row, which is
    # what made each of these look like the app breaking.
    assert _row_counts() == before


def test_a_garbled_date_keeps_its_old_sentence_and_an_ordinary_date_still_works(signed_in):
    """The edge check sits beside the ISO check, not in place of it."""
    res = signed_in.get("/api/week/not-a-date/attendance")
    assert res.status_code == 400
    assert res.json()["detail"] == "week_start must be an ISO date (YYYY-MM-DD)."
    # 1999 is inside the window on purpose: tests ask about it to get "no plan".
    assert signed_in.post("/api/week/1999-01-04/approve", json={}).status_code == 404
    assert signed_in.get("/api/today/moves?date=2026-10-27").status_code == 200
