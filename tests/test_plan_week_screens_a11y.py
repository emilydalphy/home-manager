"""
Plan a week's screens and onboarding's, driven by keyboard and read as a
screen reader would (overnight hunt, 2026-10-25, a real browser at 390x844
on a throwaway household). Every defect here was found on the running page:

1. Chips said nothing about being chosen. "On" was the .active class — a
   colour — so "Mexican, button" was all anyone heard, picked or not, on
   every chip row of onboarding and of Plan a week's Change screens.
2. Who's eating, and when? dropped the keyboard on <body> after every tap
   (the grid is redrawn whole), and a dinner's picker opened under the
   fixed foot, out of sight.
3. "Away for a few nights?" opened a modal with focus left on the card
   behind it, and its choices were colour-only too.
4. Onboarding's quiet "Skip" / "Not now" / "Nothing else" lines were spans:
   no keyboard could reach them, and a screen reader read them as text.
5. Return in "What's your name?" did nothing.

The page's own functions run under node (tests/nodeharness.py) with the
few DOM pieces they touch stubbed.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGE = (STATIC / "plan-week.html").read_text(encoding="utf-8")
ONBOARDING = (STATIC / "onboarding.html").read_text(encoding="utf-8")
SCREENS = (STATIC / "setup-screens.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")


def _fn(name: str, source: str) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start:j + 1]
        j += 1


def _const(name: str) -> str:
    m = re.search(rf"(?m)^const {name} = [^\n]*;\n", SCREENS)
    assert m, name
    return m.group(0)


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# A button as far as these functions look at one.
FAKE_EL = r"""
function el(attrs, cls) {
  var a = Object.assign({}, attrs || {});
  var classes = new Set((cls || '').split(' ').filter(Boolean));
  var e = {
    nodeType: 1, attrs: a, focused: false, scrolled: false, card: null, kids: [],
    classList: {
      contains: function (c) { return classes.has(c); },
      toggle: function (c, on) { if (on === undefined) on = !classes.has(c); if (on) classes.add(c); else classes.delete(c); return on; },
      add: function (c) { classes.add(c); }, remove: function (c) { classes.delete(c); },
    },
    hasAttribute: function (n) { return n in a; },
    getAttribute: function (n) { return n in a ? a[n] : null; },
    setAttribute: function (n, v) { a[n] = String(v); },
    matches: function (sel) { return sel === 'button.chip, button.rhythm-chip' && (classes.has('chip') || classes.has('rhythm-chip')); },
    querySelectorAll: function () { return e.kids; },
    closest: function (sel) { return sel === '[data-uw-card]' ? e.card : null; },
    focus: function () { e.focused = true; },
    scrollIntoView: function () { e.scrolled = true; },
  };
  return e;
}
"""


@_needs_node
def test_a_chip_says_whether_it_is_chosen_wherever_its_class_changes():
    script = FAKE_EL + _const("CHIP_TOGGLE") + "\n".join(
        _fn(n, SCREENS) for n in ("syncChipPressed", "sweepChipPressed", "watchChipPressed")
    ) + r"""
var observed = null;
function MutationObserver(cb) { this.observe = function (root, opts) { observed = { cb: cb, opts: opts }; }; }
var chosen = el({}, 'chip active'), plain = el({}, 'chip'), age = el({}, 'rhythm-chip');
var root = el({}, ''); root.kids = [chosen, plain];
watchChipPressed(root);
var first = [chosen.attrs['aria-pressed'], plain.attrs['aria-pressed']];
// A tap toggles the class (chipWithCustom's onclick, or any of the others).
plain.classList.toggle('active');
observed.cb([{ type: 'attributes', target: plain }]);
// A row drawn later (buildSingleSelectChips) arrives as added nodes.
var row = el({}, ''); row.kids = [age];
observed.cb([{ type: 'childList', addedNodes: [row] }]);
console.log(JSON.stringify({ first: first, tapped: plain.attrs['aria-pressed'], drawn: age.attrs['aria-pressed'],
  watches: observed.opts.attributeFilter }));
"""
    assert _node(script) == {"first": ["true", "false"], "tapped": "true", "drawn": "false", "watches": ["class"]}


@_needs_node
def test_plan_a_weeks_own_on_chips_are_left_to_say_their_own_pressed():
    """Plan a week's own chips mean chosen by .on and set aria-pressed
    themselves (paintChipSet); some are actions. The observer watches only
    #setup-host there — the .active screens — and the whole page only on
    onboarding, which has no host."""
    script = FAKE_EL + _fn("chipPressedRoot", SCREENS) + r"""
var host = el({}), html = el({});
var planWeek = { documentElement: html, getElementById: function (id) { return id === 'setup-host' ? host : null; } };
var onboarding = { documentElement: html, getElementById: function () { return null; } };
console.log(JSON.stringify([chipPressedRoot(planWeek) === host, chipPressedRoot(onboarding) === html]));
"""
    assert _node(script) == [True, True]
    # The host is in plan-week's markup before the script that looks for it.
    assert PAGE.index('<section id="setup-host"') < PAGE.index('<script src="/static/setup-screens.js">')
    assert 'id="setup-host"' not in ONBOARDING
    # And Plan a week's own convention is still its own: .on plus aria-pressed.
    paint = _fn("paintChipSet", PAGE)
    assert "classList.toggle('on', on)" in paint and "setAttribute('aria-pressed'" in paint


def _grid_harness(body: str) -> object:
    script = FAKE_EL + _const("UW_MEALS") + _const("UW_FOCUS_ATTRS") + "\n".join(
        _fn(n, SCREENS) for n in ("uwFocusSelector", "uwDaySelector", "renderMealsDays")
    ) + r"""
