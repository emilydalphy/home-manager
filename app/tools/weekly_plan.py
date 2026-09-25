"""
The weekly plan as an object: slots, the menu view, approval, and swaps.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from ..db import get_conn
from ._shared import acting_member_id_for, acting_name, household_id, require_household_row
from . import coordination as _coordination
from . import grocery as _grocery
from . import meal_plans as _meal_plans
from . import notifications as _notifications
from . import plan_undo as _plan_undo
from . import plates as _plates
from . import plate_parts as _plate_parts_mod
from . import recipes as _recipes
from . import rhythm as _rhythm
from . import time_caps as _time_caps
from . import week_intake as _week_intake


logger = logging.getLogger("home_manager")

WEEK_SLOTS = ("breakfast", "lunch", "dinner")

# Every slot a day can actually hold, snacks included. WEEK_SLOTS above is
# deliberately narrower — it is the 21-slot GUARANTEE (what audit_plan_slots
# demands and _finish_week_slots fills), and snacks are not part of that
# promise. But a snack is still a real, planned, swappable entry, and
# reading the guarantee as if it were the list of slots that exist is what
# left snacks off the Meals screen entirely (Julia, 2026-09-08 — "the chat
# said it changed a snack and it didn't change it in the meal plan"). Use
# this one wherever the question is "which slots can a day have?"
DAY_SLOTS = WEEK_SLOTS + ("snack",)


def _household_today() -> date:
    """
    Today where the household lives, not where the container runs.

    The deployed container is UTC and households.timezone defaults to
    America/Toronto, so between 8pm and midnight Eastern the server's date
    is already tomorrow. Now's timeline runs on the household's day
    (moves.py, 2026-09-14) — so anything here that names a day the SCREEN
    will show, or that decides which days the screen can see, has to run on
    the same clock or the two halves land on different days. That is
    exactly how "a dinner answered on Now is saved and invisible"
    (overnight/needs-you-dinner-invisible, 2026-09-13) comes back: the card
    offers the server's date, the client posts it back verbatim, and the
    timeline is looking at the household's.

    cooker.household_now is the one reader of that column, and it is
    imported HERE rather than at the top of the file because cooker imports
    this module at import time — a top-level import would be a cycle. The
    same lazy-import shape moves._today_holiday already uses. A clock that
    can't be read falls back to the server's date: a bad setting is worth a
    wrong hour, never a blank screen.

    Written as a SHIFT applied to this module's own `date.today()` rather
    than as the household datetime's date, and that is deliberate: "the
    household's today" is exactly "the server's today, moved by however
    many whole days the two clocks are apart", and saying it that way
    leaves `weekly_plan.date` the single seam it has always been. A dozen
    test files pin this module's clock by patching that name
    (test_holidays, test_planning_periods, test_stale_draft_front_page,
    test_sunday_next_week_span, …); reading the zone-converted datetime
    directly would have walked straight past every one of them, and the
    first version of this did.
    """
    from . import cooker as _cooker

    try:
        shift = (_cooker.household_now().date() - datetime.now().date()).days
    except Exception:
        logger.exception("Couldn't read the household's clock; falling back to the server's date")
        return date.today()
    return date.today() + timedelta(days=shift)


def slot_order_sql(column: str) -> str:
    """
    An ORDER BY fragment that sorts a day's slots into the order they are
    actually EATEN, read off DAY_SLOTS.

    `slot` is a TEXT column, so a plain `ORDER BY slot` is ALPHABETICAL —
    breakfast, dinner, lunch, snack — and every day this app has ever shown
    printed dinner before lunch because of it (Loop Board "A day's meals
    list in alphabetical order, so dinner prints before lunch"). The right
    order was written down in DAY_SLOTS the whole time; the query simply
    never asked for it. So it is read off that tuple rather than the four
    words being spelled out a second time: a day that ever gains a slot is
    told once, up there, and every query using this follows.

    A slot DAY_SLOTS doesn't know sorts LAST rather than vanishing — an
    unexpected row is still somebody's food, and dropping it to tidy a sort
    would be the worse bug. Interpolated into SQL like _SQL_PERIOD_START
    below, and safe for the same reason: the values are this module's own
    constant, never anything a caller can hand in.
    """
    whens = " ".join(f"WHEN '{slot}' THEN {i}" for i, slot in enumerate(DAY_SLOTS))
    return f"CASE {column} {whens} ELSE {len(DAY_SLOTS)} END"


# The longest period the app will plan in one go. Not a data-model limit —
# nothing below cares — but a guard on the generation call, which asks the
# model for every day at once and is already the slowest thing in the app at
# seven. Named rather than inlined so the API, the UI and the tool schema all
# refuse the same number.
MAX_PERIOD_DAYS = 28

# From which day of a period the app's attention moves on to the NEXT one.
# An index into the period, 0 = its first day — so for the default
# Monday-to-Sunday week it is a weekday, and 4 is Friday (Emily, 2026-09-11:
# from Friday, "this week" means next week when this week was never
# planned; two days left is not a week to plan). A household on another
# rhythm gets the same distance in — the fifth day of a Saturday-start
# week is Wednesday — because the point is how much of the period is left,
# not what the calendar calls the day. Two readers, deliberately ONE number:
# suggest_planning_period skips an unplanned current period from here on,
# and get_week_planning_nudge offers the following period from here on when
# the current one is planned. See _attention_moves_on.
PLAN_AHEAD_FROM_WEEKDAY = 4


class SlotRefused(ValueError):
    """
    A plan write declining for a reason the HOUSEHOLD should read, in the
    words it is declining in — "that one's already been cooked", not "no
    slot 4021 on that week's plan."

    It exists because the route in front of these functions turns every
    ValueError into a 404 with the message attached, and the screen had
    started printing that message. That is right for the two or three
    sentences written for a person and wrong for everything else: an id, a
    component-plan mismatch or a raw Python exception on screen reports an
    app that did exactly the right thing as broken. Subclasses ValueError
    so every existing `except ValueError` still catches it; the route
    simply asks first.
    """


# The one sentence every "you can't change that night" refusal says, so a
# fourth door cannot invent a fourth wording for one fact. add_dish_day has
# said it since 2026-09-16; the swap doors below say it now.
NIGHT_GONE = "That night’s already gone."

# The same fact as a FRAGMENT, for the one caller that interpolates rather
# than prints: the chat change card renders a refused row as "<dish> stays
# — <why>" and its toast as "I left the week as it was — <why>."
# (changeRowHtml in shell.js), so a whole sentence with a stop on the end of
# it lands there as "— That night’s already gone.." Lower case, no stop,
# reads as the end of somebody else's sentence.
NIGHT_GONE_WHY = "that night has already gone"


def night_has_gone(meal_date: str) -> bool:
    """
    Whether `meal_date` is behind the day the HOUSEHOLD is having.

    The one test every person-initiated change to the week has to pass,
    written once so the doors that ask it cannot drift about what "already
    gone" means. add_dish_day (the Review stepper's "+", 2026-09-16) and
    drop_dish_from_day (its "−") ask the same question inline; the swap
    doors ask it through here.

    THE HOUSEHOLD'S TODAY, never the server's, and that is the whole care
    in it. The container runs UTC and households default to
    America/Toronto, so from 8pm local the server's date is already
    tomorrow — on that clock this would refuse TONIGHT for four hours
    every evening, which is a worse bug than the one it fixes.

    Strictly BEFORE, so today itself is never refused: changing tonight's
    dinner is the most ordinary thing anybody does here, and a check that
    took it away would be the same bug wearing the other hat.

    ISO dates compare as strings, so a date somebody wrote by hand that
    isn't one simply fails this test and meets whatever its caller already
    does with it. This is not the place to start validating dates.

    **It opens a connection** — _household_today reaches
    cooker.household_now, which has one of its own — so every caller has to
    ask it with none of its own open. Read from inside a write transaction
    it would be a nested get_conn, and this repo has twice paid for one of
    those with an intermittent "database is locked" rather than a wrong
    answer: the kind of failure no test sees until production.
    """
    return meal_date < _household_today().isoformat()


# The SQL form of plan_period(), for the two places that have to resolve the
# period inside a query rather than in Python (see _current_weekly_plan_row).
# Kept beside the Python version because they have to agree exactly, and a
# drift between them is invisible: the query would simply return a different
# plan than every other reader thinks is current.
_SQL_PERIOD_START = "COALESCE(NULLIF(content_start_date, ''), week_start_date)"
_SQL_PERIOD_LAST_OFFSET = (
    "(CASE WHEN content_start_date = '' AND day_count = 0 THEN 6 ELSE day_count - 1 END)"
)
# "This plan's last day is before the bound date" — the period end resolved
# in SQL, so retire_expired_drafts, _current_weekly_plan_row's fallback and
# _pending_draft_over agree with plan_period exactly. Takes one bound
# parameter, and ALL THREE readers must bind it with the same clock: this
# comment named only the first two, and the third was duly left on the
# server's date when the other two moved to the household's (2026-09-15).
# If you add a fourth, name it here.
_SQL_EXPIRED_BEFORE = (
    f"date({_SQL_PERIOD_START}, '+' || {_SQL_PERIOD_LAST_OFFSET} || ' days') < date(?)"
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


def clear_plan_slot(weekly_plan_id: int, meal_date: str, slot: str, conn=None) -> int:
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

    **A DONE prep row goes with the meal, and that is a real loss to know
    about**: a fridge move somebody actually ticked off is history, and
    deleting the meal deletes the record of the work. It is deliberate and
    predates this note — a reminder for a meal that no longer exists has
    nowhere honest to live, and get_prep_schedule drops a dangling row on
    read anyway — but a caller whose ticket says "a fridge move already
    done stays done" is not getting that from here.

    `conn` is the same arrangement plan_slot_open and
    _reverse_meal_grocery_contributions already have, and it exists for the
    same reason: this function DELETES a row, and a caller that has to put
    something in its place (tonight.tonight_night_off, which follows it with
    plan_slot_empty) must do both or neither — the gap between two commits
    is a genuinely ABSENT slot, the one state schema.sql, audit_plan_slots
    and plan_slot_open's own docstring all say cannot exist. Given a
    connection this reads and writes on it and neither commits nor closes;
    the caller owns both, and owns taking the write lock (BEGIN IMMEDIATE)
    before its first read. The leftover source's grocery rescale runs INSIDE
    that transaction, exactly as _replace_slot_entries does it. Left unset,
    every other call site behaves precisely as before.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? "
        "AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, meal_date, slot, household_id()),
    ).fetchall()
    approved = False
    if not own_conn and rows:
        plan_row = conn.execute(
            "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
        approved = bool(plan_row) and plan_row["status"] == "approved"
    if own_conn:
        conn.close()
    for row in rows:
        # If this row was reheating an earlier night's batch, tell that
        # source before the row disappears out from under it — see
        # _unlink_leftover_target.
        if own_conn:
            _unlink_leftover_target(weekly_plan_id, row["id"])
        else:
            # Handed a connection it defers the source's grocery rescale to
            # us; on an approved week that happens right here, inside the
            # transaction (the shape _replace_slot_entries uses).
            rescale_source_id = _unlink_leftover_target(weekly_plan_id, row["id"], conn=conn)
            if rescale_source_id is not None and approved:
                _rescale_leftover_source_grocery(rescale_source_id, row["id"], conn=conn)
        # Same care swap_meal_in_plan takes — anything this entry put on the
        # list comes back off, and anything already in a cart is left alone.
        _grocery._reverse_meal_grocery_contributions(row["id"], conn=None if own_conn else conn)
    if rows:
        if own_conn:
            conn = get_conn()
        marks = ",".join("?" * len(rows))
        # Prep rows for a meal that no longer exists go with it.
        #
        # BOTH HALVES OF WHAT THIS COMMENT USED TO SAY WERE FALSE, and it
        # was corrected on 2026-09-21 rather than left, because a false
        # comment is what the next reader acts on — one already did. It
        # said _replace_slot_entries "applies the same reasoning" and that
        # a path forgetting this "still shows nothing stale". Neither was
        # true: that write deleted prep rows only when asked, so every
        # ordinary swap left them standing, and get_prep_schedule is the
        # ONLY reader that drops a dangling row on read.
        # prep_sessions._prep_task_rows (the Cook tab's prep session),
        # defrost.get_defrost_schedule (the chat answer to "what do I need
        # to defrost?"), defrost.get_defrost_today, _pending_thaw_count
        # (the receipt's thaw line) and defrost's own settled-move reads
        # all show it. Measured through real doors, not reasoned.
        #
        # Since 2026-09-22 the first half IS true again — every door of
        # _replace_slot_entries releases the rows (_release_prep_rows).
        # The second half is still false and always will be, so do not
        # read "the swap covers it now" as "a new path need not".
        #
        # ONE THING _release_prep_rows DOES THAT THIS DOES NOT: a fridge
        # move already TICKED becomes a held thing there, so the thawed
        # meat outlives the meal. Here it is still destroyed with the
        # meal — see the docstring above. This function's callers are a
        # night nobody is home, a night called off, and generation's own
        # tidying, and whether each of those should hold the meat too is a
        # product question nobody has asked; the swap is the one Emily
        # answered.
        conn.execute(
            f"DELETE FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id IN ({marks})",
            (household_id(), *[r["id"] for r in rows]),
        )
        conn.execute(
            f"DELETE FROM meal_plan_entries WHERE id IN ({marks}) AND household_id = ?",
            (*[r["id"] for r in rows], household_id()),
        )
        if own_conn:
            conn.commit()
            conn.close()
    return len(rows)


def plan_slot_empty(
    weekly_plan_id: int,
    meal_date: str,
    slot: str,
    reason: str,
    derived_from: dict | None = None,
    conn=None,
) -> dict:
    """
    Record a slot as deliberately empty — `planned_empty`.

    Not a gap and not a question. This is a slot that needs no decision and
    must NEVER be offered to the household as one. Three things produce it:
    a dinner on a night nobody is home ("You're out — I've planned nothing
    and bought nothing"), a meal category the household has asked for zero
    of, and a night the household called off outright (tonight.py's
    "Not tonight — we're going out").

    `reason` is what the draft screen shows in place of a meal, so it has
    to read as a statement, never as an apology or an ask.

    `conn` is the sibling of plan_slot_open's own, added for the same
    reason and late: a caller that CLEARS a slot and then states it empty
    has to do both or neither, because the gap between two commits is a
    genuinely absent slot. Given a connection this writes on it and neither
    commits nor closes — the caller owns both. Left unset, every other call
    site behaves exactly as before.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, reasoning, derived_from_json) "
        "VALUES (?, ?, ?, ?, 'planned_empty', ?, ?)",
        (household_id(), meal_date, slot, weekly_plan_id, reason, json.dumps(derived_from or {})),
    )
    entry_id = cur.lastrowid
    if own_conn:
        conn.commit()
        conn.close()
    return {"entry_id": entry_id, "date": meal_date, "slot": slot, "slot_state": "planned_empty", "reason": reason}


def plan_slot_open(
    weekly_plan_id: int,
    meal_date: str,
    slot: str,
    open_reason: str,
    options: list[dict] | None = None,
    derived_from: dict | None = None,
    conn=None,
) -> dict:
    """
    Record a slot as `open` — a decision the app is genuinely handing back.

    `open_reason` is a full sentence naming the CONSTRAINT that caused it,
    not an apology: "Wednesday I'd rather ask than guess: after Monday's
    chili, everything I have that takes 30 minutes or less repeats something
    you've just eaten." Naming the constraint is what makes the ask read as
    diligence rather than failure.

    An open slot is still a slot. What it must never be is absent — a
    silently missing slot is the bug this whole state exists to make
    impossible.

    `conn` is for one caller and is not part of the assistant-facing API,
    the same arrangement _reverse_meal_grocery_contributions already has:
    drop_dish_from_day takes a meal away and puts this row in its place,
    and those two have to be one transaction or the gap between them is a
    genuinely absent slot. Given a connection, this writes on it and
    neither commits nor closes — the caller owns both. Left unset, every
    other call site behaves exactly as before.
    """
    if not (open_reason or "").strip():
        raise ValueError("An open slot needs a reason naming the constraint that caused it.")
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, open_reason, derived_from_json) "
        "VALUES (?, ?, ?, ?, 'open', ?, ?)",
        (
            household_id(), meal_date, slot, weekly_plan_id, open_reason,
            json.dumps({**(derived_from or {}), "options": options or []}),
        ),
    )
    entry_id = cur.lastrowid
    if own_conn:
        conn.commit()
        conn.close()
    return {
        "entry_id": entry_id, "date": meal_date, "slot": slot,
        "slot_state": "open", "open_reason": open_reason, "options": options or [],
    }


