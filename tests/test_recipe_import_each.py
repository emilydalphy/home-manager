"""
"1 tsp each salt and pepper" on a recipe page is one amount for each of two
things. The link importer saved an item called "each salt and pepper" — a
grocery line that merged with nothing (defect hunt, 2026-10-18). It is now
a line per name: salt 1 tsp, pepper 1 tsp.

("1/4 cup + 2 tbsp flour" was found alongside and is deliberately NOT fixed
here — summing it trips the cook card's plausibility guard; see the
Decision log.)
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
        "name": "Roast Potatoes",
        "recipeYield": "4",
        "recipeIngredient": [
            "2 lb potatoes",
            "1 tsp each salt and pepper",
            "1/2 tsp each of garlic powder, paprika, and thyme",
        ],
        "recipeInstructions": ["Toss.", "Roast."],
    })
    + "</script></head><body></body></html>"
)


def test_a_linked_recipes_each_line_saves_a_line_per_thing(signed_in):
    draft = ri.extract_recipe_draft(PAGE, "https://recipes.example.com/potatoes")
    assert [(i["item"], i["qty"]) for i in draft["ingredients"]] == [
        ("potatoes", "2 lb"), ("salt", "1 tsp"), ("pepper", "1 tsp"),
        ("garlic powder", "1/2 tsp"), ("paprika", "1/2 tsp"), ("thyme", "1/2 tsp"),
    ]
    res = signed_in.post("/api/recipes/add", json=draft)
    assert res.status_code == 200, res.text
    tools.plan_meal(household_date(1), "Roast Potatoes", add_ingredients_to_grocery_list=True)
    names = {g["item"].lower() for g in tools.list_grocery_list()}
    assert {"salt", "pepper", "paprika"} <= names
    assert not any(n.startswith(("each", "of ")) for n in names)


def test_a_model_read_string_line_fans_out_the_same_way():
    draft = ri.draft_from_model(
        {"found": True, "name": "Rub", "ingredients": ["1/2 tsp each cumin, coriander and paprika"],
         "instructions": ["Mix."]},
        "https://example.com/rub",
    )
    assert [(i["item"], i["qty"]) for i in draft["ingredients"]] == [
        ("cumin", "1/2 tsp"), ("coriander", "1/2 tsp"), ("paprika", "1/2 tsp"),
    ]


@pytest.mark.parametrize("line, expected", [
    # No unit: the names can share a noun, so the line stays whole.
    ("1 each red and green bell pepper", [{"item": "red and green bell pepper", "qty": "1"}]),
    # A note after the last name stays on that name.
    ("2 tbsp each fresh parsley and mint, chopped",
     [{"item": "fresh parsley", "qty": "2 tbsp"}, {"item": "mint, chopped", "qty": "2 tbsp"}]),
])
def test_edges(line, expected):
    assert ri.split_ingredient_lines(line) == expected
