"""
Two shops, everything answered "Anywhere": the paused trip knows it isn't
finished.

Loop Board, Phase 1 (Beta, Bug). The residue
`overnight/one-list-can-start-a-trip` measured, characterised and
deliberately left on 2026-09-16 — see section 9 of
tests/test_one_list_can_start_a_trip.py, which is the test this branch was
asked to invert, and that branch's decision-log entry for why it stopped
where it did.

REPRODUCED before anything was touched, by running the Grocery region's own
functions against the payload the server hands such a household (two usual
stores, one Unassigned bucket, every row `store: ''` with
`store_decided: 1`):

    stores             {Unassigned}  <- and no bucket for either shop
    stops              ['Costco']   <- groStoresWithNeeded's fallback stop
    isStandIn          False        <- a real shop, so the sibling's rule misses
    soleStore          None         <- two pill stores, so that one misses too
    on the trip        "Stop 1 of 1 - 2 left", Rice and Oats both tickable
    groStopRemaining   0            <- and here the two screens part company
    paused line        "Trip in progress - every stop done"
    band, same screen  "2 things - 1 stop"
    dock               "Finish the trip", and nothing else
    continuing anyway  'next', which renderGrocery folds into the wrap-up

So the trip screen and the root, over one snapshot, disagreed about the
same stop. `groStopRemaining` was the one place that did not follow
`groTripItems`, and a stop holding nothing of its own is where that shows.

THE RULE, which is the question the sibling branch had no answer to: a
thing that could be bought at either shop is owed at THE STOP YOU ARE
STANDING IN. Not a new rule — groTripItems has said it since 2026-09-13,
when the shopless pile stopped being stranded at the first stop and started
following the shopper. It cannot double-count, because only one stop is
open at a time.

Behaviour is run under node against shell.js's own functions, on the same
stub the sibling file builds (`twoShops`, `oneShop`, `oneList`), because
this card is that card's residue and the two must be describing one
household shape. "The button isn't there" is exactly what a source-marker
test cannot see.

The payload above was read off a real uvicorn on a throwaway database, not
assumed: /api/grocery-list/by-store hands such a household ONE Unassigned
bucket and no bucket for either shop, so the fallback stop is a name that
only groceryState.usualStores knows and groStopRemaining starts from an
undefined store. There is a test for that shape as well as for the
fixtures'.

Each test says in its own docstring whether it is a CATCH (red against the
unmodified shell.js) or a no-regression GUARD, and one says it is half of
each. Eleven of the nineteen are red; every guard names the mutation that
pins it, and five were run — including the sibling branch's own
no-double-counting mutation, which still bites.
"""
from __future__ import annotations

import json

import pytest
from test_one_list_can_start_a_trip import _STUB, _grocery_block, _needs_node

import nodeharness

# Helpers this card needs on top of the sibling file's stub. `own` is the
# number groStopRemaining starts from — what a stop holds of its own —
# so a test can ask who is carrying the pile without restating the sum.
_PRELUDE = """
// Two named shops, every row answered "Anywhere". groStoresWithNeeded
// finds no shop with rows of its own, so it falls back to one stop: the
// shop this household buys from most (groMostUsedStore). That stop is a
// real shop, so neither groIsStandIn nor groSoleStore answers for it, and
// before 2026-09-17 nothing else did either.
function anywhere() {
  return base({ usualStores: ['Costco', 'Metro'], stores: {
    Unassigned: { sections: rows([item(1, 'Rice', '', 1), item(2, 'Oats', '', 1)]),
      purchased: [], inCart: [] },
    Costco: empty(), Metro: empty()
  } });
}
function own(d, name) {
  var s = d.stores[name];
  return s ? groNeededCount(s) + s.inCart.length : 0;
}
// Who is claiming more than their own rows, i.e. who is carrying the
// shopless pile. The property this card turns on is that this list never
// holds two names.
function carriers(d, stops) {
  return stops.filter(function (n) { return groStopRemaining(d, n) > own(d, n); });
}
function remainingOf(d, stops) {
  var out = {};
  stops.forEach(function (n) { out[n] = groStopRemaining(d, n); });
  return out;
}
"""


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + _PRELUDE + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the reported symptom ----------------------------------------------


