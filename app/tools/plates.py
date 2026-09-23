"""
Every meal is a full plate — the rule, and the repair when it isn't.

Emily, 2026-09-05, deciding a question this codebase had deliberately left
open (see plan_quality's `full_plate`, which until now only WARNED):

  - A plate is **protein + vegetable**, plus **carb** unless the household
    is on NO carb (keto, carnivore — carb_level "none"). Keto's full plate
    is protein + vegetable, full stop. A LOW-carb household (Emily,
    2026-09-21: "low carbs doesn't say no carbs") still gets the carb, a
    small one — see carb_level and CARB_GUIDANCE below.
  - It applies to ALL four slots, not dinner alone. Breakfast and snack get
    a LIGHTER rule (see LIGHT_SLOTS below) because a breakfast held to
    protein+vegetable+carb is not a breakfast, it's a dinner at 7am.
  - When a plate is short, the week is NOT regenerated. One small model call
    per short meal returns one or two SIDES, and they are ATTACHED to the
    same entry. See complete_plate.
  - A one-pot dish whose own food_groups already cover the rule is
    complete: nothing gets bolted onto it.
  - "Complete" is not the same claim as "one pot," and the label must not
    say the second when only the first is true (Emily, 2026-09-13: a
    grilled burger-and-charred-vegetables plate that covered every group on
    its own still got called "one-pot, nothing extra"). See
    contradicts_one_pot below — a genuinely complete plate that used a
    grill, or names a component cooked in a second vessel, is complete but
    not one-pot, and the reassurance line has to say nothing rather than
    say the wrong thing.

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
import re

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

# How much carb this household's plate carries — FOUR levels, not two
# (Emily, 2026-09-21: "my preferences say 'low carbs' but it doesn't say
# 'no carbs'. Make sure you can tell the difference between low, a lot,
# and none." Her dinners had come with no carb at all — chicken + corn +
# zucchini; salmon + broccoli — because "low carb" and "keto" were one
# bucket, and that bucket meant no carb).
#
#   none   — keto, carnivore, "no carbs": the plate is protein + vegetable.
#   low    — "low carb", Atkins, "fewer carbs": every lunch and dinner still
#            carries a carb, a SMALL one (half a portion of potato, rice,
#            tortilla, bread).
#   normal — nothing said: a full portion.
#   lots   — "lots of carbs", "high carb", "carb heavy": a generous one.
#
# Read off the household's own words wherever they typed them — the
# eating_style line, a What-we-know fact, the notes — by
# household_carb_level below. The same deliberately literal keyword
# stance is_low_carb always took: it answers "did they say one of these",
# never "would a nutritionist agree", because the cost of being wrong is
# one side on one plate and a phrase list anyone can read and correct
# beats a judgement nobody can see. Existing "low carb" households read
# as `low` from here on with no data edit — a mapping, not a migration.
CARB_LEVELS = ("none", "low", "normal", "lots")

_NONE_CARB = re.compile(
    r"\b(?:keto|ketogenic|carnivore|carb[ -]?free)\b|(?<!not )\b(?:no|zero|without) carbs?\b"
)
_LOW_CARB = re.compile(
    r"\b(?:low[ -]?carbs?|lowcarb|atkins|banting|fewer carbs?|less carbs?|lighter on carbs?|"
    r"light on (?:the )?carbs?|reduced[ -]carbs?|cut(?:ting)? (?:back on |down on )?carbs?|"
    r"easy on (?:the )?carbs?|not (?:too )?many carbs?|go easy on carbs?|carbs? (?:low|down|minimal|light))\b"
)
_LOTS_CARB = re.compile(
    r"\b(?:lots of carbs?|plenty of carbs?|high[ -]carbs?|carb[ -]heavy|carb[ -]loading|"
    r"big on carbs?|extra carbs?|more carbs?|love (?:our |my |the )?carbs?|heavy on (?:the )?carbs?)\b"
)

# What each level asks of a lunch or dinner, in words the prompts and the
# side call share. `portion` is the marker a carb side carries.
CARB_GUIDANCE = {
    "none": "no carb on the plate: protein and vegetable, full stop.",
    "low": "a SMALL carb on every lunch and dinner — half a portion of potato, rice, tortilla or bread "
           "— never none: low carb is not no carb.",
    "normal": "a full carb portion on every lunch and dinner.",
    "lots": "a generous carb portion on every lunch and dinner — they like their carbs.",
}
CARB_PORTION = {"none": "none", "low": "small", "normal": "normal", "lots": "generous"}


def _normalise(text: str | None) -> str:
    # Hyphens fold to spaces, as is_low_carb always did: "no-carb",
    # "zero-carb" and "carb-free" are the same words as with a space
    # (verifier, 2026-09-21: dropping this read "no-carb" as a full plate).
    text = (text or "").lower().replace("-", " ").replace("_", " ")
    return " ".join(text.split())


# A What-we-know fact about the PAST, or about someone else's diet, is not
# the household's eating today: "Vic tried keto in 2023 and hated it", "I
# am not on keto anymore" (verifier, 2026-09-21). A fact carrying any of
# these is left out of the carb reading. Deliberately blunt — a fact
# wrongly skipped costs the default plate, a fact wrongly read costs a
# keto week nobody asked for.
_NOT_NOW = re.compile(
    r"\b(?:tried|used to|anymore|any more|no longer|not on|hated|hates|stopped|quit|gave up|"
    r"back in|last year|years? ago|in (?:19|20)\d\d)\b"
)


def _is_about_now(text: str) -> bool:
    return not _NOT_NOW.search(_normalise(text))


def _level_of(texts: list[str]) -> str | None:
    """The level these words name, or None when they name nothing.
    `none` wins over `low` ("keto, low carb" is keto), `low` over `lots`."""
    texts = [_normalise(t) for t in texts if t]
    if any(_NONE_CARB.search(t) for t in texts):
        return "none"
    if any(_LOW_CARB.search(t) for t in texts):
        return "low"
    if any(_LOTS_CARB.search(t) for t in texts):
        return "lots"
    return None


def carb_level(eating_style: str | None, *more_texts: str | None) -> str:
    """
    One of CARB_LEVELS, from the household's own words. The eating_style
    line is the household's answer to the question and WINS where it says
    anything; `more_texts` (What-we-know facts, the notes) are read only
    when it doesn't, and only the ones about the household's eating now
    (_is_about_now). Nothing said is `normal`. Pure — see
    household_carb_level for the one that reads the row and the facts.
    """
    said = _level_of([eating_style])
    if said:
        return said
    return _level_of([t for t in more_texts if t and _is_about_now(t)]) or "normal"


def household_carb_level(eating_style: str | None = None) -> str:
    """
    The household's carb level, read from everywhere they may have said
    it: eating_style (passed in, or read from meal_preferences), every
    What-we-know fact, and the preferences notes. Never raises — a read
    that fails is `normal`, the plain default.
    """
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT eating_style, notes FROM meal_preferences WHERE household_id = ?", (household_id(),)
        ).fetchone()
        facts = conn.execute(
            "SELECT text FROM facts WHERE household_id = ?", (household_id(),)
        ).fetchall()
        conn.close()
    except Exception:
        logger.exception("Could not read the household's carb level; treating it as normal")
        return carb_level(eating_style)
    style = eating_style if eating_style is not None else ((row["eating_style"] if row else "") or "")
    return carb_level(style, (row["notes"] if row else "") or "", *[f["text"] for f in facts])


def carb_portion(level: str) -> str:
    return CARB_PORTION.get(level, "normal")


def is_low_carb(eating_style: str | None) -> bool:
    """
    Does this household's own eating_style read as low-carb or keto —
    either of the two levels under normal? Kept for its callers; the
    plate rule itself now asks carb_level, because these two levels want
    different plates (see above).
    """
    return carb_level(eating_style) in ("none", "low")


def plate_rule(eating_style: str | None = None, level: str | None = None) -> tuple[str, ...]:
    """
    The groups a full plate has to cover for this household, in the order
    they should be filled if more than one is missing.

    protein + vegetable + carb for everyone except a household on `none`
    (keto, carnivore, "no carbs"), whose plate is protein + vegetable
    (Emily, 2026-09-05, decision 7a — narrowed 2026-09-21 to none alone:
    a low-carb plate still carries a small carb). `level` is one of
    CARB_LEVELS when the caller has it; otherwise it is read off
    eating_style. Returned as a tuple rather than a set so a caller
    filling gaps does so in a stable, sensible order — the protein first.
    """
    if level is None:
        level = carb_level(eating_style)
    if level == "none":
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


# Cooking on a grill (or under a broiler) is never "one-pot," no matter how
# completely the dish's own food_groups cover the plate rule — Emily's
# report (2026-09-13) was exactly this: a grilled turkey burger plate that
# was a genuinely complete plate on its own still isn't the "nothing extra
# to wash" claim the label makes. Word-boundary matched against the
# dish's own name/tags/instructions text, same style as plan_quality's
# _method_words — deliberately generous, since a false "no tag" costs
# nothing and a false "one-pot" is the bug being fixed.
_GRILL_WORDS = {
    "grill", "grills", "grilled", "grilling",
    "bbq", "barbecue", "barbecued", "barbecuing",
    "broil", "broils", "broiled", "broiling", "broiler",
}

# Evidence that the recipe's OWN steps name a second cooking vessel/surface
# used apart from the main — "in a separate pan," "meanwhile, in a skillet"
# — which is the other half of Emily's report: the burger recipe's charred
# vegetables are cooked apart from the patties, in the same recipe. This is
# about a component the recipe itself calls out as separate, not the sides
# the app attaches via attach_sides (weekly_plan.plate_note already routes
# those through sides_label before this is ever consulted, so an
# app-attached side never reaches this check).
#
# Two things deliberately don't count as a second vessel, found by testing
# this against ordinary recipe phrasing rather than just the burger case
# (2026-09-13):
#   - "bowl" is dropped from the vessel list. A bowl holds a marinade,
#     dressing or garnish that's mixed cold and added, not cooked apart —
#     "in a separate bowl, whisk the dressing" is standard phrasing on
#     genuinely one-pot dinners and was stripping their tag for no reason.
#   - A trigger word followed by "preheat" doesn't count, no matter which
#     vessel word comes after. "Meanwhile, preheat the oven" is the first
#     step of nearly every oven/sheet-pan recipe, one-pot ones very much
#     included — it names the SAME oven the dish itself finishes in, not a
#     second one, so on its own it isn't evidence of anything.
_SEPARATE_VESSEL_PATTERN = re.compile(
    r"\b(?:separate|another|second|meanwhile)\b"
    r"(?![^.]{0,25}\bpreheat)"
    r"[^.]{0,25}\b"
    r"(?:pan|pot|skillet|saucepan|sheet\s*pan|tray|dish|grill|oven|broiler)\b"
)


def contradicts_one_pot(
    name: str | None, tags: list[str] | None, instructions: list[str] | None,
) -> bool:
    """
    True when the dish's own name/tags/instructions describe a method that
    "one-pot, nothing extra" would misrepresent: cooked on a grill (or
    under a broiler), or with a step naming a second cooking vessel apart
    from the main. See weekly_plan.plate_note, the only caller — it only
    asks this once a plate is already judged `is_complete`, since a plate
    that's short of the rule gets no reassurance line either way.

    Deliberately a text scan over what the recipe already recorded, not a
    new field to fill in: a dedicated "cooking method" column would need
    every existing recipe backfilled and every future one to remember to
    set it, where the method is already spelled out in the name and steps
    the household reads anyway.

    "Grilled cheese" is dropped before the grill-word scan: a grilled
    cheese sandwich is pan-fried on a griddle, not cooked on an actual
    grill, and it's common enough as an ingredient or topping name (a
    one-pot tomato soup finished with grilled-cheese croutons) that the
    literal word "grilled" there was stripping the tag from genuinely
    one-pot dishes that never touched a grill.
    """
    text = " ".join([name or "", *(tags or []), *(instructions or [])]).lower()
    text = re.sub(r"\bgrilled\s+cheese\b", " ", text)
    words = set(re.findall(r"[a-z]+", text))
    if words & _GRILL_WORDS:
        return True
    return bool(_SEPARATE_VESSEL_PATTERN.search(text))


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
          "carb_portion": "small",      # plates.carb_portion — low carb is not no carb
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
        # A low-carb household's carb side is a half portion, and the
        # plate says so ("Small" — plate_parts.parts_of_plate).
        if context.get("carb_portion") == "small" and "carb" in (side.get("covers") or []):
            side["portion"] = "small"
    attach_sides(entry_id, cleaned, covered)
    return {
        "entry_id": entry_id,
        "attached": [s["name"] for s in cleaned],
        "groups_added": covered,
        "still_missing": [g for g in missing if g not in covered],
    }


# ---------- adding something yourself (Emily, 2026-09-13) ----------
#
# Loop Board "Meal screen: add what's missing ('add potatoes') from the dish
# itself, not via chat". The plate-completion pass above attaches a side the
# APP chose; this is the household choosing one on the meal screen — the
# same side shape, the same column, the same readers (grocery, Cooker, the
# card), so "add potatoes" is one feature wherever it is offered from. The
# draft-stage card ("Draft: add what's missing from the plate (potatoes)
# from the card") calls exactly these three functions and nothing else:
# suggest_additions to fill its picker, add_component on a tap,
# remove_component for its Undo.
#
# The picker's rows are a small written-down catalogue, not a model call:
# the ticket asks for "a starch, a green, a sauce" and one tap for the
# common case, and a list anyone can read and correct beats a judgement
# nobody can see (the same stance is_low_carb takes). Typing something the
# catalogue doesn't have ("cauliflower rice") is the one path that asks the
# model — generate_sides_llm, the call that already writes sides — so the
# amounts and the step come back real rather than invented here.

# ---------- the dish already has a carb (Emily, 2026-09-22) ----------
#
# Cajun Salmon with Green Beans and Sweet Potato Mash recorded food_groups
# ["protein", "vegetable"] — the model-written classification left the
# sweet potato mash off — so the plate rule read the plate as short a
# carb everywhere it looked: the "+Add a carb" chip on the card, the "Add
# a carb" sheet (which then offered Rice, Crusty bread and worse, sauces),
# and the plate-completing pass, which would have bolted a carb SIDE onto
# a dish that already has one. Rather than trust the model's food_groups
# a second time, a small deterministic word list checked against the
# dish's own name and its ingredients' names settles it independent of
# what food_groups_json says — the same stance is_low_carb always took: a
# list anyone can read and correct beats a judgement nobody can see.
#
# Pragmatic, not exhaustive. Deliberately left out: "corn" — the ticket's
# own word list named it, but this app already has a settled, documented
# call the other way (household_carb_level's CARB_LEVELS comment, Emily
# 2026-09-21: "chicken + corn + zucchini" is her own example of a dinner
# that carries NO carb — corn reads here as a vegetable, not a carb — and
# test_low_carb_is_not_no_carb.py holds a whole dinner to that). Adding
# "corn" to this list would have that dish's own carb side stop being
# attached, silently reversing a decision Emily already made. If she wants
# corn read as a carb after all, that's a rule she should make on purpose,
# not a side effect of this fix.
#
# Two guards keep the rest honest:
#   - word-boundary matching only, so "potatoey" never hits;
#   - a short list of phrases that carry a starch WORD without being a
#     carb on the plate — "rice vinegar" (an acid, not rice) and
#     "breadcrumbs"/"bread crumbs"/"breaded" (a coating on something
#     else, not a side of bread) — stripped out before the word list is
#     checked.
_STARCH_WORDS_RE = re.compile(
    r"\b(?:sweet potato|potato(?:es)?|rice|pasta|noodles?|orzo|bread|pita|naan|"
    r"tortillas?|couscous|quinoa|polenta|gnocchi|farro|barley|buns?|wraps?)\b"
)
_STARCH_FALSE_POSITIVES_RE = re.compile(
    r"\brice vinegar\b|\bbread\s*crumbs?\b|\bbreaded\b"
)


def has_starch(*texts: str | None) -> bool:
    """
    Deterministic: do these texts already name a carb — no model call, no
    guess. See the module note above _STARCH_WORDS_RE for what this does
    and doesn't catch.
    """
    text = " ".join(t for t in texts if t).lower()
    if not text:
        return False
    text = _STARCH_FALSE_POSITIVES_RE.sub(" ", text)
    return bool(_STARCH_WORDS_RE.search(text))


def dish_has_carb(meal_name: str | None, ingredients: list[dict] | None) -> bool:
    """
    has_starch applied to a dish's own name and its ingredients' names —
    not sides, not sauces: whether the DISH ITSELF already carries a
    carb, the deterministic backstop for a food_groups_json that missed
    one. Used wherever the plate rule decides carb is missing (the chip,
    the sheet, and the plate-completing pass) — never on its own; a
    dish with no recorded food groups at all stays unknown, exactly as
    missing_groups already treats it, because this settles which group a
    RECORDED plate is missing, not whether the plate was ever read.
    """
    names = [meal_name or ""]
    for ing in ingredients or []:
        if isinstance(ing, dict):
            names.append(ing.get("item") or "")
    return has_starch(*names)


# Every catalogue side is written for this many people. The grocery ingest
# scales it to who is actually eating (eaters ÷ ADDITION_SERVINGS, the
# same arithmetic a recipe's default_servings gets), and the Cooker card
# scales it the same way — see scale_side_ingredients.
ADDITION_SERVINGS = 4

# `hint` is the picker row's one line: how long it adds, or that it cooks
# nothing. `kind` orders the sheet (starch, green, sauce). Quantities are
# written as bought at the store, the way generate_sides_llm is told to
# write them, and salt, pepper and oil are left off — the spice-rack rule
# (spices.py). `match` is the words that mean the dish already has this
# (broccoli beside broccolini is not an addition); see suggest_additions.
ADDITIONS = [
    {
        # "potato" is matched specially, not as a plain substring — see
        # _addition_matches below. Sweet potato mash must not hide this
        # (Emily's Cajun Salmon night, 2026-09-22); a dish with real
        # potatoes still does.
        "key": "roasted-potatoes", "name": "Roasted potatoes", "kind": "starch",
        "covers": ["carb"], "minutes": 25, "hint": "25 minutes in the oven",
        "match": ["potato"],
        "ingredients": [{"item": "Yukon Gold potatoes", "qty": "2 lb", "category": "produce"}],
        "instructions": [
            "Halve the potatoes, toss with oil and salt on a sheet pan.",
            "Roast at 425° until golden, about 25 minutes.",
        ],
    },
    {
        "key": "rice", "name": "Rice", "kind": "starch",
        "covers": ["carb"], "minutes": 20, "hint": "20 minutes on the stove",
        "match": ["rice"],
        "ingredients": [{"item": "Long grain rice", "qty": "2 cups", "category": "pantry"}],
        "instructions": [
            "Rinse the rice, add it to a pot with 3 cups water and a pinch of salt.",
            "Bring to a boil, cover, simmer 15 minutes, then rest 5 off the heat.",
        ],
    },
    {
        "key": "crusty-bread", "name": "Crusty bread", "kind": "starch",
        "covers": ["carb"], "minutes": 2, "hint": "nothing to cook",
        "match": ["bread", "baguette"],
        "ingredients": [{"item": "Baguette", "qty": "1", "category": "other"}],
        "instructions": ["Slice the baguette and put it on the table."],
    },
    {
        "key": "couscous", "name": "Couscous", "kind": "starch",
        "covers": ["carb"], "minutes": 10, "hint": "10 minutes, mostly off the heat",
        "match": ["couscous"],
        "ingredients": [{"item": "Couscous", "qty": "1 box", "category": "pantry"}],
        "instructions": [
            "Bring water to a boil, stir in the couscous, cover and take off the heat.",
            "Rest 5 minutes, then fluff with a fork.",
        ],
    },
    {
        "key": "quinoa", "name": "Quinoa", "kind": "starch",
        "covers": ["carb"], "minutes": 20, "hint": "20 minutes on the stove",
        "match": ["quinoa"],
        "ingredients": [{"item": "Quinoa", "qty": "1 bag", "category": "pantry"}],
        "instructions": [
            "Rinse the quinoa, add it to a pot with 2 cups water and a pinch of salt.",
            "Bring to a boil, cover, simmer 15 minutes, then rest 5 off the heat.",
        ],
    },
    {
        "key": "orzo", "name": "Orzo", "kind": "starch",
        "covers": ["carb"], "minutes": 12, "hint": "12 minutes on the stove",
        "match": ["orzo"],
        "ingredients": [{"item": "Orzo", "qty": "1 box", "category": "pantry"}],
        "instructions": [
            "Boil salted water and add the orzo.",
            "Cook 9-11 minutes until tender, then drain.",
        ],
    },
    {
        "key": "warm-pita", "name": "Warm pita", "kind": "starch",
        "covers": ["carb"], "minutes": 3, "hint": "a few minutes, no real cooking",
        "match": ["pita"],
        "ingredients": [{"item": "Pita bread", "qty": "1 bag", "category": "other"}],
        "instructions": ["Warm the pita in a dry pan or the oven, a minute a side."],
    },
    {
        "key": "tortillas", "name": "Tortillas", "kind": "starch",
        "covers": ["carb"], "minutes": 3, "hint": "a few minutes, no real cooking",
        "match": ["tortilla"],
        "ingredients": [{"item": "Tortillas", "qty": "1 pack", "category": "other"}],
        "instructions": ["Warm the tortillas in a dry pan or the microwave."],
    },
    {
        "key": "green-salad", "name": "Green salad", "kind": "green",
        "covers": ["vegetable"], "minutes": 5, "hint": "5 minutes, no cooking",
        "match": ["salad", "lettuce", "greens", "arugula", "romaine"],
        "ingredients": [
            {"item": "Mixed greens", "qty": "1 bag", "category": "produce"},
            {"item": "Lemon", "qty": "1", "category": "produce"},
        ],
        "instructions": ["Toss the greens with oil, a squeeze of lemon and salt just before serving."],
    },
    {
        "key": "green-beans", "name": "Green beans", "kind": "green",
        "covers": ["vegetable"], "minutes": 10, "hint": "10 minutes on the stove",
        "match": ["green bean", "haricot"],
        "ingredients": [{"item": "Green beans", "qty": "1 lb", "category": "produce"}],
        "instructions": [
            "Trim the beans.",
            "Steam or boil 5 minutes until bright and tender, then toss with butter and salt.",
        ],
    },
    {
        "key": "steamed-broccoli", "name": "Steamed broccoli", "kind": "green",
        "covers": ["vegetable"], "minutes": 10, "hint": "10 minutes on the stove",
        "match": ["broccoli"],
        "ingredients": [{"item": "Broccoli", "qty": "1 head", "category": "produce"}],
        "instructions": [
            "Cut the broccoli into florets.",
            "Steam 5 minutes, then toss with oil, salt and a squeeze of lemon.",
        ],
    },
    {
        "key": "garlic-yogurt-sauce", "name": "Garlic yogurt sauce", "kind": "sauce",
        "covers": [], "minutes": 5, "hint": "5 minutes, no cooking",
        "match": ["yogurt sauce", "tzatziki", "raita"],
        "ingredients": [
            {"item": "Plain Greek yogurt", "qty": "1 cup", "category": "dairy"},
            {"item": "Garlic", "qty": "1 clove", "category": "produce"},
            {"item": "Lemon", "qty": "1", "category": "produce"},
        ],
        "instructions": ["Stir the yogurt with the garlic (grated), a squeeze of lemon and salt."],
    },
    {
        "key": "chimichurri", "name": "Chimichurri", "kind": "sauce",
        "covers": [], "minutes": 10, "hint": "10 minutes, no cooking",
        "match": ["chimichurri", "salsa verde"],
        "ingredients": [
            {"item": "Fresh parsley", "qty": "1 bunch", "category": "produce"},
            {"item": "Garlic", "qty": "2 cloves", "category": "produce"},
            {"item": "Red wine vinegar", "qty": "1 bottle", "category": "pantry"},
        ],
        "instructions": ["Chop the parsley and garlic fine; stir with oil, a splash of vinegar, salt and chili flakes."],
    },
]

MAX_ADDITIONS_OFFERED = 6

_KIND_ORDER = {"starch": 0, "green": 1, "sauce": 2}
_KIND_GROUP = {"starch": "carb", "green": "vegetable"}

# Units that only come whole, for scaling a side's amounts — the same list
# recipes._DISCRETE_UNITS keeps for scale_recipe, repeated here rather than
# imported so this module stays free of the recipes import (recipes reaches
# into plates already).
_WHOLE_UNITS = {"clove", "head", "bunch", "stick", "slice", "sprig", "stalk"}


def addition_by_key(key: str) -> dict | None:
    for a in ADDITIONS:
        if a["key"] == key:
            return a
    return None


def addition_by_name(name: str) -> dict | None:
    """The catalogue row whose name is `name`, spelled any way; None if none."""
    wanted = _name_key(name or "")
    if not wanted:
        return None
    for a in ADDITIONS:
        if _name_key(a["name"]) == wanted:
            return a
    return None


def _catalogue_side(addition: dict) -> dict:
    """One catalogue row as a side ready for sides_json — the same shape
    _clean_side stores, plus `servings` (what it was written for) and
    `added_by: "household"` so a screen can tell it from one the app chose."""
    return {
        "name": addition["name"],
        "covers": list(addition["covers"]),
        "ingredients": [dict(i) for i in addition["ingredients"]],
        "instructions": list(addition["instructions"]),
        "minutes": addition["minutes"],
        "servings": ADDITION_SERVINGS,
        "added_by": "household",
    }


def scale_side_ingredients(side: dict, target_servings: int | None) -> list[dict]:
    """
    A side's ingredients scaled from the servings it was written for to
    `target_servings`. A side with no `servings` of its own (every side the
    plate pass attached before this existed) is left exactly as written,
    and so is any quantity that doesn't parse ("a bunch", blank) — the
    same rule scale_recipe follows. Whole things round to a whole, never
    below one.
    """
    base = side.get("servings")
    try:
        base = int(base) if base else None
    except (TypeError, ValueError):
        base = None
    out = []
    for ing in side.get("ingredients") or []:
        item = (ing.get("item") or "").strip()
        if not item:
            continue
        row = {
            "item": item,
            "qty": (ing.get("qty") or "").strip(),
            "category": (ing.get("category") or "other").strip() or "other",
        }
        if base and target_servings and target_servings > 0:
            from . import quantities as _quantities
            parsed = _quantities._parse_quantity(row["qty"])
            if parsed:
                amount, unit = parsed
                scaled = amount * target_servings / base
                if unit is None or unit in _WHOLE_UNITS:
                    scaled = max(1.0, float(round(scaled)))
                # Through _format_quantity even at 1:1, so the card says
                # "2 lbs" the way the list does rather than the catalogue's
                # raw "2 lb" (found by the verifying pass, 2026-09-13).
                row["qty"] = _quantities._format_quantity(scaled, unit)
        out.append(row)
    return out


def side_ingest_groups(sides: list[dict] | None) -> list[tuple[list[dict], int | None]]:
    """
    A list of sides as (ingredients, servings) groups for the grocery
    ingest — one group per distinct `servings` value, so a household's
    catalogue side (written for four) is scaled to the night's eaters the
    way a recipe is, while the app's own sides (no servings) keep riding
    on attendance alone as they always have. Order is preserved.
    """
    groups: dict[int | None, list[dict]] = {}
    for side in sides or []:
        servings = side.get("servings")
        try:
            servings = int(servings) if servings else None
        except (TypeError, ValueError):
            servings = None
        groups.setdefault(servings, []).extend(side_ingredients([side]))
    return [(ings, servings) for servings, ings in groups.items() if ings]


def _entry_row(conn, entry_id: int, weekly_plan_id: int | None = None):
    """The entry, household-scoped; with `weekly_plan_id` also plan-scoped
    (the week routes name the week, so an id from another week is a 404)."""
    row = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.weekly_plan_id, mpe.sides_json,
               mpe.food_groups_json, wp.status AS plan_status,
               COALESCE(r.name, mpe.freeform_meal) AS meal, r.ingredients_json
        FROM meal_plan_entries mpe
        LEFT JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.id = ? AND mpe.household_id = ?
        """,
        (entry_id, household_id()),
    ).fetchone()
    if row and weekly_plan_id is not None and row["weekly_plan_id"] != weekly_plan_id:
        return None
    return row


