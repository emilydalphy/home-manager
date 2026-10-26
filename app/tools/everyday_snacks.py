"""
A week's snacks come from a real list of good everyday snacks.

Loop Board, 2026-10-21: "my week's snacks come from a real list of good
everyday snacks, not the same apple-and-peanut-butter ideas". The planner
prompt said how many snacks and what rules they follow but never what to
pick from, so the model invented them — and the 62 snacks in the shared
recipe library are mostly party food.

The list is data, in one place: data/everyday_snacks.json. A snack is added
there, never here; the rules below read whatever the file holds.

Two halves, because telling the generator is not the same as preventing it
(CLAUDE.md):

  - TELLING. prompt_context() hands the drafting call a short shortlist —
    the list filtered in code for THIS household (allergens, a nut-free
    lunch, vegetarian/vegan/pescatarian, dislikes, the no-repeat window) and
    ordered least-recently-used first, so the rotation walks the whole list
    rather than the model's favourite four.
  - PREVENTING. enforce_snacks_from_list() runs after the snack counts are
    held (agent._hold_counts, both runs, no model call). A snack that is
    not on the list, is not one this household can have, or repeats the
    no-repeat window, is replaced — the whole dish at once, so the week's
    count of different snacks and its spread are kept — by the
    least-recently-used list snack that fits all of its days: not already
    on the week, not that day's other food (plan_quality.shared_food), past
    the allergen gate with the allergens the list names, and past the taste
    veto (swap_in_place.pick_gate). A list snack this household can't have
    whose list names a nut-free swap becomes the swap ("Rice Cakes with
    Sunflower Butter and Banana") rather than a different snack.

Before the allergen gate, swap_for_household reads every list snack the
model sent with the list's OWN allergen tags (whatever the eating style):
a nut butter becomes its swap, and one the house can't have is held back
like any clashing dish. A held snack is re-picked from the list first
(fill_held_snacks), with no model call, rather than left open.

What stays off-list: a snack they asked for (their words for the week or a
standing note name it, or the model stamped derived_from.freeform), a
cooked one, and any snack when nothing on the list fits — better their
snack than an empty slot. A household with an eating style the list can't
be judged against (keto, a list of allowed foods) is told about the list
but never has a snack replaced; vegetarian / vegan / pescatarian are read
here and need no stand-down. Never raises.
"""
from __future__ import annotations

import functools
import json
import logging
import re
from pathlib import Path

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

LIST_PATH = Path(__file__).resolve().parents[2] / "data" / "everyday_snacks.json"

CONSTRAINT = "everyday_snacks"

# How many names the drafting call is handed: enough for a week of
# different snacks with room for the model to match them to the week's
# meals, few enough that it reads as a shortlist and not a catalogue.
SHORTLIST_SIZE = 14

# What a tag on the list is matched as, in the words a restriction is
# written in ("milk allergy", "lactose", "tree nut", "coeliac" via gluten).
# The shared matcher knows some foods only by a family word and misses
# others outright (pretzels for gluten), so a snack's allergens are always
# handed to it as hidden ingredients — the list protects itself.
_ALLERGEN_WORDS = {
    "nuts": ("tree nuts", "nuts"),
    "peanuts": ("peanuts",),
    "dairy": ("milk", "dairy", "lactose"),
    "egg": ("egg",),
    "gluten": ("wheat", "gluten"),
    "fish": ("fish",),
    "soy": ("soy",),
    "sesame": ("sesame",),
    # Not top-eight allergens, but ones a household writes down and the
    # old twelve-snack pool already protected (2026-10-19).
    "legumes": ("legumes", "chickpeas"),
    "corn": ("corn",),
    "citrus": ("citrus",),
}

