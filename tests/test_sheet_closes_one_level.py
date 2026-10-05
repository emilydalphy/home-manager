"""
Gowthami's household, 2026-10-04: "Everytime you click x on the top it exist
the whole app - need an x that's for the screen so it's cleaner."

Every sheet in this app already closed ITSELF. The bug was one level up:
opening a Settings section ran `closePrefsSheet()` first — the "one sheet at a
time" rule — so by the time the section's x was tapped, Settings had already
gone and that x was the last thing between the household and the tab.

What this file pins, running shell.js's OWN sheet-stack functions under node
(the card's two named criteria first):

  - Settings -> Your rhythm -> x lands on Settings, scrolled where it was;
  - x again on Settings lands on the tab;
  - the back GESTURE does exactly what the chevron does, including on a
    second and third visit (the popstate check compares the level's own id,
    not the stack's depth — a close never calls history.back(), so stale
    entries from earlier stacks are left behind at the same depth);
  - a child with a z-index of its own (`{ stays: true }`) leaves its parent
    on screen and does not reopen or re-scroll it;
  - the chevron and the crumb appear only when there IS a level above, which
    is what keeps a sheet opened from a tab exactly as it was.

Plus source-level guards over the whole audit, so a tenth sheet cannot be
added with its x wired to a close that skips the pop, and so no open path
goes back to closing its parent before opening its child.

RED AGAINST MAIN IS NOT A MEANINGFUL NUMBER FOR THIS FILE, so it is not
quoted: `_slice` reads the sheet-stack region by its own banner comment,
which does not exist on main, so the file is a COLLECTION ERROR there
(`ValueError: substring not found`, at module scope) and zero tests run.
The evidence is mutation instead — 18 of them, every one named in the
2026-10-05 Decision log entry and every one measured to bite. Four of the
first twelve bit NOTHING and two of those were the harness lying rather
than the code being safe: `El.innerHTML` was a plain property, so writing a
scroller left `scrollTop` where it was (a browser always zeroes it), which
made three scroll assertions unfailable; and the `stays` test used What we
know as the parent, a level with neither a `close` nor an `open` hook, so a
wrongful close AND a wrongful reopen were both no-ops. Both are fixed here;
read a green scroll assertion in this file as resting on `El.innerHTML`
being an accessor.
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
    b = SHELL_JS.index(end, a)
    return SHELL_JS[a:b]


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# Pulled straight from shell.js rather than retyped, the discipline every
# other harness in this repo follows, so an edit to either region cannot
# drift out of sync with what this file asserts.
_STACK = _slice(
    "  // ---------- One level at a time: the sheet stack (2026-10-05) ----------",
    "\n  // Tab panels crossfade with a small rise",
)
_POPSTATE = _slice(
    "  window.addEventListener('popstate', function (e) {",
    "\n  // ---------- The root band (every tab root) ----------",
)


# --------------------------------------------------------------------------
# A DOM small enough to read and real enough for the two things the chrome
# painter does: find a row by class, and insert a sibling after it.
# --------------------------------------------------------------------------
_PRELUDE = r"""
function El(opts) {
  opts = opts || {};
  this.id = opts.id || '';
  this.className = opts.className || '';
  this.kids = [];
  this.attrs = {};
  this.hidden = false;
  this.scrollTop = 0;
  this._html = '';
  this.textContent = '';
  this.type = '';
  this.listeners = {};
  this.parentNode = null;
}
// Writing innerHTML replaces the content, which in a real browser puts the
// scroller back at the top. Modelled, because three of this file's
// assertions are about a scroll position surviving a re-render, and with a
// plain property every one of them passed whether the code kept the place or
// threw it away (measured: four mutations bit nothing until this went in).
Object.defineProperty(El.prototype, 'innerHTML', {
  get: function () { return this._html; },
  set: function (v) { this._html = v; this.scrollTop = 0; }
});
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
El.prototype.addEventListener = function (k, f) { this.listeners[k] = f; };
El.prototype.click = function () { if (this.listeners.click) this.listeners.click({}); };
El.prototype.append = function (node) { node.parentNode = this; this.kids.push(node); return node; };
El.prototype.insertBefore = function (node, ref) {
  node.parentNode = this;
  var at = ref ? this.kids.indexOf(ref) : -1;
  if (at === -1) this.kids.push(node); else this.kids.splice(at, 0, node);
  return node;
};
Object.defineProperty(El.prototype, 'firstChild', {
  get: function () { return this.kids.length ? this.kids[0] : null; }
});
Object.defineProperty(El.prototype, 'nextSibling', {
  get: function () {
    if (!this.parentNode) return null;
    var at = this.parentNode.kids.indexOf(this);
    return (at > -1 && at + 1 < this.parentNode.kids.length) ? this.parentNode.kids[at + 1] : null;
  }
});
function matchesOne(el, sel) {
  sel = sel.trim();
  if (sel.charAt(0) === '.') return (' ' + el.className + ' ').indexOf(' ' + sel.slice(1) + ' ') > -1;
  if (sel.charAt(0) === '#') return el.id === sel.slice(1);
  return false;
}
El.prototype.matches = function (sel) {
  return sel.split(',').some(function (s) { return matchesOne(this, s); }, this);
};
El.prototype.querySelector = function (sel) {
  for (var i = 0; i < this.kids.length; i++) {
    if (this.kids[i].matches(sel)) return this.kids[i];
    var deeper = this.kids[i].querySelector(sel);
    if (deeper) return deeper;
  }
  return null;
};
El.prototype.closest = function (sel) {
  var at = this;
  while (at) { if (at.matches(sel)) return at; at = at.parentNode; }
  return null;
};

