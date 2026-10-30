"""
The grocery list: adding, merging, marking, clearing and repairing items.
"""
from __future__ import annotations

import re
import json
import unicodedata
import math
from datetime import date
from ..db import get_conn, write
from ._shared import acting_name, household_id, require_household_row
from . import inventory as _inventory
from . import quantities as _quantities
from . import weekly_plan as _weekly_plan


# Sane upper bounds, not measured limits -- nothing in the schema or the
# shell enforces a length today, so a stray tap (holding a key down) or a
# mis-parsed scanned receipt line can otherwise leave an unbounded row on
# the list. Chosen as assumptions (2026-09-15): 120 covers any real grocery
# item name with room to spare; 40 covers any real quantity phrase
# ("3 cans (14 oz each)") with room to spare. Emily's call to move either
# number.
MAX_ITEM_NAME_LENGTH = 120
MAX_QUANTITY_LENGTH = 40


def _household_today(conn=None) -> date:
    """
    Today where the household lives, not where the container runs.

    Lazily imported, and that is forced rather than stylistic: cooker
    imports this module at module scope, so `from . import cooker` up
    there would be a cycle. Same shape inventory._today and held._today
    use, for the same reason.

    Both readers below compare the household's own day against a date
    that came out of plan_period — i.e. against the days a planning
    period was written in, which are household-relative. The container
    runs UTC and households default to America/Toronto, so reading the
    server's date here put one half of that comparison in a zone nobody
    lives in. `conn` rides through to household_today for the caller
    that is already inside a write transaction; see
    set_aside_carried_over_items.
    """
    from .cooker import household_today

    return household_today(conn=conn)


# Single-word names where the plural is a DIFFERENT product, not more of
# the same one. "Pepper" is the black pepper in the cupboard; "peppers"
# are the bell peppers in the fridge. Merging those puts a pantry staple
# under Produce and it never gets bought -- silently, which is the whole
# failure this normalisation is supposed to avoid.
#
# Only applies to a bare one-word name, because that is where the
# ambiguity lives. "Bell peppers" and "chocolate chips" say which thing
# they are, so they still merge with their singulars normally.
_NUMBER_CHANGES_MEANING = {
    "pepper", "peppers",
    "chili", "chilis", "chilli", "chillies", "chile", "chiles",
    "green", "greens",
    "chip", "chips", "crisp", "crisps",
    "ground", "grounds",
    "sprout", "sprouts",
    "grit", "grits",
    "bitter", "bitters",
    # Uncountables people only ever write one way; the "singular" is not a
    # word anyone would type on a list.
    "oat", "oats", "pea", "peas", "grape", "grapes",
}

# Plurals that are just the word plus an "s", where the general rules
# would guess wrong: "cookies" is cookie+s, not cooky, and "quiches" is
# quiche+s, not quich. English has no reliable way to tell these from
# "berries" and "peaches" by spelling alone, so the honest fix is to name
# the ones that actually turn up on a shopping list.
_JUST_ADD_S = {
    "cookies", "brownies", "smoothies", "veggies", "pies", "quiches",
    "brioches", "cupcakes", "pastries",
}


# A recipe's plain name and the specific variety a shopper buys when the
# plain name is all they're given -- the same purchase under two names
# (Loop Board bug, QA walk 2026-10-02: "Onions 2" beside "Yellow onion 3",
# "Rice 2 cups" beside "Long-grain white rice 1 cup"). Keyed on the
# variety's own key (lowercased, last word singularised -- see
# _plain_name_key) and pointing at the plain name's key, so both land on
# one line.
#
# An ALLOW-LIST, deliberately, never a "the shorter name is inside the
# longer one" rule. That rule is how "olive oil" once read as "olives"
# (2026-09-14) and it would merge red onion into onions, brown rice into
# rice, chicken thighs into chicken -- things a shopper buys separately.
# Only the variety a plain-named recipe actually means goes here: red,
# white and green onions, brown/basmati/jasmine rice, whole-wheat and
# bread flour, brown and icing sugar all stay their own lines. Adding a
# pair is a product call (one line instead of two) and wants its test in
# tests/test_grocery_same_purchase.py.
# Words in an item NAME that never change what goes in the trolley, so two
# lines differing only by one of them are the same purchase ("Store-bought
# hummus" / "Hummus").
#
# Kept SHORT and deliberately NOT a general "drop the adjectives" rule.
# This app reads fresh-vs-dried herbs as genuinely different purchases
# (spices.py counts basil as a rack item only when written "dried"), and
# low-fat-vs-full-fat milk is two products. So "fresh" and "low-fat" are
# NOT here, even though the card that asked for this named "fresh": only a
# word that cannot name a different product belongs. "full-fat" is safe
# because stripping it leaves the plain name, and "low-fat milk" keeps its
# own key either way.
#
# NOTE ON THE CARD'S "when the other line doesn't specify": a merge key is
# a pure function of ONE name, so it cannot be conditional on what some
# other line happens to say — two keys are either equal or they are not.
# Stripping unconditionally is the only form that fits, and it is the safe
# one here for the reason above.
_NAME_FILLER = (
    "store-bought", "store bought", "shop-bought", "shop bought",
    "full-fat", "full fat",
)

# Preparation a recipe asks for AFTER a comma ("Baby spinach, chopped").
# Dropped ONLY post-comma, which is the whole care in it: a LEADING
# adjective can name the product -- "Diced tomatoes" is a tin and not a
# fresh tomato -- so a general strip of these words would merge two things
# a shopper buys separately.
_NAME_PREP_AFTER_COMMA = {
    "chopped", "diced", "minced", "sliced", "shredded", "grated", "crushed",
    "cubed", "julienned", "halved", "quartered", "peeled", "trimmed",
    "rinsed", "drained", "softened", "melted", "divided", "thawed",
    "packed", "lightly packed", "well shaken", "plus more", "to taste",
    "for serving", "for garnish", "optional", "room temperature",
    "at room temperature",
}


_SAME_PURCHASE = {
    "yellow onion": "onion",
    "cooking onion": "onion",     # the Canadian shelf name for a yellow onion
    "brown onion": "onion",       # the UK/Australian one
    "white rice": "rice",
    "long-grain white rice": "rice",
    "long grain white rice": "rice",
    "long-grain rice": "rice",
    "long grain rice": "rice",
    "all-purpose flour": "flour",
    "all purpose flour": "flour",
    "plain flour": "flour",
    "granulated sugar": "sugar",
    "white sugar": "sugar",
    "granulated white sugar": "sugar",
    # Yogurt (Loop Board bug, Gowthami's household 2026-10-04: three
    # variations of plain yogurt on one list). Greek yogurt IS a plain
    # yogurt -- strained -- so it is the same purchase, and because it is
    # the VARIETY the merged line keeps its name (see _more_specific_name),
    # which is the safe direction: buying Greek satisfies a recipe that
    # asked for plain, where buying plain fails one that asked for Greek.
    # FLAVOURED yogurt is a different purchase and stays its own line --
    # "strawberry yogurt" and "vanilla greek yogurt" are not here, and the
    # allow-list design is what keeps them out rather than a rule.
    # Bare "yogurt" is deliberately NOT mapped: it could be flavoured, and
    # this module's stated bias is to fail toward two lines.
    "plain greek yogurt": "plain yogurt",
    "greek yogurt": "plain yogurt",
    "plain greek yoghurt": "plain yogurt",
    "greek yoghurt": "plain yogurt",
    "plain yoghurt": "plain yogurt",
    # Seasonings that do one job under several names (Loop Board,
    # realistic week amounts, 2026-10-19: Salt / Sea salt / Flaky salt and
    # red chili flakes / red pepper flakes as separate lines). Cooking salt
    # is one purchase whatever the grind; FLAKY salt is not here — it is a
    # finishing salt, bought for the crunch, and keeps its own line.
    "sea salt": "salt",
    "fine sea salt": "salt",
    "coarse sea salt": "salt",
    "kosher salt": "salt",
    "table salt": "salt",
    "fine salt": "salt",
    "iodized salt": "salt",
    "flaky sea salt": "flaky salt",
    "sea salt flake": "flaky salt",
    "flake salt": "flaky salt",
    "maldon salt": "flaky salt",
    "maldon sea salt": "flaky salt",
    "red chili flake": "red pepper flake",
    "red chilli flake": "red pepper flake",
    "red chile flake": "red pepper flake",
    "chili flake": "red pepper flake",
    "chilli flake": "red pepper flake",
    "crushed red pepper": "red pepper flake",
    "crushed red pepper flake": "red pepper flake",
    # Sandwich bread for the same toast (the card: "Multigrain bread 8
    # slices" beside "Whole grain bread 2 slices"). White and sourdough
    # are a different loaf and stay their own.
    "multigrain bread": "whole grain bread",
    "multi-grain bread": "whole grain bread",
    "whole-grain bread": "whole grain bread",
    "wholegrain bread": "whole grain bread",
    "whole wheat bread": "whole grain bread",
    "whole-wheat bread": "whole grain bread",
    "ground black pepper": "black pepper",
    "freshly ground black pepper": "black pepper",
    "fresh ground black pepper": "black pepper",
    "cracked black pepper": "black pepper",
    # Fresh herbs under the names recipes give them (Loop Board, "Flat-leaf
    # parsley and Fresh parsley are one line"). A bare herb name already
    # means the fresh bunch in this app -- spices.py files bare basil,
    # parsley, thyme under produce and only "dried ..." on the rack -- so
    # "fresh X" joining "X" is the same purchase, and "dried X" is NOT
    # here: it keys to itself and stays its own line. "fresh" is still not
    # stripped as filler (see _NAME_FILLER); these are explicit pairs.
    #
    # Two spellings deliberately left out. Bare "coriander" is the ground
    # seed in a Canadian/US recipe (spices.py's rack list), so only
    # "fresh coriander" / "coriander leaves" join cilantro. And "ground
    # ginger" is a jar, not a root, so it is not here either.
    "flat-leaf parsley": "parsley",
    "flat leaf parsley": "parsley",
    "italian parsley": "parsley",
    "fresh flat-leaf parsley": "parsley",
    "fresh flat leaf parsley": "parsley",
    "fresh italian parsley": "parsley",
    "fresh parsley": "parsley",
    "fresh parsley leave": "parsley",   # keys singularise the last word
    "parsley leave": "parsley",
    "fresh cilantro": "cilantro",
    "fresh cilantro leave": "cilantro",
    "cilantro leave": "cilantro",
    "fresh coriander": "cilantro",
    "fresh coriander leave": "cilantro",
    "coriander leave": "cilantro",
    "fresh basil": "basil",
    "fresh basil leave": "basil",
    "basil leave": "basil",
    "fresh mint": "mint",
    "fresh mint leave": "mint",
    "mint leave": "mint",
    "fresh dill": "dill",
    "fresh thyme": "thyme",
    "fresh ginger": "ginger",
    "ginger root": "ginger",
    "fresh ginger root": "ginger",
    "gingerroot": "ginger",
    "fresh gingerroot": "ginger",
}

# Names in _SAME_PURCHASE that add only "fresh" (or "leaves") to the plain
# name, so they tell the shopper less than a real variety does: the merged
# line reads "Flat-leaf parsley" whichever of "Fresh parsley" / "Flat-leaf
# parsley" landed first. They still beat the bare name ("Fresh basil" over
# "Basil"), the same direction every other pair goes.
_ONLY_SAYS_FRESH = {
    key for key, plain in _SAME_PURCHASE.items()
    if plain in {"parsley", "cilantro", "basil", "mint", "dill", "thyme", "ginger"}
    and not any(word in key for word in ("flat", "italian"))
}


def _is_prep_segment(segment: str) -> bool:
    """
    Is this post-comma segment only PREPARATION?

    Matched on the whole segment and, failing that, on its LAST word, so
    the adverb family comes for free: "finely chopped", "roughly diced" and
    "thinly sliced" are all the prep word they end in, and the list does
    not have to enumerate every adverb anyone might write. Hard to
    construct a counter-example -- a segment ending in "chopped" is not
    something else -- and an unrecognised segment is still kept rather than
    dropped, so the failure direction is a name that keeps a word it did
    not need.
    """
    if segment in _NAME_PREP_AFTER_COMMA:
        return True
    words = segment.split(" ")
    return len(words) > 1 and words[-1] in _NAME_PREP_AFTER_COMMA


def _uninvert_name(cleaned: str) -> str:
    """
    "yogurt, plain" is the same thing as "plain yogurt", so a comma-inverted
    name is written back out the way a shopper says it: the first segment is
    the thing, every later one describes it and moves in front.

    A later segment that is only PREPARATION is dropped rather than moved
    ("Baby spinach, chopped" is baby spinach), which is the historical case
    this repo has been bitten by -- see the 2026-08-30 Decision log entry on
    prep descriptors blocking a merge. Anything the list does not recognise
    is KEPT as a modifier, so an unknown word can never silently vanish
    from a name.
    """
    if "," not in cleaned:
        return cleaned
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if not parts:
        return ""
    head, rest = parts[0], parts[1:]
    mods = [p for p in rest if not _is_prep_segment(p)]
    return " ".join(mods + [head])


def _drop_name_filler(cleaned: str) -> str:
    """Take the words that cannot change the purchase out of the key."""
    out = cleaned
    for filler in _NAME_FILLER:
        out = re.sub(r"\b%s\b" % re.escape(filler), " ", out)
    out = " ".join(out.split())
    # A name that is NOTHING but filler keeps itself: "Store-bought" alone
    # is a bad line, and keying it to "" would merge it with every other
    # bad line on the list.
    return out or cleaned


def _fold_accents(text: str) -> str:
    # Only a mark on a Latin letter is dropped: "ñ" -> "n" is a spelling
    # variant, but a Japanese voicing mark makes a different word (ごま,
    # sesame, is not こま) and must not merge two lines.
    if text.isascii():
        return text
    out: list[str] = []
    for ch in unicodedata.normalize("NFD", text):
        if unicodedata.combining(ch) and out and out[-1].isascii() and out[-1].isalpha():
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _plain_name_key(name: str) -> str:
    """_merge_key without the same-purchase step: the name's own key.

    Accents are folded (defect hunt, 2026-10-22): "Jalapeño" and
    "jalapeno", "Crème fraîche" and "creme fraiche" are one thing to buy,
    and recipes spell them both ways — the list carried two lines for one
    pepper. Only the key folds; the line keeps the name as written."""
    cleaned = " ".join(_fold_accents(name or "").strip().lower().split())
    if not cleaned:
        return ""
    cleaned = _drop_name_filler(_uninvert_name(cleaned))
    if not cleaned:
        return ""
    words = cleaned.split(" ")
    # A bare ambiguous noun is left exactly as written, so "Pepper" and
    # "Peppers" stay the two different things they are.
    if len(words) == 1 and words[0] in _NUMBER_CHANGES_MEANING:
        return cleaned
    words[-1] = _singular_word(words[-1])
    return " ".join(words)


def _specificity(name: str) -> int:
    """
    0 for a plain name, 1 for one that only says fresh, 2 for a real
    variety, 3 for flat-leaf parsley.

    The 3 is a tie-break the card asks for: "Italian parsley" and
    "Flat-leaf parsley" are the same bunch under two names, and the list
    should read "Flat-leaf parsley" whichever recipe landed first --
    it is the name on the shelf tag here.
    """
    key = _plain_name_key(name)
    if key not in _SAME_PURCHASE:
        return 0
    if key in _ONLY_SAYS_FRESH:
        return 1
    if _SAME_PURCHASE[key] == "parsley" and "flat" in key:
        return 3
    return 2


def _refold_quantity_segments(qty: str, families: bool = False) -> str:
    """
    Re-sum the same-unit segments of an already-"+"-joined quantity, so a
    third amount joining a line that could not reconcile the first two
    still lands on whichever segment shares its unit.

    `_try_consolidate_quantity` merges a PAIR, and once a line reads
    "500 g + 1 cup" neither side parses as one amount, so every later add
    just lengthened the string: three recipes wanting plain yogurt read
    "500 g + 1 cup + 200 g" where the honest answer is "700 g + 1 cup".
    Folding the segments one at a time is what repair_grocery_quantities
    has always done for lines mangled before that fix; this is that loop,
    extracted so the repair and the live merge cannot disagree.

    Segments that genuinely do not reconcile (mass against volume, "a
    bunch") are left joined exactly as the fallback would write them, so
    this is idempotent and never guesses a conversion.

    `families` is for a household's own line taking the household's own
    add: segments in one measurable family fold too (_sum_measures), so a
    line written "500 g + 1 kg" before 2026-10-20 reads "2 kg" after
    another "500 g" rather than "1 kg + 1 kg".
    """
    if " + " not in (qty or ""):
        return qty
    kept: list[str] = []
    for seg in (part.strip() for part in qty.split(" + ")):
        if not seg:
            continue
        for i, already in enumerate(kept):
            candidate, reconciled = _try_consolidate_quantity(already, seg)
            # Only a REAL reconciliation counts. Folding left to right into
            # one accumulator cannot work -- the accumulator itself becomes
            # "500 g + 1 cup", which parses as nothing, so every later
            # segment just lengthened it. Each segment is offered to each
            # one already kept instead, and the flag is what says whether
            # the units actually met.
            if not reconciled and families:
                summed = _sum_measures(already, seg, exact=False)
                candidate, reconciled = (summed, True) if summed is not None else (candidate, False)
            if reconciled:
                kept[i] = candidate
                break
        else:
            kept.append(seg)
    return " + ".join(kept) or qty


def _more_specific_name(current: str, incoming: str) -> str:
    """
    Which wording one merged line should carry: the variety over the plain
    name, because "Yellow onion" tells the shopper which onion and "Onions"
    does not, and a real variety over a name that only adds "fresh"
    ("Flat-leaf parsley" over "Fresh parsley"; see _ONLY_SAYS_FRESH).
    Otherwise the name already there stays (two plurals, two varieties of
    equal standing, or anything not in _SAME_PURCHASE).
    """
    if incoming and _specificity(incoming) > _specificity(current) \
            and _merge_key(current) == _merge_key(incoming):
        return incoming
    return current


def _merge_key(name: str) -> str:
    """
    The name two grocery lines have to share to be the same thing.

    Case and spacing were already ignored; number was not, so "Bell
    pepper" and "Bell peppers" sat on the list as two separate lines whose
    quantities never combined -- which reads to whoever is shopping as two
    different things to buy.

    Only the LAST word is singularised. In an English food name the
    trailing word is the thing itself and the leading words describe it
    ("bell pepper", "spring onion", "chicken thigh"), so that is where
    number lives; touching earlier words would start collapsing names that
    genuinely differ.

    Deliberately conservative, and it fails toward leaving two lines
    rather than combining two things. A duplicate line is visible and
    mildly annoying; a wrong merge is invisible and means something never
    gets bought.

    One step past number: a short allow-list of plain names and the one
    variety they mean ("Onions" / "Yellow onion", "Rice" / "Long-grain
    white rice") share a key -- see _SAME_PURCHASE for why it is a list
    and not a rule.
    """
    key = _plain_name_key(name)
    # The variety a plain-named recipe means is the same purchase as the
    # plain name ("Yellow onion" and "Onions"); see _SAME_PURCHASE.
    return _SAME_PURCHASE.get(key, key)


def _singular_word(word: str) -> str:
    if len(word) <= 3 or not word.endswith("s"):
        return word
    if word.endswith("ss"):                  # glass, cress, watercress
        return word
    if word in _JUST_ADD_S:
        return word[:-1]
    if word.endswith("ies"):                 # berries -> berry
        return word[:-3] + "y"
    if word.endswith("oes"):                 # tomatoes -> tomato
        return word[:-2]
    if word.endswith(("ches", "shes", "xes", "zes")):   # peaches -> peach
        return word[:-2]
    # Everything else drops the s. Words like "asparagus" and "hummus"
    # come out mangled ("asparagu") but consistently so, which is all a
    # matching key needs -- they only ever have to equal themselves.
    return word[:-1]


def _try_consolidate_quantity(existing_qty: str, new_qty: str) -> tuple[str, bool]:
    """
    Try to merge two quantity strings for the same grocery item. Returns
    (resulting_quantity_string, was_merged). Merges when both are parseable
    and share the same unit (e.g. "2 cups" + "1 cup" -> "3 cups"), or when
    one side is blank. When units are both present but don't reconcile
    (e.g. "2 cups flour" + "1 lb flour"), nothing is guessed — both amounts
    are kept together on one line so the shopper sees both rather than a
    silently wrong conversion.

    What it will NOT do is write the same quantity twice. Two sides that
    are the same words are the same unit whether or not this module can
    read that unit, and "2 lb bag (frozen) + 2 lb bag (frozen) + 2 lb bag
    (frozen) + 2 lb bag (frozen)" is not an honest report of uncertainty,
    it is four copies of one line — see _repeat_or_concatenate.
    """
    note = _quantities._merge_notes(
        _quantities._quantity_note(existing_qty), _quantities._quantity_note(new_qty)
    )
    existing_qty = _quantities._strip_prep_descriptor((existing_qty or "").strip())
    new_qty = _quantities._strip_prep_descriptor((new_qty or "").strip())
    if not existing_qty:
        return new_qty, True
    if not new_qty:
        return existing_qty, True
    existing_parsed = _quantities._parse_quantity(existing_qty)
    new_parsed = _quantities._parse_quantity(new_qty)
    if existing_parsed and new_parsed and existing_parsed[1] == new_parsed[1]:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(existing_parsed[0] + new_parsed[0], existing_parsed[1]),
            note,
        ), True
    shared_unit = _shared_package_unit(existing_parsed, new_parsed)
    if shared_unit:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(existing_parsed[0] + new_parsed[0], shared_unit), note
        ), True
    return _repeat_or_concatenate(existing_qty, new_qty, sum_counts=True)


def _rack_quantity(item: str, qtys: list[str]) -> str | None:
    """
    What a spice-rack line (spices.is_spice) holds, from its parts — the
    amounts already on it and the one arriving, or its whole ledger — or
    None when the name isn't a spice-rack thing.

    One STORE UNIT, whatever the week's recipes measure (Loop Board,
    2026-10-18: "Ticking a spice to buy adds one store unit ('1 jar'),
    whatever the total"): seven pinches of flaky salt are one box, not
    seven (Tester Home plan 72), and 2½ tsp of cumin are one jar, not a
    line reading "2.5 tsp" that nobody can buy. The unit is the kind's
    (spices.store_unit: a jar, a bottle of oil, a box of salt) whatever
    package word an old recipe used, so the line reads the same whichever
    meal arrived first; two only when the measured parts come to more
    than one holds.
    The measured total itself is the Spices row's to show
    (spices.week_total), not the line's. The merge and every recompute
    off the ledger ask this one function, so the line a week builds is
    the line a removal re-derives.
    """
    from . import spices as _spices  # lazy: spices imports this module

    parts = [_quantities._strip_prep_descriptor(_quantities._split_quantity_note((q or "").strip())[0])
             for q in qtys]
    parts = [_quantities._split_repeat_count(p)[0] for p in parts if p]
    if not parts or not _spices.is_spice(item):
        return None
    # Only what the parts really measure counts toward a second unit; a
    # package or "to taste" here is a little (spices.spice_measure).
    measured = sum(
        m for m, about in (_spices.spice_measure(item, p) for p in parts) if m is not None and not about
    )
    # A recipe that names MORE than one package ("2 bottles" of frying
    # oil) meant it: that count is a floor, as the old max rule had it.
    return _with_package_floor(_spices.store_unit(item, measured), parts)


