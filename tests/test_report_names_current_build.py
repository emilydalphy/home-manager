"""
The morning report says whether a browser error came from the build running now.

Each client error carries the build that was live when it landed
("build 88371c1871a7"), but until 2026-10-07 the report had nothing to
compare it to, so "this is from a build we no longer run" was left to the
reader. /api/health-report now carries the app's own build on the request
the report already makes, and the "on:" line says it plainly.

Never a guess: no current build (a local file, an older deployment, a run
off Railway) prints the build alone, exactly as before.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

import observability_report as report

OLD = "88371c1871a7"
NOW_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"  # 40 chars, as Railway sets it
NOW = NOW_SHA[:12]


def _client_row(build):
    return {"kind": "client", "location": "/", "detail": "browser error",
            "error_type": "TypeError", "source": "shell.js:10", "stack_shape": "",
            "occurrences": 1, "device": "iPhone · Safari", "display_mode": "app",
            "lang": "en", "app_version": build}


def _household(build, current=None):
    h = {"household_id": 1, "household": "My Household",
         "errors": {"total": 1, "by_kind": {"client": 1}, "recent": [_client_row(build)], "days": 1},
         "usage": {"looks_inactive": True, "days": 7, "chat_turns": 0, "meals_cooked": 0,
                   "plans_generated": 0, "plans_approved": 0, "last_active_at": None}}
    if current is not None:
        h["current_build"] = current
    return h


def _on_line(household) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        report._print_human([household], days=1, source="test")
    return next(line.strip() for line in out.getvalue().splitlines() if line.strip().startswith("on: "))


# ---------- the line ----------

def test_an_error_from_an_older_build_says_so_and_names_the_one_running():
    assert _on_line(_household(OLD, NOW)) == (
        f"on: iPhone · Safari · home-screen app · en · build {OLD} — not the build running now ({NOW})"
    )


def test_an_error_from_the_build_running_now_says_that():
    assert _on_line(_household(NOW, NOW)).endswith(f"build {NOW} — the build running now")


def test_an_unknown_current_build_prints_the_build_alone():
    """A local file or an older deployment: no comparison, and no guess."""
    assert _on_line(_household(OLD)).endswith(f"build {OLD}")
    assert _on_line(_household(OLD, "")).endswith(f"build {OLD}")


def test_a_row_with_no_build_never_grows_a_comparison():
    assert "running now" not in _on_line(_household("", NOW))


@pytest.mark.parametrize("a, b, same", [
    (NOW, NOW_SHA, True),            # 12-char stamp vs a full 40-char sha
    (NOW_SHA, NOW, True),
    (NOW.upper(), NOW, True),
    (OLD, NOW_SHA, False),
    ("a1b2c3", "a1b2c3ffff", False),  # under seven characters is not a sha prefix
    ("v41", "v41", True),             # an APP_VERSION label still matches itself
])
def test_builds_compare_on_their_common_length(a, b, same):
    assert report._same_build(a, b) is same


# ---------- the request the report already makes ----------

class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _serve(monkeypatch, payload):
    calls = []

    def _urlopen(req, timeout=60):
        calls.append(req.full_url)
        return _Resp(payload)

    monkeypatch.setattr(report.urllib.request, "urlopen", _urlopen)
    return calls


def test_the_token_path_carries_the_running_build_without_a_second_request(monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    calls = _serve(monkeypatch, {"days": 1, "app_version": NOW, "households": [_household(OLD)]})

    households, _ = report.collect(days=1)

    assert len(calls) == 1
    assert households[0]["current_build"] == NOW
    assert "not the build running now" in _on_line(households[0])


def test_an_older_deployment_without_the_field_prints_as_before(monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    _serve(monkeypatch, {"days": 1, "households": [_household(OLD)]})

    households, _ = report.collect(days=1)

    assert "current_build" not in households[0]
    assert _on_line(households[0]).endswith(f"build {OLD}")


# ---------- the server half ----------

TOKEN = "test-report-token-not-a-real-one"


def test_the_health_report_carries_the_build_it_is_running(client, monkeypatch):
    monkeypatch.setenv("REPORT_TOKEN", TOKEN)
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", NOW_SHA)
    res = client.get("/api/health-report", headers={"x-report-token": TOKEN})
    assert res.status_code == 200
    # The same 12 characters _app_version() stamps on every client error.
    assert res.json()["app_version"] == NOW


def test_off_railway_the_health_report_says_no_build(client, monkeypatch):
    monkeypatch.setenv("REPORT_TOKEN", TOKEN)
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    res = client.get("/api/health-report", headers={"x-report-token": TOKEN})
    assert res.json()["app_version"] == ""
