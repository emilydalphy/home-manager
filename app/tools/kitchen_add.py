"""
Adding to the Kitchen tab (Loop Board C2, 2026-10-27) — the "Add to the
kitchen" sheet's server half. `home` is the Kitchen TAB in code; see
whats_home.py for the naming trap (`kitchen` means Cook).

Four ways in, and the one rule they share: NOTHING IS SAVED BEFORE THE
REVIEW. A photo (a shelf or a receipt) and a sentence ("Two litres of milk
and a dozen eggs") each come back as a draft list; only add_items writes,
and only what the person left ticked. Type it is one thing the person
typed, so it saves straight away — through add_items too.

  review_items(items, place)  a scan's raw output -> the review's rows, each
                              with `sure`. Items Pomona isn't sure of start
                              unticked: the model's own confidence 'low', a
                              name with a question mark, or a vague name.
  read_said_items(text)       a sentence -> the review's rows. Deterministic
                              (no model call): what it can't read it leaves
                              on the name, and the review is where that gets
                              caught.
  add_items(items, kind)      writes the ticked rows; returns an `undo`.
  undo_added(undo)            the toast's Undo: fresh rows go, merged rows
                              go back to what they were.
"""
from __future__ import annotations

import json
import re

from ..db import write
from ._shared import household_id
from . import inventory as _inventory
from . import quantities as _quantities

LOCATIONS = ("fridge", "freezer", "pantry")
# Which door a draft came through. The shelf kinds name the place the photo
# was of; a receipt and a sentence name food, never a shelf.
KINDS = ("fridge", "freezer", "pantry", "receipt", "say", "type")
SHELF_KINDS = ("fridge", "freezer", "pantry")
# One review is one shelf, one receipt or one sentence.
MAX_ITEMS = 80

# Provenance per kind, for inventory_items.source. The shelf photos and the
# receipt keep the names inventory.scan_source already writes; a sentence's
# shelf is a guess (inventory.GUESSED_LOCATION_SOURCES lists it), a typed
# one is the shelf the person picked.
SAID_SOURCE = "kitchen_said"
TYPED_SOURCE = "kitchen_typed"


# ---------------------------------------------------------------------------
# "Isn't sure of"
# ---------------------------------------------------------------------------
# A name that says what kind of thing it is but not which food: a photo of a
# jar is not a food. Matched on the whole name.
_VAGUE_NAMES = frozenset({
    "item", "items", "food", "something", "thing", "stuff", "unknown",
    "container", "containers", "jar", "jars", "bottle", "bottles", "box",
    "boxes", "bag", "bags", "package", "packages", "packet", "tub", "tubs",
    "can", "cans", "tin", "tins", "carton", "tupperware", "wrapped item",
    "condiment", "condiments", "sauce", "sauces", "leftover container",
    "misc", "miscellaneous", "groceries", "produce", "other",
})
# A name that opens by hedging.
_HEDGE_RE = re.compile(
    r"^(unknown|unidentified|unlabeled|unlabelled|possibly|maybe|probably|perhaps|"
    r"some kind of|some sort of|some type of|assorted|various|misc\.?|mystery)\b"
)
# "milk or cream", "(?)", "parsley/cilantro": two guesses are no answer.
_EITHER_RE = re.compile(r"\bor\b|\(\?\)|\w/\w")


def is_sure(item: dict) -> bool:
    """
    Whether a scanned row starts ticked. The scan tool's own `confidence`
    is the first word (agent._SCAN_ITEMS_TOOL has asked for it since the
    scans were built); a row without one is read as sure, so an older or
    partial answer never unticks everything. Then the name itself: a
    question mark, a hedge, two guesses, or a container rather than a food.
    """
    if (str(item.get("confidence") or "")).strip().lower() == "low":
        return False
    name = str(item.get("item") or "").strip()
    if not name or "?" in name:
        return False
    low = name.lower().strip(" .!")
    if low in _VAGUE_NAMES or _HEDGE_RE.search(low) or _EITHER_RE.search(low):
        return False
    return True