function sheet(id) {
  var el = new El({ id: id });
  var row = el.append(new El({ className: 'kit-sheet-titlerow' }));
  row.append(new El({ className: 'kit-sheet-title' }));
  row.append(new El({ className: 'kit-sheet-close' }));
  return el;
}

var PREFS = sheet('prefs-sheet');
var PREFS_ROWS = PREFS.append(new El({ id: 'prefs-rows' }));
var KIT = sheet('kit-sheet');
var WWK_BODY = KIT.append(new El({ id: 'wwk-body' }));
var UW = sheet('uw-sheet');
var RECIPES = sheet('recipes-sheet');
var RECIPES_BODY = RECIPES.append(new El({ id: 'recipes-body' }));
var TIPS = sheet('tips-sheet');
var TIPS_BODY = TIPS.append(new El({ className: 'tips-body' }));
var RLI = sheet('rli-sheet');
var RLI_BODY = RLI.append(new El({ id: 'rli-body' }));

var BY_ID = {
  'prefs-rows': PREFS_ROWS, 'wwk-body': WWK_BODY, 'recipes-body': RECIPES_BODY,
  'rli-body': RLI_BODY
};
global.document = {
  getElementById: function (id) { return BY_ID[id] || null; },
  querySelector: function (sel) {
    if (sel === '#tips-sheet .tips-body') return TIPS_BODY;
    return null;
  },
  createElement: function (tag) { return new El({ tag: tag }); }
};

// Which sheets are up, and what the stack's close/open hooks did.
var open = { prefs: false, kit: false, uw: false, recipes: false, tips: false };
var log = [];
// Each real opener renders its body before the stack restores the scroll
// position (openPrefsSheet -> renderPrefsRows, openRecipesSheet ->
// renderRecipesSheet), so the stubs write their scroller too.
function closePrefsSheet() { open.prefs = false; log.push('close prefs'); }
function openPrefsSheet() { open.prefs = true; log.push('open prefs'); PREFS_ROWS.innerHTML = '<p>Settings</p>'; }
function closeRecipesSheet() { open.recipes = false; log.push('close recipes'); }
function openRecipesSheet() { open.recipes = true; log.push('open recipes'); RECIPES_BODY.innerHTML = '<p>Recipes</p>'; }
function closeTipsSheet() { open.tips = false; log.push('close tips'); }
function openTipsSheet() { open.tips = true; log.push('open tips'); TIPS_BODY.innerHTML = '<p>Tips</p>'; }
function dismissTipsSheet() { closeTipsSheet(); popSheetLevelFor(dismissTipsSheet); }

function closeKitchenSheet() { open.kit = false; }
function dismissKitchenSheet() { closeKitchenSheet(); popSheetLevel(); }
function openSection(section, parent) {
  if (parent) openOverSheet(parent, dismissKitchenSheet);
  else forgetSheetLevels();
  open.kit = true;
  paintSheetLevelChrome(KIT, section);
}

// Settings -> Recipes, the one stack level whose parent has both a close and
// an open hook — so a `stays` child over it is where "left exactly where it
// was" can actually be measured.
function openRecipes(parent) {
  if (parent) openOverSheet(parent, dismissRecipesSheet);
  open.recipes = true;
  paintSheetLevelChrome(RECIPES, 'Recipes');
}
function dismissRecipesSheet() { closeRecipesSheet(); popSheetLevel(); }

