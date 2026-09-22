"""
Cook mode: recipe detail, the prep schedule, and checking things off.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import get_conn
from ._shared import household_id, require_household_row
from . import attendance as _attendance
from . import attention as _attention
from . import batch_components as _batch_components
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

logger = logging.getLogger(__name__)

MEAL_COOKED_STATUSES = ("pending", "done")


class InvalidMealStatus(ValueError):
    """
    A cooked status outside MEAL_COOKED_STATUSES. The two screen paths
    cannot produce one: the cook checkbox is a toggle and every site that
    renders it writes data-next as 'done' or 'pending', and Now's tick
    computes the same two. THE CHAT TOOL IS THE THIRD CALLER AND IS NOT
    A GUARANTEE — its schema enumerates the two, but this app's own rule
    is that telling the generator something is not the same as
    preventing it, and the tool immediately next to it in
    TOOL_DEFINITIONS, check_off_prep_step, enumerates 'skipped' and
    talks about skipping. "We skipped Wednesday's dinner" is a plausible
    way to reach this, not a theoretical one. That is a good outcome —
    the model is handed a sentence it can act on instead of writing a
    garbage status and reporting success — but it means this can appear
    in error_events as check_off_meal / InvalidMealStatus, which is the
    guard working rather than a new breakage.

    Its own marker type, distinct from this function's own "No meal plan
    entry with id N." ValueError, so the route can answer 422 ("that
    request doesn't make sense") rather than the 404 that means "no such
    meal" — the shape chores.InvalidChoreStatus already uses one door
    over. It IS a ValueError subclass, which is exactly why the route's
    except for it must come before the plain one; ordering is
    load-bearing here, not tidiness.

    Without it a typo'd status wrote straight through to
    meal_plan_entries.cooked_status and left the row in a state no
    screen's WHERE clause looks for: not done, not pending, just gone.
    Nothing heals a row already written that way — reaching the bug
    needed a hand-made request, so the count in the wild is likely zero,
    but this closes the door rather than sweeping up behind it.
    """


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

    THIS IS WHERE A RECIPE'S COOK COUNT MOVES. recipes.times_cooked and
    last_cooked_date ("you've made this 4 times", "last cooked in
    August") follow the tick, not the plan: they go up once when a night
    is marked done and back down when it is marked not-cooked — see
    _move_recipe_cook_counters. Planning a recipe (plan_meal) leaves them
    alone, so a discarded draft, a swapped night or a week that was never
    cooked cannot leave a phantom count behind.

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
    # Before the connection, so a status nothing can read never reaches the
    # row. The cooked tick drives times_cooked and the inventory claim off
    # this column, and both read it by name.
    if status not in MEAL_COOKED_STATUSES:
        raise InvalidMealStatus(
            f"A meal is cooked or it isn't — status must be "
            f"{' or '.join(MEAL_COOKED_STATUSES)}, not {status!r}."
        )
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
        # Still forget a start on a not-cooked row: "Mark not cooked" is
        # the one way to clear a start tapped by mistake, and it must work
        # whether or not the tick itself has anything to change.
        if status == "pending":
            conn.executemany(
                "UPDATE meal_plan_entries SET cook_started_at = NULL WHERE id = ? AND household_id = ?",
                [(eid, household_id()) for eid in linked_ids],
            )
            conn.commit()
        conn.close()
        result["unchanged"] = True
        if status == "done":
            result["inventory_depleted"] = []
            result["inventory_queued_for_review"] = []
        return result

    cooked_at = "datetime('now')" if status == "done" else "NULL"
    # "Mark not cooked" also forgets when the cook began (cook_started_at,
    # 2026-09-13): a night put back to not-cooked is a night still to
    # cook, and its clock goes back to the plan's arithmetic everywhere
    # that reads it. Marking DONE leaves the start alone — it is a true
    # fact about the cook that just happened, and every reader already
    # says "Cooked." over it rather than a clock.
    started_at = "cook_started_at" if status == "done" else "NULL"
    conn.executemany(
        f"UPDATE meal_plan_entries SET cooked_status = ?, cooked_at = {cooked_at}, "
        f"cook_started_at = {started_at} WHERE id = ? AND household_id = ?",
        [(status, eid, household_id()) for eid in linked_ids],
    )
    # Once per BATCH, like the depletion below: a component batch with a
    # sibling already done was counted when that sibling was ticked, and
    # unticking any sibling puts the whole batch back, so the count comes
    # off once. `was_done` is exactly "had this batch been counted?".
    if status == "done" and not was_done:
        _move_recipe_cook_counters(conn, linked_ids, cooked=True)
    elif status == "pending" and was_done:
        _move_recipe_cook_counters(conn, linked_ids, cooked=False)
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
            # The CANONICAL entry_id for this batch, not whichever sibling
            # was tapped THIS time (found 2026-09-13, adversarial review of
            # the attention-reopen fix above: deplete_inventory_for_meal
            # queues its "how much did you use?" attention item keyed on
            # entry_id+ingredient — see attention.add_attention_item — so
            # that reopen only works if the batch always hands it the SAME
            # entry_id. A component batch's own claim already gets released
            # when nothing was actually reconciled (a no-qty ingredient goes
            # to queued_for_review, not depleted — see
            # _changed_any_inventory_row), which is exactly the shape an
            # untick/re-tick of a batch with such an ingredient takes: the
            # SAME batch is depleted again on every re-tick, same as a
            # single-entry meal. But unlike a single-entry meal, a
            # component-based re-tick can arrive through a DIFFERENT sibling
            # checkbox than the one originally tapped (Today/Kitchen both
            # dispatch here, and shell.js's toggle can land on any sibling in
            # the merged card) — passing that sibling's own entry_id straight
            # through would queue a brand-new attention item instead of
            # reopening the first, and answering it would re-deplete the
            # same shelf a second time, the very bug this file exists to
            # fix. linked_ids names the same full sibling set regardless of
            # which one was tapped (the siblings query above keys off
            # weekly_plan_id + meal name, not entry_id), so its minimum is a
            # stable stand-in for "the batch" across every tap — ids only
            # grow as new siblings are planned, so a later-added sibling
            # never changes it.
            depletion = deplete_inventory_for_meal(min(linked_ids))
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


def _is_leftovers_night(derived_from_json: str | None) -> bool:
    """
    A night whose derived_from.links_to names an earlier cook is a reheat
    of that cook, not a cook of its own (see leftovers.py). The bare
    links_to is the test, not the fully validated chain
    plan_leftover_chains builds: the question here is only whether THIS
    night was meant as leftovers, and a half-written chain still was.
    repair_leftover_chains strips links_to off a night whose claim did
    not hold up, so a night that reads as an ordinary cook here is one.
    """
    try:
        derived = json.loads(derived_from_json or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(derived, dict) and bool(str(derived.get("links_to") or "").strip())


def _move_recipe_cook_counters(conn, entry_ids: list[int], cooked: bool) -> None:
    """
    Bump (cooked=True) or reverse (cooked=False) recipes.times_cooked /
    last_cooked_date for the recipe(s) behind these entries. Loop Board
    "A recipe counts as cooked the moment it's planned" (Emily): the two
    columns are what "you've made this 4 times" and "last cooked in
    August" read, and they feed the variety rules in the generation
    prompt, so they have to mean meals the household actually ate.

    Called AFTER the entries' cooked_status has been written on `conn`, so
    "every other done night of this recipe" below already excludes the
    ones being unticked. Nothing here commits; the caller owns the
    transaction, and the tick and its count land together or not at all.

    - A leftovers night (_is_leftovers_night) is skipped: ticking it eaten
      is not a cook. A freeform entry has no recipe row, so nothing to
      count.
    - One bump per recipe per batch, whatever the batch's size — a
      component batch is several rows for one cook.
    - last_cooked_date is the meal's own date (the night it was for, as
      the old planning-time bump also wrote), and only ever moves forward
      on a tick: marking an older night done late does not pull "last
      cooked" backwards. On an untick it is recomputed only if this was
      the latest cook — it falls back to the newest remaining done night,
      or to NULL when none is left.
    - times_cooked is nudged, not recomputed from rows, so history
      survives a cooked entry later being deleted with its plan. It never
      goes below 0.
    """
    if not entry_ids:
        return
    marks = ",".join("?" for _ in entry_ids)
    rows = conn.execute(
        f"""
        SELECT recipe_id, date, derived_from_json FROM meal_plan_entries
        WHERE id IN ({marks}) AND household_id = ? AND recipe_id IS NOT NULL
        """,
        (*entry_ids, household_id()),
    ).fetchall()
    latest_by_recipe: dict[int, str] = {}
    for r in rows:
        if _is_leftovers_night(r["derived_from_json"]):
            continue
        latest_by_recipe[r["recipe_id"]] = max(latest_by_recipe.get(r["recipe_id"], ""), r["date"] or "")
    for recipe_id, meal_date in latest_by_recipe.items():
        if cooked:
            conn.execute(
                """
                UPDATE recipes
                   SET times_cooked = times_cooked + 1,
                       last_cooked_date = CASE
                           WHEN last_cooked_date IS NULL OR last_cooked_date < ? THEN ?
                           ELSE last_cooked_date END
                 WHERE id = ? AND household_id = ?
                """,
                (meal_date, meal_date, recipe_id, household_id()),
            )
            continue
        remaining = conn.execute(
            """
            SELECT date, derived_from_json FROM meal_plan_entries
            WHERE household_id = ? AND recipe_id = ? AND cooked_status = 'done'
            """,
            (household_id(), recipe_id),
        ).fetchall()
        newest = max(
            (r["date"] for r in remaining if r["date"] and not _is_leftovers_night(r["derived_from_json"])),
            default=None,
        )
        conn.execute(
            """
            UPDATE recipes
               SET times_cooked = MAX(times_cooked - 1, 0),
                   last_cooked_date = CASE WHEN last_cooked_date = ? THEN ? ELSE last_cooked_date END
             WHERE id = ? AND household_id = ?
            """,
            (meal_date, newest, recipe_id, household_id()),
        )


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


# ---------- the real start ----------
# Loop Board "Cook: the real start time moves the clock (and says so once)"
# — Emily, 2026-09-13: "It's good to set the planned start time, but if the
# user ends up starting at a different time it should auto connect to
# whatever time it is for them and update the done time accordingly too.
# And it can make a little pop up note that it adjusted for actual timing."
#
# Until this, every clock in the app was PLANNED only: moves.py worked the
# start back from the dinner hour, and the Meal step's stops did the same
# arithmetic in shell.js. "Start cooking" in cook mode wrote nothing down,
# so a cook that began at 6:02 kept reading "Start by 5:45" all evening and
# every stop time was fifteen minutes stale. Now the tap records the real
# start on the entry (cook_started_at), and every reader rebases from it.

DEFAULT_TIMEZONE = "America/Toronto"  # the same default digest.py and holidays.py fall back to


def household_zone() -> ZoneInfo:
    """
    The zone the household lives in, for the caller that needs the zone
    itself rather than the time in it.

    household_now below answers "what does the household's clock read at
    this instant". This is the opposite question: where does one of the
    household's own DAYS begin, as an instant? pre_shop.get_already_have_
    decisions is the caller — removed_at is a UTC instant and the window
    it is measured against is a household day, so the day has to be turned
    back into the instant it started at before the two can be compared at
    all. SQLite cannot do that conversion: it knows UTC and the SERVER's
    local zone, and neither is the household's.

    Opens a connection. An unreadable zone name falls back to Toronto —
    the same choice household_now has always made, and it lives here now
    rather than in two places, because a reader whose day started in a
    different zone from another reader's is the whole bug this is for.
    """
    conn = get_conn()
    row = conn.execute("SELECT timezone FROM households WHERE id = ?", (household_id(),)).fetchone()
    conn.close()
    name = (row["timezone"] if row else None) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def household_now(now_utc: datetime | None = None) -> datetime:
    """
    Now, on the household's own clock (households.timezone), as the naive
    local datetime the rest of this app's clocks are in — moves.py's
    windows, get_week_menu's slot_times and cook_started_at all say
    "18:02" and mean the household's 18:02, never the server's. The
    deployed container runs in UTC, so reading datetime.now() here would
    have stamped a Toronto dinner as starting at ten at night.

    `now_utc` is for tests. The zone read, and the fallback to Toronto for
    a name ZoneInfo can't make sense of, are household_zone's above — so a
    bad setting never stops a cook from starting, and never leaves this
    reader in a different zone from the one asking where a day begins.
    """
    zone = household_zone()
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(zone).replace(tzinfo=None, microsecond=0)


def household_today(now_utc: datetime | None = None) -> date:
    """
    Today where the household lives, not where the container runs — the
    date half of household_now, for the caller below that only needs a
    day.

    The deployed container is UTC and households default to
    America/Toronto, so from 8pm Eastern the server's date is already
    tomorrow. get_cooker_view's staleness check read date.today(), which
    meant that on the LAST day of a plan's period, from 8pm local, the
    plan's last day was "before today" by the server's reckoning: the view
    collapsed to the same empty shape as no-plan-at-all and the Cook tab
    said "Nothing planned this week yet" for a week still running, on the
    evening the household is most likely to be cooking from it. Now's
    timeline reads the household's day (moves.py, 2026-09-14) and so does
    unplanned_meals_ahead, so this was the last half of the view still
    asking a different clock what day it is.

    Opens a connection (household_now does), so resolve it ONCE per view
    and thread the answer down — never per card, and never inside an open
    write transaction. A clock that can't be read falls back to the
    server's date: a wrong hour once a day beats a blank Cook tab, the
    same stance household_now itself takes towards an unreadable zone.
    """
    try:
        return household_now(now_utc).date()
    except Exception:
        logger.exception("Couldn't read the household's clock; falling back to the server's date")
        return date.today()


def cook_total_minutes(meal: dict | None) -> int | None:
    """
    How long this card's cook takes, the one number every clock uses: the
    recipe's prep + cook, or the longest side's minutes when that is more
    (twenty-five-minute potatoes beside a fifteen-minute stir-fry start
    before the stir-fry does). The server twin of shell.js's
    mealClockTotal — written here so Now's "Start by", the Tonight card's
    tiles and the on-the-table time after a real start all add up the same
    total the Meal step's stops are spread over. Until 2026-09-13 moves.py
    added prep + cook alone and the Meal step counted the side, so the two
    screens could name different starts for the same dinner. None when
    nothing says how long it takes.
    """
    if not meal:
        return None
    main = (meal.get("prep_time_minutes") or 0) + (meal.get("cook_time_minutes") or 0)
    longest = 0
    for side in meal.get("sides") or []:
        # A side with no steps has nothing on the clock — the Meal step's
        # own stops skip it (shell.js mealClockSides), so the total does
        # too, or Now's "Start by" and the Meal step's would disagree again.
        if not [s for s in (side.get("instructions") or []) if str(s).strip()]:
            continue
        try:
            mins = int(side.get("minutes")) if side.get("minutes") is not None else 0
        except (TypeError, ValueError):
            mins = 0
        longest = max(longest, mins)
    total = max(int(main or 0), longest)
    return total if total > 0 else None


def _weekday_word(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%A")
    except (TypeError, ValueError):
        return "that day"


def cook_started_dt(value: str | None) -> datetime | None:
    """cook_started_at back into a naive local datetime; None for NULL or
    anything unreadable (a reader must never fail over a stored time)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except ValueError:
        return None