@_needs_node
def test_the_paused_trip_offers_continue_and_does_not_claim_every_stop_is_done():
    """CATCH — the ticket. Before this the line read "every stop done" and
    the dock held one button, "Finish the trip", over two unbought things."""
    out = _node("""
const d = anywhere();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
console.log(JSON.stringify({
  step: groceryState.step,
  line: groTripPausedLine(d),
  dock: groDockHtml(d, 'list'),
  band: groBandEyebrow(d)
}));
""")
    assert out["step"] == "list", "Finish later keeps the trip and goes to the list"
    assert out["line"] == "Trip in progress · 1 stop left"
    assert 'data-gro="trip-resume">Continue the trip</button>' in out["dock"]
    assert 'data-gro="trip-finish-now">Finish the trip</button>' in out["dock"], \
        "and finishing early is still offered, quietly"
    assert out["band"] == "2 things · 1 stop", \
        "the eyebrow never stopped counting them, which is half of why this read wrong"


@_needs_node
def test_continuing_lands_on_the_stop_with_the_things_on_it():
    """CATCH — acceptance criterion 3. Before this, continuing went to
    WHERE NEXT with no cards on it, which renderGrocery folds straight into
    the wrap-up."""
    out = _node("""
const d = anywhere();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
click({ gro: 'trip-resume' });
console.log(JSON.stringify({
  step: groceryState.step,
  at: groTripStore(),
  items: groTripItems(d).map(function (i) { return i.item; }),
  next: groNextHtml(d).indexOf('wrap it up') === -1
}));
""")
    assert out["step"] == "trip", "not the wrap-up"
    assert out["at"] == "Costco"
    assert out["items"] == ["Rice", "Oats"]
    assert out["next"] is True, "and WHERE NEXT would not have sent them there either"


@_needs_node
def test_the_root_and_the_stop_screen_no_longer_disagree_about_one_stop():
    """CATCH, and the sharpest statement of the bug: what the stop's own
    screen shows and what the root counts for it are the same number now.
    Before, the trip read "Stop 1 of 1 - 2 left" over two tickable rows
    while groStopRemaining answered 0 for that very stop."""
    out = _node("""
const d = anywhere();
click({ gro: 'start-trip' });
console.log(JSON.stringify({
  head: groHeadFor(d, 'trip').sub,
  onScreen: groTripItems(d).length + groTripInCart(d).length,
  counted: groStopRemaining(d, groTripStore())
}));
""")
    assert out["onScreen"] == 2
    assert out["counted"] == out["onScreen"], \
        "groStopRemaining follows groTripItems for the stop you are standing in"
    assert out["head"] == "Stop 1 of 1 · 2 left"


@_needs_node
def test_the_stop_can_be_a_shop_the_payload_never_mentions():
    """CATCH — and the reason it is here rather than folded into the three
    above: the fixtures carry an empty bucket per named shop, and the
    server does not. Checked over HTTP on a throwaway database
    (/api/grocery-list/by-store for this household): the response holds one
    Unassigned bucket and nothing else, so groLoadAllData builds
    data.stores with no Costco key at all and groStopRemaining starts from
    an undefined store. The fallback stop is then a name only
    groceryState.usualStores knows."""
    out = _node("""
// exactly what groLoadAllData builds from the live response
const d = base({ usualStores: ['Costco', 'Metro'], stores: {
  Unassigned: { sections: rows([item(1, 'Rice', '', 1), item(2, 'Oats', '', 1)]),
    purchased: [], inCart: [] }
} });
click({ gro: 'start-trip' });
const onTrip = { step: groceryState.step, at: groTripStore(),
  items: groTripItems(d).map(function (i) { return i.item; }) };
click({ gro: 'trip-pause' });
console.log(JSON.stringify({
  onTrip: onTrip,
  hasBucket: Object.prototype.hasOwnProperty.call(d.stores, 'Costco'),
  remaining: groStopRemaining(d, 'Costco'),
  line: groTripPausedLine(d),
  dock: groDockHtml(d, 'list')
}));
""")
    assert out["hasBucket"] is False, "the shop has no bucket of its own in the payload"
    assert out["onTrip"] == {"step": "trip", "at": "Costco", "items": ["Rice", "Oats"]}
    assert out["remaining"] == 2
    assert out["line"] == "Trip in progress \u00b7 1 stop left"
    assert 'data-gro="trip-resume">Continue the trip</button>' in out["dock"]