// The kept page photo, over the recipe it came from (#rph-sheet has a
// z-index of its own, so the recipe stays on screen under it).
var photoUp = false;
function rphClose() { photoUp = false; popSheetLevel(); }
function openPhotoViewer(parent) {
  if (parent) openOverSheet(parent, rphClose, { stays: true });
  photoUp = true;
  paintSheetLevelChrome(RECIPES, 'The page');
}

// "Who's this?" — a full-screen takeover (inset:0, z-index 60, opaque
// --ground), opened from the Settings row that reads "You're Emily / Not
// you? Switch". Its single exit is closeWhoScreen, whichever of the three
// ways out ran ("Never mind", Escape, a successful pick).
var whoUp = false;
function closeWhoScreen() { whoUp = false; popSheetLevelFor(closeWhoScreen); }
function openWho(parent) {
  openOverSheet(parent, closeWhoScreen);
  whoUp = true;
}

function closeUwSheet() { open.uw = false; }
function dismissUwSheet() { closeUwSheet(); popSheetLevel(); }
function openUw(label) {
  openOverSheet('wwk', dismissUwSheet, { stays: true });
  open.uw = true;
  paintSheetLevelChrome(UW, label);
}

// The history the back gesture walks: one entry per pushState, and a
// cursor, so a pop hands back the state of the entry it lands on exactly
// as a browser does.
var entries = [{ tab: 'today' }];
var cursor = 0;
function currentTabKey() { return 'today'; }
global.window = {
  location: { pathname: '/' },
  history: {
    pushState: function (state) { entries = entries.slice(0, cursor + 1); entries.push(state); cursor = entries.length - 1; },
    replaceState: function (state) { entries[cursor] = state; }
  },
  addEventListener: function (k, f) { (global.popstateHandlers = global.popstateHandlers || {})[k] = f; }
};
function back() {
  if (cursor === 0) return false;
  cursor--;
  global.popstateHandlers.popstate({ state: entries[cursor] });
  return true;
}

// The rest of the popstate listener's world. None of it is what this file
// is about; it must simply not throw.
var askSheetHistoryPushed = false;
function closeAskSheet() {}
var tabActivations = 0;
function activateTab() { tabActivations++; }
function applyMealsStepFromHistory() {}
function applyGroceryStepFromHistory() {}

function snap() {
  var row = KIT.querySelector('.kit-sheet-titlerow');
  var crumbEl = KIT.querySelector('.kit-sheet-crumb');
  var backEl = row && row.querySelector('.kit-sheet-back');
  return {
    open: Object.assign({}, open),
    depth: sheetLevelDepth(),
    prefsScroll: PREFS_ROWS.scrollTop,
    label: sheetBackLabel(),
    crumb: crumbEl ? { text: crumbEl.textContent, hidden: !!crumbEl.hidden } : null,
    back: backEl ? { label: backEl.getAttribute('aria-label'), hidden: !!backEl.hidden } : null,
    tabActivations: tabActivations
  };
}
"""


def _script(body: str) -> str:
    return _PRELUDE + _STACK + _POPSTATE + body


# --------------------------------------------------------------------------
# The card's two named criteria
# --------------------------------------------------------------------------

@_needs_node
def test_settings_to_a_section_and_back_again_one_level_at_a_time():
    """The card's own test, word for word: Settings -> Your rhythm -> x lands
    on Settings; x again lands on the original tab."""
    out = _node(_script("""
openPrefsSheet();
PREFS_ROWS.scrollTop = 240;      // the household has scrolled down Settings
openSection('Your rhythm', 'prefs');
var onSection = snap();
dismissKitchenSheet();           // the SECTION's x
var afterFirstX = snap();
closePrefsSheet();               // Settings' own x — nothing left to pop
var afterSecondX = snap();
console.log(JSON.stringify({
  onSection: onSection, afterFirstX: afterFirstX, afterSecondX: afterSecondX,
  popWithNothingLeft: popSheetLevel()
}));
"""))
    # On the section: Settings is out of the way, one level is recorded.
    assert out["onSection"]["open"] == {"prefs": False, "kit": True, "uw": False,
                                        "recipes": False, "tips": False}
    assert out["onSection"]["depth"] == 1
    # Its x lands on SETTINGS, scrolled where it was — not on the tab.
    assert out["afterFirstX"]["open"]["kit"] is False
    assert out["afterFirstX"]["open"]["prefs"] is True, (
        "the section's x must land on Settings, which is the whole card"
    )
    assert out["afterFirstX"]["prefsScroll"] == 240, (
        "Settings has to come back scrolled where the household left it"
    )
    assert out["afterFirstX"]["depth"] == 0
    # And Settings' own x is the bottom of the stack: the tab.
    assert out["afterSecondX"]["open"]["prefs"] is False
    assert out["popWithNothingLeft"] is False, (
        "with no parent recorded, popSheetLevel answers false — which is how "
        "an x on a sheet opened from a tab still lands on the tab"
    )


@_needs_node
def test_a_section_opened_from_the_tab_still_closes_to_the_tab():
    """Cook's More sheet, a chat action's /memory href and Shop's store
    picker all open the same sheet with no parent. Unchanged by this card."""
    out = _node(_script("""
