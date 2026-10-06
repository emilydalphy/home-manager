"""
Sign in with your email and a 6-digit code (Loop Board "App Store: anyone
can sign up — email + 6-digit code creates a new household", 2026-10-06).
app/account_email.py, the /api/auth/email/* and /api/account/email/* routes.

The card's test list: sign-up creates a household and member; five wrong
tries lock a code; an expired code fails; the rate limits hold; an unknown
email gets the same answer as a known one; the passphrase still works.
Plus the two things most likely to break: codes never stored or logged in
production, and a removed adult's email no longer opening the household.
"""
from __future__ import annotations

import logging
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import account_email, households, legal, security
from app.db import get_conn
from app.main import app


@pytest.fixture
def outbox(monkeypatch):
    """SMTP 'configured', with delivery captured instead of sent."""
    for k, v in {"SMTP_HOST": "smtp.test", "SMTP_USER": "pomona@test", "SMTP_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    sent: list = []
    monkeypatch.setattr(account_email, "_deliver", lambda msg: sent.append(msg))
    return sent


def _code_in(msg) -> str:
    return re.search(r"\b(\d{6})\b", msg.get_content()).group(1)


def _start(client, email):
    return client.post("/api/auth/email/start", json={"email": email})


def _verify(client, email, code, agreed=True, next="/"):
    return client.post("/api/auth/email/verify", json={"email": email, "code": code, "agreed": agreed, "next": next})


def _onboard(client, name="Rowan"):
    res = client.post(
        "/api/onboarding/household",
        json={"members": [{"name": name, "age_group": "Adult"}], "pets": [], "goals": "", "primary_name": name},
    )
    assert res.status_code == 200, res.text


def _sign_up(email="new@example.com", name="Rowan", outbox=None):
    client = TestClient(app)
    assert _start(client, email).status_code == 200
    res = _verify(client, email, _code_in(outbox[-1]))
    assert res.status_code == 200, res.text
    _onboard(client, name)
    return client, res.json()


def test_a_new_email_makes_a_household_and_its_main_person(outbox):
    client = TestClient(app)
    assert _start(client, "New@Example.com ").json()["sent"] is True
    msg = outbox[-1]
    assert msg["Subject"] == "Your Pomona code"
    assert msg["To"] == "new@example.com"
    assert "It works for 10 minutes." in msg.get_content()

    res = _verify(client, "new@example.com", _code_in(msg))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["new_household"] is True and body["next"] == "/onboarding"
    household = security.read_session_household(client.cookies.get(security.COOKIE_NAME))
    assert household not in (None, 1)
    # No passphrase was made for it; the terms version was recorded.
    conn = get_conn()
    assert conn.execute("SELECT 1 FROM household_credentials WHERE household_id = ?", (household,)).fetchone() is None
    assert account_email.accepted_legal_version(household) == legal.LEGAL_VERSION
    conn.close()

    _onboard(client, "Rowan")
    who = client.get("/api/whoami").json()
    assert who["household_id"] == household and who["member"]["name"] == "Rowan"
    assert who["legal_accepted_version"] == legal.LEGAL_VERSION
    assert client.get("/api/account/email").json()["email"] == "new@example.com"

    # A new device: the same address signs the same person back in.
    phone = TestClient(app)
    _start(phone, "new@example.com")
    res = _verify(phone, "new@example.com", _code_in(outbox[-1]), next="/grocery")
    assert res.json() == {"signed_in": True, "new_household": False, "next": "/grocery"}
    assert phone.get("/api/whoami").json()["member"]["name"] == "Rowan"


def test_an_unknown_email_gets_the_same_answer_as_a_known_one(outbox):
    _sign_up("known@example.com", outbox=outbox)
    a = _start(TestClient(app), "known@example.com")
    b = _start(TestClient(app), "nobody@example.com")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json() == {"sent": True, "detail": account_email.SENT_LINE}


def test_five_wrong_tries_kill_the_code(outbox):
    client = TestClient(app)
    _start(client, "a@example.com")
    code = _code_in(outbox[-1])
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(account_email.MAX_ATTEMPTS):
        assert _verify(client, "a@example.com", wrong).status_code == 400
    # The right code, sixth try: refused like a wrong one.
    res = _verify(client, "a@example.com", code)
    assert res.status_code == 400 and res.json()["detail"] == account_email.BAD_CODE_LINE
    assert security.COOKIE_NAME not in client.cookies


def test_a_code_works_once_and_a_new_one_retires_the_old(outbox):
    client = TestClient(app)
    _start(client, "b@example.com")
    first = _code_in(outbox[-1])
    _start(client, "b@example.com")
    second = _code_in(outbox[-1])
    if first != second:
        assert _verify(client, "b@example.com", first).status_code == 400
    assert _verify(client, "b@example.com", second).status_code == 200
    assert _verify(TestClient(app), "b@example.com", second).status_code == 400


def test_an_expired_code_fails(outbox, monkeypatch):
    client = TestClient(app)
    _start(client, "c@example.com")
    code = _code_in(outbox[-1])
    real = time.time
    monkeypatch.setattr(account_email.time, "time", lambda: real() + account_email.CODE_TTL_SECONDS + 1)
    assert _verify(client, "c@example.com", code).status_code == 400


def test_the_code_is_stored_hashed_and_never_logged_in_production(outbox, caplog):
    caplog.set_level(logging.DEBUG)
    _start(TestClient(app), "d@example.com")
    code = _code_in(outbox[-1])
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM email_codes").fetchall()]
    conn.close()
    assert rows and all(code not in str(r) and "d@example.com" not in str(r) for r in rows)
    assert code not in caplog.text and "d@example.com" not in caplog.text


def test_without_smtp_a_deployed_server_says_it_isnt_on_and_sends_nothing(monkeypatch, caplog):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    caplog.set_level(logging.DEBUG)
    # The tests run with HOME_MANAGER_PASSWORD set: that IS production.
    assert account_email.delivery_mode() == "off"
    res = _start(TestClient(app), "e@example.com")
    assert res.status_code == 503 and res.json()["detail"] == account_email.OFF_LINE
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) FROM email_codes").fetchone()[0] == 0
    conn.close()
    # Railway's own variable is enough on its own, password or not.
    monkeypatch.delenv("HOME_MANAGER_PASSWORD")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert account_email.delivery_mode() == "off"
    # Only a laptop with neither prints the code to the log.
    monkeypatch.delenv("RAILWAY_ENVIRONMENT")
    assert account_email.delivery_mode() == "log"


