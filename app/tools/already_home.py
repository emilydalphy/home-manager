"""
"Already home" — the Shop list leaves off what the kitchen has (card B4,
Option B "plan around what's home", 2026-10-26).

THE RULE. A grocery line the week's meals put there is set aside, off the
list, when the kitchen can be SHOWN to hold the whole of it in food that was
bought, added or counted in the last FRESH_DAYS days. Set aside means the
same soft remove every other "already have" answer makes (status 'removed',
removed_by REMOVED_BY_KITCHEN), so it lands in Shop's "Already had on hand"
foot — retitled "Already home · N" while Pomona filled any of it — and the
one undo (pre_shop.undo_pre_shop_drop, "Put back on the list") brings it
back as the same row with the same ledger.

What is never set aside here, each one deliberately:

- A household's own line (source_weekly_plan_id NULL): every hand, chat,
  scan or staple add. It is their want, and the standing-want arithmetic
  (grocery._restate_standing_want) is not this module's to touch. The
  ingest keeps its old silent skip when a covered ingredient would have
  merged onto one (ingest_keeps_skip), so their line is not grown either.
- A staple's line, a spice, a sauce or pantry staple: their own rules
  (staples.py, spices.py, pantry.py), unchanged — the ingest keeps its old
  silent skip for them, against the whole kitchen as before.
- PARTLY covered. Home has a pound, the week wants two: the line stays on
  at the full amount. Buying the difference is a guess about what is in
  the fridge, and a wrong guess costs a dinner. (_KitchenStock.covers is
  all-or-nothing already; this asks it about the whole LINE, so two recipes
  wanting a pound each against one pound at home leave the two-pound line
  on — where the ingest used to skip one recipe's pound silently.)
- A line somebody already answered: already_have_reviewed (a pre-shop
  "Still need to buy", or this section's own "add it back"), an amount the
  household edited, a kept carry-over amount.
- Anything put back from this section for THIS plan, even after the list
  is rebuilt (already_home_put_back) — "add it back" sticks for the week.

WHEN. Once per ingest pass, at the end of recipes.WeekGroceryBuffer.flush —
approval, a rebuild, a swap, a meal added — on that pass's connection. A
line this pass set aside that a later pass finds no longer covered (its
meals grew, the food went) is put back on the list; the safe direction.
Nothing happens on a plain list read: the list does not rearrange itself
while somebody is standing in the shop.

Stock is claimed as it is granted (the _KitchenStock ledger), other live
weeks' set-aside lines first, so one pound of beef at home is "already
home" for one week's line, never two.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

REMOVED_BY_KITCHEN = "kitchen"
FRESH_DAYS = 14


def fresh_cutoff(conn) -> tuple[str, str]:
    """(UTC instant, household-local ISO date) FRESH_DAYS days back from the
    household's today — the two shapes the freshness columns are written in."""
    from . import cooker as _cooker
    from . import pre_shop as _pre_shop

    today = _cooker.household_today(conn=conn)
    day = today - timedelta(days=FRESH_DAYS)
    return _pre_shop._household_day_start_utc(day, _cooker.household_zone(conn=conn)), day.isoformat()


def _home_recently(conn) -> tuple[str, int]:
    """
    THE recency rule, in one place: an SQL condition on inventory row `i`
    meaning "bought or confirmed in the window". A row counts when it was
    created (bought, added in chat, a "have it"), counted (counted_on), or
    bought into again (a grocery tick whose receipt names this row). Not
    updated_at: cooking a meal writes that too (cooker's depletion), and
    "we cooked from it" is not "we know it's there".

    `confirmed_on` (the "still have these?" answer, card A4, branch
    inventory/pre-draft-check — not on main when this was written) is OR'd
    in whenever the column exists, so that merge needs no change here.
    Returns (sql, kinds) where kinds lists 'utc' / 'day' per placeholder.
    """
    parts = ["i.created_at >= ?", "(i.counted_on IS NOT NULL AND i.counted_on >= ?)"]
    kinds = ["utc", "day"]
    columns = {r[1] for r in conn.execute("PRAGMA table_info(inventory_items)").fetchall()}
    if "confirmed_on" in columns:
        parts.append("(i.confirmed_on IS NOT NULL AND i.confirmed_on >= ?)")
        kinds.append("day")
    parts.append(
        "EXISTS (SELECT 1 FROM grocery_items g WHERE g.household_id = i.household_id "
        "AND g.inventory_added_at IS NOT NULL AND g.inventory_added_at >= ? "
        "AND json_extract(g.inventory_receipt_json, '$.inventory_id') = i.id)"
    )
    kinds.append("utc")
    return "(" + " OR ".join(parts) + ")", kinds


