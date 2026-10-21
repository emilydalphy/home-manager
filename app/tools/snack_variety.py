"""
A week's snacks have some variety — no one snack on more than three days.

Loop Board, 2026-10-19 (Tester Home, plan 72: 2 adults, 2 snacks a day).
All fourteen snack slots were "Sliced Orange with Pumpkin Seeds" or
"Edamame with Sea Salt", seven days each, and the list bought 14 oranges.
Two things made that week, and only one of them was the model:

  - meal_variety.enforce_snack_dishes folds the week's snacks to
    meal_preferences.snack_dishes_per_week, a column that is NOT NULL
    DEFAULT 2 (2026-09-27, "ASSUMPTION for Emily"). Two dishes and two
    snacks a day is every dish on every day — so for a household that
    never touched the stepper, the app itself folded any variety the
    model did send down to the orange-and-edamame week. The count is
    now read as an answer only once someone sets it
    (snack_dishes_per_week_set); until then it is default_snack_dishes:
    enough dishes that none has to be on more than MAX_SNACK_DAYS days.
  - nothing held the model to a spread. Telling the generator is not
    the same as preventing it (CLAUDE.md), so enforce_snack_variety runs
    after the counts, in code, with no model call.

What it does: a snack on more than its cap of days keeps that many days,
spread across the week, and each other day is re-picked — from the week's
own snacks with room first (nothing new to buy), then from SNACK_POOL, the
household's everyday snack list (everyday_snacks) with real per-person
amounts. A pool
snack is held to the same allergen and taste gate a swap is
(swap_in_place.pick_gate, plus the ingredients a bought item hides —
tahini in hummus), skips a standing dislike, last fortnight's snacks and
anything already on that day, and leans toward the ones whose ingredients
the week already buys, so variety doesn't balloon the list.

What stands it down: a count they set (the cap is then what their number
needs: two dishes at two a day is seven days each, as they said); words
for the week or a standing note that say repeats are fine ("same snack
every day is fine"); and, per dish, a snack they asked for by name or one
carried in on purpose (meal_variety.theirs / asked_for_by_name). A house
with an eating style (keto, a list of allowed foods) gets no pool snack —
the pool cannot be judged against a style — only the week's own reshuffle
(a style that is only "vegetarian"/"vegan" is read by everyday_snacks).
Never raises.
"""
from __future__ import annotations

import json
import logging
import math
import re

from ..db import get_conn, write
from ._shared import household_id
from . import everyday_snacks as _everyday

logger = logging.getLogger("home_manager")

# No snack on more days than this, unless the household asked for it.
MAX_SNACK_DAYS = 3

VARIETY_CONSTRAINT = "snack_variety"


def default_snack_dishes(snacks_per_day: int | None) -> int | None:
    """
    How many different snacks a week reads as, for a household that never
    set the "Snacks" stepper: enough that no snack is on more than
    MAX_SNACK_DAYS of seven days (2 a day -> ceil(14/3) = 5), never fewer
    than one day's snacks, at most 7. None for a house with no snacks.
    """
    per_day = int(snacks_per_day or 0)
    if per_day <= 0:
        return None
    return min(7, max(per_day, math.ceil(per_day * 7 / MAX_SNACK_DAYS)))


# "same snack every day is fine", "the same snacks all week", "snacks can
# repeat", "happy repeating snacks". Narrow on purpose: "snacks every day"
# is how many, not which, and "not the same snack" is the opposite ask.
_SAME_SNACK = re.compile(
    r"\bsame\s+(?:\w+\s+){0,2}snacks?\b"
    r"|\bsnacks?\s+(?:can|may|could|are\s+fine\s+to)\s+(?:be\s+the\s+same|repeat)"
    r"|\brepeat(?:ing|ed)?\s+(?:the\s+|our\s+)?snacks?\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:not|no|never|don'?t|doesn'?t|isn'?t|without)\b|n't\b", re.IGNORECASE)


