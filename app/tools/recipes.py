"""
Recipes: adding, listing, scaling, feedback and cooking notes.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id
from . import grocery as _grocery
from . import household as _household
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

    When a rating is given AND the most recent time this recipe was
    actually cooked (a checked-off meal_plan_entries row) had exactly one
    household member home for it, that person's own taste is updated too,
    silently — see attribute_recipe_feedback's 'solo_auto' source and
    DESIGN_SYSTEM.md §7's silent-learning rule. This never overwrites an
    explicit attribution someone already gave that recipe/person pair. The
    result's solo_auto_attribution key names who this fired for, if anyone,
    so a caller can mention it if it seems worth surfacing.
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

    solo_auto_attribution = None
    if rating is not None:
        solo_auto_attribution = _maybe_auto_attribute_solo_night(recipe["id"], recipe_name, rating)
    return {
        "name": recipe_name, "rating": rating, "feedback_notes": merged_notes,
        "solo_auto_attribution": solo_auto_attribution,
    }


def _maybe_auto_attribute_solo_night(recipe_id: int, recipe_name: str, rating: str) -> str | None:
    """
    Solo-night auto-attribution (Loop Board "Per-person taste learning +
    solo-night personalization"): if the last time this recipe was actually
    cooked, exactly one household member was home for it, that meal's
    feedback is really THEIRS, not the household's in general — silently
    record it as such and return their name. Returns None when the last
    cooked instance wasn't a solo meal (or there isn't one), or when an
    'explicit' attribution already exists for this exact recipe/person —
    a stated fact from chat is never quietly overwritten by a guess.

    "Last cooked" is read off meal_plan_entries.cooked_status='done' (an
    actually-checked-off meal — see cooker.check_off_meal), not just the
    most recently PLANNED entry, since a planned-but-never-cooked meal
    tells us nothing about who actually ate it.

    Deferred import: attendance imports weekly_plan, which imports this
    module — importing attendance at module scope here would be circular.
    Same trick household.py uses for the same reason.
    """
    from . import attendance as _attendance

    conn = get_conn()
    entry = conn.execute(
        """
        SELECT date, slot FROM meal_plan_entries
        WHERE household_id = ? AND recipe_id = ? AND cooked_status = 'done'
        ORDER BY COALESCE(cooked_at, created_at) DESC, id DESC LIMIT 1
        """,
        (household_id(), recipe_id),
    ).fetchone()
    if not entry:
        conn.close()
        return None
    conn.close()

    att = _attendance.get_slot_attendance(entry["date"], entry["slot"])
    if att["guest_count"] or len(att["present_member_ids"]) != 1:
        return None
    member_id = att["present_member_ids"][0]
    member_name = att["present_names"][0]

    conn = get_conn()
    existing = conn.execute(
        "SELECT source FROM member_recipe_feedback WHERE household_id = ? AND recipe_id = ? AND member_id = ?",
        (household_id(), recipe_id, member_id),
    ).fetchone()
    if existing and existing["source"] == "explicit":
        conn.close()
        return None  # a stated fact outranks a guess — never clobber it silently
    conn.execute(
        """
        INSERT INTO member_recipe_feedback (household_id, recipe_id, member_id, rating, source)
        VALUES (?, ?, ?, ?, 'solo_auto')
        ON CONFLICT(household_id, recipe_id, member_id) DO UPDATE SET
            rating = excluded.rating, source = 'solo_auto', updated_at = datetime('now')
        """,
        (household_id(), recipe_id, member_id, rating),
    )
    conn.commit()
    conn.close()
    _household._log_preference_event(f"member:{member_name}:recipe:{recipe_name}", "write")
    return member_name


def attribute_recipe_feedback(
    recipe_name: str, member_name: str, rating: str | None = None, notes: str = "",
) -> dict:
    """
    Record which SPECIFIC household member a recipe's feedback belongs to —
    additive on top of the household-level rating from mark_recipe_feedback,
    which stays exactly as-is and remains the fallback for anyone (or any
    recipe) without their own row here. Call this the moment a rating comes
    with a name attached, in either of these two shapes:

    1. Someone says it with a name attached ("Vineeth loved the skewers") —
       pass rating explicitly.
    2. A household-level rating already exists and someone clarifies it was
       really just their own opinion ("that was just my rating," "that's
       just me, Vineeth actually didn't love it") — omit rating and this
       reuses the recipe's current household-level rating as this person's.
       Raises if the recipe has no rating yet to attribute — ask for one
       instead of guessing.

    A stated attribution like this counts as its own confirmation
    (DESIGN_SYSTEM.md §7) — safe to save immediately, no separate
    confirm-first step needed. Always recorded as source='explicit', so it
    can never be silently overwritten by solo-night auto-attribution later.
    """
    conn = get_conn()
    recipe = conn.execute(
        "SELECT id, rating FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (household_id(), recipe_name),
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")

    resolved_rating = rating or (recipe["rating"] or None)
    if not resolved_rating:
        conn.close()
        raise ValueError(
            f"'{recipe_name}' has no rating yet to attribute to {member_name} — pass rating explicitly."
        )
    if resolved_rating not in ("liked", "disliked"):
        conn.close()
        raise ValueError("rating must be 'liked' or 'disliked'.")

    member_id = _household._get_or_create_member(conn, member_name)
    conn.execute(
        """
        INSERT INTO member_recipe_feedback (household_id, recipe_id, member_id, rating, source, notes)
        VALUES (?, ?, ?, ?, 'explicit', ?)
        ON CONFLICT(household_id, recipe_id, member_id) DO UPDATE SET
            rating = excluded.rating, source = 'explicit',
            notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE member_recipe_feedback.notes END,
            updated_at = datetime('now')
        """,
        (household_id(), recipe["id"], member_id, resolved_rating, notes),
    )
    conn.commit()
    conn.close()
    _household._log_preference_event(f"member:{member_name}:recipe:{recipe_name}", "write")
    return {"name": recipe_name, "member": member_name, "rating": resolved_rating, "source": "explicit"}


def get_member_taste(member_name: str) -> dict:
    """
    What's actually known about ONE person's own taste, separate from the
    household's shared rating — answers "what does Vineeth like?" from
    per-person data specifically (see attribute_recipe_feedback and
    solo-night auto-attribution), rather than the whole household's.

    has_any_data tells you whether this is real signal or a cold start: for
    a recipe this person has no row for, the household-level rating from
    mark_recipe_feedback is what actually governs their meals, exactly as
    it always has — say so plainly rather than implying deeper personal
    knowledge than actually exists yet.
    """
    conn = get_conn()
    member = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (household_id(), member_name),
    ).fetchone()
    if not member:
        conn.close()
        raise ValueError(f"No household member named '{member_name}'.")
    rows = conn.execute(
        """
        SELECT r.name AS recipe_name, mrf.rating
        FROM member_recipe_feedback mrf JOIN recipes r ON r.id = mrf.recipe_id
        WHERE mrf.household_id = ? AND mrf.member_id = ?
        ORDER BY mrf.updated_at DESC
        """,
        (household_id(), member["id"]),
    ).fetchall()
    conn.close()
    return {
        "name": member_name,
        "liked_recipes": [r["recipe_name"] for r in rows if r["rating"] == "liked"],
        "disliked_recipes": [r["recipe_name"] for r in rows if r["rating"] == "disliked"],
        "has_any_data": bool(rows),
    }


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
    One planned meal's ingredients onto the grocery list — the single-meal
    door onto _add_recipe_ingredients_for_entries below, which is where
    the behaviour lives. Used by plan_meal and the swap paths, where there
    genuinely is only one meal to account for.
    """
    return _add_recipe_ingredients_for_entries([entry_id], recipe_ingredients, weekly_plan_id)


def _add_recipe_ingredients_for_entries(
    entry_ids: list[int], recipe_ingredients: list[dict], weekly_plan_id: int | None
) -> tuple[list[str], list[str]]:
    """
    Put ONE RECIPE's ingredients onto the grocery list for every meal in
    this week that cooks it, and record what each of those meals
    contributed, returning (added_items, already_have).

    The unit of work is a recipe-week, not a meal, and that is the whole
    point. Called once per meal — which is what approve_weekly_plan used
    to do — a breakfast planned six mornings put "1 bag baby spinach" on
    the list six times, and the summing that a previous fix correctly
    introduced turned that into six bags. Emily's first approved week
    asked her to buy 6 bags of spinach, 4 bottles of honey and 4 tubs of
    hummus. Nothing downstream was wrong; the inputs were.

    So each ingredient goes down one of two paths:

    - A SEALED PACKAGE ("1 bag", "1 bottle", "1 jar", "48 oz tub" — see
      quantities.package_unit) is what the household buys ONE of and draws
      on all week. It is added once for the whole recipe-week, not
      multiplied by how often the meal repeats, and it consolidates with
      the same package on another recipe by keeping the larger of the two
      rather than adding them (quantity_mode="max"), so three dinners that
      each list a bottle of olive oil buy one bottle. A recipe that really
      does want "2 bottles" still wins.

      This is a beta rule and it is deliberately generous-downward: one
      bottle of oil, one jar of spice, one bag of granola per week is
      right far more often than it is wrong, and a household that truly
      needs a second one can bump the line. Under-buying a staple costs a
      trip; the old behaviour cost trust in the whole list.

    - Everything else is a PER-PORTION amount — 4 cups of beans, 3 bell
      peppers, a bunch of cilantro — and still adds up across every meal
      that wants it, each meal scaled to its own headcount exactly as
      before. Five dinners wanting 2-4 peppers each genuinely want the
      sum.

    Quantities are scaled to each meal's actual HEADCOUNT before they reach
    the list (Emily's deepened attendance model): a Thursday dinner only
    one of two people is home for buys for one. The factor comes from
    attendance.grocery_scale_factor, which is 1.0 — an exact no-op, leaving
    every quantity byte-for-byte as the recipe writes it — unless that
    specific meal has an explicit attendance row. Scaling stays per meal
    rather than being folded into one recipe-week factor, so a week where
    everyone is home shops precisely as it always has and the anchor
    grocery_scale_factor deliberately chose (the household, not the
    recipe's default_servings) is untouched here. Moving that anchor is
    the leftovers-servings-scaling branch's job, and this function is
    where it plugs in: give the per-portion path a total-servings factor
    computed across `entry_ids` instead of the per-entry factor below.
    A package is not scaled at all — half a table still buys one bottle.

    Every contributing meal gets its own meal_plan_grocery_links row, so
    reversal stays exactly symmetric with what was added: a per-portion
    row carries that meal's own scaled share, and a package row carries
    the package, with _reverse_meal_grocery_contributions holding the line
    on the list until the last meal that named it is gone.

    Deliberately never called from anywhere the household hasn't said yes
    — see plan_meal (opt-in flag, default off) and approve_weekly_plan
    (the yes for a whole generated week). Shared by both so a meal's
    ingredients land on the list identically whether it was planned
    one-off in chat or arrived with an approved week.

    KNOWN LIMITATION: this runs when ingredients are ADDED to the list, so
    it reflects attendance as it stood at approval. Changing a meal's
    headcount after the week is approved does not re-quantify what's
    already on the list — the line stays at the number it was bought for.
    (The away path is different, and does reverse post-approval: an away
    slot's ingredients are taken back off, because "nothing bought" is a
    promise rather than an estimate.) Re-scaling an approved line means
    reversing and re-adding a partial contribution, which is the
    swap-a-meal machinery, not this function's. Worth doing; deliberately
    not smuggled into this change.
    """
    from . import attendance as _attendance

    # Each meal's own headcount factor, so attendance can say how many it
    # feeds. A missing entry (an ad hoc add, a row since deleted) simply
    # doesn't scale rather than failing the shop.
    entry_conn = get_conn()
    scaled_for_entry: dict[int, list[dict]] = {}
    for entry_id in entry_ids:
        entry_row = entry_conn.execute(
            "SELECT date, slot FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
        scale = (
            _attendance.grocery_scale_factor(entry_row["date"], entry_row["slot"])
            if entry_row else 1.0
        )
        scaled_for_entry[entry_id] = (
            _attendance.scale_ingredients(recipe_ingredients, scale)
            if scale != 1.0 else recipe_ingredients
        )
    entry_conn.close()
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
    def _record_link(entry_id: int, item: str, grocery_item_id: int, qty: str) -> None:
        # Record exactly what THIS entry contributed to that grocery
        # line, before it got merged with anything else already there —
        # see _reverse_meal_grocery_contributions, which is what lets
        # swap_meal_in_plan/swap_component_in_plan take this back out
        # precisely if the meal is later swapped for something else.
        link_conn = get_conn()
        link_conn.execute(
            "INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
            "VALUES (?, ?, ?, ?, ?)",
            (household_id(), entry_id, grocery_item_id, item, _quantities._strip_prep_descriptor(qty or "")),
        )
        link_conn.commit()
        link_conn.close()

    for index, ing in enumerate(recipe_ingredients):
        if ing["item"].strip().lower() in have_names:
            already_have.append(ing["item"])
            continue
        # The recipe's own wording decides the path, before any headcount
        # scaling — scaling can only ever turn a package into the same
        # package (you cannot buy two thirds of a jar), so asking the
        # unscaled quantity keeps the classification stable across meals.
        raw_qty = ing.get("qty", "") or ""
        if _quantities.package_unit(raw_qty):
            add_result = _grocery.add_grocery_item(
                ing["item"], quantity=raw_qty, category=ing.get("category", "other"), added_by="ai",
                source_weekly_plan_id=weekly_plan_id, quantity_mode="max",
            )
            for entry_id in entry_ids:
                _record_link(entry_id, ing["item"], add_result["item_id"], raw_qty)
        else:
            for entry_id in entry_ids:
                # This meal's own scaled copy of the same ingredient, by
                # position — scale_ingredients preserves order and length.
                scaled = scaled_for_entry[entry_id][index]
                add_result = _grocery.add_grocery_item(
                    scaled["item"], quantity=scaled.get("qty", ""), category=scaled.get("category", "other"),
                    added_by="ai", source_weekly_plan_id=weekly_plan_id,
                )
                _record_link(entry_id, scaled["item"], add_result["item_id"], scaled.get("qty", ""))
        # Once per ingredient, not once per meal: this is the list of
        # NAMES that landed on the shopping list, and approve_weekly_plan
        # counts it distinctly anyway.
        added_items.append(ing["item"])
    return added_items, already_have
