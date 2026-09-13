"""
Sorting the list: "have this already" and "I'll use something else instead"
for any ingredient (Emily, 2026-09-13).

    "When you're going through the organizing for the week, there should
    also be the 'have this already' option ... and if we want to use an
    alternative that should be a spot we can put it here too, for example,
    instead of fresh oregano I'll use dry oregano."

"Have it" is a per-week answer, not an inventory record (policy
2026-09-01): it is the pre-shop drop — soft-removed, undoable, on the
wrap-up — and never the kitchen-inventory route. "Use something else" is
grocery.substitute_grocery_item: the line becomes the alternative (or comes
off, when the alternative is at home), and the recipe's ingredient line
says "using dry oregano instead" when it is cooked.
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


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _monday() -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _needed() -> dict[str, dict]:
    return {i["item"]: i for i in tools.list_grocery_list()}


@pytest.fixture
def week() -> int:
    tools.add_recipe("Greek salad", ingredients=[
        {"item": "Fresh oregano", "qty": "2 tbsp", "category": "produce"},
        {"item": "Cucumber", "qty": "1", "category": "produce"},
        {"item": "Feta", "qty": "200 g", "category": "dairy"},
    ])
    tools.add_recipe("Lemon chicken", ingredients=[
        {"item": "Fresh oregano", "qty": "1 tbsp", "category": "produce"},
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
    ])
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    days = tools._week_dates(_monday())
    tools.plan_meal(days[0], "Greek salad", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(days[1], "Lemon chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _cook_ingredients(plan_id: int) -> dict[str, list[dict]]:
    view = tools.get_cooker_view(plan_id)
    return {m["meal"]: m["ingredients"] for m in view["meals"]}


# ---------- use something else ----------

def test_the_line_becomes_the_alternative_and_the_recipe_says_so(week):
    oregano = _needed()["Fresh oregano"]
    assert oregano["quantity"] == "3 tbsp", "two recipes, one line"
    result = tools.substitute_grocery_item(oregano["id"], "dry oregano")
    assert result["item"] == "dry oregano" and result["original_item"] == "Fresh oregano"
    needed = _needed()
    assert "Fresh oregano" not in needed
    assert needed["dry oregano"]["quantity"] == "3 tbsp", "the amount stays for the person to adjust"
    cook = _cook_ingredients(week)
    for meal in ("Greek salad", "Lemon chicken"):
        line = next(i for i in cook[meal] if i["item"] == "Fresh oregano")
        assert line["substitute"] == "dry oregano", "every meal that wanted it hears about the swap"
        assert line["substitute_at_home"] is False
    assert "substitute" not in next(i for i in cook["Greek salad"] if i["item"] == "Feta")


def test_an_alternative_already_at_home_takes_the_line_off(week):
    oregano = _needed()["Fresh oregano"]
    result = tools.substitute_grocery_item(oregano["id"], "dry oregano", at_home=True, author="Emily")
    assert result["status"] == "removed"
    assert "Fresh oregano" not in _needed() and "dry oregano" not in _needed()
    line = next(i for i in _cook_ingredients(week)["Greek salad"] if i["item"] == "Fresh oregano")
    assert line["substitute"] == "dry oregano" and line["substitute_at_home"] is True
    # On the wrap-up's "already have" list, like any drop, with who said so.
    decided = {d["item"]: d for d in tools.get_already_have_decisions()}
    assert decided["dry oregano"]["removed_by"] == "Emily"


def test_undo_puts_the_original_back_either_way(week):
    oregano = _needed()["Fresh oregano"]
    tools.substitute_grocery_item(oregano["id"], "dry oregano")
    tools.undo_substitution(oregano["id"])
    assert _needed()["Fresh oregano"]["quantity"] == "3 tbsp"
    assert "substitute" not in next(i for i in _cook_ingredients(week)["Greek salad"] if i["item"] == "Fresh oregano")

    tools.substitute_grocery_item(oregano["id"], "dry oregano", at_home=True)
    assert "Fresh oregano" not in _needed()
    tools.undo_substitution(oregano["id"])
    assert _needed()["Fresh oregano"]["quantity"] == "3 tbsp"
    assert tools.undo_substitution(oregano["id"])["unchanged"] is True, "nothing left to undo"


def test_the_alternative_picks_up_its_usual_store(week):
    tools.set_item_store("dry oregano", "Bulk Barn")
    oregano = _needed()["Fresh oregano"]
    tools.substitute_grocery_item(oregano["id"], "dry oregano")
    assert _needed()["dry oregano"]["store"] == "Bulk Barn"


def test_a_swap_is_this_weeks_not_the_recipes(week):
    """Next week the recipe asks for fresh oregano again."""
    oregano = _needed()["Fresh oregano"]
    tools.substitute_grocery_item(oregano["id"], "dry oregano")
    conn = get_conn()
    ings = conn.execute("SELECT ingredients_json FROM recipes WHERE name = 'Greek salad'").fetchone()[0]
    conn.close()
    assert "dry oregano" not in ings
    monday_next = (datetime.date.fromisoformat(_monday()) + datetime.timedelta(days=7)).isoformat()
    plan_b = tools.create_weekly_plan(monday_next)["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(monday_next)[0], "Greek salad", slot="dinner", weekly_plan_id=plan_b)
    tools.approve_weekly_plan(plan_b, approved_by="Emily")
    assert "substitute" not in next(i for i in _cook_ingredients(plan_b)["Greek salad"] if i["item"] == "Fresh oregano")


def test_a_blank_alternative_and_a_bought_line_are_refused(week):
    oregano = _needed()["Fresh oregano"]
    with pytest.raises(ValueError):
        tools.substitute_grocery_item(oregano["id"], "   ")
    tools.mark_grocery_item(oregano["id"], "in_cart")
    assert tools.substitute_grocery_item(oregano["id"], "dry oregano")["unchanged"] is True


def test_a_pending_spice_swapped_for_something_goes_on_the_list(week):
    tools.add_recipe("Chili", ingredients=[{"item": "Ground cumin", "qty": "1 tsp", "category": "pantry"}])
    tools.plan_meal(tools._week_dates(_monday())[2], "Chili", slot="dinner", weekly_plan_id=week,
                    add_ingredients_to_grocery_list=True)
    cumin = next(sp for sp in tools.list_spices_this_week()["items"] if sp["item"] == "Ground cumin")
    tools.substitute_grocery_item(cumin["id"], "cumin seeds")
    assert "cumin seeds" in _needed(), "choosing what to buy instead is choosing to buy it"


def test_a_swap_dies_with_the_line_it_was_made_on(week):
    """Verifier, 2026-09-13: both meals that wanted the oregano swapped out
    → the ledger deletes the line; a third meal wanting fresh oregano
    later in the week must not be told it was substituted."""
    oregano = _needed()["Fresh oregano"]
    tools.substitute_grocery_item(oregano["id"], "dry oregano")
    days = tools._week_dates(_monday())
    tools.add_recipe("Plain rice", ingredients=[{"item": "Rice", "qty": "2 cups", "category": "pantry"}])
    tools.swap_meal_in_plan(week, days[0], "Plain rice", slot="dinner")
    tools.swap_meal_in_plan(week, days[1], "Plain rice", slot="dinner")
    assert "dry oregano" not in _needed() and "Fresh oregano" not in _needed()
    tools.plan_meal(days[2], "Greek salad", slot="dinner", weekly_plan_id=week, add_ingredients_to_grocery_list=True)
    assert _needed()["Fresh oregano"]["quantity"] == "2 tbsp"
    line = next(i for i in _cook_ingredients(week)["Greek salad"] if i["item"] == "Fresh oregano")
    assert "substitute" not in line


def test_a_swap_with_no_plan_annotates_nothing_later():
    tools.add_grocery_item("Fresh oregano", "1 bunch", category="produce")
    row = _needed()["Fresh oregano"]
    tools.substitute_grocery_item(row["id"], "dry oregano")
    assert _needed()["dry oregano"]["quantity"] == "1 bunch"
    tools.add_recipe("Greek salad", ingredients=[{"item": "Fresh oregano", "qty": "2 tbsp", "category": "produce"}])
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(_monday())[0], "Greek salad", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    line = next(i for i in _cook_ingredients(plan_id)["Greek salad"] if i["item"] == "Fresh oregano")
    assert "substitute" not in line, "a swap made with no week is not every week's"


# ---------- have it already: no inventory ----------

def test_have_it_is_the_pre_shop_drop_and_never_an_inventory_write(week):
    cucumber = _needed()["Cucumber"]
    tools.drop_grocery_item_pre_shop(cucumber["id"], author="Emily")
    assert "Cucumber" not in _needed()
    assert all(i["item"].lower() != "cucumber" for i in tools.get_inventory()), "a per-week answer, not a record"
    assert "Cucumber" in {d["item"] for d in tools.get_already_have_decisions()}
    tools.undo_pre_shop_drop(cucumber["id"])
    assert _needed()["Cucumber"]["quantity"] == "1"


# ---------- over HTTP ----------

def test_the_routes(signed_in, week):
    oregano = _needed()["Fresh oregano"]
    res = signed_in.post(f"/api/grocery-list/{oregano['id']}/substitute", json={"alternative": "dry oregano"})
    assert res.status_code == 200 and res.json()["item"] == "dry oregano"
    assert "dry oregano" in _needed()
    res = signed_in.post(f"/api/grocery-list/{oregano['id']}/substitute-undo")
    assert res.status_code == 200
    assert "Fresh oregano" in _needed()
    assert signed_in.post(f"/api/grocery-list/{oregano['id']}/substitute", json={"alternative": ""}).status_code == 400
    assert signed_in.post("/api/grocery-list/999999/substitute", json={"alternative": "x"}).status_code == 404
    view = signed_in.get("/api/cooker-view").json()
    assert view["meals"]


# ---------- the screens ----------

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
let FIELD_VALUE = '';
// groPanel() is null in this harness, so subst-save reads its field from
// here: the handler's `substPanel && ...` short-circuits to a null field.
// We give it a panel with exactly one query.
panels.grocery = { dataset: {}, querySelector: function (sel) {
  return sel.indexOf('#gro-subst-') === 0 ? { value: FIELD_VALUE, focus: function () {} } : null;
} };
function click(dataset) {
  const el = { dataset: dataset, disabled: false, closest: function () { return null; },
    classList: { toggle: function () {} }, setAttribute: function () {}, querySelectorAll: function () { return []; } };
  onGroceryClick({ target: { closest: function () { return el; } } });
  return el;
}
function settle(fn) { setTimeout(fn, 30); }
function setUp(n, shops) {
  const rows = [];
  for (let i = 1; i <= n; i++) rows.push({ id: i, item: 'Thing ' + i, quantity: '1', store: '', store_decided: 0 });
  groceryState.data = { stores: { Unassigned: { sections: [{ section: 'other', items: rows }], purchased: [], inCart: [] } } };
  groceryState.usualStores = shops === undefined ? ['Loblaws', 'Costco'] : shops;
  groceryState.storesPromptDismissed = true;
  groceryState.sortAllPicks = {};
  groceryState.carried = [];
  groceryState.spices = { items: [], recently_bought: [] };
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


@_needs_node
def test_every_sorting_screen_and_the_row_menu_offer_both():
    out = _node("""
