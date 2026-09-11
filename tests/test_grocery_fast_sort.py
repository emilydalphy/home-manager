"""
Sorting forty things shouldn't take forty screens (Emily, 2026-09-09).

    "if there's 40 ingredients ... it can take too long to go through the
    screens all like this."

The one-at-a-time queue stays — for three stray items it is the right shape
— but it stops being the only shape. Above it: "Put all 40 at Loblaws" (one
tap, undoable) and "Sort them all on one screen" (every unsorted thing, one
row each, everything starting at the most-used shop). The queue is the third
option, and it says out loud how many answers it wants ("1 of 40").

Three more things ride along, each because the sorting step touched them:

  * an "Any" answer now PERSISTS (grocery_items.store_decided). It used to
    live in a page-view map, so a reload put every skipped item straight back
    into the queue — the known limit recorded in CLAUDE.md.
  * a household with one shop, or none, never sees a sorting step at all,
    and a one-shop household can now start a trip without tagging anything.
  * finishing a stop asks where next instead of assuming the snapshot's
    order, and offers "I'm done shopping for today".

Most of this runs shell.js's own functions under node against a small stub,
the way tests/test_stores_multiselect.py does — every bug this fixes is a
piece of behaviour (a queue that re-asks, a stop that starts itself), which
is exactly what a source-marker test cannot see. The persistence half is
driven over real HTTP against the real routes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import tools


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# --- the node harness -----------------------------------------------------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _grocery_block() -> str:
    """The whole Grocery region INCLUDING onGroceryClick, up to the
    hands-free voice code (which wants a SpeechRecognition engine).

    The handlers are in here on purpose. They were source-marker-only in the
    first pass, and both of the reviewer's CONCERNs — a partial bulk write
    and an undo that spends its own payload — lived in exactly that untested
    half. A handler needs an event and an element rather than a DOM, and
    _CLICK below is the eleven lines that supply them."""
    start = SHELL_JS.index("  var GRO_STORE_PALETTE = [")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
// Fails the Nth POST (1-based) when FAIL_ON is set — how a dropped
// connection is injected without a server.
let FAIL_ON = 0;
function fetch(url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  const ok = POSTS.length !== FAIL_ON;
  return Promise.resolve({ ok: ok, json: function () { return Promise.resolve({}); } });
}
const panels = {};
const TOASTS = [];
function showToast(msg, action, hold) { TOASTS.push({ msg: msg, action: action, hold: hold }); }
function lastToast() {
  const t = TOASTS[TOASTS.length - 1];
  return t ? { msg: t.msg, action: t.action ? t.action.label : null, hold: t.hold } : null;
}
function tapUndo() {
  for (let i = TOASTS.length - 1; i >= 0; i--) {
    if (TOASTS[i].action && TOASTS[i].action.onClick) { TOASTS[i].action.onClick(); return true; }
  }
  return false;
}
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
function activateTab() {}
var coachState = { householdId: 1 };
"""

# The handler half. onGroceryClick wants an event whose target can find a
# [data-gro] element and an element it can disable — not a document. This is
# both, so the seven new verbs run for real rather than being read for.
_CLICK = """
function fakeEl(dataset, row) {
  return {
    dataset: dataset, disabled: false,
    closest: function () { return row || null; },
    classList: { toggle: function () {} },
    setAttribute: function () {},
    querySelectorAll: function () { return []; }
  };
}
function click(dataset, row) {
  const el = fakeEl(dataset, row);
  onGroceryClick({ target: { closest: function () { return el; } } });
  return el;
}
// Handlers write, then re-read, then render — all through promises. Give
// them a few turns of the loop before reading the state back.
function settle(fn) { setTimeout(fn, 30); }
"""

# A household with two shops and a list where nothing has been sorted yet —
# the state Emily was describing, shrunk to a size a test can read. `n` rows
# of "Thing 1..n", plus whatever extra rows a test wants to add.
_FIXTURE = """
function unsortedRow(i) {
  return { id: i, item: 'Thing ' + i, quantity: '1', store: '', store_decided: 0 };
}
function listOf(n, extra) {
  const rows = [];
  for (let i = 1; i <= n; i++) rows.push(unsortedRow(i));
  const data = { stores: { Unassigned: { sections: [{ section: 'other', items: rows }], purchased: [], inCart: [] } } };
  (extra || []).forEach(function (s) {
    data.stores[s.store] = {
      sections: s.items && s.items.length ? [{ section: 'other', items: s.items }] : [],
      purchased: s.purchased || [], inCart: s.inCart || []
    };
  });
  return data;
}
function setUp(n, extra, shops) {
  groceryState.data = listOf(n, extra);
  groceryState.usualStores = shops === undefined ? ['Loblaws', 'Costco'] : shops;
  groceryState.storesPromptDismissed = true;
  groceryState.sortAllPicks = {};
  return groceryState.data;
}
function pickedChip(html, id) {
  const row = new RegExp('data-row-for="' + id + '"[\\\\s\\\\S]*?</div>\\\\s*</div>').exec(html);
  const m = /data-store="([^"]*)" aria-pressed="true"/.exec(row ? row[0] : '');
  return m ? m[1] : null;
}
"""


def _node(body: str):
    script = _STUB + _grocery_block() + _CLICK + _FIXTURE + body
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the fast paths are offered, and the queue survives -----------------


