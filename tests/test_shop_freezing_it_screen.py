"""
Shop: the "Freezing it?" follow-up on the screen (Loop Board
3e21f4c0-5231-8153-bb6a-c064eaf421f4, 2026-09-21; mockup F-C-when-ticked).

The markup and the taps run under node against shell.js's own Grocery
region (tests/shop_harness): the block appears under a meat row the
moment it is ticked, inside the store card, with the sentence and the two
mini buttons; never on a non-meat tick, a loose line, a put-back or a
re-tick; only one open at a time; Yes folds it to "In the freezer — out
Saturday night." and posts the route with "Changes saved · Put back";
Straight to the fridge folds it to "In the fridge." and posts nothing;
leaving the screen folds it away. The offline half runs the real
static/grocery-offline.js in the seat it has in the browser. The server
half is tests/test_shop_freezing_it.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import nodeharness
from shop_harness import CLICK, FIXTURE, STUB, grocery_block, needs_node

REPO = Path(__file__).resolve().parent.parent
OFFLINE = REPO / "static" / "grocery-offline.js"

# The mockup's Costco card: chicken thighs (the offer: Saturday night for
# Monday), ground beef with an offer of its own, salmon with none (its meal
# is already booked, say), orzo (not meat), and a loose pork chop nobody's
# meal recorded (no offer).
_MOCKUP = """
function offer(moveLabel, cookWeekday) {
  return { entry_id: 7, meal: 'Chicken Skewers', cook_date: '2026-09-28', cook_weekday: cookWeekday,
           move_date: '2026-09-26', move_label: moveLabel, lead_hours: 48, lead_tier: 'standard' };
}
function mockup() {
  groceryState.data = { stores: {
    Costco: { sections: [
      { section: 'meat/seafood', items: [
        { id: 1, item: 'Chicken thighs', quantity: '2 lb', store: 'Costco', store_decided: 1, category: 'meat/seafood', status: 'needed', freezing: offer('Saturday night', 'Monday') },
        { id: 2, item: 'Ground beef', quantity: '1 lb', store: 'Costco', store_decided: 1, category: 'meat/seafood', status: 'needed', freezing: offer('Wednesday night', 'Friday') },
        { id: 4, item: 'Salmon', quantity: '4 fillets', store: 'Costco', store_decided: 1, category: 'meat/seafood', status: 'needed' }] },
      { section: 'pantry', items: [
        { id: 3, item: 'Orzo', quantity: '1 box', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' }] }],
      purchased: [], inCart: [] },
    Unassigned: { sections: [{ section: 'meat/seafood', items: [
      { id: 20, item: 'Pork chops', quantity: '4', store: '', store_decided: 0, category: 'meat/seafood', status: 'needed' }] }], purchased: [], inCart: [] }
  } };
  groceryState.usualStores = ['Costco', 'Loblaws'];
  groceryState.storesPromptDismissed = true;
  groceryState.step = 'list';
  groceryState.freezing = null;
  groceryState.freezingAsked = {};
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
function blocks(html) {
  return (html.match(/<div class="gro-freeze[^"]*" data-freeze-for="(\\d+)">/g) || [])
    .map(function (m) { return /data-freeze-for="(\\d+)"/.exec(m)[1]; });
}
function blockText(html) {
  var m = /<p class="gro-freeze-text">([^<]*)<\\/p>/.exec(html);
  return m ? m[1] : null;
}
function tick(id) { return clickIfRendered({ gro: 'line-tick', id: String(id), bought: '0' }); }
function putBack(id) { return clickIfRendered({ gro: 'line-tick', id: String(id), bought: '1' }); }
function list() { return groListHtml(groceryState.data); }
"""


# Every screen test runs with the real static/grocery-offline.js in its
# seat, as the browser does: it is what applies a tick to the list on the
# screen before the server answers, so the struck row and the block under
# it are drawn by the same render. `DEAD` is the phone's signal.
_HARNESS = STUB + """
window.PomonaGroceryOffline = require(""" + json.dumps(str(OFFLINE)) + """);
var DEAD = false;
fetch = function (url, opts) {
  if (DEAD) return Promise.reject(new TypeError('Failed to fetch'));
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({}); } });
};
"""


def _screen(body: str):
    res = nodeharness.run_node(_HARNESS + grocery_block() + CLICK + FIXTURE + _MOCKUP + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the block -----------------------------------------------------------------


@needs_node
def test_ticking_a_meat_line_shows_the_question_under_that_row_inside_the_store_card():
    out = _screen("""
mockup();
var before = list();
tick(1);
var html = list();
var costco = card(html, 'Costco');
var rowAt = costco.indexOf('data-gro="line-tick" data-id="1"');
var blockAt = costco.indexOf('data-freeze-for="1"');
var nextRowAt = costco.indexOf('data-gro="line-tick" data-id="2"');
console.log(JSON.stringify({
  before: blocks(before), after: blocks(html),
  inCard: rowAt !== -1 && blockAt > rowAt && blockAt < nextRowAt,
  text: blockText(costco),
  yes: /<button type="button" class="wk-mini gro-freeze-yes" data-gro="freeze-yes" data-id="1"><svg[^>]*stroke-width="2.2"[^>]*>[\\s\\S]*?<\\/svg>Yes, freezing it<\\/button>/.test(costco),
  ghost: costco.indexOf('<button type="button" class="wk-mini is-ghost" data-gro="freeze-fridge" data-id="1">Straight to the fridge</button>') !== -1,
  struck: /class="gro-row gro-line done[^"]*" data-gro="line-tick" data-id="1"/.test(costco),
  sheet: html.indexOf('sheet') !== -1 && html.indexOf('gro-freeze') === -1
}));
""")
    assert out["before"] == [], "nothing until a tick"
    assert out["after"] == ["1"]
    assert out["inCard"] is True, "under the row, before the next one, inside the card"
    assert out["text"] == "Freezing it? I’ll remind you Saturday night to move it to the fridge for Monday."
    assert out["yes"] is True, "the snowflake stroke icon and the words, a 36px mini"
    assert out["ghost"] is True
    assert out["struck"] is True, "the row itself is ticked as usual"
    assert out["sheet"] is False, "no sheet"


@needs_node
def test_no_question_on_a_non_meat_tick_or_a_loose_meat_line():
    out = _screen("""
mockup();
tick(3);
var orzo = blocks(list());
mockup();
tick(20);
var pork = blocks(list());
mockup();
tick(4);
var salmon = blocks(list());
console.log(JSON.stringify({ orzo: orzo, pork: pork, salmon: salmon }));
""")
    assert out["orzo"] == [], "orzo is not meat"
    assert out["pork"] == [], "loose 'Add something' meat with no meal"
    assert out["salmon"] == [], "meat the server made no offer for (its move is booked)"


@needs_node
def test_never_on_a_put_back_or_a_re_tick_and_only_one_open_at_a_time():
    out = _screen("""
mockup();
tick(1);
var first = blocks(list());
tick(2);
var second = blocks(list());
putBack(2);
var afterPutBack = blocks(list());
tick(2);
var reTick = blocks(list());
putBack(1);
tick(1);
console.log(JSON.stringify({ first: first, second: second, afterPutBack: afterPutBack, reTick: reTick,
  reTickOne: blocks(list()) }));
""")
    assert out["first"] == ["1"]
    assert out["second"] == ["2"], "ticking another row collapses the first — one open at a time"
    assert out["afterPutBack"] == [], "a put-back asks nothing"
    assert out["reTick"] == [], "a re-tick never asks twice"
    assert out["reTickOne"] == [], "nor for the row asked earlier this page view"


@needs_node
def test_the_ticks_own_put_back_toast_folds_the_question_too():
    out = _screen("""
mockup();
tick(1);
var open = blocks(list());
tapUndo(); // the tick's "Changes saved · Put back"
settle(function () {
  console.log(JSON.stringify({ open: open, after: blocks(list()), posts: POSTS.map(function (p) { return [p.url, p.body.status]; }) }));
});
""")
    assert out["open"] == ["1"]
    assert out["after"] == []
    assert out["posts"][-1] == ["/api/grocery-list/1/status", "needed"]


@needs_node
def test_leaving_the_screen_folds_the_question_away():
    out = _screen("""
mockup();
tick(1);
var open = blocks(list());
groLeaveScreen();
console.log(JSON.stringify({ open: open, after: blocks(list()), state: groceryState.freezing }));
""")
    assert out["open"] == ["1"]
    assert out["after"] == [] and out["state"] is None


# --- 2. the answers ---------------------------------------------------------------


@needs_node
def test_yes_books_the_move_folds_the_block_and_offers_put_back():
    out = _screen("""
mockup();
tick(1);
var tickPosts = POSTS.length;
clickIfRendered({ gro: 'freeze-yes', id: '1' });
settle(function () {
  var html = list();
  var afterYes = {
    blocks: blocks(html), text: blockText(card(html, 'Costco')),
    buttons: (html.match(/data-gro="freeze-/g) || []).length,
    posts: POSTS.slice(tickPosts).map(function (p) { return [p.url, p.body]; }),
    toast: lastToast()
  };
  tapUndo();
  settle(function () {
    var again = list();
    console.log(JSON.stringify({ afterYes: afterYes,
      afterPutBack: { blocks: blocks(again), text: blockText(card(again, 'Costco')), buttons: (again.match(/data-gro="freeze-/g) || []).length,
        posts: POSTS.slice(tickPosts).map(function (p) { return [p.url, p.body]; }) } }));
  });
});
""")
    assert out["afterYes"]["blocks"] == ["1"]
    assert out["afterYes"]["text"] == "In the freezer — out Saturday night."
    assert out["afterYes"]["buttons"] == 0, "folded to the one line"
    assert out["afterYes"]["posts"] == [["/api/grocery-list/1/freezing", {"answer": "freezer"}]]
    assert out["afterYes"]["toast"] == {"msg": "Changes saved", "action": "Put back"}
    assert out["afterPutBack"]["posts"][-1] == ["/api/grocery-list/1/freezing", {"answer": "fridge"}], "Put back removes the move"
    assert out["afterPutBack"]["buttons"] == 2 and out["afterPutBack"]["text"].startswith("Freezing it?"), "and the question is back"


@needs_node
def test_straight_to_the_fridge_folds_the_block_and_writes_nothing():
    out = _screen("""
mockup();
tick(1);
var tickPosts = POSTS.length;
var toasts = TOASTS.length;
clickIfRendered({ gro: 'freeze-fridge', id: '1' });
settle(function () {
  var html = list();
  console.log(JSON.stringify({ blocks: blocks(html), text: blockText(card(html, 'Costco')),
    buttons: (html.match(/data-gro="freeze-/g) || []).length, posts: POSTS.length - tickPosts, toasts: TOASTS.length - toasts,
    queued: groOffline ? groOffline.pending().length : 0 }));
});
""")
    assert out["blocks"] == ["1"] and out["text"] == "In the fridge."
    assert out["buttons"] == 0
    assert out["posts"] == 0 and out["toasts"] == 0, "nothing written, nothing said"


@needs_node
def test_a_yes_the_server_refuses_puts_the_question_back_with_the_toast():
    """A 4xx (the line stopped being askable, say) is not a saved answer:
    the block cannot keep reading "In the freezer" over a move that was
    never booked. It goes back to the open question; the no-signal path
    (queued, replayed) is the other test and keeps the answer."""
    out = _screen("""
mockup();
tick(1);
var realFetch = fetch;
fetch = function (url, opts) {
  if (/\\/freezing$/.test(url)) {
    return Promise.resolve({ ok: false, status: 400, json: function () { return Promise.resolve({ detail: 'no' }); } });
  }
  return realFetch(url, opts);
};
var toasts = TOASTS.length;
clickIfRendered({ gro: 'freeze-yes', id: '1' });
var rightAway = blockText(card(list(), 'Costco'));
settle(function () {
  var html = list();
  console.log(JSON.stringify({ rightAway: rightAway, blocks: blocks(html), text: blockText(card(html, 'Costco')),
    buttons: (html.match(/data-gro="freeze-/g) || []).length,
    toasts: TOASTS.slice(toasts).map(function (t) { return t.msg; }),
    queued: groOffline.pending().length, offline: groceryState.offline }));
});
""")
    assert out["rightAway"] == "In the freezer — out Saturday night.", "optimistic, as a tick is"
    assert out["blocks"] == ["1"] and out["text"].startswith("Freezing it?"), "and back to the question once refused"
    assert out["buttons"] == 2
    assert out["toasts"][-1] == "Couldn't save that — try again."
    assert out["queued"] == 0 and out["offline"] is False, "a refusal is not a dead zone: nothing queued"


# --- 3. no signal -----------------------------------------------------------------


_offline = _screen


@needs_node
def test_a_yes_with_no_signal_is_queued_behind_the_tick_and_replayed_in_order():
    out = _offline("""
mockup();
groOffline.setHousehold(1);
DEAD = true;
navigator.onLine = false;
tick(1);
clickIfRendered({ gro: 'freeze-yes', id: '1' });
settle(function () {
  var html = list();
  var offline = {
    text: blockText(card(html, 'Costco')),
    queued: groOffline.pending().map(function (op) { return [op.id, op.kind || 'status', op.status || op.answer]; }),
    posts: POSTS.length, toast: lastToast()
  };
  DEAD = false;
  navigator.onLine = true;
  groReplayQueue().then(function () {
    settle(function () {
      console.log(JSON.stringify({ offline: offline, sent: POSTS.map(function (p) { return [p.url, p.body]; }), left: groOffline.pending().length }));
      process.exit(0);
    });
  });
});
""")
    assert out["offline"]["text"] == "In the freezer — out Saturday night.", "answered on the screen at once"
    assert out["offline"]["queued"] == [["1", "status", "purchased"], ["1", "freezing", "freezer"]]
    assert out["offline"]["posts"] == 0
    assert out["offline"]["toast"] == {"msg": "Changes saved", "action": "Put back"}
    assert out["sent"] == [
        ["/api/grocery-list/1/status", {"status": "purchased"}],
        ["/api/grocery-list/1/freezing", {"answer": "freezer"}],
    ], "the tick first, then the answer"
    assert out["left"] == 0


@needs_node
def test_a_put_back_with_no_signal_replaces_the_queued_yes_latest_wins():
    out = _offline("""
mockup();
groOffline.setHousehold(1);
DEAD = true;
navigator.onLine = false;
tick(1);
clickIfRendered({ gro: 'freeze-yes', id: '1' });
tapUndo();
settle(function () {
  console.log(JSON.stringify({
    queued: groOffline.pending().map(function (op) { return [op.id, op.kind || 'status', op.status || op.answer]; }),
    text: blockText(card(list(), 'Costco'))
  }));
  process.exit(0); // the replay timer would keep node alive
});
""")
    assert out["queued"] == [["1", "status", "purchased"], ["1", "freezing", "fridge"]], "one op per line for the answer; the tick is its own op"
    assert out["text"].startswith("Freezing it?")


def test_the_offline_module_validates_and_replays_the_new_op_on_its_own():
    """The module alone, as tests/test_grocery_offline.py runs it: a
    freezing op is valid, is one per line, never touches the list's shape,
    and is spent when the server answers."""
    res = nodeharness.run_node("""
const mod = require(""" + json.dumps(str(OFFLINE)) + """);
const store = new Map();
const storage = { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k), length: 0, key: () => null };
const q = mod.create({ storage, householdId: 1, now: (() => { let t = 0; return () => ++t; })() });
q.queueStatus(1, 'purchased');
q.queueFreezing(1, 'freezer');
q.queueFreezing(1, 'fridge');
q.queueFreezing(2, 'freezer');
const bad = q.queueFreezing(3, 'maybe');
const data = { stores: { Costco: { sections: [{ section: 'meat/seafood', items: [{ id: 1, category: 'meat/seafood', status: 'needed' }, { id: 2, category: 'meat/seafood', status: 'needed' }] }], purchased: [], inCart: [] } } };
const applied = q.applyPending(data);
// A stale shape from an older build sits beside a good op: only the good one survives the read.
store.set('pomona.grocery.queue.h1', JSON.stringify(JSON.parse(store.get('pomona.grocery.queue.h1')).concat([{ id: 9, kind: 'freezing' }, null])));
const pendingNow = q.pending().map(op => [op.id, op.kind || 'status', op.status || op.answer]);
const sent = [];
q.replay((url, body) => { sent.push([url, body]); return Promise.resolve({ ok: url.indexOf('/2/') === -1, status: 200 }); }).then(result => {
  console.log(JSON.stringify({ bad, pendingNow, applied: applied.stores.Costco.purchased.map(i => i.id), left: q.pending().length, sent, result }));
});
""")
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip())
    assert out["bad"] is None
    assert out["pendingNow"] == [["1", "status", "purchased"], ["1", "freezing", "fridge"], ["2", "freezing", "freezer"]]
    assert out["applied"] == [1], "the answer changes nothing on the list; the tick still does"
    assert out["sent"] == [
        ["/api/grocery-list/1/status", {"status": "purchased"}],
        ["/api/grocery-list/1/freezing", {"answer": "fridge"}],
        ["/api/grocery-list/2/freezing", {"answer": "freezer"}],
    ]
    assert out["left"] == 0 and out["result"] == {"sent": 2, "dropped": 1, "kept": 0}
