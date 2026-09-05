"""
Every meal is a full plate — the rule, and the repair when it isn't.

Emily, 2026-09-05, deciding a question this codebase had deliberately left
open (see plan_quality's `full_plate`, which until now only WARNED):

  - A plate is **protein + vegetable**, plus **carb** unless the household's
    eating_style reads as low-carb/keto. Keto's full plate is protein +
    vegetable, full stop — no "optional add-ons," that is a separate later
    ticket and is deliberately not built here.
  - It applies to ALL four slots, not dinner alone. Breakfast and snack get
    a LIGHTER rule (see LIGHT_SLOTS below) because a breakfast held to
    protein+vegetable+carb is not a breakfast, it's a dinner at 7am.
  - When a plate is short, the week is NOT regenerated. One small model call
    per short meal returns one or two SIDES, and they are ATTACHED to the
    same entry. See complete_plate.
  - A one-pot dish whose own food_groups already cover the rule is
    complete: nothing gets bolted onto it.

WHERE THE SIDES LIVE, and why not on the recipe. A side attaches to
`meal_plan_entries.sides_json` — the ENTRY, not the recipe. A recipe is
shared: the same chili is reused across weeks and households' own saved
lists, and rewriting its ingredients_json to bolt a salad onto it would
change every future plan that reuses it and would be impossible to tell
apart later from the recipe's own ingredients. The entry is the one place
that means "this dish, on this night, for this household." The other
candidate was derived_from_json, which is already a free-form blob and
needs no migration; it was rejected because derived_from is a record of
what CAUSED a slot (tags, constraints, links_to) and is read as provenance
by four separate modules, whereas sides are content that has to reach the
grocery list and the Cooker. One narrow column, one meaning.

sides_json shape (a list; empty for the overwhelming majority of entries):
    [
      {
        "name": "Green salad with lemon",
        "covers": ["vegetable"],          # subset of protein/carb/vegetable
        "ingredients": [{"item": "Romaine", "qty": "1 head",
                         "category": "produce"}, ...],
        "instructions": ["Tear the romaine.", "Dress and toss."],
        "minutes": 5,
      },
      ...
    ]

Readers, all of which treat a side as part of the entry rather than as a
second meal:
  - weekly_plan.approve_weekly_plan / preview_plan_grocery_impact — the
    side's ingredients go on the shopping list with the dish's, under the
    SAME meal_plan_entry_id, so removing the meal removes them again
    (_reverse_meal_grocery_contributions is keyed by entry).
  - cooker.get_cooker_view — the side's steps follow the main's, prefixed
    "Alongside", and its ingredients follow the main's.
  - weekly_plan.get_week_menu / get_weekly_plan — the "with <side>" line.

WHAT THIS MODULE DOES NOT DO. It never guesses at food groups. An entry
whose food_groups_json is empty records no answer either way, and this
module skips it and says so in the log rather than inventing a plate to
repair — the same stance plan_quality's full_plate rule already takes.
"""
from __future__ import annotations

import json
import logging

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger(__name__)

# The whole food-group vocabulary. meal_plan_entries.food_groups_json is a
# subset of exactly these three (see schema.sql).
ALL_GROUPS = ("protein", "vegetable", "carb")

# Breakfast and snack are held to the lighter rule below. Lunch is not: a
# packed lunch is a real plate and Emily said so.
LIGHT_SLOTS = ("breakfast", "snack")

# THE LIGHT RULE, stated exactly, because "never just a fruit" needs to be
# a rule and not a vibe: a breakfast or snack must cover AT LEAST TWO of
# the three groups. Which two is not prescribed — yogurt and berries
# (protein + vegetable, since fruit is recorded under `vegetable` in this
# vocabulary) passes, and so does toast and eggs (carb + protein).
#
# That single line is the whole of "never just a fruit, never just a
# granola bar," and it is honest about why: both of those carry AT MOST ONE
# group, so a two-group floor already excludes them without this module
# having to keep a list of foods it disapproves of. Anything that really
# does cover two groups is a real small meal, and the app has no business
# adding a side to it.
LIGHT_SLOT_MIN_GROUPS = 2

# A household whose eating_style reads as low-carb doesn't get a carb
# bolted onto its dinner. Written fresh for this ticket -- nothing in the
# codebase classified eating_style before; it was passed to the model as
# free text and the model did the interpreting. This is a deliberately
# small keyword rule rather than a model call: it runs on every entry of
# every generated week, the cost of being wrong is one unwanted side (which
# the household can delete), and a phrase list anyone can read and correct
# beats a judgement nobody can see. Substring matching on a lowercased,
# punctuation-normalized style string, so "high-protein, low carb" and
# "Keto (mostly)" both land.
_LOW_CARB_PHRASES = (
    "keto",
    "ketogenic",
    "low carb",
    "lowcarb",
    "no carb",
    "zero carb",
    "carnivore",
    "atkins",
    "banting",
)


