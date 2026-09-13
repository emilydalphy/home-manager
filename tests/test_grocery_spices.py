"""
Spices and dried herbs: one opt-in section on the list (Emily, 2026-09-13).

    "put all the spices together under one section ... 'All the spices you
    need for the recipes this week, select the ones you want to add to the
    list to buy' ... we can assume that spices and herbs they probably
    already have some at home."

A recipe's cumin no longer lands under Pantry as a thing to buy. It waits
UNTICKED in "Spices this week" (status 'spice' — app/tools/spices.py), off
every count, merge, sort queue and trip, and a tick is what makes it an
ordinary needed line in its store. Which names count is a maintained list
in code, not a heuristic on the word "spice"; fresh herbs stay in produce.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.db import get_conn
from app.tools import spices


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _monday(offset_weeks: int = 0) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _needed() -> dict[str, str]:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list()}


def _section() -> dict[str, dict]:
    return {sp["item"]: sp for sp in tools.list_spices_this_week()["items"]}


@pytest.fixture
def curry_week() -> int:
    tools.add_recipe("Chicken curry", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "Ground cumin", "qty": "1 tsp", "category": "pantry"},
        {"item": "Smoked paprika", "qty": "2 tsp", "category": "pantry"},
        {"item": "Fresh cilantro", "qty": "1 bunch", "category": "produce"},
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        {"item": "Salt", "qty": "to taste", "category": "pantry"},
    ])
    tools.add_recipe("Tacos", ingredients=[
        {"item": "Ground cumin", "qty": "2 tsp", "category": "pantry"},
        {"item": "Tortillas", "qty": "1 pack", "category": "pantry"},
    ])
    plan_id = tools.create_weekly_plan(_monday(0))["weekly_plan_id"]
    days = tools._week_dates(_monday(0))
    tools.plan_meal(days[0], "Chicken curry", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(days[1], "Tacos", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


# ---------- the classifier ----------

@pytest.mark.parametrize("name", [
    "Cumin", "ground cumin", "Cumin seeds", "Smoked paprika", "Sweet paprika", "Turmeric",
    "Cinnamon", "Cinnamon sticks", "Nutmeg", "Cayenne", "Chili powder", "Chili flakes",
    "Red pepper flakes", "dried chilies", "Garlic powder", "Onion powder", "ground ginger",
    "Bay leaves", "Oregano", "dried oregano", "dried basil", "dried thyme", "Italian seasoning",
    "Taco seasoning", "Garam masala", "Curry powder", "Star anise", "Cardamom pods", "whole cloves",
    "Vanilla extract", "Sesame seeds", "Mustard seeds", "Fennel seeds", "Baking powder",
    "Salt", "Kosher salt", "flaky sea salt", "Black pepper", "Cracked black pepper", "Pepper",
    "Salt and pepper", "Peppercorns", "Olive oil", "Vegetable oil", "Sesame oil", "chili oil",
    # verifier, 2026-09-13: the realistic phrasings that slipped through
    "Saffron threads", "Nutmeg, grated", "Beef bouillon cube", "Vegetable stock cube",
    "Chipotle chili powder", "Crushed red pepper", "Red chili flakes", "Lemon pepper",
    "Vanilla beans", "Dill weed", "Steak spice", "Chili lime seasoning", "Five spice powder",
    "Pumpkin pie spice", "Seasoned salt", "Cumin, ground",
])
def test_spices_and_dried_herbs_count(name):
    assert spices.is_spice(name) is True


@pytest.mark.parametrize("name", [
    # fresh herbs are produce, bought each week
    "Fresh cilantro", "cilantro", "a bunch of mint", "basil", "Thai basil", "parsley", "thyme",
    "rosemary sprigs", "Dill", "Curry leaves", "Kaffir lime leaves", "Sage leaves", "fresh ginger", "Ginger",
    # produce that ends in a spice word
    "Bell pepper", "Bell peppers", "Jalapeño pepper", "Peppers", "chili", "chilies", "Red chili",
    "Thai chilies", "garlic cloves", "Garlic", "Red onion", "Green onions", "fennel", "Fennel bulb",
    # pantry, not spice rack
    "Sugar", "flour", "Cornstarch", "soy sauce", "Chicken stock", "curry paste", "Dijon mustard",
    "Chili crisp", "Butter", "Lemon", "Chipotle in adobo",
    # verifier, 2026-09-13
    "Coriander leaves", "Red pepper", "Green pepper", "Red bell pepper", "Beef", "Chicken thighs",
    "Peppermint", "Vanilla yogurt", "Pepper jack cheese", "Salted butter", "Salt cod",
    "Ginger paste", "Green chili", "Onion soup mix", "Cinnamon roll",
])
def test_everything_else_stays_where_it_is(name):
    assert spices.is_spice(name) is False


# ---------- the section ----------

def test_a_recipes_spices_wait_unticked_and_off_the_to_buy_list(curry_week):
    needed = _needed()
    assert set(needed) == {"Chicken thighs", "Fresh cilantro", "Tortillas"}, "no spice on the buy list"
    section = _section()
    assert set(section) == {"Ground cumin", "Smoked paprika", "Olive oil", "Salt"}
    assert all(not sp["ticked"] for sp in section.values())
    # Two recipes' cumin joined one line, in the section, not two.
    assert section["Ground cumin"]["quantity"] == "1 tbsp"
    # The count the band shows is the needed list: three things.
    by_store = tools.get_grocery_list_by_store()
    assert sum(len(s["items"]) for st in by_store["stores"] for s in st["sections"]) == 3


def test_a_tick_moves_the_spice_onto_the_list_and_an_untick_back(curry_week):
    cumin = _section()["Ground cumin"]
    tools.tick_spice(cumin["id"])
    assert _needed()["Ground cumin"] == "1 tbsp"
    assert _section()["Ground cumin"]["ticked"] is True, "still in the section, shown ticked"
    tools.tick_spice(cumin["id"], ticked=False)
    assert "Ground cumin" not in _needed()
    assert _section()["Ground cumin"]["ticked"] is False


def test_a_ticked_spice_lands_in_its_usual_store(curry_week):
    tools.set_item_store("Smoked paprika", "Bulk Barn")
    paprika = _section()["Smoked paprika"]
    tools.tick_spice(paprika["id"])
    by_store = {st["store"]: [it["item"] for s in st["sections"] for it in s["items"]]
                for st in tools.get_grocery_list_by_store()["stores"]}
    assert "Smoked paprika" in by_store.get("Bulk Barn", [])


def test_a_person_asking_for_a_spice_ticks_it(curry_week):
    """A hand add is a want: it merges onto the pending line and puts it
    on the list, rather than starting a second cumin."""
    tools.add_grocery_item("ground cumin", "1 jar", category="pantry")
    assert _needed()["Ground cumin"] == "1 tbsp + 1 jar", "one line (units honestly unreconciled), on the list"
    assert [sp["item"] for sp in tools.list_spices_this_week()["items"] if "cumin" in sp["item"].lower()] == ["Ground cumin"]
    assert _section()["Ground cumin"]["ticked"] is True


def test_a_hand_added_spice_with_no_plan_is_simply_on_the_list():
    tools.add_grocery_item("Paprika", "1 jar", category="pantry")
    assert _needed()["Paprika"] == "1 jar"
    assert _section()["Paprika"]["ticked"] is True


def test_bought_lately_is_not_offered_again(curry_week):
    """Light memory across weeks, borrowed from staples: a purchased line's
    created_at stands in for when it was bought."""
    tools.add_grocery_item("Ground cumin", "1 jar", category="pantry")
    cumin_id = next(i["id"] for i in tools.list_grocery_list() if i["item"] == "Ground cumin")
    tools.mark_grocery_item(cumin_id, "purchased")
    # Buying it also logged it to inventory, and the ingest already skips
    # anything inventory holds — cleared here so the test reads THIS rule.
    conn = get_conn()
    conn.execute("DELETE FROM inventory_items")
    conn.commit()
    conn.close()
    tools.add_recipe("Chili", ingredients=[{"item": "Ground cumin", "qty": "1 tbsp", "category": "pantry"}])
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(1))[0], "Chili", slot="dinner", weekly_plan_id=plan_b)
    tools.approve_weekly_plan(plan_b, approved_by="Emily")
    got = tools.list_spices_this_week()
    assert "Ground cumin" not in {sp["item"] for sp in got["items"]}
    assert got["recently_bought"] == ["Ground cumin"]
    # Long enough ago, and it is offered again.
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET created_at = '2026-01-01 10:00:00' WHERE status = 'purchased'")
    conn.commit()
    conn.close()
    got = tools.list_spices_this_week()
    assert "Ground cumin" in {sp["item"] for sp in got["items"]}
    assert got["recently_bought"] == []


def test_the_sort_queue_and_the_trip_never_see_an_unticked_spice(curry_week):
    tools.set_household_stores(["Loblaws", "Costco"]) if hasattr(tools, "set_household_stores") else None
    needed_ids = {i["id"] for i in tools.list_grocery_list()}
    for sp in _section().values():
        assert sp["id"] not in needed_ids
    assert all(f["name"] not in _section() for f in tools.get_pre_shop_flags())


def test_swapping_out_the_only_meal_that_wanted_a_spice_clears_its_line(curry_week):
    days = tools._week_dates(_monday(0))
    tools.add_recipe("Plain rice", ingredients=[{"item": "Rice", "qty": "2 cups", "category": "pantry"}])
    tools.swap_meal_in_plan(curry_week, days[1], "Plain rice", slot="dinner")
    section = _section()
    assert section["Ground cumin"]["quantity"] == "1 tsp", "the curry's teaspoon, the tacos' gone"
    assert "Rice" in _needed()


def test_a_new_week_replaces_last_weeks_unticked_spices(curry_week):
    tools.add_recipe("Chili", ingredients=[{"item": "Chili powder", "qty": "1 tbsp", "category": "pantry"}])
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(1))[0], "Chili", slot="dinner", weekly_plan_id=plan_b)
    tools.approve_weekly_plan(plan_b, approved_by="Emily")
    assert set(_section()) == {"Chili powder"}, "last week's unticked spices were never wanted"


def test_a_ticked_spice_from_last_week_goes_through_keep_or_drop(curry_week):
    cumin = _section()["Ground cumin"]
    tools.tick_spice(cumin["id"])
    tools.add_recipe("Chili", ingredients=[{"item": "Ground cumin", "qty": "1 tbsp", "category": "pantry"}])
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(1))[0], "Chili", slot="dinner", weekly_plan_id=plan_b)
    tools.approve_weekly_plan(plan_b, approved_by="Emily")
    carried = {c["item"]: c for c in tools.list_carried_over_items()}
    assert "Ground cumin" in carried, "ticked, so it was a real line last week"
    assert "Smoked paprika" not in carried, "unticked, so it was never wanted"
    assert _section()["Ground cumin"]["ticked"] is False, "this week's own cumin, unticked"
    tools.keep_carried_over_item(carried["Ground cumin"]["item_id"])
    assert _section()["Ground cumin"]["ticked"] is True, "kept means buy it"
    assert _needed()["Ground cumin"] == "2 tbsp"


def test_the_routes(signed_in, curry_week):
    res = signed_in.get("/api/grocery-list/spices")
    assert res.status_code == 200
    items = {sp["item"]: sp for sp in res.json()["items"]}
    assert set(items) == {"Ground cumin", "Smoked paprika", "Olive oil", "Salt"}
    res = signed_in.post(f"/api/grocery-list/{items['Salt']['id']}/spice", json={"ticked": True})
    assert res.status_code == 200 and res.json()["ticked"] is True
    assert "Salt" in _needed()
    res = signed_in.post(f"/api/grocery-list/{items['Salt']['id']}/spice", json={"ticked": False})
    assert res.status_code == 200
    assert "Salt" not in _needed()
    assert signed_in.post("/api/grocery-list/999999/spice", json={"ticked": True}).status_code == 404
    # The needed views never carry an unticked spice.
    res = signed_in.get("/api/grocery-list/by-store?status=needed")
    names = [it["item"] for st in res.json()["stores"] for s in st["sections"] for it in s["items"]]
    assert "Ground cumin" not in names


# ---------- the screen ----------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)

_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
function fetch(url, opts) {
  if (opts && opts.method === 'POST') POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
}
const panels = {};
const TOASTS = [];
function showToast(msg, action, hold) { TOASTS.push({ msg: msg, action: action, hold: hold }); }
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
function emptyMomentHtml(icon, line) { return '<div class="empty-moment">' + line + '</div>'; }
function click(dataset) {
  const el = { dataset: dataset, disabled: false, closest: function () { return null; },
    classList: { toggle: function () {} }, setAttribute: function () {}, querySelectorAll: function () { return []; } };
  onGroceryClick({ target: { closest: function () { return el; } } });
  return el;
}
function setUp(rows, spices, shops) {
  groceryState.data = { stores: { Loblaws: { sections: rows.length ? [{ section: 'other', items: rows }] : [], purchased: [], inCart: [] } } };
  groceryState.usualStores = shops || ['Loblaws'];
  groceryState.storesPromptDismissed = true;
  groceryState.spices = spices;
  return groceryState.data;
}
"""


