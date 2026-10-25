"""
A peanut butter is a spread, not a cooking fat — the cook card shows the
recipe's own "1/3 cup", not "1 tbsp".

THE BUG (live report, 2026-10-25, Tester Home). "Thai Peanut Chicken and
Vegetable Rice Bowl: Peanut butter '1/3 cup' is more than 2 would use — the
cook view shows 1 tbsp." The per-serving plausibility table
(recipes._PLAUSIBLE_PER_SERVING) matched "butter" in "Peanut butter" and
held it to the fat range, 2 tbsp a head, so the pre-save pass wrote a
cook_qty of "1 tbsp" (the table's guess for two) onto a line whose own
amount was right for a peanut sauce. The list and the steps said 1/3 cup;
the cook card said 1 tbsp. Tahini, the same kind of paste, has no class and
was never touched. The fix gives nut, seed and fruit butters a "spread"
class of their own (up to 1/4 cup / 70 g a head, so a whole jar is still
caught), and the cook card stops reading the old guess on a recipe saved
before the fix — only where the old fat range could have written it.
"""
from __future__ import annotations

import json

from conftest import household_date

from app import tools
from app.db import get_conn
from app.tools import plan_quality
from app.tools import recipes as _recipes

STEPS = [
    "Whisk 1/3 cup peanut butter with the lime juice and soy sauce.",
    "Cook the chicken thighs and toss with the sauce and rice.",
]


def _bowl(peanut: dict, servings: int = 2, steps: list[str] = STEPS):
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe(
        "Thai Peanut Bowl",
        ingredients=[
            peanut,
            {"item": "Chicken thighs", "qty": "1 lb"},
            {"item": "Rice", "qty": "1 cup"},
            {"item": "Butter", "qty": "1 stick"},
        ],
        default_servings=servings,
        instructions=steps,
    )


def _card() -> dict:
    plan_id = tools.create_weekly_plan(household_date(0))["weekly_plan_id"]
    entry = tools.plan_meal(household_date(2), "Thai Peanut Bowl", slot="dinner",
                            weekly_plan_id=plan_id)["entry_id"]
    meal = next(m for m in tools.get_cooker_view(plan_id)["meals"] if m.get("entry_id") == entry)
    return {i["item"]: i["qty"] for i in meal["ingredients"]}


def _cups(qty: str) -> float:
    """The card's amount in cups — the scaler writes "0.333333 cup", which
    the screen draws as "⅓ cup" (humanQtyText)."""
    amount, unit = _recipes._parse_cook_qty(qty)
    assert unit == "cup", qty
    return round(amount, 3)


def test_a_new_recipe_keeps_its_peanut_butter_amount_on_the_cook_card():
    _bowl({"item": "Peanut butter", "qty": "1/3 cup"})

    saved = tools.get_recipe("Thai Peanut Bowl")["ingredients"]
    assert not next(i for i in saved if i["item"] == "Peanut butter").get("cook_qty")
    card = _card()
    assert _cups(card["Peanut butter"]) == 0.333, "the list's amount, not the butter range's 1 tbsp"
    # Real butter is still held to the fat range: a stick for two is not
    # what goes in.
    assert card["Butter"] == "1 tbsp"


def test_a_recipe_saved_with_the_old_guess_shows_the_list_amount():
    _bowl({"item": "Peanut butter", "qty": "1/3 cup"})
    # What approval wrote before the fix: the clamp's guess as cook_qty.
    with get_conn() as conn:
        row = conn.execute("SELECT id, ingredients_json FROM recipes WHERE name = ?", ("Thai Peanut Bowl",)).fetchone()
        ings = json.loads(row["ingredients_json"])
        for ing in ings:
            if ing["item"] == "Peanut butter":
                ing["cook_qty"] = "1 tbsp"
        conn.execute("UPDATE recipes SET ingredients_json = ? WHERE id = ?", (json.dumps(ings), row["id"]))

    assert _cups(_card()["Peanut butter"]) == 0.333


def test_a_cook_amount_the_writer_gave_a_jar_is_kept():
    """Only the table's exact guess on a line that already measures is
    read as the old clamp — a jar's measured cook_qty is the recipe's."""
    _bowl({"item": "Peanut butter", "qty": "1 jar", "cook_qty": "1/3 cup"})

    assert _cups(_card()["Peanut butter"]) == 0.333


def test_the_morning_report_has_no_line_for_it():
    entry = {
        "date": household_date(2), "slot": "dinner", "slot_state": "planned",
        "meal_name": "Thai Peanut Bowl", "default_servings": 2, "instructions": STEPS,
        "ingredients": [{"item": "Peanut butter", "qty": "1/3 cup"}, {"item": "Almond butter", "qty": "1/4 cup"}],
    }
    assert plan_quality._quantities_plausible([entry], {}) == []
    assert _recipes.check_steps_ingredients_consistency(
        entry["ingredients"][:1], STEPS, 2,
    )["amount_mismatches"] == []


def test_a_cook_amount_the_writer_gave_a_line_the_old_rule_passed_is_kept():
    """Review, 2026-10-25 (repro C): "1/2 cup" for four is 2 tbsp a head,
    inside the old fat range, so a "2 tbsp" cook_qty on it is the writer's
    — even though it equals the table's guess — and cooked for two it is
    the 1 tbsp the step says."""
    _bowl({"item": "Peanut butter", "qty": "1/2 cup", "cook_qty": "2 tbsp"}, servings=4,
          steps=["Stir 2 tbsp peanut butter into the noodles.", "Cook the chicken thighs with the rice."])

    assert _card()["Peanut butter"] == "1 tbsp"


def test_a_spread_still_has_a_ceiling():
    """A whole jar of peanut butter for celery sticks is still caught —
    spreads are judged by their own range, not none."""
    assert _recipes.plausible_cooking_quantity("Peanut butter", "16 oz", 4) != "16 oz"
    assert _recipes.plausible_cooking_quantity("Peanut butter", "2 cups", 2) != "2 cups"
    assert _recipes.plausible_cooking_quantity("Peanut butter", "1/3 cup", 2) == "1/3 cup"


def test_other_spellings_are_spreads_and_butter_beans_are_beans():
    for name in ("Peanut-butter", "Sunflower butter", "Sesame butter", "Natural peanut butter"):
        assert _recipes.plausible_cooking_quantity(name, "1/3 cup", 2) == "1/3 cup", name
    assert _recipes.plausible_cooking_quantity("Butter beans", "1 lb", 2) == "1 lb"
    assert _recipes.plausible_cooking_quantity("Butter", "1/3 cup", 2) == "1 tbsp"
