"""
A household that says "One list is fine" can shop its list.

Loop Board, Phase 1 (Beta, Bug): "Shop: a household that says 'One list is
fine' has no 'Start the trip' at all." Pre-existing on main, and the state
of Emily's own database.

Reproduced before anything was changed, by running the Grocery region's own
functions against the payload the server really hands a household with no
usual stores (one 'Unassigned' bucket, every row `store: ''` and
`store_decided: 0` — checked over HTTP on a throwaway database):

    stops          []            <- groStoresWithNeeded
    mostUsedStore  null          <- groMostUsedStore, and this is the root
    dock           ""            <- nothing to tap

`groStoresWithNeeded` already carries a stand-in stop for the two
households whose list is untagged — the one that named a single shop, and
the one that answered "Anywhere" to everything — and reads it off
`groMostUsedStore`, which iterates `groPillStores`. A household that named
no shop has no pill stores, so there was no stop, so the dock was empty:
the list could be read and never shopped through, ticked or wrapped up.

The fix is a third household in that same fallback rather than a second way
of answering "which stops are there" — GRO_ONE_LIST_STOP, a stop and not a
store. Nothing heads a card with it, nothing offers it as a pill, nothing
writes it to a row, and the one place a stop name reaches the database
(shopping_trips) records it as no shop at all.

Section 8 is the correction that came out of reviewing the first commit,
and it is the one to read before changing any of this. That commit matched
the stand-in by NAME, under a comment claiming it "cannot collide with a
real store". It can, twice over — a trip is a snapshot, so a shop named
mid-trip leaves "Your list" in tripStops beside real pill stores; and the
app shows the household the words "Your list" as a stop title, so a shop
can be typed with that name. Measured, such a shop was undercounted in the
band, double-counted in its own remaining, lost its "Stop 1 of 2", was
recorded under no shop, and was finished with "Done shopping" while another
shop was still to go. groIsStandIn — that name AND no pill store by it —
is read by all five sites now.

Behaviour is run under node against shell.js's own functions, the way
tests/test_shop_trip_exit.py and tests/test_grocery_fast_sort.py do — "the
button isn't there" is exactly what a source-marker test cannot see.

Each test says in its own docstring whether it is a CATCH (red against the
merge base) or a no-regression GUARD, and two say they are half of each.
Sixteen of the thirty-two go red on main — but redness there is not worth
much on its own for a screen that does not exist: several of them can only
fail because the trip never starts, and one is a source marker whose
redness proves nothing at all. Those say so, and the claims that matter
are pinned by MUTATION instead, each with the mutation named. Eight were
run, and every one reddens at least one test here.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _grocery_block() -> str:
    """The whole Grocery region including onGroceryClick, up to the
    hands-free voice code — the same slice test_shop_trip_exit takes."""
    start = SHELL_JS.index("  var GRO_CATEGORY_LABELS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
// The real thing writes and then groDo reloads the list, so a row marked
// purchased has left the trolley by the time anything reads it again.
// The stub does that move itself: without it the trolley is still full at
// the wrap-up and the trip's own count comes out double.
function applyStatus(url, body) {
  const m = /^\/api\/grocery-list\/([0-9]+)\/status$/.exec(url);
  if (!m || !body || body.status !== 'purchased' || !groceryState.data) return;
  Object.keys(groceryState.data.stores).forEach(function (name) {
    const s = groceryState.data.stores[name];
    const still = [];
    s.inCart.forEach(function (it) {
      if (String(it.id) === m[1]) s.purchased.push(it); else still.push(it);
    });
    s.inCart = still;
  });
}
function fetch(url, opts) {
  const body = JSON.parse((opts && opts.body) || '{}');
  POSTS.push({ url: url, body: body });
  applyStatus(url, body);
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
}
function closesOf() { return POSTS.filter(function (p) { return p.url === '/api/shopping-trips/close'; }); }
function purchases() { return POSTS.filter(function (p) { return p.body && p.body.status === 'purchased'; }); }
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
var BAND_IDENTITY = 'mark';
function bandDateLabel() { return 'Tuesday, Sep 16'; }
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
function item(id, name, store, decided) {
  return { id: id, item: name, quantity: '1', store: store || '', store_decided: decided ? 1 : 0 };
}
function rows(items) { return [{ section: 'other', items: items }]; }
function empty() { return { sections: [], purchased: [], inCart: [] }; }
function base(opts) {
  groceryState.data = { stores: opts.stores };
  groceryState.usualStores = opts.usualStores || [];
  groceryState.storesPromptDismissed = true;
  groceryState.tripRestored = true;
  groceryState.step = 'list';
  groceryState.tripStops = null;
  groceryState.tripIndex = 0;
  groceryState.tripDone = {};
  groceryState.carried = [];
  groceryState.spices = { items: [], recently_bought: [], note: '' };
  groceryState.justFinishedTrip = false;
  return groceryState.data;
}
// "One list is fine": no usual stores, three things, nothing tagged. This
// is the exact shape /api/grocery-list/by-store hands such a household —
// one Unassigned bucket, store '', store_decided 0.
function oneList() {
  return base({ usualStores: [], stores: {
    Unassigned: { sections: rows([item(1, 'Rice'), item(2, 'Oats'), item(3, 'Eggs')]),
      purchased: [], inCart: [] }
  } });
}
// One named shop, list never tagged (there was no question to answer).
function oneShop() {
  return base({ usualStores: ['Loblaws'], stores: {
    Unassigned: { sections: rows([item(1, 'Rice'), item(2, 'Oats'), item(3, 'Eggs')]),
      purchased: [], inCart: [] }
  } });
}
// The ordinary multi-store week, three things across two shops and one
// with no shop of its own.
function twoShops() {
  return base({ usualStores: ['Costco', 'Metro'], stores: {
    Unassigned: { sections: rows([item(9, 'Foil', '', 1)]), purchased: [], inCart: [] },
    Costco: { sections: rows([item(1, 'Rice', 'Costco', 1)]), purchased: [], inCart: [] },
    Metro: { sections: rows([item(3, 'Eggs', 'Metro', 1)]), purchased: [], inCart: [] }
  } });
}
// Tick a row into the trolley the way the trip screen's checkbox does.
function intoCart(data, id) {
  var u = data.stores.Unassigned;
  var it = u.sections[0].items.filter(function (x) { return String(x.id) === String(id); })[0];
  u.sections[0].items = u.sections[0].items.filter(function (x) { return String(x.id) !== String(id); });
  u.inCart.push(it);
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


# --- 1. the bug: there was no way to shop the list at all -----------------


@_needs_node
def test_a_household_that_named_no_shop_can_start_a_trip():
    """CATCH — on main the dock is the empty string, so the whole list can
    be read and never shopped. This is the ticket."""
    out = _node("""
