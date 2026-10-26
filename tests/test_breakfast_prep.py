"""
Breakfast made fresh or made ahead (Emily, 2026-10-26).

Today said Avocado and Egg White Toast was "Cooking for 6 — enough for
Monday, Tuesday, and Thursday": the prep-day rule batched a breakfast
that only works fresh. Now a household says how breakfast gets made
(rhythm.breakfast_prep), and apply_prep_day_batches batches a breakfast
only when it keeps in the fridge and the household doesn't make it fresh.
"""
from __future__ import annotations

import pytest

from app import tools
from app.tools import cook_ahead
from app.tools.breakfast_prep import breakfast_keeps, may_batch_breakfast
from test_batch_from_prep_days import (
    MON, ON_MONDAY, TUE, WED, _derived, _household, _plan, _sunday_prep,
)


# ---------- which breakfasts keep ----------

@pytest.mark.parametrize("name", [
    "Egg White Bites", "Spinach Feta Egg Muffins", "Overnight Oats with Berries",
    "Chocolate Chia Pudding", "Baked Oatmeal Cups", "Freezer Breakfast Burritos",
    "Breakfast Burrito with Scrambled Egg", "Sweet Potato Frittata", "Banana Bread",
    "Greek Yogurt Parfait", "Hard-Boiled Eggs",
])
def test_make_ahead_breakfasts_keep(name):
    assert breakfast_keeps(name) is True


@pytest.mark.parametrize("name", [
    "Avocado and Egg White Toast with Cucumber", "Scrambled Eggs and Toast",
    "Fried Egg Sandwich", "Buttermilk Pancakes", "Poached Eggs on Toast",
    "Shakshuka", "Green Smoothie", "Soft-Boiled Eggs with Soldiers", "Toasted English Muffin with Jam",
])
def test_fresh_breakfasts_dont_keep(name):
    assert breakfast_keeps(name) is False


def test_a_name_that_says_neither_is_unknown():
    assert breakfast_keeps("Cottage Cheese Bowl") is None
    assert breakfast_keeps("Barley Porridge") is None  # "bar" is a word start, not barley


def test_fresh_never_batches_and_make_ahead_only_what_keeps():
    assert may_batch_breakfast("Egg White Bites", "fresh") is False
    assert may_batch_breakfast("Egg White Bites", "make_ahead") is True
    assert may_batch_breakfast("Egg White Bites", None) is True
    assert may_batch_breakfast("Avocado Toast", "make_ahead") is False
    assert may_batch_breakfast("Avocado Toast", None) is False
    assert may_batch_breakfast("Cottage Cheese Bowl", "make_ahead") is True


# ---------- the rhythm answer ----------

def test_breakfast_prep_is_saved_and_read_back():
    assert tools.get_household_rhythm()["breakfast_prep"] is None
    tools.set_breakfast_prep("make_ahead")
    assert tools.get_household_rhythm()["breakfast_prep"] == "make_ahead"
    tools.save_rhythm_answers(breakfast_prep="fresh")
    assert tools.get_household_rhythm()["breakfast_prep"] == "fresh"


def test_a_bad_value_is_refused_and_writes_nothing():
    with pytest.raises(ValueError):
        tools.set_breakfast_prep("whenever")
    with pytest.raises(ValueError):
        tools.save_rhythm_answers(leftovers_stance="love_them", breakfast_prep="whenever")
    rhythm = tools.get_household_rhythm()
    assert rhythm["breakfast_prep"] is None and rhythm["leftovers_stance"] is None


def test_the_rhythm_route_saves_it(signed_in):
    r = signed_in.post("/api/onboarding/rhythm", json={"breakfast_prep": "make_ahead"})
    assert r.status_code == 200, r.text
    assert r.json()["breakfast_prep"] == "make_ahead"
    assert signed_in.post("/api/onboarding/rhythm", json={"breakfast_prep": "x"}).status_code == 400


# ---------- the prep-day batch ----------

def _toast():
    tools.add_recipe(
        "Avocado and Egg White Toast", ingredients=[{"item": "bread", "qty": "2 slices"}],
        default_servings=2, prep_time_minutes=10, cook_time_minutes=10,
        instructions=["Toast the bread.", "Cook the egg whites."],
    )


def _bites():
    tools.add_recipe(
        "Egg White Bites", ingredients=[{"item": "egg whites", "qty": "1 cup"}],
        default_servings=2, prep_time_minutes=10, cook_time_minutes=20,
        instructions=["Heat the oven.", "Bake 20 minutes."],
    )


def _three_mornings(dish):
    return _plan((MON, dish, "breakfast"), (TUE, dish, "breakfast"), (WED, dish, "breakfast"))


@ON_MONDAY
def test_toast_is_never_batched_on_a_prep_week():
    _household()
    _toast()
    _sunday_prep()
    plan_id, ids = _three_mornings("Avocado and Egg White Toast")

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert out["applied"] == []
    assert out["declined"] == [{"kind": "fresh_breakfast", "dish": "Avocado and Egg White Toast"}]
    assert "make_double_for" not in _derived(ids[MON])
    assert tools.plan_leftover_chains(plan_id)["leftovers"] == {}


@ON_MONDAY
def test_a_fresh_household_never_batches_breakfast_even_egg_bites():
    _household()
    _bites()
    _sunday_prep()
    tools.set_breakfast_prep("fresh")
    plan_id, ids = _three_mornings("Egg White Bites")

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert out["applied"] == []
    assert "make_double_for" not in _derived(ids[MON])


@ON_MONDAY
def test_a_make_ahead_household_still_batches_egg_bites():
    _household()
    _bites()
    _sunday_prep()
    tools.set_breakfast_prep("make_ahead")
    plan_id, ids = _three_mornings("Egg White Bites")

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert [a["dish"] for a in out["applied"]] == ["Egg White Bites"]
    assert _derived(ids[MON])["make_double_for"] == [f"{TUE}:breakfast", f"{WED}:breakfast"]


@ON_MONDAY
def test_a_fresh_breakfast_leaves_a_repeated_lunch_batched():
    _household()
    _sunday_prep()
    tools.set_breakfast_prep("fresh")
    tools.add_recipe(
        "Chickpea Salad Bowl", ingredients=[{"item": "chickpeas", "qty": "1 can"}],
        default_servings=2, prep_time_minutes=15, instructions=["Toss everything."],
    )
    plan_id, ids = _plan((MON, "Chickpea Salad Bowl", "lunch"), (TUE, "Chickpea Salad Bowl", "lunch"))

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert [a["dish"] for a in out["applied"]] == ["Chickpea Salad Bowl"]


def test_the_planner_prompt_carries_the_rule():
    from app import agent
    import inspect
    assert "rhythm.breakfast_prep" in inspect.getsource(agent)
    assert any(t["name"] == "set_breakfast_prep" for t in agent.TOOL_DEFINITIONS)
