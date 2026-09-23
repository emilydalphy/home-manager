"""
"Put back" after an add that MERGED must restore the line, never delete it.

Loop Board bug. The list says "Bell peppers · 3", you add "Bell pepper · 2"
from the Shop tab's add sheet, Pomona correctly puts them on one line —
"Bell peppers · 5" — and the toast says "Changes saved · Put back". Tapping
Put back removed the whole line: the 3 that were already on the list went
with the 2 just added, silently, and nothing said so.

ROOT CAUSE, and it is a disagreement between two halves rather than a
missing branch. `groAddedToast` (static/shell.js) has always had the right
branch — if the add merged, put the old amount and store back rather than
removing — and it was guarded on `wasBefore`, which came from an EXACT name
match against the phone's copy of the list. The server merges on
`grocery._merge_key`, which ignores case, spacing and a trailing s on the
last word, deliberately, so a household never gets two lines of peppers. So
the two rules disagreed in exactly the case a merge happened: server merged,
client found nothing, `&& wasBefore` failed, and the tap fell through to the
delete.

The merge rule itself is right and is not touched. What moves is the
client's lookup: `groLinesById` reads the pre-write list by ROW ID, and the
row id is the one the server hands back (`item_id`, the line it merged
into). One merge rule in the app, on the server, so the two cannot drift.

WHY THIS IS NOT A SOURCE-MARKER FILE. The defect is a guard falling through
— the delete call was there all along and is still there, correctly, for a
genuinely new line. Reading the source for a name cannot tell the two apart.
So section 1 RUNS the shell's own `groAddLine` and the toast's own
`onClick` under node (tests/nodeharness.py) against a stubbed fetch, and
asserts WHICH requests the tap puts on the wire. Section 2 drives the real
`add_grocery_item` and the real routes' tools, so the household-visible
before/after is measured rather than reasoned about.

A SECOND, NARROWER THING THE SAME UNDO GETS WRONG is NOT fixed here, and
is characterised at the end of this file so nobody reports it as new:
`add_grocery_item` also overwrites the merged row's CATEGORY with the one
groGuessCategory picked for the words that were typed, and Put back
restores the amount and the store only — so a line can come back at the
right number in the wrong section of the shop. Pre-existing; main's
exact-name path had it too. It was tried as one extra field on the
`/update` call this already makes and taken back out, for two reasons
worth having written down: `update_grocery_item` leaves a field alone only
for None, so a copy of the list whose rows carry no category would blank
it outright, and it broke `tests/test_shop_add_sheet.py`'s own pin on that
request's body — a widening of somebody else's contract, which is more
than this card gets to do.

LABELS, and read the red count for less than it looks. Measured against
`main` at a2e3129: **7 red, 11 green**, of which only **FIVE are behaviour
catches**, each failing on the assertion it is named for with `POST
/api/grocery-list/<id>/remove` on the wire. Of the other two red, one is a
source marker and one dies on `groLinesById is not defined`, a name main
has not got. Every docstring says which it is. The greens are guards on
what did NOT change, and each names the mutation that pins it — none of
them is coverage for the bug.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.tools.grocery import _merge_key

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    """
    Source with `//` and `/* */` removed, so a marker cannot be satisfied
    by a comment that merely names the thing it is checking for.

    Deliberately crude — it is not a JS parser and does not need to be.
    It leaves string literals alone by only cutting at a `//` that is not
    inside quotes on that line, which is enough for the one job here:
    stopping a NAME in prose from standing in for a definition.
    """
    out = []
    for line in src.splitlines():
        quote = None
        cut = len(line)
        i = 0
        while i < len(line) - 1:
            ch = line[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "\"'`":
                quote = ch
            elif ch == "/" and line[i + 1] == "/":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is needed to execute the shell's own functions",
)


# ==========================================================================
# The harness
# ==========================================================================
# The whole Grocery region, up to the hands-free voice code (which wants a
# SpeechRecognition engine) — the same slice tests/test_grocery_fast_sort.py
# and tests/test_shop_stale_after_chat.py take.


def _grocery_block() -> str:
    start = SHELL_JS.index("  var GRO_ICONS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


_PRELUDE = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function setRootBand() {}
function bandDateLabel() { return 'Wednesday, Sep 17'; }
function emptyMomentHtml() { return ''; }
function snwLink() { return ''; }
function activateTab() {}
var PLAIN_TOASTS = [];
function showToast(msg) { PLAIN_TOASTS.push(msg); }
// The real toastSaved is far outside this region (it is the shell's, not
// the Grocery tab's). What matters here is what it is HANDED: an action
// with a label and an onClick, or nothing at all.
var TOASTS = [];
function toastSaved(action, holdMs) { TOASTS.push({ action: action || null, holdMs: holdMs || null }); }
var coachState = { householdId: 1 };
var scrollEl = { scrollTop: 0 };
const STORE = new Map();
const window = {
  localStorage: {
    getItem: function (k) { return STORE.has(k) ? STORE.get(k) : null; },
    setItem: function (k, v) { STORE.set(k, String(v)); },
    removeItem: function (k) { STORE.delete(k); }
  },
  history: { pushState: function () {} },
  confirm: function () { return true; }
};
var navigator = { onLine: true };
var document = { activeElement: null, createElement: function () { return null; },
  getElementById: function () { return null; } };
var panels = { grocery: null };
// Every request the tab puts on the wire, in order, with its body — which
// is the whole assertion in this file: Put back either restores a line or
// removes it, and those are two different URLs.
var CALLS = [];
var SERVER_ADD = {};
function fetch(url, opts) {
  CALLS.push((((opts && opts.method) || 'GET') + ' ' + url
    + (opts && opts.body ? ' ' + opts.body : '')));
  return Promise.resolve({ ok: true, status: 200,
    json: function () { return Promise.resolve(url === '/api/grocery-list/add' ? SERVER_ADD : {}); } });
}
function listOf(items) {
  return { stores: { Loblaws: { sections: [{ section: 'produce', items: items }],
                                purchased: [], inCart: [] } } };
}
// What the server hands back on the re-read AFTER the add, if a test sets
// it. Not decoration: it is what makes "the amount restored is the one
// from BEFORE the write" a real question — read the line live at toast
// time instead of from the snapshot and you get the merged 5 back, not
// the 3 that were there.
var AFTER_ADD = null;
const out = (v) => console.log(JSON.stringify(v));
"""

