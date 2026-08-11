"""
Tool functions the AI agent can call. Each function talks directly to
SQLite and returns plain dicts/lists (JSON-serializable) so they can be
handed straight back to Claude as tool results.

HOUSEHOLD_ID is hardcoded to 1 for V1 (single household, single user).
When this becomes multi-tenant, thread a real household_id through here
instead of the constant.
"""
from __future__ import annotations  # lets `str | None` etc. work on Python 3.9

import json
import re
import secrets
from datetime import date, timedelta
from .db import get_conn

HOUSEHOLD_ID = 1

_FREQUENCY_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91, "once": None}


# ---------- Household setup / onboarding ----------

def get_household_setup_status() -> dict:
    """
    Check how far onboarding has gotten: whether any members and any chores
    exist yet, plus household basics (pets, goals). Call this at the start
    of a conversation to decide whether to walk the user through setup
    before doing anything else.
    """
    conn = get_conn()
    members = conn.execute(
        "SELECT id, name, age_group FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    pets = conn.execute(
        "SELECT id, name, pet_type FROM pets WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    household = conn.execute(
        "SELECT goals FROM households WHERE id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    chore_count = conn.execute(
        "SELECT COUNT(*) AS c FROM chores WHERE household_id = ? AND active = 1", (HOUSEHOLD_ID,)
    ).fetchone()["c"]
    conn.close()
    return {
        "has_members": len(members) > 0,
        "members": [dict(m) for m in members],
        "pets": [dict(p) for p in pets],
        "goals": household["goals"] if household else "",
        "has_chores": chore_count > 0,
        "chore_count": chore_count,
        "onboarding_complete": len(members) > 0 and chore_count > 0,
    }


def add_member(name: str) -> dict:
    """Add a household member (person who can be assigned chores)."""
    conn = get_conn()
    member_id = _get_or_create_member(conn, name)
    conn.close()
    return {"member_id": member_id, "name": name}


def list_members() -> list[dict]:
    """List all household members, including any saved dietary restrictions."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, dietary_restrictions_json FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    return [
        {"id": r["id"], "name": r["name"], "dietary_restrictions": json.loads(r["dietary_restrictions_json"])}
        for r in rows
    ]


def set_member_dietary_restrictions(name: str, restrictions: list[str]) -> dict:
    """Set a member's dietary restrictions/allergies (e.g. ['vegetarian', 'peanut allergy']). Pass an empty list if they have none."""
    conn = get_conn()
    member_id = _get_or_create_member(conn, name)
    conn.execute(
        "UPDATE members SET dietary_restrictions_json = ? WHERE id = ?",
        (json.dumps(restrictions), member_id),
    )
    conn.commit()
    conn.close()
    return {"name": name, "dietary_restrictions": restrictions}


def set_member_age_group(name: str, age_group: str) -> dict:
    """Set a member's general age group (e.g. 'adult', 'teen', 'child', 'toddler', or anything freeform)."""
    conn = get_conn()
    member_id = _get_or_create_member(conn, name)
    conn.execute("UPDATE members SET age_group = ? WHERE id = ?", (age_group, member_id))
    conn.commit()
    conn.close()
    return {"name": name, "age_group": age_group}


