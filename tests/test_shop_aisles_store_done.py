"""
Shop: rows grouped by aisle inside each store card, and a store done as a
small moment that rolls up to one line (Emily, 2026-09-22; Loop Board
3e31f4c0-5231-81c2-b886-c2696cddf2a9 and 3e31f4c0-5231-81f5-8f37-c1377b4d557e;
mockups S1-add-in-dock — the Costco card with aisle eyebrows — and
S3-store-done).

Card 1. Inside every store card (and the Anywhere card) the rows sit
under 10px/800 uppercase --ink-muted eyebrows naming the aisle —
Produce, Dairy & eggs, Meat & seafood, Pantry, Frozen — in the card's
walking order (the taxonomy's default unless the payload carries a
store's own), Other last, no eyebrow over an empty aisle, a --hairline
above every eyebrow but a card's first. "N of M", the done state and the
tick are unchanged, and a tick never moves a row. (The 2026-09-18
checklist had dropped the eyebrows; this brings the grouping back inside
the new cards.)

Card 2. The last tick on a store's card: the toast says "That's Costco
done — 13 things." with the tick, the head reads "Done at Costco" for a
beat, then the card rolls up — rows fold, the one-line row settles, the
app's fifth animation — to a --celadon-tint row below the stores still
to do: a round --celadon tick, "Done at Costco", "13 things · tap to see
them", a chevron. A tap opens it in place; a put-back un-rolls it. Page-
view state, so a reload shows done cards rolled up. When every card is
done the finished moment stands above the rolled-up rows. Instant under
prefers-reduced-motion.

Behaviour runs under node against shell.js's own functions
(tests/shop_harness), with the real static/grocery-offline.js in its
seat for the ticks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from shop_harness import grocery_block, needs_node, run
from test_shop_checklist import _MOCKUP

REPO = Path(__file__).resolve().parent.parent
OFFLINE = REPO / "static" / "grocery-offline.js"
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
DESIGN_SYSTEM = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")

_needs_node = needs_node

# The mockup list (test_shop_checklist._MOCKUP), the offline module in its
# seat so a tick moves the row at once, and a few readers of the markup.
_HARNESS = _MOCKUP + """
window.PomonaGroceryOffline = require(""" + json.dumps(str(OFFLINE)) + """);
""" + grocery_block() + """
// The toast, with its fourth argument (the icon) kept.
showToast = function (msg, action, hold, opts) { TOASTS.push({ msg: msg, action: action, hold: hold, icon: !!(opts && opts.icon) }); };
function lastToastFull() {
  var t = TOASTS[TOASTS.length - 1];
  return t ? { msg: t.msg, action: t.action ? t.action.label : null, icon: t.icon } : null;
}
// A card's eyebrows in order, and the row ids under each.
function aisles(cardHtml) {
  var out = [];
  var re = /<div class="gro-aisle" data-aisle="([^"]*)"><span class="gro-eyebrow">([^<]*)<\\/span><\\/div>|data-gro="line-tick" data-id="(\\d+)" data-bought="[01]" aria-label/g;
  var m;
  while ((m = re.exec(cardHtml)) !== null) {
    if (m[1] !== undefined) out.push({ aisle: m[1], label: m[2], rows: [] });
    else if (out.length) out[out.length - 1].rows.push(m[3]);
    else out.push({ aisle: null, label: null, rows: [m[3]] });
  }
  return out;
}
function rolled(html) {
  return (html.match(/gro-rolled-title">([^<]*)</g) || []).map(function (m) { return /gro-rolled-title">([^<]*)</.exec(m)[1]; });
}
function rolledCard(html, name) {
  var re = name ? new RegExp('<div class="gro-store gro-rolled[^"]*" data-store="' + name + '">') : /<div class="gro-store gro-rolled[^"]*gro-anywhere">/;
  var at = html.search(re);
  if (at === -1) return '';
  var rest = html.slice(at);
  var next = rest.slice(1).search(/<div class="gro-store[ "]/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}
