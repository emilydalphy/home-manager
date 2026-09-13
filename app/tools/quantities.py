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
    #
    # Kept as a backstop only: _split_quantity_note below now lifts these
    # words out of the string before it is ever matched, wherever in the
    # string they sit, and hands them back as a note. This table only
    # still fires for a unit string built by hand somewhere else.
    "large": "", "medium": "", "small": "", "whole": "", "jumbo": "", "xl": "",
    # Counted packs (see _PACK_CONVERSION_GROUPS): the recipe's own words
    # for the thing and for the pack it comes in read as those two units.
    "egg": "egg", "eggs": "egg", "dozen": "dozen", "dozens": "dozen",
    "dozen egg": "dozen", "dozen eggs": "dozen",
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


# The subset of container words that name a SEALED PACKAGE the household
# buys one of and draws on all week — a bottle of oil, a jar of spice, a
# bag of granola, a tub of cottage cheese. This is the line the grocery
# ingestion path draws (see recipes._add_recipe_ingredients_for_entries):
# a package is added once per week however many meals name it, while
# everything else adds up per meal.
#
# Deliberately a SHORT list, and much shorter than _CONTAINER_UNIT_PLURALS
# above. The question a word has to pass is not "is this a package?" but
# "does one of these last a household a whole week?", and the two answers
# come apart constantly:
#
#   - "bunch", "head", "clove", "pint", "punnet", "sprig", "slice",
#     "stick", "block", "bar", "loaf" and "roll" are per-meal amounts.
#     Five dinners each wanting a bunch of cilantro want five bunches.
#   - "can", "tin", "carton", "box", "pack", "packet" and "sachet" are
#     packages, but of things eaten a package at a time — a tin of beans,
#     a box of pasta, a carton of broth, a packet of yeast. These are the
#     ingredients the generation prompt itself calls out as needing "their
#     own real qty every time they're used, since each use is an actual
#     portion, not a pinch" (agent.py, the staples bullet). Collapsing
#     them to one would leave a cook short mid-week, which is a worse
#     failure than a list that asks for one line too many.
#
# What is left is the shape Emily's first approved week actually got wrong:
# the bag of spinach, the bag of granola, the bag of quinoa, the bottle of
# honey, the bottle of olive oil, the tub of hummus, the tub of cottage
# cheese. When in doubt a word stays OUT of this set, because that is the
# side that only ever costs an extra line rather than a missing dinner.
_PACKAGE_UNITS = {"bag", "bottle", "container", "jar", "tub"}


def _normalize_container_word(unit_str: str) -> str:
    """Singularize a trailing container word ("lb bags" -> "lb bag") so a
    previously-pluralized display string parses back to the same canonical
    unit as a fresh singular one. Leaves everything else untouched."""
    if not unit_str:
        return unit_str
    words = unit_str.split(" ")
    words[-1] = _CONTAINER_UNIT_SINGULARS.get(words[-1], words[-1])
    return " ".join(words)


# Words a recipe hangs on a quantity that describe the PRODUCT rather than
# the amount of it — "2 lb bag (frozen)", "3 bag frozen", "1 large", "2
# ripe". They are not units and they are not counts, and while they sat in
# the string they defeated the number/unit match below outright: Emily's
# first approved week showed "Mixed berries: 2 lb bag (frozen) + 2 lb bag
# (frozen) + 2 lb bag (frozen) + 2 lb bag (frozen)", four unparseable
# strings glued end to end, and "Pineapple chunks · 3 bag frozen", where
# "bag frozen" read as the unit so the package rule never saw a bag.
#
# So they come OUT of the string before parsing and are kept as a NOTE,
# which the grocery list appends back after the amount: "1 bag (2 lb),
# frozen". That is the one canonical shape — amount first, then a comma,
# then whatever describes the product — because the amount is what a
# shopper scans a list for and the note is what they read once they have
# found the line. Frozen berries stay visibly frozen; they just stop
# breaking the arithmetic.
#
# Deliberately a closed vocabulary of words that can only ever be
# descriptions. Anything unrecognized is left in the string, where it will
# either parse as a unit or fall through to the freeform path exactly as
# it does today — guessing that an unknown trailing word is decoration is
# how a real unit gets thrown away.
_DESCRIPTOR_WORDS = {
    # How the product is sold/preserved — genuinely useful to a shopper,
    # and the reason a note is retained rather than simply discarded.
    "frozen", "fresh", "canned", "jarred", "dried", "raw", "cooked",
    "organic", "ripe", "unripe", "seedless", "boneless", "skinless",
    "unsalted", "salted", "unsweetened", "sweetened",
    # Size words. These carry no amount at all (see _UNIT_ALIASES above,
    # which used to be the only thing that handled them) and are noted
    # rather than dropped so "3, large" still tells the shopper which
    # onions — but they never touch the number, so "1 large" and "1/2"
    # still add up to a single count the way that table intends.
    "large", "extra-large", "medium", "small", "whole", "jumbo", "xl",
}