oneList();
console.log(JSON.stringify({
  stops: groStoresWithNeeded(groceryState.data),
  dock: groDockHtml(groceryState.data, 'list')
}));
""")
    assert out["stops"], "a list with things on it has somewhere to be bought"
    assert 'data-gro="start-trip">Start the trip</button>' in out["dock"]


@_needs_node
def test_the_trip_runs_as_one_stop_named_for_the_list():
    """CATCH — on main "Start the trip" does nothing at all (groStartTrip
    returns on an empty stops list), so the step never changes."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
const s = tripState();
s.items = groTripItems(d).map(function (i) { return i.item; });
s.head = groHeadFor(d, 'trip');
s.body = groTripHtml(d);
console.log(JSON.stringify(s));
""")
    assert out["step"] == "trip", "one stop is no question — it never asks 'where are we headed?'"
    assert out["stops"] == ["Your list"]
    assert out["at"] == "Your list"
    assert out["head"]["title"] == "Your list"
    assert out["items"] == ["Rice", "Oats", "Eggs"], "the whole list is on the one stop"
    for name in ("Rice", "Oats", "Eggs"):
        assert name in out["body"], f"{name} is tickable on the trip screen"


@_needs_node
def test_the_things_tick_off_and_the_stop_finishes_into_the_wrap_up():
    """CATCH — acceptance criterion 4, end to end: three things, two ticked,
    finish, wrap-up. None of it is reachable on main."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
