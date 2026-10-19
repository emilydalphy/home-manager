"""
Spices this week totals the week (Loop Board, High, 2026-10-18 — Emily:
"the recipe should be providing measurements, and so we should have a
total of tsp/TBSPs that are needed for everything ... for the salt, even if
it's a pinch, research how much a pinch would be as a measurement").

The row SHOWS the measured total ("Ground cumin · 2½ tsp"); a tick still
BUYS one store unit ("1 jar"), two only past what one holds. Pinch 1/16 tsp,
dash 1/8, smidgen 1/32; "to taste" salt 1/4 tsp and pepper 1/8 tsp per
serving, marked "about". A package on a spice line is never a recipe's
amount: replaced on save, read as "a little" (1/4 tsp) on a recipe already
on disk.
"""
from __future__ import annotations

import datetime
import json

import pytest
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import recipes as _recipes
from app.tools import spices as _spices


def _monday():
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


def _d(n):
    return (_monday() + datetime.timedelta(days=n)).isoformat()


def _plan():
    tools.add_member("Emily")
    tools.add_member("Jamie")
    return tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]


def _rows():
    return {sp["item"]: sp for sp in tools.list_spices_this_week()["items"]}


# ---------- the conversions ----------

@pytest.mark.parametrize("qty, servings, tsp, about", [
    ("a pinch", None, 1 / 16, True),
    ("2 pinches", None, 1 / 8, True),
    ("1 dash", None, 1 / 8, True),
    ("a smidgen", None, 1 / 32, True),
    ("to taste", 4, 1.0, True),           # salt: 1/4 tsp a serving
    ("", 4, 1.0, True),                   # nothing said is "to taste"
    ("1 1/2 tbsp", None, 4.5, False),
    ("1/4 cup", None, 12.0, False),
    ("1 box", None, 0.25, True),          # an old recipe's package: a little
])
def test_salt_measures_convert(qty, servings, tsp, about):
    assert _spices.spice_measure("Salt", qty, servings) == pytest.approx((tsp, about))


def test_pepper_to_taste_is_an_eighth_a_serving_and_other_spices_a_little():
    assert _spices.spice_measure("Black pepper", "to taste", 4) == (0.5, True)
    assert _spices.spice_measure("Salt and pepper", "to taste", 4) == (1.5, True)
    assert _spices.spice_measure("Ground cumin", "to taste", 4) == (0.25, True)
    assert _spices.spice_measure("Bay leaves", "2", 4) == (None, False)


@pytest.mark.parametrize("tsp, about, item, text", [
    (2.5, False, "Ground cumin", "2½ tsp"),
    (4.5, True, "Salt", "about 1½ tbsp"),
    (32, False, "Olive oil", "⅔ cup"),
    (12, False, "Olive oil", "4 tbsp"),        # cups only PAST 4 tbsp
    (30, False, "Paprika", "10 tbsp"),         # a spice stays in tbsp
    (7 / 16, True, "Flaky salt", "about ½ tsp"),
    (1 / 16, True, "Salt", "a pinch"),
])
def test_the_total_reads_the_way_a_cook_measures(tsp, about, item, text):
    assert _spices.measure_text(tsp, about, item) == text


# ---------- the week's total on the row ----------

