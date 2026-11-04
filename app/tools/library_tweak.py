"""
Library first, then a light tweak (Emily, 2026-11-04: "the recipes first
pulled from the library, then tweaked slightly by the AI if needed just to
meet the users requirements").

Library first (2026-11-03) copied a library recipe exactly as filed, so a
household with a child could get a hot curry, and one with a listed kit a
method for a machine it doesn't own. A full rewrite for every such household
would give the savings back, so:

  * THE CHECK IS CODE AND COSTS NOTHING (reasons): a hot ingredient with a
    child at the table; a step needing equipment missing from a kit the
    household listed. Most recipes pass and no model is called.
  * A recipe that doesn't pass gets A SMALL EDIT, not a rewrite: the model
    rewrites named STEPS (agent.tweak_library_recipe_llm), never the list.
    The heat stays on the shopping list because the adults still add it,
    the researched amounts stay as they are, and no new food can come in.
  * THE EDIT IS CHECKED, NOT TRUSTED (review, 2026-11-04). The gate reads
    a list, so changed steps are read as list lines too: a step saying
    "adults add crushed peanuts" is a clash. No step may still name the heat
    unless it was edited, and the edit has to move it somewhere; no step
    may still need the missing machine. Anything wrong, or a failed call,
    leaves the library recipe as filed.
  * A tweak for children is filed back as a variant, so the next household
    with children reuses it with no model call (ready_variant).

What it does not touch: allergies and diets. Those stay with the allergen
gate and library_first.diet_blocks_the_library, which refuse a recipe
outright; this module only ever runs on a recipe already allowed.

LIBRARY_TWEAK=off copies every recipe as filed.
"""
from __future__ import annotations

import logging
import os
import re

from . import recipe_library as _recipe_library

logger = logging.getLogger("home_manager")

# Hot ingredients, read from the ingredient list only: a step saying "hot
# pan" is not one. A false match costs one small call that may change nothing.
_HEAT = re.compile(
    r"\b(cayenne|chill?(i|y|ie)e?s?|jalape[nñ]os?|habaneros?|scotch bonnets?|serranos?|"
    r"sriracha|gochujang|gochugaru|sambal|harissa|hot sauce|hot paprika|red pepper flakes|"
    r"crushed red pepper|chipotles?|wasabi|vindaloo|berbere|aleppo|pul biber|kimchi|doubanjiang|"
    r"hot (cherry )?peppers?|redhot|hot mustard|shichimi|"
    r"(red|green|massaman|panang|thai) curry paste)\b",
    re.I,
)
_NOT_HEAT = re.compile(r"\b(sweet chill?[ie]|mild chill?[ie]|serrano ham)\b", re.I)

# Equipment a method can't do without, as (what to call it, how a step says
# it, the words in a kit that mean they own it): the machines the kit
# question asks about. Cast iron is left out, any heavy pan does the job, and
# so is a food processor, which the question never offers to tick.
_EQUIPMENT = (
    ("slow cooker", r"\bslow cooker\b|\bcrock.?pot\b", ("slow cooker", "crock pot", "crockpot")),
    ("Instant Pot", r"\binstant pot\b|\bpressure cooker\b", ("instant pot", "pressure cooker")),
    ("air fryer", r"\bair fryer\b", ("air fryer",)),
    ("stand mixer", r"\bstand mixer\b", ("stand mixer",)),
    ("blender", r"\bblender\b", ("blender",)),
    ("grill", r"\b(on|over|onto|to) the grill\b(?! pan)|\b(a|the|your) grill\b(?! pan)|\bgrill grates?\b|"
     r"\bcharcoal\b", ("grill",)),
)
# Kit answers that say nothing about the machines above: ticking only these
# isn't a list of what they own.
_NOT_MACHINES = ("no dishwasher", "cast iron")

# How an edited step says where the heat went.
_MOVED = re.compile(r"\b(at the table|on the table|adults?|grown.?ups?|own (bowl|plate)|on the side|set aside|"
                    r"hold back|leave out|leave off|skip|less|reduce|cut)\b", re.I)

REUSED_CHANGE = "Milder in the pot, with the heat added at the table."


def enabled() -> bool:
    return os.environ.get("LIBRARY_TWEAK", "on").strip().lower() not in ("off", "0", "false", "no")


def _hot_items(entry: dict) -> list[str]:
    out = []
    for ing in entry.get("ingredients") or []:
        item = str((ing or {}).get("item") or "") if isinstance(ing, dict) else ""
        if item and _HEAT.search(item) and not _NOT_HEAT.search(item):
            out.append(item)
    return out


def _missing_kit(entry: dict, kit) -> list[str]:
    """Equipment the steps need that a listed kit doesn't have. No kit
    listed means unknown, so nothing is missing."""
    names = [str(k).replace("_", " ").lower() for k in kit or []]
    if not [n for n in names if n not in _NOT_MACHINES]:
        return []
    owned = " | ".join(names)
    steps = " ".join(str(s) for s in entry.get("instructions") or [])
    return [label for label, said, words in _EQUIPMENT
            if re.search(said, steps, re.I) and not any(w in owned for w in words)]


