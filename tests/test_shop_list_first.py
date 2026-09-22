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
groMaybeCarryFirst and asks about last week's leftovers only; SORT ALL is
reached from the "N things to sort" row and nowhere else; the unsorted
things are rows on the list — on the "Anywhere" card since 2026-09-18
("the list is the checklist"), on a "Not sorted yet" card of their own
before that — tickable like everything else; an Add lands on the list
with the add box focused and an aisle guessed locally (groGuessCategory)
rather than a flat 'other'.

Behaviour runs under node against shell.js's own functions
(tests/shop_harness). The tab-open and add tests drive loadGrocery /
groAddItem end to end with canned fetch answers, so they are the real
paths, not a re-statement of them.
"""
from __future__ import annotations

import json

import nodeharness
from shop_harness import CLICK as _CLICK, SHELL_JS, STUB as _STUB, grocery_block as _grocery_block, needs_node

_needs_node = needs_node

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
panels['grocery'] = { dataset: { built: '1' }, querySelector: function () { return null; } };
// The add sheet, as groAddSheetOpen leaves it (no document here, so the
// sheet's state is set the way the open sets it and the field's value
// is the state's typed text).
function typeIntoSheet(text, store) {
  groceryState.addSheet = { typed: text, store: store === undefined ? '' : store, picked: store !== undefined };
}
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
function cards(html) {
  return (html.match(/gro-store-name">([^<]*)</g) || []).map(function (m) { return /gro-store-name">([^<]*)</.exec(m)[1]; });
}
function counts(html) {
  return (html.match(/gro-store-count">([^<]*)</g) || []).map(function (m) { return /gro-store-count">([^<]*)</.exec(m)[1]; });
}
"""


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + _CLICK + _PATCH + body, timeout=30)
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
def test_an_add_stays_on_the_list_and_closes_the_sheet():
    """groAddItem end to end: the POST (with the sheet's store), then the
    re-read of the list (loadGrocery). On the parent branch the re-read
    opened SORT because the new thing had no store."""
    out = _node("""
loadGrocery().then(function () {
  typeIntoSheet('oat milk', 'Metro');
  return groAddItem();
}).then(function () {
  console.log(JSON.stringify({
    step: groceryState.step, sheet: groceryState.addSheet,
    post: posts('/api/grocery-list/add')[0].body, renders: RENDERS
  }));
});
""")
    assert out["step"] == "list", "an add never switches step"
    assert out["sheet"] is None, "the sheet closes on Add"
    assert out["post"] == {"item": "oat milk", "quantity": "", "category": "dairy", "store": "Metro", "remember": True}
    assert out["renders"] >= 2, "the list re-drew with the new thing on it"


