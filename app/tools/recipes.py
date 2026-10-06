"""
Recipes: adding, listing, scaling, feedback and cooking notes.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit
from ..db import get_conn, write
from ._shared import household_id
from . import grocery as _grocery
from . import household as _household
from . import quantities as _quantities
from . import bring_over as _bring_over
from . import spices as _spices

logger = logging.getLogger("home_manager")


def add_recipe_for_chat(*args, override: bool = False, **kwargs) -> dict:
    """
    add_recipe with the one refusal a person is owed in front of it: a
    recipe somebody at the table can't have is never saved (2026-09-30).
    Until then chat saved it, then plan_meal_for_chat refused to plan it,
    and the recipe was left behind in the box. agent.TOOL_FUNCTIONS points
    at this; everything else (the week's own saves, imports, the recipe
    pass) calls add_recipe itself, the shape plan_meal_for_chat has.

    `override` is the person's own "save it anyway" (a recipe kept for
    guests, say) — never the model's call. See
    allergen_gate.refuse_recipe_if_clashing for why this raises.
    """
    from . import allergen_gate as _allergen_gate
    name = kwargs.get("name")
    if name is None and args:
        name = args[0]
    ingredients = kwargs.get("ingredients")
    if ingredients is None and len(args) >= 2:
        ingredients = args[1]
    if isinstance(name, str):
        _allergen_gate.refuse_recipe_if_clashing(name, ingredients, override=override)
    return add_recipe(*args, **kwargs)


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
    source_url: str = "",
    source_book: str = "",
    source_author: str = "",
    source_page: str = "",
    details_pending: bool = False,
    dish_note: str = "",
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
    source_url is the web page a recipe was brought in from (the recipe
    import sheet sets it); leave it blank for anything generated or typed.
    source_book / source_author / source_page credit the cookbook a recipe
    came from ("Salt Fat Acid Heat", "Samin Nosrat", "212") — set them when
    the user names the book, even without the other two; leave blank
    otherwise. Every screen that shows the recipe says where it came from.
    details_pending=True saves a dish the week's menu pass chose but has
    not written up yet — no ingredients, no steps — with the planner's
    dish_note kept for the recipe pass that will (see
    agent.fill_pending_recipes_for_plan). Nothing else sets it.

    Before anything is written, every line's cooking amount is held to
    the per-serving ranges in _PLAUSIBLE_PER_SERVING at default_servings
    (settle_cooking_quantities): a line out of range gets a cook_qty the
    cook can trust, and its shopping qty is stored exactly as given.
    """
    # One household, one recipe per name. Checked here rather than at each
    # door because the door with no check was the one a person reaches by
    # talking — see existing_recipe_named. Resolved before get_conn: this
    # opens its own connection, and this repo has twice earned an
    # intermittent "database is locked" from a nested one.
    # Stripped on the way IN as well as on the way out. The rule trimmed
    # the query and the INSERT stored the name as typed, so a recipe saved
    # with a stray space was invisible to it and the reported bug came
    # straight back through the other door — found by review, measured.
    name = (name or "").strip()
    # The planner's items can carry an explicit null for any of these
    # (2026-09-28: `"cuisine": null` hit recipes.cuisine NOT NULL and failed
    # the whole week's draft). The columns are NOT NULL DEFAULT '' — a
    # missing value is the empty string, never None.
    notes = notes or ""
    cuisine = cuisine or ""
    main_protein = main_protein or ""
    advance_prep_notes = advance_prep_notes or ""
    default_servings = default_servings or 4
    ingredients = settle_cooking_quantities(ingredients or [], default_servings)

    # The check and the INSERT share one transaction, opened with BEGIN
    # IMMEDIATE so the write lock is held from the READ rather than from
    # the write. Asked on a connection of its own it is a TOCTOU: review
    # measured 6 of 20 simultaneous saves still writing a duplicate,
    # which is better than main's 20 of 20 and is not the invariant this
    # claims to be. `/api/recipes/add` is a sync def, so Starlette runs it
    # in a threadpool and two devices really are concurrent.
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        clash = existing_recipe_named(name, conn=conn)
        if clash:
            conn.rollback()
            raise DuplicateRecipeName(duplicate_recipe_message(clash["name"]))
    except DuplicateRecipeName:
        conn.close()
        raise
    except Exception:
        conn.rollback()
        conn.close()
        raise
    # A failed INSERT must give the write lock back. It sat outside the try
    # above, so on 2026-09-28 one IntegrityError left BEGIN IMMEDIATE's lock
    # held and every later write in the app failed "database is locked"
    # until the server restarted.
    try:
        cur = conn.execute(
            "INSERT INTO recipes (household_id, name, notes, ingredients_json, tags_json, food_groups_json, cuisine, main_protein, "
            "instructions_json, default_servings, prep_time_minutes, cook_time_minutes, advance_prep_notes, advance_prep_step_indices_json, "
            "source_url, source_book, source_author, source_page, details_pending, dish_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                household_id(), name, notes, json.dumps(ingredients), json.dumps(tags or []),
                json.dumps(food_groups or []), cuisine, main_protein,
                json.dumps(instructions or []), default_servings, prep_time_minutes, cook_time_minutes,
                advance_prep_notes, json.dumps(advance_prep_step_indices or []), source_url or "",
                (source_book or "").strip(), (source_author or "").strip(), (source_page or "").strip(),
                1 if details_pending else 0, (dish_note or "").strip(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    recipe_id = cur.lastrowid
    conn.close()
    citation = recipe_citation(source_url, source_book, source_author, source_page)
    return {
        "recipe_id": recipe_id, "name": name, "tags": tags or [], "food_groups": food_groups or [],
        "cuisine": cuisine, "main_protein": main_protein, "instructions": instructions or [],
        "default_servings": default_servings, "advance_prep_step_indices": advance_prep_step_indices or [],
        "details_pending": bool(details_pending),
        "source_url": source_url or "",
        "source_book": (source_book or "").strip(), "source_author": (source_author or "").strip(),
        "source_page": (source_page or "").strip(),
        "citation": citation,
    }



RECIPE_RATINGS = ("liked", "disliked")


class InvalidRecipeRating(ValueError):
    """
    A word outside RECIPE_RATINGS handed to mark_recipe_feedback as a rating.

    The same marker shape as cooker.InvalidMealStatus,
    chores.InvalidChoreStatus, grocery.InvalidGroceryStatus and
    attention.InvalidAttentionStatus: a ValueError subclass, so the route's
    except for it MUST come before the plain one, or the 400 that means "no
    recipe by that name" swallows a refusal that means "that isn't a
    verdict".

    THE TWO WORDS ARE WHAT THIS DOOR ACCEPTS, not every value the column
    takes, and the difference is deliberate. schema.sql documents
    recipes.rating as `'' | 'liked' | 'disliked'`, where '' is "no verdict
    yet" — a starting state, written by the column's own DEFAULT and by
    nothing else. Nothing in the app sends it: the Cook screen's buttons
    send the two, the chat tool's schema enumerates the two, and
    member_recipe_feedback.rating — which a solo night writes the same word
    into — carries CHECK(rating IN ('liked','disliked')) and cannot hold it
    at all. So "clear a verdict we already gave" is a feature nobody has
    asked for, and it is better refused outright than half-supported down a
    path where the household half of the write would land and the
    per-person half would raise. rating=None keeps meaning exactly what it
    has always meant — leave the rating alone, just add notes — and is
    checked before this.

    Emily changes the vocabulary by changing RECIPE_RATINGS, in one line.

    WHY THIS ONE IS WORSE THAN ITS FIVE SIBLINGS, measured on a real
    uvicorn 2026-09-26 before the guard. The other five wrote a word no
    screen reads and lost nothing. Here a recipe rated 'liked' and re-rated
    'teleported' came back carrying 'teleported': the verdict the household
    gave is COMMITTED over and gone, it stops counting as rated in the
    "what we know" score, it sorts as unrated in list_recipes, and — since
    the planner's filter is `rating != 'disliked'` — a dish the household
    explicitly rejected becomes a planning candidate again. Even 'Liked'
    with a capital is a different word to every one of those readers.

    And when the recipe's last cook was a solo night, the per-person write
    that follows hits that CHECK and raises, so the route answered 500
    "your data is fine" over a rating it had already destroyed. Nothing
    heals a row already carrying a third word: reaching one needed a
    hand-made request, and a migration inventing history is worse than
    leaving the handful that can only have come from somebody's curl.
    """


class DuplicateRecipeName(ValueError):
    """
    A save that would give one household two recipes under one name.

    Every lookup that resolves a recipe BY NAME — plan_meal, get_recipe,
    mark_recipe_feedback, update_recipe_details, the swap paths — is a
    fetchone() with no ORDER BY, so a second row under the same name is
    reachable by nothing: the older one wins every read and the newer one
    is saved and then invisible. repair_recipe_titles.py already refuses
    any rename that would produce that state and calls it the worse
    problem; this stops it being produced in the first place.

    A ValueError, so the chat loop surfaces it to the model as a tool
    error rather than a success — the same reasoning check_off_meal's
    status guard made (2026-09-16): _turn_wrote_anything counts a
    non-error result, so answering with a dict would let the assistant
    say "Saved" about a recipe that was never written. The cost is one
    error_events row per collision, which is the guard working.
    """


def existing_recipe_named(name: str, conn=None) -> dict | None:
    """
    This household's recipe of that name, or None — case-insensitively,
    because that is what a person means by "the same recipe" and what
    most of this module's own lookups already do.

    The one place the "one household, one recipe per name" rule is
    decided. It had been decided in four places instead, which is how
    they came to disagree: the import route answered 409, swap and
    big_meal skipped silently, week generation compared names
    case-SENSITIVELY (so `chicken tacos` beside `Chicken Tacos` made a
    second row), and the chat tool checked nothing at all. A fifth door
    should call this rather than write a fifth answer.

    Given a connection it reads on that one and neither commits nor
    closes, so a caller can ask the question and act on the answer inside
    one transaction — which is what stops `add_recipe`'s check being a
    TOCTOU. Left unset it opens and closes its own, as every other caller
    wants.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        row = conn.execute(
            "SELECT id, name FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?) "
            "ORDER BY id LIMIT 1",
            (household_id(), (name or "").strip()),
        ).fetchone()
    finally:
        if own:
            conn.close()
    return {"id": row["id"], "name": row["name"]} if row else None


def duplicate_recipe_message(existing_name: str) -> str:
    """The one sentence both doors say about a name that is already taken."""
    return (
        f"You already have a recipe called “{existing_name}” — "
        "change the name to keep both."
    )


def recipe_citation(source_url: str = "", source_book: str = "", source_author: str = "", source_page: str = "",
                    has_photo: bool = False) -> dict | None:
    """
    Where a recipe came from, said the one way every screen says it (Loop
    Board recipe-photo import, 2026-09-13). None for a recipe generated or
    typed in — those show no credit at all.

    A book:  {"kind": "book", "book", "author", "page", "text": "From Salt Fat Acid Heat, Samin Nosrat, p. 212"}
    A link:  {"kind": "link", "url", "host", "text": "From seriouseats.com"}

    `text` is the plain sentence; the shell renders `book` in italics from
    the parts. The book wins when both are set (a photo of a page that also
    carries a URL is still a book). Never "Source:" — DESIGN_SYSTEM §8.
    """
    book = (source_book or "").strip()
    author = (source_author or "").strip()
    page = (source_page or "").strip()
    url = (source_url or "").strip()
    if book or author or page or has_photo:
        parts = [book or "a cookbook"]
        if author:
            parts.append(author)
        if page:
            parts.append(("pp. " if re.search(r"[–\-]", page) else "p. ") + page)
        return {"kind": "book", "book": book, "author": author, "page": page, "text": "From " + ", ".join(parts)}
    if url:
        # The registered host only: lowercase, no scheme, no userinfo
        # ("https://evil.com@seriouseats.com/x" is seriouseats.com), no
        # port, no path, no leading www.
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        host = re.sub(r"^www\.", "", host)
        return {"kind": "link", "url": url, "host": host, "text": f"From {host}" if host else "From a link"}
    return None


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
        params.extend([existing["id"], household_id()])
        conn.execute(
            f"UPDATE recipes SET {', '.join(fields)} WHERE id = ? AND household_id = ?",
            params,
        )
        conn.commit()
    conn.close()
    return get_recipe(recipe_name)


def fill_recipe_details(
    recipe_name: str,
    ingredients: list[dict],
    instructions: list[str],
    default_servings: int,
    prep_time_minutes: int | None = None,
    cook_time_minutes: int | None = None,
    advance_prep_notes: str = "",
    advance_prep_step_indices: list[int] | None = None,
    new_name: str | None = None,
    household_changes: list[str] | None = None,
) -> dict:
    """
    The recipe pass's save: write a pending recipe out in full — the
    ingredient list the grocery list is built from and the steps the Cook
    screen shows — and clear details_pending. Every line's cooking amount
    goes through settle_cooking_quantities exactly as add_recipe's does,
    so a recipe written in two passes ends up in the same state as one
    written in one. The planner's prep/cook minutes are kept when the
    writer sent none.

    Not update_recipe_details, which backfills steps onto a recipe that
    already has ingredients and never touches them; this is the one door
    that writes ingredients onto a saved row, and it only opens for a row
    that is pending. A recipe already written up is left alone and
    returned as is — two approvals racing, or Cook's fill-in landing after
    the approval's, must not overwrite a good recipe with a second draft.
    """
    settled = settle_cooking_quantities(ingredients or [], default_servings)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, details_pending FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?)",
            (household_id(), recipe_name),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError(f"No saved recipe named '{recipe_name}'.")
        if not row["details_pending"]:
            conn.rollback()
            return get_recipe(recipe_name)
        # `new_name`: the draft's name with an allergen-free label taken off
        # ("Dairy-Free Pancakes" -> "Pancakes", allergen_gate.plain_dish_name)
        # — the name it was checked under. Renamed on the same row, so the
        # plan's entries (which point at the row) follow it. The caller has
        # made sure no other recipe already has that name.
        if new_name and new_name.strip() and new_name.strip().lower() != recipe_name.strip().lower():
            conn.execute(
                "UPDATE recipes SET name = ? WHERE id = ? AND household_id = ?",
                (new_name.strip(), row["id"], household_id()),
            )
            recipe_name = new_name.strip()
        conn.execute(
            "UPDATE recipes SET ingredients_json = ?, instructions_json = ?, default_servings = ?, "
            "prep_time_minutes = COALESCE(?, prep_time_minutes), "
            "cook_time_minutes = COALESCE(?, cook_time_minutes), "
            "advance_prep_notes = ?, advance_prep_step_indices_json = ?, details_pending = 0, "
            # "Changed for your household" (research-first writing, 2026-10-06).
            "household_changes_json = ? "
            "WHERE id = ? AND household_id = ?",
            (
                json.dumps(settled), json.dumps(instructions or []), int(default_servings or 4),
                prep_time_minutes, cook_time_minutes, advance_prep_notes or "",
                json.dumps(advance_prep_step_indices or []),
                json.dumps([str(c) for c in (household_changes or []) if str(c).strip()]),
                row["id"], household_id(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_recipe(recipe_name)


def planned_slot_for_recipe(recipe_name: str) -> str | None:
    """The slot this recipe is most recently planned in ('dinner',
    'lunch', ...), or None when it is on no plan."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT mpe.slot FROM meal_plan_entries mpe JOIN recipes r ON r.id = mpe.recipe_id "
            "WHERE r.household_id = ? AND LOWER(r.name) = LOWER(?) ORDER BY mpe.id DESC LIMIT 1",
            (household_id(), recipe_name),
        ).fetchone()
    finally:
        conn.close()
    return (row["slot"] or None) if row else None


def pending_recipes_for_plan(weekly_plan_id: int) -> list[dict]:
    """
    The recipes on this plan that the menu pass left unwritten, each once,
    with the slot it is planned in (a dish planned for two dinners is one
    recipe, written once). What agent.fill_pending_recipes_for_plan works
    through on approval; [] for a plan made of saved recipes, which is
    then no model call at all.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT r.id, r.name, r.cuisine, r.main_protein, r.tags_json, r.food_groups_json,
               r.prep_time_minutes, r.cook_time_minutes, r.dish_note,
               MIN(mpe.slot) AS slot
        FROM meal_plan_entries mpe
        JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND r.details_pending = 1
        GROUP BY r.id
        ORDER BY r.id
        """,
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "slot": r["slot"] or "dinner",
            "cuisine": r["cuisine"] or "",
            "main_protein": r["main_protein"] or "",
            "tags": json.loads(r["tags_json"] or "[]"),
            "food_groups": json.loads(r["food_groups_json"] or "[]"),
            "prep_time_minutes": r["prep_time_minutes"],
            "cook_time_minutes": r["cook_time_minutes"],
            "dish_note": r["dish_note"] or "",
        }
        for r in rows
    ]


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
               cook_time_minutes, advance_prep_notes, advance_prep_step_indices_json, source_url,
               source_book, source_author, source_page, details_pending, dish_note
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
    # The page photos a recipe was read from, if any (recipe photo import).
    # Imported lazily: app.recipe_photos reads this package's _shared, and
    # this package is still being assembled when this module is imported.
    from ..recipe_photos import photo_urls_by_recipe
    photos_by_recipe = photo_urls_by_recipe() if recipe_ids else {}

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
            # True for a dish the menu pass chose that the recipe pass has
            # not written up yet (see fill_recipe_details): ingredients and
            # instructions are empty on purpose, not by omission.
            "details_pending": bool(r["details_pending"]),
            "dish_note": r["dish_note"] or "",
            # The web page it was brought in from, or '' (recipe import).
            "source_url": r["source_url"] or "",
            # The cookbook it was photographed from, or '' (recipe photo import).
            "source_book": r["source_book"] or "",
            "source_author": r["source_author"] or "",
            "source_page": r["source_page"] or "",
            # Where it came from, ready to say — None for a generated or
            # typed recipe. Every screen that shows a recipe renders this
            # one field rather than re-deriving it from the four above.
            "citation": recipe_citation(
                r["source_url"], r["source_book"], r["source_author"], r["source_page"],
                has_photo=bool(photos_by_recipe.get(r["id"])),
            ),
            # The page photo(s) it was read from, in page order; [] otherwise.
            "photo_urls": photos_by_recipe.get(r["id"], []),
        }
        for r in rows
    ]


def list_recipes_for_planning(include_temporarily_excluded: bool = False) -> list[dict]:
    """
    A slimmed projection of list_recipes for feeding into weekly-plan
    generation: name, rating, cuisine, main_protein, tags, times_cooked,
    prep/cook time, food_groups, and recent_one_off_notes -- WITHOUT
    ingredients or instructions.

    The generation prompt tells the model to reuse a saved recipe by exact
    name rather than re-inventing its ingredients (see the is_new_recipe
    bullet in generate_weekly_plan_llm's instructions), which means the
    full recipe body -- everything list_recipes returns -- was pure
    overhead on every single generation call: measured at ~19.8k
    characters (~5.4k tokens) for a 13-recipe household, resent in full on
    every generation whether or not any of those recipes were even
    considered that week. recent_one_off_notes is kept even though it's
    not part of the "for choosing a recipe by name" core, because two
    separate prompt bullets (day-based and component-based) tell the model
    to weigh it when deciding whether to repeat a recipe -- dropping it
    would silently break that instruction rather than just trim tokens.

    A separate function rather than a parameter on list_recipes, so every
    other caller (recipe detail views, the Cooker, editing) keeps getting
    the full row untouched.
    """
    conn = get_conn()
    query = """
        SELECT id, name, rating, cuisine, main_protein, tags_json, times_cooked,
               prep_time_minutes, cook_time_minutes, food_groups_json
        FROM recipes WHERE household_id = ?
        {exclusion_clause}
        ORDER BY (rating = 'liked') DESC, (rating = 'disliked') ASC, times_cooked DESC, name ASC
        """.format(exclusion_clause="" if include_temporarily_excluded else "AND temporarily_excluded = 0")
    rows = conn.execute(query, (household_id(),)).fetchall()

    recipe_ids = [r["id"] for r in rows]
    notes_by_recipe: dict[int, list[str]] = {}
    if recipe_ids:
        placeholders = ",".join("?" * len(recipe_ids))
        note_rows = conn.execute(
            f"SELECT recipe_id, note FROM recipe_notes WHERE recipe_id IN ({placeholders}) "
            "ORDER BY created_at DESC",
            recipe_ids,
        ).fetchall()
        for nr in note_rows:
            notes_by_recipe.setdefault(nr["recipe_id"], [])
            if len(notes_by_recipe[nr["recipe_id"]]) < 3:
                notes_by_recipe[nr["recipe_id"]].append(nr["note"])
    conn.close()

    return [
        {
            "name": r["name"],
            "rating": r["rating"] or None,
            "cuisine": r["cuisine"] or None,
            "main_protein": r["main_protein"] or None,
            "tags": json.loads(r["tags_json"]),
            "times_cooked": r["times_cooked"],
            "prep_time_minutes": r["prep_time_minutes"],
            "cook_time_minutes": r["cook_time_minutes"],
            "food_groups": json.loads(r["food_groups_json"]),
            "recent_one_off_notes": notes_by_recipe.get(r["id"], []),
        }
        for r in rows
    ]


