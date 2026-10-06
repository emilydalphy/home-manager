"""
The household's usual week: which meals get planned on which days, who is
eating them, when they prep, and how many different dishes each meal
rotates (Loop Board "Your week: meals × days × who's eating, prep day, and
variety drive every plan", 2026-09-30).

WHAT IS STORED, AND WHERE. Four answers, each in the one place the app
already reads that kind of fact from, plus one new column for what had no
home:

  - The grid — breakfast, lunch, dinner × Monday–Sunday, each "everyone",
    "off" (don't plan) or a list of member ids (just those people) — and
    the variety choice per meal live in meal_preferences.usual_week_json,
    written only by save_usual_week. '' means never answered.
  - The variety NUMBER is also written into the columns every planner pass
    already reads (breakfasts/lunches/dinners_per_week, with
    meal_counts_set), so there is one number and every existing reader
    — the prompt, the count pass, the fold, the opener — sees it.
  - Snacks a day is meal_preferences.snacks_per_day, as it always was.
  - The prep answer is the rhythm fact prep_days (rhythm.set_prep_days),
    with "About an hour" = 60 minutes and "A longer stretch" = 120 — the
    same minutes the Settings prep chips and rhythm.prep_minutes_label use.

EXISTING HOUSEHOLDS. A household that has never saved this answer has no
stored grid, and get_usual_week DERIVES one from what they already have
on every read: every day on for "everyone", except a meal they asked for
none of (a count of 0), which is off every day; the variety number is
their stored count with no choice key. Nothing is written for them at
startup, so their planning cannot drift: until they save an answer the
generation passes below find nothing to do (no off slots, no subsets, no
lunch answer, the old fill-up rule), and the model is handed exactly the
inputs it was handed before this existed. tests/test_usual_week.py pins
that.

THE MAPPING from what a person taps to a number lives in VARIETY_CHOICES
and nowhere else.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import date

from ..db import get_conn
from ._shared import IN_MEALS_SQL, household_id

logger = logging.getLogger("home_manager")

MEALS = ("breakfast", "lunch", "dinner")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
EVERYONE = "everyone"
OFF = "off"

COUNT_COLUMNS = {"breakfast": "breakfasts_per_week", "lunch": "lunches_per_week", "dinner": "dinners_per_week"}

# ---------- the mapping: one place, easy to change ----------
#
# Emily, 2026-09-30. A number is that many different dishes a week.
# DAYS means "as many as the days that meal is on in the grid". LEFTOVERS
# means lunch is last night's dinner: the lunches reheat the dinner before
# them (the weekday-lunches "leftovers" kind), and the lunch slot keeps
# LEFTOVER_LUNCH_DISHES of its own for a lunch no dinner feeds. Every
# number is clamped to the days the meal is on.
DAYS = "days"
LEFTOVERS = "leftovers"
VARIETY_CHOICES: dict[str, dict[str, int | str]] = {
    "breakfast": {
        "go_to_or_two": 2,          # "A go-to or two"
        "few_in_rotation": 3,       # "A few in rotation"
        "new_every_day": DAYS,      # "Something new every morning"
    },
    "lunch": {
        "last_nights_dinner": LEFTOVERS,  # "Last night's dinner"
        "meal_prep_ahead": 2,       # "Meal prep ahead" — made on the prep day; needs one
        "few_in_rotation": 3,       # "A few in rotation"
        "new_every_day": DAYS,      # "Something new every day"
    },
    "dinner": {
        "cook_big_eat_twice": 2,    # "Cook big, eat twice"
        "few_in_rotation": 4,       # "A few in rotation"
        "new_every_day": DAYS,      # "Something new every night"
    },
}
LEFTOVER_LUNCH_DISHES = 1
# The lunch choice that needs a prep day to mean anything.
NEEDS_PREP_DAY = {("lunch", "meal_prep_ahead")}

# What a stored "Meal prep ahead" lunch becomes when the prep days are
# taken away (there is no session left to prep in).
PREP_REMOVED_LUNCH_CHOICE = "few_in_rotation"

# The prep answer's two lengths, as minutes on the rhythm fact.
PREP_LENGTHS = {"hour": 60, "longer": 120}
SNACKS_PER_DAY_MAX = 3

# The derived_from constraint and the reason on a slot the usual week has
# off, written planned_empty by agent._finish_week_slots.
OFF_CONSTRAINT = "usual_week_off"
OFF_REASON = "Not planned — you don’t plan this meal on this day."
# slot_attendance.source for a row written from the grid's "just these
# people", so a later save can rewrite or remove its own rows and never
# anybody else's (a trip, the day sheet, the guests chip).
ATTENDANCE_SOURCE = "usual_week"
# The week's own attendance rows the grid's subset is applied UNDER (their
# base was "everyone") — the day sheet, a single toggle, the guests chip.
WEEK_OWN_SOURCES = ("sheet", "toggle", "guests")


# ---------- reading ----------

def _prefs_row(conn):
    return conn.execute(
        "SELECT breakfasts_per_week, lunches_per_week, dinners_per_week, meal_counts_set, "
        "snacks_per_day, usual_week_json FROM meal_preferences WHERE household_id = ?",
        (household_id(),),
    ).fetchone()


def _members(conn) -> list[dict]:
    return [
        {"id": r["id"], "name": r["name"]}
        for r in conn.execute(
            f"SELECT id, name FROM members WHERE household_id = ? AND {IN_MEALS_SQL} ORDER BY id",
            (household_id(),),
        ).fetchall()
    ]


def _stored(row) -> dict | None:
    if not row:
        return None
    try:
        value = json.loads(row["usual_week_json"] or "")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("grid"), dict) else None


def _counts(row) -> dict[str, int]:
    return {m: (int(row[c]) if row else 7) for m, c in COUNT_COLUMNS.items()}


def _derived_grid(counts: dict[str, int]) -> dict:
    return {m: {d: (OFF if counts[m] == 0 else EVERYONE) for d in WEEKDAYS} for m in MEALS}


def _clean_cell(value, member_ids: set[int]):
    """A stored cell read back against today's members: a subset that has
    lost everyone it named (they left the household) reads as everyone."""
    if value == OFF:
        return OFF
    if isinstance(value, list):
        ids = [i for i in value if i in member_ids]
        if ids and len(ids) < len(member_ids):
            return ids
    return EVERYONE


def days_on(grid: dict, meal: str) -> list[str]:
    return [d for d in WEEKDAYS if (grid.get(meal) or {}).get(d, EVERYONE) != OFF]


def resolve_dishes(meal: str, choice: str | None, on_count: int, fallback: int | None = None) -> int:
    """The number a variety choice stands for, clamped to the days on."""
    if on_count <= 0:
        return 0
    value = VARIETY_CHOICES.get(meal, {}).get(choice) if choice else None
    if value == DAYS:
        n = on_count
    elif value == LEFTOVERS:
        n = LEFTOVER_LUNCH_DISHES
    elif isinstance(value, int):
        n = value
    else:
        n = fallback if fallback is not None else on_count
    return max(1, min(int(n), on_count))


def _prep_answer() -> dict:
    from . import rhythm as _rhythm

    days = _rhythm.get_household_rhythm().get("prep_days") or []
    minutes = [d.get("minutes") for d in days if d.get("minutes")]
    length = None
    if minutes:
        length = "hour" if max(minutes) <= 75 else "longer"
    return {"days": [d["weekday"] for d in days], "length": length}


def _state(conn) -> dict:
    """The usual week as generation and the API read it (see get_usual_week)."""
    row = _prefs_row(conn)
    members = _members(conn)
    ids = {m["id"] for m in members}
    counts = _counts(row)
    stored = _stored(row)
    prep_days = _prep_answer()["days"] if stored else []
    if stored:
        grid = {m: {d: _clean_cell((stored["grid"].get(m) or {}).get(d, EVERYONE), ids) for d in WEEKDAYS}
                for m in MEALS}
    else:
        grid = _derived_grid(counts)
    variety = {}
    stored_variety = (stored or {}).get("variety") or {}
    for m in MEALS:
        on = len(days_on(grid, m))
        choice = (stored_variety.get(m) or {}).get("choice")
        if choice not in VARIETY_CHOICES[m]:
            choice = None
        dishes = counts[m]
        # A number changed somewhere else since (chat's edit_preference, the
        # old Different dishes steppers) wins, and the choice it no longer
        # matches is dropped rather than shown as what they picked.
        if choice is not None and resolve_dishes(m, choice, on) != dishes:
            choice = None
        if (m, choice) in NEEDS_PREP_DAY and not prep_days:
            choice = None
        variety[m] = {"choice": choice, "dishes": dishes, "days_on": on}
    return {
        "answered": stored is not None,
        "grid": grid,
        "variety": variety,
        "snacks_per_day": int(row["snacks_per_day"]) if row else 2,
        "members": members,
        "meal_counts_set": bool(row["meal_counts_set"]) if row else False,
    }


def _public(state: dict) -> dict:
    return {
        "answered": state["answered"],
        "weekdays": list(WEEKDAYS),
        "members": state["members"],
        "grid": state["grid"],
        "snacks_per_day": state["snacks_per_day"],
        "prep": _prep_answer(),
        "variety": state["variety"],
        "variety_choices": {
            m: [
                {"key": k, "dishes": v, **({"needs_prep_day": True} if (m, k) in NEEDS_PREP_DAY else {})}
                for k, v in VARIETY_CHOICES[m].items()
            ]
            for m in MEALS
        },
    }


def get_usual_week() -> dict:
    """
    The household's usual week, for onboarding and Settings. See the module
    docstring for where each part lives and what `answered: false` means
    (derived from today's settings, nothing stored yet).
    """
    conn = get_conn()
    try:
        state = _state(conn)
    finally:
        conn.close()
    return _public(state)


# ---------- writing ----------

def _parse_grid(grid, members: list[dict], current: dict, pending_names: list[str] | None = None) -> dict:
    """The grid being saved, from the stored one and the cells sent.
    `pending_names` (onboarding's validate-first pass) are people about to
    be added: a cell naming one is accepted as a subset without an id."""
    from . import attendance as _attendance

    if not isinstance(grid, dict):
        raise ValueError("grid must be an object of meal → weekday → who.")
    all_ids = [m["id"] for m in members]
    pending = {str(n).strip().lower() for n in (pending_names or []) if str(n).strip()}
    known = {m["name"].strip().lower() for m in members}
    out = {m: dict(current[m]) for m in MEALS}
    for meal, days in grid.items():
        if meal not in MEALS:
            raise ValueError(f"grid meal must be one of {', '.join(MEALS)}, not {meal!r}.")
        if not isinstance(days, dict):
            raise ValueError(f"grid.{meal} must be an object of weekday → who.")
        for day, who in days.items():
            key = str(day).strip().lower()
            if key not in WEEKDAYS:
                raise ValueError(f"{day!r} isn't a day of the week.")
            if who in (EVERYONE, OFF):
                out[meal][key] = who
                continue
            if not isinstance(who, list) or not who:
                raise ValueError(
                    f"{meal} on {key} must be \"everyone\", \"off\", or a list of the people eating it."
                )
            if pending and any(isinstance(w, str) and w.strip().lower() in pending - known for w in who):
                # Validation only: someone not added yet. Checked for real
                # on the save that follows the answers.
                rest = [w for w in who if not (isinstance(w, str) and w.strip().lower() in pending - known)]
                if rest:
                    _attendance.resolve_member_ids(rest)
                out[meal][key] = list(who)
                continue
            ids = _attendance.resolve_member_ids(who)
            out[meal][key] = EVERYONE if set(ids) >= set(all_ids) else ids
    return out


# One save at a time per household: a save reads the stored usual week,
# changes the cells it was sent and writes the whole thing back, so two
# partial saves racing each other would otherwise lose one's cells.
_SAVE_LOCKS: dict[int, threading.RLock] = {}
_SAVE_LOCKS_GUARD = threading.Lock()


def _save_lock() -> threading.RLock:
    with _SAVE_LOCKS_GUARD:
        return _SAVE_LOCKS.setdefault(household_id(), threading.RLock())


def _prepare(grid, variety, snacks_per_day, prep, pending_names=None) -> dict:
    """Everything a save would write, worked out and checked — raises
    ValueError on anything invalid, and writes nothing."""
    from . import rhythm as _rhythm

    conn = get_conn()
    try:
        state = _state(conn)
    finally:
        conn.close()

    new_grid = (_parse_grid(grid, state["members"], state["grid"], pending_names)
                if grid is not None else state["grid"])

    choices = {m: state["variety"][m]["choice"] for m in MEALS}
    if variety is not None:
        if not isinstance(variety, dict):
            raise ValueError("variety must be an object of meal → choice.")
        for meal, choice in variety.items():
            if meal not in MEALS:
                raise ValueError(f"variety meal must be one of {', '.join(MEALS)}, not {meal!r}.")
            if isinstance(choice, dict):
                choice = choice.get("choice")
            if choice is not None and choice not in VARIETY_CHOICES[meal]:
                raise ValueError(
                    f"{meal} variety must be one of {', '.join(VARIETY_CHOICES[meal])}, not {choice!r}."
                )
            choices[meal] = choice

    if snacks_per_day is not None:
        try:
            snacks_per_day = int(snacks_per_day)
        except (TypeError, ValueError):
            raise ValueError(f"snacks_per_day must be 0 to {SNACKS_PER_DAY_MAX}.")
        if not 0 <= snacks_per_day <= SNACKS_PER_DAY_MAX:
            raise ValueError(f"snacks_per_day must be 0 to {SNACKS_PER_DAY_MAX}.")

    prep_days = None
    if prep is not None:
        if not isinstance(prep, dict):
            raise ValueError("prep must be an object with days and length.")
        raw_days = prep.get("days") or []
        length = prep.get("length")
        if raw_days and length is not None and length not in PREP_LENGTHS:
            raise ValueError(f"prep length must be one of {', '.join(PREP_LENGTHS)}, or null.")
        minutes = PREP_LENGTHS.get(length) if length else None
        prep_days = _rhythm._normalize_prep_days([{"weekday": d, "minutes": minutes} for d in raw_days])
    has_prep_day = bool(prep_days) if prep_days is not None else bool(_prep_answer()["days"])
    lunch_sent = variety is not None and "lunch" in variety
    if choices["lunch"] == "meal_prep_ahead" and not has_prep_day and days_on(new_grid, "lunch"):
        if lunch_sent:
            raise ValueError("“Meal prep ahead” needs a prep day — pick the day you prep, or another lunch choice.")
        # The prep day was taken away under a stored "Meal prep ahead": the
        # lunch goes back to a few in rotation rather than naming a prep
        # session that no longer exists.
        choices["lunch"] = PREP_REMOVED_LUNCH_CHOICE

    counts = {}
    answered_any = False
    for meal in MEALS:
        on = len(days_on(new_grid, meal))
        current = state["variety"][meal]["dishes"]
        if choices[meal] is not None:
            counts[meal] = resolve_dishes(meal, choices[meal], on)
            answered_any = True
        elif on == 0:
            counts[meal] = 0
        elif current == 0:
            # Back on without a choice: the unanswered default, every day
            # it's on — not a floor (fill_up_allowed reads it that way).
            counts[meal] = on
        else:
            counts[meal] = min(current, on)
    return {
        "stored": {"grid": new_grid, "variety": {m: {"choice": choices[m], "dishes": counts[m]} for m in MEALS}},
        "counts": counts, "answered_any": answered_any,
        "snacks_per_day": snacks_per_day, "prep_days": prep_days,
    }


def validate_usual_week(grid=None, variety=None, snacks_per_day=None, prep=None,
                        pending_names: list[str] | None = None) -> None:
    """Check a usual-week answer without writing anything (onboarding asks
    this BEFORE it saves the rest of its answers). Raises ValueError."""
    _prepare(grid, variety, snacks_per_day, prep, pending_names)


def save_usual_week(
    grid: dict | None = None,
    variety: dict | None = None,
    snacks_per_day: int | None = None,
    prep: dict | None = None,
    source: str = "settings",
) -> dict:
    """
    Save any part of the usual week — omitted parts stay as they are.

    grid: {meal: {weekday: "everyone" | "off" | [member ids or names]}},
      partial is fine (only the cells sent change).
    variety: {meal: choice key from VARIETY_CHOICES} (or {meal: {"choice": key}}).
    snacks_per_day: 0-3.
    prep: {"days": [weekday, ...], "length": "hour" | "longer" | null};
      {"days": []} is "we don't prep ahead".

    The variety number is resolved against the grid being saved and
    written into the per-week count columns; a choice sets meal_counts_set
    (the number is theirs). A meal off every day is 0 — "none, thanks" —
    which every existing pass already honours. Raises ValueError on
    anything invalid, before anything is written. One save at a time per
    household, and everything it writes (the grid, the counts, snacks, the
    prep days) is one transaction.
    """
    from . import household as _household
    from . import preferences as _preferences
    from . import rhythm as _rhythm

    with _save_lock():
        plan = _prepare(grid, variety, snacks_per_day, prep)
        counts = plan["counts"]
        conn = get_conn()
        try:
            conn.execute("INSERT OR IGNORE INTO meal_preferences (household_id) VALUES (?)", (household_id(),))
            conn.execute(
                "UPDATE meal_preferences SET usual_week_json = ?, breakfasts_per_week = ?, lunches_per_week = ?, "
                "dinners_per_week = ?, meal_counts_set = CASE WHEN ? THEN 1 ELSE meal_counts_set END, "
                "updated_at = datetime('now') WHERE household_id = ?",
                (json.dumps(plan["stored"]), counts["breakfast"], counts["lunch"], counts["dinner"],
                 1 if plan["answered_any"] else 0, household_id()),
            )
            if plan["snacks_per_day"] is not None:
                # What set_household_meal_preferences writes for a snacks
                # answer (both numbers, both answered flags), on this
                # transaction.
                per_day = plan["snacks_per_day"]
                conn.execute(
                    "UPDATE meal_preferences SET snacks_per_day = ?, snacks_per_day_set = 1, "
                    "snacks_per_week = ?, snacks_per_week_set = 1 WHERE household_id = ?",
                    (per_day, _preferences.snacks_per_week_from_per_day(per_day), household_id()),
                )
                _preferences.keep_snack_counts_consistent(conn, household_id())
            if plan["prep_days"] is not None:
                _rhythm._upsert(conn, "", "", "prep_days", json.dumps(plan["prep_days"]), "", source)
            conn.commit()
        finally:
            conn.close()
    _household._log_preference_event("usual_week", "write")
    if plan["snacks_per_day"] is not None:
        _household._log_preference_event("snacks_per_day", "write")
    if plan["prep_days"] is not None:
        _household._log_preference_event("rhythm:prep_days", "write")
    return get_usual_week()


def meal_counts_changed_elsewhere(changed: dict[str, int]) -> None:
    """
    An older door (the Different dishes steppers, chat's edit_preference, a
    reset) just set a meal's count. When the saved grid has that meal off
    every day and the count is now above 0, the number wins: the meal is
    back on for everyone every day and its variety choice is cleared —
    otherwise the grid would say "never" while the count says "three", and
    generation would plan one dish a day from a meal the grid has off.
    Nothing for a household with no saved grid.
    """
    with _save_lock():
        conn = get_conn()
        try:
            row = _prefs_row(conn)
            stored = _stored(row)
            if not stored:
                return
            touched = False
            for meal, count in changed.items():
                if meal not in MEALS or not count or int(count) <= 0:
                    continue
                cells = stored["grid"].get(meal) or {}
                if all(cells.get(d, EVERYONE) == OFF for d in WEEKDAYS):
                    stored["grid"][meal] = {d: EVERYONE for d in WEEKDAYS}
                    stored.setdefault("variety", {})[meal] = {"choice": None, "dishes": int(count)}
                    touched = True
            if touched:
                conn.execute(
                    "UPDATE meal_preferences SET usual_week_json = ? WHERE household_id = ?",
                    (json.dumps(stored), household_id()),
                )
                conn.commit()
        finally:
            conn.close()


def prep_days_cleared() -> None:
    """The prep days were cleared through another door (rhythm.set_prep_days
    — the Settings chips, chat): a stored "Meal prep ahead" lunch goes back
    to PREP_REMOVED_LUNCH_CHOICE, number and all, so the usual week never
    names a prep session that no longer exists."""
    with _save_lock():
        conn = get_conn()
        try:
            row = _prefs_row(conn)
            stored = _stored(row)
            if not stored or ((stored.get("variety") or {}).get("lunch") or {}).get("choice") != "meal_prep_ahead":
                return
            grid = {m: {d: (stored["grid"].get(m) or {}).get(d, EVERYONE) for d in WEEKDAYS} for m in MEALS}
            on = len(days_on(grid, "lunch"))
            dishes = resolve_dishes("lunch", PREP_REMOVED_LUNCH_CHOICE, on)
            stored["variety"]["lunch"] = {"choice": PREP_REMOVED_LUNCH_CHOICE, "dishes": dishes}
            conn.execute(
                "UPDATE meal_preferences SET usual_week_json = ?, lunches_per_week = ? WHERE household_id = ?",
                (json.dumps(stored), dishes, household_id()),
            )
            conn.commit()
        finally:
            conn.close()


# ---------- generation ----------

def _weekday(iso: str) -> str:
    return WEEKDAYS[date.fromisoformat(iso).weekday()]


def generation_plan(dates: list[str], skipped: list[str] | None = None) -> dict:
    """
    What the usual week means for one period being planned:

      answered        — whether the household has saved a usual week
      off_slots       — [{"date", "slot"}] the grid has off (a meal off
                        every day is left to the zero-count pass)
      subsets         — {(date, slot): [member ids]} eaten by just those
      targets         — {slot: different dishes for THIS period}, for an
                        answered household only
      usual_on        — {slot: days on in the usual week} (7 unanswered)
      variety_answered — the meals whose number is a choice they made
      lunch_choice    — the lunch variety choice key, or None

    Unanswered: nothing to do, and the counts are left to the existing
    proration exactly as before.
    """
    conn = get_conn()
    try:
        state = _state(conn)
    finally:
        conn.close()
    skipped_set = set(skipped or [])
    out = {
        "answered": state["answered"], "off_slots": [], "subsets": {}, "targets": {},
        "usual_on": {m: 7 for m in MEALS}, "variety_answered": [], "lunch_choice": None,
    }
    if not state["answered"]:
        return out
    grid = state["grid"]
    for meal in MEALS:
        on = days_on(grid, meal)
        out["usual_on"][meal] = len(on)
        if state["variety"][meal]["choice"] is not None:
            out["variety_answered"].append(meal)
        planned_days = [d for d in dates if d not in skipped_set and _weekday(d) in on]
        if on:
            for d in dates:
                if d in skipped_set:
                    continue
                cell = grid[meal][_weekday(d)]
                if cell == OFF:
                    out["off_slots"].append({"date": d, "slot": meal})
                elif isinstance(cell, list):
                    out["subsets"][(d, meal)] = list(cell)
        n = state["variety"][meal]["dishes"]
        if state["variety"][meal]["choice"] == "new_every_day":
            # Something new every day means one per day the meal is on in
            # THIS period — a two-week period has twice as many.
            out["targets"][meal] = len(planned_days) if n > 0 else 0
        else:
            out["targets"][meal] = scale_to_period(n, len(planned_days), len(on))
    out["lunch_choice"] = state["variety"]["lunch"]["choice"]
    out["lunch_dishes"] = state["variety"]["lunch"]["dishes"]
    return out


def switched_off_meals() -> set[str]:
    """
    The meals this household has switched off ENTIRELY in its usual week:
    no day of the grid has them on (breakfast set to None in onboarding, or
    every cell of its row tapped off). Read through the same state
    get_usual_week reads, so a household that never saved a usual week gets
    its grid derived from its counts (a count of 0 is off every day) and
    the answer is the same. A meal that is on most days and off or empty on
    one is NOT in this set — that day's row keeps its own wording.
    """
    conn = get_conn()
    try:
        state = _state(conn)
    finally:
        conn.close()
    return {m for m in MEALS if not days_on(state["grid"], m)}


def off_slots_on(dates: list[str]) -> set[tuple[str, str]]:
    """{(date, slot)} the saved usual week has off on these dates — what
    "Build a plan" on a left-out day and the menu's can_fill must not fill.
    Empty for a household with no saved usual week."""
    plan = generation_plan(dates)
    return {(s["date"], s["slot"]) for s in plan["off_slots"]}


def slot_days(dates: list[str], skipped: list[str] | None = None) -> dict | None:
    """
    For the draft's count line (draft_opener.count_note): {"off": {(date,
    slot)}, "days": {slot: (days this period has the meal on, days the
    usual week has it on)}}, so "three breakfasts this week, not five" is
    scaled by the days breakfast is actually on, not by seven. None for a
    household that hasn't saved a usual week (the line reads as before).
    """
    plan = generation_plan(dates, skipped)
    if not plan["answered"]:
        return None
    off = {(s["date"], s["slot"]) for s in plan["off_slots"]}
    kept = [d for d in dates if d not in set(skipped or [])]
    return {
        "off": off,
        "days": {
            m: (sum(1 for d in kept if (d, m) not in off) if plan["usual_on"][m] else 0, plan["usual_on"][m])
            for m in MEALS
        },
    }


def scale_to_period(dishes: int, days_this_period: int, days_usual: int) -> int:
    """
    A usual week's different-dish number, for a period that has
    `days_this_period` of that meal where the usual week has `days_usual`.
    The same rule as meal_variety.prorate_meal_count (scaled to the days,
    rounded up, never below one, never above the days there are) with the
    usual week's own days in place of seven — so a seven-day grid gives
    exactly what prorate_meal_count gives. Never more than the usual
    number on a period longer than a week (the number is per week, as
    prorate_meal_count has it); "Something new every day" is the one choice
    that grows with the period, and generation_plan counts it as the days
    on directly rather than through here. A period with none of that meal
    is 0.
    """
    from . import meal_variety as _meal_variety

    if dishes <= 0 or days_this_period <= 0:
        return 0
    if days_usual >= 7:
        return _meal_variety.prorate_meal_count(dishes, days_this_period)
    if days_this_period >= days_usual:
        return min(dishes, days_this_period)
    return max(1, min(int(math.ceil(dishes * days_this_period / days_usual)), days_this_period))


def fill_up_allowed(slot: str, memory: dict, usual: int | None, usual_on: int = 7) -> bool:
    """
    Whether the count pass may re-pick UP to this slot's number (spending
    model calls). A number the household chose on the variety question is
    always a target — "Something new every morning" included, which is the
    one the old `< 7` rule could never reach. A household that has not
    answered it keeps the old rule exactly: only with meal_counts_set, and
    only below its usual days (seven, for everyone before the grid) — a
    count still sitting at every day is a default nobody chose.
    """
    if not memory.get("meal_counts_set"):
        return False
    if slot in (memory.get("variety_answered") or ()):
        return True
    return usual is not None and int(usual) < usual_on


def apply_usual_attendance(plan: dict, dates: list[str]) -> None:
    """
    Write "just these people" from the grid into slot_attendance for the
    period, so the meal is planned, sized and shopped for that table by the
    machinery that already does it (attendance.context_for_week for the
    model, servings_scale_factor for the list). Writes or removes the rows
    it owns (source usual_week).

    A row the week has of its own (the day sheet, a toggle, the guests
    chip) wins over the grid ENTIRELY once the grid has been applied to
    that slot — slot_attendance.grid_applied, set by every write here and
    left alone by every later write (attendance._write's upsert doesn't
    name it), so a person toggling Vineeth back onto a "just Emily"
    Thursday keeps him through any regeneration. Only a week-own row
    written BEFORE the grid ever reached that slot (guests set before the
    first draft) was built on "everyone's here"; that one is rebased once
    onto the grid's subset (their absences plus the people the grid leaves
    out, their guests kept) — unless that would leave nobody from the
    household at the meal. A trip's rows are never touched.
    """
    from . import attendance as _attendance

    if not plan["answered"] or not dates:
        return
    conn = get_conn()
    try:
        members = [m["id"] for m in _members(conn)]
        rows = conn.execute(
            "SELECT date, slot, source, absent_member_ids_json, guest_count, away_stretch_id, grid_applied "
            "FROM slot_attendance WHERE household_id = ? AND date >= ? AND date <= ?",
            (household_id(), min(dates), max(dates)),
        ).fetchall()
    finally:
        conn.close()
    existing = {(r["date"], r["slot"]): r for r in rows}
    for d in dates:
        for meal in MEALS:
            key = (d, meal)
            subset = plan["subsets"].get(key)
            row = existing.get(key)
            source = row["source"] if row is not None else None
            if source is not None and source != ATTENDANCE_SOURCE:
                if (not subset or source not in WEEK_OWN_SOURCES or row["away_stretch_id"]
                        or row["grid_applied"]):
                    continue
                absent = set(json.loads(row["absent_member_ids_json"] or "[]")) | {
                    i for i in members if i not in subset}
                if [i for i in members if i not in absent]:
                    _attendance._write(d, meal, sorted(absent), int(row["guest_count"] or 0), source, None)
                _mark_grid_applied(d, meal)
                continue
            if subset:
                absent = [i for i in members if i not in subset]
                _attendance._write(d, meal, absent, 0, ATTENDANCE_SOURCE, None)
                _mark_grid_applied(d, meal)
            elif source == ATTENDANCE_SOURCE:
                _attendance.clear_slot_attendance(d, meal)


def _mark_grid_applied(meal_date: str, slot: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE slot_attendance SET grid_applied = 1 WHERE household_id = ? AND date = ? AND slot = ?",
            (household_id(), meal_date, slot),
        )
        conn.commit()
    finally:
        conn.close()


def weekday_lunches_answer(plan: dict, dates: list[str], skipped: list[str] | None = None) -> dict:
    """
    The weekday-lunches answer the lunch variety stands for, when the
    week's own intake has none: "Last night's dinner" is every weekday
    lunch on the grid as leftovers of the dinner before it; "Meal prep
    ahead" is every one prepped on the household's prep day. Built
    through weekday_lunches.normalize (not strict: a lunch with no dinner
    the evening before in the period is simply left to the planner), so
    the planner, the prompt and the fold read it exactly as if the
    household had answered step 3. {} for any other choice.
    """
    from . import weekday_lunches as _weekday_lunches

    choice = plan.get("lunch_choice")
    if choice not in ("last_nights_dinner", "meal_prep_ahead"):
        return {}
    off = {(s["date"], s["slot"]) for s in plan["off_slots"]}
    skipped_set = set(skipped or [])
    days = []
    for d in dates:
        if d in skipped_set or (d, "lunch") in off or not _weekday_lunches.is_weekday(d):
            continue
        if choice == "last_nights_dinner":
            evening = date.fromordinal(date.fromisoformat(d).toordinal() - 1).isoformat()
            if (evening, "dinner") in off:
                continue
            days.append({"date": d, "kind": "leftovers"})
        else:
            days.append({"date": d, "kind": "prepped"})
    prep_days = _prep_answer()["days"] if choice == "meal_prep_ahead" else []
    answer = {"days": days, "prep_days": prep_days}
    if prep_days:
        # "Meal prep ahead" = 2 with ONE prep day is both dishes cooked in
        # that one session, the week's lunches alternating between them
        # (weekday_lunches.normalize deals each lunch a `batch`); with two
        # prep days it is one dish each, as before.
        dishes = plan.get("lunch_dishes") or VARIETY_CHOICES["lunch"]["meal_prep_ahead"]
        answer["dishes_per_prep_day"] = max(1, math.ceil(int(dishes) / len(prep_days)))
    return _weekday_lunches.normalize(answer, dates, skipped=list(skipped_set), strict=False)


def settle_off_slots(plan_id: int, off_slots: list[dict], leave: set | None = None) -> None:
    """Every slot the usual week has off, planned empty — cleared first,
    because the model being told is not the model being prevented."""
    from . import slot_needs as _slot_needs

    for s in off_slots:
        if leave and (s["date"], s["slot"]) in leave:
            continue
        _slot_needs._settle_slot_empty(
            plan_id, s["date"], s["slot"], OFF_REASON, derived_from={"constraint": OFF_CONSTRAINT},
        )


# ---------- the first week never arrives with a gap ----------

def household_has_a_plan() -> bool:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM weekly_plans WHERE household_id = ? LIMIT 1", (household_id(),)
        ).fetchone() is not None
    finally:
        conn.close()


def _open_gaps(plan_id: int, dates: list[str]) -> list[dict]:
    from . import dinner_gaps as _dinner_gaps

    rows = _dinner_gaps._plan_rows(plan_id)
    by_slot: dict[tuple, list] = {}
    for r in rows:
        by_slot.setdefault((r["date"], r["slot"] or "dinner"), []).append(r)
    gaps = []
    for d in dates:
        for meal in MEALS:
            here = by_slot.get((d, meal)) or []
            if not here or not all(r["slot_state"] == "open" for r in here):
                continue
            if any(_dinner_gaps.keeps_its_question(json.loads(r["derived_from_json"] or "{}") or {}) for r in here):
                continue
            gaps.append({"date": d, "slot": meal, "rows": here})
    return gaps


# How many quick picks one group of open slots may spend before it falls
# back to a repeat — more than the swap's two, since a first week that
# arrives with a question in it is the thing this exists to prevent.
FIRST_PLAN_PICK_ATTEMPTS = 3
FIRST_PLAN_FILL_CONSTRAINT = "first_week_fill"


def _deal(gaps: list[dict], groups: int) -> list[list[dict]]:
    """Open slots of one meal dealt in date order into `groups` dishes —
    Mon A, Tue B, Wed A… — so the week alternates rather than bunching."""
    out: list[list[dict]] = [[] for _ in range(max(1, groups))]
    for n, gap in enumerate(sorted(gaps, key=lambda g: g["date"])):
        out[n % len(out)].append(gap)
    return [g for g in out if g]


def fill_first_plan_gaps(plan_id: int, dates: list[str], picker=None) -> dict:
    """
    The household's FIRST week never arrives with a meal left open (the
    reveal's "Still deciding"). Runs after every pass of
    agent._finish_week_slots, on a first plan only.

    The only slots left as questions are the ones that genuinely need the
    person (dinner_gaps.keeps_its_question: an allergy ruled everything
    out, who's home changed, a holiday only they can answer). Everything
    else that is still open is filled:

      1. Grouped by meal into as many dishes as that meal's number asks for
         (never more than its open slots), and every group picked AT THE
         SAME TIME through main's fast path — allergen_gate.quick_pick, the
         name-a-dish call — each pick gated for the household's allergies
         before it is accepted, up to FIRST_PLAN_PICK_ATTEMPTS a group (a
         rejection is never the end of a group). A pick is saved PENDING,
         like any new dish of the menu pass, and the recipe pass writes it
         up with the rest of the week.
      2. What no pick filled: another of the week's own dishes for that meal
         (meal_variety.fill_gaps_with_a_repeat, dinner_gaps.
         fill_open_dinners with no model calls left) — a safe repeat before
         a question.

    `picker` stands in for quick_pick (tests). Never raises.
    """
    from . import allergen_gate as _allergen_gate
    from . import dinner_gaps as _dinner_gaps
    from . import meal_variety as _meal_variety
    from . import swap_in_place as _swap
    from . import weekly_plan as _weekly_plan
    from . import plates as _plates
    from . import leftovers as _leftovers
    import contextvars

    out = {"filled": [], "repeated": [], "left": []}
    try:
        gaps = _open_gaps(plan_id, dates)
        if not gaps:
            return out
        pick_one = picker or _allergen_gate.quick_pick
        avoidances = _allergen_gate.hard_avoidances()
        conn = get_conn()
        try:
            counts = _counts(_prefs_row(conn))
        finally:
            conn.close()

        # 1. One group per dish to pick, contexts built here (they read the
        # plan as it stands), picks side by side.
        # At most allergen_gate.MAX_HELD_DISHES picks in flight (the same
        # cap and worker pool the held-back re-pick uses), every meal with a
        # gap getting at least one.
        tasks = []
        meals_open = [m for m in MEALS if any(g["slot"] == m for g in gaps)]
        room = _allergen_gate.MAX_HELD_DISHES
        for n, meal in enumerate(meals_open):
            meal_gaps = [g for g in gaps if g["slot"] == meal]
            have = _meal_variety.distinct_dishes(plan_id, meal)
            wanted = max(1, (counts.get(meal) or 1) - len(have))
            groups = max(1, min(wanted, len(meal_gaps), room - (len(meals_open) - n - 1)))
            room -= groups
            for group in _deal(meal_gaps, groups):
                first = group[0]
                entry = {"date": first["date"], "slot": meal, "meal": "", "entry_id": first["rows"][0]["id"]}
                try:
                    context = _swap.build_swap_context(plan_id, entry, list(have))
                    context["replacing_because"] = f"nothing was planned for this {meal} yet."
                    others = [g["date"] for g in group[1:]]
                    if others:
                        context["also_on"] = others
                except Exception:
                    logger.exception("Could not build the first-week pick for %s %s", first["date"], meal)
                    continue
                tasks.append((contextvars.copy_context(), context, group))

        def _run(task):
            ctx, context, _group = task
            try:
                return ctx.run(_allergen_gate._pick_for_group, context, "", pick_one, avoidances,
                               FIRST_PLAN_PICK_ATTEMPTS)
            except Exception:
                logger.exception("First-week pick for %s %s failed", context.get("date"), context.get("slot"))
                return {"pick": None, "calls": 0, "seconds": 0.0}

        outcomes = _allergen_gate._run_all(tasks, _run)

        # Written back on this thread, in order.
        for (_ctx, context, group), outcome in zip(tasks, outcomes):
            pick = outcome.get("pick")
            if not pick:
                continue
            meal = context["slot"]
            try:
                serves = _swap._table_for(group[0]["date"], meal)["serves"]
                pick["meal_name"] = _swap.honest_meal_name(pick)
                _allergen_gate._save_pick(pick, serves)
                groups_ = [g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS]
                cook = None
                for gap in group:
                    ids = [r["id"] for r in gap["rows"]]
                    # A dinner dish on several nights is cooked once and eaten
                    # again (a leftovers chain, dinner_gaps._reheat) while the
                    # next night is within the three-day reach of the cook.
                    if (meal == "dinner" and cook is not None
                            and _leftovers.days_apart(cook["date"], gap["date"]) <= _leftovers.MAX_LEFTOVER_DAYS):
                        _dinner_gaps._reheat(plan_id, ids, gap["date"], meal, cook,
                                             {"constraint": FIRST_PLAN_FILL_CONSTRAINT})
                    else:
                        written = _weekly_plan._replace_slot_entries(
                            plan_id, ids, gap["date"], meal, pick["meal_name"],
                            food_groups=groups_, reasoning="",
                            derived_from={"constraint": FIRST_PLAN_FILL_CONSTRAINT},
                        )
                        if meal == "dinner" and written.get("entry_id"):
                            cook = {"id": written["entry_id"], "date": gap["date"], "meal": pick["meal_name"],
                                    "food_groups_json": json.dumps(groups_)}
                    out["filled"].append({"date": gap["date"], "slot": meal, "meal": pick["meal_name"]})
            except Exception:
                logger.exception("Writing the first-week pick for %s failed; its slots go to the repeat pass", meal)

        # 2. A repeat of a safe dish already on the week, before a question.
        if _open_gaps(plan_id, dates):
            _meal_variety.fill_gaps_with_a_repeat(plan_id, dates)
            _dinner_gaps.fill_open_dinners(plan_id, dates, budget=_allergen_gate.CallBudget(0), reserve=0)
        out["left"] = [{"date": g["date"], "slot": g["slot"]} for g in _open_gaps(plan_id, dates)]
        settled = {(f["date"], f["slot"]) for f in out["filled"]} | {(g["date"], g["slot"]) for g in out["left"]}
        out["repeated"] = [{"date": g["date"], "slot": g["slot"]} for g in gaps
                           if (g["date"], g["slot"]) not in settled]
        if out["left"]:
            logger.warning("First plan %s still has open slots after filling: %s", plan_id, out["left"])
        logger.info("First plan %s gaps: %d picked in %d group(s), %d left", plan_id,
                    len(out["filled"]), len(tasks), len(out["left"]))
    except Exception:
        logger.exception("Filling the first plan's open slots failed for plan %s; it stands as it is", plan_id)
    return out
