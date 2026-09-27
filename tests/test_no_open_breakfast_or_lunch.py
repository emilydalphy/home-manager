"""
A breakfast or a lunch is never handed back as a question.

plan_quality._open_slot_budget has stated that rule for as long as it has
existed and only ever MEASURED it. The morning report for household 1 on
2026-09-27 carried the warning it exists to produce:

    warn  3 open slots this week; the budget is at most 1.
    warn  2026-10-02 breakfast is open, but breakfast/lunch must never be.

And the thing that had broken it was the app's own generation, not the
model. agent._finish_week_slots ends with a gap audit that turns ANY slot
the model failed to return into an open question — breakfasts and lunches
included — and weekly_plan.repair_leftover_chains reopens a breakfast
whose chain doesn't check out.

A missing breakfast does not need a model call to fill. This app already
treats a repeated breakfast as normal and desirable, so the free, honest
answer is another of THIS WEEK'S OWN breakfasts.

WHAT THIS DOES NOT COVER. allergen_gate opens a breakfast or lunch of
its own when it cannot find a safe dish, and that question is LEFT
standing — it says something true about the household's own week, where
the gap audit's says only that the app failed. Its late sweep
(allergen_gate.sweep_plan, after the quality pass) is out of reach for
the same reason. Both are named in the code.

WHAT IS RED AGAINST MAIN, said honestly. The end-to-end tests in section 2
are the behaviour catches: they drive real generation with the model
stubbed and read the rows back, and on main the slot comes back `open`.
The unit tests in section 1 name meal_variety.fill_gaps_with_a_repeat,
which main has not got, so against main they die on an AttributeError —
that is the only kind of red a test of a new function can have and it is
NOT evidence of anything. Each says which it is. The guards say what
mutation pins them.

The model call is stubbed throughout; none of what is under test is the
model's behaviour.
"""
import datetime
import json

import pytest

from conftest import household_date
from app import agent, tools
from app.tools import allergen_gate as _allergen_gate
from app.tools import meal_variety as _meal_variety
from app.tools import plan_quality
from app.db import get_conn


# ---------- the week under test ----------

def _week_start() -> str:
    """Next Monday on the HOUSEHOLD's clock (CLAUDE.md: a dated test seeds
    off the household's clock, never the process's)."""
    today = datetime.date.fromisoformat(household_date())
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


BREAKFASTS = ["Oatmeal", "Eggs on Toast"]
LUNCHES = ["Chicken Salad", "Lentil Soup"]
DINNERS = ["Chili", "Tilapia", "Stir Fry", "Roast", "Pasta", "Tacos", "Curry"]
SNACKS = ["Apple and Peanut Butter", "Hummus and Carrots"]


@pytest.fixture
def recipes():
    for name in BREAKFASTS + LUNCHES + DINNERS + SNACKS:
        tools.add_recipe(
            name, ingredients=[{"item": name.split()[0], "qty": "1"}],
            prep_time_minutes=5, cook_time_minutes=5,
        )


def _entry(date, slot, meal, **extra):
    return {"date": date, "slot": slot, "meal_name": meal, "is_new_recipe": False,
            "reasoning": "fits the week", **extra}


def _full_week(week: str) -> list[dict]:
    """A complete 21-slot week, one entry per slot, nothing folded — so a
    test can take exactly one slot out and nothing else moves."""
    days = tools._week_dates(week)
    out = []
    for i, day in enumerate(days):
        out.append(_entry(day, "breakfast", BREAKFASTS[i % 2]))
        out.append(_entry(day, "lunch", LUNCHES[i % 2]))
        out.append(_entry(day, "dinner", DINNERS[i]))
    return out


def _without(week_items, date, slot):
    return [e for e in week_items if not (e["date"] == date and e["slot"] == slot)]


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _rows(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.derived_from_json, "
        "       COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.component_category IS NULL "
        "ORDER BY mpe.date, mpe.slot, mpe.id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _at(plan_id, date, slot):
    return [r for r in _rows(plan_id) if r["date"] == date and r["slot"] == slot]


def _derived(row):
    return json.loads(row["derived_from_json"] or "{}")


# =====================================================================
# 1. The pass itself.
#
#    RED AGAINST MAIN ONLY BECAUSE THE NAME IS NOT THERE. Every test in
#    this section calls meal_variety.fill_gaps_with_a_repeat directly and
#    dies on AttributeError against main. Read them as a specification,
#    not as evidence; the behaviour evidence is section 2.
# =====================================================================

def _bare_plan(week: str, rows: list[tuple]) -> int:
    """A plan with exactly the rows given, and nothing else — so the pass
    is judged on its own rather than on what generation left behind."""
    plan = tools.create_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]
    for date, slot, meal in rows:
        tools.plan_meal(meal_date=date, meal=meal, slot=slot, weekly_plan_id=plan_id)
    return plan_id