# --- 2. which stop owns a thing that could be bought at either ------------


@_needs_node
def test_the_open_stop_owns_the_pile_and_the_others_do_not():
    """CATCH — the rule, on the ordinary two-shop week. Costco is open, so
    the foil is owed there; Metro keeps its own egg and nothing else.
    Before this neither stop owned it and the foil was owed nowhere."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });          // two stops is a question
click({ gro: 'head-for', store: 'Costco' });
console.log(JSON.stringify({
  at: groTripStore(),
  remaining: remainingOf(d, groceryState.tripStops),
  carriers: carriers(d, groceryState.tripStops)
}));
""")
    assert out["at"] == "Costco"
    assert out["remaining"] == {"Costco": 2, "Metro": 1}, "Costco's rice, and the foil"
    assert out["carriers"] == ["Costco"]


@_needs_node
def test_the_pile_moves_to_the_next_stop_when_the_shopper_does():
    """CATCH — ownership is not a property of the stop, it is a property of
    where the shopper is. Walk to Metro and the foil walks with them, which
    is what groTripItems has always done on the screen itself."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
const atCostco = remainingOf(d, groceryState.tripStops);
click({ gro: 'next-stop', store: 'Metro' });
const atMetro = remainingOf(d, groceryState.tripStops);
console.log(JSON.stringify({ atCostco: atCostco, atMetro: atMetro,
  at: groTripStore(), items: groTripItems(d).map(function (i) { return i.item; }) }));
""")
    assert out["atCostco"] == {"Costco": 2, "Metro": 1}
    assert out["atMetro"] == {"Costco": 1, "Metro": 2}, "the foil went with them"
    assert out["at"] == "Metro"
    assert out["items"] == ["Eggs", "Foil"]


@_needs_node
def test_before_anybody_sets_off_the_pile_is_owed_at_no_stop():
    """GUARD — green before and after. "Where are we headed?" is asked
    before there is a stop to stand in, so each card counts its own rows
    and the celadon note under them says the rest comes with you. Pinned by
    MUTATION: make groStopRemaining add the pile unconditionally and both
    cards read 2 things for a list of three."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
console.log(JSON.stringify({
  step: groceryState.step,
  remaining: remainingOf(d, groStoresWithNeeded(d)),
  carriers: carriers(d, groStoresWithNeeded(d)),
  headed: groHeadedHtml(d)
}));
""")
    assert out["step"] == "headed"
    assert out["remaining"] == {"Costco": 1, "Metro": 1}
    assert out["carriers"] == [], "nobody is standing anywhere yet"
    assert "1 thing with no shop will come with you." in out["headed"]


# --- 3. and never owed at two stops at once -------------------------------


@_needs_node
def test_the_pile_is_never_owed_at_two_stops_at_once():
    """CATCH for the positive half (before this nobody carried the pile at
    all, so the count was 0 where it should be 1) and the no-double-count
    property both ways. Walked across the whole trip: before it starts, at
    each stop, and once a stop is behind us. Pinned by MUTATION as well:
    add the pile unconditionally and two stops carry it at once."""
    out = _node("""
const d = twoShops();
const seen = [];
function note(where) {
  const stops = groceryState.tripStops || groStoresWithNeeded(d);
  seen.push({ where: where, carriers: carriers(d, stops) });
}
note('before');
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
note('at Costco');
click({ gro: 'next-stop', store: 'Metro' });
note('at Metro');
groceryState.tripDone['Costco'] = true;
note('Costco behind us');
console.log(JSON.stringify({ seen: seen }));
""")
    counts = {s["where"]: s["carriers"] for s in out["seen"]}
    assert counts["before"] == []
    assert counts["at Costco"] == ["Costco"]
    assert counts["at Metro"] == ["Metro"]
    assert counts["Costco behind us"] == ["Metro"]
    for where, who in counts.items():
        assert len(who) <= 1, f"two stops owed the same thing at {where}"


