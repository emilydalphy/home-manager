"""
The pre-shop check -- what the household may already have before shopping,
and the wording used to ask about it.
"""
from __future__ import annotations

import math
from ..db import get_conn
from ._shared import household_id
from . import cooker as _cooker
from . import grocery as _grocery
from . import inventory as _inventory
from . import quantities as _quantities


def get_grocery_already_have_items() -> list[dict]:
    """
    Cross-reference the 'needed' grocery list against tracked inventory to
    flag items that may not actually need buying — e.g. added ad hoc in
    chat before checking, or left over from before inventory caught up.
    Uses the same confident name-match logic as meal-plan ingredient
    auto-adding (get_inventory's items with a non-blank tracked quantity),
    not a guess. Only returns items not yet reviewed (see
    mark_grocery_item_already_have_reviewed) — once the shopper confirms
    they still need something, it drops out of this list for good (even
    though the inventory match still technically exists) rather than
    nagging about the same item every time. Powers the Grocery List view's
    "Already have this?" review section, which pulls these out of the
    normal To-buy list until reviewed.
    """
    needed = _grocery.list_grocery_list(status="needed")
    if not needed:
        return []
    inventory = _inventory.get_inventory()
    have_matches = []
    for it in needed:
        if it.get("already_have_reviewed"):
            continue
        match, confident = _cooker._find_inventory_match(it["item"], inventory)
        if not match or not confident:
            continue
        if not (match.get("quantity") or "").strip():
            continue  # tracked but with no quantity on hand isn't a confident "we have it"
        have_matches.append({
            "item_id": it["id"], "item": it["item"], "quantity": it["quantity"], "category": it["category"],
            "inventory_quantity": match["quantity"], "inventory_location": match.get("location", ""),
        })
    return have_matches


def get_pre_shop_flags() -> list[dict]:
    """
    Same confident inventory cross-reference as get_grocery_already_have_items,
    reshaped for the Grocery screen's pinned "Maybe already home" pre-shop
    check (PRE_SHOP_CHECK.md): a humanised, single-sentence comparison per
    item, computed here so the client never touches raw pack quantities —
    see _pre_shop_humanize_label, which is exactly where the old block's
    "1 stick + 1 stick" bug lived. An item whose wanted or on-hand amount
    can't be reduced to one confident phrase, or whose full sentence would
    run past ~60 characters, is left off entirely rather than shown
    garbled (PRE_SHOP_CHECK.md's "if it can't be said in one sentence,
    don't flag the item"). Also powers the /api/grocery-list and
    /api/grocery-list/by-store "needed" views' exclusion filter, so a
    flagged item never appears twice and never silently vanishes from
    both places at once.
    """
    needed = _grocery.list_grocery_list(status="needed")
    if not needed:
        return []
    inventory = _inventory.get_inventory()
    flags = []
    for it in needed:
        if it.get("already_have_reviewed"):
            continue
        match, confident = _cooker._find_inventory_match(it["item"], inventory)
        if not match or not confident:
            continue
        if not (match.get("quantity") or "").strip():
            continue  # tracked but with no quantity on hand isn't a confident "we have it"
        wanted_label = _pre_shop_humanize_label(it["quantity"])
        on_hand_label = _pre_shop_humanize_label(match["quantity"])
        if not wanted_label or not on_hand_label:
            continue
        sentence = f"You want {wanted_label}. Fridge shows {on_hand_label}."
        if len(sentence) > 60:
            continue
        flags.append({
            "itemId": it["id"],
            "name": it["item"],
            "wantedLabel": wanted_label,
            "onHandLabel": on_hand_label,
            "onHandLocation": match.get("location") or None,
            "sentence": sentence,
        })
    return flags


