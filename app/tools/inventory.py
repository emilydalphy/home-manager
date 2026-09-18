"""
What is actually in the pantry and fridge, including the item detail sheet.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from ..db import get_conn
from ._shared import household_id, require_household_row
from . import grocery as _grocery
from . import quantities as _quantities


def _today() -> date:
    """
    Today where the household lives, not where the container runs.

    Lazily imported, and that is forced rather than stylistic: cooker
    imports this module at module scope, so `from . import cooker` up there
    would be a cycle. Same shape held._today uses, for the same reason.

    An expiration_date is a CALENDAR DATE, never a UTC instant, so there is
    no sense in which the server's day is the right one to compare it
    against. The container runs UTC and households default to
    America/Toronto: before this, from about 8pm local, milk good all day
    today was reported `expired`, and get_expiring_soon is one of the two
    checks agent._build_proactive_check_block injects into the model's
    context at the start of a session — so the assistant said so out loud,
    unprompted, in exactly the evening hours somebody opens the app.
    """
    from .cooker import household_today

    return household_today()


_LEADING_NUM_RE = re.compile(r"^\s*([\d.]+)\s*(.*)$")


def _step_quantity_text(quantity: str, delta: float) -> str:
    """
    Nudge a freeform inventory quantity string by delta, e.g. "3 gal" -1 ->
    "2 gal". If there's no leading number to step (a bare descriptor like
    "some" or "a bag"), a '+' tap starts a count at 1 and keeps the
    descriptor as a suffix ("a bag" -> "1 a bag" is odd but rare — most
    inventory rows already carry a numeric quantity from the grocery
    checkoff or chat capture that created them); a '-' tap on a
    non-numeric quantity is a no-op. Floors at 0, never goes negative.
    """
    quantity = (quantity or "").strip()
    m = _LEADING_NUM_RE.match(quantity)
    if m and m.group(1):
        try:
            amount = float(m.group(1))
        except ValueError:
            amount = 0.0
        suffix = m.group(2).strip()
    else:
        if delta <= 0:
            return quantity  # nothing numeric to decrement
        amount = 0.0
        suffix = quantity
    amount = max(0.0, amount + delta)
    amount_str = f"{amount:g}"
    return f"{amount_str} {suffix}".strip() if suffix else amount_str


def step_inventory_quantity(item_id: int, delta: float) -> dict:
    """Nudge one inventory item's quantity by delta (e.g. +1/-1 from the item detail sheet's stepper)."""
    conn = get_conn()
    row = conn.execute("SELECT quantity FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    new_qty = _step_quantity_text(row["quantity"], delta)
    conn.execute("UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?", (new_qty, item_id))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "found": True, "quantity": new_qty}


def set_inventory_location(item_id: int, location: str) -> dict:
    """Move one inventory item to a different storage location (fridge/freezer/pantry) — re-groups it immediately."""
    conn = get_conn()
    require_household_row(conn, "inventory_items", item_id, label="inventory item")
    conn.execute("UPDATE inventory_items SET location = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?", (location, item_id, household_id()))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "location": location}


def step_inventory_expiration(item_id: int, delta_days: int) -> dict:
    """Shift one inventory item's best-before date by delta_days (one tap = one day). Starts from today if the item has no date set yet."""
    conn = get_conn()
    row = conn.execute("SELECT expiration_date FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    base = row["expiration_date"]
    try:
        base_date = date.fromisoformat(base) if base else _today()
    except ValueError:
        base_date = _today()
    new_date = (base_date + timedelta(days=delta_days)).isoformat()
    conn.execute("UPDATE inventory_items SET expiration_date = ?, updated_at = datetime('now') WHERE id = ?", (new_date, item_id))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "found": True, "expiration_date": new_date}


