"""
"Different dishes a week" holds even when the dish to drop opens the week,
and the protein re-pick that runs after the count's last guard keeps to it.

Tester Home, the week of 2026-10-19, Dinners 2 and Lunches 2: the morning
report's dish_count_respected tripwire said three of each. Generation's own
guard (meal_variety.enforce_distinct_count) left the dropped dish standing
when it held the slot's first night with nothing earlier to feed it and the
kept dishes in chains the model wrote. And draft_rules.enforce_protein_
variety, which runs after both the guard and the tripwire, put a third lunch
dish on a two-lunch week with nobody told.

The model and the re-pick are stubbed; nothing reaches the API.
"""
from __future__ import annotations

import pytest

from app import agent, tools
from app.tools import allergen_gate, meal_variety
from test_meal_variety import _monday, _week


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


@pytest.fixture
def shrimp_picks(monkeypatch):
    """Every quick pick is a new shrimp dish, so a protein re-pick lands."""
    n = iter(range(100))

    def _pick(context, *a, **k):
        name = f"Shrimp Dish {next(n)}"
        return {"meal_name": name, "reason": "not chicken", "main_protein": "shrimp",
                "ingredients": [{"item": "shrimp", "qty": "1 lb", "category": "seafood"}],
                "instructions": ["Cook the shrimp.", "Serve."], "food_groups": ["protein"],
                "prep_time_minutes": 5, "cook_time_minutes": 10}
    monkeypatch.setattr(allergen_gate, "quick_pick", _pick)


def test_a_dropped_dish_on_the_first_night_does_not_stay_on_the_week(stub_model):
    """CATCH on main: Kofte (Monday only) is the one unchained dish, so it
    is the one picked to go; Monday has no earlier
    cook and both kept dishes are model-written chains, so the night stood
    and the week kept three dinners against Dinners 2."""
    for name in ("Kofte", "Chili", "Salmon", "Toast"):
        tools.add_recipe(name, ingredients=[{"item": f"{name} stuff", "qty": "1"}], main_protein=name)
    tools.set_household_meal_preferences(dinners_per_week=2)
    week = _monday()
    d = tools._week_dates(week)
    stub_model(_week(
        week, ["Kofte", "Chili", "Chili", "Salmon", "Salmon", "Chili", "Salmon"],
        dinner_extra={
            d[1]: {"derived_from": {"make_double_for": [f"{d[2]}:dinner"]}},
            d[2]: {"derived_from": {"links_to": f"{d[1]}:dinner"}},
            d[3]: {"derived_from": {"make_double_for": [f"{d[4]}:dinner"]}},
            d[4]: {"derived_from": {"links_to": f"{d[3]}:dinner"}},
        },
    ))

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert len(meal_variety.distinct_dishes(plan_id, "dinner")) == 2
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    assert not [v for v in tools.get_recent_plan_quality()["recent"] if v["rule"] == "dish_count_respected"]


def _chicken_week(stub_model, lunches_per_week):
    for name in ("Chicken Souvlaki", "Lebanese Chicken", "Thai Chicken Bowl"):
        tools.add_recipe(name, ingredients=[{"item": "chicken", "qty": "1 lb"}], main_protein="chicken")
    tools.add_recipe("Bean Stew", ingredients=[{"item": "beans", "qty": "1 can"}], main_protein="vegetarian")
    tools.add_recipe("Toast", ingredients=[{"item": "bread", "qty": "1 loaf"}])
    tools.set_household_meal_preferences(dinners_per_week=2, lunches_per_week=lunches_per_week)
    week = _monday()
    days = _week(week, ["Chicken Souvlaki"] * 4 + ["Lebanese Chicken"] * 3)
    # Thai Chicken Bowl cooked Monday (eaten to Thursday) and again Sunday:
    # with the two chicken dinners, Sunday's lunch is a fourth chicken cook.
    lunches = iter(["Thai Chicken Bowl"] * 4 + ["Bean Stew"] * 2 + ["Thai Chicken Bowl"])
    for day in days:
        if day["slot"] == "lunch":
            day["meal_name"] = next(lunches)
    stub_model(days)
    return agent.generate_weekly_plan(week)["weekly_plan_id"]


def test_the_protein_repick_does_not_add_a_dish_past_the_count(stub_model, shrimp_picks):
    """CATCH on main: Sunday's lunch was re-picked to a shrimp dish — a
    third lunch dish against Lunches 2, after the tripwire had looked."""
    plan_id = _chicken_week(stub_model, lunches_per_week=2)

    assert len(meal_variety.distinct_dishes(plan_id, "lunch")) == 2
    assert len(meal_variety.distinct_dishes(plan_id, "dinner")) == 2


def test_the_protein_repick_still_runs_where_the_count_has_room(stub_model, shrimp_picks):
    """Lunches 3: a third lunch dish is inside the number, so the fourth
    chicken cook is still re-picked."""
    plan_id = _chicken_week(stub_model, lunches_per_week=3)

    lunches = meal_variety.distinct_dishes(plan_id, "lunch")
    assert any(name.startswith("Shrimp Dish") for name in lunches)
    assert len(lunches) == 3
