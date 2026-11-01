"""
The prep list — what a prep day is FOR, built from steps that fit the
household's time rather than gathered whole dishes.

Emily, 2026-11-01, looking at Sunday prep: it needs redesigning "both from
a design and interface perspective and the way it's computed". Until now a
session only GATHERED what was already dated on the prep day
(prep_sessions.py's docstring), so a repeated dish showed up only if its
first day happened to be Sunday, a prepped lunch could put shrimp on Sunday
for Tuesday, and "about 2 hours" was the stored 120 rather than a sum.

So a prep day is now a LIST OF STEPS:

  * Every meal the prep day reaches (after it, up to the next prep day,
    never more than six days out) is read for the steps worth doing ahead
    — "Rice, 4 cups dry", "2 onions, 8 oz mushrooms", "Orange-ginger glaze,
    1 jar", "Marinate the chicken thighs". The reading is DETERMINISTIC:
    keyword rules over the recipe's own ingredient lines (name + category)
    and instruction text. No model is called. A recipe that yields no step
    falls back to one whole-dish row (in the section its instructions say:
    oven, stove, slow cooker, or mix and chill), and a meal with no saved
    recipe yields nothing, because nothing about it is known.
  * Shared components are summed across meals: two dinners that both chop
    onions get one "2 onions" step that says what it feeds.
  * Each step carries HANDS-ON minutes and passive (oven/stove) minutes, and
    only hands-on minutes count against the household's prep time
    (prep_day["minutes"]; 120 when they never said).
  * Steps are picked by weeknight time saved per hands-on minute, steps
    shared by two or more meals first, filled greedily up to the budget.
    What did not fit goes under "Made on the night".
  * Some steps are never dropped: thaw moves (defrost.py's own rows, listed
    exactly where defrost dated them — never re-dated here) and marinating
    any meat. A marinade for a meal more than two days out is frozen in its
    bag, and this module books the thaw for the night before the meal
    (ensure_marinade_thaws — a defrost row of its own kind).
  * Some things are never prepped: seafood, cut avocado, cut cucumber or
    tomato, anything crispy or fried. They are listed under Made on the
    night with the reason ("Better fresh. Shrimp goes rubbery after a day in
    the fridge."). That includes a prepped-lunch BATCH whose dish is better
    fresh: it is not cooked on the prep day, its card says it is cooked on
    its own day (cooker._apply_prepped_lunches asks fresh_cook_entry_ids),
    and its non-fresh steps (the peppers, the sauce) are still offered.
  * The household can take a step off (×) and add anything from Made on the
    night back ("Add it"). Both are stored per plan + prep date in
    prep_list_marks, together with the done tick of a step that has no row
    of its own anywhere else. An add-back can go over the budget; the header
    then says by how much.

What is NOT a step to fit: the work the plan already put on the day — a
prepped-lunch batch, a cook-ahead chain cooked that day, a prep-cut the
household ticked, a batch component, a holiday's make-ahead, a general prep
row. Those are "pinned": they count against the time first and are only
ever off the list when the household takes them off.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id

# ---------------------------------------------------------------------------
# Sections, in the order the screen shows them (only non-empty ones show).

SECTIONS: list[tuple[str, str]] = [
    ("thaw", "Move to thaw"),
    ("soak", "Set to soak"),
    ("oven", "In the oven"),
    ("stove", "On the stove"),
    ("slow_cooker", "In the slow cooker"),
    ("chop", "Chop and wash"),
    ("sauces", "Sauces, marinades, drinks"),
    ("chill", "Mix and chill"),
    ("freezer", "Move to freezer"),
    ("special", "Special occasion prep"),
    ("pack", "Pack it up"),
]
SECTION_ORDER = [k for k, _ in SECTIONS]
SECTION_TITLES = dict(SECTIONS)
MADE_ON_THE_NIGHT = "Made on the night"

DEFAULT_BUDGET_MINUTES = 120
# The longest prep day the household can pick (60/120/180/240); a step that
# would need more than this is never told to "add" time.
MAX_BUDGET_MINUTES = 240

# How many days after the prep day each kind of prepped food still keeps in
# the fridge. A meal further out than this simply isn't offered the step.
KEEPS_CHOPPED = 4
KEEPS_GREENS = 3
KEEPS_GRAIN = 4
KEEPS_SAUCE = 5
KEEPS_WHOLE_DISH = 3
# Marinated raw meat keeps two days in the fridge; past that it is frozen in
# its bag on the prep day and thawed the night before.
MARINADE_FRIDGE_DAYS = 2
# The furthest a prep day reaches, whatever the next prep day.
REACH_DAYS = 6

MARINADE_THAW_KIND = "marinade_thaw"
MARKS_TABLE = "prep_list_marks"

# ---------------------------------------------------------------------------
# Keyword tables. Whole-word matches on the ingredient's own name.

_SEAFOOD_SHRIMP = ("shrimp", "prawn", "prawns", "scallop", "scallops")
_SEAFOOD_FISH = (
    "fish", "salmon", "cod", "tuna", "tilapia", "halibut", "trout", "mahi", "haddock",
    "snapper", "sea bass", "bass", "sole", "pollock", "swordfish", "catfish", "mackerel",
    "sardine", "sardines", "branzino",
)
_SEAFOOD_OTHER = ("crab", "lobster", "mussel", "mussels", "clam", "clams", "squid", "calamari", "octopus", "oyster", "oysters")
_MEAT = (
    "chicken", "beef", "pork", "lamb", "turkey", "steak", "steaks", "veal", "duck", "thigh", "thighs",
    "breast", "breasts", "drumstick", "drumsticks", "wings", "brisket", "ribs", "chops", "tenderloin",
    "sirloin", "flank", "skirt", "short rib", "shoulder", "venison", "goat", "tofu", "tempeh",
)
# Things that are never prepped cut, said with why.
_FRESH_PRODUCE = {
    "avocado": "avocado", "avocados": "avocado", "guacamole": "avocado",
    "cucumber": "cucumber", "cucumbers": "cucumber",
    "tomato": "tomato", "tomatoes": "tomato",
}
# Hardy produce that keeps cut for days — "Chop and wash".
_HARDY = (
    "onion", "onions", "shallot", "shallots", "scallion", "scallions", "green onion", "green onions",
    "spring onion", "leek", "leeks", "garlic", "ginger", "carrot", "carrots", "celery",
    "bell pepper", "bell peppers", "pepper", "peppers", "mushroom", "mushrooms", "broccoli",
    "broccolini", "cauliflower", "cabbage", "kale", "brussels sprouts", "zucchini", "squash",
    "green beans", "green bean", "snap peas", "snow peas", "asparagus", "radish", "radishes",
    "fennel", "beet", "beets", "jalapeno", "jalapeño", "bok choy", "poblano",
)
_GREENS = ("lettuce", "romaine", "spinach", "arugula", "greens", "chard", "collard", "collards", "spring mix", "mesclun")
_NOT_FRESH_WORDS = (
    "powder", "dried", "ground", "flakes", "flake", "paste", "sauce", "oil", "juice", "canned",
    "frozen", "pickled", "jarred", "roasted red", "sun-dried", "sundried", "seed", "seeds", "salt",
)
# Grains worth cooking ahead, with the minutes they sit on the stove.
_GRAINS = (
    ("brown rice", 40), ("wild rice", 45), ("rice", 20), ("quinoa", 15), ("farro", 30),
    ("barley", 40), ("bulgur", 15),
)
_GRAIN_NOT = ("vinegar", "wine", "noodle", "noodles", "paper", "flour", "cake", "cakes", "milk", "syrup", "cracker", "crackers", "bran", "cereal")
# A dish whose grain is cooked IN it, not beside it.
_GRAIN_IN_DISH = ("risotto", "paella", "pilaf", "biryani", "jambalaya", "congee", "pudding", "soup", "stew", "casserole", "arroz")

_SAUCE_NOUNS = ("sauce", "glaze", "dressing", "vinaigrette", "salsa", "pesto", "chimichurri", "tzatziki", "aioli", "dip", "raita", "gremolata")
_SAUCE_VERBS = re.compile(r"\b(whisk|whisking|stir together|mix together|combine|blend|shake|make)\b")
_SAUCE_HEAT = re.compile(r"\b(simmer|boil|heat|saucepan|bubble)\b")
_MARINATE_RE = re.compile(r"\bmarinat", re.IGNORECASE)
_CRISPY_NAME = re.compile(r"\b(crispy|crisp|fried|tempura|katsu|schnitzel|fritters?|pakoras?|karaage|tostadas?|nachos|fries|chips)\b", re.IGNORECASE)
_CRISPY_STEP = re.compile(r"\b(deep[- ]fry|deep[- ]fried|until (?:golden and )?crisp[y]?|shallow[- ]fry|pan[- ]fry until crisp)\b", re.IGNORECASE)

_SLOW_RE = re.compile(r"\b(slow cooker|crock ?pot|instant pot|pressure cooker|multicooker)\b", re.IGNORECASE)
_OVEN_RE = re.compile(r"\b(oven|bake|baked|baking|roast|roasted|roasting|broil)\b", re.IGNORECASE)
_STOVE_RE = re.compile(r"\b(pan|skillet|pot|saucepan|simmer|boil|saut[eé]|stir[- ]fry|wok|griddle|stove)\b", re.IGNORECASE)

_SLOT_ORDER = ("breakfast", "lunch", "dinner", "snack")


# ---------------------------------------------------------------------------
# Small words.

def _words(text: str) -> str:
    """Lowercased, punctuation to spaces, padded, for whole-word `in` tests."""
    return " " + re.sub(r"[^a-z0-9ñé\- ]+", " ", (text or "").lower()) + " "


def _has_word(text_padded: str, phrase: str) -> bool:
    return f" {phrase} " in text_padded


def _name_only(item: str) -> str:
    """The ingredient's name before any ", diced" or "(Roma)"."""
    item = (item or "").strip()
    cut = min((i for i in (item.find("("), item.find(",")) if i >= 0), default=len(item))
    return item[:cut].strip()