@_needs_node
def test_a_handful_still_goes_straight_to_the_one_at_a_time_queue():
    """Emily likes the queue for three stray items; a menu of ways to answer
    three questions costs more than answering them."""
    out = _node("""
setUp(3);
console.log(JSON.stringify({ toSort: groUnsorted(groceryState.data).length, min: GRO_FAST_SORT_MIN }));
""")
    assert out["toSort"] == 3
    assert out["toSort"] < out["min"], "three things is under the fast-path threshold"


@_needs_node
def test_forty_things_are_over_the_threshold_that_offers_the_fast_paths():
    out = _node("""
setUp(40);
console.log(JSON.stringify(groUnsorted(groceryState.data).length >= GRO_FAST_SORT_MIN));
""")
    assert out is True


@_needs_node
def test_the_fast_path_screen_offers_both_and_still_reaches_the_queue():
    out = _node("""
setUp(40);
const html = groSortHowHtml(groceryState.data);
const verbs = [], re = /data-gro="([^"]+)"/g;
let m; while ((m = re.exec(html)) !== null) verbs.push(m[1]);
console.log(JSON.stringify({
  verbs: verbs,
  bulk: groDockHtml(groceryState.data, 'sorthow'),
  queueLine: /gro-howrow-sub">([^<]*)</g.exec(html.split('goto-sort-one')[1] || '')
}));
""")
    # "Sort them later" joined the screen on 2026-09-11, when sorting started
    # coming BEFORE the list (Build 7): the quiet way to the list instead.
    assert out["verbs"] == ["goto-sortall", "goto-sort-one", "sort-later"], "both other paths on screen, and the way out"
    assert "Put all 40 at" in out["bulk"], "the bulk answer is the screen's dock action"


@_needs_node
def test_the_queue_says_how_many_answers_it_wants():
    """"Or one at a time — 1 of 40". The count is the whole point of
    offering it third."""
    out = _node("""
setUp(40);
const html = groSortHowHtml(groceryState.data);
console.log(JSON.stringify(/Or one at a time<\\/span>\\s*<span class="gro-howrow-sub">([^<]*)</.exec(html)[1]));
""")
    assert out == "1 of 40"


@_needs_node
def test_the_fast_path_screen_carries_exactly_one_apricot():
    """Rule 5. The two rows in the body are plain; the dock is the primary."""
    out = _node("""
setUp(40);
const body = groSortHowHtml(groceryState.data);
const dock = groDockHtml(groceryState.data, 'sorthow');
console.log(JSON.stringify({
  bodyPrimaries: (body.match(/gro-primary/g) || []).length,
  dockPrimaries: (dock.match(/gro-primary/g) || []).length
}));
""")
    assert out == {"bodyPrimaries": 0, "dockPrimaries": 1}


# --- 2. "put all at X" -----------------------------------------------------


@_needs_node
def test_the_offered_shop_is_the_one_this_household_actually_buys_from():
    """Read off the list the tab already has open — rows tagged to a shop,
    whatever their status. No new counter."""
    out = _node("""
setUp(5, [
  { store: 'Costco', items: [{ id: 91, item: 'Rice', store: 'Costco' }] },
  { store: 'Loblaws', items: [
      { id: 92, item: 'Milk', store: 'Loblaws' },
      { id: 93, item: 'Eggs', store: 'Loblaws' }
    ], purchased: [{ id: 94, item: 'Bread', store: 'Loblaws' }] }
]);
console.log(JSON.stringify(groMostUsedStore(groceryState.data)));
""")
    assert out == "Loblaws"


@_needs_node
def test_with_nothing_tagged_yet_it_falls_back_to_the_first_shop_named():
    out = _node("""
setUp(5, [], ['Farm Boy', 'Metro']);
console.log(JSON.stringify(groMostUsedStore(groceryState.data)));
""")
    assert out == "Farm Boy"


@_needs_node
def test_putting_them_all_at_one_shop_is_one_request_not_forty():
    out = _node("""
setUp(40);
const items = groUnsorted(groceryState.data);
groBulkAssign(
  items.map(function (it) { return { item_id: it.id, store: 'Loblaws', decided: true }; }),
  groPreviousStores(items),
  '40 things at Loblaws.'
);
setTimeout(function () {
  console.log(JSON.stringify({
    calls: POSTS.map(function (p) { return p.url; }),
    sent: POSTS[0].body.assignments.length,
    remember: POSTS[0].body.remember,
    toast: lastToast()
  }));
}, 20);
""")
    assert out["calls"][0] == "/api/grocery-list/store-bulk"
    assert out["calls"].count("/api/grocery-list/store-bulk") == 1, "one request, not forty"
    assert out["sent"] == 40
    assert out["remember"] is False, "one tap must not become forty remembered opinions"
    assert out["toast"]["action"] == "Undo"
    assert out["toast"]["hold"] == 8000


@_needs_node
def test_undo_restores_each_row_exactly_not_everything_to_unsorted():
    """The row already answered "Any", and the row already tagged to Costco,
    both have to come back the way they were — not as open questions."""
    out = _node("""
setUp(0, [
  { store: 'Costco', items: [{ id: 7, item: 'Rice', store: 'Costco', store_decided: 1 }] }
]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 5, item: 'Salt', store: '', store_decided: 1 },
  { id: 6, item: 'Oats', store: '', store_decided: 0 }
];
const previous = groPreviousStores([
  groceryState.data.stores.Unassigned.sections[0].items[0],
  groceryState.data.stores.Unassigned.sections[0].items[1],
  groceryState.data.stores.Costco.sections[0].items[0]
]);
console.log(JSON.stringify(previous));
""")
    assert out == [
        {"item_id": 5, "store": "", "decided": True},
        {"item_id": 6, "store": "", "decided": False},
        {"item_id": 7, "store": "Costco", "decided": True},
    ]