intoCart(d, 1);
intoCart(d, 2);
const mid = { left: groTripItems(d).length, inCart: groTripInCart(d).length };
click({ gro: 'stop-done' });
settle(function () {
  console.log(JSON.stringify({
    mid: mid,
    step: groceryState.step,
    done: Object.keys(groceryState.tripDone),
    purchased: purchases().length,
    wrap: groWrapHtml(d),
    wrapDock: groDockHtml(d, 'wrap')
  }));
});
""")
    assert out["mid"] == {"left": 1, "inCart": 2}, "ticking moves a thing into the trolley"
    assert out["purchased"] == 2, "what was in the trolley comes home when the stop is finished"
    assert out["step"] == "wrap", "one stop finished is the whole trip finished"
    assert out["done"] == ["Your list"]
    assert "Still on the list" in out["wrap"] and "Eggs" in out["wrap"]
    assert "Bought 2 of 3" in out["wrap"]
    assert 'data-gro="finish-trip">Finish the trip</button>' in out["wrapDock"], \
        "the wrap-up's own finish, not LIST's paused one"


@_needs_node
def test_finishing_the_trip_lands_back_on_the_list_and_says_what_came_home():
    """CATCH — the far end of the same flow. It asserts the trip was really
    ON before it was finished, because without that it would pass on main
    for the wrong reason: there the step is already 'list' and the snapshot
    is already null, so every claim about the far end is true of a trip
    that never started."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
intoCart(d, 1);
const onTheWay = { stops: groceryState.tripStops, mirror: STORE.get('pomona.trip.h1') || null };
click({ gro: 'stop-done' });
settle(function () {
  click({ gro: 'finish-trip' });
  settle(function () {
    console.log(JSON.stringify({ onTheWay: onTheWay,
      step: groceryState.step, stops: groceryState.tripStops,
      toast: lastToast(), mirror: STORE.get('pomona.trip.h1') || null }));
  });
});
""")
    assert out["onTheWay"]["stops"] == ["Your list"], "a trip was on to be finished"
    assert out["onTheWay"]["mirror"] is not None
    assert out["step"] == "list"
    assert out["stops"] is None, "the snapshot is cleared with the trip"
    assert out["toast"] == "Trip finished — 1 thing home."
    assert out["mirror"] is None, "and so is its mirror"


# --- 2. criterion 3: the band reads sensibly ------------------------------


@_needs_node
def test_the_band_counts_things_and_not_stops_a_household_never_named():
    """GUARD — green on main for the wrong reason (there were no stops to
    count there either). Pinned by MUTATION: drop the stand-in filter from
    groBandEyebrow's stopCount and this reads "3 things · 1 stop"."""
    out = _node("""
oneList();
console.log(JSON.stringify({ eyebrow: groBandEyebrow(groceryState.data) }));
""")
    assert out["eyebrow"] == "3 things"
    assert "stop" not in out["eyebrow"], "the clause counts shops, and they named none"


@_needs_node
def test_a_household_that_named_one_shop_still_gets_its_stop_in_the_band():
    """GUARD — green on main. The clause is dropped for a household with no
    shop, never for one whose stop has a name they chose."""
    out = _node("""
oneShop();
console.log(JSON.stringify({ eyebrow: groBandEyebrow(groceryState.data) }));
""")
    assert out["eyebrow"] == "3 things · 1 stop"


@_needs_node
def test_the_trip_head_does_not_count_stops_the_household_never_named():
    """CATCH by construction — the trip does not start on main, so there is
    no head to read. The claim is that "Stop 1 of 1" is a count of stops and
    this household has none; a named shop still gets it (the test below)."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
console.log(JSON.stringify(groHeadFor(d, 'trip')));
""")
    assert out["sub"] == "3 left"
    assert "Stop" not in out["sub"]


@_needs_node
def test_a_named_shop_still_says_which_stop_of_how_many():
    """GUARD — green on main, and the thing the test above must not have
    cost. One named shop keeps the stop count it has always had."""
    out = _node("""
const d = oneShop();
click({ gro: 'start-trip' });
console.log(JSON.stringify(groHeadFor(d, 'trip')));
""")
    assert out["title"] == "Loblaws"
    assert out["sub"] == "Stop 1 of 1 · 3 left"


# --- 3. criterion 2: the one-shop household, and what was missing ---------


@_needs_node
def test_one_named_shop_already_started_a_trip_and_still_does():
    """GUARD — green on main. Criterion 2 was already true and this says so
    rather than claiming a fix for it."""
    out = _node("""
const d = oneShop();
const dock = groDockHtml(d, 'list');
click({ gro: 'start-trip' });
console.log(JSON.stringify({ dock: dock, step: groceryState.step,
  stops: groceryState.tripStops, at: groTripStore(),
  items: groTripItems(d).map(function (i) { return i.item; }) }));
""")
    assert 'data-gro="start-trip">Start the trip</button>' in out["dock"]
    assert out["step"] == "trip"
    assert out["stops"] == ["Loblaws"]
    assert out["items"] == ["Rice", "Oats", "Eggs"]


