"""
After a swap the list read "red lentils 1.5 cups + 8 tbsp" (defect hunt
2026-10-07, Medium). Swapping a dinner to Lentil Soup with lentils already
on the list added "8 tbsp" beside "1.5 cups" instead of making 2 cups:
grocery._try_consolidate_quantity summed only identical unit words, though
the shopping list's own unit families (cup/tbsp/tsp, lb/oz, g/kg, ml/l)
convert everywhere else (quantities._convert_to_unit, _ledger_buckets).

The fix lives on the PLAN-owned line (recipes.WeekGroceryBuffer.flush):
when a measured share in another unit of the same family lands on it, the
line is re-read from its ledger (grocery._recompute_plan_line_from_ledger),
which sums one family and rounds once on the whole line — as approval does.
_try_consolidate_quantity is unchanged: a household's standing want never
takes a different-unit plan share (_merge_target) and is never re-rounded,
because merging there rounded once per add and _restate_standing_want then
ratcheted the line (review 2026-10-07: "1 lb" + 7 oz + 3 oz climbing by a
quarter pound a cycle).
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery


def _today() -> datetime.date:
    return _grocery._household_today()


def _lentils() -> list[tuple[str, int | None]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE lower(item) = 'red lentils' AND status = 'needed' ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["source_weekly_plan_id"]) for r in rows]


@pytest.mark.parametrize("a,b", [("1 cup", "200 g"), ("1 lb", "2 cups"), ("1 cup", "2")])
def test_mass_against_volume_stays_two_parts(a, b):
    """GUARD. No conversion is guessed across families or onto a count."""
    assert _grocery._try_consolidate_quantity(a, b) == (f"{a} + {b}", False)


@pytest.fixture
def lentil_week():
    tools.add_recipe("Dal", ingredients=[{"item": "Red lentils", "qty": "1.5 cups", "category": "pantry"}])
    tools.add_recipe("Lentil Soup", ingredients=[{"item": "Red lentils", "qty": "8 tbsp", "category": "pantry"}])
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}])
    today = _today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=3)["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Dal", slot="dinner", weekly_plan_id=plan)
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    tools.plan_meal(tomorrow, "Toast", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    return plan, tomorrow


def test_a_swap_adds_up_and_undoing_it_restores_the_line(lentil_week):
    """CATCH. The card's repro, and its way back."""
    plan, tomorrow = lentil_week
    assert _lentils() == [("1.5 cups", plan)]
    tools.swap_meal_in_plan(plan, tomorrow, "Lentil Soup", slot="dinner")
    assert _lentils() == [("2 cups", plan)]
    tools.swap_meal_in_plan(plan, tomorrow, "Toast", slot="dinner")
    assert _lentils() == [("1.5 cups", plan)]


def test_a_households_own_lentils_keep_their_own_line(lentil_week):
    """GUARD (main's behaviour, kept): a different-unit plan share stays off
    the household's standing line, on a plan line of its own, and goes
    when the meal goes — the household's "1 cup" is never re-rounded."""
    plan, tomorrow = lentil_week
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE lower(item) = 'red lentils'")
    conn.commit()
    conn.close()
    tools.add_grocery_item("Red lentils", quantity="1 cup", category="pantry")
    tools.swap_meal_in_plan(plan, tomorrow, "Lentil Soup", slot="dinner")
    assert _lentils() == [("1 cup", None), ("8 tbsp", plan)]
    tools.swap_meal_in_plan(plan, tomorrow, "Toast", slot="dinner")
    assert _lentils() == [("1 cup", None)]


def test_a_small_amount_is_never_swallowed_by_a_household_line(lentil_week):
    """GUARD (review blocker 2): "1 cup" + "2 tbsp" never reads "1 cup"."""
    plan, tomorrow = lentil_week
    tools.add_recipe("Lentil Garnish", ingredients=[{"item": "Red lentils", "qty": "2 tbsp", "category": "pantry"}])
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE lower(item) = 'red lentils'")
    conn.commit()
    conn.close()
    tools.add_grocery_item("Red lentils", quantity="1 cup", category="pantry")
    tools.swap_meal_in_plan(plan, tomorrow, "Lentil Garnish", slot="dinner")
    assert sorted(q for q, _ in _lentils()) == ["1 cup", "2 tbsp"]


def _beef() -> list[tuple[str, int | None]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE lower(item) = 'ground beef' AND status = 'needed' ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["quantity"], r["source_weekly_plan_id"]) for r in rows]


def test_a_standing_want_holds_steady_over_five_swap_cycles():
    """GUARD (review blocker 1, from its sweep): want "1 lb", a planned 7 oz,
    a 3 oz dinner swapped in and out five times — the same two readings
    every cycle, and the household's 1 lb back at the end."""
    tools.add_recipe("Burgers", ingredients=[{"item": "Ground beef", "qty": "7 oz", "category": "meat/seafood"}])
    tools.add_recipe("Sliders", ingredients=[{"item": "Ground beef", "qty": "3 oz", "category": "meat/seafood"}])
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf", "category": "pantry"}])
    today = _today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=3)["weekly_plan_id"]
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    tools.add_grocery_item("Ground beef", quantity="1 lb", category="meat/seafood")
    tools.plan_meal(today.isoformat(), "Burgers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(tomorrow, "Toast", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    approved = _beef()
    ins, outs = set(), set()
    for _ in range(5):
        tools.swap_meal_in_plan(plan, tomorrow, "Sliders", slot="dinner")
        ins.add(tuple(_beef()))
        tools.swap_meal_in_plan(plan, tomorrow, "Toast", slot="dinner")
        outs.add(tuple(_beef()))
    assert len(ins) == 1 and outs == {tuple(approved)}
    tools.swap_meal_in_plan(plan, today.isoformat(), "Toast", slot="dinner")
    assert [q for q, p in _beef() if p is None] == ["1 lb"]
