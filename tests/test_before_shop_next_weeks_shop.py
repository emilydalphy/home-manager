"""
"Before you shop" stamps the week being SHOPPED FOR, not the week that is
ending (2026-10-27, overnight/hunt-preshop-1027).

The usual week is approved and shopped before it begins — a Sunday shop for
a Monday week. On that day the current plan is the week that is ending, and
before_shop.py stamped that one: Monday's new week offered the pass again
over a list already shopped, and a midweek top-up pass on the running week
hid the pass from the weekend's big shop.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn


def _plan(start, approved=True):
    pid = tools.create_weekly_plan(start.isoformat(), day_count=7)["weekly_plan_id"]
    if approved:
        conn = get_conn()
        try:
            conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (pid,))
            conn.commit()
        finally:
            conn.close()
    return pid


def _line(plan_id, item="Chicken thighs", status="needed"):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO grocery_items (household_id, item, quantity, category, status, added_by, "
            "source_weekly_plan_id) VALUES (1, ?, '2 lbs', 'meat', ?, 'ai', ?)",
            (item, status, plan_id),
        )
        conn.commit()
    finally:
        conn.close()


def _asked(plan_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT before_shop_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)
        ).fetchone()["before_shop_asked_at"]
    finally:
        conn.close()


def _this_week_and_next():
    today = household_today()
    ending = _plan(today - datetime.timedelta(days=6))  # today is its last day
    nxt = _plan(today + datetime.timedelta(days=1))
    return ending, nxt


def test_the_shop_for_next_week_stamps_next_week():
    """CATCH. Sunday's pass over next week's list is next week's pass, so
    Monday does not offer it again over a list already shopped."""
    ending, nxt = _this_week_and_next()
    _line(nxt)
    assert tools.before_shop_state() == {"done": False, "weekly_plan_id": nxt}
    assert tools.mark_before_shop_done() == {"done": True, "weekly_plan_id": nxt}
    assert _asked(nxt) is not None
    assert _asked(ending) is None


def test_a_midweek_pass_does_not_hide_the_pass_from_next_weeks_shop():
    """CATCH. A top-up pass stamps the running week; once next week's food
    is on the list, the big shop is asked again."""
    ending, nxt = _this_week_and_next()
    _line(ending, "Milk")
    assert tools.mark_before_shop_done()["weekly_plan_id"] == ending
    assert tools.before_shop_state() == {"done": True, "weekly_plan_id": ending}
    _line(nxt)
    assert tools.before_shop_state() == {"done": False, "weekly_plan_id": nxt}


def test_a_week_planned_ahead_with_nothing_on_the_list_is_not_this_shop():
    """EDGE. Approved ahead but nothing of it on the list (or all of it
    bought): the shop is still the running week's."""
    ending, nxt = _this_week_and_next()
    _line(nxt, status="purchased")
    assert tools.before_shop_state()["weekly_plan_id"] == ending
    assert tools.mark_before_shop_done()["weekly_plan_id"] == ending
    assert _asked(nxt) is None
