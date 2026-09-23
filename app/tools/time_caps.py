"""
How long one meal's cooking may take, in minutes — or None for no cap.

Emily, 2026-09-23: "Short on time" means dinner on the table in 30
minutes or less, prep included, never more; and every Monday-to-Friday
lunch cooked that day is 20 minutes or less. Her words on the second:
"if the meal is on a prep day, it doesn't need to be a 20min meal… if Im
prepping chili for lunches, that's a great meal to just reheat, but if
Im cooking on the day, then it needs to be 20 mins or less".

Before this the cap was keyed by DATE only, so a lunch inherited that
evening's dinner cap, and a `rush` tag returned the rush number before
the household's own weeknight cap was looked at — a weeknight cap of 15
got LOOSENED to the rush number on a rush night. One helper now, read by
the generator's plate and variety passes (agent.py), the swap sheet and
its cap gate (swap_in_place.py) and the quality check (plan_quality.py),
so no two of them can disagree about a slot.

The rules, per slot:

- dinner: `rush` is RUSH_MAX_MINUTES, or the weeknight cap when that is
  lower on a Monday-Friday; `unrushed` lifts every cap; otherwise
  Monday-Friday gets the household's weeknight_max_minutes (0 means
  none) and the weekend none.
- lunch: a Monday-Friday lunch cooked that day is WEEKDAY_LUNCH_MAX_MINUTES.
  A lunch that is part of a leftovers chain (it reheats an earlier cook,
  or it is the batch cook that feeds later meals), or one on a household
  prep day, has no cap. Weekend lunches have none. Night tags don't
  touch lunch: "short on time" was asked about dinner.
- breakfast and snack: no cap.

A dish's minutes are the recipe's prep + cook, as the model estimated
them; unknown minutes are let through by every reader, and sides aren't
counted.
"""
from __future__ import annotations

import datetime

# Emily, 2026-09-23: "Short on time" is dinner on the table in 30 minutes
# or less, prep included, never more (it was 20). Dinner only. On a
# weeknight where the household's own weeknight_max_minutes is lower, that
# lower number is the cap. Re-exported by week_intake, where callers have
# always read it.
RUSH_MAX_MINUTES = 30

# Every Monday-Friday lunch that is cooked that day (Emily, 2026-09-23).
# A lunch that reheats an earlier cook, the batch cook that feeds it, and
# a lunch on a prep day have no cap: "if Im prepping chili for lunches,
# that's a great meal to just reheat, but if Im cooking on the day, then
# it needs to be 20 mins or less". Weekend lunches have no fixed cap.
WEEKDAY_LUNCH_MAX_MINUTES = 20

_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _weekday(meal_date: str) -> int | None:
    try:
        return datetime.date.fromisoformat(meal_date).weekday()
    except (TypeError, ValueError):
        return None


def prep_weekdays(memory: dict | None) -> set[str]:
    """The household's standing prep days, as lowercase weekday names,
    from household memory's rhythm.prep_days. Empty when none are set."""
    days = (((memory or {}).get("rhythm") or {}).get("prep_days")) or []
    out = set()
    for day in days:
        name = day.get("weekday") if isinstance(day, dict) else day
        if isinstance(name, str) and name.strip().lower() in _WEEKDAY_NAMES:
            out.add(name.strip().lower())
    return out


def minutes_cap(
    meal_date: str,
    slot: str | None,
    tags: list[str] | None,
    memory: dict | None,
    is_leftovers: bool = False,
) -> int | None:
    """
    The real cap on this meal's prep + cook, or None. See the module
    docstring for the rules. `tags` are that date's night tags; `memory`
    is household memory (weeknight_max_minutes, rhythm.prep_days);
    `is_leftovers` is True for either end of a leftovers chain — the
    reheat, or the batch cook that feeds it.
    """
    slot = slot or "dinner"
    tags = tags or []
    memory = memory or {}
    weekday = _weekday(meal_date)
    if weekday is None:
        return None
    if slot == "dinner":
        weeknight_cap = memory.get("weeknight_max_minutes") or 0
        if "rush" in tags:
            # A stricter weeknight cap stays the cap on a rush weeknight:
            # the tag only ever tightens a night. The weeknight cap is
            # Monday-Friday only, so a rush Saturday is the rush number.
            if weeknight_cap and weekday < 5:
                return min(RUSH_MAX_MINUTES, weeknight_cap)
            return RUSH_MAX_MINUTES
        if "unrushed" in tags:
            return None
        return weeknight_cap if (weeknight_cap and weekday < 5) else None
    if slot == "lunch":
        if weekday >= 5 or is_leftovers:
            return None
        if _WEEKDAY_NAMES[weekday] in prep_weekdays(memory):
            return None
        return WEEKDAY_LUNCH_MAX_MINUTES
    return None


def caps_for_slot(caps: dict | None, slot: str) -> dict[str, int | None]:
    """
    The {date: minutes} view of one slot, for code that looks caps up by
    date alone (meal_variety's surplus and repeat picks). An old
    date-keyed dict comes back as it was.
    """
    caps = caps or {}
    if not any(isinstance(k, tuple) for k in caps):
        return dict(caps)
    return {k[0]: v for k, v in caps.items() if isinstance(k, tuple) and k[1] == slot}
