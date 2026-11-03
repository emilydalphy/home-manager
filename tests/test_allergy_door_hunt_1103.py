"""
A celiac child is told about in gluten, and a bought wheat food is held
back for them (allergy door hunt, 2026-11-03).

Driven over HTTP on a throwaway DB with Theo marked "Celiac" in Settings:
the import sheet saved "Nugget Night" (one line, "Frozen chicken nuggets")
and "Saturday Pancakes" ("Pancake mix"); an open night took Nugget Night
and Approve went through with both, no ask. The alias table knew
"muffin mix" but none of its siblings. And whenever the gate DID refuse a
celiac household it said the condition as the food: "Spaghetti Bolognese
has celiac, which Theo can’t have".

Also here: a peanut allergy reaches Kung Pao Chicken, which "nut" already
listed because it is made with peanuts.
"""
import datetime

import pytest

from app import tools
from app.tools import allergen_gate as g
from app.tools import moves
from conftest import household_today


TODAY = household_today()
D1 = (TODAY + datetime.timedelta(days=1)).isoformat()


def _theo(restriction="Celiac"):
    tools.add_member("Emily")
    tools.add_member("Theo")
    tools.set_member_dietary_restrictions("Theo", [restriction])


def _import(client, name, item):
    res = client.post("/api/recipes/add", json={
        "name": name, "ingredients": [{"item": item, "qty": "2 cups"}, {"item": "Milk", "qty": "1 cup"}],
        "instructions": ["Mix.", "Cook."], "source_url": "https://example.com/r"})
    assert res.status_code == 200, res.text


def test_a_bought_wheat_food_is_refused_on_an_open_night_and_said_as_gluten(signed_in):
    _theo()
    _import(signed_in, "Nugget Night", "Frozen chicken nuggets")
    start = TODAY.isoformat()
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_slot_open(plan_id, D1, "dinner", "I would rather ask than guess.")
    res = signed_in.post(f"/api/week/{start}/slot", json={
        "date": D1, "slot": "dinner", "choice": "Nugget Night", "weekly_plan_id": plan_id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("status") == "refused", body
    assert body["message"] == "Nugget Night has gluten, which Theo can’t have."


@pytest.mark.parametrize("item", ["Pancake mix", "Bisquick", "Frozen waffles", "Frozen pierogi",
                                  "Fish sticks", "Breaded chicken cutlets", "Brownie mix"])
def test_approve_asks_about_a_bought_wheat_food(item):
    _theo()
    tools.add_recipe("Saturday Breakfast", ingredients=[{"item": item, "qty": "1 box"}])
    plan_id = tools.create_weekly_plan(TODAY.isoformat())["weekly_plan_id"]
    tools.plan_meal(D1, "Saturday Breakfast", slot="breakfast", weekly_plan_id=plan_id)
    assert tools.approve_weekly_plan(plan_id, approved_by="Emily")["status"] == "needs_confirmation"


@pytest.mark.parametrize("item", ["Gluten-free pancake mix", "Frozen waffle fries", "Nugget potatoes",
                                  "Corn tortillas"])
def test_what_is_not_wheat_still_goes_through(item):
    _theo()
    assert g.hard_clashes("Side", [{"item": item}]) == []


def test_coeliac_after_approval_says_gluten_on_today():
    tools.add_member("Emily")
    tools.add_member("Theo")
    tools.add_recipe("Spaghetti Bolognese", ingredients=[{"item": "Spaghetti", "qty": "1 lb"}])
    plan_id = tools.create_weekly_plan((TODAY - datetime.timedelta(days=TODAY.weekday())).isoformat())["weekly_plan_id"]
    tools.plan_meal(TODAY.isoformat(), "Spaghetti Bolognese", slot="dinner", weekly_plan_id=plan_id)
    assert tools.approve_weekly_plan(plan_id, approved_by="Emily")["status"] == "approved"
    tools.set_member_dietary_restrictions("Theo", ["coeliac"])
    noon = datetime.datetime.combine(TODAY, datetime.time(12, 0))
    row = next(m for m in moves.moves_for_day(day=TODAY, now=noon) if m["title"] == "Spaghetti Bolognese")
    assert row["reason"].startswith("Has gluten, which Theo can’t have — swap it on Plan")
    assert "coeliac" not in row["reason"].lower()


def test_a_peanut_allergy_reaches_kung_pao_from_chat():
    _theo("peanut allergy")
    plan_id = tools.create_weekly_plan(TODAY.isoformat())["weekly_plan_id"]
    with pytest.raises(tools.SlotRefused) as refused:
        tools.plan_meal_for_chat(D1, "Kung Pao Chicken", slot="dinner", weekly_plan_id=plan_id)
    assert "Theo can’t have" in str(refused.value)
