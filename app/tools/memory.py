"""
What we know about the household -- the memory view, its freeform facts,
and editing or deleting a stored preference.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import household_id, require_household_row
from . import household as _household
from . import preferences as _preferences
from . import rhythm as _rhythm


# See schema.sql's comment on the `facts` table for why this is a separate
# layer from the structured meal_preferences/members fields.
def get_facts(category: str | None = None) -> list[dict]:
    """List household facts for the What We Know screen, optionally filtered to one category (people/taste/rhythm)."""
    conn = get_conn()
    if category:
        rows = conn.execute(
            "SELECT id, category, text, hard, author, updated_at FROM facts WHERE household_id = ? AND category = ? ORDER BY id ASC",
            (household_id(), category),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, category, text, hard, author, updated_at FROM facts WHERE household_id = ? ORDER BY category, id ASC",
            (household_id(),),
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
        (household_id(), category, text, 1 if hard else 0, author),
    )
    conn.commit()
    fact_id = cur.lastrowid
    conn.close()
    return {"added": True, "id": fact_id, "category": category, "text": text, "hard": hard}


def update_fact(fact_id: int, text: str | None = None, hard: bool | None = None) -> dict:
    """Edit an existing fact's text and/or hard flag in place."""
    conn = get_conn()
    row = conn.execute("SELECT text, hard FROM facts WHERE id = ? AND household_id = ?", (fact_id, household_id())).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No fact with id {fact_id}.")
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
    require_household_row(conn, "facts", fact_id, label="fact")
    conn.execute("DELETE FROM facts WHERE id = ? AND household_id = ?", (fact_id, household_id()))
    conn.commit()
    conn.close()
    return {"id": fact_id, "deleted": True}


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
    prefs = conn.execute("SELECT * FROM meal_preferences WHERE household_id = ?", (household_id(),)).fetchone()
    members = conn.execute(
        "SELECT name, age_group, dietary_restrictions_json FROM members WHERE household_id = ?", (household_id(),)
    ).fetchall()
    household = conn.execute("SELECT goals FROM households WHERE id = ?", (household_id(),)).fetchone()

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
        (household_id(),),
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
        (household_id(),),
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
        (household_id(),),
    ).fetchone()
    meals_cooked_row = conn.execute(
        "SELECT COUNT(*) AS c FROM meal_plan_entries WHERE household_id = ? AND cooked_status = 'done'",
        (household_id(),),
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

    rhythm = _rhythm.get_household_rhythm()
    rhythm_signals = _rhythm.rhythm_completeness_signals()
    context_completeness = _build_context_completeness(
        members=member_list, protein_preferences=protein_prefs, cuisine_preferences=cuisine_prefs,
        dislikes=dislikes, cooking_time_preference=cooking_time_pref, usual_stores=usual_stores,
        eating_style=eating_style, goals=goals,
        recipes_rated=recipes_rated_row["c"] if recipes_rated_row else 0,
        meals_cooked=meals_cooked_row["c"] if meals_cooked_row else 0,
        rhythm_lunch_location_set=rhythm_signals["lunch_location_set"],
        rhythm_meals_together_set=rhythm_signals["meals_together_set"],
        rhythm_cooking_role_set=rhythm_signals["cooking_role_set"],
        rhythm_dinner_window_set=rhythm_signals["dinner_window_set"],
        rhythm_planning_anchor_set=rhythm_signals["planning_anchor_set"],
        rhythm_leftovers_stance_set=rhythm_signals["leftovers_stance_set"],
    )

    return {
        # Loop Board "Onboarding: household rhythm without traditional
        # assumptions" — the structured rhythm facts (lunch location per
        # person, meals eaten together, who cooks), separate from the
        # freeform facts table's category='rhythm' notes (see get_facts).
        "rhythm": rhythm,
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
        "growth_count_this_month": _household.count_preference_events_this_month(),
        "context_completeness": context_completeness,
    }


# Weight of each context signal toward the overall completeness score
# (out of 100-ish — doesn't need to sum exactly, the score is earned/total).
#
# Reweighted 2026-09-03 per Emily's stated learning hierarchy (Loop Board,
# "Onboarding: household rhythm..."), her words: "learn more about their
# rhythm first as the first important, and their habits, then their
# preferences — this is the key benefit of this app." RHYTHM -> HABITS ->
# PREFERENCES, in that order:
#
#   - Rhythm (lunch_location/meals_together/cooking_role, below): the
#     highest-weighted cluster, individually and combined — the household's
#     standing pattern, learned once and corrected via chat.
#   - Habits (recipes_rated, meals_cooked): real usage signal, tied for
#     second-highest — a household actually cooking and reacting to plans
#     outweighs anything merely stated.
#   - Preferences (protein/cuisine/cooking_time/eating_style/etc.): stated
#     intent, weighted below both — useful, but the thing this app is
#     explicitly trying to need less of over time as rhythm and habits fill
#     in. dietary_restrictions is the one exception kept high regardless of
#     this hierarchy: it's a hard safety constraint, not a preference.
_CONTEXT_SIGNALS = [
    ("lunch_location", "Tell me where everyone typically is at lunchtime", "Drives whether lunch gets a real planned meal or something packable — this is your household's rhythm, the first thing I want to know.", 18),
    ("meals_together", "Tell me which meals your household eats together", "Shapes portioning and what counts as a shared meal here — part of your rhythm, not a preference.", 16),
    ("cooking_role", "Tell me who does the cooking", "Shapes who I address and how prep gets assigned — no assumptions made either way.", 16),
    # The three rhythm questions Emily locked afterward (Loop Board
    # "Onboarding: household rhythm...", "LOCKED: the six rhythm questions",
    # 2026-09-03) — same rhythm cluster, same reasoning, weighted just under
    # the original three so the earliest-asked rhythm facts still lead.
    ("dinner_window", "Tell me when dinner usually lands", "Times prep schedules and defrost reminders around when you actually eat — part of your rhythm.", 14),
    ("planning_anchor", "Tell me when your weekly plan should be ready", "Sets the day your plan and list are final, and when your week starts — part of your rhythm.", 14),
    ("leftovers_stance", "Tell me how you feel about leftovers", "Powers batch-cooking and ready-made suggestions instead of guessing — part of your rhythm.", 14),
    ("dietary_restrictions", "Note any dietary restrictions or allergies", "The single most important thing to get right before I suggest a week of meals.", 15),
    ("recipes_rated", "Rate a few recipes after cooking them", "The strongest habit signal I get — real reactions beat stated preferences every time.", 15),
    ("meals_cooked", "Cook a few planned meals and check them off", "Shows me the plan is actually being used, not just generated and ignored.", 15),
    ("members", "Add your household's members", "So dietary restrictions and portions can be personalized per person, not guessed at.", 10),
    ("protein_preferences", "Rate a few proteins you like or don't", "Helps me actually favor what your household enjoys instead of rotating blindly.", 10),
    ("cuisine_preferences", "Tell me a few cuisines you're into", "Keeps suggestions feeling like your food, not a generic rotation.", 10),
    ("cooking_time_preference", "Set a cooking time preference", "Keeps weeknight suggestions realistic for how much time you actually have.", 10),
    ("usual_stores", "Add the store(s) you usually shop at", "Powers store-aware grocery lists and shopping-trip planning.", 10),
    ("eating_style", "Tell me about your overall eating style", "e.g. vegetarian, keto, low-carb — shapes every suggestion, not just individual meals.", 10),
    ("dislikes", "Mention any standing dislikes", "So I stop suggesting the same thing you keep passing on.", 5),
    ("goals", "Share any household goals", "e.g. eating healthier, saving money, more variety — gives me something to optimize toward.", 5),
]


def _build_context_completeness(
    *, members, protein_preferences, cuisine_preferences, dislikes, cooking_time_preference,
    usual_stores, eating_style, goals, recipes_rated, meals_cooked,
    rhythm_lunch_location_set: bool = False, rhythm_meals_together_set: bool = False,
    rhythm_cooking_role_set: bool = False, rhythm_dinner_window_set: bool = False,
    rhythm_planning_anchor_set: bool = False, rhythm_leftovers_stance_set: bool = False,
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

    The six rhythm_* booleans come from rhythm.rhythm_completeness_signals
    (see get_household_memory) rather than being computed here, same as
    every other done_map entry takes its answer as a plain argument rather
    than reaching for a connection itself.
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
        "lunch_location": rhythm_lunch_location_set,
        "meals_together": rhythm_meals_together_set,
        "cooking_role": rhythm_cooking_role_set,
        "dinner_window": rhythm_dinner_window_set,
        "planning_anchor": rhythm_planning_anchor_set,
        "leftovers_stance": rhythm_leftovers_stance_set,
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

    _household._log_preference_event(field, "write")
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
            (household_id(), stored),
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
            (household_id(), json.dumps(value)),
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
            (household_id(), json.dumps(value)),
        )
        conn.commit()
        conn.close()
        return {"usual_stores": value}
    if field == "cuisine_preferences":
        return _preferences.set_household_meal_preferences(cuisine_preferences=value, mark_complete=False)
    if field == "protein_preferences":
        return _preferences.set_household_meal_preferences(protein_preferences=value, mark_complete=False)
    if field == "notes":
        return _preferences.set_household_meal_preferences(notes=value, mark_complete=False)
    if field == "novelty_preference":
        return _preferences.set_household_meal_preferences(novelty_preference=value, mark_complete=False)
    if field == "eating_style":
        return _preferences.set_household_meal_preferences(eating_style=value, mark_complete=False)
    if field == "dinners_per_week":
        return _preferences.set_household_meal_preferences(dinners_per_week=int(value), mark_complete=False)
    if field == "breakfasts_per_week":
        return _preferences.set_household_meal_preferences(breakfasts_per_week=int(value), mark_complete=False)
    if field == "lunches_per_week":
        return _preferences.set_household_meal_preferences(lunches_per_week=int(value), mark_complete=False)
    return _preferences.set_household_meal_preferences(cooking_time_preference=value, mark_complete=False)


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
    existing = conn.execute("SELECT * FROM meal_preferences WHERE household_id = ?", (household_id(),)).fetchone()
    if not existing:
        conn.close()
        raise ValueError("No saved preferences yet.")

    if field == "dislikes":
        updated = [d for d in json.loads(existing["dislikes_json"]) if d.lower() != (item or "").lower()]
        conn.execute(
            "UPDATE meal_preferences SET dislikes_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(updated), household_id()),
        )
    elif field == "cuisine_preferences":
        updated = [c for c in json.loads(existing["cuisine_preferences_json"]) if c.lower() != (item or "").lower()]
        conn.execute(
            "UPDATE meal_preferences SET cuisine_preferences_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(updated), household_id()),
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
            (json.dumps(updated), json.dumps(store_items), household_id()),
        )
    elif field == "protein_preferences":
        current = dict(json.loads(existing["protein_preferences_json"]))
        current.pop(item, None)
        conn.execute(
            "UPDATE meal_preferences SET protein_preferences_json = ?, updated_at = datetime('now') WHERE household_id = ?",
            (json.dumps(current), household_id()),
        )
    elif field == "notes":
        conn.execute(
            "UPDATE meal_preferences SET notes = '', updated_at = datetime('now') WHERE household_id = ?", (household_id(),)
        )
    elif field == "cooking_time_preference":
        conn.execute(
            "UPDATE meal_preferences SET cooking_time_preference = '', updated_at = datetime('now') WHERE household_id = ?",
            (household_id(),),
        )
    elif field == "eating_style":
        conn.execute(
            "UPDATE meal_preferences SET eating_style = '', updated_at = datetime('now') WHERE household_id = ?",
            (household_id(),),
        )
    elif field == "dinners_per_week":
        conn.execute(
            "UPDATE meal_preferences SET dinners_per_week = 7, updated_at = datetime('now') WHERE household_id = ?",
            (household_id(),),
        )
    elif field == "breakfasts_per_week":
        conn.execute(
            "UPDATE meal_preferences SET breakfasts_per_week = 7, updated_at = datetime('now') WHERE household_id = ?",
            (household_id(),),
        )
    elif field == "lunches_per_week":
        conn.execute(
            "UPDATE meal_preferences SET lunches_per_week = 7, updated_at = datetime('now') WHERE household_id = ?",
            (household_id(),),
        )
    else:
        conn.close()
        raise ValueError(f"Unknown preference field '{field}'.")
    conn.commit()
    conn.close()
    _household._log_preference_event(field, "delete")
    return {"field": field, "item": item, "deleted": True}
