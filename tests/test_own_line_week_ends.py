"""
A household's own grocery line sheds an ended week's share (defect hunt,
2026-10-22).

A plan's amount joins a line a person typed ("Onion 2" + a week of curries),
and a person's add onto a plan line makes that line theirs. When the week
ended only PLAN lines were set aside as "Still on the list from last week",
so the ended week's onions stayed on the household's own line, never asked
about, and the next week's landed on top: 2 -> 6 -> 10, a week at a time,
for as long as the onions went unbought.

Now the ended week's part comes off the household's line and is asked about
like any other leftover (grocery._set_aside_ended_shares) — at approval, and
on the Monday read when next week was approved early.
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


def _onions() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, status, source_weekly_plan_id IS NULL AS own FROM grocery_items "
        "WHERE item = 'Onion' AND status IN ('needed', 'carried') ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["status"], bool(r["own"])) for r in rows]


def _week(week: int, nights: int = 2) -> int:
    plan = tools.create_weekly_plan(_day(week))["weekly_plan_id"]
    for n in range(nights):
        tools.plan_meal(_day(week, n + 1), "Curry", slot="dinner", weekly_plan_id=plan)
    return plan


def _on(week: int):
    """The household's clock on that week's Monday, for the set-aside."""
    monday = datetime.date.fromisoformat(_day(week))
    return mock.patch.object(_grocery, "_household_today", lambda conn=None: monday)


def _seed():
    tools.add_recipe("Curry", ingredients=[{"item": "Onion", "qty": "2", "category": "produce"}])


def test_approving_the_next_week_asks_about_the_ended_weeks_share():
    _seed()
    tools.add_grocery_item("Onion", "2", category="produce")
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    assert _onions() == [("6", "needed", True)]
    for week in (1, 2):
        plan = _week(week)
        with _on(week):
            tools.approve_weekly_plan(plan, approved_by="Emily")
        # The household's 2 plus this week's 4 — never last week's on top.
        needed = [o for o in _onions() if o[1] == "needed"]
        assert needed == [("6", "needed", True)], _onions()
    carried = tools.list_carried_over_items()
    assert [(c["item"], c["quantity"]) for c in carried] == [("Onion", "4"), ("Onion", "4")]


def test_a_person_adding_onto_the_plans_line_is_set_aside_the_same_way():
    _seed()
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    tools.add_grocery_item("onions", "2")  # joins the plan's line, which becomes theirs
    assert _onions() == [("6", "needed", True)]
    plan = _week(1)
    with _on(1):
        tools.approve_weekly_plan(plan, approved_by="Emily")
    assert sorted(_onions()) == [("4", "carried", False), ("6", "needed", True)]


def test_next_week_approved_early_is_asked_about_on_monday_and_keep_adds_it_back():
    _seed()
    tools.add_grocery_item("Onion", "2", category="produce")
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    # Next week approved while this one is still running: both weeks' onions
    # are on the household's line, and that is right until Monday.
    tools.approve_weekly_plan(_week(1), approved_by="Emily")
    assert _onions() == [("10", "needed", True)]
    with _on(1):
        set_aside = tools.set_aside_ended_weeks()
        assert [(s["item"], s["quantity"]) for s in set_aside] == [("Onion", "4")]
        assert tools.set_aside_ended_weeks() == []  # asked once
    assert sorted(_onions()) == [("4", "carried", False), ("6", "needed", True)]
    carried = tools.list_carried_over_items()[0]
    tools.keep_carried_over_item(carried["item_id"])
    assert _onions() == [("10", "needed", True)]
    tools.undo_carried_over_decision(carried["item_id"])
    assert sorted(_onions()) == [("4", "carried", False), ("6", "needed", True)]


def test_a_line_with_only_a_live_weeks_share_is_left_alone():
    _seed()
    tools.add_grocery_item("Onion", "2", category="produce")
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    with _on(0):
        assert tools.set_aside_ended_weeks() == []
    assert _onions() == [("6", "needed", True)]


def test_a_line_typed_down_to_the_plans_share_is_asked_about_not_left_blank():
    _seed()
    line = tools.add_grocery_item("Onion", "2", category="produce")["item_id"]
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    # Typed down to what the curries want: none of it is the household's own.
    tools.update_grocery_item(line, quantity="4")
    plan = _week(1)
    with _on(1):
        tools.approve_weekly_plan(plan, approved_by="Emily")
    # The line itself is last week's leftover — not a blank "Onion" beside it.
    assert sorted(_onions()) == [("4", "carried", False), ("4", "needed", False)]
