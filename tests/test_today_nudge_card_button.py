"""
Today's "Shall I put Oct 19–25 together?" card when another action owns the
dock (Loop Board, 2026-10-18; Emily chose Option A).

The dock is Today's one action. The featured move ("Go shopping") wins it,
and the plan offer used to be left on screen as a question with nothing on
Today to answer it with. Now the card carries its own small outline
"Plan next week" button exactly when the dock is taken, and does what the
dock's "Let's plan the week" would have done (startPlanningWeek with the
nudge's week and length). With the dock free, nothing changes: the dock
answers, and the card has no button of its own.

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


# A tiny DOM: elements are found by id or class inside their own innerHTML,
# rebuilt on every query so a re-render is seen. Enough for the card and the
# dock, which are all these functions touch.
_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s);}\n"
    "function emptyMomentHtml(icon, text){ return '<p class=\"empty\">' + text + '</p>'; }\n"
    "function tomorrowCardHtml(m){ return '<div class=\"tomorrow\">' + m.title + '</div>'; }\n"
    "function todayLocalStr(){ return '2026-10-19'; }\n"
    "function todayMoveById(){ return null; }\n"
    "function openRecipeFor(){}\n function moveRecipeTarget(){}\n"
    "var calls = [];\n"
    "function runTodayMoveAction(p, id){ calls.push(['move', id]); }\n"
    "function dismissPlanWeekNudge(){ calls.push(['dismiss']); }\n"
    "function startPlanningWeek(ws, days){ calls.push(['plan', ws, days]); }\n"
    "function openAskSheet(){}\n"
    "var planningPeriodDefault = null;\n"
    + _function("todayShownMoves")
    + _function("todayIsEmpty")
    + _function("todayNeedsPlan")
    + _function("tonightIsOff")
    + _function("renderTodayEmpty")
    + _function("planEntryLabel")
    + _function("startPlanningFromNudge")
    + _function("renderTodayDock")
    + _function("renderPlanWeekNudge")
    + r"""
function node(html, sel) {
  // The first tag carrying this id/class; its attributes are read live.
  var re = sel[0] === '#'
    ? new RegExp('<(\\w+)([^>]*\\sid="' + sel.slice(1) + '"[^>]*)>([^<]*)')
    : new RegExp('<(\\w+)([^>]*\\sclass="[^"]*\\b' + sel.slice(1) + '\\b[^"]*"[^>]*)>([^<]*)');
  var m = re.exec(html);
  if (!m) return null;
  return { tag: m[1], attrs: m[2], text: m[3], hidden: /\shidden\b/.test(m[2]),
           handlers: {}, addEventListener: function (t, f) { this.handlers[t] = f; } };
}
function container() {
  var self = { innerHTML: '', hidden: false, found: {},
    querySelector: function (s) {
      if (!self.found[s] || self.found[s].src !== self.innerHTML) {
        var n = node(self.innerHTML, s);
        if (n) n.src = self.innerHTML;
        self.found[s] = n;
      }
      return self.found[s];
    },
    querySelectorAll: function () { return []; },
    closest: function () { return panel; },
    classList: { remove: function () {} } };
  return self;
}
var panel;
function render(moves, nudge, featured) {
  var els = { '#today-empty': container(), '#plan-week-nudge': container(), '#today-dock': container() };
  panel = { _moves: moves, _featured: featured,
            querySelector: function (s) { return els[s] || null; } };
  renderPlanWeekNudge(els['#plan-week-nudge'], nudge);
  var cardGo = els['#plan-week-nudge'].querySelector('#plan-nudge-card-go');
  var out = { dock: els['#today-dock'].innerHTML, card: els['#plan-week-nudge'].innerHTML,
              cardGo: cardGo && { hidden: cardGo.hidden, text: cardGo.text, attrs: cardGo.attrs } };
  if (cardGo && !cardGo.hidden && cardGo.handlers.click) cardGo.handlers.click();
  out.calls = calls;
  return out;
}
"""
)

_NEXT_WEEK = {"show": True, "week_start": "2026-10-19", "week_label": "Oct 19–25", "day_count": 7,
              "is_current_week": False, "dismiss_key": "plan_week_nudge:2026-10-19"}
_SHOP = {"id": "shop:1", "kind": "shop", "title": "Go shopping", "action": {"label": "Go shopping"}}


def _day(moves, week_state="set"):
    return {"date": "2026-10-16", "moves": moves, "featured": moves[0]["id"] if moves else None,
            "week_state": week_state, "tomorrow": None, "holiday": None}


def _run(moves, nudge, featured):
    res = nodeharness.run_node(
        _PRELUDE + "console.log(JSON.stringify(render(%s, %s, %s)));"
        % (json.dumps(moves), json.dumps(nudge), json.dumps(featured)),
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_dock_taken_by_another_action_puts_a_plan_button_on_the_card():
    """The repro: "Go shopping" owns the dock, the next-week offer is up.
    The card carries a visible, secondary (not apricot) "Plan next week",
    and tapping it starts planning the nudge's week, as the dock would."""
    out = _run(_day([_SHOP]), _NEXT_WEEK, _SHOP)
    assert "Go shopping" in out["dock"] and "plan-nudge-go" not in out["dock"]
    assert out["cardGo"] is not None, "the question needs a way to answer it on Today"
    assert out["cardGo"]["hidden"] is False
    assert out["cardGo"]["text"] == "Plan next week"
    assert "btn-secondary" in out["cardGo"]["attrs"] and "btn-primary" not in out["cardGo"]["attrs"]
    assert out["calls"] == [["plan", "2026-10-19", 7]]


@_needs_node
def test_dock_free_answers_the_offer_and_the_card_has_no_button():
    """Unchanged: nothing featured, the dock carries "Let's plan the week"
    and "Not now", and the card's own button stays hidden (no duplicate)."""
    out = _run(_day([], week_state="set"), _NEXT_WEEK, None)
    assert "plan-nudge-go" in out["dock"] and "plan-nudge-dismiss" in out["dock"]
    assert out["cardGo"] is None or out["cardGo"]["hidden"] is True
    assert out["calls"] == []


@_needs_node
def test_this_weeks_offer_names_this_week_and_uses_its_own_length():
    """Edge: the offer is for the CURRENT period (a five-day one). The card's
    button names it the way the Plan tab does and passes the nudge's own day_count, as the dock does."""
    nudge = dict(_NEXT_WEEK, is_current_week=True, week_start="2026-10-12", day_count=5)
    out = _run(_day([_SHOP], week_state="none"), nudge, _SHOP)
    assert out["cardGo"]["hidden"] is False
    assert out["cardGo"]["text"] == "Plan the next 5 days", "the Plan tab's name for the same tap"
    assert out["calls"] == [["plan", "2026-10-12", 5]]
