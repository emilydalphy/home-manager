"""
A snack from the last two weeks never reaches the draft either.

Emily, 2026-09-28: "It keeps giving me the same snack suggestions as
previous weeks." meal_variety.repick_recent_repeats already enforced the
two-week no-repeat window for dinner and lunch (2026-09-25), and for
breakfast under "Something new" (2026-09-27) — but snack was never in
either list, in any mood, and the docstring said so was on purpose:
"Breakfast and snack are NOT checked, on purpose... the same oats every
morning is the rhythm working." That reasoning is right for breakfast
(the prompt asks for it to repeat, three places say so in agent.py and
meal_variety.py) but was never actually asked for snack anywhere — snack
was simply never checked, the exact gap dinner and lunch had before this
pass existed at all.

The fix is at the call site in app/agent.py's _finish_week_slots: `slots`
now always carries "snack" in addition to whatever
meal_variety.no_repeat_slots(intake) returns, rather than widening
no_repeat_slots() itself — that function also feeds
meal_variety.recent_refusals(), which the distinct-dish-COUNT pass reads
(enforce_distinct_meal_count), and that pass is intentionally left alone
here (owned elsewhere). See tests/test_no_repeat_enforced.py for the
dinner/lunch/breakfast half of this rule and its CATCH/GUARD/NAME
vocabulary, which this file follows.

No "favourite" or "staple" concept exists anywhere in this codebase that
would let a household mark a snack to intentionally keep repeating it
(verified: the only "staple" concept is grocery items bought on a rhythm,
app/tools/staples.py — unrelated; saved_recipes.rating and taste_verdict
='favourite' only lean generation toward a dish or note it's liked, they
never exempt it from the no-repeat window). So there is no "unless
favourited" branch to test — every snack is simply held to the window,
exactly like a dinner or lunch.

`last_week`'s own snack (see its fixture docstring in
test_no_repeat_enforced.py) is "Fig bars", deliberately not `_week()`'s
plain default ("Apple") — every test here that wants a collision passes
`snacks=["Fig bars"] * 7` for THIS week on purpose.
"""
from __future__ import annotations

from app import agent, tools
from app.db import get_conn
from app.tools import allergen_gate, meal_variety

from test_no_repeat_enforced import (  # noqa: F401  (reused fixtures/helpers)
    FRESH_DINNERS,
    _approve_in_place,
    _at,
    _monday,
    _names,
    _week,
    last_week,
    picker,
    stub_model,
)


def test_a_snack_from_last_week_is_replaced_not_merely_left(last_week, stub_model, picker):
    """
    CATCH. Last week's snack (Fig bars, every day — see `last_week`) is on
    `main` still Fig bars on the new draft, and the picker is never
    called: nothing ever checked snack against the window. Here it goes,
    the whole dish, every day it holds, for ONE picker call — the same
    shape as a folded dinner or lunch repeat.
    """
    week = _monday(0)
    stub_model(_week(week, FRESH_DINNERS, ["Chickpea salad"] * 7, snacks=["Fig bars"] * 7))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert "Fig bars" not in _names(plan_id, "snack")
    assert set(_names(plan_id, "snack")) == {"Moussaka"}
    assert len(picker) == 1
    assert picker[0]["slot"] == "snack"
    # Untouched: breakfast still repeats by design, dinner/lunch are fresh.
    assert set(_names(plan_id, "breakfast")) == {"Overnight oats"}


def test_the_snack_repeat_and_the_window_are_on_avoid_and_named_in_the_ask(last_week, stub_model, picker):
    """
    CATCH, and red on `main` for the right reason by an indirect route:
    it dies reading picker[0], because on `main` the picker is never
    asked about a snack at all.
    """
    week = _monday(0)
    stub_model(_week(week, FRESH_DINNERS, ["Chickpea salad"] * 7, snacks=["Fig bars"] * 7))
    agent.generate_weekly_plan(week)

    context = picker[0]
    assert context["slot"] == "snack"
    assert "Fig bars" in context["avoid"]
    assert "Fig bars" in context["replacing_because"]
    assert "last two weeks" in context["replacing_because"]


