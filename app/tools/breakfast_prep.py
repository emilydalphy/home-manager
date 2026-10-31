"""
Which breakfasts can be made on a prep day, and which have to be made fresh.

Emily, 2026-10-26, looking at Today: Avocado and Egg White Toast "Cooking
for 6 — enough for Monday, Tuesday, and Thursday." Toast and a cooked egg
are not something anyone makes three days ahead. "We should add an option
in the rhythm of if the user wants breakfast to be made fresh or if it
should be made ahead during the meal prep session. If it's made ahead, it
should be something that can be easy to keep in the fridge. For example,
egg bites, overnight oats, chia puddings."

Two halves:

  rhythm.breakfast_prep  the household's answer — 'fresh' (made each
                         morning) or 'make_ahead' (made on the prep day).
                         Unset is neither: the planner uses its judgement.
  breakfast_keeps(name)  whether a dish is one that keeps in the fridge.

cook_ahead.apply_prep_day_batches asks may_batch_breakfast before it
batches a breakfast, so a fresh household's breakfasts are never batched
and a fresh-only dish (toast, a fried egg) is never batched for anyone.
The planner prompt (agent.py) carries the same rule, so the week picks
the right kind of breakfast in the first place.

How long a make-ahead breakfast keeps (Emily, 2026-10-31, on her draft's
egg bites Sunday and Monday and scrambled eggs the other five mornings:
"I'll make the egg bites on the Sunday then can eat them Monday -
Wednesday while they're fresh. Then I can make the other days fresh."):
KEEPS_DAYS below, read by mornings_after. prep_freshness.py puts the batch
on the mornings right after the prep day, inside that window, and
cook_ahead.apply_prep_day_batches batches it no further.

The check reads the dish's NAME. Recipes carry no "keeps" field, and the
name is what says it: "Egg Bites" keep, "Scrambled Eggs" don't. A make-
ahead word wins over a fresh one ("Overnight Oats with Toasted Seeds",
"Breakfast Burrito with Scrambled Egg" — a burrito is wrapped and
reheated). A name with neither is left as it was: batched as before.
"""
from __future__ import annotations

import re

BREAKFAST_PREP_OPTIONS = ("fresh", "make_ahead")

# Made on a prep day and eaten cold or reheated over the next few days.
MAKE_AHEAD_WORDS = (
    "bite", "egg muffin", "egg cup", "frittata", "quiche", "strata",
    "casserole", "breakfast bake", "egg bake", "overnight",
    "overnight oat", "baked oat", "baked oatmeal", "oatmeal cup", "oat cup", "oat bar",
    "chia", "pudding", "parfait", "bircher",
    "muffin", "banana bread", "zucchini bread", "loaf", "scone",
    "breakfast burrito", "burrito", "breakfast sandwich", "wrap",
    "granola", "energy bite", "energy ball", "breakfast cookie", "bar",
    "hard-boiled", "hard boiled", "jammy egg",
    "freezer", "make-ahead", "make ahead", "meal prep",
)

# Only good the moment they're made.
FRESH_WORDS = (
    "toast", "scrambled", "fried egg", "sunny", "over easy", "poached",
    "soft-boiled", "soft boiled", "omelet", "omelette", "benedict",
    "shakshuka", "huevos", "eggs in", "egg in a hole", "crepe", "crêpe",
    "pancake", "waffle", "french toast", "smoothie", "avocado",
    "bagel", "english muffin",
)


def _has(name: str, words: tuple[str, ...]) -> bool:
    # Whole words (a plural is fine), so "bar" is a breakfast bar and not
    # barley, and "bite" is egg bites and energy bites.
    text = re.sub(r"\s+", " ", (name or "").lower())
    return any(re.search(r"\b" + re.escape(w) + r"(e?s)?\b", text) for w in words)


def breakfast_keeps(name: str) -> bool | None:
    """
    True: made ahead, keeps in the fridge. False: made fresh only.
    None: the name doesn't say either way.

    "english muffin" is a fresh word and "muffin" a make-ahead one, so an
    English muffin sandwich is checked first.
    """
    lowered = (name or "").lower()
    if "english muffin" in lowered and not _has(lowered.replace("english muffin", ""), MAKE_AHEAD_WORDS):
        return False
    if _has(lowered, MAKE_AHEAD_WORDS):
        return True
    if _has(lowered, FRESH_WORDS):
        return False
    return None


def may_batch_breakfast(name: str, breakfast_prep: str | None) -> bool:
    """
    Can one cook of this breakfast cover later mornings?

    'fresh'      never — every breakfast is made that morning.
    'make_ahead' or unset: yes, unless the dish only works fresh.
    """
    if breakfast_prep == "fresh":
        return False
    return breakfast_keeps(name) is not False


# How many days a make-ahead breakfast keeps in the fridge, counting the
# day it's made (the Loop Board card's own table, 2026-10-31). First match
# wins, so the shorter keeps come first: an "Overnight Oats Parfait" is a
# parfait. A make-ahead name with none of these keeps DEFAULT_KEEPS_DAYS.
KEEPS_DAYS = (
    (("parfait",), 2),
    (("overnight", "overnight oat", "bircher"), 3),
    (("bite", "egg muffin", "egg cup", "frittata", "quiche", "strata", "casserole",
      "breakfast bake", "egg bake"), 4),
    (("baked oat", "baked oatmeal", "oatmeal cup", "oat cup", "oat bar"), 4),
    (("muffin", "banana bread", "zucchini bread", "loaf", "scone"), 4),
    (("chia", "pudding"), 4),
    (("breakfast burrito", "burrito", "breakfast sandwich", "wrap"), 4),
    (("hard-boiled", "hard boiled", "jammy egg"), 4),
)
DEFAULT_KEEPS_DAYS = 4


def keeps_days(name: str) -> int:
    """Days this breakfast keeps in the fridge, counting the day it's made."""
    for words, days in KEEPS_DAYS:
        if _has(name, words):
            return days
    return DEFAULT_KEEPS_DAYS


def mornings_after(name: str) -> int:
    """
    How many mornings after the prep day a batch of this breakfast covers:
    egg bites made Sunday evening are Monday, Tuesday and Wednesday (3);
    overnight oats Monday and Tuesday (2).

    Never past leftovers.MAX_LEFTOVER_DAYS, the food-safety default every
    other batch already keeps (cook_ahead._within_three_days): a muffin
    that would keep a week still isn't a week of one breakfast.
    """
    from .leftovers import MAX_LEFTOVER_DAYS

    return max(0, min(keeps_days(name) - 1, MAX_LEFTOVER_DAYS))
