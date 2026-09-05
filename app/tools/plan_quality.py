"""
Deterministic, LOG-AND-WARN-ONLY quality checks for a generated week.

Scope: check_and_log is invoked from _finish_week_slots, which only the
DAY-BASED generation branch calls (pre-existing placement, agent.py). A
component-based household therefore gets no quality checking from this
module today -- that is a known gap, not coverage.

The generation prompt (see generate_weekly_plan_llm's instructions in
agent.py) tells the model a long list of rules -- a `rush` night is capped
at RUSH_MAX_MINUTES, a weeknight cap when the household has set one, don't
run the same main_protein three nights straight, don't repeat a dinner
already eaten in the last three weeks, write a real reason instead of
generic filler, surface at least one new recipe, use at most one open slot
and never for breakfast/lunch. Nothing downstream ever checked whether the
model actually did any of that -- see the VERIFIED FINDINGS this module
was built against. This is a first pass at closing that gap WITHOUT
changing behaviour: it only observes and logs. It does not repair
anything, does not touch the plan, and does not decide anything Emily
hasn't decided yet (the "does every dinner need a vegetable" plate rule,
and exactly how a wrong-direction leftover link should be fixed, are both
still open -- see leftover_direction/full_plate below).

Two halves:

- check_week(plan_entries, context) is the pure rule engine. Given a plain
  list of entry dicts and a small context dict (see their shapes below),
  it returns a list of Violation records -- no I/O, no model calls, and
  fully unit-testable against hand-written fixtures (see
  tests/test_plan_quality.py) without touching a database.

- check_and_log(plan_id, generation_context) is the thin, NOT pure,
  wrapper that _finish_week_slots actually calls, once, as the very last
  thing it does. It reads the just-written plan back out of the database
  (joined with recipes, since a model reusing a saved recipe by name isn't
  required to re-state its prep/cook time or main_protein -- the saved
  row already has that, and is more reliable than trusting the model's own
  restatement of data it was already handed), builds the small context
  check_week wants from the SAME generation context that was handed to the
  model for this plan (not a fresh query -- see its docstring for why that
  distinction matters), runs check_week, and logs whatever it finds at
  WARNING. Nothing about the plan changes.

plan_entries shape (what check_week expects, one dict per meal_plan_entries
row already scoped to one plan):
    {
        "date": "2026-09-07",             # ISO date
        "slot": "dinner",                 # breakfast | lunch | dinner | snack
        "slot_state": "planned",          # planned | planned_empty | open
        "meal_name": "Chili" | None,
        "reasoning": "...",
        "food_groups": ["protein", "carb"],
        "main_protein": "beef" | None,
        "prep_time_minutes": 10 | None,
        "cook_time_minutes": 20 | None,
        "is_new_recipe": False,
        "links_to": "2026-09-02:dinner" | None,
    }

context shape (what check_week expects -- distinct from the larger
generation context check_and_log is handed; see check_and_log for how one
becomes the other):
    {
        "rush_max_minutes": 20,
        "rush_dates": {"2026-09-10"},      # dates tagged `rush` this week
        "weeknight_max_minutes": 30 | None | 0,
        "recent_history": [
            {"date": ..., "slot": ..., "meal": ..., "cuisine": ...,
             "main_protein": ..., "rating": ...},
            ...
        ],
    }

No record of a violation count is kept anywhere persistent. There's no
obvious cheap existing place for one: weekly_plans has no free-form/count
column to reuse, and api_calls' schema is tokens-and-timing only, not a
fit for a plan-quality count. Logging is the whole of it for this pass --
a future pass could add a column if this turns out to need one.
"""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass

from ..db import get_conn
from ._shared import household_id
from .week_intake import RUSH_MAX_MINUTES

logger = logging.getLogger("home_manager")

# Generic filler the generation prompt explicitly tells the model to avoid
# (see the `reasoning` bullet in generate_weekly_plan_llm's instructions).
# Deliberately does NOT include "it fit the week" -- that exact phrase is
# what the SAME prompt tells the model to say when there's honestly
# nothing more specific, so flagging it here would punish the model for
# following instructions.
_BANNED_REASONING_PHRASES = {
    "a balanced, tasty option",
    "a balanced choice",
    "a balanced option",
    "a balanced meal",
    "a great meal",
    "a good meal",
    "a great option",
    "a good option",
    "a tasty option",
    "a nice option for tonight",
    "a delicious meal",
}

_WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str  # "warn" | "info"
    date: str | None
    slot: str | None
    message: str


def _weekday_name(iso_date: str) -> str:
    return datetime.date.fromisoformat(iso_date).strftime("%A")


def _minutes(entry: dict) -> int | None:
    prep, cook = entry.get("prep_time_minutes"), entry.get("cook_time_minutes")
    if prep is None and cook is None:
        return None
    return (prep or 0) + (cook or 0)


def _is_planned(entry: dict) -> bool:
    return entry.get("slot_state", "planned") == "planned" and entry.get("meal_name")


def _rush_cap_respected(entries: list[dict], context: dict) -> list[Violation]:
    rush_dates = context.get("rush_dates") or set()
    rush_max = context.get("rush_max_minutes", RUSH_MAX_MINUTES)
    violations = []
    for entry in entries:
        if entry.get("slot") != "dinner" or not _is_planned(entry):
            continue
        if entry["date"] not in rush_dates:
            continue
        total = _minutes(entry)
        if total is not None and total > rush_max:
            violations.append(Violation(
                rule="rush_cap_respected", severity="warn",
                date=entry["date"], slot="dinner",
                message=(
                    f"{entry['date']} was tagged `rush` (capped at {rush_max} minutes) but "
                    f"'{entry['meal_name']}' comes to {total} minutes of prep+cook."
                ),
            ))
    return violations


def _weeknight_cap_respected(entries: list[dict], context: dict) -> list[Violation]:
    cap = context.get("weeknight_max_minutes")
    if not cap:
        return []
    rush_dates = context.get("rush_dates") or set()
    violations = []
    for entry in entries:
        if entry.get("slot") != "dinner" or not _is_planned(entry):
            continue
        if entry["date"] in rush_dates:
            continue  # already checked, at a stricter cap, by _rush_cap_respected
        if _weekday_name(entry["date"]) not in _WEEKDAYS:
            continue
        total = _minutes(entry)
        if total is not None and total > cap:
            violations.append(Violation(
                rule="weeknight_cap_respected", severity="warn",
                date=entry["date"], slot="dinner",
                message=(
                    f"{entry['date']} is a weeknight (household cap {cap} minutes) but "
                    f"'{entry['meal_name']}' comes to {total} minutes of prep+cook."
                ),
            ))
    return violations


def _no_protein_run(entries: list[dict], context: dict) -> list[Violation]:
    dinners = sorted(
        (e for e in entries if e.get("slot") == "dinner" and _is_planned(e) and e.get("main_protein")),
        key=lambda e: e["date"],
    )
    violations = []
    run: list[dict] = []
    for entry in dinners:
        if run:
            prev_date = datetime.date.fromisoformat(run[-1]["date"])
            this_date = datetime.date.fromisoformat(entry["date"])
            consecutive = (this_date - prev_date).days == 1
            same_protein = entry["main_protein"].strip().lower() == run[-1]["main_protein"].strip().lower()
        else:
            consecutive = same_protein = False
        if consecutive and same_protein:
            run.append(entry)
        else:
            if len(run) >= 3:
                violations.append(_protein_run_violation(run))
            run = [entry]
    if len(run) >= 3:
        violations.append(_protein_run_violation(run))
    return violations


def _protein_run_violation(run: list[dict]) -> Violation:
    return Violation(
        rule="no_protein_run", severity="warn",
        date=run[-1]["date"], slot="dinner",
        message=(
            f"{run[0]['main_protein']} is the dinner main_protein on {len(run)} consecutive nights "
            f"({run[0]['date']} through {run[-1]['date']})."
        ),
    )


def _dinner_repeat_in_history(entries: list[dict], context: dict) -> list[Violation]:
    history_names = {
        (h.get("meal") or "").strip().lower()
        for h in (context.get("recent_history") or [])
        if h.get("slot") == "dinner" and h.get("meal")
    }
    violations = []
    for entry in entries:
        if entry.get("slot") != "dinner" or not _is_planned(entry):
            continue
        if entry["meal_name"].strip().lower() in history_names:
            violations.append(Violation(
                rule="dinner_repeat_in_history", severity="warn",
                date=entry["date"], slot="dinner",
                message=(
                    f"'{entry['meal_name']}' on {entry['date']} also appears in the last 3 weeks "
                    "of dinner history."
                ),
            ))
    return violations


