"""
The day's meals, as the row per meal Today puts at the top of the screen.

Loop Board "Today: the day's meals at the top, then Cook tonight, Prep and
a shopping-day line" (High, Phase 1; Gowthami's household, 2026-10-04:
"Today screen should show what the meals are for the day, and have a prep
section"). Until this, Today had no breakfast/lunch/dinner summary at all —
it had Shop and Cook, with the day's prep rows inside Cook, so the one
question a person opening the app in the morning actually has ("what are we
eating today?") was answered by inference from a list of jobs.

This module answers only that question. It is NOT a second timeline: it
reads the same cooker view `moves.py` reads and says nothing about when to
start, which belongs to the move (and so to the server's own start-by
arithmetic — Today and Cook must never disagree about a clock).

ROW ORDER is breakfast, lunch, SNACKS, dinner — Emily's own instruction on
the mockup, 2026-10-04: snacks "get a row in the same card, after Lunch and
before Dinner". The card's older summary line reads "(Snacks last if
planned)"; the later, more specific sentence wins, and it is also what the
card's own mutation list assumes ("the snack row before Lunch" is named
there as a wrong state). ASSUMPTION, reversible in one line — SLOT_ORDER
below.

A day holds at most one breakfast, lunch and dinner (weekly_plan.WEEK_SLOTS
is the three real meals, and two rows for one of them is a bug
audit_plan_slots reports) and may hold SEVERAL snacks (DAY_SLOTS includes
snack; see the 2026-09-08 "Meals renders snacks" entry). So the snack row is
ONE row naming every snack dish, which is also what the card asks for while
snacks are household-wide.


WHAT A PER-PERSON LINE CAN AND CANNOT SAY — measured, 2026-10-05
-----------------------------------------------------------------
The card's examples ("office, packed cold", "school, nut-free thermos") are
richer than anything this app records. What it actually has:

- `slot_attendance` — who is present and who is absent at one meal, plus a
  guest COUNT (not guests by name).
- `rhythm.lunch_location` — per member, one of 'home' | 'out' | 'varies',
  with an optional per-weekday override. That is the whole vocabulary:
  "out" is as specific as it gets, and there is no 'school', no 'office'.
- `week_intake.packed_lunch_days` and `weekday_lunches` kinds — both keyed
  by DATE and household-wide, so neither can say that one person's lunch is
  packed and another's is not.

And what it does NOT have, which decides the shape of this module:
`meal_plan_entries` carries no member column of any kind, and a (date,
slot) holds one dish. **So the app cannot record two people eating
different things at one meal.** The card's "when people at a meal eat
different things" branch therefore has no data behind it today, and a line
reading "Arjun · school · nut-free thermos" would be invented
(DESIGN_SYSTEM.md §8: never say a thing that isn't true).

So the per-person shape is built and is fed by the one fact that really
does vary per person today: WHERE each of them is at lunch. Lines are
grouped by (dish, where) — the generalisation of the card's "people eating
the same dish share a line" that reproduces the card's own example output
(Arjun on his own, "Gowthami + Ravi" together, all three on the same dish),
which grouping by dish ALONE could not. When every present person shares
one group, the row collapses to the card's single line with "everyone".

The shape has a place for a per-person DISH (`lines[].dish`) already, so
the day a per-person dish is recordable this becomes a read rather than a
redesign.
"""
from __future__ import annotations

import logging
from datetime import date

from ..db import get_conn
from . import attendance as _attendance
from . import member_needs as _member_needs
from . import rhythm as _rhythm
from ._shared import IN_MEALS_SQL, display_initials, household_id

logger = logging.getLogger(__name__)


# Breakfast, lunch, snacks, dinner — see the module docstring. Emily's to
# reverse in one line.
SLOT_ORDER = ("breakfast", "lunch", "snack", "dinner")

SLOT_LABELS = {
    "breakfast": "Breakfast",
    "lunch": "Lunch",
    "snack": "Snacks",
    "dinner": "Dinner",
}

# What `rhythm.lunch_location` can say, said the way a person would. 'varies'
# is a real answer and still tells a reader nothing about today, so it reads
# as nothing at all rather than as "varies" — the same rule
# shop_days_summary follows for an unanswered shop day.
WHERE_WORDS = {"home": "at home", "out": "out"}

