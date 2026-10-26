"""
A due staple that JOINED a week's line (sync_due_staples, plan first) does
not climb when weeks end unbought (review of overnight/grocery-staple-beside-
week, 2026-10-24).

The other order is tests/test_staple_line_week_ends.py. Here the week's
"Onion 4" is on the list first and the running-low staple's 2 joins it, so
the line becomes the staple's (a standing want with the meals' share on it).
Before the ended-week fix from overnight/staple-share-asked-not-added was
merged in, nothing took an ended week's share off a staple's line: three
weeks approved in turn with nothing bought read 6 -> 10 -> 14. Now each
ended week's 4 is set aside as "Still on the list from last week" and the
line stays the staple's 2 plus this week's 4.
"""
from __future__ import annotations

import datetime
from unittest import mock

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery
from conftest import household_today, on_the_weeks_monday

_TODAY = household_today()
MONDAY = _TODAY - datetime.timedelta(days=_TODAY.weekday())

pytestmark = on_the_weeks_monday(MONDAY)


def _day(week: int, n: int = 0) -> str:
    return (MONDAY + datetime.timedelta(days=7 * week + n)).isoformat()


def _needed_onions() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, staple_id IS NOT NULL AS staples FROM grocery_items "
        "WHERE item = 'Onion' AND status = 'needed' ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["quantity"], bool(r["staples"])) for r in rows]


def _approve(week: int) -> None:
    plan = tools.create_weekly_plan(_day(week))["weekly_plan_id"]
    tools.plan_meal(_day(week, 1), "Onion soup", slot="dinner", weekly_plan_id=plan)
    monday = datetime.date.fromisoformat(_day(week))
    with mock.patch.object(_grocery, "_household_today", lambda conn=None: monday):
        tools.approve_weekly_plan(plan, approved_by="Emily")


def test_a_staple_that_joined_the_weeks_line_does_not_climb_week_after_week():
    tools.add_recipe("Onion soup", ingredients=[{"item": "Onion", "qty": "4", "category": "produce"}])
    _approve(0)
    assert _needed_onions() == [("4", False)]
    tools.add_staple("Onion", quantity="2", category="produce", running_low=True)
    tools.sync_due_staples()  # what any read of the list does
    assert _needed_onions() == [("6", True)]

    for week in (1, 2):
        _approve(week)
        tools.sync_due_staples()
        # The staple's 2 plus this week's 4, never an ended week's on top.
        assert _needed_onions() == [("6", True)], _needed_onions()

    carried = tools.list_carried_over_items()
    assert [(c["item"], c["quantity"]) for c in carried] == [("Onion", "4"), ("Onion", "4")]