def fresh_inventory_ids(conn, since: tuple[str, str]) -> set[int]:
    """Kitchen rows the household has bought or confirmed since `since`
    (see _home_recently for exactly what counts)."""
    since_utc, since_day = since
    cond, kinds = _home_recently(conn)
    params = [since_utc if k == "utc" else since_day for k in kinds]
    rows = conn.execute(
        f"SELECT i.id FROM inventory_items i WHERE i.household_id = ? AND {cond}",
        (household_id(), *params),
    ).fetchall()
    return {r["id"] for r in rows}


def _own_rules(item: str) -> bool:
    """A spice, sauce or pantry staple — the list's own sections decide those."""
    from . import spices as _spices

    return _spices.is_spice(item) or _spices.is_assumed(item)


def ingest_keeps_skip(item: str, weekly_plan_id: int | None, conn=None) -> bool:
    """
    For an ingredient the kitchen covers at ingest: True when the ingest
    should leave it off silently, exactly as it did before this module —
    a meal with no week behind it, a spice / sauce / pantry staple, a
    staple, or a name the household already has a line of its own for
    (the plan's share would merge onto their line, which nothing here may
    set aside). Otherwise False: the ingredient goes on the list and the
    flush's leave_off_what_is_home decides on the whole line.
    """
    if weekly_plan_id is None or _own_rules(item):
        return True
    from . import grocery as _grocery
    from . import staples as _staples

    own = conn is None
    if own:
        conn = get_conn()
    try:
        if _staples._find_by_name(conn, item) is not None:
            return True
        key = _grocery._merge_key(item)
        for r in conn.execute(
            "SELECT item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'spice') "
            "AND excluded_from_list = 0 AND (source_weekly_plan_id IS NULL OR staple_id IS NOT NULL)",
            (household_id(),),
        ).fetchall():
            if _grocery._merge_key(r["item"]) == key:
                return True
        return False
    finally:
        if own:
            conn.close()


def _put_back_keys(conn, weekly_plan_id: int) -> set[str]:
    return {
        r["item_key"] for r in conn.execute(
            "SELECT item_key FROM already_home_put_back WHERE household_id = ? AND weekly_plan_id = ?",
            (household_id(), weekly_plan_id),
        ).fetchall()
    }


def note_put_back(conn, row) -> None:
    """
    "Bought some anyway? Tap to add it back." — remember it for the plan, so
    a rebuild of the list (which deletes and rewrites the plan's lines) does
    not set it aside again. Called from pre_shop.undo_pre_shop_drop on the
    caller's connection, for a line this module set aside. Never raises.
    """
    try:
        if row is None or row["removed_by"] != REMOVED_BY_KITCHEN or row["source_weekly_plan_id"] is None:
            return
        from . import grocery as _grocery

        conn.execute(
            "INSERT OR IGNORE INTO already_home_put_back (household_id, weekly_plan_id, item_key) "
            "VALUES (?, ?, ?)",
            (household_id(), row["source_weekly_plan_id"], _grocery._merge_key(row["item"] or "")),
        )
    except Exception:
        logger.exception("Remembering an Already home put-back failed")


def _covered(stock, fresh_names: list[dict], row) -> bool:
    """Whether the kitchen covers this whole line — and claims it when so."""
    from . import cooker as _cooker
    from . import pre_shop as _pre_shop

    need = _pre_shop._pre_shop_parse_total(row["quantity"] or "")
    if need is None:
        return False  # "a bunch", or two amounts that won't add: buy it
    match, confident = _cooker._find_inventory_match(row["item"], fresh_names)
    if not match or not confident:
        return False
    return stock.covers(match["item"], need)


_LINE_COLUMNS = (
    "g.id, g.item, g.quantity, g.status, g.removed_by, g.source_weekly_plan_id, g.staple_id, "
    "g.already_have_reviewed, g.kept_amount, g.edited_to, g.excluded_from_list"
)

_HAS_A_MEAL = (
    "EXISTS (SELECT 1 FROM meal_plan_grocery_links l WHERE l.household_id = g.household_id "
    "AND l.grocery_item_id = g.id)"
)