# What each diet word keeps off a snack. The allergen matcher does not read
# "vegetarian" as a family (big_meal.py says so), so it is read here.
_DIET_EXCLUDES = {
    "vegan": {"meat", "fish", "egg", "dairy", "honey"},
    "plant-based": {"meat", "fish", "egg", "dairy", "honey"},
    "vegetarian": {"meat", "fish"},
    "pescatarian": {"meat"},
}
_DIET_RE = re.compile(r"\b(vegan|plant[\s-]based|vegetarian|pescatarian)\b", re.IGNORECASE)
_DIET_NEGATED = re.compile(r"\b(?:not|no|non|isn'?t|aren'?t)\s*-?\s*$", re.IGNORECASE)

_STOP = {"with", "and", "a", "an", "the", "of", "on", "plus", "few", "small", "bowl", "some", "&"}


def _key(name: str | None) -> frozenset:
    """A name as a set of its words — "Apple slices & cheddar with pretzels"
    and "Apple Slices with Cheddar and Pretzels" are one snack."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("'", ""))
    return frozenset(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
                     for w in words if w not in _STOP)


def _variant(raw: dict, base: dict | None = None) -> dict:
    base = base or {}
    allergens = [a for a in (raw.get("allergens") if "allergens" in raw else base.get("allergens") or [])]
    animal = set(raw.get("animal") if "animal" in raw else base.get("animal") or [])
    animal |= {a for a in allergens if a in ("fish", "egg", "dairy")}
    return {
        "name": raw["name"],
        "group": raw.get("group") or base.get("group") or "",
        "food_groups": list(raw.get("food_groups") or base.get("food_groups") or []),
        "allergens": allergens,
        "animal": sorted(animal),
        "lines": [tuple(line) for line in (raw.get("lines") or base.get("lines") or [])],
    }


@functools.lru_cache(maxsize=1)
def load_list() -> tuple[dict, ...]:
    """Every list snack, in the file's order: {name, group, food_groups,
    allergens, animal, lines, swap} — `swap` the nut-free version or None."""
    with open(LIST_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    out = []
    for i, entry in enumerate(raw.get("snacks") or []):
        snack = _variant(entry)
        snack["index"] = i
        swap = entry.get("nut_free_swap")
        snack["swap"] = _variant(swap, entry) if swap else None
        if snack["swap"]:
            snack["swap"]["index"] = i
        out.append(snack)
    return tuple(out)


def _all_keys() -> dict[frozenset, dict]:
    out = {}
    for snack in load_list():
        out[_key(snack["name"])] = snack
        if snack["swap"]:
            out[_key(snack["swap"]["name"])] = snack
    return out


def on_list(name: str | None) -> bool:
    return _key(name) in _all_keys()


def hidden_words(variant: dict) -> list[str]:
    """What the allergen gate is handed beyond the item lines."""
    return [w for a in variant["allergens"] for w in _ALLERGEN_WORDS.get(a, (a,))]


# A line's last word that says nothing about what the week buys ("olive
# oil", "cheddar cheese", "tortilla chips"): the word before it is the key,
# and a line that is only a pantry staple has none.
_GENERIC_TAIL = {"cheese", "butter", "oil", "seasoning", "sauce", "chips", "seeds", "bread", "crackers",
                 "sheets", "snacks", "triangles", "balls", "crust", "chunks", "leaves", "masala", "stalks"}
# How a thing is cut or packed, never what it is: "sliced cheese" is keyed
# on cheese, not on "sliced" (which the week's "Sliced Orange" also says).
_MODIFIERS = {"sliced", "shredded", "plain", "frozen", "canned", "fresh", "ground", "mini", "string",
              "cream", "whole", "grain", "roasted", "rolled", "cooked", "dried", "deli", "seed", "rice"}
_NO_KEY = {"olive oil", "honey", "ground cinnamon", "smoked paprika", "mayonnaise", "soy sauce",
           "everything bagel seasoning", "za'atar", "chaat masala", "tajín seasoning", "salsa", "pizza sauce"}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3]          # berries -> berr (berry / berries)
    if len(word) > 4 and word.endswith("oes"):
        return word[:-2]          # tomatoes -> tomato
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def line_key(item: str) -> str:
    """The word the week's own food is searched for, or "" for none."""
    item = (item or "").lower().strip()
    if item in _NO_KEY:
        return ""
    words = re.findall(r"[a-z']+", item)
    if not words:
        return ""
    word = words[-1]
    if word in _GENERIC_TAIL and len(words) > 1 and words[-2] not in _MODIFIERS:
        word = words[-2]
    return _stem(word)