# After the region, so these win over the real ones: loadGrocery re-reads
# eight endpoints and renderGrocery wants a panel, and neither is what is
# under test here.
_TAIL = """
function loadGrocery() { if (AFTER_ADD) groceryState.data = AFTER_ADD; return Promise.resolve(); }
function renderGrocery() {}
function renderGroceryOfflineLine() {}
function groAddSheetOpen() {}
"""


def _run(tail: str):
    script = _PRELUDE + _grocery_block() + _TAIL + tail
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# One add, then the toast's own Put back tapped, and the requests that tap
# made. `before` is the phone's copy of the list; `server` is what
# /api/grocery-list/add answers; `after` is what the re-read then shows.
_DRIVE = """
function drive(opts) {
  groceryState.data = listOf(opts.before);
  AFTER_ADD = opts.after ? listOf(opts.after) : null;
  SERVER_ADD = opts.server;
  TOASTS.length = 0;
  return groAddLine({ typed: opts.typed, item: opts.typed, quantity: opts.quantity || '2',
                      category: 'produce', store: null })
    .then(function () {
      var t = TOASTS[TOASTS.length - 1];
      CALLS.length = 0;
      if (t && t.action) t.action.onClick();
      return new Promise(function (res) {
        setTimeout(function () {
          res({ label: t && t.action ? t.action.label : null, calls: CALLS.slice() });
        }, 30);
      });
    });
}
"""

PEPPERS = ("{ id: 1, item: 'Bell peppers', quantity: '3', category: 'produce', "
           "store: 'Loblaws', status: 'needed' }")
CARROT = ("{ id: 7, item: 'carrot', quantity: '3', category: 'produce', "
          "store: '', status: 'needed' }")
OLIVE = ("{ id: 9, item: 'Olive  oil', quantity: '1 bottle', category: 'pantry', "
         "store: 'Costco', status: 'needed' }")


def _restores(got, item_id, quantity, store):
    """Put back restored the line: its amount, then its store. Never a
    remove — which is the whole point, so it is asserted rather than
    implied by the other two."""
    assert got["label"] == "Put back"
    assert got["calls"] == [
        'POST /api/grocery-list/%d/update {"quantity":"%s"}' % (item_id, quantity),
        'POST /api/grocery-list/%d/store {"store":"%s","remember":false}' % (item_id, store),
    ], got["calls"]
    assert not any("/remove" in c for c in got["calls"])


