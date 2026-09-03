"""
Meal-planning setup: dislikes, usual stores, and the household's stated
food preferences, plus the onboarding answers that seed them.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id
from . import household as _household
from . import memory as _memory


def get_meal_planning_setup_status() -> dict:
    """
    Check whether meal-planning onboarding (dietary restrictions + household
    food preferences) has been completed, and whether any recipes exist yet.
    Call this before helping with meal planning or groceries for the first
    time in a conversation.
    """
    conn = get_conn()
    members = conn.execute(
        "SELECT name, dietary_restrictions_json FROM members WHERE household_id = ?", (household_id(),)
    ).fetchall()
    prefs = conn.execute(
        "SELECT notes, protein_preferences_json, cuisine_preferences_json, dislikes_json, cooking_time_preference, onboarding_complete "
        "FROM meal_preferences WHERE household_id = ?",
        (household_id(),),
    ).fetchone()
    recipe_count = conn.execute(
        "SELECT COUNT(*) AS c FROM recipes WHERE household_id = ?", (household_id(),)
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
        "SELECT dislikes_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    current = json.loads(existing["dislikes_json"]) if existing else []
    merged = list(dict.fromkeys(current + [i.strip() for i in items if i.strip()]))
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, dislikes_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET dislikes_json = excluded.dislikes_json, updated_at = datetime('now')
        """,
        (household_id(), json.dumps(merged)),
    )
    conn.commit()
    conn.close()
    _household._log_preference_event("dislikes", "write")
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
        "SELECT usual_stores_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    current = json.loads(existing["usual_stores_json"]) if existing else []
    merged = list(dict.fromkeys(current + [i.strip() for i in items if i.strip()]))
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, usual_stores_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET usual_stores_json = excluded.usual_stores_json, updated_at = datetime('now')
        """,
        (household_id(), json.dumps(merged)),
    )
    conn.commit()
    conn.close()
    _household._log_preference_event("usual_stores", "write")
    return {"usual_stores": merged}


def add_store_typical_items(
    store: str, items: list[str], log_event: bool = True, sync_preference: bool = True
) -> dict:
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

    Also remembers each item's store preference (see
    stores.set_item_store), so a future grocery add of that item name
    auto-assigns here instead of landing unsorted — this is the Kitchen
    sheet's half of the Loop Board "Stores: one bidirectional memory..."
    unification. Each item is dropped from any OTHER store's typical-items
    list it was on, so the two lists can't disagree about where an item
    usually comes from (the preference always wins).

    sync_preference=False is for internal use only (stores.set_item_store
    sets it when it calls back in here, so a chat/grocery-triage write
    can't bounce back and forth with this function forever).
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT store_typical_items_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    current = json.loads(existing["store_typical_items_json"]) if existing else {}
    cleaned = [i.strip() for i in items if i.strip()]
    store_items = list(dict.fromkeys(current.get(store, []) + cleaned))
    current[store] = store_items
    cleaned_lower = {i.lower() for i in cleaned}
    for other_store in list(current.keys()):
        if other_store == store:
            continue
        filtered = [i for i in current[other_store] if i.lower() not in cleaned_lower]
        if filtered != current[other_store]:
            current[other_store] = filtered
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, store_typical_items_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET store_typical_items_json = excluded.store_typical_items_json, updated_at = datetime('now')
        """,
        (household_id(), json.dumps(current)),
    )
    conn.commit()
    conn.close()
    if log_event:
        _household._log_preference_event("store_typical_items", "write")
    if sync_preference:
        # Local import: see the matching note in stores.set_item_store —
        # these two modules call each other, which is only safe resolved
        # at call time, not at module load time.
        from . import stores as _stores
        for item in cleaned:
            _stores.set_item_store(item, store, log_event=False, sync_typical=False)
    return {"store": store, "typical_items": store_items}


