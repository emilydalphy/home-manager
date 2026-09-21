"""
Shop: "Getting it elsewhere" says so, undoes, and stays in sight.

Loop Board, 2026-09-15 (HIGH · Bug): "Somewhere else" quietly removed an
item from the list — no undo, nowhere to see it. A thumb-slip in the aisle
could make the week's chicken disappear: the chip posted /exclude, groDo
re-read the list, and the row was gone with no word; the only place it
showed again was WRAP UP's "Already sorted this week".

Now:
  * the chip reads "Getting it elsewhere" (GRO_ELSEWHERE_CHIP) in both
    places it exists — SORT's card and the LIST row's ⋯ — and keeps its
    sand fill, apart from "Add a new store"'s link-on-nothing;
  * a tap toasts "Whole chicken set aside — getting it elsewhere." with an
    Undo that posts /include (groSetAside / groPutBack), the shape Remove's
    undo already had;
  * LIST carries a "Getting elsewhere · N" foot section (groElsewhereHtml)
    with "Put it back" on each row — the same /include, one tap;
  * the header count and Now's "Shop for tonight · N items" both read
    needed rows with excluded_from_list = 0, so they agree before and
    after, and after the undo.

Behaviour runs under node against shell.js's own functions (the
tests/test_shop_trip_exit.py harness); the routes are exercised through
the app; the copy and the CSS are read off the source.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, time
from pathlib import Path

import nodeharness
from shop_harness import CLICK as _CLICK, SHELL_CSS, SHELL_JS, STUB as _STUB, grocery_block as _grocery_block, needs_node

from app import tools
from app.tools import moves as _moves

_needs_node = needs_node

# groIsBuilt/renderGrocery need a panel the harness does not have — replaced
# after the region loads, which a function declaration allows. loadGrocery
# itself returns early without a panel, so a groDo is just its write.
_PATCH = """
var RENDERS = 0;
groIsBuilt = function () { return true; };
renderGrocery = function () { RENDERS += 1; };
function posts(url) { return POSTS.filter(function (p) { return p.url.indexOf(url) !== -1; }).map(function (p) { return p.url; }); }
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
function listWith(aside) {
  twoShops();
  groceryState.alreadyHaveSummary = { already_have: [], elsewhere: aside || [] };
}
"""


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + _CLICK + _PATCH + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the chip ---------------------------------------------------------------


@_needs_node
def test_the_chip_says_what_it_does_and_the_old_words_are_gone():
    """On the row's ⋯ — the one place it lives since the sort queue went
    (2026-09-18); SORT ALL's rows have Have it / Use something else and
    the shops, not this."""
    out = _node("""
twoShops();
groceryState.openRowId = '1';
groceryState.data.stores.Unassigned.sections = [{ section: 'other', items: [
  { id: 7, item: 'Whole chicken', quantity: '1', store: '', store_decided: 0, status: 'needed' }] }];
console.log(JSON.stringify({
  row: groRowMenuHtml(groceryState.data.stores.Costco.sections[0].items[0], groceryState.data),
  sortall: groSortAllHtml(groceryState.data)
}));
""")
    html = out["row"]
    assert html.count(">Getting it elsewhere</button>") == 1
    assert "Somewhere else" not in html
    assert 'class="gro-pill gro-pill-else"' in html
    assert 'data-gro="row-exclude" data-id="1" data-name="Rice"' in html
    assert 'aria-label="Getting Rice elsewhere"' in html
    assert "triage-exclude" not in out["sortall"] and "triage-exclude" not in SHELL_JS


def test_the_chip_and_the_foot_wording_are_one_line_changes():
    """The card leaves the words to Emily — they sit together as constants."""
    assert "var GRO_ELSEWHERE_CHIP = 'Getting it elsewhere';" in SHELL_JS
    assert "var GRO_ELSEWHERE_SECTION = 'Getting elsewhere';" in SHELL_JS
    assert "var GRO_ELSEWHERE_BACK = 'Put it back';" in SHELL_JS
    # And nothing renders the chip's words directly.
    assert SHELL_JS.count(">Getting it elsewhere<") == 0
    assert SHELL_JS.count("escapeHtml(GRO_ELSEWHERE_CHIP)") == 1, "one renderer"


def test_the_chip_is_visually_apart_from_add_a_new_store():
    """Sand fill with secondary ink vs. no fill with link ink: two different
    kinds of thing on the same card. Neither is apricot (Rule 5)."""
    else_rule = SHELL_CSS.split(".gro-pill-else {", 1)[1][:120]
    add_rule = SHELL_CSS.split(".gro-pill-add {", 1)[1][:120]
    assert "var(--sand)" in else_rule and "var(--ink-secondary)" in else_rule
    assert "background: none" in add_rule and "var(--apricot-label)" in add_rule
    assert "var(--apricot)" not in else_rule


# --- 2. the toast and its undo -------------------------------------------------


@_needs_node
def test_a_row_set_aside_says_so_with_an_undo_that_puts_it_back():
    out = _node("""