@_needs_node
def test_the_stops_add_up_to_the_list_and_not_to_more_than_it():
    """CATCH — the arithmetic both ways, which is the honest test of "not
    double-counted": what the snapshot's stops owe between them is the size
    of what is left to buy. Before this it was 2 for a list of 3 (the foil
    was owed nowhere); an unconditional pile makes it 4."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
function sum() {
  return groceryState.tripStops.reduce(function (n, name) {
    return n + groStopRemaining(d, name);
  }, 0);
}
const fresh = { sum: sum(), left: groTotals(d).needed + groInCartCount(d) };
intoCart(d, 9);                       // the foil goes in the trolley at Costco
const mid = { sum: sum(), left: groTotals(d).needed + groInCartCount(d) };
console.log(JSON.stringify({ fresh: fresh, mid: mid }));
""")
    assert out["fresh"] == {"sum": 3, "left": 3}
    assert out["mid"] == {"sum": 3, "left": 3}, \
        "a thing in the trolley is still owed at the stop it is being carried round"


# --- 4. the single-stop rule this is stacked on still works ---------------


@_needs_node
@pytest.mark.parametrize("shape,stop", [("oneList", "Your list"), ("oneShop", "Loblaws")])
def test_a_single_stop_household_still_counts_its_pile_before_it_sets_off(shape, stop):
    """GUARD — green before and after, and the thing this card was told
    explicitly not to cost. Both single-stop households have a list that is
    entirely shopless, and both must read three things before anybody is
    standing anywhere. Pinned by MUTATION: delete
    `groIsStandIn(data, name) || groSoleStore(data) === name` and both read
    0, because the third clause only answers once a trip is on."""
    out = _node("""
const d = %s();
console.log(JSON.stringify({
  stops: groStoresWithNeeded(d),
  remaining: groStopRemaining(d, '%s'),
  trip: groceryState.tripStops
}));
""" % (shape, stop))
    assert out["trip"] is None, "no trip, so only the single-stop clauses can answer"
    assert out["stops"] == [stop]
    assert out["remaining"] == 3


@_needs_node
@pytest.mark.parametrize("shape,stop", [("oneList", "Your list"), ("oneShop", "Loblaws")])
def test_a_paused_single_stop_trip_still_continues(shape, stop):
    """GUARD — green before and after; the sibling branch's own acceptance
    criterion, re-run here because this card added a clause to the same
    condition and a shadowed clause fails silently."""
    out = _node("""
const d = %s();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
const paused = { line: groTripPausedLine(d), dock: groDockHtml(d, 'list') };
click({ gro: 'trip-resume' });
console.log(JSON.stringify({ paused: paused, step: groceryState.step, at: groTripStore() }));
""" % shape)
    assert out["paused"]["line"] == "Trip in progress · 1 stop left"
    assert 'data-gro="trip-resume">Continue the trip</button>' in out["paused"]["dock"]
    assert out["step"] == "trip"
    assert out["at"] == stop


# --- 4b. the same sentence, for an ORDINARY multi-shop household ---------
# Found by review, and the reason it has a section of its own: this is a
# strictly BIGGER population than the household the card was written about,
# and nothing pinned it.


@_needs_node
def test_an_ordinary_multi_shop_trip_is_not_finished_while_the_pile_is_left():
    """CATCH, and the win this card did not claim until review found it.
    Nothing here is answered "Anywhere" and nothing falls back to a
    stand-in stop: two real shops, each with rows of its own, and one thing
    with no shop.

    A stop’s own rows can leave the list without that stop being finished
    — the other adult ticks them off on their phone, chat is told "we got
    the rice", a row is removed or dropped from LIST’s row menu while the
    trip is paused. Once both shops’ own rows have gone that way and only
    the shopless foil is left, the open stop holds nothing of its own, so
    on the unmodified file LIST read "every stop done" over an unbought
    thing, offered "Finish the trip" and nothing else, and continuing
    walked into the wrap-up — the card’s sentence exactly, reached without
    a stand-in stop anywhere near it."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
click({ gro: 'trip-pause' });
// Both shops' own rows leave the list some other way; the foil does not.
['Costco', 'Metro'].forEach(function (name) {
  d.stores[name].purchased = d.stores[name].sections[0].items;
  d.stores[name].sections[0].items = [];
});
const paused = { line: groTripPausedLine(d), dock: groDockHtml(d, 'list'),
  remaining: groRemainingStops(d), costco: groStopRemaining(d, 'Costco'),
  metro: groStopRemaining(d, 'Metro') };
click({ gro: 'trip-resume' });
console.log(JSON.stringify({ paused: paused, step: groceryState.step,
  at: groTripStore(), items: groTripItems(d).map(function (i) { return i.item; }) }));
""")
    assert out["paused"]["costco"] == 1, "nothing of its own, and the foil"
    assert out["paused"]["metro"] == 0, "and Metro is not owed it as well"
    assert out["paused"]["remaining"] == ["Costco"]
    assert out["paused"]["line"] == "Trip in progress · 1 stop left"
    assert 'data-gro="trip-resume">Continue the trip</button>' in out["paused"]["dock"]
    assert out["step"] == "trip", "not the wrap-up"
    assert out["at"] == "Costco"
    assert out["items"] == ["Foil"]


