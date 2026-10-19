"""
Sauces and pantry staples: assumed at home, like the spice rack (Loop
Board "pantry staples are assumed at home like spices, and I tick the ones
I need to buy", Emily, 2026-10-18).

    "the grocery list doesn't make me buy what's almost always in the
    cupboard. I see them in one short list for the week, assume I have
    them, and tick only the ones I'm out of."

This module is ONLY the classifier — the maintained lists of names, one
line per name so Emily can argue with any of them. Everything else is the
spices machinery, unchanged in kind: a recipe's soy sauce or basmati rice
lands as a grocery line with status 'spice' (which reads "assumed at home,
waiting in its section", whatever the section), off every count, merge,
sort and trip until it is ticked (spices.tick_spice), and the section it
shows under is spices.assumed_section(). A second status for the same
state machine would have meant teaching every query in grocery.py a new
word, and the list already reads 'spice' as exactly this.

Three answers, never mixed up:
  * is_sauce / is_pantry_staple — assumed; the list shows them under
    "Sauces this week" / "Pantry staples this week".
  * is_canned — NOT assumed (Emily: "canned is not a staple"); an ordinary
    line, filed in its own "canned" aisle so the tins sit together.
  * anything fresh, chilled or frozen — never assumed, whatever the name
    (a "fresh" or "frozen" word anywhere in it wins over every list).

Matching: the name before any comma or parenthesis ("Soy sauce, low
sodium", "Rice (basmati)"), lowercased, every word singularised, hyphens
as spaces, chilli spellings folded. An entry matches when the name ENDS
with it as whole words, so "basmati rice", "toasted sesame seeds" and
"low-sodium chicken broth" match "rice", "sesame seed" and "broth" — and
"almond milk" or "rice paper" match nothing, because the thing a food
name is about is its last word.
"""
from __future__ import annotations

import re

from . import grocery as _grocery


# ---------------------------------------------------------------- sauces ----
_SAUCES = [
    # everyday
    "ketchup",
    "mustard",              # yellow, Dijon, grainy — not mustard seed or greens (last word)
    "dijon",
    "mayonnaise",
    "mayo",
    "hot sauce",
    "sriracha",
    "worcestershire sauce",
    "worcestershire",
    "bbq sauce",
    "barbecue sauce",
    "salsa",
    "pasta sauce",
    "marinara sauce",
    "marinara",
    "soy sauce",            # light, dark, low-sodium — one item (see canonical_name)
    "tamari",
    # Asian
    "fish sauce",
    "oyster sauce",
    "hoisin sauce",
    "hoisin",
    "sweet chili sauce",
    "gochujang",
    "miso",
    "miso paste",
    "sambal oelek",
    "sambal",
    "chili crisp",
    "chili oil",
    "shaoxing wine",
    "shaoxing",
    "shaoxing rice wine",
    "rice wine",
    "chinese cooking wine",
    "mirin",
    "vinegar",              # rice, white, apple cider, balsamic, red wine, any vinegar
    # Middle Eastern & Mediterranean
    "tahini",
    "harissa",
    "harissa paste",
    "pomegranate molasses",
    # Indian
    "tamarind",
    "tamarind paste",
    "tamarind concentrate",
    "curry paste",          # red, green, Thai — any curry paste
    "mango chutney",
    # pastes
    "tomato paste",
    "ginger garlic paste",
    "garlic ginger paste",
]

