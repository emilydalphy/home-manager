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
