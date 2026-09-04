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


def test_the_chat_fallback_model_has_a_published_rate_too():
    """
    Same failure mode as the primary-model pin above, one hop removed: a
    529-exhaustion fallback call records agent.CHAT_FALLBACK_MODEL into
    api_calls.model, and _cost_breakdown prices every row by its own
    model (see usage.py's _cost_breakdown) -- with no rate row for it,
    that pricing call raises instead of quietly mis-billing, which would
    take the whole cost report down with it the first time a fallback
    actually fires.
    """
    from app import agent

    assert agent.CHAT_FALLBACK_MODEL in usage_module._RATES_PER_MTOK, (
        "agent.CHAT_FALLBACK_MODEL changed (or its env override moved) "
        "without adding a matching row to _RATES_PER_MTOK"
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


# ---------- every call site, not just chat (api_calls) ----------


def _call_rows(household_id: int = 1):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM api_calls WHERE household_id = ? ORDER BY id", (household_id,)
    ).fetchall()
    conn.close()
    return rows


def _age_last_call(days_ago: int):
    """Push the most recently inserted api_calls row's created_at back in
    time, so month-boundary tests don't have to wait for an actual month
    to pass."""
    conn = get_conn()
    conn.execute(
        "UPDATE api_calls SET created_at = datetime('now', ?) "
        "WHERE id = (SELECT MAX(id) FROM api_calls)",
        (f"-{days_ago} days",),
    )
    conn.commit()
    conn.close()


def test_record_api_call_writes_a_row():
    usage_module.record_api_call(
        "generate_weekly_plan_llm", "claude-sonnet-5",
        {"input_tokens": 17975, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "output_tokens": 4837},
        seconds=36.49,
    )
    rows = _call_rows()
    assert len(rows) == 1
    assert rows[0]["call_site"] == "generate_weekly_plan_llm"
    assert rows[0]["model"] == "claude-sonnet-5"
    assert rows[0]["input_tokens"] == 17975
    assert rows[0]["output_tokens"] == 4837
    assert round(rows[0]["seconds"], 2) == 36.49


