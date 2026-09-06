"""
The defrost flow — Loop Board "First-class 'defrost' prep step: say exactly
what to take out and when."

Before this module, defrost existed only as the word "thawing" inside the
LLM's freeform prep-schedule prompt (see agent.generate_prep_schedule_llm),
which never fired in practice: it only creates a task when a recipe's
advance_prep_notes calls for one, and every recipe sampled had that field
blank. And even when it did fire, generate_prep_schedule never saw the
household's freezer at all — the prep scheduler could only reason about
recipe text, not about what's actually frozen.

This module is deliberately NOT another LLM prompt. Whether something needs
to move from the freezer to the fridge is a fact about inventory (is it
tracked with location='freezer'?) crossed with a fact about the week's plan
(does a meal actually use it?) and a small, honest rule of thumb about how
long a cut of that size takes to thaw — none of that benefits from a model
guessing, and Emily's ask was explicitly for documented defaults, not
invented precision. So this is plain arithmetic and a lookup table, run for
free every time a plan is generated (see agent.py's
_sync_defrost_tasks_if_needed) — no API cost, unlike the general prep
schedule's model call.

Two things produce a defrost task, both landing in the same prep_tasks rows
(task_type='defrost'):
  1. A plan's own meals, matched against freezer inventory by ingredient
     name (see sync_defrost_tasks / _candidates_from_plan).
  2. A confirmed ready_made recommendation (slot_needs.recommended_defrost_item)
     — see defrost_task_from_ready_made, called from
     slot_needs.confirm_slot_recommendation once (and only once) the
     household has said yes. Emily's rule there ("the system recommends,
     the household confirms") is honored by never creating this task until
     recommendation_confirmed=1 — this module just reads that flag rather
     than duplicating the confirm gate.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta

from ..db import get_conn
from ._shared import household_id
from . import attendance as _attendance
from . import inventory as _inventory
from . import leftovers as _leftovers
from . import recipes as _recipes
from . import rhythm as _rhythm
from . import weekly_plan as _weekly_plan
from .cooker import _find_inventory_match

# ---------- Lead-time rule of thumb (Emily's ask: honest defaults, no
# invented precision) ----------
#
# Real thaw time depends on the exact cut, thickness, and fridge
# temperature — nothing here claims to model that precisely. This is a
# small, documented, three-tier table matched by keyword against the
# inventory item's own name, coarser than a real cookbook but transparent
# about what it is: a rule of thumb, not a simulation.
#
# "Standard cuts" (24h) is the default for anything that doesn't match a
# more specific keyword — chicken breasts/thighs, pork chops, steaks,
# ground meat, most fish not caught by the "small/thin" list below.
STANDARD_LEAD_HOURS = 24.0

# Large roasts / whole birds: the USDA rule of thumb is roughly 24h per
# 4-5 lbs in the fridge, which for a typical whole chicken/small roast
# rounds to "about two days" — 48h is the honest single number for that
# tier, not a weight-scaled formula (this app doesn't track item weight).
LARGE_LEAD_HOURS = 48.0
# No bare "turkey": "whole turkey" already covers the legitimate whole-bird
# case, and a bare "turkey" keyword was matching straight through common
# compound items that are anything but large — "Ground Turkey" and "Turkey
# Bacon" both got tagged 48h before this was caught in review. Same reasoning
# kept "ham" off this list entirely (it's genuinely ambiguous — a whole
# holiday ham vs. diced deli ham — and "Hamburger" doesn't even mean ham; see
# _matches_keyword's word-boundary matching below for why that one part was
# already a plain bug, not just an ambiguous call).
_LARGE_KEYWORDS = (
    "whole chicken", "whole turkey", "roast", "brisket",
    "prime rib", "leg of lamb", "pork shoulder", "pork butt", "whole duck",
    "whole ham", "rack of",
)

# Small/thin cuts thaw faster — commonly cited as 12-24h. 18h is the
# midpoint of that range, used as a single number only because scheduling
# needs one; the comment is the honesty, not the number itself.
SMALL_THIN_LEAD_HOURS = 18.0
_SMALL_THIN_KEYWORDS = (
    "shrimp", "prawn", "fillet", "filet", "tilapia", "cutlet", "thin-cut",
    "thin cut", "scallop", "bacon",
)


def _matches_keyword(name: str, keyword: str) -> bool:
    """
    Whole-word/whole-phrase match (allowing a plain trailing 's', since
    inventory items are commonly named in the plural — "Chicken Thighs",
    "Salmon Fillets"), not bare substring containment. A plain `keyword in
    name` check was the actual bug caught in review: "ham" is a substring
    of "hamburger" (a single word, not "ham" + "burger"), and "roast" is a
    substring of "roasted" ("Roasted Vegetables") — both matched and
    wrongly tagged an ordinary item as a 48h large roast. \\b anchors the
    keyword to real word edges so it only matches the words it's meant to;
    the first fix (plain \\b, no plural allowance) over-corrected and
    stopped matching "Salmon Fillets"/"Beef Roasts" at all, caught in the
    same review — `s?` restores the plural without reopening the
    substring hole ("roasts?" still doesn't match inside "roasted", since
    the literal character after "roast" there is "e", not "s").
    Doesn't handle a plural that changes an earlier word ("legs of lamb"),
    an acceptable gap for a documented rule-of-thumb table.
    """
    return re.search(r"\b" + re.escape(keyword) + r"s?\b", name) is not None

# Approximate clock time dinner actually lands, per the household's own
# dinner_window rhythm fact (app/tools/rhythm.py) — used to place a defrost
# move on the calendar day it actually needs to happen, not just count
# whole days. 'all_over' and an unset dinner_window have no reliable target
# time at all (see get_household_rhythm's docstring and the matching
# guidance in agent.generate_prep_schedule_llm) — deliberately absent from
# this map rather than guessed at, handled instead by _move_date's
# whole-day fallback below.
_DINNER_CLOCK_BY_WINDOW = {
    "5_6ish": time(17, 30),
    "6_8": time(19, 0),
    "later": time(20, 0),
}


def lead_hours_for_item(item_name: str) -> tuple[float, str]:
    """
    The rule-of-thumb defrost lead time for a freezer item, by name —
    (hours, tier) where tier is 'large' | 'standard' | 'small_thin', purely
    so callers/tests can explain which bucket produced the number. Keyword
    match is case-insensitive and whole-word (see _matches_keyword — NOT
    bare substring containment, which used to false-positive on "Hamburger"
    and "Roasted Vegetables"), most-specific tiers (large, then small/thin)
    checked first.
    """
    name = (item_name or "").strip().lower()
    for kw in _LARGE_KEYWORDS:
        if _matches_keyword(name, kw):
            return LARGE_LEAD_HOURS, "large"
    for kw in _SMALL_THIN_KEYWORDS:
        if _matches_keyword(name, kw):
            return SMALL_THIN_LEAD_HOURS, "small_thin"
    return STANDARD_LEAD_HOURS, "standard"


def _move_date(cook_date_str: str, lead_hours: float, dinner_window: str | None) -> str:
    """
    Work backward from the cook day to the calendar day the item should
    come out of the freezer, honoring the household's dinner_window rhythm
    fact when it's known (see _DINNER_CLOCK_BY_WINDOW).

    With a real target time, this is straightforward clock arithmetic: dinner
    time minus the lead time, then take that moment's own date. Without one
    ('all_over', or dinner_window never answered), there's no honest clock
    to subtract from, so this falls back to whole-day counting instead —
    round the lead time up to full days and step back that many calendar
    days. Rounding up (not down) means the fallback never recommends less
    lead time than the table calls for.

    Never returns the cook day itself, even when the clock arithmetic would
    technically allow it (a short lead against a late dinner_window, e.g.
    18h against a 7pm dinner lands at 1am the SAME calendar day) — caught
    in independent review: the Today tile frames this as "defrost tonight",
    and "tonight" for a meal happening that same evening is nonsensical (the
    move would need to happen that morning, and by the time anyone reads a
    tile that says "tonight" it may already be too late). The ticket's own
    framing is consistently "the night before, sometimes two" — same-day
    was never part of the intended shape — so this floors at one full
    calendar day of buffer, always.
    """
    cook_date = date.fromisoformat(cook_date_str)
    clock = _DINNER_CLOCK_BY_WINDOW.get(dinner_window or "")
    if clock is None:
        days_before = math.ceil(lead_hours / 24.0)
        result = cook_date - timedelta(days=max(days_before, 0))
    else:
        cook_dt = datetime.combine(cook_date, clock)
        move_dt = cook_dt - timedelta(hours=lead_hours)
        result = move_dt.date()
    return min(result, cook_date - timedelta(days=1)).isoformat()


def _weekday_name(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A")


def _describe(item: str, meal: str, meal_date: str) -> str:
    """"Move the chicken thighs to the fridge — for Thursday's skewers." Matches the voice guide: state the fact, name the day the way a person would."""
    return f"Move the {item} to the fridge — for {_weekday_name(meal_date)}'s {meal}."


def _batch_quantity(ing: dict, batch_factor: float) -> str:
    """
    How much of this ingredient to actually move to the fridge. 1.0 for an
    ordinary meal, which leaves the recipe's own wording untouched, byte
    for byte; more than that for a night cooking for its own table plus a
    later night's leftovers. Rounded the way an amount you handle is
    written rather than the way the division lands, via
    attendance.scale_ingredients.

    That helper is now this module's alone: the shopping list used to go
    through it too, but the grocery ingest holds its amounts unrounded
    until the whole week is in and rounds once per line instead (see
    recipes.WeekGroceryBuffer). Nothing to unify — a defrost is one
    person taking one thing out of one freezer, so per-item rounding is
    exactly right here.
    """
    qty = (ing.get("qty") or "").strip()
    if batch_factor == 1.0 or not qty:
        return qty
    return (_attendance.scale_ingredients([{**ing, "qty": qty}], batch_factor)[0].get("qty") or "").strip()


def _candidates_from_plan(plan: dict, freezer_items: list[dict], dinner_window: str | None) -> list[dict]:
    """
    Walk a plan's meals, cross-referencing each recipe's ingredient list
    against tracked freezer inventory. Only a *confident* name match (see
    cooker._find_inventory_match — exact, allowing a trailing-'s' plural)
    produces a candidate, the same bar deplete_inventory_for_meal uses
    before touching inventory automatically; a loose/ambiguous match isn't
    safe to act on silently and is left alone here (nothing in this ticket
    queues it for review the way depletion does — a missed vague match
    just means no reminder, not a wrong one, which is the safer failure
    direction for something time-sensitive).

    One candidate per (meal, ingredient) pair — if the same freezer item
    is needed by two different meals this week, each gets its own task
    with its own move date and its own quantity, rather than merged into
    one. Documented as a v1 simplification (see the ticket write-up) —
    consolidating same-day/same-item defrosts is a reasonable follow-up.

    A leftovers night is skipped outright and its cook night's quantity is
    scaled up instead (Emily, 2026-09-04). Nothing is cooked on a reheat
    night, so "move the beef to the fridge for Thursday" was a reminder
    for a thing that never happens — and the Tuesday it really belongs to
    has to thaw enough beef for both nights, not one.
    """
    if not freezer_items:
        return []
    recipes_by_name = {r["name"]: r for r in _recipes.list_recipes()}
    chains = _leftovers.plan_leftover_chains(plan["weekly_plan_id"]) if plan.get("weekly_plan_id") else {"sources": {}, "leftovers": {}}
    candidates = []
    for m in plan.get("meals") or []:
        recipe = recipes_by_name.get(m.get("meal"))
        if not recipe:
            continue
        entry_id = m.get("entry_id")
        if entry_id in chains["leftovers"]:
            continue
        batch_factor = 1.0
        source = chains["sources"].get(entry_id)
        if source:
            batch = _leftovers.batch_for_source(source)
            if batch["servings"] > 0 and batch["cook_eaters"] > 0:
                batch_factor = batch["servings"] / batch["cook_eaters"]
        for ing in recipe.get("ingredients") or []:
            ing_name = (ing.get("item") or "").strip()
            if not ing_name:
                continue
            match, confident = _find_inventory_match(ing_name, freezer_items)
            if not match or not confident:
                continue
            lead_hours, tier = lead_hours_for_item(match["item"])
            move_date = _move_date(m["date"], lead_hours, dinner_window)
            candidates.append({
                "inventory_item_id": match["id"],
                "meal_plan_entry_id": m.get("entry_id"),
                "task_date": move_date,
                "description": _describe(match["item"], m["meal"], m["date"]),
                "related_meal": m["meal"],
                "quantity": _batch_quantity(ing, batch_factor),
                "lead_hours": lead_hours,
                "lead_tier": tier,
            })
    return candidates


def defrost_candidates_for_plan(weekly_plan_id: int) -> list[dict]:
    """
    What needs defrosting for this plan, without writing anything —
    exposed separately from sync_defrost_tasks so callers (tests, a future
    "preview before it's saved" UI) can inspect the derivation on its own.
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return []
    freezer_items = [i for i in _inventory.get_inventory() if i.get("location") == "freezer"]
    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    return _candidates_from_plan(plan, freezer_items, dinner_window)


