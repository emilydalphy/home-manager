"""
Emily's first approved week, as a test suite.

Sixty items, and the ones that made the list unusable were not wrong by a
little: 18 bell peppers, 13 lemons, 6 bags of baby spinach, 4 bottles of
honey, 4 tubs of hummus, 3 bottles of olive oil, and cottage cheese listed
as "48 oz tubs". Three earlier fixes had already made the grocery list sum
quantities instead of concatenating them, merge singular with plural, and
pluralize container words — all of them correct, and none of them the
problem. The inputs to the sum were wrong.

Two separate causes, both covered here:

  1. approve_weekly_plan ingested one meal at a time. The same breakfast
     planned six mornings called the ingest six times, so "1 bag baby
     spinach" was added six times and summed to six bags. Ingestion is now
     per RECIPE per week, and a sealed package is added once for that
     recipe-week however often the meal repeats.
  2. "48 oz tub" parsed as forty-eight tubs — the size of one package read
     as a count of packages.

The bell peppers are the control: five dinners wanting two to four peppers
each genuinely want the sum, and this suite pins that they still do.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import quantities


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


def _days() -> list[str]:
    return tools._week_dates(_week_start())


def _qty(item: str) -> str | None:
    return next((i["quantity"] for i in tools.list_grocery_list() if i["item"] == item), None)


def _plan(plan_id: int, dates: list[str], meal: str, slot: str) -> None:
    for date in dates:
        tools.plan_meal(date, meal, slot=slot, weekly_plan_id=plan_id)


@pytest.fixture
def week() -> int:
    return tools.create_weekly_plan(_week_start())["weekly_plan_id"]


# ---------- the packages that multiplied ----------

def test_a_breakfast_eaten_six_mornings_buys_one_bag_and_one_bottle(week):
    """
    The headline bug. Emily's spinach-and-honey breakfast was planned six
    mornings and the list asked for six bags of spinach and six bottles of
    honey — one per morning, faithfully summed.
    """
    tools.add_recipe("Green scramble", ingredients=[
        {"item": "Baby spinach", "qty": "1 bag", "category": "produce"},
        {"item": "Honey", "qty": "1 bottle", "category": "pantry"},
        {"item": "Eggs", "qty": "4", "category": "dairy"},
    ])
    _plan(week, _days()[:6], "Green scramble", "breakfast")

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Baby spinach") == "1 bag"
    assert _qty("Honey") == "1 bottle"
    # And the control in the same recipe: eggs are a per-portion count and
    # six breakfasts really do want two dozen.
    assert _qty("Eggs") == "24"


def test_oatmeal_four_mornings_buys_one_bottle_of_honey(week):
    tools.add_recipe("Oatmeal", ingredients=[
        {"item": "Rolled oats", "qty": "1 bag", "category": "pantry"},
        {"item": "Honey", "qty": "1 bottle", "category": "pantry"},
    ])
    _plan(week, _days()[:4], "Oatmeal", "breakfast")

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Honey") == "1 bottle"
    assert _qty("Rolled oats") == "1 bag"


def test_three_different_dinners_listing_olive_oil_buy_one_bottle(week):
    """
    The cross-recipe half of the same rule. Nothing repeats here — three
    separate dinners each honestly list a bottle of oil, and the household
    still wants one bottle.
    """
    for name in ("Stir fry", "Roast chicken", "Pasta"):
        tools.add_recipe(name, ingredients=[
            {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        ])
    days = _days()
    for date, name in zip(days[:3], ("Stir fry", "Roast chicken", "Pasta")):
        tools.plan_meal(date, name, slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Olive oil") == "1 bottle"


def test_a_recipe_that_really_wants_two_bottles_still_gets_two(week):
    """
    "One per week" is a floor, not a cap: a recipe that names more than one
    package wins over the recipes beside it that name one.
    """
    tools.add_recipe("Fry up", ingredients=[
        {"item": "Olive oil", "qty": "2 bottles", "category": "pantry"}])
    tools.add_recipe("Salad", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"}])
    days = _days()
    tools.plan_meal(days[0], "Fry up", slot="dinner", weekly_plan_id=week)
    tools.plan_meal(days[1], "Salad", slot="lunch", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Olive oil") == "2 bottles"


# ---------- the amounts that are supposed to add up ----------

def test_five_dinners_of_peppers_still_add_up(week):
    """
    The control, and the reason this fix is narrow. Whole peppers are a
    per-portion amount: five dinners wanting 2-4 each want seventeen. That
    is a real number, not an artefact — if Emily decides seventeen peppers
    is still too many to face, the answer is a cap on the total, not a
    change here.
    """
    amounts = ["3", "4", "2", "4", "4"]
    for index, amount in enumerate(amounts):
        tools.add_recipe(f"Peppers {index}", ingredients=[
            {"item": "Bell peppers", "qty": amount, "category": "produce"}])
    for date, index in zip(_days()[:5], range(5)):
        tools.plan_meal(date, f"Peppers {index}", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Bell peppers") == "17"


def test_a_per_meal_produce_unit_is_not_treated_as_a_weeks_supply(week):
    """
    A bunch of cilantro is not a bottle of oil. The package rule covers
    sealed staples only — anything a meal genuinely uses up keeps summing.
    """
    tools.add_recipe("Tacos", ingredients=[
        {"item": "Cilantro", "qty": "1 bunch", "category": "produce"},
        {"item": "Black beans", "qty": "1 tin", "category": "pantry"},
    ])
    _plan(week, _days()[:3], "Tacos", "dinner")

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Cilantro") == "3 bunches"
    assert _qty("Black beans") == "3 tins"


# ---------- "48 oz tubs" ----------

@pytest.mark.parametrize("written,reads_as", [
    ("48 oz tub", "1 tub (48 oz)"),
    ("12 oz can", "1 can (12 oz)"),
    ("1 lb bag", "1 bag (1 lb)"),
    ("500 g bag", "1 bag (500 g)"),
])
def test_a_sized_package_is_one_package_not_that_many(written, reads_as):
    """
    The number in "48 oz tub" is the size of one tub. Read as a count it
    became "48 oz tubs" on Emily's list, and four planned lunches summed it
    to "192 oz tubs".
    """
    assert quantities._normalize_grocery_quantity(written) == reads_as
    assert quantities._parse_quantity(written)[0] == 1.0


def test_a_counted_sized_package_round_trips():
    """Formatting and parsing have to agree, or a merged line stops merging
    the next time something is added to it — the concatenation bug all over
    again (see _normalize_container_word)."""
    unit = quantities._parse_quantity("48 oz tub")[1]
    assert quantities._format_quantity(3, unit) == "3 tubs (48 oz)"
    assert quantities._parse_quantity("3 tubs (48 oz)") == (3.0, unit)


def test_cottage_cheese_reads_like_something_you_can_buy(week):
    tools.add_recipe("Cottage bowl", ingredients=[
        {"item": "Cottage cheese", "qty": "48 oz tub", "category": "dairy"}])
    _plan(week, _days()[:4], "Cottage bowl", "lunch")

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Cottage cheese") == "1 tub (48 oz)"


# ---------- the ledger stays symmetric ----------

def test_clearing_the_week_takes_back_exactly_what_approving_added(week):
    """
    The invariant that makes the package rule safe to hold a line on the
    list: approve then clear must leave nothing behind. A package is shared
    by every meal that named it, so it comes off with the last of them.
    """
    tools.add_recipe("Green scramble", ingredients=[
        {"item": "Baby spinach", "qty": "1 bag", "category": "produce"},
        {"item": "Honey", "qty": "1 bottle", "category": "pantry"},
        {"item": "Eggs", "qty": "4", "category": "dairy"},
    ])
    tools.add_recipe("Stir fry", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        {"item": "Bell peppers", "qty": "3", "category": "produce"},
    ])
    _plan(week, _days()[:6], "Green scramble", "breakfast")
    _plan(week, _days()[:3], "Stir fry", "dinner")
    tools.approve_weekly_plan(week, approved_by="Emily")
    assert tools.list_grocery_list() != []

    tools.clear_weekly_plan(week)

    assert tools.list_grocery_list() == []


def test_swapping_one_meal_leaves_the_bottle_the_other_meals_still_need(week):
    """
    The other half of the same invariant. One bottle of oil is on the list
    because three dinners want it; changing one of those dinners must not
    take it away from the two that remain.
    """
    tools.add_recipe("Stir fry", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        {"item": "Bell peppers", "qty": "3", "category": "produce"},
    ])
    tools.add_recipe("Soup", ingredients=[{"item": "Carrots", "qty": "4", "category": "produce"}])
    days = _days()
    _plan(week, days[:3], "Stir fry", "dinner")
    tools.approve_weekly_plan(week, approved_by="Emily")
    assert _qty("Bell peppers") == "9"

    tools.swap_meal_in_plan(week, days[0], "Soup", slot="dinner")

    assert _qty("Olive oil") == "1 bottle", "two dinners still need the oil"
    assert _qty("Bell peppers") == "6", "only the swapped meal's peppers came off"


def test_swapping_the_last_meal_that_wanted_it_takes_the_bottle_off(week):
    tools.add_recipe("Stir fry", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"}])
    tools.add_recipe("Soup", ingredients=[{"item": "Carrots", "qty": "4", "category": "produce"}])
    days = _days()
    tools.plan_meal(days[0], "Stir fry", slot="dinner", weekly_plan_id=week)
    tools.approve_weekly_plan(week, approved_by="Emily")

    tools.swap_meal_in_plan(week, days[0], "Soup", slot="dinner")

    assert _qty("Olive oil") is None


def test_a_package_someone_asked_for_directly_survives_the_plan_going_away(week):
    """
    A hand-added line is a standing want, and the plan only ever borrowed
    it. Clearing the week must not take Emily's own bottle of olive oil off
    her list just because a recipe happened to name one too.
    """
    tools.add_grocery_item("Olive oil", quantity="1 bottle", category="pantry")
    tools.add_recipe("Stir fry", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"}])
    _plan(week, _days()[:2], "Stir fry", "dinner")
    tools.approve_weekly_plan(week, approved_by="Emily")

    tools.clear_weekly_plan(week)

    assert _qty("Olive oil") == "1 bottle"


def test_a_package_already_in_the_cart_is_never_yanked_out(week):
    tools.add_recipe("Stir fry", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"}])
    _plan(week, _days()[:2], "Stir fry", "dinner")
    tools.approve_weekly_plan(week, approved_by="Emily")
    oil = next(i for i in tools.list_grocery_list() if i["item"] == "Olive oil")
    tools.mark_grocery_item(oil["id"], "in_cart")

    tools.clear_weekly_plan(week)

    conn = get_conn()
    row = conn.execute("SELECT status FROM grocery_items WHERE id = ?", (oil["id"],)).fetchone()
    conn.close()
    assert row["status"] == "in_cart"


def test_every_contributing_meal_is_recorded_against_the_package(week):
    """
    The ledger is what makes the two tests above work, so pin its shape:
    a package added once still gets a link row per meal that wanted it.
    """
    tools.add_recipe("Green scramble", ingredients=[
        {"item": "Baby spinach", "qty": "1 bag", "category": "produce"}])
    _plan(week, _days()[:6], "Green scramble", "breakfast")
    tools.approve_weekly_plan(week, approved_by="Emily")

    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM meal_plan_grocery_links WHERE item = 'Baby spinach'"
    ).fetchall()
    conn.close()
    assert len(rows) == 6
    assert {r["quantity"] for r in rows} == {"1 bag"}


# ---------- attendance still decides how much of the per-portion stuff ----------

def test_headcount_still_scales_the_per_portion_amounts(week):
    """
    Grouping the ingest by recipe must not flatten attendance: each meal is
    still scaled by its own table before its share is added, so a night one
    of two people is home still buys for one.
    """
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    days = _days()
    tools.set_member_attendance(days[1], "dinner", "Vineeth", present=False)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "4 cups"}])
    _plan(week, days[:2], "Chili", "dinner")

    tools.approve_weekly_plan(week, approved_by="Emily")

    # 4 cups for the full table plus 2 for the solo night.
    assert _qty("beans") == "6 cups"


def test_headcount_does_not_split_a_package(week):
    """Half a table still buys one whole bottle — you cannot buy half a jar."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    days = _days()
    tools.set_member_attendance(days[0], "dinner", "Vineeth", present=False)
    tools.add_recipe("Stir fry", ingredients=[
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"}])
    tools.plan_meal(days[0], "Stir fry", slot="dinner", weekly_plan_id=week)

    tools.approve_weekly_plan(week, approved_by="Emily")

    assert _qty("Olive oil") == "1 bottle"
