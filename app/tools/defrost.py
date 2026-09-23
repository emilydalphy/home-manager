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
from ._shared import household_id, require_household_row
from . import attendance as _attendance
# cooker is this module's clock as well as its name matcher (see the note
# above get_defrost_today). The module alias replaces the `from .cooker
# import _find_inventory_match` this block used to end with — one import
# for one module, the package's own convention, and no new edge in the
# import graph, since that named import already pulled cooker in here.
from . import cooker as _cooker
from . import inventory as _inventory
from . import leftovers as _leftovers
from . import quantities as _quantities
from . import recipes as _recipes
from . import rhythm as _rhythm
from . import weekly_plan as _weekly_plan

# ---------- Lead-time rule of thumb (Emily's ask: honest defaults, no
# invented precision) ----------
#
# Real thaw time depends on the exact cut, thickness, and fridge
# temperature — nothing here claims to model that precisely. This is a
# small, documented, three-tier table matched by keyword against the
# inventory item's own name, coarser than a real cookbook but transparent
# about what it is: a rule of thumb, not a simulation.
#
# Rule cited: USDA FSIS "The Big Thaw" — a refrigerator thaw is safe but
# slow, and even a comparatively small, thin cut (a pound of ground meat,
# boneless chicken breasts) needs a FULL DAY in the fridge; large or
# tightly-packed items scale up from there at roughly 24h per 4-5 lb.
# Once thawed, poultry/ground meat/fish are good for another 1-2 days in
# the fridge and red meat for 3-5 — which is exactly why the longer leads
# below are safe to plan around rather than cutting it close: a household
# that takes something out a night early hasn't put it at risk, it has
# room to spare.
#
# Emily's ask (2026-09-18): the household-pack sizes this app is actually
# planning for — a Costco-sized family pack of chicken thighs, say — are
# not the one-pound item the USDA's 24h figure assumes, and she's found in
# practice that a full pack needs more than 24 hours to thaw through. So
# every tier below moved up a size from where it started: what used to be
# the 24h "standard" default is now 48h, and the two tiers on either side
# of it moved with it.
#
# "Everyday cuts" (48h) is the default for anything that doesn't match a
# more specific keyword — chicken breasts/thighs, pork chops, steaks,
# ground meat, sausages, most fish not caught by the "small/thin" list
# below. This is the tier Emily's ask was about: a family-pack quantity of
# an ordinary cut, not the single-portion USDA baseline.
STANDARD_LEAD_HOURS = 48.0

# Large roasts / whole birds: USDA's roughly-24h-per-4-5-lb rule of thumb,
# for a family-pack-sized whole chicken/small roast, rounds to "about three
# days" once the same up-sizing above is applied — 72h is the honest single
# number for that tier, not a weight-scaled formula (this app doesn't track
# item weight).
LARGE_LEAD_HOURS = 72.0
# No bare "turkey": "whole turkey" already covers the legitimate whole-bird
# case, and a bare "turkey" keyword was matching straight through common
# compound items that are anything but large — "Ground Turkey" and "Turkey
# Bacon" both got tagged as a large roast before this was caught in review.
# Same reasoning kept "ham" off this list entirely (it's genuinely
# ambiguous — a whole holiday ham vs. diced deli ham — and "Hamburger"
# doesn't even mean ham; see _matches_keyword's word-boundary matching below
# for why that one part was already a plain bug, not just an ambiguous
# call).
_LARGE_KEYWORDS = (
    "whole chicken", "whole turkey", "roast", "brisket",
    "prime rib", "leg of lamb", "pork shoulder", "pork butt", "whole duck",
    "whole ham", "rack of",
)

# Small/thin cuts thaw faster than a family-pack of an everyday cut, but
# still get the USDA's own full-day floor rather than the shorter window
# sometimes quoted for a single one-pound portion — 24h is the honest
# single number for this tier now, one size up from where it started, same
# reasoning as STANDARD_LEAD_HOURS above.
SMALL_THIN_LEAD_HOURS = 24.0
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
    wrongly tagged an ordinary item as a 72h large roast. \\b anchors the
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
    technically allow it (a short lead against a late dinner_window, e.g. a
    synthetic 6h lead against an 8pm dinner lands at 2pm the SAME calendar
    day — no real tier is ever this short, but the floor holds regardless of
    how short a lead gets) — caught in independent review: the Today tile
    frames this as "defrost tonight",
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
        # A chain source, or a cook with portions for the freezer (2026-09-22).
        batch = _leftovers.batch_for_entry(entry_id, chains) if entry_id else None
        if batch:
            if batch["servings"] > 0 and batch["cook_eaters"] > 0:
                batch_factor = batch["servings"] / batch["cook_eaters"]
        for ing in recipe.get("ingredients") or []:
            ing_name = (ing.get("item") or "").strip()
            if not ing_name:
                continue
            match, confident = _cooker._find_inventory_match(ing_name, freezer_items)
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

    TODAY IS THE HOUSEHOLD'S, and this is the note the other two clock
    reads in this module point at. The deployed container runs UTC and
    households.timezone defaults to America/Toronto, so from 8pm Eastern
    the server's date is already tomorrow. Every date this module writes
    is a plain calendar day (prep_tasks.task_date), and every date it
    reads back is compared against one — so a server-clock "today" shows
    the Today tile TOMORROW's move while hiding tonight's still-pending
    one, for four hours every evening. Measured at Toronto 21:30 before
    this was changed: a task dated the household's today was invisible and
    a task dated the day after was on the tile.

    It also has to be the same clock moves.py already reads, because the
    fridge move on Now is built from these very rows and Now has run on
    `cooker.household_now` since 2026-09-14 — two halves of one screen
    disagreeing about what day it is is exactly how this app produced four
    separate defects in two days.

    household_today opens its own connection, so it is resolved BEFORE
    get_conn here and in both siblings below — nesting one inside an open
    transaction is how this codebase has twice earned an intermittent
    "database is locked". A clock that cannot be read falls back to the
    server's date (see cooker.household_today): a wrong hour once a day
    beats a blank tile.
    """
    today = _cooker.household_today().isoformat()
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

    The household's clock, for the reason written out above
    get_defrost_today: the window's near edge IS a calendar day, so on the
    server's clock the evening answer to "what do I need to defrost?"
    silently drops the move due TONIGHT — the one being asked about — and
    reaches a day too far at the other end. Measured at Toronto
    21:30 before this was changed: the window ran [server today .. +7]
    rather than [household today .. +7], losing today's own pending row.
    """
    today = _cooker.household_today()
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
        # A chain source, or a cook with portions for the freezer (2026-09-22).
        batch = _leftovers.batch_for_entry(entry_id, chains) if entry_id else None
        if batch:
            if batch["servings"] > 0 and batch["cook_eaters"] > 0:
                batch_factor = batch["servings"] / batch["cook_eaters"]
        for ing in recipe.get("ingredients") or []:
            if not _is_meat_ingredient(ing):
                continue
            ing_name = (ing.get("item") or "").strip()
            if not ing_name:
                continue
            yield m, ing, ing_name, batch_factor