# Real tracked baseline of what's currently on hand, distinct from the
# grocery list (what's still needed). Captured three ways: chat mention,
# the Inventory screen's own add form, and the photo scanners (receipt,
# fridge, pantry) behind /api/inventory/scan-*. Chat is still the one that
# catches what gets said in passing rather than typed in deliberately.
def _try_subtract_quantity(existing_qty: str, minus_qty: str) -> tuple[str | None, bool]:
    """
    Try to subtract minus_qty from existing_qty for the same inventory item.
    Returns (resulting_quantity_or_None, reconciled). None means "fully used
    up, remove the row" — either because minus_qty was blank (caller meant
    "all of it") or because subtracting brought it to zero or below.
    reconciled=False means the units didn't match closely enough to safely
    subtract, so existing_qty is returned unchanged rather than guessing.
    """
    if not (minus_qty or "").strip():
        return None, True
    existing_parsed = _quantities._parse_quantity(existing_qty)
    minus_parsed = _quantities._parse_quantity(minus_qty)
    if not existing_parsed:
        # Existing quantity is freeform/unset (e.g. "a bunch") — can't do
        # precise math, so treat any explicit "used some" as using it all
        # rather than leaving a stale, unreconciled line behind.
        return None, True
    if minus_parsed and existing_parsed[1] == minus_parsed[1]:
        remaining = existing_parsed[0] - minus_parsed[0]
        if remaining <= 0:
            return None, True
        return _quantities._format_quantity(remaining, existing_parsed[1]), True
    return existing_qty, False


_RECEIPT_FIELDS = ("quantity", "source", "category", "expiration_date", "updated_at", "rev")


def _receipt_snapshot(conn, inventory_id: int) -> dict | None:
    """The fields of one inventory row that grocery.mark_grocery_item's
    receipt records on either side of a write — see
    grocery_items.inventory_receipt_json. None when the row is gone."""
    row = conn.execute(
        f"SELECT {', '.join(_RECEIPT_FIELDS)} FROM inventory_items WHERE id = ? AND household_id = ?",
        (inventory_id, household_id()),
    ).fetchone()
    return {k: row[k] for k in _RECEIPT_FIELDS} if row else None


def _find_row_by_name(conn, item: str, location: str | None, columns: str):
    """
    The one inventory row a name-only write (add / set / use) lands on.
    With a location, the row at that location. Without one, an item kept
    in two places ("BBQ sauce" opened in the fridge and unopened in the
    pantry, see get_cross_location_duplicates) used to come back in
    whatever order SQLite happened to store the rows — an add could merge
    into one row and the next "used some" subtract from the other
    (2026-09-13). Now: the most recently written row, ties to the newer
    one (updated_at DESC, id DESC). The argument: the row the household
    last touched is the one in play, and every name-only path uses the
    same rule, so add-then-use land on the same row. Nothing per-item
    records a "usual" place — location comes from an explicit hint or the
    category default at insert time — so there is nothing better to
    prefer; the callers that know the place pass it and skip all this.
    """
    if location:
        return conn.execute(
            f"SELECT {columns} FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?) AND location = ? "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            (household_id(), item, location),
        ).fetchone()
    return conn.execute(
        f"SELECT {columns} FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?) "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        (household_id(), item),
    ).fetchone()


