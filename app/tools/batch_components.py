"""
One component, several dishes, one cook.

Emily, 2026-09-13, reviewing the cook-ahead ask on her phone: "if there is
something that is repeated it should be in the same group. For example,
I'm seeing boiled eggs in two different recipes, ask me if I want them
all in the same bulk cook."

cook_ahead.py groups repeats of the same DISH ("Egg White Bites is on 5
mornings"). This module looks inside dishes: when two or more different
dishes on the plan each cook the same thing the same way — hard-boiled
eggs in a breakfast and again in a salad — the approval-time ask gets ONE
block for that component, and saying yes files one cook for all of it on
the earliest day.

How "the same prepped component" is found, with the data that exists
today (no new recipe field, no model call — the mental-load guardrail in
the loop skill forbids adding a data-entry step, and reliable beats
clever):

  A recipe stores its ingredients as bare names with no prep descriptor
  ("Eggs", never "Eggs, hard-boiled" — the generation prompt asks for
  exactly that so the grocery list reads cleanly), and its method as a
  list of instruction steps. So the prep STATE of an ingredient lives in
  the steps: "Hard-boil the eggs", "Roast the chickpeas". This module
  reads each step clause for a cooking verb from a short fixed list
  (_PREP_VERBS) and takes the first of the recipe's own ingredients named
  in the next few words as the thing being cooked. An imported recipe
  that kept the descriptor on the ingredient line ("Hard-boiled eggs",
  "4, hard-boiled") is read the same way from that line. Two dishes share
  a component when they land on the same verb AND the same ingredient
  (grocery._merge_key, so "egg" and "eggs" agree).

  Its limits, stated plainly: a recipe with no steps and no descriptor
  offers nothing; a step that names the verb without the ingredient near
  it ("Bring to a boil. Add the eggs.") is missed; and "cook" is only
  read for grains and legumes (_COOK_ONLY_FOR), because "cook the onions"
  in two recipes is not a batch anyone wants to be asked about. A miss
  costs one question not asked; a wrong match costs a silly question, so
  the reading fails toward silence.

What saying yes writes: ONE prep_tasks row (task_type
'batch_component'), dated on the earliest chosen day and attached to that
day's entry, so it is a real tickable thing on that dish's cook screen
and on Today's timeline — the same table a thaw or a prep-cut lives in.
detail_json carries which entries it covers, and get_cooker_view reads
that back onto the cards: the cook day's card says the batch is for the
later dishes too, and each later dish reads that its component is
already done. Saying no writes nothing.

Groceries are untouched, exactly as cook_ahead.py leaves them: the
household eats the same eggs whichever day they are boiled.
"""
from __future__ import annotations

import json
import re
from datetime import date

from ..db import get_conn
from ._shared import household_id
from . import grocery as _grocery
from . import leftovers as _leftovers
from . import quantities as _quantities
from . import recipes as _recipes

BATCH_TASK_TYPE = "batch_component"

# verb stem -> (imperative, past participle). The stems are what a step
# says ("Hard-boil the eggs"); the participle is what the ask says
# ("Boiled eggs"); the imperative is what the prep row says ("Boil the
# eggs for Wednesday's Egg Salad too"). Short on purpose: these are the
# things a household really does batch — boil, roast, grill, bake, poach,
# steam — plus "cook" for the grains it is always said about.
_PREP_VERBS: dict[str, tuple[str, str]] = {
    "boil": ("Boil", "boiled"),
    "hard-boil": ("Boil", "boiled"),
    "hardboil": ("Boil", "boiled"),
    "soft-boil": ("Boil", "boiled"),
    "roast": ("Roast", "roasted"),
    "grill": ("Grill", "grilled"),
    "bake": ("Bake", "baked"),
    "poach": ("Poach", "poached"),
    "steam": ("Steam", "steamed"),
    "braise": ("Braise", "braised"),
    "cook": ("Cook", "cooked"),
}

