"""
The plate, part by part: change the protein of a dish without losing the
dish, and know what the plate is short of.

Emily, 2026-09-13 ("Shaping the Draft", Flows A and B): "there's been a
few tests now where I wanted to swap just the type of meat that was used
in a recipe", and "add a carb, like potatoes, to make it more filling".
Until now the only ways to change a meal were "Swap · I'll pick" (a
different dish altogether) and a sentence to the chat. This module is the
part in between:

  * `parts_of_plate(...)` — the plate as the card shows it: protein, then
    vegetable and carb as the household's plate rule asks for them (see
    plates.plate_rule: keto has no carb; breakfast and snack are lighter).
    Each part is either named (the dish's own protein; a side by name), in
    the dish (covered by its food_groups, no separate name), or missing —
    which the card draws as a dashed "+ Add a carb". Read from what the
    entry already holds; nothing new to enter.
  * `part_options(...)` — the four proteins that would work IN THIS DISH,
    written for the dish, not from a list: a burger gets ground meats and
    a bean patty, never chicken thighs; a pan-fried chicken gets the CUT
    (thighs vs breasts) and what it changes. One small forced tool call
    on the cheapest route, cached for the sitting so reopening the sheet
    is instant. The household's hard exclusions and dislikes are handed in
    so nothing offered is something they can't have.
  * `change_part(...)` — the household picked one (or typed one): one
    small call rewrites the recipe around the new protein — the same dish,
    a name that says what changed ("Beef burgers"), ingredients, steps,
    main_protein, minutes — then the pick goes through swap_in_place's
    own two gates and apply (pick_gate, apply_pick), so it lands exactly
    as a swap does: a saved recipe, the plan updated, the grocery list in
    step on an approved week, and "Undo" through undo_meal_swap.

Vegetables and carbs are not model calls at all: adding one is
plates.add_component (the "Add something" sheet, 2026-09-13), which knows
what goes with this dish; the card's dashed chip and the plate rows open
that same sheet. Only the protein needs the recipe rewritten around it —
which is why only the protein comes here.
"""
from __future__ import annotations

import json
import logging
import time

from ._shared import household_id
from . import plates as _plates

logger = logging.getLogger("home_manager")

# weekly_plan imports this module for plate_parts() (pure, plates-only);
# everything below that touches the plan goes through swap_in_place, which
# reaches weekly_plan itself — so those are imported where they are used,
# never at the top, or the two modules would import each other.


def _swap_mod():
    from . import swap_in_place
    return swap_in_place


def _memory_mod():
    from . import memory
    return memory

ROLES = ("protein", "vegetable", "carb")
# The word on the chip for each role — the household's words, not the
# schema's ("Veg", never "vegetable").
ROLE_WORDS = {"protein": "Protein", "vegetable": "Veg", "carb": "Carb"}
MAX_OPTIONS = 4
_OPTIONS_TTL = 30 * 60
# (household_id, entry_id) -> {"at": t, "options": [...], "meal": name}
_OPTIONS_CACHE: dict[tuple[int, int], dict] = {}


# ---------- the plate as the card shows it ----------

