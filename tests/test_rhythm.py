"""
Household rhythm — Loop Board "Onboarding: household rhythm without
traditional assumptions". Covers the structured rhythm tools (set/get,
weekday overrides, corrections), the completeness reweighting that puts
rhythm at the top of the learning hierarchy (rhythm -> habits ->
preferences), the preference_events growth-counter logging, and the
rhythm-derived packed-lunch suggestion in the week intake prefill.
"""
import datetime

import pytest

from app import agent, tools


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _full_week(week: str, meal: str = "Chili") -> list[dict]:
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False, "reasoning": "fits the week"}
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


# ---------- lunch location: standing answer, weekday overrides, corrections ----------

def test_lunch_location_standing_and_weekday_override():
    tools.set_lunch_location("Marcus", "home")
    assert tools.effective_lunch_location("Marcus", "Tuesday") == "home"

    # "Marcus is in the office Tuesdays now" — Emily's exact example.
    tools.set_lunch_location("Marcus", "out", weekday="Tuesday")
    assert tools.effective_lunch_location("Marcus", "Tuesday") == "out"
    assert tools.effective_lunch_location("Marcus", "Wednesday") == "home", "the standing answer is untouched"


def test_calling_set_lunch_location_again_is_how_a_correction_works():
    tools.set_lunch_location("Marcus", "out", weekday="Tuesday")
    tools.set_lunch_location("Marcus", "varies", weekday="Tuesday")
    assert tools.effective_lunch_location("Marcus", "Tuesday") == "varies"


def test_clear_lunch_location_override_reverts_to_standing():
    tools.set_lunch_location("Marcus", "home")
    tools.set_lunch_location("Marcus", "out", weekday="Tuesday")
    tools.clear_lunch_location_override("Marcus", "Tuesday")
    assert tools.effective_lunch_location("Marcus", "Tuesday") == "home"


def test_effective_lunch_location_is_none_when_never_set():
    assert tools.effective_lunch_location("Nobody", "Monday") is None


def test_set_lunch_location_validates_inputs():
    with pytest.raises(ValueError):
        tools.set_lunch_location("Marcus", "sometimes")
    with pytest.raises(ValueError):
        tools.set_lunch_location("", "home")
    with pytest.raises(ValueError):
        tools.set_lunch_location("Marcus", "home", weekday="Someday")
    with pytest.raises(ValueError):
        tools.clear_lunch_location_override("Marcus", "")


# ---------- meals_together / cooking_role ----------

def test_set_meals_together_round_trips_and_validates():
    result = tools.set_meals_together("dinner_only")
    assert result == {"meals_together": "dinner_only"}
    assert tools.get_household_rhythm()["meals_together"] == "dinner_only"
    with pytest.raises(ValueError):
        tools.set_meals_together("every_meal_ever")


def test_set_cooking_role_requires_who_for_one_person_and_clears_it_otherwise():
    with pytest.raises(ValueError):
        tools.set_cooking_role("one_person")

    result = tools.set_cooking_role("one_person", who="Emily")
    assert result == {"cooking_role": "one_person", "who": "Emily"}

    turns = tools.set_cooking_role("turns", who="Emily")  # who is ignored/cleared for non-one_person values
    assert turns == {"cooking_role": "turns", "who": None}
    assert tools.get_household_rhythm()["cooking_role"] == {"value": "turns", "who": None}


def test_get_household_rhythm_shape():
    tools.set_lunch_location("Marcus", "home")
    tools.set_lunch_location("Marcus", "out", weekday="Tuesday")
    tools.set_meals_together("dinner_only")
    tools.set_cooking_role("one_person", who="Emily")

    rhythm = tools.get_household_rhythm()
    assert rhythm["lunch_location"]["Marcus"]["standing"] == "home"
    assert rhythm["lunch_location"]["Marcus"]["overrides"] == {"Tuesday": "out"}
    assert rhythm["meals_together"] == "dinner_only"
    assert rhythm["cooking_role"] == {"value": "one_person", "who": "Emily"}


# ---------- the three rhythm facts locked afterward: dinner_window, planning_anchor, leftovers_stance ----------

def test_set_dinner_window_round_trips_and_validates():
    result = tools.set_dinner_window("6_8")
    assert result == {"dinner_window": "6_8"}
    assert tools.get_household_rhythm()["dinner_window"] == "6_8"
    with pytest.raises(ValueError):
        tools.set_dinner_window("whenever")


