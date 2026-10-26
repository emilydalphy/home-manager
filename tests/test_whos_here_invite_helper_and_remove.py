"""
Preferences -> Who's here: invite a helper, and take someone out
(two Loop Board cards, 2026-10-21).

A. "Invite another adult" asks setup's choice — eats here, or "Someone not
   eating here" — so a nanny or a grandparent who helps can be invited
   after setup, and lands in the "Helps out" row, never planned for.
B. The main person can take another adult or a helper out of the household
   from their row (POST /api/household/members/remove). It is "Remove me"'s
   removal (household_deletion.remove_member): their devices are signed
   out, their invite link stops working, a cook answer naming them goes
   back to unanswered, and what they added stays. Only the main person
   (Emily's open question; this is the default), never the last adult,
   never another household's member.

The rendering half runs the real shell.js functions under node.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import households, tools
from app.db import get_conn
from app.main import app
from tests import nodeharness
from tests.test_settings_main_person_row import SHELL_JS, _function

BETA = "whos-here-remove-passphrase"


@pytest.fixture
def beta_household():
    return households.create_household("The Removers", BETA)


def _adult(name: str, household: int) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, "adult")
    return member_id


def _device(member_id: int | None, passphrase: str = BETA) -> TestClient:
    c = TestClient(app)
    res = c.post("/login", data={"password": passphrase, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    if member_id is not None:
        assert c.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200
    return c


def _adults(device: TestClient) -> dict:
    return {a["name"]: a for a in device.get("/api/household/invites").json()["adults"]}


# --- A: inviting a helper from Preferences ---------------------------------------

def test_a_helper_invited_from_preferences_helps_out_and_is_never_planned_for(beta_household):
    julia = _adult("Julia", beta_household)
    _adult("Sam", beta_household)
    phone = _device(julia)
    res = phone.post("/api/household/invites", json={"name": "Maria", "eats_here": False})
    assert res.status_code == 200, res.text
    assert res.json()["path"].startswith("/join#")
    maria = _adults(phone)["Maria"]
    assert maria["eats_here"] is False and maria["joined"] is False
    # Not one of the people meals are planned for: no portions, no
    # allergies, no age chips — Who's here draws her from the invite read.
    assert "Maria" not in [m["name"] for m in phone.get("/api/memory").json()["members"]]
    # Someone who eats here is invited exactly as before.
    assert phone.post("/api/household/invites", json={"name": "Ana"}).status_code == 200
    assert "Ana" in [m["name"] for m in phone.get("/api/memory").json()["members"]]


# --- B: taking someone out -----------------------------------------------------

def test_the_main_person_takes_a_helper_out_and_what_they_added_stays(beta_household):
    julia = _adult("Julia", beta_household)
    _adult("Sam", beta_household)
    phone = _device(julia)
    minted = phone.post("/api/household/invites", json={"name": "Maria", "eats_here": False}).json()
    maria = minted["member"]["id"]
    # Her old link, sent once and never opened, and a second one she used.
    old_token = minted["path"].split("#", 1)[1]
    used_token = phone.post("/api/household/invites", json={"member_id": maria}).json()["path"].split("#", 1)[1]
    assert old_token != used_token
    marias_phone = TestClient(app)
    assert marias_phone.post("/api/join", json={"token": used_token}).status_code == 200
    assert marias_phone.get("/api/whoami").json()["member"]["id"] == maria
    added = marias_phone.post("/api/grocery-list/add", json={"item": "Bananas", "added_by": "Maria"})
    assert added.status_code == 200, added.text
    fresh_token = phone.post("/api/household/invites", json={"member_id": maria}).json()["path"].split("#", 1)[1]

    assert _adults(phone)["Maria"]["can_remove"] is True
    res = phone.post("/api/household/members/remove", json={"member_id": maria})
    assert res.status_code == 200, res.text
    assert res.json() == {"removed": True, "name": "Maria"}

    # Gone from Who's here; her phone is signed out; her link is dead.
    assert "Maria" not in _adults(phone)
    assert marias_phone.get("/api/whoami").status_code == 401
    assert TestClient(app).post("/api/join", json={"token": fresh_token}).status_code == 410
    # The person asking stays signed in, as herself.
    assert phone.get("/api/whoami").json()["member"]["id"] == julia
    # What she added stays with the household.
    conn = get_conn()
    try:
        kept = conn.execute(
            "SELECT COUNT(*) FROM grocery_items WHERE household_id = ? AND item = 'Bananas'",
            (beta_household,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert kept == 1


def test_a_removed_cook_goes_back_to_unanswered(beta_household):
    julia = _adult("Julia", beta_household)
    vineeth = _adult("Vineeth", beta_household)
    phone = _device(julia)
    res = phone.post("/api/onboarding/rhythm", json={"cooking_role": "one_person", "cooking_role_who": "Vineeth"})
    assert res.status_code == 200, res.text
    assert phone.post("/api/household/members/remove", json={"member_id": vineeth}).status_code == 200
    assert phone.get("/api/memory").json()["rhythm"]["cooking_role"] is None


def test_only_the_main_person_can_take_someone_out(beta_household):
    julia = _adult("Julia", beta_household)
    sam = _adult("Sam", beta_household)
    maria = _adult("Maria", beta_household)
    main = _device(julia)  # the first adult, and the main person
    sams_phone = _device(sam)
    assert _adults(sams_phone)["Maria"]["can_remove"] is False
    res = sams_phone.post("/api/household/members/remove", json={"member_id": maria})
    assert res.status_code == 403, res.text
    # A device that hasn't said who it is can't either.
    assert _device(None).post("/api/household/members/remove", json={"member_id": maria}).status_code == 403
    # Nobody was touched, and the main person can't use this on herself.
    assert set(_adults(sams_phone)) == {"Julia", "Sam", "Maria"}
    assert _adults(main)["Julia"]["can_remove"] is False
    assert main.post("/api/household/members/remove", json={"member_id": julia}).status_code == 400
    assert main.get("/api/whoami").json()["member"]["id"] == julia


def test_a_household_cannot_take_out_another_households_member(beta_household):
    julia = _adult("Julia", beta_household)
    _adult("Sam", beta_household)
    other = households.create_household("Next Door", "next-door-passphrase")
    _adult("Pat", other)
    neighbour = _adult("Lee", other)
    res = _device(julia).post("/api/household/members/remove", json={"member_id": neighbour})
    assert res.status_code == 404, res.text
    conn = get_conn()
    try:
        assert conn.execute("SELECT household_id FROM members WHERE id = ?", (neighbour,)).fetchone()[0] == other
    finally:
        conn.close()


# --- the rendering half ----------------------------------------------------------

def _harness() -> str:
    return (
        _function("escapeHtml") + "\n"
        + _function("wwkChip") + "\n"
        + _function("wwkAddChip") + "\n"
        + _function("wwkNote") + "\n"
        + _function("inviteRowHtml") + "\n"
        + _function("inviteRemoveHtml") + "\n"
        + _function("inviteNewHtml") + "\n"
    )


def _run(state: dict, expr: str) -> str:
    script = _harness() + "var inviteState = %s;\nconsole.log(JSON.stringify(%s));\n" % (json.dumps(state), expr)
    res = nodeharness.run_node(script)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


STATE = {"adults": [], "newOpen": True, "newEatsHere": True, "removeAsk": None, "busy": False, "ready": {}}


def test_invite_another_adult_asks_whether_they_eat_here():
    html = _run(STATE, "inviteNewHtml()")
    assert "Eats here" in html and "Someone not eating here" in html
    assert 'data-value="1"' in html.split("Eats here")[0].rsplit("<button", 1)[1]
    assert "I won’t plan meals for them" not in html
    helper = _run(dict(STATE, newEatsHere=False), "inviteNewHtml()")
    assert "I won’t plan meals for them" in helper


def test_the_remove_line_is_on_rows_the_server_says_can_go_and_confirms_by_name():
    maria = {"id": 9, "name": "Maria", "is_you": False, "joined": True, "joined_label": "Oct 12",
             "eats_here": False, "can_remove": True}
    row = _run(STATE, "inviteRemoveHtml(%s)" % json.dumps(maria))
    assert "Take Maria out of the household" in row
    assert "Take Maria out of the household?" not in row
    assert _run(STATE, "inviteRemoveHtml(%s)" % json.dumps(dict(maria, can_remove=False))) == ""
    asking = _run(dict(STATE, removeAsk="9"), "inviteRemoveHtml(%s)" % json.dumps(maria))
    assert "Take Maria out of the household?" in asking
    assert 'data-invite="remove-yes" data-member-id="9"' in asking and "Keep Maria" in asking