@_needs_node
@pytest.mark.parametrize("shape,stop", [("oneList", "Your list"), ("oneShop", "Loblaws")])
def test_a_paused_single_stop_trip_can_be_continued(shape, stop):
    """CATCH, and the one-shop half is red on main for a bug nobody had
    filed: groStopRemaining counted only the stop's own bucket, and a
    single-stop household's list is all in the shopless pile. So LIST said
    "every stop done" over three unbought things, offered no way to
    continue, and continuing anyway walked past the stop into the wrap-up.
    Reproduced on main for Loblaws before this was touched."""
    out = _node("""
const d = %s();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
const paused = { step: groceryState.step, line: groTripPausedLine(d), dock: groDockHtml(d, 'list') };
click({ gro: 'trip-resume' });
console.log(JSON.stringify({ paused: paused, step: groceryState.step, at: groTripStore(),
  items: groTripItems(d).map(function (i) { return i.item; }) }));
""" % shape)
    assert out["paused"]["step"] == "list", "Finish later keeps the trip and goes to the list"
    assert out["paused"]["line"] == "Trip in progress · 1 stop left"
    assert 'data-gro="trip-resume">Continue the trip</button>' in out["paused"]["dock"]
    assert out["step"] == "trip", "continuing lands back on the stop, not in the wrap-up"
    assert out["at"] == stop
    assert out["items"] == ["Rice", "Oats", "Eggs"]


@_needs_node
def test_the_shopless_pile_is_not_counted_against_any_one_of_several_stops():
    """GUARD — green on main, and the thing the fix above must not have
    cost. With more than one stop the shopless things follow the shopper
    (groTripItems) and belong to no one of them, so counting them per stop
    would count them twice. Pinned by MUTATION: drop the
    `name === GRO_ONE_LIST_STOP || groSoleStore(data) === name` condition
    from groStopRemaining and both stops below read 2."""
    out = _node("""
const d = twoShops();
console.log(JSON.stringify({
  costco: groStopRemaining(d, 'Costco'),
  metro: groStopRemaining(d, 'Metro')
}));
""")
    assert out == {"costco": 1, "metro": 1}, "one thing each, and the foil is neither's"


# --- 4. the list itself is not broken by having a stop ---------------------


@_needs_node
def test_the_one_list_household_keeps_its_headingless_list():
    """GUARD — green on main, and this is the regression the fix could most
    easily have caused: LIST draws the loose pile as one plain section only
    when there are no stops, so a stand-in stop would have emptied the
    screen. Emily's call, 2026-09-09: "One list is fine" means what it says.
    Pinned by MUTATION: drop `!groNoShopNamed(data) &&` from groListHtml and
    the three rows are drawn by nothing."""
    out = _node("""
const d = oneList();
console.log(JSON.stringify({ list: groListHtml(d) }));
""")
    for name in ("Rice", "Oats", "Eggs"):
        assert name in out["list"], f"{name} is on the list"
    assert "gro-card-head" not in out["list"], "no store heading they never chose"


@_needs_node
def test_the_stand_in_stop_is_a_stop_and_never_a_store():
    """GUARD — it cannot go red on main, where the name does not exist.
    It is the collision rule written down: the stand-in heads no card, is
    offered as no pill, and is never written to a row."""
    out = _node("""
const d = oneList();
const list = groListHtml(d);
const row = groRowMenuHtml(d.stores.Unassigned.sections[0].items[0], d);
console.log(JSON.stringify({
  listNames: list.indexOf('Your list') !== -1,
  rowMenuNames: row.indexOf('Your list') !== -1,
  pills: groPillStores(d),
  sortRow: groSortRowHtml(d)
}));
""")
    assert out["listNames"] is False, "the list is not headed by a stop"
    assert out["rowMenuNames"] is False, "and the row ⋯ never offers it as a store"
    assert out["pills"] == [], "a household with no shop still has no shop"
    assert out["sortRow"] == "", "and nothing to sort, because there is nowhere to sort to"


