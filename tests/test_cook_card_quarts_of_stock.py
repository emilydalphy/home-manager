"""
"1 ½ quarts chicken stock" is quarts of stock on the cook card, not pounds.

THE BUG (defect hunt, 2026-10-24). Two holes lined up:

  - The parser knew "quart" but not "quarts", "qt" or "gallons", so the
    unit came back as an unknown word and the cook card called the line
    unmeasured — and swapped in the table's amount for the item.
  - The table's lookup takes the LONGEST key that matches, and "chicken"
    (1.5 lb) is longer than "stock" (4 cups). So that swap read
    "1.5 lb chicken stock", on the household's own recipe, at its own size.

And the serving stepper left a "1 ½ quarts" line unscaled at every size
while the "2 quarts" of water beside it doubled.
"""
from __future__ import annotations

from app import tools
from app.tools import quantities as _quantities
from app.tools import recipes as _recipes


def _soup(**kw):
    tools.add_recipe(
        "Soup",
        ingredients=[
            {"item": "chicken stock", "qty": "1 ½ quarts"},
            {"item": "water", "qty": "2 quarts"},
            {"item": "beef broth", "qty": "1 1/2 qt"},
        ],
        default_servings=4,
        instructions=["Bring the stock and water to a boil in a 6 quart pot"],
        **kw,
    )


def _cook_card(servings=4):
    recipe = tools.get_recipe("Soup")
    return {
        i["item"]: i["qty"]
        for i in _recipes.cooking_ingredients(recipe["ingredients"], servings=servings,
                                              instructions=recipe["instructions"])
    }


def test_the_recipes_own_quarts_of_stock_stay_quarts():
    _soup(amounts_as_written=True)

    card = _cook_card()

    assert card["chicken stock"] == "1 ½ quarts", "not the table's '1.5 lb'"
    assert card["beef broth"] == "1 1/2 qt", "qt is a quart, not a word to replace"


def test_a_model_written_quart_line_is_kept_too():
    _soup()

    assert _cook_card()["chicken stock"] == "1 ½ quarts"


def test_stock_with_no_amount_gets_cups_not_pounds():
    assert _recipes.cooking_quantity("chicken stock", servings=4, shopping_qty="1 carton") == "4 cups"
    assert _recipes.cooking_quantity("beef stock", servings=4) == "4 cups"
    assert _recipes.cooking_quantity("chicken", servings=4) == "1.5 lb", "the bird itself is unchanged"


def test_the_stepper_scales_quarts_like_everything_else():
    _soup(amounts_as_written=True)

    scaled = tools.scale_recipe("Soup", 8)

    qty = {i["item"]: i["qty"] for i in scaled["scaled_ingredients"]}
    assert qty["chicken stock"] == "3 quarts"
    assert qty["water"] == "4 quarts", "a whole number of quarts reads in the plural"
    assert qty["beef broth"] == "3 quarts"
    assert scaled["unscaled_items"] == []
    assert scaled["scaled_instructions"] == ["Bring the stock and water to a boil in a 6 quart pot"], \
        "a pot's size is not an amount of food"


def test_quarts_and_a_quart_are_one_unit_to_the_list_too():
    assert _quantities._parse_quantity("2 quarts") == (2.0, "quart")
    assert _quantities._parse_quantity("1 gallon") == (1.0, "gallon")
    assert _quantities._format_quantity(3, "quart") == "3 quarts"


def test_a_step_scales_quarts_of_food_and_leaves_the_pot_alone():
    """Review, 2026-10-24: the list said "3 quarts" of stock while the step
    still said "1 ½ quarts". Both in one step: the stock scales, the pot
    doesn't — whichever way the pot's size is written."""
    tools.add_recipe(
        "Stock Pot",
        ingredients=[
            {"item": "chicken stock", "qty": "1 ½ quarts"},
            {"item": "beef broth", "qty": "2 qt"},
            {"item": "water", "qty": "1 gallon"},
        ],
        default_servings=4,
        instructions=[
            "Bring 1 ½ quarts chicken stock to a boil in a 6 quart pot",
            "Add 2 qt beef broth and 1 gallon water to an 8-qt stockpot or 6 quart pot",
        ],
        amounts_as_written=True,
    )

    scaled = tools.scale_recipe("Stock Pot", 8)

    qty = {i["item"]: i["qty"] for i in scaled["scaled_ingredients"]}
    assert qty["chicken stock"] == "3 quarts"
    assert scaled["scaled_instructions"] == [
        "Bring 3 quarts chicken stock to a boil in a 6 quart pot",
        "Add 4 qt beef broth and 2 gallons water to an 8-qt stockpot or 6 quart pot",
    ]
