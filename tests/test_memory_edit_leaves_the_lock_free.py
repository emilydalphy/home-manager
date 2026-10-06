"""
A "remember that…" edit that fails, fails on its own (Loop Board, High bug,
2026-10-06): the household's next save still works.

THE BUG, as measured on main by the time-limits build (2026-10-05):
edit_preference("typical_week", None) raised `NOT NULL constraint failed`
from INSIDE its write — after the INSERT had taken SQLite's write lock and
before the bare conn.close() — so the connection leaked holding the lock and
the next writer failed after 5.01s with "database is locked". What keeps a
leaked connection alive in production is the traceback (FastAPI holds it for
the response; the chat loop's logger.exception formats it), so these tests
hold it too — see tests/test_write_closes_however_it_leaves.py for why that
is the honest reproduction rather than a contrivance.

The fix is db.write() on every writer in memory.py and preferences.py (the
functions chat's "remember that…" tools land in), plus a plain refusal for
a text answer that arrives as nothing at all.
"""
from __future__ import annotations

import gc
import sqlite3
import sys
import types

import pytest

from app import db, tools
from app.tools import memory as _memory
from app.tools import preferences as _preferences


def _next_write_is_not_locked_out():
    """True when another connection can take the write lock right now.

    busy_timeout 300ms, not sqlite's 5s default: the question is whether
    the lock is HELD, and the timeout only says how long to wait to find out.
    """
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


def _fail_holding_the_traceback(fn, *args, exc=Exception):
    held = None
    try:
        fn(*args)
    except exc:
        held = sys.exc_info()[2]
    assert held is not None, f"{fn.__name__}{args} was expected to fail"
    gc.collect()  # does not help on the bug: the reference is on the traceback
    return held


def test_a_refused_typical_week_says_so_and_the_next_save_works():
    """CATCH — the card's own repro. Red on main: IntegrityError, then locked."""
    held = None
    try:
        with pytest.raises(ValueError) as caught:
            try:
                tools.edit_preference("typical_week", None)
            except Exception:
                held = sys.exc_info()[2]
                raise
        assert "didn't save" in str(caught.value)
        assert _next_write_is_not_locked_out()
        # And the household's next real save lands.
        assert tools.edit_preference("typical_week", "Busy Tuesdays") == {"typical_week": "Busy Tuesdays"}
    finally:
        del held
        gc.collect()


def test_a_raise_after_the_preferences_write_still_frees_the_lock(monkeypatch):
    """CATCH. Anything raising between the write and the commit — here the
    snack-count keeper set_household_meal_preferences calls after its INSERT."""
    def _boom(conn, hid):
        raise RuntimeError("raised mid-write")

    monkeypatch.setattr(_preferences, "keep_snack_counts_consistent", _boom)
    held = _fail_holding_the_traceback(tools.edit_preference, "dinners_per_week", 3, exc=RuntimeError)
    try:
        assert _next_write_is_not_locked_out()
    finally:
        del held
        gc.collect()
    monkeypatch.undo()
    # Rolled back, not half-written.
    assert tools.get_household_memory().get("dinners_per_week") != 3


def test_a_raise_inside_delete_preference_frees_the_lock(monkeypatch):
    """CATCH. The forget path: UPDATE, then the same keeper, then the close."""
    tools.edit_preference("snacks_per_day", 2)

    def _boom(conn, hid):
        raise RuntimeError("raised mid-write")

    monkeypatch.setattr(_preferences, "keep_snack_counts_consistent", _boom)
    held = _fail_holding_the_traceback(tools.delete_preference, "snacks_per_week", exc=RuntimeError)
    try:
        assert _next_write_is_not_locked_out()
    finally:
        del held
        gc.collect()


def test_through_chat_the_tool_answers_that_it_did_not_save_and_the_app_still_saves(monkeypatch):
    """CATCH, end to end with only the model stubbed: the real dispatch loop
    runs edit_preference, the model is handed a plain "didn't save" error,
    and the household's next save works straight after."""
    from app import agent

    usage = types.SimpleNamespace(
        input_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, output_tokens=0,
    )
    seen = []

    class _Messages:
        def __init__(self):
            self._seq = [
                types.SimpleNamespace(
                    content=[types.SimpleNamespace(
                        type="tool_use", name="edit_preference",
                        input={"field": "typical_week", "value": None}, id="tu_1")],
                    stop_reason="tool_use", usage=usage,
                ),
                types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="text", text="That didn't save.")],
                    stop_reason="end_turn", usage=usage,
                ),
            ]

        def create(self, **kwargs):
            seen.append(kwargs.get("messages"))
            return self._seq.pop(0)

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=_Messages()))
    agent.run_agent_turn([], "remember that our week is…")

    results = [
        block for msg in seen[-1] if isinstance(msg.get("content"), list)
        for block in msg["content"] if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert results and results[-1]["is_error"] is True
    assert "didn't save" in results[-1]["content"]
    assert _next_write_is_not_locked_out()
    assert tools.add_fact("people", "Sam is home Fridays")["added"] is True
