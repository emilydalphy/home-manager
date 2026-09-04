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
from . import rhythm as _rhythm
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
    return period_dates(week_start, 7)


def period_dates(start_date: str, day_count: int = 7) -> list[str]:
    """
    The ISO dates of a planning period: `day_count` days from `start_date`,
    inclusive. `_week_dates` is this with day_count pinned to 7.

    Exists because the old idiom for "the days of a shorter window" was
    `_week_dates(start)[:day_count]`, which silently CAPS at seven — fine
    while every period was a Monday week or a part of one, wrong the moment
    a household plans Thursday to next Thursday (Loop Board "Planning
    periods, not weeks"). That slice returned 7 days for an 8-day period and
    the eighth day would have been generated for, never audited, and never
    rendered. Slicing a list can't express a window longer than the list, so
    the window is built at the length it actually is.

    day_count below 1 yields no dates rather than raising: a plan that has
    surrendered every one of its days to a newer period (see
    retire_overlapping_plans) is a real, queryable row with an empty window,
    and every caller here loops over the result.
    """
    start = date.fromisoformat(start_date)
    return [(start + timedelta(days=i)).isoformat() for i in range(max(0, day_count))]


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
    day_count: int = 7,
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

    day_count is the length of the PERIOD these answers are for (Loop Board
    "Planning periods, not weeks"), defaulting to the seven this always
    assumed. It is not cosmetic: the in-range check below is the reason a
    household planning Thursday to next Thursday could not tag the eighth
    day of their own period at all — the save was refused outright, so
    question 1 could not be answered and the whole flow stopped. Found by
    running the round trip, not by reading the code.
    """
    date.fromisoformat(week_start)  # fail loudly on a malformed week
    week_days = set(period_dates(week_start, day_count))
    for day, tags in (night_tags or {}).items():
        date.fromisoformat(day)  # keyed by ISO date, never by weekday
        # A tag on a date outside the PERIOD it's being saved for would
        # produce a planned_empty entry stranded outside the plan's days —
        # visible nowhere, and impossible to clear from any screen. The
        # check stays; only its idea of the window widened.
        if day not in week_days:
            raise ValueError(
                f"{day} isn't in the {day_count}-day period starting {week_start}."
            )
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
            saved = _intake_row_to_dict(row)
            _sync_guest_attendance(saved)
            return saved
        except sqlite3.IntegrityError:
            # Somebody took this revision number between our read and our
            # write. Undo our supersede and start again from what they left.
            conn.rollback()
            if attempt == 4:
                raise
        finally:
            conn.close()


def _sync_guest_attendance(intake: dict) -> None:
    """
    Push the "Hosting guests" answers into attendance, so a bigger table is
    the SAME fact as a smaller one rather than a parallel notion of it.

    Emily's deepened model unifies guests into attendance: members present
    ± guests. Without this, "hosting 3 on Saturday" would live only in
    week_intake.guest_counts_json, where the grocery path can't see it —
    which is exactly how the guests chip's "and shop for that" promise
    ends up depending entirely on the model choosing to write bigger
    quantities. Writing it here means the headcount reaches groceries
    structurally, the same way a presence toggle does.

    Only dinner: the guest steppers are a night-tag follow-up, and a night
    tag is a dinner concept in this app. Deliberately tolerant of failure —
    a household mid-onboarding may have no members yet, and an intake save
    must not fail over a headcount echo.
    """
    from . import attendance as _attendance

    for day, extras in (intake.get("guest_counts") or {}).items():
        if not isinstance(extras, dict):
            continue
        total_guests = int(extras.get("adults", 0) or 0) + int(extras.get("children", 0) or 0)
        try:
            current = _attendance.get_slot_attendance(day, "dinner")
            if current["guest_count"] == total_guests:
                continue
            if total_guests == 0 and not current["explicit"]:
                continue
            _attendance.set_guest_count(day, "dinner", total_guests)
        except ValueError:
            continue


def _observed_day_patterns(week_start: str, day_count: int = 7) -> dict:
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
    A period longer than a week can repeat a weekday (Thursday to next
    Thursday has two Thursdays). The hints are keyed by DATE, and the same
    weekday's hint simply lands on both — which is right: "Tuesdays are
    tee-ball" is true of every Tuesday in the window.
    """
    dates = period_dates(week_start, day_count)
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


