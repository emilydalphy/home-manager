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
               COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.freeform_meal, mpe.reasoning,
               r.dish_note, r.main_protein, r.ingredients_json, r.cuisine
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


# ---------- a dish asked for today, tonight or tomorrow ----------
#
# Emily, 2026-09-27: "I want to make a Japanese curry heavy on veggies
# today … and have leftovers for it", typed at 3:53pm on the Sunday the
# week began. The model put the curry on Sunday lunch — gone by then — and
# left Sunday dinner open. week_intake.freeform_day_requests pins the
# sentence to one exact meal (Sunday dinner); place_day_requests puts the
# dish there, whatever the model did; chain_requested_leftovers makes
# "and have leftovers" a real chain off that cook. The draft says so in one
# line only when Pomona had to MOVE the dish (report["moved_requests"],
# read by draft_opener) — a dish the model already put where it was asked
# needs no sentence of its own.

# On a meal their words named whose time had already gone by when the
# week was drafted ("tonight" at 10pm): planned anyway, and the draft says
# so (draft_flags.flags_for_late_requests).
LATE_KEY = "asked_late"

# Words a sentence asking for a dish carries that are not the dish.
_DAY_REQUEST_FILLER = {
    "want", "make", "making", "cook", "cooking", "like", "love", "have", "use", "used", "stuff", "fridge",
    "freezer", "heavy", "light", "today", "tonight", "tomorrow", "leftover", "leftovers", "some", "something",
    "dish", "meal", "please", "also", "need", "suggest", "really", "just", "thing", "things", "lot", "lots",
}


def _request_stems(words: str) -> set[str]:
    stems = {_plan_quality._stem(w) for w in re.findall(r"[a-z]+", (words or "").lower())
             if len(w) > 2 and w not in _DAY_REQUEST_FILLER}
    return {s for s in stems if s not in _DAY_REQUEST_FILLER}


def _matches_request(entry: dict, request: dict) -> int:
    """How strongly a planned entry is the dish this sentence asked for: the
    model's own citation (derived_from.freeform quoting the sentence), or
    at least two of the dish's name words in the sentence. 0 when neither."""
    from . import draft_opener as _draft_opener

    try:
        derived = json.loads(entry.get("derived_from_json") or "{}") or {}
    except (TypeError, ValueError):
        derived = {}
    shared = len(_plan_quality._name_stems(entry.get("meal")) & _request_stems(request["words"]))
    span = str(derived.get("freeform") or "").strip()
    cited = bool(span) and _draft_opener._cited(request["words"], span)
    if shared >= 2:
        return shared + (1 if cited else 0)
    return 1 if cited and shared >= 1 else 0


def _when_words(meal_date: str, slot: str) -> str:
    from datetime import date as _date

    return f"{_date.fromisoformat(meal_date).strftime('%A')} {slot}"