@_needs_node
def test_a_bulk_assign_only_touches_what_was_still_unsorted():
    out = _node("""
setUp(0, [{ store: 'Costco', items: [{ id: 7, item: 'Rice', store: 'Costco', store_decided: 1 }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 5, item: 'Salt', store: '', store_decided: 1 },
  { id: 6, item: 'Oats', store: '', store_decided: 0 }
];
console.log(JSON.stringify(groUnsorted(groceryState.data).map(function (it) { return it.id; })));
""")
    assert out == [6], "an answered row keeps its answer"


# --- 3. sort them all on one screen ---------------------------------------


@_needs_node
def test_the_one_screen_shows_every_unsorted_thing_with_a_shop_on_it():
    out = _node("""
setUp(40);
const html = groSortAllHtml(groceryState.data);
console.log(JSON.stringify({
  rows: (html.match(/gro-sortall-row/g) || []).length,
  firstPick: pickedChip(html, '1'),
  lastPick: pickedChip(html, '40'),
  hasAny: /data-store="" aria-pressed="false"/.test(html)
}));
""")
    assert out["rows"] == 40, "every unsorted thing, one row each"
    assert out["firstPick"] == "Loblaws", "everything starts at the default"
    assert out["lastPick"] == "Loblaws"
    assert out["hasAny"] is True, "'Any' is still an answer on every row"


@_needs_node
def test_changing_one_row_changes_only_that_row_and_writes_nothing():
    out = _node("""
setUp(40);
groceryState.sortAllPicks['3'] = 'Costco';
const html = groSortAllHtml(groceryState.data);
console.log(JSON.stringify({
  changed: pickedChip(html, '3'),
  neighbourBefore: pickedChip(html, '2'),
  neighbourAfter: pickedChip(html, '4'),
  posts: POSTS.length
}));
""")
    assert out["changed"] == "Costco"
    assert out["neighbourBefore"] == "Loblaws"
    assert out["neighbourAfter"] == "Loblaws"
    assert out["posts"] == 0, "staged, so a tap costs no request and cannot reload the list"


@_needs_node
def test_saving_the_screen_sends_the_exceptions_and_the_default_together():
    out = _node("""
setUp(4);
groceryState.sortAllPicks['2'] = 'Costco';
groceryState.sortAllPicks['4'] = '';
const items = groUnsorted(groceryState.data);
const fallback = groMostUsedStore(groceryState.data);
groBulkAssign(
  items.map(function (it) { return { item_id: it.id, store: groSortAllPick(it, fallback), decided: true }; }),
  groPreviousStores(items),
  '4 things sorted.'
);
setTimeout(function () { console.log(JSON.stringify(POSTS[0].body.assignments)); }, 20);
""")
    assert out == [
        {"item_id": 1, "store": "Loblaws", "decided": True},
        {"item_id": 2, "store": "Costco", "decided": True},
        {"item_id": 3, "store": "Loblaws", "decided": True},
        {"item_id": 4, "store": "", "decided": True},
    ]


# --- 4. a household with one shop, or none --------------------------------


@_needs_node
def test_one_shop_means_no_sorting_step_at_all():
    """Sorting is a question with more than one answer, or it is not a
    question."""
    out = _node("""
setUp(40, [], ['Loblaws']);
console.log(JSON.stringify({
  canSort: groCanSort(groceryState.data),
  toSort: groUnsorted(groceryState.data).length
}));
""")
    assert out == {"canSort": False, "toSort": 0}


@_needs_node
def test_no_shop_at_all_means_no_sorting_step_either():
    out = _node("""
setUp(40, [], []);
console.log(JSON.stringify(groUnsorted(groceryState.data).length));
""")
    assert out == 0


@_needs_node
def test_a_one_shop_household_can_start_a_trip_without_tagging_anything():
    """Its whole list sits unassigned because there was never a question to
    answer. Before this, that meant no stops, and no trip."""
    out = _node("""
setUp(6, [], ['Loblaws']);
const stops = groStoresWithNeeded(groceryState.data);
groceryState.tripStops = stops;
groceryState.tripIndex = 0;
console.log(JSON.stringify({
  stops: stops,
  onTheStop: groTripItems(groceryState.data).length,
  cardCount: groStoreCardItems(groceryState.data, 'Loblaws').length
}));
""")
    assert out["stops"] == ["Loblaws"]
    assert out["onTheStop"] == 6, "the loose list has only one place it can be bought"
    assert out["cardCount"] == 6, "and the list shows it under that shop"


@_needs_node
def test_two_shops_still_leaves_the_untagged_things_to_be_sorted():
    """The control for the two above, and the one test in this file that
    passed before the change: it pins the behaviour that must NOT move while
    the one-shop case is being carved out of it."""
    out = _node("""
setUp(6, [], ['Loblaws', 'Costco']);
console.log(JSON.stringify({
  stops: groStoresWithNeeded(groceryState.data),
  toSort: groUnsorted(groceryState.data).length
}));
""")
    assert out == {"stops": [], "toSort": 6}


# --- 5. where next --------------------------------------------------------


