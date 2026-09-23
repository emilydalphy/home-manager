"""
Shop: "Add something" lives in the dock; the add sheet picks and
remembers the store (Loop Board 3e31f4c0-5231-817d-9bd4-fb77d6f5922a,
2026-09-21; Emily's S1 / S1b mockups).

The add row at the top of the list — field, camera, Add — is gone. The
root's dock holds an outline "+ Add something" beside the chat FAB, and
stays put while the list scrolls. Tapping it opens a bottom sheet: the
field (focused) with the camera inside it, "Where do you get it?" as one
44px chip per store plus Anywhere, pre-picked — the store whose card the
list is scrolled to, else the usual store of the thing typed once it
names a known item, else the store the last add went to — and one
apricot "Add to Costco" ("Add to the list" for Anywhere). Add writes the
line straight into that store in the same request (the /add route's
store, remembered as the item's usual), the sheet closes, the list
re-renders, and the toast names the thing that was just added — "Carrots
was added" — with Undo. With no signal the add queues like a tick and
the row shows at once, with the same wording.

Until 2026-09-23 the sheet also carried a celadon line under the chips
spelling out what the pick would do ("I'll remember Costco for it next
time") and the toast after Add said the generic "Changes saved · Put
back". Emily cut the line — the row landing under that store's card
already shows the remembering took, so the line was saying something the
screen already said — and asked the toast to just name the thing added,
with Undo in place of Put back. The remembering itself (teaching the
item's usual store) is unchanged; only the announcement of it went.

Behaviour runs under node against shell.js's own functions
(tests/shop_harness), with the real static/grocery-offline.js for the
queue; the route runs over the FastAPI TestClient.
"""
from __future__ import annotations

import json
from pathlib import Path

import nodeharness
from app import tools
from shop_harness import CLICK, FIXTURE, STUB, grocery_block, needs_node, run

REPO = Path(__file__).resolve().parent.parent
OFFLINE = REPO / "static" / "grocery-offline.js"
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

# Two shops, a thing at each, one loose thing.
_LIST = """
function twoShops() {
  return setUp(1, [
    { store: 'Costco', items: [{ id: 3, item: 'Orzo', quantity: '1 box', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' }] },
    { store: 'Loblaws', items: [{ id: 10, item: 'Lemons', quantity: '3', store: 'Loblaws', store_decided: 1, category: 'produce', status: 'needed' }] }
  ], ['Costco', 'Loblaws']);
}
"""


def _node(body: str):
    return run(_LIST + body)


# --- 1. the dock ---------------------------------------------------------------


@needs_node
def test_the_list_has_no_add_row_and_the_dock_holds_add_something():
    out = _node("""
var data = twoShops();
var html = groListHtml(data);
console.log(JSON.stringify({
  addRowInList: html.indexOf('gro-add-item') !== -1 || html.indexOf('gro-scan-btn') !== -1,
  dock: groDockHtml(data, 'list'),
  sortallDock: (function () { groceryState.step = 'sortall'; var d = groDockHtml(data, 'sortall'); groceryState.step = 'list'; return d; })()
}));
""")
    assert out["addRowInList"] is False, "the add row is gone from the top of the list"
    dock = out["dock"]
    assert '<button type="button" class="gro-add-open" data-gro="add-open">' in dock
    assert 'stroke-width="2.2"' in dock and 'd="M12 5v14M5 12h14"' in dock, "the plus, a stroke icon"
    assert dock.endswith("Add something</button>")
    assert "dock-primary" not in dock and "gro-primary" not in dock, "an outline, not the screen's action"
    assert out["sortallDock"] == "", "the deeper steps keep their own docks"


@needs_node
def test_an_empty_list_keeps_go_to_plan_and_still_offers_add_something():
    out = _node("""
setUp(0, [], ['Costco', 'Loblaws']);
console.log(JSON.stringify(groDockHtml(groceryState.data, 'list')));
""")
    assert out.index('data-gro="goto-plan"') < out.index('data-gro="add-open"')
    assert out.count("dock-primary") == 1