def drop_grocery_item_pre_shop(item_id: int, author: str = "") -> dict:
    """
    'Drop it' on a pre-shop flag — soft-removes the item (status:
    'removed') rather than deleting it outright, so undo_pre_shop_drop can
    restore it and so a pattern of repeat drops stays around for the
    assistant to learn from later (same reasoning as
    exclude_grocery_item/move_grocery_item_to_inventory's soft-delete
    philosophy — see DATA_AND_API.md's "Sync between the two adults").
    Idempotent: dropping an already-removed item is a no-op. `author` is
    recorded but doesn't yet drive a live cross-device "adult changed
    something" notification — NOTIFICATIONS.md #4 and README's Phase 5
    notes document that this codebase has no concept of "the other adult"
    distinct from "you" at the data layer, and that gap applies here too.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
        "WHERE id = ? AND household_id = ? AND status != 'removed'",
        (author or "", item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "status": "removed"}


def undo_pre_shop_drop(item_id: int) -> dict:
    """
    Undo a pre-shop 'Drop it' — restores the item to 'needed' and marks it
    already_have_reviewed so it goes straight back to its store card
    without being re-flagged this same trip (PRE_SHOP_CHECK.md: undo
    "does not re-add the flag this trip"). Also reused by the Review
    screen's "already have" confirmation section (see
    get_already_have_decisions) to undo a Have it / Already have action, so
    there's no separate "already-have-undo" endpoint — but that flow wrote
    an inventory row a plain pre-shop drop never did, so this also deletes
    that row when (and only when) it's safe to: already_have_inventory_id
    is set only for a fresh-insert write (see
    grocery_items.already_have_inventory_id / move_grocery_item_to_
    inventory), never for one that merged into pre-existing stock — undoing
    a merge would need the pre-merge quantity, which nothing tracks, so
    that case leaves inventory untouched on undo by design.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT already_have_inventory_id FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    conn.execute(
        "UPDATE grocery_items SET status = 'needed', already_have_reviewed = 1, "
        "removed_at = NULL, already_have_inventory_id = NULL WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    if row and row["already_have_inventory_id"]:
        _inventory.remove_inventory_item(row["already_have_inventory_id"])
    return {"item_id": item_id, "status": "needed"}


def get_already_have_decisions() -> list[dict]:
    """
    Review screen's confirmation section (Loop Board: "Review screen
    should confirm the already have decisions for the week") — every
    grocery item currently soft-removed (status='removed') because the
    household said they already have it, from either flow: a pre-shop
    "Maybe already home" Drop it (removed_by holds who dropped it, e.g.
    'user') or a Have it / Already have action on any Grocery List row
    (removed_by == 'already_have', see move_grocery_item_to_inventory).
    Scoped to this week (from this Monday, by removed_at) so old decisions
    don't linger here forever the way they would with no cutoff at all —
    nothing today ever clears a 'removed' row's removed_at. Restorable
    with undo_pre_shop_drop regardless of which flow removed it.
    """
    from datetime import date, timedelta

    monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, store, removed_by, removed_at FROM grocery_items "
        "WHERE household_id = ? AND status = 'removed' AND removed_by != '' "
        "AND removed_at IS NOT NULL AND removed_at >= ? ORDER BY removed_at DESC",
        (household_id(), monday),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def keep_all_pre_shop_flags() -> dict:
    """'Keep all {n}' — resolves every currently flagged pre-shop item as keep, in one write."""
    flags = get_pre_shop_flags()
    conn = get_conn()
    for f in flags:
        conn.execute(
            "UPDATE grocery_items SET already_have_reviewed = 1 WHERE id = ? AND household_id = ?",
            (f["itemId"], household_id()),
        )
    conn.commit()
    conn.close()
    return {"resolved_count": len(flags)}


def mark_grocery_item_already_have_reviewed(item_id: int) -> dict:
    """
    Confirm an item flagged by get_grocery_already_have_items is still
    needed despite the inventory match (e.g. running low) — moves it back
    into the normal To-buy list and stops it from being flagged again for
    this same listing. Does not touch quantity/status; only clears the flag.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET already_have_reviewed = 1 WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "already_have_reviewed": True}


# A separate formatter from _humanize_grocery_quantity above, which rounds
# to nice *decimal* quarters ("3.25 cups") for the normal list display.
# The pre-shop sentence has a stricter rule: never a decimal — a
# fractional remainder becomes a word ("half a stick", not "0.5 stick").
# This is exactly the humanising the old "Already have this?" block
# skipped, showing raw pack math instead (see PRE_SHOP_CHECK.md "Why it
# changed" #3).
_PRE_SHOP_FRACTION_LEAD = {0.25: "a quarter of a {u}", 0.5: "half a {u}", 0.75: "three quarters of a {u}"}


_PRE_SHOP_FRACTION_TAIL = {0.25: "and a quarter", 0.5: "and a half", 0.75: "and three quarters"}


def _pre_shop_pluralize(unit: str, n: float) -> str:
    if n == 1:
        return unit
    if unit in _quantities._UNIT_PLURALS:
        return _quantities._UNIT_PLURALS[unit]
    if unit in _quantities._CONTAINER_UNIT_PLURALS:
        return _quantities._CONTAINER_UNIT_PLURALS[unit]
    return unit


def _pre_shop_amount_words(amount: float, unit: str | None) -> str | None:
    """
    Render amount+unit the way a person would say it out loud: whole
    numbers as plain digits, any fractional remainder as a word ("half",
    "a quarter") rather than a decimal — e.g. 0.5/"stick" -> "half a
    stick", 1.5/"stick" -> "a stick and a half". Returns None for a
    non-positive amount (nothing to say).
    """
    if amount <= 0:
        return None
    whole = math.floor(amount + 1e-9)
    frac = amount - whole
    nearest = min((0.0, 0.25, 0.5, 0.75, 1.0), key=lambda f: abs(f - frac))
    if nearest >= 1.0:
        whole, nearest = whole + 1, 0.0
    if not unit:
        # A bare count ("3", "2.5") — nobody buys a fraction of a plain
        # count, so round to the nearest whole rather than use a fraction
        # word (PRE_SHOP_CHECK.md's "round counts; never show decimals").
        return str(max(int(round(amount)), 1))
    if nearest == 0.0:
        n = max(whole, 1)
        return f"{n} {_pre_shop_pluralize(unit, n)}"
    if whole == 0:
        return _PRE_SHOP_FRACTION_LEAD[nearest].format(u=unit)
    if whole == 1:
        return f"a {unit} {_PRE_SHOP_FRACTION_TAIL[nearest]}"
    return f"{whole} {_pre_shop_pluralize(unit, whole)} {_PRE_SHOP_FRACTION_TAIL[nearest]}"


def _pre_shop_humanize_label(raw_qty: str) -> str | None:
    """
    Turn a raw grocery/inventory quantity string into the plain-language
    label the pre-shop sentence needs. Collapses the "X + Y" artifact left
    behind when _try_consolidate_quantity couldn't reconcile two lines
    (the old block's raw-pack-math bug) into one total whenever every
    piece shares a unit. Returns None when the amount can't be reduced to
    one confident, single-unit phrase, so the caller skips flagging that
    item rather than showing something garbled (PRE_SHOP_CHECK.md: "if it
    can't be said in one sentence, don't flag the item").
    """
    raw = (raw_qty or "").strip()
    if not raw:
        return None
    pieces = [p.strip() for p in raw.split(" + ") if p.strip()]
    if len(pieces) > 1:
        parsed = [_quantities._parse_quantity(p) for p in pieces]
        if any(p is None for p in parsed):
            return None
        units = {p[1] for p in parsed}
        if len(units) > 1:
            return None
        return _pre_shop_amount_words(sum(p[0] for p in parsed), parsed[0][1])
    parsed = _quantities._parse_quantity(pieces[0])
    if parsed:
        return _pre_shop_amount_words(parsed[0], parsed[1])
    # Freeform text ("a bunch", "to taste") is already a single clean
    # phrase — just drop any trailing prep descriptor.
    cleaned = _quantities._strip_prep_descriptor(pieces[0])
    return cleaned or None