setUp(3);
groceryState.step = 'sort';
const queue = groSortHtml(groceryState.data);
const all = groSortAllHtml(groceryState.data);
groceryState.openRowId = '2';
const menu = groRowMenuHtml(groceryState.data.stores.Unassigned.sections[0].items[1], groceryState.data);
console.log(JSON.stringify({ queue: queue, all: all, menu: menu }));
""")
    for name, html in out.items():
        assert 'data-gro="have-it"' in html, name + " offers Have it"
        assert 'data-gro="subst-open"' in html, name + " offers Use something else"
        assert 'data-gro="already-have"' not in html, name + " never writes to inventory"
    assert out["all"].count('data-gro="have-it"') == 3, "one per row on the one-screen sort"


@_needs_node
def test_have_it_drops_the_line_without_inventory_and_undoes():
    out = _node("""
setUp(3);
groceryState.step = 'sort';
click({ gro: 'have-it', id: '1', name: 'Thing 1' });
settle(function () {
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({ posts: POSTS, toast: TOASTS[0].msg, undo: TOASTS[0].action.label }));
  });
});
""")
    assert out["posts"][0] == {"url": "/api/grocery-list/1/pre-shop", "body": {"decision": "drop", "author": "user"}}
    assert out["posts"][1] == {"url": "/api/grocery-list/1/pre-shop-undo", "body": {}}
    assert out["toast"] == "Thing 1 off the list — you have it" and out["undo"] == "Undo"


@_needs_node
def test_use_something_else_opens_a_field_then_writes_the_swap_and_undoes():
    out = _node("""