def _reasoning_is_specific(entries: list[dict], context: dict) -> list[Violation]:
    violations = []
    for entry in entries:
        if not _is_planned(entry):
            continue
        reasoning = (entry.get("reasoning") or "").strip()
        if not reasoning:
            violations.append(Violation(
                rule="reasoning_is_specific", severity="warn",
                date=entry["date"], slot=entry.get("slot"),
                message=f"{entry['date']} {entry.get('slot')} ('{entry['meal_name']}') has no reasoning at all.",
            ))
        elif reasoning.strip().rstrip(".!?…").strip().lower() in _BANNED_REASONING_PHRASES:
            violations.append(Violation(
                rule="reasoning_is_specific", severity="warn",
                date=entry["date"], slot=entry.get("slot"),
                message=(
                    f"{entry['date']} {entry.get('slot')} ('{entry['meal_name']}') has generic "
                    f"filler reasoning: \"{reasoning}\"."
                ),
            ))
    return violations


def _novelty_floor(entries: list[dict], context: dict) -> list[Violation]:
    dinners = [e for e in entries if e.get("slot") == "dinner" and _is_planned(e)]
    if dinners and not any(e.get("is_new_recipe") for e in dinners):
        return [Violation(
            rule="novelty_floor", severity="warn", date=None, slot="dinner",
            message="No dinner this week is a new recipe -- the novelty floor is at least one.",
        )]
    return []


def _open_slot_budget(entries: list[dict], context: dict) -> list[Violation]:
    open_entries = [e for e in entries if e.get("slot_state") == "open"]
    violations = []
    for entry in open_entries:
        if entry.get("slot") in ("breakfast", "lunch"):
            violations.append(Violation(
                rule="open_slot_budget", severity="warn",
                date=entry["date"], slot=entry.get("slot"),
                message=f"{entry['date']} {entry.get('slot')} is open, but breakfast/lunch must never be.",
            ))
    if len(open_entries) > 1:
        violations.append(Violation(
            rule="open_slot_budget", severity="warn", date=None, slot=None,
            message=f"{len(open_entries)} open slots this week; the budget is at most 1.",
        ))
    return violations


def _leftover_direction(entries: list[dict], context: dict) -> list[Violation]:
    violations = []
    for entry in entries:
        links_to = entry.get("links_to")
        if not links_to or not _is_planned(entry):
            continue
        linked_date = links_to.split(":", 1)[0]
        try:
            ok = datetime.date.fromisoformat(linked_date) < datetime.date.fromisoformat(entry["date"])
        except ValueError:
            continue  # unparseable links_to isn't this rule's concern
        if not ok:
            violations.append(Violation(
                rule="leftover_direction", severity="warn",
                date=entry["date"], slot=entry.get("slot"),
                message=(
                    f"{entry['date']} {entry.get('slot')} ('{entry['meal_name']}') links_to "
                    f"'{links_to}', which is not earlier than the entry itself."
                ),
            ))
    return violations


def _full_plate(entries: list[dict], context: dict) -> list[Violation]:
    violations = []
    for entry in entries:
        if entry.get("slot") != "dinner" or not _is_planned(entry):
            continue
        food_groups = entry.get("food_groups") or []
        if not food_groups:
            continue  # no data recorded either way -- not this rule's business to guess
        missing = [g for g in ("protein", "vegetable") if g not in food_groups]
        if missing:
            violations.append(Violation(
                rule="full_plate", severity="info",
                date=entry["date"], slot="dinner",
                message=(
                    f"{entry['date']} dinner ('{entry['meal_name']}') food_groups is missing "
                    f"{' and '.join(missing)}."
                ),
            ))
    return violations


