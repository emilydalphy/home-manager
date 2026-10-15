"""
Once tonight's cook has started, Today stops sending the cook shopping for it.

Found 2026-10-14 by driving the app: with tonight's dinner already on the
stove (meal_plan_entries.cook_started_at set), Now still featured "Shop for
tonight · by 5:55" and the dock said "Go shopping" — a deadline for food the
cook has either got or is doing without. A started dinner now leaves the
shop move's "soonest waiting cook" the same way a cooked one always has, so
the deadline moves to the next waiting cook if there is one.

Dates are the HOUSEHOLD's (conftest.household_today), and every call passes
its own `now`, so nothing here depends on the hour the suite runs at.
"""
from __future__ import annotations

import datetime
from datetime import time

from conftest import household_today

from app import tools
from app.db import get_conn


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(household_today(), time(hour, minute))


def _seed(tomorrow_too: bool = False):
    today = household_today()
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    tools.add_recipe(
        "Sunday Roast",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb", "category": "meat-seafood"}],
        prep_time_minutes=20, cook_time_minutes=90, default_servings=2,
    )
    tools.add_recipe(
        "Garlic Butter Shrimp",
        ingredients=[{"item": "Shrimp", "qty": "1 lb", "category": "meat-seafood"}],
        prep_time_minutes=10, cook_time_minutes=15, default_servings=2,
    )
    plan_id = tools.create_weekly_plan(
        (today - datetime.timedelta(days=2)).isoformat())["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Sunday Roast", slot="dinner",
                    weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True)
    if tomorrow_too:
        tools.plan_meal((today + datetime.timedelta(days=1)).isoformat(),
                        "Garlic Butter Shrimp", slot="dinner",
                        weekly_plan_id=plan_id, add_ingredients_to_grocery_list=True)
    tools.set_dinner_window("5_6ish")  # dinner lands at 5:30, roast starts 3:40


def _start_tonight(hour: int = 15, minute: int = 45):
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET cook_started_at = ? "
        "WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (_at(hour, minute).isoformat(timespec="seconds"), tools.household_id(),
         household_today().isoformat()),
    )
    conn.commit()
    conn.close()


def _shop(payload: dict) -> dict | None:
    hits = [m for m in payload["moves"] if m["kind"] == "shop"]
    return hits[0] if hits else None


def test_a_dinner_not_yet_started_still_sends_you_shopping():
    """GUARD. Unstarted, unchanged: the timed move, featured."""
    _seed()
    payload = tools.today_moves(now=_at(12))
    shop = _shop(payload)
    assert shop["title"] == "Shop for tonight"
    assert shop["detail"] == "1 item · by 3:40"
    assert payload["featured"] == shop["id"]


def test_a_started_dinner_puts_no_shop_deadline_on_the_day():
    """CATCH. The reported walk: the roast is on, its chicken still on the
    list. No "Shop for tonight", no "Go shopping" — the same answer a cooked
    dinner already gets."""
    _seed()
    _start_tonight()
    payload = tools.today_moves(now=_at(16))
    shop = _shop(payload)
    assert shop is None or shop.get("timed", True) is False, shop
    assert not (payload["featured"] or "").startswith("shop:")


def test_the_deadline_moves_to_the_next_waiting_cook():
    """CATCH. Tonight is started; tomorrow's shrimp is also waiting on the
    list. The deadline is tomorrow's, as it would be with tonight cooked."""
    _seed(tomorrow_too=True)
    _start_tonight()
    shop = _shop(tools.today_moves(now=_at(16)))
    assert shop is not None
    assert shop["title"] == "Shop before tomorrow"
    assert shop.get("timed", True) is True
    assert "2 items" in shop["detail"], "the count is still the whole list"
    assert "by tomorrow" in shop["detail"]


def test_the_list_itself_is_untouched_by_a_start():
    """GUARD. Starting a cook changes what Today says, never the list."""
    _seed()
    before = [r["item"] for r in tools.list_grocery_list(status="needed")]
    _start_tonight()
    tools.today_moves(now=_at(16))
    assert [r["item"] for r in tools.list_grocery_list(status="needed")] == before
