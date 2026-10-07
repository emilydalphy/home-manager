"""
Who a ticked move counted for — the record, and the one read of it.

Loop Board "Every move has an owner (slice 3)": *as the adult who did
tonight's cook, I want the tick to count for me, so that when the household
later looks at whether the work is split fairly, meals are in the picture.*
The card's own note (2026-09-27): this is the ONLY place that records who
ticked a cook, prep or shop move; "Since you last looked" reads it and must
not add its own.

WHAT IS CREDITED IS THE OWNER, NOT THE PERSON HOLDING THE PHONE. The card
says so, and it is also the only honest choice available: the signed-in
adult (`_shared.member_id()`) is whoever tapped, which on a shared tablet
in the kitchen is nobody in particular. The owner is move_owner.py's answer
— the one Today drew beside the move — read at the moment of the tick. An
owner changed between planning and ticking credits the new one; a credit
already written does not move when the answer changes afterwards.

ONE SOURCE FOR "WHICH MOVE IS THIS". The kind and the owner are read off
the move itself, built by moves.moves_for_day exactly as Today builds it,
rather than re-derived here. A reheat is two different shapes in moves.py
(a leftovers night, and a lunch prepped on an earlier day), a holiday's
shop is a prep_tasks row that moves.py calls a shop, and _stamp_owners
decides which of those carries a name — a second copy of any of that
would drift from the screen the first time one of them changed. Costs a
cooker view per tick; a tick is a tap, not a page load.

EMPTY IS RECORDED AS EMPTY. A move with no owner (most households, every
reheat, and every shop nobody picked "Who's on it?" for — move_owner's
docstring says why there is no standing "who shops" answer) is still
credited, with member_id NULL. Nothing fills it in later. A per-day
"Who's on it?" pick (move_owner_overrides) IS an owner and is credited.

WHAT HAS NO TICK, said rather than invented: the week's ordinary shop
move. moves.set_move_done returns without writing for `shop:<date>`
because shopping is done when the list says so, and Today renders that
move with no tick at all. There is nothing to credit and nothing here
pretends otherwise. The holiday's own shop row IS a tick (a prep_tasks
row) and is credited as kind 'shop': to whoever "Who's on it?" named
for that day, or with no owner when nobody was named.

NOTHING HERE IS SHOWN TO THE HOUSEHOLD. No count, no score, no streak —
`credits` is the shape a fairness view will need, and nothing calls it yet.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import cooker as _cooker
from . import moves as _moves

logger = logging.getLogger(__name__)

MEAL_KINDS = ("cook", "reheat")
TASK_KINDS = ("fridge", "prep", "shop")

_ANYONE = object()  # credits(): no filter on the person, as distinct from None ("nobody owned it")


# ---------- the move, as Today drew it ----------

def _move_as_drawn(weekly_plan_id: int | None, day_in, matches) -> dict | None:
    """
    The move that `matches` picks, on the day `day_in(view)` names, built
    the way Today builds it: first off the same "what am I cooking now"
    view Today reads (so the owner is the one the screen showed), then —
    only if it isn't there, e.g. yesterday's dinner from a week that has
    since ended — off the view of the plan the row belongs to. None when
    neither draws it.
    """
    views = [lambda: _cooker.get_cooker_view()]
    if weekly_plan_id is not None:
        views.append(lambda: _cooker.get_cooker_view(weekly_plan_id))
    for build in views:
        view = build()
        day = day_in(view)
        if not day:
            continue
        found = next((m for m in _moves.moves_for_day(day, view=view) if matches(m)), None)
        if found is not None:
            return found
    return None


def _card_date(view: dict, ids: set[int]) -> str | None:
    """
    The date of the card holding any of these entries. Not the tapped row's
    own date: a component batch is one card, dated its cook day, so the
    sibling ticked on Wednesday is a cook drawn on Monday.
    """
    for meal in view.get("meals") or []:
        if ids & set(meal.get("entry_ids") or [meal.get("entry_id")]):
            return meal.get("date")
    return None


def meal_credit(entry_ids: list[int], weekly_plan_id: int | None) -> dict | None:
    """
    What a cook/reheat tick on these linked entries counts as: {entry_id,
    kind, move_date, member_id}, or None when no move draws the meal at all
    (an undated row — nothing a week can place — or a row no view draws). `entry_ids` is the whole
    linked set, because a component batch is ONE merged card whose move
    carries the first sibling's id, whichever sibling was tapped.

    Never raises: a credit that can't be worked out must not cost the
    household its tick.
    """
    ids = set(entry_ids)
    try:
        move = _move_as_drawn(
            weekly_plan_id,
            lambda view: _card_date(view, ids),
            lambda m: m["kind"] in MEAL_KINDS and m.get("entry_id") in ids,
        )
    except Exception:
        logger.exception("Couldn't work out whose tick this was; the meal is ticked, uncredited")
        return None
    if move is None:
        return None
    return {"entry_id": move["entry_id"], "kind": move["kind"],
            "move_date": move["date"], "member_id": move.get("owner")}


def task_credit(task_id: int, day: str | None, weekly_plan_id: int | None) -> dict | None:
    """The same for a fridge move, a prep task or a holiday's shop row."""
    try:
        move = _move_as_drawn(
            weekly_plan_id,
            lambda _view: day,
            lambda m: m["kind"] in TASK_KINDS and m.get("task_id") == task_id,
        )
    except Exception:
        logger.exception("Couldn't work out whose tick this was; the task is ticked, uncredited")
        return None
    if move is None:
        return None
    return {"task_id": task_id, "kind": move["kind"],
            "move_date": move["date"], "member_id": move.get("owner")}


