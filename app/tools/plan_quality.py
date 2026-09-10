"""
Deterministic quality checks for a generated week — log-and-warn only,
with ONE exception, named below.

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
was built against. This was a first pass at closing that gap WITHOUT
changing behaviour: with the one exception named below, it only observes
and logs. It does not repair anything else, does not otherwise touch the
plan, and does not decide anything Emily hasn't decided yet (the "does
every dinner need a vegetable" plate rule, and exactly how a
wrong-direction leftover link should be fixed, are both still open -- see
leftover_direction/full_plate below).

The exception, and the only thing in here that writes: repair_snack_clashes
(2026-09-08, Julia's "same recommendation for breakfast and for snack on
the same day"). A snack that repeats something else eaten that day is traded
onto a day where it doesn't — or, when it fits nowhere, gives its slot to
another day's snack. Either way the week's OWN snacks are the whole
supply, so nothing reaches the plan that generation's restriction,
dislike and allergy handling never saw. It runs from _finish_week_slots
immediately before check_and_log, so the log reports only what could not
be fixed. Everything else here still observes and changes nothing.

Two halves (three, with the repair):

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
        "ingredients": [{"item": "Bell peppers", "category": "produce"}, ...],
        "instructions": ["Preheat the oven to 400F...", ...],
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
        "household_asks": "we're on a pepper kick",  # their own words, lowercased
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
import re
from dataclasses import dataclass

from ..db import get_conn
from . import recipes as _recipes
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

# All seven, lowercased — a different question from _WEEKDAYS above (which
# is "is this a weeknight?"): whether a line of copy names a day at all.
_WEEKDAY_NAMES = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)

# How many dinners one fresh ingredient may turn up in before it stops
# being a coincidence and starts being a shopping-list problem. Three of
# seven is a household that likes peppers; five is Emily's week, and
# "17 bell peppers" on the list.
INGREDIENT_REPEAT_MAX_DINNERS = 3

# The grocery-list sections that hold things bought fresh and used up.
# Pantry/frozen/other are excluded on purpose: rice in four dinners is
# not the problem this rule is looking for.
_FRESH_CATEGORIES = {"produce", "dairy"}

# The fresh things that quietly go into everything, where appearing every
# night is correct rather than repetitive. Deliberately a SHORT list a
# person can argue with, in the same spirit as _ALLERGEN_ALIASES in
# coordination.py — extend it when a real false positive shows up, rather
# than trying to infer "staple-ness". Matched on whole words, so "yellow
# onion" and "spring onion" are both covered by "onion".
#
# Salt, pepper and oil are deliberately NOT here even though they are the
# obvious staples: they are pantry, so they never reach this rule at all,
# and "pepper" sitting in this set would quietly make "Bell pepper" — the
# whole reason the rule exists — exempt from it.
_STAPLE_FRESH_WORDS = {
    "onion", "onions", "garlic", "shallot", "shallots", "ginger", "butter",
}

# Words in a dish name that say nothing about WHAT it is, so two names
# sharing only these are not the same food. Short and arguable on purpose,
# same spirit as _STAPLE_FRESH_WORDS above — extend it when a real false
# positive shows up rather than trying to infer meaning.
_NAME_STOPWORDS = {
    "a", "an", "and", "the", "of", "on", "in", "with", "plus", "side", "sides",
    "homemade", "fresh", "quick", "easy", "simple", "little", "mini", "small",
    "big", "half", "slice", "sliced", "cup", "bowl", "plate", "board", "bite",
    "topped", "served", "style", "our", "your", "some", "warm", "cold", "hot",
}


def _stem(word: str) -> str:
    """
    Crude singular form, enough to see that "pancakes" and "pancake", or
    "berries" and "berry", are the same food. Not a real stemmer, and it
    doesn't need to be: it only ever compares two dish names to each other.
    """
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word.endswith(("shes", "ches", "xes", "ses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _name_stems(name: str | None) -> set[str]:
    """The identifying words of a dish name, singularized and stripped of
    the words every dish name has."""
    words = re.findall(r"[a-z]+", (name or "").lower())
    return {_stem(w) for w in words if w not in _NAME_STOPWORDS and len(w) > 2} - _NAME_STOPWORDS


def shared_food(name_a: str | None, name_b: str | None) -> str | None:
    """
    The food two dish names have in common, or None. "Banana pancakes" and
    "Banana with peanut butter" share `banana`; "Oatmeal" and "Yogurt with
    berries" share nothing.

    Julia, first beta tester, 2026-09-08: the planner "gave the same
    recommendation for breakfast and for snack on the same day, or super
    similar ones." A name-stem match is a deliberately blunt instrument —
    it can't see that a smoothie and a parfait are both yogurt — but it
    catches the case she actually hit, which is the same word showing up
    twice on one day, and it never needs a model call to do it.
    """
    shared = sorted(_name_stems(name_a) & _name_stems(name_b))
    return shared[0] if shared else None


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


def _is_staple(name: str) -> bool:
    """A fresh ingredient that goes in everything. Whole words, so "yellow
    onion" and "spring onion" are both onions and "butternut squash" is
    not butter."""
    return bool(set(re.findall(r"[a-z]+", name)) & _STAPLE_FRESH_WORDS)


def _was_asked_for(name: str, asks: str) -> bool:
    """
    Whether the household actually asked for this ingredient — the item's
    name found as a PHRASE in whatever they wrote (this week's intake
    freeform, their standing notes, the week's constraints). Prose rather
    than a structured field, because "we're on a bell peppers kick" is
    typed into the freeform box and never into a schema.

    Known limit, and an acceptable one for a warn-only rule: this matches
    the ingredient's name as the recipe wrote it, so "peppers" in the
    freeform box does not reach an ingredient named "Bell peppers". The
    consequence of a miss is a log line nobody needed, not a bad week.
    """
    if not asks:
        return False
    return bool(re.search(rf"\b{re.escape(name)}\b", asks))


def _ingredient_repeat(entries: list[dict], context: dict) -> list[Violation]:
    """
    The same fresh ingredient in more than three of the week's dinners.

    Emily, on her first approved week: "a regular week for a family of 3
    shouldn't have 17 peppers, it's not normal." The seventeen were honest
    arithmetic over five pepper dinners out of seven — the generation
    prompt had variety rules for protein and for cuisine and none at all
    for an ingredient, so nothing was stopping the model reaching for the
    same vegetable all week and nothing downstream measured it. The prompt
    now carries that rule (see the variety bullet in
    generate_weekly_plan_llm); this is the half that checks whether the
    model listened.

    Warn-only, like everything else here. A leftovers night is skipped —
    it eats an earlier night's cooking, so counting it would charge the
    ingredient twice for one pot.
    """
    asks = (context.get("household_asks") or "").lower()
    dates_by_ingredient: dict[str, dict] = {}
    for entry in entries:
        if entry.get("slot") != "dinner" or not _is_planned(entry):
            continue
        if entry.get("links_to"):
            continue
        for ing in entry.get("ingredients") or []:
            if (ing.get("category") or "").strip().lower() not in _FRESH_CATEGORIES:
                continue
            name = (ing.get("item") or "").strip().lower()
            if not name or _is_staple(name):
                continue
            seen = dates_by_ingredient.setdefault(name, {"display": ing["item"].strip(), "dates": set()})
            seen["dates"].add(entry["date"])
    violations = []
    for name, seen in sorted(dates_by_ingredient.items()):
        count = len(seen["dates"])
        if count <= INGREDIENT_REPEAT_MAX_DINNERS or _was_asked_for(name, asks):
            continue
        violations.append(Violation(
            rule="ingredient_repeat", severity="warn", date=None, slot="dinner",
            message=(
                f"{seen['display']} is in {count} of this week's dinners "
                f"({', '.join(sorted(seen['dates']))}); the cap is "
                f"{INGREDIENT_REPEAT_MAX_DINNERS} unless the household asked for it."
            ),
        ))
    return violations


def steps_ingredients_message(result: dict) -> str:
    """
    One sentence describing a failed recipes.check_steps_ingredients_
    consistency result. Shared with agent.fill_in_recipe, which runs the
    same check on a single recipe at fill time and logs it the same way —
    two callers, one wording.
    """
    parts = []
    if result.get("missing_from_list"):
        parts.append(
            "step(s) use " + ", ".join(result["missing_from_list"]) + ", which isn't on the ingredient list"
        )
    if result.get("unused_ingredients"):
        parts.append(
            ", ".join(result["unused_ingredients"]) + " never appear(s) in any step"
        )
    return "; ".join(parts)


def _steps_match_ingredients(entries: list[dict], context: dict) -> list[Violation]:
    """
    The method and the ingredient list have to describe the same dish.

    Julia, 2026-09-08: "Recipe generation quality is low. The dishes sound
    good, but the recipe details are not accurate." A step reaching for
    cream that nobody bought, or three ingredients on the list that no step
    ever touches, is that complaint in a form a machine can see — see
    recipes.check_steps_ingredients_consistency for how conservatively it
    looks (a known food word in a step is evidence; an unknown one is
    passed over). "info", not "warn": it is a soft signal about a recipe,
    not a broken rule about the week, and like everything here it only
    logs.
    """
    violations = []
    for entry in entries:
        if not _is_planned(entry) or not entry.get("instructions"):
            continue
        result = _recipes.check_steps_ingredients_consistency(
            entry.get("ingredients") or [], entry.get("instructions") or [],
        )
        if result["ok"]:
            continue
        violations.append(Violation(
            rule="steps_match_ingredients",
            severity="info",
            date=entry["date"],
            slot=entry["slot"],
            message=f"{entry['meal_name']}: {steps_ingredients_message(result)}.",
        ))
    return violations


def _by_date(entries: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Planned entries grouped as {date: {"snacks": [...], "meals": [...]}}."""
    days: dict[str, dict[str, list[dict]]] = {}
    for entry in entries:
        if not _is_planned(entry) or not entry.get("date"):
            continue
        day = days.setdefault(entry["date"], {"snacks": [], "meals": []})
        day["snacks" if entry.get("slot") == "snack" else "meals"].append(entry)
    return days


def snack_clashes(entries: list[dict]) -> list[dict]:
    """
    Every snack on the week that repeats something else eaten the same day
    — either one of that day's own meals, or the day's other snack.

    Returns one record per offending SNACK ({date, entry, food, other}),
    ordered by date, so the same computation can be logged as a violation
    and acted on as a repair (see repair_snack_clashes) without the two
    disagreeing about what is wrong.

    Only snacks are ever the offender: breakfast is what the household
    asked for, the snack is the thing the app chose to put beside it, so
    the snack is the one that moves.
    """
    found = []
    for date_str, day in sorted(_by_date(entries).items()):
        snacks = day["snacks"]
        for i, snack in enumerate(snacks):
            others = day["meals"] + snacks[:i]
            for other in others:
                food = shared_food(snack.get("meal_name"), other.get("meal_name"))
                if food:
                    found.append({"date": date_str, "entry": snack, "food": food, "other": other})
                    break
    return found


def _snack_variety(entries: list[dict], context: dict) -> list[Violation]:
    violations = []
    for clash in snack_clashes(entries):
        other, snack = clash["other"], clash["entry"]
        same_slot = other.get("slot") == "snack"
        what = "the day’s other snack" if same_slot else f"that day’s {other.get('slot')}"
        violations.append(Violation(
            rule="snacks_distinct_per_day" if same_slot else "snack_echoes_a_meal",
            severity="warn", date=clash["date"], slot="snack",
            message=(
                f"{clash['date']}: the snack '{snack.get('meal_name')}' repeats {what} "
                f"('{other.get('meal_name')}') — both are {clash['food']}."
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
    violations += _ingredient_repeat(plan_entries, context)
    violations += _steps_match_ingredients(plan_entries, context)
    violations += _snack_variety(plan_entries, context)
    return violations


def _load_plan_entries(plan_id: int) -> list[dict]:
    # Eating order (breakfast, lunch, dinner, snack), from
    # weekly_plan.slot_order_sql — `slot` is TEXT, so the plain
    # `ORDER BY mpe.slot` this used to end with sorted a day alphabetically
    # and put dinner before lunch. Most rules in here re-sort what they
    # need, but snack_clashes reads this order straight through: it names
    # the FIRST thing a snack repeats ("that day's lunch"), and
    # repair_snack_clashes then acts on that record. A day's own sequence
    # is the honest tiebreak there. The id last keeps two snacks on one day
    # in a stable order, since they tie on date and slot both. Imported
    # inside the function, the same way repair_snack_clashes reaches
    # weekly_plan below.
    from . import weekly_plan as _weekly_plan

    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.reasoning, mpe.food_groups_json,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal_name,
               r.main_protein, r.prep_time_minutes, r.cook_time_minutes, r.times_cooked,
               r.ingredients_json, r.instructions_json
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, {_weekly_plan.slot_order_sql('mpe.slot')} ASC, mpe.id ASC
        """,
        (plan_id, household_id()),
    ).fetchall()
    conn.close()

    entries = []
    for r in rows:
        derived_from = json.loads(r["derived_from_json"] or "{}")
        entries.append({
            # The row itself, so a repair can act on the exact entry a rule
            # objected to instead of matching it back by name.
            "entry_id": r["id"],
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
            # A freeform meal has no recipe row and therefore no ingredient
            # list — no data, which _ingredient_repeat treats as nothing to
            # count rather than as a clean week.
            "ingredients": json.loads(r["ingredients_json"] or "[]"),
            # For _steps_match_ingredients. A freeform meal has no recipe
            # row and so no steps — nothing to check rather than a clean
            # recipe, same as its empty ingredient list above.
            "instructions": json.loads(r["instructions_json"] or "[]"),
        })
    return entries


def _would_clash(snack: dict, day: dict, ignoring: dict | None = None) -> bool:
    """Whether `snack` repeats anything else planned on `day` (see
    _by_date's shape), optionally ignoring one entry — the snack currently
    sitting there, when asking whether a trade would work."""
    ignore_id = (ignoring or {}).get("entry_id")
    for other in day["meals"] + day["snacks"]:
        if other.get("entry_id") == snack.get("entry_id") or other.get("entry_id") == ignore_id:
            continue
        if shared_food(snack.get("meal_name"), other.get("meal_name")):
            return True
    return False


def repair_snack_clashes(plan_id: int) -> list[dict]:
    """
    Move a snack that repeats something else eaten the same day onto a day
    where it doesn't, trading it with that day's snack. Called from
    _finish_week_slots just before check_and_log, so the log below reports
    only what could NOT be fixed.

    A trade rather than an invention, deliberately. Every snack in this
    plan has already been through the restrictions, the dislikes, the
    allergy facts and the household's own asks; a replacement conjured from
    a hard-coded list here would have been through none of them, and
    "we fixed your repetitive snack by giving you one you're allergic to"
    is a far worse bug than the one being fixed. So the week's own snacks
    are the entire supply, and a clash with nothing to trade into is left
    alone and logged rather than papered over.

    Returns one record per snack actually moved. Never raises: like
    everything else in this module, a generated week must not fail over
    the quality pass.
    """
    from . import weekly_plan as _weekly_plan

    moved: list[dict] = []
    try:
        # One fix per pass, recomputed each time — a trade changes two days
        # at once, so the next clash has to be judged against the week as
        # it now stands. Bounded well above any real week's snack count, so
        # a rule and a repair that disagreed could not spin here forever.
        for _ in range(64):
            days = _by_date(_load_plan_entries(plan_id))
            clashes = snack_clashes([e for day in days.values() for e in day["meals"] + day["snacks"]])
            fix = _plan_a_fix(clashes, days)
            if not fix:
                break
            clash, candidate, kind = fix
            snack = clash["entry"]
            if kind == "trade":
                _swap_entry_dates(snack["entry_id"], candidate["entry_id"])
            else:
                _weekly_plan.swap_meal_in_plan(
                    plan_id, clash["date"], candidate["meal_name"], slot="snack",
                    old_meal=snack["meal_name"],
                    reasoning="something different from the rest of the day",
                )
            moved.append({
                "date": clash["date"], "was": snack["meal_name"],
                "now": candidate["meal_name"], "from": candidate["date"],
                "repeated": clash["food"], "kind": kind,
            })
            logger.info(
                "Plan %s snack repair (%s): '%s' repeated %s on %s, so %s took its place "
                "(from %s)",
                plan_id, kind, snack["meal_name"], clash["food"], clash["date"],
                candidate["meal_name"], candidate["date"],
            )
    except Exception:
        logger.exception(
            "Snack repair failed for plan %s; the plan itself is unaffected", plan_id
        )
    return moved


def _plan_a_fix(clashes: list[dict], days: dict) -> tuple[dict, dict, str] | None:
    """
    The first clash that can be fixed, as (clash, donor snack, kind).

    Two kinds, tried in that order for every clash before moving on to the
    next one:

    - "trade": the clashing snack and the donor change places. Preferred,
      because it keeps the week's snack mix exactly as generated — the
      same ideas, the same number of each, just on different days.
    - "copy": the donor's dish takes the clashing snack's place and the
      clashing one is dropped. Needed when the clashing snack fits nowhere
      else (a week whose every breakfast is oatmeal has no day the oatmeal
      cookies can move to), and cheap in what it costs: a snack idea
      appearing on one more day than planned is explicitly fine, where the
      same food twice in one day is the thing being fixed.

    Both draw only on snacks already in this week, so nothing reaches the
    plan that generation's restriction/dislike/allergy handling never saw.
    """
    for clash in clashes:
        snack, here = clash["entry"], days[clash["date"]]
        donors = [
            (date_str, candidate)
            for date_str, day in sorted(days.items()) if date_str != clash["date"]
            for candidate in day["snacks"]
            if not _would_clash(candidate, here, ignoring=snack)
        ]
        for date_str, candidate in donors:
            if not _would_clash(snack, days[date_str], ignoring=candidate):
                return clash, candidate, "trade"
        if donors:
            return clash, donors[0][1], "copy"
    return None


def _swap_entry_dates(entry_id_a: int, entry_id_b: int) -> None:
    """Exchange two entries' dates. Only ever called on two snacks of one
    plan (see repair_snack_clashes), where nothing else is keyed to the
    date yet — the week is still a draft at this point, so no grocery
    contribution and no prep task has been written against either row."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date FROM meal_plan_entries WHERE id IN (?, ?) AND household_id = ?",
        (entry_id_a, entry_id_b, household_id()),
    ).fetchall()
    dates = {r["id"]: r["date"] for r in rows}
    if len(dates) != 2:
        conn.close()
        raise ValueError(f"Can't trade entries {entry_id_a} and {entry_id_b} — one of them is gone.")
    for entry_id, new_date in ((entry_id_a, dates[entry_id_b]), (entry_id_b, dates[entry_id_a])):
        conn.execute(
            "UPDATE meal_plan_entries SET date = ? WHERE id = ? AND household_id = ?",
            (new_date, entry_id, household_id()),
        )
        # A reasoning that names a weekday is now naming the wrong one —
        # "a slot whose reasoning says Friday while sitting on Sunday is a
        # plan that lies about itself" (the generation prompt's own words).
        # Rare on a snack, and saying nothing beats saying something false.
        conn.execute(
            "UPDATE meal_plan_entries SET reasoning = '' WHERE id = ? AND household_id = ? "
            "AND reasoning IS NOT NULL AND (" + " OR ".join(
                "LOWER(reasoning) LIKE ?" for _ in _WEEKDAY_NAMES
            ) + ")",
            (entry_id, household_id(), *(f"%{day}%" for day in _WEEKDAY_NAMES)),
        )
    conn.commit()
    conn.close()


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
        memory = generation_context.get("household_memory") or {}
        quality_context = {
            "rush_max_minutes": RUSH_MAX_MINUTES,
            "rush_dates": {d for d, tags in night_tags.items() if "rush" in tags},
            "weeknight_max_minutes": memory.get("weeknight_max_minutes"),
            "recent_history": generation_context.get("recent_history") or [],
            # What the household said they wanted, in their own words, so
            # _ingredient_repeat can let a requested ingredient off. Their
            # prose, not a structured field, because "we're on a pepper
            # kick" is typed into the freeform box, never into a schema.
            "household_asks": " ".join(str(part) for part in (
                intake_ctx.get("freeform") or "",
                " ".join(intake_ctx.get("cuisines") or []),
                " ".join(intake_ctx.get("moods") or []),
                memory.get("notes") or "",
                generation_context.get("constraints_notes") or "",
            )).lower(),
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