function finishCostco() {
  ['3', '4', '5', '6'].forEach(function (id) { clickIfRendered({ gro: 'line-tick', id: id, bought: '0' }); });
}
"""


def _node(body: str):
    return run(_HARNESS + body)


# --- Card 1: rows grouped by aisle --------------------------------------------


@_needs_node
def test_rows_sit_under_aisle_eyebrows_in_walking_order_with_other_last():
    out = _node("""
mockup();
var html = groListHtml(groceryState.data);
console.log(JSON.stringify({ costco: aisles(card(html, 'Costco')), loblaws: aisles(card(html, 'Loblaws')),
  counts: counts(html), eyebrow: /<span class="gro-eyebrow">Meat &amp; seafood<\\/span>/.test(html) }));
""")
    assert out["costco"] == [
        {"aisle": "meat/seafood", "label": "Meat &amp; seafood", "rows": ["1", "2", "4"]},
        {"aisle": "pantry", "label": "Pantry", "rows": ["5", "3"]},
        {"aisle": "other", "label": "Other", "rows": ["6"]},
    ], "the taxonomy's order, Other last; bought and needed rows alike under their aisle"
    assert out["loblaws"] == [
        {"aisle": "produce", "label": "Produce", "rows": ["10", "11"]},
        {"aisle": "dairy", "label": "Dairy &amp; eggs", "rows": ["12"]},
    ], "no eyebrow for an aisle with nothing in it"
    assert out["counts"] == ["2 of 6", "0 of 3", "0 of 1"], '"N of M" is unchanged'
    assert out["eyebrow"] is True


@_needs_node
def test_the_anywhere_card_is_grouped_the_same_way():
    out = _node("""
mockup();
groceryState.data.stores.Unassigned.sections.unshift({ section: 'produce', items: [
  { id: 22, item: 'Apples', quantity: '6', store: '', store_decided: 1, category: 'produce', status: 'needed' }] });
var html = groListHtml(groceryState.data);
console.log(JSON.stringify(aisles(card(html))));
""")
    assert out == [
        {"aisle": "produce", "label": "Produce", "rows": ["22"]},
        {"aisle": "other", "label": "Other", "rows": ["20"]},
    ]


@_needs_node
def test_a_store_with_a_walking_order_of_its_own_walks_it_and_other_is_still_last():
    """Nothing serves a store's `aisle_order` yet — the stores table's
    column is never written and speaks a different vocabulary — but the
    card reads one when the payload carries it, so the day it does, the
    cards walk the shop."""
    out = _node("""
mockup();
groceryState.data.stores.Costco.aisle_order = ['other', 'pantry', 'meat/seafood'];
var html = groListHtml(groceryState.data);
var order = aisles(card(html, 'Costco')).map(function (a) { return a.aisle; });
groceryState.data.stores.Costco.aisle_order = ['bakery', 'nonsense'];
var fallback = aisles(card(groListHtml(groceryState.data), 'Costco')).map(function (a) { return a.aisle; });
console.log(JSON.stringify({ order: order, fallback: fallback }));
""")
    assert out["order"] == ["pantry", "meat/seafood", "other"], "the store's order, Other last whatever it says"
    assert out["fallback"] == ["meat/seafood", "pantry", "other"], "an order naming no known aisle is the default"


@_needs_node
def test_a_category_the_taxonomy_does_not_know_is_other_and_meat_is_meat_and_seafood():
    out = _node("""
mockup();
groceryState.data.stores.Loblaws.sections.push({ section: 'other', items: [
  { id: 13, item: 'Bread', quantity: '1 loaf', store: 'Loblaws', store_decided: 1, category: 'bakery', status: 'needed' },
  { id: 14, item: 'Prawns', quantity: '300 g', store: 'Loblaws', store_decided: 1, category: 'seafood', status: 'needed' }] });
console.log(JSON.stringify(aisles(card(groListHtml(groceryState.data), 'Loblaws'))));
""")
    assert out == [
        {"aisle": "produce", "label": "Produce", "rows": ["10", "11"]},
        {"aisle": "dairy", "label": "Dairy &amp; eggs", "rows": ["12"]},
        {"aisle": "meat/seafood", "label": "Meat &amp; seafood", "rows": ["14"]},
        {"aisle": "other", "label": "Other", "rows": ["13"]},
    ]


@_needs_node
def test_a_tick_changes_a_rows_look_and_never_its_aisle_or_its_place():
    out = _node("""
