"""
"Today" means from now.

Emily, 2026-09-27, planning a week that started that Sunday at 3:53 in
the afternoon: the draft planned Sunday's breakfast and lunch — both long
gone — and put the curry she asked to make "today" on Sunday lunch, with
Sunday dinner left open. Generation only ever knew today as a DATE, from
the server's clock, so every slot of the first day was expected.

This module is the clock half of the fix: which of today's meals have
already gone by on the HOUSEHOLD's clock (cooker.household_now — the
server runs UTC), using the same meal times the Now screen does
(moves._slot_time: breakfast 8:00, lunch 12:30, dinner from the
household's dinner window). A meal is past once its time is. The past
meals are written planned_empty with ALREADY_PAST_CONSTRAINT
(agent._finish_week_slots), which the gap audit counts as present and
every fill pass leaves alone.

`now` is injectable everywhere so a test can say "Sunday, 3:53pm" without
freezing anything; left unset it is the household's now.
"""
from __future__ import annotations

import logging
from datetime import datetime, time

logger = logging.getLogger("home_manager")

MEALS = ("breakfast", "lunch", "dinner")

ALREADY_PAST_CONSTRAINT = "already_past"
ALREADY_PAST_REASON = "Not planned — this meal had already gone by."


def household_now() -> datetime:
    """Now on the household's clock; the server's now if that can't be
    read (a wrong hour beats a failed plan)."""
    from . import cooker as _cooker

    try:
        return _cooker.household_now()
    except Exception:
        logger.exception("Couldn't read the household's clock; using the server's")
        return datetime.now()


def meal_time(slot: str) -> time:
    """When this household eats `slot` — the Now screen's own clock."""
    from . import moves as _moves

    return _moves._slot_time(slot, _moves._dinner_clock())


def past_meals(dates: list[str], now: datetime | None = None) -> list[dict]:
    """[{"date", "slot"}] for every meal of TODAY in `dates` whose time has
    already come — breakfast at 9am, breakfast and lunch at 3:53pm. Empty
    when today isn't in the period (a plan for next week has no past)."""
    now = now or household_now()
    today = now.date().isoformat()
    if today not in dates:
        return []
    return [{"date": today, "slot": s} for s in MEALS if now.time() >= meal_time(s)]


def first_meal_ahead(now: datetime | None = None) -> str | None:
    """The first of today's meals still to come — "dinner" at 3:53pm — or
    None once dinner has gone by too."""
    now = now or household_now()
    return next((s for s in MEALS if now.time() < meal_time(s)), None)


def describe(now: datetime | None = None) -> str:
    """"15:53" — the time of day the generation context carries beside
    today's date, so the model is told the same thing this module knows."""
    now = now or household_now()
    return now.strftime("%H:%M")
