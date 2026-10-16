"""
"1 (8 ounce) package cream cheese" is eight ounces in the pan.

Defect hunt, 2026-10-16. A recipe added from a link says "1 (8 ounce)
package cream cheese"; the import sheet keeps it as "1 package (8 oz)". The
Cook screen threw the size away with the package word and showed the
table's per-4 guess instead: "4 oz cream cheese" at the recipe's own four,
"2 oz" at a table of two. The same recipe's "1 (16 ounce) package
spaghetti" stayed "1 package (16 ounce)" — a package word on the Cook
screen, which is Julia's complaint — and the stepper couldn't scale it
("Eyeball these — they don't scale automatically: spaghetti").
"""
from __future__ import annotations

import pytest

from app import recipe_import, tools


def _dip_pasta():
    lines = [
        "1 (16 ounce) package spaghetti",
        "1 (8 ounce) package cream cheese",
        "1/2 cup pesto",
    ]
    tools.add_recipe(
        "Creamy Pesto Pasta",
        ingredients=[recipe_import.split_ingredient_line(l) for l in lines],
        instructions=["Cook the spaghetti.", "Beat the cream cheese with the pesto.", "Toss."],
        default_servings=4,
    )


def test_the_cook_screen_uses_the_size_the_recipe_states():
    _dip_pasta()
    own = {i["item"]: i for i in tools.scale_recipe("Creamy Pesto Pasta", 4)["scaled_ingredients"]}
    assert own["cream cheese"]["qty"] == "8 oz"
    assert own["spaghetti"]["qty"] == "16 oz"
    # The shopping line is untouched: still one package.
    assert own["cream cheese"]["shopping_qty"] == "1 package (8 ounce)"


def test_the_stated_size_scales_with_the_stepper():
    _dip_pasta()
    half = tools.scale_recipe("Creamy Pesto Pasta", 2)
    double = tools.scale_recipe("Creamy Pesto Pasta", 8)
    assert {i["item"]: i["qty"] for i in half["scaled_ingredients"]}["cream cheese"] == "4 oz"
    assert {i["item"]: i["qty"] for i in double["scaled_ingredients"]}["spaghetti"] == "32 oz"
    assert "spaghetti" not in double["unscaled_items"]


@pytest.mark.parametrize("qty, shows", [
    ("2 packages (8 oz each)", "16 oz"),   # a count of them, size per package
    ("1 package (8-oz)", "8 oz"),          # the bracket the note splitter can't read
    ("1 box (16 oz)", "16 oz"),
    ("1 pkg (12 oz)", "12 oz"),
])
def test_a_sized_per_use_package_reads_as_its_size(qty, shows):
    item = "cream cheese" if "8" in qty else "spaghetti"
    assert tools.cooking_ingredients([{"item": item, "qty": qty}], servings=4)[0]["qty"] == shows


def test_an_unsized_package_is_a_package_word_like_a_pack():
    """'1 package spaghetti' is the table's amount, as '1 box' already was."""
    assert tools.recipes._quantity_problem("spaghetti", "1 package") == "package_unit"
    assert tools.recipes._quantity_problem("ramen noodles", "2 packages") == "package_unit"
    assert tools.cooking_ingredients([{"item": "spaghetti", "qty": "1 package"}], servings=4)[0]["qty"] == "12 oz"


@pytest.mark.parametrize("item, qty", [
    ("olive oil", "1 bottle (500 ml)"),          # drawn on all week; the size is the bottle
    ("marinara sauce", "1 jar (24 oz)"),          # jar is a week-long package too
    ("taco seasoning", "1 package (1 oz)"),       # a substance, never assumed whole
    ("sugar", "1 box (2 lb)"),
])
def test_a_size_is_not_the_amount_where_it_never_was(item, qty):
    assert tools.recipes._stated_package_amount(item, qty, 4) is None


def test_an_implausible_stated_size_falls_back_to_the_table():
    """A 5 lb family pack of thighs is not dinner for four."""
    assert tools.cooking_ingredients(
        [{"item": "chicken thighs", "qty": "1 package (5 lb)"}], servings=4,
    )[0]["qty"] == "1.5 lb"
