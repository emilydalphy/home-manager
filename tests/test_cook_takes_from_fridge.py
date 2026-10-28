"""
Cooking takes the food off the FRIDGE row, not the frozen pack being saved
(overnight defect hunt, 2026-10-28).

deplete_inventory_for_meal took the first inventory row whose name matched,
and get_inventory is ORDER BY item — so with Beef Mince 1 lb in the freezer
(the older row) and Beef Mince 1 lb in the fridge, cooking tonight's tacos
deleted the FREEZER pack. Kitchen said the frozen pack was gone and the
fridge one still there, and the next fridge-move sync dropped Saturday's
"Move the Beef Mince to the fridge".
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import defrost


def _week_start() -> str:
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


def _insert(item, qty, location, category="meat/seafood"):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, category, location) VALUES (?, ?, ?, ?, ?)",
        (tools.household_id(), item, qty, category, location),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _row(item_id):
    conn = get_conn()
    r = conn.execute("SELECT id, item, quantity, location FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def _moves(plan_id):
    return [t for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "defrost"]


@pytest.fixture
def tacos_twice():
    """Tacos on Tuesday ("tonight") and Saturday; a freezer pack (older row)
    and a fridge pack of the same mince."""
    tools.add_recipe("Beef Tacos", ingredients=[{"item": "Beef Mince", "qty": "1 lb"}],
                     default_servings=4, prep_time_minutes=10, cook_time_minutes=15)
    freezer_id = _insert("Beef Mince", "1 lb", "freezer")
    fridge_id = _insert("Beef Mince", "1 lb", "fridge")
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    days = tools._week_dates(week)
    tonight = tools.plan_meal(days[1], "Beef Tacos", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])["entry_id"]
    saturday = tools.plan_meal(days[5], "Beef Tacos", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])["entry_id"]
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    return {"plan": plan["weekly_plan_id"], "tonight": tonight, "saturday": saturday,
            "freezer": freezer_id, "fridge": fridge_id}


def test_cooking_tonight_takes_the_fridge_pack_and_leaves_the_frozen_one(tacos_twice):
    tools.check_off_meal(tacos_twice["tonight"], "done")
    assert _row(tacos_twice["fridge"]) is None, "the fridge pack is what got cooked"
    assert _row(tacos_twice["freezer"]) == {
        "id": tacos_twice["freezer"], "item": "Beef Mince", "quantity": "1 lb", "location": "freezer"}


def test_saturdays_fridge_move_survives_tonights_cook(tacos_twice):
    sat = [m for m in _moves(tacos_twice["plan"]) if m["meal_plan_entry_id"] == tacos_twice["saturday"]]
    assert len(sat) == 1 and sat[0]["inventory_item_id"] == tacos_twice["freezer"]

    tools.check_off_meal(tacos_twice["tonight"], "done")
    defrost.sync_defrost_tasks(tacos_twice["plan"])

    sat = [m for m in _moves(tacos_twice["plan"]) if m["meal_plan_entry_id"] == tacos_twice["saturday"]]
    assert [m["id"] for m in sat] and sat[0]["inventory_item_id"] == tacos_twice["freezer"]


def test_a_move_ticked_done_for_tonight_means_the_thawed_pack_was_cooked(tacos_twice):
    [mine] = [m for m in _moves(tacos_twice["plan"]) if m["meal_plan_entry_id"] == tacos_twice["tonight"]]
    tools.check_off_prep_step(mine["id"], "done")

    tools.check_off_meal(tacos_twice["tonight"], "done")
    assert _row(tacos_twice["freezer"]) is None, "it was thawed for this dinner"
    assert _row(tacos_twice["fridge"]) is not None


def test_only_a_freezer_row_still_comes_off_the_freezer():
    tools.add_recipe("Beef Tacos", ingredients=[{"item": "Beef Mince", "qty": "1 lb"}], default_servings=4)
    freezer_id = _insert("Beef Mince", "2 lb", "freezer")
    from conftest import household_date
    entry = tools.plan_meal(household_date(0), "Beef Tacos", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")
    assert _row(freezer_id)["quantity"] == "1 lb"


def test_undo_puts_back_the_fridge_pack_it_took(tacos_twice):
    result = tools.check_off_meal(tacos_twice["tonight"], "done")
    assert "beef" in (result.get("kitchen_took") or {}).get("line", "").lower()
    tools.undo_eaten_meal(tacos_twice["tonight"])
    assert _row(tacos_twice["fridge"]) == {
        "id": tacos_twice["fridge"], "item": "Beef Mince", "quantity": "1 lb", "location": "fridge"}
    assert _row(tacos_twice["freezer"])["quantity"] == "1 lb"
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM inventory_items WHERE item = 'Beef Mince'").fetchone()[0]
    conn.close()
    assert n == 2
