"""
Shop: the list is the checklist (Emily, 2026-09-18, Loop Board
3df1f4c0-5231-81eb-9601-fc7a62a8f116; mockup board 16-shopping-list).

Shop's root is the band ("This week · 14 things, two stores."), the add
row, then one card per store in the household's stop order — the name,
"N of M" at the right, one tickable row per thing — and a final
"Anywhere" card for the loose things. A tick marks the thing bought on
the screen at once and on the server when it can be, says "Changes saved
· Put back", and when every row on a card is ticked the head reads "Done
at Costco". When every card is done the list's finished moment stands at
the top of it. The trip screens — "Start the trip", "Where are we
headed?", the stop-by-stop list with a trolley, "Where next?", the
wrap-up, the paused-trip dock — are gone; what they wrote server-side the
tick writes now.

Behaviour runs under node against shell.js's own functions
(tests/shop_harness) — the markup the cards render, the click handler,
the tick's route, and the offline queue with the real
static/grocery-offline.js in the seat it has in the browser. The bought
window is driven over real HTTP against the real route.
"""
from __future__ import annotations

import json
from pathlib import Path

import nodeharness
from app import tools
from app.db import get_conn
from app.tools._shared import household_id
from shop_harness import CLICK, FIXTURE, STUB, grocery_block, needs_node, run

REPO = Path(__file__).resolve().parent.parent
OFFLINE = REPO / "static" / "grocery-offline.js"

_needs_node = needs_node
_node = run

# The mockup's list: two stores in stop order (Costco first — the order the
# household named them), two of six bought at Costco, nothing at Loblaws
# yet, and one loose thing with no store.
_MOCKUP = """
function mockup() {
  groceryState.data = { stores: {
    Unassigned: { sections: [{ section: 'other', items: [
      { id: 20, item: 'Foil', quantity: '', store: '', store_decided: 0, category: 'other', status: 'needed' }] }], purchased: [], inCart: [] },
    Loblaws: { sections: [
      { section: 'produce', items: [
        { id: 10, item: 'Lemons', quantity: '3', store: 'Loblaws', store_decided: 1, category: 'produce', status: 'needed' },
        { id: 11, item: 'Parsley', quantity: '1 bunch', store: 'Loblaws', store_decided: 1, category: 'produce', status: 'needed' }] },
      { section: 'dairy', items: [
        { id: 12, item: 'Greek yogurt', quantity: '750 g', store: 'Loblaws', store_decided: 1, category: 'dairy', status: 'needed' }] }],
      purchased: [], inCart: [] },
    Costco: { sections: [
      { section: 'pantry', items: [
        { id: 3, item: 'Orzo', quantity: '1 box', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' },
        { id: 5, item: 'Black beans', quantity: '2 cans', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' }] },
      { section: 'meat/seafood', items: [
        { id: 4, item: 'Salmon', quantity: '4 fillets', store: 'Costco', store_decided: 1, category: 'meat/seafood', status: 'needed' }] },
      { section: 'other', items: [
        { id: 6, item: 'Tortillas', quantity: '1 pack', store: 'Costco', store_decided: 1, category: 'other', status: 'needed' }] }],
      purchased: [
        { id: 1, item: 'Chicken thighs', quantity: '2 lb', store: 'Costco', store_decided: 1, category: 'meat/seafood', status: 'purchased' },
        { id: 2, item: 'Ground beef', quantity: '1 lb', store: 'Costco', store_decided: 1, category: 'meat/seafood', status: 'purchased' }],
      inCart: [] }
  } };
  groceryState.usualStores = ['Costco', 'Loblaws'];
  groceryState.storesPromptDismissed = true;
  groceryState.step = 'list';
  return groceryState.data;
}
function card(html, name) {
  var re = name ? new RegExp('<div class="gro-store[^"]*" data-store="' + name + '">') : /<div class="gro-store gro-anywhere[^"]*">/;
  var at = html.search(re);
  if (at === -1) return '';
  var rest = html.slice(at);
  var next = rest.slice(1).search(/<div class="gro-store[ "]/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}
function rows(cardHtml) {
  return (cardHtml.match(/<div class="gro-row gro-line[^"]*" data-gro="line-tick" data-id="(\\d+)"/g) || [])
    .map(function (m) { return /data-id="(\\d+)"/.exec(m)[1]; });
}
function struck(cardHtml) {
  return (cardHtml.match(/<div class="gro-row gro-line done[^"]*" data-gro="line-tick" data-id="(\\d+)"/g) || [])
    .map(function (m) { return /data-id="(\\d+)"/.exec(m)[1]; });
}
"""


