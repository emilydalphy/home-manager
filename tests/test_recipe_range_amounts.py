"""
A recipe's range is a measurement (defect hunt, 2026-10-18).

"1 to 2 cloves" of garlic, "2-3 lb" of thighs, "2–3 tbsp" of oil: the
cook view used to call every range "unmeasured" and put the table's per-4
guess in its place — 3 cloves for "1 to 2", 1.5 lb for "2-3 lb". The line
now keeps its own words, the stepper and the cook card scale it as text,
and a range whose two ends land on one number says that number once.
"""
from __future__ import annotations

import datetime as dt

from app import tools
from app.tools import recipes
from conftest import household_today


def _cook(item: str, qty: str, servings: int = 4) -> str:
    return recipes.cooking_ingredients([{"item": item, "qty": qty}], servings=servings)[0]["qty"]


def test_a_range_keeps_the_recipes_own_words_on_the_cook_card():
    assert _cook("Garlic", "1 to 2 cloves") == "1 to 2 cloves"
    assert _cook("Chicken thighs", "2-3 lb") == "2-3 lb"
    assert _cook("Olive oil", "2–3 tbsp") == "2–3 tbsp"
    assert _cook("Rice", "1 ½-2 cups") == "1 ½-2 cups"


def test_a_range_is_a_measurement_to_the_validator():
    check = recipes.validate_measured_quantities([
        {"item": "Garlic", "qty": "5-6 cloves"},
        {"item": "Cumin", "qty": "1 to 2 tsp"},
    ])
    assert check["ok"], check["problems"]
    # A range of something only ever measured is still not a measurement
    # when it carries no unit.
    assert recipes._quantity_problem("Olive oil", "2-3") == "unmeasured"


def test_a_range_out_of_all_reason_is_still_corrected():
    # Judged by its low end: three sticks of butter for two is no more
    # plausible than one stick was (the 2026-09-13 plausibility rule).
    assert _cook("Butter", "3-4 sticks", servings=2) != "3-4 sticks"


def test_the_stepper_scales_a_range_and_collapses_one_that_meets():
    tools.add_recipe(
        "Garlic Chicken",
        ingredients=[
            {"item": "Chicken thighs", "qty": "2-3 lb"},
            {"item": "Garlic", "qty": "1 to 2 cloves"},
        ],
        instructions=["Rub 2-3 lb thighs with 1 to 2 cloves garlic."],
        default_servings=4,
    )
    half = tools.scale_recipe("Garlic Chicken", 2)
    assert [i["qty"] for i in half["scaled_ingredients"]] == ["1-1 ½ lbs", "1 clove"]
    assert half["scaled_instructions"] == ["Rub 1-1 ½ lbs thighs with 1 clove garlic."]
    double = tools.scale_recipe("Garlic Chicken", 8)
    assert [i["qty"] for i in double["scaled_ingredients"]] == ["4-6 lbs", "2 to 4 cloves"]


def test_tonights_cook_card_shows_the_range_sized_to_the_table(signed_in):
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    tools.add_recipe(
        "Garlic Chicken",
        ingredients=[{"item": "Chicken thighs", "qty": "2-3 lb"}, {"item": "Garlic", "qty": "1 to 2 cloves"}],
        prep_time_minutes=10, cook_time_minutes=25, default_servings=4,
    )
    today = household_today()
    plan_id = tools.create_weekly_plan((today - dt.timedelta(days=today.weekday())).isoformat())["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Garlic Chicken", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=False)

    res = signed_in.get("/api/cooker-view")
    assert res.status_code == 200

    cards = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("meal") == "Garlic Chicken" and isinstance(o.get("ingredients"), list):
                cards.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(res.json())
    assert cards, "tonight's dinner should be on the cook view"
    qtys = {i["item"]: i["qty"] for i in cards[0]["ingredients"]}
    # Two adults at a recipe written for four: half of what it says, never
    # the table's 1.5 lb or 3 cloves.
    assert qtys["Chicken thighs"] == "1-1 ½ lbs"
    assert qtys["Garlic"] == "1 clove"
