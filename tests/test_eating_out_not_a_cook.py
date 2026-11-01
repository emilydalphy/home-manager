"""
A dinner typed in as a night out is a meal, never a cook (Emily,
2026-10-31): "Eating out", "Dinner at Grandma's", "restaurant" — no cook
card on Today, no "Tap to start" at the nudge hour, nothing to shop for.

Before, only the takeout words were read that way
(weekly_plan.written_in_not_a_cook), so "Eating out" typed by hand got a
cook card and the 5:30 text "Tonight: Eating out. Tap to start."
"""
from __future__ import annotations

import pytest

from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _weekly_plan
from test_takeout_night_not_a_cook import ISO_TODAY, TODAY, _adult_opted_in, _at, _plan, _utc


@pytest.mark.parametrize("dish", [
    "Eating out", "Eat out", "Out for dinner", "Dinner out", "Night out with friends",
    "Restaurant", "Dinner at Grandma's", "Dinner at Grandma’s", "At the Smiths'",
    "At Sam's place", "Dinner at Mom and Dad's", "Takeaway",
])
def test_a_night_out_is_read_as_not_a_cook(dish):
    assert _weekly_plan.written_in_not_a_cook(dish) == "takeout"


@pytest.mark.parametrize("dish", [
    "Restaurant-style salsa", "Grandma's lasagna", "Potluck at the Smiths'",
    "Bring salad to Sam's", "Steak at its best", "Dinner at 6", "Chicken Skewers",
    # Review, 2026-11-01: named after a restaurant, or not somebody's table.
    "Takeaway-style chicken curry", "Restaurant-quality steak", "Better-than-restaurant pad thai",
    "Restaurant fried rice", "Steak at it's best", "Dinner out on the patio", "Supper out on the deck",
])
def test_a_dish_that_only_sounds_like_one_is_still_a_cook(dish):
    """GUARD: a restaurant-style dish, Grandma's recipe, and a dish carried
    to someone's table are all cooked here."""
    assert _weekly_plan.written_in_not_a_cook(dish) is None


@pytest.mark.parametrize("dish", ["Eating out", "Dinner at Grandma's"])
def test_eating_out_tonight_has_no_cook_card_no_nudge_and_no_groceries(dish):
    """CATCH, end to end: the card's own two examples."""
    _adult_opted_in()
    _plan(dish)

    moves = tools.today_moves(day=TODAY, now=_at(17, 5))
    assert [m for m in moves["moves"] if m["kind"] == "cook"] == []
    assert moves["featured"] is None
    assert any(dish in row["dishes"] for row in moves["day_meals"])

    assert tools.build_evening_nudge(_at(17, 30)) is None
    calls = []
    assert tools.run_evening_nudges_once(
        now_utc=_utc(17, 31), send=lambda m, t: calls.append(t) or {"status": "ok"}) == []
    assert calls == []
    assert tools.build_morning_text(_at(7, 0), link=False, parts=["meals", "start"]) == f"Tonight: {dish}."

    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ?",
                     (tools.household_id(),)).fetchone()["n"]
    conn.close()
    assert n == 0


def test_cook_mode_will_not_start_a_night_out():
    _adult_opted_in()
    _plan("Eating out")
    conn = get_conn()
    entry_id = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE date = ? AND slot = 'dinner'", (ISO_TODAY,)
    ).fetchone()["id"]
    conn.close()
    from app.tools import cooker
    assert cooker.start_cooking(entry_id)["status"] == "refused"
