"""
Stores, per-item store preferences, and shopping trips.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from ..db import get_conn, write
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
                "UPDATE item_store_preferences SET item = ?, store = ? "
                "WHERE id = ? AND household_id = ?",
                (item.strip().lower(), store, match["id"], household_id()),
            )
        else:
            conn.execute(
                "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, ?, ?)",
                (household_id(), item.strip().lower(), store),
            )
        _apply_store_to_matching_rows(conn, item, store)
    else:
        if match:
            conn.execute(
                "DELETE FROM item_store_preferences WHERE id = ? AND household_id = ?",
                (match["id"], household_id()),
            )
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
        "UPDATE grocery_items SET store = ?, store_decided = ? "
        "WHERE id = ? AND household_id = ?",
        (store, 1 if decided else 0, item_id, household_id()),
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


# ---------- opening hours ----------
#
# Loop Board "Shop: know the hours of my preferred stores when suggesting
# when to shop" (Emily, 2026-10-18: "Yes to all"). One stored fact per
# store, kept beside the household's other store facts (usual_stores,
# store_typical_items) in meal_preferences.store_hours_json, keyed by the
# store's name as it is on usual_stores:
#
#     {"Farm Boy": {"days": {"mon": ["08:00", "21:00"], ..., "sun": "closed"},
#                   "closed_on_holidays": false}}
#
# A day that is absent is UNKNOWN, never closed — nothing in the editor is
# required, and a store with no hours behaves exactly as it did before this
# existed. Only an explicit "closed" is closed.
#
# A close at or before the open runs into the NEXT morning: "08:00"–"01:00"
# is open till one a.m., "08:00"–"00:00" till midnight, and "00:00"–"00:00"
# is open 24 hours. So a window is a pair of datetimes, not times, and
# "is it open at 12:30 a.m. Tuesday?" asks Monday's window as well as
# Tuesday's (window_at).

HOURS_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_SHORT = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
_DAY_PLURAL = {"mon": "Mondays", "tue": "Tuesdays", "wed": "Wednesdays", "thu": "Thursdays",
               "fri": "Fridays", "sat": "Saturdays", "sun": "Sundays"}
# A day key as a person or a model might write it: "mon" or "monday", any
# case. Nothing looser — "monkey" is not Monday.
_DAY_KEYS = {**{d: d for d in HOURS_DAYS},
             **{full: full[:3] for full in ("monday", "tuesday", "wednesday", "thursday",
                                            "friday", "saturday", "sunday")}}
MAX_STORE_NAME = 80


def _hhmm(value) -> str:
    """'8:00', '08:00', '20:30' -> 'HH:MM'; anything else raises ValueError."""
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise ValueError(f"'{value}' isn't a time — use 24-hour HH:MM, like 08:00 or 21:00.")
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _clean_hours(hours) -> dict | None:
    """The stored shape, validated. Empty (no day known, holidays off) is None:
    a store with nothing set has no hours row at all, not an empty one."""
    if not hours:
        return None
    if not isinstance(hours, dict):
        raise ValueError("Hours must be an object with 'days'.")
    days_in = hours.get("days") or {}
    if not isinstance(days_in, dict):
        raise ValueError("'days' must map mon..sun to [opens, closes] or 'closed'.")
    days: dict = {}
    for key, value in days_in.items():
        day = _DAY_KEYS.get(str(key).strip().lower())
        if day is None:
            raise ValueError(f"'{key}' isn't a day of the week.")
        if value in (None, "", []):
            continue  # unknown, which is the same as not saying
        if value == "closed":
            days[day] = "closed"
            continue
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"{_DAY_SHORT[day]}: give an opening and a closing time.")
        opens, closes = _hhmm(value[0]), _hhmm(value[1])
        # Equal times are only ever "open 24 hours", said as 00:00–00:00.
        if opens == closes and opens != "00:00":
            raise ValueError(f"{_DAY_SHORT[day]}: for open all day, use 00:00 to 00:00.")
        days[day] = [opens, closes]
    holidays = hours.get("closed_on_holidays") is True
    if not days and not holidays:
        return None
    return {"days": {d: days[d] for d in HOURS_DAYS if d in days}, "closed_on_holidays": holidays}


def _read_store_hours(conn) -> dict:
    row = conn.execute(
        "SELECT store_hours_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    try:
        raw = json.loads(row["store_hours_json"]) if row else {}
    except (TypeError, ValueError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _usual_stores(conn) -> list[str]:
    row = conn.execute(
        "SELECT usual_stores_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    try:
        names = json.loads(row["usual_stores_json"]) if row else []
    except (TypeError, ValueError):
        names = []
    if not isinstance(names, list):
        return []
    return [n.strip() for n in names if isinstance(n, str) and n.strip()]


def get_store_hours() -> dict:
    """Every store's saved hours, {name: hours} — see the shape above."""
    conn = get_conn()
    try:
        return _read_store_hours(conn)
    finally:
        conn.close()


