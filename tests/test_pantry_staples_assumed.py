"""
Sauces and pantry staples are assumed at home, like the spice rack (Loop
Board, Emily, 2026-10-18): "the grocery list doesn't make me buy what's
almost always in the cupboard. I see them in one short list for the week,
assume I have them, and tick only the ones I'm out of."

The spices machinery, widened: a recipe's soy sauce or basmati rice waits
UNTICKED (status 'spice') in "Sauces this week" / "Pantry staples this
week", off every count, merge, sort and trip until ticked. Canned goods are
NOT assumed — bought as normal, in their own "canned" aisle. Fresh food is
never assumed.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import nodeharness
import pytest
from conftest import household_today
from shop_harness import CLICK

from app import tools
from app.db import get_conn
from app.tools import pantry, spices


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _monday(offset_weeks: int = 0) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _needed() -> dict[str, dict]:
    return {i["item"]: i for i in tools.list_grocery_list()}


def _sections() -> dict[str, dict]:
    return {sp["item"]: sp for sp in tools.list_spices_this_week()["items"]}


@pytest.fixture
def dal_week() -> int:
    tools.add_recipe("Dal and rice", ingredients=[
        {"item": "Toor dal", "qty": "1 cup", "category": "pantry"},
        {"item": "Basmati rice", "qty": "2 cups", "category": "pantry"},
        {"item": "Ghee", "qty": "2 tbsp", "category": "dairy"},
        {"item": "Ground cumin", "qty": "1 tsp", "category": "pantry"},
        {"item": "Fresh cilantro", "qty": "1 bunch", "category": "produce"},
        {"item": "Tomatoes", "qty": "2", "category": "produce"},
    ])
    tools.add_recipe("Sesame chicken", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "Light soy sauce", "qty": "3 tbsp", "category": "pantry"},
        {"item": "Honey", "qty": "2 tbsp", "category": "pantry"},
        {"item": "Sesame seeds", "qty": "1 tbsp", "category": "pantry"},
        {"item": "Almonds", "qty": "1/2 cup", "category": "pantry"},
        {"item": "Butter", "qty": "2 tbsp", "category": "dairy"},
        {"item": "Bread", "qty": "1 loaf", "category": "other"},
    ])
    tools.add_recipe("Chickpea curry", ingredients=[
        {"item": "Canned chickpeas", "qty": "2 cans", "category": "pantry"},
        {"item": "Coconut milk", "qty": "1 can", "category": "pantry"},
        {"item": "Dark soy sauce", "qty": "1 tbsp", "category": "pantry"},
    ])
    plan_id = tools.create_weekly_plan(_monday(0))["weekly_plan_id"]
    days = tools._week_dates(_monday(0))
    tools.plan_meal(days[0], "Dal and rice", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(days[1], "Sesame chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(days[2], "Chickpea curry", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


# ---------- the classifier ----------

@pytest.mark.parametrize("name,section", [
    ("light soy sauce", "sauces"), ("Dark soy sauce", "sauces"), ("Tamari", "sauces"),
    ("Fish sauce", "sauces"), ("Rice vinegar", "sauces"), ("Dijon mustard", "sauces"),
    ("Thai red curry paste", "sauces"), ("Ginger-garlic paste", "sauces"), ("chili oil", "sauces"),
    ("Honey", "pantry"), ("Basmati rice", "pantry"), ("Toor dal", "pantry"), ("moong daal", "pantry"),
    ("Sesame seeds", "pantry"), ("Almonds, toasted", "pantry"), ("ghee", "pantry"),
    ("Red lentils", "pantry"), ("dried chickpeas", "pantry"), ("Chicken broth", "pantry"),
    ("Panko breadcrumbs", "pantry"), ("Peanut butter", "pantry"), ("Spaghetti", "pantry"),
    ("Coconut oil", "pantry"), ("Vanilla extract", "pantry"),
    ("Ground cumin", "spices"), ("Olive oil", "spices"), ("Salt", "spices"),
])
def test_assumed_names_and_their_section(name, section):
    assert spices.assumed_section(name) == section


@pytest.mark.parametrize("name", [
    # fresh, chilled or frozen: always a real purchase
    "fresh basil", "Fresh pasta", "Baby spinach", "Tomatoes", "Butter", "Milk", "Eggs", "Bread",
    "Tortillas", "Tofu", "Chicken thighs", "Frozen peas", "Cauliflower rice", "Fresh salsa",
    # whose last word is not the staple
    "Almond milk", "Rice paper", "Mustard greens", "Spaghetti squash", "Molasses",
    # canned: bought as normal
    "Canned chickpeas", "Coconut milk", "Black beans", "Diced tomatoes", "Canned tuna",
])
def test_everything_else_is_bought(name):
    assert spices.assumed_section(name) is None


@pytest.mark.parametrize("name,canned", [
    ("Canned chickpeas", True), ("Coconut milk", True), ("Diced tomatoes", True), ("Canned tuna", True),
    ("Black beans", True), ("dried chickpeas", False), ("Tuna steak", False), ("Green beans", False),
    ("Tomatoes", False),
])
def test_canned_is_its_own_aisle(name, canned):
    assert pantry.is_canned(name) is canned


def test_every_soy_sauce_reads_as_soy_sauce():
    for name in ("Light soy sauce", "dark soy sauce", "Tamari", "Soy sauce, low sodium"):
        assert pantry.canonical_name(name) == "Soy sauce"
    assert pantry.canonical_name("Fish sauce") == "Fish sauce"


# ---------- the list ----------

def test_the_weeks_staples_and_sauces_wait_unticked_off_the_list(dal_week):
    needed = _needed()
    for name in ("Soy sauce", "Light soy sauce", "Honey", "Basmati rice", "Toor dal", "Sesame seeds", "Almonds", "Ghee"):
        assert name not in needed, f"{name} is assumed at home"
    # Fresh food and tins are bought as normal; tins in their own aisle.
    for name in ("Fresh cilantro", "Tomatoes", "Chicken thighs", "Butter", "Bread"):
        assert name in needed
    assert needed["Canned chickpeas"]["category"] == "canned"
    assert needed["Coconut milk"]["category"] == "canned"
    section = _sections()
    assert {n: section[n]["section"] for n in ("Soy sauce", "Honey", "Basmati rice", "Toor dal",
                                               "Sesame seeds", "Almonds", "Ghee", "Ground cumin")} == {
        "Soy sauce": "sauces", "Honey": "pantry", "Basmati rice": "pantry", "Toor dal": "pantry",
        "Sesame seeds": "pantry", "Almonds": "pantry", "Ghee": "pantry", "Ground cumin": "spices",
    }
    assert all(not sp["ticked"] for sp in section.values())
    # Light and dark soy sauce are one bottle.
    assert [n for n in section if "soy" in n.lower()] == ["Soy sauce"]
    # The count the band shows is the needed list only.
    by_store = tools.get_grocery_list_by_store()
    shown = [it["item"] for st in by_store["stores"] for s in st["sections"] for it in s["items"]]
    assert sorted(shown) == sorted(needed)


def test_a_tick_puts_it_in_its_aisle_and_an_untick_takes_it_off(dal_week):
    honey = _sections()["Honey"]
    tools.tick_spice(honey["id"])
    assert "Honey" in _needed()
    by_store = tools.get_grocery_list_by_store()
    aisles = {it["item"]: s["section"] for st in by_store["stores"] for s in st["sections"] for it in s["items"]}
    assert aisles["Honey"] == "pantry"
    assert _sections()["Honey"]["ticked"] is True, "still in its section, shown ticked"
    tools.tick_spice(honey["id"], ticked=False)
    assert "Honey" not in _needed()
    assert _sections()["Honey"]["ticked"] is False


def test_the_sort_queue_and_the_trip_never_see_an_untouched_staple(dal_week):
    names = {i["item"] for i in tools.list_grocery_list()}
    assert "Basmati rice" not in names and "Soy sauce" not in names
    # A second week's rice joins this week's line — one bag per name.
    tools.add_recipe("Fried rice", ingredients=[{"item": "Basmati rice", "qty": "1 cup", "category": "pantry"}])
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(1))[0], "Fried rice", slot="dinner", weekly_plan_id=plan_b)
    tools.approve_weekly_plan(plan_b, approved_by="Emily")
    assert [n for n in _sections() if "rice" in n.lower()] == ["Basmati rice"]


def test_a_person_adding_a_staple_buys_it():
    tools.add_grocery_item("Honey", "1 jar", category="pantry")
    assert _needed()["Honey"]["quantity"] == "1 jar"
    # A person's own add is not this week's recipes: it is in its aisle,
    # not asked about again in the section.
    assert "Honey" not in _sections()


def test_a_lot_this_week_is_flagged():
    tools.add_recipe("Batch biryani", ingredients=[
        {"item": "Basmati rice", "qty": "4 cups", "category": "pantry"},
        {"item": "Soy sauce", "qty": "1 tbsp", "category": "pantry"},
    ])
    plan_id = tools.create_weekly_plan(_monday(0))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(0))[0], "Batch biryani", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    section = _sections()
    assert section["Basmati rice"]["a_lot"] is True
    assert section["Soy sauce"]["a_lot"] is False
    assert "Basmati rice" not in _needed(), "a lot is still assumed — only flagged"


def test_the_routes(signed_in, dal_week):
    res = signed_in.get("/api/grocery-list/spices")
    assert res.status_code == 200
    body = res.json()
    items = {sp["item"]: sp for sp in body["items"]}
    assert items["Toor dal"]["section"] == "pantry"
    assert set(body["recently_bought_by_section"]) == {"spices", "sauces", "pantry"}
    res = signed_in.post(f"/api/grocery-list/{items['Toor dal']['id']}/spice", json={"ticked": True})
    assert res.status_code == 200 and "Toor dal" in _needed()
    res = signed_in.post(f"/api/grocery-list/{items['Toor dal']['id']}/spice", json={"ticked": False})
    assert res.status_code == 200 and "Toor dal" not in _needed()


# ---------- memory: the staples, the spice-rack way ----------

def _days_ago(n: int) -> str:
    return (household_today() - datetime.timedelta(days=n)).isoformat()


def _buy(name: str) -> None:
    sp = _sections()[name]
    tools.tick_spice(sp["id"])
    tools.mark_grocery_item(sp["id"], "purchased")
    # Buying logs it to inventory too, and the ingest skips what inventory
    # holds — cleared so the test reads THIS rule.
    conn = get_conn()
    conn.execute("DELETE FROM inventory_items")
    conn.commit()
    conn.close()


def _next_week_with(*ingredients) -> int:
    tools.add_recipe("Next", ingredients=[{"item": n, "qty": "1 tbsp", "category": "pantry"} for n in ingredients])
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(1))[0], "Next", slot="dinner", weekly_plan_id=plan_b)
    tools.approve_weekly_plan(plan_b, approved_by="Emily")
    return plan_b


def test_a_staple_bought_through_the_list_is_kept_the_spice_rack_way(dal_week):
    _buy("Honey")
    honey = next(s for s in tools.list_staples() if s["item"] == "Honey")
    assert (honey["section"], honey["rack"]) == ("pantry_staples", True)
    assert honey["cadence_days"] == spices.PANTRY_RECENTLY_BOUGHT_DAYS
    # Never put on the list on a rhythm, however due.
    conn = get_conn()
    conn.execute("UPDATE staples SET next_due_at = ? WHERE item = 'Honey'", (_days_ago(1),))
    conn.commit()
    conn.close()
    tools.sync_due_staples()
    assert "Honey" not in _needed()
    # Bought lately: not offered next week, and the section says so.
    conn = get_conn()
    conn.execute("UPDATE staples SET next_due_at = ? WHERE item = 'Honey'", (_days_ago(-20),))
    conn.commit()
    conn.close()
    _next_week_with("Honey")
    got = tools.list_spices_this_week()
    assert "Honey" not in {sp["item"] for sp in got["items"]}
    assert got["recently_bought_by_section"]["pantry"] == ["Honey"]
    # Past its cadence: pre-ticked, probably running low.
    conn = get_conn()
    conn.execute("UPDATE staples SET last_bought_at = ?, next_due_at = ? WHERE item = 'Honey'",
                 (_days_ago(60), _days_ago(30)))
    conn.commit()
    conn.close()
    honey = _sections()["Honey"]
    assert honey["ticked"] is True and honey["due"] is True
    assert "Honey" in _needed()


def test_a_staple_a_person_set_up_keeps_its_rhythm():
    tools.add_staple("Coffee", category="pantry")
    coffee = next(s for s in tools.list_staples() if s["item"] == "Coffee")
    assert (coffee["section"], coffee["rack"]) == ("pantry", False), "a regular, not the list's"


def test_an_old_spice_rack_staple_stays_off_the_rhythm():
    """Sesame seeds were a spice-rack name before 2026-10-18; a staple made
    for them then has no kept_by_list flag and must still never be pushed."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO staples (household_id, item, category, cadence_days, next_due_at) "
        "VALUES (1, 'Sesame seeds', 'pantry', 56, ?)", (_days_ago(3),))
    conn.commit()
    conn.close()
    sesame = next(s for s in tools.list_staples() if s["item"] == "Sesame seeds")
    assert sesame["rack"] is True
    tools.sync_due_staples()
    assert "Sesame seeds" not in _needed()


