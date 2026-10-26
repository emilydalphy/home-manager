"""
"1 (14 oz) can" of broth is fourteen ounces in the pan, not four cups.

Defect hunt, 2026-10-26, through the real routes on a throwaway database:
POST /api/recipes/add with a 3-serving stew carrying "Stock · 1 (14 oz)
can", then GET /api/recipes/scale?name=…&servings=N. The cook amount came
back "3 cups" at the recipe's own three, "4 cups" at four and "6 cups" at
six — the table's per-4 broth guess, with the can the recipe states (1¾
cups) thrown away. Broth is measured in cups in the table, so its can is
read as a package word ("a can of olive oil") rather than as a canned
good, and the stated size went with it.

Same shape, same door: a recipe typed in (the add sheet, or the chat) keeps
"1 (8 oz) package" in the order it was written, and only the link
importer rewrites it to "1 package (8 oz)" — so a typed cream cheese
showed the table's 4 oz where the import showed 8 (the 2026-10-16 fix only
read the importer's order).
"""
from __future__ import annotations

import pytest

from app import tools


@pytest.mark.parametrize("item, qty, shows", [
    ("chicken broth", "1 can (14 oz)", "14 oz"),
    ("stock", "1 (14 oz) can", "14 oz"),
    ("chicken broth", "1 (14.5-oz) can", "14.5 oz"),
    ("beef broth", "2 cans (14.5 oz each)", "29 oz"),
    ("cream cheese", "1 (8 oz) package", "8 oz"),
    ("spaghetti", "1 (16 oz) box", "16 oz"),
])
def test_a_size_written_before_the_container_is_the_amount(item, qty, shows):
    assert tools.cooking_ingredients([{"item": item, "qty": qty}], servings=4)[0]["qty"] == shows


def test_the_stepper_scales_the_cans_size_not_the_tables_guess(signed_in):
    res = signed_in.post("/api/recipes/add", json={
        "name": "Odd Stew", "default_servings": 3,
        "ingredients": [{"item": "Stock", "qty": "1 (14 oz) can"}, {"item": "Onion", "qty": "1"}],
        "instructions": ["Simmer the onion in the stock."],
    })
    assert res.status_code == 200, res.text

    def stock(servings):
        r = signed_in.get("/api/recipes/scale", params={"name": "Odd Stew", "servings": servings})
        assert r.status_code == 200, r.text
        return {i["item"]: i for i in r.json()["scaled_ingredients"]}["Stock"]

    assert stock(3)["qty"] == "14 oz"
    assert stock(3)["shopping_qty"] == "1 (14 oz) can", "what you buy is untouched"
    assert stock(6)["qty"] == "28 oz"


@pytest.mark.parametrize("item, qty, shows", [
    ("diced tomatoes", "1 (14 oz) can", "1 (14 oz) can"),   # a canned good: the can is the measure
    ("coconut milk", "1 can (14 oz)", "1 can (14 oz)"),
    ("olive oil", "1 (14 oz) can", "2 tbsp"),                # never used whole
])
def test_canned_goods_and_substances_are_unchanged(item, qty, shows):
    assert tools.cooking_ingredients([{"item": item, "qty": qty}], servings=4)[0]["qty"] == shows


def test_the_recipe_fill_fallback_keeps_a_canned_goods_can():
    """cooking_quantity is also the recipe-fill's fallback: a can of
    tomatoes stays a can there, never "14 oz" of tomatoes."""
    assert tools.recipes.cooking_quantity("diced tomatoes", 4, shopping_qty="1 (14 oz) can") == "1 can (14 oz)"
