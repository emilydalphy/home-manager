"""
"Sign-in now goes to sam@example.com" (Emily, 2026-11-01).

After Remove me hands the household's sign-in to the adult who stays, that
adult is shown the address the leaver typed — a guard against a leaver
quietly pointing the house at an inbox they read — until they say it's
theirs or change it. Changing it needs no code from the handed address
(that code went to whoever typed it), only from the new one.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import account_email, security
from app.main import app

from test_remove_me_hands_over_sign_in import _code_in, _email_household, _member_id, outbox  # noqa: F401


def _hand_over(outbox, client, hid, to_name, address):
    target = _member_id(hid, to_name)
    res = client.post("/api/household/remove-me/handover/start", json={"member_id": target, "email": address})
    assert res.status_code == 200, res.text
    code = _code_in(outbox[-1])
    res = client.post(
        "/api/household/remove-me",
        json={"handover_member_id": target, "email": address, "code": code},
    )
    assert res.status_code == 200, res.text
    return target


def _as(hid, member_id):
    c = TestClient(app)
    c.cookies.set(security.COOKIE_NAME, security.issue_session(hid, member_id))
    return c


def test_the_adult_who_stays_sees_where_sign_in_now_goes(outbox):
    client, hid = _email_household(outbox, others=("Sam", "Carol"))
    sam = _hand_over(outbox, client, hid, "Sam", "rowans-other@example.com")
    carol = _member_id(hid, "Carol")

    note = _as(hid, sam).get("/api/whoami").json()["sign_in_note"]
    assert note == {"email": "rowans-other@example.com", "from_name": "Rowan"}
    # Every open until it's answered.
    assert _as(hid, sam).get("/api/whoami").json()["sign_in_note"] == note
    # Only the adult it was handed to.
    assert _as(hid, carol).get("/api/whoami").json()["sign_in_note"] is None


def test_another_household_never_sees_it(outbox, signed_in):
    client, hid = _email_household(outbox)
    _hand_over(outbox, client, hid, "Sam", "sam@example.com")
    assert signed_in.get("/api/whoami").json()["sign_in_note"] is None


def test_thats_my_email_ends_it_and_the_usual_second_code_is_back(outbox):
    client, hid = _email_household(outbox)
    sam = _hand_over(outbox, client, hid, "Sam", "sam@example.com")
    phone = _as(hid, sam)
    assert phone.post("/api/account/sign-in-notice/dismiss").json() == {"ok": True}
    assert phone.get("/api/whoami").json()["sign_in_note"] is None
    # From now on it's Sam's own address: replacing it needs its code too.
    res = phone.post("/api/account/email/start", json={"email": "sam.new@example.com"})
    assert res.json()["confirm_current"] is True


def test_not_your_email_can_be_changed_without_the_handed_addresss_code(outbox):
    client, hid = _email_household(outbox)
    sam = _hand_over(outbox, client, hid, "Sam", "rowans-other@example.com")
    phone = _as(hid, sam)
    before = len(outbox)
    res = phone.post("/api/account/email/start", json={"email": "sam@example.com"})
    assert res.status_code == 200
    assert res.json()["confirm_current"] is False
    # One code, to the new address; nothing goes to the handed one.
    sent = outbox[before:]
    assert [m["To"] for m in sent] == ["sam@example.com"]
    res = phone.post("/api/account/email/verify", json={"email": "sam@example.com", "code": _code_in(sent[-1])})
    assert res.status_code == 200, res.text
    assert account_email.email_for_member(hid, sam) == "sam@example.com"
    # The handed address hears nothing about where sign-in went.
    assert all(m["To"] != "rowans-other@example.com" for m in outbox[before:])
    assert phone.get("/api/whoami").json()["sign_in_note"] is None
    # And the handed address no longer opens the house.
    assert account_email.lookup("rowans-other@example.com") is None


def test_the_shell_says_it_in_full():
    with open("static/shell.js", encoding="utf-8") as f:
        js = f.read()
    assert "'Sign-in now goes to ' + email" in js
    assert "' set this before leaving.'" in js
    assert "If it isn’t your email, change it now." in js
    assert "That’s my email" in js
    assert "/api/account/sign-in-notice/dismiss" in js
