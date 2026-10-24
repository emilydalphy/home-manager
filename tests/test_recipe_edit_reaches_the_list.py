"""
An edit to a recipe reaches an approved week's shopping list (2026-10-24,
overnight/recipes-edit-reaches-list).

update_recipe_details (the chat's "fix the recipe" tool) completes the
ingredient list from new steps — "Scramble 3 eggs" puts Eggs on the recipe
— and saves a new serving count, but the week already approved kept buying
for the recipe as it was: Eggs on the recipe, not on the list.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from conftest import household_today

RICE = [{"item": "Rice", "qty": "2 cups", "category": "pantry"},
        {"item": "Chicken thighs", "qty": "2 lbs", "category": "meat-seafood"}]


def _lines():
    conn = get_conn()
    rows = {r["item"]: (r["quantity"], r["status"]) for r in conn.execute(
        "SELECT item, quantity, status FROM grocery_items"
    )}
    conn.close()
    return rows


def _approved_week(day: datetime.date, approve: bool = True) -> int:
    tools.add_recipe("Fried Rice", RICE, instructions=["Cook the rice."])
    plan = tools.create_weekly_plan(day.isoformat(), day_count=7)
    tools.plan_meal(day.isoformat(), "Fried Rice", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    if approve:
        tools.approve_weekly_plan(plan["weekly_plan_id"])
    return plan["weekly_plan_id"]


def test_new_steps_that_use_eggs_put_eggs_on_this_weeks_list():
    _approved_week(household_today())
    before = _lines()
    assert "Eggs" not in before
    tools.update_recipe_details("Fried Rice", instructions=["Cook the rice.", "Scramble 3 eggs and fold in."])
    lines = _lines()
    assert "Eggs" in lines
    assert lines["Eggs"][1] == "needed"
    assert lines["Chicken thighs"] == before["Chicken thighs"], "an untouched line is bought once, not twice"


def test_doubling_the_servings_halves_what_the_week_buys():
    for name in ("Emily", "Sam", "Ada", "Leo"):
        tools.add_member(name)
    _approved_week(household_today())
    before = _lines()["Chicken thighs"]
    tools.update_recipe_details("Fried Rice", default_servings=8)
    assert _lines()["Chicken thighs"] != before


def test_a_draft_week_buys_nothing_new():
    _approved_week(household_today(), approve=False)
    tools.update_recipe_details("Fried Rice", instructions=["Cook the rice.", "Scramble 3 eggs and fold in."])
    assert _lines() == {}


def test_steps_only_reworded_leave_the_list_exactly_as_it_was():
    _approved_week(household_today())
    before = _lines()
    tools.update_recipe_details("Fried Rice", instructions=["Cook the rice well."], prep_time_minutes=10)
    assert _lines() == before


def test_new_steps_with_an_allergen_are_refused_and_nothing_is_bought():
    tools.add_member("Sam")
    tools.set_member_dietary_restrictions("Sam", ["allergy: eggs"])
    _approved_week(household_today())
    before = _lines()
    with pytest.raises(ValueError) as caught:
        tools.update_recipe_details("Fried Rice", instructions=["Cook the rice.", "Scramble 3 eggs and fold in."])
    assert "Sam" in str(caught.value) and "egg" in str(caught.value).lower()
    assert _lines() == before
    assert tools.get_recipe("Fried Rice")["instructions"] == ["Cook the rice."]
