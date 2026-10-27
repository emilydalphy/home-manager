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
    pieces = _SPLIT_RE.split(text or "")
    out = []
    for piece in pieces:
        row = _said_piece(piece or "")
        if row:
            out.append(row)
    return out[:MAX_ITEMS]


# ---------------------------------------------------------------------------
# Saving the ticked rows, and the Undo
# ---------------------------------------------------------------------------
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


def add_items(items: list, kind: str) -> dict:
    """
    Write the rows the person left ticked. Each goes through
    inventory._add_to_inventory (so a second milk merges into the first,
    the date rules and the category guess are the ones every add uses), in
    one transaction. Returns {"added", "place", "items", "undo"}.

    A shelf photo files every row on its shelf unless the scan saw it was
    frozen (the fridge's freezer drawer); a receipt row with no location
    takes the category's usual shelf, recorded as a guess (scan_source).
    """
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("I couldn't tell where these came from.")
    rows = []
    for raw in (items or [])[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        name = _clean_name(raw.get("item"))
        if not name:
            continue
        loc = str(raw.get("location") or "").strip().lower()
        if loc not in LOCATIONS:
            loc = kind if kind in SHELF_KINDS else ""
        if kind == "type" and not loc:
            loc = "fridge"
        category = str(raw.get("category") or "").strip().lower()
        rows.append({
            "item": name,
            "quantity": str(raw.get("quantity") or "").strip(),
            "category": category if category and category != "other" else None,
            "location": loc,
        })
    if not rows:
        raise ValueError("Nothing was ticked.")
    receipts = []
    with write() as conn:
        for r in rows:
            added = _inventory._add_to_inventory(
                r["item"], r["quantity"], source=_source(kind, r["location"]),
                category=r["category"], location=r["location"] or None, conn=conn,
            )
            after = added.get("after") or {}
            receipts.append({
                "item_id": added["item_id"], "fresh": bool(added["fresh"]),
                "before": added.get("before"), "rev": after.get("rev"),
            })
    return {
        "added": len(rows),
        "place": place_word(kind, rows),
        "items": [r["item"] for r in rows],
        "undo": {"kind": "added", "rows": receipts},
    }


_RESTORE_FIELDS = ("quantity", "source", "category", "expiration_date")


def undo_added(undo: dict) -> dict:
    """
    The Undo on "13 things were added to the fridge." A row the add created
    is taken off; a row it merged into goes back to what it held. A row
    somebody has changed since (its rev moved on) is left alone — the
    Undo is for this add, not for whatever came after it.
    Returns {"undone": n}.
    """
    undo = undo or {}
    if undo.get("kind") != "added" or not isinstance(undo.get("rows"), list):
        raise ValueError("I couldn't tell what to undo.")
    undone = 0
    with write() as conn:
        for rec in undo["rows"][:MAX_ITEMS]:
            try:
                item_id = int(rec.get("item_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            row = conn.execute(
                "SELECT rev FROM inventory_items WHERE id = ? AND household_id = ?",
                (item_id, household_id()),
            ).fetchone()
            if row is None or (rec.get("rev") is not None and row["rev"] != rec.get("rev")):
                continue
            before = rec.get("before")
            if rec.get("fresh") or not isinstance(before, dict):
                conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id()))
            else:
                conn.execute(
                    f"UPDATE inventory_items SET {', '.join(f + ' = ?' for f in _RESTORE_FIELDS)}, "
                    "updated_at = datetime('now') WHERE id = ? AND household_id = ?",
                    [before.get(f) for f in _RESTORE_FIELDS] + [item_id, household_id()],
                )
            undone += 1
    return {"undone": undone}
