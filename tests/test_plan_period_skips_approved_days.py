"""
The Plan screen's "this week" (/api/week/planning-period, what /plan-week
opens on with no week chosen) never offers days an approved plan already
holds (Emily, 2026-10-31, option a — the same rule Today follows): it steps
past them to the first free day, or is cut to the free days before them.
"""
from __future__ import annotations

from app import tools
from test_intake_fixes_2026_09_21b import _insert_plan, pin_today  # noqa: F401 (fixture)


def _offer():
    return tools.step_past_approved(tools.suggest_planning_period())


def test_ready_by_saturday_on_sunday_with_next_week_approved_steps_past_it(pin_today):
    tools.set_planning_anchor("saturday")
    _insert_plan("2026-10-26", 7)
    pin_today("2026-10-25")
    assert tools.suggest_planning_period()["start_date"] == "2026-10-25", "the raw suggestion is the bug"
    offer = _offer()
    assert (offer["start_date"], offer["day_count"]) == ("2026-11-02", 7)
    assert offer["is_current_period"] is False
    assert "Nov 2" in offer["label"]


def test_ready_by_friday_on_saturday_inside_an_approved_plan_offers_the_day_after_it(pin_today):
    tools.set_planning_anchor("friday")
    _insert_plan("2026-10-26", 7)
    pin_today("2026-10-31")
    offer = _offer()
    assert (offer["start_date"], offer["day_count"]) == ("2026-11-02", 7)


def test_free_days_before_an_approved_plan_are_offered_as_that_stretch(pin_today):
    # Today's rule: a plan that starts Wednesday leaves Sun–Tue to plan.
    tools.set_planning_anchor("saturday")
    _insert_plan("2026-10-28", 7)
    pin_today("2026-10-25")
    offer = _offer()
    assert (offer["start_date"], offer["day_count"]) == ("2026-10-25", 3)
    assert offer["is_current_period"] is True


def test_a_draft_is_not_stepped_over_and_nothing_held_is_unchanged(pin_today):
    tools.set_planning_anchor("saturday")
    _insert_plan("2026-10-26", 7, status="draft")
    pin_today("2026-10-25")
    assert _offer() == tools.suggest_planning_period()


def test_back_to_back_approved_plans_step_past_both(pin_today):
    tools.set_planning_anchor("friday")
    _insert_plan("2026-10-26", 7)
    _insert_plan("2026-11-02", 7)
    pin_today("2026-10-31")
    assert _offer()["start_date"] == "2026-11-09"


def test_the_endpoint_says_the_stepped_period(signed_in, pin_today):
    tools.set_planning_anchor("friday")
    _insert_plan("2026-10-26", 7)
    pin_today("2026-10-31")
    body = signed_in.get("/api/week/planning-period").json()
    assert body["start_date"] == "2026-11-02" and body["day_count"] == 7