def set_household_goals(goals: str) -> dict:
    """Save freeform household goals for using this app (e.g. 'stay on top of chores, eat healthier, waste less food')."""
    conn = get_conn()
    conn.execute("UPDATE households SET goals = ? WHERE id = ?", (goals, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"goals": goals}


# ---------- Pets ----------

def add_pet(name: str, pet_type: str) -> dict:
    """Add a household pet. pet_type is freeform, e.g. 'dog', 'cat', 'rabbit'. Pets can influence chores (litter box, walks) and grocery/household shopping lists (food, litter, etc.)."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO pets (household_id, name, pet_type) VALUES (?, ?, ?)",
        (HOUSEHOLD_ID, name, pet_type),
    )
    conn.commit()
    pet_id = cur.lastrowid
    conn.close()
    return {"pet_id": pet_id, "name": name, "pet_type": pet_type}


def list_pets() -> list[dict]:
    """List all household pets."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, pet_type FROM pets WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Meal planning setup / onboarding ----------

def get_meal_planning_setup_status() -> dict:
    """
    Check whether meal-planning onboarding (dietary restrictions + household
    food preferences) has been completed, and whether any recipes exist yet.
    Call this before helping with meal planning or groceries for the first
    time in a conversation.
    """
    conn = get_conn()
    members = conn.execute(
        "SELECT name, dietary_restrictions_json FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    prefs = conn.execute(
        "SELECT notes, protein_preferences_json, cuisine_preferences_json, dislikes_json, cooking_time_preference, onboarding_complete "
        "FROM meal_preferences WHERE household_id = ?",
        (HOUSEHOLD_ID,),
    ).fetchone()
    recipe_count = conn.execute(
        "SELECT COUNT(*) AS c FROM recipes WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()["c"]
    conn.close()
    return {
        "members": [
            {"name": m["name"], "dietary_restrictions": json.loads(m["dietary_restrictions_json"])}
            for m in members
        ],
        "household_notes": prefs["notes"] if prefs else "",
        "protein_preferences": json.loads(prefs["protein_preferences_json"]) if prefs else {},
        "cuisine_preferences": json.loads(prefs["cuisine_preferences_json"]) if prefs else [],
        "dislikes": json.loads(prefs["dislikes_json"]) if prefs else [],
        "cooking_time_preference": prefs["cooking_time_preference"] if prefs else "",
        "onboarding_complete": bool(prefs and prefs["onboarding_complete"]),
        "has_recipes": recipe_count > 0,
        "recipe_count": recipe_count,
    }


def add_food_dislikes(items: list[str]) -> dict:
    """
    Remember one or more disliked foods/ingredients to avoid in future
    suggestions (e.g. ['peppers']) — not allergies, just preference. Call
    this immediately whenever the user mentions not wanting something in a
    meal, even mid-conversation, so it's remembered going forward rather
    than just for the current chat. Merges with anything already saved.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT dislikes_json FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    current = json.loads(existing["dislikes_json"]) if existing else []
    merged = list(dict.fromkeys(current + [i.strip() for i in items if i.strip()]))
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, dislikes_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET dislikes_json = excluded.dislikes_json, updated_at = datetime('now')
        """,
        (HOUSEHOLD_ID, json.dumps(merged)),
    )
    conn.commit()
    conn.close()
    return {"dislikes": merged}


def set_household_meal_preferences(
    notes: str = "",
    protein_preferences: dict[str, str] | None = None,
    cuisine_preferences: list[str] | None = None,
    cooking_time_preference: str = "",
    novelty_preference: str = "",
    mark_complete: bool = True,
) -> dict:
    """
    Save household food preferences: freeform notes (the "let me type it"
    catch-all), protein preferences (how often per protein, e.g.
    {"chicken": "several times a week", "beef": "rarely"} — reflects
    preference, health, and budget together, not just taste), favorite
    cuisines, a cooking time preference, and novelty_preference (how often
    new recipes should get surfaced: 'mostly_favorites', 'balanced', or
    'surprise_me_often' — even 'mostly_favorites' still gets occasional new
    recipes, it's not "never"). Any field can be omitted/partial — pass what you have.
    By default this marks meal-planning onboarding as complete; pass
    mark_complete=False if you're saving a partial update mid-conversation.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT * FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()

    merged_notes = notes if notes else (existing["notes"] if existing else "")
    merged_proteins = dict(json.loads(existing["protein_preferences_json"])) if existing else {}
    if protein_preferences:
        merged_proteins.update(protein_preferences)
    merged_cuisines = cuisine_preferences if cuisine_preferences is not None else (
        json.loads(existing["cuisine_preferences_json"]) if existing else []
    )
    merged_cooking_time = cooking_time_preference or (existing["cooking_time_preference"] if existing else "")
    merged_novelty = novelty_preference or (existing["novelty_preference"] if existing else "balanced")

    conn.execute(
        """
        INSERT INTO meal_preferences
            (household_id, notes, protein_preferences_json, cuisine_preferences_json, cooking_time_preference, novelty_preference, onboarding_complete, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET
            notes = excluded.notes,
            protein_preferences_json = excluded.protein_preferences_json,
            cuisine_preferences_json = excluded.cuisine_preferences_json,
            cooking_time_preference = excluded.cooking_time_preference,
            novelty_preference = excluded.novelty_preference,
            onboarding_complete = excluded.onboarding_complete,
            updated_at = datetime('now')
        """,
        (
            HOUSEHOLD_ID,
            merged_notes,
            json.dumps(merged_proteins),
            json.dumps(merged_cuisines),
            merged_cooking_time,
            merged_novelty,
            1 if mark_complete else (existing["onboarding_complete"] if existing else 0),
        ),
    )
    conn.commit()
    conn.close()
    return {
        "notes": merged_notes,
        "protein_preferences": merged_proteins,
        "cuisine_preferences": merged_cuisines,
        "cooking_time_preference": merged_cooking_time,
        "novelty_preference": merged_novelty,
        "onboarding_complete": bool(mark_complete),
    }


# ---------- Chores profile (context, not chores themselves) ----------

def set_chores_profile(
    home_type: str = "",
    bedrooms: int = 0,
    bathrooms: int = 0,
    has_yard: bool = False,
    standard: str = "",
    rotation_members: list[str] | None = None,
    existing_help: str = "",
    existing_help_frequency: str = "",
    include_notes: str = "",
    exclude_notes: str = "",
) -> dict:
    """
    Save household context for chores (home type/size, yard, cleanliness
    standard, who's in the rotation, existing help, notes) without creating
    any chores yet. Useful as a quick save of onboarding answers, or to
    record context conversationally before building the actual chore list
    with add_chore.
    """
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO chores_profile
            (household_id, home_type, bedrooms, bathrooms, has_yard, standard,
             rotation_members_json, existing_help, existing_help_frequency,
             include_notes, exclude_notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET
            home_type = excluded.home_type,
            bedrooms = excluded.bedrooms,
            bathrooms = excluded.bathrooms,
            has_yard = excluded.has_yard,
            standard = excluded.standard,
            rotation_members_json = excluded.rotation_members_json,
            existing_help = excluded.existing_help,
            existing_help_frequency = excluded.existing_help_frequency,
            include_notes = excluded.include_notes,
            exclude_notes = excluded.exclude_notes,
            updated_at = datetime('now')
        """,
        (
            HOUSEHOLD_ID, home_type, bedrooms, bathrooms, 1 if has_yard else 0, standard,
            json.dumps(rotation_members or []), existing_help, existing_help_frequency,
            include_notes, exclude_notes,
        ),
    )
    conn.commit()
    conn.close()
    return {"saved": True}


def get_chores_profile() -> dict:
    """Get saved chores context (home type, yard, standard, etc.), if any was recorded. Empty fields mean it wasn't collected yet."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM chores_profile WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    conn.close()
    if not row:
        return {"has_profile": False}
    return {
        "has_profile": True,
        "home_type": row["home_type"],
        "bedrooms": row["bedrooms"],
        "bathrooms": row["bathrooms"],
        "has_yard": bool(row["has_yard"]),
        "standard": row["standard"],
        "rotation_members": json.loads(row["rotation_members_json"]),
        "existing_help": row["existing_help"],
        "existing_help_frequency": row["existing_help_frequency"],
        "include_notes": row["include_notes"],
        "exclude_notes": row["exclude_notes"],
    }


# ---------- Chores ----------

def add_chore(
    name: str,
    frequency: str = "weekly",
    category: str = "cleaning",
    assignee_names: list[str] | None = None,
) -> dict:
    """
    Create a new recurring chore definition. `assignee_names` can be one
    name (always assigned to that person) or several (rotates round-robin
    each time the chore comes up). Leave empty for unassigned.
    """
    conn = get_conn()
    rotation_ids = []
    default_id = None
    if assignee_names:
        rotation_ids = [_get_or_create_member(conn, n) for n in assignee_names]
        default_id = rotation_ids[0]
    cur = conn.execute(
        "INSERT INTO chores (household_id, name, category, frequency, default_assignee_id, rotation_member_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (HOUSEHOLD_ID, name, category, frequency, default_id, json.dumps(rotation_ids)),
    )
    conn.commit()
    chore_id = cur.lastrowid
    conn.close()
    return {
        "chore_id": chore_id,
        "name": name,
        "category": category,
        "frequency": frequency,
        "assignees": assignee_names or [],
    }


def list_chore_definitions(active_only: bool = True) -> list[dict]:
    """List the recurring chore templates themselves (not individual due-date instances)."""
    conn = get_conn()
    query = "SELECT c.id, c.name, c.category, c.frequency, c.active, c.rotation_member_ids_json FROM chores c WHERE c.household_id = ?"
    if active_only:
        query += " AND c.active = 1"
    rows = conn.execute(query, (HOUSEHOLD_ID,)).fetchall()
    member_rows = conn.execute("SELECT id, name FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)).fetchall()
    names_by_id = {m["id"]: m["name"] for m in member_rows}
    conn.close()
    result = []
    for r in rows:
        rotation_ids = json.loads(r["rotation_member_ids_json"])
        result.append(
            {
                "id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "frequency": r["frequency"],
                "active": bool(r["active"]),
                "assignees": [names_by_id.get(i, "?") for i in rotation_ids],
            }
        )
    return result


def update_chore(
    chore_id: int,
    frequency: str | None = None,
    category: str | None = None,
    assignee_names: list[str] | None = None,
    active: bool | None = None,
) -> dict:
    """Update an existing chore's frequency, category, assigned rotation, or active status."""
    conn = get_conn()
    if frequency is not None:
        conn.execute("UPDATE chores SET frequency = ? WHERE id = ? AND household_id = ?", (frequency, chore_id, HOUSEHOLD_ID))
    if category is not None:
        conn.execute("UPDATE chores SET category = ? WHERE id = ? AND household_id = ?", (category, chore_id, HOUSEHOLD_ID))
    if assignee_names is not None:
        rotation_ids = [_get_or_create_member(conn, n) for n in assignee_names]
        default_id = rotation_ids[0] if rotation_ids else None
        conn.execute(
            "UPDATE chores SET rotation_member_ids_json = ?, default_assignee_id = ? WHERE id = ? AND household_id = ?",
            (json.dumps(rotation_ids), default_id, chore_id, HOUSEHOLD_ID),
        )
    if active is not None:
        conn.execute("UPDATE chores SET active = ? WHERE id = ? AND household_id = ?", (1 if active else 0, chore_id, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"chore_id": chore_id, "updated": True}


def generate_chore_schedule(days_ahead: int = 14) -> list[dict]:
    """
    Auto-generate upcoming chore instances for every active chore, out to
    `days_ahead`. Skips dates that already have a pending/done instance for
    that chore. Assigns round-robin across a chore's rotation members (or
    the single assignee if only one), continuing the rotation from whoever
    was assigned last time. Call this after onboarding, and periodically
    (e.g. "generate this week's chores") to keep the schedule filled in.
    """
    conn = get_conn()
    chores = conn.execute(
        "SELECT * FROM chores WHERE household_id = ? AND active = 1 AND frequency != 'once'", (HOUSEHOLD_ID,)
    ).fetchall()

    created = []
    today = date.today()
    horizon = today + timedelta(days=days_ahead)

    for chore in chores:
        interval = _FREQUENCY_DAYS.get(chore["frequency"], 7)
        rotation_ids = json.loads(chore["rotation_member_ids_json"])

        last = conn.execute(
            "SELECT due_date, assignee_id FROM chore_instances WHERE chore_id = ? ORDER BY due_date DESC LIMIT 1",
            (chore["id"],),
        ).fetchone()

        if last:
            next_due = date.fromisoformat(last["due_date"]) + timedelta(days=interval)
            last_assignee_id = last["assignee_id"]
        else:
            next_due = today
            last_assignee_id = None

        rotation_cursor = rotation_ids.index(last_assignee_id) + 1 if last_assignee_id in rotation_ids else 0

        while next_due <= horizon:
            exists = conn.execute(
                "SELECT id FROM chore_instances WHERE chore_id = ? AND due_date = ?",
                (chore["id"], next_due.isoformat()),
            ).fetchone()
            if not exists:
                if rotation_ids:
                    assignee_id = rotation_ids[rotation_cursor % len(rotation_ids)]
                    rotation_cursor += 1
                else:
                    assignee_id = chore["default_assignee_id"]
                cur = conn.execute(
                    "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
                    (HOUSEHOLD_ID, chore["id"], assignee_id, next_due.isoformat()),
                )
                created.append({"chore": chore["name"], "due_date": next_due.isoformat(), "instance_id": cur.lastrowid})
            next_due += timedelta(days=interval)

    conn.commit()
    conn.close()
    return created


def schedule_chore_instance(chore_name: str, due_date: str, assignee_name: str | None = None) -> dict:
    """Schedule a one-off occurrence of a chore for a specific date (YYYY-MM-DD)."""
    conn = get_conn()
    chore = conn.execute(
        "SELECT * FROM chores WHERE household_id = ? AND name = ?", (HOUSEHOLD_ID, chore_name)
    ).fetchone()
    if not chore:
        conn.close()
        raise ValueError(f"No chore named '{chore_name}'. Create it first with add_chore.")

    assignee_id = chore["default_assignee_id"]
    if assignee_name:
        assignee_id = _get_or_create_member(conn, assignee_name)

    cur = conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
        (HOUSEHOLD_ID, chore["id"], assignee_id, due_date),
    )
    conn.commit()
    instance_id = cur.lastrowid
    conn.close()
    return {"instance_id": instance_id, "chore": chore_name, "due_date": due_date}


def list_chores(status: str = "pending", days_ahead: int = 14) -> list[dict]:
    """List chore instances, optionally filtered by status (pending/done/skipped)."""
    conn = get_conn()
    end_date = (date.today() + timedelta(days=days_ahead)).isoformat()
    rows = conn.execute(
        """
        SELECT ci.id, c.name AS chore, ci.due_date, ci.status, m.name AS assignee
        FROM chore_instances ci
        JOIN chores c ON c.id = ci.chore_id
        LEFT JOIN members m ON m.id = ci.assignee_id
        WHERE ci.household_id = ?
          AND (? = 'all' OR ci.status = ?)
          AND ci.due_date <= ?
        ORDER BY ci.due_date ASC
        """,
        (HOUSEHOLD_ID, status, status, end_date),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_chore(instance_id: int) -> dict:
    """Mark a chore instance as done."""
    conn = get_conn()
    conn.execute(
        "UPDATE chore_instances SET status = 'done', completed_at = datetime('now') WHERE id = ? AND household_id = ?",
        (instance_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"instance_id": instance_id, "status": "done"}


# ---------- Meal planning ----------

def add_recipe(
    name: str,
    ingredients: list[dict],
    notes: str = "",
    tags: list[str] | None = None,
    food_groups: list[str] | None = None,
    cuisine: str = "",
    main_protein: str = "",
    instructions: list[str] | None = None,
    default_servings: int = 4,
    prep_time_minutes: int | None = None,
    cook_time_minutes: int | None = None,
    advance_prep_notes: str = "",
) -> dict:
    """
    Save a recipe. ingredients is a list of {"item": str, "qty": str}. tags
    are freeform, e.g. ["vegetarian", "quick", "kid-friendly"]. food_groups
    is a subset of ["protein", "carb", "vegetable"] describing what this
    dish covers on its own — e.g. spaghetti and meatballs is ["protein",
    "carb"] (no vegetable); a stir fry with rice might be all three. Use
    your judgment based on the ingredients; leave out anything unclear.
    cuisine (e.g. "Italian", "Mexican") and main_protein (e.g. "chicken",
    "beef", "vegetarian") are freeform but worth filling in when you can —
    they power variety checks when generating future weekly plans so the
    rotation doesn't quietly repeat the same protein or cuisine too often.
    instructions is an ordered list of step strings — fill this in whenever
    you can (from the user, or a reasonable version if generating a new
    recipe) so the recipe is actually cookable from within the app, not
    just a shopping list. default_servings is what the ingredient
    quantities are scaled for (defaults to 4) — used by scale_recipe.
    prep_time_minutes/cook_time_minutes and advance_prep_notes (e.g.
    "marinate at least 4 hours ahead, can be done the night before") power
    generate_prep_schedule — fill them in when you reasonably can, leave
    unset rather than guessing if you can't.
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO recipes (household_id, name, notes, ingredients_json, tags_json, food_groups_json, cuisine, main_protein, "
        "instructions_json, default_servings, prep_time_minutes, cook_time_minutes, advance_prep_notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            HOUSEHOLD_ID, name, notes, json.dumps(ingredients), json.dumps(tags or []),
            json.dumps(food_groups or []), cuisine, main_protein,
            json.dumps(instructions or []), default_servings, prep_time_minutes, cook_time_minutes,
            advance_prep_notes,
        ),
    )
    conn.commit()
    recipe_id = cur.lastrowid
    conn.close()
    return {
        "recipe_id": recipe_id, "name": name, "tags": tags or [], "food_groups": food_groups or [],
        "cuisine": cuisine, "main_protein": main_protein, "instructions": instructions or [],
        "default_servings": default_servings,
    }


def update_recipe_details(
    recipe_name: str,
    instructions: list[str] | None = None,
    default_servings: int | None = None,
    prep_time_minutes: int | None = None,
    cook_time_minutes: int | None = None,
    advance_prep_notes: str | None = None,
) -> dict:
    """
    Backfill or correct Cooker-layer detail on an already-saved recipe —
    instructions, servings, timing, advance-prep notes. Use this whenever
    get_recipe comes back with empty instructions (common for recipes saved
    before this detail was tracked, or a freeform meal that got saved
    quickly): work out a reasonable step-by-step from your own knowledge of
    the dish (using the recipe's existing ingredients as a guide), show it
    to the user as part of your answer, and save it here in the same turn
    so it's there next time — don't just tell the user nothing's saved and
    stop. Only pass the fields you're actually setting; anything left as
    None is untouched.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (HOUSEHOLD_ID, recipe_name),
    ).fetchone()
    if not existing:
        conn.close()
        raise ValueError(f"No saved recipe named '{recipe_name}'.")

    fields, params = [], []
    if instructions is not None:
        fields.append("instructions_json = ?")
        params.append(json.dumps(instructions))
    if default_servings is not None:
        fields.append("default_servings = ?")
        params.append(default_servings)
    if prep_time_minutes is not None:
        fields.append("prep_time_minutes = ?")
        params.append(prep_time_minutes)
    if cook_time_minutes is not None:
        fields.append("cook_time_minutes = ?")
        params.append(cook_time_minutes)
    if advance_prep_notes is not None:
        fields.append("advance_prep_notes = ?")
        params.append(advance_prep_notes)

    if fields:
        params.append(existing["id"])
        conn.execute(f"UPDATE recipes SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
    conn.close()
    return get_recipe(recipe_name)


def list_recipes(include_temporarily_excluded: bool = True) -> list[dict]:
    """
    List all saved recipes, including tags, food groups covered, how often
    each has been planned, permanent feedback (rating + notes), any recent
    one-off feedback notes (see log_recipe_note — soft signals distinct
    from the permanent rating), and whether it's currently temporarily
    excluded from rotation (see flag_recipe_temporary — distinct from a
    permanent 'disliked' rating). Sorted to surface liked recipes first,
    then by how often they've been made — use this ordering to favor known
    favorites over untested ones when suggesting meals. Pass
    include_temporarily_excluded=False when building a weekly plan's
    candidate list, so temporarily-flagged recipes aren't suggested (they
    still show up here otherwise, e.g. for "what recipes do we have").
    """
    conn = get_conn()
    query = """
        SELECT id, name, notes, ingredients_json, tags_json, food_groups_json,
               times_cooked, last_cooked_date, rating, feedback_notes, cuisine, main_protein,
               temporarily_excluded, instructions_json, default_servings, prep_time_minutes,
               cook_time_minutes, advance_prep_notes
        FROM recipes WHERE household_id = ?
        {exclusion_clause}
        ORDER BY (rating = 'liked') DESC, (rating = 'disliked') ASC, times_cooked DESC, name ASC
        """.format(exclusion_clause="" if include_temporarily_excluded else "AND temporarily_excluded = 0")
    rows = conn.execute(query, (HOUSEHOLD_ID,)).fetchall()

    recipe_ids = [r["id"] for r in rows]
    notes_by_recipe: dict[int, list[str]] = {}
    if recipe_ids:
        placeholders = ",".join("?" * len(recipe_ids))
        # Both note types (one-off feedback AND cooking deviations) feed the
        # same soft signal — per the product decision that deviations "feed
        # back into the same memory system recipe ratings already use."
        note_rows = conn.execute(
            f"SELECT recipe_id, note FROM recipe_notes WHERE recipe_id IN ({placeholders}) "
            "ORDER BY created_at DESC",
            recipe_ids,
        ).fetchall()
        for nr in note_rows:
            notes_by_recipe.setdefault(nr["recipe_id"], [])
            if len(notes_by_recipe[nr["recipe_id"]]) < 3:  # most recent few is plenty of signal
                notes_by_recipe[nr["recipe_id"]].append(nr["note"])
    conn.close()

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "notes": r["notes"],
            "ingredients": json.loads(r["ingredients_json"]),
            "tags": json.loads(r["tags_json"]),
            "food_groups": json.loads(r["food_groups_json"]),
            "times_cooked": r["times_cooked"],
            "last_cooked_date": r["last_cooked_date"],
            "rating": r["rating"] or None,
            "feedback_notes": r["feedback_notes"],
            "recent_one_off_notes": notes_by_recipe.get(r["id"], []),
            "cuisine": r["cuisine"] or None,
            "main_protein": r["main_protein"] or None,
            "temporarily_excluded": bool(r["temporarily_excluded"]),
            "instructions": json.loads(r["instructions_json"]),
            "default_servings": r["default_servings"],
            "prep_time_minutes": r["prep_time_minutes"],
            "cook_time_minutes": r["cook_time_minutes"],
            "advance_prep_notes": r["advance_prep_notes"],
        }
        for r in rows
    ]


def get_recipe(recipe_name: str) -> dict:
    """
    Get full detail for a single saved recipe by exact name — ingredients,
    instructions, timing, everything. Use this when the user wants to see
    a specific recipe in full (e.g. "show me the recipe for the chicken
    stir fry") rather than filtering list_recipes yourself.
    """
    matches = [r for r in list_recipes() if r["name"].lower() == recipe_name.lower()]
    if not matches:
        raise ValueError(f"No recipe named '{recipe_name}'.")
    return matches[0]


def scale_recipe(recipe_name: str, target_servings: int) -> dict:
    """
    Scale a saved recipe's ingredient quantities from its default_servings
    to target_servings — e.g. cooking for 6 when the recipe is written for
    4. Quantities that parse cleanly (a number + a known unit) are scaled
    directly; anything freeform (like "a pinch" or "to taste") is left
    as-is rather than guessed, and flagged in unscaled_items so the Cooker
    knows to eyeball it themselves.
    """
    recipe = get_recipe(recipe_name)
    base_servings = recipe["default_servings"] or 4
    if base_servings <= 0:
        base_servings = 4
    ratio = target_servings / base_servings

    scaled_ingredients = []
    unscaled_items = []
    for ing in recipe["ingredients"]:
        parsed = _parse_quantity(ing.get("qty", ""))
        if parsed:
            amount, unit = parsed
            scaled_ingredients.append({**ing, "qty": _format_quantity(amount * ratio, unit)})
        else:
            scaled_ingredients.append(dict(ing))
            if (ing.get("qty") or "").strip():
                unscaled_items.append(ing["item"])

    return {
        "name": recipe_name,
        "base_servings": base_servings,
        "target_servings": target_servings,
        "scaled_ingredients": scaled_ingredients,
        "unscaled_items": unscaled_items,
    }


def mark_recipe_feedback(recipe_name: str, rating: str | None = None, notes: str = "") -> dict:
    """
    Record feedback on a saved recipe after it's been made — rating is
    'liked', 'disliked', or omit to just add notes without changing the
    rating. notes are freeform (e.g. "loved the sauce, a bit too spicy for
    the kids") and get appended to any existing feedback rather than
    replacing it. Call this the moment the user expresses an opinion about
    a specific recipe they've made, so future suggestions can favor what
    they actually liked.
    """
    conn = get_conn()
    recipe = conn.execute(
        "SELECT id, feedback_notes FROM recipes WHERE household_id = ? AND name = ?",
        (HOUSEHOLD_ID, recipe_name),
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")

    merged_notes = recipe["feedback_notes"]
    if notes:
        merged_notes = f"{merged_notes} | {notes}" if merged_notes else notes

    if rating is not None:
        conn.execute(
            "UPDATE recipes SET rating = ?, feedback_notes = ? WHERE id = ?",
            (rating, merged_notes, recipe["id"]),
        )
    else:
        conn.execute("UPDATE recipes SET feedback_notes = ? WHERE id = ?", (merged_notes, recipe["id"]))
    conn.commit()
    conn.close()
    return {"name": recipe_name, "rating": rating, "feedback_notes": merged_notes}


def log_recipe_note(recipe_name: str, note: str) -> dict:
    """
    Log a one-off note about a specific time a recipe was made — e.g.
    "wasn't great with this cut of meat," "ran out of time to marinate
    properly" — WITHOUT changing the recipe's permanent rating. This is the
    key distinction from mark_recipe_feedback: a single bad (or good, but
    not pattern-worthy) experience shouldn't by itself blacklist or
    permanently boost a recipe. Use mark_recipe_feedback instead when the
    user is expressing an actual pattern ("we don't like this," "this is a
    new favorite"). Recent notes are surfaced alongside the rating (see
    list_recipes) as a soft signal when generating future plans.
    """
    conn = get_conn()
    recipe = conn.execute(
        "SELECT id FROM recipes WHERE household_id = ? AND name = ?", (HOUSEHOLD_ID, recipe_name)
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")
    conn.execute(
        "INSERT INTO recipe_notes (household_id, recipe_id, note_type, note) VALUES (?, ?, 'feedback', ?)",
        (HOUSEHOLD_ID, recipe["id"], note),
    )
    conn.commit()
    conn.close()
    return {"name": recipe_name, "note": note}


def log_cooking_deviation(recipe_name: str, note: str) -> dict:
    """
    Capture something that actually changed while cooking a recipe — a
    swap ("used ground turkey instead of beef"), an adjusted step ("skipped
    the marinating step, still turned out fine"), a doubled component
    ("doubled the sauce") — so it's not lost. Feeds into the same memory
    system recipe feedback already uses (see list_recipes'
    recent_one_off_notes), distinct from log_recipe_note only in intent
    (what changed vs. a taste/quality comment) — call this the moment the
    user mentions cooking something differently than the recipe says.
    """
    conn = get_conn()
    recipe = conn.execute(
        "SELECT id FROM recipes WHERE household_id = ? AND name = ?", (HOUSEHOLD_ID, recipe_name)
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")
    conn.execute(
        "INSERT INTO recipe_notes (household_id, recipe_id, note_type, note) VALUES (?, ?, 'deviation', ?)",
        (HOUSEHOLD_ID, recipe["id"], note),
    )
    conn.commit()
    conn.close()
    return {"name": recipe_name, "note": note}


def flag_recipe_temporary(recipe_name: str, excluded: bool = True) -> dict:
    """
    Temporarily exclude a recipe from auto-suggestion rotation (excluded=
    True), or bring it back (excluded=False) — distinct from a permanent
    'disliked' rating (see mark_recipe_feedback). Use this when the
    household is just tired of a favorite for now ("let's not do the
    chicken stir fry for a while") rather than actually disliking it; it
    stays saved and can come back into rotation any time by calling this
    again with excluded=False. No auto-expiry — it's manually toggled.
    """
    conn = get_conn()
    recipe = conn.execute(
        "SELECT id FROM recipes WHERE household_id = ? AND name = ?", (HOUSEHOLD_ID, recipe_name)
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")
    conn.execute(
        "UPDATE recipes SET temporarily_excluded = ? WHERE id = ?",
        (1 if excluded else 0, recipe["id"]),
    )
    conn.commit()
    conn.close()
    return {"name": recipe_name, "temporarily_excluded": excluded}


def plan_meal(
    meal_date: str,
    meal: str,
    slot: str = "dinner",
    add_ingredients_to_grocery_list: bool = True,
    food_groups: list[str] | None = None,
    weekly_plan_id: int | None = None,
    component_category: str | None = None,
) -> dict:
    """
    Schedule a meal for a date. `meal` can be a saved recipe name or a
    freeform description (e.g. "leftovers", "tacos"). If it matches a saved
    recipe and add_ingredients_to_grocery_list is true, its ingredients are
    auto-added to the grocery list (skipping anything already tracked in
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
    to a specific day, and slot is ignored.
    """
    conn = get_conn()
    recipe = conn.execute(
        "SELECT * FROM recipes WHERE household_id = ? AND name = ?", (HOUSEHOLD_ID, meal)
    ).fetchone()

    recipe_id = recipe["id"] if recipe else None
    freeform = None if recipe else meal
    entry_food_groups = json.loads(recipe["food_groups_json"]) if recipe else (food_groups or [])

    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, recipe_id, freeform_meal, food_groups_json, weekly_plan_id, component_category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (HOUSEHOLD_ID, meal_date, slot, recipe_id, freeform, json.dumps(entry_food_groups), weekly_plan_id, component_category),
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
        # Skip auto-adding anything already tracked in pantry/fridge
        # inventory (with a non-blank quantity) — this is the "accounts for
        # logged inventory" behavior for the automated plan-generation path.
        # For a direct chat-driven add ("add flour to the list"), the agent
        # checks get_inventory itself and asks first instead (see system
        # prompt) since there's a person there to actually ask.
        inv_conn = get_conn()
        have_names = {
            row["item"].strip().lower()
            for row in inv_conn.execute(
                "SELECT item FROM inventory_items WHERE household_id = ? AND TRIM(quantity) != ''",
                (HOUSEHOLD_ID,),
            ).fetchall()
        }
        inv_conn.close()

        # Routed through add_grocery_item (its own connection per call) rather
        # than a raw insert here, so quantities consolidate with anything
        # already on the list instead of creating duplicate lines. Tagged
        # with source_weekly_plan_id when this meal belongs to a generated
        # week (not an ad hoc one-off), so a later week's generation can
        # tell this ingredient apart from a genuine standing want and clear
        # it out once it's stale — see clear_stale_grocery_items.
        for ing in recipe_ingredients:
            if ing["item"].strip().lower() in have_names:
                already_have.append(ing["item"])
                continue
            add_grocery_item(
                ing["item"], quantity=ing.get("qty", ""), category=ing.get("category", "other"), added_by="ai",
                source_weekly_plan_id=weekly_plan_id,
            )
            added_items.append(ing["item"])

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


# ---------- Weekly plans (plan-as-object, reviewable/editable as a whole) ----------

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
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, constraints_notes, planning_mode) VALUES (?, ?, ?, ?)",
        (HOUSEHOLD_ID, week_start_date, constraints_notes, planning_mode),
    )
    conn.commit()
    plan_id = cur.lastrowid
    conn.close()
    return {"weekly_plan_id": plan_id, "week_start_date": week_start_date, "status": "draft", "planning_mode": planning_mode}


def set_planning_mode(mode: str) -> dict:
    """
    Set the household's standing weekly-planning mode: 'day_based' (default
    — one meal per day/slot) or 'component_based' (plan by category instead
    — a breakfast for the week, several proteins, several vegetables,
    carbs, a treat, a dip — for the household to assemble freely rather
    than a fixed day->meal mapping). This is household-level, not per-week
    — it applies to the next plan generated, and can be changed again any
    time, but a single already-generated plan stays whatever mode it was
    created under.
    """
    if mode not in ("day_based", "component_based"):
        raise ValueError("mode must be 'day_based' or 'component_based'.")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, planning_mode, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET planning_mode = excluded.planning_mode, updated_at = datetime('now')
        """,
        (HOUSEHOLD_ID, mode),
    )
    conn.commit()
    conn.close()
    return {"planning_mode": mode}


def set_week_constraints(constraints_notes: str, weekly_plan_id: int | None = None) -> dict:
    """
    Set/update the one-off constraints for a specific week's plan (e.g. "3
    nights this week," "under 30 minutes on weeknights," "one vegetarian
    night") without those constraints becoming a permanent household
    preference — they only apply to this plan record, unlike
    edit_preference which changes standing preferences. Omit
    weekly_plan_id to apply to the household's current (most recent) plan.
    If you're generating a brand-new plan, just pass constraints_notes
    directly to generate_weekly_plan instead — use this tool when
    constraints come up for a plan that already exists (e.g. mid-week) or
    you want them on record before generating.
    """
    conn = get_conn()
    if weekly_plan_id is None:
        row = conn.execute(
            "SELECT id FROM weekly_plans WHERE household_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (HOUSEHOLD_ID,),
        ).fetchone()
        if not row:
            conn.close()
            raise ValueError("No weekly plan exists yet — generate one first, or pass constraints_notes to generate_weekly_plan directly.")
        weekly_plan_id = row["id"]
    conn.execute(
        "UPDATE weekly_plans SET constraints_notes = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (constraints_notes, weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "constraints_notes": constraints_notes}


_COMPONENT_CATEGORY_ORDER = ["breakfast", "protein", "vegetable", "carb", "treat", "dip"]


def get_weekly_plan(weekly_plan_id: int | None = None) -> dict:
    """
    Get a weekly plan with all its meals. If weekly_plan_id is omitted,
    returns the household's most recently created plan — use that form
    when the user just says "what's this week's plan?" Returns
    weekly_plan_id: None with an empty meals list if no plan exists yet.
    Always includes a flat `meals` list (each with date/slot/
    component_category). For a component_based plan (see planning_mode),
    also includes a `components` list grouped by category — prefer that
    grouping when describing a component_based plan back to the user,
    since date/slot aren't meaningful there (every entry shares the same
    placeholder date).
    """
    conn = get_conn()
    if weekly_plan_id is None:
        # id DESC as a tiebreaker matters: two plans created within the same
        # second (created_at has only second-level resolution) would
        # otherwise resolve non-deterministically, which broke
        # clear_stale_grocery_items identifying the actual newest plan.
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE household_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (HOUSEHOLD_ID,),
        ).fetchone()
    else:
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, HOUSEHOLD_ID),
        ).fetchone()
    if not plan:
        conn.close()
        return {"weekly_plan_id": None, "meals": []}

    meals = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.food_groups_json, mpe.component_category, mpe.cooked_status
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ?
        ORDER BY mpe.date ASC, mpe.slot ASC
        """,
        (plan["id"],),
    ).fetchall()
    conn.close()

    meal_dicts = [
        {
            "entry_id": m["id"], "date": m["date"], "slot": m["slot"], "meal": m["meal"],
            "food_groups": json.loads(m["food_groups_json"]),
            "component_category": m["component_category"],
            "cooked_status": m["cooked_status"],
        }
        for m in meals
    ]

    result = {
        "weekly_plan_id": plan["id"],
        "week_start_date": plan["week_start_date"],
        "status": plan["status"],
        "constraints_notes": plan["constraints_notes"],
        "planning_mode": plan["planning_mode"],
        "meals": meal_dicts,
    }

    if plan["planning_mode"] == "component_based":
        by_category: dict[str, list[str]] = {}
        for m in meal_dicts:
            cat = m["component_category"] or "other"
            by_category.setdefault(cat, []).append(m["meal"])
        ordered_cats = [c for c in _COMPONENT_CATEGORY_ORDER if c in by_category]
        ordered_cats += [c for c in by_category if c not in ordered_cats]
        result["components"] = [{"category": c, "items": by_category[c]} for c in ordered_cats]

    return result


def approve_weekly_plan(weekly_plan_id: int) -> dict:
    """Mark a weekly plan as approved/reviewed by the Planner."""
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET status = 'approved', updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "status": "approved"}


def swap_meal_in_plan(
    weekly_plan_id: int,
    meal_date: str,
    new_meal: str,
    slot: str = "dinner",
    food_groups: list[str] | None = None,
) -> dict:
    """
    Replace the meal on one day/slot of an already-generated weekly plan,
    without regenerating or touching the rest of the plan. new_meal can be
    a saved recipe name or a freeform description, same as plan_meal.
    """
    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ?",
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return plan_meal(meal_date, new_meal, slot=slot, food_groups=food_groups, weekly_plan_id=weekly_plan_id)


def swap_component_in_plan(
    weekly_plan_id: int,
    component_category: str,
    old_meal: str,
    new_meal: str,
    food_groups: list[str] | None = None,
) -> dict:
    """
    Replace one item within a component_based plan's category (e.g. swap
    out one of the proteins) without touching the rest of the plan — the
    component_based equivalent of swap_meal_in_plan. old_meal must match
    the exact meal name currently in that category/plan.
    """
    conn = get_conn()
    week_start_date = conn.execute(
        "SELECT week_start_date FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not week_start_date:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    week_start_date = week_start_date["week_start_date"]

    deleted = conn.execute(
        """
        DELETE FROM meal_plan_entries WHERE id IN (
            SELECT mpe.id FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.weekly_plan_id = ? AND mpe.component_category = ? AND mpe.household_id = ?
              AND COALESCE(r.name, mpe.freeform_meal) = ?
            LIMIT 1
        )
        """,
        (weekly_plan_id, component_category, HOUSEHOLD_ID, old_meal),
    )
    conn.commit()
    removed = deleted.rowcount
    conn.close()
    if not removed:
        raise ValueError(f"Couldn't find '{old_meal}' under category '{component_category}' in that plan.")
    return plan_meal(
        week_start_date, new_meal, food_groups=food_groups, weekly_plan_id=weekly_plan_id,
        component_category=component_category,
    )


# ---------- Cooker execution layer (recipe detail, prep schedule, check-off) ----------

def check_off_meal(entry_id: int, status: str = "done") -> dict:
    """Mark a specific planned meal (meal_plan_entries row) as cooked (status='done') or back to pending. Use get_weekly_plan/get_plan_progress to find the entry_id."""
    conn = get_conn()
    cooked_at = "datetime('now')" if status == "done" else "NULL"
    conn.execute(
        f"UPDATE meal_plan_entries SET cooked_status = ?, cooked_at = {cooked_at} WHERE id = ? AND household_id = ?",
        (status, entry_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"entry_id": entry_id, "cooked_status": status}


def check_off_prep_step(prep_task_id: int, status: str = "done") -> dict:
    """Mark a specific prep task (from generate_prep_schedule/get_prep_schedule) as done or back to pending."""
    conn = get_conn()
    conn.execute(
        "UPDATE prep_tasks SET status = ? WHERE id = ? AND household_id = ?",
        (status, prep_task_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"prep_task_id": prep_task_id, "status": status}


def get_prep_schedule(weekly_plan_id: int | None = None) -> list[dict]:
    """Get the generated prep-task schedule for a plan (see generate_prep_schedule). Omit weekly_plan_id for the household's current/most recent plan."""
    conn = get_conn()
    if weekly_plan_id is None:
        row = conn.execute(
            "SELECT id FROM weekly_plans WHERE household_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (HOUSEHOLD_ID,),
        ).fetchone()
        if not row:
            conn.close()
            return []
        weekly_plan_id = row["id"]
    rows = conn.execute(
        "SELECT id, task_date, description, related_meal, status FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND household_id = ? ORDER BY task_date ASC, id ASC",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_prep_tasks(weekly_plan_id: int, tasks: list[dict]) -> dict:
    """
    Persist a generated prep schedule for a plan — internal helper used by
    generate_prep_schedule right after the LLM produces the task list.
    Replaces any previously-generated tasks for this plan (re-generating
    supersedes, rather than appending duplicates).
    """
    conn = get_conn()
    conn.execute("DELETE FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ?", (weekly_plan_id, HOUSEHOLD_ID))
    for t in tasks:
        if not t.get("task_date") or not t.get("description"):
            continue
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal) VALUES (?, ?, ?, ?, ?)",
            (HOUSEHOLD_ID, weekly_plan_id, t["task_date"], t["description"], t.get("related_meal", "")),
        )
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "task_count": len(tasks)}


def get_plan_progress(weekly_plan_id: int | None = None) -> dict:
    """
    Get a done-vs-outstanding view of a weekly plan: which meals have been
    cooked (see check_off_meal) and which prep tasks are done (see
    check_off_prep_step), plus counts. Omit weekly_plan_id for the
    household's current/most recent plan.
    """
    plan = get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {"weekly_plan_id": None, "meals_done": 0, "meals_total": 0, "prep_done": 0, "prep_total": 0}
    conn = get_conn()
    meal_rows = conn.execute(
        "SELECT mpe.id AS entry_id, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.cooked_status AS cooked_status "
        "FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ?",
        (plan["weekly_plan_id"],),
    ).fetchall()
    conn.close()
    prep_tasks = get_prep_schedule(plan["weekly_plan_id"])
    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "meals": [{"entry_id": m["entry_id"], "meal": m["meal"], "cooked_status": m["cooked_status"]} for m in meal_rows],
        "meals_done": sum(1 for m in meal_rows if m["cooked_status"] == "done"),
        "meals_total": len(meal_rows),
        "prep_tasks": prep_tasks,
        "prep_done": sum(1 for t in prep_tasks if t["status"] == "done"),
        "prep_total": len(prep_tasks),
    }


def get_cooker_view(weekly_plan_id: int | None = None) -> dict:
    """
    Everything the person actually cooking needs for the current (or given)
    plan in one shot: each meal with its full recipe detail (ingredients,
    instructions, timing, advance-prep notes, cooked status), plus the prep
    schedule and overall progress — powers the dedicated Cooker view page
    rather than requiring separate get_weekly_plan/get_recipe/
    get_prep_schedule calls. Omit weekly_plan_id for the household's
    current plan.
    """
    plan = get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {"weekly_plan_id": None, "meals": [], "prep_tasks": [], "meals_done": 0, "meals_total": 0, "prep_done": 0, "prep_total": 0}

    recipes_by_name = {r["name"].lower(): r for r in list_recipes()}
    meals = []
    for m in plan["meals"]:
        recipe = recipes_by_name.get((m["meal"] or "").lower())
        meals.append({
            "entry_id": m["entry_id"],
            "date": m["date"],
            "slot": m["slot"],
            "component_category": m["component_category"],
            "meal": m["meal"],
            "cooked_status": m["cooked_status"],
            "ingredients": recipe["ingredients"] if recipe else [],
            "instructions": recipe["instructions"] if recipe else [],
            "default_servings": recipe["default_servings"] if recipe else None,
            "prep_time_minutes": recipe["prep_time_minutes"] if recipe else None,
            "cook_time_minutes": recipe["cook_time_minutes"] if recipe else None,
            "advance_prep_notes": recipe["advance_prep_notes"] if recipe else "",
            "has_full_recipe": recipe is not None,
        })

    prep_tasks = get_prep_schedule(plan["weekly_plan_id"])
    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "week_start_date": plan["week_start_date"],
        "planning_mode": plan["planning_mode"],
        "status": plan["status"],
        "meals": meals,
        "meals_done": sum(1 for m in meals if m["cooked_status"] == "done"),
        "meals_total": len(meals),
        "prep_tasks": prep_tasks,
        "prep_done": sum(1 for t in prep_tasks if t["status"] == "done"),
        "prep_total": len(prep_tasks),
    }


# ---------- Household coordination & trust (Phase 3, Workstream D) ----------

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
    plan = get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {"weekly_plan_id": None, "conflicts": []}

    members = list_members()
    restrictions = [(m["name"], r.lower()) for m in members for r in m["dietary_restrictions"] if r.strip()]
    if not restrictions:
        return {"weekly_plan_id": plan["weekly_plan_id"], "conflicts": []}

    recipes_by_name = {r["name"].lower(): r for r in list_recipes()}
    conflicts = []
    for meal in plan["meals"]:
        recipe = recipes_by_name.get((meal["meal"] or "").lower())
        if not recipe:
            continue
        ingredient_text = " ".join((i.get("item") or "") for i in recipe.get("ingredients", [])).lower()
        for member_name, restriction in restrictions:
            # crude but transparent: match on the restriction's significant words
            # (skip generic words like "allergy"/"free" that would false-positive on everything)
            keywords = [w for w in restriction.replace("-", " ").split() if w not in ("allergy", "allergic", "free", "intolerance", "intolerant")]
            if keywords and any(kw in ingredient_text for kw in keywords):
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
        recipe = get_recipe(meal_name)
    except ValueError:
        return {"meal_name": meal_name, "found": False, "reason": "Not a saved recipe — likely a freeform/one-off meal with no tracked history."}
    memory = get_household_memory()
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
        (HOUSEHOLD_ID,),
    ).fetchone()
    conn.close()
    if not row:
        return {"has_nudge": False}
    return {"has_nudge": True, "meal": row["meal"], "cooked_at": row["cooked_at"]}


