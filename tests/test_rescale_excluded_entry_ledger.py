"""
_rescale_leftover_source_grocery excludes the entry its caller is taking
away (`unlinked_entry_id`) from the recipe group it re-buys. Excluding it
used to mean only "leave it out of the re-ingest": its ledger rows
(meal_plan_grocery_links) stayed on file, so the recompute re-derived each
line WITH its share still counted and then added the group's fresh rounded
total on top — "12 oz + 1.5 lbs" where the list should read "1.5 lbs".

Every caller then reversed the entry itself a moment later and the line
came right, so nobody ever saw it; a caller that excluded and kept would
have left it. The exclusion now clears the entry's ledger rows as part of
excluding it (Loop Board, 2026-10-07).
"""
from __future__ import annotations

import datetime

from conftest import household_today
from app import db, tools
from app.tools import grocery as _grocery, weekly_plan


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


WEEK = _monday().isoformat()
MON, THU = _day(0), _day(3)


def _week(standing_onion=False):
    for name in ("Emily", "Vineeth", "Kid", "Baby"):
        tools.add_member(name)
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])
    tools.add_recipe(
        "Beef Ragu",
        ingredients=[{"item": "Ground beef", "qty": "12 oz", "category": "meat"},
                     {"item": "Onion", "qty": "1", "category": "produce"}],
        default_servings=4, prep_time_minutes=15, cook_time_minutes=40,
        instructions=["Brown the beef.", "Simmer."],
    )
    if standing_onion:
        tools.add_grocery_item("Onion", quantity="1", category="produce")
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {d: tools.plan_meal(d, "Beef Ragu", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
           for d in (MON, THU)}
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    # Chained AFTER approval, so each night still holds its own 12 oz on
    # the ledger while Monday is now the batch for both.
    tools.set_cook_ahead(ids[MON], [ids[THU]])
    return plan_id, ids


def _lines():
    conn = db.get_conn()
    rows = {r["item"]: r["quantity"] for r in conn.execute(
        "SELECT item, quantity FROM grocery_items WHERE status = 'needed'").fetchall()}
    conn.close()
    return rows


def _ledger(entry_id):
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
                     (entry_id,)).fetchone()["n"]
    conn.close()
    return n


def test_excluding_an_entry_takes_its_ledger_rows_with_it():
    """The card's own repro: exclude Thursday, leave its ledger rows on
    file, recompute. Before the fix the line came back "12 oz + 1.5 lbs"
    and "Onion 3"; it reads what the batch buys, one amount each."""
    plan_id, ids = _week()
    assert _lines() == {"Ground beef": "1.5 lbs", "Onion": "2"}
    assert _ledger(ids[THU]) == 2, "precondition: the excluded night still holds its share"

    weekly_plan._rescale_leftover_source_grocery(ids[MON], ids[THU])

    assert _lines() == {"Ground beef": "1.5 lbs", "Onion": "2"}
    assert _ledger(ids[THU]) == 0


def test_the_callers_own_reversal_after_it_changes_nothing():
    """Every caller today reverses the excluded entry right after the
    rescale returns. That must stay harmless: nothing left to reverse."""
    plan_id, ids = _week()
    weekly_plan._rescale_leftover_source_grocery(ids[MON], ids[THU])
    after = _lines()

    _grocery._reverse_meal_grocery_contributions(ids[THU])

    assert _lines() == after == {"Ground beef": "1.5 lbs", "Onion": "2"}


def test_the_same_on_the_callers_connection():
    """_replace_slot_entries and clear_plan_slot hand their transaction in."""
    plan_id, ids = _week()
    conn = db.get_conn()
    try:
        weekly_plan._rescale_leftover_source_grocery(ids[MON], ids[THU], conn=conn)
        conn.commit()
    finally:
        conn.close()

    assert _lines() == {"Ground beef": "1.5 lbs", "Onion": "2"}
    assert _ledger(ids[THU]) == 0


def test_a_standing_want_on_the_line_is_not_taken_down_with_it():
    """A hand-added onion shares the line with the plan's. The excluded
    night's share comes off once — in the helper, not again in the caller —
    and the household's own onion is still there."""
    plan_id, ids = _week(standing_onion=True)
    before = _lines()["Onion"]

    weekly_plan._rescale_leftover_source_grocery(ids[MON], ids[THU])
    _grocery._reverse_meal_grocery_contributions(ids[THU])

    assert _lines()["Onion"] == before


def test_nothing_excluded_still_rebuys_the_whole_recipe_week():
    """meal_variety and batch_undo pass 0: nothing to clear, and the
    recompute is the whole recipe-week as before — the batch on the cook
    night, the fed night holding none of it."""
    plan_id, ids = _week()
    weekly_plan._rescale_leftover_source_grocery(ids[MON], 0)

    assert _lines() == {"Ground beef": "1.5 lbs", "Onion": "2"}
    assert _ledger(ids[MON]) == 2


def test_clearing_the_fed_night_through_the_real_caller():
    """clear_plan_slot is one of the callers that rescales BEFORE reversing
    the entry; end to end, the line is what Monday cooks alone."""
    plan_id, ids = _week()

    weekly_plan.clear_plan_slot(plan_id, THU, "dinner")

    assert _lines() == {"Ground beef": "12 oz", "Onion": "1"}
