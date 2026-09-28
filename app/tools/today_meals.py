"""
"Today" means from now.

Emily, 2026-09-27, planning a week that started that Sunday at 3:53 in
the afternoon: the draft planned Sunday's breakfast and lunch — both long
gone — and put the curry she asked to make "today" on Sunday lunch, with
Sunday dinner left open. Generation only ever knew today as a DATE, from
the server's clock, so every slot of the first day was expected.

This module is the clock half of the fix: which of today's meals have
already gone by on the HOUSEHOLD's clock (cooker.household_now — the
server runs UTC). A meal is gone once its WINDOW has closed, not the
moment it starts — someone planning at 6:45pm hasn't eaten dinner yet
(review, 2026-09-27):

  * breakfast until BREAKFAST_ENDS (10:00),
  * lunch until LUNCH_ENDS (14:00),
  * dinner until the end of the household's dinner window
    (DINNER_ENDS_BY_WINDOW, off rhythm.dinner_window), or — with no
    window answered — DINNER_GRACE_HOURS after the Now screen's own
    dinner time (moves._slot_time, 18:30 by default: so 20:30).

The gone meals are written planned_empty with ALREADY_PAST_CONSTRAINT
(agent._finish_week_slots), which the gap audit counts as present and
every fill pass leaves alone — EXCEPT a meal the household's own words
name ("tonight", "today", "lunch today"): that one is planned whatever the
clock says, and the draft says it was late (draft_flags).

`now` is injectable everywhere so a test can say "Sunday, 3:53pm" without
freezing anything; left unset it is the household's now.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

logger = logging.getLogger("home_manager")

MEALS = ("breakfast", "lunch", "dinner")

ALREADY_PAST_CONSTRAINT = "already_past"
ALREADY_PAST_REASON = "Not planned — this meal had already gone by."

BREAKFAST_ENDS = time(10, 0)
LUNCH_ENDS = time(14, 0)
# The end of each answer to "When does dinner usually happen?"
# (rhythm.dinner_window). "5–6ish" runs to half past six; "6–8" to eight;
# "later" to half past nine. "all_over" and no answer fall back to the
# dinner time plus DINNER_GRACE_HOURS.
DINNER_ENDS_BY_WINDOW = {
    "5_6ish": time(18, 30),
    "6_8": time(20, 0),
    "later": time(21, 30),
}
DINNER_GRACE_HOURS = 2


def household_now() -> datetime:
    """Now on the household's clock; the server's now if that can't be
    read (a wrong hour beats a failed plan)."""
    from . import cooker as _cooker

    try:
        return _cooker.household_now()
    except Exception:
        logger.exception("Couldn't read the household's clock; using the server's")
        return datetime.now()


def _dinner_window() -> str | None:
    from . import rhythm as _rhythm

    try:
        return _rhythm.get_household_rhythm().get("dinner_window")
    except Exception:
        return None


def meal_ends(slot: str) -> time:
    """When this household's `slot` is over for the day."""
    if slot == "breakfast":
        return BREAKFAST_ENDS
    if slot == "lunch":
        return LUNCH_ENDS
    ends = DINNER_ENDS_BY_WINDOW.get(_dinner_window() or "")
    if ends:
        return ends
    from . import moves as _moves

    start = datetime.combine(datetime(2000, 1, 1).date(), _moves._slot_time("dinner", _moves._dinner_clock()))
    return min((start + timedelta(hours=DINNER_GRACE_HOURS)).time(), time(23, 59))


def has_gone(slot: str, now: datetime | None = None) -> bool:
    now = now or household_now()
    return now.time() >= meal_ends(slot)


def past_meals(dates: list[str], now: datetime | None = None, keep: set | None = None) -> list[dict]:
    """[{"date", "slot"}] for every meal of TODAY in `dates` that has
    already gone by — breakfast and lunch at 3:53pm, nothing at 9am.
    `keep` is the (date, slot) of any meal the household's words name,
    which is never taken away. Empty when today isn't in the period."""
    now = now or household_now()
    today = now.date().isoformat()
    if today not in dates:
        return []
    return [{"date": today, "slot": s} for s in MEALS
            if has_gone(s, now) and (today, s) not in (keep or set())]


def first_meal_ahead(now: datetime | None = None) -> str | None:
    """The first of today's meals not yet over — "dinner" at 3:53pm and
    still at 6:45pm — or None once dinner has gone by too."""
    now = now or household_now()
    return next((s for s in MEALS if not has_gone(s, now)), None)


def clear_snacks_of_gone_days(plan_id: int, gone_by_day: dict[str, set]) -> list[str]:
    """
    Snacks have no clock of their own, so a day's snacks are planned while
    any of its meals is still ahead, and go only when every one of them has
    gone by (integration review, 2026-09-27). `gone_by_day` is {date: the
    meals written already-past}. Clears those days' snacks and returns the
    days, for the snack passes to leave alone the way they leave a day the
    household left out.
    """
    from . import weekly_plan as _weekly_plan

    days = sorted(d for d, slots in gone_by_day.items() if set(slots) >= set(MEALS))
    for day in days:
        _weekly_plan.clear_plan_slot(plan_id, day, "snack")
    return days


def describe(now: datetime | None = None) -> str:
    """"15:53" — the time of day the generation context carries beside
    today's date, so the model is told the same thing this module knows."""
    now = now or household_now()
    return now.strftime("%H:%M")