@_needs_node
def test_but_a_pile_left_once_every_stop_is_BEHIND_you_is_still_not_offered():
    """CHARACTERISATION — byte-identical on the unmodified file, so this is
    residue rather than regression, and it is the near neighbour of the
    test above. The difference is one word: there the open stop was still
    OPEN, here it has been finished. groRemainingStops filters on
    `!tripDone[name]` before it asks anything about the pile, so a pile
    owed at the stop you are standing in is owed at no REMAINING stop once
    you have finished that stop and the only other one has nothing of its
    own left.

    Reachable the same way as the test above (rows leaving by another
    route), and the wrap-up is arguably where such a pile belongs — that is
    what "Couldn’t find it / Will grab elsewhere / Don’t need anymore" is
    for. Named so nobody reports it as new. Its own card if it bites.

    It asserts the OUTCOME only, because that is the part that is
    byte-identical on the unmodified file. The open stop’s own reading does
    differ (1 here, 0 there — it is owed the foil now), and asserting that
    would make this red on main for a reason other than the one it is named
    after, which would prove nothing about the residue it is about. What
    matters is that the reading is filtered out before anything reads it."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
click({ gro: 'stop-done' });                 // Costco is behind us now
settle(function () {
  // …and both shops' own rows went elsewhere, leaving only the foil.
  ['Costco', 'Metro'].forEach(function (name) {
    d.stores[name].purchased = d.stores[name].purchased.concat(d.stores[name].sections[0].items);
    d.stores[name].sections[0].items = [];
  });
  console.log(JSON.stringify({
    done: Object.keys(groceryState.tripDone),
    remaining: groRemainingStops(d),
    stillToBuy: groTotals(d).needed,
    line: groTripPausedLine(d)
  }));
});
""")
    assert out["done"] == ["Costco"]
    assert out["remaining"] == [], "the open stop is filtered out for being finished"
    assert out["stillToBuy"] == 1, "over a thing nobody has bought"
    assert out["line"] == "Trip in progress · every stop done"


# --- 5. end to end --------------------------------------------------------


@_needs_node
def test_the_two_shop_anywhere_trip_runs_from_paused_to_finished():
    """CATCH — the whole thing, the way the household meets it: pause,
    continue, tick both, finish the stop, wrap up, finish the trip. None of
    it past "continue" was reachable before."""
    out = _node("""
const d = anywhere();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
// Read the dock before tapping it: the harness can click a button that was
// never rendered, and on the unmodified file this whole walk "passed"
// because of exactly that.
const offered = groDockHtml(d, 'list').indexOf('data-gro="trip-resume"') !== -1;
click({ gro: 'trip-resume' });
const resumedAt = { step: groceryState.step, at: groTripStore() };
intoCart(d, 1);
intoCart(d, 2);
const mid = { left: groTripItems(d).length, inCart: groTripInCart(d).length };
click({ gro: 'stop-done' });
settle(function () {
  const wrapStep = groceryState.step;
  const wrap = groWrapHtml(d);
  click({ gro: 'finish-trip' });
  settle(function () {
    console.log(JSON.stringify({
      offered: offered, resumedAt: resumedAt,
      mid: mid, wrapStep: wrapStep, wrap: wrap,
      purchased: purchases().length,
      step: groceryState.step,
      stops: groceryState.tripStops,
      toast: lastToast()
    }));
  });
});
""")
    assert out["offered"] is True, "the dock really did offer the way back in"
    assert out["resumedAt"] == {"step": "trip", "at": "Costco"}
    assert out["mid"] == {"left": 0, "inCart": 2}
    assert out["purchased"] == 2, "what was in the trolley came home"
    assert out["wrapStep"] == "wrap", "one stop finished is the whole trip finished"
    assert "Bought 2 of 2" in out["wrap"]
    assert out["step"] == "list"
    assert out["stops"] is None, "the snapshot is cleared"
    assert out["toast"] == "Trip finished — 2 things home."


