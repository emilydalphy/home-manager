"""
Ticking a meal on Today or Cook left Plan showing it not cooked; picking a
shop day from Today's prompt left the prompt up (defect hunt 2026-10-07,
Medium).

Tab panels build once (CLAUDE.md, "Tab panels build once per page load"),
so a write made on one tab has to tell the others. Today's tick told Cook
only (refreshKitchenPanel), Cook's ticks told Today and Cook
(refreshPlanSurfacesAfterCook) — neither told Plan, whose "Done" reads the
same rows. And the shop day, saved from What we know's rhythm section that
Today's "No shopping day set. Pick one" opens, never re-read Today.

Run against shell.js's own functions under node, with the network and the
renderers stubbed.
"""
from __future__ import annotations

import shutil

import pytest

from test_plan_cards_2026_09_18 import _run
from test_week_seven_tiles import _extract, _extract_async
from test_plan_next_on_draft import SHELL_JS

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the functions")

_STUBS = """
var CALLS = [];
var panels = { week: { dataset: { built: '1' } } };
function loadWeekMenu(p, opts) { CALLS.push(['week', !!(opts && opts.kitchenFresh)]); }
function refreshTodayMoves() { CALLS.push(['today']); }
function refreshKitchenMoves() { CALLS.push(['kitchen-moves']); }
function refreshKitchenPanel() { CALLS.push(['kitchen']); }
"""


@_needs_node
def test_a_cook_tick_refreshes_plan_without_reloading_cook_under_the_cook():
    """CATCH."""
    out = _run(_STUBS + _extract("refreshWeekPanel", SHELL_JS) + "\n"
               + _extract("refreshPlanSurfacesAfterCook", SHELL_JS) + "\n"
               + "refreshPlanSurfacesAfterCook();\n"
               + "panels.week.dataset.built = '';\n"
               + "refreshPlanSurfacesAfterCook();\n"
               + "console.log(JSON.stringify(CALLS));")
    assert out == [["today"], ["kitchen-moves"], ["week", True],
                   ["today"], ["kitchen-moves"]], "Plan re-read once built, and never before"


@_needs_node
def test_a_today_tick_refreshes_plan():
    """CATCH. The whole tick, server answer included."""
    out = _run(_STUBS + _extract("refreshWeekPanel", SHELL_JS) + "\n"
               + _extract_async("toggleTodayMove", SHELL_JS) + "\n"
               + """
var fresh = { moves: [{ id: 'm1', done: true, title: 'Chicken Stir Fry' }] };
var Api = { fetch: async function () { return { ok: true, json: async function () { return fresh; } }; } };
function renderTodayMoves() {}
function todayAnimateNodeSettle() {}
function savedName(s) { return s; }
function savedLine(n, v) { return n + ' ' + v; }
function showToast(t) { CALLS.push(['toast', t]); }
var panel = { _moves: { moves: [{ id: 'm1', done: false, title: 'Chicken Stir Fry' }] }, _featured: null };
toggleTodayMove(panel, 'm1', true).then(function () { console.log(JSON.stringify(CALLS)); });
""")
    assert ["kitchen"] in out
    assert ["week", True] in out, "Plan's Done reads the same row"


def test_the_kitchen_fresh_option_is_what_loadweekmenu_honours():
    body = _extract("loadWeekMenu", SHELL_JS)
    assert "if (!(opts && opts.kitchenFresh)) refreshKitchenPanel();" in body


@_needs_node
def test_a_saved_rhythm_re_reads_today_and_a_failed_one_does_not():
    """CATCH. The shop day picked from Today's "Pick one" is a rhythm save."""
    out = _run(_STUBS + _extract("wwkSaveRhythm", SHELL_JS) + "\n"
               + """
var OK = true;
function wwkPost() {}
function wwkAdoptRhythm() {}
async function wwkCommit() { return OK; }
(async function () {
  var a = await wwkSaveRhythm('rhythm', { shop_day: 'saturday' }, function () {});
  OK = false;
  var b = await wwkSaveRhythm('rhythm', { shop_day: 'saturday' }, function () {});
  console.log(JSON.stringify({ a: a, b: b, calls: CALLS }));
})();
""")
    assert out == {"a": True, "b": False, "calls": [["today"]]}


# ---------- review, 2026-10-07: a background refresh never breaks Plan ----------

_LOAD_PRELUDE = """
var weekState = { showWeekStart: null, cookView: null };
var PAINTED = [];
async function loadPlanningPeriodDefault() {}
function todayLocalStr() { return '2026-10-07'; }
function renderWeekMenu(p, d) { PAINTED.push(d.n); }
function refreshKitchenPanel() {}
async function planIdForWeek() { return null; }
var planningPeriodDefault = null, planningPeriodFetchedOn = '';
var steps = { innerHTML: 'the week' };
var panel = { querySelector: function () { return steps; } };
"""


def _load_week_menu() -> str:
    return "var weekMenuSeq = 0;\nasync " + _extract("loadWeekMenu", SHELL_JS) + "\n"


@_needs_node
def test_a_quiet_refresh_that_fails_keeps_the_week_on_screen():
    """CATCH. A tick's background refresh failing used to paint "Couldn't
    load your week right now." over a Plan nobody was reloading."""
    out = _run(_LOAD_PRELUDE + _load_week_menu() + """
var Api = { fetch: async function () { return { ok: false }; } };
(async function () {
  await loadWeekMenu(panel, { quiet: true, kitchenFresh: true });
  var quiet = steps.innerHTML;
  await loadWeekMenu(panel);
  console.log(JSON.stringify({ quiet: quiet, loud: steps.innerHTML }));
})();
""")
    assert out["quiet"] == "the week"
    assert "Couldn't load your week" in out["loud"], "a load someone asked for still says so"


def test_refresh_week_panel_is_quiet():
    assert "loadWeekMenu(panels.week, Object.assign({ quiet: true }, opts));" in _extract("refreshWeekPanel", SHELL_JS)


@_needs_node
def test_an_older_reply_never_paints_over_a_newer_one():
    """CATCH. The first load's reply arrives last; only the second paints."""
    out = _run(_LOAD_PRELUDE + _load_week_menu() + """
var resolvers = [];
var Api = { fetch: function () { return new Promise(function (r) { resolvers.push(r); }); } };
function reply(n) { return { ok: true, json: async function () { return { n: n }; } }; }
(async function () {
  var first = loadWeekMenu(panel, { quiet: true });
  await new Promise(function (r) { setTimeout(r, 0); });
  var second = loadWeekMenu(panel);
  await new Promise(function (r) { setTimeout(r, 0); });
  resolvers[1](reply(2));
  await second;
  resolvers[0](reply(1));
  await first;
  console.log(JSON.stringify(PAINTED));
})();
""")
    assert out == [2]