_EXTRA_DESCRIPTORS = {"lean", "extra", "free-range", "thinly", "thick", "thin", "baby", "mini", "peeled", "trimmed"}


def display_name(item: str) -> str:
    """"Boneless skinless chicken thighs" -> "chicken thighs" — the name
    without the words that describe it, lowercased."""
    from . import quantities as _quantities
    drop = set(_quantities._DESCRIPTOR_WORDS) | _EXTRA_DESCRIPTORS
    words = [w for w in _name_only(item).lower().split() if w not in drop]
    return " ".join(words) or _name_only(item).lower()


def _singular_key(name: str) -> str:
    from . import grocery as _grocery
    return " ".join(_grocery._singular_word(w) for w in name.split())


def _day_short(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a")


def _day_long(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%A")


def _slot_rank(slot: str) -> int:
    return _SLOT_ORDER.index(slot) if slot in _SLOT_ORDER else len(_SLOT_ORDER)


def feeds_line(uses: list[dict]) -> str:
    """"Tue lunch, Wed dinner" — the meals a step feeds, in eating order.
    Days that share a meal of the day are said once: "Mon, Tue lunches"."""
    seen = sorted({(u["date"], u["slot"]) for u in uses if u.get("date")},
                  key=lambda p: (p[0], _slot_rank(p[1])))
    groups: list[tuple[str, list[str]]] = []
    for d, slot in seen:
        if groups and groups[-1][0] == slot:
            groups[-1][1].append(d)
        else:
            groups.append((slot, [d]))
    parts = []
    for slot, days in groups:
        word = slot or "dinner"
        if len(days) > 1:
            word = {"lunch": "lunches"}.get(word, word + "s")
        parts.append(", ".join(_day_short(d) for d in days) + " " + word)
    return ", ".join(parts)


def duration_words(minutes: int) -> str:
    """"1 hr 25" / "40 min" / "2 hr" — the header's short form."""
    minutes = max(int(round(minutes or 0)), 0)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} hr" + (f" {rest}" if rest else "")


def hours_phrase(minutes: int) -> str:
    """"2 hours" / "1 hour" / "1 hr 30" / "30 min" — the budget said in a
    sentence; a budget that isn't whole hours reads the header's way."""
    minutes = int(minutes or 0)
    if minutes and minutes % 60 == 0:
        h = minutes // 60
        return f"{h} hour" + ("" if h == 1 else "s")
    return duration_words(minutes)


def made_on_the_night_line(budget_minutes: int) -> str:
    return (f"These didn’t fit in your {hours_phrase(budget_minutes)}, so they’re cooked fresh "
            "on the day. Tap Add it to prep one now.")


def header_line(budget_minutes: int, hands_on: int) -> str:
    line = f"Up to {duration_words(budget_minutes)} · about {duration_words(hands_on)} hands-on"
    over = hands_on - budget_minutes
    if over > 0:
        line += f" · {duration_words(over)} over"
    return line


def section_line(count: int, hands_on: int) -> str:
    return f"{count} thing{'' if count == 1 else 's'} · {duration_words(hands_on)} hands-on"


# ---------------------------------------------------------------------------
# Classifying one recipe.

def _breakfast_keeps(name: str, breakfast_prep: str | None) -> bool:
    from . import breakfast_prep as _breakfast_prep
    return breakfast_prep != "fresh" and _breakfast_prep.breakfast_keeps(name) is True


def _is_snack(slot: str) -> bool:
    return (slot or "") == "snack"


# Condiments named after seafood are not seafood: "fish sauce", "shrimp
# paste", "oyster sauce", "anchovy paste", "clam juice", "oyster mushrooms"
# (review, 2026-11-01: "Thai Fish Sauce Chicken" read as fish).
_SEAFOOD_CONDIMENT = re.compile(
    r"\b(?:fish|oyster|shrimp|prawn|anchov(?:y|ies)|clam|crab|lobster|scallop|bonito|dried)\s+"
    r"(?:sauce|paste|stock|broth|juice|powder|flakes?|oil|seasoning|bouillon|base|mushrooms?|crackers?|chips?)\b"
)


def seafood_family(name: str) -> str | None:
    w = _SEAFOOD_CONDIMENT.sub(" ", _words(name))
    w = " " + " ".join(w.split()) + " "
    if any(_has_word(w, k) for k in _SEAFOOD_SHRIMP):
        return "shrimp"
    if any(_has_word(w, k) for k in _SEAFOOD_FISH):
        return "fish"
    if any(_has_word(w, k) for k in _SEAFOOD_OTHER):
        return "seafood"
    return None


_FRESH_REASONS = {
    "shrimp": "Better fresh. Shrimp goes rubbery after a day in the fridge.",
    "fish": "Better fresh. Fish is best cooked the day you eat it.",
    "seafood": "Better fresh. Seafood is best cooked the day you eat it.",
    "crispy": "Better fresh. Crispy food goes soft in the fridge.",
    "avocado": "Better fresh. Cut avocado turns brown within hours.",
    "cucumber": "Better fresh. Cut cucumber goes soft and watery overnight.",
    "tomato": "Better fresh. Cut tomato goes soft and watery overnight.",
    "cucumber_tomato": "Better fresh. Cut cucumber and tomato go soft and watery overnight.",
}


def _category(ing: dict) -> str:
    return (ing.get("category") or "").strip().lower()


def _is_meatish(ing: dict) -> bool:
    cat = _category(ing)
    name = _words(_name_only(ing.get("item") or ""))
    if cat in ("meat/seafood", "meat", "seafood"):
        return True
    if cat and cat not in ("other",):
        return False
    return any(_has_word(name, k) for k in _MEAT) or seafood_family(name) is not None


def _produce_kind(ing: dict) -> tuple[str, str] | None:
    """('fresh', family) / ('hardy', '') / ('greens', '') or None."""
    cat = _category(ing)
    if cat and cat != "produce":
        return None
    name = _name_only(ing.get("item") or "")
    w = _words(name)
    if any(_has_word(w, bad) for bad in _NOT_FRESH_WORDS):
        return None
    for word, family in _FRESH_PRODUCE.items():
        if _has_word(w, word):
            return ("fresh", family)
    if any(_has_word(w, g) for g in _GREENS):
        return ("greens", "")
    if any(_has_word(w, h) for h in _HARDY):
        # "Chili pepper" flakes are excluded above; a bare "pepper" without
        # a category is black pepper, not a vegetable.
        if not cat and (_has_word(w, "pepper") or _has_word(w, "peppers")) and not (
                _has_word(w, "bell") or _has_word(w, "red") or _has_word(w, "green") or _has_word(w, "yellow")):
            return None
        return ("hardy", "")
    return None


def _grain_of(ing: dict) -> tuple[str, int] | None:
    name = _name_only(ing.get("item") or "")
    w = _words(name)
    if any(_has_word(w, bad) for bad in _GRAIN_NOT):
        return None
    for grain, passive in _GRAINS:
        if _has_word(w, grain):
            return (grain, passive)
    return None


def heat_section(text: str, has_cook_time: bool = True) -> str:
    """Which section a whole dish belongs in, read off its own steps."""
    if _SLOW_RE.search(text or ""):
        return "slow_cooker"
    if _OVEN_RE.search(text or ""):
        return "oven"
    if _STOVE_RE.search(text or ""):
        return "stove"
    return "chill" if not has_cook_time else "stove"


def dish_fresh_reason(meal: str, recipe: dict | None) -> str | None:
    """Why a WHOLE dish is better made on the day, or None. Seafood as the
    main thing, or anything crispy or fried."""
    name = meal or ""
    if _CRISPY_NAME.search(name):
        return _FRESH_REASONS["crispy"]
    steps = " ".join(str(s) for s in ((recipe or {}).get("instructions") or []))
    if steps and _CRISPY_STEP.search(steps):
        return _FRESH_REASONS["crispy"]
    family = seafood_family(name)
    if family is None and recipe:
        # By the ingredient's NAME, whatever its category says (or doesn't):
        # a "Shrimp" line with no category is still shrimp. Condiments are
        # excluded inside seafood_family.
        for ing in recipe.get("ingredients") or []:
            if isinstance(ing, dict):
                family = seafood_family(_name_only(ing.get("item") or ""))
                if family:
                    break
    if family:
        return _FRESH_REASONS[family]
    return None


def _sauce_phrase(step: str, ingredient_names: set[str]) -> tuple[str, str] | None:
    """
    ("orange-ginger glaze", "glaze") from "Whisk the orange-ginger glaze…".
    A step makes a sauce when it mixes (whisk, stir together, combine,
    blend, shake, make) and names a sauce that is not itself one of the
    recipe's ingredients — "whisk in the soy sauce" adds a bottle, it makes
    nothing. ("", "sauce") when the sauce has no name of its own.
    """
    low = (step or "").lower()
    if "marinade" in low or not _SAUCE_VERBS.search(low):
        return None
    nouns = "|".join(_SAUCE_NOUNS)
    for m in re.finditer(r"\b(?:the|a|your|for)\s+((?:[a-z][a-z'\-]*\s+){0,2})(" + nouns + r")\b", low):
        lead = [w for w in m.group(1).split() if w not in ("all", "of", "the", "rest", "remaining", "some", "and")]
        phrase = " ".join(lead + [m.group(2)])
        if any(phrase == n or phrase in n or (lead and n.endswith(phrase)) for n in ingredient_names):
            continue
        if not lead:
            return ("", m.group(2))
        return (phrase, m.group(2))
    return None


def recipe_steps(meal: dict, recipe: dict | None, ingredients: list[dict]) -> dict:
    """
    Everything one meal offers a prep day, before any summing or fitting:
      {"chop": [(key, ing, kind)], "grains": [(key, ing, passive)],
       "sauce": {...} | None, "marinate": {...} | None,
       "fresh": [(family, display)], "dish_fresh": reason | None}

    `ingredients` is the meal's cooking list (recipes.cooking_ingredients
    for the people eating it), so amounts are what goes in the pan.
    """
    out = {"chop": [], "grains": [], "sauce": None, "marinate": None, "fresh": [], "dish_fresh": None}
    if not recipe:
        return out
    name_padded = _words(meal.get("meal") or "")
    steps = [str(s or "") for s in (recipe.get("instructions") or [])]
    all_steps = " ".join(steps) + " " + (recipe.get("advance_prep_notes") or "")
    out["dish_fresh"] = dish_fresh_reason(meal.get("meal") or "", recipe)
    grain_in_dish = any(_has_word(name_padded, g) for g in _GRAIN_IN_DISH)

    seen_keys: set[str] = set()
    meats: list[dict] = []
    ingredient_names = {display_name(i.get("item") or "") for i in ingredients if isinstance(i, dict)}
    for ing in ingredients:
        if not isinstance(ing, dict) or not (ing.get("item") or "").strip():
            continue
        name = display_name(ing["item"])
        key = _singular_key(" ".join(_words(name).split()))
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        # Seafood by NAME, whatever the category says ("Shrimp" filed under
        # pantry is still shrimp); condiments are excluded in seafood_family.
        family = seafood_family(name)
        if family:
            out["fresh"].append((family, name))
            continue
        if _is_meatish(ing):
            meats.append(ing)
            continue
        grain = _grain_of(ing)
        if grain and not grain_in_dish and _category(ing) in ("", "pantry", "other", "grains"):
            out["grains"].append((key, ing, grain[1]))
            continue
        kind = _produce_kind(ing)
        if kind is None:
            continue
        if kind[0] == "fresh":
            out["fresh"].append((kind[1], name))
        else:
            out["chop"].append((key, ing, kind[0]))

    for idx, step in enumerate(steps):
        found = _sauce_phrase(step, ingredient_names)
        if found:
            phrase, noun = found
            # A salsa with tomato, avocado or cucumber in it is fresh food.
            if noun == "salsa" and any(w in step.lower() for w in ("tomato", "avocado", "cucumber")):
                continue
            out["sauce"] = {"index": idx, "phrase": phrase, "noun": noun,
                            "passive": 5 if _SAUCE_HEAT.search(step.lower()) else 0}
            break

    if meats and _MARINATE_RE.search(all_steps):
        target = meats[0]
        marinate_text = " ".join(s for s in steps if _MARINATE_RE.search(s)).lower()
        for m in meats:
            if any(part in marinate_text for part in _name_only(m["item"]).lower().split()):
                target = m
                break
        out["marinate"] = {"ing": target, "key": _singular_key(" ".join(_words(display_name(target["item"])).split()))}
    return out


# ---------------------------------------------------------------------------
# Quantities.

def _qty_of(ing: dict) -> str:
    from . import recipes as _recipes
    from . import quantities as _quantities
    return _quantities._strip_prep_descriptor(_recipes._ascii_fractions((ing.get("qty") or "").strip()))


_FRACTIONS = ((0.25, "1/4"), (1 / 3, "1/3"), (0.5, "1/2"), (2 / 3, "2/3"), (0.75, "3/4"))
_LEAD_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*")


def _number_words(n: float) -> str:
    whole = int(n)
    rest = n - whole
    if rest < 0.05:
        return str(whole)
    frac = min(_FRACTIONS, key=lambda f: abs(f[0] - rest))
    if abs(frac[0] - rest) > 0.07:
        return f"{n:.1f}".rstrip("0").rstrip(".")
    return f"{whole} {frac[1]}" if whole else frac[1]


def summed_amount(ings: list[dict]) -> str:
    """
    The amount several meals need together, in the cook's own unit: "4
    cloves" + "3 cloves" is "7 cloves". Not the grocery ledger's sum, which
    rounds to what a shop sells ("2 heads"). Lines in different units, or
    that don't parse, give "" — the step then names the food without an
    amount rather than stating one nobody can trust.
    """
    from . import quantities as _quantities
    amounts = [_qty_of(i) for i in ings]
    if not all(amounts):
        return ""
    if len(set(amounts)) == 1 and len(amounts) == 1:
        return amounts[0]
    parsed = [_quantities._parse_quantity(a) for a in amounts]
    if not all(parsed) or len({p[1] for p in parsed}) != 1:
        return ""
    m = _LEAD_NUMBER.match(amounts[0])
    if not m:
        return ""
    total = sum(p[0] for p in parsed)
    tail = amounts[0][m.end():].strip()
    if tail and total > 1 and not tail.endswith("s") and tail.split()[0] in ("clove", "cup", "can", "head", "bunch", "stalk", "piece"):
        first, _, rest = tail.partition(" ")
        tail = (first + ("es" if first.endswith("ch") else "s") + (" " + rest if rest else "")).strip()
    return f"{_number_words(total)} {tail}".strip()


def amount_and_name(ings: list[dict]) -> str:
    """"2 onions" / "8 oz mushrooms" / "garlic"."""
    from . import recipes as _recipes
    name = display_name(ings[0].get("item") or "")
    qty = summed_amount(ings)
    if not qty:
        return name
    labelled = _recipes._with_item_label({"item": name, "qty": qty})
    return f"{qty} {labelled.get('item_label') or name}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


# ---------------------------------------------------------------------------
# Marks: take off / add back / done, per plan + prep date.

def _marks(weekly_plan_id: int, prep_date: str) -> dict[str, dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT step_key, state, done FROM {MARKS_TABLE} "
            "WHERE household_id = ? AND weekly_plan_id = ? AND prep_date = ?",
            (household_id(), weekly_plan_id, prep_date),
        ).fetchall()
    finally:
        conn.close()
    return {r["step_key"]: {"state": r["state"] or "", "done": bool(r["done"])} for r in rows}


def marks_for_plan(weekly_plan_id: int) -> dict[tuple[str, str], dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT prep_date, step_key, state, done FROM {MARKS_TABLE} "
            "WHERE household_id = ? AND weekly_plan_id = ?",
            (household_id(), weekly_plan_id),
        ).fetchall()
    finally:
        conn.close()
    return {(r["prep_date"], r["step_key"]): {"state": r["state"] or "", "done": bool(r["done"])} for r in rows}


def _write_mark(weekly_plan_id: int, prep_date: str, key: str, *, state: str | None = None,
                done: bool | None = None) -> None:
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT state, done FROM {MARKS_TABLE} WHERE household_id = ? AND weekly_plan_id = ? "
            "AND prep_date = ? AND step_key = ?",
            (household_id(), weekly_plan_id, prep_date, key),
        ).fetchone()
        new_state = state if state is not None else (row["state"] if row else "")
        new_done = (1 if done else 0) if done is not None else (row["done"] if row else 0)
        conn.execute(
            f"INSERT INTO {MARKS_TABLE} (household_id, weekly_plan_id, prep_date, step_key, state, done, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT (household_id, weekly_plan_id, prep_date, step_key) "
            "DO UPDATE SET state = excluded.state, done = excluded.done, updated_at = excluded.updated_at",
            (household_id(), weekly_plan_id, prep_date, key, new_state, new_done),
        )
        conn.commit()
    finally:
        conn.close()