def place_day_requests(plan_id: int, requests: list[dict], report: dict | None = None) -> dict:
    """
    Put each dish asked for today, tonight or tomorrow on the exact meal
    week_intake.freeform_day_requests resolved it to. The dish is found on
    the plan by the model's own citation or its name; when it is on another
    meal it MOVES — traded with what the target meal held when both are
    the same kind of meal, otherwise the meal it leaves is cleared for the
    gap passes to fill. Not found anywhere: nothing is invented here (the
    model's report says whether the request was met). Runs FIRST in
    _finish_week_slots, before today's past meals are emptied, so a dish
    the model put on a meal that is already gone can still be rescued.

    Every move is recorded in `report["moved_requests"]` for the draft's
    one line. Returns {"placed": [...], "moved": [...], "missing": [...]}.
    Never raises.
    """
    from . import weekly_plan as _weekly_plan

    out = {"placed": [], "moved": [], "missing": []}
    if not requests:
        return out
    try:
        for request in requests:
            target = (request["date"], request["slot"])
            entries = [e for e in _load_entries(plan_id)
                       if e["slot"] in ("lunch", "dinner", "breakfast") and e["slot_state"] == "planned" and e["meal"]]
            scored = [(e, _matches_request(e, request)) for e in entries]
            scored = [(e, s) for e, s in scored if s > 0]
            if not scored:
                out["missing"].append(request["words"])
                continue
            at_target = [e for e, _s in scored if (e["date"], e["slot"]) == target]
            if at_target:
                _cite(at_target[0], request)
                out["placed"].append({"date": target[0], "slot": target[1], "meal": at_target[0]["meal"]})
                continue
            # The strongest match; the earliest of equals.
            dish, _score = min(scored, key=lambda es: (-es[1], _ord(es[0])))
            try:
                dish_derived = json.loads(dish.get("derived_from_json") or "{}") or {}
            except (TypeError, ValueError):
                dish_derived = {}
            dish_derived.pop("links_to", None)
            dish_derived["freeform"] = str(dish_derived.get("freeform") or "").strip() or request["words"]
            dish_derived["moved_for"] = {"said": request["said"], "from": f"{dish['date']}:{dish['slot']}"}
            if request.get("late"):
                dish_derived[LATE_KEY] = True
            here = [e for e in _load_entries(plan_id) if (e["date"], e["slot"]) == target]
            displaced = next((e for e in here if e["slot_state"] == "planned" and e["meal"]), None)
            groups = _food_groups_of(dish)
            displaced_groups = _food_groups_of(displaced) if displaced is not None else None
            _weekly_plan._replace_slot_entries(
                plan_id, [e["id"] for e in here], target[0], target[1], dish["meal"],
                food_groups=groups, reasoning=dish.get("reasoning") or "", derived_from=dish_derived,
            )
            if displaced is not None and displaced["slot"] == dish["slot"]:
                try:
                    back = json.loads(displaced.get("derived_from_json") or "{}") or {}
                except (TypeError, ValueError):
                    back = {}
                back.pop("links_to", None)
                _weekly_plan._replace_slot_entries(
                    plan_id, [dish["id"]], dish["date"], dish["slot"], displaced["meal"],
                    food_groups=displaced_groups, reasoning=displaced.get("reasoning") or "",
                    derived_from=back,
                )
            else:
                _weekly_plan.clear_plan_slot(plan_id, dish["date"], dish["slot"])
            line = f"I moved {dish['meal']} to {_when_words(*target)}, as you asked."
            out["moved"].append({"words": request["words"], "from": f"{dish['date']}:{dish['slot']}",
                                 "to": f"{target[0]}:{target[1]}", "meal": dish["meal"], "line": line})
            if report is not None:
                # A LATE move says both things in the one flag line
                # (draft_flags.late_text, "I moved X to Sunday dinner, as
                # you asked — its usual time had already gone by."), so it
                # is not said again by the opener. Its words still ride
                # along, with no line, so line one does not repeat them.
                report.setdefault("moved_requests", []).append(
                    {"words": request["words"], "line": "" if request.get("late") else line})
    except Exception:
        logger.exception("Placing today/tonight/tomorrow requests failed for plan %s; the week stands", plan_id)
    if out["moved"] or out["missing"]:
        logger.info("Plan %s day requests: %s", plan_id, out)
    return out


def _ord(entry: dict) -> tuple:
    return (entry["date"], ("breakfast", "lunch", "dinner").index(entry["slot"]))


def _food_groups_of(entry: dict) -> list[str] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT food_groups_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry["id"], household_id()),
    ).fetchone()
    conn.close()
    try:
        return json.loads((row["food_groups_json"] if row else None) or "[]") or None
    except (TypeError, ValueError):
        return None


