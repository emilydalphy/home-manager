"""
An adult who joins by invite link ticks the Terms and Privacy Policy
themselves, recorded per person (Loop Board, Emily 2026-10-31).

The household's own agreement is made at email sign-up by whoever set it
up; a second adult joining by link never saw that screen. Now /join asks
first ("I agree to the Terms and Privacy Policy"), /api/join refuses an
unticked join BEFORE it spends the link, and a successful join writes a
member_legal_acceptances row for that adult.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import account_email, household_deletion, households, invites, legal, tools
from app.db import get_conn
from app.main import JOIN_AGREE_LINE, app

REPO = Path(__file__).resolve().parent.parent
PASS = "invite-agrees-to-terms-passphrase"


@pytest.fixture
def home():
    hid = households.create_household("The Tickers", PASS)
    with tools.use_household(hid):
        julia = tools.add_member("Julia")["member_id"]
        tools.set_member_age_group("Julia", "adult")
        sam = tools.add_member("Sam")["member_id"]
        tools.set_member_age_group("Sam", "adult")
    return hid, julia, sam


def _rows(hid, mid):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT version FROM member_legal_acceptances WHERE household_id = ? AND member_id = ?", (hid, mid)
        ).fetchall()
    finally:
        conn.close()


def test_an_unticked_join_is_refused_and_the_link_still_works(home):
    hid, julia, sam = home
    token = invites.mint_invite(hid, sam, invited_by=julia)
    phone = TestClient(app)
    res = phone.post("/api/join", json={"token": token})
    assert res.status_code == 400 and res.json()["detail"] == JOIN_AGREE_LINE
    res = phone.post("/api/join", json={"token": token, "agreed": False})
    assert res.status_code == 400
    assert _rows(hid, sam) == []
    # Nothing was spent: ticking and joining now works.
    assert phone.post("/api/join", json={"token": token, "agreed": True}).status_code == 200
    assert [r["version"] for r in _rows(hid, sam)] == [legal.LEGAL_VERSION]


def test_the_agreement_is_the_joiners_own(home):
    hid, julia, sam = home
    phone = TestClient(app)
    assert phone.post("/api/join", json={"token": invites.mint_invite(hid, sam), "agreed": True}).status_code == 200
    me = phone.get("/api/whoami").json()
    assert me["member"]["name"] == "Sam"
    assert me["legal_you_accepted_version"] == legal.LEGAL_VERSION
    # Julia didn't tick anything; her row says nothing for her.
    assert _rows(hid, julia) == []
    assert account_email.member_accepted_legal_version(hid, julia) is None
    # ...and the household-level record (email sign-up) is untouched.
    assert account_email.accepted_legal_version(hid) is None


def test_a_spent_link_records_nothing(home):
    hid, julia, sam = home
    token = invites.mint_invite(hid, sam)
    assert TestClient(app).post("/api/join", json={"token": token, "agreed": True}).status_code == 200
    assert TestClient(app).post("/api/join", json={"token": token, "agreed": True}).status_code == 410
    assert len(_rows(hid, sam)) == 1


def test_their_agreement_goes_when_they_leave(home):
    hid, julia, sam = home
    assert TestClient(app).post("/api/join", json={"token": invites.mint_invite(hid, sam), "agreed": True}).status_code == 200
    household_deletion.remove_member(hid, sam)
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM member_legal_acceptances WHERE household_id = ?", (hid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_the_join_page_asks_for_the_tick_before_it_spends_the_link():
    html = (REPO / "static" / "join.html").read_text(encoding="utf-8")
    block = html[html.index('id="jn-agree"'):html.index('<div class="jn-state"', html.index('id="jn-agree"') + 1)]
    assert 'type="checkbox"' in block
    assert 'href="/terms"' in block and 'href="/privacy"' in block
    assert block.count("btn-primary") == 1
    assert "JSON.stringify({ token: token, agreed: true })" in html
    # The page opens on the question, not on spending the link.
    assert html.rstrip().endswith("</html>")
    assert "askToAgree();\n  }\n  start();\n})();" in html
    # The About line on the joiner's own phone reads their own agreement.
    shell = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
    assert "legal_you_accepted_version" in shell and "you agreed when you joined" in shell
