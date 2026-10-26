"""
A pool snack's lines read like every other line on the shopping list
(overnight e2e hunt, 2026-10-23).

Walked as a new household: the week's snacks were folded for variety and
"Carrot Sticks with Hummus" came in from snack_variety's code-side pool.
The list then read "Avocados · Bread · … · carrots · … OTHER · hummus" —
the pool is spelled lower case (it is matched against the week's food) and
the list prints a line's name as the recipe saved it.
"""
from __future__ import annotations

from app import agent, tools
from app.tools import recipes, snack_variety

from test_snack_variety import _monday, _snack_days, _tester_home_week, stub_model, two_adults  # noqa: F401

HUMMUS_SNACK = "Carrot Sticks with Hummus"


def test_a_pool_snack_puts_capitalised_lines_on_the_list(two_adults, stub_model):
    """CATCH: on main the line is "hummus"."""
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert HUMMUS_SNACK in _snack_days(plan_id)
    tools.approve_weekly_plan(plan_id)
    names = [r["item"] for r in tools.list_grocery_list()]
    assert "Hummus" in names, names
    assert "hummus" not in names, names


def test_a_lower_case_pool_recipe_saved_before_is_respelled_at_the_same_table(two_adults, stub_model):
    """A household whose pool recipe was saved lower case, for the same two
    people: picked again, it is written again — otherwise "hummus" stays on
    every list it makes."""
    pool = next(p for p in snack_variety.SNACK_POOL if p["name"] == HUMMUS_SNACK)
    old = [dict(i, item=i["item"].lower()) for i in snack_variety.pool_ingredients(pool, 2)]
    tools.add_recipe(name=pool["name"], ingredients=old, default_servings=2, food_groups=pool["groups"])
    week = _monday()
    stub_model(_tester_home_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    assert HUMMUS_SNACK in _snack_days(plan_id)
    items = [i["item"] for i in recipes.saved_ingredients(HUMMUS_SNACK)]
    assert items == ["Carrots", "Hummus"], items


def test_a_pool_recipe_the_household_edited_is_left_alone(two_adults, stub_model):
    """GUARD: only the pool's own list is respelled, never a household's."""
    tools.add_recipe(name=HUMMUS_SNACK, default_servings=2, food_groups=["vegetable"],
                     ingredients=[{"item": "carrots", "qty": "4"}, {"item": "hummus", "qty": "1 tub"}])
    week = _monday()
    stub_model(_tester_home_week(week))
    agent.generate_weekly_plan(week)
    items = [(i["item"], i["qty"]) for i in recipes.saved_ingredients(HUMMUS_SNACK)]
    assert items == [("carrots", "4"), ("hummus", "1 tub")], items