def _with_package_floor(rack: str, theirs: list[str]) -> str:
    """`rack` (_rack_quantity's store units), or the largest package
    amount among `theirs` when that is more — in their words."""
    best = _quantities._parse_quantity(rack) or (0.0, None)
    out = rack
    for qty in theirs:
        pack = _quantities.rack_package(
            _quantities._strip_prep_descriptor(_quantities._split_quantity_note((qty or "").strip())[0])
        )
        if pack and pack[0] > best[0] + 1e-9:
            best = pack
            out = _quantities._format_quantity(pack[0], _quantities._normalize_container_word(pack[1]))
    return out


# What a shopper picks off the shelf for the things recipes measure in
# kitchen units (Loop Board, realistic week amounts, 2026-10-19: "8 slices"
# of bread, "5 cups" of blueberries and Greek yogurt, "2 cups" of granola,
# Butter "1 tub + 10 tbsp", Feta "12 oz + 1 cup crumbled", Baby spinach
# "1 bag + 2 cups"). Per kind: the store unit (sized where the size is the
# thing to know), and how much of one each kitchen unit is — per cup, per
# gram, per slice, per package word. Approximate on purpose (a cup of
# crumbled feta is ~150 g; a loaf ~20 slices) and rounded UP to whole store
# units, the direction this module errs. One line of the table to argue.
_STORE_UNITS = {
    "bread": ("loaf", {"slice": 1 / 20, "loaf": 1.0}),
    "berries": ("pint", {"cup": 0.5, "pint": 1.0, "container": 1.0, "punnet": 1.0}),
    "yogurt": ("tub (750 g)", {"cup": 1 / 3, "g": 1 / 750, "tub": 1.0, "container": 1.0}),
    "granola": ("bag", {"cup": 0.25, "g": 1 / 400, "bag": 1.0, "box": 1.0}),
    # Not "stick": a recipe that buys butter by the stick is shopped by the
    # stick (test_recipe_quantity_sanity), so such a ledger reads as before.
    "butter": ("lb", {"cup": 0.5, "g": 1 / 454, "tub": 1.0, "block": 1.0,
                      "package": 1.0, "pack": 1.0}),
    "feta": ("block (200 g)", {"cup": 0.75, "g": 1 / 200, "block": 1.0, "container": 1.0, "tub": 1.0,
                               "package": 1.0}),
    "baby spinach": ("bag", {"cup": 0.2, "g": 1 / 142, "bag": 1.0, "container": 1.0, "box": 1.0,
                             "package": 1.0}),
}
_BERRY_WORDS = {"blueberry", "raspberry", "blackberry", "strawberry"}
_NOT_A_LOAF = {"crumb", "breadcrumb", "flour", "garlic", "naan", "pita", "banana", "corn", "flat", "monkey"}
_NOT_FRESH = {"frozen", "dried", "jam", "freeze"}
_G_PER = {"g": 1.0, "kg": 1000.0, "oz": 28.35, "lb": 453.6}


def _store_kind(item: str) -> str | None:
    """Which _STORE_UNITS kind a grocery name is, by its merge key."""
    key = _merge_key(item)
    words = key.replace("-", " ").split(" ")
    if not words or not words[-1] or any(w in _NOT_FRESH for w in words):
        return None
    last = words[-1]
    if last in ("bread", "sourdough") and not (set(words) & _NOT_A_LOAF):
        return "bread"
    if last in _BERRY_WORDS or key in ("cherry tomato", "grape tomato"):
        return "berries"
    if last in ("yogurt", "yoghurt"):
        return "yogurt"
    if last == "granola":
        return "granola"
    if key in ("butter", "unsalted butter", "salted butter"):
        return "butter"
    if last == "feta" or key == "feta cheese":
        return "feta"
    if key == "baby spinach":
        return "baby spinach"
    return None


def _store_amount(item: str, qtys: list[str]) -> str | None:
    """
    A plan line's amount in the store's unit, from its ledger — "3 pints",
    "2 tubs (750 g)", "1 lb" — or None when the item has no store unit or a
    row can't be read in it (the ordinary arithmetic applies).

    Read off the WHOLE LEDGER, never off the line's text, so it is the one
    rounding on the whole line: the meals' unrounded shares (cups, slices,
    grams) are converted and added, a sealed package (the "1 tub" the
    package path bought once for the week, quantities._PACKAGE_UNITS)
    counts once however many meals name it, and the total is rounded UP to
    whole store units once. The ingest and every removal both ask this,
    through _plan_or_rack_amount, so a week and its re-reads agree.
    """
    kind = _store_kind(item)
    if not kind:
        return None
    unit, per = _STORE_UNITS[kind]
    packages, measured, seen, by_weight = 0.0, 0.0, False, 0
    for raw in qtys:
        core = _quantities._strip_prep_descriptor(_quantities._split_quantity_note((raw or "").strip())[0])
        if not core:
            continue
        parsed = _quantities._parse_quantity(core)
        if not parsed or not parsed[1]:
            return None
        amount, u = parsed
        word = _quantities._split_package_size(u)[0].rpartition(" ")[2]
        if u in _quantities._VOLUME_TO_TSP and "cup" in per:
            measured += amount * _quantities._VOLUME_TO_TSP[u] / _quantities._VOLUME_TO_TSP["cup"] * per["cup"]
        elif u in _G_PER and "g" in per:
            measured += amount * _G_PER[u] * per["g"]
            by_weight += 1
        elif word in per and word in _quantities._PACKAGE_UNITS:
            packages = max(packages, amount * per[word])
        elif word in per:
            measured += amount * per[word]
        else:
            return None
        seen = True
    if not seen or by_weight == sum(1 for q in qtys if (q or "").strip()):
        # Nothing to convert, or only weights: a weight is what the label
        # says already ("300 g" of yogurt is the cook card's 300 g —
        # test_onboarding_planning). Weights convert only beside a cup or a
        # package, to make one amount of "12 oz + 1 cup crumbled".
        return None
    # A sealed package is what the week draws on, so it covers measured
    # amounts up to its own size — "1 tub" of butter and 10 tbsp is the tub
    # (the package rule's generous-downward bet, recipes'
    # _add_recipe_ingredients_for_entries). Past its size the package was
    # not enough for them, so the two add: a tub and 3 cups of butter is
    # 2.5 lb, not the 1.5 the larger alone says (review, 2026-10-19).
    total = packages if measured <= packages + 1e-9 else packages + measured
    return _quantities._format_quantity(max(1.0, float(math.ceil(total - 1e-6))), unit)


def _sum_measures(existing_qty: str, new_qty: str, exact: bool, same_unit: bool = False) -> str | None:
    """
    Two amounts in one measurable family added as one amount, or None when
    they are not — the caller then merges the way it always did.

    Part cups made this needed (2026-10-19): "1/4 cup" used to be stored as
    "4 tbsp" and summed with "3 tbsp" as the same word; as "1/4 cup" it did
    not, and the list read "3 tbsp + 1/4 cup". `exact` is for a plan's share
    joining a household's line — see _quantities._exact_volume_text.

    A household's own second amount (not `exact`, not `same_unit`) adds in
    ANY of the measurable families, not just volumes: "500 g" of flour and
    then "1 kg" is "1.5 kg", "8 oz" of beef and then "1 lb" is "1.5 lbs",
    "500 ml" and "1 l" is "1.5 l" (2026-10-20). It used to read
    "500 g + 1 kg" — two numbers to add up in the aisle, on a line nothing
    else will ever recompute. Families only (quantities._UNIT_CONVERSION_
    GROUPS): grams never turn into pounds here, the same "nothing guessed"
    line _convert_to_unit draws.
    """
    note = _quantities._merge_notes(
        _quantities._quantity_note(existing_qty), _quantities._quantity_note(new_qty)
    )
    a = _quantities._parse_quantity(_quantities._strip_prep_descriptor((existing_qty or "").strip()))
    b = _quantities._parse_quantity(_quantities._strip_prep_descriptor((new_qty or "").strip()))
    if not a or not b or not a[1] or not b[1]:
        return None
    if same_unit and a[1] != b[1]:
        return None
    if a[1] in _quantities._VOLUME_TO_TSP and b[1] in _quantities._VOLUME_TO_TSP:
        tsp = a[0] * _quantities._VOLUME_TO_TSP[a[1]] + b[0] * _quantities._VOLUME_TO_TSP[b[1]]
        if exact:
            text = _quantities._exact_volume_text(tsp, "tsp")
        else:
            text = _quantities._humanize_grocery_quantity(tsp, "tsp")
        return _quantities._with_note(text, note)
    if exact or a[1] == b[1]:
        # A plan's share outside volumes, or one unit word: the ordinary
        # arithmetic already does this, exactly as before.
        return None
    family = next(
        (g for g in _quantities._UNIT_CONVERSION_GROUPS if a[1] in g and b[1] in g), None
    )
    if family is None:
        return None
    smallest = min(family, key=family.get)
    total = a[0] * family[a[1]] + b[0] * family[b[1]]
    text = _quantities._humanize_grocery_quantity(total, smallest)
    # The list rounds a weight to the nearest quarter of its unit, so a
    # small amount can round away ("1 lb" + "2 oz" is "1 lb", either way
    # round). A sum that reads no more than the larger of the two would
    # swallow the smaller: both amounts stay on the line instead, as before.
    summed = _quantities._parse_quantity(text)
    if not summed or summed[1] not in family:
        return None
    if summed[0] * family[summed[1]] <= max(a[0] * family[a[1]], b[0] * family[b[1]]) + 1e-9:
        return None
    return _quantities._with_note(text, note)


def _shared_package_unit(existing_parsed, new_parsed) -> str | None:
    """
    The unit two parsed quantities should merge under when their units
    differ only in whether the package's SIZE was stated — "1 bag (2 lb)"
    from one recipe beside "1 bag" from another. To a shopper those are
    two bags of the same thing, and leaving them as "1 bag (2 lb) + 1 bag"
    is the concatenation bug wearing a different hat. The stated size
    wins, because it says more.

    Returns None — and the caller falls through to the honest "both
    amounts, unreconciled" line — whenever there is a real disagreement to
    report: a different container word, or two DIFFERENT stated sizes
    ("1 bag (2 lb)" against "1 bag (500 g)"), where picking one would
    silently throw away what the other recipe asked for.
    """
    if not existing_parsed or not new_parsed or not existing_parsed[1] or not new_parsed[1]:
        return None
    existing_head, existing_size = _quantities._split_package_size(existing_parsed[1])
    new_head, new_size = _quantities._split_package_size(new_parsed[1])
    if existing_head != new_head or existing_head not in _quantities._CONTAINER_UNIT_PLURALS:
        return None
    if existing_size and new_size:
        return None
    return existing_parsed[1] if existing_size else new_parsed[1]


def _repeat_or_concatenate(existing_qty: str, new_qty: str, sum_counts: bool) -> tuple[str, bool]:
    """
    The last resort when two quantities for the same item can't be added.
    Identical text is never written twice: it collapses to one copy with a
    "×N" repeat marker ("a handful" + "a handful" -> "a handful ×2"), and
    an existing marker counts up rather than starting a second line.
    sum_counts says whether N adds (four meals each wanting one) or takes
    the larger (a sealed package, where four meals still want one) — the
    same split as _try_consolidate_quantity vs _greater_of_quantity.

    Genuinely different text still concatenates, because that is the
    honest answer: "2 cups + 1 lb" tells the shopper there are two amounts
    and this module could not reconcile them. What it stops being is a way
    for one repeated breakfast to fill a line with copies of itself.
    """
    existing_base, existing_count = _quantities._split_repeat_count(existing_qty)
    new_base, new_count = _quantities._split_repeat_count(new_qty)
    if existing_base.lower() == new_base.lower():
        count = existing_count + new_count if sum_counts else max(existing_count, new_count)
        return _quantities._with_repeat_count(existing_base, count), True
    return f"{existing_qty} + {new_qty}", False


def _merge_target(same_name: list, quantity: str, consolidate, standing: bool, source_plan: int | None = None):
    """
    Which of the lines already on the list with this name a new amount
    joins — (row, merged quantity, units reconciled) — or None for a line
    of its own.

    The first line the amount adds up with cleanly wins, in list order.
    Failing that, the amount concatenates ("2 cups + 1 lb") onto the
    first line of its OWN KIND — a person's add onto a person's line, a
    plan's onto a plan's — and never across that line. `standing` says
    which kind the add is (True: no plan behind it).

    Why the line between the two kinds matters (Loop Board bug,
    2026-09-13): a hand-added "1" eggs that a recipe's "2 cups" was
    concatenated onto read "1 + 2 cups", and nothing could ever take the
    plan's share back off — the reversal reads a line as one number in one
    unit, and "1 + 2 cups" is not one — so every week added another
    "+ 2 cups" for as long as the eggs stayed unbought. Two lines are the
    honest answer: the person's want, untouched, and the plan's amount on
    a plan-owned line that recomputes from its ledger, leaves with its
    week, and is set aside as a leftover like any other plan line. A
    plan's two recipes that disagree on a unit still share ONE plan line,
    exactly as before, because that line is fully described by its ledger
    and recomputes correctly whatever it reads.

    One line is a person's to join whatever it reads: a spice still
    unticked in the "Spices this week" section (status 'spice',
    spices.py). That line is the plan's reminder, not an amount, and a
    person asking for cumin is answering it — their add ticks it onto the
    list (add_grocery_item), so it must land there and not beside it.

    And a plan's amount never joins ANOTHER plan's amount line
    (`source_plan`, Loop Board, QA walk 2026-10-02). Next week can be
    approved while this week is still running, and both weeks' lines are
    on the list at once; the first same-name line is this week's, so a
    meal added or swapped into next week used to sum onto tonight's
    "2 lbs ground turkey" and stamp next week's id on it — 4 lbs that
    belonged to neither week, and tonight's line gone from this week's
    ledger reading. Each week keeps its own line, which is also what the
    2026-09-13 carry-over rule wanted at approval. A plan's add still
    joins a person's line (standing, see above) and a spice line from any
    week, ticked or not: the section is one jar per name, not one per week,
    and a second cumin row beside a ticked one is a question asked twice.
    That is the spice RACK only. A SAUCE or PANTRY STAPLE shares the
    'spice' status but carries its week's real amount (pantry.py), so it is
    an amount line like any other and keeps to its own week (integration
    hunt, 2026-10-26; widened to them with the 2026-10-19 pantry work, next
    week approved on the Sunday summed its lasagna noodles onto this week's
    unanswered "2 boxes", stamped next week's id on the lot, and Monday's
    sweep of the ended week no longer saw it: "3 boxes" for one lasagna).
    """
    from . import spices as _spices  # lazy: spices imports this module

    kin = None
    for row in same_name:
        other_plan = row["source_weekly_plan_id"]
        if (
            source_plan is not None and other_plan is not None and other_plan != source_plan
            and not _spices.is_spice(row["item"])
        ):
            continue
        households_line = row["source_weekly_plan_id"] is None and row["status"] == "needed"
        if _spices.is_spice(row["item"]) and (
            (not standing and (
                not households_line
                or (_quantities.rack_package(row["quantity"] or "") and _quantities.rack_package(quantity))
            ))
            # A person asking for cumin onto the plan's cumin answers its
            # reminder: the jar the plan's line already buys, not a second.
            or (standing and row["source_weekly_plan_id"] is not None)
        ):
            # A plan's spice is at most one package (_rack_quantity). Onto a
            # household's own line only package-onto-package, which no
            # removal ever re-derives (the package rule holds the line).
            rack = _rack_quantity(row["item"], [row["quantity"] or "", quantity])
            if rack is not None:
                # A household's own count is a floor, never collapsed: their
                # "2 jars" stays 2 jars whatever a plan's share would buy
                # (review, 2026-10-19).
                theirs = [row["quantity"] or ""] if households_line else []
                theirs += [quantity] if standing else []
                return row, _with_package_floor(rack, theirs), True
        merged_qty, merged = None, False
        if households_line and consolidate is _try_consolidate_quantity:
            # Onto a household's own line, the household's own second
            # amount adds within its family ("3 tbsp" + "1/4 cup",
            # "500 g" + "1 kg"),
            # shopping-rounded like any add. A plan's share is added
            # EXACTLY, so _restate_standing_want can take back what was put
            # on — and only in the line's own unit word: a "2 tbsp" share
            # beside "1 cup" still gets a plan line of its own (2026-10-07).
            merged_qty = _sum_measures(
                row["quantity"] or "", quantity, exact=not standing, same_unit=not standing,
            )
            merged = merged_qty is not None
        if not merged:
            merged_qty, merged = consolidate(row["quantity"] or "", quantity)
        if merged:
            return row, merged_qty, True
        # A person's add joins a waiting 'spice' line only when it is a
        # spice-rack line — one jar whatever is written, which the rack
        # branch above settles. A waiting SAUCE or PANTRY STAPLE (pantry.py,
        # 2026-10-18) shares the status but carries the recipes' real amount,
        # so a typed "1 bag" of rice concatenated onto its "2 cups" read
        # "2 cups + 1 bag", the merge made it the household's line, and no
        # swap or drop could take the plan's "2 cups" back off — the
        # 2026-09-13 bug above, through the one door it was left open. Its
        # own line instead: theirs on the list, the plan's still waiting
        # in its section, leaving with its week. An amount that adds up
        # cleanly ("3 cups") still merges and ticks, above.
        own_kind = (row["source_weekly_plan_id"] is None) == standing or (
            standing and row["status"] == "spice" and _spices.is_spice(row["item"])
        )
        if kin is None and own_kind:
            kin = (row, merged_qty, False)
    return kin


def _greater_of_quantity(existing_qty: str, new_qty: str) -> tuple[str, bool]:
    """
    Keep the LARGER of two quantities for the same grocery item instead of
    their sum. Same contract as _try_consolidate_quantity — returns
    (resulting_quantity_string, was_merged) and concatenates rather than
    guessing when the units don't reconcile.

    This is what "one bottle of olive oil per week" means in code. Three
    dinners that each list "1 bottle" want one bottle between them, not
    three; a recipe that genuinely asks for "2 bottles" still wins over a
    "1 bottle" beside it. Only the sealed-package quantities identified by
    quantities.package_unit are merged this way — see
    recipes._add_recipe_ingredients_for_entries, the only caller.
    """
    note = _quantities._merge_notes(
        _quantities._quantity_note(existing_qty), _quantities._quantity_note(new_qty)
    )
    existing_qty = _quantities._strip_prep_descriptor((existing_qty or "").strip())
    new_qty = _quantities._strip_prep_descriptor((new_qty or "").strip())
    if not existing_qty:
        return new_qty, True
    if not new_qty:
        return existing_qty, True
    existing_parsed = _quantities._parse_quantity(existing_qty)
    new_parsed = _quantities._parse_quantity(new_qty)
    if existing_parsed and new_parsed and existing_parsed[1] == new_parsed[1]:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(max(existing_parsed[0], new_parsed[0]), existing_parsed[1]),
            note,
        ), True
    shared_unit = _shared_package_unit(existing_parsed, new_parsed)
    if shared_unit:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(max(existing_parsed[0], new_parsed[0]), shared_unit), note
        ), True
    return _repeat_or_concatenate(existing_qty, new_qty, sum_counts=False)


def _subtract_quantity(current_qty: str, remove_qty: str) -> tuple[str, bool]:
    """
    The inverse of _try_consolidate_quantity — used when a meal that
    contributed some amount to a grocery line is being un-planned (see
    _reverse_meal_grocery_contributions) and that amount needs to come back
    out. Returns (resulting_quantity_string, fully_removed). When both sides
    parse and their units reconcile — the same unit, or two units in the
    same measurable family (lb/oz, cup/tbsp/tsp, g/kg, ml/l; see
    quantities._convert_to_unit) — subtracts normally, treating a
    non-positive remainder as "nothing left" (fully_removed=True, resulting
    string blank). When they can't be reconciled (freeform text, unrelated
    units) but the two strings are identical, that means this contribution
    *is* the whole line (nothing else merged into it), so it's still safe
    to remove entirely. Otherwise nothing is guessed — the line is left
    exactly as-is (fully_removed=False, resulting string unchanged) rather
    than risk deleting an amount still needed for something else.

    This is the fallback path now — a grocery line with other ledger rows
    still on it is reconciled by summing what's LEFT instead (see
    quantities._sum_ledger_quantities), which is what actually fixed the
    lb/oz stranding this function alone couldn't: it only ever sees the
    one subtraction in front of it, not that the line's display unit
    rolled between when this contribution was recorded and now. This still
    has to be unit-normalising itself, though, for the lines that never
    had ledger rows to recompute from (a hand-added line with its own
    quantity edit) and for the household's own standing wants, which are
    never replaced by a recompute — see _reverse_meal_grocery_contributions.

    Whatever _try_consolidate_quantity can write, this has to be able to
    unwrite, or a swap leaves the list slowly drifting: the note a merged
    line carries survives the subtraction ("3 bags, frozen" less one bag
    is "2 bags, frozen"), and a "×N" repeat marker counts back down
    instead of being left as an unreconcilable string.
    """
    note = _quantities._quantity_note(current_qty)
    current_qty = _quantities._strip_prep_descriptor((current_qty or "").strip())
    remove_qty = _quantities._strip_prep_descriptor((remove_qty or "").strip())
    if not current_qty:
        return "", True
    if not remove_qty or current_qty == remove_qty:
        return "", True
    current_parsed = _quantities._parse_quantity(current_qty)
    remove_parsed = _quantities._parse_quantity(remove_qty)
    if current_parsed and remove_parsed:
        remove_amount = _quantities._convert_to_unit(remove_parsed[0], remove_parsed[1], current_parsed[1])
        if remove_amount is not None:
            remainder = current_parsed[0] - remove_amount
            if remainder <= 0.0001:
                return "", True
            return _quantities._with_note(
                _quantities._humanize_grocery_quantity(remainder, current_parsed[1]), note
            ), False
    current_base, current_count = _quantities._split_repeat_count(current_qty)
    remove_base, remove_count = _quantities._split_repeat_count(remove_qty)
    if current_base.lower() == remove_base.lower():
        remaining = current_count - remove_count
        if remaining <= 0:
            return "", True
        return _quantities._with_repeat_count(current_base, remaining), False
    return current_qty, False


def _more_than(qty: str, than_qty: str) -> bool:
    """Whether `qty` is a bigger amount than `than_qty`, read in one unit.
    False whenever either can't be read or the units don't convert — the
    caller then does what it did before."""
    a = _quantities._parse_quantity(qty or "")
    b = _quantities._parse_quantity(than_qty or "")
    if not a or not b:
        return False
    b_in_a = _quantities._convert_to_unit(b[0], b[1], a[1])
    return b_in_a is not None and a[0] > b_in_a + 1e-9


def _no_more_than(new_qty: str, current_qty: str) -> str:
    """
    A meal leaving never makes a line ask for MORE than it asks for now
    (Emily, 2026-10-12: an edit below what the plan needs is kept). On a
    plan's line the edit itself is stored and put back on every recompute
    (_with_household_edit); this is what holds it on a household's own
    line, whose restate cannot see an edit down, and a backstop on any
    line. For a line nobody edited this changes nothing — the meals left
    never round to more than all of them did.
    """
    return current_qty if _more_than(new_qty, current_qty) else new_qty


