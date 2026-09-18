"""
A way out of the Shop loop before every store is done.

Emily, 2026-09-13, on her phone: "Unless you complete all the shops, you get
stuck in the Shop loop. We need to make an exit out for users so this
doesn't happen."

What trapped her, read off the code rather than guessed:

  * WHERE NEXT had no way to the Shop root at all. Its crumb reopens the
    stop just finished (on purpose — the mis-tap guard), and its one button,
    "I'm done shopping for today", is exactly what a shopper going home with
    a store still to do would say — and it walked her into "How did it go?",
    which asked about every unbought thing at a store she had not been to.
  * TRIP and WRAP UP did have a crumb ("‹ Shop") — small, grey, at the top
    — but the list it landed on looked identical to a list with no trip on:
    "Start the trip" again, nothing saying a trip was half done.
  * "Start the trip" over a paused trip resumed at the snapshot INDEX, which
    still names the stop just finished after "Done at Costco" — so coming
    back reopened Costco ("Stop 2 of 2 · everything here is in the cart")
    and the only way forward was to finish it a second time. That is the
    loop.
  * The trip was page-view state only. On a phone, "come back later" means
    the installed app was killed in between, and with it went the snapshot;
    what had been ticked into a trolley sat in_cart on the server, off
    every screen, until some later finish swept it.

So: every trip screen's dock carries "Finish later" beside its own action;
WHERE NEXT's end-the-trip button says what it does ("Skip the rest"); LIST
over a paused trip says "Trip in progress · 1 stop left" in the band and
offers "Continue the trip" + "Finish the trip" in its dock;
continuing lands on the current stop if it is still open, else on the
choice of what is left; and the trip is mirrored into localStorage, per
household, and read back once on the first list load, dropped after three
days.

Behaviour is run under node against shell.js's own functions, the way
tests/test_grocery_fast_sort.py does — every bug above is a piece of
behaviour a source-marker test cannot see.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _grocery_block() -> str:
    """The whole Grocery region including onGroceryClick, up to the
    hands-free voice code — the same slice test_grocery_fast_sort takes."""
    start = SHELL_JS.index("  var GRO_CATEGORY_LABELS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
function fetch(url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
}
function closes() { return POSTS.filter(function (p) { return p.url === '/api/shopping-trips/close'; }).length; }
function purchases() { return POSTS.filter(function (p) { return p.body && p.body.status === 'purchased'; }).map(function (p) { return p.url; }); }
const panels = {};
const TOASTS = [];
function showToast(msg, action, hold) { TOASTS.push({ msg: msg, action: action, hold: hold }); }
function lastToast() { const t = TOASTS[TOASTS.length - 1]; return t ? t.msg : null; }
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
var scrollEl = null;
var TAB_SWITCHES = [];
function activateTab(key) { TAB_SWITCHES.push(key); }
var coachState = { householdId: 1 };
function emptyMomentHtml(icon, sentence, detail) { return '<div class="empty-moment">' + sentence + '</div>'; }
function fakeEl(dataset) {
  return { dataset: dataset, disabled: false, closest: function () { return null; },
    classList: { toggle: function () {} }, setAttribute: function () {}, querySelectorAll: function () { return []; } };
}
function click(dataset) {
  const el = fakeEl(dataset);
  onGroceryClick({ target: { closest: function () { return el; } } });
  return el;
}
function settle(fn) { setTimeout(fn, 30); }
// "Start the trip" over more than one stop asks "Where are we headed?"
// first (2026-09-13, Loop Board: the trip starts on the store you name).
// Every trip in this file starts at Costco, the first stop, which is where
// the old button used to land without asking.
function startTripAt(store) { click({ gro: 'start-trip' }); click({ gro: 'head-for', store: store }); }
// Two shops, three things, nothing in any trolley. The trip Emily was
// describing, shrunk to a size a test can read.
function twoShops(opts) {
  opts = opts || {};
  groceryState.data = { stores: {
    Unassigned: { sections: [], purchased: [], inCart: [] },
    Costco: { sections: opts.costcoNeeded === false ? [] : [{ section: 'other', items: [
      { id: 1, item: 'Rice', quantity: '1', store: 'Costco', store_decided: 1 },
      { id: 2, item: 'Oats', quantity: '1', store: 'Costco', store_decided: 1 }] }],
      purchased: [], inCart: opts.costcoInCart || [] },
    Metro: { sections: [{ section: 'other', items: [
      { id: 3, item: 'Eggs', quantity: '1', store: 'Metro', store_decided: 1 }] }],
      purchased: [], inCart: [] }
  } };
  groceryState.usualStores = ['Costco', 'Metro'];
  groceryState.storesPromptDismissed = true;
  groceryState.tripRestored = true;
  groceryState.step = 'list';
  return groceryState.data;
}
function tripState() {
  return { step: groceryState.step, at: groTripStore(), stops: groceryState.tripStops,
    done: Object.keys(groceryState.tripDone || {}), index: groceryState.tripIndex,
    saved: STORE.get('pomona.trip.h1') || null };
}
"""


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. every trip screen has the exit, and it keeps the trip -------------


