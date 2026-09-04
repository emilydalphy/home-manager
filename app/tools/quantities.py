"""
Parsing, converting and humanizing ingredient quantities, units and
storage locations. Shared by grocery, inventory and the pre-shop check.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta


# Standard store sections, in a sensible shopping order. "meat" is an alias
# kept for rows saved before "meat/seafood" was standardized.
_GROCERY_SECTION_ORDER = ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]


_GROCERY_CATEGORY_ALIASES = {"meat": "meat/seafood", "seafood": "meat/seafood"}


# Storage location — independent from category/food-type, since an item's
# physical location can diverge from its grocery-aisle category (an opened
# sauce is category='pantry' by food type but lives in the fridge once
# opened). Falls back to a category-based guess when not stated explicitly.
_LOCATION_ORDER = ["fridge", "freezer", "pantry"]


_DEFAULT_LOCATION_BY_CATEGORY = {
    "produce": "fridge",
    "dairy": "fridge",
    "meat/seafood": "fridge",
    "frozen": "freezer",
    "pantry": "pantry",
    "other": "pantry",
}


def _resolve_location(explicit_location: str | None, category: str | None) -> str:
    if explicit_location:
        return explicit_location
    return _DEFAULT_LOCATION_BY_CATEGORY.get(category or "other", "pantry")


def _display_location(item: dict) -> str:
    """An item's location, falling back to the category-based default for legacy rows saved before location was tracked."""
    return item.get("location") or _DEFAULT_LOCATION_BY_CATEGORY.get(item.get("category"), "pantry")


# Phase 4, §4.2: default shelf life (days) by broad category, used only as
# a fallback when an item isn't found in _ITEM_SHELF_LIFE_DAYS below.
# Deliberately a plain code constant, not a DB table/setting — the PRD
# calls for global defaults only this phase, not household-customizable,
# consistent with not building a tunable setting before there's dogfooding
# data to justify it. Rough, conservative estimates; an explicit
# expiration_date (from the user, or a receipt/photo scan) always wins.
_DEFAULT_SHELF_LIFE_DAYS = {
    "produce": 7,
    "dairy": 10,
    "meat/seafood": 3,
    "pantry": 180,
    "frozen": 90,
    "other": 14,
}


# Item-level shelf life (days), refrigerated/pantry as typical for that
# item, adapted from general USDA FoodKeeper / FDA freshness guidance —
# still rough rule-of-thumb estimates, not a live lookup against any
# database, and always overridden by an explicit expiration_date. Keys are
# matched as substrings against the (lowercased) item name, longest key
# first, so "sweet potato" matches before the more generic "potato". Falls
# back to the category-level default in _DEFAULT_SHELF_LIFE_DAYS when no
# item key matches.
_ITEM_SHELF_LIFE_DAYS = {
    # Dairy
    "milk": 7, "buttermilk": 14, "yogurt": 14, "sour cream": 14,
    "heavy cream": 10, "half and half": 7, "cream cheese": 14,
    "feta": 30, "mozzarella": 14, "burrata": 3, "parmesan": 60,
    "cheddar": 30, "cheese": 21, "butter": 90, "eggs": 21, "egg": 21,
    # Produce
    "lettuce": 7, "spinach": 5, "arugula": 5, "kale": 7, "salad mix": 5,
    "greens": 5, "berries": 5, "strawberr": 5, "raspberr": 4,
    "blueberr": 10, "blackberr": 4, "banana": 5, "apple": 21,
    "avocado": 5, "tomato": 7, "cucumber": 7, "zucchini": 7,
    "broccoli": 7, "cauliflower": 10, "carrot": 21, "celery": 14,
    "bell pepper": 10, "pepper": 10, "mushroom": 5, "onion": 30,
    "garlic": 30, "ginger": 21, "sweet potato": 21, "potato": 30,
    "lemon": 21, "lime": 21, "cilantro": 5, "parsley": 7, "basil": 5,
    "mint": 5, "asparagus": 4,
    # Meat / seafood
    "ground beef": 2, "ground turkey": 2, "ground pork": 2,
    "chicken": 2, "turkey": 2, "steak": 3, "pork": 3, "beef": 3,
    "salmon": 2, "shrimp": 2, "fish": 2, "seafood": 2, "bacon": 7,
    "sausage": 5, "deli meat": 5, "ham": 5,
    # Pantry (longer-lived; category default of 180 already covers most)
    "bread": 7, "tortilla": 14,
}