def parts_of_plate(slot: str, food_groups: list[str], main_protein: str | None,
                sides: list[dict] | None, eating_style: str | None) -> list[dict]:
    """
    The plate for one entry, in the order the card shows it. Pure; the
    week menu calls it with what its rows already carry.

    Each part: {role, word, name, source, missing}
      source 'dish'  — the dish covers it itself (name is the protein's
                       own word, or None for a veg/carb with no name)
      source 'side'  — a side attached to the entry covers it (name is
                       the side's)
      missing True   — the rule asks for it and nothing covers it
    """
    rule = _plates.plate_rule(eating_style)
    if slot in _plates.LIGHT_SLOTS:
        # The lighter rule (plates.missing_groups): a breakfast held to
        # protein + veg + carb is a dinner at 7am.
        wanted = [r for r in rule if r == "protein"]
    else:
        wanted = list(rule)
    groups = set(food_groups or [])
    # A dish with no food groups recorded at all (an older recipe, a
    # freeform line) is UNKNOWN, not short: "Roast Chicken · + Add a
    # protein" is the card guessing wrong out loud. Only a dish that
    # says what it covers can be said to be missing something — the
    # same line the plate pass draws (plates.has_food_groups).
    known = bool(groups)
    covered_by_side: dict[str, str] = {}
    for side in sides or []:
        for g in side.get("covers") or []:
            covered_by_side.setdefault(g, side.get("name") or "")
    parts = []
    for role in ROLES:
        if role not in wanted:
            continue
        if role == "protein":
            # The protein is never "missing": every dinner has one to
            # change, named when the recipe says (main_protein), plain
            # "Protein" when it doesn't.
            name = (main_protein or "").strip()
            name = name[:1].upper() + name[1:] if name else None
            if not name and role in covered_by_side:
                parts.append({"role": role, "word": ROLE_WORDS[role], "name": covered_by_side[role], "source": "side", "missing": False})
            else:
                parts.append({"role": role, "word": ROLE_WORDS[role], "name": name, "source": "dish", "missing": False})
            continue
        if role in covered_by_side:
            parts.append({"role": role, "word": ROLE_WORDS[role], "name": covered_by_side[role], "source": "side", "missing": False})
        elif role in groups:
            parts.append({"role": role, "word": ROLE_WORDS[role], "name": None, "source": "dish", "missing": False})
        elif known:
            parts.append({"role": role, "word": ROLE_WORDS[role], "name": None, "source": None, "missing": True})
    return parts


# ---------- the options ----------

OPTIONS_TOOL = {
    "name": "submit_protein_options",
    "description": "The proteins that would work in this exact dish, as the household would name them at the counter.",
    "input_schema": {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The protein at the level a shopper buys it: 'Ground pork', 'Chicken thighs', 'Chicken breasts', 'Salmon fillets', 'Halloumi', 'Black beans'. Never a dish name."},
                        "note": {"type": "string", "description": "Two to five words on what changes, or nothing: 'leaner, 5 min less', 'richer', 'same pan, same time', 'vegetarian'. No exclamation marks."},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["options"],
    },
}

OPTIONS_INSTRUCTIONS = """A household wants to change the PROTEIN in one dish they have already planned, and keep \
the dish. Offer the four proteins that would genuinely work in THIS dish, cooked THIS way — not a \
generic list.

Rules:
- Name the protein at the level it is bought and at the cut the method needs. A burger or a \
meatball dish gets ground meats (ground beef, ground pork, ground chicken) and a bean or lentil \
patty — never 'chicken thighs'. A pan-fry, a roast or a traybake gets the cut: 'Chicken breasts' \
and 'Chicken thighs' are two different offers, and each carries what it changes ('leaner, 5 min \
less'). A curry or a stew gets what braises. A stir-fry gets what slices thin.
- Never offer the protein the dish already has, and never offer anything in must_not_contain or \
dislikes. If a taste verdict says someone at the table avoids it, leave it out.
- One of the four may be vegetarian when it genuinely suits the dish (halloumi, tofu, beans, \
lentils); say 'vegetarian' in its note.
- Notes are two to five plain words about what changes for the cook — time, richness, the pan — or \
empty. No selling, no exclamation marks.
- Exactly four options where four fit; fewer only if the dish truly allows fewer."""


def _options_context(entry: dict, recipe: dict | None) -> dict:
    _swap = _swap_mod()
    memory = _memory_mod().get_household_memory()
    table = _swap._table_for(entry["date"], entry["slot"])
    context = {
        "dish": entry["meal"],
        "current_protein": (recipe or {}).get("main_protein") or "",
        "ingredients": [
            {"item": i.get("item"), "qty": i.get("qty")}
            for i in ((recipe or {}).get("ingredients") or [])[:25]
        ],
        "method": ((recipe or {}).get("instructions") or [])[:6],
        "slot": entry["slot"],
        "must_not_contain": _swap._hard_exclusions(),
        "dislikes": memory.get("dislikes") or [],
        "eating_style": memory.get("eating_style") or "",
    }
    taste = _swap._taste_lines_for(entry["date"], entry["slot"], table)
    if taste:
        context["taste_verdicts"] = taste
    return context


