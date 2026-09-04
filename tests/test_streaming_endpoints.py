"""
The two Server-Sent-Events endpoints that make week generation feel like
something is happening instead of ~37 seconds of silence:
/api/week/{week_start}/generate/stream (the plan-week screen) and
/api/chat/stream (chat, when the turn ends up calling
generate_weekly_plan). Both wrap the same blocking generation on a
background thread and relay progress through a queue -- what's under test
here is that plumbing: does the first event really arrive before a
(mocked) slow generation finishes, do day events carry what the model
produced, does a failure surface as an error event rather than a hang or
a raw 500.

No real Anthropic call happens in any of these -- generate_weekly_plan
itself is replaced with a fake that sleeps and calls whatever progress
callback is currently registered, the same shape the real one uses via
agent._WEEK_GEN_PROGRESS.
"""
from __future__ import annotations

import json
import time

import pytest

from app import agent, main as main_module


def _parse_sse(raw_chunks):
    """
    Turn raw SSE text back into (event, payload) pairs, the way a
    browser's SSE parser would. Accepts either pre-split lines (as
    httpx's response.iter_lines() gives) or whole "event: X\\ndata:
    Y\\n\\n" frames (as a raw generator yields) -- splitting every chunk
    on "\\n" first makes both shapes equivalent.
    """
    events = []
    pending_event = None
    for chunk in raw_chunks:
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                pending_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                events.append((pending_event, json.loads(line[len("data:"):].strip())))
    return events


@pytest.fixture
def slow_week_generation(monkeypatch):
    """
    Stands in for generate_weekly_plan: sleeps before AND after calling
    the progress callback for one day, so a test can tell "arrived while
    still running" apart from "arrived only once everything finished".
    """
    calls = []

    def fake(week_start, constraints_notes="", day_count=7, intake_id=None):
        calls.append(week_start)
        on_item = agent._WEEK_GEN_PROGRESS.get(None)
        time.sleep(0.25)
        if on_item:
            on_item({"date": week_start, "slot": "dinner", "meal_name": "Chili"})
        time.sleep(0.25)
        return {"weekly_plan_id": 1, "week_start_date": week_start, "meals": []}

    monkeypatch.setattr(main_module, "generate_weekly_plan", fake)
    return calls


def test_plan_week_stream_generator_yields_status_before_the_slow_generation_finishes(
    slow_week_generation,
):
    """
    Drives _stream_week_generation directly rather than through the HTTP
    test client: the TestClient's in-process transport does not reliably
    preserve chunk-by-chunk timing (it can coalesce a whole response
    before the test ever sees the first line), so it can't tell "streamed"
    apart from "buffered then sent all at once". Calling the generator and
    timing each `next()` proves what actually matters -- that the first
    event is produced before the (mocked, deliberately slow) generation
    call returns -- independent of how a real ASGI server or this test
    harness chooses to flush bytes onto the wire.
    """
    week = "2026-09-07"
    gen = main_module._stream_week_generation(
        week_start=week, constraints_notes="", intake_id=None,
    )
    started = time.perf_counter()
    first = next(gen)
    first_at = time.perf_counter() - started
    assert first_at < 0.05, f"the status event took {first_at:.2f}s to yield -- should be near-instant"
    assert "event: status" in first

    remaining = [first]
    for chunk in gen:
        remaining.append(chunk)
    total = time.perf_counter() - started
    assert total >= 0.45, "sanity check: the mocked generation really did take ~0.5s"

    events = _parse_sse(remaining)
    names = [e for e, _ in events]
    assert names[0] == "status"
    assert "day" in names
    assert names[-1] == "done"
    day_payload = next(p for e, p in events if e == "day")
    assert day_payload == {"date": week, "slot": "dinner", "meal_name": "Chili"}
    done_payload = next(p for e, p in events if e == "done")
    assert done_payload["week_start_date"] == week


