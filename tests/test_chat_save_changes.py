"""
The pinned "Save N changes to the week" (card 8, 2026-10-05).

Gowthami's household, 2026-10-04: "Has to click save changes, but it's not
obvious, kind of hidden for after the draft is done. Make it clearer in the
design."

The only Save was a quiet spruce button at the FOOT of the chat change
card, so a household could ask chat to change the draft, read a card
describing the change, close the sheet, and believe the week had changed
when nothing was written. That last sentence is the bug, and it is a
DATA-HONESTY one rather than a styling one: this app has a standing rule
that it never tells a household something untrue (DESIGN_SYSTEM §8), and
there is already a Decision log entry (2026-09-08) about a chat reply
claiming a change that had not landed — agent.verify_change_claim and
CHANGE_CLAIM_RETRACTION exist because of it. A silent unsaved card is the
same failure one screen out.

So: the Ask sheet's dock carries an apricot "Save N changes to the week"
and a soft "Keep the week as it was" while a card has changes waiting, a
line above them says what the week still shows, the card's own buttons are
gone, and all THREE closing doors — the Back button, the scrim/handle and
the back GESTURE — ask inside the app first.

THE COUNT IS ONE READ. changeCardPending() is the predicate `canSave` has
always been, given a name, and the dock's count, the dialog's question,
the "Not saved yet" line and the toast are all built from it. A count that
disagrees with what lands is worse than no count.

THIRTEEN MUTATIONS RUN, every one biting, with the red counts read off
the runs over this file (25 tests, baseline 25 passed):

   1. the dock never renders while unsaved, i.e. main's behaviour  -> 14
   2. the count narrowed to one row whatever is waiting            ->  7
   3. the Back button's guard removed (door 1)                     ->  1
   4. the scrim's guard removed (door 2)                           ->  1
   5. the back GESTURE's guard removed (door 3)                    ->  1
   6. "Don't save" writes anyway                                   ->  1
   7. the dialog's Save does not write                             ->  1
   8. the "Not saved yet" line dropped from the dock               ->  2
   9. the card's own Save and Leave left in place                  ->  1
  10. "Another" no longer holds the dock's Save                    ->  1
  11. the toast built off the card's rows, not what landed         ->  2
  12. the dock's Save swallows its own promise                     ->  4
  13. the dialog markup loses its container id                     ->  1

READ 3, 4 AND 5 FOR WHAT THEY ARE. Each reddens exactly ONE test, and it
is a source marker: there is no real DOM under node, so a tap on the
scrim or a back gesture is not reachable here. What IS driven for real is
the thing behind all three doors — askSheetCloseRequested asking instead
of closing, and the dialog's two answers — by five behavioural tests
(2 + 2 + 1). So the doors are pinned by their wiring and the guard by its
behaviour, which is the honest division rather than a claim that a
harness tapped a scrim.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import proposals as prop

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)

TODAY = household_today()
WEEK_START = TODAY.isoformat()
DAYS = [(datetime.date.fromisoformat(WEEK_START) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)]
DAY1, DAY2, DAY3, DAY4 = DAYS[0], DAYS[1], DAYS[2], DAYS[3]


def _slice(start: str, end: str) -> str:
    a = SHELL_JS.index(start)
    return SHELL_JS[a:SHELL_JS.index(end, a)]


# The sheet's own functions, run under node — that is how this repo tests
# shell.js, and the risk on this card is a GUARD THAT DOES NOTHING, which a
# source-marker test cannot see.
_PRELUDE = """
let TOASTS = [];
let POSTED = [];
let DIALOG = { hidden: true, title: '', note: '' };
let SHEET = { closed: 0 };
let WEEK_RELOADED = 0;
function escapeHtml(s) { return String(s === null || s === undefined ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function showToast(m) { TOASTS.push({ msg: m }); }
function toastSaved(m, undo, ms) { TOASTS.push({ msg: m, undo: !!undo, ms: ms }); }
function dayName(iso, o) { return ({'%(d1)s':'Monday','%(d2)s':'Tuesday','%(d3)s':'Wednesday','%(d4)s':'Thursday'})[iso] || iso; }
function slotWord(s) { return s === 'lunch' ? 'Lunch' : (s === 'breakfast' ? 'Breakfast' : 'Dinner'); }
// S10's shared confirmation builder, verbatim from shell.js: the
// toast's wording is the one the in-card Save already shipped.
var SAVED_PLAIN = 'Changes saved';
function savedName(t) { return t ? String(t) : ''; }
function savedLine(thing, verbed) {
  var name = savedName(thing);
  if (!name) return SAVED_PLAIN;
  return name + ' was ' + (verbed || 'saved');
}
function markRecentlyChanged() {}
function refreshDishIndex() {}
function loadWeekMenu() { WEEK_RELOADED++; }
var panels = { week: null };
var SWAP_UNDO_MS = 8000;
var APPLY = null;   // the answer /apply gives, set per test
function postJson(url, body) {
  POSTED.push(url);
  if (url.indexOf('/apply') >= 0) {
    return APPLY === 'reject'
      ? Promise.reject(new Error('That didn’t save — try again.'))
      : Promise.resolve(APPLY);
  }
  return Promise.resolve(null);
}
// The dock element, and the dialog, as the document holds them.
var DOCK = { hidden: true, innerHTML: '', _wired: {},
  querySelector: function (sel) {
    var m = /\\[(data-[a-z-]+)\\]/.exec(sel);
    if (!m) return null;
    if (this.innerHTML.indexOf(m[1]) < 0) return null;
    var key = m[1], self = this;
    return { addEventListener: function (ev, fn) { self._wired[key] = fn; } };
  } };
// closeSheet/openSheet set .hidden on whatever element they are handed,
// exactly as the real pair does, so askSaveDialogIsDown() is the real
// predicate here rather than a stub that always agrees.
function closeSheet(el) { if (el) el.hidden = true; }
function openSheet(el) { if (el) el.hidden = false; }
function closeAskSheet() { SHEET.closed++; }
// Every element the dialog's own wiring touches is a fake that COLLECTS
// its handler, because the wiring block (`if (askSaveScrim) { ... }`) runs
// at module load and the mutations this file exists for are inside those
// handlers — Don't-save writing anyway, Save not writing. A bare {} here
// is a TypeError at load; a no-op addEventListener would make both of
// those mutations invisible.
let WIRED = {};
function fakeEl(id) {
  return {
    id: id,
    focus: function () {},
    addEventListener: function (ev, fn) { WIRED[id + ':' + ev] = fn; },
  };
}
var askSaveDialog = DIALOG;
var askSaveScrim = fakeEl('ask-save-scrim');

var ELS = {
  'ask-save-dock': DOCK,
  'ask-save-title': { set textContent(v) { DIALOG.title = v; }, get textContent() { return DIALOG.title; } },
  'ask-save-dialog-note': { set textContent(v) { DIALOG.note = v; }, get textContent() { return DIALOG.note; } },
  'ask-save-do': fakeEl('ask-save-do'),
  'ask-save-dont': fakeEl('ask-save-dont'),
};
var document = {
  getElementById: function (id) { return ELS[id] || null; },
  addEventListener: function (ev, fn) { WIRED['document:' + ev] = fn; },
  createElement: function () { return { className: '', innerHTML: '', appendChild: function () {},
    querySelectorAll: function () { return []; } }; },
};
// --- the dock's own clicks, driven by name ---
function tapDock(which) {
  var fn = DOCK._wired['data-ask-save-' + which];
  if (!fn) throw new Error('no ' + which + ' in the dock: ' + DOCK.innerHTML);
  return fn();
}
// --- the dialog's own clicks ---
function tapDialog(which) {
  var fn = WIRED['ask-save-' + which + ':click'];
  if (!fn) throw new Error('no ' + which + ' wired on the dialog');
  return fn();
}
function pressEscape() {
  var fn = WIRED['document:keydown'];
  if (!fn) throw new Error('no keydown wired');
  return fn({ key: 'Escape' });
}
""" % {"d1": DAY1, "d2": DAY2, "d3": DAY3, "d4": DAY4}


def _region() -> str:
    """changeCardPending through saveChangeCard, plus the dock, plus the
    closing guard — the real functions, nothing retyped."""
    return (
        _slice("  function changeCardPending(proposal) {", "  function changeCardHtml(proposal, state) {")
        + _slice("  function mountChangeCard(replyEls, proposal) {", "  function wireChangeCard(card, state, draw) {")
        + _slice("  function saveChangeCard(state) {", "  function undoChangeCard(state) {")
        + _slice("  function askSaveDialogIsDown() {", "  askScrim.addEventListener(")
        # mountChangeCard draws the card itself; here only the dock matters.
        + "\nfunction changeCardHtml() { return ''; }\n"
        + "function wireChangeCard() {}\n"
    )


def _run(body: str):
    res = nodeharness.run_node(_PRELUDE + _region() + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _proposal(n: int = 2):
    """A card with `n` changeable rows, in the shape the server sends."""
    rows = []
    for i, (day, was, now) in enumerate((
        (DAY1, "Chicken Stew", "Curd Rice"),
        (DAY2, "Tacos", "Dal"),
        (DAY3, "Pizza", "Paneer"),
    )[:n]):
        rows.append({"date": day, "slot": "dinner", "weekday": "Monday", "action": "change",
                     "current": {"meal": was}, "chosen": 0,
                     "candidates": [{"meal_name": now, "minutes": 25, "reason": "Quick."}]})
    rows.append({"date": DAY4, "slot": "dinner", "weekday": "Thursday", "action": "keep",
                 "current": {"meal": "Roast"}})
    return {"proposal_id": "p1", "rows": rows}


# =========================================================== the dock

@_needs_node
def test_the_dock_shows_the_count_and_both_buttons():
    """The card's first test. Two changeable rows -> "Save 2 changes to the
    week", apricot, with the soft "Keep the week as it was" beside it."""
    out = _run("""
mountChangeCard([], %s);
console.log(JSON.stringify({ hidden: DOCK.hidden, html: DOCK.innerHTML }));
""" % json.dumps(_proposal(2)))
    assert out["hidden"] is False
    assert "Save 2 changes to the week" in out["html"]
    assert "Keep the week as it was" in out["html"]
    # The apricot is the dock's own primary; the soft one is .dock-secondary.
    assert 'class="dock-primary" data-ask-save-go' in out["html"]
    assert 'class="dock-secondary" data-ask-save-keep' in out["html"]


@_needs_node
def test_one_change_reads_save_1_change():
    out = _run("""
mountChangeCard([], %s);
console.log(JSON.stringify({ html: DOCK.innerHTML }));
""" % json.dumps(_proposal(1)))
    assert "Save 1 change to the week" in out["html"]
    assert "1 changes" not in out["html"]


@_needs_node
def test_the_count_is_read_off_the_rows_save_would_actually_write():
    """Not every row is a change: a `keep`, a row with a problem and a row
    with no candidates are all on the card and none of them is saved. The
    count has to be what lands."""
    p = _proposal(2)
    p["rows"].append({"date": DAY3, "slot": "dinner", "weekday": "Wednesday", "action": "change",
                      "current": {"meal": "Soup"}, "problem": "no dish", "candidates": []})
    p["rows"].append({"date": DAY3, "slot": "lunch", "weekday": "Wednesday", "action": "change",
                      "current": {"meal": "Wrap"}, "candidates": []})
    out = _run("""
mountChangeCard([], %s);
console.log(JSON.stringify({ html: DOCK.innerHTML, n: changeCardPending(%s).length }));
""" % (json.dumps(p), json.dumps(p)))
    assert out["n"] == 2
    assert "Save 2 changes to the week" in out["html"]


@_needs_node
def test_the_line_above_the_dock_names_what_the_week_still_shows():
    """The card: "Not saved yet. Your week still shows the old <meals>." """
    out = _run("""
mountChangeCard([], %s);
console.log(JSON.stringify({ html: DOCK.innerHTML }));
""" % json.dumps(_proposal(2)))
    assert "Not saved yet. Your week still shows the old Chicken Stew and Tacos." in out["html"]


@_needs_node
def test_a_row_with_no_old_dish_does_not_promise_one():
    """A row filling a slot that held nothing has no "old" meal to name."""
    p = {"proposal_id": "p1", "rows": [
        {"date": DAY1, "slot": "dinner", "weekday": "Monday", "action": "change",
         "current": None, "chosen": 0, "candidates": [{"meal_name": "Curd Rice", "minutes": 20}]}]}
    out = _run("""
mountChangeCard([], %s);
console.log(JSON.stringify({ html: DOCK.innerHTML }));
""" % json.dumps(p))
    assert "Not saved yet. Your week is still as it was." in out["html"]
    assert "the old" not in out["html"]


@_needs_node
def test_a_card_with_nothing_to_save_shows_no_dock():
    p = {"proposal_id": "p1", "rows": [
        {"date": DAY1, "slot": "dinner", "weekday": "Monday", "action": "keep",
         "current": {"meal": "Chicken Stew"}}]}
    out = _run("""
mountChangeCard([], %s);
console.log(JSON.stringify({ hidden: DOCK.hidden, html: DOCK.innerHTML }));
""" % json.dumps(p))
    assert out["hidden"] is True
    assert out["html"] == ""


@_needs_node
def test_another_still_disables_save_while_it_works():
    """The card's own criterion, unchanged rule in a new home: a save that
    raced the re-pick would write one dish and show another."""
    out = _run("""
mountChangeCard([], %s);
var before = DOCK.innerHTML;
askChangeDock.busyRow = 0;
renderAskSaveDock();
console.log(JSON.stringify({ before: before, busy: DOCK.innerHTML }));
""" % json.dumps(_proposal(2)))
    assert "disabled" not in out["before"]
    assert out["busy"].count("disabled") == 2, "both dock buttons stand down while Another works"


@_needs_node
def test_saving_says_so_on_the_button():
    out = _run("""
mountChangeCard([], %s);
askChangeDock.saving = true;
renderAskSaveDock();
console.log(JSON.stringify({ html: DOCK.innerHTML }));
""" % json.dumps(_proposal(2)))
    assert "Saving…" in out["html"]


# ================================================= save / keep, from the dock

@_needs_node
def test_save_writes_and_the_dock_returns_to_normal():
    """The card's third test. And the toast names the change."""
    out = _run("""
APPLY = { status: 'applied', refused: [], proposal: { proposal_id: 'p1', rows: [],
  applied: [{ date: '%s', slot: 'dinner', meal: 'curd rice' }] } };
mountChangeCard([], %s);
tapDock('go').then(function () {
  console.log(JSON.stringify({ posted: POSTED, toasts: TOASTS,
    hidden: DOCK.hidden, html: DOCK.innerHTML, reloaded: WEEK_RELOADED }));
});
""" % (DAY2, json.dumps(_proposal(2))))
    assert any("/apply" in u for u in out["posted"]), "Save has to write"
    # S10's shipped wording, unchanged by this card — what moved is only
    # that it is built by the same one function the dock's count is.
    assert out["toasts"][-1]["msg"] == "curd rice was put on Tuesday"
    assert out["toasts"][-1]["undo"] is True
    assert out["hidden"] is True and out["html"] == "", "the dock returns to normal"


@_needs_node
def test_keep_the_week_as_it_was_writes_nothing_and_clears_the_dock():
    out = _run("""
mountChangeCard([], %s);
tapDock('keep');
console.log(JSON.stringify({ posted: POSTED, hidden: DOCK.hidden, left: true }));
""" % json.dumps(_proposal(2)))
    assert out["posted"] == [], "Keep must write nothing at all"
    assert out["hidden"] is True


@_needs_node
def test_a_refused_save_says_the_servers_sentence_and_leaves_the_week():
    out = _run("""
APPLY = { status: 'refused', refused: [{ date: '%s', slot: 'dinner', why: 'Emily would rather not' }] };
mountChangeCard([], %s);
tapDock('go').then(function () {
  console.log(JSON.stringify({ toasts: TOASTS, hidden: DOCK.hidden }));
});
""" % (DAY1, json.dumps(_proposal(2))))
    assert "I left the week as it was — Emily would rather not." == out["toasts"][-1]["msg"]
    assert out["hidden"] is True


@_needs_node
def test_a_failed_save_keeps_the_dock_so_it_can_be_tried_again():
    out = _run("""
APPLY = 'reject';
mountChangeCard([], %s);
tapDock('go').then(function () {
  console.log(JSON.stringify({ toasts: TOASTS, hidden: DOCK.hidden, html: DOCK.innerHTML }));
});
""" % json.dumps(_proposal(2)))
    assert "didn’t save" in out["toasts"][-1]["msg"]
    assert out["hidden"] is False, "a failure must leave the Save where it was"
    assert "Save 2 changes to the week" in out["html"]


# ========================================== closing with changes waiting

@_needs_node
def test_closing_with_changes_waiting_asks_instead_of_closing():
    """The card's second test. And the question carries the same count."""
    out = _run("""
mountChangeCard([], %s);
askSheetCloseRequested();
console.log(JSON.stringify({ dialogHidden: DIALOG.hidden, title: DIALOG.title,
  note: DIALOG.note, closed: SHEET.closed }));
""" % json.dumps(_proposal(2)))
    assert out["dialogHidden"] is False, "the dialog has to come up"
    assert out["title"] == "Save the 2 changes to the week?"
    assert out["note"] == "Not saved yet. Your week still shows the old Chicken Stew and Tacos."
    assert out["closed"] == 0, "the sheet must NOT have closed under the question"


@_needs_node
def test_closing_with_nothing_waiting_just_closes():
    p = {"proposal_id": "p1", "rows": [
        {"date": DAY1, "slot": "dinner", "weekday": "Monday", "action": "keep",
         "current": {"meal": "Chicken Stew"}}]}
    out = _run("""
mountChangeCard([], %s);
askSheetCloseRequested();
console.log(JSON.stringify({ dialogHidden: DIALOG.hidden, closed: SHEET.closed }));
""" % json.dumps(p))
    assert out["dialogHidden"] is True
    assert out["closed"] == 1


@_needs_node
def test_a_saved_card_no_longer_asks_on_the_way_out():
    out = _run("""
APPLY = { status: 'applied', refused: [], proposal: { proposal_id: 'p1', rows: [],
  applied: [{ date: '%s', slot: 'dinner', meal: 'curd rice' }] } };
mountChangeCard([], %s);
tapDock('go').then(function () {
  askSheetCloseRequested();
  console.log(JSON.stringify({ dialogHidden: DIALOG.hidden, closed: SHEET.closed }));
});
""" % (DAY1, json.dumps(_proposal(2))))
    assert out["dialogHidden"] is True
    assert out["closed"] == 1


@_needs_node
def test_the_dialogs_dont_save_throws_the_changes_away_and_closes():
    """"Don't save" means the week stays exactly as it was: nothing is
    POSTed, and the sheet closes rather than asking a second time."""
    out = _run("""
mountChangeCard([], %s);
askSheetCloseRequested();
tapDialog('dont');
console.log(JSON.stringify({ posted: POSTED, closed: SHEET.closed,
  dialogHidden: DIALOG.hidden, dockHidden: DOCK.hidden }));
""" % json.dumps(_proposal(2)))
    assert out["posted"] == [], "Don't save must not write"
    assert out["closed"] == 1
    assert out["dialogHidden"] is True
    assert out["dockHidden"] is True, "nothing is waiting any more"


@_needs_node
def test_the_dialogs_save_writes_and_then_closes():
    out = _run("""
APPLY = { status: 'applied', refused: [], proposal: { proposal_id: 'p1', rows: [],
  applied: [{ date: '%s', slot: 'dinner', meal: 'curd rice' }] } };
mountChangeCard([], %s);
askSheetCloseRequested();
tapDialog('do').then(function () {
  console.log(JSON.stringify({ posted: POSTED, closed: SHEET.closed,
    dialogHidden: DIALOG.hidden, toasts: TOASTS }));
});
""" % (DAY1, json.dumps(_proposal(2))))
    assert any("/apply" in u for u in out["posted"]), "Save has to write"
    assert out["closed"] == 1, "and then the sheet closes"
    assert out["dialogHidden"] is True
    assert out["toasts"] and "curd rice" in out["toasts"][-1]["msg"]


@_needs_node
@pytest.mark.parametrize("how", ["scrim", "escape"])
def test_the_scrim_and_escape_are_i_didnt_mean_to_close(how):
    """A mis-tap outside the dialog, or Escape, is NOT "don't save".
    Throwing a household's changes away on a mis-tap is the thing this
    dialog exists to prevent — so the sheet stays up with the dock still
    showing what is waiting, and nothing is written either way."""
    tap = "WIRED['ask-save-scrim:click']()" if how == "scrim" else "pressEscape()"
    out = _run("""
mountChangeCard([], %s);
askSheetCloseRequested();
%s;
console.log(JSON.stringify({ posted: POSTED, closed: SHEET.closed,
  dialogHidden: DIALOG.hidden, dockHidden: DOCK.hidden, html: DOCK.innerHTML }));
""" % (json.dumps(_proposal(2)), tap))
    assert out["dialogHidden"] is True, "the dialog goes"
    assert out["closed"] == 0, "the sheet stays up"
    assert out["posted"] == [], "nothing written"
    assert out["dockHidden"] is False and "Save 2 changes to the week" in out["html"]


def test_all_three_closing_doors_go_through_the_one_guard():
    """"✕, swipe, back" is three doors and they are not one code path. A
    guard on one of three is the bug still shipping — so there is exactly
    ONE guard function and every door calls it."""
    for wire in (
        "askScrim.addEventListener('click', askSheetCloseRequested)",
        "document.getElementById('ask-sheet-handle').addEventListener('click', askSheetCloseRequested)",
        "document.getElementById('ask-sheet-back').addEventListener('click', askSheetCloseRequested)",
    ):
        assert wire in SHELL_JS, wire
    # Escape, and the back GESTURE through the shell's ONE popstate listener.
    assert re.search(r"e\.key === 'Escape' && !askSheet\.hidden && askSaveDialogIsDown\(\)"
                     r" askSheetCloseRequested\(\);".replace(" ask", r"\) ask"), SHELL_JS) \
        or "askSaveDialogIsDown()) askSheetCloseRequested();" in SHELL_JS
    popstate = re.search(r"window\.addEventListener\('popstate', function \(e\) \{(.*?)\n  \}\);",
                         SHELL_JS, re.S)
    assert popstate and "askSheetCloseRequested()" in popstate.group(1)
    assert len(re.findall(r"addEventListener\('popstate'", SHELL_JS)) == 1
    # And the guard is on the DOORS, not inside closeAskSheet — which ~30
    # programmatic callers use and must not be asked about.
    i = SHELL_JS.index("function closeAskSheet()")
    assert "askChangeUnsaved" not in SHELL_JS[i:i + 600]


def test_the_dialog_is_a_real_dialog_and_not_just_the_insides():
    """The container rules in shell.css are ID-scoped: reusing
    .reset-title / .reset-actions gets the insides and none of the box.
    A dialog shipped that way once (2026-09-14) and rendered in document
    flow under the tab bar with aria-modal lying. All FIVE lists."""
    assert 'id="ask-save-dialog" hidden data-motion="dialog" role="dialog" aria-modal="true"' in SHELL_HTML
    assert 'id="ask-save-scrim" hidden' in SHELL_HTML
    for anchor in (
        "#ask-save-scrim,\n#reset-scrim",                      # the scrim box
        "#ask-save-dialog,\n#reset-dialog",                    # the dialog box
        "#ask-save-scrim[hidden], #ask-save-dialog[hidden],",  # the [hidden] guard
        "#ask-save-scrim,\n#reset-scrim, #dinner-confirm-scrim",  # the transition list
        "#ask-save-scrim.is-open,",                            # and its is-open twin
    ):
        assert anchor in SHELL_CSS, anchor


def test_the_cards_own_save_and_leave_are_gone():
    """The card: "The buttons inside the card go." """
    assert "data-change-save" not in SHELL_JS
    assert "data-change-leave" not in SHELL_JS
    i = SHELL_JS.index("function changeCardHtml(")
    body = SHELL_JS[i:SHELL_JS.index("function mountChangeCard(")]
    assert "Save changes" not in body
    assert "Leave the week as it was" not in body


def test_the_dock_is_the_sheets_one_apricot():
    """Rule 5. The send button beside it is a SPRUCE fill with an apricot
    glyph, not an apricot fill, so it needed no change — but if that ever
    becomes a fill there would be two."""
    i = SHELL_CSS.index(".ask-composer-send {")
    send = SHELL_CSS[i:i + 400]
    assert "background: var(--spruce)" in send
    assert "background: var(--apricot)" not in send
    # And the dock's own two: one apricot (.dock-primary), one sand.
    j = SHELL_CSS.index(".ask-save-dock {")
    dock = SHELL_CSS[j:SHELL_CSS.index(".ask-composer-send {", j)]
    assert "var(--apricot)" not in dock, "the apricot is .dock-primary's, not a second one here"
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", dock) is None, "tokens only (Rule 9)"


# ================================= and against the DATABASE, not the markup

@pytest.fixture
def week():
    tools.add_member("Emily")
    for name in ("Pork Chops", "Chili"):
        tools.add_recipe(name, ingredients=[{"item": name.split()[0], "qty": "1 lb",
                                             "category": "meat/seafood"}],
                         food_groups=["protein"], prep_time_minutes=10, cook_time_minutes=20)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day, dish in ((DAY1, "Pork Chops"), (DAY2, "Chili")):
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan_id, reasoning="fits")
    tools.approve_weekly_plan(plan_id)
    prop._PROPOSALS.clear()
    return plan_id


def _plan_rows(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? AND mpe.slot='dinner' "
        "ORDER BY mpe.date", (plan_id,)).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def _grocery():
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? ORDER BY item, quantity",
        (tools.household_id(),)).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def test_dont_save_really_leaves_the_plan_rows_and_the_grocery_list(week):
    """The card's fourth test, asserted against the DATABASE rather than
    the markup: "Don't save" is a tap that writes NOTHING. The dock's
    "Keep the week as it was" is the same answer by the other door — both
    simply never call /apply, which is the one thing that writes."""
    out = tools.propose_plan_changes(week, [
        {"date": DAY1, "slot": "dinner", "action": "change", "candidates": [{
            "meal_name": "Shrimp Tacos", "reason": "Quick.",
            "ingredients": [{"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"}],
            "instructions": ["Cook it."], "food_groups": ["protein"], "main_protein": "shrimp",
            "prep_time_minutes": 5, "cook_time_minutes": 15, "default_servings": 2}]},
    ])
    rows_before, grocery_before = _plan_rows(week), _grocery()
    assert out["proposal_id"]

    # Don't save / Keep: the proposal is simply never applied.
    assert _plan_rows(week) == rows_before
    assert _grocery() == grocery_before
    assert not any(r["name"] == "Shrimp Tacos" for r in tools.list_recipes())

    # And Save, through the same route the dock posts to, really writes —
    # so the test above is about the choice, not about a dead route.
    tools.apply_proposal(out["proposal_id"])
    assert _plan_rows(week) != rows_before
    assert ("Shrimp Tacos" in [m for _, m in _plan_rows(week)])


def test_the_count_matches_what_apply_actually_lands(week):
    """The honesty claim, end to end: the number on the button is the
    number of rows the server then reports as applied."""
    cand = lambda n: {
        "meal_name": n, "reason": "Quick.",
        "ingredients": [{"item": n.split()[0], "qty": "1 lb", "category": "meat/seafood"}],
        "instructions": ["Cook it."], "food_groups": ["protein"], "main_protein": "chicken",
        "prep_time_minutes": 5, "cook_time_minutes": 15, "default_servings": 2}
    out = tools.propose_plan_changes(week, [
        {"date": DAY1, "slot": "dinner", "action": "change", "candidates": [cand("Shrimp Tacos")]},
        {"date": DAY2, "slot": "dinner", "action": "change", "candidates": [cand("Lemon Chicken")]},
        {"date": DAY3, "slot": "dinner", "action": "keep"},
    ])
    # What the dock would count: the same predicate, in Python.
    pending = [r for r in out["rows"]
               if r["action"] == "change" and r.get("candidates") and not r.get("problem")]
    applied = tools.apply_proposal(out["proposal_id"])
    assert len(pending) == len(applied["proposal"]["applied"]) == 2