def _sides_of(row) -> list[dict]:
    try:
        sides = json.loads(row["sides_json"] or "[]")
    except (TypeError, ValueError):
        return []
    return sides if isinstance(sides, list) else []


def _name_key(name: str) -> str:
    return " ".join((name or "").lower().replace("-", " ").split())


def _dish_words(row, sides: list[dict]) -> str:
    """Everything the dish already is, as one lowercase string to look
    words up in: its name, its ingredients, and the sides already on it."""
    parts = [row["meal"] or ""]
    try:
        for ing in json.loads(row["ingredients_json"] or "[]"):
            if isinstance(ing, dict):
                parts.append(ing.get("item") or "")
    except (TypeError, ValueError):
        pass
    for side in sides:
        parts.append(side.get("name") or "")
        for ing in side.get("ingredients") or []:
            parts.append(ing.get("item") or "")
    return " ".join(parts).lower()


def _addition_matches(addition: dict, words: str) -> bool:
    """
    Does `words` (_dish_words: the dish's own name, its ingredients, and
    its sides, already lowercased) already have this addition, so it
    isn't offered a second time?

    "potato" is handled apart from a plain substring check: "Sweet Potato
    Mash" must not hide "Roasted potatoes" (Emily's Cajun Salmon night,
    2026-09-22) — a dish naming sweet potato is not a dish that already
    has (regular) potatoes — but a dish with real potatoes still does.
    Every other addition's match list is still the plain substring check
    it always was.
    """
    for w in addition["match"]:
        if w == "potato":
            if re.search(r"(?<!sweet )potato", words):
                return True
            continue
        if w in words:
            return True
    return False


