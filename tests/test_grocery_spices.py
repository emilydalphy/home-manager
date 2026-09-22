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

from conftest import household_today

import nodeharness
import pytest
from shop_harness import CLICK

from app import tools
from app.db import get_conn
from app.tools import spices


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _monday(offset_weeks: int = 0) -> str:
    today = household_today()
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
    """Memory across weeks is the spice rack — the Spices section of staples
    (2026-09-13): buying a spice makes it a staple, and a jar bought within
    its cadence is at home. Once the cadence has run out it is offered
    again — pre-ticked, since the rack says it's probably running low."""
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
    # Long enough ago — the staple's own clock, one source of truth — and
    # it is offered again, ticked, as probably running low. Counted back from
    # today, not written out: a literal date is only "long enough ago" while
    # today stays ahead of it, and under `--today=2026-01-15` 2026-01-01 is a
    # fortnight back against a 56-day cadence, so the rack rightly answered
    # "at home" and this test asked for a spice nothing was offering.
    conn = get_conn()
    conn.execute(
        "UPDATE staples SET last_bought_at = ?, next_due_at = ?",
        (_days_ago(120), _days_ago(30)),
    )
    conn.commit()
    conn.close()
    got = tools.list_spices_this_week()
    cumin = next(sp for sp in got["items"] if sp["item"] == "Ground cumin")
    assert cumin["ticked"] is True and cumin["due"] is True
    assert got["recently_bought"] == []


# ---------- the spice rack (Spices section of staples, 2026-09-13) ----------

def _rack_says(item: str, last_bought: str, next_due: str) -> None:
    """Move a spice staple's clock by hand — the staple is the one source
    of truth for 'at home' / 'running low', so the tests set it there."""
    conn = get_conn()
    conn.execute("UPDATE staples SET last_bought_at = ?, next_due_at = ? WHERE item = ?", (last_bought, next_due, item))
    conn.commit()
    conn.close()


def _days_ago(n: int) -> str:
    # The HOUSEHOLD's day. staples._today() moved onto the household's clock
    # on 2026-09-22, so a server-clock seed here is a day out from what the
    # app compares it against under a straddling runner. The 2026-09-21
    # far-date-pin-cliffs entry left this file on date.today() on the
    # explicit grounds that "a server-clock seed agrees with the app exactly
    # and by construction" BECAUSE staples read the server's clock — that
    # premise is now false, and the same sentence now argues for this.
    return (household_today() - datetime.timedelta(days=n)).isoformat()


def test_a_ticked_spice_that_came_home_is_a_spices_staple(curry_week):
    tools.tick_spice(_section()["Ground cumin"]["id"], True)
    cumin_id = next(i["id"] for i in tools.list_grocery_list() if i["item"] == "Ground cumin")
    tools.mark_grocery_item(cumin_id, "purchased")
    grouped = tools.list_staples_by_section()
    assert [g["label"] for g in grouped] == ["Spices"]
    (cumin,) = grouped[0]["staples"]
    assert cumin["item"] == "Ground cumin" and cumin["cadence_days"] == spices.RECENTLY_BOUGHT_DAYS
    assert cumin["last_bought_at"] == household_today().isoformat()
    assert cumin["due"] is False


def test_a_spice_whose_cadence_has_run_out_is_pre_ticked(curry_week):
    tools.add_staple("Ground cumin", category="pantry")
    _rack_says("Ground cumin", _days_ago(70), _days_ago(14))
    cumin = _section()["Ground cumin"]
    assert cumin["ticked"] is True and cumin["due"] is True
    # Made the way a due staple's line is made: an ordinary needed line
    # carrying the staple, so the list's own two answers work on it.
    row = next(i for i in tools.list_grocery_list() if i["item"] == "Ground cumin")
    assert row["staple_id"] == tools.list_staples()[0]["id"]
    # The rest of the card is untouched: paprika has no history, so it
    # waits unticked as before, and nothing is "bought lately".
    paprika = _section()["Smoked paprika"]
    assert paprika["ticked"] is False and paprika["due"] is False
    assert tools.list_spices_this_week()["recently_bought"] == []


def test_the_card_reads_the_staples_cadence_not_a_second_clock(curry_week):
    """A jar the household has shown it buys every three months is 'bought
    lately' for three months, not eight weeks — the learned cadence is
    the window."""
    tools.add_staple("Ground cumin", category="pantry")
    conn = get_conn()
    conn.execute("UPDATE staples SET cadence_days = 90, cadence_source = 'learned' WHERE item = 'Ground cumin'")
    conn.commit()
    conn.close()
    _rack_says("Ground cumin", _days_ago(70), (household_today() + datetime.timedelta(days=20)).isoformat())
    got = tools.list_spices_this_week()
    assert got["recently_bought"] == ["Ground cumin"]
    assert "Ground cumin" not in {sp["item"] for sp in got["items"]}


