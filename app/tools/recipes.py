"""
Recipes: adding, listing, scaling, feedback and cooking notes.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id
from . import grocery as _grocery
from . import quantities as _quantities


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
            household_id(), name, notes, json.dumps(ingredients), json.dumps(tags or []),
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
        (household_id(), recipe_name),
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
    rows = conn.execute(query, (household_id(),)).fetchall()

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
        parsed = _quantities._parse_quantity(ing.get("qty", ""))
        if parsed:
            amount, unit = parsed
            scaled_ingredients.append({**ing, "qty": _quantities._format_quantity(amount * ratio, unit)})
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
        (household_id(), recipe_name),
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
        "SELECT id FROM recipes WHERE household_id = ? AND name = ?", (household_id(), recipe_name)
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")
    conn.execute(
        "INSERT INTO recipe_notes (household_id, recipe_id, note_type, note) VALUES (?, ?, 'feedback', ?)",
        (household_id(), recipe["id"], note),
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
        "SELECT id FROM recipes WHERE household_id = ? AND name = ?", (household_id(), recipe_name)
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")
    conn.execute(
        "INSERT INTO recipe_notes (household_id, recipe_id, note_type, note) VALUES (?, ?, 'deviation', ?)",
        (household_id(), recipe["id"], note),
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
        "SELECT id FROM recipes WHERE household_id = ? AND name = ?", (household_id(), recipe_name)
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

    Quantities are scaled to the meal's actual HEADCOUNT before they reach
    the list (Emily's deepened attendance model): a Thursday dinner only
    one of two people is home for buys for one. The factor comes from
    attendance.grocery_scale_factor, which is 1.0 — an exact no-op, leaving
    every quantity byte-for-byte as the recipe writes it — unless that
    specific meal has an explicit attendance row. So a week where everyone
    is home shops precisely as it always has.
    """
    # Which meal this is, so attendance can say how many it feeds. A
    # missing entry (an ad hoc add, a row since deleted) simply doesn't
    # scale rather than failing the shop.
    scale = 1.0
    entry_conn = get_conn()
    entry_row = entry_conn.execute(
        "SELECT date, slot FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    entry_conn.close()
    if entry_row:
        from . import attendance as _attendance
        scale = _attendance.grocery_scale_factor(entry_row["date"], entry_row["slot"])
        if scale != 1.0:
            recipe_ingredients = _attendance.scale_ingredients(recipe_ingredients, scale)
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
            (household_id(),),
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
        add_result = _grocery.add_grocery_item(
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
            (household_id(), entry_id, add_result["item_id"], ing["item"], _quantities._strip_prep_descriptor(ing.get("qty", "") or "")),
        )
        link_conn.commit()
        link_conn.close()
    return added_items, already_have