def test_recording_an_api_call_never_raises(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(usage_module, "get_conn", explode)
    usage_module.record_api_call("run_agent_turn", "claude-sonnet-5", {"output_tokens": 1})
    # No exception means the whole point of the test passed.


def test_month_to_date_cost_breaks_down_by_call_site():
    usage_module.record_api_call(
        "generate_weekly_plan_llm", "claude-sonnet-5",
        {"input_tokens": 1_000_000, "output_tokens": 0}, seconds=36.0,
    )
    usage_module.record_api_call(
        "run_agent_turn", "claude-sonnet-5",
        {"cache_read_tokens": 1_000_000, "output_tokens": 0}, seconds=2.0,
    )

    breakdown = usage_module.get_month_to_date_cost()
    assert breakdown["by_call_site"]["generate_weekly_plan_llm"]["cost"]["total"] == 2.00
    assert breakdown["by_call_site"]["run_agent_turn"]["cost"]["total"] == 0.20
    assert breakdown["total_cost"]["total"] == round(2.00 + 0.20, 6)
    assert breakdown["by_call_site"]["generate_weekly_plan_llm"]["calls"] == 1


def test_month_to_date_cost_excludes_last_calendar_month():
    """
    Deliberately the calendar month, not a rolling 30-day window -- a
    target of $1/household/month means "since the 1st", not "in the last
    30 days", and the two disagree for anyone who reads this on the 2nd.
    """
    usage_module.record_api_call(
        "run_agent_turn", "claude-sonnet-5", {"output_tokens": 1_000_000}, seconds=1.0,
    )
    _age_last_call(days_ago=40)  # safely into last month regardless of today's date

    breakdown = usage_module.get_month_to_date_cost()
    assert breakdown["total_cost"]["total"] == 0.0
    assert breakdown["by_call_site"] == {}


def test_month_to_date_cost_is_scoped_to_one_household():
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (2, 'The Testers')")
    conn.commit()
    conn.close()

    usage_module.record_api_call("run_agent_turn", "claude-sonnet-5", {"output_tokens": 1000})

    with tools.use_household(2):
        breakdown = usage_module.get_month_to_date_cost()
        assert breakdown["total_cost"]["total"] == 0.0, (
            "one household's API spend must not show up in another's total"
        )


def test_a_call_site_on_a_different_model_is_priced_at_its_own_rate():
    """
    price_tokens raises for a model with no rate row, so if a call site
    ever does run on something other than claude-sonnet-5, the report
    fails loudly rather than silently billing it at the wrong price. Add
    a second rate here to prove the per-row model is actually threaded
    through, not just assumed.
    """
    usage_module._RATES_PER_MTOK["claude-cheap-test-model"] = {
        "input": 1.00, "cache_read": 0.10, "cache_write": 1.25, "output": 5.00,
    }
    try:
        usage_module.record_api_call(
            "generate_recipe_detail_llm", "claude-cheap-test-model",
            {"input_tokens": 1_000_000, "output_tokens": 0},
        )
        breakdown = usage_module.get_month_to_date_cost()
        assert breakdown["by_call_site"]["generate_recipe_detail_llm"]["cost"]["total"] == 1.00
    finally:
        del usage_module._RATES_PER_MTOK["claude-cheap-test-model"]


def test_plan_generation_latency_is_p50_and_max_not_an_average():
    """
    Emily asked specifically how long generating the week takes. An
    average hides a single bad outlier; p50 and max both surface it.
    """
    for seconds in (30.0, 32.0, 40.0):
        usage_module.record_api_call(
            "generate_weekly_plan_llm", "claude-sonnet-5",
            {"input_tokens": 18000, "output_tokens": 4800}, seconds=seconds,
        )

    stats = usage_module.get_usage_summary()["plan_generation"]
    assert stats["count"] == 3
    assert stats["p50_seconds"] == 32.0
    assert stats["max_seconds"] == 40.0
    assert stats["total_cost"]["total"] > 0


def test_plan_generation_stats_are_empty_with_no_runs():
    stats = usage_module.get_usage_summary()["plan_generation"]
    assert stats == {"count": 0}


def test_every_llm_call_site_passes_the_shared_model_constant():
    """
    Every one of the app's Anthropic call sites must pass model=MODEL,
    never a hardcoded string -- api_calls prices each row at the model it
    recorded, which only stays correct as long as every call site keeps
    passing the one shared constant rather than its own copy. A call site
    that hardcoded its own model string would keep working today (the API
    doesn't care) and would still price correctly today, right up until
    agent.MODEL changes and that one site doesn't -- exactly the failure
    this test exists to catch before it ships, not after.
    """
    import inspect

    from app import agent

    source = inspect.getsource(agent)
    labels = [
        "generate_weekly_plan_llm", "generate_component_plan_llm",
        "generate_prep_schedule_llm", "generate_recipe_detail_llm",
        "_scan_image_for_items", "generate_chore_recommendations", "run_agent_turn",
    ]
    # generate_weekly_plan_llm and generate_component_plan_llm route through
    # _stream_forced_tool_call instead of _create_with_retry directly (added
    # for the "stream week generation" work) -- it's the streaming
    # equivalent, with the same instrumentation (see its own
    # _record_api_call call), so both helpers' call sites count toward the
    # same "every call site is covered" invariant this test exists to check.
    call_sites = source.count("_create_with_retry(") - 1 + source.count("_stream_forced_tool_call(") - 1
    assert call_sites == len(labels), (
        "the number of _create_with_retry/_stream_forced_tool_call call sites in "
        "agent.py changed (each -1 is that helper's own definition) -- update "
        "this test's label list, and the api_calls schema comment, to match"
    )
    for label in labels:
        idx = source.index(f'label="{label}"')
        window = source[idx: idx + 150]
        assert "model=MODEL" in window, (
            f"{label} does not pass model=MODEL -- it will be priced at "
            f"whatever _PRICED_MODEL says even if it actually runs on "
            f"something else"
        )
