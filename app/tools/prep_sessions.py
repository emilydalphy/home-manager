"""
Prep sessions — the work a prep day actually holds.

Emily, 2026-09-04 and again 2026-09-08: "I like to do some prep on Sunday
to make the week easier, make some things fresh during the week, and then
do another prep Wednesday/Thursday depending on the week."

A prep day is three kinds of work, and two of them already existed in this
codebase under other names:

  1. Batch-cooking a repeating dish — the breakfasts. That is a cook-ahead
     chain (cook_ahead.py / leftovers.py), and this module does not create
     one; it reads the chains whose SOURCE night falls on the prep day.
  2. A fridge move — thaw, marinate, soak. That is a prep_tasks row, dated
     by defrost.py's own lead-time arithmetic. This module deliberately
     does NOT re-date those: a defrost date is a food-safety answer, not a
     scheduling preference, and moving one to suit a prep day would be
     this module quietly overruling the module that knows why the date is
     what it is. It only LISTS the rows that already land on the day.
  3. Prep-cutting raw components — the salad ingredients for the bowls.
     Nothing produced those before, so this module adds the one new row
     type: prep_tasks with task_type='prep_cut' (see add_prep_cut).

So a session is a GATHERING, not a generator: everything in it is already
on the plan, and the session is the view that says "all of this happens
on Sunday." That is why there is no LLM anywhere in this file and no
guessing about what a household should batch — slice B is household-driven
(the Cook screen offers an entry's produce ingredients, the household ticks
what to cut), and the planner itself is deliberately unchanged.

The standing answer — which days these are — is a household rhythm fact
(rhythm.set_prep_days). The one-off "not this Sunday" is a flag on the
PLAN (weekly_plans.skip_prep_this_week), because it is true of one week
rather than of the household; see set_skip_prep_this_week below.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import leftovers as _leftovers
from . import recipes as _recipes
from . import rhythm as _rhythm
from . import weekly_plan as _weekly_plan

# What one item is worth when the household never told us how long their
# prep day runs. Deliberately crude, and only ever a fallback: a session
# with `minutes_planned` set uses that number instead, because the
# household's own answer beats arithmetic about it (see
# _session_minutes). A cook-ahead batch falls back to
# COOK_AHEAD_FALLBACK_MINUTES only when its dish has no saved recipe (a
# freeform meal) or a recipe with no times on it.
PREP_CUT_MINUTES = 10
FRIDGE_MOVE_MINUTES = 2
COOK_AHEAD_FALLBACK_MINUTES = 30

# task_type for the one row type this module creates. The other two kinds
# of item are read from rows other modules own — see the docstring.
PREP_CUT_TASK_TYPE = "prep_cut"


def _weekday_key(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A").lower()


def _plan_row(weekly_plan_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, week_start_date, content_start_date, day_count, skip_prep_this_week "
        "FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    conn.close()
    return row


def set_skip_prep_this_week(skip: bool = True, weekly_plan_id: int | None = None) -> dict:
    """
    Skip (or un-skip) prep for ONE plan, leaving the household's standing
    prep days exactly as they are — "I can't prep this Sunday" is a fact
    about this week, not a correction to how the household usually runs.

    Omit weekly_plan_id for the current plan, resolved the way every
    plan-scoped tool resolves it (_current_weekly_plan_row). A household
    with no plan at all is not an error: there is nothing to skip, so this
    reports weekly_plan_id=None and changes nothing.
    """
    conn = get_conn()
    if weekly_plan_id is None:
        row = _weekly_plan._current_weekly_plan_row(conn)
        if not row:
            conn.close()
            return {"weekly_plan_id": None, "skip_prep_this_week": bool(skip)}
        weekly_plan_id = row["id"]
    cur = conn.execute(
        "UPDATE weekly_plans SET skip_prep_this_week = ? WHERE id = ? AND household_id = ?",
        (1 if skip else 0, weekly_plan_id, household_id()),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    # A plan that is not this household's is left alone and says so, rather
    # than reporting a write that never happened.
    return {"weekly_plan_id": weekly_plan_id, "skip_prep_this_week": bool(skip) if changed else None, "changed": changed}


def add_prep_cut(weekly_plan_id: int, prep_date: str, description: str, entry_ids: list[int] | None = None) -> dict:
    """
    Record "cut this up on the prep day" — the raw-component half of a prep
    session, and the one row type this module creates.

    ONE ROW PER (description, entry), not one row covering several entries.
    prep_tasks.meal_plan_entry_id is singular (defrost uses it the same
    way), and a row per meal is what lets the session say what each cut
    actually feeds and compute `covers` honestly. Ticking "Cut up romaine
    · for Cobb Salad" and leaving "Cut up romaine · for the bowls" is a
    real state a person can be in, so it gets to be a real state here.

    Idempotent on (plan, date, description, entry): adding the same cut
    twice does not duplicate it, and does not reset a row someone already
    ticked — the same rule sync_defrost_tasks follows for its own rows.
    """
    description = (description or "").strip()
    if not description:
        raise ValueError("description is required — a prep-cut is a thing to do, said in words.")
    try:
        date.fromisoformat(prep_date)
    except (TypeError, ValueError):
        raise ValueError("prep_date must be an ISO date (YYYY-MM-DD).")

    conn = get_conn()
    plan = conn.execute(
        "SELECT id FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if plan is None:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")

    targets = [e for e in (entry_ids or []) if e is not None]
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    # An entry that isn't on this plan is dropped rather than refused: the
    # screen that sends these is looking at one plan's cards, so a stray id
    # means the plan moved under it, not that the whole tick was wrong.
    chosen = [by_id[e] for e in dict.fromkeys(targets) if e in by_id]
    if targets and not chosen:
        # Every id named was stray: the plan moved under the screen. Drop
        # the tick rather than filing an unattached cut nobody asked for.
        conn.close()
        return {"prep_task_id": None, "entry_id": None, "added": False, "dropped": True}

    created: list[dict] = []
    # No entries named at all is still a real prep-cut ("cut the onions"),
    # it just isn't attached to a meal.
    for entry in chosen or [None]:
        entry_id = entry["id"] if entry is not None else None
        related = (entry["meal"] or "") if entry is not None else ""
        existing = conn.execute(
            "SELECT id FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ? "
            # `IS` rather than `=` so the no-entry case (NULL) matches
            # itself — SQLite's `=` never matches NULL, which would make an
            # unattached prep-cut insert a fresh duplicate every tap.
            "AND task_type = ? AND task_date = ? AND description = ? AND meal_plan_entry_id IS ?",
            (weekly_plan_id, household_id(), PREP_CUT_TASK_TYPE, prep_date, description, entry_id),
        ).fetchone()
        if existing:
            created.append({"prep_task_id": existing["id"], "entry_id": entry_id, "added": False})
            continue
        cur = conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type, meal_plan_entry_id, quantity) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, '')",
            (household_id(), weekly_plan_id, prep_date, description, related, PREP_CUT_TASK_TYPE, entry_id),
        )
        created.append({"prep_task_id": cur.lastrowid, "entry_id": entry_id, "added": True})
    conn.commit()
    conn.close()
    return {
        "weekly_plan_id": weekly_plan_id,
        "task_date": prep_date,
        "description": description,
        "tasks": created,
        "added": sum(1 for c in created if c["added"]),
    }


def _period_dates(plan) -> list[str]:
    start, day_count = _weekly_plan.plan_period(plan)
    first = date.fromisoformat(start)
    return [(first + timedelta(days=i)).isoformat() for i in range(day_count)]


def _plan_entries(weekly_plan_id: int) -> dict[int, dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.cooked_status,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?
        """,
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    return {r["id"]: dict(r) for r in rows}