# ==========================================================================
# 1. The tap, driven
# ==========================================================================

@_needs_node
def test_a_singular_add_onto_a_plural_line_puts_the_amount_back_rather_than_deleting_it():
    """CATCH — the reported bug, word for word. "Bell peppers · 3" on the
    list, "Bell pepper · 2" typed in. RED ON MAIN, where the tap fires
    POST /api/grocery-list/1/remove and the 3 go with it."""
    got = _run(_DRIVE + """
      drive({ before: [%s], typed: 'Bell pepper',
              server: { item_id: 1, item: 'Bell peppers', quantity: '5', merged: true },
              after: [{ id: 1, item: 'Bell peppers', quantity: '5', category: 'other',
                        store: 'Loblaws', status: 'needed' }] })
        .then(out);
    """ % PEPPERS)
    _restores(got, 1, "3", "Loblaws")


@_needs_node
def test_a_plural_add_onto_a_singular_line_does_too():
    """CATCH — the same disagreement the other way round: the list says
    "carrot", the household types "Carrots". RED ON MAIN. Its store is ''
    (nobody has sorted it), which is restored as '' rather than skipped —
    a blank store is an answer the line had."""
    got = _run(_DRIVE + """
      drive({ before: [%s], typed: 'Carrots',
              server: { item_id: 7, item: 'carrot', quantity: '5', merged: true } }).then(out);
    """ % CARROT)
    _restores(got, 7, "3", "")


@_needs_node
def test_a_double_space_difference_does_too():
    """CATCH — _merge_key collapses runs of whitespace, so "Olive  oil"
    and "Olive oil" are one line to the server and were two names to the
    phone. RED ON MAIN."""
    got = _run(_DRIVE + """
      drive({ before: [%s], typed: 'Olive oil', quantity: '1 bottle',
              server: { item_id: 9, item: 'Olive  oil', quantity: '2 bottles', merged: true } }).then(out);
    """ % OLIVE)
    _restores(got, 9, "1 bottle", "Costco")


@_needs_node
def test_a_case_only_difference_does_too():
    """GUARD — case was already ignored by the old lookup's .toLowerCase(),
    so the DELETION never happened for this pair. GREEN ON MAIN, and it is
    here because the fix must not lose the cases the old rule did get
    right: point the lookup at anything but the server's own id and it
    goes."""
    got = _run(_DRIVE + """
      drive({ before: [%s], typed: 'bell peppers',
              server: { item_id: 1, item: 'Bell peppers', quantity: '5', merged: true } }).then(out);
    """ % PEPPERS)
    _restores(got, 1, "3", "Loblaws")


@_needs_node
def test_a_genuinely_new_line_is_still_removed_outright():
    """GUARD — nothing was merged into, so Put back means take it off the
    list. GREEN ON MAIN; pinned by the mutation that restores
    unconditionally, which turns this into two writes to a row that had no
    'before'."""
    got = _run(_DRIVE + """
      drive({ before: [%s], typed: 'Kale',
              server: { item_id: 42, item: 'Kale', quantity: '1 bag', merged: false } }).then(out);
    """ % PEPPERS)
    assert got["label"] == "Put back"
    assert got["calls"] == ["POST /api/grocery-list/42/remove"]


@_needs_node
def test_a_merge_into_a_line_this_phone_never_had_offers_no_put_back_at_all():
    """CATCH — the other adult put "Leeks" on the list from their phone;
    this one adds "Leek" before its own copy has caught up, and the server
    merges into a row that was never in this copy. RED ON MAIN, where the
    tap removes a line this phone has never seen the amount of. Knowing a
    line changed and not knowing what it read before is exactly when an
    undo must not touch it — so the change is saved and nothing is
    offered."""
    got = _run(_DRIVE + """
      drive({ before: [%s], typed: 'Leek',
              server: { item_id: 99, item: 'Leeks', quantity: '4', merged: true } }).then(out);
    """ % PEPPERS)
    assert got["label"] is None
    assert got["calls"] == []


