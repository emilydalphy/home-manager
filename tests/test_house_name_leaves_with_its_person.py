"""
A household named after its main person ("Rowan’s house") stops carrying
that name when Rowan leaves (defect hunt 2026-11-02).

The name was given automatically at onboarding (households.name_chosen = 0)
and only ever while the placeholder was there, so once Rowan tapped Remove
me the house went on calling itself "Rowan’s house" for Sam — on Sam's
Preferences, the leave dialog, and the public share link ("Meals this week
at Rowan’s house"), naming somebody who is no longer in it. The automatic
name now follows the main person out; a name somebody chose never moves.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import security, tools
from app.db import get_conn
from app.main import app

from test_remove_me_hands_over_sign_in import _code_in, _email_household, _member_id, outbox  # noqa: F401


def _name(hid):
    conn = get_conn()
    try:
        return conn.execute("SELECT name FROM households WHERE id = ?", (hid,)).fetchone()["name"]
    finally:
        conn.close()


def _rowan_leaves(client, outbox, hid):
    sam = _member_id(hid, "Sam")
    client.post("/api/household/remove-me/handover/start", json={"member_id": sam, "email": "sam@example.com"})
    res = client.post(
        "/api/household/remove-me",
        json={"handover_member_id": sam, "email": "sam@example.com", "code": _code_in(outbox[-1])},
    )
    assert res.status_code == 200, res.text
    return sam


def _sams_phone(hid, sam):
    phone = TestClient(app)
    phone.cookies.set(security.COOKIE_NAME, security.issue_session(hid, sam))
    return phone


def test_remove_me_takes_the_main_persons_name_off_the_house(outbox):
    client, hid = _email_household(outbox)
    assert _name(hid) == "Rowan’s house"
    sam = _rowan_leaves(client, outbox, hid)
    phone = _sams_phone(hid, sam)
    assert phone.get("/api/whoami").json()["household_name"] == "Sam’s house"
    with tools.use_household(hid):
        token = tools.get_or_create_share_link()["token"]
    assert TestClient(app).get(f"/api/share/{token}").json()["household_name"] == "Sam’s house"


def test_the_main_person_taking_out_someone_else_leaves_the_name_alone(outbox):
    client, hid = _email_household(outbox, others=("Sam", "Alex"))
    alex = _member_id(hid, "Alex")
    res = client.post("/api/household/members/remove", json={"member_id": alex})
    assert res.status_code == 200, res.text
    assert _name(hid) == "Rowan’s house"


def test_a_name_somebody_chose_stays_when_the_main_person_leaves(outbox):
    client, hid = _email_household(outbox)
    assert client.post("/api/household/name", json={"name": "Rowan’s house"}).status_code == 200
    _rowan_leaves(client, outbox, hid)
    assert _name(hid) == "Rowan’s house"


def test_main_person_moved_first_then_the_named_adult_leaves(outbox):
    client, hid = _email_household(outbox, others=("Sam", "Alex"))
    sam = _member_id(hid, "Sam")
    with tools.use_household(hid):
        tools.set_primary_member(sam)
    # Who's here moving the main person is not a rename (Emily's call to make).
    assert _name(hid) == "Rowan’s house"
    # Sam takes Rowan out: the house is no longer called after somebody gone.
    phone = _sams_phone(hid, sam)
    res = phone.post("/api/household/members/remove", json={"member_id": _member_id(hid, "Rowan")})
    assert res.status_code == 200, res.text
    assert _name(hid) == "Sam’s house"