def _list(body: str):
    return _node(_MOCKUP + body)


# --- 1. the cards ------------------------------------------------------------


@_needs_node
def test_the_root_is_the_band_and_one_card_per_store_in_stop_order_with_add_in_the_dock():
    out = _list("""
mockup();
var html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  band: [groBandEyebrow(groceryState.data), groBandLine(groceryState.data)],
  cards: cards(html), counts: counts(html),
  order: [html.indexOf('id="gro-add-item"'), html.indexOf('data-store="Costco"'), html.indexOf('data-store="Loblaws"'), html.indexOf('gro-anywhere')],
  preShop: html.indexOf('gro-add'),
  dock: groDockHtml(groceryState.data, 'list'),
  crumb: groHeadFor(groceryState.data, 'list').back
}));
""")
    assert out["band"] == ["This week", "10 things, two stores."]
    assert out["cards"] == ["Costco", "Loblaws", "Anywhere"], "stop order, the loose pile last"
    assert out["counts"] == ["2 of 6", "0 of 3", "0 of 1"]
    a, b, c, d = out["order"]
    assert a == -1, "no add row at the top of the list (2026-09-21: it is the dock's button)"
    assert 0 <= b < c < d, "the cards, in stop order"
    assert out["preShop"] == -1
    assert 'data-gro="add-open"' in out["dock"] and "Add something" in out["dock"], "the dock holds Add something"
    assert "dock-primary" not in out["dock"] and "gro-primary" not in out["dock"], "and nothing apricot: ticking a row is the action"


@_needs_node
def test_stop_order_is_the_order_the_household_named_its_shops():
    out = _list("""
mockup();
groceryState.usualStores = ['Loblaws', 'Costco'];
var named = cards(groListHtml(groceryState.data));
groceryState.usualStores = [];
groceryState.data.stores['T&T'] = { sections: [{ section: 'other', items: [
  { id: 30, item: 'Rice', quantity: '', store: 'T&T', store_decided: 1, category: 'pantry', status: 'needed' }] }], purchased: [], inCart: [] };
groceryState.usualStores = ['Costco'];
var unnamed = cards(groListHtml(groceryState.data));
console.log(JSON.stringify({ named: named, unnamed: unnamed }));
""")
    assert out["named"] == ["Loblaws", "Costco", "Anywhere"]
    assert out["unnamed"] == ["Costco", "Loblaws", "T&amp;T", "Anywhere"], "named shops first, then the list's own, the pile last"


@_needs_node
def test_a_card_is_a_tick_per_row_with_the_bought_ones_struck():
    out = _list("""
mockup();
var html = groListHtml(groceryState.data);
var costco = card(html, 'Costco');
console.log(JSON.stringify({
  rows: rows(costco), struck: struck(costco),
  thighs: /data-gro="line-tick" data-id="1" data-bought="1"/.test(costco) &&
          /class="gro-box checked" role="checkbox" aria-checked="true" data-gro="line-tick" data-id="1" data-bought="1" aria-label="Put Chicken thighs back on the list"/.test(costco),
  orzo: /class="gro-box" role="checkbox" aria-checked="false" data-gro="line-tick" data-id="3" data-bought="0" aria-label="Bought Orzo"/.test(costco),
  qty: costco.indexOf('<span class="gro-line-qty">2 lb</span>') !== -1,
  menus: (costco.match(/data-gro="row-menu"/g) || []).length,
  aisles: costco.indexOf('gro-eyebrow') !== -1
}));
""")
    assert out["rows"] == ["1", "2", "4", "5", "3", "6"], "the server's aisle order, then the name — a tick never moves a row"
    assert out["struck"] == ["1", "2"]
    assert out["thighs"] is True and out["orzo"] is True
    assert out["qty"] is True
    assert out["menus"] == 6, "every row keeps its ⋯"
    assert out["aisles"] is False, "flat rows, no aisle eyebrows (the mockup)"


