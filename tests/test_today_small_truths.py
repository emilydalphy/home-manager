"""
Today and Cook small truths (overnight, 2026-10-21). Three things the demo
household's Today said with the clock frozen that were no longer true:

1. At 9pm, "Tonight: Weeknight Chili. Still good?" — tonight_check only
   checked it was the afternoon. It now goes quiet ('evening_over') past
   the evening nudge's late window, the app's one answer to "the night
   has moved on".
2. At 6:31, with the chili's things still unbought and the cook not
   started, the shop move vanished (`now <= at`) while the cook card
   stayed. It now stays, "still to do", for a cook of today's still
   waiting on the list.
3. Skipping yesterday's double-batch chili on a Thursday toasted
   "Weeknight Chili moved to Thursday." — on Thursday. It says "tonight".

Every test here seeds off the HOUSEHOLD's clock (conftest.household_today).
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import moves, weekly_plan

from test_skipped_cook_moves_on import _chain, _plan as _skip_plan


def _at(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hour, minute))


def _tonight_planned(**plan_kwargs) -> datetime.date:
    """Today's dinner, a real dish, its ingredient on the list (ledgered)."""
    today = household_today()
    plan = tools.create_weekly_plan((today - datetime.timedelta(days=1)).isoformat(),
                                    day_count=3)["weekly_plan_id"]
    tools.add_recipe("Weeknight Chili", ingredients=[{"item": "Ground beef", "qty": "1 lb",
                                                      "category": "meat-seafood"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=4)
    tools.plan_meal(today.isoformat(), "Weeknight Chili", slot="dinner", weekly_plan_id=plan,
                    **plan_kwargs)
    return today


# ---------- 1. "Still good?" closes with the evening ----------

def test_still_good_is_not_asked_at_nine_at_night():
    today = _tonight_planned()
    late = tools.tonight_check(now=_at(today, 21))
    assert late["ask"] is False
    assert late["reason"] == "evening_over"
    assert late["dinner"]["meal"] == "Weeknight Chili", "it still knows what tonight is"
    # The afternoon is untouched.
    assert tools.tonight_check(now=_at(today, 15, 50))["ask"] is True


def test_still_good_is_asked_through_dinner_time_and_closes_with_the_nudge():
    # No dinner_window: dinner lands 6:30 (moves' default), the nudge goes at
    # 5:30 and its late window closes at 7:00. At 6:45 a household running
    # late can still say "we're going out"; at 7:01 the night has moved on.
    today = _tonight_planned()
    assert tools.tonight_check(now=_at(today, 18, 45))["ask"] is True
    assert tools.tonight_check(now=_at(today, 19, 0))["ask"] is True
    assert tools.tonight_check(now=_at(today, 19, 1))["reason"] == "evening_over"


def test_a_later_dinner_window_keeps_the_question_later():
    today = _tonight_planned()
    tools.set_dinner_window("later")  # nudge 7:00, closes 8:30; dinner lands 8:00
    assert tools.tonight_check(now=_at(today, 20, 15))["ask"] is True
    assert tools.tonight_check(now=_at(today, 20, 31))["reason"] == "evening_over"


# ---------- 2. The shop move stays while tonight still needs it ----------

def _shop(today: datetime.date, now: datetime.datetime) -> dict | None:
    payload = moves.today_moves(day=today.isoformat(), now=now)
    hits = [m for m in payload["moves"] if m["kind"] == "shop"]
    return hits[0] if hits else None


def test_the_shop_move_stays_past_dinner_time_while_tonight_still_needs_it():
    today = _tonight_planned(add_ingredients_to_grocery_list=True)
    before = _shop(today, _at(today, 15))
    assert before is not None and before["title"] == "Shop for tonight"

    after = _shop(today, _at(today, 18, 31))
    assert after is not None, "the cook card stays, so the shop it waits on does too"
    assert after["title"] == "Shop for tonight"
    assert after["overdue"] is True
    assert after["detail"] == "1 item · still to do"
    payload = moves.today_moves(day=today.isoformat(), now=_at(today, 18, 31))
    assert [m["kind"] for m in payload["moves"] if m["slot"] == "dinner"] == ["cook"]


def test_a_passed_cook_does_not_take_the_deadline_from_one_still_ahead():
    # Breakfast at 8 never ticked, its berries still on the list; tonight's chili
    # waits on beef. At noon the deadline is tonight's, not "still to do".
    today = _tonight_planned(add_ingredients_to_grocery_list=True)
    conn = get_conn()
    plan = conn.execute("SELECT weekly_plan_id FROM meal_plan_entries WHERE date = ? AND slot = 'dinner'",
                        (today.isoformat(),)).fetchone()[0]
    conn.close()
    tools.add_recipe("Overnight Oats", ingredients=[{"item": "Blueberries", "qty": "2 pints", "category": "produce"}],
                     prep_time_minutes=5, cook_time_minutes=0, default_servings=2)
    tools.plan_meal(today.isoformat(), "Overnight Oats", slot="breakfast", weekly_plan_id=plan,
                    add_ingredients_to_grocery_list=True)
    noon = _shop(today, _at(today, 12))
    assert noon["title"] == "Shop for tonight"
    assert noon["overdue"] is False and "by " in noon["detail"]


def test_a_started_cook_past_dinner_time_still_has_no_shop_deadline():
    today = _tonight_planned(add_ingredients_to_grocery_list=True)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cook_started_at = ? WHERE date = ? AND slot = 'dinner'",
                 (f"{today.isoformat()}T18:00:00", today.isoformat()))
    conn.commit()
    conn.close()
    assert _shop(today, _at(today, 18, 31)) is None


# ---------- 3. "moved to tonight", not "moved to Thursday" ----------

def test_skipping_yesterdays_double_cook_says_it_moved_to_tonight():
    plan, today, yday = _skip_plan()
    cook, _ = _chain(plan, yday, [today.isoformat()])
    out = tools.answer_yesterday(cook, "skipped", today)
    assert out["moved_to"]["date"] == today.isoformat()
    assert out["moved_to"]["label"] == "tonight"
    # The stored reason is read on later days, so it keeps the weekday.
    conn = get_conn()
    reason = conn.execute(
        "SELECT reasoning FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan, yday)).fetchone()[0]
    conn.close()
    assert today.strftime("%A") in reason and "tonight" not in reason


def test_a_move_onto_a_later_night_still_names_its_weekday():
    plan, today, yday = _skip_plan()
    tomorrow = today + datetime.timedelta(days=1)
    target = {"date": tomorrow.isoformat(), "slot": "dinner"}
    assert weekly_plan.fed_night_label(target, today=today.isoformat()) == tomorrow.strftime("%A")
    lunch = {"date": today.isoformat(), "slot": "lunch"}
    assert weekly_plan.fed_night_label(lunch, today=today.isoformat()) == "today’s lunch"
