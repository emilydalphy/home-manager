"""
Planning next week before this week ends must not hide this week's shopping
(Loop Board, QA walk 2 — 2026-10-02, High).

    Onboard on a Friday (plan = Fri–Sun), approve, tick two or three things.
    Same morning: Plan › Plan next week › Draft my week › Approve. Shop now
    opens on "Still on the list from last week · 35 things", listing
    tonight's bolognese and tomorrow's Pad Kra Pao; "Decide later" leaves
    only next week's list, and today's "Shop for lunch · by noon" turns into
    an untimed "Any time".

Root cause: grocery.set_aside_carried_over_items counted a plan as "last
week" once its period had STARTED, which takes in the week the household
is still in. Now only a plan whose period has ENDED is a leftover; a week
still running keeps its lines on the list as this week's, and they are held
out of the new week's ingest just long enough that its amounts land on
their own lines (the 2026-09-13 quantity-inflation fix, kept).

Every test here is red on main at 926bab4 except the three marked GUARD.
"""
from __future__ import annotations

import datetime
from unittest import mock

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery
from app.tools import recipes as _recipes


def _today() -> datetime.date:
    return _grocery._household_today()


def _plan(start: datetime.date, days: int = 7) -> int:
    """A plan covering `days` days from `start`, with nothing on it yet."""
    return tools.create_weekly_plan(start.isoformat(), day_count=days)["weekly_plan_id"]


def _rows(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, status, source_weekly_plan_id, carried_from_plan_id "
        "FROM grocery_items WHERE item = ? ORDER BY id",
        (item,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture
def bolognese():
    tools.add_recipe("Bolognese", ingredients=[
        {"item": "Ground turkey", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "Spaghetti", "qty": "1 box", "category": "pantry"},
        {"item": "Ground cumin", "qty": "1 tsp", "category": "pantry"},
    ])


def _this_week_running(days_left: int = 2) -> tuple[int, int]:
    """This week's plan, begun two days ago with `days_left` days still to
    come after today, tonight's bolognese on it and approved. Returns
    (plan id, tonight's entry id)."""
    today = _today()
    plan = _plan(today - datetime.timedelta(days=2), days=3 + days_left)
    entry = tools.plan_meal(today.isoformat(), "Bolognese", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    return plan, entry["entry_id"]


def _approve_next_week(start: datetime.date) -> dict:
    plan = _plan(start)
    tools.plan_meal(start.isoformat(), "Bolognese", slot="dinner", weekly_plan_id=plan)
    return tools.approve_weekly_plan(plan, approved_by="Emily") | {"plan": plan}


def test_next_week_keeps_this_weeks_shop(bolognese):
    """CATCH. The card's own test: two plans, the first still running,
    approve the second — the first's lines stay needed."""
    this_week, _tonight = _this_week_running()
    result = _approve_next_week(_today() + datetime.timedelta(days=3))

    assert result["carried_over_count"] == 0
    assert tools.list_carried_over_items() == []
    turkey = _rows("Ground turkey")
    assert [(r["status"], r["source_weekly_plan_id"]) for r in turkey] == [
        ("needed", this_week),
        ("needed", result["plan"]),
    ], "this week's line stays this week's; next week's is its own"


def test_the_two_weeks_amounts_never_sum_onto_one_line(bolognese):
    """GUARD (the 2026-09-13 inflation fix, kept — passes on main too, where
    the running week was set aside instead). Held out of the ingest,
    this week's 2 lbs is still 2 lbs and next week's 2 lbs lands on a line
    of its own rather than making 4 lbs on this week's."""
    _this_week_running()
    _approve_next_week(_today() + datetime.timedelta(days=3))
    assert [r["quantity"] for r in _rows("Ground turkey")] == ["2 lbs", "2 lbs"]


def test_tonights_dinner_still_has_a_shop_waiting(bolognese):
    """CATCH. Today's Shop row reads its deadline off the ledger: tonight's
    meal must still have a needed line after next week is approved."""
    _plan_id, tonight = _this_week_running()
    assert tonight in tools.entry_ids_awaiting_a_shop()
    _approve_next_week(_today() + datetime.timedelta(days=3))
    assert tonight in tools.entry_ids_awaiting_a_shop()


def test_the_last_day_of_the_week_is_still_this_week(bolognese):
    """CATCH. Sunday, tonight's dinner on the plan's LAST day, next week
    approved that morning: still running, still on the list."""
    today = _today()
    this_week = _plan(today - datetime.timedelta(days=6))
    tools.plan_meal(today.isoformat(), "Bolognese", slot="dinner", weekly_plan_id=this_week)
    tools.approve_weekly_plan(this_week, approved_by="Emily")
    result = _approve_next_week(today + datetime.timedelta(days=1))
    assert result["carried_over_count"] == 0
    assert _rows("Ground turkey")[0]["status"] == "needed"


def test_a_running_weeks_unticked_spice_stays_in_its_section(bolognese):
    """CATCH. The set-aside used to delete an earlier week's unticked
    spices; this week's cumin is still this week's."""
    this_week, _ = _this_week_running()
    _approve_next_week(_today() + datetime.timedelta(days=3))
    cumin = [r for r in _rows("Ground cumin") if r["source_weekly_plan_id"] == this_week]
    assert [r["status"] for r in cumin] == ["spice"]


def test_a_week_that_has_ended_is_still_asked_about(bolognese):
    """GUARD. A week whose last day was yesterday IS last week: its unbought
    lines are set aside and the keep-or-drop step asks about them."""
    today = _today()
    last_week = _plan(today - datetime.timedelta(days=7))
    tools.plan_meal((today - datetime.timedelta(days=1)).isoformat(), "Bolognese", slot="dinner", weekly_plan_id=last_week)
    tools.approve_weekly_plan(last_week, approved_by="Emily")
    result = _approve_next_week(today)
    assert result["carried_over_count"] >= 1
    old = [r for r in _rows("Ground turkey") if r["carried_from_plan_id"] == last_week]
    assert [r["status"] for r in old] == ["carried"]


def test_a_failed_approval_puts_this_weeks_lines_back(bolognese):
    """GUARD. The hold lives inside the approval's one transaction: if the
    ingest blows up, nothing of it survives and this week's lines are
    exactly as they were."""
    this_week, _ = _this_week_running()
    start = _today() + datetime.timedelta(days=3)
    plan = _plan(start)
    tools.plan_meal(start.isoformat(), "Bolognese", slot="dinner", weekly_plan_id=plan)
    with mock.patch.object(_recipes, "_add_recipe_ingredients_for_entries", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            tools.approve_weekly_plan(plan, approved_by="Emily")
    assert [(r["status"], r["source_weekly_plan_id"]) for r in _rows("Ground turkey")] == [("needed", this_week)]
    assert [r["status"] for r in _rows("Ground cumin")] == ["spice"]