def set_item_store(item: str, store: str) -> dict:
    """
    Remember which store an item (or type of item) should be bought at,
    e.g. "we get paper towels at Costco" -> set_item_store("paper towels",
    "Costco"). Applies immediately to any matching item already on the
    grocery list, and automatically to future adds of that same item name.
    Pass an empty store to clear the preference.
    """
    conn = get_conn()
    if store:
        conn.execute(
            "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, ?, ?) "
            "ON CONFLICT(household_id, item) DO UPDATE SET store = excluded.store",
            (HOUSEHOLD_ID, item.strip().lower(), store),
        )
        conn.execute(
            "UPDATE grocery_items SET store = ? WHERE household_id = ? AND LOWER(item) = LOWER(?)",
            (store, HOUSEHOLD_ID, item),
        )
    else:
        conn.execute(
            "DELETE FROM item_store_preferences WHERE household_id = ? AND item = ?",
            (HOUSEHOLD_ID, item.strip().lower()),
        )
        conn.execute(
            "UPDATE grocery_items SET store = '' WHERE household_id = ? AND LOWER(item) = LOWER(?)",
            (HOUSEHOLD_ID, item),
        )
    conn.commit()
    conn.close()
    return {"item": item, "store": store}


def get_grocery_list_by_store(status: str = "needed") -> dict:
    """
    Get the grocery list split into store groups (see set_item_store),
    each internally grouped by section like get_grocery_list_by_section —
    use this instead of get_grocery_list_by_section when the household
    shops at more than one store, so the list reads like separate trips
    rather than one mixed pile. Items with no assigned store are grouped
    under "Unassigned".
    """
    items = list_grocery_list(status=status)
    by_store: dict[str, list[dict]] = {}
    for it in items:
        store = it.get("store") or "Unassigned"
        by_store.setdefault(store, []).append(it)
    stores = []
    for store, store_items in by_store.items():
        sections: dict[str, list[dict]] = {s: [] for s in _GROCERY_SECTION_ORDER}
        for it in store_items:
            cat = _GROCERY_CATEGORY_ALIASES.get(it["category"], it["category"])
            sections.setdefault("other", [])
            sections[cat if cat in sections else "other"].append(it)
        stores.append({
            "store": store,
            "sections": [{"section": s, "items": sections[s]} for s in _GROCERY_SECTION_ORDER if sections[s]],
        })
    return {"stores": stores}


