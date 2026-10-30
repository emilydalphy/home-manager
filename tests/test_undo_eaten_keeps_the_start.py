"""
Undo on the "marked eaten" toast puts the night back as it was before the
tap — and before the tap a cook that had been started was cooking.

Found driving cook mode end to end (defect hunt, 2026-10-28): Start cooking
at 11:10, Mark eaten, Undo. The meal went back to not cooked and its
ingredients came back, but cook_started_at was gone: check_off_meal's
un-mark clears it (that is how "Mark not cooked" forgets a start tapped by
mistake), and undo_eaten_meal is built on that un-mark. Today's cook move
went back to "17:55" and the evening nudge would have said "Tap to start".
"""
from __future__ import annotations

from conftest import household_date

from app import tools
from app.db import get_conn


def _tonight():
    tools.add_member("Emily")
    tools.add_recipe("Lemon chicken", ingredients=[{"item": "Chicken thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=1)
    tools.update_inventory("Chicken thighs", "add", quantity="3 lbs", category="meat/seafood", location="fridge")
    return tools.plan_meal(household_date(0), "Lemon chicken", slot="dinner")["entry_id"]


def _row(entry_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT cooked_status, cook_started_at, inventory_depleted_at FROM meal_plan_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def _chicken():
    conn = get_conn()
    q = conn.execute("SELECT quantity FROM inventory_items WHERE item = 'Chicken thighs'").fetchone()
    conn.close()
    return q["quantity"]


def test_undo_of_a_tick_mid_cook_keeps_the_real_start(signed_in):
    entry = _tonight()
    started = tools.start_cooking(entry)["started_at"]
    assert started
    ticked = signed_in.post("/api/cooker/check-meal", json={"entry_id": entry, "status": "done"})
    assert ticked.status_code == 200 and ticked.json().get("kitchen_took")
    assert _chicken() == "2 lbs"

    res = signed_in.post("/api/cooker/undo-eaten", json={"entry_id": entry})

    assert res.status_code == 200
    row = _row(entry)
    assert row["cooked_status"] == "pending"
    assert row["cook_started_at"] == started, "still cooking — the Undo is of the tick, not of the start"
    assert row["inventory_depleted_at"] is None
    assert _chicken() == "3 lbs"
    card = next(m for m in res.json()["meals"] if m.get("entry_id") == entry)
    assert card["cooked_status"] == "pending"
    # And a second tap of Start answers with the first time, not a new one.
    again = tools.start_cooking(entry)
    assert again["already_started"] is True and again["started_at"] == started


def test_undo_of_a_tick_never_started_writes_no_start():
    entry = _tonight()
    tools.check_off_meal(entry, "done")

    tools.undo_eaten_meal(entry)

    assert _row(entry)["cook_started_at"] is None
    assert _chicken() == "3 lbs"


def test_mark_not_cooked_still_forgets_the_start():
    """The plain un-tick is unchanged: it is the way to clear a start."""
    entry = _tonight()
    tools.start_cooking(entry)
    tools.check_off_meal(entry, "done")

    tools.check_off_meal(entry, "pending")

    assert _row(entry)["cook_started_at"] is None


def test_another_nights_start_is_left_alone():
    entry = _tonight()
    tools.start_cooking(entry)
    lunch = tools.plan_meal(household_date(0), "Lemon chicken", slot="lunch")["entry_id"]
    tools.start_cooking(lunch)
    lunch_started = _row(lunch)["cook_started_at"]
    tools.check_off_meal(lunch, "done")
    tools.check_off_meal(entry, "done")

    tools.undo_eaten_meal(entry)

    assert _row(lunch)["cooked_status"] == "done"
    assert _row(lunch)["cook_started_at"] == lunch_started


def _set_start(entry_id, value):
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cook_started_at = ? WHERE id = ?", (value, entry_id))
    conn.commit()
    conn.close()


def test_a_component_batch_keeps_each_rows_own_start():
    tools.add_recipe("Jello Bowl", ingredients=[{"item": "Jello", "qty": "1 box"}], default_servings=2)
    tools.update_inventory("Jello", "add", quantity="3 box", category="pantry", location="pantry")
    tools.set_planning_mode("component_based")
    day = household_date(0)
    plan_id = tools.create_weekly_plan(day)["weekly_plan_id"]
    a, b, c = (
        tools.plan_meal(day, "Jello Bowl", weekly_plan_id=plan_id, component_category="treat")["entry_id"]
        for _ in range(3)
    )
    _set_start(a, f"{day}T17:02:00")
    _set_start(b, f"{day}T17:10:00")
    tools.check_off_meal(b, "done")
    assert {_row(e)["cooked_status"] for e in (a, b, c)} == {"done"}

    tools.undo_eaten_meal(b)

    assert {_row(e)["cooked_status"] for e in (a, b, c)} == {"pending"}
    assert _row(a)["cook_started_at"] == f"{day}T17:02:00"
    assert _row(b)["cook_started_at"] == f"{day}T17:10:00"
    assert _row(c)["cook_started_at"] is None


def test_the_start_is_never_cleared_on_the_way(monkeypatch):
    """No window where the night reads not begun: the un-mark keeps the start
    in its own write, rather than a later write putting it back. Read at the
    moment the un-mark has committed — where the evening nudge, Today or a
    second phone could look."""
    from app.tools import cooker

    entry = _tonight()
    started = tools.start_cooking(entry)["started_at"]
    tools.check_off_meal(entry, "done")
    seen = {}
    real_check_off = cooker.check_off_meal

    def probe(*args, **kwargs):
        out = real_check_off(*args, **kwargs)
        seen.update(_row(entry))
        return out

    monkeypatch.setattr(cooker, "check_off_meal", probe)
    cooker.undo_eaten_meal(entry)

    assert seen["cooked_status"] == "pending"
    assert seen["cook_started_at"] == started