def remove_store_typical_item(store: str, item: str, log_event: bool = True) -> dict:
    """
    Remove a single item from a store's typical-items list (case-insensitive
    match). Leaves the store itself (in usual_stores) untouched.

    Loop Board "Stores: one bidirectional memory..." decided this should
    also forget the item's remembered store preference (see
    stores.set_item_store) — otherwise the grocery list would keep
    auto-assigning an item to a store the Kitchen sheet no longer lists it
    under. Guarded to only clear the preference when it currently points
    at THIS store: removing a stale/duplicate typical-items entry for a
    store the item isn't actually preferred at anymore shouldn't be able
    to wipe out an unrelated, correct preference for a different store.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT store_typical_items_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    current = json.loads(existing["store_typical_items_json"]) if existing else {}
    store_items = [i for i in current.get(store, []) if i.lower() != (item or "").lower()]
    current[store] = store_items
    conn.execute(
        "UPDATE meal_preferences SET store_typical_items_json = ?, updated_at = datetime('now') WHERE household_id = ?",
        (json.dumps(current), household_id()),
    )
    conn.commit()
    conn.close()
    if log_event:
        _household._log_preference_event("store_typical_items", "delete")
    from . import stores as _stores
    current_pref = _stores.get_item_store_preferences().get((item or "").strip().lower())
    if current_pref and current_pref.lower() == (store or "").lower():
        _stores.set_item_store(item, "", log_event=False)
    return {"store": store, "typical_items": store_items}


def remove_item_from_all_stores_typical_list(item: str) -> None:
    """
    Drop `item` from every store's typical-items list. Called when its
    item_store_preferences row is cleared (see stores.set_item_store) so
    the Kitchen sheet's "usually get here" suggestions and the grocery
    auto-assigner can never disagree — an item with no remembered store
    isn't "usually" bought anywhere anymore. Not itself a preference_events
    write: it's cleanup for whichever action already logged the real one.
    """
    conn = get_conn()
    existing = conn.execute(
        "SELECT store_typical_items_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    if not existing:
        conn.close()
        return
    current = json.loads(existing["store_typical_items_json"])
    item_lower = (item or "").strip().lower()
    changed = False
    for store_name in list(current.keys()):
        filtered = [i for i in current[store_name] if i.lower() != item_lower]
        if filtered != current[store_name]:
            current[store_name] = filtered
            changed = True
    if changed:
        conn.execute(
            "UPDATE meal_preferences SET store_typical_items_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(current), household_id()),
        )
        conn.commit()
    conn.close()


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
        "SELECT * FROM meal_preferences WHERE household_id = ?", (household_id(),)
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
            household_id(),
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
        _household.add_member(name)
    _household._log_preference_event("onboarding_household_size", "write")
    _household._log_preference_event("onboarding_member_names", "write")

    # set_member_dietary_restrictions already logs its own preference_event
    # per member (same path a mid-conversation correction uses), so no
    # separate explicit log call here — one event per member who actually
    # has something, not one flat event for the whole question.
    for name, restrictions in household_restrictions.items():
        if not name.strip():
            continue
        _household.set_member_dietary_restrictions(name.strip(), restrictions, replace=True)

    set_household_meal_preferences(
        eating_style=eating_style,
        mark_complete=False,
    )
    _household._log_preference_event("onboarding_eating_style", "write")

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO meal_preferences (household_id, dislikes_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(household_id) DO UPDATE SET dislikes_json = excluded.dislikes_json, updated_at = datetime('now')
        """,
        (household_id(), json.dumps(wont_eat)),
    )
    conn.commit()
    conn.close()
    _household._log_preference_event("onboarding_wont_eat", "write")

    set_household_meal_preferences(
        cuisine_preferences=excited_about,
        dinners_per_week=dinners_per_week,
        breakfasts_per_week=breakfasts_per_week,
        lunches_per_week=lunches_per_week,
        mark_complete=True,
    )
    _household._log_preference_event("onboarding_excited_about", "write")
    _household._log_preference_event("onboarding_meals_per_week", "write")

    return _memory.get_household_memory()