@_needs_node
@pytest.mark.parametrize("step", ["trip", "next", "wrap"])
def test_every_trip_screen_offers_finish_later_beside_its_own_action(step):
    out = _node("""
twoShops();
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripIndex = 0;
groceryState.tripDone = %s;
console.log(JSON.stringify(groDockHtml(groceryState.data, '%s')));
""" % ("{ Costco: true }" if step != "trip" else "{}", step))
    assert 'data-gro="trip-pause">Finish later</button>' in out, step
    assert '<div class="dock-row">' in out, "the link rides beside the action, Now's dock shape"
    assert out.count("gro-primary") <= 1, "Rule 5: the exit is a quiet link, never a second fill"
    assert 'class="dock-link"' in out


@_needs_node
def test_finish_later_from_where_next_lands_on_the_list_with_the_trip_kept():
    """The screen that had no way out. Its crumb still reopens the finished
    stop (the mis-tap guard is untouched); the dock link is the exit."""
    out = _node("""
twoShops();
startTripAt('Costco');
click({ gro: 'stop-done' });
settle(function () {
  const beforeCrumb = groHeadFor(groceryState.data, 'next').back;
  click({ gro: 'trip-pause' });
  const s = tripState();
  s.crumbOnNext = beforeCrumb;
  s.closes = closes();
  console.log(JSON.stringify(s));
});
""")
    assert out["crumbOnNext"] == "‹ Back to Costco", "the mis-tap guard is untouched"
    assert out["step"] == "list"
    assert out["stops"] == ["Costco", "Metro"], "the snapshot survives the exit"
    assert out["done"] == ["Costco"], "and so does what is behind us"
    assert out["closes"] == 1, "the finished stop was recorded once"
    assert out["saved"] is not None, "and the trip is mirrored for a relaunch"


@_needs_node
def test_finish_later_mid_stop_keeps_the_trolley_and_writes_nothing():
    """Leaving Costco half-ticked: nothing is committed, nothing is lost.
    What is in the trolley stays in the trolley (in_cart on the server),
    and no stop is recorded as finished."""
    out = _node("""
twoShops({ costcoInCart: [{ id: 2, item: 'Oats', quantity: '1', store: 'Costco', store_decided: 1 }] });
startTripAt('Costco');
click({ gro: 'trip-pause' });
const s = tripState();
s.posts = POSTS.length;
s.inCart = groceryState.data.stores.Costco.inCart.length;
console.log(JSON.stringify(s));
""")
    assert out["step"] == "list"
    assert out["posts"] == 0, "pausing writes nothing at all"
    assert out["inCart"] == 1
    assert out["done"] == [], "Costco is still open"
    assert out["stops"] == ["Costco", "Metro"]