openSection('What we know', null);
var onSection = snap();
dismissKitchenSheet();
console.log(JSON.stringify({ onSection: onSection, after: snap() }));
"""))
    assert out["onSection"]["depth"] == 0
    assert out["onSection"]["back"]["hidden"] is True, (
        "no level above means no chevron — there is nowhere for it to go"
    )
    assert out["onSection"]["crumb"]["hidden"] is True
    assert out["after"]["open"]["prefs"] is False, (
        "a sheet opened from a tab must not reopen Settings on the way out"
    )


# --------------------------------------------------------------------------
# The back gesture
# --------------------------------------------------------------------------

@_needs_node
def test_the_back_gesture_does_what_the_chevron_does():
    out = _node(_script("""
openPrefsSheet();
PREFS_ROWS.scrollTop = 120;
openSection('Your rhythm', 'prefs');
var onSection = snap();
var moved = back();              // the phone's back gesture
console.log(JSON.stringify({ onSection: onSection, moved: moved, after: snap() }));
"""))
    assert out["moved"] is True
    assert out["after"]["open"] == {"prefs": True, "kit": False, "uw": False,
                                   "recipes": False, "tips": False}
    assert out["after"]["prefsScroll"] == 120
    assert out["after"]["depth"] == 0
    assert out["after"]["tabActivations"] == 0, (
        "the gesture must be spent on the sheet, not fall through to the tab"
    )


@_needs_node
def test_the_gesture_still_works_on_the_third_visit():
    """The id, not the depth. A close never calls history.back() — it just
    forgets the entry — so earlier visits leave stale entries behind at the
    same depth, and "is the state I am arriving at at least this deep" reads
    one of those as the entry we pushed and lets the gesture fall through to
    the tab."""
    out = _node(_script("""
var out = [];
for (var visit = 1; visit <= 3; visit++) {
  openPrefsSheet();
  openSection('Your rhythm', 'prefs');
  back();
  out.push({ visit: visit, prefs: open.prefs, kit: open.kit, depth: sheetLevelDepth(), tabs: tabActivations });
}
console.log(JSON.stringify(out));
"""))
    for row in out:
        assert row["kit"] is False and row["prefs"] is True, (
            f"visit {row['visit']}: the gesture closed the wrong thing — {row}"
        )
        assert row["depth"] == 0
        assert row["tabs"] == 0, f"visit {row['visit']}: the gesture reached the tab — {row}"


@_needs_node
def test_two_levels_deep_the_gesture_unwinds_one_at_a_time():
    out = _node(_script("""
openPrefsSheet();
openSection('What we know', 'prefs');
openUw('Dinners');
var deepest = snap();
back();
var middle = snap();
back();
var outer = snap();
console.log(JSON.stringify({ deepest: deepest, middle: middle, outer: outer }));
"""))
    assert out["deepest"]["depth"] == 2
    assert out["deepest"]["open"]["uw"] is True and out["deepest"]["open"]["kit"] is True
    # One level: the usual-week sheet goes, What we know is still there.
    assert out["middle"]["depth"] == 1
    assert out["middle"]["open"] == {"prefs": False, "kit": True, "uw": False,
                                    "recipes": False, "tips": False}
    # And the next one lands on Settings.
    assert out["outer"]["depth"] == 0
    assert out["outer"]["open"]["prefs"] is True and out["outer"]["open"]["kit"] is False


# --------------------------------------------------------------------------
# A child that genuinely stacks visually
# --------------------------------------------------------------------------

@_needs_node
def test_a_stays_child_leaves_its_parent_on_screen_and_does_not_reopen_it():
    """#rph-sheet carries its own z-index above the recipe it was opened from,
    so that recipe stays on screen under it. Closing the viewer must neither
    close the parent on the way in nor re-open and re-scroll it on the way
    out — it never moved, so there is nothing to put back.

    Over RECIPES rather than What we know because Recipes is the one stacked
    parent with both a close and an open hook: over a parent with neither,
    a wrongful close and a wrongful reopen are both no-ops and nothing here
    could fail."""
    out = _node(_script("""
