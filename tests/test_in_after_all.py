"""
"We're in after all" on a night the week's answers marked Out (Emily's pick
#31, 2026-10-26).

Before: Plan offered "Swap · I'll pick" and "Add a meal" on an Out night,
and Swap failed ("That didn't work just now") — there was no dish to swap.
Now the Out night offers one action that puts it back to normal: the `out`
tag comes off the week's answers and Pomona picks a dinner through the
swap's own picker. And the week menu no longer calls an "asked for no
breakfasts" slot "Out — nothing to cook": people are home, so it is
"Not planned".

The week model and the picker are stubbed; nothing reaches the network.
"""
from __future__ import annotations

import re

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import swap_in_place

from test_rush_cap_enforced import _filler, _monday, _recipe, _week, stub_model  # noqa: F401


def _dinner_rows(plan_id: int, day: str) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, slot_state, open_reason, derived_from_json, recipe_id, freeform_meal FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner' ORDER BY id", (plan_id, day))]
    conn.close()
    return rows


def _menu_dinner(plan_id: int, day: str) -> dict:
    menu = tools.get_week_menu(plan_id)
    return next(d for d in menu["days"] if d["date"] == day)["dinner"]


@pytest.fixture
def out_week(stub_model):
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Quick Eggs", 10)
    tools.save_week_intake(week, night_tags={dates[3]: ["out"], dates[1]: ["rush"]})
    stub_model(_week(dates, ["Quick Eggs"] * 7))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    return week, dates, plan_id


def _picker(name="Lemon Chicken"):
    calls = []

    def _pick(context):
        calls.append(context)
        return {"meal_name": name, "reason": "you're home after all",
                "ingredients": [{"item": "chicken", "qty": "1 lb", "category": "meat/seafood"}],
                "instructions": ["Cook the chicken.", "Serve it."],
                "food_groups": ["protein"], "prep_time_minutes": 5, "cook_time_minutes": 20}
    _pick.calls = calls
    return _pick


def test_an_out_night_is_marked_for_the_action_and_nothing_else_is(out_week):
    week, dates, plan_id = out_week
    out = _menu_dinner(plan_id, dates[3])
    assert out["state"] == "planned_empty" and out["title"] == "Out — nothing to cook"
    assert out.get("in_after_all") is True
    assert not _menu_dinner(plan_id, dates[2]).get("in_after_all")


def test_were_in_after_all_plans_the_night_and_takes_the_tag_off(out_week):
    week, dates, plan_id = out_week
    pick = _picker()

    result = swap_in_place.in_after_all(plan_id, dates[3], picker=pick)

    assert result["status"] == "filled"
    assert result["meal"] == "Lemon Chicken"
    rows = _dinner_rows(plan_id, dates[3])
    assert len(rows) == 1 and rows[0]["slot_state"] == "planned"
    tags = tools.get_week_intake(week)["night_tags"]
    assert dates[3] not in tags, "the out tag is still on the night"
    assert tags[dates[1]] == ["rush"], "another night's answer was lost"
    # The picker was asked for a night people are home, not an out night.
    assert "out" not in (pick.calls[0].get("night_tags") or [])
    assert result["day"]["dinner"]["state"] == "planned"


def test_with_no_dish_the_night_comes_back_as_a_dinner_to_decide(out_week):
    week, dates, plan_id = out_week

    result = swap_in_place.in_after_all(plan_id, dates[3], picker=lambda context: {})

    assert result["status"] == "opened"
    rows = _dinner_rows(plan_id, dates[3])
    assert len(rows) == 1 and rows[0]["slot_state"] == "open"
    assert rows[0]["open_reason"] == swap_in_place.IN_AFTER_ALL_OPEN_REASON
    assert dates[3] not in tools.get_week_intake(week)["night_tags"]


def test_a_night_that_is_not_out_is_refused_and_left_alone(out_week):
    week, dates, plan_id = out_week
    before = _dinner_rows(plan_id, dates[2])

    result = swap_in_place.in_after_all(plan_id, dates[2], picker=_picker())

    assert result == {"status": "refused", "message": swap_in_place.IN_AFTER_ALL_NOT_OUT}
    assert _dinner_rows(plan_id, dates[2]) == before


def test_a_second_tap_is_refused(out_week):
    week, dates, plan_id = out_week
    swap_in_place.in_after_all(plan_id, dates[3], picker=_picker())

    again = swap_in_place.in_after_all(plan_id, dates[3], picker=_picker("Other"))

    assert again["status"] == "refused"
    assert len(_dinner_rows(plan_id, dates[3])) == 1


def test_the_route(out_week, signed_in, monkeypatch):
    week, dates, plan_id = out_week
    monkeypatch.setattr(swap_in_place, "_pick_replacement", _picker())

    res = signed_in.post(f"/api/week/{week}/in-after-all", json={"date": dates[3], "weekly_plan_id": plan_id})

    assert res.status_code == 200
    assert res.json()["status"] == "filled"


def test_a_no_breakfasts_slot_is_not_called_out(stub_model):
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Quick Eggs", 10)
    stub_model(_week(dates, ["Quick Eggs"] * 7))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    # Written the way generation's zero-count pass writes it.
    tools.clear_plan_slot(plan_id, dates[0], "breakfast")
    tools.plan_slot_empty(weekly_plan_id=plan_id, meal_date=dates[0], slot="breakfast",
                          reason="You’ve asked me not to plan breakfasts — I’ve left this to you.",
                          derived_from={"constraint": "breakfasts_per_week:0"})

    menu = tools.get_week_menu(plan_id)
    breakfast = next(d for d in menu["days"] if d["date"] == dates[0])["breakfast"]
    assert breakfast["title"] == "Not planned"
    assert not breakfast.get("in_after_all")


# ---------- the screen ----------

SHELL = open("static/shell.js", encoding="utf-8").read()


def _fn(name: str) -> str:
    start = SHELL.index(f"function {name}(")
    i = SHELL.index("{", start)
    depth = 0
    for j in range(i, len(SHELL)):
        if SHELL[j] == "{":
            depth += 1
        elif SHELL[j] == "}":
            depth -= 1
            if depth == 0:
                return SHELL[start:j + 1]
    raise AssertionError(name)


def test_an_empty_slot_offers_no_swap_and_an_out_night_offers_were_in():
    body = _fn("slotActionsHtml")
    empty = body[body.index("entry.state === 'planned_empty'"):]
    empty = empty[:empty.index("if (!entry && !day.isPast)")]
    assert "swap" not in re.sub(r"//.*", "", empty).replace("Swap", ""), "an empty slot still offers Swap"
    assert "data-wk-in-after-all" in empty and "IN_AFTER_ALL_LABEL" in empty
    assert "var IN_AFTER_ALL_LABEL = 'We’re in after all';" in SHELL


def test_add_a_meal_skips_an_out_night_and_the_tap_posts_the_route():
    assert "!e.in_after_all" in _fn("wkAddMealFor")
    run = _fn("runInAfterAll")
    assert "/in-after-all" in run
