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


_LEADING_NUM_RE = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|[\d.]+)\s*(.*)$")
# "½ cup", "1½ cups", "1 ½ cups" — a glyph is not a \d, so _LEADING_NUM_RE
# alone read "½ cup" as no number at all and "1½ cups" as "1".
_FRACTION_GLYPHS = {"¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
_LEADING_GLYPH_RE = re.compile(r"^(\d+)?\s*([¼½¾⅓⅔⅛])\s*(.*)$")
# "1,5 kg" — a decimal comma, which _LEADING_NUM_RE read as "1".
_LEADING_COMMA_DECIMAL_RE = re.compile(r"^(\d+),(\d+)(?![\d/])\s*(.*)$")


def _step_quantity_text(quantity: str, delta: float) -> str:
    """
    Nudge a freeform inventory quantity string by delta, e.g. "3 gal" -1 ->
    "2 gal". If there's no leading number to step (a bare descriptor like
    "some" or "a bag", or nothing at all), a '+' tap starts a count at 1 and
    keeps the descriptor as a suffix ("a bag" -> "1 a bag" is odd but rare —
    most inventory rows already carry a numeric quantity from the grocery
    checkoff or chat capture that created them); a '-' tap on a
    non-numeric quantity is a no-op. Floors at 0, never goes negative.

    A blank quantity is the common case of that, not a rare one: the
    Kitchen form's amount box is optional. Its "+" answered 500 until
    2026-10-25 — `parsed` was only assigned on the numeric path and read on
    both. A fraction glyph ("½ cup", "1½ cups") and a decimal comma
    ("1,5 kg") step as the amounts they are and keep their own writing.
    """
    quantity = (quantity or "").strip()
    parsed = None
    comma = False
    glyph = _LEADING_GLYPH_RE.match(quantity)
    comma_match = None if glyph else _LEADING_COMMA_DECIMAL_RE.match(quantity)
    m = None if (glyph or comma_match) else _LEADING_NUM_RE.match(quantity)
    if glyph:
        amount = float(glyph.group(1) or 0) + _FRACTION_GLYPHS[glyph.group(2)]
        parsed = (amount, None)
        suffix = glyph.group(3).strip()
    elif comma_match:
        whole, frac, suffix = comma_match.group(1), comma_match.group(2), comma_match.group(3).strip()
        # "1,000 g" is a thousand grams; "1,5 kg", "0,500 kg" and "1,250 l"
        # are decimals — nobody keeps 1250 litres (the same reading as
        # quantities' own, review 2026-10-22).
        unit = (suffix.split() or [""])[0].lower()
        comma = len(frac) != 3 or whole.strip("0") == "" or unit in ("kg", "l", "litre", "litres", "liter", "liters")
        amount = float(f"{whole}.{frac}") if comma else float(whole + frac)
    elif m and m.group(1):
        # A bought cup reads "2/3 cup" since 2026-10-19 (part cups read as
        # cups); reading only the "2" stepped it to "1 /3 cup".
        parsed = _quantities._parse_quantity(m.group(1)) if "/" in m.group(1) else None
        try:
            amount = parsed[0] if parsed else float(m.group(1))
        except ValueError:
            amount = 0.0
        suffix = m.group(2).strip()
    else:
        if delta <= 0:
            return quantity  # nothing numeric to decrement
        amount = 0.0
        suffix = quantity
    amount = max(0.0, amount + delta)
    amount_str = (_quantities._kitchen_number(amount) if parsed else None) or f"{amount:g}"
    if comma:
        amount_str = amount_str.replace(".", ",")
    return f"{amount_str} {suffix}".strip() if suffix else amount_str


def step_inventory_quantity(item_id: int, delta: float) -> dict:
    """Nudge one inventory item's quantity by delta (e.g. +1/-1 from the item detail sheet's stepper)."""
    conn = get_conn()
    row = conn.execute("SELECT quantity FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    new_qty = _step_quantity_text(row["quantity"], delta)
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now'), counted_on = ? "
        "WHERE id = ? AND household_id = ?",
        (new_qty, _today().isoformat(), item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "found": True, "quantity": new_qty}


class InvalidInventoryLocation(ValueError):
    """A shelf that is not one of fridge / freezer / pantry — its own type
    so the route can answer 400 for it and keep 404 for a missing row."""


# A thawed thing with no shelf life of its own (a cooked portion, a bag of
# frozen peas): three days in the fridge, the leftovers rule of thumb.
THAWED_KEEPS_DAYS = 3


def _normalised_location(location: str | None) -> str:
    where = (location or "").strip().lower()
    if where not in _quantities._LOCATION_ORDER:
        raise InvalidInventoryLocation(
            f"'{location}' isn't a place this keeps food — fridge, freezer or pantry."
        )
    return where


def _freezer_door_date(row: dict, to_location: str) -> str | None:
    """
    The date a row moving to `to_location` should carry, or None to leave
    its date alone. `row` needs item, category, location, expiration_date.
    See set_inventory_location for the rule. (update_inventory never moves
    a row: a write naming a shelf only matches a row already on it.)
    """
    was = _quantities._display_location(row).strip().lower()
    today = _today()
    if to_location == "freezer" and was != "freezer":
        # Food already past its date stays past it: a freezer does not
        # unspoil salmon, and re-dating it took the "past" warning away
        # (review, 2026-10-25). Coming OUT, a past date is re-dated all
        # the same: on a freezer row it is almost always the fridge
        # estimate it was bought with, passing while it sat frozen — the
        # bug this rule exists for — and nothing on the row tells the two
        # apart.
        if (row.get("expiration_date") or "9999") < today.isoformat():
            return None
        frozen = (today + timedelta(days=_quantities._DEFAULT_SHELF_LIFE_DAYS["frozen"])).isoformat()
        return None if (row.get("expiration_date") or "") > frozen else frozen
    if was == "freezer" and to_location != "freezer":
        days = _quantities._item_shelf_life_match(row.get("item") or "")
        if days is None:
            category = row.get("category") or "other"
            # "other" too: its 14 days is a guess for an unknown thing on a
            # shelf, not for a thawed lasagna (review, 2026-10-25).
            days = THAWED_KEEPS_DAYS if category in ("frozen", "other") else _quantities._DEFAULT_SHELF_LIFE_DAYS.get(category, THAWED_KEEPS_DAYS)
        return (today + timedelta(days=days)).isoformat()
    return None


def set_inventory_location(item_id: int, location: str) -> dict:
    """
    Move one inventory item to a different storage location (fridge/freezer/pantry) — re-groups it immediately.

    Crossing the freezer door restarts the date (defect hunt, 2026-10-25).
    Every estimated date is a fridge or pantry shelf life, so chicken
    ticked off the list, frozen that night and taken out a fortnight later
    read "12 days past" the moment it reached the fridge — and the
    assistant's start-of-session check told the household so — while the
    same chicken sat in the freezer as "use soon". Into the freezer: the
    frozen shelf life from today, never shortening a later date already
    on the row. Out of it: the item's own fridge/pantry life from today
    (thawed), THAWED_KEEPS_DAYS for food with none of its own. A move
    between fridge and pantry leaves the date alone.
    """
    location = _normalised_location(location)
    conn = get_conn()
    require_household_row(conn, "inventory_items", item_id, label="inventory item")
    row = conn.execute(
        "SELECT item, category, location, expiration_date FROM inventory_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    new_date = _freezer_door_date(dict(row), location)
    if new_date:
        conn.execute(
            "UPDATE inventory_items SET location = ?, expiration_date = ?, updated_at = datetime('now') "
            "WHERE id = ? AND household_id = ?",
            (location, new_date, item_id, household_id()),
        )
    else:
        conn.execute("UPDATE inventory_items SET location = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?", (location, item_id, household_id()))
    # frozen_on (2026-10-26): the day it went IN, so freezer food is planned
    # oldest first (use_up.candidates). Cleared on the way out.
    was = _quantities._display_location(dict(row)).strip().lower()
    if location == "freezer" and was != "freezer":
        conn.execute("UPDATE inventory_items SET frozen_on = ? WHERE id = ? AND household_id = ?",
                     (_today().isoformat(), item_id, household_id()))
    elif location != "freezer" and was == "freezer":
        conn.execute("UPDATE inventory_items SET frozen_on = NULL WHERE id = ? AND household_id = ?",
                     (item_id, household_id()))
    conn.commit()
    conn.close()
    result = {"item_id": item_id, "location": location}
    if new_date:
        result["expiration_date"] = new_date
    return result


class CannotFreeze(ValueError):
    """freeze_item on a row that is already in the freezer."""


def freeze_item(item_id: int) -> dict:
    """
    Move one fridge/pantry row to the Freezer, dated today ("frozen today").

    The one "freeze this" door (2026-10-26, Loop Board B3; the Kitchen tab
    reuses it). The move itself is set_inventory_location's, so the
    freezer-door date rule is the same one every move follows; this adds
    the undo. Returns {"item_id", "item", "location": "freezer",
    "frozen_on", "expiration_date", "undo"} — hand `undo` back to
    unfreeze_item unchanged to put the row exactly where it was.
    Raises CannotFreeze for a row already in the freezer.
    """
    conn = get_conn()
    require_household_row(conn, "inventory_items", item_id, label="inventory item")
    row = conn.execute(
        "SELECT id, item, category, location, expiration_date, frozen_on FROM inventory_items "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    conn.close()
    before = dict(row)
    if _quantities._display_location(before).strip().lower() == "freezer":
        raise CannotFreeze(f"{before['item']} is already in the freezer.")
    moved = set_inventory_location(item_id, "freezer")
    conn = get_conn()
    after = conn.execute(
        "SELECT expiration_date, frozen_on FROM inventory_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    conn.close()
    return {
        "item_id": item_id,
        "item": before["item"],
        "location": moved["location"],
        "frozen_on": after["frozen_on"],
        "expiration_date": after["expiration_date"],
        "undo": {
            "item_id": item_id,
            "location": before["location"] or "",
            "expiration_date": before["expiration_date"],
            "frozen_on": before["frozen_on"],
        },
    }


def unfreeze_item(undo: dict) -> dict:
    """
    Undo a freeze_item: put the row back on its shelf with the date it had.
    Only while it is still in the freezer — a row that has moved since, or
    is gone, is left alone and {"undone": False} comes back.
    """
    try:
        item_id = int((undo or {}).get("item_id"))
    except (TypeError, ValueError):
        raise ValueError("I couldn't tell which item to put back.")
    conn = get_conn()
    row = conn.execute(
        "SELECT item, location, category FROM inventory_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None or _quantities._display_location(dict(row)).strip().lower() != "freezer":
        conn.close()
        return {"item_id": item_id, "undone": False}
    conn.execute(
        "UPDATE inventory_items SET location = ?, expiration_date = ?, frozen_on = ?, updated_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (undo.get("location") or "", undo.get("expiration_date"), undo.get("frozen_on"), item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "undone": True, "location": undo.get("location") or ""}


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
    conn.execute(
        "UPDATE inventory_items SET expiration_date = ?, updated_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (new_date, item_id, household_id()),
    )
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
    # The same unit, or one family apart (lb and oz, cups and tbsp):
    # quantities._convert_to_unit. The grocery line rounds to whichever unit
    # reads best, so a recipe's "1 lb" chicken came home as "12 oz" (three
    # eaters of four) and cooking it took nothing off — the Monday walk,
    # 2026-10-26: last Monday's chicken sat in the fridge as on hand, and
    # approving this week left Saturday's stir-fry chicken off the list.
    used = (_quantities._convert_to_unit(minus_parsed[0], minus_parsed[1], existing_parsed[1])
            if minus_parsed else None)
    if used is not None:
        remaining = existing_parsed[0] - used
        if remaining <= 0:
            return None, True
        if minus_parsed[1] != existing_parsed[1] and abs(remaining * 4 - round(remaining * 4)) > 1e-9:
            # Across units the difference can be an odd fraction ("0.9375
            # cup", "0.99375 lb"): say it in the finer unit instead (15 tbsp,
            # 15.9 oz). Never rounded up — a tbsp off a cup must come off.
            finer = _quantities._convert_to_unit(remaining, existing_parsed[1], minus_parsed[1])
            if finer is not None:
                return _quantities._format_quantity(round(finer, 1), minus_parsed[1]), True
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


# Names whose fresh-sounding word is not the food on the shelf: canned
# tuna is not fresh fish, cream of mushroom soup is not cream, chicken
# nuggets and leftover chili are cooked food (review, 2026-10-25).
_NOT_FRESH_WORDS_RE = re.compile(
    r"\b(canned|can|cans|tinned|tin|tins|jar|jarred|soup|soups|nuggets?|leftovers?|"
    r"dried|powder(ed)?|broth|stock|sauce|instant|boxed|mix)\b"
)


def _new_row_category(item: str, category: str | None, location: str | None = None) -> str:
    """
    The category a NEW inventory row is written with when nobody gave one.

    The Kitchen screen's add form has no category box, so every row it
    created was "other" — milk under Other, chicken thighs that the meal
    planner's "favour what's on hand" list (get_fresh_perishable_inventory,
    which reads produce/dairy/meat) never saw (defect hunt, 2026-10-25).
    recipe_import.guess_category reads the name the way the grocery list
    does. Only a positive read is taken — produce, dairy, meat/seafood,
    frozen; its fallback for a name it does not know is "pantry", and a
    pantry row is dated six months out, so an unknown name stays "other"
    (two weeks) as before.

    And only a read that agrees with the shelf the household chose (the
    form always sends one). "Canned tuna" read as meat/seafood went in the
    pantry dated three days out and was "turning soon" by the weekend
    (_GUESS_SHELVES). A name with a not-fresh word in it
    (_NOT_FRESH_WORDS_RE) is never guessed.
    """
    if category:
        return category
    name = (item or "").strip().lower()
    if not name or _NOT_FRESH_WORDS_RE.search(name):
        return "other"
    # Imported here: recipe_import imports the tools package.
    from .. import recipe_import as _recipe_import

    guess = _recipe_import.guess_category(name)
    if guess not in ("produce", "dairy", "meat/seafood", "frozen"):
        return "other"
    where = (location or "").strip().lower()
    if where and where not in _GUESS_SHELVES[guess]:
        return "other"
    return guess


# Where each guessed category may be kept for the guess to stand. Meat is
# frozen as often as not; produce or dairy in the pantry is a can or a box.
_GUESS_SHELVES = {
    "produce": {"fridge"}, "dairy": {"fridge"},
    "meat/seafood": {"fridge", "freezer"}, "frozen": {"freezer"},
}


def _estimated_date(category: str, item: str, location: str) -> str:
    """
    The date a new row with none given is written with: the item's fridge
    or pantry shelf life — or, on a row going straight into the freezer,
    the frozen one. Chicken thighs added "in the freezer" were dated two
    days out, the fridge's number, and Kitchen showed them as "use soon"
    (defect hunt, 2026-10-25). set_inventory_location applies the same
    rule when a row crosses the freezer door.
    """
    if (location or "").strip().lower() == "freezer":
        return (_today() + timedelta(days=_quantities._DEFAULT_SHELF_LIFE_DAYS["frozen"])).isoformat()
    name = (item or "").strip().lower()
    if _NOT_FRESH_WORDS_RE.search(name):
        # The item table matches substrings, so "cream of mushroom soup"
        # took mushrooms' five days and "chicken broth" chicken's two
        # (review, 2026-10-25). Cooked leftovers keep a leftover's days;
        # anything else canned, jarred or dried keeps its category's.
        days = THAWED_KEEPS_DAYS if re.search(r"\bleftovers?\b", name) else \
            _quantities._DEFAULT_SHELF_LIFE_DAYS.get(category, _quantities._DEFAULT_SHELF_LIFE_DAYS["other"])
        return (_today() + timedelta(days=days)).isoformat()
    return _quantities._estimate_expiration_date(category, item)


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
        old_use_by = (existing["expiration_date"] or "").strip()
        if (not expiration_date and old_use_by and (category or existing["category"])
                and (location or _quantities._display_location(dict(existing))).strip().lower() != "freezer"
                and old_use_by < _grocery._household_today(conn=conn).isoformat()):
            # Fresh food bought onto a row already past its use-by
            # (2026-10-25): what was there has gone off, so the row becomes
            # the new food — its amount and its date — rather than the old
            # date on the sum (fresh pasta read as expired the day it was
            # bought) or the new date on the sum (3 lb "in date" of which
            # 2 lb had turned). The untick's receipt still restores the row.
            params[0] = quantity
            resolved_exp = _quantities._estimate_expiration_date(category or existing["category"], item)
        if resolved_exp:
            fields += ", expiration_date = ?"
            params.append(resolved_exp)
        if category:
            fields += ", category = ?"
            params.append(category)
        if location and location != existing["location"]:
            fields += ", location = ?"
            params.append(location)
        params += [existing["id"], household_id()]
        before = _receipt_snapshot(conn, existing["id"])
        conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ? AND household_id = ?", params)
        item_id = existing["id"]
    else:
        before = None
        item_category = _new_row_category(item, category, location)
        item_location = _quantities._resolve_location(location, item_category)
        cur = conn.execute(
            "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (household_id(), item, quantity, source, expiration_date or _estimated_date(item_category, item, item_location), item_category, item_location),
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
    matches the item; if omitted, a new row is filed by its name
    (_new_row_category), 'other' when the name says nothing. location (fridge,
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
    # A blank name was saved as a nameless row, and a location outside the
    # three shelves as a row Kitchen never drew (defect hunt, 2026-10-25).
    item = (item or "").strip()
    if not item:
        raise ValueError("Name the item to keep track of.")
    if location is not None and location.strip():
        location = _normalised_location(location)
    else:
        location = None
    if action == "add":
        # The receipt fields (fresh/before/after) are mark_grocery_item's
        # business, not the chat agent's — keep this tool's answer as it was.
        added = _add_to_inventory(item, quantity, source=source, expiration_date=expiration_date, category=category, location=location)
        return {"item_id": added["item_id"], "item": added["item"]}

    if action == "set":
        conn = get_conn()
        existing = _find_row_by_name(conn, item, location, "id, category, expiration_date, location")
        if existing:
            # The household's own count: see db._MIGRATIONS' counted_on.
            fields = "quantity = ?, updated_at = datetime('now'), counted_on = ?"
            params = [quantity, _today().isoformat()]
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
            params += [existing["id"], household_id()]
            conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ? AND household_id = ?", params)
            conn.commit()
            item_id = existing["id"]
        else:
            item_category = _new_row_category(item, category, location)
            item_location = _quantities._resolve_location(location, item_category)
            cur = conn.execute(
                "INSERT INTO inventory_items (household_id, item, quantity, source, category, expiration_date, location, counted_on) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (household_id(), item, quantity, source, item_category, expiration_date or _estimated_date(item_category, item, item_location), item_location, _today().isoformat()),
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
            conn.execute(
                "DELETE FROM inventory_items WHERE id = ? AND household_id = ?",
                (existing["id"], household_id()),
            )
            conn.commit()
            conn.close()
            return {"item": item, "removed": True}
        # A partial use is the household's own count too (counted_on).
        conn.execute(
            "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now'), counted_on = ? "
            "WHERE id = ? AND household_id = ?",
            (remaining, _today().isoformat(), existing["id"], household_id()),
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
        try:
            results.append(update_inventory(name, act, quantity=qty, expiration_date=exp,
                                            category=cat, location=loc, source=src))
        except InvalidInventoryLocation as e:
            # One entry's bad shelf is that entry's answer, not the batch's:
            # raising here would leave the entries before it written, and a
            # retried batch would add those twice.
            results.append({"item": name, "error": str(e)})
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
        # Folded and defaulted: a row saved as "Freezer" or "counter" (the
        # route never checked) used to get a bucket of its own here, which
        # the screen — it draws only fridge/freezer/pantry — never showed,
        # so the item vanished from Kitchen (defect hunt, 2026-10-25).
        loc = (it["location"] or "").strip().lower()
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

    Not the freezer. Every estimated date is a FRIDGE/pantry shelf life
    (quantities._ITEM_SHELF_LIFE_DAYS says so), so a pack of chicken thighs
    told to the app as "in the freezer" was dated two days out and then said
    "turns soon" every morning, in the same text that told the household to
    move it to the fridge for Friday (defect hunt, 2026-10-22). Frozen food
    left too long is freezer_portions.sweep_use_soon's to say, not this.
    A row with a blank location counts as frozen by its category, the
    same fallback as quantities._display_location.
    """
    today_date = _today()
    cutoff = (today_date + timedelta(days=days)).isoformat()
    today = today_date.isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, expiration_date FROM inventory_items "
        "WHERE household_id = ? AND expiration_date IS NOT NULL AND expiration_date != '' AND expiration_date <= ? "
        "AND COALESCE(NULLIF(location, ''), category, '') NOT IN ('freezer', 'frozen') "
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
