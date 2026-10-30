"""
A household that stops hosting stops being asked "what's the main?".

When no main can be had for the big meal — a guest's note clashes with the
turkey and the replacement clashes too, or the model is down — the holiday
dinner is handed back as "You're hosting Thanksgiving for 8 — what's the
main?", and the menu records entry_id None (there is no dinner to point
at). Answering "Just us" then went through big_meal.clear_menu, which only
reopened a dinner it had an entry_id for, so the hosting question stayed
on the night of a household no longer hosting anyone. The night is now the
ordinary "Plans for Thanksgiving changed" question — or the trip's
planned_empty if a trip has it.
"""
import json

from app import households, tools
from app.tools import _shared
from tests.test_big_meal import MAIN, _entry, _holiday_tasks, family, proposer, recipes  # noqa: F401
from tests.test_holidays import _shift, _thanksgiving

HOSTING_Q = "what’s the main?"
CHANGED_Q = "Plans for Thanksgiving changed — what would you like for dinner?"


def _hosting_question_after_a_guest_note(proposer) -> int:
    """The reviewer's flow: a standing turkey menu, then a note that rules out every main offered."""
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.answer_holiday(tg, "hosting", headcount=6)
    assert _entry(plan_id, tg)["recipe_name"] == "Herb Roast Turkey"
    proposer["answer"]["main"] = {**MAIN, "name": "Turkey Pot Pie"}
    tools.answer_holiday(tg, "hosting", guest_notes="no turkey")
    row = _entry(plan_id, tg)
    assert row["slot_state"] == "open" and HOSTING_Q in row["open_reason"], "premise: the hosting question"
    return plan_id


def test_just_us_after_the_main_clashed_away_asks_the_ordinary_question(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _hosting_question_after_a_guest_note(proposer)

    tools.answer_holiday(tg, "just_us")

    row = _entry(plan_id, tg)
    assert row["slot_state"] == "open" and row["open_reason"] == CHANGED_Q
    assert not json.loads(row["derived_from_json"] or "{}").get("holiday_menu")
    assert tools.get_big_meal(tg)["hosting"] is False
    assert _holiday_tasks() == []
    audit = tools.audit_plan_slots(plan_id)
    assert audit["duplicated"] == []
    assert {"date": tg, "slot": "dinner"} not in audit["missing"], "the night is a question, never absent"


def test_unsure_after_no_main_could_be_found_asks_the_ordinary_question(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    proposer["answer"]["raise"] = RuntimeError("no API key")
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert HOSTING_Q in _entry(plan_id, tg)["open_reason"]

    tools.answer_holiday(tg, "unsure")

    assert _entry(plan_id, tg)["open_reason"] == CHANGED_Q


def test_a_trip_over_the_hosting_question_keeps_its_empty_night(family, recipes, proposer):
    # Hosting with no count yet, and no main to be had: the hosting
    # question. A trip then takes the night (nobody home) — and leaving
    # hosting must not hand a night nobody is home back as a question.
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    proposer["answer"]["raise"] = RuntimeError("no API key")
    tools.answer_holiday(tg, "hosting")
    assert HOSTING_Q in _entry(plan_id, tg)["open_reason"]
    tools.set_away_stretch(_shift(tg, -1), "dinner", _shift(tg, 1), "lunch")
    assert _entry(plan_id, tg)["slot_state"] == "planned_empty", "premise: the trip has the night"

    tools.answer_holiday(tg, "just_us")

    row = _entry(plan_id, tg)
    assert row["slot_state"] == "planned_empty", "a night nobody is home is never a question"
    assert json.loads(row["derived_from_json"] or "{}").get("need") == "away"
    assert tools.audit_plan_slots(plan_id)["duplicated"] == []


def test_a_main_they_planned_themselves_after_the_question_is_theirs(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _hosting_question_after_a_guest_note(proposer)
    tools.clear_plan_slot(plan_id, tg, "dinner")
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan_id)

    tools.answer_holiday(tg, "just_us")

    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Chili" and row["slot_state"] == "planned"


def test_another_households_hosting_question_is_untouched(family, recipes, proposer):
    tg = _thanksgiving()
    other = households.create_household("Other", "other-pass-not-the-env-1")
    with tools.use_household(other):
        tools.add_member("Sam")
        tools.set_member_age_group("Sam", "adult")
        other_plan = tools.create_weekly_plan(tg)["weekly_plan_id"]
        proposer["answer"]["raise"] = RuntimeError("no API key")
        tools.answer_holiday(tg, "hosting", headcount=4)
        assert HOSTING_Q in _entry(other_plan, tg)["open_reason"]
        proposer["answer"].pop("raise")
    plan_id = _hosting_question_after_a_guest_note(proposer)

    tools.answer_holiday(tg, "just_us")

    assert _entry(plan_id, tg)["open_reason"] == CHANGED_Q
    with tools.use_household(other):
        assert HOSTING_Q in _entry(other_plan, tg)["open_reason"], "another household's night is theirs"
    assert _shared.household_id() == 1


def test_an_open_night_that_is_not_the_hosting_question_is_left_as_worded(family, recipes, proposer):
    # Hosting recorded without building (the Days screen's tap): the night's
    # own open question was never ours, so leaving hosting does not reword it.
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=tg, slot="dinner", open_reason="Your call tonight.")
    tools.answer_holiday(tg, "hosting", headcount=4, build_menu=False)

    tools.answer_holiday(tg, "just_us")

    assert _entry(plan_id, tg)["open_reason"] == "Your call tonight."


def test_just_us_through_the_route_after_the_guest_note(signed_in, family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    assert signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "headcount": 6}).status_code == 200
    proposer["answer"]["main"] = {**MAIN, "name": "Turkey Pot Pie"}
    assert signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "guest_notes": "no turkey"}).status_code == 200
    assert HOSTING_Q in _entry(plan_id, tg)["open_reason"]

    res = signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "just_us"})

    assert res.status_code == 200
    assert _entry(plan_id, tg)["open_reason"] == CHANGED_Q
