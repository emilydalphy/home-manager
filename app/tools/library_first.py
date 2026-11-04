"""
Library first (Emily, 2026-11-03).

Live, one household, Oct 25–Nov 3: $3.75 of AI in 10 days against a
$1.50/household/month target. Research ($0.72) and the recipe writer
($0.62) were billed for every new dish, because the planner invents its
own names and the shared library (1,116 dishes researched AND written) is
looked up by name — 0 hits that month.

Emily's decisions, built here and at the call sites:
  1. When Pomona suggests a dish — drafting the week, swap options, "Swap ·
     I'll pick" — it picks from the shared library dishes that fit the
     household's rules before inventing anything. A library pick is copied
     as the library has it (agent._copy_from_library): no research, no
     writer call. The household's own serving size still scales it the way
     every recipe is scaled (scale_recipe / the cook card). A recipe that
     doesn't suit a child or the household's kit gets a small model edit
     (Emily, 2026-11-04: library_tweak).
  2. Only when nothing in the library fits — or, on a swap, the household
     turns the library's options down ("show me different ones", or types
     what they want) — is a new dish researched and written, as before.
  3. Whatever is researched and written new goes back into the library
     (recipe_library.file_new_recipe).

WHY THE NARROWING IS IN CODE, AND THE CHOOSING IS THE MODEL'S. The hard
rules are checked here on each dish's real ingredient list — the allergen
gate, the diet words, standing dislikes, a disliked or set-aside recipe,
the last two weeks' dishes — so nothing the model is handed is a dish the
household can't have. The model then chooses from a short list (name,
cuisine, protein, minutes) inside the call it already makes: the week's
shape (night tags and time caps, leftovers, typed requests, use-up items,
variety across cuisines and proteins) is the planner's job and stays in
one place. Choosing in code would mean a second planner. The list is
~40 dinners and ~25 lunches (~1,000 input tokens), spread across proteins
so a week can be varied from it, and different each week.

Stands down — the household is planned exactly as before — when the library
is switched off (RECIPE_LIBRARY=off), or for a diet the code can't read
(halal, kosher, "no meat" in a fact, an eating style like keto or
low-FODMAP: swap_option_picks._diet_the_library_cant_judge and
everyday_snacks.style_stands_down), because a library dish isn't written
around it and nothing here could tell.

Never raises: a failure here plans the week the old way.
"""
from __future__ import annotations

import logging
import random

from ._shared import household_id
from .recipe_research import dish_key

logger = logging.getLogger("home_manager")

# How many dishes the drafting prompt is handed per meal. Enough for a week
# of different dinners several times over, spread across proteins; few
# enough to stay ~1,000 input tokens.
DINNER_CANDIDATES = 40
LUNCH_CANDIDATES = 25
# The weeknight dinner share's cap when the household hasn't set one.
WEEKNIGHT_GUIDE_MINUTES = 45

# The library meals a slot may take (a lunch may be a dinner dish — the
# swap sheet's own rule, swap_option_picks.LIBRARY_MEALS_FOR_SLOT).
MEALS_FOR_SLOT = {
    "breakfast": ("breakfast",),
    "lunch": ("lunch", "dinner"),
    "dinner": ("dinner",),
    "snack": ("snack",),
}

# A gate check per dish is a real matcher run; a household with many
# must-avoids could turn away most of the library, so this many are tried
# at most per list.
_MAX_GATE_CHECKS = 400


def _seed_meals() -> dict[str, str]:
    """dish_key -> meal, from the starter list (data/recipe_library_seed.json,
    read once by swap_option_picks._seed_dishes — the one copy the swap sheet
    and this module both read): the meal of an entry filed before the
    library kept its own. Built per call (a millisecond), read once a list."""
    from . import swap_option_picks as _picks
    return {d.get("dish_key") or dish_key(d.get("dish_name") or ""): d.get("meal") or ""
            for d in _picks._seed_dishes() if d.get("dish_name")}


