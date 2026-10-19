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

def test_a_package_on_a_spice_is_never_saved_as_its_amount():
    saved = tools.add_recipe("Roast", default_servings=4, ingredients=[
        {"item": "Ground cumin", "qty": "1 jar", "category": "pantry"},
        {"item": "Paprika", "qty": "1 jar", "cook_qty": "2 tsp", "category": "pantry"},
        {"item": "Olive oil", "qty": "1 bottle", "category": "pantry"},
        {"item": "Soy sauce", "qty": "1 bottle", "category": "pantry"},
    ])
    assert saved["recipe_id"]
    by = {i["item"]: i for i in _recipes.saved_ingredients("Roast")}
    # No cook amount: the Cook screen's own (cooking_quantity's table).
    assert by["Ground cumin"]["qty"] == "1 tsp"
    assert by["Paprika"]["qty"] == "2 tsp" and "cook_qty" not in by["Paprika"]
    assert by["Olive oil"]["qty"] == "2 tbsp"
    assert by["Soy sauce"]["qty"] == "1 bottle", "a condiment is still bought by the bottle"
