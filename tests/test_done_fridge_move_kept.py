"""
Loop Board "Eating the food erases the record that it was moved to the
fridge" (Bug, Low).

defrost.sync_defrost_tasks' sweep is keyed on inventory_item_id, so once the
freezer row is gone — which is what eating the thing does — a fridge move
the household had already ticked DONE was swept with it. The food was
handled correctly; what went was the record that somebody did the work.
A ticked move now survives; an unticked one for food that's gone is still
swept, so no stale reminder appears anywhere.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import defrost


def _week_start() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


@pytest.fixture
def week_with_skewers():
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=15,
    )
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb",
                           category="meat/seafood", location="freezer")
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    entry = tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner",
                            weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    [task] = tools.get_prep_schedule(plan["weekly_plan_id"])
    assert task["task_type"] == "defrost" and task["inventory_item_id"]
    return plan["weekly_plan_id"], entry["entry_id"], task


def _freezer_rows():
    return [i for i in tools.get_inventory() if i.get("location") == "freezer"]


def test_a_ticked_fridge_move_survives_the_food_being_eaten(week_with_skewers):
    plan_id, entry_id, task = week_with_skewers
    tools.check_off_prep_step(task["id"], "done")

    # Cooking it is what eats the freezer row (deplete_inventory_for_meal).
    tools.check_off_meal(entry_id, "done")
    assert _freezer_rows() == [], "the thighs are eaten, so there's no freezer row to key on"

    result = defrost.sync_defrost_tasks(plan_id)

    assert result["removed"] == 0
    tasks = tools.get_prep_schedule(plan_id)
    assert [(t["id"], t["status"]) for t in tasks] == [(task["id"], "done")]


def test_an_unticked_move_for_food_that_is_gone_is_still_swept(week_with_skewers):
    plan_id, _entry_id, task = week_with_skewers
    tools.update_inventory("Chicken Thighs", "remove")
    assert _freezer_rows() == []

    result = defrost.sync_defrost_tasks(plan_id)

    assert result["removed"] == 1
    assert tools.get_prep_schedule(plan_id) == []


def test_a_skipped_move_for_food_that_is_gone_is_still_swept(week_with_skewers):
    # 'skipped' is a decline — nothing was moved, so nothing to record.
    plan_id, _entry_id, task = week_with_skewers
    tools.check_off_prep_step(task["id"], "skipped")
    tools.update_inventory("Chicken Thighs", "remove")

    assert defrost.sync_defrost_tasks(plan_id)["removed"] == 1
    assert tools.get_prep_schedule(plan_id) == []


def test_the_kept_done_row_is_not_a_to_do_anywhere(week_with_skewers):
    """The spared row reads as handled: no pending count, no Today move,
    no defrost-today tile."""
    plan_id, entry_id, task = week_with_skewers
    # Date the move today so every 'today' reader would see it if it
    # counted it as owed.
    from conftest import household_date
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET task_date = ? WHERE id = ?", (household_date(0), task["id"]))
    conn.commit()
    conn.close()
    tools.check_off_prep_step(task["id"], "done")
    tools.update_inventory("Chicken Thighs", "remove")
    defrost.sync_defrost_tasks(plan_id)

    assert [t["id"] for t in tools.get_prep_schedule(plan_id)] == [task["id"]], "kept"
    assert task["id"] not in [t["id"] for t in defrost.get_defrost_today()]
    assert task["id"] not in [t["id"] for t in defrost.get_defrost_schedule()]
    assert defrost.get_defrost_schedule() == []

    # Today's strip (moves._prep_moves) shows it as handled, never owed.
    from app.tools import moves
    day = datetime.date.fromisoformat(household_date(0))
    view = {"prep_tasks": tools.get_prep_schedule(plan_id)}
    built = moves._prep_moves(view, day, datetime.datetime.combine(day, datetime.time(9, 0)),
                              datetime.time(18, 0))
    assert [m["done"] for m in built if m["kind"] == "fridge"] == [True]


def test_a_done_move_whose_item_still_exists_is_still_swept(week_with_skewers):
    """Narrow on purpose: only EATEN food (freezer row gone) keeps the
    record. A done move that lost its candidate some other way — here the
    item re-filed out of the freezer — goes as before, the same rule the
    own-portion sweep keeps, so no thaw note is left on a night it no
    longer describes."""
    plan_id, _entry_id, task = week_with_skewers
    tools.check_off_prep_step(task["id"], "done")
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET location = 'fridge' WHERE id = ?", (task["inventory_item_id"],))
    conn.commit()
    conn.close()

    assert defrost.sync_defrost_tasks(plan_id)["removed"] == 1
    assert tools.get_prep_schedule(plan_id) == []