mockup();
var before = aisles(card(groListHtml(groceryState.data), 'Costco'));
clickIfRendered({ gro: 'line-tick', id: '5', bought: '0' });
var html = groListHtml(groceryState.data);
console.log(JSON.stringify({ before: before, after: aisles(card(html, 'Costco')), struck: struck(card(html, 'Costco')), counts: counts(html) }));
""")
    assert [a["aisle"] for a in out["before"]] == ["meat/seafood", "pantry", "other"]
    assert out["after"] == out["before"], "same eyebrows, same rows under each"
    assert out["struck"] == ["1", "2", "5"]
    assert out["counts"][0] == "3 of 6"


@_needs_node
def test_the_big_meal_split_keeps_its_headings_with_a_run_of_aisles_under_each():
    out = _node("""
mockup();
groceryState.data.shopSplit = { early: { label: 'For Thanksgiving — buy by Friday' }, fresh: { label: 'For Thanksgiving — buy fresh on Sunday' } };
groceryState.data.stores.Costco.sections[0].items[0].shop_timing = 'early';   // Orzo (3)
groceryState.data.stores.Costco.sections[1].items[0].shop_timing = 'fresh';   // Salmon (4)
var costco = card(groListHtml(groceryState.data), 'Costco');
var marks = [];
var re = /<div class="gro-trip"><span class="gro-trip-label">([^<]*)<\\/span><\\/div>|data-aisle="([^"]*)"|data-id="(\\d+)" data-bought="[01]" aria-label/g;
var m;
while ((m = re.exec(costco)) !== null) marks.push(m[1] ? 'TRIP:' + m[1] : m[2] ? 'AISLE:' + m[2] : m[3]);
console.log(JSON.stringify(marks));
""")
    assert out == [
        "AISLE:meat/seafood", "1", "2", "AISLE:pantry", "5", "AISLE:other", "6",
        "TRIP:For Thanksgiving — buy by Friday", "AISLE:pantry", "3",
        "TRIP:For Thanksgiving — buy fresh on Sunday", "AISLE:meat/seafood", "4",
    ]


def test_the_eyebrow_is_the_ten_px_caps_one_with_a_hairline_above_all_but_the_first():
    assert "var GRO_AISLE_LABELS = {" in SHELL_JS
    assert "'<span class=\"gro-eyebrow\">' + escapeHtml(GRO_AISLE_LABELS[aisle] || aisle) + '</span></div>'" in SHELL_JS
    eyebrow = re.search(r"\.gro-eyebrow \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "font-size: 10px" in eyebrow and "font-weight: 800" in eyebrow
    assert "text-transform: uppercase" in eyebrow and "color: var(--ink-muted)" in eyebrow
    aisle = re.search(r"\n\.gro-aisle \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "border-top: 1.5px solid var(--hairline)" in aisle
    assert ".gro-card-rows > .gro-aisle:first-child, .gro-trip + .gro-aisle { border-top: 0; }" in SHELL_CSS
    assert ".gro-aisle + .gro-line { border-top: 0; }" in SHELL_CSS, "the eyebrow is the separator for the row under it"


# --- Card 2: a store done -----------------------------------------------------


@_needs_node
def test_the_last_tick_says_so_with_the_tick_and_holds_the_card_for_a_beat_then_rolls_it_up():
    out = _node("""
mockup();
finishCostco();
var beat = groListHtml(groceryState.data);
var during = { cards: cards(beat), counts: counts(beat), rolled: rolled(beat), doneBeat: groceryState.doneBeat, toast: lastToastFull(),
  head: /class="gro-store is-done" data-store="Costco"/.test(beat) };