def set_store_hours(store: str, hours: dict | None) -> dict:
    """
    Save (or, with nothing in it, forget) one store's opening hours.
    Replaces that store's whole entry: the editor always sends all of it,
    which is what lets "Different on some days" going back to "Same every
    day" drop the day rows it no longer shows.

    Only for one of the household's usual stores (matched any case, saved
    under the usual-stores spelling). Hours are read into the chat's
    context, so an arbitrary name of any length must not get in that way.
    """
    name = (store or "").strip()
    if not name:
        raise ValueError("Which store?")
    if len(name) > MAX_STORE_NAME:
        raise ValueError("That store name is too long.")
    cleaned = _clean_hours(hours)
    with write() as conn:
        usual = {n.lower(): n for n in _usual_stores(conn)}
        if name.lower() not in usual:
            raise ValueError(f"{name} isn't one of your stores yet — add it first.")
        name = usual[name.lower()]
        current = _read_store_hours(conn)
        # One entry per store, whatever case it was saved under — the same
        # case-insensitive identity add_usual_stores merges on.
        current = {k: v for k, v in current.items() if k.strip().lower() != name.lower()}
        if cleaned:
            current[name] = cleaned
        conn.execute(
            """
            INSERT INTO meal_preferences (household_id, store_hours_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(household_id) DO UPDATE SET store_hours_json = excluded.store_hours_json, updated_at = datetime('now')
            """,
            (household_id(), json.dumps(current)),
        )
    _household._log_preference_event("store_hours", "write" if cleaned else "delete")
    return {"store": name, "hours": cleaned}


def hours_for(store: str, all_hours: dict | None = None) -> dict | None:
    """One store's hours, matched case-insensitively, or None."""
    name = (store or "").strip().lower()
    if not name:
        return None
    table = all_hours if all_hours is not None else get_store_hours()
    for key, value in table.items():
        if key.strip().lower() == name and isinstance(value, dict):
            return value
    return None


def _is_holiday(day: date, cache: dict | None = None) -> bool:
    """`cache` (a dict the caller keeps for one render) saves asking the
    holidays module — region, rule table, calendar — once per day per store
    per walk of next_opening."""
    if cache is not None and day in cache:
        return cache[day]
    from . import holidays as _holidays  # call time: holidays reaches back into the plan
    try:
        found = _holidays.holiday_on(day.isoformat()) is not None
    except Exception:
        found = False
    if cache is not None:
        cache[day] = found
    return found


