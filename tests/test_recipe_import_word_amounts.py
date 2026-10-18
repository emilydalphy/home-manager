"""
An amount written as words on a recipe page — "a pinch of salt", "Pinch of
red pepper flakes", "a handful of basil" — is the line's amount, not part
of the ingredient's name (defect hunt, 2026-10-18).

Before: the link importer left "a pinch of salt" whole on the item with no
qty, the cook card filled the blank from its table and read "1 tsp a pinch
of salt", and the grocery list carried "a pinch of salt" as a line of its
own beside "salt".
"""
from __future__ import annotations

import json

import pytest

from app import recipe_import as ri, tools
from conftest import household_date


PAGE = (
    '<html><head><script type="application/ld+json">'
    + json.dumps({
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": "Garlic Butter Pasta",
        "recipeYield": "4",
        "recipeIngredient": [
            "1 lb spaghetti",
            "A pinch of salt",
            "Pinch of red pepper flakes",
            "a handful of fresh basil",
        ],
        "recipeInstructions": ["Boil the pasta.", "Toss with everything."],
    })
    + "</script></head><body></body></html>"
)


def test_a_linked_recipes_pinch_is_its_amount_on_the_draft_the_cook_card_and_the_list(signed_in):
    draft = ri.extract_recipe_draft(PAGE, "https://recipes.example.com/pasta")
    lines = {i["item"]: i["qty"] for i in draft["ingredients"]}
    assert lines["salt"] == "a pinch"
    assert lines["red pepper flakes"] == "a pinch"
    assert lines["fresh basil"] == "a handful"

    res = signed_in.post("/api/recipes/add", json=draft)
    assert res.status_code == 200, res.text
    saved = tools.get_recipe("Garlic Butter Pasta")

    # The cook card says what the page said — not a teaspoon of "a pinch of salt".
    cooked = {i["item"]: i["qty"] for i in tools.cooking_ingredients(saved["ingredients"], servings=4)}
    assert cooked["salt"] == "a pinch"
    assert cooked["red pepper flakes"] == "a pinch"
    assert not any(name.lower().startswith(("a pinch", "pinch")) for name in cooked)

    # And the list names the thing to buy.
    tools.plan_meal(household_date(1), "Garlic Butter Pasta", add_ingredients_to_grocery_list=True)
    names = {g["item"].lower() for g in tools.list_grocery_list()}
    assert "salt" in names
    assert not any("pinch" in n or "handful" in n for n in names)


@pytest.mark.parametrize("line, item, qty", [
    # A number still leads: the counted form was already read, and stays so.
    ("2 pinches of salt", "salt", "2 pinches"),
    ("A few drops of vanilla extract", "vanilla extract", "a few drops"),
    ("a splash of milk, optional", "milk, optional", "a splash"),
    # Words that only START like an amount are names, not amounts.
    ("Dashi stock", "Dashi stock", ""),
    ("Pinch salt", "Pinch salt", ""),
    ("Juice of 1 lemon", "Juice of 1 lemon", ""),
])
def test_only_a_worded_amount_followed_by_of_is_lifted_off_the_name(line, item, qty):
    assert ri.split_ingredient_line(line) == {"item": item, "qty": qty}
