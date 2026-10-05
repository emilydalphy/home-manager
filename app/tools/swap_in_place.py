"""
Swap one meal, in place, for the cost of one small model call.

Julia (first beta tester, 2026-09-08): "Be able to click on the one recipe
and meal that the user wants to switch and then have it regenerate just the
one on the spot." Until now "Swap" on the Meals screen opened the ask sheet
and spent a whole chat turn — a ~16k-token conversation round trip, plus
the household having to type a sentence — to change one dinner.

What this module is, and what it deliberately is not:

  * It is ONE compact forced tool call at the app's cheapest effort route
    (`utility`), carrying only what actually constrains this one slot:
    the household's hard exclusions, their dislikes and eating style, the
    plate rule, who is at that table and how many, the night's tags and
    time cap, the taste verdicts for that table, the rest of the week's
    dishes (so the replacement doesn't repeat one), and `avoid` — the
    dishes already turned down for this slot in this sitting. Priced in
    the api_calls ledger under the call site `swap_in_place`.

  * It is NOT a second swap implementation. The pick is applied through
    weekly_plan.swap_meal_in_plan, exactly as a chat swap is, so leftover
    chains unlink the same way, the grocery list is left alone on a draft
    and kept in step on an approved week, plate sides and the shared
    taste_verdict behave identically. Anything that becomes true of a chat
    swap becomes true of this one for free.

  * It is NOT trusted. The dish that comes back is run through the same
    allergen matcher the draft's own banner uses
    (coordination.check_meal_conflicts) BEFORE anything is written. A hard
    clash costs one more call with that dish added to `avoid`; a second
    clash is refused in plain words and nothing changes. Checking before
    writing rather than after is deliberate: applying and then reversing a
    swap would drag the grocery list and any leftover chain through a
    change the household never asked for.

Undo is one field, written once. The outgoing dish is stored on the new
entry's `derived_from.swapped_from`, and a second swap of the same slot
carries the ORIGINAL forward rather than overwriting it — so "Undo" always
means "put back what was there before I started tapping", not "step back
one dish". undo_meal_swap goes through swap_meal_in_plan too, for the same
reason the swap does.

One thing this shares with every other swap in the app, worth knowing
before changing it: swap_meal_in_plan breaks a confirmed leftover chain and
nothing re-confirms it, so undoing a swap of a batch-cooking night restores
the dish but not the chain. That is pre-existing behaviour, not something
this path adds — see swap_meal_in_plan's own docstring. The one exception
is a dish swapped on every day it's planned at once (apply_pick_to_days,
Emily 2026-09-22): its cook and reheat nights are replaced together, so
the chain is carried to the new dish and back again on Undo.
"""
from __future__ import annotations

import datetime
import json
import logging
import uuid

from ..db import get_conn
from ._shared import household_id
from . import attendance as _attendance
from . import coordination as _coordination
from . import household as _household
from . import memory as _memory
from . import plates as _plates
from . import plan_quality as _plan_quality
from . import recipes as _recipes
from . import taste_verdict as _taste_verdict
from . import time_caps as _time_caps
from . import week_intake as _week_intake
from . import weekday_lunches as _weekday_lunches
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")

# One retry, and only for an allergen clash. Not a general "try until it's
# good" loop: every attempt is a real API call against a $1/household/month
# budget, and a model that has just been told the exclusion in the prompt
# and broken it twice is not going to be talked round on a third.
MAX_PICK_ATTEMPTS = 2

# Said when both attempts clash. Calm and plain, paired with its way out in
# the same breath (DESIGN_SYSTEM.md §8, the calm-in-trouble rule) — and it
# names the state of the plan, because "nothing changed" is the fact the
# household most needs after a refusal.
REFUSAL = (
    "I couldn’t find something that works around what you can’t have. "
    "Nothing changed — tell me what you’d like instead."
)

# What the model may not put in an entry. Anything else it sends back is
# ignored rather than trusted into the database.
_INGREDIENT_CATEGORIES = ("produce", "dairy", "meat/seafood", "pantry", "frozen", "other")


# ---------- reading the slot ----------


def _entry(weekly_plan_id: int, entry_id: int) -> dict:
    """
    The one entry being swapped, household- and plan-scoped both, so an
    entry id from another household (or another week) is a 404 rather than
    a swap of somebody else's dinner.
    """
    conn = get_conn()
    row = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.recipe_id, mpe.freeform_meal,
               mpe.food_groups_json, mpe.reasoning, mpe.slot_state,
               mpe.derived_from_json, mpe.component_category,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.id = ? AND mpe.household_id = ? AND mpe.weekly_plan_id = ?
        """,
        (entry_id, household_id(), weekly_plan_id),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No meal {entry_id} on that week's plan.")
    if row["component_category"]:
        # A component-based plan keys its rows by category, not by date and
        # slot: swap_meal_in_plan's date+slot delete would take every
        # component with it. Those plans swap through the component path.
        raise ValueError("That plan is built from components, not meals — swap it in chat for now.")
    return {
        "entry_id": row["id"],
        "date": row["date"],
        "slot": row["slot"] or "dinner",
        "meal": (row["meal"] or "").strip(),
        "recipe_id": row["recipe_id"],
        "freeform_meal": row["freeform_meal"],
        "food_groups": json.loads(row["food_groups_json"] or "[]"),
        "reasoning": row["reasoning"] or "",
        "slot_state": row["slot_state"] or "planned",
        "derived_from": json.loads(row["derived_from_json"] or "{}") or {},
    }


def _write_entry_note(entry_id: int, reasoning: str, derived_from: dict) -> None:
    """
    The two fields plan_meal can't be told about from inside
    swap_meal_in_plan (it passes neither through). Written straight after
    the swap rather than by widening that function's signature, so the
    chat swap path this borrows stays exactly as it is.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET reasoning = ?, derived_from_json = ? "
        "WHERE id = ? AND household_id = ?",
        (reasoning, json.dumps(derived_from or {}), entry_id, household_id()),
    )
    conn.commit()
    conn.close()


def _week_start_of(weekly_plan_id: int) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT week_start_date FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    conn.close()
    return row["week_start_date"] if row else None


