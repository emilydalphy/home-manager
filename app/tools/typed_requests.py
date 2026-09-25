"""
A dish is what its name says, and a typed ingredient reaches a dish.

Emily, 2026-09-21, after a draft: she had typed "I have some corn so
incorporate that into a meal" and, earlier, that she wanted a Korean
chicken pancake. The draft came back with "Korean Chicken Pancake with
Cucumber Salad" — "it didn't contain any corn, it was a chicken pancake
with cucumber salad on the side." Two faults, two passes here:

  * `use_requested_ingredients` — an ingredient the household typed
    (week_intake.freeform_ingredient_requests: "I have some corn", "use
    the lamb in the freezer") must be IN at least one dish. The drafting
    prompt is told so, and this pass makes it true afterwards: when no
    dish on the plan carries it, one suitable slot is re-picked quietly
    through the swap's own picker with the ingredient on `must_contain`
    — the allergen and Surprise-me re-pick's shape, the same shared
    budget — and a pick that still hasn't got it is thrown away. What
    happened is written into the plan's request report either way: the
    ingredient joins `honoured` (with the slot citing the request, so the
    opener can say where it went) or `unmet` (so the opener says "I
    couldn't fit the corn in this week"). Never silent.

  * `trim_requested_dish_names` — a dish the household asked for BY NAME
    keeps that name. The full-plate rule tells the model to plan a side
    into the meal_name ("... with Cucumber Salad"), which is right for a
    dish it invented and wrong for one they named: the pancake is the
    pancake. The clause is taken off the name before the recipe row is
    written, and each thing it named is attached to the entry as a SIDE
    (plates.add_component — the same column and card chip as a side the
    plate pass adds), never folded back into the name. Only when the head
    is a phrase they typed, the clause is a side-shaped thing, and the
    name isn't already one of their saved recipes — a dish they did NOT
    ask for keeps whatever name the model gave it.

Both passes swallow their own failures: a plan that generated correctly
is never lost to either of them.
"""
from __future__ import annotations

import json
import logging
import re

from ..db import get_conn
from ._shared import household_id
from . import allergen_gate as _allergen_gate
from . import leftovers as _leftovers
from . import meal_variety as _meal_variety
from . import plan_quality as _plan_quality
from . import plates as _plates

logger = logging.getLogger("home_manager")

# Which slots a typed ingredient can land in, in the order a slot is tried.
_REPICK_SLOTS = ("dinner", "lunch")

# Why the slot was re-picked, for the row's derived_from and the picker's
# `replacing_because`.
MUST_USE_BECAUSE = "you asked for the {ingredient} this week and nothing used it"


# ---------- does a dish carry it? ----------

def _stems(text: str) -> set[str]:
    return {_plan_quality._stem(w) for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) > 2}


def _ingredient_stems(ingredient: str) -> list[str]:
    """The content of an ingredient request, stemmed: "chicken breast" is
    two words and both have to be there; "corn" is one."""
    return [s for s in (_plan_quality._stem(w) for w in re.findall(r"[a-z]+", ingredient.lower())) if len(s) > 2]


def dish_carries(words: str, ingredient: str) -> bool:
    """Whether the dish's own words (its name, dish_note, protein, the
    ingredient list when it has one, the stock it was chosen to use up)
    carry every word of the ingredient — with plan_quality's alias groups,
    so "prawns" answers for "shrimp"."""
    pool = _stems(words)
    wanted = _ingredient_stems(ingredient)
    if not wanted:
        return False
    for stem in wanted:
        if stem in pool or (_plan_quality._same_food(stem) & pool):
            continue
        return False
    return True


def _entry_words(entry: dict) -> str:
    """Everything an entry's dish says about itself, as one string."""
    parts = [entry.get("meal") or "", entry.get("dish_note") or "", entry.get("main_protein") or ""]
    try:
        ingredients = json.loads(entry.get("ingredients_json") or "[]") or []
    except (TypeError, ValueError):
        ingredients = []
    parts += [str(i.get("item") or "") for i in ingredients if isinstance(i, dict)]
    try:
        derived = json.loads(entry.get("derived_from_json") or "{}") or {}
    except (TypeError, ValueError):
        derived = {}
    parts += [str(x) for x in (derived.get("inventory") or [])]
    return " ".join(parts)