# Participle forms that may sit on an ingredient line itself ("Hard-boiled
# eggs", "cooked rice") — read as that verb, and stripped from the name so
# the ingredient still matches the plain "Eggs" of another recipe.
_PARTICIPLES: dict[str, str] = {
    "boiled": "boil", "hard-boiled": "boil", "hardboiled": "boil", "soft-boiled": "boil",
    "roasted": "roast", "grilled": "grill", "baked": "bake", "poached": "poach",
    "steamed": "steam", "braised": "braise", "cooked": "cook",
}

# "cook" is read only when the thing cooked is one of these — the grains
# and legumes people mean when they say "cook a big batch of rice".
_COOK_ONLY_FOR = {
    "rice", "quinoa", "pasta", "noodle", "lentil", "bean", "chickpea", "farro",
    "barley", "couscous", "bulgur", "potato", "grain", "orzo", "polenta", "oat",
}

# Words in an ingredient name that describe rather than name it, so
# "large eggs" and "eggs" are the same thing. quantities' descriptor set
# plus the size/state words that turn up on ingredient lines.
_IGNORED_WORDS = set(_quantities._DESCRIPTOR_WORDS) | {
    "of", "and", "or", "the", "a", "an", "for", "with", "to", "in",
    "extra", "free-range", "free", "range", "peeled", "chopped", "diced", "sliced",
    "minced", "grated", "shredded", "halved", "quartered", "whole", "plain",
}

# How many words after the verb the ingredient may sit: "Roast the
# chickpeas" is two, "Hard-boil all of the eggs" is four.
_WINDOW = 5

_WORD_RE = re.compile(r"[a-z][a-z\-]*")
_CLAUSE_RE = re.compile(r"[.;:,()!?]|\bthen\b|\band then\b")


