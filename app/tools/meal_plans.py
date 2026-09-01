"""
Putting a recipe into a meal slot, and reading back what was planned.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from ..db import get_conn
from ._shared import HOUSEHOLD_ID
from . import recipes as _recipes


def plan_meal(
    meal_date: str,
    meal: str,
    slot: str = "dinner",
    add_ingredients_to_grocery_list: bool = False,
    food_groups: list[str] | None = None,
    weekly_plan_id: int | None = None,
    component_category: str | None = None,
    reasoning: str = "",
    derived_from: dict | None = None,
) -> dict:
    """
    Schedule a meal for a date. `meal` can be a saved recipe name or a
    freeform description (e.g. "leftovers", "tacos").

    Pass derived_from (see schema.sql on meal_plan_entries.derived_from_json)
    when planning as part of a generated week, to record which inputs
    produced this slot — the night tags that applied, the binding
    constraint, the quoted span of the household's own words that drove it.
    It's nearly free at generation time and impossible to backfill.

    Ingredients only reach the grocery list when
    add_ingredients_to_grocery_list is passed true. It defaults to FALSE
    on purpose: the grocery list is never written to without the household
    saying so. For a generated week the yes is approving the plan — see
    approve_weekly_plan, which is what puts that week's ingredients on the
    list. For a one-off meal planned in chat the yes is the person
    answering when asked (see the system prompt); pass the flag according
    to their answer.

    When the flag is true and the meal matches a saved recipe, its
    ingredients are added to the grocery list (skipping anything already tracked in
    pantry/fridge inventory with a quantity on hand — see update_inventory —
    reported back as already_have_skipped rather than silently vanishing),
    and its food_groups are used automatically. For a freeform meal, pass
    food_groups yourself if you can tell what it covers (subset of protein/
    carb/vegetable) — this powers gentle "want to round this out?"
    suggestions, never a requirement. Leave it out if you're not sure. Pass
    weekly_plan_id to attach this meal to a specific generated weekly plan
    (see create_weekly_plan/generate_weekly_plan) rather than leaving it as
    a standalone one-off entry. Pass component_category (e.g. "protein",
    "vegetable", "breakfast", "carb", "treat", "dip") only for a
    component_based plan's entries — in that case meal_date should just be
    the plan's week_start_date as a placeholder, since the item isn't tied
    to a specific day, and slot is ignored. Pass reasoning (a short,
    specific "why this?" rationale, e.g. "You said you love salmon, and
    it's Tuesday so nothing too fussy") when planning as part of a
    generated week — see generate_weekly_plan — so it's ready instantly
    later instead of needing to be worked out again on demand; omit it for
    genuinely one-off chat requests where there's no real "why" beyond the
    user asking for it.
    """
    conn = get_conn()
    recipe = conn.execute(
        "SELECT * FROM recipes WHERE household_id = ? AND name = ?", (HOUSEHOLD_ID, meal)
    ).fetchone()

    recipe_id = recipe["id"] if recipe else None
    freeform = None if recipe else meal
    entry_food_groups = json.loads(recipe["food_groups_json"]) if recipe else (food_groups or [])

    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, recipe_id, freeform_meal, food_groups_json, weekly_plan_id, component_category, reasoning, slot_state, derived_from_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)",
        (HOUSEHOLD_ID, meal_date, slot, recipe_id, freeform, json.dumps(entry_food_groups), weekly_plan_id, component_category, reasoning, json.dumps(derived_from or {})),
    )
    conn.commit()
    entry_id = cur.lastrowid

    if recipe:
        conn.execute(
            "UPDATE recipes SET times_cooked = times_cooked + 1, last_cooked_date = ? WHERE id = ?",
            (meal_date, recipe["id"]),
        )
        conn.commit()

    recipe_ingredients = json.loads(recipe["ingredients_json"]) if recipe else []
    conn.close()

    added_items = []
    already_have = []
    if recipe and add_ingredients_to_grocery_list:
        added_items, already_have = _recipes._add_recipe_ingredients_to_grocery_list(
            entry_id, recipe_ingredients, weekly_plan_id
        )

    missing = [g for g in ["protein", "carb", "vegetable"] if g not in entry_food_groups]
    return {
        "entry_id": entry_id,
        "date": meal_date,
        "slot": slot,
        "meal": meal,
        "component_category": component_category,
        "groceries_added": added_items,
        "already_have_skipped": already_have,
        "food_groups_covered": entry_food_groups,
        "food_groups_missing": missing,
    }


def get_meal_plan(days_ahead: int = 7) -> list[dict]:
    """Get the upcoming meal plan, including which food groups (protein/carb/vegetable) each planned meal covers, where known."""
    conn = get_conn()
    end_date = (date.today() + timedelta(days=days_ahead)).isoformat()
    rows = conn.execute(
        """
        SELECT mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.food_groups_json
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.date >= date('now') AND mpe.date <= ?
        ORDER BY mpe.date ASC
        """,
        (HOUSEHOLD_ID, end_date),
    ).fetchall()
    conn.close()
    return [
        {"date": r["date"], "slot": r["slot"], "meal": r["meal"], "food_groups": json.loads(r["food_groups_json"])}
        for r in rows
    ]


def get_recent_meal_history(weeks: int = 3) -> list[dict]:
    """
    Look up what's actually been planned/cooked in the last N weeks
    (default 3), including each meal's cuisine and main protein where
    known. Call this before generating a new week's plan so you can avoid
    repeating the same recipe or near-identical meals within the window,
    and check protein/cuisine variety across it — not just literal repeats.
    """
    conn = get_conn()
    start_date = (date.today() - timedelta(weeks=weeks)).isoformat()
    rows = conn.execute(
        """
        SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal, r.cuisine, r.main_protein
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.date >= ?
        ORDER BY mpe.date DESC
        """,
        (HOUSEHOLD_ID, start_date),
    ).fetchall()
    conn.close()
    return [
        {"date": r["date"], "meal": r["meal"], "cuisine": r["cuisine"] or None, "main_protein": r["main_protein"] or None}
        for r in rows
    ]


def create_weekly_plan(week_start_date: str, constraints_notes: str = "") -> dict:
    """
    Start a new weekly plan — a reviewable batch of meals for a week,
    rather than meals living only as scattered chat-planned entries.
    constraints_notes is freeform per-week context (e.g. "out Thu/Fri,
    keep it under 30 min on weeknights"). Snapshots the household's current
    planning_mode (day_based/component_based, see set_planning_mode) onto
    this plan so it stays interpretable even if the household later
    switches modes. After creating it, attach each day's meal via
    plan_meal(..., weekly_plan_id=this id). Prefer generate_weekly_plan
    over calling this directly when the user just wants "plan my week" —
    it handles the whole generation in one step.
    """
    conn = get_conn()
    prefs = conn.execute(
        "SELECT planning_mode FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    planning_mode = prefs["planning_mode"] if prefs else "day_based"
    # Determined once, atomically, right here at creation — not re-derived
    # later by querying for the household's earliest plan row, which would
    # be fragile against backfills/edits/re-onboarding (see db.py's
    # is_first_plan migration comment).
    existing_plan_count = conn.execute(
        "SELECT COUNT(*) AS n FROM weekly_plans WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()["n"]
    is_first_plan = existing_plan_count == 0
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, constraints_notes, planning_mode, is_first_plan) VALUES (?, ?, ?, ?, ?)",
        (HOUSEHOLD_ID, week_start_date, constraints_notes, planning_mode, int(is_first_plan)),
    )
    conn.commit()
    plan_id = cur.lastrowid
    conn.close()
    return {
        "weekly_plan_id": plan_id,
        "week_start_date": week_start_date,
        "status": "draft",
        "planning_mode": planning_mode,
        "is_first_plan": is_first_plan,
    }
