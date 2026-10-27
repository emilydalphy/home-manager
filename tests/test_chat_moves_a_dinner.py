"""
Chat: "move the shrimp to Wednesday" moves it, leftovers and all (Loop
Board, Emily's phone test 2026-10-26, Tester Home).

Chat's only move was swap_dinner_nights, a two-night trade that refuses
whenever a leftovers night is involved. Refused, the model improvised: it
ticked Tuesday's stew cooked on Monday morning and planned a second fresh
shrimp on Wednesday. These pin the door chat now has (move_meal_to_day —
the Plan tab's own Move, by date and slot) and the two guards that stop the
improvising: no cooked tick on a day still to come, and no second copy of a
dish that is still to be cooked elsewhere.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, main, tools
from app.tools import weekly_plan
from test_move_meal_between_days import (
    FRI, MON, SAT, SUN, THU, TUE, WED, _meal, _recipe, _stew_week,
)


def test_chat_has_the_move_and_the_guarded_doors():
    assert agent.TOOL_FUNCTIONS["move_meal_to_day"] is tools.move_meal_for_chat
    assert agent.TOOL_FUNCTIONS["check_off_meal"] is tools.check_off_meal_for_chat
    assert agent.TOOL_FUNCTIONS["swap_meal_in_plan"] is tools.swap_meal_in_plan_for_chat
    assert any(t["name"] == "move_meal_to_day" for t in agent.TOOL_DEFINITIONS)


def test_a_dinner_moves_forward_with_its_leftovers_and_carries_an_undo():
    w = _stew_week(approve=True)

    out = tools.move_meal_for_chat(w["plan"], MON, THU)

    assert out["status"] == "moved"
    assert out["said"] == "Chicken Stew was moved to Thursday, leftovers to Friday"
    assert _meal(THU)["id"] == w["stew"] and _meal(FRI)["id"] == w["left"]
    assert out["undo"] == {"weekly_plan_id": w["plan"], "week_start": MON, "move_id": out["move_id"]}
    assert "days" not in out
    # Nothing ticked cooked on the way.
    assert all((_meal(d)["cooked_status"] or "pending") != "done" for d in (MON, TUE, WED, THU, FRI, SAT, SUN))


def test_a_dinner_moves_backward_with_its_leftovers():
    w = _stew_week()
    tools.move_meal_for_chat(w["plan"], MON, THU)

    out = tools.move_meal_for_chat(w["plan"], THU, MON)

    assert out["status"] == "moved"
    assert _meal(MON)["id"] == w["stew"] and _meal(TUE)["id"] == w["left"]


def test_moving_onto_its_own_leftovers_night_cooks_there_and_pushes_the_leftovers_on():
    """Emily's case: the dish's leftovers night IS the day asked for. The
    Move sheet dims that day; chat moves the cook there and the leftovers
    on by the same gap."""
    w = _stew_week()

    out = tools.move_meal_for_chat(w["plan"], MON, TUE)

    assert out["status"] == "moved", out
    assert _meal(TUE)["id"] == w["stew"]
    assert _meal(WED)["id"] == w["left"]
    assert _meal(MON)["id"] == w["gnocchi"]
    chains = tools.plan_leftover_chains(w["plan"])
    assert chains["leftovers"][w["left"]]["source"]["date"] == TUE
    # One cook of the stew on the whole week.
    stews = [d for d in (MON, TUE, WED, THU, FRI, SAT, SUN)
             if _meal(d)["meal"] == "Chicken Stew" and _meal(d)["id"] != w["left"]]
    assert stews == [TUE]


def test_a_refused_move_writes_nothing_and_lists_the_days_that_work():
    w = _stew_week()
    before = {d: _meal(d)["id"] for d in (MON, TUE, WED, THU, FRI, SAT, SUN)}

    out = tools.move_meal_for_chat(w["plan"], MON, SUN)  # leftovers would fall off the week

    assert out["status"] == "refused"
    assert "after this week" in out["message"]
    assert THU in [d["date"] for d in out["could_go"]]
    assert SUN not in [d["date"] for d in out["could_go"]]
    assert {d: _meal(d)["id"] for d in before} == before


def test_naming_the_leftovers_night_points_at_the_cook():
    w = _stew_week()

    out = tools.move_meal_for_chat(w["plan"], TUE, FRI)

    assert out["status"] == "refused"
    assert out["message"].startswith("Leftovers move with the meal they come from.")
    assert MON in out["message"]
    assert _meal(TUE)["id"] == w["left"]


@pytest.mark.today("2026-11-04")
def test_the_stew_tonight_instead():
    """'We're having the stew tonight instead' on a Wednesday: Friday's
    stew comes to tonight, tonight's dinner takes Friday, nothing ticked."""
    week = "2026-11-02"
    for name in ("Bean Stew", "Shrimp Tacos"):
        _recipe(name)
    plan = tools.create_weekly_plan(week)["weekly_plan_id"]
    tacos = tools.plan_meal("2026-11-04", "Shrimp Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    stew = tools.plan_meal("2026-11-06", "Bean Stew", slot="dinner", weekly_plan_id=plan)["entry_id"]

    out = tools.move_meal_for_chat(plan, "2026-11-06", "2026-11-04")

    assert out["status"] == "moved"
    assert _meal("2026-11-04")["id"] == stew and _meal("2026-11-06")["id"] == tacos
    assert (_meal("2026-11-04")["cooked_status"] or "pending") != "done"


# ---------------------------------------------------------------- the guards

@pytest.mark.today("2026-11-02")
def test_chat_cannot_tick_a_meal_cooked_before_its_day():
    _recipe("Bean Stew")
    plan = tools.create_weekly_plan("2026-11-02")["weekly_plan_id"]
    tomorrow = tools.plan_meal("2026-11-03", "Bean Stew", slot="dinner", weekly_plan_id=plan)["entry_id"]
    today = tools.plan_meal("2026-11-02", "Bean Stew", slot="dinner", weekly_plan_id=plan)["entry_id"]

    with pytest.raises(weekly_plan.SlotRefused):
        tools.check_off_meal_for_chat(tomorrow, "done")
    assert (_meal("2026-11-03")["cooked_status"] or "pending") != "done"

    tools.check_off_meal_for_chat(today, "done")
    assert _meal("2026-11-02")["cooked_status"] == "done"


def test_chat_cannot_move_a_dinner_by_planning_a_second_copy():
    w = _stew_week()

    with pytest.raises(weekly_plan.SlotRefused) as e:
        tools.swap_meal_in_plan_for_chat(w["plan"], THU, "Sheet-Pan Gnocchi", slot="dinner")
    assert "move_meal_to_day" in str(e.value)
    assert _meal(THU)["meal"] == "Black Bean Tacos"

    # The person's own "have it twice" goes through.
    tools.swap_meal_in_plan_for_chat(w["plan"], THU, "Sheet-Pan Gnocchi", slot="dinner", repeat=True)
    assert _meal(THU)["meal"] == "Sheet-Pan Gnocchi"


def test_a_cooked_dish_and_its_leftovers_night_are_not_a_second_copy():
    """Monday's stew already cooked and Tuesday its leftovers: neither is
    a stew still to be cooked, so Saturday may have the stew again."""
    w = _stew_week()
    tools.check_off_meal(w["stew"], "done")

    tools.swap_meal_in_plan_for_chat(w["plan"], SAT, "Chicken Stew", slot="dinner")

    assert _meal(SAT)["meal"] == "Chicken Stew"


# ---------------------------------------------------------------- the card

def _turn(name, args, result):
    import json
    before = []
    after = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": name, "input": args}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(result)}]},
    ]
    return main.summarize_chat_actions(before, after)


def test_a_chat_move_draws_week_updated_with_an_undo_that_puts_it_back(signed_in):
    w = _stew_week()
    args = {"weekly_plan_id": w["plan"], "meal_date": MON, "to_date": THU}
    out = tools.move_meal_for_chat(**args)

    actions = _turn("move_meal_to_day", args, out)

    assert len(actions) == 1
    card = actions[0]
    assert card.kicker == "Week updated" and card.tab == "week"
    assert card.change == out["said"]
    assert card.date == THU
    assert card.undo["kind"] == "move" and card.undo["payload"] == out["undo"]

    p = card.undo["payload"]
    res = signed_in.post(f"/api/week/{p['week_start']}/move-meal-undo",
                                    json={"move_id": p["move_id"], "weekly_plan_id": p["weekly_plan_id"]})
    assert res.status_code == 200 and res.json()["status"] == "restored"
    assert _meal(MON)["id"] == w["stew"] and _meal(TUE)["id"] == w["left"]


def test_a_refused_chat_move_draws_no_card():
    w = _stew_week()
    args = {"weekly_plan_id": w["plan"], "meal_date": MON, "to_date": SUN}
    assert _turn("move_meal_to_day", args, tools.move_meal_for_chat(**args)) == []
