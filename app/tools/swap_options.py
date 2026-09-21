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
# (household_id, entry_id) -> {"at": t, "meal": name, "options": [...], "unavailable": bool}
_OPTIONS_CACHE: dict[tuple[int, int], dict] = {}

OPTIONS_TOOL = {
    "name": "submit_swap_options",
    "description": f"Submit {OPTION_COUNT} different replacement dishes for this slot.",
    "input_schema": {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "description": f"Exactly {OPTION_COUNT} dishes, each different from the others in protein or cuisine, each written out fully enough to cook and shop for.",
                "items": _swap.SWAP_TOOL["input_schema"],
            },
        },
        "required": ["options"],
    },
}

# The one-dish instructions, with the one sentence that changes: three
# picks, not one, and different from each other. Kept as a prefix of the
# swap's own text so the household's constraints are stated identically
# in both calls.
_ONE_DISH = "you are not being asked for options. Pick one dish, and write it out properly enough to cook and to shop for."
_THREE_DISHES = (
    "you are being asked for OPTIONS. Offer exactly three dishes the household can choose between — "
    "different from each other in protein or cuisine, so the choice is a real one — and write each "
    "out properly enough to cook and to shop for."
)
assert _ONE_DISH in _swap.INSTRUCTIONS, "swap_in_place's instructions changed under swap_options"
INSTRUCTIONS = _swap.INSTRUCTIONS.replace(_ONE_DISH, _THREE_DISHES).replace(
    "Call submit_swap with the one dish.", "Call submit_swap_options with the three dishes."
)


def _ask_options(context: dict) -> list[dict]:
    """The single model call — see swap_in_place._pick_replacement for why
    agent is imported lazily and why `utility` is the route."""
    from .. import agent

    client = agent._client()
    response = agent._create_with_retry(
        client,
        label="swap_options",
        model=agent.MODEL,
        max_tokens=5000,
        tools=[OPTIONS_TOOL],
        tool_choice={"type": "tool", "name": "submit_swap_options"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": f"The slot (JSON):\n{json.dumps(context, indent=2)}"},
            ],
        }],
        output_config=agent._effort_config("utility"),
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning("swap_options hit max_tokens; the picks may be incomplete")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return _model_shapes.tool_list(block.input, "options", "swap_options")
    return []


def _minutes(pick: dict) -> int | None:
    total = int(pick.get("prep_time_minutes") or 0) + int(pick.get("cook_time_minutes") or 0)
    return total or None


def _option_view(index: int, pick: dict) -> dict:
    """What the sheet shows for one pick — never the whole recipe."""
    return {
        "index": index,
        "meal": pick["meal_name"],
        "reason": (pick.get("reason") or "").strip(),
        "minutes": _minutes(pick),
    }


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
        pick = dict(pick, meal_name=name)
        why = _swap.pick_gate(pick, entry)
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
    try:
        raw = ask(context)
    except Exception:
        logger.exception("Asking for swap options failed; the sheet will offer the chat only")
        raw = []
        unavailable = True
    options = _gated(entry, raw, tried)
    _OPTIONS_CACHE[key] = {"at": time.time(), "meal": entry["meal"], "options": options,
                           "unavailable": unavailable}
    return {"entry_id": entry_id, "meal": entry["meal"],
            "options": [_option_view(i, p) for i, p in enumerate(options)],
            "options_unavailable": unavailable}


def forget_options(entry_id: int) -> None:
    _OPTIONS_CACHE.pop((household_id(), entry_id), None)


def choose_swap_option(weekly_plan_id: int, entry_id: int, index: int) -> dict:
    """
    Put the chosen option on the slot. The pick is the one the sheet was
    shown (from the sitting's cache), re-gated at the moment of writing —
    the table or the week may have changed since — and applied through
    swap_in_place.apply_pick. Returns the swap's own shape (`status`
    'swapped' with the refreshed day) or 'refused' with a plain sentence.
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
    why = _swap.pick_gate(pick, entry)
    if why:
        return {"status": "refused", "message": f"I left it as it was — {pick['meal_name']} {why}."}
    out = _swap.apply_pick(weekly_plan_id, entry, pick)
    out["status"] = "swapped"
    forget_options(entry_id)
    return out
