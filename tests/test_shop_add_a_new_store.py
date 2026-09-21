"""
"Add a new store" from Shop's sort screen, and the way back.

Emily, 2026-09-13: "have an option for them if they want to add a new
store (also in the sorting screen in case) where it brings them to the
settings to add in a store, but then make sure the flow brings them back
to this screen." The tap opens the Stores section of What we know as a
SHEET over the sort screen (never a trip to another tab), arms
groceryState.storeReturn, and a store saved there comes back as that
row's answer.

This file was tests/test_shop_wrap_up_answers.py until 2026-09-18: the
wrap-up ("How did it go?") and its three answers went with the trip
screens — the list is the checklist now, and a thing not bought simply
stays on it — and what survived is the half about the sheet. The store
route's `remember` flag stays too: SORT ALL writes this week only.

Behaviour runs under node against shell.js's own functions
(tests/shop_harness).
"""
from __future__ import annotations

import json

import nodeharness
from app import tools
from shop_harness import CLICK, SHELL_CSS, SHELL_JS, STUB, grocery_block, needs_node

_needs_node = needs_node

# The sort screen talks to the sheet (What we know) and the sheet talks
# back; both sides of that are outside the grocery region, so they are
# stubbed and their calls recorded. groIsBuilt/renderGrocery need a panel
# the harness does not have — replaced after the region loads, which a
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
function twoShops() {
  groceryState.data = { stores: {
    Unassigned: { sections: [], purchased: [], inCart: [] },
    Costco: { sections: [{ section: 'other', items: [
      { id: 1, item: 'Rice', quantity: '1', store: 'Costco', store_decided: 1, status: 'needed' },
      { id: 2, item: 'Oats', quantity: '1', store: 'Costco', store_decided: 1, status: 'needed' }] }], purchased: [], inCart: [] },
    Metro: { sections: [{ section: 'other', items: [
      { id: 3, item: 'Eggs', quantity: '1', store: 'Metro', store_decided: 1, status: 'needed' }] }], purchased: [], inCart: [] }
  } };
  groceryState.usualStores = ['Costco', 'Metro'];
  groceryState.storesPromptDismissed = true;
  groceryState.step = 'list';
}
function onSortAll(extraStores) {
  twoShops();
  groceryState.usualStores = ['Costco', 'Metro'].concat(extraStores || []);
  groceryState.data.stores.Unassigned.sections = [{ section: 'other', items: [{ id: 9, item: 'Tahini', store: '', store_decided: 0, status: 'needed' }] }];
  groceryState.step = 'sortall';
}
"""


def _node(body: str):
    res = nodeharness.run_node(STUB + grocery_block() + CLICK + _SHEET + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the sheet, and the way back ------------------------------------------


@_needs_node
def test_add_a_new_store_opens_the_stores_sheet_over_this_screen_and_arms_the_way_back():
    out = _node("""
onSortAll();
clickIfRendered({ gro: 'store-add', kind: 'sortall', id: '9', name: 'Tahini', from: '' });
console.log(JSON.stringify({ sheet: SHEET, back: groceryState.storeReturn, step: groceryState.step, tabs: TAB_SWITCHES }));
""")
    assert out["sheet"] == ["open:stores", "focus:store"], "the Stores section, landing in the add field"
    assert out["back"] == {"step": "sortall", "kind": "sortall", "id": "9", "name": "Tahini", "from": ""}
    assert out["step"] == "sortall", "still on the sort screen underneath"
    assert out["tabs"] == [], "never a trip to another tab"


@_needs_node
def test_a_store_saved_in_the_sheet_is_the_answer_on_sort_all_and_writes_at_once():
    """SORT ALL writes each answer as it is given (the row leaves the
    screen), so a store arriving from the sheet is written the same way a
    tapped chip is — this week only, like the rest of that screen
    (remember: false)."""
    out = _node("""
onSortAll();
clickIfRendered({ gro: 'store-add', kind: 'sortall', id: '9', name: 'Tahini', from: '' });
groUsualStoreAdded('Farm Boy');
settle(function () {
  console.log(JSON.stringify({ sheet: SHEET, assigns: posts('/api/grocery-list/9/store').map(function (p) { return p.body; }), renders: RENDERS, stores: groceryState.usualStores }));
});
""")
    assert out["sheet"][-1] == "close"
    assert out["assigns"] == [{"store": "Farm Boy", "remember": False}], "written at once, like a tapped chip"
    assert out["renders"] >= 1
    assert out["stores"] == ["Costco", "Metro", "Farm Boy"], "the list's own copy of the shops learns it"


@_needs_node
def test_a_store_added_from_the_gear_with_nothing_armed_just_refreshes_the_pills():
    out = _node("""
