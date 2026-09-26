"""
Self-service reset: starting a week's plan or the grocery list over.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id
from . import attendance as _attendance
from . import grocery as _grocery
from . import weekly_plan as _weekly_plan
from . import week_intake as _week_intake

# The slot_attendance.source values a WEEK'S QUESTIONS write: 'guests' (the
# "Hosting guests" night-tag chip on /plan-week AND chat's set_guest_count,
# which passes this same default — see attendance.set_guest_count), 'sheet'
# (the day-attendance sheet's Done, attendance.set_day_attendance), and
# 'away_stretch' (the trip picker AND chat's set_away_stretch, via
# attendance.remove_members_from_slot's own default). There is no 'chat'
# source in this codebase — a correction told to chat lands under one of
# these same three, same as the screen's own gesture would. So clearing
# "This week's answers" also clears a guest count or a trip told to chat
# THIS week, which is the honest reading of "this week's answers": the
# household said it about this week, however it said it. NOT 'toggle' (a
# single presence-avatar tap on a day card — a standalone gesture, never
# one of the week's own questions).
_WEEK_QUESTION_ATTENDANCE_SOURCES = ("guests", "sheet", "away_stretch")


# "This week needs a do-over" without going through chat and without
# touching anything else the household owns. Deliberately narrow: recipes,
# chores, members, inventory and the household's own memory are all out of
# scope here — wiping those is reset_household.py, an admin script that is
# not meant for regular use and has no in-app entry point on purpose.
def clear_weekly_plan(weekly_plan_id: int | None = None) -> dict:
    """
    Take every meal off a week's plan at once — the whole-plan version of
    un-planning a single meal. Defaults to the household's current plan
    (see _current_weekly_plan_row), same as every other plan-scoped tool
    that takes an optional weekly_plan_id.

    Each entry's grocery contribution is reversed first, one meal at a
    time, through the same _reverse_meal_grocery_contributions() call
    swap_meal_in_plan already makes — so the list is left holding only
    what's still actually planned or was asked for directly, rather than a
    week's worth of orphaned ingredients. That helper leaves anything
    already moved to in_cart/purchased alone (the shopper has acted on it),
    which is the behaviour wanted here too: clearing the plan shouldn't
    yank something out of a cart mid-trip.

    The weekly_plans row is emptied, not deleted. The week's dates and its
    constraints_notes ("out Thursday, keep it under 30 minutes") survive,
    so re-planning the same week fills this plan back in instead of
    stranding an empty one beside a new one for _current_weekly_plan_row to
    choose between. Its status drops back to 'draft' — an empty week isn't
    an approved one. Prep tasks go with the meals, since they only describe
    prepping meals that no longer exist.
    """
    conn = get_conn()
    if weekly_plan_id is None:
        plan = _weekly_plan._current_weekly_plan_row(conn)
    else:
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
        if not plan:
            conn.close()
            raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    if not plan:
        conn.close()
        return {
            "weekly_plan_id": None, "week_start_date": None, "meals_cleared": 0,
            "removed_items": [], "trimmed_items": [], "prep_tasks_cleared": 0,
        }
    weekly_plan_id = plan["id"]
    week_start_date = plan["week_start_date"]
    entry_ids = [
        row["id"] for row in conn.execute(
            "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchall()
    ]
    conn.close()

    removed_items = []
    trimmed_items = []
    for entry_id in entry_ids:
        reversal = _grocery._reverse_meal_grocery_contributions(entry_id)
        removed_items.extend(reversal["removed_items"])
        trimmed_items.extend(reversal["trimmed_items"])

    conn = get_conn()
    # Prep rows keyed to this plan's entries go too, wherever they were
    # dated — a big meal's make-ahead rows sit on the days before, which
    # can belong to another plan (app/tools/big_meal.py). get_prep_schedule
    # would hide them once the entries are gone; the table stays honest
    # instead of relying on that.
    marks = ",".join("?" * len(entry_ids))
    by_entry = conn.execute(
        f"DELETE FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id IN ({marks})",
        (household_id(), *entry_ids),
    ).rowcount if entry_ids else 0
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    prep = conn.execute(
        "DELETE FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    prep_tasks_cleared = prep.rowcount + by_entry
    conn.execute(
        "UPDATE weekly_plans SET status = 'draft', updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {
        "weekly_plan_id": weekly_plan_id,
        "week_start_date": week_start_date,
        "meals_cleared": len(entry_ids),
        "removed_items": removed_items,
        "trimmed_items": trimmed_items,
        "prep_tasks_cleared": prep_tasks_cleared,
    }


def get_reset_preview(weekly_plan_id: int | None = None) -> dict:
    """
    What a reset would actually remove, counted before anything happens, so
    the confirm dialog can say "12 planned meals and 23 grocery items"
    instead of asking the household to agree to an unspecified wipe — and
    can disable a choice that would do nothing. Read-only.

    `weekly_plan_id` is the plan the Plan tab is SHOWING, and the dialog
    must pass it (Loop Board, 2026-09-13). The tab pins itself to the week
    the household is working on (weekState.showWeekStart in shell.js); the
    default resolver below answers "the plan covering today" instead, and
    on a Sunday those are two different plans. Emily had just approved
    Mon–Sun, tapped Start over, and the reset cleared last week's dying
    draft (10 meals) while the approved week kept every meal and lost its
    groceries to the list clear. The preview names the week it counted
    (`week_label`) so the dialog can say which plan is about to go.

    grocery_count counts what clear_grocery_list('needed') would delete,
    which is the whole list as the Grocery screen means it: everything
    still to buy, whether a meal plan put it there or a person did.
    Anything already in a cart or bought stays, and isn't counted here.

    intake_count/attendance_count are what the third "Start over" option,
    "This week's answers", would clear: whether this week's planning
    questions have an answer on file at all (tools.get_week_intake), and
    how many of this week's days carry a day-attendance-sheet/guests/
    away-stretch answer (slot_attendance.source — see
    _WEEK_QUESTION_ATTENDANCE_SOURCES; never a standalone toggle). Holiday
    answers are deliberately NOT part of this (2026-09-25 review, item B):
    a holiday is also answered from the Today card and in chat, and
    undoing what a 'hosting'/'out' answer already did to the plan is a
    separate decision this option doesn't make. Both counts are read over
    the plan's actual PERIOD (plan_period — content_start_date/day_count
    aware, never the bare week_start_date column, which a shrunk or
    shifted period can leave stale), so a shrunk plan's preview names only
    its own days. A household with no plan on file yet has no week for
    this option to name, so both answer 0 and the row stays disabled, same
    as the other two counts do at zero.

    intake_shared is True when this plan's PERIOD overlaps a second live
    plan's (find_overlapping_plans — date-range overlap, not equal
    week_start_date: a mid-week re-plan starts on a different day but
    still overlaps the approved week underneath it, 2026-09-25 review item
    C). week_intake and slot_attendance are keyed by date/week_start rather
    than by plan id, so with two plans over the same days there is no
    telling, from the answers alone, which plan "this week's answers" is
    supposed to mean — clearing them would reach into the OTHER plan's
    days too. The dialog must hide or disable the option outright whenever
    this is True, never offer it against just this plan's own counts.
    """
    conn = get_conn()
    plan = _resolve_plan(conn, weekly_plan_id)
    meal_count = 0
    week_label = None
    week_start_date = plan["week_start_date"] if plan else None
    intake_count = 0
    attendance_count = 0
    intake_shared = False
    if plan:
        meal_count = conn.execute(
            "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (plan["id"], household_id()),
        ).fetchone()["n"]
        start, days = _weekly_plan.plan_period(plan)
        week_label = _weekly_plan._format_period_range(start, days)
        intake_shared = bool(_weekly_plan.find_overlapping_plans(start, days, exclude_plan_id=plan["id"]))
        if not intake_shared:
            intake_count = 1 if _week_intake.get_week_intake(week_start_date) else 0
            dates = _week_intake.period_dates(start, days)
            marks = ",".join("?" * len(dates))
            source_marks = ",".join("?" * len(_WEEK_QUESTION_ATTENDANCE_SOURCES))
            attendance_count = conn.execute(
                f"SELECT COUNT(*) AS n FROM slot_attendance WHERE household_id = ? "
                f"AND date IN ({marks}) AND source IN ({source_marks})",
                (household_id(), *dates, *_WEEK_QUESTION_ATTENDANCE_SOURCES),
            ).fetchone()["n"]
    grocery_count = conn.execute(
        "SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ? AND status = 'needed'",
        (household_id(),),
    ).fetchone()["n"]
    conn.close()
    return {
        "weekly_plan_id": plan["id"] if plan else None,
        "week_start_date": week_start_date,
        "week_label": week_label,
        "plan_status": plan["status"] if plan else None,
        "meal_count": meal_count,
        "grocery_count": grocery_count,
        "intake_count": intake_count,
        "attendance_count": attendance_count,
        "intake_shared": intake_shared,
    }


def _resolve_plan(conn, weekly_plan_id: int | None):
    """The named plan, or the default resolver's answer when none is named.
    A named plan another household owns, or none at all, is None — a
    reset never falls back to some other plan than the one it was told."""
    if weekly_plan_id is None:
        return _weekly_plan._current_weekly_plan_row(conn)
    return conn.execute(
        "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()


def check_week_answers_clearable(weekly_plan_id: int | None = None) -> None:
    """
    Read-only half of clear_week_answers's guard: raises ValueError with
    nothing touched when this plan's period overlaps a second live plan,
    the same test clear_week_answers itself runs right before it deletes
    anything. A no-op (never raises) when there's no plan to name.

    Exists so POST /api/reset can check this BEFORE clearing the meal plan
    or the grocery list (2026-09-25 review, item F): those two are their
    own separate, already-committed writes, so if the guard only fired
    inside clear_week_answers — run last — a refused week_answers clear
    would leave the meal plan and the grocery list already gone. Calling
    this first means a refusal here refuses the WHOLE request, before any
    of the three has touched anything.
    """
    conn = get_conn()
    plan = _resolve_plan(conn, weekly_plan_id)
    if not plan:
        conn.close()
        return
    start, days = _weekly_plan.plan_period(plan)
    overlapping = _weekly_plan.find_overlapping_plans(start, days, exclude_plan_id=plan["id"])
    conn.close()
    if overlapping:
        raise ValueError(
            "This week overlaps another plan — refusing to clear its answers."
        )


def clear_week_answers(weekly_plan_id: int | None = None) -> dict:
    """
    "This week's answers", the third "Start over" option: clears the
    intake (tools.clear_week_intake) and the day-attendance-sheet/guests/
    away-stretch attendance for this plan's actual period — everything
    THIS WEEK'S QUESTIONS wrote, and nothing else. Never household setup
    (Preferences: meal_preferences, members, the kitchen kit), never a
    standalone attendance toggle, never another week's answers, never a
    holiday answer (2026-09-25 review item B — see get_reset_preview's
    docstring for why), and never — check_week_answers_clearable — a week
    that overlaps a second live plan; that case is refused outright rather
    than guessed at.

    Attendance is cleared through attendance.clear_slot_attendance for
    each (date, slot) this week actually holds one of the three sources —
    never a raw DELETE. clear_slot_attendance's own _sync_away_need then
    runs: an 'away' need this attendance produced (whether from the
    guests chip pulling everyone present, the day sheet, or an
    away_stretch's derived edges — 'quick'/'ready_made' included, since
    those are slot_needs entries keyed to the SAME slot_attendance rows)
    is undone the same way un-toggling a presence avatar undoes it — the
    need is restored or reopened, and a meal already converted to
    planned_empty is handed back as an open decision. A raw DELETE skipped
    all of that: the need stayed 'away', the meal stayed planned_empty and
    off the list, while attendance itself said everyone was home.

    The away_stretches rows themselves are left alone. Nothing in this
    codebase reads that table back for display or behaviour — it exists
    only as the trip's own descriptive record (member_ids_json, its
    reason), stamped onto the slot_attendance rows it produced via
    away_stretch_id — so there is no "the stretch itself" to remove
    independent of those rows, and a stretch spanning past this week
    keeps its OTHER week's slot_attendance rows (and needs) exactly as
    they are; only the ones dated inside THIS week move.
    """
    check_week_answers_clearable(weekly_plan_id)
    conn = get_conn()
    plan = _resolve_plan(conn, weekly_plan_id)
    if not plan:
        conn.close()
        return {"week_start": None, "intake": None, "attendance_cleared": 0}
    week_start_date = plan["week_start_date"]
    start, days = _weekly_plan.plan_period(plan)
    dates = _week_intake.period_dates(start, days)
    marks = ",".join("?" * len(dates))
    source_marks = ",".join("?" * len(_WEEK_QUESTION_ATTENDANCE_SOURCES))
    targets = conn.execute(
        f"SELECT date, slot FROM slot_attendance WHERE household_id = ? "
        f"AND date IN ({marks}) AND source IN ({source_marks})",
        (household_id(), *dates, *_WEEK_QUESTION_ATTENDANCE_SOURCES),
    ).fetchall()
    targets = [(row["date"], row["slot"]) for row in targets]
    conn.close()
    for date_str, slot in targets:
        _attendance.clear_slot_attendance(date_str, slot)
    intake_result = _week_intake.clear_week_intake(week_start_date)
    return {
        "week_start": week_start_date,
        "intake": intake_result,
        "attendance_cleared": len(targets),
    }
