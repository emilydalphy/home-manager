"""
Swapping a night to ANOTHER dish that uses the same frozen item keeps the
household's freezer answer (Loop Board card 4031f4c052318117b7b2d5972b994cfe;
Emily 2026-10-26 "go with your picks": carry the freezer answer over to the
new dish).

Salmon Bowls Tuesday and Friday, approved, the freezer step answered
"Salmon fillets: in the freezer". Friday swapped to Salmon Tacos — also
salmon. Before: the tacos' salmon went on the list to buy, as though the
answer had never been given.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn

pytestmark = pytest.mark.today("2026-10-12 10:00")
MON = datetime.date(2026, 10, 12)


def _day(n: int) -> str:
    return (MON + datetime.timedelta(days=n)).isoformat()


def _week():
    _recipes()
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tools.plan_meal(_day(1), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(4), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    return plan_id


def _recipes():
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.add_recipe(
        "Salmon Bowls",
        ingredients=[
            {"item": "Salmon fillets", "qty": "8 oz", "category": "seafood"},
            {"item": "Rice", "qty": "2 cups", "category": "pantry"},
        ],
        instructions=["Cook the rice.", "Bake the salmon."],
    )
    tools.add_recipe(
        "Salmon Tacos",
        ingredients=[
            {"item": "Salmon fillets", "qty": "8 oz", "category": "seafood"},
            {"item": "Tortillas", "qty": "8", "category": "pantry"},
        ],
        instructions=["Flake the salmon.", "Fill the tortillas."],
    )
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "2 cans", "category": "pantry"}],
                     instructions=["Simmer."])


def _to_buy(item="Salmon fillets"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM grocery_items WHERE item = ? AND status IN ('needed', 'in_cart') "
        "AND excluded_from_list = 0 ORDER BY id", (item,),
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def _moves(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT related_meal, task_date FROM prep_tasks WHERE weekly_plan_id = ? AND task_type = 'defrost' "
        "AND status = 'pending' ORDER BY task_date", (plan_id,),
    ).fetchall()
    conn.close()
    return [(r["related_meal"], r["task_date"]) for r in rows]


def test_swapping_to_another_salmon_dish_buys_no_salmon(client):
    plan_id = _week()
    assert _to_buy() == []

    tools.swap_meal_in_plan(plan_id, _day(4), "Salmon Tacos", slot="dinner")

    assert _to_buy() == [], "the household said the salmon is in the freezer"
    assert _to_buy("Tortillas") != [], "the new dish's other things are still bought"
    meals = [m for m, _ in _moves(plan_id)]
    assert "Salmon Tacos" in meals, "the tacos' salmon gets its own fridge move"
    assert "Salmon Bowls" in meals, "Tuesday's move is untouched"
    item = next(i for i in tools.meat_items_for_plan(plan_id) if i["item"] == "Salmon fillets")
    assert item["frozen"] is True


def test_the_only_salmon_night_swapped_to_another_salmon_dish_buys_none(client):
    """The card's case: the frozen item was on that one night only."""
    _recipes()
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tue, fri = _day(1), _day(4)
    tools.plan_meal(tue, "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(fri, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    assert _to_buy() == []

    tools.swap_meal_in_plan(plan_id, tue, "Salmon Tacos", slot="dinner")

    assert _to_buy() == [], "the answer carries over: no new salmon to buy"
    assert [m for m, _ in _moves(plan_id)] == ["Salmon Tacos"]
    item = next(i for i in tools.meat_items_for_plan(plan_id) if i["item"] == "Salmon fillets")
    assert item["frozen"] is True


def test_swapping_to_a_dish_without_it_still_drops_the_move(client):
    plan_id = _week()
    tools.swap_meal_in_plan(plan_id, _day(4), "Bean Chili", slot="dinner")
    assert [m for m, _ in _moves(plan_id)] == ["Salmon Bowls"]


def test_the_only_night_swapped_to_a_dish_without_it_books_nothing(client):
    _recipes()
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    tools.plan_meal(_day(1), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    tools.swap_meal_in_plan(plan_id, _day(1), "Bean Chili", slot="dinner")
    assert _moves(plan_id) == []
    assert _to_buy() == []
