"""
The writer's own accuracy check (recipes.check_steps_ingredients_consistency)
raised two false alarms on 2026-10-19's recipe test batch, and each one paid
for a repair call that could not fix what wasn't wrong:

- a list's plural against a step's singular: "Eggs" / "the egg", "Lemons" /
  "juice of 1 lemon", "Limes" / "the juice of 1 lime" read as never used;
- "the starchy pasta water" over a list with Spaghetti read as pasta missing.
"""
from app.tools import recipes


def _check(items, steps):
    return recipes.check_steps_ingredients_consistency(
        [{"item": i, "qty": "1"} for i in items], steps, 4)


def test_a_plural_on_the_list_is_the_singular_in_a_step():
    r = _check(["Eggs", "Lemons", "Limes", "Tomatoes"], [
        "Whisk in the egg.", "Squeeze in the juice of 1 lemon.",
        "Add the juice of 1 lime.", "Dice the tomato."])
    assert r["unused_ingredients"] == []


def test_pasta_water_over_spaghetti_is_not_missing_pasta():
    r = _check(["Spaghetti"], ["Cook the spaghetti and save 1/2 cup of the pasta water."])
    assert r["missing_from_list"] == []


def test_an_unused_ingredient_is_still_found():
    r = _check(["Eggs", "Capers"], ["Whisk in the egg."])
    assert r["unused_ingredients"] == ["Capers"]
