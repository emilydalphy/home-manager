"""
A household's own grocery line comes back to the same number when a swap is
undone, and never creeps over repeated swap/undo cycles (overnight
2026-10-21; stacks on overnight/defect-hunt-2-2026-10-21).

Reviewer's repro over HTTP with the model stubbed: an approved week's onion
line read 14 (ledger 14.0), a hand-added "2" joined it and made it the
household's own (source_weekly_plan_id NULL) at 16, Swap (+0.5 net) read 17,
and Undo — ledger back to exactly 14.0 — still read 17. Main: 17 -> 18 -> 18.

Removals already take the plan's ROUNDED contribution back off a standing
want (_ledger_totals + _restate_standing_want). The add didn't: one meal's
share was rounded up on its own (0.5 -> 1) and summed onto the line, so the
line carried more plan than ceil(ledger) said it did, and the next removal
left the extra on as if it were the household's.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from conftest import household_today


def _onions() -> list[tuple[str, int | None]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE lower(item) = 'yellow onion' AND status = 'needed' ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["source_weekly_plan_id"]) for r in rows]


@pytest.fixture
def soup_week():
    """Two adults, a soup written for four: each night is half an onion.
    Three soup nights (1.5 -> "2") and one Toast night to swap."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.set_member_age_group("Emily", "adult")
    tools.set_member_age_group("Sam", "adult")
    tools.add_recipe(
        "Onion Soup", default_servings=4,
        ingredients=[{"item": "Yellow onion", "qty": "1", "category": "produce"}],
    )
    tools.add_recipe(
        "Toast", default_servings=4,
        ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}],
    )
    today = household_today()
    days = [(today + datetime.timedelta(days=n)).isoformat() for n in (1, 2, 3, 4)]
    plan = tools.create_weekly_plan(today.isoformat(), day_count=5)["weekly_plan_id"]
    for d in days[:3]:
        tools.plan_meal(d, "Onion Soup", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(days[3], "Toast", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    assert _onions() == [("2", plan)]
    # The household's own two join the plan's line and make it theirs.
    tools.add_grocery_item("Yellow onion", quantity="2", category="produce")
    assert _onions() == [("4", None)]
    return plan, days


def test_a_swap_onto_the_households_line_adds_only_what_the_rounding_moved(soup_week):
    """CATCH — main reads "5": half an onion rounded up to one on its own.
    The plan's 1.5 already bought 2; at 2.0 it still buys 2."""
    plan, days = soup_week
    tools.swap_meal_in_plan(plan, days[3], "Onion Soup", slot="dinner")
    assert _onions() == [("4", None)]


def test_swap_then_undo_comes_back_to_where_it_was(soup_week):
    """CATCH — the repro's shape: 4 nights of soup (2.0) then a fifth
    (2.5 -> 3) reads 2 + 3; taking the fifth back off reads 2 + 2."""
    plan, days = soup_week
    tools.swap_meal_in_plan(plan, days[3], "Onion Soup", slot="dinner")
    extra = (household_today() + datetime.timedelta(days=0)).isoformat()
    tools.swap_meal_in_plan(plan, extra, "Onion Soup", slot="dinner")
    assert _onions() == [("5", None)]
    tools.swap_meal_in_plan(plan, extra, "Toast", slot="dinner")
    assert _onions() == [("4", None)]


def test_repeated_swap_and_undo_never_creeps_and_never_loses_the_households_two(soup_week):
    """CATCH — main climbs a whole onion every cycle (4 -> 5 -> 6 ...)."""
    plan, days = soup_week
    for _ in range(4):
        tools.swap_meal_in_plan(plan, days[3], "Onion Soup", slot="dinner")
        tools.swap_meal_in_plan(plan, days[3], "Toast", slot="dinner")
        assert _onions() == [("4", None)]
    # Every soup night gone: the household's own two are still there.
    for d in days[:3]:
        tools.swap_meal_in_plan(plan, d, "Toast", slot="dinner")
    assert _onions() == [("2", None)]


def test_a_household_line_edited_down_takes_no_more_than_a_plain_add(soup_week):
    """GUARD — the restate can't see an edit down (the household's "1" under
    a plan that wanted 2 reads as none of their own), so it would write the
    plan's whole 3. Held at what the plain add writes: 1 + 1."""
    plan, days = soup_week
    tools.swap_meal_in_plan(plan, days[3], "Onion Soup", slot="dinner")
    conn = get_conn()
    item_id = conn.execute(
        "SELECT id FROM grocery_items WHERE lower(item) = 'yellow onion'").fetchone()["id"]
    conn.close()
    tools.update_grocery_item(item_id, quantity="1")
    assert _onions() == [("1", None)]
    tools.swap_meal_in_plan(plan, household_today().isoformat(), "Onion Soup", slot="dinner")
    assert _onions() == [("2", None)]
