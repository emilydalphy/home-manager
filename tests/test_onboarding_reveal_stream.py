"""
The onboarding reveal's plan generation streams day by day.

Loop Board "Redesign the post-onboarding 'first sample week' screen": since
that ticket was written, the Meals draft screen moved to a progressive,
day-by-day SSE reveal (/api/week/{week_start}/generate/stream) while the
onboarding reveal was left calling generate_weekly_plan directly and
returning one blocking JSON response after ~30 seconds of silence. This
adds a streaming twin, /api/onboarding/generate-first-plan/stream, built on
the exact same _stream_week_generation machinery — these tests confirm it
actually streams a "day" event before the final payload and still files
the plan under this week's Monday (test_onboarding_week_key.py's
invariant), same as the plain route it sits beside.

Stub pattern ported from test_streaming_endpoints.py: generate_weekly_plan
itself is replaced with a fake that calls whatever progress callback is
currently registered via agent._WEEK_GEN_PROGRESS, the same shape the real
one (through _stream_forced_tool_call) uses. No real Anthropic call.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, main as main_module, tools


def _this_monday() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    pending_event = None
    for line in body.split("\n"):
        if line.startswith("event:"):
            pending_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((pending_event, json.loads(line[len("data:"):].strip())))
    return events


@pytest.fixture
def fake_week_generation(monkeypatch):
    """Stands in for generate_weekly_plan: emits one day event via
    whatever progress callback the streaming endpoint registered, then
    returns the plan the plain /generate-first-plan endpoint would."""

    def fake(week_start, constraints_notes="", day_count=7, intake_id=None):
        on_item = agent._WEEK_GEN_PROGRESS.get(None)
        if on_item:
            on_item({"date": week_start, "slot": "dinner", "meal_name": "Chili"})
        plan_id = tools.get_plan_id_for_week(week_start)
        if plan_id is None:
            plan_id = 999999
        return {
            "weekly_plan_id": plan_id,
            "week_start_date": week_start,
            "meals": [{"date": week_start, "slot": "dinner", "meal": "Chili"}],
        }

    monkeypatch.setattr(main_module, "generate_weekly_plan", fake)


def test_the_reveal_stream_endpoint_emits_day_and_done_events(signed_in, fake_week_generation):
    res = signed_in.post("/api/onboarding/generate-first-plan/stream")
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]

    events = _parse_sse(res.text)
    event_names = [name for name, _ in events]
    assert "status" in event_names, "should announce it's starting before the model finishes"
    assert "day" in event_names, "should stream at least one per-day item as the plan is decided"
    assert event_names[-1] == "done", "should end with the full saved plan, like the plain endpoint returns"

    day_payload = next(p for n, p in events if n == "day")
    assert day_payload["meal_name"] == "Chili"

    done_payload = events[-1][1]
    assert done_payload["week_start_date"] == _this_monday()
    assert any(m["meal"] == "Chili" for m in done_payload["meals"])


def test_the_reveal_stream_endpoint_files_under_this_weeks_monday_like_the_plain_one(signed_in, fake_week_generation):
    """Same week-key invariant test_onboarding_week_key.py established for
    the blocking route — the streaming twin computes the same Monday key
    before calling generate_weekly_plan, so it must not regress it."""
    res = signed_in.post("/api/onboarding/generate-first-plan/stream")
    assert res.status_code == 200
    events = _parse_sse(res.text)
    done_payload = next(p for n, p in events if n == "done")

    assert done_payload["week_start_date"] == _this_monday()
    assert datetime.date.fromisoformat(done_payload["week_start_date"]).weekday() == 0
