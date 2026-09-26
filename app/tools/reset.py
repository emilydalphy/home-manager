"""
Self-service reset: starting a week's plan or the grocery list over.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id
from . import grocery as _grocery
from . import weekly_plan as _weekly_plan
from . import week_intake as _week_intake

# "This week's answers" was widened to also clear slot_attendance
# (guests/sheet/away_stretch) on 2026-09-25, then narrowed straight back on
# a THIRD review the same day: the schema can't support it safely.
# slot_attendance is one row per (date, slot) with a single last-writer
# `source` — a toggle overwritten by a later sheet save loses the toggle's
# own fact rather than layering under it, and the reverse loses the sheet's.
# A whole-household away stretch derives 'quick'/'ready_made' EDGE needs on
# neighbouring slots with no matching undo helper at all (see
# slot_needs.set_away_stretch — nothing in this codebase reverses those
# edges). And a hosting holiday writes BOTH a slot_attendance 'guests' row
# AND an intake night-tag ("guests") through the very same call chat's own
# guest-count gesture uses (holidays._set_hosting) — clearing attendance
# there half-undoes a holiday answer that itself is untouched, and
# clear_slot_attendance never calls big_meal.on_attendance_changed to keep
# a hosted menu in step. None of that is safe to paper over, so this option
# is narrow again: ONLY week_intake — the answers to the planning
# questions themselves (night tags, guest_counts, packed lunch days,
# skipped days, moods, cuisines, the freeform note) — never
# slot_attendance, slot_needs, away_stretches, or holiday_answers.


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

    intake_count is what the third "Start over" option, "This week's
    answers", would clear: whether this week's planning questions have an
    answer on file at all (tools.get_week_intake) — night tags, guest
    counts, packed lunch days, skipped days, moods, cuisines, the freeform
    note. Nothing else: never slot_attendance (who's in, guests, trips),
    never holiday_answers — see clear_week_answers's docstring for why
    (2026-09-25, third review) both are out of reach for this option.
    Keyed on plan["week_start_date"], the SAME value get_week_menu and
    generate_weekly_plan themselves read intake by (weekly_plan.py's
    get_week_menu: `_week_intake.get_week_intake(plan["week_start_date"])`;
    agent.py's generate_weekly_plan takes it as its own `week_start_date`
    parameter, distinct from the content_start_date/skip_days-derived
    range) — never plan_period's resolved start, which content_start_date
    can shift away from the row intake actually lives under. A household
    with no plan on file yet has no week for this option to name, so it
    answers 0 and the row stays disabled, same as the other two counts do
    at zero.

    intake_shared is True when this plan's PERIOD overlaps a second live
    plan's (find_overlapping_plans — date-range overlap, not equal
    week_start_date: a mid-week re-plan starts on a different day but
    still overlaps the approved week underneath it, 2026-09-25 review item
    C). week_intake is keyed by week_start rather than by plan id, so with
    two plans over the same days there is no telling, from the answer
    alone, which plan "this week's answers" is supposed to mean — clearing
    it would reach into the OTHER plan's week too. The dialog must hide or
    disable the option outright whenever this is True, never offer it
    against just this plan's own count.
    """
    conn = get_conn()
    plan = _resolve_plan(conn, weekly_plan_id)
    meal_count = 0
    week_label = None
    week_start_date = plan["week_start_date"] if plan else None
    intake_count = 0
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
    "This week's answers", the third "Start over" option: clears ONLY the
    week_intake row (tools.clear_week_intake) — the answers to the
    planning questions themselves (night tags, guest_counts, packed lunch
    days, skipped days, moods, cuisines, the freeform note). Never
    household setup (Preferences: meal_preferences, members, the kitchen
    kit), never another week's answers, and never — check_week_answers_
    clearable — a week that overlaps a second live plan; that case is
    refused outright rather than guessed at.

    Narrowed back to intake-only on a THIRD review the same day
    (2026-09-25), having briefly also cleared slot_attendance. The
    schema can't support clearing attendance safely, and no amount of
    care in HOW it's cleared changes that:

    - slot_attendance is one row per (date, slot) with a single
      last-writer `source` (schema.sql). A toggle overwritten by a later
      day-sheet save loses the toggle's own fact rather than layering
      under it, and the reverse loses the sheet's — there is no "clear
      just the sheet's part" once a second write has landed on the same
      slot.
    - A whole-household away stretch derives 'quick'/'ready_made' EDGE
      needs on the slots just outside its range (slot_needs.set_away_
      stretch's `_apply_edge`), and nothing in this codebase reverses an
      edge — clearing the stretch's own slots would leave those edges
      behind with no way back.
    - A hosting holiday answer writes a slot_attendance 'guests' row AND
      an intake night-tag ("guests")/guest_count through the exact same
      call chat's own guest-count gesture uses (holidays.py's
      _set_hosting → week_intake.save_week_intake) — indistinguishable,
      in the stored data, from a guests tag the household typed
      themselves (see this function's own note on that below). Clearing
      attendance for that date would leave the holiday half-undone
      (holiday_answers still says 'hosting') without calling
      big_meal.on_attendance_changed to keep its menu in step —
      clear_slot_attendance never does, because nothing told it a
      holiday, not an ordinary toggle, was behind the row.

    One known, disclosed gap even at this narrower scope: a hosting
    holiday's own write into week_intake (the 'guests' tag/guest_count
    above) is NOT distinguishable from an ordinary guests tag once it's
    in night_tags_json — there is no provenance field on an individual
    tag. So clearing this week's intake also removes a holiday-written
    guests tag, while holiday_answers itself keeps saying 'hosting' —
    the two fall out of step. Telling them apart cleanly would need a
    schema change (tagging which night_tags entries a holiday wrote);
    reported rather than guessed at.
    """
    check_week_answers_clearable(weekly_plan_id)
    conn = get_conn()
    plan = _resolve_plan(conn, weekly_plan_id)
    conn.close()
    if not plan:
        return {"week_start": None, "intake": None}
    intake_result = _week_intake.clear_week_intake(plan["week_start_date"])
    return {
        "week_start": plan["week_start_date"],
        "intake": intake_result,
    }
