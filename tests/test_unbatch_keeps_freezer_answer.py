"""
Taking a batch apart (or putting it back) keeps the household's freezer
answer — defrost.restate_frozen_after_batch_change.

Found by hand on a throwaway DB: a week with a prep day batches Friday's
salmon for Saturday at approval; the freezer step is answered "Salmon
fillets: in the freezer" (one move, for Friday, and the salmon line set
aside); then "cook the salmon fresh on Saturday" in the chat. Before the
fix that left NO move for Saturday — nothing anywhere said to take the
second lot out — and put four salmon fillets back on the shopping list,
under "Your list has changed to match". Undo then left the opposite: a
pending move for a night that reheats again.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn

# A Monday, mid-morning: every move this week is still ahead.
pytestmark = pytest.mark.today("2026-10-12 12:00")
MON = datetime.date(2026, 10, 12)


def _day(n: int) -> str:
    return (MON + datetime.timedelta(days=n)).isoformat()


def _week():
    tools.add_member("Emily")
    tools.add_member("Jamie")
    tools.set_prep_days([{"weekday": "sunday"}])
    tools.add_recipe(
        "Salmon Bowls",
        ingredients=[
            {"item": "Salmon fillets", "qty": "4", "category": "meat/seafood"},
            {"item": "Rice", "qty": "2 cups", "category": "pantry"},
        ],
        instructions=["Cook the rice.", "Bake the salmon for 12 minutes."],
        default_servings=4,
    )
    plan_id = tools.create_weekly_plan(MON.isoformat())["weekly_plan_id"]
    wed = tools.plan_meal(_day(2), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu = tools.plan_meal(_day(3), "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    # The approval-time batch is the precondition, not the thing under test.
    chains = tools.plan_leftover_chains(plan_id)
    assert thu in chains["leftovers"], "a prep-day household batches the repeat at approval"
    tools.confirm_frozen_items(plan_id, ["Salmon fillets"])
    return plan_id, wed, thu


def _moves(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id, task_date, status FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND task_type = 'defrost' ORDER BY meal_plan_entry_id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {r["meal_plan_entry_id"]: (r["task_date"], r["status"]) for r in rows}


def _salmon_to_buy():
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity FROM grocery_items WHERE item = 'Salmon fillets' "
        "AND status IN ('needed', 'in_cart')"
    ).fetchall()
    conn.close()
    return [r["quantity"] for r in rows]


def test_unbatching_books_the_freed_night_and_keeps_the_fish_off_the_list(client):
    plan_id, wed, thu = _week()
    assert _moves(plan_id) == {wed: (_day(1), "pending")}
    assert _salmon_to_buy() == []

    result = tools.unbatch("the salmon")
    assert result["status"] == "unbatched"

    assert _moves(plan_id) == {
        wed: (_day(1), "pending"),
        thu: (_day(2), "pending"),
    }, "Thursday cooks for itself now, so it needs its own fridge move"
    assert _salmon_to_buy() == [], "the household said the salmon is in the freezer"


def test_putting_the_batch_back_drops_the_reheat_nights_move(client):
    plan_id, wed, thu = _week()
    undo = tools.unbatch("the salmon")["undo"]
    assert thu in _moves(plan_id)

    assert tools.rebatch(undo)["status"] == "rebatched"

    assert _moves(plan_id) == {wed: (_day(1), "pending")}
    assert _salmon_to_buy() == []


def _needed_lines():
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id, staple_id FROM grocery_items "
        "WHERE item = 'Salmon fillets' AND status IN ('needed', 'in_cart')"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_a_hand_added_line_is_never_set_aside(client):
    """Review repro A: the household's own "2 salmon fillets", added after
    the freezer answer, takes the freed night's amount in the rescale. It
    stays on the list — their 2 must not vanish with the plan's share."""
    plan_id, wed, thu = _week()
    tools.add_grocery_item("Salmon fillets", quantity="2", category="meat/seafood")

    tools.unbatch("the salmon")

    lines = _needed_lines()
    assert len(lines) == 1 and lines[0]["source_weekly_plan_id"] is None
    assert thu in _moves(plan_id), "the move half still lands"


def test_a_staples_line_is_never_set_aside_and_logs_no_plenty(client):
    """Review repro B: a salmon staple's line is the staple's, not the
    plan's — setting it aside would also log a "plenty" nobody tapped."""
    plan_id, wed, thu = _week()
    tools.add_staple("Salmon fillets", quantity="2", category="meat/seafood", running_low=True)
    tools.sync_due_staples()
    assert any(r["staple_id"] for r in _needed_lines()), "precondition: the staple is on the list"

    tools.unbatch("the salmon")

    assert any(r["staple_id"] for r in _needed_lines())
    conn = get_conn()
    plenty = conn.execute("SELECT COUNT(*) FROM staple_events WHERE kind = 'plenty'").fetchone()[0]
    conn.close()
    assert plenty == 0
