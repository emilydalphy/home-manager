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
Every judgement is of what the line's meals STILL TO COME want, off the
ledger (_still_to_come): a night already cooked has taken its food out of
the kitchen and is no reason to buy more. A meal added while its food is
set aside joins that line (_tidy_set_aside_lines), so a food is in one
place, whole; a set-aside line with no meal left is deleted.
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


def _still_to_come(conn, row, today: str):
    """
    What this line is still FOR: the (amount, unit) its meals yet to be
    cooked want — read off the ledger (meal_plan_grocery_links), never the
    line's own text, which keeps the share of a night already eaten. None
    when no meal still to come is behind it (nothing left to judge).

    A meal still to come: pending, today or later, not skipped, not
    depleted — the same reading recipes._KitchenStock.reserve_bought_for_
    meals_ahead makes of "still to be cooked". Cooking a night takes its
    food out of the kitchen (cooker's depletion), so comparing the whole
    week's line against what is left would call a covered week short
    (verify 2026-10-27, issue 1).

    A sealed package or a spice's jar is the line's own amount (one jar
    for the week, however many meals name it); a ledger that won't read in
    one unit falls back to the line too, and a freeform one can't be
    compared at all (None from the parse: bought).
    """
    from . import grocery as _grocery
    from . import pre_shop as _pre_shop
    from . import quantities as _quantities

    qtys = [r["quantity"] or "" for r in conn.execute(
        "SELECT l.quantity FROM meal_plan_grocery_links l JOIN meal_plan_entries e "
        "ON e.id = l.meal_plan_entry_id AND e.household_id = l.household_id "
        "WHERE l.household_id = ? AND l.grocery_item_id = ? AND e.cooked_status = 'pending' "
        "AND e.date >= ? AND e.skipped_at IS NULL AND e.inventory_depleted_at IS NULL ORDER BY l.id",
        (household_id(), row["id"], today),
    ).fetchall()]
    if not qtys:
        return None
    line = _pre_shop._pre_shop_parse_total(row["quantity"] or "")
    if any(_grocery._holds_one_package(row["item"] or "", q) for q in qtys):
        return line or "unreadable"
    totals = _quantities._ledger_totals(qtys, [])
    if totals is None:
        return line or "unreadable"
    return (totals[0], totals[2])


def _covered(stock, fresh_names: list[dict], row, need) -> bool:
    """Whether the kitchen covers `need` for this line — and claims it when so."""
    from . import cooker as _cooker

    if need is None or need == "unreadable":
        return False  # "a bunch", or amounts that won't add: buy it
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


def _plain_plan_line(row, put_back) -> bool:
    """A line wholly the plan's to decide: no staple, spice or sauce, no
    amount the household kept or edited, not put back."""
    from . import grocery as _grocery

    return (
        row["staple_id"] is None
        and not _own_rules(row["item"])
        and not (row["kept_amount"] or "").strip()
        and not (row["edited_to"] or "").strip()
        and _grocery._merge_key(row["item"] or "") not in put_back
    )


def _tidy_set_aside_lines(conn, weekly_plan_id: int, put_back) -> None:
    """
    Before judging: one row per food, and no empty rows.

    - A set-aside line no meal is behind any more (its last night swapped
      away) is deleted: nothing will put it back, and nothing shows it.
    - A meal added to the week while its food's line is set aside gets a
      NEW needed row (grocery.add_grocery_item merges only onto lines still
      to buy), so the same food sat in both places — and twice in the
      section after a second add (verify 2026-10-27, issue 2). The set-aside
      line's meals move onto the needed one, which is re-read from its whole
      ledger, and the set-aside row goes; the pass then judges the one line
      as a whole, so a partly covered food is all on the list, never split.
      Only when the ledger reads in one unit and holds no package (a jar the
      re-read keeps as one jar): otherwise the two rows are left as they are.
    """
    from . import grocery as _grocery
    from . import quantities as _quantities

    hid = household_id()
    conn.execute(
        "DELETE FROM grocery_items WHERE household_id = ? AND source_weekly_plan_id = ? "
        "AND status = 'removed' AND removed_by = ? AND NOT EXISTS ("
        "  SELECT 1 FROM meal_plan_grocery_links l WHERE l.household_id = grocery_items.household_id "
        "  AND l.grocery_item_id = grocery_items.id)",
        (hid, weekly_plan_id, REMOVED_BY_KITCHEN),
    )
    rows = conn.execute(
        f"SELECT {_LINE_COLUMNS} FROM grocery_items g WHERE g.household_id = ? AND g.source_weekly_plan_id = ? "
        "AND ((g.status = 'removed' AND g.removed_by = ?) "
        "  OR (g.status = 'needed' AND g.excluded_from_list = 0 AND g.already_have_reviewed = 0)) ORDER BY g.id",
        (hid, weekly_plan_id, REMOVED_BY_KITCHEN),
    ).fetchall()
    by_key: dict[str, dict] = {}
    for r in rows:
        if not _plain_plan_line(r, put_back):
            continue
        slot = by_key.setdefault(_grocery._merge_key(r["item"] or ""), {"aside": [], "needed": []})
        slot["aside" if r["status"] == "removed" else "needed"].append(r)
    for slot in by_key.values():
        if not slot["aside"] or not slot["needed"]:
            continue
        keep = slot["needed"][0]
        ids = [r["id"] for r in slot["aside"]] + [keep["id"]]
        qtys = [q["quantity"] or "" for q in conn.execute(
            f"SELECT quantity FROM meal_plan_grocery_links WHERE household_id = ? "
            f"AND grocery_item_id IN ({','.join('?' * len(ids))})",
            (hid, *ids),
        ).fetchall()]
        if (_quantities._ledger_totals(qtys, []) is None
                or any(_grocery._holds_one_package(keep["item"] or "", q) for q in qtys)):
            continue
        for aside in slot["aside"]:
            conn.execute(
                "UPDATE meal_plan_grocery_links SET grocery_item_id = ? WHERE household_id = ? AND grocery_item_id = ?",
                (keep["id"], hid, aside["id"]),
            )
            conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?", (aside["id"], hid))
        _grocery._recompute_plan_line_from_ledger(keep["id"], conn=conn)


