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
the plan under the same key the plain route it sits beside would use.

CORRECTED 2026-09-13: this paragraph used to say "under this week's
Monday", and two of the tests below asserted exactly that — which is the
belief that made main red every Sunday, because on a Sunday the route
folds the one remaining day forward into next week. The key is whatever
main._first_plan_window computes; _first_plan_monday() below is the test's
copy of that rule, not the calendar.

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


# A Sunday — the one weekday the fold-forward below actually fires, so the
# frozen test at the foot of this file sees the behaviour the live-clock
# tests only see one day in seven.
FROZEN_SUNDAY = datetime.date(2026, 9, 13)
FROZEN_SUNDAY_KEY = "2026-09-14"


def _first_plan_monday() -> str:
    """
    The Monday onboarding files its first plan under, by the route's own
    floor rule (main._first_plan_window): this week's Monday, except on a
    SUNDAY, when the one day left in the period is folded forward into a
    full week starting tomorrow — a one-day plan being a lot of machinery
    for a single dinner.

    Same helper, same reason, as test_onboarding_week_key.py's: that file's
    three live-clock tests were fixed for this on 2026-09-06 ("five tests
    stop failing every Sunday") and these two, which assert the identical
    invariant about the STREAMING twin, were missed — so main went red
    every Sunday for behaviour that is deliberate. Deliberately a second
    copy rather than an import: a test helper that reaches into another
    test module couples two files whose only real relationship is that
    they check the same route pair.

    Only true for a household on the DEFAULT planning anchor, which is
    what conftest gives every test here (it wipes household_rhythm, so
    suggest_planning_period answers 'sunday' — the Monday week). A
    household that answered a different plan-ready day gets a period that
    is not a Monday week at all, and neither this helper nor the weekday()
    == 0 assertions below would hold for it. Nothing in this file sets
    one; a test that does must compute its key from the period, not here.
    """
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    if today.weekday() == 6:
        monday += datetime.timedelta(days=7)
    return monday.isoformat()


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
    returns the plan the plain /generate-first-plan endpoint would.

    Yields the recorded call, so a test can assert what the ROUTE asked
    for and not only what came back. The filing key alone does not pin the
    fold-forward: a mutant that keys next Monday and still generates one
    day starting today — the degenerate part-week the rule exists to
    prevent — passes a key-only assertion. The plain route is covered for
    that by test_part_week_onboarding.py; the streaming twin is the one
    that historically drifted (CLAUDE.md 2026-09-08: it "had none of the
    part-week or fold-forward logic"), so it is pinned here too."""
    called = {}

    # **kwargs so this stub survives generate_weekly_plan growing a
    # parameter (period_start arrived with planning periods). A stub that
    # pins the exact signature turns every future argument into a
    # TypeError inside a background thread, which surfaces as an unrelated
    # timing assertion rather than as "the signature changed".
    def fake(week_start, constraints_notes="", day_count=7, intake_id=None, **kwargs):
        called.update(week_start=week_start, day_count=day_count, **kwargs)
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
    return called


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
    assert done_payload["week_start_date"] == _first_plan_monday()
    assert any(m["meal"] == "Chili" for m in done_payload["meals"])


def test_the_reveal_stream_endpoint_files_under_this_weeks_monday_like_the_plain_one(signed_in, fake_week_generation):
    """Same week-key invariant test_onboarding_week_key.py established for
    the blocking route — the streaming twin computes the same Monday key
    before calling generate_weekly_plan, so it must not regress it."""
    res = signed_in.post("/api/onboarding/generate-first-plan/stream")
    assert res.status_code == 200
    events = _parse_sse(res.text)
    done_payload = next(p for n, p in events if n == "done")

    assert done_payload["week_start_date"] == _first_plan_monday()
    assert datetime.date.fromisoformat(done_payload["week_start_date"]).weekday() == 0


@pytest.mark.today(FROZEN_SUNDAY)
def test_the_reveal_stream_folds_a_sundays_one_day_remainder_forward(
    signed_in, fake_week_generation
):
    """
    The same key invariant with the clock pinned, so it holds every day.

    The two tests above read the real date, and on six days in seven a
    Sunday-aware helper and a bare this-week's-Monday one give the same
    answer — which is exactly why the fold-forward went untested here and
    the file went red every Sunday instead. Frozen to a Sunday, the day the
    rule fires: one day left in the period is not a part-week, so the first
    plan is a whole week starting tomorrow.

    The pin was a hand-rolled SimpleNamespace over `main.datetime`, which
    reached main.py and nothing under it — `suggest_planning_period` lives in
    weekly_plan.py behind `from datetime import date` and went on reading the
    real clock. `@pytest.mark.today` pins the whole process.
    """
    res = signed_in.post("/api/onboarding/generate-first-plan/stream")
    assert res.status_code == 200
    done_payload = next(p for n, p in _parse_sse(res.text) if n == "done")

    assert done_payload["week_start_date"] == FROZEN_SUNDAY_KEY, (
        "a Sunday sign-up has one day left in this week's period, so the "
        "first plan is the whole week starting tomorrow"
    )
    assert datetime.date.fromisoformat(done_payload["week_start_date"]).weekday() == 0

    # The key alone does not pin the rule: keying next Monday while still
    # generating one day starting today is the degenerate part-week the
    # fold-forward exists to prevent, and it passes every assertion above.
    # So check what the route actually ASKED generation for.
    assert fake_week_generation["day_count"] == 7, "folded forward means a whole week, not a remainder"
    assert fake_week_generation["period_start"] == FROZEN_SUNDAY_KEY, "content starts with the new period, not today"