# The one word for a person who is not at this meal. Kept apart from
# WHERE_WORDS because it is an absence, not a place: attendance knows it,
# lunch_location does not.
AWAY_WORD = "away"


def _weekday(day: str) -> str:
    return date.fromisoformat(day).strftime("%A")


def provenance_note(meal: dict) -> str:
    """
    Where a meal came from, when it did not come out of a pan today:
    "leftovers from Sunday", "made ahead Sunday", "from the freezer",
    "prepped Sunday". '' for an ordinary cook.

    THE one implementation of that wording. moves.py builds a reheat row's
    meta line from this too (it used to carry its own copy), because the
    two surfaces are two readings of one fact and this repo's standing
    complaint is that two implementations of one RULE drift — here they
    would have drifted in the one place a reader sees both at once, since
    Today now draws the meals row and the Cook row on the same screen.

    "Made ahead — Sunday's Egg White Bites" vs "Leftovers — Sunday's
    Bulgogi" is the same fact one honest word apart (leftovers.
    made_ahead_headline); a portion out of the freezer has no night on this
    week to point at, so it says where it came from rather than leaving
    "leftovers from" dangling.
    """
    if meal.get("is_leftovers"):
        headline = meal.get("leftovers_headline") or ""
        made_ahead = headline.startswith("Made ahead")
        lead = "made ahead" if made_ahead else "leftovers from"
        source_date = (meal.get("leftovers_from") or {}).get("date")
        if source_date:
            return f"{lead} {_weekday(source_date)}"
        return lead if made_ahead else "from the freezer"
    # A lunch whose batch was cooked on an earlier (prep) day is not a cook
    # today (Emily, 2026-09-30) — the words are Cook's own.
    prepped = meal.get("prepped_ahead") or {}
    if prepped.get("date") and prepped["date"] < (meal.get("date") or ""):
        return f"prepped {_weekday(prepped['date'])}"
    return ""


def covers_note(meal: dict, day: str) -> str:
    """
    "makes tomorrow's lunch" — a cook whose batch feeds a LATER meal, said
    as what it does for the day rather than as the batch's own servings
    arithmetic (that is `covers_note` on the cooker card, which Cook's row
    already shows). '' when this cook feeds only itself.

    Reads `covers` ({date, slot, eaters}), which cooker._apply_leftover_chains
    writes on a chain source, rather than parsing the note back out of
    English.
    """
    later = [c for c in (meal.get("covers") or []) if (c.get("date") or "") > day]
    if not later:
        return ""
    first = min(later, key=lambda c: (c.get("date") or "", c.get("slot") or ""))
    slot = (first.get("slot") or "meal").lower()
    try:
        gap = (date.fromisoformat(first["date"]) - date.fromisoformat(day)).days
    except (TypeError, ValueError):
        return ""
    when = "tomorrow's" if gap == 1 else f"{_weekday(first['date'])}'s"
    return f"makes {when} {slot}"


def _join_names(names: list[str]) -> str:
    """
    "Gowthami + Ravi" — the card's own plus, not attendance._join_names'
    "and". A line of initials and names in a column is a label, not a
    sentence, and "Gowthami and Ravi" at 13px reads as prose.
    """
    return " + ".join(names)