def test_plan_week_stream_endpoint_returns_the_same_events_over_http(
    signed_in, slow_week_generation,
):
    """Endpoint-level correctness (status code, content-type, event order/content) --
    timing is covered by the direct generator test above instead."""
    week = "2026-09-07"
    with signed_in.stream("POST", f"/api/week/{week}/generate/stream", json={}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = list(response.iter_lines())

    events = _parse_sse(lines)
    names = [e for e, _ in events]
    assert names[0] == "status"
    assert "day" in names
    assert names[-1] == "done"


def test_plan_week_stream_surfaces_a_failure_as_an_error_event(monkeypatch, signed_in):
    def fake(week_start, constraints_notes="", day_count=7, intake_id=None):
        raise ValueError("no meals came back")

    monkeypatch.setattr(main_module, "generate_weekly_plan", fake)

    with signed_in.stream("POST", "/api/week/2026-09-07/generate/stream", json={}) as response:
        assert response.status_code == 200  # headers are already sent by the time this can fail
        events = _parse_sse(list(response.iter_lines()))

    names = [e for e, _ in events]
    assert names[-1] == "error"
    error_payload = next(p for e, p in events if e == "error")
    assert error_payload["status"] == 400
    assert "no meals" in error_payload["detail"]


def test_plan_week_stream_rejects_a_bad_date(signed_in):
    res = signed_in.post("/api/week/not-a-date/generate/stream", json={})
    assert res.status_code == 400


def _fake_run_agent_turn(history, message, proactive_check=False):
    """
    Stands in for run_agent_turn: sleeps, then calls whatever progress
    callback is currently registered, exactly like a real turn that
    reached generate_weekly_plan would via _WEEK_GEN_PROGRESS.
    """
    on_item = agent._WEEK_GEN_PROGRESS.get(None)
    time.sleep(0.25)
    if on_item:
        on_item({"date": "2026-09-07", "slot": "dinner", "meal_name": "Tacos"})
    return "I've put together your week!", history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": "I've put together your week!"},
    ]


def test_chat_stream_generator_yields_status_before_the_slow_turn_finishes(monkeypatch):
    """Same rationale as the plan-week generator test above: drive the
    generator directly so timing reflects the code, not the test transport."""
    monkeypatch.setattr(main_module, "run_agent_turn", _fake_run_agent_turn)
    monkeypatch.setattr(main_module, "summarize_chat_actions", lambda old, new: [])

    gen = main_module._stream_chat_turn(
        session_id="s1", message="plan my week", history=[], proactive_check=False,
    )
    started = time.perf_counter()
    first = next(gen)
    first_at = time.perf_counter() - started
    assert first_at < 0.05, f"the status event took {first_at:.2f}s to yield -- should be near-instant"
    assert "event: status" in first

    remaining = [first] + list(gen)
    total = time.perf_counter() - started
    assert total >= 0.2, "sanity check: the mocked turn really did take ~0.25s"

    events = _parse_sse(remaining)
    names = [e for e, _ in events]
    assert names[0] == "status"
    assert "day" in names
    assert names[-1] == "done"
    done_payload = next(p for e, p in events if e == "done")
    assert done_payload["reply"] == "I've put together your week!"
    assert done_payload["actions"] == []


def test_chat_stream_endpoint_returns_the_same_events_over_http(signed_in, monkeypatch):
    monkeypatch.setattr(main_module, "run_agent_turn", _fake_run_agent_turn)
    monkeypatch.setattr(main_module, "summarize_chat_actions", lambda old, new: [])

    with signed_in.stream("POST", "/api/chat/stream", json={"message": "plan my week"}) as response:
        assert response.status_code == 200
        lines = list(response.iter_lines())

    events = _parse_sse(lines)
    names = [e for e, _ in events]
    assert names[0] == "status"
    assert "day" in names
    assert names[-1] == "done"


