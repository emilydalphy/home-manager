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
    names the steps and lines to change (agent.tweak_library_recipe_llm)
    and apply_edit changes only those. Everything else stays as researched.
  * The edit goes through the allergen gate on the full list. An edit that
    fails, or a failed call, leaves the library recipe as filed: a good
    recipe in the pot beats a pending dish.
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
    r"\b(cayenne|chil[ie]s?|chillies|chilies|jalape[nñ]os?|habaneros?|scotch bonnets?|serranos?|"
    r"sriracha|gochujang|gochugaru|sambal|harissa|hot sauce|hot paprika|red pepper flakes|"
    r"crushed red pepper|chipotles?|wasabi|vindaloo)\b",
    re.I,
)
_NOT_HEAT = re.compile(r"\b(sweet chil[ie]|serrano ham)\b", re.I)

# Equipment a method can't do without, as (what to call it, how a step says
# it, the words in a kit that mean they own it). Cast iron is left out: any
# heavy pan does the job.
_EQUIPMENT = (
    ("slow cooker", r"\bslow cooker\b|\bcrock.?pot\b", ("slow cooker", "crock pot", "crockpot")),
    ("Instant Pot", r"\binstant pot\b|\bpressure cooker\b", ("instant pot", "pressure cooker")),
    ("air fryer", r"\bair fryer\b", ("air fryer",)),
    ("stand mixer", r"\bstand mixer\b", ("stand mixer",)),
    ("blender", r"\bblender\b", ("blender",)),
    ("food processor", r"\bfood processor\b", ("food processor",)),
    ("grill", r"\b(on|over|onto|to) the grill\b|\b(a|the|your) grill\b|\bgrill grates?\b|\bcharcoal\b",
     ("grill",)),
)

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
    if not kit:
        return []
    owned = " | ".join(str(k).replace("_", " ").lower() for k in kit)
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


def apply_edit(entry: dict, edit: dict | None) -> dict | None:
    """
    The model's edit laid onto the library recipe: only the steps and lines
    it named change. {"ingredients", "instructions", "touched"} — `touched`
    is the lines the model wrote (their amounts keep the usual check) — or
    None when there is nothing usable: no change, a step or line that isn't
    in the recipe, or an empty result. Never half an edit.
    """
    if not isinstance(edit, dict):
        return None
    steps = [str(s) for s in entry.get("instructions") or []]
    lines = [dict(i) for i in entry.get("ingredients") or [] if isinstance(i, dict)]
    index = {str(i.get("item") or "").strip().lower(): n for n, i in enumerate(lines)}
    touched: set[str] = set()
    changed = False
    for e in edit.get("step_edits") or []:
        text = str((e or {}).get("text") or "").strip()
        try:
            at = int((e or {}).get("step")) - 1
        except (TypeError, ValueError):
            return None
        if not text or not 0 <= at < len(steps):
            return None
        steps[at] = text
        changed = True
    for e in edit.get("change_ingredients") or []:
        key = str((e or {}).get("item") or "").strip().lower()
        qty = str((e or {}).get("qty") or "").strip()
        if key not in index or not qty:
            return None
        line = lines[index[key]]
        line["qty"] = qty
        line.pop("cook_qty", None)
        touched.add(key)
        changed = True
    drop = set()
    for name in edit.get("remove_ingredients") or []:
        key = str(name or "").strip().lower()
        if key not in index:
            return None
        drop.add(index[key])
        changed = True
    lines = [line for n, line in enumerate(lines) if n not in drop]
    for e in edit.get("add_ingredients") or []:
        item = str((e or {}).get("item") or "").strip()
        if not item:
            return None
        lines.append({k: str(e[k]) for k in ("item", "qty", "category") if e.get(k)})
        touched.add(item.lower())
        changed = True
    if not changed or not lines or not steps:
        return None
    return {"ingredients": lines, "instructions": steps, "touched": touched}


def ready_variant(entry: dict, plain: str, avoidances: list[dict], clash, why: dict) -> dict | None:
    """A variant another household's tweak already made for children, when
    heat is the only thing wrong: reused with no model call."""
    if set(why) != {"heat_for_a_child"}:
        return None
    return _recipe_library.children_variant(plain, clash)


def fit(entry: dict, plain: str, avoidances: list[dict], shared: dict | None, *, clash, write_edit) -> dict:
    """
    The recipe this household gets: {"entry", "changes", "touched", "why"}.
    `why` is set only when the model made a new edit, so the caller files it
    back. `clash(entry)` is the allergen gate on an entry and `write_edit` the
    model call. Never raises: anything wrong leaves the library recipe as filed.
    """
    as_filed = {"entry": entry, "changes": [], "touched": set(), "why": {}}
    if not enabled():
        return as_filed
    try:
        why = reasons(entry, shared)
        if not why:
            return as_filed
        reused = ready_variant(entry, plain, avoidances, clash, why)
        if reused:
            return {"entry": reused, "changes": [REUSED_CHANGE], "touched": set(), "why": {}}
        edit = write_edit(entry, why, shared or {})
        changes = [str(c).strip() for c in ((edit or {}).get("household_changes") or []) if str(c).strip()]
        applied = apply_edit(entry, edit)
        if not applied or not changes:
            return as_filed
        tweaked = {**entry, "ingredients": applied["ingredients"], "instructions": applied["instructions"]}
        if clash(tweaked):
            logger.warning("The tweak of %r brought in a must-avoid; keeping the library recipe", plain)
            return as_filed
        return {"entry": tweaked, "changes": changes, "touched": applied["touched"], "why": why}
    except Exception:
        logger.exception("Tweaking library recipe %r failed; keeping it as filed", plain)
        return as_filed


def filing_spec(why: dict) -> dict:
    """What the tweak was shaped by, for recipe_library.file_new_recipe."""
    spec: dict = {}
    if why.get("heat_for_a_child"):
        spec["children_at_table"] = True
    if why.get("equipment_they_lack"):
        spec["kitchen_kit"] = True
    return spec