def _rhythm_packed_lunch_suggestions(week_start: str, day_count: int = 7) -> list[dict]:
    """
    Per-day packed-lunch suggestions derived from household rhythm (Loop
    Board "Onboarding: household rhythm..."): "the weekly intake's 'which
    lunches leave the house?' becomes conditional — pre-answered by
    rhythm". Only ever a SUGGESTION for the intake screen's prefill (the
    actual answer, once given, lives in week_intake.packed_lunch_days) —
    the household can always override it there.

    Per adult member with a lunch_location fact on record, resolves their
    effective location for that weekday (a per-weekday override if one's
    been learned, else the standing answer — see
    rhythm.effective_lunch_location). A day is suggested "packed" only when
    at least one adult resolves 'out' and none resolve 'home' — a mixed
    household (one home, one out) is a real judgment call about what
    "packed lunch day" even means for two people eating differently, so
    it's surfaced as a mix rather than the suggestion silently picking a
    side; flagged here as a product decision for whoever builds the
    prefill UI, not resolved by this function. A member whose rhythm was
    never set contributes to neither bucket (never asked yet is not the
    same as 'varies', which IS a real answer and also contributes to
    neither bucket for this specific suggestion, since it doesn't clearly
    say home or out).

    Returns [] entirely once no adult has any lunch_location on record —
    a household that hasn't done rhythm onboarding gets no suggestion, not
    a wrong one.

    day_count covers a planning period that isn't seven days (Loop Board
    "Planning periods, not weeks"). It defaults to 7, so every existing
    caller asks the same question; the generation path passes the real
    length so a longer period's last days get suggestions too, and a
    shorter one stops suggesting for days nobody asked to plan.
    """
    rhythm = _rhythm.get_household_rhythm()["lunch_location"]
    if not rhythm:
        return []
    dates = period_dates(week_start, day_count)
    suggestions = []
    for d in dates:
        weekday = date.fromisoformat(d).strftime("%A")
        out_members, home_members, varies_members = [], [], []
        for member_name in rhythm:
            location = _rhythm.effective_lunch_location(member_name, weekday)
            if location == "out":
                out_members.append(member_name)
            elif location == "home":
                home_members.append(member_name)
            elif location == "varies":
                varies_members.append(member_name)
        if not (out_members or home_members or varies_members):
            continue
        suggestions.append({
            "date": d,
            "weekday": weekday,
            "out": out_members,
            "home": home_members,
            "varies": varies_members,
            "suggested_packed": bool(out_members) and not home_members,
        })
    return suggestions


def get_week_intake_prefill(week_start: str, day_count: int = 7) -> dict:
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

    day_count is the length of the planning period being asked about (Loop
    Board "Planning periods, not weeks"), defaulting to the seven this
    always assumed. It matters more here than almost anywhere else: these
    screens ask the household to tag each day, and asking about seven days
    when they chose four is asking four real questions and three imaginary
    ones — whose answers would then be saved as intake for days no plan
    covers, and read back the next time that week IS planned.
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
        # The period's real dates, not seven days from its start. This is
        # the eyebrow above the questions ("Question 1 of 2 · Sep 10–17"),
        # so a 7-day label over an 8-day set of day cards would have the
        # screen naming a window it is visibly not asking about.
        "week_label": _weekly_plan._format_period_range(week_start, day_count),
        "days": [
            {
                "date": d,
                "weekday": date.fromisoformat(d).strftime("%A"),
                "short": date.fromisoformat(d).strftime("%a"),
                "day_of_month": date.fromisoformat(d).day,
                "hint": hint,
            }
            for d, hint in (
                (d, _observed_day_patterns(week_start, day_count).get(d, "")) for d in period_dates(week_start, day_count)
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
        # Loop Board "Onboarding: household rhythm..." — a suggestion only,
        # not an answer; see _rhythm_packed_lunch_suggestions.
        "rhythm_packed_lunch_suggestions": _rhythm_packed_lunch_suggestions(week_start, day_count),
        # The period these questions are about, echoed back so the screen
        # can name it ("Sep 11-18") instead of calling every window "your
        # week" regardless of what the household actually picked.
        "period_start_date": week_start,
        "day_count": day_count,
    }
