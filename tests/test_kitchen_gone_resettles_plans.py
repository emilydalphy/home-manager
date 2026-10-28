"""
A kitchen row said to be gone reaches the live plans at once (hunt
2026-10-28, branch overnight/hunt-planweek-1028).

Plan the week's "Quick check: still have these?" (and the Kitchen tab's
Used it up / Threw it out) deleted the row and nothing else. The two things
a live plan had booked off that row stayed until some unrelated change to
the plan re-read the kitchen:

  - a pending fridge move ("Move the Chicken Thighs to the fridge") for
    food the household had just said was not there — on Today, the prep
    session and the evening text;
  - a Shop line set aside as "Already home" because that row covered it,
    so the food the household said was gone was not on the list to buy.

defrost.resync_live_plan_thaws re-reads the first (this branch);
already_home.recheck_set_aside the second (overnight/hunt-whatshome-1028 —
the last test here is the check's door into it, end to end).
Every test is a CATCH (red before the fix) unless it says GUARD.
"""
from __future__ import annotations

import datetime

from conftest import household_today
from app import db, tools
from app.tools import already_home, defrost


def _next_monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


def _row_id(item):
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM inventory_items WHERE LOWER(item) = LOWER(?)", (item,)).fetchone()
    conn.close()
    return row["id"]


def _age(item, days):
    then = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    conn.execute("UPDATE inventory_items SET created_at = ?, updated_at = ?, counted_on = NULL "
                 "WHERE LOWER(item) = LOWER(?)", (then, then, item))
    conn.commit()
    conn.close()


def _pending_moves(plan_id):
    return [t["description"] for t in tools.get_prep_schedule(plan_id)
            if t["task_type"] == "defrost" and t["status"] == "pending"]


def _chicken_week():
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=15)
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="freezer")
    _age("Chicken Thighs", 60)
    monday = _next_monday()
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal((monday + datetime.timedelta(days=3)).isoformat(), "Chicken Skewers",
                    slot="dinner", weekly_plan_id=plan_id)
    defrost.sync_defrost_tasks(plan_id)
    assert len(_pending_moves(plan_id)) == 1
    return plan_id


def test_gone_on_the_check_takes_its_fridge_move_and_undo_brings_it_back(signed_in):
    plan_id = _chicken_week()
    item_id = _row_id("Chicken Thighs")
    assert item_id in [c["id"] for c in tools.inventory_check_candidates()]

    res = signed_in.post("/api/inventory/check", json={"gone": [item_id], "confirmed": []})
    assert res.status_code == 200
    assert _pending_moves(plan_id) == [], "no move for chicken the household said is gone"
    assert defrost.get_defrost_schedule(14) == []

    # The freezer segment's second tap is the Kitchen tab's Undo.
    undo = res.json()["removed"][0]["undo"]
    assert signed_in.post("/api/whats-home/undo", json={"undo": undo}).json()["undone"] is True
    assert len(_pending_moves(plan_id)) == 1, "the chicken is back, and so is its move"


def test_threw_it_out_on_the_kitchen_tab_takes_the_move_too(signed_in):
    plan_id = _chicken_week()
    assert signed_in.post(f"/api/whats-home/{_row_id('Chicken Thighs')}/thrown-out").status_code == 200
    assert _pending_moves(plan_id) == []


def test_chat_saying_we_are_out_takes_the_move_too():
    plan_id = _chicken_week()
    assert tools.update_inventory("Chicken Thighs", "remove").get("removed") is True
    assert _pending_moves(plan_id) == []


def test_untapped_chips_leave_the_move_alone(signed_in):
    """GUARD — a confirmed row is still there; its move stays."""
    plan_id = _chicken_week()
    item_id = _row_id("Chicken Thighs")
    signed_in.post("/api/inventory/check", json={"gone": [], "confirmed": [item_id]})
    assert len(_pending_moves(plan_id)) == 1


def _beef_week():
    for name in ("Emily", "Vineeth"):
        if not any(m["name"] == name for m in tools.list_members()):
            tools.add_member(name)
    tools.add_recipe("Beef Tacos", ingredients=[
        {"item": "Ground beef", "qty": "1 lb", "category": "meat"},
        {"item": "Red onion", "qty": "1", "category": "produce"},
    ], default_servings=2, prep_time_minutes=10, cook_time_minutes=20, instructions=["Cook."])
    tools.update_inventory("Ground beef", "add", quantity="1 lb", location="fridge")
    tools.update_inventory("Red onion", "add", quantity="1", location="fridge")
    _age("Ground beef", 10)   # fresh enough for Already home, old enough to be asked about
    _age("Red onion", 20)     # too old for Already home: its line stays on the list
    monday = _next_monday()
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal((monday + datetime.timedelta(days=2)).isoformat(), "Beef Tacos",
                    slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _line(item):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT status, removed_by FROM grocery_items WHERE LOWER(item) = LOWER(?)", (item,)).fetchall()]
    conn.close()
    return rows


def test_gone_on_the_check_puts_its_already_home_line_back_on_the_list(signed_in):
    _beef_week()
    assert _line("Ground beef") == [{"status": "removed", "removed_by": already_home.REMOVED_BY_KITCHEN}]
    asked = [c["id"] for c in tools.inventory_check_candidates()]
    beef, onion = _row_id("Ground beef"), _row_id("Red onion")
    assert beef in asked and onion in asked

    signed_in.post("/api/inventory/check", json={"gone": [beef], "confirmed": [onion]})

    assert [r["status"] for r in _line("Ground beef")] == ["needed"], "gone means buy it"
    # GUARD: confirming the onion makes it "fresh", but answering a check
    # only ever puts lines back — it never takes a new one off the list.
    assert [r["status"] for r in _line("Red onion")] == ["needed"]
