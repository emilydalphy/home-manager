"""
Settings -> Recipes does not count dishes swapped out of the draft.

Loop Board "Settings -> Recipes counts dishes you swapped away" (walkthrough
2026-10-06: "7 saved" for a household that had saved none). The draft
generator writes a recipe for every dish it picks; one swapped out before
approval stayed in the list and the count. It is still cached, but it is
listed only once it has been on an approved week, cooked or rated.
"""
import datetime

import pytest

from app import agent, tools

SWAPPED = "Shakshuka"


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


@pytest.fixture
def drafted(monkeypatch):
    tools.add_recipe("Chili", ingredients=[{"item": "black beans", "qty": "1 can"}],
                     food_groups=["protein", "vegetable", "carb"], default_servings=4)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", lambda spec: {
        "ingredients": [{"item": "carrots", "qty": "1 lb"}], "instructions": ["Cook."]})
    monkeypatch.setattr(agent, "generate_sides_llm", lambda context: [])
    week = _week_start()
    days = []
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            meal = SWAPPED if (slot == "dinner" and day == tools._week_dates(week)[0]) else "Chili"
            days.append({"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": meal == SWAPPED})
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    plan = agent.generate_weekly_plan(week)
    return plan["weekly_plan_id"], tools._week_dates(week)[0]


def _shelf_names():
    return [r["name"] for r in tools.recipe_shelf()]


def test_a_dish_swapped_out_before_approval_is_not_listed_or_counted(drafted):
    plan_id, monday = drafted
    # Written up, so it is no longer pending — the case the walkthrough hit.
    tools.fill_recipe_details(SWAPPED, ingredients=[{"item": "eggs", "qty": "6"}], instructions=["Simmer."], default_servings=4)
    tools.swap_meal_in_plan(plan_id, monday, "Chili", slot="dinner", old_meal=SWAPPED)
    tools.approve_weekly_plan(plan_id)

    assert tools.existing_recipe_named(SWAPPED), "still cached"
    assert SWAPPED not in _shelf_names()
    from app.main import recipes_list
    body = recipes_list()
    assert body["count"] == len(body["recipes"]) == len(tools.recipe_shelf())
    assert SWAPPED not in [r["name"] for r in body["recipes"]]


def test_the_same_dish_is_listed_once_it_is_on_an_approved_week(drafted):
    plan_id, _ = drafted
    tools.fill_recipe_details(SWAPPED, ingredients=[{"item": "eggs", "qty": "6"}], instructions=["Simmer."], default_servings=4)
    tools.approve_weekly_plan(plan_id)
    assert SWAPPED in _shelf_names()


def test_a_recipe_a_person_saved_is_always_listed(drafted):
    tools.add_recipe("Grandma's Soup", ingredients=[{"item": "leeks", "qty": "3"}], instructions=["Simmer."], default_servings=4)
    assert "Grandma's Soup" in _shelf_names()
