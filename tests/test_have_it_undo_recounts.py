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


# --- review round: what Put back must NOT change ---


def test_a_package_line_put_back_is_still_one_jar(signed_in):
    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)
    tools.add_recipe("Pasta", ingredients=[{"item": "marinara", "qty": "1 jar"}], default_servings=3)
    plan_id = tools.create_weekly_plan(_next_week(0))["weekly_plan_id"]
    for d in (1, 3):
        tools.plan_meal(_next_week(d), "Pasta", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Alex")
    conn = get_conn()
    line = dict(conn.execute("SELECT id, quantity FROM grocery_items WHERE item = 'marinara'").fetchone())
    conn.close()
    signed_in.post(f"/api/grocery-list/{line['id']}/pre-shop", json={"decision": "drop"})
    signed_in.post(f"/api/grocery-list/{line['id']}/pre-shop-undo")
    conn = get_conn()
    after = conn.execute("SELECT quantity, status FROM grocery_items WHERE id = ?", (line["id"],)).fetchone()
    conn.close()
    assert (after["quantity"], after["status"]) == (line["quantity"], "needed")


def test_an_edited_amount_put_back_with_nothing_changed_is_kept(signed_in):
    line_id, _, _ = _two_frittatas()
    tools.update_grocery_item(line_id, quantity="1 bag")
    signed_in.post(f"/api/grocery-list/{line_id}/pre-shop", json={"decision": "drop"})
    signed_in.post(f"/api/grocery-list/{line_id}/pre-shop-undo")
    assert _spinach() == [{"id": line_id, "quantity": "1 bag", "status": "needed"}]


def test_with_every_meal_gone_while_set_aside_the_row_is_still_there_to_put_back(signed_in):
    line_id, plan_id, entries = _two_frittatas()
    signed_in.post(f"/api/grocery-list/{line_id}/pre-shop", json={"decision": "drop"})
    for e in entries:
        tools.drop_dish_from_day(plan_id, e)
    assert signed_in.post(f"/api/grocery-list/{line_id}/pre-shop-undo").status_code == 200
    # Not deleted while it was off. The first night's going recounted it to
    # one frittata's 5 oz; the last one's leaves it there (the known gap in
    # the decision log: no meal left, nothing to recount from).
    conn = get_conn()
    row = conn.execute("SELECT quantity, status FROM grocery_items WHERE id = ?", (line_id,)).fetchone()
    conn.close()
    assert (row["status"], row["quantity"]) == ("needed", "5 oz")


def test_a_kept_carry_over_is_not_recounted_so_its_undo_takes_off_what_it_added():
    """Review, 2026-10-10: last week's two curries' "4 lbs" kept onto this
    week's "2 lbs" (-> 6 lbs; the carried row is removed/'carried_kept' at
    4 lbs). Clearing last week must not recount that row, or the undo takes
    off too little and leaves this week's one curry at 4 lbs."""
    from unittest import mock
    from app.tools import grocery as _grocery

    today = household_today()
    this_monday = today - timedelta(days=today.weekday())
    next_monday = this_monday + timedelta(days=7)
    tools.add_recipe("Curry", ingredients=[{"item": "Chicken thighs", "qty": "2 lb"}])
    week_a = tools.create_weekly_plan(this_monday.isoformat())["weekly_plan_id"]
    for d in (0, 1):
        tools.plan_meal((this_monday + timedelta(days=d)).isoformat(), "Curry", weekly_plan_id=week_a)
    tools.approve_weekly_plan(week_a, approved_by="Emily")
    week_b = tools.create_weekly_plan(next_monday.isoformat())["weekly_plan_id"]
    tools.plan_meal((next_monday + timedelta(days=2)).isoformat(), "Curry", weekly_plan_id=week_b)
    with mock.patch.object(_grocery, "_household_today", lambda conn=None: next_monday):
        tools.approve_weekly_plan(week_b, approved_by="Emily")

    def needed() -> list[str]:
        return [i["quantity"] for i in tools.list_grocery_list() if i["item"] == "Chicken thighs"]

    [carried] = [c for c in tools.list_carried_over_items() if c["item"] == "Chicken thighs"]
    assert carried["quantity"] == "4 lbs" and needed() == ["2 lbs"]
    tools.keep_carried_over_item(carried["item_id"])
    assert needed() == ["6 lbs"]
    tools.clear_weekly_plan(week_a)
    tools.undo_carried_over_decision(carried["item_id"])
    assert needed() == ["2 lbs"]