@_needs_node
def test_a_card_with_every_row_bought_reads_done_at_the_store():
    out = _list("""
mockup();
var costco = groceryState.data.stores.Costco;
costco.sections.forEach(function (s) { s.items.forEach(function (it) { it.status = 'purchased'; costco.purchased.push(it); }); });
costco.sections = [];
var html = groListHtml(groceryState.data);
console.log(JSON.stringify({ cards: cards(html), counts: counts(html), done: /class="gro-store is-done" data-store="Costco"/.test(html),
  stopsLeft: groStoresWithNeeded(groceryState.data), finished: groListDone(groceryState.data), shopDone: html.indexOf('gro-shop-done-card') !== -1 }));
""")
    assert out["cards"] == ["Done at Costco", "Loblaws", "Anywhere"]
    assert out["counts"] == ["6 of 6", "0 of 3", "0 of 1"]
    assert out["done"] is True
    assert out["stopsLeft"] == ["Loblaws"], "a done card keeps its place; the band's stop count drops it"
    assert out["finished"] is False and out["shopDone"] is False, "one store done is not the shopping done"


@_needs_node
def test_the_anywhere_card_holds_the_loose_things_and_says_all_bought():
    out = _list("""
mockup();
groceryState.data.stores.Unassigned.sections[0].items.push(
  { id: 21, item: 'Cling film', quantity: '1', store: '', store_decided: 1, category: 'other', status: 'needed' });
var before = groListHtml(groceryState.data);
var u = groceryState.data.stores.Unassigned;
u.sections[0].items.forEach(function (it) { it.status = 'purchased'; u.purchased.push(it); });
u.sections = [];
var after = groListHtml(groceryState.data);
console.log(JSON.stringify({ before: cards(before), beforeRows: rows(card(before)), after: cards(after), afterCount: counts(after)[2] }));
""")
    assert out["before"] == ["Costco", "Loblaws", "Anywhere"]
    assert out["beforeRows"] == ["21", "20"], "answered 'Any' and not-yet-asked alike, by name"
    assert out["after"] == ["Costco", "Loblaws", "All bought"]
    assert out["afterCount"] == "2 of 2"


@_needs_node
def test_a_household_that_named_no_shop_has_one_card_and_finishes_with_done_shopping():
    out = _list("""
var weekState = { data: null };
setUp(2, [], []);
var html = groListHtml(groceryState.data);
var u = groceryState.data.stores.Unassigned;
u.sections[0].items.forEach(function (it) { it.status = 'purchased'; u.purchased.push(it); });
u.sections = [];
var done = groListHtml(groceryState.data);
console.log(JSON.stringify({ cards: cards(html), counts: counts(html), band: groBandLine(groceryState.data), done: cards(done) }));
""")
    assert out["cards"] == ["Your list"]
    assert out["counts"] == ["0 of 2"]
    assert out["band"] == "2 things."
    assert out["done"] == ["Done shopping"]


# --- 2. the tick ----------------------------------------------------------------


@_needs_node
def test_ticking_a_row_writes_purchased_and_says_changes_saved_with_put_back():
    """Through groTick, straight to 'purchased' on the row's own status
    route — the one the trip's "Done at Costco" used to write — and the
    toast's Put back is 'needed' on the same route."""
    out = _list("""
mockup();
clickIfRendered({ gro: 'line-tick', id: '3', bought: '0' });
settle(function () {
  var afterTick = { posts: POSTS.map(function (p) { return [p.url, p.body.status]; }), toast: lastToast() };
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({ afterTick: afterTick, posts: POSTS.map(function (p) { return [p.url, p.body.status]; }), toast: lastToast() }));
  });
});
""")
    assert out["afterTick"]["posts"] == [["/api/grocery-list/3/status", "purchased"]]
    assert out["afterTick"]["toast"] == {"msg": "Changes saved", "action": "Put back"}
    assert out["posts"][1] == ["/api/grocery-list/3/status", "needed"], "Put back is the same tap the other way"
    assert out["toast"]["msg"] == "Changes saved"
    assert "in_cart" not in json.dumps(out["posts"]), "nothing is parked in a trolley any more"


@_needs_node
def test_unticking_a_bought_row_puts_it_back_and_offers_undo():
    out = _list("""
mockup();
clickIfRendered({ gro: 'line-tick', id: '1', bought: '1' });
settle(function () {
  console.log(JSON.stringify({ posts: POSTS.map(function (p) { return [p.url, p.body.status]; }), toast: lastToast() }));
});
""")
    assert out["posts"] == [["/api/grocery-list/1/status", "needed"]]
    assert out["toast"] == {"msg": "Changes saved", "action": "Undo"}