setTimeout(function () {
  var after = groListHtml(groceryState.data);
  console.log(JSON.stringify({ during: during, after: { cards: cards(after), rolled: rolled(after), doneBeat: groceryState.doneBeat,
    below: after.indexOf('gro-rolled') > after.indexOf('gro-anywhere'), sub: /gro-rolled-sub">([^<]*)</.exec(after)[1],
    tick: /gro-rolled-tick"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"/.test(after),
    chevron: rolledCard(after, 'Costco').indexOf('gro-chev') !== -1,
    tap: /data-gro="rolled-toggle" data-store="Costco" aria-expanded="false"/.test(after) } }));
}, 700);
""")
    assert out["during"]["toast"] == {"msg": "That’s Costco done — 6 things.", "action": "Undo", "icon": True}
    assert out["during"]["cards"] == ["Done at Costco", "Loblaws", "Anywhere"], "for the beat the card stands where it was"
    assert out["during"]["counts"][0] == "6 of 6" and out["during"]["head"] is True
    assert out["during"]["rolled"] == [] and out["during"]["doneBeat"] == "Costco"
    a = out["after"]
    assert a["cards"] == ["Loblaws", "Anywhere"] and a["rolled"] == ["Done at Costco"] and a["doneBeat"] is None
    assert a["below"] is True, "rolled up below the stores still to do"
    assert a["sub"] == "6 things", "the count alone — the chevron says it opens"
    assert a["tick"] is True and a["chevron"] is True and a["tap"] is True


def test_the_beat_is_about_six_hundred_milliseconds():
    assert "var GRO_STORE_DONE_BEAT_MS = 600;" in SHELL_JS


@_needs_node
def test_a_tick_that_leaves_something_on_the_card_is_the_ordinary_changes_saved():
    out = _node("""
mockup();
clickIfRendered({ gro: 'line-tick', id: '3', bought: '0' });
console.log(JSON.stringify({ toast: lastToastFull(), doneBeat: groceryState.doneBeat }));
""")
    assert out == {"toast": {"msg": "Orzo was ticked off", "action": "Undo", "icon": False}, "doneBeat": None}


@_needs_node
def test_tap_opens_the_rolled_up_card_in_place_and_tap_again_folds_it():
    out = _node("""
mockup();
finishCostco();
groceryState.doneBeat = null;   // past the beat
clickIfRendered({ gro: 'rolled-toggle', store: 'Costco' });
var open = groListHtml(groceryState.data);
var openCard = rolledCard(open, 'Costco');
var opened = { rolled: rolled(open), cards: cards(open), isOpen: /class="gro-store gro-rolled is-done is-open" data-store="Costco"/.test(open),
  rows: rows(openCard), struck: struck(openCard), aisles: aisles(openCard).map(function (a) { return a.aisle; }),
  sub: /gro-rolled-sub">([^<]*)</.exec(openCard)[1], expanded: /aria-expanded="true"/.test(openCard),
  below: open.indexOf('gro-rolled') > open.indexOf('gro-anywhere') };
clickIfRendered({ gro: 'rolled-toggle', store: 'Costco' });
var shut = groListHtml(groceryState.data);
console.log(JSON.stringify({ opened: opened, shut: { rows: rows(rolledCard(shut, 'Costco')), isOpen: shut.indexOf('is-open') !== -1 } }));
""")
    o = out["opened"]
    assert o["rolled"] == ["Done at Costco"] and o["cards"] == ["Loblaws", "Anywhere"], "still one line at the foot — opened in place"
    assert o["isOpen"] is True and o["expanded"] is True and o["below"] is True
    assert o["rows"] == ["1", "2", "4", "5", "3", "6"] and o["struck"] == o["rows"], "the ticked rows, struck, in their aisles"
    assert o["aisles"] == ["meat/seafood", "pantry", "other"]
    assert o["sub"] == "6 things"
    assert out["shut"] == {"rows": [], "isOpen": False}


@_needs_node
def test_a_reload_shows_done_cards_rolled_up():
    """Page-view state: the data alone (every row bought) draws the card
    rolled up — a fresh groceryState has nothing open and no beat."""
    out = _node("""
