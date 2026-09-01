"""
Household coordination and trust: plan conflicts, explaining a choice,
feedback nudges, and who is in the household.
"""
from __future__ import annotations

import re
from ..db import get_conn
from ._shared import household_id
from . import household as _household
from . import memory as _memory
from . import recipes as _recipes
from . import weekly_plan as _weekly_plan


def check_plan_conflicts(weekly_plan_id: int | None = None) -> dict:
    """
    Flag (don't block) any meals on a plan that look like they clash with a
    member's saved dietary restriction/allergy (set_member_dietary_restrictions)
    — a simple keyword match between the restriction and the recipe's
    ingredients, e.g. "peanut allergy" against a recipe listing "peanut
    butter". This is a soft warning surfaced before approval, not a hard
    block — the plan can still be approved as-is if the conflict is
    intentional or a false positive from the keyword match. Call this right
    before approve_weekly_plan and mention any conflicts found so the
    Planner can decide, rather than silently approving.
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {"weekly_plan_id": None, "conflicts": []}

    members = _household.list_members()
    restrictions = [(m["name"], r.lower()) for m in members for r in m["dietary_restrictions"] if r.strip()]
    if not restrictions:
        return {"weekly_plan_id": plan["weekly_plan_id"], "conflicts": []}

    recipes_by_name = {r["name"].lower(): r for r in _recipes.list_recipes()}
    conflicts = []
    for meal in plan["meals"]:
        recipe = recipes_by_name.get((meal["meal"] or "").lower())
        if not recipe:
            continue
        ingredient_text = " ".join((i.get("item") or "") for i in recipe.get("ingredients", [])).lower()
        for member_name, restriction in restrictions:
            # crude but transparent: match on the restriction's significant words
            # (skip generic/negation words like "allergy"/"free"/"no" that would
            # false-positive on everything — "no" in particular used to slip
            # through and substring-match words like "oregano", flagging
            # totally unrelated recipes)
            keywords = [w for w in restriction.replace("-", " ").split() if w not in ("allergy", "allergic", "free", "intolerance", "intolerant", "no", "not")]
            # whole-word match, not substring — substring matching let short
            # keywords like "no" (before the filter above) or e.g. "egg" match
            # inside unrelated words ("oregano", "eggplant") instead of the
            # actual ingredient
            if keywords and any(re.search(r"\b" + re.escape(kw) + r"\b", ingredient_text) for kw in keywords):
                conflicts.append({
                    "meal": meal["meal"], "member": member_name, "restriction": restriction,
                    "date": meal.get("date"), "component_category": meal.get("component_category"),
                })
    return {"weekly_plan_id": plan["weekly_plan_id"], "conflicts": conflicts}


def explain_meal_choice(meal_name: str) -> dict:
    """
    Get the full signal picture behind why a meal is/isn't a natural
    suggestion — use when the user asks "why did you suggest this?" or "why
    haven't we had X in a while?" Returns rating, feedback notes, recent
    one-off notes/deviations, times cooked, last cooked date, tags, cuisine,
    main protein, whether it's temporarily excluded, and the household's
    current novelty_preference setting (for context on how much new-recipe
    exposure the plan is aiming for generally).
    """
    try:
        recipe = _recipes.get_recipe(meal_name)
    except ValueError:
        return {"meal_name": meal_name, "found": False, "reason": "Not a saved recipe — likely a freeform/one-off meal with no tracked history."}
    memory = _memory.get_household_memory()
    return {
        "meal_name": recipe["name"],
        "found": True,
        "rating": recipe["rating"],
        "feedback_notes": recipe["feedback_notes"],
        "recent_one_off_notes": recipe["recent_one_off_notes"],
        "times_cooked": recipe["times_cooked"],
        "last_cooked_date": recipe["last_cooked_date"],
        "tags": recipe["tags"],
        "cuisine": recipe["cuisine"],
        "main_protein": recipe["main_protein"],
        "temporarily_excluded": bool(recipe["temporarily_excluded"]),
        "household_novelty_preference": memory.get("novelty_preference", "balanced"),
    }


def get_feedback_nudge() -> dict:
    """
    Check whether there's a good moment to gently ask for feedback on
    something recently cooked — call once near the start of a new
    conversation (not on every message) and, if it returns a meal, work a
    single low-key ask into the response rather than a separate prompt.
    Only surfaces a meal that's been checked off as cooked (check_off_meal)
    in the last 7 days AND whose recipe has never been rated — once a
    recipe has any rating this stops nudging about it.
    """
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.recipe_id, mpe.cooked_at
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.cooked_status = 'done' AND mpe.cooked_at IS NOT NULL
          AND mpe.cooked_at >= datetime('now', '-7 days')
          AND mpe.recipe_id IS NOT NULL
          AND (SELECT rating FROM recipes WHERE id = mpe.recipe_id) = ''
        ORDER BY mpe.cooked_at DESC LIMIT 1
        """,
        (household_id(),),
    ).fetchone()
    conn.close()
    if not row:
        return {"has_nudge": False}
    return {"has_nudge": True, "meal": row["meal"], "cooked_at": row["cooked_at"]}


def get_household_people() -> list[dict]:
    """
    The household's adults, each with the avatar initial + color the
    design_handoff_home_manager package uses for "who added it" on desktop
    grocery rows (README's People token: first adult plum #66304E, second
    green #4D8A33 — see db._backfill_member_colors). Powers the identity
    switcher (there's no real login in this app — see FIRST_RUN.md's
    "two adult accounts" note and the Phase 2 judgment call to use a
    lightweight, no-password picker instead) and the avatar rendered next
    to whichever name a grocery item's added_by holds.
    """
    conn = get_conn()
    rows = conn.execute(
        # LOWER(TRIM(...)): age_group is freeform and onboarding writes
        # "Adult", not "adult", so the exact match this used to do returned
        # an empty list for a real household — see db._backfill_member_colors
        # for the same fix and the fuller note.
        "SELECT name, color FROM members WHERE household_id = ? AND LOWER(TRIM(age_group)) = 'adult' ORDER BY id ASC",
        (household_id(),),
    ).fetchall()
    conn.close()
    # A color stored on the row (set by db._backfill_member_colors at the
    # next app restart after this adult was added) always wins; a
    # not-yet-backfilled adult still gets the right color *this* request by
    # falling back to its ordinal position among adults, not a flat
    # default — otherwise a second adult added since the last restart would
    # incorrectly show Emily's own plum instead of the household's second
    # color.
    fallback_colors = ["#66304E", "#4D8A33"]
    out = []
    for i, r in enumerate(rows):
        color = r["color"] or (fallback_colors[i] if i < len(fallback_colors) else "#8A7A82")
        out.append({"name": r["name"], "initial": (r["name"].strip()[:1] or "?").upper(), "color": color})
    return out
