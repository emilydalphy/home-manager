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