def test_chat_stream_surfaces_assistant_unavailable_as_an_error_event(signed_in, monkeypatch):
    from app.agent import AssistantUnavailableError

    def fake_run_agent_turn(history, message, proactive_check=False):
        raise AssistantUnavailableError("Claude's servers are having a moment.")

    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)

    with signed_in.stream("POST", "/api/chat/stream", json={"message": "hi"}) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    names = [e for e, _ in events]
    assert names[-1] == "error"
    payload = next(p for e, p in events if e == "error")
    assert payload["status"] == 503


def _fake_run_agent_turn_no_progress(history, message, proactive_check=False):
    """A quick, non-week-generation turn -- the shape of a normal
    action-producing reply like "add milk"."""
    return "Added milk to your grocery list.", history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": "Added milk to your grocery list."},
    ]


def test_chat_stream_generator_delivers_a_reply_that_carries_an_action_card(monkeypatch):
    """
    Regression test for the "Object of type ChatAction is not JSON
    serializable" crash: _finish_chat_turn's "done" payload includes
    `actions`, a list of ChatAction pydantic models (see
    summarize_chat_actions), not plain dicts. _sse_event used to hand that
    straight to json.dumps, which has no idea how to encode a pydantic
    model and blew up mid-stream -- after the SSE headers (and the
    "status" event) had already gone out, so the browser lost the reply
    AND the action card with no fallback.

    Every other test in this file monkeypatches summarize_chat_actions to
    return `[]`, which is exactly why this gap shipped -- none of them
    ever pushed a real ChatAction through _sse_event's json.dumps. This
    one returns actual ChatAction instances instead, driving the generator
    directly so a serialization crash here surfaces as this test raising,
    not as a silently-swallowed background-thread exception.
    """
    action = main_module.ChatAction(
        kicker="Grocery list", change="Added milk", tab="grocery",
    )
    monkeypatch.setattr(main_module, "run_agent_turn", _fake_run_agent_turn_no_progress)
    monkeypatch.setattr(main_module, "summarize_chat_actions", lambda old, new: [action])

    gen = main_module._stream_chat_turn(
        session_id="s1", message="add milk", history=[], proactive_check=False,
    )
    chunks = list(gen)  # would raise TypeError on the old json.dumps(data) before the fix

    events = _parse_sse(chunks)
    names = [e for e, _ in events]
    assert names[0] == "status"
    assert names[-1] == "done"
    done_payload = next(p for e, p in events if e == "done")
    assert done_payload["reply"] == "Added milk to your grocery list."
    assert done_payload["actions"] == [
        {"kicker": "Grocery list", "change": "Added milk", "tab": "grocery", "href": None},
    ]


def test_chat_stream_endpoint_completes_over_http_when_the_reply_carries_an_action_card(
    signed_in, monkeypatch,
):
    """Same scenario as above, but end to end through the HTTP endpoint --
    confirms the SSE response actually completes (status 200, a real
    "done" frame) instead of the connection aborting mid-stream the way it
    did before the fix, which a direct-generator test alone wouldn't catch
    if some other layer (e.g. StreamingResponse itself) swallowed the
    error differently over HTTP."""
    action = main_module.ChatAction(
        kicker="Grocery list", change="Added milk and eggs", tab="grocery",
    )
    monkeypatch.setattr(main_module, "run_agent_turn", _fake_run_agent_turn_no_progress)
    monkeypatch.setattr(main_module, "summarize_chat_actions", lambda old, new: [action])

    with signed_in.stream("POST", "/api/chat/stream", json={"message": "add milk and eggs"}) as response:
        assert response.status_code == 200
        events = _parse_sse(list(response.iter_lines()))

    names = [e for e, _ in events]
    assert names[-1] == "done"
    done_payload = next(p for e, p in events if e == "done")
    assert done_payload["actions"][0]["change"] == "Added milk and eggs"
    assert done_payload["actions"][0]["tab"] == "grocery"
