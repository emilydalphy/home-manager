"""
"2 (15 oz) cans" on the cook card is two cans, not one.

THE BUG (defect hunt, 2026-10-24). Most recipes write a canned good count
first and the size of one can in brackets — "2 (15 oz) cans chickpeas",
"1 (13.5-ounce) can coconut milk", or "2 x 400g tins". The shared quantity
parser reads none of those, so the cook screen called the household's own
line "unmeasured" and put the table's per-4 guess, "1 can (15 oz)", in its
place — scaled again for the table. A household of two cooking a recipe
for four was told "½ can (15 oz)" under a step saying "Drain 2 (15 oz)
cans chickpeas", and the coconut milk's 13.5 oz became 14.

The fix reads that shape as a count of sized containers on the cook side
(recipes._parse_cook_qty), so the line keeps the household's words and
scales by its count. The grocery list is not touched by this file.
"""
from __future__ import annotations

from conftest import household_date

from app import tools
from app.tools import recipes as _recipes


def _household(*names):
    for n in names:
        tools.add_member(n)


def _curry(**kw):
    tools.add_recipe(
        "Chickpea Curry",
        ingredients=[
            {"item": "chickpeas", "qty": "2 (15 oz) cans"},
            {"item": "coconut milk", "qty": "1 (13.5-ounce) can"},
            {"item": "tomatoes", "qty": "2 x 400g tins"},
            {"item": "onion", "qty": "1"},
        ],
        default_servings=4,
        instructions=["Drain 2 (15 oz) cans chickpeas", "Add 1 (13.5-ounce) can coconut milk"],
        **kw,
    )


def _card():
    plan_id = tools.create_weekly_plan(household_date(0))["weekly_plan_id"]
    entry = tools.plan_meal(household_date(2), "Chickpea Curry", slot="dinner",
                            weekly_plan_id=plan_id)["entry_id"]
    meal = next(m for m in tools.get_cooker_view(plan_id)["meals"] if m.get("entry_id") == entry)
    return {i["item"]: i["qty"] for i in meal["ingredients"]}


def test_a_table_the_recipe_is_written_for_sees_the_cans_it_typed():
    _household("Alex", "Sam", "Rae", "Jo")
    _curry(amounts_as_written=True)

    card = _card()

    assert card["chickpeas"] == "2 (15 oz) cans", "not the table's '1 can (15 oz)'"
    assert card["coconut milk"] == "1 (13.5-ounce) can", "the can keeps its own size"
    assert card["tomatoes"] == "2 tins (400 g)", "two tins, said the way the list says it"


def test_a_table_of_two_halves_the_count_once():
    _household("Alex", "Sam")
    _curry(amounts_as_written=True)

    card = _card()

    assert card["chickpeas"] == "1 (15 oz) can", "half of two cans is one, not half of one"
    assert card["tomatoes"] == "1 tin (400 g)"


def test_a_model_written_recipe_is_read_the_same_way():
    """No `verbatim` mark: the line still measures something, so the
    table's guess never replaces it."""
    _household("Alex", "Sam")
    _curry()

    assert _card()["chickpeas"] == "1 (15 oz) can"


def test_the_stepper_and_the_step_say_the_same_number():
    _curry(amounts_as_written=True)

    scaled = tools.scale_recipe("Chickpea Curry", 8)

    qty = {i["item"]: i["qty"] for i in scaled["scaled_ingredients"]}
    assert qty["chickpeas"] == "4 (15 oz) cans"
    assert scaled["scaled_instructions"][0] == "Drain 4 (15 oz) cans chickpeas"
    assert "chickpeas" not in scaled["unscaled_items"]


def test_a_count_of_pieces_or_an_unsized_can_is_not_a_sized_container():
    assert _recipes._count_first_sized("2 x 3 inch pieces") is None
    assert _recipes._count_first_sized("2 (15 oz) handfuls") is None
    assert _recipes._count_first_sized("2 cans") is None
    assert _recipes._count_first_sized("1 (14.5 ounce) can") == (1.0, "can (14.5 oz)")


def test_an_x_sized_tin_scales_the_same_on_the_list_and_in_the_step():
    """Review, 2026-10-24: "2 x 400g tins" scaled on the list ("4 tins
    (400 g)") while the step kept "Add 2 x 400g tins". Both say the count."""
    tools.add_recipe(
        "Tomato Soup",
        ingredients=[{"item": "tomatoes", "qty": "2 x 400g tins"}],
        default_servings=4,
        instructions=["Add 2 x 400g tins tomatoes", "Bake in a 9 x 13 inch dish"],
        amounts_as_written=True,
    )

    for servings, count, step in ((8, "4 tins", "Add 4 x 400g tins tomatoes"),
                                  (2, "1 tin", "Add 1 x 400g tin tomatoes")):
        scaled = tools.scale_recipe("Tomato Soup", servings)
        qty = {i["item"]: i["qty"] for i in scaled["scaled_ingredients"]}
        assert qty["tomatoes"] == f"{count} (400 g)"
        assert scaled["scaled_instructions"][0] == step
        assert scaled["scaled_instructions"][1] == "Bake in a 9 x 13 inch dish", "a dish size is not food"
