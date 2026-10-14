"""
"Tonight: X. Still good?" is not asked about a dinner somebody has already
started cooking (defect hunt, 2026-10-14).

Found by using the app: tap "Start cooking" on Kitchen at 5:50, go back to
Today at 5:52, and the top card asks "Tonight: Weeknight Chili. Still good?"
with Yes and "Not quite? Tell me what you'd like" — the swap sheet and the
night off behind it — about the pot already on the stove. tonight_check
went quiet for a dinner marked cooked but never read cook_started_at, the
column the evening nudge already treats as "decided" (digest's
run_evening_nudges_once is silent once it is set).
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn

from test_tonight_still_good import AFTERNOON, TONIGHT, _full_week, _plan


def _set_started(day: str, value: str | None) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET cook_started_at = ? "
        "WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (value, tools.household_id(), day),
    )
    conn.commit()
    conn.close()


def test_a_dinner_already_started_is_not_asked_about():
    _full_week(_plan())
    _set_started(TONIGHT, f"{TONIGHT}T15:40:00")
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is False
    assert out["reason"] == "cooking"
    # Still knows what tonight is — it just doesn't ask.
    assert out["dinner"]["meal"] == "Chettinad-Style Pepper Chicken"


def test_mark_not_cooked_clears_the_start_and_the_card_asks_again():
    # "Mark not cooked" clears cook_started_at (cooker.check_off_meal), so a
    # cook put back is an undecided dinner again and the question returns.
    _full_week(_plan())
    _set_started(TONIGHT, f"{TONIGHT}T15:40:00")
    assert tools.tonight_check(now=AFTERNOON)["ask"] is False
    _set_started(TONIGHT, None)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["ask"] is True and out["reason"] == ""


def test_another_nights_start_does_not_silence_tonight():
    _full_week(_plan())
    tomorrow = (datetime.date.fromisoformat(TONIGHT) + datetime.timedelta(days=1)).isoformat()
    _set_started(tomorrow, f"{tomorrow}T15:40:00")
    assert tools.tonight_check(now=AFTERNOON)["ask"] is True