def pool_lines(variant: dict) -> list[tuple]:
    """The snack_variety pool's line shape: (item, amount per person, unit,
    category, key) — `key` the word the week's own food is searched for."""
    return [(item, value, unit, category, line_key(item)) for item, value, unit, category in variant["lines"]]


def as_pool(variant: dict) -> dict:
    return {"name": variant["name"], "groups": variant["food_groups"], "lines": pool_lines(variant),
            "contains": hidden_words(variant), "everyday": True}


def ingredients(variant: dict, serves: int) -> list[dict]:
    from . import snack_variety as _snack_variety
    return _snack_variety.pool_ingredients(as_pool(variant), serves)


# ---------- this household ----------


def _diet_texts(memory: dict) -> list[str]:
    from . import household as _household
    from . import memory as _memory
    texts = [r for m in _household.list_members() for r in (m.get("dietary_restrictions") or [])]
    texts.extend(f.get("text") or "" for f in _memory.get_facts() if f.get("hard"))
    texts.append(str(memory.get("eating_style") or ""))
    return texts


def diet_excludes(memory: dict) -> set[str]:
    """The animal foods this household's own words keep off a snack."""
    out: set[str] = set()
    for text in _diet_texts(memory):
        for m in _DIET_RE.finditer(text or ""):
            if _DIET_NEGATED.search(text[:m.start()]):
                continue
            out |= _DIET_EXCLUDES[re.sub(r"[\s-]+", "-", m.group(1).lower())]
    return out


def style_stands_down(memory: dict) -> bool:
    """An eating style the list can't be judged against in code (keto, a
    list of allowed foods). A style that is only a diet word is read by
    diet_excludes and needs no stand-down."""
    style = str(memory.get("eating_style") or "")
    rest = _DIET_RE.sub(" ", style)
    return bool(re.sub(r"[\W_]+|\b(?:diet|mostly|style|eating|household|we|are|is|all)\b", "", rest, flags=re.I))


def disliked(words: str, dislikes: list[str]) -> bool:
    """A standing dislike named in these words, as a whole word (plural
    allowed): "pea" is snap peas, never pear or chickpeas; "ham" is not
    muhammara."""
    words = (words or "").lower()
    return any(d and d.strip() and re.search(rf"\b{re.escape(d.strip().lower())}(?:s|es)?\b", words)
               for d in dislikes or [])


def _disliked(variant: dict, dislikes: list[str]) -> bool:
    return disliked(" ".join([variant["name"]] + [line[0] for line in variant["lines"]]), dislikes)


def _fits(variant: dict, avoid_hard: list[dict], excludes: set[str], dislikes: list[str]) -> bool:
    if set(variant["animal"]) & excludes or _disliked(variant, dislikes):
        return False
    return not _clashes(variant, avoid_hard)


# One generation asks for the household's list several times (the prompt,
# the pre-gate swap, both count runs, the variety pool); the answer is a
# pure function of these inputs, so it is kept keyed on all of them.
_ELIGIBLE_CACHE: dict = {}