def _add_to_inventory(
    item: str,
    quantity: str = "",
    source: str = "chat",
    expiration_date: str | None = None,
    category: str | None = None,
    location: str | None = None,
    conn=None,
) -> dict:
    """
    Merge into a matching row or insert a fresh one. Returns
    {"item_id", "item", "fresh", "before", "after"}: fresh says whether a
    row was created (vs merged into stock already there), before/after are
    _receipt_snapshot views of the row on either side of the write (before
    is None for a fresh row). Those three exist for grocery.mark_grocery_item,
    which records them so an untick can reverse exactly this write and
    nothing else; other callers can ignore them.

    Pass conn to run inside the caller's transaction (no commit, no close)
    — mark_grocery_item does, so the write, its stamp and its receipt land
    atomically with the status flip, or not at all.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    # If a location is given, only merge into a row already tracked at that
    # SAME location — a "BBQ sauce" bought new for the pantry shouldn't
    # silently merge into an already-opened one sitting in the fridge; that
    # should become (and stay) a second, distinct row. Without a location
    # hint, match by name anywhere — the most recently written row when the
    # item is kept in more than one place (see _find_row_by_name).
    existing = _find_row_by_name(conn, item, location, "id, quantity, category, expiration_date, location")
    if existing:
        merged_qty, _ = _grocery._try_consolidate_quantity(existing["quantity"] or "", quantity)
        fields = "quantity = ?, source = ?, updated_at = datetime('now')"
        params = [merged_qty, source]
        resolved_exp = _quantities._resolved_expiration_update(expiration_date, category, existing["category"], existing["expiration_date"], item)
        if resolved_exp:
            fields += ", expiration_date = ?"
            params.append(resolved_exp)
        if category:
            fields += ", category = ?"
            params.append(category)
        if location and location != existing["location"]:
            fields += ", location = ?"
            params.append(location)
        params.append(existing["id"])
        before = _receipt_snapshot(conn, existing["id"])
        conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ?", params)
        item_id = existing["id"]
    else:
        before = None
        item_category = category or "other"
        item_location = _quantities._resolve_location(location, item_category)
        cur = conn.execute(
            "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (household_id(), item, quantity, source, expiration_date or _quantities._estimate_expiration_date(item_category, item), item_category, item_location),
        )
        item_id = cur.lastrowid
    after = _receipt_snapshot(conn, item_id)
    if own_conn:
        conn.commit()
        conn.close()
    return {"item_id": item_id, "item": item, "fresh": existing is None, "before": before, "after": after}


# ---------------------------------------------------------------------------
# Provenance, and the one thing it is load-bearing for
# ---------------------------------------------------------------------------
# `inventory_items.source` is the only record of whether anybody actually
# STATED where a thing is kept. _add_to_inventory resolves a blank location
# through quantities._DEFAULT_LOCATION_BY_CATEGORY and stores the guess, so
# a guessed shelf and a stated one are byte-identical in the location
# column; there is nothing else to read. defrost.meat_items_for_plan is the
# reader (2026-09-15): it may not take the freezer question away from a
# household on a shelf the app picked for them.
SCAN_SOURCES = {"receipt": "scan_receipt", "fridge": "scan_fridge", "pantry": "scan_pantry"}

# Where the shelf on a row came from the category default rather than from
# a person. The two grocery paths write a row straight off a line the app
# watched go into a bag. A RECEIPT scan belongs here too and is the one
# that is easy to miss: agent.scan_receipt_image returns no location key at
# all — a receipt names food, never a shelf — where the fridge and pantry
# scans tag every row through _tag_scan_location. static/inventory.html
# then pre-fills the review sheet's location select with a guess of its
# own, so such a row reaches the server looking stated. It is not, and a
# household that did change the select pays one extra question, which is
# the cheap direction.
GUESSED_LOCATION_SOURCES = frozenset({
    "grocery_checkoff", "grocery_list_already_have",
    SCAN_SOURCES["receipt"], "scan",
})


def scan_source(kind: str, location: str | None = None) -> str:
    """
    What to record as the source of a row saved from a photo review.

    An unrecognised kind, or one that named no shelf, falls back to the
    bare "scan" — which GUESSED_LOCATION_SOURCES contains, so a caller
    that says nothing is read as having placed nothing. That is the safe
    fallback: the cost is a question the household could have been spared,
    and the alternative is a thaw nobody mentions.
    """
    known = SCAN_SOURCES.get((kind or "").strip().lower())
    if not known or not (location or "").strip():
        return "scan"
    return known


def update_inventory(
    item: str,
    action: str,
    quantity: str = "",
    expiration_date: str | None = None,
    category: str | None = None,
    location: str | None = None,
    source: str = "chat",
) -> dict:
    """
    Update pantry/fridge inventory from a chat mention — this is the
    primary way inventory gets captured (there's also a dedicated Inventory
    view page for direct editing), so call this proactively any time the
    user mentions buying, using, or running out of something, the same way
    preferences get captured proactively. action is one of:
      - "add": something was bought/received, e.g. "picked up a rotisserie chicken"
      - "use": some (or all, if quantity is left blank) of an item was used,
        e.g. "used the last of the spinach" (blank quantity) or "used a cup
        of the rice" (quantity given)
      - "remove": the item is gone for any other reason (spoiled, thrown
        out) — same effect as "use" with a blank quantity
      - "set": state an absolute amount currently on hand, e.g. "I have
        about 2 lbs of ground beef left"
    quantity is a freeform string like "2 lbs" or "1 dozen" — leave it blank
    when the person didn't mention an amount. expiration_date (ISO date) is
    optional — only set it if the person actually mentioned one; leave it
    unset otherwise rather than guessing. category should be one of:
    produce, dairy, meat/seafood, pantry, frozen, other — same taxonomy as
    the grocery list — so the Inventory view stays organized; pick whichever
    matches the item, defaults to 'other' if omitted. location (fridge,
    freezer, or pantry) is where it's physically stored, which can diverge
    from category (an opened sauce is category='pantry' by food type but
    location='fridge' once opened) — set it explicitly whenever the person
    mentions or implies where something's actually kept ("it's in the
    fridge now that it's open"), especially for the same item that might
    also exist elsewhere (an unopened one still in the pantry) so they stay
    as distinct entries rather than merging into one. Leave it unset to
    fall back to a reasonable category-based guess for a brand-new item, or
    to leave an existing item's location as-is.

    `source` is not the assistant's to set — it is absent from this tool's
    schema in agent.TOOL_DEFINITIONS, so a model call always gets the
    "chat" default. It exists for the routes that know something the chat
    does not, and today that is one: a photo review, which knows which
    photo it was (see scan_source above and /api/inventory/confirm-scan).
    """
    if action == "add":
        # The receipt fields (fresh/before/after) are mark_grocery_item's
        # business, not the chat agent's — keep this tool's answer as it was.
        added = _add_to_inventory(item, quantity, source=source, expiration_date=expiration_date, category=category, location=location)
        return {"item_id": added["item_id"], "item": added["item"]}

    if action == "set":
        conn = get_conn()
        existing = _find_row_by_name(conn, item, location, "id, category, expiration_date, location")
        if existing:
            fields = "quantity = ?, updated_at = datetime('now')"
            params = [quantity]
            resolved_exp = _quantities._resolved_expiration_update(expiration_date, category, existing["category"], existing["expiration_date"], item)
            if resolved_exp:
                fields += ", expiration_date = ?"
                params.append(resolved_exp)
            if category:
                fields += ", category = ?"
                params.append(category)
            if location and location != existing["location"]:
                fields += ", location = ?"
                params.append(location)
            params.append(existing["id"])
            conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ?", params)
            conn.commit()
            item_id = existing["id"]
        else:
            item_category = category or "other"
            item_location = _quantities._resolve_location(location, item_category)
            cur = conn.execute(
                "INSERT INTO inventory_items (household_id, item, quantity, source, category, expiration_date, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (household_id(), item, quantity, source, item_category, expiration_date or _quantities._estimate_expiration_date(item_category, item), item_location),
            )
            conn.commit()
            item_id = cur.lastrowid
        conn.close()
        return {"item_id": item_id, "item": item, "quantity": quantity}

    if action in ("use", "remove"):
        conn = get_conn()
        # No location given and this item might exist in more than one
        # place at once (see get_cross_location_duplicates): the most
        # recently written row, the same rule "add" uses, so the two agree
        # (see _find_row_by_name). Pass location when it's actually known.
        existing = _find_row_by_name(conn, item, location, "id, quantity")
        if not existing:
            conn.close()
            return {"item": item, "found": False}
        remaining, reconciled = _try_subtract_quantity(existing["quantity"] or "", quantity)
        if remaining is None:
            conn.execute("DELETE FROM inventory_items WHERE id = ?", (existing["id"],))
            conn.commit()
            conn.close()
            return {"item": item, "removed": True}
        conn.execute(
            "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
            (remaining, existing["id"]),
        )
        conn.commit()
        conn.close()
        return {"item": item, "quantity": remaining, "units_reconciled": reconciled}

    raise ValueError(f"Unknown inventory action '{action}'.")


def update_inventory_items(items: list, action: str = "add", source: str = "chat") -> dict:
    """
    Update several inventory items at once — use this (not repeated
    update_inventory calls) whenever the user mentions more than one item
    in the same breath, which is exactly what happens the first time
    someone populates inventory ("here's what's in our pantry: rice, olive
    oil, canned tomatoes, flour..."). Each entry can be a plain string (uses
    the shared `action`) or, when you know more, a dict like {"item":
    "flour", "action": "add", "quantity": "2 cups", "expiration_date":
    "2026-09-01", "category": "pantry", "location": "pantry"} to mix
    actions/quantities/categories/locations within one call. See
    update_inventory for what each action/category/location means — fill in
    category (and location, when known) per item when populating a batch so
    everything lands in the right place immediately.

    `source` — per entry, or one for the whole call — is the routes'
    business and not the assistant's; see update_inventory just above.
    """
    results = []
    for raw in items:
        if isinstance(raw, dict):
            name = (raw.get("item") or "").strip()
            act = raw.get("action") or action
            qty = raw.get("quantity", "")
            exp = raw.get("expiration_date")
            cat = raw.get("category")
            loc = raw.get("location")
            src = raw.get("source") or source
        else:
            name = (raw or "").strip()
            act = action
            qty = ""
            exp = None
            cat = None
            loc = None
            src = source
        if not name:
            continue
        results.append(update_inventory(name, act, quantity=qty, expiration_date=exp,
                                        category=cat, location=loc, source=src))
    return {"updated": results}


def get_inventory() -> list[dict]:
    """
    List everything currently tracked in pantry/fridge inventory. Check
    this before suggesting grocery additions for staples that might already
    be on hand, and before generating a weekly plan (already threaded into
    generate_weekly_plan's context automatically).
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, source, expiration_date, category, location, created_at FROM inventory_items WHERE household_id = ? ORDER BY item",
        (household_id(),),
    ).fetchall()
    conn.close()
    items = [dict(r) for r in rows]
    for it in items:
        it["location"] = _quantities._display_location(it)
    return items