def test_a_week_sums_a_batch_dinner_and_seven_snacks():
    """The card's week: a pinch of flaky salt on seven snacks, and a
    Mon–Wed batch (one cook, three nights for two) salted to taste and
    measured in cumin. Totals off the meals, batch scaling included; the
    line buys one box and one jar."""
    plan_id = _plan()
    tools.add_recipe("Orange Slices", default_servings=2, ingredients=[
        {"item": "Oranges", "qty": "2", "category": "produce"},
        {"item": "Flaky salt", "qty": "a pinch", "category": "pantry"},
    ])
    tools.add_recipe("Chili", default_servings=2, ingredients=[
        {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Ground cumin", "qty": "1 tsp", "category": "pantry"},
        {"item": "Kosher salt", "qty": "to taste", "category": "pantry"},
    ])
    for n in range(7):
        tools.plan_meal(_d(n), "Orange Slices", slot="snack", weekly_plan_id=plan_id)
    ids = [tools.plan_meal(_d(n), "Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"] for n in (0, 1, 2)]
    tools.set_cook_ahead(ids[0], ids[1:])
    tools.approve_weekly_plan(plan_id, "Emily")
    rows = _rows()
    # 7 pinches = 7/16 tsp, not 7 boxes.
    assert rows["Flaky salt"]["total"] == "about ½ tsp"
    assert rows["Flaky salt"]["quantity"] == "1 box"
    # One pot for six portions: 3 tsp of cumin, 6 x 1/4 tsp of salt.
    assert rows["Ground cumin"]["total"] == "1 tbsp"
    assert rows["Ground cumin"]["quantity"] == "1 jar"
    assert rows["Kosher salt"]["total"] == "about 1½ tsp"


def test_a_recipe_already_saved_with_a_jar_never_sums_as_jars():
    """A recipe on disk from before this, shopping cumin by the jar: three
    dinners read "about ¾ tsp" on the row and buy one jar — never 3 jars,
    never a number the recipe didn't give."""
    plan_id = _plan()
    tools.add_recipe("Old Curry", default_servings=2, ingredients=[{"item": "Rice", "qty": "1 cup"}])
    conn = get_conn()
    conn.execute(
        "UPDATE recipes SET ingredients_json = ? WHERE name = 'Old Curry'",
        (json.dumps([{"item": "Rice", "qty": "1 cup", "category": "pantry"},
                     {"item": "Ground cumin", "qty": "1 jar", "category": "pantry"}]),),
    )
    conn.commit()
    conn.close()
    for n in range(3):
        tools.plan_meal(_d(n), "Old Curry", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    row = _rows()["Ground cumin"]
    assert row["quantity"] == "1 jar"
    assert row["total"] == "about ¾ tsp"


def test_a_tick_buys_one_jar_and_two_past_what_a_jar_holds():
    plan_id = _plan()
    tools.add_recipe("Spice Rub", default_servings=2, ingredients=[
        {"item": "Smoked paprika", "qty": "4 tsp", "category": "pantry"},
        {"item": "Ground cumin", "qty": "1/2 tsp", "category": "pantry"},
    ])
    for n in range(7):
        tools.plan_meal(_d(n), "Spice Rub", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    rows = _rows()
    tools.tick_spice(rows["Smoked paprika"]["id"])
    tools.tick_spice(rows["Ground cumin"]["id"])
    needed = {i["item"]: i["quantity"] for i in tools.list_grocery_list()}
    assert needed["Ground cumin"] == "1 jar"
    assert needed["Smoked paprika"] == "2 jars", "28 tsp is more than a ½-cup jar holds"
    rows = _rows()
    assert rows["Ground cumin"]["total"] == "1 tbsp", "the measured total stays on the row"
    assert rows["Smoked paprika"]["total"] == "9½ tbsp"


def test_the_cooks_measure_is_what_an_old_jar_line_totals():
    """An old recipe shopping paprika by the jar whose cook amount is
    measured: the row totals the cook's teaspoons, exactly (no "about")."""
    plan_id = _plan()
    tools.add_recipe("Old Stew", default_servings=2, ingredients=[{"item": "Rice", "qty": "1 cup"}])
    conn = get_conn()
    conn.execute(
        "UPDATE recipes SET ingredients_json = ? WHERE name = 'Old Stew'",
        (json.dumps([{"item": "Paprika", "qty": "1 jar", "cook_qty": "1 tsp", "category": "pantry"}]),),
    )
    conn.commit()
    conn.close()
    for n in range(2):
        tools.plan_meal(_d(n), "Old Stew", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _rows()["Paprika"]["total"] == "2 tsp"
    assert _rows()["Paprika"]["quantity"] == "1 jar"


# ---------- recipes carry measures ----------

def test_a_package_on_a_spice_the_model_wrote_is_saved_as_a_measure():
    """A door where the model wrote the recipe (measure_spices)."""
    saved = tools.add_recipe("Roast", default_servings=4, measure_spices=True, ingredients=[
        {"item": "Ground cumin", "qty": "1 jar", "category": "pantry"},
        {"item": "Paprika", "qty": "1 jar", "cook_qty": "2 tsp", "category": "pantry"},
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        {"item": "Soy sauce", "qty": "1 bottle", "category": "pantry"},
        {"item": "Canola oil, for frying", "qty": "2 bottles", "category": "pantry"},
        {"item": "Vegetable oil for frying", "qty": "1 bottle", "category": "pantry"},
        {"item": "Cumin seeds", "qty": "2 jars", "category": "pantry"},
    ])
    assert saved["recipe_id"]
    by = {i["item"]: i for i in _recipes.saved_ingredients("Roast")}
    # No cook amount: the Cook screen's own (cooking_quantity's table); the
    # package written is kept beside it, never thrown away.
    assert by["Ground cumin"]["qty"] == "1 tsp" and by["Ground cumin"]["package_qty"] == "1 jar"
    assert by["Paprika"]["qty"] == "2 tsp"
    assert by["Olive oil"]["qty"] == "2 tbsp"
    assert by["Soy sauce"]["qty"] == "1 bottle", "a condiment is still bought by the bottle"
    # Never rewritten: a frying oil, and a package count above one.
    assert by["Canola oil, for frying"]["qty"] == "2 bottles"
    assert by["Vegetable oil for frying"]["qty"] == "1 bottle"
    assert by["Cumin seeds"]["qty"] == "2 jars"


def test_a_typed_or_imported_recipe_says_what_it_says():
    """A person's recipe, a page's, a book's: no table defaults (review,
    2026-10-19 — a salt crust's box is the box)."""
    tools.add_recipe("Salt Crust Fish", default_servings=4, ingredients=[
        {"item": "Kosher salt", "qty": "1 box", "category": "pantry"},
        {"item": "Vanilla extract", "qty": "1 bottle", "category": "pantry"},
    ])
    tools.add_recipe("Page Curry", default_servings=4, source_url="https://example.com/curry", ingredients=[
        {"item": "Ground cumin", "qty": "1 jar", "category": "pantry"},
    ])
    fish = {i["item"]: i["qty"] for i in _recipes.saved_ingredients("Salt Crust Fish")}
    assert fish == {"Kosher salt": "1 box", "Vanilla extract": "1 bottle"}
    assert _recipes.saved_ingredients("Page Curry")[0]["qty"] == "1 jar"
    # Saving cooking amounts onto it later doesn't rewrite its shopping qty.
    tools.save_cooking_quantities("Salt Crust Fish", {"Vanilla extract": "1 tsp"})
    fish = {i["item"]: i for i in _recipes.saved_ingredients("Salt Crust Fish")}
    assert fish["Vanilla extract"]["qty"] == "1 bottle" and fish["Vanilla extract"]["cook_qty"] == "1 tsp"
    assert fish["Kosher salt"]["qty"] == "1 box"


# ---------- a household's own count is a floor ----------

def _needed():
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list()}


def test_a_households_two_jars_survive_a_plans_jar_landing_on_them():
    plan_id = _plan()
    tools.add_grocery_item("Ground cumin", "2 jars", category="pantry")
    tools.add_recipe("Old Curry", default_servings=2, ingredients=[
        {"item": "Ground cumin", "qty": "1 jar", "category": "pantry"},
    ])
    tools.plan_meal(_d(0), "Old Curry", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _needed()["Ground cumin"] == "2 jars"


def test_typing_two_jars_onto_the_plans_jar_keeps_two():
    plan_id = _plan()
    tools.add_recipe("Curry", default_servings=2, ingredients=[
        {"item": "Ground cumin", "qty": "1 tsp", "category": "pantry"},
    ])
    tools.plan_meal(_d(0), "Curry", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _rows()["Ground cumin"]["quantity"] == "1 jar"
    tools.add_grocery_item("ground cumin", "2 jars", category="pantry")
    assert _needed()["Ground cumin"] == "2 jars"


# ---------- frying oil: as many bottles as it takes ----------

def test_six_cups_of_frying_oil_buy_three_bottles_and_one_at_home_does_not_cover_it():
    plan_id = _plan()
    tools.update_inventory("Vegetable oil for frying", "add", quantity="1 bottle", category="pantry")
    tools.add_recipe("Fried Chicken", default_servings=2, ingredients=[
        {"item": "Chicken thighs", "qty": "2 lbs", "category": "meat/seafood"},
        {"item": "Vegetable oil for frying", "qty": "6 cups", "category": "pantry"},
    ])
    tools.plan_meal(_d(0), "Fried Chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    row = _rows()["Vegetable oil for frying"]
    assert row["quantity"] == "3 bottles"
    assert row["total"] == "6 cups"


def test_one_bottle_at_home_still_covers_a_weeks_spoonfuls():
    plan_id = _plan()
    tools.update_inventory("Olive oil", "add", quantity="1 bottle", category="pantry")
    tools.add_recipe("Salad", default_servings=2, ingredients=[
        {"item": "Lettuce", "qty": "1 head", "category": "produce"},
        {"item": "Olive oil", "qty": "2 tbsp", "category": "pantry"},
    ])
    tools.plan_meal(_d(0), "Salad", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert "Olive oil" not in _rows()


def test_frying_oil_reads_the_same_with_or_without_the_comma():
    assert _spices.is_spice("Vegetable oil for frying") and _spices.is_spice("Canola oil, for frying")
    assert _spices.store_unit("Canola oil, for frying", 6 * 48) == "3 bottles"
    assert _spices.store_unit("Ground cumin", 0.25) == "1 jar"
