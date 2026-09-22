"""
"Each week I plan" numbers are targets, not caps.

Emily, 2026-09-21. Her household: Dinners 4 · Breakfasts 3 · Lunches 3 ·
Snacks a day 2. Her four-day draft (Tue–Fri) came back with 2 distinct
breakfasts, 4 lunches, 2 dinners, 4 snacks. "Why isn't it following the
guidelines we set — fix it."

What this pins (see app/tools/meal_variety.py):

  * the ASSUMPTION, in one place: a per-week count is distinct dishes
    over 7 days, scaled to the days planned and rounded UP — 4 dinners on
    a 4-day plan = ceil(16/7) = 3; snacks a day is per day;
  * the prompt states each count as a target, not a ceiling;
  * the pass after generation enforces all four, both ways: too many
    distinct dishes fold into repeats of the kept ones; too few and the
    repeated nights are re-picked quietly into new dishes; every day gets
    exactly its snacks a day;
  * the opener names the count when a shorter period changed it.

Every test stubs the model (the week) and the picker (a re-pick).
"""
from __future__ import annotations

import datetime
import math

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import draft_opener, meal_variety, swap_in_place as sip
from conftest import prompt_literals


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
            "food_groups": ["protein", "vegetable", "carb"]}


def _days(dates, *, breakfasts, lunches, dinners, snacks) -> list[dict]:
    """One entry per slot per date; `snacks` is a list of lists (per day)."""
    out = []
    for i, date in enumerate(dates):
        out.append(_slot(date, "breakfast", breakfasts[i]))
        out.append(_slot(date, "lunch", lunches[i]))
        out.append(_slot(date, "dinner", dinners[i]))
        for s in snacks[i]:
            out.append(_slot(date, "snack", s))
    return out