# ---------- "Check you have this" ----------

def test_a_sauce_new_to_the_kitchen_is_flagged_until_ticked(dal_week):
    soy = _sections()["Soy sauce"]
    assert soy["new"] is True
    assert _sections()["Honey"]["new"] is False, "only sauces carry the flag"
    tools.tick_spice(soy["id"])
    tools.tick_spice(soy["id"], ticked=False)
    assert _sections()["Soy sauce"]["new"] is False, "ticked once: known"
    assert "Soy sauce" not in _needed(), "the flag adds nothing to the list"


def test_finishing_before_you_shop_counts_as_we_have_it(dal_week):
    steps = tools.before_shop_steps()["spices"]["choices"]
    assert steps[0]["name"] == "Soy sauce" and steps[0]["new"] is True, "the new sauce first"
    assert {"Honey", "Basmati rice", "Toor dal", "Ground cumin"} <= {c["name"] for c in steps}
    assert {c["name"]: c["section"] for c in steps}["Toor dal"] == "pantry"
    tools.mark_before_shop_done()
    assert _sections()["Soy sauce"]["new"] is False


def test_a_week_ended_with_the_sauce_unticked_counts_as_we_have_it(dal_week):
    import unittest.mock as mock
    from app.tools import grocery as _grocery
    tools.add_recipe("Stir fry", ingredients=[{"item": "Fish sauce", "qty": "1 tbsp", "category": "pantry"}])
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(1))[0], "Stir fry", slot="dinner", weekly_plan_id=plan_b)
    monday = datetime.date.fromisoformat(_monday(1))
    with mock.patch.object(_grocery, "_household_today", lambda conn=None: monday):
        tools.approve_weekly_plan(plan_b, approved_by="Emily")
    conn = get_conn()
    known = {r[0] for r in conn.execute("SELECT item_key FROM assumed_known").fetchall()}
    conn.close()
    assert "soy sauce" in known, "last week's unticked soy sauce: we have it"
    assert _sections()["Fish sauce"]["new"] is True, "a sauce never seen is still asked about"


