"""
Breads and breaded foods by definition reach a celiac child
(first-week defect hunt, 2026-11-10).

Driven over HTTP on a throwaway DB: a new household (two adults, Ava with a
peanut allergy, Leo "Celiac") approved its first week, and the approve
sweep re-picked Leo's gluten lunch onto the library's Misal Pav, whose
list says "Pav rolls". Chat's plan_meal put "French Toast" and
"Chicken Katsu" on the week, an open night took "French toast", and a
Tweak's "Something else" took "Chicken schnitzel" — all on the name alone,
because none of these words was in the gluten family.
"""
import datetime

import pytest

from app import tools
from app.tools import allergen_gate as g
from conftest import household_today


TODAY = household_today()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()


def _leo():
    tools.add_member("Maya")
    tools.add_member("Leo")
    tools.set_member_dietary_restrictions("Leo", ["Celiac"])


def test_the_librarys_misal_pav_list_is_held_for_a_celiac_child():
    _leo()
    clashes = g.hard_clashes("Misal Pav", [
        {"item": "Moth bean sprouts"}, {"item": "Farsan"}, {"item": "Pav rolls"}, {"item": "Lemon"},
    ])
    assert clashes and g._food_word(clashes) == "gluten"


@pytest.mark.parametrize("dish", ["French Toast", "Chicken Katsu", "Pork Tonkatsu", "Wiener Schnitzel", "Pav Bhaji"])
def test_chat_cannot_plan_a_wheat_dish_by_name(dish):
    _leo()
    with pytest.raises(tools.SlotRefused) as refused:
        tools.plan_meal_for_chat(D1, dish, slot="dinner")
    assert "gluten" in str(refused.value)


def test_an_open_night_refuses_french_toast(signed_in):
    _leo()
    start = TODAY.isoformat()
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_slot_open(plan_id, D1, "breakfast", "I would rather ask than guess.")
    res = signed_in.post(f"/api/week/{start}/slot", json={
        "date": D1, "slot": "breakfast", "choice": "French toast", "weekly_plan_id": plan_id})
    assert res.status_code == 200, res.text
    assert res.json().get("status") == "refused", res.json()


@pytest.mark.parametrize("name, item", [
    ("Pavlova", "Egg whites"), ("Mini Pavlovas", "Caster sugar"),
    ("Tawa Pulao", "Pav bhaji masala"), ("Breakfast", "Gluten-free French toast"),
])
def test_what_is_not_wheat_still_goes_through(name, item):
    _leo()
    assert g.hard_clashes(name, [{"item": item}]) == []