openPrefsSheet();
openRecipes('prefs');
RECIPES_BODY.scrollTop = 310;
log.length = 0;
openPhotoViewer('recipes');
var up = { recipes: open.recipes, photo: photoUp, scroll: RECIPES_BODY.scrollTop,
           depth: sheetLevelDepth(), label: sheetBackLabel(), log: log.slice() };
// Something moved the recipe under the viewer (a late read, a tap). A
// wrongful reopen would put it back to 310.
RECIPES_BODY.scrollTop = 999;
log.length = 0;
rphClose();
console.log(JSON.stringify({
  up: up,
  after: { recipes: open.recipes, photo: photoUp, scroll: RECIPES_BODY.scrollTop,
           depth: sheetLevelDepth(), log: log.slice() }
}));
"""))
    assert out["up"]["recipes"] is True, "a stays child must not close its parent"
    assert out["up"]["log"] == [], "and must not re-render it either"
    assert out["up"]["scroll"] == 310
    assert out["up"]["label"] == "Recipes"
    assert out["up"]["depth"] == 2
    assert out["after"]["recipes"] is True and out["after"]["photo"] is False
    assert out["after"]["log"] == [], (
        "a parent that was left on screen must not be re-opened on the pop"
    )
    assert out["after"]["scroll"] == 999, (
        "nothing moved, so there is no remembered position to restore"
    )
    assert out["after"]["depth"] == 1, "and the level below it is untouched"


@_needs_node
def test_a_stays_child_over_what_we_know_reads_back_its_parents_name():
    """The usual-week sheet, the other `stays` child. Its parent has no close
    or open hook at all — the level is recorded only to give this sheet the
    chevron and the crumb."""
    out = _node(_script("""
openSection('What we know', null);
WWK_BODY.scrollTop = 310;
openUw('Dinners');
var up = { kit: open.kit, uw: open.uw, scroll: WWK_BODY.scrollTop, depth: sheetLevelDepth(), label: sheetBackLabel() };
dismissUwSheet();
console.log(JSON.stringify({
  up: up,
  after: { kit: open.kit, uw: open.uw, scroll: WWK_BODY.scrollTop, depth: sheetLevelDepth() }
}));
"""))
    assert out["up"]["kit"] is True
    assert out["up"]["scroll"] == 310
    assert out["up"]["label"] == "What we know"
    assert out["after"] == {"kit": True, "uw": False, "scroll": 310, "depth": 0}


# --------------------------------------------------------------------------
# The chrome: a chevron and a crumb, only when there is a level above
# --------------------------------------------------------------------------

@_needs_node
def test_the_stacked_header_names_where_it_goes():
    out = _node(_script("""
openPrefsSheet();
openSection('Your rhythm', 'prefs');
console.log(JSON.stringify(snap()));
"""))
    assert out["crumb"] == {"text": "Preferences › Your rhythm", "hidden": False}
    assert out["back"] == {"label": "Back to Preferences", "hidden": False}


@_needs_node
def test_the_chevron_is_the_same_call_as_the_x():
    """Tapping the chevron runs the child's OWN dismiss, so the control and
    the gesture are one call rather than two implementations of one rule."""
    out = _node(_script("""
openPrefsSheet();
PREFS_ROWS.scrollTop = 80;
openSection('Your rhythm', 'prefs');
KIT.querySelector('.kit-sheet-back').click();
console.log(JSON.stringify(snap()));
"""))
    assert out["open"]["prefs"] is True and out["open"]["kit"] is False
    assert out["prefsScroll"] == 80
    assert out["depth"] == 0


@_needs_node
def test_the_chrome_is_repainted_rather_than_rebuilt():
    """Opened from a tab after being opened from Settings, the same sheet has
    to lose its chevron and its crumb — they are created once and painted on
    every open, so a stale one would claim a level that is not there."""
    out = _node(_script("""
openPrefsSheet();
openSection('Your rhythm', 'prefs');
dismissKitchenSheet();
openSection('What we know', null);   // now from the tab
console.log(JSON.stringify(snap()));
"""))
    assert out["back"]["hidden"] is True
    assert out["crumb"]["hidden"] is True
    assert out["depth"] == 0


# --------------------------------------------------------------------------
# Leaving the stack behind
# --------------------------------------------------------------------------

@_needs_node
def test_a_sheet_reopened_from_a_tab_forgets_the_parents():
    """Nothing should reopen Settings later because of a stack built before
    the household left for a tab."""
    out = _node(_script("""