def test_the_dock_button_is_an_outline_of_48px_and_the_sheet_is_at_body_level():
    outline = SHELL_CSS.split(".gro-add-open {", 1)[1][:700]
    assert "min-height: 48px" in outline and "background: transparent" in outline
    assert "border: 1.5px solid var(--hairline-strong)" in outline
    assert "--apricot" not in outline
    assert 'id="gro-add-sheet" hidden role="dialog" aria-label="Add something"' in SHELL_HTML
    assert 'id="gro-add-body"' in SHELL_HTML and 'id="gro-add-scrim"' in SHELL_HTML
    assert "#gro-scan-sheet, #gro-add-sheet {" in SHELL_CSS, "the same sheet mechanics as the photo sheet"
    # The row's CSS went with the row; the celadon note's own CSS went
    # with the note (2026-09-23 — Emily, "remove the blue text box").
    assert ".gro-add-btn" not in SHELL_CSS and ".gro-add {" not in SHELL_CSS
    assert ".gro-add-note" not in SHELL_CSS


# --- 2. the sheet ---------------------------------------------------------------


@needs_node
def test_the_sheet_is_the_field_the_camera_the_chips_and_one_apricot():
    out = _node("""
var data = twoShops();
console.log(JSON.stringify({
  costco: groAddSheetHtml({ typed: 'Cilantro', store: 'Costco' }, data),
  anywhere: groAddSheetHtml({ typed: '2 bunches cilantro', store: '' }, data),
  empty: groAddSheetHtml({ typed: '', store: 'Loblaws' }, data)
}));
""")
    c = out["costco"]
    assert 'id="gro-add-item" value="Cilantro"' in c
    assert c.index('id="gro-add-item"') < c.index('data-gro="scan-open"') < c.index("Where do you get it?"), (
        "the field with the camera beside it, then the question")
    assert c.count('data-gro="add-store"') == 3, "one chip per store, plus Anywhere"
    assert '<button type="button" class="gro-pill gro-pill-on" data-gro="add-store" data-store="Costco" aria-pressed="true">Costco</button>' in c
    assert 'class="gro-pill" data-gro="add-store" data-store="Loblaws" aria-pressed="false">Loblaws</button>' in c
    assert 'class="gro-pill" data-gro="add-store" data-store="" aria-pressed="false">Anywhere</button>' in c
    assert '<button type="button" class="dock-primary gro-add-go" id="gro-add-go" data-gro="add">Add to Costco</button>' in c
    assert c.count("dock-primary") == 1
    # The celadon note under the chips is gone (2026-09-23, Emily: "remove
    # the blue text box" — the remembering doesn't need an announcement).
    assert "gro-add-note" not in c and "I’ll remember" not in c

    a = out["anywhere"]
    assert 'data-store="" aria-pressed="true">Anywhere</button>' in a
    assert 'data-gro="add">Add to the list</button>' in a
    assert "I’ll put it under" not in a and "gro-add-note" not in a

    e = out["empty"]
    assert 'data-gro="add">Add to Loblaws</button>' in e
    assert "gro-add-note" not in e


@needs_node
def test_a_one_shop_or_no_shop_household_is_not_asked_where():
    out = _node("""
setUp(2, [], ['Costco']);
var one = groAddSheetHtml({ typed: 'Cilantro', store: 'Costco' }, groceryState.data);
setUp(2, [], []);
var none = groAddSheetHtml({ typed: 'Cilantro', store: '' }, groceryState.data);
console.log(JSON.stringify({ one: one, none: none }));
""")
    for html in (out["one"], out["none"]):
        assert "Where do you get it?" not in html and 'data-gro="add-store"' not in html
        assert "gro-add-note" not in html
        assert 'data-gro="add">Add to the list</button>' in html
        assert 'id="gro-add-item"' in html and 'data-gro="scan-open"' in html


@needs_node
def test_the_chips_are_44px_pills():
    pill = SHELL_CSS.split(".gro-pill {", 1)[1][:400]
    assert "min-height: 44px" in pill
    assert "border-radius: 999px" in pill


# --- 3. the pre-pick ------------------------------------------------------------


