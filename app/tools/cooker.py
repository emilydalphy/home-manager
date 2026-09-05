"""
Cook mode: recipe detail, the prep schedule, and checking things off.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id, require_household_row
from . import attendance as _attendance
from . import attention as _attention
from . import inventory as _inventory
from . import leftovers as _leftovers
from . import quantities as _quantities
from . import recipes as _recipes
from . import weekly_plan as _weekly_plan

# Slot states with no meal behind them, so nothing to cook. See
# get_cooker_view for why this is a deny-list rather than an allow-list of
# 'planned'. The ordered tuple and its placeholders exist only so
# get_plan_progress can apply the same rule inside SQL without the two
# drifting apart -- one definition, two ways of asking.
_NOT_COOKABLE_SLOT_STATES = frozenset({"planned_empty", "open"})
_NOT_COOKABLE_SLOT_STATES_ORDERED = tuple(sorted(_NOT_COOKABLE_SLOT_STATES))
_NOT_COOKABLE_PLACEHOLDERS = ",".join("?" * len(_NOT_COOKABLE_SLOT_STATES_ORDERED))


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
        "SELECT id, item, quantity FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())
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
    if _quantities._parse_quantity(row["quantity"] or "") is None:
        conn.close()
        return {"item_id": item_id, "item": row["item"], "quantity": row["quantity"], "units_reconciled": False}
    remaining, reconciled = _inventory._try_subtract_quantity(row["quantity"] or "", minus_qty)
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

    A leftovers night depletes nothing. The ingredients were used on the
    night the batch was actually cooked and depleted then; taking them out
    of inventory a second time for the reheat would empty a shelf that is
    still full (Emily, 2026-09-04). See leftovers.plan_leftover_chains.
    """
    conn = get_conn()
    entry = conn.execute(
        "SELECT mpe.id, mpe.recipe_id, mpe.weekly_plan_id, COALESCE(r.name, mpe.freeform_meal) AS meal_name "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.id = ? AND mpe.household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    conn.close()
    if not entry or not entry["recipe_id"]:
        return {"entry_id": entry_id, "depleted": [], "queued_for_review": []}
    if entry["weekly_plan_id"] and entry_id in _leftovers.plan_leftover_chains(entry["weekly_plan_id"])["leftovers"]:
        return {"entry_id": entry_id, "depleted": [], "queued_for_review": []}

    try:
        recipe = _recipes.get_recipe(entry["meal_name"])
    except ValueError:
        return {"entry_id": entry_id, "depleted": [], "queued_for_review": []}
    ingredients = recipe.get("ingredients", [])
    if not ingredients:
        return {"entry_id": entry_id, "depleted": [], "queued_for_review": []}

    inventory = _inventory.get_inventory()
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
            _attention.add_attention_item("inventory_depletion", summary, {
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
            _attention.add_attention_item("inventory_depletion", summary, {
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
        (entry_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No meal plan entry with id {entry_id}.")
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
                (household_id(), row["weekly_plan_id"], row["meal"]),
            ).fetchall()
            if siblings:
                linked_ids = [r["id"] for r in siblings]

    cooked_at = "datetime('now')" if status == "done" else "NULL"
    conn.executemany(
        f"UPDATE meal_plan_entries SET cooked_status = ?, cooked_at = {cooked_at} WHERE id = ? AND household_id = ?",
        [(status, eid, household_id()) for eid in linked_ids],
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
    """Mark a specific prep task (from generate_prep_schedule/get_prep_schedule, general or defrost) as done, skipped, or back to pending. 'skipped' is the defrost tile's one-tap decline — see static/shell.js's Today defrost tile — but is valid for any prep task, not defrost-specific."""
    if status not in ("pending", "done", "skipped"):
        raise ValueError(f"status must be one of pending/done/skipped, not {status!r}.")
    conn = get_conn()
    require_household_row(conn, "prep_tasks", prep_task_id, label="prep task")
    conn.execute(
        "UPDATE prep_tasks SET status = ? WHERE id = ? AND household_id = ?",
        (status, prep_task_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"prep_task_id": prep_task_id, "status": status}


def get_prep_schedule(weekly_plan_id: int | None = None) -> list[dict]:
    """Get the generated prep-task schedule for a plan (see generate_prep_schedule and defrost.sync_defrost_tasks — both general and defrost tasks come back together, distinguished by task_type). Omit weekly_plan_id for the household's current/most recent plan."""
    conn = get_conn()
    if weekly_plan_id is None:
        row = _weekly_plan._current_weekly_plan_row(conn)
        if not row:
            conn.close()
            return []
        weekly_plan_id = row["id"]
    rows = conn.execute(
        "SELECT id, task_date, description, related_meal, status, task_type, "
        "inventory_item_id, meal_plan_entry_id, quantity FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND household_id = ? ORDER BY task_date ASC, id ASC",
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_prep_tasks(weekly_plan_id: int, tasks: list[dict]) -> dict:
    """
    Persist a generated (general/LLM-derived) prep schedule for a plan —
    internal helper used by generate_prep_schedule right after the LLM
    produces the task list. Replaces any previously-generated *general*
    tasks for this plan (re-generating supersedes, rather than appending
    duplicates) — scoped to task_type='general' so this never touches the
    separately-managed defrost rows (see defrost.sync_defrost_tasks, which
    is scoped the same way in the other direction).
    """
    conn = get_conn()
    conn.execute(
        "DELETE FROM prep_tasks WHERE weekly_plan_id = ? AND household_id = ? AND task_type = 'general'",
        (weekly_plan_id, household_id()),
    )
    for t in tasks:
        if not t.get("task_date") or not t.get("description"):
            continue
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, task_type) "
            "VALUES (?, ?, ?, ?, ?, 'general')",
            (household_id(), weekly_plan_id, t["task_date"], t["description"], t.get("related_meal", "")),
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

    Counts only slots there is something to cook, on the same rule and for
    the same reason as get_cooker_view -- see its docstring. This one
    matters just as much despite having no screen: it is a chat tool, and
    the system prompt names it as the way to answer "what's left to cook
    this week". Unfiltered, it answered with a total nobody could reach and
    handed back nameless entries carrying real entry_ids the assistant
    could pass to check_off_meal.
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {"weekly_plan_id": None, "meals_done": 0, "meals_total": 0, "prep_done": 0, "prep_total": 0}
    conn = get_conn()
    meal_rows = conn.execute(
        "SELECT mpe.id AS entry_id, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.cooked_status AS cooked_status "
        "FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        f"WHERE mpe.weekly_plan_id = ? AND mpe.slot_state NOT IN ({_NOT_COOKABLE_PLACEHOLDERS})",
        (plan["weekly_plan_id"], *_NOT_COOKABLE_SLOT_STATES_ORDERED),
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


def _scale_card_to_batch(card: dict, batch_servings: int) -> bool:
    """
    Cook ONE card for the whole batch it actually has to cover: scale its
    ingredients to `batch_servings` and say so on the card.

    Both planning modes need this and neither owns it. A component-based
    plan batches because the same component is planned into several meals
    (see the merge in get_cooker_view); a day-based plan batches because
    one night's cook also feeds a later night's leftovers (see
    leftovers.py). The arithmetic and the fields it writes are identical,
    so they share this rather than each keeping their own copy — which is
    how the day-based path came to have none at all.

    `servings` is what the "for N" chip reads. `default_servings` moves
    with it so the Cook screen's live servings stepper starts from the
    batch the card is actually about, not from the recipe's own baseline.
    Returns whether it scaled: a freeform meal, a recipe with no baseline
    servings, or a batch of zero (a household with nobody on record) is
    left exactly as written rather than scaled to a guess.
    """
    if batch_servings <= 0 or not card.get("has_full_recipe") or not card.get("default_servings"):
        return False
    scaled = _recipes.scale_recipe(card["meal"], batch_servings)
    card["ingredients"] = scaled["scaled_ingredients"]
    card["default_servings"] = batch_servings
    card["servings"] = batch_servings
    return True


def _apply_leftover_chains(weekly_plan_id: int, meals: list[dict], recipes_by_name: dict) -> None:
    """
    The day-based half of batch cooking (Emily, 2026-09-04): one night
    cooks for the whole chain, and the nights eating its leftovers stop
    pretending to be cooks.

    For the SOURCE entry — scale the recipe to everyone the batch has to
    feed (that night's table plus each leftover night's), put that number
    on `servings` for the "for 6" chip, and write `covers_note` naming the
    nights it covers.

    For each LEFTOVER entry — mark `is_leftovers` and strip the cook out
    of it: no ingredients, no instructions, no advance-prep notes, no
    recipe. It keeps its own entry_id (it is still a real night that can
    be checked off) and gains `leftovers_from`/`leftovers_headline` so the
    screen can say what it is and where it came from, plus `reheat_note`
    if the recipe happens to carry reheating advice.

    Chains come from leftovers.plan_leftover_chains, which only honours a
    pairing both entries agree on — so a plan whose chains were never
    validated is left exactly as it was.
    """
    chains = _leftovers.plan_leftover_chains(weekly_plan_id)
    if not chains["sources"]:
        return
    by_entry = {m["entry_id"]: m for m in meals}

    for source in chains["sources"].values():
        card = by_entry.get(source["entry_id"])
        if card is None:
            continue
        batch = _leftovers.batch_for_source(source)
        card["covers"] = [
            {"date": t["date"], "slot": t["slot"], "eaters": t["eaters"]} for t in batch["targets"]
        ]
        if batch["servings"] > 0:
            _scale_card_to_batch(card, batch["servings"])
            card["covers_note"] = _leftovers.covers_note(source, batch["servings"])
        else:
            # Nothing countable to scale to (no members on record yet).
            # The pairing is still real, so still say it — in the words
            # repair_leftover_chains already wrote for this plan.
            card["covers_note"] = source.get("note") or ""

    for leftover in chains["leftovers"].values():
        card = by_entry.get(leftover["entry_id"])
        if card is None:
            continue
        src = leftover["source"]
        card["is_leftovers"] = True
        card["leftovers_from"] = src
        card["leftovers_headline"] = _leftovers.leftovers_headline(src["meal"], src["date"])
        card["reheat_note"] = _leftovers.reheat_note(recipes_by_name.get((src["meal"] or "").lower()))
        card["servings"] = _leftovers.eaters_at(leftover["date"], leftover["slot"]) or None
        # A reheat is not a cook. Emptied rather than left in place so no
        # screen can render this night as a second cook of the same dish
        # by reading a field it happens to still find populated.
        card["ingredients"] = []
        card["instructions"] = []
        card["advance_prep_notes"] = ""
        card["advance_prep_step_indices"] = []
        card["has_full_recipe"] = False
        card["default_servings"] = None


def get_cooker_view(weekly_plan_id: int | None = None) -> dict:
    """
    Everything the person actually cooking needs for the current (or given)
    plan in one shot: each meal with its full recipe detail (ingredients,
    instructions, timing, advance-prep notes, cooked status), plus the prep
    schedule and overall progress — powers the dedicated Cooker view page
    rather than requiring separate get_weekly_plan/get_recipe/
    get_prep_schedule calls. Omit weekly_plan_id for the household's
    current plan.

    Only slots there is something to COOK are included. A slot is one of
    three states (see meal_plan_entries.slot_state) and two of them have no
    meal behind them:

    - 'planned_empty' — nobody is home. schema.sql calls this out as the
      one deliberately empty slot in a week, which "must NEVER be offered
      to the household as one". It was reaching this view because the view
      never asked for slot_state, so a night nobody is home rendered as a
      cookable row with an empty name and a checkbox -- and, if it fell on
      today's dinner, as the Cook hero, headlined "Dinner", captioned with
      the reason nobody is eating, and offering to start cooking it.
    - 'open' — a decision genuinely handed back, carrying open_reason.
      There is no meal here either, so it cannot be cooked; it belongs to
      the Plan screen, which already renders open slots as the question
      they are.

    get_weekly_plan already carries slot_state for exactly this reason (its
    own comment records the same bug being caught in a chat turn), so this
    is a filter, not new plumbing.

    slot_state is forwarded per meal, but note what that is and isn't for:
    every row that survives the filter is cookable by construction, so it
    cannot be used to say anything about the nights that were skipped. It
    is there so a reader of one meal can see the state rather than infer
    it. A screen that wants to say "you're away Friday" needs the skipped
    slots themselves, which this deliberately does not return -- Cook is
    the "what am I making now" state, and what to say about an away night
    there is a copy decision nobody has made yet.

    Anything that is not one of those two states counts as cookable, rather
    than testing for 'planned' — the column arrived by ALTER TABLE with a
    'planned' default, and a filter that only trusted an exact match would
    blank the whole view for any row that ever held something else.

    A meal that is really a leftovers night comes back with
    is_leftovers=True and no recipe on it at all — the cook happened on an
    earlier night, and that earlier night's card carries the whole batch
    (`servings`, scaled ingredients, and `covers_note` naming the nights
    it feeds). See _apply_leftover_chains.
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {"weekly_plan_id": None, "meals": [], "prep_tasks": [], "meals_done": 0, "meals_total": 0, "prep_done": 0, "prep_total": 0}

    recipes_by_name = {r["name"].lower(): r for r in _recipes.list_recipes()}
    meals = []
    for m in plan["meals"]:
        if m.get("slot_state") in _NOT_COOKABLE_SLOT_STATES:
            continue
        recipe = recipes_by_name.get((m["meal"] or "").lower())
        meals.append({
            "entry_id": m["entry_id"],
            "date": m["date"],
            "slot": m["slot"],
            "component_category": m["component_category"],
            "meal": m["meal"],
            "slot_state": m.get("slot_state"),
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
            # Set for real by _apply_leftover_chains below. Present on
            # every meal (not only the ones it applies to) so a screen can
            # branch on it without first checking whether the field exists.
            "is_leftovers": False,
            # The "for N" chip. None unless this card stands for a batch
            # bigger than the recipe's own baseline — see
            # _scale_card_to_batch.
            "servings": None,
        })

    # Headcount for the focused cook-mode screen ("for 2 + 1 guest") — real
    # only for a day-based plan, where date+slot name an actual meal someone
    # is actually sitting down to. A component-based plan's date is a
    # placeholder (see get_weekly_plan), so attendance there would answer a
    # question nobody asked; leave it out rather than show a number that
    # looks precise and means nothing.
    for m in meals:
        att = None
        if plan["planning_mode"] != "component_based":
            try:
                slot_att = _attendance.get_slot_attendance(m["date"], m["slot"])
                att = {
                    "headcount": slot_att["headcount"],
                    "present_count": len(slot_att["present_member_ids"]),
                    "guest_count": slot_att["guest_count"],
                    "absent_names": slot_att["absent_names"],
                    "everyone_home": slot_att["everyone_home"],
                }
            except Exception:
                att = None
        m["attendance"] = att

    if plan["planning_mode"] == "component_based":
        # get_weekly_plan's meals are ordered by date/slot, which is
        # meaningless for a component-based plan (every entry shares the
        # same placeholder date) — order by the canonical component
        # category order instead (protein, vegetable, carb, etc.) so the
        # Cooker view reads grouped the same way the plan itself was
        # organized, rather than incidental insertion order.
        cat_rank = {c: i for i, c in enumerate(_weekly_plan._COMPONENT_CATEGORY_ORDER)}
        meals.sort(key=lambda m: cat_rank.get(m["component_category"] or "", len(_weekly_plan._COMPONENT_CATEGORY_ORDER)))

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
            batch_servings = (g["default_servings"] or 0) * count if count > 1 else 0
            if _scale_card_to_batch(g, batch_servings):
                g["batch_note"] = f"Bulk-cook once — makes enough for all {count} meals this week."
            else:
                g["batch_note"] = None
            merged_meals.append(g)
        meals = merged_meals
    else:
        # The day-based equivalent of the merge above: a night whose batch
        # also feeds a later night's leftovers cooks once, for everyone.
        _apply_leftover_chains(plan["weekly_plan_id"], meals, recipes_by_name)

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