def _weekday(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _singular(word: str) -> str:
    return _grocery._singular_word(word)


def _ingredient_words(item: str) -> list[str]:
    """The words that name an ingredient, singularised: "Large free-range
    eggs" -> ["egg"]; "chicken breast" -> ["chicken", "breast"]."""
    out = []
    for w in _words(item.split(",", 1)[0]):
        if w in _PARTICIPLES or w in _IGNORED_WORDS or len(w) < 3:
            continue
        out.append(_singular(w))
    return out


def _ingredient_key(item: str) -> str:
    return " ".join(_ingredient_words(item))


def _ingredient_display(item: str) -> str:
    """The name as the ask will say it — the line's own words minus the
    descriptors, lowercased: "Hard-boiled eggs" -> "eggs"."""
    kept = [w for w in _words(item.split(",", 1)[0]) if w not in _PARTICIPLES and w not in _IGNORED_WORDS]
    return " ".join(kept)


def _verb_of(token: str) -> str | None:
    """The verb stem a step word stands for, or None. Matches the bare
    imperative ("roast"), its -s form and the participle ("roasted")."""
    if token in _PREP_VERBS:
        return token
    if token in _PARTICIPLES:
        return _PARTICIPLES[token]
    if token.endswith("s") and token[:-1] in _PREP_VERBS:
        return token[:-1]
    return None


def _recipe_components(recipe: dict) -> dict[str, dict]:
    """
    Every (verb, ingredient) pair one recipe's own text says it cooks:
    {key: {label, verb, ingredient, ingredient_key, qty, cook_qty}}.

    Read from two places. The ingredient line first — an imported recipe
    may say "Hard-boiled eggs" outright. Then each instruction step, clause
    by clause: a cooking verb, then the first of this recipe's ingredients
    named within the next few words.
    """
    ingredients = [i for i in (recipe.get("ingredients") or []) if isinstance(i, dict) and (i.get("item") or "").strip()]
    if not ingredients:
        return {}
    by_word: dict[str, dict] = {}
    for ing in ingredients:
        for w in _ingredient_words(ing["item"]):
            by_word.setdefault(w, ing)

    found: dict[str, dict] = {}

    def add(verb: str, ing: dict) -> None:
        ingredient_key = _ingredient_key(ing["item"])
        if not ingredient_key:
            return
        if verb == "cook" and not any(w in _COOK_ONLY_FOR for w in ingredient_key.split()):
            return
        imperative, participle = _PREP_VERBS[verb]
        display = _ingredient_display(ing["item"]) or ingredient_key
        key = f"{participle}:{ingredient_key}"
        found.setdefault(key, {
            "key": key,
            "label": f"{participle[0].upper()}{participle[1:]} {display}",
            "imperative": imperative,
            "verb": participle,
            "ingredient": display,
            "ingredient_key": ingredient_key,
            "qty": (ing.get("qty") or "").strip(),
            "cook_qty": (ing.get("cook_qty") or "").strip(),
        })

    # 1. The ingredient line itself: "Hard-boiled eggs" / "Eggs · 4, hard-boiled".
    for ing in ingredients:
        line_words = _words(ing["item"]) + _words(ing.get("qty") or "")
        for w in line_words:
            if w in _PARTICIPLES:
                add(_PARTICIPLES[w], ing)
                break

    # 2. The steps: a verb, then the ingredient it is done to.
    for step in recipe.get("instructions") or []:
        for clause in _CLAUSE_RE.split(str(step or "")):
            tokens = _words(clause)
            for i, tok in enumerate(tokens):
                verb = _verb_of(tok)
                if not verb:
                    continue
                for nxt in tokens[i + 1:i + 1 + _WINDOW]:
                    if _verb_of(nxt):
                        break  # "roast and grill the..." — start again at that verb
                    ing = by_word.get(_singular(nxt))
                    if ing is not None:
                        add(verb, ing)
                        break
    return found


def _plan_uses(weekly_plan_id: int) -> list[dict]:
    """Every cookable day-based entry on the plan with its recipe, in the
    week's order. Reheat nights are left out: nothing is cooked on one."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.recipe_id, mpe.freeform_meal,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
          AND mpe.slot_state = 'planned'
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    if not rows:
        return []
    reheats = set(_leftovers.plan_leftover_chains(weekly_plan_id)["leftovers"])
    recipes_by_name = {r["name"].lower(): r for r in _recipes.list_recipes()}
    uses = []
    for row in rows:
        if row["id"] in reheats or not (row["meal"] or "").strip():
            continue
        recipe = recipes_by_name.get(row["meal"].strip().lower())
        if recipe is None:
            continue
        uses.append({"row": row, "recipe": recipe})
    return uses


def _batch_rows(weekly_plan_id: int) -> list[dict]:
    """
    This plan's batch rows, with detail_json read. A row whose cook-day
    entry has left the plan (swapped away — _replace_slot_entries deletes
    the entry, not the prep rows attached to it) is deleted here rather
    than returned: a "Boil the eggs" task for a dish that is gone would
    otherwise surface as a loose get-ready row on Cook's root, and a task
    the app wrote and can no longer explain is worse than none. Same for
    a row none of whose covered dishes are still on the plan — there is
    nothing left to batch for.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task_date, description, related_meal, status, meal_plan_entry_id, quantity, detail_json "
        "FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ? AND task_type = ? "
        "ORDER BY task_date ASC, id ASC",
        (weekly_plan_id, household_id(), BATCH_TASK_TYPE),
    ).fetchall()
    live = {
        r["id"] for r in conn.execute(
            "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchall()
    }
    out, stale = [], []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(r["detail_json"] or "{}")
        except (TypeError, ValueError):
            d["detail"] = {}
        covered_live = [e for e in (d["detail"].get("covered_entry_ids") or []) if e in live]
        if d["meal_plan_entry_id"] not in live or not covered_live:
            stale.append((d["id"],))
            continue
        out.append(d)
    if stale:
        conn.executemany("DELETE FROM prep_tasks WHERE id = ? AND household_id = ?", [(i, household_id()) for (i,) in stale])
        conn.commit()
    conn.close()
    return out


def _combined_quantity(uses: list[dict]) -> str:
    """The amount all the dishes need together, in cook terms: each use's
    cooking amount for the people eating it (recipes.cooking_ingredients,
    the Cook card's own arithmetic), added up the way the grocery ledger
    adds (quantities._sum_ledger_quantities). Blank when a line doesn't
    parse — an amount nobody can add is not stated as one."""
    amounts = []
    for use in uses:
        eaters = _leftovers.eaters_at(use["date"], use["slot"]) or None
        for ing in _recipes.cooking_ingredients(use["ingredients"], servings=eaters):
            if isinstance(ing, dict) and _ingredient_key(ing.get("item") or "") == use["ingredient_key"]:
                qty = _quantities._strip_prep_descriptor((ing.get("qty") or "").strip())
                if qty:
                    amounts.append(qty)
                break
    if len(amounts) != len(uses):
        return ""
    try:
        total = _quantities._sum_ledger_quantities(amounts)
    except Exception:
        return ""
    return total or ""


def shared_components(weekly_plan_id: int) -> list[dict]:
    """
    Every component two or more DIFFERENT dishes on this plan cook the
    same way, gathered for the approval-time ask:

    [{key, label, ingredient, uses: [{entry_id, date, slot, dish}],
      first: {entry_id, date}, quantity, batched}]

    `uses` is every cook on the plan that makes the component, in date
    order, the same dish on two days counted twice — one cook each. A
    component that a single dish cooks, however many nights that dish is
    on, is cook_ahead.py's question and not this one. `batched` is whether
    a batch row already stands for this key (then the Cook view carries
    it and the ask leaves it alone).
    """
    uses = _plan_uses(weekly_plan_id)
    if len(uses) < 2:
        return []
    groups: dict[str, dict] = {}
    for use in uses:
        row, recipe = use["row"], use["recipe"]
        for key, comp in _recipe_components(recipe).items():
            group = groups.setdefault(key, {"comp": comp, "uses": []})
            group["uses"].append({
                "entry_id": row["id"],
                "date": row["date"],
                "slot": row["slot"],
                "dish": (row["meal"] or "").strip(),
                "ingredients": recipe.get("ingredients") or [],
                "ingredient_key": comp["ingredient_key"],
            })

    batched = {r["detail"].get("key") for r in _batch_rows(weekly_plan_id)}
    items = []
    for key, group in groups.items():
        dishes = {u["dish"].lower() for u in group["uses"]}
        if len(dishes) < 2:
            continue
        group["uses"].sort(key=lambda u: (u["date"], u["entry_id"]))
        comp = group["comp"]
        items.append({
            "key": key,
            "label": comp["label"],
            "ingredient": comp["ingredient"],
            "uses": [
                {"entry_id": u["entry_id"], "date": u["date"], "slot": u["slot"], "dish": u["dish"]}
                for u in group["uses"]
            ],
            "first": {"entry_id": group["uses"][0]["entry_id"], "date": group["uses"][0]["date"]},
            "quantity": _combined_quantity(group["uses"]),
            "batched": key in batched,
        })
    items.sort(key=lambda i: (i["first"]["date"], i["label"].lower()))
    return items


def _description(comp: dict, others: list[dict], quantity: str = "") -> str:
    """"Boil the eggs for Wednesday's Egg Salad too — 10 in all" — the prep
    row's words, said the way a person would across the kitchen. The
    total comes last and only when it could be added up."""
    named = _leftovers._join_days([f"{_weekday(o['date'])}’s {o['dish']}" for o in others])
    line = f"{comp['imperative']} the {comp['ingredient']} for {named} too"
    return f"{line} — {quantity} in all" if quantity else line


def set_batch_component(weekly_plan_id: int, key: str, entry_ids: list[int]) -> dict | str:
    """
    Record "make this component for these dishes in one go" — or return
    the sentence to show instead, the same shape cook_ahead.set_cook_ahead
    answers with.

    The earliest chosen entry is the cook day; the rest are covered. ONE
    prep_tasks row is written for the batch (see the module docstring),
    replacing any earlier answer for the same key on this plan so that
    re-answering from the Cook view never stacks two batches. Fewer than
    two chosen dishes is not a batch, and is refused rather than written
    as a row that covers nothing.
    """
    items = {i["key"]: i for i in shared_components(weekly_plan_id)}
    item = items.get(key)
    if item is None:
        return "That one isn’t on this week’s plan anymore."
    offered = {u["entry_id"]: u for u in item["uses"]}
    chosen = [offered[e] for e in dict.fromkeys(entry_ids or []) if e in offered]
    if len(chosen) < 2:
        return "Pick at least two dishes to make them at once."
    chosen.sort(key=lambda u: (u["date"], u["entry_id"]))
    source, others = chosen[0], chosen[1:]
    if len({u["dish"].lower() for u in chosen}) < 2 and len(chosen) == len(item["uses"]):
        # Can't happen through the ask (shared_components needs two dishes)
        # but a hand-built request could; a same-dish repeat is
        # cook_ahead.py's question.
        return "That’s the same dish on two days — tick the days on its own line instead."

    comp = None
    for use in _plan_uses(weekly_plan_id):
        if use["row"]["id"] == source["entry_id"]:
            comp = _recipe_components(use["recipe"]).get(key)
            break
    if comp is None:
        return "That one isn’t on this week’s plan anymore."

    quantity = _combined_quantity([
        {**u, "ingredients": next(
            (x["recipe"].get("ingredients") or [] for x in _plan_uses(weekly_plan_id) if x["row"]["id"] == u["entry_id"]), []
        ), "ingredient_key": comp["ingredient_key"]}
        for u in chosen
    ])
    detail = {
        "key": key,
        "label": comp["label"],
        "ingredient": comp["ingredient"],
        "source_entry_id": source["entry_id"],
        "covered_entry_ids": [u["entry_id"] for u in others],
        "dishes": [{"entry_id": u["entry_id"], "date": u["date"], "dish": u["dish"]} for u in chosen],
        "quantity": quantity,
    }
    description = _description(comp, others, quantity)

    conn = get_conn()
    conn.execute(
        "DELETE FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ? AND task_type = ? "
        "AND json_extract(detail_json, '$.key') = ?",
        (weekly_plan_id, household_id(), BATCH_TASK_TYPE, key),
    )
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id, quantity, detail_json) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
        (household_id(), weekly_plan_id, source["date"], description, source["dish"],
         BATCH_TASK_TYPE, source["entry_id"], quantity, json.dumps(detail)),
    )
    conn.commit()
    conn.close()
    return {
        "prep_task_id": cur.lastrowid,
        "key": key,
        "label": comp["label"],
        "source_entry_id": source["entry_id"],
        "covered_entry_ids": detail["covered_entry_ids"],
        "quantity": quantity,
        "description": description,
    }


