"""
The weekly plan as an object: slots, the menu view, approval, and swaps.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from ..db import get_conn
from ._shared import household_id, require_household_row
from . import coordination as _coordination
from . import grocery as _grocery
from . import meal_plans as _meal_plans
from . import notifications as _notifications
from . import recipes as _recipes
from . import week_intake as _week_intake


logger = logging.getLogger("home_manager")

WEEK_SLOTS = ("breakfast", "lunch", "dinner")

# The longest period the app will plan in one go. Not a data-model limit —
# nothing below cares — but a guard on the generation call, which asks the
# model for every day at once and is already the slowest thing in the app at
# seven. Named rather than inlined so the API, the UI and the tool schema all
# refuse the same number.
MAX_PERIOD_DAYS = 28

# The SQL form of plan_period(), for the two places that have to resolve the
# period inside a query rather than in Python (see _current_weekly_plan_row).
# Kept beside the Python version because they have to agree exactly, and a
# drift between them is invisible: the query would simply return a different
# plan than every other reader thinks is current.
_SQL_PERIOD_START = "COALESCE(NULLIF(content_start_date, ''), week_start_date)"
_SQL_PERIOD_LAST_OFFSET = (
    "(CASE WHEN content_start_date = '' AND day_count = 0 THEN 6 ELSE day_count - 1 END)"
)


def plan_period(plan) -> tuple[str, int]:
    """
    The (start_date, day_count) a plan actually covers — the ONE place the
    unset sentinels are resolved, so every screen, query and audit agrees on
    which days belong to a plan.

    The legacy sentinel is BOTH columns unset together — content_start_date
    '' AND day_count 0. That is how a row written before Loop Board
    "Planning periods, not weeks" looks, and it resolves to seven days from
    week_start_date: exactly what it has always meant. Nothing backfills
    those columns, on purpose — the sentinel IS the old meaning, and
    rewriting it into explicit values is the only way this migration could
    turn a correct row into a wrong one.

    It has to be BOTH, not just day_count, and that distinction is
    load-bearing rather than fussy. retire_overlapping_plans writes
    day_count 0 to mean "this plan surrendered every one of its days" — the
    opposite of seven. Read as the legacy sentinel, a fully retired plan
    claimed a whole week again the moment anything let it back past a
    `status != 'retired'` filter, and one route did (approving a week
    resolved to the retired row and set its status back to 'approved').
    A retired plan therefore keeps its old START, so the pair reads
    unambiguously: a start with a zero count is an empty period, and only
    the two-unset pair means seven.

    Takes either a sqlite3.Row or a dict, because the plan travels as both
    (rows straight from a query, dicts out of get_weekly_plan).
    """
    keys = plan.keys() if hasattr(plan, "keys") else plan
    raw_start = (plan["content_start_date"] if "content_start_date" in keys else "") or ""
    raw_count = (plan["day_count"] if "day_count" in keys else 0) or 0
    if not raw_start and not raw_count:
        return plan["week_start_date"], 7
    return (raw_start or plan["week_start_date"]), max(0, raw_count)


def period_end_date(start_date: str, day_count: int) -> str:
    """
    The LAST day of a period, inclusive — the form every overlap test and
    every date range in this app is written in. A zero-day period (one that
    has surrendered everything, see retire_overlapping_plans) returns the day
    BEFORE its start, which is what makes `start <= d <= end` correctly match
    nothing rather than accidentally matching the start day.
    """
    return (date.fromisoformat(start_date) + timedelta(days=day_count - 1)).isoformat()


def periods_overlap(a_start: str, a_days: int, b_start: str, b_days: int) -> list[str]:
    """
    The dates two periods have in common, in order — empty when they don't
    touch. Returned as the actual dates rather than a bool because every
    caller needs them anyway: the one-plan-per-day rule is enforced by
    retiring exactly these days, not by knowing that an overlap exists.
    """
    if a_days < 1 or b_days < 1:
        return []
    lo = max(a_start, b_start)
    hi = min(period_end_date(a_start, a_days), period_end_date(b_start, b_days))
    if lo > hi:
        return []
    span = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1
    return _week_intake.period_dates(lo, span)


def clear_plan_slot(weekly_plan_id: int, meal_date: str, slot: str) -> int:
    """
    Remove whatever is currently occupying one slot of a plan, reversing any
    grocery contribution it made first. Returns how many entries went.

    Exists because a slot must hold exactly ONE entry. The generator can be
    told not to plan a dinner for a night nobody is home and plan one
    anyway; without clearing first, the deliberate `planned_empty` row lands
    *beside* the model's meal rather than instead of it, and approval then
    buys ingredients for a night the household was promised nothing would be
    bought for. Which of the two rows a screen happens to show is incidental
    — the shopping list is the part that isn't.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? "
        "AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, meal_date, slot, household_id()),
    ).fetchall()
    conn.close()
    for row in rows:
        # Same care swap_meal_in_plan takes — anything this entry put on the
        # list comes back off, and anything already in a cart is left alone.
        _grocery._reverse_meal_grocery_contributions(row["id"])
    if rows:
        conn = get_conn()
        conn.execute(
            "DELETE FROM meal_plan_entries WHERE id IN (%s)" % ",".join("?" * len(rows)),
            tuple(r["id"] for r in rows),
        )
        conn.commit()
        conn.close()
    return len(rows)


def plan_slot_empty(
    weekly_plan_id: int,
    meal_date: str,
    slot: str,
    reason: str,
    derived_from: dict | None = None,
) -> dict:
    """
    Record a slot as deliberately empty — `planned_empty`.

    Not a gap and not a question. This is a slot that needs no decision and
    must NEVER be offered to the household as one. Two things produce it:
    a dinner on a night nobody is home ("You're out — I've planned nothing
    and bought nothing"), and a meal category the household has asked for
    zero of.

    `reason` is what the draft screen shows in place of a meal, so it has
    to read as a statement, never as an apology or an ask.
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, reasoning, derived_from_json) "
        "VALUES (?, ?, ?, ?, 'planned_empty', ?, ?)",
        (household_id(), meal_date, slot, weekly_plan_id, reason, json.dumps(derived_from or {})),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return {"entry_id": entry_id, "date": meal_date, "slot": slot, "slot_state": "planned_empty", "reason": reason}


def plan_slot_open(
    weekly_plan_id: int,
    meal_date: str,
    slot: str,
    open_reason: str,
    options: list[dict] | None = None,
    derived_from: dict | None = None,
) -> dict:
    """
    Record a slot as `open` — a decision the app is genuinely handing back.

    `open_reason` is a full sentence naming the CONSTRAINT that caused it,
    not an apology: "Wednesday I'd rather ask than guess: after Monday's
    chili, everything I have under 20 minutes repeats something you've just
    eaten." Naming the constraint is what makes the ask read as diligence
    rather than failure.

    An open slot is still a slot. What it must never be is absent — a
    silently missing slot is the bug this whole state exists to make
    impossible.
    """
    if not (open_reason or "").strip():
        raise ValueError("An open slot needs a reason naming the constraint that caused it.")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, open_reason, derived_from_json) "
        "VALUES (?, ?, ?, ?, 'open', ?, ?)",
        (
            household_id(), meal_date, slot, weekly_plan_id, open_reason,
            json.dumps({**(derived_from or {}), "options": options or []}),
        ),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return {
        "entry_id": entry_id, "date": meal_date, "slot": slot,
        "slot_state": "open", "open_reason": open_reason, "options": options or [],
    }


def get_meal_planning_preferences() -> dict:
    """
    Everything the revisitable setup screen shows: the per-category meal
    counts, and every preference the household has told the app so far,
    each in a shape the screen can edit inline.

    The point of this screen is that nothing is locked in from when they
    signed up. So this deliberately returns the FULL set rather than only
    what onboarding happened to ask — a preference the app is acting on but
    won't show is one the household can't correct.
    """
    conn = get_conn()
    prefs = conn.execute(
        "SELECT * FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    conn.close()

    def field(name, default):
        return prefs[name] if prefs else default

    return {
        "meal_counts": {
            "breakfasts_per_week": field("breakfasts_per_week", 7),
            "lunches_per_week": field("lunches_per_week", 7),
            "dinners_per_week": field("dinners_per_week", 7),
            "snacks_per_week": field("snacks_per_week", 3),
        },
        "dislikes": json.loads(field("dislikes_json", "[]")),
        "protein_preferences": json.loads(field("protein_preferences_json", "{}")),
        "cuisine_preferences": json.loads(field("cuisine_preferences_json", "[]")),
        "kitchen_kit": json.loads(field("kitchen_kit_json", "[]")),
        "repeats_tolerance": field("repeats_tolerance", ""),
        "weeknight_max_minutes": field("weeknight_max_minutes", 0),
        "cooking_time_preference": field("cooking_time_preference", ""),
        "table_style": field("table_style", ""),
        "eating_style": field("eating_style", ""),
        "novelty_preference": field("novelty_preference", "balanced"),
        "typical_week": field("typical_week", ""),
        "notes": field("notes", ""),
    }


def suggest_planning_period(from_date: str = "") -> dict:
    """
    The period the app offers by default — where "this week" starts for
    THIS household, rather than where the calendar says a week starts.

    Read from the rhythm the household already gave at onboarding
    (household_rhythm.planning_anchor, "when do you want your week ready?"),
    which until now was stored and acted on nowhere — its own setter says
    so. It is a cadence, not a weekday, so the mapping is a judgment call
    and is written here rather than inferred:

    - 'sunday_before' — planned and shopped before the week begins. Their
      week IS the Monday week; anchoring anywhere else would put the shop
      in the middle of it. Monday-anchored, seven days. Also the answer for
      a household that has never said (no rhythm on record), which is what
      keeps this a no-op for everyone who predates it.
    - 'midweek' and 'as_we_go' — households whose planning does not line up
      with a Monday at all. Seven days from TODAY. For 'midweek' that is
      the whole point; for 'as_we_go' a Monday anchor is the least
      meaningful boundary there is, since nothing about their week begins
      there.

    This is a SUGGESTION and nothing more: it seeds the default on the plan
    screen, and every one of its parts is overridable by picking a start
    date and a length. The rule Pomona is actually defending is that the
    household is never forced into a week they didn't choose — a default
    that guesses better is not the same as a constraint that guesses less.
    """
    today = date.fromisoformat(from_date) if from_date else date.today()
    anchor = (_rhythm_anchor() or "sunday_before")
    if anchor == "sunday_before":
        start = today - timedelta(days=today.weekday())
    else:
        start = today
    return {
        "start_date": start.isoformat(),
        "day_count": 7,
        "planning_anchor": anchor,
        "label": _format_period_range(start.isoformat(), 7),
        "is_monday_anchored": start.weekday() == 0,
    }


def _rhythm_anchor() -> str:
    """
    The household's stored planning_anchor, or '' if it has never answered.
    Read straight from household_rhythm rather than through
    get_household_rhythm, which assembles the whole six-fact picture (and
    reaches into members) to answer a question about one string.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM household_rhythm WHERE household_id = ? AND fact_type = 'planning_anchor' "
        "AND weekday = '' ORDER BY id DESC LIMIT 1",
        (household_id(),),
    ).fetchone()
    conn.close()
    return (row["value"] if row else "") or ""


def _live_plan_covering(conn, day: str):
    """The non-retired plan whose period contains `day`, or None."""
    return conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        f"AND date({_SQL_PERIOD_START}) <= date(?) "
        f"AND date({_SQL_PERIOD_START}, '+' || {_SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(), day, day),
    ).fetchone()