# --- 6. what this did not change ------------------------------------------


@_needs_node
def test_the_paused_dock_still_has_one_apricot():
    """GUARD — green before and after. DESIGN_SYSTEM Rule 5: the dock this
    household now gets is the one a single-stop household already had
    (groTripPausedDockHtml), so nothing new is drawn and there is still one
    fill on the screen. The first draft of this also counted the dock-link
    and was therefore RED on the unmodified file — for the wrong reason
    entirely (the dock is the no-stops-left branch there, which has no
    quiet button at all), which proves nothing about Rule 5. That the
    resume button is there is test 1's claim, and it is made there."""
    out = _node("""
const d = anywhere();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
console.log(JSON.stringify({ dock: groDockHtml(d, 'list') }));
""")
    dock = out["dock"]
    assert dock.count("gro-primary") == 1, "one fill"
    assert "gro-secondary" not in dock


@_needs_node
def test_a_stop_with_nothing_left_of_its_own_is_still_not_a_stop_to_walk_into():
    """GUARD — green before and after. A shop whose things all came home
    some other way is a detour, not a choice, and the open-stop clause must
    not resurrect it. Metro is emptied while Costco is the open stop."""
    out = _node("""
const d = twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
d.stores.Metro.purchased.push(d.stores.Metro.sections[0].items.pop());
console.log(JSON.stringify({
  metro: groStopRemaining(d, 'Metro'),
  remaining: groRemainingStops(d)
}));
""")
    assert out["metro"] == 0
    assert out["remaining"] == ["Costco"], "Metro is behind us in every sense that matters"


@_needs_node
def test_nothing_on_the_list_is_still_nothing_to_continue():
    """GUARD — green before and after. Once the pile is empty and the stop
    holds nothing, the paused trip is finished and says so: the fix must
    not make "every stop done" unreachable."""
    out = _node("""
const d = anywhere();
click({ gro: 'start-trip' });
click({ gro: 'trip-pause' });
d.stores.Unassigned.purchased = d.stores.Unassigned.sections[0].items;
d.stores.Unassigned.sections[0].items = [];
console.log(JSON.stringify({
  line: groTripPausedLine(d),
  dock: groDockHtml(d, 'list')
}));
""")
    assert out["line"] == "Trip in progress · every stop done"
    assert "trip-resume" not in out["dock"]
    assert 'data-gro="trip-finish-now">Finish the trip</button>' in out["dock"]


@_needs_node
def test_a_shop_called_your_list_is_still_a_shop_and_not_the_stand_in():
    """HALF GUARD, HALF CATCH, and the halves are worth telling apart.

    The GUARD is the sibling branch's section 8 correction, re-run because
    this card touched the same condition: a real shop called "Your list"
    wins its own name back, so it is not the stand-in, it counts only its
    own rice, and it is finished by name. Those three are green on the
    unmodified file too.

    The CATCH is `costco == 2` — this card's own rule, red there. So the
    file's redness on this test is not evidence for the guard half, and the
    guard half is pinned by MUTATION instead: make groIsStandIn a bare name
    match and the rice is swallowed into the stand-in's reading."""
    out = _node("""
const d = base({ usualStores: ['Your list', 'Costco'], stores: {
  Unassigned: { sections: rows([item(9, 'Foil', '', 1)]), purchased: [], inCart: [] },
  'Your list': { sections: rows([item(1, 'Rice', 'Your list', 1)]), purchased: [], inCart: [] },
  Costco: { sections: rows([item(2, 'Eggs', 'Costco', 1)]), purchased: [], inCart: [] }
} });
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
console.log(JSON.stringify({
  isStandIn: groIsStandIn(d, 'Your list'),
  yourList: groStopRemaining(d, 'Your list'),
  costco: groStopRemaining(d, 'Costco'),
  label: groStopDoneLabel(d)
}));
""")
    assert out["isStandIn"] is False
    assert out["yourList"] == 1, "its own rice, and not the foil Costco is carrying"
    assert out["costco"] == 2
    assert out["label"] == "Done at Costco"


