"""
The kitchen only takes an ingredient off the shopping list when it can be
SHOWN to cover the amount the week needs (Loop Board bug, reproduced over
HTTP 2026-09-14).

Before this, recipes._add_recipe_ingredients_for_entries asked one
question of inventory — "is this name in there with a non-blank quantity?"
— and the quantity was selected on and then thrown away. Two ounces of
chicken thighs took two POUNDS of them off the list. Worse in ordinary
use: shopping a week normally ticks every line purchased, which writes an
inventory row per line, so the NEXT week's approval skipped almost
everything it had ever bought.

The rule now lives in recipes._KitchenStock: skip only when the tracked
quantities, read in the unit the shopping line would be written in, add up
to at least what that line would say. Everything else is bought — a
freeform quantity on either side, a package against a measured amount, two
unit families that don't convert. This module's standing bias, stated at
_PACKAGE_UNITS: an extra line beats a missing dinner.

Every test below that names an amount FAILS on the parent commit
(2120af5), where no amount was ever compared. The few that are guards
rather than catches say so in their own docstrings.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn


def _recipe(name: str, ingredients: list[dict], servings: int = 4) -> None:
    tools.add_recipe(
        name=name,
        ingredients=ingredients,
        instructions=["Cook it."],
        default_servings=servings,
    )


def _approve_week(nights: list[tuple[str, str]], week_start: str | None = None) -> dict:
    """Plan `nights` — (date, recipe name) — as one week and approve it."""
    monday = week_start or (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    plan_id = tools.create_weekly_plan(monday)["weekly_plan_id"]
    for meal_date, meal in nights:
        tools.plan_meal(meal_date, meal, "dinner", weekly_plan_id=plan_id)
    return tools.approve_weekly_plan(plan_id, approved_by="Julia")


def _next_week_dates(n: int = 2) -> list[str]:
    monday = datetime.date.today() + datetime.timedelta(days=14)
    return [(monday + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def _on_list(item: str) -> str | None:
    """The quantity of `item` on the list, or None if it isn't on it."""
    conn = get_conn()
    row = conn.execute(
        "SELECT quantity FROM grocery_items WHERE household_id = ? AND LOWER(item) = ? "
        "AND status != 'purchased' ORDER BY id LIMIT 1",
        (tools.household_id(), item.lower()),
    ).fetchone()
    conn.close()
    return row["quantity"] if row else None


# ---------------------------------------------------------------- the bug

def test_two_ounces_on_hand_does_not_take_two_pounds_off_the_list():
    """THE REPORTED BUG. Fails on 2120af5, where the name alone was enough."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="2 oz", category="meat/seafood")

    day = _next_week_dates(1)[0]
    result = _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is not None
    assert "Chicken thighs" not in result["already_have_skipped"]


def test_three_pounds_on_hand_does_take_two_pounds_off_the_list():
    """The other half of the rule, and the half that must not regress:
    enough really is enough. GREEN on 2120af5 too — a no-regression guard,
    not a catch."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="3 lbs", category="meat/seafood")

    day = _next_week_dates(1)[0]
    result = _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is None
    assert "Chicken thighs" in result["already_have_skipped"]


def test_exactly_enough_is_enough():
    """GREEN on 2120af5 (which skipped everything it recognised) — a
    boundary guard rather than a catch: it is where a `>` instead of a
    `>=` would hide, and it fails against that mutation."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat/seafood")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is None


def test_a_hair_short_is_still_short():
    """Fails on 2120af5."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="1.75 lbs", category="meat/seafood")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is not None


# ------------------------------------------------- the week, not one night

def test_the_amount_compared_is_the_whole_weeks_not_one_nights():
    """Three dinners of a 1-lb recipe want 3 lbs; 2 lbs on hand is not
    enough, even though it covers any ONE of them. Fails on 2120af5."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat/seafood")

    days = _next_week_dates(3)
    _approve_week([(d, "Sheet Pan Chicken") for d in days])

    assert _on_list("Chicken thighs") is not None


def test_two_recipes_cannot_each_be_told_about_the_same_two_pounds():
    """The stock is claimed as it is spent, so the second recipe-week group
    of one approval sees what the first one already took. Fails on
    2120af5 (both skipped there — and both would skip without the claim
    ledger too, which is what that ledger is for)."""
    _recipe("Chicken One", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    _recipe("Chicken Two", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat/seafood")

    a, b = _next_week_dates(2)
    _approve_week([(a, "Chicken One"), (b, "Chicken Two")])

    # One group's worth was genuinely at home; the other's was not.
    assert _on_list("Chicken thighs") is not None


# -------------------------------------------- what cannot be reconciled

def test_a_freeform_quantity_on_the_shelf_is_never_a_reason_not_to_buy():
    """"Some left" is not an amount. Fails on 2120af5."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="some left", category="meat/seafood")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is not None


def test_a_freeform_quantity_in_the_recipe_is_never_covered():
    """A recipe that declines to say how much names no amount to be
    covered, so it is bought. Fails on 2120af5, where any tracked row was
    enough."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "a handful", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="3 lbs", category="meat/seafood")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is not None


def test_a_package_on_the_shelf_does_not_cover_a_measured_amount():
    """A bag is not a cup. Fails on 2120af5."""
    _recipe("Bean Chili", [{"item": "Black beans", "qty": "2 cups", "category": "pantry"}])
    tools.update_inventory("Black beans", "add", quantity="1 bag", category="pantry")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Bean Chili")])

    assert _on_list("Black beans") is not None


def test_a_different_unit_family_does_not_cover_it():
    """Millilitres against pounds is not a comparison. Fails on 2120af5."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="900 ml", category="meat/seafood")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is not None