def get_inventory_by_section() -> dict:
    """
    Get pantry/fridge inventory grouped into store sections (produce,
    dairy, meat/seafood, pantry, frozen, other) — same grouping as
    get_grocery_list_by_section. Powers the dedicated Inventory view page;
    use this instead of get_inventory whenever showing the full inventory
    to the user so it reads organized rather than a flat list.
    """
    items = get_inventory()
    sections: dict[str, list[dict]] = {s: [] for s in _quantities._GROCERY_SECTION_ORDER}
    for it in items:
        cat = _quantities._GROCERY_CATEGORY_ALIASES.get(it["category"], it["category"])
        sections.setdefault("other", [])
        sections[cat if cat in sections else "other"].append(it)
    return {"sections": [{"section": s, "items": sections[s]} for s in _quantities._GROCERY_SECTION_ORDER if sections[s]]}


def get_inventory_by_location() -> dict:
    """
    Get pantry/fridge inventory grouped by storage location (fridge,
    freezer, pantry) instead of food category — use this when the user
    specifically wants to know what's in the fridge or what's in the
    pantry, since location can diverge from category (an opened sauce is
    category='pantry' by food type but location='fridge' once opened).
    Powers the Inventory view's location-grouping toggle.
    """
    items = get_inventory()
    buckets: dict[str, list[dict]] = {loc: [] for loc in _quantities._LOCATION_ORDER}
    for it in items:
        loc = it["location"]
        buckets.setdefault(loc, [])
        buckets[loc if loc in buckets else "pantry"].append(it)
    return {"locations": [{"location": loc, "items": buckets[loc]} for loc in _quantities._LOCATION_ORDER if buckets[loc]]}