def test_a_missing_breakfast_is_filled_with_another_of_this_weeks_breakfasts(recipes):
    """NAME-ONLY red against main. The whole point of the pass."""
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "breakfast", "Oatmeal")])

    out = _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    assert out["filled"] == [{"date": days[1], "slot": "breakfast", "meal": "Oatmeal"}]
    assert [(r["slot_state"], r["meal"]) for r in _at(plan_id, days[1], "breakfast")] == [
        ("planned", "Oatmeal")]


def test_the_dish_on_the_fewest_nights_wins_and_ties_break_by_date_then_name(recipes):
    """
    NAME-ONLY red against main. Filling three gaps must spread them rather
    than pile them onto one dish, and a test has to be able to predict the
    answer — "whatever the database handed back first" is not a rule.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [
        (days[0], "breakfast", "Oatmeal"),
        (days[1], "breakfast", "Oatmeal"),
        (days[2], "breakfast", "Eggs on Toast"),
    ])

    out = _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:6])

    # Eggs is on 1 night and Oatmeal on 2, so Eggs goes first; then they
    # are level at 2 and the tie goes to the earlier first appearance
    # (Oatmeal, days[0]); then Eggs is behind again.
    assert [f["meal"] for f in out["filled"]] == ["Eggs on Toast", "Oatmeal", "Eggs on Toast"]
    assert [f["date"] for f in out["filled"]] == days[3:6]


def test_an_open_breakfast_is_filled_as_well_as_a_missing_one(recipes):
    """
    NAME-ONLY red against main. A slot holding an `open` row and a slot
    holding no row at all are the same thing to a household — a meal
    nobody has answered — and repair_leftover_chains produces the first.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "breakfast", "Oatmeal")])
    tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=days[1], slot="breakfast",
                         open_reason="I'd rather ask than guess.")

    _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    rows = _at(plan_id, days[1], "breakfast")
    assert [(r["slot_state"], r["meal"]) for r in rows] == [("planned", "Oatmeal")], \
        "the open row is replaced, not left beside the fill"


def test_a_planned_empty_slot_is_not_a_gap(recipes):
    """
    NAME-ONLY red against main. An out day, a skipped day, an away slot
    and a zero-count category are ANSWERS, deliberately written earlier in
    _finish_week_slots. Filling one would sell a household breakfast they
    said they did not want.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "breakfast", "Oatmeal")])
    tools.plan_slot_empty(weekly_plan_id=plan_id, meal_date=days[1], slot="breakfast",
                          reason="You've asked me not to plan breakfasts.")

    out = _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    assert out["filled"] == []
    assert [r["slot_state"] for r in _at(plan_id, days[1], "breakfast")] == ["planned_empty"]


def test_a_week_with_nothing_to_repeat_leaves_the_gap(recipes):
    """
    NAME-ONLY red against main. A week with no breakfast anywhere has
    nothing to copy, and the question is then the honest answer.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "dinner", "Chili")])

    out = _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    assert out["filled"] == []
    assert {g["why"] for g in out["left"]} == {"nothing to repeat"}
    assert _at(plan_id, days[0], "breakfast") == [], "and nothing was invented"


