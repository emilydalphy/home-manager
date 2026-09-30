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
from datetime import date

from ..db import get_conn
from ._shared import EATS_HERE_SQL, household_id

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
            f"SELECT id, name FROM members WHERE household_id = ? AND {EATS_HERE_SQL} ORDER BY id",
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

def _parse_grid(grid, members: list[dict], current: dict) -> dict:
    from . import attendance as _attendance

    if not isinstance(grid, dict):
        raise ValueError("grid must be an object of meal → weekday → who.")
    all_ids = [m["id"] for m in members]
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
            ids = _attendance.resolve_member_ids(who)
            out[meal][key] = EVERYONE if set(ids) >= set(all_ids) else ids
    return out


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
    anything invalid, before anything is written.
    """
    from . import household as _household
    from . import rhythm as _rhythm
    from . import preferences as _preferences

    conn = get_conn()
    try:
        state = _state(conn)
    finally:
        conn.close()

    new_grid = _parse_grid(grid, state["members"], state["grid"]) if grid is not None else state["grid"]

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
        snacks_per_day = int(snacks_per_day)
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
    for meal, choice in choices.items():
        if (meal, choice) in NEEDS_PREP_DAY and not has_prep_day and days_on(new_grid, meal):
            raise ValueError("“Meal prep ahead” needs a prep day — pick the day you prep, or another lunch choice.")

    # The numbers, resolved against the grid being saved.
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

    stored = {
        "grid": new_grid,
        "variety": {m: {"choice": choices[m], "dishes": counts[m]} for m in MEALS},
    }
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO meal_preferences (household_id) VALUES (?)", (household_id(),))
        conn.execute(
            "UPDATE meal_preferences SET usual_week_json = ?, breakfasts_per_week = ?, lunches_per_week = ?, "
            "dinners_per_week = ?, meal_counts_set = CASE WHEN ? THEN 1 ELSE meal_counts_set END, "
            "updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(stored), counts["breakfast"], counts["lunch"], counts["dinner"],
             1 if answered_any else 0, household_id()),
        )
        conn.commit()
    finally:
        conn.close()
    if snacks_per_day is not None:
        _preferences.set_household_meal_preferences(
            snacks_per_day=snacks_per_day,
            snacks_per_week=_preferences.snacks_per_week_from_per_day(snacks_per_day),
            mark_complete=False,
        )
    if prep_days is not None:
        _rhythm.set_prep_days(prep_days, source=source)
    _household._log_preference_event("usual_week", "write")
    return get_usual_week()


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
        out["targets"][meal] = scale_to_period(n, len(planned_days), len(on))
    out["lunch_choice"] = state["variety"]["lunch"]["choice"]
    out["lunch_dishes"] = state["variety"]["lunch"]["dishes"]
    return out


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
    exactly what prorate_meal_count gives, and "something new every
    morning" is one dish per morning whatever the period.
    """
    from . import meal_variety as _meal_variety

    if dishes <= 0 or days_this_period <= 0:
        return 0 if dishes <= 0 else 1
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
    model, servings_scale_factor for the list). Only ever writes or
    removes rows it owns (source usual_week): a trip, the day sheet or the
    guests chip is the week's own answer and wins.
    """
    from . import attendance as _attendance

    if not plan["answered"] or not dates:
        return
    conn = get_conn()
    try:
        members = [m["id"] for m in _members(conn)]
        rows = conn.execute(
            "SELECT date, slot, source FROM slot_attendance WHERE household_id = ? AND date >= ? AND date <= ?",
            (household_id(), min(dates), max(dates)),
        ).fetchall()
    finally:
        conn.close()
    existing = {(r["date"], r["slot"]): r["source"] for r in rows}
    for d in dates:
        for meal in MEALS:
            key = (d, meal)
            subset = plan["subsets"].get(key)
            source = existing.get(key)
            if source is not None and source != ATTENDANCE_SOURCE:
                continue
            if subset:
                absent = [i for i in members if i not in subset]
                _attendance._write(d, meal, absent, 0, ATTENDANCE_SOURCE, None)
            elif source == ATTENDANCE_SOURCE:
                _attendance.clear_slot_attendance(d, meal)


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


def fill_first_plan_gaps(plan_id: int, dates: list[str], budget=None, picker=None) -> dict:
    """
    The household's FIRST week never arrives with a meal left open (the
    reveal's "Still deciding"). Runs after every pass of
    agent._finish_week_slots, on a first plan only. A slot that keeps its
    question — an allergy ruled everything out, who's home changed, a
    holiday only they can answer (dinner_gaps.keeps_its_question) — keeps
    it; anything else that is still open is filled:

      - a breakfast or lunch: another of the week's own dishes for that
        meal (meal_variety.fill_gaps_with_a_repeat); with none to repeat,
        one fresh pick through the swap's picker, which the repeat pass
        then spreads to the rest;
      - a dinner: dinner_gaps.fill_open_dinners, with the budget below.

    Its own call budget (allergen_gate.CallBudget), since the week's
    shared one may be spent by now. Never raises.
    """
    from . import allergen_gate as _allergen_gate
    from . import dinner_gaps as _dinner_gaps
    from . import meal_variety as _meal_variety

    out = {"filled": [], "left": []}
    budget = budget or _allergen_gate.CallBudget()
    try:
        gaps = _open_gaps(plan_id, dates)
        if not gaps:
            return out
        for meal in ("breakfast", "lunch"):
            for _ in range(len(dates)):
                meal_gaps = [g for g in _open_gaps(plan_id, dates) if g["slot"] == meal]
                if not meal_gaps:
                    break
                _meal_variety.fill_gaps_with_a_repeat(plan_id, dates)
                meal_gaps = [g for g in _open_gaps(plan_id, dates) if g["slot"] == meal]
                if not meal_gaps:
                    break
                gap = meal_gaps[0]
                row = gap["rows"][0]
                entry = {"id": row["id"], "date": gap["date"], "slot": meal, "meal": row["meal"] or "",
                         "derived_from_json": row["derived_from_json"] or "{}"}
                picked = _meal_variety._repick_entry(
                    plan_id, entry, budget, avoid=_meal_variety.distinct_dishes(plan_id, meal),
                    because=f"nothing was planned for this {meal} yet", reject=lambda name: False,
                    derived_key="first_week_fill", picker=picker,
                    derived_extra={"constraint": "first_week_fill"},
                )
                if picked is None:
                    break
                out["filled"].append({"date": gap["date"], "slot": meal, "meal": picked.get("meal")})
        _dinner_gaps.fill_open_dinners(plan_id, dates, budget=budget, picker=picker, reserve=0)
        out["left"] = [{"date": g["date"], "slot": g["slot"]} for g in _open_gaps(plan_id, dates)]
        if out["left"]:
            logger.warning("First plan %s still has open slots after filling: %s", plan_id, out["left"])
    except Exception:
        logger.exception("Filling the first plan's open slots failed for plan %s; it stands as it is", plan_id)
    return out