def get_week_planning_nudge() -> dict:
    """
    Whether to offer to plan a week, and which one — the Sunday nudge on
    Today (design_handoff_plan_the_week, DECISIONS.md #6).

    Two cases, in priority order:

    1. The week the household is CURRENTLY LIVING IN has no plan at all.
       That's the more pressing one, and it's offered any day of the week —
       waiting until Sunday to mention that this week was never planned
       would be absurd.
    2. Otherwise, from Saturday onward, the week that starts next Monday.
       The design asks for Sunday morning; Saturday is included because
       this is in-app only, not real push (there is no scheduler and no
       push infrastructure — see schema.sql on notification_dismissals), so
       the nudge is only ever seen when the app is opened. Starting a day
       early means a household that doesn't open it on Sunday still gets
       the offer before the week begins, rather than on the Monday it was
       meant to prepare for.

    Suppressed once dismissed, and the dismissal key is the week itself —
    so "I won't ask again this week" is literally true, and next week's
    offer isn't silenced by this week's dismissal. Also suppressed once
    that week has a plan: there is nothing left to offer.
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    suggestion = suggest_planning_period()

    conn = get_conn()
    dismissed = _notifications._dismissed_keys(conn)
    covering = _live_plan_covering(conn, today.isoformat())
    planned_week_keys = {
        row["week_start_date"]
        for row in conn.execute(
            "SELECT DISTINCT week_start_date FROM weekly_plans WHERE household_id = ? AND status != 'retired'",
            (household_id(),),
        ).fetchall()
    }
    conn.close()

    target = None
    target_days = suggestion["day_count"]
    is_current = False
    if covering is None and this_monday.isoformat() not in planned_week_keys:
        # Nothing covers today. The filing-key half of that test is what
        # keeps a part-week honest: a plan filed under this Monday whose
        # content deliberately starts on the Wednesday the household joined
        # has NOT left Monday unplanned in any sense worth nudging about —
        # those days went by before the household existed here. Without it,
        # every mid-week onboarding would be met by an immediate offer to
        # re-plan the week it had just been given.
        target, is_current = date.fromisoformat(suggestion["start_date"]), True
    elif covering is not None:
        # The generalisation of "from Saturday onward, offer next week".
        # A period ends on some day E; the offer opens two days before E,
        # which for a Monday-to-Sunday week is exactly Saturday — the same
        # day, for the same reason (this nudge is only ever seen when the
        # app is opened, so it has to be early enough to be seen at all).
        # Now it is right for a period of any length or start.
        cover_start, cover_days = plan_period(covering)
        cover_end = date.fromisoformat(period_end_date(cover_start, cover_days))
        if (cover_end - today).days <= 1:
            following = cover_end + timedelta(days=1)
            if _plan_covers_any(following.isoformat(), target_days) is None:
                target = following

    if target is None:
        return {"show": False, "week_start": None}
    week_start = target.isoformat()
    if f"plan_week_nudge:{week_start}" in dismissed:
        return {"show": False, "week_start": week_start, "dismissed": True}
    return {
        "show": True,
        "week_start": week_start,
        "week_label": _format_period_range(week_start, target_days),
        "day_count": target_days,
        "is_current_week": is_current,
        "dismiss_key": f"plan_week_nudge:{week_start}",
    }


def _plan_covers_any(start_date: str, day_count: int) -> int | None:
    """
    The id of a live plan already holding any day of the given period, or
    None. Used to stop the nudge offering a period the household has
    already planned — the old test asked whether a plan was FILED under that
    Monday, which a Thursday-to-Thursday period covering the same days is
    not.
    """
    found = find_overlapping_plans(start_date, day_count)
    return found[0]["weekly_plan_id"] if found else None


def _week_headline(plan: dict, days: list[dict], intake: dict | None) -> str:
    """
    The one line above the draft. One line, no recap — the per-slot reasons
    carry the detail, and the assistant never lists what it did.

    It says at most two things, in priority order:

    1. That there's a decision waiting. An open slot is the only thing on
       this screen the household has to act on, so it outranks everything.
    2. DECISIONS.md #1 — when the week's tags leave fewer dinners than the
       household's usual count, the tags win, and the app says so ONCE
       rather than shorting them silently. Not a question: asking would
       turn a tagging screen into a negotiation whose answer is nearly
       always "yes, obviously".

    With neither, it just says the week is here. There is deliberately no
    third clause: a headline that grows a sentence per feature is the recap
    this rule exists to prevent.
    """
    open_days = [
        date.fromisoformat(d["date"]).strftime("%A")
        for d in days
        for slot in WEEK_SLOTS
        if (d.get(slot) or {}).get("state") == "open"
    ]
    if len(open_days) == 1:
        return f"Your week’s here — there’s one night I’d like your call on."
    if open_days:
        return f"Your week’s here — there are {len(open_days)} slots I’d like your call on."

    # The baseline is the seven nights of the week, NOT
    # preferences_snapshot's dinner count: since that count means how many
    # DISTINCT dinners to plan rather than how many nights to plan one,
    # comparing it against nights cooked would be comparing two different
    # things and would fire on weeks with nothing wrong with them.
    #
    # And this only speaks when the week's TAGS caused the reduction, which
    # is the case DECISIONS.md #1 is actually about. A household that set
    # its own counts to zero already knows; being told about it is not news.
    night_tags = (intake or {}).get("night_tags") or {}
    because = []
    for day, tags in sorted(night_tags.items()):
        weekday = date.fromisoformat(day).strftime("%A")
        if "out" in tags:
            because.append(f"you’re out {weekday}")
        elif "left" in tags:
            because.append(f"it’s leftovers {weekday}")
    cooked = sum(1 for d in days if (d.get("dinner") or {}).get("state") == "planned")
    if because and cooked and cooked < len(days):
        return (
            f"That’s {cooked} dinners you’ll cook this week rather than {len(days)}"
            f" — {' and '.join(because[:2])}."
        )
    return "Your week’s here."


def resolve_open_slot(weekly_plan_id: int, meal_date: str, slot: str, choice: str) -> dict:
    """
    Settle a slot the app handed back. `choice` is what the household
    picked — one of the offered options, or anything they typed instead.

    The open row is replaced by a real planned meal, so the slot moves from
    'open' to 'planned' rather than accumulating two rows for one slot. Its
    provenance records that a person settled it, which is worth keeping:
    "the app asked and they answered" is a different thing from "the app
    chose", and only one of them is evidence about the household's taste.

    A choice that declines to plan anything at all ("Takeout, don't plan
    it") is honoured as exactly that — a planned takeout entry, not a
    silent gap and not a slot left open forever.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, slot_state, open_reason FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ? LIMIT 1",
        (weekly_plan_id, meal_date, slot, household_id()),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No {slot} slot on {meal_date} in that plan.")
    if not (choice or "").strip():
        raise ValueError("A choice is needed to settle this slot.")
    # A deliberately empty slot is not a question, and settling one would
    # mean planning — and buying — for a night the household said they're
    # out. The screens never offer it; this makes that true of the API too,
    # rather than of the UI alone. Changing your mind about being out is a
    # change to the week's ANSWERS, so it belongs in the questions, not here.
    if row["slot_state"] == "planned_empty":
        raise ValueError(
            f"That {slot} is deliberately empty — nothing is planned or bought for it. "
            "Change the night's answer if you're in after all."
        )

    was_open = row["slot_state"] == "open"
    # Reverse anything the outgoing entry contributed before deleting it —
    # the same care swap_meal_in_plan takes. An open slot has contributed
    # nothing, but this also serves the "change my mind about an already
    # planned slot" path.
    _grocery._reverse_meal_grocery_contributions(row["id"])
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()

    result = _meal_plans.plan_meal(
        meal_date, choice.strip(), slot=slot, weekly_plan_id=weekly_plan_id,
        # Mirrors the plan's approved state, exactly as a swap does: settling
        # a slot in a draft leaves the shopping list alone, settling one in
        # an already-approved week keeps the list in step. Otherwise the
        # list would quietly lack the one meal the household chose by hand.
        add_ingredients_to_grocery_list=_weekly_plan_is_approved(weekly_plan_id),
        reasoning="you chose this one",
        derived_from={"constraint": "settled_by_household", "answered": row["open_reason"] or ""},
    )
    return {**result, "was_open": was_open}


def reopen_weekly_plan(weekly_plan_id: int) -> dict:
    """
    Reopen an approved week so it can be edited again — DECISIONS.md #2.

    It never removes anything from the shopping list. Groceries already
    added stay, and re-approving only adds what's new. That's deliberate:
    taking items off a list somebody may already have bought is worse than
    a slightly long list, and a true reversal would need "was this item
    actually bought?" tracking that doesn't exist.

    The approval receipt is cleared, because it no longer describes a
    settled week — but the grocery links are untouched, which is what makes
    the re-approval add only the difference.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    conn.execute(
        "UPDATE weekly_plans SET status = 'draft', approved_by = '', approved_at = NULL, "
        "approved_grocery_added = 0, approved_grocery_skipped = 0, updated_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {
        "weekly_plan_id": weekly_plan_id,
        "status": "draft",
        "was_approved": plan["status"] == "approved",
    }


def attach_intake_to_plan(weekly_plan_id: int, intake_id: int) -> dict:
    """
    Record which revision of the household's answers produced this plan.
    Set once, at generation. It's what makes "the week you had before you
    redid it" recoverable and "why did it plan that?" answerable.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET intake_id = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (intake_id, weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "intake_id": intake_id}


# derived_from.links_to's one agreed shape (see schema.sql and the
# generation prompt, both of which used to disagree with this and each
# other — Loop Board "the planner scheduled Wednesday as leftovers of
# Thursday's cook"). "entry_id:<n>" is also accepted and resolved below,
# since one existing design doc used that form.
_LINKS_TO_DATE_SLOT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):(breakfast|lunch|dinner)$")
_LINKS_TO_ENTRY_ID_RE = re.compile(r"^entry_id:(\d+)$")

# Offered when a leftovers night gets reopened — genuinely generic, since
# by the time this runs there's no real recipe recommendation behind any
# of them, unlike the model's own open slots. "Takeout, don't plan it"
# matches the exact phrase the generation schema already promises the
# household for this kind of honest last resort.
_LEFTOVER_REPAIR_OPTIONS = [
    {"label": "Something quick", "meta": "20 min or less"},
    {"label": "Cook something fresh", "meta": ""},
    {"label": "Takeout, don’t plan it", "meta": ""},
]


def _join_with_and(items: list[str]) -> str:
    """
    "Tuesday", "Tuesday and Thursday", or "Tuesday, Thursday, and Friday" —
    the prose join for however many nights end up sharing one source's
    leftovers.
    """
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _resolve_leftover_source(links_to: str, by_date_slot: dict, by_id: dict):
    """
    The row `links_to` claims to point at, or None if it doesn't resolve to
    anything in THIS plan. Deliberately scoped to `by_date_slot`/`by_id` —
    both built from a single plan's rows — so an entry_id belonging to a
    different plan resolves to nothing rather than reaching across plans.
    """
    m = _LINKS_TO_DATE_SLOT_RE.match(links_to)
    if m:
        return by_date_slot.get((m.group(1), m.group(2)))
    m = _LINKS_TO_ENTRY_ID_RE.match(links_to)
    if m:
        return by_id.get(int(m.group(1)))
    return None