def test_a_reheat_night_is_never_the_dish_that_gets_copied(recipes):
    """
    NAME-ONLY red against main. A reheat row's name is a sentence about
    one particular cook and its links_to points at a row this gap has no
    claim on. Copying it would plan a fresh cook under a made-ahead name.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "lunch", "Chicken Salad"),
                                (days[1], "lunch", "Chicken Salad")])
    cook = _at(plan_id, days[0], "lunch")[0]
    tools.plan_meal(meal_date=days[2], meal="Leftovers — Monday's Chicken Salad",
                    slot="lunch", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"entry_id:{cook['id']}"})
    # A chain is only a chain when BOTH halves agree, and _finish_week_slots
    # runs this pass after repair_leftover_chains, so the chain a real week
    # hands it is a confirmed one. Confirm it here too.
    tools.repair_leftover_chains(plan_id)

    # The reheat is on ONE night and the cook on two, so with the reheat in
    # the supply the fewest-nights rule would pick IT. That is the whole
    # point of the seed: a tie broken by date would let a broken exclusion
    # through unnoticed.
    out = _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:4])

    assert [f["meal"] for f in out["filled"]] == ["Chicken Salad"], \
        "the cook is copied, never the sentence about it"


def test_a_dish_over_the_nights_time_cap_is_not_offered(recipes):
    """
    NAME-ONLY red against main. This pass runs after cap_enforce, which is
    dinner-only, so nothing downstream would catch a 45-minute lunch
    landing on a day the household said they have twenty minutes.
    """
    week = _week_start()
    days = tools._week_dates(week)
    tools.add_recipe("Slow Ragu", ingredients=[{"item": "beef", "qty": "1"}],
                     prep_time_minutes=15, cook_time_minutes=30)
    plan_id = _bare_plan(week, [(days[0], "lunch", "Slow Ragu")])

    out = _meal_variety.fill_gaps_with_a_repeat(
        plan_id, days[:2], caps={(days[1], "lunch"): 20})

    assert out["filled"] == []
    assert [g["why"] for g in out["left"] if g["slot"] == "lunch"] == \
        ["nothing fits 20 minutes"]


def test_a_dish_inside_the_cap_still_fills_it(recipes):
    """NAME-ONLY red against main. The cap filter must not be a wall."""
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "lunch", "Chicken Salad")])

    out = _meal_variety.fill_gaps_with_a_repeat(
        plan_id, days[:2], caps={(days[1], "lunch"): 20})

    assert [f["meal"] for f in out["filled"]] == ["Chicken Salad"]


def test_the_filled_row_says_why(recipes):
    """
    NAME-ONLY red against main. derived_from is how the draft stays
    honest about a night Pomona chose rather than the model.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "breakfast", "Oatmeal")])

    _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    assert _derived(_at(plan_id, days[1], "breakfast")[0]) == {
        "constraint": _meal_variety.GAP_FILL_CONSTRAINT, "repeat_of": "Oatmeal"}


