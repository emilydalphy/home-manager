"""
Cook mode: recipe detail, the prep schedule, and checking things off.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id, require_household_row
from . import attendance as _attendance
from . import attention as _attention
from . import cook_ahead as _cook_ahead
from . import grocery as _grocery
from . import inventory as _inventory
from . import leftovers as _leftovers
from . import plates as _plates
from . import prep_sessions as _prep_sessions
from . import quantities as _quantities
from . import recipes as _recipes
from . import rhythm as _rhythm
from . import weekly_plan as _weekly_plan

# Slot states with no meal behind them, so nothing to cook. See
# get_cooker_view for why this is a deny-list rather than an allow-list of
# 'planned'. The ordered tuple and its placeholders exist only so
# get_plan_progress can apply the same rule inside SQL without the two
# drifting apart -- one definition, two ways of asking.
_NOT_COOKABLE_SLOT_STATES = frozenset({"planned_empty", "open"})
_NOT_COOKABLE_SLOT_STATES_ORDERED = tuple(sorted(_NOT_COOKABLE_SLOT_STATES))
_NOT_COOKABLE_PLACEHOLDERS = ",".join("?" * len(_NOT_COOKABLE_SLOT_STATES_ORDERED))


def _side_steps(sides: list[dict] | None) -> list[str]:
    """
    A side's own steps, worded so they read as what they are inside the
    main recipe's numbered list: something happening ALONGSIDE the dish,
    not step nine of it.

    The side is named on its first step only. Naming it on every step
    reads as a stutter in a list of four, and leaving it off entirely
    makes two sides indistinguishable from each other.
    """
    steps = []
    for side in sides or []:
        name = (side.get("name") or "").strip()
        own = [str(s).strip() for s in (side.get("instructions") or []) if str(s).strip()]
        for i, step in enumerate(own):
            steps.append(f"Alongside — {name}: {step}" if i == 0 and name else f"Alongside: {step}")
    return steps


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
    once for the whole batch since the ingredients were only actually used
    once — once per BATCH, not once per entry_id ticked, which is the
    distinction the paragraph below is built on (the claim is taken over
    every linked entry, so ticking a second sibling cannot buy a second
    helping of the same cook).

    A BATCH'S INGREDIENTS COME OUT OF INVENTORY AT MOST ONCE
    (2026-09-13, meal_plan_entries.inventory_depleted_at). This used to run
    on every single call with status='done', so ticking twice took the
    ingredients twice: 20 tortillas -> 12 -> 4 -> the row deleted outright,
    driven live. It is reachable two ways, and only the first is fixed by
    the unchanged-status no-op that grocery.mark_grocery_item has:

      - Today and Kitchen are separate build-once panels that can disagree
        about whether tonight is cooked, and Today's tick dispatches here
        through moves.set_move_done — so two unticked boxes can each post
        'done'. A retried or double-tapped POST is the same shape, and
        because /api/cooker/check-meal is a sync `def` route Starlette runs
        it in a threadpool, so two of those really do run at once.
      - The cook checkbox is a plain TOGGLE: shell.js renders
        data-next="pending" once a meal is done and posts whatever
        data-next says. So the ordinary way to send 'done' twice is
        tick -> untick -> tick, three taps on one control — and a status
        guard alone does not see that at all, because the status really did
        change in between.

    "At most once" therefore has to hold against two threads and across a
    whole component batch, not just against one row read a moment earlier —
    both of which the first version of this got wrong (17/20 concurrent
    pairs double-depleted; tick(A) -> untick(B) -> tick(B) took a second
    batch every time). So the depletion is CLAIMED before it runs, in one
    BEGIN IMMEDIATE transaction over every linked entry
    (_claim_inventory_depletion) — the same lock-from-the-first-read shape
    weekly_plan._replace_slot_entries uses, and for the same reason: read
    and write have to be one indivisible step or two writers both see NULL.
    Whoever loses the claim depletes nothing. The claim covers the whole
    linked set because a component batch is cooked once for all its
    siblings, so a stamp on any one of them means the food is gone.

    UN-TICKING DOES NOT PUT THE INGREDIENTS BACK, deliberately, which is
    why the memory has to be its own column rather than cooked_at (which an
    untick sets back to NULL). There is no ledger of what a depletion took
    — unlike a grocery line, which records grocery_item_links — so once
    this function returns, the amounts are gone. Every way of reversing it
    is a guess, and each is wrong in a different direction:
    deplete_inventory_for_meal DELETES a row whose quantity reaches zero or
    cannot be reconciled, so a re-created row would have lost its location,
    category and expiry; a depletion that reported success but wrote
    nothing back (units_reconciled False, where the tracked quantity was
    too imprecise to subtract from) took nothing at all, so "restoring" it
    would INVENT inventory; and the household may have edited those same
    rows in between. A partial restore is worse than none, because it
    silently inflates a pantry that then gets shopped against. So an untick
    means only "this is not cooked yet" — the result says inventory_restored:
    False rather than staying quiet about it — and putting something back is
    the inventory screen's job (or chat: "put the tortillas back"). What the
    column buys is that the mistake is bounded at one meal's worth however
    many times the box is tapped, instead of compounding per tap.

    That "any restore would be a guess" is true of the data as it stands,
    not of the design: this column could have carried the per-item delta and
    made an exact restore constructible. It doesn't, because a reversal
    still could not rebuild a DELETED row's location and expiry, and
    half-exact is the worst of the three. The door is open if Emily wants it.
    """
    conn = get_conn()
    row = conn.execute(
        """
        SELECT mpe.weekly_plan_id, mpe.cooked_status, mpe.inventory_depleted_at,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.id = ? AND mpe.household_id = ?
        """,
        (entry_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No meal plan entry with id {entry_id}.")
    linked_ids = [entry_id]
    linked_statuses = {entry_id: row["cooked_status"]}
    if row and row["weekly_plan_id"] is not None:
        plan_row = conn.execute(
            "SELECT planning_mode FROM weekly_plans WHERE id = ?", (row["weekly_plan_id"],)
        ).fetchone()
        if plan_row and plan_row["planning_mode"] == "component_based":
            siblings = conn.execute(
                """
                SELECT mpe.id, mpe.cooked_status FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
                WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ?
                  AND LOWER(COALESCE(r.name, mpe.freeform_meal)) = LOWER(?)
                """,
                (household_id(), row["weekly_plan_id"], row["meal"]),
            ).fetchall()
            if siblings:
                linked_ids = [r["id"] for r in siblings]
                linked_statuses = {r["id"]: r["cooked_status"] for r in siblings}

    # ANY linked entry, not just this row: unticking a component card whose
    # siblings were done is still an untick of something that was cooked.
    was_done = any(s == "done" for s in linked_statuses.values())
    result = {"entry_id": entry_id, "cooked_status": status, "linked_entry_ids": linked_ids}

    # Nothing to change at all: every linked entry already reads this way.
    # Written as "every linked entry" rather than a wholesale early return on
    # this one row, because a component-based card is done only when all its
    # siblings are (see get_cooker_view's merge) — a sibling planned after
    # the batch was cooked still needs the tick to reach it. Returning here
    # also leaves cooked_at where it was, so a second tap doesn't move the
    # time the meal was actually cooked.
    if all(s == status for s in linked_statuses.values()):
        conn.close()
        result["unchanged"] = True
        if status == "done":
            result["inventory_depleted"] = []
            result["inventory_queued_for_review"] = []
        return result

    cooked_at = "datetime('now')" if status == "done" else "NULL"
    conn.executemany(
        f"UPDATE meal_plan_entries SET cooked_status = ?, cooked_at = {cooked_at} WHERE id = ? AND household_id = ?",
        [(status, eid, household_id()) for eid in linked_ids],
    )
    conn.commit()
    conn.close()
    if status == "done":
        # Claim FIRST, deplete second. Reading "has this been depleted?" and
        # then depleting are two steps, and between them a second thread
        # reads the same NULL — see the docstring.
        #
        # Claiming first means the two ways this can go wrong both fail
        # toward UNDER-depleting, which is the direction chosen everywhere
        # else here: a crash inside deplete_inventory_for_meal leaves the
        # batch stamped whether or not the food came out, and a lock held
        # past the busy timeout between the status commit above and the
        # claim leaves the meal reading cooked with nothing taken (a plain
        # re-tick then early-returns on the unchanged status). Both leave a
        # pantry that says there is MORE than there is, which costs a trip;
        # claiming after the fact would fail the other way and cost a
        # dinner, and is the bug this whole change is about.
        if not _claim_inventory_depletion(linked_ids):
            # Somebody already has this batch's ingredients out of the
            # kitchen: a repeat tick, a re-tick after an untick, a component
            # sibling, or the other panel a millisecond ago.
            result["inventory_depleted"] = []
            result["inventory_queued_for_review"] = []
            result["inventory_already_depleted"] = True
        else:
            depletion = deplete_inventory_for_meal(entry_id)
            result["inventory_depleted"] = depletion["depleted"]
            result["inventory_queued_for_review"] = depletion["queued_for_review"]
            if not _changed_any_inventory_row(depletion["depleted"]):
                _release_inventory_depletion(linked_ids)
    elif was_done:
        # See the docstring: nothing is put back, and the caller is told so
        # rather than left to assume either way.
        result["inventory_restored"] = False
    return result


def _changed_any_inventory_row(depleted: list[dict]) -> bool:
    """
    Did the depletion pass actually move anything?

    Not the same question as "is `depleted` non-empty", which is what the
    first version of this asked and got wrong. An ingredient is reported as
    depleted whenever the recipe named an amount and the match was
    confident — INCLUDING the case where the tracked quantity was too
    imprecise to subtract from ("a big carton"), where
    _use_inventory_row_by_id deliberately writes nothing and says so with
    units_reconciled: False. Stamping that would be recording a depletion
    that did not happen, and would then block the real one for good once
    the household tidied the quantity up.

    Granularity is the whole entry, not the ingredient: a pass that moved
    one row and skipped another still counts, and the skipped one stays
    skipped. Per-ingredient memory is a ledger, which is the thing this
    column deliberately is not.
    """
    return any((d.get("result") or {}).get("units_reconciled") for d in depleted)


def _claim_inventory_depletion(entry_ids: list[int]) -> bool:
    """
    Take the right to deplete this batch, atomically. True if we got it.

    One BEGIN IMMEDIATE transaction, so the write lock is held from the
    FIRST read rather than from the first write — sqlite3's legacy
    isolation_level="" would otherwise leave a gap between reading NULL and
    stamping it, which is exactly the gap two threadpool workers both fit
    through. Same shape and same reasoning as
    weekly_plan._replace_slot_entries.

    All-or-nothing across the linked set: if ANY entry in a component batch
    is already stamped, the food is already out, so nobody claims.
    """
    if not entry_ids:
        return False
    placeholders = ",".join("?" * len(entry_ids))
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT inventory_depleted_at FROM meal_plan_entries "
            f"WHERE household_id = ? AND id IN ({placeholders})",
            (household_id(), *entry_ids),
        ).fetchall()
        if not rows or any(r["inventory_depleted_at"] is not None for r in rows):
            conn.rollback()
            return False
        conn.execute(
            f"UPDATE meal_plan_entries SET inventory_depleted_at = datetime('now') "
            f"WHERE household_id = ? AND id IN ({placeholders})",
            (household_id(), *entry_ids),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _release_inventory_depletion(entry_ids: list[int]) -> None:
    """
    Give the claim back, for a pass that moved nothing.

    A leftovers night, a freeform meal, a recipe nothing is tracked for and
    a quantity too imprecise to subtract from all take nothing, and a stamp
    left on those would mean the column read "this batch's ingredients have
    been taken" about food still on the shelf — and would block the real
    depletion later, when the night stops being a reheat or the quantity
    gets tidied up. Safe to release: whoever lost the claim depleted
    nothing, so the end state is the true one either way.

    Guarded like _claim_inventory_depletion — but note what the guard can
    and cannot do, because the two are easy to run together: it closes the
    connection, and it CANNOT save the stamp, since the UPDATE is the thing
    that failed. A raise here leaves the batch reading "taken" with nothing
    moved, permanently. That is the same under-depleting direction as the
    rest of this (see check_off_meal), so the guard is about not leaking a
    connection on top of it — the avoidable half of the two.
    """
    if not entry_ids:
        return
    placeholders = ",".join("?" * len(entry_ids))
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE meal_plan_entries SET inventory_depleted_at = NULL "
            f"WHERE household_id = ? AND id IN ({placeholders})",
            (household_id(), *entry_ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_off_prep_step(prep_task_id: int, status: str = "done") -> dict:
    """Mark a specific prep task (from generate_prep_schedule/get_prep_schedule — general, defrost, or a prep_cut added on a prep day) as done, skipped, or back to pending. 'skipped' is the defrost tile's one-tap decline — see static/shell.js's Today defrost tile — but is valid for any prep task, not defrost-specific."""
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
        # Counts a reheat night as its own item, same choice and same
        # reasoning as get_cooker_view's meals_done/meals_total — see the
        # comment there.
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
            # Same note, two truths: a chain the household picked itself
            # (cook_ahead.py) is portions cooked ahead on purpose, not
            # leftovers of a dinner. See leftovers.cook_ahead_note.
            card["covers_note"] = (
                _leftovers.cook_ahead_note(source, batch["servings"])
                if source.get("cook_ahead")
                else _leftovers.covers_note(source, batch["servings"])
            )
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
        card["leftovers_headline"] = (
            _leftovers.made_ahead_headline(src["meal"], src["date"])
            if leftover.get("cook_ahead")
            else _leftovers.leftovers_headline(src["meal"], src["date"])
        )
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


def _slot_rank(slot: str | None) -> int:
    """Eating order, the Python twin of weekly_plan.slot_order_sql. An
    unknown slot sorts LAST rather than disappearing, exactly as that one
    does."""
    slots = _weekly_plan.DAY_SLOTS
    try:
        return slots.index(slot)
    except ValueError:
        return len(slots)


def get_cooker_view(weekly_plan_id: int | None = None) -> dict:
    """
    Everything the person actually cooking needs for the current (or given)
    plan in one shot: each meal with its full recipe detail (ingredients,
    instructions, timing, advance-prep notes, cooked status), plus the prep
    schedule and overall progress — powers the dedicated Cooker view page
    rather than requiring separate get_weekly_plan/get_recipe/
    get_prep_schedule calls. Omit weekly_plan_id for the household's
    current plan.

    Omitting it also folds in the days-ahead meals that belong to no plan
    at all (weekly_plan.unplanned_meals_ahead) — a dinner answered on Now
    when no plan covers today, and every one-off chat plan_meal. Naming a
    weekly_plan_id asks about that plan and nothing else. See the comment
    at the top of the body for what that does and doesn't change, and
    UNPLANNED_HORIZON_DAYS for how far ahead it looks.

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

    # ...plus the meals for days ahead that no plan covers. An entry with no
    # weekly_plan_id is a real, first-class shape (see
    # weekly_plan.unplanned_meals_ahead): resolve_needs_you_dinner writes one
    # whenever the current plan's period doesn't reach the date, and every
    # one-off chat plan_meal writes one always. This view is what the cook
    # actually sees — Now's moves, cook mode and the Kitchen list are all
    # built off it — so leaving them out meant a meal that was saved and
    # then invisible on every screen (2026-09-13).
    #
    # Considered and NOT done: adding a second source to moves.py. It would
    # have put the move on Now and left cook mode and the Kitchen list still
    # empty, and made "the day's meals" a question with two answers that can
    # drift. (It WOULD have fixed the morning text: digest.build_morning_text
    # reads today_moves and nothing else — an earlier draft of this comment
    # named it as a third empty surface and was wrong.) Also NOT done: making
    # get_weekly_plan return unplanned entries — its name IS its scope, and
    # 15 call sites across nine modules read it as "that plan's rows".
    #
    # Only for the "what am I cooking now" question: a caller naming a
    # weekly_plan_id is asking about THAT plan and gets exactly it.
    #
    # Component-based plans are carved out on MECHANICS, not on dates. The
    # loose rows carry perfectly real dates; it is the branch below that
    # can't take them — it groups by dish name and batch-collapses repeats
    # into one card, which would fold a dated one-off into an undated
    # component or scale it to a batch nobody planned. KNOWN RESIDUE, not
    # fixed here: a component household whose current plan doesn't cover
    # today still has the whole original bug (reproduced 2026-09-13; see
    # test_a_component_household_still_has_this_bug, which characterises it
    # so the next session finds it written down). Its own card.
    loose_meals = (
        _weekly_plan.unplanned_meals_ahead(plan)
        if weekly_plan_id is None and plan.get("planning_mode") != "component_based"
        else []
    )

    if plan.get("weekly_plan_id") is None and not loose_meals:
        return {"weekly_plan_id": None, "meals": [], "prep_tasks": [], "prep_sessions": [], "prep_days_set": False, "meals_done": 0, "meals_total": 0, "prep_done": 0, "prep_total": 0, "all_away": False,
                "period_start_date": None, "day_count": 0, "cook_name": _cook_name()}

    if plan.get("weekly_plan_id") is None:
        # Loose meals with no plan behind them at all — a brand-new
        # household's first dinner. Stand in an empty day-based shell so the
        # one card-building pass below serves both cases; every plan-scoped
        # pass after it is skipped on plan_id being None, and weekly_plan_id
        # stays None all the way out to the payload, so Today's week-state
        # badge still reads "none".
        plan = {**plan, "week_start_date": None, "planning_mode": "day_based",
                "status": None, "day_count": 0, "meals": []}
    plan_id = plan["weekly_plan_id"]

    # A week where every dinner was deliberately marked planned_empty
    # (see _NOT_COOKABLE_SLOT_STATES above) — "core loop handoffs, slice 2"
    # item C: the household said it would be away the whole period, so an
    # empty Cook screen should say that rather than reading as though
    # nothing was ever planned. Checked against the raw plan, before the
    # filter below removes those slots from `meals`. Requires a dinner row
    # for every day of the period (plan["day_count"]) — a plan that's only
    # PARTLY marked away (a few nights out, the rest just never planned)
    # is not "the household is away," it's an ordinary under-planned week.
    # Component-based plans have no per-day dinner slot to test, so this
    # is always False there.
    if plan["planning_mode"] == "component_based":
        all_away = False
    else:
        dinner_rows = [m for m in plan["meals"] if m.get("slot") == "dinner"]
        all_away = (
            bool(dinner_rows)
            and len(dinner_rows) == plan["day_count"]
            and all(m.get("slot_state") == "planned_empty" for m in dinner_rows)
        )

    recipes_by_name = {r["name"].lower(): r for r in _recipes.list_recipes()}
    meals = []
    # Eating order across BOTH sources, not "the plan's days and then the
    # loose ones". kitchenTodayRows and cookRestOfWeekHtml (shell.js) walk
    # this list unsorted — see the 2026-09-10 "a day printed dinner before
    # lunch" entry — so appending would have printed tonight's answered
    # dinner after next Friday. A no-op when loose_meals is empty:
    # get_weekly_plan already hands its meals over in this exact order.
    for m in sorted(
        plan["meals"] + loose_meals,
        key=lambda r: (r["date"], _slot_rank(r["slot"]), r["entry_id"]),
    ):
        if m.get("slot_state") in _NOT_COOKABLE_SLOT_STATES:
            continue
        recipe = recipes_by_name.get((m["meal"] or "").lower())
        sides = m.get("sides") or []
        meals.append({
            "entry_id": m["entry_id"],
            "date": m["date"],
            "slot": m["slot"],
            "component_category": m["component_category"],
            "meal": m["meal"],
            "slot_state": m.get("slot_state"),
            "cooked_status": m["cooked_status"],
            "reasoning": m.get("reasoning"),
            # The dish, then whatever the app attached beside it to make a
            # full plate (see plates.py). Folded into the SAME two lists
            # rather than given their own section: someone cooking wants one
            # shopping-shaped ingredient list and one ordered set of steps,
            # not two recipes to interleave in their head. The side's steps
            # go on the END, which also keeps advance_prep_step_indices —
            # 1-based positions into `instructions` — pointing where they
            # always did.
            "ingredients": (recipe["ingredients"] if recipe else []) + _plates.side_ingredients(sides),
            "instructions": (recipe["instructions"] if recipe else []) + _side_steps(sides),
            "sides": sides,
            "sides_label": _plates.sides_label(sides),
            "default_servings": recipe["default_servings"] if recipe else None,
            "prep_time_minutes": recipe["prep_time_minutes"] if recipe else None,
            "cook_time_minutes": recipe["cook_time_minutes"] if recipe else None,
            "advance_prep_notes": recipe["advance_prep_notes"] if recipe else "",
            "advance_prep_step_indices": recipe["advance_prep_step_indices"] if recipe else [],
            "has_full_recipe": recipe is not None,
            # Where the recipe came from, ready to say (recipes.recipe_citation)
            # and the cookbook page photo(s) it was read from, if any —
            # None / [] for a generated or typed dish (recipe photo import,
            # 2026-09-13). The meal screen and cook mode print `citation`
            # as it comes rather than re-deriving it.
            "citation": recipe.get("citation") if recipe else None,
            "photo_urls": recipe.get("photo_urls", []) if recipe else [],
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
        # Every plan-scoped pass here is skipped when there is no plan: a
        # loose meal can be in no chain, cover no other night and carry no
        # cook-ahead offer, because all three are recorded against a plan.
        if plan_id is not None:
            _apply_leftover_chains(plan_id, meals, recipes_by_name)
            # ...and the offer to make one: the later days each card could
            # cook its portions for now (Emily, 2026-09-07, on a plan with
            # the same breakfast every morning). Runs after the chains so
            # the days already ticked and the reheat cards they produced
            # agree.
            _cook_ahead.attach_cook_ahead(plan_id, meals)
        else:
            for card in meals:
                card["cook_ahead"] = {"days": []}

        # A plain night — no chain, no reheat — was left at the recipe's
        # own default_servings even when the table it's actually for is a
        # different size, so the card could say "for 2" (attendance, read
        # off the same date+slot) right next to a "Serves 4" stepper (still
        # the recipe's own baseline) — two different answers to the same
        # question (Emily, 2026-09-07). Scale the recipe to who is actually
        # eating, same arithmetic _scale_card_to_batch uses for a chain.
        #
        # Deliberately NOT calling _scale_card_to_batch itself: it also
        # sets `servings`, which is reserved for "this card covers a batch
        # bigger than one night" (the source's "for 6" chip) — see that
        # field's docstring above and test_leftovers_batch's
        # test_a_chain_nobody_validated_changes_nothing, which pins
        # servings=None for an ordinary cook. Scaling every plain card to
        # its own attendance would make that signal fire on nearly every
        # card, not just batch nights. default_servings is what the
        # stepper (cookDetailHtml, shell.js) actually reads, so correcting
        # it alone is enough to make the two numbers agree.
        chains = _leftovers.plan_leftover_chains(plan_id) if plan_id is not None else {"sources": {}, "leftovers": {}}
        chained_entry_ids = set(chains["sources"].keys()) | set(chains["leftovers"].keys())
        for m in meals:
            if m["entry_id"] in chained_entry_ids:
                continue
            if not m.get("has_full_recipe") or not m.get("default_servings"):
                continue
            eaters = _leftovers.eaters_at(m["date"], m["slot"])
            if eaters:
                scaled = _recipes.scale_recipe(m["meal"], eaters)
                m["ingredients"] = scaled["scaled_ingredients"]
                m["default_servings"] = eaters

    # Last, once every card's ingredients and servings are final (batch
    # scaling, leftover chains, attendance): rewrite the amounts into ones
    # a cook can act on. Julia, 2026-09-08 — "it's saying stuff like 'one
    # bottle olive oil'... it should give actual measurements in the
    # cooking view."
    #
    # Deliberately a presentation pass at the very end rather than a change
    # to what a recipe stores: the SAME ingredient row is both a shopping
    # line and a cooking line, and the grocery list's "1 bottle olive oil"
    # is correct where it is (recipes._add_recipe_ingredients_for_entries
    # buys one bottle a week however many dinners name it). Only the cook
    # sees a measurement; `shopping_qty` carries the bought amount along
    # for anything that still wants it. Cards already put through
    # scale_recipe come back untouched — those quantities are measured
    # already, so nothing here fires twice.
    for m in meals:
        m["ingredients"] = _recipes.cooking_ingredients(
            m["ingredients"], servings=m.get("default_servings") or m.get("servings"),
        )

    # "I'll use something else instead", said while sorting the list
    # (grocery.substitute_grocery_item): the recipe keeps asking for fresh
    # oregano, and the card says what is actually going in. Matched on the
    # list's own merge key, so "Fresh oregano" and "fresh oregano, chopped"
    # both hear about it.
    swaps = {
        _grocery._merge_key(sw["original_item"]): sw
        for sw in _grocery.substitutions_for_plan(plan_id)
    }
    if swaps:
        for m in meals:
            for ing in m["ingredients"]:
                if not isinstance(ing, dict):
                    continue
                sw = swaps.get(_grocery._merge_key(ing.get("item") or ""))
                if sw:
                    ing["substitute"] = sw["alternative"]
                    ing["substitute_at_home"] = bool(sw["at_home"])

    prep_tasks = get_prep_schedule(plan_id) if plan_id is not None else []
    return {
        "weekly_plan_id": plan_id,
        "week_start_date": plan["week_start_date"],
        "planning_mode": plan["planning_mode"],
        "status": plan["status"],
        "meals": meals,
        # "N of M cooked" (see the Cook screen's title row) counts a reheat
        # night as its own item on both sides of the fraction, exactly like
        # an ordinary cook — it keeps its own entry_id and cooked_status,
        # and Emily's own name for checking it off is "Mark eaten"
        # (REHEAT_ACTION_LABEL in shell.js), not "skip" or "already
        # counted". The alternative — excluding reheats from the total —
        # would make a chain's source night read as though it alone were
        # "the whole week", which is less honest than counting each night
        # the household actually has to deal with, cooked or reheated.
        "meals_done": sum(1 for m in meals if m["cooked_status"] == "done"),
        "meals_total": len(meals),
        "prep_tasks": prep_tasks,
        "prep_done": sum(1 for t in prep_tasks if t["status"] == "done"),
        "prep_total": len(prep_tasks),
        # The prep days themselves, gathered into sessions (Loop Board
        # "Prep days", Emily 2026-09-04/09-08). Carried on THIS payload
        # rather than fetched separately by the Cook screen for the same
        # reason cook_ahead rides along: every /api/cooker/* write returns
        # the whole refreshed view, so ticking an item in a session
        # re-renders its "N of M done" without a round trip of its own.
        # Additive — nothing already on this payload changed shape.
        "prep_sessions": _prep_sessions.prep_sessions_for_plan(plan_id) if plan_id is not None else [],
        # "No sessions" means two different things — never asked, or asked
        # and answered with a day that happens to be quiet. Only the first
        # gets the Cook screen's offer to say which days you prep.
        "prep_days_set": _prep_sessions.has_prep_days(),
        "all_away": all_away,
        # The planning period itself, for Cook's shelf (2026-09-13, "The
        # shelf" design): one tile per night of the period, planned or
        # not, so the strip can show an empty night as an empty night
        # rather than skipping it. get_weekly_plan already works these
        # out (plan_period); they ride along here the way prep_sessions
        # does, so every /api/cooker/* write hands back a view the shelf
        # can redraw from. None / 0 when there is no plan — the loose
        # meals above carry real dates and the shelf falls back to a
        # week from today.
        "period_start_date": plan.get("period_start_date"),
        "day_count": plan.get("day_count") or 0,
        # Who cooks, when the household said one person does (the
        # cooking_role rhythm fact, 'one_person' + a name). Cook's Tonight
        # card puts the name in its eyebrow; any other answer ("we take
        # turns", "whoever's free") is None here and the card names nobody
        # rather than guessing. This is the first thing that READS the
        # answer — DESIGN_SYSTEM §2b S4 noted on 2026-09-11 that
        # cooking_role was stored and shown but never acted on.
        "cook_name": _cook_name(),
    }


def _cook_name() -> str | None:
    """The one person who cooks, by name, or None (see get_cooker_view)."""
    try:
        role = _rhythm.get_household_rhythm().get("cooking_role") or {}
    except Exception:
        return None
    if role.get("value") != "one_person":
        return None
    return (role.get("who") or "").strip() or None