@needs_node
def test_the_pre_pick_is_the_card_in_view_then_the_usual_store_then_the_last_add():
    out = _node("""
var data = twoShops();
groceryState.itemStorePrefs = { cilantro: 'Loblaws', 'paper towels': 'Costco' };
var none = groAddSheetPrePick('', data);
window.localStorage.setItem('pomona.grocery.lastAddStore', 'Loblaws');
var last = groAddSheetPrePick('', data);
var usual = groAddSheetPrePick('Cilantro', data);
var usualWithQty = groAddSheetPrePick('2 bunches cilantro', data);
var usualPlural = groAddSheetPrePick('paper towel', data);
groceryState.storeInView = 'Costco';
var inView = groAddSheetPrePick('Cilantro', data);
groceryState.storeInView = 'Your list';
var standIn = groAddSheetPrePick('Cilantro', data);
groceryState.storeInView = null;
groceryState.itemStorePrefs = { cilantro: 'Farm Boy' };
var gone = groAddSheetPrePick('Cilantro', data);
console.log(JSON.stringify({ none: none, last: last, usual: usual, usualWithQty: usualWithQty,
  usualPlural: usualPlural, inView: inView, standIn: standIn, gone: gone }));
""")
    assert out["none"] == "", "nothing known: Anywhere"
    assert out["last"] == "Loblaws", "the store the last add went to"
    assert out["usual"] == "Loblaws" and out["usualWithQty"] == "Loblaws", "a known item's usual store, quantity ignored"
    assert out["usualPlural"] == "Costco", "a trailing s either way is forgiven"
    assert out["inView"] == "Costco", "the card the list is scrolled to comes first"
    assert out["standIn"] == "Loblaws", "the one-list stand-in is not a store"
    assert out["gone"] == "Loblaws", "a usual store the household no longer shops at is no answer"


@needs_node
def test_the_last_add_store_survives_and_anywhere_forgets_it():
    out = _node("""
groWriteLastAddStore('Costco');
var a = groReadLastAddStore();
groWriteLastAddStore('');
var b = groReadLastAddStore();
console.log(JSON.stringify([a, b]));
""")
    assert out == ["Costco", None]


@needs_node
def test_typing_re_picks_after_a_beat_but_never_over_a_tapped_chip():
    out = _node("""
var data = twoShops();
groceryState.itemStorePrefs = { cilantro: 'Loblaws' };
groceryState.addSheet = { typed: '', store: '', picked: false };
groAddSheetTyped('cil');
var atOnce = groceryState.addSheet.store;
groAddSheetTyped('cilantro');
setTimeout(function () {
  var afterBeat = groceryState.addSheet.store;
  groAddSheetPick('Costco');
  groAddSheetTyped('cilantr');
  groAddSheetTyped('cilantro');
  setTimeout(function () {
    console.log(JSON.stringify({ atOnce: atOnce, afterBeat: afterBeat, tapped: groceryState.addSheet }));
  }, 320);
}, 320);
""")
    assert out["atOnce"] == "", "the lookup waits for the typing to pause"
    assert out["afterBeat"] == "Loblaws", "then the usual store is picked"
    assert out["tapped"] == {"typed": "cilantro", "store": "Costco", "picked": True}, "a tap holds"


@needs_node
def test_a_tick_and_a_row_menu_tap_record_the_card_for_the_sheets_first_guess():
    out = _node("""
window.PomonaGroceryOffline = null;
var data = twoShops();
groOffline = null;
var before = groceryState.storeInView;
clickIfRendered({ gro: 'line-tick', id: '10', bought: '0' });
var ticked = groceryState.storeInView;
clickIfRendered({ gro: 'row-menu', id: '3' });
var menu = groceryState.storeInView;
clickIfRendered({ gro: 'row-menu', id: '1' });
console.log(JSON.stringify({ before: before, ticked: ticked, menu: menu, loose: groceryState.storeInView }));
""")
    assert out["before"] is None
    assert out["ticked"] == "Loblaws"
    assert out["menu"] == "Costco"
    assert out["loose"] == "Costco", "a loose row is no store; the last real card stands"


