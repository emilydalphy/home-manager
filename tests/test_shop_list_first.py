"""
Shop: the list is the first screen; nothing stands in front of it.

Loop Board, 2026-09-15 (MEDIUM · Improvement): "as the person in the car
park with one hand free, I want to open Shop and see the list — with
anything unsorted simply listed under 'Not sorted yet' — so that a store
question never stands between me and what to buy."

What stood in front of it, read off the code:

  * the first-run "Where do you usually shop?" card rendered INSTEAD of the
    list until answered (groListHtml returned early with it);
  * the tab opened on SORT ("Before the list · Where does this go?")
    whenever anything had no store — on tab open, on an approval's "Open
    the list", and after every Add from the list's own foot, because groDo
    re-reads the list through loadGrocery and loadGrocery called
    groMaybeSortFirst (decision J, 2026-09-11);
  * the unsorted rows themselves were nowhere on LIST — only counted in
    the "N things to sort" row — and left off every stop of the trip.

Now: the card sits at the top of the list; groMaybeSortFirst is
groMaybeCarryFirst and asks about last week's leftovers only; SORT is
reached from the "N things to sort" row and nowhere else; the unsorted
things are a "Not sorted yet · N" card under the store cards and ride
along on the trip like the "Any" ones; an Add lands on the list with the
add box focused and an aisle guessed locally (groGuessCategory) rather
than a flat 'other'.

Behaviour runs under node against shell.js's own functions (the
tests/test_shop_trip_exit.py harness). The tab-open and add tests drive
loadGrocery / groAddItem end to end with canned fetch answers, so they
are the real paths, not a re-statement of them.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest
from test_shop_trip_exit import _STUB, _grocery_block

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)

# A panel the harness does not otherwise have, so loadGrocery runs (it
# returns early without one) and groAddItem finds its add row. renderGrocery
# is replaced by a counter: the screens are read through the builders
# (groListHtml, groHeadFor) directly. fetch answers by URL so the list that
# loads is the one each test describes — two shops, one thing already at
# Costco, two things with no store yet.
_PATCH = """
var RENDERS = 0;
var FOCUSED = 0;
renderGrocery = function () { RENDERS += 1; };
var addInput = { value: '', focus: function () { FOCUSED += 1; }, setSelectionRange: function () {} };
var addBtn = { disabled: false };
panels['grocery'] = { dataset: { built: '1' }, querySelector: function (sel) {
  if (sel === '#gro-add-item') return addInput;
  if (sel === '#gro-add-btn') return addBtn;
  return null;
} };
var BY_STORE = { stores: [
  { store: 'Costco', sections: [{ section: 'pantry', items: [
    { id: 1, item: 'Rice', quantity: '1', store: 'Costco', store_decided: 1, category: 'pantry' }] }] },
  { store: 'Unassigned', sections: [
    { section: 'dairy', items: [{ id: 2, item: 'Milk', quantity: '2 L', store: '', store_decided: 0, category: 'dairy' }] },
    { section: 'other', items: [{ id: 3, item: 'Foil', quantity: '', store: '', store_decided: 0, category: 'other' }] }] }
] };
var CARRIED = [];
fetch = function (url, opts) {
  var body = {};
  if (url.indexOf('/api/grocery-list/by-store') === 0) body = BY_STORE;
  else if (url.indexOf('/api/grocery-list/carried-over') === 0) body = { items: CARRIED };
  else if (url.indexOf('/api/grocery-list?status=') === 0) body = { sections: [] };
  else if (url === '/api/grocery-list/spices') body = { items: [], recently_bought: [] };
  else if (url === '/api/grocery-list/already-have-summary') body = { already_have: [], elsewhere: [] };
  if (opts && opts.method === 'POST') POSTS.push({ url: url, body: JSON.parse(opts.body || '{}') });
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
groceryState.usualStores = ['Costco', 'Metro'];
groceryState.storesPromptDismissed = true;
groceryState.tripRestored = true;
function posts(url) { return POSTS.filter(function (p) { return p.url.indexOf(url) !== -1; }); }
function cards(html) {
  return (html.match(/gro-store-name">([^<]*)</g) || []).map(function (m) { return /gro-store-name">([^<]*)</.exec(m)[1]; });
}
"""


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + _PATCH + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. opening the tab -----------------------------------------------------


@_needs_node
def test_opening_shop_with_unsorted_things_lands_on_the_list():
    """The real load path (loadGrocery, as buildGroceryPanel calls it) with
    two things that have no store. On the parent branch this lands on
    'sort' — groMaybeSortFirst ran at the end of every load."""
    out = _node("""
loadGrocery().then(function () {
  console.log(JSON.stringify({
    step: groceryState.step,
    toSort: groUnsorted(groceryState.data).length,
    row: groListHtml(groceryState.data).indexOf('data-gro="goto-sort"') !== -1
  }));
});
""")
    assert out["step"] == "list"
    assert out["toSort"] == 2, "still to sort — the list just doesn't make you"
    assert out["row"] is True, "and the way into SORT is on the list"


@_needs_node
def test_the_usual_stores_landing_second_does_not_open_sort_either():
    """The shops and the list load side by side; whichever lands second
    used to decide whether sorting came first (groLoadUsualStores called
    groMaybeSortFirst too)."""
    out = _node("""
fetchWas = fetch;
fetch = function (url, opts) {
  if (url === '/api/memory') {
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ usual_stores: ['Costco', 'Metro'], stores_prompt_dismissed: true }); } });
  }
  return fetchWas(url, opts);
};
loadGrocery().then(function () { return groLoadUsualStores(); }).then(function () {
  console.log(JSON.stringify({ step: groceryState.step }));
});
""")
    assert out["step"] == "list"


@_needs_node
def test_open_the_list_after_an_approval_is_the_list():
    """groSetScreen — the approved-week receipt's "Open the list" and its
    twins — used to go on to groMaybeSortFirst."""
    out = _node("""