def _pick(name: str) -> dict:
    return {
        "meal_name": name, "reason": "something new",
        "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
        "instructions": ["Cook.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 20,
    }


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen
    return _stub


@pytest.fixture
def picker(monkeypatch):
    """A picker that hands out new dishes in order and records what it was asked."""
    calls = []
    names = iter(["Moussaka", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal", "Laksa", "Tagine"])

    def pick(context):
        calls.append(context)
        return _pick(next(names))

    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return calls


@pytest.fixture
def emilys_counts():
    tools.set_household_meal_preferences(dinners_per_week=4, breakfasts_per_week=3, lunches_per_week=3,
                                         snacks_per_day=2, snacks_per_week=7)


def _by_slot(plan_id: int, slot: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in tools.get_weekly_plan(plan_id)["meals"]:
        if m["slot"] == slot and m["slot_state"] == "planned":
            out.setdefault(m["date"], []).append(m["meal"])
    return out


def _distinct(plan_id: int, slot: str) -> set[str]:
    return {n.lower() for names in _by_slot(plan_id, slot).values() for n in names}


# ---------- the assumption, in one place ----------

def test_a_count_scales_to_the_days_planned_and_rounds_up():
    # Emily's case: 4 dinners a week on a 4-day plan.
    assert meal_variety.prorate_meal_count(4, 4) == 3
    assert agent._prorate_meal_count(4, 4) == 3, "agent's name still answers the same"
    assert meal_variety.prorate_meal_count(3, 4) == 2
    assert meal_variety.prorate_meal_count(7, 4) == 4
    assert meal_variety.prorate_meal_count(4, 7) == 4
    assert meal_variety.prorate_meal_count(0, 4) == 0


def test_the_assumption_is_two_knobs(monkeypatch):
    monkeypatch.setattr(meal_variety, "PRORATE_ROUNDING", round)
    assert meal_variety.prorate_meal_count(4, 4) == 2, "nearest instead of up"
    monkeypatch.setattr(meal_variety, "PRORATE_ROUNDING", math.ceil)
    monkeypatch.setattr(meal_variety, "PRORATE_TO_DAYS_PLANNED", False)
    assert meal_variety.prorate_meal_count(4, 4) == 4, "literal: four dinners on four days"
    assert meal_variety.prorate_meal_count(4, 2) == 2, "never more dishes than days"


def test_the_prompt_states_each_count_as_a_target_not_a_ceiling():
    src = prompt_literals(agent.generate_weekly_plan_llm)
    assert "TARGET for distinct dishes" in src
    assert "CEILING" not in src
    assert "too few" in src.lower() and "too many" in src.lower()
    assert "snacks_per_day" in src and "no more, no fewer" in src


# ---------- Emily's four-day draft ----------

def test_emilys_four_day_draft_lands_on_her_numbers(emilys_counts, stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)[1:5]  # Tue–Fri
    stub_model(_days(
        dates,
        breakfasts=["Oats", "Oats", "Eggs", "Eggs"],                  # 2 distinct — the target on 4 days
        lunches=["Wrap", "Soup", "Salad", "Sandwich"],                # 4 distinct — 2 too many
        dinners=["Chili", "Chili", "Tacos", "Tacos"],                 # 2 distinct — 1 short
        snacks=[["Apple"], ["Apple", "Nuts"], ["Apple", "Nuts", "Yogurt"], ["Carrots", "Nuts"]],
    ))

    plan = agent.generate_weekly_plan(week, day_count=4, period_start=dates[0])
    plan_id = plan["weekly_plan_id"]

    # The targets the model was handed are the scaled ones.
    assert len(_distinct(plan_id, "breakfast")) == 2
    assert len(_distinct(plan_id, "lunch")) == 2, "two extras folded into repeats"
    assert len(_distinct(plan_id, "dinner")) == 3, "a repeated night re-picked into a third dish"
    assert "moussaka" in _distinct(plan_id, "dinner")
    # Lunches: the two kept are the earliest; the later two now repeat them.
    lunches = _by_slot(plan_id, "lunch")
    assert lunches[dates[0]] == ["Wrap"] and lunches[dates[1]] == ["Soup"]
    assert lunches[dates[2]][0] in ("Wrap", "Soup") and lunches[dates[3]][0] in ("Wrap", "Soup")
    # Snacks a day is exact: two on every day, different from each other.
    snacks = _by_slot(plan_id, "snack")
    assert all(len(snacks[d]) == 2 for d in dates), snacks
    assert all(len({n.lower() for n in snacks[d]}) == 2 for d in dates)
    assert snacks[dates[2]] == ["Apple", "Nuts"], "the third snack went; the first two stayed"
    # The one dinner re-pick was told why, with the week's dishes on avoid.
    dinner_picks = [c for c in picker if c["slot"] == "dinner"]
    assert len(dinner_picks) == 1
    assert "three dinners" in dinner_picks[0]["replacing_because"]
    assert {"Chili", "Tacos"} <= set(dinner_picks[0]["avoid"])
    # The snack gap was filled from the week's own snacks — no call spent.
    assert not any(c["slot"] == "snack" for c in picker)
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    # And the opener says the count once: line 1 already has "three
    # dinners across four nights", so the count note stays unsaid.
    lines = tools.get_week_menu(plan_id)["draft_opener"]
    assert lines[0] == "An ordinary week — three dinners across four nights."
    assert not any("day plan" in line for line in lines)
    # The folded lunches say the true number, spelt right.
    lunch_rows = [m for m in tools.get_weekly_plan(plan_id)["meals"] if m["slot"] == "lunch" and m["date"] in dates[2:]]
    assert {m["reasoning"] for m in lunch_rows} == {"On again — three lunches a week, scaled to a four-day plan"}


def test_the_model_is_handed_the_scaled_targets(emilys_counts, stub_model):
    week = _monday()
    dates = tools._week_dates(week)[1:5]
    seen = stub_model(_days(dates, breakfasts=["Oats"] * 4, lunches=["Wrap"] * 4, dinners=["Chili"] * 4,
                            snacks=[["Apple", "Nuts"]] * 4))
    agent.generate_weekly_plan(week, day_count=4, period_start=dates[0])
    memory = seen["ctx"]["household_memory"]
    assert (memory["dinners_per_week"], memory["breakfasts_per_week"], memory["lunches_per_week"]) == (3, 2, 2)
    assert memory["snacks_per_day"] == 2, "per day, never scaled"


# ---------- a full week, both directions ----------

def test_a_seven_day_week_too_few_dinners_are_repicked_and_too_many_breakfasts_fold(emilys_counts, stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)
    stub_model(_days(
        dates,
        breakfasts=["Oats", "Eggs", "Toast", "Granola", "Pancakes", "Oats", "Eggs"],   # 5 against 3
        lunches=["Wrap", "Soup", "Salad", "Wrap", "Soup", "Salad", "Wrap"],            # 3: right
        dinners=["Chili", "Tacos", "Chili", "Tacos", "Chili", "Tacos", "Chili"],       # 2 against 4
        snacks=[["Apple", "Nuts"]] * 7,
    ))

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert _distinct(plan_id, "breakfast") == {"oats", "eggs", "toast"}
    assert len(_distinct(plan_id, "dinner")) == 4
    assert {"chili", "tacos"} <= _distinct(plan_id, "dinner")
    assert len([c for c in picker if c["slot"] == "dinner"]) == 2
    assert len(_distinct(plan_id, "lunch")) == 3 and not any(c["slot"] == "lunch" for c in picker)
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    # A full week: nothing to say about the counts.
    assert all("day plan" not in line for line in tools.get_week_menu(plan_id)["draft_opener"])


def test_a_night_they_asked_for_by_name_is_never_the_one_repicked(emilys_counts, stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)
    days = _days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                 dinners=["Chili"] * 7, snacks=[["Apple", "Nuts"]] * 7)
    for d in days:
        if d["slot"] == "dinner" and d["date"] in dates[:2]:
            d["derived_from"] = {"freeform": "chili please"}
    stub_model(days)
    plan = agent.generate_weekly_plan(week)
    dinners = _by_slot(plan["weekly_plan_id"], "dinner")
    assert dinners[dates[0]] == ["Chili"] and dinners[dates[1]] == ["Chili"]
    # Chili is protected as a dish (a night asked for by name protects the
    # dish), so the dinner count stays short and the week stands; the
    # breakfasts and lunches, which nobody asked for by name, were filled up.
    assert not any(c["slot"] == "dinner" for c in picker)
    assert _distinct(plan["weekly_plan_id"], "dinner") == {"chili"}
    assert len(_distinct(plan["weekly_plan_id"], "lunch")) == 3
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_when_the_budget_runs_dry_the_week_stands_with_fewer(emilys_counts, stub_model, monkeypatch):
    week = _monday()
    dates = tools._week_dates(week)
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                     dinners=["Chili"] * 7, snacks=[["Apple", "Nuts"]] * 7))
    calls = []
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: (calls.append(ctx), _pick("Chili"))[1])
    plan = agent.generate_weekly_plan(week)
    from app.tools import allergen_gate
    assert len(calls) <= allergen_gate.MAX_REPICK_CALLS
    assert all(m["slot_state"] == "planned" for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"])
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_week_that_names_its_own_count_is_left_alone(emilys_counts, stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, freeform="just two dinners this week, we're out a lot")
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                     dinners=["Chili", "Tacos"] * 3 + ["Chili"], snacks=[["Apple", "Nuts"]] * 7))
    plan = agent.generate_weekly_plan(week)
    assert len(_distinct(plan["weekly_plan_id"], "dinner")) == 2
    assert not any(c["slot"] == "dinner" for c in picker)


