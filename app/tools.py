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
import math
import os
import re
import secrets
from datetime import date, datetime, timedelta
from .db import get_conn

HOUSEHOLD_ID = 1

# The app's own public URL, so a tool can hand back a real, absolute link
# (e.g. for the Eater self-service link) instead of the chat agent having
# to guess/type out a domain itself — which it has no way to know and will
# otherwise hallucinate. Set via Railway (or wherever this is hosted) env
# vars, e.g. PUBLIC_BASE_URL=https://home-manager-production-4949.up.railway.app
# (no trailing slash). Falls back to a relative path if unset (e.g. local
# dev), which still works fine since the app only has one host there.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _absolute_url(path: str) -> str:
    return f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path

_FREQUENCY_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91, "once": None}

# Junk "no answer" values that sometimes get written into a restrictions
# list instead of just an empty list — filtered out in
# set_member_dietary_restrictions so they never persist as if they were a
# real restriction (see Phase 4, §4.1 follow-up fix).
_NON_RESTRICTION_VALUES = {"none", "n/a", "na", "no restrictions", "no allergies", "nothing", "no", "-", ""}


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


def set_member_dietary_restrictions(name: str, restrictions: list[str], replace: bool = False) -> dict:
    """
    Add dietary restrictions/allergies for a household member (e.g.
    ['vegetarian', 'peanut allergy']). Defaults to fetch-then-merge — merges
    with whatever's already saved for this person rather than overwriting
    it, deduplicated case-insensitively — so it's safe to call from a
    one-off mid-conversation mention ("my partner doesn't eat shellfish")
    without risking anything previously saved for them being silently lost
    (Phase 4, §4.1 Fix 2). Pass replace=True only when the person is
    stating their complete, authoritative list right now and anything not
    listed should be dropped — this is the right choice during onboarding
    (initial setup or a full redo), not for an ad hoc mention. Pass an
    empty list with replace=True to clear all restrictions for someone.
    """
    conn = get_conn()
    member_id = _get_or_create_member(conn, name)
    # Placeholder non-answers (an LLM occasionally writes "None" instead of
    # an empty list when someone says they have no restrictions) should
    # never actually get stored as a restriction — filtered out on both
    # merge and replace so this can't silently accumulate as real entries.
    restrictions = [r for r in restrictions if r.strip().lower() not in _NON_RESTRICTION_VALUES]
    if replace:
        merged = list(restrictions)
    else:
        existing_row = conn.execute(
            "SELECT dietary_restrictions_json FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        existing = json.loads(existing_row["dietary_restrictions_json"]) if existing_row else []
        existing = [r for r in existing if r.strip().lower() not in _NON_RESTRICTION_VALUES]
        seen_lower = {r.strip().lower() for r in existing}
        merged = list(existing)
        for r in restrictions:
            if r.strip() and r.strip().lower() not in seen_lower:
                merged.append(r)
                seen_lower.add(r.strip().lower())
    conn.execute(
        "UPDATE members SET dietary_restrictions_json = ? WHERE id = ?",
        (json.dumps(merged), member_id),
    )
    conn.commit()
    conn.close()
    _log_preference_event(f"member:{name}:dietary_restrictions", "write")
    return {"name": name, "dietary_restrictions": merged}


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
    _log_preference_event("goals", "write")
    return {"goals": goals}


def _log_preference_event(field: str, action: str) -> None:
    """
    Record one row in the append-only preference_events log — powers the
    Memory view's growth counter ("You've taught me N things this month").
    Every write counts on purpose (Phase 6 PRD §6): a correction is still a
    sign of active engagement, so this doesn't dedupe repeated edits to the
    same field. Called only from the specific correction/addition entry
    points below (not from set_household_meal_preferences directly, which
    onboarding calls in bulk) so getting through onboarding doesn't inflate
    "this month" on day one.
    """
    conn = get_conn()
    conn.execute(
        "INSERT INTO preference_events (household_id, field, action) VALUES (?, ?, ?)",
        (HOUSEHOLD_ID, field, action),
    )
    conn.commit()
    conn.close()


def count_preference_events_this_month() -> int:
    """Count preference-write events (create/update/delete) in the current calendar month — backs the Memory view's growth counter."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM preference_events "
        "WHERE household_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')",
        (HOUSEHOLD_ID,),
    ).fetchone()
    conn.close()
    return row["n"]


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
    _log_preference_event("dislikes", "write")
    return {"dislikes": merged}


def add_usual_stores(items: list[str]) -> dict:
    """
    Remember one or more stores/chains this household usually shops at
    (e.g. ['Trader Joe\'s', 'Costco']) — used to populate store suggestions
    in the grocery list view. Call this whenever the user mentions where
    they usually shop, even mid-conversation. Merges with anything already
    saved rather than replacing it.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT usual_stores_json FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    current = json.loads(existing["usual_stores_json"]) if existing else []
    merged = list(dict.fromkeys(current + [i.strip() for i in items if i.strip()]))
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, usual_stores_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET usual_stores_json = excluded.usual_stores_json, updated_at = datetime('now')
        """,
        (HOUSEHOLD_ID, json.dumps(merged)),
    )
    conn.commit()
    conn.close()
    _log_preference_event("usual_stores", "write")
    return {"usual_stores": merged}


def add_store_typical_items(store: str, items: list[str]) -> dict:
    """
    Remember one or more items typically bought at a specific usual store
    (e.g. store='Costco', items=['paper towels', 'rotisserie chicken']) —
    used to surface "usually get here" suggestions in the By Store view of
    the grocery list, which the user can confirm to add to the current
    list. Call this whenever the user mentions what they typically get
    somewhere, even mid-conversation. Merges with anything already saved
    for that store rather than replacing it; doesn't require the store to
    already be in usual_stores first, though pairing the two makes the
    suggestions actually surface in the grocery list.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT store_typical_items_json FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    current = json.loads(existing["store_typical_items_json"]) if existing else {}
    store_items = list(dict.fromkeys(current.get(store, []) + [i.strip() for i in items if i.strip()]))
    current[store] = store_items
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, store_typical_items_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET store_typical_items_json = excluded.store_typical_items_json, updated_at = datetime('now')
        """,
        (HOUSEHOLD_ID, json.dumps(current)),
    )
    conn.commit()
    conn.close()
    _log_preference_event("store_typical_items", "write")
    return {"store": store, "typical_items": store_items}


def remove_store_typical_item(store: str, item: str) -> dict:
    """Remove a single item from a store's typical-items list (case-insensitive match). Leaves the store itself (in usual_stores) untouched."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT store_typical_items_json FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    current = json.loads(existing["store_typical_items_json"]) if existing else {}
    store_items = [i for i in current.get(store, []) if i.lower() != (item or "").lower()]
    current[store] = store_items
    conn.execute(
        "UPDATE meal_preferences SET store_typical_items_json = ?, updated_at = datetime('now') WHERE household_id = ?",
        (json.dumps(current), HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    _log_preference_event("store_typical_items", "delete")
    return {"store": store, "typical_items": store_items}


def set_household_meal_preferences(
    notes: str = "",
    protein_preferences: dict[str, int | str] | None = None,
    cuisine_preferences: list[str] | None = None,
    cooking_time_preference: str = "",
    novelty_preference: str = "",
    eating_style: str | None = None,
    dinners_per_week: int | None = None,
    breakfasts_per_week: int | None = None,
    lunches_per_week: int | None = None,
    mark_complete: bool = True,
) -> dict:
    """
    Save household food preferences: freeform notes (the "let me type it"
    catch-all), protein preferences (a 1-5 rating of how much the household
    likes each protein, e.g. {"chicken": 5, "beef": 2} — 5 is a favorite,
    1 means avoid entirely; reflects preference, health, and budget
    together, not just taste — used to decide how often each protein shows
    up in a generated plan), favorite
    cuisines, a cooking time preference, novelty_preference (how often
    new recipes should get surfaced: 'mostly_favorites', 'balanced', or
    'surprise_me_often' — even 'mostly_favorites' still gets occasional new
    recipes, it's not "never"), eating_style (freeform, e.g. "keto",
    "high-protein, low-carb" — a style/goal for meals to follow, distinct
    from hard dietary_restrictions), and dinners_per_week/breakfasts_per_week/
    lunches_per_week (each 1-7, how many days a typical week should actually
    plan that meal — a household that's only home for dinner 4 nights
    doesn't need all 7 filled in, same idea for breakfast/lunch). Any field
    can be omitted/partial — pass what you have. By default this marks
    meal-planning onboarding as complete; pass mark_complete=False if
    you're saving a partial update mid-conversation.
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
    merged_eating_style = eating_style if eating_style is not None else (existing["eating_style"] if existing else "")
    merged_dinners_per_week = dinners_per_week if dinners_per_week is not None else (
        existing["dinners_per_week"] if existing else 7
    )
    merged_breakfasts_per_week = breakfasts_per_week if breakfasts_per_week is not None else (
        existing["breakfasts_per_week"] if existing else 7
    )
    merged_lunches_per_week = lunches_per_week if lunches_per_week is not None else (
        existing["lunches_per_week"] if existing else 7
    )

    conn.execute(
        """
        INSERT INTO meal_preferences
            (household_id, notes, protein_preferences_json, cuisine_preferences_json, cooking_time_preference, novelty_preference, eating_style, dinners_per_week, breakfasts_per_week, lunches_per_week, onboarding_complete, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET
            notes = excluded.notes,
            protein_preferences_json = excluded.protein_preferences_json,
            cuisine_preferences_json = excluded.cuisine_preferences_json,
            cooking_time_preference = excluded.cooking_time_preference,
            novelty_preference = excluded.novelty_preference,
            eating_style = excluded.eating_style,
            dinners_per_week = excluded.dinners_per_week,
            breakfasts_per_week = excluded.breakfasts_per_week,
            lunches_per_week = excluded.lunches_per_week,
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
            merged_eating_style,
            merged_dinners_per_week,
            merged_breakfasts_per_week,
            merged_lunches_per_week,
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
        "eating_style": merged_eating_style,
        "dinners_per_week": merged_dinners_per_week,
        "breakfasts_per_week": merged_breakfasts_per_week,
        "lunches_per_week": merged_lunches_per_week,
        "onboarding_complete": bool(mark_complete),
    }


def save_onboarding_answers(
    member_names: list[str],
    household_restrictions: dict[str, list[str]],
    eating_style: str,
    wont_eat: list[str],
    excited_about: list[str],
    dinners_per_week: int,
    breakfasts_per_week: int = 7,
    lunches_per_week: int = 7,
) -> dict:
    """
    Save all onboarding-redesign questions in one call: household
    member names, per-person hard restrictions/allergies (household_restrictions
    keyed by member name — only members who actually have something get an
    entry), a freeform eating style, standing dislikes ("won't eat, no
    matter what"), cuisines/foods to lean into, and how many
    breakfasts/lunches/dinners a typical week should plan. This
    is the entire pre-first-plan question set per the onboarding redesign —
    everything else (favorite proteins, casual dislikes beyond this list,
    cuisine depth beyond this list, feedback) is deliberately NOT asked
    here; it accumulates through ordinary chat/UI use afterward.

    Each of the seven answers is logged as its own preference_events row
    (not skipped the way the old bulk onboarding save is) — unlike the
    original onboarding flow, these answers are meant to count toward the
    Memory view's growth counter, since they're real preference-write
    events same as any later correction, not something to hide "this
    month"'s number behind.
    """
    for name in member_names:
        name = name.strip()
        if not name:
            continue
        add_member(name)
    _log_preference_event("onboarding_household_size", "write")
    _log_preference_event("onboarding_member_names", "write")

    # set_member_dietary_restrictions already logs its own preference_event
    # per member (same path a mid-conversation correction uses), so no
    # separate explicit log call here — one event per member who actually
    # has something, not one flat event for the whole question.
    for name, restrictions in household_restrictions.items():
        if not name.strip():
            continue
        set_member_dietary_restrictions(name.strip(), restrictions, replace=True)

    set_household_meal_preferences(
        eating_style=eating_style,
        mark_complete=False,
    )
    _log_preference_event("onboarding_eating_style", "write")

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, dislikes_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET dislikes_json = excluded.dislikes_json, updated_at = datetime('now')
        """,
        (HOUSEHOLD_ID, json.dumps(wont_eat)),
    )
    conn.commit()
    conn.close()
    _log_preference_event("onboarding_wont_eat", "write")

    set_household_meal_preferences(
        cuisine_preferences=excited_about,
        dinners_per_week=dinners_per_week,
        breakfasts_per_week=breakfasts_per_week,
        lunches_per_week=lunches_per_week,
        mark_complete=True,
    )
    _log_preference_event("onboarding_excited_about", "write")
    _log_preference_event("onboarding_meals_per_week", "write")

    return get_household_memory()


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


def get_chores_due_today() -> list[dict]:
    """
    Chore instances due today — powers the app-shell Today screen's chores
    card (design_handoff_shell/README.md §4). Includes both pending and
    done instances (not just pending) so the UI can show an accurate
    "x of y done" count rather than only the still-open ones.

    Household-wide, not filtered to a signed-in member: there's no
    per-user login concept yet (see HOUSEHOLD_ID above), so this can't
    actually distinguish "my chores" from anyone else's the way the
    redesign's Today spec describes. Noted as a known gap in the README
    rather than silently faked.
    """
    conn = get_conn()
    today = date.today().isoformat()
    rows = conn.execute(
        """
        SELECT ci.id, c.name AS chore, ci.due_date, ci.status, m.name AS assignee
        FROM chore_instances ci
        JOIN chores c ON c.id = ci.chore_id
        LEFT JOIN members m ON m.id = ci.assignee_id
        WHERE ci.household_id = ? AND ci.due_date = ? AND ci.status != 'skipped'
        ORDER BY ci.id ASC
        """,
        (HOUSEHOLD_ID, today),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_chore_instance_status(instance_id: int, status: str = "done") -> dict:
    """
    Mark a chore instance done or back to pending directly from the Today
    screen's chores card (no chat round-trip needed) — same shape as
    check_off_meal/check_off_prep_step below.
    """
    conn = get_conn()
    completed_at_sql = "datetime('now')" if status == "done" else "NULL"
    conn.execute(
        f"UPDATE chore_instances SET status = ?, completed_at = {completed_at_sql} WHERE id = ? AND household_id = ?",
        (status, instance_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"instance_id": instance_id, "status": status}


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
    advance_prep_step_indices: list[int] | None = None,
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
    unset rather than guessing if you can't. advance_prep_step_indices is
    the 1-based position(s) within `instructions` of the specific step(s)
    that ARE the advance prep (e.g. [2] if step 2 is "make the marinade
    ahead of time") — only set this alongside advance_prep_notes, and only
    when a specific instruction step actually corresponds to it; leave
    empty otherwise. This lets the Cooker view clearly separate "do ahead"
    steps from "day of" steps instead of just listing them flat.
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO recipes (household_id, name, notes, ingredients_json, tags_json, food_groups_json, cuisine, main_protein, "
        "instructions_json, default_servings, prep_time_minutes, cook_time_minutes, advance_prep_notes, advance_prep_step_indices_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            HOUSEHOLD_ID, name, notes, json.dumps(ingredients), json.dumps(tags or []),
            json.dumps(food_groups or []), cuisine, main_protein,
            json.dumps(instructions or []), default_servings, prep_time_minutes, cook_time_minutes,
            advance_prep_notes, json.dumps(advance_prep_step_indices or []),
        ),
    )
    conn.commit()
    recipe_id = cur.lastrowid
    conn.close()
    return {
        "recipe_id": recipe_id, "name": name, "tags": tags or [], "food_groups": food_groups or [],
        "cuisine": cuisine, "main_protein": main_protein, "instructions": instructions or [],
        "default_servings": default_servings, "advance_prep_step_indices": advance_prep_step_indices or [],
    }


def update_recipe_details(
    recipe_name: str,
    instructions: list[str] | None = None,
    default_servings: int | None = None,
    prep_time_minutes: int | None = None,
    cook_time_minutes: int | None = None,
    advance_prep_notes: str | None = None,
    advance_prep_step_indices: list[int] | None = None,
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
    stop. advance_prep_step_indices is the 1-based position(s) within
    `instructions` of the step(s) that ARE the advance prep — set it
    alongside advance_prep_notes whenever a specific step corresponds to
    it, so the Cooker view can separate "do ahead" from "day of" instead of
    listing everything flat. Only pass the fields you're actually setting;
    anything left as None is untouched.
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
    if advance_prep_step_indices is not None:
        fields.append("advance_prep_step_indices_json = ?")
        params.append(json.dumps(advance_prep_step_indices))

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
               cook_time_minutes, advance_prep_notes, advance_prep_step_indices_json
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
            # 1-based positions within `instructions` that should be done
            # ahead of time (matches advance_prep_notes) — e.g. [2] means
            # instructions[1] (step 2) is the make-ahead step, everything
            # else happens day-of. Empty when nothing needs advance prep,
            # or for recipes saved before this was tracked.
            "advance_prep_step_indices": json.loads(r["advance_prep_step_indices_json"]),
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


def _add_recipe_ingredients_to_grocery_list(
    entry_id: int, recipe_ingredients: list[dict], weekly_plan_id: int | None
) -> tuple[list[str], list[str]]:
    """
    Put one planned meal's recipe ingredients onto the grocery list and
    record what it contributed, returning (added_items, already_have).

    Deliberately never called from anywhere the household hasn't said yes
    — see plan_meal (opt-in flag, default off) and approve_weekly_plan
    (the yes for a whole generated week). Shared by both so a meal's
    ingredients land on the list identically whether it was planned
    one-off in chat or arrived with an approved week.
    """
    # Skip adding anything already tracked in pantry/fridge inventory (with
    # a non-blank quantity) — this is the "accounts for logged inventory"
    # behavior for the plan-approval path. For a direct chat-driven add
    # ("add flour to the list"), the agent checks get_inventory itself and
    # asks first instead (see system prompt) since there's a person there
    # to actually ask.
    inv_conn = get_conn()
    have_names = {
        row["item"].strip().lower()
        for row in inv_conn.execute(
            "SELECT item FROM inventory_items WHERE household_id = ? AND TRIM(quantity) != ''",
            (HOUSEHOLD_ID,),
        ).fetchall()
    }
    inv_conn.close()

    added_items: list[str] = []
    already_have: list[str] = []
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
        add_result = add_grocery_item(
            ing["item"], quantity=ing.get("qty", ""), category=ing.get("category", "other"), added_by="ai",
            source_weekly_plan_id=weekly_plan_id,
        )
        added_items.append(ing["item"])
        # Record exactly what THIS entry contributed to that grocery
        # line, before it got merged with anything else already there —
        # see _reverse_meal_grocery_contributions, which is what lets
        # swap_meal_in_plan/swap_component_in_plan take this back out
        # precisely if the meal is later swapped for something else.
        link_conn = get_conn()
        link_conn.execute(
            "INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
            "VALUES (?, ?, ?, ?, ?)",
            (HOUSEHOLD_ID, entry_id, add_result["item_id"], ing["item"], _strip_prep_descriptor(ing.get("qty", "") or "")),
        )
        link_conn.commit()
        link_conn.close()
    return added_items, already_have


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
        added_items, already_have = _add_recipe_ingredients_to_grocery_list(
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


# ---------- Week intake (design_handoff_plan_the_week/DATA_MODEL.md) ----------
# The answers to the two question screens, as a first-class object rather
# than as chat history. Read DATA_MODEL.md before changing anything here:
# the append-only revisions, the two snapshots, and the per-slot provenance
# all exist to enable things that cannot be retrofitted afterwards.

# Closed set. Each tag has exactly one planning consequence — if two tags
# would produce the same behaviour, one of them shouldn't exist.
NIGHT_TAGS = {
    # Ordinary cooked dinner. Mutually exclusive with the others. Exists so
    # someone working down the list can AFFIRM a night rather than skip it:
    # an affirmed night is data, a skipped night is a guess.
    "normal": "plan a normal dinner you cook that evening.",
    # Dinner slot planned empty, and nothing for it reaches the shopping
    # list. The only deliberately empty slot in a week.
    "out": "plan nothing and buy nothing for this night.",
    # Not a vague "busy" flag: a hard cap on cook time AND a trigger for
    # cook-once-eat-twice.
    "rush": "keep it under 20 minutes, or make the night before stretch to cover it.",
    # Opens the guest follow-up. Scales recipe AND shopping quantities.
    "guests": "scale the recipe and the shopping to the bigger table.",
    # No new dinner; the previous night's batch is increased instead.
    "left": "cook enough the night before instead of planning something new.",
}

# The hard cap a `rush` night imposes, in minutes. Named rather than inlined
# because the acknowledgement copy, the generator prompt and the draft
# screen's per-slot reasons all have to agree on the same number.
RUSH_MAX_MINUTES = 20


def _week_dates(week_start: str) -> list[str]:
    start = date.fromisoformat(week_start)
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


def _household_composition() -> dict:
    """
    How many adults and children are on record, for the guest maths and the
    preferences snapshot. Counted from members.age_group, which is freeform
    — anything that isn't recognisably an adult or a child is left out of
    both counts rather than guessed into one.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT age_group FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    adults = children = 0
    for r in rows:
        group = (r["age_group"] or "").strip().lower()
        if group == "adult":
            adults += 1
        elif group in ("child", "teen", "toddler", "kid"):
            children += 1
    return {"adults": adults, "children": children}


def _build_preferences_snapshot(conn) -> dict:
    """
    A COPY of the preference set a plan was generated under, taken at
    generation time. Not a reference: if a plan only points at live
    preferences then the day someone edits "won't eat", every past plan's
    reasoning becomes unreadable and unreproducible — you can no longer tell
    whether a strange choice was a bug or a preference that has since
    changed. A few hundred bytes buys that.
    """
    prefs = conn.execute(
        "SELECT * FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    if not prefs:
        return {}
    return {
        "meal_counts": {
            "breakfasts": prefs["breakfasts_per_week"],
            "lunches": prefs["lunches_per_week"],
            "dinners": prefs["dinners_per_week"],
        },
        "wont_eat": json.loads(prefs["dislikes_json"]),
        "protein": json.loads(prefs["protein_preferences_json"]),
        "weeknight_max_minutes": prefs["weeknight_max_minutes"],
        "cooking_time_preference": prefs["cooking_time_preference"],
        "repeats": prefs["repeats_tolerance"],
        "kit": json.loads(prefs["kitchen_kit_json"]),
        "cuisines": json.loads(prefs["cuisine_preferences_json"]),
        "table_style": prefs["table_style"],
        "eating_style": prefs["eating_style"],
        "novelty": prefs["novelty_preference"],
        "typical_week": prefs["typical_week"],
    }


def _intake_row_to_dict(row) -> dict:
    return {
        "intake_id": row["id"],
        "week_start": row["week_start"],
        "revision": row["revision"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "night_tags": json.loads(row["night_tags_json"]),
        "guest_counts": json.loads(row["guest_counts_json"]),
        "packed_lunch_days": json.loads(row["packed_lunch_days_json"]),
        "moods": json.loads(row["moods_json"]),
        "cuisines": json.loads(row["cuisines_json"]),
        "freeform": row["freeform"],
        "household_snapshot": json.loads(row["household_snapshot_json"]),
        "preferences_snapshot": json.loads(row["preferences_snapshot_json"]),
    }


def _current_intake_row(conn, week_start: str):
    """
    The current intake for a week: the highest revision that hasn't been
    superseded. Never "the most recent row" — a superseded revision can
    have a later created_at than nothing at all, and the whole point of
    append-only is that old rows stay.
    """
    return conn.execute(
        "SELECT * FROM week_intake WHERE household_id = ? AND week_start = ? AND superseded_at IS NULL "
        "ORDER BY revision DESC LIMIT 1",
        (HOUSEHOLD_ID, week_start),
    ).fetchone()


def get_week_intake(week_start: str) -> dict | None:
    """
    The answers currently in force for one week, or None if nobody has
    started. Use this rather than reading week_intake directly — it applies
    the "highest revision, not superseded" rule that append-only depends on.
    """
    conn = get_conn()
    row = _current_intake_row(conn, week_start)
    conn.close()
    return _intake_row_to_dict(row) if row else None


def get_week_intake_history(week_start: str) -> list[dict]:
    """
    Every revision of a week's answers, oldest first — what makes "the week
    you had before you redid it" recoverable. Nothing in the UI reads this
    yet; it exists because the data is only collectable as it happens.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM week_intake WHERE household_id = ? AND week_start = ? ORDER BY revision ASC",
        (HOUSEHOLD_ID, week_start),
    ).fetchall()
    conn.close()
    return [_intake_row_to_dict(r) for r in rows]


def save_week_intake(
    week_start: str,
    night_tags: dict | None = None,
    guest_counts: dict | None = None,
    packed_lunch_days: list | None = None,
    moods: list | None = None,
    cuisines: list | None = None,
    freeform: str | None = None,
    created_by: str = "",
) -> dict:
    """
    Record the household's answers for a week, as a NEW REVISION.

    Append-only, always: a week_intake row is never updated in place. This
    call copies the current revision, applies whichever answers were passed,
    and inserts the result as revision+1, stamping superseded_at on the one
    it replaced. Arguments left as None are inherited unchanged — that's how
    Q1 and Q2 each save their own half without either clobbering the other,
    and how a chat instruction can change one answer without restating the
    rest.

    Every caller that changes an ANSWER must come through here. The rule of
    thumb is: would this have changed if they'd said it during the
    questions? If yes, it's a new revision. If chat edited only the plan and
    not the intake, "Try again" would regenerate from stale answers and
    silently revert whatever the household just said.

    Both snapshots are retaken on every revision, so each one records what
    was true when that answer was given.
    """
    date.fromisoformat(week_start)  # fail loudly on a malformed week
    for day, tags in (night_tags or {}).items():
        date.fromisoformat(day)  # keyed by ISO date, never by weekday
        unknown = [t for t in tags if t not in NIGHT_TAGS]
        if unknown:
            raise ValueError(f"Unknown night tag(s): {', '.join(unknown)}.")
        # "Regular night" is mutually exclusive — affirming a night and
        # constraining it are different answers, and holding both would
        # leave the generator with no way to tell which the household meant.
        if "normal" in tags and len(tags) > 1:
            raise ValueError("'normal' is exclusive — a regular night can't also carry another tag.")

    conn = get_conn()
    current = _current_intake_row(conn, week_start)
    base = _intake_row_to_dict(current) if current else {
        "night_tags": {}, "guest_counts": {}, "packed_lunch_days": [],
        "moods": [], "cuisines": [], "freeform": "",
    }

    def pick(new, key):
        return base[key] if new is None else new

    household_snapshot = _household_composition()
    preferences_snapshot = _build_preferences_snapshot(conn)
    revision = (current["revision"] + 1) if current else 1
    if current:
        conn.execute(
            "UPDATE week_intake SET superseded_at = datetime('now') WHERE id = ?", (current["id"],)
        )
    cursor = conn.execute(
        """
        INSERT INTO week_intake (
            household_id, week_start, revision, created_by,
            night_tags_json, guest_counts_json, packed_lunch_days_json,
            moods_json, cuisines_json, freeform,
            household_snapshot_json, preferences_snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            HOUSEHOLD_ID, week_start, revision,
            (created_by or (current["created_by"] if current else "")).strip(),
            json.dumps(pick(night_tags, "night_tags")),
            json.dumps(pick(guest_counts, "guest_counts")),
            json.dumps(pick(packed_lunch_days, "packed_lunch_days")),
            json.dumps(pick(moods, "moods")),
            json.dumps(pick(cuisines, "cuisines")),
            pick(freeform, "freeform"),
            json.dumps(household_snapshot),
            json.dumps(preferences_snapshot),
        ),
    )
    conn.commit()
    intake_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM week_intake WHERE id = ?", (intake_id,)).fetchone()
    conn.close()
    return _intake_row_to_dict(row)


def _observed_day_patterns(week_start: str) -> dict:
    """
    What the app already knows about each weekday, so the household isn't
    re-answering things it has already been told. The grey hint line under
    each day row.

    Two sources, both real rather than invented:
      - A recurring rhythm fact from What We Know that names this weekday
        ("Tuesdays are tee-ball so we eat at 5").
      - An observed pattern in the plan history: the same weekday resolved
        to takeout or leftovers in most of the last four weeks.
    A day with neither gets no hint, and the row still works — it just
    reads "Nothing on the calendar."
    """
    dates = _week_dates(week_start)
    weekday_names = [date.fromisoformat(d).strftime("%A") for d in dates]

    conn = get_conn()
    facts = conn.execute(
        "SELECT text FROM facts WHERE household_id = ? AND category = 'rhythm'", (HOUSEHOLD_ID,)
    ).fetchall()
    lookback_start = (date.fromisoformat(week_start) - timedelta(days=28)).isoformat()
    history = conn.execute(
        """
        SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.slot = 'dinner'
          AND mpe.date >= ? AND mpe.date < ?
        """,
        (HOUSEHOLD_ID, lookback_start, week_start),
    ).fetchall()
    conn.close()

    takeout_by_weekday: dict[str, int] = {}
    for row in history:
        text = (row["meal"] or "").lower()
        if re.search(r"take[\s-]?out|delivery|order in", text):
            name = date.fromisoformat(row["date"]).strftime("%A")
            takeout_by_weekday[name] = takeout_by_weekday.get(name, 0) + 1

    hints = {}
    for iso, weekday in zip(dates, weekday_names):
        # A rhythm fact naming this weekday wins — it's something the
        # household said outright, not something inferred from behaviour.
        stated = next(
            (f["text"] for f in facts if weekday.lower() in (f["text"] or "").lower()), None
        )
        if stated:
            hints[iso] = stated
            continue
        count = takeout_by_weekday.get(weekday, 0)
        if count >= 3:
            hints[iso] = "Takeout four weeks running"
        elif count == 2:
            hints[iso] = "Takeout two of the last four weeks"
    return hints


# The cuisines offered when a household hasn't saved any of its own. Only a
# fallback — Q2 reads the household's real list from What We Know first, so
# it visibly reflects its own stored profile and never re-asks a question it
# has already been answered. Taps on this fallback are written back.
ONBOARDING_CUISINES = [
    "Italian", "Mexican", "Thai", "Indian", "Japanese",
    "Greek", "Chinese", "Middle Eastern", "American", "French",
]


def get_week_intake_prefill(week_start: str) -> dict:
    """
    Everything the two question screens need to open already knowing what
    the app knows: per-day hints, the household's own saved cuisines, its
    composition for the guest maths, and any intake already in flight.

    `in_flight` is the soft lock from DATA_MODEL.md → One intake in flight.
    Both adults are nudged on Sunday, so both can start; the second to open
    Q1 joins the first one's answers rather than starting a blank set. Two
    intakes racing to generate the same week is the one concurrency case
    that will actually happen, most likely on a Sunday evening.

    `household_known` is false when nobody's age group is on record. The
    guest panel switches to asking for the WHOLE TABLE in that case rather
    than for extras added to a base of zero, which would silently produce a
    wrong number and an acknowledgement that confidently states it.
    """
    date.fromisoformat(week_start)
    household = _household_composition()
    conn = get_conn()
    prefs = conn.execute(
        "SELECT cuisine_preferences_json FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    plan = conn.execute(
        "SELECT id, status, intake_id FROM weekly_plans WHERE household_id = ? AND week_start_date = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (HOUSEHOLD_ID, week_start),
    ).fetchone()
    intake_row = _current_intake_row(conn, week_start)
    conn.close()

    saved_cuisines = json.loads(prefs["cuisine_preferences_json"]) if prefs else []
    intake = _intake_row_to_dict(intake_row) if intake_row else None
    # "In flight" means somebody has answered something for this week that
    # hasn't been turned into a plan yet. Once a plan has been generated
    # from this revision, carrying on from it is a redo, not a join.
    in_flight = bool(intake and (not plan or plan["intake_id"] != intake["intake_id"]))

    return {
        "week_start": week_start,
        "week_label": _format_week_range(week_start),
        "days": [
            {
                "date": d,
                "weekday": date.fromisoformat(d).strftime("%A"),
                "short": date.fromisoformat(d).strftime("%a"),
                "day_of_month": date.fromisoformat(d).day,
                "hint": hint,
            }
            for d, hint in (
                (d, _observed_day_patterns(week_start).get(d, "")) for d in _week_dates(week_start)
            )
        ],
        "household": household,
        # False when nobody's age group is recorded — see the docstring.
        "household_known": bool(household["adults"] or household["children"]),
        "cuisines": saved_cuisines or ONBOARDING_CUISINES,
        "cuisines_are_fallback": not saved_cuisines,
        "intake": intake,
        "in_flight": in_flight,
        "plan_exists": bool(plan),
        "plan_status": plan["status"] if plan else None,
    }


WEEK_SLOTS = ("breakfast", "lunch", "dinner")


def plan_slot_empty(
    weekly_plan_id: int,
    meal_date: str,
    slot: str,
    reason: str,
    derived_from: dict | None = None,
) -> dict:
    """
    Record a slot as deliberately empty — `planned_empty`.

    Not a gap and not a question. This is a slot that needs no decision and
    must NEVER be offered to the household as one. Two things produce it:
    a dinner on a night nobody is home ("You're out — I've planned nothing
    and bought nothing"), and a meal category the household has asked for
    zero of.

    `reason` is what the draft screen shows in place of a meal, so it has
    to read as a statement, never as an apology or an ask.
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, reasoning, derived_from_json) "
        "VALUES (?, ?, ?, ?, 'planned_empty', ?, ?)",
        (HOUSEHOLD_ID, meal_date, slot, weekly_plan_id, reason, json.dumps(derived_from or {})),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return {"entry_id": entry_id, "date": meal_date, "slot": slot, "slot_state": "planned_empty", "reason": reason}


def plan_slot_open(
    weekly_plan_id: int,
    meal_date: str,
    slot: str,
    open_reason: str,
    options: list[dict] | None = None,
    derived_from: dict | None = None,
) -> dict:
    """
    Record a slot as `open` — a decision the app is genuinely handing back.

    `open_reason` is a full sentence naming the CONSTRAINT that caused it,
    not an apology: "Wednesday I'd rather ask than guess: after Monday's
    chili, everything I have under 20 minutes repeats something you've just
    eaten." Naming the constraint is what makes the ask read as diligence
    rather than failure.

    An open slot is still a slot. What it must never be is absent — a
    silently missing slot is the bug this whole state exists to make
    impossible.
    """
    if not (open_reason or "").strip():
        raise ValueError("An open slot needs a reason naming the constraint that caused it.")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, weekly_plan_id, slot_state, open_reason, derived_from_json) "
        "VALUES (?, ?, ?, ?, 'open', ?, ?)",
        (
            HOUSEHOLD_ID, meal_date, slot, weekly_plan_id, open_reason,
            json.dumps({**(derived_from or {}), "options": options or []}),
        ),
    )
    conn.commit()
    entry_id = cur.lastrowid
    conn.close()
    return {
        "entry_id": entry_id, "date": meal_date, "slot": slot,
        "slot_state": "open", "open_reason": open_reason, "options": options or [],
    }


def get_meal_planning_preferences() -> dict:
    """
    Everything the revisitable setup screen shows: the per-category meal
    counts, and every preference the household has told the app so far,
    each in a shape the screen can edit inline.

    The point of this screen is that nothing is locked in from when they
    signed up. So this deliberately returns the FULL set rather than only
    what onboarding happened to ask — a preference the app is acting on but
    won't show is one the household can't correct.
    """
    conn = get_conn()
    prefs = conn.execute(
        "SELECT * FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    conn.close()

    def field(name, default):
        return prefs[name] if prefs else default

    return {
        "meal_counts": {
            "breakfasts_per_week": field("breakfasts_per_week", 7),
            "lunches_per_week": field("lunches_per_week", 7),
            "dinners_per_week": field("dinners_per_week", 7),
        },
        "dislikes": json.loads(field("dislikes_json", "[]")),
        "protein_preferences": json.loads(field("protein_preferences_json", "{}")),
        "cuisine_preferences": json.loads(field("cuisine_preferences_json", "[]")),
        "kitchen_kit": json.loads(field("kitchen_kit_json", "[]")),
        "repeats_tolerance": field("repeats_tolerance", ""),
        "weeknight_max_minutes": field("weeknight_max_minutes", 0),
        "cooking_time_preference": field("cooking_time_preference", ""),
        "table_style": field("table_style", ""),
        "eating_style": field("eating_style", ""),
        "novelty_preference": field("novelty_preference", "balanced"),
        "typical_week": field("typical_week", ""),
        "notes": field("notes", ""),
    }


def get_week_planning_nudge() -> dict:
    """
    Whether to offer to plan a week, and which one — the Sunday nudge on
    Today (design_handoff_plan_the_week, DECISIONS.md #6).

    Two cases, in priority order:

    1. The week the household is CURRENTLY LIVING IN has no plan at all.
       That's the more pressing one, and it's offered any day of the week —
       waiting until Sunday to mention that this week was never planned
       would be absurd.
    2. Otherwise, from Saturday onward, the week that starts next Monday.
       The design asks for Sunday morning; Saturday is included because
       this is in-app only, not real push (there is no scheduler and no
       push infrastructure — see schema.sql on notification_dismissals), so
       the nudge is only ever seen when the app is opened. Starting a day
       early means a household that doesn't open it on Sunday still gets
       the offer before the week begins, rather than on the Monday it was
       meant to prepare for.

    Suppressed once dismissed, and the dismissal key is the week itself —
    so "I won't ask again this week" is literally true, and next week's
    offer isn't silenced by this week's dismissal. Also suppressed once
    that week has a plan: there is nothing left to offer.
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    next_monday = this_monday + timedelta(days=7)

    conn = get_conn()
    dismissed = _dismissed_keys(conn)
    planned = {
        row["week_start_date"]
        for row in conn.execute(
            "SELECT DISTINCT week_start_date FROM weekly_plans WHERE household_id = ?", (HOUSEHOLD_ID,)
        ).fetchall()
    }
    conn.close()

    target = None
    if this_monday.isoformat() not in planned:
        target = this_monday
    elif today.weekday() >= 5 and next_monday.isoformat() not in planned:
        target = next_monday

    if target is None:
        return {"show": False, "week_start": None}
    week_start = target.isoformat()
    if f"plan_week_nudge:{week_start}" in dismissed:
        return {"show": False, "week_start": week_start, "dismissed": True}
    return {
        "show": True,
        "week_start": week_start,
        "week_label": _format_week_range(week_start),
        "is_current_week": target == this_monday,
        "dismiss_key": f"plan_week_nudge:{week_start}",
    }


def _week_headline(plan: dict, days: list[dict], intake: dict | None) -> str:
    """
    The one line above the draft. One line, no recap — the per-slot reasons
    carry the detail, and the assistant never lists what it did.

    It says at most two things, in priority order:

    1. That there's a decision waiting. An open slot is the only thing on
       this screen the household has to act on, so it outranks everything.
    2. DECISIONS.md #1 — when the week's tags leave fewer dinners than the
       household's usual count, the tags win, and the app says so ONCE
       rather than shorting them silently. Not a question: asking would
       turn a tagging screen into a negotiation whose answer is nearly
       always "yes, obviously".

    With neither, it just says the week is here. There is deliberately no
    third clause: a headline that grows a sentence per feature is the recap
    this rule exists to prevent.
    """
    open_days = [
        date.fromisoformat(d["date"]).strftime("%A")
        for d in days
        for slot in WEEK_SLOTS
        if (d.get(slot) or {}).get("state") == "open"
    ]
    if len(open_days) == 1:
        return f"Your week’s here — there’s one night I’d like your call on."
    if open_days:
        return f"Your week’s here — there are {len(open_days)} slots I’d like your call on."

    # The baseline is the seven nights of the week, NOT
    # preferences_snapshot's dinner count: since that count means how many
    # DISTINCT dinners to plan rather than how many nights to plan one,
    # comparing it against nights cooked would be comparing two different
    # things and would fire on weeks with nothing wrong with them.
    #
    # And this only speaks when the week's TAGS caused the reduction, which
    # is the case DECISIONS.md #1 is actually about. A household that set
    # its own counts to zero already knows; being told about it is not news.
    night_tags = (intake or {}).get("night_tags") or {}
    because = []
    for day, tags in sorted(night_tags.items()):
        weekday = date.fromisoformat(day).strftime("%A")
        if "out" in tags:
            because.append(f"you’re out {weekday}")
        elif "left" in tags:
            because.append(f"it’s leftovers {weekday}")
    cooked = sum(1 for d in days if (d.get("dinner") or {}).get("state") == "planned")
    if because and cooked and cooked < len(days):
        return (
            f"That’s {cooked} dinners you’ll cook this week rather than {len(days)}"
            f" — {' and '.join(because[:2])}."
        )
    return "Your week’s here."


def resolve_open_slot(weekly_plan_id: int, meal_date: str, slot: str, choice: str) -> dict:
    """
    Settle a slot the app handed back. `choice` is what the household
    picked — one of the offered options, or anything they typed instead.

    The open row is replaced by a real planned meal, so the slot moves from
    'open' to 'planned' rather than accumulating two rows for one slot. Its
    provenance records that a person settled it, which is worth keeping:
    "the app asked and they answered" is a different thing from "the app
    chose", and only one of them is evidence about the household's taste.

    A choice that declines to plan anything at all ("Takeout, don't plan
    it") is honoured as exactly that — a planned takeout entry, not a
    silent gap and not a slot left open forever.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, slot_state, open_reason FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ? LIMIT 1",
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No {slot} slot on {meal_date} in that plan.")
    if not (choice or "").strip():
        raise ValueError("A choice is needed to settle this slot.")

    was_open = row["slot_state"] == "open"
    # Reverse anything the outgoing entry contributed before deleting it —
    # the same care swap_meal_in_plan takes. An open slot has contributed
    # nothing, but this also serves the "change my mind about an already
    # planned slot" path.
    _reverse_meal_grocery_contributions(row["id"])
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()

    result = plan_meal(
        meal_date, choice.strip(), slot=slot, weekly_plan_id=weekly_plan_id,
        # Mirrors the plan's approved state, exactly as a swap does: settling
        # a slot in a draft leaves the shopping list alone, settling one in
        # an already-approved week keeps the list in step. Otherwise the
        # list would quietly lack the one meal the household chose by hand.
        add_ingredients_to_grocery_list=_weekly_plan_is_approved(weekly_plan_id),
        reasoning="you chose this one",
        derived_from={"constraint": "settled_by_household", "answered": row["open_reason"] or ""},
    )
    return {**result, "was_open": was_open}


def reopen_weekly_plan(weekly_plan_id: int) -> dict:
    """
    Reopen an approved week so it can be edited again — DECISIONS.md #2.

    It never removes anything from the shopping list. Groceries already
    added stay, and re-approving only adds what's new. That's deliberate:
    taking items off a list somebody may already have bought is worse than
    a slightly long list, and a true reversal would need "was this item
    actually bought?" tracking that doesn't exist.

    The approval receipt is cleared, because it no longer describes a
    settled week — but the grocery links are untouched, which is what makes
    the re-approval add only the difference.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    conn.execute(
        "UPDATE weekly_plans SET status = 'draft', approved_by = '', approved_at = NULL, "
        "approved_grocery_added = 0, approved_grocery_skipped = 0, updated_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {
        "weekly_plan_id": weekly_plan_id,
        "status": "draft",
        "was_approved": plan["status"] == "approved",
    }


def attach_intake_to_plan(weekly_plan_id: int, intake_id: int) -> dict:
    """
    Record which revision of the household's answers produced this plan.
    Set once, at generation. It's what makes "the week you had before you
    redid it" recoverable and "why did it plan that?" answerable.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET intake_id = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (intake_id, weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "intake_id": intake_id}


def audit_plan_slots(weekly_plan_id: int) -> dict:
    """
    Check a generated week against the one rule it can't be allowed to
    break: every one of the 21 slots exists, and each is planned,
    planned_empty, or open — never absent, and never a row carrying no
    meal, no emptiness and no question.

    "Week generation silently leaves random meal slots empty" is a real
    reported bug, and its shape is exactly this: nothing anywhere asserted
    that a slot had to be there. Returns the offenders rather than raising,
    so the generator can fill them rather than fail the whole week over one.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT week_start_date FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    rows = conn.execute(
        "SELECT date, slot, slot_state, recipe_id, freeform_meal, open_reason "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchall()
    conn.close()

    present = {(r["date"], r["slot"]) for r in rows}
    missing = [
        {"date": d, "slot": s}
        for d in _week_dates(plan["week_start_date"])
        for s in WEEK_SLOTS
        if (d, s) not in present
    ]
    # A row that claims to be planned but holds no meal, or claims to be
    # open but names no reason. Stored form of the same bug.
    hollow = [
        {"date": r["date"], "slot": r["slot"], "slot_state": r["slot_state"]}
        for r in rows
        if (r["slot_state"] == "planned" and not (r["recipe_id"] or r["freeform_meal"]))
        or (r["slot_state"] == "open" and not (r["open_reason"] or "").strip())
    ]
    return {
        "weekly_plan_id": weekly_plan_id,
        "expected": len(_week_dates(plan["week_start_date"])) * len(WEEK_SLOTS),
        "present": len(present),
        "missing": missing,
        "hollow": hollow,
        "complete": not missing and not hollow,
    }


def get_plan_id_for_week(week_start_date: str) -> int | None:
    """
    The weekly_plans row id for one specific week's Monday, or None if that
    week has no plan yet. The week-scoped endpoints
    (design_handoff_plan_the_week/DATA_AND_API.md) are keyed by date, not
    plan id, so they need this to get back to a row.

    Deliberately NOT _current_weekly_plan_row: that answers "which plan is
    the household's current one," a different and week-agnostic question.
    Asking to approve Sep 1–7 must approve Sep 1–7 even if the current plan
    is a different week. Picks the most recently created row if a week
    somehow has more than one, which shouldn't happen but shouldn't 500
    either.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM weekly_plans WHERE household_id = ? AND week_start_date = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (HOUSEHOLD_ID, week_start_date),
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def _format_week_range(week_start_date: str) -> str:
    """
    A week as the design writes it: "Sep 1–7", or "Aug 30–Sep 5" when the
    seven days straddle a month. En dash, no padded day numbers, matching
    design_handoff_plan_the_week/COPY.md's own eyebrow strings. Used
    wherever a week has to be named in a sentence rather than shown as a
    grid — the Sunday nudge, the approval notification, the draft eyebrow.
    """
    start = date.fromisoformat(week_start_date)
    end = start + timedelta(days=6)
    start_month = start.strftime("%b")
    if start.month == end.month:
        return f"{start_month} {start.day}–{end.day}"
    return f"{start_month} {start.day}–{end.strftime('%b')} {end.day}"


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


def _current_weekly_plan_row(conn):
    """
    Resolve "the household's current plan" the way every plan-scoped tool
    means it when weekly_plan_id is omitted: the plan whose week actually
    contains today, so a chat answer about "this week's plan" always
    matches the same days the Meals tab is showing. Falls back to the
    most-recently-created plan when none covers today — a household with
    no plan at all still correctly resolves to None either way.

    This used to just be "most recently created plan" everywhere, which
    silently drifted away from "this week" the moment any other plan
    existed (a leftover from last week that was never cleared, or one
    generated ahead of time for next week) — the assistant would describe
    that other plan's meals while the Meals tab, which only ever shows the
    real current calendar week, correctly showed nothing. That's the exact
    "the chat knows about a meal plan the app doesn't show" report this
    fixes at the source, instead of just in one call site.
    """
    today = date.today().isoformat()
    plan = conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? "
        "AND date(week_start_date) <= date(?) AND date(week_start_date, '+6 days') >= date(?) "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (HOUSEHOLD_ID, today, today),
    ).fetchone()
    if plan:
        return plan
    return conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (HOUSEHOLD_ID,),
    ).fetchone()


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
        row = _current_weekly_plan_row(conn)
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


_COMPONENT_CATEGORY_ORDER = ["breakfast", "protein", "vegetable", "carb", "treat", "dip", "snack"]


def _build_day_based_menu(meal_dicts: list[dict]) -> list[dict]:
    """
    Group a day-based plan's flat meal entries into a day-by-day menu — one
    row per date with each planned slot filled in (breakfast/lunch/dinner/
    snack), for a real weekly-menu view (see get_weekly_plan's `menu`)
    instead of one flat card per meal. This is real, already-planned data,
    not a suggestion. Each slot's "why this?" rationale (see
    meal_plan_entries.reasoning) rides along as `{slot}_reasoning`, e.g.
    day["dinner_reasoning"], so a "why this?" affordance can show it
    without a second lookup.
    """
    by_date: dict[str, dict] = {}
    slots = ["breakfast", "lunch", "dinner", "snack"]
    for m in meal_dicts:
        if not m["date"]:
            continue
        day = by_date.setdefault(
            m["date"],
            {"date": m["date"], **{s: None for s in slots}, **{f"{s}_reasoning": None for s in slots}},
        )
        slot = m["slot"] if m["slot"] in slots else "dinner"
        day[slot] = m["meal"]
        day[f"{slot}_reasoning"] = m.get("reasoning")
    return [by_date[d] for d in sorted(by_date)]


def _build_suggested_schedule(components: list[dict], week_start_date: str, days: int = 7) -> list[dict]:
    """
    Deterministically spread a component_based item pool across a 7-day
    menu, purely for display (see get_weekly_plan's `menu`/
    `menu_is_suggested`) — the whole point of component_based planning is
    that the household assembles freely, so this is never saved or tracked
    as "planned," just one reasonable example arrangement. The pool
    (roughly a handful of proteins/vegetables/carbs, one breakfast idea, a
    treat, a dip, a snack or two) is intentionally smaller than 7 days x 4
    meals, so items repeat across days by design — rotated with an offset
    per slot so the same day doesn't always pair the same vegetable with
    both lunch and dinner. Lunch and dinner are both built as full plates —
    protein + vegetable + carb — never just a side pairing, so every
    suggested meal reads as a real plate rather than a partial one.
    """
    by_cat = {c["category"]: c["items"] for c in components if c.get("items")}
    breakfast = by_cat.get("breakfast", [])
    protein = by_cat.get("protein", [])
    vegetable = by_cat.get("vegetable", [])
    carb = by_cat.get("carb", [])
    treat = by_cat.get("treat", [])
    dip = by_cat.get("dip", [])
    snack = by_cat.get("snack", [])

    def pick(items, i):
        return items[i % len(items)] if items else None

    def plate(*parts):
        parts = [p for p in parts if p]
        return " with ".join(parts) if parts else None

    def snack_pick(i):
        if snack:
            return pick(snack, i)
        # No dedicated snack items saved — fall back to alternating the
        # treat/dip pool rather than leaving the slot empty, since either
        # reasonably doubles as a snack.
        fallback = (treat if i % 2 == 0 else dip) or treat or dip
        return pick(fallback, i)

    start = date.fromisoformat(week_start_date)
    schedule = []
    for i in range(days):
        schedule.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "breakfast": pick(breakfast, i),
            "lunch": plate(pick(protein, i), pick(vegetable, i), pick(carb, i)),
            "dinner": plate(pick(protein, i + 1), pick(vegetable, i + 1), pick(carb, i + 1)),
            "snack": snack_pick(i),
        })
    return schedule


def _compute_freshness(meal_dicts: list[dict], plan_created_at: str) -> dict:
    """
    Count how many of this week's planned meals are recipes newly
    introduced by this same plan versus recipes that already existed
    beforehand — the "2 new recipes this week" freshness signal. A recipe
    counts as "new" if its created_at is at/after this plan's own
    created_at (it didn't exist before this plan brought it in).
    Deliberately NOT based on recipes.times_cooked: that counter
    increments at *planning* time (see plan_meal), not at actual-cooking
    time, so it already reads >= 1 for every meal in the very plan being
    inspected — using it here would make everything look like a repeat.
    Freeform/untracked entries (no saved recipe) aren't counted either way.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT name, created_at FROM recipes WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    created_by_name = {r["name"].lower(): r["created_at"] for r in rows}

    new_count = 0
    repeat_count = 0
    for m in meal_dicts:
        created_at = created_by_name.get((m["meal"] or "").lower())
        if created_at is None:
            continue
        if created_at >= plan_created_at:
            new_count += 1
        else:
            repeat_count += 1
    return {"new_recipe_count": new_count, "repeat_recipe_count": repeat_count}


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
    placeholder date). Also includes `is_first_plan` (true only for a
    household's very first generated plan — mention this warmly, e.g.
    "here's your first week, built around what you told me") and
    `new_recipe_count`/`repeat_recipe_count` (the freshness signal — how
    many planned meals are recipes never cooked before vs. ones the
    household's made before).
    """
    conn = get_conn()
    if weekly_plan_id is None:
        # Resolves to the plan whose week actually contains today when one
        # exists, falling back to the most-recently-created plan otherwise
        # — see _current_weekly_plan_row. id DESC as a tiebreaker still
        # matters within that: two plans created within the same second
        # (created_at has only second-level resolution) would otherwise
        # resolve non-deterministically, which broke clear_stale_grocery_items
        # identifying the actual newest plan.
        plan = _current_weekly_plan_row(conn)
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
               mpe.food_groups_json, mpe.component_category, mpe.cooked_status, mpe.reasoning
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
            "reasoning": m["reasoning"] or None,
        }
        for m in meals
    ]

    result = {
        "weekly_plan_id": plan["id"],
        "week_start_date": plan["week_start_date"],
        "status": plan["status"],
        # Who said yes to this week and when — the approved receipt's own
        # two fields. Blank/None while the plan is still a draft.
        "approved_by": plan["approved_by"],
        "approved_at": plan["approved_at"],
        "approved_grocery_added": plan["approved_grocery_added"],
        "approved_grocery_skipped": plan["approved_grocery_skipped"],
        # Which revision of the household's answers produced this week.
        "intake_id": plan["intake_id"],
        "constraints_notes": plan["constraints_notes"],
        "planning_mode": plan["planning_mode"],
        "is_first_plan": bool(plan["is_first_plan"]),
        "meals": meal_dicts,
    }
    result.update(_compute_freshness(meal_dicts, plan["created_at"]))

    if plan["planning_mode"] == "component_based":
        by_category: dict[str, list[str]] = {}
        for m in meal_dicts:
            cat = m["component_category"] or "other"
            by_category.setdefault(cat, []).append(m["meal"])
        ordered_cats = [c for c in _COMPONENT_CATEGORY_ORDER if c in by_category]
        ordered_cats += [c for c in by_category if c not in ordered_cats]
        result["components"] = [{"category": c, "items": by_category[c]} for c in ordered_cats]
        # `menu`: a real day-by-day weekly menu for display (see the Share
        # view) even though component_based plans have no fixed day
        # mapping underneath — menu_is_suggested tells the caller this is
        # one example arrangement, not something actually planned/tracked.
        result["suggested_schedule"] = _build_suggested_schedule(result["components"], plan["week_start_date"])
        result["menu"] = result["suggested_schedule"]
        result["menu_is_suggested"] = True
    else:
        result["menu"] = _build_day_based_menu(meal_dicts)
        result["menu_is_suggested"] = False

    return result


def get_week_menu(weekly_plan_id: int | None = None) -> dict:
    """
    The always-7-day weekly menu for the Week tab (design_handoff_shell/
    README.md §5) — the "one backend ask" for that redesign. Unlike
    get_weekly_plan's `menu` (which only lists dates that already have at
    least one entry), this always returns exactly 7 days starting at the
    plan's week_start_date, one dict per day with `breakfast`/`lunch`/
    `dinner` keys — each either None (nothing planned, drives the "Pick"
    row) or `{title, meta, source}`.

    `source`/`meta` have no backing column in meal_plan_entries, so they're
    derived with a keyword heuristic against the entry's freeform text —
    documented here as a judgment call, not a spec'd mapping:
      - "leftover"/"leftovers" in the text -> source "leftovers", meta "reheat"
      - "takeout"/"take-out"/"take out"/"delivery"/"order in" -> source
        "takeout", meta "takeout"
      - anything else (a saved recipe or a plain freeform entry) -> source
        "plan", meta the recipe's prep_time_minutes + cook_time_minutes as
        "N min" when both are known, else None (nothing informative to show
        rather than a misleading guess).

    Component_based plans (planning_mode == "component_based") have no
    real per-day assignment underneath — get_weekly_plan already covers
    this with a suggested_schedule/menu_is_suggested pair. This function
    mirrors that: it fills the 7 days from that same suggested spread, with
    every present slot as source "plan" / meta None (it's an example
    arrangement, not real timing), and passes menu_is_suggested through so
    the UI can note that.

    Omit weekly_plan_id for the household's current (most recently
    created) plan, same convention as get_weekly_plan. Returns
    week_start_date: None and an empty days list if no plan exists yet —
    there's nothing to anchor 7 days to.
    """
    conn = get_conn()
    household = conn.execute(
        "SELECT name FROM households WHERE id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    conn.close()
    household_name = household["name"] if household else ""

    plan = get_weekly_plan(weekly_plan_id)
    if not plan.get("weekly_plan_id"):
        return {"weekly_plan_id": None, "week_start_date": None, "household_name": household_name, "days": [], "menu_is_suggested": False}

    # design_handoff_plan_the_week: the Meals screen is where a week is
    # approved, so it needs both halves of that state — whether this plan
    # is still a draft (and what approving it would cost the grocery list),
    # and, once approved, who settled it and when. The preview is computed
    # for a draft only: an approved plan has already contributed, so its
    # number would always be zero and reads as a promise of nothing.
    approval = {
        "status": plan["status"],
        "approved_by": plan["approved_by"],
        "approved_at": plan["approved_at"],
        "approved_grocery_added": plan["approved_grocery_added"],
        "approved_grocery_skipped": plan["approved_grocery_skipped"],
        "grocery_preview": None,
    }
    if plan["status"] != "approved":
        approval["grocery_preview"] = preview_plan_grocery_impact(plan["weekly_plan_id"])
    # Every adult but the one who approved — the receipt's "{Other adult}
    # has been told the week is settled." Empty for a one-adult household,
    # which is what keeps that sentence from being written at all rather
    # than written about nobody.
    approval["other_adults"] = [
        p["name"] for p in get_household_people()
        if p["name"].strip().lower() != (plan["approved_by"] or "").strip().lower()
    ]

    slots = ("breakfast", "lunch", "dinner")
    start = date.fromisoformat(plan["week_start_date"])
    dates = [(start + timedelta(days=i)).isoformat() for i in range(7)]

    if plan["planning_mode"] == "component_based":
        by_date = {d["date"]: d for d in plan["menu"]}
        days = []
        today_str = date.today().isoformat()
        suggestions = None
        for d in dates:
            row = by_date.get(d, {})
            day = {"date": d}
            for s in slots:
                title = row.get(s)
                day[s] = {"title": title, "meta": None, "source": "plan"} if title else None
            if day["dinner"] is None and d >= today_str:
                if suggestions is None:
                    suggestions = _suggest_quick_dinners()
                day["dinner_suggestions"] = suggestions
            days.append(day)
        return {
            "weekly_plan_id": plan["weekly_plan_id"],
            "week_start_date": plan["week_start_date"],
            "household_name": household_name,
            "days": days,
            "menu_is_suggested": True,
            **approval,
        }

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.recipe_id, mpe.freeform_meal,
               COALESCE(r.name, mpe.freeform_meal) AS meal,
               mpe.slot_state, mpe.open_reason, mpe.reasoning, mpe.derived_from_json,
               r.prep_time_minutes, r.cook_time_minutes
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ?
        """,
        (plan["weekly_plan_id"],),
    ).fetchall()
    conn.close()

    def build_slot(row) -> dict | None:
        # The three states a slot can be in. Only a slot that is genuinely
        # absent returns None — and after a generation through
        # _finish_week_slots there shouldn't be any.
        if row["slot_state"] == "planned_empty":
            return {
                "title": "Out — nothing to cook", "meta": None, "source": "empty",
                "state": "planned_empty", "reason": row["reasoning"], "entry_id": row["id"],
            }
        if row["slot_state"] == "open":
            derived = json.loads(row["derived_from_json"] or "{}")
            return {
                "title": "I’d like your call on this one", "meta": None, "source": "open",
                "state": "open", "open_reason": row["open_reason"],
                "options": derived.get("options") or [], "entry_id": row["id"],
            }
        title = row["meal"]
        if not title:
            return None
        # The 4-9 word "why" shown under the meal name. Generated with the
        # plan (see meal_plan_entries.reasoning) rather than improvised on
        # demand, so it can't contradict the actual reason.
        common = {
            "state": "planned", "reason": row["reasoning"] or None, "entry_id": row["id"],
        }
        text = (row["freeform_meal"] or "").lower()
        if re.search(r"leftovers?\b", text):
            return {"title": title, "meta": "reheat", "source": "leftovers", **common}
        if re.search(r"take[\s-]?out|delivery|order in", text):
            return {"title": title, "meta": "takeout", "source": "takeout", **common}
        prep = row["prep_time_minutes"] or 0
        cook = row["cook_time_minutes"] or 0
        total = prep + cook
        meta = f"{total} min" if total else None
        return {"title": title, "meta": meta, "source": "plan", **common}

    by_date_slot = {}
    for r in rows:
        if r["slot"] in slots:
            by_date_slot[(r["date"], r["slot"])] = build_slot(r)

    days = [
        {"date": d, **{s: by_date_slot.get((d, s)) for s in slots}}
        for d in dates
    ]

    # This Week's day card (design_handoff_home_manager option 6a) shows the
    # same two-quick-dinner "Pick" rows on ANY day's empty dinner slot, not
    # just the one nearest gap get_needs_you_items flags for the Today band —
    # so a day beyond that 48h window still has something to tap instead of
    # a dead end. Only for today-or-future days: a past day's empty dinner
    # is just "not planned," nothing to suggest into it.
    today_str = date.today().isoformat()
    suggestions = None
    for day in days:
        if day["dinner"] is None and day["date"] >= today_str:
            if suggestions is None:
                suggestions = _suggest_quick_dinners()
            day["dinner_suggestions"] = suggestions

    intake = get_week_intake(plan["week_start_date"])
    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "week_start_date": plan["week_start_date"],
        "week_label": _format_week_range(plan["week_start_date"]),
        "household_name": household_name,
        "days": days,
        "menu_is_suggested": False,
        "headline": _week_headline(plan, days, intake),
        **approval,
    }


def _suggest_quick_dinners(limit: int = 2) -> list[dict]:
    """
    A couple of fast, currently-in-rotation recipes to offer as one-tap
    picks for an undecided dinner (see get_needs_you_items) — not a real
    recommendation engine, just "what's quick and not off the table right
    now." Excludes disliked and temporarily-excluded recipes; orders by
    known prep+cook time ascending (recipes with no timing info sort last,
    since we can't call them "quick"). Returns [] if there are no recipes
    saved yet — the needs-you card skips the suggestion rows rather than
    inventing options in that case.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT name, prep_time_minutes, cook_time_minutes
        FROM recipes
        WHERE household_id = ? AND rating != 'disliked' AND temporarily_excluded = 0
        ORDER BY
            (prep_time_minutes IS NULL AND cook_time_minutes IS NULL) ASC,
            (COALESCE(prep_time_minutes, 0) + COALESCE(cook_time_minutes, 0)) ASC
        LIMIT ?
        """,
        (HOUSEHOLD_ID, limit),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        total = (r["prep_time_minutes"] or 0) + (r["cook_time_minutes"] or 0)
        out.append({"meal": r["name"], "minutes": total or None})
    return out


def get_needs_you_items() -> list[dict]:
    """
    The Today screen's needs-you band (design_handoff_shell/README.md §4,
    §9 Step 5) — 0-3 cards for things that need a decision right now.
    Starting with the two rules the README calls out explicitly rather
    than a general prioritisation engine (that's future work):

      1. **Dinner decision** — the soonest of tonight's/tomorrow's dinner
         slots that's still empty (the "within 48 hours" window from the
         spec). Comes with up to two quick-recipe suggestions (see
         _suggest_quick_dinners) so the card's "Pick" rows have something
         real to offer — the card is omitted entirely if there isn't even
         one recipe saved yet, since a decision card with nothing to pick
         is worse than no card.
      2. **Shop run** — there are ungathered grocery items *and* something
         is actually planned (any slot, any meal) in the next 48 hours
         that hasn't been cooked yet. There's no ingredient-to-grocery-item
         link in this schema to check "these specific items block that
         specific meal," so this is a proxy: "you have a shop to do, and
         something's coming up soon" rather than a precise per-ingredient
         match — documented here rather than pretending it's exact.

    Returns at most one card per rule (max 2 for now, out of the spec's
    0-3 headroom) in the order the mock shows them: dinner decision first,
    then shop run.
    """
    conn = get_conn()
    today = date.today()
    horizon_end = today + timedelta(days=2)  # today, tomorrow, day-after exclusive edge -> "within 48h" covers today+tomorrow

    items: list[dict] = []

    # ---- Rule 1: dinner decision ----
    dinner_rows = conn.execute(
        "SELECT date FROM meal_plan_entries WHERE household_id = ? AND slot = 'dinner' AND date >= ? AND date < ?",
        (HOUSEHOLD_ID, today.isoformat(), horizon_end.isoformat()),
    ).fetchall()
    planned_dinner_dates = {r["date"] for r in dinner_rows}
    for offset in (0, 1):
        candidate = (today + timedelta(days=offset)).isoformat()
        if candidate in planned_dinner_dates:
            continue
        options = _suggest_quick_dinners()
        if not options:
            break  # no recipes to suggest at all -- nothing later in the loop will differ, so stop
        when = "Tonight" if offset == 0 else "Tomorrow"
        items.append({
            "type": "dinner_decision",
            "kicker": "DINNER",
            "title": when + " needs a dinner",
            "urgency": "urgent",
            "date": candidate,
            "slot": "dinner",
            "options": options,
        })
        break  # only the soonest empty dinner becomes a card

    # ---- Rule 2: shop run ----
    # Same "needed, not excluded from the list" filter list_grocery_list
    # uses for the normal shopping list, so this count matches what the
    # Grocery tab itself would show.
    needed_count = conn.execute(
        "SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ? AND status = 'needed' AND excluded_from_list = 0",
        (HOUSEHOLD_ID,),
    ).fetchone()["n"]

    # cooked_status uses 'pending', not 'cooked' — see meal_plan_entries schema.
    upcoming_meal = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE household_id = ? AND date >= ? AND date < ? AND cooked_status = 'pending'",
        (HOUSEHOLD_ID, today.isoformat(), horizon_end.isoformat()),
    ).fetchone()["n"]

    if needed_count > 0 and upcoming_meal > 0:
        sample = conn.execute(
            "SELECT item FROM grocery_items WHERE household_id = ? AND status = 'needed' AND excluded_from_list = 0 ORDER BY id ASC LIMIT 4",
            (HOUSEHOLD_ID,),
        ).fetchall()
        items.append({
            "type": "shop_run",
            "kicker": "SHOP RUN",
            "title": "Grocery run needed",
            "urgency": "warn",
            "count": needed_count,
            "sample_items": [s["item"] for s in sample],
        })

    conn.close()
    return items


def resolve_needs_you_dinner(
    meal_date: str, meal: str, add_ingredients_to_grocery_list: bool = False
) -> dict:
    """
    Resolve a needs-you dinner-decision card by planning the picked meal —
    thin wrapper around plan_meal that also attaches it to the household's
    current weekly plan (if one exists) so it shows up correctly in the
    Week tab's menu, then returns the refreshed needs-you list so the
    Today screen can just re-render from the response.

    add_ingredients_to_grocery_list carries the answer the card's confirm
    step collected. It is a real question asked of a real person, which is
    what makes this an explicit yes and not a silent write — the same
    standard chat is held to (see plan_meal). It defaults to False so a
    caller that forgets to ask adds nothing.
    """
    plan = get_weekly_plan()
    weekly_plan_id = plan.get("weekly_plan_id")
    result = plan_meal(
        meal_date, meal, slot="dinner", weekly_plan_id=weekly_plan_id,
        add_ingredients_to_grocery_list=add_ingredients_to_grocery_list,
    )
    return {
        "items": get_needs_you_items(),
        "groceries_added": result["groceries_added"],
        "already_have_skipped": result["already_have_skipped"],
    }


def _weekly_plan_is_approved(weekly_plan_id: int | None) -> bool:
    """Whether a plan is approved — i.e. whether its ingredients are already on the grocery list."""
    if weekly_plan_id is None:
        return False
    conn = get_conn()
    row = conn.execute(
        "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?", (weekly_plan_id, HOUSEHOLD_ID)
    ).fetchone()
    conn.close()
    return bool(row) and row["status"] == "approved"


def _plan_grocery_candidate_entries(conn, weekly_plan_id: int):
    """
    The plan's meal entries whose ingredients have NOT yet been recorded as
    contributing to the grocery list — i.e. exactly what an approval would
    add. Shared by approve_weekly_plan (which then adds them) and
    preview_plan_grocery_impact (which only counts them), so the number the
    draft screen promises and the number approval actually delivers come
    from one query rather than two that can drift apart.
    """
    return conn.execute(
        """
        SELECT mpe.id, r.ingredients_json
        FROM meal_plan_entries mpe
        JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM meal_plan_grocery_links mpgl
              WHERE mpgl.meal_plan_entry_id = mpe.id AND mpgl.household_id = mpe.household_id
          )
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchall()


def preview_plan_grocery_impact(weekly_plan_id: int) -> dict:
    """
    What approving this plan WOULD put on the grocery list, without putting
    anything there. Writes nothing at all.

    This is what makes the draft screen's promise a real number rather than
    a guess: "I haven't put anything on your shopping list yet. Approve the
    week and I'll build it — 22 items, less whatever's already in your
    kitchen." (design_handoff_plan_the_week/COPY.md → Draft).

    Mirrors _add_recipe_ingredients_to_grocery_list's own two rules exactly
    — entries that already contributed are skipped, and an ingredient whose
    name matches a tracked inventory item with a real quantity counts as
    already-in-the-kitchen rather than as something to buy. Deliberately
    counts DISTINCT ingredient names, not raw rows: two recipes both
    wanting onions consolidate onto one grocery line (add_grocery_item
    merges by name), so counting rows would promise more items than
    approval actually creates.
    """
    conn = get_conn()
    plan = conn.execute(
        "SELECT id, status FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not plan:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    entries = _plan_grocery_candidate_entries(conn, weekly_plan_id)
    have_names = {
        row["item"].strip().lower()
        for row in conn.execute(
            "SELECT item FROM inventory_items WHERE household_id = ? AND TRIM(quantity) != ''",
            (HOUSEHOLD_ID,),
        ).fetchall()
    }
    conn.close()

    would_add: set[str] = set()
    already_have: set[str] = set()
    for entry in entries:
        for ing in json.loads(entry["ingredients_json"]):
            name = ing["item"].strip()
            if name.lower() in have_names:
                already_have.add(name.lower())
            else:
                would_add.add(name.lower())
    return {
        "weekly_plan_id": weekly_plan_id,
        "would_add_count": len(would_add),
        "already_have_count": len(already_have),
    }


def approve_weekly_plan(weekly_plan_id: int, approved_by: str = "") -> dict:
    """
    Approve a weekly plan — and, in the same step, put its meals'
    ingredients on the grocery list.

    `approved_by` is an adult's name (see schema.sql on
    weekly_plans.approved_by for why a name and not a member id). It's
    recorded with the approval time so the Meals screen can render the
    receipt the design calls for — "APPROVED BY EMILY · 9:41AM" — and so
    the other adult can be told who settled the week. Optional: an approval
    with no name still approves, and the receipt just drops the name rather
    than inventing one.

    Approving used to only flip a status flag; the grocery list had
    already been filled in during generation, whether or not the household
    ever agreed to that plan. Ingredients from drafts that were changed,
    abandoned or never approved piled up on the real shopping list as a
    result. Now generation adds nothing (see plan_meal, whose
    add_ingredients_to_grocery_list defaults to False) and approval is
    what populates the list, so the list only ever reflects a week the
    household actually said yes to.

    Safe to call more than once. Two separate guards, because they cover
    different things:

    - Re-approving an ALREADY-approved plan adds nothing at all. The
      grocery work happens on the transition into 'approved', not on every
      call. Without this, an entry whose ingredients were all skipped as
      already-in-the-pantry leaves no trace that it was ever considered
      (the ledger only records what was actually added), so a later
      re-approve would add them for real once the pantry had emptied — a
      surprise write to the list nobody asked for.
    - Within a single approval, an entry whose contributions are already
      recorded in meal_plan_grocery_links is skipped, so a plan whose
      meals already put their ingredients on the list another way (a swap,
      or plan_meal called with the flag) doesn't double up its quantities.

    Raises ValueError for a weekly_plan_id that doesn't exist, rather than
    reporting a cheerful approval of nothing — same as clear_weekly_plan
    and swap_component_in_plan.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()
    if not existing:
        conn.close()
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    was_already_approved = existing["status"] == "approved"
    # A re-approval never overwrites the original approver/time — the
    # receipt names who actually settled the week, and the first yes is the
    # one that built the list. Only a genuine transition into 'approved'
    # (including a re-approval after a reopen, which clears these back out)
    # writes them.
    conn.execute(
        "UPDATE weekly_plans SET status = 'approved', updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    if not was_already_approved:
        conn.execute(
            "UPDATE weekly_plans SET approved_by = ?, approved_at = datetime('now') WHERE id = ? AND household_id = ?",
            (approved_by.strip(), weekly_plan_id, HOUSEHOLD_ID),
        )
    conn.commit()
    if was_already_approved:
        receipt = conn.execute(
            "SELECT approved_by, approved_at, approved_grocery_added, approved_grocery_skipped "
            "FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, HOUSEHOLD_ID),
        ).fetchone()
        conn.close()
        return {
            "weekly_plan_id": weekly_plan_id,
            "status": "approved",
            "groceries_added": [],
            "already_have_skipped": [],
            # The counts stay the ORIGINAL approval's — this call added
            # nothing, and the receipt still describes the yes that built
            # the list.
            "groceries_added_count": receipt["approved_grocery_added"] if receipt else 0,
            "already_have_skipped_count": receipt["approved_grocery_skipped"] if receipt else 0,
            "was_already_approved": True,
            "approved_by": receipt["approved_by"] if receipt else "",
            "approved_at": receipt["approved_at"] if receipt else None,
        }
    entries = _plan_grocery_candidate_entries(conn, weekly_plan_id)
    approved_at = conn.execute(
        "SELECT approved_at FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    ).fetchone()["approved_at"]
    conn.close()

    added_items = []
    already_have = []
    for entry in entries:
        added, have = _add_recipe_ingredients_to_grocery_list(
            entry["id"], json.loads(entry["ingredients_json"]), weekly_plan_id
        )
        added_items.extend(added)
        already_have.extend(have)

    # Counted as distinct names, matching preview_plan_grocery_impact, so
    # the number the draft promised and the number the receipt reports are
    # the same number rather than two different ways of counting the same
    # groceries. Persisted because neither is recoverable later — see
    # schema.sql on approved_grocery_added.
    added_count = len({n.strip().lower() for n in added_items})
    skipped_count = len({n.strip().lower() for n in already_have})
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET approved_grocery_added = ?, approved_grocery_skipped = ? "
        "WHERE id = ? AND household_id = ?",
        (added_count, skipped_count, weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()

    return {
        "weekly_plan_id": weekly_plan_id,
        "status": "approved",
        "groceries_added": added_items,
        "already_have_skipped": already_have,
        "groceries_added_count": added_count,
        "already_have_skipped_count": skipped_count,
        "was_already_approved": False,
        "approved_by": approved_by.strip(),
        "approved_at": approved_at,
    }


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
    a saved recipe name or a freeform description, same as plan_meal. The
    old meal's auto-added grocery ingredients are removed first (trimmed or
    deleted, whatever the amount it contributed calls for — see
    _reverse_meal_grocery_contributions) so the grocery list reflects only
    the new meal afterward instead of carrying both.
    """
    conn = get_conn()
    old_entries = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ?",
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
    ).fetchall()
    conn.close()
    for row in old_entries:
        _reverse_meal_grocery_contributions(row["id"])

    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? AND household_id = ?",
        (weekly_plan_id, meal_date, slot, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return plan_meal(
        meal_date, new_meal, slot=slot, food_groups=food_groups, weekly_plan_id=weekly_plan_id,
        # Only put the new meal's ingredients on the list if this week has
        # already been approved — approval is what put the old meal's
        # ingredients there in the first place, and the reversal above just
        # took them back off. Swapping inside a still-unapproved draft
        # leaves the grocery list alone, exactly as generating it did.
        add_ingredients_to_grocery_list=_weekly_plan_is_approved(weekly_plan_id),
    )


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

    match = conn.execute(
        """
        SELECT mpe.id FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.component_category = ? AND mpe.household_id = ?
          AND COALESCE(r.name, mpe.freeform_meal) = ?
        LIMIT 1
        """,
        (weekly_plan_id, component_category, HOUSEHOLD_ID, old_meal),
    ).fetchone()
    conn.close()
    if not match:
        removed = 0
    else:
        # Reverse the old item's grocery contribution first (see
        # swap_meal_in_plan) so replacing one component actually swaps its
        # ingredients on the list rather than piling the new ones on top.
        _reverse_meal_grocery_contributions(match["id"])
        conn = get_conn()
        deleted = conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (match["id"],))
        conn.commit()
        removed = deleted.rowcount
        conn.close()
    if not removed:
        raise ValueError(f"Couldn't find '{old_meal}' under category '{component_category}' in that plan.")
    return plan_meal(
        week_start_date, new_meal, food_groups=food_groups, weekly_plan_id=weekly_plan_id,
        component_category=component_category,
        # See swap_meal_in_plan — mirrors the plan's approved state.
        add_ingredients_to_grocery_list=_weekly_plan_is_approved(weekly_plan_id),
    )


# ---------- Self-service reset ----------
# "This week needs a do-over" without going through chat and without
# touching anything else the household owns. Deliberately narrow: recipes,
# chores, members, inventory and the household's own memory are all out of
# scope here — wiping those is reset_household.py, an admin script that is
# not meant for regular use and has no in-app entry point on purpose.

def clear_weekly_plan(weekly_plan_id: int | None = None) -> dict:
    """
    Take every meal off a week's plan at once — the whole-plan version of
    un-planning a single meal. Defaults to the household's current plan
    (see _current_weekly_plan_row), same as every other plan-scoped tool
    that takes an optional weekly_plan_id.

    Each entry's grocery contribution is reversed first, one meal at a
    time, through the same _reverse_meal_grocery_contributions() call
    swap_meal_in_plan already makes — so the list is left holding only
    what's still actually planned or was asked for directly, rather than a
    week's worth of orphaned ingredients. That helper leaves anything
    already moved to in_cart/purchased alone (the shopper has acted on it),
    which is the behaviour wanted here too: clearing the plan shouldn't
    yank something out of a cart mid-trip.

    The weekly_plans row is emptied, not deleted. The week's dates and its
    constraints_notes ("out Thursday, keep it under 30 minutes") survive,
    so re-planning the same week fills this plan back in instead of
    stranding an empty one beside a new one for _current_weekly_plan_row to
    choose between. Its status drops back to 'draft' — an empty week isn't
    an approved one. Prep tasks go with the meals, since they only describe
    prepping meals that no longer exist.
    """
    conn = get_conn()
    if weekly_plan_id is None:
        plan = _current_weekly_plan_row(conn)
    else:
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, HOUSEHOLD_ID),
        ).fetchone()
        if not plan:
            conn.close()
            raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    if not plan:
        conn.close()
        return {
            "weekly_plan_id": None, "week_start_date": None, "meals_cleared": 0,
            "removed_items": [], "trimmed_items": [], "prep_tasks_cleared": 0,
        }
    weekly_plan_id = plan["id"]
    week_start_date = plan["week_start_date"]
    entry_ids = [
        row["id"] for row in conn.execute(
            "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (weekly_plan_id, HOUSEHOLD_ID),
        ).fetchall()
    ]
    conn.close()

    removed_items = []
    trimmed_items = []
    for entry_id in entry_ids:
        reversal = _reverse_meal_grocery_contributions(entry_id)
        removed_items.extend(reversal["removed_items"])
        trimmed_items.extend(reversal["trimmed_items"])

    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    prep = conn.execute(
        "DELETE FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    prep_tasks_cleared = prep.rowcount
    conn.execute(
        "UPDATE weekly_plans SET status = 'draft', updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {
        "weekly_plan_id": weekly_plan_id,
        "week_start_date": week_start_date,
        "meals_cleared": len(entry_ids),
        "removed_items": removed_items,
        "trimmed_items": trimmed_items,
        "prep_tasks_cleared": prep_tasks_cleared,
    }


def get_reset_preview() -> dict:
    """
    What a reset would actually remove, counted before anything happens, so
    the confirm dialog can say "12 planned meals and 23 grocery items"
    instead of asking the household to agree to an unspecified wipe — and
    can disable a choice that would do nothing. Read-only.

    grocery_count counts what clear_grocery_list('needed') would delete,
    which is the whole list as the Grocery screen means it: everything
    still to buy, whether a meal plan put it there or a person did.
    Anything already in a cart or bought stays, and isn't counted here.
    """
    conn = get_conn()
    plan = _current_weekly_plan_row(conn)
    meal_count = 0
    if plan:
        meal_count = conn.execute(
            "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ?",
            (plan["id"], HOUSEHOLD_ID),
        ).fetchone()["n"]
    grocery_count = conn.execute(
        "SELECT COUNT(*) AS n FROM grocery_items WHERE household_id = ? AND status = 'needed'",
        (HOUSEHOLD_ID,),
    ).fetchone()["n"]
    conn.close()
    return {
        "weekly_plan_id": plan["id"] if plan else None,
        "week_start_date": plan["week_start_date"] if plan else None,
        "meal_count": meal_count,
        "grocery_count": grocery_count,
    }


# ---------- Needs-attention queue (Phase 4, §4.4) ----------
# The first real multi-item "needs your attention" surface — until now the
# only precedent (get_feedback_nudge) was a single computed check with
# nothing persisted. Built for inventory-depletion matches that are too
# uncertain to act on silently, but kept general (kind + freeform
# detail_json) so other soft nudges can land here later instead of each
# inventing their own one-off pattern.

def add_attention_item(kind: str, summary: str, detail: dict | None = None) -> dict:
    """
    Queue something for later review rather than guessing or silently
    dropping it. Skips creating a duplicate if a pending item with the same
    kind+summary already exists, so the same ambiguous match doesn't spam
    the queue every time it's encountered again before being resolved.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM attention_items WHERE household_id = ? AND kind = ? AND summary = ? AND status = 'pending'",
        (HOUSEHOLD_ID, kind, summary),
    ).fetchone()
    if existing:
        conn.close()
        return {"id": existing["id"], "created": False}
    cur = conn.execute(
        "INSERT INTO attention_items (household_id, kind, summary, detail_json) VALUES (?, ?, ?, ?)",
        (HOUSEHOLD_ID, kind, summary, json.dumps(detail or {})),
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return {"id": item_id, "created": True}


def resolve_attention_item(item_id: int, status: str = "resolved") -> dict:
    """Mark a queued attention item 'resolved' (handled) or 'dismissed' (not relevant/skip it) — either way it stops showing up in get_attention_items."""
    conn = get_conn()
    conn.execute(
        "UPDATE attention_items SET status = ?, resolved_at = datetime('now') WHERE id = ? AND household_id = ?",
        (status, item_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"id": item_id, "status": status}


def record_attention_item_usage(item_id: int, amount_used: str = "") -> dict:
    """
    Resolve a "needs_amount_used" inventory_depletion attention item by
    applying the amount the person says they actually used, rather than
    asking them to instead go figure out and report what's left (less
    intuitive in the moment, right after cooking). Reuses the same
    lenient subtract logic as the general chat "use" flow (_try_subtract_
    quantity) — appropriate here because, unlike the original automated
    depletion attempt, this IS an explicit person-confirmed amount, so an
    unparseable/freeform tracked quantity can safely be treated as "used
    it all" rather than queued again. Leaving amount_used blank means
    "used all of it," same convention as update_inventory's "use" action.
    Marks the attention item resolved either way (even if the candidate
    row no longer exists) so it doesn't stay stuck in the queue.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, detail_json FROM attention_items WHERE id = ? AND household_id = ? AND status = 'pending'",
        (item_id, HOUSEHOLD_ID),
    ).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "applied": False, "reason": "not found or already resolved"}
    detail = json.loads(row["detail_json"])
    candidate_item_id = detail.get("candidate_item_id")
    inv_row = None
    if candidate_item_id is not None:
        inv_row = conn.execute(
            "SELECT id, item, quantity FROM inventory_items WHERE id = ? AND household_id = ?",
            (candidate_item_id, HOUSEHOLD_ID),
        ).fetchone()
    conn.close()

    if inv_row is None:
        resolve_attention_item(item_id, "resolved")
        return {"item_id": item_id, "applied": False, "reason": "tracked item no longer exists"}

    remaining, reconciled = _try_subtract_quantity(inv_row["quantity"] or "", amount_used)
    conn = get_conn()
    if remaining is None:
        conn.execute("DELETE FROM inventory_items WHERE id = ?", (inv_row["id"],))
        conn.commit()
        conn.close()
        resolve_attention_item(item_id, "resolved")
        return {"item_id": item_id, "applied": True, "item": inv_row["item"], "removed": True, "units_reconciled": True}
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
        (remaining, inv_row["id"]),
    )
    conn.commit()
    conn.close()
    resolve_attention_item(item_id, "resolved")
    return {"item_id": item_id, "applied": True, "item": inv_row["item"], "quantity": remaining, "units_reconciled": reconciled}


def get_attention_items() -> list[dict]:
    """
    The unified "needs your attention" list — combines the feedback nudge
    (a recently-cooked meal with no rating yet, see get_feedback_nudge)
    with persisted queue items (currently: low-confidence
    ingredient-to-inventory matches from checking a meal off as cooked, see
    check_off_meal). Check this proactively near the start of a
    conversation, the same way get_expiring_soon/get_cross_location_duplicates
    are checked, and work anything pending into the reply in one low-key
    way — not an interrogation checklist. Each item has an `id` (None for
    the feedback nudge, since that's computed rather than a real row —
    only pass real ids to resolve_attention_item), `kind`, `summary`, and
    `detail`.
    """
    items = []
    nudge = get_feedback_nudge()
    if nudge.get("has_nudge"):
        items.append({
            "id": None,
            "kind": "feedback_nudge",
            "summary": f"{nudge['meal']} was cooked recently and hasn't been rated yet — worth asking how it went.",
            "detail": {"meal": nudge["meal"], "cooked_at": nudge["cooked_at"]},
        })
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, kind, summary, detail_json, created_at FROM attention_items WHERE household_id = ? AND status = 'pending' ORDER BY created_at ASC",
        (HOUSEHOLD_ID,),
    ).fetchall()
    conn.close()
    for r in rows:
        items.append({
            "id": r["id"], "kind": r["kind"], "summary": r["summary"],
            "detail": json.loads(r["detail_json"]), "created_at": r["created_at"],
        })
    return items


# ---------- Cooker execution layer (recipe detail, prep schedule, check-off) ----------

def _singularize(word: str) -> str:
    return word[:-1] if word.endswith("s") else word


def _find_inventory_match(ingredient_item: str, inventory_items: list[dict]) -> tuple[dict | None, bool]:
    """
    Try to match a recipe ingredient name to a tracked inventory row for
    depletion purposes. Returns (matched_row_or_None, confident).
    confident=True only for an exact case-insensitive match (allowing a
    simple trailing-'s' plural difference, e.g. "egg" vs "eggs") — anything
    looser is returned as a candidate with confident=False rather than
    silently treated as the same thing, since 'close' isn't a safe basis
    to deplete inventory from on its own (e.g. "garlic" vs a tracked
    "garlic bulb" — probably the same ingredient, but not safe to assume).

    When more than one loose candidate matches, they're ranked rather than
    just taking whichever happens to come first in inventory order — e.g.
    for ingredient "feta cheese" tracked alongside both a "Feta" and a
    generic "Cheese" row, "Feta" should be the one surfaced for review, not
    "Cheese". Candidates whose full name exactly matches one of the
    ingredient's words win over a merely-partial substring match, and among
    those, an earlier word wins over a later one — in an English compound
    food name ("feta cheese", "soy sauce", "chicken broth") the leading
    word is typically the specific descriptor and the trailing word the
    generic category, so matching on the earlier word is the more specific,
    more likely-correct guess.
    """
    name = (ingredient_item or "").strip().lower()
    if not name:
        return None, False
    name_words = name.split()
    name_singular = _singularize(name)
    for row in inventory_items:
        row_name = row["item"].strip().lower()
        row_singular = _singularize(row_name)
        if name_singular == row_singular:
            return row, True

    candidates = [
        row for row in inventory_items
        if name in row["item"].strip().lower() or row["item"].strip().lower() in name
    ]
    if not candidates:
        return None, False

    def candidate_rank(row):
        row_words = [_singularize(w) for w in row["item"].strip().lower().split()]
        for idx, w in enumerate(name_words):
            if _singularize(w) in row_words:
                return (0, idx)  # exact whole-word match — ranked by how early that word appears
        return (1, 0)  # only a raw substring overlap, no shared whole word

    candidates.sort(key=candidate_rank)
    return candidates[0], False


def _use_inventory_row_by_id(item_id: int, minus_qty: str) -> dict:
    """Deplete a specific, already-identified inventory row by id (not by name lookup) — used by deplete_inventory_for_meal once a match has already been resolved, so there's no risk of a second, different row matching the same name."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID)
    ).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    # Unlike the general chat "use" flow (_try_subtract_quantity's default
    # for update_inventory, where an unparseable/freeform existing quantity
    # like "a big bag" is treated as fully consumed), don't apply that same
    # leniency here — that default makes sense when a person explicitly
    # says "used the rice," but automated depletion has no such explicit
    # confirmation, so guessing "fully used" risks silently wiping out
    # inventory that's mostly still there. Flag it for review instead.
    if _parse_quantity(row["quantity"] or "") is None:
        conn.close()
        return {"item_id": item_id, "item": row["item"], "quantity": row["quantity"], "units_reconciled": False}
    remaining, reconciled = _try_subtract_quantity(row["quantity"] or "", minus_qty)
    if remaining is None:
        conn.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
        return {"item_id": item_id, "item": row["item"], "removed": True, "units_reconciled": True}
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
        (remaining, item_id),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "quantity": remaining, "units_reconciled": reconciled}


def deplete_inventory_for_meal(entry_id: int) -> dict:
    """
    Phase 4, §4.4: deplete tracked inventory for a meal's ingredients when
    it's checked off as cooked — called automatically from check_off_meal,
    not meant to be called directly for a meal that isn't actually being
    marked done. Confident ingredient-to-inventory name matches deplete
    automatically with no interruption — and if the recipe itself states a
    quantity for the ingredient (e.g. "1 lb deli meat"), that's trusted as
    the amount used without asking, even when the *existing* tracked
    quantity is too imprecise or in a mismatched unit to compute an exact
    new remaining total (the recipe already told us what was used; there's
    just nothing more precise to write back for what's left, so the
    tracked row is simply left as-is rather than interrupting to ask about
    something already answered). The only things actually queued into
    get_attention_items for review are genuine unknowns: an ambiguous name
    match ("garlic" vs a tracked "garlic bulb"), or a confident match where
    the recipe itself doesn't say how much of the ingredient was used —
    guessing "all of it" there risks wrongly zeroing out inventory that's
    still mostly there, so it's worth a quick check instead. Freeform meals
    (no saved recipe) have no ingredient list, so there's nothing to
    deplete or flag.
    """
    conn = get_conn()
    entry = conn.execute(
        "SELECT mpe.id, mpe.recipe_id, COALESCE(r.name, mpe.freeform_meal) AS meal_name "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.id = ? AND mpe.household_id = ?",
        (entry_id, HOUSEHOLD_ID),
    ).fetchone()
    conn.close()
    if not entry or not entry["recipe_id"]:
        return {"entry_id": entry_id, "depleted": [], "queued_for_review": []}

    try:
        recipe = get_recipe(entry["meal_name"])
    except ValueError:
        return {"entry_id": entry_id, "depleted": [], "queued_for_review": []}
    ingredients = recipe.get("ingredients", [])
    if not ingredients:
        return {"entry_id": entry_id, "depleted": [], "queued_for_review": []}

    inventory = get_inventory()
    depleted, queued = [], []
    for ing in ingredients:
        ing_name = (ing.get("item") or "").strip()
        if not ing_name:
            continue
        match, confident = _find_inventory_match(ing_name, inventory)
        if not match:
            continue  # nothing tracked for this ingredient — nothing to deplete or flag
        if not confident:
            summary = (
                f"Used {ing_name} for {entry['meal_name']} — closest thing tracked is "
                f"\"{match['item']}\" ({match['quantity'] or 'no quantity tracked'}). Deplete that, "
                f"or was this something else?"
            )
            add_attention_item("inventory_depletion", summary, {
                "entry_id": entry_id, "meal": entry["meal_name"], "ingredient": ing_name,
                "candidate_item_id": match["id"], "candidate_item": match["item"],
            })
            queued.append({"ingredient": ing_name, "candidate": match["item"]})
            continue
        qty_used = (ing.get("qty") or "").strip()
        if not qty_used:
            # The recipe doesn't say how much of this ingredient was used
            # (freeform, e.g. "salt to taste") — genuinely unclear, and
            # assuming "used all of it" here could wrongly wipe out
            # inventory that's still mostly there, so this is worth a
            # quick check rather than a guess.
            tracked_qty = match["quantity"] or "no amount tracked"
            summary = (
                f"How much {ing_name} did you use for {entry['meal_name']}? "
                f"(tracking \"{match['item']}\" — {tracked_qty})"
            )
            add_attention_item("inventory_depletion", summary, {
                "entry_id": entry_id, "meal": entry["meal_name"], "ingredient": ing_name,
                "candidate_item_id": match["id"], "candidate_item": match["item"],
                "needs_amount_used": True,
            })
            queued.append({"ingredient": ing_name, "candidate": match["item"]})
            continue
        # The recipe told us exactly how much was used, so this always
        # counts as depleted (not queued) even if the *existing* tracked
        # quantity was too imprecise or in a mismatched unit for
        # _use_inventory_row_by_id to compute an exact new remaining total
        # — see units_reconciled on the result for whether the tracked row
        # was actually updated or just left as-is.
        result = _use_inventory_row_by_id(match["id"], qty_used)
        depleted.append({"ingredient": ing_name, "item": match["item"], "result": result})
    return {"entry_id": entry_id, "depleted": depleted, "queued_for_review": queued}


def check_off_meal(entry_id: int, status: str = "done") -> dict:
    """
    Mark a specific planned meal (meal_plan_entries row) as cooked
    (status='done') or back to pending. Use get_weekly_plan/get_plan_progress
    to find the entry_id. Marking a meal done also attempts to deplete its
    ingredients from tracked inventory (see deplete_inventory_for_meal) —
    confident matches happen silently; anything uncertain is queued into
    get_attention_items rather than guessed at, and both are reported back
    in the result so it can be mentioned if relevant.

    Component-based plans are meal-prepped: the same component (e.g. a
    "Jello Bowl" side) is often planned for several meals across the week,
    but it only gets cooked once in one batch, not separately per meal —
    see get_cooker_view, which shows those as a single merged card rather
    than repeating the row. So checking off any one of those linked entries
    marks every entry sharing that same component name (within the same
    plan) done/pending together, and inventory depletion still only runs
    once (against entry_id itself) since the ingredients were only actually
    used once for the whole batch.
    """
    conn = get_conn()
    row = conn.execute(
        """
        SELECT mpe.weekly_plan_id, COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.id = ? AND mpe.household_id = ?
        """,
        (entry_id, HOUSEHOLD_ID),
    ).fetchone()
    linked_ids = [entry_id]
    if row and row["weekly_plan_id"] is not None:
        plan_row = conn.execute(
            "SELECT planning_mode FROM weekly_plans WHERE id = ?", (row["weekly_plan_id"],)
        ).fetchone()
        if plan_row and plan_row["planning_mode"] == "component_based":
            siblings = conn.execute(
                """
                SELECT mpe.id FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
                WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ?
                  AND LOWER(COALESCE(r.name, mpe.freeform_meal)) = LOWER(?)
                """,
                (HOUSEHOLD_ID, row["weekly_plan_id"], row["meal"]),
            ).fetchall()
            if siblings:
                linked_ids = [r["id"] for r in siblings]

    cooked_at = "datetime('now')" if status == "done" else "NULL"
    conn.executemany(
        f"UPDATE meal_plan_entries SET cooked_status = ?, cooked_at = {cooked_at} WHERE id = ? AND household_id = ?",
        [(status, eid, HOUSEHOLD_ID) for eid in linked_ids],
    )
    conn.commit()
    conn.close()
    result = {"entry_id": entry_id, "cooked_status": status, "linked_entry_ids": linked_ids}
    if status == "done":
        depletion = deplete_inventory_for_meal(entry_id)
        result["inventory_depleted"] = depletion["depleted"]
        result["inventory_queued_for_review"] = depletion["queued_for_review"]
    return result


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
        row = _current_weekly_plan_row(conn)
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
            "reasoning": m.get("reasoning"),
            "ingredients": recipe["ingredients"] if recipe else [],
            "instructions": recipe["instructions"] if recipe else [],
            "default_servings": recipe["default_servings"] if recipe else None,
            "prep_time_minutes": recipe["prep_time_minutes"] if recipe else None,
            "cook_time_minutes": recipe["cook_time_minutes"] if recipe else None,
            "advance_prep_notes": recipe["advance_prep_notes"] if recipe else "",
            "advance_prep_step_indices": recipe["advance_prep_step_indices"] if recipe else [],
            "has_full_recipe": recipe is not None,
        })

    if plan["planning_mode"] == "component_based":
        # get_weekly_plan's meals are ordered by date/slot, which is
        # meaningless for a component-based plan (every entry shares the
        # same placeholder date) — order by the canonical component
        # category order instead (protein, vegetable, carb, etc.) so the
        # Cooker view reads grouped the same way the plan itself was
        # organized, rather than incidental insertion order.
        cat_rank = {c: i for i, c in enumerate(_COMPONENT_CATEGORY_ORDER)}
        meals.sort(key=lambda m: cat_rank.get(m["component_category"] or "", len(_COMPONENT_CATEGORY_ORDER)))

        # A component-based plan assumes meal prep: the same component
        # (e.g. a "Jello Bowl" side) commonly gets planned into several
        # meals for the week, but it only needs to be batch-cooked once —
        # so collapse repeat entries for the same component name into one
        # card instead of showing "Jello Bowl" three separate times, and
        # scale its ingredients up to a batch that covers every use
        # (see check_off_meal, which marks every collapsed entry done
        # together once this one card gets checked off).
        grouped: dict[str, dict] = {}
        order: list[str] = []
        for m in meals:
            key = (m["meal"] or "").strip().lower()
            if key not in grouped:
                grouped[key] = {**m, "entry_ids": [m["entry_id"]], "meal_count": 1, "_statuses": [m["cooked_status"]]}
                order.append(key)
            else:
                g = grouped[key]
                g["entry_ids"].append(m["entry_id"])
                g["meal_count"] += 1
                g["_statuses"].append(m["cooked_status"])

        merged_meals = []
        for key in order:
            g = grouped[key]
            statuses = g.pop("_statuses")
            g["cooked_status"] = "done" if all(s == "done" for s in statuses) else "pending"
            count = g["meal_count"]
            if count > 1 and g["has_full_recipe"] and g["default_servings"]:
                batch_servings = g["default_servings"] * count
                scaled = scale_recipe(g["meal"], batch_servings)
                g["ingredients"] = scaled["scaled_ingredients"]
                g["default_servings"] = batch_servings
                g["batch_note"] = f"Bulk-cook once — makes enough for all {count} meals this week."
            else:
                g["batch_note"] = None
            merged_meals.append(g)
        meals = merged_meals

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


def is_multi_store_household() -> bool:
    """
    Whether this household actually shops at more than one store — the
    signal the Grocery List view uses to decide whether "By store" is
    worth showing at all (Phase 6 PRD §4.1/§4.4 audit finding: the tab used
    to show unconditionally, with nothing behind it distinguishing single-
    from multi-store households). True if either the saved usual_stores
    list has more than one entry, or more than one distinct store name is
    actually tagged on a grocery item right now — covers both "told the
    app up front" and "tagged ad hoc without ever saving it as a usual
    store."
    """
    conn = get_conn()
    prefs = conn.execute(
        "SELECT usual_stores_json FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    usual = set(json.loads(prefs["usual_stores_json"])) if prefs else set()
    tagged_rows = conn.execute(
        "SELECT DISTINCT store FROM grocery_items WHERE household_id = ? AND store != ''", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    tagged = {r["store"] for r in tagged_rows}
    return len(usual | tagged) > 1


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


def set_grocery_item_store(item_id: int, store: str, remember: bool = True) -> dict:
    """
    Set which store a specific already-listed grocery item should be
    bought at — for assigning a store directly from a grocery list row
    (e.g. the Grocery List view's triage screen) rather than a general "we
    get X at Costco" chat mention (see set_item_store for that). By
    default, assigning a real (non-empty) store also remembers it as this
    item's usual store going forward — same underlying item_store_preferences
    row set_item_store writes — so the next time this item name is added to
    the list (a new week's plan, a chat mention, a manual add) it's already
    tagged to that store instead of landing back in the unsorted "to sort"
    queue. Picking "no particular store" (an empty store) never touches or
    clears an existing preference — that's a one-off skip, not a decision
    to forget where this item usually comes from. Pass remember=False to
    set just this one row without touching the remembered preference at
    all (used when re-displaying/correcting a row rather than the shopper
    actively choosing a store for it).
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID)
    ).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    conn.execute("UPDATE grocery_items SET store = ? WHERE id = ?", (store, item_id))
    remembered = False
    if store and remember:
        conn.execute(
            "INSERT INTO item_store_preferences (household_id, item, store) VALUES (?, ?, ?) "
            "ON CONFLICT(household_id, item) DO UPDATE SET store = excluded.store",
            (HOUSEHOLD_ID, row["item"].strip().lower(), store),
        )
        remembered = True
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "store": store, "found": True, "remembered": remembered}


def get_item_store_preferences() -> dict:
    """
    All remembered item->store associations (see set_item_store/
    set_grocery_item_store) as a flat {item_name_lowercase: store} map —
    powers the Grocery List view's "usually here" indicator, so a shopper
    can see at a glance which store assignments were auto-applied from
    memory (and weren't necessarily decided fresh this week) rather than
    treating every tagged item the same.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, store FROM item_store_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    return {r["item"]: r["store"] for r in rows}


_DEFAULT_AISLE_ORDER = ["Produce", "Bakery", "Dairy", "Meat", "Frozen", "Pantry", "Household"]


def get_stores() -> list[dict]:
    """
    Every store the household currently has grocery items assigned to
    (design_handoff_home_manager Phase 2, §8's left-rail "STORES" filter
    list), each with its real metadata row from the `stores` table when one
    exists (habit/role/aisle_order — see schema.sql), or just the default
    aisle order when it doesn't. A store only gets a real `stores` row once
    something sets its habit/role (Phase 4's Stores tab) — until then it's
    just a name the grocery list already knows about, listed here with
    defaults so the rail filter still works. Does NOT include "Unassigned"
    (storeId-null items) — that's a separate, always-present filter the UI
    adds itself, not a real store.
    """
    conn = get_conn()
    names = [
        r["store"] for r in conn.execute(
            "SELECT DISTINCT store FROM grocery_items WHERE household_id = ? AND store != '' AND status != 'removed'",
            (HOUSEHOLD_ID,),
        ).fetchall()
    ]
    meta_rows = conn.execute(
        "SELECT name, habit, role, aisle_order_json FROM stores WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    meta_by_name = {r["name"]: r for r in meta_rows}
    # Union in any store that has real metadata but currently has no items
    # on the list (e.g. between shopping trips) — it's still a store the
    # household shops at.
    for name in meta_by_name:
        if name not in names:
            names.append(name)
    return [
        {
            "name": name,
            "habit": meta_by_name[name]["habit"] if name in meta_by_name else "",
            "role": meta_by_name[name]["role"] if name in meta_by_name else "",
            "aisle_order": json.loads(meta_by_name[name]["aisle_order_json"]) if name in meta_by_name else list(_DEFAULT_AISLE_ORDER),
        }
        for name in names
    ]


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
        (HOUSEHOLD_ID,),
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


def close_shopping_trip(store: str, item_count: int = 0) -> dict:
    """
    Record that a shopping stop at `store` just wrapped up — desktop
    Shopping mode's (design_handoff_home_manager Phase 3, option 5g)
    "Done shopping" / "Next store" actions call this once per store as the
    household finishes there. Deliberately minimal (see schema.sql's
    comment on shopping_trips): per-item inventory promotion already
    happened when each item was marked purchased, so this is just a closed
    record of the stop, not another promotion pass. Nothing reads trip
    history back yet — this is forward-compatible bookkeeping.
    """
    conn = get_conn()
    conn.execute(
        "INSERT INTO shopping_trips (household_id, store, item_count) VALUES (?, ?, ?)",
        (HOUSEHOLD_ID, store, item_count),
    )
    conn.commit()
    conn.close()
    return {"store": store, "item_count": item_count}


# ---------- What we know: freeform facts (Phase 4, option 5d) ----------
# See schema.sql's comment on the `facts` table for why this is a separate
# layer from the structured meal_preferences/members fields.

def get_facts(category: str | None = None) -> list[dict]:
    """List household facts for the What We Know screen, optionally filtered to one category (people/taste/rhythm)."""
    conn = get_conn()
    if category:
        rows = conn.execute(
            "SELECT id, category, text, hard, author, updated_at FROM facts WHERE household_id = ? AND category = ? ORDER BY id ASC",
            (HOUSEHOLD_ID, category),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, category, text, hard, author, updated_at FROM facts WHERE household_id = ? ORDER BY category, id ASC",
            (HOUSEHOLD_ID,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_fact(category: str, text: str, hard: bool = False, author: str = "") -> dict:
    """Add one freeform fact to a What We Know tab. An empty/whitespace-only text is refused — the UI's own rule is that an abandoned empty fact never persists."""
    text = (text or "").strip()
    if not text:
        return {"added": False}
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO facts (household_id, category, text, hard, author) VALUES (?, ?, ?, ?, ?)",
        (HOUSEHOLD_ID, category, text, 1 if hard else 0, author),
    )
    conn.commit()
    fact_id = cur.lastrowid
    conn.close()
    return {"added": True, "id": fact_id, "category": category, "text": text, "hard": hard}


def update_fact(fact_id: int, text: str | None = None, hard: bool | None = None) -> dict:
    """Edit an existing fact's text and/or hard flag in place."""
    conn = get_conn()
    row = conn.execute("SELECT text, hard FROM facts WHERE id = ? AND household_id = ?", (fact_id, HOUSEHOLD_ID)).fetchone()
    if not row:
        conn.close()
        return {"id": fact_id, "found": False}
    new_text = text.strip() if text is not None else row["text"]
    new_hard = (1 if hard else 0) if hard is not None else row["hard"]
    conn.execute(
        "UPDATE facts SET text = ?, hard = ?, updated_at = datetime('now') WHERE id = ?",
        (new_text, new_hard, fact_id),
    )
    conn.commit()
    conn.close()
    return {"id": fact_id, "found": True, "text": new_text, "hard": bool(new_hard)}


def delete_fact(fact_id: int) -> dict:
    """Delete one fact outright."""
    conn = get_conn()
    conn.execute("DELETE FROM facts WHERE id = ? AND household_id = ?", (fact_id, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"id": fact_id, "deleted": True}


# ---------- Inventory item detail sheet (Phase 4, option 5a) ----------

_LEADING_NUM_RE = re.compile(r"^\s*([\d.]+)\s*(.*)$")


def _step_quantity_text(quantity: str, delta: float) -> str:
    """
    Nudge a freeform inventory quantity string by delta, e.g. "3 gal" -1 ->
    "2 gal". If there's no leading number to step (a bare descriptor like
    "some" or "a bag"), a '+' tap starts a count at 1 and keeps the
    descriptor as a suffix ("a bag" -> "1 a bag" is odd but rare — most
    inventory rows already carry a numeric quantity from the grocery
    checkoff or chat capture that created them); a '-' tap on a
    non-numeric quantity is a no-op. Floors at 0, never goes negative.
    """
    quantity = (quantity or "").strip()
    m = _LEADING_NUM_RE.match(quantity)
    if m and m.group(1):
        try:
            amount = float(m.group(1))
        except ValueError:
            amount = 0.0
        suffix = m.group(2).strip()
    else:
        if delta <= 0:
            return quantity  # nothing numeric to decrement
        amount = 0.0
        suffix = quantity
    amount = max(0.0, amount + delta)
    amount_str = f"{amount:g}"
    return f"{amount_str} {suffix}".strip() if suffix else amount_str


def step_inventory_quantity(item_id: int, delta: float) -> dict:
    """Nudge one inventory item's quantity by delta (e.g. +1/-1 from the item detail sheet's stepper)."""
    conn = get_conn()
    row = conn.execute("SELECT quantity FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID)).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    new_qty = _step_quantity_text(row["quantity"], delta)
    conn.execute("UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?", (new_qty, item_id))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "found": True, "quantity": new_qty}


def set_inventory_location(item_id: int, location: str) -> dict:
    """Move one inventory item to a different storage location (fridge/freezer/pantry) — re-groups it immediately."""
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET location = ?, updated_at = datetime('now') WHERE id = ? AND household_id = ?", (location, item_id, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "location": location}


def step_inventory_expiration(item_id: int, delta_days: int) -> dict:
    """Shift one inventory item's best-before date by delta_days (one tap = one day). Starts from today if the item has no date set yet."""
    conn = get_conn()
    row = conn.execute("SELECT expiration_date FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID)).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    base = row["expiration_date"]
    try:
        base_date = date.fromisoformat(base) if base else date.today()
    except ValueError:
        base_date = date.today()
    new_date = (base_date + timedelta(days=delta_days)).isoformat()
    conn.execute("UPDATE inventory_items SET expiration_date = ?, updated_at = datetime('now') WHERE id = ?", (new_date, item_id))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "found": True, "expiration_date": new_date}


# ---------- Notifications (Phase 5, NOTIFICATIONS.md) ----------
# Live, in-app "what needs your attention" feed — see schema.sql's comment
# on notification_dismissals for why this isn't real scheduled push.
# Covers 3 of the 4 spec'd types (dinner nudge, expiring soon, weekly plan
# ready); the 4th ("the other adult changed something") is not computed
# here — see README's Phase 5 notes for why.

def _dismissed_keys(conn) -> set:
    rows = conn.execute("SELECT key FROM notification_dismissals WHERE household_id = ?", (HOUSEHOLD_ID,)).fetchall()
    return {r["key"] for r in rows}


def get_active_notifications() -> list[dict]:
    """
    Compute the household's current notifications live (not scheduled —
    see schema.sql's notification_dismissals comment). Each has a stable
    `key` so dismissing one doesn't hide a future, different occurrence of
    the same type (e.g. dismissing today's dinner-gap nudge doesn't
    suppress tomorrow's). Powers the shell's notification bell.
    """
    conn = get_conn()
    dismissed = _dismissed_keys(conn)
    conn.close()
    out = []

    # 1. Dinner decision nudge (NOTIFICATIONS.md #1) — reuses the same
    # dinner-gap detection the Today needs-you band already uses, so the
    # notification and the band never disagree about what's open.
    for item in get_needs_you_items():
        if item.get("type") != "dinner_decision":
            continue
        key = f"dinner_gap:{item['date']}"
        if key in dismissed:
            continue
        day_label = "Tonight" if item["date"] == date.today().isoformat() else "tomorrow"
        first_option = (item.get("options") or [{}])[0].get("name") if item.get("options") else None
        out.append({
            "key": key, "type": "dinner_decision",
            "title": item["title"],
            "body": f"The quickest option is {first_option}." if first_option else "Nothing planned yet — take a look at tonight's options.",
            "tab": "today", "action_label": "Show options",
        })
        break

    # 2. Expiring soon (NOTIFICATIONS.md #2) — 2-day window per spec (the
    # Kitchen/Inventory badge itself uses 4 days; this notification is
    # deliberately tighter, matching the spec's own trigger).
    expiring = get_expiring_soon(days=2)
    if expiring:
        today_iso = date.today().isoformat()
        key = f"expiring:{today_iso}"
        if key not in dismissed:
            if len(expiring) == 1:
                it = expiring[0]
                title = f"{it['item']} needs using soon"
                body = f"{it['quantity']}. Want it in a dinner this week?" if it["quantity"] else "Want it in a dinner this week?"
            else:
                names = ", ".join(e["item"] for e in expiring[:3])
                title = f"{len(expiring)} things to use this week"
                body = f"{names} are all near their date."
            out.append({
                "key": key, "type": "expiring_soon", "title": title, "body": body,
                "href": "/inventory", "action_label": "Plan a meal with it",
            })

    # 3. Weekly plan ready (NOTIFICATIONS.md #3) — a plan for a week that
    # hasn't started yet, generated recently, with at least 2 dinners (the
    # spec's own "don't notify for an empty plan" suppression rule).
    conn = get_conn()
    plan_row = conn.execute(
        "SELECT id, week_start_date, created_at FROM weekly_plans WHERE household_id = ? AND week_start_date > ? ORDER BY created_at DESC LIMIT 1",
        (HOUSEHOLD_ID, date.today().isoformat()),
    ).fetchone()
    if plan_row:
        created = plan_row["created_at"]
        recent = False
        try:
            created_dt = datetime.fromisoformat(created.replace(" ", "T"))
            recent = (datetime.utcnow() - created_dt) <= timedelta(hours=24)
        except ValueError:
            recent = False
        if recent:
            dinner_count = conn.execute(
                "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner'",
                (plan_row["id"],),
            ).fetchone()["n"]
            key = f"weekly_plan:{plan_row['id']}"
            if dinner_count >= 2 and key not in dismissed:
                out.append({
                    "key": key, "type": "weekly_plan_ready",
                    "title": "Next week's plan is ready",
                    "body": f"{dinner_count} dinners planned.",
                    "tab": "week", "action_label": "Looks good",
                })

    # 4. The other adult settled the week (NOTIFICATIONS.md #4 — the type
    # that was previously left uncomputed for want of any per-adult
    # identity). Approval now records WHO said yes (weekly_plans.approved_by
    # — see design_handoff_plan_the_week), which is enough to make this
    # real. It is still household-wide rather than addressed to one person:
    # there is no per-adult session to deliver it to, so the honest version
    # is a shared "Emily approved it" the other adult sees when they next
    # open the app. That is exactly what the approved receipt's "{Other
    # adult} has been told the week is settled" is promising — so the
    # sentence describes something that actually happens.
    approved_row = conn.execute(
        "SELECT id, week_start_date, approved_by, approved_at FROM weekly_plans "
        "WHERE household_id = ? AND status = 'approved' AND TRIM(approved_by) != '' AND approved_at IS NOT NULL "
        "ORDER BY approved_at DESC LIMIT 1",
        (HOUSEHOLD_ID,),
    ).fetchone()
    if approved_row:
        # Keyed by plan id AND approval time, so reopening and re-approving
        # a week raises a fresh notification rather than being silenced by
        # the earlier approval's dismissal.
        key = f"week_approved:{approved_row['id']}:{approved_row['approved_at']}"
        recent = False
        try:
            approved_dt = datetime.fromisoformat(approved_row["approved_at"].replace(" ", "T"))
            recent = (datetime.utcnow() - approved_dt) <= timedelta(hours=48)
        except (ValueError, AttributeError):
            recent = False
        if recent and key not in dismissed:
            week_label = _format_week_range(approved_row["week_start_date"])
            out.append({
                "key": key, "type": "week_approved",
                "title": f"{approved_row['approved_by']} approved the week",
                "body": f"{week_label} is settled, and the shopping list is built.",
                "tab": "week", "action_label": "Take a look",
            })
    conn.close()
    return out


def dismiss_notification(key: str) -> dict:
    """Mark one notification key dismissed so it stops showing until its underlying condition changes (a new date/plan id)."""
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO notification_dismissals (household_id, key) VALUES (?, ?)",
        (HOUSEHOLD_ID, key),
    )
    conn.commit()
    conn.close()
    return {"key": key, "dismissed": True}


def get_grocery_already_have_items() -> list[dict]:
    """
    Cross-reference the 'needed' grocery list against tracked inventory to
    flag items that may not actually need buying — e.g. added ad hoc in
    chat before checking, or left over from before inventory caught up.
    Uses the same confident name-match logic as meal-plan ingredient
    auto-adding (get_inventory's items with a non-blank tracked quantity),
    not a guess. Only returns items not yet reviewed (see
    mark_grocery_item_already_have_reviewed) — once the shopper confirms
    they still need something, it drops out of this list for good (even
    though the inventory match still technically exists) rather than
    nagging about the same item every time. Powers the Grocery List view's
    "Already have this?" review section, which pulls these out of the
    normal To-buy list until reviewed.
    """
    needed = list_grocery_list(status="needed")
    if not needed:
        return []
    inventory = get_inventory()
    have_matches = []
    for it in needed:
        if it.get("already_have_reviewed"):
            continue
        match, confident = _find_inventory_match(it["item"], inventory)
        if not match or not confident:
            continue
        if not (match.get("quantity") or "").strip():
            continue  # tracked but with no quantity on hand isn't a confident "we have it"
        have_matches.append({
            "item_id": it["id"], "item": it["item"], "quantity": it["quantity"], "category": it["category"],
            "inventory_quantity": match["quantity"], "inventory_location": match.get("location", ""),
        })
    return have_matches


def get_pre_shop_flags() -> list[dict]:
    """
    Same confident inventory cross-reference as get_grocery_already_have_items,
    reshaped for the Grocery screen's pinned "Maybe already home" pre-shop
    check (PRE_SHOP_CHECK.md): a humanised, single-sentence comparison per
    item, computed here so the client never touches raw pack quantities —
    see _pre_shop_humanize_label, which is exactly where the old block's
    "1 stick + 1 stick" bug lived. An item whose wanted or on-hand amount
    can't be reduced to one confident phrase, or whose full sentence would
    run past ~60 characters, is left off entirely rather than shown
    garbled (PRE_SHOP_CHECK.md's "if it can't be said in one sentence,
    don't flag the item"). Also powers the /api/grocery-list and
    /api/grocery-list/by-store "needed" views' exclusion filter, so a
    flagged item never appears twice and never silently vanishes from
    both places at once.
    """
    needed = list_grocery_list(status="needed")
    if not needed:
        return []
    inventory = get_inventory()
    flags = []
    for it in needed:
        if it.get("already_have_reviewed"):
            continue
        match, confident = _find_inventory_match(it["item"], inventory)
        if not match or not confident:
            continue
        if not (match.get("quantity") or "").strip():
            continue  # tracked but with no quantity on hand isn't a confident "we have it"
        wanted_label = _pre_shop_humanize_label(it["quantity"])
        on_hand_label = _pre_shop_humanize_label(match["quantity"])
        if not wanted_label or not on_hand_label:
            continue
        sentence = f"You want {wanted_label}. Fridge shows {on_hand_label}."
        if len(sentence) > 60:
            continue
        flags.append({
            "itemId": it["id"],
            "name": it["item"],
            "wantedLabel": wanted_label,
            "onHandLabel": on_hand_label,
            "onHandLocation": match.get("location") or None,
            "sentence": sentence,
        })
    return flags


def drop_grocery_item_pre_shop(item_id: int, author: str = "") -> dict:
    """
    'Drop it' on a pre-shop flag — soft-removes the item (status:
    'removed') rather than deleting it outright, so undo_pre_shop_drop can
    restore it and so a pattern of repeat drops stays around for the
    assistant to learn from later (same reasoning as
    exclude_grocery_item/move_grocery_item_to_inventory's soft-delete
    philosophy — see DATA_AND_API.md's "Sync between the two adults").
    Idempotent: dropping an already-removed item is a no-op. `author` is
    recorded but doesn't yet drive a live cross-device "adult changed
    something" notification — NOTIFICATIONS.md #4 and README's Phase 5
    notes document that this codebase has no concept of "the other adult"
    distinct from "you" at the data layer, and that gap applies here too.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = ? "
        "WHERE id = ? AND household_id = ? AND status != 'removed'",
        (author or "", item_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "status": "removed"}


def undo_pre_shop_drop(item_id: int) -> dict:
    """
    Undo a pre-shop 'Drop it' — restores the item to 'needed' and marks it
    already_have_reviewed so it goes straight back to its store card
    without being re-flagged this same trip (PRE_SHOP_CHECK.md: undo
    "does not re-add the flag this trip").
    """
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET status = 'needed', already_have_reviewed = 1 "
        "WHERE id = ? AND household_id = ?",
        (item_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "status": "needed"}


def keep_all_pre_shop_flags() -> dict:
    """'Keep all {n}' — resolves every currently flagged pre-shop item as keep, in one write."""
    flags = get_pre_shop_flags()
    conn = get_conn()
    for f in flags:
        conn.execute(
            "UPDATE grocery_items SET already_have_reviewed = 1 WHERE id = ? AND household_id = ?",
            (f["itemId"], HOUSEHOLD_ID),
        )
    conn.commit()
    conn.close()
    return {"resolved_count": len(flags)}


def mark_grocery_item_already_have_reviewed(item_id: int) -> dict:
    """
    Confirm an item flagged by get_grocery_already_have_items is still
    needed despite the inventory match (e.g. running low) — moves it back
    into the normal To-buy list and stops it from being flagged again for
    this same listing. Does not touch quantity/status; only clears the flag.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET already_have_reviewed = 1 WHERE id = ? AND household_id = ?",
        (item_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "already_have_reviewed": True}


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
    return {"token": token, "link": _absolute_url(f"/share/{token}")}


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


# ---------- Eater self-service (Phase 4, §4.1 new capability) ----------
# A personal, tokenized, limited-write link tied to one members row, so a
# household member other than the Planner can register their own dietary
# restrictions and feedback directly, without full account creation or
# relaying it secondhand through the Planner. Modeled on the read-only plan
# share link above, but scoped to a single person and able to write.

def get_or_create_member_share_link(member_name: str) -> dict:
    """
    Get (or create on first use) a standing, personal share link for one
    household member — lets them view/add their own dietary restrictions
    and leave feedback notes directly, without going through the Planner.
    Raises ValueError if no member with this name exists yet (add them with
    add_member or set_member_dietary_restrictions first). If a non-revoked
    link already exists for this person, returns the same one rather than
    creating a duplicate — use regenerate_member_share_link to force a new
    token instead. Returns a `link` field with the actual, absolute URL —
    share that value verbatim, never construct or guess a URL yourself.
    """
    conn = get_conn()
    member = conn.execute(
        "SELECT id, name FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (HOUSEHOLD_ID, member_name),
    ).fetchone()
    if not member:
        conn.close()
        raise ValueError(f"No household member named '{member_name}' yet.")
    row = conn.execute(
        "SELECT token FROM member_share_links WHERE household_id = ? AND member_id = ? AND revoked = 0 "
        "ORDER BY created_at DESC LIMIT 1",
        (HOUSEHOLD_ID, member["id"]),
    ).fetchone()
    if row:
        token = row["token"]
    else:
        token = secrets.token_urlsafe(16)
        conn.execute(
            "INSERT INTO member_share_links (household_id, member_id, token) VALUES (?, ?, ?)",
            (HOUSEHOLD_ID, member["id"], token),
        )
        conn.commit()
    conn.close()
    return {"member_name": member["name"], "token": token, "link": _absolute_url(f"/member-share/{token}")}


def revoke_member_share_link(member_name: str) -> dict:
    """Revoke a household member's self-service link — any existing link stops working immediately. Use before regenerate_member_share_link, or on its own if the link should just be shut off (e.g. it was shared by mistake)."""
    conn = get_conn()
    member = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (HOUSEHOLD_ID, member_name),
    ).fetchone()
    if not member:
        conn.close()
        raise ValueError(f"No household member named '{member_name}'.")
    conn.execute(
        "UPDATE member_share_links SET revoked = 1 WHERE household_id = ? AND member_id = ? AND revoked = 0",
        (HOUSEHOLD_ID, member["id"]),
    )
    conn.commit()
    conn.close()
    return {"member_name": member_name, "revoked": True}


def regenerate_member_share_link(member_name: str) -> dict:
    """Revoke a household member's current self-service link (if any) and issue a fresh one in the same call — use if a link may have leaked, or the Planner just wants a clean new one."""
    revoke_member_share_link(member_name)
    return get_or_create_member_share_link(member_name)


def resolve_member_share_link(token: str) -> dict | None:
    """
    Resolve a member self-service token to that person's own view: their
    name, current dietary restrictions, and any notes they've left before.
    Returns None for an invalid or revoked token, so the caller can 404
    rather than leak whether a token almost matched. Only this one member's
    own data is exposed through this path — nothing else about the
    household.
    """
    conn = get_conn()
    link = conn.execute(
        "SELECT member_id FROM member_share_links WHERE token = ? AND revoked = 0", (token,)
    ).fetchone()
    if not link:
        conn.close()
        return None
    member = conn.execute(
        "SELECT name, dietary_restrictions_json FROM members WHERE id = ?", (link["member_id"],)
    ).fetchone()
    if not member:
        conn.close()
        return None
    notes = conn.execute(
        "SELECT note, created_at FROM member_notes WHERE member_id = ? ORDER BY created_at DESC LIMIT 10",
        (link["member_id"],),
    ).fetchall()
    conn.close()
    return {
        "member_name": member["name"],
        "dietary_restrictions": json.loads(member["dietary_restrictions_json"]),
        "notes": [{"note": n["note"], "created_at": n["created_at"]} for n in notes],
    }


def eater_add_dietary_restriction(token: str, restrictions: list[str]) -> dict:
    """
    Add dietary restriction(s) for the member behind this self-service
    token — merges with whatever they already have (never a destructive
    replace, since this is a one-off self-edit, not a full-list
    resubmission). Raises ValueError for an invalid/revoked token.
    """
    conn = get_conn()
    link = conn.execute(
        "SELECT member_id FROM member_share_links WHERE token = ? AND revoked = 0", (token,)
    ).fetchone()
    if not link:
        conn.close()
        raise ValueError("This link isn't valid.")
    member = conn.execute("SELECT name FROM members WHERE id = ?", (link["member_id"],)).fetchone()
    conn.close()
    if not member:
        raise ValueError("This link isn't valid.")
    return set_member_dietary_restrictions(member["name"], restrictions)


def eater_add_note(token: str, note: str) -> dict:
    """Leave a freeform preference/feedback note as the member behind this self-service token. Raises ValueError for an invalid/revoked token."""
    conn = get_conn()
    link = conn.execute(
        "SELECT member_id FROM member_share_links WHERE token = ? AND revoked = 0", (token,)
    ).fetchone()
    if not link:
        conn.close()
        raise ValueError("This link isn't valid.")
    conn.execute(
        "INSERT INTO member_notes (household_id, member_id, note) VALUES (?, ?, ?)",
        (HOUSEHOLD_ID, link["member_id"], note),
    )
    conn.commit()
    conn.close()
    return {"saved": True}


def get_member_notes(member_name: str | None = None) -> list[dict]:
    """
    List freeform notes members have left via their self-service links —
    use this when the Planner asks what an Eater has said, or as part of
    reviewing feedback generally. Omit member_name for every member's
    notes, most recent first.
    """
    conn = get_conn()
    if member_name:
        rows = conn.execute(
            "SELECT m.name, n.note, n.created_at FROM member_notes n "
            "JOIN members m ON m.id = n.member_id "
            "WHERE n.household_id = ? AND LOWER(m.name) = LOWER(?) ORDER BY n.created_at DESC",
            (HOUSEHOLD_ID, member_name),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.name, n.note, n.created_at FROM member_notes n "
            "JOIN members m ON m.id = n.member_id "
            "WHERE n.household_id = ? ORDER BY n.created_at DESC",
            (HOUSEHOLD_ID,),
        ).fetchall()
    conn.close()
    return [{"member_name": r["name"], "note": r["note"], "created_at": r["created_at"]} for r in rows]


# ---------- Household memory (transparency & correction) ----------

def get_household_memory() -> dict:
    """
    Return a plain summary of everything the app has learned/saved about
    this household: each member's dietary restrictions, favorite proteins
    and cuisines, standing dislikes, cooking-time preference, freeform
    notes, and household goals. Powers a "what the app knows" view the user
    can review and correct directly (see edit_preference/delete_preference),
    and is useful context to pull before discussing or generating meal
    plans. Also includes growth_count_this_month — how many preference
    writes (create/update/delete) have happened this calendar month, per
    the append-only preference_events log — for the "what we know" view's
    "you've taught me N things this month" counter. cooking_time_insight
    and recipe_variety_insight pair the cooking-time and recipe-variety
    settings with an actual read on this month's plans (average prep+cook
    minutes, new-vs-repeat recipe count) rather than leaving them as plain
    settings with no feedback loop — either is None if there's not yet
    enough data this month to say anything meaningful.
    """
    conn = get_conn()
    prefs = conn.execute("SELECT * FROM meal_preferences WHERE household_id = ?", (HOUSEHOLD_ID,)).fetchone()
    members = conn.execute(
        "SELECT name, age_group, dietary_restrictions_json FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    household = conn.execute("SELECT goals FROM households WHERE id = ?", (HOUSEHOLD_ID,)).fetchone()

    # Cooking-time insight: average actual prep+cook time across this
    # month's planned meals (recipe-backed entries only, and only where
    # both times are actually known) — pairs the editable
    # cooking_time_preference setting with a real read on whether plans are
    # actually landing near it, instead of it just being a form field with
    # no feedback loop.
    time_row = conn.execute(
        """
        SELECT AVG(r.prep_time_minutes + r.cook_time_minutes) AS avg_minutes, COUNT(*) AS n
        FROM meal_plan_entries e JOIN recipes r ON r.id = e.recipe_id
        WHERE e.household_id = ? AND strftime('%Y-%m', e.created_at) = strftime('%Y-%m', 'now')
          AND r.prep_time_minutes IS NOT NULL AND r.cook_time_minutes IS NOT NULL
        """,
        (HOUSEHOLD_ID,),
    ).fetchone()
    cooking_time_insight = (
        {"avg_minutes": round(time_row["avg_minutes"]), "meal_count": time_row["n"]}
        if time_row and time_row["n"] else None
    )

    # Recipe-variety insight: of this month's planned meals, how many used
    # a recipe first created this month (a "new" recipe surfacing, same
    # idea novelty_preference controls) vs. one that already existed before
    # this month (a repeat/favorite) — same pairing idea as above.
    variety_row = conn.execute(
        """
        SELECT
          SUM(CASE WHEN strftime('%Y-%m', r.created_at) = strftime('%Y-%m', 'now') THEN 1 ELSE 0 END) AS new_count,
          SUM(CASE WHEN strftime('%Y-%m', r.created_at) != strftime('%Y-%m', 'now') THEN 1 ELSE 0 END) AS repeat_count
        FROM meal_plan_entries e JOIN recipes r ON r.id = e.recipe_id
        WHERE e.household_id = ? AND strftime('%Y-%m', e.created_at) = strftime('%Y-%m', 'now')
        """,
        (HOUSEHOLD_ID,),
    ).fetchone()
    total_variety = (variety_row["new_count"] or 0) + (variety_row["repeat_count"] or 0) if variety_row else 0
    recipe_variety_insight = (
        {"new_count": variety_row["new_count"] or 0, "repeat_count": variety_row["repeat_count"] or 0}
        if total_variety else None
    )

    # Context completeness: a plain read on whether there's actually enough
    # signal for recommendations to be personalized yet, vs. still mostly
    # running on defaults — see get_context_completeness for the full
    # weighting rationale. Computed on this same connection/snapshot of
    # members/prefs rather than re-querying, plus two extra counts
    # (recipes rated, meals actually cooked) that aren't part of the
    # regular memory payload.
    recipes_rated_row = conn.execute(
        "SELECT COUNT(*) AS c FROM recipes WHERE household_id = ? AND rating IN ('liked', 'disliked')",
        (HOUSEHOLD_ID,),
    ).fetchone()
    meals_cooked_row = conn.execute(
        "SELECT COUNT(*) AS c FROM meal_plan_entries WHERE household_id = ? AND cooked_status = 'done'",
        (HOUSEHOLD_ID,),
    ).fetchone()
    conn.close()

    member_list = [
        {
            "name": m["name"], "age_group": m["age_group"],
            "dietary_restrictions": json.loads(m["dietary_restrictions_json"]),
        }
        for m in members
    ]
    protein_prefs = json.loads(prefs["protein_preferences_json"]) if prefs else {}
    cuisine_prefs = json.loads(prefs["cuisine_preferences_json"]) if prefs else []
    dislikes = json.loads(prefs["dislikes_json"]) if prefs else []
    cooking_time_pref = prefs["cooking_time_preference"] if prefs else ""
    usual_stores = json.loads(prefs["usual_stores_json"]) if prefs else []
    eating_style = prefs["eating_style"] if prefs else ""
    goals = household["goals"] if household else ""

    context_completeness = _build_context_completeness(
        members=member_list, protein_preferences=protein_prefs, cuisine_preferences=cuisine_prefs,
        dislikes=dislikes, cooking_time_preference=cooking_time_pref, usual_stores=usual_stores,
        eating_style=eating_style, goals=goals,
        recipes_rated=recipes_rated_row["c"] if recipes_rated_row else 0,
        meals_cooked=meals_cooked_row["c"] if meals_cooked_row else 0,
    )

    return {
        "members": member_list,
        "goals": goals,
        "notes": prefs["notes"] if prefs else "",
        "protein_preferences": protein_prefs,
        "cuisine_preferences": cuisine_prefs,
        "dislikes": dislikes,
        "cooking_time_preference": cooking_time_pref,
        "cooking_time_insight": cooking_time_insight,
        "novelty_preference": prefs["novelty_preference"] if prefs else "balanced",
        "recipe_variety_insight": recipe_variety_insight,
        "planning_mode": prefs["planning_mode"] if prefs else "day_based",
        "usual_stores": usual_stores,
        "store_typical_items": json.loads(prefs["store_typical_items_json"]) if prefs else {},
        "eating_style": eating_style,
        "dinners_per_week": prefs["dinners_per_week"] if prefs else 7,
        "breakfasts_per_week": prefs["breakfasts_per_week"] if prefs else 7,
        "lunches_per_week": prefs["lunches_per_week"] if prefs else 7,
        # design_handoff_plan_the_week. kitchen_kit is the highest-value
        # constraint the app wasn't collecting — it stops impossible
        # suggestions outright rather than filtering them afterwards. And
        # repeats_tolerance changes the structure of every week built:
        # whether to cook once and stretch it, or give seven different
        # dinners.
        "kitchen_kit": json.loads(prefs["kitchen_kit_json"]) if prefs else [],
        "repeats_tolerance": prefs["repeats_tolerance"] if prefs else "",
        "weeknight_max_minutes": prefs["weeknight_max_minutes"] if prefs else 0,
        "table_style": prefs["table_style"] if prefs else "",
        "typical_week": prefs["typical_week"] if prefs else "",
        "growth_count_this_month": count_preference_events_this_month(),
        "context_completeness": context_completeness,
    }


# Weight of each context signal toward the overall completeness score
# (out of 100-ish — doesn't need to sum exactly, the score is earned/total).
# Ordered roughly by how much it actually improves recommendation quality:
# real taste signal (ratings, protein/cuisine likes) outweighs one-time
# setup fields, and actual usage (cooked meals) outweighs stated intent.
_CONTEXT_SIGNALS = [
    ("members", "Add your household's members", "So dietary restrictions and portions can be personalized per person, not guessed at.", 10),
    ("dietary_restrictions", "Note any dietary restrictions or allergies", "The single most important thing to get right before I suggest a week of meals.", 15),
    ("protein_preferences", "Rate a few proteins you like or don't", "Helps me actually favor what your household enjoys instead of rotating blindly.", 10),
    ("cuisine_preferences", "Tell me a few cuisines you're into", "Keeps suggestions feeling like your food, not a generic rotation.", 10),
    ("dislikes", "Mention any standing dislikes", "So I stop suggesting the same thing you keep passing on.", 5),
    ("cooking_time_preference", "Set a cooking time preference", "Keeps weeknight suggestions realistic for how much time you actually have.", 10),
    ("usual_stores", "Add the store(s) you usually shop at", "Powers store-aware grocery lists and shopping-trip planning.", 10),
    ("eating_style", "Tell me about your overall eating style", "e.g. vegetarian, keto, low-carb — shapes every suggestion, not just individual meals.", 10),
    ("goals", "Share any household goals", "e.g. eating healthier, saving money, more variety — gives me something to optimize toward.", 5),
    ("recipes_rated", "Rate a few recipes after cooking them", "The strongest signal I get — real reactions beat stated preferences every time.", 15),
    ("meals_cooked", "Cook a few planned meals and check them off", "Shows me the plan is actually being used, not just generated and ignored.", 15),
]


def _build_context_completeness(
    *, members, protein_preferences, cuisine_preferences, dislikes, cooking_time_preference,
    usual_stores, eating_style, goals, recipes_rated, meals_cooked,
) -> dict:
    """
    Turn the household's current signals into a plain "how well do I
    actually know this household yet" read — a weighted checklist (see
    _CONTEXT_SIGNALS) rolled up into a 0-100 score and a named tier, plus
    the specific highest-value gaps still open. This is intentionally a
    simplification: an empty dislikes list, for instance, could mean
    "nothing to report" or "never asked" — there's no way to tell the
    difference from stored data alone, so an unset/empty signal always
    reads as "not yet captured" even if the true answer really is "none."
    Powers the What We Know view's completeness card.
    """
    has_dietary_note = any(m["dietary_restrictions"] for m in members)
    done_map = {
        "members": len(members) > 0,
        "dietary_restrictions": has_dietary_note,
        "protein_preferences": len(protein_preferences) > 0,
        "cuisine_preferences": len(cuisine_preferences) > 0,
        "dislikes": len(dislikes) > 0,
        "cooking_time_preference": bool(cooking_time_preference),
        "usual_stores": len(usual_stores) > 0,
        "eating_style": bool(eating_style),
        "goals": bool(goals),
        "recipes_rated": recipes_rated >= 3,
        "meals_cooked": meals_cooked >= 3,
    }

    earned = sum(weight for key, _, _, weight in _CONTEXT_SIGNALS if done_map[key])
    total = sum(weight for _, _, _, weight in _CONTEXT_SIGNALS)
    score = round(100 * earned / total) if total else 0

    if score >= 80:
        tier, tier_label, tier_blurb = "dialed_in", "Dialed in", (
            "I've got a strong read on your household — recommendations should feel personalized and on-target."
        )
    elif score >= 50:
        tier, tier_label, tier_blurb = "know_your_household", "Know your household", (
            "Recommendations are starting to reflect real preferences, not just generic defaults."
        )
    elif score >= 25:
        tier, tier_label, tier_blurb = "getting_acquainted", "Getting acquainted", (
            "I've got the basics, but there's still a lot of guessing happening under the hood."
        )
    else:
        tier, tier_label, tier_blurb = "just_met", "Just met", (
            "I'm working from defaults right now — plans will be pretty generic until I know more about your household."
        )

    missing = sorted(
        (
            {"key": key, "label": label, "why": why, "weight": weight}
            for key, label, why, weight in _CONTEXT_SIGNALS
            if not done_map[key]
        ),
        key=lambda item: -item["weight"],
    )

    return {
        "score": score,
        "tier": tier,
        "tier_label": tier_label,
        "tier_blurb": tier_blurb,
        "missing": missing,
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
    'protein_preferences' (dict of protein -> 1-5 like rating, e.g. {"chicken":
    5} — merged into existing), 'novelty_preference'
    (str: 'mostly_favorites', 'balanced', or 'surprise_me_often' — how often
    new recipes get surfaced when generating a weekly plan), 'usual_stores'
    (list of str — the stores/chains this household usually shops at, e.g.
    ["Trader Joe's", "Costco"] — replaces the whole list; used to populate
    store suggestions in the grocery list view), 'eating_style' (str,
    freeform — a diet/eating style the household's meals should follow,
    e.g. "keto" or "high-protein, low-carb"; distinct from hard dietary
    restrictions), 'dinners_per_week'/'breakfasts_per_week'/'lunches_per_week'
    (each int, 0-7 — how many DISTINCT meals of that kind a typical week
    should plan, spread across the seven days; 0 means "none, thanks" and
    leaves that meal unplanned all week), 'kitchen_kit' (list of str — what
    the household has to cook with, e.g. ["slow_cooker", "air_fryer"];
    recipes are limited to what their kitchen can actually make),
    'repeats_tolerance' (str: 'cook_once_eat_twice', 'one_a_week' or
    'all_different' — this one changes the shape of every week built),
    'weeknight_max_minutes' (int — a real cap on Mon-Fri dinners; 0 means no
    cap), 'table_style' (str), 'typical_week'/'next_week_notes' (str,
    freeform, kept in the household's own words). To remove a
    single item from a list rather than replacing
    it wholesale, use delete_preference instead.
    """
    # Straightforward column writes with no merging or special casing —
    # a table rather than another chain of ifs alongside the ones below.
    simple_text_columns = {
        "repeats_tolerance": "repeats_tolerance",
        "table_style": "table_style",
        "typical_week": "typical_week",
        "next_week_notes": "next_week_notes",
    }
    valid_fields = {
        "notes", "cooking_time_preference", "cuisine_preferences", "protein_preferences",
        "dislikes", "novelty_preference", "usual_stores", "eating_style",
        "dinners_per_week", "breakfasts_per_week", "lunches_per_week",
        "kitchen_kit", "weeknight_max_minutes", *simple_text_columns,
    }
    if field not in valid_fields:
        raise ValueError(f"Unknown preference field '{field}'. Valid fields: {sorted(valid_fields)}")

    # Validated rather than trusted, because nothing else enforces the range
    # and it genuinely matters: the floor is 0, not 1 — "none, thanks" is a
    # real answer to the setup screen's stepper — and a value above 7 would
    # be a count of distinct meals larger than the week itself.
    if field in ("dinners_per_week", "breakfasts_per_week", "lunches_per_week"):
        try:
            count = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field} must be a whole number from 0 to 7.")
        if not 0 <= count <= 7:
            raise ValueError(f"{field} must be from 0 to 7, not {count}.")
        value = count
    if field == "weeknight_max_minutes":
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            raise ValueError("weeknight_max_minutes must be a whole number of minutes (0 for no cap).")
        if minutes < 0:
            raise ValueError("weeknight_max_minutes can't be negative.")
        value = minutes
    if field == "repeats_tolerance" and value not in ("", "cook_once_eat_twice", "one_a_week", "all_different"):
        raise ValueError("repeats_tolerance must be 'cook_once_eat_twice', 'one_a_week' or 'all_different'.")

    _log_preference_event(field, "write")
    if field in simple_text_columns or field in ("kitchen_kit", "weeknight_max_minutes"):
        column = {
            "kitchen_kit": "kitchen_kit_json",
            "weeknight_max_minutes": "weeknight_max_minutes",
            **simple_text_columns,
        }[field]
        stored = json.dumps(value) if field == "kitchen_kit" else value
        conn = get_conn()
        conn.execute(
            f"INSERT INTO meal_preferences (household_id, {column}, updated_at) "
            f"VALUES (?, ?, datetime('now')) "
            f"ON CONFLICT(household_id) DO UPDATE SET {column} = excluded.{column}, updated_at = datetime('now')",
            (HOUSEHOLD_ID, stored),
        )
        conn.commit()
        conn.close()
        return {field: value}
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
    if field == "usual_stores":
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO meal_preferences (household_id, usual_stores_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(household_id) DO UPDATE SET usual_stores_json = excluded.usual_stores_json, updated_at = datetime('now')
            """,
            (HOUSEHOLD_ID, json.dumps(value)),
        )
        conn.commit()
        conn.close()
        return {"usual_stores": value}
    if field == "cuisine_preferences":
        return set_household_meal_preferences(cuisine_preferences=value, mark_complete=False)
    if field == "protein_preferences":
        return set_household_meal_preferences(protein_preferences=value, mark_complete=False)
    if field == "notes":
        return set_household_meal_preferences(notes=value, mark_complete=False)
    if field == "novelty_preference":
        return set_household_meal_preferences(novelty_preference=value, mark_complete=False)
    if field == "eating_style":
        return set_household_meal_preferences(eating_style=value, mark_complete=False)
    if field == "dinners_per_week":
        return set_household_meal_preferences(dinners_per_week=int(value), mark_complete=False)
    if field == "breakfasts_per_week":
        return set_household_meal_preferences(breakfasts_per_week=int(value), mark_complete=False)
    if field == "lunches_per_week":
        return set_household_meal_preferences(lunches_per_week=int(value), mark_complete=False)
    return set_household_meal_preferences(cooking_time_preference=value, mark_complete=False)


def delete_preference(field: str, item: str | None = None) -> dict:
    """
    Remove a remembered preference. For list fields ('dislikes',
    'cuisine_preferences', or 'usual_stores'), pass item = the specific
    value to remove (case-insensitive match). For 'protein_preferences',
    item = the protein name to forget. For scalar fields ('notes',
    'cooking_time_preference', or 'eating_style'), omit item to clear the
    field entirely. 'dinners_per_week'/'breakfasts_per_week'/'lunches_per_week'
    each reset to the default of 7.
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
    elif field == "usual_stores":
        updated = [s for s in json.loads(existing["usual_stores_json"]) if s.lower() != (item or "").lower()]
        # Drop that store's typical-items list along with it — an orphaned
        # entry would keep surfacing "usually get here" suggestions in the
        # grocery list for a store the household no longer shops at.
        store_items = json.loads(existing["store_typical_items_json"])
        store_items = {k: v for k, v in store_items.items() if k.lower() != (item or "").lower()}
        conn.execute(
            "UPDATE meal_preferences SET usual_stores_json = ?, store_typical_items_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(updated), json.dumps(store_items), HOUSEHOLD_ID),
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
    elif field == "eating_style":
        conn.execute(
            "UPDATE meal_preferences SET eating_style = '', updated_at = datetime('now') WHERE household_id = ?",
            (HOUSEHOLD_ID,),
        )
    elif field == "dinners_per_week":
        conn.execute(
            "UPDATE meal_preferences SET dinners_per_week = 7, updated_at = datetime('now') WHERE household_id = ?",
            (HOUSEHOLD_ID,),
        )
    elif field == "breakfasts_per_week":
        conn.execute(
            "UPDATE meal_preferences SET breakfasts_per_week = 7, updated_at = datetime('now') WHERE household_id = ?",
            (HOUSEHOLD_ID,),
        )
    elif field == "lunches_per_week":
        conn.execute(
            "UPDATE meal_preferences SET lunches_per_week = 7, updated_at = datetime('now') WHERE household_id = ?",
            (HOUSEHOLD_ID,),
        )
    else:
        conn.close()
        raise ValueError(f"Unknown preference field '{field}'.")
    conn.commit()
    conn.close()
    _log_preference_event(field, "delete")
    return {"field": field, "item": item, "deleted": True}


# ---------- Grocery list ----------

# Standard store sections, in a sensible shopping order. "meat" is an alias
# kept for rows saved before "meat/seafood" was standardized.
_GROCERY_SECTION_ORDER = ["produce", "dairy", "meat/seafood", "pantry", "frozen", "other"]
_GROCERY_CATEGORY_ALIASES = {"meat": "meat/seafood", "seafood": "meat/seafood"}

# Storage location — independent from category/food-type, since an item's
# physical location can diverge from its grocery-aisle category (an opened
# sauce is category='pantry' by food type but lives in the fridge once
# opened). Falls back to a category-based guess when not stated explicitly.
_LOCATION_ORDER = ["fridge", "freezer", "pantry"]
_DEFAULT_LOCATION_BY_CATEGORY = {
    "produce": "fridge",
    "dairy": "fridge",
    "meat/seafood": "fridge",
    "frozen": "freezer",
    "pantry": "pantry",
    "other": "pantry",
}


def _resolve_location(explicit_location: str | None, category: str | None) -> str:
    if explicit_location:
        return explicit_location
    return _DEFAULT_LOCATION_BY_CATEGORY.get(category or "other", "pantry")


def _display_location(item: dict) -> str:
    """An item's location, falling back to the category-based default for legacy rows saved before location was tracked."""
    return item.get("location") or _DEFAULT_LOCATION_BY_CATEGORY.get(item.get("category"), "pantry")

# Phase 4, §4.2: default shelf life (days) by broad category, used only as
# a fallback when an item isn't found in _ITEM_SHELF_LIFE_DAYS below.
# Deliberately a plain code constant, not a DB table/setting — the PRD
# calls for global defaults only this phase, not household-customizable,
# consistent with not building a tunable setting before there's dogfooding
# data to justify it. Rough, conservative estimates; an explicit
# expiration_date (from the user, or a receipt/photo scan) always wins.
_DEFAULT_SHELF_LIFE_DAYS = {
    "produce": 7,
    "dairy": 10,
    "meat/seafood": 3,
    "pantry": 180,
    "frozen": 90,
    "other": 14,
}

# Item-level shelf life (days), refrigerated/pantry as typical for that
# item, adapted from general USDA FoodKeeper / FDA freshness guidance —
# still rough rule-of-thumb estimates, not a live lookup against any
# database, and always overridden by an explicit expiration_date. Keys are
# matched as substrings against the (lowercased) item name, longest key
# first, so "sweet potato" matches before the more generic "potato". Falls
# back to the category-level default in _DEFAULT_SHELF_LIFE_DAYS when no
# item key matches.
_ITEM_SHELF_LIFE_DAYS = {
    # Dairy
    "milk": 7, "buttermilk": 14, "yogurt": 14, "sour cream": 14,
    "heavy cream": 10, "half and half": 7, "cream cheese": 14,
    "feta": 30, "mozzarella": 14, "burrata": 3, "parmesan": 60,
    "cheddar": 30, "cheese": 21, "butter": 90, "eggs": 21, "egg": 21,
    # Produce
    "lettuce": 7, "spinach": 5, "arugula": 5, "kale": 7, "salad mix": 5,
    "greens": 5, "berries": 5, "strawberr": 5, "raspberr": 4,
    "blueberr": 10, "blackberr": 4, "banana": 5, "apple": 21,
    "avocado": 5, "tomato": 7, "cucumber": 7, "zucchini": 7,
    "broccoli": 7, "cauliflower": 10, "carrot": 21, "celery": 14,
    "bell pepper": 10, "pepper": 10, "mushroom": 5, "onion": 30,
    "garlic": 30, "ginger": 21, "sweet potato": 21, "potato": 30,
    "lemon": 21, "lime": 21, "cilantro": 5, "parsley": 7, "basil": 5,
    "mint": 5, "asparagus": 4,
    # Meat / seafood
    "ground beef": 2, "ground turkey": 2, "ground pork": 2,
    "chicken": 2, "turkey": 2, "steak": 3, "pork": 3, "beef": 3,
    "salmon": 2, "shrimp": 2, "fish": 2, "seafood": 2, "bacon": 7,
    "sausage": 5, "deli meat": 5, "ham": 5,
    # Pantry (longer-lived; category default of 180 already covers most)
    "bread": 7, "tortilla": 14,
}


def _lookup_item_shelf_life_days(item: str, category: str | None) -> int:
    name = item.strip().lower()
    best_match: tuple[str, int] | None = None
    for key, days in _ITEM_SHELF_LIFE_DAYS.items():
        if key in name and (best_match is None or len(key) > len(best_match[0])):
            best_match = (key, days)
    if best_match:
        return best_match[1]
    return _DEFAULT_SHELF_LIFE_DAYS.get(category, _DEFAULT_SHELF_LIFE_DAYS["other"])


def _estimate_expiration_date(category: str, item: str = "", from_date: date | None = None) -> str:
    """ISO date estimate for when this item likely goes bad, starting from today (or from_date). Checks _ITEM_SHELF_LIFE_DAYS for an item-specific estimate first, falling back to the category-level default in _DEFAULT_SHELF_LIFE_DAYS."""
    days = _lookup_item_shelf_life_days(item, category)
    base = from_date or date.today()
    return (base + timedelta(days=days)).isoformat()


def _resolved_expiration_update(
    explicit_expiration_date: str | None,
    new_category: str | None,
    existing_category: str | None,
    existing_expiration_date: str | None,
    item: str = "",
) -> str | None:
    """
    Work out what (if anything) an inventory write should set
    expiration_date to, without ever clobbering something more specific
    than what's being provided now:
      - An explicit date always wins outright.
      - Otherwise, estimate/re-estimate only if there's no date yet, or the
        only date on file was itself a guess from the generic 'other'
        bucket and a real category is now known — refining an unknown-item
        placeholder, not overwriting a specific-category estimate.
      - Returns None when nothing should change.
    """
    if explicit_expiration_date:
        return explicit_expiration_date
    effective_category = new_category or existing_category
    if not effective_category:
        return None
    if not existing_expiration_date:
        return _estimate_expiration_date(effective_category, item)
    if existing_category == "other" and new_category and new_category != "other":
        return _estimate_expiration_date(new_category, item)
    return None

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
    # Size descriptors ("1 large" onion) aren't real units of measure — map
    # them to "" (normalized to None below) so "1 large" and "1/2" (no
    # unit) are recognized as the same kind of quantity and merge into a
    # single count instead of falling through to literal "1 large + 1/2"
    # concatenation. See _try_consolidate_quantity / the grocery-quantity
    # bug this fixes (onion showing as "1 large + 1/2" instead of "2").
    "large": "", "medium": "", "small": "", "whole": "", "jumbo": "", "xl": "",
}

# Package/container words that can appear as a unit on their own ("1 head")
# or as the second word of a compound unit ("1 lb bag"). Singular is the
# canonical parsed form; _format_quantity pluralizes only for display
# ("2 lb bags"), and _normalize_container_word below undoes that pluralization
# on the way back in — otherwise re-parsing an already-merged "2 lb bags" to
# add a third "1 lb bag" would see "bags" != "bag" and fail to match, falling
# back to concatenation again (the exact bug this whole fix is for).
_CONTAINER_UNIT_PLURALS = {
    "bag": "bags", "box": "boxes", "can": "cans", "jar": "jars",
    "bottle": "bottles", "block": "blocks", "bunch": "bunches",
    "head": "heads", "pint": "pints", "clove": "cloves",
    "tub": "tubs", "container": "containers", "pack": "packs",
    "loaf": "loaves", "stick": "sticks",
}
_CONTAINER_UNIT_SINGULARS = {plural: singular for singular, plural in _CONTAINER_UNIT_PLURALS.items()}


def _normalize_container_word(unit_str: str) -> str:
    """Singularize a trailing container word ("lb bags" -> "lb bag") so a
    previously-pluralized display string parses back to the same canonical
    unit as a fresh singular one. Leaves everything else untouched."""
    if not unit_str:
        return unit_str
    words = unit_str.split(" ")
    words[-1] = _CONTAINER_UNIT_SINGULARS.get(words[-1], words[-1])
    return " ".join(words)


# Unit is normally one word ("lb", "cup"), but a store-purchase quantity
# sometimes carries a package word too ("1 lb bag", "12 oz can") — allow one
# optional second word so these parse instead of falling through to the
# "unparseable, concatenate raw strings" fallback in _try_consolidate_quantity.
_QTY_RE = re.compile(r"^(\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)\s*([a-zA-Z]+(?:\s+[a-zA-Z]+)?)?$")


def _strip_prep_descriptor(qty: str) -> str:
    """
    A recipe ingredient's quantity sometimes carries a prep instruction
    after a comma — "3, diced", "4.75 cups, sliced into planks" — useful
    in a recipe's own ingredient list, but not something that belongs on a
    grocery list (nobody buys "3, diced tomatoes"; they buy 3 tomatoes).
    Keep only the purchase amount before the first comma. This also fixes
    quantity *consolidation*: "3, diced" and "1, diced" used to each fail
    to parse (the comma broke the number/unit match below) and so
    "merged" by literally concatenating the raw strings instead of adding
    them — repeated across a few weeks of the same ingredient, that's how
    a line like "3, diced + 1, diced + 1, diced + ..." happens. Stripped
    first, both sides parse as plain numbers and add normally.
    """
    if not qty:
        return qty
    return qty.split(",", 1)[0].strip()


def _parse_quantity(qty: str) -> tuple[float, str | None] | None:
    """Parse a freeform quantity string into (amount, normalized_unit_or_None). Returns None if unparseable (e.g. blank, or freeform text like 'a bunch')."""
    if not qty or not qty.strip():
        return None
    match = _QTY_RE.match(_strip_prep_descriptor(qty.strip()).lower())
    if not match:
        return None
    amount_str, unit_str = match.group(1), (match.group(2) or "").strip()
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
    return amount, (_UNIT_ALIASES.get(unit_str, _normalize_container_word(unit_str)) or None)


_UNIT_PLURALS = {"cup": "cups", "lb": "lbs"}


def _format_quantity(amount: float, unit: str | None) -> str:
    amount_str = f"{amount:g}"
    if not unit:
        return amount_str
    if amount == 1:
        return f"{amount_str} {unit}"
    if unit in _UNIT_PLURALS:
        return f"{amount_str} {_UNIT_PLURALS[unit]}"
    prefix, _, last_word = unit.rpartition(" ")
    if last_word in _CONTAINER_UNIT_PLURALS:
        display_unit = f"{prefix} {_CONTAINER_UNIT_PLURALS[last_word]}" if prefix else _CONTAINER_UNIT_PLURALS[last_word]
        return f"{amount_str} {display_unit}"
    return f"{amount_str} {unit}"


# Unit groups for shopping-list "roll up to a bigger unit" conversion, each
# mapping unit -> how many of the group's smallest unit it equals. Used only
# for grocery-list display (see _humanize_grocery_quantity) — recipe
# scaling (scale_recipe) calls _format_quantity directly and is left in
# whatever unit the recipe was written in, since a cook following a recipe
# wants "12 tbsp", not a shopper's "3/4 cup".
_VOLUME_TO_TSP = {"tsp": 1.0, "tbsp": 3.0, "cup": 48.0}
_WEIGHT_TO_OZ = {"oz": 1.0, "lb": 16.0}
_MASS_TO_G = {"g": 1.0, "kg": 1000.0}
_METRIC_VOL_TO_ML = {"ml": 1.0, "l": 1000.0}
_UNIT_CONVERSION_GROUPS = [_VOLUME_TO_TSP, _WEIGHT_TO_OZ, _MASS_TO_G, _METRIC_VOL_TO_ML]

_NICE_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


def _roll_up_unit(amount: float, unit: str) -> tuple[float, str]:
    """
    Convert amount/unit up to the largest unit in its conversion group that
    it comfortably fits — e.g. 52 tbsp -> ~3.25 cups instead of staying in
    a unit nobody actually measures a shopping quantity in.
    """
    for group in _UNIT_CONVERSION_GROUPS:
        if unit not in group:
            continue
        base_amount = amount * group[unit]
        for candidate_unit, factor in sorted(group.items(), key=lambda kv: -kv[1]):
            if base_amount >= factor - 1e-9:
                return base_amount / factor, candidate_unit
        smallest_unit = min(group, key=group.get)
        return base_amount / group[smallest_unit], smallest_unit
    return amount, unit


def _round_to_nice_fraction(amount: float) -> float:
    """Round to the nearest quarter — friendlier for a shopping list than a repeating decimal."""
    whole = math.floor(amount + 1e-9)
    frac = amount - whole
    best = min(_NICE_FRACTIONS, key=lambda f: abs(f - frac))
    return whole + best if best < 1.0 else whole + 1.0


def _humanize_grocery_quantity(amount: float, unit: str | None) -> str:
    """
    Format a quantity for the grocery list the way a shopper actually buys
    it: unit=None or a discrete descriptor ("clove", "can", or a size
    adjective normalized away in _UNIT_ALIASES) rounds UP to a whole number
    — you can't buy 1.5 onions at the store — while a measurable unit
    (volume/weight) is rolled up to the largest sensible unit and rounded
    to the nearest quarter, so "52 tbsp" becomes "3.25 cups" instead of a
    number nobody would actually measure out.
    """
    if unit not in {u for group in _UNIT_CONVERSION_GROUPS for u in group}:
        whole = math.ceil(amount - 1e-9)
        return _format_quantity(max(whole, 1) if amount > 0 else whole, unit)
    rolled_amount, rolled_unit = _roll_up_unit(amount, unit)
    nice_amount = _round_to_nice_fraction(rolled_amount)
    if nice_amount <= 0 and amount > 0:
        nice_amount = 0.25
    return _format_quantity(nice_amount, rolled_unit)


def _normalize_grocery_quantity(qty: str) -> str:
    """
    Reformat a raw quantity string for shopper-friendly display (see
    _humanize_grocery_quantity). Freeform text that doesn't parse as a
    number+unit (e.g. "a bunch", "to taste") is left exactly as-is.
    """
    stripped = _strip_prep_descriptor((qty or "").strip())
    parsed = _parse_quantity(stripped)
    if not parsed:
        return stripped
    return _humanize_grocery_quantity(parsed[0], parsed[1])


# ---------- Pre-shop check formatter (PRE_SHOP_CHECK.md) ----------
# A separate formatter from _humanize_grocery_quantity above, which rounds
# to nice *decimal* quarters ("3.25 cups") for the normal list display.
# The pre-shop sentence has a stricter rule: never a decimal — a
# fractional remainder becomes a word ("half a stick", not "0.5 stick").
# This is exactly the humanising the old "Already have this?" block
# skipped, showing raw pack math instead (see PRE_SHOP_CHECK.md "Why it
# changed" #3).

_PRE_SHOP_FRACTION_LEAD = {0.25: "a quarter of a {u}", 0.5: "half a {u}", 0.75: "three quarters of a {u}"}
_PRE_SHOP_FRACTION_TAIL = {0.25: "and a quarter", 0.5: "and a half", 0.75: "and three quarters"}


def _pre_shop_pluralize(unit: str, n: float) -> str:
    if n == 1:
        return unit
    if unit in _UNIT_PLURALS:
        return _UNIT_PLURALS[unit]
    if unit in _CONTAINER_UNIT_PLURALS:
        return _CONTAINER_UNIT_PLURALS[unit]
    return unit


def _pre_shop_amount_words(amount: float, unit: str | None) -> str | None:
    """
    Render amount+unit the way a person would say it out loud: whole
    numbers as plain digits, any fractional remainder as a word ("half",
    "a quarter") rather than a decimal — e.g. 0.5/"stick" -> "half a
    stick", 1.5/"stick" -> "a stick and a half". Returns None for a
    non-positive amount (nothing to say).
    """
    if amount <= 0:
        return None
    whole = math.floor(amount + 1e-9)
    frac = amount - whole
    nearest = min((0.0, 0.25, 0.5, 0.75, 1.0), key=lambda f: abs(f - frac))
    if nearest >= 1.0:
        whole, nearest = whole + 1, 0.0
    if not unit:
        # A bare count ("3", "2.5") — nobody buys a fraction of a plain
        # count, so round to the nearest whole rather than use a fraction
        # word (PRE_SHOP_CHECK.md's "round counts; never show decimals").
        return str(max(int(round(amount)), 1))
    if nearest == 0.0:
        n = max(whole, 1)
        return f"{n} {_pre_shop_pluralize(unit, n)}"
    if whole == 0:
        return _PRE_SHOP_FRACTION_LEAD[nearest].format(u=unit)
    if whole == 1:
        return f"a {unit} {_PRE_SHOP_FRACTION_TAIL[nearest]}"
    return f"{whole} {_pre_shop_pluralize(unit, whole)} {_PRE_SHOP_FRACTION_TAIL[nearest]}"


def _pre_shop_humanize_label(raw_qty: str) -> str | None:
    """
    Turn a raw grocery/inventory quantity string into the plain-language
    label the pre-shop sentence needs. Collapses the "X + Y" artifact left
    behind when _try_consolidate_quantity couldn't reconcile two lines
    (the old block's raw-pack-math bug) into one total whenever every
    piece shares a unit. Returns None when the amount can't be reduced to
    one confident, single-unit phrase, so the caller skips flagging that
    item rather than showing something garbled (PRE_SHOP_CHECK.md: "if it
    can't be said in one sentence, don't flag the item").
    """
    raw = (raw_qty or "").strip()
    if not raw:
        return None
    pieces = [p.strip() for p in raw.split(" + ") if p.strip()]
    if len(pieces) > 1:
        parsed = [_parse_quantity(p) for p in pieces]
        if any(p is None for p in parsed):
            return None
        units = {p[1] for p in parsed}
        if len(units) > 1:
            return None
        return _pre_shop_amount_words(sum(p[0] for p in parsed), parsed[0][1])
    parsed = _parse_quantity(pieces[0])
    if parsed:
        return _pre_shop_amount_words(parsed[0], parsed[1])
    # Freeform text ("a bunch", "to taste") is already a single clean
    # phrase — just drop any trailing prep descriptor.
    cleaned = _strip_prep_descriptor(pieces[0])
    return cleaned or None


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
    existing_qty = _strip_prep_descriptor((existing_qty or "").strip())
    new_qty = _strip_prep_descriptor((new_qty or "").strip())
    if not existing_qty:
        return new_qty, True
    if not new_qty:
        return existing_qty, True
    existing_parsed = _parse_quantity(existing_qty)
    new_parsed = _parse_quantity(new_qty)
    if existing_parsed and new_parsed and existing_parsed[1] == new_parsed[1]:
        return _humanize_grocery_quantity(existing_parsed[0] + new_parsed[0], existing_parsed[1]), True
    return f"{existing_qty} + {new_qty}", False


def _subtract_quantity(current_qty: str, remove_qty: str) -> tuple[str, bool]:
    """
    The inverse of _try_consolidate_quantity — used when a meal that
    contributed some amount to a grocery line is being un-planned (see
    _reverse_meal_grocery_contributions) and that amount needs to come back
    out. Returns (resulting_quantity_string, fully_removed). When both sides
    parse with the same unit, subtracts normally, treating a non-positive
    remainder as "nothing left" (fully_removed=True, resulting string
    blank). When they can't be reconciled (freeform text, mismatched units)
    but the two strings are identical, that means this contribution *is*
    the whole line (nothing else merged into it), so it's still safe to
    remove entirely. Otherwise nothing is guessed — the line is left
    exactly as-is (fully_removed=False, resulting string unchanged) rather
    than risk deleting an amount still needed for something else.
    """
    current_qty = _strip_prep_descriptor((current_qty or "").strip())
    remove_qty = _strip_prep_descriptor((remove_qty or "").strip())
    if not current_qty:
        return "", True
    if not remove_qty or current_qty == remove_qty:
        return "", True
    current_parsed = _parse_quantity(current_qty)
    remove_parsed = _parse_quantity(remove_qty)
    if current_parsed and remove_parsed and current_parsed[1] == remove_parsed[1]:
        remainder = current_parsed[0] - remove_parsed[0]
        if remainder <= 0.0001:
            return "", True
        return _humanize_grocery_quantity(remainder, current_parsed[1]), False
    return current_qty, False


def _reverse_meal_grocery_contributions(entry_id: int) -> dict:
    """
    Undo whatever a meal_plan_entries row added to the grocery list, via the
    meal_plan_grocery_links ledger recorded at plan_meal() time — called
    right before that entry is deleted (see swap_meal_in_plan/
    swap_component_in_plan) so changing a planned meal actually replaces its
    ingredients on the grocery list instead of only ever piling the new
    meal's ingredients on top of the old ones. For each linked grocery line,
    subtracts back out exactly the amount this meal contributed (see
    _subtract_quantity) — removing the line entirely if nothing's left,
    trimming it if something is, or leaving it untouched if the amounts
    can't be safely reconciled. A line already moved to in_cart/purchased is
    left alone regardless — the shopper has already acted on it, so this
    won't yank something out of a cart mid-trip. Always clears the ledger
    rows for this entry afterward, whether or not anything was adjusted.
    """
    conn = get_conn()
    links = conn.execute(
        "SELECT id, grocery_item_id, item, quantity FROM meal_plan_grocery_links "
        "WHERE household_id = ? AND meal_plan_entry_id = ?",
        (HOUSEHOLD_ID, entry_id),
    ).fetchall()
    removed_items = []
    trimmed_items = []
    for link in links:
        grocery_row = conn.execute(
            "SELECT id, item, quantity, status FROM grocery_items WHERE id = ? AND household_id = ?",
            (link["grocery_item_id"], HOUSEHOLD_ID),
        ).fetchone()
        if grocery_row and grocery_row["status"] == "needed":
            new_qty, fully_removed = _subtract_quantity(grocery_row["quantity"] or "", link["quantity"] or "")
            if fully_removed:
                conn.execute("DELETE FROM grocery_items WHERE id = ?", (grocery_row["id"],))
                removed_items.append(grocery_row["item"])
            elif new_qty != (grocery_row["quantity"] or ""):
                conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (new_qty, grocery_row["id"]))
                trimmed_items.append(grocery_row["item"])
    conn.execute("DELETE FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id = ?", (HOUSEHOLD_ID, entry_id))
    conn.commit()
    conn.close()
    return {"removed_items": removed_items, "trimmed_items": trimmed_items}


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
    quantity = _normalize_grocery_quantity(quantity or "")
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
    """
    List grocery items, optionally filtered by status: 'needed', 'in_cart',
    'purchased', 'all' (every status, including excluded items), or
    'excluded' (only items hidden via exclude_grocery_item). For 'needed'/
    'in_cart'/'purchased', items excluded from the list (see
    exclude_grocery_item) are left out automatically — they're still
    tracked, just not shown on the normal shopping list.
    """
    conn = get_conn()
    if status == "excluded":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, excluded_from_list, already_have_reviewed, added_by FROM grocery_items "
            "WHERE household_id = ? AND excluded_from_list = 1 ORDER BY category, item",
            (HOUSEHOLD_ID,),
        ).fetchall()
    elif status == "all":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, excluded_from_list, already_have_reviewed, added_by FROM grocery_items "
            "WHERE household_id = ? ORDER BY category, item",
            (HOUSEHOLD_ID,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, excluded_from_list, already_have_reviewed, added_by FROM grocery_items "
            "WHERE household_id = ? AND status = ? AND excluded_from_list = 0 ORDER BY category, item",
            (HOUSEHOLD_ID, status),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def exclude_grocery_item(item_id: int) -> dict:
    """
    Hide an item from the normal shown/shopped grocery list without
    deleting it — for something the Shopper will get elsewhere (a butcher,
    a farmers market) rather than on the regular trip. It stays tracked:
    still in grocery_items with its status unchanged, so a future
    add_grocery_item call for the same item still consolidates into this
    same line instead of creating a duplicate — only its visibility in the
    default 'needed'/'in_cart'/'purchased' views changes. See
    include_grocery_item to undo, and list_grocery_list(status='excluded')
    to see what's currently hidden this way.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET excluded_from_list = 1 WHERE id = ? AND household_id = ?",
        (item_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "excluded_from_list": True}


def include_grocery_item(item_id: int) -> dict:
    """Undo exclude_grocery_item — put an item back on the normal shown/shopped grocery list."""
    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET excluded_from_list = 0 WHERE id = ? AND household_id = ?",
        (item_id, HOUSEHOLD_ID),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "excluded_from_list": False}


def get_grocery_list_by_section(status: str = "needed") -> dict:
    """
    Get the grocery list grouped into standard store sections (produce,
    dairy, meat/seafood, pantry, frozen, other) in a sensible shopping
    order, rather than a flat list. Use this whenever showing or reviewing
    the grocery list to the user so it reads like something they can
    actually shop from, aisle by aisle, instead of a flat ingredient dump.
    Items hidden via exclude_grocery_item are left out automatically (see
    list_grocery_list) unless status='excluded' or 'all' is passed.
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


def repair_grocery_quantities(status: str = "needed") -> dict:
    """
    One-time cleanup for grocery lines whose quantity got stuck as an
    ugly, concatenated "+"-joined string from the prep-descriptor
    consolidation bug (see _strip_prep_descriptor) — e.g. "3, diced + 1,
    diced + 1, diced" instead of a clean "5", or "4.75 cups, sliced + 1/4
    cup, sliced" instead of "5 cups". Re-parses each "+"-joined segment
    (stripping any prep descriptor first) and re-sums same-unit segments
    into one clean total, using the same logic add_grocery_item now uses
    automatically for new additions. A segment that still can't be
    reconciled (mixed incompatible units, or genuinely non-numeric text
    like "a bunch") is left joined with " + " exactly as that same fallback
    would produce today — so this is safe to run more than once. The
    underlying bug is fixed at the source now (see _strip_prep_descriptor),
    so this is purely for repairing lines that already got mangled before
    that fix existed; it isn't something that needs to run automatically
    going forward.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity FROM grocery_items WHERE household_id = ? AND status = ?",
        (HOUSEHOLD_ID, status),
    ).fetchall()
    fixed = []
    for r in rows:
        qty = r["quantity"] or ""
        if " + " not in qty and "," not in qty:
            continue  # nothing to clean on this line
        segments = [s.strip() for s in qty.split(" + ") if s.strip()]
        cleaned = ""
        for seg in segments:
            cleaned, _ = _try_consolidate_quantity(cleaned, seg)
        if cleaned != qty:
            conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (cleaned, r["id"]))
            fixed.append({"item": r["item"], "before": qty, "after": cleaned})
    conn.commit()
    conn.close()
    return {"fixed_count": len(fixed), "fixed": fixed}


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


def update_grocery_item(item_id: int, quantity: str | None = None, category: str | None = None) -> dict:
    """
    Directly edit an already-listed grocery item's quantity and/or category
    by id — for correcting something already on the list (wrong amount,
    miscategorized) rather than adding a new line. Unlike add_grocery_item,
    this never merges/consolidates with another row since it's already
    targeting one specific, known item. Leave a field as None to leave it
    unchanged.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, HOUSEHOLD_ID),
    ).fetchone()
    if not row:
        conn.close()
        return {"item_id": item_id, "found": False}
    new_quantity = quantity if quantity is not None else row["quantity"]
    new_category = category if category is not None else row["category"]
    conn.execute(
        "UPDATE grocery_items SET quantity = ?, category = ? WHERE id = ?",
        (new_quantity, new_category, item_id),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "quantity": new_quantity, "category": new_category, "found": True}


def remove_grocery_item(item_id: int) -> dict:
    """Delete an item from the grocery list."""
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "deleted": True}


def move_grocery_item_to_inventory(item_id: int) -> dict:
    """
    For a grocery list item the household realizes they already have on
    hand (turns out there's already a box in the pantry, a bag in the
    freezer, etc.) — not the get_grocery_already_have_items cross-reference
    case, which only catches items inventory already happens to know
    about, but the "oh wait, I actually have this" moment on any item,
    known to inventory or not. Adds it straight to pantry/fridge inventory
    (merging into a matching existing row the same way _add_to_inventory
    always does) carrying over its grocery-list quantity and category, then
    removes it from the grocery list — no separate manual inventory entry
    needed. Raises ValueError if the item isn't found.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, HOUSEHOLD_ID),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No grocery list item with id {item_id}.")

    inventory_result = _add_to_inventory(
        row["item"],
        row["quantity"] or "",
        source="grocery_list_already_have",
        category=row["category"] or None,
    )
    remove_grocery_item(item_id)
    return {
        "item_id": item_id,
        "item": row["item"],
        "moved_to_inventory": True,
        "inventory_item_id": inventory_result.get("item_id"),
    }


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
    location: str | None = None,
) -> dict:
    conn = get_conn()
    # If a location is given, only merge into a row already tracked at that
    # SAME location — a "BBQ sauce" bought new for the pantry shouldn't
    # silently merge into an already-opened one sitting in the fridge; that
    # should become (and stay) a second, distinct row. Without a location
    # hint, fall back to the old broad match-by-name-anywhere behavior.
    if location:
        existing = conn.execute(
            "SELECT id, quantity, category, expiration_date, location FROM inventory_items "
            "WHERE household_id = ? AND LOWER(item) = LOWER(?) AND location = ?",
            (HOUSEHOLD_ID, item, location),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id, quantity, category, expiration_date, location FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
            (HOUSEHOLD_ID, item),
        ).fetchone()
    if existing:
        merged_qty, _ = _try_consolidate_quantity(existing["quantity"] or "", quantity)
        fields = "quantity = ?, source = ?, updated_at = datetime('now')"
        params = [merged_qty, source]
        resolved_exp = _resolved_expiration_update(expiration_date, category, existing["category"], existing["expiration_date"], item)
        if resolved_exp:
            fields += ", expiration_date = ?"
            params.append(resolved_exp)
        if category:
            fields += ", category = ?"
            params.append(category)
        if location and location != existing["location"]:
            fields += ", location = ?"
            params.append(location)
        params.append(existing["id"])
        conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ?", params)
        conn.commit()
        item_id = existing["id"]
    else:
        item_category = category or "other"
        item_location = _resolve_location(location, item_category)
        cur = conn.execute(
            "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (HOUSEHOLD_ID, item, quantity, source, expiration_date or _estimate_expiration_date(item_category, item), item_category, item_location),
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
    location: str | None = None,
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
    matches the item, defaults to 'other' if omitted. location (fridge,
    freezer, or pantry) is where it's physically stored, which can diverge
    from category (an opened sauce is category='pantry' by food type but
    location='fridge' once opened) — set it explicitly whenever the person
    mentions or implies where something's actually kept ("it's in the
    fridge now that it's open"), especially for the same item that might
    also exist elsewhere (an unopened one still in the pantry) so they stay
    as distinct entries rather than merging into one. Leave it unset to
    fall back to a reasonable category-based guess for a brand-new item, or
    to leave an existing item's location as-is.
    """
    if action == "add":
        return _add_to_inventory(item, quantity, source="chat", expiration_date=expiration_date, category=category, location=location)

    if action == "set":
        conn = get_conn()
        if location:
            existing = conn.execute(
                "SELECT id, category, expiration_date, location FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?) AND location = ?",
                (HOUSEHOLD_ID, item, location),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT id, category, expiration_date, location FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
                (HOUSEHOLD_ID, item),
            ).fetchone()
        if existing:
            fields = "quantity = ?, updated_at = datetime('now')"
            params = [quantity]
            resolved_exp = _resolved_expiration_update(expiration_date, category, existing["category"], existing["expiration_date"], item)
            if resolved_exp:
                fields += ", expiration_date = ?"
                params.append(resolved_exp)
            if category:
                fields += ", category = ?"
                params.append(category)
            if location and location != existing["location"]:
                fields += ", location = ?"
                params.append(location)
            params.append(existing["id"])
            conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ?", params)
            conn.commit()
            item_id = existing["id"]
        else:
            item_category = category or "other"
            item_location = _resolve_location(location, item_category)
            cur = conn.execute(
                "INSERT INTO inventory_items (household_id, item, quantity, source, category, expiration_date, location) VALUES (?, ?, ?, 'chat', ?, ?, ?)",
                (HOUSEHOLD_ID, item, quantity, item_category, expiration_date or _estimate_expiration_date(item_category, item), item_location),
            )
            conn.commit()
            item_id = cur.lastrowid
        conn.close()
        return {"item_id": item_id, "item": item, "quantity": quantity}

    if action in ("use", "remove"):
        conn = get_conn()
        if location:
            existing = conn.execute(
                "SELECT id, quantity FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?) AND location = ?",
                (HOUSEHOLD_ID, item, location),
            ).fetchone()
        else:
            # No location given and this item might exist in more than one
            # place at once (see get_cross_location_duplicates) — this picks
            # whichever row the database returns first rather than asking,
            # a known limitation; pass location when it's actually known to
            # avoid the ambiguity.
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
    "2026-09-01", "category": "pantry", "location": "pantry"} to mix
    actions/quantities/categories/locations within one call. See
    update_inventory for what each action/category/location means — fill in
    category (and location, when known) per item when populating a batch so
    everything lands in the right place immediately.
    """
    results = []
    for raw in items:
        if isinstance(raw, dict):
            name = (raw.get("item") or "").strip()
            act = raw.get("action") or action
            qty = raw.get("quantity", "")
            exp = raw.get("expiration_date")
            cat = raw.get("category")
            loc = raw.get("location")
        else:
            name = (raw or "").strip()
            act = action
            qty = ""
            exp = None
            cat = None
            loc = None
        if not name:
            continue
        results.append(update_inventory(name, act, quantity=qty, expiration_date=exp, category=cat, location=loc))
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
        "SELECT id, item, quantity, source, expiration_date, category, location, created_at FROM inventory_items WHERE household_id = ? ORDER BY item",
        (HOUSEHOLD_ID,),
    ).fetchall()
    conn.close()
    items = [dict(r) for r in rows]
    for it in items:
        it["location"] = _display_location(it)
    return items


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


def get_inventory_by_location() -> dict:
    """
    Get pantry/fridge inventory grouped by storage location (fridge,
    freezer, pantry) instead of food category — use this when the user
    specifically wants to know what's in the fridge or what's in the
    pantry, since location can diverge from category (an opened sauce is
    category='pantry' by food type but location='fridge' once opened).
    Powers the Inventory view's location-grouping toggle.
    """
    items = get_inventory()
    buckets: dict[str, list[dict]] = {loc: [] for loc in _LOCATION_ORDER}
    for it in items:
        loc = it["location"]
        buckets.setdefault(loc, [])
        buckets[loc if loc in buckets else "pantry"].append(it)
    return {"locations": [{"location": loc, "items": buckets[loc]} for loc in _LOCATION_ORDER if buckets[loc]]}


def get_cross_location_duplicates() -> list[dict]:
    """
    Find items tracked in more than one storage location at once — e.g. an
    opened BBQ sauce in the fridge and an unopened one still in the pantry.
    Surfaced so a near-empty opened item doesn't go unnoticed while an
    unopened twin sits untouched elsewhere, and so a grocery re-buy doesn't
    happen when one's already on hand, just not where expected. Check this
    proactively the same way as get_expiring_soon when it's relevant to the
    conversation (inventory questions, plan generation, grocery additions).
    """
    items = get_inventory()
    by_name: dict[str, list[dict]] = {}
    for it in items:
        by_name.setdefault(it["item"].strip().lower(), []).append(it)
    duplicates = []
    for entries in by_name.values():
        locations = {e["location"] for e in entries}
        if len(entries) > 1 and len(locations) > 1:
            duplicates.append({
                "item": entries[0]["item"],
                "entries": [
                    {"id": e["id"], "quantity": e["quantity"], "location": e["location"], "expiration_date": e["expiration_date"]}
                    for e in entries
                ],
            })
    return duplicates


def get_expiring_soon(days: int = 4) -> list[dict]:
    """
    List inventory items that are already past their (entered or
    estimated) expiration, or expiring within the given number of days —
    soonest/most overdue first. Each item includes a status of 'expired' or
    'expiring_soon'. Use this for "what's about to go bad" questions, and
    check it proactively when generating a weekly plan or suggesting a
    meal, so near-expiring items get worked in before they're wasted (see
    generate_weekly_plan's use_it_up weighting).
    """
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    today = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, expiration_date FROM inventory_items "
        "WHERE household_id = ? AND expiration_date IS NOT NULL AND expiration_date != '' AND expiration_date <= ? "
        "ORDER BY expiration_date ASC",
        (HOUSEHOLD_ID, cutoff),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        status = "expired" if r["expiration_date"] < today else "expiring_soon"
        result.append({
            "id": r["id"], "item": r["item"], "quantity": r["quantity"], "category": r["category"],
            "expiration_date": r["expiration_date"], "status": status,
        })
    return result


def get_fresh_perishable_inventory(near_expiring_days: int = 4) -> list[dict]:
    """
    List perishable items on hand (meat/seafood, produce, dairy) that
    AREN'T already covered by get_expiring_soon — i.e. still have some
    runway left, not just the ones about to go bad. Soonest-expiring first.
    Use alongside get_expiring_soon when generating a weekly plan: this is
    a softer, general nudge to favor meats/seafood/produce/dairy already on
    hand over buying more of the same, distinct from near_expiring_inventory's
    stronger "use this up before it's wasted" signal.
    """
    cutoff = (date.today() + timedelta(days=near_expiring_days)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, expiration_date FROM inventory_items "
        "WHERE household_id = ? AND category IN ('produce', 'dairy', 'meat/seafood') "
        "AND (expiration_date IS NULL OR expiration_date = '' OR expiration_date > ?) "
        "ORDER BY CASE WHEN expiration_date IS NULL OR expiration_date = '' THEN 1 ELSE 0 END, expiration_date ASC",
        (HOUSEHOLD_ID, cutoff),
    ).fetchall()
    conn.close()
    return [
        {"id": r["id"], "item": r["item"], "quantity": r["quantity"], "category": r["category"], "expiration_date": r["expiration_date"]}
        for r in rows
    ]


def remove_inventory_item(item_id: int) -> dict:
    """Remove a single inventory item outright (e.g. it spoiled, or was added by mistake) — used by the Inventory view's delete control."""
    conn = get_conn()
    conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "removed": True}


# ---------- internal helpers ----------

def _get_or_create_member(conn, name: str) -> int:
    # Case-insensitive lookup, matching the pattern used for recipes and
    # grocery items elsewhere in this file — an exact-match lookup here let
    # something like "my partner" vs. the saved name "Alex" (or even just
    # "alex" vs. "Alex") silently create a duplicate member row instead of
    # attaching to the existing person (Phase 4, §4.1 Fix 1).
    row = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)", (HOUSEHOLD_ID, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO members (household_id, name) VALUES (?, ?)", (HOUSEHOLD_ID, name))
    conn.commit()
    return cur.lastrowid