@_needs_node
def test_an_add_from_the_list_carries_a_guessed_aisle_not_other():
    out = _node("""
groceryState.data = { stores: {} };
['2 lb carrots', 'chicken thighs', 'frozen peas', 'paper towels', 'bread'].forEach(function (typed) {
  typeIntoSheet(typed);
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


@_needs_node
def test_the_categoriser_reads_plurals_and_puts_the_sauce_with_the_pantry():
    """Verifier, 2026-09-15: "tomatoes" and "sweet potatoes" came back as
    Other, because a bare trailing-s rule made "tomatoe" of them; and the
    single-word match put peanut butter with the dairy, chicken stock with
    the meat, and garlic powder / tomato sauce / apple juice with the
    produce. The pantry phrases are tried before those aisles now."""
    out = _node("""
console.log(JSON.stringify(['tomatoes', 'sweet potatoes', 'peaches', 'grapes', 'peanut butter', 'chicken stock',
  'garlic powder', 'tomato sauce', 'apple juice', 'orange juice', 'tomato paste', 'butter', 'chicken thighs',
  'garlic', 'oranges'].map(groGuessCategory)));
""")
    assert out == [
        "produce", "produce", "other", "produce",
        "pantry", "pantry", "pantry", "pantry", "pantry", "pantry", "pantry",
        "dairy", "meat/seafood", "produce", "produce",
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
    # One function, used by the add row and the voice add — the two places
    # that used to say 'other' — and nowhere a real category is known.
    assert SHELL_JS.count("function groGuessCategory(") == 1
    assert SHELL_JS.count("category: groGuessCategory(name)") == 2
    assert "category: 'other' }" not in SHELL_JS
    assert "category: 'other' }" not in SHELL_JS.split("async function groAddItem(", 1)[1].split("\n  }\n", 1)[0]


# --- 4. the list shows the unsorted things ----------------------------------


@_needs_node
def test_the_anywhere_card_lists_the_unsorted_rows_after_the_stores():
    """The unsorted things are rows on the list — on the "Anywhere" card,
    last, after every store card (2026-09-18; a "Not sorted yet" card of
    their own before that) — each with its ⋯ and its tick."""
    out = _node("""
loadGrocery().then(function () {
  var html = groListHtml(groceryState.data);
  var card = html.slice(html.indexOf('gro-anywhere'));
  console.log(JSON.stringify({
    cards: cards(html), counts: counts(html), card: card,
    order: [html.indexOf('data-gro="goto-sort"'), html.indexOf('data-store="Costco"'), html.indexOf('gro-anywhere')]
  }));
});
""")
    assert out["cards"] == ["Costco", "Anywhere"]
    assert out["counts"] == ["0 of 1", "0 of 2"]
    card = out["card"]
    assert "Milk" in card and "Foil" in card
    assert '<span class="gro-line-qty">2 L</span>' in card
    assert card.count('data-gro="row-menu"') == 2, "each row keeps its ⋯, which is how one gets a store from here"
    assert card.count('data-gro="line-tick"') == 4, "and its tick (the row and its box)"
    a, b, c = out["order"]
    assert 0 <= a < b < c, "the sort row at the top, the stores, then the loose things"


@_needs_node
def test_any_and_not_sorted_share_the_anywhere_card_and_a_row_is_on_it_once():
    out = _node("""
BY_STORE.stores[1].sections[0].items.push({ id: 4, item: 'Eggs', quantity: '12', store: '', store_decided: 1, category: 'dairy' });
loadGrocery().then(function () {
  var html = groListHtml(groceryState.data);
  var anywhere = html.slice(html.indexOf('gro-anywhere'));
  console.log(JSON.stringify({ cards: cards(html), counts: counts(html),
    anywhereHasEggs: anywhere.indexOf('Eggs') !== -1, anywhereHasMilk: anywhere.indexOf('Milk') !== -1,
    eggsRows: (html.match(/Eggs</g) || []).length, toSort: groUnsorted(groceryState.data).length }));
});
""")
    assert out["cards"] == ["Costco", "Anywhere"]
    assert out["counts"] == ["0 of 1", "0 of 3"]
    assert out["anywhereHasEggs"] and out["anywhereHasMilk"]
    assert out["eggsRows"] == 1
    assert out["toSort"] == 2, "the answered 'Any' row is not to sort; the other two are"


@_needs_node
def test_a_list_that_is_nothing_but_unsorted_things_still_reads_and_still_shops():
    """No shop has anything tagged yet (a fresh approval, no remembered
    stores): the rows are on the list, tickable, on the one card there is.
    Until 2026-09-15 this household saw the sort queue instead."""
    out = _node("""
BY_STORE.stores = [BY_STORE.stores[1]];
loadGrocery().then(function () {
  var html = groListHtml(groceryState.data);
  console.log(JSON.stringify({ step: groceryState.step, cards: cards(html), ticks: (html.match(/data-gro="line-tick"/g) || []).length / 2,
    band: groBandLine(groceryState.data), dock: groDockHtml(groceryState.data, 'list') }));
});
""")
    assert out["step"] == "list"
    assert out["cards"] == ["Anywhere"]
    assert out["ticks"] == 2
    assert out["band"] == "2 things."
    assert "dock-primary" not in out["dock"] and 'data-gro="add-open"' in out["dock"], "no action on the root — ticking a row is it; the dock holds Add something"


@_needs_node
def test_a_household_with_one_shop_or_none_keeps_its_one_card():
    """groUnsorted is empty when sorting isn't a question, so "Anywhere"
    never appears for them — their loose pile is the whole list, on the
    one card there is: their shop's, or "Your list" for a household that
    named none (Emily, 2026-09-09: 'One list is fine' means what it says)."""
    out = _node("""
BY_STORE.stores = [BY_STORE.stores[1]];
groceryState.usualStores = [];
loadGrocery().then(function () {
  var none = groListHtml(groceryState.data);
  var noneBand = groBandLine(groceryState.data);
  groceryState.usualStores = ['Costco'];
  var one = groListHtml(groceryState.data);
  console.log(JSON.stringify({ none: cards(none), noneHasMilk: none.indexOf('Milk') !== -1, noneBand: noneBand,
    one: cards(one), oneCounts: counts(one), oneBand: groBandLine(groceryState.data) }));
});
""")
    assert out["none"] == ["Your list"] and out["noneHasMilk"] is True
    assert out["noneBand"] == "2 things.", "a household that named no shop is not told it has one"
    assert out["one"] == ["Costco"] and out["oneCounts"] == ["0 of 2"]
    assert out["oneBand"] == "2 things, one store."


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
  clickIfRendered({ gro: 'stores-prompt-done' });
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
    assert "dock-primary" not in out["asking"]["dock"], "the card's button is the screen's one apricot while it is up (Rule 5)"
    assert out["dismissed"] is True and out["posted"] == 1
    assert out["cardAfter"] is False and out["listAfter"] is True


# --- 6. the heading is one line to change -----------------------------------


def test_the_heading_is_one_line_to_change():
    assert "var GRO_ANYWHERE_CARD = 'Anywhere';" in SHELL_JS
    assert SHELL_JS.count("GRO_ANYWHERE_CARD,") == 1
    assert SHELL_JS.count(">Anywhere<") == 0
    assert "GRO_UNSORTED_SECTION" not in SHELL_JS, "the 'Not sorted yet' card folded into Anywhere (2026-09-18)"