def set_prep_list_mark(weekly_plan_id: int, prep_date: str, key: str, action: str) -> dict:
    """
    One change to one prep day's list: 'off' takes a step off (it moves to
    Made on the night as "Cooked fresh on Wednesday"), 'add' puts a Made on
    the night item back (it may go over the time; the header says so),
    'done' / 'undone' tick a step that has no row of its own elsewhere.

    Refused (ValueError): a step that isn't on that day's list, taking off a
    thaw, a marinade or its freezer move, and adding back something better
    fresh. A tick on a step that IS a row elsewhere (a prep task, a cook)
    goes through that row's own write, never a second copy here.
    """
    from . import prep_sessions as _prep_sessions
    if action not in ("off", "add", "done", "undone"):
        raise ValueError("A prep step is taken off, added back, done or not done.")
    sessions = _prep_sessions.prep_sessions_for_plan(weekly_plan_id)
    session = next((s for s in sessions if s["date"] == prep_date), None)
    if session is None:
        raise LookupError("There is no prep on that day.")
    item = next((i for i in session["items"] if i.get("key") == key), None)
    if item is None:
        raise LookupError("That step isn't on this prep day any more.")
    if action == "off":
        if not item.get("can_remove"):
            raise ValueError("Thawing and marinating always happen on the prep day.")
        _write_mark(weekly_plan_id, prep_date, key, state="off")
    elif action == "add":
        if item.get("state") == "better_fresh":
            raise ValueError("That one is better made on the day.")
        _write_mark(weekly_plan_id, prep_date, key, state="on")
    else:
        if item.get("prep_task_id") is not None or item.get("kind") == "cook_ahead":
            raise ValueError("That step ticks off where it lives.")
        _write_mark(weekly_plan_id, prep_date, key, done=(action == "done"))
    return {"weekly_plan_id": weekly_plan_id, "prep_date": prep_date, "key": key, "action": action,
            "title": item.get("title") or ""}