@_needs_node
def test_finishing_a_stop_leaves_the_others_to_choose_from():
    out = _node("""
setUp(0, [
  { store: 'Costco', items: [{ id: 1, item: 'Rice', store: 'Costco' }] },
  { store: 'Loblaws', items: [{ id: 2, item: 'Milk', store: 'Loblaws' }] },
  { store: 'Metro', items: [{ id: 3, item: 'Eggs', store: 'Metro' }] }
]);
groceryState.tripStops = ['Costco', 'Loblaws', 'Metro'];
groceryState.tripIndex = 0;
groceryState.tripDone = { Costco: true };
console.log(JSON.stringify({
  remaining: groRemainingStops(groceryState.data),
  html: groNextHtml(groceryState.data)
}));
""")
    assert out["remaining"] == ["Loblaws", "Metro"]
    assert 'data-store="Costco"' not in out["html"], "a finished stop is never offered again"
    assert "1 thing left" in out["html"]


@_needs_node
def test_the_trip_can_be_ended_from_the_where_next_screen():
    out = _node("""
setUp(0, [{ store: 'Metro', items: [{ id: 3, item: 'Eggs', store: 'Metro' }] }]);
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripDone = { Costco: true };
console.log(JSON.stringify(groDockHtml(groceryState.data, 'next')));
""")
    assert "trip-end" in out
    assert "done shopping for today" in out
    assert "gro-primary" not in out, "the stops above are the choice; ending early is not the accent"


@_needs_node
def test_a_stop_with_nothing_left_on_it_is_not_offered_as_a_choice():
    out = _node("""
setUp(0, [
  { store: 'Costco', items: [] },
  { store: 'Metro', items: [{ id: 3, item: 'Eggs', store: 'Metro' }] }
]);
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripDone = {};
console.log(JSON.stringify(groRemainingStops(groceryState.data)));
""")
    assert out == ["Metro"], "a shop with nothing left is a detour, not a choice"


@_needs_node
def test_the_shopless_things_follow_the_shopper_rather_than_the_first_stop():
    """They used to be pinned to stop one, so one left unbought there was
    stranded for the rest of the trip — and the household now picks its own
    order of stops, which made that worse."""
    out = _node("""
setUp(0, [
  { store: 'Costco', items: [{ id: 1, item: 'Rice', store: 'Costco' }] },
  { store: 'Metro', items: [{ id: 3, item: 'Eggs', store: 'Metro' }] }
]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 9, item: 'Batteries', store: '', store_decided: 1 }
];
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripIndex = 1;
console.log(JSON.stringify(groTripItems(groceryState.data).map(function (it) { return it.item; })));
""")
    assert out == ["Eggs", "Batteries"]


@_needs_node
def test_the_where_next_screen_says_the_shopless_things_are_coming_along():
    out = _node("""
setUp(0, [{ store: 'Metro', items: [{ id: 3, item: 'Eggs', store: 'Metro' }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 9, item: 'Batteries', store: '', store_decided: 1 },
  { id: 10, item: 'Foil', store: '', store_decided: 1 }
];
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripDone = { Costco: true };
const html = groNextHtml(groceryState.data);
console.log(JSON.stringify({
  html: html,
  // Inside the card, where --ink-secondary clears AA — see shell.css.
  insideCard: html.indexOf('gro-next-note') < html.lastIndexOf('</div>')
}));
""")
    assert "2 things with no shop will come with you." in out["html"]
    assert out["insideCard"] is True


@_needs_node
def test_the_stop_counter_counts_the_stops_behind_you():
    """Not this stop's place in the snapshot: the household picks its own
    order now, so the index would say "Stop 3 of 3" with two shops waiting."""
    out = _node("""
setUp(0, [
  { store: 'Costco', items: [{ id: 1, item: 'Rice', store: 'Costco' }] },
  { store: 'Loblaws', items: [{ id: 2, item: 'Milk', store: 'Loblaws' }] },
  { store: 'Metro', items: [{ id: 3, item: 'Eggs', store: 'Metro' }] }
]);
groceryState.tripStops = ['Costco', 'Loblaws', 'Metro'];
groceryState.tripDone = { Costco: true };
groceryState.tripIndex = 2;
console.log(JSON.stringify(groHeadFor(groceryState.data, 'trip').sub));
""")
    assert out.startswith("Stop 2 of 3"), out


# --- 6. an answer survives a reload (over real HTTP) ----------------------


def test_saying_no_particular_shop_is_remembered(signed_in):
    """The known limit CLAUDE.md recorded: "Any" was a page-view map, so a
    reload put the item straight back into the to-sort queue."""
    added = signed_in.post("/api/grocery-list/add", json={"item": "Batteries", "quantity": "1"}).json()
    item_id = added["item_id"]

    before = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert before["store_decided"] == 0, "nobody has answered yet"

    signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": ""})

    after = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert after["store"] == "", "still no particular shop"
    assert after["store_decided"] == 1, "but the question has been answered"


def test_the_answer_reaches_the_screen_that_has_to_read_it(signed_in):
    added = signed_in.post("/api/grocery-list/add", json={"item": "Foil", "quantity": "1"}).json()
    signed_in.post(f"/api/grocery-list/{added['item_id']}/store", json={"store": ""})

    payload = signed_in.get("/api/grocery-list/by-store?status=needed").json()
    rows = [
        it
        for s in payload["stores"]
        for sec in s["sections"]
        for it in sec["items"]
        if it["id"] == added["item_id"]
    ]
    assert rows and rows[0]["store_decided"] == 1


