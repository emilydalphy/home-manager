"""
A meal added to an approved week rounds the plan's line ONCE, on the whole
line (defect hunt 2026-10-21).

Found driving an approved week over HTTP with the model stubbed: Swap a
dinner, then Undo, and the onions read 21 where they had read 20 before the
swap — the ledger under the line still added up to exactly 20.0. The add
path (recipes.WeekGroceryBuffer.flush) rounded the ONE meal's share up on
its own (0.75 onion -> 1) and summed that onto a line that was already
rounded (20), instead of re-reading the line from its ledger the way every
removal does (grocery._recompute_plan_line_from_ledger). CLAUDE.md's rule
for this ledger: the rounding happens once, on the whole line.

Only a PLAN-owned line is re-read. A household's standing want is never
re-rounded (the 2026-10-07 review: re-rounding it ratcheted the line), and
stays exactly as main writes it.
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
def onion_week():
    """Two adults, a soup written for four: each night's share is half an onion."""
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
    days = [(today + datetime.timedelta(days=n)).isoformat() for n in (1, 2)]
    plan = tools.create_weekly_plan(today.isoformat(), day_count=3)["weekly_plan_id"]
    tools.plan_meal(days[0], "Onion Soup", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(days[1], "Toast", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    return plan, days


def test_a_second_nights_share_rounds_with_the_line_not_on_its_own(onion_week):
    """CATCH — main reads "2": half an onion rounded up to one, added to the
    line's one. Two half-onion nights are one onion."""
    plan, days = onion_week
    assert _onions() == [("1", plan)]
    tools.swap_meal_in_plan(plan, days[1], "Onion Soup", slot="dinner")
    assert _onions() == [("1", plan)]


def test_swap_then_swap_back_leaves_the_line_as_it_was(onion_week):
    """CATCH — the repro's shape: there and back again is where it started."""
    plan, days = onion_week
    tools.swap_meal_in_plan(plan, days[0], "Toast", slot="dinner")
    assert _onions() == []
    tools.swap_meal_in_plan(plan, days[0], "Onion Soup", slot="dinner")
    tools.swap_meal_in_plan(plan, days[1], "Onion Soup", slot="dinner")
    assert _onions() == [("1", plan)]
    tools.swap_meal_in_plan(plan, days[1], "Toast", slot="dinner")
    assert _onions() == [("1", plan)]


def test_a_households_own_onions_are_never_re_rounded(onion_week):
    """GUARD (main's behaviour, kept): the household's typed "3" takes the
    plan's share the way it always has, and is not re-read off a ledger
    that does not describe it."""
    plan, days = onion_week
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE lower(item) = 'yellow onion'")
    conn.commit()
    conn.close()
    tools.add_grocery_item("Yellow onion", quantity="3", category="produce")
    before = _onions()
    tools.swap_meal_in_plan(plan, days[1], "Onion Soup", slot="dinner")
    after = _onions()
    assert [src for _q, src in after] == [None]
    assert before == [("3", None)]
    assert after == [("4", None)]
