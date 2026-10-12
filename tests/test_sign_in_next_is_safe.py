"""
The sign-in page's ?next= can neither run a script nor send anyone off-site
(defect hunt, 2026-10-11).

Two holes in the one parameter, both measured in a real browser:

  * /login?next=/"><script>… — the value went into value="…" unescaped, so
    the script ran on the sign-in page itself, where the passphrase and the
    email code are typed.
  * /login?next=/\\example.org/phish — `_safe_next` let it through (it starts
    with one "/"), the email sign-in returned it as `next`, login.html did
    window.location.assign(next), and the browser — which reads a backslash
    as "/" — landed on example.org.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import account_email, security
from app.main import app

SCRIPT = '/"><script>window.__pwned=1</script>'


@pytest.fixture
def outbox(monkeypatch):
    for k, v in {"SMTP_HOST": "smtp.test", "SMTP_USER": "pomona@test", "SMTP_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    sent: list = []
    monkeypatch.setattr(account_email, "_deliver", lambda msg: sent.append(msg))
    return sent


def _next_value(html: str) -> str:
    return re.search(r'<input type="hidden" name="next" value="([^"]*)" />', html).group(1)


def test_the_sign_in_page_escapes_next():
    res = TestClient(app).get("/login", params={"next": SCRIPT}, follow_redirects=False)
    assert res.status_code == 200
    assert "<script>window.__pwned" not in res.text
    assert _next_value(res.text) == "/&quot;&gt;&lt;script&gt;window.__pwned=1&lt;/script&gt;"


def test_a_refused_passphrase_escapes_next_too():
    res = TestClient(app).post(
        "/login", data={"password": "not-it", "next": SCRIPT}, follow_redirects=False
    )
    assert res.status_code == 401
    assert "<script>window.__pwned" not in res.text


@pytest.mark.parametrize("sneaky", [
    "/\\example.org/phish",
    "/\\/example.org",
    "/\t/example.org",
    "/\n/example.org",
    "/\r/example.org",
])
def test_paths_a_browser_reads_as_off_site_are_refused(sneaky):
    assert security.sanitize_next(sneaky) == "/"


@pytest.mark.parametrize("fine", ["/", "/week", "/kitchen?step=cook#tonight", "/onboarding"])
def test_ordinary_paths_are_kept(fine):
    assert security.sanitize_next(fine) == fine


def test_email_sign_in_never_hands_back_an_off_site_next(outbox):
    first = TestClient(app)
    first.post("/api/auth/email/start", json={"email": "rae@example.com"})
    code = re.search(r"\b(\d{6})\b", outbox[-1].get_content()).group(1)
    first.post("/api/auth/email/verify", json={"email": "rae@example.com", "code": code, "agreed": True})
    assert first.post(
        "/api/onboarding/household",
        json={"members": [{"name": "Rae", "age_group": "Adult"}], "pets": [], "goals": "", "primary_name": "Rae"},
    ).status_code == 200

    again = TestClient(app)
    again.post("/api/auth/email/start", json={"email": "rae@example.com"})
    code = re.search(r"\b(\d{6})\b", outbox[-1].get_content()).group(1)
    res = again.post(
        "/api/auth/email/verify",
        json={"email": "rae@example.com", "code": code, "agreed": True, "next": "/\\example.org/phish"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["next"] == "/"