def get_cross_location_duplicates() -> list[dict]:
    """
    Find items tracked in more than one storage location at once — e.g. an
    opened BBQ sauce in the fridge and an unopened one still in the pantry.
    Surfaced so a near-empty opened item doesn't go unnoticed while an
    unopened twin sits untouched elsewhere, and so a grocery re-buy doesn't
    happen when one's already on hand, just not where expected. Check this
    proactively the same way as get_expiring_soon when it's relevant to the
    conversation (inventory questions, plan generation, grocery additions).
    """
    items = get_inventory()
    by_name: dict[str, list[dict]] = {}
    for it in items:
        by_name.setdefault(it["item"].strip().lower(), []).append(it)
    duplicates = []
    for entries in by_name.values():
        locations = {e["location"] for e in entries}
        if len(entries) > 1 and len(locations) > 1:
            duplicates.append({
                "item": entries[0]["item"],
                "entries": [
                    {"id": e["id"], "quantity": e["quantity"], "location": e["location"], "expiration_date": e["expiration_date"]}
                    for e in entries
                ],
            })
    return duplicates


def get_expiring_soon(days: int = 4) -> list[dict]:
    """
    List inventory items that are already past their (entered or
    estimated) expiration, or expiring within the given number of days —
    soonest/most overdue first. Each item includes a status of 'expired' or
    'expiring_soon'. Use this for "what's about to go bad" questions, and
    check it proactively when generating a weekly plan or suggesting a
    meal, so near-expiring items get worked in before they're wasted (see
    generate_weekly_plan's use_it_up weighting).
    """
    today_date = _today()
    cutoff = (today_date + timedelta(days=days)).isoformat()
    today = today_date.isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, expiration_date FROM inventory_items "
        "WHERE household_id = ? AND expiration_date IS NOT NULL AND expiration_date != '' AND expiration_date <= ? "
        "ORDER BY expiration_date ASC",
        (household_id(), cutoff),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        status = "expired" if r["expiration_date"] < today else "expiring_soon"
        result.append({
            "id": r["id"], "item": r["item"], "quantity": r["quantity"], "category": r["category"],
            "expiration_date": r["expiration_date"], "status": status,
        })
    return result