def test_many_rows_are_assigned_in_one_request(signed_in):
    ids = [
        signed_in.post("/api/grocery-list/add", json={"item": name, "quantity": "1"}).json()["item_id"]
        for name in ("Leeks", "Barley", "Yoghurt")
    ]
    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": i, "store": "Loblaws"} for i in ids]},
    )
    assert res.status_code == 200
    assert res.json()["updated"] == 3
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    for i in ids:
        assert rows[i]["store"] == "Loblaws"
        assert rows[i]["store_decided"] == 1


def test_the_undo_puts_every_row_back_the_way_it_was(signed_in):
    """Not "everything to unsorted": the row that was already answered "Any"
    has to come back answered, and the one that was never asked has to come
    back unasked."""
    answered = signed_in.post("/api/grocery-list/add", json={"item": "Cling film", "quantity": "1"}).json()["item_id"]
    untouched = signed_in.post("/api/grocery-list/add", json={"item": "Paprika", "quantity": "1"}).json()["item_id"]
    signed_in.post(f"/api/grocery-list/{answered}/store", json={"store": ""})

    previous = [
        {"item_id": answered, "store": "", "decided": True},
        {"item_id": untouched, "store": "", "decided": False},
    ]
    signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": i, "store": "Costco"} for i in (answered, untouched)]},
    )
    signed_in.post("/api/grocery-list/store-bulk", json={"assignments": previous})

    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert (rows[answered]["store"], rows[answered]["store_decided"]) == ("", 1)
    assert (rows[untouched]["store"], rows[untouched]["store_decided"]) == ("", 0)


def test_a_bulk_assign_does_not_remember_a_shop_for_every_item(signed_in):
    """One tap covering forty rows must not become forty remembered opinions
    about where each of those things is usually bought."""
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Tahini", "quantity": "1"}).json()["item_id"]
    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "Costco"}]},
    )
    assert res.status_code == 200 and res.json()["updated"] == 1, "the write really happened"
    prefs = {p["item"].lower(): p["store"] for p in tools.get_item_store_preferences()}
    assert "tahini" not in prefs
    # The single-row route, which IS a deliberate one-at-a-time choice, still
    # offers to remember it — that etiquette is untouched.
    assert signed_in.post(
        f"/api/grocery-list/{item_id}/store", json={"store": "Costco"}
    ).json()["needs_confirmation"] is True


# --- 7. the design rules this slice had to keep --------------------------


def test_the_new_screens_use_tokens_only():
    """Rule 9 — a literal hex outside theme.css is a review failure."""
    for marker in (".gro-howrow", ".gro-sortall-row", ".gro-nextrow", ".gro-secondary"):
        assert marker in SHELL_CSS, f"{marker} should be styled"
    block = SHELL_CSS[SHELL_CSS.index("/* ---------- SORT HOW"):SHELL_CSS.index("/* ---------- Review")]
    assert ".gro-secondary {" in block, "the block boundaries still cover the new rules"
    for line in block.splitlines():
        code = line.split("/*")[0]
        assert "#" not in code, f"literal colour in new grocery CSS: {line.strip()}"


def test_nothing_new_is_under_the_tap_size():
    """Rule 6 — every new tappable row is at least 44px tall."""
    block = SHELL_CSS[SHELL_CSS.index("/* ---------- SORT HOW"):SHELL_CSS.index("/* ---------- Review")]
    for rule in (".gro-howrow {", ".gro-nextrow {"):
        start = block.index(rule)
        body = block[start:block.index("}", start)]
        assert "min-height: 60px" in body, f"{rule} needs a real tap target"
    assert "min-height: 52px" in block[block.index(".gro-secondary {"):]


# --- 8. "Anywhere" is a place, not a disappearance ------------------------
# Reviewer, 2026-09-09, reproduced in Chromium: once "Any" persisted, a row
# answered that way was on NO screen for a multi-shop household. Out of the
# badge (it is answered), out of every store card (it has no store), and so
# out of reach of the row ⋯ that is the only way to change it. It survived
# only in the subtitle's count and as a trip ride-along. That was a real
# regression on the parent, where a reload put the row back in the queue —
# visible, and editable.


@_needs_node
def test_a_row_answered_anywhere_is_still_on_the_list():
    out = _node("""
setUp(0, [{ store: 'Costco', items: [{ id: 1, item: 'Eggs', quantity: '1', store: 'Costco' }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 2, item: 'Milk', quantity: '1', store: '', store_decided: 1 }
];
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  showsMilk: html.indexOf('Milk') !== -1,
  showsEggs: html.indexOf('Eggs') !== -1,
  hasAnywhereCard: html.indexOf('Anywhere &middot; 1') !== -1,
  inTheQueue: groUnsorted(groceryState.data).length
}));
""")
    assert out["showsEggs"] is True
    assert out["showsMilk"] is True, "a row answered 'Any' must still be readable"
    assert out["hasAnywhereCard"] is True
    assert out["inTheQueue"] == 0, "and it must not be asked about again"


