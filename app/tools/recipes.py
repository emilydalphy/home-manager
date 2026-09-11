"""
Recipes: adding, listing, scaling, feedback and cooking notes.
"""
from __future__ import annotations

import json
import math
import re
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
    source_url: str = "",
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
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO recipes (household_id, name, notes, ingredients_json, tags_json, food_groups_json, cuisine, main_protein, "
        "instructions_json, default_servings, prep_time_minutes, cook_time_minutes, advance_prep_notes, advance_prep_step_indices_json, "
        "source_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            household_id(), name, notes, json.dumps(ingredients), json.dumps(tags or []),
            json.dumps(food_groups or []), cuisine, main_protein,
            json.dumps(instructions or []), default_servings, prep_time_minutes, cook_time_minutes,
            advance_prep_notes, json.dumps(advance_prep_step_indices or []), source_url or "",
        ),
    )
    conn.commit()
    recipe_id = cur.lastrowid
    conn.close()
    return {
        "recipe_id": recipe_id, "name": name, "tags": tags or [], "food_groups": food_groups or [],
        "cuisine": cuisine, "main_protein": main_protein, "instructions": instructions or [],
        "default_servings": default_servings, "advance_prep_step_indices": advance_prep_step_indices or [],
        "source_url": source_url or "",
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
               cook_time_minutes, advance_prep_notes, advance_prep_step_indices_json, source_url
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
            # The web page it was brought in from, or '' (recipe import).
            "source_url": r["source_url"] or "",
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
            if unit in _DISCRETE_UNITS:
                # "0.5 heads of garlic" is not an amount anyone measures.
                # Same rounding cooking_quantity applies, so the two agree
                # about a thing that only comes whole.
                scaled = max(1.0, round(scaled))
            scaled_ingredients.append({**ing, "qty": _quantities._format_quantity(scaled, unit)})
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


def _table_lookup(item: str) -> str | None:
    """The table's per-4-servings amount for an ingredient name, longest
    matching key first ("black pepper" before "pepper")."""
    clean = _clean_item(item)
    if not clean:
        return None
    for key in _COOKING_KEYS_LONGEST_FIRST:
        if _item_matches(clean, key):
            return COOKING_QUANTITIES_PER_4[key]
    return None


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


def validate_measured_quantities(ingredients: list[dict], field: str = "qty") -> dict:
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
            out.append({**ing, "qty": stored, "shopping_qty": qty})
            continue
        if not _quantity_problem(item, qty):
            out.append(dict(ing))
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
        "SELECT id, ingredients_json FROM recipes WHERE household_id = ? AND LOWER(name) = LOWER(?)",
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
    conn.execute(
        "UPDATE recipes SET ingredients_json = ? WHERE id = ?",
        (json.dumps(ingredients), row["id"]),
    )
    conn.commit()
    conn.close()
    return {"name": recipe_name, "ingredients": ingredients}


# Words a step names without the ingredient list ever having to.
_STEP_ONLY_WORDS = {"water", "ice"}


def check_steps_ingredients_consistency(ingredients: list[dict], instructions: list[str]) -> dict:
    """
    A soft accuracy check on a recipe: does the method match the list?

    Two ways a generated recipe quietly goes wrong, both of which read to a
    household as "the recipe details are not accurate" (Julia, 2026-09-08):

    - an ingredient bought and then never used — it appears in no step;
    - a step reaching for something that was never on the list ("stir in
      the heavy cream", no cream anywhere), which is how a household finds
      out mid-cook that they didn't buy it.

    Only the second half needs a vocabulary of food words, and it uses the
    measurement table's own keys as that vocabulary — one list to maintain
    rather than two that drift. A known food word in a step is evidence; an
    unknown one is not, and is passed over rather than guessed at. That
    asymmetry is deliberate: a false "you forgot to buy shallots" is worse
    than a missed one, and this only ever logs.

    Returns {"ok", "unused_ingredients", "missing_from_list"} and never
    raises — an observation, not a gate.
    """
    names = [
        n for n in (
            (ing.get("item") or "").strip() if isinstance(ing, dict) else str(ing).strip()
            for ing in ingredients or []
        ) if n
    ]
    steps = [s for s in (instructions or []) if (s or "").strip()]
    if not names or not steps:
        return {"ok": True, "unused_ingredients": [], "missing_from_list": []}

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
        if key not in _STEP_ONLY_WORDS and _item_matches(text, key) and not _item_matches(listed, key)
    ]
    # Report the longest name for a thing, not every fragment of it:
    # "heavy cream", never "heavy cream" and "cream".
    missing = [m for m in matched if not any(m != other and m in other for other in matched)]
    return {
        "ok": not unused and not missing,
        "unused_ingredients": unused,
        "missing_from_list": sorted(missing),
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


def _record_grocery_link(entry_id: int, item: str, grocery_item_id: int, qty: str) -> None:
    """
    Record exactly what THIS meal contributed to that grocery line, before
    it got merged with anything else already there — see
    grocery._reverse_meal_grocery_contributions, which is what lets
    swap_meal_in_plan/swap_component_in_plan/clear_weekly_plan take this
    back out precisely if the meal is later swapped or dropped.
    """
    link_conn = get_conn()
    link_conn.execute(
        "INSERT INTO meal_plan_grocery_links (household_id, meal_plan_entry_id, grocery_item_id, item, quantity) "
        "VALUES (?, ?, ?, ?, ?)",
        (household_id(), entry_id, grocery_item_id, item, _quantities._strip_prep_descriptor(qty or "")),
    )
    link_conn.commit()
    link_conn.close()


_MEASURABLE_UNITS = {u for group in _quantities._UNIT_CONVERSION_GROUPS for u in group}


def _measurable(unit: str | None) -> bool:
    """A volume/weight unit that converts, as opposed to a countable thing
    (a pepper, a clove, a tin) or no unit at all."""
    return unit in _MEASURABLE_UNITS


def _week_bought_amount(amount: float, unit: str | None) -> tuple[float, str | None, float]:
    """
    A week's worth of one per-portion ingredient, rounded to something a
    person can actually buy — ONCE, on the total, in the unit the grocery
    line will actually be written in.

    Returns (rounded_amount, unit_to_write_it_in, quantum), where quantum
    is the step that rounding moved in — 1 whole pepper, or a quarter of a
    measurable unit. _apportion needs it to hand the rounded total back out
    to the meals in the same currency the line is denominated in.

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
    """
    if amount <= 0:
        return 0.0, unit, (0.25 if _measurable(unit) else 1.0)
    if not _measurable(unit):
        # You cannot buy 12.75 peppers. Up, not nearest: an extra pepper
        # costs a pepper, a missing one costs the dinner.
        return float(math.ceil(amount - 1e-9)), unit, 1.0
    rolled, rolled_unit = _quantities._roll_up_unit(amount, unit)
    nice = _quantities._round_to_nice_fraction(rolled)
    if nice <= 0:
        nice = 0.25  # never round a real quantity away to nothing
    return nice, rolled_unit, 0.25


def _apportion(total: float, shares: list[float], quantum: float) -> list[float]:
    """
    Hand ONE rounded week total back out to the meals that asked for it, so
    the ledger adds up to exactly what went on the list.

    This is the price of rounding once. The list says 13 peppers; the five
    dinners behind it wanted 2.25, 3, 1.5, 3 and 3. If each meal's ledger
    row recorded its own unrounded share, clearing the week would subtract
    12.75 from 13 and leave a phantom quarter of a pepper on the list,
    which then displays as one whole pepper nobody is cooking. So the
    rounded total is split by largest remainder — every row is a whole
    quantum, and they sum to the line exactly. Clearing a week empties it;
    swapping one dinner out takes a believable share with it.

    A meal can legitimately come out at zero (a tiny share of an amount
    that rounded down to nothing much). That is recorded as a real "0"
    rather than a blank, because a blank means "this contribution IS the
    whole line" to _subtract_quantity and would take the line away.
    """
    if not shares:
        return []
    n_quanta = int(round(total / quantum))
    if n_quanta <= 0:
        return [0.0] * len(shares)
    weight_total = sum(shares)
    if weight_total <= 0:
        # No meal has a claim on it in proportion; give it all to the first.
        return [n_quanta * quantum] + [0.0] * (len(shares) - 1)
    exact = [n_quanta * s / weight_total for s in shares]
    whole = [math.floor(e + 1e-9) for e in exact]
    remaining = n_quanta - sum(whole)
    order = sorted(
        range(len(shares)),
        key=lambda i: (-(exact[i] - whole[i]), -shares[i], i),
    )
    for i in order[:max(0, remaining)]:
        whole[i] += 1
    return [w * quantum for w in whole]


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

    def __init__(self, weekly_plan_id: int | None):
        self.weekly_plan_id = weekly_plan_id
        # (merge key, unit, note) -> the one grocery line that will become.
        # Two recipes writing the same item in units that don't reconcile
        # ("2 cups beans" and "1 lb beans") stay two entries here and meet
        # each other in add_grocery_item, which reports the disagreement
        # honestly instead of guessing a conversion.
        self._lines: dict[tuple, dict] = {}

    def add(self, entry_id: int, item: str, category: str, amount: float, unit: str | None, note: str) -> None:
        key = (_grocery._merge_key(item), unit, note)
        line = self._lines.get(key)
        if line is None:
            line = self._lines[key] = {
                "item": item, "category": category, "unit": unit, "note": note, "shares": {},
            }
        line["shares"][entry_id] = line["shares"].get(entry_id, 0.0) + amount

    def flush(self) -> None:
        for line in self._lines.values():
            entry_ids = list(line["shares"])
            shares = [line["shares"][e] for e in entry_ids]
            rounded, unit, quantum = _week_bought_amount(sum(shares), line["unit"])
            qty = _quantities._with_note(_quantities._format_quantity(rounded, unit), line["note"])
            add_result = _grocery.add_grocery_item(
                line["item"], quantity=qty, category=line["category"], added_by="ai",
                source_weekly_plan_id=self.weekly_plan_id,
            )
            for entry_id, share in zip(entry_ids, _apportion(rounded, shares, quantum)):
                _record_grocery_link(
                    entry_id, line["item"], add_result["item_id"],
                    _quantities._format_quantity(share, unit),
                )
        self._lines.clear()


def _add_recipe_ingredients_to_grocery_list(
    entry_id: int, recipe_ingredients: list[dict], weekly_plan_id: int | None,
    default_servings: int | None = None,
) -> tuple[list[str], list[str]]:
    """
    One planned meal's ingredients onto the grocery list — the single-meal
    door onto _add_recipe_ingredients_for_entries below, which is where
    the behaviour lives. Used by plan_meal and the swap paths, where there
    genuinely is only one meal to account for.
    """
    return _add_recipe_ingredients_for_entries(
        [entry_id], recipe_ingredients, weekly_plan_id, default_servings=default_servings
    )


def _add_recipe_ingredients_for_entries(
    entry_ids: list[int], recipe_ingredients: list[dict], weekly_plan_id: int | None,
    default_servings: int | None = None, buffer: "WeekGroceryBuffer | None" = None,
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
    so the arithmetic rounds once. Each meal's ledger row then carries an
    apportioned whole share of that rounded total (_apportion), which is
    what keeps reversal exactly symmetric — clearing the week empties the
    line rather than leaving a phantom quarter-pepper behind.

    A quantity with no number in it at all ("a bunch", "to taste") can't be
    summed, so it skips the buffer and goes on per meal exactly as before.

    A cook-once-eat-twice chain moves that factor a second time, and moves
    the other night to zero. A LEFTOVERS entry contributes NOTHING at all
    — its dinner was already bought on the night it is actually cooked,
    and buying the same recipe twice was the shopping half of the bug
    Emily reported on 2026-09-04. The SOURCE entry buys for the whole
    chain: its factor is multiplied by (everyone the batch feeds ÷ the
    people at the cook night's own table), so a Tuesday cook for three
    that also feeds a Thursday for three shops for six. The two factors
    compose rather than fight: each is relative to a different baseline
    (the household, then that night's table), so a chain with no
    attendance rows anywhere doubles exactly, and one with someone away on
    Thursday buys for five.

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
    row carries that meal's apportioned share of the rounded line, and a
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
    """
    from . import attendance as _attendance
    from . import leftovers as _leftovers

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
    entry_conn = get_conn()
    scale_for_entry: dict[int, float] = {}
    contributing_ids: list[int] = []
    chains_by_plan: dict[int, dict] = {}
    for entry_id in entry_ids:
        entry_row = entry_conn.execute(
            "SELECT date, slot, weekly_plan_id FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
        scale = (
            _attendance.servings_scale_factor(entry_row["date"], entry_row["slot"], default_servings)
            if entry_row else 1.0
        )
        if entry_row and entry_row["weekly_plan_id"]:
            plan_id = entry_row["weekly_plan_id"]
            if plan_id not in chains_by_plan:
                chains_by_plan[plan_id] = _leftovers.plan_leftover_chains(plan_id)
            chains = chains_by_plan[plan_id]
            # A reheat night buys nothing and links to nothing — it drops
            # out of the group entirely rather than returning early, since
            # the cook night it eats from shares this very group.
            if entry_id in chains["leftovers"]:
                continue
            source = chains["sources"].get(entry_id)
            if source:
                batch = _leftovers.batch_for_source(source)
                if batch["servings"] > 0 and batch["cook_eaters"] > 0:
                    scale *= batch["servings"] / batch["cook_eaters"]
        contributing_ids.append(entry_id)
        scale_for_entry[entry_id] = scale
    entry_conn.close()
    # Every meal in this group was a reheat, so the group buys nothing —
    # the same answer the single-meal path gives for a lone leftovers entry.
    if not contributing_ids:
        return [], []
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
    # A buffer of this group's own when nobody handed one down, so a single
    # meal planned in chat still rounds exactly once and this function has
    # only one code path.
    own_buffer = buffer is None
    if own_buffer:
        buffer = WeekGroceryBuffer(weekly_plan_id)

    for ing in recipe_ingredients:
        if ing["item"].strip().lower() in have_names:
            already_have.append(ing["item"])
            continue
        # The recipe's own wording decides the path, before any headcount
        # scaling — scaling can only ever turn a package into the same
        # package (you cannot buy two thirds of a jar), so asking the
        # unscaled quantity keeps the classification stable across meals.
        raw_qty = ing.get("qty", "") or ""
        category = ing.get("category", "other")
        if _quantities.package_unit(raw_qty):
            add_result = _grocery.add_grocery_item(
                ing["item"], quantity=raw_qty, category=category, added_by="ai",
                source_weekly_plan_id=weekly_plan_id, quantity_mode="max",
            )
            for entry_id in contributing_ids:
                _record_grocery_link(entry_id, ing["item"], add_result["item_id"], raw_qty)
        else:
            # Split exactly the way _normalize_grocery_quantity does, so
            # the amount and the note that rides with it ("1 bag (2 lb),
            # frozen") come apart the same on both sides of the list.
            # _parse_quantity strips a prep descriptor ("3, diced") itself.
            core, note = _quantities._split_quantity_note(raw_qty.strip())
            parsed = _quantities._parse_quantity(core)
            if parsed:
                # Into the buffer unrounded, one share per meal. Nothing
                # reaches the list until every recipe in this pass has
                # added its claim on the same item.
                for entry_id in contributing_ids:
                    buffer.add(
                        entry_id, ing["item"], category,
                        parsed[0] * scale_for_entry[entry_id], parsed[1], note,
                    )
            else:
                # "A bunch", "to taste", blank. No number to scale or sum,
                # so it goes on per meal exactly as it always has and
                # _repeat_or_concatenate handles the repetition.
                for entry_id in contributing_ids:
                    add_result = _grocery.add_grocery_item(
                        ing["item"], quantity=raw_qty, category=category,
                        added_by="ai", source_weekly_plan_id=weekly_plan_id,
                    )
                    _record_grocery_link(entry_id, ing["item"], add_result["item_id"], raw_qty)
        # Once per ingredient, not once per meal: this is the list of
        # NAMES that landed on the shopping list, and approve_weekly_plan
        # counts it distinctly anyway.
        added_items.append(ing["item"])
    if own_buffer:
        buffer.flush()
    return added_items, already_have
