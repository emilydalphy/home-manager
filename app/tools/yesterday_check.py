"""
"Did you have it?" — Today's card the morning after (Loop Board "Today:
next morning, ask 'Did you have it?' about any meal left unticked",
Emily 2026-09-25, the bottom of the "Bring it over" mockup).

The monitor phase of the mental-load model: a meal nobody ticked cooked
is a question the app should close, not the household. The morning after
a day with any unticked meal or snack, Today shows one small card —
"YESTERDAY", then "<Meal>. Did you have it?" — one row per meal, each
with two answers:

- **We had it** is exactly the cooked tick: cooker.check_off_meal(entry,
  'done'). cooked_status done, the batch's ingredients out of inventory
  at most once, times_cooked / last_cooked_date moved once. No second
  path to "cooked" is written here.
- **We skipped it** stamps meal_plan_entries.skipped_at. cooked_status
  stays 'pending' ON PURPOSE: every reader of that column (moves, the
  week's progress, meal_variety, typed_requests, prep_sessions, recipes'
  last-cooked, usage, coordination, memory, bring_over, shell.js) asks
  "is it done?" and a skipped meal is not, so none of them has to learn a
  third value. In particular bring_over.last_week_uncooked selects
  cooked_status = 'pending', so a skipped dinner or lunch is still offered
  on next week's "Same as last week?" — which is the point of skipping.
  The stamp's only reader is this module: it is what makes a row
  "answered" so the card never asks about it again.

Asked once: answering removes the row, and the card only ever looks at
the household's yesterday (cooker.household_today — never the server's
clock), so an ignored card is gone when the household's day turns over,
and the meal stays exactly as it was. No second nudge, no count.

What is asked about: yesterday's planned slots (any slot, snacks
included) on the APPROVED plan covering yesterday, that are a real dish
and not yet done or skipped. Not a reheat — a confirmed leftovers chain
target, a derived_from.links_to night, a freezer portion, or a freeform
"leftovers"/takeout line (the same test bring_over uses, so the two lists
agree about what a dish is). Not a component-based plan's rows (they
carry no day). A dish on several of yesterday's slots is one row per
slot — each is its own meal.
"""
from __future__ import annotations

import datetime

from ..db import get_conn
from ._shared import household_id
from . import bring_over as _bring_over
from . import cooker as _cooker
from . import leftovers as _leftovers
from . import weekly_plan as _weekly_plan

ANSWERS = ("had", "skipped")


class NotAskedAbout(ValueError):
    """The entry isn't one of the rows the card is asking about right now —
    already answered, not yesterday's, a reheat, or no such meal. Its own
    type so the route can answer 409 (the screen is out of date) rather
    than a 500."""


def _plan_covering(conn, day: str):
    """The approved plan whose period holds `day`. Approved only, as
    bring_over's "last week" is: a draft nobody approved was never the
    household's food, so there is nothing to ask about it."""
    return conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND status = 'approved' "
        f"AND date({_weekly_plan._SQL_PERIOD_START}) <= date(?) "
        f"AND date({_weekly_plan._SQL_PERIOD_START}, '+' || {_weekly_plan._SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY created_at DESC, id DESC LIMIT 1",
        (household_id(), day, day),
    ).fetchone()


def _unanswered(conn, day: str) -> list[dict]:
    plan = _plan_covering(conn, day)
    if plan is None:
        return []
    rows = conn.execute(
        f"""
        SELECT mpe.id, mpe.slot, mpe.recipe_id, mpe.freeform_meal, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ? AND mpe.date = ?
          AND mpe.slot_state = 'planned' AND mpe.cooked_status != 'done'
          AND mpe.skipped_at IS NULL AND mpe.component_category IS NULL
        ORDER BY {_weekly_plan.slot_order_sql('mpe.slot')}, mpe.id
        """,
        (household_id(), plan["id"], day),
    ).fetchall()
    if not rows:
        return []
    chains = _leftovers.plan_leftover_chains(plan["id"], conn=conn)
    out = []
    for r in rows:
        meal = (r["meal"] or "").strip()
        if not meal or r["id"] in chains["leftovers"]:
            continue
        derived = _bring_over._derived(r["derived_from_json"])
        if derived.get("links_to") or derived.get(_leftovers.FROM_FREEZER_KEY):
            continue
        if not r["recipe_id"] and _bring_over._NOT_A_DISH.search(r["freeform_meal"] or ""):
            continue
        out.append({"entry_id": r["id"], "meal": meal, "slot": r["slot"]})
    return out


def yesterday_check(today: datetime.date | None = None) -> dict:
    """
    What Today's "Did you have it?" card asks about.

    Returns {"date": <yesterday, ISO>, "meals": [{"entry_id", "meal",
    "slot"}]} — meals in eating order, [] when there is nothing to ask
    (and the card draws nothing). `today` is for tests; the app passes
    nothing and gets the household's own today.
    """
    today = today or _cooker.household_today()
    day = (today - datetime.timedelta(days=1)).isoformat()
    conn = get_conn()
    try:
        meals = _unanswered(conn, day)
    finally:
        conn.close()
    return {"date": day, "meals": meals}


def answer_yesterday(entry_id: int, answer: str, today: datetime.date | None = None) -> dict:
    """
    One row's answer. 'had' is the cooked tick itself (check_off_meal);
    'skipped' stamps skipped_at and leaves cooked_status alone (see the
    module docstring). Either way the row is answered and the card stops
    asking about it. Returns the fresh card plus `meal` (the dish's name,
    for the toast) and `answer`.

    Only a row the card is asking about right now can be answered — never
    an arbitrary entry, and never a day other than yesterday (NotAskedAbout).
    """
    if answer not in ANSWERS:
        raise ValueError(f"The answer is 'had' or 'skipped', not {answer!r}.")
    today = today or _cooker.household_today()
    current = yesterday_check(today)
    row = next((m for m in current["meals"] if m["entry_id"] == entry_id), None)
    if row is None:
        raise NotAskedAbout("That meal isn't one I'm asking about any more.")
    if answer == "had":
        _cooker.check_off_meal(entry_id, "done")
    else:
        conn = get_conn()
        conn.execute(
            "UPDATE meal_plan_entries SET skipped_at = datetime('now') "
            "WHERE id = ? AND household_id = ? AND skipped_at IS NULL",
            (entry_id, household_id()),
        )
        conn.commit()
        conn.close()
    fresh = yesterday_check(today)
    fresh["meal"] = row["meal"]
    fresh["answer"] = answer
    return fresh