def repair_leftover_chains(weekly_plan_id: int) -> dict:
    """
    Enforce that every leftovers entry actually eats a real, earlier cook —
    the fix for "the planner scheduled Wednesday as leftovers of Thursday's
    cook" (Loop Board). derived_from.links_to has existed since the
    generation prompt started asking the model to set it, but nothing ever
    read it back: a leftovers night pointing at a date that hadn't happened
    yet saved exactly as written, same as one pointing at a date outside
    the plan or at a night nothing was actually cooked. Two pathways write
    a links_to (the household's `left` night tag, and the model's own
    cook-once-eat-twice pairing) and both land in this same unchecked
    field, so both get checked here.

    Called from _finish_week_slots AFTER duplicates are deduped and BEFORE
    the final audit_plan_slots, for two separate reasons that both land on
    "run it here": a slot repaired here becomes `open`, which the audit
    must see as present, not question a second time as missing — and a
    slot that still has two rows for it must never reach this function,
    because repairing one of them clears the WHOLE slot (both rows), not
    just the bad one. See _finish_week_slots' docstring for why dedupe now
    runs before this rather than after.

    A chain is valid only if ALL of:
    - links_to parses (see _resolve_leftover_source);
    - the source date is strictly EARLIER than this entry's date;
    - the source is a `planned` entry with a real meal, in THIS plan;
    - the source slot is lunch or dinner — a dinner claiming a breakfast's
      leftovers is a type mismatch as backwards as the bug this exists to
      catch, just sideways instead of in time. (A breakfast eating an
      earlier breakfast's leftovers still isn't allowed, since the rule is
      about the SOURCE, not a match between the two slots — the smallest
      rule that rejects the reported shape without inventing a same-slot
      requirement nobody asked for.)
    - the source is not itself a leftovers entry (chaining leftovers off
      leftovers is exactly as backwards as the bug this exists to catch).

    On failure, the week is never reordered — reordering a night the
    household already saw a reasoning for is its own kind of surprise.
    Instead the slot is reopened: cleared and handed back as a real
    question, the same shape as any other open slot, with the failure kept
    on derived_from.repaired/original_links_to rather than silently
    dropped, so it stays traceable.

    On success, the SOURCE (the earlier cook) gets a note recorded on its
    own derived_from_json — no schema change needed for it — so the
    Cooker/plan screens can eventually say "make double." Quantities
    themselves are not scaled here; see cooker.py's batch_note for the
    component-based equivalent of that half. A single source can carry
    more than one leftovers night (Tuesday AND Thursday both eating
    Monday's cook), so make_double_for is always a list of "date:slot"
    targets, accumulated rather than overwritten — never assume it holds
    exactly one.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date, slot, slot_state, recipe_id, freeform_meal, derived_from_json "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()

    by_date_slot = {(r["date"], r["slot"]): r for r in rows}
    by_id = {r["id"]: r for r in rows}

    repaired = []
    confirmed = []
    for r in rows:
        if r["slot"] not in WEEK_SLOTS or r["slot_state"] != "planned":
            continue
        derived = json.loads(r["derived_from_json"] or "{}")
        links_to = (derived.get("links_to") or "").strip()
        if not links_to:
            continue

        source = _resolve_leftover_source(links_to, by_date_slot, by_id)
        issue = None
        if source is None:
            issue = "a meal I can’t find anymore"
        elif source["date"] >= r["date"]:
            issue = "a meal that hasn’t happened yet"
        elif source["slot_state"] != "planned" or not (source["recipe_id"] or source["freeform_meal"]):
            issue = "a night nothing was actually cooked"
        elif source["slot"] not in ("lunch", "dinner"):
            issue = "a breakfast"
        else:
            source_derived = json.loads(source["derived_from_json"] or "{}")
            if (source_derived.get("links_to") or "").strip():
                issue = "another leftovers night, not an actual cook"

        if issue:
            day_name = date.fromisoformat(r["date"]).strftime("%A")
            clear_plan_slot(weekly_plan_id, r["date"], r["slot"])
            repaired_derived = {k: v for k, v in derived.items() if k != "links_to"}
            repaired_derived["repaired"] = "leftovers_backwards"
            repaired_derived["original_links_to"] = links_to
            plan_slot_open(
                weekly_plan_id=weekly_plan_id,
                meal_date=r["date"],
                slot=r["slot"],
                open_reason=(
                    f"{day_name} I’d rather ask than guess: I’d pencilled in leftovers "
                    f"from {issue}, and there’s nothing to reheat. Pick something, or "
                    "I’ll cook fresh."
                ),
                options=_LEFTOVER_REPAIR_OPTIONS,
                derived_from=repaired_derived,
            )
            repaired.append({"date": r["date"], "slot": r["slot"], "original_links_to": links_to, "issue": issue})
        else:
            target = f"{r['date']}:{r['slot']}"
            conn = get_conn()
            existing = conn.execute(
                "SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (source["id"],)
            ).fetchone()
            source_derived = json.loads(existing["derived_from_json"] or "{}") if existing else {}
            # A list, not a scalar: one cook can feed more than one leftovers
            # night (Tuesday AND Thursday both eating Monday's chili), and an
            # overwrite here would silently drop every earlier one. Sorted by
            # date so the note reads in the order the nights actually fall,
            # regardless of the order this loop happens to visit them in.
            targets = source_derived.get("make_double_for") or []
            if isinstance(targets, str):  # tolerate the pre-fix scalar shape
                targets = [targets]
            if target not in targets:
                targets.append(target)
            targets.sort(key=lambda t: t.split(":")[0])
            day_names = [date.fromisoformat(t.split(":")[0]).strftime("%A") for t in targets]
            source_derived["make_double_note"] = (
                f"I’ll set aside a double batch tonight — {_join_with_and(day_names)} "
                f"{'eats' if len(day_names) == 1 else 'eat'} the leftovers."
            )
            source_derived["make_double_for"] = targets
            conn.execute(
                "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                (json.dumps(source_derived), source["id"]),
            )
            conn.commit()
            conn.close()
            confirmed.append({"date": r["date"], "slot": r["slot"], "source_entry_id": source["id"]})

    if repaired:
        logger.warning(
            "Week plan %s had %d backwards leftovers chain(s); reopened: %s",
            weekly_plan_id, len(repaired),
            ", ".join(f"{x['date']} {x['slot']} ({x['issue']})" for x in repaired),
        )
    return {"repaired": repaired, "confirmed": confirmed}


def _dedupe_duplicate_slots(weekly_plan_id: int, duplicated: list[dict]) -> None:
    """
    audit_plan_slots computes `duplicated` (two-or-more rows claiming one
    slot) but until now nothing consumed it — a latent bug found alongside
    the leftovers-ordering one, same shape: a check that runs and is
    ignored is no different from no check at all. Approving a week with a
    duplicated slot buys groceries for the same slot twice.

    Keeps the first-created row (lowest id — the order these were written
    in) for each duplicated (date, slot) and removes the rest, reversing
    any grocery contribution they made first, same care clear_plan_slot
    takes for a single slot.
    """
    if not duplicated:
        return
    conn = get_conn()
    for dup in duplicated:
        rows = conn.execute(
            "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? "
            "AND household_id = ? AND component_category IS NULL ORDER BY id ASC",
            (weekly_plan_id, dup["date"], dup["slot"], household_id()),
        ).fetchall()
        extras = rows[1:]
        for row in extras:
            _grocery._reverse_meal_grocery_contributions(row["id"])
        if extras:
            conn.execute(
                "DELETE FROM meal_plan_entries WHERE id IN (%s)" % ",".join("?" * len(extras)),
                tuple(r["id"] for r in extras),
            )
        logger.warning(
            "Week plan %s had %d entries for %s %s; kept the first, removed %d duplicate(s)",
            weekly_plan_id, dup["count"], dup["date"], dup["slot"], len(extras),
        )
    conn.commit()
    conn.close()


def audit_plan_slots(weekly_plan_id: int, day_count: int = 7, skip_days: int = 0) -> dict:
    """
    Check a generated week against the one rule it can't be allowed to
    break: every slot exists, and each is planned, planned_empty, or open —
    never absent, and never a row carrying no meal, no emptiness and no
    question.

    "Week generation silently leaves random meal slots empty" is a real
    reported bug, and its shape is exactly this: nothing anywhere asserted
    that a slot had to be there. Returns the offenders rather than raising,
    so the generator can fill them rather than fail the whole week over one.

    `day_count` is how many days were actually asked for — normally 7, but
    generate_weekly_plan takes it as a parameter and chat can ask for a
    short week. Auditing a 5-day request against 7 days would invent
    questions about Saturday and Sunday nobody asked to have planned.

    Both parameters are now the FALLBACK rather than the source of truth:
    since Loop Board "Planning periods, not weeks" a plan stores the period
    it was generated for, and this reads that when it's there. They still
    matter for a plan with no period on record, and passing them wrong can
    no longer silently audit the wrong days for a plan that has one.

    `skip_days` is the other half of that same idea, for a genuine
    part-week (Loop Board "Build a real part-week for households who
    onboard mid-week"): the plan is filed under this week's Monday
    (week_start_date, read from the row below) regardless of which day its
    content actually starts on, so a household onboarding on a Wednesday
    is audited against days 2 through 6 of that week (Wed-Sun), not days 0
    through 4 (Mon-Fri) — the days that have already gone by are simply
    never in scope, not present, not missing, not asked about. Defaults to
    0, which reproduces the exact previous behaviour for every ordinary
    caller.

    Duplicates matter as much as gaps: two rows for one slot is how a night
    nobody is home ends up with groceries bought for it. They're reported
    separately from `missing` because they need the opposite fix.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT week_start_date, content_start_date, day_count FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    rows = conn.execute(
        "SELECT date, slot, slot_state, recipe_id, freeform_meal, open_reason "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()

    # The plan's own stored period wins when it has one, because that is what
    # was actually generated; the skip_days/day_count parameters are the
    # pre-period way of saying the same thing and stay authoritative only for
    # a plan that has no period on record. Deriving the window from the row
    # rather than from the caller is also what makes an audit of an 8-day
    # Thursday-to-Thursday period cover all eight days: the old expression
    # sliced a 7-item list and could not have returned more than seven.
    stored_start, stored_count = plan_period(plan)
    if (plan["content_start_date"] or plan["day_count"]):
        dates = _week_intake.period_dates(stored_start, stored_count)
    else:
        dates = _week_intake._week_dates(plan["week_start_date"])[skip_days:skip_days + day_count]
    # Only the three real meals, and only within the days actually asked
    # for. Snacks ride along in the same table but aren't part of the
    # guarantee, and counting them made `present` exceed `expected` and
    # would have reported a duplicate snack as a broken week.
    in_scope = set(dates)
    seen: dict[tuple, int] = {}
    for r in rows:
        if r["slot"] not in WEEK_SLOTS or r["date"] not in in_scope:
            continue
        key = (r["date"], r["slot"])
        seen[key] = seen.get(key, 0) + 1
    missing = [
        {"date": d, "slot": s}
        for d in dates
        for s in WEEK_SLOTS
        if (d, s) not in seen
    ]
    duplicated = [
        {"date": d, "slot": s, "count": n} for (d, s), n in sorted(seen.items()) if n > 1
    ]
    # A row that claims to be planned but holds no meal, or claims to be
    # open but names no reason. Stored form of the same bug.
    hollow = [
        {"date": r["date"], "slot": r["slot"], "slot_state": r["slot_state"]}
        for r in rows
        if r["slot"] in WEEK_SLOTS and r["date"] in in_scope
        and ((r["slot_state"] == "planned" and not (r["recipe_id"] or r["freeform_meal"]))
             or (r["slot_state"] == "open" and not (r["open_reason"] or "").strip()))
    ]
    return {
        "weekly_plan_id": weekly_plan_id,
        "expected": len(dates) * len(WEEK_SLOTS),
        "present": len(seen),
        "missing": missing,
        "duplicated": duplicated,
        "hollow": hollow,
        "complete": not missing and not hollow and not duplicated,
    }


def get_plan_id_for_week(week_start_date: str) -> int | None:
    """
    The weekly_plans row id for one specific week's Monday, or None if that
    week has no plan yet. The week-scoped endpoints
    (design_handoff_plan_the_week/DATA_AND_API.md) are keyed by date, not
    plan id, so they need this to get back to a row.

    Deliberately NOT _current_weekly_plan_row: that answers "which plan is
    the household's current one," a different and week-agnostic question.
    Asking to approve Sep 1–7 must approve Sep 1–7 even if the current plan
    is a different week. Picks the most recently created row if a week
    somehow has more than one, which shouldn't happen but shouldn't 500
    either.

    A RETIRED plan is never returned, and that is the whole reason this
    function is not one line. Every week-scoped route resolves through here
    — /approve, /reopen, /slot — and a retired plan filed under the same key
    as its replacement was still reachable by all three. Approving it set
    its status back to 'approved', which is precisely the flag keeping it
    out of every live-plan query, so a single approve of a dead plan put two
    live plans on the same seven days. The one-plan-per-day rule cannot be
    enforced only where plans are created; it has to hold at every door that
    can bring one back to life.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM weekly_plans WHERE household_id = ? AND week_start_date = ? "
        "AND status != 'retired' ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(), week_start_date),
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def find_overlapping_plans(period_start: str, day_count: int, exclude_plan_id: int | None = None) -> list[dict]:
    """
    Every live plan of this household that holds at least one day inside the
    given period, with the days it holds. Read-only.

    Two callers, and they want it for opposite reasons.
    retire_overlapping_plans uses it to find what a new period has to take
    over. And it answers, without changing anything, "does this household
    already have plans that break the one-plan-per-day rule?" — which
    matters because the rule is NEW (Emily, 2026-09-04), not something the
    database has ever enforced. There is no uniqueness constraint on
    weekly_plans and never was; overlapping plans are ordinary existing
    data, resolved until now by _current_weekly_plan_row's newest-wins
    tiebreak. Nothing here or in the migration rewrites those. Deciding at
    startup which of a household's real, already-cooked-from plans to
    dismantle is not a migration's business, and doing it in the one
    database that matters (Emily's) with no undo is not a risk worth taking
    for tidiness.

    They are resolved the first time a period is generated over ANY of the
    days they share — retire_overlapping_plans deconflicts every live plan
    at once, not just each one against the new period, so a generation
    settles pre-existing clashes between old plans too. Until then they
    stand, and this reports them. Note the corollary: a pair of overlapping
    plans nowhere near anything newly planned stays overlapping, and only a
    person can decide which of those should lose days.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' ORDER BY id",
        (household_id(),),
    ).fetchall()
    conn.close()
    found = []
    for row in rows:
        if exclude_plan_id is not None and row["id"] == exclude_plan_id:
            continue
        other_start, other_days = plan_period(row)
        shared = periods_overlap(period_start, day_count, other_start, other_days)
        if shared:
            found.append({
                "weekly_plan_id": row["id"],
                "week_start_date": row["week_start_date"],
                "period_start_date": other_start,
                "day_count": other_days,
                "status": row["status"],
                "planning_mode": row["planning_mode"],
                "overlap_dates": shared,
            })
    return found


def _longest_run(days: list[str]) -> list[str]:
    """
    The longest contiguous stretch of consecutive dates in an ordered list.

    A period is a start plus a length, so a plan can only ever keep a
    CONTIGUOUS set of days. When a takeover leaves it days on both sides of
    the new period, this is the half it keeps. Ties go to the earlier run —
    arbitrary, but it has to be decided somewhere and deciding it here keeps
    the result reproducible rather than dependent on iteration order.
    """
    best: list[str] = []
    run: list[str] = []
    for day in days:
        if run and date.fromisoformat(day) - date.fromisoformat(run[-1]) == timedelta(days=1):
            run.append(day)
        else:
            run = [day]
        if len(run) > len(best):
            best = list(run)
    return best


def _plan_takeover(new_plan_id: int, period_start: str, day_count: int) -> list[dict]:
    """
    Decide what every other live plan keeps and gives up — and decide ALL of
    it before anything is written.

    Two reasons it is separated from the writing.

    It is the only way to deconflict globally. Each plan's decision depends
    on what the plans newer than it kept, so the claims have to accumulate
    across the whole walk; handling one plan at a time against the new
    period alone is what let two of them be shortened onto the same resume
    date and invent a clash. Here, `claimed` starts as the new period's days
    and grows as each plan keeps its run, so a day can be awarded once.

    And it makes the destructive half short. retire_overlapping_plans still
    is not atomic — each plan's meals, prep tasks and grocery reversal
    commit before the next plan is touched — but every decision is settled
    first, so nothing can be destroyed on the strength of a calculation that
    then throws. **The remaining non-atomicity is real and known: a failure
    between two plans (a locked database, a killed process) leaves the first
    one's days genuinely gone. Worth Emily's eyes.**

    Returns one dict per affected plan, newest first, or [] when nothing
    overlaps — the ordinary case.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        "ORDER BY created_at DESC, id DESC",
        (household_id(),),
    ).fetchall()
    conn.close()

    new_days = set(_week_intake.period_dates(period_start, day_count))
    claimed = set(new_days)
    decisions = []
    for row in rows:
        if row["id"] == new_plan_id:
            continue
        other_start, other_days = plan_period(row)
        days = _week_intake.period_dates(other_start, other_days)
        if not days:
            continue
        available = [d for d in days if d not in claimed]
        if len(available) == len(days):
            # Untouched: it clashes with nothing, so it is not part of this
            # takeover at all and must not be rewritten or reported.
            claimed.update(days)
            continue

        # A component_based plan's entries carry no real date (they all sit
        # on the plan's week_start_date as a placeholder — see
        # meal_plan_entries.component_category), so there is no subset of
        # them corresponding to the days being taken over. It goes whole.
        component_based = row["planning_mode"] == "component_based"
        keep = [] if component_based else _longest_run(available)
        surrendered = [d for d in days if d not in keep]
        claimed.update(keep)
        decisions.append({
            "weekly_plan_id": row["id"],
            "status": row["status"],
            "component_based": component_based,
            "previous_start": other_start,
            "previous_day_count": other_days,
            "kept_start": keep[0] if keep else other_start,
            "kept_day_count": len(keep),
            "retired": not keep,
            "surrendered": surrendered,
            # A surrendered day the NEW period does not cover is a day
            # nothing has replaced. Computed against new_days rather than
            # against the period's start/end pair so it stays right for a
            # plan that gave up days on both sides of it.
            "orphaned": [d for d in surrendered if d not in new_days],
        })

    # A day another surviving plan still holds was never orphaned, whatever
    # the plan that gave it up thinks. Resolved here, once, now that every
    # decision is known — per-plan it could only have been guessed at.
    for decision in decisions:
        decision["orphaned"] = [d for d in decision["orphaned"] if d not in claimed]
    return decisions


def retire_overlapping_plans(new_plan_id: int, period_start: str, day_count: int) -> dict:
    """
    Make "no day has two plans" true rather than merely intended: every
    other plan holding a day inside this period gives that day up, and the
    groceries it put on the list for those days come back off.

    This is the enforcement half of Emily's one-plan-per-day rule
    (2026-09-04). "What's for dinner?" has to have exactly one answer, and
    before this it could have two — the loser being decided by a
    created_at tiebreak in one query while the other plan's ingredients
    stayed on the shopping list forever, bought for meals nobody would cook.

    It deconflicts the household's plans GLOBALLY, not just each old plan
    against the new one. That distinction was found by adversarial review
    and it matters: overlapping plans are ordinary pre-existing data (there
    has never been a uniqueness constraint), and shortening two of them
    independently pushed both onto the same resume date — five clashing days
    that did not exist before the takeover ran. So the rule is applied once,
    over every live plan at once: walk them newest-first, and each may keep
    only days nothing newer has already claimed. Newest-first is not
    arbitrary — it is the same tiebreak _current_weekly_plan_row has always
    used to decide which of two overlapping plans wins.

    What a loser keeps is then the longest contiguous run of days still
    available to it, because a period is a start plus a length and cannot
    have a hole in the middle:

    - Nothing left: it retires. status 'retired', day_count 0, and it keeps
      its old start so the pair reads as an empty period rather than as the
      legacy seven-day sentinel (see plan_period).
    - Days left only BEFORE the new period (the usual case — a new period
      starting partway through the current week): it ends the day before the
      new one starts, exactly as the ticket describes.
    - Days left only AFTER: it begins the day after the new period ends.
    - Days left on BOTH sides: it keeps the longer run, and the shorter one
      is orphaned — returned as `orphaned_dates` and logged, because this is
      the one case where the household loses planned days they did not ask
      to replace. There is no honest alternative within this model: a plan
      cannot survive with a gap punched through it, and retiring it whole
      would surrender those days plus the ones it could have kept. Flagged
      rather than hidden. **Worth Emily's eyes.**

    `orphaned_dates` excludes any day another surviving plan still holds,
    so the sentence a screen builds from it is true. Reporting a day as
    lost while a plan is still cooking from it is worse than not reporting.

    Groceries reconcile through _reverse_meal_grocery_contributions, the
    same per-meal reversal a swap and clear_weekly_plan already use — so
    the guarantee it carries holds here too: a line already moved to
    in_cart or purchased is LEFT ALONE. Somebody has bought that food. It
    is reported back as `grocery_kept_bought` rather than silently skipped,
    because "we already own three peppers" is the household's business and
    the only place that fact still exists after the meal is gone.

    A component_based plan surrenders ENTIRELY on any overlap. Its entries
    carry no real date (they all sit on the plan's week_start_date as a
    placeholder — see meal_plan_entries.component_category), so there is no
    subset of them that corresponds to the days being taken over; picking
    some to reverse would be inventing a day-assignment the plan
    deliberately doesn't have.

    Returns what happened, in enough detail for a screen to say it out loud.
    Never raises for "nothing overlapped" — that's the ordinary case, and
    it returns the same shape with empty lists.
    """
    result = {
        "new_plan_id": new_plan_id,
        "retired_plan_ids": [],
        "shortened_plan_ids": [],
        "surrendered_dates": [],
        "orphaned_dates": [],
        "meals_removed": 0,
        "grocery_removed": [],
        "grocery_trimmed": [],
        "grocery_kept_bought": [],
    }
    decisions = _plan_takeover(new_plan_id, period_start, day_count)
    if not decisions:
        return result

    for decision in decisions:
        other_id = decision["weekly_plan_id"]
        other_start, other_days = decision["previous_start"], decision["previous_day_count"]
        component_based = decision["component_based"]
        surrendered = decision["surrendered"]
        orphaned = decision["orphaned"]
        new_start_date = decision["kept_start"]
        new_day_count = decision["kept_day_count"]
        retired = decision["retired"]

        removal = _release_plan_days(other_id, surrendered, include_components=component_based)
        result["meals_removed"] += removal["meals_removed"]
        result["grocery_removed"].extend(removal["grocery_removed"])
        result["grocery_trimmed"].extend(removal["grocery_trimmed"])
        result["grocery_kept_bought"].extend(removal["grocery_kept_bought"])
        result["surrendered_dates"].extend(surrendered)
        result["orphaned_dates"].extend(orphaned)

        record = {
            "superseded_at": datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
            "by_plan_id": new_plan_id,
            "by_period": {"start_date": period_start, "day_count": day_count},
            # The period this plan HAD, so what it gave up stays legible
            # after the columns have been rewritten — the same reason
            # slot_needs.superseded_json stores the whole need rather than
            # its name.
            "previous_period": {"start_date": other_start, "day_count": other_days},
            "surrendered_dates": surrendered,
            "orphaned_dates": orphaned,
            "grocery_removed": removal["grocery_removed"],
            "grocery_trimmed": removal["grocery_trimmed"],
            "grocery_kept_bought": removal["grocery_kept_bought"],
        }
        conn = get_conn()
        conn.execute(
            "UPDATE weekly_plans SET content_start_date = ?, day_count = ?, status = ?, "
            "superseded_json = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
            (
                new_start_date, new_day_count,
                "retired" if retired else decision["status"],
                json.dumps(record), other_id, household_id(),
            ),
        )
        conn.commit()
        conn.close()
        (result["retired_plan_ids"] if retired else result["shortened_plan_ids"]).append(other_id)

    result["surrendered_dates"] = sorted(set(result["surrendered_dates"]))
    result["orphaned_dates"] = sorted(set(result["orphaned_dates"]))
    if result["orphaned_dates"]:
        logger.warning(
            "Plan %s left %d day(s) of an existing plan unplanned and unreplaced "
            "(a plan cannot keep a window with a hole in it, so it kept the longer side): %s",
            new_plan_id, len(result["orphaned_dates"]), ", ".join(result["orphaned_dates"]),
        )
    return result


def _release_plan_days(plan_id: int, dates: list[str], include_components: bool = False) -> dict:
    """
    Take a plan's meals for specific dates off it, reversing what each one
    put on the grocery list first.

    The reversal is _grocery._reverse_meal_grocery_contributions, per meal,
    exactly as clear_weekly_plan and swap_meal_in_plan do it — this is
    deliberately not a second implementation of the grocery-side logic. It
    also means the in_cart/purchased rule is inherited rather than
    re-decided: food someone has already bought is never yanked back off
    the list, so a takeover mid-shop cannot empty a cart.

    What IS added here is naming the kept lines. The reversal helper
    reports what it removed and trimmed but says nothing about what it
    declined to touch, so the already-bought items are read off the ledger
    BEFORE the reversal clears it — afterwards the link rows are gone and
    the fact is unrecoverable.

    include_components sweeps up a component_based plan's entries, which
    carry no real date and would otherwise survive their own plan's
    retirement — still on the grocery list, attached to a plan no screen
    shows. Only ever true for a plan being surrendered WHOLE, because a
    subset of undated components is not a thing that exists.
    """
    if not dates:
        return {"meals_removed": 0, "grocery_removed": [], "grocery_trimmed": [], "grocery_kept_bought": []}
    conn = get_conn()
    placeholders = ",".join("?" * len(dates))
    entry_ids = [
        r["id"] for r in conn.execute(
            f"SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? "
            f"AND (date IN ({placeholders})"
            + (" OR component_category IS NOT NULL)" if include_components else ")"),
            (plan_id, household_id(), *dates),
        ).fetchall()
    ]
    kept_bought = []
    if entry_ids:
        entry_placeholders = ",".join("?" * len(entry_ids))
        kept_bought = [
            r["item"] for r in conn.execute(
                f"SELECT DISTINCT g.item FROM meal_plan_grocery_links l "
                f"JOIN grocery_items g ON g.id = l.grocery_item_id "
                f"WHERE l.household_id = ? AND l.meal_plan_entry_id IN ({entry_placeholders}) "
                f"AND g.status != 'needed'",
                (household_id(), *entry_ids),
            ).fetchall()
        ]
    conn.close()

    removed_items, trimmed_items = [], []
    for entry_id in entry_ids:
        reversal = _grocery._reverse_meal_grocery_contributions(entry_id)
        removed_items.extend(reversal["removed_items"])
        trimmed_items.extend(reversal["trimmed_items"])
    if entry_ids:
        conn = get_conn()
        entry_placeholders = ",".join("?" * len(entry_ids))
        # Prep tasks describe prepping meals that no longer exist, the same
        # reasoning clear_weekly_plan applies when it empties a whole plan.
        conn.execute(
            f"DELETE FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id IN ({entry_placeholders})",
            (household_id(), *entry_ids),
        )
        conn.execute(
            f"DELETE FROM meal_plan_entries WHERE household_id = ? AND id IN ({entry_placeholders})",
            (household_id(), *entry_ids),
        )
        conn.commit()
        conn.close()
    return {
        "meals_removed": len(entry_ids),
        "grocery_removed": removed_items,
        "grocery_trimmed": trimmed_items,
        "grocery_kept_bought": kept_bought,
    }


def get_plan_id_for_date(meal_date: str) -> int | None:
    """
    The plan whose PERIOD contains a given day, or None.

    "Which plan does Thursday belong to?" used to be answered by snapping
    Thursday back to its Monday and looking up a plan filed under that key
    — which is right exactly as long as every plan is a Monday week. A
    Thursday-to-Thursday period is filed under its own Thursday, so the
    Monday snap looks up a key no plan has and reports the day unplanned.

    Under the one-plan-per-day rule at most one non-retired plan can match,
    so the ordering is a tiebreak for legacy overlaps only (newest wins,
    the same tiebreak _current_weekly_plan_row has always used).

    There is deliberately NO Monday fallback. An earlier version fell
    through to get_plan_id_for_week, which finds a plan by FILING KEY and
    therefore answered "yes, that plan" for days the plan no longer covers
    — a day orphaned by a takeover, or a day of a component plan that had
    retired whole. The caller that makes this dangerous is
    slot_needs._plan_id_for_date: an `away` declared for such a day attached
    to a dead or shortened plan, so apply_slot_needs_to_plan would never
    enforce it and the household would be sold food for a night they had
    said they were away. None is the honest answer for a day no live plan
    covers, and every caller already handles it — a need declared before a
    week is generated is the ordinary case.
    """
    date.fromisoformat(meal_date)
    conn = get_conn()
    row = conn.execute(
        f"SELECT id FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        f"AND date({_SQL_PERIOD_START}) <= date(?) "
        f"AND date({_SQL_PERIOD_START}, '+' || {_SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(), meal_date, meal_date),
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def _format_week_range(week_start_date: str) -> str:
    """
    A week as the design writes it: "Sep 1–7", or "Aug 30–Sep 5" when the
    seven days straddle a month. En dash, no padded day numbers, matching
    design_handoff_plan_the_week/COPY.md's own eyebrow strings. Used
    wherever a week has to be named in a sentence rather than shown as a
    grid — the Sunday nudge, the approval notification, the draft eyebrow.
    """
    return _format_period_range(week_start_date, 7)


def _format_period_range(start_date: str, day_count: int = 7) -> str:
    """
    A planning period as the design writes a week: "Sep 1–7", or
    "Aug 30–Sep 5" across a month boundary. _format_week_range is this with
    day_count pinned to 7, and every string it produced is byte-identical.

    A one-day period is written as the single date ("Sep 10") rather than
    "Sep 10–10", which is the only shape a range can't say sensibly.
    """
    start = date.fromisoformat(start_date)
    end = start + timedelta(days=max(1, day_count) - 1)
    start_month = start.strftime("%b")
    if start == end:
        return f"{start_month} {start.day}"
    if start.month == end.month:
        return f"{start_month} {start.day}–{end.day}"
    return f"{start_month} {start.day}–{end.strftime('%b')} {end.day}"


def set_planning_mode(mode: str) -> dict:
    """
    Set the household's standing weekly-planning mode: 'day_based' (default
    — one meal per day/slot) or 'component_based' (plan by category instead
    — a breakfast for the week, several proteins, several vegetables,
    carbs, a treat, a dip — for the household to assemble freely rather
    than a fixed day->meal mapping). This is household-level, not per-week
    — it applies to the next plan generated, and can be changed again any
    time, but a single already-generated plan stays whatever mode it was
    created under.
    """
    if mode not in ("day_based", "component_based"):
        raise ValueError("mode must be 'day_based' or 'component_based'.")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, planning_mode, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET planning_mode = excluded.planning_mode, updated_at = datetime('now')
        """,
        (household_id(), mode),
    )
    conn.commit()
    conn.close()
    return {"planning_mode": mode}