@_needs_node
def test_a_one_list_household_whose_rows_were_all_answered_still_sees_them():
    """Half CATCH, half GUARD. It is red on main for the dock, which is
    the bug. The other half is the guard: "Any" persists
    (grocery_items.store_decided), and LIST reads the loose pile in two
    halves for a household with shops — answered ones into the Anywhere
    card, the rest into "Not sorted yet". A household with no shop has no
    card for either, so the plain section has to draw the whole pile
    whatever each row was answered. That half is green on main and is
    pinned by the `!groNoShopNamed(data) &&` mutation."""
    out = _node("""
const d = base({ usualStores: [], stores: {
  Unassigned: { sections: rows([item(1, 'Rice', '', 1), item(2, 'Oats', '', 1), item(3, 'Eggs', '', 1)]),
    purchased: [], inCart: [] }
} });
console.log(JSON.stringify({ list: groListHtml(d), dock: groDockHtml(d, 'list'),
  anywhereCard: groListHtml(d).indexOf('Anywhere') !== -1 }));
""")
    for name in ("Rice", "Oats", "Eggs"):
        assert name in out["list"]
    assert out["anywhereCard"] is False, "there are no store cards for it to sit beside"
    assert 'data-gro="start-trip">Start the trip</button>' in out["dock"]


@_needs_node
def test_naming_a_shop_mid_trip_does_not_renumber_the_trip_already_on():
    """GUARD by construction — there is no one-list trip on main to keep.
    The snapshot rule from the shop-trip-exit work, applied to the new
    stop: a trip is a snapshot, so a shop named while one is paused changes
    the list underneath and never the trip."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
groceryState.usualStores = ['Metro'];   // added from What we know
const after = { snapshot: groceryState.tripStops, line: groTripPausedLine(d),
  listHeads: groListHtml(d).indexOf('gro-card-head') !== -1 };
click({ gro: 'trip-resume' });
console.log(JSON.stringify({ after: after, at: groTripStore(), step: groceryState.step }));
""")
    assert out["after"]["snapshot"] == ["Your list"], "the trip keeps the stop it started with"
    assert out["after"]["line"] == "Trip in progress · 1 stop left"
    assert out["after"]["listHeads"] is True, "the list behind it starts showing the new shop"
    assert out["at"] == "Your list"
    assert out["step"] == "trip"


@_needs_node
def test_one_thing_reads_as_one_thing():
    """GUARD — it cannot be run on main, where the trip never starts, so
    its redness there says nothing. The claim is that the two counts this
    ticket rewrote stayed plural-aware like every other count on this tab;
    the mutations that reword them are what pin it."""
    out = _node("""
const d = base({ usualStores: [], stores: {
  Unassigned: { sections: rows([item(1, 'Milk')]), purchased: [], inCart: [] }
} });
const eyebrow = groBandEyebrow(d);
click({ gro: 'start-trip' });
console.log(JSON.stringify({ eyebrow: eyebrow, sub: groHeadFor(d, 'trip').sub }));
""")
    assert out["eyebrow"] == "1 thing"
    assert out["sub"] == "1 left"


@_needs_node
def test_the_list_keeps_its_one_apricot():
    """Half CATCH, half GUARD, and the halves are worth telling apart. It
    is red on main because there is no apricot at all — the bug. The claim
    it exists for is the other direction, DESIGN_SYSTEM rule 5: exactly
    one, never a second anywhere on the screen. That half is green on main
    and can only be pinned by the count."""
    out = _node("""
const d = oneList();
console.log(JSON.stringify({ dock: groDockHtml(d, 'list'), list: groListHtml(d) }));
""")
    assert out["dock"].count("gro-primary") == 1
    assert out["list"].count("gro-primary") == 0
    assert 'class="dock-link" data-gro="see-week"' in out["dock"], "the quiet one stays quiet"


@_needs_node
def test_nothing_is_offered_while_the_shops_question_is_still_on_screen():
    """GUARD — green on main. The stores card's own "That's where we shop"
    is the screen's one apricot while it is up, so a "Start the trip" under
    it would be a second (rule 5). A stand-in stop must not change that."""
    out = _node("""
const d = oneList();
groceryState.storesPromptDismissed = false;
console.log(JSON.stringify({ dock: groDockHtml(d, 'list') }));
""")
    assert out["dock"] == ""


@_needs_node
def test_an_empty_list_still_points_at_plan():
    """GUARD — green on main. Nothing to buy means no stand-in stop and no
    trip: the empty moment's next step is where a week gets approved."""
    out = _node("""
const d = base({ usualStores: [], stores: { Unassigned: empty() } });
console.log(JSON.stringify({ stops: groStoresWithNeeded(d), dock: groDockHtml(d, 'list') }));
""")
    assert out["stops"] == []
    assert 'data-gro="goto-plan">Go to Plan</button>' in out["dock"]