def _cite(entry: dict, request: dict) -> None:
    """The dish was already where it was asked for: make sure it carries
    the request's words, so every repair pass treats it as theirs."""
    try:
        derived = json.loads(entry.get("derived_from_json") or "{}") or {}
    except (TypeError, ValueError):
        derived = {}
    late = bool(request.get("late")) and not derived.get(LATE_KEY)
    if str(derived.get("freeform") or "").strip() and not late:
        return
    if not str(derived.get("freeform") or "").strip():
        derived["freeform"] = request["words"]
    if late:
        derived[LATE_KEY] = True
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(derived), entry["id"], household_id()),
    )
    conn.commit()
    conn.close()


def requested_leftover_cooks(requests: list[dict]) -> list[tuple[str, str]]:
    """The (date, slot) of every dish asked to "have leftovers" — the cooks
    the Leftovers-night pass should reach for first
    (dinner_gaps.apply_leftovers_nights' `prefer`)."""
    return [(r["date"], r["slot"]) for r in requests or [] if r.get("leftovers")]


def chain_requested_leftovers(plan_id: int, requests: list[dict], intake: dict | None = None) -> dict:
    """
    "…and have leftovers for it" becomes a real chain. When the requested
    cook already feeds another meal (the Leftovers night reached for it, or
    a weekday lunch the household said is leftovers), there is nothing to
    do. Otherwise the next day's lunch reheats it — unless the household
    said that lunch is cooked or prepped, it is theirs, or it would put the
    dish on a third meal in a row. Nothing found: logged, and the cook
    stands as a single meal. Never raises.
    """
    from . import weekly_plan as _weekly_plan
    from . import weekday_lunches as _weekday_lunches
    from datetime import date as _date, timedelta as _td

    out = {"chained": [], "already": [], "left": []}
    kinds = _weekday_lunches.kinds_by_date(intake)
    for request in requests or []:
        if not request.get("leftovers"):
            continue
        try:
            entries = _load_entries(plan_id)
            cook = next((e for e in entries if (e["date"], e["slot"]) == (request["date"], request["slot"])
                         and e["slot_state"] == "planned" and e["meal"]), None)
            if cook is None or cook["slot"] not in ("lunch", "dinner"):
                out["left"].append(request["words"])
                continue
            chains = _leftovers.plan_leftover_chains(plan_id)
            if cook["id"] in chains["sources"]:
                out["already"].append(request["words"])
                continue
            lunch_date = (_date.fromisoformat(cook["date"]) + _td(days=1)).isoformat()
            lunch = next((e for e in entries if e["date"] == lunch_date and e["slot"] == "lunch"
                          and e["slot_state"] == "planned" and e["meal"]), None)
            keys = _leftovers.run_keys(plan_id)
            ok = (
                lunch is not None
                and kinds.get(lunch_date) not in ("cooked", "prepped")
                and lunch["id"] not in chains["sources"] and lunch["id"] not in chains["leftovers"]
                and not _meal_variety.theirs(json.loads(lunch.get("derived_from_json") or "{}") or {})
                and not _leftovers.too_many_in_a_row(keys, lunch_date, "lunch", cook["meal"])
            )
            if not ok:
                out["left"].append(request["words"])
                continue
            _weekly_plan._replace_slot_entries(
                plan_id, [lunch["id"]], lunch_date, "lunch", cook["meal"],
                food_groups=_food_groups_of(cook), reasoning="",
                derived_from={"links_to": f"entry_id:{cook['id']}", "freeform": request["words"],
                              "replaced": lunch["meal"]},
            )
            _meal_variety._write_cook_sides(plan_id, {cook["id"]: [f"{lunch_date}:lunch"]}, [], {"batched": []})
            out["chained"].append({"cook": f"{cook['date']}:{cook['slot']}", "lunch": lunch_date})
        except Exception:
            logger.exception("Chaining requested leftovers failed for plan %s", plan_id)
    if out["chained"] or out["left"]:
        logger.info("Plan %s requested leftovers: %s", plan_id, out)
    return out


