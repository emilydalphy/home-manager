"""
An invited adult's email belongs to the family household from day one
(Loop Board "An invited adult who signs out and signs back in by email
lands in a brand-new empty household, not their family's" — Emily's
Option A, 2026-10-12).

The defect: Rowan signs up by email and invites Alex. Alex opens the link,
later signs out, and signs back in with alex@x — an address nobody knew,
so a new, empty household was made and onboarding started. An email
household has no passphrase, so the email screen was the only way back.

Now /join asks for Alex's email the moment the link has said who Alex is
(the same /api/account/email routes Preferences uses), and onboarding has
"Not you? Sign out".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import account_email
from app.main import app

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def outbox(monkeypatch):
    """SMTP 'configured', with delivery captured instead of sent."""
    for k, v in {"SMTP_HOST": "smtp.test", "SMTP_USER": "pomona@test", "SMTP_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    sent: list = []
    monkeypatch.setattr(account_email, "_deliver", lambda msg: sent.append(msg))
    return sent


def _code(outbox) -> str:
    return re.search(r"\b(\d{6})\b", outbox[-1].get_content()).group(1)


def _email_sign_in(client, email, outbox):
    assert client.post("/api/auth/email/start", json={"email": email}).status_code == 200
    res = client.post(
        "/api/auth/email/verify", json={"email": email, "code": _code(outbox), "agreed": True, "next": "/"}
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_an_invited_adult_who_adds_their_email_on_the_link_comes_back_to_the_same_household(outbox):
    # Rowan's household exists only by email: no passphrase anywhere.
    rowan = TestClient(app)
    _email_sign_in(rowan, "rowan@example.com", outbox)
    res = rowan.post(
        "/api/onboarding/household",
        json={"members": [{"name": "Rowan", "age_group": "Adult"}], "pets": [], "goals": "", "primary_name": "Rowan"},
    )
    assert res.status_code == 200, res.text
    home = rowan.get("/api/whoami").json()["household_id"]
    path = rowan.post("/api/household/invites", json={"name": "Alex"}).json()["path"]

    # Alex opens the link: signed in as Alex, and the page's next question
    # — has Alex an email yet? — says no, and that Alex may add one.
    alex = TestClient(app)
    assert alex.post("/api/join", json={"token": path.split("#", 1)[1]}).status_code == 200
    assert alex.get("/api/account/email").json() == {"email": None, "can_change": True}
    assert alex.post("/api/account/email/start", json={"email": "alex@example.com"}).status_code == 200
    res = alex.post("/api/account/email/verify", json={"email": "alex@example.com", "code": _code(outbox)})
    assert res.status_code == 200, res.text

    # Signed out (a new browser has no cookies), Alex signs in by email
    # and lands at home, as Alex — not in a new household.
    later = TestClient(app)
    answer = _email_sign_in(later, "alex@example.com", outbox)
    assert answer["new_household"] is False
    assert answer["next"] != "/onboarding"
    me = later.get("/api/whoami").json()
    assert me["household_id"] == home
    assert me["member"]["name"] == "Alex"


def test_the_link_page_asks_for_the_email_after_the_link_lets_them_in():
    html = (REPO / "static" / "join.html").read_text(encoding="utf-8")
    # A successful join asks, rather than going straight to "/".
    assert "if (res.ok) { askForEmail(); return; }" in html
    script = html[html.index("async function askForEmail"):html.index("async function sendCode")]
    # Someone who already has an address (or can't add one) is not asked.
    assert "state.email || !state.can_change" in script and "into()" in script
    # Through api.js (public), the same routes Preferences uses.
    assert '<script src="/static/api.js"></script>' in html
    assert "post('/api/account/email/start'" in html
    assert "post('/api/account/email/verify'" in html
    # Email codes switched off on the server: straight in, never stuck.
    assert "if (r.status === 503) { into(); return; }" in html
    # They are already signed in, so there is always a way past.
    assert re.search(r'<a class="jn-quiet" id="jn-later" href="/">Not now</a>', html)
    # One apricot button on each step.
    for step in ("jn-email", "jn-code"):
        start = html.index(f'id="{step}"')
        block = html[start:html.index('<div class="jn-state"', start + 1)]
        assert block.count("btn-primary") == 1, step


def test_onboarding_has_a_way_out_for_someone_who_is_not_who_it_thinks():
    html = (REPO / "static" / "onboarding.html").read_text(encoding="utf-8")
    for step in ("step-intro-hello", "step-your-name"):
        start = html.index(f'<div id="{step}"')
        block = html[start:html.index('\n<div id="step-', start + 1)]
        assert re.search(r'<a class="not-you[^"]*" href="/logout">Not you\? Sign out</a>', block), step
    rule = re.search(r"\.not-you\s*\{([^}]*)\}", html)
    assert rule and "min-height: 44px" in rule.group(1)


def test_sign_out_from_onboarding_lands_on_the_sign_in_screen(outbox):
    stranger = TestClient(app)
    assert _email_sign_in(stranger, "nobody-yet@example.com", outbox)["next"] == "/onboarding"
    res = stranger.get("/logout", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].startswith("/login")
    assert stranger.get("/api/whoami").status_code == 401
