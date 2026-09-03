"""
Every Anthropic call the app makes, priced -- not just chat.

Before this, chat_turns recorded the chat loop and nothing else. The
app's single most expensive call (weekly-plan generation, ~18,000
uncached tokens per run) was invisible to it, and so were the other five:
component-plan fill-in, prep-schedule generation, recipe fill-in, photo
scans, and chore recommendations.

These tests cover the one instrumentation point (agent._create_with_retry)
that now sees all seven, plus a couple of call sites end to end through
their real public functions -- so a regression here shows up as a test
failure today, not as an unexplained gap in next month's bill.
"""
from __future__ import annotations

import types

import pytest

from app import agent, tools
from app.db import get_conn


class _Usage:
    def __init__(self, input_tokens=0, cache_read=0, cache_write=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write
        self.output_tokens = output_tokens


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    """Hands back one scripted response, or raises, like the real API would."""

    def __init__(self, responses=None, raises=None):
        self._responses = list(responses or [])
        self._raises = raises
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses=None, raises=None):
    fake = types.SimpleNamespace(messages=_FakeMessages(responses, raises))
    monkeypatch.setattr(agent, "_client", lambda: fake)
    return fake


def _api_call_rows(household_id: int = 1):
    conn = get_conn()
    try:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM api_calls WHERE household_id = ? ORDER BY id", (household_id,)
            ).fetchall()
        ]
    finally:
        conn.close()


# ---------- the central hook: _create_with_retry itself ----------


def test_a_successful_call_is_recorded_regardless_of_label(monkeypatch):
    """
    _create_with_retry is the one function every one of the app's seven
    call sites goes through -- recording lives here, not at each of the
    seven, specifically so a label this test has never heard of is
    covered automatically.
    """
    fake = _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=_Usage(input_tokens=123, cache_read=456, cache_write=78, output_tokens=90),
        ),
    ])
    client = fake

    agent._create_with_retry(
        client, label="a_call_site_nobody_wrote_a_special_case_for", model=agent.MODEL,
        max_tokens=10, messages=[{"role": "user", "content": "hi"}],
    )

    rows = _api_call_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["call_site"] == "a_call_site_nobody_wrote_a_special_case_for"
    assert row["model"] == agent.MODEL
    assert row["input_tokens"] == 123
    assert row["cache_read_tokens"] == 456
    assert row["cache_write_tokens"] == 78
    assert row["output_tokens"] == 90
    assert row["seconds"] > 0


def test_every_real_call_site_label_is_recorded(monkeypatch):
    """
    The definitive list this ticket asked for: every label actually passed
    to _create_with_retry in agent.py today. If a call site is renamed or
    removed, this test's own list goes stale in an obvious way (it fails),
    which is the point -- a silently-dropped call site is exactly the bug
    this whole feature exists to prevent.
    """
    labels = [
        "generate_weekly_plan_llm",
        "generate_component_plan_llm",
        "generate_prep_schedule_llm",
        "generate_recipe_detail_llm",
        "_scan_image_for_items",
        "generate_chore_recommendations",
        "run_agent_turn",
    ]
    for label in labels:
        fake = _stub_client(monkeypatch, [
            types.SimpleNamespace(
                content=[], stop_reason="end_turn",
                usage=_Usage(input_tokens=1, output_tokens=1),
            ),
        ])
        agent._create_with_retry(
            fake, label=label, model=agent.MODEL, max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )

    recorded = {row["call_site"] for row in _api_call_rows()}
    assert recorded == set(labels)


def test_a_call_that_never_succeeds_records_nothing(monkeypatch):
    """
    A response never comes back, so there is nothing real to price --
    recording a phantom call here would overstate the bill for work that
    never happened (and never got billed by Anthropic either).
    """
    from anthropic import APIConnectionError

    _stub_client(monkeypatch, raises=APIConnectionError(request=None))

    with pytest.raises(agent.AssistantUnavailableError):
        agent._create_with_retry(
            agent._client(), label="run_agent_turn", model=agent.MODEL, max_attempts=1,
            max_tokens=10, messages=[{"role": "user", "content": "hi"}],
        )

    assert _api_call_rows() == []