def _current_weekly_plan_row(conn):
    """
    Resolve "the household's current plan" the way every plan-scoped tool
    means it when weekly_plan_id is omitted: the plan whose week actually
    contains today, so a chat answer about "this week's plan" always
    matches the same days the Meals tab is showing. Falls back to the
    most-recently-created plan when none covers today — a household with
    no plan at all still correctly resolves to None either way.

    This used to just be "most recently created plan" everywhere, which
    silently drifted away from "this week" the moment any other plan
    existed (a leftover from last week that was never cleared, or one
    generated ahead of time for next week) — the assistant would describe
    that other plan's meals while the Meals tab, which only ever shows the
    real current calendar week, correctly showed nothing. That's the exact
    "the chat knows about a meal plan the app doesn't show" report this
    fixes at the source, instead of just in one call site.

    Two changes came with Loop Board "Planning periods, not weeks":

    The seven-day window used to be written into the SQL as a literal
    `date(week_start_date, '+6 days')`, which is why a grep for `timedelta`
    or `range(7)` would never have found it. It now asks the same question
    of the plan's real PERIOD, via the SQL twin of plan_period(). A row with
    the unset sentinels resolves to exactly the old expression, so this is a
    no-op for every plan written before periods existed — verified by test
    rather than argued from the SQL.

    And a retired plan is never current. Under the one-plan-per-day rule
    (Emily, 2026-09-04) a plan whose days were taken over by a newer period
    has genuinely stopped being anybody's answer to "what's for dinner"; the
    fallback branch would otherwise resurrect it the moment no plan covered
    today, which is the emptiest week of all to hand back.
    """
    today = date.today().isoformat()
    plan = conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        f"AND date({_SQL_PERIOD_START}) <= date(?) "
        f"AND date({_SQL_PERIOD_START}, '+' || {_SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(), today, today),
    ).fetchone()
    if plan:
        return plan
    return conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(),),
    ).fetchone()


