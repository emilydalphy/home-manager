"""
"Juice of 1 lemon", "Zest of 2 limes" on a recipe page: the count comes
AFTER the name, so the link importer read the whole line as an item with
no amount (defect hunt, 2026-11-02).

Before: the recipe saved "Juice of 1 lemon" with a blank qty; the grocery
list carried a line called "Juice of 1 lemon" in the pantry aisle that never
merged with another dinner's lemons, and the cook card at double read
"2 Juice of 1 lemons". Now the line names the fruit that is bought, with the
juicing kept as its note: {"item": "lemon, juiced", "qty": "1"}.
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
        "name": "Lemon Garlic Shrimp",
        "recipeYield": "2",
        "recipeIngredient": [
            "1 lb shrimp",
            "Juice of 1 lemon",
            "Zest of 2 limes",
        ],
        "recipeInstructions": ["Toss the shrimp with the juice of 1 lemon and the zest of 2 limes."],
    })
    + "</script></head><body></body></html>"
)


def test_a_linked_recipes_juice_of_a_lemon_buys_the_lemon(signed_in):
    draft = ri.extract_recipe_draft(PAGE, "https://recipes.example.com/shrimp")
    lines = {i["item"]: (i["qty"], i["category"]) for i in draft["ingredients"]}
    assert lines["lemon, juiced"] == ("1", "produce")
    assert lines["limes, zested"] == ("2", "produce")

    res = signed_in.post("/api/recipes/add", json=draft)
    assert res.status_code == 200, res.text
    # Another dinner that week buys lemons the ordinary way.
    tools.add_recipe("Lemon Pasta", [{"item": "lemons", "qty": "2", "category": "produce"}],
                     instructions=["Zest and juice the lemons."], default_servings=2)

    # The cook card at double counts lemons, not "Juice of 1 lemon"s.
    scaled = tools.scale_recipe("Lemon Garlic Shrimp", 4)
    by_item = {i["item"]: i for i in scaled["scaled_ingredients"]}
    assert by_item["lemon, juiced"]["qty"] == "2"
    assert not any(i["item"].lower().startswith(("juice of", "zest of")) for i in scaled["scaled_ingredients"])

    tools.plan_meal(household_date(1), "Lemon Garlic Shrimp", add_ingredients_to_grocery_list=True)
    tools.plan_meal(household_date(2), "Lemon Pasta", add_ingredients_to_grocery_list=True)
    rows = tools.list_grocery_list()
    names = [g["item"].lower() for g in rows]
    assert not any(n.startswith(("juice of", "zest of")) for n in names), names
    lemon_lines = [g for g in rows if "lemon" in g["item"].lower()]
    # One lemon line, with both dinners' lemons on it.
    assert len(lemon_lines) == 1, lemon_lines
    assert lemon_lines[0]["quantity"] == "3", lemon_lines


@pytest.mark.parametrize("line, item, qty", [
    ("zest and juice of 1 orange", "orange, zested and juiced", "1"),
    ("Juice of half a lemon", "lemon, juiced", "1/2"),
    ("Finely grated zest of 1 large lemon", "lemon, zested", "1 large"),
    ("Juice of 2 limes, plus wedges to serve", "limes, juiced, plus wedges to serve", "2"),
    ("juice of a lime", "lime, juiced", "1"),
    # No count to buy by: left as written for the review sheet, as before.
    ("Juice of lemon", "Juice of lemon", ""),
    # A measured juice is already read the ordinary way.
    ("2 tbsp lemon juice", "lemon juice", "2 tbsp"),
    # A measure after "juice of" is not a count of fruit to buy.
    ("Juice of 1 cup oranges", "Juice of 1 cup oranges", ""),
])
def test_juice_and_zest_lines_name_the_fruit(line, item, qty):
    assert ri.split_ingredient_line(line) == {"item": item, "qty": qty}
