"""
Food bought for a dinner still to come is not "already home" for another
one (2026-10-12, overnight Shop hunt).

    This week: tacos on Wednesday. Monday's shop ticks the ground beef and
    tortillas — each tick puts them on the kitchen's shelf. Friday, next
    week is approved (two live weeks) with tacos on it again. The approval
    read Monday's pound of beef as spare, skipped it, and next week's list
    had no beef and no tortillas. Wednesday's tacos ate them; next week's
    tacos had nothing.

The kitchen check (recipes._KitchenStock) now reserves each still-to-cook
meal's ledger share of a line that reached the shelf before it answers
anything (reserve_bought_for_meals_ahead). The pre-shop "maybe already
home" check reads the same stock and gets the same reservation.

CATCH tests are red without the reservation; GUARD tests pass either way.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import pre_shop as _pre_shop
from conftest import household_today


@pytest.fixture
def tacos():
    tools.add_recipe("Tacos", ingredients=[
        {"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Tortillas", "qty": "8", "category": "pantry"},
    ])


def _lines(item: str, status: str = "needed") -> list[dict]:
    return [r for r in tools.list_grocery_list("all") if r["item"] == item and r["status"] == status]


def _this_week_with_tacos(on: datetime.date) -> tuple[int, int]:
    """This week's plan (began yesterday), tacos on `on`, approved.
    Returns (plan id, the tacos' entry id)."""
    today = household_today()
    plan = tools.create_weekly_plan((today - datetime.timedelta(days=1)).isoformat(), day_count=7)["weekly_plan_id"]
    entry = tools.plan_meal(on.isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.approve_weekly_plan(plan, approved_by="Emily")
    return plan, entry


def _shop_everything() -> None:
    for r in tools.list_grocery_list("needed"):
        tools.mark_grocery_item(r["id"], "purchased")


def _approve_next_week_with_tacos() -> dict:
    start = household_today() + datetime.timedelta(days=6)
    plan = tools.create_weekly_plan(start.isoformat(), day_count=7)["weekly_plan_id"]
    tools.plan_meal((start + datetime.timedelta(days=1)).isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)
    return tools.approve_weekly_plan(plan, approved_by="Emily") | {"plan": plan}


def test_catch_next_weeks_tacos_still_get_their_beef(tacos):
    _this_week_with_tacos(household_today() + datetime.timedelta(days=2))
    _shop_everything()
    res = _approve_next_week_with_tacos()
    assert res["already_have_skipped"] == []
    beef = _lines("Ground beef")
    assert [(r["quantity"], r["source_weekly_plan_id"]) for r in beef] == [("1 lb", res["plan"])]
    assert [r["quantity"] for r in _lines("Tortillas")] == ["8"]


def test_catch_pre_shop_does_not_call_a_second_pound_already_home(tacos):
    # Wednesday's beef is bought; somebody adds another pound for a burger
    # night. The shelf's pound is Wednesday's, so it is not "already home".
    _this_week_with_tacos(household_today() + datetime.timedelta(days=2))
    _shop_everything()
    tools.add_grocery_item("Ground beef", quantity="1 lb", category="meat/seafood")
    flagged = {f["item"] for f in _pre_shop.get_pre_shop_flags()}
    assert "Ground beef" not in flagged
    assert "Ground beef" not in {f["item"] for f in _pre_shop.get_grocery_already_have_items()}


def test_guard_stock_beyond_what_is_spoken_for_still_skips(tacos):
    # Two pounds on the shelf: one is Wednesday's, the other really is spare.
    _this_week_with_tacos(household_today() + datetime.timedelta(days=2))
    _shop_everything()
    tools.update_inventory("Ground beef", "add", quantity="1 lb", category="meat/seafood")
    res = _approve_next_week_with_tacos()
    assert "Ground beef" in res["already_have_skipped"]
    assert _lines("Ground beef") == []


def test_guard_a_cooked_dinner_reserves_nothing(tacos):
    # Tonight's tacos, bought and cooked: the shelf was emptied by the cook,
    # and an extra pound put away since is genuinely spare.
    _plan, entry = _this_week_with_tacos(household_today())
    _shop_everything()
    tools.check_off_meal(entry, "done")
    tools.update_inventory("Ground beef", "add", quantity="1 lb", category="meat/seafood")
    tools.update_inventory("Tortillas", "add", quantity="8", category="pantry")
    res = _approve_next_week_with_tacos()
    assert set(res["already_have_skipped"]) == {"Ground beef", "Tortillas"}


def test_guard_a_past_dinner_nobody_ticked_reserves_nothing(tacos):
    # Yesterday's tacos were never ticked cooked: whether they were eaten is
    # unknown, so the shelf is read the way it always was.
    _this_week_with_tacos(household_today() - datetime.timedelta(days=1))
    _shop_everything()
    res = _approve_next_week_with_tacos()
    assert set(res["already_have_skipped"]) == {"Ground beef", "Tortillas"}


def test_reservation_is_per_meal_share_not_the_whole_line(tacos):
    # Two taco nights this week share one 2 lb line; one is cooked. Only
    # the other night's pound is spoken for.
    today = household_today()
    plan = tools.create_weekly_plan((today - datetime.timedelta(days=1)).isoformat(), day_count=7)["weekly_plan_id"]
    first = tools.plan_meal(today.isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal((today + datetime.timedelta(days=3)).isoformat(), "Tacos", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    _shop_everything()
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (first,))
    conn.commit()
    conn.close()
    # Shelf still says 2 lb (the cook above did not deplete): one is spare.
    res = _approve_next_week_with_tacos()
    assert "Ground beef" in res["already_have_skipped"]


# ---------- the thaw question (defrost.meat_items_for_plan) ----------
# A ticked line's own inventory row is a GUESSED shelf and defrost never
# reads it (inventory.GUESSED_LOCATION_SOURCES), so the case that matters
# is beef somebody has put on the fridge shelf by name.

def _fridge_beef() -> None:
    # The household tells the kitchen where the pound the shop bought is:
    # the guessed row goes and a named fridge row takes its place.
    tools.update_inventory("Ground beef", "remove")
    tools.update_inventory("Ground beef", "add", quantity="1 lb", category="meat/seafood", location="fridge")


def test_catch_next_weeks_thaw_ask_does_not_spend_this_weeks_fridge_beef(tacos):
    from app.tools import defrost as _defrost
    _this_week_with_tacos(household_today() + datetime.timedelta(days=2))
    _shop_everything()
    _fridge_beef()
    res = _approve_next_week_with_tacos()
    assert "Ground beef" in [i["item"] for i in _defrost.meat_items_for_plan(res["plan"])]


def test_guard_this_weeks_own_fridge_beef_still_covers_this_week(tacos):
    from app.tools import defrost as _defrost
    plan, _entry = _this_week_with_tacos(household_today() + datetime.timedelta(days=2))
    _shop_everything()
    _fridge_beef()
    assert "Ground beef" not in [i["item"] for i in _defrost.meat_items_for_plan(plan)]