def diet_blocks_the_library(eating_style: str | None = None) -> bool:
    """
    THE ONE DIET CHECK every library path asks before it offers or copies a
    library dish (review, 2026-11-03: a vegetarian eating style was copied
    a Pad Thai with prawns and oyster sauce — the copy path had lost the
    eating-style check the draft list still had). True when:
      * the eating style, or any member restriction or hard fact
        (swap_in_place._hard_exclusions), names a diet the allergen gate
        can't read (agent._DIET_THE_GATE_CANT_READ: vegetarian, vegan,
        pescatarian, halal, kosher, "no meat"…) — no word list here can
        prove a library dish has no prawn, gelatin, lard or dashi in it;
      * the eating style is one the code can't read at all (keto,
        low-FODMAP: everyday_snacks.style_stands_down).
    `eating_style` defaults to the household's own; a caller holding the
    writer's spec passes that one (the stricter of the two is read: both).
    Fails CLOSED: a read that raises blocks the library.
    Used by: stands_down (the draft list, the swap sheet, "Swap · I'll
    pick"), agent._library_entry_for (every copy — the recipe pass and a
    tapped swap), swap_option_picks._diet_the_library_cant_judge (the swap
    sheet's fallback) and breakfast_variety's library breakfasts.
    """
    from .. import agent
    from . import everyday_snacks as _everyday
    from . import memory as _memory
    from . import swap_in_place as _swap
    try:
        styles = [str((_memory.get_household_memory() or {}).get("eating_style") or "")]
        if eating_style:
            styles.append(str(eating_style))
        lines = styles + [str(x or "") for x in (_swap._hard_exclusions() or [])]
        if any(agent._DIET_THE_GATE_CANT_READ.search(line) for line in lines):
            return True
        return any(style and _everyday.style_stands_down({"eating_style": style}) for style in styles)
    except Exception:
        logger.exception("Library first: could not read the household's diet; the library stands down")
        return True


def stands_down(memory: dict | None = None) -> bool:
    """True when the library can't be used for this household (see the
    module docstring) — it is then planned and swapped as before."""
    from . import recipe_library as _recipe_library
    try:
        if not _recipe_library.library_enabled():
            return True
        return diet_blocks_the_library((memory or {}).get("eating_style"))
    except Exception:
        logger.exception("Library first: could not read the household's diet; planning without it")
        return True


def entry_clashes(entry: dict, avoidances: list[dict] | None) -> bool:
    """The allergen gate on a library recipe, judged on the list the copy
    will SAVE (steps' ingredients included — agent._copy_from_library's
    own rule)."""
    from . import allergen_gate as _allergen_gate
    from . import recipes as _recipes
    judged = _recipes.complete_ingredient_list(entry.get("ingredients") or [], entry.get("instructions") or [],
                                               entry.get("default_servings") or 4)
    return bool(_allergen_gate.hard_clashes(entry.get("dish_name") or entry.get("name") or "",
                                            ingredients=judged, avoidances=avoidances))


def diet_refuses(dish: dict, memory: dict, excludes: set[str] | None = None) -> bool:
    """A diet said in words (vegetarian, vegan, pescatarian, no eggs…) that
    keeps this dish off the plate, read off its name and ingredients.
    `excludes` is everyday_snacks.diet_excludes(memory), when the caller has
    read it once already (it reads the members and facts)."""
    from . import breakfast_variety as _breakfast_variety
    from . import everyday_snacks as _everyday
    if excludes is None:
        excludes = _everyday.diet_excludes(memory)
    if not excludes:
        return False
    return _breakfast_variety._diet_refuses(
        {"dish_name": dish.get("name") or dish.get("dish_name") or "",
         "ingredients": [{"item": i} for i in dish.get("items") or []]
         or dish.get("ingredients") or []},
        excludes,
    )


def _refused_names() -> set[str]:
    """Recipes this household rated disliked or set aside, by repeat_key."""
    from . import meal_variety as _meal_variety
    from . import recipes as _recipes
    out = set()
    for r in _recipes.list_recipes():
        if (r.get("rating") or "") == "disliked" or r.get("temporarily_excluded"):
            out.add(_meal_variety.repeat_key(r.get("name")))
    return out


def _spread(dishes: list[dict], memory: dict, limit: int) -> list[dict]:
    """Up to `limit` dishes, round-robin across main proteins (most liked
    protein first, a protein rated 1 left out), the household's cuisines
    first within each protein — so a week can be varied from the list."""
    prefs = {str(k).lower(): v for k, v in (memory.get("protein_preferences") or {}).items()
             if isinstance(v, (int, float))}
    cuisines = {str(c).lower() for c in memory.get("cuisine_preferences") or []}
    buckets: dict[str, list[dict]] = {}
    for d in dishes:
        protein = (d.get("main_protein") or "other").lower()
        if prefs.get(protein, 3) <= 1:
            continue
        buckets.setdefault(protein, []).append(d)
    for protein, items in buckets.items():
        items.sort(key=lambda d: (d.get("cuisine") or "").lower() not in cuisines)  # stable: keeps the shuffle
    order = sorted(buckets, key=lambda p: (-prefs.get(p, 3), p))
    out: list[dict] = []
    while len(out) < limit and any(buckets.values()):
        for protein in order:
            if buckets[protein] and len(out) < limit:
                out.append(buckets[protein].pop(0))
    return out