def set_week_constraints(constraints_notes: str, weekly_plan_id: int | None = None) -> dict:
    """
    Set/update the one-off constraints for a specific week's plan (e.g. "3
    nights this week," "under 30 minutes on weeknights," "one vegetarian
    night") without those constraints becoming a permanent household
    preference — they only apply to this plan record, unlike
    edit_preference which changes standing preferences. Omit
    weekly_plan_id to apply to the household's current (most recent) plan.
    If you're generating a brand-new plan, just pass constraints_notes
    directly to generate_weekly_plan instead — use this tool when
    constraints come up for a plan that already exists (e.g. mid-week) or
    you want them on record before generating.
    """
    conn = get_conn()
    if weekly_plan_id is None:
        row = _current_weekly_plan_row(conn)
        if not row:
            conn.close()
            raise ValueError("No weekly plan exists yet — generate one first, or pass constraints_notes to generate_weekly_plan directly.")
        weekly_plan_id = row["id"]
    else:
        # Unlike the None-branch above (which always resolves to this
        # household's own current plan), an explicitly passed
        # weekly_plan_id is caller/model-supplied and was never checked
        # against the caller's household before this write — the same
        # no-op-on-a-foreign-id bug fixed elsewhere in app/tools/.
        require_household_row(conn, "weekly_plans", weekly_plan_id, label="weekly plan")
    conn.execute(
        "UPDATE weekly_plans SET constraints_notes = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (constraints_notes, weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "constraints_notes": constraints_notes}


_COMPONENT_CATEGORY_ORDER = ["breakfast", "protein", "vegetable", "carb", "treat", "dip", "snack"]


def _build_day_based_menu(meal_dicts: list[dict]) -> list[dict]:
    """
    Group a day-based plan's flat meal entries into a day-by-day menu — one
    row per date with each planned slot filled in (breakfast/lunch/dinner/
    snack), for a real weekly-menu view (see get_weekly_plan's `menu`)
    instead of one flat card per meal. This is real, already-planned data,
    not a suggestion. Each slot's "why this?" rationale (see
    meal_plan_entries.reasoning) rides along as `{slot}_reasoning`, e.g.
    day["dinner_reasoning"], so a "why this?" affordance can show it
    without a second lookup.
    """
    by_date: dict[str, dict] = {}
    slots = ["breakfast", "lunch", "dinner", "snack"]
    for m in meal_dicts:
        if not m["date"]:
            continue
        day = by_date.setdefault(
            m["date"],
            {"date": m["date"], **{s: None for s in slots}, **{f"{s}_reasoning": None for s in slots}},
        )
        slot = m["slot"] if m["slot"] in slots else "dinner"
        day[slot] = m["meal"]
        day[f"{slot}_reasoning"] = m.get("reasoning")
    return [by_date[d] for d in sorted(by_date)]


def _build_suggested_schedule(components: list[dict], week_start_date: str, days: int = 7) -> list[dict]:
    """
    Deterministically spread a component_based item pool across a 7-day
    menu, purely for display (see get_weekly_plan's `menu`/
    `menu_is_suggested`) — the whole point of component_based planning is
    that the household assembles freely, so this is never saved or tracked
    as "planned," just one reasonable example arrangement. The pool
    (roughly a handful of proteins/vegetables/carbs, one breakfast idea, a
    treat, a dip, a snack or two) is intentionally smaller than 7 days x 4
    meals, so items repeat across days by design — rotated with an offset
    per slot so the same day doesn't always pair the same vegetable with
    both lunch and dinner. Lunch and dinner are both built as full plates —
    protein + vegetable + carb — never just a side pairing, so every
    suggested meal reads as a real plate rather than a partial one.
    """
    by_cat = {c["category"]: c["items"] for c in components if c.get("items")}
    breakfast = by_cat.get("breakfast", [])
    protein = by_cat.get("protein", [])
    vegetable = by_cat.get("vegetable", [])
    carb = by_cat.get("carb", [])
    treat = by_cat.get("treat", [])
    dip = by_cat.get("dip", [])
    snack = by_cat.get("snack", [])

    def pick(items, i):
        return items[i % len(items)] if items else None

    def plate(*parts):
        parts = [p for p in parts if p]
        return " with ".join(parts) if parts else None

    def snack_pick(i):
        if snack:
            return pick(snack, i)
        # No dedicated snack items saved — fall back to alternating the
        # treat/dip pool rather than leaving the slot empty, since either
        # reasonably doubles as a snack.
        fallback = (treat if i % 2 == 0 else dip) or treat or dip
        return pick(fallback, i)

    start = date.fromisoformat(week_start_date)
    schedule = []
    for i in range(days):
        schedule.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "breakfast": pick(breakfast, i),
            "lunch": plate(pick(protein, i), pick(vegetable, i), pick(carb, i)),
            "dinner": plate(pick(protein, i + 1), pick(vegetable, i + 1), pick(carb, i + 1)),
            "snack": snack_pick(i),
        })
    return schedule


