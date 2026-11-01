"""
Plan: a drafted next week couldn't be reached again after leaving the
screen (defect hunt 2026-10-07, High).

    Plan › Plan next week › questions › Draft my week; reload. Plan shows
    only this week, and the dock's only way forward is "Re-plan next
    week" — the questions again, then a new draft generated over the one
    just made.

Only /plan-week's ?drafted= hand-back ever pinned the draft on screen.
Now next_period (weekly_plan.next_period_after) names a draft that already
holds the next stretch's first day, the dock reads "See next week's draft",
and the tap opens that draft — Approve and its Re-plan pill included.
"""
from __future__ import annotations

import datetime
import json
import shutil

import pytest

from app import tools
from app.tools import weekly_plan as _weekly_plan

from test_plan_cards_2026_09_18 import _run, _week, _approved
from test_week_seven_tiles import _extract
from test_draft_front_door import _band_prelude
from test_plan_cards_2026_09_18 import SHELL_JS

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _this_week_and_next(approve_next: bool):
    today = _weekly_plan._household_today()
    this_week = tools.create_weekly_plan((today - datetime.timedelta(days=1)).isoformat(), day_count=7)["weekly_plan_id"]
    tools.approve_weekly_plan(this_week, approved_by="Emily")
    nxt_start = today + datetime.timedelta(days=6)
    nxt = tools.create_weekly_plan(nxt_start.isoformat(), day_count=7)["weekly_plan_id"]
    if approve_next:
        tools.approve_weekly_plan(nxt, approved_by="Emily")
    return this_week, nxt


def test_next_period_names_the_draft_that_holds_it(signed_in):
    """CATCH. The approved week's payload says next week is a draft, and
    which — through the HTTP the Plan tab reads."""
    this_week, nxt = _this_week_and_next(approve_next=False)
    menu = signed_in.get(f"/api/week-menu?weekly_plan_id={this_week}").json()
    nxt_row = tools.get_weekly_plan(nxt)
    assert menu["next_period"]["is_planned"] is True
    assert menu["next_period"]["draft_plan_id"] == nxt
    assert menu["next_period"]["draft_week_start"] == nxt_row["week_start_date"]


def test_an_approved_next_week_is_not_called_a_draft():
    """GUARD. Approved next week keeps "Re-plan next week" — no draft to see."""
    this_week, _ = _this_week_and_next(approve_next=True)
    nxt = _weekly_plan.next_period_after(tools.get_weekly_plan(this_week))
    assert nxt["is_planned"] is True and nxt["draft_plan_id"] is None


_DRAFT_NEXT = {"start_date": "2026-09-28", "day_count": 7, "is_current_period": False, "is_planned": True,
               "draft_plan_id": 9, "draft_week_start": "2026-09-28"}


@_needs_node
def test_plan_the_week_after_says_its_draft_is_waiting_and_opens_it():
    """CATCH. The approved dock's "See next week's draft" moved into More
    as "Plan the week after" (the declutter, 2026-10-30): its line says
    the draft is waiting, and its tap pins the draft and loads it instead
    of starting the questions over."""
    days = _week()
    out = _run(_band_prelude()
               + _extract("planAfterTitle", SHELL_JS) + "\n"
               + _extract("planAfterLine", SHELL_JS) + "\n"
               + "var panels = { week: {} };\n"
               + "function dayName(d) { return 'Monday, Sep 28'; }\n"
               + "function loadWeekMenu(p) { CALLS.push(['load', weekState.showPlanId, weekState.showWeekStart]); }\n"
               + f"""
var data = {json.dumps(dict(_approved(days), next_period=_DRAFT_NEXT))};
weekState.data = data;
var next = nextPeriodFor(data, data.days);
var title = planAfterTitle(next), line = planAfterLine(next);
planNextWeek();
console.log(JSON.stringify({{ title: title, line: line, calls: CALLS,
  plain: planNextLabel({json.dumps(dict(_DRAFT_NEXT, draft_plan_id=None))}) }}));""")
    assert out["title"] == "Plan the week after"
    assert "its draft is waiting" in out["line"] and out["line"].startswith("Starts ")
    assert out["calls"] == [["load", 9, None]], "opens the draft BY ITS ID, never the questions"
    assert out["plain"] == "Re-plan next week", "an approved next week still re-plans"


@_needs_node
def test_a_next_week_with_no_draft_still_starts_the_questions():
    """GUARD."""
    days = _week()
    nxt = {"start_date": "2026-09-28", "day_count": 7, "is_current_period": False, "is_planned": False}
    out = _run(_band_prelude() + f"""
weekState.data = {json.dumps(dict(_approved(days), next_period=nxt))};
planNextWeek();
console.log(JSON.stringify(CALLS));""")
    assert out == [["2026-09-28", 7]]


# ---------- review, 2026-10-07: pin by plan, not by date ----------