def test_units_in_the_same_family_do_convert():
    """40 oz on the shelf covers 2 lbs in the week. GREEN on 2120af5 — a
    guard, and the one that bites a fix which compares raw numbers without
    converting: 40 is not less than 2, but 2.5 lbs really does cover 2."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="40 oz", category="meat/seafood")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is None


def test_a_package_still_covers_the_same_package():
    """One bottle of oil at home is still one bottle of oil not bought —
    the ordinary sealed-package case has to keep working. GREEN on
    2120af5: a no-regression guard."""
    _recipe("Sheet Pan Chicken", [{"item": "Olive oil", "qty": "1 bottle", "category": "pantry"}])
    tools.update_inventory("Olive oil", "add", quantity="1 bottle", category="pantry")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Olive oil") is None


# ------------------------------------------- rows for one name are summed

def test_two_rows_of_the_same_food_add_up():
    """The opened jar in the fridge and the unopened one in the pantry are
    two rows of one food, and "have we enough" is about the food. GREEN on
    2120af5 — a guard, and the one that pins the SUM decision: pick one row
    instead and neither 1 lb nor 1.5 lbs covers the 2 lbs, so it reddens."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="1 lb", category="meat/seafood", location="fridge")
    tools.update_inventory("Chicken thighs", "add", quantity="1.5 lbs", category="meat/seafood", location="freezer")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is None


def test_one_unreadable_row_makes_the_whole_name_unknown():
    """Three pounds beside "a bit left" is a total nobody can state, so it
    is bought. Fails on 2120af5."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="3 lbs", category="meat/seafood", location="freezer")
    tools.update_inventory("Chicken thighs", "add", quantity="a bit left", category="meat/seafood", location="fridge")

    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is not None


# ------------------------------------------------------ the reported shape

def test_shopping_one_week_does_not_empty_the_next_weeks_list():
    """The shape Julia actually hit: approve a week, tick every line
    purchased (which is what "Done at Costco" does, and it writes one
    inventory row per line), then approve the next week — whose recipes
    want MORE of the same things. Before this, week two's list was three
    items. Fails on 2120af5."""
    _recipe("Week One Chicken", [
        {"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Rice", "qty": "2 cups", "category": "pantry"},
    ])
    _recipe("Week Two Chicken", [
        {"item": "Chicken thighs", "qty": "3 lbs", "category": "meat/seafood"},
        {"item": "Rice", "qty": "6 cups", "category": "pantry"},
    ])

    today = datetime.date.today()
    week_one = (today + datetime.timedelta(days=14)).isoformat()
    week_two = (today + datetime.timedelta(days=28)).isoformat()

    _approve_week([(week_one, "Week One Chicken")], week_start=week_one)

    # Shop it: every needed line ticked purchased, which writes inventory.
    conn = get_conn()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM grocery_items WHERE household_id = ? AND status = 'needed'",
        (tools.household_id(),),
    ).fetchall()]
    conn.close()
    assert ids, "week one put nothing on the list to shop"
    for item_id in ids:
        tools.mark_grocery_item(item_id, "purchased")

    _approve_week([(week_two, "Week Two Chicken")], week_start=week_two)

    assert _on_list("Chicken thighs") is not None
    assert _on_list("Rice") is not None


# ----------------------------------------------------------- housekeeping

def test_a_skipped_line_is_still_reported_as_already_have():
    """The receipt's "already_have_skipped" keeps meaning what it meant.
    GREEN on 2120af5: a no-regression guard."""
    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    tools.update_inventory("Chicken thighs", "add", quantity="5 lbs", category="meat/seafood")

    day = _next_week_dates(1)[0]
    result = _approve_week([(day, "Sheet Pan Chicken")])

    assert result["already_have_skipped"] == ["Chicken thighs"]


def test_another_households_kitchen_is_not_read():
    """Household isolation, which the query has always had and must keep.
    GREEN on 2120af5: a no-regression guard."""
    from app import households

    other = households.create_household("Beta House", "beta-passphrase-xyz")
    with tools.use_household(other):
        tools.update_inventory("Chicken thighs", "add", quantity="50 lbs", category="meat/seafood")

    _recipe("Sheet Pan Chicken", [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"}])
    day = _next_week_dates(1)[0]
    _approve_week([(day, "Sheet Pan Chicken")])

    assert _on_list("Chicken thighs") is not None


# ------------------------------------------------------ the rule in isolation

def test_the_stock_claims_only_what_it_grants():
    """Unit-level, on _KitchenStock itself, and it pins the claim ledger
    from both sides: the refused 5 lb ask spends nothing (or the 2 lb ask
    after it could not be granted), and the granted 2 lb ask spends
    everything (or the 0.5 lb ask after it would be). Fails on 2120af5,
    which has no such class."""
    from app.tools import recipes as _recipes

    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat/seafood")
    conn = get_conn()
    stock = _recipes._KitchenStock(conn)
    conn.close()

    assert stock.covers("Chicken thighs", (5.0, "lb")) is False
    assert stock.covers("Chicken thighs", (2.0, "lb")) is True
    assert stock.covers("Chicken thighs", (0.5, "lb")) is False
