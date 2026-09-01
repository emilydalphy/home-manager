"""
Self-service reset: starting a week's plan or the grocery list over.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import HOUSEHOLD_ID
from . import grocery as _grocery
from . import weekly_plan as _weekly_plan


# "This week needs a do-over" without going through chat and without
# touching anything else the household owns. Deliberately narrow: recipes,
# chores, members, inventory and the household's own memory are all out of
# scope here — wiping those is reset_household.py, an admin script that is
# not meant for regular use and has no in-app entry point on purpose.
def clear_weekly_plan(weekly_plan_id: int | None = None) -> dict:
    """
    Take every meal off a week's plan at once — the whole-plan version of
    un-planning a single meal. Defaults to the household's current plan
    (see _current_weekly_plan_row), same as every other plan-scoped tool
    that takes an optional weekly_plan_id.

    Each entry's grocery contribution is reversed first, one meal at a
    time, through the same _reverse_meal_grocery_contributions() call
    swap_meal_in_plan already makes — so the list is left holding only
    what's still actually planned or was asked for directly, rather than a
    week's worth of orphaned ingredients. That helper leaves anything
    already moved to in_cart/purchased alone (the shopper has acted on it),
    which is the behaviour wanted here too: clearing the plan shouldn't
    yank something out of a cart mid-trip.

    The weekly_plans row is emptied, not deleted. The week's dates and its
    constraints_notes ("out Thursday, keep it under 30 minutes") survive,
    so re-planning the same week fills this plan back in instead of
    stranding an empty one beside a new one for _current_weekly_plan_row to
    choose between. Its status drops back to 'draft' — an empty week isn't
    an approved one. Prep tasks go with the meals, since they only describe
    prepping meals that no longer exist.
    """
    conn = get_conn()
    if weekly_plan_id is None:
        plan = _weekly_plan._current_weekly_plan_row(conn)
    else:
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, HOUSEHOLD_ID),
        ).fetchone()
        if not plan:
            conn.close()
            raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    if not plan:
        conn.close()
        return {
            "weekly_plan_id": None, "week_start_date": None, "meals_cleared": 0,
            "removed_items": [], "trimmed_items": [], "prep_tasks_cleared": 0,
        }
    weekly_plan_id = plan["id"]
    week_start_date = plan["week_start_date"]
    entry_ids = [
        row["id"] for row in conn.execute(
            "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (weekly_plan_id, HOUSEHOLD_ID),
        ).fetchall()
    ]
    conn.close()

    removed_items = []
    trimmed_items = []
    for entry_id in entry_ids:
        reversal = _grocery._reverse_meal_grocery_contributions(entry_id)
        removed_items.extend(reversal["removed_items"])
        trimmed_items.extend(reversal["trimmed_items"])

    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    prep = conn.execute(
        "DELETE FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    prep_tasks_cleared = prep.rowcount
    conn.execute(
        "UPDATE weekly_plans SET status = 'draft', updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {
        "weekly_plan_id": weekly_plan_id,
        "week_start_date": week_start_date,
        "meals_cleared": len(entry_ids),
        "removed_items": removed_items,
        "trimmed_items": trimmed_items,
        "prep_tasks_cleared": prep_tasks_cleared,
    }


def get_reset_preview() -> dict:
    """
    What a reset would actually remove, counted before anything happens, so
    the confirm dialog can say "12 planned meals and 23 grocery items"
    instead of asking the household to agree to an unspecified wipe — and
    can disable a choice that would do nothing. Read-only.

    grocery_count counts what clear_grocery_list('needed') would delete,
    which is the whole list as the Grocery screen means it: everything
    still to buy, whether a meal plan put it there or a person did.
    Anything already in a cart or bought stays, and isn't counted here.
    """
    conn = get_conn()
    plan = _weekly_plan._current_weekly_plan_row(conn)
    meal_count = 0
    if plan:
        meal_count = conn.execute(
            "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (plan["id"], HOUSEHOLD_ID),
        ).fetchone()["n"]
    grocery_count = conn.execute(
        "SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ? AND status = 'needed'",
        (HOUSEHOLD_ID,),
    ).fetchone()["n"]
    conn.close()
    return {
        "weekly_plan_id": plan["id"] if plan else None,
        "week_start_date": plan["week_start_date"] if plan else None,
        "meal_count": meal_count,
        "grocery_count": grocery_count,
    }
