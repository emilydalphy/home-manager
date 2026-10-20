"""
The public share link shows only APPROVED weeks, and no line written to the
household (Emily, 2026-10-20).

A draft nobody has approved is never shown: with only a draft for this week
the page says there is no plan yet. An approved current week beside a draft
next week shows the approved week. And "Here's your first week" / "N new
recipes this week" are notes to the household, so share.html and its JSON
carry neither.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools

NO_PLAN = "No plan has been made for this week yet"


def _monday(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=d.weekday())


def _week(start: datetime.date, dish: str, approve: bool) -> int:
    tools.add_recipe(dish, [{"item": "Salt", "qty": "1 tsp"}])
    plan_id = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    for i in range(7):
        tools.plan_meal((start + datetime.timedelta(days=i)).isoformat(), dish,
                        slot="dinner", weekly_plan_id=plan_id)
    if approve:
        tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _shared(client):
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    return client.get(f"/api/share/{token}").json()


def test_a_draft_only_week_is_not_shared(client):
    _week(_monday(household_today()), "Draft Stew", approve=False)
    plan = _shared(client)
    assert plan["weekly_plan_id"] is None
    assert plan["meals"] == [] and plan["menu"] == []
    assert "Draft Stew" not in str(plan)
    assert plan["household_name"] == "My Household"


def test_the_page_says_no_plan_yet_for_a_draft(client):
    # The page draws the same empty state it uses for no plan at all.
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    html = client.get(f"/share/{token}").text
    assert NO_PLAN in html


def test_an_approved_week_with_a_draft_next_week_shows_the_approved_week(client):
    start = _monday(household_today())
    _week(start, "Approved Chili", approve=True)
    _week(start + datetime.timedelta(days=7), "Draft Stew", approve=False)
    plan = _shared(client)
    assert plan["week_start_date"] == start.isoformat()
    assert {m["meal"] for m in plan["meals"]} == {"Approved Chili"}
    assert "Draft Stew" not in str(plan)


def test_a_draft_over_the_current_week_does_not_hide_a_later_approved_week(client):
    start = _monday(household_today())
    _week(start, "Draft Stew", approve=False)
    nxt = start + datetime.timedelta(days=7)
    _week(nxt, "Approved Chili", approve=True)
    plan = _shared(client)
    assert plan["week_start_date"] == nxt.isoformat()
    assert {m["meal"] for m in plan["meals"]} == {"Approved Chili"}


def test_no_line_written_to_the_household_is_sent_or_drawn(client):
    _week(_monday(household_today()), "Approved Chili", approve=True)
    token = tools.get_or_create_share_link()["token"]
    client.cookies.clear()
    plan = client.get(f"/api/share/{token}").json()
    assert plan["weekly_plan_id"]
    assert "is_first_plan" not in plan and "new_recipe_count" not in plan
    html = client.get(f"/share/{token}").text
    for line in ("first week", "built around what you told me", "new recipe", "freshness", "first-run"):
        assert line not in html, line