def _looks_like_package_size(text: str) -> bool:
    """
    True for the parenthetical that states the SIZE of one package — "(2
    lb)", "(48 oz)" — and false for one that merely describes it,
    "(frozen)". The two are told apart by whether the contents are a
    number and a unit of MEASURE, which is the only shape the canonical
    sized-package form ever writes (see _sized_package_unit).
    """
    match = re.match(r"^(\d*\.?\d+)\s*([a-zA-Z]+)$", text.strip().lower())
    if not match:
        return False
    unit = _UNIT_ALIASES.get(match.group(2), match.group(2))
    return unit in _measure_units()


def _merge_notes(*notes) -> str:
    """Combine note strings/lists into one comma-separated note, keeping
    first-seen order and dropping duplicates — two dinners that both say
    "frozen" produce one "frozen", not two."""
    seen: list[str] = []
    for note in notes:
        parts = note if isinstance(note, (list, tuple, set)) else [note]
        for part in parts:
            for word in str(part or "").split(","):
                word = word.strip()
                if word and word not in seen:
                    seen.append(word)
    return ", ".join(seen)


def _with_note(text: str, note: str) -> str:
    """The canonical shape: the amount, then the note. "1 bag (2 lb)" +
    "frozen" -> "1 bag (2 lb), frozen"."""
    if not note:
        return text
    return f"{text}, {note}" if text else note


def _split_quantity_note(qty: str) -> tuple[str, str]:
    """
    Split a raw quantity into (the part that states an amount, the note
    describing the product). "2 lb bag (frozen)" -> ("2 lb bag",
    "frozen"); "3 bag frozen" -> ("3 bag", "frozen"); "1 bag (2 lb),
    frozen" -> ("1 bag (2 lb)", "frozen"), so a quantity this module has
    already formatted splits back into exactly what it was built from.
    A quantity with nothing to strip comes back unchanged with an empty
    note, which is the overwhelmingly common case.
    """
    if not qty:
        return "", ""
    notes: list[str] = []

    def _take_paren(match: re.Match) -> str:
        inner = match.group(1).strip()
        if _looks_like_package_size(inner):
            return match.group(0)
        if inner:
            notes.append(inner.lower())
        return " "

    core = re.sub(r"\(([^()]*)\)", _take_paren, qty)
    kept: list[str] = []
    # Split keeping the separators, so removing a word leaves the rest of
    # the string spaced and punctuated exactly as it was written.
    for token in re.split(r"(\s+|,)", core):
        if token.strip().lower() in _DESCRIPTOR_WORDS:
            notes.append(token.strip().lower())
            continue
        kept.append(token)
    core = re.sub(r"\s+", " ", "".join(kept)).strip().strip(" ,")
    return core, _merge_notes(notes)


def _quantity_note(qty: str) -> str:
    """Just the note half of _split_quantity_note, for callers that merge
    two already-normalized quantities and have to carry the note across."""
    return _split_quantity_note((qty or "").strip())[1]


# A quantity that genuinely cannot be parsed still must not be written
# twice on one line. "a handful" and "a handful" become "a handful ×2" —
# see grocery._repeat_or_concatenate, which is the only thing that writes
# this, and _subtract_quantity, which is the only thing that unwinds it.
_REPEAT_RE = re.compile(r"^(.+?)\s*×\s*(\d+)\s*$")


def _split_repeat_count(qty: str) -> tuple[str, int]:
    """"a handful ×3" -> ("a handful", 3); anything else -> (itself, 1)."""
    match = _REPEAT_RE.match((qty or "").strip())
    if match:
        return match.group(1).strip(), int(match.group(2))
    return (qty or "").strip(), 1


def _with_repeat_count(base: str, count: int) -> str:
    return base if count <= 1 else f"{base} ×{count}"


