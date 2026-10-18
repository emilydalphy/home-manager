"""
A plan's line unticked after a night was dropped asks for what the meals
left need (2026-10-18, overnight hunt in the Shop tab).

Repro, through the real routes on a throwaway database: two stir-fry nights
put "rice · 4 cups" on the list. Tick it (bought); drop one stir-fry night;
untick it (it wasn't bought after all) -> "4 cups" back on the list for the
2 cups one dinner needs. The meal reversal leaves a bought line's amount
alone but deletes the dropped night's ledger rows, so nothing put the line
right when it came back.
"""
from __future__ import annotations

from datetime import timedelta

from conftest import household_today

from app import tools
from app.db import get_conn


def _next_week(day: int) -> str:
    today = household_today()
    return (today - timedelta(days=today.weekday()) + timedelta(days=7 + day)).isoformat()


def _line(item: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, quantity, status FROM grocery_items WHERE item = ?", (item,)
    ).fetchone()
    conn.close()
    return dict(row)


def _two_stir_fries() -> tuple[int, list[int]]:
    tools.add_recipe(
        "Stir Fry",
        ingredients=[{"item": "rice", "qty": "2 cups"}, {"item": "olive oil", "qty": "1 bottle"}],
        default_servings=4,
    )
    plan_id = tools.create_weekly_plan(_next_week(0))["weekly_plan_id"]
    entries = [
        tools.plan_meal(_next_week(d), "Stir Fry", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for d in (1, 3)
    ]
    tools.approve_weekly_plan(plan_id, "Alex")
    assert _line("rice")["quantity"] == "4 cups"
    return plan_id, entries


def _tick(client, item_id: int, status: str) -> None:
    assert client.post(f"/api/grocery-list/{item_id}/status", json={"status": status}).status_code == 200


def test_untick_after_a_night_dropped_is_what_the_meals_left_need(signed_in):
    plan_id, entries = _two_stir_fries()
    rice = _line("rice")["id"]
    _tick(signed_in, rice, "purchased")
    tools.drop_dish_from_day(plan_id, entries[1])
    assert _line("rice")["quantity"] == "4 cups", "a bought line is never changed under the shopper"
    _tick(signed_in, rice, "needed")
    assert _line("rice") == {"id": rice, "quantity": "2 cups", "status": "needed"}


def test_untick_with_nothing_changed_is_the_same_amount(signed_in):
    _two_stir_fries()
    rice = _line("rice")["id"]
    _tick(signed_in, rice, "purchased")
    _tick(signed_in, rice, "needed")
    assert _line("rice") == {"id": rice, "quantity": "4 cups", "status": "needed"}


def test_a_households_edit_down_still_stands_after_the_untick(signed_in):
    plan_id, entries = _two_stir_fries()
    rice = _line("rice")["id"]
    assert signed_in.post(f"/api/grocery-list/{rice}/update", json={"quantity": "1 cup"}).status_code == 200
    _tick(signed_in, rice, "purchased")
    tools.drop_dish_from_day(plan_id, entries[1])
    _tick(signed_in, rice, "needed")
    # The household asked for 1 cup with both nights on; one night gone,
    # their 1 cup is still what they want — never back up to the meals' 2.
    assert _line("rice")["quantity"] == "1 cup"


def test_a_sealed_package_comes_back_as_one_bottle(signed_in):
    plan_id, entries = _two_stir_fries()
    oil = _line("olive oil")
    assert oil["quantity"] == "1 bottle"
    _tick(signed_in, oil["id"], "purchased")
    tools.drop_dish_from_day(plan_id, entries[1])
    _tick(signed_in, oil["id"], "needed")
    assert _line("olive oil")["quantity"] == "1 bottle"
