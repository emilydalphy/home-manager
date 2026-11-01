"""
A dish we're bringing to someone else's holiday dinner: warn, don't block
(Loop Board, Emily 2026-10-31).

"We're out, bringing Satay Noodles" with Theo allergic to peanuts: the dish
stays on that day, and the answer says "Has peanut, which Theo can’t have".
It is not swept off the week, does not stop Approve, and is not the draft's
settle card — those stay for the week's own dishes, which keep the hard
check.
"""
from datetime import date, timedelta

import pytest

from app import tools
from app.tools import allergen_gate, coordination

from tests.test_holidays import _thanksgiving

SATAY = [{"item": "Peanut butter", "qty": "3 tbsp", "category": "pantry"},
         {"item": "Rice noodles", "qty": "400 g", "category": "pantry"}]


@pytest.fixture
def theo():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Theo")
    tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    tools.add_recipe("Satay Noodles", ingredients=SATAY)
    tools.add_recipe("Chili", ingredients=[{"item": "Kidney beans", "qty": "1 tin"}])


def _dinner(plan_id, day):
    return next(m for m in tools.get_weekly_plan(plan_id)["meals"]
                if m["date"] == day and m["slot"] == "dinner" and m["slot_state"] == "planned")


def test_the_answer_says_the_allergy_line_and_the_dish_stays(theo):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]

    out = tools.answer_holiday(tg, "out", bring_dish="Satay Noodles")

    assert out["allergy_line"] == "Has peanut, which Theo can’t have"
    assert out["dish_planned"] is True
    assert _dinner(plan_id, tg)["meal"] == "Satay Noodles"


def test_it_is_not_the_weeks_clash_and_approval_goes_through(theo):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.answer_holiday(tg, "out", bring_dish="Satay Noodles")

    found = coordination.check_plan_conflicts(plan_id)

    assert not [c for c in found["conflicts"] if c["severity"] == "hard"]
    assert found["settle"] is None
    assert [(w["meal"], w["line"]) for w in found["brought_dish_warnings"]] == [
        ("Satay Noodles", "Has peanut, which Theo can’t have")]
    assert tools.approve_weekly_plan(plan_id, approved_by="Emily")["status"] == "approved"
    assert _dinner(plan_id, tg)["meal"] == "Satay Noodles"


def test_the_weeks_own_dishes_keep_the_hard_check(theo):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    other = (date.fromisoformat(tg) + timedelta(days=1)).isoformat()
    tools.answer_holiday(tg, "out", bring_dish="Satay Noodles")
    # Planned straight onto the week, as a hand-planned dish (not through a gated door).
    tools.plan_meal(other, "Satay Noodles", slot="dinner", weekly_plan_id=plan_id)

    out = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert out["status"] == "needs_confirmation"
    assert {c["date"] for c in out["conflicts"] if c["severity"] == "hard"} == {other}


def test_the_sweep_leaves_the_brought_dish_where_it_is(theo):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.answer_holiday(tg, "out", bring_dish="Satay Noodles")

    counts = allergen_gate.sweep_plan(plan_id, picker=lambda *a, **k: pytest.fail("the sweep re-picked it"))

    assert counts == {"sides_removed": 0, "dishes_repicked": 0, "slots_opened": 0}
    assert _dinner(plan_id, tg)["meal"] == "Satay Noodles"


def test_a_dish_with_nothing_wrong_has_no_line(theo):
    tg = _thanksgiving()
    tools.create_weekly_plan(tg)
    assert tools.answer_holiday(tg, "out", bring_dish="Chili")["allergy_line"] is None
    # …and a dish typed by name alone is read on its name.
    assert tools.answer_holiday(tg, "out", bring_dish="Peanut Brittle")["allergy_line"] == \
        "Has peanut, which Theo can’t have"


def test_the_route_answers_the_line(signed_in, theo):
    tg = _thanksgiving()
    res = signed_in.post("/api/holidays/answer",
                         json={"date": tg, "answer": "out", "bring_dish": "Satay Noodles"})
    assert res.status_code == 200, res.text
    assert res.json()["allergy_line"] == "Has peanut, which Theo can’t have"


def test_the_line_comes_back_with_the_weeks_holidays(theo):
    """The holiday block on the week's questions reads it again after a reload."""
    tg = _thanksgiving()
    tools.answer_holiday(tg, "out", bring_dish="Satay Noodles")
    h = next(h for h in tools.get_upcoming_holidays(tg, 1) if h["date"] == tg)
    assert h["answer"]["allergy_line"] == "Has peanut, which Theo can’t have"
