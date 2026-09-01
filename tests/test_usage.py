"""
Usage recording: chat turns, last-active, and the summary built on them.

The point of these tests is that the recording cannot quietly stop. A chat
turn that isn't recorded leaves no trace anywhere — chat history is
memory-only — so a regression here doesn't show up as a failure, it shows
up months later as a beta with no usage data and no way to reconstruct it.
"""
from app import main, tools
from app.db import get_conn
from app.tools import usage as usage_module


def _turn_rows(household_id: int = 1):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM chat_turns WHERE household_id = ? ORDER BY id", (household_id,)
    ).fetchall()
    conn.close()
    return rows


def _fake_turn_returning(reply="Done."):
    def fake_turn(conversation, user_message, *, proactive_check=False):
        return reply, conversation + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply},
        ]
    return fake_turn


def test_a_chat_turn_is_recorded(signed_in, monkeypatch):
    monkeypatch.setattr(main, "run_agent_turn", _fake_turn_returning())

    assert _turn_rows() == []
    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "hello"})
    assert res.status_code == 200

    rows = _turn_rows()
    assert len(rows) == 1, "one chat turn should record exactly one row"
    assert rows[0]["household_id"] == 1


def test_the_recorded_turn_carries_what_it_cost(signed_in, monkeypatch):
    """
    The numbers are the whole reason the row exists — a row of zeros would
    pass a "did it record" test while being useless for costing a job.
    """
    def fake_turn(conversation, user_message, *, proactive_check=False):
        main.agent.LAST_TURN_USAGE.set({
            "rounds": 3, "input_tokens": 11, "cache_read_tokens": 2200,
            "cache_write_tokens": 40, "output_tokens": 90, "seconds": 4.5,
        })
        return "Done.", conversation + [{"role": "user", "content": user_message}]

    monkeypatch.setattr(main, "run_agent_turn", fake_turn)
    signed_in.post("/api/chat", json={"session_id": "default", "message": "hi"})

    row = _turn_rows()[0]
    assert row["rounds"] == 3
    assert row["input_tokens"] == 11
    assert row["cache_read_tokens"] == 2200
    assert row["cache_write_tokens"] == 40
    assert row["output_tokens"] == 90
    assert round(row["seconds"], 1) == 4.5


def test_no_message_content_is_stored(signed_in, monkeypatch):
    """
    This is a privacy-sensitive household app and the ticket that asked for
    this recording drew the line explicitly: counts, never transcripts.
    """
    monkeypatch.setattr(main, "run_agent_turn", _fake_turn_returning("Sure — added."))
    signed_in.post(
        "/api/chat",
        json={"session_id": "default", "message": "remind me about the divorce paperwork"},
    )

    row = _turn_rows()[0]
    stored = " ".join(str(v) for v in tuple(row))
    assert "divorce" not in stored.lower()
    assert "added" not in stored.lower()


def test_a_failure_to_record_never_breaks_the_chat_turn(signed_in, monkeypatch):
    """
    Bookkeeping attached to a reply that already succeeded must not be able
    to turn that reply into an error for the person waiting on it.
    """
    monkeypatch.setattr(main, "run_agent_turn", _fake_turn_returning())

    def explode(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(usage_module, "get_conn", explode)

    res = signed_in.post("/api/chat", json={"session_id": "default", "message": "hello"})
    assert res.status_code == 200, "a broken stats write must not fail the chat turn"
    assert res.json()["reply"] == "Done."


def test_using_the_app_marks_the_household_active(signed_in):
    usage_module._last_touched.clear()
    conn = get_conn()
    conn.execute("UPDATE households SET last_active_at = NULL WHERE id = 1")
    conn.commit()
    conn.close()

    signed_in.get("/api/grocery-list")

    conn = get_conn()
    stamp = conn.execute("SELECT last_active_at FROM households WHERE id = 1").fetchone()[0]
    conn.close()
    assert stamp is not None, "an authenticated request should mark the household active"


def test_marking_active_is_throttled(signed_in):
    """
    Every page load could write this column; at day-level granularity that
    would be a lot of writes for nothing. One write per household per
    interval is the deal — see touch_household_active.
    """
    usage_module._last_touched.clear()
    signed_in.get("/api/grocery-list")

    conn = get_conn()
    conn.execute("UPDATE households SET last_active_at = 'sentinel' WHERE id = 1")
    conn.commit()
    conn.close()

    signed_in.get("/api/grocery-list")

    conn = get_conn()
    stamp = conn.execute("SELECT last_active_at FROM households WHERE id = 1").fetchone()[0]
    conn.close()
    assert stamp == "sentinel", "a second request inside the interval should not write again"


def test_usage_summary_counts_what_the_household_did(signed_in, monkeypatch):
    monkeypatch.setattr(main, "run_agent_turn", _fake_turn_returning())
    signed_in.post("/api/chat", json={"session_id": "default", "message": "hello"})
    tools.create_weekly_plan("2026-08-31")

    summary = tools.get_usage_summary(days=7)
    assert summary["chat_turns"] == 1
    assert summary["plans_generated"] == 1
    assert summary["meals_cooked"] == 0
    assert summary["looks_inactive"] is False


def test_a_silent_week_is_named_as_such():
    """
    The signal most easily skimmed past in a wall of counts is the one that
    matters most in a beta: nothing happened at all.
    """
    summary = tools.get_usage_summary(days=7)
    assert summary["chat_turns"] == 0
    assert summary["looks_inactive"] is True


def test_usage_is_scoped_to_one_household(signed_in, monkeypatch):
    monkeypatch.setattr(main, "run_agent_turn", _fake_turn_returning())
    signed_in.post("/api/chat", json={"session_id": "default", "message": "hello"})

    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (2, 'The Testers')")
    conn.commit()
    conn.close()

    with tools.use_household(2):
        assert tools.get_usage_summary()["chat_turns"] == 0, (
            "one household's chat activity must not show up in another's numbers"
        )
