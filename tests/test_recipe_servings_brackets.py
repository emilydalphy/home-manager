"""
The servings stepper moves the amounts in brackets too (Loop Board
"Recipes people trust", slice 3, 2026-10-06: "rescales every ingredient
amount, including the amounts in brackets, and the amounts written in the
steps").

A bracket after a MEASURE restates the same amount ("2 cups (480 ml)") and
scales with it; a bracket after anything else is the size of one of them
("1 can (400 g)") and stays. The steps already scaled bracketed amounts
(scale_steps); this pins that alongside the list so the two can't drift.
"""
from app import tools


def _scaled(qty, servings, steps=None):
    tools.add_recipe(
        name="Rice Pot", ingredients=[{"item": "Water", "qty": qty}],
        instructions=steps or ["Bring the water to a boil."], default_servings=4,
    )
    return tools.scale_recipe("Rice Pot", servings)


def test_a_bracket_after_a_measure_doubles_with_it():
    out = _scaled("2 cups (480 ml)", 8, ["Add 2 cups (480 ml) of water."])
    assert out["scaled_ingredients"][0]["qty"] == "4 cups (960 ml)"
    assert out["scaled_instructions"] == ["Add 4 cups (960 ml) of water."]


def test_a_bracket_after_a_weight_halves_with_it():
    assert _scaled("400 g (14 oz)", 2)["scaled_ingredients"][0]["qty"] == "200 g (7 oz)"


def test_a_cans_size_stays_the_size_of_one_can():
    out = _scaled("1 can (400 g)", 8, ["Tip in the 1 can (400 g) of chickpeas."])
    assert out["scaled_ingredients"][0]["qty"] == "2 cans (400 g)"


# ---------- review round, 2026-10-06 ----------

import pytest


@pytest.mark.parametrize("qty,servings,expected", [
    ("1 (14 oz) can", 8, "2 (14 oz) cans"),          # count first: the count scales, the size stays
    ("2 (15 oz) cans", 2, "1 (15 oz) can"),
    ("1 cup (about 240 ml)", 8, "2 cups (about 480 ml)"),
    ("2 cups (16 fl oz)", 8, "4 cups (32 fl oz)"),
    ("1 cup (2 sticks)", 8, "2 cups (4 sticks)"),
    ("1 cup (240 ml), divided", 8, "2 cups (480 ml), divided"),
    ("½ cup (120 ml)", 8, "1 cup (240 ml)"),
    ("1-2 cups (240-480 ml)", 8, "2-4 cups (480-960 ml)"),
])
def test_the_list_scales_every_written_form_and_keeps_its_words(qty, servings, expected):
    assert _scaled(qty, servings)["scaled_ingredients"][0]["qty"] == expected


def test_the_list_and_the_steps_say_the_same_amount():
    out = _scaled("1-2 cups (240-480 ml)", 8, ["Pour in 1-2 cups (240-480 ml) of water."])
    assert out["scaled_ingredients"][0]["qty"] == "2-4 cups (480-960 ml)"
    assert out["scaled_instructions"] == ["Pour in 2-4 cups (480-960 ml) of water."]


def test_a_count_first_can_in_a_step_scales_too():
    out = _scaled("1 (14 oz) can", 8, ["Tip in the 1 (14 oz) can of water."])
    assert out["scaled_instructions"] == ["Tip in the 2 (14 oz) cans of water."]