mockup();
var costco = groceryState.data.stores.Costco;
costco.sections.forEach(function (s) { s.items.forEach(function (it) { it.status = 'purchased'; costco.purchased.push(it); }); });
costco.sections = [];
var html = groListHtml(groceryState.data);
console.log(JSON.stringify({ rolled: rolled(html), cards: cards(html), open: html.indexOf('is-open') !== -1, doneOpen: groceryState.doneOpen, doneBeat: groceryState.doneBeat }));
""")
    assert out == {"rolled": ["Done at Costco"], "cards": ["Loblaws", "Anywhere"], "open": False, "doneOpen": {}, "doneBeat": None}


@_needs_node
def test_put_back_on_a_rolled_up_card_un_rolls_it_into_the_to_do_section():
    out = _node("""
mockup();
finishCostco();
groceryState.doneBeat = null;
clickIfRendered({ gro: 'rolled-toggle', store: 'Costco' });
clickIfRendered({ gro: 'line-tick', id: '6', bought: '1' });
var html = groListHtml(groceryState.data);
var costco = card(html, 'Costco');
console.log(JSON.stringify({ cards: cards(html), counts: counts(html), rolled: rolled(html), toast: lastToastFull(),
  above: html.indexOf('data-store="Costco"') < html.indexOf('data-store="Loblaws"'),
  struck: struck(costco), doneOpen: groceryState.doneOpen,
  posts: POSTS.filter(function (p) { return p.url.indexOf('/6/status') !== -1; }).map(function (p) { return p.body.status; }) }));
""")
    assert out["cards"] == ["Costco", "Loblaws", "Anywhere"] and out["rolled"] == []
    assert out["counts"][0] == "5 of 6" and out["above"] is True, "a to-do card again, back in stop order"
    assert out["struck"] == ["1", "2", "4", "5", "3"]
    assert out["toast"] == {"msg": "Tortillas is back on the list", "action": "Undo", "icon": False}
    assert out["posts"] == ["purchased", "needed"], "the same row, the same tick, the other way"
    assert out["doneOpen"] == {}, "the next time it finishes it starts rolled up"


@_needs_node
def test_a_put_back_within_the_beat_calls_the_roll_up_off_and_undoing_a_put_back_rolls_it_back_up():
    out = _node("""
mockup();
finishCostco();
clickIfRendered({ gro: 'line-tick', id: '6', bought: '1' });
var calledOff = { doneBeat: groceryState.doneBeat, cards: cards(groListHtml(groceryState.data)) };
tapUndo();
var undone = { doneBeat: groceryState.doneBeat, cards: cards(groListHtml(groceryState.data)), rolled: rolled(groListHtml(groceryState.data)) };
setTimeout(function () {
  var html = groListHtml(groceryState.data);
  console.log(JSON.stringify({ calledOff: calledOff, undone: undone, later: { cards: cards(html), rolled: rolled(html), doneBeat: groceryState.doneBeat } }));
}, 700);
""")
    assert out["calledOff"] == {"doneBeat": None, "cards": ["Costco", "Loblaws", "Anywhere"]}
    assert out["undone"] == {"doneBeat": "Costco", "cards": ["Done at Costco", "Loblaws", "Anywhere"], "rolled": []}, "done again: its beat, then the roll-up"
    assert out["later"] == {"cards": ["Loblaws", "Anywhere"], "rolled": ["Done at Costco"], "doneBeat": None}


@_needs_node
def test_the_anywhere_card_and_the_one_list_stand_in_roll_up_quietly():
    """They are stops, not shops: no "That's X done" — the tick's own
    ordinary line ("Foil was ticked off · Undo") — but the same roll-up
    after the beat."""
    out = _node("""