def _with_household_edit(plan_qty: str, short: str, edited: str) -> str:
    """
    A plan line's amount as its meals' ledger reads it (`plan_qty`), with
    the household's edit DOWN put back on it — see schema.sql on
    short_amount. max(meals - short, min(typed, meals)):

    - "onion 3" edited to 1 (short 2). Lentil Soup's night goes: the meals
      left want 2, and the household's 1 stands (not 2, and not 0 — they
      typed 1). The soup planned again: 3 - 2 = 1, still their 1. Before
      this was stored, each recompute forgot the edit and a drop and
      re-plan walked the line 1 -> 2 -> 3 (review, 2026-10-13).
    - A new dinner wanting 2 more: 5 - 2 = 3 — the new meal's share is
      bought; the household's edit only ever spoke for what was planned.
    - Every meal gone: the meals want nothing, so neither does the line.

    Returns `plan_qty` unchanged when there is no edit or anything can't
    be read in one unit — the line is then what its meals say, as before.
    """
    if not (short or "").strip():
        return plan_qty
    plan_amount, _note = _quantities._split_quantity_note((plan_qty or "").strip())
    p = _quantities._parse_quantity(plan_amount)
    s = _quantities._parse_quantity(short)
    e = _quantities._parse_quantity(edited)
    if not p or not s or not e:
        return plan_qty
    s_amt = _quantities._convert_to_unit(s[0], s[1], p[1])
    e_amt = _quantities._convert_to_unit(e[0], e[1], p[1])
    if s_amt is None or e_amt is None:
        return plan_qty
    amount = max(p[0] - s_amt, min(e_amt, p[0]))
    if amount >= p[0] - 1e-9:
        return plan_qty
    if amount <= 1e-9:
        # "0" typed (they have all of it) stays 0 — a blank result read as
        # "no edit" and the next recompute forgot it (re-review, 2026-10-13).
        return _quantities._with_note(_quantities._format_quantity(0, p[1]), _note)
    return _quantities._with_note(_quantities._humanize_grocery_quantity(amount, p[1]), _note)


def _spices_is_spice(item: str) -> bool:
    from . import spices as _spices  # lazy: spices imports this module
    return _spices.is_spice(item)


def _holds_one_package(item: str, qty: str) -> bool:
    """A ledger row that holds its line as one package until the last meal
    naming it goes: a sealed package (quantities.package_unit), or a spice
    in any package it comes in (_rack_quantity)."""
    return bool(_quantities.package_unit(qty) or (_quantities.rack_package(qty) and _spices_is_spice(item)))


def reversal_changes_line(item: str, reversing_qtys: list[str], others_left: int) -> bool:
    """
    Whether _reverse_meal_grocery_contributions, taking these ledger rows
    off one live line while `others_left` of its rows stay, changes what
    the line says — for the question asked BEFORE a takeover
    (weekly_plan.preview_approved_takeover), so its count follows the
    reversal's own rule. A line no row is left on goes. A package line (a
    jar of cumin, a bottle of oil — _holds_one_package) is held whole
    while any meal still names it, so it stays as it is (review,
    2026-10-22: "3 things would change" when only the soy sauce did). A
    measured line loses the reversed share and is trimmed.
    """
    if not others_left:
        return True
    return not all(_holds_one_package(item, qty or "") for qty in reversing_qtys)


def _plan_or_rack_amount(item: str, ledger: list[str], kept: str, short: str, edited: str) -> str | None:
    """_plan_line_amount, except where the NAME decides how a ledger reads:
    a spice is one package (_rack_quantity), and bread, berries, yogurt,
    granola, butter, feta and baby spinach are counted in the unit the
    store sells them in (_store_amount). The household's edit down and any
    kept amount go on top of that reading, as they do on the plain one."""
    meals = (_rack_quantity(item, ledger) or _store_amount(item, ledger)) if ledger else None
    if meals is None:
        return _plan_line_amount(ledger, kept, short, edited)
    meals = _with_household_edit(meals, short, edited)
    kept = (kept or "").strip()
    if not kept:
        return meals
    return _quantities._sum_ledger_quantities([meals, kept]) or meals


def _plan_line_amount(ledger: list[str], kept: str, short: str, edited: str) -> str | None:
    """
    A plan line's amount from its parts: the meals' ledger, with the
    household's edit down on THAT part alone (_with_household_edit), and
    any kept amount on top. Applied to a total that already held a kept
    leftover, the edit ate the Keep: soup 1 + chili 2 edited to 1, last
    week's 3 kept (4), chili off — read 2, not 1 + 3 (re-review,
    2026-10-13). Without an edit down it is the plain sum it always was.
    None when the ledger can't be read (the caller's fallback applies).
    """
    kept = (kept or "").strip()
    if not (short or "").strip():
        return _quantities._sum_ledger_quantities(ledger + ([kept] if kept else []))
    meals = _quantities._sum_ledger_quantities(ledger)
    if meals is None:
        return None
    if meals:
        meals = _with_household_edit(meals, short, edited)
    if not kept:
        return meals
    # A meals part edited down to nothing adds nothing — and is left out
    # rather than summed, since the ledger sum floors a lone 0 at one.
    parsed = _quantities._parse_quantity(meals or "")
    parts = [meals] if parsed and parsed[0] > 0 else []
    return _quantities._sum_ledger_quantities(parts + [kept])


def _household_edit_columns(conn, row, new_quantity: str) -> dict | None:
    """
    What an edit of a plan line's amount records beside it (Emily,
    2026-10-12: the household's number survives the plan changing). The
    line STAYS the plan's — that is what sets it aside as "still on the
    list from last week" after its week and lets the week-end sweep clear
    it; making it a standing want (the first cut of this) let an edited
    line soak up every later week's onions, 5 -> 7 -> 9, never asked about.

    Above what its meals want: the difference is kept_amount, the column
    "Keep" already uses — every reversal and recompute counts it in, and
    it is still there when the last meal goes. Below: short_amount and
    edited_to (_with_household_edit). Equal: neither.

    An amount typed that isn't a number clears all three. None when the
    meals' side can't be read in one unit with it — the columns are left
    as they were and the line follows its meals, as before. "3 kg" against
    "5 lbs" IS read in one unit (quantities._convert_for_household_edit),
    and recorded in the meals' unit; mass against volume never is.
    """
    ledger = [
        r["quantity"] or "" for r in conn.execute(
            "SELECT quantity FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ?",
            (household_id(), row["id"]),
        ).fetchall()
    ]
    # Read the way the line is written (a spice's one jar, berries in
    # pints), or an edit of "3 pints" down to 2 had no meals' amount in
    # pints to be measured against.
    meals = _plan_or_rack_amount(row["item"], ledger, "", "", "")
    typed = _quantities._parse_quantity(
        _quantities._split_quantity_note((new_quantity or "").strip())[0]
    )
    clear = {"kept_amount": "", "short_amount": "", "edited_to": ""}
    if not typed:
        # "a few": no number left to keep an edit against.
        return clear
    if meals is None:
        return None
    if not meals:
        # No meal behind it (its last one went while something was kept):
        # all of it is the household's.
        return {**clear, "kept_amount": _quantities._format_quantity(typed[0], typed[1])}
    m = _quantities._parse_quantity(meals)
    m_amt = _quantities._convert_to_unit(m[0], m[1], typed[1]) if m else None
    if m_amt is None and m:
        # Typed in the other system ("3 kg" on a "5 lbs" line): read the
        # typed number in the MEALS' unit and record the edit there, so
        # every later recompute sums one family the way it always has.
        in_meals_unit = _quantities._convert_for_household_edit(typed[0], typed[1], m[1])
        if in_meals_unit is not None:
            typed, m_amt = (in_meals_unit, m[1]), m[0]
    if m_amt is None:
        return None
    diff = typed[0] - m_amt
    if diff > 1e-9:
        return {**clear, "kept_amount": _quantities._format_quantity(diff, typed[1])}
    if diff < -1e-9:
        return {
            **clear,
            "short_amount": _quantities._format_quantity(-diff, typed[1]),
            "edited_to": _quantities._format_quantity(typed[0], typed[1]),
        }
    return clear


def _restate_standing_want(
    current_qty: str, total_before: float, total_after: float, unit: str | None,
) -> tuple[str, bool] | None:
    """
    Write a household's own standing want back out with the plan's share
    changed — "what is on this line now, less what the plan's meals rounded
    to, plus what the ones still planned round to."

    Returns (quantity, fully_removed) or None when the line itself can't be
    read as a number, or its unit can't be reconciled with the plan's, in
    which case the caller falls back to _subtract_quantity.

    Three things make it work, and every one of them was learned by
    measuring drift that survived the version before it.

    ONE STEP, not a running subtraction. Taking a share off and
    re-humanizing the whole line at every removal snaps the result to a
    quantum each time, and the residue accumulates instead of cancelling.

    THE PLAN'S SHARE IS ROUNDED THE WAY THE INGEST ROUNDED IT
    (_shopping_round on the ledger's own unit, rolled up exactly as
    recipes._week_bought_amount does, and only then converted into the
    line's unit). Rounding it in the LINE's unit instead looks tidier and
    is wrong whenever the line's display unit rolls mid-sequence: the first
    removal is then rounded at a quarter-POUND, over-crediting the plan,
    the line rolls to ounces, and the later removals — now on a quarter-
    ounce quantum — never give the over-credit back. Measured: a hand-added
    "2 oz" under three dinners came back BLANK, and a "500 ml" staple came
    back as 416.75 ml and kept falling.

    THE HOUSEHOLD'S OWN AMOUNT IS SNAPPED BACK ONTO THE LINE'S QUANTUM
    (_snap_to_unit_quantum). It is re-derived out of the displayed line
    every time, and the line has been re-rounded for display in between, so
    without this the derived value drifts a little further each removal.
    Snapping is skipped when it would annihilate a genuinely positive
    amount — a 2 oz want on a line reading in pounds is smaller than half
    that line's quantum, and the household's own two ounces must not
    disappear because of the unit its line happens to be shown in.

    WHAT IS NOT PROMISED. An exact restate is not reachable across a
    rolling display unit: the line is a rounded string, and what the add
    rounded away is not recoverable from it — 15 oz of plan on a
    household's own 8 oz is stored as "1.5 lbs" and the 1.75 oz difference
    is gone before any reversal runs. "Never below the household's own
    amount" is therefore NOT the promise, and is not a bar the code this
    replaces clears either. What IS held to: a line somebody typed is never
    blanked; nothing compounds week to week; and there are strictly fewer
    below-own outcomes than before, at comparable worst case. The tail that
    remains is the snap below — putting a display-drifted number back on a
    quantum can be half a quantum out, which at a quarter-POUND is three
    ounces. One-shot, never cumulative, and measured better in aggregate
    than what it replaces. The numbers are in CLAUDE.md's entry for this
    change. The bias is upward wherever the arithmetic has any freedom,
    which is this module's whole stance (an extra pepper costs a pepper; a
    missing one costs the dinner).

    The household's own amount is also floored at nothing, which is load-
    bearing rather than defensive: hand-edit an 8 down to 2 mid-week and
    the plan's share is bigger than the whole line, so without the floor
    the next removal blanks something the household typed.
    """
    note = _quantities._quantity_note(current_qty or "")
    parsed = _quantities._parse_quantity(current_qty or "")
    if not parsed:
        return None
    current_amount, line_unit = parsed
    rounded_before, before_unit = _quantities._shopping_round(total_before, unit)
    rounded_after, after_unit = _quantities._shopping_round(total_after, unit)
    plan_before = _quantities._convert_to_unit(rounded_before, before_unit, line_unit)
    plan_after = _quantities._convert_to_unit(rounded_after, after_unit, line_unit)
    if plan_before is None or plan_after is None:
        return None
    households_own = max(0.0, current_amount - plan_before)
    snapped = _quantities._snap_to_unit_quantum(households_own, line_unit)
    if snapped > 0 or households_own <= 0:
        households_own = snapped
    total = households_own + plan_after
    if total <= 0:
        return "", True
    if line_unit in _quantities._VOLUME_TO_TSP:
        # Written exactly, never re-rounded to a cup step: the next removal
        # re-derives the household's own amount from this text, and cup
        # steps are not closed under addition (see _exact_volume_text).
        written = _quantities._exact_volume_text(total, line_unit)
    else:
        written = _quantities._humanize_grocery_quantity(total, line_unit)
    return _quantities._with_note(written, note), False


def _ledger_quantities(item_id: int, conn) -> list[str]:
    """Every per-meal ledger row's quantity on one grocery line."""
    return [
        row["quantity"] or ""
        for row in conn.execute(
            "SELECT quantity FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ?",
            (household_id(), item_id),
        ).fetchall()
    ]


def _restate_standing_want_after_add(
    item_id: int, previous_qty: str, prior_ledger: list[str], merged_qty: str, conn=None,
) -> None:
    """
    The ADD half of _restate_standing_want: a plan's meal joining a
    household's own line puts on what the plan's ROUNDED total moved by —
    ceil(ledger after) - ceil(ledger before) — not the meal's share rounded
    on its own.

    add_grocery_item sums the incoming amount, rounded alone, onto the line
    (half an onion goes on as a whole one). Every removal then takes back
    only the rounded-total difference, so the extra stayed on as if the
    household had typed it: Swap then Undo read 16 -> 17 -> 17 over a
    ledger back at exactly 14.0, and every cycle added an onion (overnight
    2026-10-21). Restating with the same arithmetic both ways is what makes
    there-and-back land where it started.

    Held between what the line read before (the household's own amount is
    never lost) and what the plain add wrote (a line the household edited
    down, which the restate cannot see, does no worse than it did). Leaves
    the add's answer alone wherever the restate declines (None).
    """
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        totals = _quantities._ledger_totals(_ledger_quantities(item_id, conn), prior_ledger)
        if totals is None:
            return
        total_after, total_before, unit = totals
        restated = _restate_standing_want(previous_qty, total_before, total_after, unit)
        if restated is None or restated[1]:
            return
        new_qty = _quantities._with_note(
            _quantities._split_quantity_note(restated[0])[0], _quantities._quantity_note(merged_qty),
        )
        new_qty = _no_more_than(new_qty, merged_qty)
        if _more_than(previous_qty, new_qty):
            new_qty = merged_qty
        if new_qty != merged_qty:
            conn.execute(
                "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                (new_qty, item_id, household_id()),
            )
            if own_conn:
                conn.commit()
    finally:
        if own_conn:
            conn.close()


def _reverse_meal_grocery_contributions(entry_id: int, conn=None, only_items=None, only_link_ids=None) -> dict:
    """
    Undo whatever a meal_plan_entries row added to the grocery list, via the
    meal_plan_grocery_links ledger recorded at plan_meal() time — called
    right before that entry is deleted (see swap_meal_in_plan/
    swap_component_in_plan) so changing a planned meal actually replaces its
    ingredients on the grocery list instead of only ever piling the new
    meal's ingredients on top of the old ones. For each linked grocery line,
    puts the line back to what every OTHER meal still on the ledger for it
    actually adds up to (see quantities._sum_ledger_quantities) —
    removing the line entirely once no ledger row for it remains at all,
    or updating it to that recomputed total otherwise. A line already
    moved to in_cart/purchased is left alone regardless — the shopper has
    already acted on it, so this won't yank something out of a cart
    mid-trip. Always clears the ledger rows for this entry afterward,
    whether or not anything was adjusted.

    Recomputing from the ledger, rather than subtracting this one
    contribution out of whatever the line currently displays, is what
    keeps this exactly reversible regardless of what order a week's
    reversals happen in (see clear_weekly_plan, which reverses a week's
    entries in no particular order). A grocery line's display unit rolls
    to whatever reads best AT ITS CURRENT TOTAL (see
    quantities._humanize_grocery_quantity) — sequentially subtracting one
    contribution at a time can walk that total down through a unit
    boundary (a line at "1.25 lbs" becomes "8 oz" once it drops under a
    pound), and the NEXT contribution still on the ledger was recorded
    against the total as it stood at ingest, not against whatever unit the
    line happens to display now. Recomputing the whole remaining total in
    one pass sidesteps that entirely: it only ever asks what's left, never
    what changed.

    That recompute only ever replaces a line's quantity with something the
    ledger can fully account for, which is true whenever the line's
    source_weekly_plan_id is set — every dollar of it came from a
    recipe, and the ledger has a row for each one. A line with no
    source_weekly_plan_id (see add_grocery_item's keep_standing) has at
    least one contribution the ledger doesn't know about — a person's own
    standing want, or an amount they added by hand — so it falls back to
    subtracting this one meal's share out of the current display instead
    (see _subtract_quantity), and is never deleted by this at all: the
    meals that borrowed space on it are gone, but the want isn't.

    A SEALED-PACKAGE line (one bottle of oil, one bag of granola — see
    quantities.package_unit) is a separate exception, and has to be,
    because the add side no longer adds one per meal: the whole week's oil
    is a single bottle however many dinners named it. Subtracting a bottle
    per meal would take the line off the list the first time any one of
    those meals changed, while the rest still needed it. So a package line
    survives until the LAST meal holding a link to it goes, and then goes
    with it — which keeps clearing a whole week exactly symmetric with
    approving it, and leaves the bottle alone when a single meal is
    swapped. A package line with no source_weekly_plan_id was asked for by
    a person directly and is never removed by this at all; the plan
    borrowed it, it doesn't own it.

    `conn` is for one caller and is not part of the assistant-facing API:
    retire_overlapping_plans runs a whole multi-plan takeover inside ONE
    write transaction, so it hands its connection down rather than letting
    each meal's reversal commit on its own — a crash between two of those
    commits used to leave the first plan's groceries reversed while the
    caller reported failure. Given a connection, this reads and writes on
    it and neither commits nor closes: the caller owns both. Left unset,
    every other call site behaves exactly as before — its own connection,
    its own commit, its own close.

    `only_link_ids` narrows the reversal to those ledger rows of the entry;
    `only_items` to the rows whose item is one of these names (matched on
    _merge_key). Both are plates.remove_component taking one added side
    back off a meal without touching the dish's own shopping. Left unset,
    every row the entry holds is reversed, as before. In every case
    "every OTHER contribution" to a line means every ledger row not being
    reversed here, by row id — so a side's lemon coming off leaves the
    recipe's own lemon, on the same entry, counted.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    links = conn.execute(
        "SELECT id, grocery_item_id, item, quantity FROM meal_plan_grocery_links "
        "WHERE household_id = ? AND meal_plan_entry_id = ?",
        (household_id(), entry_id),
    ).fetchall()
    if only_link_ids is not None:
        keep_ids = {int(i) for i in only_link_ids}
        links = [link for link in links if link["id"] in keep_ids]
    if only_items is not None:
        wanted = {_merge_key(name) for name in only_items if name}
        links = [link for link in links if _merge_key(link["item"] or "") in wanted]
    narrowed = only_items is not None or only_link_ids is not None
    reversing_ids = [link["id"] for link in links]
    not_reversing = "(" + ",".join("?" * len(reversing_ids)) + ")" if reversing_ids else "(-1)"
    removed_items = []
    trimmed_items = []
    for link in links:
        grocery_row = conn.execute(
            "SELECT id, item, quantity, status, removed_by, source_weekly_plan_id, kept_amount, "
            "short_amount, edited_to FROM grocery_items "
            "WHERE id = ? AND household_id = ?",
            (link["grocery_item_id"], household_id()),
        ).fetchone()
        # A pending spice ('spice', see spices.py) is a needed line the
        # shopper hasn't ticked; it recomputes and clears like one.
        live = bool(grocery_row) and grocery_row["status"] in ("needed", "spice")
        # Last week's amount the household said "Keep" to, sitting on this
        # plan line (keep_carried_over_item). No meal's ledger row holds it,
        # so it is counted in beside them and the line never goes while it
        # is there — the kept amount is the household's, not a meal's.
        kept = (grocery_row["kept_amount"] or "").strip() if live and grocery_row["source_weekly_plan_id"] is not None else ""
        # A plan's line set aside for now ("Have it", the freezer step) is
        # kept in step with its meals too, so Put back returns what the
        # meals left need: skipping it put back "10 oz" of spinach for one
        # 5 oz frittata (2026-10-10). The same rules as a live line — a
        # package keeps its one jar, an edited amount is only re-read when a
        # meal really goes — except it is never deleted here: it is off the
        # list already, and its Put back must still find the row.
        # NOT a carried-over row (carried_kept / carried_dropped): its amount
        # was folded onto this week's line or answered for good, and
        # undo_carried_over_decision takes that recorded amount back off —
        # recounting it here made the undo take off too little (review,
        # 2026-10-10: 10 oz left for one 5 oz dinner).
        set_aside = (
            bool(grocery_row) and grocery_row["status"] == "removed"
            and grocery_row["source_weekly_plan_id"] is not None
            and grocery_row["removed_by"] not in ("carried_kept", "carried_dropped")
        )
        # So does a line taken off with Remove (REMOVED_BY_LIST): its Undo
        # puts the row back exactly as it stands (undo_remove_grocery_item),
        # so it is kept in step with its meals while it is off — the same
        # package and recompute rules as a live line, so a jar stays one
        # jar and an edited amount is only re-read when a meal really goes.
        # A household's own line has the plan's part taken off the same
        # way, while it can still be measured. Never deleted here: the
        # Undo must still find the row.
        list_removed = (
            grocery_row is not None and grocery_row["status"] == "removed"
            and grocery_row["removed_by"] == REMOVED_BY_LIST
        )
        # Either way the row is off the list for now and its Undo / Put back
        # must still find it, so it is recounted but never deleted here.
        off_list = set_aside or list_removed
        live = live or off_list
        if live and _holds_one_package(grocery_row["item"], link["quantity"] or ""):
            still_wanted = conn.execute(
                "SELECT COUNT(*) AS n FROM meal_plan_grocery_links "
                f"WHERE household_id = ? AND grocery_item_id = ? AND id NOT IN {not_reversing}",
                (household_id(), link["grocery_item_id"], *reversing_ids),
            ).fetchone()["n"]
            if not still_wanted and not kept and grocery_row["source_weekly_plan_id"] is not None and not off_list:
                conn.execute(
                    "DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
                    (grocery_row["id"], household_id()),
                )
                removed_items.append(grocery_row["item"])
            elif not still_wanted and kept and grocery_row["source_weekly_plan_id"] is not None:
                # The last meal that wanted the bag has gone; what is left
                # to buy is exactly the kept amount — not the merged "2 bags".
                note = _quantities._quantity_note(grocery_row["quantity"] or "")
                new_qty = _quantities._with_note(kept, note)
                if new_qty != (grocery_row["quantity"] or ""):
                    conn.execute(
                        "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                        (new_qty, grocery_row["id"], household_id()),
                    )
                    trimmed_items.append(grocery_row["item"])
            elif still_wanted and grocery_row["source_weekly_plan_id"] is not None and (
                _spices_is_spice(grocery_row["item"]) or _store_kind(grocery_row["item"])
            ):
                # A spice's box going while a measured pinch stays, or a tub
                # of yogurt while the cups stay: the line reads what is left
                # (_plan_or_rack_amount), not the package.
                _recompute_plan_line_from_ledger(grocery_row["id"], conn=conn, skip_link_ids=reversing_ids)
        elif live:
            is_standing_want = grocery_row["source_weekly_plan_id"] is None
            other_rows = conn.execute(
                "SELECT quantity FROM meal_plan_grocery_links "
                f"WHERE household_id = ? AND grocery_item_id = ? AND id NOT IN {not_reversing}",
                (household_id(), link["grocery_item_id"], *reversing_ids),
            ).fetchall()
            other_qtys = [row["quantity"] or "" for row in other_rows]
            summed = None if is_standing_want else _plan_or_rack_amount(
                grocery_row["item"], other_qtys, kept, grocery_row["short_amount"], grocery_row["edited_to"])
            if summed is not None:
                # Fully accounted for by the ledger (and any kept amount) —
                # recompute rather than subtract (see the docstring above).
                current_note = _quantities._quantity_note(grocery_row["quantity"] or "")
                new_qty = _quantities._with_note(summed, current_note) if summed else ""
                fully_removed = not other_qtys and not kept
            else:
                # A standing want, or a line the ledger can't fully account
                # for — the person's own amount is in this line and no
                # recompute can see it, so this contribution has to be
                # subtracted out of the current display instead.
                #
                # WHAT to subtract is the whole difficulty, and it is not
                # this meal's own share. The ingest adds ONE rounded week
                # total to the line, so taking back each meal's unrounded
                # share leaves a remainder that rounds straight back up and
                # the line ratchets a little higher every week for ever.
                # _ledger_totals + _restate_standing_want put the same
                # single rounding on both sides, in the line's own unit,
                # and restate the line in one step; either declines (None)
                # for the rows it can't read, and then the one contribution
                # in front of us is still the best available answer.
                totals = _quantities._ledger_totals(
                    other_qtys + [link["quantity"] or ""], other_qtys,
                )
                restated = (
                    None if totals is None
                    else _restate_standing_want(grocery_row["quantity"] or "", *totals)
                )
                if restated is None:
                    new_qty, fully_removed = _subtract_quantity(
                        grocery_row["quantity"] or "", link["quantity"] or "",
                    )
                else:
                    new_qty, fully_removed = restated
            if new_qty and not fully_removed:
                new_qty = _no_more_than(new_qty, grocery_row["quantity"] or "")
            if fully_removed:
                if is_standing_want:
                    if new_qty != (grocery_row["quantity"] or ""):
                        conn.execute(
                            "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                            (new_qty, grocery_row["id"], household_id()),
                        )
                        if not off_list:
                            trimmed_items.append(grocery_row["item"])
                elif not off_list:
                    conn.execute(
                        "DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
                        (grocery_row["id"], household_id()),
                    )
                    removed_items.append(grocery_row["item"])
            elif new_qty != (grocery_row["quantity"] or ""):
                conn.execute(
                    "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                    (new_qty, grocery_row["id"], household_id()),
                )
                if not off_list:  # not on the list: nothing to tell anyone
                    trimmed_items.append(grocery_row["item"])
    if not narrowed:
        conn.execute("DELETE FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id = ?", (household_id(), entry_id))
    elif reversing_ids:
        conn.execute(
            f"DELETE FROM meal_plan_grocery_links WHERE household_id = ? AND id IN {not_reversing}",
            (household_id(), *reversing_ids),
        )
    if own_conn:
        conn.commit()
        conn.close()
    return {"removed_items": removed_items, "trimmed_items": trimmed_items}


def _recompute_plan_line_from_ledger(
    item_id: int, conn=None, skip_link_ids: list[int] | None = None,
) -> None:
    """
    Put a plan-owned grocery line back to what its whole ledger adds up
    to — the recompute half of _reverse_meal_grocery_contributions, run
    after an ADD instead of after a removal.

    Every ingest pass rounds its own total before it reaches the list,
    so a second pass onto the same line — a meal swapped in after
    approval, a dinner planned in chat — adds a rounded amount to a
    rounded amount. For a counted pack that is a whole carton onto a
    whole carton: three eggs already on the line and three more arriving
    read "1 dozen" + "1 dozen" = "2 dozen" for six eggs. A count was
    once thought small enough to accept, and is not: one meal's 0.75
    onion went on as a whole one, so Swap then Undo read 21 onions over
    a ledger of exactly 20 (defect hunt 2026-10-21). The ledger holds
    the unrounded shares, so WeekGroceryBuffer.flush re-reads every line
    a share merged onto, exactly as a reversal would.

    Only a line the ledger fully describes (source_weekly_plan_id set)
    is touched; a person's standing want keeps whatever it reads. A
    ledger the recompute can't read (a freeform row) leaves the line
    alone too.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status, source_weekly_plan_id, kept_amount, short_amount, edited_to "
        "FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    # A line the household edited down keeps their number on the re-read
    # (_with_household_edit, Emily 2026-10-12).
    if not row or row["source_weekly_plan_id"] is None or row["status"] not in ("needed", "spice"):
        if own_conn:
            conn.close()
        return
    skip = set(skip_link_ids or ())
    qtys = [
        r["quantity"] or "" for r in conn.execute(
            "SELECT id, quantity FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ? ORDER BY id",
            (household_id(), item_id),
        ).fetchall()
        if r["id"] not in skip
    ]
    # A kept leftover on the line is part of what it says (see
    # keep_carried_over_item), and so is the household's edit down.
    summed = _plan_or_rack_amount(row["item"], qtys, row["kept_amount"], row["short_amount"], row["edited_to"])
    if summed:
        new_qty = _quantities._with_note(summed, _quantities._quantity_note(row["quantity"] or ""))
        if new_qty != (row["quantity"] or ""):
            conn.execute(
                "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                (new_qty, row["id"], household_id()),
            )
    if own_conn:
        conn.commit()
        conn.close()