# Which rows' shelf the app guessed rather than being told — the one list,
# kept beside the writes that produce them (inventory.GUESSED_LOCATION_SOURCES)
# rather than restated here, because this module reads it and never writes
# one. It covers the two grocery paths and the receipt scan; the comment
# there says which and why.
#
# What it can only ever do is make this ask LOUDER: excluding a row takes a
# reason for silence away, never adds one. So a stated fridge row that a
# receipt scan later merged into costs one extra question rather than a
# missed thaw, which is the direction this whole function is biased in.
_INFERRED_LOCATION_SOURCES = _inventory.GUESSED_LOCATION_SOURCES

# A grocery line that still means "you are going to buy this" — what a
# tapped freezer chip takes off the list. Deliberately an allow-list:
# 'purchased' and 'removed' are in the kitchen or nowhere, and 'carried'
# is an UNANSWERED keep-or-drop line from last week, which the carry-over
# step owns and this one must not answer for it.
_STILL_TO_BUY_STATUSES = ("needed", "in_cart", "spice")

# The mark the freezer step leaves on a grocery line it sets aside — the
# pre-shop drop's removed_by, beside 'already_have', 'staple' and the two
# carried marks. The line lands under Shop's "Already had on hand" with
# every other "have it" and comes back through the same undo
# (pre_shop.undo_pre_shop_drop), which reads this mark to know the line's
# defrost move goes with it.
FREEZER_REMOVED_BY = "freezer"

# The item out of a _describe() string — for _release_frozen_item and
# _settled_move_for_entry alike: the task rows carry no item column, and
# the description is the one key this module has always de-duped moves
# by. One pattern for both readers (2026-09-21 integration folded a
# second, case-blind copy into this one), so the two doors that book a
# move can never disagree about which row is whose.
_MOVE_ITEM_RE = re.compile(r"^move the (.+?) to the fridge\b", re.IGNORECASE)


def thawed_item(description: str) -> str:
    """
    The ingredient a defrost row is about, read back out of its own
    sentence — the public door onto _MOVE_ITEM_RE, for the one reader
    outside this module (weekly_plan._release_prep_rows, which holds a
    thawed ingredient when a swap takes its dinner off the plan). A
    description this module did not write answers "" and the caller says
    something that needs no ingredient name.
    """
    match = _MOVE_ITEM_RE.match(description or "")
    return match.group(1).strip() if match else ""


# A defrost row that means the move is settled: booked, or done. NOT
# 'skipped' — that is the household declining this one move on the Now
# tile (see cooker.check_off_prep_step), which says nothing about whether
# the food is frozen, and must leave the question askable.
_SETTLED_DEFROST_STATUSES = ("pending", "done")


def _plan_need_by_item(weekly_plan_id: int) -> tuple[dict[str, dict], dict[str, tuple[float, str | None] | None]]:
    """
    One walk over the plan's meat/seafood ingredients, giving both halves
    meat_items_for_plan needs: the nights each one feeds, and how much of
    it the week actually wants.

    The need is the same figure the defrost task itself would carry
    (_batch_quantity, so a batch cooked for two nights counts once for
    both), summed across nights. None the moment any night's amount cannot
    be read or cannot be added to the others — an unknown total is not a
    total, and this module's bias is to ask rather than to assume.
    """
    nights: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    need: dict[str, tuple[float, str | None] | None] = {}
    for m, ing, ing_name, batch_factor in _iter_plan_meat_ingredients(weekly_plan_id):
        key = ing_name.lower()
        names.setdefault(key, ing_name)
        night = {"date": m["date"], "meal": m["meal"], "weekday": _weekday_name(m["date"]),
                 "slot": m.get("slot") or "dinner"}
        rows = nights.setdefault(key, [])
        if night not in rows:
            rows.append(night)
        parsed = _quantities._parse_quantity(_batch_quantity(ing, batch_factor))
        if key not in need:
            need[key] = parsed
            continue
        running = need[key]
        if running is None or parsed is None:
            need[key] = None
            continue
        added = _quantities._convert_to_unit(parsed[0], parsed[1], running[1])
        need[key] = None if added is None else (running[0] + added, running[1])
    return {k: {"item": names[k], "nights": v} for k, v in nights.items()}, need