def candidates(slot: str, memory: dict, *, avoid=(), seed: str = "", limit: int = DINNER_CANDIDATES,
               avoidances: list[dict] | None = None, max_minutes: int | None = None) -> list[dict]:
    """
    Library dishes for `slot` this household may be offered, narrowed in
    code by every hard rule (see the module docstring), in an order that is
    the household's and the week's own (`seed`), spread across proteins.
    Each is {name, cuisine, main_protein, minutes, meal}. [] when the
    library stands down for this household.
    """
    from . import allergen_gate as _allergen_gate
    from . import everyday_snacks as _everyday
    from . import meal_variety as _meal_variety
    from . import recipe_library as _recipe_library
    try:
        if stands_down(memory):
            return []
        meals = MEALS_FOR_SLOT.get(slot, (slot,))
        refused = {_meal_variety.repeat_key(n) for n in avoid if n} | _refused_names()
        dislikes = memory.get("dislikes") or []
        children = _has_children()
        excludes = _everyday.diet_excludes(memory)
        avoidances = _allergen_gate.hard_avoidances() if avoidances is None else avoidances

        def _unwanted(name: str, items: list[str], minutes) -> bool:
            if max_minutes and minutes and minutes > max_minutes:
                return True
            return (diet_refuses({"name": name, "items": items}, memory, excludes)
                    or _everyday.disliked(" ".join([name] + items), dislikes))

        pool = []
        seed_meals = _seed_meals()
        for d in _recipe_library.written_dishes():
            d = dict(d, meal=d.get("meal") or seed_meals.get(d["dish_key"], ""))
            if d["meal"] not in meals or _meal_variety.repeat_key(d["name"]) in refused:
                continue
            # A cheap first pass on the listed version; the version actually
            # served is checked again below.
            if _unwanted(d["name"], d["items"], d.get("minutes")):
                continue
            pool.append(d)
        random.Random(f"{household_id()}:{seed}:{slot}").shuffle(pool)
        # The slot's own meal first (a lunch list is lunches, topped up with
        # dinners only when there are too few).
        pool.sort(key=lambda d: meals.index(d["meal"]))
        safe, checked = [], 0
        for d in pool:
            if len(safe) >= limit * 2 or checked >= _MAX_GATE_CHECKS:
                break
            checked += 1
            # Every rule on the version a copy would SERVE (review,
            # 2026-11-03): the base, else a variant that fits this
            # household (recipe_library.servable_recipe) — its allergens,
            # diet words, dislikes and minutes, not the listed version's.
            entry, _ = _recipe_library.servable_recipe(
                d["name"], children=children, has_avoidances=bool(avoidances),
                clashes=lambda e: bool(avoidances) and entry_clashes(e, avoidances))
            if entry is None:
                continue
            items = [str(i.get("item") or "") for i in entry.get("ingredients") or [] if isinstance(i, dict)]
            minutes = (int(entry.get("prep_time_minutes") or 0) + int(entry.get("cook_time_minutes") or 0)) or None
            if _unwanted(entry["dish_name"], items, minutes):
                continue
            safe.append(dict(d, minutes=minutes, cuisine=entry.get("cuisine") or d["cuisine"],
                             main_protein=entry.get("main_protein") or d["main_protein"]))
        picked = _spread(safe, memory, limit)
        return [{"name": d["name"], "cuisine": d["cuisine"], "main_protein": d["main_protein"],
                 "minutes": d["minutes"], "meal": d["meal"]} for d in picked]
    except Exception:
        logger.exception("Library first: building the %s list failed; planning without it", slot)
        return []


def prompt_lines(dishes: list[dict]) -> list[str]:
    """The compact form the drafting prompt reads: "Name | cuisine | protein | 35 min"."""
    return [" | ".join([d["name"], d.get("cuisine") or "-", d.get("main_protein") or "-",
                        f"{d['minutes']} min" if d.get("minutes") else "-"]) for d in dishes]


