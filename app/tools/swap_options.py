"""
Three other picks for one slot — the Week 1 reveal's Swap (Loop Board
"Week 1: day-card carousel replaces 'sample week'", 2026-09-18).

The in-app Swap is "Swap · I'll pick": one model call, one dish, written
straight onto the slot (swap_in_place). The first-week screen wants the
other half of that bargain — "I'll show you three other picks" (the
Need-a-hand sheet's own words) — so the household chooses before anything
is written. This module is that:

  * ONE model call asks for three dishes instead of one, with exactly the
    context swap_in_place already builds for a slot (build_swap_context:
    the hard exclusions, the table, the night's tags and cap, the rest of
    the week, `avoid`). Nothing is written.
  * Every option is run through the same two gates a swap runs before it
    is offered (swap_in_place.pick_gate: the hard allergen match and the
    one-veto taste rule). A pick that fails is dropped, not shown — an
    option the household can't have is not an option.
  * The chosen one is applied through swap_in_place.apply_pick, exactly
    as a swap or the chat's change card is, so Undo, the grocery list and
    the leftover chains all behave as they do everywhere else.

Cached per entry for the sitting, the way plate_parts.part_options is:
tapping Swap twice on the same dish opens instantly the second time, and
the cache is forgotten the moment the slot changes.

**Picks arrive as picks, not recipes** (Loop Board "Swap picks arrive
faster", Emily, 2026-09-21: "make it faster if possible"). Until then the
options call asked for three complete recipes — every ingredient with its
store quantity, every step — and Railway's log showed it at 10.5–13 s for
1300–1600 output tokens, of which the sheet drew a name, a line and the
minutes. Now the call asks for what the sheet shows plus what the gate
needs (the main ingredients, no quantities), at the `picks` effort route
(low) with max_tokens capped to three trimmed picks; the recipe is
written out for ONE dish, the one the household taps, at choose time
(`_write_out`: the swap's own tool, instructions and cached prefix, so a
chosen pick lands as a full, shoppable recipe exactly as "Swap · I'll
pick" does). A pick that already carries steps — a test's, or a dish the
household has saved — is applied as it is.
"""
from __future__ import annotations

import json
import logging
import time

from ._shared import household_id
from . import model_shapes as _model_shapes
from . import swap_in_place as _swap
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")

OPTION_COUNT = 3
_OPTIONS_TTL = 30 * 60
# (household_id, entry_id) -> {"at": t, "meal": name, "options": [...],
#   "unavailable": bool, "context": the slot JSON the picks were asked
#   against, kept so the chosen one is written out against the same}
_OPTIONS_CACHE: dict[tuple[int, int], dict] = {}

# How many main ingredients a pick names. Enough for the allergen gate to
# see what the dish is made of (the gate matches items, not names), few
# enough that three picks stay a few hundred tokens.
MAIN_INGREDIENTS_MAX = 6

# What three trimmed picks need. Measured on the trimmed schema: a pick is
# a name, a line under ten words, six or so plain grocery names and a
# number — about 70–90 tokens as tool JSON, so three are under 300. The
# cap leaves room for the model's own reasoning at low effort (thinking
# tokens count against max_tokens) without letting it wander back into
# writing recipes: the old call's 1300–1600 no longer fit.
OPTIONS_MAX_TOKENS = 1200

# The one pick, written out at choose time — the same budget the single
# swap's own call has (swap_in_place._pick_replacement), since it is the
# same job: one dish, every quantity, every step.
WRITE_OUT_MAX_TOKENS = 2000

OPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "meal_name": {
            "type": "string",
            "description": "What the dish IS — never named after an ingredient it leaves out.",
        },
        "reason": {
            "type": "string",
            "description": "ONE short line the household reads on the card — warm, plain, under about ten words, no exclamation mark. Say what makes this a better fit than the dish it replaces. E.g. 'Lighter than the chops, and nothing to thaw.'",
        },
        "ingredients": {
            "type": "array",
            "description": f"The main items only — the protein, the starch, the vegetables, the one thing that makes it this dish — as plain grocery names ('Chicken thighs', 'Baby spinach'), at most {MAIN_INGREDIENTS_MAX}. No quantities, no staples (salt, pepper, oil), no steps.",
            "items": {"type": "string"},
        },
        "minutes": {
            "type": "integer",
            "description": "Prep plus cook, in minutes.",
        },
    },
    "required": ["meal_name", "reason", "ingredients", "minutes"],
}

OPTIONS_TOOL = {
    "name": "submit_swap_options",
    "description": f"Submit {OPTION_COUNT} different replacement dishes for this slot — names, a line each, their main ingredients. Not recipes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "description": f"Exactly {OPTION_COUNT} dishes, each different from the others in protein or cuisine.",
                "items": OPTION_SCHEMA,
            },
        },
        "required": ["options"],
    },
}

