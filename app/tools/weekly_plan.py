"""
The weekly plan as an object: slots, the menu view, approval, and swaps.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from ..db import get_conn
from ._shared import HOUSEHOLD_ID
from . import coordination as _coordination
from . import grocery as _grocery
from . import meal_plans as _meal_plans
from . import notifications as _notifications
from . import recipes as _recipes
from . import week_intake as _week_intake


WEEK_SLOTS = ("breakfast", "lunch", "dinner")


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
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
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
        (HOUSEHOLD_ID, meal_date, slot, weekly_plan_id, reason, json.dumps(derived_from or {})),
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
            HOUSEHOLD_ID, meal_date, slot, weekly_plan_id, open_reason,
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
        "SELECT * FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    conn.close()

    def field(name, default):
        return prefs[name] if prefs else default

    return {
        "meal_counts": {
            "breakfasts_per_week": field("breakfasts_per_week", 7),
            "lunches_per_week": field("lunches_per_week", 7),
            "dinners_per_week": field("dinners_per_week", 7),
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
    next_monday = this_monday + timedelta(days=7)

    conn = get_conn()
    dismissed = _notifications._dismissed_keys(conn)
    planned = {
        row["week_start_date"]
        for row in conn.execute(
            "SELECT DISTINCT week_start_date FROM weekly_plans WHERE household_id = ?", (HOUSEHOLD_ID,)
        ).fetchall()
    }
    conn.close()

    target = None
    if this_monday.isoformat() not in planned:
        target = this_monday
    elif today.weekday() >= 5 and next_monday.isoformat() not in planned:
        target = next_monday

    if target is None:
        return {"show": False, "week_start": None}
    week_start = target.isoformat()
    if f"plan_week_nudge:{week_start}" in dismissed:
        return {"show": False, "week_start": week_start, "dismissed": True}
    return {
        "show": True,
        "week_start": week_start,
        "week_label": _format_week_range(week_start),
        "is_current_week": target == this_monday,
        "dismiss_key": f"plan_week_nudge:{week_start}",
    }


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
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
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
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    conn.execute(
        "UPDATE weekly_plans SET status = 'draft', approved_by = '', approved_at = NULL, "
        "approved_grocery_added = 0, approved_grocery_skipped = 0, updated_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
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
        (intake_id, weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "intake_id": intake_id}


def audit_plan_slots(weekly_plan_id: int, day_count: int = 7) -> dict:
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

    Duplicates matter as much as gaps: two rows for one slot is how a night
    nobody is home ends up with groceries bought for it. They're reported
    separately from `missing` because they need the opposite fix.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT week_start_date FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    rows = conn.execute(
        "SELECT date, slot, slot_state, recipe_id, freeform_meal, open_reason "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchall()
    conn.close()

    dates = _week_intake._week_dates(plan["week_start_date"])[:day_count]
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
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM weekly_plans WHERE household_id = ? AND week_start_date = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (HOUSEHOLD_ID, week_start_date),
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
    start = date.fromisoformat(week_start_date)
    end = start + timedelta(days=6)
    start_month = start.strftime("%b")
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
        (HOUSEHOLD_ID, mode),
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
    """
    today = date.today().isoformat()
    plan = conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? "
        "AND date(week_start_date) <= date(?) AND date(week_start_date, '+6 days') >= date(?) "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (HOUSEHOLD_ID, today, today),
    ).fetchone()
    if plan:
        return plan
    return conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (HOUSEHOLD_ID,),
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
    conn.execute(
        "UPDATE weekly_plans SET constraints_notes = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (constraints_notes, weekly_plan_id, HOUSEHOLD_ID),
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
        "SELECT name, created_at FROM recipes WHERE household_id = ?", (HOUSEHOLD_ID,)
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
            (weekly_plan_id, HOUSEHOLD_ID),
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

    result = {
        "weekly_plan_id": plan["id"],
        "week_start_date": plan["week_start_date"],
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
        result["suggested_schedule"] = _build_suggested_schedule(result["components"], plan["week_start_date"])
        result["menu"] = result["suggested_schedule"]
        result["menu_is_suggested"] = True
    else:
        result["menu"] = _build_day_based_menu(meal_dicts)
        result["menu_is_suggested"] = False

    return result


def get_week_menu(weekly_plan_id: int | None = None) -> dict:
    """
    The always-7-day weekly menu for the Week tab (design_handoff_shell/
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
        "SELECT name FROM households WHERE id = ?", (HOUSEHOLD_ID,)
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
    start = date.fromisoformat(plan["week_start_date"])
    dates = [(start + timedelta(days=i)).isoformat() for i in range(7)]

    if plan["planning_mode"] == "component_based":
        by_date = {d["date"]: d for d in plan["menu"]}
        days = []
        today_str = date.today().isoformat()
        suggestions = None
        for d in dates:
            row = by_date.get(d, {})
            day = {"date": d}
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

    days = [
        {"date": d, **{s: by_date_slot.get((d, s)) for s in slots}}
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
    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "week_start_date": plan["week_start_date"],
        "week_label": _format_week_range(plan["week_start_date"]),
        "household_name": household_name,
        "days": days,
        "menu_is_suggested": False,
        "headline": _week_headline(plan, days, intake),
        **approval,
    }


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
        (HOUSEHOLD_ID, limit),
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
        (HOUSEHOLD_ID, today.isoformat(), horizon_end.isoformat()),
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
        (HOUSEHOLD_ID,),
    ).fetchone()["n"]

    # cooked_status uses 'pending', not 'cooked' — see meal_plan_entries schema.
    upcoming_meal = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE household_id = ? AND date >= ? AND date < ? AND cooked_status = 'pending'",
        (HOUSEHOLD_ID, today.isoformat(), horizon_end.isoformat()),
    ).fetchone()["n"]

    if needed_count > 0 and upcoming_meal > 0:
        sample = conn.execute(
            "SELECT item FROM grocery_items WHERE household_id = ? AND status = 'needed' AND excluded_from_list = 0 ORDER BY id ASC LIMIT 4",
            (HOUSEHOLD_ID,),
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
        "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?", (weekly_plan_id, HOUSEHOLD_ID)
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
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchall()


def preview_plan_grocery_impact(weekly_plan_id: int) -> dict:
    """
    What approving this plan WOULD put on the grocery list, without putting
    anything there. Writes nothing at all.

    This is what makes the draft screen's promise a real number rather than
    a guess: "I haven't put anything on your shopping list yet. Approve the
    week and I'll build it — 22 items, less whatever's already in your
    kitchen." (design_handoff_plan_the_week/COPY.md → Draft).

    Mirrors _add_recipe_ingredients_to_grocery_list's own two rules exactly
    — entries that already contributed are skipped, and an ingredient whose
    name matches a tracked inventory item with a real quantity counts as
    already-in-the-kitchen rather than as something to buy. Deliberately
    counts DISTINCT ingredient names, not raw rows: two recipes both
    wanting onions consolidate onto one grocery line (add_grocery_item
    merges by name), so counting rows would promise more items than
    approval actually creates.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT id, status FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    entries = _plan_grocery_candidate_entries(conn, weekly_plan_id)
    have_names = {
        row["item"].strip().lower()
        for row in conn.execute(
            "SELECT item FROM inventory_items WHERE household_id = ? AND TRIM(quantity) != ''",
            (HOUSEHOLD_ID,),
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
        (weekly_plan_id, HOUSEHOLD_ID),
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
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    if not was_already_approved:
        conn.execute(
            "UPDATE weekly_plans SET approved_by = ?, approved_at = datetime('now') WHERE id = ? AND household_id = ?",
            (approved_by.strip(), weekly_plan_id, HOUSEHOLD_ID),
        )
    conn.commit()
    if was_already_approved:
        receipt = conn.execute(
            "SELECT approved_by, approved_at, approved_grocery_added, approved_grocery_skipped "
            "FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, HOUSEHOLD_ID),
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
        (weekly_plan_id, HOUSEHOLD_ID),
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
        (added_count, skipped_count, weekly_plan_id, HOUSEHOLD_ID),
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
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
    ).fetchall()
    conn.close()
    for row in old_entries:
        _grocery._reverse_meal_grocery_contributions(row["id"])

    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ?",
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
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
        (weekly_plan_id, HOUSEHOLD_ID),
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
        (weekly_plan_id, component_category, HOUSEHOLD_ID, old_meal),
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