def _restate_lines_for_new_shares(conn, before_by_line: dict[int, list[str]]) -> list[int]:
    """
    Re-read each of these lines from its ledger after some of its meals'
    shares were rewritten IN PLACE (attendance.resize_for_new_headcount:
    a table that changed size after approval). `before_by_line` is
    {grocery_item_id: every ledger quantity on that line BEFORE the
    rewrite}; the rows as they stand now are the after.

    The same two readings the meal reversal uses, so a line resized and
    resized back is where it started:
      - a plan line (source_weekly_plan_id set) is recomputed from its
        whole ledger (_plan_or_rack_amount — the kept amount and the
        household's edit down ride on top, as they do on a reversal);
      - a household's own standing want is RESTATED in one step
        (_ledger_totals + _restate_standing_want): what is on it, less
        what the plan's meals rounded to before, plus what they round to
        now. Never a share subtracted or added on its own (see CLAUDE.md,
        "Grocery quantity merging/display"), and never blanked here — a
        restate that would empty it, or that can't read it, leaves it.
    A sealed package keeps its one jar (a spice's or a store-sold line is
    re-read, as on a reversal). Only a line the reversal would keep in
    step is touched: needed / a pending spice, a plan line set aside
    ("Have it", the freezer step, a pre-shop drop), or one taken off with
    Remove. The status is NEVER changed — a line off the list stays off
    and its Put back / Undo returns the resized amount on the same row.
    A line in a cart or bought is the caller's to refuse before this.
    Returns the ids whose quantity changed.
    """
    changed: list[int] = []
    hid = household_id()
    for item_id, before in before_by_line.items():
        row = conn.execute(
            "SELECT id, item, quantity, status, removed_by, source_weekly_plan_id, kept_amount, "
            "short_amount, edited_to FROM grocery_items WHERE id = ? AND household_id = ?",
            (item_id, hid),
        ).fetchone()
        if row is None:
            continue
        plan_line = row["source_weekly_plan_id"] is not None
        live = row["status"] in ("needed", "spice")
        set_aside = (row["status"] == "removed" and plan_line
                     and row["removed_by"] not in ("carried_kept", "carried_dropped"))
        list_removed = row["status"] == "removed" and row["removed_by"] == REMOVED_BY_LIST
        if not (live or set_aside or list_removed):
            continue
        after = [r["quantity"] or "" for r in conn.execute(
            "SELECT quantity FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ? ORDER BY id",
            (hid, item_id),
        ).fetchall()]
        if not after:
            continue
        current = row["quantity"] or ""
        if any(_holds_one_package(row["item"], q) for q in after):
            if not (plan_line and (_spices_is_spice(row["item"]) or _store_kind(row["item"]))):
                continue
        if plan_line:
            summed = _plan_or_rack_amount(row["item"], after, row["kept_amount"], row["short_amount"], row["edited_to"])
            if not summed:
                continue
            new_qty = _quantities._with_note(summed, _quantities._quantity_note(current))
        else:
            totals = _quantities._ledger_totals(before, after)
            restated = None if totals is None else _restate_standing_want(current, *totals)
            if restated is None or restated[1] or not restated[0]:
                continue
            new_qty = restated[0]
        if new_qty != current:
            conn.execute(
                "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                (new_qty, item_id, hid),
            )
            changed.append(int(item_id))
    return changed


def add_grocery_item(
    item: str,
    quantity: str = "",
    category: str = "other",
    added_by: str = "user",
    source_weekly_plan_id: int | None = None,
    quantity_mode: str = "sum",
    conn=None,
    report_previous: bool = False,
) -> dict:
    """
    Add an item to the grocery list. If an item with the same name is
    already on the list (status 'needed'), the quantity is consolidated
    into that single line — e.g. "2 cups flour" + "1 cup flour" becomes
    "3 cups flour" — instead of creating a duplicate line. If the
    quantities can't be reconciled (different, incompatible units), both
    are kept together on the one line rather than silently guessing a
    conversion — EXCEPT across the line between a person's own want and a
    plan's: a hand-added "1" eggs and a recipe's "2 cups" stay two lines,
    the person's and the plan's, so each can be read, bought and cleared
    on its own (see _merge_target). category should be one of: produce,
    dairy, meat/seafood, pantry, frozen, other — pick the one that
    actually matches the item so the list stays organized by store
    section. Leave source_weekly_plan_id
    unset for anything a person asked for directly (or an ad hoc one-off
    meal) — it marks the item as a standing want that should never be
    auto-cleared. It's set automatically when ingredients come from a
    generated weekly plan (see plan_meal/generate_weekly_plan), so
    clear_stale_grocery_items can tell a current week's ingredients apart
    from an old week's leftovers.

    quantity_mode is for callers, not for the assistant: leave it at "sum"
    for anything a person asks for. "max" keeps the larger of the two
    quantities rather than adding them, which is how a sealed package a
    whole week draws on lands once instead of once per meal — see
    _greater_of_quantity and recipes._add_recipe_ingredients_for_entries.

    `conn` is for the grocery ingest and is not part of the assistant-facing
    API, the same arrangement _reverse_meal_grocery_contributions has:
    swap_meal_in_plan buys the new meal's ingredients inside ONE write
    transaction, and every line that ingest lands has to be written on that
    connection — a second connection writing while the first holds the
    write lock is the "database is locked" trap. Given a connection this
    reads and writes on it and neither commits nor closes; left unset, it
    opens its own, takes the write lock before the merge's read, and
    commits.

    `item` is trimmed and, once trimmed, must not be blank -- a stray tap
    or a mis-parsed scanned receipt line shouldn't be able to leave a
    blank row on the list -- and is capped at MAX_ITEM_NAME_LENGTH so one
    can't run unbounded either. Both checks live here rather than in the
    HTTP route so every caller gets them: the direct-add route, the chat
    tools, and the scan-review path all come through this one function.
    Raises ValueError on a blank name; the route layer turns that into a
    400 (see main.py's `except ValueError`). `quantity` gets the same
    trim and a shorter cap (MAX_QUANTITY_LENGTH) -- it has no "must not be
    blank" rule, since an item with no quantity at all is normal.
    """
    if not (item or "").strip():
        # Refused before any connection or lock (the same check runs below).
        raise ValueError("I need a name for that.")
    if conn is None:
        # The write lock from the first read (two phones, 2026-10-29): the
        # merge reads the list's 'needed' lines and then writes the summed
        # amount onto the one it matched. Without the lock the other
        # phone's tick could land in between — the add then summed onto a
        # line already BOUGHT, the shopper's kitchen got the old amount,
        # and the extra carton was on nobody's list (26 of 40 concurrent
        # add+tick pairs over HTTP). Under it, an add that loses the race
        # to a tick sees the line gone from 'needed' and starts its own.
        with write() as own:
            own.execute("BEGIN IMMEDIATE")
            return add_grocery_item(
                item, quantity=quantity, category=category, added_by=added_by,
                source_weekly_plan_id=source_weekly_plan_id, quantity_mode=quantity_mode,
                conn=own, report_previous=report_previous,
            )
    item = (item or "").strip()
    if not item:
        raise ValueError("I need a name for that.")
    if len(item) > MAX_ITEM_NAME_LENGTH:
        item = item[:MAX_ITEM_NAME_LENGTH].rstrip()
    quantity = (quantity or "").strip()
    if len(quantity) > MAX_QUANTITY_LENGTH:
        quantity = quantity[:MAX_QUANTITY_LENGTH].rstrip()
    if source_weekly_plan_id is None:
        # A person's "24 pack" is one pack of 24, not 24 packs.
        quantity = _quantities.typed_multipack(quantity)
    quantity = _quantities._normalize_grocery_quantity(quantity)
    # A recipe's "light soy sauce", "dark soy sauce" and "tamari" are one
    # bottle on the list (Emily, 2026-10-18) — renamed before the merge key
    # is taken, so they meet on one line. A person's own words are kept.
    # A tin goes in the canned aisle whoever added it: "canned is not a
    # staple", it is bought as normal, and the tins sit together.
    # Imported here: pantry.py imports this module for its singulariser.
    from . import pantry as _pantry
    if source_weekly_plan_id is not None:
        item = _pantry.canonical_name(item)
    if _pantry.is_canned(item):
        category = "canned"
    # Compared in Python rather than SQL because the comparison is a
    # normalised key, not a column value. The 'needed' list is a shopping
    # list -- tens of rows -- so reading it to find one match is cheaper
    # than it looks and far clearer than encoding the rule in SQL.
    # 'spice' as well as 'needed': a spice the week's recipes put in the
    # "Spices this week" section, still unticked (see spices.py). A second
    # recipe's cumin joins that line rather than starting another, and a
    # person asking for cumin ticks it — below.
    # Never a line set aside as "getting it somewhere else"
    # (excluded_from_list): that line is hidden from the list, so a new
    # amount merged onto it was hidden too and never bought (Loop Board,
    # 2026-10-07: a second turkey dinner added to the week vanished into
    # the turkey marked "somewhere else"). The new amount gets a visible
    # line of its own and the set-aside line stays exactly as it was —
    # the same rule consolidate_grocery_list keeps.
    candidates = conn.execute(
        "SELECT id, item, quantity, category, source_weekly_plan_id, status, added_by FROM grocery_items "
        "WHERE household_id = ? AND status IN ('needed', 'spice') AND excluded_from_list = 0 ORDER BY id",
        (household_id(),),
    ).fetchall()
    wanted = _merge_key(item)
    same_name = [r for r in candidates if _merge_key(r["item"]) == wanted]
    consolidate = _greater_of_quantity if quantity_mode == "max" else _try_consolidate_quantity
    target = _merge_target(
        same_name, quantity, consolidate, standing=source_weekly_plan_id is None,
        source_plan=source_weekly_plan_id,
    )
    # Matched on the same key the list itself merges on. Otherwise a
    # preference saved for "bell peppers" never applies to the line that
    # won the merge under the name "Bell pepper": the app confirms the
    # preference and it silently never takes effect.
    prefs = conn.execute(
        "SELECT item, store FROM item_store_preferences WHERE household_id = ?",
        (household_id(),),
    ).fetchall()
    pref = next((p for p in prefs if _merge_key(p["item"]) == _merge_key(item)), None)
    preferred_store = pref["store"] if pref else ""
    if target:
        existing, merged_qty, merged = target
        # A third amount joining a line the first two could not
        # reconcile still has to find its own unit; see
        # _refold_quantity_segments.
        merged_qty = _refold_quantity_segments(
            merged_qty,
            families=(
                source_weekly_plan_id is None and existing["source_weekly_plan_id"] is None
                and existing["status"] == "needed" and quantity_mode != "max"
            ),
        )
        # A row with no source_weekly_plan_id is something a person asked
        # for directly, and clear_stale_grocery_items is required to leave
        # those alone forever. Stamping this week's plan id onto it during
        # a merge would quietly convert a standing want into a line the
        # next generation deletes -- so a plan can add quantity to a
        # hand-added item, but it cannot take ownership of it.
        keep_standing = existing["source_weekly_plan_id"] is None
        # A plan's line takes the more specific of the two names when a
        # plan's add joins it ("Onions" + "Yellow onion" reads "Yellow
        # onion"); see _more_specific_name. A person's own line, or a
        # person's own add, keeps the wording already there -- that is the
        # line they typed, and the shop sheet's Put back restores amount
        # and store only, so a rename it caused could not be put back.
        line_name = existing["item"]
        if not keep_standing and source_weekly_plan_id is not None:
            line_name = _more_specific_name(existing["item"], item)
        # The aisle a line is already filed under stays: an add joining it
        # only fills one in where the line had none ('other'). The incoming
        # category is usually a guess — the shop sheet's groGuessCategory,
        # or chat's add_grocery_items default of 'other' for a bare name —
        # and overwriting with it moved a recipe's "Orzo · pantry" to Other
        # the moment someone typed "orzo", where Put back could not move it
        # home (tests/test_merge_keeps_the_aisle.py). Changing a line's
        # aisle on purpose is update_grocery_item's job, not a merge's.
        # "None" means what the list shows as Other: '', 'other', or a word
        # no section knows ("bakery" off a recipe) — get_grocery_list_by_
        # section's own fallback, so a line SHOWN under Other still takes
        # the real aisle an add brings.
        filed = _quantities._GROCERY_CATEGORY_ALIASES.get(existing["category"], existing["category"])
        if filed not in _quantities._GROCERY_SECTION_ORDER or filed == "other":
            filed = category
        else:
            filed = existing["category"]
        conn.execute(
            "UPDATE grocery_items SET item = ?, quantity = ?, category = ?, "
            "source_weekly_plan_id = CASE WHEN ? THEN NULL ELSE ? END, "
            # Fills in a store the row doesn't have yet, without
            # overwriting one already chosen for this line.
            "store = CASE WHEN store = '' THEN ? ELSE store END "
            "WHERE id = ? AND household_id = ?",
            (line_name, merged_qty, filed, 1 if keep_standing else 0, source_weekly_plan_id,
             preferred_store, existing["id"], household_id()),
        )
        # A person asking for a spice by name wants it bought: their add
        # ticks the pending line onto the list. A plan's add leaves it in
        # the section, unticked, whatever the quantity now reads.
        if existing["status"] == "spice" and source_weekly_plan_id is None:
            conn.execute(
                "UPDATE grocery_items SET status = 'needed' WHERE id = ? AND household_id = ?",
                (existing["id"], household_id()),
            )
        # A person's own amount joining a line a staple put there makes the
        # line theirs — add_regulars' rule. Left as the staple's, it kept
        # "Probably running low" and both buttons, and "We have plenty" took
        # the typed amount off with the staple's (2026-10-10: Coffee
        # "1 bag" + a typed "2 bags" read "3 bags", one tap left none).
        # staple_id stays, so buying it still teaches the rhythm.
        if (existing["added_by"] == "staple" and source_weekly_plan_id is None
                and (added_by or "") != "staple"):
            conn.execute(
                "UPDATE grocery_items SET added_by = ? WHERE id = ? AND household_id = ?",
                (acting_name(added_by) or "user", existing["id"], household_id()),
            )
        item_id = existing["id"]
        # The name on the line now, not the one just asked for: saying
        # "item" back means the line the shopper will actually see.
        item_name = line_name
        result = {"item_id": item_id, "item": item_name, "quantity": merged_qty, "merged": True, "units_reconciled": merged}
        if report_previous:
            # What the line read before this add, and whether it was the
            # household's own: WeekGroceryBuffer.flush restates a standing
            # want from them (_restate_standing_want_after_add). Asked for,
            # not always said: this dict is what the add route and chat see.
            result.update(previous_quantity=existing["quantity"] or "", standing=keep_standing,
                          status=existing["status"])
        return result

    # Who added it: the adult picked on this device, when the caller did
    # not say (the shell sends nothing; the offline queue replays the same
    # body). "ai" and an explicit name are kept — see _shared.acting_name.
    # Never empty: a blank added_by is what Shop reads as the staple's own
    # line (the merge branch above has the same "or 'user'").
    # A plan's spice starts in the "Spices this week" section, unticked —
    # the list assumes a spice rack (Emily, 2026-09-13; see spices.py) —
    # and so does a plan's sauce or pantry staple, in "Sauces this week" /
    # "Pantry staples this week" (pantry.py, 2026-10-18): the same status,
    # the same tick. A person's own add is a thing to buy, whatever it is.
    # Imported here, not at the top: spices.py imports this module for
    # the merge key.
    from . import spices as _spices
    status = "spice" if source_weekly_plan_id is not None and _spices.is_assumed(item) else "needed"
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, store, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (household_id(), item, quantity, category, acting_name(added_by) or "user", source_weekly_plan_id, preferred_store, status),
    )
    item_id = cur.lastrowid
    return {"item_id": item_id, "item": item, "quantity": quantity, "merged": False, "units_reconciled": True}


def add_grocery_items(items: list, category: str = "other", added_by: str = "user") -> dict:
    """
    Add several items to the grocery list at once. Each entry can be a
    plain string (e.g. "milk") or, when you know more, a dict like
    {"item": "flour", "quantity": "2 cups", "category": "pantry"} — mix
    and match as needed. Prefer setting an accurate category per item
    (produce, dairy, meat/seafood, pantry, frozen, other) so the list
    stays organized by store section; the category argument is only a
    fallback for entries you didn't categorize individually. Quantities
    are consolidated with any matching item already on the list rather
    than creating duplicate lines (see add_grocery_item).
    """
    added, merged = [], []
    for raw in items:
        if isinstance(raw, dict):
            name = (raw.get("item") or "").strip()
            qty = raw.get("quantity", "")
            cat = raw.get("category") or category
        else:
            name = (raw or "").strip()
            qty = ""
            cat = category
        if not name:
            continue
        result = add_grocery_item(name, quantity=qty, category=cat, added_by=added_by)
        (merged if result["merged"] else added).append(name)
    return {"added": added, "merged_with_existing": merged}