def _lookup_item_shelf_life_days(item: str, category: str | None) -> int:
    name = item.strip().lower()
    best_match: tuple[str, int] | None = None
    for key, days in _ITEM_SHELF_LIFE_DAYS.items():
        if key in name and (best_match is None or len(key) > len(best_match[0])):
            best_match = (key, days)
    if best_match:
        return best_match[1]
    return _DEFAULT_SHELF_LIFE_DAYS.get(category, _DEFAULT_SHELF_LIFE_DAYS["other"])


def _estimate_expiration_date(category: str, item: str = "", from_date: date | None = None) -> str:
    """ISO date estimate for when this item likely goes bad, starting from today (or from_date). Checks _ITEM_SHELF_LIFE_DAYS for an item-specific estimate first, falling back to the category-level default in _DEFAULT_SHELF_LIFE_DAYS."""
    days = _lookup_item_shelf_life_days(item, category)
    base = from_date or date.today()
    return (base + timedelta(days=days)).isoformat()


def _resolved_expiration_update(
    explicit_expiration_date: str | None,
    new_category: str | None,
    existing_category: str | None,
    existing_expiration_date: str | None,
    item: str = "",
) -> str | None:
    """
    Work out what (if anything) an inventory write should set
    expiration_date to, without ever clobbering something more specific
    than what's being provided now:
      - An explicit date always wins outright.
      - Otherwise, estimate/re-estimate only if there's no date yet, or the
        only date on file was itself a guess from the generic 'other'
        bucket and a real category is now known — refining an unknown-item
        placeholder, not overwriting a specific-category estimate.
      - Returns None when nothing should change.
    """
    if explicit_expiration_date:
        return explicit_expiration_date
    effective_category = new_category or existing_category
    if not effective_category:
        return None
    if not existing_expiration_date:
        return _estimate_expiration_date(effective_category, item)
    if existing_category == "other" and new_category and new_category != "other":
        return _estimate_expiration_date(new_category, item)
    return None


_UNIT_ALIASES = {
    "cup": "cup", "cups": "cup", "c": "cup",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "g": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml",
    "l": "l", "liter": "l", "liters": "l",
    # Size descriptors ("1 large" onion) aren't real units of measure — map
    # them to "" (normalized to None below) so "1 large" and "1/2" (no
    # unit) are recognized as the same kind of quantity and merge into a
    # single count instead of falling through to literal "1 large + 1/2"
    # concatenation. See _try_consolidate_quantity / the grocery-quantity
    # bug this fixes (onion showing as "1 large + 1/2" instead of "2").
    "large": "", "medium": "", "small": "", "whole": "", "jumbo": "", "xl": "",
}


# Package/container words that can appear as a unit on their own ("1 head")
# or as the second word of a compound unit ("1 lb bag"). Singular is the
# canonical parsed form; _format_quantity pluralizes only for display
# ("2 lb bags"), and _normalize_container_word below undoes that pluralization
# on the way back in — otherwise re-parsing an already-merged "2 lb bags" to
# add a third "1 lb bag" would see "bags" != "bag" and fail to match, falling
# back to concatenation again (the exact bug this whole fix is for).
_CONTAINER_UNIT_PLURALS = {
    "bag": "bags", "box": "boxes", "can": "cans", "jar": "jars",
    "bottle": "bottles", "block": "blocks", "bunch": "bunches",
    "head": "heads", "pint": "pints", "clove": "cloves",
    "tub": "tubs", "container": "containers", "pack": "packs",
    "loaf": "loaves", "stick": "sticks",
    # Filled in 2026-09-03 (previously a known, deliberate gap left alone
    # while the attendance-scaling work was under review — this table
    # drives the grocery list for every household and every week, so
    # changing it needed its own branch where it's the only thing being
    # touched; see tests/test_attendance.py's former "2 tin" assertion,
    # now "2 tins"). "tin" and "carton" are real container words already
    # in use throughout the app's recipes/tests; "punnet", "sachet",
    # "bar", "roll" and "sprig" were named alongside "tin" as the same
    # kind of gap even though nothing in this codebase uses them yet.
    "tin": "tins", "carton": "cartons", "punnet": "punnets",
    "sachet": "sachets", "bar": "bars", "roll": "rolls",
    "sprig": "sprigs", "slice": "slices",
}


