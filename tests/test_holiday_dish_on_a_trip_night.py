"""
A dish brought on a night a trip already covers, then taken back.

The calendar has the household away over Thanksgiving (a trip: the dinner
is planned_empty, nobody home, nothing bought). "We're bringing a pie to
Grandma's" plans the pie into that dinner, which is right — somebody makes
it. But changing the answer again (just us, unsure, not bringing anything
after all) went through holidays._reopen, which hands the dinner back as
"Plans for Thanksgiving changed — what would you like for dinner?": an
OPEN question, on a night attendance still says nobody is home. A
planned_empty night must never be offered as a decision (CLAUDE.md). The
answer going now gives the trip its night back, deliberately empty.
"""
import json

from app import tools
from app.db import get_conn
from tests.test_big_meal import family, proposer  # noqa: F401
from tests.test_holidays import _shift, _thanksgiving
from conftest import on_the_weeks_monday

ON_MONDAY = on_the_weeks_monday()


def _dinner(plan_id: int, day: str) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT slot_state, derived_from_json, open_reason FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'", (plan_id, day),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"{day} holds {len(rows)} dinner rows"
    return {**dict(rows[0]), "derived": json.loads(rows[0]["derived_from_json"] or "{}")}


def _trip_over(tg: str) -> int:
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin", "category": "pantry"}], default_servings=2)
    tools.add_recipe("Pumpkin Pie", ingredients=[{"item": "pie crust", "qty": "1", "category": "frozen"}],
                     default_servings=8)
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.set_away_stretch(_shift(tg, -1), "dinner", tg, "dinner", reason="At Grandma’s")
    assert _dinner(plan_id, tg)["slot_state"] == "planned_empty", "premise: the trip has the night"
    return plan_id


@ON_MONDAY
def test_taking_the_dish_back_gives_the_trip_its_night_back(family, proposer):
    tg = _thanksgiving()
    plan_id = _trip_over(tg)
    tools.answer_holiday(tg, "out", bring_dish="Pumpkin Pie")
    assert _dinner(plan_id, tg)["slot_state"] == "planned"
    assert [i["item"] for i in tools.list_grocery_list()] == ["pie crust"]

    tools.answer_holiday(tg, "just_us")

    night = _dinner(plan_id, tg)
    assert night["slot_state"] == "planned_empty", "a night nobody is home offered as a question"
    assert night["derived"].get("need") == "away" and night["derived"].get("away_stretch_id")
    assert tools.list_grocery_list() == []
    audit = tools.audit_plan_slots(plan_id)
    assert audit["duplicated"] == [] and audit["hollow"] == []


@ON_MONDAY
def test_out_without_the_dish_on_a_trip_night_stays_empty(family, proposer):
    tg = _thanksgiving()
    plan_id = _trip_over(tg)
    tools.answer_holiday(tg, "out", bring_dish="Pumpkin Pie")

    tools.answer_holiday(tg, "out", bring_dish="")

    assert _dinner(plan_id, tg)["slot_state"] == "planned_empty"
    assert tools.list_grocery_list() == []


@ON_MONDAY
def test_with_nobody_away_the_dinner_still_comes_back_as_a_question(family, proposer):
    tg = _thanksgiving()
    tools.add_recipe("Pumpkin Pie", ingredients=[{"item": "pie crust", "qty": "1", "category": "frozen"}],
                     default_servings=8)
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.answer_holiday(tg, "out", bring_dish="Pumpkin Pie")

    tools.answer_holiday(tg, "just_us")

    night = _dinner(plan_id, tg)
    assert night["slot_state"] == "open"
    assert night["open_reason"].startswith("Plans for Thanksgiving changed")