def _load_entries(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.freeform_meal,
               r.dish_note, r.main_protein, r.ingredients_json
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (plan_id, household_id()),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _pick_carries(candidate: dict, ingredient: str) -> str | None:
    """reject_pick for _repick_entry: the pick's name, ingredients and
    steps have to carry the ingredient."""
    words = " ".join(
        [candidate.get("meal_name") or ""]
        + [str(i.get("item") or "") for i in (candidate.get("ingredients") or []) if isinstance(i, dict)]
        + [str(s) for s in (candidate.get("instructions") or [])]
    )
    return None if dish_carries(words, ingredient) else f"no {ingredient} in it"


def _slot_to_repick(entries: list[dict], chains: dict) -> dict | None:
    """
    The one slot to re-pick around the ingredient: a planned dinner (else
    lunch) that nobody asked for by name (no derived_from.freeform), not
    yet cooked, not one end of a leftovers chain, and — so the week's
    distinct-dish count is left exactly as it was — a dish that appears
    once, when there is one. The earliest such night.
    """
    for once_only in (True, False):
        for slot in _REPICK_SLOTS:
            planned = [
                e for e in entries
                if e["slot"] == slot and e["slot_state"] == "planned" and e["meal"]
                and not _meal_variety._LEFTOVER_LINE.search(e["freeform_meal"] or "")
            ]
            counts: dict[str, int] = {}
            for e in planned:
                counts[e["meal"].strip().lower()] = counts.get(e["meal"].strip().lower(), 0) + 1
            for e in planned:
                try:
                    derived = json.loads(e["derived_from_json"] or "{}") or {}
                except (TypeError, ValueError):
                    derived = {}
                # Their own words, or a meal they brought over from last
                # week (meal_variety.theirs): never the slot re-picked.
                if _meal_variety.theirs(derived) or (e["cooked_status"] or "") == "done":
                    continue
                if e["id"] in chains["leftovers"] or e["id"] in chains["sources"]:
                    continue
                if once_only and counts[e["meal"].strip().lower()] != 1:
                    continue
                return e
    return None


def _report_lists(report: dict) -> tuple[list[dict], list[dict]]:
    honoured = report.setdefault("honoured_requests", [])
    unmet = report.setdefault("unmet_requests", [])
    if not isinstance(honoured, list):
        honoured = report["honoured_requests"] = []
    if not isinstance(unmet, list):
        unmet = report["unmet_requests"] = []
    return honoured, unmet


def _about(entry: dict, request: dict) -> bool:
    """Is this report line about this ingredient request? The parser's
    sentence, or a span quoting it, or the ingredient's own words."""
    words = str(entry.get("words") or "").strip().lower()
    if not words:
        return False
    if entry.get("ingredient") == request["ingredient"]:
        return True
    if words == request["words"].strip().lower():
        return True
    return dish_carries(words, request["ingredient"]) and len(_stems(words) & _stems(request["words"])) >= 2


def use_requested_ingredients(plan_id: int, requests: list[dict], report: dict, budget=None, picker=None) -> dict:
    """
    For every typed ingredient (week_intake.freeform_ingredient_requests),
    make sure one dish on the plan carries it, and say so in `report`
    (the model's own honoured_requests / unmet_requests, amended in
    place — what weekly_plan.record_plan_requests stores). Returns
    {"used": [...], "repicked": [...], "unmet": [...]} for the log and
    for tests. Never raises.
    """
    out = {"used": [], "repicked": [], "unmet": []}
    if not requests:
        return out
    budget = budget or _allergen_gate.CallBudget()
    try:
        honoured, unmet = _report_lists(report)
        for request in requests:
            ingredient = request["ingredient"]
            entries = _load_entries(plan_id)
            planned = [e for e in entries if e["slot_state"] == "planned" and e["meal"]]
            carrier = next((e for e in planned if dish_carries(_entry_words(e), ingredient)), None)
            if carrier is None:
                chains = _leftovers.plan_leftover_chains(plan_id)
                target = _slot_to_repick(entries, chains)
                replaced = None
                if target is not None:
                    replaced = _meal_variety._repick_entry(
                        plan_id, target, budget,
                        avoid=[], because=MUST_USE_BECAUSE.format(ingredient=ingredient),
                        reject=lambda name: False,
                        derived_key="must_use_repick", picker=picker,
                        context_extra={"must_contain": [ingredient]},
                        reject_pick=lambda candidate, _i=ingredient: _pick_carries(candidate, _i),
                        derived_extra={"freeform": request["words"], "must_use": [ingredient]},
                    )
                if replaced is None:
                    # Said, never silent: the opener reads this line.
                    unmet[:] = [u for u in unmet if not _about(u, request)]
                    honoured[:] = [h for h in honoured if not _about(h, request)]
                    unmet.append({"words": request["words"], "reason": f"nothing fit the {ingredient} in",
                                  "ingredient": ingredient})
                    out["unmet"].append(ingredient)
                    logger.info("Plan %s: nothing used the %s the household typed, and no re-pick landed one",
                                plan_id, ingredient)
                    continue
                out["repicked"].append(ingredient)
                logger.info("Plan %s: %s %s re-picked as %r so the %s they typed is used",
                            plan_id, target["date"], target["slot"],
                            replaced.get("meal") or replaced.get("meal_name"), ingredient)
            else:
                out["used"].append(ingredient)
                _note_must_use(carrier, request)
            # Honoured — once, and off the unmet list if the model had
            # given up on it.
            unmet[:] = [u for u in unmet if not _about(u, request)]
            if not any(_about(h, request) for h in honoured):
                honoured.append({"words": request["words"], "label": f"the {ingredient}", "ingredient": ingredient})
    except Exception:
        logger.exception("Typed-ingredient pass failed for plan %s; the week stands as generated", plan_id)
    return out


def _note_must_use(entry: dict, request: dict) -> None:
    """The dish the model chose for the ingredient cites the request (so
    the opener can say which night) and carries `must_use`, which the
    recipe pass reads when it writes the dish up on approval."""
    try:
        derived = json.loads(entry.get("derived_from_json") or "{}") or {}
    except (TypeError, ValueError):
        derived = {}
    must = [m for m in (derived.get("must_use") or []) if isinstance(m, str)]
    if request["ingredient"] not in must:
        must.append(request["ingredient"])
    derived["must_use"] = must
    if not (derived.get("freeform") or "").strip():
        derived["freeform"] = request["words"]
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(derived), entry["id"], household_id()),
    )
    conn.commit()
    conn.close()


