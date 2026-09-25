"""
Deterministic quality checks for a generated week — log-and-warn only,
with ONE exception, named below.

Scope: check_and_log is invoked from _finish_week_slots, which only the
DAY-BASED generation branch calls (pre-existing placement, agent.py). A
component-based household therefore gets no quality checking from this
module today -- that is a known gap, not coverage.

The generation prompt (see generate_weekly_plan_llm's instructions in
agent.py) tells the model a long list of rules -- a `rush` night is capped
at RUSH_MAX_MINUTES, a weeknight cap when the household has set one, a
weekday lunch cooked that day at WEEKDAY_LUNCH_MAX_MINUTES, don't
run the same main_protein three nights straight, don't repeat a dinner
already eaten inside the variety window (meal_variety.VARIETY_WINDOW_WEEKS),
write a real reason instead of
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
        "make_double_for": ["2026-09-03:lunch"] | None,  # a batch cook's later meals
        "ingredients": [{"item": "Bell peppers", "category": "produce"}, ...],
        "instructions": ["Preheat the oven to 400F...", ...],
        "default_servings": 4 | None,     # what the ingredient amounts are written for
    }

context shape (what check_week expects -- distinct from the larger
generation context check_and_log is handed; see check_and_log for how one
becomes the other):
    {
        "rush_max_minutes": 30,
        "rush_dates": {"2026-09-10"},      # dates tagged `rush` this week
        "unrushed_dates": {"2026-09-12"},  # dates tagged `unrushed`: no weeknight cap
        "weeknight_max_minutes": 30 | None | 0,
        "prep_days": [{"weekday": "sunday"}],  # rhythm.prep_days: no lunch cap that day
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
from . import usage as _usage
from ._shared import household_id
from .week_intake import RUSH_MAX_MINUTES
from . import time_caps as _time_caps
from . import weekday_lunches as _weekday_lunches
from .meal_variety import NO_REPEAT_SLOTS as _NO_REPEAT_SLOTS, variety_window_words as _variety_window_words

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
    # "oes" is here because "potatoes" and "tomatoes" are two of the
    # commonest words in a dinner title, and without it they stem to
    # "potatoe"/"tomatoe" and match nothing -- so whether a title agreed
    # with its own ingredient line came down to which plural each happened
    # to use. Found by review 2026-09-15; invisible to two rounds of
    # sweeping because every potato and tomato in both corpora was spelled
    # the way that happened to agree.
    if len(word) > 3 and word.endswith("es") and word.endswith(("shes", "ches", "xes", "ses", "oes")):
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
    weeknight_cap = context.get("weeknight_max_minutes") or 0
    violations = []
    for entry in entries:
        if entry.get("slot") != "dinner" or not _is_planned(entry):
            continue
        if entry["date"] not in rush_dates:
            continue
        # A lower weeknight cap stays the cap on a rush weeknight (Emily,
        # 2026-09-23 — the tag only tightens), and the weeknight rule
        # below skips rush nights, so this is the one place it's held.
        cap = rush_max
        if weeknight_cap and _weekday_name(entry["date"]) in _WEEKDAYS:
            cap = min(rush_max, weeknight_cap)
        total = _minutes(entry)
        if total is not None and total > cap:
            violations.append(Violation(
                rule="rush_cap_respected", severity="warn",
                date=entry["date"], slot="dinner",
                message=(
                    f"{entry['date']} was tagged `rush` (capped at {cap} minutes) but "
                    f"'{entry['meal_name']}' comes to {total} minutes of prep+cook."
                ),
            ))
    return violations


def _weeknight_cap_respected(entries: list[dict], context: dict) -> list[Violation]:
    cap = context.get("weeknight_max_minutes")
    if not cap:
        return []
    rush_dates = context.get("rush_dates") or set()
    unrushed_dates = context.get("unrushed_dates") or set()
    violations = []
    for entry in entries:
        if entry.get("slot") != "dinner" or not _is_planned(entry):
            continue
        if entry["date"] in rush_dates:
            continue  # already checked, at a stricter cap, by _rush_cap_respected
        if entry["date"] in unrushed_dates:
            continue  # the household lifted the cap for this night; a long dinner is the point
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


def _weekday_lunch_cap_respected(entries: list[dict], context: dict) -> list[Violation]:
    """
    Every Monday-Friday lunch cooked that day is WEEKDAY_LUNCH_MAX_MINUTES
    or less (Emily, 2026-09-23). A lunch that reheats an earlier cook, the
    batch cook other meals reheat, and a lunch on a prep day have no cap —
    time_caps.minutes_cap decides, the same rule the generator and the
    swap sheet use. Warn only, like the dinner caps above.
    """
    memory = {"rhythm": {"prep_days": context.get("prep_days") or []}}
    # How the week's intake says each weekday lunch is made (step 3,
    # 2026-09-25): "cooked" keeps the cap even on a prep day.
    kinds = context.get("lunch_kinds") or {}
    fed = {e.get("links_to") for e in entries if e.get("links_to")}
    violations = []
    for entry in entries:
        if entry.get("slot") != "lunch" or not _is_planned(entry):
            continue
        chained = bool(
            entry.get("links_to") or entry.get("make_double_for")
            or f"{entry['date']}:lunch" in fed
        )
        cap = _time_caps.minutes_cap(entry["date"], "lunch", [], memory, is_leftovers=chained,
                                     lunch_kind=kinds.get(entry["date"]))
        if not cap:
            continue
        total = _minutes(entry)
        if total is not None and total > cap:
            violations.append(Violation(
                rule="weekday_lunch_cap_respected", severity="warn",
                date=entry["date"], slot="lunch",
                message=(
                    f"{entry['date']} is a weekday lunch cooked that day (capped at {cap} minutes) "
                    f"but '{entry['meal_name']}' comes to {total} minutes of prep+cook."
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
    """
    A dinner or lunch the household ate inside the variety window.

    LUNCH as well as dinner since 2026-09-25: the prompt's own rule has
    always been "DINNER and LUNCH — not breakfast or snack"
    (meal_variety.NO_REPEAT_SLOTS), and this read only ever checked
    dinner, so a lunch back from last week was breached, drafted and
    reported by the opener with nothing here saying so. The comparison is
    within a slot — last week's dinner repeated as this week's lunch is a
    household using up a dish, not the rule being broken.

    Still warn-only, and since the same day it is mostly the report of a
    repair rather than of a repeat: meal_variety.repick_recent_repeats
    replaces what it can before this ever runs, so what is left here is
    the residue that pass names out loud — a dish they asked for, a night
    already cooked, a chain it could not move whole, a picker that found
    nothing better. Worth reading in the morning for exactly that reason.
    """
    history = [h for h in (context.get("recent_history") or []) if h.get("meal")]
    by_slot = {
        slot: {(h.get("meal") or "").strip().lower() for h in history if h.get("slot") == slot}
        for slot in _NO_REPEAT_SLOTS
    }
    violations = []
    for entry in entries:
        slot = entry.get("slot")
        if slot not in by_slot or not _is_planned(entry):
            continue
        if entry["meal_name"].strip().lower() in by_slot[slot]:
            violations.append(Violation(
                rule="dinner_repeat_in_history", severity="warn",
                date=entry["date"], slot=slot,
                message=(
                    f"'{entry['meal_name']}' on {entry['date']} also appears in {_variety_window_words()} "
                    f"of {slot} history."
                ),
            ))
    return violations


def _reasoning_is_specific(entries: list[dict], context: dict) -> list[Violation]:
    violations = []
    for entry in entries:
        if not _is_planned(entry):
            continue
        reasoning = (entry.get("reasoning") or "").strip()
        # A leftovers night's reason is its cook: the batch the fold wrote
        # (2026-09-23) carries none of its own, and its headline ("Leftovers
        # — Monday's Pasta") says why it's there. Not a missing reason.
        if not reasoning and (entry.get("links_to") or (entry.get("meal_name") or "").startswith("Leftovers")):
            continue
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


def _quantities_plausible(entries: list[dict], context: dict) -> list[Violation]:
    """
    The amounts have to make sense for the number of people.

    Emily, 2026-09-13, Turkish-Style Lentil Soup for two: "One stick of
    butter is a crazy amount for this whole recipe." The cook view now
    corrects a line like that on its own (recipes.plausible_cooking_
    quantity — a stick in a two-person soup shows as 1 tbsp), so this is
    the flag half of that fix: it reports the line as the model WROTE it
    (as_written — the saved cook_qty is the correction, not the
    complaint) and what the cook view shows instead. "info", like
    _steps_match_ingredients: a soft signal about a recipe, logged and
    put in the morning report, never a change to the week.
    """
    violations = []
    for entry in entries:
        if not _is_planned(entry) or not entry.get("ingredients"):
            continue
        servings = entry.get("default_servings")
        lines = _recipes._implausible_lines(entry["ingredients"], servings, as_written=True)
        if not lines:
            continue
        violations.append(Violation(
            rule="quantities_plausible",
            severity="info",
            date=entry["date"],
            slot=entry["slot"],
            message=f"{entry['meal_name']}: " + "; ".join(
                _recipes.implausible_quantity_message(line, servings) for line in lines
            ) + ".",
        ))
    return violations


def _produce_variety_named(entries: list[dict], context: dict) -> list[Violation]:
    """
    A count of produce says which kind when the count depends on it.

    Emily, 2026-09-13, on the grocery list: "It says 6 cucumbers - does it
    mean the persian cucumbers? Because that makes sense, but 6 english
    cucumbers would be a crazy amount." The generation prompt now asks for
    the variety whenever the count only makes sense for one; this is the
    catch for the times the model forgets, on the handful of produce
    where the ordinary kind and a small kind are both bought by the count
    (recipes._PRODUCE_COUNT_PER_SERVING). Nothing is rewritten — "6
    cucumbers" was very likely six Persian ones, and only the model knows
    — so this is "info", like _quantities_plausible: a line in the morning
    report, never a change to the week or the list.
    """
    violations = []
    for entry in entries:
        if not _is_planned(entry) or not entry.get("ingredients"):
            continue
        servings = entry.get("default_servings")
        clauses = []
        for ing in entry["ingredients"]:
            if not isinstance(ing, dict):
                continue
            item, qty = (ing.get("item") or "").strip(), (ing.get("qty") or "").strip()
            problem = _recipes.produce_count_problem(item, qty, servings)
            if problem:
                clauses.append(_recipes.produce_count_message(item, qty, problem, servings))
        if not clauses:
            continue
        violations.append(Violation(
            rule="produce_variety_named",
            severity="info",
            date=entry["date"],
            slot=entry["slot"],
            message=f"{entry['meal_name']}: " + "; ".join(clauses) + ".",
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


# ---------------------------------------------------------------------------
# The food-quality floor (route 4, Emily 2026-09-10).
#
# Route 1 told the planner what good food is. These three are the floor under
# it: not "is this dish good", which no amount of string matching can answer,
# but "did something obviously fall out". Each one exists because a real row
# in a real household database failed it.
#
# All three are deliberately narrow. A check that fires on a decent dinner is
# worse than no check, because the morning report is the one place breakage is
# supposed to be legible — so each looks for an absence it can be sure about
# and passes over everything it cannot.
# ---------------------------------------------------------------------------

# "Pineapple-Free Fruit Cup". Both planner prompts forbid naming a dish for
# what it leaves out, in those words, with that exact example. It shipped
# anyway, and nothing downstream looked.
#
# The captured group is the thing said to be ABSENT — "pineapple" in
# "Pineapple-Free", "knead" in "No-Knead". Matching the shape is only half the
# test; see _dish_named_for_an_absence for the half that decides.
_ABSENCE_IN_NAME = re.compile(
    r"\b(?:"
    r"([a-z]+)[- ]free"        # pineapple-free, dairy free
    r"|no[- ]([a-z]+)"         # no-knead, no-bake
    r"|without[- ]([a-z]+)"    # without dairy
    r"|([a-z]{3,})less"        # flourless, crustless
    r")\b",
    re.I,
)


def _avoided_words(context: dict) -> set:
    """Every food this household actually avoids, as lowercase words.

    Built in check_and_log from their member dietary restrictions, their
    dislikes, and any fact flagged hard — the same three sources the
    generation prompt treats as must-avoids.
    """
    out = set()
    for phrase in context.get("avoided") or []:
        out |= set(re.findall(r"[a-z]+", str(phrase).lower()))
    return out - {"a", "an", "the", "is", "are", "to", "no", "not", "free", "and", "or"}


def _dish_named_for_an_absence(entries: list[dict], context: dict) -> list[Violation]:
    """A dish must not be named after an ingredient THIS HOUSEHOLD avoids.

    The prompt's own words: 'Never name a dish after an ingredient it leaves
    out. No "Pineapple-Free Fried Rice", no "Nut-Free Brownies" — the name
    should describe what the dish IS.' And the reason it gives is the whole
    test: 'a meal named after an allergen is alarming to read on the week's
    menu even when the recipe is safe.'

    So the absent thing has to be one of THEIR allergens or must-avoids. The
    first version of this check matched the SHAPE alone and fired on
    "No-Knead Bread", "Flourless Chocolate Cake", "Crustless Quiche" and —
    memorably — "Timeless Tiramisu", none of which name an allergen and none
    of which alarm anyone. That is precisely the noise this module's own
    docstring warns is worse than no check at all.

    With no avoid-list in the context it fires on nothing. Silence is the
    right failure here: a check that cannot tell is a check that should not
    speak.
    """
    avoided = _avoided_words(context)
    if not avoided:
        return []
    violations = []
    for entry in entries:
        if not _is_planned(entry):
            continue
        name = entry.get("meal_name") or ""
        for match in _ABSENCE_IN_NAME.finditer(name):
            missing = next((g for g in match.groups() if g), "").lower()
            if not missing or missing not in avoided:
                continue
            violations.append(Violation(
                rule="dish_named_for_an_absence", severity="warn",
                date=entry.get("date"), slot=entry.get("slot"),
                message=(
                    f"'{name}' is named after {missing}, which this household avoids. "
                    "The name should say what the dish is."
                ),
            ))
            break
    return violations


# ---------------------------------------------------------------------------
# A title must not promise an ingredient the recipe hasn't got.
#
# Emily, 2026-09-14, standing at the stove: "Seared Turkey and Zucchini
# Skillet with White Beans" — seven ingredients, seven steps, and not a
# bean anywhere in either. The title and the ingredient list come out of
# ONE model call (generate_weekly_plan_llm), so nothing downstream had ever
# compared the two halves of the same answer to each other. The cook is
# left wondering what they missed, and the shopping list bought a dish the
# name doesn't describe.
#
# Unlike everything else in this module the fix REPAIRS rather than logs,
# because a wrong title is read by a person every time the dish comes round
# — and it repairs by correcting the NAME, never by inventing an
# ingredient. A recipe whose groceries have already shipped must not grow a
# tin of beans nobody bought; "Seared Turkey and Zucchini Skillet" is the
# honest name of what is actually in the pan.
#
# The rule is deliberately hard to trip. Its whole risk is the one the
# allergy work wrote down on 2026-09-04: a check that fires on good dinners
# is one people learn to click past. So it only ever looks at a TRAILING
# "with ..." clause, and it passes over anything it cannot be sure about.
# ---------------------------------------------------------------------------

# The first "with" in the title, and everything after it. An "and" clause
# was considered and left out on purpose: "Mac and Cheese", "Surf and
# Turf", "Beef and Broccoli Stir-Fry" all join two halves of ONE name
# rather than adding a side, and there is no string test that tells those
# from "Chicken and Dumplings" with no dumplings in it. Missing those is
# cheap; rewriting "Mac and Cheese" to "Mac" is not.
_TITLE_WITH_CLAUSE = re.compile(r"^(?P<head>.+?)\s+with\s+(?P<clause>\S.*)$", re.I)

# Words in a clause that describe HOW a thing was done to, not WHAT it is.
# Dropped before the clause is checked, so "with Roasted Asparagus" is
# still a promise of asparagus and "with White Beans" is still a promise of
# beans. Raw words, not stems, so the plural "greens" below can mean
# something different from the adjective "green".
_TITLE_MODIFIERS = {
    "roasted", "roast", "grilled", "griddled", "seared", "charred", "blistered",
    "crispy", "creamy", "toasted", "whipped", "smashed", "mashed",
    "braised", "pickled", "marinated", "fried", "baked", "steamed", "sauteed",
    "sautéed", "caramelized", "caramelised", "shaved", "torn", "melted",
    "broiled", "poached", "smoked", "cured", "chopped", "shredded", "wilted",
    "herbed", "buttered", "spiced", "seasoned", "dressed", "glazed", "candied",
    "white", "green", "red", "brown", "black", "yellow", "golden", "purple",
    "wild", "sweet", "savoury", "savory", "spicy", "tangy", "zesty", "smoky",
    "herby", "garlicky", "buttery", "cheesy", "lemony", "nutty", "crunchy",
    "baby", "extra", "double", "loaded", "all", "whole", "chunky", "thin",
    "thick", "deep", "pan", "sheet", "one", "two", "slow", "fast", "soft",
    "hearty", "light", "rich", "bright", "tender", "juicy", "perfect",
    "crusty", "fluffy", "silky", "sticky", "charcoal", "flame", "oven",
}

# Words that name a CATEGORY or a preparation rather than a thing you buy.
# When one of these turns up, the clause is passed over whole: "with Garlic
# Butter Sauce" is a sauce made of things already in the pan, "with
# Everything Seasoning" is a spice jar, "with Roasted Root Vegetables" is
# whatever vegetables are in the list. None of them can be checked against
# an ingredient line, and guessing is how this rule would start rewriting
# good dinners. Raw words — both forms are listed where both are used.
_TITLE_VAGUE_WORDS = {
    "vegetable", "vegetables", "veggie", "veggies", "veg", "greens", "salad",
    "salads", "slaw", "sauce", "sauces", "dressing", "gravy", "seasoning",
    "seasonings", "spice", "spices", "herb", "herbs", "topping", "toppings",
    "top", "garnish", "garnishes", "crumb", "crumbs", "crust", "glaze",
    "marinade", "drizzle", "relish", "pesto", "aioli", "salsa", "chutney",
    "dip", "dips", "stuffing", "trimmings", "fixings", "everything", "extras",
    "fruit", "fruits", "grain", "grains", "protein", "carb", "carbs", "starch",
    "broth", "stock", "medley", "mix", "blend", "mash", "puree", "hash",
    "crunch", "finish", "skin", "seasonal", "leftovers", "filling", "batter",
    "dough", "syrup", "seeds", "sprinkle", "shavings", "ribbons", "wedges",
    # Not a category word — a word that means two different foods depending
    # on who wrote it. British "chips" are American "fries"; American
    # "chips" are British "crisps". No table reconciles that, so the rule
    # cannot say what "with Chips" promises and must not guess: review
    # 2026-09-15 found it renaming "Battered Cod with Chips" over a list
    # saying French fries.
    "chip", "chips", "fries", "crisp", "crisps",
    # Named preparations — a thing made FROM other things, which is why the
    # ingredient list says yogurt and cucumber where the title says
    # tzatziki. Found by sweeping plausible generated titles: "Chicken
    # Souvlaki with Tzatziki" and "Turkey Meatballs with Marinara" were the
    # only two good dinners the rule touched, and both are this shape.
    # A few of these (hummus, guacamole) really are bought in a tub, so
    # listing them costs a miss — which is the cheap direction.
    "marinara", "tzatziki", "hummus", "guacamole", "raita", "tapenade",
    "chimichurri", "romesco", "gremolata", "bechamel", "hollandaise",
    "vinaigrette", "ragu", "bolognese", "alfredo", "remoulade", "compote",
    "coulis", "custard", "ganache", "frosting", "icing", "caramel", "curd",
}

# The short, arguable list of "this word is satisfied by that one", in the
# same spirit as _ALLERGEN_ALIASES in coordination.py — a table a person can
# read and extend when a real miss shows up, rather than a heuristic nobody
# can see. Every entry makes the rule QUIETER, never louder: it can only
# turn a would-be flag into a pass — which is true only because these words
# are deliberately kept OUT of _known_food_words (see it). Keys and values
# are stemmed on the way in, so an entry written in a form that stems to
# something else cannot sit here doing nothing.
_TITLE_INGREDIENT_ALIASES = {
    "bean": {"cannellini", "borlotti", "butterbean", "chickpea", "garbanzo", "edamame", "legume", "lentil"},
    "pea": {"chickpea", "garbanzo", "edamame", "mangetout"},
    "crouton": {"bread", "baguette", "sourdough", "ciabatta", "loaf", "brioche", "focaccia"},
    # Baked alongside, from a meal or a flour the list does name.
    "cornbread": {"cornmeal", "polenta", "flour"},
    "dumpling": {"flour", "suet", "wonton", "gyoza"},
    "biscuit": {"flour", "buttermilk"},
    "breadcrumb": {"bread", "panko", "baguette", "sourdough"},
    "bread": {"sourdough", "baguette", "ciabatta", "focaccia", "brioche", "loaf",
              "roll", "bun", "pita", "naan", "challah", "rye", "flour"},
    "rice": {"basmati", "jasmine", "arborio", "risotto"},
    "mac": {"macaroni", "pasta"},
    "pasta": {"spaghetti", "penne", "rigatoni", "macaroni", "fusilli", "linguine", "orzo",
              "farfalle", "noodle", "tagliatelle", "fettuccine", "ziti", "shell", "gnocchi",
              "lasagne", "lasagna", "cavatappi", "bucatini", "orecchiette"},
    "noodle": {"spaghetti", "linguine", "ramen", "udon", "soba", "vermicelli", "pasta"},
    "cheese": {"parmesan", "parmigiano", "cheddar", "mozzarella", "feta", "gruyere", "ricotta",
               "halloumi", "provolone", "pecorino", "manchego", "brie", "monterey", "queso",
               "paneer", "burrata", "mascarpone", "boursin", "cotija"},
    "potato": {"yukon", "russet", "fingerling"},
    "squash": {"zucchini", "courgette", "butternut", "acorn", "delicata"},
    "nut": {"almond", "walnut", "pecan", "cashew", "pistachio", "hazelnut", "peanut", "macadamia"},
    "berry": {"strawberry", "blueberry", "raspberry", "blackberry", "cranberry"},
    "yogurt": {"greek", "skyr", "yoghurt"},
    "yoghurt": {"greek", "skyr", "yogurt"},
    "chili": {"jalapeno", "serrano", "poblano", "chipotle", "habanero", "chile"},
    "chile": {"jalapeno", "serrano", "poblano", "chipotle", "habanero", "chili"},
    "tortilla": {"wrap", "taco"},
    "naan": {"flatbread", "roti", "chapati", "pita"},
    "chicken": {"thigh", "breast", "drumstick"},
    "beef": {"steak", "sirloin", "chuck", "brisket", "mince"},
    "pork": {"bacon", "sausage", "chorizo", "prosciutto", "pancetta", "ham"},
    "onion": {"shallot", "scallion", "leek"},
    # The same food in two Englishes. The app's own fixtures write both —
    # "Courgette" and "Rocket" appear as ingredients in this repo's tests
    # while the cooking-quantity table says zucchini and arugula — so
    # without these a household writing one and a recipe writing the other
    # reads as a broken promise. Review found five.
    "courgette": {"zucchini"},
    "aubergine": {"eggplant"},
    "coriander": {"cilantro"},
    "rocket": {"arugula"},
    "swede": {"rutabaga", "turnip"},
    "mangetout": {"pea"},
    "prawn": {"shrimp"},
    "mince": {"beef"},
    "beetroot": {"beet"},
    "capsicum": {"pepper"},
    "scallion": {"shallot", "leek", "onion"},
}

def _build_food_groups() -> dict:
    """Two words are the same food when they share a group.

    Symmetric on purpose, which is the second thing review caught: the
    table was read only one way, so every word listed as a SATISFIER was an
    unsatisfiable PROMISE. "Chicken with Orzo" over a list saying Pasta,
    "Curry with Basmati" over Rice, "Soup with Ciabatta" over Sourdough —
    eight wrong renames from one missing direction. Read as groups, orzo
    and pasta and sourdough and ciabatta all answer for each other.
    """
    groups: dict[str, set] = {}
    for key, family in _TITLE_INGREDIENT_ALIASES.items():
        # Stemmed on both sides. Lookups are stemmed, so an entry written in
        # a form that stems to something else is simply dead -- "fries"
        # stems to "fry" and never matched anything, which review found
        # because the DEAD half still made its words judgeable.
        whole = {_stem(w) for w in ({key} | set(family))}
        for word in whole:
            groups.setdefault(word, set()).update(whole)
    return {word: frozenset(family - {word}) for word, family in groups.items()}


_TITLE_FOOD_GROUPS = _build_food_groups()


def _same_food(stem: str) -> set:
    """Every word that means the same food as this one."""
    return _TITLE_FOOD_GROUPS.get(stem, frozenset())


# An ingredient list shorter than this is treated as no answer rather than
# as a short one. The model can be cut off mid-list, and the import paths
# can read a page badly; either way, rewriting a title against three lines
# that may be all there is of a list of ten is the one way this rule could
# do real damage.
_TITLE_MIN_INGREDIENTS = 3


# The two questions asked about every promised word, in this order: is it a
# food this app has a vocabulary for, and does the recipe have it?
#
# The FIRST question is the one an independent review forced (2026-09-15).
# The rule shipped with the opposite default — strip unless the word was on
# a list of category words — which is an open-ended blocklist against an
# open-ended world, and it lost badly: of 77 titles written by somebody who
# had not seen the lists, 27 were renamed wrongly. Almost all of one shape,
# and it is the shape this app's own prompt asks for (agent.py: "Every
# dinner plate carries a sauce, dressing, broth or spoonable something ...
# the chilli crisp, the herby green sauce, the pickled onions"): toum,
# mojo, chermoula, sofrito, ssamjang, zhoug, crema, pico de gallo, beurre
# blanc, aji verde, nuoc cham, ranch, chilli crisp. No blocklist bounds
# that tail, because the tail is every sauce in the world.
#
# So the default is inverted: a promise is only ever judged when every word
# of it is food this app already knows about, and everything else is left
# alone. That is the bias the grocery ingest already takes — "what cannot
# be compared stays on the list" — applied to a name instead of an amount.
#
# The vocabulary is ASSEMBLED FROM TABLES THIS APP ALREADY MAINTAINS for
# other reasons, never hand-written here. That matters more than its size:
# a list invented for this rule would rot, and every word added to it would
# make the rule louder with nobody watching. These grow as the app grows,
# and they were each written for a job that has its own tests.
_KNOWN_FOOD_WORDS: set | None = None


def _known_food_words() -> set:
    """Every word this app already treats as a food, stemmed.

    Six sources, all of them tables with another job: the cooking-quantity
    table (recipes.COOKING_QUANTITIES_PER_4, ~176 everyday items), the
    spice rack (spices._SPICES), the allergen families
    (coordination._ALLERGEN_ALIASES), the perishable words the holiday shop
    splits on (big_meal.PERISHABLE_WORDS), the staples sections'
    fridge/pantry words, and the produce-variety table. Plus this module's
    own alias groups, which are food by construction.

    Imported inside the function for the reason _allergen_title_words gives
    — several of these pull in the heavier half of the package.
    """
    global _KNOWN_FOOD_WORDS
    if _KNOWN_FOOD_WORDS is None:
        from . import big_meal as _big_meal
        from . import spices as _spices
        from . import staples as _staples
        from .coordination import _ALLERGEN_ALIASES
        phrases = set(_recipes.COOKING_QUANTITIES_PER_4)
        phrases |= set(_spices._SPICES)
        phrases |= set(_ALLERGEN_ALIASES) | {w for g in _ALLERGEN_ALIASES.values() for w in g}
        phrases |= set(_big_meal.PERISHABLE_WORDS)
        phrases |= set(_staples._FRIDGE_WORDS) | set(_staples._PANTRY_WORDS) | set(_staples._PANTRY_PHRASES)
        phrases |= {row[0] for row in _recipes._PRODUCE_COUNT_PER_SERVING}
        phrases |= {w for row in _recipes._PRODUCE_COUNT_PER_SERVING for w in row[1]}
        # NOT the alias table, and this is the correction review forced: 91
        # of the words here used to come from it, so ADDING AN ALIAS MADE
        # THE RULE LOUDER — it made that word judgeable — which is the exact
        # opposite of what the table's own comment promises. Out of the
        # vocabulary, an alias can only ever help a known word find its
        # match, which is the one direction it is meant to work in. The cost
        # is that a clause naming only an alias word (orzo, courgette,
        # chorizo) is never judged at all. A miss, and the cheap direction.
        _KNOWN_FOOD_WORDS = {
            _stem(w) for phrase in phrases for w in re.findall(r"[a-z]+", str(phrase).lower()) if len(w) > 2
        }
    return _KNOWN_FOOD_WORDS


def _head_says_something(text: str) -> bool:
    """Whether what is left of a title once its clause goes still names the
    dish.

    NOT _title_content_stems, which returns None on the first vague word
    and means "I can't read this as a promise" — a different question.
    Using it here detected every head containing salad, slaw, hash, ragu,
    mash, mix, medley or stuffing, so "Lentil Salad with Feta", "Pork Ragu
    with Peas" and "Beef Hash with Mushrooms" were reported and could never
    be corrected — permanent lines in the morning report, on an extremely
    common shape of generated title. Found by review 2026-09-15.

    "Salad with Feta" is still refused, and should be: "Salad" on its own
    says nothing, which is exactly what this asks.
    """
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if len(word) <= 2 or word in _TITLE_VAGUE_WORDS:
            continue
        if word in _TITLE_MODIFIERS or word in _NAME_STOPWORDS:
            continue
        stem = _stem(word)
        if stem in _TITLE_VAGUE_WORDS or stem in _NAME_STOPWORDS:
            continue
        return True
    return False


def _title_content_stems(text: str) -> list[str] | None:
    """The things a clause actually promises, or None when it promises
    nothing this rule can check.

    None covers two cases that must behave identically: a clause with
    nothing concrete left in it once the adjectives go ("with a Crispy
    Top"), and a clause naming a category rather than a food ("with
    Roasted Root Vegetables"). Both mean "can't tell", and can't-tell
    passes.
    """
    stems = []
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if len(word) <= 2:
            continue
        if word in _TITLE_VAGUE_WORDS:
            return None
        if word in _TITLE_MODIFIERS or word in _NAME_STOPWORDS:
            continue
        stem = _stem(word)
        if stem in _TITLE_VAGUE_WORDS:
            return None
        if stem in _NAME_STOPWORDS:
            continue
        stems.append(stem)
    return stems or None


def _recipe_word_pool(ingredients: list, instructions: list) -> set:
    """Every word the recipe itself says, stemmed.

    The METHOD counts as well as the ingredient list, and that is the
    conservative choice rather than a loose one: a dish whose steps really
    do simmer white beans has an ingredient list with a hole in it, which
    is _steps_match_ingredients' business — and renaming it would take a
    true title off a recipe to cover for a false list.
    """
    words = []
    for ing in ingredients or []:
        item = ing.get("item") if isinstance(ing, dict) else ing
        words += re.findall(r"[a-z]+", str(item or "").lower())
    for step in instructions or []:
        words += re.findall(r"[a-z]+", str(step or "").lower())
    return {_stem(w) for w in words if len(w) > 2}


_ALLERGEN_TITLE_WORDS: set | None = None


# The allergen families whose MEMBERS read as a warning rather than as
# food. Every family's own name is carved out (below) — "Nut-Free", "with
# Shellfish" — but only these three expand to their members, because the
# rest expand to words nobody reads as an allergen: dairy reaches butter,
# cheese, cream and milk; gluten reaches bread, pasta and flour; egg
# reaches mayonnaise. Carving all of those out took "Steak with Garlic
# Butter" off the rule entirely, which review caught as a missed control.
_ALARMING_ALLERGEN_FAMILIES = ("nut", "nuts", "shellfish", "sesame")
# Members of those families that are a DISH rather than the allergen's own
# name (2026-09-21, when the alias table grew them): "with Pesto" or "with
# Hummus" over a recipe without it is an ordinary title lie, not a warning,
# and reads as one to nobody. The matcher still reaches them; the
# title-correction rule simply doesn't treat them as sacred.
_DISH_NOT_A_WARNING = frozenset({"pesto", "marzipan", "praline", "hummus", "satay"})


def _allergen_title_words() -> set:
    """The words in a title that read as an allergen warning, stemmed.

    A clause naming one of these is never stripped, even when the recipe
    hasn't got it. Two reasons, and the second is the important one: a
    title saying peanut over a recipe with no peanut in it is worth a
    person's eyes rather than a silent rename — and the allergen matcher
    reads the NAME as well as the ingredients, so quietly taking the word
    off would take a fail-closed signal off every check downstream of it.

    Deliberately NOT every word coordination.py can match. That set
    includes butter, cheese, bread and pasta, which are ordinary dinner
    words — carving them out made the rule decline half the titles it
    exists for, and buys nothing: the rule only ever removes a word the
    recipe does NOT have, so it can hide a lie about butter and never real
    butter.

    Imported here rather than at module scope because coordination pulls in
    weekly_plan, which is the heavier half of this package.
    """
    global _ALLERGEN_TITLE_WORDS
    if _ALLERGEN_TITLE_WORDS is None:
        from .coordination import _ALLERGEN_ALIASES
        words = set(_ALLERGEN_ALIASES)
        for family in _ALARMING_ALLERGEN_FAMILIES:
            words |= set(_ALLERGEN_ALIASES.get(family, ())) - _DISH_NOT_A_WARNING
        _ALLERGEN_TITLE_WORDS = {_stem(w) for word in words for w in re.findall(r"[a-z]+", word)}
    return _ALLERGEN_TITLE_WORDS


def _promise_is_kept(stem: str, pool: set) -> bool:
    """Whether one promised thing turns up anywhere in the recipe."""
    if stem in pool:
        return True
    if _same_food(stem) & pool:
        return True
    # "blueberry" keeps a promise of "berry", "butterbean" of "bean". Only
    # in that direction, and only for a word long enough that the ending
    # means something -- "pea" inside "chickpea" is the alias table's job,
    # not a suffix rule's.
    return len(stem) >= 4 and any(p.endswith(stem) for p in pool)


def _split_title_clause(clause: str) -> list[str]:
    """"Rice, Beans and Slaw" -> the three things it names."""
    parts = re.split(r"\s*,\s*|\s+and\s+|\s+&\s+", clause, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def unkept_title_promises(name: str, ingredients: list, instructions: list | None = None) -> list[str]:
    """The parts of a title's trailing "with ..." clause the recipe hasn't got.

    Empty for every title this rule cannot be sure about — which is most of
    them, by design. See the section comment above for what it passes over
    and why.
    """
    if len(ingredients or []) < _TITLE_MIN_INGREDIENTS:
        return []
    match = _TITLE_WITH_CLAUSE.match((name or "").strip())
    if not match:
        return []
    known = _known_food_words()
    pool = _recipe_word_pool(ingredients, instructions)
    unkept = []
    for part in _split_title_clause(match.group("clause")):
        stems = _title_content_stems(part)
        if stems is None:
            continue
        # EVERY word, not any: a clause is only judged when the whole of it
        # is food this app knows. "Grilled Cheese Croutons" is two known
        # words; "Pico de Gallo" is none; "Chilli Crisp" is one and one, so
        # it is left alone rather than judged on the half that is legible.
        if not all(s in known for s in stems):
            continue
        if not any(_promise_is_kept(s, pool) for s in stems):
            unkept.append(part)
    return unkept


def honest_recipe_title(
    name: str,
    ingredients: list,
    instructions: list | None = None,
    taken: set | frozenset | tuple = (),
) -> str:
    """The title with anything the recipe hasn't got taken off it.

    "Seared Turkey and Zucchini Skillet with White Beans" -> "Seared Turkey
    and Zucchini Skillet". A clause that names several things keeps the
    ones that are real: "Chicken with Rice and White Beans" -> "Chicken
    with Rice".

    `taken` is every recipe name the household already uses, lowercased,
    and a correction that lands on one of them is refused. **Pass it from
    every live write path.** Two different things go wrong without it, and
    review reproduced both:

    - A new dish corrected onto an existing recipe's name is DISCARDED:
      _ensure_recipe_saved and swap's _save_recipe_if_new are both
      skip-if-the-name-exists, and plan_meal then resolves `WHERE name = ?`
      — so the slot silently points at a different dinner and the week
      shops for it. The correction makes that likelier, not less, because
      taking the distinguishing words off is what causes the collision.
    - plate_parts._variant_name builds "<base> with <choice>" precisely
      BECAUSE the base name is taken (read its docstring), so stripping the
      clause hands back a name that cannot be saved and the old dish is
      planned again and reported as a change.

    Returns the name unchanged whenever there is nothing to take off; when
    the corrected name is taken; when the clause names an allergen (see
    _allergen_title_words); and when taking it off would leave a name that
    says nothing — "Bowl with White Beans" would become "Bowl", so it is
    left alone for _title_promises_an_ingredient to report instead. A bad
    name beats no name.
    """
    return title_correction(name, ingredients, instructions, taken)[0]


def title_correction(
    name: str,
    ingredients: list,
    instructions: list | None = None,
    taken: set | frozenset | tuple = (),
) -> tuple[str, str | None]:
    """honest_recipe_title, plus the sentence for why it declined.

    The reason is part of the ANSWER, not a log line: repair_recipe_titles
    prints it, and printing the same sentence for three different refusals
    — as the first cut did — tells the person reading it something untrue.
    """
    held = {str(t).strip().lower() for t in (taken or ())}
    # THE ONE GUARD THAT MATTERS, and the root of three separate blockers.
    # A name that is already a recipe of this household's does not describe
    # a dish, it IDENTIFIES A ROW — and what it is being judged against here
    # is the model's echo of that row, which can be missing the very word
    # the clause names, or (big_meal) its whole method. Correcting it
    # renames the dish off its own recipe: the save path is
    # skip-if-the-name-exists, so a stepless, timeless duplicate is planted
    # under the shortened name and the household's real recipe is orphaned.
    # Reproduced three ways — a week reusing a recipe, a swap picker reusing
    # one, and chat making one the holiday main.
    #
    # This subsumes the is_new_recipe gates rather than replacing them: a
    # model that mislabels a reuse as new lands here too, which is the case
    # those gates cannot see.
    if (name or "").strip().lower() in held:
        return name, None
    allergens = _allergen_title_words()
    unkept = [
        part for part in unkept_title_promises(name, ingredients, instructions)
        if not (set(_title_content_stems(part) or []) & allergens)
    ]
    if not unkept:
        if unkept_title_promises(name, ingredients, instructions):
            return name, "It names an allergen, so I'd rather you looked at it than have me quietly take the word off."
        return name, None
    match = _TITLE_WITH_CLAUSE.match((name or "").strip())
    head = match.group("head").strip()
    if not _head_says_something(head):
        return name, "Taking that off would leave a name that doesn't say what the dish is."
    kept = [p for p in _split_title_clause(match.group("clause")) if p not in unkept]
    honest = f"{head} with {' and '.join(kept)}" if kept else head
    if honest.strip().lower() in held:
        return name, f"{honest!r} is already another of your recipes, so renaming onto it would point meals at the wrong one."
    return honest, None


def _title_promises_an_ingredient(entries: list[dict], context: dict) -> list[Violation]:
    """A dish's name must not name something that is in neither its
    ingredients nor its method.

    Every generated week is repaired before it is saved (see
    honest_recipe_title, called from agent._generate_weekly_plan), so this
    reports the leftovers: a recipe saved before that shipped, one brought
    in from a link or a cookbook page, or a title whose head is too thin to
    correct.
    """
    violations = []
    for entry in entries:
        if not _is_planned(entry):
            continue
        name = entry.get("meal_name") or ""
        unkept = unkept_title_promises(name, entry.get("ingredients") or [], entry.get("instructions") or [])
        if not unkept:
            continue
        promised = " and ".join(unkept)
        violations.append(Violation(
            rule="title_promises_an_ingredient", severity="warn",
            date=entry.get("date"), slot=entry.get("slot"),
            message=(
                f"'{name}' says {promised}, and there is none in the ingredients or the steps. "
                "The name should say what the dish is."
            ),
        ))
    return violations


# Any of these in a method is evidence the dish was seasoned. Deliberately
# generous: one hit anywhere is enough to pass, because the question is
# "was it seasoned at all", not "was it seasoned well".
_SEASONING_WORDS = {
    "salt", "salted", "salting", "pepper", "peppered", "season", "seasoned",
    "seasoning", "spice", "spices", "spiced", "herb", "herbs", "garlic",
    "onion", "ginger", "chili", "chilli", "chile", "paprika", "cumin",
    "coriander", "turmeric", "oregano", "thyme", "rosemary", "basil",
    "cilantro", "parsley", "dill", "soy", "miso", "vinegar", "lemon", "lime",
    "mustard", "curry", "masala", "cinnamon", "nutmeg", "sesame", "zest",
}

# Evidence that heat was actually used to build flavour rather than just
# applied.
_TECHNIQUE_WORDS = {
    "sear", "seared", "brown", "browned", "browning", "saute", "sauté",
    "sautee", "sauteed", "sautéed", "fry", "fried", "sizzle", "caramelize",
    "caramelise", "caramelized", "caramelised", "toast", "toasted", "char",
    "charred", "grill", "grilled", "simmer", "simmered", "reduce", "reduced",
    "deglaze", "bloom", "blooms", "blooming", "marinate", "marinated",
    "sweat", "render", "rendered", "crisp", "crisped", "broil", "broiled",
    "roast", "roasted",
}

# Dry or fat heat — an oven or a pan, where browning is actually available.
# This is what makes the check narrow enough to be worth having: a dressed
# salad or a cold noodle bowl never browns anything and is not supposed to,
# so without this gate the rule fired on good cold dishes and told them they
# "combine and heat" when they apply no heat at all.
#
# Note "bake"/"oven" appear HERE and deliberately not in _TECHNIQUE_WORDS:
# putting everything on one sheet pan is the exact failure being caught.
_DRY_HEAT_WORDS = {
    "bake", "baked", "baking", "oven", "pan", "skillet", "sheet", "tray",
    "griddle", "air-fry", "airfryer",
}


def _method_words(entry: dict) -> set:
    return set(re.findall(r"[a-zé]+", " ".join(entry.get("instructions") or []).lower()))


def _cooked_dinner(entry: dict) -> bool:
    """A dinner somebody actually cooks, with a method long enough to judge.

    Leftovers nights and reheats have nothing to season. A two-step method is
    not evidence of anything either way — plenty of genuinely good dinners are
    short, and this floor is not a length rule.
    """
    if entry.get("slot") != "dinner" or not _is_planned(entry):
        return False
    if (entry.get("source") or "") == "leftovers":
        return False
    return len(entry.get("instructions") or []) >= 3


def _seasoning_never_mentioned(entries: list[dict], context: dict) -> list[Violation]:
    """A cooked dinner whose method never mentions seasoning of any kind.

    Before route 1 the generation prompt said nothing about seasoning at all —
    both of its matches for "season" were the calendar. This is the floor
    under that fix: not "is it seasoned well", which is a judgement, but "does
    the method mention salt, a spice, an aromatic or an acid anywhere", which
    is a fact. A dinner that mentions none of them is not a dinner anyone
    wants to eat twice.
    """
    violations = []
    for entry in entries:
        if not _cooked_dinner(entry):
            continue
        if _method_words(entry) & _SEASONING_WORDS:
            continue
        violations.append(Violation(
            rule="seasoning_never_mentioned", severity="warn",
            date=entry["date"], slot="dinner",
            message=(
                f"{entry['date']} dinner ('{entry['meal_name']}'): the method never mentions "
                "salt, a spice, an aromatic or an acid."
            ),
        ))
    return violations


def _method_is_assembly(entries: list[dict], context: dict) -> list[Violation]:
    """A cooked dinner whose method shows no cooking technique at all.

    "Combine the ingredients, apply heat, serve" is the failure route 1's
    guidance is named after. The reference case is real output: "Baked Lemon
    Herb Salmon with Roasted Asparagus" — preheat, put it on a sheet pan,
    drizzle, top, bake. Nothing browned, nothing bloomed, nothing layered.

    'Bake' is deliberately not evidence of technique here, because baking
    everything together on one pan is precisely the thing being caught.
    """
    violations = []
    for entry in entries:
        if not _cooked_dinner(entry):
            continue
        words = _method_words(entry)
        if words & _TECHNIQUE_WORDS:
            continue
        # Nothing was ever put in an oven or a pan, so there was no browning
        # to skip. A dressed salad and a cold noodle bowl belong here, and
        # telling either of them it "combines and heats" would be false.
        if not (words & _DRY_HEAT_WORDS):
            continue
        violations.append(Violation(
            rule="method_is_assembly", severity="info",
            date=entry["date"], slot="dinner",
            message=(
                f"{entry['date']} dinner ('{entry['meal_name']}'): the method uses an oven "
                "or a pan but never browns, blooms or layers anything."
            ),
        ))
    return violations


# --------------------------------------------------------------------------
# Round 2 of the food floor ("Recipes, round 2: write the step, order the
# minutes, sauce the plate", 2026-09-14). agent.WRITE_IT_DOWN now tells the
# planner how a step is written and how the steps are ordered. These are the
# floor under that, in the same spirit as the three above: a fact about the
# method, never a judgement of taste, and narrow enough that the good dinner
# stays clean. A "dry plate" check (no sauce word anywhere) was considered
# and deliberately NOT written — it fires on a perfectly good stir-fry, and
# a check that fires on a decent dinner is worse than none.
# --------------------------------------------------------------------------

# A step that says when it is done: "until golden", "about 4 minutes",
# "400°F", "to 165". The prompt asks for "roughly how long, and what done
# looks like" on every step; this only asks that the METHOD has it somewhere.
_DONENESS_CUE = re.compile(
    r"\buntil\b"
    r"|\b\d+\s*(?:-|–|to)?\s*\d*\s*(?:min|mins|minute|minutes|hr|hrs|hour|hours|sec|seconds)\b"
    r"|\d+\s*°|\d+\s*(?:°?\s*[fc]\b|degrees)"
    r"|\b(?:reads|registers|reaches)\s+\d+",
    re.IGNORECASE,
)

# An "until" that is about the cook's schedule, not the food: "until you
# have time", "until ready to serve", "until needed". Not a doneness cue.
_NOT_A_CUE = re.compile(
    r"\buntil\s+(?:you|we|ready|needed|serving|required|it'?s time|the day|the next day)\b",
    re.IGNORECASE,
)

# A heat level or a temperature. "hot pan" counts — it is the instruction
# that matters ("heat the pan before the food"), not the vocabulary.
_HEAT_NAMED = re.compile(
    r"\b(?:low|medium|med|high|medium-high|medium-low)\s+heat\b"
    r"|\bover\s+(?:low|medium|med|high|medium-high|medium-low)\b"
    r"|\b(?:on|to)\s+(?:low|medium|high)\b"
    r"|\bhot\s+(?:pan|skillet|oil|oven|wok|griddle|grill)\b"
    r"|\b(?:until|till)\s+(?:it(?:'s| is)\s+|just\s+|lightly\s+)?smoking\b"
    r"|\b(?:smoking|ripping|screaming|blazing|very)\s+hot\b"
    r"|\b(?:simmer|simmering|boil|boiling|broil|broiling)\b"
    r"|\d+\s*°|\d+\s*(?:°?\s*[fc]\b|degrees)",
    re.IGNORECASE,
)

# The things that take longest and should therefore be started first. This
# is about STARTING an anchor, not mentioning it: "serve over rice" is not
# starting the rice.
_ANCHOR_START = re.compile(
    r"\bpreheat\b"
    r"|\bbring\b[^.]{0,40}\bto (?:a |the )?boil\b"
    r"|\bboil(?:ing)? (?:the |a |some )?(?:salted )?water\b"
    r"|\b(?:put|get|start|set)\b[^.]{0,20}\b(?:rice|potatoes|pasta water|water)\b[^.]{0,20}\bon\b"
    r"|\bstart (?:the |cooking the )?(?:rice|potatoes|grains|quinoa)\b"
    r"|\bcook (?:the )?rice (?:according|per|as)\b",
    re.IGNORECASE,
)

# Where the anchor may appear and still be "first". Step 1 is ideal; a
# "pat the chicken dry and salt it" step before "preheat" is normal and
# fine. Step 4 or later means the oven was remembered after the chopping.
_ANCHOR_LATEST_OK = 3

# A dinner cannot have this many real steps in this little time. Chosen
# conservatively — the local dev database held no recipes with steps on
# 2026-09-14 to tune against, so this is set where it is plainly impossible
# rather than merely optimistic. Revisit with real rows.
_MANY_STEPS = 9
_FEW_MINUTES = 15


# Anything that says heat is being applied — an oven, a pan, a pot, or the
# verb that needs one. Base-form verbs only, on purpose: a recipe cooks in
# the imperative ("toast the nuts"), while the past participle is usually an
# ingredient ("toasted hazelnuts" on a salad that never meets heat). A cold
# plate has nothing that can be "done", so the cue check has no question to
# ask it.
_APPLIES_HEAT_WORDS = _DRY_HEAT_WORDS | {
    "pot", "saucepan", "wok", "stove", "stovetop", "burner",
    "cook", "cooking", "heat", "boil", "simmer", "fry", "sear", "roast", "grill",
    "braise", "poach", "steam", "saute", "sauté", "sautee", "sweat", "microwave",
    "broil", "toast", "char", "reduce", "deglaze", "render", "bloom", "blanch",
}


def _steps_have_no_cue(entries: list[dict], context: dict) -> list[Violation]:
    """A cooked dinner whose method never says when anything is done.

    Not "every step has a cue" — that is the prompt's job — but "no step has
    one", which means the household is cooking blind. "Cook the chicken.
    Make the sauce. Serve." has no "until", no minutes, no temperature.
    Silent on a plate that never meets heat: there is nothing to be done.

    The cue has to sit on a step that applies heat. The verifier found the
    first draft passed a method whose only "until" was "marinate until you
    have time to cook it" — a cue on the prep, none on the cooking.
    """
    violations = []
    for entry in entries:
        if not _cooked_dinner(entry):
            continue
        steps = entry.get("instructions") or []
        heat_steps = [
            step for step in steps
            if set(re.findall(r"[a-zé]+", step.lower())) & _APPLIES_HEAT_WORDS
        ]
        if not heat_steps:
            continue
        if any(_DONENESS_CUE.search(_NOT_A_CUE.sub(" ", step)) for step in heat_steps):
            continue
        violations.append(Violation(
            rule="steps_have_no_cue", severity="info",
            date=entry["date"], slot="dinner",
            message=(
                f"{entry['date']} dinner ('{entry['meal_name']}'): no step says when anything "
                "is done — no 'until', no minutes, no temperature."
            ),
        ))
    return violations


# Where a heat level is genuinely the cook's to choose: an oven, a pan, a
# grill, and the verbs that need one. Deliberately NOT "cook", "pot" or
# "heat" — "cook the rice according to the packet" gets its heat from the
# packet, and a bowl built on that plus a dressing has no level to name.
# Not "broil" either: a home broiler has one setting, so "broil" names the
# heat the way "simmer" does. Prefer silence in both cases.
_NEEDS_A_HEAT_LEVEL = _DRY_HEAT_WORDS | {
    "grill", "grilling", "roast", "roasting", "sear", "searing", "fry",
    "frying", "saute", "sauté", "sautee", "sweat", "braise", "wok",
    "stovetop", "stove", "burner",
}


def _no_heat_named(entries: list[dict], context: dict) -> list[Violation]:
    """A cooked dinner that grills, roasts, sears, bakes or fries something
    and never says how hot.

    Gated like method_is_assembly: a dressed salad has no heat to name, and
    telling it so would be false. The gate is wider than that check's,
    though — the verifier found "grill until the juices run clear" slipping
    past a pan-or-oven-only gate.
    """
    violations = []
    for entry in entries:
        if not _cooked_dinner(entry):
            continue
        if not (_method_words(entry) & _NEEDS_A_HEAT_LEVEL):
            continue
        method = " ".join(entry.get("instructions") or [])
        if _HEAT_NAMED.search(method):
            continue
        violations.append(Violation(
            rule="no_heat_named", severity="info",
            date=entry["date"], slot="dinner",
            message=(
                f"{entry['date']} dinner ('{entry['meal_name']}'): the method uses an oven "
                "or a pan but never names a heat level or temperature."
            ),
        ))
    return violations


def _longest_thing_not_first(entries: list[dict], context: dict) -> list[Violation]:
    """The oven, the water or the rice is started late in the method.

    The prompt's rule is that whatever takes longest starts first. A method
    that preheats the oven at step 5 has the household chopping for ten
    minutes and then waiting for the oven — the 25-minutes-becomes-40 case.
    Silent when no anchor appears at all: a stir-fry has none to start.
    """
    violations = []
    for entry in entries:
        if not _cooked_dinner(entry):
            continue
        steps = entry.get("instructions") or []
        first_anchor = None
        for i, step in enumerate(steps, start=1):
            if _ANCHOR_START.search(step):
                first_anchor = i
                break
        if first_anchor is None or first_anchor <= _ANCHOR_LATEST_OK:
            continue
        violations.append(Violation(
            rule="longest_thing_not_first", severity="info",
            date=entry["date"], slot="dinner",
            message=(
                f"{entry['date']} dinner ('{entry['meal_name']}'): the oven, water or rice "
                f"is only started at step {first_anchor} of {len(steps)} — the longest thing "
                "should start first."
            ),
        ))
    return violations


def _minutes_vs_steps(entries: list[dict], context: dict) -> list[Violation]:
    """A method with many steps claiming very few minutes.

    Nine real steps cannot happen in fifteen minutes at a home stove. This is
    the one place the honest-minutes rule is checkable from outside; an
    optimistic number satisfies the rush cap while breaking the promise, and
    nothing else would notice.
    """
    violations = []
    for entry in entries:
        if not _cooked_dinner(entry):
            continue
        total = _minutes(entry)
        if not total:
            continue
        steps = len(entry.get("instructions") or [])
        if steps < _MANY_STEPS or total > _FEW_MINUTES:
            continue
        violations.append(Violation(
            rule="minutes_vs_steps", severity="info",
            date=entry["date"], slot="dinner",
            message=(
                f"{entry['date']} dinner ('{entry['meal_name']}'): {steps} steps in {total} "
                "minutes — the estimate is not one a household can hit."
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
    violations += _weekday_lunch_cap_respected(plan_entries, context)
    violations += _no_protein_run(plan_entries, context)
    violations += _dinner_repeat_in_history(plan_entries, context)
    violations += _reasoning_is_specific(plan_entries, context)
    violations += _novelty_floor(plan_entries, context)
    violations += _open_slot_budget(plan_entries, context)
    violations += _leftover_direction(plan_entries, context)
    violations += _full_plate(plan_entries, context)
    violations += _ingredient_repeat(plan_entries, context)
    violations += _steps_match_ingredients(plan_entries, context)
    violations += _quantities_plausible(plan_entries, context)
    violations += _produce_variety_named(plan_entries, context)
    violations += _snack_variety(plan_entries, context)
    # The food-quality floor (route 4). Same contract as everything above:
    # independent, additive, and one firing never suppresses another.
    violations += _dish_named_for_an_absence(plan_entries, context)
    violations += _title_promises_an_ingredient(plan_entries, context)
    violations += _seasoning_never_mentioned(plan_entries, context)
    violations += _method_is_assembly(plan_entries, context)
    # Round 2 of the floor: how the method is written, not just what it does.
    violations += _steps_have_no_cue(plan_entries, context)
    violations += _no_heat_named(plan_entries, context)
    violations += _longest_thing_not_first(plan_entries, context)
    violations += _minutes_vs_steps(plan_entries, context)
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
               r.ingredients_json, r.instructions_json, r.default_servings
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
            # what this floor cares about. The counter moves only when a
            # night is ticked cooked (cooker.check_off_meal), never at
            # planning time, so a recipe planned last week and never made
            # is still new here -- and this plan's own entries, written
            # moments before this reads them, have not touched it. A
            # freeform meal (no recipe row) isn't a recipe at all, so it
            # doesn't count either way.
            "is_new_recipe": r["times_cooked"] == 0 if r["times_cooked"] is not None else False,
            "links_to": derived_from.get("links_to"),
            "make_double_for": derived_from.get("make_double_for"),
            # A freeform meal has no recipe row and therefore no ingredient
            # list — no data, which _ingredient_repeat treats as nothing to
            # count rather than as a clean week.
            "ingredients": json.loads(r["ingredients_json"] or "[]"),
            # For _steps_match_ingredients. A freeform meal has no recipe
            # row and so no steps — nothing to check rather than a clean
            # recipe, same as its empty ingredient list above.
            "instructions": json.loads(r["instructions_json"] or "[]"),
            # For _quantities_plausible: the table the amounts are written
            # for. None for a freeform meal, which has no amounts either.
            "default_servings": r["default_servings"],
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


def repair_recipe_titles(apply: bool = False) -> list[dict]:
    """Every saved recipe whose title names something it hasn't got, and —
    with apply — the corrected name written back.

    The other half of the 2026-09-14 fix. Generation is honest from now on
    (agent._honest_meal_names), but the recipes already on record are not,
    and a dish reheated on Friday is read by the same person who wondered
    about it on Monday. Renaming the RECIPE ROW is what reaches every
    screen: plan_meal stores recipe_id and leaves freeform_meal null when a
    recipe matched, so the week card, the Cook view, the leftover's own
    line and the share page all read the recipe's name through that join.

    Two things it will not do. It never renames onto a name this household
    already uses — several lookups are `WHERE name = ?` with one row
    expected, and two recipes answering to one name is a worse problem than
    an inaccurate title. And it never touches a meal saved as freeform
    text: there is no ingredient list behind one, so there is nothing to
    hold the name against.

    One row per title with something to answer for: `after` is the
    corrected name, or None with a `why` when the correction was refused.
    A refusal is part of the ANSWER, not a log line — the person running
    this is deciding whether to write, and "I found one and left it" is
    something they need to read on the page rather than in stderr.

    Read-only unless asked. This is a rename with no undo in a database
    that holds a household's real cooking, so the script that drives it
    prints the list first and writes only on --apply.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, ingredients_json, instructions_json FROM recipes WHERE household_id = ?",
        (household_id(),),
    ).fetchall()
    taken = {(r["name"] or "").strip().lower() for r in rows}

    found = []
    for row in rows:
        try:
            ingredients = json.loads(row["ingredients_json"] or "[]")
            instructions = json.loads(row["instructions_json"] or "[]")
        except (TypeError, ValueError):
            continue
        if not unkept_title_promises(row["name"], ingredients, instructions):
            continue
        # Its OWN name is not a collision -- it is the name being corrected,
        # and here it is judged against the row's OWN ingredients and steps
        # rather than a model's echo of them, which is what makes this the
        # one path allowed to correct a name that names a saved recipe.
        honest, why = title_correction(
            row["name"], ingredients, instructions, taken=taken - {(row["name"] or "").strip().lower()},
        )
        if honest == row["name"]:
            found.append({"recipe_id": row["id"], "before": row["name"], "after": None, "why": why})
            continue
        found.append({"recipe_id": row["id"], "before": row["name"], "after": honest, "why": None})
        taken.add(honest.strip().lower())

    if apply:
        for change in found:
            if not change["after"]:
                continue
            conn.execute(
                "UPDATE recipes SET name = ? WHERE id = ? AND household_id = ?",
                (change["after"], change["recipe_id"], household_id()),
            )
        conn.commit()
    conn.close()
    return found