mockup();
clickIfRendered({ gro: 'line-tick', id: '20', bought: '0' });
var anywhere = { toast: lastToastFull(), doneBeat: groceryState.doneBeat, cards: cards(groListHtml(groceryState.data)) };
var weekState = { data: null };
setUp(2, [], []);
clickIfRendered({ gro: 'line-tick', id: '1', bought: '0' });
clickIfRendered({ gro: 'line-tick', id: '2', bought: '0' });
var standIn = { toast: lastToastFull(), doneBeat: groceryState.doneBeat, cards: cards(groListHtml(groceryState.data)) };
setTimeout(function () {
  var html = groListHtml(groceryState.data);
  console.log(JSON.stringify({ anywhere: anywhere, standIn: standIn, later: { cards: cards(html), rolled: rolled(html),
    moment: html.indexOf('gro-shop-done-card') !== -1 && html.indexOf('gro-shop-done-card') < html.indexOf('gro-rolled') } }));
}, 700);
""")
    assert out["anywhere"] == {"toast": {"msg": "Foil was ticked off", "action": "Undo", "icon": False}, "doneBeat": "", "cards": ["Costco", "Loblaws", "All bought"]}
    assert out["standIn"] == {"toast": {"msg": "Thing 2 was ticked off", "action": "Undo", "icon": False}, "doneBeat": "Your list", "cards": ["Done shopping"]}
    assert out["later"] == {"cards": [], "rolled": ["Done shopping"], "moment": True}


@_needs_node
def test_every_store_done_puts_the_finished_moment_above_the_rolled_up_rows():
    out = _node("""
var weekState = { data: null };
mockup();
finishCostco();
['10', '11', '12', '20'].forEach(function (id) { clickIfRendered({ gro: 'line-tick', id: id, bought: '0' }); });
setTimeout(function () {
  var html = groListHtml(groceryState.data);
  var at = function (s) { return html.indexOf(s); };
  console.log(JSON.stringify({ finished: groListDone(groceryState.data), cards: cards(html), rolled: rolled(html),
    order: [at('gro-shop-done-card'), at('data-store="Costco"'), at('data-store="Loblaws"'), at('gro-anywhere')] }));
}, 700);
""")
    assert out["finished"] is True and out["cards"] == []
    assert out["rolled"] == ["Done at Costco", "Done at Loblaws", "All bought"], "in stop order, the pile last; they stay for the week"
    a, b, c, d = out["order"]
    assert 0 <= a < b < c < d


# --- the roll-up itself: the fifth animation ----------------------------------

# Just enough of a panel for groRollUp: the card, its rows block, and
# what the fold touches on them.
_PANEL = """
var RENDERS = 0;
renderGrocery = function () { RENDERS += 1; };
var REDUCE = false;
window.matchMedia = function () { return { matches: REDUCE }; };
function Rows(height) {
  this.classes = []; this.style = {}; this.offsetHeight = height; this.listeners = {};
  var self = this;
  this.classList = { add: function (c) { self.classes.push(c); }, contains: function (c) { return self.classes.indexOf(c) !== -1; }, remove: function () {} };
}
Rows.prototype.addEventListener = function (ev, fn) { this.listeners[ev] = fn; };
function CardEl(store, rows) {
  this.store = store; this.rows = rows; this.classes = [];
  var self = this;
  this.classList = { add: function (c) { self.classes.push(c); }, contains: function (c) { return self.classes.indexOf(c) !== -1; }, remove: function (c) { self.classes.splice(self.classes.indexOf(c), 1); } };
}
CardEl.prototype.getAttribute = function (k) { return k === 'data-store' ? this.store : null; };
CardEl.prototype.querySelector = function (sel) { return sel === '.gro-card-rows' ? this.rows : null; };
Object.defineProperty(CardEl.prototype, 'offsetHeight', { get: function () { return 56; } });
function panelWith(cards) {
  panels['grocery'] = { dataset: { built: '1' }, querySelectorAll: function () { return cards; } };
}
"""


@_needs_node
def test_the_roll_up_folds_the_rows_then_redraws_and_settles_the_one_line_row():
    out = run(_HARNESS + _PANEL + """