def eligible(memory: dict, all_variants: bool = False) -> list[dict]:
    """The list as this household can have it, in the file's order: each
    snack as itself, or as its nut-free swap when only the swap passes, or
    left out. `all_variants` keeps a swap that passes beside its original
    (both are on the list for a house with no nut rule)."""
    from . import allergen_gate as _allergen_gate
    avoid_hard = _allergen_gate.hard_avoidances()
    excludes = diet_excludes(memory)
    dislikes = memory.get("dislikes") or []
    sig = (household_id(), all_variants, json.dumps(avoid_hard, sort_keys=True, default=str),
           tuple(sorted(excludes)), tuple(dislikes), LIST_PATH.stat().st_mtime)
    if sig in _ELIGIBLE_CACHE:
        return list(_ELIGIBLE_CACHE[sig])
    out = []
    for snack in load_list():
        for variant in (snack, snack["swap"]):
            if variant and _fits(variant, avoid_hard, excludes, dislikes):
                out.append(variant)
                if not all_variants:
                    break
    if len(_ELIGIBLE_CACHE) > 64:
        _ELIGIBLE_CACHE.clear()
    _ELIGIBLE_CACHE[sig] = tuple(out)
    return out


def last_used(before: str | None = None, exclude_plan_id: int | None = None) -> dict[frozenset, str]:
    """The last date each snack was on an approved week, by name key."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT COALESCE(r.name, mpe.freeform_meal) AS meal, MAX(mpe.date) AS last
        FROM meal_plan_entries mpe
        JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.slot = 'snack' AND mpe.slot_state = 'planned'
          AND wp.status = 'approved' AND mpe.weekly_plan_id != ? AND (? IS NULL OR mpe.date < ?)
        GROUP BY meal
        """,
        (household_id(), exclude_plan_id or -1, before, before),
    ).fetchall()
    conn.close()
    out: dict[frozenset, str] = {}
    for r in rows:
        if r["meal"]:
            k = _key(r["meal"])
            out[k] = max(out.get(k, ""), r["last"] or "")
    return out


def _recent_keys(period_start: str | None, plan_id: int | None) -> set[frozenset]:
    from . import draft_opener as _draft_opener
    if not period_start:
        return set()
    return {_key(n) for n in (_draft_opener.recent_dish_names(period_start, plan_id) or ())}


def rotation(memory: dict, period_start: str | None, plan_id: int | None = None) -> list[dict]:
    """The household's eligible snacks outside the no-repeat window, least
    recently used first (never used first of all, then the file's order)."""
    used = last_used(period_start, plan_id)
    recent = _recent_keys(period_start, plan_id)
    fresh = [v for v in eligible(memory) if _key(v["name"]) not in recent]
    return sorted(fresh, key=lambda v: (used.get(_key(v["name"]), ""), v["index"]))


def prompt_context(memory: dict, period_start: str | None) -> dict | None:
    """What the drafting call is handed as `everyday_snacks`, or None for a
    house with no snacks."""
    try:
        picks = rotation(memory, period_start)
    except Exception:
        logger.exception("Everyday snack shortlist failed; the draft goes without it")
        return None
    if not picks:
        return {"pick_from": [], "nothing_fits": True}
    return {"pick_from": [{"name": v["name"], "food_groups": v["food_groups"]}
                          for v in picks[:SHORTLIST_SIZE]]}


# ---------- the draft as it comes back ----------


def _clashes(variant: dict, avoid_hard: list[dict]) -> list[dict]:
    from . import allergen_gate as _allergen_gate
    items = [{"item": line[0], "qty": ""} for line in variant["lines"]]
    items += [{"item": w, "qty": ""} for w in hidden_words(variant)]
    return _allergen_gate.hard_clashes(variant["name"], ingredients=items, avoidances=avoid_hard)


