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
from . import inventory as _inventory
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
    """
    if not freezer_items:
        return []
    recipes_by_name = {r["name"]: r for r in _recipes.list_recipes()}
    candidates = []
    for m in plan.get("meals") or []:
        recipe = recipes_by_name.get(m.get("meal"))
        if not recipe:
            continue
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
                "quantity": (ing.get("qty") or "").strip(),
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
    """
    conn = get_conn()
    candidates = defrost_candidates_for_plan(weekly_plan_id)
    existing = conn.execute(
        "SELECT id, inventory_item_id, meal_plan_entry_id, task_date FROM prep_tasks "
        "WHERE weekly_plan_id = ? AND household_id = ? AND task_type = 'defrost' "
        "AND meal_plan_entry_id IS NOT NULL",
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