# --- 7. known, left, and named so nobody reports it as new -----------------


@_needs_node
def test_where_next_says_the_open_stops_pile_twice_and_that_is_left_alone():
    """CHARACTERISATION of behaviour this card INTRODUCES — red on the
    unmodified file (where that stop is filtered out of WHERE NEXT
    altogether and the screen reads "wrap it up"), and deliberately not
    fixed.

    WHERE NEXT is normally reached by finishing a stop, which puts the open
    stop behind you, so its card is filtered out before this can happen.
    The back gesture can land on it with the stop still open, and then that
    card's count includes the pile ("Costco, 2 things left") over the
    celadon note that says the same two things come with you. Redundant,
    not false — those two things really are what you would tick if you went
    there now — and suppressing the note for one stop is the kind of
    condition-piling the Grocery region already warns against. Its own card
    if it ever reads wrong to somebody.

    THIS HOUSEHOLD HAS ONE STOP, SO IT IS THE MILD HALF. The test below is
    the scope this docstring used to leave out: with several stops the same
    inflation is an arithmetic error across the screen, not just a repeated
    sentence. Read the two together."""
    out = _node("""
const d = anywhere();
click({ gro: 'start-trip' });
console.log(JSON.stringify({ next: groNextHtml(d) }));
""")
    assert "Costco, 2 things left" in out["next"]
    assert "2 things with no shop will come with you." in out["next"]


@_needs_node
def test_and_on_a_multi_shop_where_next_those_cards_add_up_to_more_than_the_list():
    """CHARACTERISATION, added on review because the test above understated
    its own scope — and this is the sharper half, because it is an ORDINARY
    multi-shop household rather than the one this card is about.

    Costco holds two of its own, Metro one, and two things have no shop: a
    list of five. Back-gesture onto WHERE NEXT with Costco still open and
    the cards advertise 4 + 1 with a note about 2 more — SEVEN things on a
    screen about five. On the unmodified file they advertise 2 + 1 and the
    same note, which is five, i.e. the list.

    So "the sum over the stops is the size of the list" — the property this
    card's own no-double-counting tests assert over the SNAPSHOT — does not
    hold over what WHERE NEXT DISPLAYS when the open stop is among the
    cards. Per card it is still redundant rather than false, and it is
    display only: tapping the card is guarded (the 'head-for' handler
    re-reads a live trip the way WHERE NEXT would), so nothing can be
    written wrongly from here. Left alone deliberately; named so that
    whoever does fix it knows which property they are restoring."""
    out = _node("""
// Costco 2 of its own, Metro 1, two with no shop — a list of five.
const d = base({ usualStores: ['Costco', 'Metro'], stores: {
  Unassigned: { sections: rows([item(8, 'Foil', '', 1), item(9, 'Cling film', '', 1)]),
    purchased: [], inCart: [] },
  Costco: { sections: rows([item(1, 'Rice', 'Costco', 1), item(2, 'Oats', 'Costco', 1)]),
    purchased: [], inCart: [] },
  Metro: { sections: rows([item(3, 'Eggs', 'Metro', 1)]), purchased: [], inCart: [] }
} });
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
goGroceryStep('next');                       // what the back gesture reaches
const next = groNextHtml(d);
console.log(JSON.stringify({
  list: groTotals(d).needed,
  costco: groStopRemaining(d, 'Costco'),
  metro: groStopRemaining(d, 'Metro'),
  ride: groRideAlongItems(d).length,
  note: next.indexOf('2 things with no shop will come with you.') !== -1,
  cards: [next.indexOf('Costco, 4 things left') !== -1,
          next.indexOf('Metro, 1 thing left') !== -1]
}));
""")
    assert out["list"] == 5
    assert out["costco"] == 4, "its own two, and both shopless things"
    assert out["metro"] == 1, "and Metro is still owed only its own — never double-counted"
    assert out["cards"] == [True, True]
    assert out["note"] is True
    assert out["costco"] + out["metro"] + out["ride"] == 7, \
        "seven advertised on a screen about five — the known overstatement"