def _compute_freshness(meal_dicts: list[dict], plan_created_at: str) -> dict:
    """
    Count how many of this week's planned meals are recipes newly
    introduced by this same plan versus recipes that already existed
    beforehand — the "2 new recipes this week" freshness signal. A recipe
    counts as "new" if its created_at is at/after this plan's own
    created_at (it didn't exist before this plan brought it in).
    Deliberately NOT based on recipes.times_cooked: that counter
    increments at *planning* time (see plan_meal), not at actual-cooking
    time, so it already reads >= 1 for every meal in the very plan being
    inspected — using it here would make everything look like a repeat.
    Freeform/untracked entries (no saved recipe) aren't counted either way.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT name, created_at FROM recipes WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()
    created_by_name = {r["name"].lower(): r["created_at"] for r in rows}

    new_count = 0
    repeat_count = 0
    for m in meal_dicts:
        created_at = created_by_name.get((m["meal"] or "").lower())
        if created_at is None:
            continue
        if created_at >= plan_created_at:
            new_count += 1
        else:
            repeat_count += 1
    return {"new_recipe_count": new_count, "repeat_recipe_count": repeat_count}


def get_weekly_plan(weekly_plan_id: int | None = None) -> dict:
    """
    Get a weekly plan with all its meals. If weekly_plan_id is omitted,
    returns the household's most recently created plan — use that form
    when the user just says "what's this week's plan?" Returns
    weekly_plan_id: None with an empty meals list if no plan exists yet.
    Always includes a flat `meals` list (each with date/slot/
    component_category). For a component_based plan (see planning_mode),
    also includes a `components` list grouped by category — prefer that
    grouping when describing a component_based plan back to the user,
    since date/slot aren't meaningful there (every entry shares the same
    placeholder date). Also includes `is_first_plan` (true only for a
    household's very first generated plan — mention this warmly, e.g.
    "here's your first week, built around what you told me") and
    `new_recipe_count`/`repeat_recipe_count` (the freshness signal — how
    many planned meals are recipes never cooked before vs. ones the
    household's made before).
    """
    conn = get_conn()
    if weekly_plan_id is None:
        # Resolves to the plan whose week actually contains today when one
        # exists, falling back to the most-recently-created plan otherwise
        # — see _current_weekly_plan_row. id DESC as a tiebreaker still
        # matters within that: two plans created within the same second
        # (created_at has only second-level resolution) would otherwise
        # resolve non-deterministically, which broke clear_stale_grocery_items
        # identifying the actual newest plan.
        plan = _current_weekly_plan_row(conn)
    else:
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
    if not plan:
        conn.close()
        return {"weekly_plan_id": None, "meals": []}

    meals = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.food_groups_json, mpe.component_category, mpe.cooked_status, mpe.reasoning,
               mpe.slot_state, mpe.open_reason
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ?
        ORDER BY mpe.date ASC, mpe.slot ASC
        """,
        (plan["id"],),
    ).fetchall()
    conn.close()

    meal_dicts = [
        {
            "entry_id": m["id"], "date": m["date"], "slot": m["slot"], "meal": m["meal"],
            "food_groups": json.loads(m["food_groups_json"]),
            "component_category": m["component_category"],
            "cooked_status": m["cooked_status"],
            "reasoning": m["reasoning"] or None,
            # The assistant reads this list, so it has to be able to tell a
            # slot that needs no decision from one that does. Without it, a
            # nobody-home dinner looks exactly like a missing meal and gets
            # offered back as a question — which is precisely the thing
            # planned_empty exists to prevent. Caught in a real chat turn,
            # not by reading the code.
            "slot_state": m["slot_state"],
            "open_reason": m["open_reason"] or None,
        }
        for m in meals
    ]

    # The plan's real first day of content, as opposed to week_start_date
    # (always that week's Monday — the filing key every screen looks this
    # plan up by, see tools.get_plan_id_for_week). For an ordinary
    # full week these are the same date. For a genuine part-week (Loop
    # Board "Build a real part-week for households who onboard mid-week"),
    # generation never writes a row for a day before the household actually
    # joined — no meal, no open question, nothing — so the earliest date
    # actually on record IS the first day the household has anything to see.
    # Computed here rather than stored, so it stays correct even if rows are
    # edited later; falls back to week_start_date for a plan with no meals
    # yet, which reproduces the pre-part-week behaviour exactly.
    #
    # Since Loop Board "Planning periods, not weeks" the plan usually KNOWS
    # its own first day, and a stored answer beats a derived one: a plan
    # whose opening days are all `planned_empty` has rows for them, so the
    # derivation was only ever right because part-weeks wrote no rows at all
    # for the days before they began. The min() stays as the fallback for
    # every plan written before periods existed, unchanged.
    period_start, period_day_count = plan_period(plan)
    first_planned_date = (
        plan["content_start_date"]
        or min((m["date"] for m in meal_dicts), default=plan["week_start_date"])
    )

    result = {
        "weekly_plan_id": plan["id"],
        "week_start_date": plan["week_start_date"],
        # The period, as the one honest answer to "which days is this plan
        # for". week_start_date above stays what it has always been: the
        # filing key every /api/week/{...} route is addressed by.
        "period_start_date": period_start,
        "day_count": period_day_count,
        "period_end_date": period_end_date(period_start, period_day_count),
        "period_label": _format_period_range(period_start, period_day_count),
        # True for anything that isn't a plain seven days — a mid-week
        # onboarding part-week, a Thursday-to-Thursday period, a three-day
        # window. Named for the shape rather than for one cause of it.
        "is_custom_period": period_day_count != 7 or period_start != plan["week_start_date"],
        "first_planned_date": first_planned_date,
        "is_part_week": first_planned_date != plan["week_start_date"],
        "status_is_retired": plan["status"] == "retired",
        "superseded": json.loads(plan["superseded_json"]) if plan["superseded_json"] else None,
        "status": plan["status"],
        # Who said yes to this week and when — the approved receipt's own
        # two fields. Blank/None while the plan is still a draft.
        "approved_by": plan["approved_by"],
        "approved_at": plan["approved_at"],
        "approved_grocery_added": plan["approved_grocery_added"],
        "approved_grocery_skipped": plan["approved_grocery_skipped"],
        # Which revision of the household's answers produced this week.
        "intake_id": plan["intake_id"],
        "constraints_notes": plan["constraints_notes"],
        "planning_mode": plan["planning_mode"],
        "is_first_plan": bool(plan["is_first_plan"]),
        "meals": meal_dicts,
    }
    result.update(_compute_freshness(meal_dicts, plan["created_at"]))

    if plan["planning_mode"] == "component_based":
        by_category: dict[str, list[str]] = {}
        for m in meal_dicts:
            cat = m["component_category"] or "other"
            by_category.setdefault(cat, []).append(m["meal"])
        ordered_cats = [c for c in _COMPONENT_CATEGORY_ORDER if c in by_category]
        ordered_cats += [c for c in by_category if c not in ordered_cats]
        result["components"] = [{"category": c, "items": by_category[c]} for c in ordered_cats]
        # `menu`: a real day-by-day weekly menu for display (see the Share
        # view) even though component_based plans have no fixed day
        # mapping underneath — menu_is_suggested tells the caller this is
        # one example arrangement, not something actually planned/tracked.
        result["suggested_schedule"] = _build_suggested_schedule(
            result["components"], period_start, days=period_day_count,
        )
        result["menu"] = result["suggested_schedule"]
        result["menu_is_suggested"] = True
    else:
        result["menu"] = _build_day_based_menu(meal_dicts)
        result["menu_is_suggested"] = False

    return result


def _menu_dates(plan: dict) -> list[str]:
    """
    The days the Meals screen draws for a plan: its period, plus any filing
    days that run ahead of it.

    Three shapes, and the second is the reason this isn't just the period:

    - An ordinary Monday week — period start == week_start_date, 7 days —
      gives the same seven dates the old `range(7)` loop gave. Byte-identical,
      which is the property the no-op test pins.
    - A part-week filed under its Monday (onboarding Wednesday: filed Monday,
      content Wed, 5 days) gives all seven days again, with Monday and
      Tuesday carrying `before_plan_start: True` — the flag exists precisely
      so the grid can grey days that have already gone by rather than show
      three blank slots that look like an unplanned day. Dropping them to
      show only the period would have thrown that distinction away.
    - A custom period (Thursday to next Thursday, filed under the Thursday)
      gives its own eight days and nothing else. There is no lead-in,
      because the filing key IS the period start.

    The lead-in is bounded by the period start, and the API refuses a
    period whose start is more than one period-length past its filing key
    (see main._validated_period) — without that the distance is unbounded
    and a 3-day plan filed months earlier drew 150 empty days, each of
    which _decorate_with_needs then looked up.

    The lead-in is also dropped entirely once the plan has been SHORTENED
    by a takeover — i.e. once its content start has moved forward from a
    period it used to hold. Those days now belong to another plan, and
    `before_plan_start` means "already gone by" everywhere else in this
    codebase; using it for "somebody else owns this" would have two screens
    drawing the same dates and neither saying so.
    """
    # Reads get_weekly_plan's already-resolved period fields rather than
    # calling plan_period again: this is handed that function's RESULT, not a
    # database row, and the result carries no `content_start_date` for
    # plan_period to find — it would have quietly resolved every part-week's
    # start back to its filing Monday and drawn two days of ghost slots.
    period_start = plan["period_start_date"]
    day_count = plan["day_count"]
    week_start = plan["week_start_date"]
    lead_in = []
    if period_start > week_start and not plan.get("superseded"):
        span = (date.fromisoformat(period_start) - date.fromisoformat(week_start)).days
        lead_in = _week_intake.period_dates(week_start, span)
    return lead_in + _week_intake.period_dates(period_start, day_count)