def is_low_carb(eating_style: str | None) -> bool:
    """
    Does this household's own eating_style read as low-carb/keto?

    Deliberately literal. It answers only "did they say one of these
    words", not "would a nutritionist call this low-carb" — a household
    eating 40g of carbs a day and describing it as "clean eating" reads as
    False here, and the honest consequence is that they get a carb on the
    plate, which is the app's default and not a harm. The failure mode
    worth avoiding is the other one: putting rice next to the steak of a
    household that typed "keto" in the box.
    """
    style = (eating_style or "").lower().replace("-", " ").replace("_", " ")
    style = " ".join(style.split())  # collapse whitespace so "low  carb" matches
    return any(phrase in style for phrase in _LOW_CARB_PHRASES)


def plate_rule(eating_style: str | None) -> tuple[str, ...]:
    """
    The groups a full plate has to cover for this household, in the order
    they should be filled if more than one is missing.

    protein + vegetable + carb ordinarily; protein + vegetable for a
    low-carb/keto household (Emily, 2026-09-05, decision 7a). Returned as a
    tuple rather than a set so a caller filling gaps does so in a stable,
    sensible order — the protein first, since a plate missing its protein
    is missing more than a plate missing its rice.
    """
    if is_low_carb(eating_style):
        return ("protein", "vegetable")
    return ("protein", "vegetable", "carb")


def _groups_of(entry: dict) -> list[str]:
    groups = entry.get("food_groups") or []
    return [g for g in groups if g in ALL_GROUPS]


def missing_groups(entry: dict, rule: tuple[str, ...]) -> list[str]:
    """
    What this entry would need to be a full plate — [] when it already is.

    `entry` is a plain dict with at least "slot" and "food_groups" (the
    shape get_weekly_plan's meals already have), so this is unit-testable
    without a database.

    An entry with NO recorded food groups returns [] here, the same as a
    complete one. That is not the module shrugging: it genuinely doesn't
    know, and the caller that cares about the difference asks
    `has_food_groups` and logs it. Guessing would be worse than either.
    """
    groups = _groups_of(entry)
    if not groups:
        return []
    if entry.get("slot") in LIGHT_SLOTS:
        shortfall = LIGHT_SLOT_MIN_GROUPS - len(groups)
        if shortfall <= 0:
            return []
        # Fill toward two groups in the rule's own order, skipping whatever
        # is already there. A low-carb household's breakfast reaches two
        # with protein + vegetable and never gets offered toast.
        return [g for g in rule if g not in groups][:shortfall]
    return [g for g in rule if g not in groups]


def has_food_groups(entry: dict) -> bool:
    """Whether this entry recorded any food groups at all — see missing_groups."""
    return bool(_groups_of(entry))


def is_complete(entry: dict, rule: tuple[str, ...]) -> bool:
    """
    True when this entry is already a full plate under `rule` — including
    the one-pot dish whose own food_groups cover everything, which is
    exactly the case Emily called out (decision 6a): nothing gets bolted
    onto it.

    Also True for an entry with no recorded food groups, for the reason
    missing_groups explains. Pair it with has_food_groups when the
    difference matters.
    """
    return not missing_groups(entry, rule)


# ---------- reading and writing the sides on one entry ----------

def get_sides(entry_id: int) -> list[dict]:
    """The sides attached to one entry; [] for the overwhelming majority."""
    conn = get_conn()
    row = conn.execute(
        "SELECT sides_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    conn.close()
    if not row:
        return []
    try:
        sides = json.loads(row["sides_json"] or "[]")
    except (TypeError, ValueError):
        return []
    return sides if isinstance(sides, list) else []


def side_ingredients(sides: list[dict] | None) -> list[dict]:
    """
    Every ingredient across a list of sides, flattened, in order.

    Shaped exactly like a recipe's own ingredients ({item, qty, category})
    so the grocery path can concatenate the two lists and stay one code
    path — see approve_weekly_plan.
    """
    out = []
    for side in sides or []:
        for ing in side.get("ingredients") or []:
            item = (ing.get("item") or "").strip()
            if not item:
                continue
            out.append({
                "item": item,
                "qty": (ing.get("qty") or "").strip(),
                "category": (ing.get("category") or "other").strip() or "other",
            })
    return out


def sides_label(sides: list[dict] | None) -> str:
    """
    The "with a green salad" fragment the Plan card and Cook hero show
    after the dish name. "" when nothing was attached, which is the signal
    to say nothing rather than to say "no sides."
    """
    names = [(s.get("name") or "").strip() for s in (sides or [])]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return f"with {names[0]}"
    return "with " + ", ".join(names[:-1]) + f" and {names[-1]}"


_VALID_CATEGORIES = ("produce", "dairy", "meat/seafood", "pantry", "frozen", "other")


def _clean_side(raw: dict, allowed_groups: tuple[str, ...] | list[str]) -> dict | None:
    """
    One side as the model sent it, reduced to something safe to store.

    Everything here is defensive on purpose: this is model output going
    straight into a column that the grocery list and the Cooker both read,
    and a malformed side must degrade to "no side" rather than to a broken
    week. Returns None for anything that isn't usable.
    """
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or "").strip()
    if not name:
        return None
    covers = [g for g in (raw.get("covers") or []) if g in allowed_groups]
    ingredients = []
    for ing in raw.get("ingredients") or []:
        if not isinstance(ing, dict):
            continue
        item = (ing.get("item") or "").strip()
        if not item:
            continue
        category = (ing.get("category") or "").strip().lower()
        ingredients.append({
            "item": item,
            "qty": (ing.get("qty") or "").strip(),
            "category": category if category in _VALID_CATEGORIES else "other",
        })
    instructions = [str(s).strip() for s in (raw.get("instructions") or []) if str(s).strip()]
    minutes = raw.get("minutes")
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = None
    if minutes is not None and minutes < 0:
        minutes = None
    # A side with no ingredients buys nothing and cooks nothing; it would
    # show up on the card as a promise the rest of the app can't keep.
    if not ingredients:
        return None
    return {
        "name": name,
        "covers": covers,
        "ingredients": ingredients,
        "instructions": instructions,
        "minutes": minutes,
    }