def drop_dish_from_day(weekly_plan_id: int, entry_id: int, open_reason: str | None = None,
                       confirm_cooked: bool | str = False) -> dict:
    """
    Take one day away from a dish, and hand that slot back as a question.

    `open_reason` is the question's own sentence. Left unset it is the
    stepper's ("You cut … back, so this one is yours to fill"); the
    allergen sweep (tools.allergen_gate.sweep_plan) passes its own, because
    a slot opened over an allergy has to say so rather than claim the
    household cut something back.

    The Review screen's stepper (Emily's approved design, 2026-09-09): a
    dish covering four mornings should come down to three without spending
    a chat turn on it. What it must never do is leave the morning ABSENT —
    a slot is one of three states, never present-or-missing, and a silently
    missing slot is the bug plan_slot_open/plan_slot_empty exist to prevent.

    Removal is BY ID and never by (date, slot). That distinction is the
    whole of a real bug this function shipped with: clear_plan_slot deletes
    every row in a slot, and a day holds TWO rows at slot='snack' by
    default (preferences.resolve_snacks_per_day), so stepping one snack
    down from two days destroyed the day's OTHER snack along with it —
    grocery reversal and all, on an approved week. swap_meal_in_plan's own
    docstring had already written this rule down ("a slot holding two
    snacks would lose both to a swap that was only ever about one of
    them"); this composes plan_slot_open with the same by-id removal that
    function does, rather than with clear_plan_slot.

    `open` and not `planned_empty`, deliberately: planned_empty means
    nobody is home, or the household asked for none of that meal, and it
    must NEVER be offered as a decision. Cutting one dish back is neither
    of those — something still has to go on that plate, and only the
    household knows what.

    A chain SOURCE is refused rather than dropped. _unlink_leftover_target
    covers the target side — a reheat night going away tells the cook night
    that fed it — but nothing covers the reverse, so removing a night that
    feeds another one leaves that other night holding a real recipe it was
    never planned to cook, with the doubled batch's groceries just reversed
    out from under it. Refusing and naming the night that depends on it is
    the honest answer; quietly promoting somebody's reheat into a cook is
    not. (The underlying gap is pre-existing and shared with every chat
    swap. What is new here is a control that would otherwise hit it by
    arithmetic rather than by a decision.)

    A night already COOKED is refused too, and that one was missing from
    this function for two days while its sibling add_dish_day grew the
    check. It is reachable whenever a dish covers two nights and the LATER
    one has been ticked, since this always targets the last day the dish
    covers: the cooked_status went, the inventory stayed depleted for a
    meal now off the plan, and the ingredients for a meal somebody had
    eaten came off the shopping list. A tick is a record of something that
    happened, and no arithmetic on a plan gets to delete one.

    And a night that has already GONE BY, which is the same refusal
    add_dish_day grew on 2026-09-16 — the "+" in this very stepper. The
    "−" was left alone then so that branch stayed the size of its ticket;
    this is the other half. See the comparison below for what going
    through wrote, and for why the clock it reads is the household's.

    `confirm_cooked` is the household's explicit yes to the one question
    this can ask (Emily, 2026-09-24, option B — ask first). When the dish
    was cooked double for later nights its cook moves onto the first of
    them, and that night's leftovers row goes. If that row has been ticked
    cooked, the move would delete the tick without a word — so without
    this flag nothing is written and the answer is `needs_confirmation`,
    carrying the question (`message`) and the button's word
    (`confirm_label`). Never set on a first call, and never by a caller
    that is not a person (allergen_gate.sweep_plan leaves it off). See
    _drop_by_cooking_on_the_fed_night for where it is read.
    """
    # The household's day, resolved BEFORE the first connection below is
    # opened rather than beside the comparison it is for.
    # _household_today reaches cooker.household_now, which opens a
    # connection of its own; read from inside this function's own write
    # transaction it would be a nested get_conn, and this repo has twice
    # paid for one of those with an intermittent "database is locked"
    # rather than a wrong answer — the kind of failure no test sees until
    # production. discard_draft_plan resolves its own clock the same way
    # and for the same reason, and the cost of reading it early is one
    # small SELECT.
    today = _household_today().isoformat()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.component_category,
               mpe.derived_from_json, mpe.cooked_status,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.id = ? AND mpe.household_id = ? AND mpe.weekly_plan_id = ?
        """,
        (entry_id, household_id(), weekly_plan_id),
    ).fetchone()
    conn.close()
    # Household- and plan-scoped both, same as the in-place swap: an entry id
    # from another household (or another week) is a 404, not a quiet edit of
    # somebody else's dinner.
    if not row:
        raise ValueError(f"No meal {entry_id} on that week's plan.")
    if row["component_category"]:
        # A component-based plan keys its rows by category rather than by
        # date and slot, so it has no day slot to hand back at all.
        raise ValueError("That plan is built from components, not day slots.")
    if row["slot_state"] != "planned" or not row["meal"]:
        raise ValueError("There's no meal on that slot to take away.")

    meal_date, slot = row["date"], row["slot"]
    # The dish this row READS AS, not the text it stores — the same
    # resolution add_dish_day makes and for the same reason. A confirmed
    # reheat night is labelled on screen with the dish it reheats, so a
    # sentence built from its own freeform text ("You cut Leftover chili
    # back") names something the household was never shown.
    from . import leftovers as _leftovers
    chains = _leftovers.plan_leftover_chains(weekly_plan_id)
    chained = chains["leftovers"].get(row["id"])
    dish = chained["source"]["meal"] if chained else row["meal"]

    if (row["cooked_status"] or "") == "done":
        # An answer, not an error: the same `refused` shape this function
        # already uses for a chain source, so the screen shows the sentence
        # rather than a generic failure. Nothing is written.
        return {
            "status": "refused",
            "date": meal_date,
            "slot": slot,
            "dish": dish,
            "message": (
                f"{dish} on {date.fromisoformat(meal_date).strftime('%A')} has already "
                "been cooked — I’ll leave that one on the week."
            ),
        }

    if meal_date < today:
        # A night that has already gone by. Nothing on the screen kept
        # these off it — unlike the "+", whose picker filters them out,
        # "−" simply takes the LAST day the dish covers, so a dish whose
        # days are ALL behind today (a week reviewed on Wednesday, a tab
        # drawn yesterday, a retried POST, a direct call) hands one over.
        # Reproduced before this went in: it went straight through and
        # wrote two things, neither recoverable from the screen — the
        # meal's grocery contribution reversed, for food that was in all
        # likelihood already bought, and the night handed back as an `open`
        # question, a decision returned on a day nobody can act on.
        #
        # THE HOUSEHOLD'S TODAY, never the server's. The container runs UTC
        # and households default to America/Toronto, so from 8pm local the
        # server's date is already tomorrow — on that clock this would
        # refuse TONIGHT for four hours every evening, which is a worse bug
        # than the one it fixes.
        #
        # Strictly BEFORE, so today itself is never refused: taking
        # tonight's dinner off the week is an ordinary thing to ask for,
        # and a check that took it away would be the same bug wearing the
        # other hat.
        #
        # ABOVE the chain refusal below, deliberately. That one's sentence
        # names a remedy — "change that first and I'll take this one off" —
        # which cannot work on a night that is already over, so it must
        # never be the answer a past night gets. The cooked refusal keeps
        # its place above this one: both sentences are true of a past night
        # somebody cooked, and "already been cooked" is the more specific
        # of the two and says why the record is being kept.
        return {
            "status": "refused",
            "date": meal_date,
            "slot": slot,
            "dish": dish,
            "message": "That night’s already gone.",
        }

    open_reason = (open_reason or "").strip() or f"You cut {dish} back, so this one is yours to fill."

    fed = fed_nights_in_eating_order(chains["sources"].get(entry_id), meal_date)
    if fed:
        # This night was cooked double for later ones. Until 2026-09-24 that
        # was a REFUSAL — "…also feeds Friday's lunch, change that first and
        # I'll take this one off" — and Emily's standing rule since the
        # night off learned the same lesson two days earlier is that there
        # is no such answer: "the job of Pomona is to do all that planning
        # work." So the app does the knock-on planning itself, by the one
        # rule tonight.tonight_night_off already follows for the identical
        # shape (its 'cook_on_fed', Emily's option A of 2026-09-22): the
        # cook MOVES ONTO THE FIRST NIGHT IT WAS FEEDING, the later fed
        # nights keep their leftovers — now from the new cook night — and
        # the night the household stepped down comes back as a question,
        # exactly as every other "−" leaves one.
        #
        # ONE decision and ONE move, shared with the night off
        # (fed_nights_in_eating_order, move_cook_onto_fed_night), because
        # two implementations of "which night does the cook land on" is the
        # failure this file's log records most often.
        #
        # The one caller today that is not a person is
        # allergen_gate.sweep_plan, and this changes what it does with a
        # clashing CHAIN SOURCE. Measured on a peanut chain rather than
        # reasoned about, same seed both ways: main refuses the cook night,
        # then drops the reheat, and the week keeps the allergen on the
        # COOK night; here the cook moves onto the reheat night, the cook
        # night opens, and the sweep's own later pass finds that reheat row
        # gone and logs it. One allergen night survives either way,
        # `slots_opened` is 1 either way and audit_plan_slots is clean
        # either way — a different night, not a worse week. That the sweep
        # cannot clear a whole chain in one pass is older than this and is
        # its own card.
        #
        # What differs, and it is the whole of the difference: the night off
        # is a night nobody is eating, so the batch keeps its size and
        # tonight's share goes in the freezer. This is the household asking
        # for one FEWER night of the dish, so the batch really does shrink —
        # _unlink_leftover_target takes the new cook night off the source's
        # make_double_for and re-says its note, and the rescale after the
        # commit brings an approved week's line down with it. Freezing a
        # portion of a meal nobody has cooked, for a night the household has
        # just asked to fill with something else, would be the "−" doing
        # something no other "−" does.
        return _drop_by_cooking_on_the_fed_night(
            weekly_plan_id, entry_id, meal_date, slot, dish, fed[0], open_reason,
            confirm_cooked=confirm_cooked,
        )
    # ONE connection, ONE commit, for all four steps — the same shape and
    # for the same reason as retire_overlapping_plans (2026-09-06). These
    # used to be four separate commits, and the gap between the delete and
    # the open row is the one state this app's rule says can never exist:
    # a reviewer forced a RuntimeError inside plan_slot_open and got a
    # genuinely ABSENT slot, its grocery line already reversed, under a
    # screen reading "nothing changed". Either the meal is gone and a
    # question stands in its place, or nothing moved.
    #
    # sqlite3 connects with the legacy isolation_level of "", so the first
    # write below opens a transaction implicitly and there is no BEGIN to
    # issue. Nothing called from inside here may open a second connection —
    # it would block on this one's write lock and time out — which is why
    # _unlink_leftover_target, _reverse_meal_grocery_contributions and
    # plan_slot_open all take the connection rather than making their own.
    #
    # BY ID. See the docstring: a slot legitimately holding two snacks must
    # lose only the one being stepped down. The order inside is the care
    # clear_plan_slot and swap_meal_in_plan take — tell any chain that was
    # reheating this night before the row goes, then put back anything it
    # contributed to the shopping list (leaving anything already in a cart
    # alone).
    conn = get_conn()
    rescale_source_id = None
    try:
        rescale_source_id = _unlink_leftover_target(weekly_plan_id, entry_id, conn=conn)
        _grocery._reverse_meal_grocery_contributions(entry_id, conn=conn)
        conn.execute(
            "DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        )
        plan_slot_open(
            weekly_plan_id, meal_date, slot, open_reason,
            derived_from={"constraint": "household_cut_back", "dish": dish},
            conn=conn,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if rescale_source_id is not None and _weekly_plan_is_approved(weekly_plan_id):
        # The one step that cannot join the transaction above: it re-ingests
        # through add_grocery_item and the whole recipe ingest tree, each of
        # which opens its own connection (see _unlink_leftover_target's
        # `conn` note). So it runs AFTER the commit, and a failure here is
        # logged rather than raised — the day has already been handed back,
        # and the cost of not shrinking an approved source's line is one
        # night's share of over-buying, where raising would report "nothing
        # changed" over a change that did happen. Same call
        # _taste_verdict_for_slot makes, for the same reason: the household's
        # answer must not fail over a trim. Running after the dropped entry
        # has already been reversed and deleted, rather than before, is safe
        # for the reason that function excludes `unlinked_entry_id` at all:
        # what must not happen is its share being folded into this rounding
        # and subtracted back out afterwards, and a row that no longer
        # exists cannot be.
        try:
            _rescale_leftover_source_grocery(rescale_source_id, entry_id)
        except Exception:
            logger.exception(
                "Rescaling leftover source %s after dropping entry %s failed; the day was "
                "handed back and its line may be over-bought by one night's share",
                rescale_source_id, entry_id,
            )
    return {
        "status": "dropped",
        "date": meal_date,
        "slot": slot,
        "dish": dish,
        "open_reason": open_reason,
        # get_week_menu's own day dict, exactly as the in-place swap hands
        # one back, so the Review screen can splice the changed day into the
        # week it is already holding — one shape, one renderer, no second
        # round trip. Looked up here rather than borrowed from
        # swap_in_place._refreshed_day: this module owns get_week_menu, and
        # a two-line lookup is not the kind of thing worth closing an import
        # cycle for.
        "day": _day_of(weekly_plan_id, meal_date),
    }


# Where the "−" leaves its undo record: on the derived_from of the `open`
# row that stands on the night the household stepped down. Written once by
# the tap, read once by drop_dish_undo — the shape the night off's own
# NIGHT_OFF_UNDO_KEY takes, under its own key so the two can never read each
# other's record off one week.
DROP_DISH_UNDO_KEY = "drop_dish_undo"

# The week moved between the household seeing it and tapping. An answer,
# not an error — the same `refused` shape every other sentence here takes.
DROP_DISH_CHANGED = "That dish changed just now, so I’ve left the week as it is."


def _drop_by_cooking_on_the_fed_night(
    weekly_plan_id: int, entry_id: int, meal_date: str, slot: str,
    dish: str, target: dict, open_reason: str, confirm_cooked: bool | str = False,
) -> dict:
    """
    One fewer night of a dish that was cooked double for later ones: the
    cook moves onto the first night it was feeding, the batch comes down by
    the night that was stepped away, and the stepped-away night comes back
    as a question.

    See the comment at the call site for why this is not a refusal any
    more, and for what it shares with tonight.tonight_night_off.

    ONE transaction, and the write lock is taken before the first read —
    the shape _replace_slot_entries and tonight_night_off both use, and for
    the reason that function learned the hard way: the decision that chose
    this target was made on a connection of its own and the week can move
    underneath it, so it is checked again under the lock and refused in a
    sentence rather than written on top of somebody else's change.
    """
    from . import leftovers as _leftovers

    conn = get_conn()
    changed = {
        "status": "refused", "date": meal_date, "slot": slot, "dish": dish,
        "message": DROP_DISH_CHANGED,
    }
    try:
        conn.execute("BEGIN IMMEDIATE")
        # The same decision again, under the lock. A chain the household (or
        # the other phone, or a chat turn) has broken since is a different
        # week from the one this answer was computed for.
        fed = fed_nights_in_eating_order(
            _leftovers.plan_leftover_chains(weekly_plan_id, conn=conn)["sources"].get(entry_id),
            meal_date,
        )
        if not fed or fed[0]["entry_id"] != target["entry_id"]:
            conn.rollback()
            return changed
        target = fed[0]

        # The night the cook lands on has been ticked cooked: the move
        # deletes that row, tick and all. Asked, never done silently
        # (Emily, 2026-09-24, option B). Read HERE, under the lock and on
        # this connection, rather than beside the first decision — a tick
        # the other phone made a second ago is exactly the one this has to
        # see. Nothing has been written yet.
        if not cooked_move_confirmed(confirm_cooked, target) and fed_night_is_cooked(conn, target):
            conn.rollback()
            return cooked_fed_night_question(target, dish, {"date": meal_date, "slot": slot})

        old_ref = f"{meal_date}:{slot}"
        new_ref = f"{target['date']}:{target['slot']}"
        all_rows = conn.execute(
            "SELECT id, derived_from_json FROM meal_plan_entries WHERE weekly_plan_id = ? "
            "AND household_id = ? AND component_category IS NULL",
            (weekly_plan_id, household_id()),
        ).fetchall()
        touched = _plan_undo.touched_ids(
            all_rows, {entry_id, target["entry_id"]}, [old_ref, new_ref],
        )
        snap = _plan_undo.snapshot(conn, touched)

        # The new cook night stops being a night the batch FEEDS — it is the
        # night that cooks it. _unlink_leftover_target is the one place that
        # takes a night off a source's make_double_for and re-says its note,
        # and it reads the target's own links_to, so it runs while that row
        # is still there.
        source_id = _unlink_leftover_target(weekly_plan_id, target["entry_id"], conn=conn) or entry_id
        move_cook_onto_fed_night(conn, weekly_plan_id, entry_id, meal_date, slot, target)
        holder = plan_slot_open(
            weekly_plan_id, meal_date, slot, open_reason,
            derived_from={"constraint": "household_cut_back", "dish": dish},
            conn=conn,
        )
        holder_id = holder["entry_id"]
        _plan_undo.stamp(conn, holder_id, DROP_DISH_UNDO_KEY, {
            "kind": "cook_on_fed", "dish": dish, "source_entry_id": source_id,
            "weekly_plan_id": weekly_plan_id, **snap,
            "after": _plan_undo.fingerprint(conn, touched, holder_id),
        })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _rescale_after_a_chain_moved(weekly_plan_id, source_id, target["entry_id"])
    moved_to = fed_night_label(target)
    return {
        "status": "dropped",
        "date": meal_date,
        "slot": slot,
        "dish": dish,
        "open_reason": open_reason,
        "moved_to": target["date"],
        "moved_to_label": moved_to,
        "said": f"{_weekday_of(meal_date)}’s yours to fill. {dish} moved to {moved_to}.",
        "can_undo": True,
        "undo_entry_id": holder_id,
        "day": _day_of(weekly_plan_id, meal_date),
    }


def _rescale_after_a_chain_moved(weekly_plan_id: int, source_entry_id: int, unlinked_entry_id: int) -> None:
    """
    Bring an approved week's line down (or back up) to the batch as it now
    stands — the one step that cannot join the transaction above, for the
    reason drop_dish_from_day's own tail gives: it re-ingests through
    add_grocery_item and the whole recipe ingest tree.

    A failure is logged rather than raised. The night has already been
    handed back, and raising would report "nothing changed" over a change
    that did happen; the cost is one night's share of over- or under-buying
    on a line the household can see and correct.
    """
    if not _weekly_plan_is_approved(weekly_plan_id):
        return
    try:
        _rescale_leftover_source_grocery(source_entry_id, unlinked_entry_id)
    except Exception:
        logger.exception(
            "Rescaling leftover source %s after moving its cook failed; the week is "
            "planned and its line may be a night's share out", source_entry_id,
        )


def drop_dish_undo(weekly_plan_id: int, entry_id: int) -> dict:
    """
    Undo on the "−" toast: put the night back, and with it every night the
    answer touched — the cook on its own night, the leftovers row it landed
    on, the later fed nights' chain as it read, its fridge moves on their
    old dates, and an approved week's shopping line back at the batch's
    full size.

    `entry_id` is the `open` row the "−" left behind (`undo_entry_id` on
    its result) — the handle rather than a date, because a "−" can land on
    any night and any slot, and a day legitimately holds two snacks.

    Only while nothing has changed since: the rows it touched must still
    look exactly as the tap left them, or nothing is written and `status`
    is 'refused' with a sentence — the same answer-not-error rule the tap
    itself follows. One transaction, lock first, for the reason
    _drop_by_cooking_on_the_fed_night gives.

    The grocery LIST is only ever recomputed, never un-reversed: a line
    already in a cart or through the till was left alone on the way down
    and is left alone here too.
    """
    nothing = {"status": "refused", "message": "There’s nothing to put back."}
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        holder = conn.execute(
            "SELECT id, date, slot, derived_from_json FROM meal_plan_entries "
            "WHERE id = ? AND household_id = ? AND weekly_plan_id = ?",
            (entry_id, household_id(), weekly_plan_id),
        ).fetchone()
        record = _plan_undo.read(holder, DROP_DISH_UNDO_KEY) if holder else None
        if not record:
            conn.rollback()
            return nothing
        if not _plan_undo.still_as_left(conn, record, holder["id"]):
            conn.rollback()
            return {
                "status": "refused", "date": holder["date"], "slot": holder["slot"],
                "message": "That night’s changed since, so I’ve left it as it is.",
            }
        meal_date, slot = holder["date"], holder["slot"]
        _plan_undo.restore(conn, record, holder["id"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # The batch is whole again, so the line goes back with it. Nothing is
    # excluded from the recipe group this time (0 names no row): the
    # leftovers night is back and the ingest reads it as the reheat it is.
    _rescale_after_a_chain_moved(weekly_plan_id, record.get("source_entry_id") or 0, 0)
    dish = record.get("dish") or ""
    return {
        "status": "restored", "date": meal_date, "slot": slot, "dish": dish or None,
        "said": f"{dish} is back on {_weekday_of(meal_date)}." if dish else "Put back.",
        "day": _day_of(weekly_plan_id, meal_date),
    }


def _day_of(weekly_plan_id: int, meal_date: str) -> dict | None:
    """get_week_menu's own day dict — the shape both halves of the stepper
    hand a changed day back in, so a screen can splice it into the week it
    is already holding rather than re-read the lot."""
    return next(
        (d for d in (get_week_menu(weekly_plan_id).get("days") or [])
         if d.get("date") == meal_date),
        None,
    )


def add_dish_day(
    weekly_plan_id: int, entry_id: int,
    target_entry_id: int | None = None, target_date: str | None = None,
) -> dict:
    """
    Put a dish the week already has onto one more day — the Review screen's
    stepper going UP.

    Down is arithmetic and up is not, which is why this took a second pass
    to build at all. Every candidate day already holds something: a dish,
    or a question the household has been handed. So going up is never
    "add" — it is always REPLACE, and the only honest way to do it is to
    show what each day is holding and let the household say which one they
    are willing to lose. That decision is the screen's; this function's
    job is to carry it out against the day they picked, by id.

    `entry_id` is any night the dish already covers (the screen sends the
    first), and it is read for the dish's NAME and its recorded food
    groups — never for its recipe row directly, since plan_meal resolves a
    saved recipe by name exactly as a chat swap does.

    **THAT NAME IS RESOLVED THROUGH THE CHAIN, and it has to be.** A row on
    the Review screen is labelled by mealDisplayName, which for a confirmed
    reheat night answers with the dish being reheated rather than with the
    row's own freeform text — so a row reading "Beef Bulgogi · nothing to
    cook · 1 lunch" is a night whose stored text is "Leftover bulgogi
    bowls". Deriving the name from the row alone therefore wrote a DIFFERENT
    dish from the one the household tapped: a night reading as a reheat with
    no batch behind it, nothing bought for it, and the dish they agreed to
    lose gone. Reachable for any confirmed chain whose reheat lands in a
    different meal-type group from its cook — a dinner cooked double for the
    next day's lunch, which repair_leftover_chains accepts and the
    generation prompt asks for by name. Same class as the two blockers
    already fixed on this screen: a control labelled with one dish acting on
    another. plan_leftover_chains is the one reader of that pairing, and it
    is what mealDisplayName's own data came from, so it is what this asks.

    `target_entry_id` is the slot being taken over, and it must be
    `planned` or `open`. A `planned_empty` target is refused outright.
    Nothing on the screen offers one — but three separate bugs in this app
    have come from code treating that state as a missing meal, and the
    rule holds at the write, not only at the control: a night nobody is
    home is not a night with a free plate on it. A target already COOKED is
    refused for the same reason one level along: ticking it off wrote a
    record, depleted the inventory and fed somebody, and replacing the row
    would destroy all three — the cooked_status, and the shopping line for
    a meal that has already been eaten.

    Pass `target_date` instead of `target_entry_id` for a genuinely EMPTY
    night — the Check-the-week strip's "Nothing yet", meaning no row in
    meal_plan_entries at all (get_week_menu returns None for a slot that
    is truly absent, never a stand-in row). There is no id to send because
    there is nothing to send one for, so the caller names only the day;
    the slot is the one `entry_id`'s dish already sits in
    (`source["slot"]`) — the picker only ever offers days within the same
    meal type. Exactly one of `target_entry_id`/`target_date` must be
    given. The target is still resolved fresh, by (date, slot), rather
    than trusted from the screen: the picker was drawn from a snapshot,
    and if a chat swap or another tab filled that night in the gap, what
    is there now gets exactly the refusals above — a night that has since
    gone `planned_empty` or been cooked is still not a night to plan into,
    however the caller addressed it.

    A night that has already GONE BY is refused the same way, on the date
    alone and so for both address forms — including a genuinely empty one,
    which has no row to be refused on anything else. Only the picker's
    `day.isPast` filter used to keep those days off the strip, which is a
    screen's rule and not the week's. Read on the HOUSEHOLD's clock, or
    this would refuse tonight for the four hours a day the server is
    already on tomorrow; today itself is never refused.

    Breaking a chain on the way in is ALLOWED and reported, which is
    deliberately not what the stepper going down does. Down DELETES, so a
    night that was eating off the removed one is left holding a recipe
    nobody cooks with nothing bought for it, and that is refused. This
    REPLACES, and the freed night keeps its row either way. `unchained`
    names those nights and the screen says so out loud — a screen must not
    refuse the mirror of what it silently allows.

    **How much a freed night gets re-bought is CONDITIONAL, and an earlier
    version of this docstring said it flatly.** swap_meal_in_plan's
    _reingest_unlinked_entries only buys for an entry with a real
    `recipe_id`, on a plan that is already approved; a freed night written
    as freeform text ("Leftover bulgogi") has no recipe to ingest and gets
    nothing, and a DRAFT buys nothing for anything because approval is what
    puts a week on the list at all. So the guarantee here is narrower than
    "it re-buys": the night keeps its slot and its own text, and it is
    bought for exactly when it has a recipe on an approved week. That is
    why the toast says the chain is broken rather than claiming the night
    is now a cook — see addDishToastText, which was caught claiming the
    wider thing over a row that still read "nothing to cook".

    The write itself is swap_meal_in_plan, unchanged and by id. That is
    the whole point: reversing the displaced dish's groceries, telling any
    chain that was reheating it, re-buying for nights that were eating off
    it, and the taste verdict on the dish going in are all things that
    function already does correctly, and a second implementation of them
    here is how two paths end up disagreeing about one week's shopping
    list. `old_entry_id` (added for this) is what keeps a day's OTHER
    snack out of it.
    """
    if (target_entry_id is None) == (not target_date):
        # Covers both "neither" and "both" — a caller must say which night
        # it means exactly one way.
        raise ValueError("Pass exactly one of target_entry_id or target_date.")

    conn = get_conn()
    source = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.component_category,
               mpe.food_groups_json, mpe.cooked_status,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.id = ? AND mpe.household_id = ? AND mpe.weekly_plan_id = ?
        """,
        (entry_id, household_id(), weekly_plan_id),
    ).fetchone()
    # Household- and plan-scoped, same as the in-place swap and the stepper
    # going down: an id from another household or another week is a 404,
    # not a quiet edit of somebody else's dinner.
    if not source:
        conn.close()
        raise ValueError(f"No meal {entry_id} on that week's plan.")
    if source["component_category"]:
        conn.close()
        raise ValueError("That plan is built from components, not day slots.")
    if source["slot_state"] != "planned" or not source["meal"]:
        conn.close()
        raise ValueError("There's no dish on that slot to put anywhere.")

    if target_entry_id is not None:
        target_row = conn.execute(
            """
            SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.component_category,
                   mpe.cooked_status, COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.id = ? AND mpe.household_id = ? AND mpe.weekly_plan_id = ?
            """,
            (target_entry_id, household_id(), weekly_plan_id),
        ).fetchone()
        conn.close()
        if not target_row:
            raise ValueError(f"No slot {target_entry_id} on that week's plan.")
        if target_row["component_category"]:
            raise ValueError("That plan is built from components, not day slots.")
        if target_row["slot"] != source["slot"]:
            # A breakfast dish onto a dinner is a different decision, and one
            # nobody made on this screen: the stepper is inside a meal-type
            # group and every day it offers is a day of that same meal.
            raise ValueError("That day is a different meal from the one being added to.")
        target_id = target_row["id"]
        target_date_resolved = target_row["date"]
        target_slot = target_row["slot"]
        target_slot_state = target_row["slot_state"]
        target_cooked = target_row["cooked_status"]
        target_meal = target_row["meal"]
    else:
        # An empty night has no row to look up by id — that's the whole
        # point — so it's resolved fresh by (date, slot) instead: the same
        # slot source["slot"] already sits in, on the day the caller named.
        # A row that has appeared here since the screen drew "Nothing yet"
        # (a chat swap, another tab) is carried through and gets exactly
        # the same checks below a target_entry_id would.
        target_row = conn.execute(
            """
            SELECT mpe.id, mpe.slot_state, mpe.cooked_status,
                   COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ?
              AND mpe.date = ? AND mpe.slot = ?
            """,
            (household_id(), weekly_plan_id, target_date, source["slot"]),
        ).fetchone()
        conn.close()
        target_id = target_row["id"] if target_row else None
        target_date_resolved = target_date
        target_slot = source["slot"]
        target_slot_state = target_row["slot_state"] if target_row else None
        target_cooked = target_row["cooked_status"] if target_row else None
        target_meal = target_row["meal"] if target_row else None

    if target_id == source["id"]:
        # Readable, and so a SlotRefused: the strip never offers a day the
        # dish already covers, but a screen drawn before the week moved
        # under it can still send one, and "that dish is already on that
        # day" is the whole of the answer. Same for the name match further
        # down. (The narrowing that introduced SlotRefused swallowed both
        # of these into the generic line for a day; by its own rule —
        # sentences written for a person — they belong here.)
        raise SlotRefused("That dish is already on that day.")
    if target_id is not None and target_slot_state not in ("planned", "open", "planned_empty"):
        raise ValueError("That slot isn't one this can take over.")

    # A night that has already gone by, whichever way the caller named it.
    # Only the picker's own `day.isPast` filter kept those days off the
    # strip, and that is a screen's rule rather than the week's — a tab drawn
    # yesterday, a retried POST or a direct call can all still send one, and
    # the rule holds at the write, same as planned_empty and cooked just
    # below it. Refused before the chain read and long before the swap:
    # nothing is written.
    #
    # Whose clock this reads, why it is strictly BEFORE, and why a caller
    # must be holding no connection of its own when it asks are all in
    # night_has_gone, which is where they belong now that the swap doors
    # ask the same question (2026-09-17). The one thing that IS local: both
    # branches above close their connection before they reach this, so the
    # read inside it nests nothing.
    if night_has_gone(target_date_resolved):
        raise SlotRefused(NIGHT_GONE)

    from . import leftovers as _leftovers

    # The name the ROW READS AS, which for a confirmed reheat is the dish it
    # reheats and not its own text. See the docstring: deriving it from the
    # row alone is what wrote a dish nobody picked.
    chains = _leftovers.plan_leftover_chains(weekly_plan_id)
    chained = chains["leftovers"].get(source["id"])
    dish = chained["source"]["meal"] if chained else source["meal"]

    # The two refusals a PERSON reads, raised as their own type so the route
    # can tell them apart from "No slot 999 on that week's plan." Everything
    # else here is an impossible state reachable only from a stale screen,
    # and printing a row id (or a Python exception) into the household's
    # week is how an app that did the right thing reports itself broken.
    # A genuinely empty night (target_id is None) has neither state, so
    # neither line below ever fires for one — there is no row to refuse.
    # The DAY still can be: an empty night that has gone by is refused
    # above, on the date alone.
    if target_slot_state == "planned_empty":
        raise SlotRefused("Nobody’s eating that one — it isn’t a day to plan into.")
    if (target_cooked or "") == "done":
        # Somebody cooked it and ate it. The tick is a record, the inventory
        # was depleted against it, and the ingredients are on a list that
        # has already been shopped — taking the row away destroys all three
        # and buys nothing back.
        raise SlotRefused("That one’s already been cooked — it isn’t a day to plan into.")

    groups_from = source
    if chained:
        # ...and the plate belongs to the dish, not to the night that
        # reheated it — a reheat row records no food groups of its own.
        conn = get_conn()
        cook = conn.execute(
            "SELECT food_groups_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (chained["source"]["entry_id"], household_id()),
        ).fetchone()
        conn.close()
        groups_from = cook or source

    # Through the chain too, and for exactly the reason `dish` is: the
    # picker showed this night by the dish it READS AS, so a toast naming
    # its stored text reports a different meal from the one the household
    # just agreed to lose. The right night is displaced either way — this
    # is the same "labelled with one dish, reported as another" defect the
    # blocker above was, left half-fixed. None of this applies to a
    # genuinely empty night: target_id is None, chains["leftovers"].get(None)
    # is None, and target_slot_state (also None) never equals "planned", so
    # replaced stays None — nothing was displaced, exactly as for an open
    # slot.
    target_chained = chains["leftovers"].get(target_id)
    replaced = None
    if target_slot_state == "planned":
        replaced = target_chained["source"]["meal"] if target_chained else target_meal
    if replaced and replaced.strip().lower() == dish.strip().lower():
        raise SlotRefused("That day already has it.")

    # Every night that was eating off the dish being displaced. Read BEFORE
    # the swap, because afterwards there is nothing left to ask. Those
    # nights are not harmed — swap_meal_in_plan re-buys for them — but
    # nobody was being told, and the stepper going down refuses the mirror
    # of this outright. Empty for a genuinely empty night, same reason as
    # `replaced` above: nothing was there to have been feeding anyone.
    unchained = [
        {"date": t["date"], "slot": t["slot"]}
        for t in (chains["sources"].get(target_id, {}).get("targets") or [])
    ]

    swap_meal_in_plan(
        weekly_plan_id,
        target_date_resolved,
        dish,
        slot=target_slot,
        food_groups=json.loads(groups_from["food_groups_json"] or "[]") or None,
        # None for a genuinely empty night — there is no row to name, and
        # swap_meal_in_plan resolves an empty (old_entry_id, old_meal) pair
        # to "every row already in that slot", which for a slot with
        # nothing in it is the empty list _replace_slot_entries already
        # treats as a plain insert (see its own docstring). Nothing here
        # re-implements that; this is the same write add_dish_day always
        # made, aimed at a day that starts with nothing instead of
        # something.
        old_entry_id=target_id,
        # Blank, the same call swap_meal_in_plan's own docstring makes for a
        # swap asked for in chat: there is no "why this?" beyond the
        # household having chosen it, and writing one would be the plan
        # explaining their own decision back to them.
        reasoning="",
    )
    return {
        "status": "added",
        "date": target_date_resolved,
        "slot": target_slot,
        "dish": dish,
        # What the day was holding, so the screen can say what it cost.
        # None for an open or genuinely empty slot: nothing was displaced,
        # a question was answered or a blank was filled.
        "replaced": replaced,
        # The nights that were eating off what just went, now cooking for
        # themselves. Empty for the overwhelming majority of taps.
        "unchained": unchained,
        # get_week_menu's own day dict, exactly as the stepper going down
        # and the in-place swap both answer — one shape, one renderer, and
        # the week the screen is holding updates by splicing one day.
        "day": next(
            (d for d in (get_week_menu(weekly_plan_id).get("days") or [])
             if d.get("date") == target_date_resolved),
            None,
        ),
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
            # Snacks a DAY (Julia, 2026-09-08) — the answer onboarding
            # actually collects; the per-week number above is the distinct-
            # recipe count derived from it.
            "snacks_per_day": field("snacks_per_day", 2),
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


def suggest_planning_period(from_date: str = "", plan_ahead: bool = True) -> dict:
    """
    The period the app offers by default — where "this week" starts for
    THIS household, rather than where the calendar says a week starts.

    Read from the rhythm the household already gave at onboarding
    (household_rhythm.planning_anchor, "when should your weekly plan be
    ready?"). Emily's decision, 2026-09-05: the anchor is a WEEKDAY the
    plan and list are final by — her example, "ready by Friday" — not an
    abstract cadence, and 'as_we_go' is the one non-weekday escape with a
    concrete meaning of its own (short horizons, not "no answer"):

    - A weekday (rhythm.PLANNING_ANCHOR_WEEKDAYS) — "ready by Friday" means
      the week STARTS THE NEXT MORNING, so the period begins the day after
      the ready day and runs seven days. The nearest such start (today
      counts, if today already is that day) is used, matching how the
      original Monday default always meant the week currently running
      rather than some future one. A household that has never answered
      defaults to 'sunday' — ready the Sunday before, Monday start — which
      is the exact old default, so this is a no-op for everyone who
      predates the weekday picker.
    - 'as_we_go' — no weekly ready day at all. Three days from TODAY: a
      short horizon a household re-plans every couple of days, not a
      Monday-shaped week with a different start.

    This is a SUGGESTION and nothing more: it seeds the default on the plan
    screen, and every one of its parts is overridable by picking a start
    date and a length. The rule Pomona is actually defending is that the
    household is never forced into a week they didn't choose — a default
    that guesses better is not the same as a constraint that guesses less.

    **This is the ONE source of "which week" (Emily, 2026-09-11).** On
    Friday 2026-09-11 the Plan tab led with a draft for Aug 24–30 while Now
    asked "Shall I put Sep 7–13 together?" — two screens naming two weeks,
    because each derived its own. Now the nudge, the Plan tab's empty state
    and the plan-week default all read this, and a test holds the nudge and
    this to the same answer.

    And from PLAN_AHEAD_FROM_WEEKDAY on, the current period is skipped in
    favour of what comes next: on a Friday with no approved plan covering
    today, "this week" is next week (`is_current_period` False, so a
    screen can say "next week" rather than "this week"). Approved is the
    test, not merely live — a draft covering today is exactly the
    unplanned week this rule is about, and the household is offered the
    week after it while the draft stays where it is on Plan. With an
    APPROVED plan covering today, what comes next is the day after that
    plan's last day (2026-09-21, the "Friday edge" found while building
    the intake motion): until then the approved case stayed on the
    current period and the today-clamp below turned it into Fri–Thu — a
    window nobody asked for, straddling three days already approved and
    four of the week the nudge and the Plan tab's own "Plan next week ›"
    were both offering. Now all three name the same days. An 'as_we_go'
    household is never shifted: its period starts today by definition.

    And it never starts before today (2026-09-21). The anchor says where
    the household's week begins; once that day has passed, the suggestion
    begins on today instead and keeps its length — a "ready by Friday"
    household opening Plan on a Sunday is offered Sun–Sat, not the
    Sat–Fri whose first dinner is already eaten. `is_current_period`
    stays True: it is still this week, just the part of it that is left
    to plan ahead of. The length is the household's HORIZON, whatever the
    start (Emily, 2026-09-21): a period offered from any day runs seven
    days from it (three for as-we-go), never cut short at some other
    plan's edge — that is the intake's overlap warning's job, not this
    default's.

    `plan_ahead=False` asks for the current period regardless — for the
    one caller that is not choosing a default but resolving a choice the
    person already made: onboarding's "this week / next week" (main.py
    _first_plan_window), which builds a part-week from today for "this
    week" and has its own floor rule for how short is too short. Shifting
    underneath it would turn "this week" into next week and "next week"
    into the one after, on a Friday sign-up — Julia's bug, inverted.

    Unasked, "today" is the HOUSEHOLD's (2026-09-15). This names a week a
    screen shows, and from 8pm Eastern the server is already on tomorrow —
    which, on the last evening of a period, is the evening the Friday rule
    starts skipping it.

    It was FILED with no reproduced symptom, and the reason is worth
    keeping: the check that found nothing compared two households' nudge
    payloads, and the nudge and the Plan tab both read this function, so
    the two move together and stay self-consistent on either clock.
    Self-consistent is not the same as right — driven at Toronto 21:30 the
    wrong week really is offered, and every window that has to coincide
    with the household's day moves in the same commit (the 2026-09-14
    lesson).

    A caller that passes `from_date` reads no clock here at all, so this
    change reaches neither main._first_plan_window nor chores._chores_week:
    both resolve a day themselves and hand it in. What day they hand in is
    their own business, and today both hand in the SERVER's — their own
    cards, not this one's.
    """
    today = date.fromisoformat(from_date) if from_date else _household_today()
    anchor = (_rhythm_anchor() or "sunday")
    is_current = True
    if anchor == "as_we_go":
        start = today
        day_count = 3
    else:
        ready_index = _rhythm.PLANNING_ANCHOR_WEEKDAYS.index(anchor)
        start_index = (ready_index + 1) % 7
        start = today - timedelta(days=(today.weekday() - start_index) % 7)
        day_count = 7
        if plan_ahead and _attention_moves_on(today, start.isoformat(), day_count):
            conn = get_conn()
            approved = _live_plan_covering(conn, today.isoformat(), approved_only=True)
            conn.close()
            if approved is None:
                start = start + timedelta(days=day_count)
            else:
                # This week is planned and mostly eaten: what is left to
                # plan is what follows the approved plan — the same
                # `following` the nudge and next_period_after offer.
                cover_start, cover_days = plan_period(approved)
                start = date.fromisoformat(period_end_date(cover_start, cover_days)) + timedelta(days=1)
            is_current = False
        # Today, never yesterday (Emily, 2026-09-20, re-planning on a
        # Sunday: "the days are showing from yesterday"). The anchor names
        # where the household's week BEGINS, and mid-week that day has
        # gone: a "ready by Friday" household opening Plan on Sunday was
        # offered Sat 19–Fri 25 on Sep 20, and yesterday's dinner is
        # already eaten. A suggestion is what to plan NEXT, so it starts on
        # the first day still ahead — today — and keeps the household's
        # horizon (seven days, or the anchor's own count), which is what
        # the "Starting when?" screen shows as "Today · Sun 20 → Sat 26".
        # Only the suggestion: plan_ahead=False callers are resolving a
        # period the person already chose and do their own part-week
        # arithmetic from the anchor start (main._first_plan_window).
        if plan_ahead and start < today:
            start = today
    return {
        "start_date": start.isoformat(),
        "day_count": day_count,
        "planning_anchor": anchor,
        "label": _format_period_range(start.isoformat(), day_count),
        "is_monday_anchored": start.weekday() == 0,
        "is_current_period": is_current,
    }


def _attention_moves_on(today: date, start_date: str, day_count: int) -> bool:
    """
    Whether `today` is far enough into the period that the app should be
    talking about the NEXT one: from its PLAN_AHEAD_FROM_WEEKDAY-th day
    (Friday of a Monday week), or from the day before its last day,
    whichever comes first. The second clause is what keeps a short period
    honest — a three-day 'as_we_go' horizon never reaches a fifth day, and
    the nudge for what follows it still has to open before it ends (the
    old "from Saturday" rule, which this generalises rather than replaces).
    Before the period starts, never — a future period has no "days left".
    """
    start = date.fromisoformat(start_date)
    if today < start:
        return False
    end = date.fromisoformat(period_end_date(start_date, day_count))
    return (today - start).days >= PLAN_AHEAD_FROM_WEEKDAY or (end - today).days <= 1


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


# The tiebreak between two live plans on one day, since a draft may sit
# over an approved week until it is approved (2026-09-13): the approved
# one is the household's real week. Prepended to the newest-first order
# every day-resolver already used, so with no draft in play nothing
# changes.
_SQL_APPROVED_FIRST = "(status = 'approved') DESC"


def _live_plan_covering(conn, day: str, approved_only: bool = False):
    """
    The non-retired plan whose period contains `day`, or None. With
    approved_only, a draft covering the day does not count — the reading
    suggest_planning_period needs, where "this week is planned" means
    somebody said yes to it.
    """
    status_clause = "status = 'approved'" if approved_only else "status != 'retired'"
    return conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND {status_clause} "
        f"AND date({_SQL_PERIOD_START}) <= date(?) "
        f"AND date({_SQL_PERIOD_START}, '+' || {_SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY {_SQL_APPROVED_FIRST}, created_at DESC, id DESC LIMIT 1",
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
    2. Otherwise, from PLAN_AHEAD_FROM_WEEKDAY on (Friday of a Monday
       week — see _attention_moves_on), the period that follows the one
       covering today. The design asks for Sunday morning; it opened on
       Saturday from the start because this is in-app only, not real push
       (there is no scheduler and no push infrastructure — see schema.sql
       on notification_dismissals), so the nudge is only ever seen when the
       app is opened, and a household that doesn't open it on Sunday still
       needs the offer before the week begins. Friday since 2026-09-11, so
       that Now talks about next week from the same day whether this week
       was planned or not — one threshold, not two.

    Case 1 reads the week to offer from suggest_planning_period and nothing
    else (Emily, 2026-09-11: Plan and Now must name the same week). That is
    what carries the Friday rule here: with nothing approved for this week
    and only the weekend left, the suggestion is already next week, and the
    eyebrow says so (`is_current_week` False). If that next period has
    already been planned ahead, there is nothing to offer and this says so.

    Suppressed once dismissed, and the dismissal key is the week itself —
    so "I won't ask again this week" is literally true, and next week's
    offer isn't silenced by this week's dismissal.

    Emily's rule (2026-09-05): case 1 shows every morning until a plan
    actually covers today again — including the morning after a plan's
    last day has passed and nothing has replaced it. There used to be a
    second guard here ("but this week was already filed under a plan"),
    meant to stop a mid-week-onboarding household from being told Monday
    was left unplanned when it simply didn't exist yet. In practice that
    guard also silenced the nudge for the rest of ANY week whose plan ran
    out early — the exact case this rule now says must keep nudging — so
    it's gone. The only thing that still silences case 1 is a dismissal of
    THIS suggested period specifically (below): dismissed, it stays quiet
    until the suggestion changes; not dismissed, it asks again tomorrow.
    """
    # ONE clock for the whole function (2026-09-15). Three READS on TWO
    # clocks before it: retire_expired_drafts on the household's, a bare
    # date.today() here, and suggest_planning_period on the server's
    # underneath it — so for the four evening hours the two dates differ,
    # the sweep at the top and the offer beneath it were reasoning about
    # different days, and a draft this function had just decided was still
    # live was invisible to the question it asked next. Resolved once and
    # threaded down, which also keeps the nudge at the single
    # connection-and-SELECT it already cost.
    today = _household_today()
    # A draft whose last day has passed is nobody's week any more; retire
    # it before deciding what to offer, so this and the Plan tab (which
    # sweeps too, in get_week_menu) are reasoning about the same plans.
    retire_expired_drafts(today.isoformat())
    suggestion = suggest_planning_period(from_date=today.isoformat())

    conn = get_conn()
    dismissed = _notifications._dismissed_keys(conn)
    covering = _live_plan_covering(conn, today.isoformat())
    conn.close()

    target = None
    target_days = suggestion["day_count"]
    is_current = False
    if covering is None:
        # Nothing covers today, full stop — offer the suggested period,
        # which is the current one until PLAN_AHEAD_FROM_WEEKDAY and the
        # next one from there. See the docstring above for why there's no
        # additional "already filed this week" guard for the current
        # period; a shifted-to suggestion IS checked, because "plan next
        # week" to a household that already has is an offer to undo it.
        target, is_current = date.fromisoformat(suggestion["start_date"]), suggestion["is_current_period"]
        if not is_current and _plan_covers_any(target.isoformat(), target_days) is not None:
            target = None
    elif covering is not None:
        # A period ends on some day E; the offer for what follows opens
        # from the period's PLAN_AHEAD_FROM_WEEKDAY-th day or from the day
        # before E, whichever is first — Friday for a Monday-to-Sunday
        # week, and right for a period of any length or start (see
        # _attention_moves_on).
        cover_start, cover_days = plan_period(covering)
        cover_end = date.fromisoformat(period_end_date(cover_start, cover_days))
        if _attention_moves_on(today, cover_start, cover_days):
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


def next_period_after(plan: dict, today: str = "") -> dict:
    """
    The period the Plan tab's "Plan next week ›" offers underneath a plan
    that is on screen: the stretch that FOLLOWS it, sized by the
    household's own rhythm rather than by the plan it happens to follow.

    Emily, Sunday 2026-09-13: her plan on screen was a two-day one (a
    Saturday sign-up's "this week" is Sat–Sun, see main._first_plan_window
    — a custom range or a takeover remnant does the same), and the link
    under it was built on the client as "start + day_count, for day_count
    days". So the week after a two-day plan was offered as two more days,
    Sep 14–15, while Now's nudge — which reads the rhythm — asked about
    Sep 14–20. Two screens naming two spans, the class of bug the
    2026-09-11 "one source of which week" rule exists to stop; this is
    that rule reaching the one link that was still deriving its own.

    The rule:
    - It starts the day after the plan's last day, and runs the rhythm's
      length (seven, or three for a household planning as it goes — the
      same day_count suggest_planning_period gives). That is exactly what
      get_week_planning_nudge offers from Friday, so Plan and Now agree.
    - A plan whose period has already ended (an approved week shown as the
      fallback when nothing covers today) is not something to plan "after"
      — the day after IT may be weeks ago. Then the offer is simply the
      household's standing suggestion, this week or next by the Friday
      rule, and `is_current_period` says which so the link can say so.
    - It is the WHOLE horizon, whatever else is already planned (Emily,
      Monday 2026-09-21, from her phone). Until then a stretch with another
      live plan part-way into it was cut short to stop the day before that
      plan — which is how "Plan next week ›" under an ended week offered
      her "Mon 21 → Mon 21 · 1 day" (a draft began on the Tuesday), and
      how the intake it opened counted "Today" and "Tomorrow" by one day.
      Her rule: from any start, the range is the household's horizon; the
      one exception to "never one day" is a household whose horizon is
      one day. A stretch whose FIRST day is already held is flagged with
      `is_planned` True — that is a re-plan, and the link says "Re-plan".
      Either way the question screen's own warning ("… already has a
      draft. Answering again replaces it") says what generating over held
      days costs, before anything is answered — that warning is where the
      overlap belongs, not in a shorter offer.

    Never shortened for a trip either: a night away is a planned_empty slot
    inside the week, not a reason to plan a shorter one (see
    _finish_week_slots).

    `today` is the household's day, passed in by get_week_menu, which has
    already resolved it — the clock is one connection-and-SELECT and this
    is the only caller. Left out, it resolves its own, on the household's
    clock rather than the server's (2026-09-15): "has this plan already
    ended" decides whether the link offers the day after it or falls back
    to the standing suggestion, and on the evening of a plan's last day
    the server says ended and the household says not yet.
    """
    # `plan` is get_weekly_plan's dict, whose period is already resolved
    # (period_start_date / day_count) — not a weekly_plans row, which
    # would need plan_period() to read its sentinels.
    start_str, days = plan["period_start_date"], int(plan["day_count"] or 0)
    plan_id = plan["weekly_plan_id"]
    on = date.fromisoformat(today) if today else _household_today()
    suggestion = suggest_planning_period(from_date=on.isoformat())
    following = date.fromisoformat(period_end_date(start_str, days)) + timedelta(days=1)
    if days < 1 or following <= on:
        start = date.fromisoformat(suggestion["start_date"])
        is_current = suggestion["is_current_period"]
    else:
        start = following
        is_current = False
    day_count = int(suggestion["day_count"]) or 7

    held = find_overlapping_plans(start.isoformat(), day_count, exclude_plan_id=plan_id)
    is_planned = any(p["overlap_dates"][0] == start.isoformat() for p in held)
    return {
        "start_date": start.isoformat(),
        "day_count": day_count,
        "label": _format_period_range(start.isoformat(), day_count),
        "is_current_period": is_current,
        "is_planned": is_planned,
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
    # The same one-transaction write a swap uses (_replace_slot_entries):
    # tell any source this row was reheating from, reverse whatever it
    # contributed to the list, delete it, and plan the choice in its place
    # — all or nothing. An open slot links to nothing and has bought
    # nothing, but the "change my mind about an already planned slot" path
    # comes through here too. The grocery list mirrors the plan's approved
    # state exactly as a swap does: settling a slot in a draft leaves the
    # list alone, settling one in an approved week keeps it in step.
    result = _replace_slot_entries(
        weekly_plan_id, [row["id"]], meal_date, slot, choice.strip(),
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
        "UPDATE weekly_plans SET status = 'draft', approved_by = '', approved_by_member_id = NULL, "
        "approved_at = NULL, "
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


def discard_draft_plan(weekly_plan_id: int) -> dict:
    """
    Drop a draft the household has decided against — Loop Board 2026-09-13,
    "a draft I walked away from never lingers on Plan and never becomes the
    week by accident".

    Exactly what retire_expired_drafts does to a draft whose days have
    passed, said out loud instead of waited out: status 'retired' with
    `retired_reason` 'discarded', and nothing else touched. The meals, the
    period and the intake stay on record — this is "don't lead with it",
    not deletion — and there is nothing to reverse on the shopping list,
    because since 2026-09-13 a draft contributes to it only at approval.
    An approved plan underneath is therefore whole already, and is never
    WRITTEN here — it is read, but only to name it (see
    `approved_week_label` below).

    An APPROVED plan is refused rather than retired: dropping a week that
    has been shopped for would take the list's own reason away with it,
    and the household's two real answers are reopening it or re-planning.
    That refusal is a SlotRefused, because the sentence is written for the
    household to read and the other adult approving while this screen sat
    open is exactly how it gets reached — "no such plan" stays an ordinary
    ValueError and takes the screen's plain line, since an id on screen
    reports an app that did the right thing as broken. Retiring an
    already-retired plan is a no-op — the household tapping twice, or a
    stale screen, must not be an error.

    `approved_week_label` names the approved week this draft was sitting
    over, when there is one, so the screen that dropped it can say which
    week is theirs again instead of just that something went.
    """
    # The household's day, resolved BEFORE the connection below is opened
    # rather than where it is used, forty lines down. This function writes
    # and _household_today opens its own connection; this repo has twice
    # earned a "database is locked" from a nested get_conn inside a write
    # transaction, and the cost of reading it early is nothing.
    today = _household_today().isoformat()
    conn = get_conn()
    plan = conn.execute(
        "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    if plan["status"] == "approved":
        conn.close()
        raise SlotRefused("That week's approved — reopen it or re-plan it instead.")

    start, days = plan_period(plan)
    label = _format_period_range(start, days)
    # The approved week this draft is sitting over, by the same test
    # _pending_draft_over uses in the other direction (it asks "is there a
    # draft over this approved week?"; this asks "which approved week is
    # under this draft?").
    #
    # A draft can straddle TWO approved weeks — "Pick my own days" will
    # happily draft Sep 19–23 across a Sep 14–20 and a Sep 21–27 — so which
    # one gets named matters. The one whose period contains TODAY is the
    # week that is actually theirs again once the draft goes; without that
    # rule the toast told a household living in Sep 14–20 that "Sep 21–27
    # is still your week", which is true of a week they are not in yet.
    # Failing that (a draft entirely in the future), the earliest by period
    # start — the next week they will reach. On the HOUSEHOLD's clock
    # (2026-09-15, resolved at the top): the toast names a week out loud,
    # and an evening where the server is already on tomorrow is exactly
    # where "which week contains today" flips from one of a straddling
    # pair to the other.
    overlapping = []
    for other in conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? AND status = 'approved' AND id != ?",
        (household_id(), weekly_plan_id),
    ).fetchall():
        other_start, other_days = plan_period(other)
        if periods_overlap(start, days, other_start, other_days):
            overlapping.append((other_start, other_days))
    approved_label = None
    if overlapping:
        covering = [
            p for p in overlapping
            if p[0] <= today <= period_end_date(p[0], p[1])
        ]
        pick = min(covering or overlapping, key=lambda p: p[0])
        approved_label = _format_period_range(pick[0], pick[1])

    if plan["status"] == "retired":
        conn.close()
        return {
            "weekly_plan_id": weekly_plan_id, "status": "retired",
            "week_label": label, "approved_week_label": approved_label,
            "was_already_retired": True,
        }

    conn.execute(
        "UPDATE weekly_plans SET status = 'retired', retired_reason = 'discarded', "
        "updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
    logger.info("Discarded draft plan %s (%s)", weekly_plan_id, label)
    return {
        "weekly_plan_id": weekly_plan_id, "status": "retired",
        "week_label": label, "approved_week_label": approved_label,
        "was_already_retired": False,
    }


def record_plan_requests(weekly_plan_id: int, report: dict | None) -> None:
    """
    Keep what the model reported doing with the typed requests when it
    drafted this plan — `honoured_requests` and `unmet_requests` from
    submit_weekly_plan, trimmed to their two fields each. Nothing is
    written for an empty report (a stubbed model, an older prompt), so
    the opener falls back to the slots' own derived_from.
    """
    report = report or {}
    # `ingredient` rides along when the line is about a typed ingredient
    # (typed_requests.use_requested_ingredients): the opener says "the
    # corn", not the whole sentence they typed.
    def _trim(r: dict, field: str) -> dict:
        out = {"words": str(r.get("words") or "").strip(), field: str(r.get(field) or "").strip()}
        if r.get("ingredient"):
            out["ingredient"] = str(r["ingredient"]).strip()
        return out

    honoured = [
        _trim(r, "label") for r in (report.get("honoured_requests") or []) if isinstance(r, dict) and r.get("words")
    ]
    unmet = [
        _trim(r, "reason") for r in (report.get("unmet_requests") or []) if isinstance(r, dict) and r.get("words")
    ]
    if not honoured and not unmet:
        return
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET requests_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps({"honoured": honoured, "unmet": unmet}), weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()


def plan_requests(weekly_plan_id: int) -> dict:
    """The stored report, or {"honoured": [], "unmet": []} when there is none."""
    conn = get_conn()
    row = conn.execute(
        "SELECT requests_json FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    conn.close()
    try:
        data = json.loads(row["requests_json"]) if row and row["requests_json"] else {}
    except (TypeError, ValueError):
        data = {}
    return {"honoured": data.get("honoured") or [], "unmet": data.get("unmet") or []}


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
#
# WEEK_SLOTS and deliberately NOT DAY_SLOTS: "<date>:snack" is not a key.
# A day holds two snacks by default (preferences.resolve_snacks_per_day),
# so that string names two rows, and every resolver here picks one of them
# out of a dict built from an unordered SELECT. Widening this to include
# snack was tried on 2026-09-15 and made things WORSE than leaving a snack
# chain unreadable: with one legacy snack chain on the day, an ordinary
# "batch cook these" on the OTHER snack resolved to the wrong dish, counted
# its days twice and put ten plates of chickpeas over a shopping list for
# six, with the apples headlined "Made ahead — Tuesday's Roasted
# Chickpeas". That is the same failure this ticket exists to fix, arriving
# from the other side and worse. So a snack chain names the ROW it means
# (cook_ahead._source_ref), and this parser goes on saying plainly that a
# date and a slot do not name a snack.
_LINKS_TO_DATE_SLOT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}):(" + "|".join(WEEK_SLOTS) + r")$"
)
_LINKS_TO_ENTRY_ID_RE = re.compile(r"^entry_id:(\d+)$")

# Offered when a leftovers night gets reopened — genuinely generic, since
# by the time this runs there's no real recipe recommendation behind any
# of them, unlike the model's own open slots. "Takeout, don't plan it"
# matches the exact phrase the generation schema already promises the
# household for this kind of honest last resort. "Something quick" says
# the rush number, which is a dinner's (Emily, 2026-09-23: 30 minutes).
_LEFTOVER_REPAIR_OPTIONS = [
    {"label": "Something quick", "meta": f"{_time_caps.RUSH_MAX_MINUTES} min or less"},
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
    - the two are at most leftovers.MAX_LEFTOVER_DAYS (3) days apart
      (Emily, 2026-09-23: leftovers are eaten within 3 days of the cook —
      the food-safety default). See below for what happens to one that
      isn't: it is NOT reopened.

    On failure, the week is never reordered — reordering a night the
    household already saw a reasoning for is its own kind of surprise.
    Instead the slot is reopened: cleared and handed back as a real
    question, the same shape as any other open slot, with the failure kept
    on derived_from.repaired/original_links_to rather than silently
    dropped, so it stays traceable.

    A chain that is fine in every way except that it is more than three
    days apart is turned into a FREEZER PORTION rather than reopened
    (_freeze_instead): the cook makes that night's portion extra and
    freezes it (leftovers.FREEZER_EXTRA_KEY, which every batch reader
    already counts, so the shopping still buys it once, on the cook), and
    the night reads "Leftovers from the freezer — Monday’s Chili". Chosen
    over reopening because nothing about that plan is wrong except how
    long the food sits in the fridge, and a reopened slot hands the
    household a question Pomona can answer itself (Emily, 2026-09-23: it
    does the planning work and says what it did). Reopening stays the
    answer for the broken chains above, where there is genuinely nothing
    to reheat.

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
    frozen = []
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
        elif source["slot"] not in ("lunch", "dinner") and not derived.get("cook_ahead"):
            # A household's own cook-ahead (cook_ahead.set_cook_ahead) is
            # the one chain that legitimately runs breakfast -> breakfast:
            # the person said "make Monday's batch cover Tuesday", so the
            # source being a breakfast is the point, not a mix-up.
            issue = "a breakfast"
        else:
            source_derived = json.loads(source["derived_from_json"] or "{}")
            if (source_derived.get("links_to") or "").strip():
                issue = "another leftovers night, not an actual cook"

        if issue is None and (
            date.fromisoformat(r["date"]) - date.fromisoformat(source["date"])
        ).days > _leftovers_mod().MAX_LEFTOVER_DAYS:
            _freeze_instead(r, source, derived, links_to)
            frozen.append({"date": r["date"], "slot": r["slot"], "source_entry_id": source["id"]})
            continue

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
            # Scoped by household as well as by id. The id here can only
            # have come from the household-filtered read at the top of
            # this function, so nothing reaches this statement with a
            # foreign id today — the guard is being made a property of the
            # statement rather than of whoever calls it. The rule the
            # package is built on is that scoping is not something a
            # caller does (see _shared.household_id), and a statement
            # quietly relying on a caller having already done it is how
            # that stops being true.
            #
            # tests/test_leftover_chain_household_filter.py sweeps the
            # whole module for the same shape, so a new one cannot appear
            # without a test going red — read that file's docstring for
            # what the sweep can and cannot see before relying on it.
            existing = conn.execute(
                "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                (source["id"], household_id()),
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
                "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                (json.dumps(source_derived), source["id"], household_id()),
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
    if frozen:
        logger.info(
            "Week plan %s had %d leftovers night(s) more than %d days after the cook; "
            "each is a freezer portion now: %s",
            weekly_plan_id, len(frozen), _leftovers_mod().MAX_LEFTOVER_DAYS,
            ", ".join(f"{x['date']} {x['slot']}" for x in frozen),
        )
    return {"repaired": repaired, "confirmed": confirmed, "frozen": frozen}


def _leftovers_mod():
    from . import leftovers as _leftovers  # lazy: leftovers reaches back here
    return _leftovers


def freeze_a_portion(conn, cook_id: int, night_date: str, night_slot: str) -> None:
    """
    The cook at `cook_id` makes one more night's portion, for the freezer:
    FREEZER_EXTRA_KEY's servings grow by that night's headcount, and the
    night is listed under `for` so the portion can be traced to the night
    that eats it. On `conn`; the caller commits. Shared by the chain
    repair (_freeze_instead) and the fold (meal_variety), the two places
    that turn a too-far leftovers night into a frozen one.
    """
    _leftovers = _leftovers_mod()
    row = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (cook_id, household_id()),
    ).fetchone()
    if row is None:
        return
    derived = json.loads(row["derived_from_json"] or "{}")
    extra = dict(derived.get(_leftovers.FREEZER_EXTRA_KEY) or {})
    extra["servings"] = _leftovers.freezer_servings(derived) + max(
        0, _leftovers.eaters_at(night_date, night_slot, conn=conn)
    )
    extra["for"] = sorted({*(extra.get("for") or []), f"{night_date}:{night_slot}"})
    derived[_leftovers.FREEZER_EXTRA_KEY] = extra
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(derived), cook_id, household_id()),
    )


def _freeze_instead(row, source, derived: dict, links_to: str) -> None:
    """
    repair_leftover_chains' answer for a leftovers night more than three
    days after its cook: the night becomes "Leftovers from the freezer —
    Monday’s Chili" (a freeform row, so it buys nothing and reads as a
    reheat), and the cook makes that portion extra for the freezer. In
    place, in one transaction — this runs on a freshly generated draft,
    where there is no shopping line or prep row to move.
    """
    _leftovers = _leftovers_mod()
    meal = (source["freeform_meal"] or "").strip()
    if source["recipe_id"]:
        conn = get_conn()
        name_row = conn.execute("SELECT name FROM recipes WHERE id = ?", (source["recipe_id"],)).fetchone()
        conn.close()
        meal = name_row["name"] if name_row else meal
    night = {k: v for k, v in derived.items() if k != "links_to"}
    night[_leftovers.FROM_FREEZER_KEY] = {"cook": f"entry_id:{source['id']}", "dish": meal}
    night["repaired"] = "leftovers_too_far"
    night["original_links_to"] = links_to
    conn = get_conn()
    try:
        freeze_a_portion(conn, source["id"], row["date"], row["slot"])
        conn.execute(
            "UPDATE meal_plan_entries SET recipe_id = NULL, freeform_meal = ?, derived_from_json = ? "
            "WHERE id = ? AND household_id = ?",
            (_leftovers.freezer_night_name(meal, source["date"]), json.dumps(night), row["id"], household_id()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _make_double_note_text(targets: list[str]) -> str:
    """
    The same "I'll set aside a double batch..." sentence
    repair_leftover_chains writes the first time a source is confirmed —
    rebuilt here for a source that's losing a target (a swapped or cleared
    reheat night) rather than gaining one. Reuses _join_with_and, the one
    piece of that construction worth not copying a second time; the rest
    is intentionally identical wording so a source note never reads
    differently depending on which direction last touched it.
    """
    day_names = [
        date.fromisoformat(t.split(":")[0]).strftime("%A")
        for t in sorted(targets, key=lambda t: t.split(":")[0])
    ]
    return (
        f"I’ll set aside a double batch tonight — {_join_with_and(day_names)} "
        f"{'eats' if len(day_names) == 1 else 'eat'} the leftovers."
    )


def _unlink_leftover_target(weekly_plan_id: int, entry_id: int, conn=None) -> int | None:
    """
    Tell a source entry that one of the nights it fed is about to be
    removed or replaced — the other half of the fix repair_leftover_chains'
    docstring already anticipates for the SOURCE side (see
    swap_meal_in_plan's own docstring), but nothing wrote for this,
    reverse direction. clear_plan_slot, resolve_open_slot and
    swap_meal_in_plan all just deleted the target row outright, leaving
    the source's make_double_for/make_double_note naming a night that no
    longer exists — so the cook night kept a batch (and a grocery line)
    sized for a reheat that isn't coming (Loop Board, "swapping a leftover
    TARGET night leaves the source's make_double_for stale").

    Must be called BEFORE `entry_id` itself is deleted — it reads that
    row's own date/slot/derived_from.links_to to find its source. A no-op
    for the overwhelming majority of removals: an entry with no links_to,
    one whose links_to doesn't resolve to a real row in THIS plan, or one
    the source never actually confirmed via make_double_for (nothing to
    undo in any of those cases).

    Only rewrites the source's derived_from_json here. Rescaling its
    grocery contribution to the smaller batch is a separate, pricier step
    a caller opts into explicitly — see _rescale_leftover_source_grocery —
    because it only matters at all once the plan is approved.

    `conn` is for one caller (drop_dish_from_day), same arrangement
    _reverse_meal_grocery_contributions has: given a connection this reads
    and writes on it and neither commits nor closes, so the unlink can be
    part of the caller's one transaction rather than a commit of its own.
    Given one it also SKIPS the rescale and RETURNS the source entry id
    instead, leaving WHEN to run it to the caller. That split dates from
    when the rescale could not join a transaction at all — it re-ingests
    through add_grocery_item and the recipe ingest tree, which used to
    open connections of their own. The tree takes a `conn` now (the
    swap-atomic work), so a caller can run _rescale_leftover_source_grocery
    inside its own transaction on the same connection — which is what
    _replace_slot_entries does — or after its commit, which is what
    drop_dish_from_day still does. Returns None when there was nothing to
    unlink, and None on the ordinary self-owned path (where the rescale
    has already been done here, exactly as before).
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        entry = conn.execute(
            "SELECT date, slot, derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
        if not entry:
            return None
        links_to = (json.loads(entry["derived_from_json"] or "{}").get("links_to") or "").strip()
        if not links_to:
            return None
        rows = conn.execute(
            "SELECT id, date, slot, slot_state, recipe_id, freeform_meal, derived_from_json "
            "FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL",
            (weekly_plan_id, household_id()),
        ).fetchall()

        source = _resolve_leftover_source(links_to, {(r["date"], r["slot"]): r for r in rows}, {r["id"]: r for r in rows})
        if source is None or source["id"] == entry_id:
            return None
        source_derived = json.loads(source["derived_from_json"] or "{}")
        targets = source_derived.get("make_double_for") or []
        if isinstance(targets, str):  # tolerate the pre-fix scalar shape
            targets = [targets]
        target = f"{entry['date']}:{entry['slot']}"
        if target not in targets:
            return None  # the source never actually confirmed this pairing — nothing to undo
        targets = [t for t in targets if t != target]
        if targets:
            source_derived["make_double_for"] = targets
            source_derived["make_double_note"] = _make_double_note_text(targets)
        else:
            # No target left at all — plan_leftover_chains stops treating this
            # entry as a source the moment make_double_for is gone, which is
            # exactly right: it's an ordinary cook again.
            source_derived.pop("make_double_for", None)
            source_derived.pop("make_double_note", None)

        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
            (json.dumps(source_derived), source["id"], household_id()),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()

    if not own_conn:
        return source["id"]
    if _weekly_plan_is_approved(weekly_plan_id):
        _rescale_leftover_source_grocery(source["id"], entry_id)
    return None


def _rescale_leftover_source_grocery(source_entry_id: int, unlinked_entry_id: int, conn=None) -> None:
    """
    Redo an already-approved leftover SOURCE's grocery contribution — and
    every OTHER already-approved entry in the plan that cooks the SAME
    recipe — after _unlink_leftover_target shrinks (or clears) the batch
    the source was scaled to.

    Scoped to the whole recipe-week rather than just the source, for the
    same reason approve_weekly_plan groups by recipe instead of by meal:
    if some unrelated entry elsewhere in the plan happens to cook the
    source's own recipe (a Thursday dinner of the dish a Tuesday source
    also made, say), the two were bought TOGETHER as one recipe-week at
    approval — one WeekGroceryBuffer, one rounding, on their combined raw
    total (see WeekGroceryBuffer and _week_bought_amount). Reversing and
    recomputing the source ALONE would round its new share a second time,
    on its own, drifting from what a full-plan recompute would say the
    same way ingesting meal-by-meal used to multiply spinach. So this
    reverses and re-ingests every entry sharing the source's recipe_id as
    one group through one shared buffer — the same reverse-then-reingest
    shape _reingest_unlinked_entries uses for the opposite gap (entries
    that have never bought anything); both now share
    _ingest_recipe_group_and_sides rather than duplicating the
    group/round/side logic.

    `unlinked_entry_id` is the target _unlink_leftover_target just
    unconfirmed — the entry the caller (clear_plan_slot or
    swap_meal_in_plan) is about to delete or replace, and will reverse
    itself right after this call returns. It is deliberately excluded
    from the recipe group here even though it still physically exists in
    the table at this instant: having just lost its confirmed pairing, it
    would otherwise look like an ordinary same-recipe cook and get folded
    into this rounding, only for the caller's own reversal a moment later
    to subtract a share back out of a line that was never rounded
    without it — the same drift, one step removed.

    A no-op for a freeform source: nothing structured to rescale, and a
    freeform meal never reaches the grocery list to begin with (see
    plan_meal).

    `conn` is for one caller (swap_meal_in_plan, through
    _replace_slot_entries) and is not part of the assistant-facing API.
    Given a connection every read, reversal and re-ingest here runs on it
    and nothing commits or closes — the whole grocery ingest tree takes a
    connection now, which is what lets this join the swap's one
    transaction where drop_dish_from_day (written before it did) still
    runs it after its commit. Left unset it behaves exactly as before.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    source = conn.execute(
        "SELECT recipe_id, weekly_plan_id FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (source_entry_id, household_id()),
    ).fetchone()
    if not source or not source["recipe_id"]:
        if own_conn:
            conn.close()
        return
    entries = conn.execute(
        "SELECT mpe.id, mpe.recipe_id, r.ingredients_json, r.default_servings, mpe.sides_json "
        "FROM meal_plan_entries mpe JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.recipe_id = ? "
        "AND mpe.component_category IS NULL AND mpe.id != ? "
        # Source last. It carries the group's biggest single ledger share
        # (scaled up for the whole batch), so reversing every other entry
        # first keeps the running grocery-line remainder above
        # _subtract_quantity's whole-unit rollup threshold (1 lb, under
        # which _humanize_grocery_quantity switches the display to oz) for
        # as long as possible. Reversing the source first can instead
        # leave a sub-threshold remainder in a unit that doesn't match the
        # next entry's own ledger record, which _subtract_quantity can't
        # reconcile and silently leaves alone — stranding a phantom amount
        # on the list this rescale was supposed to clear.
        "ORDER BY (mpe.id = ?) ASC, mpe.date ASC, mpe.id ASC",
        (
            source["weekly_plan_id"], household_id(), source["recipe_id"], unlinked_entry_id,
            source_entry_id,
        ),
    ).fetchall()
    if own_conn:
        conn.close()
    if not entries:
        return

    shared = None if own_conn else conn
    for entry in entries:
        _grocery._reverse_meal_grocery_contributions(entry["id"], conn=shared)
    buffer = _recipes.WeekGroceryBuffer(source["weekly_plan_id"], conn=shared)
    _ingest_recipe_group_and_sides(entries, source["weekly_plan_id"], buffer)
    buffer.flush()


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
                "DELETE FROM meal_plan_entries WHERE id IN (%s) AND household_id = ?"
                % ",".join("?" * len(extras)),
                (*[r["id"] for r in extras], household_id()),
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


def _plan_takeover(
    new_plan_id: int | None, period_start: str, day_count: int, drafts_only: bool = False, conn=None,
) -> list[dict]:
    """
    Decide what every other live plan keeps and gives up — and decide ALL of
    it before anything is written.

    drafts_only is the generation-time reading (Emily, 2026-09-13: "make
    the draft wait until approval"): a DRAFT being generated may replace
    other drafts on its days at once — nothing of theirs has reached the
    shopping list — but an APPROVED plan is left exactly as it is, neither
    shortened nor even counted as a claimant, until the draft is approved.
    Approval then runs the full walk (see _settle_weekly_plan_approval),
    which is where the approved week actually gives its days up.

    Two reasons it is separated from the writing.

    It is the only way to deconflict globally. Each plan's decision depends
    on what the plans newer than it kept, so the claims have to accumulate
    across the whole walk; handling one plan at a time against the new
    period alone is what let two of them be shortened onto the same resume
    date and invent a clash. Here, `claimed` starts as the new period's days
    and grows as each plan keeps its run, so a day can be awarded once.

    And it makes the destructive half short — short enough that
    retire_overlapping_plans can now run the whole of it inside ONE
    transaction on ONE connection, which is what finally closed the gap
    this docstring used to flag: a failure between two plans left the first
    one's days genuinely gone while the caller reported failure. Deciding
    first is still what makes that possible, because nothing is destroyed
    on the strength of a calculation that then throws — and because reading
    every live plan happens HERE, before the write transaction is opened,
    rather than from a second connection that would block against it.

    Returns one dict per affected plan, newest first, or [] when nothing
    overlaps — the ordinary case.

    new_plan_id may be None: preview_approved_takeover asks the same
    question BEFORE a plan exists, and the answer is the same one, because
    the new plan would be the newest row and is the one this skips anyway.
    """
    # On the caller's connection when given (the approval transaction —
    # see _settle_weekly_plan_approval, which must open exactly one), on
    # one of its own otherwise.
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        "ORDER BY created_at DESC, id DESC",
        (household_id(),),
    ).fetchall()
    if own_conn:
        conn.close()

    new_days = set(_week_intake.period_dates(period_start, day_count))
    claimed = set(new_days)
    decisions = []
    for row in rows:
        if row["id"] == new_plan_id:
            continue
        if drafts_only and row["status"] == "approved":
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


def retire_overlapping_plans(
    new_plan_id: int, period_start: str, day_count: int, drafts_only: bool = False, conn=None,
) -> dict:
    """
    Make "no day has two plans" true rather than merely intended: every
    other plan holding a day inside this period gives that day up, and the
    groceries it put on the list for those days come back off.

    Since 2026-09-13 the rule reads "no day has two APPROVED plans", and
    this runs at two moments with two scopes (Emily: "make the draft wait
    until approval"):

    - Generation, `drafts_only=True`: a new draft replaces other DRAFTS on
      its days immediately, and leaves an approved plan untouched — the
      household can draft a different Thursday-to-Sunday, look at it, and
      walk away with the approved week exactly as it was.
    - Approval, `conn=` the approval's own transaction: the full walk. This
      is when an approved plan's overlapping days go, meals and groceries,
      in the same commit that puts the draft's own groceries on the list —
      so the list never holds both weeks' food for one night, and a
      failure anywhere leaves both plans as they were. Given a connection
      this applies on it and neither commits nor closes; the caller owns
      the transaction.

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
    decisions = _plan_takeover(new_plan_id, period_start, day_count, drafts_only=drafts_only, conn=conn)
    if not decisions:
        return result
    if conn is not None:
        result = _apply_takeover(conn, result, decisions, new_plan_id, period_start, day_count)
        return _finish_takeover_result(result, new_plan_id)

    # ONE connection, ONE commit, for the whole destruction loop. Every
    # decision was already settled above; what was left was that acting on
    # them was not atomic — each plan's meals, prep tasks and grocery
    # reversal committed before the next plan was touched, so a failure
    # partway through (a locked database, a killed process) left the first
    # plan's days genuinely gone while the caller raised and every screen
    # said nothing had been saved. sqlite3 connects with the legacy
    # isolation_level of "", so the first write below opens a transaction
    # implicitly and there is no BEGIN to issue; reads on this connection
    # see the uncommitted writes, which is what the loop has always relied
    # on (plan two must not find plan one's already-deleted entries).
    # Nothing called from inside here opens a second connection — that
    # would block on this one's write lock and time out — which is why
    # _release_plan_days and _reverse_meal_grocery_contributions take the
    # connection rather than making their own.
    conn = get_conn()
    try:
        result = _apply_takeover(conn, result, decisions, new_plan_id, period_start, day_count)
        conn.commit()
    except Exception:
        # All or nothing. A half-applied takeover is the worst outcome
        # available: days destroyed under a plan the household is cooking
        # from, and an error message saying it did not happen.
        conn.rollback()
        raise
    finally:
        conn.close()
    return _finish_takeover_result(result, new_plan_id)


def _finish_takeover_result(result: dict, new_plan_id: int) -> dict:
    result["surrendered_dates"] = sorted(set(result["surrendered_dates"]))
    result["orphaned_dates"] = sorted(set(result["orphaned_dates"]))
    if result["orphaned_dates"]:
        logger.warning(
            "Plan %s left %d day(s) of an existing plan unplanned and unreplaced "
            "(a plan cannot keep a window with a hole in it, so it kept the longer side): %s",
            new_plan_id, len(result["orphaned_dates"]), ", ".join(result["orphaned_dates"]),
        )
    return result


def preview_approved_takeover(period_start: str, day_count: int) -> dict | None:
    """
    What APPROVING a draft of this period would take away from an APPROVED
    plan — said before anything is generated, so the household can be
    asked, and again on the draft itself (get_week_menu's approval block)
    so the Approve button says what it costs. Read-only. Returns None when
    no approved plan would lose a day, which is the ordinary case. Since
    2026-09-13 generating the draft itself takes nothing away; approval is
    the takeover (see retire_overlapping_plans).

    This exists because the take-over is Emily's rule and stays one
    (2026-09-04: no day has two plans), but asking first is also her rule
    (2026-09-11): a running week the household has approved is being
    cooked from and shopped for, and "plan the rest of my week" from chat
    used to dismantle it in one tap with nothing said first. A DRAFT is
    not covered here on purpose — nothing of a draft's has reached the
    shopping list, and replacing one is what re-planning means.

    The decision is the real one, not an approximation of it: this runs
    _plan_takeover exactly as retire_overlapping_plans will, with no new
    plan to skip, so the days named here are the days that would actually
    go — including the ORPHANED ones (a period strictly inside an approved
    week costs it the shorter side too, and that is the case most worth
    saying out loud).

    Returns {"approved_plan_ids", "days", "orphaned_dates", "note"}:
    `days` is one entry per lost day, in order, each with its `weekday`
    and the `meals` it holds (slot + name, eaten order, deliberately-empty
    slots left out); `note` is a sentence in the app's own voice that
    states the thing and its way out, ready to be said as-is.
    """
    if day_count < 1:
        return None
    decisions = [
        d for d in _plan_takeover(None, period_start, day_count)
        if d["status"] == "approved"
    ]
    if not decisions:
        return None

    lost_dates = sorted({day for d in decisions for day in d["surrendered"]})
    orphaned = sorted({day for d in decisions for day in d["orphaned"]})
    plan_ids = [d["weekly_plan_id"] for d in decisions]

    conn = get_conn()
    placeholders = ",".join("?" * len(plan_ids))
    date_placeholders = ",".join("?" * len(lost_dates))
    rows = conn.execute(
        f"SELECT mpe.id, mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal "
        f"FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        f"WHERE mpe.household_id = ? AND mpe.weekly_plan_id IN ({placeholders}) "
        f"AND mpe.component_category IS NULL AND mpe.slot_state = 'planned' "
        f"AND mpe.date IN ({date_placeholders}) "
        f"ORDER BY mpe.date, {slot_order_sql('mpe.slot')}",
        (household_id(), *plan_ids, *lost_dates),
    ).fetchall()
    # How many shopping-list lines the takeover would touch (removed, or
    # trimmed because a surviving meal still needs some): READ off the same
    # ledger _release_plan_days reverses, with the same rule — a line
    # already in the cart or bought is left alone, so it is not counted.
    # The card's own criterion: the number in the question matches what
    # actually happens, never an estimate.
    # Two numbers, not one, because zero has two meanings and the sentence
    # has to tell them apart (found by review): "nothing changes because
    # it's all bought" and "nothing changes because nothing was ever on
    # the list for these days" are different facts.
    entry_ids = [r["id"] for r in rows]
    grocery_line_count = 0
    bought_line_count = 0
    if entry_ids:
        entry_placeholders = ",".join("?" * len(entry_ids))
        counts = conn.execute(
            f"SELECT "
            f"COUNT(DISTINCT CASE WHEN g.status = 'needed' THEN g.id END) AS needed, "
            f"COUNT(DISTINCT CASE WHEN g.status != 'needed' THEN g.id END) AS bought "
            f"FROM meal_plan_grocery_links l "
            f"JOIN grocery_items g ON g.id = l.grocery_item_id "
            f"WHERE l.household_id = ? AND l.meal_plan_entry_id IN ({entry_placeholders})",
            (household_id(), *entry_ids),
        ).fetchone()
        grocery_line_count = counts["needed"]
        bought_line_count = counts["bought"]
    conn.close()

    meals_by_date: dict[str, list[dict]] = {}
    for row in rows:
        if row["meal"]:
            meals_by_date.setdefault(row["date"], []).append(
                {"slot": row["slot"], "meal_name": row["meal"]}
            )
    days = [
        {
            "date": day,
            "weekday": date.fromisoformat(day).strftime("%A"),
            "meals": meals_by_date.get(day, []),
        }
        for day in lost_dates
    ]
    meal_count = sum(len(d["meals"]) for d in days)
    return {
        "approved_plan_ids": plan_ids,
        "days": days,
        "orphaned_dates": orphaned,
        "meal_count": meal_count,
        "grocery_line_count": grocery_line_count,
        "grocery_bought_line_count": bought_line_count,
        "note": _takeover_question(days, orphaned, grocery_line_count, bought_line_count),
    }


def _takeover_question(
    days: list[dict], orphaned: list[str], grocery_line_count: int, bought_line_count: int,
) -> str:
    """
    The confirmation as a person would say it (DESIGN_SYSTEM §8, including
    the "sounding human" rules of 2026-09-10): the span, the dinners by
    name, what it costs the shopping, then the question. Calm and plain —
    this is about losing something — so no exclamation marks and no
    softening before the fact.

      "Once it's approved, I'd replace Thursday to Sunday's dinners — Bean
       Chili, Salmon — and 11 things on your shopping list would change.
       Go ahead?"

    Dinners are what a household remembers a day by, so those are named
    (each once, in the order they come); the rest of the day's meals go too
    and the `days` payload lists every one of them. The shopping number is
    the real one (see preview_approved_takeover) and is said as "change"
    rather than "come off", because a line a surviving meal still needs is
    trimmed, not removed.

    Four shapes of the shopping clause, and the first version of this
    conflated the last three (review caught it, with an approved week whose
    overlapping days were all "out" being told "it's all bought already"):
      * lines still needed        -> "and N things on your shopping list would change."
      * none needed, some bought  -> "Nothing comes off the shopping list — it's all bought already."
      * meals, but no lines at all -> "Nothing on the shopping list changes."
      * no meals on those days    -> "Nothing's planned for <span>, so nothing would be lost."
    """
    span = _weekday_span([d["weekday"] for d in days])
    meals = [m for d in days for m in d["meals"]]
    dinners: list[str] = []
    for m in meals:
        if m["slot"] == "dinner" and m["meal_name"] not in dinners:
            dinners.append(m["meal_name"])

    if not meals:
        # Deliberately empty days (away, "none tonight") or a plan that
        # never held these days' meals: the household loses nothing but
        # the plan's claim on the dates, and the sentence must not invent
        # a loss — or a purchase.
        sentence = f"Nothing's planned for {span}, so nothing would be lost."
    else:
        # "meals" when there is no dinner to name — a component_based
        # plan's items carry no date, so its days have nothing called a
        # dinner.
        noun = ("dinners" if len(days) > 1 else "dinner") if dinners else "meals"
        # "Once it's approved": the draft itself changes nothing (Emily,
        # 2026-09-13) — approving it is the moment these days go.
        head = f"Once it's approved, I'd replace {span}'s {noun}"
        if grocery_line_count:
            things = "one thing" if grocery_line_count == 1 else f"{grocery_line_count} things"
            # The dashes only exist to carry the dinner names INTO the
            # clause that follows; with nothing following there is no
            # closing dash (review found "— Bean Chili —." in the zero case).
            named = f" — {', '.join(dinners)} —" if dinners else ""
            sentence = f"{head}{named} and {things} on your shopping list would change."
        else:
            named = f" — {', '.join(dinners)}" if dinners else ""
            if bought_line_count:
                cost = "Nothing comes off the shopping list — it's all bought already."
            else:
                cost = "Nothing on the shopping list changes."
            sentence = f"{head}{named}. {cost}"
    if orphaned:
        lost = _weekday_span([date.fromisoformat(d).strftime("%A") for d in orphaned])
        sentence += (
            f" {lost} would be left unplanned too — a plan can't keep a gap in the middle."
        )
    return sentence + " Go ahead?"


def _weekday_span(weekdays: list[str]) -> str:
    """"Thursday", "Monday and Tuesday", "Thursday to Sunday"."""
    if len(weekdays) == 1:
        return weekdays[0]
    if len(weekdays) == 2:
        return f"{weekdays[0]} and {weekdays[1]}"
    return f"{weekdays[0]} to {weekdays[-1]}"


def _apply_takeover(conn, result: dict, decisions: list[dict], new_plan_id: int,
                    period_start: str, day_count: int) -> dict:
    """
    The destructive half of retire_overlapping_plans, on a connection it
    does not own — separated only so the transaction it runs inside is one
    unmissable try/except/finally at the call site rather than a loop with
    a commit buried at the bottom of it.
    """
    for decision in decisions:
        other_id = decision["weekly_plan_id"]
        other_start, other_days = decision["previous_start"], decision["previous_day_count"]
        component_based = decision["component_based"]
        surrendered = decision["surrendered"]
        orphaned = decision["orphaned"]
        new_start_date = decision["kept_start"]
        new_day_count = decision["kept_day_count"]
        retired = decision["retired"]

        removal = _release_plan_days(other_id, surrendered, include_components=component_based, conn=conn)
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
        conn.execute(
            "UPDATE weekly_plans SET content_start_date = ?, day_count = ?, status = ?, "
            "retired_reason = ?, superseded_json = ?, updated_at = datetime('now') "
            "WHERE id = ? AND household_id = ?",
            (
                new_start_date, new_day_count,
                "retired" if retired else decision["status"],
                "superseded" if retired else "",
                json.dumps(record), other_id, household_id(),
            ),
        )
        (result["retired_plan_ids"] if retired else result["shortened_plan_ids"]).append(other_id)
    return result


def _release_plan_days(plan_id: int, dates: list[str], include_components: bool = False, conn=None) -> dict:
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

    `conn` is how retire_overlapping_plans keeps a multi-plan takeover
    atomic: given a connection, everything here — the reads, the per-meal
    grocery reversal, the prep-task and entry deletes — runs on it and
    nothing is committed or closed, so the whole takeover lands or none of
    it does. Left unset it owns one connection for the same work and
    commits at the end, which is the behaviour the single call site had
    before, minus the two extra connections it used to open and the three
    commits it used to make along the way.
    """
    if not dates:
        return {"meals_removed": 0, "grocery_removed": [], "grocery_trimmed": [], "grocery_kept_bought": []}
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
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

        removed_items, trimmed_items = [], []
        for entry_id in entry_ids:
            reversal = _grocery._reverse_meal_grocery_contributions(entry_id, conn=conn)
            removed_items.extend(reversal["removed_items"])
            trimmed_items.extend(reversal["trimmed_items"])
        if entry_ids:
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
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
    return {
        "meals_removed": len(entry_ids),
        "grocery_removed": removed_items,
        "grocery_trimmed": trimmed_items,
        "grocery_kept_bought": kept_bought,
    }


def _plan_row_by_id(weekly_plan_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    conn.close()
    return row


def _pending_draft_over(plan: dict) -> int | None:
    """
    The id of a live DRAFT, newer than `plan`, that shares at least one
    day with it — the draft the household is shaping over their approved
    week — or None. Only a draft over an APPROVED plan counts: two drafts
    never share a day (generation still replaces a draft on the spot), and
    a draft is its own front page already.
    """
    if plan.get("status") != "approved":
        return None
    row = _plan_row_by_id(plan["weekly_plan_id"])
    if row is None:
        return None
    start, days = plan_period(row)
    conn = get_conn()
    drafts = conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND status = 'draft' "
        f"AND id != ? AND NOT (status = 'draft' AND {_SQL_EXPIRED_BEFORE}) "
        f"ORDER BY created_at DESC, id DESC",
        # The HOUSEHOLD's today, like the predicate's other two readers
        # (2026-09-15). This is the THIRD reader of _SQL_EXPIRED_BEFORE
        # and its comment only names two, which is exactly how it got
        # left behind: retiring the draft was fixed and this was not, so
        # for the same four evening hours the draft survived in the
        # database and STILL wasn't the Plan tab's front page. Retiring
        # is not the only thing that stops a plan leading the tab.
        #
        # It is evaluated AFTER get_conn above, which is the shape
        # discard_draft_plan's own comment argues against one screen down
        # — noted rather than moved (2026-09-15). Harmless here and only
        # here: this function never writes, so there is no BEGIN IMMEDIATE
        # for a second connection to sit behind, and moving it would be a
        # change with no behaviour behind it. Give this function a write
        # and it wants hoisting first.
        (household_id(), row["id"], _household_today().isoformat()),
    ).fetchall()
    conn.close()
    for draft in drafts:
        if draft["created_at"] < row["created_at"]:
            continue
        other_start, other_days = plan_period(draft)
        if periods_overlap(start, days, other_start, other_days):
            return draft["id"]
    return None


def get_plan_id_for_date(meal_date: str, conn=None) -> int | None:
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

    `conn` is the read-only member of the family clear_plan_slot and
    plan_slot_empty already belong to, and it is here for one caller:
    slot_needs.set_slot_need asks this question from inside its own open
    write transaction. This function never writes, so given a connection it
    reads on it and leaves the caller to close it.

    HYGIENE AND CONSISTENCY, not a deadlock fix, and saying otherwise was
    the first version of this paragraph. A nested WRITING connection inside
    an open write transaction really does sit out SQLite's busy timeout —
    that is the minute-plus hang slot_needs' own tests reproduce on
    purpose. This one is a SELECT, and SQLite lets a reader in alongside a
    writer holding RESERVED, so calling it without `conn` from in there is
    measured at 0.8s and no hang at all. What it costs is a connection per
    call and one more place the package's "one connection inside the
    transaction" rule is not actually true; the only thing that would catch
    losing it is slot_needs' connection-counting test.
    """
    date.fromisoformat(meal_date)
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    # Approved before draft, then newest — see _current_weekly_plan_row.
    row = conn.execute(
        f"SELECT id FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        f"AND date({_SQL_PERIOD_START}) <= date(?) "
        f"AND date({_SQL_PERIOD_START}, '+' || {_SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY {_SQL_APPROVED_FIRST}, created_at DESC, id DESC LIMIT 1",
        (household_id(), meal_date, meal_date),
    ).fetchone()
    if own_conn:
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

    Nor is a DRAFT whose last day has passed (Emily, 2026-09-11). The
    fallback handed one back as "current" for twelve days — the Plan tab
    opened on "Aug 24–30 · a draft, your turn" with an Approve button, on
    a Friday in September. retire_expired_drafts is the sweep that marks
    such a draft retired; this query refuses it independently, so the chat
    tools that resolve through here between sweeps get the same answer
    the screens do. An APPROVED plan whose period has passed is still
    returned by the fallback: it was the household's real week and the
    only thing left to show, which is a different question from a draft
    nobody said yes to.
    """
    # The HOUSEHOLD's today, not the server's. The container runs UTC and
    # households default to America/Toronto, so from 8pm Toronto the
    # server's date is already tomorrow — and a plan that fails "covers
    # today" by one evening falls through to the branch below. That is
    # mostly masked, because the fallback usually hands back the same
    # plan anyway; with a future draft on file it does NOT, and the
    # household is shown next week's draft on this week's last evening.
    # Read once, before either query, and never inside a write
    # transaction: every caller passes a connection sitting on a plain
    # read, and _household_today opens its own.
    today = _household_today().isoformat()
    # An approved plan outranks a draft on the same day (2026-09-13): a
    # draft may now sit over an approved week until it is approved, and
    # "what's for dinner" — Cook, Now, defrost, prep, the list — keeps
    # following the week somebody said yes to. The Plan tab is the one
    # screen that leads with the draft instead; see get_week_menu.
    plan = conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        f"AND date({_SQL_PERIOD_START}) <= date(?) "
        f"AND date({_SQL_PERIOD_START}, '+' || {_SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY {_SQL_APPROVED_FIRST}, created_at DESC, id DESC LIMIT 1",
        (household_id(), today, today),
    ).fetchone()
    if plan:
        return plan
    return conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        f"AND NOT (status = 'draft' AND {_SQL_EXPIRED_BEFORE}) "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(), today),
    ).fetchone()


def retire_expired_drafts(today: str = "") -> list[int]:
    """
    Retire every draft of this household whose last day is already behind
    us, and say which. The lazy sweep behind Emily's 2026-09-11 decision:
    a draft whose period has ended without approval is no longer the front
    page. There is no scheduler in this app, so this runs when the plan
    and nudge endpoints are read (get_week_menu, get_week_planning_nudge)
    and nowhere else.

    The threshold is the day AFTER the period's last day, not a grace
    period beyond it. Approving a draft is what puts its meals on the
    shopping list; a draft whose every day has passed can no longer be
    shopped for or cooked from, so keeping it a day longer helps nobody,
    and the morning after is precisely when the household needs the tab
    to open on the week that is actually starting.

    Retiring here changes ONE thing: status, with `retired_reason` set to
    'expired_draft'. The period columns, the meals and the intake are all
    kept — this is "don't lead with it", not deletion — and unlike a
    takeover there is nothing to reverse on the grocery list, because a
    draft never contributed to it. Only drafts: an approved plan whose
    week has passed was the household's real week and is left alone.
    """
    # The HOUSEHOLD's today (2026-09-15), for the reason the sibling
    # reads moved in 2026-09-14: retiring is a WRITE, it is what stops a
    # plan being the front page, and it happens silently — so on the
    # server's clock a draft could be retired at 9pm on the evening of
    # its own last day, out from under a household still cooking from it.
    # Resolved here, before get_conn, so the clock is never read inside
    # this function's own write transaction.
    bound = today or _household_today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        f"SELECT id FROM weekly_plans WHERE household_id = ? AND status = 'draft' "
        f"AND {_SQL_EXPIRED_BEFORE} ORDER BY id",
        (household_id(), bound),
    ).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        conn.executemany(
            "UPDATE weekly_plans SET status = 'retired', retired_reason = 'expired_draft', "
            "updated_at = datetime('now') WHERE id = ? AND household_id = ?",
            [(plan_id, household_id()) for plan_id in ids],
        )
        conn.commit()
        logger.info("Retired %d expired draft plan(s) as of %s: %s", len(ids), bound, ids)
    conn.close()
    return ids


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
    # DAY_SLOTS rather than the same four words written out again, so the
    # keys a day carries and the order they come back in have one source.
    slots = list(DAY_SLOTS)
    for m in meal_dicts:
        if not m["date"]:
            continue
        day = by_date.setdefault(
            m["date"],
            {
                "date": m["date"], **{s: None for s in slots},
                **{f"{s}_reasoning": None for s in slots}, "snacks": [],
            },
        )
        slot = m["slot"]
        if slot not in slots:
            # A slot this app doesn't know still has to land somewhere, and
            # dinner has always been where this folds it — but it must not
            # push a real dinner off the day to get there. Which one won
            # used to depend on the unknown slot's own spelling: read
            # alphabetically, 'brunch' arrived before dinner and was
            # overwritten by it, 'elevenses' arrived after and overwrote it.
            # Now that a day is read in eating order an unknown slot always
            # comes last, so the real dinner is protected explicitly —
            # the same "first planned wins" rule the snack key follows just
            # below. The flat `meals` list still carries the row either way.
            if day["dinner"] is not None:
                continue
            slot = "dinner"
        # A day has two snacks by default (see
        # preferences.resolve_snacks_per_day), and the single `snack` key
        # can only hold one of them — first planned wins, rather than last
        # written silently replacing it. `snacks` beside it is the whole
        # truth, and the key a caller showing a day's snacks should read.
        if slot == "snack":
            if m["meal"]:
                day["snacks"].append(m["meal"])
            if day["snack"] is not None:
                continue
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
    Deliberately NOT based on recipes.times_cooked. That counter now
    moves only when a night is ticked cooked (cooker.check_off_meal), so
    it could be read here — but it answers a different question. "Never
    cooked" would call a recipe imported months ago and never made "new
    this week", which the share page's "2 new recipes this week" line
    would then say about a dish the household has been looking at for
    ages. What this signal means is "this plan brought it in", and
    created_at is the honest record of that.
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
    many planned meals are recipes this plan brought in vs. ones the
    household already had saved).
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

    # Eating order, not alphabetical order — see slot_order_sql. The id is
    # the last word because a day can hold more than one snack: the two tie
    # on date AND on slot, so without it SQLite is free to hand them back in
    # either order, and "first planned wins" (the `snack` key in
    # _build_day_based_menu) would mean a different snack run to run.
    meals = conn.execute(
        f"""
        SELECT mpe.id, mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.food_groups_json, mpe.component_category, mpe.cooked_status, mpe.reasoning,
               mpe.slot_state, mpe.open_reason, mpe.sides_json, r.ingredients_json
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ?
        ORDER BY mpe.date ASC, {slot_order_sql('mpe.slot')} ASC, mpe.id ASC
        """,
        (plan["id"],),
    ).fetchall()
    conn.close()

    meal_dicts = [
        {
            "entry_id": m["id"], "date": m["date"], "slot": m["slot"], "meal": m["meal"],
            "food_groups": json.loads(m["food_groups_json"]),
            # The dish's own ingredients — not the sides — so a caller
            # deciding whether carb is missing can check the dish itself
            # deterministically (plates.dish_has_carb) rather than trust
            # food_groups_json alone; see _complete_plates_pass. None for a
            # freeform or component meal with no recipe behind it.
            "ingredients": json.loads(m["ingredients_json"]) if m["ingredients_json"] else None,
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
            # The side(s) the app attached to make this a full plate (see
            # plates.py) — [] for the overwhelming majority of meals, and
            # `sides_label` the ready-made "with a green salad" fragment so
            # every screen says it the same way.
            "sides": _plate_sides(m["sides_json"]),
            "sides_label": _plates.sides_label(_plate_sides(m["sides_json"])),
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
        # The freezer-check ask card's own gate (Loop Board "Defrost check:
        # ask at approval") — None until the household has answered it or
        # quietly dismissed it once for this plan. See tools.defrost.
        "defrost_asked_at": plan["defrost_asked_at"],
        # The approval-time cook-ahead card's gate, same shape and same
        # reason. See tools.cook_ahead.cook_ahead_repeats.
        "cook_ahead_asked_at": plan["cook_ahead_asked_at"],
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


# How far ahead an unplanned meal still counts as "this week's cooking",
# as an offset from today, inclusive at both ends.
#
# It is 7 and not 6 to MATCH get_meal_plan's default, which is what the
# assistant reads: that one computes `today + days_ahead` and filters `<=`,
# so `days_ahead=7` is today..+7 — eight days, not seven. Measured, because
# the first version of this constant was 6 with a comment claiming the two
# already agreed, and they did not: loose dinners at +0/+6/+7/+8 gave
# get_meal_plan [+0, +6, +7] and the Cook view [+0, +6]. That one-day sliver
# is a thin band of the very bug this exists to fix — a meal the assistant
# can name and no screen will show — so the number that closes it wins over
# the tidier-sounding "a week is seven days". The cost is one extra day in
# the Cook screen's "rest of the week", which is a real meal on a real day.
#
# The two are one number apart by coincidence, not by construction: if
# get_meal_plan's window ever moves, this has to move with it, and
# test_the_horizon_matches_what_the_assistant_can_talk_about is what says so.
UNPLANNED_HORIZON_DAYS = 7


def unplanned_meals_ahead(plan: dict | None = None) -> list[dict]:
    """
    The days-ahead meals that belong to NO weekly plan, in exactly the
    shape get_weekly_plan puts its own `meals` in — so one caller can
    concatenate the two and treat them alike.

    A meal with no weekly_plan_id is a first-class shape, not an accident:
    plan_meal writes one for every one-off chat request, and
    resolve_needs_you_dinner deliberately writes one when the current plan's
    period doesn't cover the date (2026-09-11 — attaching it to a plan that
    doesn't cover the day 500'd the tap). Bug, 2026-09-13: nothing on any
    SCREEN read those rows back. get_cooker_view is plan-scoped, and every
    surface a cook actually looks at — Now's moves, cook mode, the morning
    text — is built off it, so a brand-new household answering "Tonight
    needs a dinner" saw the card vanish and nothing take its place. The
    meal was saved and invisible.

    Days `plan` already covers are left out, and that is the whole of the
    no-duplicates rule: on a day a plan speaks for, the plan is the answer
    and this changes nothing. Only a day no plan covers falls back to its
    own rows.

    Bounded at UNPLANNED_HORIZON_DAYS from today (see it for why that
    number) and never looking back: this answers "what is there to cook
    from here on", not "what has this household ever eaten". A loose meal
    in the past is still readable through get_meal_plan and
    get_recent_meal_history.
    """
    # The household's today, for the reason this function exists at all: the
    # window never looks back, so reading the SERVER's date would drop a
    # loose meal saved on the household's own evening the moment the two
    # dates differ — which is this very bug, one door over.
    today = _household_today()
    start = today.isoformat()
    end = (today + timedelta(days=UNPLANNED_HORIZON_DAYS)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT mpe.id, mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.food_groups_json, mpe.component_category, mpe.cooked_status, mpe.reasoning,
               mpe.slot_state, mpe.open_reason, mpe.sides_json
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.weekly_plan_id IS NULL
          AND mpe.date >= ? AND mpe.date <= ?
        ORDER BY mpe.date ASC, {slot_order_sql('mpe.slot')} ASC, mpe.id ASC
        """,
        (household_id(), start, end),
    ).fetchall()
    conn.close()

    covered_start = covered_end = None
    if plan and plan.get("weekly_plan_id") is not None:
        covered_start = plan["period_start_date"]
        covered_end = plan["period_end_date"]

    return [
        {
            "entry_id": m["id"], "date": m["date"], "slot": m["slot"], "meal": m["meal"],
            "food_groups": json.loads(m["food_groups_json"]),
            "component_category": m["component_category"],
            "cooked_status": m["cooked_status"],
            "reasoning": m["reasoning"] or None,
            "slot_state": m["slot_state"],
            "open_reason": m["open_reason"] or None,
            "sides": _plate_sides(m["sides_json"]),
            "sides_label": _plates.sides_label(_plate_sides(m["sides_json"])),
        }
        for m in rows
        if not (covered_start is not None and covered_start <= m["date"] <= covered_end)
    ]


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


def _slot_clock_labels() -> dict:
    """
    "8:00" / "12:30" / "6:30" — when this household's three meals actually
    land, said the way a person says a time (DESIGN_SYSTEM §8).

    Read out of moves.py rather than re-derived here, deliberately: Today's
    timeline and the Meals day card now put the same hour on screen for the
    same meal, and two copies of the dinner_window mapping is exactly how
    they would come to disagree. Imported inside the function because
    moves.py reads this module's cooker view — a module-level import would
    close the cycle at import time.
    """
    from . import moves as _moves

    dinner = _moves._dinner_clock()
    return {
        slot: _moves._clock(_moves._slot_time(slot, dinner))
        for slot in ("breakfast", "lunch", "dinner")
    }


# ---------- The approved receipt's two lines (flows 3) -------------------
# Emily's approved 2026-09-08 design ends approval in ONE short receipt:
# a title that counts the week, and a line about the freezer. Both are built
# here rather than in shell.js so the numbers and the sentence they live in
# cannot drift apart, and so they can be tested at all — shell.js has no JS
# test harness in this repo.

# One to twelve as words, digits above (Emily, 2026-09-08). Kept separate
# from coordination._NUMBER_WORDS, which stops at ten and feeds different
# copy — widening that one would silently reword the allergy warnings.
# Digits, to match the week card's own subtitle ("4 cooks, 3 made ahead")
# and the ask lines — one number style per screen (verifier, 2026-09-08).
_RECEIPT_NUMBER_WORDS: dict[int, str] = {}


def _receipt_number(n: int) -> str:
    return _RECEIPT_NUMBER_WORDS.get(n, str(n))


def _is_cook(entry: dict | None) -> bool:
    """
    A slot somebody actually cooks. A reheat night and a made-ahead night
    are meals but not cooks (the cooking already happened), and takeout is
    neither cooked nor made ahead — counting it would overstate the week's
    work. Same rule as the week card's own "4 cooks, 3 made ahead" subtitle
    (shell.js weekCountsLabel), deliberately, so the receipt and the card
    can't put different numbers on the same week.
    """
    if not entry or entry.get("state") != "planned":
        return False
    return entry.get("source") not in ("leftovers", "takeout")


def _pending_thaw_count(weekly_plan_id: int) -> int:
    """
    Every freezer-to-fridge move still outstanding on this plan — including
    a ready-made earmark, which has no meal_plan_entry_id but is still
    something to move. get_week_menu's per-entry `defrost` deliberately
    skips those (a task belonging to no single slot can't sit on one card);
    the receipt counts the whole week, so it counts them.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM prep_tasks "
        "WHERE household_id = ? AND weekly_plan_id = ? AND task_type = 'defrost' "
        "AND status = 'pending'",
        (household_id(), weekly_plan_id),
    ).fetchone()
    conn.close()
    return row["n"] if row else 0


def week_receipt(days: list[dict], weekly_plan_id: int, today: str = "") -> dict:
    """
    The approved week in one sentence plus one line.

    Returns `meals`, `recipes`, `cooks`, `list_count`, `thaw_count` and the
    two strings built out of them:

      title      "16 meals, 5 recipes, one list of 23 ingredients."
      thaw_line  "Two things to move to the fridge this week." when there is
                 something to thaw, else "Nothing to thaw before Wednesday."
                 — Wednesday being the next day of the plan somebody cooks,
                 which is the day the question would next come up. With no
                 cook left in the plan it drops to "Nothing to thaw this
                 week." rather than naming a day that isn't there.

    ...and `batched_line`: what approval batched for the household, said
    once on All set (see batched_line below) — "" when nothing was.

    `days` is get_week_menu's own day list, so an away night ("Out —
    nothing to cook", state planned_empty) and an open slot count as
    neither a meal nor a cook, and a reheat night counts as a meal only.

    `recipes` is the number of DIFFERENT dishes somebody cooks — the word
    the screen uses (Emily, 2026-09-13: "'6 cooks' is confusing language
    ... say it's recipes instead"). A dish cooked on two nights is one
    recipe and two cooks, so the two numbers are kept apart: `cooks` still
    counts the week's work, the way the week card's "4 cooks, 3 made
    ahead" does, and the receipt's tiles show `recipes`.

    `list_count` is what is still to buy — the same 'needed' view the
    Grocery tab opens on, which is where "Open the list" lands. A spice
    waiting unticked in "Spices this week" (status 'spice') and last
    week's leftover waiting for keep-or-drop ('carried') are not on it;
    a hand-added line and a staple's suggestion are. Zero is not a
    failure (a household whose kitchen already had everything), so the
    sentence drops that clause instead of promising a list of nothing.

    `today` is the household's day, passed in by get_week_menu, which has
    already resolved it. Left out it resolves its own, on the household's
    clock (2026-09-15): the no-thaw line names a weekday out loud — the
    week's next cook from today on — and on the server's date a household
    a day behind skipped tonight's cook and was told to thaw nothing
    before TOMORROW, in the evening the receipt is read.
    """
    meals = 0
    cooks = 0
    dishes: set[str] = set()
    for day in days:
        for slot in WEEK_SLOTS:
            entry = day.get(slot)
            if not entry or entry.get("state") != "planned":
                continue
            meals += 1
            if _is_cook(entry):
                cooks += 1
                dishes.add((entry.get("title") or "").strip().casefold())
    recipes = len(dishes)

    list_count = len(_grocery.list_grocery_list("needed"))
    thaw_count = _pending_thaw_count(weekly_plan_id)

    parts: list[str] = []
    if meals:
        parts.append(f"{_receipt_number(meals)} {'meal' if meals == 1 else 'meals'}")
    if recipes:
        parts.append(f"{_receipt_number(recipes)} {'recipe' if recipes == 1 else 'recipes'}")
    if list_count:
        parts.append(
            f"one list of {_receipt_number(list_count)} "
            f"{'ingredient' if list_count == 1 else 'ingredients'}"
        )
    else:
        parts.append("nothing left to buy")
    title = ", ".join(parts)
    title = f"{title[0].upper()}{title[1:]}." if title else "Your week is set."

    if thaw_count:
        thing = "thing" if thaw_count == 1 else "things"
        count = _receipt_number(thaw_count)
        thaw_line = f"{count[0].upper()}{count[1:]} {thing} to move to the fridge this week."
    else:
        today_str = today or _household_today().isoformat()
        next_cook = next(
            (
                d["date"] for d in days
                if d["date"] >= today_str
                and any(_is_cook(d.get(s)) for s in WEEK_SLOTS)
            ),
            None,
        )
        when = _weekday_label(next_cook)
        thaw_line = f"Nothing to thaw before {when}." if when else "Nothing to thaw this week."

    return {
        "meals": meals, "recipes": recipes, "cooks": cooks, "list_count": list_count,
        "thaw_count": thaw_count, "title": title, "thaw_line": thaw_line,
        "batched_line": batched_line(weekly_plan_id),
    }


# What a batch of each kind is cooked in — "one pot Sunday covers Tuesday
# and Thursday". Keyed by batch_components' participle; anything unlisted
# is just "one cook".
_BATCH_VESSEL = {
    "boiled": "one pot", "poached": "one pot", "steamed": "one pot", "cooked": "one pot",
    "braised": "one pot", "roasted": "one tray", "baked": "one tray",
}

_SLOT_PLURAL = {"breakfast": "breakfasts", "lunch": "lunches", "dinner": "dinners", "snack": "snacks"}


def batched_line(weekly_plan_id: int) -> str:
    """
    The one quiet line under All set's numbers that says what approval
    batched (Loop Board "Batch a shared ingredient automatically when the
    household preps", 2026-09-21 — Emily's "show the value" note): nothing
    was asked, so this is the only place the household hears that one
    cook now covers several meals. Built here, not in the shell, so the
    words live with the rule that earns them.

      one component     "I’ve batched the rice: one pot Sunday covers
                         Tuesday and Thursday."
      one repeated dish "I’ve made Monday’s chili big enough for
                         Thursday too."
      more than one     "I’ve batched the rice and the eggs — one cook
                         each, covering 3 dinners." — the count is the
                         LATER meals the batches feed (the cooks the
                         household is spared), in the tiles' own digits
                         (_receipt_number), and the noun is their slot
                         when they share one, else "meals".

    Empty when nothing on the plan is batched, so the line is simply not
    there. Reads what stands on the plan (batch_components.
    batched_components, cook_ahead.batched_dishes — the chosen batches,
    never the planner's own leftover nights), which right after an
    approval is exactly what that approval wrote; a batch undone later
    (a swap on the Plan tab) drops out of the sentence with it.
    """
    from . import batch_components as _batch_components
    from . import cook_ahead as _cook_ahead
    from . import leftovers as _leftovers

    comps = [dict(c, kind="component") for c in _batch_components.batched_components(weekly_plan_id)]
    dishes = [dict(d, kind="dish") for d in _cook_ahead.batched_dishes(weekly_plan_id)]
    batches = [b for b in comps + dishes if b["covered"]]
    if not batches:
        return ""
    batches.sort(key=lambda b: b["date"])

    if len(batches) == 1:
        b = batches[0]
        # Two later dishes on one day (a lunch and a dinner that both use
        # the rice) are one day to say.
        days = _leftovers._join_days(list(dict.fromkeys(_weekday_label(c["date"]) for c in b["covered"])))
        if b["kind"] == "component":
            vessel = _BATCH_VESSEL.get(b["verb"], "one cook")
            return f"I’ve batched the {b['ingredient']}: {vessel} {_weekday_label(b['date'])} covers {days}."
        return f"I’ve made {_weekday_label(b['date'])}’s {b['dish']} big enough for {days} too."

    # A dish cooked twice (the three-day leftover rule) is named once.
    labels = list(dict.fromkeys(
        f"the {b['ingredient']}" if b["kind"] == "component" else b["dish"] for b in batches
    ))
    names = _leftovers._join_days(labels)
    covered = [c for b in batches for c in b["covered"]]
    slots = {c.get("slot") for c in covered}
    noun = _SLOT_PLURAL.get(slots.pop(), "meals") if len(slots) == 1 else "meals"
    cooks = "one cook each" if len(labels) == len(batches) else f"{_receipt_number(len(batches))} cooks"
    return f"I’ve batched {names} — {cooks}, covering {_receipt_number(len(covered))} {noun}."


def _weekday_label(date_str: str | None) -> str:
    if not date_str:
        return ""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")
    except (TypeError, ValueError):
        return ""


def _day_clock_flags(day: str, today_str: str) -> dict:
    """
    Whether a day is already over, and whether it is today — ON THE
    HOUSEHOLD'S CLOCK, which is the whole point of this living here.

    The screen used to work both out for itself, comparing the day against
    the BROWSER's date (shell.js classifyDay, off todayLocalStr). The
    server refuses a write into the past on households.timezone
    (night_has_gone), and that column defaults to America/Toronto for every
    household with nothing in the app prompting a change — so a phone west
    of it disagrees with the server for as long as its own offset: three
    hours a night in Vancouver, two in Denver, one in Chicago (the figures
    add-a-night-refuses-the-past and swap-refuses-the-past both measured).
    Reproduced here on a throwaway database: a Vancouver phone at 21:30
    read its own tonight as today while the server had already rolled over
    and refused every change to it.

    Both flags together, deliberately. They are one line of arithmetic off
    one date and a screen that took `is_past` from here and `is_today` from
    the phone would say "Tonight" over a night it had just greyed every
    control on — a new bug rather than a smaller one.

    `today_str` is the caller's ONE reading of _household_today (see
    get_week_menu), so this reads no clock of its own and costs no
    connection: a day added to the payload can never cost another.
    """
    return {"is_past": day < today_str, "is_today": day == today_str}


def get_week_menu(weekly_plan_id: int | None = None) -> dict:
    """
    The weekly menu for the Week tab (design_handoff_shell/
    README.md §5) — the "one backend ask" for that redesign. Unlike
    get_weekly_plan's `menu` (which only lists dates that already have at
    least one entry), this always returns exactly 7 days starting at the
    plan's week_start_date, one dict per day with `breakfast`/`lunch`/
    `dinner` keys — each either None (nothing planned, drives the "Pick"
    row) or `{title, meta, source}`.

    Every day also carries `is_past` and `is_today` on the HOUSEHOLD's
    clock (see _day_clock_flags): the screen greys its controls off those
    rather than off the browser's date, so it and the server agree about
    which nights are still changeable by construction rather than by each
    doing its own arithmetic.

    `source`/`meta` have no backing column in meal_plan_entries, so most of
    them are derived with a keyword heuristic against the entry's freeform
    text — documented here as a judgment call, not a spec'd mapping:
      - a night in a CONFIRMED leftovers chain (both the entry and its
        source agree — see leftovers.plan_leftover_chains) -> source
        "leftovers", meta "reheat", title replaced with
        leftovers.leftovers_headline naming the source dish and night,
        checked before the text heuristic below because a chain entry can
        carry a real recipe_id (the source's own dish) with nothing in its
        own freeform text for a regex to catch.
      - "leftover"/"leftovers" in the text (and no confirmed chain) ->
        source "leftovers", meta "reheat"
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
    there's nothing to anchor 7 days to — plus `suggested_period`, the
    week the screen should name instead (suggest_planning_period's
    answer, the same one the Now nudge is built from).
    """
    # ONE reading of the household's clock for this whole payload
    # (2026-09-15), threaded down instead of each part asking again.
    # retire_expired_drafts, next_period_after, week_receipt and the two
    # branches' "is this day still ahead of us" all need the same day, and
    # each resolve costs a connection and a SELECT — so a payload that
    # already read the clock four times would have read it seven. Resolved
    # before the first get_conn below, because household_now opens its own.
    today_str = _household_today().isoformat()

    # The Plan tab's read is one of the two moments an expired draft is
    # retired (the nudge is the other) — see retire_expired_drafts.
    if weekly_plan_id is None:
        retire_expired_drafts(today_str)

    conn = get_conn()
    household = conn.execute(
        "SELECT name FROM households WHERE id = ?", (household_id(),)
    ).fetchone()
    conn.close()
    household_name = household["name"] if household else ""

    plan = get_weekly_plan(weekly_plan_id)
    if weekly_plan_id is None and plan.get("weekly_plan_id"):
        # The Plan tab is the one screen that leads with a DRAFT sitting
        # over the household's approved week (2026-09-13: a draft waits
        # until approval, so the two coexist for a while). Every other
        # resolver prefers the approved plan — Cook, Now, the list follow
        # the real week — but here the draft is the pending decision, and
        # a screen that hid it would leave the household no way back to
        # the week they were shaping. A pinned read (weekly_plan_id given)
        # is left alone: it asked for a specific plan.
        pending = _pending_draft_over(plan)
        if pending is not None:
            plan = get_weekly_plan(pending)
    if not plan.get("weekly_plan_id"):
        return {
            "weekly_plan_id": None, "week_start_date": None,
            "household_name": household_name, "days": [], "menu_is_suggested": False,
            "slot_times": _slot_clock_labels(),
            "receipt": None,
            "suggested_period": suggest_planning_period(from_date=today_str),
        }

    # What "Plan next week ›" under this plan offers — sized by the
    # household's rhythm, not by the plan on screen (Emily, 2026-09-13: a
    # two-day plan was offering two more days). See next_period_after.
    next_period = next_period_after(plan, today=today_str)

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
        # Passed through so the Meals screen's approved receipt (the only
        # place the freezer-check ask card shows itself) knows whether to
        # offer it — see tools.defrost.meat_items_for_plan.
        "defrost_asked_at": plan["defrost_asked_at"],
        # Same passthrough for the cook-ahead ask card, which sits on the
        # same receipt right below the freezer check — see
        # tools.cook_ahead.cook_ahead_repeats.
        "cook_ahead_asked_at": plan["cook_ahead_asked_at"],
        # The household's very first plan — All set says "Week 1 is
        # planned." for it and the dates for every week after (2026-09-18).
        "is_first_plan": bool(plan.get("is_first_plan")),
        "grocery_preview": None,
        # The dietary/allergy warning the review band shows above the
        # Approve button. Recomputed here rather than stored with the plan
        # so it stays true after a swap, and only for a draft: a week that's
        # already approved has had its decision made, and a warning about it
        # would be a scold rather than a help.
        "conflicts": [],
        "conflicts_note": None,
        # The two halves of that warning, as the approved 2026-09-08 Meals
        # design renders them: `settle` is the hard clash's own card above
        # the week card (note/meal/date/member/count), `soft_note` the one
        # quiet line under it. See coordination._settle / _soft_note.
        "settle": None,
        "soft_note": None,
        # The candidates for the approval itself — a different question from
        # other_adults below, and the reason this is a second field rather
        # than a widening of that one. other_adults answers "who gets TOLD",
        # which is only knowable AFTER somebody has approved; this answers
        # "who is about to approve", which is only ever asked BEFORE.
        # Reading one list as if it were the other is what made the
        # who's-approving step unreachable: a draft has no approver, so
        # other_adults was empty, so the picker was skipped and every week
        # was approved by nobody. (Same shape as WEEK_SLOTS/DAY_SLOTS — one
        # name was carrying two meanings.)
        #
        # Filled in the draft-only block below, and left empty once the week
        # is approved: there is nothing left to approve. The screen asks only
        # when there is more than one name (static/shell.js approveWeek) — a
        # single-adult household has no question to answer.
        "approving_adults": [],
        # For a draft: what approving it takes off an approved week (see
        # below). None for an approved week and in the ordinary case.
        "replaces": None,
    }
    if plan["status"] != "approved":
        approval["approving_adults"] = [
            p["name"] for p in _coordination.get_household_people()
        ]
        approval["grocery_preview"] = preview_plan_grocery_impact(plan["weekly_plan_id"])
        # How many new recipes approving will write up first (the recipe
        # pass, 2026-09-21) — so the Approve button can say what the wait
        # is for instead of "Approving…" for ten seconds. 0 for a week made
        # of saved recipes, where approval is as quick as it always was.
        approval["recipes_pending"] = len(_recipes.pending_recipes_for_plan(plan["weekly_plan_id"]))
        # What approving THIS draft takes off an approved week — the days
        # and the sentence — so the screen can say it beside the Approve
        # button rather than after the fact. None in the ordinary case.
        plan_row = _plan_row_by_id(plan["weekly_plan_id"])
        if plan_row is not None:
            period_start, period_days = plan_period(plan_row)
            approval["replaces"] = preview_approved_takeover(period_start, period_days)
        try:
            found = _coordination.check_plan_conflicts(plan["weekly_plan_id"])
            approval["conflicts"] = found["conflicts"]
            approval["conflicts_note"] = found["note"]
            approval["settle"] = found.get("settle")
            approval["soft_note"] = found.get("soft_note")
        except Exception:
            # The Meals screen must still render if the check itself breaks.
            logger.exception("Conflict check failed for plan %s", plan["weekly_plan_id"])
    # Every adult but the one who approved. Empty for a one-adult household,
    # and empty when nobody is recorded as having approved: an approval with
    # no name raises no notification (see get_active_notifications #4, which
    # requires one), so nobody WAS told — and every plan approved before this
    # flow existed has a blank approved_by. Listing all the adults there
    # would put a claim on screen that is simply untrue, to a reader who may
    # be among those supposedly told.
    #
    # NOTE (2026-09-09): nothing on screen reads this today. It was the
    # receipt's "{Other adult} has been told the week is settled." — a line
    # the 2026-09-08 flows-3 rework deleted along with receiptBodyText and
    # approvedAtLabel. Kept because the fact is still true and still the
    # honest answer to "who was told", and because the notification it
    # describes does fire; but do not read the comment above as describing
    # something the household currently sees. Its one live consumer used to
    # be the who's-approving picker, which was reading it for the wrong
    # question — see approving_adults above.
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
        suggestions = None
        for d in dates:
            row = by_date.get(d, {})
            # component_based plans have no fixed day mapping and aren't
            # part-week-aware yet (see the day-based branch below for the
            # real field) — always False here so the key exists either way.
            day = {"date": d, "before_plan_start": False, **_day_clock_flags(d, today_str)}
            for s in slots + ("snack",):
                title = row.get(s)
                # `state` matters even here, where every slot is "planned"
                # by construction: the Meals screen keys "Cook this" /
                # "Swap it" and the dinner star off it, so omitting it made
                # those disappear for component-based households.
                day[s] = (
                    {"title": title, "meta": None, "source": "plan", "state": "planned", "reason": None}
                    if title else None
                )
            # Same two shapes as the day-based branch below, so one caller
            # can render either kind of plan. A suggested schedule spreads
            # one snack per day, so the list is never longer than one here.
            day["snacks"] = [day["snack"]] if day["snack"] else []
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
            "slot_times": _slot_clock_labels(),
            # Only an approved week has a receipt to show — a draft's
            # question is still "is this right", not "here's what you did".
            "receipt": (
                week_receipt(days, plan["weekly_plan_id"], today=today_str)
                if plan["status"] == "approved" else None
            ),
            "next_period": next_period,
            **approval,
        }

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.recipe_id, mpe.freeform_meal,
               COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.slot_state, mpe.open_reason, mpe.reasoning, mpe.derived_from_json,
               mpe.food_groups_json, mpe.sides_json, mpe.cooked_status,
               r.prep_time_minutes, r.cook_time_minutes,
               r.tags_json, r.instructions_json, r.main_protein, r.ingredients_json,
               r.source_url, r.source_book, r.source_author, r.source_page,
               (SELECT COUNT(*) FROM recipe_photos rp WHERE rp.recipe_id = r.id) AS photo_count
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ?
        """,
        (plan["weekly_plan_id"],),
    ).fetchall()
    prefs = conn.execute(
        "SELECT eating_style, plates_intro_shown_at FROM meal_preferences WHERE household_id = ?",
        (household_id(),),
    ).fetchone()
    # Every freezer-to-fridge move this plan already has on the books, keyed
    # by the entry it feeds. The Meal step's "The plate" card ends with
    # either the thaw this dish needs or "Nothing to thaw", and that has to
    # be the SAME row Today's fridge move reads (moves.py) rather than a
    # second guess at what is frozen. A task with no meal_plan_entry_id
    # (a ready-made earmark, see defrost.py) belongs to no single slot and
    # is deliberately skipped.
    defrost_rows = conn.execute(
        "SELECT meal_plan_entry_id, task_date, description FROM prep_tasks "
        "WHERE household_id = ? AND weekly_plan_id = ? AND task_type = 'defrost' "
        "AND meal_plan_entry_id IS NOT NULL ORDER BY task_date",
        (household_id(), plan["weekly_plan_id"]),
    ).fetchall()
    conn.close()
    defrost_by_entry = {
        r["meal_plan_entry_id"]: {"date": r["task_date"], "note": r["description"]}
        for r in defrost_rows
    }
    # One read of the household's carb level for the whole menu — their
    # eating_style, facts and notes together (plates.household_carb_level;
    # Emily, 2026-09-21: low carb is not no carb).
    carb_level = _plates.household_carb_level(prefs["eating_style"] if prefs else "")
    plate_rule = _plates.plate_rule(level=carb_level)

    # Every confirmed cook-once-eat-twice pairing on this plan (see
    # leftovers.py) — computed once for the whole week rather than per slot,
    # since it's one query either way and build_slot needs it for every
    # reheat night it might encounter. Only entries BOTH sides agree on come
    # back here, same rule the Cook view (cooker._apply_leftover_chains)
    # already applies: a reheat night is only rendered as one when the
    # source it names also names it back.
    from . import leftovers as _leftovers
    chains = _leftovers.plan_leftover_chains(plan["weekly_plan_id"])

    def _effective_food_groups(row) -> list[str]:
        """
        The row's food_groups_json, with a deterministic backstop: a dish
        whose own name or ingredients already carry a carb counts as
        having one even when the model's food_groups missed it
        (plates.dish_has_carb) — Emily's Cajun Salmon with Green Beans and
        Sweet Potato Mash (2026-09-22), recorded protein+vegetable only,
        so the "+Add a carb" chip and the plate note both read it as
        short a carb it already had. Only applied once the dish has SOME
        recorded groups; one with none stays unknown, same as
        missing_groups always treats it.
        """
        groups = json.loads(row["food_groups_json"] or "[]")
        if groups and "carb" not in groups:
            ingredients = json.loads(row["ingredients_json"] or "[]") if row["ingredients_json"] else []
            if _plates.dish_has_carb(row["meal"], ingredients):
                groups = groups + ["carb"]
        return groups

    def plate_note(row, sides) -> str:
        """
        The one short line about this plate: "with a green salad" when the
        app added something, "one-pot, nothing extra" when the dish covers
        the household's plate rule on its own AND was actually cooked that
        way — a plate that's complete but used a grill, or names a second
        vessel for one of its components, gets neither line rather than the
        wrong one (Emily, 2026-09-13: a grilled burger-and-charred-vegetable
        plate that was food-group-complete still isn't "nothing extra to
        wash"). See plates.contradicts_one_pot.

        An added side is disclosed on EVERY slot — the household's shopping
        list has it, so their card must say so. The reassurance half is
        DINNER ONLY, deliberately: it is the answer to "why does Tuesday
        say 'with a salad' and Wednesday say nothing", and repeating it
        under all four slots of all seven days would be chrome, not an
        answer. See plates.py.
        """
        label = _plates.sides_label(sides)
        if label:
            return label
        if row["slot"] != "dinner":
            return ""
        entry = {"slot": row["slot"], "food_groups": _effective_food_groups(row)}
        if not (_plates.has_food_groups(entry) and _plates.is_complete(entry, plate_rule)):
            return ""
        tags = json.loads(row["tags_json"] or "[]")
        instructions = json.loads(row["instructions_json"] or "[]")
        if _plates.contradicts_one_pot(row["meal"], tags, instructions):
            return ""
        return "one-pot, nothing extra"

    def build_slot(row) -> dict | None:
        # The three states a slot can be in. Only a slot that is genuinely
        # absent returns None — and after a generation through
        # _finish_week_slots there shouldn't be any.
        if row["slot_state"] == "planned_empty":
            # A day tapped off "Which days?" is not an out night: nobody is
            # away, it was left out on purpose, and the screen says so in
            # those words (2026-09-21, board D1). Read off derived_from,
            # where _finish_week_slots wrote it.
            derived = json.loads(row["derived_from_json"] or "{}")
            skipped = derived.get("constraint") == _week_intake.SKIPPED_DAY_CONSTRAINT
            return {
                "title": "Not planned" if skipped else "Out — nothing to cook", "meta": None, "source": "empty",
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
        sides = _plate_sides(row["sides_json"])
        common = {
            "state": "planned", "reason": row["reasoning"] or None, "entry_id": row["id"],
            "sides": sides, "plate_note": plate_note(row, sides),
            # The plate in its own words, for the Meal step's "The plate"
            # card: which of protein/carb/vegetable this dish records, and
            # the thaw it needs (or doesn't). Both are already stored —
            # this only stops the Meals screen having to ask a second
            # endpoint for what it needs to describe one meal.
            "food_groups": _effective_food_groups(row),
            # The plate as parts — protein, veg, carb — for the card's
            # chips and the Meal step's rows (Emily, 2026-09-13, "Shaping
            # the Draft" Flows A and B): what each part is, whether a side
            # or the dish covers it, and what is missing. plate_parts.py.
            "main_protein": row["main_protein"] or "",
            "plate_parts": _plate_parts_mod.parts_of_plate(
                row["slot"] or "dinner", _effective_food_groups(row),
                row["main_protein"], sides, prefs["eating_style"] if prefs else "",
                carb_level=carb_level,
                ingredients=json.loads(row["ingredients_json"] or "[]") if row["ingredients_json"] else None,
            ),
            "defrost": defrost_by_entry.get(row["id"]),
            # Where the dish's recipe came from, said the one way every
            # screen says it (recipes.recipe_citation); None for a generated
            # or typed dish. The Day step's card prints it under the name.
            "citation": _recipes.recipe_citation(
                row["source_url"] or "", row["source_book"] or "", row["source_author"] or "",
                row["source_page"] or "", has_photo=bool(row["photo_count"]),
            ) if row["recipe_id"] else None,
            # Somebody has cooked and eaten this one. Additive, and the
            # Review screen's "+" is the first reader: a day already cooked
            # is not a day to plan into, because replacing the row would
            # take the tick, the inventory it depleted and the shopping
            # line for a meal that has already been eaten with it.
            "cooked": (row["cooked_status"] or "") == "done",
        }
        # A confirmed chain (see `chains` above) takes priority over the
        # freeform-text heuristic below: a chain entry can carry a REAL
        # recipe_id (the source's own dish, so the reheat night can say
        # what it's actually eating) with nothing in its freeform text for
        # the regex to catch — which is exactly how this used to show up as
        # "Korean Beef Bulgogi Lettuce Wraps · 35 min · Cook this" instead
        # of the reheat it actually is (Loop Board). No time chip (nothing
        # is cooked tonight) and no plate note (not a plate this app
        # assembled tonight either — same reasoning as the freeform case
        # just below).
        leftover = chains["leftovers"].get(row["id"])
        if leftover:
            src = leftover["source"]
            # Same wording rule as the Cook tab (cooker.py): a day the
            # household chose to cook ahead for reads "Made ahead", not
            # "Leftovers" — the plan and the cook schedule must agree.
            headline = (
                _leftovers.made_ahead_headline(src["meal"], src["date"])
                if leftover.get("cook_ahead")
                else _leftovers.leftovers_headline(src["meal"], src["date"])
            )
            return {
                "title": headline,
                "meta": "reheat", "source": "leftovers", **common, "plate_note": "",
                # The same two facts the headline is built from, kept apart
                # from it so a caller can say "made ahead Sunday" in an
                # eyebrow and "Egg White Bites" as the dish name without
                # having to unpick the sentence. cook_ahead is the one word
                # of difference between the two headlines above.
                "leftover_from": {
                    "date": src["date"],
                    "meal": src["meal"],
                    "cook_ahead": bool(leftover.get("cook_ahead")),
                },
            }
        text = (row["freeform_meal"] or "").lower()
        # Neither a reheat nor takeout is a plate this app assembled, so
        # neither gets a plate note — "one-pot, nothing extra" over a night
        # that reheats an earlier batch would be describing the wrong meal.
        if re.search(r"leftovers?\b", text):
            return {"title": title, "meta": "reheat", "source": "leftovers", **common, "plate_note": ""}
        if re.search(r"take[\s-]?out|delivery|order in", text):
            return {"title": title, "meta": "takeout", "source": "takeout", **common, "plate_note": ""}
        prep = row["prep_time_minutes"] or 0
        cook = row["cook_time_minutes"] or 0
        total = prep + cook
        meta = f"{total} min" if total else None
        return {"title": title, "meta": meta, "source": "plan", **common}

    # The one short fact a row carries beside its days ("Mexican, as
    # asked", "travels well") — read off the entry's derived_from, so it is
    # only ever said of a slot the household's own answer actually shaped
    # (draft_opener.asked_fact). Added onto every planned slot after the
    # fact so the three build_slot returns above stay as they are.
    from . import draft_opener as _draft_opener  # lazy: it reads meal_variety, which reaches back here

    def with_asked(built, row):
        if built and built.get("state") == "planned":
            built["asked"] = _draft_opener.asked_fact({
                "slot_state": row["slot_state"], "derived_from": row["derived_from_json"],
            })
        return built

    by_date_slot = {}
    # Snacks are a LIST per day, not one entry: two different snacks a day
    # is the default (preferences.resolve_snacks_per_day), so keying them
    # by (date, slot) like the other three would silently keep only the
    # last one. Ordered by entry id — the order they were planned in.
    snacks_by_date: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda row: row["id"]):
        if r["slot"] in slots:
            by_date_slot[(r["date"], r["slot"])] = with_asked(build_slot(r), r)
        elif r["slot"] == "snack":
            built = with_asked(build_slot(r), r)
            if built:
                snacks_by_date.setdefault(r["date"], []).append(built)

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
        {
            "date": d, "before_plan_start": d < content_start,
            **_day_clock_flags(d, today_str),
            **{s: by_date_slot.get((d, s)) for s in slots},
            # Both shapes, deliberately: `snacks` is the honest one (a day
            # has two by default), `snack` the first of them for a caller
            # that only has room for one. Before 2026-09-08 neither
            # existed, so a snack the chat had genuinely swapped could not
            # appear on the Meals screen at all — the swap wrote, the
            # screen had nowhere to draw it, and the household was told a
            # change had happened that they could not see (Julia).
            "snacks": snacks_by_date.get(d, []),
            "snack": (snacks_by_date.get(d) or [None])[0],
        }
        for d in dates
    ]

    # This Week's day card (design_handoff_home_manager option 6a) shows the
    # same two-quick-dinner "Pick" rows on ANY day's empty dinner slot, not
    # just the one nearest gap get_needs_you_items flags for the Today band —
    # so a day beyond that 48h window still has something to tap instead of
    # a dead end. Only for today-or-future days: a past day's empty dinner
    # is just "not planned," nothing to suggest into it.
    #
    # The HOUSEHOLD's today (2026-09-15). Pre-existing, and moved here
    # rather than left because this branch put the OTHER half of
    # get_week_menu's clock on the household — and a function answering
    # about two different days is a new bug, not a smaller one (the
    # 2026-09-14 log entry's own lesson). On the server's date a
    # household a day behind lost the Pick rows on tonight's empty
    # dinner, in the very evening they would reach for them. `today_str`
    # is the one this function resolved at the top; the component branch
    # above gates its own Pick rows on the same value.
    suggestions = None
    for day in days:
        if day["dinner"] is None and day["date"] >= today_str:
            if suggestions is None:
                suggestions = _suggest_quick_dinners()
            day["dinner_suggestions"] = suggestions

    intake = _week_intake.get_week_intake(plan["week_start_date"])
    trip = _decorate_with_needs(days, plan["week_start_date"])
    _decorate_with_holidays(days)
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
        # When breakfast/lunch/dinner land for this household — the Day
        # step's eyebrows ("Dinner · 6:30"). See _slot_clock_labels.
        "slot_times": _slot_clock_labels(),
        "headline": _week_headline(plan, days, intake),
        # The draft's two opening lines — what it planned around and the
        # one thing worth knowing (draft_opener.build_opener). Built from
        # the rows and the intake, so it can't describe a week it didn't
        # make. Only for a draft: an approved week's band is about the
        # week, not the decision. Never fails the screen.
        "draft_opener": _safe_draft_opener(rows, intake, plan, days) if plan["status"] != "approved" else [],
        # Told once, and only once — see PLATES_INTRO and
        # mark_plates_intro_shown. None on every week after the first one
        # where the app actually completed a plate, and None immediately if
        # it never has. Deliberately NOT folded into `headline`, which says
        # at most two things by design (see _week_headline) and would grow a
        # sentence per feature if this were the third.
        # Asked of the ROWS rather than of `days`, because `days` only
        # carries breakfast/lunch/dinner — a side attached to a snack is
        # still a side the household paid for and is owed the explanation
        # about.
        "plates_note": (
            PLATES_INTRO
            if (not (prefs["plates_intro_shown_at"] if prefs else "")
                and any(_plate_sides(r["sides_json"]) for r in rows))
            else None
        ),
        # The trip banner ("Away Sat–Sun") — present only when the week
        # actually has one, so the ordinary week carries no extra chrome.
        "trip_summary": trip,
        # The approved receipt's counts and its two lines (week_receipt).
        # None while the week is a draft: a receipt is what you get for
        # having decided, and a draft hasn't.
        "receipt": (
            week_receipt(days, plan["weekly_plan_id"], today=today_str)
            if plan["status"] == "approved" else None
        ),
        # The stretch "Plan next week ›" offers, and why it is the length
        # it is — see next_period_after.
        "next_period": next_period,
        **approval,
    }


def _safe_draft_opener(rows, intake, plan, days) -> list[str]:
    from . import draft_opener as _draft_opener  # lazy, see get_week_menu

    try:
        from . import memory as _memory  # lazy, as above
        return _draft_opener.build_opener(
            rows, intake, plan["period_start_date"], plan["day_count"], days, plan_id=plan["weekly_plan_id"],
            report=plan_requests(plan["weekly_plan_id"]),
            memory=_memory.get_household_memory(),
        )
    except Exception:
        logger.exception("The draft's opening lines could not be built")
        return []


def _decorate_with_holidays(days: list[dict]) -> None:
    """
    The quiet label on a holiday's row — "Thanksgiving · going to someone’s"
    (Loop Board "Holidays: Pomona knows 12 October is coming...", 2026-09-11).
    Only a day that IS a holiday gets the key, so an ordinary week's payload
    is byte-for-byte what it was. Never fails the screen: a calendar feed
    that can't be read is a missing label, not a missing week.
    """
    from . import holidays as _holidays

    try:
        found = {h["date"]: h for h in _holidays.holidays_for_dates([d["date"] for d in days])}
    except Exception:
        logger.exception("Holiday labels could not be built for the week menu")
        return
    for day in days:
        h = found.get(day["date"])
        if h:
            day["holiday"] = {
                "name": h["name"],
                "answer": h["answer"]["answer"] if h["answer"] else None,
                "asks": h["asks"],
                "label": _holidays.holiday_day_label(h),
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
         slots that still needs one. Two shapes of "needs one":

         - No entry at all for that date/slot. Comes with up to two
           quick-recipe suggestions (see _suggest_quick_dinners) so the
           card's "Pick" rows have something real to offer — the card is
           omitted entirely if there isn't even one recipe saved yet,
           since a decision card with nothing to pick is worse than no
           card.
         - An 'open' slot — a decision the app already handed back on the
           Plan screen (see plan_slot_open/resolve_open_slot), still
           unsettled. "core loop handoffs, slice 2" item D (Emily,
           2026-09-05): this used to be silently swallowed by the
           "there's already a row for that date" check below, so an open
           dinner never surfaced here even though it is, by definition,
           exactly the kind of thing this band exists for. It carries the
           slot's own options (open_options: label/meta, from
           derived_from_json) and its open_reason as the body, and
           resolves through the same path the Plan screen's open-slot
           cards already use (resolve_open_slot / POST
           /api/week/{week_start}/slot) rather than plan_meal — plan_meal
           only inserts, so calling it here would leave the old open row
           behind as a second, orphaned entry for the same date/slot.

         A 'planned_empty' slot (the household said it's away) or an
         ordinary 'planned' one both count as handled — nothing to surface
         for either.
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
    # The household's today, not the server's: this card says "Tonight" and
    # carries the date the client posts straight back, and Now's timeline
    # reads the household's day. The two have to be the same day or the
    # answered dinner lands where nothing is looking. See _household_today.
    today = _household_today()
    horizon_end = today + timedelta(days=2)  # today, tomorrow, day-after exclusive edge -> "within 48h" covers today+tomorrow

    items: list[dict] = []

    # ---- Rule 0: a holiday close enough to ask about ----
    # Loop Board "Holidays: Pomona knows 12 October is coming and asks how
    # you're spending it": within three days of a holiday that still has
    # no real answer (none, or "not sure yet"), Now asks — at most once a
    # day. See holidays.holiday_needs_you_item. First, not because it
    # outranks tonight's dinner but because the answer changes what that
    # dinner even is.
    try:
        from . import holidays as _holidays
        holiday_ask = _holidays.holiday_needs_you_item(today)
        if holiday_ask:
            items.append(holiday_ask)
    except Exception:
        logger.exception("Holiday ask could not be built for the needs-you band")

    # ---- Rule 1: dinner decision ----
    # Ordered so the APPROVED plan's row is the last one seen for a date
    # and wins the dict below: a draft may sit over the approved week until
    # it is approved (2026-09-13), and Now follows the real week, not the
    # draft — a draft's open Thursday is not tonight's decision, and the
    # approved week's open Thursday still is.
    dinner_rows = conn.execute(
        "SELECT mpe.date, mpe.slot_state, mpe.open_reason, mpe.derived_from_json, mpe.weekly_plan_id "
        "FROM meal_plan_entries mpe LEFT JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        "WHERE mpe.household_id = ? AND mpe.slot = 'dinner' AND mpe.date >= ? AND mpe.date < ? "
        "AND (wp.id IS NULL OR wp.status != 'retired') "
        # COALESCE: a row with no plan at all (a dinner planned on its own)
        # compares as NULL, which would sort ahead of everything; it ties
        # with a draft instead and the newer row wins, as it always did.
        f"ORDER BY COALESCE(wp.status = 'approved', 0) ASC, mpe.id ASC",
        (household_id(), today.isoformat(), horizon_end.isoformat()),
    ).fetchall()
    dinner_by_date = {r["date"]: r for r in dinner_rows}
    for offset in (0, 1):
        candidate = (today + timedelta(days=offset)).isoformat()
        when = "Tonight" if offset == 0 else "Tomorrow"
        row = dinner_by_date.get(candidate)

        if row is not None and row["slot_state"] == "open":
            derived = json.loads(row["derived_from_json"] or "{}")
            week_start = None
            if row["weekly_plan_id"] is not None:
                plan_row = conn.execute(
                    "SELECT week_start_date FROM weekly_plans WHERE id = ?", (row["weekly_plan_id"],)
                ).fetchone()
                week_start = plan_row["week_start_date"] if plan_row else None
            items.append({
                "type": "dinner_open",
                "kicker": "DINNER",
                "title": when + "’s dinner needs your call",
                "urgency": "urgent",
                "date": candidate,
                "slot": "dinner",
                "body": row["open_reason"] or "",
                "options": derived.get("options") or [],
                "week_start": week_start,
                # The plan this card is about, by id: a week key alone
                # resolves to the newest plan filed under it, which is the
                # DRAFT when one sits over this week (2026-09-13). The pick
                # has to land on the row the card was built from.
                "weekly_plan_id": row["weekly_plan_id"],
            })
            break  # only the soonest unsettled dinner becomes a card

        if row is not None:
            continue  # planned, or deliberately away — already handled

        options = _suggest_quick_dinners()
        if not options:
            break  # no recipes to suggest at all -- nothing later in the loop will differ, so stop
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
    thin wrapper around plan_meal that also attaches it to the weekly plan
    that covers that day (if one does) so it shows up correctly in the
    Week tab's menu, then returns the refreshed needs-you list so the
    Today screen can just re-render from the response.

    add_ingredients_to_grocery_list carries the answer the card's confirm
    step collected. It is a real question asked of a real person, which is
    what makes this an explicit yes and not a silent write — the same
    standard chat is held to (see plan_meal). It defaults to False so a
    caller that forgets to ask adds nothing.

    The plan is resolved BY THE MEAL'S DAY, not by "which plan is current".
    Bug, 2026-09-11: attaching meal_date to a plan whose period doesn't
    cover it made plan_meal's own period check reject the insert, 500ing a
    tap on the card get_needs_you_items had just offered. That was first
    fixed by asking _current_weekly_plan_row for "the" plan and then
    dropping the link when its period missed the date — right, but decided
    partly by the SERVER's today, which is a different day from the
    household's for four hours every evening (see _household_today): at a
    period boundary in that window the covering plan was resolved as "not
    current" and the link dropped for no reason the household could see.
    get_plan_id_for_date is the app's own answer to "which plan does this
    day belong to", it reads no clock at all, and it returns None for a day
    no live plan covers — in which case plan_meal still saves the meal,
    just with no plan link, the same shape a one-off chat request already
    gets and the shape unplanned_meals_ahead exists to keep visible.
    """
    weekly_plan_id = get_plan_id_for_date(meal_date)
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

    `sides_json` rides along because a side the app attached to complete a
    plate is part of THAT MEAL's shopping, not a meal of its own (see
    plates.py). Both callers read the combined list through
    _entry_shopping_ingredients below, so the number promised and the
    number delivered still come from one place.
    """
    return conn.execute(
        """
        SELECT mpe.id, mpe.recipe_id, r.ingredients_json, r.default_servings, mpe.sides_json
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


# What the household is told, once, the first time the app rounds a meal
# out for them. Emily, 2026-09-05: they should hear that this is on purpose
# and that they can stop it. DESIGN_SYSTEM.md §8 — state the thing, then the
# way out, in that order and at that length. Not cheery, not an apology, and
# it names the reason rather than hiding behind "for balance".
PLATES_INTRO = (
    "Where a meal came out short, I added a small side — I’m thinking about how you’re eating. "
    "If you’d rather I left them alone, tell me and I’ll stop."
)


def mark_plates_intro_shown() -> dict:
    """
    Record that the household has now been told (see PLATES_INTRO).

    Called by the /api/week-menu ROUTE, not by get_week_menu itself, and
    that split is the whole point: get_week_menu is also a read the
    assistant makes on the household's behalf mid-conversation, and burning
    a once-in-a-lifetime sentence on a tool call nobody saw would mean the
    household never gets told at all. The screen fetch is the one caller
    that can honestly claim the sentence was delivered.

    Idempotent: the first stamp wins, so a second screen fetch racing the
    first doesn't rewrite the date.
    """
    conn = get_conn()
    conn.execute(
        "INSERT INTO meal_preferences (household_id, plates_intro_shown_at, updated_at) "
        "VALUES (?, datetime('now'), datetime('now')) "
        "ON CONFLICT(household_id) DO UPDATE SET "
        "plates_intro_shown_at = CASE WHEN plates_intro_shown_at = '' "
        "THEN datetime('now') ELSE plates_intro_shown_at END",
        (household_id(),),
    )
    conn.commit()
    conn.close()
    return {"shown": True}


def _plate_sides(sides_json: str | None) -> list[dict]:
    """A row's sides_json as a list, tolerating anything stored badly."""
    try:
        sides = json.loads(sides_json or "[]")
    except (TypeError, ValueError):
        return []
    return sides if isinstance(sides, list) else []


def _ingest_recipe_group_and_sides(
    entries: list, weekly_plan_id: int | None, buffer: "_recipes.WeekGroceryBuffer"
) -> dict:
    """
    Put every one of `entries` (each exposing id/recipe_id/ingredients_json/
    default_servings/sides_json) on the grocery list through ONE shared
    buffer: grouped by recipe first — so a recipe cooked several nights
    this pass still buys as one recipe-week, not one line per meal — then
    each entry's own side, riding the same buffer so a side sharing an
    ingredient with a recipe (or another side) rounds together with it
    instead of separately. Does NOT flush the buffer; the caller owns
    that, since the entire point of sharing one is letting it hold more
    than one call's worth of lines before anything gets rounded.

    Shared by _reingest_unlinked_entries (every never-bought entry in a
    plan) and _rescale_leftover_source_grocery (one recipe's worth,
    replayed after a leftover source's confirmed batch changes size), so
    the recipe-grouping-plus-sides shape approve_weekly_plan defines for a
    first approval lives in one place rather than drifting across copies.

    The buffer carries the connection, when there is one: a buffer built
    with `conn=` (inside swap_meal_in_plan's transaction) puts every read
    and write of this ingest on it — see
    recipes._add_recipe_ingredients_for_entries.
    """
    by_recipe: dict[int, dict] = {}
    for entry in entries:
        group = by_recipe.setdefault(
            entry["recipe_id"], {
                "ingredients_json": entry["ingredients_json"],
                "default_servings": entry["default_servings"],
                "entry_ids": [],
            },
        )
        group["entry_ids"].append(entry["id"])

    added_items: list[str] = []
    already_have: list[str] = []
    for group in by_recipe.values():
        added, have = _recipes._add_recipe_ingredients_for_entries(
            group["entry_ids"], json.loads(group["ingredients_json"]), weekly_plan_id,
            default_servings=group["default_servings"], buffer=buffer,
        )
        added_items.extend(added)
        already_have.extend(have)

    for entry in entries:
        for side_ingredients, side_servings, cooked_on_the_night in _entry_side_groups(entry):
            added, have = _recipes._add_recipe_ingredients_for_entries(
                [entry["id"]], side_ingredients, weekly_plan_id,
                default_servings=side_servings, buffer=buffer,
                # A big-meal dish belongs to the holiday table alone: a
                # reheat night buys nothing new for it, and the cook
                # night's batch is the main rather than the stuffing.
                # Every other side is cooked on the night it sits on, so
                # it follows its dish through the chain on a cook night
                # AND is bought on a reheat night, where it is a different
                # dish made that evening (_entry_side_groups decides
                # which is which).
                chain_scale=cooked_on_the_night,
                reheat_buys_it=cooked_on_the_night,
            )
            added_items.extend(added)
            already_have.extend(have)
    return {"groceries_added": added_items, "already_have_skipped": already_have}


def _reingest_unlinked_entries(weekly_plan_id: int, conn=None) -> dict:
    """
    Buy, for the first time, whatever this approved plan's entries have
    never actually contributed to the grocery list — swap_meal_in_plan's
    fix for the leftover-chain-swap gap its own docstring describes.

    Deliberately general rather than a special case for the chain it was
    written for: it finds every entry with a real recipe and no
    meal_plan_grocery_links row yet (_plan_grocery_candidate_entries, the
    same query approve_weekly_plan and preview_plan_grocery_impact already
    trust for "what hasn't been bought"), then runs them through
    _ingest_recipe_group_and_sides with one WeekGroceryBuffer for the
    whole pass, so amounts that land on the same line still consolidate
    and round together rather than each being bought — and rounded — on
    its own.

    `conn` is for swap_meal_in_plan (through _replace_slot_entries), which
    runs this inside its one transaction; given a connection nothing here
    commits or closes. Left unset it behaves exactly as before.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    entries = _plan_grocery_candidate_entries(conn, weekly_plan_id)
    if own_conn:
        conn.close()
    if not entries:
        return {"groceries_added": [], "already_have_skipped": []}

    buffer = _recipes.WeekGroceryBuffer(weekly_plan_id, conn=None if own_conn else conn)
    result = _ingest_recipe_group_and_sides(entries, weekly_plan_id, buffer)
    buffer.flush()
    return result


def _entry_shopping_ingredients(row) -> list[dict]:
    """
    Everything one plan entry puts on the shopping list: its recipe's own
    ingredients, then any side the app attached to complete its plate.

    Recorded against the SAME meal_plan_entry_id as the dish, which is what
    makes removing the meal remove its side's shopping too —
    _reverse_meal_grocery_contributions is keyed by entry, so the side needs
    no unwinding logic of its own.
    """
    ingredients = json.loads(row["ingredients_json"] or "[]")
    return ingredients + _entry_side_ingredients(row)


def _entry_side_ingredients(row) -> list[dict]:
    """Just the side's ingredients for one plan entry ('[]' when none)."""
    return _plates.side_ingredients(_entry_sides(row))


def _entry_sides(row) -> list[dict]:
    sides = row["sides_json"] if "sides_json" in row.keys() else "[]"
    try:
        parsed = json.loads(sides or "[]")
    except (TypeError, ValueError):
        parsed = []
    return parsed if isinstance(parsed, list) else []


def _entry_side_groups(row) -> list[tuple[list[dict], int | None, bool]]:
    """
    One entry's sides for the grocery ingest, as (ingredients, servings,
    cooked_on_the_night) groups — one group per distinct (servings,
    big-meal) pair, order preserved, empty groups dropped (an entry with
    no sides yields []).

    `servings` is what the side was written for, and anchors the ingest
    the way a recipe's default_servings does: a side the household added
    from the meal screen carries `servings` (plates.ADDITION_SERVINGS)
    and scales to the night's eaters like a recipe; a side the plate pass
    attached carries none and rides on attendance alone, exactly as
    before; a dish on a hosted holiday's big meal carries the table it
    was written for (app/tools/big_meal.py), so a stuffing written for
    seven isn't bought three and a half times over.

    `cooked_on_the_night` is False for a big-meal dish only — the one
    kind of side that belongs to the holiday table alone rather than to
    the night it sits on (plates.is_big_meal_dish, which is where that
    one-line rule lives). It answers both grocery questions a leftover
    chain asks of a side, and the two really are one question:

    - on the COOK night, does the batch scale it? A household or plate
      side is cooked alongside the doubled dish and covers the night it
      feeds, so yes; a stuffing written for the holiday table is already
      sized for that table, so no.
    - on a REHEAT night, is it bought at all? A green salad beside
      Thursday's leftovers is a different dish, cooked that evening, and
      nothing else on the week buys it — so yes, at that night's own
      headcount (batch_for_entry finds no batch on a reheat, so the
      chain factor is a no-op there — but that is true of today's DATA
      and not enforced; see the note beside batch_for_entry in
      recipes._add_recipe_ingredients_for_entries for the two shapes
      that would make it false). A big-meal dish is the holiday
      table's, so still no.

    Before 2026-09-25 the second question was never asked: the ingest
    dropped every leftovers entry whatever it was buying, so a side on a
    reheat night was silently skipped and the household was shown a
    plate with no lettuce on the list for it.
    """
    groups: dict[tuple[int | None, bool], list[dict]] = {}
    for side in _entry_sides(row):
        if not isinstance(side, dict):
            continue
        servings = side.get("servings")
        try:
            servings = int(servings) if servings else None
        except (TypeError, ValueError):
            servings = None
        if servings is not None and servings <= 0:
            servings = None
        big_meal_dish = _plates.is_big_meal_dish(side)
        groups.setdefault((servings, big_meal_dish), []).extend(_plates.side_ingredients([side]))
    return [(ings, servings, not big) for (servings, big), ings in groups.items() if ings]


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

    IT NO LONGER MIRRORS _add_recipe_ingredients_to_grocery_list EXACTLY,
    and the docstring said it did until 2026-09-14. The first rule still
    holds — entries that already contributed are skipped. The second does
    not: this counts an ingredient as already-in-the-kitchen when its NAME
    is tracked with a real quantity, which is the question the ingest
    stopped asking when recipes._KitchenStock landed (a name with two
    ounces behind it is not an answer to a two-pound line). So the promise
    can over-count what is already at home and under-count what will be
    bought, and the count it names can be smaller than the list that
    arrives.

    Left as it is deliberately, and it is the parent branch's debt rather
    than something this function did wrong: it works over DISTINCT
    ingredient NAMES with no scaling at all, so asking _KitchenStock here
    means computing each recipe-week's whole scaled claim the way the
    ingest does — pack shares, chain scaling, attendance — which is a
    rewrite of this function, not a gate on it. Its own card. It only
    mis-states a promise; nothing here buys or skips.

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
        for ing in _entry_shopping_ingredients(entry):
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


# Shown above Approve when the allergy check itself could not run. Calm and
# plain (DESIGN_SYSTEM §8: safety copy states the thing and its way out).
_CHECK_FAILED_NOTE = (
    "I couldn't check this week against your household's allergies just now. "
    "Approve anyway, or try again in a moment."
)


def _write_pending_recipes(weekly_plan_id: int) -> None:
    """
    Approval's first move since 2026-09-21: any dish the menu pass left
    unwritten gets its ingredients and steps now, before the grocery list
    is built from them and before the allergy check reads them. Imported
    at call time, not import time: agent imports this package, and the
    same convention as plates.complete_plate keeps the cycle from being
    real. Never raises — a recipe that could not be written stays pending
    (the Cook screen fills it when it's needed) and the approval goes
    ahead with what it has; a lost approval over one recipe is the worse
    outcome.
    """
    try:
        from .. import agent as _agent
        _agent.fill_pending_recipes_for_plan(weekly_plan_id)
    except Exception:
        logger.exception("Writing the pending recipes for plan %s failed; approving with what is written", weekly_plan_id)


def approve_weekly_plan(
    weekly_plan_id: int, approved_by: str = "", confirm_hard_conflicts: bool = False
) -> dict:
    """
    Approve a weekly plan — and, in the same step, put its meals'
    ingredients on the grocery list.

    `approved_by` is an adult's name (see schema.sql on
    weekly_plans.approved_by). Leave it blank and it is the adult picked on
    this device (slice 1 of per-adult login — see _shared.acting_name);
    name someone and that name is kept. It's recorded with the approval
    time so the Meals screen can render the receipt the design calls for —
    "APPROVED BY EMILY · 9:41AM" — and so the OTHER adult can be told who
    settled the week (get_active_notifications #4 hides that item from the
    approver). When the name is the session's own adult, their member id is
    stored beside it (approved_by_member_id), so "not the approver" is a
    fact and not a name comparison. An approval with no name anywhere still
    approves, and the receipt just drops the name rather than inventing one.

    Approving used to only flip a status flag; the grocery list had
    already been filled in during generation, whether or not the household
    ever agreed to that plan. Ingredients from drafts that were changed,
    abandoned or never approved piled up on the real shopping list as a
    result. Now generation adds nothing (see plan_meal, whose
    add_ingredients_to_grocery_list defaults to False) and approval is
    what populates the list, so the list only ever reflects a week the
    household actually said yes to.

    Safe to call more than once, AND safe to call twice at once. Two
    separate guards, because they cover different things:

    - Re-approving an ALREADY-approved plan adds nothing at all — with one
      exception: if every line the first approval put on the list is gone
      (the household wiped it with "Start over" / clear_grocery_list), the
      re-approval REBUILDS the list from the same week and says so
      (`list_rebuilt: True`). A plain no-op re-approval reports the live
      `list_needed_count` and a `note` to relay instead of the old count.
      Otherwise the grocery work happens on the transition into
      'approved', not on every call. Without this, an entry whose ingredients were all skipped as
      already-in-the-pantry leaves no trace that it was ever considered
      (the ledger only records what was actually added), so a later
      re-approve would add them for real once the pantry had emptied — a
      surprise write to the list nobody asked for.
    - Within a single approval, an entry whose contributions are already
      recorded in meal_plan_grocery_links is skipped, so a plan whose
      meals already put their ingredients on the list another way (a swap,
      or plan_meal called with the flag) doesn't double up its quantities.

    Both guards used to be READ on one connection and then acted on by a
    later, separate write — so two adults tapping Approve at the same
    instant could both read "not approved yet" before either had written
    anything, and both would go on to ingest the week's groceries once
    each. Measured at a 0ms gap between two threaded calls on a seeded
    database: 6/6 trials doubled the grocery lines (and the
    set_aside_carried_over_items carry-over and the approval receipt with
    them); 0/6 at a 20ms gap, because by then the first call had already
    committed. Same class of bug this repo has already closed three times
    — atomic-period-takeover, swap-atomic, drop-dish-atomic — and the same
    shape: the status flip into 'approved' is now a single conditional
    UPDATE (`WHERE status != 'approved'`), and everything the transition
    does — the carry-over set-aside, the recipe-week grocery ingest, the
    grocery-count receipt — runs inside the SAME transaction as that flip,
    on one connection, with an explicit `BEGIN IMMEDIATE` so the write
    lock is held from the first statement rather than sqlite3's default of
    only the first write. A caller that loses the race (0 rows flipped,
    because the plan was already approved when this call started, or
    another call approved it in the gap since) rolls back having written
    nothing and returns the exact same "was_already_approved" shape a
    second honest call always has.

    Raises ValueError for a weekly_plan_id that doesn't exist, rather than
    reporting a cheerful approval of nothing — same as clear_weekly_plan
    and swap_component_in_plan.

    The returned `conflicts`/`conflicts_note` are the dietary/allergy check
    (check_plan_conflicts) run automatically on the way through. A SOFT one
    (a standing dislike) never blocks — the household may well mean it —
    but is still said out loud rather than left to whether anyone thought
    to ask. Mention any that come back when reporting the approval.

    A HARD one (an allergy/must-avoid, member restriction or hard fact) is
    different: unless `confirm_hard_conflicts` is true, this does NOT
    approve — it writes nothing at all — and instead returns
    `{"status": "needs_confirmation", "conflicts", "conflicts_note",
    "weekly_plan_id"}`. That is the household's explicit "I've seen it and
    I still want this" tap, not something to pass as true on your own
    initiative — ask first, every time (see app/agent.py's tool
    description). Re-approving an already-approved plan is exempt: the
    decision was already made, so it takes the guard's other branch below
    (adds nothing, asks nothing) rather than this one.

    This confirmation gate, and the conflict check behind it, run BEFORE
    the atomic transaction below and off a plain, un-locked read of the
    plan's status — deliberately, the same call the takeover/swap/drop
    fixes made: `check_plan_conflicts` does real work (reading every
    member and fact) and nothing that slow belongs inside a write lock. A
    plan that is concurrently approved by someone else in the gap between
    that read and the transaction is caught correctly where it matters —
    the transaction's own conditional UPDATE still bails and nothing is
    double-written — but in the one-in-a-million case where a hard
    conflict exists AND two approvals land in that exact gap, the loser
    can see one extra "needs_confirmation" round-trip for a week that was,
    by the time it asked, already approved. Confirming past it is still
    harmless: the guard below simply finds nothing left to do.
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
    conn.close()

    # The recipes first, then everything that reads them: the allergy check
    # below and the grocery ingest inside the transaction both work from
    # ingredients, and a dish the menu pass chose has none until this
    # writes them. Skipped for a re-approval, which adds nothing anyway.
    if not was_already_approved:
        _write_pending_recipes(weekly_plan_id)

    # Run before the approval work, so the warning (and the confirmation
    # gate just below) describe the plan that was actually approved, and a
    # failure here can't half-approve a week.
    conflicts, note = [], None
    check_failed = False
    try:
        found = _coordination.check_plan_conflicts(weekly_plan_id)
        conflicts = found["conflicts"]
        note = found["note"]
    except Exception:
        # Logged, not swallowed: the gate below treats a check that could
        # not run the same as a check that found something. Approving past
        # an allergy because the allergy check crashed is exactly the
        # failure this confirm exists to prevent.
        logger.exception("Conflict check failed for plan %s", weekly_plan_id)
        check_failed = True

    # A check that CRASHED fails closed: the household is asked rather than
    # waved through, because "we couldn't look" is not "nothing was found".
    # The note says so plainly and the flag still approves — a broken check
    # must not lock a household out of its week.
    if (
        not was_already_approved
        and not confirm_hard_conflicts
        and (check_failed or any(c["severity"] == "hard" for c in conflicts))
    ):
        # `note` here, not conflicts_note_after_approval: nothing has been
        # approved, so the sentence should still say "before you approve" —
        # the same wording the draft's own review-band warning uses.
        return {
            "weekly_plan_id": weekly_plan_id,
            "status": "needs_confirmation",
            "conflicts": conflicts,
            "conflicts_note": _CHECK_FAILED_NOTE if check_failed else note,
            "check_failed": check_failed,
        }

    # Not `note`: that sentence ends "before you approve", and this is the
    # moment just after (or, for a hard clash, the moment the household
    # confirmed past it). Same clash, worded for a decision already made —
    # see conflicts_note_after_approval.
    conflicts_note = _coordination.conflicts_note_after_approval(conflicts)
    approved_by = acting_name(approved_by)
    # Resolved here, not inside the transaction below: acting_member_id_for
    # calls current_member(), which opens its OWN connection (a local
    # `from ..db import get_conn` inside _shared.py, invisible to anything
    # that watches this module's own get_conn) to read the members table.
    # Called as a bare argument to the UPDATE it would run AFTER BEGIN
    # IMMEDIATE — a second connection reading while the first holds the
    # write lock. Harmless in practice (a read, not a write, so it cannot
    # deadlock against the RESERVED lock) but it breaks the one-connection
    # invariant every atomic write in this file otherwise holds to exactly,
    # so it is computed out here instead, exactly where `approved_by`
    # itself already is.
    approved_by_member_id = acting_member_id_for(approved_by)
    result = _settle_weekly_plan_approval(weekly_plan_id, approved_by, approved_by_member_id, conflicts, conflicts_note)

    # A hosted holiday's big meal on this plan gets its prep spread now —
    # approval is the moment the week becomes real (app/tools/big_meal.py).
    # After the settle, not inside it: spread_prep opens connections of its
    # own, and the settle holds the write lock until it commits. Only when
    # the yes did something — a genuine approval, or a rebuild of a wiped
    # list; a no-op re-approval leaves the prep exactly as it is.
    if not result["was_already_approved"] or result["list_rebuilt"]:
        from . import big_meal as _big_meal
        _big_meal.spread_prep_for_plan(weekly_plan_id)
    # Batch cooking is assumed from prep days (2026-09-18): a dish on more
    # than one day is cooked once, on its first day, when the household
    # preps ahead — the chain the old approval-time ask wrote on a "yes".
    # Only on a genuine approval, and after the settle for the same reason
    # spread_prep runs after it (its own connections; the settle holds the
    # write lock until it commits). It never raises past here: a batch
    # that could not be written is not a reason to un-approve the week.
    if not result["was_already_approved"]:
        from . import cook_ahead as _cook_ahead
        try:
            result["cook_ahead"] = _cook_ahead.apply_prep_day_batches(weekly_plan_id)
        except Exception:
            logger.exception("Batching repeated dishes at approval failed for plan %s", weekly_plan_id)
    return result


def _settle_weekly_plan_approval(
    weekly_plan_id: int, approved_by: str, approved_by_member_id: int | None,
    conflicts: list[dict], conflicts_note: str | None,
) -> dict:
    """
    The write behind a genuine transition into 'approved' — one connection,
    one commit, the shape atomic-period-takeover/swap-atomic/
    drop-dish-atomic all used. The write lock is taken FIRST, with an
    explicit BEGIN IMMEDIATE, not left to sqlite3's default of opening a
    transaction implicitly at the first write: without it, the status flip
    below and the grocery ingest after it were two separate implicit
    transactions on the same connection (the flip committed on its own),
    which is exactly the gap two simultaneous approvals raced through.
    From the BEGIN on, a second caller blocks until this one commits or
    rolls back, and sees THIS call's result when it resumes — not a second
    helping of groceries.

    `conflicts`/`conflicts_note`/`approved_by_member_id` ride straight
    through from approve_weekly_plan, computed before this opens (see its
    docstring for why each stays outside the lock): they are reported back
    or written unchanged, never re-derived here — nothing in this function
    reads anything but weekly_plans/meal_plan_entries/grocery_items/
    meal_plan_grocery_links, on this one connection.
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # THE GUARD. Re-approving an ALREADY-approved plan, and two
        # approvals racing each other, are now the same case: the flip
        # only succeeds for a genuine draft/reopened -> approved
        # transition, so a plan that is already 'approved' — whether it
        # was when this call started, or became so in the gap since,
        # under another call's lock — leaves this UPDATE at 0 rows and
        # nothing below it ever runs. A re-approval never overwrites the
        # original approver/time this way either: the receipt names who
        # actually settled the week, and the first yes is the one that
        # built the list.
        flipped = conn.execute(
            "UPDATE weekly_plans SET status = 'approved', updated_at = datetime('now'), "
            "approved_by = ?, approved_by_member_id = ?, approved_at = datetime('now') "
            "WHERE id = ? AND household_id = ? AND status != 'approved'",
            (approved_by.strip(), approved_by_member_id, weekly_plan_id, household_id()),
        ).rowcount
        # A wiped list is the one re-approval that should NOT be a no-op.
        # "Start over" (main.py's /api/reset, tools.clear_grocery_list)
        # deletes every needed line and leaves the week approved, so the
        # next "approve it" / "build my list" used to hit the guard above,
        # add nothing, and hand back the ORIGINAL count — the chat then
        # said "nothing new to add" over a list holding one jar of
        # allspice (Emily, 2026-09-13: "the chat is obviously not reading
        # the shop list"). The signal is narrow on purpose: the first
        # approval put lines on the list, and not one of them is left in a
        # LIVE state — needed, in a cart, bought, or carried to next week.
        # A list with even one such line (a bag already bought, a line
        # still waiting) is a list the household is still working from,
        # and re-approving it stays a no-op exactly as before. Two kinds
        # of row are bookkeeping rather than list: a recipe's spice waiting
        # unticked in its own section (spices.py), and a line taken off
        # with "have it" / "not this trip" ('removed'). Both survive a
        # clear, both still carry the meal's ledger link — and that link
        # is what _plan_grocery_candidate_entries uses to skip a meal as
        # already ingested. So a rebuild deletes them first (the links go
        # with them, schema.sql's ON DELETE CASCADE), and every meal is put
        # on the list again the way the first approval did it — spices
        # back in their section, and a "have it" line back on the list
        # unless the pantry still says it is there (the ingest's own
        # already-have skip). The verifier's case (2026-09-13): a recipe
        # with allspice in it rebuilt NOTHING under a status != 'spice'
        # test, reported list_rebuilt anyway, and wrote 0 to the receipt —
        # which locked the week out of ever rebuilding again.
        list_wiped = False
        receipt = None
        if flipped == 0:
            receipt = conn.execute(
                "SELECT approved_by, approved_at, approved_grocery_added, approved_grocery_skipped "
                "FROM weekly_plans WHERE id = ? AND household_id = ?",
                (weekly_plan_id, household_id()),
            ).fetchone()
            # "This plan's lines" is the ledger's answer, not just the
            # source id: an ingredient that merged onto a standing,
            # hand-added line keeps source NULL by design (grocery.py's
            # keep_standing) but is linked to the meal all the same, and
            # that line surviving the clear — bought, or "have it" — is
            # the household still working from the list (verifier,
            # 2026-09-13, second pass).
            live_lines_left = conn.execute(
                "SELECT COUNT(*) FROM grocery_items g WHERE g.household_id = ? "
                "AND g.status IN ('needed', 'in_cart', 'purchased', 'carried') "
                "AND (g.source_weekly_plan_id = ? OR g.id IN ("
                "  SELECT l.grocery_item_id FROM meal_plan_grocery_links l "
                "  JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
                "  WHERE e.weekly_plan_id = ? AND e.household_id = g.household_id))",
                (household_id(), weekly_plan_id, weekly_plan_id),
            ).fetchone()[0]
            list_wiped = bool(receipt and (receipt["approved_grocery_added"] or 0) > 0 and live_lines_left == 0)
            if list_wiped:
                conn.execute(
                    "DELETE FROM grocery_items WHERE household_id = ? AND source_weekly_plan_id = ? "
                    "AND status IN ('spice', 'removed')",
                    (household_id(), weekly_plan_id),
                )
                # And every remaining link this plan's meals hold (a
                # standing line's, a removed line's) — the link is what
                # hides a meal from the ingest, and with nothing live left
                # there is nothing for it to protect.
                conn.execute(
                    "DELETE FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id IN ("
                    "  SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?)",
                    (household_id(), weekly_plan_id, household_id()),
                )
        if flipped == 0 and not list_wiped:
            conn.rollback()
            # What the list holds right now, so the assistant can say so
            # instead of repeating the receipt as if it were news.
            list_needed_count = conn.execute(
                "SELECT COUNT(*) FROM grocery_items WHERE household_id = ? "
                "AND status = 'needed' AND excluded_from_list = 0",
                (household_id(),),
            ).fetchone()[0]
            result = {
                "weekly_plan_id": weekly_plan_id,
                "status": "approved",
                "groceries_added": [],
                "already_have_skipped": [],
                # The counts stay the ORIGINAL approval's — this call added
                # nothing, and the receipt still describes the yes that
                # built the list.
                "groceries_added_count": receipt["approved_grocery_added"] if receipt else 0,
                "already_have_skipped_count": receipt["approved_grocery_skipped"] if receipt else 0,
                "was_already_approved": True,
                "list_rebuilt": False,
                "list_needed_count": list_needed_count,
                "note": (
                    "This week was already approved, so nothing was added this time. "
                    f"The shopping list currently has {list_needed_count} item"
                    f"{'' if list_needed_count == 1 else 's'} to buy."
                ),
                "approved_by": receipt["approved_by"] if receipt else "",
                "approved_at": receipt["approved_at"] if receipt else None,
                "conflicts": conflicts,
                "conflicts_note": conflicts_note,
                "carried_over": [],
                "carried_over_count": 0,
            }
        else:
            # THE takeover (Emily, 2026-09-13: a draft waits until approval).
            # Any other plan holding a day of this one gives it up now —
            # meals, and their groceries back off the list — in this same
            # transaction, before this plan's own groceries go on. A draft's
            # overlap with other drafts was already settled at generation;
            # what is left to settle here is the approved week this draft
            # was drafted over. Only on a real transition: a re-approval
            # (flipped == 0, list rebuild) has nothing left to take.
            took_over = {"retired_plan_ids": [], "shortened_plan_ids": [], "surrendered_dates": []}
            if flipped:
                plan_row = conn.execute(
                    "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
                    (weekly_plan_id, household_id()),
                ).fetchone()
                period_start, period_days = plan_period(plan_row)
                took_over = retire_overlapping_plans(weekly_plan_id, period_start, period_days, conn=conn)
            entries = _plan_grocery_candidate_entries(conn, weekly_plan_id)
            approved_at = conn.execute(
                "SELECT approved_at FROM weekly_plans WHERE id = ? AND household_id = ?",
                (weekly_plan_id, household_id()),
            ).fetchone()["approved_at"]

            # Before a single ingredient lands: whatever is still unbought
            # from an EARLIER week is set aside, so this week's amounts go
            # on clean lines and the Shop tab can ask "still on the list
            # from last week — keep or drop?" instead of the two weeks
            # silently summing into one number. See
            # grocery.set_aside_carried_over_items for what counts. `conn`
            # rides through so this joins the same transaction rather than
            # committing on its own ahead of the ingest below.
            carried_over = _grocery.set_aside_carried_over_items(weekly_plan_id, conn=conn)

            # Grouped by RECIPE, not left one row per meal. A week's shop is
            # a recipe-week question: the same breakfast six mornings needs
            # one bag of spinach, not six, and only something that looks at
            # all six meals at once can know that. Ingesting per meal is
            # what put 6 bags of spinach and 4 bottles of honey on Emily's
            # first approved week — every downstream step was working
            # correctly on wrong inputs. See _add_recipe_ingredients_for_entries
            # for which ingredients stop multiplying and which (rightly)
            # still add up.
            by_recipe: dict[int, dict] = {}
            for entry in entries:
                group = by_recipe.setdefault(
                    entry["recipe_id"], {
                        "ingredients_json": entry["ingredients_json"],
                        "default_servings": entry["default_servings"],
                        "entry_ids": [],
                    },
                )
                group["entry_ids"].append(entry["id"])

            # One buffer for the WHOLE approval, not one per recipe.
            # Grouping by recipe is the right unit for a sealed package
            # (six breakfasts of the same dish, one bag of spinach) but the
            # wrong one for rounding a per-portion amount: Emily's 17
            # peppers came from five DIFFERENT dinners, so five separate
            # calls below each round their own share up and the week ends
            # up buying a pepper more than it wants. The buffer holds every
            # per-portion amount unrounded until all five have spoken, then
            # writes one rounded line — see recipes.WeekGroceryBuffer. It
            # takes `conn` too, so every line it flushes lands on this same
            # transaction.
            buffer = _recipes.WeekGroceryBuffer(weekly_plan_id, conn=conn)
            added_items = []
            already_have = []
            for group in by_recipe.values():
                added, have = _recipes._add_recipe_ingredients_for_entries(
                    group["entry_ids"], json.loads(group["ingredients_json"]), weekly_plan_id,
                    default_servings=group["default_servings"], buffer=buffer, conn=conn,
                )
                added_items.extend(added)
                already_have.extend(have)

            # A side the app attached to complete a plate belongs to ONE
            # meal, not to the recipe, so it goes in as its own one-entry
            # group (plates.py). It is still recorded against that entry's
            # id, which is what lets removing the meal remove its side's
            # shopping too. It goes through the SAME buffer as the recipe
            # ingredients above rather than one of its own, and the buffer
            # is flushed only once both loops are done: a side sharing an
            # ingredient with the night's own recipe (or another night's)
            # must round together with it, or the two independent roundings
            # can each tip up and buy more than either alone would have
            # asked for — the same class of bug as the 17 peppers. A side
            # the app attached carries no servings of its own (see
            # plates.py's sides_json shape), so servings_scale_factor falls
            # back to attendance alone — that entry's eaters relative to
            # the household; a side the household added from the meal
            # screen is written for four and scales to the night's eaters
            # the way a recipe does, and a dish on a hosted holiday's big
            # meal carries the table it was written for (app/tools/
            # big_meal.py), which anchors it exactly the way a recipe's
            # default_servings would — so a stuffing written for seven
            # isn't bought three and a half times over (_entry_side_groups).
            for entry in entries:
                for side_ingredients, side_servings, cooked_on_the_night in _entry_side_groups(entry):
                    added, have = _recipes._add_recipe_ingredients_for_entries(
                        [entry["id"]], side_ingredients, weekly_plan_id,
                        default_servings=side_servings, buffer=buffer, conn=conn,
                        # A big-meal dish belongs to the holiday table
                        # alone: a reheat night buys nothing new for it.
                        # Every other side is cooked on the night it sits
                        # on — so it follows its dish through the chain on
                        # a cook night, and on a reheat night it is bought,
                        # because the salad beside the leftovers is a
                        # different dish and nothing else buys it.
                        chain_scale=cooked_on_the_night,
                        reheat_buys_it=cooked_on_the_night,
                    )
                    added_items.extend(added)
                    already_have.extend(have)
            buffer.flush()

            # Counted as distinct names, matching preview_plan_grocery_impact,
            # so the number the draft promised and the number the receipt
            # reports are the same number rather than two different ways of
            # counting the same groceries. Persisted because neither is
            # recoverable later — see schema.sql on approved_grocery_added.
            added_count = len({n.strip().lower() for n in added_items})
            skipped_count = len({n.strip().lower() for n in already_have})
            # A rebuild that put nothing back (every line now skipped as
            # already-have, say) keeps the first approval's receipt: a 0
            # here would fail the `approved_grocery_added > 0` test above
            # and lock the week out of ever rebuilding again.
            if not list_wiped or added_count > 0:
                conn.execute(
                    "UPDATE weekly_plans SET approved_grocery_added = ?, approved_grocery_skipped = ? "
                    "WHERE id = ? AND household_id = ?",
                    (added_count, skipped_count, weekly_plan_id, household_id()),
                )
            conn.commit()
            result = {
                "weekly_plan_id": weekly_plan_id,
                "status": "approved",
                "groceries_added": added_items,
                "already_have_skipped": already_have,
                "groceries_added_count": added_count,
                "already_have_skipped_count": skipped_count,
                "was_already_approved": list_wiped,
                "list_rebuilt": list_wiped,
                "list_needed_count": None,
                # A rebuild keeps the receipt's approver — the yes that
                # settled the week is unchanged; only the list is new.
                "approved_by": (receipt["approved_by"] if list_wiped and receipt else approved_by.strip()),
                "approved_at": approved_at,
                "conflicts": conflicts,
                "conflicts_note": conflicts_note,
                # Unbought lines from an earlier week, set aside for the
                # household to keep or drop on the Shop tab. Named here so
                # the approval can say so; nothing was merged.
                "carried_over": carried_over,
                "carried_over_count": len(carried_over),
                # What this approval took off other plans — the takeover
                # that used to happen at generation (2026-09-13).
                "took_over": took_over,
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result


# What a held thawed ingredient says, and the one thing to ask about it.
# Emily, 2026-09-21, on the thaw a swap strands: "Is there a way to delete
# it but then also have it still note if it had been defrosted already if
# they want to switch recipes for later in the week to use up the meat?"
# The reminder goes — it named a dinner nobody is cooking — and the FACT
# survives, because meat out of the freezer has a clock on it whatever the
# plan says. Both are copy Emily can change in one line.
THAWED_HOLD_TEXT = "{item} came out of the freezer {when} — the dinner it was for has changed."
# The same thing with no day in it, for a row whose task_date held.when_label
# cannot read. Saying less is the only honest option there — a held thing
# that names the wrong day is worse than one that names none.
THAWED_HOLD_TEXT_NO_DAY = "{item} is already out of the freezer — the dinner it was for has changed."
THAWED_HOLD_ASK = "Plan a dinner later this week around the {item} I've already thawed."


def _lower_lead(item: str) -> str:
    """
    "Whole chicken" mid-sentence is "whole chicken"; "BBQ pork" stays as
    typed. An ingredient name is written however the recipe wrote it, and
    the ask reads as the household's own sentence, so only a plain
    Sentence-cased word is lowered.
    """
    if len(item) > 1 and item[1].isupper():
        return item
    return item[:1].lower() + item[1:]


def _release_prep_rows(conn, entry_ids: list[int]) -> list[dict]:
    """
    Take the prep rows for meals that are leaving the plan, and keep the
    one fact that outlives the meal.

    Loop Board "After a swap, the Cook tab still says to thaw something
    for a dinner that isn't on the plan any more" (Emily, 2026-09-21).
    prep_tasks.meal_plan_entry_id carries no foreign key, and only ONE of
    its readers drops a dangling row — cooker.get_prep_schedule. The other
    five show it: prep_sessions._prep_task_rows (the Cook tab's prep
    session), defrost.get_defrost_schedule (the chat answer to "what do I
    need to defrost?"), _pending_thaw_count (the receipt's thaw line),
    defrost.get_defrost_today, and defrost._settled_nights /
    _settled_move_for_entry, which can quietly suppress a freezer chip the
    household should still be offered. Deleting the rows at the source
    covers all six by construction, rather than a filter per reader that
    the seventh would miss.

    A DONE defrost row is the one that must not just vanish: the meat is
    already thawing, so the reminder is stale and the FACT is not. It
    becomes a held thing (app/tools/held.py — the "Pomona, hold this"
    strip on Today and the section under What we know), carrying the day
    it came out and a one-tap way to plan a later dinner around it. Held,
    not re-dated onto some other night: which night is the household's
    call, and guessing one would be the app planning a dinner nobody
    asked for.

    Scoped to defrost. A done prep CUT ("chop the onions") for a dinner
    that changed is a smaller loss — chopped onions keep, and a hold for
    every ticked prep row would turn the strip into a log. Named rather
    than left to be found.

    Runs on the caller's connection and neither commits nor closes: the
    delete and the hold belong to the swap's own transaction, or a
    rolled-back swap leaves a hold for a dinner still on the plan.
    Everything it reaches for takes that connection too (held.hold_thing,
    and cooker.household_today under it) — a nested get_conn inside an
    open write transaction is how this repo has twice earned an
    intermittent "database is locked".
    """
    from . import defrost as _defrost
    from . import held as _held

    if not entry_ids:
        return []
    marks = ",".join("?" * len(entry_ids))
    rows = conn.execute(
        f"SELECT id, task_date, description, quantity, status, task_type FROM prep_tasks "
        f"WHERE household_id = ? AND meal_plan_entry_id IN ({marks}) ORDER BY id",
        (household_id(), *entry_ids),
    ).fetchall()
    if not rows:
        return []
    held: list[dict] = []
    today = None
    for row in rows:
        if row["task_type"] != "defrost" or row["status"] != "done":
            continue
        item = _defrost.thawed_item(row["description"] or "")
        if not item:
            # A defrost row whose description this app did not write has no
            # ingredient to name, so there is nothing honest to hold.
            continue
        if today is None:
            today = _cooker_household_today(conn)
        # The amount, when it says something: "Chicken thighs (2 lbs)" is
        # worth knowing when you are planning a dinner around it, and
        # "Whole chicken (1)" is noise — a bare count of a thing already
        # named in the singular repeats itself.
        quantity = (row["quantity"] or "").strip()
        named = f"{item} ({quantity})" if quantity and not quantity.replace(".", "", 1).isdigit() else item
        when = _held.when_label(row["task_date"], today)
        said = (THAWED_HOLD_TEXT.format(item=named, when=when) if when
                else THAWED_HOLD_TEXT_NO_DAY.format(item=named))
        result = _held.hold_thing(
            said,
            ask_text=THAWED_HOLD_ASK.format(item=_lower_lead(item)),
            member_id=None,
            conn=conn,
        )
        if result.get("held"):
            held.append({"held_id": result["id"], "item": item, "text": result["text"]})
    conn.execute(
        f"DELETE FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id IN ({marks})",
        (household_id(), *entry_ids),
    )
    return held


def _cooker_household_today(conn):
    """The household's own day, read on an already-open connection — see cooker.household_zone's `conn`."""
    from . import cooker as _cooker

    return _cooker.household_today(conn=conn)


def _replace_slot_entries(
    weekly_plan_id: int,
    old_entry_ids: list[int],
    meal_date: str,
    slot: str,
    new_meal: str,
    *,
    food_groups: list[str] | None = None,
    reasoning: str = "",
    derived_from: dict | None = None,
) -> dict:
    """
    Take `old_entry_ids` off a day and put `new_meal` in their place, as
    ONE transaction — the write behind swap_meal_in_plan and
    resolve_open_slot, and through the first of those behind add_dish_day
    (the Check-the-week "+"), every chat swap, swap_in_place, and the
    generation's snack repair. Two more modules compose it directly:
    holidays._plan_dish (the dish a household is bringing somewhere) and
    meal_variety.enforce_distinct_count (the surplus-repeat repair). Both
    did the same job by hand until 2026-09-21, and holidays' hand-rolled
    pair was reproduced losing an approved week's shopping line; a second
    implementation of this write is free to disagree with this one about
    one household's list, which is what it was extracted to prevent.

    The outgoing meals' prep rows go with them, inside this same
    transaction — what clear_plan_slot has always done, and since
    2026-09-22 what every door here does (_release_prep_rows). It was an
    opt-in for one day: `delete_prep_rows` defaulted OFF because a ticked
    fridge move destroyed with the meal is "a real loss to know about",
    and that was a product decision this write did not get to make. Emily
    made it — delete the reminder, HOLD the thawed ingredient — so the
    opt-in is gone and the loss it was protecting is handled rather than
    avoided. Leaving the rows was never the safe side: five of the six
    readers of prep_tasks show a dangling row, so an ordinary swap left
    the Cook tab asking for a chicken to be thawed for a dinner nobody was
    cooking, tickable.

    It used to be four commits in a row: unlink any leftover chain, reverse
    the old meal's groceries, DELETE the row, then plan_meal to INSERT the
    replacement and buy for it. A failure anywhere after the delete left a
    day with no row at all — the one state schema.sql says can never exist,
    and the exact shape drop_dish_from_day was fixed for the same morning
    (see its entry in CLAUDE.md, 2026-09-11). Now everything from the
    unlink to the last grocery line runs on one connection and commits
    once; any exception rolls the lot back, so the day is either exactly
    as it was or exactly as asked.

    Every step joins. The one drop_dish_from_day had to lift out — the
    leftover source's grocery rescale — re-enters through the recipe
    ingest tree, and that tree takes a connection now
    (recipes._add_recipe_ingredients_for_entries and everything under it),
    so here it runs inside, before the old row is reversed and deleted, in
    the same order the self-owned path always ran it. So does the re-buy
    for nights that were eating off a swapped-out source
    (_reingest_unlinked_entries). Nothing here opens a second connection —
    tests/test_swap_atomic.py and test_replace_slot_entries_two_writes.py
    count them — because SQLite gives one writer at a time and a nested
    get_conn inside this transaction would die of "database is locked".

    The write lock is taken FIRST, with an explicit `BEGIN IMMEDIATE`, and
    that is not decoration. db.get_conn leaves sqlite3's legacy
    isolation_level="", which only opens a transaction implicitly at the
    first INSERT/UPDATE/DELETE — so without it every read above the DELETE
    (the plan's approved state, the chain map, the unlink's own reads) ran
    in autocommit, and an independent review showed a second writer could
    swap the same slot and flip the plan to draft in that gap, leaving TWO
    rows on the day and groceries bought for a draft. From the BEGIN on,
    nothing else can write until this commits or rolls back, and every
    read here sees one consistent world.

    The one read that cannot be inside is the caller's: every caller
    resolves `old_entry_ids` on a connection of its own before this opens.
    So the DELETE's rowcount is checked against the ids it was given — a
    row that went away between the caller's read and this lock is a
    concurrent change, and the answer is to roll back and say so, not to
    plan a second meal on top of whatever replaced it.
    """
    from . import leftovers as _leftovers

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        plan_row = conn.execute(
            "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
        approved = bool(plan_row) and plan_row["status"] == "approved"
        # Asked before anything is torn down, on the transaction's own
        # connection: once the old entry is deleted there is nothing left
        # to ask whether it used to feed other nights' leftovers.
        sources = _leftovers.plan_leftover_chains(weekly_plan_id, conn=conn)["sources"]
        was_a_leftovers_source = any(old_id in sources for old_id in old_entry_ids)
        for old_id in old_entry_ids:
            # If the OUTGOING entry was itself a reheat night, its source's
            # make_double_for/make_double_note still names it after this
            # deletes it — see _unlink_leftover_target. Handed a connection
            # it defers the source's grocery rescale to us, and on an
            # approved week that happens right here, inside the transaction.
            rescale_source_id = _unlink_leftover_target(weekly_plan_id, old_id, conn=conn)
            if rescale_source_id is not None and approved:
                _rescale_leftover_source_grocery(rescale_source_id, old_id, conn=conn)
            _grocery._reverse_meal_grocery_contributions(old_id, conn=conn)
        # Before the entries go, and on this connection — the order
        # clear_plan_slot uses, and the only way the pair is atomic. Five
        # of the six readers of prep_tasks do NOT drop a dangling row, so
        # leaving one here is a fridge move on the Cook tab, and an answer
        # to "what do I need to defrost?", for a meal that is no longer
        # planned. A thaw already TICKED is held rather than lost.
        held_thawed = _release_prep_rows(conn, old_entry_ids)
        # By id, not by (date, slot): a slot legitimately holding two
        # snacks must lose only the one being replaced. With no old_meal
        # this is every row in the slot, which is exactly what the by-slot
        # DELETE this replaced did.
        deleted = sum(
            conn.execute(
                "DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                (old_id, household_id()),
            ).rowcount
            for old_id in old_entry_ids
        )
        if deleted != len(old_entry_ids):
            raise RuntimeError(
                f"The {slot} on {meal_date} changed under this swap "
                f"({deleted} of {len(old_entry_ids)} rows still there) — nothing was changed; try again."
            )
        result = _meal_plans.plan_meal(
            meal_date, new_meal, slot=slot, food_groups=food_groups,
            weekly_plan_id=weekly_plan_id, reasoning=reasoning, derived_from=derived_from,
            # Only put the new meal's ingredients on the list if this week
            # has already been approved — approval is what put the old
            # meal's ingredients there in the first place, and the reversal
            # above just took them back off. Swapping inside a
            # still-unapproved draft leaves the grocery list alone, exactly
            # as generating it did.
            add_ingredients_to_grocery_list=approved,
            conn=conn,
        )
        # See swap_meal_in_plan's docstring: breaking a confirmed chain
        # strands the former leftover night(s) with no grocery contribution
        # of their own. Only worth the extra query when the swapped entry
        # actually was a confirmed source and the plan is one whose list is
        # live at all — the overwhelming majority of swaps are neither.
        if was_a_leftovers_source and approved:
            reingested = _reingest_unlinked_entries(weekly_plan_id, conn=conn)
            result["reingested_groceries_added"] = reingested["groceries_added"]
            result["reingested_already_have_skipped"] = reingested["already_have_skipped"]
        # Only when there is something to say: the chat reads this to tell
        # the household the meat did not go with the meal.
        if held_thawed:
            result["held_thawed"] = held_thawed
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result


def swap_meal_in_plan(
    weekly_plan_id: int,
    meal_date: str,
    new_meal: str,
    slot: str = "dinner",
    food_groups: list[str] | None = None,
    old_meal: str | None = None,
    old_entry_id: int | None = None,
    reasoning: str = "",
) -> dict:
    """
    Replace the meal on one day/slot of an already-generated weekly plan,
    without regenerating or touching the rest of the plan. new_meal can be
    a saved recipe name or a freeform description, same as plan_meal. The
    old meal's auto-added grocery ingredients are removed first (trimmed or
    deleted, whatever the amount it contributed calls for — see
    _reverse_meal_grocery_contributions) so the grocery list reflects only
    the new meal afterward instead of carrying both.

    A source night other nights were eating as leftovers (see leftovers.py)
    reverses its WHOLE batch contribution above, same as any other swap —
    the scaled amount that covered its own table plus every leftover night
    it fed. Once the swap lands, plan_leftover_chains no longer confirms
    that pairing: the new entry here carries no make_double_for of its own
    (repair_leftover_chains, the only writer of that field, runs at
    generation time, not here — see its docstring), so each former
    leftover night is now, honestly, just an ordinary planned meal — one
    that has never had its own ingredients bought, because a leftovers
    night never contributes to the grocery list on its own (see
    recipes._add_recipe_ingredients_for_entries). Nothing else re-buys them
    on its own, so this does, via _reingest_unlinked_entries — the same
    incremental top-up approve_weekly_plan's own candidate query already
    performs for a slot planned after the week was approved.

    slot is any of DAY_SLOTS — `snack` included, and a snack swap is an
    ordinary swap in every respect. Anything else raises rather than
    silently deleting nothing and planning a meal into a slot no screen
    reads.

    A day can hold MORE than one entry in one slot: two different snacks a
    day is the default (see preferences.resolve_snacks_per_day), and
    nothing about that is a duplicate to be cleaned up. Pass `old_meal` to
    say WHICH of them is being replaced — without it a slot holding two
    snacks would lose both to a swap that was only ever about one of them.
    An old_meal that matches nothing in the slot raises, rather than
    quietly adding a third snack to the day.

    `old_entry_id` says the same thing by id, and it exists because a name
    cannot always say it: an OPEN slot has no meal name at all, so a
    caller replacing one (see add_dish_day) would have to pass old_meal
    None and take every row in the slot with it — the two-snacks bug over
    again, reached from the other side. Given both, the id wins; it is the
    more precise of the two.

    **THIS FUNCTION DELIBERATELY ACCEPTS A NIGHT THAT HAS ALREADY GONE BY,
    and the refusal lives at the doors instead — 2026-09-17, and read this
    before moving it.** A person may not rewrite a night that is over: it
    changes a plan nobody can act on and, on an approved week, puts a new
    line on a shopping list for a dinner that has been and gone (measured:
    "Black beans 4 cans" became "Black beans 2 cans, Carrots 6"). But this
    is not only a person's write. plan_quality.repair_snack_clashes reaches
    it AT GENERATION TIME, and a period that STARTED before today is an
    ordinary shape here — a Saturday sign-up's Sat–Sun week, a custom date
    range, a takeover remnant — so the repair legitimately swaps a snack on
    a day that is behind the household's today (measured, on a plan begun
    three days ago). A refusal in here would break that, and silently, in
    both possible shapes: repair_snack_clashes wraps its whole loop in one
    try/except, so a raise abandons every other repair on the week, and it
    reads nothing off the result, so a refusal dict would be recorded as a
    move that never happened.

    So the rule is stated where the DECISION is, not where the write is —
    which is also what the two halves of the Review stepper already do
    (add_dish_day, drop_dish_from_day), and what keeps the machine path
    byte-identical rather than exempt. Every door a person reaches asks
    weekly_plan.night_has_gone first:
      * this function's chat twin, swap_meal_in_plan_for_chat, which is
        what agent.TOOL_FUNCTIONS points at;
      * add_dish_day, above — the Review stepper's "+";
      * swap_in_place.swap_meal_in_place — "Swap · I'll pick";
      * plate_parts.change_part — a different protein in the same dish;
      * proposals.apply_proposal — the chat change card's Save changes;
      * and swap_in_place.apply_pick, which the last three share, as the
        backstop: a NEW door that forgets gets a refusal there rather than
        the bug back.
    What that costs is honest and worth knowing: a new caller of THIS
    function, composing it directly the way add_dish_day does, is not
    covered by any of them. Add the ask at your door, or the class comes
    back.
    """
    if slot not in DAY_SLOTS:
        raise ValueError(
            f"'{slot}' is not a slot a day has — expected one of {', '.join(DAY_SLOTS)}."
        )

    conn = get_conn()
    old_entries = conn.execute(
        "SELECT mpe.id AS id, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = ? AND mpe.household_id = ?",
        (weekly_plan_id, meal_date, slot, household_id()),
    ).fetchall()
    conn.close()
    if old_entry_id is not None:
        matched = [r for r in old_entries if r["id"] == old_entry_id]
        if not matched:
            raise ValueError(
                f"No meal {old_entry_id} in the {slot} slot on {meal_date}."
            )
        old_entries = matched
    elif old_meal is not None:
        wanted = old_meal.strip().lower()
        matched = [r for r in old_entries if (r["meal"] or "").strip().lower() == wanted]
        if not matched:
            have = ", ".join(f"'{r['meal']}'" for r in old_entries) or "nothing"
            raise ValueError(
                f"No '{old_meal}' in the {slot} slot on {meal_date} — that slot holds {have}."
            )
        old_entries = matched

    result = _replace_slot_entries(
        weekly_plan_id, [row["id"] for row in old_entries], meal_date, slot, new_meal,
        food_groups=food_groups,
        # Blank for a swap the household asked for in chat — there is no
        # "why this?" beyond their asking, and inventing one would be the
        # plan explaining itself back to the person who chose it. Set by
        # an automatic repair, which does owe the card a reason.
        reasoning=reasoning,
    )
    # The same shared verdict generation is held to (see
    # taste_verdict.dish_verdict), for the dish CHAT just picked and the
    # people actually eating that night. Reported, never enforced: a swap
    # the household asked for by name still happens. It's here rather than
    # left to the model's own memory because chat is the one place a dish
    # gets chosen with no candidate list in front of it — and because "not
    # on Thursday, Vineeth's home" is a thing to say back in the same
    # breath, not after the fact.
    verdict = _taste_verdict_for_slot(new_meal, meal_date, slot)
    if verdict:
        result["taste_verdict"] = verdict
    return result


# Keys on derived_from that say where an entry sits in a leftover chain.
# replace_dish_on_days works these out against the group it is replacing;
# everything else on derived_from is the caller's to carry.
_CHAIN_KEYS = ("links_to", "make_double_for", "make_double_note", "cook_ahead")


def replace_dish_on_days(weekly_plan_id: int, items: list[dict]) -> dict:
    """
    Put a new dish on SEVERAL slots of one plan at once, as ONE transaction
    — the write behind a Swap tapped on a multi-day row of "What we're
    eating" (Emily, 2026-09-22, on her phone: a lunch planned Thursday and
    Friday was one row, its Swap changed Thursday only — "it didn't swap
    it, it just added it so i have two meals instead of 1").

    `items` is one dict per slot being replaced: {old_entry_id, date, slot,
    new_meal, food_groups, reasoning, derived_from}. `derived_from` is what
    the caller wants the new row to carry (the swap's undo note, the
    group's token); its leftover-chain keys (_CHAIN_KEYS) are NOT taken
    from it — they are worked out here, against the chains as this
    transaction reads them, so the new dish keeps the old one's shape:

      * a cook whose reheat nights are all in the group stays the cook and
        feeds exactly those nights (make_double_for rewritten as
        "date:slot" — the ids change, the nights don't);
      * a reheat night whose cook is in the group stays a reheat of it
        (links_to "date:slot", cook_ahead kept, so "Made ahead" still
        reads "Made ahead");
      * a reheat night whose cook is NOT in the group (the cook night has
        gone by) becomes an ordinary night of the new dish, and its old
        cook is told so (_unlink_leftover_target, as every swap does);
      * a reheat night the group leaves behind (already cooked, so not
        swapped) drops out of the new cook's batch and, on an approved
        week, buys for itself (the _reingest_unlinked_entries rule);
      * separate cooks stay separate cooks.

    Why a function of its own and not N calls to swap_meal_in_plan — two
    reasons. N transactions can leave the week half swapped (Thursday new,
    Friday old: the bug over again, reached by a failure instead of by
    design). And a chain cannot be carried across N swaps: the first swap
    sees the reheat night still holding the old dish, unlinks the pair and
    buys the new cook for one table, so the batch and the groceries would
    both be wrong even when every call succeeded.

    The order is _replace_slot_entries' own, widened to many rows: every
    unlink (and its rescale) first, THEN every reversal — a rescale
    re-ingests every entry cooking the source's recipe, so a reversal done
    before it would be bought straight back — then the prep rows, the
    deletes (count-checked, as there), the inserts, and on an approved
    week ONE grocery ingest of the new rows (plus any reheat night the
    group orphaned) through one buffer, after the chains are written, so
    the cook buys for its whole batch and a reheat night buys nothing —
    what approval itself would have bought. A draft's list is left alone,
    as every draft swap leaves it.

    Does NOT ask night_has_gone — the door does (swap_in_place.
    apply_pick_to_days), as swap_meal_in_plan's docstring says every door
    must. Returns {entry_ids (in `items` order), held_thawed?}.
    """
    from . import leftovers as _leftovers

    if not items:
        raise ValueError("Nothing to swap.")
    for item in items:
        if item["slot"] not in DAY_SLOTS:
            raise ValueError(
                f"'{item['slot']}' is not a slot a day has — expected one of {', '.join(DAY_SLOTS)}."
            )
    old_ids = [int(item["old_entry_id"]) for item in items]
    group = set(old_ids)
    if len(group) != len(old_ids):
        raise ValueError("The same meal was named twice in one swap.")

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        plan_row = conn.execute(
            "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
        if not plan_row:
            raise ValueError(f"No weekly plan {weekly_plan_id} in this household.")
        approved = plan_row["status"] == "approved"
        marks = ",".join("?" * len(old_ids))
        present = {
            r["id"] for r in conn.execute(
                f"SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? "
                f"AND id IN ({marks})",
                (weekly_plan_id, household_id(), *old_ids),
            ).fetchall()
        }
        if present != group:
            raise RuntimeError("Part of that meal changed under this swap — nothing was changed; try again.")

        chains = _leftovers.plan_leftover_chains(weekly_plan_id, conn=conn)
        chain_fields: dict[int, dict] = {}
        for old_id in old_ids:
            fields: dict = {}
            source = chains["sources"].get(old_id)
            if source:
                kept = [f"{t['date']}:{t['slot']}" for t in source["targets"] if t["entry_id"] in group]
                if kept:
                    fields["make_double_for"] = kept
                    fields["make_double_note"] = _make_double_note_text(kept)
            reheat = chains["leftovers"].get(old_id)
            if reheat and reheat["source"]["entry_id"] in group:
                fields["links_to"] = f"{reheat['source']['date']}:{reheat['source']['slot']}"
                if reheat.get("cook_ahead"):
                    fields["cook_ahead"] = True
            chain_fields[old_id] = fields
        # Reheat nights this group feeds now and will not feed after: they
        # stay on the plan (not in the group) with nothing bought for them.
        orphaned = [
            t["entry_id"]
            for old_id in old_ids if old_id in chains["sources"]
            for t in chains["sources"][old_id]["targets"] if t["entry_id"] not in group
        ]

        for old_id in old_ids:
            reheat = chains["leftovers"].get(old_id)
            if reheat and reheat["source"]["entry_id"] in group:
                # Both ends leave together: nothing to tell the cook, and a
                # rescale here would re-buy a row about to be reversed.
                continue
            rescale_source_id = _unlink_leftover_target(weekly_plan_id, old_id, conn=conn)
            if rescale_source_id is not None and approved:
                _rescale_leftover_source_grocery(rescale_source_id, old_id, conn=conn)
        for old_id in old_ids:
            _grocery._reverse_meal_grocery_contributions(old_id, conn=conn)
        held_thawed = _release_prep_rows(conn, old_ids)
        deleted = sum(
            conn.execute(
                "DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                (old_id, household_id()),
            ).rowcount
            for old_id in old_ids
        )
        if deleted != len(old_ids):
            raise RuntimeError(
                f"Part of that meal changed under this swap ({deleted} of {len(old_ids)} "
                "rows still there) — nothing was changed; try again."
            )

        new_ids: list[int] = []
        for item, old_id in zip(items, old_ids):
            derived = {k: v for k, v in (item.get("derived_from") or {}).items() if k not in _CHAIN_KEYS}
            derived.update(chain_fields[old_id])
            planned = _meal_plans.plan_meal(
                item["date"], item["new_meal"], slot=item["slot"],
                food_groups=item.get("food_groups"), weekly_plan_id=weekly_plan_id,
                reasoning=item.get("reasoning") or "", derived_from=derived,
                add_ingredients_to_grocery_list=False, conn=conn,
            )
            new_ids.append(planned["entry_id"])

        if approved:
            buy = set(new_ids) | set(orphaned)
            rows = [r for r in _plan_grocery_candidate_entries(conn, weekly_plan_id) if r["id"] in buy]
            if rows:
                buffer = _recipes.WeekGroceryBuffer(weekly_plan_id, conn=conn)
                _ingest_recipe_group_and_sides(rows, weekly_plan_id, buffer)
                buffer.flush()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    out = {"entry_ids": new_ids}
    if held_thawed:
        out["held_thawed"] = held_thawed
    return out


def swap_meal_in_plan_for_chat(*args, override: bool = False, **kwargs) -> dict:
    """
    swap_meal_in_plan with the two refusals a PERSON is owed in front of
    it: a night that has already gone by, and — since 2026-09-21 — a dish
    somebody at the table can't have (allergen_gate.refuse_if_clashing,
    which `override`, the person's own "do it anyway", is the only way
    past).

    agent.TOOL_FUNCTIONS points at this rather than at the function itself,
    the shape grocery.add_grocery_item_for_chat already uses (2026-09-15).
    A wrapper rather than an argument on the real function, because the
    caller that must NOT be refused is week generation, and an opt-out is a
    line somebody deletes while tidying — with nothing going red, since
    repair_snack_clashes swallows what it gets. A door that has to opt IN
    is a door somebody adds; a door that has to opt OUT is a door somebody
    silently loses. See swap_meal_in_plan's own docstring for the whole
    argument and for what it gives up.

    `*args` rather than a restated signature on purpose: everything about
    which slot, which old meal and which id is that function's to define,
    and a second copy of its parameters here is one more thing to keep in
    step. The date is read the same way the model sends it — second
    positional or `meal_date` — and a call that names neither falls through
    to the real function's own TypeError rather than being second-guessed.

    **It RAISES rather than answering a refusal dict, and that is not a
    style choice.** The agent dispatch packages a raise as `is_error`, and
    `_turn_wrote_anything` counts only tool results that are NOT errors —
    so a dict would let the model say "I've swapped that" with
    verify_change_claim finding a successful write behind it and declining
    to retract. That is the 2026-09-08 bug ("the chat said it changed a
    snack and it didn't") reached from a new direction. The known cost is
    one `error_events` row per refusal, `swap_meal_in_plan / SlotRefused`:
    the same shape check_off_meal's status guard already produces, and the
    guard working rather than a new breakage.
    """
    meal_date = kwargs.get("meal_date")
    if meal_date is None and len(args) >= 2:
        meal_date = args[1]
    # Asked before anything is read for the write, and with no connection of
    # this call's open — there is none, this is the first line.
    if isinstance(meal_date, str) and night_has_gone(meal_date):
        raise SlotRefused(NIGHT_GONE)
    new_meal = kwargs.get("new_meal")
    if new_meal is None and len(args) >= 3:
        new_meal = args[2]
    if isinstance(new_meal, str):
        from . import allergen_gate as _allergen_gate
        _allergen_gate.refuse_if_clashing(new_meal, override=override)
    return swap_meal_in_plan(*args, **kwargs)


def describe_planned_meal(entry_id: int | None = None, meal_date: str | None = None,
                          slot: str | None = None) -> dict | None:
    """
    The one planned meal a chat turn is ABOUT, for "Tell me what instead"
    on a meal card (Emily, 2026-09-13: she tapped the link beside the
    burgers and chat had no idea which meal she meant). Household-scoped,
    read-only, and None rather than an error for anything it can't find —
    a missing subject makes the turn an ordinary one, never a failed one.

    Looked up by entry id first, then by the slot: a swap deletes the row
    and inserts a new one (see _replace_slot_entries), so the id the card
    was drawn with goes stale the moment the household changes the meal
    — and "actually, make it chicken" one message later is exactly the
    follow-up this exists for. The slot fallback resolves against the live
    plan covering that day, so it says what is there NOW.

    Only a real meal is a subject: a planned_empty row is a night nobody
    is home (never offered as a decision — see CLAUDE.md) and an open one
    has no dish to talk about yet, so both come back None. Ingredients
    ride along so "swap the turkey for beef" can be proposed without a
    get_recipe round first.
    """
    hh = household_id()
    conn = get_conn()
    try:
        select = (
            "SELECT mpe.id, mpe.weekly_plan_id, mpe.date, mpe.slot, mpe.slot_state, "
            "mpe.component_category, mpe.recipe_id, "
            "COALESCE(r.name, mpe.freeform_meal) AS meal, r.ingredients_json, "
            "r.main_protein, wp.status "
            "FROM meal_plan_entries mpe "
            "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
            "JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        )
        row = None
        if entry_id is not None:
            row = conn.execute(
                select + "WHERE mpe.id = ? AND mpe.household_id = ? AND wp.status != 'retired'",
                (entry_id, hh),
            ).fetchone()
        if row is None and meal_date and slot in DAY_SLOTS:
            try:
                plan_id = get_plan_id_for_date(meal_date)
            except ValueError:
                plan_id = None
            if plan_id is not None:
                # A day's two snacks share one slot; without the id there is
                # no honest way to pick between them, so the first is taken
                # only when it is the only one.
                rows = conn.execute(
                    select + "WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? "
                    "AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id",
                    (plan_id, hh, meal_date, slot),
                ).fetchall()
                if len(rows) == 1:
                    row = rows[0]
    finally:
        conn.close()
    if row is None or row["component_category"] or (row["slot_state"] or "planned") != "planned":
        return None
    meal = (row["meal"] or "").strip()
    if not meal:
        return None
    try:
        ingredients = json.loads(row["ingredients_json"] or "[]")
    except (TypeError, ValueError):
        ingredients = []
    return {
        "entry_id": row["id"],
        "weekly_plan_id": row["weekly_plan_id"],
        "date": row["date"],
        "weekday": _weekday_label(row["date"]),
        "slot": row["slot"] or "dinner",
        "meal": meal,
        "recipe_id": row["recipe_id"],
        "main_protein": row["main_protein"] or "",
        "ingredients": [
            {"item": i.get("item", ""), "qty": i.get("qty", "")}
            for i in ingredients if isinstance(i, dict) and i.get("item")
        ],
        "approved": row["status"] == "approved",
    }


def _taste_verdict_for_slot(meal: str, meal_date: str, slot: str) -> dict | None:
    """
    dish_verdict for one planned slot, or None when there's nothing worth
    saying (no per-person feedback on this dish, or it couldn't be
    computed). Never raises — a swap must not fail over an advisory.
    """
    from . import attendance as _attendance
    from . import taste_verdict as _taste_verdict

    try:
        eaters = _attendance.get_slot_attendance(meal_date, slot)["present_names"]
        verdict = _taste_verdict.dish_verdict(meal, eaters)
        return verdict if verdict["verdict"] != _taste_verdict.NEUTRAL else None
    except Exception:
        logger.exception("Taste verdict failed for %s on %s %s", meal, meal_date, slot)
        return None


# ---------- Moving a dinner between nights ----------
# Plan › Which days, seven tiles (Emily, 2026-09-12, "Week · A · Seven
# tiles"): drag one night onto another and the two DINNERS trade places.
# Chat reaches the same write through the swap_dinner_nights tool
# ("move Thursday's dinner to Friday").
#
# This is a MOVE, not a swap-out. swap_meal_in_plan replaces a dish — it
# deletes the row, reverses its groceries and plans a new one. Here the
# same dish is still on the week, cooked for the same table one night
# later or earlier, so the rows are re-dated in place and keep their ids.
# Everything keyed by entry id rides along for free: the grocery links
# (meal_plan_grocery_links.meal_plan_entry_id), the cooked tick, the
# inventory-depletion stamp, the plate sides. What is keyed by DATE has to
# be moved by hand, and this is the list — anything added later that keys
# off a dinner's date belongs here too:
#
#   * derived_from.links_to / make_double_for — a leftover chain names its
#     other half as "YYYY-MM-DD:slot" (leftovers.py). Every reference to
#     either night's dinner is rewritten to the night it now sits on, so
#     the pairing survives the move. A chain that would run BACKWARDS
#     afterwards (a reheat before its cook) is refused, nothing written —
#     the same 'refused' answer drop_dish_from_day gives for a source.
#   * prep_tasks of task_type 'defrost' that name one of the moved entries
#     (both the freezer-matched kind, defrost.sync_defrost_tasks, and the
#     household-confirmed kind, defrost.confirm_frozen_items). A defrost
#     date is the cook date minus a lead time, so it moves by exactly the
#     number of days the dinner moved, status untouched; its sentence
#     names the weekday and is re-said.
#   * prep-cut rows (prep_sessions.add_prep_cut) are NOT re-dated: a prep
#     session sits on the household's prep DAY, a rhythm fact, and what it
#     feeds is read off the entry's own date, which has just moved. The
#     LLM-written 'general' prep tasks carry no entry id at all and are
#     left alone too — they are regenerated wholesale by
#     generate_prep_schedule.
#   * slot_needs / slot_attendance / away_stretches stay where they are:
#     "Emily is out Thursday" is a fact about Thursday, not about the dish
#     that was going to be cooked on it. That is also why an away night
#     (slot_state 'planned_empty') refuses to take part — moving a dinner
#     onto a night nobody is home would plan food for an empty table.
#
# The GROCERY LIST is deliberately untouched. Same dishes, same
# quantities, same lines — only the day they are cooked has changed, and
# the links are by entry id, so nothing needs reversing or re-buying.
#
# Undo is one token, written on each moved row as derived_from.moved_from
# = {"date": <where it was>, "at": <when>}. undo_dinner_nights_swap checks
# both nights still carry tokens pointing at each other before moving
# anything back, then clears them — the same "written once, read once"
# shape swap_in_place's swapped_from takes. A second move of the same tile
# overwrites the token: Undo is the LAST move, which is what a toast can
# honestly offer.

NIGHTS_MOVED_KEY = "moved_from"


def _weekday_of(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A")


def _nights_swap_refusal(message: str, date_a: str, date_b: str) -> dict:
    return {"status": "refused", "date_a": date_a, "date_b": date_b, "message": message}


def _rewrite_chain_ref(ref, mapping: dict[str, str]):
    """One links_to / make_double_for value, re-pointed if it names a moved
    dinner; anything else (an entry_id form, another slot) unchanged."""
    if not isinstance(ref, str):
        return ref
    return mapping.get(ref.strip(), ref)


def delete_plan_entry(conn, entry_id: int) -> None:
    """
    Take one row off the plan WITHOUT touching the grocery list — its prep
    rows with it (the order clear_plan_slot uses), its grocery links by the
    table's own cascade.

    Only for rows whose food is not going anywhere: a leftovers night the
    cook itself now lands on, a reheat whose portion is frozen, a row an
    Undo is about to replace with the one it stood in for. Everything
    removed this way belongs in an undo snapshot (plan_undo) — nothing else
    can put it back.

    Written in tonight.py first (2026-09-22) and lifted here on 2026-09-24
    when drop_dish_from_day grew the same need, because this module owns
    meal_plan_entries and a second copy of "delete a row and its prep" is
    exactly how the two would come to disagree about whether prep travels.
    """
    conn.execute(
        "DELETE FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id = ?",
        (household_id(), entry_id),
    )
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    )


def fed_night_label(target: dict) -> str:
    """"Wednesday", or "Wednesday’s lunch" for a night a chain feeds at
    another meal — the one way this app names a fed night, read by the
    night off's sub-line and toast and by the Review stepper's "−"."""
    return _weekday_of(target["date"]) if target["slot"] == "dinner" \
        else f"{_weekday_of(target['date'])}’s {target['slot']}"


# The one question moving a cook onto a fed night can ask: that night's
# leftovers row has been ticked cooked, and the move deletes it (Emily,
# 2026-09-24, option B — "ask first"). Both doors ask it in these words:
# tonight.tonight_night_off and drop_dish_from_day.
COOKED_FED_NIGHT = "fed_night_cooked"
COOKED_FED_NIGHT_CONFIRM_LABEL = "Move it"


def fed_night_is_cooked(conn, target: dict) -> bool:
    """Whether the row a cook would land on has been ticked cooked. Read on
    the caller's connection, so a caller holding the write lock sees the
    tick as it stands under that lock."""
    row = conn.execute(
        "SELECT cooked_status FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (target["entry_id"], household_id()),
    ).fetchone()
    return row is not None and (row["cooked_status"] or "") == "done"


def cooked_move_confirmed(confirm_cooked, target: dict) -> bool:
    """
    Whether the household's yes covers THIS night. `confirm_cooked` is
    either True (chat: the model asked in words and the household said yes)
    or the "YYYY-MM-DD:slot" the question was about (the screen sends back
    the night it showed). A yes to Friday is not a yes to Saturday: if the
    week moved between the question and the tap and a different ticked
    night is now the target, the answer is the question again, about that
    night — never a silent delete of one nobody was asked about.
    """
    if confirm_cooked is True:
        return True
    if isinstance(confirm_cooked, str) and confirm_cooked:
        return confirm_cooked == f"{target['date']}:{target['slot']}"
    return False


def cooked_fed_night_question(target: dict, dish: str, extra: dict | None = None) -> dict:
    """
    The `needs_confirmation` answer both doors give when the night a cook
    would move onto has already been ticked cooked. Nothing was written;
    the same call again with `confirm_cooked` goes ahead as it always did,
    and Undo still puts the ticked row back.

    The dish is named rather than "it" (copy rule 1, clear beats warm): on
    the night off "it" could be tonight's dinner or the leftovers already
    sitting on that night.
    """
    label = fed_night_label(target)
    night = f"{label}’s" if target["slot"] == "dinner" else f"{label} is"
    return {
        **(extra or {}),
        "status": "needs_confirmation",
        "reason": COOKED_FED_NIGHT,
        "dish": dish,
        "cooked_date": target["date"],
        "cooked_slot": target["slot"],
        # What a screen sends back as `confirm_cooked` to say yes to THIS
        # night and no other (cooked_move_confirmed).
        "confirm_night": f"{target['date']}:{target['slot']}",
        "message": f"{night} already marked cooked. Move the {dish} there anyway?",
        "confirm_label": COOKED_FED_NIGHT_CONFIRM_LABEL,
    }


def fed_nights_in_eating_order(source: dict | None, after: str) -> list[dict]:
    """
    The nights a cook feeds, later than `after`, in the order they are
    EATEN — `plan_leftover_chains`' own target dicts, re-sorted.

    In EATING order, not that function's (date, slot) string sort, which
    puts Wednesday's dinner before Wednesday's lunch: a cook that has to
    move onto one of these has to land on the first meal that eats from it,
    or the meal before it would be a reheat of a batch not yet cooked.

    One reading, because two answers now move a cook onto the first night
    it was feeding and they must not disagree about WHICH night that is:
    tonight.tonight_night_off ("we're going out", nowhere free to move to)
    and weekly_plan.drop_dish_from_day (the Review stepper's "−").
    """
    slots = list(DAY_SLOTS)
    return sorted(
        (t for t in (source or {}).get("targets") or [] if t["date"] > after),
        key=lambda t: (t["date"], slots.index(t["slot"]) if t["slot"] in slots else len(slots)),
    )


def move_cook_onto_fed_night(conn, weekly_plan_id: int, entry_id: int,
                             old_date: str, old_slot: str, target: dict) -> None:
    """
    Cook a batch on the first night it was feeding instead of on the night
    it was planned for — Emily's option A, 2026-09-22, and the only answer
    that leaves nobody eating a reheat of a batch nobody cooked.

    The target's leftovers row goes (it is the cook now), the cook moves
    onto its date and slot, every OTHER row that named the old night by
    date re-points at the new one, and the cook's fridge moves travel with
    it by the same rule a nights swap moves them (_shift_defrost_tasks).

    Runs on the caller's open transaction and neither commits nor closes.
    What it deliberately does NOT do is the chain's own bookkeeping — the
    cook's make_double_for still names the night it has just landed on, and
    the two callers settle that differently: the night off counts tonight's
    share as the freezer's and keeps the batch its size
    (tonight._shrink_chain_into_freezer), while the stepper's "−" is the
    household asking for one fewer night of the dish, so the batch really
    does shrink (_unlink_leftover_target, then a rescale after the commit).
    The move is the same either way, which is the whole reason it is one
    function.
    """
    new_ref = f"{target['date']}:{target['slot']}"
    delete_plan_entry(conn, target["entry_id"])
    conn.execute(
        "UPDATE meal_plan_entries SET date = ?, slot = ? WHERE id = ? AND household_id = ?",
        (target["date"], target["slot"], entry_id, household_id()),
    )
    # Any other row naming the old night by date ("2026-09-23:dinner") now
    # names the night the cook moved to — the later fed nights' links_to,
    # above all. _rewrite_chain_ref, the nights swap's own.
    mapping = {f"{old_date}:{old_slot}": new_ref}
    for r in conn.execute(
        "SELECT id, derived_from_json FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? "
        "AND component_category IS NULL",
        (weekly_plan_id, household_id()),
    ).fetchall():
        derived = json.loads(r["derived_from_json"] or "{}")
        if "links_to" not in derived:
            continue
        new = _rewrite_chain_ref(derived["links_to"], mapping)
        if new != derived["links_to"]:
            derived["links_to"] = new
            conn.execute(
                # Scoped, like the read four lines above it. The id came
                # out of a household-filtered SELECT, so nothing was
                # leaking — but the guard belongs on the statement rather
                # than on whoever wrote the read, which is the whole point
                # of the sweep in test_leftover_chain_household_filter.py.
                # That sweep is what caught this: it is green on this
                # branch alone and RED on a tree that also carries
                # overnight/leftover-chain-household-filter, because that
                # branch is what makes the module's other writes scoped
                # and this one the odd one out.
                "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                (json.dumps(derived), r["id"], household_id()),
            )
    _shift_defrost_tasks(conn, weekly_plan_id, entry_id, old_date, target["date"])


def _shift_defrost_tasks(conn, weekly_plan_id: int, entry_id: int, old_date: str, new_date: str) -> int:
    """
    Move one dinner's freezer-to-fridge reminders by exactly the number of
    days the dinner itself moved, status untouched, and re-say the weekday
    in their sentence. Returns how many rows moved.

    Lifted out of _apply_dinner_nights_swap (2026-09-22) so the night off's
    "cook it on the night it was feeding" (tonight.tonight_night_off, Emily
    2026-09-22) moves a dinner's reminders by the SAME rule a nights swap
    does rather than a second copy of it. Runs on the caller's connection
    and transaction; neither commits nor closes.
    """
    delta = (date.fromisoformat(new_date) - date.fromisoformat(old_date)).days
    old_wd, new_wd = _weekday_of(old_date), _weekday_of(new_date)
    tasks = conn.execute(
        "SELECT id, task_date, description FROM prep_tasks "
        "WHERE household_id = ? AND weekly_plan_id = ? AND task_type = 'defrost' "
        "AND meal_plan_entry_id = ?",
        (household_id(), weekly_plan_id, entry_id),
    ).fetchall()
    for t in tasks:
        moved_to = (date.fromisoformat(t["task_date"]) + timedelta(days=delta)).isoformat()
        # defrost._describe: "… — for Thursday's skewers." Only the
        # weekday changes, so only the weekday is re-said.
        said = (t["description"] or "").replace(f"for {old_wd}’s", f"for {new_wd}’s") \
            .replace(f"for {old_wd}'s", f"for {new_wd}'s")
        conn.execute(
            "UPDATE prep_tasks SET task_date = ?, description = ? WHERE id = ?",
            (moved_to, said, t["id"]),
        )
    return len(tasks)


def _apply_dinner_nights_swap(
    weekly_plan_id: int, date_a: str, date_b: str, *, undo: bool, dry_run: bool = False,
    conn=None,
) -> dict:
    """
    The one write behind swap_dinner_nights and undo_dinner_nights_swap.
    Validates, refuses in plain words, then re-dates both nights' dinner
    rows and everything keyed by their dates in ONE transaction. `undo`
    only changes what happens to the moved_from token: a move writes it,
    an undo requires it and clears it.

    `dry_run` (tonight.py, 2026-09-13) runs every check — the same
    ValueErrors, the same refusals — and then rolls back instead of
    writing, answering `status` 'ok'. It is how Now's "Something else"
    sheet offers only nights that would actually swap, without a second
    copy of these rules that could drift.

    `conn` lets a caller that ALREADY holds BEGIN IMMEDIATE fold this whole
    swap into its own transaction (tonight.tonight_night_off, which moves
    tonight's dish and then states the night empty — two writes that must
    not be separately visible, or a second tap computed against the gap
    swaps the dish straight back and then deletes it). Given one, this
    neither begins, commits, rolls back nor closes, and a `dry_run` simply
    returns without rolling back the caller's work. Two fields are then
    OMITTED from the result, deliberately rather than silently: `days` and
    `taste_verdicts` are reads, and a read on a second connection inside
    an open write transaction sees the world as it was BEFORE it — so they
    are the caller's to take after its commit, if it wants them at all.
    """
    for d in (date_a, date_b):
        try:
            date.fromisoformat(d)
        except (TypeError, ValueError):
            raise ValueError("Both nights must be ISO dates (YYYY-MM-DD).")
    if date_a == date_b:
        raise ValueError("Those are the same night — nothing to move.")

    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        if own_conn:
            conn.execute("BEGIN IMMEDIATE")
        plan = conn.execute(
            "SELECT id, week_start_date, content_start_date, day_count, planning_mode, status "
            "FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
        if plan is None:
            raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
        if plan["planning_mode"] == "component_based":
            raise ValueError("That plan is built from components, not nights — there's nothing to move.")
        start, day_count = plan_period(plan)
        end = period_end_date(start, day_count)
        for d in (date_a, date_b):
            if not (start <= d <= end):
                raise ValueError(f"{_weekday_of(d)} ({d}) isn't on this plan.")

        rows = conn.execute(
            """
            SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status,
                   mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
            ORDER BY mpe.date ASC, mpe.id ASC
            """,
            (weekly_plan_id, household_id()),
        ).fetchall()
        moving = [r for r in rows if r["slot"] == "dinner" and r["date"] in (date_a, date_b)]
        if not moving:
            raise ValueError("Neither of those nights has a dinner on it yet.")

        # ---- refusals: answers, not errors, and nothing is written ----
        for r in moving:
            wd = _weekday_of(r["date"])
            if r["slot_state"] == "planned_empty":
                return _nights_swap_refusal(
                    f"Nobody’s cooking {wd} — I’ve left it as it is.", date_a, date_b)
            if (r["cooked_status"] or "") == "done":
                return _nights_swap_refusal(
                    f"{r['meal']} on {wd} has already been cooked — I’ll leave that one where it is.",
                    date_a, date_b)
        if undo:
            # Both halves have to still say they came from each other; a
            # night that has been moved again since, or never was, has
            # nothing to put back.
            for r in moving:
                token = (json.loads(r["derived_from_json"] or "{}").get(NIGHTS_MOVED_KEY) or {})
                other = date_b if r["date"] == date_a else date_a
                if token.get("date") != other:
                    raise ValueError("Those nights haven’t just been moved, so there’s nothing to put back.")

        # ---- the new picture, in memory first, so a backwards chain is
        # caught before anything is written ----
        new_date = {r["id"]: (date_b if r["date"] == date_a else date_a) for r in moving}
        now_date = {r["id"]: r["date"] for r in rows}
        mapping = {f"{date_a}:dinner": f"{date_b}:dinner", f"{date_b}:dinner": f"{date_a}:dinner"}
        after: list[dict] = []
        for r in rows:
            derived = json.loads(r["derived_from_json"] or "{}")
            changed = False
            if "links_to" in derived:
                new_ref = _rewrite_chain_ref(derived["links_to"], mapping)
                changed = changed or new_ref != derived["links_to"]
                derived["links_to"] = new_ref
            fed = derived.get("make_double_for")
            if fed:
                fed_list = [fed] if isinstance(fed, str) else list(fed)
                new_fed = [_rewrite_chain_ref(t, mapping) for t in fed_list]
                changed = changed or new_fed != fed_list
                derived["make_double_for"] = new_fed
            if r["id"] in new_date:
                if undo:
                    derived.pop(NIGHTS_MOVED_KEY, None)
                else:
                    derived[NIGHTS_MOVED_KEY] = {
                        "date": r["date"],
                        "at": datetime.now().isoformat(timespec="seconds"),
                    }
                changed = True
            after.append({
                "id": r["id"], "date": new_date.get(r["id"], r["date"]), "slot": r["slot"],
                "slot_state": r["slot_state"], "meal": r["meal"], "derived": derived,
                "changed": changed,
            })
        by_date_slot = {(e["date"], e["slot"]): e for e in after}
        by_id = {e["id"]: e for e in after}
        for e in after:
            if e["slot_state"] != "planned":
                continue
            links_to = (e["derived"].get("links_to") or "").strip()
            if not links_to:
                continue
            source = _resolve_leftover_source(links_to, by_date_slot, by_id)
            if source is None or source["id"] == e["id"]:
                continue
            if source["date"] >= e["date"]:
                # Named by where things ARE, not where the move would have
                # put them — the household is looking at the week as it
                # stands, and nothing has moved.
                dish = source["meal"] or e["meal"] or "That one"
                return _nights_swap_refusal(
                    f"{dish} on {_weekday_of(now_date[source['id']])} feeds "
                    f"{_weekday_of(now_date[e['id']])}’s {e['slot']} — it can’t move past that night.",
                    date_a, date_b)

        if dry_run:
            # Nothing has been written yet — the rollback only releases the
            # lock this call took, so on a caller's connection it must not
            # happen: it would throw away the caller's own work.
            if own_conn:
                conn.rollback()
            return {"status": "ok", "date_a": date_a, "date_b": date_b}

        # ---- write: the rows, then what their dates were holding up ----
        for r in moving:
            conn.execute(
                "UPDATE meal_plan_entries SET date = ? WHERE id = ? AND household_id = ?",
                (new_date[r["id"]], r["id"], household_id()),
            )
        for e in after:
            if e["changed"]:
                conn.execute(
                    "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                    (json.dumps(e["derived"]), e["id"], household_id()),
                )
        prep_moved = 0
        for r in moving:
            prep_moved += _shift_defrost_tasks(conn, weekly_plan_id, r["id"], r["date"], new_date[r["id"]])
        if own_conn:
            conn.commit()
    except Exception:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()

    moved = [
        {"entry_id": r["id"], "meal": r["meal"], "from": r["date"], "to": new_date[r["id"]]}
        for r in moving
    ]
    out = {
        "status": "restored" if undo else "swapped",
        "date_a": date_a,
        "date_b": date_b,
        "moved": moved,
        "prep_tasks_moved": prep_moved,
        "can_undo": not undo,
    }
    if not own_conn:
        # See the docstring: both of the fields below are reads, and the
        # caller's transaction is still open.
        return out
    # Both changed days in get_week_menu's own shape, so the screen
    # splices them in exactly as it does after an in-place swap.
    out["days"] = _menu_days_for(weekly_plan_id, [date_a, date_b])
    # Reported, never enforced — the same advisory a chat swap carries:
    # the dish is now in front of whoever is home THAT night.
    verdicts = []
    for m in moved:
        if not m["meal"]:
            continue
        verdict = _taste_verdict_for_slot(m["meal"], m["to"], "dinner")
        if verdict:
            verdicts.append({"date": m["to"], "meal": m["meal"], **verdict})
    if verdicts:
        out["taste_verdicts"] = verdicts
    return out


def _menu_days_for(weekly_plan_id: int, dates: list[str]) -> list[dict]:
    try:
        menu = get_week_menu(weekly_plan_id)
    except Exception:
        logger.exception("Could not re-read the week after moving a night")
        return []
    wanted = set(dates)
    return [d for d in (menu.get("days") or []) if d.get("date") in wanted]


def swap_dinner_nights(weekly_plan_id: int, date_a: str, date_b: str) -> dict:
    """
    Trade the dinners on two nights of a plan — "move Thursday's dinner to
    Friday" — keeping each dish's groceries, cooked tick and leftover
    chain with it, and moving its defrost reminders by the same number of
    days. The grocery list is not touched: same dishes, same lines.

    Returns `status` 'swapped' with `moved` (each entry's id, dish, from
    and to), `prep_tasks_moved`, both changed `days` in get_week_menu's
    shape, and `can_undo`; or `status` 'refused' with a plain `message`
    and nothing written — a night nobody is home, a dinner already cooked,
    or a leftover chain that would end up running backwards. Raises
    ValueError for a night that isn't on this plan, two identical nights,
    a component-based plan, or nights with no dinner at all.
    """
    return _apply_dinner_nights_swap(weekly_plan_id, date_a, date_b, undo=False)


def undo_dinner_nights_swap(weekly_plan_id: int, date_a: str, date_b: str) -> dict:
    """
    Put two nights' dinners back where they were before the last
    swap_dinner_nights of them. Only works while both nights still carry
    the token that swap wrote (derived_from.moved_from naming the other
    night); raises ValueError otherwise, so an Undo tapped after a second
    move can never quietly move the wrong dish.
    """
    return _apply_dinner_nights_swap(weekly_plan_id, date_a, date_b, undo=True)


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
        deleted = conn.execute(
            "DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (match["id"], household_id()),
        )
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