onSortAll();
groUsualStoreAdded('Farm Boy');
console.log(JSON.stringify({ sheet: SHEET, stores: groceryState.usualStores, posts: POSTS.length, renders: RENDERS }));
""")
    assert out["sheet"] == [], "no return armed: the sheet stays open"
    assert out["stores"] == ["Costco", "Metro", "Farm Boy"]
    assert out["posts"] == 0
    assert out["renders"] == 1


@_needs_node
def test_a_return_armed_on_another_step_is_not_used():
    """The way back is to the screen it was armed on. If the person has
    moved on (the crumb, the tab bar) the store is learned and nothing is
    written to the row they left behind."""
    out = _node("""
onSortAll();
clickIfRendered({ gro: 'store-add', kind: 'sortall', id: '9', name: 'Tahini', from: '' });
groceryState.step = 'list';
groUsualStoreAdded('Farm Boy');
console.log(JSON.stringify({ back: groceryState.storeReturn, posts: POSTS.length, sheet: SHEET.slice(2) }));
""")
    assert out["back"] is None
    assert out["posts"] == 0
    assert out["sheet"] == [], "and the sheet is left to close itself"


@_needs_node
def test_closing_the_sheet_without_saving_forgets_the_way_back_and_keeps_the_screen():
    out = _node("""
onSortAll();
clickIfRendered({ gro: 'store-add', kind: 'sortall', id: '9', name: 'Tahini', from: '' });
prefsState.memory = { usual_stores: ['Costco', 'Metro', 'Farm Boy'] };
groStoresSheetClosed();
console.log(JSON.stringify({ back: groceryState.storeReturn, step: groceryState.step, stores: groceryState.usualStores, posts: POSTS.length }));
""")
    assert out["back"] is None
    assert out["step"] == "sortall"
    assert out["stores"] == ["Costco", "Metro", "Farm Boy"], "a shop saved in the sheet by hand is still picked up"
    assert out["posts"] == 0


@_needs_node
def test_the_sheets_cached_read_never_removes_a_shop_the_list_knows():
    out = _node("""
onSortAll(['Farm Boy']);
prefsState.memory = { usual_stores: ['Costco'] };
groStoresSheetClosed();
console.log(JSON.stringify(groceryState.usualStores));
""")
    assert out == ["Costco", "Metro", "Farm Boy"]


@_needs_node
def test_the_sort_screen_offers_a_new_store_once_per_row():
    out = _node("""
onSortAll();
console.log(JSON.stringify(groSortAllHtml(groceryState.data)));
""")
    assert 'data-gro="store-add" data-kind="sortall" data-id="9"' in out
    assert out.count(">Add a new store</button>") == 1


# --- 2. the route: remember is the caller's call -----------------------------


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


# --- 3. the wiring, read off the source ---------------------------------------


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
    region = grocery_block()
    assert "activateTab('kitchen'" not in region[region.index("function groOpenStoresSheet("):region.index("function groUsualStoreAdded(")]
    assert "location" not in region[region.index("function groOpenStoresSheet("):region.index("function groStoresSheetClosed(")]


def test_the_add_control_is_a_link_not_a_second_apricot():
    add = SHELL_CSS[SHELL_CSS.index(".gro-pill-add {"):SHELL_CSS.index("}", SHELL_CSS.index(".gro-pill-add {"))]
    assert "var(--apricot-label)" in add and "var(--apricot)" not in add, "a link, not a second apricot fill"


def test_the_wrap_up_is_gone():
    for gone in ("function groWrapHtml(", "function groWrapStorePickerHtml(", "function groWrapMove(",
                 'data-gro="wrap-keep"', 'data-gro="wrap-else"', 'data-gro="wrap-move"', 'data-gro="wrap-drop"',
                 "title: 'How did it go?'", ">Couldn’t find it<", ">Will grab elsewhere<"):
        assert gone not in SHELL_JS, f"{gone!r} went with the trip (2026-09-18)"
    for gone in (".gro-wrap-row", ".gro-wrap-btn", ".gro-wrap-pick", ".gro-wrap-summary", ".gro-allclear"):
        assert gone not in SHELL_CSS, f"{gone} is dead CSS now"
