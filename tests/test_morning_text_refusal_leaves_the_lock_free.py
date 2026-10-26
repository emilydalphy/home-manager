"""
A morning-text change that is refused changes nothing — and leaves the
household able to save (defect hunt, 2026-10-21).

THE BUG: digest.set_morning_text_for_member wrote the number first and
checked the hour (normalise_time) and the parts (_checked_parts) after,
with a bare conn.close() that the refusal skipped. So "text me on
416-555-0100 at noon" — from the chat, or any POST /api/morning-text with
a time it couldn't read — answered a fine 400 and left the connection
holding SQLite's write lock: the household's next save ANYWHERE (measured on
a real uvicorn: the other adult's Save on the same sheet) waited out the
5-second busy timeout and 500'd. Now the function runs under db.write().

The traceback is held on purpose, as tests/test_memory_edit_leaves_the_lock_free.py
explains: it is what keeps a leaked connection alive in production.
"""
from __future__ import annotations

import gc
import sqlite3
import sys

import pytest

from app import db, tools
from app.tools import digest


def _adults(*names):
    for n in names:
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")


def _member(name: str) -> dict:
    return next(a for a in tools.get_morning_text_settings()["adults"] if a["name"] == name)


def _next_write_is_not_locked_out() -> bool:
    probe = db.get_conn()
    probe.execute("PRAGMA busy_timeout = 300")
    try:
        probe.execute("BEGIN IMMEDIATE")
        probe.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()


def _refused_holding_the_traceback(fn, **kwargs):
    held = None
    try:
        fn(**kwargs)
    except ValueError:
        held = sys.exc_info()[2]
    assert held is not None, f"{fn.__name__}{kwargs} was expected to be refused"
    gc.collect()
    return held


def test_a_time_it_cannot_read_is_refused_and_writes_nothing():
    """CATCH. Red before the fix: the lock is held, and the number had begun to change."""
    _adults("Emily")
    me = _member("Emily")
    held = _refused_holding_the_traceback(
        digest.set_morning_text_for_member, member_id=me["member_id"], phone="416-555-0100", on=True, time="noon",
    )
    try:
        assert _next_write_is_not_locked_out()
        assert _member("Emily")["phone"] == ""
        assert tools.get_morning_text_settings()["time"] == "07:00"
    finally:
        del held
        gc.collect()


def test_an_unknown_part_from_the_chat_is_refused_and_leaves_the_switch_alone():
    """The chat's own door (set_morning_text) with a number, on, and a part it doesn't have."""
    _adults("Emily")
    tools.set_morning_text(name="Emily", phone="416-555-0100", on=False)
    held = _refused_holding_the_traceback(
        tools.set_morning_text, name="Emily", on=True, add_parts=["weather"],
    )
    try:
        assert _next_write_is_not_locked_out()
        assert _member("Emily")["on"] is False
        assert "weather" not in tools.get_morning_text_settings()["parts"]
    finally:
        del held
        gc.collect()


def test_the_other_adults_save_still_lands_after_one_refused_time(signed_in):
    """The sheet's own shape: one POST per adult, the first refused."""
    client = signed_in
    _adults("Emily", "Vineeth")
    emily, vineeth = _member("Emily"), _member("Vineeth")
    refused = client.post("/api/morning-text", json={
        "member_id": emily["member_id"], "phone": "416-555-0100", "on": True, "time": "half seven",
    })
    assert refused.status_code == 400
    assert "didn't catch the time" in refused.json()["detail"]
    saved = client.post("/api/morning-text", json={
        "member_id": vineeth["member_id"], "phone": "416-555-0101", "on": True, "time": "07:30",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["on"] is True