def sync_defrost_tasks(weekly_plan_id: int) -> dict:
    """
    Recompute and persist this plan's meal-derived defrost tasks
    (task_type='defrost' rows with meal_plan_entry_id set) — safe to call
    as often as needed (plan generation, a manual prep-schedule regenerate,
    a swapped meal): it only ever touches its own task_type, never the
    LLM-generated 'general' rows (see save_prep_tasks, which is scoped the
    same way in the other direction).

    Deliberately scoped to `meal_plan_entry_id IS NOT NULL` — a ready_made
    recommendation's defrost task (meal_plan_entry_id IS NULL, see
    defrost_task_from_ready_made) is never one of this function's own
    candidates, since candidates only ever come from the plan's own meals.
    An earlier version swept those in as "stale" and deleted a
    just-confirmed reminder the very next time a plan synced — caught in
    independent review and reproduced against real code before this fix:
    confirming a ready_made defrost, then calling this function, made the
    task vanish. Each producer now only ever touches the rows it created;
    see defrost_task_from_ready_made's own docstring for its side.

    Existing rows are matched to fresh candidates by
    (inventory_item_id, meal_plan_entry_id, task_date) and left with their
    current status untouched — regenerating a schedule must not silently
    un-defrost something the household already marked done or skipped just
    because the plan was re-saved. A candidate with no existing match is
    inserted as pending; an existing row with no matching candidate any
    more (the meal was swapped away, the item left the freezer) is deleted
    — it would otherwise linger as a reminder for something no longer true.

    Also scoped to `inventory_item_id IS NOT NULL` — every candidate this
    function ever produces has one (see _candidates_from_plan: a candidate
    only exists once a confident freezer match is found), so this is
    free for this function's own rows, but it matters for a THIRD kind of
    row sharing the same task_type/meal_plan_entry_id-not-null shape:
    confirm_frozen_items's household-confirmed tasks, which never have an
    inventory row to point at (inventory is deferred policy — see that
    function's own docstring) and set meal_plan_entry_id instead, the same
    field this function keys its own candidates by. Without this filter
    those rows read as "existing" here, never match one of THIS function's
    freezer-derived candidate keys, and get swept away as stale the next
    time a plan resyncs — the exact bug independent review already caught
    once for the ready_made path (see this function's own history), just
    on the other column.
    """
    conn = get_conn()
    candidates = defrost_candidates_for_plan(weekly_plan_id)
    existing = conn.execute(
        "SELECT id, inventory_item_id, meal_plan_entry_id, task_date FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND household_id = ? AND task_type = 'defrost' "
        "AND meal_plan_entry_id IS NOT NULL AND inventory_item_id IS NOT NULL",
        (weekly_plan_id, household_id()),
    ).fetchall()
    existing_by_key = {
        (r["inventory_item_id"], r["meal_plan_entry_id"], r["task_date"]): r["id"] for r in existing
    }

    kept_ids = set()
    inserted, updated = 0, 0
    for c in candidates:
        key = (c["inventory_item_id"], c["meal_plan_entry_id"], c["task_date"])
        existing_id = existing_by_key.get(key)
        if existing_id:
            conn.execute(
                "UPDATE prep_tasks SET description = ?, related_meal = ?, quantity = ? WHERE id = ?",
                (c["description"], c["related_meal"], c["quantity"], existing_id),
            )
            kept_ids.add(existing_id)
            updated += 1
        else:
            cur = conn.execute(
                "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
                "related_meal, status, task_type, inventory_item_id, meal_plan_entry_id, quantity) "
                "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', ?, ?, ?)",
                (household_id(), weekly_plan_id, c["task_date"], c["description"], c["related_meal"],
                 c["inventory_item_id"], c["meal_plan_entry_id"], c["quantity"]),
            )
            # Recorded immediately (not just added to kept_ids) so two
            # identical candidates within the same call — the same
            # ingredient named twice on one recipe, say — update the row
            # just inserted instead of inserting a second duplicate.
            existing_by_key[key] = cur.lastrowid
            kept_ids.add(cur.lastrowid)
            inserted += 1

    stale_ids = [r["id"] for r in existing if r["id"] not in kept_ids]
    if stale_ids:
        conn.executemany("DELETE FROM prep_tasks WHERE id = ?", [(i,) for i in stale_ids])
    conn.commit()
    conn.close()
    return {"weekly_plan_id": weekly_plan_id, "inserted": inserted, "updated": updated, "removed": len(stale_ids)}