openPrefsSheet();
openSection('Your rhythm', 'prefs');
forgetSheetLevels();                 // what activateTab does
var afterForget = sheetLevelDepth();
dismissKitchenSheet();
console.log(JSON.stringify({ afterForget: afterForget, prefs: open.prefs, depth: sheetLevelDepth() }));
"""))
    assert out["afterForget"] == 0
    assert out["prefs"] is False
    assert out["depth"] == 0


@_needs_node
def test_which_sheet_a_control_sits_inside_decides_where_its_close_lands():
    """Read off the DOM, not off whichever sheet happens to be open — so the
    "Something not working?" tile in Settings opens the form on Settings
    while the same control in a tab's error paragraph opens it on the tab."""
    out = _node(_script("""
var inPrefs = PREFS_ROWS.append(new El({ className: 'snw-tile' }));
var inTips = TIPS_BODY.append(new El({ className: 'snw-tile' }));
var loose = new El({ className: 'snw-tile' });
console.log(JSON.stringify({
  inPrefs: sheetLevelHost(inPrefs),
  inTips: sheetLevelHost(inTips),
  loose: sheetLevelHost(loose),
  nothing: sheetLevelHost(null)
}));
"""))
    assert out == {"inPrefs": "prefs", "inTips": "tips", "loose": None, "nothing": None}


@_needs_node
def test_a_late_read_does_not_throw_away_the_place_it_was_restored_to():
    """Settings re-renders on every late read it is waiting on (the calendar,
    the morning text, what the chat is holding). Each of those replaced the
    scroller's innerHTML, which jumped the household back to the top — so the
    remembered position landed and was then lost."""
    out = _node(_script("""
PREFS_ROWS.scrollTop = 200;
writeKeepingPlace(PREFS_ROWS, '<p>re-rendered after a late read</p>');
var kept = PREFS_ROWS.scrollTop;
var fresh = new El({ id: 'fresh' });
writeKeepingPlace(fresh, '<p>first render</p>');
console.log(JSON.stringify({ kept: kept, html: PREFS_ROWS.innerHTML, fresh: fresh.scrollTop }));
"""))
    assert out["kept"] == 200
    assert out["html"] == "<p>re-rendered after a late read</p>"
    assert out["fresh"] == 0


# --------------------------------------------------------------------------
# Source guards over the audit
# --------------------------------------------------------------------------

def _strip_js_comments(js: str) -> str:
    """So a marker can never be satisfied by the prose explaining it — a trap
    this repo has recorded being caught by three times, and one this very
    file was caught by on its first run (the comment that says the section
    row "used to call closePrefsSheet()" matched the assertion that it no
    longer does)."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def _code(fn_name: str) -> str:
    """One function's body, comments stripped."""
    start = SHELL_JS.index("function " + fn_name + "(")
    depth = 0
    for i in range(SHELL_JS.index("{", start), len(SHELL_JS)):
        if SHELL_JS[i] == "{":
            depth += 1
        elif SHELL_JS[i] == "}":
            depth -= 1
            if depth == 0:
                body = SHELL_JS[start:i + 1]
                break
    return _strip_js_comments(body)


def test_the_settings_section_row_no_longer_closes_settings_first():
    """The bug itself, in one line: `data-prefs="section"` used to call
    closePrefsSheet() before opening the section."""
    handler = _strip_js_comments(SHELL_JS)
    handler = handler[handler.index("if (what === 'section') {"):]
    handler = handler[:handler.index("if (what === 'signout')")]
    assert "closePrefsSheet()" not in handler, (
        "opening a Settings section must not close Settings — that is the "
        "whole of Gowthami's report"
    )
    assert "openKitchenSheet('memory', target.getAttribute('data-section'), 'prefs')" in handler


def test_every_sheet_that_can_be_opened_from_another_has_a_dismiss_that_pops():
    """The audit, as a rule rather than a list: every sheet whose open
    function records a level must have exactly one way out, and that way out
    must pop the level. A tenth sheet added without one is the bug again."""
    dismissals = {
        "dismissKitchenSheet", "dismissUwSheet", "dismissRecipeLinkSheet",
        "rphClose", "closeAiConsentScreen", "dismissLeaveDialog",
        "dismissMorningSheet", "dismissRecipesSheet", "dismissTipsSheet",
        "dismissSnwSheet",
        # Full-screen takeovers rather than bottom sheets, so their own copy
        # is the named way back and they get no chevron — but their close
        # pops a level like every other.
        "closeWhoScreen",
    }
    pushed = set(re.findall(r"openOverSheet\([^,]+,\s*([A-Za-z_$][\w$]*)", SHELL_JS))
    # Every dismiss named above exists and pops.
    for name in sorted(dismissals):
        body = _code(name)
        assert "popSheetLevel" in body, (
            f"{name} is a sheet's own way out and does not pop its level — "
            "its x would land on the tab"
        )
    # And every function openOverSheet is actually handed is one of them.
    unknown = pushed - dismissals - {"dismiss"}
    assert not unknown, (
        f"openOverSheet is handed {sorted(unknown)}, which this file does not "
        "know to be a dismiss that pops. Add it to the list above (and check "
        "that it pops) or route the sheet through one that does."
    )