@_needs_node
def test_an_anywhere_row_can_still_be_changed_back_to_a_shop():
    """The ⋯ is the only control that can move a row, so the card is only
    worth having if its rows carry one."""
    out = _node("""
setUp(0, [{ store: 'Costco', items: [{ id: 1, item: 'Eggs', quantity: '1', store: 'Costco' }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 2, item: 'Milk', quantity: '1', store: '', store_decided: 1 }
];
groceryState.openRowId = '2';
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  hasRowMenuButton: html.indexOf('data-gro="row-menu" data-id="2"') !== -1,
  offersAShop: /data-gro="row-store" data-id="2" data-store="Costco"/.test(html)
}));
""")
    assert out["hasRowMenuButton"] is True
    assert out["offersAShop"] is True


@_needs_node
def test_answering_anywhere_for_everything_still_leaves_a_trip_to_start():
    """The sub-case: no shop is tagged at all, so there were no stops and
    'Start the trip' never rendered. The shop the household buys from most
    is the stop — the same default 'Put all 40 at Loblaws' already offers."""
    out = _node("""
setUp(0, [], ['Loblaws', 'Costco']);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 1, item: 'Milk', quantity: '1', store: '', store_decided: 1 },
  { id: 2, item: 'Foil', quantity: '1', store: '', store_decided: 1 }
];
console.log(JSON.stringify({
  stops: groStoresWithNeeded(groceryState.data),
  foot: groDockHtml(groceryState.data, 'list'),
  onTheStop: (function () {
    groceryState.tripStops = groStoresWithNeeded(groceryState.data);
    groceryState.tripIndex = 0;
    return groTripItems(groceryState.data).map(function (i) { return i.item; });
  })()
}));
""")
    assert out["stops"] == ["Loblaws"], "the most-used shop stands in"
    assert 'data-gro="start-trip"' in out["foot"]
    assert out["onTheStop"] == ["Milk", "Foil"]


@_needs_node
def test_a_one_shop_household_still_sees_its_list_exactly_once():
    """Its loose pile is inside its shop's card; a second 'Anywhere' card
    would print the same rows twice."""
    out = _node("""
setUp(3, [], ['Loblaws']);
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  thing1: (html.match(/Thing 1</g) || []).length,
  anywhereCards: (html.match(/Anywhere &middot;/g) || []).length,
  card: /gro-store-name">([^<]*)</.exec(html)[1]
}));
""")
    assert out["thing1"] == 1
    assert out["anywhereCards"] == 0
    assert out["card"] == "Loblaws · 3"


@_needs_node
def test_rows_still_waiting_to_be_sorted_are_not_printed_twice():
    """The control. Unanswered rows live in SORT, which the badge opens —
    the Anywhere card must not pull them onto LIST as well."""
    out = _node("""
setUp(0, [{ store: 'Costco', items: [{ id: 1, item: 'Eggs', quantity: '1', store: 'Costco' }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 2, item: 'Milk', quantity: '1', store: '', store_decided: 0 }
];
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  showsMilk: html.indexOf('Milk') !== -1,
  inTheQueue: groUnsorted(groceryState.data).length
}));
""")
    assert out["showsMilk"] is False, "an unanswered row belongs to the queue only"
    assert out["inTheQueue"] == 1


@_needs_node
def test_the_trolley_at_a_stop_holds_the_same_rows_the_stop_showed():
    """groTripItems filters the shopless rows to the ones that ride along;
    the cart and the commit have to filter the same way, or a stop commits
    something that was never on it."""
    out = _node("""
setUp(0, [{ store: 'Costco', items: [], inCart: [{ id: 1, item: 'Eggs', store: 'Costco' }] }]);
groceryState.data.stores.Unassigned.inCart = [
  { id: 2, item: 'Milk', store: '', store_decided: 1 },
  { id: 3, item: 'Nutmeg', store: '', store_decided: 0 }
];
groceryState.tripStops = ['Costco'];
groceryState.tripIndex = 0;
console.log(JSON.stringify(groTripInCart(groceryState.data).map(function (i) { return i.item; })));
""")
    assert out == ["Eggs", "Milk"], "the unanswered row is not on this stop"


# --- 9. the bulk write is one transaction --------------------------------


def test_a_bulk_assign_that_fails_part_way_writes_nothing(signed_in, monkeypatch):
    """Reviewer, reproduced: a failure on row 3 of 4 left rows 1 and 2
    written and returned a 500 — a half-applied list, with the toast's Undo
    chip already spent on it."""
    from app.tools import stores as stores_mod

    ids = [
        signed_in.post("/api/grocery-list/add", json={"item": n, "quantity": "1"}).json()["item_id"]
        for n in ("Anchovies", "Bay leaves", "Cardamom", "Dill")
    ]
    real = stores_mod._stage_grocery_item_store
    calls = {"n": 0}

    def flaky(conn, item_id, store, remember, decided):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("connection dropped")
        return real(conn, item_id, store, remember, decided)

    monkeypatch.setattr(stores_mod, "_stage_grocery_item_store", flaky)

    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": i, "store": "Costco"} for i in ids]},
    )
    assert res.status_code == 500

    rows = {it["id"]: it for it in tools.list_grocery_list()}
    for i in ids:
        assert (rows[i]["store"], rows[i]["store_decided"]) == ("", 0), (
            "a failed batch must leave every row exactly as it was"
        )


def test_a_batch_bigger_than_a_grocery_list_is_refused(signed_in):
    """Unbounded, one connection per row, it was 5000 connections and 3.3s
    in one request. Now it is a 400 before anything is written."""
    item_id = signed_in.post(
        "/api/grocery-list/add", json={"item": "Sumac", "quantity": "1"}
    ).json()["item_id"]
    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "Costco"}] * 501},
    )
    assert res.status_code == 400
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert rows[item_id]["store"] == "", "and nothing was written on the way to refusing"