def _recipe_for(entry: dict) -> dict | None:
    from . import recipes as _recipes
    if not entry.get("recipe_id"):
        return None
    try:
        return _recipes.get_recipe(entry["meal"])
    except Exception:
        return None


def _ask_options(context: dict) -> list[dict]:
    from .. import agent
    client = agent._client()
    response = agent._create_with_retry(
        client,
        label="plate_part_options",
        model=agent.MODEL,
        max_tokens=600,
        tools=[OPTIONS_TOOL],
        tool_choice={"type": "tool", "name": "submit_protein_options"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": OPTIONS_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"The dish (JSON):\n{json.dumps(context, indent=2)}"},
            ],
        }],
        output_config=agent._effort_config("utility"),
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return list((block.input or {}).get("options") or [])
    return []


def _clean_options(raw: list, exclude: str) -> list[dict]:
    out = []
    seen = set()
    for o in raw or []:
        if not isinstance(o, dict):
            continue
        name = (o.get("name") or "").strip()
        # "Ground turkey" is the turkey the dish already has: an option that
        # names the current protein anywhere in it is not a change.
        if not name or name.lower() in seen or (exclude and exclude.lower() in name.lower()):
            continue
        seen.add(name.lower())
        out.append({"name": name[:1].upper() + name[1:], "note": (o.get("note") or "").strip()[:40]})
        if len(out) >= MAX_OPTIONS:
            break
    return out


def part_options(weekly_plan_id: int, entry_id: int, role: str = "protein", asker=None) -> dict:
    """
    What the "Change the protein" sheet offers, for this dish. Cached per
    entry for the sitting: the options don't change until the dish does,
    and the sheet should open instantly the second time. `asker` is the
    model call, injectable for tests.
    """
    if role != "protein":
        raise ValueError("Only the protein is changed here; a vegetable or carb is added with add_component.")
    _swap = _swap_mod()
    entry = _swap._entry(weekly_plan_id, entry_id)
    if entry["slot_state"] != "planned" or not entry["meal"]:
        raise ValueError("There's no meal on that slot to change.")
    key = (household_id(), entry_id)
    cached = _OPTIONS_CACHE.get(key)
    if cached and cached["meal"] == entry["meal"] and time.time() - cached["at"] < _OPTIONS_TTL:
        return {"entry_id": entry_id, "meal": entry["meal"], "role": role,
                "current": cached["current"], "options": cached["options"]}
    recipe = _recipe_for(entry)
    current = ((recipe or {}).get("main_protein") or "").strip()
    context = _options_context(entry, recipe)
    ask = asker or _ask_options
    try:
        raw = ask(context)
    except Exception:
        logger.exception("Asking for protein options failed; the sheet will offer the typed line only")
        raw = []
    options = _clean_options(raw, current)
    _OPTIONS_CACHE[key] = {"at": time.time(), "meal": entry["meal"], "current": current, "options": options}
    return {"entry_id": entry_id, "meal": entry["meal"], "role": role, "current": current, "options": options}


def forget_options(entry_id: int) -> None:
    _OPTIONS_CACHE.pop((household_id(), entry_id), None)


# ---------- the change ----------

VARIANT_TOOL = {
    "name": "submit_variant",
    "description": "The same dish, rewritten around the new protein.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meal_name": {"type": "string", "description": "The dish's name with the protein changed where the name carries it: 'Turkey burgers' → 'Beef burgers'; 'Lemon chicken with green beans' → 'Lemon pork chops with green beans'. Keep everything else of the name."},
            "ingredients": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "qty": {"type": "string"},
                        "category": {"type": "string", "enum": ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]},
                    },
                    "required": ["item"],
                },
            },
            "instructions": {"type": "array", "items": {"type": "string"}},
            "food_groups": {"type": "array", "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]}},
            "cuisine": {"type": "string"},
            "main_protein": {"type": "string"},
            "prep_time_minutes": {"type": "integer"},
            "cook_time_minutes": {"type": "integer"},
            "default_servings": {"type": "integer"},
            "reason": {"type": "string", "description": "ONE short line the card shows: what changed and what it means for the cook. 'Beef instead of turkey. Same recipe, same time.' 'Chicken breasts instead of thighs — five minutes less.' Under twelve words, no exclamation mark."},
        },
        "required": ["meal_name", "ingredients", "instructions", "main_protein", "reason"],
    },
}