def swap_for_household(items: list[dict], memory: dict) -> list[dict]:
    """
    Before the allergen gate, every list snack the model sent is read with
    the list's OWN allergen tags (a model's tuna salad may leave the mayo
    off its lines; everything seasoning hides sesame):

      - one this house can only have as its nut-free swap becomes the swap,
        in place — "Rice Cakes with Almond Butter" would otherwise be held
        back and the snack lost;
      - one this house can't have at all comes out of `items` and is
        returned in split_safe's held shape ({"item", "clashes"}), so it is
        re-picked like any held dish (fill_held_snacks first).

    Runs whatever the eating style: allergens are never a style question.
    Never raises; on a failure the items go to the gate as sent.
    """
    from . import allergen_gate as _allergen_gate
    from . import swap_in_place as _swap
    held: list[dict] = []
    try:
        snack_items = [i for i in items or [] if (i.get("slot") or "") == "snack" and i.get("meal_name")]
        if not snack_items:
            return held
        listed = _all_keys()
        avoid_hard = _allergen_gate.hard_avoidances()
        excludes = diet_excludes(memory)
        for item in snack_items:
            k = _key(item["meal_name"])
            own = listed.get(k)
            if not own:
                continue
            sent = own if _key(own["name"]) == k else own["swap"]
            if not (set(sent["animal"]) & excludes or _clashes(sent, avoid_hard)):
                continue
            swap = own["swap"]
            if swap and swap is not sent and not (set(swap["animal"]) & excludes or _clashes(swap, avoid_hard)):
                serves = _swap._table_for(item.get("date") or "", "snack")["serves"] or 1
                item.update({"meal_name": swap["name"], "ingredients": ingredients(swap, serves),
                             "food_groups": list(swap["food_groups"]), "is_new_recipe": True,
                             "default_servings": serves})
                continue
            clashes = _clashes(sent, avoid_hard) or [{"meal": sent["name"], "member": None, "severity": "hard",
                                                      "restriction": "diet", "source": "everyday_snacks",
                                                      "matched": ", ".join(sorted(set(sent["animal"]) & excludes))}]
            held.append({"item": item, "clashes": clashes})
        if held:
            gone = {id(h["item"]) for h in held}
            items[:] = [i for i in items if id(i) not in gone]
            logger.warning("Generation drafted %d list snack(s) this house can't have — held back: %s",
                           len(held), ", ".join(sorted({h["item"]["meal_name"] for h in held})))
    except Exception:
        logger.exception("Everyday snack check failed; the draft's snacks go to the gate as sent")
        return []
    return held


# ---------- after the draft ----------


def _save(variant: dict, serves: int) -> None:
    from . import recipes as _recipes
    from . import snack_variety as _snack_variety
    existing = _recipes.existing_recipe_named(variant["name"])
    if not existing:
        _recipes.add_recipe(name=variant["name"], ingredients=ingredients(variant, serves),
                            food_groups=variant["food_groups"], default_servings=serves,
                            prep_time_minutes=5)
    else:
        _snack_variety._rescale_pool_recipe(existing["id"], as_pool(variant),
                                            _recipes.saved_ingredients(variant["name"]), serves)


