"""
Approving a draft that takes over an already-approved week used to say so
BEFORE the tap — a note above the Approve button ("Once it's approved, I'd
replace Wed to Fri's dinners…") built from preview_approved_takeover's
`replaces` (app/tools/weekly_plan.py). Emily asked for it gone (2026-09-25)
and, in its place, a pop-up AFTER approving that names what was replaced
(option B): no extra tap before the decision, the house toast pattern
after it — see DESIGN_SYSTEM.md §2b S10.

Two layers, and a review of the first pass (0fa71d1) found a real problem
in each:

1. THE ROUTE. `/api/week/{week_start}/approve` (app/main.py) now also
   reports `replaced`: the real dates and meal slots approve_weekly_plan's
   own takeover (`result["took_over"]`, via retire_overlapping_plans /
   _release_plan_days) actually removed, read AFTER the write rather than
   guessed at before it. shell.js's toast prefers this over the
   pre-approval preview (`data.replaces`, from get_week_menu) because that
   preview is captured when the screen loads and can go stale by the time
   Approve is tapped — a swap, or another adult approving something else,
   in between.
2. THE SPAN. `dateSpanLabel` (static/shell.js, was `weekdaySpan`) only
   says "Monday to Friday" when the underlying DATES are actually back to
   back, not just because there happen to be three or more of them —
   several plans can be affected by one takeover, and the days lost are
   not guaranteed to be a run. Judged from date arithmetic, never from
   weekday names.

This file covers both: a route-level test that a real takeover's `replaced`
field carries the true days and slots, and node-level tests of
`dateSpanLabel`/`weekTakeoverToastNote` for consecutive, non-consecutive and
week-boundary-crossing spans, and for preferring the route's real data over
the stale preview.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import tools
from conftest import household_today
from test_week_seven_tiles import _extract

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)

_PRELUDE = (
    _extract("isoWeekdayName", SHELL_JS) + "\n"
    + _extract("isoDaysBetween", SHELL_JS) + "\n"
    + _extract("dateSpanLabel", SHELL_JS) + "\n"
    + _extract("weekTakeoverToastNote", SHELL_JS) + "\n"
)


def _run(expr: str):
    script = _PRELUDE + f"console.log(JSON.stringify({expr}));"
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _span(dates):
    return _run(f"dateSpanLabel({json.dumps(dates)})")


def _note(replaced, preview=None):
    return _run(f"weekTakeoverToastNote({json.dumps(replaced)}, {json.dumps(preview)})")


def _days(*entries):
    """entries: (date, weekday, [(slot, meal_name), ...]) triples, for the
    PREVIEW shape only (preview_approved_takeover's `days`)."""
    return [
        {"date": date, "weekday": weekday,
         "meals": [{"slot": slot, "meal_name": name} for slot, name in meals]}
        for date, weekday, meals in entries
    ]


# ---------------------------------------------------------------------------
# 1. dateSpanLabel: consecutiveness comes from the dates, not the count
# ---------------------------------------------------------------------------

@_needs_node
class TestDateSpanLabel:

    def test_single_date(self):
        assert _span(["2026-09-16"]) == "Wednesday"

    def test_two_dates_always_and_even_when_not_adjacent(self):
        # Two is never ambiguous the way three+ is — "and" reads fine
        # whether or not they're back to back.
        assert _span(["2026-09-14", "2026-09-18"]) == "Monday and Friday"

    def test_three_consecutive_dates_use_to(self):
        assert _span(["2026-09-16", "2026-09-17", "2026-09-18"]) == "Wednesday to Friday"

    def test_three_non_consecutive_dates_are_listed(self):
        # Monday, Wednesday, Friday of the same week — same three weekday
        # NAMES a naive "first to last" would also produce for a real
        # Mon-Tue-Wed run, which is exactly the bug: these are not adjacent
        # days and must not read as "Monday to Friday".
        assert _span(["2026-09-14", "2026-09-16", "2026-09-18"]) == "Monday, Wednesday and Friday"

    def test_four_non_consecutive_dates_are_listed_with_oxford_and(self):
        assert _span(["2026-09-14", "2026-09-15", "2026-09-17", "2026-09-19"]) == \
            "Monday, Tuesday, Thursday and Saturday"

    def test_consecutive_across_a_month_boundary(self):
        # Date.UTC has to carry the month rollover correctly, or this reads
        # as non-consecutive when it plainly isn't.
        assert _span(["2026-09-29", "2026-09-30", "2026-10-01"]) == "Tuesday to Thursday"

    def test_consecutive_across_a_year_boundary(self):
        assert _span(["2026-12-30", "2026-12-31", "2027-01-01"]) == "Wednesday to Friday"

    def test_non_consecutive_across_a_week_boundary(self):
        # Two plans losing separate days either side of a week's seam —
        # the case the review flagged: same-week weekday names would look
        # adjacent even though the actual dates are eight days apart.
        assert _span(["2026-09-18", "2026-09-26"]) == "Friday and Saturday"


# ---------------------------------------------------------------------------
# 2. weekTakeoverToastNote: the real `replaced` summary wins over the
#    stale pre-tap preview
# ---------------------------------------------------------------------------

@_needs_node
class TestWeekTakeoverToastNote:

    def test_nothing_either_way_is_no_toast_text(self):
        assert _note(None, None) is None

    def test_real_replaced_with_no_meal_slots_is_no_toast_text(self):
        # Days were surrendered but held nothing a person would call a meal
        # (an away stretch, or days the plan never covered) — same "don't
        # invent a loss" rule preview_approved_takeover documents.
        assert _note({"dates": ["2026-09-16"], "meal_slots": []}, None) is None

    def test_real_replaced_all_dinners(self):
        replaced = {"dates": ["2026-09-17", "2026-09-16", "2026-09-18"],
                    "meal_slots": ["dinner", "dinner", "dinner"]}
        assert _note(replaced, None) == "Wednesday to Friday’s dinners were replaced"

    def test_real_replaced_mixed_slots_says_meals(self):
        replaced = {"dates": ["2026-09-16", "2026-09-17"],
                    "meal_slots": ["dinner", "lunch", "dinner"]}
        assert _note(replaced, None) == "Wednesday and Thursday’s meals were replaced"

    def test_real_replaced_wins_over_a_stale_preview(self):
        # The preview claims a wider, all-dinner takeover from before the
        # tap; the route's own report says only Wednesday's lunch actually
        # went (a swap landed in between). The toast must say the truth.
        stale_preview = {
            "meal_count": 3,
            "days": _days(
                ("2026-09-16", "Wednesday", [("dinner", "Bean Chili")]),
                ("2026-09-17", "Thursday", [("dinner", "Salmon")]),
                ("2026-09-18", "Friday", [("dinner", "Tacos")]),
            ),
        }
        real = {"dates": ["2026-09-16"], "meal_slots": ["lunch"]}
        assert _note(real, stale_preview) == "Wednesday’s meal was replaced"

    def test_falls_back_to_the_preview_when_the_route_reports_nothing(self):
        # An older server, or any other path that leaves `replaced` unset —
        # the toast should still say what the preview knew rather than stay
        # silent.
        preview = {
            "meal_count": 1,
            "days": _days(("2026-09-16", "Wednesday", [("dinner", "Bean Chili")])),
        }
        assert _note(None, preview) == "Wednesday’s dinner was replaced"

    def test_preview_with_no_meals_is_no_toast_text(self):
        preview = {"meal_count": 0, "days": [{"date": "2026-09-16", "weekday": "Wednesday", "meals": []}]}
        assert _note(None, preview) is None


# ---------------------------------------------------------------------------
# 3. The route: `replaced` is the REAL takeover, read after the write
# ---------------------------------------------------------------------------

def _monday(offset_weeks: int = 0) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _iso(base: str, n: int) -> str:
    return (datetime.date.fromisoformat(base) + datetime.timedelta(days=n)).isoformat()


class TestApproveRouteReportsWhatWasActuallyReplaced:

    def test_an_ordinary_approval_reports_no_takeover(self, signed_in):
        week = _monday(1)
        plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
        tools.plan_meal(week, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)

        res = signed_in.post(f"/api/week/{week}/approve", json={"approved_by": "Emily"})

        assert res.status_code == 200, res.text
        assert res.json()["replaced"] is None

    def test_a_takeover_reports_the_true_days_and_slots(self, signed_in):
        week = _monday(1)
        wed, thu, fri = _iso(week, 2), _iso(week, 3), _iso(week, 4)

        approved_id = tools.create_weekly_plan(
            wed, content_start_date=wed, day_count=3
        )["weekly_plan_id"]
        for day, dish in ((wed, "Bean Chili"), (thu, "Salmon"), (fri, "Tacos")):
            tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=approved_id)
        tools.approve_weekly_plan(approved_id, approved_by="Emily")

        # A draft over exactly those three days, approved through the route.
        draft_id = tools.create_weekly_plan(
            wed, content_start_date=wed, day_count=3
        )["weekly_plan_id"]
        for day, dish in ((wed, "Katsu"), (thu, "Ramen"), (fri, "Curry")):
            tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=draft_id)

        res = signed_in.post(f"/api/week/{wed}/approve", json={"approved_by": "Emily"})

        assert res.status_code == 200, res.text
        body = res.json()
        replaced = body["replaced"]
        assert replaced is not None
        assert replaced["dates"] == [wed, thu, fri]
        assert sorted(replaced["meal_slots"]) == ["dinner", "dinner", "dinner"]

    def test_a_takeover_of_mixed_slots_reports_every_slot(self, signed_in):
        week = _monday(1)
        wed = _iso(week, 2)

        approved_id = tools.create_weekly_plan(
            wed, content_start_date=wed, day_count=1
        )["weekly_plan_id"]
        tools.plan_meal(wed, "Bean Chili", slot="dinner", weekly_plan_id=approved_id)
        tools.plan_meal(wed, "Leftover soup", slot="lunch", weekly_plan_id=approved_id)
        tools.approve_weekly_plan(approved_id, approved_by="Emily")

        draft_id = tools.create_weekly_plan(
            wed, content_start_date=wed, day_count=1
        )["weekly_plan_id"]
        tools.plan_meal(wed, "Katsu", slot="dinner", weekly_plan_id=draft_id)
        tools.plan_meal(wed, "Sandwiches", slot="lunch", weekly_plan_id=draft_id)

        res = signed_in.post(f"/api/week/{wed}/approve", json={"approved_by": "Emily"})

        assert res.status_code == 200, res.text
        replaced = res.json()["replaced"]
        assert replaced["dates"] == [wed]
        assert sorted(replaced["meal_slots"]) == ["dinner", "lunch"]