def _covered_at_home(need: dict[str, tuple[float, str | None] | None]) -> set[str]:
    """
    The names this household's own fridge demonstrably covers — the one
    reason the app can be sure a thing is not frozen without anybody
    saying so.

    Reuses recipes._KitchenStock rather than answering "is there enough at
    home" a second time: that class is the app's one answer to it
    (2026-09-14, after two ounces on a shelf took two POUNDS off a
    shopping list), and a second implementation here could disagree with
    the ingest about the very line it is deciding against. Narrowed to the
    FRIDGE — a pantry or 'other' row is neither thawed nor in the fridge,
    which is what criterion 3 actually says — and away from the rows whose
    shelf the app guessed (_INFERRED_LOCATION_SOURCES).
    """
    conn = get_conn()
    try:
        stock = _recipes._KitchenStock(
            conn, locations={"fridge"}, exclude_sources=set(_INFERRED_LOCATION_SOURCES),
        )
    finally:
        conn.close()
    return {key for key, want in need.items() if stock.covers(key, want)}


def _settled_nights(weekly_plan_id: int, by_item: dict[str, dict]) -> tuple[set[str], set[str]]:
    """
    Two sets of the very _describe() strings confirm_frozen_items de-dupes
    with — so the ask and the write cannot drift about which move is which.

      * settled — the MOVES this ask has nothing left to say about: a move
        the app booked itself off tracked freezer inventory
        (sync_defrost_tasks; inventory_item_id set), pending or done, and
        a night it is already too late to thaw for. confirm_frozen_items
        refuses to write a task whose move date has gone by and hands back
        TOO_LATE_TO_THAW_NOTE instead, so asking about such a night can
        only ever produce a note — which is Emily's "tonight's already
        eaten shrimp", settled without guessing at a shelf.
      * frozen — the moves the household booked by hand (inventory_item_id
        NULL: this step's chips, or Shop's "Yes, freezing it" —
        book_defrost_for_grocery_line writes the same row), pending or
        done. Until 2026-09-21 these were settled too,
        and the chip vanished the moment it was tapped; the Plan tab's
        freezer row now reopens the step to CHANGE the answer, so a booked
        night is offered again with its chip already on, and deselecting
        it is how the move is cancelled (see confirm_frozen_items).

    Both are per NIGHT rather than per item, which is the whole point: a
    chicken booked for Wednesday says nothing about the second chicken
    dinner swapped in for Friday, and keying this by name made that second
    thaw unbookable for the rest of the week.

    The too-late test reads the HOUSEHOLD's today because confirm_frozen_items
    does, and the two have to agree about what is still possible: an earlier
    draft read date.today() here "to match" a write that had, on a parallel
    branch, just moved to household_today — so from 8pm Eastern the ask
    dropped a night the write would happily have booked. Same clock on both
    sides, or the screen and the write drift about what is still possible.
    household_today opens its own connection, so it is resolved before this
    function's own opens (see get_defrost_today's note).
    """
    today = _cooker.household_today()
    conn = get_conn()
    rows = conn.execute(
        "SELECT description, inventory_item_id FROM prep_tasks WHERE household_id = ? "
        "AND weekly_plan_id = ? AND task_type = 'defrost' "
        f"AND status IN ({','.join('?' * len(_SETTLED_DEFROST_STATUSES))})",
        (household_id(), weekly_plan_id, *_SETTLED_DEFROST_STATUSES),
    ).fetchall()
    conn.close()
    settled = {(r["description"] or "") for r in rows if r["inventory_item_id"] is not None}
    frozen = {(r["description"] or "") for r in rows if r["inventory_item_id"] is None}

    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    for entry in by_item.values():
        lead_hours, _tier = lead_hours_for_item(entry["item"])
        for night in entry["nights"]:
            move = _move_date(night["date"], lead_hours, dinner_window)
            if date.fromisoformat(move) < today:
                settled.add(_describe(entry["item"], night["meal"], night["date"]))
    return settled, frozen


# A plan whose week is still ahead of or around the household: its lines
# are still wanted. 'retired' is the one status a plan leaves the list by
# (the same set cooker.list_prep_tasks reads).
_LIVE_PLAN_STATUSES = ("draft", "approved")

# Why an item's line cannot come off the list even though one exists —
# the one reason today: another live plan's meal is counted into the same
# line (the ingest folds two weeks' chicken onto one line while the first
# is unbought — grocery._merge_target), so this week's yes must not take
# next week's share off the list. The screen says the fridge half alone.
ON_LIST_SHARED = "shared"


