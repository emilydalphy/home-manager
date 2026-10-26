"""
The shared recipe library's dishes, through the app's own checks (defect
hunt, 2026-10-23, on the 594 dishes in data/recipe_library_batches/).

1. A library dish is COPIED for a household (agent._copy_from_library) with
   no writer told about its allergies, so the allergen gate's alias table is
   the only thing standing between them. A celiac or gluten-free household
   was copied Smash Burgers on hamburger buns, a Philly Cheesesteak on hoagie
   rolls, Banh Mi on a baguette and Minestrone with ditalini — 42 library
   dishes in all; a soy-allergic one, Kimchi Fried Rice with gochujang.
2. A spice the list didn't know sat in a store aisle reading "½ tsp" —
   Kashmiri chili powder on 46 library dishes, kasuri methi on 21,
   asafoetida, gochugaru, chaat masala, Aleppo pepper — where cumin beside
   it read "1 jar" in Spices this week. 92 dishes put a spoonful nobody can
   buy on the shopping list.
3. The cook card read "Tuna in olive oil" as olive oil and showed Salade
   Nicoise's two cans of tuna as "2 tbsp" (and Empanada Gallega's three).
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import re

import pytest

from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.tools import allergen_gate
from app.tools import recipes
from app.tools import recipe_library as lib
from app.tools import spices

BATCHES = os.path.join(os.path.dirname(__file__), "..", "data", "recipe_library_batches")


def _entry(key):
    with open(os.path.join(BATCHES, f"{key}.json")) as f:
        return json.load(f)


def _file(key):
    e = _entry(key)
    lib.file_entry(e["dish_name"], **{**{k: v for k, v in e.items() if k != "dish_name"}, "origin": "seed"})
    return e["dish_name"]


def _copied(name):
    tools.add_recipe(name=name, ingredients=[], details_pending=True, cuisine="")
    recipe = {**tools.get_recipe(name), "slot": "dinner"}
    out, clash = agent._copy_from_library(recipe, name, name, allergen_gate.hard_avoidances(), {})
    return bool(out), clash


# ---------- 1. the gate on what a library copy carries ----------

@pytest.mark.parametrize("restriction", ["gluten", "Celiac", "gluten-free", "wheat"])
@pytest.mark.parametrize("key", [
    "smash-burgers", "philly-cheesesteak", "banh-mi", "minestrone", "chicken-torta",
    "egg-and-cheese-breakfast-sandwiches", "paneer-tikka-wraps", "quiche-lorraine",
    "lentil-sambusa", "tamale-pie", "tomato-and-egg-stir-fry", "bucatini-all-amatriciana",
])
def test_a_library_dish_made_with_wheat_is_never_copied_for_a_gluten_avoiding_household(restriction, key):
    """CATCH: each of these was copied, bread and all."""
    name = _file(key)
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", [restriction])
    copied, clash = _copied(name)
    assert not copied and clash, f"{name} was copied for a {restriction!r} household"


def test_rice_noodles_and_corn_tortillas_still_copy_for_a_celiac_household():
    """GUARD: Bun Cha is rice vermicelli, and its name is not a bun."""
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["Celiac"])
    assert _copied(_file("bun-cha")) == (True, False)
    assert allergen_gate.hard_clashes("Tacos", [{"item": "Corn tortillas", "qty": "8"}]) == []
    assert allergen_gate.hard_clashes("Cold Rice Bowl", [{"item": "Rice vermicelli", "qty": "8 oz"}]) == []
    assert allergen_gate.hard_clashes("Soup", [{"item": "Vermicelli", "qty": "1/2 cup"}])


def test_gochujang_is_soy():
    """CATCH: Kimchi Fried Rice went to a soy-allergic household."""
    name = _file("kimchi-fried-rice")
    tools.add_member("Ravi")
    tools.set_member_dietary_restrictions("Ravi", ["soy"])
    assert _copied(name) == (False, True)


# ---------- 2. a library spice is a jar on the list ----------

def test_every_spoon_measured_pantry_line_in_the_library_waits_at_home():
    """GUARD over the whole library: a pantry line written in tsp/tbsp/
    pinch is a rack, sauce or pantry thing (the list buys it by the jar),
    never a store-aisle line reading "½ tsp". Butter and milk are dairy,
    measured on purpose. The Shiozake's salt names its own amount in the
    item ("Salt (5% of the salmon's weight)") and is the one left — see the
    2026-10-23 decision-log entry."""
    stray = []
    for path in sorted(glob.glob(os.path.join(BATCHES, "*.json"))):
        with open(path) as f:
            e = json.load(f)
        for ing in e["ingredients"]:
            if (ing.get("category") == "pantry"
                    and re.search(r"\b(tsp|tbsp|pinch|teaspoons?|tablespoons?)\b", ing.get("qty") or "")
                    and not spices.is_assumed(ing["item"]) and "%" not in ing["item"]):
                stray.append(f"{e['dish_name']}: {ing['item']} {ing['qty']}")
    assert not stray, "spoonfuls on the shopping list:\n" + "\n".join(stray)


@pytest.mark.parametrize("name", [
    "Kashmiri chili powder", "Kasuri methi", "Asafoetida (hing)", "Gochugaru", "Chaat masala",
    "Aleppo pepper", "Ajwain (carom seeds)", "Fenugreek seeds (methi)", "Black mustard seeds",
    "Ground Sichuan pepper", "Ground fennel", "Mustard powder", "Amchur", "Tajín",
])
def test_the_librarys_spices_are_spices(name):
    assert spices.is_spice(name)


@pytest.mark.parametrize("name", [
    "Fennel", "Fennel bulb", "Methi leaves", "Pepper (bell)", "Cloves (garlic)", "Black beans",
    "Kashmiri chilies", "Chinese eggplant", "Mustard greens", "Dried mango",
])
def test_the_food_beside_them_is_not(name):
    """GUARD: fresh fennel and methi are produce; a bell pepper in brackets
    is still a pepper; "black" and "Chinese" describe, they don't make a spice."""
    assert not spices.is_spice(name)


