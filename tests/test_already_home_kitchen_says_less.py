"""
Defect hunt 2026-10-28: Shop's "Already home" kept a line set aside after the
kitchen said the food was gone.

The pass (already_home.leave_off_what_is_home) only ran at the end of an
ingest — approval, a swap, a rebuild — so "Threw it out" on the Kitchen tab,
the check's "gone", or chat's "we're out of beef" left Shop saying the beef
was already home, with nothing on the list to buy. Now every one of those
doors runs already_home.recheck_set_aside: lines no longer covered go back on
the list; the action's Undo sets them aside again (re-judged).
"""
from __future__ import annotations

import datetime

import pytest

from conftest import household_today
from app import db, tools
from app.tools import already_home

# Every test here plans "tomorrow" (and one "the day after") inside the plan
# for THIS Monday's week. On a Sunday tomorrow is next week's Monday and every
# test raises "isn't in weekly plan 1's period"; on a Saturday the day-after
# one does (found failing on main, Saturday 2026-10-31). A Wednesday leaves
# room on both sides, and a weekday name never ages the way a fixed date does.
pytestmark = pytest.mark.today("wednesday")


def _recipe(name, ingredients):
    tools.add_recipe(name, ingredients=ingredients, default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=20, instructions=["Cook."])


def _tacos_tomorrow(beef_home="2 lbs"):
    _recipe("Beef Tacos", [{"item": "Ground beef", "qty": "1 lb", "category": "meat"}])
    tools.update_inventory("Ground beef", "add", quantity=beef_home, location="fridge")
    for name in ("Emily", "Vineeth"):
        if not any(m["name"] == name for m in tools.list_members()):
            tools.add_member(name)
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal((today + datetime.timedelta(days=1)).isoformat(), "Beef Tacos",
                    slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert [r["item"] for r in tools.already_home_lines()] == ["Ground beef"]
    return plan_id


def _beef_line():
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, status, removed_by, quantity FROM grocery_items WHERE item = 'Ground beef'").fetchall()]
    conn.close()
    assert len(rows) == 1, rows
    return rows[0]


def _beef_row_id():
    conn = db.get_conn()
    rid = conn.execute("SELECT id FROM inventory_items WHERE item = 'Ground beef'").fetchone()["id"]
    conn.close()
    return rid


def test_threw_it_out_puts_the_line_back_and_undo_sets_it_aside_again(signed_in):
    _tacos_tomorrow()
    res = signed_in.post(f"/api/whats-home/{_beef_row_id()}/thrown-out").json()
    line = _beef_line()
    assert (line["status"], line["quantity"]) == ("needed", "1 lb")
    summary = signed_in.get("/api/grocery-list/already-have-summary").json()
    assert not [r for r in summary["already_have"] if r["removed_by"] == "kitchen"]

    assert signed_in.post("/api/whats-home/undo", json={"undo": res["undo"]}).json()["undone"]
    line = _beef_line()
    assert (line["status"], line["removed_by"]) == ("removed", already_home.REMOVED_BY_KITCHEN)
    assert [r["item"] for r in tools.already_home_lines()] == ["Ground beef"]


def test_the_checks_gone_puts_the_line_back(signed_in):
    _tacos_tomorrow()
    res = signed_in.post("/api/inventory/check", json={"gone": [_beef_row_id()], "confirmed": []}).json()
    assert _beef_line()["status"] == "needed"
    signed_in.post("/api/whats-home/undo", json={"undo": res["removed"][0]["undo"]})
    assert _beef_line()["status"] == "removed"


def test_chat_using_most_of_it_puts_the_line_back_only_when_short():
    _tacos_tomorrow(beef_home="2 lbs")
    tools.update_inventory("Ground beef", "use", quantity="0.5 lb")  # 1.5 lb left: still covers 1 lb
    assert _beef_line()["status"] == "removed"
    tools.update_inventory("Ground beef", "use", quantity="1 lb")  # half a pound left
    assert _beef_line()["status"] == "needed"


def test_chat_saying_its_gone_puts_the_line_back():
    _tacos_tomorrow()
    tools.update_inventory("Ground beef", "remove")
    assert _beef_line()["status"] == "needed"


def test_undo_does_not_set_aside_a_line_still_not_covered(signed_in):
    """Two kitchen rows gone at once in the check, one undone: the other
    food is still gone, so its line stays on the list."""
    _recipe("Salmon Bake", [{"item": "Salmon", "qty": "1 lb", "category": "seafood"}])
    plan_id = _tacos_tomorrow()
    tools.update_inventory("Salmon", "add", quantity="1 lb", location="fridge")
    today = household_today()
    tools.swap_meal_in_plan(plan_id, (today + datetime.timedelta(days=2)).isoformat(), "Salmon Bake", slot="dinner")
    assert sorted(r["item"] for r in tools.already_home_lines()) == ["Ground beef", "Salmon"]
    conn = db.get_conn()
    salmon_id = conn.execute("SELECT id FROM inventory_items WHERE item = 'Salmon'").fetchone()["id"]
    conn.close()
    res = signed_in.post("/api/inventory/check", json={"gone": [_beef_row_id(), salmon_id]}).json()
    assert tools.already_home_lines() == []
    beef_undo = [r for r in res["removed"] if r["item"] == "Ground beef"][0]["undo"]
    signed_in.post("/api/whats-home/undo", json={"undo": beef_undo})
    assert [r["item"] for r in tools.already_home_lines()] == ["Ground beef"]


def test_a_put_back_by_hand_is_not_undone_by_a_kitchen_undo(signed_in):
    _tacos_tomorrow()
    res = signed_in.post(f"/api/whats-home/{_beef_row_id()}/used-up").json()
    line = _beef_line()
    # The household then answers the line itself: "Still need to buy".
    conn = db.get_conn()
    conn.execute("UPDATE grocery_items SET already_have_reviewed = 1 WHERE id = ?", (line["id"],))
    conn.commit()
    conn.close()
    signed_in.post("/api/whats-home/undo", json={"undo": res["undo"]})
    assert _beef_line()["status"] == "needed"


# ---- bought onto a row already there counts as bought ----------------------
# A receipt / Say it / Type it add, or chat's "picked up beef", merges into a
# row that is already on the shelf; that row's created_at is the OLD food's,
# so Shop's Already home read the fresh beef as stale and put it on the list.

def _old_beef_row():
    tools.update_inventory("Ground beef", "add", quantity="0.5 lb", location="fridge", category="meat")
    then = (datetime.datetime.utcnow() - datetime.timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    conn.execute("UPDATE inventory_items SET created_at = ?, updated_at = ?, confirmed_on = NULL, "
                 "counted_on = NULL WHERE item = 'Ground beef'", (then, then))
    conn.commit()
    conn.close()


def _approve_tacos():
    _recipe("Beef Tacos", [{"item": "Ground beef", "qty": "1 lb", "category": "meat"}])
    for name in ("Emily", "Vineeth"):
        if not any(m["name"] == name for m in tools.list_members()):
            tools.add_member(name)
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal((today + datetime.timedelta(days=1)).isoformat(), "Beef Tacos",
                    slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")


def test_a_receipt_added_onto_an_old_row_is_already_home(signed_in):
    _old_beef_row()
    res = signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [
        {"item": "Ground beef", "quantity": "2 lb", "category": "meat"}]})
    assert res.status_code == 200, res.text
    _approve_tacos()
    assert _beef_line()["status"] == "removed"


def test_the_receipts_undo_takes_the_stamp_back(signed_in):
    _old_beef_row()
    res = signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [
        {"item": "Ground beef", "quantity": "2 lb", "category": "meat"}]}).json()
    signed_in.post("/api/whats-home/add/undo", json={"undo": res["undo"]})
    conn = db.get_conn()
    assert conn.execute("SELECT confirmed_on FROM inventory_items WHERE item = 'Ground beef'").fetchone()[0] is None
    conn.close()


def test_chat_picking_some_up_onto_an_old_row_is_already_home():
    _old_beef_row()
    tools.update_inventory("Ground beef", "add", quantity="2 lb")
    _approve_tacos()
    assert _beef_line()["status"] == "removed"


def test_an_old_row_nobody_bought_into_is_still_bought():
    _old_beef_row()
    conn = db.get_conn()
    conn.execute("UPDATE inventory_items SET quantity = '3 lb' WHERE item = 'Ground beef'")
    conn.commit()
    conn.close()
    _approve_tacos()
    assert _beef_line()["status"] == "needed"


def test_undoing_a_receipt_add_puts_the_line_back(signed_in):
    """The add's Undo is the kitchen having less too: the old stale half
    pound can't cover the tacos, so Shop's Already home line comes back."""
    _old_beef_row()
    res = signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [
        {"item": "Ground beef", "quantity": "2 lb", "category": "meat"}]}).json()
    _approve_tacos()
    assert _beef_line()["status"] == "removed"
    signed_in.post("/api/whats-home/add/undo", json={"undo": res["undo"]})
    assert _beef_line()["status"] == "needed"
    assert tools.already_home_lines() == []
