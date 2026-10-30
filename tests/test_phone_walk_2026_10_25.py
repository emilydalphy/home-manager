"""
Four small defects found by USING the app at 390x844 (the overnight phone
walk, 2026-10-25: sign in -> Now -> Plan -> Shop -> Cook -> Settings in a
real browser on a throwaway database), each pinned here:

  1. A toast covered the dock's one button. "Logged — that'll steer next
     week. Rate it" sat on top of Cook's "Start cooking"; "lemons was added
     Undo" on Shop's "Add something". showToast already lifted itself over
     cook mode's dock; now over any tab's dock at the foot (toastDockBelow).
  2. The toast's own action ("Undo", "Rate it") was a 34x17 tap target.
  3. Shop's "Somewhere else? ... Add" was 33px wide.
  4. Escape closed Settings and everything reached from it, but none of the
     ten sheets a tab opens directly — on the desktop "phone in the room",
     Cook's More had no keyboard way out at all.
  5. The Meal step's dock ("Done — on the table") sat mid-screen on a short
     page, the bug is-chores and is-review-layout already fixed for theirs.

The behaviour (1, 4) runs shell.js's own functions under node, the house
standard; the CSS (2, 3, 5) is read off the stylesheet because node has no
layout — the drive that measured them is in the Decision log entry.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the shell's own functions",
)


def _slice(start: str, end: str) -> str:
    a = SHELL_JS.index(start)
    return SHELL_JS[a:SHELL_JS.index(end, a)]


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# ---------------------------------------------------------------------------
# 1. The toast sits above a tab's dock
# ---------------------------------------------------------------------------

_TOAST = _slice("  // The dock at the foot of the tab on screen", "  // Api.json's failure toast")

_TOAST_PRELUDE = r"""
function Cls(){ this.s = {}; }
Cls.prototype.add = function(c){ this.s[c] = 1; };
Cls.prototype.remove = function(c){ delete this.s[c]; };
Cls.prototype.contains = function(c){ return !!this.s[c]; };
Cls.prototype.toggle = function(c, on){ if (on) this.add(c); else this.remove(c); };
function El(top, h){ this.top = top; this.h = h || 75; this.style = {}; this.classList = new Cls(); this.children = [];
  this.offsetParent = {}; this.offsetWidth = 1; this.hidden = true; }
El.prototype.getBoundingClientRect = function(){ return { top: this.top, bottom: this.top + this.h }; };
El.prototype.appendChild = function(c){ this.children.push(c); };
El.prototype.addEventListener = function(){};
El.prototype.setAttribute = function(){};
var STATE = { scrimOpen: false, cookDock: null, docks: [] };
var panel = { querySelectorAll: function(sel){ return sel === '.dock' ? STATE.docks : []; } };
var document = {
  createElement: function(){ return new El(0); },
  createTextNode: function(t){ return { text: t }; },
  getElementById: function(id){ return id === 'shell-scroll' ? { getBoundingClientRect: function(){ return { bottom: 761 }; } } : null; },
  querySelector: function(sel){
    if (sel === '[id$="scrim"].is-open') return STATE.scrimOpen ? {} : null;
    if (sel === '.cook-focus .cook-dock') return STATE.cookDock;
    if (sel === '.tab-panel.active') return panel;
    return null;
  }
};
var window = { innerHeight: 844 };
var toastEl = new El(0), toastTimer = null, askSheet = null;
function setTimeout(){ return 1; } function clearTimeout(){}
"""


def _toast_bottom(**state) -> str:
    script = _TOAST_PRELUDE + _TOAST + r"""
var s = %s;
if (s.docks) STATE.docks = s.docks.map(function(p){ var d = new El(Math.abs(p[0]), p[1]); if (p[0] < 0) d.offsetParent = null; return d; });
STATE.scrimOpen = !!s.scrimOpen;
if (s.cookDock) STATE.cookDock = new El(s.cookDock);
showToast('lemons was added', { label: 'Undo' });
console.log(JSON.stringify(toastEl.style.bottom || ''));
""" % json.dumps(state)
    return _node(script)


@_needs_node
def test_a_toast_sits_above_the_tab_dock_rather_than_on_its_button():
    # Shop's dock measured stuck at the foot: top 628, 133 tall, its bottom
    # the scroller's (761, where the tab bar starts) on a 844px phone.
    assert _toast_bottom(docks=[[628, 133]]) == f"{844 - 628 + 12}px"


@_needs_node
def test_a_dock_a_short_page_left_mid_screen_does_not_throw_the_toast_up():
    # The approved Plan root, measured: .dock is sticky, so on a short page
    # it sits after the content (541..616) — in the lower half, not at the
    # foot. Lifting over it put the toast on the freezer row (review, 2026-10-25).
    assert _toast_bottom(docks=[[541, 75]]) == ""
    assert _toast_bottom(docks=[[300, 75]]) == ""


@_needs_node
def test_a_hidden_dock_is_passed_over_for_the_one_on_screen():
    # A negative top here stands for offsetParent === null (display: none).
    assert _toast_bottom(docks=[[-686, 75], [686, 75]]) == f"{844 - 686 + 12}px"


@_needs_node
def test_with_a_sheet_up_the_toast_keeps_its_usual_spot():
    assert _toast_bottom(docks=[[686, 75]], scrimOpen=True) == ""


@_needs_node
def test_cook_modes_own_dock_still_wins():
    assert _toast_bottom(docks=[[686, 75]], cookDock=640) == f"{844 - 640 + 12}px"


# ---------------------------------------------------------------------------
# 4. Escape closes the sheet a tab opened
# ---------------------------------------------------------------------------

_ESC = _slice("  // Escape for the tab-level sheets", "  // ---------- One level at a time")


def _escape(open_ids, key="Escape", prevented=False, typed=None, opener=True):
    script = r"""