def for_prompt(memory: dict, *, avoid=(), seed: str = "", avoidances: list[dict] | None = None) -> dict:
    """{"dinner": [...], "lunch": [...]} for the drafting context, only the
    meals the household plans and only non-empty lists; {} when the library
    stands down."""
    out = {}
    if stands_down(memory):
        return out
    from . import time_caps as _time_caps
    weeknight = _positive(memory.get("weeknight_max_minutes")) or WEEKNIGHT_GUIDE_MINUTES
    lunch_cap = _positive(memory.get("weekday_lunch_max_minutes")) or _time_caps.WEEKDAY_LUNCH_MAX_MINUTES
    # Most nights have a cap, so most of the list fits one: a share quick
    # enough for a rush night, a share within the weeknight cap, the rest
    # any length (a weekend, an unrushed night).
    tiers = {
        "dinner": ("dinners_per_week", [(_time_caps.RUSH_MAX_MINUTES, 10), (weeknight, 20), (None, 10)]),
        "lunch": ("lunches_per_week", [(lunch_cap, 12), (None, LUNCH_CANDIDATES - 12)]),
    }
    taken: set[str] = set()
    for slot, (count_key, parts) in tiers.items():
        count = memory.get(count_key)
        if count is not None and int(count or 0) == 0:
            continue
        dishes: list[dict] = []
        for cap, share in parts:
            # Nothing twice: not across the tiers, not dinner and lunch.
            got = candidates(slot, memory, avoid=list(avoid) + sorted(taken), seed=seed, limit=share,
                             avoidances=avoidances, max_minutes=cap)
            taken |= {d["name"] for d in got}
            dishes += got
        if dishes:
            out[slot] = prompt_lines(dishes)
    return out


def _positive(value) -> int | None:
    try:
        return int(value) if value and int(value) > 0 else None
    except (TypeError, ValueError):
        return None


def _has_children() -> bool:
    """Children eat here — the recipe pass's own reading (children_at_table)."""
    from . import recipe_research as _recipe_research
    try:
        return bool(_recipe_research.children_eat_here())
    except Exception:
        return False



# ---------- the write-back rule, for saves outside the recipe pass ----------

def household_kinds() -> dict:
    """What a writer outside the recipe pass was told about this household,
    as the spec keys file_new_recipe reads (truthy or not) — in ONE
    connection, because a swap pays for every read (test_swap_dinner_swaps_
    lunch's connection guard). Leans to "written around them": a household
    with any restriction or hard fact counts as allergies."""
    from ..db import get_conn
    from ._shared import EATS_HERE_SQL
    hid = household_id()
    conn = get_conn()
    try:
        members = conn.execute(
            f"SELECT age_group, dietary_restrictions_json FROM members WHERE household_id = ? AND {EATS_HERE_SQL}",
            (hid,)).fetchall()
        hard_fact = conn.execute("SELECT 1 FROM facts WHERE household_id = ? AND hard = 1 LIMIT 1",
                                 (hid,)).fetchone()
        prefs = conn.execute("SELECT eating_style, kitchen_kit_json FROM meal_preferences WHERE household_id = ?",
                             (hid,)).fetchone()
        stock = conn.execute("SELECT 1 FROM inventory_items WHERE household_id = ? LIMIT 1", (hid,)).fetchone()
    finally:
        conn.close()
    restricted = any((m["dietary_restrictions_json"] or "[]").strip() not in ("", "[]") for m in members)
    return {
        "must_not_contain": bool(restricted or hard_fact),
        "children_at_table": any((m["age_group"] or "").strip().lower() == "child" for m in members),
        "eating_style": bool(prefs and (prefs["eating_style"] or "").strip()),
        "kitchen_kit": bool(prefs and (prefs["kitchen_kit_json"] or "[]").strip() not in ("", "[]")),
        "current_inventory": bool(stock),
    }


def file_written(saved: dict | int | None, *, slot: str = "", spec: dict | None = None) -> int | None:
    """
    File a recipe the model just wrote and the household just saved —
    a swap, a re-pick, a filled day, a big meal's main — through THE
    write-back rule (recipe_library.file_new_recipe). `spec` defaults to
    what every writer is told about this household
    (agent._shared_recipe_details_context: must-avoids, children, eating
    style, kit, what's on hand), which decides base or variant. Never
    raises: the household's recipe is saved either way.
    """
    from . import recipe_library as _recipe_library
    try:
        recipe_id = saved if isinstance(saved, int) else ((saved or {}).get("recipe_id") or (saved or {}).get("id"))
        if not recipe_id or not _recipe_library.library_enabled():
            return None
        if spec is None:
            spec = household_kinds()
        return _recipe_library.file_new_recipe(recipe_id, spec=spec, meal=slot if slot in _recipe_library.MEALS else "")
    except Exception:
        logger.exception("Filing a written recipe in the library failed; the recipe is saved")
        return None


# ---------- swaps ----------