def _dedup(names: list[str]) -> list[str]:
    """Names in the order they were first said, case-insensitively once each."""
    seen, out = set(), []
    for name in names:
        key = (name or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(name.strip())
    return out


def _weekday(iso: str) -> str:
    try:
        return datetime.date.fromisoformat(iso).strftime("%A")
    except (TypeError, ValueError):
        return ""


# ---------- what the model is told ----------


def _hard_exclusions() -> list[str]:
    """
    Allergies and must-avoids, as the household wrote them: each member's
    own dietary_restrictions, plus every What-we-know fact flagged hard.
    Named "must_not_contain" in the prompt because that is what the model
    has to do with them — an allergy phrased as a preference gets treated
    like one.
    """
    out: list[str] = []
    for member in _household.list_members():
        for restriction in member.get("dietary_restrictions") or []:
            restriction = (restriction or "").strip()
            if restriction:
                out.append(f"{member['name']}: {restriction}")
    for fact in _memory.get_facts():
        if fact.get("hard") and (fact.get("text") or "").strip():
            out.append(fact["text"].strip())
    return out


def _table_for(meal_date: str, slot: str) -> dict:
    att = _attendance.get_slot_attendance(meal_date, slot)
    return {
        "serves": att["headcount"],
        "present": att["present_names"],
        "away": att["absent_names"],
        "guests": att["guest_count"],
    }


def _taste_lines_for(meal_date: str, slot: str, table: dict) -> list[str]:
    """
    The same shared verdicts generation is handed, narrowed to this one
    table: the whole-household line, plus the line for this slot when its
    table differs and its verdicts differ with it.

    Built by handing generation_taste_lines a one-slot attendance context
    rather than by reimplementing the resolution — one hater at the table
    vetoes a dish, and that rule must not have a second, slightly different
    copy living here.
    """
    try:
        members = [m["name"] for m in _household.list_members()]
        return _taste_verdict.generation_taste_lines({
            "household_members": members,
            "slots_with_a_different_table": [{
                "date": meal_date, "slot": slot,
                "serves": table["serves"], "present": table["present"],
                "away": table["away"], "guests": table["guests"],
            }],
        })
    except Exception:
        logger.exception("Taste lines failed for %s %s; swapping without them", meal_date, slot)
        return []


def _other_dishes(weekly_plan_id: int, entry_id: int) -> list[str]:
    """
    The rest of the week, one line each ("Monday dinner: Chili") — what
    stops the replacement being a second helping of Tuesday. Only real
    planned dishes: an open or deliberately-empty slot has nothing to vary
    from.
    """
    lines = []
    for meal in _weekly_plan.get_weekly_plan(weekly_plan_id).get("meals") or []:
        if meal.get("entry_id") == entry_id:
            continue
        name = (meal.get("meal") or "").strip()
        if not name or meal.get("slot_state") in ("planned_empty", "open"):
            continue
        when = _weekday(meal.get("date") or "")
        lines.append(f"{when} {meal.get('slot') or 'dinner'}: {name}".strip())
    return lines


def _minutes_cap(entry: dict, tags: list[str], memory: dict, lunch_kind: str | None = None) -> int | None:
    """
    The real cap on this meal's cooking, or None — time_caps.minutes_cap,
    the same rule the generator's plate and variety passes use, for this
    entry's own slot (Emily, 2026-09-23: a weekday lunch cooked that day is
    20 minutes; a rush dinner 30). Either end of a leftovers chain — the
    reheat (links_to) or the batch cook (make_double_for) — is not a cook
    on the day, so it carries no lunch cap. `lunch_kind` is how the week's
    intake says this weekday lunch is made (step 3, 2026-09-25): a lunch
    "cooked" that day stays at 20 minutes even on a prep day.
    """
    derived = entry.get("derived_from") or {}
    chained = bool(derived.get("links_to") or derived.get("make_double_for"))
    return _time_caps.minutes_cap(entry["date"], entry.get("slot") or "dinner", tags, memory,
                                  is_leftovers=chained, lunch_kind=lunch_kind)


def build_swap_context(weekly_plan_id: int, entry: dict, avoid: list[str] | None = None) -> dict:
    """
    Everything the one call is told, and nothing else.

    Deliberately small — this is the constraint set for ONE slot, not a
    week's worth of planning context. No saved-recipe catalogue, no
    inventory, no three weeks of history: the replacement has to be safe,
    different from the rest of the week, and right for that table, and
    every key here earns its tokens against one of those three.
    """
    memory = _memory.get_household_memory()
    week_start = _week_start_of(weekly_plan_id)
    intake = _week_intake.get_week_intake(week_start) if week_start else None
    tags = ((intake or {}).get("night_tags") or {}).get(entry["date"]) or []
    table = _table_for(entry["date"], entry["slot"])
    # The outgoing dish is always an avoid — swapping a meal for itself is
    # the one answer the household has definitely already rejected.
    avoid_list = _dedup([entry["meal"]] + list(avoid or []))

    carb = _plates.household_carb_level(memory.get("eating_style") or "")
    context = {
        "date": entry["date"],
        "weekday": _weekday(entry["date"]),
        "slot": entry["slot"],
        "replacing": entry["meal"],
        "avoid": avoid_list,
        "must_not_contain": _hard_exclusions(),
        "dislikes": memory.get("dislikes") or [],
        "eating_style": memory.get("eating_style") or "",
        "kitchen_kit": memory.get("kitchen_kit") or [],
        "plate_rule": list(_plates.plate_rule(level=carb)),
        "carb_portion": _plates.carb_portion(carb),
        "carb_guidance": _plates.CARB_GUIDANCE[carb],
        # Explained IN the context, not only in this module's INSTRUCTIONS:
        # the three-picks sheet (swap_options) reads this same context
        # under instructions of its own, and a field the model is never
        # told the meaning of is a field it may read wrong whichever
        # branch merges first (verifier, 2026-09-21).
        "carb_note": (
            "carb_portion is how much carb this household's plate carries: 'none' means no carb at "
            "all; 'small' means a half portion of potato, rice, tortilla or bread — never none, low "
            "carb is not no carb; 'normal' a full portion; 'generous' a big one. carb_guidance says "
            "the same in a sentence."
        ),
        "table": table,
        "night_tags": tags,
        "max_minutes": _minutes_cap(entry, tags, memory, _lunch_kind(intake, entry)),
        "week_other_dishes": _other_dishes(weekly_plan_id, entry["entry_id"]),
    }
    taste = _taste_lines_for(entry["date"], entry["slot"], table)
    if taste:
        context["taste_verdicts"] = taste
    return context


# ---------- the pick ----------


SWAP_TOOL = {
    "name": "submit_swap",
    "description": "Submit the one replacement dish for this slot.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meal_name": {"type": "string", "description": "What the dish IS — never named after an ingredient it leaves out."},
            "is_new_recipe": {"type": "boolean", "description": "False only when reusing a dish already saved for this household by that exact name."},
            "ingredients": {
                "type": "array",
                "description": "Required for a new recipe. Store-bought units, plain grocery names, no prep descriptors, no staples (salt, pepper, oil).",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "qty": {"type": "string"},
                        "category": {"type": "string", "enum": list(_INGREDIENT_CATEGORIES)},
                    },
                    "required": ["item"],
                },
            },
            "instructions": {"type": "array", "items": {"type": "string"}},
            "food_groups": {
                "type": "array",
                "items": {"type": "string", "enum": ["protein", "carb", "vegetable"]},
                "description": "What the whole plate covers once this dish is on it.",
            },
            "cuisine": {"type": "string"},
            "main_protein": {"type": "string"},
            "prep_time_minutes": {"type": "integer"},
            "cook_time_minutes": {"type": "integer"},
            "default_servings": {"type": "integer"},
            "reason": {
                "type": "string",
                "description": "ONE short line the household reads on the card — warm, plain, under about ten words, no exclamation mark. Say what makes this a better fit than the dish it replaces. E.g. 'Lighter than the chops, and nothing to thaw.'",
            },
        },
        "required": ["meal_name", "reason"],
    },
}


# Static, so it caches: the household's own JSON is the only thing that
# changes between calls (see generate_weekly_plan_llm's note on why the
# order matters for Anthropic's prefix cache).
INSTRUCTIONS = """You are replacing ONE meal in a household's already-planned week. Everything \
else about the week stays exactly as it is — you are not re-planning anything, and you are not \
being asked for options. Pick one dish, and write it out properly enough to cook and to shop for.

Rules, in this order:
- `must_not_contain` is absolute. Nothing you pick may contain any of it, in the dish or in \
anything served with it. An allergy written as a sentence is still an allergy. If honouring it \
leaves you no good answer, pick a plainer dish rather than a clever one — never a compromise.
- Never name a dish after an ingredient it leaves out. No "Nut-Free Noodles". The name says what \
the dish IS. No allergen-free label in the name either ("Dairy-Free", \
"Egg-Free", "Non-Dairy", "Vegan"): "Oat Milk Pancakes", not "Dairy-Free Pancakes" — a dish with \
a label in its name is turned down, because the label itself names the allergen.
- `avoid` is what has already been turned down for this slot, including the dish being replaced. \
Don't come back with any of them, or with a near-identical variant of one.
- `must_contain`, when present, is something the household asked to use this week ("I have some \
corn — work it in"). It goes IN the dish: a real part of it, in the ingredient list and the steps, \
never a garnish — and in the name where that reads naturally. A pick without it is thrown away.
- `must_be_cuisine`, when present, is a cuisine or kind of dish the household picked for this week \
("Mexican", "Burgers"). The dish IS that — a burger for Burgers, a Mexican dish for Mexican — with \
it in the name or in `cuisine`. A pick that isn't is thrown away.
- `week_other_dishes` is the rest of this week. Don't repeat one, and don't repeat the protein or \
cuisine of the nights either side of this one — the point of a swap is a different night, not the \
same night renamed.
- `taste_verdicts`, when present, is per-person feedback already resolved into one verdict per \
table. A dish under `avoid:` has somebody at that table who won't eat it, and one person not \
eating it rules it out however many others like it. A dish under `loved:` is a nudge, never a \
reason to repeat the week.
- `plate_rule` is what a full plate covers here. Dinner and lunch cover all of it — plan the side \
INTO the dish (in the name, the ingredients and the steps) rather than leaving the plate short. \
Breakfast and snack cover at least two of those groups. `carb_guidance` says how much carb that \
is: "small" (`carb_portion`) means a half portion of potato, rice, tortilla or bread on the \
plate, never none — low carb is not no carb; "none" means no carb at all.
- `table.serves` is how many actually eat this meal. Choose something that suits that number and \
write every quantity for it — a dinner for one is what a person makes for themselves, not a \
family tray divided.
- `max_minutes`, when given, is a hard cap on prep plus cook for this meal.
- `night_tags`: `rush` means fast and unfussy, `unrushed` means there is no time cap tonight (a \
longer dish is allowed, not required), `guests` means scale it and pick something the table will \
all eat, `normal` means an ordinary night — don't get clever with it.
- `eating_style` is a hard constraint, not a style nudge — every ingredient has to fit it. \
`dislikes` are to be avoided. `kitchen_kit` is what they own to cook with; an empty list means \
unknown, so don't constrain on it.
- Write each ingredient's qty the way it's bought at the store ("1 head", "1 bunch", "1 lb"), and \
the item as the plain grocery name ("Baby spinach"), never with a prep descriptor. Leave staples \
they certainly have — salt, pepper, oil — out of the list entirely.
- `reason` is the one line shown under the new dish on the card. Warm, plain, specific to the \
swap, under about ten words, no exclamation mark: "Lighter than the chops, and nothing to thaw."

Call submit_swap with the one dish."""


def _pick_replacement(context: dict) -> dict:
    """
    The single model call. Lazily imports agent for its client, retry,
    effort routing and cost ledger, rather than duplicating any of them —
    and lazily because agent imports tools, so a module-level import here
    would be a cycle.

    `utility` is the app's cheapest effort route, which is the right one:
    this is a small, well-constrained choice with the constraints handed to
    it, not the week-shaped reasoning `generation` exists for.
    """
    from .. import agent

    client = agent._client()
    response = agent._create_with_retry(
        client,
        label="swap_in_place",
        model=agent.MODEL,
        max_tokens=2000,
        tools=[SWAP_TOOL],
        tool_choice={"type": "tool", "name": "submit_swap"},
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
        logger.warning("swap_in_place hit max_tokens; the pick may be incomplete")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input or {})
    return {}


def _clean_ingredients(raw) -> list[dict]:
    out = []
    for ing in raw or []:
        item = (ing.get("item") or "").strip() if isinstance(ing, dict) else ""
        if not item:
            continue
        category = (ing.get("category") or "").strip().lower()
        out.append({
            "item": item,
            "qty": (ing.get("qty") or "").strip(),
            "category": category if category in _INGREDIENT_CATEGORIES else "other",
        })
    return out


