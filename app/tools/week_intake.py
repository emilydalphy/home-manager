"""
The week's intake: the answers to the planning questions, stored as a
first-class object rather than as chat history.
"""
from __future__ import annotations

import json
import re
import sqlite3  # for IntegrityError -- see save_week_intake's retry loop
from datetime import date, timedelta
from ..db import get_conn
from ._shared import household_id
from . import weekly_plan as _weekly_plan


# The answers to the two question screens, as a first-class object rather
# than as chat history. Read DATA_MODEL.md before changing anything here:
# the append-only revisions, the two snapshots, and the per-slot provenance
# all exist to enable things that cannot be retrofitted afterwards.

# Closed set. Each tag has exactly one planning consequence — if two tags
# would produce the same behaviour, one of them shouldn't exist.
NIGHT_TAGS = {
    # Ordinary cooked dinner. Mutually exclusive with the others. Exists so
    # someone working down the list can AFFIRM a night rather than skip it:
    # an affirmed night is data, a skipped night is a guess.
    "normal": "plan a normal dinner you cook that evening.",
    # Dinner slot planned empty, and nothing for it reaches the shopping
    # list. The only deliberately empty slot in a week.
    "out": "plan nothing and buy nothing for this night.",
    # Not a vague "busy" flag: a hard cap on cook time AND a trigger for
    # cook-once-eat-twice.
    "rush": "keep it under 20 minutes, or make the night before stretch to cover it.",
    # Opens the guest follow-up. Scales recipe AND shopping quantities.
    "guests": "scale the recipe and the shopping to the bigger table.",
    # No new dinner; the previous night's batch is increased instead.
    "left": "cook enough the night before instead of planning something new.",
}


# The hard cap a `rush` night imposes, in minutes. Named rather than inlined
# because the acknowledgement copy, the generator prompt and the draft
# screen's per-slot reasons all have to agree on the same number.
RUSH_MAX_MINUTES = 20


def _week_dates(week_start: str) -> list[str]:
    start = date.fromisoformat(week_start)
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