def suggest_additions(entry_id: int, eating_style: str | None = None, weekly_plan_id: int | None = None,
                      role: str | None = None) -> dict:
    """
    What the meal screen's "Add something" sheet offers for THIS dish:
    the catalogue, minus anything the dish already has (a potato dish is
    not offered potatoes; a side already added is not offered twice),
    ordered so whatever the plate is short of comes first, and — for a
    household whose eating_style reads low-carb — with the starches last
    rather than hidden: they asked to add something, and the picker is
    theirs to choose from.

    `role` is the part the sheet was opened for ("carb" from the card's
    "+ Add a carb" chip, "vegetable" from a Veg chip): the sheet is
    authoritative here and shows ONLY that kind — a role sheet titled
    "Add a carb" offering a green salad or a sauce is the sheet lying
    about its own title (Emily, 2026-09-22: her Cajun Salmon night's "Add
    a carb" sheet listed Green salad, Steamed broccoli, Garlic yogurt
    sauce and Chimichurri alongside the two carbs — "some of these are
    not carb suggestions"). With no role (the general "What should go
    with it?" sheet) everything is still offered, sauces included.

    Whether carb is missing at all is starch-aware, not just a read of
    food_groups_json: a dish whose own name or ingredients already carry
    a carb (plates.dish_has_carb) counts as having one even when the
    model's food_groups missed it — the same Cajun Salmon dish's sweet
    potato mash. That backstop only applies once the dish has SOME
    recorded food groups; one with none stays unknown, same as
    missing_groups always treats it.

    Pure read. Returns {"entry_id", "meal", "options": [...], "added":
    [names already on the dish]}.
    """
    conn = get_conn()
    row = _entry_row(conn, entry_id, weekly_plan_id)
    conn.close()
    if not row:
        raise ValueError(f"No meal plan entry {entry_id} in this household.")
    sides = _sides_of(row)
    already = {_name_key(s.get("name") or "") for s in sides}
    words = _dish_words(row, sides)
    try:
        groups = json.loads(row["food_groups_json"] or "[]")
    except (TypeError, ValueError):
        groups = []
    if groups and "carb" not in groups and has_starch(words):
        groups = groups + ["carb"]
    level = household_carb_level(eating_style)
    rule = plate_rule(level=level)
    missing = missing_groups({"slot": row["slot"], "food_groups": groups}, rule)
    # Only a household on NONE sees the starches last; a low-carb plate
    # carries a small one by rule now (Emily, 2026-09-21).
    low_carb = level == "none"

    asked_for = (role or "").strip().lower()

    def rank(a):
        group = _KIND_GROUP.get(a["kind"])
        return (
            0 if group and group in missing else 1,
            3 if (low_carb and a["kind"] == "starch") else _KIND_ORDER.get(a["kind"], 9),
        )

    # The general (no-role) sheet still shows every kind, sauces included
    # — a bigger carb catalogue (Emily, 2026-09-22) must not let one kind's
    # "missing" priority push every other kind off the six-row cap before
    # it gets a look-in. A role sheet has no such cap: it's filtered to
    # one kind already, so all of that kind's rows compete for the six.
    per_kind_cap = None if asked_for else 3
    kind_counts: dict[str, int] = {}
    options = []
    for a in sorted(ADDITIONS, key=rank):
        # A role sheet is filtered to its own kind BEFORE the six-row cap,
        # not merely sorted to the top of it — see the docstring above.
        if asked_for and _KIND_GROUP.get(a["kind"]) != asked_for:
            continue
        if _name_key(a["name"]) in already:
            continue
        if _addition_matches(a, words):
            continue
        if per_kind_cap and kind_counts.get(a["kind"], 0) >= per_kind_cap:
            continue
        kind_counts[a["kind"]] = kind_counts.get(a["kind"], 0) + 1
        options.append({
            "key": a["key"], "name": a["name"], "kind": a["kind"],
            "minutes": a["minutes"], "hint": a["hint"], "covers": list(a["covers"]),
        })
    # A short list (the ticket's words): the sheet shows the rows and the
    # line to type without scrolling on a phone at six, not at eight.
    options = options[:MAX_ADDITIONS_OFFERED]
    return {
        "entry_id": entry_id,
        "meal": row["meal"],
        "options": options,
        "added": [s.get("name") for s in sides if s.get("added_by") == "household"],
    }