def _grocery_block() -> str:
    start = SHELL_JS.index("  var GRO_CATEGORY_LABELS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


_SPICES = """{ items: [
  { id: 7, item: 'Ground cumin', quantity: '1 tbsp', category: 'pantry', store: '', ticked: false },
  { id: 8, item: 'Salt', quantity: 'to taste', category: 'pantry', store: '', ticked: true }
], recently_bought: ['Smoked paprika'] }"""


@_needs_node
def test_the_section_sits_on_the_list_closed_with_the_line_emily_asked_for():
    out = _node("""
setUp([{ id: 1, item: 'Chicken thighs', quantity: '2 lbs', store: 'Loblaws', store_decided: 1 }], %s);
const closed = groListHtml(groceryState.data);
groceryState.spicesOpen = true;
const open = groListHtml(groceryState.data);
console.log(JSON.stringify({ closed: closed, open: open }));
""" % _SPICES)
    assert "Spices this week" in out["closed"]
    assert "2 spices · 1 to buy" in out["closed"]
    assert "Tick the ones you need" not in out["closed"], "closed by default: less scrolling"
    html = out["open"]
    assert "All the spices the recipes need. Tick the ones you need to buy." in html
    assert 'data-id="7" data-ticked="0"' in html and 'data-id="8" data-ticked="1"' in html
    assert "Bought lately, so not listed: Smoked paprika" in html
    assert html.index("Spices this week") > html.index("Chicken thighs"), "after the stops, before Staples"


@_needs_node
def test_a_tick_and_an_untick_are_the_same_box():
    out = _node("""
setUp([], %s);
click({ gro: 'spice-tick', id: '7', ticked: '0' });
click({ gro: 'spice-tick', id: '8', ticked: '1' });
setTimeout(function () { console.log(JSON.stringify(POSTS)); }, 30);
""" % _SPICES)
    assert out == [
        {"url": "/api/grocery-list/7/spice", "body": {"ticked": True}},
        {"url": "/api/grocery-list/8/spice", "body": {"ticked": False}},
    ]


@_needs_node
def test_a_list_that_is_only_spices_is_not_nothing_to_buy():
    out = _node("""
setUp([], %s);
console.log(JSON.stringify({ body: groListHtml(groceryState.data), dock: groDockHtml(groceryState.data, 'list') }));
""" % _SPICES)
    assert "Nothing to buy" not in out["body"]
    assert "Spices this week" in out["body"]
    assert out["dock"] == "", "nothing to start a trip for, and nowhere to send anyone"


@_needs_node
def test_with_no_spices_the_section_is_absent():
    out = _node("""
setUp([{ id: 1, item: 'Chicken thighs', quantity: '2 lbs', store: 'Loblaws', store_decided: 1 }], { items: [], recently_bought: [] });
console.log(JSON.stringify(groListHtml(groceryState.data).indexOf('gro-spices')));
""")
    assert out == -1