# --- 2. the list says a trip is on, and offers continue / finish ----------


@_needs_node
def test_the_list_shows_the_paused_trip_and_offers_to_continue_or_finish():
    out = _node("""
twoShops();
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripIndex = 0;
groceryState.tripDone = { Costco: true };
console.log(JSON.stringify({
  line: groTripPausedLine(groceryState.data),
  dock: groDockHtml(groceryState.data, 'list')
}));
""")
    assert out["line"] == "Trip in progress · 1 stop left"
    assert 'data-gro="trip-resume">Continue the trip</button>' in out["dock"]
    assert 'class="dock-link" data-gro="trip-finish-now">Finish the trip</button>' in out["dock"]
    assert "start-trip" not in out["dock"], "a paused trip is never started again over the top of itself"
    assert out["dock"].count("gro-primary") == 1


@_needs_node
def test_the_list_says_nothing_about_a_trip_when_none_is_on():
    out = _node("""
twoShops();
console.log(JSON.stringify({ line: groTripPausedLine(groceryState.data), dock: groDockHtml(groceryState.data, 'list') }));
""")
    assert out["line"] == ""
    assert 'data-gro="start-trip">Start the trip</button>' in out["dock"]
    assert "trip-resume" not in out["dock"]


@_needs_node
def test_with_every_stop_done_finishing_is_the_lists_one_action():
    out = _node("""
twoShops();
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripDone = { Costco: true, Metro: true };
console.log(JSON.stringify({ line: groTripPausedLine(groceryState.data), dock: groDockHtml(groceryState.data, 'list') }));
""")
    assert out["line"] == "Trip in progress · every stop done"
    assert 'class="gro-primary" data-gro="trip-finish-now">Finish the trip</button>' in out["dock"]
    assert "trip-resume" not in out["dock"], "nothing left to continue into"


@_needs_node
def test_a_list_with_everything_in_the_cart_does_not_say_nothing_to_buy():
    """Every last thing ticked at Costco, "Done at Costco" never tapped, and
    the app put away. The list is empty of needed things, but the trolley
    is full — the empty moment must say so, not "nothing to buy"."""
    out = _node("""
twoShops({ costcoNeeded: false, costcoInCart: [{ id: 1, item: 'Rice', store: 'Costco', store_decided: 1 }] });
groceryState.data.stores.Metro.sections = [];
groceryState.tripStops = ['Costco'];
console.log(JSON.stringify({ body: groListHtml(groceryState.data), dock: groDockHtml(groceryState.data, 'list') }));
""")
    assert "Everything’s in the cart. Finish the trip to bring it home." in out["body"]
    assert "Nothing to buy" not in out["body"]
    assert "trip-resume" in out["dock"], "Costco is still open, with a full trolley"


# --- 3. continuing lands where it makes sense, and never on a done stop ---


@_needs_node
def test_continuing_after_done_at_costco_asks_where_next_rather_than_reopening_costco():
    """The loop itself. The index still names Costco after "Done at Costco"
    (WHERE NEXT never moves it), so the old resume reopened the finished
    stop — "Stop 2 of 2 · everything here is in the cart" — and the only
    way on was to finish it a second time."""
    out = _node("""
twoShops();
startTripAt('Costco');
click({ gro: 'stop-done' });
settle(function () {
  click({ gro: 'trip-pause' });
  const paused = groceryState.step;
  click({ gro: 'trip-resume' });
  const s = tripState();
  s.paused = paused;
  s.closes = closes();
  console.log(JSON.stringify(s));
});
""")
    assert out["paused"] == "list", "the exit actually left"
    assert out["step"] == "next", "the choice of what is left, not the stop just finished"
    assert out["done"] == ["Costco"]
    assert out["closes"] == 1, "resuming records no second trip"