def plan_must_use(plan_id: int, recipe_id: int) -> list[str]:
    """The ingredients the entries planned under this recipe were chosen
    to carry (derived_from.must_use), for the recipe pass's spec."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE weekly_plan_id = ? AND recipe_id = ? AND household_id = ?",
        (plan_id, recipe_id, household_id()),
    ).fetchall()
    conn.close()
    out: list[str] = []
    for r in rows:
        try:
            derived = json.loads(r["derived_from_json"] or "{}") or {}
        except (TypeError, ValueError):
            continue
        for m in derived.get("must_use") or []:
            if isinstance(m, str) and m and m not in out:
                out.append(m)
    return out


# ---------- a dish requested by name keeps its name ----------

# What a trailing "with ..." clause has to name for the pass to be sure it
# is a SIDE and not part of the dish: "with Cucumber Salad" comes off,
# "with Lemon and Herbs" stays, because lemon and herbs are the dish.
_SIDE_WORDS = {
    "salad", "slaw", "coleslaw", "rice", "pilaf", "potato", "potatoes", "fries", "chips", "wedges", "mash",
    "bread", "naan", "pita", "roti", "tortilla", "tortillas", "flatbread", "baguette", "toast", "buns",
    "noodles", "couscous", "quinoa", "polenta", "grits", "farro", "bulgur", "orzo", "pasta", "greens",
    "vegetables", "veg", "veggies", "beans", "corn", "broccoli", "broccolini", "asparagus", "carrots", "peas",
    "spinach", "kale", "cabbage", "cauliflower", "zucchini", "squash", "sprouts", "edamame", "kimchi",
    "pickles", "salsa", "chutney", "raita", "tzatziki", "hummus", "guacamole", "dumplings", "plantains",
    "cucumbers", "tomatoes", "beets", "roasted", "steamed", "sautéed", "sauteed", "grilled", "charred",
}

_WORD_RE = re.compile(r"[a-z][a-z\-']*")


def _norm(text: str) -> str:
    return " ".join(w.rstrip("s") if len(w) > 3 else w for w in _WORD_RE.findall((text or "").lower()))


def _typed(norm_phrase: str, norm_text: str):
    """The matches of a normalised phrase in the normalised text, as whole
    words — "rice" is not in "price"."""
    return re.finditer(rf"(?<![a-z\-']){re.escape(norm_phrase)}(?![a-z\-'])", norm_text)


def _phrase_typed(phrase: str, asks: str) -> bool:
    """Whether the household typed this phrase (two words or more), as a
    phrase, and not as the start of a longer one — "chicken" inside
    "chicken breast" is not the pancake case, it is an ingredient."""
    head = _norm(phrase)
    if len(head.split()) < 2:
        return False
    text = _norm(asks)
    for m in _typed(head, text):
        after = text[m.end():].strip().split(" ")[0] if text[m.end():].strip() else ""
        if after and _plan_quality._stem(after) in _plan_quality._known_food_words():
            continue
        return True
    return False


def _is_side(part: str) -> bool:
    words = set(_WORD_RE.findall(part.lower()))
    return bool(words & _SIDE_WORDS)


def requested_dish_name(name: str, asks: str, taken=()) -> tuple[str, list[str]]:
    """
    (the name the household asked for, the sides that were folded into
    it) — or (name, []) when there is nothing to take off: the head isn't a
    phrase they typed, the clause is part of the dish rather than a side,
    they typed the clause too ("chicken with rice and beans"), or the name
    is already one of their saved recipes (it identifies a row).
    """
    name = (name or "").strip()
    if not name or not asks or name.lower() in {str(t).strip().lower() for t in (taken or ())}:
        return name, []
    match = _plan_quality._TITLE_WITH_CLAUSE.match(name)
    if not match:
        return name, []
    head = match.group("head").strip()
    if not _phrase_typed(head, asks) or not _plan_quality._head_says_something(head):
        return name, []
    typed = _norm(asks)
    sides, kept = [], []
    for part in _plan_quality._split_title_clause(match.group("clause")):
        if _norm(part) and any(True for _ in _typed(_norm(part), typed)):
            kept.append(part)          # they asked for it with the dish
        elif _is_side(part):
            sides.append(part)
        else:
            kept.append(part)          # part of the dish, as far as we can tell
    if not sides:
        return name, []
    honest = f"{head} with {' and '.join(kept)}" if kept else head
    return honest, sides


def trim_requested_dish_names(items: list[dict], asks: str, taken=()) -> list[dict]:
    """
    Over the model's `days` BEFORE they are written: a new dish whose
    name is a requested dish plus a side loses the side from its name —
    every copy of it across the week (a breakfast repeated three mornings
    is one dish) — and the sides come back as
    [{"date", "slot", "meal_name", "sides": [...]}] for attach_named_sides
    once the entries exist. A dish already saved under the full name is
    left alone (see requested_dish_name).
    """
    moved: list[dict] = []
    if not asks:
        return moved
    held = {str(t).strip().lower() for t in (taken or ())}
    renamed: dict[str, tuple[str, list[str]]] = {}
    for item in items:
        name = (item.get("meal_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in renamed:
            if not item.get("is_new_recipe") or key in held:
                continue
            honest, sides = requested_dish_name(name, asks, taken=held)
            if honest == name:
                continue
            if honest.lower() in held:
                logger.info("Not trimming %r to %r: that is already one of the household's recipes", name, honest)
                continue
            renamed[key] = (honest, sides)
            held.add(honest.lower())
            logger.info("Generation named the requested dish %r; saving it as %r with %s on the side",
                        name, honest, ", ".join(sides))
        honest, sides = renamed[key]
        item["meal_name"] = honest
        moved.append({"date": item.get("date"), "slot": item.get("slot"), "meal_name": honest, "sides": list(sides)})
    return moved


def attach_named_sides(plan_id: int, moved: list[dict], context: dict | None = None, side_generator=None) -> int:
    """
    Put each side a trimmed name carried onto its entry, as a side —
    plates.add_component, the same call the meal screen's "Add something"
    makes: one small model call writes it (amounts, a step), and if that
    can't, the side goes on as named so nothing they were shown
    disappears. Returns how many were attached. Never raises.
    """
    attached = 0
    if not moved:
        return attached
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT mpe.id, mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?
              AND mpe.slot_state = 'planned' AND mpe.component_category IS NULL
            """,
            (plan_id, household_id()),
        ).fetchall()
        conn.close()
        by_slot = {(r["date"], r["slot"]): (r["id"], (r["meal"] or "").strip().lower()) for r in rows}
        for m in moved:
            entry_id, meal = by_slot.get((m.get("date"), m.get("slot")), (None, ""))
            # Only onto the dish the side came off: a slot re-picked
            # around an allergen in between holds a different dinner.
            if entry_id is None or meal != (m.get("meal_name") or "").strip().lower():
                continue
            for side in m.get("sides") or []:
                try:
                    result = _plates.add_component(
                        entry_id, text=side, context=context or {}, side_generator=side_generator,
                        weekly_plan_id=plan_id,
                    )
                    if result.get("status") == "added":
                        attached += 1
                except Exception:
                    logger.exception("Attaching %r as a side of %s %s failed", side, m.get("date"), m.get("slot"))
    except Exception:
        logger.exception("Attaching named sides failed for plan %s; the week stands as generated", plan_id)
    return attached
