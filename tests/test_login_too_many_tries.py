"""
The passphrase form's ninth try is the sign-in page, not a JSON blob
(defect hunt, 2026-10-21).

POST /login is a plain form post, so whatever comes back IS the page. Once
the "login" bucket was full (8 in five minutes), _enforce_rate_limit's 429
came back as {"detail": "That's a lot of requests at once — ..."}: raw JSON
in the browser, no form, and in the iPhone app's web view no way back.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import ratelimit, security
from app.main import LOGIN_TOO_MANY_LINE, app

PASSWORD = "test-password"  # conftest's HOME_MANAGER_PASSWORD


def _post(client, password, next="/grocery"):
    return client.post("/login", data={"password": password, "next": next}, follow_redirects=False)


def _fill_the_bucket(client):
    allowed = ratelimit.LIMITS["login"][0][0]
    for _ in range(allowed):
        assert _post(client, "not-it").status_code == 401


def test_the_try_after_the_limit_is_the_sign_in_page_with_a_line():
    client = TestClient(app)
    _fill_the_bucket(client)
    res = _post(client, "not-it")
    assert res.status_code == 429
    assert res.headers["content-type"].startswith("text/html")
    assert 'id="passphrase-form"' in res.text
    # In the passphrase step's banner, which is what keeps that step showing.
    step = res.text[res.text.index('id="step-passphrase"'):res.text.index('id="passphrase-form"')]
    assert f'<p class="error">{LOGIN_TOO_MANY_LINE}</p>' in step
    # Where they were headed survives the refusal.
    assert '<input type="hidden" name="next" value="/grocery" />' in res.text


def test_the_right_passphrase_over_the_limit_still_signs_nobody_in():
    client = TestClient(app)
    _fill_the_bucket(client)
    res = _post(client, PASSWORD)
    assert res.status_code == 429
    assert "location" not in res.headers
    assert security.COOKIE_NAME not in res.cookies


def test_the_line_is_the_email_steps_own():
    # One sentence for one refusal, whichever way in the person was trying.
    html = TestClient(app).get("/login").text
    assert "That\\u2019s a lot of tries. Wait a few minutes, then try again." in html
    assert LOGIN_TOO_MANY_LINE == "That’s a lot of tries. Wait a few minutes, then try again."
