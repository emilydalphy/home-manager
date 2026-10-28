"""
Inventory A1 — finishing the shop tells me what's now in the kitchen
(Emily, 2026-10-27; Flow 3 · Shop, wrap-up; Flow 15 · Inventory).

Every purchased tick has always written the kitchen (grocery.
mark_grocery_item -> inventory._add_to_inventory, with a receipt). Nobody
was told. At the list's finished moment the Shop tab now shows "Now in the
kitchen" — counts by place, read off THIS shop's tick receipts — and "Use
these first": up to three of those things that go off soonest, each with
its plan. Unticking a line takes it back out (the untick's own reversal),
and a shop that put nothing in the kitchen shows no card.

Backend over the real tools and the real route; the card under node
against shell.js's own functions (tests/shop_harness).
"""
from __future__ import annotations

from conftest import household_date, household_today

from app import tools
from app.db import get_conn
from app.tools._shared import household_id
from shop_harness import needs_node, run


def _bought(item, category, quantity="1"):
    line = tools.add_grocery_item(item, quantity=quantity, category=category)
    tools.mark_grocery_item(line["item_id"], "purchased")
    return line["item_id"]


def _places(now):
    return {p["place"]: p["count"] for p in now["places"]}


def _plan_dinner_for(grocery_item_id, offset_days, slot="dinner"):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, freeform_meal, slot_state) "
        "VALUES (?, ?, ?, 'Chicken skewers', 'planned')",
        (household_id(), household_date(offset_days), slot),
    )
    conn.execute(
        "INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
        "VALUES (?, ?, ?, 'x', '1')",
        (household_id(), cur.lastrowid, grocery_item_id),
    )
    conn.commit()
    conn.close()


def test_counts_by_place_are_what_this_shops_ticks_added():
    _bought("Chicken thighs", "meat/seafood", "2 lb")
    _bought("Milk", "dairy", "2 l")
    _bought("Frozen peas", "frozen")
    _bought("Jasmine rice", "pantry", "1 bag")
    tools.add_grocery_item("Lemons", quantity="3", category="produce")  # not ticked

    _bought("Paper towels", "other", "1 pack")  # no shelf of its own: counted under its stated place
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET location = 'pantry' WHERE item = 'Paper towels'")
    conn.commit()
    conn.close()

    now = tools.kitchen_from_this_shop()
    # The rice became a pantry staple on its tick (staples._create_spice_
    # staple) and the Kitchen tab never draws a staple, so neither does
    # the card (defect hunt 2026-10-28: "Pantry · 1" above "Pantry · 0").
    assert now["total"] == 4
    assert [p["place"] for p in now["places"]] == ["fridge", "freezer", "pantry"], "the card's order"
    assert _places(now) == {"fridge": 2, "freezer": 1, "pantry": 1}


def test_untick_takes_it_back_out_and_retick_counts_it_once():
    _bought("Chicken thighs", "meat/seafood", "2 lb")
    milk = _bought("Milk", "dairy", "2 l")
    tools.mark_grocery_item(milk, "needed")
    assert _places(tools.kitchen_from_this_shop()) == {"fridge": 1}
    tools.mark_grocery_item(milk, "purchased")
    tools.mark_grocery_item(milk, "needed")
    tools.mark_grocery_item(milk, "purchased")
    assert _places(tools.kitchen_from_this_shop()) == {"fridge": 2}, "the no-double-add guard still holds"
    assert len([r for r in tools.get_inventory() if r["item"].lower() == "milk"]) == 1


def test_a_shop_that_put_nothing_in_the_kitchen_has_no_card():
    tools.add_grocery_item("Lemons", quantity="3", category="produce")
    assert tools.kitchen_from_this_shop() == {"total": 0, "places": [], "use_first": []}