# ------------------------------------------------------- pantry staples ----
_PANTRY = [
    # grains & pasta
    "rice",                 # white, basmati, jasmine, brown, sona masoori
    "pasta",
    "dried pasta",
    "spaghetti",
    "penne",
    "fusilli",
    "rigatoni",
    "linguine",
    "fettuccine",
    "macaroni",
    "orzo",
    "farfalle",
    "rotini",
    "lasagna noodle",
    "lasagna sheet",
    "egg noodle",
    "rice noodle",
    "rice vermicelli",
    "quinoa",
    "couscous",
    "bulgur",
    "oat",                  # rolled, quick, steel-cut
    "oatmeal",
    "cornmeal",
    "semolina",
    "sooji",
    "suji",
    "rava",
    "poha",
    # dals, lentils & dried beans
    "dal",                  # toor, moong, masoor, chana, urad (daal / dhal fold to dal)
    "lentil",
    "dried chickpea",
    "dried kidney bean",
    "dried black bean",
    # stock
    "stock",
    "broth",
    "stock cube",
    "bouillon",
    "bouillon cube",
    # flours & thickeners
    "flour",                # bare: all-purpose
    "all purpose flour",
    "plain flour",
    "whole wheat flour",
    "atta",
    "bread flour",
    "cornstarch",
    "corn starch",
    "cornflour",
    "breadcrumb",
    "bread crumb",
    "panko",
    # sweeteners (molasses is NOT here — Emily: a normal purchase)
    "sugar",                # white, brown, icing
    "honey",
    "maple syrup",
    "jaggery",
    # baking
    "baking soda",
    "bicarbonate of soda",
    "baking powder",
    "vanilla extract",
    "vanilla essence",
    "cocoa powder",
    "cocoa",
    "chocolate chip",
    "yeast",
    # nuts & seeds
    "almond",
    "cashew",
    "walnut",
    "peanut",
    "pine nut",
    "pecan",
    "sesame seed",
    "sesame",
    "chia seed",
    "chia",
    "flax seed",
    "flaxseed",
    "linseed",
    "pumpkin seed",
    "pepita",
    "sunflower seed",
    # nut butters
    "peanut butter",
    "almond butter",
    "cashew butter",
    # dried fruit
    "raisin",
    "date",
    "dried cranberry",
    "craisin",
    # fats that keep in the cupboard (the cooking oils sit with the spices)
    "ghee",
    "coconut oil",
    # drinks
    "coffee",
    "tea",
]

# Not assumed, though the last word is on a list above (review,
# 2026-10-19): a specialty version a kitchen does not just have. Emily's
# list names the everyday kind — white and brown sugar, all-purpose flour,
# everyday rice — so these are bought as normal. Matched the same way (the
# name ends with one of these).
_NOT_ASSUMED = [
    # flours
    "almond flour", "coconut flour", "chickpea flour", "gram flour", "besan", "oat flour",
    "rice flour", "self raising flour", "self rising flour", "cake flour", "tapioca flour",
    "buckwheat flour", "spelt flour", "gluten free flour",
    # rice
    "arborio rice", "wild rice", "sushi rice", "black rice", "glutinous rice", "sticky rice",
    "paella rice", "carnaroli rice",
    # sugar
    "coconut sugar", "caster sugar", "superfine sugar", "powdered sugar", "icing sugar",
    "confectioner sugar", "confectioners sugar", "pearl sugar", "sanding sugar", "demerara sugar",
    "turbinado sugar",
    # pasta
    "lentil pasta", "chickpea pasta", "gluten free pasta", "bean pasta",
    # drinks
    "iced tea", "cold brew", "cold brew coffee", "bubble tea", "chai concentrate",
    # baking & sweeteners
    "nutritional yeast", "hot honey", "honeycomb",
]

# ------------------------------------------------------------- canned ----
# Bought as normal, in their own aisle. A bare "chickpeas" or "black beans"
# in a recipe is the tin (a dried one says "dried" — _PANTRY above).
_CANNED = [
    "diced tomato",
    "crushed tomato",
    "whole peeled tomato",
    "peeled tomato",
    "coconut milk",
    "coconut cream",
    "tuna",
    "chickpea",
    "garbanzo bean",
    "black bean",
    "kidney bean",
    "cannellini bean",
    "pinto bean",
    "navy bean",
    "white bean",
    "butter bean",
    "great northern bean",
    "baked bean",
    "refried bean",
]

# A tuna the tin never held: sashimi-grade, a steak. Bought, not canned.
_FRESH_FISH = ["ahi tuna", "yellowfin tuna", "bluefin tuna", "sashimi tuna", "sushi grade tuna"]