def _grocery_lines_by_item(names, weekly_plan_id: int) -> dict[str, list[dict]]:
    """
    Each of the plan's meats' grocery lines, keyed the way by_item is: the
    lines still to be bought (_STILL_TO_BUY_STATUSES), and the lines this
    very step already set aside (removed_by FREEZER_REMOVED_BY). The first
    kind is what a tapped chip takes off the list; the second is what a
    deselected chip puts back — and both count as "on the list" for the
    step's "What that means" line, so a chip reads "off the shopping list"
    the same way before and after it is tapped. Plural-tolerant, the way
    every name comparison in this module is: "Chicken Thigh" on the list is
    "Chicken Thighs" in the plan.

    Scoped to THIS plan's lines — stamped with it (source_weekly_plan_id),
    or counted into by one of its meals (meal_plan_grocery_links: the
    ingest restamps a merged line to the LATEST plan, so the stamp alone
    would lose this week's line to next week's approval) — plus the loose
    ones no plan wrote (NULL — a hand-added "chicken thighs" is still the
    line a tapped chip would take off). Never another plan's own line:
    next week's line set aside by next week's step is not this week's
    answer, and un-tapping here must not put it back (2026-09-21).

    Each row carries `shared_with`: the OTHER live plans whose meals are
    counted into the same line. Such a line is not this plan's to take off
    (confirm_frozen_items books the move only) and does not make the item
    `on_list` (meat_items_for_plan says why: ON_LIST_SHARED).
    """
    conn = get_conn()
    live = ",".join("?" * len(_LIVE_PLAN_STATUSES))
    rows = conn.execute(
        "SELECT g.id, g.item, g.status, g.removed_by, g.source_weekly_plan_id, "
        "  (SELECT GROUP_CONCAT(DISTINCT e.weekly_plan_id) FROM meal_plan_grocery_links l "
        "   JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
        "   JOIN weekly_plans wp ON wp.id = e.weekly_plan_id "
        "   WHERE l.grocery_item_id = g.id AND e.weekly_plan_id != ? "
        f"  AND wp.status IN ({live})) AS shared_with "
        "FROM grocery_items g WHERE g.household_id = ? "
        "AND (g.source_weekly_plan_id = ? OR g.source_weekly_plan_id IS NULL "
        "     OR EXISTS (SELECT 1 FROM meal_plan_grocery_links l "
        "                JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
        "                WHERE l.grocery_item_id = g.id AND e.weekly_plan_id = ?)) "
        f"AND ((g.status IN ({','.join('?' * len(_STILL_TO_BUY_STATUSES))}) AND g.excluded_from_list = 0) "
        "OR (g.status = 'removed' AND g.removed_by = ?))",
        (weekly_plan_id, *_LIVE_PLAN_STATUSES, household_id(), weekly_plan_id, weekly_plan_id,
         *_STILL_TO_BUY_STATUSES, FREEZER_REMOVED_BY),
    ).fetchall()
    conn.close()
    out: dict[str, list[dict]] = {key: [] for key in names}
    for row in rows:
        line = (row["item"] or "").strip().lower()
        record = dict(row)
        record["shared_with"] = [int(x) for x in (row["shared_with"] or "").split(",") if x]
        for key in out:
            if _matches_selected_item(key, {line}):
                out[key].append(record)
    return out


def _own_lines(lines: list[dict]) -> list[dict]:
    """The lines a tapped chip may take off the list: not shared with
    another live plan's meal."""
    return [r for r in lines if not r["shared_with"]]


def meat_items_for_plan(weekly_plan_id: int) -> list[dict]:
    """
    Every distinct meat/seafood ingredient this plan's own meals call for,
    each with the night(s) it feeds — the freezer step's own chip list, and
    the GET route behind it. Each entry also says whether it has a grocery
    line this week (`on_list`, so the step can promise "off the shopping
    list" only where there is a line to take off — `on_list_reason` says
    why not when a line exists but is another live plan's too,
    ON_LIST_SHARED) and whether the household has already said it is
    frozen (`frozen`, so reopening the step shows the answer as given).

    Emily, 2026-09-19: "it is for the user to be able to flag if they have
    meat in the freezer, that needs to be defrosted in time to be cooked,
    and takes the mental energy off the user by asking the question so
    they don't need to think about it." So the question is asked about
    ALL of the week's meat, at approval, and a yes does two things at once
    (confirm_frozen_items): the line comes off the list and the move is
    booked. Until 2026-09-21 a third rule left off any name with a grocery
    line still to buy — "the app has just told her to go and buy it" —
    which, since approval puts the whole week's meat on the list, made
    this list EMPTY at the one moment the step is shown, and the ask lived
    on only after the shop. That rule is gone: being on the list is now
    the thing a tapped chip changes, not a reason to stay quiet.

    What is still left off, and the safe direction throughout is to ask one
    question too many rather than miss a thaw — a missed thaw costs the
    dinner:

      1. a name the FRIDGE demonstrably covers (_covered_at_home) — it is
         thawed, and the ingest never put it on the list;
      2. a night whose move the app booked itself off tracked freezer
         inventory, pending or done (the app already knows it is frozen;
         a second yes here would book the move twice);
      3. a night it is already too late to thaw for.

    2 and 3 are per NIGHT, so an item keeps the nights that are still open
    and only drops out when every one of them is settled. A night this step
    itself booked is NOT left off: it is offered again with `frozen` set,
    which is how the Plan tab's freezer row reopens the answer to change it
    (see _settled_nights).

    A night only appears here if it's a real cook night (see
    _iter_plan_meat_ingredients) — the leftover-chain reheat night is left
    off "which night(s)" the same way it's left off the scheduled task
    itself: the batch that covers it is cooked, and defrosted for, on the
    source night alone.
    """
    by_item, need = _plan_need_by_item(weekly_plan_id)
    covered = _covered_at_home(need)
    settled, frozen_nights = _settled_nights(weekly_plan_id, by_item)
    lines = _grocery_lines_by_item(by_item, weekly_plan_id)
    # Each night also says WHEN it would move to the fridge — the same
    # _move_date confirm_frozen_items books, so the freezer step's "What
    # that means" line ("Chicken thighs → off the shopping list · into the
    # fridge Saturday night, for Monday's dinner") and the task it writes
    # can't name two nights.
    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")

    out: list[dict] = []
    for key, entry in by_item.items():
        if key in covered:
            continue
        lead_hours, _tier = lead_hours_for_item(entry["item"])
        # `frozen` is THIS plan's own booked moves (_settled_nights), never
        # a name-matched grocery line: a line another plan's step set
        # aside says nothing about this week's freezer.
        frozen = False
        nights = []
        for n in entry["nights"]:
            description = _describe(entry["item"], n["meal"], n["date"])
            if description in settled:
                continue
            move = _move_date(n["date"], lead_hours, dinner_window)
            nights.append(dict(n, move_date=move, move_weekday=_weekday_name(move)))
            if description in frozen_nights:
                frozen = True
        if nights:
            own = _own_lines(lines[key])
            out.append({"item": entry["item"], "nights": nights,
                        "on_list": bool(own), "frozen": frozen,
                        "on_list_reason": None if own or not lines[key] else ON_LIST_SHARED})
    return sorted(out, key=lambda e: (e["item"].lower()))


