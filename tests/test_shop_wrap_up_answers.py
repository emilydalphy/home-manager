"""
"How did it go?": "Will grab elsewhere" with a store picker, "Don't need
anymore", and an "Add a new store" that comes back to where you were.

Emily, 2026-09-13: "How did it go screen - 'somewhere else' make it 'will
grab elsewhere' and then if they select that show the drop down of the
other stores. and have an option for them if they want to add a new store
(also in the sorting screen in case) where it brings them to the settings
to add in a store, but then make sure the flow brings them back to this
screen. Also add a 'don't need anymore' option."

What the wrap-up did before, read off the code: "Somewhere else" EXCLUDED
the row on the spot (status 'excluded' — off every list, listed under
"Getting elsewhere" on the confirmation card), and the only way to a new
store was the Kitchen tab's What we know sheet, which a chat href reaches
by switching tabs (followActionHref) — the wrap-up gone with it.

Now:
  * "Will grab elsewhere" opens the household's OTHER stores under the
    row; tapping one moves the line to that store's list for this week
    only (remember: false — next week's list still puts it where it was);
  * "Don't need anymore" is the pre-shop drop ("Have it"'s route): off
    the list for the week, undone from the toast, never bought, never
    inventory;
  * "Add a new store" — on the wrap-up's picker, the SORT queue and SORT
    ALL — opens the Stores section of What we know as a sheet over the
    screen, and a store saved there closes the sheet and becomes the
    answer on the row it was opened from. Closing the sheet without
    saving just returns.

Behaviour runs under node against shell.js's own functions (the
tests/test_shop_trip_exit.py harness); the route change is exercised
through the app; the copy and the sheet wiring are read off the source.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest
from test_shop_trip_exit import _STUB, _grocery_block

from app import tools

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)

# The wrap-up talks to the sheet (What we know) and the sheet talks back;
# both sides of that are outside the grocery region, so they are stubbed
# and their calls recorded. groIsBuilt/renderGrocery need a panel the
# harness does not have — replaced after the region loads, which a
# function declaration allows.
_SHEET = """
const SHEET = [];
function openKitchenSheet(key, section) { SHEET.push('open:' + key); }
function closeKitchenSheet() { SHEET.push('close'); }
function wwkFocusAdd(kind) { SHEET.push('focus:' + kind); }
var prefsState = { memory: null };
var RENDERS = 0;
groIsBuilt = function () { return true; };
renderGrocery = function () { RENDERS += 1; };
function posts(url) { return POSTS.filter(function (p) { return p.url.indexOf(url) !== -1; }); }
function wrapWith(extraStores) {
  twoShops();
  groceryState.usualStores = ['Costco', 'Metro'].concat(extraStores || []);
  groceryState.tripStops = ['Costco', 'Metro'];
  groceryState.tripDone = { Costco: true, Metro: true };
  groceryState.step = 'wrap';
}
"""


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + _SHEET + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the three answers -------------------------------------------------


@_needs_node
def test_every_unbought_thing_gets_three_answers_and_the_old_one_is_gone():
    out = _node("""
