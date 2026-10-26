"""
A dish approved before its recipe was written is bought once it is written
(defect hunt, 2026-10-24).

Found driving the first run on a throwaway DB: a first week with one new
dish (Tuesday's Coconut Lentil Curry), the recipe writer down when Approve
was tapped. Approval goes ahead on purpose when a recipe can't be written
(weekly_plan._write_pending_recipes), so the curry was approved with no
ingredients and bought nothing. Cook's "Fill in this recipe" then wrote it
— and nothing went back to the list. "Week approved · 16 things to buy"
over a list with no lentils, no coconut milk, no spinach, for good.

Now the one call that clears details_pending (recipes.fill_recipe_details)
buys that recipe's meals on any APPROVED week, from today on, through the
same query and grouped ingest approval uses — and only that recipe's.
"""
import datetime

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import weekly_plan


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


NEW_DINNER = "Coconut Lentil Curry"

DETAILS = {
    "ingredients": [
        {"item": "Red lentils", "qty": "1 cup", "category": "pantry"},
        {"item": "Coconut milk", "qty": "1 can", "category": "canned"},
        {"item": "Cauliflower", "qty": "1 head", "category": "produce"},
    ],
    "instructions": [
        "Simmer the lentils and cauliflower in the coconut milk for 20 minutes.",
        "Season and serve.",
    ],
    "default_servings": 2,
    "prep_time_minutes": 10,
    "cook_time_minutes": 25,
}


def _menu_week(week: str) -> list[dict]:
    days = []
    for i, day in enumerate(tools._week_dates(week)):
        for slot in tools.WEEK_SLOTS:
            if slot == "dinner" and i == 1:
                days.append({
                    "date": day, "slot": slot, "meal_name": NEW_DINNER, "is_new_recipe": True,
                    "cuisine": "Indian", "main_protein": "lentils",
                    "food_groups": ["protein", "vegetable"],
                    "prep_time_minutes": 10, "cook_time_minutes": 25,
                    "dish_note": "simmer red lentils in coconut milk",
                })
            elif slot == "dinner":
                days.append({"date": day, "slot": slot, "meal_name": "Toast", "is_new_recipe": False})
            else:
                days.append({"date": day, "slot": slot, "meal_name": "Toast", "is_new_recipe": False})
    return days


@pytest.fixture
def household(monkeypatch):
    tools.add_member("Emily")
    tools.add_member("Ana")
    tools.add_recipe("Toast", ingredients=[{"item": "bread", "qty": "1 loaf"}])
    tools.edit_preference("complete_plates", False)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: _menu_week(_week_start()))


@pytest.fixture
def writer(monkeypatch):
    """The recipe writer: down until `up()` is called."""
    state = {"up": False}

    def _fake(spec):
        if not state["up"]:
            raise RuntimeError("the recipe writer is down")
        return dict(DETAILS)
    monkeypatch.setattr(agent, "generate_recipe_details_llm", _fake)
    state["up_fn"] = lambda: state.update(up=True)
    return state


def _list_lines(*names):
    wanted = [n.lower() for n in names]
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = 1 "
        "AND status IN ('needed', 'spice')"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows if r["item"].lower() in wanted]


def _approved_with_the_writer_down(writer):
    plan = agent.generate_weekly_plan(_week_start())
    result = tools.approve_weekly_plan(plan["weekly_plan_id"])
    assert result["status"] == "approved"
    assert tools.get_recipe(NEW_DINNER)["details_pending"] is True
    assert _list_lines("Red lentils", "Coconut milk", "Cauliflower") == []
    return plan["weekly_plan_id"]


def test_writing_the_recipe_after_approval_puts_its_ingredients_on_the_list(household, writer):
    _approved_with_the_writer_down(writer)
    writer["up_fn"]()

    filled = agent.fill_in_recipe(NEW_DINNER)

    assert filled["details_pending"] is False
    lines = _list_lines("Red lentils", "Coconut milk", "Cauliflower")
    assert sorted(l["item"].lower() for l in lines) == ["cauliflower", "coconut milk", "red lentils"], (
        "the curry was approved, then written, and its ingredients never reached the list"
    )
    # Recorded in the ledger like any approval's buy, so dropping or
    # swapping Tuesday later takes them back off.
    conn = get_conn()
    linked = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_grocery_links mpgl JOIN meal_plan_entries mpe "
        "ON mpe.id = mpgl.meal_plan_entry_id JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE LOWER(r.name) = LOWER(?)", (NEW_DINNER,),
    ).fetchone()["n"]
    conn.close()
    assert linked == 3


def test_it_is_bought_once_however_often_it_is_asked_for(household, writer):
    plan_id = _approved_with_the_writer_down(writer)
    writer["up_fn"]()
    agent.fill_in_recipe(NEW_DINNER)
    before = _list_lines("Red lentils", "Coconut milk", "Cauliflower")

    agent.fill_in_recipe(NEW_DINNER)             # already written: a no-op
    tools.approve_weekly_plan(plan_id)           # re-approval adds nothing
    weekly_plan.buy_for_late_written_recipe(tools.get_recipe(NEW_DINNER)["id"])

    assert _list_lines("Red lentils", "Coconut milk", "Cauliflower") == before


def test_a_draft_is_left_for_its_approval_to_buy(household, writer):
    writer["up_fn"]()
    plan = agent.generate_weekly_plan(_week_start())

    agent.fill_in_recipe(NEW_DINNER)

    assert _list_lines("Red lentils", "Coconut milk", "Cauliflower") == [], (
        "a draft is not a yes — nothing reaches the list before approval"
    )
    tools.approve_weekly_plan(plan["weekly_plan_id"])
    lines = _list_lines("Red lentils", "Coconut milk", "Cauliflower")
    assert len(lines) == 3, "and approval buys it once, not twice"


def test_only_the_written_recipes_meals_are_bought(household, writer):
    """An entry with no ledger row is not always one approval missed: one
    whose every ingredient was already in the kitchen has none either, and
    approval's promise is that nothing re-buys it later."""
    plan_id = _approved_with_the_writer_down(writer)
    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_grocery_links WHERE household_id = 1 AND meal_plan_entry_id IN ("
        "SELECT mpe.id FROM meal_plan_entries mpe JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND r.name = 'Toast')", (plan_id,),
    )
    conn.execute("DELETE FROM grocery_items WHERE household_id = 1 AND LOWER(item) = 'bread'")
    conn.commit()
    conn.close()
    writer["up_fn"]()

    agent.fill_in_recipe(NEW_DINNER)

    assert _list_lines("bread") == []
    assert len(_list_lines("Red lentils", "Coconut milk", "Cauliflower")) == 3


def test_the_cook_screens_fill_in_route_buys_it(household, writer, signed_in):
    week = _week_start()
    plan = agent.generate_weekly_plan(week)
    res = signed_in.post(f"/api/week/{week}/approve", json={})
    assert res.status_code == 200 and res.json()["status"] == "approved"
    writer["up_fn"]()

    res = signed_in.post("/api/cooker/fill-recipe", json={"recipe_name": NEW_DINNER})

    assert res.status_code == 200
    shop = signed_in.get("/api/grocery-list").json()
    names = {i["item"].lower() for s in shop["sections"] for i in s["items"]}
    assert {"coconut milk", "cauliflower"} <= names
    assert plan["weekly_plan_id"]