# ---------- a household that never said ----------

def test_column_defaults_are_never_a_floor(stub_model, picker):
    """No counts set: 7/7/7 are column defaults, and snacks unanswered.
    Nothing is re-picked and no snack is added or removed."""
    week = _monday()
    dates = tools._week_dates(week)
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                     dinners=["Chili"] * 7, snacks=[["Apple"]] * 7))
    plan = agent.generate_weekly_plan(week)
    assert picker == []
    assert all(len(v) == 1 for v in _by_slot(plan["weekly_plan_id"], "snack").values())
    assert tools.get_household_memory()["meal_counts_set"] is False


def test_an_answered_count_sets_the_flag_and_the_backfill_reads_old_rows():
    tools.set_household_meal_preferences(dinners_per_week=4)
    assert tools.get_household_memory()["meal_counts_set"] is True
    # An older row: counts not all 7, flag never written.
    conn = get_conn()
    conn.execute("UPDATE meal_preferences SET meal_counts_set = 0")
    conn.commit()
    from app import db
    db._backfill_meal_counts_set(conn)
    conn.commit()
    assert conn.execute("SELECT meal_counts_set FROM meal_preferences").fetchone()[0] == 1
    conn.execute("UPDATE meal_preferences SET meal_counts_set = 0, dinners_per_week = 7")
    conn.commit()
    db._backfill_meal_counts_set(conn)
    conn.commit()
    assert conn.execute("SELECT meal_counts_set FROM meal_preferences").fetchone()[0] == 0, "7/7/7 stays a guess"
    conn.close()