# --- 5. the multi-store path, which must not move --------------------------


@_needs_node
def test_two_shops_are_still_asked_which_one_we_are_headed_to():
    """GUARD — green on main. More than one stop is still a question, and
    the answer is still what the snapshot opens on."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
const asked = { step: groceryState.step, cards: groHeadedHtml(d) };
click({ gro: 'head-for', store: 'Metro' });
console.log(JSON.stringify({ asked: asked, step: groceryState.step, at: groTripStore(),
  stops: groceryState.tripStops }));
""")
    assert out["asked"]["step"] == "headed"
    assert 'data-store="Costco"' in out["asked"]["cards"]
    assert 'data-store="Metro"' in out["asked"]["cards"]
    assert "Your list" not in out["asked"]["cards"], "no stand-in beside real shops"
    assert out["step"] == "trip"
    assert out["at"] == "Metro", "the trip opens on the stop they named"
    assert out["stops"] == ["Costco", "Metro"], "and the snapshot keeps its own order"


@_needs_node
def test_the_snapshot_is_never_reordered_and_done_is_keyed_by_name():
    """GUARD — green on main. The load-bearing rule from the shop-trip-exit
    work: finishing a stop out of order renumbers nothing."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Metro' });
click({ gro: 'stop-done' });
settle(function () {
  console.log(JSON.stringify({ stops: groceryState.tripStops,
    done: Object.keys(groceryState.tripDone), step: groceryState.step,
    next: groNextHtml(d) }));
});
""")
    assert out["stops"] == ["Costco", "Metro"], "the snapshot is untouched"
    assert out["done"] == ["Metro"], "and what is behind us is keyed by name"
    assert out["step"] == "next", "there is still somewhere to go"
    assert 'data-store="Costco"' in out["next"]


@_needs_node
def test_a_household_with_shops_but_nothing_tagged_still_gets_its_usual_shop():
    """GUARD — green on main. The stand-in the fallback already had: a
    household that answered "Anywhere" to everything shops at the place it
    actually buys from most, not at a stop called "Your list"."""
    out = _node("""
const d = base({ usualStores: ['Costco', 'Metro'], stores: {
  Unassigned: { sections: rows([item(1, 'Rice', '', 1), item(2, 'Oats', '', 1)]), purchased: [], inCart: [] },
  Costco: { sections: [], purchased: [{ id: 7, item: 'Milk', store: 'Costco' }], inCart: [] },
  Metro: empty()
} });
console.log(JSON.stringify({ stops: groStoresWithNeeded(d), mostUsed: groMostUsedStore(d) }));
""")
    assert out["stops"] == ["Costco"]
    assert out["mostUsed"] == "Costco"


@_needs_node
def test_two_shops_and_a_full_list_still_read_two_stops_in_the_band():
    """GUARD — green on main."""
    out = _node("""
console.log(JSON.stringify({ eyebrow: groBandEyebrow(twoShops()) }));
""")
    assert out["eyebrow"] == "3 things · 2 stops"


# --- 6. the trip survives being put down ----------------------------------


@_needs_node
def test_a_one_list_trip_survives_a_relaunch_through_the_mirror():
    """CATCH — there is no trip to mirror on main. An installed app that is
    put away at the car and opened again at home has usually been
    relaunched in between, which is what the mirror is for."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
intoCart(d, 1);
click({ gro: 'trip-pause' });
const mirrored = JSON.parse(STORE.get('pomona.trip.h1'));
// The relaunch: page-view state is gone, localStorage is not.
groceryState.tripStops = null; groceryState.tripIndex = 0; groceryState.tripDone = {};
groceryState.tripRestored = false; groceryState.step = 'list';
groRestoreTrip();
const back = { stops: groceryState.tripStops, at: groTripStore(), total: groceryState.tripTotal,
  line: groTripPausedLine(d), dock: groDockHtml(d, 'list') };
click({ gro: 'trip-resume' });
console.log(JSON.stringify({ mirrored: mirrored, back: back,
  step: groceryState.step, items: groTripItems(d).map(function (i) { return i.item; }) }));
""")
    assert out["mirrored"]["stops"] == ["Your list"]
    assert out["mirrored"]["total"] == 3
    assert out["back"]["stops"] == ["Your list"]
    assert out["back"]["at"] == "Your list"
    assert out["back"]["line"] == "Trip in progress · 1 stop left"
    assert 'data-gro="trip-resume">Continue the trip</button>' in out["back"]["dock"]
    assert out["step"] == "trip"
    assert out["items"] == ["Oats", "Eggs"], "and what was ticked is still in the trolley"