def household_people() -> dict:
    """
    Everyone meals are planned for, and the letter each of them is drawn
    as, in ONE read: {"names": [...], "initials": {name: letter}}.

    The initials come from `_shared.display_initials`, which is THE rule
    for them (Loop Board "Two people with the same initial", 2026-09-21) —
    not a first-letter slice of our own, which would have told Emily and
    Ethan apart differently from the day sheet two taps away. Computed over
    the WHOLE household rather than over the people at one meal, because
    that is what makes the letter the same on every screen: a collision is
    a property of the household, not of who turned up to lunch.

    A helper who does not eat here is not one of them (EATS_HERE_SQL, the
    same filter household.list_members uses).
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT name, age_group, snacks_per_day FROM members "
            f"WHERE household_id = ? AND {IN_MEALS_SQL} ORDER BY id ASC",
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()
    names = [r["name"] for r in rows]
    # Snacks a day per person rides along on the SAME read (the day's read
    # budget is pinned by a test): the snack row names who they are for.
    snacks = {
        r["name"]: (r["snacks_per_day"] if r["snacks_per_day"] is not None
                    else _member_needs.default_snacks(r["age_group"]))
        for r in rows
    }
    return {"names": names, "initials": dict(zip(names, display_initials(names))), "snacks": snacks}


def _where_for(slot: str, name: str, weekday: str, lunch: dict) -> str:
    """
    Where this person will be at this meal, in their own words, or ''.

    Lunch only: lunch_location is the only per-person place fact the app
    has, and it is about lunch by name. Saying nothing about breakfast and
    dinner is the honest answer rather than an omission — see the module
    docstring.

    `lunch` is get_household_rhythm()'s own `lunch_location` map, read ONCE
    for the whole day by the caller. Measured: going through
    rhythm.effective_lunch_location here instead cost a whole
    get_household_rhythm — one connection — PER PERSON PER LUNCH, which is
    exactly the per-row read this repo keeps writing down. The override
    rule is that function's, restated in one line rather than reimplemented:
    the weekday's override if one has been learned, else the standing
    answer, else nothing (never-asked and 'varies' both say nothing, which
    is honest about today either way).
    """
    if slot != "lunch":
        return ""
    entry = lunch.get((name or "").strip()) or {}
    where = (entry.get("overrides") or {}).get(weekday) or entry.get("standing")
    return WHERE_WORDS.get((where or "").strip().lower(), "")


def _lines_for(slot: str, dish: str, entry_id, att: dict, day: str, initials: dict,
               lunch: dict) -> tuple[list[dict], bool]:
    """
    The row's lines, and whether they are a per-person breakdown.

    One group per (dish, where) over the people who are AT this meal. One
    group means the ordinary single line, whose `who` is "everyone" when
    everyone is home and the present names when somebody is out — the
    card's "who it's for when not everyone".
    """
    present = att.get("present_names") or []
    everyone = bool(att.get("everyone_home"))
    weekday = _weekday(day)
    groups: list[dict] = []
    index: dict[tuple, dict] = {}
    for name in present:
        key = (dish, _where_for(slot, name, weekday, lunch))
        group = index.get(key)
        if group is None:
            group = {"dish": dish, "entry_id": entry_id, "where": key[1], "names": []}
            index[key] = group
            groups.append(group)
        group["names"].append(name)

    if len(groups) > 1:
        for g in groups:
            g["initials"] = [initials.get(n, "?") for n in g["names"]]
            g["who"] = _join_names(g["names"])
        return groups, True

    # One line. Whose it is only needs saying when it is not everybody's;
    # a guest count says the table is bigger, not that the meal is for
    # somebody in particular, so it leaves "everyone" standing.
    who = "everyone" if everyone or not present else _join_names(present)
    where = groups[0]["where"] if groups else ""
    return [{
        "dish": dish,
        "entry_id": entry_id,
        "where": where,
        "names": [] if (everyone or not present) else list(present),
        "initials": [] if (everyone or not present) else [initials.get(n, "?") for n in present],
        "who": who,
    }], False


def _row_note(meal: dict, day: str) -> str:
    """The row's short grey note: where it came from, else what it makes."""
    return provenance_note(meal) or covers_note(meal, day)


