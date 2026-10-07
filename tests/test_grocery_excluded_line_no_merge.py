"""
A line set aside as "getting it somewhere else" never swallows a new amount
(Loop Board, 2026-10-07).

    Approve a week with ground turkey; on Shop mark ground turkey "somewhere
    else"; add another meal with ground turkey to the same week. The new
    amount merged onto the excluded line and was hidden — never bought.

Root cause: add_grocery_item's merge candidates read status IN ('needed',
'spice') without `excluded_from_list = 0`, so _merge_target could pick the
hidden line. Now the new amount lands on a visible line of its own and the
excluded line stays exactly as it was. Every test marked CATCH is red
without the fix; GUARD passes on both.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery


def _today() -> datetime.date:
    return _grocery._household_today()


def _rows(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, status, excluded_from_list, source_weekly_plan_id "
        "FROM grocery_items WHERE item = ? COLLATE NOCASE ORDER BY id",
        (item,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _needed(item: str) -> list[dict]:
    key = _grocery._merge_key(item)
    return [r for r in tools.list_grocery_list("needed") if _grocery._merge_key(r["item"]) == key]


@pytest.fixture
def week():
    """An approved week with turkey tacos tonight, the turkey line then set
    aside as "somewhere else". Returns (plan id, excluded line before)."""
    tools.add_recipe("Turkey Tacos", ingredients=[
        {"item": "Ground turkey", "qty": "2 lb", "category": "meat/seafood"},
    ])
    tools.add_recipe("Turkey Chili", ingredients=[
        {"item": "Ground turkey", "qty": "1 lb", "category": "meat/seafood"},
    ])
    today = _today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=7)["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Turkey Tacos", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    [line] = _rows("Ground turkey")
    tools.exclude_grocery_item(line["id"])
    assert _needed("Ground turkey") == []
    return plan, _rows("Ground turkey")[0]


def test_a_plans_new_amount_shows_on_the_list(week):
    """CATCH. The card's own test: exclude, add the same item from a plan,
    the new amount is on the needed list and the excluded line is as it was."""
    plan, before = week
    tomorrow = (_today() + datetime.timedelta(days=1)).isoformat()
    tools.plan_meal(
        tomorrow, "Turkey Chili", slot="dinner", weekly_plan_id=plan,
        add_ingredients_to_grocery_list=True,
    )

    shown = _needed("Ground turkey")
    assert [r["quantity"] for r in shown] == ["1 lb"], shown
    rows = _rows("Ground turkey")
    assert rows[0] == before, "the 'somewhere else' line is untouched"
    assert len(rows) == 2


def test_a_persons_add_shows_on_the_list(week):
    """CATCH. A hand add of the same thing is its own visible line too."""
    _plan, before = week
    result = tools.add_grocery_item("ground turkey", quantity="1 lb", category="meat/seafood")
    assert result["merged"] is False
    assert [r["quantity"] for r in _needed("Ground turkey")] == ["1 lb"]
    assert _rows("Ground turkey")[0] == before


def test_dropping_the_new_meal_takes_back_only_its_own_line(week):
    """CATCH. The ledger points the new meal at the VISIBLE line, so taking
    the meal off reverses that line and leaves the excluded one alone."""
    plan, before = week
    tomorrow = (_today() + datetime.timedelta(days=1)).isoformat()
    tools.plan_meal(
        tomorrow, "Turkey Chili", slot="dinner", weekly_plan_id=plan,
        add_ingredients_to_grocery_list=True,
    )
    assert _needed("Ground turkey")

    tools.clear_plan_slot(plan, tomorrow, "dinner")
    assert _needed("Ground turkey") == []
    live = [r for r in _rows("Ground turkey") if r["status"] in ("needed", "spice")]
    assert live == [before]


def test_put_back_on_the_list_still_works(week):
    """GUARD. include_grocery_item brings the set-aside line back as it was."""
    _plan, before = week
    tools.include_grocery_item(before["id"])
    assert [r["quantity"] for r in _needed("Ground turkey")] == [before["quantity"]]