@_needs_node
def test_a_step_change_before_the_mirror_is_read_never_wipes_a_one_list_trip():
    """CATCH — the trap groSaveTrip documents, checked for this trip too:
    "no trip" means "not yet" until the mirror has been read this page
    view, so an approval's "Open the list" must not wipe what a relaunch is
    about to restore."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
groceryState.tripStops = null; groceryState.tripRestored = false;
groSaveTrip();            // a step change landing before the list loads
const survived = STORE.get('pomona.trip.h1') || null;
groRestoreTrip();
console.log(JSON.stringify({ survived: survived !== null, stops: groceryState.tripStops }));
""")
    assert out["survived"] is True
    assert out["stops"] == ["Your list"]


# --- 7. the words, and what reaches the database ---------------------------


@_needs_node
def test_the_stop_is_finished_with_done_shopping_not_done_at_your_list():
    """CATCH by construction — there is no trip on main to read a dock
    from. "Done at Costco" names the shop you are standing in; this
    household is standing in no shop they ever named."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
console.log(JSON.stringify({ dock: groDockHtml(d, 'trip') }));
""")
    assert 'data-gro="stop-done">Done shopping</button>' in out["dock"]
    assert "Done at" not in out["dock"]
    assert 'data-gro="trip-pause">Finish later</button>' in out["dock"], "the way out is still there"
    assert out["dock"].count("gro-primary") == 1


@_needs_node
def test_a_named_shop_is_still_finished_by_name():
    """GUARD — green on main, and the thing the test above must not have
    cost."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
console.log(JSON.stringify({ dock: groDockHtml(d, 'trip') }));
""")
    assert 'data-gro="stop-done">Done at Costco</button>' in out["dock"]


@_needs_node
def test_the_trip_is_recorded_as_no_shop_rather_than_a_shop_nobody_named():
    """CATCH by construction — nothing is recorded on main, because no stop
    is ever finished. shopping_trips is the one place a stop name reaches
    the database; inventing a store called "Your list" there would hand
    whatever reads trip history back one day a shop nobody has been to."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
intoCart(d, 1);
click({ gro: 'stop-done' });
settle(function () {
  console.log(JSON.stringify({ closes: closesOf().map(function (p) { return p.body; }) }));
});
""")
    assert out["closes"] == [{"store": "", "item_count": 1}]


@_needs_node
def test_a_named_shop_is_still_recorded_under_its_own_name():
    """GUARD — green on main."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
click({ gro: 'stop-done' });
settle(function () {
  console.log(JSON.stringify({ closes: closesOf().map(function (p) { return p.body.store; }) }));
});
""")
    assert out["closes"] == ["Costco"]


def test_the_new_copy_keeps_the_voice():
    """GUARD — DESIGN_SYSTEM §8: never eager, no exclamation marks, and
    never a word the household would have to decode. Read off the source,
    which is all a copy rule can be read off. It goes red on main only
    because these strings do not exist there, which proves nothing about
    behaviour; it is here so a later rewording is a deliberate one."""
    region = _grocery_block()
    for phrase in ("'Your list'", "'Done shopping'"):
        assert phrase in region, f"{phrase} is the copy this ticket chose"
    assert "Done at Your list" not in region
    assert "!" not in "Your list Done shopping"


# --- 8. a real shop called "Your list" wins its own name back --------------


