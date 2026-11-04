"""
No breakfast on more than three mornings of a week, unless the household
asked for the same ones.

Emily, 2026-10-31, phone test (draft week Nov 1–7): Veggie Scrambled Eggs
Tuesday to Saturday — "It's giving me a lot of repeats … We need to get
better parameters set in place." The prompt asks for a breakfast to repeat
"two or three times a week" and the count pass folds the week's breakfasts
to the household's number, but nothing ever looked at how the mornings were
SHARED between those dishes: three breakfasts can be five mornings, one and
one.

What it does: a breakfast on more than its cap of mornings keeps that many,
spread across the week (a cook a reheat eats from is kept first, so a batch
is never left feeding nothing), and each other morning gets —
  1. another of the week's own breakfasts with room, not already that day
     (nothing new to buy); else
  2. while the week has room for another breakfast dish under the
     household's count, a breakfast from Pomona's recipe library — a dish
     whose full recipe is already written there (recipe_library.
     base_recipe), copied to this household's recipes, after the same
     allergen and taste gate a swap passes (swap_in_place.pick_gate), a
     standing dislike and the diet words (everyday_snacks.diet_excludes),
     and never one from the last two weeks. No model call: the library IS
     the "before inventing" the card asks for, and an unanswered count is
     never a reason to spend model calls (the count pass's own rule); else
  3. the repeat, left as it was and logged — better a repeat than an empty
     morning (a breakfast is never handed back as a question).

THE CAP is MAX_BREAKFAST_MORNINGS, widened to what the household's own count
needs when that count is THEIR answer (meal_counts_set): "A go-to or two"
(2 breakfasts) over seven mornings is four mornings each, and that is them
asking for the same breakfasts. It stands down entirely for words that say
so ("same breakfast every day") and for a household whose stored answer is
the same breakfasts (memory["same_breakfasts"] — the hook for the "should
breakfasts change every week?" setting, decided 2026-10-31 as "ask each
household"; until that setting exists nobody has it and the cap applies).
A breakfast they asked for by name, a cooked morning and their own words
(meal_variety.theirs) are never moved.

Never raises.
"""
from __future__ import annotations

import functools
import json
import logging
import math
import random
import re
from pathlib import Path

from . import leftovers as _leftovers
from . import meal_variety as _meal_variety

logger = logging.getLogger("home_manager")

# Emily's card, 2026-10-31.
MAX_BREAKFAST_MORNINGS = 3

VARIETY_CONSTRAINT = "breakfast_variety"

# How many library breakfasts the drafting prompt is handed.
LIBRARY_PICKS = 12

_SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "recipe_library_seed.json"

# "same breakfast every day", "the same breakfasts all week", "breakfast can
# repeat". Narrow on purpose, like snack_variety's: "breakfast every day" is
# when, not which.
_SAME_BREAKFAST = re.compile(
    r"\bsame\s+(?:\w+\s+){0,2}breakfasts?\b"
    r"|\bbreakfasts?\s+(?:can|may|could|are\s+fine\s+to)\s+(?:be\s+the\s+same|repeat)"
    r"|\brepeat(?:ing|ed)?\s+(?:the\s+|our\s+)?breakfasts?\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:not|no|never|don'?t|doesn'?t|isn'?t|without)\b|n't\b", re.IGNORECASE)


def asks_for_same_breakfasts(*texts: str | None) -> bool:
    """Do these words say the same breakfasts all week is what they want?"""
    for text in texts:
        for m in _SAME_BREAKFAST.finditer(text or ""):
            before = (text or "")[max(0, m.start() - 20):m.start()]
            if not _NEGATION.search(before):
                return True
    return False


