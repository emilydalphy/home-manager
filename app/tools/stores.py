"""
Stores, per-item store preferences, and shopping trips.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id
from . import grocery as _grocery
from . import quantities as _quantities


def set_item_store(item: str, store: str) -> dict:
    """
    Remember which store an item (or type of item) should be bought at,
    e.g. "we get paper towels at Costco" -> set_item_store("paper towels",
    "Costco"). Applies immediately to any matching item already on the
    grocery list, and automatically to future adds of that same item name.
    Pass an empty store to clear the preference.
    """
    conn = get_conn()
    if store:
        conn.execute(
            "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, ?, ?) "
            "ON CONFLICT(household_id, item) DO UPDATE SET store = excluded.store",
            (household_id(), item.strip().lower(), store),
        )
        conn.execute(
            "UPDATE grocery_items SET store = ? WHERE household_id = ? AND LOWER(item) = LOWER(?)",
            (store, household_id(), item),
        )
    else:
        conn.execute(
            "DELETE FROM item_store_preferences WHERE household_id = ? AND item = ?",
            (household_id(), item.strip().lower()),
        )
        conn.execute(
            "UPDATE grocery_items SET store = '' WHERE household_id = ? AND LOWER(item) = LOWER(?)",
            (household_id(), item),
        )
    conn.commit()
    conn.close()
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


def set_grocery_item_store(item_id: int, store: str, remember: bool = True) -> dict:
    """
    Set which store a specific already-listed grocery item should be
    bought at — for assigning a store directly from a grocery list row
    (e.g. the Grocery List view's triage screen) rather than a general "we
    get X at Costco" chat mention (see set_item_store for that). By
    default, assigning a real (non-empty) store also remembers it as this
    item's usual store going forward — same underlying item_store_preferences
    row set_item_store writes — so the next time this item name is added to
    the list (a new week's plan, a chat mention, a manual add) it's already
    tagged to that store instead of landing back in the unsorted "to sort"
    queue. Picking "no particular store" (an empty store) never touches or
    clears an existing preference — that's a one-off skip, not a decision
    to forget where this item usually comes from. Pass remember=False to
    set just this one row without touching the remembered preference at
    all (used when re-displaying/correcting a row rather than the shopper
    actively choosing a store for it).
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id())
    ).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    conn.execute("UPDATE grocery_items SET store = ? WHERE id = ?", (store, item_id))
    remembered = False
    if store and remember:
        conn.execute(
            "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, ?, ?) "
            "ON CONFLICT(household_id, item) DO UPDATE SET store = excluded.store",
            (household_id(), row["item"].strip().lower(), store),
        )
        remembered = True
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "store": store, "found": True, "remembered": remembered}


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