def get_week_menu(weekly_plan_id: int | None = None) -> dict:
    """
    The weekly menu for the Week tab (design_handoff_shell/
    README.md §5) — the "one backend ask" for that redesign. Unlike
    get_weekly_plan's `menu` (which only lists dates that already have at
    least one entry), this always returns exactly 7 days starting at the
    plan's week_start_date, one dict per day with `breakfast`/`lunch`/
    `dinner` keys — each either None (nothing planned, drives the "Pick"
    row) or `{title, meta, source}`.

    `source`/`meta` have no backing column in meal_plan_entries, so they're
    derived with a keyword heuristic against the entry's freeform text —
    documented here as a judgment call, not a spec'd mapping:
      - "leftover"/"leftovers" in the text -> source "leftovers", meta "reheat"
      - "takeout"/"take-out"/"take out"/"delivery"/"order in" -> source
        "takeout", meta "takeout"
      - anything else (a saved recipe or a plain freeform entry) -> source
        "plan", meta the recipe's prep_time_minutes + cook_time_minutes as
        "N min" when both are known, else None (nothing informative to show
        rather than a misleading guess).

    Component_based plans (planning_mode == "component_based") have no
    real per-day assignment underneath — get_weekly_plan already covers
    this with a suggested_schedule/menu_is_suggested pair. This function
    mirrors that: it fills the 7 days from that same suggested spread, with
    every present slot as source "plan" / meta None (it's an example
    arrangement, not real timing), and passes menu_is_suggested through so
    the UI can note that.

    Omit weekly_plan_id for the household's current (most recently
    created) plan, same convention as get_weekly_plan. Returns
    week_start_date: None and an empty days list if no plan exists yet —
    there's nothing to anchor 7 days to.
    """
    conn = get_conn()
    household = conn.execute(
        "SELECT name FROM households WHERE id = ?", (household_id(),)
    ).fetchone()
    conn.close()
    household_name = household["name"] if household else ""

    plan = get_weekly_plan(weekly_plan_id)
    if not plan.get("weekly_plan_id"):
        return {"weekly_plan_id": None, "week_start_date": None, "household_name": household_name, "days": [], "menu_is_suggested": False}

    # design_handoff_plan_the_week: the Meals screen is where a week is
    # approved, so it needs both halves of that state — whether this plan
    # is still a draft (and what approving it would cost the grocery list),
    # and, once approved, who settled it and when. The preview is computed
    # for a draft only: an approved plan has already contributed, so its
    # number would always be zero and reads as a promise of nothing.
    approval = {
        "status": plan["status"],
        "approved_by": plan["approved_by"],
        "approved_at": plan["approved_at"],
        "approved_grocery_added": plan["approved_grocery_added"],
        "approved_grocery_skipped": plan["approved_grocery_skipped"],
        "grocery_preview": None,
    }
    if plan["status"] != "approved":
        approval["grocery_preview"] = preview_plan_grocery_impact(plan["weekly_plan_id"])
    # Every adult but the one who approved — the receipt's "{Other adult}
    # has been told the week is settled." Empty for a one-adult household,
    # which is what keeps that sentence from being written at all rather
    # than written about nobody.
    #
    # Also empty when nobody is recorded as having approved. An approval
    # with no name raises no notification (see get_active_notifications #4,
    # which requires one), so nobody WAS told — and every plan approved
    # before this flow existed has a blank approved_by. Listing all the
    # adults there would put a claim on screen that is simply untrue, to a
    # reader who may be among those supposedly told.
    approver = (plan["approved_by"] or "").strip()
    approval["other_adults"] = [
        p["name"] for p in _coordination.get_household_people()
        if p["name"].strip().lower() != approver.lower()
    ] if approver else []

    slots = ("breakfast", "lunch", "dinner")
    dates = _menu_dates(plan)

    if plan["planning_mode"] == "component_based":
        by_date = {d["date"]: d for d in plan["menu"]}
        days = []
        today_str = date.today().isoformat()
        suggestions = None
        for d in dates:
            row = by_date.get(d, {})
            # component_based plans have no fixed day mapping and aren't
            # part-week-aware yet (see the day-based branch below for the
            # real field) — always False here so the key exists either way.
            day = {"date": d, "before_plan_start": False}
            for s in slots:
                title = row.get(s)
                # `state` matters even here, where every slot is "planned"
                # by construction: the Meals screen keys "Cook this" /
                # "Swap it" and the dinner star off it, so omitting it made
                # those disappear for component-based households.
                day[s] = (
                    {"title": title, "meta": None, "source": "plan", "state": "planned", "reason": None}
                    if title else None
                )
            if day["dinner"] is None and d >= today_str:
                if suggestions is None:
                    suggestions = _suggest_quick_dinners()
                day["dinner_suggestions"] = suggestions
            days.append(day)
        return {
            "weekly_plan_id": plan["weekly_plan_id"],
            "week_start_date": plan["week_start_date"],
            "period_start_date": plan["period_start_date"],
            "period_end_date": plan["period_end_date"],
            "day_count": plan["day_count"],
            "is_custom_period": plan["is_custom_period"],
            "household_name": household_name,
            "days": days,
            "menu_is_suggested": True,
            **approval,
        }

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.recipe_id, mpe.freeform_meal,
               COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.slot_state, mpe.open_reason, mpe.reasoning, mpe.derived_from_json,
               r.prep_time_minutes, r.cook_time_minutes
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ?
        """,
        (plan["weekly_plan_id"],),
    ).fetchall()
    conn.close()

    def build_slot(row) -> dict | None:
        # The three states a slot can be in. Only a slot that is genuinely
        # absent returns None — and after a generation through
        # _finish_week_slots there shouldn't be any.
        if row["slot_state"] == "planned_empty":
            return {
                "title": "Out — nothing to cook", "meta": None, "source": "empty",
                "state": "planned_empty", "reason": row["reasoning"], "entry_id": row["id"],
            }
        if row["slot_state"] == "open":
            derived = json.loads(row["derived_from_json"] or "{}")
            return {
                "title": "I’d like your call on this one", "meta": None, "source": "open",
                "state": "open", "open_reason": row["open_reason"],
                "options": derived.get("options") or [], "entry_id": row["id"],
            }
        title = row["meal"]
        if not title:
            return None
        # The 4-9 word "why" shown under the meal name. Generated with the
        # plan (see meal_plan_entries.reasoning) rather than improvised on
        # demand, so it can't contradict the actual reason.
        common = {
            "state": "planned", "reason": row["reasoning"] or None, "entry_id": row["id"],
        }
        text = (row["freeform_meal"] or "").lower()
        if re.search(r"leftovers?\b", text):
            return {"title": title, "meta": "reheat", "source": "leftovers", **common}
        if re.search(r"take[\s-]?out|delivery|order in", text):
            return {"title": title, "meta": "takeout", "source": "takeout", **common}
        prep = row["prep_time_minutes"] or 0
        cook = row["cook_time_minutes"] or 0
        total = prep + cook
        meta = f"{total} min" if total else None
        return {"title": title, "meta": meta, "source": "plan", **common}

    by_date_slot = {}
    for r in rows:
        if r["slot"] in slots:
            by_date_slot[(r["date"], r["slot"])] = build_slot(r)

    # The day list is the plan's period plus any filing days ahead of it —
    # see _menu_dates, which keeps an ordinary week at exactly the seven days
    # this used to hard-code and a part-week at the same seven it did. But a
    # part-week's earlier days never got any rows at all (see
    # get_weekly_plan's first_planned_date), so all three of their slots
    # would otherwise be indistinguishable None from an ordinary day
    # nobody's planned yet. before_plan_start names that difference
    # explicitly, so the caller can choose to grey/hide those days rather
    # than guess from three blank slots what they mean. False for every
    # plan that isn't a part-week (the overwhelming majority), which
    # reproduces the exact previous shape of this response.
    content_start = plan["period_start_date"]
    days = [
        {"date": d, "before_plan_start": d < content_start, **{s: by_date_slot.get((d, s)) for s in slots}}
        for d in dates
    ]

    # This Week's day card (design_handoff_home_manager option 6a) shows the
    # same two-quick-dinner "Pick" rows on ANY day's empty dinner slot, not
    # just the one nearest gap get_needs_you_items flags for the Today band —
    # so a day beyond that 48h window still has something to tap instead of
    # a dead end. Only for today-or-future days: a past day's empty dinner
    # is just "not planned," nothing to suggest into it.
    today_str = date.today().isoformat()
    suggestions = None
    for day in days:
        if day["dinner"] is None and day["date"] >= today_str:
            if suggestions is None:
                suggestions = _suggest_quick_dinners()
            day["dinner_suggestions"] = suggestions

    intake = _week_intake.get_week_intake(plan["week_start_date"])
    trip = _decorate_with_needs(days, plan["week_start_date"])
    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "week_start_date": plan["week_start_date"],
        # The eyebrow names the days the household actually chose, not the
        # seven the filing key implies — "Sep 10–17" for a Thursday-to-
        # Thursday period, still "Sep 7–13" for an ordinary week.
        "week_label": _format_period_range(plan["period_start_date"], plan["day_count"]),
        "period_start_date": plan["period_start_date"],
        "period_end_date": plan["period_end_date"],
        "day_count": plan["day_count"],
        "is_custom_period": plan["is_custom_period"],
        "household_name": household_name,
        "days": days,
        "menu_is_suggested": False,
        "headline": _week_headline(plan, days, intake),
        # The trip banner ("Away Sat–Sun") — present only when the week
        # actually has one, so the ordinary week carries no extra chrome.
        "trip_summary": trip,
        **approval,
    }


def _decorate_with_needs(days: list[dict], week_start: str) -> str:
    """
    Fold each slot's derived need and real headcount into the week menu the
    Meals screen already fetches, and return a short label for the week's
    trip if it has one.

    Done here rather than as a second endpoint because the screen renders
    a slot and its state together — two round trips would let the meal and
    the reason it looks the way it does arrive separately, which is exactly
    how a slot ends up briefly claiming to be something it isn't.

    Only decorates; a slot with nothing unusual is left exactly as it was,
    so every existing consumer of this payload is unaffected.

    The lookup window is taken from `days` itself rather than from a fixed
    seven, because `days` is now a planning period and can be longer (Loop
    Board "Planning periods, not weeks"). Fetching seven days of needs for
    an eight-day period would have left the last day silently undecorated —
    an away night rendering as an ordinary empty slot, which is the one
    difference this decoration exists to make visible. `week_start` is still
    the filing key and still the fallback for an empty day list.
    """
    from . import slot_needs as _slot_needs
    from . import attendance as _attendance

    window_start = days[0]["date"] if days else week_start
    window_days = len(days) or 7
    needs = _slot_needs.get_week_slot_needs(window_start, window_days)
    attendance = _attendance.get_week_attendance(window_start, window_days)
    away_dates: list[str] = []

    for day in days:
        d = day["date"]
        day_needs = needs.get(d) or {}
        day_attendance = attendance.get(d) or {}
        if any(info["need"] == "away" for info in day_needs.values()):
            away_dates.append(d)
        for slot in ("breakfast", "lunch", "dinner"):
            entry = day.get(slot)
            info = day_needs.get(slot)
            att = day_attendance.get(slot)
            if entry is None and (info or att):
                # A need declared before this week was generated has no
                # entry to hang off yet. Give it a shell so the screen can
                # still show why the slot looks the way it does.
                entry = {"title": None, "meta": None, "source": "empty", "state": "planned_empty", "reason": None}
                day[slot] = entry
            if entry is None:
                continue
            if info:
                entry["need"] = info["need"]
                entry["need_reason"] = info["reason"]
                entry["need_for_names"] = info["for_member_names"]
                if info["need"] == "ready_made":
                    entry["recommendation"] = _slot_needs.describe_ready_made(d, slot)
            if att:
                entry["serves"] = att["headcount"]
                entry["away_names"] = att["absent_names"]
                entry["present_names"] = att["present_names"]
                entry["guest_count"] = att["guest_count"]
                entry["attendance_summary"] = _attendance.summary_line(att)

    if not away_dates:
        return ""
    first, last = away_dates[0], away_dates[-1]
    fmt = "%a"
    start_label = date.fromisoformat(first).strftime(fmt)
    if first == last:
        return f"Away {start_label}"
    return f"Away {start_label}–{date.fromisoformat(last).strftime(fmt)}"


def _suggest_quick_dinners(limit: int = 2) -> list[dict]:
    """
    A couple of fast, currently-in-rotation recipes to offer as one-tap
    picks for an undecided dinner (see get_needs_you_items) — not a real
    recommendation engine, just "what's quick and not off the table right
    now." Excludes disliked and temporarily-excluded recipes; orders by
    known prep+cook time ascending (recipes with no timing info sort last,
    since we can't call them "quick"). Returns [] if there are no recipes
    saved yet — the needs-you card skips the suggestion rows rather than
    inventing options in that case.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT name, prep_time_minutes, cook_time_minutes
        FROM recipes
        WHERE household_id = ? AND rating != 'disliked' AND temporarily_excluded = 0
        ORDER BY
            (prep_time_minutes IS NULL AND cook_time_minutes IS NULL) ASC,
            (COALESCE(prep_time_minutes, 0) + COALESCE(cook_time_minutes, 0)) ASC
        LIMIT ?
        """,
        (household_id(), limit),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        total = (r["prep_time_minutes"] or 0) + (r["cook_time_minutes"] or 0)
        out.append({"meal": r["name"], "minutes": total or None})
    return out


def get_needs_you_items() -> list[dict]:
    """
    The Today screen's needs-you band (design_handoff_shell/README.md §4,
    §9 Step 5) — 0-3 cards for things that need a decision right now.
    Starting with the two rules the README calls out explicitly rather
    than a general prioritisation engine (that's future work):

      1. **Dinner decision** — the soonest of tonight's/tomorrow's dinner
         slots that's still empty (the "within 48 hours" window from the
         spec). Comes with up to two quick-recipe suggestions (see
         _suggest_quick_dinners) so the card's "Pick" rows have something
         real to offer — the card is omitted entirely if there isn't even
         one recipe saved yet, since a decision card with nothing to pick
         is worse than no card.
      2. **Shop run** — there are ungathered grocery items *and* something
         is actually planned (any slot, any meal) in the next 48 hours
         that hasn't been cooked yet. There's no ingredient-to-grocery-item
         link in this schema to check "these specific items block that
         specific meal," so this is a proxy: "you have a shop to do, and
         something's coming up soon" rather than a precise per-ingredient
         match — documented here rather than pretending it's exact.

    Returns at most one card per rule (max 2 for now, out of the spec's
    0-3 headroom) in the order the mock shows them: dinner decision first,
    then shop run.
    """
    conn = get_conn()
    today = date.today()
    horizon_end = today + timedelta(days=2)  # today, tomorrow, day-after exclusive edge -> "within 48h" covers today+tomorrow

    items: list[dict] = []

    # ---- Rule 1: dinner decision ----
    dinner_rows = conn.execute(
        "SELECT date FROM meal_plan_entries WHERE household_id = ? AND slot = 'dinner' AND date >= ? AND date < ?",
        (household_id(), today.isoformat(), horizon_end.isoformat()),
    ).fetchall()
    planned_dinner_dates = {r["date"] for r in dinner_rows}
    for offset in (0, 1):
        candidate = (today + timedelta(days=offset)).isoformat()
        if candidate in planned_dinner_dates:
            continue
        options = _suggest_quick_dinners()
        if not options:
            break  # no recipes to suggest at all -- nothing later in the loop will differ, so stop
        when = "Tonight" if offset == 0 else "Tomorrow"
        items.append({
            "type": "dinner_decision",
            "kicker": "DINNER",
            "title": when + " needs a dinner",
            "urgency": "urgent",
            "date": candidate,
            "slot": "dinner",
            "options": options,
        })
        break  # only the soonest empty dinner becomes a card

    # ---- Rule 2: shop run ----
    # Same "needed, not excluded from the list" filter list_grocery_list
    # uses for the normal shopping list, so this count matches what the
    # Grocery tab itself would show.
    needed_count = conn.execute(
        "SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ? AND status = 'needed' AND excluded_from_list = 0",
        (household_id(),),
    ).fetchone()["n"]

    # cooked_status uses 'pending', not 'cooked' — see meal_plan_entries schema.
    upcoming_meal = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE household_id = ? AND date >= ? AND date < ? AND cooked_status = 'pending'",
        (household_id(), today.isoformat(), horizon_end.isoformat()),
    ).fetchone()["n"]

    if needed_count > 0 and upcoming_meal > 0:
        sample = conn.execute(
            "SELECT item FROM grocery_items WHERE household_id = ? AND status = 'needed' AND excluded_from_list = 0 ORDER BY id ASC LIMIT 4",
            (household_id(),),
        ).fetchall()
        items.append({
            "type": "shop_run",
            "kicker": "SHOP RUN",
            "title": "Grocery run needed",
            "urgency": "warn",
            "count": needed_count,
            "sample_items": [s["item"] for s in sample],
        })

    conn.close()
    return items


def resolve_needs_you_dinner(
    meal_date: str, meal: str, add_ingredients_to_grocery_list: bool = False
) -> dict:
    """
    Resolve a needs-you dinner-decision card by planning the picked meal —
    thin wrapper around plan_meal that also attaches it to the household's
    current weekly plan (if one exists) so it shows up correctly in the
    Week tab's menu, then returns the refreshed needs-you list so the
    Today screen can just re-render from the response.

    add_ingredients_to_grocery_list carries the answer the card's confirm
    step collected. It is a real question asked of a real person, which is
    what makes this an explicit yes and not a silent write — the same
    standard chat is held to (see plan_meal). It defaults to False so a
    caller that forgets to ask adds nothing.
    """
    plan = get_weekly_plan()
    weekly_plan_id = plan.get("weekly_plan_id")
    result = _meal_plans.plan_meal(
        meal_date, meal, slot="dinner", weekly_plan_id=weekly_plan_id,
        add_ingredients_to_grocery_list=add_ingredients_to_grocery_list,
    )
    return {
        "items": get_needs_you_items(),
        "groceries_added": result["groceries_added"],
        "already_have_skipped": result["already_have_skipped"],
    }


def _weekly_plan_is_approved(weekly_plan_id: int | None) -> bool:
    """Whether a plan is approved — i.e. whether its ingredients are already on the grocery list."""
    if weekly_plan_id is None:
        return False
    conn = get_conn()
    row = conn.execute(
        "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?", (weekly_plan_id, household_id())
    ).fetchone()
    conn.close()
    return bool(row) and row["status"] == "approved"


def _plan_grocery_candidate_entries(conn, weekly_plan_id: int):
    """
    The plan's meal entries whose ingredients have NOT yet been recorded as
    contributing to the grocery list — i.e. exactly what an approval would
    add. Shared by approve_weekly_plan (which then adds them) and
    preview_plan_grocery_impact (which only counts them), so the number the
    draft screen promises and the number approval actually delivers come
    from one query rather than two that can drift apart.
    """
    return conn.execute(
        """
        SELECT mpe.id, r.ingredients_json
        FROM meal_plan_entries mpe
        JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM meal_plan_grocery_links mpgl
              WHERE mpgl.meal_plan_entry_id = mpe.id AND mpgl.household_id = mpe.household_id
          )
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (weekly_plan_id, household_id()),
    ).fetchall()