def reasons(entry: dict, shared: dict | None) -> dict:
    """Why this recipe doesn't suit this household as filed: {} when it does.
    A variant already made for children has had its heat dealt with."""
    shared = shared or {}
    why: dict = {}
    if shared.get("children_at_table") and "children" not in (entry.get("adapted_for") or ()):
        hot = _hot_items(entry)
        if hot:
            why["heat_for_a_child"] = hot
    missing = _missing_kit(entry, shared.get("kitchen_kit"))
    if missing:
        why["equipment_they_lack"] = missing
    return why


def _with_steps(entry: dict, steps: list[str]) -> dict:
    """The entry with these steps read as list lines, for the gate and the
    diet words, which only read a list."""
    return {**entry, "ingredients": list(entry["ingredients"]) + [{"item": s} for s in steps]}


def apply_edit(entry: dict, edit: dict | None) -> tuple[list[str], list[int]] | None:
    """The model's edit laid onto the recipe's steps: (steps, the 0-based
    positions changed), or None when there is nothing usable. Never half an
    edit."""
    if not isinstance(edit, dict):
        return None
    steps = [str(s) for s in entry.get("instructions") or []]
    changed: list[int] = []
    for e in edit.get("step_edits") or []:
        text = str((e or {}).get("text") or "").strip() if isinstance(e, dict) else ""
        try:
            at = int(e.get("step")) - 1
        except (AttributeError, TypeError, ValueError):
            return None
        if not text or not 0 <= at < len(steps):
            return None
        steps[at] = text
        changed.append(at)
    return (steps, changed) if changed else None


def _does_the_job(entry: dict, steps: list[str], changed: list[int], why: dict, kit) -> bool:
    """The edit did what it was asked: a step left alone names no heat, an
    edited one says where the heat went, and no step needs the machine."""
    if why.get("heat_for_a_child"):
        words = {m.group(0).lower() for item in why["heat_for_a_child"] for m in [_HEAT.search(item)] if m}
        for n, step in enumerate(steps):
            if n not in changed and any(w in step.lower() for w in words):
                return False
        if not any(_MOVED.search(steps[n]) for n in changed):
            return False
    tweaked = {**entry, "instructions": steps}
    return not (why.get("equipment_they_lack") and _missing_kit(tweaked, kit))


def fit(entry: dict, plain: str, avoidances: list[dict], shared: dict | None, *, clash, write_edit) -> dict:
    """
    The recipe this household gets: {"entry", "changes", "why"}. `why` is
    set only when the model made a new edit, so the caller files it back.
    `clash(entry)` is the allergen gate and diet words, `write_edit` the
    model call. Never raises: anything wrong leaves the recipe as filed.
    """
    as_filed = {"entry": entry, "changes": [], "why": {}}
    if not enabled():
        return as_filed
    try:
        why = reasons(entry, shared)
        if not why:
            return as_filed
        if set(why) == {"heat_for_a_child"}:
            reused = _recipe_library.children_variant(plain, lambda e: clash(_with_steps(e, e["instructions"])))
            if reused:
                return {"entry": reused, "changes": [REUSED_CHANGE], "why": {}}
        edit = write_edit(entry, why, shared or {})
        changes = [str(c).strip() for c in ((edit or {}).get("household_changes") or []) if str(c).strip()]
        applied = apply_edit(entry, edit)
        if not applied or not changes:
            return as_filed
        steps, changed = applied
        if not _does_the_job(entry, steps, changed, why, (shared or {}).get("kitchen_kit")):
            logger.warning("The tweak of %r didn't do its job; keeping the library recipe", plain)
            return as_filed
        tweaked = {**entry, "instructions": steps}
        if clash(_with_steps(tweaked, [steps[n] for n in changed])):
            logger.warning("The tweak of %r brought in something this household avoids; "
                           "keeping the library recipe", plain)
            return as_filed
        return {"entry": tweaked, "changes": changes, "why": why}
    except Exception:
        logger.exception("Tweaking library recipe %r failed; keeping it as filed", plain)
        return as_filed


def file_back(plain: str, fitted: dict) -> None:
    """A tweak is its own entry, a variant of the dish (THE WRITE-BACK RULE),
    built from the edit itself, not from the household's saved row, which
    another door may have finished first. It keeps the kinds of the entry
    it came from, so an allergy version stays one. Never raises."""
    try:
        e, why = fitted["entry"], fitted["why"]
        kinds = set(e.get("adapted_for") or ())
        if why.get("heat_for_a_child"):
            kinds.add("children")
        if why.get("equipment_they_lack"):
            kinds.add("kitchen_kit")
        _recipe_library.file_entry(
            plain, ingredients=e["ingredients"], instructions=e["instructions"],
            default_servings=e.get("default_servings"), prep_time_minutes=e.get("prep_time_minutes"),
            cook_time_minutes=e.get("cook_time_minutes"), cuisine=e.get("cuisine"),
            main_protein=e.get("main_protein"), advance_prep_notes=e.get("advance_prep_notes") or "",
            advance_prep_step_indices=e.get("advance_prep_step_indices") or [],
            sources=e.get("sources") or [], method_notes=e.get("method_notes") or "",
            fallback_used=bool(e.get("fallback_used")), origin="tweak", variant=True,
            meal=e.get("meal") or "", adapted_for=sorted(kinds),
        )
    except Exception:
        logger.exception("Filing the tweak of %r in the library failed; the recipe is saved", plain)