# Unit is normally one word ("lb", "cup"), but a store-purchase quantity
# sometimes carries a package word too ("1 lb bag", "12 oz can") — allow one
# optional second word so these parse instead of falling through to the
# "unparseable, concatenate raw strings" fallback in _try_consolidate_quantity.
# The optional trailing "(...)" is the canonical form this module writes a
# sized package back out in — "1 tub (48 oz)" — so a formatted quantity
# parses back to exactly the value it was formatted from.
_QTY_RE = re.compile(
    r"^(\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)\s*([a-zA-Z]+(?:\s+[a-zA-Z]+)?)?(?:\s*\(([^()]*)\))?$"
)


def _measure_units() -> set[str]:
    """Units that measure an amount (oz, lb, g, cup…) as opposed to counting
    things. Computed at call time because _UNIT_CONVERSION_GROUPS is defined
    further down the module."""
    return {u for group in _UNIT_CONVERSION_GROUPS for u in group}


def _sized_package_unit(container: str, amount: float, measure: str) -> str:
    """Canonical unit string for a package that carries its own size:
    ("tub", 48, "oz") -> "tub (48 oz)". The size never pluralizes — a
    48-ounce tub is "48 oz", not "48 ozs" — so it is written directly
    rather than through _format_quantity."""
    return f"{container} ({amount:g} {measure})"


def _split_package_size(unit: str | None) -> tuple[str | None, str]:
    """Split a canonical unit into (package word, " (size)" suffix).
    "tub (48 oz)" -> ("tub", " (48 oz)"); "cup" -> ("cup", "")."""
    if not unit:
        return unit, ""
    head, sep, rest = unit.partition(" (")
    return head, (f" ({rest}" if sep else "")


def package_unit(qty: str) -> str | None:
    """
    The sealed-package word a bought-unit quantity names ("1 bottle",
    "2 bags", "48 oz tub" -> "bottle", "bag", "tub"), or None for anything
    that isn't one — a plain count, a measured amount, a per-meal produce
    unit like a bunch, or freeform text.

    This is the single place the "buy one per week, not one per meal" rule
    decides what counts as a package; grocery ingestion and the reversal
    that undoes it both ask this so they can never disagree about which
    lines follow that rule.
    """
    parsed = _parse_quantity(qty)
    if not parsed or not parsed[1]:
        return None
    head, _ = _split_package_size(parsed[1])
    # Last word, so a size descriptor in front of the package word ("2
    # large bags") still reads as a bag rather than falling through.
    word = head.rpartition(" ")[2]
    return word if word in _PACKAGE_UNITS else None


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
    """
    Parse a freeform quantity string into (amount, normalized_unit_or_None).
    Returns None if unparseable (e.g. blank, or freeform text like 'a bunch').

    One shape needs explaining. In "48 oz tub", "1 lb bag", "12 oz can" the
    number is the SIZE of one package, not a count of packages — nobody
    means forty-eight tubs of cottage cheese. Those parse as ONE package
    whose size travels with the unit: (1.0, "tub (48 oz)"). Read the other
    way (the way this used to), the size became the count and the grocery
    list asked Emily to buy "48 oz tubs" of cottage cheese, then "192 oz
    tubs" once four lunches had each added one.

    A count of same-sized packages is written the way this module formats
    it back out — "3 tubs (48 oz)" — and parses to (3.0, "tub (48 oz)"),
    so formatting and parsing round-trip exactly.
    """
    if not qty or not qty.strip():
        return None
    # Descriptors ("frozen", "large", "(frozen)") come out first — they
    # describe the product, not the amount, and left in place they defeat
    # the match below entirely. The note itself is not this function's to
    # return; _normalize_grocery_quantity re-attaches it for display.
    core, _note = _split_quantity_note(qty.strip())
    match = _QTY_RE.match(_strip_prep_descriptor(core).lower())
    if not match:
        return None
    amount_str, unit_str = match.group(1), (match.group(2) or "").strip()
    size_str = (match.group(3) or "").strip()
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
    unit = _UNIT_ALIASES.get(unit_str, _normalize_container_word(unit_str)) or None
    if size_str:
        # "3 tubs (48 oz)" — an explicit count of packages of a stated size.
        size = _parse_quantity(size_str)
        if unit and size and size[1]:
            return amount, _sized_package_unit(unit, size[0], size[1])
        return amount, unit
    if unit and " " in unit:
        # "48 oz tub" — a measure word in front of a container word means
        # the number sizes the package rather than counting packages.
        measure, _, container = unit.partition(" ")
        measure = _UNIT_ALIASES.get(measure, measure)
        if container in _CONTAINER_UNIT_PLURALS and measure in _measure_units():
            return 1.0, _sized_package_unit(container, amount, measure)
    return amount, unit


