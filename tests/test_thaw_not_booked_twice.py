"""
A fridge move the household booked is not booked a second time by the
inventory sync (2026-10-21, overnight defect hunt).

Reproduced over HTTP on a throwaway database: the week approved, "chicken
thighs" ticked on the freezer step (a move booked by hand), the thighs also
on the freezer shelf in inventory — then Wednesday's night off. The night
off resyncs the week's thaws (defrost.resync_plan_thaws ->
sync_defrost_tasks), which keyed only its own inventory-backed rows, found
none for Friday's Chicken Skewers, and inserted one: /api/today/moves and
the Wednesday prep session both drew "Move the chicken thighs to the
fridge" twice, the household's ticked and a fresh one unticked.
"""
from app import tools
from app.db import get_conn
from app.tools import defrost
from conftest import household_date, household_today


def _week(*days):
    """Chicken Skewers on each day offset given; returns (plan_id, entry ids)."""
    tools.add_member("Alex")
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "chicken thighs", "qty": "2 lb"}], default_servings=1)
    today = household_today().isoformat()
    plan_id = tools.create_weekly_plan(today, content_start_date=today, day_count=7)["weekly_plan_id"]
    ids = [tools.plan_meal(household_date(d), "Chicken Skewers", weekly_plan_id=plan_id)["entry_id"] for d in days]
    return plan_id, ids


def _moves(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, meal_plan_entry_id, status, inventory_item_id FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND task_type = 'defrost' ORDER BY id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _on_the_shelf(name="chicken thighs"):
    tools.update_inventory(name, "add", quantity="2 lb", location="freezer")


def test_a_move_the_household_booked_is_not_booked_again():
    """CATCH. Red before: two rows for the night, one by each door."""
    plan_id, [friday] = _week(3)
    defrost.confirm_frozen_items(plan_id, ["chicken thighs"])
    _on_the_shelf()

    defrost.sync_defrost_tasks(plan_id)

    moves = _moves(plan_id)
    assert [(m["meal_plan_entry_id"], m["inventory_item_id"]) for m in moves] == [(friday, None)]


def test_a_ticked_move_stays_the_only_one_and_stays_ticked():
    """CATCH. The reported shape: ticked, then a resync — no unticked twin."""
    plan_id, [friday] = _week(3)
    [booked] = defrost.confirm_frozen_items(plan_id, ["chicken thighs"])["created"]
    tools.check_off_prep_step(booked["prep_task_id"], "done")
    _on_the_shelf("chicken thigh")  # the inventory's own spelling, singular

    defrost.sync_defrost_tasks(plan_id)
    defrost.sync_defrost_tasks(plan_id)

    assert [(m["id"], m["status"]) for m in _moves(plan_id)] == [(booked["prep_task_id"], "done")]


def test_the_inventory_still_books_a_night_nobody_booked_by_hand():
    """GUARD, and red before for the first night's twin. A hand-booked move
    covers ITS night only: the other chicken night, with no move of its
    own, still gets the inventory's."""
    plan_id, [first, second] = _week(3, 5)
    defrost.confirm_frozen_items(plan_id, ["chicken thighs"])
    conn = get_conn()
    conn.execute("DELETE FROM prep_tasks WHERE meal_plan_entry_id = ?", (second,))
    conn.commit()
    conn.close()
    _on_the_shelf()

    defrost.sync_defrost_tasks(plan_id)

    by_night = {m["meal_plan_entry_id"]: m["inventory_item_id"] for m in _moves(plan_id)}
    assert by_night[first] is None
    assert by_night[second] is not None
    assert len(_moves(plan_id)) == 2


def test_without_a_hand_booked_move_the_sync_books_as_before():
    """GUARD. The inventory door on its own is unchanged."""
    plan_id, [friday] = _week(3)
    _on_the_shelf()

    defrost.sync_defrost_tasks(plan_id)

    [move] = _moves(plan_id)
    assert move["meal_plan_entry_id"] == friday and move["inventory_item_id"] is not None