loadGrocery().then(function () {
  groceryState.step = 'trip';
  groSetScreen('plan');
  console.log(JSON.stringify({ step: groceryState.step }));
});
""")
    assert out["step"] == "list"


@_needs_node
def test_last_weeks_leftovers_still_come_first_and_hand_over_to_the_list():
    """The one automatic screen that stays (Emily, 2026-09-13: the
    leftovers are a question about amounts, asked before the list). When
    the last one is answered the list follows — not SORT, which is where
    groAdvanceCarry used to go when anything had no store."""
    out = _node("""
CARRIED = [{ item_id: 41, item: 'Onion', quantity: '2', category: 'produce', store: '', this_week_quantity: null }];
loadGrocery().then(function () {
  var first = groceryState.step;
  groceryState.carried = [];
  groAdvanceCarry();
  console.log(JSON.stringify({ first: first, after: groceryState.step }));
});
""")
    assert out["first"] == "carry"
    assert out["after"] == "list"


# --- 2. adding a thing ------------------------------------------------------


@_needs_node
def test_an_add_stays_on_the_list_and_focuses_the_add_box_again():
    """groAddItem end to end: the POST, then groDo's re-read of the list
    (loadGrocery), then focus. On the parent branch the re-read opened SORT
    because the new thing had no store."""
    out = _node("""
loadGrocery().then(function () {
  addInput.value = 'oat milk';
  return groAddItem();
}).then(function () {
  console.log(JSON.stringify({
    step: groceryState.step, focused: FOCUSED, cleared: addInput.value,
    post: posts('/api/grocery-list/add')[0].body, renders: RENDERS
  }));
});
""")
    assert out["step"] == "list", "an add never switches step"
    assert out["focused"] >= 1, "the add box is ready for the next thing"
    assert out["cleared"] == ""
    assert out["post"] == {"item": "oat milk", "quantity": "", "category": "dairy"}
    assert out["renders"] >= 2, "the list re-drew with the new thing on it"


@_needs_node
def test_an_add_from_the_list_carries_a_guessed_aisle_not_other():
    out = _node("""
var bodies = [];
['2 lb carrots', 'chicken thighs', 'frozen peas', 'paper towels', 'bread'].forEach(function (typed) {
  addInput.value = typed;
  groAddItem();
});
settle(function () {
  console.log(JSON.stringify(posts('/api/grocery-list/add').map(function (p) { return [p.body.item, p.body.category]; })));
});
""")
    assert out == [
        ["carrots", "produce"],
        ["chicken thighs", "meat/seafood"],
        ["frozen peas", "frozen"],
        ["paper towels", "other"],
        ["bread", "other"],
    ]


# --- 3. the local categoriser ----------------------------------------------


@_needs_node
def test_the_categoriser_maps_a_few_words_and_leaves_the_rest_as_other():
    out = _node("""