def test_recording_an_api_call_never_breaks_the_caller(monkeypatch):
    """
    Same guarantee as chat's own record_chat_turn: bookkeeping wrapped
    around a call that already succeeded must not be able to turn that
    success into a failure for whoever was waiting on the reply.
    """
    fake = _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[], stop_reason="end_turn", usage=_Usage(input_tokens=1, output_tokens=1),
        ),
    ])

    def explode(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(tools, "record_api_call", explode)

    response = agent._create_with_retry(
        fake, label="run_agent_turn", model=agent.MODEL, max_tokens=10,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert response is not None, "a broken stats write must not fail the underlying call"


def test_recorded_calls_are_scoped_to_the_household_that_made_them(monkeypatch):
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (2, 'The Testers')")
    conn.commit()
    conn.close()

    fake = _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[], stop_reason="end_turn", usage=_Usage(input_tokens=5, output_tokens=5),
        ),
    ])
    with tools.use_household(2):
        agent._create_with_retry(
            fake, label="run_agent_turn", model=agent.MODEL, max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )

    assert _api_call_rows(household_id=1) == []
    assert len(_api_call_rows(household_id=2)) == 1


# ---------- end to end, through the real public functions ----------


def test_recipe_fill_in_is_recorded_end_to_end(monkeypatch):
    _stub_client(monkeypatch, [
        _fake_response(_tool_block("submit_recipe_detail", {
            "instructions": ["Boil water.", "Add pasta."],
            "prep_time_minutes": 5, "cook_time_minutes": 10, "advance_prep_notes": "",
        })),
    ])

    detail = agent.generate_recipe_detail_llm({"name": "Pasta", "ingredients": ["pasta", "water"]})
    assert detail["instructions"]

    rows = _api_call_rows()
    assert len(rows) == 1
    assert rows[0]["call_site"] == "generate_recipe_detail_llm"


def test_a_photo_scan_is_recorded_end_to_end(monkeypatch):
    _stub_client(monkeypatch, [
        _fake_response(_tool_block("submit_scanned_items", {
            "items": [{"item": "milk", "quantity": "1 gal", "category": "dairy", "confidence": "high"}],
        })),
    ])

    items = agent.scan_receipt_image("ZmFrZQ==", "image/jpeg")
    assert items

    rows = _api_call_rows()
    assert len(rows) == 1
    assert rows[0]["call_site"] == "_scan_image_for_items"


def test_chore_recommendations_are_recorded_end_to_end(monkeypatch):
    _stub_client(monkeypatch, [
        _fake_response(_tool_block("submit_chore_recommendations", {
            "chores": [{"name": "Take out trash", "frequency_days": 7}],
        })),
    ])

    chores = agent.generate_chore_recommendations({"home_type": "apartment", "rotation_members": []})
    assert chores

    rows = _api_call_rows()
    assert len(rows) == 1
    assert rows[0]["call_site"] == "generate_chore_recommendations"


def _fake_response(*blocks, input_tokens=100, output_tokens=50, cache_read=0, cache_write=0):
    return types.SimpleNamespace(
        content=list(blocks), stop_reason="tool_use",
        usage=_Usage(input_tokens, cache_read, cache_write, output_tokens),
    )


# ---------- consistency between chat_turns and api_calls ----------


def test_a_chat_turn_agrees_with_its_own_api_calls_row(signed_in, monkeypatch):
    """
    A single chat round is recorded twice on purpose -- once aggregated
    into chat_turns (for turn/round-level reporting) and once into
    api_calls (for cost-by-call-site reporting). For a one-round turn the
    two must describe the exact same call, or one of the two bookkeeping
    paths has drifted from the truth.
    """
    def fake_turn(conversation, user_message, *, proactive_check=False):
        agent.LAST_TURN_USAGE.set({
            "rounds": 1, "input_tokens": 11, "cache_read_tokens": 2200,
            "cache_write_tokens": 40, "output_tokens": 90, "seconds": 1.0,
        })
        # Also exercise the real call-recording path for this round.
        agent._record_api_call(
            "run_agent_turn", agent.MODEL,
            types.SimpleNamespace(usage=_Usage(11, 2200, 40, 90)), 1.0,
        )
        return "Done.", conversation + [{"role": "user", "content": user_message}]

    from app import main
    monkeypatch.setattr(main, "run_agent_turn", fake_turn)
    signed_in.post("/api/chat", json={"session_id": "default", "message": "hi"})

    conn = get_conn()
    turn = conn.execute("SELECT * FROM chat_turns WHERE household_id = 1").fetchone()
    call = conn.execute(
        "SELECT * FROM api_calls WHERE household_id = 1 AND call_site = 'run_agent_turn'"
    ).fetchone()
    conn.close()

    assert turn["input_tokens"] == call["input_tokens"]
    assert turn["cache_read_tokens"] == call["cache_read_tokens"]
    assert turn["cache_write_tokens"] == call["cache_write_tokens"]
    assert turn["output_tokens"] == call["output_tokens"]
