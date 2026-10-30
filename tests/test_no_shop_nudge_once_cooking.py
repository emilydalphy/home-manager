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


def _tonight_id() -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), household_today().isoformat()),
    ).fetchone()
    conn.close()
    return f"cook:{row['id']}"


def test_a_started_dinner_is_the_card_over_tomorrows_shop():
    """CATCH (day walk, 2026-10-29). The roast is on and tomorrow's shrimp is
    still on the list: "Shop before tomorrow" stays a row, but Next up — and
    the dock's button — is the dinner on the stove, not "Go shopping"."""
    _seed(tomorrow_too=True)
    _start_tonight()
    payload = tools.today_moves(now=_at(16))
    assert any(m["kind"] == "shop" for m in payload["moves"]), "the shop row stays"
    assert payload["featured"] == _tonight_id()


def test_before_the_start_tomorrows_shop_still_leads():
    """GUARD. Not started: the ranking is as it was — the shop (window open
    since midnight) wins the tie with tonight's cook."""
    _seed(tomorrow_too=True)
    payload = tools.today_moves(now=_at(15))
    tonight = next(m for m in payload["moves"] if m["id"] == _tonight_id())
    assert tonight["started_at"] is None
    assert payload["featured"].startswith("shop:")


def test_an_overdue_fridge_move_still_beats_the_started_dinner():
    """GUARD. Urgent stays first: a fridge move gone past "by tonight" is
    still the card while the roast cooks — it is the one thing that can't
    wait."""
    _seed()
    conn = get_conn()
    plan_id = conn.execute("SELECT id FROM weekly_plans WHERE household_id = ?",
                           (tools.household_id(),)).fetchone()["id"]
    task = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type) VALUES (?, ?, ?, 'Move the shrimp to the fridge — for tomorrow.', "
        "'Garlic Butter Shrimp', 'pending', 'defrost')",
        (tools.household_id(), plan_id, household_today().isoformat()),
    ).lastrowid
    conn.commit()
    conn.close()
    _start_tonight()
    payload = tools.today_moves(now=_at(18))
    assert next(m for m in payload["moves"] if m["id"] == f"fridge:{task}")["overdue"]
    assert payload["featured"] == f"fridge:{task}"
    # ...and only the fridge move: with it ticked, the roast is the card.
    tools.set_move_done(f"fridge:{task}", True)
    assert tools.today_moves(now=_at(18))["featured"] == _tonight_id()