@_needs_node
def test_a_failed_undo_keeps_its_payload_so_there_is_an_again():
    """Reviewer, reproduced: the payload was cleared on the tap, so a failed
    undo took the only record of the previous state with it — 40 rows at a
    shop nobody chose, a toast saying "try again", and no again."""
    out = _node("""
setUp(4);
const items = groUnsorted(groceryState.data);
groBulkAssign(
  items.map(function (it) { return { item_id: it.id, store: 'Loblaws', decided: true }; }),
  groPreviousStores(items),
  '4 things at Loblaws.'
);
settle(function () {
  FAIL_ON = POSTS.length + 1;   // the undo's own POST fails
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({
      payloadKept: groceryState.bulkUndo !== null,
      payloadLength: groceryState.bulkUndo ? groceryState.bulkUndo.length : 0,
      offeredAgain: lastToast().action
    }));
  });
});
""")
    assert out["payloadKept"] is True, "a failed undo must stay undoable"
    assert out["payloadLength"] == 4
    assert out["offeredAgain"] == "Undo", "and the chip has to come back"


@_needs_node
def test_a_successful_undo_spends_its_payload_exactly_once():
    out = _node("""
setUp(4);
const items = groUnsorted(groceryState.data);
groBulkAssign(
  items.map(function (it) { return { item_id: it.id, store: 'Loblaws', decided: true }; }),
  groPreviousStores(items),
  '4 things at Loblaws.'
);
settle(function () {
  tapUndo();
  settle(function () {
    const postsAfterUndo = POSTS.length;
    tapUndo();
    settle(function () {
      console.log(JSON.stringify({
        cleared: groceryState.bulkUndo === null,
        secondTapWroteNothing: POSTS.length === postsAfterUndo
      }));
    });
  });
});
""")
    assert out == {"cleared": True, "secondTapWroteNothing": True}


# --- 10. every writer of `store` maintains `store_decided` ---------------


def test_clearing_a_store_preference_re_opens_the_question(signed_in):
    """_apply_store_to_matching_rows is the second writer of the column, and
    it used to write only half of it: a row cleared in chat kept
    store_decided = 1 and was then permanently "answered" with no shop on
    it. Same drift class as snacks_per_week_set, and delete_preference is
    the precedent for clearing the flag."""
    item_id = signed_in.post(
        "/api/grocery-list/add", json={"item": "Halloumi", "quantity": "1"}
    ).json()["item_id"]
    signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Costco"})
    assert [it for it in tools.list_grocery_list() if it["id"] == item_id][0]["store_decided"] == 1

    tools.set_item_store("Halloumi", "")

    row = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert row["store"] == ""
    assert row["store_decided"] == 0, "no opinion about the shop re-opens the question"


def test_applying_a_remembered_store_counts_as_answered(signed_in):
    item_id = signed_in.post(
        "/api/grocery-list/add", json={"item": "Fennel", "quantity": "1"}
    ).json()["item_id"]
    tools.set_item_store("Fennel", "Farm Boy")
    row = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert (row["store"], row["store_decided"]) == ("Farm Boy", 1)


# --- 11. the click handlers, run rather than read ------------------------


@_needs_node
def test_the_badge_opens_the_fast_paths_only_when_there_are_enough():
    out = _node("""
setUp(40);
click({ gro: 'goto-sort' });
const many = groceryState.step;
setUp(3);
groceryState.step = 'list';
click({ gro: 'goto-sort' });
console.log(JSON.stringify({ many: many, few: groceryState.step }));
""")
    assert out == {"many": "sorthow", "few": "sort"}


@_needs_node
def test_a_mis_tapped_done_at_this_stop_can_be_taken_back():
    """"Done at Costco" is a full-width apricot under a list of things still
    to tick. Without a way back the mis-tap ended that shop for the trip."""
    out = _node("""
setUp(0, [
  { store: 'Costco', items: [{ id: 1, item: 'Rice', store: 'Costco' }] },
  { store: 'Metro', items: [{ id: 2, item: 'Eggs', store: 'Metro' }] }
]);
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripIndex = 0;
groceryState.tripDone = {};
groceryState.step = 'trip';
click({ gro: 'stop-done' });
settle(function () {
  const landed = groceryState.step;
  const back = groHeadFor(groceryState.data, 'next').back;
  click({ gro: 'step-back' });
  settle(function () {
    console.log(JSON.stringify({
      landed: landed,
      backLabel: back,
      reopenedStep: groceryState.step,
      atStop: groTripStore(),
      stillFinished: Object.keys(groceryState.tripDone),
      snapshot: groceryState.tripStops
    }));
  });
});
""")
    assert out["landed"] == "next"
    assert out["backLabel"] == "‹ Back to Costco"
    assert out["reopenedStep"] == "trip"
    assert out["atStop"] == "Costco"
    assert out["stillFinished"] == [], "the reopened stop is a choice again"
    assert out["snapshot"] == ["Costco", "Metro"], "and the snapshot never moved"