def planned_start_for(meal: dict) -> datetime | None:
    """
    When the plan said to start this meal: its slot's hour (the household's
    dinner_window for dinner, moves.py's defaults otherwise) minus
    cook_total_minutes — the exact arithmetic behind Now's "Start by". Read
    from moves.py rather than copied, so the number the toast compares
    against is the number the card showed. None when the card carries no
    date or no minutes; a component-based card's date is a placeholder,
    and a start with no length has no plan to be late against.
    """
    from . import moves as _moves  # lazy: moves imports this module

    total = cook_total_minutes(meal)
    if not total or not meal.get("date"):
        return None
    try:
        day = date.fromisoformat(meal["date"])
    except ValueError:
        return None
    at = _moves._slot_dt(day, meal.get("slot") or "dinner", _moves._dinner_clock())
    return at - timedelta(minutes=total)


def _cook_started_map(entry_ids: list[int]) -> dict[int, str]:
    """entry_id -> cook_started_at, for the entries that have one."""
    ids = [i for i in entry_ids if i is not None]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    conn = get_conn()
    rows = conn.execute(
        f"SELECT id, cook_started_at FROM meal_plan_entries "
        f"WHERE household_id = ? AND cook_started_at IS NOT NULL AND id IN ({placeholders})",
        (household_id(), *ids),
    ).fetchall()
    conn.close()
    return {r["id"]: r["cook_started_at"] for r in rows}


