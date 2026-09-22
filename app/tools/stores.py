"""
Stores, per-item store preferences, and shopping trips.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id
from .grocery import _merge_key
from . import grocery as _grocery
from . import household as _household
from . import quantities as _quantities


# The most rows one bulk store assignment may carry — see
# set_grocery_items_stores. Comfortably above any real grocery list, and low
# enough that a runaway caller gets an error rather than a long write.
MAX_BULK_STORE_ASSIGNMENTS = 500


def _apply_store_to_matching_rows(conn, item: str, store: str) -> None:
    """
    Set the store on whichever line the list is actually holding.

    Matched on the grocery list's own merge key rather than the exact
    name: a preference saved for "bell peppers" has to reach the line
    that ended up called "Bell pepper", or the app cheerfully confirms a
    preference that never takes effect.

    This is the SECOND writer of grocery_items.store (set_grocery_item_store
    below is the other), so it has to keep store_decided in step or the flag
    drifts — the failure class CLAUDE.md records for snacks_per_week_set,
    and the reason delete_preference clears that one. A real store answers
    "where does this go?"; clearing the preference re-opens the question.
    Leaving the flag set through a clear would strand the row as
    permanently answered with no shop on it: never asked about again, and
    sitting in the one bucket the sorting step deliberately skips.
    """
    wanted = _merge_key(item)
    rows = conn.execute(
        "SELECT id, item FROM grocery_items WHERE household_id = ?", (household_id(),)
    ).fetchall()
    for row in rows:
        if _merge_key(row["item"]) == wanted:
            conn.execute(
                "UPDATE grocery_items SET store = ?, store_decided = ? WHERE id = ? AND household_id = ?",
                (store, 1 if store else 0, row["id"], household_id()),
            )


def set_item_store(item: str, store: str, log_event: bool = True, sync_typical: bool = True) -> dict:
    """
    Remember which store an item (or type of item) should be bought at,
    e.g. "we get paper towels at Costco" -> set_item_store("paper towels",
    "Costco"). Applies immediately to any matching item already on the
    grocery list, and automatically to future adds of that same item name.
    Pass an empty store to clear the preference.

    This is the single place an item->store preference actually gets
    written or cleared (the Shop tab's add sheet, "Sort them all" and a
    row's ⋯ all funnel through it via set_grocery_item_store, and the
    Kitchen "What we know" Stores sheet via
    preferences.add_store_typical_items) — so it's also the one place that
    keeps the Kitchen sheet's typical-items list and this preference from
    ever disagreeing (Loop Board "Stores: one bidirectional memory..."):
    setting a store here also remembers the item as typical for that store,
    and clearing it drops the item from every store's typical list, not
    just the one it happened to be filed under.

    sync_typical=False is for internal use only (preferences.
    add_store_typical_items sets it when it calls back in here, so a
    Kitchen-sheet add can't bounce back and forth with this function
    forever). log_event=False similarly avoids double-logging one teaching
    moment as two preference_events rows when this function is one half of
    a compound write.

    Identity here is by _merge_key, the same singular/plural-insensitive
    key the grocery list itself merges on — NOT by the exact text typed.
    Found by independent review (2026-09-03): the old exact-string
    ON CONFLICT/DELETE let set_item_store("paper towel", "Costco") and a
    later set_item_store("paper towels", "Walmart") create two separate
    rows for what the grocery list treats as one item, so it could end up
    "typical" at two stores at once and a future add would pick between
    them non-deterministically. A find-by-merge-key-then-write replaces
    the raw upsert/delete so there is only ever one row per real item,
    same as grocery_items itself. The stored text is still whatever was
    most recently written (never the mangled _merge_key form itself —
    that's a matching key, not something to show anyone).
    """
    conn = get_conn()
    key = _merge_key(item)
    existing_rows = conn.execute(
        "SELECT id, item FROM item_store_preferences WHERE household_id = ?", (household_id(),)
    ).fetchall()
    match = next((r for r in existing_rows if _merge_key(r["item"]) == key), None)
    if store:
        if match:
            conn.execute(
                "UPDATE item_store_preferences SET item = ?, store = ? WHERE id = ?",
                (item.strip().lower(), store, match["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, ?, ?)",
                (household_id(), item.strip().lower(), store),
            )
        _apply_store_to_matching_rows(conn, item, store)
    else:
        if match:
            conn.execute("DELETE FROM item_store_preferences WHERE id = ?", (match["id"],))
        _apply_store_to_matching_rows(conn, item, "")
    conn.commit()
    conn.close()
    if log_event:
        _household._log_preference_event("item_store_preference", "write" if store else "delete")
    # Local import: stores.py and preferences.py each call into the other
    # (this keeps the Kitchen typical-items list and this table in sync in
    # both directions), which would be a circular import at module load
    # time — safe here because it's resolved at call time, once both
    # modules already finished loading.
    from . import preferences as _preferences
    if store and sync_typical:
        _preferences.add_store_typical_items(store, [item], log_event=False, sync_preference=False)
    elif not store:
        _preferences.remove_item_from_all_stores_typical_list(item)
    return {"item": item, "store": store}


def is_multi_store_household() -> bool:
    """
    Whether this household actually shops at more than one store — the
    signal the Grocery List view uses to decide whether "By store" is
    worth showing at all (Phase 6 PRD §4.1/§4.4 audit finding: the tab used
    to show unconditionally, with nothing behind it distinguishing single-
    from multi-store households). True if either the saved usual_stores
    list has more than one entry, or more than one distinct store name is
    actually tagged on a grocery item right now — covers both "told the
    app up front" and "tagged ad hoc without ever saving it as a usual
    store."
    """
    conn = get_conn()
    prefs = conn.execute(
        "SELECT usual_stores_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    usual = set(json.loads(prefs["usual_stores_json"])) if prefs else set()
    tagged_rows = conn.execute(
        "SELECT DISTINCT store FROM grocery_items WHERE household_id = ? AND store != ''", (household_id(),)
    ).fetchall()
    conn.close()
    tagged = {r["store"] for r in tagged_rows}
    return len(usual | tagged) > 1


def get_grocery_list_by_store(status: str = "needed") -> dict:
    """
    Get the grocery list split into store groups (see set_item_store),
    each internally grouped by section like get_grocery_list_by_section —
    use this instead of get_grocery_list_by_section when the household
    shops at more than one store, so the list reads like separate trips
    rather than one mixed pile. Items with no assigned store are grouped
    under "Unassigned".
    """
    if status == "needed":
        # Same as get_grocery_list_by_section: a due staple joins the list
        # on read. See app/tools/staples.py.
        from . import staples as _staples
        _staples.sync_due_staples()
    items = _grocery.list_grocery_list(status=status)
    by_store: dict[str, list[dict]] = {}
    for it in items:
        store = it.get("store") or "Unassigned"
        by_store.setdefault(store, []).append(it)
    stores = []
    for store, store_items in by_store.items():
        sections: dict[str, list[dict]] = {s: [] for s in _quantities._GROCERY_SECTION_ORDER}
        for it in store_items:
            cat = _quantities._GROCERY_CATEGORY_ALIASES.get(it["category"], it["category"])
            sections.setdefault("other", [])
            sections[cat if cat in sections else "other"].append(it)
        stores.append({
            "store": store,
            "sections": [{"section": s, "items": sections[s]} for s in _quantities._GROCERY_SECTION_ORDER if sections[s]],
        })
    return {"stores": stores}


def set_grocery_item_store(item_id: int, store: str, remember: bool = True, decided: bool = True) -> dict:
    """
    Set which store a specific already-listed grocery item should be
    bought at — for assigning a store directly from a grocery list row
    (the Shop tab's add sheet, "Sort them all", a row's ⋯) rather than a
    general "we get X at Costco" chat mention (see set_item_store for
    that). By default, assigning a real (non-empty) store also remembers
    it as this item's usual store going forward — the same
    item_store_preferences row set_item_store writes — so the next time
    this item name is added to the list (a new week's plan, a chat
    mention, a manual add) it's already tagged to that store instead of
    landing back in the unsorted "to sort" queue. Picking "no particular
    store" (an empty store) never touches or clears an existing
    preference — that's a one-off skip, not a decision to forget where
    this item usually comes from. Pass remember=False to set just this
    one row without touching the remembered preference at all (a
    one-week-only move: this week the eggs come from Metro because
    Costco was out).

    Remembering is immediate and quiet (Loop Board 3e31f4c0-5231-81ca,
    2026-09-21: "adding or sorting an item once remembers its store").
    Until then the FIRST store an item got came back with
    needs_confirmation and a "Remember X at Costco?" toast the shopper had
    to tap; sorting forty things meant forty toasts, and a toast let
    expire meant the same question next week. Putting a thing under a
    store IS the answer now, and the row's ⋯ is the correction — the
    response says so with remembered=True.

    decided (default True) records that a PERSON answered the "where does
    this go?" question for this row — see grocery_items.store_decided. It
    matters only for the empty store: without it "no particular shop" is
    written as '' and is then indistinguishable from never having been
    asked, so the Grocery tab's sorting step asked about the same skipped
    item again on every reload. Pass decided=False to put a row back the way
    it was — an undo of a bulk assign, which has to restore the exact
    previous state rather than blank it.
    """
    conn = get_conn()
    try:
        staged = _stage_grocery_item_store(conn, item_id, store, remember, decided)
        conn.commit()
    finally:
        conn.close()
    return _settle_grocery_item_store(staged)


def _stage_grocery_item_store(conn, item_id: int, store: str, remember: bool, decided: bool, forget: bool = False) -> dict:
    """
    The half of set_grocery_item_store that touches the database, on a
    connection somebody else owns and commits.

    Split out for the bulk call below, which has to write many rows inside
    ONE transaction. Nothing here opens a connection of its own: on SQLite
    there is a single writer, so a nested get_conn inside an open write
    transaction waits on the lock it is itself holding and fails with
    "database is locked" — the same reason atomic-period-takeover threads a
    connection through its helpers instead of trusting each to commit
    politely. The preference write that may follow is deliberately NOT done
    here for exactly that reason; see _settle_grocery_item_store.

    `forget` is the undo's flag (set_grocery_items_stores): an empty store
    with forget=True clears the item's remembered store as well as the
    row's, so undoing a sort takes back what the sort remembered.
    """
    row = conn.execute(
        "SELECT id, item FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id())
    ).fetchone()
    if not row:
        return {"item_id": item_id, "found": False}
    conn.execute(
        "UPDATE grocery_items SET store = ?, store_decided = ? WHERE id = ?",
        (store, 1 if decided else 0, item_id),
    )
    return {
        "item_id": item_id,
        "item": row["item"],
        "store": store,
        "found": True,
        "remember": remember,
        "forget": forget,
    }


def _settle_grocery_item_store(staged: dict) -> dict:
    """
    The half that runs AFTER the transaction has committed and closed: the
    item->store preference write, which opens its own connection and so
    cannot happen while the row write is still open (see above).
    """
    if not staged.get("found"):
        return {"item_id": staged["item_id"], "found": False}
    store = staged["store"]
    remembered = False
    forgotten = False
    if store and staged["remember"]:
        # Remembered at once, and the Kitchen sheet kept in step with
        # wherever it now points (set_item_store syncs the typical list).
        set_item_store(staged["item"], store)
        remembered = True
    elif not store and staged.get("forget"):
        # An undo of a sort: the row goes back to unsorted and the usual
        # store the sort wrote goes with it. Only when there is one to
        # clear — set_item_store('') on nothing would still log an event.
        if _pref_key(staged["item"]) is not None:
            set_item_store(staged["item"], "")
            forgotten = True
    return {
        "item_id": staged["item_id"],
        "item": staged["item"],
        "store": store,
        "found": True,
        "remembered": remembered,
        "forgotten": forgotten,
    }


def _pref_key(item: str) -> str | None:
    """The stored spelling of `item`'s preference row, by merge key, or
    None when nothing is remembered for it."""
    wanted = _merge_key(item)
    for name in get_item_store_preferences():
        if _merge_key(name) == wanted:
            return name
    return None


def set_grocery_items_stores(assignments: list[dict], remember: bool = False, forget: bool = False) -> dict:
    """
    Answer "where does this go?" for many listed items at once — one write
    per row, one request. Each assignment is {"item_id", "store", "decided"}
    ("store" defaults to '' meaning no particular shop, "decided" to True).

    This exists because the Grocery tab's two fast paths ("put all forty at
    Loblaws", and the sort-them-all-on-one-screen list) are ONE tap covering
    forty rows: forty sequential round trips would make a one-tap action
    take several seconds on a phone, and its undo just as long again.

    ONE transaction, committed once. That is not tidiness — it is what
    makes the undo trustworthy. The undo IS a bulk assign (the rows as they
    were before), so a half-applied one would leave the list in a state
    neither the household nor the app has a name for, with the toast's chip
    already spent. A failure anywhere rolls the whole thing back and the
    caller still has its payload. It is also why nothing in the loop opens
    a connection of its own — see _stage_grocery_item_store — and why the
    preference writes are done afterwards, outside the transaction.

    remember defaults to False here, the opposite of the single-row call:
    a bulk write is a restore more often than a choice. The Shop tab's
    "Sort them all" writes one row per tap through the single-row route
    (remember=True since 2026-09-21 — putting a thing under a store is
    the answer, Loop Board 3e31f4c0-5231-81ca), and its undo comes
    through here with remember=True and forget=True: a row put back to a
    real store is remembered there again, and a row put back to unsorted
    has its usual store cleared, so the undo takes back the whole of what
    the sort did.

    Bounded at MAX_BULK_STORE_ASSIGNMENTS. A grocery list is a few hundred
    rows at the outside; anything past that is not a household sorting its
    shop, and a request has to be able to say so rather than sit there
    writing.
    """
    if len(assignments) > MAX_BULK_STORE_ASSIGNMENTS:
        raise ValueError(
            f"too many assignments ({len(assignments)}); "
            f"the limit is {MAX_BULK_STORE_ASSIGNMENTS}"
        )
    conn = get_conn()
    staged = []
    try:
        for a in assignments:
            item_id = a.get("item_id")
            if item_id is None:
                continue
            staged.append(_stage_grocery_item_store(
                conn,
                int(item_id),
                a.get("store") or "",
                remember,
                bool(a.get("decided", True)),
                forget,
            ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    updated = 0
    for s in staged:
        if _settle_grocery_item_store(s).get("found"):
            updated += 1
    return {"updated": updated, "requested": len(assignments)}


def get_item_store_preferences() -> dict:
    """
    All remembered item->store associations (see set_item_store/
    set_grocery_item_store) as a flat {item_name_lowercase: store} map —
    powers the Grocery List view's "usually here" indicator, so a shopper
    can see at a glance which store assignments were auto-applied from
    memory (and weren't necessarily decided fresh this week) rather than
    treating every tagged item the same.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, store FROM item_store_preferences WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()
    return {r["item"]: r["store"] for r in rows}


_DEFAULT_AISLE_ORDER = ["Produce", "Bakery", "Dairy", "Meat", "Frozen", "Pantry", "Household"]


def get_stores() -> list[dict]:
    """
    Every store the household currently has grocery items assigned to
    (design_handoff_home_manager Phase 2, §8's left-rail "STORES" filter
    list), each with its real metadata row from the `stores` table when one
    exists (habit/role/aisle_order — see schema.sql), or just the default
    aisle order when it doesn't. A store only gets a real `stores` row once
    something sets its habit/role (Phase 4's Stores tab) — until then it's
    just a name the grocery list already knows about, listed here with
    defaults so the rail filter still works. Does NOT include "Unassigned"
    (storeId-null items) — that's a separate, always-present filter the UI
    adds itself, not a real store.
    """
    conn = get_conn()
    names = [
        r["store"] for r in conn.execute(
            "SELECT DISTINCT store FROM grocery_items WHERE household_id = ? AND store != '' AND status != 'removed'",
            (household_id(),),
        ).fetchall()
    ]
    meta_rows = conn.execute(
        "SELECT name, habit, role, aisle_order_json FROM stores WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()
    meta_by_name = {r["name"]: r for r in meta_rows}
    # Union in any store that has real metadata but currently has no items
    # on the list (e.g. between shopping trips) — it's still a store the
    # household shops at.
    for name in meta_by_name:
        if name not in names:
            names.append(name)
    return [
        {
            "name": name,
            "habit": meta_by_name[name]["habit"] if name in meta_by_name else "",
            "role": meta_by_name[name]["role"] if name in meta_by_name else "",
            "aisle_order": json.loads(meta_by_name[name]["aisle_order_json"]) if name in meta_by_name else list(_DEFAULT_AISLE_ORDER),
        }
        for name in names
    ]


def close_shopping_trip(store: str, item_count: int = 0) -> dict:
    """
    Record that a shopping stop at `store` just wrapped up — desktop
    Shopping mode's (design_handoff_home_manager Phase 3, option 5g)
    "Done shopping" / "Next store" actions call this once per store as the
    household finishes there. Deliberately minimal (see schema.sql's
    comment on shopping_trips): per-item inventory promotion already
    happened when each item was marked purchased, so this is just a closed
    record of the stop, not another promotion pass. Nothing reads trip
    history back yet — this is forward-compatible bookkeeping.
    """
    conn = get_conn()
    conn.execute(
        "INSERT INTO shopping_trips (household_id, store, item_count) VALUES (?, ?, ?)",
        (household_id(), store, item_count),
    )
    conn.commit()
    conn.close()
    return {"store": store, "item_count": item_count}