def _prep_task_rows(weekly_plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task_date, description, related_meal, status, task_type, meal_plan_entry_id, quantity "
        "FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ? "
        "ORDER BY task_date ASC, id ASC",
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _slot_word(slot: str, count: int) -> str:
    """"3 mornings" / "2 nights" — the meal of the day, said the way the
    Cook screen already says it (see cookSlotWord in shell.js)."""
    one = {"breakfast": "morning", "lunch": "lunch"}.get(slot, "night")
    if count == 1:
        return one
    return "lunches" if one == "lunch" else one + "s"


def _cook_ahead_items(plan_id: int, prep_date: str, entries: dict[int, dict]) -> list[dict]:
    """
    The batch-cook half: a chain whose SOURCE night is this prep day.
    "Egg White Bites for 3 mornings" — the cook day plus every day it
    covers, which is what a household actually stands at the counter for.

    Both kinds of chain count: one the household ticked (cook_ahead) and
    one the planner wrote (leftovers). The work on the prep day is the
    same batch either way; only the words for the days AFTER it differ,
    and those belong to the cards for those days, not to this session.
    """
    recipes_by_name = {r["name"].lower(): r for r in _recipes.list_recipes()}
    items = []
    for source in _leftovers.plan_leftover_chains(plan_id)["sources"].values():
        if source["date"] != prep_date:
            continue
        entry = entries.get(source["entry_id"]) or {}
        days = [source["date"]] + [t["date"] for t in source["targets"]]
        recipe = recipes_by_name.get((source["meal"] or "").strip().lower())
        minutes = 0
        if recipe:
            minutes = (recipe["prep_time_minutes"] or 0) + (recipe["cook_time_minutes"] or 0)
        items.append({
            "kind": "cook_ahead",
            # The item is the COOK, so it is checked off by cooking it —
            # the Cook screen opens the recipe rather than offering a box
            # this module would have to keep in sync with cooked_status.
            "prep_task_id": None,
            "entry_id": source["entry_id"],
            "title": f"{source['meal']} for {len(days)} {_slot_word(source['slot'], len(days))}",
            "feeds": source["meal"],
            "done": (entry.get("cooked_status") == "done"),
            "covers": sorted(set(days)),
            "minutes": minutes or COOK_AHEAD_FALLBACK_MINUTES,
        })
    items.sort(key=lambda i: (i["title"].lower(), i["entry_id"]))
    return items


def _task_items(tasks: list[dict], prep_date: str, entries: dict[int, dict]) -> list[dict]:
    """
    The two prep_tasks halves: fridge moves (defrost's own rows, and any
    general prep the schedule dated here) and prep-cuts. Rows are taken
    exactly as they are dated — nothing here re-dates a defrost move, for
    the reason in the module docstring.
    """
    items = []
    for task in tasks:
        if task["task_date"] != prep_date:
            continue
        entry = entries.get(task["meal_plan_entry_id"]) if task["meal_plan_entry_id"] else None
        is_cut = task["task_type"] == PREP_CUT_TASK_TYPE
        covers = [entry["date"]] if entry else [task["task_date"]]
        items.append({
            "kind": "prep_cut" if is_cut else "fridge_move",
            "prep_task_id": task["id"],
            "entry_id": task["meal_plan_entry_id"],
            "title": task["description"],
            "feeds": task["related_meal"] or (entry["meal"] if entry else ""),
            "done": task["status"] == "done",
            "covers": sorted(set(covers)),
            "minutes": PREP_CUT_MINUTES if is_cut else FRIDGE_MOVE_MINUTES,
        })
    return items


def prep_sessions_for_plan(weekly_plan_id: int) -> list[dict]:
    """
    One session per prep day that actually falls inside this plan's period.

    Returns [] — not an error, and not a placeholder session — for a
    household that has never answered the prep-days question, for a plan
    whose period contains none of their prep days, and for a plan the
    household has skipped prep on this week (skip_prep_this_week). All
    three are "there is no prep session here", and the Cook screen says
    nothing rather than showing an empty one.

    Each session:
      {date, weekday, minutes_planned, note, items, covers,
       items_done, items_total, total_minutes_estimate}

    `covers` is computed from what the items actually feed — a batch's
    chain targets and the meals a cut or a fridge move is for — never from
    the prep day plus a fixed window, so "covers Mon–Wed" is a claim about
    this plan rather than a guess about a week.
    """
    plan = _plan_row(weekly_plan_id)
    if plan is None or plan["skip_prep_this_week"]:
        return []

    prep_days = _rhythm.get_household_rhythm()["prep_days"]
    if not prep_days:
        return []

    dates = _period_dates(plan)
    by_weekday: dict[str, str] = {}
    for d in dates:
        by_weekday.setdefault(_weekday_key(d), d)

    entries = _plan_entries(weekly_plan_id)
    tasks = _prep_task_rows(weekly_plan_id)

    sessions = []
    for prep_day in prep_days:
        prep_date = by_weekday.get(prep_day.get("weekday") or "")
        if not prep_date:
            continue  # that weekday isn't in this plan's period at all
        items = _cook_ahead_items(weekly_plan_id, prep_date, entries) + _task_items(tasks, prep_date, entries)
        if not items:
            continue  # a prep day with nothing on it is not a session yet
        covers = sorted({d for item in items for d in item["covers"]})
        minutes_planned = prep_day.get("minutes")
        sessions.append({
            "date": prep_date,
            "weekday": date.fromisoformat(prep_date).strftime("%A"),
            "minutes_planned": minutes_planned,
            "note": prep_day.get("note"),
            "items": items,
            "covers": covers,
            "items_done": sum(1 for i in items if i["done"]),
            "items_total": len(items),
            # The household's own answer beats arithmetic about it: if they
            # said "about an hour", the session is about an hour, however
            # the items happen to add up.
            "total_minutes_estimate": minutes_planned or sum(i["minutes"] for i in items),
        })
    sessions.sort(key=lambda s: s["date"])
    return sessions


def has_prep_days() -> bool:
    """
    Whether the household has answered the prep-days question at all.

    The Cook screen needs this because "no sessions" has two very
    different meanings: a household that never told us gets the quiet
    offer to say so, and a household that DID tell us and simply has a
    quiet prep day gets nothing at all — nagging them to set days they
    already set would be the app not listening.
    """
    return bool(_rhythm.get_household_rhythm()["prep_days"])


def prep_sessions_for_current_plan() -> list[dict]:
    """prep_sessions_for_plan for whichever plan is current — the shape
    every plan-scoped tool takes when weekly_plan_id is omitted."""
    conn = get_conn()
    row = _weekly_plan._current_weekly_plan_row(conn)
    conn.close()
    return prep_sessions_for_plan(row["id"]) if row else []