@_needs_node
def test_continuing_mid_stop_returns_to_that_stop():
    out = _node("""
twoShops();
startTripAt('Costco');
click({ gro: 'next-stop', store: 'Metro' });
click({ gro: 'trip-pause' });
const paused = groceryState.step;
click({ gro: 'trip-resume' });
const s = tripState();
s.paused = paused;
console.log(JSON.stringify(s));
""")
    assert out["paused"] == "list"
    assert out["step"] == "trip"
    assert out["at"] == "Metro"
    assert out["index"] == 1


@_needs_node
def test_start_the_trip_over_a_paused_trip_continues_it_rather_than_resnapshotting():
    """Belt and braces: the button LIST shows is "Continue the trip", but if
    "Start the trip" is ever reached with a trip on, it must not forget
    which stops are already behind us."""
    out = _node("""
twoShops();
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripIndex = 0;
groceryState.tripDone = { Costco: true };
groceryState.tripBought = 2;
click({ gro: 'start-trip' });
const s = tripState();
s.bought = groceryState.tripBought;
console.log(JSON.stringify(s));
""")
    assert out["step"] == "next"
    assert out["done"] == ["Costco"]
    assert out["bought"] == 2


@_needs_node
def test_the_tab_bar_is_never_hidden_by_the_trip():
    """Leaving by the tab bar and coming back is the third exit, and it has
    always worked: nothing in the Grocery region touches the tab bar, and a
    tab switch changes no trip state."""
    block = _grocery_block()
    assert "tabBarEl" not in block
    assert "tab-bar" not in block
    out = _node("""
twoShops();
startTripAt('Costco');
click({ gro: 'stop-done' });
settle(function () {
  const before = JSON.stringify(tripState());
  activateTab('today');
  activateTab('grocery');
  console.log(JSON.stringify({ same: before === JSON.stringify(tripState()), step: groceryState.step }));
});
""")
    assert out["same"] is True
    assert out["step"] == "next", "the screen you left is the screen you come back to"


# --- 4. finishing from the list brings the trolley home, once ------------


@_needs_node
def test_finishing_from_the_list_commits_the_trolley_and_clears_the_trip():
    out = _node("""
twoShops({ costcoInCart: [{ id: 2, item: 'Oats', quantity: '1', store: 'Costco', store_decided: 1 }] });
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripDone = {};
groceryState.tripBought = 0;
groceryState.step = 'list';
click({ gro: 'trip-finish-now' });
settle(function () {
  const s = tripState();
  s.purchases = purchases();
  s.toast = lastToast();
  s.justFinished = groceryState.justFinishedTrip;
  console.log(JSON.stringify(s));
});
""")
    assert out["purchases"] == ["/api/grocery-list/2/status"], "what was in the trolley comes home"
    assert out["stops"] is None
    assert out["saved"] is None, "the mirror goes with it"
    assert out["toast"] == "Trip finished — 1 thing home."
    assert out["justFinished"] is True
    assert out["step"] == "list"


@_needs_node
def test_a_failed_finish_keeps_the_trip():
    out = _node("""
twoShops({ costcoInCart: [{ id: 2, item: 'Oats', quantity: '1', store: 'Costco', store_decided: 1 }] });
fetch = function () { return Promise.resolve({ ok: false, json: function () { return Promise.resolve({}); } }); };
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.step = 'list';
const el = click({ gro: 'trip-finish-now' });
settle(function () {
  console.log(JSON.stringify({ stops: groceryState.tripStops, enabled: !el.disabled, toast: lastToast() }));
});
""")
    assert out["stops"] == ["Costco", "Metro"]
    assert out["enabled"] is True
    assert out["toast"] == "Couldn't finish the trip — try again."


# --- 5. the trip survives a relaunch ---------------------------------------


