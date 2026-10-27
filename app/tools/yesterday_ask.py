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
import json

from ..db import get_conn, write
from ._shared import household_id
from . import bring_over as _bring_over
from . import cooker as _cooker
from . import leftovers as _leftovers
from . import plan_undo as _plan_undo
from . import weekly_plan as _weekly_plan

ANSWERS = ("had", "skipped")

# On the empty night a skipped double-batch cook leaves behind (see
# _skip_moving_the_cook): what the night was, for anyone reading it later.
SKIPPED_MOVED_CONSTRAINT = "skipped_moved_on"


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
    moved = None
    if answer == "had":
        _cooker.check_off_meal(entry_id, "done")
    else:
        moved = _skip(entry_id, row["meal"], current["date"], today.isoformat())
    fresh = yesterday_check(today)
    fresh["meal"] = row["meal"]
    fresh["answer"] = answer
    if moved:
        # {"date", "slot", "label"} — where the dish is now cooked — plus
        # the Undo's handle and any fridge move that has to start now.
        fresh["moved_to"] = {k: moved[k] for k in ("date", "slot", "label")}
        fresh["undo_entry_id"] = moved["undo_entry_id"]
        fresh["can_undo"] = True
        if moved.get("thaw_now"):
            fresh["thaw_now"] = moved["thaw_now"]
    return fresh


# Where a moved skip's undo record lives: on the planned_empty row it
# leaves on yesterday (plan_undo's holder), as the night off keeps its own.
SKIPPED_UNDO_KEY = "skipped_moved_undo"


