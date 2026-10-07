"""
After a swap the list read "red lentils 1.5 cups + 8 tbsp" (defect hunt
2026-10-07, Medium). Swapping a dinner to Lentil Soup with lentils already
on the list added "8 tbsp" beside "1.5 cups" instead of making 2 cups:
grocery._try_consolidate_quantity summed only identical unit words, though
the shopping list's own unit families (cup/tbsp/tsp, lb/oz, g/kg, ml/l)
convert everywhere else (quantities._convert_to_unit, _ledger_buckets).

Now one family is one amount, humanised once on the whole line. Mass
against volume still stays two parts — nothing is guessed — and undoing
the swap puts the line back from the per-meal ledger.
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


@pytest.mark.parametrize("a,b,want", [
    ("1.5 cups", "8 tbsp", "2 cups"),
    ("1 lb", "8 oz", "1.5 lbs"),
    ("500 g", "1 kg", "1.5 kg"),
    ("750 ml", "1 l", "1.75 l"),
])
def test_one_family_is_one_amount(a, b, want):
    """CATCH."""
    assert _grocery._try_consolidate_quantity(a, b) == (want, True)
    assert _grocery._refold_quantity_segments(f"{a} + {b}") == want


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


def test_a_households_own_lentils_take_the_plans_and_give_them_back(lentil_week):
    """GUARD on the standing-want rule (CLAUDE.md): the household's line
    takes the plan's amount in its own family, and only that comes back
    off when the meal goes."""
    plan, tomorrow = lentil_week
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE lower(item) = 'red lentils'")
    conn.commit()
    conn.close()
    tools.add_grocery_item("Red lentils", quantity="1 cup", category="pantry")
    tools.swap_meal_in_plan(plan, tomorrow, "Lentil Soup", slot="dinner")
    assert _lentils() == [("1.5 cups", None)]
    tools.swap_meal_in_plan(plan, tomorrow, "Toast", slot="dinner")
    assert _lentils() == [("1 cup", None)]
