"""
Today's "Quiet day" on a night the usual week has off (Loop Board bug,
2026-10-09; stacked on week-set-no-quiet-day).

Repro: a usual week saved with today's dinner "Don't plan", and no plan
covering today (before the first week, or between weeks). Today read
"Quiet day. Want me to sort dinner, or the whole week?" over [Let's plan the
week] [Just tonight] — offering to sort the dinner the household had said not
to plan. The empty moment (shell.js renderTodayEmpty) had nothing to tell it
tonight's dinner was off; the usual week lives server-side.

The moves payload now carries `usual_week_dinner_off`, read through the same
usual_week helpers get_needs_you_items uses on the
usual-week-night-off-not-asked branch. The empty moment, given it, offers to
plan the week and nothing about tonight. The server half is asserted here;
the client half runs shell.js's own functions under node.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import tools
from app.tools.usual_week import WEEKDAYS
from conftest import household_today


def _weekday(offset_days: int = 0) -> str:
    return WEEKDAYS[(household_today() + datetime.timedelta(days=offset_days)).weekday()]


# ---------- server: the payload says so ----------

def test_tonight_off_in_the_usual_week_with_no_plan_is_in_the_payload():
    tools.save_usual_week(grid={"dinner": {_weekday(0): "off"}})
    payload = tools.today_moves()
    assert payload["week_state"] == "none"
    assert payload["usual_week_dinner_off"] is True


def test_dinner_off_all_week_is_in_the_payload():
    # Edge: off_slots_on leaves a meal off EVERY day to the counts, so
    # switched_off_meals is the other half.
    tools.save_usual_week(grid={"dinner": {d: "off" for d in WEEKDAYS}})
    assert tools.today_moves()["usual_week_dinner_off"] is True


def test_a_night_the_usual_week_has_on_is_not_off():
    # Guard: another night's dinner off, and tonight's LUNCH off, say
    # nothing about tonight's dinner.
    tools.save_usual_week(grid={"dinner": {_weekday(2): "off"}, "lunch": {_weekday(0): "off"}})
    assert tools.today_moves()["usual_week_dinner_off"] is False


def test_no_usual_week_saved_is_not_off():
    assert tools.today_moves()["usual_week_dinner_off"] is False


# ---------- client: the empty moment reads it ----------

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs shell.js's own functions")


def _function(name: str) -> str:
    marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s);}\n"
    "function emptyMomentHtml(icon, text){ return '<p class=\"empty\">' + text + '</p>'; }\n"
    "function tomorrowCardHtml(m){ return '<div class=\"tomorrow\">' + m.title + '</div>'; }\n"
    "function todayLocalStr(){ return '2026-10-09'; }\n"
    "function todayMoveById(){ return null; }\n"
    "function openRecipeFor(){}\n function moveRecipeTarget(){}\n"
    "function runTodayMoveAction(){}\n function dismissPlanWeekNudge(){}\n"
    "function startPlanningWeek(){}\n function openAskSheet(){}\n"
    "var planningPeriodDefault = null;\n"
    + _function("todayIsEmpty")
    + _function("todayNeedsPlan")
    + _function("tonightIsOff")
    + _function("renderTodayEmpty")
    + _function("renderTodayDock")
    + """
function el() {
  return { innerHTML: '', hidden: false,
           querySelector: function () { return null; },
           querySelectorAll: function () { return []; },
           classList: { remove: function () {} } };
}
function render(moves, nudge) {
  var els = { '#today-empty': el(), '#plan-week-nudge': el(), '#today-dock': el() };
  var panel = { _moves: moves, _nudge: nudge, _featured: null, _nudgeDismissed: false,
                querySelector: function (s) { return els[s] || null; } };
  renderTodayEmpty(panel);
  renderTodayDock(panel);
  return { empty: els['#today-empty'].innerHTML, emptyHidden: els['#today-empty'].hidden,
           nudgeHidden: els['#plan-week-nudge'].hidden, dock: els['#today-dock'].innerHTML };
}
"""
)

_THIS_WEEK = {"show": True, "week_start": "2026-10-05", "week_label": "Oct 5–11", "day_count": 7,
              "is_current_week": True, "dismiss_key": "plan_week_nudge:2026-10-05"}


def _run(moves, nudge):
    res = nodeharness.run_node(
        _PRELUDE + "console.log(JSON.stringify(render(%s, %s)));" % (json.dumps(moves), json.dumps(nudge)),
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _day(week_state, dinner_off):
    return {"date": "2026-10-09", "moves": [], "featured": None, "week_state": week_state,
            "tomorrow": None, "holiday": None, "usual_week_dinner_off": dinner_off}


@_needs_node
@pytest.mark.parametrize("week_state", ["none", "ahead"])
def test_a_night_off_with_no_plan_offers_the_week_and_not_tonight(week_state):
    out = _run(_day(week_state, True), _THIS_WEEK)
    assert "sort dinner" not in out["empty"]
    assert "Quiet day" not in out["empty"]
    assert out["empty"] == '<p class="empty">Night off tonight. Want me to plan the week?</p>'
    assert "today-just-tonight" not in out["dock"], "no offer to sort a dinner they said not to plan"
    assert "plan-nudge-go" in out["dock"], "planning the week is still the ask"
    assert out["nudgeHidden"] is True


@_needs_node
def test_a_night_that_is_on_still_gets_the_quiet_day_offer():
    # Guard: exactly as before when tonight's dinner is on (or the field is
    # missing — an older server).
    for moves in (_day("none", False), {k: v for k, v in _day("none", False).items() if k != "usual_week_dinner_off"}):
        out = _run(moves, _THIS_WEEK)
        assert out["empty"] == '<p class="empty">Quiet day. Want me to sort dinner, or the whole week?</p>'
        assert "today-just-tonight" in out["dock"] and "plan-nudge-go" in out["dock"]


@_needs_node
def test_a_set_week_is_untouched_by_the_usual_week():
    # Guard: a covered day falls through to the tomorrow card as on the base
    # branch, whatever the usual week says.
    moves = dict(_day("set", True), tomorrow={"id": "cook:6", "title": "Chicken stir fry"})
    out = _run(moves, dict(_THIS_WEEK, week_start="2026-10-12", is_current_week=False))
    assert out["empty"] == '<div class="tomorrow">Chicken stir fry</div>'
    assert "today-just-tonight" not in out["dock"]