def get_learning_summary() -> dict:
    """
    A visible, human-readable snapshot of what the app has actually learned
    so far — use when the user asks something like "what have you picked up
    about us?" or "has this gotten smarter?" Distinct from
    get_household_memory (raw preference values): this is aggregate stats
    that show adaptation over time.
    """
    recipes = list_recipes()
    liked = [r for r in recipes if r["rating"] == "liked"]
    disliked = [r for r in recipes if r["rating"] == "disliked"]
    excluded = [r for r in recipes if r["temporarily_excluded"]]
    deviation_notes = 0
    conn = get_conn()
    deviation_notes = conn.execute(
        "SELECT COUNT(*) AS c FROM recipe_notes WHERE household_id = ? AND note_type = 'deviation'",
        (HOUSEHOLD_ID,),
    ).fetchone()["c"]
    conn.close()
    return {
        "recipes_tracked": len(recipes),
        "recipes_liked": len(liked),
        "recipes_disliked": len(disliked),
        "recipes_temporarily_excluded": len(excluded),
        "cooking_deviations_logged": deviation_notes,
        "liked_recipe_names": [r["name"] for r in liked],
        "disliked_recipe_names": [r["name"] for r in disliked],
    }


# ---------- Eater share link (read-only, tokenized, no new auth) ----------

