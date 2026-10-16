"""
An ingredient line that gives the same amount twice — "1 cup (120g)
flour", "400 g / 14 oz spaghetti" — keeps the first measure and the plain
item name (overnight defect hunt, 2026-10-11). The fake network is
test_recipe_import's.
"""
from __future__ import annotations

import json

import pytest

from app import recipe_import as ri, tools

from test_recipe_import import _html_response, _page, _wire


# ---------- the same amount said twice (overnight defect hunt, 2026-10-11) ----------
# US baking sites print the grams in brackets after the cups on nearly every
# line, UK ones the ounces after a slash. Both used to land on the ITEM —
# "(120g) all-purpose flour" — which is what the grocery list then showed,
# as a line that never merges with plain "all-purpose flour".

@pytest.mark.parametrize("line, item, qty", [
    ("1 cup (120g) all-purpose flour", "all-purpose flour", "1 cup"),
    ("1/2 cup (1 stick / 113g) butter, melted", "butter, melted", "1/2 cup"),
    ("3/4 cup (180 ml) whole milk", "whole milk", "3/4 cup"),
    ("250g (9oz) plain flour", "plain flour", "250 g"),
    ("400 g / 14 oz spaghetti", "spaghetti", "400 g"),
    ("400g/14oz spaghetti", "spaghetti", "400 g"),
    ("500 g/1 lb 2 oz minced beef", "minced beef", "500 g"),
    ("1 cup/240ml milk", "milk", "1 cup"),
    # Not a second measure: left exactly as the page wrote it.
    ("2 tbsp (optional) honey", "(optional) honey", "2 tbsp"),
    ("1 cup / or more water", "/ or more water", "1 cup"),
    # A package size before the unit is a different thing, and still kept.
    ("1 (14 oz) can diced tomatoes", "diced tomatoes", "1 can (14 oz)"),
])
def test_a_second_measure_after_the_unit_stays_off_the_item(line, item, qty):
    assert ri.split_ingredient_line(line) == {"item": item, "qty": qty}


def test_a_bracketed_gram_weight_reaches_the_grocery_list_as_plain_flour(signed_in, monkeypatch):
    recipe = json.dumps({
        "@type": "Recipe",
        "name": "Banana Bread",
        "recipeYield": "8",
        "recipeIngredient": ["2 cups (250g) all-purpose flour", "1/2 cup (113g) butter", "3 ripe bananas"],
        "recipeInstructions": "Mix.\nBake.",
    })
    _wire(monkeypatch, {"/bread": _html_response(_page(recipe))})
    draft = signed_in.post("/api/recipes/import-url", json={"url": "https://recipes.example.com/bread"}).json()["draft"]
    assert [(i["item"], i["qty"]) for i in draft["ingredients"]] == [
        ("all-purpose flour", "2 cups"), ("butter", "1/2 cup"), ("ripe bananas", "3"),
    ]
    assert signed_in.post("/api/recipes/add", json=draft).status_code == 200
    flour = next(i for i in tools.get_recipe("Banana Bread")["ingredients"] if "flour" in i["item"])
    assert flour["item"] == "all-purpose flour"
    assert tools._parse_quantity(flour["qty"]) == (2.0, "cup")


# ---------- what a bracket after the unit is NOT (review, 2026-10-11) ----------

@pytest.mark.parametrize("line, item, qty", [
    # After a container the bracket is the SIZE of what is bought: it goes
    # on the qty, the way "1 (14 oz) can" already reads.
    ("1 can (15 oz) black beans, drained", "black beans, drained", "1 can (15 oz)"),
    ("2 cans (14.5 oz) diced tomatoes", "diced tomatoes", "2 cans (14.5 oz)"),
    ("1 jar (24 oz) marinara", "marinara", "1 jar (24 oz)"),
    ("2 packages (8 oz each) cream cheese", "cream cheese", "2 packages (8 oz each)"),
    ("1 box (16 oz) penne", "penne", "1 box (16 oz)"),
    ("1 bag (12 oz) chocolate chips", "chocolate chips", "1 bag (12 oz)"),
    ("1 can (optional) corn", "(optional) corn", "1 can"),
    # A percentage describes the item, it doesn't re-measure it.
    ("1 cup (2%) milk", "(2%) milk", "1 cup"),
    ("1 lb (85% lean) ground beef", "(85% lean) ground beef", "1 lb"),
    ("1 cup (60% cacao) chocolate chips", "(60% cacao) chocolate chips", "1 cup"),
    # An alternative amount is not the same amount said twice.
    ("1 cup (or 2 cups for extra) cheese", "(or 2 cups for extra) cheese", "1 cup"),
])
def test_a_bracket_that_sizes_or_describes_keeps_what_it_says(line, item, qty):
    assert ri.split_ingredient_line(line) == {"item": item, "qty": qty}


def test_a_can_size_after_the_word_reaches_the_grocery_layer():
    qty = ri.split_ingredient_line("1 can (15 oz) black beans, drained")["qty"]
    assert tools._parse_quantity(qty) == (1.0, "can (15 oz)")


@pytest.mark.parametrize("line,item,qty", [
    ("2 T butter", "butter", "2 tbsp"),
    ("3 T. honey", "honey", "3 tbsp"),
    ("1 t salt", "salt", "1 tsp"),
    ("1 t. vanilla", "vanilla", "1 tsp"),
    # Not shorthand: a word that only starts with T stays the item.
    ("2 T-bone steaks", "T-bone steaks", "2"),
    ("4 Thyme sprigs", "Thyme sprigs", "4"),
])
def test_cookbook_t_and_capital_t_are_teaspoon_and_tablespoon(line, item, qty):
    """"2 T butter" saved an item called "T butter" with a bare "2"
    (defect hunt, 2026-10-16). Case decides which spoon it is."""
    assert ri.split_ingredient_line(line) == {"item": item, "qty": qty}