def confirm_frozen_items(weekly_plan_id: int, items: list[str]) -> dict:
    """
    The household's whole answer to "Anything already in the freezer?" —
    `items` are the chips that are ON, as plain ingredient names exactly as
    meat_items_for_plan hands them out (its own "item" values); everything
    else on the plan is, by that same answer, NOT frozen. Anything that
    doesn't match one of THIS plan's own meat/seafood ingredients is
    silently ignored rather than erroring, since a stale chip (the plan
    changed after the step was drawn) is expected, not a bug report.

    A tapped chip means "I already have this, frozen", and that is two
    facts, written together (Emily, 2026-09-21):

      1. its grocery line(s) for this week come off the list — the SAME
         write Shop's "Have it" makes (pre_shop.drop_grocery_item_pre_shop:
         a soft remove that lands under "Already had on hand", tells a
         staple "we have plenty", and never writes inventory — policy
         2026-09-01), marked removed_by FREEZER_REMOVED_BY so the one undo
         (pre_shop.undo_pre_shop_drop, "Actually, I need it") knows to take
         the move below with it;
      2. one prep_tasks row per (item, cook night), dated with the item's
         own lead (lead_hours_for_item) — same v1-simplification and same
         leftovers handling as _candidates_from_plan (a reheat night is
         never its own task; its cook night's quantity already covers it).
         inventory_item_id is always NULL on what this creates — see the
         module-level note above: confirming a freezer item here is a fact
         about this one plan, never a write to inventory.

    An un-tapped chip that WAS on (the step reopened from the Plan tab's
    freezer row to change the answer) is the same two facts reversed: the
    line goes back on the list and the still-pending move is cancelled
    (_release_frozen_item). A move already ticked done stays — the food is
    in the fridge, whatever the list says. On a first answer nothing is
    on, so "Nothing frozen — I'm buying it all" writes nothing at all here
    (the route stamps defrost_asked_at and that is the whole answer).

    Answering again with the same chips is idempotent: one row per
    (entry, item), a line already set aside is left alone.

    A candidate whose move date has already passed is never inserted —
    only possible for a meal happening TODAY (_move_date always leaves at
    least one full calendar day between the move and the meal, so any
    earlier cook date's move date is still today-or-later). That item/meal
    gets a plain, calm note back instead of a task nobody could have
    actually acted on — in voice, per DESIGN_SYSTEM.md's "calm and
    reassuring, never cheery" rule for anything that names a problem: the
    fact, plus its way out, in the same breath.

    "Already passed" is measured against the HOUSEHOLD's today (see
    get_defrost_today's note), and this one had teeth. On the server's
    clock, from 8pm Eastern, a night whose move date is the household's
    own today read as already gone: measured with a 48h item at Toronto
    21:30, a cook two nights out was refused with the too-late note, and
    at Toronto 09:00 on the same household day the same cook was booked.
    So for four hours every evening — precisely when somebody taps
    "Something in the freezer?" — the household lost a thaw it could
    genuinely have started that night with about forty-five hours in hand,
    and was told it was too late instead. The ask leaves off a night that
    is already too late (meat_items_for_plan, rule 3) on the same clock,
    for the same reason — see _settled_nights.

    Returns {"created": [...], "notes": [...], "set_aside": [...],
    "put_back": [...], "cancelled": n} — never raises for "nothing
    matched" or "nothing to do"; both are ordinary answers here (see
    get_defrost_today's docstring for why this module treats an empty
    result as data, not an error).
    """
    selected_lower = {(i or "").strip().lower() for i in (items or []) if (i or "").strip()}

    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    # Every read below is resolved before get_conn — see get_defrost_today's
    # note on the nested-connection hazard. The plan walk is materialised
    # here for the same reason: with a leftover chain on the plan,
    # _iter_plan_meat_ingredients -> leftovers.batch_for_source -> eaters_at
    # -> attendance.get_slot_attendance opens one connection per counted
    # night, and until 2026-09-21 the generator was consumed while this
    # function's own connection was already mid-write. What the ordering
    # buys is that the clock is not one more.
    today = _cooker.household_today()
    plan_meats = list(_iter_plan_meat_ingredients(weekly_plan_id))
    plan_names: dict[str, str] = {}
    for _m, _ing, ing_name, _factor in plan_meats:
        plan_names.setdefault(ing_name.lower(), ing_name)

    conn = get_conn()
    created: list[dict] = []
    notes: list[dict] = []
    seen_keys: set[tuple] = set()  # (ingredient name, entry_id) -- the same ingredient listed twice on one recipe shouldn't double-book
    for m, ing, ing_name, batch_factor in plan_meats:
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
        # Answering again (a reopened step, a double tap, a retried POST)
        # must not book the same move twice: one row per (entry, item).
        existing = conn.execute(
            "SELECT id FROM prep_tasks WHERE household_id = ? AND weekly_plan_id = ? "
            "AND task_type = 'defrost' AND inventory_item_id IS NULL "
            "AND meal_plan_entry_id IS ? AND description = ?",
            (household_id(), weekly_plan_id, entry_id, description),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE prep_tasks SET task_date = ?, quantity = ? WHERE id = ?",
                (move_date_str, quantity, existing["id"]),
            )
            task_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
                "related_meal, status, task_type, inventory_item_id, meal_plan_entry_id, quantity) "
                "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', NULL, ?, ?)",
                (household_id(), weekly_plan_id, move_date_str, description, m["meal"], entry_id, quantity),
            )
            task_id = cur.lastrowid
        created.append({
            "prep_task_id": task_id, "item": ing_name, "task_date": move_date_str,
            "related_meal": m["meal"], "date": m["date"], "lead_hours": lead_hours, "lead_tier": tier,
        })
    conn.commit()
    conn.close()

    # The list half, after the write above has closed: both pre_shop writes
    # open connections of their own.
    from . import pre_shop as _pre_shop

    lines = _grocery_lines_by_item(plan_names, weekly_plan_id)
    set_aside: list[int] = []
    put_back: list[int] = []
    cancelled = 0
    for key, name in plan_names.items():
        if _matches_selected_item(key, selected_lower):
            # A line another live plan's meal is counted into stays: this
            # week's yes is not next week's, and the move alone is booked.
            for row in _own_lines(lines[key]):
                if row["status"] != "removed":
                    _pre_shop.drop_grocery_item_pre_shop(row["id"], author=FREEZER_REMOVED_BY)
                    set_aside.append(row["id"])
        else:
            for row in lines[key]:
                if row["status"] == "removed":
                    # Takes the move with it (undo_pre_shop_drop reads the
                    # mark), so a deselect is one write path — the same
                    # one Shop's "Actually, I need it" runs.
                    put_back.append(row["id"])
                    cancelled += _pre_shop.undo_pre_shop_drop(row["id"]).get("moves_cancelled", 0)
            # And the move alone where there was no line to put back
            # (covered at home, never on the list) — a no-op otherwise.
            cancelled += _release_frozen_item(name, weekly_plan_id)
    return {"created": created, "notes": notes, "set_aside": set_aside,
            "put_back": put_back, "cancelled": cancelled}