def get_or_create_share_link() -> dict:
    """
    Get this household's read-only share-link token for the weekly meal
    plan, creating one on first use. The token is stable — it's not tied to
    a specific plan, so the same link keeps working and always shows
    whichever plan is most recent as new weeks get generated. No login is
    involved; anyone with the link can view the current plan, nothing else.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT token FROM share_links WHERE household_id = ? ORDER BY created_at ASC LIMIT 1",
        (HOUSEHOLD_ID,),
    ).fetchone()
    if row:
        token = row["token"]
    else:
        token = secrets.token_urlsafe(16)
        conn.execute(
            "INSERT INTO share_links (household_id, token) VALUES (?, ?)",
            (HOUSEHOLD_ID, token),
        )
        conn.commit()
    conn.close()
    return {"token": token}


def get_shared_weekly_plan(token: str) -> dict | None:
    """
    Resolve a share-link token to the household's current (most recent)
    weekly plan. Returns None if the token doesn't match anything, so the
    caller can 404 rather than leak whether a token almost matched. Only
    meal-plan data is returned — no other household info (dietary details,
    chores, etc.) is exposed through this path.
    """
    conn = get_conn()
    row = conn.execute("SELECT household_id FROM share_links WHERE token = ?", (token,)).fetchone()
    household = None
    if row:
        household = conn.execute(
            "SELECT name FROM households WHERE id = ?", (row["household_id"],)
        ).fetchone()
    conn.close()
    if not row:
        return None
    plan = get_weekly_plan()
    plan["household_name"] = household["name"] if household else ""
    return plan


# ---------- Household memory (transparency & correction) ----------

def get_household_memory() -> dict:
    """
    Return a plain summary of everything the app has learned/saved about
    this household: each member's dietary restrictions, favorite proteins
    and cuisines, standing dislikes, cooking-time preference, freeform
    notes, and household goals. Powers a "what the app knows" view the user
    can review and correct directly (see edit_preference/delete_preference),
    and is useful context to pull before discussing or generating meal
    plans.
    """
    conn = get_conn()
    prefs = conn.execute("SELECT * FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)).fetchone()
    members = conn.execute(
        "SELECT name, age_group, dietary_restrictions_json FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    household = conn.execute("SELECT goals FROM households WHERE id = ?", (HOUSEHOLD_ID,)).fetchone()
    conn.close()
    return {
        "members": [
            {
                "name": m["name"], "age_group": m["age_group"],
                "dietary_restrictions": json.loads(m["dietary_restrictions_json"]),
            }
            for m in members
        ],
        "goals": household["goals"] if household else "",
        "notes": prefs["notes"] if prefs else "",
        "protein_preferences": json.loads(prefs["protein_preferences_json"]) if prefs else {},
        "cuisine_preferences": json.loads(prefs["cuisine_preferences_json"]) if prefs else [],
        "dislikes": json.loads(prefs["dislikes_json"]) if prefs else [],
        "cooking_time_preference": prefs["cooking_time_preference"] if prefs else "",
        "novelty_preference": prefs["novelty_preference"] if prefs else "balanced",
        "planning_mode": prefs["planning_mode"] if prefs else "day_based",
    }


def edit_preference(field: str, value) -> dict:
    """
    Directly set a household meal-preference field to a new value — for
    correcting something the app got wrong, whether from the "what we
    know" view or conversationally ("actually make cooking time preference
    quick"). Valid fields: 'notes' (str), 'cooking_time_preference' (str),
    'cuisine_preferences' (list of str — replaces the whole list),
    'dislikes' (list of str — replaces the whole list; for adding just one
    new dislike in conversation, prefer add_food_dislikes instead so it
    merges rather than requiring you to pass the full existing list),
    'protein_preferences' (dict of protein -> how-often, e.g. {"chicken":
    "several times a week"} — merged into existing), 'novelty_preference'
    (str: 'mostly_favorites', 'balanced', or 'surprise_me_often' — how often
    new recipes get surfaced when generating a weekly plan). To remove a
    single item from a list rather than replacing
    it wholesale, use delete_preference instead.
    """
    valid_fields = {
        "notes", "cooking_time_preference", "cuisine_preferences", "protein_preferences",
        "dislikes", "novelty_preference",
    }
    if field not in valid_fields:
        raise ValueError(f"Unknown preference field '{field}'. Valid fields: {sorted(valid_fields)}")
    if field == "dislikes":
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO meal_preferences (household_id, dislikes_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(household_id) DO UPDATE SET dislikes_json = excluded.dislikes_json, updated_at = datetime('now')
            """,
            (HOUSEHOLD_ID, json.dumps(value)),
        )
        conn.commit()
        conn.close()
        return {"dislikes": value}
    if field == "cuisine_preferences":
        return set_household_meal_preferences(cuisine_preferences=value, mark_complete=False)
    if field == "protein_preferences":
        return set_household_meal_preferences(protein_preferences=value, mark_complete=False)
    if field == "notes":
        return set_household_meal_preferences(notes=value, mark_complete=False)
    if field == "novelty_preference":
        return set_household_meal_preferences(novelty_preference=value, mark_complete=False)
    return set_household_meal_preferences(cooking_time_preference=value, mark_complete=False)