wrapWith();
console.log(JSON.stringify(groWrapHtml(groceryState.data)));
""")
    assert out.count('data-gro="wrap-keep"') == 3
    assert out.count('>Will grab elsewhere</button>') == 3
    assert out.count('>Don’t need anymore</button>') == 3
    assert "Somewhere else" not in out
    assert "wrap-move" not in out, "the stores only appear once a row asks for them"
    assert "gro-primary" not in out, "the answers are quiet; the dock has the one fill"


@_needs_node
def test_will_grab_elsewhere_opens_the_other_stores_under_that_row_only():
    """Rice is at Costco: the picker offers Metro and Farm Boy, not Costco
    (the store it wasn't found at), and only under Rice."""
    out = _node("""
wrapWith(['Farm Boy']);
click({ gro: 'wrap-else', id: '1' });
const html = groWrapHtml(groceryState.data);
console.log(JSON.stringify({ html: html, open: groceryState.wrapElseId, posts: POSTS.length }));
""")
    assert out["open"] == "1"
    assert out["posts"] == 0, "opening the picker writes nothing"
    html = out["html"]
    assert html.count('class="gro-wrap-pick"') == 1
    pick = html[html.index('data-pick-for="1"'):html.index("</div>", html.index('data-pick-for="1"'))]
    assert 'data-gro="wrap-move" data-id="1" data-store="Metro" data-from="Costco"' in pick
    assert 'data-store="Farm Boy"' in pick
    assert 'data-store="Costco"' not in pick, "not the store it was just not found at"
    assert 'data-gro="store-add" data-kind="wrap" data-id="1"' in pick
    assert ">Add a new store</button>" in pick
    assert 'aria-expanded="true">Will grab elsewhere' in html
    assert "gro-wrap-btn-on" in html


@_needs_node
def test_a_second_tap_closes_the_picker_and_a_step_change_forgets_it():
    out = _node("""
wrapWith();
click({ gro: 'wrap-else', id: '1' });
click({ gro: 'wrap-else', id: '1' });
const closed = groceryState.wrapElseId;
click({ gro: 'wrap-else', id: '2' });
goGroceryStep('list');
console.log(JSON.stringify({ closed: closed, afterStep: groceryState.wrapElseId }));
""")
    assert out["closed"] is None
    assert out["afterStep"] is None


@_needs_node
def test_a_one_shop_household_is_offered_only_a_new_store():
    out = _node("""
wrapWith();
groceryState.usualStores = ['Costco'];
delete groceryState.data.stores.Metro;
click({ gro: 'wrap-else', id: '1' });
console.log(JSON.stringify(groWrapHtml(groceryState.data)));
""")
    assert "wrap-move" not in out, "no other store to offer"
    assert 'data-gro="store-add" data-kind="wrap"' in out


@_needs_node
def test_picking_a_store_moves_the_line_for_this_week_only_and_undoes():
    out = _node("""
wrapWith();
click({ gro: 'wrap-else', id: '1' });
click({ gro: 'wrap-move', id: '1', store: 'Metro', from: 'Costco', name: 'Rice' });
settle(function () {
  const first = posts('/api/grocery-list/1/store');
  const toast = TOASTS[TOASTS.length - 1];
  toast.action.onClick();
  settle(function () {
    console.log(JSON.stringify({
      moves: posts('/api/grocery-list/1/store').map(function (p) { return p.body; }),
      toast: toast.msg, undoLabel: toast.action.label,
      excluded: posts('/exclude').length, pickerOpen: groceryState.wrapElseId
    }));
  });
});
""")
    assert out["moves"][0] == {"store": "Metro", "remember": False}, "this week's answer, not a new preference"
    assert out["moves"][1] == {"store": "Costco", "remember": False}, "undo puts it back the same way"
    assert out["toast"] == "Rice moved to Metro"
    assert out["undoLabel"] == "Undo"
    assert out["excluded"] == 0, "nothing is excluded any more"
    assert out["pickerOpen"] is None


@_needs_node
def test_a_moved_line_reads_as_answered_and_undo_asks_again():
    """The line is still needed (it is on Metro's list now), so without
    this the wrap-up asked about it a second time."""
    out = _node("""
wrapWith();
click({ gro: 'wrap-move', id: '1', store: 'Metro', from: 'Costco', name: 'Rice' });
settle(function () {
  const answered = groWrapHtml(groceryState.data);
  const saved = JSON.parse(STORE.get('pomona.trip.h1')).moved;
  TOASTS[TOASTS.length - 1].action.onClick();
  settle(function () {
    console.log(JSON.stringify({ answered: answered, saved: saved, again: groWrapHtml(groceryState.data) }));
  });
});
""")
    row = out["answered"][out["answered"].index("Rice"):out["answered"].index("Oats")]
    assert '<span class="gro-wrap-kept">Grabbing at Metro</span>' in row
    assert "wrap-keep" not in row and "wrap-drop" not in row, "answered rows are not asked again"
    assert out["saved"] == {"1": "Metro"}, "and a relaunch mid wrap-up remembers the answer"
    again = out["again"][out["again"].index("Rice"):out["again"].index("Oats")]
    assert "Grabbing at" not in again and "wrap-keep" in again


@_needs_node
def test_dont_need_anymore_is_the_pre_shop_drop_with_an_undo():
    """Not bought, not inventory, not excluded: the same soft-remove
    "Have it" uses, so it lands on the confirmation card with the same
    undo."""
    out = _node("""
wrapWith();
click({ gro: 'wrap-drop', id: '3', name: 'Eggs' });
settle(function () {
  const toast = TOASTS[TOASTS.length - 1];
  toast.action.onClick();
  settle(function () {
    console.log(JSON.stringify({
      drop: posts('/api/grocery-list/3/pre-shop').map(function (p) { return p.url + ' ' + JSON.stringify(p.body); }),
      toast: toast.msg, undoLabel: toast.action.label,
      purchased: purchases().length, excluded: posts('/exclude').length,
      inventory: posts('/already-have').length, removed: posts('/remove').length
    }));
  });
});
""")
    assert out["drop"] == [
        '/api/grocery-list/3/pre-shop {"decision":"drop","author":"user"}',
        "/api/grocery-list/3/pre-shop-undo {}",
    ]
    assert out["toast"] == "Eggs off the list for this week"
    assert out["undoLabel"] == "Undo"
    assert out["purchased"] == 0 and out["excluded"] == 0 and out["inventory"] == 0 and out["removed"] == 0


# --- 2. "Add a new store", and the way back --------------------------------


@_needs_node
def test_add_a_new_store_opens_the_stores_sheet_over_this_screen_and_arms_the_way_back():
    out = _node("""
wrapWith();
click({ gro: 'wrap-else', id: '1' });
click({ gro: 'store-add', kind: 'wrap', id: '1', name: 'Rice', from: 'Costco' });
console.log(JSON.stringify({ sheet: SHEET, back: groceryState.storeReturn, step: groceryState.step, tabs: TAB_SWITCHES }));
""")
    assert out["sheet"] == ["open:stores", "focus:store"], "the sheet, opened on the add field"
    assert out["back"] == {"step": "wrap", "kind": "wrap", "id": "1", "name": "Rice", "from": "Costco"}
    assert out["step"] == "wrap", "still on How did it go?"
    assert out["tabs"] == [], "never the Kitchen tab"


@_needs_node
def test_a_store_saved_in_the_sheet_comes_back_as_the_wrap_ups_answer():
    out = _node("""
wrapWith();
click({ gro: 'wrap-else', id: '1' });
click({ gro: 'store-add', kind: 'wrap', id: '1', name: 'Rice', from: 'Costco' });
groUsualStoreAdded('Farm Boy');
settle(function () {
  console.log(JSON.stringify({
    sheet: SHEET, stores: groceryState.usualStores, back: groceryState.storeReturn, step: groceryState.step,
    moves: posts('/api/grocery-list/1/store').map(function (p) { return p.body; }), toast: lastToast()
  }));
});
""")
    assert out["sheet"][-1] == "close", "the sheet closes itself once the store is saved"
    assert out["stores"] == ["Costco", "Metro", "Farm Boy"], "the list's own copy of the shops learns it"
    assert out["moves"] == [{"store": "Farm Boy", "remember": False}], "and the line moves to it"
    assert out["toast"] == "Rice moved to Farm Boy"
    assert out["back"] is None
    assert out["step"] == "wrap"


@_needs_node
def test_a_store_saved_in_the_sheet_answers_the_sort_queue():
    out = _node("""
twoShops();
groceryState.data.stores.Unassigned.sections = [{ section: 'other', items: [{ id: 9, item: 'Tahini', store: '' }] }];
groceryState.step = 'sort';
click({ gro: 'store-add', kind: 'sort', id: '9', name: 'Tahini', from: '' });
groUsualStoreAdded('Farm Boy');
settle(function () {
  console.log(JSON.stringify({ sheet: SHEET, assigns: posts('/api/grocery-list/9/store').map(function (p) { return p.body; }) }));
});
""")
    assert out["sheet"] == ["open:stores", "focus:store", "close"]
    assert out["assigns"] == [{"store": "Farm Boy"}], "the queue's own answer — remembered like any pill"


@_needs_node
def test_a_store_saved_in_the_sheet_is_the_answer_on_sort_all_and_writes_at_once():
    """Since 2026-09-13 SORT ALL writes each answer as it is given (the row
    leaves the screen), so a store arriving from the sheet is written the
    same way a tapped chip is — this week only, like the rest of that
    screen (remember: false)."""
    out = _node("""
twoShops();
groceryState.data.stores.Unassigned.sections = [{ section: 'other', items: [{ id: 9, item: 'Tahini', store: '' }] }];
groceryState.step = 'sortall';
click({ gro: 'store-add', kind: 'sortall', id: '9', name: 'Tahini', from: '' });
groUsualStoreAdded('Farm Boy');
settle(function () {
  console.log(JSON.stringify({ sheet: SHEET, assigns: posts('/api/grocery-list/9/store').map(function (p) { return p.body; }), renders: RENDERS }));
});
""")
    assert out["sheet"][-1] == "close"
    assert out["assigns"] == [{"store": "Farm Boy", "remember": False}], "written at once, like a tapped chip"
    assert out["renders"] >= 1


@_needs_node
def test_a_store_added_from_the_gear_with_nothing_armed_just_refreshes_the_pills():
    out = _node("""
wrapWith();
groUsualStoreAdded('Farm Boy');
console.log(JSON.stringify({ sheet: SHEET, stores: groceryState.usualStores, posts: POSTS.length, renders: RENDERS }));
""")
    assert out["sheet"] == [], "no return armed: the sheet stays open"
    assert out["stores"] == ["Costco", "Metro", "Farm Boy"]
    assert out["posts"] == 0
    assert out["renders"] == 1


@_needs_node
def test_closing_the_sheet_without_saving_forgets_the_way_back_and_keeps_the_screen():
    out = _node("""
wrapWith();
click({ gro: 'wrap-else', id: '1' });
click({ gro: 'store-add', kind: 'wrap', id: '1', name: 'Rice', from: 'Costco' });
prefsState.memory = { usual_stores: ['Costco', 'Metro', 'Farm Boy'] };
groStoresSheetClosed();
console.log(JSON.stringify({ back: groceryState.storeReturn, step: groceryState.step, stores: groceryState.usualStores, posts: POSTS.length }));
""")
    assert out["back"] is None
    assert out["step"] == "wrap"
    assert out["stores"] == ["Costco", "Metro", "Farm Boy"], "a shop saved in the sheet by hand is still picked up"
    assert out["posts"] == 0


@_needs_node
def test_the_sheets_cached_read_never_removes_a_shop_the_list_knows():
    out = _node("""
wrapWith(['Farm Boy']);
prefsState.memory = { usual_stores: ['Costco'] };
groStoresSheetClosed();
console.log(JSON.stringify(groceryState.usualStores));
""")
    assert out == ["Costco", "Metro", "Farm Boy"]


@_needs_node
def test_both_sorting_screens_offer_a_new_store():
    out = _node("""
twoShops();
groceryState.data.stores.Unassigned.sections = [{ section: 'other', items: [{ id: 9, item: 'Tahini', store: '' }] }];
console.log(JSON.stringify({ sort: groSortHtml(groceryState.data), all: groSortAllHtml(groceryState.data) }));
""")
    assert 'data-gro="store-add" data-kind="sort" data-id="9"' in out["sort"]
    assert 'data-gro="store-add" data-kind="sortall" data-id="9"' in out["all"]
    for html in out.values():
        assert html.count(">Add a new store</button>") == 1


# --- 3. the route: remember is the caller's call -----------------------------


def test_the_store_route_can_move_a_line_without_touching_the_remembered_store(signed_in):
    tools.edit_preference("usual_stores", ["Costco", "Metro"])
    item_id = tools.add_grocery_item("eggs", "12", "dairy")["item_id"]
    tools.set_item_store("eggs", "Costco")
    res = signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Metro", "remember": False})
    assert res.status_code == 200
    body = res.json()
    assert body["remembered"] is False and body["needs_confirmation"] is False
    assert tools.get_item_store_preferences()["eggs"] == "Costco", "next week the eggs still go to Costco"
    at = {s["store"]: [it["item"] for sec in s["sections"] for it in sec["items"]] for s in tools.get_grocery_list_by_store()["stores"]}
    assert at.get("Metro") == ["eggs"], "but this week's line is at Metro"


def test_the_store_route_still_remembers_by_default(signed_in):
    tools.edit_preference("usual_stores", ["Costco", "Metro"])
    item_id = tools.add_grocery_item("eggs", "12", "dairy")["item_id"]
    tools.set_item_store("eggs", "Costco")
    res = signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Metro"})
    assert res.status_code == 200 and res.json()["remembered"] is True
    assert tools.get_item_store_preferences()["eggs"] == "Metro", "the LIST row's pills correct the preference, as before"


# --- 4. the wiring and the words, read off the source ---------------------------


def test_the_sheet_hands_a_saved_store_back_and_says_when_it_closes():
    add = SHELL_JS[SHELL_JS.index("function wwkAddStore("):SHELL_JS.index("function wwkStoreItems(")]
    assert "groUsualStoreAdded(name)" in add
    close = SHELL_JS[SHELL_JS.index("function closeKitchenSheet("):SHELL_JS.index("if (kitSheetScrim) {")]
    assert "groStoresSheetClosed();" in close
    assert "wwkState.addNext = null;" in close
    assert "function wwkFocusAdd(" in SHELL_JS
    assert "wwkTryFocusAdd();" in SHELL_JS[SHELL_JS.index("function renderWhatWeKnow("):SHELL_JS.index("function wwkSectionInnerHtml(")]


def test_the_way_back_is_page_view_state_not_a_url():
    """The nav-v2 rules: a sheet over the step, one URL throughout."""
    assert "storeReturn" in SHELL_JS
    region = _grocery_block()
    assert "activateTab('kitchen'" not in region[region.index("function groOpenStoresSheet("):region.index("function groUsualStoreAdded(")]
    assert "location" not in region[region.index("function groOpenStoresSheet("):region.index("function groStoresSheetClosed(")]


def test_the_confirmation_card_covers_both_kinds_of_not_needed():
    assert "Not needed this week: " in SHELL_JS
    assert "You said you already have" not in SHELL_JS


def test_the_new_controls_use_tokens_and_the_tap_size():
    block = SHELL_CSS[SHELL_CSS.index("/* ---------- WRAP UP ----------"):SHELL_CSS.index(".gro-wrap-summary {")]
    for line in block.splitlines():
        assert "#" not in line.split("/*")[0], f"literal colour: {line.strip()}"
    assert "flex-wrap: wrap" in block[block.index(".gro-wrap-seg"):], "three answers wrap rather than overflow at 375px"
    on = block[block.index(".gro-wrap-btn-on {"):block.index("}", block.index(".gro-wrap-btn-on {"))]
    assert "var(--celadon-tint)" in on and "var(--ink-strong)" in on
    add = SHELL_CSS[SHELL_CSS.index(".gro-pill-add {"):SHELL_CSS.index("}", SHELL_CSS.index(".gro-pill-add {"))]
    assert "var(--apricot-label)" in add and "var(--apricot)" not in add, "a link, not a second apricot fill"
