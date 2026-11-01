"""
New terms: everyone is asked to agree again (Emily, 2026-11-01).

Agreement is per adult. When legal.LEGAL_VERSION moves past what an adult
agreed to — including adults who never agreed to anything, because they
signed in by passphrase — /api/whoami says `legal_needs_agree` and the
shell puts the agree screen up before anything else. Shipping this asks
nobody: adults with no agreement on record are taken to have agreed to
legal.REASK_FROM, the version live when re-asking was built.

Nothing on the server is locked while an agreement is waiting: share
pages, the legal pages and the app's own calls all still answer.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import households, invites, legal, security, tools
from app.main import app

REPO = Path(__file__).resolve().parent.parent
NEXT = "2026-12-01"
PASS = "terms-reask-passphrase"


@pytest.fixture
def home():
    hid = households.create_household("The Readers", PASS)
    with tools.use_household(hid):
        julia = tools.add_member("Julia")["member_id"]
        tools.set_member_age_group("Julia", "adult")
        sam = tools.add_member("Sam")["member_id"]
        tools.set_member_age_group("Sam", "adult")
    return hid, julia, sam


@pytest.fixture
def new_terms(monkeypatch):
    monkeypatch.setattr(legal, "LEGAL_VERSION", NEXT)
    return NEXT


def _as(hid, mid):
    c = TestClient(app)
    c.cookies.set(security.COOKIE_NAME, security.issue_session(hid, mid))
    return c


def test_shipping_this_asks_nobody(home):
    """The deploy itself: the version is still the one live before re-asking
    existed, so a passphrase adult who never agreed is not asked."""
    assert legal.LEGAL_VERSION == legal.REASK_FROM
    hid, julia, _ = home
    assert _as(hid, julia).get("/api/whoami").json()["legal_needs_agree"] is False


def test_a_new_version_asks_an_adult_who_never_agreed(home, new_terms):
    hid, julia, sam = home
    assert _as(hid, julia).get("/api/whoami").json()["legal_needs_agree"] is True
    assert _as(hid, sam).get("/api/whoami").json()["legal_needs_agree"] is True


def test_agreeing_is_per_person(home, new_terms):
    hid, julia, sam = home
    phone = _as(hid, julia)
    res = phone.post("/api/legal/agree", json={"version": NEXT})
    assert res.status_code == 200, res.text
    me = phone.get("/api/whoami").json()
    assert me["legal_needs_agree"] is False
    assert me["legal_you_accepted_version"] == NEXT
    assert me["legal_you_agreed_how"] == "reask"
    # Sam hasn't.
    assert _as(hid, sam).get("/api/whoami").json()["legal_needs_agree"] is True


def test_an_adult_who_joined_by_invite_is_asked_again(home, monkeypatch):
    hid, julia, sam = home
    phone = TestClient(app)
    assert phone.post("/api/join", json={"token": invites.mint_invite(hid, sam), "agreed": True}).status_code == 200
    assert phone.get("/api/whoami").json()["legal_needs_agree"] is False
    monkeypatch.setattr(legal, "LEGAL_VERSION", NEXT)
    assert phone.get("/api/whoami").json()["legal_needs_agree"] is True


def test_agreeing_to_a_version_the_screen_did_not_show_is_refused(home, new_terms):
    hid, julia, _ = home
    phone = _as(hid, julia)
    assert phone.post("/api/legal/agree", json={"version": legal.REASK_FROM}).status_code == 409
    assert phone.get("/api/whoami").json()["legal_needs_agree"] is True


def test_a_device_that_is_nobody_yet_is_asked_after_the_pick(home, new_terms):
    hid, julia, _ = home
    phone = _as(hid, None)
    me = phone.get("/api/whoami").json()
    assert me["needs_pick"] is True and me["legal_needs_agree"] is False
    assert phone.post("/api/legal/agree", json={"version": NEXT}).status_code == 400
    picked = phone.post("/api/whoami/pick", json={"member_id": julia}).json()
    assert picked["legal_needs_agree"] is True


def test_someone_who_signs_up_on_the_new_version_is_not_asked_again(monkeypatch):
    """The email sign-up's tick is the household's row; it counts for the
    adult who set the household up."""
    from test_remove_me_hands_over_sign_in import _email_household

    for k, v in {"SMTP_HOST": "smtp.test", "SMTP_USER": "pomona@test", "SMTP_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    sent: list = []
    from app import account_email

    monkeypatch.setattr(account_email, "_deliver", lambda msg: sent.append(msg))
    monkeypatch.setattr(legal, "LEGAL_VERSION", NEXT)
    client, hid = _email_household(sent)
    me = client.get("/api/whoami").json()
    assert me["member"]["name"] == "Rowan"
    assert me["legal_needs_agree"] is False
    assert me["legal_you_agreed_how"] == "signup"
    # Sam, added at setup, never ticked anything.
    from test_remove_me_hands_over_sign_in import _member_id

    assert _as(hid, _member_id(hid, "Sam")).get("/api/whoami").json()["legal_needs_agree"] is True


def test_nothing_is_locked_while_an_agreement_waits(home, new_terms):
    """Share pages, the legal pages, static assets and the app's own calls
    keep answering: the ask is the shell's screen, not a server lock."""
    hid, julia, _ = home
    with tools.use_household(hid):
        token = tools.get_or_create_share_link()["token"]
    stranger = TestClient(app)
    assert stranger.get(f"/api/share/{token}").status_code == 200
    assert stranger.get(f"/share/{token}").status_code == 200
    for path in ("/terms", "/privacy", "/support", "/static/theme.css", "/static/api.js", "/login"):
        assert stranger.get(path).status_code == 200, path
    phone = _as(hid, julia)
    assert phone.get("/api/whoami").json()["legal_needs_agree"] is True
    assert phone.get("/api/grocery-list").status_code == 200
    assert phone.get("/terms").status_code == 200


def test_the_shell_asks_before_anything_else():
    js = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
    gate = js.index("if (shellWho.loaded && shellWho.legal_needs_agree) await openTermsScreen();")
    assert gate < js.index("if (shellWho.loaded && shellWho.ai_consent === '') await openAiConsentScreen();")
    assert "Pomona’s terms have changed" in js
    assert "Have a read, then agree to keep using Pomona." in js
    assert "Tick the box to say you agree, then Agree." in js
    assert "'/api/legal/agree'" in js