def _save_recipe_if_new(pick: dict, serves: int) -> None:
    """
    Make the dish cookable and shoppable, the same way a chat swap does
    (the system prompt's one-off-meal rule: build it into a real recipe
    first, then plan it by name). Without this the swap lands as a freeform
    entry — nothing in the Cooker view, and nothing for the grocery list to
    add on approval.
    """
    name = pick["meal_name"]
    if pick.get("details_pending"):
        # A swap picked on a DRAFT (swap_options.choose_swap_option): the
        # dish goes in the way the menu pass saves one — a pending row with
        # the one-line dish_note — and is written up with the rest at
        # approval (agent.fill_pending_recipes_for_plan). Nothing shops for
        # a draft, so nothing needs a quantity yet.
        if not _recipes.existing_recipe_named(name):
            _recipes.add_recipe(
                name=name,
                ingredients=[],
                food_groups=[g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS],
                cuisine=(pick.get("cuisine") or "").strip(),
                main_protein=(pick.get("main_protein") or "").strip(),
                default_servings=pick.get("default_servings") or serves or 4,
                prep_time_minutes=pick.get("prep_time_minutes"),
                cook_time_minutes=pick.get("cook_time_minutes"),
                details_pending=True,
                dish_note=pick.get("dish_note") or "",
            )
        return
    ingredients = _clean_ingredients(pick.get("ingredients"))
    if not ingredients:
        return
    if _recipes.existing_recipe_named(name):
        return
    _recipes.add_recipe(
        name=name,
        ingredients=ingredients,
        food_groups=[g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS],
        cuisine=(pick.get("cuisine") or "").strip(),
        main_protein=(pick.get("main_protein") or "").strip(),
        instructions=[s for s in (pick.get("instructions") or []) if (s or "").strip()],
        default_servings=pick.get("default_servings") or serves or 4,
        prep_time_minutes=pick.get("prep_time_minutes"),
        cook_time_minutes=pick.get("cook_time_minutes"),
    )


def _hard_clash(pick: dict) -> list[dict]:
    """
    The dish, against the same matcher the draft's own allergen banner uses
    — before it is saved or planned. Only `hard` counts here: a standing
    dislike is worth a word, never worth refusing a swap the household
    asked for.
    """
    # The list the pick came with, else the saved recipe's — a pick that
    # reuses a dish by name (is_new_recipe false, no list) is matched on
    # what that dish is made of, never on its name alone.
    ingredients = _clean_ingredients(pick.get("ingredients")) or _recipes.saved_ingredients(
        pick.get("meal_name") or ""
    )
    try:
        hits = _coordination.check_meal_conflicts(
            pick.get("meal_name") or "",
            ingredients=ingredients,
        )
    except Exception:
        logger.exception("Allergen check failed for a swap pick; refusing rather than guessing")
        return [{"meal": pick.get("meal_name") or "", "severity": "hard", "member": None,
                 "restriction": "", "source": "check_failed", "matched": []}]
    return [h for h in hits if h.get("severity") == "hard"]


# ---------- the swap ----------


def swap_meal_in_place(
    weekly_plan_id: int,
    entry_id: int,
    avoid: list[str] | None = None,
    picker=None,
) -> dict:
    """
    Replace the dish on one slot with one model call, and hand back the
    day it changed.

    Returns `status` 'swapped' with the refreshed day (get_week_menu's own
    day dict, so the screen re-renders from exactly the shape it already
    knows), the new entry, the one-line reason and the dishes now on
    `avoid`; or `status` 'refused' with a plain message and nothing
    written.

    `picker` is the model call, injectable so tests never touch the real
    API.
    """
    pick_one = picker or _pick_replacement
    entry = _entry(weekly_plan_id, entry_id)
    if entry["slot_state"] != "planned" or not entry["meal"]:
        raise ValueError("There's no meal on that slot to swap.")

    # A night that has already gone by — the Day/Meal step's "Swap · I'll
    # pick", and the Review row's "Change one", which walks to that same
    # step. Measured before this went in, on an approved week: the dish was
    # rewritten and a NEW line went on the shopping list for a dinner that
    # was over.
    #
    # BE PRECISE ABOUT WHAT KEPT THAT OFF THE SCREEN, because it is not
    # nothing: slotActionsHtml and mealDockHtml both render no controls at
    # all for a day whose `isPast` is set. But that flag is computed from
    # todayLocalStr() — the BROWSER's date — while this refuses on
    # households.timezone, so it is a screen's rule and not the week's. A
    # tab drawn yesterday, a retried POST, a direct call, or a phone west
    # of the stored zone all still reach here, which is exactly the list
    # add_dish_day wrote down for its own "+" on 2026-09-16.
    #
    # Asked HERE, above the picker, rather than left to apply_pick's
    # backstop below: every attempt down there is a real API call against a
    # $1/household/month budget, and spending one to be told the night is
    # gone is a cost with nothing on the other side of it. _entry's own
    # connection is closed by the time it returns, so nothing nests.
    #
    # A refusal, not an error — this function's own contract, the one the
    # allergen refusal already uses, and runSwapInPlace shows `message` for
    # any status that isn't 'swapped'. `avoid` is echoed back untouched
    # because nothing was tried.
    if _weekly_plan.night_has_gone(entry["date"]):
        return {"status": "refused", "message": _weekly_plan.NIGHT_GONE,
                "avoid": _dedup(list(avoid or []))}

    # The outgoing dish is on `avoid` from the first call and stays on the
    # list handed back, so tapping Swap three times never circles back to
    # what was there to begin with — the screen sends this list straight
    # back as the next call's `avoid`.
    tried = _dedup([entry["meal"]] + list(avoid or []))
    # A cook feeding later meals is one pot, so this swap is one dish on
    # all of them (fed_days, 2026-10-04) — and the pick has to be asked
    # for that: the strictest of those days, and the whole batch's serves,
    # or a dinner swapped for tomorrow's lunch too would be chosen and
    # written for one table (build_dish_swap_context, batch_serves — the
    # same two the whole-dish Swap already asks under).
    group = fed_days(weekly_plan_id, entry_id)
    for attempt in range(1, MAX_PICK_ATTEMPTS + 1):
        if len(group) > 1:
            context = build_dish_swap_context(weekly_plan_id, group, tried)
            batch = batch_serves(weekly_plan_id, group, entry)
            if batch > (context.get("table") or {}).get("serves", 0):
                context["cook_for"] = (f"{batch} servings — one cook feeds this meal and the "
                                       "meals eating its leftovers")
        else:
            context = build_swap_context(weekly_plan_id, entry, tried)
        pick = pick_one(context) or {}
        name = (pick.get("meal_name") or "").strip()
        if not name:
            logger.warning("swap_in_place came back with no dish (attempt %d)", attempt)
            return {"status": "refused", "message": REFUSAL, "avoid": tried}
        pick["meal_name"] = name
        clash = _hard_clash(pick)
        if not clash:
            # Emily's taste rule (2026-09-08): one person who dislikes a dish
            # vetoes it for the whole table that night. The prompt already
            # says so; this makes it a gate rather than a request.
            # EVERY meal of the group, not only the tapped one: a dinner
            # and the lunch eating its leftovers are two tables, and one
            # person's veto on either rules the dish out (Emily's standing
            # rule, 2026-09-22, which the whole-dish Swap already applies
            # through _gate_all).
            verdict = next(
                (v for v in (_weekly_plan._taste_verdict_for_slot(name, m["date"], m["slot"])
                             for m in group)
                 if v and v.get("verdict") == "avoid"), None)
            if not verdict:
                break
            logger.warning(
                "swap_in_place picked %r, which %s would rather not eat (attempt %d)",
                name, ", ".join(verdict.get("vetoed_by") or []) or "someone", attempt,
            )
        else:
            logger.warning(
                "swap_in_place picked %r, which clashes with %s (attempt %d)",
                name, [c.get("restriction") for c in clash], attempt,
            )
        # The clashing dish joins `avoid` so the retry cannot land on it
        # again, and stays there afterwards for the same reason.
        tried.append(name)
    else:
        return {"status": "refused", "message": REFUSAL, "avoid": tried}

    tried.append(pick["meal_name"])
    out = apply_pick(weekly_plan_id, entry, pick, group=group)
    out["status"] = "swapped"
    # Both spellings: `tried` caught the name the model wrote, and
    # apply_pick may have shortened it. The screen sends this straight back
    # as the next tap's `avoid`, and it has to name the dish it can see.
    out["avoid"] = _dedup(tried + [out["meal"]])
    return out


# ---------- a blank day, planned ----------

# Said when the day has nothing to fill (every meal already planned, or the
# empty ones are away nights). Plain, and it names the state of the plan.
FILL_NOTHING = "There’s nothing on that day for me to plan."

# The note the one-slot pick is handed when there is no dish to replace —
# the swap prompt's INSTRUCTIONS talk about a replacement, and a field the
# model isn't told the meaning of is a field it may read wrong.
_FILL_NOTE = (
    "Nothing is planned on this slot yet: the household left the day out of the week and has "
    "now asked for it to be planned. `replacing` is empty — pick one dish for the slot, and "
    "make `reason` say why it suits the day, not what it replaces."
)


# Said when the day isn't one of this week's days at all.
FILL_NOT_THIS_WEEK = "That day isn’t part of this week’s plan."


def _fillable_slots(weekly_plan_id: int, meal_date: str) -> list[dict]:
    """
    The meals of one BLANK day that "Build a plan" may fill (Emily,
    2026-09-26): a slot the household left out ("Which days?" tapped the
    day off — planned_empty with the skipped_day constraint). Never a
    planned or open slot, never an away night or a night the household is
    out (a different planned_empty), and never a meal they asked for none
    of (weekly_plan.unwanted_meal_slots — a skipped day's rows for those
    are written before generation's zero-count pass, so they carry the
    skipped constraint too). Those were answers, not gaps.

    Only a slot WITH its left-out row: the write's stale check
    (_replace_slot_entries) compares the rows it deletes with the rows it
    was given, so a slot with no row would give two concurrent taps
    nothing to disagree about and plan it twice. A day with no rows at all
    renders as "Nothing yet" with its own Pick per slot, not as blank.
    Snacks are not touched; a skipped day's snacks were cleared on purpose.
    """
    from . import slot_needs as _slot_needs

    conn = get_conn()
    rows = conn.execute(
        "SELECT id, slot, slot_state, derived_from_json FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, meal_date, household_id()),
    ).fetchall()
    unwanted = _weekly_plan.unwanted_meal_slots(conn)
    conn.close()
    # A DAY, not a slot: a day with any meal on it (or any question handed
    # back) is not blank, and its gaps are the per-slot Pick's job.
    if any(r["slot_state"] in ("planned", "open") for r in rows if r["slot"] in _weekly_plan.WEEK_SLOTS):
        return []
    # …nor a meal the household's usual week has off that day
    # (usual_week.off_slots_on) — "Not planned" on a Saturday breakfast is
    # an answer, left out day or not.
    from . import usual_week as _usual_week
    usual_off = {slot for (_d, slot) in _usual_week.off_slots_on([meal_date])}
    out = []
    for slot in _weekly_plan.WEEK_SLOTS:
        if slot in unwanted or slot in usual_off:
            continue
        mine = [r for r in rows if (r["slot"] or "dinner") == slot]
        skipped = bool(mine) and all(
            r["slot_state"] == "planned_empty"
            and (json.loads(r["derived_from_json"] or "{}") or {}).get("constraint")
            == _week_intake.SKIPPED_DAY_CONSTRAINT
            for r in mine
        )
        if not skipped:
            continue
        if _slot_needs.get_slot_need(meal_date, slot).get("need") == "away":
            continue
        out.append({"slot": slot, "old_ids": [r["id"] for r in mine]})
    return out