# The rules that read a recipe rather than the week: what a step reaches
# for, whether an amount is plausible, whether the title's promise is in
# the list, and how the method is written. At draft time these have
# nothing to read — the menu pass saves a new dish without ingredients or
# steps (2026-09-21) — so check_recipes_and_log runs exactly these again
# once the recipe pass has written them. The week-level rules (repeats,
# caps, variety, plates) were checked at draft time and are not re-run,
# so the morning report's FOOD count never carries them twice.
_RECIPE_RULES = (
    _ingredient_repeat,
    _steps_match_ingredients,
    _quantities_plausible,
    _produce_variety_named,
    _title_promises_an_ingredient,
    _seasoning_never_mentioned,
    _method_is_assembly,
    _steps_have_no_cue,
    _no_heat_named,
    _longest_thing_not_first,
    _minutes_vs_steps,
)


def _plan_intake(plan_id: int) -> dict:
    """The intake the plan was generated from, or {} — read here rather
    than through week_intake's by-week lookup, because by approval time a
    newer revision for the same week may exist and this wants the one the
    dishes were actually chosen from."""
    from . import week_intake as _week_intake

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT wi.* FROM weekly_plans wp JOIN week_intake wi ON wi.id = wp.intake_id "
            "WHERE wp.id = ? AND wp.household_id = ?",
            (plan_id, household_id()),
        ).fetchone()
    finally:
        conn.close()
    return _week_intake._intake_row_to_dict(row) if row else {}