# ---------- a cuisine chip they picked is on the week ----------
#
# Emily, 2026-09-27: she tapped Burgers on the week's cuisine chips, the
# draft had no burger, and a Greek chicken carried "Burgers, as asked".
# The chips reach the prompt (intake.cuisines) and nothing checked the
# answer. This is use_requested_ingredients' shape for a chip: each picked
# cuisine is matched by at least one lunch or dinner of that cuisine, else
# ONE fitting slot is re-picked with it on `must_be_cuisine` (the swap
# picker's gates: allergies, taste, the table that's home, and that slot's
# own time cap — cap_gate), else the report gets an unmet line the opener
# says plainly ("No burgers fit this week.").

# Why the slot was re-picked, for the row's derived_from and the picker's
# `replacing_because`.
CUISINE_BECAUSE = "you picked {cuisine} this week and nothing on the week was {cuisine}"


# Words only: a hyphen splits ("Thai-style" is Thai, "stir-fry" and
# "stir fry" read alike).
_CUISINE_WORD_RE = re.compile(r"[a-z][a-z']*")

# Two-word dishes kept as one word after splitting, so a chip for one of
# their halves doesn't match them ("Fries" is not a stir-fry).
_CUISINE_COMPOUNDS = {"stir fry": "stirfry"}


def _singular(word: str) -> str:
    """One word's matching form, the same for singular and plural:
    curries/curry, sandwiches/sandwich, smoothies/smoothie,
    quiches/quiche, potatoes/potato, burgers/burger. Short words and -ss
    words are left alone (Swiss, BBQ). For matching only — never shown."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith(("ches", "shes", "xes", "zes", "sses", "oes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    # smoothie and smoothies (-> smoothy) meet; quiche and quiches (-> quich) meet.
    if len(word) > 4 and word.endswith("ie"):
        word = word[:-2] + "y"
    if len(word) > 4 and word.endswith("che"):
        word = word[:-1]
    return word


def _cuisine_norm(text: str) -> str:
    said = " ".join(_singular(w) for w in _CUISINE_WORD_RE.findall((text or "").lower()))
    for pair, joined in _CUISINE_COMPOUNDS.items():
        said = re.sub(rf"\b{pair}\b", joined, said)
    return said


# A chip that names a family of cuisines (Emily's chips are free text; a
# household that taps "Asian" means a Thai curry counts). Each chip, as
# _cuisine_norm reads it, answers to itself and to every name here — the
# recipe's `cuisine` field or the dish's name. Kept small and plain; a
# chip not listed answers only to its own words.
CUISINE_FAMILIES = {
    "asian": ["thai", "chinese", "japanese", "korean", "vietnamese", "indian", "malaysian",
              "indonesian", "filipino", "taiwanese", "szechuan", "sichuan", "cantonese", "nepalese",
              "pakistani", "sri lankan", "singaporean", "burmese"],
    "mediterranean": ["greek", "italian", "lebanese", "turkish", "spanish", "moroccan", "israeli",
                      "middle eastern", "provencal", "cypriot", "tunisian"],
    "middle eastern": ["lebanese", "turkish", "persian", "israeli", "syrian", "iraqi", "palestinian",
                       "jordanian", "egyptian", "arab"],
    "tex-mex": ["mexican"],
    "bbq": ["barbecue", "barbeque", "bbq"],
    "barbecue": ["bbq", "barbeque", "barbecue"],
    "latin american": ["mexican", "brazilian", "peruvian", "argentinian", "cuban", "colombian"],
    "caribbean": ["jamaican", "cuban", "trinidadian", "haitian"],
    "east asian": ["chinese", "japanese", "korean", "taiwanese"],
    "south asian": ["indian", "pakistani", "nepalese", "sri lankan", "bangladeshi"],
    "southeast asian": ["thai", "vietnamese", "malaysian", "indonesian", "filipino", "singaporean"],
}


def _cuisine_names(chip: str) -> list[str]:
    want = _cuisine_norm(chip)
    if not want:
        return []
    families = {_cuisine_norm(k): v for k, v in CUISINE_FAMILIES.items()}
    return [want] + [_cuisine_norm(n) for n in families.get(want, [])]


def dish_is_cuisine(chip: str, meal: str | None, cuisine: str | None = None) -> bool:
    """
    Whether a dish answers a cuisine chip: the recipe's own cuisine field
    or the dish's name says it, as whole words — "Mexican" for Mexican,
    "Smash Burgers" for Burgers, "Chicken curry" for Curries (plurals read
    as one word, -ies and -es included), and a family chip answers to its
    members (Asian to a Thai dish, Tex-Mex to a Mexican one — CUISINE_
    FAMILIES). "Fries" is not in "Stir-fry". A chip that says nothing
    matches nothing.
    """
    names = _cuisine_names(chip)
    if not names:
        return False
    for text in (cuisine, meal):
        said = _cuisine_norm(text or "")
        if said and any(any(True for _ in _typed(want, said)) for want in names):
            return True
    return False


def _pick_is_cuisine(candidate: dict, chip: str) -> str | None:
    return None if dish_is_cuisine(chip, candidate.get("meal_name"), candidate.get("cuisine")) else f"not {chip}"


def _cuisine_slot(plan_id: int, entries: list[dict], chains: dict, keep_ids=frozenset()) -> dict | None:
    """
    The one slot to re-pick for a chip: a planned dinner (else lunch)
    nobody asked for (meal_variety.theirs, or `keep_ids` — the only dish
    answering another picked chip, and a dish their own words name), not
    cooked, not either end of a leftovers chain, not already re-picked for
    another chip or a typed ingredient, and — so the distinct-dish count
    stays as enforced — a dish on that one night when there is one. Within
    that, the night with the most time (no cap first, then the biggest), so
    Burgers lands where a burger fits rather than on a 20-minute Tuesday;
    earliest on a tie.
    """
    from . import swap_in_place as _swap

    counts: dict[tuple, int] = {}
    for e in entries:
        if e["slot"] in _REPICK_SLOTS and e["slot_state"] == "planned" and e["meal"]:
            k = (e["slot"], e["meal"].strip().lower())
            counts[k] = counts.get(k, 0) + 1
    for once_only in (True, False):
        for slot in _REPICK_SLOTS:
            fits = []
            for e in entries:
                if e["slot"] != slot or e["slot_state"] != "planned" or not e["meal"]:
                    continue
                if e["id"] in keep_ids:
                    continue
                if _meal_variety._LEFTOVER_LINE.search(e["freeform_meal"] or ""):
                    continue
                try:
                    derived = json.loads(e["derived_from_json"] or "{}") or {}
                except (TypeError, ValueError):
                    derived = {}
                if _meal_variety.theirs(derived) or (e["cooked_status"] or "") == "done":
                    continue
                if derived.get("cuisine_repick") or derived.get("must_use_repick") or derived.get("prep_date"):
                    continue
                if e["id"] in chains["leftovers"] or e["id"] in chains["sources"]:
                    continue
                if once_only and counts[(slot, e["meal"].strip().lower())] != 1:
                    continue
                fits.append(e)
            if fits:
                capped = _swap.day_caps(plan_id, [dict(e, entry_id=e["id"]) for e in fits])
                best = min(capped, key=lambda pair: (pair[1] is not None, -(pair[1] or 0), pair[0]["date"]))
                return next(e for e in fits if e["id"] == best[0]["id"])
    return None


def _is_cuisine_adjective(chip: str) -> bool:
    from . import week_intake as _week_intake
    known = {c.lower() for c in (_week_intake.KNOWN_CUISINES + _week_intake.ONBOARDING_CUISINES)}
    known |= {k for k in CUISINE_FAMILIES if k not in ("bbq", "barbecue")}
    return chip.strip().lower() in known


# Plural dish words that end in -s without being plural dish nouns in the
# sense this line wants ("No Swiss dish", "No Hummus dish").
_NOT_A_PLURAL = {"hummus", "couscous", "asparagus", "swiss", "citrus", "molasses"}


def _is_plural_dish_noun(chip: str) -> bool:
    last = chip.split()[-1].lower() if chip.split() else ""
    if chip.lower() in _NOT_A_PLURAL or last in _NOT_A_PLURAL or _is_cuisine_adjective(chip):
        return False
    return len(last) > 3 and last.endswith("s") and not last.endswith("ss")


# First words that are names, and keep their capital mid-sentence:
# "No Brussels sprouts fit", "No Korean tacos fit" (every known cuisine
# counts too — _is_cuisine_adjective).
_PROPER_FIRST_WORDS = {"brussels", "caesar", "buffalo", "philly", "belgian", "swedish", "nashville",
                       "texas", "cajun", "creole", "sichuan", "szechuan", "hawaiian"}


def _sentence_case(chip: str) -> str:
    """The chip mid-sentence: its own capitals kept, except a first letter
    that is only there because it starts the chip ("Burgers" -> "burgers",
    "Brussels sprouts" stays)."""
    first = chip.split()[0]
    if first.lower() in _PROPER_FIRST_WORDS or _is_cuisine_adjective(first):
        return chip
    return chip[:1].lower() + chip[1:]


def cuisine_unmet_line(chip: str) -> str:
    """
    The opener's one plain line for a chip nothing could answer
    (draft_opener._line_two). The chip as the household wrote it:
    "No Mexican dish fit this week.", "No Vegetarian dish…", "No BBQ
    dish…", "No Mac and cheese dish…". Only a chip that is itself a
    plural dish noun reads as one, lower-cased: "No burgers fit this
    week.", "No curries fit this week."
    """
    chip = " ".join((chip or "").split())
    if not chip:
        return ""
    if _is_plural_dish_noun(chip):
        return f"No {_sentence_case(chip)} fit this week."
    return f"No {chip} dish fit this week."


def _drop_unmet_about(unmet: list, chip: str) -> None:
    """A chip the week now answers: every unmet line that IS about it goes
    — ours (its `cuisine`) and a model line whose words are the chip itself
    ({"words": "burger"} for Burgers, plural-insensitive) — so the opener
    never says "I couldn't fit Burgers" over a burger. A line that merely
    mentions it ("Turkey burgers on Friday", "Italian sausage on Tuesday")
    is a different request and stays."""
    want = _cuisine_norm(chip)

    def about(u) -> bool:
        if str(u.get("cuisine") or "").lower() == chip.lower():
            return True
        said = _cuisine_norm(str(u.get("words") or ""))
        return bool(want) and said == want

    unmet[:] = [u for u in unmet if not about(u)]


def chips_left_unanswered(plan_id: int, cuisines: list[str] | None, report: dict) -> list[str]:
    """
    After the passes that can still take a dish away — cap_enforce's
    re-pick and the allergen sweep — a chip whose only dish went gets its
    unmet line after all, so the opener never stays quiet about it. No
    model call: it only reads the plan. Returns the chips it added. Never
    raises.
    """
    added: list[str] = []
    try:
        _honoured, unmet = _report_lists(report)
        said = {str(u.get("cuisine") or "").lower() for u in unmet}
        planned = [e for e in _load_entries(plan_id) if e["slot"] in _REPICK_SLOTS and e["slot_state"] == "planned"
                   and e["meal"] and not _meal_variety._LEFTOVER_LINE.search(e["freeform_meal"] or "")]
        for chip in _unique_chips(cuisines):
            if chip.lower() in said:
                continue
            if not any(dish_is_cuisine(chip, e["meal"], e.get("cuisine")) for e in planned):
                unmet.append({"words": chip, "reason": "nothing of it fit", "cuisine": chip})
                added.append(chip)
        if added:
            logger.info("Plan %s: %s lost its only dish after the chip pass", plan_id, ", ".join(added))
    except Exception:
        logger.exception("Cuisine-chip re-check failed for plan %s", plan_id)
    return added


def _unique_chips(cuisines) -> list[str]:
    chips: list[str] = []
    for c in cuisines or []:
        c = str(c or "").strip()
        if c and c.lower() not in {x.lower() for x in chips}:
            chips.append(c)
    return chips


def use_picked_cuisines(plan_id: int, cuisines: list[str] | None, report: dict, budget=None, picker=None,
                        asks: tuple[str | None, ...] = ()) -> dict:
    """
    For every cuisine chip the household picked this week (intake.cuisines),
    make sure one lunch or dinner is of that cuisine — the model's own dish
    when one is, else one slot re-picked for it — and when none can be,
    say so in `report` (an unmet line carrying `cuisine`, for the opener).
    A re-picked dish carries `cuisines:<chip>` in derived_from.inputs; the
    row's "…, as asked" fact is still only said once draft_opener.asked_fact
    has checked the chip is this week's and the dish is that cuisine.
    Returns {"matched", "repicked", "unmet"} for the log and tests. Never
    raises.
    """
    out = {"matched": [], "repicked": [], "unmet": []}
    chips = _unique_chips(cuisines)
    if not chips:
        return out
    budget = budget or _allergen_gate.CallBudget()
    try:
        _honoured, unmet = _report_lists(report)
        for chip in chips:
            entries = _load_entries(plan_id)
            planned = [e for e in entries if e["slot"] in _REPICK_SLOTS and e["slot_state"] == "planned"
                       and e["meal"] and not _meal_variety._LEFTOVER_LINE.search(e["freeform_meal"] or "")]
            if any(dish_is_cuisine(chip, e["meal"], e.get("cuisine")) for e in planned):
                out["matched"].append(chip)
                _drop_unmet_about(unmet, chip)
                continue
            # Never the only dish another chip has, and never one their
            # own words name (Emily's chips Mexican + Burgers: the burger
            # must not take the week's only Mexican dish).
            keep = set()
            for other in chips:
                if other.lower() == chip.lower():
                    continue
                answering = [e["id"] for e in planned if dish_is_cuisine(other, e["meal"], e.get("cuisine"))]
                if len(answering) == 1:
                    keep.update(answering)
            keep.update(e["id"] for e in planned if asks and _meal_variety.asked_for_by_name(e["meal"], asks))
            target = _cuisine_slot(plan_id, entries, _leftovers.plan_leftover_chains(plan_id), keep_ids=keep)
            replaced = None
            if target is not None:
                from . import swap_in_place as _swap
                slot_entry = {"date": target["date"], "slot": target["slot"], "entry_id": target["id"]}

                def reject_pick(candidate, _chip=chip, _entry=slot_entry):
                    return _pick_is_cuisine(candidate, _chip) or _swap.cap_gate(plan_id, candidate, [_entry])

                replaced = _meal_variety._repick_entry(
                    plan_id, target, budget,
                    avoid=[], because=CUISINE_BECAUSE.format(cuisine=chip),
                    reject=lambda name: False,
                    derived_key="cuisine_repick", picker=picker,
                    context_extra={"must_be_cuisine": chip},
                    reject_pick=reject_pick,
                    derived_extra={"inputs": [f"cuisines:{chip}"]},
                )
            if replaced is None:
                # Said, never silent: the opener reads this line.
                unmet[:] = [u for u in unmet if str(u.get("cuisine") or "").lower() != chip.lower()]
                unmet.append({"words": chip, "reason": "nothing of it fit", "cuisine": chip})
                out["unmet"].append(chip)
                logger.info("Plan %s: no lunch or dinner was %s, and no re-pick landed one", plan_id, chip)
                continue
            out["repicked"].append(chip)
            _drop_unmet_about(unmet, chip)
            logger.info("Plan %s: %s %s re-picked as %r so the %s chip is on the week",
                        plan_id, target["date"], target["slot"], replaced.get("meal"), chip)
    except Exception:
        logger.exception("Cuisine-chip pass failed for plan %s; the week stands as generated", plan_id)
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