def delete_preference(field: str, item: str | None = None) -> dict:
    """
    Remove a remembered preference. For list fields ('dislikes' or
    'cuisine_preferences'), pass item = the specific value to remove
    (case-insensitive match). For 'protein_preferences', item = the
    protein name to forget. For scalar fields ('notes' or
    'cooking_time_preference'), omit item to clear the field entirely.
    """
    conn = get_conn()
    existing = conn.execute("SELECT * FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)).fetchone()
    if not existing:
        conn.close()
        raise ValueError("No saved preferences yet.")

    if field == "dislikes":
        updated = [d for d in json.loads(existing["dislikes_json"]) if d.lower() != (item or "").lower()]
        conn.execute(
            "UPDATE meal_preferences SET dislikes_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(updated), HOUSEHOLD_ID),
        )
    elif field == "cuisine_preferences":
        updated = [c for c in json.loads(existing["cuisine_preferences_json"]) if c.lower() != (item or "").lower()]
        conn.execute(
            "UPDATE meal_preferences SET cuisine_preferences_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(updated), HOUSEHOLD_ID),
        )
    elif field == "protein_preferences":
        current = dict(json.loads(existing["protein_preferences_json"]))
        current.pop(item, None)
        conn.execute(
            "UPDATE meal_preferences SET protein_preferences_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(current), HOUSEHOLD_ID),
        )
    elif field == "notes":
        conn.execute(
            "UPDATE meal_preferences SET notes = '', updated_at = datetime('now') WHERE household_id = ?", (HOUSEHOLD_ID,)
        )
    elif field == "cooking_time_preference":
        conn.execute(
            "UPDATE meal_preferences SET cooking_time_preference = '', updated_at = datetime('now') WHERE household_id = ?",
            (HOUSEHOLD_ID,),
        )
    else:
        conn.close()
        raise ValueError(f"Unknown preference field '{field}'.")
    conn.commit()
    conn.close()
    return {"field": field, "item": item, "deleted": True}


# ---------- Grocery list ----------

# Standard store sections, in a sensible shopping order. "meat" is an alias
# kept for rows saved before "meat/seafood" was standardized.
_GROCERY_SECTION_ORDER = ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]
_GROCERY_CATEGORY_ALIASES = {"meat": "meat/seafood", "seafood": "meat/seafood"}