let uwShownOpen = null, uwOpen = null;
var usualGrid = { breakfast: [], lunch: [], dinner: [] };
function currentMembers() { return []; }
function uwCleanCell(c) { return c; }
function uwMealCardHtml() { return ''; }
function uwSummaryLine() { return ''; }
function uwTotalMeals() { return 1; }
var found = {};   // what the redrawn grid answers querySelector with
var grid = { innerHTML: '', contains: function () { return true; },
  querySelector: function (sel) { return found[sel] || null; } };
var sum = { textContent: '' };
var document = { activeElement: null,
  getElementById: function (id) { return id === 'uw-grid' ? grid : id === 'uw-sum' ? sum : null; } };
var dinnerCard = { dataset: { uwCard: 'dinner' } };
""" + body
    return _node(script)


@_needs_node
def test_the_grid_puts_focus_back_on_the_same_control_after_a_redraw():
    got = _grid_harness(r"""
var friday = el({ 'data-uw-day': '4' }); friday.card = dinnerCard;
document.activeElement = friday;
var fridayAgain = el({ 'data-uw-day': '4' }), pickerRow = el({});
found['[data-uw-card="dinner"] [data-uw-day="4"]'] = fridayAgain;
found['[data-uw-card="dinner"]'] = { lastElementChild: pickerRow };
uwOpen = { meal: 'dinner', i: 4 };
renderMealsDays();
console.log(JSON.stringify({ focused: fridayAgain.focused, scrolled: pickerRow.scrolled }));
""")
    assert got == {"focused": True, "scrolled": True}


@_needs_node
def test_a_pick_that_closes_the_picker_hands_focus_to_its_day():
    got = _grid_harness(r"""
uwShownOpen = { meal: 'dinner', i: 4 };
var pick = el({ 'data-uw-pick': 'only:Emily' }); pick.card = dinnerCard;
document.activeElement = pick;
var friday = el({ 'data-uw-day': '4' });
found['[data-uw-card="dinner"] [data-uw-day="4"]'] = friday;
uwOpen = null;              // the pick closed it
renderMealsDays();
console.log(JSON.stringify({ focused: friday.focused, sel: uwFocusSelector(pick) }));
""")
    assert got == {"focused": True, "sel": '[data-uw-card="dinner"] [data-uw-pick="only:Emily"]'}


def test_a_picker_scrolled_to_clears_the_fixed_foot():
    assert re.search(r"\.uw-row \{ scroll-margin-bottom: \d+px; \}", ONBOARDING)


@_needs_node
def test_the_away_sheet_takes_focus_in_and_gives_it_back():
    script = FAKE_EL + "\n".join(_fn(n, PAGE) for n in ("openAwaySheet", "closeAwaySheet", "pressOn")) + r"""
var gate = el({}); gate.offsetParent = {};
var closeBtn = el({});
var sheet = { hidden: true, contains: function (x) { return x === closeBtn; } };
var els = { 'away-sheet': sheet, 'away-close': closeBtn, 'away-gate': gate };
function $(id) { return els[id]; }
var document = { activeElement: gate, body: { style: {} }, contains: function () { return true; } };
var awayOpener = null, awayDraft = null;
function plannedDays() { return [{ date: '2026-11-02' }, { date: '2026-11-03' }]; }
function buildAwaySheet() {}
openAwaySheet();
var inSheet = closeBtn.focused;
document.activeElement = closeBtn;
openAwaySheet();            // "Add another range", from inside the sheet
closeAwaySheet();
var opt = el({}, 'opt'); pressOn(opt, true);
console.log(JSON.stringify({ inSheet: inSheet, back: gate.focused, hidden: sheet.hidden,
  pressed: opt.attrs['aria-pressed'], on: opt.classList.contains('on') }));
"""
    assert _node(script) == {"inSheet": True, "back": True, "hidden": True, "pressed": "true", "on": True}
    paint = _fn("paintAwaySheet", PAGE)
    assert "classList.toggle('on'" not in paint, "every away choice goes through pressOn"
    assert paint.count("pressOn(b, ") == 5


def test_the_quiet_lines_are_buttons_a_keyboard_can_reach():
    for quiet_id in ("who-cooks-skip", "dinner-time-skip", "shop-day-skip",
                     "kit-repeats-skip", "ai-consent-later", "anything-else-quiet"):
        assert re.search(rf'<button type="button" class="[^"]*" id="{quiet_id}">', ONBOARDING), quiet_id
    assert not re.search(r'<span class="(skip-link|q-skip-quiet)"', ONBOARDING)
    # A busy line is disabled as well as greyed: Enter on a focused button
    # still clicks it, where pointer-events: none only stopped a finger.
    assert "quiet.disabled = anythingElseBusy;" in ONBOARDING
    assert "skip.disabled = busy;" in ONBOARDING
    assert "later.disabled = busy;" in ONBOARDING


def test_return_in_the_name_box_is_next():
    m = re.search(r"getElementById\('your-name-input'\)\.addEventListener\('keydown', \(e\) => \{([\s\S]*?)\n\}\);",
                  ONBOARDING)
    assert m, "no keydown on the name box"
    assert "e.isComposing" in m.group(1)
    assert "getElementById('your-name-next').click()" in m.group(1)
