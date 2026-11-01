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
    # The cut is the offer, not the household's horizon: /plan-week's
    # loadHorizon reads usual_day_count, so a Re-plan link with no &days
    # still asks about a full week (review, 2026-11-01).
    assert offer["usual_day_count"] == 7


def test_plan_week_takes_its_horizon_from_the_usual_length_not_the_cut():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "static" / "plan-week.html").read_text()
    body = page.split("async function loadHorizon()", 1)[1].split("catch (err)", 1)[0]
    assert "period.usual_day_count" in body


def test_a_draft_is_not_stepped_over_and_nothing_held_is_unchanged(pin_today):
    tools.set_planning_anchor("saturday")
    _insert_plan("2026-10-26", 7, status="draft")
    pin_today("2026-10-25")
    offer = _offer()
    assert offer.pop("usual_day_count") == offer["day_count"]
    assert offer == tools.suggest_planning_period()


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


def test_a_page_with_no_week_opens_on_the_offer_with_the_offers_own_length():
    # The bookmark door: start AND length from the offer, so a cut offer
    # (Sun–Tue before a Wednesday plan) isn't stretched over the held days.
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "static" / "plan-week.html").read_text()
    branch = page.split("weekStart = period.start_date;", 1)[1].split("} else", 1)[0]
    assert "period.day_count" in branch
