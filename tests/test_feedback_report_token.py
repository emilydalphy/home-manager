"""
The cross-household "Something not working?" reader (/api/feedback-report).

Emily, 2026-11-02: she filed a report from the app and had nowhere to see
it. The email half was never switched on, and /api/feedback answers only
for the household that is signed in. This route lets whoever holds
FEEDBACK_TOKEN read every household's notes, word for word, without a
passphrase.

It has its own token on purpose. REPORT_TOKEN belongs to the overnight
routine, and /api/health-report carries no feedback prose because the
routine acts on what it reads.
"""
from __future__ import annotations

import pytest

from app import tools


TOKEN = "test-feedback-token-not-a-real-one"


@pytest.fixture
def with_token(monkeypatch):
    from app import ratelimit

    ratelimit.reset()
    monkeypatch.setenv("FEEDBACK_TOKEN", TOKEN)
    yield TOKEN


def _hdr(token):
    return {"x-feedback-token": token}


# ---------- the gate ----------

def test_without_a_token_configured_the_route_does_not_exist(client, monkeypatch):
    monkeypatch.delenv("FEEDBACK_TOKEN", raising=False)
    assert client.get("/api/feedback-report", headers=_hdr(TOKEN)).status_code == 404


def test_a_wrong_or_missing_token_gets_404(client, with_token):
    assert client.get("/api/feedback-report", headers=_hdr("wrong")).status_code == 404
    assert client.get("/api/feedback-report").status_code == 404


def test_an_empty_token_cannot_match_an_empty_env(client, monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN", "")
    assert client.get("/api/feedback-report", headers=_hdr("")).status_code == 404


def test_a_non_ascii_token_gets_404_not_a_crash(client, with_token):
    """A 500 here would tell a stranger the route exists."""
    res = client.get("/api/feedback-report", headers={"x-feedback-token": "caf\u00e9".encode("latin-1")})
    assert res.status_code == 404


def test_the_report_token_does_not_open_it(client, with_token, monkeypatch):
    """The overnight routine's token must not reach people's words."""
    monkeypatch.setenv("REPORT_TOKEN", "the-routine-token")
    res = client.get("/api/feedback-report", headers={"x-report-token": "the-routine-token"})
    assert res.status_code == 404


def test_wrong_guesses_are_rate_limited(client, with_token):
    """Counted before the token check, so guessing is throttled too."""
    codes = [
        client.get("/api/feedback-report", headers={
            "x-feedback-token": f"guess-{i}",
            "x-forwarded-for": f"203.0.113.{i + 1}, 198.51.100.5",
        }).status_code
        for i in range(14)
    ]
    assert 429 in codes, f"unlimited guesses allowed: {codes}"


# ---------- what it returns ----------

def test_it_returns_every_household_s_reports_word_for_word(client, with_token):
    from app.db import get_conn

    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (7, 'Someone Else')")
    conn.commit()
    conn.close()

    tools.record_feedback_report("the swap button did nothing", trying_to_do="swap Tuesday")
    with tools.use_household(7):
        tools.record_feedback_report("Shop list would not save")

    body = client.get("/api/feedback-report", headers=_hdr(TOKEN)).json()
    by_id = {h["household_id"]: h for h in body["households"]}
    assert [r["what_happened"] for r in by_id[1]["reports"]] == ["the swap button did nothing"]
    assert by_id[1]["reports"][0]["trying_to_do"] == "swap Tuesday"
    assert [r["what_happened"] for r in by_id[7]["reports"]] == ["Shop list would not save"]
    assert by_id[7]["household"] == "Someone Else"


def test_it_needs_no_household_session(client, with_token):
    """`client` here has never signed in."""
    assert client.get("/api/feedback-report", headers=_hdr(TOKEN)).status_code == 200


def test_the_window_is_bounded(client, with_token):
    assert client.get("/api/feedback-report?days=9999", headers=_hdr(TOKEN)).json()["days"] == 30
    assert client.get("/api/feedback-report?days=0", headers=_hdr(TOKEN)).json()["days"] == 1


def test_the_health_report_still_carries_no_feedback_prose(client, with_token, monkeypatch):
    """Adding this route must not move the words into the routine's report."""
    monkeypatch.setenv("REPORT_TOKEN", "the-routine-token")
    tools.record_feedback_report("I was cross about the swap button")
    body = client.get("/api/health-report", headers={"x-report-token": "the-routine-token"}).json()
    assert "cross about the swap" not in repr(body)


# ---------- the script's client half ----------

def test_the_script_prefers_the_feedback_token(monkeypatch):
    import observability_report as rep

    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("FEEDBACK_TOKEN", TOKEN)
    monkeypatch.delenv("HOME_MANAGER_PASSPHRASES", raising=False)
    monkeypatch.delenv("HOME_MANAGER_PASSWORD", raising=False)
    called = {}
    monkeypatch.setattr(rep, "_collect_feedback_over_token", lambda d: called.setdefault("days", d) and [])
    monkeypatch.setattr(rep, "_collect_feedback_over_http", lambda d: pytest.fail("used passphrases"))
    rep.collect_feedback(30)
    assert called["days"] == 30


def test_the_script_reads_the_route_end_to_end(client, with_token, monkeypatch):
    """The client half against the real route, through the test client."""
    import io
    import json
    import observability_report as rep

    tools.record_feedback_report("rice came out crunchy")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=60):
        path = req.full_url.split("example.invalid", 1)[1]
        res = client.get(path, headers=dict(req.header_items()))
        assert res.status_code == 200, res.status_code
        return _Resp(json.dumps(res.json()).encode())

    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setattr(rep.urllib.request, "urlopen", fake_urlopen)
    households = rep._collect_feedback_over_token(30)
    mine = next(h for h in households if h["household_id"] == 1)
    assert mine["reports"][0]["what_happened"] == "rice came out crunchy"


def test_a_refused_token_is_couldnt_look_not_a_crash(monkeypatch):
    import urllib.error
    import observability_report as rep

    def refuse(req, timeout=60):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("FEEDBACK_TOKEN", "wrong")
    monkeypatch.setattr(rep.urllib.request, "urlopen", refuse)
    with pytest.raises(rep.NoData, match="FEEDBACK_TOKEN"):
        rep._collect_feedback_over_token(30)