def test_the_filled_row_carries_a_reason_rather_than_a_blank(recipes):
    """
    NAME-ONLY red against main. plan_quality._reasoning_is_specific warns
    "has no reasoning at all" for every blank planned row (it exempts a
    leftovers night and nothing else), so a blank here would trade one
    warning in the morning report for another.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "breakfast", "Oatmeal")])

    _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    entries = plan_quality._load_plan_entries(plan_id)
    assert [e["reasoning"] for e in entries if e["date"] == days[1]] == \
        [_meal_variety.GAP_FILL_REASON]
    # And the rule that would have caught a blank says nothing about it.
    # (The seeded Monday row is hand-planted with no reasoning of its own
    #  and is not this pass's work, so it is not in scope here.)
    assert [v.date for v in plan_quality._reasoning_is_specific(entries, {})] == [days[0]]


def test_the_pass_never_raises(recipes):
    """
    NAME-ONLY red against main. A week that stands as generated with an
    open breakfast in it is a warning in the morning report; a week that
    fails to save is not a week.
    """
    out = _meal_variety.fill_gaps_with_a_repeat(-1, ["2026-10-05"])
    assert out["filled"] == []


def test_dinner_is_not_in_scope(recipes):
    """
    NAME-ONLY red against main. A dinner genuinely is a decision, and
    quietly repeating one nobody asked for is the opposite of what the
    household wants. Rule (a) names only breakfast and lunch.
    """
    assert _meal_variety.NEVER_OPEN_SLOTS == ("breakfast", "lunch")

    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "dinner", "Chili")])

    out = _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    assert out["filled"] == []
    assert _at(plan_id, days[1], "dinner") == []


# =====================================================================
# 2. End to end through real generation. THESE ARE THE BEHAVIOUR CATCHES.
# =====================================================================

def test_a_week_missing_a_breakfast_comes_back_planned_not_open(recipes, stub_model):
    """
    CATCH. On main this slot is `open` with derived_from
    {"constraint": "generation_gap"} — reproduced before this was built.
    """
    week = _week_start()
    days = tools._week_dates(week)
    stub_model(_without(_full_week(week), days[3], "breakfast"))

    plan = agent.generate_weekly_plan(week)

    rows = _at(plan["weekly_plan_id"], days[3], "breakfast")
    assert [r["slot_state"] for r in rows] == ["planned"]
    assert rows[0]["meal"] in BREAKFASTS
    assert _derived(rows[0])["constraint"] == _meal_variety.GAP_FILL_CONSTRAINT


def test_a_week_missing_a_lunch_comes_back_planned_not_open(recipes, stub_model):
    """CATCH. The same, for the other slot the rule names."""
    week = _week_start()
    days = tools._week_dates(week)
    stub_model(_without(_full_week(week), days[5], "lunch"))

    plan = agent.generate_weekly_plan(week)

    rows = _at(plan["weekly_plan_id"], days[5], "lunch")
    assert [r["slot_state"] for r in rows] == ["planned"]
    assert rows[0]["meal"] in LUNCHES


def test_the_rule_the_morning_report_measures_is_true_of_the_finished_week(recipes, stub_model):
    """
    CATCH, and the one that answers the report. Emily's own warning, run
    against a generated week rather than asserted about: no breakfast or
    lunch violation, and the whole-week budget of one open slot met.
    """
    week = _week_start()
    days = tools._week_dates(week)
    items = _without(_full_week(week), days[1], "breakfast")
    items = _without(items, days[2], "lunch")
    items = _without(items, days[4], "breakfast")
    stub_model(items)

    plan = agent.generate_weekly_plan(week)

    entries = plan_quality._load_plan_entries(plan["weekly_plan_id"])
    violations = plan_quality._open_slot_budget(entries, {})
    assert [v.message for v in violations] == []


def test_three_missing_breakfasts_are_spread_across_the_weeks_own_dishes(recipes, stub_model):
    """
    CATCH. Three gaps must not all become the same dish — the fewest-first
    rule, driven through generation rather than through the pass alone.
    """
    week = _week_start()
    days = tools._week_dates(week)
    # _full_week alternates the two breakfasts, so taking out days 2, 3
    # and 5 leaves Oatmeal on three mornings and Eggs on one. The rule
    # then reads: Eggs (1), Eggs (2), and on the third gap the two are
    # level at 3 so the tie goes to the dish that appears earliest.
    items = _full_week(week)
    for d in (days[2], days[3], days[5]):
        items = _without(items, d, "breakfast")
    stub_model(items)

    plan = agent.generate_weekly_plan(week)

    rows = _rows(plan["weekly_plan_id"])
    gaps = [r for r in rows if r["slot"] == "breakfast" and r["date"] in (days[2], days[3], days[5])]
    # The claim this is named for is the SPREAD, but assert the states
    # first so the test fails on main for its own reason rather than on a
    # constant main has not got.
    assert [r["slot_state"] for r in gaps] == ["planned"] * 3
    assert [(r["date"], r["meal"]) for r in gaps] == [
        (days[2], "Eggs on Toast"), (days[3], "Eggs on Toast"), (days[5], "Oatmeal")]
    assert all(_derived(r).get("constraint") == _meal_variety.GAP_FILL_CONSTRAINT for r in gaps)


def test_a_breakfast_the_model_itself_handed_back_as_open_is_filled(recipes, stub_model):
    """
    CATCH. The gap audit is not the only producer: the per-slot save loop
    writes whatever slot_state the model sent, and repair_leftover_chains
    reopens a broken chain. Both land here as an `open` row.
    """
    week = _week_start()
    days = tools._week_dates(week)
    items = _without(_full_week(week), days[2], "breakfast")
    items.append(_entry(days[2], "breakfast", "", slot_state="open",
                        open_reason="I'd rather ask than guess."))
    stub_model(items)

    plan = agent.generate_weekly_plan(week)

    rows = _at(plan["weekly_plan_id"], days[2], "breakfast")
    assert [r["slot_state"] for r in rows] == ["planned"]


def test_the_morning_report_is_never_told_about_an_open_breakfast(recipes, stub_model, caplog):
    """
    CATCH, and the one closest to what Emily actually reads.
    plan_quality.check_and_log runs INSIDE _finish_week_slots and writes
    the warning that reached the morning report; the finished week and
    the log are two different questions, and a fill that ran only after
    the quality pass would leave the week clean and the log still
    shouting. Pinned by the mutation "the fill moved below the quality
    pass", which reddens this and the source marker and nothing else.

    NOT pinned by "moved below the gap audit" — measured, not assumed:
    moved there but still ABOVE the quality pass, the fill replaces the
    open rows the audit has just written before the quality pass reads
    them, so only the source marker goes red. Worth knowing, because it
    is why the source marker exists at all.
    """
    import logging

    week = _week_start()
    days = tools._week_dates(week)
    items = _without(_full_week(week), days[1], "breakfast")
    items = _without(items, days[2], "lunch")
    stub_model(items)

    with caplog.at_level(logging.INFO, logger="home_manager"):
        agent.generate_weekly_plan(week)

    assert [m for m in caplog.messages if "open_slot_budget" in m] == []


def test_a_missing_dinner_is_still_handed_back_as_a_question(recipes, stub_model):
    """
    GUARD, green on main and here. Dinner is deliberately out of scope.
    Pinned by the mutation "NEVER_OPEN_SLOTS gains dinner", which reddens
    this and nothing else.
    """
    week = _week_start()
    days = tools._week_dates(week)
    stub_model(_without(_full_week(week), days[3], "dinner"))

    plan = agent.generate_weekly_plan(week)

    rows = _at(plan["weekly_plan_id"], days[3], "dinner")
    assert [r["slot_state"] for r in rows] == ["open"]
    assert _derived(rows[0])["constraint"] == "generation_gap"


def test_the_21_slot_guarantee_still_holds(recipes, stub_model):
    """
    GUARD, green on main (an `open` row is present as far as the audit is
    concerned, so main satisfies this too). Seeded with a missing slot AND
    an open one, because the way to break it is to plan the repeat BESIDE
    the open row instead of in its place: pinned by the mutation "the fill
    passes no entry ids to _replace_slot_entries", which leaves two rows
    on the Wednesday and reddens this on `duplicated`.
    """
    week = _week_start()
    days = tools._week_dates(week)
    items = _without(_full_week(week), days[3], "breakfast")
    items = _without(items, days[2], "lunch")
    items.append(_entry(days[2], "lunch", "", slot_state="open",
                        open_reason="I'd rather ask than guess."))
    stub_model(items)

    audit = tools.audit_plan_slots(agent.generate_weekly_plan(week)["weekly_plan_id"])
    assert audit["complete"] is True
    assert audit["duplicated"] == []


def test_filling_a_gap_adds_no_new_distinct_dish(recipes, stub_model):
    """
    GUARD, green on main (main plans nothing there at all). The reason
    the pass can sit after the count pass: copying a dish the week
    already keeps cannot push the household over the number they asked
    for. Pinned by the mutation "fill from a dish that is not on this
    week".

    This household never set its counts, so the count pass stands down
    here. The case where it does NOT was measured outside the suite
    rather than asserted in it (meal_counts_set, breakfasts_per_week 3,
    two distinct breakfasts returned and one slot missed): both running
    orders end with three distinct breakfasts and exactly one picker
    call. See the comment at the call site.
    """
    week = _week_start()
    days = tools._week_dates(week)
    stub_model(_without(_full_week(week), days[3], "breakfast"))

    rows = _rows(agent.generate_weekly_plan(week)["weekly_plan_id"])
    breakfasts = {r["meal"] for r in rows
                  if r["slot"] == "breakfast" and r["slot_state"] == "planned"}
    assert breakfasts == set(BREAKFASTS)


def test_a_skipped_day_keeps_its_answer(recipes, stub_model):
    """
    GUARD, green on main. A day tapped off "Which days?" is planned_empty
    on every meal, and filling one would undo what the household just
    said. Pinned by the mutation "a planned_empty row counts as a gap".
    """
    week = _week_start()
    days = tools._week_dates(week)
    tools.save_week_intake(week, skipped_days=[days[2]])
    stub_model(_without(_full_week(week), days[2], "breakfast"))

    plan = agent.generate_weekly_plan(week)

    assert [r["slot_state"] for r in _at(plan["weekly_plan_id"], days[2], "breakfast")] \
        == ["planned_empty"]


def test_a_household_that_asked_for_no_breakfasts_is_still_sold_none(recipes, stub_model):
    """
    GUARD, green on main, and the weakest thing in this file: NOTHING
    reddens it, and that was measured rather than assumed. Twelve
    mutations were run, including "a planned_empty row counts as a gap"
    both alone and compounded with "fill from a dish that is not on this
    week"; none touches it. It is green STRUCTURALLY — a zero-count slot
    has no planned row anywhere in the week, so the supply is empty and
    the pass stands down at `if not supply` before it ever reaches the
    question about planned_empty rows.

    Kept anyway, and labelled rather than quietly counted as coverage: it
    is the one test that says a household who answered "none, thanks" is
    still sold none. The planned_empty rule it leans on is genuinely
    pinned by the skipped-day test above, which the same mutation reddens.
    """
    tools.set_household_meal_preferences(breakfasts_per_week=0)
    week = _week_start()
    days = tools._week_dates(week)
    stub_model(_without(_full_week(week), days[3], "breakfast"))

    rows = [r for r in _rows(agent.generate_weekly_plan(week)["weekly_plan_id"])
            if r["slot"] == "breakfast"]
    assert {r["slot_state"] for r in rows} == {"planned_empty"}


def test_a_lunch_the_allergen_gate_opened_is_left_as_a_question(recipes):
    """
    CATCH against this branch's own first cut, which filled it — and the
    most useful thing this file records.

    allergen_gate opens a slot when every dish it can find for that meal
    clashes with something somebody in the house can't have, and its
    question SAYS so ("I couldn't find a lunch without pineapple for
    Emily"). The gap audit's question says only that the app couldn't
    settle it. Filling the first is safe and still wrong: it throws away
    a true thing the household needs to know, and
    tests/test_allergen_hard_block.py pins that sentence as Emily's own
    answer. Filling it turned that test red, which is how this was found.
    """
    week = _week_start()
    days = tools._week_dates(week)
    plan_id = _bare_plan(week, [(days[0], "lunch", "Chicken Salad")])
    tools.plan_slot_open(
        weekly_plan_id=plan_id, meal_date=days[1], slot="lunch",
        open_reason="I couldn't find a lunch without pineapple for Emily.",
        derived_from={"constraint": _allergen_gate.ALLERGEN_CONSTRAINT,
                      "dropped": "Pineapple Salsa Bowls", "avoided": "pineapple"},
    )

    out = _meal_variety.fill_gaps_with_a_repeat(plan_id, days[:2])

    assert out["filled"] == []
    assert [r["slot_state"] for r in _at(plan_id, days[1], "lunch")] == ["open"]


def test_the_two_open_slots_are_told_apart_by_one_word(recipes):
    """
    GUARD on the seam above. The word is allergen_gate's own constant, so
    the module that writes it and the module that has to recognise it can
    never drift. Pinned by the mutation "the allergen exclusion is
    dropped", which reddens the test above.
    """
    assert _allergen_gate.ALLERGEN_CONSTRAINT == "allergen"
    from conftest import agent_source
    assert 'derived_from={"constraint": "allergen"' not in agent_source()


# ---------- where it runs ----------

def test_the_fill_runs_once_before_the_gap_audit(recipes):
    """
    SOURCE MARKER. Where it runs is the claim — before the audit and
    before plan_quality.check_and_log, which is where the rule is
    measured — so that is what is asserted. ONCE, deliberately: a second
    call after the allergen sweep was built and taken back out; see the
    allergen test above and the comment at the call site.
    """
    from conftest import agent_function_source
    # Comments stripped: this repo has been bitten three times by a marker
    # a comment could satisfy on its own.
    src = "\n".join(
        line for line in agent_function_source("_finish_week_slots").splitlines()
        if not line.strip().startswith("#")
    )
    fill = src.index("_meal_variety.fill_gaps_with_a_repeat")
    # "early_audit = tools.audit_plan_slots" (the dedupe's own read, much
    # earlier) contains the shorter string, so the gap audit is named by
    # the whole line.
    audit = src.index("\n    audit = tools.audit_plan_slots")
    quality = src.index("plan_quality.check_and_log")

    assert fill < audit < quality
    assert src.count("_meal_variety.fill_gaps_with_a_repeat") == 1
