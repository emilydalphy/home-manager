"""
A breakfast with no written recipe still shops for itself.

Loop Board "Breakfast ingredients reach the shopping list even when the
breakfast has no written recipe" (walkthrough 2026-10-06): "Greek Yogurt
with Berries and Granola" came back from the menu pass is_new_recipe=false
with no saved row by that name. agent._generate_weekly_plan's
_ensure_recipe_saved trusted the flag (it already made an exception for
snacks), plan_meal wrote the breakfast FREEFORM, and a freeform entry is
never written up and never shopped for. Yogurt, berries and granola never
reached the list. Both model calls are stubbed.
"""
import datetime

import pytest

from app import agent, tools
from app.db import get_conn

YOGURT = "Greek Yogurt with Berries and Granola"
DETAILS = {YOGURT: [{"item": "greek yogurt", "qty": "1 tub"}, {"item": "blueberries", "qty": "1 pint"},
                    {"item": "granola", "qty": "1 bag"}]}


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


@pytest.fixture
def stubbed(monkeypatch):
    tools.add_recipe("Chili", ingredients=[{"item": "black beans", "qty": "1 can"}],
                     food_groups=["protein", "vegetable", "carb"], default_servings=4)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {
        "ingredients": DETAILS.get(spec["name"], [{"item": "carrots", "qty": "1 lb"}]),
        "instructions": ["Spoon it into a bowl."]})
    monkeypatch.setattr(agent, "generate_sides_llm", lambda context: [])


def _days(week, breakfast_item):
    out = []
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            meal = {"date": day, "slot": slot, "meal_name": "Chili", "is_new_recipe": False}
            if slot == "breakfast":
                meal.update(breakfast_item)
            out.append(meal)
    return out


def _names():
    return [i["item"].lower() for i in tools.list_grocery_list() if i.get("status", "needed") == "needed"]


def test_a_breakfast_the_model_called_not_new_is_saved_and_shopped_for(stubbed, monkeypatch):
    week = _week_start()
    days = _days(week, {"meal_name": YOGURT, "is_new_recipe": False})
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)

    plan = agent.generate_weekly_plan(week)

    conn = get_conn()
    rows = conn.execute("SELECT recipe_id, freeform_meal FROM meal_plan_entries "
                        "WHERE weekly_plan_id = ? AND slot = 'breakfast'", (plan["weekly_plan_id"],)).fetchall()
    conn.close()
    assert rows and all(r["recipe_id"] and r["freeform_meal"] is None for r in rows), [dict(r) for r in rows]

    tools.approve_weekly_plan(plan["weekly_plan_id"])
    names = _names()
    for item in ("greek yogurt", "blueberries", "granola"):
        assert item in names, f"{item!r} missing from the list: {names}"


def test_a_dish_carrying_its_own_ingredient_list_is_saved_whatever_its_flag(stubbed, monkeypatch):
    week = _week_start()
    own = [{"item": "oats", "qty": "1 bag"}, {"item": "bananas", "qty": "1 bunch"}]
    days = _days(week, {"meal_name": "Banana Oats", "is_new_recipe": False, "ingredients": own,
                        "instructions": ["Stir."]})
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)

    plan = agent.generate_weekly_plan(week)
    tools.approve_weekly_plan(plan["weekly_plan_id"])

    names = _names()
    assert "oats" in names and "bananas" in names, names


def test_a_leftovers_night_stays_freeform(stubbed, monkeypatch):
    week = _week_start()
    tuesday = tools._week_dates(week)[1]
    days = _days(week, {"meal_name": "Chili"})
    for d in days:
        if d["date"] == tuesday and d["slot"] == "dinner":
            d.update({"meal_name": "Leftover Chili", "is_new_recipe": False,
                      "ingredients": [{"item": "beans", "qty": "1"}], "derived_from": {"links_to": "x"}})
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)

    agent.generate_weekly_plan(week)

    assert not tools.existing_recipe_named("Leftover Chili")