def test_the_cards_are_watched_with_an_intersection_observer_that_is_only_read():
    watch = SHELL_JS.split("function groWatchStoreCards(", 1)[1].split("\n  }\n", 1)[0]
    assert "new IntersectionObserver(" in watch
    assert "'.gro-store[data-store]'" in watch
    assert "groceryState.storeInView = best.target.getAttribute('data-store')" in watch
    assert "innerHTML" not in watch, "the cards are the store renderer's; this only reads them"
    render = SHELL_JS.split("  function renderGrocery() {", 1)[1].split("\n  }\n", 1)[0]
    assert "if (onRoot) groWatchStoreCards(body);" in render


# --- 4. the add -----------------------------------------------------------------


@needs_node
def test_add_posts_the_store_with_the_line_closes_the_sheet_and_names_what_was_added():
    out = _node("""
var data = twoShops();
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  var body = url === '/api/grocery-list/add' ? { item_id: 77, item: 'cilantro', merged: false } : {};
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
groceryState.addSheet = { typed: '2 bunches cilantro', store: 'Costco', picked: true };
groAddItem().then(function () {
  var toast = lastToast();
  var sheet = groceryState.addSheet;
  var last = groReadLastAddStore();
  var prefs = JSON.parse(JSON.stringify(groceryState.itemStorePrefs));
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({ posts: POSTS.map(function (p) { return [p.url, p.body]; }), toast: toast,
      sheet: sheet, last: last, prefs: prefs }));
  });
});
""")
    assert out["posts"][0] == ["/api/grocery-list/add", {
        "item": "cilantro", "quantity": "2 bunches", "category": "other", "store": "Costco", "remember": True}]
    assert out["sheet"] is None, "the sheet closed on the tap"
    # The name is the server's own (`r.item`), and Undo is the toast's
    # only action (2026-09-23 — no more "Changes saved · Put back").
    assert out["toast"] == {"msg": "cilantro was added", "action": "Undo", "hold": 8000}
    assert out["last"] == "Costco", "the next sheet's third guess"
    assert out["prefs"] == {"cilantro": "Costco"}, "the screen's copy of the usual stores follows"
    assert out["posts"][-1][0] == "/api/grocery-list/77/remove", "Undo removes the line"


@needs_node
def test_add_to_anywhere_sends_an_empty_store_and_a_one_shop_household_sends_none():
    out = _node("""
var data = twoShops();
groceryState.addSheet = { typed: 'foil', store: '', picked: true };
groAddItem().then(function () {
  setUp(2, [], ['Costco']);
  groceryState.addSheet = { typed: 'foil', store: '', picked: false };
  return groAddItem();
}).then(function () {
  console.log(JSON.stringify(posts('/api/grocery-list/add').map(function (p) { return p.body; })));
});
""")
    assert out[0] == {"item": "foil", "quantity": "", "category": "other", "store": "", "remember": True}
    assert out[1] == {"item": "foil", "quantity": "", "category": "other"}, "not asked: the server's usual store applies"


@needs_node
def test_undo_on_a_merged_line_restores_its_old_amount_and_store():
    out = _node("""
var data = twoShops();
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  var body = url === '/api/grocery-list/add' ? { item_id: 3, item: 'Orzo', quantity: '2 box', merged: true } : {};
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
groceryState.addSheet = { typed: 'orzo 1', store: 'Loblaws', picked: true };
groAddItem().then(function () {
  tapUndo();
  settle(function () {
    console.log(JSON.stringify(POSTS.slice(1).map(function (p) { return [p.url, p.body]; })));
  });
});
""")
    assert out == [
        ["/api/grocery-list/3/update", {"quantity": "1 box"}],
        ["/api/grocery-list/3/store", {"store": "Costco", "remember": False}],
    ]


@needs_node
def test_a_refused_add_reopens_the_sheet_with_the_typing_in_it():
    out = _node("""
var data = twoShops();
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: false, status: 500, json: function () { return Promise.resolve({}); } });
};
groceryState.addSheet = { typed: 'cilantro', store: 'Costco', picked: true };
groAddItem().then(function (ok) {
  console.log(JSON.stringify({ ok: ok, sheet: groceryState.addSheet, toast: lastToast().msg }));
});
""")
    assert out["ok"] is False
    assert out["sheet"] == {"typed": "cilantro", "store": "Costco", "picked": True}
    assert out["toast"] == "Couldn't add that — try again."