# Static, so it caches: the household's own JSON is the only thing that
# changes between calls. The constraint rules are the swap's own
# (swap_in_place.INSTRUCTIONS), restated for a call that names dishes
# rather than writing them — the recipe for the chosen one is written by
# that module's instructions, at choose time, so the two never disagree
# about what the household can have.
INSTRUCTIONS = """You are offering OPTIONS to replace ONE meal in a household's already-planned week. \
Everything else about the week stays exactly as it is. Name exactly three dishes the household can \
choose between — different from each other in protein or cuisine, so the choice is a real one. \
Do NOT write recipes: for each dish give its name, one line for the card, its main ingredients as \
plain grocery names, and the minutes. Someone else writes the recipe for the one they pick.

Rules, in this order:
- `must_not_contain` is absolute. Nothing you offer may contain any of it, in the dish or in \
anything served with it. An allergy written as a sentence is still an allergy. If honouring it \
leaves you no good answer, offer a plainer dish rather than a clever one — never a compromise.
- Never name a dish after an ingredient it leaves out. No "Nut-Free Noodles". The name says what \
the dish IS.
- `avoid` is what has already been turned down for this slot, including the dish being replaced. \
Don't come back with any of them, or with a near-identical variant of one.
- `week_other_dishes` is the rest of this week. Don't repeat one, and don't repeat the protein or \
cuisine of the nights either side of this one — the point of a swap is a different night, not the \
same night renamed.
- `taste_verdicts`, when present, is per-person feedback already resolved into one verdict per \
table. A dish under `avoid:` has somebody at that table who won't eat it, and one person not \
eating it rules it out however many others like it. A dish under `loved:` is a nudge, never a \
reason to repeat the week.
- `plate_rule` is what a full plate covers here. Dinner and lunch cover all of it — name the side \
in the dish ("Lemon chicken with greens and rice") rather than leaving the plate short. Breakfast \
and snack cover at least two of those groups.
- `table.serves` is how many actually eat this meal. Offer things that suit that number.
- `max_minutes`, when given, is a hard cap on prep plus cook for this meal.
- `night_tags`: `rush` means fast and unfussy, `unrushed` means there is no time cap tonight (a \
longer dish is allowed, not required), `guests` means something the table will all eat, `normal` \
means an ordinary night — don't get clever with it.
- `eating_style` is a hard constraint, not a style nudge — every ingredient has to fit it. \
`dislikes` are to be avoided. `kitchen_kit` is what they own to cook with; an empty list means \
unknown, so don't constrain on it.
- `ingredients` is the main items only, as they are bought ("Chicken thighs", "Baby spinach"), no \
quantities and no staples — it is how the household's allergies are checked against the dish, so \
name what is actually in it.
- `reason` is the one line shown under the dish on the card. Warm, plain, specific to the swap, \
under about ten words, no exclamation mark: "Lighter than the chops, and nothing to thaw."

Call submit_swap_options with the three dishes."""

# Appended to the swap's own instructions at choose time: same rules, same
# cached prefix, one sentence that changes what is being asked.
WRITE_OUT_ASK = (
    "The household has already chosen the dish from three you offered — do not pick a different "
    "one. Write out exactly this dish, with every ingredient and its store quantity and every step:"
)


def _ask_options(context: dict) -> list[dict]:
    """The single model call — see swap_in_place._pick_replacement for why
    agent is imported lazily and why this is a utility-shaped call. Runs
    on the `picks` effort route (low): the constraints are handed to it
    and the answer is three names, not three recipes."""
    from .. import agent

    client = agent._client()
    response = agent._create_with_retry(
        client,
        label="swap_options",
        model=agent.MODEL,
        max_tokens=OPTIONS_MAX_TOKENS,
        tools=[OPTIONS_TOOL],
        tool_choice={"type": "tool", "name": "submit_swap_options"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"The slot (JSON):\n{json.dumps(context, indent=2)}"},
            ],
        }],
        output_config=agent._effort_config("picks"),
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning("swap_options hit max_tokens; the picks may be incomplete")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return _model_shapes.tool_list(block.input, "options", "swap_options")
    return []


def _write_out(context: dict, pick: dict) -> dict:
    """The chosen dish as a full recipe — the swap's own tool and
    instructions (so the cached prefix and every rule are shared with
    "Swap · I'll pick"), asked for ONE named dish rather than a choice."""
    from .. import agent

    client = agent._client()
    response = agent._create_with_retry(
        client,
        label="swap_option_writeout",
        model=agent.MODEL,
        max_tokens=WRITE_OUT_MAX_TOKENS,
        tools=[_swap.SWAP_TOOL],
        tool_choice={"type": "tool", "name": "submit_swap"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _swap.INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"The slot (JSON):\n{json.dumps(context, indent=2)}"},
                {"type": "text", "text": f"{WRITE_OUT_ASK} {pick['meal_name']}"},
            ],
        }],
        output_config=agent._effort_config("utility"),
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning("swap_option_writeout hit max_tokens; the recipe may be incomplete")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    return {}


