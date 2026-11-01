"""
"Sign-in now goes to sam@example.com" (Emily, 2026-11-01).

After Remove me hands the household's sign-in to the adult who stays, that
adult is shown the address the leaver typed — a guard against a leaver
quietly pointing the house at an inbox they read — until they say it's
theirs or change it. Changing it needs no code from the handed address
(that code went to whoever typed it), only from the new one.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import account_email, security
from app.db import get_conn
from app.main import HANDED_ADDRESS_REPLACED_NOTICE, app

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
    # The handed address is told sign-in moved — but never where to, since
    # it may be the leaver's inbox (security review, 2026-11-01).
    told = [m for m in outbox[before:] if m["To"] == "rowans-other@example.com"]
    assert len(told) == 1
    body = told[0].get_content()
    assert "sam@example.com" not in body and "sam@" not in body
    assert "sign-in email was changed in Pomona" in body
    assert "Preferences → Sign-in email" in body
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


def test_another_device_changing_a_handed_address_is_told_to_that_address(outbox):
    """The reviewer's repro: the hand-over went to Sam's real inbox; another
    adult's device picks Sam and moves sign-in to an address of their own.
    Sam's inbox hears about it, and is not told where it went."""
    client, hid = _email_household(outbox, others=("Sam", "Carol"))
    sam = _hand_over(outbox, client, hid, "Sam", "sam@example.com")
    carols_device = _as(hid, sam)  # "Who's this?" -> Sam: no per-adult secret
    before = len(outbox)
    res = carols_device.post("/api/account/email/start", json={"email": "carol-evil@example.com"})
    assert res.json()["confirm_current"] is False
    code = _code_in(outbox[-1])
    res = carols_device.post("/api/account/email/verify", json={"email": "carol-evil@example.com", "code": code})
    assert res.status_code == 200, res.text
    told = [m for m in outbox[before:] if m["To"] == "sam@example.com"]
    assert len(told) == 1
    assert told[0].get_content().strip() == HANDED_ADDRESS_REPLACED_NOTICE.strip()
    assert "carol" not in told[0].get_content()


def test_the_handed_address_needs_its_code_again_after_seven_days(outbox):
    client, hid = _email_household(outbox)
    sam = _hand_over(outbox, client, hid, "Sam", "sam@example.com")
    conn = get_conn()
    conn.execute(
        "UPDATE sign_in_notices SET created_at = datetime('now', '-8 days') WHERE household_id = ?", (hid,)
    )
    conn.commit()
    conn.close()
    phone = _as(hid, sam)
    # Still shown until it's answered...
    assert phone.get("/api/whoami").json()["sign_in_note"]["email"] == "sam@example.com"
    # ...but replacing it now asks for the code at sam@example.com too.
    before = len(outbox)
    res = phone.post("/api/account/email/start", json={"email": "carol-evil@example.com"})
    assert res.json()["confirm_current"] is True
    assert sorted(m["To"] for m in outbox[before:]) == ["carol-evil@example.com", "sam@example.com"]
    new_code = _code_in([m for m in outbox[before:] if m["To"] == "carol-evil@example.com"][0])
    res = phone.post("/api/account/email/verify", json={"email": "carol-evil@example.com", "code": new_code})
    assert res.status_code == 400
    assert account_email.email_for_member(hid, sam) == "sam@example.com"


def _unpicked(hid, issued_at=None):
    c = TestClient(app)
    c.cookies.set(security.COOKIE_NAME, security.issue_session(hid, None, issued_at=issued_at))
    return c


def test_a_leavers_unpicked_second_device_is_signed_out_by_the_hand_over(outbox):
    client, hid = _email_household(outbox)
    # Rowan's iPad: signed in, never said who it is.
    ipad = _unpicked(hid, issued_at=int(time.time()) - 60)
    assert ipad.get("/api/whoami").status_code == 200
    # Sam's phone, which said it's Sam.
    sams_phone = _as(hid, _member_id(hid, "Sam"))
    sam = _hand_over(outbox, client, hid, "Sam", "rowans-other@example.com")

    # With one adult left the iPad would read as Sam; it's signed out instead.
    res = ipad.post("/api/account/sign-in-notice/dismiss")
    assert res.status_code == 401
    assert res.headers.get(security.SIGNED_OUT_HEADER) == "1"
    assert ipad.post("/api/account/email/start", json={"email": "rowan2@example.com"}).status_code == 401
    page = ipad.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith("/login")
    # Neither a re-pick nor a renewal brings it back.
    stale = security.issue_session(hid, None, issued_at=int(time.time()) - 60)
    again = TestClient(app)
    again.cookies.set(security.COOKIE_NAME, stale)
    res = again.post("/api/whoami/pick", json={"member_id": sam})
    assert res.status_code == 401
    assert security.read_session_member(again.cookies.get(security.COOKIE_NAME)) is None
    old = TestClient(app)
    old.cookies.set(
        security.COOKIE_NAME,
        security.issue_session(hid, None, issued_at=int(time.time()) - security.COOKIE_RENEW_AFTER - 60),
    )
    assert old.get("/api/whoami").status_code == 401

    # The notice is still waiting for Sam, and Sam's phone is still in.
    assert sams_phone.get("/api/whoami").json()["sign_in_note"]["email"] == "rowans-other@example.com"
    # A device that signs in afresh afterwards is let in as usual.
    fresh = _unpicked(hid, issued_at=int(time.time()) + 2)
    assert fresh.get("/api/whoami").status_code == 200