_CONTAINER_UNIT_SINGULARS = {plural: singular for singular, plural in _CONTAINER_UNIT_PLURALS.items()}


def _normalize_container_word(unit_str: str) -> str:
    """Singularize a trailing container word ("lb bags" -> "lb bag") so a
    previously-pluralized display string parses back to the same canonical
    unit as a fresh singular one. Leaves everything else untouched."""
    if not unit_str:
        return unit_str
    words = unit_str.split(" ")
    words[-1] = _CONTAINER_UNIT_SINGULARS.get(words[-1], words[-1])
    return " ".join(words)


# Unit is normally one word ("lb", "cup"), but a store-purchase quantity
# sometimes carries a package word too ("1 lb bag", "12 oz can") — allow one
# optional second word so these parse instead of falling through to the
# "unparseable, concatenate raw strings" fallback in _try_consolidate_quantity.
_QTY_RE = re.compile(r"^(\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)\s*([a-zA-Z]+(?:\s+[a-zA-Z]+)?)?$")


def _strip_prep_descriptor(qty: str) -> str:
    """
    A recipe ingredient's quantity sometimes carries a prep instruction
    after a comma — "3, diced", "4.75 cups, sliced into planks" — useful
    in a recipe's own ingredient list, but not something that belongs on a
    grocery list (nobody buys "3, diced tomatoes"; they buy 3 tomatoes).
    Keep only the purchase amount before the first comma. This also fixes
    quantity *consolidation*: "3, diced" and "1, diced" used to each fail
    to parse (the comma broke the number/unit match below) and so
    "merged" by literally concatenating the raw strings instead of adding
    them — repeated across a few weeks of the same ingredient, that's how
    a line like "3, diced + 1, diced + 1, diced + ..." happens. Stripped
    first, both sides parse as plain numbers and add normally.
    """
    if not qty:
        return qty
    return qty.split(",", 1)[0].strip()


def _parse_quantity(qty: str) -> tuple[float, str | None] | None:
    """Parse a freeform quantity string into (amount, normalized_unit_or_None). Returns None if unparseable (e.g. blank, or freeform text like 'a bunch')."""
    if not qty or not qty.strip():
        return None
    match = _QTY_RE.match(_strip_prep_descriptor(qty.strip()).lower())
    if not match:
        return None
    amount_str, unit_str = match.group(1), (match.group(2) or "").strip()
    try:
        if "/" in amount_str:
            parts = amount_str.split(" ")
            if len(parts) == 2:
                whole, frac = parts
                num, den = frac.split("/")
                amount = float(whole) + float(num) / float(den)
            else:
                num, den = amount_str.split("/")
                amount = float(num) / float(den)
        else:
            amount = float(amount_str)
    except (ValueError, ZeroDivisionError):
        return None
    return amount, (_UNIT_ALIASES.get(unit_str, _normalize_container_word(unit_str)) or None)


_UNIT_PLURALS = {"cup": "cups", "lb": "lbs"}


