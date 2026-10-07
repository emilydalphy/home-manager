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
from test_plan_next_on_draft import SHELL_JS

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
def test_the_dock_reads_see_next_weeks_draft_and_opens_it():
    """CATCH. The dock's words, and its tap pins the draft and loads it
    instead of starting the questions over."""
    days = _week()
    out = _run(_band_prelude()
               + _extract("weekDecideHtml", SHELL_JS) + "\n"
               + "function wkDockMoreHtml() { return ''; }\n"
               + "var panels = { week: {} };\n"
               + "function loadWeekMenu(p) { CALLS.push(['load', weekState.showPlanId, weekState.showWeekStart]); }\n"
               + f"""
var data = {json.dumps(dict(_approved(days), next_period=_DRAFT_NEXT))};
weekState.data = data;
var html = weekDecideHtml(data, nextPeriodFor(data, data.days));
planNextWeek();
console.log(JSON.stringify({{ html: html, calls: CALLS,
  plain: planNextLabel({json.dumps(dict(_DRAFT_NEXT, draft_plan_id=None))}) }}));""")
    assert "See next week’s draft" in out["html"]
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
