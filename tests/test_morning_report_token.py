"""The morning report reads the live app through the report token.

/api/health-report was built on 2026-09-10 ("let's fix the morning report")
so the overnight routine could see every household without holding a
passphrase for any of them. For most of that day it had no caller: this
script still knew only the passphrase path, and the routine's environment
had nothing set. A server half with no client half is the same as no fix —
the morning check stayed blind after it was "fixed".

These pin the client half, and the two ways it must NOT fail quietly.
"""

import io
import json
import sys
import urllib.error

import pytest

import observability_report as report


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _serve(monkeypatch, payload=None, *, status=None, capture=None):
    """Stand in for the live app. `status` makes it answer an HTTP error."""

    def _urlopen(req, timeout=60):
        if capture is not None:
            capture["url"] = req.full_url
            capture["token"] = req.get_header("X-report-token")
        if status is not None:
            raise urllib.error.HTTPError(req.full_url, status, "nope", {}, io.BytesIO(b""))
        return _Resp(payload)

    monkeypatch.setattr(report.urllib.request, "urlopen", _urlopen)


HOUSEHOLDS = [
    {"household_id": 1, "household": "My Household",
     "errors": {"total": 0, "by_kind": {}, "recent": [], "days": 1},
     "usage": {"looks_inactive": True, "days": 7, "chat_turns": 0, "meals_cooked": 0,
               "plans_generated": 0, "plans_approved": 0, "last_active_at": None},
     "feedback_waiting": 0, "plan_quality": {}},
    {"household_id": 2, "household": "Julia",
     "errors": {"total": 1, "by_kind": {"tool": 1},
                "recent": [{"kind": "tool", "location": "swap_meal", "detail": "", "created_at": "x"}],
                "days": 1},
     "usage": {"looks_inactive": False, "days": 7, "chat_turns": 3, "meals_cooked": 1,
               "plans_generated": 1, "plans_approved": 1, "last_active_at": "2026-09-10"},
     "feedback_waiting": 0, "plan_quality": {}},
]


def test_the_token_is_preferred_and_reads_every_household(monkeypatch):
    """One request, every household — including the tester's, without this
    script ever holding her passphrase. That was the whole point of the route:
    run over the web with passphrases it showed one household; the failure
    that mattered was Julia's staying invisible."""
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    monkeypatch.delenv("HOME_MANAGER_PASSPHRASES", raising=False)
    seen = {}
    _serve(monkeypatch, {"days": 1, "households": HOUSEHOLDS}, capture=seen)

    households, source = report.collect(days=1)

    assert source == "the live app, via the report token"
    assert [h["household"] for h in households] == ["My Household", "Julia"]
    assert seen["url"] == "https://example.invalid/api/health-report?days=1"
    assert seen["token"] == "abc", "the token has to travel in the x-report-token header"


def test_the_token_wins_over_passphrases_when_both_are_set(monkeypatch):
    """A routine that still has the old passphrase pair set alongside the new
    token should use the token — the passphrase path is the one that means an
    unattended job holding credentials that open the app itself."""
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    monkeypatch.setenv("HOME_MANAGER_PASSPHRASES", "should-not-be-used")
    _serve(monkeypatch, {"days": 1, "households": HOUSEHOLDS})

    def _must_not_sign_in(base, phrase):
        raise AssertionError("the passphrase path ran even though a token was set")

    monkeypatch.setattr(report, "_sign_in", _must_not_sign_in)
    _, source = report.collect(days=1)
    assert "token" in source


def test_a_wrong_token_never_falls_back_to_a_stale_local_file(monkeypatch, tmp_path):
    """The lie this script was rewritten to stop, in its newest form.

    The route answers 404 for a wrong token on purpose. If that 404 fell
    through to a local database file — any clone where the app was run once —
    the report would print "Nothing broke" from stale data, exit 0, and the
    real reason would appear nowhere. So a configured-and-refused web source
    is a hard stop: exit 2, the operator's problem named in plain words.
    """
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "wrong")
    _serve(monkeypatch, status=404)

    with pytest.raises(report.NoData) as e:
        report.collect(days=1)
    assert "REPORT_TOKEN" in str(e.value)
    assert "Nothing was read" in str(e.value)

    monkeypatch.setattr(sys, "argv", ["observability_report.py"])
    assert report.main() == 2, "a refused token has to exit 2 — 'could not look', never 'looked and it's fine'"


def test_a_deployment_without_the_route_is_named_not_hidden(monkeypatch):
    """A 404 is also what an older deployment answers, and the message says so
    rather than sending the operator to check a token that is fine."""
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    _serve(monkeypatch, status=404)
    with pytest.raises(report.NoData) as e:
        report.collect(days=1)
    assert "predates the route" in str(e.value)


def test_a_malformed_answer_is_an_error_not_an_empty_report(monkeypatch):
    """An answer with no households list must not print as "nothing to report
    on" — that reads as a healthy app."""
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    _serve(monkeypatch, {"days": 1})
    with pytest.raises(report.NoData):
        report.collect(days=1)


def test_the_passphrase_path_still_works_when_no_token_is_set(monkeypatch):
    """Kept for a deployment that predates the route. Nothing about the old
    path changed; this just proves adding the new one did not break it."""
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.delenv("REPORT_TOKEN", raising=False)
    monkeypatch.setenv("HOME_MANAGER_PASSPHRASES", "one")

    class _Opener:
        def open(self, req, timeout=60):
            url = req.full_url
            if url.endswith("/api/whoami"):
                return _Resp({"household_id": 1, "household_name": "My Household"})
            return _Resp({"errors": HOUSEHOLDS[0]["errors"], "usage": HOUSEHOLDS[0]["usage"],
                          "feedback_waiting": 0})

    monkeypatch.setattr(report, "_sign_in", lambda base, phrase: _Opener())
    households, source = report.collect(days=1)
    assert source == "the live app"
    assert households[0]["household"] == "My Household"
