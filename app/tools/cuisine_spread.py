"""
A week with no cuisine asked for spreads across cuisines.

Emily, 2026-10-31: "I didn't give it any restrictions with cuisines and I'm
surprised it didn't give me as much variety with it still. Why is that."
Why: the spread was only ever a sentence in the drafting prompt ("avoid
repeating the same main_protein or cuisine too many days in a row for
dinner") — nothing counted the cuisines the model sent back, nothing put a
crowded week right, and lunches were not mentioned at all. Telling the
generator is not the same as preventing it (CLAUDE.md); this is the pass
that does the counting.

THE RULE, for a household with no cuisine preferences and no cuisine chip
this week (a household WITH either keeps exactly what it asked for — this
pass stands down):
  - no one cuisine on more than MAX_DINNERS_PER_CUISINE dinner DISHES — a
    dish cooked once and eaten twice is one dish, so a batch is never read
    as a crowded cuisine;
  - the week's dinners and lunches together cover at least MIN_CUISINES
    cuisines (or as many as there are dishes, on a short week).

How: a dish from the most crowded cuisine — the latest one in the week,
never one they asked for by name or that carries their own words, never one
whose leftovers chain reaches outside it — is re-picked whole through the
swap's own picker with the crowded cuisines on `avoid_cuisines`, on the
generation's SHARED call budget (allergen_gate.MAX_REPICK_CALLS for the
whole week). A pick with no cuisine, or one of the crowded ones, is turned
down. What the budget cannot reach stays as generated and is logged — a
week of three Italian dinners is far better than a lost week.

A dish's cuisine is the recipe's own `cuisine` field, which the drafting
model fills for every dish. A dish without one is not counted either way:
the pass never guesses a cuisine from a name.

Never raises.
"""
from __future__ import annotations

import logging
from collections import Counter

from . import leftovers as _leftovers
from . import meal_variety as _meal_variety

logger = logging.getLogger("home_manager")

# Emily's card, 2026-10-31. One place each.
MIN_CUISINES = 4
MAX_DINNERS_PER_CUISINE = 2

# What a re-picked dish records about itself. No note under the dish: the
# household reads a varied week, not the app explaining it (plain-copy rule).
CUISINE_SPREAD_BECAUSE = "the week had too much of one cuisine"
CUISINE_SPREAD_KEY = "cuisine_spread"

# How many dishes this pass tries at most. The shared budget is the real
# bound; this only keeps a week of protected dishes from looping.
MAX_SPREAD_REPICKS = 6


def stands_down(memory: dict | None, intake: dict | None) -> bool:
    """A household that named cuisines — standing preferences or this
    week's chips — gets what it named, not a spread."""
    prefs = [c for c in ((memory or {}).get("cuisine_preferences") or []) if str(c).strip()]
    chips = [c for c in ((intake or {}).get("cuisines") or []) if str(c).strip()]
    return bool(prefs or chips)


def _norm(cuisine: str | None) -> str:
    from . import typed_requests as _typed_requests
    return _typed_requests._cuisine_norm(cuisine or "").strip()


def _cuisines_by_entry(plan_id: int) -> dict[int, str]:
    from . import typed_requests as _typed_requests
    return {e["id"]: _norm(e.get("cuisine")) for e in _typed_requests._load_entries(plan_id)}


def _dishes(plan_id: int, chains: dict) -> list[dict]:
    """The week's dinner and lunch dishes (meal_variety._week_dishes), each
    with its cuisine: the first night that names one."""
    by_entry = _cuisines_by_entry(plan_id)
    dishes = _meal_variety._week_dishes(plan_id, chains)
    for dish in dishes:
        dish["cuisine"] = next((by_entry.get(n["id"]) for n in dish["nights"] if by_entry.get(n["id"])), "")
    return dishes


def shortfall(dishes: list[dict]) -> tuple[set[str], str | None]:
    """(crowded cuisines, why) for the week as it stands — empty and None
    when it already meets the rule."""
    known = [d for d in dishes if d["cuisine"]]
    dinner_counts = Counter(d["cuisine"] for d in known if d["slot"] == "dinner")
    over = {c for c, n in dinner_counts.items() if n > MAX_DINNERS_PER_CUISINE}
    if over:
        return over, "dinners"
    all_counts = Counter(d["cuisine"] for d in known)
    want = min(MIN_CUISINES, len(known))
    if len(all_counts) < want:
        top = max(all_counts.values())
        return {c for c, n in all_counts.items() if n == top and n > 1}, "spread"
    return set(), None


def enforce_cuisine_spread(plan_id: int, memory: dict | None, intake: dict | None, budget,
                           picker=None, asks: tuple[str | None, ...] = ()) -> dict:
    """See the module docstring. Returns {"repicked", "left", "skipped"}."""
    out = {"repicked": [], "left": [], "skipped": None}
    if stands_down(memory, intake):
        out["skipped"] = "they named cuisines"
        return out
    try:
        chains = _leftovers.plan_leftover_chains(plan_id)
        tried: set[str] = set()
        for _ in range(MAX_SPREAD_REPICKS):
            dishes = _dishes(plan_id, chains)
            crowded, why = shortfall(dishes)
            if not why:
                break
            seen = {d["cuisine"] for d in dishes if d["cuisine"]}
            # Latest first, dinners first when the dinners are the problem.
            candidates = [
                d for d in reversed(dishes)
                if d["cuisine"] in crowded and _meal_variety.repeat_key(d["name"]) not in tried
                and (why != "dinners" or d["slot"] == "dinner")
            ]
            target = None
            for dish in candidates:
                tried.add(_meal_variety.repeat_key(dish["name"]))
                if (dish["protected"] or _meal_variety.theirs_by_hand(dish)
                        or _meal_variety.asked_for_by_name(dish["name"], asks)):
                    out["left"].append(dish["name"])
                    continue
                nights = _meal_variety._replaceable(dish, chains)
                if nights is None:
                    out["left"].append(dish["name"])
                    continue
                target = (dish, nights)
                break
            if target is None:
                break
            dish, nights = target
            # Under "spread" any cuisine already on the week is one too
            # many for the new dish; under "dinners" only the crowded one.
            refuse = set(seen) if why == "spread" else set(crowded)
            others = [d["name"] for d in dishes if d is not dish]

            def reject_pick(candidate, refuse=refuse):
                said = _norm(candidate.get("cuisine"))
                if not said:
                    return "no cuisine given"
                if said in refuse:
                    return f"{said} again"
                return None

            new = _meal_variety._repick_entry(
                plan_id, nights[0], budget,
                avoid=others, because=CUISINE_SPREAD_BECAUSE,
                reject=lambda name, others=others: _meal_variety.same_as_any(name, others),
                derived_key=CUISINE_SPREAD_KEY, picker=picker,
                context_extra={"avoid_cuisines": sorted(refuse)},
                reject_pick=reject_pick, reason_line=_meal_variety.REPEAT_REASON,
                also=nights[1:] or None,
            )
            if new is None:
                out["left"].append(dish["name"])
                if getattr(budget, "left", 1) <= 0:
                    break
                continue
            out["repicked"].append({"dropped": dish["name"], "with": new["meal"], "cuisine": dish["cuisine"]})
            logger.info("Cuisine spread: plan %s %r (%s) -> %r", plan_id, dish["name"], dish["cuisine"], new["meal"])
            chains = _leftovers.plan_leftover_chains(plan_id)
        if out["left"]:
            logger.info("Cuisine spread: plan %s left as generated: %s", plan_id, ", ".join(out["left"]))
    except Exception:
        logger.exception("Cuisine spread failed for plan %s; the week stands as generated", plan_id)
    return out