def _release_frozen_item(item_name: str, weekly_plan_id: int | None = None) -> int:
    """
    Cancel the still-pending moves THIS STEP booked for one ingredient —
    the household saying "actually, I don't have that frozen", either by
    un-tapping the chip (confirm_frozen_items) or by putting its grocery
    line back on the list (pre_shop.undo_pre_shop_drop, which calls here
    only for a line the step set aside). Deleted rather than marked, the
    way sync_defrost_tasks drops a move that is no longer true; a row
    already ticked done is left alone — the food is in the fridge — and
    so is anything the app booked off tracked inventory (inventory_item_id
    set), which is the sync's to keep. Scoped to one plan when the caller
    knows which; a grocery line with no source plan releases every plan's.
    Returns how many were cancelled.
    """
    name = (item_name or "").strip().lower()
    if not name:
        return 0
    wanted = {name}
    conn = get_conn()
    params: list = [household_id()]
    scope = ""
    if weekly_plan_id is not None:
        scope = "AND weekly_plan_id = ? "
        params.append(weekly_plan_id)
    rows = conn.execute(
        "SELECT id, description FROM prep_tasks WHERE household_id = ? " + scope +
        "AND task_type = 'defrost' AND status = 'pending' AND inventory_item_id IS NULL "
        "AND meal_plan_entry_id IS NOT NULL",
        params,
    ).fetchall()
    doomed = []
    for r in rows:
        match = _MOVE_ITEM_RE.match(r["description"] or "")
        if match and _matches_selected_item(match.group(1), wanted):
            doomed.append((r["id"],))
    if doomed:
        conn.executemany("DELETE FROM prep_tasks WHERE id = ?", doomed)
        conn.commit()
    conn.close()
    return len(doomed)


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

    The one clock read in this module deliberately left on the server:
    SQLite's datetime('now') is UTC, and defrost_asked_at is only ever
    read as "is it set" — weekly_plan passes it straight through to
    get_week_menu and shell.js asks `!data.defrost_asked_at`. Nothing
    compares it against a calendar day, so there is no day for it to be
    wrong about. Give it a reader that does, and it belongs on
    cooker.household_now like everything above it.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET defrost_asked_at = datetime('now') WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()


# ---------- "Freezing it?" — the question the Shop checklist asks ----------
#
# Loop Board 3e21f4c0 (2026-09-21): ticking a meat/seafood line bought is
# the moment the household knows where that pack is going, and the freezer
# step at approval could not ask about it (rule 3 of meat_items_for_plan:
# it was still on the shopping list). So the list asks, once, under the
# just-ticked row — "Freezing it? I'll remind you Saturday night to move
# it to the fridge for Monday." — and a yes books the same defrost row
# confirm_frozen_items writes for the freezer step: inventory_item_id
# NULL (a fact about this plan, never an inventory write), keyed by the
# meal's entry and the same _describe() string, so the two doors cannot
# book one move twice and sync_defrost_tasks never sweeps it.
#
# Which meal a line is FOR is read off meal_plan_grocery_links, the
# ledger plan_meal writes at ingest — the same record Today's shop move
# reads (grocery.entry_ids_awaiting_a_shop) — never by matching names. A
# line no meal recorded (loose "Add something" meat) has no cook date and
# is not asked about. A line feeding two meals is asked about the FIRST
# cook night (the mockup's one sentence), and the yes books that night.
#
# Deliberately separate from confirm_frozen_items (another branch is
# changing that function at the time of writing): the helpers below share
# its arithmetic (lead_hours_for_item, _move_date, _describe) and nothing
# else.

