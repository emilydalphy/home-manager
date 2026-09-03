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


# ---------- what the tokens cost ----------


def test_the_priced_model_is_the_model_the_app_actually_runs():
    """
    The one way this pricing goes silently wrong: someone changes the model
    in agent.py and the rate table keeps billing the old one. Nothing about
    that failure is visible — the report still prints a confident dollar
    figure, just the wrong one — so it is pinned here rather than trusted
    to be noticed.
    """
    from app import agent

    assert usage_module._PRICED_MODEL == agent.MODEL, (
        "agent.MODEL changed without updating _PRICED_MODEL — the cost "
        "report would price the new model at the old model's rates"
    )
    assert usage_module._PRICED_MODEL in usage_module._RATES_PER_MTOK, (
        "no published rate for the model the app runs on"
    )


def test_price_tokens_bills_each_kind_at_its_own_rate():
    """
    Cache reads are a tenth of input and cache writes are a quarter more
    than it. Treating them all as input — the easy mistake, since they are
    all "input tokens" — would overstate a cached app's bill several-fold.
    """
    costs = usage_module.price_tokens({
        "input": 1_000_000,
        "cache_read": 1_000_000,
        "cache_write": 1_000_000,
        "output": 1_000_000,
    }, model="claude-sonnet-5")

    assert costs["input"] == 2.00
    assert costs["cache_read"] == 0.20
    assert costs["cache_write"] == 2.50
    assert costs["output"] == 10.00
    assert costs["total"] == 14.70
    assert costs["model"] == "claude-sonnet-5"


def test_price_tokens_treats_missing_counts_as_zero():
    """
    A turn that never touched the cache records nothing for it. Pricing
    must read that as no cost, not raise, or one such turn takes the whole
    morning report down with it.
    """
    assert usage_module.price_tokens({"output": 1_000_000})["total"] == 10.00
    assert usage_module.price_tokens({})["total"] == 0.0


def test_a_real_week_of_chat_is_priced_in_the_summary(signed_in, monkeypatch):
    """
    The end of the chain: tokens recorded by a real request come back out
    of the summary as money, which is the number this ticket exists for.
    """
    monkeypatch.setattr(main, "run_agent_turn", _fake_turn_returning())
    signed_in.post(
        "/api/chat",
        json={"session_id": "default", "message": "hello"},
    )

    conn = get_conn()
    conn.execute(
        "UPDATE chat_turns SET input_tokens = 1000, cache_read_tokens = 30000, "
        "cache_write_tokens = 2000, output_tokens = 500 WHERE household_id = 1"
    )
    conn.commit()
    conn.close()

    summary = tools.get_usage_summary(days=7)
    # 1000 in + 30000 cached reads + 2000 cache writes + 500 out, at
    # Sonnet 5 list prices. The cache *write* is deliberately non-zero:
    # it is the priciest input class, and a summary that dropped it would
    # still look plausible.
    assert summary["cost"]["total"] == round(
        1000 * 2.0 / 1e6 + 30000 * 0.2 / 1e6 + 2000 * 2.5 / 1e6 + 500 * 10.0 / 1e6, 6
    )
    assert summary["cost"]["cache_write"] == round(2000 * 2.5 / 1e6, 6)
    assert summary["cost"]["total"] > 0


def test_pricing_an_unpriced_model_says_so():
    """
    The bare KeyError this replaced named the dictionary, not the problem.
    The realistic way to arrive here is swapping the model and forgetting
    the rate row, so the message says to add one.
    """
    import pytest

    with pytest.raises(ValueError, match="No published token rate"):
        usage_module.price_tokens({"output": 100}, model="claude-not-a-model")