def asks_for_same_snacks(*texts: str | None) -> bool:
    """Do these words say repeating snacks is what they want?"""
    for text in texts:
        for m in _SAME_SNACK.finditer(text or ""):
            before = (text or "")[max(0, m.start() - 20):m.start()]
            if not _NEGATION.search(before):
                return True
    return False


# The pool is the household's everyday snack list (data/everyday_snacks.json,
# 2026-10-21), as everyday_snacks.eligible filters it for this house —
# allergens the list names, a nut-free lunch (a nut butter becomes its
# nut-free swap), vegetarian/vegan, dislikes. It replaced a twelve-snack
# list written here (2026-10-19). Each pool line is (item, amount PER
# PERSON, unit, category, key) — `key` is the word the week's own food is
# searched for; `contains` is the list's allergens in the words a
# restriction is written in, matched for allergies only (review,
# 2026-10-19: a "milk allergy" house was offered yogurt and cheddar). The
# pool protects itself rather than waiting on the matcher.
# SNACK_POOL is the whole list, unfiltered, in that shape.
SNACK_POOL: list[dict] = [_everyday.as_pool(s) for s in _everyday.load_list()]


def _amount(value: float, serves: int) -> str:
    total = value * max(1, serves)
    return str(int(total)) if float(total).is_integer() else f"{total:g}"


def pool_ingredients(pool: dict, serves: int) -> list[dict]:
    """The pool snack's list for `serves` people: one person's amount each.
    A whole thing (an apple) is written as the TABLE's count, because a
    count on a recipe is the dish's amount and is never multiplied up
    (attendance.count_scale_factor) — so two adults' apple is "2"."""
    out = []
    for item, value, unit, category, _key in pool["lines"]:
        qty = _amount(value, serves)
        out.append({"item": item, "qty": f"{qty} {unit}".strip(), "category": category})
    return out


def _rescale_pool_recipe(recipe_id: int, pool: dict, saved: list[dict], serves: int) -> None:
    """
    A pool recipe saved for last week's table is written again for this
    one — but only while it is still exactly the pool's own list for the
    table it was saved for. A count on a recipe is never multiplied up
    (attendance.count_scale_factor), so "2 apples" saved for two would buy
    two for a table of four. A list anyone has edited is theirs and is
    left alone.
    """
    with write() as conn:
        row = conn.execute("SELECT default_servings FROM recipes WHERE id = ? AND household_id = ?",
                           (recipe_id, household_id())).fetchone()
        old = int(row["default_servings"] or 0) if row else 0
        pairs = lambda lines: [((l.get("item") or "").strip().lower(), (l.get("qty") or "").strip()) for l in lines]
        if row and old != serves and old > 0 and pairs(saved) == pairs(pool_ingredients(pool, old)):
            conn.execute("UPDATE recipes SET ingredients_json = ?, default_servings = ? "
                         "WHERE id = ? AND household_id = ?",
                         (json.dumps(pool_ingredients(pool, serves)), serves, recipe_id, household_id()))


def _week_text(plan_id: int) -> str:
    """Everything the week already buys, lowercased, to search a pool
    snack's keys in: every planned dish's name and saved ingredients, its
    dish note, and the open grocery list."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT COALESCE(r.name, mpe.freeform_meal) AS meal, r.ingredients_json, r.dish_note
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.slot_state = 'planned'
        """,
        (plan_id, household_id()),
    ).fetchall()
    grocery = conn.execute(
        "SELECT item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'in_cart')",
        (household_id(),),
    ).fetchall()
    conn.close()
    parts = []
    for r in rows:
        parts.append(r["meal"] or "")
        parts.append(r["dish_note"] or "")
        try:
            parts.extend((i.get("item") or "") for i in json.loads(r["ingredients_json"] or "[]")
                         if isinstance(i, dict))
        except (TypeError, ValueError):
            pass
    parts.extend(g["item"] or "" for g in grocery)
    return " | ".join(parts).lower()


def _overlap(pool: dict, week_text: str) -> int:
    return sum(1 for line in pool["lines"] if line[4] in week_text)


def _disliked(pool: dict, dislikes: list[str]) -> bool:
    words = " ".join([pool["name"]] + [l[0] for l in pool["lines"]]).lower()
    return any(d and d.strip().lower() in words for d in dislikes or [])