def check_recipes_and_log(plan_id: int, recipe_names: list[str]) -> list[Violation]:
    """
    The recipe-level half of check_and_log, for the recipes the recipe
    pass just wrote (agent.fill_pending_recipes_for_plan). Same contract:
    read-only, log-only, never raises, and what it finds lands in the
    morning report's FOOD section through record_plan_quality.

    The context is rebuilt from what the household has on record rather
    than from the generation's snapshot, which is gone by approval time:
    the must-avoids from members and hard facts, the household's own
    words from its notes and the plan's intake. Every rule here checks a
    recipe against itself or against those, never against the week's
    history, so nothing is lost by not having the snapshot.
    """
    wanted = {(n or "").strip().lower() for n in recipe_names if n}
    if not wanted:
        return []
    try:
        from . import household as _household, memory as _memory

        entries = [
            e for e in _load_plan_entries(plan_id)
            if (e.get("meal_name") or "").strip().lower() in wanted
        ]
        if not entries:
            return []
        household_memory = _memory.get_household_memory() or {}
        intake = _plan_intake(plan_id)
        quality_context = {
            "household_asks": " ".join(str(part) for part in (
                intake.get("freeform") or "",
                " ".join(intake.get("cuisines") or []),
                " ".join(intake.get("moods") or []),
                household_memory.get("notes") or "",
            )).lower(),
            "avoided": [
                *(r for m in _household.list_members() for r in (m.get("dietary_restrictions") or [])),
                *(household_memory.get("dislikes") or []),
                *(f.get("text") or "" for f in _memory.get_facts() if f.get("hard")),
            ],
        }
        violations: list[Violation] = []
        for rule in _RECIPE_RULES:
            violations += rule(entries, quality_context)
        for v in violations:
            logger.warning(
                "Plan %s recipe quality [%s/%s]%s%s: %s",
                plan_id, v.rule, v.severity,
                f" {v.date}" if v.date else "", f" {v.slot}" if v.slot else "",
                v.message,
            )
        _usage.record_plan_quality(plan_id, violations)
        return violations
    except Exception:
        logger.exception(
            "Recipe quality check failed for plan %s; the recipes themselves are unaffected", plan_id
        )
        return []


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
            "unrushed_dates": {d for d, tags in night_tags.items() if "unrushed" in tags},
            "weeknight_max_minutes": memory.get("weeknight_max_minutes"),
            "prep_days": (memory.get("rhythm") or {}).get("prep_days") or [],
            "lunch_kinds": _weekday_lunches.kinds_by_date(intake_ctx),
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
            # What this household actually avoids, from the same three
            # sources the generation prompt treats as must-avoids: each
            # member's dietary restrictions, their standing dislikes, and any
            # fact flagged hard. _dish_named_for_an_absence needs it to tell
            # "Pineapple-Free Fruit Cup" (a household allergic to pineapple)
            # from "No-Knead Bread" (a household allergic to nothing in that
            # name). Without it that check stays silent, which is the right
            # way for it to fail.
            "avoided": [
                *(r for m in (memory.get("members") or [])
                  for r in (m.get("dietary_restrictions") or [])),
                *(memory.get("dislikes") or []),
                *(f.get("text") or "" for f in (generation_context.get("household_facts") or [])
                  if f.get("hard")),
            ],
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
                "Plan %s came back with %d quality violation(s) logged above. Nothing "
                "about the plan itself was changed.",
                plan_id, len(violations),
            )
        # ...and persisted, so the morning report can say so. Emily's call
        # (2026-09-10) when asked what a failed check should do: "tell you in
        # the morning report" — a log line on a server nobody reads was the
        # option she was offered and did not take. Its own table, and its own
        # section in the report: a dull dinner must never outrank a server
        # error under BROKEN.
        _usage.record_plan_quality(plan_id, violations)
        return violations
    except Exception:
        logger.exception(
            "Plan quality check failed for plan %s; the plan itself is unaffected", plan_id
        )
        return []