def _minutes(pick: dict) -> int | None:
    if pick.get("minutes") is not None:
        try:
            return int(pick["minutes"]) or None
        except (TypeError, ValueError):
            pass
    total = int(pick.get("prep_time_minutes") or 0) + int(pick.get("cook_time_minutes") or 0)
    return total or None


def _as_ingredient_rows(raw) -> list[dict]:
    """A trimmed pick's ingredients are plain names; the gate and the
    recipe store read rows. Rows already given (a full pick) pass through."""
    rows = []
    for ing in raw or []:
        if isinstance(ing, dict):
            rows.append(ing)
        elif isinstance(ing, str) and ing.strip():
            rows.append({"item": ing.strip()})
    return rows


def _option_view(index: int, pick: dict) -> dict:
    """What the sheet shows for one pick — never the whole recipe."""
    return {
        "index": index,
        "meal": pick["meal_name"],
        "reason": (pick.get("reason") or "").strip(),
        "minutes": _minutes(pick),
    }


def _saved_dish(name: str) -> dict | None:
    """The household's own recipe of that name, or None. Imported here,
    not at the top: the import block above is kept as it is on main so
    hotfix-model-list-shapes (which adds a line to it) merges cleanly."""
    from . import recipes as _recipes
    return _recipes.existing_recipe_named(name)


def _gate(pick: dict, entry: dict) -> str | None:
    """swap_in_place.pick_gate, with one rule in front of it: a pick that
    names a dish the household already has is judged on THAT recipe's
    full list, never on the short list the model sent with the name.
    The short list is six main items; the saved recipe is what will
    actually be cooked (apply_pick saves nothing for a known name), and
    the verifier of 2026-09-21 reproduced a saved bake with peanuts
    offered — and planned — to a peanut-allergic house because the
    model's four names left the peanuts out. pick_gate's own fallback to
    the saved list only fires on an EMPTY list, which a trimmed pick
    never has, so the swap is made here."""
    from . import recipes as _recipes
    if _saved_dish(pick.get("meal_name") or ""):
        pick = dict(pick, ingredients=_recipes.saved_ingredients(pick["meal_name"]))
    return _swap.pick_gate(pick, entry)


def _gated(entry: dict, raw: list, avoid: list[str]) -> list[dict]:
    """The picks the household may actually have, in the order the model
    gave them, at most OPTION_COUNT, none repeating `avoid` or each other."""
    out: list[dict] = []
    seen = {a.strip().lower() for a in avoid}
    for pick in raw or []:
        if not isinstance(pick, dict):
            continue
        name = (pick.get("meal_name") or "").strip()
        if not name or name.lower() in seen:
            continue
        pick = dict(pick, meal_name=name, ingredients=_as_ingredient_rows(pick.get("ingredients")))
        why = _gate(pick, entry)
        if why:
            logger.warning("swap_options dropped %r: %s", name, why)
            continue
        seen.add(name.lower())
        out.append(pick)
        if len(out) >= OPTION_COUNT:
            break
    return out


def swap_options(weekly_plan_id: int, entry_id: int, avoid: list[str] | None = None, asker=None) -> dict:
    """
    Three other dishes for this slot, gated, nothing written. `asker` is
    the model call, injectable so tests never touch the real API.

    Returns {entry_id, meal, options: [{index, meal, reason, minutes}],
    options_unavailable}. `options_unavailable` tells a failed call apart
    from a model that found nothing safe — both leave `options` empty.
    """
    entry = _swap._entry(weekly_plan_id, entry_id)
    if entry["slot_state"] != "planned" or not entry["meal"]:
        raise ValueError("There's no meal on that slot to swap.")
    key = (household_id(), entry_id)
    cached = _OPTIONS_CACHE.get(key)
    if cached and cached["meal"] == entry["meal"] and time.time() - cached["at"] < _OPTIONS_TTL:
        return {"entry_id": entry_id, "meal": entry["meal"],
                "options": [_option_view(i, p) for i, p in enumerate(cached["options"])],
                "options_unavailable": cached["unavailable"]}
    tried = _swap._dedup([entry["meal"]] + list(avoid or []))
    context = _swap.build_swap_context(weekly_plan_id, entry, tried)
    ask = asker or _ask_options
    unavailable = False
    started = time.perf_counter()
    try:
        raw = ask(context)
    except Exception:
        logger.exception("Asking for swap options failed; the sheet will offer the chat only")
        raw = []
        unavailable = True
    options = _gated(entry, raw, tried)
    # Next to agent's own per-call line ("llm call swap_options took …"):
    # the whole ask, gate included, and how many picks survived it.
    logger.info("swap_options for entry %s: %d of %d picks kept in %.2fs",
                entry_id, len(options), len(raw or []), time.perf_counter() - started)
    _OPTIONS_CACHE[key] = {"at": time.time(), "meal": entry["meal"], "options": options,
                           "unavailable": unavailable, "context": context}
    return {"entry_id": entry_id, "meal": entry["meal"],
            "options": [_option_view(i, p) for i, p in enumerate(options)],
            "options_unavailable": unavailable}


