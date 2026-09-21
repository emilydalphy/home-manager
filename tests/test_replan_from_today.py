"""Re-planning from today finds the week it re-plans.

The regression (verification of intake-motion-2026-09-21, card "Plan a
week never offers a range that starts before today"): the intake screen
now opens on today, never yesterday (clampStart), so "Re-plan this week"
on a Sat–Fri plan asks the server about Sun–Sat — and the prefill looked
the plan and the intake up by an EXACT start date. Nothing matched.
Two things followed: the "already approved … re-planning makes a new
draft beside it" warning went unsaid while the route still drafted with
confirm_takeover=True; and "Change my answers" on any day but the first
opened blank — night tags, guests and the typed note gone.

The fix is at the root: the prefill's plan and intake are the ones that
COVER the period's first day (week_intake._plan_for_period,
_intake_for_period), the approved warning fires whenever the period
overlaps an approved plan, and every saved answer for the days still in
range comes back.
"""
from __future__ import annotations

import pytest

from conftest import household_date
from app import agent, tools
from app.db import get_conn


@pytest.fixture
def chili():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(start: str, count: int):
        days = [
            {"date": d, "slot": s, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
            for d in tools.period_dates(start, count) for s in tools.WEEK_SLOTS
        ]
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    return _stub


def _drafted_from_intake(start: str, approve: bool, stub_model) -> tuple[dict, dict]:
    """A 7-day plan starting `start`, drafted from an intake with a night
    tag, a guest count and a note on its second day, approved or not."""
    dates = tools.period_dates(start, 7)
    intake = tools.save_week_intake(
        start,
        night_tags={dates[1]: ["rush"], dates[0]: ["left"]},
        guest_counts={dates[1]: {"adults": 2, "children": 0}},
        packed_lunch_days=[dates[0], dates[2]],
        moods=["Comfort food"], cuisines=["Thai"], freeform="Use the lamb in the freezer",
    )
    stub_model(start, 7)
    plan = agent.generate_weekly_plan(start, day_count=7, period_start=start, intake_id=intake["intake_id"])
    assert plan.get("weekly_plan_id"), plan
    if approve:
        tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")
    return plan, intake


def _plan_row(plan_id: int):
    conn = get_conn()
    row = conn.execute("SELECT status, content_start_date, day_count FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return dict(row)


def test_replan_on_the_day_after_an_approved_start_still_warns_and_has_the_answers(chili, stub_model):
    saturday = household_date(7)
    sunday = household_date(8)
    plan, intake = _drafted_from_intake(saturday, approve=True, stub_model=stub_model)

    prefill = tools.get_week_intake_prefill(sunday, 7)

    # The plan that covers Sunday is found, and it is the approved one.
    assert prefill["plan_exists"] is True
    assert prefill["plan_id"] == plan["weekly_plan_id"]
    assert prefill["plan_status"] == "approved"
    assert prefill["approved_overlap"] is True
    # The answers it was drafted from come back — the ones for days still
    # in range, since Saturday is eaten.
    assert prefill["intake"] is not None
    assert prefill["intake"]["intake_id"] == intake["intake_id"]
    dates = tools.period_dates(saturday, 7)
    assert prefill["intake"]["night_tags"] == {dates[1]: ["rush"]}
    assert prefill["intake"]["guest_counts"] == {dates[1]: {"adults": 2, "children": 0}}
    assert prefill["intake"]["packed_lunch_days"] == [dates[2]]
    assert prefill["intake"]["freeform"] == "Use the lamb in the freezer"
    assert prefill["intake"]["moods"] == ["Comfort food"] and prefill["intake"]["cuisines"] == ["Thai"]
    # Not "in flight": nothing has been answered since that plan was drafted,
    # so the screen says "already approved", not "carried on from".
    assert prefill["in_flight"] is False
    # And the exact-key lookup still behaves for the first day.
    first = tools.get_week_intake_prefill(saturday, 7)
    assert first["plan_id"] == plan["weekly_plan_id"] and first["intake"]["night_tags"] == {dates[0]: ["left"], dates[1]: ["rush"]}


def test_change_my_answers_on_a_later_day_has_every_answer(chili, stub_model):
    start = household_date(7)
    plan, intake = _drafted_from_intake(start, approve=False, stub_model=stub_model)
    dates = tools.period_dates(start, 7)

    later = tools.get_week_intake_prefill(dates[2], 5)

    assert later["plan_exists"] is True and later["plan_status"] == "draft"
    assert later["approved_overlap"] is False
    assert later["intake"]["intake_id"] == intake["intake_id"]
    # Day 2's tag and guests are gone with day 2; nothing else is.
    assert later["intake"]["night_tags"] == {}
    assert later["intake"]["packed_lunch_days"] == [dates[2]]
    assert later["intake"]["freeform"] == "Use the lamb in the freezer"
    assert later["intake"]["moods"] == ["Comfort food"]
    # A day still in range keeps everything.
    day_two = tools.get_week_intake_prefill(dates[1], 6)
    assert day_two["intake"]["night_tags"] == {dates[1]: ["rush"]}
    assert day_two["intake"]["guest_counts"] == {dates[1]: {"adults": 2, "children": 0}}
    # The answers handed back save straight into the clamped period
    # (nothing out of range to refuse).
    saved = tools.save_week_intake(dates[2], day_count=5, **{
        k: later["intake"][k] for k in ("night_tags", "guest_counts", "packed_lunch_days", "moods", "cuisines", "freeform")
    })
    assert saved["packed_lunch_days"] == [dates[2]]


def test_an_approved_plan_the_period_merely_overlaps_still_warns(chili, stub_model):
    # Approved Wed–Fri; re-planning Mon–Sun covers it without starting on it.
    wednesday = household_date(9)
    _drafted_from_intake(wednesday, approve=True, stub_model=stub_model)
    monday = household_date(7)
    prefill = tools.get_week_intake_prefill(monday, 7)
    assert prefill["approved_overlap"] is True
    assert prefill["plan_status"] == "approved"


def test_nothing_is_taken_over_without_a_yes(chili, stub_model):
    saturday = household_date(7)
    sunday = household_date(8)
    plan, _ = _drafted_from_intake(saturday, approve=True, stub_model=stub_model)
    before = _plan_row(plan["weekly_plan_id"])

    stub_model(sunday, 7)
    result = agent.generate_weekly_plan(sunday, day_count=7, period_start=sunday)

    assert result["status"] == "needs_confirmation"
    assert result["reason"] == "approved_plan_overlap"
    assert "weekly_plan_id" not in result
    assert _plan_row(plan["weekly_plan_id"]) == before
    # And no draft was written.
    assert tools.find_overlapping_plans(sunday, 7) == [
        o for o in tools.find_overlapping_plans(sunday, 7) if o["weekly_plan_id"] == plan["weekly_plan_id"]
    ]


def test_a_period_touching_nothing_opens_blank():
    prefill = tools.get_week_intake_prefill(household_date(30), 7)
    assert prefill["plan_exists"] is False and prefill["intake"] is None
    assert prefill["approved_overlap"] is False


def test_the_second_adult_joins_across_midnight():
    # One adult starts the week on Saturday night; nothing is drafted; the
    # other opens it on Sunday, when the screen starts from today.
    saturday, sunday = household_date(7), household_date(8)
    dates = tools.period_dates(saturday, 7)
    tools.save_week_intake(saturday, night_tags={dates[3]: ["rush"]}, freeform="pizza Friday", created_by="Emily")
    prefill = tools.get_week_intake_prefill(sunday, 7)
    assert prefill["in_flight"] is True
    assert prefill["intake"]["created_by"] == "Emily"
    assert prefill["intake"]["night_tags"] == {dates[3]: ["rush"]}
    assert prefill["intake"]["freeform"] == "pizza Friday"
    # An abandoned set of answers from weeks ago is not "in flight".
    tools.save_week_intake(household_date(40), freeform="long ago")
    far = tools.get_week_intake_prefill(household_date(60), 7)
    assert far["intake"] is None


def test_the_screen_warns_on_either_signal():
    page = (tools.__file__ and __import__("pathlib").Path(tools.__file__).resolve().parents[2] / "static" / "plan-week.html").read_text(encoding="utf-8")
    assert "(data.plan_status === 'approved' || data.approved_overlap)" in page
