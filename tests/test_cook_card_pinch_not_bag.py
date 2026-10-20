"""
"A pinch of sugar" is a pinch on the cook card, not "1 bag".

2026-10-19 recipe batch: Spaghetti and Meatballs listed Sugar as "1 bag"
(the shopping line, by design) and its sauce step added "a pinch of
sugar". The recipe writer gave no cook_qty for an unmeasured pinch, so the
cook view showed the package, or the table's per-4 guess of half a cup.
recipes.cook_amounts_from_steps reads the pinch off the steps; the list
still buys the bag.
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from conftest import household_today

STEPS = [
    "Brown the meatballs in a little oil.",
    "Stir in the crushed tomatoes, a splash of red wine vinegar and a pinch of sugar, then simmer 20 minutes.",
    "Cook the spaghetti and toss with the sauce.",
]


def _spaghetti():
    tools.add_recipe(
        "Spaghetti and Meatballs",
        ingredients=[
            {"item": "Spaghetti", "qty": "1 lb", "category": "pantry"},
            {"item": "Crushed tomatoes", "qty": "1 can (28 oz)", "category": "pantry"},
            {"item": "Sugar", "qty": "1 bag", "category": "pantry"},
            {"item": "Red wine vinegar", "qty": "1 bottle", "category": "pantry"},
        ],
        instructions=STEPS,
        default_servings=4,
    )


def _plan_it():
    monday = household_today() - datetime.timedelta(days=household_today().weekday())
    plan = tools.create_weekly_plan(week_start_date=monday.isoformat())
    plan_id = plan["weekly_plan_id"] if isinstance(plan, dict) else plan
    tools.plan_meal(
        household_today().isoformat(), "Spaghetti and Meatballs", slot="dinner",
        weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True,
    )
    return plan_id


def test_a_pinch_of_sugar_shows_a_pinch_on_the_cook_card_and_the_list_buys_the_bag():
    _spaghetti()
    plan_id = _plan_it()
    meal = next(m for m in tools.get_cooker_view(plan_id)["meals"] if m["meal"] == "Spaghetti and Meatballs")
    rows = {i["item"]: i for i in meal["ingredients"]}
    assert rows["Sugar"]["qty"] == "a pinch"
    assert rows["Sugar"]["shopping_qty"] == "1 bag"
    assert rows["Red wine vinegar"]["qty"] == "a splash"
    # The recipe page's stepper (scale_recipe) reads the same amount.
    scaled = {i["item"]: i for i in tools.scale_recipe("Spaghetti and Meatballs", 4)["scaled_ingredients"]}
    assert scaled["Sugar"]["qty"] == "a pinch"
    # The shopping side is untouched: the stored line and the list's line.
    stored = {i["item"]: i for i in tools.get_recipe("Spaghetti and Meatballs")["ingredients"]}
    assert stored["Sugar"]["qty"] == "1 bag"
    # Pantry lines sit in the list's pantry section (status 'spice'), still
    # bought as the package.
    conn = get_conn()
    rows = conn.execute("SELECT quantity FROM grocery_items WHERE LOWER(item) = 'sugar'").fetchall()
    conn.close()
    assert [r["quantity"] for r in rows] == ["1 bag"]


def test_a_line_the_steps_also_measure_is_not_a_pinch():
    # "2 tsp sugar" elsewhere: the pinch is not the whole of it, so the
    # pinch is never shown as the amount.
    lines = tools.recipes.cook_amounts_from_steps(
        [{"item": "Sugar", "qty": "1 bag"}],
        ["Add a pinch of sugar to the onions.", "Finish with 2 tsp sugar."],
    )
    assert "cook_qty" not in lines[0]
    # Nor a line the steps use for more than the splash, unmeasured.
    lines = tools.recipes.cook_amounts_from_steps(
        [{"item": "Olive oil", "qty": "1 bottle"}],
        ["Heat the olive oil and brown the meatballs.", "Finish with a splash of olive oil."],
    )
    assert "cook_qty" not in lines[0]


def test_a_pinch_of_sugar_is_not_the_brown_sugar_and_a_measured_cook_qty_stands():
    lines = tools.recipes.cook_amounts_from_steps(
        [
            {"item": "Brown sugar", "qty": "1 bag"},
            {"item": "Sugar", "qty": "1 bag", "cook_qty": "1 tbsp"},
        ],
        ["Add a pinch of sugar."],
    )
    assert "cook_qty" not in lines[0]
    assert lines[1]["cook_qty"] == "1 tbsp"


def test_a_phrase_about_something_else_gives_the_line_nothing():
    # Review 2026-10-20: the words after "of" ran on past "with", so "a
    # splash of red wine with the tomato paste" handed the paste a splash;
    # and a line whose name is only PART of the phrase ("Sauce" in "hot
    # sauce", "Lemon" in "lemon juice") is about another thing.
    lines = tools.recipes.cook_amounts_from_steps(
        [
            {"item": "Tomato paste", "qty": "1 can"},
            {"item": "Red wine", "qty": "1 bottle"},
            {"item": "Sauce", "qty": "1 bottle"},
            {"item": "Lemon", "qty": "1 bag"},
        ],
        [
            "Stir in a splash of red wine with the tomato paste.",
            "Finish with a dash of hot sauce and a splash of lemon juice.",
        ],
    )
    rows = {line["item"]: line for line in lines}
    assert rows["Red wine"]["cook_qty"] == "a splash"
    assert "cook_qty" not in rows["Tomato paste"]
    assert "cook_qty" not in rows["Sauce"]
    assert "cook_qty" not in rows["Lemon"]