def test_the_one_sheet_at_a_time_closers_never_pop():
    """closePrefsSheet / closeKitchenSheet and the rest are what "one sheet at
    a time" calls to get a sheet out of the way — and openOverSheet itself
    calls one of them right after pushing. A pop inside one would undo the
    push it was pushed by."""
    for name in ("closePrefsSheet", "closeKitchenSheet", "closeRecipesSheet",
                 "closeTipsSheet", "closeMorningSheet", "closeSnwSheet",
                 "closeRecipeLinkSheet", "closeUwSheet", "closeLeaveDialog"):
        assert "popSheetLevel" not in _code(name), (
            f"{name} must not pop: openOverSheet calls it immediately after "
            "pushing the level, so a pop in there undoes the push"
        )


def test_the_popstate_check_is_on_the_levels_own_id():
    """Not the depth: a close never calls history.back(), so stale entries
    from earlier stacks sit at the same depth and a depth test reads one of
    them as the entry we pushed."""
    assert "e.state.sheetLevelId === sheetLevelTopId()" in SHELL_JS
    assert "sheetDepth" not in SHELL_JS, (
        "the old depth-based state key is gone; a mix of the two would make "
        "the gesture work on the first visit and not the third"
    )
    assert "sheetLevelId: sheetLevelSeq" in SHELL_JS


def test_the_leave_dialog_stays_over_settings_rather_than_closing_it():
    """#leave-dialog is z-index 51 and its scrim 50, both above
    #prefs-sheet's 41 — so Settings is dimmed under it, and closing it would
    show the tab through for the length of the dialog's own fetch."""
    handler = _strip_js_comments(SHELL_JS)
    handler = handler[handler.index("if (what === 'leave') {"):]
    handler = handler[:handler.index("openLeaveDialog();")]
    assert "openOverSheet('prefs', dismissLeaveDialog, { stays: true })" in handler, (
        "the leave dialog sits ON TOP of Settings; it must pass stays, or "
        "Settings slides away while GET /api/household/leave is in flight"
    )


def test_the_back_control_and_the_crumb_are_styled_and_hidable():
    """A rule that sets `display` beats the bare [hidden] attribute — the same
    trap .today-tile and .dinner-hero already carry a guard for."""
    assert re.search(r"\.kit-sheet-back\s*\{", SHELL_CSS)
    assert ".kit-sheet-back[hidden] { display: none; }" in SHELL_CSS
    assert ".kit-sheet-crumb[hidden] { display: none; }" in SHELL_CSS
    # 34px of ink in a 44px target (rule 6) — SIZED rather than inset, and
    # lifted above the title and the crumb that come after it in the row.
    # Both halves were measured by walking out from the centre a pixel at a
    # time: the house `inset: -5px` spelling gives 42 on a bordered 34px
    # control (an absolutely positioned child is laid out against the
    # padding box), and without the z-index the reach was 39.
    assert ".kit-sheet-close, .kit-sheet-back { position: relative; }" in SHELL_CSS
    rule = re.search(r"\.kit-sheet-close::after, \.kit-sheet-back::after \{([^}]*)\}", SHELL_CSS, re.S)
    assert rule, "static/shell.css has no ::after rule for the sheet header's two controls"
    body = rule.group(1)
    for want in ("width: 44px", "height: 44px", "top: 50%", "left: 50%",
                 "translate: -50% -50%", "z-index: 1"):
        assert want in body, (
            f"the 44px tap target needs `{want}` — measured, `inset: -5px` "
            "reaches 42 and no z-index reaches 39"
        )
    assert "inset: -5px" not in body


# --------------------------------------------------------------------------
# A pop closes the level it is the way out of, and nothing else
# --------------------------------------------------------------------------

