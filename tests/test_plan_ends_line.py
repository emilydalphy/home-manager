"""
The plan says where it ends (Loop Board: "Plan day strip shows past
Sat/Sun/Mon as 'Not planned', and 'Plan the rest of my week' is offered
while nothing says why the weekend wasn't planned" — second half).

  * A plan that stops before the week it is drawn in (made on a Wednesday
    for Wed–Fri, or a short custom range) carries
    `next_period.after_short_plan`; the Plan root then says "This plan ends
    Friday." in one quiet line, and the button already on screen (the
    approved dock's / the draft band's) reads "Plan from Saturday" — same
    stretch, same tap (planNextWeek). No second button.
  * Nothing is said when the plan runs to the end of its week, when the
    household's own rhythm is that short, or when the next day is already
    held by another plan.
  * "Before this plan" is only for days before the plan's first day — a
    past day inside the plan that was emptied keeps "Not planned".
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import household_today
from app.tools import weekly_plan as _weekly_plan
from tests import nodeharness

SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(name: str) -> str:
    m = re.search(r"^  (?:async )?function " + re.escape(name) + r"\(", SHELL_JS, re.M)
    assert m, f"{name} not found in shell.js"
    i = SHELL_JS.index("{", SHELL_JS.index(")", m.start()))
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[m.start():j + 1]
        j += 1


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# ---------- the server's flag ----------

def _next_monday() -> _dt.date:
    # A week wholly in the future, so "has this plan already ended" never
    # sends next_period_after to its fallback whatever day the suite runs.
    today = household_today()
    return today + _dt.timedelta(days=7 - today.weekday())


def _offer(start: _dt.date, day_count: int, filed: _dt.date) -> dict:
    plan = {
        "weekly_plan_id": 987654,  # no such plan: nothing to exclude
        "period_start_date": start.isoformat(),
        "day_count": day_count,
        "week_start_date": filed.isoformat(),
    }
    return _weekly_plan.next_period_after(plan, today=household_today().isoformat())


def test_a_wednesday_to_friday_plan_ends_before_its_week_and_the_offer_starts_saturday():
    mon = _next_monday()
    nxt = _offer(mon + _dt.timedelta(days=2), 3, filed=mon)
    assert nxt["after_short_plan"] is True
    assert nxt["start_date"] == (mon + _dt.timedelta(days=5)).isoformat()
    assert nxt["is_planned"] is False


def test_a_short_custom_range_filed_under_its_own_first_day_is_flagged_too():
    wed = _next_monday() + _dt.timedelta(days=2)
    assert _offer(wed, 3, filed=wed)["after_short_plan"] is True


def test_a_plan_that_has_already_ended_falls_back_to_the_ordinary_wording():
    # Review, 2026-10-17: a Mon–Wed plan seen on Thursday. The fallback
    # suggestion starts today, the same day as "the day after the plan",
    # and must not be read as one.
    # Pinned to the review's own dates: next_period_after takes the day as
    # an argument, and on a Thursday the fallback suggestion is this week
    # from today — which is what made the two days coincide.
    nxt = _weekly_plan.next_period_after({
        "weekly_plan_id": 987654,
        "period_start_date": "2026-10-12",
        "day_count": 3,
        "week_start_date": "2026-10-12",
    }, today="2026-10-15")
    assert nxt["start_date"] == "2026-10-15", "the fallback really does start today"
    assert nxt["after_short_plan"] is False


def test_a_part_week_that_runs_to_sunday_and_a_whole_week_say_nothing():
    mon = _next_monday()
    assert _offer(mon + _dt.timedelta(days=2), 5, filed=mon)["after_short_plan"] is False
    assert _offer(mon, 7, filed=mon)["after_short_plan"] is False


# ---------- the Plan root ----------

_DAYNAME = (
    "function dayName(d, opts){ var names = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];"
    " return names[new Date(d + 'T00:00:00').getDay()]; }\n"
)


def _prelude() -> str:
    return (
        _function("escapeHtml") + "\n" + _DAYNAME
        + "var planningPeriodDefault = null;\n"
        + "function thisWeekStartLocal() { return '2026-10-12'; }\n"
        + "var TODAY = '2026-10-14';\n"
        + "function todayLocalStr() { return TODAY; }\n"
        + _function("addDaysLocal") + "\n"
        + _function("nextPeriodFor") + "\n"
        + _function("planEntryLabel") + "\n"
        + _function("planNextLabel") + "\n"
        + _function("planEndsEarly") + "\n"
        + _function("planEndsLineHtml") + "\n"
    )


# Wed Oct 14 – Fri Oct 16, 2026; the offer is Sat Oct 17 for a week.
_SHORT = {"start_date": "2026-10-17", "day_count": 7, "is_current_period": False,
          "is_planned": False, "draft_plan_id": None, "after_short_plan": True}


@_needs_node
def test_the_root_says_where_the_plan_ends_and_the_existing_button_names_the_next_day():
    data = {"weekly_plan_id": 1, "days": [{"date": "2026-10-14"}], "next_period": _SHORT}
    out = _node(_prelude() + f"""
