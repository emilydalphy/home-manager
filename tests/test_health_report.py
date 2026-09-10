"""
The cross-household health report (/api/health-report).

The overnight routine runs in a sandbox that cannot reach this machine, so
its morning "did anything break?" check was reading an empty clone and
printing "nothing broke" every single day — which reads as good news and is
actually no news at all. This route is how it gets the real answer.

It authenticates with its own token rather than a household passphrase,
because the alternative was an automation holding credentials that also
open the app itself, for households that are not Emily's.
"""
from __future__ import annotations

import os

import pytest

from app import tools


TOKEN = "test-report-token-not-a-real-one"


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setenv("REPORT_TOKEN", TOKEN)
    yield TOKEN


def _hdr(token):
    return {"x-report-token": token}


# ---------- the gate ----------

def test_without_a_token_configured_the_route_does_not_exist(client, monkeypatch):
    """
    Unset REPORT_TOKEN disables it outright. A deployment that never
    configures this has no cross-household route at all, which is the
    correct default for a route that reads every household.
    """
    monkeypatch.delenv("REPORT_TOKEN", raising=False)
    assert client.get("/api/health-report", headers=_hdr(TOKEN)).status_code == 404


def test_a_wrong_token_gets_404_not_401(client, with_token):
    """
    401 confirms the route exists and is worth grinding at. 404 says nothing.
    """
    res = client.get("/api/health-report", headers=_hdr("wrong"))
    assert res.status_code == 404


def test_no_token_at_all_gets_404(client, with_token):
    assert client.get("/api/health-report").status_code == 404


def test_an_empty_token_header_cannot_match_an_empty_env(client, monkeypatch):
    """
    The failure this guards: comparing "" to "" is True. If REPORT_TOKEN were
    ever set to empty and the check only compared the two, every caller would
    be admitted. The route requires BOTH to be non-empty before comparing.
    """
    monkeypatch.setenv("REPORT_TOKEN", "")
    assert client.get("/api/health-report", headers=_hdr("")).status_code == 404


def test_the_right_token_is_let_through(client, with_token):
    res = client.get("/api/health-report", headers=_hdr(TOKEN))
    assert res.status_code == 200


def test_it_needs_no_household_session(client, with_token):
    """
    The point of the route: it is cross-household, so there is no session to
    bind it to. `client` here has never signed in.
    """
    assert client.get("/api/health-report", headers=_hdr(TOKEN)).status_code == 200


# ---------- what it returns ----------

def test_it_reports_every_household_not_just_the_caller_s(client, with_token):
    """
    The whole reason it exists: the old path signed in as one household and
    could therefore only ever see that one. Julia's household staying
    invisible is the failure mode.
    """
    from app.db import get_conn

    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (7, 'Someone Else')")
    conn.commit()
    conn.close()

    body = client.get("/api/health-report", headers=_hdr(TOKEN)).json()
    ids = [h["household_id"] for h in body["households"]]
    assert 1 in ids and 7 in ids, f"expected both households, got {ids}"


def test_it_carries_errors_and_usage_per_household(client, with_token):
    tools.record_error("client", where="/", detail="browser error")
    body = client.get("/api/health-report", headers=_hdr(TOKEN)).json()
    mine = next(h for h in body["households"] if h["household_id"] == 1)
    assert "errors" in mine and "usage" in mine
    assert "feedback_waiting" in mine


def test_it_never_returns_anything_a_person_typed(client, with_token):
    """
    THE boundary. This output is read into an agent's context under an
    instruction to act on what it says, so free text from an untrusted end is
    an injection channel, not merely a privacy question — the same reasoning
    observability_report.py's own docstring gives for keeping --feedback
    opt-in and fenced.

    A waiting report is a NUMBER here. If someone ever adds the text to
    _collect_from_db, this test is what stops it reaching the routine.
    """
    tools.record_feedback_report("the swap button did nothing and I was cross about it")

    body = client.get("/api/health-report", headers=_hdr(TOKEN)).json()
    mine = next(h for h in body["households"] if h["household_id"] == 1)

    assert isinstance(mine["feedback_waiting"], int), "feedback must be a count"
    assert "cross about it" not in repr(body), "a person's own words reached the report"


def test_the_window_is_bounded(client, with_token):
    """
    An unbounded `days` is a way to ask the box to read its whole history on
    demand. 1..30: a day is the nightly question, a month is as wide as the
    usage summary is meaningful.
    """
    assert client.get("/api/health-report?days=9999", headers=_hdr(TOKEN)).json()["days"] == 30
    assert client.get("/api/health-report?days=0", headers=_hdr(TOKEN)).json()["days"] == 1
    assert client.get("/api/health-report?days=3", headers=_hdr(TOKEN)).json()["days"] == 3


def test_guessing_at_the_token_is_rate_limited(client, with_token):
    """
    A token is only guessable if you may guess without limit. The bucket is
    generous for one caller a night and mean to anyone else.
    """
    from app import ratelimit

    ratelimit.reset()
    codes = [
        client.get("/api/health-report", headers={
            "x-report-token": TOKEN,
            "x-forwarded-for": f"203.0.113.{i + 1}, 198.51.100.5",
        }).status_code
        for i in range(14)
    ]
    assert 429 in codes, f"unlimited attempts allowed: {codes}"