def _format_quantity(amount: float, unit: str | None) -> str:
    amount_str = f"{amount:g}"
    if not unit:
        return amount_str
    if amount == 1:
        return f"{amount_str} {unit}"
    if unit in _UNIT_PLURALS:
        return f"{amount_str} {_UNIT_PLURALS[unit]}"
    prefix, _, last_word = unit.rpartition(" ")
    if last_word in _CONTAINER_UNIT_PLURALS:
        display_unit = f"{prefix} {_CONTAINER_UNIT_PLURALS[last_word]}" if prefix else _CONTAINER_UNIT_PLURALS[last_word]
        return f"{amount_str} {display_unit}"
    return f"{amount_str} {unit}"


# Unit groups for shopping-list "roll up to a bigger unit" conversion, each
# mapping unit -> how many of the group's smallest unit it equals. Used only
# for grocery-list display (see _humanize_grocery_quantity) — recipe
# scaling (scale_recipe) calls _format_quantity directly and is left in
# whatever unit the recipe was written in, since a cook following a recipe
# wants "12 tbsp", not a shopper's "3/4 cup".
_VOLUME_TO_TSP = {"tsp": 1.0, "tbsp": 3.0, "cup": 48.0}


_WEIGHT_TO_OZ = {"oz": 1.0, "lb": 16.0}


_MASS_TO_G = {"g": 1.0, "kg": 1000.0}


_METRIC_VOL_TO_ML = {"ml": 1.0, "l": 1000.0}


_UNIT_CONVERSION_GROUPS = [_VOLUME_TO_TSP, _WEIGHT_TO_OZ, _MASS_TO_G, _METRIC_VOL_TO_ML]


_NICE_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


def _roll_up_unit(amount: float, unit: str) -> tuple[float, str]:
    """
    Convert amount/unit up to the largest unit in its conversion group that
    it comfortably fits — e.g. 52 tbsp -> ~3.25 cups instead of staying in
    a unit nobody actually measures a shopping quantity in.
    """
    for group in _UNIT_CONVERSION_GROUPS:
        if unit not in group:
            continue
        base_amount = amount * group[unit]
        for candidate_unit, factor in sorted(group.items(), key=lambda kv: -kv[1]):
            if base_amount >= factor - 1e-9:
                return base_amount / factor, candidate_unit
        smallest_unit = min(group, key=group.get)
        return base_amount / group[smallest_unit], smallest_unit
    return amount, unit


def _round_to_nice_fraction(amount: float) -> float:
    """Round to the nearest quarter — friendlier for a shopping list than a repeating decimal."""
    whole = math.floor(amount + 1e-9)
    frac = amount - whole
    best = min(_NICE_FRACTIONS, key=lambda f: abs(f - frac))
    return whole + best if best < 1.0 else whole + 1.0


def _humanize_grocery_quantity(amount: float, unit: str | None) -> str:
    """
    Format a quantity for the grocery list the way a shopper actually buys
    it: unit=None or a discrete descriptor ("clove", "can", or a size
    adjective normalized away in _UNIT_ALIASES) rounds UP to a whole number
    — you can't buy 1.5 onions at the store — while a measurable unit
    (volume/weight) is rolled up to the largest sensible unit and rounded
    to the nearest quarter, so "52 tbsp" becomes "3.25 cups" instead of a
    number nobody would actually measure out.
    """
    if unit not in {u for group in _UNIT_CONVERSION_GROUPS for u in group}:
        whole = math.ceil(amount - 1e-9)
        return _format_quantity(max(whole, 1) if amount > 0 else whole, unit)
    rolled_amount, rolled_unit = _roll_up_unit(amount, unit)
    nice_amount = _round_to_nice_fraction(rolled_amount)
    if nice_amount <= 0 and amount > 0:
        nice_amount = 0.25
    return _format_quantity(nice_amount, rolled_unit)


def _normalize_grocery_quantity(qty: str) -> str:
    """
    Reformat a raw quantity string for shopper-friendly display (see
    _humanize_grocery_quantity). Freeform text that doesn't parse as a
    number+unit (e.g. "a bunch", "to taste") is left exactly as-is.
    """
    stripped = _strip_prep_descriptor((qty or "").strip())
    parsed = _parse_quantity(stripped)
    if not parsed:
        return stripped
    return _humanize_grocery_quantity(parsed[0], parsed[1])
