"""
Self-service reset: starting a week's plan or the grocery list over.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id
from . import grocery as _grocery
from . import weekly_plan as _weekly_plan
from . import week_intake as _week_intake

# The slot_attendance.source values a WEEK'S QUESTIONS write, as opposed to
# a standalone gesture elsewhere in the app: 'guests' (the "Hosting guests"
# night-tag chip), 'sheet' (the day-attendance sheet's Done), 'away_stretch'
# (the trip picker). NOT 'toggle' (a single presence-avatar tap on a day
# card — a standalone gesture, not one of the week's own questions) and NOT
# 'chat' (Emily's deepened model, schema.sql on slot_attendance: chat is
# the permanent "Corrections" layer, one step past the weekly "Exceptions"
# these questions write — never what "This week's answers" means to clear).
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

    intake_count/attendance_count/holiday_count are what the third "Start
    over" option, "This week's answers", would clear: whether this week's
    planning questions have an answer on file at all
    (tools.get_week_intake), how many of this week's days carry a
    day-attendance-sheet/guests-chip/away-stretch answer
    (slot_attendance.source — see _WEEK_QUESTION_ATTENDANCE_SOURCES; never
    a standalone toggle or a chat correction), and how many of this week's
    holidays have been answered (holiday_answers). All three ride on the
    same plan's week_start_date as the other two counts, so they are the
    SAME week the dialog is about. A household with no plan on file yet
    (week_start_date is None) has no week for this option to name, so all
    three answer 0 and the row stays disabled, same as the other two
    counts do at zero.

    intake_shared is True when week_start_date is also the OTHER live
    plan's week — a draft generated over an already-approved week, or the
    reverse (see _week_intake_is_shared). week_intake, slot_attendance and
    holiday_answers are all keyed by date/week_start, never by plan id, so
    clearing "this week's answers" in that state would reach into the
    OTHER plan's week too — the rush caps and away days an approved week
    may still be running on. The dialog must hide or disable the option
    outright whenever this is True, never offer it against just this
    plan's own counts.
    """
    conn = get_conn()
    plan = _resolve_plan(conn, weekly_plan_id)
    meal_count = 0
    week_label = None
    week_start_date = plan["week_start_date"] if plan else None
    if plan:
        meal_count = conn.execute(
            "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (plan["id"], household_id()),
        ).fetchone()["n"]
        start, days = _weekly_plan.plan_period(plan)
        week_label = _weekly_plan._format_period_range(start, days)
    grocery_count = conn.execute(
        "SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ? AND status = 'needed'",
        (household_id(),),
    ).fetchone()["n"]

    intake_count = 0
    attendance_count = 0
    holiday_count = 0
    intake_shared = False
    if plan and week_start_date:
        intake_shared = _week_intake_is_shared(conn, week_start_date, plan["id"])
        if not intake_shared:
            intake_count = 1 if _week_intake.get_week_intake(week_start_date) else 0
            dates = _week_dates_for_plan(plan)
            marks = ",".join("?" * len(dates))
            source_marks = ",".join("?" * len(_WEEK_QUESTION_ATTENDANCE_SOURCES))
            attendance_count = conn.execute(
                f"SELECT COUNT(*) AS n FROM slot_attendance WHERE household_id = ? "
                f"AND date IN ({marks}) AND source IN ({source_marks})",
                (household_id(), *dates, *_WEEK_QUESTION_ATTENDANCE_SOURCES),
            ).fetchone()["n"]
            holiday_count = conn.execute(
                f"SELECT COUNT(*) AS n FROM holiday_answers WHERE household_id = ? AND date IN ({marks})",
                (household_id(), *dates),
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
        "holiday_count": holiday_count,
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


def _week_dates_for_plan(plan) -> list[str]:
    """The calendar dates this week's questions cover — the same days the
    intake, the day-attendance sheet, the away-stretch picker and the
    holiday card on /plan-week all ask about for this plan. week_start_date
    (not plan_period's resolved start, which content_start_date can shift)
    is the value startPlanningWeek, tryAgain and the intake route already
    agree means "this week" — see get_reset_preview's own week_start_date."""
    _, day_count = _weekly_plan.plan_period(plan)
    return _week_intake.period_dates(plan["week_start_date"], day_count)


def _week_intake_is_shared(conn, week_start_date: str, plan_id: int) -> bool:
    """True when more than one LIVE (non-retired) plan shares this
    week_start_date — a draft generated over an already-approved week, or
    the reverse. week_intake, slot_attendance and holiday_answers are all
    keyed by date/week_start rather than by plan id, so with two plans
    sharing a week there is no telling, from the answers alone, which
    plan "this week's answers" is supposed to mean — clearing them would
    reach into the other plan's week too. `plan_id` is unused in the
    count on purpose: even the plan passed in counts itself, so a lone
    plan on its own week answers False and a genuine pair (whichever one
    is showing) answers True."""
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM weekly_plans WHERE household_id = ? AND week_start_date = ? AND status != 'retired'",
        (household_id(), week_start_date),
    ).fetchone()["n"]
    return n > 1


def clear_week_answers(weekly_plan_id: int | None = None) -> dict:
    """
    "This week's answers", the third "Start over" option: clears the
    intake (tools.clear_week_intake), the day-attendance-sheet/guests-chip/
    away-stretch attendance rows, and the holiday answers — everything
    THIS WEEK'S QUESTIONS wrote, and nothing else. Never household setup
    (Preferences: meal_preferences, members, the kitchen kit), never a
    standalone attendance toggle or a chat correction (see
    _WEEK_QUESTION_ATTENDANCE_SOURCES), never another week's answers, and
    never — see _week_intake_is_shared — a week shared with a second live
    plan; that case is refused outright rather than guessed at.

    Away_stretches rows themselves are left alone: they are the trip's own
    descriptive record (member_ids_json, its reason), often spanning past
    this one week, and deleting one to match a partial in-range clear
    would take a bite out of a trip that continues into next week. Only
    the derived slot_attendance rows dated inside THIS week move; a
    stretch that also covers next week keeps its rows there untouched.
    """
    conn = get_conn()
    plan = _resolve_plan(conn, weekly_plan_id)
    if not plan:
        conn.close()
        return {"week_start": None, "intake": None, "attendance_cleared": 0, "holidays_cleared": 0}
    week_start_date = plan["week_start_date"]
    if _week_intake_is_shared(conn, week_start_date, plan["id"]):
        conn.close()
        raise ValueError(
            "This week's answers are shared with another plan on the same week — refusing to clear them."
        )
    dates = _week_dates_for_plan(plan)
    marks = ",".join("?" * len(dates))
    source_marks = ",".join("?" * len(_WEEK_QUESTION_ATTENDANCE_SOURCES))
    attendance_cleared = conn.execute(
        f"DELETE FROM slot_attendance WHERE household_id = ? AND date IN ({marks}) AND source IN ({source_marks})",
        (household_id(), *dates, *_WEEK_QUESTION_ATTENDANCE_SOURCES),
    ).rowcount
    holidays_cleared = conn.execute(
        f"DELETE FROM holiday_answers WHERE household_id = ? AND date IN ({marks})",
        (household_id(), *dates),
    ).rowcount
    conn.commit()
    conn.close()
    intake_result = _week_intake.clear_week_intake(week_start_date)
    return {
        "week_start": week_start_date,
        "intake": intake_result,
        "attendance_cleared": attendance_cleared,
        "holidays_cleared": holidays_cleared,
    }