@_needs_node
def test_the_last_tick_on_a_store_records_the_stop_the_way_done_at_costco_did():
    """The old trip closed a stop with /api/shopping-trips/close on "Done
    at Costco" (tools.close_shopping_trip — bookkeeping nothing reads back
    yet). The tick that finishes a card writes the same record, once; a
    tick that leaves something on the card writes none; the loose pile's
    card and the one-list stand-in are recorded as no shop."""
    out = _node(_MOCKUP + """
window.PomonaGroceryOffline = require(""" + json.dumps(str(OFFLINE)) + """);
""" + grocery_block() + """
mockup();
// Costco has four left: tick three, then the fourth.
clickIfRendered({ gro: 'line-tick', id: '3', bought: '0' });
clickIfRendered({ gro: 'line-tick', id: '4', bought: '0' });
clickIfRendered({ gro: 'line-tick', id: '5', bought: '0' });
var beforeLast = closes().length;
clickIfRendered({ gro: 'line-tick', id: '6', bought: '0' });
var costco = closes().map(function (p) { return p.body; });
clickIfRendered({ gro: 'line-tick', id: '20', bought: '0' });
settle(function () {
  console.log(JSON.stringify({ beforeLast: beforeLast, costco: costco, all: closes().map(function (p) { return p.body; }),
    cardNow: cards(groListHtml(groceryState.data)) }));
});
""")
    assert out["beforeLast"] == 0, "three of four ticked: the stop is not done"
    assert out["costco"] == [{"store": "Costco", "item_count": 6}]
    assert out["all"] == [{"store": "Costco", "item_count": 6}, {"store": "", "item_count": 1}], "the loose pile is recorded as no shop"
    assert out["cardNow"] == ["Done at Costco", "Loblaws", "All bought"]


@_needs_node
def test_every_card_done_puts_the_finished_moment_at_the_top_of_the_list():
    out = _list("""
mockup();
Object.keys(groceryState.data.stores).forEach(function (name) {
  var s = groceryState.data.stores[name];
  s.sections.forEach(function (sec) { sec.items.forEach(function (it) { it.status = 'purchased'; s.purchased.push(it); }); });
  s.sections = [];
});
var tonightDinnerName = function () { return 'Tacos'; };
var html = groListHtml(groceryState.data);
groceryState.shopDoneHandoffDismissed = true;
var folded = groListHtml(groceryState.data);
console.log(JSON.stringify({
  finished: groListDone(groceryState.data), band: groBandLine(groceryState.data),
  moment: html.indexOf('gro-shop-done-card') !== -1 && html.indexOf('That’s the shopping done.') !== -1,
  first: html.indexOf('gro-shop-done-card') < html.indexOf('data-store="Costco"'),
  cardsStay: cards(html), tonight: html.indexOf('data-gro="shop-done-tonight"') !== -1,
  folded: folded.indexOf('gro-shop-done-dismissed') !== -1 && folded.indexOf('See tonight’s dinner') !== -1,
  dock: groDockHtml(groceryState.data, 'list')
}));
""")
    assert out["finished"] is True
    assert out["band"] == "10 things, two stores."
    assert out["moment"] is True and out["first"] is True
    assert out["cardsStay"] == ["Done at Costco", "Done at Loblaws", "All bought"], "the ticked cards stay under it, so a mis-tick can be put back"
    assert out["tonight"] is True
    assert out["folded"] is True
    assert 'data-gro="add-open"' in out["dock"] and "dock-primary" not in out["dock"]


# --- 3. the tick with no signal -----------------------------------------------
# The real static/grocery-offline.js in the seat it has in the browser, and a
# fetch that throws the way a phone with no signal does.


_OFFLINE_HARNESS = STUB + """
window.PomonaGroceryOffline = require(""" + json.dumps(str(OFFLINE)) + """);
var DEAD = false;
fetch = function (url, opts) {
  if (DEAD) return Promise.reject(new TypeError('Failed to fetch'));
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({}); } });
};
"""


