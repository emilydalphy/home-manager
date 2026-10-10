"""
"Have it" then Put back re-reads the line from the meals still on it
(2026-10-10, overnight hunt in the Shop tab).

Repro, through the real routes on a throwaway database: two frittatas put
"spinach · 10 oz" on the list. "Have it" (POST /pre-shop, drop) takes it
off; one frittata is dropped; Put back (POST /pre-shop-undo) brought back
"10 oz" for the one frittata (5 oz) left — the meal reversal skips a line
that is off the list, so the dropped night's share was still in it.
"""
from __future__ import annotations

from datetime import timedelta

from conftest import household_today

from app import tools
from app.db import get_conn


def _next_week(day: int) -> str:
    today = household_today()
    return (today - timedelta(days=today.weekday()) + timedelta(days=7 + day)).isoformat()


def _spinach() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, quantity, status FROM grocery_items WHERE item = 'spinach' AND status = 'needed'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _two_frittatas() -> tuple[int, int, list[int]]:
    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)
    tools.add_recipe(
        "Frittata",
        ingredients=[{"item": "eggs", "qty": "8"}, {"item": "spinach", "qty": "5 oz"}],
        default_servings=3,
    )
    plan_id = tools.create_weekly_plan(_next_week(0))["weekly_plan_id"]
    entries = [
        tools.plan_meal(_next_week(d), "Frittata", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for d in (1, 3)
    ]
    tools.approve_weekly_plan(plan_id, "Alex")
    [line] = _spinach()
    assert line["quantity"] == "10 oz"
    return line["id"], plan_id, entries


def test_put_back_after_a_night_dropped_is_what_the_meals_left_need(signed_in):
    line_id, plan_id, entries = _two_frittatas()
    assert signed_in.post(f"/api/grocery-list/{line_id}/pre-shop", json={"decision": "drop"}).status_code == 200
    tools.drop_dish_from_day(plan_id, entries[1])
    assert signed_in.post(f"/api/grocery-list/{line_id}/pre-shop-undo").status_code == 200
    assert _spinach() == [{"id": line_id, "quantity": "5 oz", "status": "needed"}]


def test_put_back_with_nothing_changed_is_the_same_amount(signed_in):
    line_id, _, _ = _two_frittatas()
    signed_in.post(f"/api/grocery-list/{line_id}/pre-shop", json={"decision": "drop"})
    signed_in.post(f"/api/grocery-list/{line_id}/pre-shop-undo")
    assert _spinach() == [{"id": line_id, "quantity": "10 oz", "status": "needed"}]


def test_a_households_own_line_is_put_back_as_it_was(signed_in):
    item_id = tools.add_grocery_item("spinach", quantity="3 oz")["item_id"]
    signed_in.post(f"/api/grocery-list/{item_id}/pre-shop", json={"decision": "drop"})
    signed_in.post(f"/api/grocery-list/{item_id}/pre-shop-undo")
    assert _spinach() == [{"id": item_id, "quantity": "3 oz", "status": "needed"}]