def for_day(day: str | date, view: dict, attendance_by_slot: dict | None = None,
            people: dict | None = None, rhythm: dict | None = None) -> list[dict]:
    """
    One row per planned meal on `day`, in SLOT_ORDER.

    `view` is the cooker view the caller already holds (today_moves reads it
    once for the whole payload); this never reads the plan itself, so the
    meals row and the Cook row can never be built from two different reads
    of one week.

    `attendance_by_slot` is {slot: attendance} for this day — what
    get_week_attendance(day, 1) returns for it, which is ONE connection for
    the whole day rather than one per meal. Omitted, it is read here. Only
    the slots that DEVIATE from everyone's-home appear in it (that read's
    own convention), so a missing slot is the implicit everyone's-home
    default and costs nothing.
    """
    day_str = day.isoformat() if isinstance(day, date) else str(day)
    if attendance_by_slot is None:
        attendance_by_slot = day_attendance(day_str)
    if people is None:
        people = household_people()
    initials = people.get("initials") or {}
    # Read once for the whole day. today_moves already holds the household's
    # rhythm for the Today payload and hands it in, so on the path the
    # screen actually takes this costs nothing at all.
    if rhythm is None:
        rhythm = _rhythm.get_household_rhythm()
    lunch = rhythm.get("lunch_location") or {}

    by_slot: dict[str, list[dict]] = {}
    for meal in view.get("meals") or []:
        if meal.get("date") != day_str:
            continue
        slot = (meal.get("slot") or "dinner").lower()
        if slot not in SLOT_LABELS:
            # A slot nobody has a word for is not a meal this card can
            # name. weekly_plan.slot_order_sql sorts one last rather than
            # dropping it; here there is no honest label to give it.
            continue
        by_slot.setdefault(slot, []).append(meal)

    rows = []
    for slot in SLOT_ORDER:
        meals = by_slot.get(slot) or []
        if not meals:
            continue
        att = attendance_by_slot.get(slot) or _everyone_home(people)
        if att.get("nobody_home"):
            # Nobody is eating it, so there is nothing to say about it. The
            # plan already writes that slot as planned_empty (slot_needs);
            # this is the belt for a row that reached here anyway.
            continue
        if slot == "snack":
            att = _snack_eaters(att, people.get("snacks") or {})
        # Snacks: ONE row naming every snack dish (the card, while snacks
        # are household-wide). The lines are built off the FIRST of them so
        # the per-person shape is the same shape as every other row's —
        # per-person snacks drop into `lines` when the data arrives.
        lead = meals[0]
        dishes = [m.get("meal") or SLOT_LABELS[slot] for m in meals]
        lines, per_person = _lines_for(
            slot, dishes[0], lead.get("entry_id"), att, day_str, initials, lunch
        )
        rows.append({
            "slot": slot,
            "label": SLOT_LABELS[slot],
            "date": day_str,
            "dishes": dishes,
            "entry_id": lead.get("entry_id"),
            "entry_ids": [m.get("entry_id") for m in meals],
            "note": _row_note(lead, day_str),
            "lines": lines,
            "per_person": per_person,
            # Tapping the row opens that meal; a reheat has no recipe
            # behind it, which is the one thing the row must not promise
            # (moves.py's own rule, several Decision log entries).
            "is_leftovers": bool(lead.get("is_leftovers")),
        })
    return rows


def _snack_eaters(att: dict, counts: dict) -> dict:
    """
    The snack row's attendance, narrowed to the people who have snacks.

    Per-person snack counts (members.snacks_per_day, 2026-10-06) say who a
    snack is FOR: a household whose adults said "no snacks" and whose child
    said two must not read "everyone" on Today (Loop Board, walkthrough
    2026-10-06). Present people with 0 snacks a day leave the line; if that
    leaves everyone who is present, `att` is returned untouched and the row
    still says "everyone". If nobody present has a count above 0, the plan
    wrote a snack anyway — say nothing new rather than an empty "for".
    """
    present = att.get("present_names") or []
    if not present:
        return att
    eaters = [n for n in present if counts.get(n, 1) > 0]
    if not eaters or len(eaters) == len(present):
        return att
    narrowed = dict(att)
    narrowed["present_names"] = eaters
    narrowed["everyone_home"] = False
    return narrowed


def _everyone_home(people: dict) -> dict:
    """
    The implicit attendance for a slot get_week_attendance left out — the
    household, all home. Built from the names already read once for the
    whole day (household_people), so it costs no connection of its own.
    """
    names = list(people.get("names") or [])
    return {
        "present_names": names,
        "absent_names": [],
        "guest_count": 0,
        "everyone_home": True,
        "nobody_home": False,
    }


def day_attendance(day: str) -> dict:
    """
    {slot: attendance} for one day, in one read. Only the slots that
    deviate from everyone's-home are in it (get_week_attendance's own
    convention), so an ordinary day is {}.
    """
    try:
        return _attendance.get_week_attendance(day, 1).get(day, {}) or {}
    except Exception:
        # A meals card that draws without a deviation is better than one
        # that does not draw.
        logger.exception("Attendance for %s could not be read", day)
        return {}


# The package's public face (app/tools/__init__.py) already exports a
# `for_day` of somebody else's; this module's is named for its own domain
# there. `for_day` stays the name inside the module, where the module name
# is the namespace.
day_meals_for_day = for_day