listWith();
groceryState.openRowId = '1';
clickIfRendered({ gro: 'row-exclude', id: '1', name: 'Rice' });
settle(function () {
  const toast = TOASTS[TOASTS.length - 1];
  const before = { msg: toast.msg, label: toast.action && toast.action.label, hold: toast.hold, open: groceryState.openRowId };
  toast.action.onClick();
  settle(function () {
    console.log(JSON.stringify({
      before: before, posts: posts('/api/grocery-list/1/'), after: lastToast().msg, removed: posts('/remove').length
    }));
  });
});
""")
    assert out["before"]["msg"] == "Rice set aside — getting it elsewhere."
    assert out["before"]["label"] == "Undo"
    assert out["before"]["hold"] == 8000, "the tab's own undo window, same as Remove's"
    assert out["before"]["open"] is None, "the ⋯ closes"
    assert out["posts"] == ["/api/grocery-list/1/exclude", "/api/grocery-list/1/include"]
    assert out["after"] == "Put back."
    assert out["removed"] == 0, "set aside is never a delete"


@_needs_node
def test_a_failed_set_aside_says_so_and_offers_no_undo():
    out = _node("""
listWith();
// The chip lives inside the row's quiet ⋯, so the ⋯ has to be open for
// there to be a chip to tap — the same line its sibling above carries.
// (Added 2026-09-21: the tap used to go straight into the handler over a
// closed menu, which clickIfRendered now refuses.)
groceryState.openRowId = '1';
fetch = function (url, opts) {
  POSTS.push({ url: url, body: {} });
  return Promise.resolve({ ok: false, status: 500, json: function () { return Promise.resolve({}); } });
};
clickIfRendered({ gro: 'row-exclude', id: '1', name: 'Rice' });
settle(function () {
  const toast = TOASTS[TOASTS.length - 1];
  console.log(JSON.stringify({ msg: toast.msg, action: toast.action || null }));
});
""")
    assert out["msg"] == "Couldn't set that aside — try again."
    assert out["action"] is None


# --- 3. LIST's foot section ------------------------------------------------------


@_needs_node
def test_list_shows_what_is_set_aside_in_a_quiet_foot_with_one_tap_back():
    out = _node("""
listWith([{ id: 7, item: 'Whole chicken', quantity: '1', category: 'meat' }, { id: 8, item: 'Sourdough', quantity: '', category: 'bakery' }]);
const html = groListHtml(groceryState.data);
clickIfRendered({ gro: 'elsewhere-back', id: '7', name: 'Whole chicken' });
settle(function () {
  console.log(JSON.stringify({ html: html, posts: posts('/api/grocery-list/7/'), toast: lastToast().msg }));
});
""")
    html = out["html"]
    foot = html[html.index('class="gro-staples gro-elsewhere"'):]
    assert "Getting elsewhere &middot; 2" in foot
    assert foot.count('data-gro="elsewhere-back"') == 2
    assert 'data-gro="elsewhere-back" data-id="7" data-name="Whole chicken"' in foot
    assert foot.count(">Put it back</button>") == 2
    assert 'aria-label="Put Whole chicken back on the list"' in foot
    assert '<span class="gro-qty">1</span>' in foot
    assert "flag-toggle" not in foot and "staples-toggle" not in foot, "not a fold — the rows are in view"
    # Below the store cards.
    assert html.index("gro-elsewhere") > html.index('data-store="Costco"')
    assert out["posts"] == ["/api/grocery-list/7/include"]
    assert out["toast"] == "Put back."


@_needs_node
def test_the_foot_is_absent_when_nothing_is_aside_and_present_on_an_empty_list():
    out = _node("""