def test_set_planning_anchor_round_trips_and_validates():
    result = tools.set_planning_anchor("sunday_before")
    assert result == {"planning_anchor": "sunday_before"}
    assert tools.get_household_rhythm()["planning_anchor"] == "sunday_before"
    with pytest.raises(ValueError):
        tools.set_planning_anchor("whenever")


def test_set_leftovers_stance_round_trips_and_validates():
    result = tools.set_leftovers_stance("love_them")
    assert result == {"leftovers_stance": "love_them"}
    assert tools.get_household_rhythm()["leftovers_stance"] == "love_them"
    with pytest.raises(ValueError):
        tools.set_leftovers_stance("whenever")


def test_get_household_rhythm_includes_all_six_locked_facts():
    tools.set_lunch_location("Marcus", "home")
    tools.set_meals_together("dinner_only")
    tools.set_cooking_role("turns")
    tools.set_dinner_window("later")
    tools.set_planning_anchor("midweek")
    tools.set_leftovers_stance("fine_sometimes")

    rhythm = tools.get_household_rhythm()
    assert rhythm["dinner_window"] == "later"
    assert rhythm["planning_anchor"] == "midweek"
    assert rhythm["leftovers_stance"] == "fine_sometimes"


def test_new_rhythm_writes_are_also_logged_to_preference_events():
    before = tools.count_preference_events_this_month()
    tools.set_dinner_window("6_8")
    tools.set_planning_anchor("as_we_go")
    tools.set_leftovers_stance("fresh_each_night")
    after = tools.count_preference_events_this_month()
    assert after == before + 3


def test_rhythm_completeness_signals_include_the_three_new_facts():
    signals = tools.rhythm_completeness_signals()
    assert signals["dinner_window_set"] is False
    assert signals["planning_anchor_set"] is False
    assert signals["leftovers_stance_set"] is False

    tools.set_dinner_window("5_6ish")
    tools.set_planning_anchor("sunday_before")
    tools.set_leftovers_stance("love_them")

    signals = tools.rhythm_completeness_signals()
    assert signals["dinner_window_set"] is True
    assert signals["planning_anchor_set"] is True
    assert signals["leftovers_stance_set"] is True


def test_the_three_new_rhythm_facts_count_toward_completeness():
    completeness = tools._build_context_completeness(
        members=[], protein_preferences={}, cuisine_preferences=[], dislikes=[],
        cooking_time_preference="", usual_stores=[], eating_style="", goals="",
        recipes_rated=0, meals_cooked=0,
    )
    missing_keys = {m["key"] for m in completeness["missing"]}
    assert {"dinner_window", "planning_anchor", "leftovers_stance"} <= missing_keys

    completeness = tools._build_context_completeness(
        members=[], protein_preferences={}, cuisine_preferences=[], dislikes=[],
        cooking_time_preference="", usual_stores=[], eating_style="", goals="",
        recipes_rated=0, meals_cooked=0,
        rhythm_dinner_window_set=True, rhythm_planning_anchor_set=True, rhythm_leftovers_stance_set=True,
    )
    missing_keys = {m["key"] for m in completeness["missing"]}
    assert not ({"dinner_window", "planning_anchor", "leftovers_stance"} & missing_keys)


# ---------- preference_events growth counter (Loop Board item E) ----------

def test_rhythm_writes_are_logged_to_preference_events():
    before = tools.count_preference_events_this_month()
    tools.set_lunch_location("Emily", "home")
    tools.set_lunch_location("Emily", "out", weekday="Tuesday")
    tools.set_meals_together("most_meals")
    tools.set_cooking_role("turns")
    after = tools.count_preference_events_this_month()
    assert after == before + 4


def test_clearing_an_override_is_also_logged():
    tools.set_lunch_location("Emily", "out", weekday="Tuesday")
    before = tools.count_preference_events_this_month()
    tools.clear_lunch_location_override("Emily", "Tuesday")
    assert tools.count_preference_events_this_month() == before + 1


# ---------- completeness reweighting: rhythm -> habits -> preferences ----------

def test_rhythm_signals_rank_above_every_other_signal():
    rhythm_keys = {"lunch_location", "meals_together", "cooking_role"}
    rhythm_weights = [w for k, _, _, w in tools._CONTEXT_SIGNALS if k in rhythm_keys]
    other_weights = [w for k, _, _, w in tools._CONTEXT_SIGNALS if k not in rhythm_keys]
    assert len(rhythm_weights) == 3
    assert min(rhythm_weights) > max(other_weights), (
        "every rhythm signal must outweigh every non-rhythm signal, per Emily's "
        "rhythm -> habits -> preferences hierarchy"
    )