def start_cooking(entry_id: int, now_utc: datetime | None = None) -> dict:
    """
    "Start cooking" was tapped: write the real start on this entry and hand
    back the refreshed cooker view (the same shape every /api/cooker/*
    write returns) with the receipt on top — `started_at` (household
    local, "2026-09-13T18:02:00"), `on_the_table` (started_at plus
    cook_total_minutes, or None when the card has no minutes),
    `planned_start` (what the plan said, or None) and `already_started`.

    Idempotent, first tap wins: the UPDATE is COALESCE(cook_started_at, ?)
    so a second tap — or two panels tapping at once, which the sync route
    lets Starlette run in two threads — keeps the first time and answers
    already_started=True, which is how the shell knows to say nothing the
    second time. Household-scoped like every other write: an entry that
    belongs to somebody else is "no such meal", not a start.

    The receipt is computed off the refreshed VIEW's card rather than the
    row, because the total the clock uses lives on the card (recipe
    minutes plus the sides the plate pass attached) — see
    cook_total_minutes. A component batch's card stands for several
    entries; the tap lands on the one entry_id it was given, and the view
    then carries the batch's earliest start.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, date, cook_started_at, recipe_id, freeform_meal FROM meal_plan_entries "
        "WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No meal plan entry with id {entry_id}.")
    # Only a cook that is happening now has a real start. Cook mode opens
    # any night from the shelf (reading tomorrow's recipe tonight is
    # normal); a start recorded on it would sit there for days, its
    # Tonight card saying "started two hours late" tomorrow (found by the
    # branch's verifier, 2026-09-13). A day that isn't today is refused
    # with a plain sentence and nothing written; a reheat night has no
    # cook in it to start.
    today = household_now(now_utc).date().isoformat()
    if row["date"] != today:
        conn.close()
        when = _weekday_word(row["date"])
        return {"status": "refused", "entry_id": entry_id,
                "message": f"That’s {when}’s — I’ll note the start when you cook it {when}."}
    if row["recipe_id"] is None and (row["freeform_meal"] or "").strip().lower().startswith("leftover"):
        conn.close()
        return {"status": "refused", "entry_id": entry_id, "message": "Nothing to start on a reheat night."}
    already_started = row["cook_started_at"] is not None
    if not already_started:
        conn.execute(
            "UPDATE meal_plan_entries SET cook_started_at = COALESCE(cook_started_at, ?) "
            "WHERE id = ? AND household_id = ?",
            (household_now(now_utc).isoformat(timespec="seconds"), entry_id, household_id()),
        )
        conn.commit()
        # Read it back rather than trusting the value just sent: if another
        # tap landed first, COALESCE kept theirs and that is the real one.
        started = conn.execute(
            "SELECT cook_started_at FROM meal_plan_entries WHERE id = ?", (entry_id,)
        ).fetchone()["cook_started_at"]
    else:
        started = row["cook_started_at"]
    conn.close()

    view = get_cooker_view()
    card = None
    for meal in view.get("meals") or []:
        if entry_id in (meal.get("entry_ids") or [meal.get("entry_id")]):
            card = meal
            break
    total = cook_total_minutes(card)
    started_dt = cook_started_dt(started)
    on_the_table = (
        (started_dt + timedelta(minutes=total)).isoformat(timespec="seconds")
        if started_dt and total else None
    )
    planned = planned_start_for(card) if card else None
    return {
        **view,
        "entry_id": entry_id,
        "started_at": started,
        "on_the_table": on_the_table,
        "planned_start": planned.isoformat(timespec="seconds") if planned else None,
        "already_started": already_started,
    }


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
    # This plan's own rows, plus any row another plan dated INTO this
    # plan's period. A holiday's big meal (app/tools/big_meal.py) spreads
    # its make-ahead work over the days before, and for a Monday holiday
    # those days belong to the week before — a row dated there but written
    # by the holiday's own plan has to show up where Now and the Cook
    # screen actually look, which is the plan covering today — and the
    # holiday's plan still sees its own dinner's rows, wherever dated.
    plan_row = conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (weekly_plan_id,)).fetchone()
    first = last = None
    if plan_row is not None:
        from . import week_intake as _week_intake
        start, days = _weekly_plan.plan_period(plan_row)
        period = _week_intake.period_dates(start, days) if days > 0 else []
        if period:
            first, last = period[0], period[-1]
    # Two guards on the wider read. A row dated into this period by ANOTHER
    # plan counts only while that plan is live (draft or approved) — a
    # retired plan's "chop onions" for a Chili that is no longer planned
    # must not surface in the plan that replaced it. And a row whose entry
    # is gone (a hand swap deleted the dinner; the row survived because the
    # column carries no foreign key) is a reminder for a meal that no
    # longer exists, so it is dropped on read — the one place that covers
    # every delete path, past and future, rather than a fix per path.
    rows = conn.execute(
        "SELECT t.id, t.task_date, t.description, t.related_meal, t.status, t.task_type, "
        "t.inventory_item_id, t.meal_plan_entry_id, t.quantity FROM prep_tasks t "
        "WHERE t.household_id = ? "
        "AND (t.weekly_plan_id = ? "
        "     OR (? IS NOT NULL AND t.task_date >= ? AND t.task_date <= ? "
        "         AND t.weekly_plan_id IN (SELECT id FROM weekly_plans WHERE household_id = t.household_id AND status IN ('draft', 'approved'))) "
        "     OR t.meal_plan_entry_id IN (SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ?)) "
        "AND (t.meal_plan_entry_id IS NULL OR EXISTS (SELECT 1 FROM meal_plan_entries e WHERE e.id = t.meal_plan_entry_id)) "
        "ORDER BY t.task_date ASC, t.id ASC",
        (household_id(), weekly_plan_id, first, first, last, weekly_plan_id),
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
    card["ingredients"] = scaled["scaled_ingredients"] + _side_ingredients_for(card, batch_servings)
    card["default_servings"] = batch_servings
    card["servings"] = batch_servings
    return True


def _side_ingredients_for(card: dict, servings: int | None) -> list[dict]:
    """
    The card's sides' ingredients, scaled to `servings` where a side says
    what it was written for (a side the household added from the meal
    screen — plates.ADDITION_SERVINGS) and as written otherwise. Every
    rewrite of a card's ingredient list goes through this so the side is
    never dropped: until 2026-09-13 scale_recipe's list REPLACED the
    folded one, and a plain night with attendance on record lost its
    side's ingredients from the card (the steps stayed, so "Alongside —
    Green salad" had no romaine above it).
    """
    out = []
    for side in card.get("sides") or []:
        for ing in _plates.scale_side_ingredients(side, servings):
            # Which side the row came from, so the meal screen's "What's in
            # it" can say "added" beside the potatoes the household put
            # there (added_by "household"); the app's own plate sides carry
            # no such mark and read as part of the dish.
            ing["from_side"] = side.get("name") or ""
            if side.get("added_by") == "household":
                ing["added"] = True
            out.append(ing)
    return out


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
    current plan — except when the plan get_weekly_plan/
    _current_weekly_plan_row would fall back to has ALREADY ENDED (its
    last day is before today): a plan generated ahead of time and not yet
    started still falls back normally (cook mode legitimately opens next
    week's draft when nothing covers today), but a plan whose entire
    period is behind us gets the empty "no plan" view instead, with
    `last_planned_label` naming that last week so a screen can still say
    when it was. Passing weekly_plan_id explicitly always returns that
    exact plan, stale or not.

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

    # _current_weekly_plan_row's own fallback (its docstring: "Falls back
    # to the most-recently-created plan when none covers today") is right
    # for a chat answer to "what's the plan" — the household's real last
    # week beats nothing. It is wrong here IN ONE OF ITS TWO CASES: a call
    # with no weekly_plan_id means "what am I cooking right now", and
    # handing back August's dinners under a heading that says "this week"
    # (Loop Board, a household whose last approved week was weeks ago) is
    # worse than honestly saying nothing is planned. Caught 2026-09-13.
    #
    # The OTHER case the same fallback covers is not this bug and must not
    # be touched: a plan generated ahead of time for NEXT week, on a day
    # nothing has started yet. test_is_current_plan_is_the_same_query_not_a_date_rule
    # and TestAPlanThatDoesNotCoverToday (test_needs_you_dinner_visible.py)
    # pin that a retired "this week" correctly falls back to next week's
    # draft, and that a future plan's own meals still show alongside a
    # loose one-off for today — cook mode opening "next week's draft" when
    # today's own week has nothing left is a real, wanted answer. So the
    # test here is specifically "has this plan's last day already gone
    # by", not "does it cover today" — a period that hasn't started yet
    # fails the second and passes the first, and must fall through
    # unchanged.
    #
    # Only fires when the CALLER left weekly_plan_id out — an explicit
    # ask for that plan (the Plan tab opening a specific week's meal, a
    # draft nobody's approved yet) still gets exactly what it asked for,
    # stale or not. Reduce to the same shape get_weekly_plan hands back
    # for "no plan exists" at all, so every pass below (loose meals, the
    # empty-view return, the shell reshape, is_current_plan) already
    # knows how to treat it — this is "no current plan", not a new case.
    #
    # "Already gone by" is asked of the HOUSEHOLD's clock, not the
    # container's (see household_today). Read the server's, and on the last
    # day of a period a Toronto household lost its whole week from 8pm —
    # empty Cook tab, empty Now, "Last planned: Sep 7-13" — for the four
    # hours of the evening it was most likely to be cooking from it.
    # Resolved ONCE, here, for the whole view — it costs a connection.
    last_planned_label = None
    if weekly_plan_id is None and plan.get("weekly_plan_id") is not None:
        period_end = plan.get("period_end_date")
        today = household_today().isoformat()
        if period_end and period_end < today:
            last_planned_label = plan.get("period_label")
            plan = {"weekly_plan_id": None, "meals": []}

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
    # component or scale it to a batch nobody planned.
    #
    # KNOWN RESIDUE, narrower than it was: the stale-plan fix above (see its
    # own comment, "period has ALREADY ENDED") already reduces an ENDED
    # component plan to plain "no plan" before this line ever runs, so that
    # sub-case now gets its loose meal like any day-based household does
    # (test_a_component_household_no_longer_has_this_bug, inverted
    # 2026-09-13). What's left: a component household whose current plan
    # HASN'T STARTED YET (a future plan, still "current" by the fallback,
    # so the stale-plan fix correctly leaves it alone) still has the
    # original bug for today's loose meal. Not fixed here for the same
    # reason as before — it needs the component branch itself to learn to
    # carry a dated row, not another carve-out in front of it.
    loose_meals = (
        _weekly_plan.unplanned_meals_ahead(plan)
        if weekly_plan_id is None and plan.get("planning_mode") != "component_based"
        else []
    )

    if plan.get("weekly_plan_id") is None and not loose_meals:
        return {"weekly_plan_id": None, "is_current_plan": False, "meals": [], "prep_tasks": [], "prep_sessions": [], "prep_days_set": False, "meals_done": 0, "meals_total": 0, "prep_done": 0, "prep_total": 0, "all_away": False,
                "period_start_date": None, "day_count": 0, "cook_name": _cook_name(), "last_planned_label": last_planned_label}

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

    # Whether this is the plan the Cook tab itself shows — the one a call
    # with NO weekly_plan_id resolves to (_current_weekly_plan_row: the
    # plan whose period contains today, else the newest that hasn't
    # expired). The Plan tab asks for a specific plan's view so a meal on
    # next week's draft can show its recipe (2026-09-13, "tapping a meal
    # sometimes lands on the full cook list"), and this is how it knows
    # whether cook mode — which only ever holds the no-id view — can open
    # that meal, or whether offering "Cook this" would land the household
    # on Cook's root looking at a different week. Read from the same query
    # rather than inferred from dates: on a Sunday with no plan covering
    # today the fallback IS next week's draft, so "its period has started"
    # would be the wrong test.
    if weekly_plan_id is None or plan_id is None:
        is_current_plan = plan_id is not None
    else:
        conn = get_conn()
        current = _weekly_plan._current_weekly_plan_row(conn)
        conn.close()
        is_current_plan = bool(current) and current["id"] == plan_id

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
    # The real starts, read here rather than added to get_weekly_plan's own
    # rows: that list is the chat's view of the plan (15 call sites), and a
    # start time is a cook-screen fact, not a planning one.
    started_by_entry = _cook_started_map([m["entry_id"] for m in plan["meals"] + loose_meals])
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
            "ingredients": (recipe["ingredients"] if recipe else []) + _side_ingredients_for({"sides": sides}, None),
            "instructions": (recipe["instructions"] if recipe else []) + _side_steps(sides),
            "sides": sides,
            "sides_label": _plates.sides_label(sides),
            "default_servings": recipe["default_servings"] if recipe else None,
            "prep_time_minutes": recipe["prep_time_minutes"] if recipe else None,
            "cook_time_minutes": recipe["cook_time_minutes"] if recipe else None,
            "advance_prep_notes": recipe["advance_prep_notes"] if recipe else "",
            "advance_prep_step_indices": recipe["advance_prep_step_indices"] if recipe else [],
            "has_full_recipe": recipe is not None,
            # True while the menu pass's dish waits for the recipe pass (see
            # recipes.fill_recipe_details): a real recipe with nothing in it
            # yet, which the plan's copy of this screen says plainly and cook
            # mode's "Fill in this recipe" writes on the spot.
            "details_pending": bool(recipe.get("details_pending")) if recipe else False,
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
            # When "Start cooking" was really tapped (household local ISO,
            # see schema.sql), or None — the one fact every clock reader
            # checks before falling back to the plan's arithmetic
            # (2026-09-13, "the real start time moves the clock").
            "cook_started_at": started_by_entry.get(m["entry_id"]),
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
                # One batch, one start: the earliest any sibling was begun.
                starts = [s for s in (g.get("cook_started_at"), m.get("cook_started_at")) if s]
                g["cook_started_at"] = min(starts) if starts else None

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
            scalable = bool(m.get("has_full_recipe") and m.get("default_servings"))
            if not scalable and not m.get("sides"):
                continue
            eaters = _leftovers.eaters_at(m["date"], m["slot"])
            if not eaters:
                continue
            if scalable:
                scaled = _recipes.scale_recipe(m["meal"], eaters)
                m["ingredients"] = scaled["scaled_ingredients"] + _side_ingredients_for(m, eaters)
                m["default_servings"] = eaters
            elif m.get("sides"):
                # No recipe to scale, but the side the household added is
                # still for tonight's table, not the four it was written for.
                recipe = recipes_by_name.get((m["meal"] or "").lower())
                m["ingredients"] = (recipe["ingredients"] if recipe else []) + _side_ingredients_for(m, eaters)

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

    # The component batches the household said yes to at approval
    # (batch_components.py): the cook day's card says the eggs are for the
    # later dishes too, and each later dish reads that they are already
    # done — on its card and on the eggs' own ingredient row. After the
    # chains (a reheat night is never told its eggs are made ahead twice
    # over) and after every ingredient rewrite above, since those build
    # fresh dicts and the row note has to land on the ones the screen gets.
    if plan_id is not None:
        _batch_components.attach_batch_components(plan_id, meals)
    else:
        for card in meals:
            card["batch_components"] = []
            card["components_made_ahead"] = []

    # "I'll use something else instead", said while sorting the list
    # (grocery.substitute_grocery_item): the recipe keeps asking for fresh
    # oregano, and the card says what is actually going in. Matched on the
    # list's own merge key, so "Fresh oregano" and "fresh oregano, chopped"
    # both hear about it.
    # Already at home, by the one rule the grocery ingest itself uses to
    # skip buying a thing (recipes._add_recipe_ingredients_for_entries):
    # an inventory row with a quantity on it. Read-only, and nothing new to
    # keep up — inventory is deferred as policy, so this is a courtesy mark
    # on the meal screen's "What's in it", never a thing to fill in.
    inv_conn = get_conn()
    at_home = {
        (row["item"] or "").strip().lower()
        for row in inv_conn.execute(
            "SELECT item FROM inventory_items WHERE household_id = ? AND TRIM(quantity) != ''",
            (household_id(),),
        ).fetchall()
    }
    inv_conn.close()
    if at_home:
        for m in meals:
            for ing in m["ingredients"]:
                if isinstance(ing, dict) and (ing.get("item") or "").strip().lower() in at_home:
                    ing["at_home"] = True

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
        "is_current_plan": is_current_plan,
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
        # Set only when the fallback above discarded a stale plan for
        # THIS call — a household's last approved week, named the way
        # the design writes a range ("Aug 18–24"), so Cook can say
        # honestly what it isn't showing instead of just going quiet.
        # None on every ordinary call, including one that named its own
        # weekly_plan_id.
        "last_planned_label": last_planned_label,
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