_UNIT_PLURALS = {"cup": "cups", "lb": "lbs", "egg": "eggs", "dozen": "dozen"}


def _plain_number(amount: float, sig: int = 6) -> str:
    """
    A number written out, never in scientific notation.

    "%g" switches to an exponent below 1e-4 and at 1e6 and above, and
    "1e-05 cups" parses as nothing at all (_QTY_RE reads no exponent). One
    such row makes _sum_ledger_quantities give up on the WHOLE line, which
    drops grocery._reverse_meal_grocery_contributions into
    _subtract_quantity — where an identical current-and-remove string means
    "this contribution is the whole line" and deletes a row other meals are
    still linked to.

    Six significant figures by default, which is what a person reads; the
    per-meal ledger asks for more (see recipes._ledger_share), because
    three sixths of something has to add back up to a whole one and
    "0.666667 x 3" is 2.000001, which rounds UP to three of them. A crumb
    below what the written form can show becomes a plain "0", which the
    ledger's own floor then handles.
    """
    text = f"{amount:.{sig}g}"
    if "e" not in text and "E" not in text:
        return text
    return f"{amount:.{max(sig, 10)}f}".rstrip("0").rstrip(".") or "0"


def _format_quantity(amount: float, unit: str | None, sig: int = 6) -> str:
    amount_str = _plain_number(amount, sig)
    if not unit:
        return amount_str
    # A sized package ("tub (48 oz)") pluralizes the package word and
    # leaves the size alone: "2 tubs (48 oz)", never "2 tub (48 ozs)".
    head, size_suffix = _split_package_size(unit)
    # One or less is singular: "½ cup", "¾ lb", "1 head" — the way a person
    # writes it, and what the Cook screen renders once shell.js's
    # humanQtyText has turned the leading 0.5 into "½". This used to
    # pluralize everything but exactly 1, which is how Emily's two-serving
    # soup read "½ cups Red lentils" (2026-09-13). Parsing is unaffected:
    # _UNIT_ALIASES and _normalize_container_word read either form.
    if amount <= 1:
        return f"{amount_str} {head}{size_suffix}"
    if head in _UNIT_PLURALS:
        return f"{amount_str} {_UNIT_PLURALS[head]}{size_suffix}"
    prefix, _, last_word = head.rpartition(" ")
    if last_word in _CONTAINER_UNIT_PLURALS:
        display_unit = f"{prefix} {_CONTAINER_UNIT_PLURALS[last_word]}" if prefix else _CONTAINER_UNIT_PLURALS[last_word]
        return f"{amount_str} {display_unit}{size_suffix}"
    return f"{amount_str} {head}{size_suffix}"


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


# COUNTED PACKS (Loop Board, 2026-09-13 — Emily's list said "Eggs · 4
# dozen"). A store sells a pack of N countable things and a recipe uses a
# few of them: eggs by the dozen, garlic by the head. Every recipe the app
# had written said "1 dozen" — the prompt asked for the store's unit — and
# four meals with eggs then added up to four cartons. Emily: "it should add
# up how many eggs, not the whole dozen for each recipe."
#
# So a pack is a unit FAMILY, like tbsp/cup: the ledger keeps each meal's
# share in the small unit (eggs, cloves), the shares add up across the
# week, and the line is written in the pack — rounded UP to a whole one,
# because that is what the shelf sells. The two families here are the two
# whose words are unambiguous: nothing but eggs comes by the dozen, and
# nothing but garlic comes in cloves. ("Head" alone is also a lettuce or a
# cauliflower; it is only read as ten cloves because it sits in this group
# beside "clove", and a head of lettuce still rounds up to whole heads
# exactly as it did before — _shopping_round ceils in the pack unit.)
#
# These stay OUT of _UNIT_CONVERSION_GROUPS on purpose: a measurable unit
# rounds to the nearest quarter and rolls up only once it reaches the
# bigger unit; a pack always rounds up to whole packs.
_EGGS_TO_EACH = {"egg": 1.0, "dozen": 12.0}
_GARLIC_TO_CLOVE = {"clove": 1.0, "head": 10.0}
_PACK_CONVERSION_GROUPS = [_EGGS_TO_EACH, _GARLIC_TO_CLOVE]