def defrost_task_from_ready_made(date_str: str, slot: str) -> dict | None:
    """
    Wire a confirmed ready_made defrost recommendation (slot_needs.py) into
    the same defrost prep-task machinery a normal meal's ingredient uses —
    called from slot_needs.confirm_slot_recommendation, never on its own,
    so this always runs after (and only after) the household has said yes.

    recommended_defrost_item is stored as a plain item-name string, not an
    inventory row id (see slot_needs.py's schema comment) — matched back to
    a live freezer row by exact case-insensitive name here. No match (the
    item was used up or renamed since the recommendation was computed)
    means nothing to schedule; that's reported rather than raised, since a
    stale recommendation racing real life is expected, not an error.

    Confirmed=false, or no recommendation at all, means no task should
    exist — any previously-created task for this slot is removed rather
    than left to linger as a reminder for something no longer planned.
    """
    from . import slot_needs as _slot_needs  # local import: slot_needs imports this module

    need = _slot_needs.get_slot_need(date_str, slot)
    conn = get_conn()
    plan_row = _weekly_plan._current_weekly_plan_row(conn)
    weekly_plan_id = plan_row["id"] if plan_row else None

    # related_meal is the dedup key here (there's no meal_plan_entry_id for
    # a ready_made slot to key off, the way a normal meal's defrost task
    # does) — it has to be the slot's own event date, NOT task_date: the
    # move date is computed earlier than the meal on purpose, so matching
    # on task_date would look for a row at the wrong date and never find
    # the one just created (this was a real bug caught by
    # test_declining_a_ready_made_recommendation_removes_any_created_task).
    related_meal = f"{slot} (ready-made) for {date_str}"
    existing = conn.execute(
        "SELECT id FROM prep_tasks WHERE household_id = ? AND task_type = 'defrost' "
        "AND meal_plan_entry_id IS NULL AND related_meal = ?",
        (household_id(), related_meal),
    ).fetchall()

    item_name = (need.get("recommended_defrost_item") or "").strip()
    if not need.get("recommendation_confirmed") or not item_name or weekly_plan_id is None:
        if existing:
            conn.executemany("DELETE FROM prep_tasks WHERE id = ?", [(r["id"],) for r in existing])
            conn.commit()
        conn.close()
        return None

    freezer_items = [i for i in _inventory.get_inventory() if i.get("location") == "freezer"]
    match = next((i for i in freezer_items if i["item"].strip().lower() == item_name.lower()), None)
    if not match:
        if existing:
            conn.executemany("DELETE FROM prep_tasks WHERE id = ?", [(r["id"],) for r in existing])
            conn.commit()
        conn.close()
        return None

    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    lead_hours, tier = lead_hours_for_item(match["item"])
    move_date = _move_date(date_str, lead_hours, dinner_window)
    description = f"Move the {match['item']} to the fridge — for {_weekday_name(date_str)}'s dinner."

    if existing:
        conn.execute(
            "UPDATE prep_tasks SET task_date = ?, description = ?, inventory_item_id = ? WHERE id = ?",
            (move_date, description, match["id"], existing[0]["id"]),
        )
        task_id = existing[0]["id"]
    else:
        cur = conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type, inventory_item_id, meal_plan_entry_id, quantity) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', ?, NULL, '')",
            (household_id(), weekly_plan_id, move_date, description, related_meal, match["id"]),
        )
        task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"prep_task_id": task_id, "task_date": move_date, "item": match["item"], "lead_hours": lead_hours, "lead_tier": tier}