mockup();
var costco = groceryState.data.stores.Costco;
costco.sections.forEach(function (s) { s.items.forEach(function (it) { it.status = 'purchased'; costco.purchased.push(it); }); });
costco.sections = [];
groceryState.doneBeat = 'Costco';
var rowsEl = new Rows(312);
var cardEl = new CardEl('Costco', rowsEl);
panelWith([new CardEl('Loblaws', new Rows(200)), cardEl]);
groRollUp('Costco');
var folding = { classes: rowsEl.classes.slice(), height: rowsEl.style.height, renders: RENDERS, doneBeat: groceryState.doneBeat, hasEnd: !!rowsEl.listeners.transitionend };
// The list is redrawn before the arriving row is looked for.
panelWith([new CardEl('Loblaws', new Rows(200)), (function () { var c = new CardEl('Costco', null); c.classes.push('gro-rolled'); return c; })()]);
rowsEl.listeners.transitionend();
var settled = { renders: RENDERS, doneBeat: groceryState.doneBeat, arrived: panels['grocery'].querySelectorAll()[1].classes.slice() };
rowsEl.listeners.transitionend();
console.log(JSON.stringify({ folding: folding, settled: settled, twice: RENDERS }));
""")
    assert out["folding"] == {"classes": ["is-rolling", "is-rolled"], "height": "312px", "renders": 0, "doneBeat": "Costco", "hasEnd": True}, (
        "the rows are pinned at their height, then folded; nothing is redrawn until the fold ends"
    )
    assert out["settled"]["renders"] == 1 and out["settled"]["doneBeat"] is None
    assert out["settled"]["arrived"] == ["gro-rolled"], "is-arriving was added and taken away again in one reflow — the row eases to rest"
    assert out["twice"] == 1, "a second transitionend (or the fallback timer) settles nothing twice"


@_needs_node
def test_under_reduced_motion_the_roll_up_is_the_redraw_alone_with_no_beat_in_front_of_it():
    out = run(_HARNESS + _PANEL + """
mockup();
var costco = groceryState.data.stores.Costco;
costco.sections.forEach(function (s) { s.items.forEach(function (it) { it.status = 'purchased'; costco.purchased.push(it); }); });
costco.sections = [];
groceryState.doneBeat = 'Costco';
REDUCE = true;
var rowsEl = new Rows(312);
var cardEl = new CardEl('Costco', rowsEl);
panelWith([cardEl]);
groRollUp('Costco');
var instant = { classes: rowsEl.classes, height: rowsEl.style.height || null, renders: RENDERS, doneBeat: groceryState.doneBeat, cardClasses: cardEl.classes };
// And the beat itself is skipped: the last tick rolls the card up at
// once, on the same turn, rather than 600ms later.
groceryState.data = null; mockup();
delete panels['grocery'];   // no panel at all, as in every other tick test here
finishCostco();
var html = groListHtml(groceryState.data);
console.log(JSON.stringify({ rolled: instant, atOnce: { doneBeat: groceryState.doneBeat, cards: cards(html), rolledUp: rolled(html), toast: lastToastFull() } }));
""")
    assert out["rolled"] == {"classes": [], "height": None, "renders": 1, "doneBeat": None, "cardClasses": []}, "no transition class either way: instant"
    assert out["atOnce"] == {"doneBeat": None, "cards": ["Loblaws", "Anywhere"], "rolledUp": ["Done at Costco"],
                             "toast": {"msg": "That’s Costco done — 6 things.", "action": "Undo", "icon": True}}, "no beat: rolled up on the tick"


@_needs_node
def test_a_roll_up_whose_beat_was_called_off_or_whose_card_is_not_done_does_nothing():
    out = run(_HARNESS + _PANEL + """
mockup();
var rowsEl = new Rows(312);
panelWith([new CardEl('Costco', rowsEl)]);
groceryState.doneBeat = null;
groRollUp('Costco');
var calledOff = { classes: rowsEl.classes.slice(), renders: RENDERS };
groceryState.doneBeat = 'Costco';   // but Costco has four rows still to buy
groRollUp('Costco');
console.log(JSON.stringify({ calledOff: calledOff, notDone: { classes: rowsEl.classes, renders: RENDERS, doneBeat: groceryState.doneBeat } }));
""")
    assert out["calledOff"] == {"classes": [], "renders": 0}
    assert out["notDone"] == {"classes": [], "renders": 0, "doneBeat": None}


@_needs_node
def test_a_card_with_the_freezing_question_open_moves_down_opened_rather_than_hiding_it():
    out = run(_HARNESS + _PANEL + """