# ---------------------------------------------------------------------------
# Thaws a frozen marinade needs.

def thaw_description(meat: str, meal: str, meal_date: str) -> str:
    return f"Move the marinated {meat} to the fridge — for {_day_long(meal_date)}'s {meal}."


def thaw_date_for(meal_date: str) -> str:
    return (date.fromisoformat(meal_date) - timedelta(days=1)).isoformat()


def marinade_items(e: dict, meat: str, qty: str, mkey: str, days_out: int, frozen: bool,
                   thaw_wants: list, *, existing: dict | None = None) -> list[dict]:
    """
    The marinade for one meal, ALWAYS on the prep day and never removable:
      * fresh meat, meal within two days: marinate, keep in the fridge;
      * fresh meat, meal further out: marinate, freeze in the bag (a freezer
        step), and the thaw the night before the meal is wanted
        (reconcile_marinade_thaws books it);
      * meat still frozen on the prep day: it goes into the marinade bag
        frozen — into the fridge to marinate as it thaws when the meal is
        within two days, back into the freezer otherwise (its own thaw is
        defrost.py's row, already booked, so none is added here).
    `existing` is a pinned prep task's own step, which keeps its key and
    row and gains the freezer half.
    """
    uses = _uses_of([e])
    title = (f"Marinate the frozen {meat}" if frozen else f"Marinate the {meat}") + (f", {qty}" if qty else "")
    out: list[dict] = []
    freeze = days_out > MARINADE_FRIDGE_DAYS
    if frozen and not freeze:
        keeps = f"Put the frozen {meat} in the marinade bag; it marinates as it thaws in the fridge."
    elif frozen:
        keeps = f"Put the frozen {meat} in the marinade bag and back in the freezer. Its thaw is already booked."
    elif freeze:
        thaw = thaw_date_for(e["date"])
        keeps = f"Freeze it in the bag. It thaws in the fridge {_day_long(thaw)} night."
        thaw_wants.append({"weekly_plan_id": e["weekly_plan_id"], "entry_id": e["id"], "meat": meat,
                           "meal": e["meal"], "meal_date": e["date"],
                           "prep_date": (date.fromisoformat(e["date"]) - timedelta(days=days_out)).isoformat()})
    else:
        keeps = f"Keeps in the fridge until {_day_long(e['date'])}"
    if existing is not None:
        existing.update(kind=existing.get("kind") or "marinate", always=True, can_remove=False,
                        keeps=keeps, feeds_line=existing.get("feeds_line") or feeds_line(uses),
                        first_date=e["date"], first_slot=e["slot"], covers=sorted(set(existing.get("covers") or []) | {e["date"]}))
    else:
        out.append(_base_item(
            key=f"marinate:{e['id']}:{mkey}", kind="marinate", section="sauces",
            title=title, feeds=e["meal"], feeds_line=feeds_line(uses), keeps=keeps,
            covers=[e["date"]], hands_on=8, saved=8, always=True, can_remove=False,
            first_date=e["date"], first_slot=e["slot"],
        ))
    if freeze:
        out.append(_base_item(
            key=f"freeze:{e['id']}:{mkey}", kind="freeze", section="freezer",
            title=f"Marinated {meat}, in the bag", feeds=e["meal"], feeds_line=feeds_line(uses),
            keeps=("Thaw already booked" if frozen else f"Thaws {_day_long(thaw_date_for(e['date']))} night"),
            covers=[e["date"]], hands_on=1, saved=0, always=True, can_remove=False,
            first_date=e["date"], first_slot=e["slot"],
        ))
    return out