def test_an_empty_household_sees_a_rhythm_gap_surfaced_first():
    completeness = tools._build_context_completeness(
        members=[], protein_preferences={}, cuisine_preferences=[], dislikes=[],
        cooking_time_preference="", usual_stores=[], eating_style="", goals="",
        recipes_rated=0, meals_cooked=0,
    )
    assert completeness["missing"][0]["key"] == "lunch_location"


def test_get_household_memory_wires_rhythm_into_completeness_and_its_own_payload():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.set_lunch_location("Emily", "home")
    tools.set_meals_together("most_meals")
    tools.set_cooking_role("turns")

    memory = tools.get_household_memory()
    assert memory["rhythm"]["meals_together"] == "most_meals"
    missing_keys = {m["key"] for m in memory["context_completeness"]["missing"]}
    assert "meals_together" not in missing_keys
    assert "cooking_role" not in missing_keys
    # Emily is the only adult and has answered — lunch_location is satisfied too.
    assert "lunch_location" not in missing_keys


def test_rhythm_completeness_requires_every_adult_not_just_one():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Marcus")
    tools.set_member_age_group("Marcus", "adult")

    assert tools.rhythm_completeness_signals()["lunch_location_set"] is False

    tools.set_lunch_location("Emily", "home")
    assert tools.rhythm_completeness_signals()["lunch_location_set"] is False, "Marcus hasn't answered yet"

    tools.set_lunch_location("Marcus", "out")
    assert tools.rhythm_completeness_signals()["lunch_location_set"] is True


# ---------- rhythm feeds generation defaults (week intake prefill) ----------

def test_week_intake_prefill_suggests_packed_lunches_from_rhythm():
    week = _week_start()
    tools.set_lunch_location("Emily", "out")

    prefill = tools.get_week_intake_prefill(week)
    suggestions = prefill["rhythm_packed_lunch_suggestions"]
    assert len(suggestions) == 7
    assert all(s["suggested_packed"] for s in suggestions)
    assert all(s["out"] == ["Emily"] for s in suggestions)


def test_week_intake_prefill_suggests_nothing_without_rhythm():
    week = _week_start()
    prefill = tools.get_week_intake_prefill(week)
    assert prefill["rhythm_packed_lunch_suggestions"] == []


def test_a_mixed_household_is_not_forced_to_one_side():
    week = _week_start()
    tools.set_lunch_location("Emily", "out")
    tools.set_lunch_location("Marcus", "home")

    prefill = tools.get_week_intake_prefill(week)
    monday = next(s for s in prefill["rhythm_packed_lunch_suggestions"] if s["date"] == week)
    assert monday["suggested_packed"] is False
    assert monday["out"] == ["Emily"]
    assert monday["home"] == ["Marcus"]


def test_a_weekday_override_changes_just_that_days_suggestion():
    week = _week_start()
    dates = tools._week_dates(week)
    tools.set_lunch_location("Emily", "home")
    tuesday = next(d for d in dates if datetime.date.fromisoformat(d).strftime("%A") == "Tuesday")
    tools.set_lunch_location("Emily", "out", weekday="Tuesday")

    prefill = tools.get_week_intake_prefill(week)
    by_date = {s["date"]: s for s in prefill["rhythm_packed_lunch_suggestions"]}
    assert by_date[tuesday]["suggested_packed"] is True
    monday = dates[0]
    assert by_date[monday]["suggested_packed"] is False


# ---------- generation defaults: onboarding's first week, no intake yet ----------

def test_generation_falls_back_to_rhythm_when_no_intake_exists_yet(monkeypatch):
    """
    Onboarding's very first week has no week_intake row at all. Rhythm
    should still feed the existing packed_lunch_days mechanism (see
    agent._rhythm_only_generation_context) so "out lunches = packable"
    holds even before the household has ever opened the intake screens.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    week = _week_start()
    tools.set_lunch_location("Emily", "out")  # standing answer covers every weekday

    seen = {}

    def _fake(context):
        seen["context"] = context
        return _full_week(week)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)

    agent.generate_weekly_plan(week)  # no intake_id, and no week_intake row was ever saved

    assert seen["context"]["intake"] is not None
    assert week in seen["context"]["intake"]["packed_lunch_days"]


def test_generation_leaves_intake_context_none_with_neither_intake_nor_rhythm(monkeypatch):
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    week = _week_start()

    seen = {}

    def _fake(context):
        seen["context"] = context
        return _full_week(week)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)

    agent.generate_weekly_plan(week)

    assert seen["context"]["intake"] is None