console.log(JSON.stringify(['milk', 'Cheddar cheese', 'eggs', 'butter', 'bananas', 'apples', 'lettuce', 'red onions',
  'chicken breast', 'ground beef', 'salmon', 'basmati rice', 'pasta', 'flour', 'black beans', 'frozen peas',
  'ice cream', 'bell peppers', 'paper towels', 'dish soap', 'bread', 'bagels', ''].map(groGuessCategory)));
""")
    assert out == [
        "dairy", "dairy", "dairy", "dairy",
        "produce", "produce", "produce", "produce",
        "meat/seafood", "meat/seafood", "meat/seafood",
        "pantry", "pantry", "pantry", "pantry",
        "frozen", "frozen",
        "produce",
        "other", "other", "other", "other", "other",
    ]


def test_the_categoriser_only_knows_aisles_the_server_groups_by():
    """The by-store grouping folds anything else to Other, so a made-up
    aisle here would be a silent no-op. quantities._GROCERY_SECTION_ORDER
    is the list; 'bakery' is deliberately not on it (see the comment on
    GRO_AISLE_WORDS)."""
    from app.tools import quantities

    block = SHELL_JS.split("var GRO_AISLE_WORDS = [", 1)[1].split("];", 1)[0]
    aisles = [line.strip()[2:].split("'", 1)[0] for line in block.splitlines() if line.strip().startswith("['")]
    assert aisles, "the word list is a list of [aisle, words] pairs"
    for aisle in aisles:
        assert aisle in quantities._GROCERY_SECTION_ORDER, aisle
    assert "bakery" not in block
    # One function, one call site, and only where the caller used to say 'other'.
    assert SHELL_JS.count("function groGuessCategory(") == 1
    assert "category: groGuessCategory(name)" in SHELL_JS
    assert "category: 'other' }" not in SHELL_JS.split("async function groAddItem(", 1)[1].split("\n  }\n", 1)[0]


# --- 4. the list shows the unsorted things ----------------------------------


@_needs_node
def test_the_not_sorted_yet_card_lists_the_unsorted_rows_after_the_stores():
    out = _node("""
loadGrocery().then(function () {
  var html = groListHtml(groceryState.data);
  var card = html.slice(html.indexOf('gro-unsorted'));
  console.log(JSON.stringify({
    cards: cards(html), card: card,
    order: [html.indexOf('data-gro="goto-sort"'), html.indexOf('Costco'), html.indexOf('gro-unsorted')]
  }));
});
""")
    assert out["cards"] == ["Costco · 1", "Not sorted yet &middot; 2"]
    card = out["card"]
    assert "Milk" in card and "Foil" in card
    assert '<span class="gro-qty">2 L</span>' in card
    assert card.count('data-gro="row-menu"') == 2, "each row keeps its ⋯, which is how one gets a store from here"
    assert ">Dairy<" in card and ">Other<" in card, "aisle eyebrows, like every other card"
    assert 'class="gro-store-avatar gro-anywhere-avatar">?</span>' in card
    a, b, c = out["order"]
    assert 0 <= a < b < c, "the sort row at the top, the stores, then the unsorted things"


@_needs_node
def test_any_and_not_sorted_are_two_cards_and_a_row_is_never_on_both():
    out = _node("""
BY_STORE.stores[1].sections[0].items.push({ id: 4, item: 'Eggs', quantity: '12', store: '', store_decided: 1, category: 'dairy' });
loadGrocery().then(function () {
  var html = groListHtml(groceryState.data);
  var anywhere = html.slice(html.indexOf('Anywhere &middot;'), html.indexOf('gro-unsorted'));
  var unsorted = html.slice(html.indexOf('gro-unsorted'));
  console.log(JSON.stringify({ cards: cards(html),
    anywhereHasEggs: anywhere.indexOf('Eggs') !== -1, anywhereHasMilk: anywhere.indexOf('Milk') !== -1,
    unsortedHasEggs: unsorted.indexOf('Eggs') !== -1, unsortedHasMilk: unsorted.indexOf('Milk') !== -1,
    eggsRows: (html.match(/Eggs</g) || []).length }));
});
""")
    assert out["cards"] == ["Costco · 1", "Anywhere &middot; 1", "Not sorted yet &middot; 2"]
    assert out["anywhereHasEggs"] and not out["anywhereHasMilk"]
    assert out["unsortedHasMilk"] and not out["unsortedHasEggs"]
    assert out["eggsRows"] == 1


@_needs_node
def test_a_list_that_is_nothing_but_unsorted_things_still_reads_and_still_shops():
    """No shop has anything tagged yet (a fresh approval, no remembered
    stores): the rows are on the list under their heading, and the trip
    can start — the most-used shop stands in, exactly as it does for a
    list answered 'Anywhere' throughout. Until 2026-09-15 this household
    saw the sort queue and, from the list, no 'Start the trip'."""
    out = _node("""
