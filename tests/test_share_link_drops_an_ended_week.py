"""
The public share link does not show a week that has ended as "this week"
(defect hunt, 2026-10-20).

With nothing covering today, get_weekly_plan falls back to the newest plan,
an approved week long over included. /share/<token> titled it "<name>'s
meals this week" and opened on its Monday as "Monday's Table" — whoever
holds the link read last week's dinners as this week's. Cook and Now
already treat such a plan as no plan; the share link now does too, on the
household's clock.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools


def _week(start: datetime.date, days: int = 7, approve: bool = True) -> int:
    tools.add_recipe("Lentil Soup", [{"item": "Lentils", "qty": "2 cups"}])
    plan_id = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    for i in range(days):
        tools.plan_meal((start + datetime.timedelta(days=i)).isoformat(), "Lentil Soup",
                        slot="dinner", weekly_plan_id=plan_id)
    if approve:
        tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _monday(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=d.weekday())


def test_an_approved_week_that_has_ended_is_not_shown(client):
    _week(_monday(household_today()) - datetime.timedelta(days=7))
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    assert plan["weekly_plan_id"] is None
    assert plan["meals"] == [] and plan["menu"] == []
    assert plan["household_name"] == "My Household"


def test_a_week_whose_last_day_is_today_is_still_shown(client):
    today = household_today()
    _week(today - datetime.timedelta(days=6))
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    assert plan["weekly_plan_id"]
    assert any(d["date"] == today.isoformat() and d["dinner"] == "Lentil Soup" for d in plan["menu"])


def test_a_week_that_has_not_started_is_still_shown(client):
    start = _monday(household_today()) + datetime.timedelta(days=7)
    _week(start)
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    assert plan["weekly_plan_id"]
    assert plan["week_start_date"] == start.isoformat()