def get_fresh_perishable_inventory(near_expiring_days: int = 4) -> list[dict]:
    """
    List perishable items on hand (meat/seafood, produce, dairy) that
    AREN'T already covered by get_expiring_soon — i.e. still have some
    runway left, not just the ones about to go bad. Soonest-expiring first.
    Use alongside get_expiring_soon when generating a weekly plan: this is
    a softer, general nudge to favor meats/seafood/produce/dairy already on
    hand over buying more of the same, distinct from near_expiring_inventory's
    stronger "use this up before it's wasted" signal.
    """
    # The same clock get_expiring_soon reads: this is its complement, and
    # two clocks here would let one item be in both lists or in neither.
    cutoff = (_today() + timedelta(days=near_expiring_days)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, expiration_date FROM inventory_items "
        "WHERE household_id = ? AND category IN ('produce', 'dairy', 'meat/seafood') "
        "AND (expiration_date IS NULL OR expiration_date = '' OR expiration_date > ?) "
        "ORDER BY CASE WHEN expiration_date IS NULL OR expiration_date = '' THEN 1 ELSE 0 END, expiration_date ASC",
        (household_id(), cutoff),
    ).fetchall()
    conn.close()
    return [
        {"id": r["id"], "item": r["item"], "quantity": r["quantity"], "category": r["category"], "expiration_date": r["expiration_date"]}
        for r in rows
    ]


def remove_inventory_item(item_id: int) -> dict:
    """Remove a single inventory item outright (e.g. it spoiled, or was added by mistake) — used by the Inventory view's delete control."""
    conn = get_conn()
    require_household_row(conn, "inventory_items", item_id, label="inventory item")
    conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id()))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "removed": True}
