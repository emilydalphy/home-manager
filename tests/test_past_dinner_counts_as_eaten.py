"""
A dinner whose night has passed counts as eaten for inventory, ticked or
not (Loop Board, 2026-10-21).

    Last week: tortillas and ground beef ticked on Shop (each tick puts
    them on the kitchen's shelf), tacos never marked cooked. This week:
    tacos again, approved. The list had no tortillas and no beef — the
    approval read last week's as still at home.

recipes._KitchenStock.reserve_bought_for_meals_ahead now also claims the
ledger share of a PAST meal that was not marked skipped, so the food bought
for it is not offered to anything else. Emily's open question: is assuming
an unanswered past dinner was eaten right? Built with "yes".

CATCH tests are red on main; GUARD tests pass either way.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.tools import pre_shop as _pre_shop
from conftest import approving_on, household_today


@pytest.fixture
def tacos():
    tools.add_recipe("Tacos", ingredients=[
        {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Tortillas", "qty": "8", "category": "pantry"},
    ])


def _needed(item: str) -> list[str]:
    return [r["quantity"] for r in tools.list_grocery_list("needed") if r["item"] == item]


def _last_week_tacos_shopped(days_ago: int = 3) -> int:
    """Last week's plan with tacos `days_ago` nights back, approved as it
    began, every line ticked bought. Returns the tacos' entry id."""
    today = household_today()
    start = today - datetime.timedelta(days=7)
    plan = tools.create_weekly_plan(start.isoformat(), day_count=7)["weekly_plan_id"]
    entry = tools.plan_meal((today - datetime.timedelta(days=days_ago)).isoformat(), "Tacos",
                            slot="dinner", weekly_plan_id=plan)["entry_id"]
    with approving_on(start):
        tools.approve_weekly_plan(plan, approved_by="Emily")
    for r in tools.list_grocery_list("needed"):
        tools.mark_grocery_item(r["id"], "purchased")
    return entry


def _approve_this_week_with_tacos() -> dict:
    today = household_today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=7)["weekly_plan_id"]
    tools.plan_meal((today + datetime.timedelta(days=2)).isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)
    return tools.approve_weekly_plan(plan, approved_by="Emily")


def test_catch_last_weeks_unticked_tacos_were_eaten(tacos):
    _last_week_tacos_shopped()
    res = _approve_this_week_with_tacos()
    assert res["already_have_skipped"] == []
    assert _needed("Ground beef") == ["1 lb"]
    assert _needed("Tortillas") == ["8"]


def test_catch_pre_shop_does_not_call_eaten_tacos_already_home(tacos):
    _last_week_tacos_shopped()
    tools.add_grocery_item("Tortillas", quantity="8", category="pantry")
    assert "Tortillas" not in {f["item"] for f in _pre_shop.get_grocery_already_have_items()}


def test_guard_a_skipped_dinner_leaves_its_food_at_home(tacos):
    # "We skipped it" on yesterday's card: the tacos were not eaten, so
    # what was bought for them is still in the kitchen.
    entry = _last_week_tacos_shopped(days_ago=1)
    tools.answer_yesterday(entry, "skipped")
    res = _approve_this_week_with_tacos()
    assert set(res["already_have_skipped"]) == {"Ground beef", "Tortillas"}
    assert _needed("Ground beef") == []


def test_guard_stock_put_away_since_is_still_spare(tacos):
    # Last week's tacos ate last week's shop; a pound of beef and a pack of
    # tortillas added since really are spare.
    _last_week_tacos_shopped()
    tools.update_inventory("Ground beef", "add", quantity="1 lb", category="meat/seafood")
    tools.update_inventory("Tortillas", "add", quantity="8", category="pantry")
    res = _approve_this_week_with_tacos()
    assert set(res["already_have_skipped"]) == {"Ground beef", "Tortillas"}