def test_an_approved_library_week_buys_a_jar_of_kashmiri_chili_powder():
    """CATCH, end to end: Butter Chicken's 1¾ tsp went on the list as
    "1.75 tsp" in an aisle."""
    e = _entry("butter-chicken")
    tools.add_member("A")
    tools.add_member("B")
    tools.add_recipe(e["dish_name"], ingredients=e["ingredients"], instructions=e["instructions"],
                     default_servings=e["default_servings"])
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(monday.isoformat(), e["dish_name"], slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "A")
    conn = get_conn()
    row = conn.execute("SELECT quantity FROM grocery_items WHERE item = 'Kashmiri chili powder'").fetchone()
    conn.close()
    assert row["quantity"] == "1 jar"
    assert spices.assumed_section("Kashmiri chili powder") == spices.SECTION_SPICES


# ---------- 3. what the tuna is packed in is not the tuna ----------

@pytest.mark.parametrize("key, item, cans", [
    ("salade-nicoise", "Tuna in olive oil", "2 cans (5 oz)"),
    ("empanada-gallega", "Canned tuna in olive oil", "3 cans (5 oz each)"),
])
def test_a_library_dishs_canned_tuna_is_cans_on_the_cook_card(key, item, cans):
    """CATCH: both showed "2 tbsp", olive oil's amount."""
    e = _entry(key)
    shown = {i["item"]: i["qty"] for i in recipes.cooking_ingredients(
        e["ingredients"], servings=e["default_servings"], instructions=e["instructions"])}
    assert shown[item] == cans


def test_the_oil_itself_is_still_measured():
    """GUARD: a can of olive oil is still not a cooking amount."""
    assert recipes.cooking_ingredients([{"item": "Olive oil", "qty": "1 can"}], 4)[0]["qty"] == "2 tbsp"
    assert recipes._clean_item("Sun-dried tomatoes in oil") == "sun-dried tomatoes"


# ---------- review, 2026-10-23: what the new words must not hold ----------

@pytest.mark.parametrize("item", [
    "Glass vermicelli", "Bean thread vermicelli", "Mung bean vermicelli", "Rice stick vermicelli",
    "Vermicelli rice noodles", "Rice vermicelli noodles", "Bean thread noodles",
    "Spring roll wrappers (rice paper)", "Rice paper spring roll wrappers",
])
def test_noodles_and_wrappers_that_say_what_they_are_pass_for_celiac(item):
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["Celiac"])
    assert allergen_gate.hard_clashes("Weeknight Bowl", [{"item": item, "qty": "8 oz"}]) == []


@pytest.mark.parametrize("item", ["Vermicelli", "Spring roll wrappers"])
def test_the_bare_word_is_still_wheat(item):
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["Celiac"])
    assert allergen_gate.hard_clashes("Weeknight Bowl", [{"item": item, "qty": "8 oz"}])


@pytest.mark.parametrize("item", [
    "Tuna in olive oil", "Canned tuna in olive oil", "Sardines in olive oil", "Sun-dried tomatoes in oil",
])
def test_food_packed_in_oil_is_bought_not_assumed_at_home(item):
    """CATCH (on main too): these sat in Spices this week, assumed at home."""
    assert not spices.is_assumed(item)


def test_salade_nicoises_tuna_is_on_the_list_to_buy():
    e = _entry("salade-nicoise")
    tools.add_member("A")
    tools.add_member("B")
    tools.add_recipe(e["dish_name"], ingredients=e["ingredients"], instructions=e["instructions"],
                     default_servings=e["default_servings"])
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(monday.isoformat(), e["dish_name"], slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "A")
    conn = get_conn()
    row = conn.execute("SELECT quantity, status FROM grocery_items WHERE item = 'Tuna in olive oil'").fetchone()
    conn.close()
    assert row["status"] == "needed" and "can" in row["quantity"]
    assert spices.assumed_section("Tuna in olive oil") is None
