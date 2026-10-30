"""
"Take the night off" on a dinner that was never planned doesn't say
nobody's home (defect hunt, 2026-10-28).

Found on a usual week with Wednesday dinner "Don't plan": chat's
take_the_night_off for that Wednesday answered `already_reason: 'away'`,
said "Nobody’s home tonight anyway." — to a household at home — and the
Today sheet's fallback reads 'away' the same way. Such a night is
`not_planned` now, in the Move sheet's words for it; a real nobody-home
night and a night already called off keep theirs.
"""
from __future__ import annotations

import datetime

from app import tools
from conftest import household_today

MON = household_today() - datetime.timedelta(days=household_today().weekday()) + datetime.timedelta(days=7)
WEEK = MON.isoformat()
DATES = tools._week_dates(WEEK)
WED, THU, FRI = DATES[2], DATES[3], DATES[4]


def _week():
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for i, day in enumerate(DATES):
        if day in (WED, THU):
            continue
        name = f"Dish {i}"
        tools.add_recipe(name, ingredients=[{"item": f"{name} onion", "qty": "1", "category": "produce"}],
                         default_servings=4)
        tools.plan_meal(day, name, slot="dinner", weekly_plan_id=plan)
    from app.tools import usual_week
    usual_week.settle_off_slots(plan, [{"date": WED, "slot": "dinner"}])
    tools.plan_slot_empty(plan, THU, "dinner", reason="Out at the cottage.")
    tools.approve_weekly_plan(plan)
    return plan


def test_a_usual_week_night_off_is_no_dinner_planned_not_nobody_home():
    _week()
    out = tools.tonight_night_off(WED)
    assert out["already"] is True
    assert out["already_reason"] == "not_planned"
    assert "Nobody" not in out["said"]
    assert out["said"] == "There’s no dinner planned tonight anyway."


def test_a_real_away_night_and_a_night_already_off_keep_their_words():
    _week()
    away = tools.tonight_night_off(THU)
    assert away["already_reason"] == "away" and away["said"] == "Nobody’s home tonight anyway."
    first = tools.tonight_night_off(FRI)
    assert first["status"] == "night_off"
    again = tools.tonight_night_off(FRI)
    assert again["already_reason"] == "night_off" and again["said"] == "Tonight’s already off."