_UNIT_ALIASES = {
    "cup": "cup", "cups": "cup", "c": "cup",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "g": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml",
    "l": "l", "liter": "l", "liters": "l",
}

_QTY_RE = re.compile(r"^(\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)\s*([a-zA-Z]*)$")


def _parse_quantity(qty: str) -> tuple[float, str | None] | None:
    """Parse a freeform quantity string into (amount, normalized_unit_or_None). Returns None if unparseable (e.g. blank, or freeform text like 'a bunch')."""
    if not qty or not qty.strip():
        return None
    match = _QTY_RE.match(qty.strip().lower())
    if not match:
        return None
    amount_str, unit_str = match.group(1), match.group(2).strip()
    try:
        if "/" in amount_str:
            parts = amount_str.split(" ")
            if len(parts) == 2:
                whole, frac = parts
                num, den = frac.split("/")
                amount = float(whole) + float(num) / float(den)
            else:
                num, den = amount_str.split("/")
                amount = float(num) / float(den)
        else:
            amount = float(amount_str)
    except (ValueError, ZeroDivisionError):
        return None
    return amount, (_UNIT_ALIASES.get(unit_str, unit_str) or None)


_UNIT_PLURALS = {"cup": "cups", "lb": "lbs"}


def _format_quantity(amount: float, unit: str | None) -> str:
    amount_str = f"{amount:g}"
    if not unit:
        return amount_str
    display_unit = _UNIT_PLURALS.get(unit, unit) if amount != 1 else unit
    return f"{amount_str} {display_unit}"


def _try_consolidate_quantity(existing_qty: str, new_qty: str) -> tuple[str, bool]:
    """
    Try to merge two quantity strings for the same grocery item. Returns
    (resulting_quantity_string, was_merged). Merges when both are parseable
    and share the same unit (e.g. "2 cups" + "1 cup" -> "3 cups"), or when
    one side is blank. When units are both present but don't reconcile
    (e.g. "2 cups flour" + "1 lb flour"), nothing is guessed — both amounts
    are kept together on one line so the shopper sees both rather than a
    silently wrong conversion.
    """
    if not (existing_qty or "").strip():
        return new_qty, True
    if not (new_qty or "").strip():
        return existing_qty, True
    existing_parsed = _parse_quantity(existing_qty)
    new_parsed = _parse_quantity(new_qty)
    if existing_parsed and new_parsed and existing_parsed[1] == new_parsed[1]:
        return _format_quantity(existing_parsed[0] + new_parsed[0], existing_parsed[1]), True
    return f"{existing_qty} + {new_qty}", False


def add_grocery_item(
    item: str,
    quantity: str = "",
    category: str = "other",
    added_by: str = "user",
    source_weekly_plan_id: int | None = None,
) -> dict:
    """
    Add an item to the grocery list. If an item with the same name is
    already on the list (status 'needed'), the quantity is consolidated
    into that single line — e.g. "2 cups flour" + "1 cup flour" becomes
    "3 cups flour" — instead of creating a duplicate line. If the
    quantities can't be reconciled (different, incompatible units), both
    are kept together on the one line rather than silently guessing a
    conversion. category should be one of: produce, dairy, meat/seafood,
    pantry, frozen, other — pick the one that actually matches the item so
    the list stays organized by store section. Leave source_weekly_plan_id
    unset for anything a person asked for directly (or an ad hoc one-off
    meal) — it marks the item as a standing want that should never be
    auto-cleared. It's set automatically when ingredients come from a
    generated weekly plan (see plan_meal/generate_weekly_plan), so
    clear_stale_grocery_items can tell a current week's ingredients apart
    from an old week's leftovers.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT id, quantity FROM grocery_items WHERE household_id = ? AND status = 'needed' AND LOWER(item) = LOWER(?)",
        (HOUSEHOLD_ID, item),
    ).fetchone()
    pref = conn.execute(
        "SELECT store FROM item_store_preferences WHERE household_id = ? AND item = ?",
        (HOUSEHOLD_ID, item.strip().lower()),
    ).fetchone()
    preferred_store = pref["store"] if pref else ""
    if existing:
        merged_qty, merged = _try_consolidate_quantity(existing["quantity"] or "", quantity)
        conn.execute(
            "UPDATE grocery_items SET quantity = ?, category = ?, source_weekly_plan_id = ? WHERE id = ?",
            (merged_qty, category, source_weekly_plan_id, existing["id"]),
        )
        conn.commit()
        item_id = existing["id"]
        conn.close()
        return {"item_id": item_id, "item": item, "quantity": merged_qty, "merged": True, "units_reconciled": merged}

    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, store) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (HOUSEHOLD_ID, item, quantity, category, added_by, source_weekly_plan_id, preferred_store),
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return {"item_id": item_id, "item": item, "quantity": quantity, "merged": False, "units_reconciled": True}


def add_grocery_items(items: list, category: str = "other", added_by: str = "user") -> dict:
    """
    Add several items to the grocery list at once. Each entry can be a
    plain string (e.g. "milk") or, when you know more, a dict like
    {"item": "flour", "quantity": "2 cups", "category": "pantry"} — mix
    and match as needed. Prefer setting an accurate category per item
    (produce, dairy, meat/seafood, pantry, frozen, other) so the list
    stays organized by store section; the category argument is only a
    fallback for entries you didn't categorize individually. Quantities
    are consolidated with any matching item already on the list rather
    than creating duplicate lines (see add_grocery_item).
    """
    added, merged = [], []
    for raw in items:
        if isinstance(raw, dict):
            name = (raw.get("item") or "").strip()
            qty = raw.get("quantity", "")
            cat = raw.get("category") or category
        else:
            name = (raw or "").strip()
            qty = ""
            cat = category
        if not name:
            continue
        result = add_grocery_item(name, quantity=qty, category=cat, added_by=added_by)
        (merged if result["merged"] else added).append(name)
    return {"added": added, "merged_with_existing": merged}


def list_grocery_list(status: str = "needed") -> list[dict]:
    """List grocery items, optionally filtered by status (needed/in_cart/purchased/all)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, status, store FROM grocery_items WHERE household_id = ? AND (? = 'all' OR status = ?) ORDER BY category, item",
        (HOUSEHOLD_ID, status, status),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_grocery_list_by_section(status: str = "needed") -> dict:
    """
    Get the grocery list grouped into standard store sections (produce,
    dairy, meat/seafood, pantry, frozen, other) in a sensible shopping
    order, rather than a flat list. Use this whenever showing or reviewing
    the grocery list to the user so it reads like something they can
    actually shop from, aisle by aisle, instead of a flat ingredient dump.
    """
    items = list_grocery_list(status=status)
    sections: dict[str, list[dict]] = {s: [] for s in _GROCERY_SECTION_ORDER}
    for it in items:
        cat = _GROCERY_CATEGORY_ALIASES.get(it["category"], it["category"])
        sections.setdefault("other", [])
        sections[cat if cat in sections else "other"].append(it)
    return {"sections": [{"section": s, "items": sections[s]} for s in _GROCERY_SECTION_ORDER if sections[s]]}


def consolidate_grocery_list(status: str = "needed") -> dict:
    """
    Merge any duplicate lines already on the list (same item name,
    case-insensitive) into one line each, combining quantities with the
    same logic add_grocery_item uses automatically for new additions.
    Call this if the user asks to clean up/consolidate the list, or if you
    notice the same item appears more than once — items added since
    consolidation shipped shouldn't duplicate going forward, but this
    cleans up anything added before that, or any way it happens to slip
    through.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category FROM grocery_items WHERE household_id = ? AND status = ? ORDER BY id",
        (HOUSEHOLD_ID, status),
    ).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["item"].strip().lower(), []).append(dict(r))

    merged_count = 0
    for entries in groups.values():
        if len(entries) < 2:
            continue
        keep = entries[0]
        merged_qty = keep["quantity"] or ""
        for extra in entries[1:]:
            merged_qty, _ = _try_consolidate_quantity(merged_qty, extra["quantity"] or "")
            conn.execute("DELETE FROM grocery_items WHERE id = ?", (extra["id"],))
            merged_count += 1
        conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (merged_qty, keep["id"]))
    conn.commit()
    conn.close()
    return {"lines_merged_away": merged_count}


def clear_stale_grocery_items(current_weekly_plan_id: int | None = None) -> dict:
    """
    Remove 'needed' grocery items that came from an OLDER generated weekly
    plan — not the current one — and were never marked purchased. This is
    the fix for quantities silently stacking up across several weeks'
    plans onto the same line (e.g. "9 lbs chicken breast" built from 4
    different weeks). Items a person added directly, or that came from an
    ad hoc one-off meal rather than a generated week, are never touched —
    those represent a standing want, not a stale one. Pass
    current_weekly_plan_id explicitly when you already know it (e.g. right
    after creating a new plan); otherwise it falls back to whichever plan
    get_weekly_plan considers most recent. Called automatically at the
    start of every generate_weekly_plan; also fine to call directly if the
    user notices buildup and asks to clean it up.
    """
    current_id = current_weekly_plan_id
    if current_id is None:
        current_id = get_weekly_plan().get("weekly_plan_id")
    conn = get_conn()
    if current_id is None:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status = 'needed' "
            "AND source_weekly_plan_id IS NOT NULL",
            (HOUSEHOLD_ID,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status = 'needed' "
            "AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ?",
            (HOUSEHOLD_ID, current_id),
        ).fetchall()
    removed = [r["item"] for r in rows]
    if rows:
        conn.executemany("DELETE FROM grocery_items WHERE id = ?", [(r["id"],) for r in rows])
        conn.commit()
    conn.close()
    return {"removed_count": len(removed), "removed_items": removed}


def clear_grocery_list(status: str = "needed") -> dict:
    """
    Remove ALL items with the given status (default 'needed') in one shot —
    a full reset, not a merge or a staleness check. Use only when the user
    explicitly asks to clear/empty/start the grocery list over (e.g. "wipe
    the list, we're starting fresh"). For routine cleanup use
    consolidate_grocery_list (duplicates) or clear_stale_grocery_items (old
    plan leftovers) instead — this one has no way to know what's still
    actually needed.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM grocery_items WHERE household_id = ? AND (? = 'all' OR status = ?)",
        (HOUSEHOLD_ID, status, status),
    ).fetchall()
    count = len(rows)
    conn.execute(
        "DELETE FROM grocery_items WHERE household_id = ? AND (? = 'all' OR status = ?)",
        (HOUSEHOLD_ID, status, status),
    )
    conn.commit()
    conn.close()
    return {"removed_count": count}