def forget_options(entry_id: int) -> None:
    _OPTIONS_CACHE.pop((household_id(), entry_id), None)


def needs_write_out(pick: dict) -> bool:
    """A trimmed pick — no steps, and no saved recipe by that name to cook
    from — has to be written out before it can be planned. A pick that
    carries its steps, or reuses a dish the household already has, is
    applied as it is (apply_pick saves nothing for a name it knows)."""
    if _steps(pick):
        return False
    return not _saved_dish(pick["meal_name"])


def _steps(recipe: dict) -> list[str]:
    return [s for s in (recipe.get("instructions") or []) if (s or "").strip()]


def write_out_is_complete(full: dict | None) -> bool:
    """A written-out dish is planned only whole: a usable ingredient list
    with a quantity on every line, and at least one step. Anything less
    would land on the week as a recipe the Cooker can't cook from and the
    grocery list can't shop for — refused instead, nothing written."""
    rows = _swap._clean_ingredients((full or {}).get("ingredients"))
    if not rows or any(not r["qty"] for r in rows):
        return False
    return bool(_steps(full or {}))


# Calm, and it says what's true: the picks are still there to tap again.
WRITE_OUT_TROUBLE = "I couldn’t write that one up just now — nothing changed. Tap it again in a moment."


def choose_swap_option(weekly_plan_id: int, entry_id: int, index: int, writer=None) -> dict:
    """
    Put the chosen option on the slot. The pick is the one the sheet was
    shown (from the sitting's cache), re-gated at the moment of writing —
    the table or the week may have changed since — written out as a full
    recipe if it arrived trimmed (`writer`, injectable like `asker`; the
    full list is gated again, since a quantity-level recipe can name what
    a six-item list did not), and applied through swap_in_place.apply_pick.
    Returns the swap's own shape (`status` 'swapped' with the refreshed
    day) or 'refused' with a plain sentence.
    """
    entry = _swap._entry(weekly_plan_id, entry_id)
    if entry["slot_state"] != "planned" or not entry["meal"]:
        raise ValueError("There's no meal on that slot to swap.")
    cached = _OPTIONS_CACHE.get((household_id(), entry_id))
    if not cached or cached["meal"] != entry["meal"]:
        raise ValueError("Those picks aren't on offer any more — tap Swap again.")
    # An explicit range, not Python indexing: -1 is not "the last pick",
    # it is not one of the picks.
    try:
        index = int(index)
    except (ValueError, TypeError):
        raise ValueError("That isn't one of the picks.")
    if not 0 <= index < len(cached["options"]):
        raise ValueError("That isn't one of the picks.")
    pick = dict(cached["options"][index])
    if _weekly_plan.night_has_gone(entry["date"]):
        return {"status": "refused", "message": _weekly_plan.NIGHT_GONE}
    why = _gate(pick, entry)
    if why:
        return {"status": "refused", "message": f"I left it as it was — {pick['meal_name']} {why}."}
    if needs_write_out(pick):
        context = cached.get("context") or _swap.build_swap_context(
            weekly_plan_id, entry, _swap._dedup([entry["meal"]]))
        try:
            full = (writer or _write_out)(context, pick)
        except Exception:
            logger.exception("Writing out the chosen swap pick failed; nothing changed")
            return {"status": "refused", "message": WRITE_OUT_TROUBLE}
        if not write_out_is_complete(full):
            logger.warning("swap_option_writeout came back incomplete for %r (ingredients=%d, steps=%d)",
                           pick["meal_name"], len((full or {}).get("ingredients") or []),
                           len(_steps(full or {})))
            return {"status": "refused", "message": WRITE_OUT_TROUBLE}
        # The name and the line are the ones the household tapped and read;
        # the recipe underneath them is the model's. (apply_pick still holds
        # the name to its own ingredients — honest_meal_name.)
        pick = dict(full, meal_name=pick["meal_name"], reason=pick.get("reason") or full.get("reason") or "")
        why = _swap.pick_gate(pick, entry)
        if why:
            return {"status": "refused", "message": f"I left it as it was — {pick['meal_name']} {why}."}
    out = _swap.apply_pick(weekly_plan_id, entry, pick)
    out["status"] = "swapped"
    forget_options(entry_id)
    return out