def list_built_at() -> str:
    """
    When the household's list was last BUILT, as the UTC timestamp the
    grocery_items stamps compare against: the most recent FIRST approval
    of a week (weekly_plans.list_built_at — approving is what writes the
    list, see approve_weekly_plan), or seven days ago for a household that has
    never approved one and shops from a hand-made list.

    This is the window the Shop tab's checklist reads its BOUGHT rows in
    (list_grocery_list(status='bought'), 2026-09-18). A purchased row is
    kept for life — staples learn their rhythm from those rows and nothing
    ever resets one — so "what did we buy off THIS list" cannot be read
    off the status alone; it is the rows ticked since the list was last
    built. The next approval moves the window, and last week's ticks stop
    being drawn as this week's.

    A RE-approval is not a new list (2026-10-21, defect hunt): reopening a
    week clears its approved_at and approving it again re-stamps it, so
    Tuesday's ticks vanished from the checklist when Thursday's dinner was
    changed on Wednesday — under a toast saying "Nothing has come off your
    shopping list" — and while it sat reopened, last week's ticks came
    back instead. A plan's first approval (weekly_plans.list_built_at,
    never cleared) is when its list was built; approved_at stands in for a
    plan approved before that column existed.

    And the END of the last week with a list, when no approval follows it.
    Monday walk, 2026-10-26: a household with this week still a draft (or
    nothing planned) opened Shop to "11 of 17", last Monday's shop struck
    through as if it were this list's. Once the LAST day any non-retired
    plan with a built list covers has gone by on the household's clock, the
    window starts no earlier than the household's midnight after it. The
    latest END, not the latest approval's: re-approving last week on Sunday
    after next week was approved on Saturday must not end the window on
    last week's Sunday and hide next week's Sunday shop (review, 2026-10-26).
    """
    from datetime import datetime, time, timedelta, timezone
    from . import cooker as _cooker  # lazy: cooker imports this module

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT COALESCE(list_built_at, approved_at) AS built, status, week_start_date, "
            "content_start_date, day_count FROM weekly_plans "
            "WHERE household_id = ? AND COALESCE(list_built_at, approved_at) IS NOT NULL",
            (household_id(),),
        ).fetchall()
        fallback = conn.execute("SELECT datetime('now', '-7 days') AS at").fetchone()["at"]
        if not rows:
            return fallback
        at = max(r["built"] for r in rows)
        ends = []
        for r in rows:
            if r["status"] == "retired":
                continue
            start, days = _weekly_plan.plan_period(r)
            if days > 0:
                ends.append(_weekly_plan.period_end_date(start, days))
        end = max(ends) if ends else None
        if end and end < _household_today(conn=conn).isoformat():
            turned = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time(0, 0))
            turned = turned.replace(tzinfo=_cooker.household_zone(conn=conn)).astimezone(timezone.utc)
            at = max(at, turned.strftime("%Y-%m-%d %H:%M:%S"))
        return at
    finally:
        conn.close()


def list_grocery_list(status: str = "needed") -> list[dict]:
    """
    List grocery items, optionally filtered by status: 'needed', 'in_cart',
    'purchased', 'all' (every status, including excluded items), or
    'excluded' (only items hidden via exclude_grocery_item). Two more
    statuses exist and are never "to buy": 'spice' (a recipe's spice
    waiting unticked in the "Spices this week" section — see spices.py)
    and 'carried' (last week's leftover waiting for keep-or-drop). For 'needed'/
    'in_cart'/'purchased', items excluded from the list (see
    exclude_grocery_item) are left out automatically — they're still
    tracked, just not shown on the normal shopping list.

    'bought' (2026-09-18, the list is the checklist) is the rows ticked off
    THIS list: anything in a trolley (in_cart, a status nothing writes any
    more but an older build may have left), plus the purchased rows whose
    tick (inventory_added_at, stamped by mark_grocery_item) is since the
    list was last built — see list_built_at. 'purchased' alone is the
    household's whole buying history and is what the staples read.
    """
    conn = get_conn()
    if status == "bought":
        since = list_built_at()
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, store_decided, excluded_from_list, already_have_reviewed, added_by, staple_id, source_weekly_plan_id FROM grocery_items "
            "WHERE household_id = ? AND excluded_from_list = 0 AND (status = 'in_cart' "
            "OR (status = 'purchased' AND inventory_added_at IS NOT NULL AND inventory_added_at >= ?)) "
            "ORDER BY category, item",
            (household_id(), since),
        ).fetchall()
    elif status == "excluded":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, store_decided, excluded_from_list, already_have_reviewed, added_by, staple_id, source_weekly_plan_id FROM grocery_items "
            "WHERE household_id = ? AND excluded_from_list = 1 ORDER BY category, item",
            (household_id(),),
        ).fetchall()
    elif status == "all":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, store_decided, excluded_from_list, already_have_reviewed, added_by, staple_id, source_weekly_plan_id FROM grocery_items "
            "WHERE household_id = ? ORDER BY category, item",
            (household_id(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, store_decided, excluded_from_list, already_have_reviewed, added_by, staple_id, source_weekly_plan_id FROM grocery_items "
            "WHERE household_id = ? AND status = ? AND excluded_from_list = 0 ORDER BY category, item",
            (household_id(), status),
        ).fetchall()
    conn.close()
    # Said the way a person reads it (quantities.tidy_display_quantity):
    # whole lemons, no eighths, one unit per item. Display only - nothing
    # here is written back.
    out = []
    for r in rows:
        d = dict(r)
        d["quantity"] = _quantities.tidy_display_quantity(d.get("item") or "", d.get("quantity") or "") or d.get("quantity")
        out.append(d)
    return out


# "Now in the kitchen" (Inventory A1, 2026-10-27): the shop's wrap-up says
# out loud what the ticks already did. Places in the order the card reads;
# a row kept anywhere else (a stated shelf nobody planned for) is counted
# under its own name after these.
KITCHEN_PLACES = ("fridge", "freezer", "pantry")
# "Use these first" looks this far ahead. Without a horizon the three
# soonest are always three, and a bag of rice good till spring is not a
# thing to use first.
USE_FIRST_WITHIN_DAYS = 7
USE_FIRST_MAX = 3


def _use_by_words(expires: date, today: date) -> str:
    gap = (expires - today).days
    if gap <= 0:
        return "today"
    if gap == 1:
        return "by tomorrow"
    return "by " + expires.strftime("%A")


def _meal_words(slot: str, on: date, today: date) -> str:
    gap = (on - today).days
    if slot == "dinner" and gap == 0:
        return "in tonight’s dinner"
    # A week out is today's weekday again: "next" says which one.
    day = ("today’s" if gap == 0 else "tomorrow’s" if gap == 1
           else ("next " if gap >= 7 else "") + on.strftime("%A") + "’s")
    return f"in {day} {slot}"


def kitchen_from_this_shop() -> dict:
    """
    What this shop's ticks put in the kitchen: the rows ticked off THIS list
    (the same 'bought' window list_grocery_list draws struck through) whose
    tick wrote an inventory row, counted once per kitchen row by where it is
    kept now — plus up to three of them that go off soonest, each with what
    it is for.

    Read straight off the tick's own receipt (inventory_receipt_json), so it
    needs nothing typed and agrees with the untick: an untick that takes a
    line back out clears inventory_added_at, which takes it out of this
    window, and a kitchen row deleted since is simply not found. Never
    writes. {"total": 0} means there is no card to show.
    """
    since = list_built_at()
    conn = get_conn()
    try:
        lines = conn.execute(
            "SELECT id, inventory_receipt_json FROM grocery_items "
            "WHERE household_id = ? AND excluded_from_list = 0 AND status = 'purchased' "
            "AND inventory_added_at IS NOT NULL AND inventory_added_at >= ? "
            "AND inventory_receipt_json IS NOT NULL",
            (household_id(), since),
        ).fetchall()
        line_ids_by_row: dict[int, list[int]] = {}
        for line in lines:
            try:
                inventory_id = int(json.loads(line["inventory_receipt_json"]).get("inventory_id"))
            except (TypeError, ValueError, AttributeError):
                continue
            line_ids_by_row.setdefault(inventory_id, []).append(line["id"])
        rows = []
        if line_ids_by_row:
            marks = ",".join("?" * len(line_ids_by_row))
            rows = [dict(r) for r in conn.execute(
                f"SELECT id, item, category, location, expiration_date FROM inventory_items "
                f"WHERE household_id = ? AND id IN ({marks})",
                (household_id(), *line_ids_by_row),
            ).fetchall()]
        # What the Kitchen tab shows, and nothing else (defect hunt
        # 2026-10-28): a staple or a spice is kept by Staples and never
        # drawn there (whats_home._shown_rows, the same two tests), so the
        # card read "Pantry · 1 thing" for the coffee and "See what's home"
        # opened on "Pantry · 0". Ticking coffee is what makes it a staple.
        from . import spices as _spices  # lazy: both import this module
        from . import use_up as _use_up

        staples = _use_up._staple_keys(conn)
        rows = [r for r in rows if (r["item"] or "").strip()
                and not _use_up.is_staple(r["item"].strip(), staples) and not _spices.is_spice(r["item"].strip())]
        today = _household_today(conn=conn)
        counts: dict[str, int] = {}
        for r in rows:
            place = (_quantities._display_location(r) or "pantry").strip().lower()
            counts[place] = counts.get(place, 0) + 1
        places = [p for p in KITCHEN_PLACES if counts.get(p)] + sorted(p for p in counts if p not in KITCHEN_PLACES)

        soon = []
        for r in rows:
            if (_quantities._display_location(r) or "").strip().lower() == "freezer":
                continue
            try:
                expires = date.fromisoformat((r.get("expiration_date") or "")[:10])
            except ValueError:
                continue
            # Already past its date: "use first" would be the wrong advice,
            # and since this card stays up until the next approval it would
            # say "today" about it every day for a week.
            if 0 <= (expires - today).days <= USE_FIRST_WITHIN_DAYS:
                soon.append((expires, r["item"].lower(), r))
        soon.sort(key=lambda t: (t[0], t[1]))

        use_first = []
        for expires, _, r in soon[:USE_FIRST_MAX]:
            line_ids = line_ids_by_row[r["id"]]
            marks = ",".join("?" * len(line_ids))
            # The earliest meal still ahead that this line was bought for,
            # read off the per-meal ledger — never a name match — and only
            # one on or before the food's own date (defect hunt 2026-10-28):
            # Sunday's shop for Saturday's chicken read "Chicken thighs · in
            # Saturday's dinner" on a pack good till Tuesday, which is advice
            # to let it go off; and a bare weekday a week out read as this
            # week's. Past its date, the date is the plan.
            meal = conn.execute(
                "SELECT e.date, e.slot, e.component_category FROM meal_plan_grocery_links l "
                "JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
                "LEFT JOIN weekly_plans w ON w.id = e.weekly_plan_id "
                f"WHERE l.household_id = ? AND l.grocery_item_id IN ({marks}) "
                "AND e.slot_state = 'planned' AND e.cooked_status = 'pending' AND e.date >= ? "
                "AND e.date <= ? AND (w.id IS NULL OR w.status != 'retired') "
                "ORDER BY e.date, CASE e.slot WHEN 'breakfast' THEN 0 WHEN 'lunch' THEN 1 "
                "WHEN 'snack' THEN 2 ELSE 3 END LIMIT 1",
                (household_id(), *line_ids, today.isoformat(), expires.isoformat()),
            ).fetchone()
            use_by = _use_by_words(expires, today)
            if meal is None:
                plan = use_by
            elif meal["slot"] == "snack" or meal["component_category"] in ("snack", "treat", "dip"):
                plan = "snacks, " + use_by
            elif meal["component_category"]:
                plan = use_by
            else:
                try:
                    plan = _meal_words(meal["slot"], date.fromisoformat(meal["date"][:10]), today)
                except ValueError:
                    plan = use_by
            use_first.append({"item": r["item"], "expires": expires.isoformat(), "plan": plan})
    finally:
        conn.close()
    return {
        "total": len(rows),
        "places": [{"place": p, "count": counts[p]} for p in places],
        "use_first": use_first,
    }


def entry_ids_awaiting_a_shop() -> set[int]:
    """
    Every meal_plan_entries row that still has something to buy for it —
    read off the per-meal ledger (meal_plan_grocery_links), the same record
    the reversal above re-derives a line from.

    This is how Today answers "is the list actually for tonight?" without
    matching ingredient names, which would be a second answer to a question
    the ledger already answers exactly (see moves._shop_move).

    Two things about the rule, both deliberate:

    - 'needed' only, matching what the list itself calls "to buy". A line
      already in the trolley or bought is not a reason to tell anybody to
      go shopping, and an excluded line is off the list by the household's
      own say-so.
    - NOT gated on source_weekly_plan_id. A hand-added standing want keeps
      that NULL even after a plan's amount merges into it (add_grocery_item's
      keep_standing), so reading it as "this line belongs to nobody's meal"
      would miss exactly the lines a meal really is waiting on. A line no
      meal ever contributed to has no row here at all, which is the same
      answer by a route that cannot be wrong.

    WHAT THIS CANNOT SEE, named so nobody reports it as new. A meal with no
    ledger row cannot claim a deadline, however much the household has just
    bought for it by hand:

    - An ingredient the kitchen check skipped at ingest (recipes._KitchenStock
      decided there was enough at home) leaves that meal no row. If the
      household then finds there isn't enough and hand-adds it, that line is
      exactly what tonight is waiting on and Now will not put a clock on it.
      Routine rather than exotic — every ticked purchase writes an inventory
      row, so ingest skips are ordinary.
    - A freeform meal, and a chat-planned one where the household declined
      "shall I add the ingredients?" and added them by hand, by voice or by
      photo-scan, have no ingredients to have made rows.

    Both are the QUIET direction, which is this card's own stated
    preference — before this, the move claimed a deadline unconditionally
    and was right in these cases only by accident. Closing them means
    answering "is this line for that meal?" for a line no meal ever
    recorded, which is name-matching by another name; its own card.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT l.meal_plan_entry_id AS entry_id "
        "FROM meal_plan_grocery_links l JOIN grocery_items g ON g.id = l.grocery_item_id "
        "WHERE l.household_id = ? AND g.household_id = ? "
        "  AND g.status = 'needed' AND g.excluded_from_list = 0",
        (household_id(), household_id()),
    ).fetchall()
    conn.close()
    return {r["entry_id"] for r in rows}


def needed_item_ids_for_entries(entry_ids) -> set[int]:
    """
    The needed lines these meal_plan_entries rows put on the list — the
    other half of entry_ids_awaiting_a_shop above, read off the same ledger
    by the same rule ('needed', not set aside). It is what lets Today's
    "Shop for tonight" NAME tonight's food rather than whatever the list
    happens to sort first (moves._shop_move).
    """
    ids = [int(i) for i in entry_ids if i]
    if not ids:
        return set()
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT l.grocery_item_id AS item_id "
        "FROM meal_plan_grocery_links l JOIN grocery_items g ON g.id = l.grocery_item_id "
        "WHERE l.household_id = ? AND g.household_id = ? "
        "  AND g.status = 'needed' AND g.excluded_from_list = 0 "
        f"  AND l.meal_plan_entry_id IN ({','.join('?' * len(ids))})",
        (household_id(), household_id(), *ids),
    ).fetchall()
    conn.close()
    return {r["item_id"] for r in rows}


def exclude_grocery_item(item_id: int) -> dict:
    """
    Hide an item from the normal shown/shopped grocery list without
    deleting it — for something the Shopper will get elsewhere (a butcher,
    a farmers market) rather than on the regular trip. It stays tracked:
    still in grocery_items with its status unchanged — only its visibility
    in the default 'needed'/'in_cart'/'purchased' views changes. A later
    add_grocery_item for the same item does NOT merge onto it (that hid
    the new amount too, 2026-10-07): it lands on a visible line of its
    own, and this line stays as it was. See
    include_grocery_item to undo, and list_grocery_list(status='excluded')
    to see what's currently hidden this way.
    """
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    conn.execute(
        "UPDATE grocery_items SET excluded_from_list = 1 WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "excluded_from_list": True}


def include_grocery_item(item_id: int) -> dict:
    """Undo exclude_grocery_item — put an item back on the normal shown/shopped grocery list."""
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    conn.execute(
        "UPDATE grocery_items SET excluded_from_list = 0 WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "excluded_from_list": False}


def get_grocery_list_by_section(status: str = "needed") -> dict:
    """
    Get the grocery list grouped into standard store sections (produce,
    dairy, meat/seafood, pantry, frozen, other) in a sensible shopping
    order, rather than a flat list. Use this whenever showing or reviewing
    the grocery list to the user so it reads like something they can
    actually shop from, aisle by aisle, instead of a flat ingredient dump.
    Items hidden via exclude_grocery_item are left out automatically (see
    list_grocery_list) unless status='excluded' or 'all' is passed.
    """
    if status == "needed":
        # A staple that is probably due goes on the list the moment the
        # list is read — the one place the household is already looking.
        # Idempotent, and a no-op for a household with no staples.
        from . import staples as _staples
        _staples.sync_due_staples()
    items = list_grocery_list(status=status)
    sections: dict[str, list[dict]] = {s: [] for s in _quantities._GROCERY_SECTION_ORDER}
    for it in items:
        cat = _quantities._GROCERY_CATEGORY_ALIASES.get(it["category"], it["category"])
        sections.setdefault("other", [])
        sections[cat if cat in sections else "other"].append(it)
    return {"sections": [{"section": s, "items": sections[s]} for s in _quantities._GROCERY_SECTION_ORDER if sections[s]]}


def _consolidation_plan(rows) -> list[dict]:
    """
    Which lines on the list would fold into which, by consolidate's rule —
    worked out without writing anything, so the Shop tab's "Two rows of
    eggs · Merge" can offer exactly the merges that would happen and no
    others (consolidate_grocery_list below, and mergeable_duplicate_groups).

    Returns one keeper per surviving line: the row plus `merged_qty`,
    `keep_name` and `absorbed_ids` (the lines it would take in).
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_merge_key(r["item"]), []).append(dict(r))

    out: list[dict] = []
    for entries in groups.values():
        # Each line folds into the first earlier line it may join, by the
        # same rule add_grocery_item's merge follows (_merge_target): two
        # DIFFERENT plans' amount lines never fold together (QA walk
        # 2026-10-02 — with next week approved while this week still runs,
        # both weeks' turkey is on the list on purpose, and folding them
        # put next week's amount on tonight's line and deleted next week's
        # line with its ledger). A person's line may still take a plan's.
        keepers: list[dict] = []
        for entry in entries:
            plan = entry["source_weekly_plan_id"]
            target = None
            for k in keepers:
                # The plans already in this keeper's group, not just its own:
                # a household's line (no plan) that took this week's eggs
                # must not then take next week's too — two weeks folded
                # through a third line is still two weeks folded.
                if plan is not None and any(p is not None and p != plan for p in k["plans"]):
                    continue
                # Two lines the list keeps apart on purpose — a person's "1"
                # beside a plan's "2 cups" (see _merge_target) — stay apart
                # here too, rather than being glued into the "1 + 2 cups"
                # nothing can take back apart.
                candidate, reconciled = _try_consolidate_quantity(k["merged_qty"], entry["quantity"] or "")
                if reconciled:
                    target = (k, candidate)
                    break
            if target is None:
                keepers.append(dict(entry, merged_qty=entry["quantity"] or "", keep_name=entry["item"],
                                    absorbed_ids=[], plans={plan}, standing=plan is None,
                                    staple=entry.get("staple_id")))
                continue
            k, candidate = target
            k["merged_qty"] = candidate
            # The variety's name on a plan's line; a person's typed line
            # keeps its wording, as it does in add_grocery_item.
            if k["source_weekly_plan_id"] is not None:
                k["keep_name"] = _more_specific_name(k["keep_name"], entry["item"])
            k["absorbed_ids"].append(entry["id"])
            k["plans"].add(plan)
            # A household's own amount inside the line makes the whole line
            # theirs, as add_grocery_item's keep_standing does: a plan-owned
            # line is deleted when its meals go, and would take their
            # "2 bunches" with it. Standing, the restate path takes only the
            # meals' share back off (_restate_standing_want).
            if plan is None:
                k["standing"] = True
            if not k["staple"] and entry.get("staple_id"):
                k["staple"] = entry["staple_id"]
        out.extend(keepers)
    return out


def _consolidation_rows(conn, status: str, only_ids=None):
    # excluded_from_list rows are hidden from the list on purpose ("we get
    # those at the market"). Folding a visible line into a hidden one --
    # which happened whenever the hidden row had the lower id -- made the
    # visible line disappear and parked its quantity somewhere nobody can
    # see. They are left out of consolidation entirely instead.
    rows = conn.execute(
        "SELECT id, item, quantity, category, source_weekly_plan_id, staple_id FROM grocery_items "
        "WHERE household_id = ? AND status = ? AND excluded_from_list = 0 ORDER BY id",
        (household_id(), status),
    ).fetchall()
    if only_ids is not None:
        wanted = {int(i) for i in only_ids}
        rows = [r for r in rows if r["id"] in wanted]
    return rows


def mergeable_duplicate_groups() -> list[list[int]]:
    """
    The Shop tab's "Two rows of X · Merge" groups: each is a line and the
    lines consolidate would fold into it, keeper first. Lines from two
    different weeks' plans are never a group (they are kept apart on
    purpose — see _merge_target), and neither are two lines whose amounts
    can't be added ("1" beside "2 cups"): a Merge there could only keep one
    amount and lose the other, which is the bug this replaced (Loop Board,
    defect hunt 2026-10-07 — Merge on this week's 1 dozen eggs and next
    week's 2 dozen left "1 dozen").
    """
    conn = get_conn()
    rows = _consolidation_rows(conn, "needed")
    conn.close()
    return [[k["id"]] + k["absorbed_ids"] for k in _consolidation_plan(rows) if k["absorbed_ids"]]


def consolidate_grocery_list(status: str = "needed", only_ids: list[int] | None = None) -> dict:
    """
    Merge any duplicate lines already on the list (the same item name
    ignoring case and singular/plural) into one line each, combining
    quantities with the
    same logic add_grocery_item uses automatically for new additions.
    Two weeks' plans each keep their OWN line for the same item while both
    are live (e.g. this week's turkey and next week's) — that is not a
    duplicate, and this leaves those two apart on purpose.
    Call this if the user asks to clean up/consolidate the list, or if you
    notice the same item appears more than once — items added since
    consolidation shipped shouldn't duplicate going forward, but this
    cleans up anything added before that, or any way it happens to slip
    through.

    only_ids limits it to those lines (the Shop tab's Merge on one "Two
    rows of" group). A line folded away hands its meal ledger rows to the
    line that took its amount, so dropping or swapping one of those meals
    later still takes its share back off the list.
    """
    conn = get_conn()
    rows = _consolidation_rows(conn, status, only_ids)
    merged_count = 0
    for k in _consolidation_plan(rows):
        if not k["absorbed_ids"]:
            continue
        for gone in k["absorbed_ids"]:
            # Before the delete, whose ON DELETE CASCADE would otherwise
            # drop the meal's share with the line (schema.sql).
            conn.execute(
                "UPDATE meal_plan_grocery_links SET grocery_item_id = ? "
                "WHERE grocery_item_id = ? AND household_id = ?",
                (k["id"], gone, household_id()),
            )
            conn.execute(
                "DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
                (gone, household_id()),
            )
            merged_count += 1
        conn.execute(
            # The staple's link goes with its amount, or Shop's Remove on
            # the merged line can't say "not this trip" and the next read
            # puts the staple straight back (staples.note_line_removed).
            "UPDATE grocery_items SET item = ?, quantity = ?, "
            "source_weekly_plan_id = CASE WHEN ? THEN NULL ELSE source_weekly_plan_id END, "
            "staple_id = COALESCE(staple_id, ?) WHERE id = ? AND household_id = ?",
            (k["keep_name"], k["merged_qty"], 1 if k["standing"] else 0, k["staple"],
             k["id"], household_id()),
        )
    conn.commit()
    conn.close()
    return {"lines_merged_away": merged_count}