def mark_grocery_item(item_id: int, status: str = "purchased") -> dict:
    """
    Update a grocery item's status (needed/in_cart/purchased). Marking
    something purchased also adds it to tracked pantry/fridge inventory
    automatically (source='grocery_checkoff'), with expiration left unset —
    see update_inventory/get_inventory.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID)
    ).fetchone()
    conn.execute(
        "UPDATE grocery_items SET status = ? WHERE id = ? AND household_id = ?",
        (status, item_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    if status == "purchased" and row:
        _add_to_inventory(row["item"], row["quantity"] or "", source="grocery_checkoff", category=row["category"])
    return {"item_id": item_id, "status": status}


def remove_grocery_item(item_id: int) -> dict:
    """Delete an item from the grocery list."""
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "deleted": True}


# ---------- Pantry & fridge inventory ----------
# Real tracked baseline of what's currently on hand, distinct from the
# grocery list (what's still needed). Chat mention is the only capture
# method this phase — no manual-entry form, no photo recognition (both
# pushed to a later phase once this simpler path is proven out).

def _try_subtract_quantity(existing_qty: str, minus_qty: str) -> tuple[str | None, bool]:
    """
    Try to subtract minus_qty from existing_qty for the same inventory item.
    Returns (resulting_quantity_or_None, reconciled). None means "fully used
    up, remove the row" — either because minus_qty was blank (caller meant
    "all of it") or because subtracting brought it to zero or below.
    reconciled=False means the units didn't match closely enough to safely
    subtract, so existing_qty is returned unchanged rather than guessing.
    """
    if not (minus_qty or "").strip():
        return None, True
    existing_parsed = _parse_quantity(existing_qty)
    minus_parsed = _parse_quantity(minus_qty)
    if not existing_parsed:
        # Existing quantity is freeform/unset (e.g. "a bunch") — can't do
        # precise math, so treat any explicit "used some" as using it all
        # rather than leaving a stale, unreconciled line behind.
        return None, True
    if minus_parsed and existing_parsed[1] == minus_parsed[1]:
        remaining = existing_parsed[0] - minus_parsed[0]
        if remaining <= 0:
            return None, True
        return _format_quantity(remaining, existing_parsed[1]), True
    return existing_qty, False


def _add_to_inventory(
    item: str,
    quantity: str = "",
    source: str = "chat",
    expiration_date: str | None = None,
    category: str | None = None,
) -> dict:
    conn = get_conn()
    existing = conn.execute(
        "SELECT id, quantity FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
        (HOUSEHOLD_ID, item),
    ).fetchone()
    if existing:
        merged_qty, _ = _try_consolidate_quantity(existing["quantity"] or "", quantity)
        fields = "quantity = ?, source = ?, updated_at = datetime('now')"
        params = [merged_qty, source]
        if expiration_date:
            fields += ", expiration_date = ?"
            params.append(expiration_date)
        if category:
            fields += ", category = ?"
            params.append(category)
        params.append(existing["id"])
        conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ?", params)
        conn.commit()
        item_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category) VALUES (?, ?, ?, ?, ?, ?)",
            (HOUSEHOLD_ID, item, quantity, source, expiration_date, category or "other"),
        )
        conn.commit()
        item_id = cur.lastrowid
    conn.close()
    return {"item_id": item_id, "item": item}


def update_inventory(
    item: str,
    action: str,
    quantity: str = "",
    expiration_date: str | None = None,
    category: str | None = None,
) -> dict:
    """
    Update pantry/fridge inventory from a chat mention — this is the
    primary way inventory gets captured (there's also a dedicated Inventory
    view page for direct editing), so call this proactively any time the
    user mentions buying, using, or running out of something, the same way
    preferences get captured proactively. action is one of:
      - "add": something was bought/received, e.g. "picked up a rotisserie chicken"
      - "use": some (or all, if quantity is left blank) of an item was used,
        e.g. "used the last of the spinach" (blank quantity) or "used a cup
        of the rice" (quantity given)
      - "remove": the item is gone for any other reason (spoiled, thrown
        out) — same effect as "use" with a blank quantity
      - "set": state an absolute amount currently on hand, e.g. "I have
        about 2 lbs of ground beef left"
    quantity is a freeform string like "2 lbs" or "1 dozen" — leave it blank
    when the person didn't mention an amount. expiration_date (ISO date) is
    optional — only set it if the person actually mentioned one; leave it
    unset otherwise rather than guessing. category should be one of:
    produce, dairy, meat/seafood, pantry, frozen, other — same taxonomy as
    the grocery list — so the Inventory view stays organized; pick whichever
    matches the item, defaults to 'other' if omitted.
    """
    if action == "add":
        return _add_to_inventory(item, quantity, source="chat", expiration_date=expiration_date, category=category)

    if action == "set":
        conn = get_conn()
        existing = conn.execute(
            "SELECT id FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
            (HOUSEHOLD_ID, item),
        ).fetchone()
        if existing:
            fields = "quantity = ?, updated_at = datetime('now')"
            params = [quantity]
            if category:
                fields += ", category = ?"
                params.append(category)
            params.append(existing["id"])
            conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ?", params)
            conn.commit()
            item_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO inventory_items (household_id, item, quantity, source, category) VALUES (?, ?, ?, 'chat', ?)",
                (HOUSEHOLD_ID, item, quantity, category or "other"),
            )
            conn.commit()
            item_id = cur.lastrowid
        conn.close()
        return {"item_id": item_id, "item": item, "quantity": quantity}

    if action in ("use", "remove"):
        conn = get_conn()
        existing = conn.execute(
            "SELECT id, quantity FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
            (HOUSEHOLD_ID, item),
        ).fetchone()
        if not existing:
            conn.close()
            return {"item": item, "found": False}
        remaining, reconciled = _try_subtract_quantity(existing["quantity"] or "", quantity)
        if remaining is None:
            conn.execute("DELETE FROM inventory_items WHERE id = ?", (existing["id"],))
            conn.commit()
            conn.close()
            return {"item": item, "removed": True}
        conn.execute(
            "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
            (remaining, existing["id"]),
        )
        conn.commit()
        conn.close()
        return {"item": item, "quantity": remaining, "units_reconciled": reconciled}

    raise ValueError(f"Unknown inventory action '{action}'.")


def update_inventory_items(items: list, action: str = "add") -> dict:
    """
    Update several inventory items at once — use this (not repeated
    update_inventory calls) whenever the user mentions more than one item
    in the same breath, which is exactly what happens the first time
    someone populates inventory ("here's what's in our pantry: rice, olive
    oil, canned tomatoes, flour..."). Each entry can be a plain string (uses
    the shared `action`) or, when you know more, a dict like {"item":
    "flour", "action": "add", "quantity": "2 cups", "expiration_date":
    "2026-09-01", "category": "pantry"} to mix actions/quantities/categories
    within one call. See update_inventory for what each action/category
    means — fill in category per item when populating a batch so everything
    lands in the right section of the Inventory view immediately.
    """
    results = []
    for raw in items:
        if isinstance(raw, dict):
            name = (raw.get("item") or "").strip()
            act = raw.get("action") or action
            qty = raw.get("quantity", "")
            exp = raw.get("expiration_date")
            cat = raw.get("category")
        else:
            name = (raw or "").strip()
            act = action
            qty = ""
            exp = None
            cat = None
        if not name:
            continue
        results.append(update_inventory(name, act, quantity=qty, expiration_date=exp, category=cat))
    return {"updated": results}


def get_inventory() -> list[dict]:
    """
    List everything currently tracked in pantry/fridge inventory. Check
    this before suggesting grocery additions for staples that might already
    be on hand, and before generating a weekly plan (already threaded into
    generate_weekly_plan's context automatically).
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, source, expiration_date, category FROM inventory_items WHERE household_id = ? ORDER BY item",
        (HOUSEHOLD_ID,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_inventory_by_section() -> dict:
    """
    Get pantry/fridge inventory grouped into store sections (produce,
    dairy, meat/seafood, pantry, frozen, other) — same grouping as
    get_grocery_list_by_section. Powers the dedicated Inventory view page;
    use this instead of get_inventory whenever showing the full inventory
    to the user so it reads organized rather than a flat list.
    """
    items = get_inventory()
    sections: dict[str, list[dict]] = {s: [] for s in _GROCERY_SECTION_ORDER}
    for it in items:
        cat = _GROCERY_CATEGORY_ALIASES.get(it["category"], it["category"])
        sections.setdefault("other", [])
        sections[cat if cat in sections else "other"].append(it)
    return {"sections": [{"section": s, "items": sections[s]} for s in _GROCERY_SECTION_ORDER if sections[s]]}


def remove_inventory_item(item_id: int) -> dict:
    """Remove a single inventory item outright (e.g. it spoiled, or was added by mistake) — used by the Inventory view's delete control."""
    conn = get_conn()
    conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "removed": True}


# ---------- internal helpers ----------

def _get_or_create_member(conn, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND name = ?", (HOUSEHOLD_ID, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO members (household_id, name) VALUES (?, ?)", (HOUSEHOLD_ID, name))
    conn.commit()
    return cur.lastrowid