def _clean_name(name) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    return text.rstrip("?").strip(" ,.;")


def review_items(items: list, place: str | None = None) -> list[dict]:
    """
    A scan's draft list, shaped for the review. Every row keeps the scan's
    item / quantity / category / location and gains `sure` (see is_sure).
    The name loses a trailing "?" — the review says "not sure" beside it
    instead — and empty names are dropped. `place` fills a row with no
    location (a shelf photo always knows its shelf).
    """
    out = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        name = _clean_name(raw.get("item"))
        if not name:
            continue
        name = name[:1].upper() + name[1:]  # scans come back in lower case
        loc = str(raw.get("location") or "").strip().lower() or (place or "")
        out.append({
            "item": name,
            "quantity": str(raw.get("quantity") or "").strip(),
            "category": str(raw.get("category") or "").strip() or "other",
            "location": loc if loc in LOCATIONS else "",
            "confidence": str(raw.get("confidence") or "").strip().lower() or "high",
            "sure": is_sure(raw),
        })
    return out[:MAX_ITEMS]


def left_out(rows: list[dict], place: str | None = None) -> tuple[list[dict], dict]:
    """
    The Kitchen tab never shows a spice or one of the household's regulars
    (whats_home._shown_rows: they live with Staples), so saving one from a
    scan would be a row nobody sees and a toast that over-counts. They come
    off the review and are NAMED beside it instead — never dropped silently.
    Returns (rows kept, {"spices": [names], "regulars": [names]}).
    """
    from ..db import get_conn
    from . import spices as _spices
    from . import use_up as _use_up

    conn = get_conn()
    try:
        regulars = _use_up._staple_keys(conn)
        kept, out = [], {"spices": [], "regulars": []}
        for r in rows:
            if _use_up._name_key(r["item"]) in regulars:
                out["regulars"].append(r["item"])
                continue
            if _spices.is_spice(r["item"]):
                out["spices"].append(r["item"])
                continue
            # A shelf photo confirms a row already on that shelf (add_items),
            # so a new amount REPLACES the old one: say what it was, so a
            # lower count is seen before it's saved ("Eggs · 1 (was 6)").
            if place in SHELF_KINDS and r.get("quantity"):
                found = _existing(conn, r["item"], r.get("location") or place)
                old = (found["quantity"] or "").strip() if found is not None else ""
                if old and old.lower() != r["quantity"].strip().lower():
                    r["was"] = old
            kept.append(r)
    finally:
        conn.close()
    return kept, out


# ---------------------------------------------------------------------------
# Say it
# ---------------------------------------------------------------------------
_NUMBER_WORDS = {
    "a": "1", "an": "1", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "a couple of": "2",
    "a couple": "2", "couple of": "2", "a few": "3", "half a": "½", "half an": "½",
    "a half": "½",
}
_NUMBER_RE = re.compile(
    r"^(" + "|".join(sorted((re.escape(w) for w in _NUMBER_WORDS), key=len, reverse=True)) + r")\b\s*",
    re.IGNORECASE,
)
# What people say before the food: "I bought", "we've got", "there's".
_LEAD_IN_RE = re.compile(
    r"^(?:(?:i|we)\s+(?:just\s+)?(?:bought|got|have|picked up|grabbed)|(?:i've|we've)\s+got|"
    r"there's|there are|there is|add|also|plus|got|some|and)\b\s*",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"\s*(?:,|;|\n|\band\b|\bplus\b|&)\s*", re.IGNORECASE)
# "in the fridge" / "to the freezer" said after a thing says where it goes.
_PLACE_RE = re.compile(r"\s*\b(?:in|into|to|for)\s+(?:the\s+)?(fridge|freezer|pantry|cupboard)\b\s*", re.IGNORECASE)