# Words that make anything a real purchase, whatever list its name is on:
# fresh, chilled or frozen food is bought every time ("fresh pasta",
# "frozen peas"); a tin is bought as normal (and filed under canned). And
# the two vegetables that turn "rice" into produce.
_FRESH_WORDS = {"fresh", "frozen", "chilled", "refrigerated", "cauliflower", "broccoli"}
_TIN_WORDS = {"canned", "tinned", "can", "tin"}

_CHILI = {"chili", "chilli", "chile", "chilies", "chillies", "chiles", "chilis"}
_FOLD = {"daal": "dal", "dhal": "dal", "dahl": "dal", "dry": "dried", "rawa": "rava"}


def _words(text: str) -> list[str]:
    """Lowercased words, each singularised and folded — the one normal form
    both the lists and a recipe's names go through."""
    out = []
    for w in re.findall(r"[a-z']+", (text or "").lower()):
        w = "chili" if w in _CHILI else _FOLD.get(w, w)
        w = _grocery._singular_word(w)
        out.append(_FOLD.get(w, w))
    return out


def _head(name: str) -> list[str]:
    """The thing itself: the name before a comma, a parenthesis or a "for
    serving" tail — "Soy sauce, low sodium" is soy sauce."""
    text = re.split(r"[,(]", (name or "").lower(), maxsplit=1)[0]
    text = re.split(r"\b(?:for|to)\b", text, maxsplit=1)[0]
    return _words(text)


# Built on first use, not at import: grocery (whose singulariser the
# lists go through) is still half-initialised when this module is imported
# along the package's grocery -> weekly_plan -> recipes -> spices cycle.
_KEYS: dict[str, list[tuple]] = {}


def _keys(kind: str) -> list[tuple]:
    if kind not in _KEYS:
        names = {"sauce": _SAUCES, "pantry": _PANTRY, "canned": _CANNED, "not": _NOT_ASSUMED,
                 "fresh_fish": _FRESH_FISH}[kind]
        _KEYS[kind] = [tuple(_words(n)) for n in names]
    return _KEYS[kind]


def _ends_with_any(words: list[str], keys) -> bool:
    return any(len(k) <= len(words) and tuple(words[-len(k):]) == k for k in keys)


def _never(name: str) -> bool:
    every = set(_words(name))
    if every & _FRESH_WORDS or every & _TIN_WORDS:
        return True
    return _ends_with_any(_head(name), _keys("not"))


def is_sauce(name: str) -> bool:
    """A sauce, condiment, vinegar or paste the cupboard is assumed to have."""
    words = _head(name)
    return bool(words) and not _never(name) and _ends_with_any(words, _keys("sauce"))


def is_pantry_staple(name: str) -> bool:
    """A dry-goods staple the cupboard is assumed to have."""
    words = _head(name)
    return bool(words) and not _never(name) and _ends_with_any(words, _keys("pantry"))


def is_canned(name: str) -> bool:
    """A tin: bought as normal, filed in the canned aisle. Never one that
    says frozen or fresh, and never a dried one ("dried chickpeas" is a
    pantry staple)."""
    every = set(_words(name))
    if every & _FRESH_WORDS or "dried" in every:
        return False
    if every & _TIN_WORDS:
        return True
    words = _head(name)
    return bool(words) and _ends_with_any(words, _keys("canned")) and not _ends_with_any(words, _keys("fresh_fish"))


# Spellings that are one thing on the list: "light soy sauce", "dark soy
# sauce" and "tamari" are all the bottle of soy sauce (Emily, 2026-10-18).
_CANONICAL = [((("soy", "sauce"), ("tamari",), ("shoyu",)), "Soy sauce")]


def canonical_name(name: str) -> str:
    """The list's name for a recipe's spelling of a thing it treats as one
    item, or the name unchanged."""
    words = _head(name)
    if not words or _never(name):
        return name
    for keys, canon in _CANONICAL:
        if _ends_with_any(words, keys):
            return canon
    return name