@_needs_node
def test_a_merged_add_never_falls_through_to_the_delete_however_it_is_reached():
    """CATCH — the rule stated on its own, over `groAddedToast` directly:
    `merged` true is a promise that a line existed before this, so no path
    out of that branch may be a remove. RED ON MAIN."""
    got = _run("""
      TOASTS.length = 0;
      groAddedToast({ item_id: 5, merged: true }, null);
      groAddedToast({ item_id: 5, merged: true }, undefined);
      groAddedToast({ item_id: 5, merged: true }, { quantity: '', store: '' });
      CALLS.length = 0;
      TOASTS.forEach(function (t) { if (t.action) t.action.onClick(); });
      setTimeout(function () {
        out({ offered: TOASTS.map(function (t) { return t.action ? t.action.label : null }),
              calls: CALLS.slice() });
      }, 30);
    """)
    # The first two know a merge happened and cannot say into what.
    assert got["offered"][:2] == [None, None]
    # The third knows: the line was blank and unsorted, which is a state to
    # restore, not a reason to delete.
    assert got["offered"][2] == "Put back"
    assert not any("/remove" in c for c in got["calls"]), got["calls"]


@_needs_node
def test_the_amount_restored_is_the_one_from_before_the_write_not_after_it():
    """GUARD — GREEN ON MAIN for this exact-name case, where main's lookup
    finds the line and its ordering was never the broken part. What it pins
    is the ORDER: the snapshot is taken before the POST, and read the line
    live at toast time instead and the re-read has already replaced it with
    the merged 5, so Put back would "restore" the very total it was
    undoing. Pinned by the mutation that moves the lookup below
    loadGrocery()."""
    got = _run(_DRIVE + """
      drive({ before: [%s], typed: 'Bell peppers',
              server: { item_id: 1, item: 'Bell peppers', quantity: '5', merged: true },
              after: [{ id: 1, item: 'Bell peppers', quantity: '5', category: 'other',
                        store: 'Costco', status: 'needed' }] })
        .then(out);
    """ % PEPPERS)
    _restores(got, 1, "3", "Loblaws")


@_needs_node
def test_a_response_with_no_id_says_nothing_and_offers_nothing():
    """GUARD — unchanged from main, and it is the one shape that was
    already handled: with no row to act on there is no undo to offer.
    Pinned by the mutation that drops the `if (!id)` line, after which
    these build a Put back aimed at /api/grocery-list/undefined/remove."""
    got = _run("""
      TOASTS.length = 0;
      groAddedToast({}, null);
      groAddedToast(null, null);
      out(TOASTS.map(function (t) { return t.action ? t.action.label : null }));
    """)
    assert got == [None, None]


def test_the_lookup_is_by_row_id_and_nothing_looks_a_line_up_by_typed_name():
    """A SOURCE MARKER rather than a behaviour test. RED ON MAIN, and read
    that for what it is: it sees that the old rule's function is gone, not
    that the new one is right — the tests above do that, and they are the
    evidence. It is here because the whole bug was a second copy of a merge
    rule, and the cheapest way this comes back is somebody reaching for a
    by-name helper again."""
    # Comment-stripped, the `_code_of` idiom this repo adopted on
    # 2026-09-18 and again on 2026-09-21 after a marker was twice
    # satisfied by prose that merely NAMED the thing it was checking for.
    # This file's own prose above the function names it too, so the
    # fail-open direction was real rather than theoretical.
    code = _strip_js_comments(SHELL_JS)
    assert "function groLinesById(" in code
    assert "groLineNamed" not in code


@_needs_node
def test_a_bought_line_is_in_the_snapshot_and_a_merge_can_never_name_one():
    """GUARD — `groLinesById` walks every line the copy holds, purchased
    rows included, where the old by-name lookup skipped them. Safe because
    add_grocery_item only ever merges into 'needed' or 'spice' (asserted
    in section 2), so a purchased id cannot come back as a merge target.
    RED ON MAIN, and NOT a behaviour catch: it dies on `groLinesById is not
    defined`, which is the only kind of red a test of a new function can
    have. Pinned by the mutation that makes groLinesById return {}."""
    got = _run("""
      groceryState.data = { stores: { Loblaws: {
        sections: [{ section: 'produce', items: [%s] }],
        purchased: [{ id: 3, item: 'Milk', quantity: '2 l', category: 'dairy',
                      store: 'Loblaws', status: 'purchased' }],
        inCart: [] } } };
      out(groLinesById());
    """ % PEPPERS)
    assert got == {
        "1": {"quantity": "3", "store": "Loblaws"},
        "3": {"quantity": "2 l", "store": "Loblaws"},
    }


# ==========================================================================
# 2. The server half — the rule the client now agrees with
# ==========================================================================