# ---------- writes: inside the tick's own transaction ----------

def record(conn, credit: dict | None) -> None:
    """
    Write one credit on the tick's own connection, before its commit, so
    the tick and its credit land together or not at all. INSERT OR IGNORE:
    a second 'done' for a move already credited keeps the FIRST credit —
    the owner at the moment it was actually done.

    Guarded like the lookups above: a credit must never cost the household
    its tick, so a failed write is logged and the tick commits without it
    (SQLite rolls back only the failed statement, not the transaction).
    """
    if not credit:
        return
    try:
        conn.execute(
            "INSERT OR IGNORE INTO move_credits "
            "(household_id, meal_plan_entry_id, prep_task_id, kind, move_date, member_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (household_id(), credit.get("entry_id"), credit.get("task_id"),
             credit["kind"], credit["move_date"], credit.get("member_id")),
        )
    except Exception:
        logger.exception("Couldn't record whose tick this was; the tick stands, uncredited")


def clear_meals(conn, entry_ids: list[int]) -> None:
    """Un-tick: the credit goes with it — every linked sibling's, since a
    batch's credit sits on whichever entry its card carries."""
    conn.executemany(
        "DELETE FROM move_credits WHERE household_id = ? AND meal_plan_entry_id = ?",
        [(household_id(), eid) for eid in entry_ids],
    )


def clear_task(conn, task_id: int) -> None:
    conn.execute(
        "DELETE FROM move_credits WHERE household_id = ? AND prep_task_id = ?",
        (household_id(), task_id),
    )


# ---------- the read ----------

def credits(week_start: str | date, *, days: int = 7, member_id=_ANYONE,
            kind: str | None = None) -> list[dict]:
    """
    The household's credited moves for one week (moves dated week_start
    through week_start + days - 1), oldest first:
    [{id, kind, date, member_id}] where `id` is the move's own id on Today
    ("cook:12", "fridge:7" — a holiday shop row is "prep:7" there too).

    `member_id` filters to one person; pass None for the moves nobody owned.
    `kind` filters to one move kind. A credit counts only while the row it
    is for still reads done — the completion lives there, not here.
    """
    start = date.fromisoformat(str(week_start)) if not isinstance(week_start, date) else week_start
    end = start + timedelta(days=max(1, days) - 1)
    sql = (
        "SELECT c.kind, c.move_date, c.member_id, c.meal_plan_entry_id, c.prep_task_id "
        "FROM move_credits c "
        "LEFT JOIN meal_plan_entries e ON e.id = c.meal_plan_entry_id AND e.household_id = c.household_id "
        "LEFT JOIN prep_tasks t ON t.id = c.prep_task_id AND t.household_id = c.household_id "
        "WHERE c.household_id = ? AND c.move_date >= ? AND c.move_date <= ? "
        "AND (e.cooked_status = 'done' OR t.status = 'done')"
    )
    args: list = [household_id(), start.isoformat(), end.isoformat()]
    if member_id is None:
        sql += " AND c.member_id IS NULL"
    elif member_id is not _ANYONE:
        sql += " AND c.member_id = ?"
        args.append(member_id)
    if kind is not None:
        sql += " AND c.kind = ?"
        args.append(kind)
    sql += " ORDER BY c.move_date, c.id"
    conn = get_conn()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        if r["meal_plan_entry_id"] is not None:
            move_id = f"{r['kind']}:{r['meal_plan_entry_id']}"
        else:
            move_id = ("fridge:" if r["kind"] == "fridge" else "prep:") + str(r["prep_task_id"])
        out.append({"id": move_id, "kind": r["kind"], "date": r["move_date"], "member_id": r["member_id"]})
    return out
