"""
A thawed ingredient whose dinner moves to a later night gets a warning
(Loop Board card 4031f4c052318117b7b2d5972b994cfe; Emily 2026-10-26 "go
with your pick": warn, a plain line at the move).

Chicken ticked out of the freezer for Tuesday, then Tuesday's dinner moved
to Saturday: before, the move said nothing, and the chicken sat thawed in
the fridge four days past its 1-2. Now the move's own line says when it
needs cooking by. Only for a move already ticked done (pending moves just
move with their dinner), only when the dinner moves LATER, and only when
the new night is past how long the food keeps thawed.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import defrost as df, meal_move

from conftest import household_today


def _d(offset: int) -> str:
    return (household_today() + datetime.timedelta(days=offset)).isoformat()


def _recipe(name, item, qty="2 lbs", category="meat"):
    tools.add_recipe(name, ingredients=[{"item": item, "qty": qty, "category": category}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2)


@pytest.fixture
def kitchen():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.set_dinner_window("6_8")
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])
    _recipe("Chicken Skewers", "Chicken thighs")
    _recipe("Bean Chili", "Black beans", qty="1 can", category="pantry")
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat", location="freezer")


def _plan(*meals):
    plan_id = tools.create_weekly_plan(_d(0))["weekly_plan_id"]
    for day, meal in meals:
        tools.plan_meal(day, meal, slot="dinner", weekly_plan_id=plan_id)
    tools.sync_defrost_tasks(plan_id)
    return plan_id


def _rows(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task_date, status, detail_json FROM prep_tasks WHERE weekly_plan_id = ? "
        "AND task_type = 'defrost' ORDER BY id", (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _entry_id(plan_id, day):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan_id, day),
    ).fetchone()
    conn.close()
    return row["id"]


def test_the_sentence():
    desc = "Move the Chicken thighs to the fridge — for Tuesday's Chicken Skewers."
    # Thawed for Tuesday 2026-10-27 (today Sunday 10-25), now cooking
    # Saturday: past its 2 days.
    assert df.thawed_wait_sentence(desc, "2026-10-27", "2026-10-31", "2026-10-25") == \
        "The Chicken thighs were thawed for Tuesday, so they need cooking by Thursday."
    # Inside the 2 days: nothing to say.
    assert df.thawed_wait_sentence(desc, "2026-10-27", "2026-10-29", "2026-10-25") is None
    # Thawed for tonight, said as tonight.
    assert df.thawed_wait_sentence(desc, "2026-10-25", "2026-10-31", "2026-10-25") == \
        "The Chicken thighs were thawed for tonight, so they need cooking by Tuesday."
    # Red meat keeps 3, and one thing is "it".
    beef = "Move the Beef chuck to the fridge — for Tuesday's Beef Stew."
    assert df.thawed_wait_sentence(beef, "2026-10-27", "2026-10-30", "2026-10-25") is None
    assert df.thawed_wait_sentence(beef, "2026-10-27", "2026-10-31", "2026-10-25") == \
        "The Beef chuck was thawed for Tuesday, so it needs cooking by Friday."
    # Ground beef is the short keep.
    assert df.thawed_keeps_days("Ground beef") == 2
    assert df.thawed_wait_sentence("not one of ours", "2026-10-25", "2026-10-31", "2026-10-25") is None


def test_moving_a_thawed_chicken_dinner_later_warns_at_the_move_sheet(kitchen):
    plan_id = _plan((_d(2), "Chicken Skewers"), (_d(6), "Bean Chili"))
    [row] = _rows(plan_id)
    tools.check_off_prep_step(row["id"], "done")

    out = meal_move.move_meal(plan_id, _entry_id(plan_id, _d(2)), _d(6))

    assert out["status"] == "moved", out
    warns = [n for n in out["thaw_notes"] if "were thawed for" in n]
    assert len(warns) == 1 and "Chicken thighs were thawed for" in warns[0] and "need cooking by" in warns[0]
    # The night it was thawed for is kept on the ticked row, so a second
    # move still counts from it.
    assert '"thawed_for"' in _rows(plan_id)[0]["detail_json"]


def test_the_nights_swap_warns_too(kitchen):
    plan_id = _plan((_d(2), "Chicken Skewers"), (_d(6), "Bean Chili"))
    [row] = _rows(plan_id)
    tools.check_off_prep_step(row["id"], "done")

    out = tools.swap_dinner_nights(plan_id, _d(2), _d(6))

    assert out["status"] == "swapped"
    assert any("were thawed for" in s for s in out.get("thaw_now", [])), out


def test_a_move_inside_the_keep_says_nothing(kitchen):
    plan_id = _plan((_d(2), "Chicken Skewers"), (_d(4), "Bean Chili"))
    [row] = _rows(plan_id)
    tools.check_off_prep_step(row["id"], "done")

    out = tools.swap_dinner_nights(plan_id, _d(2), _d(4))

    assert not any("were thawed for" in s for s in out.get("thaw_now", []))


def test_a_pending_move_says_nothing_about_thawed_food(kitchen):
    plan_id = _plan((_d(2), "Chicken Skewers"), (_d(6), "Bean Chili"))

    out = tools.swap_dinner_nights(plan_id, _d(2), _d(6))

    assert not any("were thawed for" in s for s in out.get("thaw_now", []))


def test_moving_it_earlier_says_nothing(kitchen):
    plan_id = _plan((_d(6), "Chicken Skewers"), (_d(5), "Bean Chili"))
    [row] = _rows(plan_id)
    tools.check_off_prep_step(row["id"], "done")

    out = tools.swap_dinner_nights(plan_id, _d(6), _d(5))

    assert not any("were thawed for" in s for s in out.get("thaw_now", []))


def test_a_second_move_counts_from_the_first_night(kitchen):
    """Moved +2 -> +4 (inside the keep, silent), then +4 -> +6: past it,
    counted from +2, not from +4."""
    plan_id = _plan((_d(2), "Chicken Skewers"), (_d(4), "Bean Chili"), (_d(6), "Bean Chili"))
    [row] = _rows(plan_id)
    tools.check_off_prep_step(row["id"], "done")
    first = tools.swap_dinner_nights(plan_id, _d(2), _d(4))
    assert not any("were thawed for" in s for s in first.get("thaw_now", []))

    second = tools.swap_dinner_nights(plan_id, _d(4), _d(6))

    assert any("were thawed for" in s for s in second.get("thaw_now", [])), second