def _in_plan_period(weekly_plan_id: int, meal_date: str) -> bool:
    conn = get_conn()
    owner = conn.execute(
        "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    conn.close()
    if not owner:
        return False
    period_start, period_days = _weekly_plan.plan_period(owner)
    return period_start <= meal_date <= _weekly_plan.period_end_date(period_start, period_days)


def fill_empty_day(weekly_plan_id: int, meal_date: str, picker=None) -> dict:
    """
    Plan a blank day of a week that has meals on its other days — the
    "Build a plan" button on a Which days card (Emily, 2026-09-26). Only
    the day's left-out meals are filled (_fillable_slots); nothing already
    planned anywhere in the week is touched.

    One slot at a time, through the machinery a Swap already trusts: the
    same one-slot context (build_swap_context, so the rest of the week —
    including the meals filled a moment ago — is what the pick must not
    repeat), the same picker and allergen/taste gates as
    swap_meal_in_place, the same recipe save, and the same one-transaction
    write (_replace_slot_entries) — so a draft's list is left alone and an
    approved week's is kept in step, as every swap does.

    Everything that can refuse is asked BEFORE any model call: a day
    outside the plan's period, a day gone by, a day with nothing to fill.

    Returns {status: 'filled', filled: [{slot, meal, reason}], day,
    partial} or {status: 'refused', message} with nothing written. Each
    slot is its own transaction, so a slot that fails (both picks clash,
    or an error part-way — the model, the write) is left exactly as it was
    and the meals already filled stay: `partial` is true and the reply
    still carries the day, so the screen shows what did land. Only when
    NOTHING was filled does an error propagate.

    Not asked: whether today's breakfast or lunch hour has already passed.
    There is no shared per-slot "has gone" test (night_has_gone is
    per-day; moves.py's slot clock is private to Today's move list), so
    filling today fills all of today's left-out meals.

    `picker` is the model call, injectable so tests never touch the API.
    """
    pick_one = picker or _pick_replacement
    if not _in_plan_period(weekly_plan_id, meal_date):
        return {"status": "refused", "message": FILL_NOT_THIS_WEEK}
    if _weekly_plan.night_has_gone(meal_date):
        return {"status": "refused", "message": _weekly_plan.NIGHT_GONE}
    slots = _fillable_slots(weekly_plan_id, meal_date)
    if not slots:
        return {"status": "refused", "message": FILL_NOTHING}
    # The household's usual week (2026-09-30): the day's table is its
    # usual one — "just Emily" on a Thursday dinner is sized for Emily —
    # before anything is picked. (_fillable_slots already left out the
    # meals the usual week has off.)
    from . import usual_week as _usual_week
    _usual_week.apply_usual_attendance(_usual_week.generation_plan([meal_date]), [meal_date])

    filled = []
    missed = False
    for item in slots:
        try:
            done = _fill_one_slot(weekly_plan_id, meal_date, item, pick_one)
        except Exception:
            if not filled:
                raise
            logger.exception("fill_empty_day: %s %s failed after %d filled", meal_date, item["slot"], len(filled))
            done = None
        if done:
            filled.append(done)
        else:
            missed = True

    if not filled:
        return {"status": "refused", "message": REFUSAL}
    return {"status": "filled", "filled": filled, "partial": missed,
            "day": _refreshed_day(weekly_plan_id, meal_date)}


def _fill_one_slot(weekly_plan_id: int, meal_date: str, item: dict, pick_one) -> dict | None:
    """One left-out slot: pick, gate, save, write. None when both picks
    were turned away (nothing written)."""
    entry = {
        "entry_id": None, "date": meal_date, "slot": item["slot"], "meal": "",
        "recipe_id": None, "freeform_meal": None, "food_groups": [], "reasoning": "",
        "slot_state": "planned_empty", "derived_from": {},
    }
    tried: list[str] = []
    pick = None
    for _attempt in range(MAX_PICK_ATTEMPTS):
        context = build_swap_context(weekly_plan_id, entry, tried)
        context["note"] = _FILL_NOTE
        candidate = pick_one(context) or {}
        name = (candidate.get("meal_name") or "").strip()
        if not name:
            logger.warning("fill_empty_day came back with no dish for %s %s", meal_date, item["slot"])
            break
        candidate["meal_name"] = name
        if not _hard_clash(candidate):
            verdict = _weekly_plan._taste_verdict_for_slot(name, meal_date, item["slot"])
            if not (verdict and verdict.get("verdict") == "avoid"):
                pick = candidate
                break
        tried.append(name)
    if not pick:
        return None
    serves = _table_for(meal_date, item["slot"])["serves"]
    pick["meal_name"] = honest_meal_name(pick)
    _save_recipe_if_new(pick, serves)
    reason = (pick.get("reason") or "").strip()
    _weekly_plan._replace_slot_entries(
        weekly_plan_id, item["old_ids"], meal_date, item["slot"], pick["meal_name"],
        food_groups=[g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS],
        reasoning=reason,
        derived_from={"constraint": "filled_empty_day"},
    )
    return {"slot": item["slot"], "meal": pick["meal_name"], "reason": reason}


def honest_meal_name(pick: dict) -> str:
    """A picked dish's name, with anything its own ingredients don't back up
    taken off it.

    A pick's name and its list come out of ONE model call, so the name can
    promise something the list hasn't got — the week generator's own bug
    (Emily, 2026-09-14, "…with White Beans" and no beans), one door over.
    `taken` is this household's recipe names, because a correction that
    lands on one of them is worse than the title it fixes: the recipe is
    not saved and the slot points at a different dinner.

    Public so apply_proposal can ask for the name BEFORE deciding whether
    the pick is already what is on the slot; idempotent, so apply_pick
    asking again a moment later changes nothing.
    """
    return _plan_quality.honest_recipe_title(
        pick.get("meal_name") or "",
        pick.get("ingredients") or [],
        pick.get("instructions") or [],
        taken={(r.get("name") or "").strip().lower() for r in _recipes.list_recipes()},
    )


def apply_pick(weekly_plan_id: int, entry: dict, pick: dict, carry_sides: bool = False,
               correct_title: bool = True, group: list[dict] | None = None) -> dict:
    """
    Put an already-chosen dish on `entry`'s slot: save it as a recipe if it
    is new, swap it in through swap_meal_in_plan, and write the undo note.
    The tail of swap_meal_in_place, split out (2026-09-13) so the chat's
    change card (tools.proposals) applies a pick the household has looked
    at and saved through exactly the same door — never a second swap.
    Does NOT run the allergen or taste gates; the caller does, before.

    `carry_sides`: keep the slot's sides on the new entry (plate_parts.
    change_part — the same dish with a different protein is the same
    plate). A swap to a different dish leaves them behind, as it always
    has: the potatoes went with the chops, not with the night.

    `correct_title`: whether the pick's name may be held against its own
    ingredients (see honest_meal_name). ONE caller passes false, and it is
    not an optimisation — plate_parts.change_part builds its name with
    _variant_name, whose whole job is to produce a name nothing else is
    using, by appending "with <the protein you asked for>". That clause is
    a uniqueness device and not a description of the dish, so reading it as
    a promise is a category error: review, 2026-09-15, reproduced
    "Chili with mince" corrected back to the taken "Chili", the new recipe
    therefore not saved, and the OLD beef chili planned again and reported
    as a change.
    """
    # The BACKSTOP for a night that has already gone by, and deliberately
    # dead code from the three doors above it: swap_meal_in_place,
    # plate_parts.change_part and proposals.apply_proposal each ask
    # night_has_gone for themselves, earlier, so they can say it in their
    # own words and not spend a model call on it. It is here anyway because
    # this is the one write all three share, and a NEW door that forgets to
    # ask should get a refusal rather than the bug back — "a rule enforced
    # in one place and assumed in another" is how this class keeps
    # returning. First line of the function, so nothing is saved (this
    # would otherwise leave a recipe behind) and no connection is open.
    if _weekly_plan.night_has_gone(entry["date"]):
        raise _weekly_plan.SlotRefused(_weekly_plan.NIGHT_GONE)

    # A COOK FEEDING LATER MEALS IS ONE POT, SO THE SWAP IS ONE DISH ON ALL
    # OF THEM — widened HERE rather than at each door, which is the whole
    # shape of the fix (Gowthami's household, 2026-10-04). Every door a
    # person reaches a swap through shares this one write — "Swap · I'll
    # pick" (swap_meal_in_place), the three-picks sheet
    # (swap_options.choose_swap_option), the chat change card
    # (proposals.apply_proposal) — and before this each of them unlinked
    # the chain and left the fed meal holding the OLD dish, buying its own
    # ingredients at one table's size. Measured on an approved week:
    # Monday's chili swapped to chana masala left Tuesday's lunch as Beef
    # Chili, the list went from "Beef 2 lbs" to "Beef 1 lb" PLUS the new
    # dish for one table.
    #
    # `group` is the caller's when it has already worked it out (it asked
    # the model under that group's context, and gated every day of it);
    # otherwise this reads it, so a door that knows nothing about chains
    # still widens. A one-meal group is the one-day swap below, byte for
    # byte.
    if group is None:
        group = fed_days(weekly_plan_id, entry["entry_id"])
    if len(group) > 1:
        if correct_title:
            # Before the keeps-as-a-leftover read, because that read is of
            # the NAME and apply_pick_to_days would otherwise correct it
            # after the decision was taken on the uncorrected one.
            pick["meal_name"] = honest_meal_name(pick)
            correct_title = False
        instead = instead_of_the_leftovers(weekly_plan_id, entry, group, pick)
        # The batch the new dish is written for is the meals that KEEP it:
        # a fed meal leaving the chain is not eating out of this pot.
        keeping = [m for m in group if m["entry_id"] not in instead]
        return apply_pick_to_days(weekly_plan_id, group, pick, carry_sides=carry_sides,
                                  correct_title=correct_title, instead=instead,
                                  serves=batch_serves(weekly_plan_id, keeping, entry))

    serves = _table_for(entry["date"], entry["slot"])["serves"]
    if correct_title:
        pick["meal_name"] = honest_meal_name(pick)
    _save_recipe_if_new(pick, serves)
    sides = _plates.get_sides(entry["entry_id"]) if carry_sides else []
    # Read before the swap unlinks it: the chain this cook fed, so Undo
    # can put it back (undo_meal_swap -> weekly_plan.restore_leftover_chain).
    chain = None if entry["derived_from"].get("swapped_from") else _chain_record(weekly_plan_id, entry)
    result = _weekly_plan.swap_meal_in_plan(
        weekly_plan_id, entry["date"], pick["meal_name"], slot=entry["slot"],
        food_groups=[g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS],
    )
    new_entry_id = result["entry_id"]
    if sides:
        _plates.carry_sides(sides, new_entry_id)

    # Written once. A second swap of the same slot carries the ORIGINAL
    # forward rather than recording the dish it is replacing, so Undo means
    # "put back what was there before I started tapping" however many times
    # the household taps.
    swapped_from = entry["derived_from"].get("swapped_from") or {
        "meal": entry["meal"],
        "recipe_id": entry["recipe_id"],
        "freeform_meal": entry["freeform_meal"],
        "food_groups": entry["food_groups"],
        "reasoning": entry["reasoning"],
    }
    if chain:
        swapped_from = dict(swapped_from, chain=chain)
    derived = dict(entry["derived_from"])
    # A day swapped on its own after its dish was swapped as a whole
    # (apply_pick_to_days) leaves that group: its Undo is its own now.
    derived.pop("swap_group", None)
    # The old row's place in a leftover chain is not the new row's: the
    # swap above unlinked it (swap_meal_in_plan's docstring — the new entry
    # carries no make_double_for of its own). Copied forward, a
    # "date:slot" make_double_for re-formed the chain behind the unlink,
    # and an "entry_id:" links_to named a row that no longer exists. A
    # dish that keeps its chain goes through apply_pick_to_days, which
    # works these out afresh (replace_dish_on_days).
    for key in _weekly_plan._CHAIN_KEYS:
        derived.pop(key, None)
    derived["swapped_from"] = swapped_from
    derived["swapped_in_place_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    # Undo carries the plate back the same way (undo_meal_swap).
    derived["carry_sides"] = bool(carry_sides)
    reason = (pick.get("reason") or "").strip()
    _write_entry_note(new_entry_id, reason, derived)

    out = {
        "entry_id": new_entry_id,
        "date": entry["date"],
        "slot": entry["slot"],
        "meal": pick["meal_name"],
        "replaced": entry["meal"],
        "reason": reason,
        "can_undo": True,
        "day": _refreshed_day(weekly_plan_id, entry["date"]),
    }
    # Reported, never enforced — the same advisory a chat swap carries
    # (swap_meal_in_plan._taste_verdict_for_slot).
    if result.get("taste_verdict"):
        out["taste_verdict"] = result["taste_verdict"]
    return out


def _chain_record(weekly_plan_id: int, entry: dict) -> dict | None:
    """The leftover chain `entry` cooks for, as Undo needs it — each fed
    meal's id, slot and own link (`entry_id:` or `date:slot`, and whether
    it was a cook-ahead pick) — or None when it feeds nothing."""
    from . import leftovers as _leftovers
    try:
        source = _leftovers.plan_leftover_chains(weekly_plan_id)["sources"].get(entry["entry_id"])
    except Exception:
        logger.exception("Could not read the leftover chains; Undo will not re-link this cook")
        return None
    if not source:
        return None
    ids = [t["entry_id"] for t in source["targets"]]
    marks = ",".join("?" * len(ids))
    conn = get_conn()
    rows = conn.execute(
        f"SELECT id, derived_from_json FROM meal_plan_entries WHERE household_id = ? AND id IN ({marks})",
        (household_id(), *ids),
    ).fetchall()
    conn.close()
    derived = {r["id"]: json.loads(r["derived_from_json"] or "{}") or {} for r in rows}
    return {
        "make_double_note": entry["derived_from"].get("make_double_note") or "",
        "targets": [
            {"entry_id": t["entry_id"], "date": t["date"], "slot": t["slot"],
             "links_to": (derived.get(t["entry_id"]) or {}).get("links_to") or "",
             "cook_ahead": bool((derived.get(t["entry_id"]) or {}).get("cook_ahead"))}
            for t in source["targets"]
        ],
    }


def pick_gate(pick: dict, entry: dict) -> str | None:
    """
    Why this pick must not be written, or None when it may be. The two
    gates swap_meal_in_place runs between picking and applying — the hard
    allergen match and Emily's one-veto taste rule — as one call, so the
    change card runs the same two and no fewer.
    """
    name = (pick.get("meal_name") or "").strip()
    if not name:
        return "no dish"
    clash = _hard_clash(pick)
    if clash:
        who = ", ".join(sorted({c.get("restriction") or "" for c in clash if c.get("restriction")}))
        return f"clashes with {who}" if who else "clashes with something this house can't have"
    verdict = _weekly_plan._taste_verdict_for_slot(name, entry["date"], entry["slot"])
    if verdict and verdict.get("verdict") == "avoid":
        who = ", ".join(verdict.get("vetoed_by") or []) or "someone at the table"
        return f"{who} would rather not"
    return None


def undo_meal_swap(weekly_plan_id: int, entry_id: int) -> dict:
    """
    Put back the dish that was on this slot before the first in-place swap
    of it, and forget that it was ever swapped.

    Goes back through swap_meal_in_plan for the same reason the swap does:
    the grocery reversal, the chain unlink and the plate rules are that
    function's job, and an undo that wrote the row directly would be a
    second, subtly different swap.
    """
    entry = _entry(weekly_plan_id, entry_id)
    previous = (entry["derived_from"] or {}).get("swapped_from") or {}
    name = (previous.get("meal") or "").strip()
    if not name:
        raise ValueError("That meal hasn't been swapped, so there's nothing to put back.")
    # Swapped as a whole dish: every day of it goes back, together.
    if (entry["derived_from"] or {}).get("swap_group"):
        return _undo_dish_swap(weekly_plan_id, entry)

    # A change that carried the plate's sides forward carries them back.
    sides = _plates.get_sides(entry_id) if (entry["derived_from"] or {}).get("carry_sides") else []
    result = _weekly_plan.swap_meal_in_plan(
        weekly_plan_id, entry["date"], name, slot=entry["slot"],
        food_groups=previous.get("food_groups") or [],
    )
    if sides:
        _plates.carry_sides(sides, result["entry_id"])
    derived = {k: v for k, v in (entry["derived_from"] or {}).items()
               if k not in _SWAP_NOTE_KEYS}
    _write_entry_note(result["entry_id"], previous.get("reasoning") or "", derived)
    # A cook whose leftover meals the swap cut loose feeds them again —
    # the ones still as the swap left them (restore_leftover_chain).
    if previous.get("chain"):
        _weekly_plan.restore_leftover_chain(weekly_plan_id, result["entry_id"], previous["chain"])
    return {
        "status": "restored",
        "entry_id": result["entry_id"],
        "date": entry["date"],
        "slot": entry["slot"],
        "meal": name,
        "day": _refreshed_day(weekly_plan_id, entry["date"]),
    }


# What a swap writes on derived_from about itself, and what an undo takes
# back off. `swap_group` is the token apply_pick_to_days puts on every day
# of a dish it swapped together, so Undo on any one of them finds the rest.
_SWAP_NOTE_KEYS = ("swapped_from", "swapped_in_place_at", "carry_sides", "swap_group")


# ---------- a whole dish, on every day it's planned ----------


def _menu_dish_name(slot: dict) -> str:
    """The name a slot READS as on "What we're eating" — shell.js
    mealDisplayName, exactly: a reheat night is the dish it reheats."""
    leftover = slot.get("leftover_from") or {}
    if leftover.get("meal"):
        return leftover["meal"]
    return slot.get("title") or ""


def dish_days(weekly_plan_id: int, entry_id: int) -> list[dict]:
    """
    Every day still ahead that the menu row holding `entry_id` stands for,
    as _entry dicts in date order — the tapped one always among them.

    The grouping is the screen's own and has to be: "What we're eating"
    (shell.js wkMenuGroups, Emily 2026-09-15 / 2026-09-21) draws one row
    per DISH per meal type, and a Swap on that row has to change exactly
    the days the row says ("Thu, Fri") — no more, no fewer. So this reads
    the same payload the screen reads (get_week_menu) and groups it by the
    same rule: days that aren't before the plan's start; breakfast, lunch
    and dinner by their own slot and every snack as one "snack" type; only
    `planned` slots; the name the slot READS as (_menu_dish_name —
    a reheat night is its cook's dish), trimmed and compared
    case-insensitively. Emily, 2026-09-22: that row's Swap changed the
    first day only and left the dish on the others — two meals where she
    wanted one.

    Then two kinds of day are left where they are, because swapping them
    would rewrite something that has already happened: a day the menu
    marks `is_past` (the row's own Swap is only offered while a day is
    ahead; night_has_gone is the same test on the same clock), and a slot
    already ticked cooked (get_week_menu's `cooked` — "a day already
    cooked is not a day to plan into"). The tapped entry itself is kept
    whatever it is, so a row with one day ahead swaps that one day,
    exactly as it always has.
    """
    entry = _entry(weekly_plan_id, entry_id)
    try:
        menu = _weekly_plan.get_week_menu(weekly_plan_id)
    except Exception:
        logger.exception("Could not read the week to find the rest of a dish; swapping the one day")
        return [entry]
    rows = []
    for day in menu.get("days") or []:
        if day.get("before_plan_start"):
            continue
        slots = [(s, day.get(s)) for s in ("breakfast", "lunch", "dinner")]
        slots += [("snack", s) for s in (day.get("snacks") or [])]
        for kind, slot in slots:
            if not slot or slot.get("state") != "planned":
                continue
            name = _menu_dish_name(slot).strip().lower()
            if name:
                rows.append((kind, name, day, slot))
    mine = next(((kind, name) for kind, name, _day, slot in rows if slot.get("entry_id") == entry_id), None)
    if mine is None:
        return [entry]
    ids = [
        slot["entry_id"] for kind, name, day, slot in rows
        if (kind, name) == mine and (
            slot["entry_id"] == entry_id
            or (not day.get("is_past") and not slot.get("cooked"))
        )
    ]
    return [entry if i == entry_id else _entry(weekly_plan_id, i) for i in ids]


def batch_days(weekly_plan_id: int, entry_id: int) -> list[dict]:
    """
    dish_days, widened along the leftover chains: every meal still ahead
    that eats out of the same cook as one of those days, whatever meal
    type it sits in. The menu draws Thursday's dinner and the Friday lunch
    eating its leftovers as two rows (lunch and dinner are separate
    types), so dish_days alone leaves the lunch behind — but a change to
    the recipe (plate_parts.change_part) is a change to the POT, and every
    meal served out of it is the changed dish (Emily, 2026-09-27: a veg
    change on Thursday's gochujang beef left Friday's lunch on the old
    recipe). Same filter as dish_days: a day gone by or already cooked
    stays as it is. In date order; the tapped entry always among them.
    """
    return _along_chains(weekly_plan_id, dish_days(weekly_plan_id, entry_id))


def chain_days(weekly_plan_id: int, entry_id: int) -> list[dict]:
    """
    The tapped meal and every meal still ahead that eats out of the same
    cook — its cook, and the meals eating that cook's leftovers — and
    nothing else: a separate fresh cook of the same dish on another day is
    its own pot. What a CHANGE to the recipe reaches (plate_parts.
    change_part — the card: "every meal it feeds"), where a Swap on the
    menu row (batch_days) is whole-dish by design. Same filter as
    dish_days; in date order.
    """
    return _along_chains(weekly_plan_id, [_entry(weekly_plan_id, entry_id)])


def fed_days(weekly_plan_id: int, entry_id: int) -> list[dict]:
    """
    The tapped meal and every meal still ahead that eats out of ITS cook —
    DOWNSTREAM only, never up. `[entry]` when it feeds nothing, which is
    most meals.

    This is what a SWAP widens to, from every door (Gowthami's household,
    2026-10-04: "if we change one recipe that should be for dinner, then
    lunch the next day, it's not changing the lunch the next day for the
    quantity and including it as well"). A dinner cooked double for
    tomorrow's lunch is ONE pot, so changing what is in it changes both
    meals, and the batch the new dish is written and shopped for is both
    tables.

    Downstream only, and that is the whole difference from chain_days.
    Swapping a REHEAT night is the household saying "not Monday's chili
    again on Tuesday" — an answer about that one meal, which leaves the
    cook alone and takes the night out of the chain, exactly as it always
    has. Widening it upward would rewrite Monday's dinner on the strength
    of a tap about Tuesday's lunch. (The menu row's Swap is never offered
    on a leftovers row at all — shell.js wkMenuRowHtml — so the doors that
    reach a reheat are the Day and Meal steps' own Swap.)
    """
    return _along_chains(weekly_plan_id, [_entry(weekly_plan_id, entry_id)], downstream_only=True)


def _along_chains(weekly_plan_id: int, group: list[dict], downstream_only: bool = False) -> list[dict]:
    """`group`, widened along the plan's confirmed leftover chains to
    every linked meal still ahead and not cooked. `downstream_only` walks
    from a cook to the meals it feeds and never from a reheat to its cook
    (fed_days); left off it walks both ways (chain_days, batch_days)."""
    from . import leftovers as _leftovers

    try:
        chains = _leftovers.plan_leftover_chains(weekly_plan_id)
        menu = _weekly_plan.get_week_menu(weekly_plan_id)
    except Exception:
        logger.exception("Could not read the leftover chains; changing the dish's own days")
        return group
    ahead: dict[int, tuple] = {}
    for day in menu.get("days") or []:
        if day.get("before_plan_start") or day.get("is_past"):
            continue
        slots = [day.get(s) for s in ("breakfast", "lunch", "dinner")] + list(day.get("snacks") or [])
        for slot in slots:
            if slot and slot.get("state") == "planned" and slot.get("entry_id") and not slot.get("cooked"):
                ahead[slot["entry_id"]] = day.get("date") or ""
    ids = [e["entry_id"] for e in group]
    grew = True
    while grew:
        grew = False
        for i in list(ids):
            linked = [t["entry_id"] for t in (chains["sources"].get(i) or {}).get("targets") or []]
            reheat = None if downstream_only else chains["leftovers"].get(i)
            if reheat:
                linked.append(reheat["source"]["entry_id"])
            for j in linked:
                if j not in ids and j in ahead:
                    ids.append(j)
                    grew = True
    if len(ids) == len(group):
        return group
    have = {e["entry_id"]: e for e in group}
    members = [have.get(i) or _entry(weekly_plan_id, i) for i in ids]
    order = {s: n for n, s in enumerate(("breakfast", "lunch", "dinner", "snack"))}
    return sorted(members, key=lambda e: (e["date"], order.get(e["slot"], 9), e["entry_id"]))


def batch_serves(weekly_plan_id: int, group: list[dict], entry: dict) -> int:
    """
    How many servings a dish replacing `group` is written for: the batch
    its cook makes (leftovers.batch_for_entry — the same count the Cook card's
    "Cooking for" reads, cooker.py) when one of `group` is a cook feeding
    others in the group or putting portions by for the freezer; the tapped
    meal's own table otherwise, as it always was. A reheat night the
    change leaves behind (already cooked) is not counted — the write
    unlinks it (replace_dish_on_days). Shared by plate_parts.change_part
    and swap_options.choose_swap_option's whole-dish Swap.
    """
    from . import leftovers as _leftovers
    table = _table_for(entry["date"], entry["slot"])["serves"]
    try:
        chains = _leftovers.plan_leftover_chains(weekly_plan_id)
    except Exception:
        logger.exception("Could not read the leftover chains; writing the dish for one table")
        return table
    ids = {e["entry_id"] for e in group}
    best = 0
    for member in group:
        source = chains["sources"].get(member["entry_id"])
        if source:
            source = dict(source, targets=[t for t in source["targets"] if t["entry_id"] in ids])
            batch = _leftovers.batch_for_source(source)
        else:
            batch = _leftovers.batch_for_entry(member["entry_id"], chains)
        if batch and batch["servings"] > best:
            best = batch["servings"]
    return best or table


def _intake_for(weekly_plan_id: int) -> dict | None:
    week_start = _week_start_of(weekly_plan_id)
    return _week_intake.get_week_intake(week_start) if week_start else None


def _night_tags_for(weekly_plan_id: int) -> dict:
    return (_intake_for(weekly_plan_id) or {}).get("night_tags") or {}


def _lunch_kind(intake: dict | None, entry: dict) -> str | None:
    if (entry.get("slot") or "dinner") != "lunch":
        return None
    return _weekday_lunches.kinds_by_date(intake).get(entry.get("date"))


def day_caps(weekly_plan_id: int, entries: list[dict]) -> list[tuple[dict, int | None]]:
    """Each entry's own minutes cap (_minutes_cap, for its date AND slot),
    in `entries` order."""
    memory = _memory.get_household_memory()
    intake = _intake_for(weekly_plan_id)
    tags_by_date = (intake or {}).get("night_tags") or {}
    return [(e, _minutes_cap(e, tags_by_date.get(e["date"]) or [], memory, _lunch_kind(intake, e)))
            for e in entries]


def build_dish_swap_context(weekly_plan_id: int, entries: list[dict], avoid: list[str] | None = None) -> dict:
    """
    build_swap_context for a dish being swapped on several days at once
    (apply_pick_to_days), held to the STRICTEST of those days — Emily's
    standing rule, 2026-09-22: a suggestion always fits the week's
    guidelines, so one pick that lands on Thursday AND Friday has to fit
    both. The tapped day's context is the base; on top of it:

      * `max_minutes` is the lowest cap any of the days has — a `rush`
        night's or the weeknight limit. An `unrushed` night lifts only its
        OWN cap (it contributes no cap, and never lifts another day's);
      * `night_tags` is every day's tags together, except that `unrushed`
        and `normal` are only said when every day says them — "no time
        cap tonight" beside a rush night would contradict the cap above;
      * `table` is everyone who eats on any of the days (present is the
        union, `away` only those out for all of them, `serves` and
        `guests` the largest), and `taste_verdicts` covers each day's own
        table, so one person's veto on one of the days still counts;
      * `dates` / `weekdays` say which days, and `week_other_dishes`
        leaves out every one of them (they are the dish being replaced).

    choose_swap_option re-checks each day against its OWN cap at the tap
    (cap_gate) — the picks are asked under the lowest cap, but the recipe
    written out afterwards can run longer than the line promised.
    """
    first = entries[0]
    context = build_swap_context(weekly_plan_id, first, avoid)
    tags_by_date = _night_tags_for(weekly_plan_id)
    every = [tags_by_date.get(e["date"]) or [] for e in entries]
    tags: list[str] = []
    for day_tags in every:
        for tag in day_tags:
            if tag in ("unrushed", "normal") and not all(tag in t for t in every):
                continue
            if tag not in tags:
                tags.append(tag)
    caps = [cap for _e, cap in day_caps(weekly_plan_id, entries) if cap]
    tables = [_table_for(e["date"], e["slot"]) for e in entries]
    present: list[str] = []
    for table in tables:
        present += [n for n in table["present"] if n not in present]
    away = [n for n in tables[0]["away"] if all(n in t["away"] for t in tables) and n not in present]
    context.update({
        "dates": [e["date"] for e in entries],
        "weekdays": [_weekday(e["date"]) for e in entries],
        "night_tags": tags,
        "max_minutes": min(caps) if caps else None,
        "table": {
            "serves": max(t["serves"] for t in tables),
            "present": present,
            "away": away,
            "guests": max(t["guests"] for t in tables),
        },
    })
    context["week_other_dishes"] = [
        line for line in _other_dishes(weekly_plan_id, first["entry_id"])
        if not any(line.startswith(f"{_weekday(e['date'])} {e['slot']}:") for e in entries)
    ]
    taste: list[str] = []
    for entry, table in zip(entries, tables):
        for line in _taste_lines_for(entry["date"], entry["slot"], table):
            if line not in taste:
                taste.append(line)
    if taste:
        context["taste_verdicts"] = taste
    else:
        context.pop("taste_verdicts", None)
    return context


def _pick_minutes(pick: dict) -> int | None:
    if pick.get("minutes") is not None:
        try:
            return int(pick["minutes"]) or None
        except (TypeError, ValueError):
            pass
    total = int(pick.get("prep_time_minutes") or 0) + int(pick.get("cook_time_minutes") or 0)
    return total or None


def cap_gate(weekly_plan_id: int, pick: dict, entries: list[dict]) -> str | None:
    """
    Why `pick` takes too long for one of `entries`, each held to its OWN
    cap (a rush Friday is 30 minutes even when Thursday has none), or
    None. A pick that says no minutes at all isn't refused on a guess.
    """
    minutes = _pick_minutes(pick)
    if not minutes:
        return None
    for entry, cap in day_caps(weekly_plan_id, entries):
        if cap and minutes > cap:
            return f"takes {minutes} minutes, and {_weekday(entry['date'])} only has {cap}"
    return None


# The confirmation a swap that changed more than one meal says back
# (Gowthami's household, 2026-10-04: a dinner cooked double for the next
# day's lunch is two meals, and the toast named one). Built HERE, beside
# the rows that were written, rather than composed by the screen: the
# sentence counts meals, and this app's own rule is that copy which counts
# things is written where the counting happens (weekly_plan.week_receipt,
# draft_opener) so the words and the week cannot drift apart.
#
# "Swapped to Chana Masala for Monday dinner and Tuesday lunch." — the
# days in the order they fall, each with its own meal word, because a
# dinner and the lunch eating it are two different meals of two different
# days and "Monday and Tuesday" would not say which.
def swapped_said(meal: str, entries: list[dict]) -> str:
    when = [f"{_weekday(e['date'])} {e.get('slot') or 'dinner'}".strip() for e in entries]
    when = [w for w in when if w]
    if not when:
        return ""
    if len(when) == 1:
        joined = when[0]
    else:
        joined = ", ".join(when[:-1]) + " and " + when[-1]
    return f"Swapped to {meal} for {joined}."


# A fed meal that cannot hold the new dish, said back as the one sentence
# the household reads (Gowthami's card, criterion 5). It names what it put
# there rather than only what it took away, which is the difference
# between Pomona doing the planning work and handing it back.
def refilled_said(meal: str, refilled: list[dict]) -> str:
    def when(r):
        return f"{_weekday(r['date'])} {r.get('slot') or 'lunch'}".strip()
    put = [r for r in refilled if r.get("meal")]
    left = [r for r in refilled if not r.get("meal")]
    if not put and not left:
        return ""
    bits = []
    if put:
        bits.append("I’ve put " + ", ".join(f"{r['meal']} on {when(r)}" for r in put))
    if left:
        joined = ", ".join(when(r) for r in left)
        bits.append(f"{joined} {'are' if len(left) > 1 else 'is'} yours to fill")
    return f"{meal} won’t keep, so " + " and ".join(bits) + "."


def instead_of_the_leftovers(weekly_plan_id: int, entry: dict, group: list[dict],
                             pick: dict) -> dict:
    """
    What to put on the meals that were eating `entry`'s leftovers when the
    dish replacing it will not keep (leftovers.keeps_as_leftovers).

    `{entry_id: {meal, food_groups, reasoning}}`, one per fed meal, for
    apply_pick_to_days' `instead` — a `meal` of "" means the meal has
    nothing to repeat and becomes an open question. Empty dict when the
    dish keeps, which is almost every swap.

    Criterion 5 of the card, and the reason it is not "leave the lunch as
    it was": leaving it is the bug, said quietly — the household gets
    tomorrow's lunch as yesterday's dish with its own ingredients bought
    at one table's size, and nothing anywhere says the pot they thought
    they were changing is now two different meals. So the fed meal leaves
    the chain and is filled from the week's own dishes for that slot
    (meal_variety.repeat_for_slot — the same rule generation uses to make
    "a breakfast or lunch is never open" true), held to that meal's own
    time cap because it is a fresh cook now rather than a reheat.

    A fed DINNER is left as an open question rather than filled, and that
    is deliberate: meal_variety.NEVER_OPEN_SLOTS is breakfast and lunch,
    because a dinner genuinely is a decision (2026-09-27), and quietly
    repeating a dinner nobody asked for is the opposite of what the
    household wants.
    """
    from . import leftovers as _leftovers
    from . import meal_variety as _meal_variety

    if _leftovers.keeps_as_leftovers(pick):
        return {}
    out: dict[int, dict] = {}
    for member, cap in day_caps(weekly_plan_id, [m for m in group if m["entry_id"] != entry["entry_id"]]):
        repeat = None
        if member["slot"] in _meal_variety.NEVER_OPEN_SLOTS:
            # Its own cap as a FRESH cook: _minutes_cap lifts a weekday
            # lunch's 20 minutes for either end of a chain, and this meal
            # is about to stop being one.
            fresh = _time_caps.minutes_cap(
                member["date"], member["slot"],
                _night_tags_for(weekly_plan_id).get(member["date"]) or [],
                _memory.get_household_memory(), is_leftovers=False,
                lunch_kind=_lunch_kind(_intake_for(weekly_plan_id), member))
            repeat = _meal_variety.repeat_for_slot(
                weekly_plan_id, member["slot"], member["date"],
                cap=fresh if fresh is not None else cap, avoid=entry["meal"])
        out[member["entry_id"]] = {
            "meal": (repeat or {}).get("name") or "",
            "food_groups": (repeat or {}).get("food_groups") or [],
            "reasoning": _meal_variety.GAP_FILL_REASON if repeat else "",
        }
    return out


def apply_pick_to_days(weekly_plan_id: int, entries: list[dict], pick: dict,
                       carry_sides: bool = False, correct_title: bool = True,
                       serves: int | None = None, instead: dict[int, dict] | None = None) -> dict:
    """
    apply_pick for a dish planned on several days: the same chosen dish on
    every one of `entries` (dish_days' answer), in ONE transaction
    (weekly_plan.replace_dish_on_days — which also keeps a cook + reheat
    shape a cook + reheat shape). Everything else is apply_pick's, per day:
    the honest title, the recipe saved once, the undo note written once
    per day and carrying the ORIGINAL forward. Every day also carries one
    `swap_group` token, which is how Undo on any of them puts them all
    back (undo_meal_swap -> _undo_dish_swap).

    Like apply_pick, runs no gates — the caller does, for every day — and
    refuses a day that has gone by as the backstop, before anything is
    saved.

    `instead` is {entry_id: {meal, food_groups, reasoning}} for a day that
    gets a DIFFERENT dish — a meal that was eating the swapped cook's
    leftovers when the new dish will not keep as one
    (instead_of_the_leftovers, 2026-10-04). Those days leave the chain, so
    the cook stops cooking double for them and each buys for itself
    (replace_dish_on_days' `chain`), and their prior chain is recorded on
    the undo note so Undo can put it back — the only thing that cannot be
    re-derived at undo time, since by then there is no chain on the plan
    to read. A `meal` of "" is a day with nothing to repeat: it becomes an
    open question, as an un-answerable breakfast or lunch does anywhere
    else.

    `carry_sides` and `correct_title` are apply_pick's own, for the same
    one caller: plate_parts.change_part (a different protein/veg/carb in a
    dish planned on several days — Emily, 2026-09-27: her gochujang beef
    changed on Thursday only, "Cooking for 2", Friday's lunch and dinner
    still the old dish). Each day keeps ITS OWN sides, and Undo carries
    them back (_undo_dish_swap). `serves` is what a new recipe is saved
    for when the pick doesn't say — the change asks for the whole batch.
    """
    for entry in entries:
        if _weekly_plan.night_has_gone(entry["date"]):
            raise _weekly_plan.SlotRefused(_weekly_plan.NIGHT_GONE)
    # Each day's own time cap, as the backstop (choose_swap_option asks
    # first, in its own words): nothing is saved or written past this.
    too_long = cap_gate(weekly_plan_id, pick, entries)
    if too_long:
        raise _weekly_plan.SlotRefused(f"I left it as it was — {pick.get('meal_name') or 'that'} {too_long}.")
    first = entries[0]
    if correct_title:
        pick["meal_name"] = honest_meal_name(pick)
    _save_recipe_if_new(pick, serves or _table_for(first["date"], first["slot"])["serves"])
    # Read before the write: the rows they hang off are about to go.
    sides_by_entry = {e["entry_id"]: _plates.get_sides(e["entry_id"]) for e in entries} if carry_sides else {}
    token = uuid.uuid4().hex
    now = datetime.datetime.now().isoformat(timespec="seconds")
    reason = (pick.get("reason") or "").strip()
    food_groups = [g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS]
    instead = dict(instead or {})
    # Nothing to repeat on a day in `instead` means it is handed back as a
    # question rather than planned; that cannot be one row of this write
    # (replace_dish_on_days plants a dish on every item), so it is opened
    # FIRST, one transaction each, and the day is kept out of the group.
    #
    # First rather than last, which is not a tidiness choice: the row has
    # to be gone before the group's own write reads the chains, or the
    # cook's make_double_for is still confirmed by it, the cook is bought
    # for a batch nobody eats, and the orphan handling buys the OLD dish's
    # ingredients for a night about to become a question.
    to_open = [e for e in entries if e["entry_id"] in instead and not instead[e["entry_id"]].get("meal")]
    entries = [e for e in entries if e not in to_open]
    def undo_note(entry: dict) -> dict:
        swapped_from = entry["derived_from"].get("swapped_from") or {
            "meal": entry["meal"],
            "recipe_id": entry["recipe_id"],
            "freeform_meal": entry["freeform_meal"],
            "food_groups": entry["food_groups"],
            "reasoning": entry["reasoning"],
        }
        if instead:
            # The chain this row is in NOW, recorded so Undo can put it
            # back: these rows leave the chain, so by undo time there is
            # nothing on the plan to re-derive one from.
            was = {k: v for k, v in entry["derived_from"].items() if k in _weekly_plan._CHAIN_KEYS}
            if was:
                swapped_from = dict(swapped_from, chain_fields=was)
        derived = dict(entry["derived_from"])
        derived.update({"swapped_from": swapped_from, "swapped_in_place_at": now,
                        "carry_sides": bool(carry_sides), "swap_group": token})
        return derived

    refilled = []
    for entry in to_open:
        try:
            # Carries the same undo note and the same swap_group token as
            # the rows below, so Undo puts this day back with them
            # (_undo_dish_swap finds the group by that token).
            # Its place in the chain comes OFF the row (it is leaving the
            # chain) and is kept only on the undo note, where Undo reads
            # it: an open row is not a chain target to
            # plan_leftover_chains, so a links_to left on it would be
            # inert and misleading at once.
            note = {k: v for k, v in undo_note(entry).items() if k not in _weekly_plan._CHAIN_KEYS}
            note["instead_of_leftovers"] = {"of": entry["meal"], "for": pick["meal_name"]}
            if _weekly_plan.open_slot_instead_of(
                    weekly_plan_id, entry["entry_id"],
                    f"{pick['meal_name']} won’t keep, so this one is yours to fill.",
                    derived_from=note) is None:
                continue
        except Exception:
            logger.exception("Could not open %s %s after a swap that won't keep",
                             entry["date"], entry["slot"])
            continue
        refilled.append({"date": entry["date"], "slot": entry["slot"], "meal": ""})

    items = []
    for entry in entries:
        derived = undo_note(entry)
        other = instead.get(entry["entry_id"])
        item = {"old_entry_id": entry["entry_id"], "date": entry["date"], "slot": entry["slot"],
                "new_meal": pick["meal_name"], "food_groups": food_groups,
                "reasoning": reason, "derived_from": derived}
        if other:
            item.update({"new_meal": other["meal"], "food_groups": other.get("food_groups") or [],
                         "reasoning": other.get("reasoning") or "", "chain": {}})
            derived["instead_of_leftovers"] = {"of": entry["meal"], "for": pick["meal_name"]}
        items.append(item)
    result = _weekly_plan.replace_dish_on_days(weekly_plan_id, items)
    for entry, new_id in zip(entries, result["entry_ids"]):
        if sides_by_entry.get(entry["entry_id"]):
            _plates.carry_sides(sides_by_entry[entry["entry_id"]], new_id)
    days = _refreshed_days(weekly_plan_id, [e["date"] for e in entries])
    out = {
        "entry_id": result["entry_ids"][0],
        "entry_ids": result["entry_ids"],
        "date": first["date"],
        "dates": [e["date"] for e in entries],
        "slot": first["slot"],
        "meal": pick["meal_name"],
        "replaced": first["meal"],
        "reason": reason,
        "can_undo": True,
        "day": days[0] if days else None,
        "days": days,
        # Which meals, not only which days: a dinner and the lunch eating
        # its leftovers are two meal types on two dates (batch_days,
        # fed_days), so `dates` alone cannot say what changed.
        "meals": [{"date": e["date"], "slot": e["slot"]} for e in entries],
    }
    refilled += [{"date": e["date"], "slot": e["slot"], "meal": instead[e["entry_id"]]["meal"]}
                 for e in entries if e["entry_id"] in instead]
    if len(entries) > 1 or refilled:
        out["said"] = swapped_said(pick["meal_name"], [e for e in entries
                                                       if e["entry_id"] not in instead])
    if refilled:
        refilled.sort(key=lambda r: (r["date"], r["slot"]))
        out["refilled"] = refilled
        out["said"] = (out["said"] + " " + refilled_said(pick["meal_name"], refilled)).strip()
    verdict = _weekly_plan._taste_verdict_for_slot(pick["meal_name"], first["date"], first["slot"])
    if verdict:
        out["taste_verdict"] = verdict
    return out


def _undo_dish_swap(weekly_plan_id: int, entry: dict) -> dict:
    """
    Put back every day of a dish swapped as a whole — each day its OWN
    dish from before (a reheat night was its own row, and gets its own row
    back), in one transaction, with the chain shape kept the same way the
    swap kept it. Days of the group that have since gone by are left as
    they are, like every other change to a night that's over; a day
    swapped on its own since then has left the group (apply_pick drops
    its token) and keeps its own Undo.
    """
    token = entry["derived_from"]["swap_group"]
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, derived_from_json FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL "
        "ORDER BY date ASC, id ASC",
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    ids = [r["id"] for r in rows if (json.loads(r["derived_from_json"] or "{}") or {}).get("swap_group") == token]
    members = [_entry(weekly_plan_id, i) for i in ids]
    members = [m for m in members if m["entry_id"] == entry["entry_id"] or not _weekly_plan.night_has_gone(m["date"])]
    items = []
    carried = []  # each item's sides, when the change carried them (change_part)
    for m in members:
        previous = m["derived_from"].get("swapped_from") or {}
        name = (previous.get("meal") or "").strip()
        if not name:
            continue
        carried.append(_plates.get_sides(m["entry_id"]) if m["derived_from"].get("carry_sides") else [])
        item = {
            "old_entry_id": m["entry_id"], "date": m["date"], "slot": m["slot"], "new_meal": name,
            "food_groups": previous.get("food_groups") or [],
            "reasoning": previous.get("reasoning") or "",
            "derived_from": {k: v for k, v in m["derived_from"].items()
                             if k not in _SWAP_NOTE_KEYS and k != "instead_of_leftovers"},
        }
        # A swap that took a meal OUT of the chain (instead_of_leftovers)
        # left nothing on the plan for replace_dish_on_days to re-derive
        # the chain from, so the shape recorded at swap time is handed
        # back. Every other undo leaves it to be re-derived, because the
        # chain is still there to read — and a recorded one could name a
        # reheat night the swap deliberately left behind.
        if previous.get("chain_fields") is not None:
            item["chain"] = previous["chain_fields"]
        items.append(item)
    result = _weekly_plan.replace_dish_on_days(weekly_plan_id, items)
    for sides, new_id in zip(carried, result["entry_ids"]):
        if sides:
            _plates.carry_sides(sides, new_id)
    dates = [i["date"] for i in items]
    days = _refreshed_days(weekly_plan_id, dates)
    mine = next((d for d in days if d.get("date") == entry["date"]), days[0] if days else None)
    return {
        "status": "restored",
        "entry_id": next((new for item, new in zip(items, result["entry_ids"])
                          if item["old_entry_id"] == entry["entry_id"]), result["entry_ids"][0]),
        "entry_ids": result["entry_ids"],
        "date": entry["date"],
        "dates": dates,
        "slot": entry["slot"],
        "meal": (entry["derived_from"].get("swapped_from") or {}).get("meal") or "",
        "day": mine,
        "days": days,
    }


def _refreshed_days(weekly_plan_id: int, dates: list[str]) -> list[dict]:
    """_refreshed_day for several dates off ONE read of the week."""
    try:
        menu = _weekly_plan.get_week_menu(weekly_plan_id)
    except Exception:
        logger.exception("Could not re-read the week after a swap")
        return []
    wanted = list(dict.fromkeys(dates))
    by_date = {d.get("date"): d for d in menu.get("days") or []}
    return [by_date[d] for d in wanted if d in by_date]


def _refreshed_day(weekly_plan_id: int, meal_date: str) -> dict | None:
    """
    The changed day as the Meals screen already reads it. Handing back
    get_week_menu's own day dict rather than a bespoke shape is what lets
    the front end splice one day into the week it is holding without a
    second round trip and without a second renderer.
    """
    try:
        menu = _weekly_plan.get_week_menu(weekly_plan_id)
    except Exception:
        logger.exception("Could not re-read the week after a swap")
        return None
    for day in menu.get("days") or []:
        if day.get("date") == meal_date:
            return day
    return None
