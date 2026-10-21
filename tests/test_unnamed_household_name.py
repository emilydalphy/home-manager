"""
An email sign-up's household has no name of its own, and is never called
"Your household" as if it had one (defect hunt, 2026-10-21).

account_email.create_household_for names every new household "Your
household", and nothing asks for a real name afterwards. Said as a name it
read "Delete Your household?" / "Remove yourself from Your household?" in
Preferences, and "Your household's meals this week" on the share link — a
title a babysitter reads about somebody else's family.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import account_email, security
from app.main import app


def _email_household_client() -> tuple[TestClient, int]:
    hid = account_email.create_household_for("unnamed@example.com")
    client = TestClient(app)  # a fresh jar: the one cookie is this one
    client.cookies.set(security.COOKIE_NAME, security.issue_session(hid))
    return client, hid


def test_the_leave_dialog_says_your_household_in_lowercase():
    client, hid = _email_household_client()
    state = client.get("/api/household/leave").json()
    assert state["household_name"] == "your household"
    assert state["can_delete"] is True  # no adults yet: the dialog is the Delete one


def test_the_share_link_carries_no_name_for_it():
    client, hid = _email_household_client()
    token = client.get("/api/share-link").json()["token"]
    shared = TestClient(app).get(f"/api/share/{token}").json()
    # "" is share.html's cue to keep its own "This week's meals".
    assert shared["household_name"] == ""


def test_a_household_with_a_real_name_keeps_it(signed_in):
    assert signed_in.get("/api/household/leave").json()["household_name"] == _name_of(1)
    token = signed_in.get("/api/share-link").json()["token"]
    assert TestClient(app).get(f"/api/share/{token}").json()["household_name"] == _name_of(1)


def _name_of(hid: int) -> str:
    from app.db import get_conn

    conn = get_conn()
    try:
        name = conn.execute("SELECT name FROM households WHERE id = ?", (hid,)).fetchone()["name"]
    finally:
        conn.close()
    assert name and name != account_email.NEW_HOUSEHOLD_NAME
    return name