@functools.lru_cache(maxsize=1)
def _library_breakfasts() -> tuple[str, ...]:
    try:
        with open(_SEED_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return ()
    return tuple(d["dish_name"] for d in raw.get("dishes") or []
                 if d.get("meal") == "breakfast" and d.get("dish_name"))


def library_breakfasts(seed: str | int | None = None, avoid=(), limit: int = LIBRARY_PICKS) -> list[str]:
    """Up to `limit` breakfasts from the recipe library's starter list
    (data/recipe_library_seed.json, meal "breakfast"), none of `avoid` (by
    repeat_key), in an order fixed by `seed` — one week asks the same way
    twice, two weeks don't ask the same way."""
    refused = {_meal_variety.repeat_key(n) for n in avoid}
    names = [n for n in _library_breakfasts() if _meal_variety.repeat_key(n) not in refused]
    random.Random(str(seed)).shuffle(names)
    return names[:limit]


def cap_for(memory: dict, mornings: int) -> int | None:
    """The most mornings one breakfast may hold, or None when the household
    chose the same breakfasts (see the module docstring)."""
    if memory.get("same_breakfasts"):
        return None
    cap = MAX_BREAKFAST_MORNINGS
    count = int(memory.get("breakfasts_per_week") or 0)
    if memory.get("meal_counts_set") and count > 0:
        cap = max(cap, math.ceil(mornings / count))
    return cap


def _spread_keep(nights: list[dict], cap: int, sources: set[int]) -> set[int]:
    """Which of a dish's mornings stay: every cook a reheat eats from, then
    as evenly spaced as they go, up to `cap`."""
    keep = {n["id"] for n in nights if n["id"] in sources}
    rest = [n for n in nights if n["id"] not in keep]
    room = min(max(0, cap - len(keep)), len(rest))
    if room == 1:
        keep.add(rest[0]["id"])
    elif room > 1:
        keep |= {rest[round(i * (len(rest) - 1) / (room - 1))]["id"] for i in range(room)}
    return keep


# Words in a library recipe that a diet keeps off the plate, by
# everyday_snacks' diet kinds. Plain and short, like that module's lists.
_ANIMAL_WORDS = {
    "meat": ("bacon", "ham", "sausage", "chorizo", "prosciutto", "salami", "chicken", "beef", "pork",
             "turkey", "lamb", "pancetta", "machacado", "steak"),
    "fish": ("salmon", "tuna", "fish", "shrimp", "anchov", "lox", "cod", "crab", "sardine", "mackerel"),
    "egg": ("egg",),
    "dairy": ("milk", "cheese", "butter", "yogurt", "yoghurt", "cream", "ricotta", "feta", "paneer", "ghee"),
    "honey": ("honey",),
}


def _words_of(entry: dict) -> str:
    return " ".join([entry.get("dish_name") or ""] + [str(i.get("item") or "") for i in entry.get("ingredients") or []
                                                      if isinstance(i, dict)]).lower()


def _diet_refuses(entry: dict, excludes: set[str]) -> bool:
    text = _words_of(entry)
    return any(re.search(rf"\b{w}", text) for kind in excludes for w in _ANIMAL_WORDS.get(kind, ()))


def _library_pick(night: dict, on_week: list[str], memory: dict, recent: list[str],
                  refused: set[str]) -> str | None:
    """A library breakfast for this morning, saved to the household's own
    recipes if it isn't there yet — or None. Step 2 of the docstring."""
    from . import everyday_snacks as _everyday
    from . import recipe_library as _recipe_library
    from . import recipes as _recipes
    from . import swap_in_place as _swap

    from . import breakfast_prep as _breakfast_prep
    from . import rhythm as _rhythm

    from . import library_first as _library_first
    # The one diet check every library copy asks (2026-11-03): a diet the
    # gate can't read (vegetarian, halal, kosher…) or a style the code can't
    # read is never copied a library dish; the word lists below are a
    # second net, not the first.
    if _everyday.style_stands_down(memory) or _library_first.diet_blocks_the_library(memory.get("eating_style")):
        return None
    # A make-ahead household's breakfasts are made on the prep day, so a
    # dish only good fresh (toast, a fried egg) is not theirs to be given
    # (breakfast_prep, Emily 2026-10-26).
    make_ahead = (_rhythm.get_household_rhythm() or {}).get("breakfast_prep") == "make_ahead"
    excludes = _everyday.diet_excludes(memory)
    dislikes = memory.get("dislikes") or []
    for name in library_breakfasts(f"{night['date']}:{night['id']}", avoid=on_week + recent, limit=1000):
        key = _meal_variety.repeat_key(name)
        if key in refused:
            continue
        refused.add(key)
        if make_ahead and _breakfast_prep.breakfast_keeps(name) is False:
            continue
        entry = _recipe_library.base_recipe(name)
        if not entry or _diet_refuses(entry, excludes) or _everyday.disliked(_words_of(entry), dislikes):
            continue
        existing = _recipes.existing_recipe_named(name)
        ingredients = (_recipes.saved_ingredients(name) if existing else None) or entry["ingredients"]
        if _swap.pick_gate({"meal_name": name, "ingredients": ingredients},
                           {"date": night["date"], "slot": "breakfast"}):
            continue
        if not existing:
            saved = _recipes.add_recipe(
                name=name, ingredients=entry["ingredients"], instructions=entry["instructions"],
                default_servings=entry["default_servings"] or 4, cuisine=entry["cuisine"] or "",
                main_protein=entry["main_protein"] or "", prep_time_minutes=entry["prep_time_minutes"],
                cook_time_minutes=entry["cook_time_minutes"],
            )
            recipe_id = (saved or {}).get("id") or (saved or {}).get("recipe_id")
            if recipe_id:
                _recipe_library._link(recipe_id, entry["id"])
            _recipe_library.record_use(name, "recipe")
        return name
    return None


def enforce_breakfast_spread(plan_id: int, dates: list[str], memory: dict, period_start: str | None = None,
                             asks: tuple[str | None, ...] = ()) -> dict:
    """See the module docstring. No model call, so it runs on both count
    runs. Returns {"moved", "library", "left", "skipped", "cap"}."""
    from . import draft_opener as _draft_opener
    from . import weekly_plan as _weekly_plan

    out = {"moved": [], "library": [], "left": [], "skipped": None, "cap": None}
    if not dates:
        return out
    if asks_for_same_breakfasts(*asks, memory.get("notes"), memory.get("typical_week"),
                                memory.get("next_week_notes")):
        out["skipped"] = "they said the same breakfasts are fine"
        return out
    try:
        in_scope = set(dates)
        chains = _leftovers.plan_leftover_chains(plan_id)
        entries = [e for e in _meal_variety._load_slot_entries(plan_id, "breakfast")
                   if e["date"] in in_scope and e["slot_state"] == "planned" and e["meal"]]
        cap = cap_for(memory, len(entries))
        out["cap"] = cap
        if cap is None:
            out["skipped"] = "they chose the same breakfasts"
            return out
        dishes = _meal_variety._group_dishes(entries, chains)
        for dish in dishes:
            dish["days"] = {n["date"] for n in dish["nights"]}
            if not dish["protected"] and _meal_variety.asked_for_by_name(dish["name"], asks):
                dish["protected"] = True
        over = [d for d in dishes if not d["protected"] and len(d["nights"]) > cap]
        if not over:
            return out

        sources = set(chains["sources"])
        surplus: list[tuple[dict, dict]] = []
        for dish in over:
            keep = _spread_keep(dish["nights"], cap, sources)
            for night in dish["nights"]:
                if night["id"] not in keep:
                    surplus.append((night, dish))
                    dish["days"].discard(night["date"])

        count = int(memory.get("breakfasts_per_week") or 0)
        counted = bool(memory.get("meal_counts_set")) and count > 0
        recent = sorted(_draft_opener.recent_dish_names(period_start, plan_id) or ()) if period_start else []
        refused: set[str] = set()

        def _week_pick(date: str) -> dict | None:
            fits = [d for d in dishes if date not in d["days"] and len(d["days"]) < cap]
            return min(fits, key=lambda d: (len(d["days"]), d["name"])) if fits else None

        for night, old in sorted(surplus, key=lambda s: (s[0]["date"], s[0]["id"])):
            pick = _week_pick(night["date"])
            name = pick["name"] if pick else None
            if pick is None and (not counted or sum(1 for d in dishes if d["days"]) < count):
                name = _library_pick(night, [d["name"] for d in dishes], memory, recent, refused)
                if name:
                    pick = {"name": name, "nights": [], "days": set(), "protected": False, "food_groups": None}
                    dishes.append(pick)
                    out["library"].append(name)
            if pick is None:
                # Better the repeat than an empty morning or a guess.
                old["days"].add(night["date"])
                out["left"].append({"date": night["date"], "meal": night["meal"]})
                continue
            _weekly_plan._replace_slot_entries(
                plan_id, [night["id"]], night["date"], "breakfast", name,
                food_groups=pick.get("food_groups"), reasoning="",
                derived_from={"constraint": VARIETY_CONSTRAINT, "replaced": night["meal"]},
            )
            pick["days"].add(night["date"])
            out["moved"].append({"date": night["date"], "dropped": night["meal"], "with": name})
        if out["moved"] or out["left"]:
            logger.info("Plan %s breakfast spread (cap %d mornings): moved %d (%d from the library), left %d",
                        plan_id, cap, len(out["moved"]), len(out["library"]), len(out["left"]))
    except Exception:
        logger.exception("Breakfast spread failed for plan %s; the breakfasts stand as planned", plan_id)
    return out