def _household_composition() -> dict:
    """
    How many adults and children are on record, for the guest maths and the
    preferences snapshot. Counted from members.age_group, which is freeform
    — anything that isn't recognisably an adult or a child is left out of
    both counts rather than guessed into one.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT age_group FROM members WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()
    adults = children = 0
    for r in rows:
        group = (r["age_group"] or "").strip().lower()
        if group == "adult":
            adults += 1
        elif group in ("child", "teen", "toddler", "kid"):
            children += 1
    return {"adults": adults, "children": children}


def _build_preferences_snapshot(conn) -> dict:
    """
    A COPY of the preference set a plan was generated under, taken at
    generation time. Not a reference: if a plan only points at live
    preferences then the day someone edits "won't eat", every past plan's
    reasoning becomes unreadable and unreproducible — you can no longer tell
    whether a strange choice was a bug or a preference that has since
    changed. A few hundred bytes buys that.
    """
    prefs = conn.execute(
        "SELECT * FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    if not prefs:
        return {}
    return {
        "meal_counts": {
            "breakfasts": prefs["breakfasts_per_week"],
            "lunches": prefs["lunches_per_week"],
            "dinners": prefs["dinners_per_week"],
        },
        "wont_eat": json.loads(prefs["dislikes_json"]),
        "protein": json.loads(prefs["protein_preferences_json"]),
        "weeknight_max_minutes": prefs["weeknight_max_minutes"],
        "cooking_time_preference": prefs["cooking_time_preference"],
        "repeats": prefs["repeats_tolerance"],
        "kit": json.loads(prefs["kitchen_kit_json"]),
        "cuisines": json.loads(prefs["cuisine_preferences_json"]),
        "table_style": prefs["table_style"],
        "eating_style": prefs["eating_style"],
        "novelty": prefs["novelty_preference"],
        "typical_week": prefs["typical_week"],
    }


def _intake_row_to_dict(row) -> dict:
    return {
        "intake_id": row["id"],
        "week_start": row["week_start"],
        "revision": row["revision"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "night_tags": json.loads(row["night_tags_json"]),
        "guest_counts": json.loads(row["guest_counts_json"]),
        "packed_lunch_days": json.loads(row["packed_lunch_days_json"]),
        "moods": json.loads(row["moods_json"]),
        "cuisines": json.loads(row["cuisines_json"]),
        "freeform": row["freeform"],
        "household_snapshot": json.loads(row["household_snapshot_json"]),
        "preferences_snapshot": json.loads(row["preferences_snapshot_json"]),
    }


def _current_intake_row(conn, week_start: str):
    """
    The current intake for a week: the highest revision that hasn't been
    superseded. Never "the most recent row" — a superseded revision can
    have a later created_at than nothing at all, and the whole point of
    append-only is that old rows stay.
    """
    return conn.execute(
        "SELECT * FROM week_intake WHERE household_id = ? AND week_start = ? AND superseded_at IS NULL "
        "ORDER BY revision DESC LIMIT 1",
        (household_id(), week_start),
    ).fetchone()


def get_week_intake(week_start: str) -> dict | None:
    """
    The answers currently in force for one week, or None if nobody has
    started. Use this rather than reading week_intake directly — it applies
    the "highest revision, not superseded" rule that append-only depends on.
    """
    conn = get_conn()
    row = _current_intake_row(conn, week_start)
    conn.close()
    return _intake_row_to_dict(row) if row else None


def get_week_intake_history(week_start: str) -> list[dict]:
    """
    Every revision of a week's answers, oldest first — what makes "the week
    you had before you redid it" recoverable. Nothing in the UI reads this
    yet; it exists because the data is only collectable as it happens.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM week_intake WHERE household_id = ? AND week_start = ? ORDER BY revision ASC",
        (household_id(), week_start),
    ).fetchall()
    conn.close()
    return [_intake_row_to_dict(r) for r in rows]


