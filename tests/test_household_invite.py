"""
Invite the other adult with a one-time link (Loop Board "Invite my partner
from inside the app", Emily 2026-09-25).

The adult who set Pomona up taps "Invite Vineeth"; opening the link signs
Vineeth in to THIS household AS Vineeth — no passphrase, no "Who's this?".
One use, seven days, adults only, and the chat agent can never make one.

These run through the real HTTP stack with real cookies wherever the
property is about signing in, the way tests/test_adult_login.py and
tests/test_multi_household.py do.
"""
from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

from app import households, invites, security, tools
from app.db import get_conn
from app.main import app

REPO = Path(__file__).resolve().parent.parent
BETA_PASSPHRASE = "beta-tester-passphrase"


def _sign_in(client, password="test-password"):
    res = client.post("/login", data={"password": password, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return client.cookies[security.COOKIE_NAME]


def _member(name: str, age_group: str = "adult", household: int = 1) -> int:
    with tools.use_household(household):
        member_id = tools.add_member(name)["member_id"]
        tools.set_member_age_group(name, age_group)
    return member_id


def _pick(client, member_id: int):
    res = client.post("/api/whoami/pick", json={"member_id": member_id})
    assert res.status_code == 200


def _token(path: str) -> str:
    assert path.startswith("/join#"), path
    return path.split("#", 1)[1]


@pytest.fixture
def home(client):
    """Household 1: Emily (signed in, picked) and Vineeth, who hasn't joined."""
    emily = _member("Emily")
    vineeth = _member("Vineeth")
    _sign_in(client)
    _pick(client, emily)
    return {"client": client, "Emily": emily, "Vineeth": vineeth}


def _fresh_client():
    return TestClient(app)


def _invite(client, **body) -> dict:
    res = client.post("/api/household/invites", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ---------- minting ----------

def test_minting_needs_a_signed_in_session(client):
    vineeth = _member("Vineeth")
    res = client.post("/api/household/invites", json={"member_id": vineeth})
    assert res.status_code == 401
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) AS c FROM household_invites").fetchone()["c"] == 0
    conn.close()


def test_the_invite_list_is_not_public(client):
    assert client.get("/api/household/invites").status_code == 401
    assert not security.is_public_path("/api/household/invites")


def test_mint_returns_a_fragment_link_and_stores_only_a_hash(home):
    body = _invite(home["client"], member_id=home["Vineeth"])
    token = _token(body["path"])
    assert len(token) >= 40
    assert body["member"]["name"] == "Vineeth"
    conn = get_conn()
    rows = conn.execute("SELECT * FROM household_invites").fetchall()
    conn.close()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["household_id"] == 1 and row["member_id"] == home["Vineeth"]
    assert row["invited_by_member_id"] == home["Emily"]
    assert token not in " ".join(str(v) for v in row.values()), "the raw token must never be stored"
    assert row["token_hash"] == invites.hash_token(token)


def test_you_cannot_invite_yourself(home):
    res = home["client"].post("/api/household/invites", json={"member_id": home["Emily"]})
    assert res.status_code == 400


def test_only_adults_can_be_invited(home):
    kid = _member("Maya", "child")
    res = home["client"].post("/api/household/invites", json={"member_id": kid})
    assert res.status_code == 400
    assert "adult" in res.json()["detail"].lower()


def test_a_member_of_another_household_cannot_be_invited(home):
    beta = households.create_household("Beta", BETA_PASSPHRASE)
    stranger = _member("Julia", household=beta)
    res = home["client"].post("/api/household/invites", json={"member_id": stranger})
    assert res.status_code == 400
    # Same words as a child: the route says nothing about which ids exist.
    kid = _member("Maya", "child")
    other = home["client"].post("/api/household/invites", json={"member_id": kid})
    assert res.json()["detail"] == other.json()["detail"]


def test_a_first_name_adds_them_as_an_adult(home):
    client = home["client"]
    _member("Vineeth")  # already there — unrelated
    body = _invite(client, name="  Sam  ")
    assert body["member"]["name"] == "Sam"
    with tools.use_household(1):
        adults = [a["name"] for a in tools.household_adults()]
    assert "Sam" in adults
    # The same name again reuses them rather than adding a second Sam.
    again = _invite(client, name="sam")
    assert again["member"]["id"] == body["member"]["id"]


def test_a_first_name_never_promotes_a_child(home):
    _member("Maya", "child")
    res = home["client"].post("/api/household/invites", json={"name": "Maya"})
    assert res.status_code == 400
    conn = get_conn()
    row = conn.execute("SELECT age_group FROM members WHERE name = 'Maya'").fetchone()
    conn.close()
    assert row["age_group"] == "child"


def test_an_empty_ask_is_refused(home):
    assert home["client"].post("/api/household/invites", json={}).status_code == 400
    assert home["client"].post("/api/household/invites", json={"name": "   "}).status_code == 400


# ---------- redeeming ----------

def test_the_link_signs_them_in_as_that_adult_with_no_questions(home):
    token = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    with _fresh_client() as phone:
        # The landing page is public — no redirect to /login.
        page = phone.get("/join", headers={"accept": "text/html"}, follow_redirects=False)
        assert page.status_code == 200
        assert "/api/join" in page.text
        assert phone.get("/api/whoami").status_code == 401
        res = phone.post("/api/join", json={"token": token})
        assert res.status_code == 200
        assert security.COOKIE_NAME in phone.cookies
        me = phone.get("/api/whoami").json()
        assert me["household_id"] == 1
        assert me["member"]["name"] == "Vineeth"
        assert me["needs_pick"] is False, "they must never be asked Who's this?"
        # And the app itself opens for them.
        assert phone.get("/", headers={"accept": "text/html"}, follow_redirects=False).status_code == 200


def test_the_link_works_once(home):
    token = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    with _fresh_client() as first:
        assert first.post("/api/join", json={"token": token}).status_code == 200
    with _fresh_client() as second:
        res = second.post("/api/join", json={"token": token})
        assert res.status_code == 410
        assert security.COOKIE_NAME not in second.cookies
        assert second.get("/api/whoami").status_code == 401


def test_the_link_runs_out_after_seven_days(home):
    start = datetime.datetime(2026, 9, 25, 12, 0, 0)
    with freeze_time(start):
        token = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
        late = _token(_invite(home["client"], name="Sam")["path"])
    with freeze_time(start + datetime.timedelta(days=6, hours=23)):
        with _fresh_client() as phone:
            assert phone.post("/api/join", json={"token": token}).status_code == 200
    with freeze_time(start + datetime.timedelta(days=7, seconds=1)):
        with _fresh_client() as phone:
            assert phone.post("/api/join", json={"token": late}).status_code == 410


def test_an_unknown_or_malformed_token_is_the_same_answer(client):
    for token in ["", "nope", "x" * 256]:
        with _fresh_client() as phone:
            assert phone.post("/api/join", json={"token": token}).status_code == 410
    with _fresh_client() as phone:
        assert phone.post("/api/join", json={"token": "x" * 257}).status_code == 422


def test_a_new_link_leaves_the_sent_one_working_until_either_is_used(home):
    """
    Tapping Invite again (and maybe cancelling the share sheet) must not
    kill the link already in their messages; once one is used, the rest
    retire.
    """
    old = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    new = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    spare = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    assert len({old, new, spare}) == 3
    with _fresh_client() as phone:
        assert phone.post("/api/join", json={"token": old}).status_code == 200
    for token in (new, spare):
        with _fresh_client() as phone:
            assert phone.post("/api/join", json={"token": token}).status_code == 410


def test_inviting_by_name_in_a_one_adult_house_remembers_who_sent_it(client):
    only = _member("Emily")
    _sign_in(client)  # no pick: the lone adult resolves on her own
    body = _invite(client, name="Vineeth")
    conn = get_conn()
    row = conn.execute("SELECT invited_by_member_id FROM household_invites").fetchone()
    joined = conn.execute("SELECT joined_at FROM members WHERE id = ?", (only,)).fetchone()["joined_at"]
    conn.close()
    assert row["invited_by_member_id"] == only
    assert joined is not None
    assert body["member"]["is_you"] is False


def test_an_impossible_member_id_is_a_plain_refusal(home):
    for bad in (2**70, 0, -3):
        res = home["client"].post("/api/household/invites", json={"member_id": bad})
        assert res.status_code in (400, 422), (bad, res.status_code)


def test_someone_no_longer_an_adult_is_not_signed_in(home):
    token = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    with tools.use_household(1):
        tools.set_member_age_group("Vineeth", "teen")
    with _fresh_client() as phone:
        assert phone.post("/api/join", json={"token": token}).status_code == 410
        assert phone.get("/api/whoami").status_code == 401


def test_a_household_a_link_opened_while_signed_in_to_b_lands_in_a_only(home):
    """
    The link, not whatever cookie the browser had, says who this is — and
    household B is not touched by it.
    """
    beta = households.create_household("Beta", BETA_PASSPHRASE)
    _member("Julia", household=beta)
    token = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    with _fresh_client() as phone:
        _sign_in(phone, BETA_PASSPHRASE)
        assert phone.get("/api/whoami").json()["household_id"] == beta
        assert phone.post("/api/join", json={"token": token}).status_code == 200
        me = phone.get("/api/whoami").json()
        assert me["household_id"] == 1 and me["member"]["name"] == "Vineeth"
    with tools.use_household(beta):
        assert [a["name"] for a in tools.household_adults()] == ["Julia"]


def test_household_b_cannot_mint_for_household_a(home):
    beta = households.create_household("Beta", BETA_PASSPHRASE)
    with _fresh_client() as b:
        _sign_in(b, BETA_PASSPHRASE)
        res = b.post("/api/household/invites", json={"member_id": home["Vineeth"]})
        assert res.status_code == 400
        # B's list never shows A's adults.
        assert b.get("/api/household/invites").json()["adults"] == []
    assert beta


def test_passphrase_sign_in_still_works_alongside(home):
    _invite(home["client"], member_id=home["Vineeth"])
    with _fresh_client() as laptop:
        _sign_in(laptop)
        assert laptop.get("/api/whoami").json()["household_id"] == 1
    with _fresh_client() as bad:
        res = bad.post("/login", data={"password": "wrong-one", "next": "/"}, follow_redirects=False)
        assert res.status_code == 401


def test_redeeming_is_rate_limited_like_login(client):
    with _fresh_client() as phone:
        codes = [phone.post("/api/join", json={"token": f"guess-{i}"}).status_code for i in range(10)]
    assert codes[:8] == [410] * 8
    assert codes[8] == 429


def test_the_token_is_never_logged(home, caplog):
    caplog.set_level(logging.DEBUG)
    token = _token(_invite(home["client"], member_id=home["Vineeth"])["path"])
    with _fresh_client() as phone:
        phone.post("/api/join", json={"token": token})
        phone.post("/api/join", json={"token": token})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Invite minted" in text and "Invite redeemed" in text
    assert token not in text


def test_join_paths_are_public_and_nothing_else_came_with_them():
    assert security.is_public_path("/join")
    assert security.is_public_path("/api/join")
    assert not security.is_public_path("/join/../api/household/invites")
    assert not security.is_public_path("/api/join/x")


# ---------- joined ----------

def test_preferences_shows_them_as_joined_after(home):
    client = home["client"]
    before = {a["name"]: a for a in client.get("/api/household/invites").json()["adults"]}
    assert before["Vineeth"]["joined"] is False
    assert before["Emily"]["is_you"] is True
    token = _token(_invite(client, member_id=home["Vineeth"])["path"])
    with freeze_time("2026-09-27 15:00:00"):
        with _fresh_client() as phone:
            assert phone.post("/api/join", json={"token": token}).status_code == 200
    after = {a["name"]: a for a in client.get("/api/household/invites").json()["adults"]}
    assert after["Vineeth"]["joined"] is True
    assert after["Vineeth"]["joined_label"] == "Sep 27"
    # The inviter is plainly using the app — joined too, so the other
    # adult's Preferences never offers to invite them.
    assert after["Emily"]["joined"] is True
    # A fresh link is still allowed (a new phone), and the join date stays.
    _invite(client, member_id=home["Vineeth"])
    again = {a["name"]: a for a in client.get("/api/household/invites").json()["adults"]}
    assert again["Vineeth"]["joined_label"] == "Sep 27"


def test_whoami_marks_joined_only_on_a_pick(client):
    only = _member("Emily")
    _sign_in(client)
    # One adult, no pick: the overnight report's view. Not a join.
    assert client.get("/api/whoami").json()["member"]["id"] == only
    conn = get_conn()
    assert conn.execute("SELECT joined_at FROM members WHERE id = ?", (only,)).fetchone()["joined_at"] is None
    conn.close()
    _pick(client, only)
    conn = get_conn()
    assert conn.execute("SELECT joined_at FROM members WHERE id = ?", (only,)).fetchone()["joined_at"] is not None
    conn.close()


def test_children_never_appear_in_the_invite_list(home):
    _member("Maya", "child")
    names = [a["name"] for a in home["client"].get("/api/household/invites").json()["adults"]]
    assert names == ["Emily", "Vineeth"]


# ---------- the chat agent can never mint ----------

def test_nothing_in_tools_can_mint_or_redeem():
    for name in ("mint_invite", "redeem_invite", "add_adult", "invites"):
        assert not hasattr(tools, name), f"tools.{name} would be callable by the chat agent"
    for path in (REPO / "app" / "tools").glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"import\s+invites|\binvites\.(mint|redeem|add_adult)", src), path
        assert "from .. import invites" not in src and "from ..invites" not in src, path
        assert "household_invites" not in src, f"{path.name} touches the invite table"


def test_no_agent_tool_is_about_invites():
    from app.agent import TOOL_DEFINITIONS, TOOL_FUNCTIONS

    names = set(TOOL_FUNCTIONS) | {t["name"] for t in TOOL_DEFINITIONS}
    assert not [n for n in names if "invite" in n.lower() or "join" in n.lower()]


# ---------- the screens ----------

def test_the_landing_page_reads_the_fragment_and_says_ask_for_a_new_one():
    html = (REPO / "static" / "join.html").read_text(encoding="utf-8")
    assert "location.hash" in html
    assert "replaceState" in html, "the token should leave the address bar at once"
    assert "fetch('/api/join'" in html
    assert "This link has run out." in html
    assert "Ask whoever sent it for a new one." in html
    body = html[html.index("<body>"):html.index("<script>", html.index("<body>"))].lower()
    assert "password" not in body
    assert "passphrase" not in body and "sign in" not in body, "never a sign-in error"


def test_preferences_offers_invite_by_name_and_shares_or_copies():
    js = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
    assert "'Invite ' + escapeHtml(adult.name)" in js
    assert "navigator.share" in js and "navigator.clipboard" in js
    assert "savedLine(name + '’s link', 'copied')" in js
    assert "Send a new link" in js
    assert '>Joined' in js
    assert "fetch('/api/household/invites'" in js


def test_setup_asks_whether_anyone_else_helps_run_the_house():
    html = (REPO / "static" / "onboarding.html").read_text(encoding="utf-8")
    assert "Does anyone else help run the house?" in html
    assert "Just me" in html
    assert "fetch('/api/household/invites'" in html
    assert "<h2>Bring the rest of the house in?</h2>" not in html
    # A button waiting for its share tap must not mint a second link on
    # that tap (verifier, 2026-09-25): the first handler steps aside.
    assert "if (btn.disabled || btn.dataset.ready) return;" in html