# ---------- snacks a day, on its own ----------

def test_a_day_short_a_snack_borrows_one_that_repeats_nothing_that_day(emilys_counts, stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)
    snacks = [["Apple", "Nuts"]] * 6 + [["Apple"]]
    days = _days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                 dinners=["Chili", "Tacos", "Salmon", "Chili", "Tacos", "Salmon", "Nuts"], snacks=snacks)
    stub_model(days)
    plan = agent.generate_weekly_plan(week)
    sunday = _by_slot(plan["weekly_plan_id"], "snack")[dates[6]]
    # Nuts is Sunday's dinner, so it can't be Sunday's snack; the week has
    # nothing else, so one small call picks something new.
    assert len(sunday) == 2 and "Nuts" not in sunday
    snack_picks = [c for c in picker if c["slot"] == "snack"]
    assert len(snack_picks) == 1 and "Nuts" in snack_picks[0]["avoid"] and "Apple" in snack_picks[0]["avoid"]


def test_the_count_note_names_dinners_first_then_the_first_meal_that_differs():
    memory = {"dinners_per_week": 4, "breakfasts_per_week": 3, "lunches_per_week": 3}
    assert draft_opener.count_note(4, memory) == "Three dinners this week, not four — it’s a four-day plan."
    assert draft_opener.count_note(7, memory) == ""
    assert draft_opener.count_note(4, {"dinners_per_week": 7, "breakfasts_per_week": 3, "lunches_per_week": 7}) == \
        "Four dinners this week, not seven — it’s a four-day plan."
    assert draft_opener.count_note(2, {"dinners_per_week": 1, "lunches_per_week": 3, "breakfasts_per_week": 1}) == \
        "One lunch this week, not three — it’s a two-day plan."
    assert draft_opener.count_note(4, None) == ""
    # Said once: when line 1 already states the count, the note stays unsaid.
    assert draft_opener.count_note(4, memory, said="An ordinary week — three dinners across four nights.") == ""
    assert draft_opener.count_note(4, memory, said="Mexican lunches, as you asked.") == \
        "Three dinners this week, not four — it’s a four-day plan."


# ---------- the verifier's round (2026-09-21) ----------

def test_the_repeat_line_says_the_true_number_and_spells_it_right():
    assert meal_variety.repeat_reason(3, "lunch") == "On again — you asked for three lunches a week"
    assert meal_variety.repeat_reason(2, "lunch", usual=3, day_count=4) == \
        "On again — three lunches a week, scaled to a four-day plan"
    assert meal_variety.repeat_reason(1, "dinner", usual=1, day_count=4) == "On again — you asked for one dinner a week"
    assert meal_variety.repeat_reason(2, "dish", usual=2) == "On again — you asked for two dishes a week"


def test_a_folded_repeat_never_lands_a_long_dish_on_a_rush_night(stub_model, picker):
    """Sunday tagged rush holds a 30-minute stir fry; the household asked for
    two dinners. The braise is the older dish, but the stir fry is the one
    the rush night needs — it stays, and the braise never lands there."""
    tools.set_household_meal_preferences(dinners_per_week=2)
    week = _monday()
    dates = tools._week_dates(week)
    tools.add_recipe("Slow braise", ingredients=[{"item": "beef", "qty": "2 lb"}],
                     prep_time_minutes=20, cook_time_minutes=100, food_groups=["protein", "vegetable", "carb"])
    tools.add_recipe("Quick stir fry", ingredients=[{"item": "chicken", "qty": "1 lb"}],
                     prep_time_minutes=5, cook_time_minutes=10, food_groups=["protein", "vegetable", "carb"])
    tools.add_recipe("Roast", ingredients=[{"item": "lamb", "qty": "2 lb"}],
                     prep_time_minutes=15, cook_time_minutes=90, food_groups=["protein", "vegetable", "carb"])
    tools.save_week_intake(week, night_tags={dates[6]: ["rush"]})
    days = _days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                 dinners=["Slow braise", "Roast", "Slow braise", "Roast", "Slow braise", "Roast", "Quick stir fry"],
                 snacks=[["Apple", "Nuts"]] * 7)
    for d in days:
        if d["slot"] == "dinner":
            d["is_new_recipe"] = False
    stub_model(days)

    plan = agent.generate_weekly_plan(week)
    dinners = _by_slot(plan["weekly_plan_id"], "dinner")
    assert dinners[dates[6]] == ["Quick stir fry"], "the rush night keeps its quick dish"
    assert len(_distinct(plan["weekly_plan_id"], "dinner")) == 2
    assert "roast" not in _distinct(plan["weekly_plan_id"], "dinner"), "the latest long dish is the one that went"


