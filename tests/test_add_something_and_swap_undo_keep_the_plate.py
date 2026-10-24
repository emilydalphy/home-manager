"""
Changing one meal, two holes found by driving it (overnight hunt, 2026-10-24).

1. The meal screen's "Add something" sheet (plates.suggest_additions) was
   the one option sheet with no household gate: a gluten-free adult was
   offered Crusty bread, Couscous and Orzo, a dairy-free one Garlic yogurt
   sauce, and add_component planted and bought whichever was tapped — or
   typed. It also wrote a side onto a night that had gone by, which Swap
   and Change both refuse.
2. Swap, then the toast's Undo, put the old dish back with nothing beside
   it: the sides the swap left behind (a household's Roasted potatoes) were
   gone from the meal and their lines from the list.

The model is never called: the swap's `picker` seam, as
tests/test_swap_in_place.py uses it.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import household_today

from app import tools
from app.tools import plates
from app.tools import swap_in_place as sip
from app.tools import weekly_plan as _wp

TODAY = household_today()
START = TODAY - datetime.timedelta(days=2)
YESTERDAY = (TODAY - datetime.timedelta(days=1)).isoformat()
DAY1 = TODAY.isoformat()
DAY2 = (TODAY + datetime.timedelta(days=1)).isoformat()


def _pick(name="Lemon Chicken Traybake"):
    return {
        "meal_name": name, "reason": "Lighter.", "is_new_recipe": True,
        "ingredients": [{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
                        {"item": "Lemons", "qty": "3", "category": "produce"}],
        "instructions": ["Heat the oven.", "Roast."],
        "food_groups": ["protein", "vegetable"], "main_protein": "chicken",
        "prep_time_minutes": 10, "cook_time_minutes": 30, "default_servings": 2,
    }


def _seed(approve: bool = True, restrictions=("dairy", "gluten")) -> tuple[int, dict]:
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    if restrictions:
        tools.set_member_dietary_restrictions("Emily", list(restrictions))
    for name, item in (("Pork Chops", "Pork chops"), ("Beef Chili", "Ground beef")):
        tools.add_recipe(name, ingredients=[{"item": item, "qty": "1 lb", "category": "meat/seafood"}],
                         instructions=["Cook it."], food_groups=["protein", "vegetable"],
                         prep_time_minutes=10, cook_time_minutes=20)
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    ids = {
        "past": tools.plan_meal(YESTERDAY, "Beef Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"],
        "today": tools.plan_meal(DAY1, "Pork Chops", slot="dinner", weekly_plan_id=plan_id)["entry_id"],
        "next": tools.plan_meal(DAY2, "Beef Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"],
    }
    if approve:
        tools.approve_weekly_plan(plan_id)
    return plan_id, ids


def _needed() -> set[str]:
    return {i["item"] for i in tools.list_grocery_list() if i.get("status") == "needed"}


def _offered(plan_id, entry_id, role=None) -> list[str]:
    return [o["name"] for o in plates.suggest_additions(entry_id, weekly_plan_id=plan_id, role=role)["options"]]


def _dinner_id(plan_id, date) -> int:
    from app.db import get_conn
    conn = get_conn()
    row = conn.execute("SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? "
                       "AND slot = 'dinner' AND slot_state = 'planned'", (plan_id, date)).fetchone()
    conn.close()
    return row["id"]


def _sides(entry_id) -> list[str]:
    return [s["name"] for s in plates.get_sides(entry_id)]


# ---------------------------------------------- 1. the Add something sheet

def test_the_sheet_offers_nothing_someone_at_the_table_cant_have():
    plan_id, ids = _seed()
    general = _offered(plan_id, ids["today"])
    carbs = _offered(plan_id, ids["today"], role="carb")
    sauces = [o["name"] for o in plates.suggest_additions(ids["today"], weekly_plan_id=plan_id)["options"]
              if o["kind"] == "sauce"]
    for gone in ("Crusty bread", "Couscous", "Orzo", "Warm pita", "Garlic yogurt sauce"):
        assert gone not in general and gone not in carbs and gone not in sauces
    # A held-back row frees its place: still three starches on the general sheet.
    assert general[:3] == ["Roasted potatoes", "Rice", "Quinoa"]
    assert "Quinoa" in carbs and "Tortillas" in carbs


def test_a_house_with_no_restrictions_still_sees_the_whole_catalogue():
    plan_id, ids = _seed(restrictions=())
    carbs = _offered(plan_id, ids["today"], role="carb")
    assert "Crusty bread" in carbs and "Couscous" in carbs


def test_a_wont_eat_food_is_held_back_too():
    plan_id, ids = _seed(restrictions=())
    tools.edit_preference("dislikes", ["quinoa"])
    assert "Quinoa" not in _offered(plan_id, ids["today"], role="carb")


@pytest.mark.parametrize("kwargs, said", [
    ({"key": "crusty-bread"}, "I left it off — Crusty bread clashes with gluten."),
    ({"text": "garlic yogurt sauce"}, "I left it off — Garlic yogurt sauce clashes with dairy."),
    ({"text": "cheese sauce"}, "I left it off — Cheese sauce clashes with dairy."),
])
def test_a_clashing_side_is_refused_and_nothing_is_written(kwargs, said):
    plan_id, ids = _seed()
    before = _needed()
    with pytest.raises(_wp.SlotRefused) as excinfo:
        plates.add_component(ids["today"], weekly_plan_id=plan_id,
                             side_generator=lambda ctx: [], **kwargs)
    assert str(excinfo.value) == said
    assert _sides(ids["today"]) == []
    assert _needed() == before


def test_a_safe_side_still_goes_on_and_is_bought():
    plan_id, ids = _seed()
    out = plates.add_component(ids["today"], key="roasted-potatoes", weekly_plan_id=plan_id)
    assert out["status"] == "added"
    assert "Yukon Gold potatoes" in _needed()


def test_a_night_gone_by_takes_no_new_side():
    plan_id, ids = _seed(approve=False, restrictions=())
    with pytest.raises(_wp.SlotRefused) as excinfo:
        plates.add_component(ids["past"], key="rice", weekly_plan_id=plan_id)
    assert str(excinfo.value) == _wp.NIGHT_GONE
    assert _sides(ids["past"]) == []


def test_the_route_answers_a_refusal_as_409_with_the_sentence(signed_in):
    plan_id, ids = _seed()
    res = signed_in.post(f"/api/week/{START.isoformat()}/add-component",
                      json={"entry_id": ids["today"], "key": "orzo"})
    assert res.status_code == 409
    assert res.json()["detail"] == "I left it off — Orzo clashes with gluten."


# ---------------------------------------------- 2. Swap, then Undo

def test_undo_puts_back_the_sides_the_swap_left_behind():
    plan_id, ids = _seed(restrictions=())
    plates.add_component(ids["today"], key="roasted-potatoes", weekly_plan_id=plan_id)
    out = sip.swap_meal_in_place(plan_id, ids["today"], picker=lambda ctx: _pick())
    assert out["status"] == "swapped"
    assert _sides(out["entry_id"]) == []          # a swap still leaves them behind
    assert "Yukon Gold potatoes" not in _needed()

    back = sip.undo_meal_swap(plan_id, out["entry_id"])
    assert back["meal"] == "Pork Chops"
    assert _sides(back["entry_id"]) == ["Roasted potatoes"]
    assert "Yukon Gold potatoes" in _needed()


def test_two_swaps_then_undo_puts_back_the_originals_sides_only():
    plan_id, ids = _seed(restrictions=())
    plates.add_component(ids["today"], key="roasted-potatoes", weekly_plan_id=plan_id)
    first = sip.swap_meal_in_place(plan_id, ids["today"], picker=lambda ctx: _pick())
    plates.add_component(first["entry_id"], key="green-salad", weekly_plan_id=plan_id)
    second = sip.swap_meal_in_place(plan_id, first["entry_id"],
                                    picker=lambda ctx: _pick("Salmon Traybake"))
    back = sip.undo_meal_swap(plan_id, second["entry_id"])
    assert back["meal"] == "Pork Chops"
    assert _sides(back["entry_id"]) == ["Roasted potatoes"]
    assert "Mixed greens" not in _needed()


def test_a_change_then_a_swap_then_undo_keeps_the_sides():
    """The Change carried the sides and wrote no copy of its own; the Swap
    after it has to record the ones on the entry, which are the original's."""
    plan_id, ids = _seed(restrictions=())
    plates.add_component(ids["next"], key="green-salad", weekly_plan_id=plan_id)
    changed = sip.apply_pick(plan_id, sip._entry(plan_id, ids["next"]), _pick("Turkey Chili"),
                             carry_sides=True, correct_title=False)
    assert _sides(changed["entry_id"]) == ["Green salad"]
    swapped = sip.swap_meal_in_place(plan_id, changed["entry_id"], picker=lambda ctx: _pick())
    back = sip.undo_meal_swap(plan_id, swapped["entry_id"])
    assert back["meal"] == "Beef Chili"
    assert _sides(back["entry_id"]) == ["Green salad"]
    assert "Mixed greens" in _needed()


