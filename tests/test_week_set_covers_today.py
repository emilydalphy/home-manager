"""
"Week set", "This week" and "Next: Wednesday" describe THIS week, or say
which week they mean (Loop Board, 2026-09-15).

The bug: get_cooker_view hands back a plan that hasn't started yet as the
current one, on purpose (cook mode opens next week's draft when nothing
covers today — its docstring and tests/test_needs_you_dinner_visible.py
pin that). Three labels then read that plan's status and dates as if it
were this week's: Now's badge said "Week set" beside "Shall I put Sep 14–20
together?", Plan's title said "This week" over a plan for Oct 11–17, 2027,
and Cook's empty moment said "Next: Wednesday" about a Wednesday in another
year. The fallback stays; the labels now place the plan against today.

- moves._week_state: 'set' / 'draft' only when the period covers the day;
  a plan that starts later is 'ahead' (no chip — WEEK_STATE_LABELS has no
  entry for it, so Now can't wear a badge that contradicts its own nudge).
- weekBandParts (shell.js): "This week" only when the plan covers today,
  "Next week" when it starts inside seven days, else the dates, with the
  year when it isn't this one.
- kitchenNextCookLine / the shelf (shell.js): the date past six days out,
  and the month on a tile outside this month.

The screen halves run shell.js's own functions under node, the house
standard (tests/nodeharness.py). Each test says whether it is a CATCH
(red on main) or a GUARD (green on main, pinning what must not move).
"""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import tools
from app.tools import moves as _moves
from conftest import household_today
from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

# A Tuesday. Inside the week (so the Friday rule doesn't move the nudge on
# to next week) and not a Monday (so "a plan starting next Monday" is a
# different week from today's).
PIN = "2026-09-15"


def _function(name: str, source: str = SHELL_JS) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


# ---------- the server: Now's badge ----------

def _approved_week(start: date, dinner_on: date | None = None, approve: bool = True) -> int:
    plan_id = tools.create_weekly_plan(
        start.isoformat(), content_start_date=start.isoformat(), day_count=7
    )["weekly_plan_id"]
    tools.add_recipe(
        "Roast Chicken",
        ingredients=[{"item": "whole chicken", "qty": "1", "category": "meat/seafood"}],
        prep_time_minutes=20, cook_time_minutes=90, default_servings=2,
    )
    tools.plan_meal((dinner_on or start + timedelta(days=1)).isoformat(), "Roast Chicken",
                    slot="dinner", weekly_plan_id=plan_id)
    if approve:
        tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


@pytest.mark.today(PIN)
def test_a_plan_in_another_year_is_ahead_and_the_nudge_still_asks(signed_in):
    """
    CATCH. The reported data: an approved plan for next year's Thanksgiving
    week, opened on a Tuesday with nothing covering today. The view still
    returns that plan (the fallback is wanted); the badge must not.
    """
    today = household_today()
    plan_id = _approved_week(date(today.year + 1, 10, 11))

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] == plan_id, "the fallback to a not-yet-started plan is kept"

    payload = _moves.today_moves()
    assert payload["week_state"] == "ahead", "an approved week in another year is not 'set'"
    assert signed_in.get("/api/today/moves").json()["week_state"] == "ahead"

    # ...and the other half of the contradiction is still on screen: Now
    # offers to plan the week it is actually living in.
    nudge = tools.get_week_planning_nudge()
    assert nudge["show"] is True and nudge["is_current_week"] is True


@pytest.mark.today(PIN)
def test_a_plan_starting_next_monday_is_ahead_whether_draft_or_approved():
    """CATCH. Not only far-off plans: next week's plan, made on a Tuesday,
    is ahead too — 'draft' and 'set' are both claims about this week."""
    today = household_today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    plan_id = _approved_week(next_monday, approve=False)
    assert _moves.today_moves()["week_state"] == "ahead"
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _moves.today_moves()["week_state"] == "ahead"


@pytest.mark.today(PIN)
def test_a_plan_covering_today_is_set_and_the_nudge_stays_quiet():
    """GUARD. The ordinary week: approved, covering today — the badge says
    so and there is no offer to plan it again."""
    today = household_today()
    monday = today - timedelta(days=today.weekday())
    plan_id = _approved_week(monday, dinner_on=today, approve=False)
    assert _moves.today_moves()["week_state"] == "draft"
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _moves.today_moves()["week_state"] == "set"
    assert tools.get_week_planning_nudge()["show"] is False