def _skip(entry_id: int, meal: str, day: str, today: str) -> dict | None:
    """
    "We skipped it". For an ordinary meal: stamp skipped_at (the module
    docstring). For a cook that was making a double batch for later
    nights, the dish moves to the first of them instead (Emily,
    2026-10-12, option A: "the dish moves to the leftovers night"):
    Friday's tacos cooked double for Saturday, skipped on Friday, are
    Saturday's dinner — not "Tacos, leftovers from Friday", promising a
    batch nobody cooked, on Today, the morning text and the reheat move.

    The move is weekly_plan.move_cook_onto_fed_night, the night off's and
    the Review stepper's own: Saturday's leftovers row goes, the cook lands
    on it (its shopping with it — the ledger rows are the cook entry's), a
    later fed night re-points at Saturday, and the fridge moves travel. The
    chain's bookkeeping is the stepper's: Saturday is no longer a night
    the batch feeds. The batch is NOT kept double with a freezer portion
    the way a night off keeps it — Friday's share was never cooked, so
    there is no portion; it is ingredients already bought. Yesterday's
    slot is left planned_empty, never absent, saying where the dish went.
    No skipped_at: the dish was not skipped, it moved, and if Saturday
    goes unticked tomorrow's card asks about it like any other meal.

    Not when the first fed night is already ticked cooked (the move would
    delete that record) or when nothing later is fed — then it is the
    ordinary skip. One transaction: either the dish moved and the empty
    night stands in its place, or nothing changed.
    """
    with write() as conn:
        conn.execute("BEGIN IMMEDIATE")
        entry = conn.execute(
            "SELECT id, weekly_plan_id, date, slot FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
        target = None
        if entry is not None:
            source = _leftovers.plan_leftover_chains(entry["weekly_plan_id"], conn=conn)["sources"].get(entry_id)
            # Later than yesterday: a lunch that fed yesterday's dinner has
            # nowhere useful to move to — that dinner is already past.
            fed = _weekly_plan.fed_nights_in_eating_order(source, day)
            if fed and not _weekly_plan.fed_night_is_cooked(conn, fed[0]):
                target = fed[0]
        # "We skipped it" forgets any start, the way "Mark not cooked" does
        # (check_off_meal): "Start cooking" is also how a person gets to the
        # steps, so a stamp on a night nobody cooked is ordinary. Left on,
        # it is a cook still under way to every reader of cook_begun — and
        # in the moved shape below it rode onto the fed night, so Friday's
        # lunch read "Started 6:02" (Thursday's clock) and could not be
        # moved, "Already cooking" (defect hunt, 2026-10-22). Undo puts it
        # back with the rest of the snapshot.
        if target is None:
            conn.execute(
                "UPDATE meal_plan_entries SET skipped_at = datetime('now'), cook_started_at = NULL "
                "WHERE id = ? AND household_id = ? AND skipped_at IS NULL",
                (entry_id, household_id()),
            )
            return None
        plan_id = entry["weekly_plan_id"]
        new_ref = f"{target['date']}:{target['slot']}"
        old_ref = f"{entry['date']}:{entry['slot']}"
        # The Undo's snapshot, before the first write — the night off's
        # machinery (plan_undo), so "skipped" rewriting the plan can be
        # taken back exactly like "not tonight" can.
        all_rows = conn.execute(
            "SELECT id, derived_from_json FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? "
            "AND component_category IS NULL",
            (plan_id, household_id()),
        ).fetchall()
        direct = {entry_id, *[t["entry_id"] for t in fed]}
        touched = _plan_undo.touched_ids(all_rows, direct, [old_ref, new_ref])
        snap = _plan_undo.snapshot(conn, touched)
        _weekly_plan.move_cook_onto_fed_night(conn, plan_id, entry_id, entry["date"], entry["slot"], target)
        # The fridge move travelled with the cook by the days it moved, and
        # a thaw is never dated on the cook's own day — so a move that was
        # due before yesterday now reads as due yesterday, a day gone, and
        # nothing says to take the food out. It is due now: today.
        conn.execute(
            "UPDATE prep_tasks SET task_date = ? WHERE household_id = ? AND meal_plan_entry_id = ? "
            "AND task_type = 'defrost' AND status = 'pending' AND task_date < ?",
            (today, household_id(), entry_id, today),
        )
        # The night it landed on is the cook now, not a night it feeds.
        _no_longer_doubled_for(conn, entry_id, target)
        # The stored reason is read on later days, so it keeps the weekday;
        # the toast is said now, so a move onto today says "tonight".
        label = _weekly_plan.fed_night_label(target, today=today)
        holder_id = _weekly_plan.plan_slot_empty(
            plan_id, entry["date"], entry["slot"],
            reason=f"Skipped — the {meal} moved to {_weekly_plan.fed_night_label(target)}.",
            derived_from={"constraint": SKIPPED_MOVED_CONSTRAINT, "moved_entry_id": entry_id},
            conn=conn,
        )["entry_id"]
        _plan_undo.stamp(conn, holder_id, SKIPPED_UNDO_KEY, {
            "kind": "skipped_moved", "dish": meal, **snap,
            "after": _plan_undo.fingerprint(conn, touched, holder_id),
            "grocery": _plan_undo.lines_changed(conn, snap),
        })
    # After the commit: the thaw pass opens connections of its own. Never
    # raises (resync_plan_thaws), so a failed reminder can't undo the move.
    from . import defrost as _defrost
    thaw_now = _defrost.resync_plan_thaws(plan_id, [entry_id], today=today)
    return {"date": target["date"], "slot": target["slot"], "label": label,
            "undo_entry_id": holder_id, "thaw_now": thaw_now}


def _no_longer_doubled_for(conn, entry_id: int, target: dict) -> None:
    """A cook that has just landed on a night it was feeding
    (weekly_plan.move_cook_onto_fed_night) no longer cooks double for that
    night — the night eats the cook itself. Drops it from make_double_for
    (and its note) and clears any start stamp, on the caller's open
    transaction. Lifted out of _skip for going_off's swap (Loop Board B5),
    which moves tonight's cook onto its leftovers night by the same rule."""
    new_ref = f"{target['date']}:{target['slot']}"
    derived = json.loads(conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()["derived_from_json"] or "{}")
    fed_keys = derived.get("make_double_for") or []
    if isinstance(fed_keys, str):
        fed_keys = [fed_keys]
    fed_keys = [k for k in fed_keys if str(k).strip() != new_ref]
    if fed_keys:
        derived["make_double_for"] = fed_keys
        derived["make_double_note"] = _weekly_plan._make_double_note_text(fed_keys, target["slot"])
    else:
        derived.pop("make_double_for", None)
        derived.pop("make_double_note", None)
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ?, cook_started_at = NULL "
        "WHERE id = ? AND household_id = ?",
        (json.dumps(derived), entry_id, household_id()),
    )


def undo_skipped_move(holder_entry_id: int) -> dict:
    """
    Undo on the "moved to Saturday" toast: put both nights back exactly as
    they were — the dish on yesterday, its leftovers night, the chain, the
    fridge moves on their old dates — and yesterday's question with them
    (the row is unanswered again, so the card asks it). Only while nothing
    the answer touched has changed since (plan_undo.still_as_left);
    otherwise nothing is written and `status` is 'refused' with a sentence.
    """
    nothing = {"status": "refused", "message": "There’s nothing to put back."}
    with write() as conn:
        conn.execute("BEGIN IMMEDIATE")
        holder = conn.execute(
            "SELECT * FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (int(holder_entry_id), household_id()),
        ).fetchone()
        record = _plan_undo.read(holder, SKIPPED_UNDO_KEY) if holder is not None else None
        if not record:
            return nothing
        if not _plan_undo.still_as_left(conn, record, holder["id"]):
            return {"status": "refused", "message": "That’s changed since, so I’ve left it as it is."}
        _plan_undo.restore(conn, record, holder["id"])
    dish = record.get("dish") or ""
    return {"status": "restored", "dish": dish or None,
            "said": f"{dish} is back where it was." if dish else "It’s back where it was."}