def test_a_snack_they_asked_for_by_name_is_kept(last_week, stub_model, picker):
    """
    GUARD: their own words beat the rule for snack exactly as for dinner,
    lunch and (under "Something new") breakfast — asked_for_by_name reads
    the household's typed words for this week, not just the model's own
    derived_from.freeform stamp.
    """
    week = _monday(0)
    tools.save_week_intake(week, freeform="fig bars again please for snack")
    stub_model(_week(week, FRESH_DINNERS, ["Chickpea salad"] * 7, snacks=["Fig bars"] * 7))
    plan = agent.generate_weekly_plan(week)

    assert set(_names(plan["weekly_plan_id"], "snack")) == {"Fig bars"}
    assert picker == []


def test_a_snack_already_eaten_is_left_as_generated(last_week, stub_model, picker):
    """
    GUARD, same shape as test_no_repeat_enforced.
    test_a_night_already_cooked_is_never_touched: a freshly generated week
    has no eaten snack, so this is driven directly against
    repick_recent_repeats with slots=("snack",) — the exact call
    _finish_week_slots now makes. A cooked/eaten snack is never swapped
    out from under the household after the fact (_group_dishes reads
    cooked_status == 'done').
    """
    week = _monday(0)
    dates = tools._week_dates(week)
    stub_model(_week(week, FRESH_DINNERS, ["Chickpea salad"] * 7, snacks=["Trail mix"] * 7))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    # Put the repeat back, by hand, and mark it cooked (eaten).
    assert _at(plan_id, dates[0], "snack")["meal"] == "Trail mix"
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET freeform_meal = 'Fig bars', recipe_id = NULL, "
        "cooked_status = 'done' WHERE id = ?",
        (_at(plan_id, dates[0], "snack")["entry_id"],),
    )
    conn.commit()
    conn.close()

    before = len(picker)
    out = meal_variety.repick_recent_repeats(plan_id, week, allergen_gate.CallBudget(), slots=("snack",))
    assert out["repeats"] == 1 and out["repicked"] == 0
    assert out["left"] == ["Fig bars"]
    assert len(picker) == before
    assert _at(plan_id, dates[0], "snack")["meal"] == "Fig bars"


def test_snack_is_always_in_the_no_repeat_call_regardless_of_mood(last_week, stub_model, picker):
    """
    CATCH, ordinary mood (no "Something new"): snack is not gated by mood
    the way breakfast is — meal_variety.no_repeat_slots(None) still
    returns only ("dinner", "lunch"), but the call site in
    _finish_week_slots always adds "snack" on top of that. This test
    exercises the ordinary path (no moods saved at all) to be sure the
    fix isn't accidentally mood-gated too.
    """
    assert meal_variety.no_repeat_slots(None) == ("dinner", "lunch")  # unchanged, deliberately
    week = _monday(0)
    stub_model(_week(week, FRESH_DINNERS, ["Chickpea salad"] * 7, snacks=["Fig bars"] * 7))
    plan = agent.generate_weekly_plan(week)

    assert "Fig bars" not in _names(plan["weekly_plan_id"], "snack")


def test_the_repeat_window_and_the_dish_count_fold_both_hold(last_week, stub_model, picker):
    """
    Interplay guard (2026-09-28 staging merge, snacks-vary-week-to-week +
    draft-dish-counts-and-leftovers): repick_recent_repeats (the no-repeat
    window, `_finish_week_slots`) runs BEFORE enforce_snack_dishes (the
    "different dishes a week" fold), and in that order every entry is
    clear of the last-two-weeks window before the fold ever picks a
    "kept" dish to fold extras into — so the fold cannot hand a folded
    day back last week's snack, and the window's fix survives the fold
    that runs after it.

    This week hands the model four distinct snack dishes across the days,
    two of them the exact repeat from `last_week` ("Fig bars"), with
    snack_dishes_per_week set to 2 — fewer than the four distinct dishes
    the model returns even after the repeat is replaced. Both rules must
    show in the result: no "Fig bars" anywhere, AND at most 2 distinct
    snack dishes left standing.
    """
    tools.edit_preference("snack_dishes_per_week", 2)
    week = _monday(0)
    stub_model(_week(
        week, FRESH_DINNERS, ["Chickpea salad"] * 7,
        snacks=["Fig bars", "Trail mix", "Fig bars", "Orange", "Trail mix", "Orange", "Popcorn"],
    ))
    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    names = _names(plan_id, "snack")
    assert "Fig bars" not in names  # the no-repeat window held
    assert len(set(names)) <= 2  # the dish-count fold held