def saved_ingredients(name: str) -> list[dict]:
    """
    This household's saved recipe's ingredient list, by name
    (case-insensitively, the same rule existing_recipe_named applies), or
    [] when there is no such recipe.

    The ONE place a dish named without its list is read off its recipe
    before the allergen matcher sees it. Three doors need it — the week
    draft (allergen_gate), the swap and three-picks gate
    (swap_in_place._hard_clash) and the chat's change card (proposals) —
    and the verifier of 2026-09-21 found the swap gate matching a reused
    "Tropical Fruit Cup" on its name alone while its list held pineapple.
    One helper, so the three cannot drift apart again.
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return []
    for r in list_recipes():
        if (r.get("name") or "").strip().lower() == wanted:
            return list(r.get("ingredients") or [])
    return []


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


def recipe_shelf() -> list[dict]:
    """
    The household's saved recipes as a short list, A to Z — Settings →
    Recipes (Emily, 2026-10-04: "add back the function to add in your own
    recipe from a link and the recipe view and add it under the settings
    for now"). Light fields only: the id to open it by, the name, the
    minutes, and where it came from (recipe_citation's one sentence).

    Reads list_recipes, so it shows exactly what the planner can see —
    temporarily-excluded dishes included — with ONE exception: a dish the
    menu pass chose and the recipe pass hasn't written up yet
    (details_pending) has no ingredients and no steps, so there is nothing
    to open; it joins the list the moment approval writes it.
    """
    rows = [r for r in list_recipes() if not r.get("details_pending")]
    rows.sort(key=lambda r: ((r.get("name") or "").strip().lower(), r["id"]))
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "prep_time_minutes": r.get("prep_time_minutes"),
            "cook_time_minutes": r.get("cook_time_minutes"),
            "citation": r.get("citation"),
            "has_photo": bool(r.get("photo_urls")),
        }
        for r in rows
    ]


def get_recipe_by_id(recipe_id: int) -> dict | None:
    """
    One saved recipe in full, by its id, for the read-only recipe view in
    Settings → Recipes — or None when this household has no recipe with
    that id. Scoped through list_recipes (WHERE household_id = ?), so
    another household's id is simply not found.
    """
    for r in list_recipes():
        if r["id"] == recipe_id:
            return r
    return None


# The amount in brackets after a MEASURE is the same amount said another
# way — "2 cups (480 ml)" — and moves with it when the servings stepper
# does (Loop Board "Recipes people trust", slice 3, 2026-10-06: "rescales
# every ingredient amount, including the amounts in brackets"). After
# anything else — "1 can (400 g)", "1 stick (113 g)", "1 head (2 lbs)" —
# the bracket is the size of ONE of them, and two cans are still 400 g
# each. _parse_quantity carries either as a "(size)" suffix on the unit.
_MEASURE_UNITS = frozenset(
    u for group in _quantities._UNIT_CONVERSION_GROUPS for u in group
)


def _scaled_qty_text(scaled: float, unit: str | None, ratio: float) -> str:
    head, size = _quantities._split_package_size(unit)
    if size and head in _MEASURE_UNITS:
        restated = scale_steps([size.strip()], ratio)[0]
        return _quantities._format_quantity(scaled, head) + " " + restated
    return _quantities._format_quantity(scaled, unit)


def scale_recipe(recipe_name: str, target_servings: int) -> dict:
    """
    Scale a saved recipe's ingredient quantities from its default_servings
    to target_servings — e.g. cooking for 6 when the recipe is written for
    4. Quantities that parse cleanly (a number + a known unit) are scaled
    directly; anything freeform (like "a pinch" or "to taste") is left
    as-is rather than guessed, and flagged in unscaled_items so the Cooker
    knows to eyeball it themselves.

    Scaling happens on the COOKING amounts (see cooking_ingredients below),
    never on the shopping ones — this is the Cook screen's serving stepper
    and the chat's "cooking for 6 tonight", both of which are questions
    about the pan. Halving "1 bottle olive oil" used to produce "0.5
    bottles" (Julia, 2026-09-08); it now halves "2 tbsp" to "1 tbsp", and
    the untouched shopping amount rides along as `shopping_qty`.
    """
    recipe = get_recipe(recipe_name)
    base_servings = recipe["default_servings"] or 4
    if base_servings <= 0:
        base_servings = 4
    ratio = target_servings / base_servings

    scaled_ingredients = []
    unscaled_items = []
    for ing in cooking_ingredients(recipe["ingredients"], servings=base_servings):
        # cook_qty is the amount at base_servings and has already been read
        # into qty above; carrying it further would let a second pass scale
        # from the baseline again.
        ing.pop("cook_qty", None)
        parsed = _quantities._parse_quantity(ing.get("qty", ""))
        if parsed:
            amount, unit = parsed
            scaled = amount * ratio
            if unit == "stick" and abs(scaled - round(scaled)) > 1e-9:
                # A stick of butter cut to a fraction is measured in
                # tablespoons, not rounded back up to a whole stick — which
                # is how a four-person stick stayed a stick for two.
                scaled, unit = scaled * _STICK_TBSP, "tbsp"
            elif unit in _DISCRETE_UNITS:
                # "0.5 heads of garlic" is not an amount anyone measures.
                # Same rounding cooking_quantity applies, so the two agree
                # about a thing that only comes whole.
                scaled = max(1.0, round(scaled))
            scaled_ingredients.append({**ing, "qty": _scaled_qty_text(scaled, unit, ratio)})
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
        # The steps at the same size as the list above them (QA walk
        # 2026-10-02: a doubled lunch said "add 1 cup rice" over a "2 cups
        # Rice" chip). See scale_steps for what is and isn't rewritten.
        "scaled_instructions": scale_steps(
            recipe.get("instructions") or [],
            ratio,
            [ing.get("item") or "" for ing in recipe.get("ingredients") or [] if isinstance(ing, dict)],
        ),
    }


# ---------- amounts inside a step (QA walk, 2026-10-02) ----------
#
# A recipe's steps are written at its own default_servings, and every
# scaling pass in this app — a batch night, a night sized to who is
# eating, the Cook screen's stepper — rescaled the ingredient list and
# handed the steps over as written. On a doubled lunch that put "add 1 cup
# rice" directly above a "2 cups Rice" chip, on one screen, at the stove.
#
# scale_steps rewrites ONLY an amount it can tell is an amount of food:
#
#   - a number followed by a measuring word ("1 cup", "2 tbsp", "3 cloves",
#     "200g"), or
#   - a bare count followed by something on the ingredient list ("juice of
#     1 lime", "2 large eggs").
#
# Everything else is left exactly as written, because a step's numbers are
# mostly NOT amounts: oven temperatures, minutes, "1-inch pieces", a "9x13"
# dish, "divide among 4 bowls". A number straight after "a"/"an" or inside
# "(" is a SIZE ("a 12 oz can", "1 can (14 oz)"), and a measure followed by
# a vessel word ("2 quart saucepan") is a pot, so those are left too. The
# arithmetic is scale_recipe's own — the same ratio, the same whole-clove
# and stick-of-butter rules — so the step and the chip under it say one
# number. A miss leaves the recipe's own words, which is where every step
# was before this.

_STEP_FRACTION_CHARS = {
    "½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25, "¾": 0.75,
    "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}
# The fractions a step is written back out in — the same set and the same
# 0.05 tolerance as shell.js's humanQtyAmount, so "¾ cup" in a step and the
# "¾ cup Rice" chip under it come out of one rule.
_STEP_NICE_FRACTIONS = (
    (1 / 8, "⅛"), (1 / 4, "¼"), (1 / 3, "⅓"), (3 / 8, "⅜"), (1 / 2, "½"),
    (5 / 8, "⅝"), (2 / 3, "⅔"), (3 / 4, "¾"), (7 / 8, "⅞"),
)

# Measuring words a step's amount can carry, as written -> (canonical unit
# for scale_recipe's rules, singular form, plural form). Abbreviations keep
# their one spelling. Deliberately NOT here: pint/quart/inch (pots and
# pieces far more often than food), "c" and "pinch"/"dash" (freeform).
_STEP_UNIT_WORDS: dict[str, tuple[str, str, str]] = {}
for _canon, _sing, _plur in (
    ("cup", "cup", "cups"),
    ("tbsp", "tablespoon", "tablespoons"),
    ("tsp", "teaspoon", "teaspoons"),
    ("lb", "pound", "pounds"),
    ("lb", "lb", "lbs"),
    ("oz", "ounce", "ounces"),
    ("g", "gram", "grams"),
    ("kg", "kilogram", "kilograms"),
    ("ml", "milliliter", "milliliters"),
    ("l", "liter", "liters"),
    ("clove", "clove", "cloves"),
    ("can", "can", "cans"),
    ("stick", "stick", "sticks"),
    ("slice", "slice", "slices"),
    ("sprig", "sprig", "sprigs"),
    ("head", "head", "heads"),
    ("bunch", "bunch", "bunches"),
    ("handful", "handful", "handfuls"),
):
    _STEP_UNIT_WORDS[_sing] = (_canon, _sing, _plur)
    _STEP_UNIT_WORDS[_plur] = (_canon, _sing, _plur)
for _abbr in ("tbsp", "tsp", "oz", "g", "kg", "ml", "l"):
    _STEP_UNIT_WORDS[_abbr] = (_abbr, _abbr, _abbr)

# A measure followed by one of these sizes the container, not the food.
_STEP_VESSEL_WORDS = {
    "can", "cans", "tin", "tins", "jar", "jars", "package", "packages", "pkg",
    "bag", "bags", "box", "boxes", "bottle", "bottles", "carton", "cartons",
    "container", "containers", "tub", "tubs", "block", "blocks", "pan", "pans",
    "pot", "pots", "saucepan", "skillet", "dish", "casserole", "sheet", "tray",
}
_STEP_SIZE_WORDS = ("large", "medium", "small", "whole")
# A number after one of these is a count of pieces or portions, not an
# amount of food ("cut into 8 slices", "divide among 4 bowls", "1 per
# person"); one before "per"/"each" is a per-portion amount, which stays
# the same however many portions there are.
_STEP_NOT_AN_AMOUNT_BEFORE = re.compile(r"\b(?:a|an|per|each|each of)$")
# ...and after these, a count of PIECES — but "into 4 cups water" is still
# an amount, so this one only stops a count or a slice.
_STEP_PIECES_BEFORE = re.compile(r"\b(?:into|among)$")
_STEP_PER_PORTION_AFTER = re.compile(
    r"(?:\s+[a-z]+){0,2}?\s*(?:per\b|(?:into|in|to)\s+each\b|each\s*(?:[.,;:)]|$))", re.IGNORECASE
)
# "1 can (14 oz)": the bracket sizes the can, so it is left alone.
_STEP_SIZED_CONTAINER = re.compile(
    r"\b(?:cans?|tins?|jars?|packages?|bags?|box(?:es)?|bottles?|cartons?|containers?|tubs?|blocks?)\s*$"
)

_STEP_NUM = (
    r"(?:\d+\s+\d+/\d+|\d+\s*[½⅓⅔¼¾⅛⅜⅝⅞]|\d+/\d+|\d+(?:\.\d+)?|[½⅓⅔¼¾⅛⅜⅝⅞])"
)
# The amount (or a range of two), not glued to a word, a dash, a decimal
# point or a number-and-slash on its left — so "9x13", "350-degree" and
# "1/2" read whole. "(" and a word-and-slash are let through for the metric
# twin of an amount ("1 cup (240 ml)", "2 cups/500 ml"), which scale_steps
# only rewrites when a measuring word follows.
_STEP_AMOUNT_RE = re.compile(
    r"(?<![\w.\-–])(?<!\d/)(?P<a>" + _STEP_NUM + r")(?:(?P<sep>\s*(?:-|–|to|\s+and\s+)\s*)(?P<b>" + _STEP_NUM + r"))?"
)
_STEP_UNIT_RE = re.compile(
    r"(?P<sp>\s*)(?P<unit>" + "|".join(sorted(map(re.escape, _STEP_UNIT_WORDS), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _step_number(text: str) -> float | None:
    t = text.strip()
    try:
        if t and t[-1] in _STEP_FRACTION_CHARS:
            whole = t[:-1].strip()
            return (float(whole) if whole else 0.0) + _STEP_FRACTION_CHARS[t[-1]]
        if "/" in t:
            parts = t.split()
            frac = parts[-1]
            num, den = frac.split("/")
            return (float(parts[0]) if len(parts) == 2 else 0.0) + float(num) / float(den)
        return float(t)
    except (ValueError, ZeroDivisionError):
        return None


def _step_amount_text(amount: float) -> str:
    """1.5 -> "1 ½", 0.75 -> "¾", 2 -> "2" — humanQtyAmount's rule."""
    whole = int(amount + 1e-9)
    frac = amount - whole
    if frac < 0.02:
        return str(whole)
    best, best_diff = None, 0.05
    for value, char in _STEP_NICE_FRACTIONS:
        diff = abs(value - frac)
        if diff < best_diff:
            best, best_diff = char, diff
    if best is None:
        return f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{whole} {best}" if whole else best


def _step_scaled(amount: float, ratio: float, unit: str | None, butter: bool = True) -> tuple[float, str | None]:
    """scale_recipe's arithmetic for one amount, so the step and the chip
    agree: a stick of butter cut to a fraction becomes tablespoons, and a
    thing that only comes whole stays whole. A stick of anything else
    (celery, cinnamon) is just counted."""
    scaled = amount * ratio
    if unit == "stick" and not butter:
        return max(1.0, float(round(scaled))), unit
    if unit == "stick" and abs(scaled - round(scaled)) > 1e-9:
        return scaled * _STICK_TBSP, "tbsp"
    if unit in _DISCRETE_UNITS:
        return max(1.0, float(round(scaled))), unit
    return scaled, unit


def _step_ingredient_forms(items: list[str]) -> list[str]:
    """The words that name an ingredient in a step — shell.js's
    cookIngredientNouns, plus the last two words ("chicken thighs")."""
    forms: set[str] = set()
    for item in items:
        base = re.sub(r"[^a-z0-9 ]+", " ", str(item or "").split(",")[0].lower()).strip()
        if not base:
            continue
        words = base.split()
        found = {base, words[-1], " ".join(words[-2:])}
        for f in list(found):
            found.add(f[:-1] if f.endswith("s") else f + "s")
            if f.endswith("es"):
                found.add(f[:-2])
            elif f.endswith("o"):
                found.add(f + "es")
        forms |= {f for f in found if len(f) >= 3}
    return sorted(forms, key=len, reverse=True)


_F_PLURALS = {"leaf": "leaves", "loaf": "loaves", "half": "halves", "knife": "knives"}
_F_SINGULARS = {v: k for k, v in _F_PLURALS.items()}
# -ies words whose singular is -ie, not -y.
_IE_SINGULARS = {"cookies", "brownies", "pies", "smoothies", "veggies", "sweeties"}


def _pluralize_noun(phrase: str, many: bool) -> str:
    head, sep, last = phrase.rpartition(" ")
    lower = last.lower()
    if many and not lower.endswith("s"):
        if lower in _F_PLURALS:
            last = _F_PLURALS[lower]
        elif lower.endswith("y") and len(lower) > 1 and lower[-2] not in "aeiou":
            last = last[:-1] + "ies"
        else:
            last = last + ("es" if lower.endswith(("o", "ch", "sh", "x")) else "s")
    elif not many and lower.endswith("s") and not lower.endswith("ss"):
        if lower in _F_SINGULARS:
            last = _F_SINGULARS[lower]
        elif lower.endswith("ies") and lower not in _IE_SINGULARS:
            last = last[:-3] + "y"
        else:
            last = last[:-2] if lower.endswith(("oes", "ches", "shes", "xes")) else last[:-1]
    return f"{head}{sep}{last}"


def scale_steps(steps: list, ratio: float, ingredient_items: list[str] | None = None) -> list:
    """The recipe's steps with each amount of food multiplied by `ratio`
    (see the block comment above for which numbers count). A ratio of 1, a
    non-positive ratio and a non-string step all come back as written."""
    steps = list(steps or [])
    if not ratio or ratio <= 0 or abs(ratio - 1) < 1e-9:
        return steps
    forms = _step_ingredient_forms(ingredient_items or [])
    has_butter = any("butter" in str(i or "").lower() for i in ingredient_items or [])
    noun_re = (
        re.compile(
            r"(?P<sp>\s+)(?P<size>(?:" + "|".join(_STEP_SIZE_WORDS) + r")\s+)?(?P<noun>"
            + "|".join(map(re.escape, forms)) + r")\b"
            r"(?P<tail>\s+(?:clove|breast|thigh|fillet|stalk|sprig|slice|piece|wedge)s?\b)?",
            re.IGNORECASE,
        )
        if forms else None
    )

    def rewrite(step: str) -> str:
        out, pos = [], 0
        for m in _STEP_AMOUNT_RE.finditer(step):
            if m.start() < pos:
                continue
            before = step[:m.start()].rstrip().lower()
            # "a 12 oz can", "an 8 inch pan" — a size, not an amount; "into 8
            # slices", "among 4 bowls" — a count of pieces.
            if _STEP_NOT_AN_AMOUNT_BEFORE.search(before):
                continue
            pieces = bool(_STEP_PIECES_BEFORE.search(before))
            prev = step[m.start() - 1] if m.start() else ""
            bracketed = prev in "(/"
            if prev == "(" and _STEP_SIZED_CONTAINER.search(before[:-1].rstrip()):
                continue
            a = _step_number(m.group("a"))
            b = _step_number(m.group("b")) if m.group("b") else None
            if a is None or (m.group("b") and b is None):
                continue
            rest = step[m.end():]
            unit_m = _STEP_UNIT_RE.match(rest)
            if unit_m:
                after = rest[unit_m.end():]
                next_word = re.match(r"[\s)]*([a-z]+)", after.lower())
                if next_word and next_word.group(1) in _STEP_VESSEL_WORDS:
                    continue  # "2 quart saucepan", "14 oz can"
                if _STEP_PER_PORTION_AFTER.match(after):
                    continue  # "1 cup into each bowl", "2 tbsp per person"
                written = unit_m.group("unit")
                canon, sing, plur = _STEP_UNIT_WORDS[written.lower()]
                if pieces and canon == "slice":
                    continue  # "cut into 8 slices"
                # Butter is the noun the stick is OF ("1 stick cold butter"),
                # or a bare "1 stick" in a recipe that lists butter.
                butter = bool(
                    re.match(r"\s*(?:of\s+)?(?:[a-z]+\s+)?butter\b", after.lower())
                    or (has_butter and re.match(r"\s*(?:[.,;:)]|$)", after))
                )
                new_a, new_unit = _step_scaled(a, ratio, canon, butter)
                new_b = _step_scaled(b, ratio, canon, butter)[0] if b is not None else None
                biggest = new_b if new_b is not None else new_a
                if new_unit != canon:
                    unit_text = new_unit  # a stick cut to tablespoons
                elif sing == plur:
                    unit_text = written
                else:
                    unit_text = plur if biggest > 1 else sing
                    if written[:1].isupper():
                        unit_text = unit_text[:1].upper() + unit_text[1:]
                amount = _step_amount_text(new_a)
                if new_b is not None:
                    amount += m.group("sep") + _step_amount_text(new_b)
                # "250ml" made "187 ½ml" reads as nothing; a space goes in
                # whenever the new amount has one of its own.
                sp = unit_m.group("sp") or (" " if new_unit != canon or not amount.isdigit() else "")
                out.append(step[pos:m.start()] + amount + sp + unit_text)
                pos = m.end() + unit_m.end()
                continue
            noun_m = noun_re.match(rest) if noun_re and not bracketed and not pieces else None
            if not noun_m or _STEP_PER_PORTION_AFTER.match(rest[noun_m.end():]):
                continue
            # "2 garlic cloves" counts cloves, and a clove only comes whole —
            # the same rule scale_recipe gives "2 cloves garlic".
            whole_only = "clove" if (noun_m.group("tail") or "").strip().lower().startswith(("clove", "slice", "sprig")) else None
            new_a = _step_scaled(a, ratio, whole_only)[0]
            new_b = _step_scaled(b, ratio, whole_only)[0] if b is not None else None
            biggest = new_b if new_b is not None else new_a
            amount = _step_amount_text(new_a)
            if new_b is not None:
                amount += m.group("sep") + _step_amount_text(new_b)
            # The count word is the one that agrees with the number: "2 garlic
            # cloves", not "2 garlics clove". Only ever turned when the count
            # crosses one, so "2 eggs" made 4 is left as the recipe wrote it.
            noun, tail = noun_m.group("noun"), noun_m.group("tail") or ""
            was_many = (b if b is not None else a) > 1
            if was_many != (biggest > 1):
                if tail:
                    tail = _pluralize_noun(tail, biggest > 1)
                else:
                    noun = _pluralize_noun(noun, biggest > 1)
            out.append(step[pos:m.start()] + amount + noun_m.group("sp") + (noun_m.group("size") or "") + noun + tail)
            pos = m.end() + noun_m.end()
        out.append(step[pos:])
        return "".join(out)

    return [rewrite(s) if isinstance(s, str) else s for s in steps]


# ---------- cooking measurements (Julia, 2026-09-08) ----------
#
# "The recipe quantities are not specific enough. It's saying stuff like
# 'one bottle olive oil' which is incorrect. It should give actual
# measurements in the cooking view."
#
# She is right, and the "1 bottle" is not the model making something up —
# it is this app asking for it. generate_weekly_plan_llm's ingredient
# bullet tells the model to "write each ingredient's qty as how it's
# actually bought at the store, not how much ends up used once prepped",
# because that string IS the grocery line (see
# _add_recipe_ingredients_for_entries below, and quantities._PACKAGE_UNITS,
# which buys one bottle a week however many dinners name it). For the LIST
# that is correct and stays. For the COOK it never was — nobody pours a
# bottle of oil into a pan — and scale_recipe made it worse by halving it
# to "0.5 bottles".
#
# So a recipe ingredient carries two amounts, not one:
#
#   qty       — how it is BOUGHT. Unchanged; still what grocery reads.
#   cook_qty  — how much goes in the pan, at the recipe's default_servings.
#               Optional: where a recipe hasn't got one, the table below
#               derives it deterministically.
#
# Nothing in the grocery path reads cook_qty and nothing in the cook path
# shows a package word — that split is the whole fix. Both live inside
# ingredients_json, so there is no migration: an ingredient dict is stored
# as given.

COOKING_BASE_SERVINGS = 4

# Package words a cook can never act on. Rejected outright in a cooking
# quantity. Deliberately WIDER than quantities._PACKAGE_UNITS (the sealed-
# package set the grocery list buys once a week): that set answers "does
# one of these last a household a week", this one answers "can I put this
# in a pan", and a box of pasta fails the second while passing the first.
_COOKING_PACKAGE_UNITS = {
    "bag", "bottle", "box", "carton", "container", "jar", "pack", "packet",
    "punnet", "sachet", "tub",
}

# "1 can (14 oz)" is the exception a household really does cook from — a
# sized can of tomatoes IS the measurement. Kept for canned goods with a
# size on them; a can of olive oil is still nonsense (_NEVER_CANNED_WORDS).
_CANNED_UNITS = {"can", "tin"}

_NEVER_CANNED_WORDS = {
    "oil", "vinegar", "salt", "pepper", "spice", "powder", "seasoning",
    "flour", "sugar", "herbs",
}

# Kitchen units that state a real amount but don't convert, so
# quantities._measure_units() (tsp/tbsp/cup/oz/lb/g/kg/ml/l) doesn't hold
# them.
_EXTRA_MEASURED_UNITS = {
    "pint", "quart", "gallon", "stick", "clove", "slice", "sprig", "handful",
}

# Of those, the ones that only come whole: you use three cloves of garlic
# or two, never one and a half.
_DISCRETE_UNITS = {"stick", "clove", "slice", "sprig", "stalk", "head", "bunch"}

# Freeform amounts that are honest cooking instructions rather than
# vagueness — "salt to taste" is how recipes are written, and
# test_cook_ahead pins one.
_FREEFORM_COOKING_OK = {
    "to taste", "a pinch", "pinch", "a dash", "dash", "a splash", "splash",
    "as needed", "for serving", "for garnish", "to serve", "optional",
}

# Items a bare count says nothing about — there is no such thing as "2
# olive oil". A pepper is countable; pepper is not.
_NEEDS_MEASURE_WORDS = {
    "oil", "vinegar", "sauce", "syrup", "honey", "broth", "stock", "wine",
    "milk", "cream", "yogurt", "juice", "salt", "pepper", "spice", "powder",
    "flour", "sugar", "rice", "quinoa", "couscous", "oats", "butter",
    "paste", "extract", "seasoning", "breadcrumbs", "mayonnaise", "mayo",
    "mustard", "ketchup", "tahini", "hummus", "cheese", "lentils", "granola",
}

# The normalisation table: what one of this actually is, in the pan, for
# FOUR people (COOKING_BASE_SERVINGS), scaled linearly to whatever the card
# is really for.
#
# It exists so the fallback is deterministic rather than another model
# call: "1 bottle olive oil" becomes "2 tbsp olive oil" the same way every
# time, offline, in a test. Keys are matched whole-word against the
# ingredient name, longest first, so "black pepper" beats "pepper" and a
# "bell pepper" stays a vegetable instead of becoming a spice.
#
# A starting vocabulary, not a cookbook: the everyday staples a household's
# week is built from, plus every class the generation prompt itself calls
# out as a "leave qty blank on later recipes" staple (oils, vinegars,
# condiments, spices, salt, pepper, sugar) — those are precisely the ones
# that reach the cook view with no amount on them at all. Anything not
# here falls through to _CLASS_DEFAULTS rather than to a package word.
COOKING_QUANTITIES_PER_4 = {
    # fats and oils
    "olive oil": "2 tbsp", "extra virgin olive oil": "2 tbsp", "vegetable oil": "2 tbsp",
    "canola oil": "2 tbsp", "avocado oil": "2 tbsp", "coconut oil": "2 tbsp",
    "sesame oil": "1 tsp", "cooking oil": "2 tbsp", "butter": "2 tbsp", "ghee": "2 tbsp",
    # acids, condiments, sweeteners
    "vinegar": "1 tbsp", "balsamic vinegar": "1 tbsp", "red wine vinegar": "1 tbsp",
    "rice vinegar": "1 tbsp", "apple cider vinegar": "1 tbsp", "white vinegar": "1 tbsp",
    "soy sauce": "2 tbsp", "fish sauce": "1 tbsp", "worcestershire sauce": "1 tbsp",
    "hot sauce": "1 tsp", "sriracha": "1 tsp", "ketchup": "2 tbsp",
    "mustard": "1 tbsp", "dijon mustard": "1 tbsp", "mayonnaise": "2 tbsp",
    "honey": "1 tbsp", "maple syrup": "1 tbsp", "tomato paste": "2 tbsp",
    "tahini": "2 tbsp", "peanut butter": "2 tbsp", "pesto": "1/4 cup",
    "salsa": "1 cup", "hummus": "1 cup",
    # salt, pepper, dried spices and herbs
    "salt": "1 tsp", "kosher salt": "1 tsp", "sea salt": "1 tsp",
    "black pepper": "1/2 tsp", "white pepper": "1/4 tsp",
    "garlic powder": "1 tsp", "onion powder": "1 tsp", "paprika": "1 tsp",
    "smoked paprika": "1 tsp", "cumin": "1 tsp", "ground coriander": "1 tsp",
    "chili powder": "1 tsp", "cayenne": "1/4 tsp", "red pepper flakes": "1/2 tsp",
    "cinnamon": "1 tsp", "nutmeg": "1/4 tsp", "turmeric": "1 tsp",
    "curry powder": "1 tbsp", "garam masala": "1 tbsp", "italian seasoning": "1 tsp",
    "taco seasoning": "1 tbsp", "dried oregano": "1 tsp", "oregano": "1 tsp",
    "dried thyme": "1 tsp", "thyme": "1 tsp", "dried basil": "1 tsp",
    "rosemary": "1 tsp", "bay leaf": "1", "ground ginger": "1 tsp",
    "sesame seeds": "1 tbsp", "vanilla extract": "1 tsp",
    # baking and dry pantry
    "flour": "2 cups", "sugar": "1/2 cup", "brown sugar": "1/2 cup",
    "baking powder": "1 tsp", "baking soda": "1/2 tsp", "cornstarch": "1 tbsp",
    "breadcrumbs": "1 cup", "panko": "1 cup", "oats": "2 cups", "granola": "2 cups",
    "rice": "1.5 cups", "brown rice": "1.5 cups", "jasmine rice": "1.5 cups",
    "quinoa": "1 cup", "couscous": "1 cup", "pasta": "12 oz", "spaghetti": "12 oz",
    "noodles": "12 oz", "lentils": "1 cup", "almonds": "1/2 cup",
    "walnuts": "1/2 cup", "chia seeds": "2 tbsp",
    # dairy
    "milk": "1 cup", "heavy cream": "1/2 cup", "sour cream": "1/2 cup",
    "yogurt": "1 cup", "greek yogurt": "1 cup", "cream cheese": "4 oz",
    "cottage cheese": "1 cup", "parmesan": "1/2 cup", "cheddar": "1 cup",
    "mozzarella": "1 cup", "feta": "1/2 cup", "goat cheese": "1/2 cup",
    "cheese": "1 cup",
    # produce sold by the bag or bunch but cooked by volume
    "spinach": "4 cups", "baby spinach": "4 cups", "kale": "4 cups",
    "arugula": "4 cups", "mixed greens": "6 cups", "lettuce": "6 cups",
    "romaine": "1 head", "cabbage": "4 cups", "coleslaw mix": "4 cups",
    "cilantro": "1/4 cup", "parsley": "1/4 cup", "fresh basil": "1/4 cup",
    "dill": "2 tbsp", "chives": "2 tbsp", "green onions": "4",
    "mushrooms": "8 oz", "cherry tomatoes": "1 cup", "broccoli": "4 cups",
    "cauliflower": "4 cups", "green beans": "1 lb", "peas": "2 cups",
    "corn": "2 cups", "carrots": "3", "celery": "3 stalks",
    "potatoes": "1.5 lb", "sweet potatoes": "1.5 lb", "onion": "1",
    "red onion": "1", "garlic": "3 cloves", "ginger": "1 tbsp",
    "bell pepper": "2", "jalapeno": "1", "lemon": "1", "lime": "1",
    "avocado": "2", "cucumber": "1", "zucchini": "2", "blueberries": "2 cups",
    "strawberries": "2 cups", "banana": "2", "apple": "2",
    # proteins
    "chicken breast": "1.5 lb", "chicken thighs": "1.5 lb", "chicken": "1.5 lb",
    "ground beef": "1 lb", "ground turkey": "1 lb", "ground pork": "1 lb",
    "steak": "1.5 lb", "pork chops": "4", "salmon": "1.5 lb", "shrimp": "1 lb",
    "white fish": "1.5 lb", "tofu": "14 oz", "tempeh": "8 oz", "eggs": "4",
    "egg whites": "1 cup", "bacon": "6 slices", "sausage": "1 lb",
    "deli turkey": "8 oz",
    # liquids and canned goods
    "broth": "4 cups", "chicken broth": "4 cups", "vegetable broth": "4 cups",
    "beef broth": "4 cups", "stock": "4 cups", "white wine": "1/2 cup",
    "coconut milk": "1 can (14 oz)", "crushed tomatoes": "1 can (28 oz)",
    "diced tomatoes": "1 can (14 oz)", "tomato sauce": "1 can (14 oz)",
    "black beans": "1 can (15 oz)", "chickpeas": "1 can (15 oz)",
    "kidney beans": "1 can (15 oz)",
    # carriers
    "tortillas": "8", "bread": "8 slices", "buns": "4", "pita": "4", "naan": "4",
}

_COOKING_KEYS_LONGEST_FIRST = sorted(COOKING_QUANTITIES_PER_4, key=len, reverse=True)

# What to say when the item isn't in the table at all. Keyed on a word in
# the ingredient's name and checked in order, so an unknown "chipotle
# aioli" still reads as a sauce rather than as a bottle. The last resort
# is the package word itself — a bag of some unknown thing is about two
# cups of it — because any honest measure beats showing a cook a package.
_CLASS_DEFAULTS = (
    (("oil",), "2 tbsp"),
    (("vinegar", "sauce", "syrup", "dressing", "marinade", "glaze", "aioli"), "2 tbsp"),
    (("spice", "powder", "seasoning", "seeds"), "1 tsp"),
    (("juice", "milk", "broth", "stock"), "1 cup"),
    (("cheese", "yogurt", "cream"), "1 cup"),
    (("greens", "lettuce", "spinach", "salad"), "4 cups"),
    (("beans", "rice", "grain", "pasta", "flour", "sugar"), "1 cup"),
)

_PACKAGE_WORD_DEFAULTS = {
    "bottle": "2 tbsp", "jar": "2 tbsp", "sachet": "1 tsp", "packet": "1 tsp",
    "tub": "1 cup", "container": "1 cup", "carton": "2 cups", "bag": "2 cups",
    "box": "2 cups", "pack": "1 cup", "punnet": "1 cup",
}


def _measured_units() -> set[str]:
    return _quantities._measure_units() | _EXTRA_MEASURED_UNITS


def _clean_item(item: str) -> str:
    """The ingredient name lowercased and stripped of the prep descriptor
    the grocery layer already ignores ("Baby spinach, chopped")."""
    return (item or "").split(",", 1)[0].strip().lower()


def _item_matches(text: str, word: str) -> bool:
    """Whole-word (plus simple plural) containment, so "salt" matches
    "kosher salt" but not "salted butter"."""
    return re.search(rf"(?<![a-z]){re.escape(word)}e?s?(?![a-z])", text) is not None


def _table_key(item: str) -> str | None:
    """The table key an ingredient name matches, longest key first ("black
    pepper" before "pepper", "garlic powder" before "garlic"), or None."""
    clean = _clean_item(item)
    if not clean:
        return None
    for key in _COOKING_KEYS_LONGEST_FIRST:
        if _item_matches(clean, key):
            return key
    return None


def _table_lookup(item: str) -> str | None:
    """The table's per-4-servings amount for an ingredient name."""
    key = _table_key(item)
    return COOKING_QUANTITIES_PER_4[key] if key else None


# Counted packs — Loop Board 2026-09-13, "Eggs · 4 dozen". Which table
# entries are bought by the pack and used by the piece, and which pack
# family (quantities._PACK_CONVERSION_GROUPS) each one is. Keyed by the
# TABLE key rather than a unit word so "garlic powder" (its own key) and
# "egg whites" (its own key, measured in cups) are never read as these.
_COUNTED_PACK_ITEMS = {"eggs": _quantities._EGGS_TO_EACH, "garlic": _quantities._GARLIC_TO_CLOVE}


def _counted_pack_share(item: str, core_qty: str, default_servings: int | None) -> tuple[float, str] | None:
    """
    What ONE meal of this recipe, at the recipe's own table, uses of a
    counted-pack ingredient — (amount, the pack's small unit) — or None
    when the ingredient isn't one, or its quantity isn't a count.

    A recipe that writes the piece ("4" eggs, "3 cloves") is taken at its
    word. A recipe that writes the PACK ("1 dozen", "1 head") is saying how
    the thing is bought, not how much of it the dish uses — every recipe
    the app wrote before 2026-09-13 says "1 dozen" for eggs, however many
    go in the pan — so the share is what the Cook screen already tells the
    cook to use: COOKING_QUANTITIES_PER_4's amount, scaled to the recipe's
    default_servings (the same derivation as cooking_quantity). The
    caller's per-meal attendance factor is applied on top, as for any
    per-portion amount. Only when the table has no count for it does the
    pack's own size stand ("1 dozen" = 12).

    A measured amount ("1 cup", "2 tbsp minced") is not a count of the
    thing and is left to the ordinary path.
    """
    key = _table_key(item)
    group = _COUNTED_PACK_ITEMS.get(key)
    if not group:
        return None
    parsed = _quantities._parse_quantity(core_qty)
    if not parsed:
        return None
    amount, unit = parsed
    small_unit, pack_unit = _quantities._pack_units(group)
    if unit is None and key == "eggs":
        unit = small_unit  # "4" of Eggs is four eggs
    if unit == small_unit:
        return amount, small_unit
    if unit != pack_unit:
        return None
    used = _quantities._parse_quantity(COOKING_QUANTITIES_PER_4[key])
    if used and (used[1] or small_unit) == small_unit:
        table = default_servings or COOKING_BASE_SERVINGS
        return used[0] * table / COOKING_BASE_SERVINGS, small_unit
    return amount * group[pack_unit], small_unit


def _needs_measure(item: str) -> bool:
    """
    True for a substance a bare count says nothing about — oil, salt,
    flour, broth.

    The table gets a veto but not a vote: an item it counts ("carrots":
    "3", "bell pepper": "2") is countable, which is what stops "pepper"
    matching "bell pepper" and turning a vegetable into a spice. It does
    NOT work the other way round — the table measuring lettuce in cups
    doesn't make "1 head" wrong, because a head is something a cook can
    act on. Only the keyword list can say an item genuinely needs a
    measure.
    """
    clean = _clean_item(item)
    if not clean:
        return False
    table = _table_lookup(item)
    if table is not None:
        parsed = _quantities._parse_quantity(table)
        if parsed and (parsed[1] is None or parsed[1] in _DISCRETE_UNITS):
            return False
    return any(_item_matches(clean, word) for word in _NEEDS_MEASURE_WORDS)


def _is_canned_good(item: str) -> bool:
    """
    Whether "1 can" is a sane thing to say about this ingredient at all.

    The table decides where it has an opinion — it measures paprika in
    teaspoons and tomatoes by the can, so a tin of paprika is a package
    word and a can of tomatoes is a measurement. An item the table has
    never heard of is given the benefit of the doubt (a can of something
    unfamiliar is probably genuinely canned), unless it is one of the
    things that plainly never comes in one.
    """
    table = _table_lookup(item)
    if table is not None:
        parsed = _quantities._parse_quantity(table)
        return bool(parsed and parsed[1] and parsed[1].partition(" (")[0] in _CANNED_UNITS)
    return not any(_item_matches(_clean_item(item), w) for w in _NEVER_CANNED_WORDS)


def _quantity_problem(item: str, qty: str) -> str | None:
    """
    Why this quantity can't be cooked from, or None if it can. The single
    rule both validate_measured_quantities and cooking_ingredients ask, so
    the validator and the repair can never disagree about what is wrong.
    """
    text = (qty or "").strip()
    if not text:
        return "missing"
    if text.lower() in _FREEFORM_COOKING_OK:
        return None
    parsed = _quantities._parse_quantity(text)
    if not parsed:
        return "unmeasured"
    _amount, unit = parsed
    if unit is None:
        # A bare count: fine for eggs and lemons, meaningless for oil.
        return "unmeasured" if _needs_measure(item) else None
    head, size = _quantities._split_package_size(unit)
    word = head.rpartition(" ")[2]
    if word in _COOKING_PACKAGE_UNITS:
        return "package_unit"
    if word in _CANNED_UNITS:
        if not _is_canned_good(item):
            return "package_unit"  # a can of olive oil is not a measurement
        return None if size else "unsized_can"
    if word in _measured_units():
        return None
    # Some other container word — head, bunch, loaf, stick. A real per-meal
    # amount for produce, still nonsense for a substance.
    return "unmeasured" if _needs_measure(item) else None


# ---------- plausibility (Emily, 2026-09-13) ----------
#
# "One stick of butter is a crazy amount for this whole recipe. how can we
# make sure it makes better judgement calls on this." Turkish-Style Lentil
# Soup, serves 2, on her phone: "1 stick Butter", "1 lb Carrots", "1 bunch
# Mint (fresh)".
#
# The stick was not a judgement call the model got wrong. The recipe was
# generated for the household's own table of two, as the prompt asks, and
# every qty on it is a SHOPPING line, as the prompt also asks ("how it's
# actually bought at the store" — butter comes as a stick). The cook view
# is meant to swap a shopping line for a cooking amount, and it does for
# package words (cooking_ingredients below), but "stick" sits in
# _EXTRA_MEASURED_UNITS as a real kitchen unit, so _quantity_problem passed
# it through, and scale_recipe then kept it whole because a stick is
# discrete. Nothing anywhere asked whether the AMOUNT made sense for the
# number of people.
#
# This is that question, asked deterministically. Each ingredient class
# below carries a per-serving range in whichever families it is measured
# in; a cooking amount outside its class's range at the recipe's servings
# is replaced by the app's own amount for that item
# (COOKING_QUANTITIES_PER_4, scaled — the same table that already turns
# "1 bottle" into "2 tbsp", so the two corrections agree) or, when the
# table has never heard of the item, by the nearer bound of the range.
# No model call: the fix costs nothing and gives the same answer in a
# test as in production. It runs on every cooking amount the cook can
# see (cooking_ingredients, so recipes saved before today are covered)
# and once more before a recipe is saved (add_recipe /
# save_cooking_quantities write the corrected cook_qty down, so the saved
# recipe reads sensibly in chat too). The shopping qty is never touched:
# one stick is still what you buy.
#
# The ranges are deliberately generous — the job is to catch a stick in a
# two-person soup, not to second-guess a heavy hand. They are PER SERVING,
# so a cake for twelve with a cup of butter passes and the same cup for
# two does not. Every lower bound is zero: "too little" is not a complaint
# anyone has made, a false alarm here rewrites a line the cook may have
# meant (two ounces of bacon flavouring a soup is not a mistake), and the
# shape is kept so a floor can be added to one class without touching the
# rest.
#
# Each entry: (class, the words that put an ingredient in it, {family:
# (low, high) per serving}). Families are "tsp" (any volume), "g" (any
# weight), "count" (a bare number) and "clove". A family a class has no
# range for is not judged — a bunch of mint or a head of garlic is a whole
# thing this table has no opinion about. Words match whole (plus a plural)
# against the ingredient name, longest word first across ALL classes, so
# "garlic powder" is a spice and "garlic" an aromatic, and "salted butter"
# is a fat rather than salt. An item naming "fresh" anything is not a
# dried spice, and _NOT_THIS_CLASS lists the vegetables that borrow a
# class word ("sugar snap peas", "green beans").
_PLAUSIBLE_PER_SERVING = (
    ("fat", ("oil", "butter", "ghee", "lard", "fat", "shortening", "margarine"),
     {"tsp": (0, 6), "g": (0, 30)}),                       # up to 2 tbsp a head
    ("salt", ("salt",), {"tsp": (0, 1.5), "g": (0, 9)}),   # salted pasta water passes
    ("sugar", ("sugar", "honey", "maple syrup", "syrup", "molasses", "agave"),
     {"tsp": (0, 12), "g": (0, 50)}),                       # up to 1/4 cup a head: dessert
    # No bare-count ceiling here on purpose: "12 garlic knots" and "24
    # onion rings" are counted dishes wearing an aromatic's name, and a
    # count of onions the model gets wrong has not happened. Cloves and
    # spoonfuls are the aromatic amounts that go silly.
    ("aromatic", ("onion", "shallot", "garlic", "ginger", "leek"),
     {"clove": (0, 4), "tsp": (0, 3)}),
    ("spice", (
        "paprika", "cumin", "coriander", "turmeric", "cinnamon", "nutmeg", "cardamom",
        "allspice", "cayenne", "chili powder", "chilli powder", "chili flakes",
        "red pepper flakes", "pepper flakes", "black pepper", "white pepper",
        "peppercorns", "curry powder", "garam masala", "garlic powder", "onion powder",
        "seasoning", "oregano", "thyme", "rosemary", "sage", "sumac", "za'atar",
        "cumin seeds", "mustard seeds", "fennel seeds", "caraway", "five spice",
        "italian seasoning", "taco seasoning", "dried herbs",
    ), {"tsp": (0, 2), "g": (0, 6)}),
    ("protein", (
        "chicken", "beef", "pork", "lamb", "turkey", "veal", "salmon", "shrimp", "prawns",
        "fish", "cod", "tilapia", "haddock", "tuna", "trout", "tofu", "tempeh", "steak",
        "sausage", "bacon", "ham", "mince", "ground meat", "meatballs",
    ), {"g": (0, 454)}),                                    # up to 1 lb a head, bone-in
    ("grain", (
        "rice", "lentils", "quinoa", "couscous", "pasta", "spaghetti", "penne", "noodles",
        "oats", "oatmeal", "barley", "bulgur", "farro", "orzo", "polenta", "flour",
        "chickpeas", "split peas",
    ), {"tsp": (0, 96), "g": (0, 227)}),                    # up to 2 cups / 8 oz a head
)

# Names that borrow a class word and would be judged by the wrong table:
# vegetables and drinks, and the "low-fat"/"fat-free" of a dairy label.
# Matched as plain substrings of the cleaned name.
_NOT_THIS_CLASS = (
    "sugar snap", "green beans", "cauliflower rice", "ginger ale", "ginger beer",
    "onion rings", "garlic bread", "garlic knots", "ginger snap", "gingerbread",
    "low-fat", "low fat", "fat-free", "fat free", "nonfat", "non-fat", "full-fat", "full fat",
    "reduced-fat", "reduced fat",
)

# Kitchen units read into the families above, for the check only — the
# app's own unit arithmetic (quantities._UNIT_CONVERSION_GROUPS) never
# crosses cups into millilitres, and should not; this is a plausibility
# judgement, not a measurement.
_CHECK_VOLUME_TSP = {
    "tsp": 1.0, "tbsp": 3.0, "cup": 48.0, "pint": 96.0, "quart": 192.0, "gallon": 768.0,
    "ml": 1 / 4.929, "l": 1000 / 4.929,
}
_CHECK_WEIGHT_G = {"g": 1.0, "kg": 1000.0, "oz": 28.35, "lb": 453.6}

# A stick of butter is half a cup. Read as tablespoons for the fat class
# only — a cinnamon stick is a stick of nothing this table measures — and
# written back as tablespoons by scale_recipe whenever a stick would have
# to be cut into a fraction nobody measures.
_STICK_TBSP = 8


def _ingredient_class(item: str) -> tuple[str, dict] | None:
    """(class name, its per-serving ranges) for an ingredient, or None when
    the table has no opinion about it."""
    clean = _clean_item(item)
    if not clean:
        return None
    best: tuple[int, str, dict] | None = None
    for name, words, ranges in _PLAUSIBLE_PER_SERVING:
        for word in words:
            if _item_matches(clean, word) and (best is None or len(word) > best[0]):
                best = (len(word), name, ranges)
    if best is None or any(phrase in clean for phrase in _NOT_THIS_CLASS):
        return None
    if best[1] == "spice" and "fresh" in clean.split():
        return None
    return best[1], best[2]


def _check_family(unit: str | None, klass: str) -> tuple[str, float] | None:
    """Which family a unit is judged in, and the factor that takes one of
    it to the family's base (tsp, g, or one) — or None for a unit the check
    has no reading of (a can, a bunch, a head, freeform)."""
    head = (unit or "").partition(" (")[0]
    if not head:
        return "count", 1.0
    if head in _CHECK_VOLUME_TSP:
        return "tsp", _CHECK_VOLUME_TSP[head]
    if head in _CHECK_WEIGHT_G:
        return "g", _CHECK_WEIGHT_G[head]
    if head == "clove":
        return "clove", 1.0
    if head == "stick" and klass == "fat":
        return "tsp", _STICK_TBSP * 3.0
    return None


def _servings_or_base(servings: int | None) -> int:
    """add_recipe's documented default: quantities with no stated table are
    written for four."""
    return int(servings) if servings and servings > 0 else COOKING_BASE_SERVINGS


def implausible_quantity(item: str, qty: str, servings: int | None = None) -> dict | None:
    """
    Why a COOKING amount is out of range for `servings` people, or None
    when it is fine (or when the table has no opinion — an unknown item, a
    unit the check can't read, freeform text).

    Returns {"class", "family", "per_serving", "low", "high"} so a caller
    can say what was wrong in words; the per-serving figure is in the
    family's base (tsp, g, or a count).
    """
    klass = _ingredient_class(item)
    if not klass:
        return None
    name, ranges = klass
    parsed = _quantities._parse_quantity((qty or "").strip())
    if not parsed:
        return None
    amount, unit = parsed
    family = _check_family(unit, name)
    if not family or family[0] not in ranges:
        return None
    per_serving = amount * family[1] / _servings_or_base(servings)
    low, high = ranges[family[0]]
    if low <= per_serving <= high:
        return None
    return {"class": name, "family": family[0], "per_serving": per_serving, "low": low, "high": high}


def plausible_cooking_quantity(item: str, qty: str, servings: int | None = None, shopping_qty: str = "") -> str:
    """
    `qty` itself when it is a sensible cooking amount for `servings`
    people, otherwise the amount the cook should see instead: the app's
    own figure for that item scaled to the table (cooking_quantity), or —
    for an item the table has never met — the nearer bound of its class's
    range, written in the unit the line came in (tablespoons for a stick).

    A BARE COUNT is the second question (per_person_count_problem, further
    down): nine apples is not a measured amount out of range, it is a
    count a dish for three never uses. One entry point rather than two, so
    the cook view and the grocery list cannot end up holding different
    opinions about the same line.
    """
    problem = implausible_quantity(item, qty, servings)
    if not problem:
        return plausible_count_quantity(item, qty, servings)
    table = cooking_quantity(item, servings=_servings_or_base(servings), shopping_qty=shopping_qty)
    if table and not implausible_quantity(item, table, servings):
        return table
    amount, unit = _quantities._parse_quantity(qty.strip())
    family, factor = _check_family(unit, problem["class"])
    bound = problem["high"] if problem["per_serving"] > problem["high"] else problem["low"]
    total = bound * _servings_or_base(servings)
    if (unit or "").partition(" (")[0] == "stick":
        unit, factor = "tbsp", 3.0
    fixed = total / factor
    if family in ("count", "clove"):
        fixed = max(1.0, round(fixed))
    return _quantities._format_quantity(round(fixed, 3), unit)


def _implausible_lines(ingredients: list[dict], servings: int | None, as_written: bool = False) -> list[dict]:
    """
    The lines of a recipe whose cooking amount is out of range for
    `servings` — each with what the cook view shows instead:
    [{"index", "item", "qty", "shows", "problem"}], `index` being the
    line's position in `ingredients` — two lines can share a name.

    Which amount is judged: by default the one the cook view would read
    (the stored cook_qty where there is a measured one, else the qty
    itself where THAT measures something — cooking_ingredients' own
    precedence), which is what the pre-save pass wants. With `as_written`
    it is the qty line itself, cook_qty or no — what the model wrote, and
    what a cook would have read before the guard existed — which is what
    the plan-quality flag reports.
   
    TWO questions are asked of each line, and both answer in the same
    shape so there is one reader rather than two: a measured amount out of
    range for the table (implausible_quantity), and a bare COUNT past
    anything a dish for that table uses (per_person_count_problem,
    Gowthami 2026-10-04). A line can only be one or the other — a count is
    not a measured amount — so the first answer wins and nothing is
    reported twice.
    """
    out = []
    for index, ing in enumerate(ingredients or []):
        if not isinstance(ing, dict):
            continue
        item = (ing.get("item") or "").strip()
        qty = (ing.get("qty") or "").strip()
        stored = (ing.get("cook_qty") or "").strip()
        read = stored if stored and not as_written and not _quantity_problem(item, stored) else qty
        if not item or not read or _quantity_problem(item, read):
            continue
        problem = (
            implausible_quantity(item, read, servings)
            or per_person_count_problem(item, read, servings)
        )
        if problem:
            out.append({
                "index": index, "item": item, "qty": read, "problem": problem,
                "shows": cooking_ingredients([ing], servings=servings)[0].get("qty") or "",
            })
    return out


def settle_cooking_quantities(ingredients: list[dict], servings: int | None) -> list[dict]:
    """
    The pre-save pass: the same ingredient dicts, with a cook_qty written
    onto any line whose cooking amount is out of range for `servings`
    (see _PLAUSIBLE_PER_SERVING) or whose bare count is past anything a
    dish for `servings` uses (see _PER_PERSON_COUNT_CEILING). Everything
    else is returned untouched — a line that measures sensibly gets no
    cook_qty it did not have, and the shopping qty is never rewritten
    here. Logged at INFO so a run of these is visible without a database.

    The shopping qty staying as written is not an oversight for a COUNT
    line, where the two amounts are the same number: the grocery ingest
    holds that line to the same rule on the way to the list
    (_add_recipe_ingredients_for_entries), which is what covers every
    recipe already saved as well as every one saved from here.
    """
    fixes = {line["index"]: line for line in _implausible_lines(ingredients, servings)}
    if not fixes:
        return ingredients
    out = []
    for index, ing in enumerate(ingredients):
        line = fixes.get(index)
        out.append({**ing, "cook_qty": line["shows"]} if line else ing)
    for line in fixes.values():
        logger.info(
            "Recipe quantity out of range for %s: %s '%s' (%s); the cook view shows %s",
            _servings_or_base(servings), line["item"], line["qty"], line["problem"]["class"], line["shows"],
        )
    return out


def implausible_quantity_message(line: dict, servings: int | None) -> str:
    """One clause for a line _implausible_lines flagged, in the app's own
    voice: "Butter '1 stick' is more than 2 would use — the cook view shows
    1 tbsp"."""
    much = "more" if line["problem"]["per_serving"] > line["problem"]["high"] else "less"
    return (
        f"{line['item']} '{line['qty']}' is {much} than {_servings_or_base(servings)} would use "
        f"— the cook view shows {line['shows']}"
    )


# ---------- produce counts that only make sense for a small kind (Emily, 2026-09-13) ----------
#
# "It says 6 cucumbers - does it mean the persian cucumbers? Because that
# makes sense, but 6 english cucumbers would be a crazy amount."
#
# The plausibility table above has no vegetable class on purpose: a count
# of onions the model gets wrong had not happened, and the table's job is
# to REWRITE a cooking amount, which is the wrong fix here — "6 cucumbers"
# was very likely six Persian ones, and turning it into "2" would buy the
# wrong amount of the right thing. What the line is missing is a WORD, not
# a number, and only the model knows which word; the generation prompt now
# asks for it whenever the count depends on the kind. This table is the
# deterministic catch for the times it forgets: the handful of produce
# where the ordinary full-size kind and a small kind are both bought by
# the count, and the count alone says which one was meant. It only ever
# FLAGS (plan_quality._produce_variety_named → the morning report); no
# amount and no name is rewritten by THIS table.
#
# "and the grocery list shows the line as the recipe wrote it" used to
# finish that sentence, and since 2026-10-04 it is no longer true in
# general: _PER_PERSON_COUNT_CEILING below does recompute a count past
# anything a dish for the table uses, and six of its words are these six
# nouns.
#
# THE TWO TABLES DISAGREE ABOUT TWO OF THOSE SIX NOUNS AND THAT IS NOT A
# DRIFT TO BE TIDIED — they answer different questions, so neither is the
# other's ceiling. This one asks "is this too many of the ORDINARY kind?",
# and its answer has to be generous, because a high count is the EVIDENCE
# that the small kind was meant. That one asks "is this more than a dish
# for this table uses, whatever the kind?" — so tomato is 1 there against
# 2 here, and apple 1 against 2. The other four happen to agree today
# (cucumber 1, potato 2, pepper 1.5, onion 1.5) and nothing holds them
# together: agreeing is a coincidence of two separate judgements, not a
# shared number, so do not read it as one and do not "fix" a future
# divergence. Both directions really happen: a bare "8 tomatoes" for four
# is two each, which this table passes and that one recomputes; "6 Persian
# cucumbers" is six each, which this table passes (a kind was named) and
# that one leaves alone for the same reason
# (_per_person_count_ceiling asks _produce_class, so a name that says a
# kind is never second-guessed by either). What the two share is only the
# standing-down rule, never the number. An earlier draft of this comment
# said "the two do not disagree", which is true of the names and false of
# the numbers.
#
# Each entry: (the bare noun, the words that still mean the ordinary kind,
# what the ordinary kind is called, the small kind to ask about or None,
# a per-serving ceiling for the ordinary kind). A name is judged only when
# its last word is the noun and EVERY other word is in the ordinary list
# or in _PRODUCE_GENERIC_WORDS — any other word ("Persian", "cherry",
# "baby", "green" on an onion) is the model naming a kind, and a kind
# named is never second-guessed.
# The ceilings are per serving and generous, like _PLAUSIBLE_PER_SERVING:
# a French onion soup for four with six onions passes; six cucumbers in a
# salad for four does not. Only a count is judged ("6", "6 large", "6
# each", "1 dozen") — a pound of tomatoes is a weight the kind does not
# change — and a qty note of "small" is the kind being said in the amount
# instead. The kind can be said anywhere in the name — "Persian
# cucumbers", "Cucumbers (Persian)", "Cucumbers, Persian" — and a word
# that only describes ("fresh", "sliced") says nothing either way.
_PRODUCE_COUNT_PER_SERVING = (
    ("cucumber", ("english", "field", "seedless", "hothouse", "greenhouse"),
     "English cucumbers", "Persian", 1),
    ("tomato", ("beefsteak", "vine", "on-the-vine", "vine-ripened", "field", "red", "heirloom",
                "hothouse", "greenhouse", "slicing"),
     "full-size tomatoes", "cherry or plum", 2),
    ("potato", ("russet", "yukon", "gold", "idaho", "baking", "white", "yellow"),
     "full-size potatoes", "baby", 2),
    ("pepper", ("bell", "red", "green", "yellow", "orange"),
     "bell peppers", "mini", 1.5),
    ("onion", ("yellow", "red", "white", "brown", "cooking", "spanish", "vidalia"),
     "full-size onions", "pearl", 1.5),
    ("apple", ("granny", "smith", "honeycrisp", "gala", "fuji", "macintosh", "mcintosh", "pink", "lady",
               "red", "green", "baking", "tart", "sweet"),
     "full-size apples", None, 2),
)

# A qty note that already says the small kind was meant ("6 small").
_SMALL_KIND_NOTES = ("small", "mini", "baby", "little")

# Words in a name that describe the thing without naming a kind — a
# "(fresh)" tag, a prep descriptor after a comma, "on the vine" — so they
# neither exempt the line nor count as the kind being spelled out.
_PRODUCE_GENERIC_WORDS = frozenset((
    "fresh", "ripe", "firm", "raw", "whole", "large", "medium", "organic", "local",
    "peeled", "sliced", "diced", "chopped", "halved", "quartered", "grated", "cubed", "thinly", "thin",
    "on", "the", "of", "and", "or",
))

# Units that are still a count of the thing itself, and how many each is.
_COUNT_UNITS = {"each": 1, "ct": 1, "count": 1, "pc": 1, "pcs": 1, "piece": 1, "pieces": 1, "dozen": 12}


def _produce_class(item: str) -> tuple | None:
    """(the _PRODUCE_COUNT_PER_SERVING entry, whether the name spelled the
    ordinary kind out) for an ingredient written as the ordinary kind, or
    None — an item the table has no opinion about, or one whose name
    already says a different kind, anywhere in it: "Persian cucumbers",
    "Cucumbers (Persian)" and "Cucumbers, Persian" all say it."""
    lowered = (item or "").strip().lower()
    # The noun is the last word of the name proper — before any
    # parenthetical or comma tail; those words are descriptors, judged
    # alongside the words in front of the noun.
    tail_words = re.findall(r"[a-zà-ÿ'-]+", " ".join(re.findall(r"\(([^)]*)\)", lowered)))
    proper = re.sub(r"\s*\([^)]*\)", "", lowered)
    proper, _comma, comma_tail = proper.partition(",")
    tail_words += re.findall(r"[a-zà-ÿ'-]+", comma_tail)
    words = proper.split()
    if not words:
        return None
    last = words[-1]
    singular = last[:-2] if last.endswith("oes") else last[:-1] if last.endswith("s") else last
    descriptors = words[:-1] + tail_words
    for entry in _PRODUCE_COUNT_PER_SERVING:
        noun, ordinary = entry[0], entry[1]
        if singular != noun:
            continue
        if all(word in ordinary or word in _PRODUCE_GENERIC_WORDS for word in descriptors):
            return entry, any(word in ordinary and word not in _PRODUCE_GENERIC_WORDS for word in descriptors)
        return None
    return None


def produce_count_problem(item: str, qty: str, servings: int | None = None) -> dict | None:
    """
    Why a bare COUNT of produce only makes sense for the small kind, or
    None when the count fits the ordinary kind for `servings` people (or
    when the name says which kind, the amount is not a count, or the table
    has no opinion). Returns {"noun", "ordinary", "small", "per_serving",
    "high"} so a caller can say so in words.
    """
    found = _produce_class(item)
    if not found:
        return None
    (noun, _ordinary, ordinary_name, small, high), named = found
    core, note = _quantities._split_quantity_note((qty or "").strip())
    if any(word in note.lower().split() for word in _SMALL_KIND_NOTES):
        return None
    parsed = _quantities._parse_quantity(core)
    if not parsed or (parsed[1] is not None and parsed[1] not in _COUNT_UNITS):
        return None
    count = parsed[0] * _COUNT_UNITS.get(parsed[1] or "", 1)
    per_serving = count / _servings_or_base(servings)
    if per_serving <= high:
        return None
    return {
        "noun": noun, "ordinary": ordinary_name, "small": small, "named": named,
        "per_serving": per_serving, "high": high,
    }


def produce_count_message(item: str, qty: str, problem: dict, servings: int | None) -> str:
    """One clause for a line produce_count_problem flagged, in the app's
    own voice: "Cucumbers '6' would be a lot of English cucumbers for 2 —
    Persian ones? The recipe should say which kind". A name that already
    says the ordinary kind ("English cucumbers") is not asked which kind —
    the count is simply a lot."""
    table = _servings_or_base(servings)
    item, qty = (item or "").strip(), (qty or "").strip()
    if problem["named"]:
        return f"{item} '{qty}' is a lot for {table}"
    ask = f"{problem['small']} ones? The recipe" if problem["small"] else "the recipe"
    return f"{item} '{qty}' would be a lot of {problem['ordinary']} for {table} — {ask} should say which kind"


# ---------- a count that is a per-person amount (Gowthami, 2026-10-04) ----------
#
# "The quantities are off for some of the grocery list items... it's
# assuming a whole 'apple' or 'tomato' for each one, when it's not a whole
# one per person per recipe so it's way too many."
#
# A SECOND question about a bare count, and deliberately not the one
# _PRODUCE_COUNT_PER_SERVING above asks. That table asks "does this count
# only make sense for the SMALL KIND?" — six cucumbers is six Persian ones
# or a crazy amount of English ones, only the model knows which, so it
# flags and nothing is rewritten. This one asks "is this count past
# anything a dish for this many people uses, whatever the kind?" — and
# that has an answer the app can act on, because the count it should have
# been is still a count, in the same unit, and no kind explains it. So
# this one RECOMPUTES, and it is the belt rather than the fix: the fix is
# attendance.count_scale_factor (a stated count is never multiplied above
# what the recipe wrote) and the prompt rule that a count is the DISH's.
#
# Each entry is (words, the most a plausible dish for ONE person uses).
# EVERY CEILING IS AT LEAST ONE WHOLE THING PER PERSON, which is the line
# the card itself draws ("> 1 tomato per person per meal, > 1 apple per
# person per snack") and the line this module's standing bias wants: this
# clamps a number DOWN, and the bias is the other way (see
# quantities._PACKAGE_UNITS — an extra line beats a missing dinner). So
# nothing a plausible recipe writes is overruled. A Greek salad for three
# with three tomatoes passes; an apple each for three people passes; a
# French onion soup for four with six onions passes. Twelve tomatoes for
# four does not, and neither does nine apples for three.
# A starting vocabulary, not a greengrocer's: the everyday produce a
# week's recipes are built from and bought by the count. Extend it when a
# real miss shows up.
#
# WHAT IT IS NOT: a weight ("1.5 lb potatoes"), a package ("1 bag"), a
# counted pack (eggs by the dozen, garlic by the head — _counted_pack_share
# owns those), a unit the count is OF rather than the thing itself ("3
# stalks", "1 head"), a spice-rack name, or a count of a named small kind
# ("Persian cucumbers", "6 small tomatoes"), which is the question the
# table above owns and must not be second-guessed here.
_PER_PERSON_COUNT_CEILING = (
    # flavourings and aromatics bought whole
    ("lemon", 1), ("lime", 1), ("shallot", 1.5), ("jalapeno", 1.5), ("jalapeño", 1.5),
    ("serrano", 1.5), ("habanero", 1.5), ("chili", 1.5), ("chilli", 1.5), ("chile", 1.5),
    ("onion", 1.5), ("leek", 1.5),
    # vegetables that bulk the dish out
    ("tomato", 1), ("cucumber", 1), ("pepper", 1.5), ("potato", 2), ("sweet potato", 2),
    ("carrot", 2), ("zucchini", 1.5), ("courgette", 1.5), ("eggplant", 1), ("aubergine", 1),
    ("beet", 1.5), ("beetroot", 1.5), ("parsnip", 2), ("turnip", 1.5), ("avocado", 1),
    # whole fruit, where one per person really is a snack
    ("apple", 1), ("banana", 1), ("orange", 1), ("pear", 1), ("peach", 1),
    ("nectarine", 1), ("plum", 2), ("kiwi", 1), ("mango", 1),
)

# The unit a bare count comes in. None is the ordinary one ("3"); the rest
# are ways of writing the same thing. `dozen` is deliberately absent,
# though _COUNT_UNITS above carries it: a dozen of anything is a PACK, and
# _counted_pack_share owns that question.
_PER_PERSON_COUNT_UNITS = frozenset({None, "each", "ct", "count", "pc", "pcs", "piece", "pieces"})

# A name that says a small kind outright, for the nouns
# _PRODUCE_COUNT_PER_SERVING does not cover. For the six it does cover,
# _produce_class answers this properly (it knows which words mean the
# ORDINARY kind); this list is the same idea for the rest.
_SMALL_KIND_WORDS = frozenset({
    "small", "mini", "baby", "little", "cherry", "grape", "pearl", "cocktail",
    "fingerling", "new", "mandarin", "clementine", "crab", "persian", "champagne",
})

# The nouns the kind question already owns, so this one can ask it there
# rather than keeping a second opinion about cucumbers.
_PRODUCE_KIND_NOUNS = frozenset(entry[0] for entry in _PRODUCE_COUNT_PER_SERVING)


def _per_person_count_ceiling(item: str) -> tuple[str, float] | None:
    """
    (the word matched, its per-serving ceiling) for an ingredient this
    table has an opinion about, longest word first so "sweet potato" beats
    "potato" — or None.

    None for a spice-rack name however its words read ("red pepper flakes"
    is not a count of peppers), and None for a name that says a SMALL KIND:
    six Persian cucumbers is six Persian cucumbers, and the 2026-09-13
    decision that a named kind is never second-guessed holds here too. For
    the six nouns _PRODUCE_COUNT_PER_SERVING covers that judgement is
    _produce_class's, which knows which words mean the ordinary kind; for
    the rest it is _SMALL_KIND_WORDS.
    """
    clean = _clean_item(item)
    if not clean or _spices.is_spice(item):
        return None
    best: tuple[str, float] | None = None
    for word, ceiling in _PER_PERSON_COUNT_CEILING:
        if _item_matches(clean, word) and (best is None or len(word) > len(best[0])):
            best = (word, ceiling)
    if best is None:
        return None
    noun = best[0]
    if noun in _PRODUCE_KIND_NOUNS:
        # The kind table's own reading: None there means the name named a
        # kind (or an unrelated noun), and either way this is not ours.
        return best if _produce_class(item) else None
    words = set(re.findall(r"[a-zà-ÿ'-]+", clean))
    return None if words & _SMALL_KIND_WORDS else best


def per_person_count_problem(item: str, qty: str, servings: int | None = None) -> dict | None:
    """
    Why a bare COUNT is past anything a dish for `servings` people uses, or
    None when it is fine (an amount that is not a bare count, an item the
    table has no opinion about, a named small kind, a count inside the
    ceiling).

    The shape is implausible_quantity's on purpose — {"class", "family",
    "per_serving", "low", "high"} — so one message function and one reader
    (_implausible_lines) serve both questions rather than two of each.
    `family` is always "count" here, which is what tells the fix below to
    stay in the line's own unit.
    """
    found = _per_person_count_ceiling(item)
    if not found:
        return None
    word, ceiling = found
    core, note = _quantities._split_quantity_note((qty or "").strip())
    if any(w in note.lower().split() for w in _SMALL_KIND_NOTES):
        return None   # "6 small" is the kind said in the amount
    parsed = _quantities._parse_quantity(core)
    if not parsed or parsed[1] not in _PER_PERSON_COUNT_UNITS:
        return None
    per_serving = parsed[0] / _servings_or_base(servings)
    if per_serving <= ceiling:
        return None
    return {
        "class": word, "family": "count", "per_serving": per_serving,
        "low": 0, "high": ceiling,
    }


def plausible_count_quantity(item: str, qty: str, servings: int | None = None) -> str:
    """
    `qty` itself when its bare count is a dish amount, otherwise the most a
    plausible dish for THIS recipe's own table uses: the ceiling for the
    matched word times `servings`, in the unit the line came in.

    THE CEILING AND NOT cooking_quantity, which is the one place this
    deliberately differs from its sibling plausible_cooking_quantity, and
    the difference is not an oversight. That one has nothing generous to
    fall back to — a measured amount out of range has no honest upper
    bound, so the app's own typical figure is the only answer available. A
    COUNT does have one, and the gap between the two is large: measured,
    cooking_quantity's answer for cucumbers is ONE at a table of two and
    still one at a table of four, where the ceiling allows two and four.
    Clamping a flagged line to a typical amount rather than to the
    plausible maximum would be a far bigger intervention than the card
    asks for ("beyond a sane per-person-per-meal amount ... recomputes"),
    it would make the answer stop moving with the table, and it would
    collide at full force with the 2026-09-13 decision that a count is a
    word short rather than a number wrong. The ceiling errs the way this
    whole module errs (quantities._PACKAGE_UNITS: an extra line beats a
    missing dinner).

    The unit never changes, which is also why cooking_quantity could not
    simply be preferred where it exists: its answer for potatoes is
    "1.5 lb", a weight, and turning a count line into a weight line would
    answer a different question and break the merge the grocery list does
    by name and unit.

    It FLOORS rather than rounds up, which is the one place this does not
    err generously and has to not: the ceiling is the maximum a plausible
    dish uses, so rounding past it would hand back a number this very
    function would flag again. Floored at one whole thing, because no
    recipe that names a count wants none of it.
    """
    problem = per_person_count_problem(item, qty, servings)
    if not problem:
        return qty
    core, note = _quantities._split_quantity_note((qty or "").strip())
    unit = (_quantities._parse_quantity(core) or (0, None))[1]
    fixed = max(1.0, float(int(problem["high"] * _servings_or_base(servings) + 1e-9)))
    return _quantities._with_note(_quantities._format_quantity(fixed, unit), note)


def cooking_quantity(item: str, servings: int | None = None, shopping_qty: str = "") -> str | None:
    """
    What actually goes in the pan for `item`, for `servings` people —
    derived deterministically from COOKING_QUANTITIES_PER_4, then the class
    defaults, then the package word itself. None only when there is
    genuinely nothing better to say than whatever the recipe already has.

    This is the deterministic fallback the recipe-fill path lands on when
    the model won't produce a measured line (agent.fill_in_recipe), and the
    same derivation the Cooker view uses for every recipe saved before
    cook_qty existed.
    """
    base = _table_lookup(item)
    if base is None:
        clean = _clean_item(item)
        for words, default in _CLASS_DEFAULTS:
            if any(_item_matches(clean, w) for w in words):
                base = default
                break
    if base is None and shopping_qty:
        parsed = _quantities._parse_quantity(shopping_qty)
        if parsed and parsed[1]:
            head, _size = _quantities._split_package_size(parsed[1])
            base = _PACKAGE_WORD_DEFAULTS.get(head.rpartition(" ")[2])
    if base is None:
        return None
    if not servings or servings == COOKING_BASE_SERVINGS:
        return base
    parsed = _quantities._parse_quantity(base)
    if not parsed:
        return base
    amount, unit = parsed
    head = (unit or "").partition(" (")[0]
    if head in _CANNED_UNITS:
        # You open a can or you don't. "0.5 cans (14 oz)" is not a smaller
        # amount of anything, it's a package word wearing a fraction.
        return base
    scaled = amount * servings / COOKING_BASE_SERVINGS
    if unit is None or unit in _DISCRETE_UNITS:
        # Things that come in whole units — half a bay leaf, 1.5 eggs, 1.5
        # cloves of garlic — read as precision nobody has. Round, and never
        # all the way down to nothing.
        scaled = max(1.0, round(scaled))
    return _quantities._format_quantity(round(scaled, 3), unit)


def validate_measured_quantities(ingredients: list[dict], field: str = "qty", servings: int | None = None) -> dict:
    """
    Check that ingredient quantities are amounts a person can measure into
    a pan — the recipe-side rule Julia's "one bottle olive oil" broke.

    Rejected: a package unit nobody cooks by (bottle, jar, bag, box, pack,
    carton, tub, container, sachet, punnet); a can or tin with no size on
    it, or one hung on an oil/vinegar/spice; a bare count of a substance
    ("2 olive oil"); a blank quantity; freeform text that isn't one of the
    few real cooking phrases ("to taste", "a pinch").

    Accepted: a measured unit (tsp, tbsp, cup, ml, l, g, kg, oz, lb, and
    the kitchen units that don't convert — pint, stick, clove, slice), a
    bare count of a countable thing ("2 lemons"), a sized can of a canned
    good ("1 can (14 oz) diced tomatoes"), and "to taste".

    Returns {"ok": bool, "problems": [{"item", "qty", "reason",
    "suggested"}]}, where `suggested` is what cooking_quantity would write
    instead — so a caller can repair a line without asking anyone twice.
    `field` (default "qty") lets the same rule check a stored cook_qty.

    With `servings`, a measured amount is also held to the per-serving
    ranges in _PLAUSIBLE_PER_SERVING and reported as "implausible", with
    the amount plausible_cooking_quantity would show instead. Without it
    (the default, and what the recipe-fill path asks) only the unit is
    judged, so no caller starts paying for a repair call over an amount
    the table can settle for free.
    """
    problems = []
    for ing in ingredients or []:
        # An ingredient list is normally dicts, but a caller handing this a
        # bare list of names has nothing to validate rather than a crash.
        if not isinstance(ing, dict):
            continue
        item = (ing.get("item") or "").strip()
        if not item:
            continue
        qty = (ing.get(field) or "").strip()
        reason = _quantity_problem(item, qty)
        if reason:
            problems.append({
                "item": item,
                "qty": qty,
                "reason": reason,
                "suggested": cooking_quantity(item, shopping_qty=qty),
            })
        elif servings and implausible_quantity(item, qty, servings):
            problems.append({
                "item": item,
                "qty": qty,
                "reason": "implausible",
                "suggested": plausible_cooking_quantity(item, qty, servings, shopping_qty=(ing.get("qty") or "")),
            })
    return {"ok": not problems, "problems": problems}


def cooking_ingredients(ingredients: list[dict], servings: int | None = None) -> list[dict]:
    """
    The same ingredient list rewritten so every quantity is one a cook can
    act on — what the Cooker view and scale_recipe show.

    A stored `cook_qty` wins (that is the measured amount the recipe-fill
    saved). Failing that, the existing qty is kept whenever it already
    measures something and replaced from cooking_quantity when it doesn't.
    The shopping amount is never lost — it moves to `shopping_qty` — since
    it is still the honest answer to "how much do I buy", a different
    question that stays the grocery list's.

    Whichever amount wins is then held to _PLAUSIBLE_PER_SERVING for
    `servings` people (plausible_cooking_quantity): "1 stick" of butter
    measures something, and is still not what goes into a soup for two.
    `servings` is the table the amounts are written for; None means the
    recipe's documented default of four.

    Never invents an amount it has no basis for: an unknown item with a
    blank qty comes back blank rather than guessed at.
    """
    out = []
    for ing in ingredients or []:
        if not isinstance(ing, dict):
            out.append(ing)
            continue
        item = (ing.get("item") or "").strip()
        qty = (ing.get("qty") or "").strip()
        stored = (ing.get("cook_qty") or "").strip()
        if stored and not _quantity_problem(item, stored):
            sane = plausible_cooking_quantity(item, stored, servings, shopping_qty=qty)
            out.append({**ing, "qty": sane, "shopping_qty": qty})
            continue
        if not _quantity_problem(item, qty):
            sane = plausible_cooking_quantity(item, qty, servings, shopping_qty=qty)
            out.append(dict(ing) if sane == qty else {**ing, "qty": sane, "shopping_qty": qty})
            continue
        suggested = cooking_quantity(item, servings=servings, shopping_qty=qty)
        out.append({**ing, "qty": suggested, "shopping_qty": qty} if suggested else dict(ing))
    return out


def save_cooking_quantities(recipe_name: str, cook_quantities: dict[str, str]) -> dict:
    """
    Write per-ingredient cooking amounts ({"Olive oil": "2 tbsp"}) onto a
    saved recipe, leaving every shopping qty exactly as it was — used by
    the recipe-fill path once the model's measured lines have been
    validated. An item name that isn't already on the recipe is ignored
    rather than appended: this corrects a recipe, it doesn't rewrite one.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, ingredients_json, default_servings FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?)",
        (household_id(), recipe_name),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No saved recipe named '{recipe_name}'.")
    by_item = {_clean_item(k): v for k, v in (cook_quantities or {}).items()}
    ingredients = json.loads(row["ingredients_json"] or "[]")
    for ing in ingredients:
        measured = (by_item.get(_clean_item(ing.get("item") or "")) or "").strip()
        if measured:
            ing["cook_qty"] = measured
    # The model's measured lines are held to the same per-serving ranges
    # as anything else before they are written down (Emily, 2026-09-13).
    ingredients = settle_cooking_quantities(ingredients, row["default_servings"])
    conn.execute(
        "UPDATE recipes SET ingredients_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(ingredients), row["id"], household_id()),
    )
    conn.commit()
    conn.close()
    return {"name": recipe_name, "ingredients": ingredients}


# Words a step names without the ingredient list ever having to.
_STEP_ONLY_WORDS = {"water", "ice"}


# ---------- does the method use what the list bought, in the amount it bought? ----------
#
# Gowthami's household, 2026-10-04: "it says use 3 cups of cashews for
# example, but then the actually steps doesn't use the 3 cups." The name
# half of the check below has always been able to see an ingredient no
# step touches. It could not see a step that touches it for a THIRD of
# what the list bought, and to a cook that reads as the same thing — a
# recipe you can't trust.
#
# EVERY NAME THIS SECTION DEFINES IS PREFIXED _CHECK_, and that is not
# decoration: the first cut of it called two of them _STEP_AMOUNT_RE and
# _STEP_VESSEL_WORDS, which are scale_steps' own, three hundred lines up.
# A module-level name defined twice means the LATER one wins for the whole
# file, so the batch rewriter started reading this section's pattern and
# raised on every step it was handed, and its vessel words were swapped
# for these without anything raising at all. _STEP_ names belong to the
# rewriter; _CHECK_ names belong here.
#
# EVERYTHING HERE FAILS QUIET, and that is the whole design rather than a
# caveat. A step amount this cannot read, a unit it cannot convert, an
# amount it cannot pin to one ingredient: all passed over, never guessed
# at. It is the bias the name half already states — a false "your amounts
# don't add up" on a good recipe is worse than a missed one, because a
# household that learns to click past this learns to click past the real
# one — and it is why the only thing that can ever be a finding here is a
# number the recipe wrote twice, in units that convert, disagreeing with
# itself.

# A number in a step is very often not an amount of food: it is a clock, a
# thermometer, a ruler or a dial. A closed list of the words that say so,
# because the alternative is reading "bake for 25 minutes" as twenty-five
# of something.
_CHECK_NOT_AN_AMOUNT = {
    "minute", "minutes", "min", "mins", "second", "seconds", "sec", "secs",
    "hour", "hours", "hr", "hrs", "day", "days", "week", "weeks",
    "degree", "degrees", "f", "c", "fahrenheit", "celsius",
    "inch", "inches", "cm", "mm", "foot", "feet",
    "percent", "serving", "servings", "person", "people", "portion", "portions",
    "time", "times", "batch", "batches", "step", "steps", "side", "sides",
    "layer", "layers", "half", "halves", "third", "thirds", "quarter", "quarters",
}

# ...and a measuring word in front of one of THESE sizes the vessel rather
# than what goes in it: "a 2 quart saucepan", "a 9 inch baking dish".
# Quart and litre are real measures ("1 quart of stock"), so they can't
# simply join the list above.
#
# DELIBERATELY NOT scale_steps' own _STEP_VESSEL_WORDS, and the _CHECK_
# prefix on both of this section's patterns is there to stop what happened
# on 2026-10-05: these two were first written under the batch rewriter's
# names, silently shadowed them for the whole module, and broke
# scale_steps outright. Two patterns serving two different QUESTIONS is
# fine; one name serving both is not. The rewriter's set is the
# PACKAGING words whose number sizes a container it must not scale ("1
# (14 oz) can"); this one is the COOKWARE a number can size instead ("2
# quart saucepan"). They overlap on pan/pot/dish and disagree on
# everything else, and merging them would add twenty words to what the
# rewriter refuses to scale — a change to the batch rewriter, which this
# check does not get to make.
_CHECK_VESSEL_WORDS = {
    "pan", "pot", "skillet", "saucepan", "dish", "tray", "sheet", "baking",
    "casserole", "dutch", "oven", "bowl", "ramekin", "tin", "mould", "mold",
    "plate", "board", "rack", "griddle", "wok", "pressure", "slow", "air",
    "container", "jar", "loaf", "pie", "cake", "muffin", "springform",
}

# The biggest number a step counts out of something you eat. Above it, a
# bare number with no unit on it is a dial ("sear at 450", "oven to 400"),
# never three dozen of anything — and a bare count is exactly the shape
# that would otherwise read an oven temperature as an ingredient amount.
_CHECK_MAX_COUNT = 36

# How far past an amount a step may name the thing it is an amount OF:
# "2 cups of the toasted cashews" is sixteen characters of prep words. The
# window ALSO stops at the next number and at any clause break.
#
# MEASURED, 2026-10-05, because the first version of this comment said all
# three of those were what keeps "2 cups rice and 1 cup chicken stock"
# from crediting the rice with both: only the NEXT-NUMBER stop is, and it
# has a test. Widening this cap to the whole clause, and removing the
# clause break outright, each redden nothing — they are belt and braces,
# and the reason no test can tell is that a mis-attribution under the
# single-amount rule in _amounts_add_up only ever makes this check
# QUIETER unless the victim's own amount is split across steps. Both are
# kept: a tighter window is the quiet direction, which is this module's
# whole stance.
_CHECK_AMOUNT_REACH = 48

_CHECK_CLAUSE_BREAK_RE = re.compile(r"[,;.:()\n]|\bthen\b|\buntil\b|\bwhile\b")

# Its own pattern rather than scale_steps' _STEP_AMOUNT_RE, because the two
# ask different things of a step: that one captures an amount OR A RANGE
# ("2-3 cloves") in named groups it substitutes a rescaled number into,
# and never looks at the word afterwards; this one wants the number and
# the measuring word right after it, and a range would read as one amount.
_CHECK_AMOUNT_RE = re.compile(
    r"(?<![\w./])(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*°?\s*([a-z]+)?"
)

# A model writes "1½ cups" as often as "1 1/2 cups", and a fraction this
# cannot read is a step amount this cannot see.
_VULGAR_FRACTIONS = {
    "\u00bd": " 1/2", "\u2153": " 1/3", "\u2154": " 2/3", "\u00bc": " 1/4",
    "\u00be": " 3/4", "\u215b": " 1/8", "\u215c": " 3/8", "\u215d": " 5/8",
    "\u215e": " 7/8",
}

# How far two amounts may differ and still be the same amount. Recipes
# round — a third of a cup is written "0.33 cup" as readily as "1/3" — so
# an exact comparison would report arithmetic nobody got wrong. A tenth is
# wide enough for that and nowhere near the halves, thirds and doubles
# that are the actual bug.
_CHECK_AMOUNT_TOLERANCE = 0.1


def _normalized_step(step: str) -> str:
    text = (step or "").lower()
    for glyph, ascii_form in _VULGAR_FRACTIONS.items():
        text = text.replace(glyph, ascii_form)
    return text


def _parse_step_number(amount_str: str) -> float | None:
    """"1 1/2", "3/4", "0.5" as a number. None for anything else."""
    try:
        if "/" in amount_str:
            parts = amount_str.split()
            if len(parts) == 2:
                num, den = parts[1].split("/")
                return float(parts[0]) + float(num) / float(den)
            num, den = amount_str.split("/")
            return float(num) / float(den)
        return float(amount_str)
    except (ValueError, ZeroDivisionError):
        return None


def _names_a_known_food(text: str) -> bool:
    """
    Whether a stretch of step text names food this app has a word for: the
    measurement table's own keys, which is the vocabulary the
    missing-from-list half already uses, plus the two things a step names
    without the list ever having to carry them. Asked only of an amount
    that matched no LISTED ingredient, so it runs for a handful of words
    rather than for every number in the recipe.
    """
    return any(_item_matches(text, word) for word in _STEP_ONLY_WORDS) or any(
        _item_matches(text, key) for key in COOKING_QUANTITIES_PER_4
    )


def _amounts_in_one_step(
    step: str, item_words: list[list[str]],
) -> tuple[list[tuple[int, float, str | None]], list[tuple[float, str | None]]]:
    """
    What one step's numbers are amounts OF: (which listed ingredient,
    amount, unit) for the ones that can be pinned down, and the bare
    (amount, unit) of the ones that can't.

    AN AMOUNT BELONGS TO THE NEAREST LISTED INGREDIENT AFTER IT AND TO NO
    OTHER, which is the rule that keeps this honest. "Toss with 2 tbsp
    olive oil and the cashews" names one amount, and it is the oil's;
    crediting every name in reach would hand the cashews two tablespoons
    and report a recipe that is perfectly correct. The cost is that "1 cup
    each of rice and quinoa" only ever credits the rice, which is the
    quiet direction.

    THE SECOND LIST IS WHY THIS DOESN'T CRY WOLF ON A BATCH COOKED IN TWO
    PANS, and it was a real false positive before it existed. "Brown 1 lb
    of ground beef... push aside and brown the remaining 1 lb" is two
    pounds of a two-pound list, and the second amount names nothing at all
    — the beef is three words back in the previous step. No positional
    rule can reach it, so the honest reading is that an amount nobody can
    pin down means the sum is INCOMPLETE, and a sum that might be short is
    not evidence of anything. See _amount_mismatches, which passes over
    any ingredient such an amount could have been more of.
    """
    text = _normalized_step(step)
    matches = list(_CHECK_AMOUNT_RE.finditer(text))
    found, strays = [], []
    for position, match in enumerate(matches):
        amount = _parse_step_number(match.group(1))
        if amount is None:
            continue
        word = (match.group(2) or "").strip()
        unit = None
        if word:
            if word in _CHECK_NOT_AN_AMOUNT:
                continue
            unit = _quantities._UNIT_ALIASES.get(
                word, _quantities._normalize_container_word(word)
            ) or None
            if unit not in _measured_units():
                # Not a measuring word at all — "4 chicken thighs", where
                # the word after the number is the ingredient itself. Read
                # as a bare count, and the window below starts at the word
                # rather than past it.
                unit = None
        # The window: from the end of what was matched to whichever comes
        # first of the next number, a clause break, and _CHECK_AMOUNT_REACH.
        start = match.end() if unit else match.start(2) if match.group(2) else match.end()
        stop = len(text) if position + 1 >= len(matches) else matches[position + 1].start()
        stop = min(stop, start + _CHECK_AMOUNT_REACH)
        window = text[start:stop]
        break_at = _CHECK_CLAUSE_BREAK_RE.search(window)
        if break_at:
            window = window[:break_at.start()]
        # A bare number above _CHECK_MAX_COUNT is a dial, not a count of
        # food — and a bare count is the one shape that would otherwise
        # read an oven temperature as an ingredient amount.
        if unit is None and amount > _CHECK_MAX_COUNT:
            continue
        # "2 quart saucepan", "1 large bowl": the word right after the
        # amount sizes the vessel, so the number is the pan's.
        words_after = window.split()
        if words_after and words_after[0] in _CHECK_VESSEL_WORDS:
            continue
        index = _nearest_listed_item(window, item_words)
        if index is not None:
            found.append((index, amount, unit))
        elif not _names_a_known_food(window):
            # It is an amount of SOMETHING and this cannot say what. An
            # amount of broth nobody listed is the missing_from_list
            # half's finding, not a stray; an amount of nothing nameable
            # is what makes a sum untrustworthy.
            strays.append((amount, unit))
    return found, strays


def _nearest_listed_item(window: str, item_words: list[list[str]]) -> int | None:
    """
    Which listed ingredient a step's amount is an amount of: the one most
    of whose name words are in the window, earliest wins a tie. Counting
    the words matched is what tells "chicken stock" from "chicken thighs"
    when the window says chicken stock — the first matches both on
    "chicken", the second matches on both its words. Two names that are
    equally good a match for the same words (a bell pepper and black
    pepper, where the window says only "pepper") go to the one listed
    first; black pepper is a rack item and exempt from this check anyway.
    """
    best = None
    for index, words in enumerate(item_words):
        hits = []
        for word in words:
            at = re.search(rf"(?<![a-z]){re.escape(word)}e?s?(?![a-z])", window)
            if at:
                hits.append(at.start())
        if not hits:
            continue
        score = (-len(hits), min(hits))
        if best is None or score < best[0]:
            best = (score, index)
    return None if best is None else best[1]


def _amount_a_step_should_name(ing: dict) -> tuple[float, str | None] | None:
    """
    The amount a step has to agree with, or None when there is nothing to
    hold a step to.

    The ingredient dict has already been through cooking_ingredients, so
    its qty IS the number the Cook screen prints next to these very steps
    — which is the only number worth comparing a step against. Nothing
    that isn't a plain count or a measuring unit comes back: "1 head" of
    cabbage and "1 bottle" of oil are how it is bought, and a step
    shredding three cups of it is not disagreeing with them.
    """
    parsed = _quantities._parse_quantity((ing.get("qty") or "").strip())
    if not parsed:
        return None
    amount, unit = parsed
    if amount <= 0:
        return None
    if unit is not None and unit not in _measured_units():
        return None
    return amount, unit


def _amounts_add_up(listed: tuple[float, str | None], in_steps: list[tuple[float, str | None]]) -> bool | None:
    """
    Do the step amounts come to the list amount? None when the question
    can't be answered honestly — a unit that doesn't convert into the
    list's, or no step amount at all.

    A SINGLE STEP AMOUNT EQUAL TO THE LIST AMOUNT IS ENOUGH, and that is
    not a softening: a step saying "add the 3 cups of cashews" and a later
    one saying "blend the 3 cups until smooth" are the same cashews, not
    six cups of them. Summing alone would report that recipe, which is
    right, as wrong.
    """
    amount, unit = listed
    converted = []
    for step_amount, step_unit in in_steps:
        value = _quantities._convert_to_unit(step_amount, step_unit, unit)
        if value is None:
            return None
        converted.append(value)
    if not converted:
        return None
    slack = max(abs(amount) * _CHECK_AMOUNT_TOLERANCE, 1e-6)
    if any(abs(value - amount) <= slack for value in converted):
        return True
    return abs(sum(converted) - amount) <= slack


def _an_unpinned_amount_could_be_more_of_it(
    strays: list[tuple[float, str | None]], unit: str | None,
) -> bool:
    """Whether one of the step amounts this couldn't pin down might have
    been more of an ingredient measured in `unit` — in which case that
    ingredient's step amounts don't add up to a trustworthy total and are
    left alone."""
    return any(
        _quantities._convert_to_unit(1.0, stray_unit, unit) is not None
        for _amount, stray_unit in strays
    )


def _amount_mismatches(ingredients: list[dict], instructions: list[str], servings: int | None) -> list[dict]:
    """
    The lines where the list and the steps name different amounts of the
    same thing — {"item", "listed", "in_steps"} each, in list order.

    A SPICE-RACK ITEM IS EXEMPT, for the reason the name half of this
    check already exempts one: the list's amount for salt or cooking oil
    is the app's own figure rather than the recipe's (the Shop tab never
    sells them per recipe — see the long note under
    check_steps_ingredients_consistency), so holding a step to it is
    holding the recipe to a number it never wrote. The card says so in as
    many words: "to taste", "a pinch", "for garnish" and
    salt/pepper/oil-for-the-pan don't need an amount in the steps.
    Those three phrases need no rule of their own — none of them parses as
    a number, so _amount_a_step_should_name passes them over already.
    """
    raw_lines = list(ingredients or [])
    lines = cooking_ingredients(raw_lines, servings)
    item_words = []
    for ing in lines:
        name = (ing.get("item") or "").strip() if isinstance(ing, dict) else ""
        item_words.append([w for w in re.findall(r"[a-z]+", _clean_item(name)) if len(w) > 2])
    by_index: dict[int, list[tuple[float, str | None]]] = {}
    strays: list[tuple[float, str | None]] = []
    for step in instructions or []:
        attributed, unpinned = _amounts_in_one_step(step or "", item_words)
        for index, amount, unit in attributed:
            by_index.setdefault(index, []).append((amount, unit))
        strays.extend(unpinned)
    out = []
    for index, (raw, ing) in enumerate(zip(raw_lines, lines)):
        if not isinstance(ing, dict) or not isinstance(raw, dict):
            continue
        item = (ing.get("item") or "").strip()
        if not item or _spices.is_spice(item):
            continue
        # THE RECIPE HAS TO HAVE WRITTEN AN AMOUNT OF ITS OWN. A blank qty
        # comes back from cooking_ingredients filled in off
        # COOKING_QUANTITIES_PER_4 — the app's figure for what a quarter
        # cup of parsley is, not a claim the recipe made — and holding a
        # step to it reports a mismatch between the recipe and this
        # module. Same reasoning as the rack exemption above, and it is
        # what "to taste" / "a pinch" / "for garnish" / blank all land on.
        stated = (raw.get("cook_qty") or raw.get("qty") or "").strip()
        if not _quantities._parse_quantity(stated):
            continue
        listed = _amount_a_step_should_name(ing)
        if not listed:
            continue
        in_steps = by_index.get(index) or []
        if _amounts_add_up(listed, in_steps) is not False:
            continue
        if _an_unpinned_amount_could_be_more_of_it(strays, listed[1]):
            continue
        out.append({
            "item": item,
            "listed": _quantities._format_quantity(listed[0], listed[1]),
            "in_steps": " + ".join(_quantities._format_quantity(a, u) for a, u in in_steps),
        })
    return out


def check_steps_ingredients_consistency(
    ingredients: list[dict], instructions: list[str], servings: int | None = None,
) -> dict:
    """
    An accuracy check on a recipe: does the method match the list?

    Three ways a generated recipe quietly goes wrong, all of which read to
    a household as "the recipe details are not accurate" (Julia,
    2026-09-08):

    - an ingredient bought and then never used — it appears in no step;
    - a step reaching for something that was never on the list ("stir in
      the heavy cream", no cream anywhere), which is how a household finds
      out mid-cook that they didn't buy it;
    - an ingredient the steps DO use, for an amount that isn't the one the
      list bought — "3 cups of cashews in the list, the steps never use
      the 3 cups" (Gowthami's household, 2026-10-04). See
      _amount_mismatches and the long note above it: that half fails quiet
      by construction, and `servings` is the table the amounts are written
      for, since the amount a step has to agree with is the one the Cook
      screen prints beside it.

    Only the second half needs a vocabulary of food words, and it uses the
    measurement table's own keys as that vocabulary — one list to maintain
    rather than two that drift. A known food word in a step is evidence; an
    unknown one is not, and is passed over rather than guessed at. That
    asymmetry is deliberate: a false "you forgot to buy shallots" is worse
    than a missed one, and this only ever logs.

    A SPICE-RACK ITEM IS NOT A FINDING, and that is this app's own decision
    rather than a softening of the rule. The Shop tab's "Spices this week"
    section exists precisely because the grocery list assumes a rack
    (2026-09-23): salt, pepper and cooking oils are standing items, not
    per-recipe purchases, and a recipe is not wrong to season without
    listing them. Until 2026-09-27 the two halves of the app disagreed
    about that, and "a step uses salt" was the single biggest line in the
    morning report's FOOD section.
    It reads `spices.is_spice` rather than a second hand-written list for
    the reason `_STEP_ONLY_WORDS`' own two words are the exception and not
    the model: the rack is a maintained list that already exists, and a
    copy of it here would drift from the one the shopping list uses. That
    coupling is the point — extend the rack and this rule follows — and it
    is also the thing to know before extending it: a word added to
    `spices._SPICES` stops being reportable here in the same commit.
    `broth`, `butter`, `stock` and `heavy cream` are NOT rack items and
    still fire, which is the half of the rule worth keeping: a step
    reaching for broth nobody bought is how a household finds out
    mid-cook.

    Returns {"ok", "unused_ingredients", "missing_from_list",
    "amount_mismatches"} and never raises — an observation, and the thing
    agent._settle_recipe_amounts repairs a recipe against before it is
    saved.
    """
    names = [
        n for n in (
            (ing.get("item") or "").strip() if isinstance(ing, dict) else str(ing).strip()
            for ing in ingredients or []
        ) if n
    ]
    steps = [s for s in (instructions or []) if (s or "").strip()]
    if not names or not steps:
        return {"ok": True, "unused_ingredients": [], "missing_from_list": [], "amount_mismatches": []}

    text = " ".join(steps).lower()
    unused = []
    for name in names:
        # Any word of the name is enough: "Baby spinach" is "the spinach"
        # in step 3, and "Boneless chicken thighs" is "the chicken".
        words = [w for w in re.findall(r"[a-z]+", _clean_item(name)) if len(w) > 2]
        if words and not any(_item_matches(text, w) for w in words):
            unused.append(name)

    listed = " ".join(_clean_item(n) for n in names)
    matched = [
        key for key in COOKING_QUANTITIES_PER_4
        # `is_spice` is asked LAST because it is the dear one: it is asked
        # of a vocabulary word rather than of the recipe, so short-circuiting
        # behind `_item_matches` means it runs for the handful of words a
        # step really uses and not for all 176. Measured on a three-step
        # recipe: 0.52 ms a call on main, 1.43 ms asked first, 0.55 ms here.
        # Every one of these is a pure predicate, so the order is free.
        if key not in _STEP_ONLY_WORDS
        and _item_matches(text, key)
        and not _item_matches(listed, key)
        and not _spices.is_spice(key)
    ]
    # Report the longest name for a thing, not every fragment of it:
    # "heavy cream", never "heavy cream" and "cream".
    missing = [m for m in matched if not any(m != other and m in other for other in matched)]
    mismatches = _amount_mismatches(ingredients or [], steps, servings)
    return {
        "ok": not unused and not missing and not mismatches,
        "unused_ingredients": unused,
        "missing_from_list": sorted(missing),
        "amount_mismatches": mismatches,
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
    # Above get_conn on purpose: a word that is not a verdict never opens a
    # connection and never takes the write lock, and a caller that skips the
    # route entirely — chat, a script — is held to the same two words. It also
    # has to be above the UPDATE for the reason this guard exists at all: that
    # statement COMMITS, so by the time the per-person write further down
    # raises on the same word, the rating the household gave is already gone.
    # rating=None is checked first because it is not a rating at all, it is
    # "leave the rating alone, just add notes" — see RECIPE_RATINGS.
    if rating is not None and rating not in RECIPE_RATINGS:
        raise InvalidRecipeRating(
            f"{rating!r} isn't a verdict on a recipe. "
            f"Use one of: {', '.join(RECIPE_RATINGS)}."
        )
    # db.write() rather than get_conn() + conn.close(): the body raises on
    # purpose in two places (no such recipe, and -- further up -- a word that
    # is not a verdict), and before this every one of those raises leaked the
    # connection holding the write lock. See db.write's own docstring; this
    # function and attribute_recipe_feedback below are the two sites the
    # 2026-09-27 review named with line numbers as the proof of shape. The
    # body is otherwise unchanged, including its own conn.commit() -- a second
    # commit at the end of the block is a no-op.
    with write() as conn:
        recipe = conn.execute(
            "SELECT id, feedback_notes FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?) "
            "ORDER BY id LIMIT 1",
            (household_id(), recipe_name),
        ).fetchone()
        if not recipe:
            raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")

        merged_notes = recipe["feedback_notes"]
        if notes:
            merged_notes = f"{merged_notes} | {notes}" if merged_notes else notes

        if rating is not None:
            conn.execute(
                "UPDATE recipes SET rating = ?, feedback_notes = ? WHERE id = ? AND household_id = ?",
                (rating, merged_notes, recipe["id"], household_id()),
            )
        else:
            conn.execute(
                "UPDATE recipes SET feedback_notes = ? WHERE id = ? AND household_id = ?",
                (merged_notes, recipe["id"], household_id()),
            )
        conn.commit()
        recipe_id = recipe["id"]

    # Outside the block on purpose, exactly as it was outside the old
    # conn.close(): this opens two connections of its own, and nesting them
    # inside an open write transaction is how this repo has twice earned an
    # intermittent "database is locked".
    solo_auto_attribution = None
    if rating is not None:
        solo_auto_attribution = _maybe_auto_attribute_solo_night(recipe_id, recipe_name, rating)
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

    # try/finally on both connections, and the second one is why. The INSERT
    # below writes into member_recipe_feedback.rating, which carries
    # CHECK(rating IN ('liked','disliked')), so it CAN raise — and without a
    # finally the connection was left open holding SQLite's write lock, on a
    # worker thread that no other thread may even close ("SQLite objects
    # created in a thread can only be used in that same thread") and that
    # gc.collect() will not free, because the reference is live on that
    # thread's exception state rather than in a cycle. Measured 2026-09-26 on
    # a real uvicorn: the household's very next write waited out sqlite3's
    # full 5-second busy timeout and then 500'd, and tools.record_error could
    # not write either — error_events held nothing at all for the crash, so
    # the one failure the morning report most needs to see was the one it
    # could not see. mark_recipe_feedback's vocabulary guard closes today's
    # trigger; this is what makes the NEXT unexpected failure in here visible
    # instead of silent.
    conn = get_conn()
    try:
        entry = conn.execute(
            """
            SELECT date, slot FROM meal_plan_entries
            WHERE household_id = ? AND recipe_id = ? AND cooked_status = 'done'
            ORDER BY COALESCE(cooked_at, created_at) DESC, id DESC LIMIT 1
            """,
            (household_id(), recipe_id),
        ).fetchone()
    finally:
        conn.close()
    if not entry:
        return None

    att = _attendance.get_slot_attendance(entry["date"], entry["slot"])
    if att["guest_count"] or len(att["present_member_ids"]) != 1:
        return None
    member_id = att["present_member_ids"][0]
    member_name = att["present_names"][0]

    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT source FROM member_recipe_feedback WHERE household_id = ? AND recipe_id = ? AND member_id = ?",
            (household_id(), recipe_id, member_id),
        ).fetchone()
        if existing and existing["source"] == "explicit":
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
    finally:
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
    # db.write(), for the reason spelled out at mark_recipe_feedback above:
    # three of this function's four exits are a raise, and each of them used
    # to leak the connection holding the write lock.
    with write() as conn:
        recipe = conn.execute(
            "SELECT id, rating FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?)",
            (household_id(), recipe_name),
        ).fetchone()
        if not recipe:
            raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")

        resolved_rating = rating or (recipe["rating"] or None)
        if not resolved_rating:
            raise ValueError(
                f"'{recipe_name}' has no rating yet to attribute to {member_name} — pass rating explicitly."
            )
        # This door ALREADY refused a third word before mark_recipe_feedback's
        # guard existed — checked 2026-09-26, it is not a sixth instance. What
        # changed is only where the two words live: its own hard-coded tuple was a
        # second copy of one rule, so changing RECIPE_RATINGS would have moved one
        # door and not the other. InvalidRecipeRating is a ValueError subclass, so
        # every caller that was catching this still catches it; this function is
        # reachable from chat only (no route), so there is no except ordering to
        # get right here the way there is on /api/recipe-feedback.
        if resolved_rating not in RECIPE_RATINGS:
            raise InvalidRecipeRating(
                f"{resolved_rating!r} isn't a verdict on a recipe. "
                f"Use one of: {', '.join(RECIPE_RATINGS)}."
            )

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
        "SELECT id FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?) ORDER BY id LIMIT 1",
        (household_id(), (recipe_name or "").strip()),
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
        "SELECT id FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?) ORDER BY id LIMIT 1",
        (household_id(), (recipe_name or "").strip()),
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
        "SELECT id FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?) ORDER BY id LIMIT 1",
        (household_id(), (recipe_name or "").strip()),
    ).fetchone()
    if not recipe:
        conn.close()
        raise ValueError(f"No recipe named '{recipe_name}'. Save it first with add_recipe.")
    conn.execute(
        "UPDATE recipes SET temporarily_excluded = ? WHERE id = ? AND household_id = ?",
        (1 if excluded else 0, recipe["id"], household_id()),
    )
    conn.commit()
    conn.close()
    return {"name": recipe_name, "temporarily_excluded": excluded}


def _record_grocery_link(entry_id: int, item: str, grocery_item_id: int, qty: str, conn=None) -> None:
    """
    Record exactly what THIS meal contributed to that grocery line, before
    it got merged with anything else already there — see
    grocery._reverse_meal_grocery_contributions, which is what lets
    swap_meal_in_plan/swap_component_in_plan/clear_weekly_plan take this
    back out precisely if the meal is later swapped or dropped.

    Given a `conn` this writes on it and neither commits nor closes (the
    ingest running inside swap_meal_in_plan's one transaction — see
    _add_recipe_ingredients_for_entries); left unset it commits on a
    connection of its own, exactly as before.
    """
    own_conn = conn is None
    link_conn = get_conn() if own_conn else conn
    link_conn.execute(
        "INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
        "VALUES (?, ?, ?, ?, ?)",
        (household_id(), entry_id, grocery_item_id, item, _quantities._strip_prep_descriptor(qty or "")),
    )
    if own_conn:
        link_conn.commit()
        link_conn.close()


def _week_bought_amount(amount: float, unit: str | None) -> tuple[float, str | None]:
    """
    A week's worth of one per-portion ingredient, rounded to something a
    person can actually buy — ONCE, on the total, in the unit the grocery
    line will actually be written in.

    Rounding once is the point. Doing it per meal is why Emily's week asked
    for 17 peppers and would still have asked for 14 after the servings
    scaling below: five dinners wanting 2.25, 3, 1.5, 3 and 3 peppers each
    round UP on their own to 3, 3, 2, 3, 3 = 14, when the week actually
    wants 12.75 → 13. A shopper buys peppers once, so they get rounded
    once.

    A measurable unit is rolled up to its display unit FIRST (52 tbsp → 3.25
    cups) and rounded there, so the line and the per-meal ledger rows that
    reverse it are denominated the same way. Rounding in the recipe's own
    unit and letting the display roll it up afterwards would leave "3.25
    cups" on the list with "26 tbsp" in the ledger, and a swap would then
    find nothing it could safely subtract.

    It is the SAME rounding quantities._humanize_grocery_quantity applies,
    and it is the same CODE — quantities._shopping_round — rather than two
    copies that agree today. That is load-bearing: the ledger records each
    meal's share (see _ledger_share) and reversal re-rounds whatever
    survives through quantities._sum_ledger_quantities, so a line that
    loses no meal at all has to recompute to what this put on it. Two
    hand-maintained roundings could drift; one cannot.
    """
    if amount <= 0:
        return 0.0, unit
    return _quantities._shopping_round(amount, unit)


# The per-meal ledger is machinery, never read by a person, and its rows
# have to add back up to the line — so they are written finer than the six
# significant figures a shopping list is displayed at. See _ledger_share.
_LEDGER_SIG = 12


def _ledger_share(amount: float, unit: str | None, line_unit: str | None) -> str:
    """
    What ONE meal actually asked for, written in the unit the grocery line
    ended up in — UNROUNDED, and the unrounded part is the whole point.

    This used to record that meal's apportioned share of the ROUNDED line
    instead: `_apportion` (2026-09-05) split the rounded total by largest
    remainder into whole quanta that summed to the line exactly. It was
    written for a reversal that SUBTRACTED one contribution out of the
    displayed line, where a fractional row really would have left a phantom
    quarter-pepper behind after the week was cleared. Reversal has
    recomputed the line from the ledger since later that same day (see
    grocery._reverse_meal_grocery_contributions), and recomputing from
    ROUNDED shares is lossy in a way that shows on the list:

      - three nights of a recipe serving twelve apportion one lemon as
        1 / 0 / 0, so dropping the FIRST night summed the two survivors to
        nothing and the list read "Lemon · 0" with two dinners still
        planned;
      - two nights of a two-can recipe apportion three cans as 2 / 1, so
        the same plan came back as "1 can" or "2 cans" depending on which
        night was dropped.

    An unrounded share carries what the meal wanted rather than what it was
    handed, so the survivors can be summed and rounded ONCE — the same
    single rounding _week_bought_amount does on the way in. The phantom
    remainder `_apportion` guarded against cannot come back on the
    recompute path, because nothing subtracts there any more: the last meal
    off a line takes the row with it, and every meal before that re-derives
    the line from scratch.

    "Unrounded" up to _LEDGER_SIG significant figures, which is how the
    string is written (quantities._plain_number). Twelve rather than the
    six a person reads, because these have to ADD BACK UP: three sixths of
    something written at six figures is 2.000001, which rounds up to three
    of them and hands a household a whole extra onion.

    THE ONE PLACE THAT STILL SUBTRACTS is a household's own standing want,
    which no recompute may replace. It must not be handed this share: the
    line was added to ONCE, rounded, so subtracting unrounded shares
    ratchets it upward every week. See
    quantities._ledger_totals, which is what
    grocery._reverse_meal_grocery_contributions subtracts there instead.
    """
    # unit and line_unit are always the same word, or two units of one
    # measurable family — _week_bought_amount only ever hands back the unit
    # it was given or a roll-up within its own family — so this conversion
    # does not actually fail today. The fallback is here so that a change
    # to that could never write a None unit into the ledger.
    converted = _quantities._convert_to_unit(amount, unit, line_unit)
    if converted is None:
        return _quantities._format_quantity(amount, unit, sig=_LEDGER_SIG)
    return _quantities._format_quantity(converted, line_unit, sig=_LEDGER_SIG)


class _KitchenStock:
    """
    What the kitchen already has, and what this ingest pass has already
    promised out of it.

    The question this answers is the only one the grocery ingest asks of
    inventory: is there demonstrably enough of this to leave it off the
    list? Until 2026-09-14 the question was "is this name in inventory at
    all" — the quantity was selected on as a non-blank test and then
    thrown away — so two ounces of chicken thighs took two POUNDS of them
    off the shopping list, and a week shopped normally (every ticked line
    writes an inventory row) left the next week's list three items long.
    Reproduced over HTTP before this was touched.

    The rule now: an ingredient is skipped only when the tracked
    quantities, read in the unit the week's own shopping line would be
    written in, add up to at least what that line would say. Everything
    else is bought — a freeform "a handful" on either side, a bag against
    a cup, two unit families that do not convert. That is this module's
    standing bias (see _PACKAGE_UNITS: "an extra line beats a missing
    dinner"), and it is the one direction a wrong answer here is
    survivable in: an extra line costs a line, a missing one costs the
    dinner.

    ROWS FOR ONE NAME ARE SUMMED, not picked between. An item kept in two
    places — the opened jar in the fridge and the unopened one in the
    pantry — is two rows of the same food, and "do we have enough" is a
    question about the food, not about a shelf. But every one of those
    rows has to be readable and convertible: one row saying "a bit left"
    beside one saying "2 lbs" means the total is unknown, so it is bought.

    THE STOCK IS CLAIMED AS IT IS SPENT. One buffer is one approval (see
    WeekGroceryBuffer), and the ingest runs once per recipe-week inside
    it, so without this the second and third recipes of a week to want
    chicken thighs would each be told about the same two pounds and the
    household would cook six pounds out of two.

    COVERAGE IS ALL-OR-NOTHING, deliberately: a pound and a half of salmon
    against a two-pound need buys the whole two pounds, not the missing
    half. Safe, and the one place this fix makes quantities run HIGH
    rather than low — buying only the difference would mean writing a line
    the per-meal ledger cannot reverse, which is a much bigger claim than
    this. Named in the decision log so the trade is Emily's to overrule
    rather than a surprise.

    It also decides nothing beyond this function. The new condition is a
    strict SUBSET of the old one — the name still has to match, and now an
    amount has to cover as well — so nothing can be suppressed that was
    not already being suppressed, and every change is toward buying more.
    """

    def __init__(self, conn, locations: set[str] | None = None,
                 exclude_sources: set[str] | None = None):
        """
        `locations` and `exclude_sources` narrow which rows count, and
        both default to None, which is every row — the ingest's own
        question is about the kitchen as a whole and is unchanged.

        They exist for defrost.meat_items_for_plan (2026-09-15), which
        asks a narrower one: is there demonstrably enough of this ON A
        NAMED SHELF, on a row somebody actually put there. A location is
        read the way every screen reads it (_display_location, so a blank
        one falls back to its category's shelf) and compared case-folded,
        because 'Freezer' typed with a capital is the same shelf.
        """
        # Read once per buffer — one approval, one reading of the kitchen.
        # Nothing writes inventory in between.
        self._on_hand: dict[str, list[tuple[float, str | None]]] = {}
        self._unreadable: set[str] = set()
        self._claimed: dict[str, list[tuple[float, str | None]]] = {}
        rows = conn.execute(
            "SELECT item, quantity, category, location, source FROM inventory_items "
            "WHERE household_id = ? AND TRIM(quantity) != ''",
            (household_id(),),
        ).fetchall()
        for row in rows:
            if exclude_sources and (row["source"] or "") in exclude_sources:
                continue
            if locations is not None:
                where = _quantities._display_location(dict(row)).strip().lower()
                if where not in locations:
                    continue
            # Matched on the plain stripped name, exactly as the name-only
            # check this replaces did. Deliberately NOT grocery._merge_key:
            # that reads singulars, plurals and prep descriptors as the
            # same thing, which would make MORE ingredients skippable, and
            # widening what counts as "we have it" is the direction this
            # fix exists to narrow.
            key = (row["item"] or "").strip().lower()
            parsed = _quantities._parse_quantity(row["quantity"] or "")
            if parsed is None:
                self._unreadable.add(key)
            else:
                self._on_hand.setdefault(key, []).append(parsed)

    def covers(self, item: str, need: tuple[float, str | None] | None) -> bool:
        """
        True when `need` — (amount, unit), this recipe-week's whole scaled
        claim on the ingredient — is demonstrably already at home, and
        CLAIMS that much of the stock when it is.

        `need` is COMPARED as the shopping line would be written, through
        _week_bought_amount: one rounding, the same one, so this and the
        line it is deciding against can never disagree about the amount.
        Be precise about what that rounding does, because "it rounds up"
        is only true of countable things: a COUNTABLE (and a counted pack
        — a head of garlic, a dozen eggs) ceils, and a MEASURABLE unit
        goes to the nearest QUARTER of its display unit, which can round
        DOWN. So 1.5 lbs on hand covers a 1.6 lb need, because the line
        the app would have written for that need says 1.5 lbs too. The
        gap is bounded at an eighth of the display unit and is the same
        gap the shopping list itself carries; fuzzing found one skip
        short by 1.1% in 2,264 skips.

        What is CLAIMED is the raw need, not the rounded one. The
        comparison is unchanged, so nothing can be skipped that could not
        be skipped before — but a week that wants seven cloves of garlic
        out of two heads on the shelf must not have the first two
        recipe-weeks spend a whole head each and the third be sent to the
        shop. See the claim below.
        """
        if not need or need[0] is None or need[0] <= 0:
            return False  # nothing to compare against; buy it
        key = (item or "").strip().lower()
        on_hand = self._on_hand.get(key)
        if not on_hand or key in self._unreadable:
            return False
        wanted, unit = _week_bought_amount(need[0], need[1])
        have = self._total_in(on_hand, unit)
        spent = self._total_in(self._claimed.get(key, []), unit)
        if have is None or spent is None:
            return False  # can't be reconciled into one unit; buy it
        if have - spent + 1e-9 < wanted:
            return False
        # The stock is spent at the RAW amount these meals will eat, not at
        # the rounded amount the shop would have sold. Claiming the rounded
        # one is tidier and it put a false line on the list every week the
        # counted packs appeared: three recipe-weeks wanting 3, 2 and 2
        # cloves each round up to a whole head, so two heads on the shelf
        # were spent by the first two and the third bought garlic nobody
        # needed. It loosens the ledger — the household can now be told
        # about the fraction of a pack the first group rounded away — and
        # that is the safe direction, because the COMPARISON above still
        # uses the rounded figure, so every individual skip is exactly as
        # well-founded as it was.
        raw = _quantities._convert_to_unit(need[0], need[1], unit)
        self._claimed.setdefault(key, []).append((wanted if raw is None else raw, unit))
        return True

    def on_hand_total(self, item: str, unit: str | None) -> tuple[int, float] | None:
        """
        How many tracked rows there are for `item` and what they add up to
        in `unit`, or None when the name is unreadable or one of the rows
        will not convert into it.

        READ-ONLY, and no part of the decision — covers() is that. It
        exists so a caller that PRINTS what is on hand can print the
        figure that was actually compared. The pre-shop card names ONE row
        (cooker._find_inventory_match's pick) and this class sums EVERY
        row of the name, so without it a household with a pound of
        broccoli in the fridge and a pound and a half in the pantry reads
        "You want 2 lbs. Fridge shows 1 lb." over a line the card has just
        taken off the shopping list — the exact sentence this whole fix
        exists to stop printing, arriving from the other side. Which half
        it showed depended on which row came back first.
        """
        key = (item or "").strip().lower()
        rows = self._on_hand.get(key)
        if not rows or key in self._unreadable:
            return None
        total = self._total_in(rows, unit)
        return None if total is None else (len(rows), total)

    @staticmethod
    def _total_in(amounts, unit) -> float | None:
        """Everything in `amounts` added up in `unit`, or None the moment
        one of them cannot be expressed in it."""
        total = 0.0
        for amount, from_unit in amounts:
            converted = _quantities._convert_to_unit(amount, from_unit, unit)
            if converted is None:
                return None
            total += converted
        return total


class WeekGroceryBuffer:
    """
    Per-portion amounts held UNROUNDED until every recipe in an ingest pass
    has had its say, then written to the list one line at a time with a
    single rounding each.

    It exists because the recipe-week grouping isn't a big enough unit of
    work for rounding. Grouping fixed the sealed-package bug — one bag of
    spinach for six breakfasts of the same recipe — but Emily's peppers came
    from five DIFFERENT dinners, so they arrive here as five separate calls
    to _add_recipe_ingredients_for_entries and only something that spans the
    whole approval can see them as one shopping decision.

    approve_weekly_plan makes one buffer for the whole week and flushes it
    at the end. Anywhere with genuinely one meal to account for (plan_meal,
    the swap paths) passes nothing and gets a buffer of its own that flushes
    on the way out — one meal, one rounding, which is the right answer
    there.

    Freeform quantities ("a bunch", "to taste") never enter the buffer:
    there is no number to sum, so they keep going straight onto the list
    per meal, where _repeat_or_concatenate already knows what to do with
    them. Sealed packages don't either — a package is a once-per-week
    decision the grouped path already makes.
    """

    def __init__(self, weekly_plan_id: int | None, conn=None):
        self.weekly_plan_id = weekly_plan_id
        # The connection every flushed line is written on, when the caller
        # is holding one open transaction (swap_meal_in_plan). None means
        # each add_grocery_item / _record_grocery_link commits on its own,
        # exactly as before.
        self.conn = conn
        # (merge key, unit, note) -> the one grocery line that will become.
        # Two recipes writing the same item in units that don't reconcile
        # ("2 cups beans" and "1 lb beans") stay two entries here and meet
        # each other in add_grocery_item, which reports the disagreement
        # honestly instead of guessing a conversion.
        self._lines: dict[tuple, dict] = {}
        # The kitchen as it stood when this pass started, read lazily and
        # once — see _KitchenStock and kitchen_stock() below.
        self._stock: "_KitchenStock | None" = None

    def kitchen_stock(self) -> "_KitchenStock":
        """
        What the household already has, shared by every recipe-week in this
        pass so the same two pounds of chicken cannot be counted twice.

        Read on the buffer's own connection when it has one and on a fresh
        one otherwise — the same rule _add_recipe_ingredients_for_entries
        follows, because inside an open write transaction a second
        connection would sit behind its lock (see that function).
        """
        if self._stock is None:
            conn = self.conn or get_conn()
            try:
                self._stock = _KitchenStock(conn)
            finally:
                if self.conn is None:
                    conn.close()
        return self._stock

    def add(self, entry_id: int, item: str, category: str, amount: float, unit: str | None, note: str) -> None:
        key = (_grocery._merge_key(item), unit, note)
        line = self._lines.get(key)
        if line is None:
            line = self._lines[key] = {
                "item": item, "category": category, "unit": unit, "note": note, "shares": {},
            }
        else:
            # "Onions" and "Yellow onion" share a key (grocery._SAME_PURCHASE);
            # the line carries the variety, which tells the shopper more.
            line["item"] = _grocery._more_specific_name(line["item"], item)
        line["shares"][entry_id] = line["shares"].get(entry_id, 0.0) + amount

    def flush(self) -> None:
        for line in self._lines.values():
            if not (line["item"] or "").strip():
                # Defensive: _add_recipe_ingredients_for_entries already
                # skips a blank ingredient name before it ever reaches
                # buffer.add(), so this shouldn't fire in practice -- but
                # add_grocery_item now raises ValueError on a blank name
                # (Loop Board bug fix, 2026-09-15), and a 500 mid-approval
                # is worse than one skipped line, so guard here too.
                logger.debug("Skipping a blank grocery item line: %r", line)
                continue
            entry_ids = list(line["shares"])
            shares = [line["shares"][e] for e in entry_ids]
            rounded, unit = _week_bought_amount(sum(shares), line["unit"])
            qty = _quantities._with_note(_quantities._format_quantity(rounded, unit), line["note"])
            add_result = _grocery.add_grocery_item(
                line["item"], quantity=qty, category=line["category"], added_by="ai",
                source_weekly_plan_id=self.weekly_plan_id, conn=self.conn,
            )
            for entry_id, share in zip(entry_ids, shares):
                _record_grocery_link(
                    entry_id, line["item"], add_result["item_id"],
                    _ledger_share(share, line["unit"], unit), conn=self.conn,
                )
            # A counted pack that landed on a line the plan already had is
            # re-read from the whole ledger, so two passes' cartons don't
            # add as cartons — see grocery._recompute_plan_line_from_ledger.
            if add_result["merged"] and _quantities._pack_group(unit):
                _grocery._recompute_plan_line_from_ledger(add_result["item_id"], conn=self.conn)
        self._lines.clear()


def _add_recipe_ingredients_to_grocery_list(
    entry_id: int, recipe_ingredients: list[dict], weekly_plan_id: int | None,
    default_servings: int | None = None, conn=None,
) -> tuple[list[str], list[str]]:
    """
    One planned meal's ingredients onto the grocery list — the single-meal
    door onto _add_recipe_ingredients_for_entries below, which is where
    the behaviour lives. Used by plan_meal and the swap paths, where there
    genuinely is only one meal to account for. `conn` rides straight
    through — see below.
    """
    return _add_recipe_ingredients_for_entries(
        [entry_id], recipe_ingredients, weekly_plan_id, default_servings=default_servings,
        conn=conn,
    )


def _add_recipe_ingredients_for_entries(
    entry_ids: list[int], recipe_ingredients: list[dict], weekly_plan_id: int | None,
    default_servings: int | None = None, buffer: "WeekGroceryBuffer | None" = None,
    conn=None, chain_scale: bool = True, reheat_buys_it: bool = False,
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

    - A COUNTED PACK (eggs by the dozen, garlic by the head — see
      _counted_pack_share) is bought by the pack and used by the piece.
      Each meal contributes the pieces it uses, the week's pieces add up,
      and the line is written in whole packs, rounded up once. Four meals
      that each say "1 dozen" buy one carton (Loop Board, 2026-09-13).

    - Everything else is a PER-PORTION amount — 4 cups of beans, 3 bell
      peppers, a bunch of cilantro — and still adds up across every meal
      that wants it. Five dinners wanting 2-4 peppers each genuinely want
      the sum; what they do not want is the sum of five numbers each
      written for a bigger table than the one they will be eaten at.

    Quantities are scaled to the people who will actually EAT the meal
    before they reach the list, and that is two things composed, not one:
    the meal's own attendance (a Thursday dinner only one of two people is
    home for buys for one) and the recipe's own default_servings (a recipe
    written for 4 in a household of 3 buys three quarters of it). The
    factor comes from attendance.servings_scale_factor, which multiplies
    grocery_scale_factor's household-relative answer by
    household_size / default_servings so the household size cancels and
    what is left is eaters / default_servings — applied exactly once. It
    falls back to attendance alone when there is nothing to anchor to (no
    members on record, no default_servings on the recipe), so a household
    mid-onboarding still shops the way it always has.

    This is the second half of the 17-peppers fix, and the half Emily
    actually asked for: "a regular week for a family of 3 shouldn't have 17
    peppers." The first half stopped packages multiplying. This one stops
    every recipe in the app being bought for four people when three live
    here. A package is still not scaled at all — three quarters of a table
    still buys one whole bottle.

    A per-portion amount is then held UNROUNDED until the whole ingest pass
    is done and rounded ONCE per grocery line — see WeekGroceryBuffer and
    _week_bought_amount. Rounding each meal's share up on its own is how
    12.75 peppers became 14 instead of 13; a shopper buys the peppers once,
    so the arithmetic rounds once. Each meal's ledger row then carries its
    own UNROUNDED share (_ledger_share), which is what keeps reversal
    symmetric: dropping one meal re-rounds what the survivors still want,
    and the last meal off a line takes the row with it.

    A quantity with no number in it at all ("a bunch", "to taste") can't be
    summed, so it skips the buffer and goes on per meal exactly as before.

    A cook-once-eat-twice chain moves that factor a second time, and moves
    the other night to zero. A LEFTOVERS entry contributes NOTHING at all
    — its dinner was already bought on the night it is actually cooked,
    and buying the same recipe twice was the shopping half of the bug
    Emily reported on 2026-09-04. `reheat_buys_it` is the one exception,
    and it is asked about the INGREDIENTS rather than about the night: a
    side cooked fresh beside the reheated dish is not the dish, so the
    batch on the cook night says nothing about it and nothing else on the
    week buys it (see weekly_plan._entry_side_groups). The SOURCE entry
    buys for the whole chain: its factor is multiplied by (everyone the
    batch feeds ÷ the people at the cook night's own table), so a Tuesday
    cook for three that also feeds a Thursday for three shops for six.
    The two factors compose rather than fight: each is relative to a
    different baseline (the household, then that night's table), so a
    chain with no attendance rows anywhere doubles exactly, and one with
    someone away on Thursday buys for five.

    Grouping by recipe-week is what makes that chain check a FILTER rather
    than an early return, and the distinction matters: a chain reuses one
    recipe_id, so the cook night and the reheat night are two entries in
    the SAME group here. Returning early on the reheat would take the cook
    night's shop with it. So a leftovers entry is dropped from the group's
    contributing entries — no scaled share, and no grocery link, since a
    link would keep a package alive on the list past the cook night that
    actually earned it — and the group returns empty only when every entry
    in it was dropped.

    Every contributing meal gets its own meal_plan_grocery_links row, so
    reversal stays exactly symmetric with what was added: a per-portion
    row carries that meal's own unrounded share of the line, and a
    package row carries the package, with
    _reverse_meal_grocery_contributions holding the line on the list until
    the last meal that named it is gone.

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

    `conn` is for one caller and is not part of the assistant-facing API:
    swap_meal_in_plan takes the old meal off the plan and the list and puts
    the new one on both inside ONE write transaction, so this whole ingest
    — the entry reads, the chain check, the attendance reads, the inventory
    read, every add_grocery_item and every ledger row — runs on that
    connection and commits nothing of its own. SQLite gives one writer at
    a time, so any of these opening its own connection inside that
    transaction would sit behind its lock and die of "database is locked";
    and the entry being bought for is one that transaction has only just
    inserted, so any read on another connection would not even find it.
    A `buffer` handed down already carries its connection (see
    WeekGroceryBuffer), and the two must agree. Left unset, every other
    call site — plan_meal in chat, approve_weekly_plan — behaves exactly as
    before.
    """
    from . import attendance as _attendance
    from . import leftovers as _leftovers

    if conn is None and buffer is not None:
        conn = buffer.conn
    elif buffer is not None and buffer.conn is not None and buffer.conn is not conn:
        raise ValueError("The grocery buffer and the ingest are on different connections.")
    own_conn = conn is None

    # Each meal's own headcount factor, so attendance can say how many it
    # feeds. A missing entry (an ad hoc add, a row since deleted) simply
    # doesn't scale rather than failing the shop.
    #
    # The chain check sits at this one choke point rather than in the
    # callers, so plan_meal's opt-in flag and approve_weekly_plan's
    # whole-week yes both get it. Chains are looked up once per PLAN and
    # cached here rather than once per entry: grouping by recipe already
    # brings every meal that cooks this recipe through in one call, so the
    # per-entry query the single-meal path used to do would now repeat
    # itself for no reason.
    entry_conn = get_conn() if own_conn else conn
    scale_for_entry: dict[int, float] = {}
    # The same factor for a BARE COUNT of a whole thing, which is the same
    # composition with the recipe anchor capped at 1.0 — see
    # attendance.count_scale_factor. Two factors per entry rather than one
    # because the question is per INGREDIENT and the reads are per entry:
    # a Tuesday dinner's chicken scales with the eaters and its lemons do
    # not, and asking attendance again per ingredient would be a
    # connection per ingredient per meal.
    count_scale_for_entry: dict[int, float] = {}
    contributing_ids: list[int] = []
    chains_by_plan: dict[int, dict] = {}
    # "Bring over from last week" (Emily, 2026-09-25): a meal brought over
    # doesn't buy again what last week's list already bought for it —
    # the lowercased ingredient names its original nights' grocery lines
    # hold that are ticked purchased (bring_over.bought_items). Read here,
    # at the one choke point every ingest goes through (approval, the
    # re-buy after a swap, a meal planned in chat), so none of them buys it
    # twice; and because a skipped ingredient records no link, the re-buy
    # after a swap asks again and gets the same answer.
    bought_by_entry: dict[int, set[str]] = {}
    for entry_id in entry_ids:
        entry_row = entry_conn.execute(
            "SELECT date, slot, weekly_plan_id, derived_from_json FROM meal_plan_entries "
            "WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
        if entry_row and entry_row["derived_from_json"]:
            try:
                derived = json.loads(entry_row["derived_from_json"] or "{}") or {}
            except (TypeError, ValueError):
                derived = {}
            brought = derived.get(_bring_over.KEY) if isinstance(derived, dict) else None
            if isinstance(brought, dict) and brought.get("entry_ids"):
                bought = _bring_over.bought_items(brought["entry_ids"], conn=entry_conn)
                if bought:
                    bought_by_entry[entry_id] = bought
        scale = (
            _attendance.servings_scale_factor(
                entry_row["date"], entry_row["slot"], default_servings, conn=entry_conn,
            )
            if entry_row else 1.0
        )
        count_scale = (
            _attendance.count_scale_factor(
                entry_row["date"], entry_row["slot"], default_servings, conn=entry_conn,
            )
            if entry_row else 1.0
        )
        if entry_row and entry_row["weekly_plan_id"]:
            plan_id = entry_row["weekly_plan_id"]
            if plan_id not in chains_by_plan:
                chains_by_plan[plan_id] = _leftovers.plan_leftover_chains(plan_id, conn=entry_conn)
            chains = chains_by_plan[plan_id]
            # A reheat night buys nothing and links to nothing — it drops
            # out of the group entirely rather than returning early, since
            # the cook night it eats from shares this very group.
            #
            # `reheat_buys_it=True` is the one exception, and it is about
            # WHAT is being bought rather than about the night: a side
            # cooked fresh beside the reheated dish (a green salad next to
            # Thursday's chili) is a different dish, made that evening, and
            # nothing else on the week buys it. The chain says the CHILI
            # was already cooked; it says nothing about the salad. Without
            # this the side was silently dropped here and the household was
            # shown a plate they had no lettuce for (Loop Board, 2026-09-25).
            if entry_id in chains["leftovers"] and not reheat_buys_it:
                continue
            # `chain_scale=False` is a big-meal dish's path (weekly_plan's
            # side ingests, via _entry_side_groups): a dish on a hosted
            # holiday's table belongs to that table alone — the batch that
            # feeds the reheat night is the main, not the stuffing beside
            # it — so a reheat night buys nothing new for it. Every other
            # side, and every recipe, follows the chain.
            # batch_for_entry: a chain source, or a cook carrying portions
            # for the freezer (leftovers.FREEZER_EXTRA_KEY, 2026-09-22).
            #
            # A REHEAT reaching here (reheat_buys_it, for its side) still
            # asks batch_for_entry, and "it finds no batch on a reheat" is
            # true of today's DATA rather than of this code — worth saying,
            # because two shapes would make it false and neither is checked
            # here. A leftovers row carrying FREEZER_EXTRA_KEY is listed in
            # chains["freezer"] as readily as a cook is, and a row that both
            # links_to an earlier night and names a later one back is in
            # chains["sources"] AND chains["leftovers"] at once. Measured
            # 2026-09-25: force either and the reheat's salad is bought for
            # the batch rather than for the night. Neither is reachable —
            # both FREEZER_EXTRA_KEY writers (weekly_plan.freeze_a_portion,
            # tonight._shrink_chain_into_freezer) stamp the COOK — and a
            # guard was tried and taken back out, because the source-and-
            # reheat shape has an arguable right answer (a salad really is
            # cooked fresh on the reheat night, so it could cover a later
            # one) and narrowing it on a review pass would be deciding that
            # unmeasured.
            batch = _leftovers.batch_for_entry(entry_id, chains, conn=entry_conn) if chain_scale else None
            if batch:
                if batch["servings"] > 0 and batch["cook_eaters"] > 0:
                    # Both factors, because a batch really is more food: a
                    # cook for six uses twice the lemons of a cook for
                    # three. Only the RECIPE anchor is capped for a count,
                    # never this.
                    batch_factor = batch["servings"] / batch["cook_eaters"]
                    scale *= batch_factor
                    count_scale *= batch_factor
        contributing_ids.append(entry_id)
        scale_for_entry[entry_id] = scale
        count_scale_for_entry[entry_id] = count_scale
    if own_conn:
        entry_conn.close()
    # Every meal in this group was a reheat, so the group buys nothing —
    # the same answer the single-meal path gives for a lone leftovers entry.
    if not contributing_ids:
        return [], []
    added_items: list[str] = []
    already_have: list[str] = []
    # Routed through add_grocery_item (its own connection per call) rather
    # than a raw insert here, so quantities consolidate with anything
    # already on the list instead of creating duplicate lines. Tagged
    # with source_weekly_plan_id when this meal belongs to a generated
    # week (not an ad hoc one-off), so a later week's generation can
    # tell this ingredient apart from a genuine standing want and clear
    # it out once it's stale — see clear_stale_grocery_items.
    # A buffer of this group's own when nobody handed one down, so a single
    # meal planned in chat still rounds exactly once and this function has
    # only one code path.
    own_buffer = buffer is None
    if own_buffer:
        buffer = WeekGroceryBuffer(weekly_plan_id, conn=conn)
    # Anything the kitchen can be SHOWN to cover is left off the list —
    # this is the "accounts for logged inventory" behaviour for the
    # plan-approval path. For a direct chat-driven add ("add flour to the
    # list") the agent checks get_inventory itself and asks first instead
    # (see system prompt), since there's a person there to actually ask.
    # The amount is compared now; it used to be selected on and discarded.
    # See _KitchenStock.
    stock = buffer.kitchen_stock()
    all_contributing_ids = contributing_ids

    for ing in recipe_ingredients:
        # A recipe (AI-drafted, or hand-typed and mis-parsed) can carry a
        # line with a blank item name. add_grocery_item now trims and
        # rejects a blank name outright (Loop Board bug fix, 2026-09-15)
        # so it can't leave a ghost row on the list -- but this function
        # runs inside plan approval, and a raised ValueError there would
        # 500 the whole approval over one bad ingredient line, which is
        # worse than the ghost row it replaces. Skip the line instead;
        # every other ingredient in the recipe still lands normally.
        if not (ing.get("item") or "").strip():
            logger.debug("Skipping a blank ingredient name for entries %s: %r", entry_ids, ing)
            continue
        # The meals this ingredient is bought for: every contributing one
        # but a brought-over meal whose own line was bought last week.
        contributing_ids = [
            e for e in all_contributing_ids
            if ing["item"].strip().lower() not in bought_by_entry.get(e, ())
        ]
        if not contributing_ids:
            already_have.append(ing["item"])
            continue
        # The recipe's own wording decides the path, before any headcount
        # scaling — scaling can only ever turn a package into the same
        # package (you cannot buy two thirds of a jar), so asking the
        # unscaled quantity keeps the classification stable across meals.
        raw_qty = ing.get("qty", "") or ""
        category = ing.get("category", "other")
        # Split exactly the way _normalize_grocery_quantity does, so the
        # amount and the note that rides with it ("1 bag (2 lb), frozen")
        # come apart the same on both sides of the list. _parse_quantity
        # strips a prep descriptor ("3, diced") itself.
        core, note = _quantities._split_quantity_note(raw_qty.strip())
        pack_share = _counted_pack_share(ing["item"], core, default_servings)
        package = _quantities.package_unit(raw_qty)
        # A BARE COUNT of a whole thing — three tomatoes, two lemons, one
        # apple — is the one shape where the shopping amount and the
        # cooking amount are the same number, and the one the household
        # reported as "way too many" (Gowthami, 2026-10-04). Two things
        # are different for it, both about the number that goes IN rather
        # than the rounding that comes out:
        #
        #  - it is held to the most a plausible dish for this table uses
        #    (plausible_count_quantity), so a recipe that wrote the count
        #    per person rather than per dish does not ship it. Applied
        #    here rather than only at save time because this is the one
        #    choke point every ingest goes through, so it covers the
        #    recipes already on disk as well as the ones written from now
        #    on — the same argument the brought-over read above makes.
        #  - it uses count_scale_for_entry, which never multiplies that
        #    stated count above what the recipe wrote.
        #
        # A package and a counted pack keep their own paths and their own
        # factor: one bottle is one bottle, and a dozen eggs really does
        # scale with the eaters.
        bare_count = (
            not pack_share and not package
            and (_quantities._parse_quantity(core) or (0, ""))[1] in _PER_PERSON_COUNT_UNITS
        )
        if bare_count:
            core = plausible_count_quantity(ing["item"], core, default_servings)
        parsed_core = _quantities._parse_quantity(core)
        factor_for_entry = count_scale_for_entry if bare_count else scale_for_entry
        # Every meal in this group added together — the same sum the
        # buffer arrives at one share at a time, so the kitchen is asked
        # about the week's amount and not one night's.
        week_scale = sum(factor_for_entry[e] for e in contributing_ids)
        # What this whole recipe-week claims on the ingredient, written the
        # way the branches below write it into the buffer — so the kitchen
        # is asked about the amount that would actually be bought. A
        # PACKAGE is the exception and is asked about as one package,
        # because one package is what the branch below adds however often
        # the meal repeats.
        #
        # Freeform ("a bunch", "to taste", blank) names no amount, so
        # nothing can be shown to cover it and it is bought. That is the
        # one place this fix costs something rather than saving it: a
        # recipe that says "salt, to taste" now asks for salt every week,
        # where the name alone used to settle it. Kept because the
        # alternative is the shape of the bug — "there is some of this in
        # the house" standing in for "there is enough" — and because a
        # spice goes to the list's own spice section (spices.py) rather
        # than the aisles. One line to reverse if it reads as noise.
        if pack_share:
            need = (pack_share[0] * week_scale, pack_share[1])
        elif package:
            need = _quantities._parse_quantity(raw_qty)
        elif parsed_core:
            need = (parsed_core[0] * week_scale, parsed_core[1])
        else:
            need = None
        if stock.covers(ing["item"], need):
            already_have.append(ing["item"])
            continue
        if pack_share:
            # A COUNTED PACK — eggs, garlic. Whether the recipe wrote the
            # piece or the pack, what goes in the buffer is the pieces this
            # meal uses; the week's pieces add up and the line is written
            # in whole packs once (see _counted_pack_share and
            # quantities._PACK_CONVERSION_GROUPS). This is how four meals
            # that each said "1 dozen" buy one carton, not four.
            share, small_unit = pack_share
            for entry_id in contributing_ids:
                buffer.add(
                    entry_id, ing["item"], category,
                    share * factor_for_entry[entry_id], small_unit, note,
                )
        elif package:
            add_result = _grocery.add_grocery_item(
                ing["item"], quantity=raw_qty, category=category, added_by="ai",
                source_weekly_plan_id=weekly_plan_id, quantity_mode="max", conn=conn,
            )
            for entry_id in contributing_ids:
                _record_grocery_link(entry_id, ing["item"], add_result["item_id"], raw_qty, conn=conn)
        else:
            parsed = parsed_core
            if parsed:
                # Into the buffer unrounded, one share per meal. Nothing
                # reaches the list until every recipe in this pass has
                # added its claim on the same item.
                for entry_id in contributing_ids:
                    buffer.add(
                        entry_id, ing["item"], category,
                        parsed[0] * factor_for_entry[entry_id], parsed[1], note,
                    )
            else:
                # "A bunch", "to taste", blank. No number to scale or sum,
                # so it goes on per meal exactly as it always has and
                # _repeat_or_concatenate handles the repetition.
                for entry_id in contributing_ids:
                    add_result = _grocery.add_grocery_item(
                        ing["item"], quantity=raw_qty, category=category,
                        added_by="ai", source_weekly_plan_id=weekly_plan_id, conn=conn,
                    )
                    _record_grocery_link(entry_id, ing["item"], add_result["item_id"], raw_qty, conn=conn)
        # Once per ingredient, not once per meal: this is the list of
        # NAMES that landed on the shopping list, and approve_weekly_plan
        # counts it distinctly anyway.
        added_items.append(ing["item"])
    if own_buffer:
        buffer.flush()
    return added_items, already_have