def _offline(body: str):
    res = nodeharness.run_node(_OFFLINE_HARNESS + grocery_block() + CLICK + FIXTURE + _MOCKUP + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_a_tick_with_no_signal_shows_at_once_is_queued_and_is_sent_when_signal_returns():
    out = _offline("""
mockup();
groOffline.setHousehold(1);
DEAD = true;
navigator.onLine = false;
clickIfRendered({ gro: 'line-tick', id: '3', bought: '0' });
settle(function () {
  var html = groListHtml(groceryState.data);
  var offline = {
    struck: struck(card(html, 'Costco')), count: counts(html)[0],
    queued: groOffline.pending().map(function (op) { return [op.id, op.status]; }),
    posts: POSTS.length, toast: lastToast(), offlineFlag: groceryState.offline
  };
  DEAD = false;
  navigator.onLine = true;
  groReplayQueue().then(function () {
    settle(function () {
      console.log(JSON.stringify({ offline: offline, sent: POSTS.map(function (p) { return [p.url, p.body.status]; }), left: groOffline.pending().length }));
      process.exit(0);
    });
  });
});
""")
    assert out["offline"]["struck"] == ["1", "2", "3"], "on the screen at once"
    assert out["offline"]["count"] == "3 of 6"
    assert out["offline"]["queued"] == [["3", "purchased"]], "the queue captured the tick"
    assert out["offline"]["posts"] == 0
    assert out["offline"]["toast"]["msg"] == "Changes saved"
    assert out["offline"]["offlineFlag"] is True
    assert out["sent"][0] == ["/api/grocery-list/3/status", "purchased"], "sent when signal returned"
    assert out["left"] == 0


# --- 4. the bought window, over HTTP ------------------------------------------


def _bought(client) -> list[str]:
    return [it["item"] for s in client.get("/api/grocery-list?status=bought").json()["sections"] for it in s["items"]]


def test_a_tick_lands_in_the_bought_view_and_a_put_back_leaves_it(signed_in):
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Orzo", "quantity": "1 box"}).json()["item_id"]
    assert _bought(signed_in) == []
    res = signed_in.post(f"/api/grocery-list/{item_id}/status", json={"status": "purchased"})
    assert res.status_code == 200 and res.json()["inventory_added"] is True, "the kitchen add the trip used to do"
    assert _bought(signed_in) == ["Orzo"]
    signed_in.post(f"/api/grocery-list/{item_id}/status", json={"status": "needed"})
    assert _bought(signed_in) == []


def test_the_bought_view_is_the_rows_ticked_since_the_list_was_last_built(signed_in):
    """A purchased row is kept for life (the staples learn from it), so
    what the checklist draws is the ticks since the last approval
    (weekly_plans.approved_at) — or the last seven days for a household
    that has never approved a week."""
    old = signed_in.post("/api/grocery-list/add", json={"item": "Lemons", "quantity": "3"}).json()["item_id"]
    new = signed_in.post("/api/grocery-list/add", json={"item": "Parsley", "quantity": "1 bunch"}).json()["item_id"]
    for item_id in (old, new):
        signed_in.post(f"/api/grocery-list/{item_id}/status", json={"status": "purchased"})
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET inventory_added_at = datetime('now', '-10 days') WHERE id = ?", (old,))
    conn.commit()
    conn.close()
    assert _bought(signed_in) == ["Parsley"], "no plan ever approved: the last seven days"
    assert sorted(it["item"] for it in tools.list_grocery_list(status="purchased")) == ["Lemons", "Parsley"], (
        "the lifetime view is untouched"
    )

    # A week approved yesterday: only ticks since then are this list's.
    conn = get_conn()
    conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, approved_at) "
        "VALUES (?, date('now'), 'approved', datetime('now', '-1 day'))",
        (household_id(),),
    )
    conn.execute("UPDATE grocery_items SET inventory_added_at = datetime('now', '-3 days') WHERE id = ?", (new,))
    conn.commit()
    conn.close()
    assert _bought(signed_in) == []
    fresh = signed_in.post("/api/grocery-list/add", json={"item": "Salmon", "quantity": "4 fillets"}).json()["item_id"]
    signed_in.post(f"/api/grocery-list/{fresh}/status", json={"status": "purchased"})
    assert _bought(signed_in) == ["Salmon"]


def test_a_row_left_in_a_trolley_by_an_older_build_reads_as_bought(signed_in):
    """Nothing writes in_cart any more, but a phone mid-trip at deploy time
    may have; the row is drawn ticked rather than lost."""
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Oats", "quantity": "1 bag"}).json()["item_id"]
    signed_in.post(f"/api/grocery-list/{item_id}/status", json={"status": "in_cart"})
    view = signed_in.get("/api/grocery-list?status=bought").json()["sections"]
    assert [(it["item"], it["status"]) for s in view for it in s["items"]] == [("Oats", "in_cart")]


def test_closing_a_stop_from_the_list_records_it_as_before(signed_in):
    res = signed_in.post("/api/shopping-trips/close", json={"store": "Costco", "item_count": 6})
    assert res.status_code == 200 and res.json() == {"store": "Costco", "item_count": 6}
    conn = get_conn()
    row = conn.execute("SELECT store, item_count FROM shopping_trips ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert (row["store"], row["item_count"]) == ("Costco", 6)