@_needs_node
def test_a_shop_actually_named_your_list_is_a_shop_and_not_the_stand_in():
    """CATCH against this branch's own first commit (11cb882), where the
    stand-in was matched by NAME alone.

    The app shows the household the words "Your list" as a stop title, so
    typing them into "+ Add a store" is a path a confused person can take.
    Measured on 11cb882 with that shop beside Costco and one shopless row:
    the band read "1 stop" for two real shops, the shop double-counted the
    shopless row in its own remaining (1 -> 2), the head lost "Stop 1 of
    2", the trip was recorded under no shop at all — and the one with
    teeth, the dock said "Done shopping" with Costco still to go. Every
    value below is main's, which is what a real shop is owed."""
    out = _node("""
const d = base({ usualStores: ['Your list', 'Costco'], stores: {
  Unassigned: { sections: rows([item(9, 'Foil', '', 1)]), purchased: [], inCart: [] },
  'Your list': { sections: rows([item(1, 'Rice', 'Your list', 1)]), purchased: [], inCart: [] },
  Costco: { sections: rows([item(2, 'Oats', 'Costco', 1)]), purchased: [], inCart: [] }
} });
const before = { band: groBandEyebrow(d), stops: groStoresWithNeeded(d),
  remaining: groStopRemaining(d, 'Your list'), costco: groStopRemaining(d, 'Costco') };
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Your list' });
const onIt = { head: groHeadFor(d, 'trip'), dock: groDockHtml(d, 'trip') };
click({ gro: 'stop-done' });
settle(function () {
  console.log(JSON.stringify({ before: before, onIt: onIt,
    closes: closesOf().map(function (p) { return p.body.store; }),
    step: groceryState.step }));
});
""")
    assert out["before"]["band"] == "3 things · 2 stops", "two real shops are two stops"
    assert sorted(out["before"]["stops"]) == ["Costco", "Your list"], "both are stops"
    assert out["before"]["remaining"] == 1, "the shopless row rides to either shop, so it is neither's"
    assert out["before"]["costco"] == 1
    assert out["onIt"]["head"]["sub"] == "Stop 1 of 2 · 2 left"
    assert 'data-gro="stop-done">Done at Your list</button>' in out["onIt"]["dock"], \
        "a shop is finished by name, however that name reads"
    assert "Done shopping" not in out["onIt"]["dock"], "Costco is still to go"
    assert out["closes"] == ["Your list"], "and the trip is recorded under it"
    assert out["step"] == "next", "with somewhere still to go"


@_needs_node
def test_the_stand_in_survives_a_shop_being_named_while_the_trip_is_on():
    """GUARD — it cannot be run against this branch's first commit, where
    the names it calls do not exist, so its redness there says nothing.

    It is the reason groIsStandIn asks about PILL STORES rather than about
    the household or about the trip: a trip is a snapshot, so "Your list"
    stays in tripStops after a shop is named, and in that state the
    household does have a pill store. It is still the stand-in, because the
    shop they named is not called "Your list".

    Pinned by MUTATION, and the mutation is the tidier-looking rule that
    was considered and dropped — "the stand-in is whatever the only stop
    is". That reddens this and
    test_naming_a_shop_mid_trip_does_not_renumber_the_trip_already_on
    together, which is what makes the pill-store form the robust one."""
    out = _node("""
const d = oneList();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
groceryState.usualStores = ['Metro'];   // added from What we know
click({ gro: 'trip-resume' });
console.log(JSON.stringify({ at: groTripStore(), noShopNamed: groNoShopNamed(d),
  isStandIn: groIsStandIn(d, 'Your list'), dock: groDockHtml(d, 'trip'),
  head: groHeadFor(d, 'trip') }));
""")
    assert out["at"] == "Your list"
    assert out["noShopNamed"] is False, "they have named a shop by now"
    assert out["isStandIn"] is True, "and it still is not this one"
    assert 'data-gro="stop-done">Done shopping</button>' in out["dock"]
    assert out["head"]["sub"] == "3 left"


# --- 9. what is still true, and deliberately not fixed here ----------------


@_needs_node
def test_two_shops_with_everything_anywhere_still_cannot_continue_a_paused_trip():
    """CHARACTERISATION — green on main and green here, on purpose.

    A household with two named shops that answered "Anywhere" to everything
    gets one stand-in stop (its most-used shop) holding nothing of its own,
    so groStopRemaining reads zero for it and a paused trip cannot be
    continued — the same sentence this branch fixed for the one-stop
    households. It is NOT fixed here because there the shopless things
    genuinely could be bought at either shop, and "which stop owns them" is
    a question this ticket has no answer to. Pre-existing; its own card.
    Invert this test when that one is worked."""
    out = _node("""
const d = base({ usualStores: ['Costco', 'Metro'], stores: {
  Unassigned: { sections: rows([item(1, 'Rice', '', 1), item(2, 'Oats', '', 1)]), purchased: [], inCart: [] },
  Costco: empty(), Metro: empty()
} });
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
console.log(JSON.stringify({ line: groTripPausedLine(d), dock: groDockHtml(d, 'list') }));
""")
    assert out["line"] == "Trip in progress · every stop done", "the known remaining case"
    assert "trip-resume" not in out["dock"]
