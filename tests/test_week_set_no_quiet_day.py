"""
Today's empty moment on a day the week already covers (defect hunt,
2026-10-09).

Walked on a throwaway database: an approved Monday-to-Sunday week, Friday,
tonight's dinner called off with "Night off" — so the day has no moves.
From Friday the planning nudge offers NEXT week (weekly_plan's
PLAN_AHEAD_FROM_WEEKDAY), and `todayNeedsPlan` only asked whether the nudge
named a week, not which one. So Today read:

    WEEK SET · "Night off — enjoy." · "Quiet day. Want me to sort dinner,
    or the whole week?" · [Let's plan the week] [Just tonight]

— offering to sort the dinner the household had just called off, beside a
badge saying the week is set, with Saturday's cook (the tomorrow card)
hidden behind it. The same "two screens, two stories" PRODUCT_FLOWS.md
recorded on 2026-09-15; that fix (moves._week_state) kept the badge honest
but left the empty moment reading the nudge alone. A night nobody is home
on a set week lands in the same place.

The quiet-day offer is for a day NO plan covers. With today covered
(`week_state` 'set' or 'draft'), the empty moment falls through to the
tomorrow card, the next-week card stays on screen, and the dock carries the
ordinary nudge ("Let's plan the week" / "Not now").

The functions run under node, sliced from shell.js (tests/nodeharness.py).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness

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

_TOMORROW = {"id": "cook:6", "kind": "cook", "title": "Chicken stir fry", "date": "2026-10-10"}
_NEXT_WEEK = {"show": True, "week_start": "2026-10-12", "week_label": "Oct 12–18", "day_count": 7,
              "is_current_week": False, "dismiss_key": "plan_week_nudge:2026-10-12"}


def _run(moves, nudge):
    res = nodeharness.run_node(
        _PRELUDE + "console.log(JSON.stringify(render(%s, %s)));" % (json.dumps(moves), json.dumps(nudge)),
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _day(week_state):
    return {"date": "2026-10-09", "moves": [], "featured": None, "week_state": week_state,
            "tomorrow": _TOMORROW, "holiday": None}


@_needs_node
@pytest.mark.parametrize("week_state", ["set", "draft"])
def test_a_covered_day_with_nothing_on_it_is_not_a_quiet_day(week_state):
    """The repro: a set (or drafted) week, tonight called off, Friday's
    next-week nudge. No offer to sort dinner, no "Just tonight" — the
    tomorrow card, the next-week card, and the ordinary nudge dock."""
    out = _run(_day(week_state), _NEXT_WEEK)
    assert "Quiet day" not in out["empty"]
    assert "today-just-tonight" not in out["dock"], "no offer to sort a dinner that was called off"
    assert out["empty"] == '<div class="tomorrow">Chicken stir fry</div>'
    assert out["nudgeHidden"] is False, "the NEXT WEEK card is the offer on this screen"
    assert "plan-nudge-go" in out["dock"] and "plan-nudge-dismiss" in out["dock"]


@_needs_node
@pytest.mark.parametrize("week_state", ["none", "ahead"])
def test_a_day_no_plan_covers_still_gets_the_quiet_day_offer(week_state):
    """The case the empty moment was written for is unchanged: nothing covers
    today (no plan, or only one that starts later) — the offer IS the
    screen, and "Just tonight" is a real way to get dinner."""
    nudge = dict(_NEXT_WEEK, week_start="2026-10-05", is_current_week=True)
    out = _run(_day(week_state), nudge)
    assert "Quiet day. Want me to sort dinner, or the whole week?" in out["empty"]
    assert "today-just-tonight" in out["dock"] and "plan-nudge-go" in out["dock"]
    assert out["nudgeHidden"] is True


@_needs_node
def test_a_set_week_with_the_next_week_offer_dismissed_is_just_tomorrow():
    """Edge: the next-week offer was dismissed (week_start kept, show False).
    A set day is then simply tomorrow's card — no dock at all, rather than
    the quiet-day offer coming back because the dismissal still names a week."""
    dismissed = {"show": False, "week_start": "2026-10-12", "dismissed": True}
    out = _run(_day("set"), dismissed)
    assert out["empty"] == '<div class="tomorrow">Chicken stir fry</div>'
    assert out["dock"] == ""