def save_week_intake(
    week_start: str,
    night_tags: dict | None = None,
    guest_counts: dict | None = None,
    packed_lunch_days: list | None = None,
    moods: list | None = None,
    cuisines: list | None = None,
    freeform: str | None = None,
    created_by: str = "",
) -> dict:
    """
    Record the household's answers for a week, as a NEW REVISION.

    Append-only, always: a week_intake row is never updated in place. This
    call copies the current revision, applies whichever answers were passed,
    and inserts the result as revision+1, stamping superseded_at on the one
    it replaced. Arguments left as None are inherited unchanged — that's how
    Q1 and Q2 each save their own half without either clobbering the other,
    and how a chat instruction can change one answer without restating the
    rest.

    Every caller that changes an ANSWER must come through here. The rule of
    thumb is: would this have changed if they'd said it during the
    questions? If yes, it's a new revision. If chat edited only the plan and
    not the intake, "Try again" would regenerate from stale answers and
    silently revert whatever the household just said.

    Both snapshots are retaken on every revision, so each one records what
    was true when that answer was given.
    """
    date.fromisoformat(week_start)  # fail loudly on a malformed week
    week_days = set(_week_dates(week_start))
    for day, tags in (night_tags or {}).items():
        date.fromisoformat(day)  # keyed by ISO date, never by weekday
        # A tag on a date outside the week it's being saved for would
        # produce a planned_empty entry stranded outside the seven days —
        # visible nowhere, and impossible to clear from any screen.
        if day not in week_days:
            raise ValueError(f"{day} isn't in the week starting {week_start}.")
        # A bare string here would iterate its characters and report
        # "Unknown night tag(s): r, u, s, h"; a None would raise a TypeError
        # and surface as a 500. Both are only reachable by hand-written API
        # calls, and both should read as the client error they are.
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError(f"Tags for {day} must be a list of tag names.")
        unknown = [t for t in tags if t not in NIGHT_TAGS]
        if unknown:
            raise ValueError(f"Unknown night tag(s): {', '.join(unknown)}.")
        # "Regular night" is mutually exclusive — affirming a night and
        # constraining it are different answers, and holding both would
        # leave the generator with no way to tell which the household meant.
        if "normal" in tags and len(tags) > 1:
            raise ValueError("'normal' is exclusive — a regular night can't also carry another tag.")

    # Read-modify-write, retried. Two adults saving at the same moment both
    # read the same current revision and both try to write revision+1; the
    # UNIQUE index on (household_id, week_start, revision) makes the loser's
    # insert fail rather than letting it land as a second live row with the
    # first one's answers silently lost. Re-reading and retrying merges the
    # loser's answers on top of the winner's — which is what "the second
    # adult joins the first one's intake" has to mean when they arrive
    # together rather than an hour apart.
    for attempt in range(5):
        conn = get_conn()
        try:
            current = _current_intake_row(conn, week_start)
            base = _intake_row_to_dict(current) if current else {
                "night_tags": {}, "guest_counts": {}, "packed_lunch_days": [],
                "moods": [], "cuisines": [], "freeform": "",
            }

            def pick(new, key, _base=base):
                return _base[key] if new is None else new

            household_snapshot = _household_composition()
            preferences_snapshot = _build_preferences_snapshot(conn)
            revision = (current["revision"] + 1) if current else 1
            if current:
                conn.execute(
                    "UPDATE week_intake SET superseded_at = datetime('now') WHERE id = ?",
                    (current["id"],),
                )
            cursor = conn.execute(
                """
                INSERT INTO week_intake (
                    household_id, week_start, revision, created_by,
                    night_tags_json, guest_counts_json, packed_lunch_days_json,
                    moods_json, cuisines_json, freeform,
                    household_snapshot_json, preferences_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    household_id(), week_start, revision,
                    (created_by or (current["created_by"] if current else "")).strip(),
                    json.dumps(pick(night_tags, "night_tags")),
                    json.dumps(pick(guest_counts, "guest_counts")),
                    json.dumps(pick(packed_lunch_days, "packed_lunch_days")),
                    json.dumps(pick(moods, "moods")),
                    json.dumps(pick(cuisines, "cuisines")),
                    pick(freeform, "freeform"),
                    json.dumps(household_snapshot),
                    json.dumps(preferences_snapshot),
                ),
            )
            conn.commit()
            intake_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM week_intake WHERE id = ?", (intake_id,)).fetchone()
            return _intake_row_to_dict(row)
        except sqlite3.IntegrityError:
            # Somebody took this revision number between our read and our
            # write. Undo our supersede and start again from what they left.
            conn.rollback()
            if attempt == 4:
                raise
        finally:
            conn.close()


def _observed_day_patterns(week_start: str) -> dict:
    """
    What the app already knows about each weekday, so the household isn't
    re-answering things it has already been told. The grey hint line under
    each day row.

    Two sources, both real rather than invented:
      - A recurring rhythm fact from What We Know that names this weekday
        ("Tuesdays are tee-ball so we eat at 5").
      - An observed pattern in the plan history: the same weekday resolved
        to takeout or leftovers in most of the last four weeks.
    A day with neither gets no hint, and the row still works — it just
    reads "Nothing on the calendar."
    """
    dates = _week_dates(week_start)
    weekday_names = [date.fromisoformat(d).strftime("%A") for d in dates]

    conn = get_conn()
    facts = conn.execute(
        "SELECT text FROM facts WHERE household_id = ? AND category = 'rhythm'", (household_id(),)
    ).fetchall()
    lookback_start = (date.fromisoformat(week_start) - timedelta(days=28)).isoformat()
    history = conn.execute(
        """
        SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.slot = 'dinner'
          AND mpe.date >= ? AND mpe.date < ?
        """,
        (household_id(), lookback_start, week_start),
    ).fetchall()
    conn.close()

    takeout_by_weekday: dict[str, int] = {}
    for row in history:
        text = (row["meal"] or "").lower()
        if re.search(r"take[\s-]?out|delivery|order in", text):
            name = date.fromisoformat(row["date"]).strftime("%A")
            takeout_by_weekday[name] = takeout_by_weekday.get(name, 0) + 1

    hints = {}
    for iso, weekday in zip(dates, weekday_names):
        # A rhythm fact naming this weekday wins — it's something the
        # household said outright, not something inferred from behaviour.
        stated = next(
            (f["text"] for f in facts if weekday.lower() in (f["text"] or "").lower()), None
        )
        if stated:
            hints[iso] = stated
            continue
        count = takeout_by_weekday.get(weekday, 0)
        if count >= 3:
            hints[iso] = "Takeout four weeks running"
        elif count == 2:
            hints[iso] = "Takeout two of the last four weeks"
    return hints


# The cuisines offered when a household hasn't saved any of its own. Only a
# fallback — Q2 reads the household's real list from What We Know first, so
# it visibly reflects its own stored profile and never re-asks a question it
# has already been answered. Taps on this fallback are written back.
ONBOARDING_CUISINES = [
    "Italian", "Mexican", "Thai", "Indian", "Japanese",
    "Greek", "Chinese", "Middle Eastern", "American", "French",
]


def get_week_intake_prefill(week_start: str) -> dict:
    """
    Everything the two question screens need to open already knowing what
    the app knows: per-day hints, the household's own saved cuisines, its
    composition for the guest maths, and any intake already in flight.

    `in_flight` is the soft lock from DATA_MODEL.md → One intake in flight.
    Both adults are nudged on Sunday, so both can start; the second to open
    Q1 joins the first one's answers rather than starting a blank set. Two
    intakes racing to generate the same week is the one concurrency case
    that will actually happen, most likely on a Sunday evening.

    `household_known` is false when nobody's age group is on record. The
    guest panel switches to asking for the WHOLE TABLE in that case rather
    than for extras added to a base of zero, which would silently produce a
    wrong number and an acknowledgement that confidently states it.
    """
    date.fromisoformat(week_start)
    household = _household_composition()
    conn = get_conn()
    prefs = conn.execute(
        "SELECT cuisine_preferences_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    plan = conn.execute(
        "SELECT id, status, intake_id FROM weekly_plans WHERE household_id = ? AND week_start_date = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(), week_start),
    ).fetchone()
    intake_row = _current_intake_row(conn, week_start)
    conn.close()

    saved_cuisines = json.loads(prefs["cuisine_preferences_json"]) if prefs else []
    intake = _intake_row_to_dict(intake_row) if intake_row else None
    # "In flight" means somebody has answered something for this week that
    # hasn't been turned into a plan yet. Once a plan has been generated
    # from this revision, carrying on from it is a redo, not a join.
    in_flight = bool(intake and (not plan or plan["intake_id"] != intake["intake_id"]))

    return {
        "week_start": week_start,
        "week_label": _weekly_plan._format_week_range(week_start),
        "days": [
            {
                "date": d,
                "weekday": date.fromisoformat(d).strftime("%A"),
                "short": date.fromisoformat(d).strftime("%a"),
                "day_of_month": date.fromisoformat(d).day,
                "hint": hint,
            }
            for d, hint in (
                (d, _observed_day_patterns(week_start).get(d, "")) for d in _week_dates(week_start)
            )
        ],
        "household": household,
        # False when nobody's age group is recorded — see the docstring.
        "household_known": bool(household["adults"] or household["children"]),
        "cuisines": saved_cuisines or ONBOARDING_CUISINES,
        "cuisines_are_fallback": not saved_cuisines,
        "intake": intake,
        "in_flight": in_flight,
        "plan_exists": bool(plan),
        "plan_id": plan["id"] if plan else None,
        "plan_status": plan["status"] if plan else None,
    }
