"""
A draft waiting beside an approved week never costs that week its days
(overnight hunt, 2026-11-03).

Two adults. This week is approved and shopped for. Vic re-drafts this week
("something with squash") and leaves the draft unapproved — the "make the
draft wait until approval" state (Emily, 2026-09-13). Emily then approves
NEXT week. Approval's takeover walks every live plan newest-first, and the
re-plan draft, being newer, claimed this week's days before the approved
plan was reached: this week's approved plan was retired, its whole
shopping list came off, and the morning text started saying "This week's
plan is still a draft — approve it?" Nothing about this week had been
approved. Found by driving it over HTTP on two phones.
"""
from __future__ import annotations

import datetime

from fastapi.testclient import TestClient

from app import agent, tools
from app.db import get_conn
from app.main import app
from conftest import household_today


def _day(n: int) -> str:
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _period(context: dict) -> list[dict]:
    dinners = ["Chicken tacos", "Beef chili", "Tofu stir fry", "Pork chops", "Lentil soup",
               "Shrimp pasta", "Salmon bowl"]
    out = []
    for i, d in enumerate(tools.period_dates(context["week_start_date"], context["day_count"])):
        name = dinners[i % len(dinners)]
        out.append({"date": d, "slot": "dinner", "meal_name": name, "is_new_recipe": False, "reasoning": "x"})
        for s in ("breakfast", "lunch"):
            out.append({"date": d, "slot": s, "meal_name": f"{s.title()} plate", "is_new_recipe": False,
                        "reasoning": "x"})
    return out


def _phone(member_id: int) -> TestClient:
    phone = TestClient(app)
    assert phone.post("/login", data={"password": "test-password", "next": "/"},
                      follow_redirects=False).status_code == 303
    assert phone.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200
    return phone


def _status(plan_id: int) -> tuple[str, int]:
    conn = get_conn()
    row = conn.execute("SELECT status, day_count FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return row["status"], row["day_count"]


def _lines(plan_id: int) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM grocery_items WHERE source_weekly_plan_id = ? AND status != 'removed'",
        (plan_id,),
    ).fetchone()["n"]
    conn.close()
    return n


def test_approving_next_week_keeps_this_weeks_approved_plan_beside_a_waiting_redraft(monkeypatch):
    tools.add_recipe("Chicken tacos", ingredients=[{"item": "tortillas", "qty": "8"}],
                     prep_time_minutes=10, cook_time_minutes=15)
    tools.add_recipe("Beef chili", ingredients=[{"item": "ground beef", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=30)
    emily = tools.add_member("Emily")["member_id"]
    vic = tools.add_member("Vic")["member_id"]
    tools.set_member_age_group("Emily", "adult")
    tools.set_member_age_group("Vic", "adult")
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _period)
    emilys_phone, vics_phone = _phone(emily), _phone(vic)
    this_week, next_week = _day(0), _day(7)

    def draft(phone, week, freeform):
        intake = phone.post(f"/api/week/{week}/intake", json={"freeform": freeform}).json()
        res = phone.post(f"/api/week/{week}/generate", json={"intake_id": intake["intake_id"]})
        assert res.status_code == 200, res.text
        return res.json()["weekly_plan_id"]

    this_plan = draft(emilys_phone, this_week, "tacos one night")
    assert emilys_phone.post(f"/api/week/{this_week}/approve",
                             json={"weekly_plan_id": this_plan}).json()["status"] == "approved"
    this_weeks_lines = _lines(this_plan)
    assert this_weeks_lines > 0

    next_plan = draft(emilys_phone, next_week, "")
    redraft = draft(vics_phone, this_week, "something with squash")
    assert _status(this_plan) == ("approved", 7), "drafting again leaves the approved week whole"

    res = emilys_phone.post(f"/api/week/{next_week}/approve", json={"weekly_plan_id": next_plan}).json()

    assert res["status"] == "approved" and res["approved_by"] == "Emily"
    assert res["replaced"] is None, res["replaced"]
    assert _status(this_plan) == ("approved", 7)
    assert _lines(this_plan) == this_weeks_lines
    # The re-plan is still there to look at, and still waiting.
    assert _status(redraft) == ("draft", 7)


def test_the_redraft_still_takes_the_week_when_it_is_the_one_approved(monkeypatch):
    """The other half: approving the waiting draft itself is what replaces."""
    tools.add_recipe("Chicken tacos", ingredients=[{"item": "tortillas", "qty": "8"}],
                     prep_time_minutes=10, cook_time_minutes=15)
    tools.add_member("Emily")
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", _period)
    first = agent.generate_weekly_plan(_day(0), day_count=7, period_start=_day(0), confirm_takeover=True)
    tools.approve_weekly_plan(first["weekly_plan_id"])
    nxt = agent.generate_weekly_plan(_day(7), day_count=7, period_start=_day(7), confirm_takeover=True)
    again = agent.generate_weekly_plan(_day(0), day_count=7, period_start=_day(0), confirm_takeover=True)
    tools.approve_weekly_plan(nxt["weekly_plan_id"])
    assert _status(first["weekly_plan_id"]) == ("approved", 7)

    tools.approve_weekly_plan(again["weekly_plan_id"])

    assert _status(first["weekly_plan_id"]) == ("retired", 0)
    assert _status(again["weekly_plan_id"]) == ("approved", 7)
    assert _status(nxt["weekly_plan_id"]) == ("approved", 7)
