"""
Four ways the morning report said "Nothing broke" (or "something broke")
when that was not what it knew — found 2026-10-25 by breaking things on
purpose against a real server and reading the report.

1. A streamed request that failed (chat, a draft week, a first plan) was
   logged and never recorded: the stream had already answered 200.
2. A background loop's failure (backup, morning text, evening nudge) was a
   log line only.
3. An error repeating on a row filed more than --days ago was outside the
   window, because the window read created_at and the dedupe moves only
   last_seen_at.
4. A reply the report could not read — not JSON, no households, a household
   with no error count — exited 0 (empty list) or 1 (a traceback); it is 2,
   "couldn't look".
"""
from __future__ import annotations

import io
import json
import sys

import pytest

import observability_report as report
from app import agent, ai_consent, tools
from app import main as main_module
from app.db import get_conn


def _rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT household_id, kind, where_, detail FROM error_events ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()


def _drain(client, path, body):
    with client.stream("POST", path, json=body) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


# ---------- 1. streamed failures ----------

def test_a_crashed_chat_stream_is_in_the_morning_report(signed_in, monkeypatch):
    def crash(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "run_agent_turn", crash)
    assert "event: error" in _drain(signed_in, "/api/chat/stream", {"message": "hi"})
    assert {"household_id": 1, "kind": "server", "where_": "/api/chat/stream",
            "detail": "HTTP 500"} in _rows()
    assert tools.get_recent_errors(days=1)["total"] == 1


def test_claude_down_on_a_stream_counts_as_the_plain_route_s_503_does(signed_in, monkeypatch):
    def down(*a, **k):
        raise agent.AssistantUnavailableError("down")

    monkeypatch.setattr(main_module, "run_agent_turn", down)
    _drain(signed_in, "/api/chat/stream", {"message": "hi"})
    assert [r["detail"] for r in _rows() if r["where_"] == "/api/chat/stream"] == ["HTTP 503"]


def test_a_stream_refusal_is_not_breakage(signed_in, monkeypatch):
    """A 400 (a refusal) and the AI-consent 503 are answers, not breakage."""
    def refuse(week_start, **k):
        raise ValueError("no meals came back")

    monkeypatch.setattr(main_module, "generate_weekly_plan", refuse)
    _drain(signed_in, "/api/week/2026-09-07/generate/stream", {})

    def no_consent(*a, **k):
        raise agent.AIConsentRequiredError(ai_consent.REFUSAL_LINE)

    monkeypatch.setattr(main_module, "run_agent_turn", no_consent)
    _drain(signed_in, "/api/chat/stream", {"message": "hi"})
    assert _rows() == []


def test_a_crashed_first_plan_stream_is_filed_against_the_household_that_hit_it(client, monkeypatch):
    from app import households

    hid = households.create_household("Stream Isolation", "stream-isolation-pass")
    res = client.post("/login", data={"password": "stream-isolation-pass", "next": "/"},
                      follow_redirects=False)
    assert res.status_code == 303

    def crash(week_start, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "generate_weekly_plan", crash)
    _drain(client, "/api/onboarding/generate-first-plan/stream", {})
    assert _rows() == [{"household_id": hid, "kind": "server",
                        "where_": "/api/onboarding/generate-first-plan/stream",
                        "detail": "HTTP 500"}]


# ---------- 2. background loops ----------

def test_a_failed_backup_is_in_household_one_s_report(monkeypatch):
    def broken():
        raise OSError("disk full")

    monkeypatch.setattr(main_module.backup, "run_daily_maintenance", broken)
    main_module._run_backup_pass()  # never raises
    assert _rows() == [{"household_id": 1, "kind": "background",
                        "where_": "daily_backup", "detail": "OSError"}]


def test_a_morning_text_failure_is_filed_against_its_household(monkeypatch):
    from app import households
    from app.tools import digest

    hid = households.create_household("Texts Isolation", "texts-isolation-pass")

    def broken(now_utc, sender):
        if tools.household_id() == hid:
            raise KeyError("x")
        return []

    monkeypatch.setattr(digest, "_run_household", broken)
    digest.run_morning_texts_once(send=lambda to, body: {})
    assert _rows() == [{"household_id": hid, "kind": "background",
                        "where_": "morning_text", "detail": "KeyError"}]


# ---------- 3. the window reads when it was last seen ----------

def _insert(created_hours_ago, last_seen_hours_ago, where):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO error_events (household_id, kind, where_, detail, occurrences, "
            "created_at, last_seen_at) VALUES (1, 'server', ?, 'HTTP 500', 3, "
            "datetime('now', ?), datetime('now', ?))",
            (where, f"-{created_hours_ago} hours", f"-{last_seen_hours_ago} hours"),
        )
        conn.commit()
    finally:
        conn.close()


