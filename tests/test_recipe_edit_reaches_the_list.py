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
    assert _lines()["Chicken thighs"] == ("2 lbs", "needed")
    tools.update_recipe_details("Fried Rice", default_servings=8)
    assert _lines()["Chicken thighs"] == ("1 lb", "needed")


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
    assert str(caught.value) == (
        "Not saved: the new steps for Fried Rice use eggs, which Sam can\u2019t have. "
        "Write the steps again without it."
    )
    assert _lines() == before
    assert tools.get_recipe("Fried Rice")["instructions"] == ["Cook the rice."]


def _all_lines():
    conn = get_conn()
    rows = sorted((r["item"], r["quantity"], r["status"]) for r in conn.execute(
        "SELECT item, quantity, status FROM grocery_items"
    ))
    conn.close()
    return rows


def _entries():
    conn = get_conn()
    ids = [r["id"] for r in conn.execute("SELECT id FROM meal_plan_entries ORDER BY date, id")]
    conn.close()
    return ids


def test_a_night_already_cooked_buys_nothing_and_the_night_ahead_does():
    """Tonight's Fried Rice is eaten; Saturday's is still to come. New eggy
    steps buy eggs for Saturday only (3 eggs, not 6)."""
    today = household_today()
    tools.add_recipe("Fried Rice", RICE, instructions=["Cook the rice."])
    plan = tools.create_weekly_plan(today.isoformat(), day_count=7)
    later = (today + datetime.timedelta(days=3)).isoformat()
    for day in (today.isoformat(), later):
        tools.plan_meal(day, "Fried Rice", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    tonight, _ = _entries()
    tools.check_off_meal(tonight, "done")
    tools.update_recipe_details("Fried Rice", instructions=["Cook the rice.", "Scramble 3 eggs and fold in."])
    conn = get_conn()
    links = [(r["meal_plan_entry_id"], r["quantity"]) for r in conn.execute(
        "SELECT meal_plan_entry_id, quantity FROM meal_plan_grocery_links WHERE LOWER(item) = 'eggs'"
    )]
    conn.close()
    assert [eid for eid, _ in links] == [_entries()[1]], links
    assert "Eggs" in _lines()


def test_more_servings_after_the_chicken_is_ticked_buys_only_the_difference():
    """Serves 4, two of us: '1 lb' on the list, ticked. The recipe is then
    said to serve 2, so the need is 2 lbs: one more pound, not two."""
    for name in ("Emily", "Sam"):
        tools.add_member(name)
    _approved_week(household_today())
    assert _lines()["Chicken thighs"] == ("1 lb", "needed")
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE item = 'Chicken thighs'")
    conn.commit()
    conn.close()
    tools.update_recipe_details("Fried Rice", default_servings=2)
    chicken = [line for line in _all_lines() if line[0] == "Chicken thighs"]
    assert chicken == [("Chicken thighs", "1 lb", "needed"), ("Chicken thighs", "1 lb", "purchased")]


def test_fewer_servings_after_the_chicken_is_ticked_buys_nothing_more():
    for name in ("Emily", "Sam"):
        tools.add_member(name)
    _approved_week(household_today())
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE item = 'Chicken thighs'")
    conn.commit()
    conn.close()
    tools.update_recipe_details("Fried Rice", default_servings=8)
    assert [line for line in _all_lines() if line[0] == "Chicken thighs"] == [
        ("Chicken thighs", "1 lb", "purchased")
    ]


def test_the_same_serving_count_again_leaves_every_line_and_its_id_alone():
    _approved_week(household_today())
    conn = get_conn()
    before = [tuple(r) for r in conn.execute("SELECT id, item, quantity FROM grocery_items ORDER BY id")]
    conn.close()
    tools.update_recipe_details("Fried Rice", default_servings=4)
    conn = get_conn()
    after = [tuple(r) for r in conn.execute("SELECT id, item, quantity FROM grocery_items ORDER BY id")]
    conn.close()
    assert after == before