@pytest.mark.today(PIN)
def test_the_badge_is_about_the_day_asked_about():
    """GUARD. ?date= names a day; the badge follows it. The last day of the
    period is still covered; the day after is not."""
    today = household_today()
    plan_id = _approved_week(today)
    last = today + timedelta(days=6)
    assert _moves.today_moves(day=last)["week_state"] == "set"
    assert _moves.today_moves(day=last + timedelta(days=1))["week_state"] == "none"
    assert _moves.today_moves(day=today - timedelta(days=1))["week_state"] == "ahead"
    assert plan_id


def test_no_plan_is_still_none():
    """GUARD. Nothing planned at all, and a loose one-off dinner with no
    plan behind it, both read 'none' as before."""
    assert _moves.today_moves()["week_state"] == "none"
    tools.add_recipe("Tacos", ingredients=[{"item": "tortillas", "qty": "1 pack"}])
    tools.plan_meal(household_today().isoformat(), "Tacos", slot="dinner")
    assert _moves.today_moves()["week_state"] == "none"


def test_now_has_no_chip_for_a_plan_that_is_ahead():
    """The client half of the badge: 'ahead' maps to no label, so the band
    can't wear "Week set" over a week that hasn't started."""
    labels = SHELL_JS[SHELL_JS.index("var WEEK_STATE_LABELS = {"):]
    labels = labels[: labels.index(";")]
    assert labels == "var WEEK_STATE_LABELS = { set: 'Week set', draft: 'Draft' }"
    assert "badge: WEEK_STATE_LABELS[data.week_state] || ''," in _function("renderTodayMoves")


# ---------- the client: Plan's title, Cook's "Next:" line, the shelf ----------

def _date_helpers() -> str:
    # Built per test rather than at import, so on a tree without
    # periodRelation the server tests above still run and fail on their
    # own assertions rather than the whole module erroring at collection.
    return (
        _function("addDaysLocal") + "\n"
        + _function("daysBetweenLocal") + "\n"
        + _function("periodRangeLabel") + "\n"
        + _function("periodRelation") + "\n"
        + _function("periodYearSuffix") + "\n"
        "function dayName(dateStr, opts){ return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', opts); }\n"
        "function dayNameShort(iso){ return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' }); }\n"
    )


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _band(today: str, data: dict) -> dict:
    """weekBandParts as Plan's root runs it, with today pinned by hand —
    the band reads todayLocalStr, and a fixed one is the reproducible answer."""
    script = (
        _date_helpers()
        + "function todayLocalStr(){ return " + json.dumps(today) + "; }\n"
        "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        "var WEEK_BADGES = { set: 'Approved', draft: 'Draft' };\n"
        "var planningPeriodDefault = null;\n"
        + _function("weekPlanState") + "\n"
        + _function("weekCountsLabel") + "\n"
        + _function("weekBandParts") + "\n"
        "console.log(JSON.stringify(weekBandParts(" + json.dumps(data) + ", [])));"
    )
    return _node(script)


def _plan_data(start: str, status: str = "approved", day_count: int = 7) -> dict:
    return {
        "weekly_plan_id": 3, "status": status,
        "week_start_date": start, "period_start_date": start, "day_count": day_count,
        "week_label": None,  # the fallback path builds the range from the dates
        "days": [{"date": start}],
    }


@_needs_node
def test_plans_title_says_this_week_only_when_the_plan_covers_today():
    """CATCH for the 2027 and three-weeks-out cases; the rest are guards."""
    today = "2026-09-15"  # a Tuesday
    covering = _band(today, _plan_data("2026-09-14"))
    assert (covering["title"], covering["eyebrow"]) == ("This week", "Sep 14–20")

    next_week = _band(today, _plan_data("2026-09-21", status="draft"))
    assert (next_week["title"], next_week["eyebrow"], next_week["badge"]) == ("Next week", "Sep 21–27", "Draft")

    # Seven days out is still "next week"; eight is not.
    assert _band(today, _plan_data("2026-09-22"))["title"] == "Next week"
    assert _band(today, _plan_data("2026-09-23"))["title"] == "Sep 23–29"

    later = _band(today, _plan_data("2026-10-05"))
    assert (later["title"], later["eyebrow"]) == ("Oct 5–11", "7 days")

    another_year = _band(today, _plan_data("2027-10-11"))
    assert (another_year["title"], another_year["eyebrow"], another_year["badge"]) == ("Oct 11–17, 2027", "7 days", "Approved")

    # A week that straddles New Year names the year it ends in.
    assert _band(today, _plan_data("2026-12-28"))["title"] == "Dec 28–Jan 3, 2027"

    # A week already behind us is its dates too, not "This week".
    assert _band(today, _plan_data("2026-09-07"))["title"] == "Sep 7–13"

    # A custom-length period was always its dates; it gains the year the same way.
    three = _band(today, _plan_data("2027-10-11", day_count=3))
    assert (three["title"], three["eyebrow"]) == ("Oct 11–13, 2027", "3 days")