def attach_sides(entry_id: int, sides: list[dict], groups_covered: list[str]) -> dict:
    """
    Write already-cleaned sides onto an entry and widen its food_groups to
    match. Appends rather than replaces, so completing a plate twice (a
    regeneration, a second pass) adds to what's there instead of quietly
    dropping the first attempt's shopping.

    food_groups is extended only by what the sides actually SAY they cover
    (intersected with what was missing, by the caller) — never by what was
    asked for. An entry claiming a vegetable it didn't get is a lie the
    grocery list can't fix.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT sides_json, food_groups_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No meal plan entry {entry_id} in this household.")
    try:
        existing = json.loads(row["sides_json"] or "[]")
    except (TypeError, ValueError):
        existing = []
    if not isinstance(existing, list):
        existing = []
    try:
        groups = json.loads(row["food_groups_json"] or "[]")
    except (TypeError, ValueError):
        groups = []
    if not isinstance(groups, list):
        groups = []
    for group in groups_covered:
        if group in ALL_GROUPS and group not in groups:
            groups.append(group)
    combined = existing + sides
    conn.execute(
        "UPDATE meal_plan_entries SET sides_json = ?, food_groups_json = ? "
        "WHERE id = ? AND household_id = ?",
        (json.dumps(combined), json.dumps(groups), entry_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"entry_id": entry_id, "sides": combined, "food_groups": groups}


def complete_plate(entry_id: int, context: dict, side_generator=None) -> dict:
    """
    Fill in what one short plate is missing, by attaching a side or two to
    the SAME entry — never by regenerating the meal (Emily, 2026-09-05,
    decision 8a).

    `context` is the small dict the side call is given, assembled by the
    caller because only the caller knows the night:
        {
          "meal": "Sheet-pan chicken thighs",
          "slot": "dinner",
          "date": "2026-09-08",
          "missing": ["vegetable", "carb"],
          "dislikes": ["mushrooms"],
          "dietary_restrictions": ["peanut allergy"],
          "eating_style": "high-protein, low-carb",
          "max_minutes": 20 | None,     # the night's real cap, if it has one
        }

    `side_generator` is the model call, injected so this module never
    imports agent (which imports this package). It defaults, at CALL time
    rather than import time, to agent.generate_sides_llm — which is also
    what lets a test monkeypatch that name and have this pick it up.

    Returns {"entry_id", "attached": [side names], "groups_added": [...],
    "still_missing": [...]}. `still_missing` is not an error: a side call
    that came back with a vegetable but no carb has genuinely improved the
    plate, and the honest record is that one group is still short.
    """
    missing = [g for g in (context.get("missing") or []) if g in ALL_GROUPS]
    if not missing:
        return {"entry_id": entry_id, "attached": [], "groups_added": [], "still_missing": []}

    if side_generator is None:
        # Imported at call time, not import time: agent imports this
        # package, so an import-time reach back the other way would make
        # the cycle real. Same convention as leftovers._resolve.
        from .. import agent as _agent
        side_generator = _agent.generate_sides_llm

    raw_sides = side_generator(context) or []
    cleaned = []
    for raw in raw_sides[:2]:  # one or two sides, never a second dinner
        side = _clean_side(raw, ALL_GROUPS)
        if side is not None:
            cleaned.append(side)
    if not cleaned:
        logger.warning(
            "Plate completion for entry %s (%s) came back with nothing usable; leaving the "
            "meal exactly as generated", entry_id, context.get("meal"),
        )
        return {
            "entry_id": entry_id, "attached": [], "groups_added": [],
            "still_missing": missing,
        }

    covered = []
    for side in cleaned:
        for group in side.get("covers") or []:
            if group in missing and group not in covered:
                covered.append(group)
    attach_sides(entry_id, cleaned, covered)
    return {
        "entry_id": entry_id,
        "attached": [s["name"] for s in cleaned],
        "groups_added": covered,
        "still_missing": [g for g in missing if g not in covered],
    }