@needs_node
def test_the_empty_field_adds_nothing():
    out = _node("""
twoShops();
groceryState.addSheet = { typed: '   ', store: 'Costco', picked: false };
groAddItem();
settle(function () { console.log(JSON.stringify({ posts: POSTS.length, open: !!groceryState.addSheet })); });
""")
    assert out == {"posts": 0, "open": True}


def test_voice_and_the_photo_sheet_still_add_the_way_they_did():
    voice = SHELL_JS.split("function groHandleVoiceCommand(", 1)[1][:3000]
    assert "'/api/grocery-list/add'" in voice
    assert "groAddSheetOpen" not in voice
    assert "case 'scan-open':" in SHELL_JS and "groScanOpenPicker();" in SHELL_JS
    assert "groAddSheetEl.addEventListener('click', onGroceryClick);" in SHELL_JS, (
        "the sheet's camera and chips go through the same handler as the panel's controls")
    assert "'/api/grocery-list/confirm-scan'" in SHELL_JS


@needs_node
def test_picking_a_store_still_teaches_its_usual_with_no_note_to_say_so():
    """Card 3e31f4c0-5231-81b5 ("the Add sheet says too much"): the
    celadon note that used to announce the remembering is gone (see the
    sheet-body tests above), but the remembering itself is untouched —
    picking Costco for cilantro still teaches Costco as cilantro's usual
    store, silently, the same write the note used to describe rather than
    cause."""
    out = _node("""
var data = twoShops();
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  var body = url === '/api/grocery-list/add' ? { item_id: 77, item: 'cilantro', merged: false } : {};
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
var html = groAddSheetHtml({ typed: 'Cilantro', store: 'Costco' }, data);
groceryState.addSheet = { typed: 'Cilantro', store: 'Costco', picked: true };
groAddItem().then(function () {
  console.log(JSON.stringify({
    html: html,
    remembered: POSTS[0].body.remember,
    prefs: groceryState.itemStorePrefs
  }));
});
""")
    assert "gro-add-note" not in out["html"] and "I’ll remember" not in out["html"], (
        "no announcement in the markup")
    assert out["remembered"] is True, "the write still asks the server to remember it"
    assert out["prefs"] == {"cilantro": "Costco"}, "and the screen's own copy still follows, unannounced"


# --- 4a. the toast names what happened -----------------------------------------

# groScanSave (photo-scan confirm) needs its own tiny `document` — the real
# one lives outside grocery_block() and this path only ever reaches a save
# button that may not exist in this harness, so a `querySelector` that
# always answers "not there" is enough; nothing here opens or closes a
# sheet through it. `loadGrocery` is stubbed too: the real one is also
# outside grocery_block() and reads back a full list this test has no
# stake in.
_SCAN_DOC = """
var document = { getElementById: function () { return null; }, querySelector: function () { return null; } };
function loadGrocery() { return Promise.resolve(); }
"""