BY_STORE.stores = [BY_STORE.stores[1]];
loadGrocery().then(function () {
  console.log(JSON.stringify({ step: groceryState.step, cards: cards(groListHtml(groceryState.data)),
    stops: groStoresWithNeeded(groceryState.data), dock: groDockHtml(groceryState.data, 'list') }));
});
""")
    assert out["step"] == "list"
    assert out["cards"] == ["Not sorted yet &middot; 2"]
    assert out["stops"] == ["Costco"]
    assert 'data-gro="start-trip"' in out["dock"]


@_needs_node
def test_a_household_with_one_shop_or_none_keeps_its_plain_list():
    """groUnsorted is empty when sorting isn't a question, so the heading
    never appears for them — their loose pile is the whole list, headingless
    (Emily, 2026-09-09: 'One list is fine' means what it says)."""
    out = _node("""
BY_STORE.stores = [BY_STORE.stores[1]];
groceryState.usualStores = [];
loadGrocery().then(function () {
  var none = groListHtml(groceryState.data);
  groceryState.usualStores = ['Costco'];
  var one = groListHtml(groceryState.data);
  console.log(JSON.stringify({ none: cards(none), noneHasMilk: none.indexOf('Milk') !== -1, one: cards(one) }));
});
""")
    assert out["none"] == [] and out["noneHasMilk"] is True
    assert out["one"] == ["Costco · 2"]


# --- 5. the store question is a card on the list, not the screen ------------


@_needs_node
def test_the_store_question_sits_above_the_list_and_its_answers_still_work():
    out = _node("""
groceryState.usualStores = [];
groceryState.storesPromptDismissed = false;
loadGrocery().then(function () {
  var html = groListHtml(groceryState.data);
  var asking = { step: groceryState.step, card: html.indexOf('gro-stores-prompt') !== -1,
    listUnder: html.indexOf('Milk') > html.indexOf('gro-stores-prompt') && html.indexOf('Rice') !== -1,
    dock: groDockHtml(groceryState.data, 'list') };
  click({ gro: 'stores-prompt-done' });
  settle(function () {
    var after = groListHtml(groceryState.data);
    console.log(JSON.stringify({ asking: asking, dismissed: groceryState.storesPromptDismissed,
      posted: posts('/api/memory/stores-prompt-dismiss').length, cardAfter: after.indexOf('gro-stores-prompt') !== -1,
      listAfter: after.indexOf('Milk') !== -1 }));
  });
});
""")
    assert out["asking"]["step"] == "list"
    assert out["asking"]["card"] is True
    assert out["asking"]["listUnder"] is True, "the list is under the card, not behind it"
    assert "start-trip" not in out["asking"]["dock"], "the card's button is the screen's one apricot while it is up (Rule 5)"
    assert out["dismissed"] is True and out["posted"] == 1
    assert out["cardAfter"] is False and out["listAfter"] is True


# --- 6. the trip -----------------------------------------------------------


@_needs_node
def test_unsorted_things_ride_along_on_the_trip_like_any_ones():
    out = _node("""
loadGrocery().then(function () {
  groceryState.tripStops = ['Costco'];
  groceryState.tripIndex = 0;
  console.log(JSON.stringify({
    items: groTripItems(groceryState.data).map(function (i) { return i.item; }),
    sections: groTripSections(groceryState.data).map(function (s) { return [s.section, s.items.map(function (i) { return i.item; })]; })
  }));
});
""")
    assert out["items"] == ["Rice", "Milk", "Foil"]
    assert out["sections"] == [["pantry", ["Rice"]], ["dairy", ["Milk"]], ["other", ["Foil"]]], "folded into the aisles, tickable at this stop"


def test_the_heading_is_one_line_to_change():
    assert "var GRO_UNSORTED_SECTION = 'Not sorted yet';" in SHELL_JS
    assert SHELL_JS.count("escapeHtml(GRO_UNSORTED_SECTION)") == 1
    assert SHELL_JS.count(">Not sorted yet<") == 0