@pytest.mark.parametrize(
    "on_the_list, typed",
    [
        ("Bell peppers", "Bell pepper"),
        ("carrot", "Carrots"),
        ("Olive  oil", "Olive oil"),
        ("Eggs", "egg"),
    ],
)
def test_the_server_really_merges_these_name_pairs_and_names_the_row(on_the_list, typed):
    """GUARD on the rule the client is now keyed to — GREEN ON MAIN,
    because the server was never the broken half. What it pins is the
    CONTRACT the fix rests on: a merge answers `merged` true AND the id of
    the line it merged into, so the phone has something to look up."""
    first = tools.add_grocery_item(on_the_list, "3", "produce")
    second = tools.add_grocery_item(typed, "2", "produce")
    assert _merge_key(on_the_list) == _merge_key(typed)
    assert second["merged"] is True
    assert second["item_id"] == first["item_id"]
    # And the name it says back is the line's own wording, not the typed
    # one — which is precisely why matching on the typed name failed.
    assert second["item"] == on_the_list
    assert [(r["item"], r["quantity"]) for r in tools.list_grocery_list()] == [(on_the_list, "5")]


def test_a_merge_only_ever_lands_on_a_line_still_to_buy():
    """GUARD — what makes it safe for the phone's snapshot to hold bought
    rows too. A purchased line is not a merge target, so its id can never
    come back as `item_id` on a merge. GREEN ON MAIN; pinned by the
    mutation that widens add_grocery_item's candidate statuses."""
    first = tools.add_grocery_item("Bell peppers", "3", "produce")
    tools.mark_grocery_item(first["item_id"], "purchased")
    second = tools.add_grocery_item("Bell pepper", "2", "produce")
    assert second["merged"] is False
    assert second["item_id"] != first["item_id"]


def test_what_the_household_saw_before_the_fix_and_what_they_see_now():
    """DEMONSTRATION, not a catch — green on main, and relabelled after
    review pointed out it was carrying a CATCH label it cannot earn.

    It drives the real server tools and NO client code at all, so by
    construction it cannot fail on this bug: it simply performs, by hand,
    the two things Put back can ask the server to do, and shows what each
    leaves on the list. That is worth keeping — it is the clearest
    statement in this file of what the household actually lost — but it
    proves nothing about the fix, and the file's own docstring promises
    that every test says which it is.

    What catches the bug is section 1, which runs the real `groAddLine`
    and the real toast handler and asserts which requests the tap makes."""
    tools.add_grocery_item("Bell peppers", "3", "produce")
    merged = tools.add_grocery_item("Bell pepper", "2", "produce")
    assert [(r["item"], r["quantity"]) for r in tools.list_grocery_list()] == [("Bell peppers", "5")]

    # What main's Put back did.
    tools.remove_grocery_item(merged["item_id"])
    assert tools.list_grocery_list() == []

    # What it does now, from the same starting point.
    tools.add_grocery_item("Bell peppers", "3", "produce")
    merged = tools.add_grocery_item("Bell pepper", "2", "produce")
    tools.update_grocery_item(merged["item_id"], quantity="3")
    tools.set_grocery_item_store(merged["item_id"], "", remember=False)
    assert [(r["item"], r["quantity"]) for r in tools.list_grocery_list()] == [("Bell peppers", "3")]


def test_the_aisle_does_NOT_come_back_with_the_amount_and_that_is_not_fixed_here():
    """CHARACTERISATION of what this branch deliberately leaves, so nobody
    reports it as new. The add overwrites the merged row's category with
    groGuessCategory's reading of the words that were typed, and Put back
    sends the quantity and the store only — so the line returns at the
    right number under the wrong section heading. Pre-existing and
    unchanged here. INVERT THIS TEST when it is fixed; the reasons it was
    not are in the comment at the /update call and in this file's own
    docstring."""
    tools.add_grocery_item("Bell peppers", "3", "produce")
    merged = tools.add_grocery_item("Bell pepper", "2", "other")
    assert [(r["item"], r["quantity"], r["category"]) for r in tools.list_grocery_list()] == [
        ("Bell peppers", "5", "other")
    ]
    tools.update_grocery_item(merged["item_id"], quantity="3")
    tools.set_grocery_item_store(merged["item_id"], "", remember=False)
    assert [(r["item"], r["quantity"], r["category"]) for r in tools.list_grocery_list()] == [
        ("Bell peppers", "3", "other")
    ]