def repair_grocery_quantities(status: str = "needed") -> dict:
    """
    One-time cleanup for grocery lines whose quantity got stuck as an
    ugly, concatenated "+"-joined string from the prep-descriptor
    consolidation bug (see _strip_prep_descriptor) — e.g. "3, diced + 1,
    diced + 1, diced" instead of a clean "5", or "4.75 cups, sliced + 1/4
    cup, sliced" instead of "5 cups". Re-parses each "+"-joined segment
    (stripping any prep descriptor first) and re-sums same-unit segments
    into one clean total, using the same logic add_grocery_item now uses
    automatically for new additions. A segment that still can't be
    reconciled (mixed incompatible units, or genuinely non-numeric text
    like "a bunch") is left joined with " + " exactly as that same fallback
    would produce today — so this is safe to run more than once. The
    underlying bug is fixed at the source now (see _strip_prep_descriptor),
    so this is purely for repairing lines that already got mangled before
    that fix existed; it isn't something that needs to run automatically
    going forward.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity FROM grocery_items WHERE household_id = ? AND status = ?",
        (household_id(), status),
    ).fetchall()
    fixed = []
    for r in rows:
        qty = r["quantity"] or ""
        if " + " not in qty and "," not in qty:
            continue  # nothing to clean on this line
        cleaned = _refold_quantity_segments(qty)
        if " + " not in qty:
            # A single segment still wants its prep descriptor folded out,
            # which is the other half of what this repair is for.
            cleaned, _ = _try_consolidate_quantity("", qty)
        if cleaned != qty:
            conn.execute(
                "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                (cleaned, r["id"], household_id()),
            )
            fixed.append({"item": r["item"], "before": qty, "after": cleaned})
    conn.commit()
    conn.close()
    return {"fixed_count": len(fixed), "fixed": fixed}


def _live_plan_ids(current_id: int | None) -> list[int]:
    """
    The plans whose ingredients are not stale: the one being generated, plus
    every non-retired plan still holding a day from today onward. Empty only
    when the household has no plan at all, which is the one case that falls
    back to the pre-period query below unchanged.

    "From today onward" is the household's day, for the reason
    _household_today gives: period_end_date is household-relative and the
    server's date is not. It moved in the same change as
    set_aside_carried_over_items rather than after it, because a module
    half on one clock is a new bug rather than a smaller one — and this is
    the reader with teeth. The caller is a blunt DELETE with no ledger
    behind it, so on the server's clock a plan whose LAST day is the
    household's today read as finished from about 8pm local, and the
    ingredients for the dinner they were still cooking went off the list.
    """
    conn = get_conn()
    today = _household_today(conn=conn).isoformat()
    rows = conn.execute(
        "SELECT id, week_start_date, content_start_date, day_count, status "
        "FROM weekly_plans WHERE household_id = ?",
        (household_id(),),
    ).fetchall()
    conn.close()
    live = {current_id} if current_id is not None else set()
    for row in rows:
        if row["status"] == "retired":
            continue
        start, days = _weekly_plan.plan_period(row)
        if days > 0 and _weekly_plan.period_end_date(start, days) >= today:
            live.add(row["id"])
    return sorted(live)


def clear_stale_grocery_items(current_weekly_plan_id: int | None = None) -> dict:
    """
    Remove 'needed' grocery items that came from an OLDER generated weekly
    plan — not the current one — and were never marked purchased. This is
    the fix for quantities silently stacking up across several weeks'
    plans onto the same line (e.g. "9 lbs chicken breast" built from 4
    different weeks). Items a person added directly, or that came from an
    ad hoc one-off meal rather than a generated week, are never touched —
    those represent a standing want, not a stale one. Pass
    current_weekly_plan_id explicitly when you already know it (e.g. right
    after creating a new plan); otherwise it falls back to whichever plan
    get_weekly_plan considers most recent. Called automatically at the
    start of every generate_weekly_plan; also fine to call directly if the
    user notices buildup and asks to clean it up.

    "Older" means a plan that no longer holds any day from today onward —
    not merely "not the current one". Before Loop Board "Planning periods,
    not weeks" those were the same sentence, because generating a plan
    replaced whatever was there. They are not the same now: a period
    starting Thursday leaves the previous plan alive with Monday to
    Wednesday still on it, and treating that live sibling as stale would
    delete the ingredients for three days the household is about to cook.
    Plans whose days have genuinely gone by are still cleared, which is the
    quantity-stacking bug this function exists to fix ("9 lbs chicken
    breast built from 4 different weeks"), and so are retired ones.

    Note what this does NOT do to the overlap it leaves behind: the days a
    new period takes over are reconciled by retire_overlapping_plans, which
    runs later in the same generation and reverses per meal — so an item
    already bought is spared. Doing it here instead would have deleted it,
    because this is a blunt DELETE with no ledger behind it.
    """
    current_id = current_weekly_plan_id
    if current_id is None:
        current_id = _weekly_plan.get_weekly_plan().get("weekly_plan_id")
    live_ids = _live_plan_ids(current_id)
    conn = get_conn()
    # 'carried' as well as 'needed': a line set aside by a newer week's
    # approval (set_aside_carried_over_items) and never answered is the
    # same stale leftover this function exists to clear, once the plan it
    # came from has genuinely gone by. 'spice' too — an unticked spice
    # from a week that is over was never wanted.
    if live_ids:
        keep = ",".join("?" * len(live_ids))
        rows = conn.execute(
            f"SELECT id, item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'carried', 'spice') "
            f"AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id NOT IN ({keep})",
            (household_id(), *live_ids),
        ).fetchall()
    elif current_id is None:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'carried', 'spice') "
            "AND source_weekly_plan_id IS NOT NULL",
            (household_id(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'carried', 'spice') "
            "AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ?",
            (household_id(), current_id),
        ).fetchall()
    removed = [r["item"] for r in rows]
    if rows:
        conn.executemany(
            "DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
            [(r["id"], household_id()) for r in rows],
        )
        conn.commit()
    conn.close()
    return {"removed_count": len(removed), "removed_items": removed}


# ---------- Last week's leftovers ----------
# Loop Board, 2026-09-13 (Emily: "some of the quantities are so high but I
# think it might be because it was adding on from last week's"). Approving
# a new week used to pour its recipes straight onto whatever was still
# unbought from the last one: add_grocery_item found the old "2 lbs chicken
# thighs", summed the new week's 2 lbs into it and stamped the new plan's
# id on the row — so the list said 4 lbs, nothing on screen said why, and
# clear_stale_grocery_items could never take the old share back off,
# because the row now belonged to the new week. It happened every time a
# week was approved while the previous one still had a day left (any
# Sunday), since _live_plan_ids keeps a plan alive through its last day.
#
# Now the old lines are SET ASIDE first (status 'carried'), so the new
# week's ingest writes clean lines of its own, and the Shop tab asks about
# the leftovers one screen before sorting: "Still on the list from last
# week — keep or drop?" Keep adds the old amount onto this week's line
# (or restores the line on its own); Don't need takes it off. Both undo.

_CARRIED_KEPT = "carried_kept"
_CARRIED_DROPPED = "carried_dropped"


def _other_plans_by_stage(conn, weekly_plan_id: int) -> dict[str, set]:
    """
    Every plan other than `weekly_plan_id`, split by where the household's
    today sits in its period: 'ended' (its last day is before today — a
    fully surrendered plan reads as ended from its start, see
    period_end_date) and 'running' (today is one of its days). A plan that
    has not begun is in neither. Read on `conn` for the reason
    set_aside_carried_over_items gives.
    """
    today = _household_today(conn=conn).isoformat()
    stages = {"ended": set(), "running": set()}
    for plan in conn.execute(
        "SELECT id, week_start_date, content_start_date, day_count, status FROM weekly_plans "
        "WHERE household_id = ? AND id != ?",
        (household_id(), weekly_plan_id),
    ).fetchall():
        start, days = _weekly_plan.plan_period(plan)
        if start > today:
            continue
        if _weekly_plan.period_end_date(start, days) < today:
            stages["ended"].add(plan["id"])
        else:
            stages["running"].add(plan["id"])
    return stages


def set_aside_carried_over_items(weekly_plan_id: int, conn=None) -> list[dict]:
    """
    Move every still-'needed' line that came from an EARLIER plan's
    recipes out of the way of `weekly_plan_id`'s ingest, so this week's
    quantities land on their own lines rather than on top of last week's.
    Called by approve_weekly_plan on the transition into 'approved', before
    a single ingredient is added. Returns the lines set aside.

    What counts as "from last week": a line whose source_weekly_plan_id is
    another plan whose period has already ENDED — its last day is before
    the household's today (see _other_plans_by_stage). Until 2026-10-02
    this read "has already STARTED", and that took in the week the
    household is still IN: plan next week on a Friday and tonight's
    bolognese and tomorrow's Pad Kra Pao went into "Still on the list from
    last week", off the list and off today's Shop row. A week still
    running is not a leftover; its lines stay on the list, and the new
    week's ingest puts its own amounts on lines of its own because a plan
    never merges onto another plan's line (_merge_target). A plan
    that hasn't begun yet is not a leftover either — a household that approves two weeks in
    advance is building next week's list, and asking them to keep-or-drop
    it would be asking about groceries nobody has had the chance to buy.
    Hand-added lines (source NULL) are a person's standing want and are
    left exactly where they are; a staple's suggestion has its own
    answers. Excluded lines ("somewhere else") and lines already in a
    cart are the shopper's, not the plan's, and stay too.

    "Has already STARTED" is the HOUSEHOLD's day, and until 2026-09-23 it
    was the container's. Every date on the other side of that comparison
    is household-relative — plan_period reads the columns the planning
    period was written in — so the server's date was the one half that
    belonged to nobody, and it is wrong in both directions. The container
    runs UTC and households default to America/Toronto, so from about 8pm
    local the server is already on tomorrow: a plan beginning the
    household's TOMORROW read as started, its unbought lines were set
    aside, and the household approving a week that evening was asked to
    keep or drop next week's shopping — the exact thing the paragraph
    above says must never be asked. East of UTC it fails the other way: a
    plan that began the household's TODAY reads as not yet begun, so last
    week's leftovers are NOT set aside and this week's amounts land on
    top of them, which is the quantity inflation this whole function
    exists to prevent.

    The clock is read on `conn` rather than on one of its own. The caller
    that matters (approve_weekly_plan) is holding an open write
    transaction, SQLite gives one writer at a time, and a nested get_conn
    in that position is how this app has twice earned an intermittent
    "database is locked".
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    ended = _other_plans_by_stage(conn, weekly_plan_id)["ended"]
    rows = conn.execute(
        "SELECT id, item, quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' AND excluded_from_list = 0 "
        "AND staple_id IS NULL AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ? "
        "ORDER BY id",
        (household_id(), weekly_plan_id),
    ).fetchall()
    set_aside = [r for r in rows if r["source_weekly_plan_id"] in ended]
    if set_aside:
        conn.executemany(
            "UPDATE grocery_items SET status = 'carried', carried_from_plan_id = source_weekly_plan_id "
            "WHERE id = ? AND household_id = ?",
            [(r["id"], household_id()) for r in set_aside],
        )
    set_aside = [dict(r) for r in set_aside] + _set_aside_ended_shares(conn, ended)
    # An earlier week's spices still UNTICKED in the section were never
    # wanted, so there is nothing to keep or drop: they go, and this
    # week's recipes put their own in (spices.py).
    stale_spices = conn.execute(
        "SELECT id, item, source_weekly_plan_id FROM grocery_items WHERE household_id = ? AND status = 'spice' "
        "AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ?",
        (household_id(), weekly_plan_id),
    ).fetchall()
    stale_spices = [r for r in stale_spices if r["source_weekly_plan_id"] in ended]
    if stale_spices:
        # A sauce left unticked through a whole week's shop counts as "we
        # have it" (Emily, 2026-10-18): its "Check you have this" is done.
        from . import spices as _spices  # lazy: spices imports this module
        _spices.note_known(conn, [r["item"] for r in stale_spices], "had_it")
        conn.executemany(
            "DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
            [(r["id"], household_id()) for r in stale_spices],
        )
    if own_conn:
        conn.commit()
        conn.close()
    return [{"item_id": r["id"], "item": r["item"], "quantity": r["quantity"] or ""} for r in set_aside]


def _ended_share_splits(conn, ended: set) -> list[tuple]:
    """
    What _set_aside_ended_shares would write, read only: for each of a
    household's own lines (source NULL, still needed, on the list — a
    staple's included) that an ENDED plan's meals put an amount on, (line, the
    line's restated (amount, nothing of it left), [(plan id, its ledger rows, its amount)]).
    Shared with _ended_week_to_ask_about, so a Today or Shop read only
    takes the write lock when there is really something to move.

    Left out (as before): a sealed package or spice the plan only borrowed
    (it never added an amount — the household's bottle is the week's
    bottle), and any line the restate can't read in one unit.
    """
    if not ended:
        return []
    rows = conn.execute(
        "SELECT g.id, g.item, g.quantity, g.category, g.store, g.staple_id, l.id AS link_id, "
        "l.quantity AS share, e.weekly_plan_id AS plan_id "
        "FROM grocery_items g "
        "JOIN meal_plan_grocery_links l ON l.grocery_item_id = g.id AND l.household_id = g.household_id "
        "JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
        "WHERE g.household_id = ? AND g.status = 'needed' AND g.excluded_from_list = 0 "
        # A staple's line too (overnight review, 2026-10-22): "Onion 2" put
        # there as probably running low, plus a week's curries' 4, went
        # 6 -> 10 -> 14 while it was left out. The staple's amount is a
        # standing want exactly like a typed one — the same restate takes
        # the ended plan's rounded total off it — and its "We have plenty"
        # / "Not this trip" (staples._trim_to_the_weeks_part) then answer
        # for the staple's amount and leave only the LIVE week's meals.
        "AND g.source_weekly_plan_id IS NULL ORDER BY g.id, l.id",
        (household_id(),),
    ).fetchall()
    by_line: dict[int, list] = {}
    for r in rows:
        by_line.setdefault(r["id"], []).append(r)
    splits = []
    for links in by_line.values():
        line = links[0]
        ended_links = [r for r in links if r["plan_id"] in ended]
        if not ended_links or any(_holds_one_package(line["item"], r["share"] or "") for r in ended_links):
            continue
        by_plan: dict[int, list] = {}
        for r in ended_links:
            by_plan.setdefault(r["plan_id"], []).append(r)
        # All of the line's shares at once: before = every plan's on it,
        # after = the live plans' alone. An add onto a standing want puts
        # on what the rounded ledger total moved
        # (_restate_standing_want_after_add, own-line-undo-no-creep), so the
        # line holds own + ceil(all shares), and taking an ended week off
        # must move it by the same arithmetic: ceil(all) - ceil(live).
        # (On the branch that first built this, adds were still each rounded
        # on their own, so it took each ended plan's rounded total off one at
        # a time; merged with the joint add, that took off too much and the
        # household's own line fell 4 -> 3 -> 2 -> 1. Merge 2026-10-25.)
        totals = _quantities._ledger_totals(
            [r["share"] or "" for r in links],
            [r["share"] or "" for r in links if r["plan_id"] not in ended],
        )
        restated = None if totals is None else _restate_standing_want(line["quantity"] or "", *totals)
        if restated is None:
            continue
        # Each ended plan's part is what IT moved the line's rounded total
        # by, taken off one plan at a time from the top (the later ended
        # plans and the live ones still on): ceil(this + rest) - ceil(rest).
        # The parts then add up to exactly what came off the line, so a Keep
        # puts back what was taken and no more. Each plan's own rounded total
        # here asked about more than came off, and Keep put the difference
        # back as creep (review of the 2026-10-25 merge: Dal 0.5 x 3 nights,
        # own 2, kept each week, went 4 -> 6 -> 8 -> 10).
        live_shares = [r["share"] or "" for r in links if r["plan_id"] not in ended]
        plan_shares = [[r["share"] or "" for r in pl] for pl in by_plan.values()]
        parts = []
        for i, (plan_id, plan_links) in enumerate(by_plan.items()):
            rest = [q for qs in plan_shares[i + 1:] for q in qs] + live_shares
            with_it = _plan_or_rack_amount(line["item"], plan_shares[i] + rest, "", "", "")
            without = _plan_or_rack_amount(line["item"], rest, "", "", "") if rest else ""
            if not with_it or without is None:
                qty = None
            elif not without:
                qty = with_it
            else:
                left, gone = _subtract_quantity(with_it, without)
                qty = None if gone else left
            parts.append((plan_id, plan_links, qty))
        # Only when every ended plan's part reads as an amount: half a split
        # would take the share off the line and ask about less than it took.
        # A part the rounding swallowed whole (nothing moved) is left where
        # it is, on the household's line, as before this existed.
        if all(qty for _p, _l, qty in parts):
            splits.append((line, restated, parts))
    return splits


def _set_aside_ended_shares(conn, ended: set) -> list[dict]:
    """
    The ended week's share on a household's OWN line, set aside the way its
    plan lines are (defect hunt, 2026-10-22). A plan's amount joins a line
    a person typed ("Onion 2" + three soups = "Onion 5"), and so does a
    person's add onto a plan line (which then becomes theirs). Only plan
    lines were set aside when the week ended, so last week's three onions
    stayed on the household's line, never asked about, and next week's
    soups landed on top: 2 -> 5 -> 8 -> 11, one week at a time, for as long
    as nobody bought onions — the creep CLAUDE.md warns about.

    Now the ended plan's part comes off the household's line in one step
    (_ledger_totals + _restate_standing_want, the same restate a dropped
    night uses) and stands as that plan's own 'carried' line with its
    meals' ledger rows moved onto it, so Shop asks "Still on the list from
    last week — keep or drop?" about it like any other. The household's own
    amount, and any live week's share, stay where they were.
    """
    out = []
    for line, (new_qty, nothing_left), parts in _ended_share_splits(conn, ended):
        if nothing_left:
            # All of the line was the ended week's (a merged add put back
            # with Undo, an edit down below the plan's share): the line
            # itself is the leftover, rather than a blank "Onion" left
            # on the list beside it.
            # On a staple's line it stops being the staple's, as the split-off
            # lines below never were: the leftover is the plan's, and a Keep
            # must not bring it back as "Probably running low" with the
            # staple's buttons on it (the same handover _trim_to_the_weeks_part
            # makes). With no line of its own, the staple is listed again
            # only when sync_due_staples finds it due.
            plan_id, _links, qty = parts[0]
            conn.execute(
                "UPDATE grocery_items SET quantity = ?, status = 'carried', source_weekly_plan_id = ?, "
                "carried_from_plan_id = ?, staple_id = NULL, "
                "added_by = CASE WHEN added_by = 'staple' THEN 'ai' ELSE added_by END "
                "WHERE id = ? AND household_id = ?",
                (qty, plan_id, plan_id, line["id"], household_id()),
            )
            out.append({"id": line["id"], "item": line["item"], "quantity": qty})
            parts = parts[1:]
        else:
            conn.execute(
                "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                (new_qty, line["id"], household_id()),
            )
        for plan_id, plan_links, qty in parts:
            cur = conn.execute(
                "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, "
                "source_weekly_plan_id, store, status, carried_from_plan_id) "
                "VALUES (?, ?, ?, ?, 'ai', ?, ?, 'carried', ?)",
                (household_id(), line["item"], qty, line["category"], plan_id, line["store"] or "", plan_id),
            )
            marks = ",".join("?" * len(plan_links))
            conn.execute(
                f"UPDATE meal_plan_grocery_links SET grocery_item_id = ? "
                f"WHERE household_id = ? AND id IN ({marks})",
                (cur.lastrowid, household_id(), *[r["link_id"] for r in plan_links]),
            )
            out.append({"id": cur.lastrowid, "item": line["item"], "quantity": qty})
    return out


def set_aside_ended_weeks() -> list[dict]:
    """
    The Monday ask (Loop Board "When a week ends with unbought items and
    next week was already approved, ask keep-or-drop on Monday", Emily
    2026-10-03; rebuilt on main 2026-10-13). Returns the lines set aside.

    set_aside_carried_over_items runs when a week is APPROVED, and only
    sets aside weeks that have ENDED by then — so a household that approved
    next week on Friday, while this week was still running, was never asked
    about this week's leftovers at all: on Monday they simply stayed on the
    list, groceries for meals that already happened. This is the same set-
    aside, run on the first read of Today or Shop after a week's last day,
    on the household's clock: the ended week's unbought lines go into the
    same "Still on the list from last week" step, with the same Keep /
    Don't need / Decide later. Nothing new is built for the asking.

    Only when a later week is already APPROVED and still live — otherwise
    approving the next week asks, exactly as it always has. What moves is
    exactly what approval moves (a plan's line, still needed, not set
    aside as "somewhere else", not a staple's — see
    set_aside_carried_over_items), from a plan whose last day is before
    today; a week on its last day is still running.

    Opening again never asks twice. An answered line is no longer a
    'needed' line of the ended plan — Don't need is removed, Keep either
    went onto this week's line or became the household's own want (source
    NULL), and Decide later is still 'carried', waiting — so a second read
    finds nothing to move.

    It runs on every read of Today and Shop, so it must cost a read
    nothing when there is nothing to do: a plain read decides first, and
    only a read that finds a line to move takes the write lock (review,
    2026-10-13 — taking BEGIN IMMEDIATE on every read made Today and Shop
    answer 500 "database is locked" while any other write held the lock
    past the busy timeout). The decision is made again inside the lock,
    because Today and Shop read at once on an open and two passes must not
    both move the same line.
    """
    today = _household_today().isoformat()
    conn = get_conn()
    try:
        if _ended_week_to_ask_about(conn, today) is None:
            return []
        conn.execute("BEGIN IMMEDIATE")
        live_approved = _ended_week_to_ask_about(conn, today)
        if live_approved is None:
            conn.rollback()
            return []
        set_aside = set_aside_carried_over_items(live_approved, conn=conn)
        conn.commit()
    finally:
        conn.close()
    return set_aside


def _ended_week_to_ask_about(conn, today: str) -> int | None:
    """
    The live approved plan to set aside against — but only when there is
    something to set aside: a still-needed plan line (or an unticked spice,
    which the set-aside clears) from a plan whose last day is before
    `today`, not set aside as "somewhere else", not a staple's. None
    otherwise. Read only.
    """
    live_approved = None
    ended = set()
    for plan in conn.execute(
        "SELECT id, status, week_start_date, content_start_date, day_count FROM weekly_plans "
        "WHERE household_id = ? ORDER BY id DESC",
        (household_id(),),
    ).fetchall():
        start, days = _weekly_plan.plan_period(plan)
        end = _weekly_plan.period_end_date(start, days)
        if plan["status"] == "approved" and days > 0 and end >= today:
            if live_approved is None:
                live_approved = plan["id"]
        elif start <= today and end < today:
            ended.add(plan["id"])
    if live_approved is None or not ended:
        return None
    marks = ",".join("?" * len(ended))
    waiting = conn.execute(
        "SELECT 1 FROM grocery_items WHERE household_id = ? AND ("
        "(status = 'needed' AND excluded_from_list = 0 AND staple_id IS NULL) OR status = 'spice') "
        f"AND source_weekly_plan_id IN ({marks}) LIMIT 1",
        (household_id(), *sorted(ended)),
    ).fetchone()
    # Or an ended week's share sitting on a household's own line
    # (_set_aside_ended_shares): with next week approved before this one
    # ended, nothing else would ever ask about it.
    if not waiting and _ended_share_splits(conn, ended):
        waiting = True
    return live_approved if waiting else None


def _this_weeks_line(conn, item: str, quantity: str, for_keep: bool = False):
    """
    The line a carried-over amount would merge into, if this week's
    recipes (or a hand add) put one there: a 'needed' line, or a spice
    still unticked in the section (spices.py) — keeping last week's cumin
    is saying you want to buy cumin, so the kept amount lands on that line
    and ticks it.

    Only a line the amount adds up with CLEANLY counts. Last week's "2 lbs"
    beside this week's hand-added "1" is the same unit clash add_grocery_item
    keeps as two lines (see _merge_target), and Keep must not put it back
    together as "1 + 2 lbs" — the caller restores the carried line on its
    own instead.
    """
    wanted = _merge_key(item)
    # Not a line set aside as "somewhere else": Keep would park the kept
    # amount on a hidden line — the hole add_grocery_item closes too.
    for r in conn.execute(
        "SELECT id, item, quantity, status, source_weekly_plan_id, kept_amount, short_amount, edited_to, "
        "staple_id, added_by FROM grocery_items "
        "WHERE household_id = ? AND status IN ('needed', 'spice') AND excluded_from_list = 0 ORDER BY id",
        (household_id(),),
    ).fetchall():
        # Never Keep onto a staple's own suggestion ("Probably running low"):
        # its "We have plenty" / "Not this trip" cut it back to the meals'
        # share (staples._trim_to_the_weeks_part), and a kept amount merged
        # in went with the staple's — kept, then gone, never asked about
        # again (review, 2026-10-23: Onion 2 + kept 4 + this week's 4,
        # plenty left 4). The kept amount stands on its own line instead,
        # the household's want, which the staple's answers don't touch.
        if for_keep and r["staple_id"] and r["added_by"] == "staple" and r["source_weekly_plan_id"] is None:
            continue
        if _merge_key(r["item"]) == wanted and _try_consolidate_quantity(r["quantity"] or "", quantity)[1]:
            return r
    return None


def list_carried_over_items() -> list[dict]:
    """
    What is still on the list from an earlier week and waiting for an
    answer — the rows set aside by set_aside_carried_over_items and not
    yet kept or dropped. Each carries `this_week_quantity`: what this
    week's recipes put on the list for the same thing, so the two amounts
    can be shown side by side rather than as one inflated number.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, store, carried_from_plan_id FROM grocery_items "
        "WHERE household_id = ? AND status = 'carried' ORDER BY category, item",
        (household_id(),),
    ).fetchall()
    out = []
    for r in rows:
        this_week = _this_weeks_line(conn, r["item"], r["quantity"] or "")
        out.append({
            "item_id": r["id"], "item": r["item"], "quantity": r["quantity"] or "",
            "category": r["category"], "store": r["store"] or "",
            "carried_from_plan_id": r["carried_from_plan_id"],
            "this_week_quantity": (this_week["quantity"] or "") if this_week else None,
        })
    conn.close()
    return out


def keep_carried_over_item(item_id: int) -> dict:
    """
    "Keep" on a carried-over line: the household still wants it. When this
    week's recipes put the same thing on the list, the old amount is added
    onto that line — the same consolidation add_grocery_item does, but
    asked for out loud this time — and the carried row is soft-removed
    (removed_by 'carried_kept') so an undo can take exactly that amount
    back off. Otherwise — nothing on the list for it this week, or only a
    line in a unit the old amount can't add up with ("1" beside "2 lbs";
    see _this_weeks_line) — the line itself comes back as needed, as the
    household's own standing want (source NULL): it was asked for, so no
    later week's cleanup may quietly delete it.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, category, status FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["status"] != "carried":
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    target = _this_weeks_line(conn, row["item"], row["quantity"] or "", for_keep=True)
    if target is not None:
        merged_qty, _reconciled = _try_consolidate_quantity(target["quantity"] or "", row["quantity"] or "")
        # Last week's unbought jar of cumin is the jar this week's cumin
        # needs too: one store unit, nothing kept on top (_rack_quantity) —
        # a Keep read "2 jars" for one recipe's tablespoon.
        rack = (
            _rack_quantity(target["item"], [target["quantity"] or "", row["quantity"] or ""])
            if target["source_weekly_plan_id"] is not None else None
        )
        if rack is not None:
            merged_qty = rack
        # On a plan's line the kept amount is also recorded as kept_amount
        # (2026-10-12). The line is recomputed from its meals' ledger when
        # one is swapped or dropped, and deleted with the last — which took
        # the kept 2 lbs of chicken with the stir fry. The reversal now
        # counts kept_amount in beside the ledger. The line stays the
        # plan's, so after its week it is set aside and asked about again
        # like any leftover (making it a standing want instead let it climb
        # week after week, owned by nobody). A household's own line needs
        # nothing: a reversal only ever takes the meals' share off it.
        kept_amount = target["kept_amount"] or ""
        if target["source_weekly_plan_id"] is not None and rack is None:
            kept_amount = (
                _try_consolidate_quantity(kept_amount, row["quantity"] or "")[0]
                if kept_amount.strip() else (row["quantity"] or "")
            )
        conn.execute(
            "UPDATE grocery_items SET quantity = ?, status = 'needed', kept_amount = ? WHERE id = ? AND household_id = ?",
            (merged_qty, kept_amount, target["id"], household_id()),
        )
        conn.execute(
            "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now'), "
            "kept_into_item_id = ? WHERE id = ? AND household_id = ?",
            (_CARRIED_KEPT, target["id"], item_id, household_id()),
        )
        conn.commit()
        conn.close()
        return {
            "item_id": item_id, "item": target["item"], "kept": True,
            "merged_into": target["id"], "quantity": merged_qty,
        }
    conn.execute(
        "UPDATE grocery_items SET status = 'needed', source_weekly_plan_id = NULL "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "kept": True, "merged_into": None, "quantity": row["quantity"] or ""}