def test_use_these_first_is_the_soonest_three_with_their_plan():
    chicken = _bought("Chicken thighs", "meat/seafood", "2 lb")
    _plan_dinner_for(chicken, 2)
    berries = _bought("Raspberries", "produce", "1 pint")
    _plan_dinner_for(berries, 1, slot="snack")
    _bought("Baby spinach", "produce", "1 bag")
    _bought("Milk", "dairy", "2 l")
    _bought("Frozen peas", "frozen")
    _bought("Jasmine rice", "pantry", "1 bag")
    # Even dated tomorrow, a thing in the freezer is not one to use first.
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET expiration_date = ? WHERE item = 'Frozen peas'", (household_date(1),))
    conn.commit()
    conn.close()

    now = tools.kitchen_from_this_shop()
    first = now["use_first"]
    assert len(first) == 3
    items = [u["item"] for u in first]
    assert "Frozen peas" not in items and "Jasmine rice" not in items, "the freezer and a long keeper are never first"
    assert [u["expires"] for u in first] == sorted(u["expires"] for u in first), "soonest first"
    by_item = {u["item"]: u for u in first}
    on = household_today()
    weekday = (on.fromordinal(on.toordinal() + 2)).strftime("%A")
    assert by_item["Chicken thighs"]["plan"] == f"in {weekday}’s dinner"
    assert by_item["Raspberries"]["plan"].startswith("snacks, by ")
    # A thing no meal was bought for still says when.
    for u in first:
        assert u["plan"].startswith(("in ", "snacks, ", "by ", "today"))


def test_the_route_answers_the_same(signed_in):
    _bought("Milk", "dairy", "2 l")
    res = signed_in.get("/api/grocery-list/now-in-kitchen")
    assert res.status_code == 200
    assert res.json()["total"] == 1 and res.json()["places"] == [{"place": "fridge", "count": 1}]


# --- the card, under node ---------------------------------------------------

_DONE = """
groceryState.data = { stores: { Costco: { sections: [], inCart: [], purchased: [
  { id: 1, item: 'Milk', quantity: '2 l', store: 'Costco', store_decided: 1, category: 'dairy', status: 'purchased' }] } } };
groceryState.usualStores = ['Costco'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'list';
var tonightDinnerName = function () { return null; };
"""


@needs_node
def test_the_card_shows_counts_note_use_first_and_two_actions():
    out = run(_DONE + """
groceryState.kitchenNow = { total: 4, places: [{ place: 'fridge', count: 3 }, { place: 'pantry', count: 1 }],
  use_first: [{ item: 'Baby spinach', plan: 'in Tuesday’s dinner' }, { item: 'Raspberries', plan: 'snacks, by Monday' }] };
var withCard = groListHtml(groceryState.data);
groceryState.kitchenNow = null;
var without = groListHtml(groceryState.data);
groceryState.kitchenNow = undefined;
var waiting = groListHtml(groceryState.data);
console.log(JSON.stringify({ withCard: withCard, without: without, waiting: waiting }));
""")
    html = out["withCard"]
    for text in ("Now in the kitchen", "From what you ticked off. Didn’t get something? Untick it on the list.",
                 "<span>Fridge</span>", "3 things", "<span>Pantry</span>", "1 thing<",
                 "Use these first", "Baby spinach", "in Tuesday’s dinner", "snacks, by Monday",
                 'data-gro="kitchen-see-home">See what’s home</button>',
                 'class="btn-gold" data-gro="shop-done-later">Done</button>'):
        assert text in html, text
    assert "Freezer" not in html, "a place this shop put nothing in is not a row"
    assert "Show me tonight" not in html, "one primary: Done"
    assert "Now in the kitchen" not in out["without"] and "Show me tonight" in out["without"]
    assert "That’s the shopping done." in out["waiting"] and "Show me tonight" not in out["waiting"]


def test_a_thing_already_past_its_date_is_not_one_to_use_first():
    # The card stays up until the next approval, so a dated-yesterday row
    # would otherwise read "today" every day for a week.
    _bought("Chicken thighs", "meat/seafood", "2 lb")
    _bought("Baby spinach", "produce", "1 bag")
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET expiration_date = ? WHERE item = 'Chicken thighs'", (household_date(-3),))
    conn.commit()
    conn.close()

    now = tools.kitchen_from_this_shop()
    assert "Chicken thighs" not in [u["item"] for u in now["use_first"]]
    assert now["total"] == 2, "it still counts as in the kitchen"