def check_week(plan_entries: list[dict], context: dict) -> list[Violation]:
    """
    Pure rule engine over an already-assembled week. Takes plain dicts
    rather than DB rows, precisely so a test can hand it a hand-written
    week without touching a database -- see the module docstring for the
    exact shapes expected. Rules are independent and additive: one firing
    never suppresses another, and their order here doesn't matter.
    """
    violations: list[Violation] = []
    violations += _rush_cap_respected(plan_entries, context)
    violations += _weeknight_cap_respected(plan_entries, context)
    violations += _no_protein_run(plan_entries, context)
    violations += _dinner_repeat_in_history(plan_entries, context)
    violations += _reasoning_is_specific(plan_entries, context)
    violations += _novelty_floor(plan_entries, context)
    violations += _open_slot_budget(plan_entries, context)
    violations += _leftover_direction(plan_entries, context)
    violations += _full_plate(plan_entries, context)
    return violations


def _load_plan_entries(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.date, mpe.slot, mpe.slot_state, mpe.reasoning, mpe.food_groups_json,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal_name,
               r.main_protein, r.prep_time_minutes, r.cook_time_minutes, r.times_cooked
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, mpe.slot ASC
        """,
        (plan_id, household_id()),
    ).fetchall()
    conn.close()

    entries = []
    for r in rows:
        derived_from = json.loads(r["derived_from_json"] or "{}")
        entries.append({
            "date": r["date"],
            "slot": r["slot"],
            "slot_state": r["slot_state"],
            "meal_name": r["meal_name"],
            "reasoning": r["reasoning"] or "",
            "food_groups": json.loads(r["food_groups_json"] or "[]"),
            "main_protein": r["main_protein"] or None,
            "prep_time_minutes": r["prep_time_minutes"],
            "cook_time_minutes": r["cook_time_minutes"],
            # times_cooked == 0 is true both for a recipe the model just
            # added this turn (add_recipe never sets times_cooked) and for
            # an older saved recipe nobody has actually cooked yet -- both
            # are real, uncooked novelty on the household's plate, which is
            # what this floor cares about. A freeform meal (no recipe row)
            # isn't a recipe at all, so it doesn't count either way.
            "is_new_recipe": r["times_cooked"] == 0 if r["times_cooked"] is not None else False,
            "links_to": derived_from.get("links_to"),
        })
    return entries


def check_and_log(plan_id: int, generation_context: dict) -> list[Violation]:
    """
    Read plan `plan_id` back from the database, run check_week against it,
    and log whatever it finds at WARNING. Called once, as the very last
    line of _finish_week_slots. Never raises: a plan that would otherwise
    generate fine must not fail because this optional, log-only check
    couldn't be computed, and never repairs or otherwise changes the plan.

    `generation_context` is the SAME context dict that was built for and
    handed to the model when this plan was generated -- specifically its
    `recent_history`, `household_memory`, and `intake` keys. That matters:
    by the time this runs, this plan's own entries are already written to
    the database, so a fresh call to get_recent_meal_history here (which
    has no upper date bound by design) would see this plan's own dinners
    as "recent history" and flag every one of them as a repeat of itself.
    Reusing the pre-generation snapshot is what the model actually saw,
    which is the only thing worth checking it against.
    """
    try:
        entries = _load_plan_entries(plan_id)
        intake_ctx = generation_context.get("intake") or {}
        night_tags = intake_ctx.get("night_tags") or {}
        quality_context = {
            "rush_max_minutes": RUSH_MAX_MINUTES,
            "rush_dates": {d for d, tags in night_tags.items() if "rush" in tags},
            "weeknight_max_minutes": (generation_context.get("household_memory") or {}).get(
                "weeknight_max_minutes"
            ),
            "recent_history": generation_context.get("recent_history") or [],
        }
        violations = check_week(entries, quality_context)
        for v in violations:
            logger.warning(
                "Plan %s quality [%s/%s]%s%s: %s",
                plan_id, v.rule, v.severity,
                f" {v.date}" if v.date else "", f" {v.slot}" if v.slot else "",
                v.message,
            )
        if violations:
            logger.warning(
                "Plan %s came back with %d quality violation(s) logged above. Log-only: "
                "nothing about the plan was changed.",
                plan_id, len(violations),
            )
        return violations
    except Exception:
        logger.exception(
            "Plan quality check failed for plan %s; the plan itself is unaffected", plan_id
        )
        return []