def _pack_group(unit: str | None) -> dict | None:
    """The counted-pack family `unit` belongs to, or None."""
    return next((g for g in _PACK_CONVERSION_GROUPS if unit in g), None)


def _pack_units(group: dict) -> tuple[str, str]:
    """(the small unit, the pack unit) of a counted-pack family."""
    return min(group, key=group.get), max(group, key=group.get)


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


def _convert_to_unit(amount: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """
    Re-express `amount` in `to_unit`, when the two units are the same word
    or both belong to one of the measurable-unit families above (lb<->oz,
    cup<->tbsp<->tsp, g<->kg, ml<->l). Returns None when they can't be
    reconciled this way — different families, a discrete count, freeform
    text — so a caller can tell "genuinely nothing to convert" apart from
    a real zero.

    Exists because the grocery line and the per-meal ledger row that
    reverses it don't always agree on which unit a measurable amount is
    written in: _humanize_grocery_quantity rolls a quantity to whichever
    unit in its family displays best AT THAT AMOUNT, so a line can read
    "10 oz" one day and the ledger row still reversing into it reads
    "0.75 lb" from the week it was bought. Same amount, different label —
    this is what lets _subtract_quantity tell that apart from an actual
    disagreement.
    """
    if from_unit == to_unit:
        return amount
    if not from_unit or not to_unit:
        return None
    for group in _UNIT_CONVERSION_GROUPS + _PACK_CONVERSION_GROUPS:
        if from_unit in group and to_unit in group:
            return amount * group[from_unit] / group[to_unit]
    return None


def _round_to_nice_fraction(amount: float) -> float:
    """Round to the nearest quarter — friendlier for a shopping list than a repeating decimal."""
    whole = math.floor(amount + 1e-9)
    frac = amount - whole
    best = min(_NICE_FRACTIONS, key=lambda f: abs(f - frac))
    return whole + best if best < 1.0 else whole + 1.0


def _measurable_unit(unit: str | None) -> bool:
    """A volume/weight unit that converts, as opposed to a countable thing
    (a pepper, a clove, a tin) or no unit at all."""
    return unit in {u for group in _UNIT_CONVERSION_GROUPS for u in group}


def _round_in_unit(amount: float, unit: str | None) -> float:
    """
    The shopping rounding held to ONE unit — no rolling up to a bigger one.

    _shopping_round is this plus the roll-up, and is what a line is written
    with. This bare form is for arithmetic that has to stay on one
    quantum: grocery._restate_standing_want rounds the plan's before and
    after in the LINE's own unit, so their difference is a whole quantum of
    that unit and subtracting it cannot leave a sub-quantum residue for the
    next removal to round away. Rounding each side in whatever unit it
    happened to roll to is how a 2-cup standing want crept up a quarter of
    a cup a week.
    """
    if not _measurable_unit(unit):
        return float(math.ceil(amount - 1e-9))
    nice = _round_to_nice_fraction(amount)
    if nice <= 0 and amount > 0:
        nice = 0.25  # never round a real quantity away to nothing
    return nice


def _snap_to_unit_quantum(amount: float, unit: str | None) -> float:
    """
    The NEAREST quantum of `unit` — a quarter of a measurable one, a whole
    countable thing.

    Deliberately not _round_in_unit, whose ceil is the shopping decision
    ("an extra pepper costs a pepper"). This is for recovering a number
    that was already written on a quantum and has picked up a little
    display error since: grocery._restate_standing_want re-derives the
    household's own amount out of the line at every removal, and the line
    is re-rounded for display in between, so the derived value drifts by a
    fraction of a quantum each time and would otherwise compound. Nearest
    puts it back where it started; rounding it UP every time would be the
    ratchet again in the other direction.

    HALF ROUNDS UP, which is the one place this differs from
    _round_to_nice_fraction and is not a detail. The display rounding that
    put the error there rounds half DOWN, so exactly-half is the case that
    keeps happening — a household's 8 oz sitting on a line displayed in
    pounds derives as 0.375 lb, dead between two quarters, and rounding
    that down quietly hands them 4 oz instead. Half up costs at most a
    quarter of a unit and costs it in the direction this module always
    errs.
    """
    quantum = 0.25 if _measurable_unit(unit) else 1.0
    return math.floor(amount / quantum + 0.5 + 1e-9) * quantum


def _shopping_round(amount: float, unit: str | None) -> tuple[float, str | None]:
    """
    THE rounding a grocery line is written in, and the one place it lives.

    A countable thing rounds UP to a whole one — you cannot buy 1.5 onions,
    an extra onion costs an onion and a missing one costs the dinner
    (Emily, 2026-09-05). A measurable unit is rolled up to the largest
    sensible unit in its family and rounded to the nearest quarter, so "52
    tbsp" becomes "3.25 cups" rather than a number nobody measures out.

    recipes._week_bought_amount is a thin wrapper on this and
    _humanize_grocery_quantity is the display form of it. That is
    load-bearing rather than tidiness: a grocery line is rounded ONCE, on
    the whole week's total, and reversal re-rounds the surviving ledger
    shares through _sum_ledger_quantities. If the two roundings could
    disagree, a line that lost no meal at all would come back different
    from what was put on it. They cannot disagree, because there is one of
    them.
    """
    pack = _pack_group(unit)
    if pack:
        # A counted pack: however the shares were written (eggs or dozens,
        # cloves or heads), the line is whole packs, rounded up — 14 eggs
        # is 2 dozen, 8 cloves is 1 head.
        _small_unit, pack_unit = _pack_units(pack)
        in_packs = amount * pack[unit] / pack[pack_unit]
        return float(math.ceil(in_packs - 1e-9)), pack_unit
    if not _measurable_unit(unit):
        return _round_in_unit(amount, unit), unit
    rolled_amount, rolled_unit = _roll_up_unit(amount, unit)
    return _round_in_unit(rolled_amount, rolled_unit), rolled_unit


def _humanize_grocery_quantity(amount: float, unit: str | None) -> str:
    """
    Format a quantity for the grocery list the way a shopper actually buys
    it — see _shopping_round, which is the arithmetic. A real amount of a
    countable thing never displays as none of it.
    """
    rounded, rounded_unit = _shopping_round(amount, unit)
    if amount > 0 and rounded < 1 and not _measurable_unit(unit):
        rounded = 1.0
    return _format_quantity(rounded, rounded_unit)


def _ledger_buckets(qty_strings: list[str]) -> dict | None:
    """
    Add a grocery line's per-meal ledger rows up, one bucket per unit
    family — the shared reading behind _sum_ledger_quantities and
    _ledger_totals.

    Rows in the same measurable family (lb/oz, cup/tbsp/tsp, g/kg, ml/l)
    are converted to that family's smallest unit and summed together even
    when they were written in different units of it. Rows that are the same
    bare count sum directly. Rows in genuinely different families (a count
    next to a measured amount, or two container words) stay in separate
    buckets — guessing a conversion that does not exist is worse than
    reporting both.

    None means a row does not parse as a number at all (a freeform "a
    bunch"), so the whole line is unaccountable and the caller must not
    pretend otherwise.
    """
    totals: dict[tuple, list] = {}
    for raw in qty_strings:
        text = (raw or "").strip()
        if not text:
            continue
        parsed = _parse_quantity(text)
        if not parsed:
            return None
        amount, unit = parsed
        group = next((g for g in _UNIT_CONVERSION_GROUPS + _PACK_CONVERSION_GROUPS if unit in g), None)
        if group:
            # A counted pack's rows sum in the small unit too ("2 eggs" +
            # "0.5 dozen" = 8 eggs) and are written back as whole packs
            # by _shopping_round.
            base_unit = min(group, key=group.get)
            key = ("measure", id(group))
            bucket = totals.setdefault(key, [0.0, base_unit])
            bucket[0] += amount * group[unit]
        else:
            key = ("discrete", unit)
            bucket = totals.setdefault(key, [0.0, unit])
            bucket[0] += amount
    return totals


def _sum_ledger_quantities(qty_strings: list[str]) -> str | None:
    """
    Recombine a grocery line's remaining per-meal ledger contributions into
    one quantity, in the same units-and-rounding a grocery line is always
    written in (see _shopping_round) — the recompute half of
    grocery._reverse_meal_grocery_contributions, its only caller.

    Each row is a bare "<amount> <unit>" string with no note or repeat
    marker (see recipes._record_grocery_link / WeekGroceryBuffer.flush, the
    only things that write one), so there is only ever a unit to
    reconcile, never a note. How the rows are added up is _ledger_buckets;
    a family that cannot be reconciled with another is kept apart and the
    two humanized amounts are concatenated with " + ", the same honest
    disagreement _repeat_or_concatenate reports elsewhere in this module.

    Returns None when a row doesn't parse as a number at all (a freeform
    contribution like "a bunch" that reached this grocery line before a
    later contribution turned it into something summable) — the caller
    falls back to subtracting this one contribution out of the current
    display instead of guessing what the word means.

    A GROCERY LINE WITH MEALS STILL BEHIND IT NEVER READS "0". The rows
    written since 2026-09-13 are each meal's unrounded share (see
    recipes._ledger_share), so a surviving meal's share is only ever zero
    if nobody is eating it — but rows written BEFORE that carry an
    apportioned whole share of the rounded line, and three nights of a
    recipe serving twelve apportion one lemon as 1 / 0 / 0. Summing the
    survivors of that is genuinely zero, and "Lemon · 0" on the list with
    two dinners still planned is a line a shopper would walk past. So a
    bucket contributing nothing is simply not printed, and a line where
    NOTHING is left to print is floored at the smallest amount you can buy
    rather than shown as none of it. (Printing a floored bucket beside a
    real one would invent a whole pepper next to "2 cups".)
    """
    totals = _ledger_buckets(qty_strings)
    if totals is None:
        return None
    if not totals:
        return ""
    parts = [
        _humanize_grocery_quantity(amount, unit)
        for amount, unit in totals.values() if amount > 0
    ]
    if parts:
        return " + ".join(parts)
    (kind, _), (_, unit) = next(iter(totals.items()))
    return _humanize_grocery_quantity(0.25 if kind == "measure" else 1.0, unit)


def _ledger_totals(
    all_qtys: list[str], surviving_qtys: list[str]
) -> tuple[float, float, str | None] | None:
    """
    What a grocery line's meals wanted in total, before and after some of
    them leave — (before, after, unit), unrounded, in one unit.

    It exists for the household's own standing want ("Onions · 3", then a
    week of dinners adds to it), the one line
    grocery._reverse_meal_grocery_contributions may never recompute: the
    person's own amount is in there and no ledger row describes it. The
    plan's part has to be taken back instead, and getting THAT wrong
    compounds — the ingest adds ONE rounded week total, so subtracting each
    meal's own share leaves a remainder that the ceil pushes straight back
    up, every week, for ever. (Measured before this existed: a hand-added
    "3 Onions" under three dinners wanting 1.5 apiece went 3 → 5 → 7 → 9
    over four approve-and-clear cycles.)

    The numbers come back UNROUNDED on purpose: grocery._restate_standing_want
    rounds both of them in the LINE's own unit, so the difference is a
    whole quantum of the unit the line is written in. Rounding them here,
    each in whatever unit it rolled to, is a quarter of a cup a week of
    residue.

    None when the rows cannot be read this way — a freeform "a bunch", or a
    line whose rows span two unit families (a package word beside a
    measured amount) — and the caller falls back to subtracting the one
    contribution in front of it, which is what it always did.

    Correct for ledger rows written before 2026-09-13 too, which hold
    apportioned whole shares: those already sum to the rounded total and
    rounding leaves such a value alone, so before-minus-after is that
    meal's own share, exactly what used to be subtracted.
    """
    before = _ledger_buckets(all_qtys)
    after = _ledger_buckets(surviving_qtys)
    if before is None or after is None or len(before) != 1:
        return None
    key, (total_before, unit) = next(iter(before.items()))
    if set(after) - {key}:
        return None
    return total_before, (after[key][0] if key in after else 0.0), unit


def _normalize_grocery_quantity(qty: str) -> str:
    """
    Reformat a raw quantity string for shopper-friendly display (see
    _humanize_grocery_quantity). Freeform text that doesn't parse as a
    number+unit (e.g. "a bunch", "to taste") is left exactly as-is —
    including any descriptor inside it, since re-attaching a note to a
    string that still contains the word would say it twice.

    A quantity that DOES parse comes back in the canonical shape amount
    first, note last: "2 lb bag (frozen)" -> "1 bag (2 lb), frozen".
    """
    raw = (qty or "").strip()
    core, note = _split_quantity_note(raw)
    parsed = _parse_quantity(core)
    if not parsed:
        return _strip_prep_descriptor(raw)
    return _with_note(_humanize_grocery_quantity(parsed[0], parsed[1]), note)
