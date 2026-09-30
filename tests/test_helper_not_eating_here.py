"""
"Someone not eating here" (setup's helpers question, 2026-09-30): a nanny,
a parent who helps with dinners. They get an invite and sign in, but meals
are never planned for them — members.eats_here = 0, read through
tools._shared.EATS_HERE_SQL by the members the planner is given, the usual
week's grid, attendance and servings.
"""
from __future__ import annotations

from pathlib import Path

from app import invites, tools
from app.db import get_conn

ONBOARDING = (Path(__file__).resolve().parent.parent / "static" / "onboarding.html").read_text()


def _household(signed_in):
    res = signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}, {"name": "Greg", "age_group": "adult"}],
        "pets": [], "goals": "",
    })
    assert res.status_code == 200, res.text


def _invite_maria(signed_in):
    res = signed_in.post("/api/household/invites", json={"name": "Maria", "eats_here": False})
    assert res.status_code == 200, res.text
    assert res.json()["path"].startswith("/join#")
    return res.json()["member"]["id"]


def _eats_here(member_id):
    conn = get_conn()
    try:
        return conn.execute("SELECT eats_here FROM members WHERE id = ?", (member_id,)).fetchone()["eats_here"]
    finally:
        conn.close()


def test_a_helper_is_an_adult_who_can_sign_in_but_does_not_eat_here(signed_in):
    _household(signed_in)
    maria = _invite_maria(signed_in)
    assert _eats_here(maria) == 0
    adults = [a["name"] for a in invites.household_adults_status(tools.household_id(), None)]
    assert "Maria" in adults, "a helper still gets a way in"


def test_nobody_plans_for_the_helper(signed_in):
    _household(signed_in)
    _invite_maria(signed_in)
    assert [m["name"] for m in tools.list_members()] == ["Emily", "Greg"]
    assert [m["name"] for m in tools.get_household_memory()["members"]] == ["Emily", "Greg"]
    assert [m["name"] for m in tools.get_meal_planning_setup_status()["members"]] == ["Emily", "Greg"]
    usual = tools.get_usual_week()
    assert [m["name"] for m in usual["members"]] == ["Emily", "Greg"]
    assert usual["grid"]["dinner"]["monday"] == "everyone"


def test_attendance_and_servings_leave_the_helper_out(signed_in):
    _household(signed_in)
    _invite_maria(signed_in)
    att = tools.get_slot_attendance("2026-10-05", "dinner")
    assert att["household_size"] == 2
    assert att["headcount"] == 2
    assert "Maria" not in att["present_names"]


def test_a_grid_cell_cannot_name_the_helper_as_eating(signed_in):
    _household(signed_in)
    _invite_maria(signed_in)
    week = {d: "everyone" for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
    greg = next(m["id"] for m in tools.get_usual_week()["members"] if m["name"] == "Greg")
    saved = signed_in.post("/api/usual-week", json={"grid": {"dinner": dict(week, thursday=[greg])}}).json()
    # Greg alone out of the two who eat here, not "everyone but Maria".
    assert saved["grid"]["dinner"]["thursday"] == [greg]


def test_inviting_someone_already_eating_here_leaves_them_eating(signed_in):
    _household(signed_in)
    res = signed_in.post("/api/household/invites", json={"name": "Greg", "eats_here": False})
    assert res.status_code == 200, res.text
    assert _eats_here(res.json()["member"]["id"]) == 1
    assert [m["name"] for m in tools.list_members()] == ["Emily", "Greg"]


def test_an_ordinary_invite_still_adds_someone_who_eats_here(signed_in):
    _household(signed_in)
    res = signed_in.post("/api/household/invites", json={"name": "Sam"})
    assert res.status_code == 200, res.text
    assert _eats_here(res.json()["member"]["id"]) == 1
    assert "Sam" in [m["name"] for m in tools.list_members()]


def test_setup_sends_someone_not_eating_here_as_a_helper():
    assert "const body = { name: helper.name, eats_here: helper.eatsHere !== false };" in ONBOARDING
    assert "eatsHere: false });" in ONBOARDING