def _said_piece(piece: str) -> dict | None:
    from .. import recipe_import as _recipe_import  # imported here: it imports the tools package

    text = piece.strip(" .!")
    place = ""
    m = _PLACE_RE.search(text)
    if m:
        place = "pantry" if m.group(1).lower() == "cupboard" else m.group(1).lower()
        text = (text[: m.start()] + " " + text[m.end():]).strip()
    for _ in range(3):
        stripped = _LEAD_IN_RE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    if not text:
        return None
    m = _NUMBER_RE.match(text)
    if m:
        text = _NUMBER_WORDS[m.group(1).lower()] + " " + text[m.end():]
    # "1 dozen eggs" reads as a count of dozens; say it the way it's bought.
    text = re.sub(r"^1\s+dozen\b", "12", text, flags=re.IGNORECASE)
    # "1 loaf of bread", "2 tubs of yogurt": the word before "of" is how it's
    # bought, whatever it is.
    m = re.match(r"^(\d+(?:[./]\d+)?|½)\s+([a-z]+)\s+of\s+(.+)$", text, flags=re.IGNORECASE)
    if m:
        parsed = {"qty": f"{m.group(1)} {m.group(2)}", "item": m.group(3)}
    else:
        parsed = _recipe_import.split_ingredient_line(text)
    name = re.sub(r"^of\s+", "", parsed.get("item") or "", flags=re.IGNORECASE).strip(" ,.")
    if not name:
        return None
    # Said in lower case; written the way the rest of the kitchen reads.
    name = name[:1].upper() + name[1:]
    qty = (parsed.get("qty") or "").strip()
    category = _inventory._new_row_category(name, None, place or None)
    return {
        "item": name,
        "quantity": qty,
        "category": category,
        "location": place or _quantities._resolve_location(None, category),
        "confidence": "high",
        "sure": True,
    }


def read_said_items(text: str) -> list[dict]:
    """
    "Two litres of milk and a dozen eggs" -> milk · 2 litres, eggs · 12.
    Split on commas, "and", "plus"; each piece's leading amount read the
    way a recipe line is (recipe_import.split_ingredient_line, after number
    words become digits). Every row is ticked: it is what the person said,
    and the review is where a mishearing gets unticked. The shelf is the
    one they said ("…in the freezer"), else the category's usual one.
    """
    text = text or ""
    # One food whose name has "and" in it stays one row.
    for phrase in _NEVER_SPLIT:
        text = re.sub(r"\b" + phrase.replace(" and ", r"\s+and\s+") + r"\b", phrase.replace(" and ", " _AND_ "),
                      text, flags=re.IGNORECASE)
    pieces = _SPLIT_RE.split(text)
    out = []
    for piece in pieces:
        row = _said_piece((piece or "").replace(" _AND_ ", " and "))
        if row:
            low = row["item"].lower()
            if low in _NEVER_SPLIT_PLACE:
                row["location"] = _NEVER_SPLIT_PLACE[low]
                row["category"] = "other"
            out.append(row)
    # Dictation often leaves the commas out: "milk eggs butter", or "milk
    # eggs and butter" (a piece of "milk eggs"). A piece with no amount
    # whose EVERY word is a food on its own becomes a row per word, unless
    # the words are one food together ("orange juice", "cream cheese").
    # Anything less certain stays as said.
    split = []
    for row in out:
        words = row["item"].split()
        if (not row["quantity"] and len(words) >= 2 and row["item"].lower() not in _TWO_WORD_FOODS
                and row["item"].lower() not in _quantities._ITEM_SHELF_LIFE_DAYS
                and all(_known_food(w) for w in words)):
            split.extend(r for r in (_said_piece(w) for w in words) if r)
        else:
            split.append(row)
    return split[:MAX_ITEMS]