def _grocery_line_first_meal(conn, item_id: int) -> dict | None:
    """
    The first cook night a grocery line feeds, read off the ledger:
    {entry_id, weekly_plan_id, date, meal, item} or None when no meal
    recorded the line. A component-based plan's entries carry a
    placeholder date (see schema.sql on meal_plan_entries.component_category)
    and are left out; so is an entry with no plan, which the defrost row
    could not be filed under.
    """
    row = conn.execute(
        "SELECT l.item AS link_item, e.id AS entry_id, e.weekly_plan_id, e.date, "
        "COALESCE(r.name, e.freeform_meal) AS meal "
        "FROM meal_plan_grocery_links l "
        "JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
        "LEFT JOIN recipes r ON r.id = e.recipe_id "
        "WHERE l.household_id = ? AND l.grocery_item_id = ? "
        "AND e.household_id = ? AND e.weekly_plan_id IS NOT NULL "
        "AND e.component_category IS NULL "
        "ORDER BY e.date ASC, e.id ASC LIMIT 1",
        (household_id(), item_id, household_id()),
    ).fetchone()
    if row is None or not row["date"]:
        return None
    return {
        "entry_id": row["entry_id"], "weekly_plan_id": row["weekly_plan_id"],
        "date": row["date"], "meal": row["meal"] or "dinner", "item": (row["link_item"] or "").strip(),
    }


def _settled_move_for_entry(conn, entry_id: int, names: set[str]):
    """
    The pending-or-done defrost row already booked for this meal and this
    ingredient, whichever door wrote it (the freezer step, the inventory
    sync, this ask), or None. Matched on the row's own description rather
    than a column, because that is the one thing every writer stamps the
    same way (_describe); the plural tolerance is _matches_selected_item's.
    """
    rows = conn.execute(
        "SELECT id, status, task_date, description FROM prep_tasks WHERE household_id = ? "
        "AND task_type = 'defrost' AND meal_plan_entry_id = ? "
        f"AND status IN ({','.join('?' * len(_SETTLED_DEFROST_STATUSES))}) ORDER BY id",
        (household_id(), entry_id, *_SETTLED_DEFROST_STATUSES),
    ).fetchall()
    wanted = {n.strip().lower() for n in names if (n or "").strip()}
    for row in rows:
        m = _MOVE_ITEM_RE.match(row["description"] or "")
        if m and _matches_selected_item(m.group(1), wanted):
            return row
    return None


def _move_label(move_date_str: str, today: date) -> str:
    """"Saturday night", or "tonight" when the move is today's."""
    if date.fromisoformat(move_date_str) == today:
        return "tonight"
    return f"{_weekday_name(move_date_str)} night"


def freezing_offer_for_grocery_line(line: dict, *, conn=None, today: date | None = None,
                                    dinner_window: str | None = None) -> dict | None:
    """
    What the checklist may ask under this line once it is ticked, or None:
    only a meat/seafood line (_is_meat_ingredient, on the line's own
    category), only one a plan meal recorded (the ledger), only while the
    move is still ahead on the household's clock, and never once a move
    for that meal and ingredient is booked or done — by the freezer step,
    the inventory sync, or an earlier yes here.

    The dict is what the Shop screen needs to say its sentence and what
    the route needs to book: entry_id, meal, cook_date, cook_weekday,
    move_date, move_label ("Saturday night" / "tonight"), lead_hours.
    """
    if not _is_meat_ingredient(line):
        return None
    own_conn = conn is None
    if today is None:
        today = _cooker.household_today()
    if dinner_window is None:
        dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    c = get_conn() if own_conn else conn
    try:
        meal = _grocery_line_first_meal(c, int(line["id"]))
        if meal is None:
            return None
        item_name = meal["item"] or (line.get("item") or "").strip()
        lead_hours, tier = lead_hours_for_item(item_name)
        move_date_str = _move_date(meal["date"], lead_hours, dinner_window)
        if date.fromisoformat(move_date_str) < today:
            return None
        if _settled_move_for_entry(c, meal["entry_id"], {item_name, line.get("item") or ""}) is not None:
            return None
    finally:
        if own_conn:
            c.close()
    return {
        "entry_id": meal["entry_id"], "weekly_plan_id": meal["weekly_plan_id"],
        "item": item_name, "meal": meal["meal"],
        "cook_date": meal["date"], "cook_weekday": _weekday_name(meal["date"]),
        "move_date": move_date_str, "move_label": _move_label(move_date_str, today),
        "lead_hours": lead_hours, "lead_tier": tier,
    }


def stamp_freezing_offers(items: list[dict]) -> list[dict]:
    """
    Mutates each needed grocery line in `items`, adding `freezing` (the
    offer above) to the meat/seafood ones that have one. Everything else is
    left untouched — no key at all, so an older copy of the list on a
    phone reads the same as a fresh one. One clock and one rhythm read for
    the whole list; one connection.
    """
    meat = [it for it in items if _is_meat_ingredient(it)]
    if not meat:
        return items
    today = _cooker.household_today()
    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    conn = get_conn()
    try:
        for it in meat:
            offer = freezing_offer_for_grocery_line(it, conn=conn, today=today, dinner_window=dinner_window)
            if offer is not None:
                it["freezing"] = offer
    finally:
        conn.close()
    return items


