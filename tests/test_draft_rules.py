"""Card "The draft breaks the household's own rules" (2026-10-06): a NEW
household's FIRST week holds the child's cold packed lunch and a week's
protein variety, through generation itself."""
from __future__ import annotations

import datetime
import threading

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import allergen_gate, draft_rules


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, protein=None):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
         "food_groups": ["protein", "vegetable", "carb"]}
    if protein:
        d["main_protein"] = protein
    return d


def _meals(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, mpe.slot, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal, r.main_protein "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? "
        "ORDER BY mpe.date, mpe.slot", (plan_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture
def quick(monkeypatch):
    asked = []
    lock = threading.Lock()

    def pick(context):
        with lock:
            asked.append(context)
            n = len(asked)
        if context.get("slot") == "lunch" and context.get("must_be"):
            return {"meal_name": f"Hummus veggie wrap {n}", "ingredients": ["Hummus", "Tortilla"],
                    "dish_note": "Cold.", "main_protein": "chickpeas"}
        return {"meal_name": f"Lentil bowl {n}", "ingredients": ["Lentils", "Rice"], "dish_note": "Simple.",
                "main_protein": "lentils"}

    monkeypatch.setattr(allergen_gate, "quick_pick", pick)
    return asked


def test_a_cold_packed_childs_weekday_lunch_is_never_a_hot_dish(monkeypatch, quick):
    tools.add_member("Emily")
    tools.add_member("Mia")
    tools.save_member_needs(lunch_needs={"Mia": {"needs": ["cold_packed", "nut_free"]}})
    dates = tools._week_dates(_monday())
    days = []
    for d in dates:
        days += [_slot(d, "breakfast", "Oats"), _slot(d, "lunch", "Rice skillet", "chicken"),
                 _slot(d, "dinner", "Pasta primavera", "vegetarian")]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    plan_id = agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    weekday_lunches = [m for m in _meals(plan_id) if m["slot"] == "lunch" and m["date"] in dates[:5]]
    assert weekday_lunches and not any(draft_rules._is_hot(m["meal"]) for m in weekday_lunches), weekday_lunches
    assert any("cold packed" in (c.get("replacing_because") or "") for c in quick)


def test_a_lunch_for_a_thermos_child_may_stay_hot(monkeypatch, quick):
    tools.add_member("Emily")
    tools.add_member("Mia")
    tools.save_member_needs(lunch_needs={"Mia": {"needs": ["thermos"]}})
    dates = tools._week_dates(_monday())
    days = []
    for d in dates:
        days += [_slot(d, "lunch", "Rice skillet"), _slot(d, "dinner", "Pasta primavera", "vegetarian")]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    plan_id = agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    assert all(m["meal"] == "Rice skillet" for m in _meals(plan_id) if m["slot"] == "lunch")


def test_one_protein_is_cooked_at_most_twice_a_week_across_lunch_and_dinner(monkeypatch, quick):
    tools.add_member("Emily")
    dates = tools._week_dates(_monday())
    dinners = [("Beef tacos", "beef"), ("Tofu stir", "tofu"), ("Tilapia with rice", "tilapia"),
               ("Chicken salad", "chicken"), ("Tilapia tacos", "tilapia"), ("Pork chops", "pork"),
               ("Egg fried noodles", "egg")]
    days = []
    for i, d in enumerate(dates):
        days.append(_slot(d, "dinner", *dinners[i]))
        days.append(_slot(d, "lunch", "Tilapia lunch bowl" if i == 3 else f"Bean salad {i}",
                          "tilapia" if i == 3 else "beans"))
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    plan_id = agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    meals = [m for m in _meals(plan_id) if m["slot"] in ("lunch", "dinner") and m["slot_state"] == "planned"]
    tilapia = {(m["meal"]) for m in meals if (m["main_protein"] or "") == "tilapia"}
    assert len(tilapia) <= 2, meals
    assert any((c.get("replacing_because") or "") == "not tilapia again this week." for c in quick)