def _marinade_settled(conn, entry_id: int, prep_date: str, today) -> bool:
    """Whether a frozen marinade for this meal has really been made: its
    prep day is past, or its step was ticked (a derived marinade's mark, or
    a pinned marinate task for the meal marked done)."""
    if prep_date and prep_date < today():
        return True
    return _marinade_ticked(conn, entry_id)


def _marinade_ticked(conn, entry_id: int) -> bool:
    """The marinade for this meal was ticked done on a prep list."""
    if conn.execute(
        f"SELECT 1 FROM {MARKS_TABLE} WHERE household_id = ? AND done = 1 AND step_key LIKE ?",
        (household_id(), f"marinate:{entry_id}:%"),
    ).fetchone():
        return True
    return conn.execute(
        "SELECT 1 FROM prep_tasks WHERE household_id = ? AND meal_plan_entry_id = ? AND status = 'done' "
        "AND task_type != 'defrost' AND lower(description) LIKE '%marinat%'",
        (household_id(), entry_id),
    ).fetchone() is not None


def reconcile_marinade_thaws(wants: list[dict], plan_ids, windows: list[tuple[str, str]]) -> None:
    """
    Make this module's OWN thaw rows (defrost rows with detail_json.kind =
    marinade_thaw; defrost.sync_defrost_tasks, scoped to rows with a freezer
    item, never sweeps them) match what the prep list wants:
      * a wanted thaw with no row is booked for the night before the meal
        (never for a night already gone by);
      * a pending row whose meal moved follows it;
      * a pending row nobody wants any more — its meal is gone, no longer
        planned, or sits in a reach this read covered without a frozen
        marinade — is deleted.
    Ticked or skipped rows are the household's record and stay. Nothing
    else is ever touched: defrost.py's own rows are never read for writing.
    Idempotent; never raises (a failure leaves the rows as they were).
    """
    from . import cooker as _cooker
    today_cache: list[str] = []

    def today() -> str:
        # Read the household clock only when a thaw is about to be booked:
        # the Cook view counts its clock reads (test_cooker_household_clock).
        if not today_cache:
            try:
                today_cache.append(_cooker.household_today().isoformat())
            except Exception:
                today_cache.append(date.today().isoformat())
        return today_cache[0]

    plan_ids = sorted({p for p in plan_ids if p} | {w["weekly_plan_id"] for w in wants})
    if not plan_ids:
        return
    wanted = {w["entry_id"]: w for w in wants}
    try:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT t.id, t.status, t.task_date, t.description, t.related_meal, t.meal_plan_entry_id, "
                "COALESCE(json_extract(t.detail_json, '$.prep_date'), '') AS prep_date, "
                "m.date AS meal_date, m.slot_state FROM prep_tasks t "
                "LEFT JOIN meal_plan_entries m ON m.id = t.meal_plan_entry_id AND m.household_id = t.household_id "
                "WHERE t.household_id = ? AND t.task_type = 'defrost' "
                f"AND t.weekly_plan_id IN ({','.join('?' * len(plan_ids))}) "
                "AND COALESCE(json_extract(t.detail_json, '$.kind'), '') = ?",
                (household_id(), *plan_ids, MARINADE_THAW_KIND),
            ).fetchall()
            have = {}
            for r in rows:
                entry_id = r["meal_plan_entry_id"]
                if entry_id in wanted and entry_id not in have:
                    have[entry_id] = r
                    continue
                if r["status"] != "pending":
                    continue
                gone = r["meal_date"] is None or r["slot_state"] != "planned" or entry_id in have
                # The meal moved to (or before) its prep day and the
                # marinade was never ticked: it never happened, so neither
                # does its thaw (Loop Board follow-up, 2026-11-01).
                moved_before = bool(r["prep_date"]) and r["meal_date"] is not None \
                    and r["meal_date"] <= r["prep_date"] and not _marinade_ticked(conn, entry_id)
                if gone or moved_before:
                    conn.execute("DELETE FROM prep_tasks WHERE id = ? AND household_id = ?", (r["id"], household_id()))
                    continue
                # Once the bag is really in the freezer — its prep day has
                # gone by, or its marinade was ticked done — that is a fact,
                # whatever the plan does next: the thaw stays and follows the
                # meal to the night before its new day (review, 2026-11-01:
                # a meal moved closer after the prep day lost its thaw).
                if _marinade_settled(conn, entry_id, r["prep_date"], today):
                    # Never a pending thaw dated in the past: a night-before
                    # already gone by makes it due now, so Cook's thaw card
                    # shows it.
                    thaw = max(thaw_date_for(r["meal_date"]), today())
                    m = re.match(r"^Move the marinated (.+?) to the fridge", r["description"] or "")
                    description = thaw_description(m.group(1) if m else "meat", r["related_meal"] or "dinner",
                                                   r["meal_date"])
                    if r["task_date"] != thaw or r["description"] != description:
                        conn.execute(
                            "UPDATE prep_tasks SET task_date = ?, description = ? WHERE id = ? AND household_id = ?",
                            (thaw, description, r["id"], household_id()),
                        )
                    continue
                if any(a <= r["meal_date"] <= b for a, b in windows):
                    conn.execute("DELETE FROM prep_tasks WHERE id = ? AND household_id = ?", (r["id"], household_id()))
            for entry_id, w in wanted.items():
                thaw = thaw_date_for(w["meal_date"])
                description = thaw_description(w["meat"], w["meal"], w["meal_date"])
                row = have.get(entry_id)
                if row is not None:
                    if row["status"] == "pending" and row["task_date"] != thaw and thaw < today():
                        thaw = today()  # never a pending thaw dated in the past
                    if row["status"] == "pending" and (row["task_date"] != thaw or row["description"] != description):
                        conn.execute(
                            "UPDATE prep_tasks SET task_date = ?, description = ? WHERE id = ? AND household_id = ?",
                            (thaw, description, row["id"], household_id()),
                        )
                    continue
                if thaw < today():
                    continue
                conn.execute(
                    "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
                    "status, task_type, inventory_item_id, meal_plan_entry_id, quantity, detail_json) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', NULL, ?, '', ?)",
                    (household_id(), w["weekly_plan_id"], thaw, description, w["meal"], entry_id,
                     json.dumps({"kind": MARINADE_THAW_KIND, "prep_date": w.get("prep_date") or ""})),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        import logging
        logging.getLogger("home_manager").exception("Marinade thaws not reconciled; they stand as they were")


# ---------------------------------------------------------------------------
# Reading the meals a prep day reaches.

def _live_plans() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, status, week_start_date, content_start_date, day_count FROM weekly_plans "
            "WHERE household_id = ? AND status IN ('draft', 'approved')",
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _plan_dates(plan) -> set[str]:
    from . import weekly_plan as _weekly_plan
    start, count = _weekly_plan.plan_period(plan)
    first = date.fromisoformat(start)
    return {(first + timedelta(days=i)).isoformat() for i in range(count)}


def _owner_by_date(weekly_plan_id: int, plan_row, first: str, last: str) -> dict[str, int]:
    """Which plan's meals count on each day of the reach: the viewed plan
    for its own days, else an approved plan whose period holds the day,
    else a draft. One plan per day, so a re-plan draft over the same week
    never doubles a step."""
    own = _plan_dates(plan_row) if plan_row is not None else set()
    others = sorted(_live_plans(), key=lambda p: (p["status"] != "approved", -p["id"]))
    periods = [(p["id"], _plan_dates(p)) for p in others if p["id"] != weekly_plan_id]
    out: dict[str, int] = {}
    d = date.fromisoformat(first)
    end = date.fromisoformat(last)
    while d <= end:
        iso = d.isoformat()
        if iso in own:
            out[iso] = weekly_plan_id
        else:
            for pid, dates in periods:
                if iso in dates:
                    out[iso] = pid
                    break
        d += timedelta(days=1)
    return out


def _entries_in(owner: dict[str, int]) -> list[dict]:
    if not owner:
        return []
    plan_ids = sorted(set(owner.values()))
    marks = ",".join("?" * len(plan_ids))
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT mpe.id, mpe.weekly_plan_id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status,
                   mpe.recipe_id, COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id AND r.household_id = mpe.household_id
            WHERE mpe.household_id = ? AND mpe.weekly_plan_id IN ({marks})
              AND mpe.component_category IS NULL AND mpe.slot_state = 'planned'
            ORDER BY mpe.date ASC, mpe.id ASC
            """,
            (household_id(), *plan_ids),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows if owner.get(r["date"]) == r["weekly_plan_id"] and (r["meal"] or "").strip()]


def _recipes_by_id(ids) -> dict[int, dict]:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT id, name, ingredients_json, instructions_json, prep_time_minutes, cook_time_minutes, "
            f"advance_prep_notes, default_servings FROM recipes WHERE household_id = ? AND id IN ({','.join('?' * len(ids))})",
            (household_id(), *ids),
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        try:
            ingredients = json.loads(r["ingredients_json"] or "[]")
        except (TypeError, ValueError):
            ingredients = []
        try:
            instructions = json.loads(r["instructions_json"] or "[]")
        except (TypeError, ValueError):
            instructions = []
        out[r["id"]] = {
            "id": r["id"], "name": r["name"], "ingredients": ingredients if isinstance(ingredients, list) else [],
            "instructions": instructions if isinstance(instructions, list) else [],
            "prep_time_minutes": r["prep_time_minutes"], "cook_time_minutes": r["cook_time_minutes"],
            "advance_prep_notes": r["advance_prep_notes"] or "", "default_servings": r["default_servings"],
        }
    return out


def _recipe_by_name(name: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM recipes WHERE household_id = ? AND lower(name) = lower(?) ORDER BY id LIMIT 1",
            (household_id(), (name or "").strip()),
        ).fetchone()
    finally:
        conn.close()
    return _recipes_by_id([row["id"]]).get(row["id"]) if row else None


def _cooking_list(recipe: dict, meal_date: str, slot: str) -> list[dict]:
    from . import leftovers as _leftovers
    from . import recipes as _recipes
    eaters = _leftovers.eaters_at(meal_date, slot) or None
    try:
        return _recipes.cooking_ingredients(recipe["ingredients"], servings=eaters)
    except Exception:
        return [i for i in recipe["ingredients"] if isinstance(i, dict)]


def _reheat_ids(plan_ids) -> set[int]:
    from . import leftovers as _leftovers
    out: set[int] = set()
    for pid in plan_ids:
        try:
            out |= set(_leftovers.plan_leftover_chains(pid)["leftovers"])
        except Exception:
            continue
    return out


def next_prep_after(prep_date: str, prep_weekdays: set[str], other_dates) -> str:
    """The last day a prep day reaches: the day before the next prep day,
    and never more than REACH_DAYS out."""
    start = date.fromisoformat(prep_date)
    cap = start + timedelta(days=REACH_DAYS)
    others = sorted(d for d in other_dates if d > prep_date)
    for i in range(1, REACH_DAYS + 1):
        d = start + timedelta(days=i)
        if d.strftime("%A").lower() in prep_weekdays or (others and d.isoformat() == others[0]):
            return (d - timedelta(days=1)).isoformat()
    return cap.isoformat()


# ---------------------------------------------------------------------------
# Building one prep day's list.

def _base_item(_from: dict | None = None, **kw) -> dict:
    """One step with every field a reader may ask for. `_from` is an older
    item shape (prep_sessions' gathered rows) laid under the overrides."""
    item = {
        "key": "", "kind": "", "section": "chop", "title": "", "line": "", "feeds_line": "",
        "keeps": "", "feeds": "", "prep_task_id": None, "entry_id": None, "done": False,
        "skipped": False, "covers": [], "hands_on": 0, "passive": 0, "minutes": 0,
        "saved": 0, "shared": False, "pinned": False, "always": False, "can_remove": True,
        "state": "on_list", "on_list": True, "added_back": False, "off_line": "",
        "first_date": "", "first_slot": "",
    }
    if _from:
        item.update(_from)
    item.update(kw)
    item["minutes"] = item["hands_on"]
    return item


def _uses_of(entries: list[dict]) -> list[dict]:
    return [{"date": e["date"], "slot": e["slot"], "entry_id": e["id"], "meal": e["meal"]} for e in entries]


def _fresh_line(item: dict) -> str:
    """"Cooked fresh on Wednesday" / "Cooked fresh each night" (snacks)."""
    if _is_snack(item.get("first_slot")):
        return "Cooked fresh each night"
    if not item.get("first_date"):
        return "Cooked fresh on the day"
    return f"Cooked fresh on {_day_long(item['first_date'])}"


def _didnt_fit_line(item: dict, used: int, budget: int) -> str:
    total = (item.get("hands_on") or 0) + (item.get("passive") or 0)
    when = ("each night" if _is_snack(item.get("first_slot"))
            else _day_long(item["first_date"]) if item.get("first_date") else "on the day")
    line = f"{duration_words(total)}. Make it {when}"
    # The real shortfall, rounded up to 5 minutes ("or add 10 min"), said in
    # hours only from an hour up.
    need = max(used + (item.get("hands_on") or 0) - budget, 1)
    need = int(math.ceil(need / 5.0) * 5)
    if budget + need <= MAX_BUDGET_MINUTES:
        line += f", or add {duration_words(need)}"
    return line


def derive_steps(prep_date: str, last_date: str, weekly_plan_id: int, plan_row, *,
                 exclude_entry_ids: set[int], covered_by_rows: dict[int, set[str]],
                 marinated_by_rows: set[int], frozen_meat: dict[int, set[str]],
                 batch_grain_entries: set[int], fresh_batch_entries: set[int],
                 thaw_wants: list | None = None) -> list[dict]:
    """The steps (and better-fresh notes) the meals between the prep day
    and `last_date` offer. Nothing is written here: the thaw a frozen
    marinade needs is appended to `thaw_wants`, and
    reconcile_marinade_thaws writes them."""
    if thaw_wants is None:
        thaw_wants = []
    first = (date.fromisoformat(prep_date) + timedelta(days=1)).isoformat()
    if first > last_date:
        return []
    owner = _owner_by_date(weekly_plan_id, plan_row, first, last_date)
    entries = _entries_in(owner)
    reheats = _reheat_ids(set(owner.values()))
    entries = [e for e in entries if e["id"] not in reheats
               and (e["id"] not in exclude_entry_ids or e["id"] in fresh_batch_entries)]
    recipes = _recipes_by_id(e["recipe_id"] for e in entries)
    try:
        from . import rhythm as _rhythm
        breakfast_prep = _rhythm.get_household_rhythm().get("breakfast_prep")
    except Exception:
        breakfast_prep = None

    chop: dict[str, list[tuple[dict, dict, str]]] = {}   # ingredient key -> [(entry, ing, kind)]
    grains: dict[str, list[tuple[dict, dict, int]]] = {}
    fresh: dict[str, list[tuple[dict, str]]] = {}        # family -> [(entry, display)]
    sauces: dict[str, list[tuple[dict, dict]]] = {}      # phrase -> [(entry, sauce)]
    items: list[dict] = []

    for e in entries:
        recipe = recipes.get(e["recipe_id"]) if e["recipe_id"] else None
        if recipe is None and e["meal"]:
            recipe = None  # a meal with no saved recipe: nothing known about it
        days_out = (date.fromisoformat(e["date"]) - date.fromisoformat(prep_date)).days
        ings = _cooking_list(recipe, e["date"], e["slot"]) if recipe else []
        found = recipe_steps(e, recipe, ings)
        offered = False
        for family, display in found["fresh"]:
            fresh.setdefault(family, []).append((e, display))
        if found["dish_fresh"] and found["dish_fresh"] == _FRESH_REASONS["crispy"]:
            fresh.setdefault("crispy", []).append((e, e["meal"]))
        for key, ing, kind in found["chop"]:
            if any(key == c or key in c or c in key for c in covered_by_rows.get(e["id"], set())):
                offered = True
                continue
            keeps = KEEPS_GREENS if kind == "greens" else KEEPS_CHOPPED
            if days_out <= keeps:
                chop.setdefault(key, []).append((e, ing, kind))
                offered = True
        for key, ing, passive in found["grains"]:
            if e["id"] in batch_grain_entries:
                offered = True
                continue
            if days_out <= KEEPS_GRAIN:
                grains.setdefault(key, []).append((e, ing, passive))
                offered = True
        sauce = found["sauce"]
        if sauce and days_out <= KEEPS_SAUCE:
            # The same named sauce in several meals is ONE step, in jars
            # ("Lemon-herb dressing, 3 jars"), and ranks as shared.
            skey = sauce["phrase"].lower() if sauce["phrase"] else f"__{e['id']}:{sauce['index']}"
            sauces.setdefault(skey, []).append((e, sauce))
            offered = True
        mar = found["marinate"]
        if mar and e["id"] not in marinated_by_rows:
            meat = display_name(mar["ing"].get("item") or "")
            # Meat still in the freezer on the prep day is marinated all the
            # same — it goes into the bag frozen. Never a prep-day cook of the
            # raw dish instead (review, 2026-11-01).
            still_frozen = any(w in meat for w in frozen_meat.get(e["id"], set()))
            items.extend(marinade_items(
                e, meat, _qty_of(mar["ing"]), mar["key"], days_out, still_frozen, thaw_wants))
            offered = True
        # A recipe that offers nothing to do ahead: the dish itself, made
        # ahead, when it keeps that long and isn't better fresh.
        # Only a recipe with steps written down (one with none is unknown,
        # not undecomposable), and a breakfast only when it is the make-
        # ahead kind (breakfast_prep: "Fresh eggs and toast cannot be made
        # on a prep day", Emily 2026-10-26). Never a dish that eats anything
        # better fresh — seafood, avocado, cucumber, tomato, crispy — whatever
        # its cook time (review, 2026-11-01: a chicken avocado wrap with 10
        # minutes on the stove is still a wrap with avocado in it). Its
        # other steps are still offered above.
        # Tomato is the exception: a cooked stew or sauce with tomato is the
        # classic make-ahead dish, so tomato blocks only a raw (no cook
        # time) dish (coordinator, 2026-11-01).
        fresh_families = {f for f, _d in found["fresh"]}
        if not (recipe or {}).get("cook_time_minutes"):
            blocking = fresh_families
        else:
            blocking = fresh_families - {"tomato"}
        if (recipe and not offered and not found["dish_fresh"] and not blocking
                and days_out <= KEEPS_WHOLE_DISH
                and recipe.get("instructions")
                and (recipe.get("prep_time_minutes") or recipe.get("cook_time_minutes"))
                and (e["slot"] != "breakfast" or _breakfast_keeps(e["meal"], breakfast_prep))):
            hands = recipe.get("prep_time_minutes") or 15
            passive = recipe.get("cook_time_minutes") or 0
            text = " ".join(str(s) for s in recipe.get("instructions") or [])
            items.append(_base_item(
                key=f"dish:{e['id']}", kind="whole_dish", section=heat_section(text, bool(passive)),
                title=e["meal"], feeds=e["meal"], entry_id=e["id"], feeds_line=feeds_line(_uses_of([e])),
                keeps=f"Keeps {KEEPS_WHOLE_DISH} days", covers=[e["date"]], hands_on=hands, passive=passive,
                saved=max(hands + passive - 5, 1), first_date=e["date"], first_slot=e["slot"],
                fallback=True,
            ))

    for skey, uses in sauces.items():
        meal_entries = list({u[0]["id"]: u[0] for u in uses}.values())
        first_e = min(meal_entries, key=lambda u: (u["date"], _slot_rank(u["slot"])))
        s0 = uses[0][1]
        phrase = s0["phrase"] or f"{s0['noun']} for {first_e['meal']}"
        n = len(meal_entries)
        passive = max(u[1]["passive"] for u in uses)
        items.append(_base_item(
            key="sauce:" + ",".join(str(u["id"]) for u in meal_entries) + ":" + str(s0["index"]),
            kind="step", section="sauces", title=f"{_cap(phrase)}, {n} jar{'' if n == 1 else 's'}",
            feeds=", ".join(sorted({u["meal"] for u in meal_entries})),
            feeds_line=feeds_line(_uses_of(meal_entries)), keeps=f"Keeps {KEEPS_SAUCE} days",
            covers=sorted({u["date"] for u in meal_entries}), hands_on=5 + 2 * (n - 1), passive=passive,
            saved=6 * n + passive, shared=n > 1, first_date=first_e["date"], first_slot=first_e["slot"],
        ))

    # Chop and wash: one step per set of meals, the ingredients that feed
    # exactly those meals summed into one line.
    by_meals: dict[tuple, list[str]] = {}
    for key, uses in chop.items():
        meal_key = tuple(sorted({u[0]["id"] for u in uses}))
        by_meals.setdefault(meal_key, []).append(key)
    for meal_key, keys in sorted(by_meals.items(), key=lambda kv: (min(u[0]["date"] for k in kv[1] for u in chop[k]), kv[0])):
        parts = []
        pairs = 0
        greens_only = True
        uses_all: list[dict] = []
        for key in sorted(keys, key=lambda k: chop[k][0][1].get("item", "").lower()):
            uses = chop[key]
            parts.append(amount_and_name([u[1] for u in uses]))
            pairs += len(uses)
            greens_only = greens_only and all(u[2] == "greens" for u in uses)
            uses_all.extend(u[0] for u in uses)
        meal_entries = {u["id"]: u for u in uses_all}.values()
        first_e = min(meal_entries, key=lambda u: (u["date"], _slot_rank(u["slot"])))
        hands = 2 + 2 * len(keys) + len(meal_key)
        items.append(_base_item(
            key="chop:" + ",".join(str(i) for i in meal_key) + ":" + "|".join(sorted(keys)),
            kind="step", section="chop", title=_cap(", ".join(parts)),
            feeds=", ".join(sorted({u["meal"] for u in meal_entries})),
            feeds_line=feeds_line(_uses_of(list(meal_entries))),
            keeps=f"Keeps {KEEPS_GREENS if greens_only else KEEPS_CHOPPED} days",
            covers=sorted({u["date"] for u in meal_entries}), hands_on=hands, saved=4 * pairs + len(keys),
            shared=len(meal_key) > 1, first_date=first_e["date"], first_slot=first_e["slot"],
        ))

    for key, uses in sorted(grains.items()):
        meal_entries = list({u[0]["id"]: u[0] for u in uses}.values())
        first_e = min(meal_entries, key=lambda u: (u["date"], _slot_rank(u["slot"])))
        passive = max(u[2] for u in uses)
        qty = summed_amount([u[1] for u in uses])
        name = _cap(display_name(uses[0][1].get("item") or key))
        dry = " dry" if qty and re.search(r"\b(cups?|g|kg|oz|lb|grams?)\b", qty) else ""
        items.append(_base_item(
            key=f"grain:{key}:" + ",".join(str(u["id"]) for u in meal_entries), kind="step", section="stove",
            title=f"{name}, {qty}{dry}" if qty else name,
            feeds=", ".join(sorted({u["meal"] for u in meal_entries})),
            feeds_line=feeds_line(_uses_of(meal_entries)), keeps=f"Keeps {KEEPS_GRAIN} days",
            covers=sorted({u["date"] for u in meal_entries}), hands_on=5, passive=passive,
            saved=5 + passive + 2 * (len(meal_entries) - 1), shared=len(meal_entries) > 1,
            first_date=first_e["date"], first_slot=first_e["slot"],
        ))

    # Better fresh: one note per kind of food, saying which meals.
    if "cucumber" in fresh and "tomato" in fresh:
        fresh["cucumber_tomato"] = fresh.pop("cucumber") + fresh.pop("tomato")
    for family, uses in sorted(fresh.items()):
        meal_entries = list({u[0]["id"]: u[0] for u in uses}.values())
        first_e = min(meal_entries, key=lambda u: (u["date"], _slot_rank(u["slot"])))
        names = []
        for _e, display in uses:
            if _singular_key(display) not in {_singular_key(n) for n in names}:
                names.append(display)
        what = _cap(", ".join(names[:3]))
        items.append(_base_item(
            key=f"fresh:{family}:" + ",".join(str(u["id"]) for u in meal_entries), kind="better_fresh",
            section="made_fresh", title=f"{what} for {feeds_line(_uses_of(meal_entries))}",
            feeds=", ".join(sorted({u["meal"] for u in meal_entries})),
            feeds_line=feeds_line(_uses_of(meal_entries)), covers=sorted({u["date"] for u in meal_entries}),
            state="better_fresh", on_list=False, can_remove=False, off_line=_FRESH_REASONS[family],
            first_date=first_e["date"], first_slot=first_e["slot"],
        ))
    return items


def fit(items: list[dict], budget: int, marks: dict[str, dict]) -> int:
    """
    Decide what is on the list. In order: thaws and marinades (always);
    what the plan already put on the day (pinned) and anything the
    household added back, unless taken off; then the derived steps, shared
    ones first, by weeknight minutes saved per hands-on minute, each taken
    while it still fits. Returns the hands-on minutes on the list.
    Writes each item's `state`, `on_list`, `off_line` and `done`.
    """
    used = 0
    for item in items:
        mark = marks.get(item["key"]) or {}
        if item["state"] == "better_fresh":
            continue
        if mark.get("done") and item.get("prep_task_id") is None and item.get("kind") != "cook_ahead":
            item["done"] = True
        if item["always"]:
            used += item["hands_on"]
            continue
        if mark.get("state") == "off":
            item.update(state="taken_off", on_list=False, off_line=_fresh_line(item))
            continue
        if mark.get("state") == "on":
            # Counted AFTER the fill below: adding one thing back puts it
            # on top of the list as it was, rather than quietly bumping
            # another step off to make room.
            item["added_back"] = True
            continue
        if item["pinned"]:
            used += item["hands_on"]
    candidates = [i for i in items if i["state"] == "on_list" and not i["always"] and not i["pinned"]
                  and not i["added_back"]]
    candidates.sort(key=lambda i: (
        0 if i["shared"] else 1,
        1 if i.get("fallback") else 0,
        -(i["saved"] / max(i["hands_on"], 1)),
        -i["saved"], i["first_date"], i["key"],
    ))
    for item in candidates:
        if used + item["hands_on"] <= budget:
            used += item["hands_on"]
        else:
            item.update(state="didnt_fit", on_list=False)
    used += sum(i["hands_on"] for i in items if i["added_back"] and i["state"] == "on_list")
    for item in candidates:
        if item["state"] == "didnt_fit":
            item["off_line"] = _didnt_fit_line(item, used, budget)
    return used


def section_rows(items: list[dict]) -> list[dict]:
    on = [i for i in items if i["on_list"]]
    out = []
    for key in SECTION_ORDER:
        rows = sorted((i for i in on if i["section"] == key),
                      key=lambda i: (i["first_date"] or "", _slot_rank(i["first_slot"]), i["title"].lower()))
        if not rows:
            continue
        hands = sum(i["hands_on"] for i in rows)
        out.append({
            "key": key, "title": SECTION_TITLES[key], "step_keys": [i["key"] for i in rows],
            "count": len(rows), "hands_on": hands, "line": section_line(len(rows), hands),
            "done": all(i["done"] for i in rows),
        })
    return out


def made_fresh_rows(items: list[dict]) -> list[dict]:
    order = {"didnt_fit": 0, "taken_off": 1, "better_fresh": 2}
    return sorted((i for i in items if not i["on_list"]),
                  key=lambda i: (order.get(i["state"], 3), i["first_date"] or "", i["title"].lower()))


def fresh_cook_entry_ids(weekly_plan_id: int, batches: list[dict]) -> set[int]:
    """
    The prepped batches that are NOT made on their prep day after all — a
    dish that is better fresh (seafood, crispy), or one the household took
    off its prep list. Their cards read as an ordinary cook on their own
    day (cooker._apply_prepped_lunches), so the card and the prep list say
    the same thing. Never raises: a failure here leaves the cards as they
    were.
    """
    out: set[int] = set()
    if not batches:
        return out
    try:
        # By household, not by plan: the mark is stored under whichever plan
        # the Cook tab was showing on the prep day, and a Sunday prep for a
        # Monday-start week is shown from the week before.
        conn = get_conn()
        try:
            off = {
                (r["prep_date"], r["step_key"]) for r in conn.execute(
                    f"SELECT prep_date, step_key FROM {MARKS_TABLE} WHERE household_id = ? "
                    "AND state = 'off' AND step_key LIKE 'cook:%'",
                    (household_id(),),
                ).fetchall()
            }
        finally:
            conn.close()
        for b in batches:
            recipe = _recipe_by_name(b.get("meal") or "")
            if dish_fresh_reason(b.get("meal") or "", recipe):
                out.add(b["cook_entry_id"])
            elif (b["prep_date"], f"cook:{b['cook_entry_id']}") in off:
                out.add(b["cook_entry_id"])
    except Exception:
        return set()
    return out
