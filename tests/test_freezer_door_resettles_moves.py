"""
Moving food across the freezer door reaches the live plans at once (hunt
2026-11-02, branch overnight/hunt-inventory-freezer-1102b).

set_inventory_location (Freeze it, the Kitchen tab's Move to fridge, the
Inventory sheet's shelf picker) changed the row and nothing else, so:

  - Freeze it on the chicken thighs Thursday's dinner needs booked no
    "Move the Chicken Thighs to the fridge" — the chicken stayed frozen
    with no reminder until some unrelated plan change re-ran the sync;
  - Move to fridge on the frozen pack left that move pending on Today
    for food already out.

The gone doors re-read the plans since 2026-10-28
(test_kitchen_gone_resettles_plans.py); a move is the same news.
Every test is a CATCH (red before the fix) unless it says GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_today
from app import db, tools


def _pending_moves(plan_id):
    return [t["description"] for t in tools.get_prep_schedule(plan_id)
            if t["task_type"] == "defrost" and t["status"] == "pending"]


def _row_id(item):
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM inventory_items WHERE LOWER(item) = LOWER(?)", (item,)).fetchone()
    conn.close()
    return row["id"]


def _chicken_thursday(location):
    """This week, approved; Chicken Skewers three days out; the thighs on `location`."""
    today = household_today()
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=15)
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location=location)
    plan_id = tools.create_weekly_plan((today - datetime.timedelta(days=today.weekday())).isoformat())["weekly_plan_id"]
    tools.plan_meal((today + datetime.timedelta(days=3)).isoformat(), "Chicken Skewers",
                    slot="dinner", weekly_plan_id=plan_id)
    conn = db.get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    tools.sync_defrost_tasks(plan_id)
    return plan_id


def test_freeze_it_books_the_move_and_its_undo_takes_it_back(signed_in):
    plan_id = _chicken_thursday("fridge")
    assert _pending_moves(plan_id) == []

    res = signed_in.post(f"/api/inventory/{_row_id('Chicken Thighs')}/freeze")
    assert res.status_code == 200
    assert len(_pending_moves(plan_id)) == 1, "frozen chicken Thursday needs is a move owed"

    assert signed_in.post("/api/inventory/unfreeze", json={"undo": res.json()["undo"]}).json()["undone"] is True
    assert _pending_moves(plan_id) == [], "back in the fridge: nothing to move"


def test_move_to_fridge_settles_the_move_and_its_undo_books_it_again(signed_in):
    plan_id = _chicken_thursday("freezer")
    assert len(_pending_moves(plan_id)) == 1

    res = signed_in.post(f"/api/whats-home/{_row_id('Chicken Thighs')}/move", json={"location": "fridge"})
    assert res.status_code == 200, res.text
    assert _pending_moves(plan_id) == [], "no move for chicken already in the fridge"

    assert signed_in.post("/api/whats-home/undo", json={"undo": res.json()["undo"]}).json()["undone"] is True
    assert len(_pending_moves(plan_id)) == 1, "back in the freezer, and so is its move"


def test_the_shelf_picker_is_the_same_door(signed_in):
    plan_id = _chicken_thursday("fridge")
    res = signed_in.post(f"/api/inventory/{_row_id('Chicken Thighs')}/location", json={"location": "freezer"})
    assert res.status_code == 200
    assert len(_pending_moves(plan_id)) == 1


def test_fridge_to_pantry_leaves_a_ticked_move_alone(signed_in):
    """GUARD — a move that never crosses the freezer door changes no plan,
    and a move the household already ticked stays ticked."""
    plan_id = _chicken_thursday("freezer")
    task = next(t for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "defrost")
    conn = db.get_conn()
    conn.execute("UPDATE prep_tasks SET status = 'done' WHERE id = ?", (task["id"],))
    conn.commit()
    conn.close()
    item_id = _row_id("Chicken Thighs")
    conn = db.get_conn()
    conn.execute("UPDATE inventory_items SET location = 'fridge' WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    assert signed_in.post(f"/api/inventory/{item_id}/location", json={"location": "pantry"}).status_code == 200
    statuses = [t["status"] for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "defrost"]
    assert statuses == ["done"]