var clicked = [], focused = [];
var listeners = [];
function Scrim(id, open){ this.id = id; this.hidden = !open; this.classList = { contains: function(c){ return c === 'is-open' && open; } }; }
Scrim.prototype.click = function(){ clicked.push(this.id); };
var OPEN = %s, TYPED = %s;
var opener = { isConnected: true, focus: function(){ focused.push('opener'); }, closest: function(){ return null; } };
var field = { tagName: 'INPUT', value: TYPED || '', closest: function(sel){ return sel === '[role="dialog"]' ? {} : null; } };
var document = {
  activeElement: TYPED === null ? null : field,
  getElementById: function(id){ return new Scrim(id, OPEN.indexOf(id) !== -1); },
  addEventListener: function(type, fn){ listeners.push([type, fn]); }
};
""" % (json.dumps(open_ids), json.dumps(typed)) + _ESC + r"""
if (%s) listeners.forEach(function(l){ if (l[0] === 'focusin') l[1]({ target: opener }); });
listeners.forEach(function(l){ if (l[0] === 'keydown') l[1]({ key: %s, defaultPrevented: %s }); });
console.log(JSON.stringify({ clicked: clicked, focused: focused }));
""" % (json.dumps(opener), json.dumps(key), json.dumps(prevented))
    return _node(script)


@_needs_node
def test_escape_closes_cooks_more_the_way_a_tap_outside_does():
    out = _escape(["cook-more-scrim"])
    assert out["clicked"] == ["cook-more-scrim"]
    # ...and the keyboard lands back on what opened it.
    assert out["focused"] == ["opener"]


@_needs_node
def test_escape_closes_only_the_innermost_of_two():
    # The swap, opened from the whole-week sheet, goes first; the week stays.
    assert _escape(["week-sheet-scrim", "wk-swap-scrim"])["clicked"] == ["wk-swap-scrim"]


@_needs_node
def test_another_key_or_an_escape_already_handled_closes_nothing():
    assert _escape(["cook-more-scrim"], key="Enter")["clicked"] == []
    assert _escape(["cook-more-scrim"], prevented=True)["clicked"] == []


@_needs_node
def test_escape_in_a_field_with_words_in_it_keeps_the_sheet_and_the_words():
    # Shop's "Add something" forgets its draft on close (review, 2026-10-25).
    assert _escape(["gro-add-scrim"], typed="oat milk")["clicked"] == []
    # An empty field is no draft: Escape still closes.
    assert _escape(["gro-add-scrim"], typed="  ")["clicked"] == ["gro-add-scrim"]


def test_every_listed_scrim_is_a_real_scrim_the_shell_has():
    # The list is ids, so a renamed scrim would fail silently: check each.
    html = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
    listed = re.search(r"ESCAPE_CLOSES_SCRIMS = \[(.*?)\]", _ESC, re.S).group(1)
    ids = re.findall(r"'([a-z-]+-scrim)'", listed)
    # + the Kitchen tab's item sheet (2026-10-26), + the quiet sheet a row
    # opens on Plan, Shop and Today (2026-10-30)
    assert len(ids) == 12
    for sid in ids:
        assert f'id="{sid}"' in html or f"id = '{sid}'" in SHELL_JS, sid


# ---------------------------------------------------------------------------
# 2, 3, 5. Tap targets and the Meal step's dock (CSS)
# ---------------------------------------------------------------------------

def test_the_toast_action_reaches_44px_without_growing_the_toast():
    m = re.search(r"\.toast-action::before \{[^}]*inset: -(\d+)px -(\d+)px;", SHELL_CSS)
    assert m, "the toast action needs its hit area"
    v, h = int(m.group(1)), int(m.group(2))
    # 14px/1.2-ish line = 17px measured; "Undo" 34px wide, measured.
    assert 17 + 2 * v >= 44 and 34 + 2 * h >= 44
    assert re.search(r"\.toast-action \{ position: relative; \}", SHELL_CSS)


def test_the_somewhere_else_add_is_44px_wide():
    rule = re.search(r"\.gro-stores-prompt-add \.gro-linkbtn \{([^}]*)\}", SHELL_CSS).group(1)
    assert "min-width: 44px" in rule and "min-height: 44px" in rule


def test_the_meal_step_grows_so_its_dock_reaches_the_foot():
    assert ".tab-panel.is-meal-layout #week-plan-view { flex: 1 0 auto; }" in SHELL_CSS
    assert "panel.classList.toggle('is-meal-layout', weekState.step === 'meal');" in SHELL_JS
    # The dock it is for is the one #week-steps already pushes down.
    assert "#week-steps .wk-decide.dock { margin-top: auto; }" in SHELL_CSS
    assert "'<div class=\"wk-decide dock wk-meal-dock\">'" in SHELL_JS