def leave_off_what_is_home(weekly_plan_id: int, conn=None, put_back_only: bool = False) -> list[str]:
    """
    The pass (see the module docstring). Returns the names it set aside.
    Writes on `conn` without committing when one is given (the ingest's
    open transaction); opens, commits and closes its own otherwise.
    Never raises: a failure here costs the "Already home" section, never
    the approval it runs inside.

    Every line is judged on what its meals STILL TO COME want
    (_still_to_come), never its whole week: a night already cooked has
    taken its food out of the kitchen, and is not a reason to buy more.
    A set-aside line with nothing still to come stays set aside.

    `put_back_only` runs only the safe direction — a set-aside line the
    kitchen no longer covers goes back on the list, nothing new is set
    aside: the re-read after a kitchen row is said to be gone
    (inventory_check.settle_live_plans_after_kitchen_change).
    """
    from . import cooker as _cooker
    from . import recipes as _recipes

    own = conn is None
    if own:
        conn = get_conn()
    left_off: list[str] = []
    try:
        today = _cooker.household_today(conn=conn).isoformat()
        put_back = _put_back_keys(conn, weekly_plan_id)
        _tidy_set_aside_lines(conn, weekly_plan_id, put_back)
        since = fresh_cutoff(conn)
        # Not the Freezer rows this plan's own freezer step added: they are
        # this plan's set-aside line already (inventory A3), the same rule
        # WeekGroceryBuffer.kitchen_stock follows (merge 2026-10-27).
        from . import inventory as _inventory
        own_freezer = _inventory.rows_added_by_plans_freezer_answer(conn, weekly_plan_id)
        stock = _recipes._KitchenStock(conn, fresh_since=since, exclude_ids=own_freezer)
        stock.reserve_bought_for_meals_ahead(conn)
        fresh = fresh_inventory_ids(conn, since) - own_freezer
        # A row the household marked "Almost gone" (C3's chip, while it still
        # stands: read against the quantity it was tapped at, as
        # whats_home.amount_level reads it) covers nothing — they just told
        # us it's about out (merge seams, 2026-10-27).
        fresh_names = [
            {"item": r["item"]} for r in conn.execute(
                "SELECT id, item, quantity, amount_left, amount_left_qty FROM inventory_items "
                "WHERE household_id = ? AND TRIM(quantity) != ''",
                (household_id(),),
            ).fetchall() if r["id"] in fresh and not (
                (r["amount_left"] or "") == "almost_gone"
                and (r["amount_left_qty"] or "") == (r["quantity"] or ""))
        ]
        # Other live weeks' set-aside lines speak for their food first — only
        # for the meals of theirs still to come (a cooked night's beef is
        # already out of the kitchen; verify 2026-10-27, issue 3).
        for row in conn.execute(
            f"SELECT {_LINE_COLUMNS} FROM grocery_items g "
            "LEFT JOIN weekly_plans w ON w.id = g.source_weekly_plan_id AND w.household_id = g.household_id "
            "WHERE g.household_id = ? AND g.status = 'removed' AND g.removed_by = ? "
            f"AND g.source_weekly_plan_id IS NOT NULL AND g.source_weekly_plan_id != ? "
            f"AND COALESCE(w.status, '') != 'retired' AND {_HAS_A_MEAL} ORDER BY g.id",
            (household_id(), REMOVED_BY_KITCHEN, weekly_plan_id),
        ).fetchall():
            _covered(stock, fresh_names, row, _still_to_come(conn, row, today))
        rows = conn.execute(
            f"SELECT {_LINE_COLUMNS} FROM grocery_items g "
            "WHERE g.household_id = ? AND g.source_weekly_plan_id = ? AND ("
            "  (g.status = 'needed' AND g.excluded_from_list = 0 AND g.already_have_reviewed = 0)"
            "  OR (g.status = 'removed' AND g.removed_by = ?)) ORDER BY g.id",
            (household_id(), weekly_plan_id, REMOVED_BY_KITCHEN),
        ).fetchall()
        for row in rows:
            need = _still_to_come(conn, row, today)
            if need is None:
                continue  # nothing still to come behind it: leave it where it is
            if put_back_only and row["status"] == "needed":
                continue  # not judged at all, so it claims no stock from a set-aside line
            covered = _plain_plan_line(row, put_back) and _covered(stock, fresh_names, row, need)
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
