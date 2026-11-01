"""
"Remove me" when the leaver's email is the household's only way in
(Loop Board, Emily 2026-10-31: "ask for another adult's email before
removing the sign-in adult").

A household made by email sign-up has no passphrase. If the adult whose
email it is leaves and nobody else who lives there has an email, nobody
can sign back in. Remove me now asks "Before you go, who should sign in
from now on?", sends a code to the staying adult's email, and removes the
leaver only once that code is spent and the email is theirs.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import account_email, households, invites, security
from app.db import get_conn
from app.main import HANDOVER_NEEDED_LINE, app


@pytest.fixture
def outbox(monkeypatch):
    for k, v in {"SMTP_HOST": "smtp.test", "SMTP_USER": "pomona@test", "SMTP_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    sent: list = []
    monkeypatch.setattr(account_email, "_deliver", lambda msg: sent.append(msg))
    return sent


def _code_in(msg) -> str:
    return re.search(r"\b(\d{6})\b", msg.get_content()).group(1)


def _email_household(outbox, email="rowan@example.com", others=("Sam",)):
    """An email sign-up household: Rowan (the sign-in email) plus `others`."""
    client = TestClient(app)
    assert client.post("/api/auth/email/start", json={"email": email}).status_code == 200
    res = client.post(
        "/api/auth/email/verify",
        json={"email": email, "code": _code_in(outbox[-1]), "agreed": True, "next": "/"},
    )
    assert res.status_code == 200, res.text
    members = [{"name": "Rowan", "age_group": "Adult"}] + [{"name": n, "age_group": "Adult"} for n in others]
    res = client.post(
        "/api/onboarding/household",
        json={"members": members, "pets": [], "goals": "", "primary_name": "Rowan"},
    )
    assert res.status_code == 200, res.text
    hid = security.read_session_household(client.cookies.get(security.COOKIE_NAME))
    return client, hid


def _member_id(hid, name):
    conn = get_conn()
    try:
        return conn.execute("SELECT id FROM members WHERE household_id = ? AND name = ?", (hid, name)).fetchone()["id"]
    finally:
        conn.close()


def test_remove_me_asks_who_signs_in_and_hands_over_with_a_code(outbox):
    client, hid = _email_household(outbox)
    sam = _member_id(hid, "Sam")
    state = client.get("/api/household/leave").json()
    assert state["can_remove_self"] is True
    assert state["sign_in_handover"] == [{"id": sam, "name": "Sam"}]

    # Without the handover, nobody is removed.
    res = client.post("/api/household/remove-me", json={})
    assert res.status_code == 400 and res.json()["detail"] == HANDOVER_NEEDED_LINE
    # Nor with no body at all (an older page).
    assert client.post("/api/household/remove-me").status_code == 400

    res = client.post("/api/household/remove-me/handover/start", json={"member_id": sam, "email": "Sam@Example.com"})
    assert res.status_code == 200 and res.json()["sent"] is True
    assert outbox[-1]["To"] == "sam@example.com"
    code = _code_in(outbox[-1])
    wrong = "000000" if code != "000000" else "111111"

    body = {"handover_member_id": sam, "email": "sam@example.com"}
    res = client.post("/api/household/remove-me", json={**body, "code": wrong})
    assert res.status_code == 400 and res.json()["detail"] == account_email.BAD_CODE_LINE
    assert _member_id(hid, "Rowan")  # still here
    assert account_email.email_for_member(hid, sam) is None

    res = client.post("/api/household/remove-me", json={**body, "code": code})
    assert res.status_code == 200, res.text
    assert res.json()["removed"] is True
    assert account_email.email_for_member(hid, sam) == "sam@example.com"
    conn = get_conn()
    assert conn.execute("SELECT 1 FROM members WHERE household_id = ? AND name = 'Rowan'", (hid,)).fetchone() is None
    conn.close()

    # Sam can sign back in with that email, as Sam, into the same household.
    phone = TestClient(app)
    phone.post("/api/auth/email/start", json={"email": "sam@example.com"})
    res = phone.post(
        "/api/auth/email/verify",
        json={"email": "sam@example.com", "code": _code_in(outbox[-1]), "agreed": True, "next": "/"},
    )
    assert res.status_code == 200 and res.json()["new_household"] is False
    who = phone.get("/api/whoami").json()
    assert who["household_id"] == hid and who["member"]["name"] == "Sam"


def test_a_code_for_one_adult_cant_hand_sign_in_to_another(outbox):
    client, hid = _email_household(outbox, others=("Sam", "Alex"))
    sam, alex = _member_id(hid, "Sam"), _member_id(hid, "Alex")
    assert [p["name"] for p in client.get("/api/household/leave").json()["sign_in_handover"]] == ["Sam", "Alex"]
    client.post("/api/household/remove-me/handover/start", json={"member_id": sam, "email": "sam@example.com"})
    code = _code_in(outbox[-1])
    res = client.post(
        "/api/household/remove-me",
        json={"handover_member_id": alex, "email": "sam@example.com", "code": code},
    )
    assert res.status_code == 400
    assert account_email.email_for_member(hid, alex) is None
    assert _member_id(hid, "Rowan")


def test_no_handover_when_someone_else_can_already_sign_in(outbox):
    client, hid = _email_household(outbox)
    sam = _member_id(hid, "Sam")
    account_email.set_member_email(hid, sam, "sam@example.com")
    assert client.get("/api/household/leave").json()["sign_in_handover"] is None
    assert client.post("/api/household/remove-me", json={}).status_code == 200


def test_no_handover_when_the_household_has_a_passphrase(outbox):
    client, hid = _email_household(outbox)
    households.set_passphrase(hid, "a long household passphrase here")
    assert client.get("/api/household/leave").json()["sign_in_handover"] is None
    assert client.post("/api/household/remove-me", json={}).status_code == 200


def test_a_helper_is_not_offered_and_her_email_doesnt_count(outbox):
    client, hid = _email_household(outbox)
    nanny = invites.add_adult(hid, "Nadia", eats_here=False)
    account_email.set_member_email(hid, nanny, "nadia@example.com")
    state = client.get("/api/household/leave").json()
    assert [p["name"] for p in state["sign_in_handover"]] == ["Sam"]
    res = client.post("/api/household/remove-me/handover/start", json={"member_id": nanny, "email": "x@example.com"})
    assert res.status_code == 400


def test_the_only_adult_still_cant_leave(outbox):
    client, hid = _email_household(outbox, others=())
    state = client.get("/api/household/leave").json()
    assert state["can_remove_self"] is False
    assert client.post("/api/household/remove-me", json={}).status_code == 400


def test_the_dialog_asks_before_you_go():
    js = open("static/shell.js", encoding="utf-8").read()
    assert "Before you go, who should sign in from now on?" in js
    assert "/api/household/remove-me/handover/start" in js