mockup();
var costco = groceryState.data.stores.Costco;
costco.sections.forEach(function (s) { s.items.forEach(function (it) { it.status = 'purchased'; costco.purchased.push(it); }); });
costco.sections = [];
groceryState.doneBeat = 'Costco';
groceryState.freezing = { id: '4', moveLabel: 'on Saturday', cookWeekday: 'Sunday', answer: null };
var rowsEl = new Rows(312);
panelWith([new CardEl('Costco', rowsEl)]);
groRollUp('Costco');
var html = groListHtml(groceryState.data);
console.log(JSON.stringify({ classes: rowsEl.classes, renders: RENDERS, doneOpen: groceryState.doneOpen, rolled: rolled(html),
  open: /gro-rolled is-done is-open" data-store="Costco"/.test(html), question: rolledCard(html, 'Costco').indexOf('Freezing it?') !== -1 }));
""")
    assert out["classes"] == [] and out["renders"] == 1
    assert out["doneOpen"] == {"Costco": True} and out["rolled"] == ["Done at Costco"]
    assert out["open"] is True and out["question"] is True


def test_the_fold_uses_the_base_token_on_the_leaving_curve_and_is_off_under_reduced_motion():
    motion = SHELL_CSS[SHELL_CSS.index("   Motion (Emily, 2026-09-11)."):]
    assert "Five animations in the whole app" in motion
    assert "5. A done store card rolls up" in motion
    rule = re.search(r"\.gro-card-rows\.is-rolling \{(.*?)\}", motion, re.S)
    assert rule, "the fold rule lives in the Motion section with the other four"
    body = rule.group(1)
    assert "height var(--motion-base) var(--motion-ease-in)" in body
    assert re.search(r"\d+ms", body) is None, "durations come from the tokens, which are 0ms under reduced motion"
    gone = re.search(r"\.gro-card-rows\.is-rolling\.is-rolled \{(.*?)\}", motion, re.S).group(1)
    assert "height: 0" in gone and "opacity: 0" in gone
    arrive = re.search(r"\.gro-store\.gro-rolled\.is-arriving \{(.*?)\}", motion, re.S).group(1)
    assert "opacity: 0" in arrive and "transform: translateY(-6px)" in arrive
    # Reduced motion is honoured in code too, not only by the 0ms tokens.
    roll = SHELL_JS[SHELL_JS.index("  function groRollUp(key) {"):]
    roll = roll[:roll.index("\n  }\n") + 4]
    assert "var reduce = groReducedMotion();" in roll
    assert "if (reduce || !rows || !rows.offsetHeight) { settle(); return; }" in roll
    assert "window.matchMedia('(prefers-reduced-motion: reduce)').matches" in SHELL_JS[SHELL_JS.index("  function groReducedMotion() {"):][:200]


def test_the_design_system_records_the_fifth_animation():
    motion = DESIGN_SYSTEM[DESIGN_SYSTEM.index("- **Motion**"):]
    motion = motion[:motion.index("\n\n")]
    assert "five animations in the whole app" in motion
    assert "(5) *(added 2026-09-22, Emily's \"a store done\" mockup)*" in motion
    assert "`groRollUp`" in motion


def test_the_rolled_up_row_wears_the_celadon_tokens_and_the_toast_carries_its_tick():
    rolled_css = re.search(r"\.gro-store\.gro-rolled \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "background: var(--celadon-tint)" in rolled_css and "border-color: var(--celadon-edge)" in rolled_css
    tick = re.search(r"\.gro-rolled-tick \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "width: 32px" in tick and "height: 32px" in tick and "background: var(--celadon)" in tick
    assert "border-radius: var(--radius-pill)" in tick
    title = re.search(r"\.gro-rolled-title \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "color: var(--celadon-label)" in title
    head = re.search(r"\.gro-rolled-head \{(.*?)\}", SHELL_CSS, re.S).group(1)
    assert "min-height: 56px" in head, "the whole row is the tap (Rule 6)"
    assert ".toast-icon { display: inline-flex;" in SHELL_CSS and "color: var(--celadon)" in SHELL_CSS.split(".toast-icon {")[1].split("}")[0]
    assert "showToast(groStoreDoneToast(key, groCardLines(data, key).length), wayBack, null, { icon: GRO_ICONS.tick });" in SHELL_JS
    assert "return 'That’s ' + key + ' done — ' + groPlural(count, 'thing', 'things') + '.';" in SHELL_JS