def test_a_whole_dish_swap_then_undo_puts_back_each_days_own_sides():
    plan_id, ids = _seed(restrictions=())
    day3 = (TODAY + datetime.timedelta(days=2)).isoformat()
    third = tools.plan_meal(day3, "Beef Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    plates.add_component(ids["next"], key="green-salad", weekly_plan_id=plan_id)
    plates.add_component(third, key="rice", weekly_plan_id=plan_id)
    out = sip.swap_meal_in_place(plan_id, ids["next"], picker=lambda ctx: _pick(), whole_dish=True)
    assert len(out["entry_ids"]) == 2
    back = sip.undo_meal_swap(plan_id, out["entry_ids"][0])
    sides = {d: _sides(_dinner_id(plan_id, d)) for d in (DAY2, day3)}
    assert back["meal"] == "Beef Chili"
    assert sides == {DAY2: ["Green salad"], day3: ["Rice"]}


def test_a_swap_then_a_change_then_undo_goes_back_to_the_first_plate():
    plan_id, ids = _seed(restrictions=())
    plates.add_component(ids["today"], key="roasted-potatoes", weekly_plan_id=plan_id)
    swapped = sip.swap_meal_in_place(plan_id, ids["today"], picker=lambda ctx: _pick())
    plates.add_component(swapped["entry_id"], key="green-salad", weekly_plan_id=plan_id)
    changed = sip.apply_pick(plan_id, sip._entry(plan_id, swapped["entry_id"]),
                             _pick("Lemon Turkey Traybake"), carry_sides=True, correct_title=False)
    back = sip.undo_meal_swap(plan_id, changed["entry_id"])
    assert back["meal"] == "Pork Chops"
    assert _sides(back["entry_id"]) == ["Roasted potatoes"]
    assert "Mixed greens" not in _needed()