def test_an_error_still_happening_last_night_is_inside_a_one_day_window():
    _insert(25, 1, "/api/still-happening")   # filed 25h ago, hit again an hour ago
    _insert(30, 26, "/api/gone")             # last seen before the window
    errors = tools.get_recent_errors(days=1)
    assert errors["total"] == 3
    assert [r["location"] for r in errors["recent"]] == ["/api/still-happening"]


def test_a_row_from_before_last_seen_at_still_windows_on_created_at():
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO error_events (household_id, kind, where_, detail, created_at, last_seen_at) "
            "VALUES (1, 'server', '/api/legacy', 'HTTP 500', datetime('now', '-2 hours'), '')"
        )
        conn.commit()
    finally:
        conn.close()
    assert tools.get_recent_errors(days=1)["total"] == 1


# ---------- 4. a reply the report cannot read is exit 2 ----------

class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _run_main(monkeypatch, body: bytes) -> int:
    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    monkeypatch.setattr(report.urllib.request, "urlopen", lambda req, timeout=60: _Resp(body))
    monkeypatch.setattr(sys, "argv", ["observability_report.py"])
    return report.main()


@pytest.mark.parametrize("body", [
    b"<html>Sign in</html>",
    b"[]",
    b'{"households": []}',
    b'{"households": [1]}',
], ids=["html", "list", "no-households", "not-a-record"])
def test_a_reply_it_cannot_read_is_couldnt_look_never_nothing_broke(monkeypatch, capsys, body):
    assert _run_main(monkeypatch, body) == 2


def test_a_cut_off_body_is_couldnt_look(monkeypatch, capsys):
    import http.client

    class _Cut(_Resp):
        def read(self):
            raise http.client.IncompleteRead(b"{")

    monkeypatch.setenv("HOME_MANAGER_URL", "https://example.invalid")
    monkeypatch.setenv("REPORT_TOKEN", "abc")
    monkeypatch.setattr(report.urllib.request, "urlopen", lambda req, timeout=60: _Cut(b""))
    monkeypatch.setattr(sys, "argv", ["observability_report.py"])
    assert report.main() == 2


@pytest.mark.parametrize("bad", [
    {"by_kind": {}},
    {"total": "3", "by_kind": {}},
    None,
], ids=["no-total", "string-total", "no-errors"])
def test_one_unreadable_household_never_hides_another_s_breakage(monkeypatch, capsys, bad):
    """Review repro: household 1 had 5 real errors, household 2's record was
    unreadable, and the whole run was exit 2 with nothing printed."""
    body = json.dumps({"households": [
        {"household_id": 1, "household": "Ours",
         "errors": {"total": 5, "by_kind": {"server": 5}, "recent": []}, "usage": {}},
        {"household_id": 2, "household": "Julia", "errors": bad, "usage": {}},
    ]}).encode()
    assert _run_main(monkeypatch, body) == 1
    out = capsys.readouterr().out
    assert "BROKEN — 5" in out
    assert "=== Julia (household 2) ===" in out
    assert "UNREACHABLE" in out.split("=== Julia")[1]


def test_an_unreadable_household_alone_is_still_said_out_loud(monkeypatch, capsys):
    body = b'{"households": [{"household_id": 1, "household": "a"}]}'
    assert _run_main(monkeypatch, body) == 1
    assert "UNREACHABLE" in capsys.readouterr().out


def test_a_missing_usage_block_is_its_own_note_not_an_unreadable_count(monkeypatch, capsys):
    body = json.dumps({"households": [
        {"household_id": 1, "household": "Ours",
         "errors": {"total": 0, "by_kind": {}, "recent": []}},
        {"household_id": 2, "household": "Julia",
         "errors": {"total": 2, "by_kind": {"server": 2}, "recent": []}},
    ]}).encode()
    assert _run_main(monkeypatch, body) == 1
    out = capsys.readouterr().out
    assert "UNREACHABLE" not in out
    assert out.count("Usage — not in this answer") == 2
    assert "Nothing broke." in out and "BROKEN — 2" in out


def test_breakage_it_could_read_still_exits_1_when_the_rest_will_not_print(monkeypatch, capsys):
    # usage {}: present, but missing everything the usage lines read.
    body2 = json.dumps({"households": [
        {"household_id": 1, "household": "a",
         "errors": {"total": 2, "by_kind": {"server": 2}, "recent": []}, "usage": {}},
        {"household_id": 2, "household": "b",
         "errors": {"total": 0, "by_kind": {}, "recent": []}, "usage": {}},
    ]}).encode()
    assert _run_main(monkeypatch, body2) == 1
    captured = capsys.readouterr()
    assert "BROKEN — 2" in captured.out
    assert "could not be printed" in captured.out and "could not be printed" in captured.err
    # The household after the short one still prints.
    assert "=== b (household 2) ===" in captured.out