class _Chooser:
    """Picks a list snack for a dish's days in one plan: this household's
    eligible list, least recently used first, outside the no-repeat window,
    never a snack (or its swap) already on the week, never that day's other
    food, past the allergen gate on the list's tags AND on a saved recipe of
    that name, and past the taste veto for each day."""

    def __init__(self, plan_id: int, memory: dict, period_start: str | None, dates: list[str]):
        from . import meal_variety as _meal_variety
        self.ok = {_key(v["name"]) for v in eligible(memory, all_variants=True)}
        self.recent = _recent_keys(period_start, plan_id)
        used = last_used(period_start, plan_id)
        self.candidates = sorted((v for v in eligible(memory) if _key(v["name"]) not in self.recent),
                                 key=lambda v: (used.get(_key(v["name"]), ""), v["index"]))
        self.listed = _all_keys()
        self.day_food = _meal_variety._day_food(plan_id, sorted(set(dates)))
        self.on_week: set[int] = set()

    def snack_index(self, name: str) -> int | None:
        own = self.listed.get(_key(name))
        return own["index"] if own else None

    def needs_replacing(self, name: str) -> bool:
        k = _key(name)
        return k not in self.ok or k in self.recent

    def fits(self, variant: dict, dates: list[str], leaving: str = "") -> bool:
        from . import plan_quality as _plan_quality
        from . import recipes as _recipes
        from . import swap_in_place as _swap
        if variant["index"] in self.on_week:
            return False
        # A recipe of that name already in their box is what the week will
        # plan and buy, so ITS list is checked too (snack_variety does the
        # same): theirs may be made with peanut butter.
        saved = (_recipes.saved_ingredients(variant["name"])
                 if _recipes.existing_recipe_named(variant["name"]) else [])
        candidate = {"meal_name": variant["name"],
                     "ingredients": ingredients(variant, 1) + saved
                     + [{"item": w, "qty": ""} for w in hidden_words(variant)],
                     "food_groups": variant["food_groups"]}
        # What the snack is made of, by the same words the variety pool
        # searches the week for: "Overnight Oats Jars" is oats, and so is
        # that morning's "Oatmeal" — one day never eats the same thing twice.
        keys = [k for k in (line[4] for line in pool_lines(variant)) if len(k) >= 3]
        for date in dates:
            food = [v for k, v in self.day_food.get(date, {}).items() if k != leaving]
            if any(_key(variant["name"]) == _key(f) or _plan_quality.shared_food(variant["name"], f)
                   or any(re.search(rf"\b{re.escape(k)}", f.lower()) for k in keys)
                   for f in food):
                return False
            # The allergen match (the list's allergens, their saved list)
            # and the one-veto taste rule, for whoever is home that day.
            if _swap.pick_gate(candidate, {"date": date, "slot": "snack"}):
                return False
        return True

    def choose(self, name: str, dates: list[str], leaving: str = "") -> dict | None:
        own = self.listed.get(_key(name))
        # The list's own snack this house can't have as written: its
        # nut-free swap first, so the snack is kept rather than lost.
        first = [v for v in ((own, own["swap"]) if own else ())
                 if v and _key(v["name"]) in self.ok and _key(v["name"]) not in self.recent]
        return next((v for v in first + self.candidates if self.fits(v, dates, leaving)), None)

    def landed(self, variant: dict, dates: list[str], leaving: str = "") -> None:
        self.on_week.add(variant["index"])
        for date in dates:
            food = self.day_food.setdefault(date, {})
            food.pop(leaving, None)
            food[variant["name"].lower()] = variant["name"]


def fill_held_snacks(plan_id: int, held: list[dict], memory: dict, period_start: str | None) -> list[dict]:
    """
    Snacks the allergen gate held back are re-picked from the list first —
    one list snack per held dish, on all of its days, no model call —
    before allergen_gate.repick_held spends a call on them. Returns what is
    still held (everything else, and a snack nothing on the list fits).
    Stands down under an eating style the list can't be judged against.
    Never raises.
    """
    from . import meal_plans as _meal_plans
    from . import swap_in_place as _swap
    snacks = [h for h in held or [] if (h["item"].get("slot") or "") == "snack" and h["item"].get("date")]
    if not snacks or style_stands_down(memory):
        return held
    rest = [h for h in held if h not in snacks]
    try:
        groups: dict[str, list[dict]] = {}
        for h in snacks:
            groups.setdefault((h["item"].get("meal_name") or "").strip().lower(), []).append(h)
        dates = [h["item"]["date"] for h in snacks]
        chooser = _Chooser(plan_id, memory, period_start, dates)
        from . import meal_variety as _meal_variety
        for e in _meal_variety._load_slot_entries(plan_id, "snack"):
            if e["slot_state"] == "planned" and e["meal"]:
                idx = chooser.snack_index(e["meal"])
                if idx is not None:
                    chooser.on_week.add(idx)
        for name, hs in groups.items():
            days = sorted({h["item"]["date"] for h in hs})
            pick = chooser.choose(name, days)
            if pick is None:
                rest.extend(hs)
                continue
            _save(pick, _swap._table_for(days[0], "snack")["serves"] or 1)
            for date in days:
                _meal_plans.plan_meal(meal_date=date, meal=pick["name"], slot="snack",
                                      food_groups=list(pick["food_groups"]), weekly_plan_id=plan_id,
                                      derived_from={"constraint": CONSTRAINT, "replaced": hs[0]["item"]["meal_name"]})
            chooser.landed(pick, days)
            logger.info("Plan %s: held snack %r re-picked from the everyday list as %r",
                        plan_id, hs[0]["item"]["meal_name"], pick["name"])
    except Exception:
        logger.exception("Filling held snacks from the everyday list failed for plan %s", plan_id)
        return held
    return rest