@_needs_node
def test_picking_a_stop_out_of_order_leaves_the_snapshot_alone():
    out = _node("""
setUp(0, [
  { store: 'Costco', items: [{ id: 1, item: 'Rice', store: 'Costco' }] },
  { store: 'Loblaws', items: [{ id: 2, item: 'Milk', store: 'Loblaws' }] },
  { store: 'Metro', items: [{ id: 3, item: 'Eggs', store: 'Metro' }] }
]);
groceryState.tripStops = ['Costco', 'Loblaws', 'Metro'];
groceryState.tripDone = { Costco: true };
groceryState.step = 'next';
click({ gro: 'next-stop', store: 'Metro' });
console.log(JSON.stringify({
  step: groceryState.step,
  at: groTripStore(),
  snapshot: groceryState.tripStops
}));
""")
    assert out["at"] == "Metro"
    assert out["step"] == "trip"
    assert out["snapshot"] == ["Costco", "Loblaws", "Metro"]


@_needs_node
def test_a_staged_row_change_writes_nothing_until_the_button():
    out = _node("""
setUp(6);
groceryState.step = 'sortall';
click({ gro: 'sortall-pick', id: '2', store: 'Costco' });
click({ gro: 'sortall-pick', id: '4', store: '' });
const duringStaging = POSTS.length;
click({ gro: 'sortall-save' });
settle(function () {
  console.log(JSON.stringify({
    duringStaging: duringStaging,
    picks: groceryState.sortAllPicks,
    sent: POSTS[0] ? POSTS[0].body.assignments : null
  }));
});
""")
    assert out["duringStaging"] == 0, "tapping a chip must not cost a request"
    assert out["sent"][1] == {"item_id": 2, "store": "Costco", "decided": True}
    assert out["sent"][3] == {"item_id": 4, "store": "", "decided": True}
    assert out["sent"][0]["store"] == "Loblaws", "the rest keep the default"


@_needs_node
def test_the_stand_in_stop_does_not_draw_a_card_about_nothing():
    """When every row is answered "Anywhere", the most-used shop becomes the
    stop so the trip can start — but it holds nothing of its own, and
    "Loblaws · 0" above "Anywhere · 2" is a card about nothing."""
    out = _node("""
setUp(0, [], ['Loblaws', 'Costco']);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 1, item: 'Milk', quantity: '1', store: '', store_decided: 1 },
  { id: 2, item: 'Foil', quantity: '1', store: '', store_decided: 1 }
];
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  cards: (html.match(/gro-store-name">([^<]*)</g) || []).map(function (m) {
    return /gro-store-name">([^<]*)</.exec(m)[1];
  }),
  stopsForTheTrip: groStoresWithNeeded(groceryState.data)
}));
""")
    assert out["cards"] == ["Anywhere &middot; 2"], "no empty stop card"
    assert out["stopsForTheTrip"] == ["Loblaws"], "but the trip still has somewhere to go"


# --- 12. what the display filters, the commit must not -------------------


@_needs_node
def test_finishing_a_stop_buys_everything_in_the_trolley_even_the_unanswered():
    """Second reviewer, reproduced: the ride-along filter added for the
    DISPLAY was applied to the COMMIT too, so an Unassigned row that reached
    the trolley without an answer — a store added or a preference cleared
    mid-trip — was committed by nothing and drawn by nothing. It stayed
    in_cart forever, on no screen, with the receipt under-reporting.

    The stop still SHOWS only what rides along (the test above pins that).
    This pins the other half: something physically in the cart is bought."""
    out = _node("""
setUp(0, [{ store: 'Costco', items: [], inCart: [{ id: 1, item: 'Eggs', store: 'Costco' }] }]);
groceryState.data.stores.Unassigned.inCart = [
  { id: 2, item: 'Milk', store: '', store_decided: 1 },
  { id: 3, item: 'Nutmeg', store: '', store_decided: 0 }
];
groceryState.tripStops = ['Costco'];
groceryState.tripIndex = 0;
groceryState.tripBought = 0;
groFinishStore('Costco').then(function (n) {
  console.log(JSON.stringify({
    bought: n,
    purchased: POSTS.filter(function (p) { return p.body.status === 'purchased'; })
      .map(function (p) { return Number(/grocery-list\\/(\\d+)\\//.exec(p.url)[1]); }).sort(),
    shownAtTheStop: groTripInCart(groceryState.data).map(function (i) { return i.item; })
  }));
});
""")
    assert out["purchased"] == [1, 2, 3], "the unanswered row in the cart is bought too"
    assert out["bought"] == 3, "and the receipt counts it"
    assert out["shownAtTheStop"] == ["Eggs", "Milk"], "while the stop still shows only its own"


@_needs_node
def test_an_undo_that_works_on_the_second_try_replaces_the_failure_line():
    """The failure toast holds for its full window, so a retry that worked
    otherwise leaves "tap Undo to try again" sitting over a list that has
    already been put back. The chip is inert by then; it is the words that
    contradict the screen."""
    out = _node("""
groceryState.bulkUndo = [{ item_id: 1, store: 'Costco', decided: true }];
FAIL_ON = 1;
groRunBulkUndo();
settle(function () {
  const afterFailure = lastToast();
  FAIL_ON = 0;
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({
      afterFailure: afterFailure,
      afterRetry: lastToast(),
      payloadSpent: groceryState.bulkUndo === null
    }));
  });
});
""")
    assert "try again" in out["afterFailure"]["msg"]
    assert out["afterFailure"]["action"] == "Undo", "a failure keeps the chip"
    assert "try again" not in out["afterRetry"]["msg"], "the retry replaces the failure line"
    assert out["afterRetry"]["action"] is None, "and offers nothing more to undo"
    assert out["payloadSpent"], "a successful undo spends its payload"
