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
this path adds — see swap_meal_in_plan's own docstring.
"""
from __future__ import annotations

import datetime
import json
import logging

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
from . import week_intake as _week_intake
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


def _minutes_cap(meal_date: str, tags: list[str], memory: dict) -> int | None:
    """
    The real cap on this night's cooking, or None. Same order agent.py's
    plate pass uses: a `rush` tag wins, an `unrushed` tag lifts the cap,
    then the household's weeknight cap on a Monday-Friday. A weekend with
    no rush tag has no cap, which is the truth rather than a number
    invented to look precise.
    """
    if "rush" in tags:
        return _week_intake.RUSH_MAX_MINUTES
    if "unrushed" in tags:
        return None
    cap = memory.get("weeknight_max_minutes") or 0
    try:
        weekday = datetime.date.fromisoformat(meal_date).weekday()
    except (TypeError, ValueError):
        return None
    return cap if (cap and weekday < 5) else None


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
        "plate_rule": list(_plates.plate_rule(memory.get("eating_style"))),
        "table": table,
        "night_tags": tags,
        "max_minutes": _minutes_cap(entry["date"], tags, memory),
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
- `plate_rule` is what a full plate covers here. Dinner and lunch cover all of it — plan the side \
INTO the dish (in the name, the ingredients and the steps) rather than leaving the plate short. \
Breakfast and snack cover at least two of those groups.
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
    try:
        hits = _coordination.check_meal_conflicts(
            pick.get("meal_name") or "",
            ingredients=_clean_ingredients(pick.get("ingredients")),
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
    for attempt in range(1, MAX_PICK_ATTEMPTS + 1):
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
            verdict = _weekly_plan._taste_verdict_for_slot(name, entry["date"], entry["slot"])
            if not (verdict and verdict.get("verdict") == "avoid"):
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
    out = apply_pick(weekly_plan_id, entry, pick)
    out["status"] = "swapped"
    # Both spellings: `tried` caught the name the model wrote, and
    # apply_pick may have shortened it. The screen sends this straight back
    # as the next tap's `avoid`, and it has to name the dish it can see.
    out["avoid"] = _dedup(tried + [out["meal"]])
    return out


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
               correct_title: bool = True) -> dict:
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

    serves = _table_for(entry["date"], entry["slot"])["serves"]
    if correct_title:
        pick["meal_name"] = honest_meal_name(pick)
    _save_recipe_if_new(pick, serves)
    sides = _plates.get_sides(entry["entry_id"]) if carry_sides else []
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
    derived = dict(entry["derived_from"])
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

    # A change that carried the plate's sides forward carries them back.
    sides = _plates.get_sides(entry_id) if (entry["derived_from"] or {}).get("carry_sides") else []
    result = _weekly_plan.swap_meal_in_plan(
        weekly_plan_id, entry["date"], name, slot=entry["slot"],
        food_groups=previous.get("food_groups") or [],
    )
    if sides:
        _plates.carry_sides(sides, result["entry_id"])
    derived = {k: v for k, v in (entry["derived_from"] or {}).items()
               if k not in ("swapped_from", "swapped_in_place_at", "carry_sides")}
    _write_entry_note(result["entry_id"], previous.get("reasoning") or "", derived)
    return {
        "status": "restored",
        "entry_id": result["entry_id"],
        "date": entry["date"],
        "slot": entry["slot"],
        "meal": name,
        "day": _refreshed_day(weekly_plan_id, entry["date"]),
    }


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
