"""
The household's weekly-menu share link can be replaced (defect, 2026-10-29).

The link is standing: once handed to a babysitter or an ex-nanny it showed
every future week, and nothing could shut it. reset_share_link retires every
live link for the signed-in household (share_links.revoked_at) and issues a
fresh one. The old token then reads exactly like a token that never existed;
the new one shows the current approved week; another household's link is
untouched.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import agent, households, tools
from app.db import get_conn


def _approved_week(dish: str) -> str:
    today = household_today()
    start = today - datetime.timedelta(days=today.weekday())
    tools.add_recipe(dish, [{"item": "Salt", "qty": "1 tsp"}])
    plan_id = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    for i in range(7):
        tools.plan_meal((start + datetime.timedelta(days=i)).isoformat(), dish,
                        slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return start.isoformat()


def test_the_old_link_stops_and_the_new_one_shows_this_week(client):
    week = _approved_week("Approved Chili")
    old = tools.get_or_create_share_link()["token"]

    result = tools.reset_share_link()
    new = result["token"]
    assert new != old
    assert result["old_link_stopped"] is True
    assert result["link"].endswith(f"/share/{new}")
    assert "doesn't work any more" in result["message"]
    assert result["link"] in result["message"]

    client.cookies.clear()
    gone = client.get(f"/api/share/{old}")
    garbage = client.get("/api/share/not-a-real-token")
    assert gone.status_code == 404
    # Indistinguishable from a token that never existed.
    assert gone.json() == garbage.json()
    # The page is still served; share.html draws "This link isn't valid" on the 404.
    assert client.get(f"/share/{old}").status_code == 200

    plan = client.get(f"/api/share/{new}").json()
    assert plan["week_start_date"] == week
    assert {m["meal"] for m in plan["meals"]} == {"Approved Chili"}


def test_asking_for_the_link_afterwards_hands_out_the_new_one(client):
    old = tools.get_or_create_share_link()["token"]
    new = tools.reset_share_link()["token"]
    assert tools.get_or_create_share_link()["token"] == new
    assert tools.get_or_create_share_link()["token"] != old
    # Twice in a row: the second reset retires the first replacement too.
    newer = tools.reset_share_link()["token"]
    client.cookies.clear()
    assert client.get(f"/api/share/{new}").status_code == 404
    assert client.get(f"/api/share/{newer}").status_code == 200
    conn = get_conn()
    live = conn.execute("SELECT COUNT(*) FROM share_links WHERE revoked_at IS NULL").fetchone()[0]
    conn.close()
    assert live == 1


def test_resetting_with_no_link_out_yet_just_issues_one(client):
    result = tools.reset_share_link()
    assert result["old_link_stopped"] is False
    assert "doesn't work any more" not in result["message"]
    client.cookies.clear()
    assert client.get(f"/api/share/{result['token']}").status_code == 200


def test_another_households_link_is_untouched(client):
    beta = households.create_household("The Beta Testers", "beta-tester-passphrase")
    with tools.use_household(beta):
        beta_token = tools.get_or_create_share_link()["token"]
    mine = tools.get_or_create_share_link()["token"]

    tools.reset_share_link()

    client.cookies.clear()
    assert client.get(f"/api/share/{mine}").status_code == 404
    assert client.get(f"/api/share/{beta_token}").status_code == 200
    with tools.use_household(beta):
        assert tools.get_or_create_share_link()["token"] == beta_token


def test_the_signed_in_route_replaces_the_link(signed_in):
    client = signed_in
    old = client.get("/api/share-link").json()["token"]
    res = client.post("/api/share-link/replace")
    assert res.status_code == 200
    new = res.json()["token"]
    assert new != old
    assert client.get("/api/share-link").json()["token"] == new


def test_the_chat_tool_is_wired():
    names = [t["name"] for t in agent.TOOL_DEFINITIONS]
    assert "reset_share_link" in names
    assert agent.TOOL_FUNCTIONS["reset_share_link"] is tools.reset_share_link
    assert "reset_share_link" in agent.SYSTEM_PROMPT
