"""
A staple's grocery line sheds an ended week's share too (overnight review,
2026-10-22).

Onions are a staple ("Onion 2", put there because they're probably running
low) and a week's curries add 4 onto that same line. The ended-week fix for a
household's own line (grocery._set_aside_ended_shares) left out any line with
a staple_id, so last week's 4 stayed on the staple's line, never asked about,
and the next week's landed on top: 6 -> 10 -> 14.

Now the ended week's part comes off the staple's line and is asked about as
"Still on the list from last week", the same as a hand-typed line, and the
staple's own amount keeps its "We have plenty" / "Not this trip".
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
        "SELECT quantity, status, staple_id IS NOT NULL AS staples FROM grocery_items "
        "WHERE item = 'Onion' AND status IN ('needed', 'carried') ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["status"], bool(r["staples"])) for r in rows]


def _staple_line() -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM grocery_items WHERE item = 'Onion' AND status = 'needed' AND staple_id IS NOT NULL"
    ).fetchone()
    conn.close()
    return row["id"]


def _week(week: int) -> int:
    plan = tools.create_weekly_plan(_day(week))["weekly_plan_id"]
    for n in range(2):
        tools.plan_meal(_day(week, n + 1), "Curry", slot="dinner", weekly_plan_id=plan)
    return plan


def _on(week: int):
    monday = datetime.date.fromisoformat(_day(week))
    return mock.patch.object(_grocery, "_household_today", lambda conn=None: monday)


def _seed():
    tools.add_recipe("Curry", ingredients=[{"item": "Onion", "qty": "2", "category": "produce"}])
    tools.add_staple("Onion", quantity="2", category="produce", running_low=True)
    tools.sync_due_staples()  # what any read of the list does
    assert _onions() == [("2", "needed", True)]


def test_a_staples_line_does_not_climb_week_after_week():
    _seed()
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    assert _onions() == [("6", "needed", True)]
    for week in (1, 2):
        plan = _week(week)
        with _on(week):
            tools.approve_weekly_plan(plan, approved_by="Emily")
        # The staple's 2 plus this week's 4 — never last week's on top.
        needed = [o for o in _onions() if o[1] == "needed"]
        assert needed == [("6", "needed", True)], _onions()
    carried = tools.list_carried_over_items()
    assert [(c["item"], c["quantity"]) for c in carried] == [("Onion", "4"), ("Onion", "4")]


def test_next_week_approved_early_is_asked_about_on_monday():
    _seed()
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    tools.approve_weekly_plan(_week(1), approved_by="Emily")
    assert _onions() == [("10", "needed", True)]
    with _on(1):
        set_aside = tools.set_aside_ended_weeks()
    assert [(s["item"], s["quantity"]) for s in set_aside] == [("Onion", "4")]
    # The left-over is the plan's, not the staple's.
    assert sorted(_onions()) == [("4", "carried", False), ("6", "needed", True)]


def _ended_then_answer(decision: str):
    _seed()
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    plan = _week(1)
    with _on(1):
        tools.approve_weekly_plan(plan, approved_by="Emily")
    return tools.decide_staple_line(_staple_line(), decision)


def test_we_have_plenty_leaves_this_weeks_curries_and_last_weeks_question():
    out = _ended_then_answer("plenty")
    # The staple's 2 go; this week's 4 stay for the curries; last week's 4
    # are still the question they were.
    assert out["kept_on_list"] == "4"
    assert sorted(_onions()) == [("4", "carried", False), ("4", "needed", False)]
    tools.undo_staple_decision(out["id"])
    assert sorted(_onions()) == [("4", "carried", False), ("6", "needed", True)]


def test_not_this_trip_leaves_this_weeks_curries_and_last_weeks_question():
    out = _ended_then_answer("skip")
    assert out["kept_on_list"] == "4"
    assert sorted(_onions()) == [("4", "carried", False), ("4", "needed", False)]


def test_a_staple_line_that_was_all_last_weeks_becomes_the_plans_leftover():
    _seed()
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    line = _staple_line()
    tools.update_grocery_item(line, quantity="4")  # typed down to the curries' share
    plan = _week(1)
    with _on(1):
        tools.approve_weekly_plan(plan, approved_by="Emily")
    # The line itself is last week's leftover, and no longer the staple's:
    # a Keep must not bring it back as "Probably running low".
    assert sorted(_onions()) == [("4", "carried", False), ("4", "needed", False)]
    conn = get_conn()
    added_by = conn.execute("SELECT added_by FROM grocery_items WHERE id = ?", (line,)).fetchone()[0]
    conn.close()
    assert added_by != "staple"


def _kept_then_answer(decision: str):
    _seed()
    tools.approve_weekly_plan(_week(0), approved_by="Emily")
    plan = _week(1)
    with _on(1):
        tools.approve_weekly_plan(plan, approved_by="Emily")
    carried = tools.list_carried_over_items()[0]
    kept = tools.keep_carried_over_item(carried["item_id"])
    # Kept on a line of its own, not onto the staple's suggestion: the
    # staple's answers would take it off with the staple's 2 (review,
    # 2026-10-23).
    assert kept["merged_into"] is None
    assert sorted(_onions()) == [("4", "needed", False), ("6", "needed", True)]
    staple = _staple_line()
    out = tools.decide_staple_line(staple, decision)
    return carried["item_id"], staple, out


def test_kept_amount_survives_we_have_plenty_and_both_undo():
    carried, _staple, out = _kept_then_answer("plenty")
    # The staple's 2 go; the kept 4 and this week's 4 stay.
    assert sorted(_onions()) == [("4", "needed", False), ("4", "needed", False)]
    tools.undo_staple_decision(out["id"])
    assert sorted(_onions()) == [("4", "needed", False), ("6", "needed", True)]
    tools.undo_carried_over_decision(carried)
    assert sorted(_onions()) == [("4", "carried", False), ("6", "needed", True)]


def test_kept_amount_survives_not_this_trip_and_both_undo():
    carried, _staple, out = _kept_then_answer("skip")
    assert sorted(_onions()) == [("4", "needed", False), ("4", "needed", False)]
    tools.undo_staple_decision(out["id"])
    assert sorted(_onions()) == [("4", "needed", False), ("6", "needed", True)]
    tools.undo_carried_over_decision(carried)
    assert sorted(_onions()) == [("4", "carried", False), ("6", "needed", True)]
