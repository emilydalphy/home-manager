"""
A report token pasted into the host's variables with a trailing newline.

Defect hunt, 2026-11-03: /api/health-report and /api/feedback-report
compared the raw environment value, while observability_report.py strips
its own copy before sending it (and no HTTP client can send a header value
ending in a newline at all). So a value pasted with a trailing newline or
space matched nothing, ever — the overnight check answered "404, token
refused" every morning with the same token set at both ends.
"""
from __future__ import annotations

import pytest

from app import ratelimit

ROUTES = [
    ("/api/health-report", "REPORT_TOKEN", "x-report-token"),
    ("/api/feedback-report", "FEEDBACK_TOKEN", "x-feedback-token"),
]
TOKEN = "test-report-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _fresh_limits():
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.mark.parametrize("path,env,header", ROUTES)
@pytest.mark.parametrize("pasted", [TOKEN + "\n", TOKEN + " ", "  " + TOKEN + "\r\n"])
def test_a_pasted_token_with_stray_whitespace_still_opens_the_route(client, monkeypatch, path, env, header, pasted):
    monkeypatch.setenv(env, pasted)
    # Exactly what observability_report.py sends: its own copy, stripped.
    assert client.get(path, headers={header: pasted.strip()}).status_code == 200


@pytest.mark.parametrize("path,env,header", ROUTES)
def test_a_token_that_is_only_whitespace_leaves_the_route_off(client, monkeypatch, path, env, header):
    """Stripping must not turn "set to a space" into "anyone may read"."""
    monkeypatch.setenv(env, " \n")
    assert client.get(path, headers={header: "x"}).status_code == 404
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path,env,header", ROUTES)
def test_stripping_does_not_loosen_the_match(client, monkeypatch, path, env, header):
    monkeypatch.setenv(env, TOKEN + "\n")
    assert client.get(path, headers={header: TOKEN[:-1]}).status_code == 404
    assert client.get(path, headers={header: TOKEN + "x"}).status_code == 404