def leave_off_what_is_home(weekly_plan_id: int, conn=None) -> list[str]:
    """
    The pass (see the module docstring). Returns the names it set aside.
    Writes on `conn` without committing when one is given (the ingest's
    open transaction); opens, commits and closes its own otherwise.
    Never raises: a failure here costs the "Already home" section, never
    the approval it runs inside.
    """
    from . import recipes as _recipes
    from . import grocery as _grocery

    own = conn is None
    if own:
        conn = get_conn()
    left_off: list[str] = []
    try:
        since = fresh_cutoff(conn)
        stock = _recipes._KitchenStock(conn, fresh_since=since)
        stock.reserve_bought_for_meals_ahead(conn)
        fresh = fresh_inventory_ids(conn, since)
        fresh_names = [
            {"item": r["item"]} for r in conn.execute(
                "SELECT id, item FROM inventory_items WHERE household_id = ? AND TRIM(quantity) != ''",
                (household_id(),),
            ).fetchall() if r["id"] in fresh
        ]
        # Other live weeks' set-aside lines speak for their food first.
        for row in conn.execute(
            f"SELECT {_LINE_COLUMNS} FROM grocery_items g "
            "LEFT JOIN weekly_plans w ON w.id = g.source_weekly_plan_id AND w.household_id = g.household_id "
            "WHERE g.household_id = ? AND g.status = 'removed' AND g.removed_by = ? "
            f"AND g.source_weekly_plan_id IS NOT NULL AND g.source_weekly_plan_id != ? "
            f"AND COALESCE(w.status, '') != 'retired' AND {_HAS_A_MEAL} ORDER BY g.id",
            (household_id(), REMOVED_BY_KITCHEN, weekly_plan_id),
        ).fetchall():
            _covered(stock, fresh_names, row)
        put_back = _put_back_keys(conn, weekly_plan_id)
        rows = conn.execute(
            f"SELECT {_LINE_COLUMNS}, {_HAS_A_MEAL} AS has_meal FROM grocery_items g "
            "WHERE g.household_id = ? AND g.source_weekly_plan_id = ? AND ("
            "  (g.status = 'needed' AND g.excluded_from_list = 0 AND g.already_have_reviewed = 0)"
            "  OR (g.status = 'removed' AND g.removed_by = ?)) ORDER BY g.id",
            (household_id(), weekly_plan_id, REMOVED_BY_KITCHEN),
        ).fetchall()
        for row in rows:
            if not row["has_meal"]:
                continue  # no meal behind it: nothing to decide (and not shown)
            eligible = (
                row["staple_id"] is None
                and not _own_rules(row["item"])
                and not (row["kept_amount"] or "").strip()
                and not (row["edited_to"] or "").strip()
                and _grocery._merge_key(row["item"] or "") not in put_back
            )
            covered = eligible and _covered(stock, fresh_names, row)
            if row["status"] == "needed" and covered:
                conn.execute(
                    "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
                    "WHERE id = ? AND household_id = ? AND status = 'needed'",
                    (REMOVED_BY_KITCHEN, row["id"], household_id()),
                )
                left_off.append(row["item"])
            elif row["status"] == "removed" and not covered:
                conn.execute(
                    "UPDATE grocery_items SET status = 'needed', removed_by = '', removed_at = NULL "
                    "WHERE id = ? AND household_id = ? AND status = 'removed' AND removed_by = ?",
                    (row["id"], household_id(), REMOVED_BY_KITCHEN),
                )
        if own:
            conn.commit()
    except Exception:
        logger.exception("The Already home pass failed for plan %s", weekly_plan_id)
        return []
    finally:
        if own:
            conn.close()
    return left_off


def already_home_lines() -> list[dict]:
    """
    What the Shop list's "Already home" section shows: every line this
    module set aside that still has a meal to come behind it, on a week
    that isn't retired. Same shape as pre_shop.get_already_have_decisions'
    rows (the section reads both), plus weekly_plan_id. Not windowed by
    removed_at the way that function is: these are set aside at approval,
    usually the evening BEFORE the period starts, which that window cuts.
    """
    from . import cooker as _cooker

    conn = get_conn()
    try:
        today = _cooker.household_today(conn=conn).isoformat()
        rows = conn.execute(
            "SELECT g.id, g.item, g.quantity, g.category, g.store, g.removed_by, g.removed_at, "
            "g.source_weekly_plan_id AS weekly_plan_id FROM grocery_items g "
            "LEFT JOIN weekly_plans w ON w.id = g.source_weekly_plan_id AND w.household_id = g.household_id "
            "WHERE g.household_id = ? AND g.status = 'removed' AND g.removed_by = ? "
            "AND COALESCE(w.status, '') != 'retired' AND EXISTS ("
            "  SELECT 1 FROM meal_plan_grocery_links l JOIN meal_plan_entries e "
            "  ON e.id = l.meal_plan_entry_id AND e.household_id = l.household_id "
            "  WHERE l.household_id = g.household_id AND l.grocery_item_id = g.id AND e.date >= ?) "
            "ORDER BY g.id",
            (household_id(), REMOVED_BY_KITCHEN, today),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