var data = {json.dumps(data)};
console.log(JSON.stringify([planEndsLineHtml(data), planNextLabel(nextPeriodFor(data, data.days))]));""")
    assert "This plan ends Friday." in out[0]
    assert "<button" not in out[0], "the next step is the button already on screen"
    assert out[1] == "Plan from Saturday"


@_needs_node
def test_on_the_plans_last_day_it_says_today_and_tomorrow():
    data = {"weekly_plan_id": 1, "days": [{"date": "2026-10-14"}], "next_period": _SHORT}
    out = _node(_prelude() + f"""
TODAY = '2026-10-16';
var data = {json.dumps(data)};
console.log(JSON.stringify([planEndsLineHtml(data), planNextLabel(nextPeriodFor(data, data.days))]));""")
    assert "This plan ends today." in out[0]
    assert out[1] == "Plan from tomorrow"


@_needs_node
def test_no_line_when_the_week_is_whole_or_the_next_day_is_already_held():
    whole = dict(_SHORT, after_short_plan=False)
    held = dict(_SHORT, is_planned=True)
    drafted = dict(_SHORT, is_planned=True, draft_plan_id=7)
    out = _node(_prelude() + f"""
console.log(JSON.stringify([{json.dumps(whole)}, {json.dumps(held)}, {json.dumps(drafted)}].map(function (n) {{
  return [planEndsLineHtml({{ days: [], next_period: n }}), planNextLabel(n)];
}})));""")
    assert out[0] == ["", "Plan next week"]
    assert out[1] == ["", "Re-plan next week"]
    assert out[2] == ["", "See next week’s draft"]


def test_both_roots_carry_the_line_above_the_week():
    # One Plan root for a draft and an approved week since 2026-10-30.
    assert "return planWeekHtml(data, days);" in _function("weekStepHtml")
    assert "weekSuggestedNoteHtml(data) + planEndsLineHtml(data) +" in _function("planWeekHtml")
    assert "draftSoftNoteHtml(data) + planEndsLineHtml(data)" in _function("reviewStepHtml")


# ---------- "Before this plan" only before the plan ----------

@_needs_node
def test_before_this_plan_is_only_for_days_before_the_plans_first_day():
    out = _node(
        "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        "var weekState = { data: { period_start_date: '2026-10-14' } };\n"
        + _function("pastEmptyWord")
        + "\nconsole.log(JSON.stringify(["
        # a lead-in day, flagged by the server
        "pastEmptyWord({ date: '2026-10-12', before_plan_start: true, dinner: null }),"
        # a day inside the plan the household emptied
        "pastEmptyWord({ date: '2026-10-15', before_plan_start: false, breakfast: null, lunch: null, dinner: null }),"
        # a payload without the flag: the plan's first day decides
        "pastEmptyWord({ date: '2026-10-13', dinner: null }),"
        "pastEmptyWord({ date: '2026-10-15', dinner: null })"
        "]));"
    )
    assert out == ["Before this plan", "Not planned", "Before this plan", "Not planned"]
