"""
A cook somebody has started stays a cook in progress only while it really
is one (defect hunt, 2026-10-22).

Two doors still got it wrong after the 2026-10-14 started-dinner work:

- "We're going out tonight" (take_the_night_off in chat, or a second phone
  still showing the tonight card from before the start) on a started
  dinner that feeds no other night fell through to the drop: the dish came
  off the week and its unbought groceries were reversed, with the pot on
  the stove. Only a chain source was refused.
- "We skipped it" the next morning kept the start stamp. On a double batch
  the dish moves onto the night it fed and the stamp went with it, so that
  night read "Started 6:02" — yesterday's clock — and was "Already cooking"
  to every door that asks cook_begun.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import leftovers, moves
from app.tools import tonight as _tonight
from app.tools import weekly_plan as _weekly_plan


def _plan():
    """An approved three-day plan, yesterday through tomorrow (household
    clock), with a real dinner on every night — so there is no free night
    for a called-off dinner to move to."""
    today = household_today()
    yday = today - datetime.timedelta(days=1)
    plan = tools.create_weekly_plan(yday.isoformat(), day_count=3)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    for name in ("Tacos", "Lentil Soup", "Roast Veg"):
        tools.add_recipe(name, ingredients=[{"item": f"Greens for {name}", "qty": "1 bag"}],
                         prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    return plan, today, yday


def _start(entry_id, stamp="2026-01-01T18:02:00"):
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cook_started_at = ? WHERE id = ?", (stamp, entry_id))
    conn.commit()
    conn.close()


def _row(entry_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, date, slot, slot_state, cooked_status, cook_started_at, skipped_at "
        "FROM meal_plan_entries WHERE id = ?", (entry_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _afternoon(today):
    return datetime.datetime.combine(today, datetime.time(15, 50))


def test_night_off_on_a_started_dinner_that_feeds_nobody_is_refused():
    plan, today, yday = _plan()
    tools.plan_meal(yday.isoformat(), "Lentil Soup", slot="dinner", weekly_plan_id=plan)
    tonight = tools.plan_meal(today.isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal((today + datetime.timedelta(days=1)).isoformat(), "Roast Veg",
                    slot="dinner", weekly_plan_id=plan)
    _start(tonight)

    out = _tonight.tonight_night_off(now=_afternoon(today))

    assert out["status"] == "refused"
    assert out["message"] == "The Tacos is already cooking, so it stays on tonight."
    row = _row(tonight)
    assert row is not None and row["slot_state"] == "planned"
    assert row["cook_started_at"] == "2026-01-01T18:02:00"


def test_mark_not_cooked_makes_the_night_off_work_again():
    """The way out the refusal relies on: clearing the start (Mark not
    cooked) lets the same tap drop the dish as it always did."""
    plan, today, yday = _plan()
    tools.plan_meal(yday.isoformat(), "Lentil Soup", slot="dinner", weekly_plan_id=plan)
    tonight = tools.plan_meal(today.isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal((today + datetime.timedelta(days=1)).isoformat(), "Roast Veg",
                    slot="dinner", weekly_plan_id=plan)
    _start(tonight)
    tools.check_off_meal(tonight, "pending")

    out = _tonight.tonight_night_off(now=_afternoon(today))

    assert out["status"] == "night_off" and out["kind"] == "drop"


def _chain(plan, yday, today):
    cook = tools.plan_meal(yday.isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan,
                           derived_from={"make_double_for": [f"{today.isoformat()}:dinner"]})["entry_id"]
    fed = tools.plan_meal(today.isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan,
                          derived_from={"links_to": f"{yday.isoformat()}:dinner"})["entry_id"]
    assert set(leftovers.plan_leftover_chains(plan)["leftovers"]) == {fed}, "premise: a real chain"
    return cook


def test_skipping_a_started_double_cook_does_not_carry_the_start_to_the_fed_night():
    plan, today, yday = _plan()
    cook = _chain(plan, yday, today)
    _start(cook)

    out = tools.answer_yesterday(cook, "skipped", today)

    assert out["moved_to"]["date"] == today.isoformat()
    row = _row(cook)
    assert row["date"] == today.isoformat()
    assert row["cook_started_at"] is None
    assert _weekly_plan.cook_begun(row) is None
    payload = moves.today_moves(day=today.isoformat(),
                                now=datetime.datetime.combine(today, datetime.time(8, 0)))
    [dinner] = [m for m in payload["moves"] if m["slot"] == "dinner"]
    assert not [c for c in dinner["chips"] if c.startswith("Started")]

    # Undo puts the week back exactly as it was, start included.
    assert tools.undo_skipped_move(out["undo_entry_id"])["status"] == "restored"
    assert _row(cook)["cook_started_at"] == "2026-01-01T18:02:00"


def test_skipping_a_started_ordinary_cook_forgets_the_start():
    plan, today, yday = _plan()
    cook = tools.plan_meal(yday.isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    _start(cook)

    tools.answer_yesterday(cook, "skipped", today)

    row = _row(cook)
    assert row["skipped_at"] is not None
    assert row["cook_started_at"] is None