def test_the_dev_log_is_refused_for_a_request_from_elsewhere(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "HOME_MANAGER_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    remote = TestClient(app, client=("203.0.113.7", 4000))
    assert _start(remote, "f@example.com").status_code == 503


def test_codes_per_address_are_capped_without_saying_so(outbox):
    for i in range(7):
        res = _start(TestClient(app, client=(f"198.51.100.{i}", 1)), "g@example.com")
        assert res.status_code == 200 and res.json()["sent"] is True
    assert len(outbox) == 5


def test_codes_per_ip_are_capped(outbox):
    client = TestClient(app, client=("198.51.100.200", 1))
    for i in range(20):
        assert _start(client, f"h{i}@example.com").status_code == 200
    assert _start(client, "h20@example.com").status_code == 429


def test_agreeing_to_the_terms_is_required_for_everyone(outbox):
    client = TestClient(app)
    _start(client, "i@example.com")
    code = _code_in(outbox[-1])
    res = _verify(client, "i@example.com", code, agreed=False)
    assert res.status_code == 400
    # ...and asking did not spend the code.
    assert _verify(client, "i@example.com", code).status_code == 200


def test_the_passphrase_still_signs_in(signed_in, outbox):
    assert signed_in.get("/api/whoami").json()["household_id"] == 1


def test_a_removed_adult_s_email_no_longer_opens_the_household(outbox):
    client, _ = _sign_up("j@example.com", "Jo", outbox=outbox)
    household = client.get("/api/whoami").json()["household_id"]
    # A second adult, so Jo can leave.
    conn = get_conn()
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (?, 'Sam', 'adult')", (household,))
    conn.commit()
    conn.close()
    assert client.get("/api/whoami").json()["picked_on_this_device"] is True
    r = client.post("/api/household/remove-me")
    assert r.status_code == 200
    assert account_email.lookup("j@example.com") is None
    # The address is free again: signing in with it starts afresh.
    other = TestClient(app)
    _start(other, "j@example.com")
    res = _verify(other, "j@example.com", _code_in(outbox[-1]))
    assert res.json()["new_household"] is True
    assert security.read_session_household(other.cookies.get(security.COOKIE_NAME)) != household


def test_an_adult_adds_or_changes_their_email_with_a_code_to_the_new_address(signed_in, outbox):
    conn = get_conn()
    mid = conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Emily', 'adult')").lastrowid
    conn.commit()
    conn.close()
    assert signed_in.post("/api/whoami/pick", json={"member_id": mid}).status_code == 200
    assert signed_in.get("/api/account/email").json() == {"email": None, "can_change": True}
    assert signed_in.post("/api/account/email/start", json={"email": "emily@example.com"}).status_code == 200
    assert outbox[-1]["To"] == "emily@example.com"
    code = _code_in(outbox[-1])
    assert signed_in.post("/api/account/email/verify", json={"email": "emily@example.com", "code": code}).json() == {
        "email": "emily@example.com"
    }
    # A sign-in code for the same address is a different thing: the change
    # code cannot be replayed at the sign-in door.
    assert _verify(TestClient(app), "emily@example.com", code).status_code == 400
    # And the address now signs Emily in, to household 1.
    phone = TestClient(app)
    _start(phone, "emily@example.com")
    assert _verify(phone, "emily@example.com", _code_in(outbox[-1])).json()["new_household"] is False
    assert phone.get("/api/whoami").json()["member"]["id"] == mid


def test_someone_else_s_address_gets_no_code_on_change(signed_in, outbox):
    _sign_up("taken@example.com", outbox=outbox)
    sent_before = len(outbox)
    conn = get_conn()
    mid = conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (1, 'Emily', 'adult')").lastrowid
    conn.commit()
    conn.close()
    signed_in.post("/api/whoami/pick", json={"member_id": mid})
    res = signed_in.post("/api/account/email/start", json={"email": "taken@example.com"})
    assert res.json()["detail"] == account_email.SENT_LINE
    assert len(outbox) == sent_before


def test_the_sign_in_routes_are_public_and_the_account_ones_are_not():
    for path in ("/api/auth/email/start", "/api/auth/email/verify"):
        assert security.is_public_path(path)
    assert not security.is_public_path("/api/account/email")
    assert TestClient(app).get("/api/account/email").status_code == 401


def test_households_module_is_untouched_by_email_sign_up(outbox):
    """No passphrase is made, read or reset by signing in with a code."""
    _sign_up("k@example.com", outbox=outbox)
    assert all(not h["has_credential"] for h in households.list_households() if h["id"] != 1)


# ---------- The sign-in screen (slice 2) ----------


def test_the_sign_in_screen_leads_with_email_and_keeps_the_passphrase():
    html = TestClient(app).get("/login").text.replace("&rsquo;", "'")
    assert "Start with your email" in html
    assert "Sign in with a household passphrase" in html
    assert 'By continuing you agree to the <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>' in html
    # The passphrase form is the one it always was.
    assert '<form method="post" action="/login" id="passphrase-form">' in html
    assert 'name="password"' in html and 'name="next" value="/"' in html


def test_a_refused_passphrase_lands_on_the_passphrase_step():
    res = TestClient(app).post("/login", data={"password": "nope", "next": "/"}, follow_redirects=False)
    assert res.status_code == 401
    step = res.text[res.text.index('id="step-passphrase"'):res.text.index('id="passphrase-form"')]
    assert 'class="error"' in step