def enforce_snacks_from_list(plan_id: int, dates: list[str], memory: dict, period_start: str | None = None,
                             asks: tuple[str | None, ...] = ()) -> dict:
    """See the module docstring. Returns what it replaced and what it left."""
    from . import meal_variety as _meal_variety
    from . import swap_in_place as _swap
    from . import weekly_plan as _weekly_plan

    out = {"replaced": [], "left": [], "skipped": None}
    if not dates:
        return out
    if style_stands_down(memory):
        out["skipped"] = "eating style"
        return out
    try:
        in_scope = set(dates)
        snacks = [e for e in _meal_variety._load_slot_entries(plan_id, "snack")
                  if e["date"] in in_scope and e["slot_state"] == "planned" and e["meal"]]
        if not snacks:
            return out
        words = tuple(asks) + (memory.get("notes"), memory.get("typical_week"), memory.get("next_week_notes"))
        dishes: dict[str, dict] = {}
        for e in sorted(snacks, key=lambda e: (e["date"], e["id"])):
            dish = dishes.setdefault(e["meal"].strip().lower(),
                                     {"name": e["meal"].strip(), "entries": [], "protected": False})
            dish["entries"].append(e)
            derived = json.loads(e["derived_from_json"] or "{}") or {}
            if _meal_variety.theirs(derived) or (e["cooked_status"] or "") == "done":
                dish["protected"] = True
        for dish in dishes.values():
            if not dish["protected"] and _meal_variety.asked_for_by_name(dish["name"], words):
                dish["protected"] = True

        chooser = _Chooser(plan_id, memory, period_start, sorted(in_scope))
        todo = [d for d in dishes.values() if not d["protected"] and chooser.needs_replacing(d["name"])]
        if not todo:
            return out
        for d in dishes.values():
            idx = chooser.snack_index(d["name"])
            if d not in todo and idx is not None:
                chooser.on_week.add(idx)

        for dish in todo:
            leaving = dish["name"].lower()
            days = [e["date"] for e in dish["entries"]]
            pick = chooser.choose(dish["name"], days, leaving)
            if pick is None:
                out["left"].append(dish["name"])
                continue
            _save(pick, _swap._table_for(days[0], "snack")["serves"] or 1)
            for e in dish["entries"]:
                _weekly_plan._replace_slot_entries(
                    plan_id, [e["id"]], e["date"], "snack", pick["name"],
                    food_groups=pick["food_groups"], reasoning="",
                    derived_from={"constraint": CONSTRAINT, "replaced": e["meal"]},
                )
            chooser.landed(pick, days, leaving)
            out["replaced"].append({"dropped": dish["name"], "with": pick["name"], "days": len(days)})
        if out["replaced"] or out["left"]:
            logger.info("Plan %s everyday snacks: replaced %d dish(es)%s", plan_id, len(out["replaced"]),
                        f", left {len(out['left'])} off-list (nothing on the list fit)" if out["left"] else "")
    except Exception:
        logger.exception("Everyday snack pass failed for plan %s; the snacks stand as planned", plan_id)
    return out