@_needs_node
def test_a_paused_trip_comes_back_after_a_relaunch():
    """Fresh state, as after the installed app was killed: the mirror is
    read once, and the trip is exactly what it was."""
    out = _node("""
twoShops();
startTripAt('Costco');
click({ gro: 'stop-done' });
settle(function () {
  click({ gro: 'trip-pause' });
  const before = tripState();
  // Relaunch: page-view state gone, localStorage kept.
  groceryState.tripStops = null; groceryState.tripDone = {}; groceryState.tripIndex = 0;
  groceryState.tripBought = 0; groceryState.tripStartedAt = null; groceryState.tripRestored = false;
  groRestoreTrip();
  const after = tripState();
  after.dropped = groDropStaleTrip();
  after.line = groTripPausedLine(groceryState.data);
  console.log(JSON.stringify({ before: before, after: after }));
});
""")
    assert out["after"]["stops"] == out["before"]["stops"] == ["Costco", "Metro"]
    assert out["after"]["done"] == ["Costco"]
    assert out["after"]["dropped"] is False
    assert out["after"]["line"] == "Trip in progress · 1 stop left"


@_needs_node
def test_the_mirror_is_read_once_and_never_over_a_live_trip():
    out = _node("""
twoShops();
STORE.set('pomona.trip.h1', JSON.stringify({ stops: ['Metro'], index: 0, done: {}, startedAt: Date.now() }));
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripRestored = false;
groRestoreTrip();
const live = groceryState.tripStops.slice();
groceryState.tripStops = null;
groRestoreTrip();
console.log(JSON.stringify({ live: live, second: groceryState.tripStops }));
""")
    assert out["live"] == ["Costco", "Metro"], "a live trip is never replaced by the mirror"
    assert out["second"] is None, "and the mirror is read once per page view"


@_needs_node
def test_a_step_change_before_the_first_load_does_not_wipe_the_mirror():
    """An approval's "Open the list" (groSetScreen) or the first sort landing
    calls goGroceryStep before the list has loaded. With no trip in memory
    yet, that must not remove the trip a relaunch is about to restore."""
    out = _node("""
twoShops();
STORE.set('pomona.trip.h1', JSON.stringify({ stops: ['Metro'], index: 0, done: {}, startedAt: Date.now() }));
groceryState.tripRestored = false;
goGroceryStep('list');
const stillThere = STORE.get('pomona.trip.h1') != null;
groRestoreTrip();
console.log(JSON.stringify({ stillThere: stillThere, stops: groceryState.tripStops }));
""")
    assert out["stillThere"] is True
    assert out["stops"] == ["Metro"]


@_needs_node
def test_a_trip_older_than_three_days_is_dropped_not_resumed():
    out = _node("""
twoShops();
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripStartedAt = Date.now() - GRO_TRIP_KEEP_MS - 1000;
groSaveTrip();
const dropped = groDropStaleTrip();
console.log(JSON.stringify({ dropped: dropped, stops: groceryState.tripStops, saved: STORE.get('pomona.trip.h1') || null,
  keepMs: GRO_TRIP_KEEP_MS }));
""")
    assert out["dropped"] is True
    assert out["stops"] is None
    assert out["saved"] is None
    assert out["keepMs"] == 3 * 24 * 60 * 60 * 1000


@_needs_node
def test_a_trip_saved_before_the_household_was_known_is_adopted_once():
    out = _node("""
twoShops();
STORE.set('pomona.trip.hx', JSON.stringify({ stops: ['Metro'], index: 0, done: {}, startedAt: Date.now() }));
groceryState.tripRestored = false;
groRestoreTrip();
console.log(JSON.stringify({ stops: groceryState.tripStops, adopted: STORE.get('pomona.trip.h1') != null, orphan: STORE.has('pomona.trip.hx') }));
""")
    assert out["stops"] == ["Metro"]
    assert out["adopted"] is True
    assert out["orphan"] is False