setUp(3);
groceryState.step = 'sort';
click({ gro: 'subst-open', id: '1' });
const opened = groSortHtml(groceryState.data);
FIELD_VALUE = '';
click({ gro: 'subst-save', id: '1', have: '0', name: 'Thing 1' });
const postsAfterBlank = POSTS.length;
FIELD_VALUE = 'dry oregano';
click({ gro: 'subst-save', id: '1', have: '0', name: 'Thing 1' });
settle(function () {
  tapUndo();
  settle(function () {
    FIELD_VALUE = 'dry oregano';
    click({ gro: 'subst-save', id: '2', have: '1', name: 'Thing 2' });
    settle(function () {
      console.log(JSON.stringify({ opened: opened, blank: postsAfterBlank, posts: POSTS,
        toasts: TOASTS.map(function (t) { return t.msg; }), substOpen: groceryState.substOpenId }));
    });
  });
});
""")
    assert 'placeholder="What instead?"' in out["opened"]
    assert "Put it on the list" in out["opened"] and "I have it" in out["opened"]
    assert out["blank"] == 0, "nothing is written for an empty field"
    assert out["posts"][0] == {"url": "/api/grocery-list/1/substitute",
                               "body": {"alternative": "dry oregano", "at_home": False, "author": "user"}}
    assert out["posts"][1] == {"url": "/api/grocery-list/1/substitute-undo", "body": {}}
    assert out["posts"][2]["body"]["at_home"] is True
    assert out["toasts"][0] == "dry oregano instead of Thing 1"
    assert out["toasts"][1] == "Thing 2 off the list — using dry oregano instead"
    assert out["substOpen"] is None, "the field closes once it has been answered"


def test_the_cook_card_says_what_is_going_in_instead():
    """cookIngredientLabel is the one place every cook-screen ingredient
    line is worded, so the note lands on the recipe, the everything-out
    list and the step needs at once."""
    start = SHELL_JS.index("  function cookIngredientLabel(ing) {")
    fn = SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]
    assert "if (ing && ing.substitute) label += ' — using ' + ing.substitute + ' instead';" in fn
