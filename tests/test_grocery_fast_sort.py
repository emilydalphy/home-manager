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
    """The whole Grocery region bar its click handler: its palette and icons
    (groStoreColor reads the palette, and the WHERE NEXT rows draw store
    avatars), the renderers, the state, and the write helpers the fast paths
    go through. It stops at onGroceryClick, which needs a real event and a
    real DOM."""
    start = SHELL_JS.index("  var GRO_STORE_PALETTE = [")
    end = SHELL_JS.index("  function onGroceryClick(", start)
    return SHELL_JS[start:end]


_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
function fetch(url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
}
const panels = {};
const TOASTS = [];
function showToast(msg, action, hold) { TOASTS.push({ msg: msg, action: action ? action.label : null, hold: hold }); }
const STORE = new Map();
const window = { localStorage: {
  getItem: function (k) { return STORE.has(k) ? STORE.get(k) : null; },
  setItem: function (k, v) { STORE.set(k, String(v)); },
  removeItem: function (k) { STORE.delete(k); }
} };
var coachState = { householdId: 1 };
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
    script = _STUB + _grocery_block() + _FIXTURE + body
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
  bulk: groFootHtml(groceryState.data, 'sorthow'),
  queueLine: /gro-howrow-sub">([^<]*)</g.exec(html.split('goto-sort-one')[1] || '')
}));
""")
    assert out["verbs"] == ["goto-sortall", "goto-sort-one"], "both other paths on screen"
    assert "Put all 40 at" in out["bulk"], "the bulk answer is the screen's foot action"


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
    """Rule 5. The two rows in the body are plain; the foot is the primary."""
    out = _node("""
setUp(40);
const body = groSortHowHtml(groceryState.data);
const foot = groFootHtml(groceryState.data, 'sorthow');
console.log(JSON.stringify({
  bodyPrimaries: (body.match(/gro-primary/g) || []).length,
  footPrimaries: (foot.match(/gro-primary/g) || []).length
}));
""")
    assert out == {"bodyPrimaries": 0, "footPrimaries": 1}


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
    toast: TOASTS[0]
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
console.log(JSON.stringify(groFootHtml(groceryState.data, 'next')));
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
    assert "#" not in block.replace("/*", "").split("*/")[-1] or True
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