def test_a_draft_filed_under_another_weeks_date_is_still_the_one_opened(signed_in):
    """CATCH. After a takeover (or a custom range) a draft's week_start_date
    is not its first day, and that date resolves to the plan COVERING it —
    the week already on screen. The dock's tap reloaded it: a dead button.
    The draft's id reaches it whatever its date says."""
    # The reviewer's repro: this week approved, next week drafted, then
    # this week re-planned from today — the takeover trims next week's
    # draft to start a few days later and leaves its week_start_date.
    today = _weekly_plan._household_today()
    this_week = tools.create_weekly_plan((today - datetime.timedelta(days=2)).isoformat(), day_count=7)["weekly_plan_id"]
    tools.approve_weekly_plan(this_week, approved_by="Emily")
    nxt = tools.create_weekly_plan((today + datetime.timedelta(days=5)).isoformat(), day_count=7)["weekly_plan_id"]
    replan = tools.create_weekly_plan(today.isoformat(), day_count=7)["weekly_plan_id"]
    _weekly_plan.retire_overlapping_plans(replan, today.isoformat(), 7)
    this_week = replan

    menu = signed_in.get(f"/api/week-menu?weekly_plan_id={this_week}").json()
    np = menu["next_period"]
    assert np["draft_plan_id"] == nxt and np["draft_week_start"] != np["start_date"]
    by_date = signed_in.get(f"/api/week/{np['draft_week_start']}/intake").json().get("plan_id")
    assert by_date != nxt, "the date resolves to the week on screen — why the pin is by id"
    shown = signed_in.get(f"/api/week-menu?weekly_plan_id={np['draft_plan_id']}").json()
    assert shown["weekly_plan_id"] == nxt and shown["status"] == "draft"


def test_loadweekmenu_pins_by_id_and_lets_go_once_approved():
    body = _extract("loadWeekMenu", SHELL_JS)
    assert "if (weekState.showPlanId) {" in body
    assert "'?weekly_plan_id=' + encodeURIComponent(weekState.showPlanId)" in body
    assert "data.status === 'approved'" in body and "weekState.showPlanId = null;" in body


@_needs_node
def test_the_pin_is_dropped_after_the_load_that_shows_it_approved():
    """The approved draft's "All set" still shows; the next load is this week."""
    out = _run(
        "var weekState = { showPlanId: 9, showWeekStart: null, cookView: null };\n"
        "var weekMenuSeq = 0;  // loadWeekMenu's newest-reply-wins counter (ticks-refresh-plan)\n"
        "var URLS = []; var REPLY = { weekly_plan_id: 9, status: 'draft' };\n"
        "var Api = { fetch: async function (u) { URLS.push(u); return { ok: true, json: async function () { return REPLY; } }; } };\n"
        "async function loadPlanningPeriodDefault() {}\n"
        "function todayLocalStr() { return '2026-10-07'; }\n"
        "function renderWeekMenu() {}\nfunction refreshKitchenPanel() {}\n"
        "async function planIdForWeek() { URLS.push('intake'); return null; }\n"
        "var planningPeriodDefault = null, planningPeriodFetchedOn = '';\n"
        + "async " + _extract("loadWeekMenu", SHELL_JS) + "\n"
        + """
(async function () {
  var p = { querySelector: function () { return {}; } };
  await loadWeekMenu(p);
  var a = weekState.showPlanId;
  REPLY = { weekly_plan_id: 9, status: 'approved' };
  await loadWeekMenu(p);
  var b = weekState.showPlanId;
  await loadWeekMenu(p);
  console.log(JSON.stringify({ urls: URLS, a: a, b: b }));
})();
""")
    assert out["a"] == 9 and out["b"] is None
    assert out["urls"] == ["/api/week-menu?weekly_plan_id=9", "/api/week-menu?weekly_plan_id=9", "/api/week-menu"]


# --- A draft over PART of an approved next week (Emily, 2026-10-31) -------

def _approved_next_and_part_draft(offset_days=2):
    this_week, nxt = _this_week_and_next(approve_next=True)
    nxt_start = datetime.date.fromisoformat(tools.get_weekly_plan(nxt)["period_start_date"])
    part = tools.create_weekly_plan((nxt_start + datetime.timedelta(days=offset_days)).isoformat(),
                                    day_count=7 - offset_days)["weekly_plan_id"]
    return this_week, nxt, part


def test_a_part_week_draft_over_approved_next_week_is_named_too(signed_in):
    """CATCH. Next week approved, re-planned from its third day: the draft
    is saved, and Plan's next_period names it so "See next week's draft"
    (and More's "its draft is waiting") reach it."""
    this_week, nxt, part = _approved_next_and_part_draft()
    assert tools.get_weekly_plan(nxt)["status"] == "approved", "the approved week is untouched by a draft"
    menu = signed_in.get(f"/api/week-menu?weekly_plan_id={this_week}").json()
    assert menu["next_period"]["draft_plan_id"] == part
    assert menu["next_period"]["draft_week_start"] == tools.get_weekly_plan(part)["week_start_date"]
    assert menu["next_period"]["day_count"] == 7, "a full week's stretch, so the label is next week's"


def test_a_draft_later_in_a_free_next_week_is_not_called_next_weeks_draft():
    """GUARD. With next week's first day free, the stretch is still to plan:
    a draft that starts mid-week leaves the link as "Plan next week"."""
    today = _weekly_plan._household_today()
    this_week = tools.create_weekly_plan((today - datetime.timedelta(days=1)).isoformat(), day_count=7)["weekly_plan_id"]
    tools.approve_weekly_plan(this_week, approved_by="Emily")
    tools.create_weekly_plan((today + datetime.timedelta(days=9)).isoformat(), day_count=4)
    nxt = _weekly_plan.next_period_after(tools.get_weekly_plan(this_week))
    assert nxt["draft_plan_id"] is None and nxt["is_planned"] is False


@_needs_node
def test_the_part_week_drafts_link_reads_see_next_weeks_draft():
    out = _run(_extract("planNextLabel", SHELL_JS) + "\n"
               + "function planEntryLabel(){return 'X';} function addDaysLocal(){return '';} function todayLocalStr(){return '';}\n"
               + "console.log(JSON.stringify(planNextLabel(" + json.dumps(dict(_DRAFT_NEXT, draft_week_start="2026-09-30")) + ")));")
    assert out == "See next week’s draft"