def _plan_move_quantity(weekly_plan_id: int, entry_id: int, item_name: str, fallback: str) -> str:
    """
    The amount a move for (entry, item) carries — the very string
    confirm_frozen_items writes (_batch_quantity over the plan's own
    ingredient, so a night cooking for a later night's leftovers is
    scaled the same way), so the two doors' rows are identical byte for
    byte. Where the plan has no such ingredient to read (a freeform
    meal, a recipe edited since the line was written) the grocery line's
    own amount stands in, normalised through the app's one parser and
    formatter (quantities._parse_quantity / _format_quantity) rather
    than copied as the list spelt it.

    Opens connections of its own (the plan walk) — call it with none of
    this module's open, the get_defrost_today rule.
    """
    wanted = {item_name.strip().lower()}
    for m, ing, ing_name, batch_factor in _iter_plan_meat_ingredients(weekly_plan_id):
        if m.get("entry_id") == entry_id and _matches_selected_item(ing_name, wanted):
            return _batch_quantity(ing, batch_factor)
    parsed = _quantities._parse_quantity(fallback or "")
    if parsed is None:
        return (fallback or "").strip()
    return _quantities._format_quantity(parsed[0], parsed[1])


class FreezingNotOffered(ValueError):
    """The line is not one the checklist asks about: not meat/seafood, no
    meal recorded it, too late to thaw for that meal, or the move is
    already booked. The message is the plain reason."""


def book_defrost_for_grocery_line(item_id: int, freezing: bool) -> dict:
    """
    The checklist's answer for one line. `freezing` True books the defrost
    move for the first meal the line feeds — one prep_tasks row, the exact
    shape confirm_frozen_items writes (inventory_item_id NULL, keyed by
    entry and _describe), updated in place if it exists. False is "Put
    back": the pending move this ask (or any door) booked for that meal
    and ingredient is removed; a done one is left alone, and nothing to
    remove is an ordinary answer. "Straight to the fridge" never reaches
    here — it writes nothing.

    Raises ValueError for a line this household does not have (the
    route's 404) and FreezingNotOffered when the line is not askable (the
    route's 400) — on a yes only; a put-back on an unaskable line is a
    no-op, because the line stopped being askable the moment the yes
    booked it.
    """
    # Both open a connection of their own — resolved before this
    # function's, the module's rule (see get_defrost_today's note).
    today = _cooker.household_today()
    dinner_window = _rhythm.get_household_rhythm().get("dinner_window")
    conn = get_conn()
    try:
        require_household_row(conn, "grocery_items", item_id, label="grocery list item")
        line = conn.execute(
            "SELECT id, item, category, quantity FROM grocery_items WHERE id = ? AND household_id = ?",
            (item_id, household_id()),
        ).fetchone()
        line = dict(line)
        if not freezing:
            meal = _grocery_line_first_meal(conn, item_id)
            removed = None
            if meal is not None:
                row = _settled_move_for_entry(conn, meal["entry_id"], {meal["item"], line["item"]})
                if row is not None and row["status"] == "pending":
                    conn.execute("DELETE FROM prep_tasks WHERE id = ?", (row["id"],))
                    conn.commit()
                    removed = row["id"]
            return {"item_id": item_id, "freezing": False, "removed_prep_task_id": removed}

        if not _is_meat_ingredient(line):
            raise FreezingNotOffered("That's not something to thaw.")
        meal = _grocery_line_first_meal(conn, item_id)
        if meal is None:
            raise FreezingNotOffered("No meal on the plan is waiting on that.")
        item_name = meal["item"] or line["item"].strip()
        # A yes sent twice — a double tap, or a replay whose first reply was
        # lost in the store's dead zone — is the same yes: the move already
        # booked is the answer, not a refusal. Same rule as the status route.
        booked = _settled_move_for_entry(conn, meal["entry_id"], {item_name, line["item"]})
        if booked is not None:
            return {
                "item_id": item_id, "freezing": True, "prep_task_id": booked["id"],
                "task_date": booked["task_date"], "move_label": _move_label(booked["task_date"], today),
                "item": item_name, "related_meal": meal["meal"], "date": meal["date"], "already_booked": True,
            }
        lead_hours, tier = lead_hours_for_item(item_name)
        move_date_str = _move_date(meal["date"], lead_hours, dinner_window)
        if date.fromisoformat(move_date_str) < today:
            raise FreezingNotOffered(TOO_LATE_TO_THAW_NOTE)
    finally:
        conn.close()

    # The amount is the freezer step's own (_plan_move_quantity walks the
    # plan, which opens connections of its own), so it is read between
    # this function's read and its write — the same row from either door.
    quantity = _plan_move_quantity(meal["weekly_plan_id"], meal["entry_id"], item_name, line.get("quantity") or "")
    description = _describe(item_name, meal["meal"], meal["date"])
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type, inventory_item_id, meal_plan_entry_id, quantity) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost', NULL, ?, ?)",
            (household_id(), meal["weekly_plan_id"], move_date_str, description, meal["meal"],
             meal["entry_id"], quantity),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "item_id": item_id, "freezing": True, "prep_task_id": cur.lastrowid,
        "task_date": move_date_str, "move_label": _move_label(move_date_str, today),
        "item": item_name, "related_meal": meal["meal"], "date": meal["date"],
        "lead_hours": lead_hours, "lead_tier": tier, "already_booked": False,
    }