def open_window(store: str, day: date, all_hours: dict | None = None, cache: dict | None = None) -> dict | None:
    """
    The window that OPENS on `day`, as far as the household has said:

      None                          — not known (no hours, or not that day):
                                      every caller says what it said before
      {"closed": True, "holiday": bool}
      {"opens": datetime, "closes": datetime, "all_day": bool}
                                    — closes may be the next calendar day
                                      (after-midnight, midnight, 24 hours)

    "Closed on holidays" counts on the dates the holidays module already
    knows (its rule table for the household's region, plus holiday-looking
    events on its calendar) — no real holiday hours in this slice.
    """
    hours = hours_for(store, all_hours)
    if not hours:
        return None
    if hours.get("closed_on_holidays") is True and _is_holiday(day, cache):
        return {"closed": True, "holiday": True}
    value = (hours.get("days") or {}).get(HOURS_DAYS[day.weekday()])
    if value == "closed":
        return {"closed": True, "holiday": False}
    if not isinstance(value, list) or len(value) != 2:
        return None
    try:
        o = datetime.strptime(value[0], "%H:%M").time()
        c = datetime.strptime(value[1], "%H:%M").time()
    except (TypeError, ValueError):
        return None
    opens = datetime.combine(day, o)
    closes = datetime.combine(day, c)
    if closes <= opens:
        closes += timedelta(days=1)
    return {"opens": opens, "closes": closes, "all_day": o == c}


def window_at(store: str, moment: datetime, all_hours: dict | None = None, cache: dict | None = None):
    """
    Is `store` open at `moment`? ("open", window) | ("closed", None) |
    ("unknown", None). Yesterday's window counts: a store open till 1 a.m.
    is open at 12:30 a.m. the next day.
    """
    today = open_window(store, moment.date(), all_hours, cache)
    yesterday = open_window(store, moment.date() - timedelta(days=1), all_hours, cache)
    for w in (yesterday, today):
        if w and not w.get("closed") and w["opens"] <= moment < w["closes"]:
            return "open", w
    if today is None:
        return "unknown", None
    return "closed", None


def closed_on_weekday(store: str, weekday: str, all_hours: dict | None = None) -> bool:
    """True only when the household said the store is closed that weekday."""
    hours = hours_for(store, all_hours)
    return bool(hours) and (hours.get("days") or {}).get((weekday or "").strip().lower()[:3]) == "closed"


def _short_clock(hhmm: str) -> str:
    """'08:00' -> '8', '20:30' -> '8:30', '00:00' -> 'midnight' — the
    editor's line and the chat's, where am/pm is plain from context ("8–9")."""
    h, m = (int(x) for x in hhmm.split(":"))
    if h == 0 and m == 0:
        return "midnight"
    hour = h % 12 or 12
    return f"{hour}" if m == 0 else f"{hour}:{m:02d}"


def _span(value) -> str:
    if value[0] == value[1]:
        return "24 hours"
    return f"{_short_clock(value[0])}–{_short_clock(value[1])}"


def _day_ranges(days: list[str]) -> str:
    """['mon','tue','wed','sat'] -> 'Mon–Wed, Sat'."""
    idx = sorted(HOURS_DAYS.index(d) for d in days)
    runs, start, prev = [], None, None
    for i in idx:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            runs.append((start, prev)); start = prev = i
    if start is not None:
        runs.append((start, prev))
    out = []
    for a, b in runs:
        if a == b:
            out.append(_DAY_SHORT[HOURS_DAYS[a]])
        elif b == a + 1:
            out.append(f"{_DAY_SHORT[HOURS_DAYS[a]]}, {_DAY_SHORT[HOURS_DAYS[b]]}")
        else:
            out.append(f"{_DAY_SHORT[HOURS_DAYS[a]]}–{_DAY_SHORT[HOURS_DAYS[b]]}")
    return ", ".join(out)