def _spread(n: int, keep: int) -> list[int]:
    """`keep` indices out of n, as evenly spaced as they go (first and last
    kept), so a capped snack sits across the week rather than Mon–Wed."""
    if keep <= 0:
        return []
    if keep == 1:
        return [0]
    return sorted({round(i * (n - 1) / (keep - 1)) for i in range(keep)})


def enforce_snack_variety(plan_id: int, dates: list[str], memory: dict, period_start: str | None = None,
                          asks: tuple[str | None, ...] = ()) -> dict:
    """
    No snack on more than its cap of days (see the module docstring). The
    cap is MAX_SNACK_DAYS, or — when the household set the count — what
    their number needs to cover the week's snack days. Never raises.
    """
    from . import allergen_gate as _allergen_gate
    from . import draft_opener as _draft_opener
    from . import meal_variety as _meal_variety
    from . import plan_quality as _plan_quality
    from . import recipes as _recipes
    from . import swap_in_place as _swap
    from . import weekly_plan as _weekly_plan

    out = {"replaced": [], "left": [], "skipped": None, "cap": None}
    if not dates:
        return out
    if asks_for_same_snacks(*asks, memory.get("notes"), memory.get("typical_week"),
                            memory.get("next_week_notes")):
        out["skipped"] = "they said repeats are fine"
        logger.info("Snack variety not enforced for plan %s: their words say repeats are fine", plan_id)
        return out
    try:
        in_scope = set(dates)
        snacks = [e for e in _meal_variety._load_slot_entries(plan_id, "snack")
                  if e["date"] in in_scope and e["slot_state"] == "planned" and e["meal"]]
        if not snacks:
            return out
        cap = MAX_SNACK_DAYS
        if memory.get("snack_dishes_per_week_set") and memory.get("snack_dishes_per_week"):
            cap = max(cap, math.ceil(len(snacks) / int(memory["snack_dishes_per_week"])))
        out["cap"] = cap

        dishes: dict[str, dict] = {}
        for e in sorted(snacks, key=lambda e: (e["date"], e["id"])):
            key = e["meal"].strip().lower()
            dish = dishes.setdefault(key, {"name": e["meal"].strip(), "entries": [], "days": set(),
                                           "protected": False, "food_groups": None})
            dish["entries"].append(e)
            dish["days"].add(e["date"])
            derived = json.loads(e["derived_from_json"] or "{}") or {}
            if _meal_variety.theirs(derived) or (e["cooked_status"] or "") == "done":
                dish["protected"] = True
            if dish["food_groups"] is None:
                dish["food_groups"] = json.loads(e["food_groups_json"] or "[]") or None
        for dish in dishes.values():
            if not dish["protected"] and _meal_variety.asked_for_by_name(dish["name"], asks):
                dish["protected"] = True

        over = [d for d in dishes.values() if not d["protected"] and len(d["days"]) > cap]
        if not over:
            return out

        day_food = _meal_variety._day_food(plan_id, sorted(in_scope))
        recent = {_meal_variety.repeat_key(n) for n in
                  ((_draft_opener.recent_dish_names(period_start, plan_id) or ()) if period_start else ())}
        use_pool = not _everyday.style_stands_down(memory)
        household_pool = [_everyday.as_pool(v) for v in _everyday.eligible(memory)] if use_pool else []
        week_text = _week_text(plan_id) if use_pool else ""
        avoid_hard = _allergen_gate.hard_avoidances()
        dislikes = memory.get("dislikes") or []
        pool_refused: set[str] = set()

        surplus: list[dict] = []
        for dish in sorted(over, key=lambda d: (-len(d["days"]), d["name"])):
            entries = dish["entries"]
            keep = set(_spread(len(entries), cap))
            surplus.extend(e for i, e in enumerate(entries) if i not in keep)
            dish["days"] = {e["date"] for i, e in enumerate(entries) if i in keep}

        def _echoes(name: str, date: str) -> bool:
            # The same word as something else that day ("Carrot Sticks" on a
            # carrot-soup day) is plan_quality's snack_echoes_a_meal, and its
            # repair would copy an over-cap snack back in: never pick one.
            return any(_plan_quality.shared_food(name, other) for other in day_food.get(date, {}).values())

        def _week_pick(date: str) -> dict | None:
            on_day = set(day_food.get(date, {}))
            fits = [d for k, d in dishes.items() if k not in on_day and len(d["days"]) < cap
                    and not _echoes(d["name"], date)]
            return min(fits, key=lambda d: (len(d["days"]), d["name"])) if fits else None

        def _pool_pick(date: str) -> dict | None:
            if not use_pool:
                return None
            on_day = set(day_food.get(date, {}))
            ranked = []
            for i, pool in enumerate(household_pool):
                key = pool["name"].lower()
                if key in dishes or key in on_day or key in pool_refused or _echoes(pool["name"], date):
                    continue
                if _meal_variety.repeat_key(pool["name"]) in recent or _disliked(pool, dislikes):
                    pool_refused.add(key)
                    continue
                ranked.append((-_overlap(pool, week_text), i, pool))
            for _score, _i, pool in sorted(ranked, key=lambda r: (r[0], r[1])):
                serves = _swap._table_for(date, "snack")["serves"] or 1
                ingredients = pool_ingredients(pool, serves)
                hidden = [{"item": w, "qty": ""} for w in pool.get("contains") or ()]
                candidate = {"meal_name": pool["name"], "ingredients": ingredients,
                             "food_groups": pool["groups"]}
                # A recipe of that name already in their box is what the
                # week will actually plan (and buy), so ITS list is checked
                # too — a household's own "Carrot Sticks with Hummus" may
                # be made with peanut butter.
                existing = _recipes.existing_recipe_named(pool["name"])
                saved = _recipes.saved_ingredients(pool["name"]) if existing else []
                if (_allergen_gate.hard_clashes(pool["name"], ingredients=ingredients + hidden + saved,
                                                avoidances=avoid_hard)
                        or _swap.pick_gate(candidate, {"date": date, "slot": "snack"})):
                    pool_refused.add(pool["name"].lower())
                    continue
                if not existing:
                    _recipes.add_recipe(
                        name=pool["name"], ingredients=ingredients, food_groups=pool["groups"],
                        default_servings=serves, prep_time_minutes=5,
                    )
                else:
                    _rescale_pool_recipe(existing["id"], pool, saved, serves)
                dish = {"name": pool["name"], "entries": [], "days": set(), "protected": False,
                        "food_groups": pool["groups"] or None}
                dishes[pool["name"].lower()] = dish
                return dish
            return None

        for e in sorted(surplus, key=lambda e: (e["date"], e["id"])):
            old_key = e["meal"].strip().lower()
            day_food.setdefault(e["date"], {}).pop(old_key, None)
            pick = _week_pick(e["date"]) or _pool_pick(e["date"])
            if pick is None:
                # Better the repeat than an empty snack or a guess.
                out["left"].append({"date": e["date"], "meal": e["meal"]})
                dishes[old_key]["days"].add(e["date"])
                day_food[e["date"]][old_key] = e["meal"]
                continue
            _weekly_plan._replace_slot_entries(
                plan_id, [e["id"]], e["date"], "snack", pick["name"],
                food_groups=pick["food_groups"], reasoning="",
                derived_from={"constraint": VARIETY_CONSTRAINT, "replaced": e["meal"]},
            )
            pick["days"].add(e["date"])
            day_food[e["date"]][pick["name"].lower()] = pick["name"]
            out["replaced"].append({"date": e["date"], "dropped": e["meal"], "with": pick["name"]})
        if out["replaced"] or out["left"]:
            logger.info("Plan %s snack variety (cap %d days): re-picked %d%s", plan_id, cap,
                        len(out["replaced"]),
                        f", {len(out['left'])} left as they were" if out["left"] else "")
    except Exception:
        logger.exception("Snack variety failed for plan %s; the snacks stand as planned", plan_id)
    return out