@_needs_node
def test_a_broken_mirror_is_ignored():
    """Not JSON, no stops, or no start time (so the three-day rule could
    never apply to it): none of these is resumed."""
    out = _node("""
twoShops();
STORE.set('pomona.trip.h1', '{not json');
groceryState.tripRestored = false;
groRestoreTrip();
const a = groceryState.tripStops;
STORE.set('pomona.trip.h1', JSON.stringify({ stops: [] }));
groceryState.tripRestored = false;
groRestoreTrip();
const b = groceryState.tripStops;
STORE.set('pomona.trip.h1', JSON.stringify({ stops: ['Metro'], index: 0, done: {} }));
groceryState.tripRestored = false;
groRestoreTrip();
console.log(JSON.stringify({ a: a, b: b, c: groceryState.tripStops }));
""")
    assert out["a"] is None
    assert out["b"] is None
    assert out["c"] is None, "a trip with no start time can never age out, so it is not resumed"


@_needs_node
def test_a_stop_the_list_no_longer_has_does_not_claim_a_full_trolley():
    """A trip paused across a new week's approval, reached by the back
    gesture: the store is gone from the list. TRIP used to say "Everything
    here is in the cart" over an empty trolley (found on review)."""
    out = _node("""
twoShops();
groceryState.tripStops = ['Loblaws', 'Metro'];
groceryState.tripIndex = 0;
const gone = groTripHtml(groceryState.data);
groceryState.data.stores.Loblaws = { sections: [], purchased: [], inCart: [{ id: 9, item: 'Jam', store: 'Loblaws', store_decided: 1 }] };
const full = groTripHtml(groceryState.data);
console.log(JSON.stringify({ gone: gone, full: full }));
""")
    assert "Nothing left on this stop." in out["gone"]
    assert "Everything here is in the cart." not in out["gone"]
    assert "Everything here is in the cart." in out["full"]


# --- 6. source markers for the wiring the harness cannot reach ------------


def test_the_list_load_restores_then_drops_stale_then_asks_about_leftovers():
    """Order matters: the mirror is read before groMaybeCarryFirst can call
    goGroceryStep (which saves), and the stale check runs on every load so
    it covers a tab left open for days as well as a relaunch. (It was
    groMaybeSortFirst until 2026-09-15; the list never opens SORT on its
    own now, and the leftovers question is the only automatic step.)"""
    load = SHELL_JS[SHELL_JS.index("async function loadGrocery("):SHELL_JS.index("function refreshGroceryPanel(")]
    # The leftovers call gained a guard on 2026-09-17: a BACKGROUND re-read
    # (a chat turn, another tab's tap) does everything a load does except
    # open a step, because navigating out from under somebody cost them
    # their scroll and a half-typed add row. The ORDER this test is about
    # is unchanged — the mirror is still read, and the stale check still
    # run, before anything can call goGroceryStep.
    assert ("groRestoreTrip();\n      groDropStaleTrip();\n    }\n"
            "    if (!(opts && opts.background)) groMaybeCarryFirst();") in load


def test_the_band_carries_the_paused_trip_line_and_the_dock_row_fits_shops_buttons():
    render = SHELL_JS[SHELL_JS.index("function renderGrocery("):SHELL_JS.index("function groCaptureStoresPromptInput(")]
    assert "sub: groTripPausedLine(data)" in render
    assert ".dock-row .gro-primary, .dock-row .gro-secondary { flex: 1 1 auto; width: auto; min-width: 0; }" in SHELL_CSS


def test_finish_the_trip_is_one_function_wherever_it_is_reached():
    """WRAP UP's button and LIST's link end the trip the same way, so the two
    cannot drift about what "finished" means."""
    handlers = SHELL_JS[SHELL_JS.index("function onGroceryClick("):]
    assert "case 'finish-trip':\n        groFinishTrip(el);" in handlers
    assert "case 'trip-finish-now':\n        groFinishTrip(el);" in handlers
    assert SHELL_JS.count("function groFinishTrip(") == 1