def hours_summary(hours: dict | None) -> str:
    """
    The one quiet line under a store's name in Preferences, and what the
    chat reads: "8–9 every day", "8–9, Sun 10–6", "8–9, closed Sun",
    "8–midnight every day", "Open 24 hours". '' when nothing is set. The
    most common hours are the base; every other day is said after it. The
    only implementation: the screen draws what get_household_memory's
    store_hours_summary carries.
    """
    if not hours:
        return ""
    days = hours.get("days") or {}
    open_days = {d: tuple(v) for d, v in days.items() if isinstance(v, list)}
    closed = [d for d in HOURS_DAYS if days.get(d) == "closed"]
    parts: list[str] = []
    if open_days:
        counts: dict = {}
        for d in HOURS_DAYS:
            if d in open_days:
                counts[open_days[d]] = counts.get(open_days[d], 0) + 1
        base = max(counts, key=lambda k: counts[k])  # first-seen wins a tie (dict order)
        base_days = [d for d in HOURS_DAYS if open_days.get(d) == base]
        if len(base_days) == 7:
            parts.append("Open 24 hours" if base[0] == base[1] else f"{_span(base)} every day")
        elif len(base_days) + len(closed) + sum(1 for d in open_days if open_days[d] != base) == 7:
            parts.append(_span(base))  # every day known: the base needs no days named
        else:
            parts.append(f"{_span(base)} {_day_ranges(base_days)}")
        for d in HOURS_DAYS:
            if d in open_days and open_days[d] != base:
                parts.append(f"{_DAY_SHORT[d]} {_span(open_days[d])}")
    if closed:
        parts.append(f"closed {_day_ranges(closed)}" if open_days else f"Closed {_day_ranges(closed)}")
    line = ", ".join(parts)
    if hours.get("closed_on_holidays") is True:
        line = f"{line} · closed on holidays" if line else "Closed on holidays"
    return line


def say_clock(t) -> str:
    """"8:00", "5:30", "noon", "midnight" — moves._clock's way of saying a
    time, plus the midnight close only a store has. Takes a time or a
    datetime."""
    if isinstance(t, datetime):
        t = t.time()
    if t.hour == 0 and t.minute == 0:
        return "midnight"
    if t.hour == 12 and t.minute == 0:
        return "noon"
    return f"{t.hour % 12 or 12}:{t.minute:02d}"


def next_opening(store: str, after: datetime, all_hours: dict | None = None,
                 cache: dict | None = None, days: int = 7) -> dict | None:
    """The next window that opens after `after`, within a week — or None
    when the hours on record can't say (an unknown day is never skipped
    past to a known one)."""
    for offset in range(0, days + 1):
        w = open_window(store, after.date() + timedelta(days=offset), all_hours, cache)
        if w is None:
            return None
        if w.get("closed") or w["opens"] <= after:
            continue
        return w


def opens_said(opens: datetime, today: date) -> str:
    """"opens 8:00", "opens 8:00 tomorrow", "opens 10:00 Monday"."""
    when = say_clock(opens)
    if opens.date() == today:
        return f"opens {when}"
    if opens.date() == today + timedelta(days=1):
        return f"opens {when} tomorrow"
    return f"opens {when} {opens.strftime('%A')}"


def open_now_said(store: str, now: datetime, all_hours: dict | None = None, cache: dict | None = None) -> str:
    """"open till 9:00", "open 24 hours", "closed — opens 8:00 tomorrow",
    or '' when the hours on record don't cover now. The chat's answer to
    "is it open?"."""
    state, w = window_at(store, now, all_hours, cache)
    if state == "unknown":
        return ""
    if state == "open":
        return "open 24 hours" if w["all_day"] else f"open till {say_clock(w['closes'])}"
    today = open_window(store, now.date(), all_hours, cache)
    why = " for the holiday" if today and today.get("closed") and today.get("holiday") else ""
    nxt = next_opening(store, now, all_hours, cache)
    return f"closed{why} — {opens_said(nxt['opens'], now.date())}" if nxt else f"closed{why}"


def first_usual_store() -> str | None:
    """The household's first usual store — whose hours an untagged list
    goes by (the card: "untagged list → household's first store")."""
    conn = get_conn()
    try:
        names = _usual_stores(conn)
    finally:
        conn.close()
    return names[0] if names else None


def possessive_is(name: str) -> str:
    """"Farm Boy's" for "Farm Boy is"; "Loblaws is" where an apostrophe-s
    would read as a typo."""
    return f"{name} is" if name.lower().endswith("s") else f"{name}'s"