VARIANT_INSTRUCTIONS = """A household is changing the PROTEIN in one dish they have already planned. Rewrite the \
recipe around the new protein and change NOTHING else that doesn't have to change.

- Keep the dish: its name (with only the protein word changed), its other ingredients, its \
sauce, its sides, its shape. The household liked this dish; they are changing one part.
- Swap the protein ingredient for the new one at the amount the new one is bought in \
(store-bought units, plain grocery names). Change a step only where the new protein needs it \
(a different cooking time, a different doneness, no thawing).
- Update prep and cook minutes honestly for the new protein.
- Set main_protein to the new protein's plain word ('beef', 'pork', 'chicken', 'vegetarian').
- Never use anything in must_not_contain or dislikes. If the requested protein is one of them, \
still write it — the app checks and refuses; do not substitute something else silently.
- The reason line is for the card: what changed and what it means for the cook, in one plain \
sentence or two short ones."""


def _ask_variant(context: dict) -> dict:
    from .. import agent
    client = agent._client()
    response = agent._create_with_retry(
        client,
        label="plate_part_change",
        model=agent.MODEL,
        max_tokens=2000,
        tools=[VARIANT_TOOL],
        tool_choice={"type": "tool", "name": "submit_variant"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": VARIANT_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"The dish and the change (JSON):\n{json.dumps(context, indent=2)}"},
            ],
        }],
        output_config=agent._effort_config("utility"),
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning("plate_part_change hit max_tokens; the variant may be incomplete")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    return {}


# Calm and plain, paired with its way out, and it names the state of the
# plan (DESIGN_SYSTEM §8) — the same shape as swap_in_place.REFUSAL.
REFUSAL = "I couldn’t make that change — the dish is as it was. Try a different protein, or tell me in the chat."


def change_part(weekly_plan_id: int, entry_id: int, role: str, choice: str, asker=None) -> dict:
    """
    Put the chosen protein into the dish. `choice` is an option's name or
    what the household typed. Returns swap_in_place's own result shape
    (`status` 'changed' with the refreshed day, the new entry, the reason;
    or 'refused' with a plain message and nothing written) so the screen
    handles it exactly as it handles a swap — Undo included.
    """
    if role != "protein":
        raise ValueError("Only the protein is changed here; a vegetable or carb is added with add_component.")
    choice = (choice or "").strip()
    if not choice:
        raise ValueError("Say which protein.")
    if len(choice) > 60:
        raise ValueError("That’s a bit long for a protein — a few words is plenty.")
    _swap = _swap_mod()
    entry = _swap._entry(weekly_plan_id, entry_id)
    if entry["slot_state"] != "planned" or not entry["meal"]:
        raise ValueError("There's no meal on that slot to change.")
    recipe = _recipe_for(entry)
    context = _options_context(entry, recipe)
    context["new_protein"] = choice
    context["serves"] = _swap._table_for(entry["date"], entry["slot"])["serves"]
    ask = asker or _ask_variant
    pick = ask(context) or {}
    name = (pick.get("meal_name") or "").strip()
    if not name or not _swap._clean_ingredients(pick.get("ingredients")):
        logger.warning("plate_part_change came back with no usable dish")
        return {"status": "refused", "message": REFUSAL}
    pick["meal_name"] = name
    if not (pick.get("main_protein") or "").strip():
        pick["main_protein"] = choice.lower()
    why = _swap.pick_gate(pick, entry)
    if why:
        logger.warning("plate_part_change refused %r: %s", name, why)
        return {"status": "refused", "message": f"I left it as it was — {choice} {why}."}
    out = _swap.apply_pick(weekly_plan_id, entry, pick)
    out["status"] = "changed"
    out["role"] = role
    out["choice"] = choice
    forget_options(entry_id)
    return out