def clear_batch_component(weekly_plan_id: int, key: str) -> int:
    """
    Take a component batch off this plan — the row itself, not a smaller
    version of it. Returns how many rows went.

    set_batch_component already deletes this plan's row for a key before
    writing the new one; this is that DELETE on its own, for the one
    caller that has nothing to write in its place (batch_undo.py). It is not
    set_batch_component with an empty list: that refuses, correctly, with
    "Pick at least two dishes" — a sentence about making a batch, said to
    somebody undoing one.
    """
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ? AND task_type = ? "
        "AND json_extract(detail_json, '$.key') = ?",
        (weekly_plan_id, household_id(), BATCH_TASK_TYPE, key),
    )
    conn.commit()
    removed = cur.rowcount
    conn.close()
    return removed if removed and removed > 0 else 0


def batched_components(weekly_plan_id: int) -> list[dict]:
    """
    The batches standing on this plan, read for the All set line
    (weekly_plan.batched_line): [{key, verb, ingredient, label, date,
    dish, covered: [{entry_id, date, slot, dish}]}] in cook-day order. `verb`
    is the participle ("boiled"); `covered` is the later dishes only —
    the cook day's own dish is `dish`. A stale row (its cook day or every
    covered dish swapped away) is dropped on the way, as _batch_rows does.
    """
    rows = _batch_rows(weekly_plan_id)
    if not rows:
        return []
    conn = get_conn()
    slot_of = {
        r["id"]: r["slot"] for r in conn.execute(
            "SELECT id, slot FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchall()
    }
    conn.close()
    out = []
    for row in rows:
        detail = row["detail"]
        source_id = detail.get("source_entry_id") or row["meal_plan_entry_id"]
        dishes = {d.get("entry_id"): d for d in (detail.get("dishes") or []) if isinstance(d, dict)}
        covered = [
            {"entry_id": e, "date": dishes[e].get("date") or "", "slot": slot_of.get(e, ""),
             "dish": dishes[e].get("dish") or ""}
            for e in (detail.get("covered_entry_ids") or []) if e in dishes and e in slot_of
        ]
        covered.sort(key=lambda c: (c["date"], c["entry_id"]))
        verb, _, _ingredient_key = (detail.get("key") or row["description"]).partition(":")
        out.append({
            "key": detail.get("key") or "",
            "verb": verb if _ingredient_key else "",
            "ingredient": detail.get("ingredient") or "",
            "label": detail.get("label") or row["description"],
            "date": row["task_date"],
            "dish": (dishes.get(source_id) or {}).get("dish") or row["related_meal"] or "",
            # The entry this batch is cooked on. Derived here, off
            # _batch_rows(weekly_plan_id), so it is scoped to THIS plan by
            # construction. It used to be computed and dropped, and
            # batch_undo then re-found it with a household-only query
            # taking the newest row for the key across every plan — see
            # that function for what that cost.
            "source_entry_id": source_id,
            "covered": covered,
        })
    out.sort(key=lambda b: (b["date"], b["label"].lower()))
    return out


def attach_batch_components(weekly_plan_id: int, meals: list[dict]) -> None:
    """
    Hang the week's batches on the Cook view's cards.

    The cook day's card gets `batch_components`: [{label, ingredient,
    quantity, prep_task_id, done, covers: [{entry_id, date, dish}]}].
    Each covered card gets `components_made_ahead`: [{label, ingredient,
    source_date, source_meal, done}], and the matching ingredient row a
    `made_ahead` note ("boiled Monday") so the get-out list says it where
    the eggs are read off. Every card carries both keys, empty or not, so
    the screen can branch without checking for them.

    A covered entry that has since left the plan is simply not there to
    hang anything on; a batch whose cook day left the plan went with it
    (swap_meal_in_plan deletes that entry's prep rows).
    """
    by_entry = {m.get("entry_id"): m for m in meals}
    for m in meals:
        m.setdefault("batch_components", [])
        m.setdefault("components_made_ahead", [])
    for row in _batch_rows(weekly_plan_id):
        detail = row["detail"]
        source = by_entry.get(row["meal_plan_entry_id"])
        if source is None:
            continue
        done = row["status"] == "done"
        covers = []
        for e in detail.get("covered_entry_ids") or []:
            card = by_entry.get(e)
            if card is None or card.get("is_leftovers"):
                continue
            covers.append({"entry_id": e, "date": card["date"], "dish": card["meal"]})
            card["components_made_ahead"].append({
                "label": detail.get("label") or row["description"],
                "ingredient": detail.get("ingredient") or "",
                "source_date": row["task_date"],
                "source_meal": source["meal"],
                "done": done,
            })
            ingredient_key = detail.get("key", "").split(":", 1)[-1]
            for ing in card.get("ingredients") or []:
                if isinstance(ing, dict) and _ingredient_key(ing.get("item") or "") == ingredient_key:
                    verb = detail.get("key", "").split(":", 1)[0]
                    ing["made_ahead"] = f"{verb} {_weekday(row['task_date'])}".strip()
        if not covers:
            continue
        source["batch_components"].append({
            "label": detail.get("label") or row["description"],
            "ingredient": detail.get("ingredient") or "",
            "quantity": row["quantity"] or "",
            "prep_task_id": row["id"],
            "done": done,
            "covers": covers,
        })