def get_defrost_today() -> list[dict]:
    """
    Pending defrost tasks due today — powers the Today screen's defrost
    tile (design: DESIGN_SYSTEM.md's celadon-tint "nudge, not the task"
    tile). Deliberately not scoped to any one weekly_plan_id: a task can
    outlive the plan that produced it (e.g. next week's plan already
    exists), and "what needs to move today" should still surface.
    """
    today = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task_date, description, related_meal, quantity, inventory_item_id, "
        "meal_plan_entry_id, status FROM prep_tasks "
        "WHERE household_id = ? AND task_type = 'defrost' AND task_date = ? AND status = 'pending' "
        "ORDER BY id",
        (household_id(), today),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_defrost_schedule(days: int = 7) -> list[dict]:
    """
    Every pending defrost task due today or in the next `days` days —
    chat-parity answer for "what do I need to defrost?" / "what's coming
    up to defrost this week?". Ordered soonest-first.
    """
    today = date.today()
    end = (today + timedelta(days=max(days, 0))).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task_date, description, related_meal, quantity, status FROM prep_tasks "
        "WHERE household_id = ? AND task_type = 'defrost' AND status = 'pending' "
        "AND task_date >= ? AND task_date <= ? ORDER BY task_date ASC, id ASC",
        (household_id(), today.isoformat(), end),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Ask instead of infer (Loop Board "Defrost check: ask at
# approval") ----------
#
# Everything above this line only ever finds a defrost candidate by
# crossing the plan against TRACKED inventory — and inventory is deferred
# policy (2026-09-01 product decision): it stays lightweight background,
# gets no new investment, and no household should have to do inventory
# work to complete the core loop. Most households never track a freezer
# item there at all, which means most households never got a defrost
# reminder either, regardless of whether they actually had something
# frozen. Emily's ticket: ask directly, once, right after the week is
# approved — the same moment the "your list is ready" handoff appears —
# rather than silently doing nothing.
#
# This is deliberately NOT a new scheduling rule. It reuses the exact
# lead-time table and dinner-window-aware backward math every other
# defrost task in this module uses; the only new thing is where the fact
# "this is in the freezer" comes from (a tap, not an inventory row) — and,
# per Emily's explicit "optional bonus OFF", confirming it here is never
# written back to inventory. A household that taps "Chicken Thighs" has
# told this ONE plan about ONE freezer item, not made a standing inventory
# claim.

# Same alias set quantities.py's grocery-category normalization uses —
# recipe ingredients recorded before "meat/seafood" was standardized (or
# generated by an older prompt) may still say "meat" or "seafood" plain.
_MEAT_CATEGORY_ALIASES = frozenset({"meat/seafood", "meat", "seafood"})

TOO_LATE_TO_THAW_NOTE = (
    "Too late to thaw safely for tonight — a cold-water thaw takes about "
    "an hour per pound, or cook something else."
)


def _is_meat_ingredient(ing: dict) -> bool:
    return (ing.get("category") or "").strip().lower() in _MEAT_CATEGORY_ALIASES


def _matches_selected_item(ingredient_name: str, selected_lower: set[str]) -> bool:
    """
    Same plural tolerance as _matches_keyword's word-boundary matching, but
    for comparing two ingredient-ish names to each other rather than a name
    to a fixed keyword — the chip a household taps is the ingredient's own
    name (see meat_items_for_plan), so this is normally an exact match, but
    a recipe edited after the chip was shown ("Chicken Thighs" -> "Chicken
    Thigh") shouldn't silently stop matching.
    """
    name = (ingredient_name or "").strip().lower()
    if not name:
        return False
    if name in selected_lower:
        return True
    return (name.rstrip("s") in selected_lower) or (name + "s" in selected_lower)


def _iter_plan_meat_ingredients(weekly_plan_id: int):
    """
    Shared walk over a plan's own meals (never a ready_made recommendation
    — that path is defrost_task_from_ready_made's alone), yielding
    (meal_dict, recipe_ingredient, batch_factor) for every meat/seafood
    ingredient on a real cook night. A leftovers night is skipped outright,
    same reasoning _candidates_from_plan documents: nothing is cooked on a
    reheat night, and its cook night's batch_factor already scales the
    quantity to cover both.
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return
    recipes_by_name = {r["name"]: r for r in _recipes.list_recipes()}
    chains = _leftovers.plan_leftover_chains(weekly_plan_id) if weekly_plan_id else {"sources": {}, "leftovers": {}}
    for m in plan.get("meals") or []:
        recipe = recipes_by_name.get(m.get("meal"))
        if not recipe:
            continue
        entry_id = m.get("entry_id")
        if entry_id in chains["leftovers"]:
            continue
        batch_factor = 1.0
        source = chains["sources"].get(entry_id)
        if source:
            batch = _leftovers.batch_for_source(source)
            if batch["servings"] > 0 and batch["cook_eaters"] > 0:
                batch_factor = batch["servings"] / batch["cook_eaters"]
        for ing in recipe.get("ingredients") or []:
            if not _is_meat_ingredient(ing):
                continue
            ing_name = (ing.get("item") or "").strip()
            if not ing_name:
                continue
            yield m, ing, ing_name, batch_factor


def meat_items_for_plan(weekly_plan_id: int) -> list[dict]:
    """
    The distinct meat/seafood ingredients this plan's own meals actually
    call for, each with the night(s) it feeds — the ask card's own chip
    list, and the GET route behind it. Reads recipes directly; never looks
    at inventory, since the whole point is to work for a household that
    doesn't track any.

    A night only appears here if it's a real cook night (see
    _iter_plan_meat_ingredients) — the leftover-chain reheat night is left
    off "which night(s)" the same way it's left off the scheduled task
    itself: the batch that covers it is cooked, and defrosted for, on the
    source night alone.
    """
    by_item: dict[str, dict] = {}
    for m, _ing, ing_name, _batch_factor in _iter_plan_meat_ingredients(weekly_plan_id):
        key = ing_name.lower()
        entry = by_item.get(key)
        if entry is None:
            entry = {"item": ing_name, "nights": []}
            by_item[key] = entry
        night = {"date": m["date"], "meal": m["meal"], "weekday": _weekday_name(m["date"])}
        if night not in entry["nights"]:
            entry["nights"].append(night)
    return sorted(by_item.values(), key=lambda e: (e["item"].lower()))


def confirm_frozen_items(weekly_plan_id: int, items: list[str]) -> dict:
    """
    The household-confirmed twin of the inventory-matched candidates above
    — "yes, that one's in the freezer" for a plan that was never tracking
    it anywhere. `items` are plain ingredient names, exactly as shown by
    meat_items_for_plan (its own "item" values); anything that doesn't
    match one of THIS plan's own meat/seafood ingredients is silently
    ignored rather than erroring, since a stale chip (the plan changed
    after the ask card was drawn) is expected, not a bug report.

    One prep_tasks row per (item, cook night) — same v1-simplification and
    same leftovers handling as _candidates_from_plan (a reheat night is
    never its own task; its cook night's quantity already covers it).
    inventory_item_id is always NULL on what this creates — see the
    module-level note above: confirming a freezer item here is a fact
    about this one plan, never a write to inventory (Emily's "optional
    bonus" stayed off).

    A candidate whose move date has already passed is never inserted —
    only possible for a meal happening TODAY (_move_date always leaves at
    least one full calendar day between the move and the meal, so any
    earlier cook date's move date is still today-or-later). That item/meal
    gets a plain, calm note back instead of a task nobody could have
    actually acted on — in voice, per DESIGN_SYSTEM.md's "calm and
    reassuring, never cheery" rule for anything that names a problem: the
    fact, plus its way out, in the same breath.

    Returns {"created": [...], "notes": [...]} — never raises for "nothing
    matched" or "nothing to do"; both are ordinary answers here (see
    get_defrost_today's docstring for why this module treats an empty
    result as data, not an error).
    """
    selected_lower = {(i or "").strip().lower() for i in (items or []) if (i or "").strip()}
    if not selected_lower:
        return {"created": [], "notes": []}

    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    today = date.today()

    conn = get_conn()
    created: list[dict] = []
    notes: list[dict] = []
    seen_keys: set[tuple] = set()  # (ingredient name, entry_id) -- the same ingredient listed twice on one recipe shouldn't double-book
    for m, ing, ing_name, batch_factor in _iter_plan_meat_ingredients(weekly_plan_id):
        if not _matches_selected_item(ing_name, selected_lower):
            continue
        entry_id = m.get("entry_id")
        key = (ing_name.lower(), entry_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        lead_hours, tier = lead_hours_for_item(ing_name)
        move_date_str = _move_date(m["date"], lead_hours, dinner_window)
        if date.fromisoformat(move_date_str) < today:
            notes.append({
                "item": ing_name, "meal": m["meal"], "date": m["date"],
                "note": TOO_LATE_TO_THAW_NOTE,
            })
            continue

        description = _describe(ing_name, m["meal"], m["date"])
        quantity = _batch_quantity(ing, batch_factor)
        cur = conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type, inventory_item_id, meal_plan_entry_id, quantity) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', NULL, ?, ?)",
            (household_id(), weekly_plan_id, move_date_str, description, m["meal"], entry_id, quantity),
        )
        created.append({
            "prep_task_id": cur.lastrowid, "item": ing_name, "task_date": move_date_str,
            "related_meal": m["meal"], "date": m["date"], "lead_hours": lead_hours, "lead_tier": tier,
        })
    conn.commit()
    conn.close()
    return {"created": created, "notes": notes}


def mark_defrost_asked(weekly_plan_id: int) -> None:
    """
    Records that the freezer-check ask card has been answered or dismissed
    for this plan — the auto-card's own gate (see weekly_plan.get_weekly_plan
    /get_week_menu's defrost_asked_at passthrough). Set unconditionally
    (not "only if not already set"): re-answering from the Cook view's
    "Something in the freezer?" link is allowed any number of times per
    Emily's ask, and there's no meaningful difference between the first
    answer's timestamp and a later one for what this column exists to do
    (hide the automatic card, once).
    """
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET defrost_asked_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