def test_a_rush_night_no_kept_dish_fits_is_left_as_generated():
    caps = {"2026-10-05": 20}
    braise = {"name": "Braise", "nights": [{"date": "2026-10-01", "id": 1}], "protected": False, "chained": False,
              "food_groups": None, "minutes": 120}
    assert meal_variety._spread_pick([braise], "2026-10-05", caps["2026-10-05"]) is None
    quick = dict(braise, name="Quick", minutes=15, nights=[{"date": "2026-10-02", "id": 2}])
    assert meal_variety._spread_pick([braise, quick], "2026-10-05", 20) is quick
    unknown = dict(braise, name="Unknown", minutes=None, nights=[{"date": "2026-10-03", "id": 3}])
    assert meal_variety._spread_pick([braise, unknown], "2026-10-05", 20) is unknown, "unknown minutes can't be judged"


@pytest.mark.parametrize("text,stands_down", [
    ("just two dinners this week", {"dinner"}),
    ("just two lunches this week", {"lunch"}),
    ("three breakfasts this week please", {"breakfast"}),
    ("only 4 meals this week", {"dinner", "lunch", "breakfast", "snack"}),
    ("five different dishes", {"dinner", "lunch", "breakfast", "snack"}),
    ("just three snacks this week", {"snack"}),
    ("two snacks a day", set()),
    ("we are 5 for dinner", set()),
    ("under 30 minutes for dinner", set()),
])
def test_a_typed_count_stands_down_its_own_slot_only(text, stands_down):
    got = {slot for slot in ("dinner", "lunch", "breakfast", "snack") if meal_variety.asks_for_a_count(text, slot=slot)}
    assert got == stands_down


def test_a_typed_dinner_count_leaves_the_lunches_held_to_their_number(emilys_counts, stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, freeform="just two dinners this week, we're out a lot")
    stub_model(_days(dates, breakfasts=["Oats", "Eggs", "Toast", "Oats", "Eggs", "Toast", "Oats"],
                     lunches=["Wrap", "Soup", "Salad", "Sandwich", "Wrap", "Soup", "Salad"],   # 4 against 3
                     dinners=["Chili", "Tacos"] * 3 + ["Chili"], snacks=[["Apple", "Nuts"]] * 7))
    plan = agent.generate_weekly_plan(week)
    assert len(_distinct(plan["weekly_plan_id"], "dinner")) == 2, "their own words win for dinners"
    assert len(_distinct(plan["weekly_plan_id"], "lunch")) == 3, "the lunches still fold to three"


def test_a_slot_left_at_the_default_seven_is_never_filled_up(stub_model, picker):
    """She set dinners to 4 and never touched breakfasts: 7 breakfasts is
    the column default, and the model is not sent chasing seven distinct
    ones — but the four dinners are hers, and short means re-picked."""
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    dates = tools._week_dates(week)
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                     dinners=["Chili", "Tacos"] * 3 + ["Chili"], snacks=[["Apple", "Nuts"]] * 7))
    plan = agent.generate_weekly_plan(week)
    assert {c["slot"] for c in picker} == {"dinner"}
    assert len(_distinct(plan["weekly_plan_id"], "dinner")) == 4
    assert _distinct(plan["weekly_plan_id"], "breakfast") == {"oats"}