@needs_node
def test_scan_confirm_names_what_was_added_and_pluralizes_things_not_items():
    """Multi-add (Loop Board 3e31f4c0-5231-81b5, acceptance criterion 7):
    one added item is named the same way a typed add is; more than one
    stays plain — "2 things were added" — rather than counting "items"."""
    script = (
        STUB + _SCAN_DOC + grocery_block() + CLICK + FIXTURE
        + """
var RESPONSES = {};
fetch = function (url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  var body = url === '/api/grocery-list/confirm-scan' ? RESPONSES.next : {};
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
function scan(items, response) {
  groScanState.items = items;
  RESPONSES.next = response;
  TOASTS.length = 0;
  return groScanSave().then(function () { return TOASTS[TOASTS.length - 1].msg; });
}
(async function () {
  var one = await scan(
    [{ item: 'Carrots', quantity: '', category: 'produce', keep: true }],
    { added: ['Carrots'], merged_with_existing: [] }
  );
  var two = await scan(
    [{ item: 'Carrots', quantity: '', category: 'produce', keep: true },
     { item: 'Milk', quantity: '', category: 'dairy', keep: true }],
    { added: ['Carrots', 'Milk'], merged_with_existing: [] }
  );
  var mergedOnly = await scan(
    [{ item: 'Eggs', quantity: '', category: 'dairy', keep: true }],
    { added: [], merged_with_existing: ['Eggs'] }
  );
  var mixed = await scan(
    [{ item: 'Carrots', quantity: '', category: 'produce', keep: true },
     { item: 'Milk', quantity: '', category: 'dairy', keep: true }],
    { added: ['Carrots'], merged_with_existing: ['Milk'] }
  );
  console.log(JSON.stringify({ one: one, two: two, mergedOnly: mergedOnly, mixed: mixed }));
})();
"""
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    out = json.loads(res.stdout.strip())
    assert out["one"] == "Carrots was added", "one thing: named the same way a typed add is"
    assert out["two"] == "2 things were added", "more than one: a plain count, not \"items\""
    assert out["mergedOnly"] == "1 thing combined with what was already on the list"
    assert out["mixed"] == "Carrots was added, 1 thing combined with what was already on the list"


# --- 4b. the camera from inside the sheet --------------------------------------------

def _shell_fn(name: str) -> str:
    """The shell's own top-level `function name(...) { ... }`, up to its
    closing brace at the function's own indent."""
    start = SHELL_JS.index("  function " + name + "(")
    end = SHELL_JS.index("\n  }\n", start) + len("\n  }\n")
    return SHELL_JS[start:end]


# A document just wide enough for the two body-level sheets: each id is one
# element with `hidden` and a classList; openSheet/closeSheet are the shell's
# real ones (they only touch hidden/classList/offsetHeight/listeners). The
# 'transitionend' listener never fires here — closeSheet's own setTimeout
# fallback (motionMs + 50ms) finishes the close, so the test reads the sheets
# back on a timer longer than that.
_DOC = """
var ELS = {};
function el(id) {
  if (!ELS[id]) ELS[id] = {
    id: id, hidden: true, innerHTML: '', value: '', clicks: 0,
    classList: (function () { var set = {}; return {
      add: function (c) { set[c] = 1; }, remove: function (c) { delete set[c]; },
      contains: function (c) { return !!set[c]; } }; })(),
    click: function () { this.clicks += 1; },
    addEventListener: function () {}, focus: function () {}, setSelectionRange: function () {},
    querySelector: function () { return null; }, offsetHeight: 0
  };
  return ELS[id];
}
var document = { getElementById: el };
function motionMs() { return 0; }
""" + _shell_fn("openSheet") + _shell_fn("closeSheet")


def _sheets(body: str):
    res = nodeharness.run_node(STUB + _DOC + grocery_block() + CLICK + FIXTURE + _LIST + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@needs_node
def test_the_camera_tapped_inside_the_add_sheet_leaves_it_up_until_the_photo_lands_then_closes_it():
    out = _sheets("""
twoShops();
groAddSheetOpen({ typed: 'cilan' });
var before = { addOpen: !el('gro-add-sheet').hidden, typed: groceryState.addSheet && groceryState.addSheet.typed };
// The sheet is at body level, outside the screen mirror clickIfRendered
// checks — so the button is looked for in the sheet's own markup first.
if (el('gro-add-body').innerHTML.indexOf('data-gro="scan-open"') === -1) throw new Error('the add sheet draws no camera');
clickHandlerDirectly({ gro: 'scan-open' });
var tapped = { picker: el('gro-scan-input').clicks, addOpen: !el('gro-add-sheet').hidden, typed: groceryState.addSheet && groceryState.addSheet.typed };
groScanUploadPhoto({ name: 'list.jpg' });
// closeSheet hides the element on its own fallback timer (motionMs + 50ms
// here; no transitionend under node) — read after that, not at settle's 30ms.
setTimeout(function () {
  console.log(JSON.stringify({
    before: before, tapped: tapped,
    landed: { addOpen: !el('gro-add-sheet').hidden, addState: groceryState.addSheet, scanOpen: !el('gro-scan-sheet').hidden && el('gro-scan-sheet').classList.contains('is-open') }
  }));
  process.exit(0);
}, 150);
""")
    assert out["before"] == {"addOpen": True, "typed": "cilan"}
    assert out["tapped"] == {"picker": 1, "addOpen": True, "typed": "cilan"}, (
        "the tap opens the picker and leaves the sheet up — backing out of the camera keeps the typing")
    assert out["landed"]["addOpen"] is False and out["landed"]["addState"] is None, (
        "the photo landing closes the add sheet — it sits later in shell.html at the same z-index and would paint over the review")
    assert out["landed"]["scanOpen"] is True


# --- 5. no signal -----------------------------------------------------------------


_OFFLINE = STUB + """
window.PomonaGroceryOffline = require(""" + json.dumps(str(OFFLINE)) + """);
var DEAD = false;
fetch = function (url, opts) {
  if (DEAD) return Promise.reject(new TypeError('Failed to fetch'));
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  var body = url === '/api/grocery-list/add' ? { item_id: 90, item: 'cilantro', merged: false } : {};
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
"""


def _offline(body: str):
    res = nodeharness.run_node(_OFFLINE + grocery_block() + CLICK + FIXTURE + _LIST + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@needs_node
def test_an_add_with_no_signal_shows_at_once_queues_and_is_sent_when_signal_returns():
    out = _offline("""
twoShops();
groOffline.setHousehold(1);
DEAD = true;
navigator.onLine = false;
groceryState.addSheet = { typed: 'cilantro', store: 'Costco', picked: true };
groAddItem().then(function () {
  var html = groListHtml(groceryState.data);
  var costco = html.slice(html.indexOf('data-store="Costco"'), html.indexOf('data-store="Loblaws"'));
  var offline = {
    onCard: costco.indexOf('cilantro') !== -1,
    queued: groOffline.pending().map(function (op) { return [op.kind, op.item, op.store]; }),
    posts: POSTS.length, toast: lastToast(), offlineFlag: groceryState.offline
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
    assert out["offline"]["onCard"] is True, "on the screen at once, under its store"
    assert out["offline"]["queued"] == [["add", "cilantro", "Costco"]]
    assert out["offline"]["posts"] == 0
    assert out["offline"]["toast"]["msg"] == "cilantro was added" and out["offline"]["toast"]["action"] == "Undo"
    assert out["offline"]["offlineFlag"] is True
    assert out["sent"] == [["/api/grocery-list/add", {
        "item": "cilantro", "quantity": "", "category": "other", "store": "Costco", "remember": True}]]
    assert out["left"] == 0


@needs_node
def test_undo_on_a_queued_add_takes_it_out_of_the_queue_and_off_the_screen():
    out = _offline("""
twoShops();
groOffline.setHousehold(1);
DEAD = true;
navigator.onLine = false;
groceryState.addSheet = { typed: 'cilantro', store: 'Costco', picked: true };
groAddItem().then(function () {
  var before = groOffline.pending().length;
  tapUndo();
  var html = groListHtml(groceryState.data);
  console.log(JSON.stringify({ before: before, after: groOffline.pending().length, onScreen: html.indexOf('cilantro') !== -1 }));
  process.exit(0); // the replay timer would keep node up otherwise
});
""")
    assert out == {"before": 1, "after": 0, "onScreen": False}


@needs_node
def test_a_queued_line_cannot_be_ticked_until_it_has_landed():
    out = _offline("""
twoShops();
groOffline.setHousehold(1);
DEAD = true;
navigator.onLine = false;
groceryState.addSheet = { typed: 'cilantro', store: 'Costco', picked: true };
groAddItem().then(function () {
  var id = groOffline.pending()[0].id;
  clickIfRendered({ gro: 'line-tick', id: id, bought: '0' });
  console.log(JSON.stringify({ toast: lastToast().msg, queued: groOffline.pending().map(function (op) { return op.kind; }) }));
  process.exit(0); // the replay timer would keep node up otherwise
});
""")
    assert out["toast"] == "That one’s still on its way — tick it once we’re back."
    assert out["queued"] == ["add"], "no status op for a row the server has not seen"


@needs_node
def test_the_queue_module_replays_an_add_behind_the_ticks_and_survives_a_reload():
    out = _offline("""
groOffline.setHousehold(1);
groOffline.queueStatus('3', 'purchased');
var op = groOffline.queueAdd({ item: 'cilantro', quantity: '', category: 'produce', store: '' });
var again = window.PomonaGroceryOffline.create({ storage: window.localStorage, householdId: 1 });
var shape = again.applyPending({ stores: { Costco: { sections: [{ section: 'pantry', items: [
  { id: 3, item: 'Orzo', quantity: '1 box', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' }] }], purchased: [], inCart: [] } } });
var sent = [];
var pendingBefore = again.pending().map(function (o) { return o.kind || 'status'; });
again.replay(function (url, body) { sent.push([url, body]); return Promise.resolve({ ok: true, status: 200 }); }).then(function (r) {
  console.log(JSON.stringify({ pending: pendingBefore,
    loose: (shape.stores.Unassigned.sections[0].items || []).map(function (it) { return [it.item, it.store_decided, it.pending]; }),
    bought: shape.stores.Costco.purchased.map(function (it) { return it.item; }),
    sent: sent, result: r, isAdd: again.isPendingAddId(op.id) }));
});
""")
    assert out["pending"] == ["status", "add"], "the add waits behind the tick, across a reload"
    assert out["loose"] == [["cilantro", 1, True]], "Anywhere, decided, drawn as pending"
    assert out["bought"] == ["Orzo"]
    assert out["sent"] == [
        ["/api/grocery-list/3/status", {"status": "purchased"}],
        ["/api/grocery-list/add", {"item": "cilantro", "quantity": "", "category": "produce", "store": "", "remember": True}],
    ]
    assert out["result"] == {"sent": 2, "dropped": 0, "kept": 0}
    assert out["isAdd"] is True


# --- 6. the route --------------------------------------------------------------


def _by_store() -> dict[str, list[str]]:
    data = tools.get_grocery_list_by_store()
    return {s["store"]: [it["item"] for sec in s["sections"] for it in sec["items"]] for s in data["stores"]}


def test_the_add_route_puts_the_line_in_the_store_and_remembers_it(signed_in):
    res = signed_in.post("/api/grocery-list/add", json={"item": "Cilantro", "quantity": "2 bunches", "store": "Costco", "remember": True})
    assert res.status_code == 200
    body = res.json()
    assert body["store"] == "Costco" and body["remembered"] is True and body["merged"] is False
    assert _by_store().get("Costco") == ["Cilantro"]
    assert tools.get_item_store_preferences().get("cilantro") == "Costco"
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert rows[body["item_id"]]["store_decided"] == 1


def test_the_add_route_with_anywhere_puts_it_under_no_store_and_remembers_nothing(signed_in):
    tools.set_item_store("cilantro", "Loblaws")
    res = signed_in.post("/api/grocery-list/add", json={"item": "Cilantro", "store": ""})
    body = res.json()
    assert body["store"] == "" and body["remembered"] is False
    assert _by_store().get("Unassigned") == ["Cilantro"], "Anywhere, as picked, even with a usual store"
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert rows[body["item_id"]]["store_decided"] == 1, "decided: not on 'Sort them all'"
    assert tools.get_item_store_preferences().get("cilantro") == "Loblaws", "the usual is not forgotten"


def test_the_add_route_without_a_store_applies_the_usual_as_before(signed_in):
    tools.set_item_store("cilantro", "Loblaws")
    res = signed_in.post("/api/grocery-list/add", json={"item": "Cilantro"})
    assert "store" not in res.json() and "remembered" not in res.json()
    assert _by_store().get("Loblaws") == ["Cilantro"]


def test_an_add_that_merges_still_moves_the_line_to_the_picked_store(signed_in):
    first = signed_in.post("/api/grocery-list/add", json={"item": "Orzo", "quantity": "1 box", "store": "Costco"}).json()
    res = signed_in.post("/api/grocery-list/add", json={"item": "orzo", "quantity": "1 box", "store": "Loblaws"})
    body = res.json()
    assert body["merged"] is True and body["item_id"] == first["item_id"]
    assert _by_store().get("Loblaws") == ["Orzo"] and not _by_store().get("Costco")
    assert tools.get_item_store_preferences().get("orzo") == "Loblaws"