listWith();
const none = groListHtml(groceryState.data);
groceryState.alreadyHaveSummary = { already_have: [], elsewhere: [{ id: 7, item: 'Whole chicken', quantity: '1' }] };
groceryState.data = { stores: { Unassigned: { sections: [], purchased: [], inCart: [] } } };
groceryState.spices = { items: [], recently_bought: [] };
const empty = groListHtml(groceryState.data);
console.log(JSON.stringify({ none: none, empty: empty }));
""")
    assert "gro-elsewhere" not in out["none"]
    assert "Nothing to buy." in out["empty"]
    assert "Getting elsewhere &middot; 1" in out["empty"], "the last thing set aside is still findable from the empty screen it left"


def test_the_foot_is_quiet_and_its_tap_is_44px():
    rule = SHELL_CSS.split(".gro-elsewhere-row {", 1)[1][:400]
    assert "min-height: 52px" in rule
    assert "--apricot" not in SHELL_CSS.split("/* \"Getting elsewhere · 1\"", 1)[1][:1200]
    act = SHELL_CSS.split(".gro-staple-act {", 1)[1][:200]
    assert "min-height: 44px" in act and "min-width: 44px" in act


def test_the_foot_is_the_one_way_back():
    """The wrap-up's "Actually, get it here" went with the wrap-up
    (2026-09-18); the foot's "Put it back" is the way back, on the same
    /include the toast's Undo runs."""
    assert "undo-elsewhere" not in SHELL_JS
    assert "case 'elsewhere-back':" in SHELL_JS


# --- 4. the routes and the counts -----------------------------------------------


def _needed_ids() -> list[int]:
    return [it["id"] for s in tools.get_grocery_list_by_store()["stores"] for sec in s["sections"] for it in sec["items"]]


def test_include_restores_a_set_aside_row_to_needed(signed_in):
    tools.edit_preference("usual_stores", ["Costco", "Metro"])
    chicken = tools.add_grocery_item("whole chicken", "1", "meat")["item_id"]
    tools.set_item_store("whole chicken", "Costco")
    rice = tools.add_grocery_item("rice", "1 bag", "pantry")["item_id"]

    res = signed_in.post(f"/api/grocery-list/{chicken}/exclude")
    assert res.status_code == 200 and res.json()["excluded_from_list"] is True
    assert _needed_ids() == [rice]
    aside = signed_in.get("/api/grocery-list/already-have-summary").json()["elsewhere"]
    assert [it["id"] for it in aside] == [chicken], "what LIST's foot section reads"
    assert aside[0]["item"] == "whole chicken" and aside[0]["quantity"] == "1"

    res = signed_in.post(f"/api/grocery-list/{chicken}/include")
    assert res.status_code == 200 and res.json()["excluded_from_list"] is False
    row = next(it for it in tools.list_grocery_list(status="all") if it["id"] == chicken)
    assert row["status"] == "needed" and row["excluded_from_list"] == 0
    assert row["store"] == "Costco" and row["quantity"] == "1", "back exactly as it was"
    assert sorted(_needed_ids()) == sorted([chicken, rice])
    assert signed_in.get("/api/grocery-list/already-have-summary").json()["elsewhere"] == []


def test_include_of_an_unknown_row_is_a_404_not_a_crash(signed_in):
    assert signed_in.post("/api/grocery-list/999999/include").status_code == 404


def test_the_header_count_and_nows_shop_move_agree_through_set_aside_and_back(signed_in):
    """The band's "N things" is the by-store needed count; Now's "Shop for
    tonight · N items" is moves._shop_move's len(list_grocery_list('needed')).
    Both leave a set-aside row out and both take it back."""
    chicken = tools.add_grocery_item("whole chicken", "1", "meat")["item_id"]
    tools.add_grocery_item("rice", "1 bag", "pantry")
    today = date(2026, 9, 15)
    now = datetime.combine(today, time(10, 0))
    # Both rows are what tonight's dinner is waiting on: since 2026-09-16
    # the shop move only names a deadline for a cook the list is actually
    # for (moves._shop_move reads the per-meal ledger). Planning the meal
    # with its ingredients merges into the two rows above rather than
    # adding new ones, so the counts under test are still 2 and then 1.
    tools.add_recipe("Roast Night", ingredients=[
        {"item": "whole chicken", "qty": "1", "category": "meat"},
        {"item": "rice", "qty": "1 bag", "category": "pantry"},
    ])
    plan_id = tools.create_weekly_plan(today.isoformat())["weekly_plan_id"]
    entry = tools.plan_meal(today.isoformat(), "Roast Night", slot="dinner",
                            weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True)
    view = {"meals": [{"date": today.isoformat(), "slot": "dinner",
                       "entry_id": entry["entry_id"]}]}

    def counts() -> tuple[int, int]:
        move = _moves._shop_move(view, today, now, time(18, 0))
        by_store = signed_in.get("/api/grocery-list/by-store?status=needed").json()["stores"]
        header = sum(len(sec["items"]) for s in by_store for sec in s["sections"])
        return header, (int(move[0]["detail"].split(" item")[0]) if move else 0)

    assert counts() == (2, 2)
    signed_in.post(f"/api/grocery-list/{chicken}/exclude")
    assert counts() == (1, 1)
    signed_in.post(f"/api/grocery-list/{chicken}/include")
    assert counts() == (2, 2)