def swap_picks(weekly_plan_id: int, entry: dict, tried: list[str], context: dict | None = None,
               limit: int = 3) -> list[dict]:
    """
    Library dishes for one slot's swap, as picks (the swap sheet's shape,
    `from_library` set), narrowed by the same hard rules as the draft plus
    the slot's own: its time cap (`max_minutes`), the rest of the week (no
    repeat), what has been turned down (`tried`). Spread across proteins and
    cuisines, the protein and cuisine of the dish being replaced last.
    UNGATED for the table's taste vetoes — the caller runs its own gate
    (swap_in_place.pick_gate / swap_option_picks._gated), which is the one
    the model's picks pass too. [] when the library stands down.
    """
    from . import memory as _memory
    from . import recipe_library as _recipe_library
    from . import draft_opener as _draft_opener
    from . import swap_in_place as _swap
    try:
        memory = _memory.get_household_memory() or {}
        if stands_down(memory):
            return []
        context = context or {}
        # The week's other dishes arrive as "Monday dinner: Chili".
        avoid = list(tried) + [str(line).split(": ", 1)[-1] for line in context.get("week_other_dishes") or []]
        week_start = _swap._week_start_of(weekly_plan_id)
        if week_start:
            avoid += sorted(_draft_opener.recent_dish_names(week_start, weekly_plan_id) or ())
        found = candidates(entry["slot"], memory, avoid=avoid, seed=f"swap:{entry.get('entry_id')}",
                           limit=12, max_minutes=context.get("max_minutes"))
        replaced = _replaced_shape(entry["meal"])
        # A different night, not the same night renamed: another protein and
        # cuisine than the dish going out come first.
        found.sort(key=lambda d: ((d.get("main_protein") or "").lower() == replaced[0],
                                  (d.get("cuisine") or "").lower() == replaced[1]))
        from . import allergen_gate as _allergen_gate
        avoidances = _allergen_gate.hard_avoidances()
        children = _has_children()
        picks, seen_proteins = [], set()
        for d in found:
            # The very version a tap would copy (base, else a variant that
            # fits here), so the sheet's gates judge what will be cooked.
            full, _ = _recipe_library.servable_recipe(
                d["name"], children=children, has_avoidances=bool(avoidances),
                clashes=lambda e: entry_clashes(e, avoidances))
            if full is None:
                continue
            protein = (d.get("main_protein") or "").lower()
            if protein in seen_proteins and len(found) > limit:
                continue
            seen_proteins.add(protein)
            picks.append(as_pick(full))
            if len(picks) >= limit:
                break
        return picks
    except Exception:
        logger.exception("Library first: swap picks failed; asking the model instead")
        return []


def _replaced_shape(meal: str) -> tuple[str, str]:
    from . import recipes as _recipes
    found = _recipes.existing_recipe_named(meal or "") if meal else None
    row = (_recipes.get_recipe(found["name"]) or {}) if found else {}
    return ((row.get("main_protein") or "").lower(), (row.get("cuisine") or "").lower())


def as_pick(full: dict) -> dict:
    """A library recipe as a swap pick: the whole list (what the gates judge
    and what the copy will cook) and its minutes, marked from_library."""
    minutes = int(full.get("prep_time_minutes") or 0) + int(full.get("cook_time_minutes") or 0)
    return {
        "meal_name": full["dish_name"],
        "reason": "",
        "ingredients": [{"item": r.get("item")} for r in full.get("ingredients") or [] if r.get("item")],
        "minutes": minutes or None,
        "cuisine": full.get("cuisine") or "",
        "main_protein": full.get("main_protein") or "",
        "from_library": True,
    }


def copy_for_swap(name: str, serves: int | None = None) -> bool:
    """
    Make the library dish `name` this household's own saved recipe, copied
    as the library has it (agent._copy_from_library: the allergen gate on
    the full list, sources, library_id, its own amounts) — no writer call.
    True when the household now has a full recipe by that name.
    """
    from .. import agent
    from . import allergen_gate as _allergen_gate
    from . import recipes as _recipes
    existing = _recipes.existing_recipe_named(name)
    if existing and not _recipes.get_recipe(existing["name"]).get("details_pending"):
        return True
    avoidances = _allergen_gate.hard_avoidances()
    shared = agent._shared_recipe_details_context()
    # Asked BEFORE anything is saved: a pending row left behind by a refused
    # copy would read as "the household has this dish" to the swap's own
    # write-out check, and an approved week would plan a recipe with no list.
    entry, _ = agent._library_entry_for(name, avoidances, shared)
    if entry is None:
        return False
    if not existing:
        _recipes.add_recipe(name=name, ingredients=[], details_pending=True,
                            default_servings=serves or 4)
    copied, _ = agent._copy_from_library({"name": name}, name, name, avoidances, shared)
    row = _recipes.existing_recipe_named(name)
    return bool(copied) and bool(row) and not _recipes.get_recipe(row["name"]).get("details_pending")