# ---------- review, 2026-10-19 ----------

@pytest.mark.parametrize("name", [
    "Almond flour", "chickpea flour", "Self-raising flour", "Arborio rice", "Wild rice", "Sushi rice",
    "Icing sugar", "Coconut sugar", "Powdered sugar", "Lentil pasta", "Iced tea", "Cold brew coffee",
    "Nutritional yeast", "Hot honey",
])
def test_specialty_versions_are_bought(name):
    assert spices.assumed_section(name) is None


def test_the_everyday_kinds_are_still_assumed():
    for name in ("All-purpose flour", "Brown sugar", "Jasmine rice", "Penne", "Black tea", "Honey"):
        assert spices.assumed_section(name) == "pantry", name
    assert pantry.is_canned("Ahi tuna") is False and pantry.is_canned("Canned tuna") is True


def test_bought_lately_never_hides_a_big_amount(dal_week):
    _buy("Soy sauce")
    tools.add_recipe("Braise", ingredients=[{"item": "Soy sauce", "qty": "2 cups", "category": "pantry"}])
    plan_b = tools.create_weekly_plan(_monday(1))["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday(1))[0], "Braise", slot="dinner", weekly_plan_id=plan_b)
    tools.approve_weekly_plan(plan_b, approved_by="Emily")
    soy = _sections()["Soy sauce"]
    assert soy["a_lot"] is True and soy["ticked"] is False
    assert "Soy sauce" not in tools.list_spices_this_week()["recently_bought_by_section"]["sauces"]


def test_an_untick_never_takes_a_households_own_amount_off(dal_week):
    rice = _sections()["Basmati rice"]
    tools.tick_spice(rice["id"])
    tools.add_grocery_item("Basmati rice", "3 cups", category="pantry")
    line = _needed()["Basmati rice"]
    assert line["quantity"] == "5 cups"
    got = tools.tick_spice(line["id"], ticked=False)
    assert got.get("unchanged") is True and got["said"]
    assert _needed()["Basmati rice"]["quantity"] == "5 cups", "still on the list, the household's amount with it"


# ---------- the screen ----------

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the shell's own functions")

_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
function fetch(url, opts) {
  if (opts && opts.method === 'POST') POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
}
const panels = {};
function showToast() {}
const STORE = new Map();
const window = {
  localStorage: { getItem: function (k) { return STORE.has(k) ? STORE.get(k) : null; },
    setItem: function (k, v) { STORE.set(k, String(v)); }, removeItem: function (k) { STORE.delete(k); } },
  history: { pushState: function () {} }, confirm: function () { return true; }
};
var scrollEl = null;
function activateTab() {}
var coachState = { householdId: 1 };
function emptyMomentHtml(icon, line) { return '<div class="empty-moment">' + line + '</div>'; }
function setUp(spices) {
  groceryState.data = { stores: { Loblaws: { sections: [{ section: 'produce', items: [
    { id: 1, item: 'Tomatoes', quantity: '2', store: 'Loblaws', store_decided: 1 }] }], purchased: [], inCart: [] } } };
  groceryState.usualStores = ['Loblaws'];
  groceryState.storesPromptDismissed = true;
  groceryState.spices = spices;
}
"""

_PAYLOAD = """{ items: [
  { id: 7, item: 'Ground cumin', quantity: '1 tsp', section: 'spices', ticked: false },
  { id: 8, item: 'Soy sauce', quantity: '4 tbsp', section: 'sauces', ticked: false },
  { id: 9, item: 'Fish sauce', quantity: '1 tbsp', section: 'sauces', ticked: false, new: true },
  { id: 10, item: 'Basmati rice', quantity: '4 cups', section: 'pantry', ticked: false, a_lot: true },
  { id: 11, item: 'Honey', quantity: '2 tbsp', section: 'pantry', ticked: true }
], recently_bought: ['Toor dal'], recently_bought_by_section: { spices: [], sauces: [], pantry: ['Toor dal'] } }"""


def _node(body: str):
    start = SHELL_JS.index("  var GRO_ICONS = {")
    end = SHELL_JS.index("  // ---------- Hands-free voice ----------", start)
    res = nodeharness.run_node(_STUB + SHELL_JS[start:end] + CLICK + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_three_sections_closed_then_open():
    out = _node("""
setUp(%s);
const closed = groListHtml(groceryState.data);
clickIfRendered({ gro: 'spices-toggle', section: 'sauces' });
clickIfRendered({ gro: 'spices-toggle', section: 'pantry' });
const open = groListHtml(groceryState.data);
clickIfRendered({ gro: 'spice-tick', id: '10', ticked: '0' });
setTimeout(function () { console.log(JSON.stringify({ closed: closed, open: open, posts: POSTS })); }, 30);
""" % _PAYLOAD)
    closed, html = out["closed"], out["open"]
    assert "Spices this week" in closed and "Sauces this week" in closed and "Pantry staples this week" in closed
    assert "2 sauces" in closed and "2 staples · 1 to buy" in closed
    assert "Check you have this" not in closed, "closed by default"
    assert "A few of these are new to your kitchen: tick any you need." in html
    assert html.index("Fish sauce") < html.index("Soy sauce"), "the new one first"
    assert "Check you have this" in html and "A lot this week" in html
    assert "Bought lately, so not listed: Toor dal" in html
    assert "All the spices" not in html, "the spices fold stayed closed"
    assert out["posts"] == [{"url": "/api/grocery-list/10/spice", "body": {"ticked": True}}]


@_needs_node
def test_canned_is_an_aisle_on_the_card():
    out = _node("""
console.log(JSON.stringify({ label: GRO_AISLE_LABELS.canned, at: GRO_SECTION_ORDER.indexOf('canned'),
  pantry: GRO_SECTION_ORDER.indexOf('pantry'), aisle: groAisleOf({ category: 'canned' }) }));
""")
    assert out == {"label": "Canned", "at": out["pantry"] + 1, "pantry": out["pantry"], "aisle": "canned"}


@_needs_node
def test_before_you_shop_groups_the_chips_new_first():
    out = _node("""
groceryState.step = 'beforeshop';
groceryState.bsSnap = { spices: { choices: [
  { item_id: 9, name: 'Fish sauce', section: 'sauces', new: true },
  { item_id: 7, name: 'Ground cumin', section: 'spices' },
  { item_id: 10, name: 'Basmati rice', section: 'pantry' }
], on_list: [] } };
console.log(JSON.stringify({ html: bsSpicesHtml() }));
""")
    html = out["html"]
    order = [html.index(w) for w in ("Check you have these", "Fish sauce", "Spices and oils", "Ground cumin",
                                       "Pantry staples", "Basmati rice")]
    assert order == sorted(order)