def preview_plan_grocery_impact(weekly_plan_id: int) -> dict:
    """
    What approving this plan WOULD put on the grocery list, without putting
    anything there. Writes nothing at all.

    This is what makes the draft screen's promise a real number rather than
    a guess: "I haven't put anything on your shopping list yet. Approve the
    week and I'll build it — 22 items." (design_handoff_plan_the_week/COPY.md
    → Draft).

    Note that would_add_count is already NET of the kitchen — an ingredient
    lands in exactly one of the two buckets below, never both. The promise
    line used to end "less whatever's already in your kitchen", which
    offered that subtraction as though it were still to come; it now names
    already_have_count separately, and only when it is non-zero.

    Mirrors _add_recipe_ingredients_to_grocery_list's own two rules exactly
    — entries that already contributed are skipped, and an ingredient whose
    name matches a tracked inventory item with a real quantity counts as
    already-in-the-kitchen rather than as something to buy.

    It does NOT mirror that function's third rule, the leftovers one, and
    doesn't need to: a leftovers night contributes nothing on approval,
    but it names the same dish as the cook night it eats from, so its
    ingredients are already in this set of DISTINCT names anyway. The
    promised count and the delivered count still match. (If a leftovers
    entry ever named a different recipe from its source, this would
    over-promise by those names — worth knowing, not worth a second
    chain lookup on a read-only preview today.) Deliberately
    counts DISTINCT ingredient names, not raw rows: two recipes both
    wanting onions consolidate onto one grocery line (add_grocery_item
    merges by name), so counting rows would promise more items than
    approval actually creates.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT id, status FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    entries = _plan_grocery_candidate_entries(conn, weekly_plan_id)
    have_names = {
        row["item"].strip().lower()
        for row in conn.execute(
            "SELECT item FROM inventory_items WHERE household_id = ? AND TRIM(quantity) != ''",
            (household_id(),),
        ).fetchall()
    }
    conn.close()

    would_add: set[str] = set()
    already_have: set[str] = set()
    for entry in entries:
        for ing in json.loads(entry["ingredients_json"]):
            name = ing["item"].strip()
            if name.lower() in have_names:
                already_have.add(name.lower())
            else:
                would_add.add(name.lower())
    return {
        "weekly_plan_id": weekly_plan_id,
        "would_add_count": len(would_add),
        "already_have_count": len(already_have),
    }


def approve_weekly_plan(weekly_plan_id: int, approved_by: str = "") -> dict:
    """
    Approve a weekly plan — and, in the same step, put its meals'
    ingredients on the grocery list.

    `approved_by` is an adult's name (see schema.sql on
    weekly_plans.approved_by for why a name and not a member id). It's
    recorded with the approval time so the Meals screen can render the
    receipt the design calls for — "APPROVED BY EMILY · 9:41AM" — and so
    the other adult can be told who settled the week. Optional: an approval
    with no name still approves, and the receipt just drops the name rather
    than inventing one.

    Approving used to only flip a status flag; the grocery list had
    already been filled in during generation, whether or not the household
    ever agreed to that plan. Ingredients from drafts that were changed,
    abandoned or never approved piled up on the real shopping list as a
    result. Now generation adds nothing (see plan_meal, whose
    add_ingredients_to_grocery_list defaults to False) and approval is
    what populates the list, so the list only ever reflects a week the
    household actually said yes to.

    Safe to call more than once. Two separate guards, because they cover
    different things:

    - Re-approving an ALREADY-approved plan adds nothing at all. The
      grocery work happens on the transition into 'approved', not on every
      call. Without this, an entry whose ingredients were all skipped as
      already-in-the-pantry leaves no trace that it was ever considered
      (the ledger only records what was actually added), so a later
      re-approve would add them for real once the pantry had emptied — a
      surprise write to the list nobody asked for.
    - Within a single approval, an entry whose contributions are already
      recorded in meal_plan_grocery_links is skipped, so a plan whose
      meals already put their ingredients on the list another way (a swap,
      or plan_meal called with the flag) doesn't double up its quantities.

    Raises ValueError for a weekly_plan_id that doesn't exist, rather than
    reporting a cheerful approval of nothing — same as clear_weekly_plan
    and swap_component_in_plan.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if not existing:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    was_already_approved = existing["status"] == "approved"
    # A re-approval never overwrites the original approver/time — the
    # receipt names who actually settled the week, and the first yes is the
    # one that built the list. Only a genuine transition into 'approved'
    # (including a re-approval after a reopen, which clears these back out)
    # writes them.
    conn.execute(
        "UPDATE weekly_plans SET status = 'approved', updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    if not was_already_approved:
        conn.execute(
            "UPDATE weekly_plans SET approved_by = ?, approved_at = datetime('now') WHERE id = ? AND household_id = ?",
            (approved_by.strip(), weekly_plan_id, household_id()),
        )
    conn.commit()
    if was_already_approved:
        receipt = conn.execute(
            "SELECT approved_by, approved_at, approved_grocery_added, approved_grocery_skipped "
            "FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
        conn.close()
        return {
            "weekly_plan_id": weekly_plan_id,
            "status": "approved",
            "groceries_added": [],
            "already_have_skipped": [],
            # The counts stay the ORIGINAL approval's — this call added
            # nothing, and the receipt still describes the yes that built
            # the list.
            "groceries_added_count": receipt["approved_grocery_added"] if receipt else 0,
            "already_have_skipped_count": receipt["approved_grocery_skipped"] if receipt else 0,
            "was_already_approved": True,
            "approved_by": receipt["approved_by"] if receipt else "",
            "approved_at": receipt["approved_at"] if receipt else None,
        }
    entries = _plan_grocery_candidate_entries(conn, weekly_plan_id)
    approved_at = conn.execute(
        "SELECT approved_at FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()["approved_at"]
    conn.close()

    added_items = []
    already_have = []
    for entry in entries:
        added, have = _recipes._add_recipe_ingredients_to_grocery_list(
            entry["id"], json.loads(entry["ingredients_json"]), weekly_plan_id
        )
        added_items.extend(added)
        already_have.extend(have)

    # Counted as distinct names, matching preview_plan_grocery_impact, so
    # the number the draft promised and the number the receipt reports are
    # the same number rather than two different ways of counting the same
    # groceries. Persisted because neither is recoverable later — see
    # schema.sql on approved_grocery_added.
    added_count = len({n.strip().lower() for n in added_items})
    skipped_count = len({n.strip().lower() for n in already_have})
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET approved_grocery_added = ?, approved_grocery_skipped = ? "
        "WHERE id = ? AND household_id = ?",
        (added_count, skipped_count, weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()

    return {
        "weekly_plan_id": weekly_plan_id,
        "status": "approved",
        "groceries_added": added_items,
        "already_have_skipped": already_have,
        "groceries_added_count": added_count,
        "already_have_skipped_count": skipped_count,
        "was_already_approved": False,
        "approved_by": approved_by.strip(),
        "approved_at": approved_at,
    }


def swap_meal_in_plan(
    weekly_plan_id: int,
    meal_date: str,
    new_meal: str,
    slot: str = "dinner",
    food_groups: list[str] | None = None,
) -> dict:
    """
    Replace the meal on one day/slot of an already-generated weekly plan,
    without regenerating or touching the rest of the plan. new_meal can be
    a saved recipe name or a freeform description, same as plan_meal. The
    old meal's auto-added grocery ingredients are removed first (trimmed or
    deleted, whatever the amount it contributed calls for — see
    _reverse_meal_grocery_contributions) so the grocery list reflects only
    the new meal afterward instead of carrying both.
    """
    conn = get_conn()
    old_entries = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ?",
        (weekly_plan_id, meal_date, slot, household_id()),
    ).fetchall()
    conn.close()
    for row in old_entries:
        _grocery._reverse_meal_grocery_contributions(row["id"])

    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ?",
        (weekly_plan_id, meal_date, slot, household_id()),
    )
    conn.commit()
    conn.close()
    return _meal_plans.plan_meal(
        meal_date, new_meal, slot=slot, food_groups=food_groups, weekly_plan_id=weekly_plan_id,
        # Only put the new meal's ingredients on the list if this week has
        # already been approved — approval is what put the old meal's
        # ingredients there in the first place, and the reversal above just
        # took them back off. Swapping inside a still-unapproved draft
        # leaves the grocery list alone, exactly as generating it did.
        add_ingredients_to_grocery_list=_weekly_plan_is_approved(weekly_plan_id),
    )


def swap_component_in_plan(
    weekly_plan_id: int,
    component_category: str,
    old_meal: str,
    new_meal: str,
    food_groups: list[str] | None = None,
) -> dict:
    """
    Replace one item within a component_based plan's category (e.g. swap
    out one of the proteins) without touching the rest of the plan — the
    component_based equivalent of swap_meal_in_plan. old_meal must match
    the exact meal name currently in that category/plan.
    """
    conn = get_conn()
    week_start_date = conn.execute(
        "SELECT week_start_date FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if not week_start_date:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    week_start_date = week_start_date["week_start_date"]

    match = conn.execute(
        """
        SELECT mpe.id FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.component_category = ? AND mpe.household_id = ?
          AND COALESCE(r.name, mpe.freeform_meal) = ?
        LIMIT 1
        """,
        (weekly_plan_id, component_category, household_id(), old_meal),
    ).fetchone()
    conn.close()
    if not match:
        removed = 0
    else:
        # Reverse the old item's grocery contribution first (see
        # swap_meal_in_plan) so replacing one component actually swaps its
        # ingredients on the list rather than piling the new ones on top.
        _grocery._reverse_meal_grocery_contributions(match["id"])
        conn = get_conn()
        deleted = conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (match["id"],))
        conn.commit()
        removed = deleted.rowcount
        conn.close()
    if not removed:
        raise ValueError(f"Couldn't find '{old_meal}' under category '{component_category}' in that plan.")
    return _meal_plans.plan_meal(
        week_start_date, new_meal, food_groups=food_groups, weekly_plan_id=weekly_plan_id,
        component_category=component_category,
        # See swap_meal_in_plan — mirrors the plan's approved state.
        add_ingredients_to_grocery_list=_weekly_plan_is_approved(weekly_plan_id),
    )