# Foods with "and" in the name. Said whole, they're one thing.
_NEVER_SPLIT = (
    "mac and cheese", "macaroni and cheese", "fish and chips", "salt and pepper",
    "half and half", "sweet and sour sauce", "peanut butter and jelly", "chips and salsa",
)
# Two known foods that are one food together: never split by the
# comma-less rule.
_TWO_WORD_FOODS = frozenset({
    "orange juice", "apple juice", "cream cheese", "sour cream", "ice cream", "heavy cream",
    "peanut butter", "almond butter", "olive oil", "chicken stock", "beef stock", "chicken broth",
    "green beans", "green onions", "spring onions", "sweet potatoes", "sweet potato", "egg noodles",
    "rice noodles", "tomato sauce", "apple sauce", "chocolate milk", "almond milk", "oat milk",
    "coconut milk", "butter beans", "bread crumbs", "corn tortillas", "flour tortillas",
})
# ...and the one whose usual shelf a guess gets wrong (pepper reads as produce).
_NEVER_SPLIT_PLACE = {"salt and pepper": "pantry"}
# Pantry foods the shelf-life table doesn't name, for the no-comma rule.
_PANTRY_WORDS = frozenset({
    "rice", "pasta", "flour", "sugar", "oats", "cereal", "coffee", "tea", "beans",
    "lentils", "bread", "juice", "honey", "jam", "crackers", "noodles", "tortillas",
})


def _known_food(word: str) -> bool:
    from .. import recipe_import as _recipe_import

    w = word.strip(" ,.").lower()
    if not w or len(w) < 3:
        return False
    singular = w[:-1] if w.endswith("s") else w
    if w in _PANTRY_WORDS or singular in _PANTRY_WORDS:
        return True
    if w in _quantities._ITEM_SHELF_LIFE_DAYS or singular in _quantities._ITEM_SHELF_LIFE_DAYS:
        return True
    return _recipe_import.guess_category(w) in ("produce", "dairy", "meat/seafood", "frozen")


# ---------------------------------------------------------------------------
# Saving the ticked rows, and the Undo
# ---------------------------------------------------------------------------
MAX_NAME = 80
MAX_QTY = 40
_QTY_NUMBER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")
# A size or a fat percentage is not part of what the thing IS: "milk 2%" and
# "Milk" are the same row, as are "Eggs 12 pack" and "eggs".
_SIZE_TOKEN_RE = re.compile(r"\S*\d\S*|%")


def _source(kind: str, location: str) -> str:
    if kind == "say":
        return SAID_SOURCE
    if kind == "type":
        return TYPED_SOURCE
    return _inventory.scan_source(kind, location)


def place_word(kind: str, items: list[dict]) -> str:
    """Where the button and the toast say the things went: the shelf the
    photo was of, else the one shelf every row shares, else the kitchen."""
    if kind in SHELF_KINDS:
        return kind
    places = {i.get("location") for i in items}
    if len(places) == 1 and next(iter(places)) in LOCATIONS:
        return next(iter(places))
    return "kitchen"


def _match_key(name: str) -> str:
    """The name two rows have to share to be the same food — the grocery
    list's merge key (case, spacing, a trailing plural, the plain-name
    list), after any size or percentage is taken off."""
    from . import grocery as _grocery

    base = re.sub(r"\s+", " ", _SIZE_TOKEN_RE.sub(" ", str(name or ""))).strip()
    return _grocery._merge_key(base or str(name or ""))


def _clean_rows(items, kind: str) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("I couldn't read that list.")
    if len(items) > MAX_ITEMS:
        raise ValueError("That's a lot at once — try one shelf at a time.")
    rows = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("I couldn't read that list.")
        if not isinstance(raw.get("item"), str):
            raise ValueError("Each thing needs a name.")
        name = _clean_name(raw["item"])
        if not name:
            continue
        if len(name) > MAX_NAME:
            raise ValueError("That name is too long — try a shorter one.")
        qty_raw = raw.get("quantity")
        if qty_raw is None:
            qty = ""
        elif isinstance(qty_raw, bool) or not isinstance(qty_raw, (str, int, float)):
            raise ValueError("That amount doesn't look right.")
        else:
            qty = str(qty_raw).strip()
        if len(qty) > MAX_QTY or qty.startswith("-"):
            raise ValueError("That amount doesn't look right.")
        m = _QTY_NUMBER_RE.match(qty)
        if m and float(m.group(1)) > 10000:
            raise ValueError("That amount doesn't look right.")
        loc = str(raw.get("location") or "").strip().lower()
        if loc not in LOCATIONS:
            loc = kind if kind in SHELF_KINDS else ""
        category = str(raw.get("category") or "").strip().lower()[:30]
        if kind == "type":
            # Type it reads a leading amount the way Say it does:
            # "2 litres of milk" -> Milk · 2 litres.
            said = _said_piece(name)
            if said and not qty:
                name, qty = said["item"], said["quantity"]
            if not loc:
                loc = "fridge"
        rows.append({"item": name[:1].upper() + name[1:], "quantity": qty,
                     "category": category if category and category != "other" else None, "location": loc})
    if not rows:
        raise ValueError("Nothing was ticked.")
    return rows