@_needs_node
def test_who_is_this_opened_from_settings_comes_back_to_settings():
    """The one Settings control still closing Settings outright when this
    card's audit was run: "You're Emily / Not you? Switch"."""
    out = _node(_script("""
openPrefsSheet();
PREFS_ROWS.scrollTop = 60;
openWho('prefs');
var up = { prefs: open.prefs, who: whoUp, depth: sheetLevelDepth() };
closeWhoScreen();               // "Never mind"
console.log(JSON.stringify({
  up: up,
  after: { prefs: open.prefs, who: whoUp, depth: sheetLevelDepth(), scroll: PREFS_ROWS.scrollTop }
}));
"""))
    assert out["up"] == {"prefs": False, "who": True, "depth": 1}
    assert out["after"] == {"prefs": True, "who": False, "depth": 0, "scroll": 60}


@_needs_node
def test_a_pop_never_closes_a_sheet_it_was_not_standing_on():
    """The who screen is also reached from the leave dialog ("Who's asking?
    Pick your name first"), which pushes no level of its own for it — the
    level on the stack there is the DIALOG's. A bare pop would have closed
    the dialog's level and left Settings on screen with the dialog gone and
    nothing to come back to."""
    out = _node(_script("""
openPrefsSheet();
// Settings -> Delete your household: stays, because the dialog sits on top.
var leaveUp = false;
function dismissLeaveDialog() { leaveUp = false; popSheetLevelFor(dismissLeaveDialog); }
openOverSheet('prefs', dismissLeaveDialog, { stays: true });
leaveUp = true;
// "Pick my name first" — the dialog closes itself and sends the household
// to the who screen without pushing a level for it.
leaveUp = false;
openWho(null);
closeWhoScreen();
var afterWho = { depth: sheetLevelDepth(), prefs: open.prefs, label: sheetBackLabel() };
// The dialog's own level is still there, so Cancel still lands on Settings.
leaveUp = true;
dismissLeaveDialog();
console.log(JSON.stringify({ afterWho: afterWho, afterCancel: { depth: sheetLevelDepth(), prefs: open.prefs } }));
"""))
    assert out["afterWho"]["depth"] == 1, (
        "the who screen popped a level it was never standing on"
    )
    assert out["afterWho"]["label"] == "Preferences"
    assert out["afterWho"]["prefs"] is True, "Settings stayed under the dialog, as it should"
    assert out["afterCancel"]["depth"] == 0


@_needs_node
def test_a_sheet_opened_from_a_tab_mid_stack_pops_nothing():
    """Belt and braces for the invariant rather than a bug being fixed: the
    stack is forgotten on every tab change and every sheet's scrim covers
    the tab under it, so nothing reachable today opens one of these from a
    tab while a level is standing. If something ever does, its x has to
    land on the tab — not on whichever sheet happened to be on the stack."""
    out = _node(_script("""
openPrefsSheet();
openSection('Your rhythm', 'prefs');       // stack: [prefs -> the section]
var before = sheetLevelDepth();
openTipsSheet();                            // as if from a tab, no parent
dismissTipsSheet();
console.log(JSON.stringify({ before: before, after: sheetLevelDepth(), prefs: open.prefs }));
"""))
    assert out["before"] == 1
    assert out["after"] == 1, (
        "Helpful tips closed a level it never pushed — the section's own x "
        "would then have nothing left to land on"
    )
    assert out["prefs"] is False


def test_the_who_switch_row_no_longer_closes_settings_outright():
    handler = _strip_js_comments(SHELL_JS)
    handler = handler[handler.index("""closest('[data-who="switch"]')"""):]
    handler = handler[:handler.index("openWhoScreen(true)")]
    assert "closePrefsSheet()" not in handler, (
        "the last Settings control that closed Settings before opening its "
        "child — \"Never mind\" landed on the tab"
    )
    assert "openOverSheet(sheetLevelHost(target), closeWhoScreen)" in handler


def test_every_childs_own_way_out_pops_only_its_own_level():
    """popSheetLevel is "pop whatever is on top"; popSheetLevelFor is "pop
    the level I am the recorded way out of". Every sheet's own dismiss uses
    the second — the first is for the save paths, which land the household
    somewhere themselves."""
    for name in ("dismissKitchenSheet", "dismissUwSheet", "dismissRecipeLinkSheet",
                 "rphClose", "closeAiConsentScreen", "dismissLeaveDialog",
                 "dismissMorningSheet", "dismissRecipesSheet", "dismissTipsSheet",
                 "dismissSnwSheet", "closeWhoScreen"):
        body = _code(name)
        assert "popSheetLevelFor(" + name in body, (
            f"{name} pops whatever is on top rather than its own level"
        )