def carry_sides(from_sides: list[dict], to_entry_id: int) -> list[dict]:
    """
    Put the sides one entry had onto its replacement (plate_parts.change_part
    and its undo, 2026-09-13): the same dish with a different protein is
    the same plate, and the roasted potatoes the household just added must
    not vanish because the meat changed. Written through attach_sides (the
    same column and shape), then bought again on an approved week — the
    swap that made the new entry reversed the old entry's shopping, sides
    included, so each side buys itself back the way add_component does.
    The old grocery_link_ids are dropped first: they name rows that no
    longer exist. Returns the sides as attached.
    """
    sides = []
    for s in from_sides or []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        sides.append({k: v for k, v in s.items() if k != "grocery_link_ids"})
    if not sides:
        return []
    covered: list[str] = []
    for s in sides:
        for g in s.get("covers") or []:
            if g in ALL_GROUPS and g not in covered:
                covered.append(g)
    attach_sides(to_entry_id, sides, covered)
    conn = get_conn()
    row = _entry_row(conn, to_entry_id)
    conn.close()
    if row is not None:
        for s in sides:
            try:
                _buy_side_now(row, s)
            except Exception:
                logger.exception("Buying the carried side %r for entry %s failed", s.get("name"), to_entry_id)
    return sides


def _buy_side_now(row, side: dict) -> list[str]:
    """
    Put one just-added side on the grocery list, if the week it belongs to
    is already approved — a draft's sides are bought at approval with
    everything else (_entry_side_ingredients), so nothing to do there.
    Recorded against the entry, like the app's own sides, so swapping the
    dish away takes the addition's shopping with it
    (_reverse_meal_grocery_contributions is keyed by entry). Scaled to who
    is eating through the side's own `servings`, the recipe way.
    """
    if row["plan_status"] != "approved":
        return []
    from . import recipes as _recipes
    before = _link_ids(row["id"])
    added_all: list[str] = []
    for ingredients, servings in side_ingest_groups([side]):
        added, _have = _recipes._add_recipe_ingredients_for_entries(
            [row["id"]], ingredients, row["weekly_plan_id"], default_servings=servings,
        )
        added_all.extend(added)
    # Remember exactly which ledger rows this addition wrote, so Undo can
    # take back those and only those — the dish's own lemon, on the same
    # entry, is a different row and stays (remove_component).
    new_ids = sorted(_link_ids(row["id"]) - before)
    if new_ids:
        _set_side_field(row["id"], side["name"], "grocery_link_ids", new_ids)
    return added_all