def _existing(conn, name: str, location: str | None):
    """The row this name already is: same food by _match_key, at `location`
    when one is given, else anywhere — the most recently touched, the rule
    inventory._find_row_by_name uses."""
    key = _match_key(name)
    # Every column: the undo's before-state includes confirmed_on where a
    # database has it (_restore_fields).
    sql = "SELECT * FROM inventory_items WHERE household_id = ?"
    params = [household_id()]
    if location:
        sql += " AND location = ?"
        params.append(location)
    for row in conn.execute(sql + " ORDER BY updated_at DESC, id DESC", params).fetchall():
        if _match_key(row["item"]) == key:
            return row
    return None


_RESTORE_FIELDS = ("quantity", "source", "category", "expiration_date", "counted_on")


def _restore_fields(conn) -> tuple:
    """_RESTORE_FIELDS, plus confirmed_on where the database has it (the
    pre-draft check's column, added on another branch — read, not assumed)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(inventory_items)").fetchall()}
    return _RESTORE_FIELDS + (("confirmed_on",) if "confirmed_on" in cols else ())


def _rev(conn, item_id: int) -> int:
    return conn.execute(
        "SELECT rev FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())
    ).fetchone()["rev"]


def add_items(items: list, kind: str) -> dict:
    """
    Write the rows the person left ticked, in one transaction. Returns
    {"added", "updated", "place", "items", "undo"}.

    A SHELF PHOTO IS A COUNT of what is there, not a purchase: a ticked row
    already tracked on that shelf (same food, _match_key) is confirmed — its
    amount becomes the photo's when the photo read one, `counted_on` is
    today, and its name, bought date and frozen date stay — so photographing
    the fridge twice never doubles the milk. A row not tracked there is
    added. A receipt, Say it and Type it are purchases: they add
    (inventory._add_to_inventory sums into a matching row), under the
    existing row's name when the food is already tracked, so "milk 2%"
    lands on "Milk".

    A shelf photo files every row on its shelf unless the scan saw it was
    frozen (the fridge's freezer drawer); a receipt row with no location
    takes the category's usual shelf, recorded as a guess (scan_source).
    The Undo's record stays here (kitchen_add_receipts); the toast holds
    only its id.
    """
    from . import grocery as _grocery

    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("I couldn't tell where these came from.")
    rows = _clean_rows(items, kind)
    # ONE undo record per inventory row per save, holding the row as it was
    # before this save FIRST touched it; a later touch only moves its rev.
    # A receipt often lists the same thing twice ("Milk", "milk 2%"), and two
    # records for one row undid it halfway (re-verify, 2026-10-27).
    receipts: dict[int, dict] = {}

    def record(item_id: int, fresh: bool, before: dict | None):
        if item_id in receipts:
            receipts[item_id]["rev"] = _rev(conn, item_id)
            return
        receipts[item_id] = {"item_id": item_id, "fresh": fresh, "before": None if fresh else before,
                             "rev": _rev(conn, item_id)}

    added = updated = 0
    with write() as conn:
        today = _grocery._household_today(conn=conn).isoformat()
        fields = _restore_fields(conn)
        for r in rows:
            where = r["location"] or None
            found = _existing(conn, r["item"], where)
            if kind in SHELF_KINDS and found is not None:
                before = {f: found[f] for f in fields}
                # A count only when the photo read an amount: counted_on is
                # what the stock check trusts (recipes._KitchenStock), and a
                # photo that just SAW the tortillas didn't count them. Seen
                # with no amount is a confirmation (confirmed_on, where it
                # exists) and the amount stays.
                if r["quantity"]:
                    conn.execute(
                        "UPDATE inventory_items SET quantity = ?, counted_on = ?, updated_at = datetime('now') "
                        "WHERE id = ? AND household_id = ?",
                        (r["quantity"], today, found["id"], household_id()),
                    )
                elif "confirmed_on" in fields:
                    conn.execute(
                        "UPDATE inventory_items SET confirmed_on = ?, updated_at = datetime('now') "
                        "WHERE id = ? AND household_id = ?",
                        (today, found["id"], household_id()),
                    )
                record(found["id"], False, before)
                updated += 1
                continue
            if kind not in SHELF_KINDS:
                anywhere = found or (_existing(conn, r["item"], None) if where else None)
                if anywhere is not None:
                    r["item"] = anywhere["item"]
            before_row = _inventory._find_row_by_name(conn, r["item"], where, ", ".join(fields))
            before = {f: before_row[f] for f in fields} if before_row is not None else None
            res = _inventory._add_to_inventory(
                r["item"], r["quantity"], source=_source(kind, r["location"]),
                category=r["category"], location=where, conn=conn,
            )
            record(res["item_id"], bool(res["fresh"]), before)
            added += 1
        cur = conn.execute(
            "INSERT INTO kitchen_add_receipts (household_id, rows_json) VALUES (?, ?)",
            (household_id(), json.dumps(list(receipts.values()))),
        )
        receipt_id = cur.lastrowid
    return {
        "added": added,
        "updated": updated,
        "place": place_word(kind, rows),
        "items": [r["item"] for r in rows],
        "undo": {"kind": "added", "receipt_id": receipt_id},
    }


def undo_added(undo: dict) -> dict:
    """
    The Undo on the save's toast. Only the receipt id comes from the
    client; what to put back is read from kitchen_add_receipts, for this
    household. A row the save created is taken off; a row it added to or
    confirmed goes back to what it held. A row somebody has changed since
    (its rev moved on) is left alone. Runs once. Returns {"undone": n}.
    """
    undo = undo if isinstance(undo, dict) else {}
    receipt_id = None
    if undo.get("kind") == "added":
        try:
            receipt_id = int(undo.get("receipt_id"))
        except (TypeError, ValueError):
            receipt_id = None
    if receipt_id is None:
        raise ValueError("I couldn't tell what to undo.")
    undone = 0
    with write() as conn:
        rec = conn.execute(
            "SELECT rows_json, undone_at FROM kitchen_add_receipts WHERE id = ? AND household_id = ?",
            (receipt_id, household_id()),
        ).fetchone()
        if rec is None:
            raise ValueError("I couldn't tell what to undo.")
        if rec["undone_at"]:
            return {"undone": 0}
        for r in json.loads(rec["rows_json"]):
            row = conn.execute(
                "SELECT rev FROM inventory_items WHERE id = ? AND household_id = ?",
                (r["item_id"], household_id()),
            ).fetchone()
            if row is None or row["rev"] != r["rev"]:
                continue
            if r["fresh"] or not r.get("before"):
                conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (r["item_id"], household_id()))
            else:
                b = r["before"]
                use = [f for f in _restore_fields(conn) if f in b]
                conn.execute(
                    f"UPDATE inventory_items SET {', '.join(f + ' = ?' for f in use)}, "
                    "updated_at = datetime('now') WHERE id = ? AND household_id = ?",
                    [b.get(f) for f in use] + [r["item_id"], household_id()],
                )
            undone += 1
        conn.execute(
            "UPDATE kitchen_add_receipts SET undone_at = datetime('now') WHERE id = ? AND household_id = ?",
            (receipt_id, household_id()),
        )
    return {"undone": undone}