def drop_carried_over_item(item_id: int) -> dict:
    """"Don't need" on a carried-over line: soft-removed (removed_by 'carried_dropped'), so it can be undone."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, status FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["status"] != "carried":
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (_CARRIED_DROPPED, item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "dropped": True}


def undo_carried_over_decision(item_id: int) -> dict:
    """
    Put a carried-over line back to "waiting for an answer", whichever
    answer it got. A kept line that was merged onto this week's line has
    its amount subtracted back out of that line first (_subtract_quantity,
    the same inverse a swapped meal uses); a kept line restored on its own
    goes back to 'carried' with its old plan as its source again; a
    dropped line simply comes back. A row that was never carried over is
    left alone.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status, removed_by, carried_from_plan_id, kept_into_item_id FROM grocery_items "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["carried_from_plan_id"] is None or row["status"] == "carried":
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    if row["status"] == "removed" and row["removed_by"] == _CARRIED_KEPT:
        # The kept amount has to come back OFF this week's line before the
        # question can be asked again — otherwise a second Keep counts it
        # twice. Two cases where it can't: the line has gone (bought, in a
        # cart, removed), or the merge can't be read back apart ("1 bag"
        # kept onto "1 bag (2 lb)" is "2 bags (2 lb)", and _subtract_quantity
        # has no bag to take off it). Then the honest answer is "too late",
        # not a reopened question (verifier, 2026-09-13 — reproduced both).
        #
        # The line is the one the keep went onto (kept_into_item_id), not
        # whichever line now matches by name — that could be a person's own
        # want added since. A keep from before that column existed has no
        # record and is found by name, as it always was.
        if row["kept_into_item_id"] is not None:
            target = conn.execute(
                "SELECT id, item, quantity, status, source_weekly_plan_id, kept_amount, short_amount, edited_to "
                "FROM grocery_items WHERE id = ? AND household_id = ?",
                (row["kept_into_item_id"], household_id()),
            ).fetchone()
        else:
            target = _this_weeks_line(conn, row["item"], row["quantity"] or "")
        if target is None or target["status"] != "needed":
            conn.close()
            return {"item_id": item_id, "item": row["item"], "unchanged": True, "reason": "acted_on"}
        kept_left = None
        if target["source_weekly_plan_id"] is not None and (target["kept_amount"] or "").strip():
            left, gone = _subtract_quantity(target["kept_amount"], row["quantity"] or "")
            kept_left = "" if gone else left
        elif target["source_weekly_plan_id"] is not None and (target["short_amount"] or "").strip():
            # The household edited the line down after the Keep, which
            # folded the kept amount into their number: nothing kept is
            # left to take off, and the line is its meals under their edit.
            # Subtracting the Keep from the "1" they typed blanked it with
            # the chili still planned (re-review, 2026-10-13).
            kept_left = ""
        rebuilt = False
        # Not a sealed package: its line is one bag for the week however
        # many meals name it, so it is never re-read off the ledger (see
        # _reverse_meal_grocery_contributions) and falls to the subtraction
        # below, exactly as before.
        if kept_left is not None and not _quantities.package_unit(target["quantity"] or ""):
            # A plan's line: what is left is its meals' ledger plus any
            # other kept amount — read off the ledger, the way a reversal
            # reads it, rather than subtracted from the rounded display.
            ledger = [
                r["quantity"] or "" for r in conn.execute(
                    "SELECT quantity FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ?",
                    (household_id(), target["id"]),
                ).fetchall()
            ]
            if not ledger and not kept_left:
                # Its meals have since gone (a swap, a drop): nothing is
                # left to buy, so the line goes rather than staying blank.
                conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
                             (target["id"], household_id()))
                rebuilt = True
            else:
                summed = _plan_line_amount(ledger, kept_left or "", target["short_amount"], target["edited_to"])
                if summed is not None:
                    note = _quantities._quantity_note(target["quantity"] or "")
                    rebuilt_qty = _quantities._with_note(summed, note) if summed else ""
                    conn.execute(
                        "UPDATE grocery_items SET quantity = ?, kept_amount = ? WHERE id = ? AND household_id = ?",
                        (rebuilt_qty, kept_left,
                         target["id"], household_id()),
                    )
                    rebuilt = True
        if not rebuilt and kept_left == "" and _quantities.package_unit(target["quantity"] or ""):
            # A sealed package whose meals have all gone was holding only the
            # kept bag (the reversal set it to that): taking it back leaves
            # nothing to buy, so the line goes — as on the path above —
            # rather than staying as an orphan "1 bag" nobody wants.
            if not conn.execute(
                "SELECT 1 FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ? LIMIT 1",
                (household_id(), target["id"]),
            ).fetchone():
                conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
                             (target["id"], household_id()))
                rebuilt = True
        if not rebuilt:
            new_qty, fully_removed = _subtract_quantity(target["quantity"] or "", row["quantity"] or "")
            if not fully_removed and new_qty == (target["quantity"] or ""):
                conn.close()
                return {"item_id": item_id, "item": row["item"], "unchanged": True, "reason": "acted_on"}
            # Fully removed means the line held nothing but the kept amount
            # (this week's line was blank), so blank is what it goes back to.
            conn.execute(
                "UPDATE grocery_items SET quantity = ?, kept_amount = COALESCE(?, kept_amount) "
                "WHERE id = ? AND household_id = ?",
                ("" if fully_removed else new_qty, kept_left, target["id"], household_id()),
            )
    elif row["status"] not in ("removed", "needed"):
        # Bought or in a cart since: the decision has been acted on.
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    conn.execute(
        "UPDATE grocery_items SET status = 'carried', removed_by = '', removed_at = NULL, "
        "source_weekly_plan_id = carried_from_plan_id, kept_into_item_id = NULL WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "status": "carried"}


# ---------- "I'll use something else instead" ----------
# Loop Board, 2026-09-13 (Emily: "if we want to use an alternative that
# should be a spot we can put it here too, for example, instead of fresh
# oregano I'll use dry oregano"). A per-week swap, made while sorting the
# list: the line becomes the alternative — or comes off, when the
# alternative is already in the house — and the recipe's ingredient line
# says "using dry oregano instead" when cooking (cooker.get_cooker_view).
# Never a recipe edit, never an inventory record. The "have it already"
# half of the same ticket is pre_shop.drop_grocery_item_pre_shop: a
# soft-remove with an undo, listed on the wrap-up — and, since Emily lifted
# policy 2026-09-01 on 2026-10-25, counted as what's home (the kitchen row
# confirmed or added, and taken back exactly by the undo; never for a
# staple's line). This swap's own "I have it" still writes nothing.


def substitute_grocery_item(item_id: int, alternative: str, at_home: bool = False, author: str = "") -> dict:
    """
    Swap a grocery line for something else this week. The line is renamed
    to `alternative` (its quantity kept as written — nobody can convert
    fresh oregano into dry, so the number stays for the person to adjust)
    and picks up the alternative's usual store if the line had none. With
    `at_home`, the alternative is already in the house, so the line is
    soft-removed as well (the same removal "have it already" makes —
    reversible, and listed on the wrap-up). Either way the swap is recorded
    against the week, so the recipe can say so when it is cooked. Raises
    ValueError for an unknown line or a blank alternative.
    """
    alternative = " ".join((alternative or "").strip().split())
    if not alternative:
        raise ValueError("Say what to use instead.")
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status, store, source_weekly_plan_id FROM grocery_items "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["status"] not in ("needed", "spice"):
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    plan_id = row["source_weekly_plan_id"]
    if plan_id is None:
        current = _weekly_plan._current_weekly_plan_row(conn)
        plan_id = current["id"] if current else None
    conn.execute(
        "INSERT INTO grocery_substitutions (household_id, grocery_item_id, weekly_plan_id, original_item, alternative, at_home) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (household_id(), item_id, plan_id, row["item"], alternative, 1 if at_home else 0),
    )
    prefs = conn.execute(
        "SELECT item, store FROM item_store_preferences WHERE household_id = ?", (household_id(),),
    ).fetchall()
    pref = next((p for p in prefs if _merge_key(p["item"]) == _merge_key(alternative)), None)
    store = row["store"] or (pref["store"] if pref else "")
    if at_home:
        conn.execute(
            "UPDATE grocery_items SET item = ?, store = ?, status = 'removed', removed_by = ?, "
            "removed_at = datetime('now') WHERE id = ? AND household_id = ?",
            (alternative, store, acting_name(author) or "", item_id, household_id()),
        )
    else:
        # A spice waiting unticked becomes a needed line: choosing what
        # to buy instead is choosing to buy it.
        conn.execute(
            "UPDATE grocery_items SET item = ?, store = ?, status = 'needed' WHERE id = ? AND household_id = ?",
            (alternative, store, item_id, household_id()),
        )
    conn.commit()
    conn.close()
    return {
        "item_id": item_id, "item": alternative, "original_item": row["item"],
        "at_home": bool(at_home), "status": "removed" if at_home else "needed",
    }


def undo_substitution(item_id: int) -> dict:
    """Put a substituted line back under its original name (and back on the list, if the swap took it off)."""
    conn = get_conn()
    sub = conn.execute(
        "SELECT id, original_item, at_home FROM grocery_substitutions WHERE household_id = ? AND grocery_item_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (household_id(), item_id),
    ).fetchone()
    if sub is None:
        conn.close()
        return {"item_id": item_id, "unchanged": True}
    row = conn.execute(
        "SELECT status FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id()),
    ).fetchone()
    if row is not None:
        if sub["at_home"] and row["status"] == "removed":
            conn.execute(
                "UPDATE grocery_items SET item = ?, status = 'needed', removed_by = '', removed_at = NULL "
                "WHERE id = ? AND household_id = ?",
                (sub["original_item"], item_id, household_id()),
            )
        else:
            conn.execute(
                "UPDATE grocery_items SET item = ? WHERE id = ? AND household_id = ?",
                (sub["original_item"], item_id, household_id()),
            )
    conn.execute(
        "DELETE FROM grocery_substitutions WHERE id = ? AND household_id = ?",
        (sub["id"], household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": sub["original_item"], "status": "needed"}


def substitutions_for_plan(plan_id: int | None) -> list[dict]:
    """
    This week's swaps, for the cook view: [{original_item, alternative,
    at_home}]. Only swaps made against THIS plan, and only while the
    grocery line they were made on still exists — a line the ledger
    deleted (both meals that wanted it swapped out, the week cleared) takes
    its swap with it, so a fresh need for the same thing later in the week
    is not told it was substituted (verifier, 2026-09-13 — reproduced). A
    swap made with no plan at all annotates nothing: there is no card for
    it, and matching every later week would make it permanent.
    """
    if plan_id is None:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.original_item, s.alternative, s.at_home FROM grocery_substitutions s "
        "WHERE s.household_id = ? AND s.weekly_plan_id = ? "
        "AND EXISTS (SELECT 1 FROM grocery_items g WHERE g.id = s.grocery_item_id) ORDER BY s.id",
        (household_id(), plan_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_grocery_list(status: str = "needed") -> dict:
    """
    Remove ALL items with the given status (default 'needed') in one shot —
    a full reset, not a merge or a staleness check. Use only when the user
    explicitly asks to clear/empty/start the grocery list over (e.g. "wipe
    the list, we're starting fresh"). For routine cleanup use
    consolidate_grocery_list (duplicates) or clear_stale_grocery_items (old
    plan leftovers) instead — this one has no way to know what's still
    actually needed.
    """
    from . import staples as _staples

    conn = get_conn()
    rows = conn.execute(
        "SELECT id, staple_id, status FROM grocery_items WHERE household_id = ? AND (? = 'all' OR status = ?)",
        (household_id(), status, status),
    ).fetchall()
    count = len(rows)
    # A staple's line still to buy is soft-removed, not deleted (defect
    # hunt, 2026-10-20). Deleted, the very next read of the list ran
    # staples.sync_due_staples, found the staple still due and nothing by
    # its name on the list, and put it straight back: "Start over" said it
    # removed 2 items and Shop showed the same 2. A line removed TODAY is
    # the one thing sync leaves alone (and tidies away tomorrow), so this
    # keeps them off for the rest of the household's day — and a staple
    # line already removed today is kept for the same reason. Unlike
    # ⋯ → Remove this is not "not this trip": a wipe is no answer about
    # any one staple, so no rhythm moves and no skip counts toward the
    # auto-pause. A bought staple line is deleted like anything else.
    held = {r["id"] for r in rows if r["staple_id"] and r["status"] in ("needed", "in_cart", "removed")}
    gone = [r["id"] for r in rows if r["id"] not in held]
    held = sorted(held)
    for i in range(0, len(held), 500):
        chunk = held[i:i + 500]
        conn.execute(
            f"UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
            f"WHERE household_id = ? AND status != 'removed' AND id IN ({','.join('?' * len(chunk))})",
            (_staples.ADDED_BY_STAPLE, household_id(), *chunk),
        )
    for i in range(0, len(gone), 500):
        chunk = gone[i:i + 500]
        conn.execute(
            f"DELETE FROM grocery_items WHERE household_id = ? AND id IN ({','.join('?' * len(chunk))})",
            (household_id(), *chunk),
        )
    conn.commit()
    conn.close()
    return {"removed_count": count}


RESTORED, ROW_GONE, LEFT_ALONE = "restored", "gone", "left"


def _restore_inventory_from_receipt(conn, receipt_json: str | None, item_id: int | None = None) -> str:
    """
    Reverse exactly the inventory write a purchased tick recorded, on the
    caller's connection. Returns one of three answers, because the caller
    treats them differently:

    RESTORED — the kitchen was put back. The receipt
    (grocery_items.inventory_receipt_json) says which row the tick wrote,
    whether it created that row or merged into stock already there, and
    what the row read on either side of the write. The row is reversed
    ONLY if it still reads exactly what the write left it at — proven by
    inventory_items.rev, the per-row write counter its trigger bumps on
    every update. Not updated_at: datetime('now') is whole-second, so "set
    to 8, set back to 12" inside the tick's own second read as untouched
    (reproduced 2026-09-13); rev counts both. Then: a row the tick created
    is deleted (it exists only because of the tick, the same call
    undo_pre_shop_drop makes on already_have_inventory_id); a merged row
    gets its quantity, source, category and expiry put back to the
    recorded before-state, not to "now minus what we added" — the
    before-state is known, so nothing is computed.

    ROW_GONE — the row the tick wrote no longer exists (deleted from the
    Kitchen screen, or used down to zero, which deletes). Nothing to put
    back, and nothing left to double onto either, so the caller clears the
    stamp: a re-tick is a first tick again. The alternative — keeping the
    stamp — locks a line out of the kitchen for good, silently, the moment
    somebody tidies the kitchen by hand before fixing the list; the cost of
    clearing is one contrived sequence (eat all twelve, THEN untick, then
    re-tick) that puts twelve back. Certainty about a row that is gone is a
    different thing from certainty about a row that was merely edited.

    LEFT_ALONE — the row is there but has been written since (used from,
    edited, nudged, moved, re-set), or there is no receipt at all (a line
    bought before the column existed). Nothing is guessed: the row stays,
    and so does the stamp, so the re-tick adds nothing on top of what is
    already there. Never subtracts, never deletes on a guess: the
    household's later edits are the truer record of the shelf.
    """
    if not receipt_json:
        return LEFT_ALONE
    try:
        receipt = json.loads(receipt_json)
    except (TypeError, ValueError):
        return LEFT_ALONE
    inventory_id = receipt.get("inventory_id")
    after = receipt.get("after") or {}
    if not inventory_id or not after:
        return LEFT_ALONE
    current = _inventory._receipt_snapshot(conn, inventory_id)
    if current is None:
        return ROW_GONE
    if not _same_state(current, after):
        if item_id is not None and _untick_from_under(conn, item_id, receipt, current):
            return RESTORED
        return LEFT_ALONE
    if receipt.get("fresh"):
        conn.execute(
            "DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (inventory_id, household_id())
        )
        return RESTORED
    before = receipt.get("before") or {}
    if not before:
        return LEFT_ALONE
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, source = ?, category = ?, expiration_date = ?, "
        "updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (
            before.get("quantity") if before.get("quantity") is not None else "",
            before.get("source") or "chat",
            before.get("category") or "other",
            before.get("expiration_date"),
            inventory_id,
            household_id(),
        ),
    )
    if item_id is not None:
        # The row reads what the tick underneath left it at again — but the
        # restore bumped its rev, so hand that tick the row as it is now.
        under = _stacked_tick(conn, item_id, inventory_id, after_state=before)
        if under is not None:
            under_id, under_receipt = under
            under_receipt["after"] = _inventory._receipt_snapshot(conn, inventory_id)
            _save_tick_receipt(conn, under_id, under_receipt)
    return RESTORED


# Two ticks on ONE kitchen row (defect hunt 2026-10-28). A row's ticks
# merge: two weeks' onion lines behind one Shop row (week_lines — ticked
# and unticked together, in whatever order the two posts land), or a
# person's "12" eggs beside a recipe's "2 cups". Each tick's receipt is
# exact about its own write only, so whichever untick went second found
# the row changed by the first and left it — both lines back on the list
# and the eggs still "12" in the kitchen, for good (its stamp stays, so
# the re-tick adds nothing either). The receipts chain: the later tick's
# `before` is the earlier tick's `after`. These two helpers follow that
# chain one link, and only while every state on it still reads exactly
# (rev and quantity) — anything else wrote to the row since, and that is
# left alone exactly as before.
def _same_state(a: dict | None, b: dict | None) -> bool:
    return bool(a) and bool(b) and a.get("rev") == b.get("rev") and a.get("quantity") == b.get("quantity")


def _stacked_tick(conn, item_id: int, inventory_id, *, after_state: dict | None = None,
                  before_state: dict | None = None):
    """The other still-stamped tick on this kitchen row whose receipt's
    `after` (or `before`) is exactly this state: (line id, receipt)."""
    for r in conn.execute(
        "SELECT id, inventory_receipt_json FROM grocery_items WHERE household_id = ? AND id != ? "
        "AND inventory_added_at IS NOT NULL AND inventory_receipt_json IS NOT NULL "
        "AND json_extract(inventory_receipt_json, '$.inventory_id') = ?",
        (household_id(), item_id, inventory_id),
    ).fetchall():
        try:
            other = json.loads(r["inventory_receipt_json"])
        except (TypeError, ValueError):
            continue
        if after_state is not None and _same_state(other.get("after"), after_state):
            return r["id"], other
        if before_state is not None and _same_state(other.get("before"), before_state):
            return r["id"], other
    return None


def _save_tick_receipt(conn, item_id: int, receipt: dict) -> None:
    conn.execute(
        "UPDATE grocery_items SET inventory_receipt_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(receipt), item_id, household_id()),
    )


def _untick_from_under(conn, item_id: int, receipt: dict, current: dict) -> bool:
    """
    Take an earlier tick back out from under the one later tick that sits
    exactly on top of it: the row becomes what it would read had only the
    later tick happened — the earlier tick's before-state with the later
    line's amount merged onto it (just that amount, for a row the earlier
    tick created) — and the later tick's receipt now starts where the
    earlier one did, so its own untick still reverses exactly. Only when
    replaying the later tick onto the earlier one's after-state reproduces
    the row as it reads now (its amount was not edited since, nothing else
    landed between). True when it did.
    """
    inventory_id = receipt.get("inventory_id")
    after = receipt.get("after") or {}
    top = _stacked_tick(conn, item_id, inventory_id, before_state=after)
    if top is None:
        return False
    top_id, top_receipt = top
    if not _same_state(top_receipt.get("after"), current):
        return False
    line = conn.execute(
        "SELECT quantity FROM grocery_items WHERE id = ? AND household_id = ?", (top_id, household_id()),
    ).fetchone()
    added = (line["quantity"] or "") if line else ""
    if _inventory._merged_quantity(after.get("quantity") or "", added) != (current.get("quantity") or ""):
        return False
    fresh = bool(receipt.get("fresh"))
    before = receipt.get("before") or {}
    if not fresh and not before:
        return False
    quantity = added if fresh else _inventory._merged_quantity(before.get("quantity") or "", added)
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (quantity, inventory_id, household_id()),
    )
    top_receipt["fresh"] = fresh
    top_receipt["before"] = None if fresh else before
    top_receipt["after"] = _inventory._receipt_snapshot(conn, inventory_id)
    _save_tick_receipt(conn, top_id, top_receipt)
    return True


# The three statuses the SHOPPER moves a line between. Deliberately not
# every value the column takes: 'removed', 'carried', 'excluded' and
# 'spice' are real statuses, and each is set by its own function with its
# own bookkeeping beside it (removed_by, carried_from_plan_id, the
# staple's "we have plenty", the pre-shop undo). Reaching one of those
# through this door would set the status and none of the rest.
GROCERY_SHOPPER_STATUSES = ("needed", "in_cart", "purchased")


class InvalidGroceryStatus(ValueError):
    """
    A status outside GROCERY_SHOPPER_STATUSES. Same marker shape as
    cooker.InvalidMealStatus and chores.InvalidChoreStatus, and here for
    the same reason: it IS a ValueError subclass, so the route's 404
    handler would swallow it, and "that request doesn't make sense" is a
    different answer from "no such line".

    Reproduced 2026-09-24 before this existed: mark_grocery_item("Rice",
    status="teleported") answered 200 and wrote it. Nothing crashed. The
    line was then in a status no view's WHERE clause looks for — off the
    needed list, off the trolley, off the bought list, gone from the
    shop without being removed from it, and with no screen able to put
    it back.

    The screens cannot produce one (groTick sends the three, and the
    offline queue replays what it recorded) and the chat tool's schema
    enumerates them. That was true of check_off_meal too and it was
    guarded anyway, on this app's own rule that telling the generator
    something is not the same as preventing it — and here the sibling
    vocabulary is bigger, not smaller: four other real statuses sit one
    function over.
    """


def _catch_up_unticked_plan_line(conn, item_id: int) -> None:
    """
    A plan's line coming back onto the list from the trolley re-reads its
    amount off the meals still planned, when a meal went while it was
    ticked.

    _reverse_meal_grocery_contributions leaves a bought line's amount alone
    (nothing is yanked out of a cart) but always deletes the leaving meal's
    ledger rows, so the line was left saying more than its meals: rice
    ticked at "6 cups", a stir-fry night dropped, untick -> "6 cups" to
    buy for 4 cups of dinners. Only ever DOWN, and only when the ledger
    still reads in the line's unit: a line whose meals never changed reads
    the same, a household's edit is put back by _plan_line_amount, and
    wording ("a few") is never replaced. Left alone: a person's own line
    (its dropped share went with the ledger row and can't be told apart
    from their want), a sealed package (one bottle however many meals),
    and a line with no meals left — the same open case as a set-aside
    line whose meals all went while it was off.
    """
    row = conn.execute(
        "SELECT quantity, source_weekly_plan_id, kept_amount, short_amount, edited_to "
        "FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None or row["source_weekly_plan_id"] is None:
        return
    current = row["quantity"] or ""
    if _quantities.package_unit(current):
        return
    ledger = [
        r["quantity"] or "" for r in conn.execute(
            "SELECT quantity FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ?",
            (household_id(), item_id),
        ).fetchall()
    ]
    if not ledger:
        return
    summed = _plan_line_amount(ledger, row["kept_amount"], row["short_amount"], row["edited_to"])
    if not summed or not _more_than(_quantities._split_quantity_note(current)[0], summed):
        return
    conn.execute(
        "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
        (_quantities._with_note(summed, _quantities._quantity_note(current)), item_id, household_id()),
    )


# The receipt a household supply's tick records in place of a kitchen row's.
SUPPLY_RECEIPT = {"supply": True}


def _is_supply_receipt(receipt_json: str | None) -> bool:
    try:
        return bool(receipt_json) and json.loads(receipt_json) == SUPPLY_RECEIPT
    except (TypeError, ValueError):
        return False


_FOOD_ENDING_SUPPLY_WORDS = {"sponge", "floss", "foil"}
_SUPPLY_FLOSS_AND_FOIL = {"dental floss", "kitchen foil", "baking foil", "scrub sponge", "kitchen sponge"}


def _is_household_supply(conn, item_id: int, item: str) -> bool:
    """
    Whether a ticked line is a household supply — dish soap, toilet paper,
    batteries — that a tick must NOT put in the kitchen (defect hunt
    2026-10-29). Walking a shop end to end, the dish soap landed on the
    Kitchen tab as "Pantry · bought today · best by Nov 11", counted in
    Shop's "Now in the kitchen · Pantry 3 things". The Kitchen is food;
    supplies are Staples' (CLAUDE.md: staples never touch inventory).

    Stricter than staples' own section on purpose, because a wrong yes here
    loses food from the kitchen rather than filing a staple under the wrong
    heading: the whole name is one of staples' household phrases, or its
    LAST word — the thing itself — is a household word ("dish soap", "AA
    batteries"; never "sponge cake" or "pad thai noodles"). And a line a
    planned meal is buying for is food whatever its name says. Not the
    category: a chat add files dish soap under pantry as often as not.
    """
    from . import staples as _staples  # lazy: staples imports this module

    words = _staples._name_words(item)
    if not words:
        return False
    if " ".join(words) not in _staples._HOUSEHOLD_PHRASES and words[-1] not in _staples._HOUSEHOLD_WORDS:
        return False
    # Three household words are also the last word of real food: "Victoria
    # sponge", "Candy floss", "Salmon in foil" (review, 2026-10-29). For
    # those the name has to say supply outright — a plural "sponges" (no one
    # buys sponge cakes by that word), or a named floss or foil.
    if words[-1] in _FOOD_ENDING_SUPPLY_WORDS and " ".join(words) not in _staples._HOUSEHOLD_PHRASES:
        raw = (item or "").strip().lower().replace(",", " ").split()
        if raw[-1] != "sponges" and " ".join(raw[-2:]) not in _SUPPLY_FLOSS_AND_FOIL:
            return False
    for_a_meal = conn.execute(
        "SELECT 1 FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ? LIMIT 1",
        (household_id(), item_id),
    ).fetchone()
    return for_a_meal is None


def mark_grocery_item(item_id: int, status: str = "purchased") -> dict:
    """
    Update a grocery item's status (needed/in_cart/purchased). Marking
    something purchased also adds it to tracked pantry/fridge inventory
    automatically (source='grocery_checkoff'), with expiration left unset —
    see update_inventory/get_inventory. That add happens ONCE per line
    however many times it is ticked, un-ticked and ticked again; moving a
    purchased line back off 'purchased' takes it back OUT of the kitchen
    when the kitchen row is still exactly as the tick left it, and leaves
    the kitchen alone otherwise (the result says which:
    inventory_added / inventory_restored). See _restore_inventory_from_
    receipt for the argument. A household supply (dish soap, toilet paper —
    _is_household_supply) never goes in the kitchen: inventory_added False.
    """
    # Before get_conn, so a bad status never opens a connection and never
    # takes the write lock — and so a caller that never reaches the route
    # (chat, a script) is held to the same three.
    if status not in GROCERY_SHOPPER_STATUSES:
        raise InvalidGroceryStatus(
            f"{status!r} isn't a status a grocery line can be moved to. "
            f"Use one of: {', '.join(GROCERY_SHOPPER_STATUSES)}."
        )
    conn = get_conn()
    added = None
    restored = None
    try:
        # One BEGIN IMMEDIATE, so the write lock is held from the first read
        # (the shape cooker._claim_inventory_depletion and
        # weekly_plan._replace_slot_entries use, for the same reason): the
        # status route is a sync def in a threadpool, and two 'purchased'
        # posts for one line at the same instant both read the old status
        # otherwise — 20/20 concurrent pairs doubled before this.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT item, quantity, category, status, staple_id, inventory_added_at, inventory_receipt_json "
            "FROM grocery_items WHERE id = ? AND household_id = ?",
            (item_id, household_id()),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ValueError(f"No grocery list item with id {item_id}.")
        # Idempotent on purpose (grocery offline, 2026-09-11): a phone in a
        # store with one bar can send the same status twice — the request
        # got through but the reply didn't, so the queue in
        # static/grocery-offline.js sends it again, and "Done at <store>"
        # tapped twice after a "try again" does the same. Setting an
        # unchanged status is a no-op. This guard is necessary and NOT
        # sufficient: purchased -> needed -> purchased changes the status
        # each time, which is why the inventory write is keyed on
        # inventory_added_at below and not on this comparison.
        if row["status"] == status:
            conn.rollback()
            return {"item_id": item_id, "status": status, "unchanged": True}
        fields = "status = ?"
        params: list = [status]
        if status == "purchased":
            if row["inventory_added_at"] is None and _is_household_supply(conn, item_id, row["item"]):
                # Dish soap is bought, not put in the kitchen. The stamp is
                # still written — it is also WHEN the line was ticked, which
                # Shop's checklist and the staples' rhythm read — beside a
                # receipt that names no kitchen row, so the untick below
                # has nothing to take back.
                fields += ", inventory_added_at = datetime('now'), inventory_receipt_json = ?"
                params.append(json.dumps(SUPPLY_RECEIPT))
                added = False
            elif row["inventory_added_at"] is None:
                receipt = _inventory._add_to_inventory(
                    row["item"], row["quantity"] or "", source="grocery_checkoff", category=row["category"], conn=conn
                )
                fields += ", inventory_added_at = datetime('now'), inventory_receipt_json = ?"
                params.append(json.dumps({
                    "inventory_id": receipt["item_id"],
                    "fresh": receipt["fresh"],
                    "before": receipt["before"],
                    "after": receipt["after"],
                }))
                added = True
            else:
                # Already in the kitchen from an earlier tick on this line
                # and never taken back out — the status moves, the shelf
                # does not.
                added = False
        elif _is_supply_receipt(row["inventory_receipt_json"]):
            # A supply's tick never touched the kitchen: just un-stamp it.
            fields += ", inventory_added_at = NULL, inventory_receipt_json = NULL"
        elif row["status"] == "purchased" or row["inventory_added_at"] is not None:
            # Leaving 'purchased' (or a line whose earlier untick could not
            # restore): try the exact reversal. The stamp clears only when
            # the shelf really was put back, so a later re-tick is a first
            # tick again; when it was not, the stamp stays and the re-tick
            # adds nothing on top of what is already there. A row that is
            # GONE also clears the stamp (nothing left to double onto — see
            # the helper). A line bought before the stamp existed has no
            # receipt, so this reports inventory_restored: False rather than
            # staying quiet.
            outcome = _restore_inventory_from_receipt(conn, row["inventory_receipt_json"], item_id)
            restored = outcome == RESTORED
            if outcome in (RESTORED, ROW_GONE):
                fields += ", inventory_added_at = NULL, inventory_receipt_json = NULL"
            # Not bought after all: the fridge move this line's "Yes,
            # freezing it" booked goes too, whatever the shelf says — no
            # pack, nothing to thaw (defrost.cancel_ticked_pack_move).
            from . import defrost as _defrost  # lazy: defrost imports this module

            if _defrost.cancel_ticked_pack_move(conn, row["inventory_receipt_json"]) and outcome == LEFT_ALONE:
                kept = json.loads(row["inventory_receipt_json"])
                kept.pop(_defrost.FREEZING_RECEIPT_KEY, None)
                fields += ", inventory_receipt_json = ?"
                params.append(json.dumps(kept))
        conn.execute(
            f"UPDATE grocery_items SET {fields} WHERE id = ? AND household_id = ?",
            (*params, item_id, household_id()),
        )
        if status == "needed" and row["status"] in ("purchased", "in_cart"):
            _catch_up_unticked_plan_line(conn, item_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    result: dict = {"item_id": item_id, "status": status}
    if added is not None:
        result["inventory_added"] = added
    if restored is not None:
        result["inventory_restored"] = restored
    # Imported here, not at the top: staples.py imports this module for the
    # merge key. Both calls run after the commit above, on their own
    # connection.
    from . import staples as _staples

    if status == "purchased":
        # A bought staple teaches its rhythm, whoever put the line there —
        # a hand-added "coffee" counts the same as the suggestion Pomona
        # made. No-op for anything that isn't a staple — except a spice,
        # which becomes one (the spice rack, kept from purchases; see
        # staples.py's note on sections) — and one bought date per day per
        # staple, so a re-tick teaches nothing twice. The event remembers
        # this line as its creator, so the untick below can take it back.
        _staples.record_staple_purchase(
            row["item"], source="grocery", staple_id=row["staple_id"],
            grocery_item_id=item_id, category=row["category"],
        )
    elif row["status"] == "purchased":
        # Un-ticked: the purchase this line taught did not happen. Takes
        # back today's bought event only when this line created it and
        # nothing else bought the same thing today — see
        # staples.unrecord_staple_purchase. Earlier days are never touched.
        _staples.unrecord_staple_purchase(row["item"], staple_id=row["staple_id"], grocery_item_id=item_id)
    return result


def update_grocery_item(item_id: int, quantity: str | None = None, category: str | None = None) -> dict:
    """
    Directly edit an already-listed grocery item's quantity and/or category
    by id — for correcting something already on the list (wrong amount,
    miscategorized) rather than adding a new line. Unlike add_grocery_item,
    this never merges/consolidates with another row since it's already
    targeting one specific, known item. Leave a field as None to leave it
    unchanged.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, category, source_weekly_plan_id FROM grocery_items "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if quantity is not None:
        # Typed into the row's box: "24 pack" is one pack of 24.
        quantity = _quantities.typed_multipack(quantity)
    if quantity is not None and _quantities.same_shown_quantity(
        row["item"] or "", quantity.strip(), row["quantity"] or ""
    ):
        # The box sent back as it was shown (the row's Save sends it
        # changed or not): nothing to record. Writing it would swap the
        # stored amount for its two-place display ("0.67 cup" for ⅔) and
        # file the difference as a household edit nobody made.
        quantity = None
    elif quantity is not None:
        # A merge's Undo sends the phone's copy, which is the shown text.
        quantity = _quantities.typed_back_quantity(quantity)
    new_quantity = quantity if quantity is not None else row["quantity"]
    new_category = category if category is not None else row["category"]
    # The household's number survives the plan changing (Emily,
    # 2026-10-12) — recorded beside the line, which stays the plan's; see
    # _household_edit_columns.
    edit = None
    if quantity is not None and row["source_weekly_plan_id"] is not None:
        edit = _household_edit_columns(conn, row, new_quantity or "")
    conn.execute(
        "UPDATE grocery_items SET quantity = ?, category = ? WHERE id = ? AND household_id = ?",
        (new_quantity, new_category, item_id, household_id()),
    )
    if edit is not None:
        conn.execute(
            "UPDATE grocery_items SET kept_amount = ?, short_amount = ?, edited_to = ? "
            "WHERE id = ? AND household_id = ?",
            (edit["kept_amount"], edit["short_amount"], edit["edited_to"], item_id, household_id()),
        )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "quantity": new_quantity, "category": new_category, "found": True}


def remove_grocery_item(item_id: int) -> dict:
    """
    Take an item off the grocery list. A hard delete — except for a
    still-needed line a staple put there, which is soft-removed and counts
    as "not this trip" (see below); a bought staple line is deleted like
    anything else.
    """
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    row = conn.execute(
        "SELECT id, staple_id, status, removed_by FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is not None and row["staple_id"] and row["status"] == "needed":
        # Removing a staple's suggestion is "not this trip" — otherwise the
        # next list read would put it straight back. Soft-removed rather
        # than deleted, so the staple's own Undo can restore it.
        from . import staples as _staples
        _staples.note_line_removed(conn, row, how="skip")
        conn.execute(
            "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
            "WHERE id = ? AND household_id = ? AND status != 'removed'",
            (_staples.ADDED_BY_STAPLE, item_id, household_id()),
        )
        conn.commit()
        conn.close()
        return {"item_id": item_id, "deleted": True, "staple_id": row["staple_id"]}
    if row is not None and row["status"] == "removed" and row["removed_by"] == REMOVED_BY_LIST:
        # Already off by an earlier Remove (a double tap, a second screen):
        # a no-op. Falling through to the hard delete took the row and its
        # meal links with it, and the first toast's Undo had nothing left.
        conn.close()
        return {"item_id": item_id, "deleted": True, "restorable": True}
    if row is not None and row["status"] == "needed" and conn.execute(
        "SELECT 1 FROM meal_plan_grocery_links WHERE grocery_item_id = ? AND household_id = ? LIMIT 1",
        (item_id, household_id()),
    ).fetchone():
        # A line with meals behind it is soft-removed, so the toast's Undo
        # (undo_remove_grocery_item) can put back the very row. A hard
        # delete cascades its meal_plan_grocery_links away, and the undo
        # could only re-add the NAME — a person's own line, no meals on it:
        # it no longer shrank when a night was dropped, never cleared when
        # the week was over, and soaked up next week's amount (measured
        # 2026-10-10: spinach stayed "10 oz" with both frittatas gone).
        # Keeping the links also keeps a re-approval from putting back a
        # line the household took off — the pre-shop drop's rule.
        conn.execute(
            "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
            "WHERE id = ? AND household_id = ?",
            (REMOVED_BY_LIST, item_id, household_id()),
        )
        conn.commit()
        conn.close()
        return {"item_id": item_id, "deleted": True, "restorable": True}
    conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id()))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "deleted": True}


# removed_by for a line a person took off with Remove (remove_grocery_item)
# while meals still wanted it. Not an "already have" decision, so
# pre_shop.get_already_have_decisions leaves it out.
REMOVED_BY_LIST = "list"


def undo_remove_grocery_item(item_id: int) -> dict:
    """
    The Undo on Remove's toast, for a line remove_grocery_item soft-removed:
    the same row back on the list, its store, its meals' links and its
    author intact. A night dropped or swapped in between is already
    reflected (the meal reversal keeps a 'list' row in step); a week's line
    with no meal links left has nothing to buy for, and stays off
    ({"restored": False}). Only a 'list' removal is
    restored here — a pre-shop or staple removal has its own undo.
    """
    with write() as conn:
        require_household_row(conn, "grocery_items", item_id, label="grocery list item")
        row = conn.execute(
            "SELECT id, item, status, removed_by, source_weekly_plan_id FROM grocery_items "
            "WHERE id = ? AND household_id = ?",
            (item_id, household_id()),
        ).fetchone()
        if row["status"] != "removed" or row["removed_by"] != REMOVED_BY_LIST:
            return {"item_id": item_id, "item": row["item"], "restored": False}
        if row["source_weekly_plan_id"] is not None and not conn.execute(
            "SELECT 1 FROM meal_plan_grocery_links WHERE grocery_item_id = ? AND household_id = ? LIMIT 1",
            (item_id, household_id()),
        ).fetchone():
            return {"item_id": item_id, "item": row["item"], "restored": False}
        conn.execute(
            "UPDATE grocery_items SET status = 'needed', removed_by = '', removed_at = NULL "
            "WHERE id = ? AND household_id = ?",
            (item_id, household_id()),
        )
        # No recount here: the meal reversal kept the row in step while it
        # was off. Re-reading it on the way back made a jar "2 jars" and
        # put the meals' "10 oz" over an amount the household had edited.
    return {"item_id": item_id, "item": row["item"], "restored": True}


def move_grocery_item_to_inventory(item_id: int) -> dict:
    """
    For a grocery list item the household realizes they already have on
    hand (turns out there's already a box in the pantry, a bag in the
    freezer, etc.) — not the get_grocery_already_have_items cross-reference
    case, which only catches items inventory already happens to know
    about, but the "oh wait, I actually have this" moment on any item,
    known to inventory or not. Adds it straight to pantry/fridge inventory
    (merging into a matching existing row the same way _add_to_inventory
    always does) carrying over its grocery-list quantity and category, then
    soft-removes it from the grocery list (status='removed',
    removed_by='already_have') — same soft-delete philosophy as
    drop_grocery_item_pre_shop, rather than a hard delete, so the Review
    screen's confirmation section (get_already_have_decisions) can list
    and undo the decision with undo_pre_shop_drop like any other pre-shop
    drop. If undo_pre_shop_drop restores this item, it also needs to
    decide what to do with the inventory row just written: when there was
    no existing stock to merge into (a brand-new inventory_items row), it's
    unambiguously safe to delete on undo, so that row's id is recorded on
    already_have_inventory_id below; when it merged into existing stock,
    reversing it would need the pre-merge quantity, which nothing tracks
    (deliberately — this followed the "inventory is deferred as policy"
    call of 2026-09-01, reversed 2026-10-25), so that case is left for undo to leave inventory alone.
    Raises ValueError if the item isn't found.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    # Same match _add_to_inventory itself will use (no location passed
    # below) — checked first so we know, after the fact, whether its write
    # was a fresh insert or a merge into this pre-existing row.
    pre_existing = conn.execute(
        "SELECT id FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
        (household_id(), row["item"]),
    ).fetchone()
    conn.close()

    inventory_result = _inventory._add_to_inventory(
        row["item"],
        row["quantity"] or "",
        source="grocery_list_already_have",
        category=row["category"] or None,
    )
    fresh_inventory_id = None if pre_existing else inventory_result.get("item_id")

    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = 'already_have', "
        "removed_at = datetime('now'), already_have_inventory_id = ? "
        "WHERE id = ? AND household_id = ?",
        (fresh_inventory_id, item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {
        "item_id": item_id,
        "item": row["item"],
        "moved_to_inventory": True,
        "inventory_item_id": inventory_result.get("item_id"),
    }