def _link_ids(entry_id: int) -> set[int]:
    conn = get_conn()
    ids = {
        r["id"] for r in conn.execute(
            "SELECT id FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id = ?",
            (household_id(), entry_id),
        ).fetchall()
    }
    conn.close()
    return ids


def _set_side_field(entry_id: int, name: str, field: str, value) -> None:
    """Write one field onto the named side of an entry's sides_json."""
    conn = get_conn()
    row = conn.execute(
        "SELECT sides_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    if not row:
        conn.close()
        return
    sides = _sides_of(row)
    for side in sides:
        if _name_key(side.get("name") or "") == _name_key(name):
            side[field] = value
    conn.execute(
        "UPDATE meal_plan_entries SET sides_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(sides), entry_id, household_id()),
    )
    conn.commit()
    conn.close()


def _free_text_side(text: str, context: dict, side_generator) -> tuple[dict, str]:
    """
    A side for something typed rather than tapped. One small model call
    (generate_sides_llm, told what was asked for by name); if it can't
    answer, a bare side named as typed — one line on the list with no
    amount, no step on the clock — and a sentence saying so, rather than
    an invented recipe. Returns (side, note).
    """
    if side_generator is None:
        from .. import agent as _agent
        side_generator = _agent.generate_sides_llm
    raw_sides = []
    try:
        raw_sides = side_generator({**context, "missing": [], "requested": text}) or []
    except Exception:
        logger.exception("Writing a side for %r failed; adding it as typed", text)
    side = None
    for raw in raw_sides[:1]:
        side = _clean_side(raw, ALL_GROUPS)
    if side is not None:
        side["servings"] = ADDITION_SERVINGS
        side["added_by"] = "household"
        return side, ""
    name = text.strip()
    name = name[:1].upper() + name[1:]
    return ({
        "name": name, "covers": [],
        "ingredients": [{"item": name, "qty": "", "category": "other"}],
        "instructions": [], "minutes": None,
        "servings": None, "added_by": "household",
    }, "I couldn’t work out amounts or a step for that, so it’s on the list as written.")


def add_component(
    entry_id: int, key: str | None = None, text: str | None = None,
    context: dict | None = None, side_generator=None, weekly_plan_id: int | None = None,
) -> dict:
    """
    Add one thing to a planned meal from its own screen — "add potatoes"
    without opening the chat. `key` names a catalogue row (ADDITIONS);
    `text` is the free-text line. Exactly one of them.

    Writes the side onto the entry (attach_sides — the same column and
    shape as a side the plate pass chose, so every reader already knows
    it), then buys it if the week is approved (_buy_side_now). The Cooker
    card picks it up on the next read: its ingredients fold into the
    dish's list, its steps land as "Alongside — <name>: …" on the recipe's
    Steps card, and `minutes` counts toward the whole plate's time
    (mealClockTotal, shell.js — what the Tonight card's clock reads).

    The same thing twice is a no-op with status "already", never a second
    row and never a second buy.

    `context` is what the free-text model call should know beyond the
    meal itself (dislikes, dietary_restrictions, eating_style) — the
    caller assembles it from household memory, as _complete_plates_pass
    does; `side_generator` is injectable for tests, as in complete_plate.

    Returns {"status": "added" | "already", "entry_id", "name", "side",
    "grocery_added": [items], "note": "" | one sentence}.
    """
    key = (key or "").strip()
    text = (text or "").strip()
    if bool(key) == bool(text):
        raise ValueError("Say which addition, or what to add — one or the other.")
    conn = get_conn()
    row = _entry_row(conn, entry_id, weekly_plan_id)
    conn.close()
    if not row:
        raise ValueError(f"No meal plan entry {entry_id} in this household.")
    sides = _sides_of(row)
    note = ""
    if key:
        addition = addition_by_key(key)
        if addition is None:
            raise ValueError(f"No addition called {key!r}.")
        side = _catalogue_side(addition)
    elif addition_by_name(text) is not None:
        # "Roasted potatoes" typed is the catalogue's roasted potatoes —
        # amounts, step and minutes included — not a model's guess at
        # them. Also how a changed side's Undo puts the old one back
        # (shell.js runMealAddUndo, 2026-09-13).
        side = _catalogue_side(addition_by_name(text))
    else:
        if len(text) > 80:
            raise ValueError("That’s a bit long for one addition — a few words is plenty.")
        ctx = {
            "meal": row["meal"], "slot": row["slot"], "date": row["date"],
            **(context or {}),
        }
        side, note = _free_text_side(text, ctx, side_generator)

    if _name_key(side["name"]) in {_name_key(s.get("name") or "") for s in sides}:
        return {
            "status": "already", "entry_id": entry_id, "name": side["name"],
            "side": side, "grocery_added": [], "note": "",
        }

    attach_sides(entry_id, [side], list(side.get("covers") or []))
    grocery_added: list[str] = []
    try:
        grocery_added = _buy_side_now(row, side)
    except Exception:
        logger.exception("Buying the addition %r for entry %s failed", side["name"], entry_id)
        note = (note + " " if note else "") + "It’s on the meal, but I couldn’t put it on the list — add it there by hand."
    return {
        "status": "added", "entry_id": entry_id, "name": side["name"], "side": side,
        "grocery_added": grocery_added, "note": note.strip(),
    }


def remove_component(entry_id: int, name: str, weekly_plan_id: int | None = None) -> dict:
    """
    Take one added side back off a meal — the Undo on "Added roasted
    potatoes". Removes it from sides_json and takes just ITS lines back
    off the grocery list, leaving the dish's own shopping alone
    (_reverse_meal_grocery_contributions, narrowed): by the exact ledger
    rows the addition wrote when it was bought on an approved week
    (grocery_link_ids), or — for a side added to a draft and bought at
    approval, where its shares were rounded in with the recipe's — by
    item name, skipping any item the dish or another side also lists, so
    the worst case is a lemon left on the list rather than one taken off
    the shrimp. Removing something that isn't there is a no-op with
    status "gone".
    """
    from . import grocery as _grocery
    conn = get_conn()
    row = _entry_row(conn, entry_id, weekly_plan_id)
    if not row:
        conn.close()
        raise ValueError(f"No meal plan entry {entry_id} in this household.")
    sides = _sides_of(row)
    wanted = _name_key(name)
    keep = [s for s in sides if _name_key(s.get("name") or "") != wanted]
    gone = [s for s in sides if _name_key(s.get("name") or "") == wanted]
    if not gone:
        conn.close()
        return {"status": "gone", "entry_id": entry_id, "name": name, "grocery_removed": []}
    conn.execute(
        "UPDATE meal_plan_entries SET sides_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(keep), entry_id, household_id()),
    )
    conn.commit()
    conn.close()
    link_ids = [i for s in gone for i in (s.get("grocery_link_ids") or [])]
    if link_ids:
        reversal = _grocery._reverse_meal_grocery_contributions(entry_id, only_link_ids=link_ids)
    else:
        still_there = _dish_words(row, keep)
        items = {
            (i.get("item") or "").strip()
            for s in gone for i in (s.get("ingredients") or [])
            if (i.get("item") or "").strip() and (i.get("item") or "").strip().lower() not in still_there
        }
        reversal = _grocery._reverse_meal_grocery_contributions(entry_id, only_items=items)
    return {
        "status": "removed", "entry_id": entry_id, "name": gone[0].get("name") or name,
        "grocery_removed": reversal["removed_items"] + reversal["trimmed_items"],
    }