def test_unticking_a_pre_ticked_jar_is_we_have_plenty_and_a_retick_takes_it_back(curry_week):
    tools.add_staple("Ground cumin", category="pantry")
    _rack_says("Ground cumin", _days_ago(70), _days_ago(14))
    cumin = _section()["Ground cumin"]
    assert cumin["ticked"] is True
    tools.tick_spice(cumin["id"], False)
    # Back in the card, unticked, and the staple is a whole cadence out —
    # so a re-read does not tick it again.
    after = _section()["Ground cumin"]
    assert after["ticked"] is False and after["due"] is False
    (staple,) = tools.list_staples()
    assert staple["due"] is False
    assert staple["next_due_at"] == (household_today() + datetime.timedelta(days=56)).isoformat()
    assert "Ground cumin" not in _needed()
    # "Actually, I need it": the plenty is taken back, the jar is due again.
    tools.tick_spice(cumin["id"], True)
    again = _section()["Ground cumin"]
    assert again["ticked"] is True and again["due"] is True
    assert tools.list_staples()[0]["due"] is True
    conn = get_conn()
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM staple_events ORDER BY id").fetchall()]
    conn.close()
    assert "plenty" not in kinds


def test_a_hand_ticked_jar_is_not_probably_running_low(curry_week):
    """No staple, no claim: a person's own tick is just a thing to buy."""
    tools.tick_spice(_section()["Smoked paprika"]["id"], True)
    assert _section()["Smoked paprika"]["due"] is False


def test_nothing_in_the_rack_is_inventory(curry_week):
    tools.add_staple("Ground cumin", category="pantry")
    _rack_says("Ground cumin", _days_ago(70), _days_ago(14))
    tools.list_spices_this_week()
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
    conn.close()
    assert n == 0


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
// The click machinery is the shared one now (tests/shop_harness.CLICK),
// appended after the region below: this file used to carry its own copy
// of it, and with it the hole that copy had — a tap on a control the
// screen never rendered ran the handler anyway.
function setUp(rows, spices, shops) {
  groceryState.data = { stores: { Loblaws: { sections: rows.length ? [{ section: 'other', items: rows }] : [], purchased: [], inCart: [] } } };
  groceryState.usualStores = shops || ['Loblaws'];
  groceryState.storesPromptDismissed = true;
  groceryState.spices = spices;
  return groceryState.data;
}
"""


def _grocery_block() -> str:
    start = SHELL_JS.index("  var GRO_ICONS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    return SHELL_JS[start:end]


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + CLICK + body, timeout=30)
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
// The fold is closed by default (the test above pins that), so the boxes
// are not on the screen until it is opened — open it the way a person
// does. (Added 2026-09-21: the taps used to go straight into the handler
// over a closed fold, which clickIfRendered now refuses.)
clickIfRendered({ gro: 'spices-toggle' });
clickIfRendered({ gro: 'spice-tick', id: '7', ticked: '0' });
clickIfRendered({ gro: 'spice-tick', id: '8', ticked: '1' });
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
    assert 'data-gro="goto-plan"' not in out["dock"], "nowhere to send anyone: there is a list"
    assert 'data-gro="add-open"' in out["dock"]


@_needs_node
def test_a_pre_ticked_jar_says_why():
    out = _node("""
setUp([], { items: [
  { id: 7, item: 'Ground cumin', quantity: '1 tbsp', category: 'pantry', store: '', ticked: true, due: true },
  { id: 8, item: 'Salt', quantity: 'to taste', category: 'pantry', store: '', ticked: true, due: false }
], recently_bought: [] });
groceryState.spicesOpen = true;
console.log(JSON.stringify(groListHtml(groceryState.data)));
""")
    assert out.count("Probably running low") == 1
    assert out.index("Ground cumin") < out.index("Probably running low") < out.index("Salt")
    assert 'data-id="7" data-ticked="1"' in out, "still the same box — unticking it is the answer"


@_needs_node
def test_the_staples_card_groups_under_section_headings():
    out = _node("""
setUp([], { items: [], recently_bought: [] });
groceryState.staples = [
  { id: 1, item: 'Ground cumin', section: 'spices', section_label: 'Spices', cadence_words: 'about every 2 months', due_words: '', paused: false },
  { id: 2, item: 'Coffee', section: 'pantry', section_label: 'Pantry basics', cadence_words: 'about every 3 weeks', due_words: 'due next week', paused: false },
  { id: 3, item: 'Dish soap', section: 'household', section_label: 'Household supplies', cadence_words: 'about every month', due_words: '', paused: true }
];
groceryState.stapleSections = [
  { section: 'spices', label: 'Spices', staples: [groceryState.staples[0]] },
  { section: 'pantry', label: 'Pantry basics', staples: [groceryState.staples[1]] },
  { section: 'household', label: 'Household supplies', staples: [groceryState.staples[2]] }
];
groceryState.staplesOpen = true;
console.log(JSON.stringify(groListHtml(groceryState.data)));
""")
    assert out.count('class="gro-staple-sec"') == 3
    for label in ("Spices", "Pantry basics", "Household supplies"):
        assert '<span class="gro-eyebrow">' + label + '</span>' in out, label
    assert out.index("Spices</span>") < out.index("Ground cumin") < out.index("Pantry basics") < out.index("Coffee") \
        < out.index("Household supplies") < out.index("Dish soap")
    assert "3 things you buy on a rhythm" in out
    assert out.count('data-gro="staple-pause"') == 2 and out.count('data-gro="staple-resume"') == 1


@_needs_node
def test_with_no_spices_the_section_is_absent():
    out = _node("""
setUp([{ id: 1, item: 'Chicken thighs', quantity: '2 lbs', store: 'Loblaws', store_decided: 1 }], { items: [], recently_bought: [] });
console.log(JSON.stringify(groListHtml(groceryState.data).indexOf('gro-spices')));
""")
    assert out == -1
