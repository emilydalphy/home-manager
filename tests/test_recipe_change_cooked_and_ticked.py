"""
Change recipe / a rewrite / its undo, on an approved week, against what the
household has already done (defect hunt, 2026-10-27,
overnight/hunt-recipes-1027).

1. Tonight's dinner is ticked cooked and changed to what they really made
   ("we had tacos instead"): the tacos went on the live list.
2. The chicken is in the cart and the night is changed (or its recipe
   rewritten) to another chicken dish: a second 2 lbs went on the list.
"""
import datetime

from app import tools
from app.db import get_conn
from app.tools import recipe_change
from conftest import household_today

RICE = [{"item": "Rice", "qty": "2 cups", "category": "pantry"},
        {"item": "Chicken thighs", "qty": "2 lbs", "category": "meat-seafood"}]
TACO = [{"item": "Ground beef", "qty": "1 lb", "category": "meat-seafood"},
        {"item": "Tortillas", "qty": "8", "category": "bakery"}]
CURRY = [{"item": "Chicken thighs", "qty": "2 lbs", "category": "meat-seafood"},
         {"item": "Coconut milk", "qty": "1 can", "category": "pantry"}]
BIG_CURRY = [{"item": "Chicken thighs", "qty": "3 lbs", "category": "meat-seafood"},
             {"item": "Coconut milk", "qty": "1 can", "category": "pantry"}]


def _lines():
    conn = get_conn()
    rows = sorted((r["item"], r["quantity"], r["status"]) for r in conn.execute(
        "SELECT item, quantity, status FROM grocery_items"))
    conn.close()
    return rows


def _id(sql, *args):
    conn = get_conn()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _week_with_fried_rice(day: datetime.date) -> int:
    tools.add_recipe("Fried Rice", RICE, instructions=["Cook the rice."])
    plan = tools.create_weekly_plan(household_today().isoformat(), day_count=7)
    tools.plan_meal(day.isoformat(), "Fried Rice", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    return _id("SELECT id FROM meal_plan_entries WHERE recipe_id IS NOT NULL")


def test_changing_tonights_cooked_dinner_buys_nothing_for_it():
    entry = _week_with_fried_rice(household_today())
    tools.add_recipe("Tacos", TACO, instructions=["Brown the beef."])
    tools.check_off_meal(entry, "done")
    before = _lines()
    out = tools.change_meal_recipe(entry, _id("SELECT id FROM recipes WHERE name = 'Tacos'"))
    assert out["status"] == "changed"
    assert _lines() == before, "an eaten dinner is not bought for"


def test_changing_a_night_ahead_counts_the_chicken_already_in_the_cart():
    entry = _week_with_fried_rice(household_today() + datetime.timedelta(days=2))
    tools.add_recipe("Chicken Curry", CURRY, instructions=["Simmer."])
    tools.mark_grocery_item(_id("SELECT id FROM grocery_items WHERE item = 'Chicken thighs'"), "in_cart")
    tools.change_meal_recipe(entry, _id("SELECT id FROM recipes WHERE name = 'Chicken Curry'"))
    lines = _lines()
    assert ("Chicken thighs", "2 lbs", "in_cart") in lines
    assert not [l for l in lines if l[0] == "Chicken thighs" and l[2] == "needed"], lines
    assert ("Coconut milk", "1 can", "needed") in lines


def test_a_bigger_need_buys_only_the_difference():
    entry = _week_with_fried_rice(household_today() + datetime.timedelta(days=2))
    tools.add_recipe("Big Curry", BIG_CURRY, instructions=["Simmer."])
    tools.mark_grocery_item(_id("SELECT id FROM grocery_items WHERE item = 'Chicken thighs'"), "purchased")
    tools.change_meal_recipe(entry, _id("SELECT id FROM recipes WHERE name = 'Big Curry'"))
    assert ("Chicken thighs", "1 lb", "needed") in _lines()


def test_a_rewrite_and_its_undo_count_the_chicken_already_in_the_cart():
    entry = _week_with_fried_rice(household_today() + datetime.timedelta(days=2))
    tools.mark_grocery_item(_id("SELECT id FROM grocery_items WHERE item = 'Chicken thighs'"), "in_cart")
    spec = recipe_change.rewrite_spec(entry)
    detail = {"ingredients": [dict(i) for i in RICE] + [{"item": "Peas", "qty": "1 cup", "category": "frozen"}],
              "instructions": ["Cook the rice.", "Stir in the peas."], "default_servings": 4}
    out = recipe_change.apply_rewrite(spec, detail, "add peas")
    assert out["status"] == "rewritten"
    lines = _lines()
    assert not [l for l in lines if l[0] == "Chicken thighs" and l[2] == "needed"], lines
    assert [l for l in lines if l[0] == "Peas" and l[2] == "needed"], lines
    undone = recipe_change.undo_recipe_change(out["request_id"])
    assert undone["status"] == "undone"
    lines = _lines()
    assert not [l for l in lines if l[0] in ("Chicken thighs", "Peas") and l[2] == "needed"], lines