@_needs_node
def test_with_no_plan_the_band_still_follows_the_suggestion():
    """GUARD. The empty state's "This week" / "Next week" comes from the
    suggestion (period_is_ahead), exactly as before."""
    today = "2026-09-18"  # a Friday: the suggestion is next week
    empty = {"weekly_plan_id": None, "days": [], "week_label": "Sep 21–27", "day_count": 7, "period_is_ahead": True}
    assert _band(today, empty)["title"] == "Next week"
    empty["period_is_ahead"] = False
    empty["week_label"] = "Sep 14–20"
    assert _band(today, empty)["title"] == "This week"


def _next_line(today: str, meals: list[dict]) -> str:
    return _node(
        _date_helpers()
        + _function("kitchenNextCookLine") + "\n"
        "console.log(JSON.stringify(kitchenNextCookLine(" + json.dumps(meals) + ", " + json.dumps(today) + ")));"
    )


def _meal(d: str, meal: str = "Roast Chicken") -> dict:
    return {"entry_id": 1, "date": d, "slot": "dinner", "meal": meal, "is_leftovers": False}


@_needs_node
def test_cooks_next_line_names_the_date_past_six_days_out():
    """CATCH for the dated forms; the weekday form is the guard."""
    today = "2026-09-15"
    assert _next_line(today, [_meal("2026-09-16", "Pancakes")]) == "Next: Wednesday, Pancakes."
    # Six days out is still a weekday; seven is a date.
    assert _next_line(today, [_meal("2026-09-21")]) == "Next: Monday, Roast Chicken."
    assert _next_line(today, [_meal("2026-09-22")]) == "Next: Tue Sep 22 — Roast Chicken."
    assert _next_line(today, [_meal("2026-10-14")]) == "Next: Wed Oct 14 — Roast Chicken."
    # Another year says so.
    assert _next_line(today, [_meal("2027-10-13")]) == "Next: Wed Oct 13, 2027 — Roast Chicken."
    assert _next_line(today, []) == ""


@_needs_node
def test_the_shelf_carries_the_month_on_a_night_outside_this_one():
    """CATCH. The strip's tiles say "13" under WED; outside this month that
    is "Oct 13". Tonight's tile is always this month and never changes."""
    script = (
        _date_helpers()
        + "function escapeHtml(s){ return String(s == null ? '' : s); }\n"
        "function dishShortWord(n){ return n; }\n"
        + _function("cookShelfNights") + "\n"
        + _function("cookShelfTileHtml") + "\n"
        "var meals = " + json.dumps([_meal("2026-09-15", "Chili"), _meal("2027-10-13")]) + ";\n"
        "function nums(html){ return html.match(/shelf-num\">([^<]*)</g).map(function (m) { return m.replace(/.*\">/, '').replace('<', ''); }); }\n"
        "console.log(JSON.stringify({"
        "  thisWeek: nums(cookShelfNights(meals, { period_start_date: '2026-09-14', day_count: 7 }, '2026-09-15').map(cookShelfTileHtml).join('')),"
        "  ahead: nums(cookShelfNights(meals, { period_start_date: '2027-10-11', day_count: 7 }, '2026-09-15').map(cookShelfTileHtml).join('')),"
        "  monthEdge: nums(cookShelfNights([], { period_start_date: '2026-09-28', day_count: 7 }, '2026-09-29').map(cookShelfTileHtml).join(''))"
        "}));"
    )
    out = _node(script)
    assert out["thisWeek"] == ["14", "15", "16", "17", "18", "19", "20"]
    assert out["ahead"] == ["Oct 11", "Oct 12", "Oct 13", "Oct 14", "Oct 15", "Oct 16", "Oct 17"]
    # A week that crosses into next month: the month appears exactly where it changes.
    assert out["monthEdge"] == ["28", "29", "30", "Oct 1", "Oct 2", "Oct 3", "Oct 4"]
