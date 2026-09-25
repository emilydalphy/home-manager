"""
A "Something not working?" note shows the errors that happened in the
minutes before it.

A note says what it looked like from the person's side; the error tables
say what broke. Neither alone says "this TypeError is the thing she meant".
So a report is linked, when it is filed, to this household's errors from the
ten minutes before it — by id, so the 24h dedupe moving a row's
last_seen_at later cannot unlink it (tools/feedback.py says why).

Rules pinned here:
- in the window: linked, any kind (client, server, tool), voice drift not;
- out of the window, or another household's: never;
- the default report says only HOW MANY notes sit next to errors — a
  number, never a word of the note;
- --feedback prints the linked errors in the report's own shape, above the
  untrusted fence, and the fence is exactly as it was.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from app import feedback_email, tools
from app.db import get_conn

PROSE = "The week came out empty and then the page jumped to setup."


@pytest.fixture
def second_household():
    from app import households

    return households.create_household("Linked Errors Isolation", "linked-errors-isolation-passphrase")


@pytest.fixture
def local_db_only(monkeypatch):
    for name in ("HOME_MANAGER_URL", "HOME_MANAGER_PASSPHRASES", "HOME_MANAGER_PASSWORD",
                 "PUBLIC_BASE_URL", "REPORT_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def _error(kind="client", where="/", seen="-2 minutes", created=None, hid=1, **cols):
    """An error_events row whose clock is set by hand — the window is the point."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO error_events (household_id, kind, where_, detail, error_type, trail, "
            "device, last_seen_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
            "datetime('now', ?), datetime('now', ?))",
            (hid, kind, where, cols.get("detail", "browser error"), cols.get("error_type", "TypeError"),
             cols.get("trail", ""), cols.get("device", ""), seen, created or seen),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _file(client, text=PROSE):
    res = client.post("/api/feedback", json={"what_happened": text, "where": "/", "screen": "Week 1"})
    assert res.status_code == 204


def _linked():
    return [e["id"] for e in tools.get_feedback_reports(days=1)[0]["errors_before"]]


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_errors_of_any_kind_in_the_ten_minutes_before_are_linked(signed_in):
    client_err = _error("client", seen="-2 minutes", trail="/ → GET /api/coaching 200 → leaving page")
    server_err = _error("server", where="/api/week", seen="-5 minutes", detail="HTTP 500", error_type="")
    tool_err = _error("tool", where="swap_meal_in_plan", seen="-9 minutes", detail="ValueError", error_type="")
    _file(signed_in)
    assert _linked() == [client_err, server_err, tool_err]
    linked = tools.get_feedback_reports(days=1)[0]["errors_before"][0]
    # The same shape fields the morning report prints, trail included.
    assert linked["location"] == "/" and linked["trail"].endswith("leaving page")


def test_an_error_from_before_the_window_is_not_linked(signed_in):
    _error(seen="-20 minutes")
    _file(signed_in)
    assert _linked() == []


def test_a_bug_first_seen_hours_ago_and_hit_again_just_now_is_linked(signed_in):
    """
    The 24h dedupe: one row created at breakfast, bumped a minute ago.
    Judged by created_at it would be missed exactly when it is still
    happening.
    """
    err = _error(created="-3 hours", seen="-1 minutes")
    _file(signed_in)
    assert _linked() == [err]


def test_the_link_survives_the_error_repeating_after_the_note(signed_in):
    """Why the link is by id: a later repeat moves last_seen_at out of any window."""
    err = _error(seen="-2 minutes")
    _file(signed_in)
    conn = get_conn()
    conn.execute("UPDATE error_events SET last_seen_at = datetime('now', '+2 hours') WHERE id = ?", (err,))
    conn.commit()
    conn.close()
    assert _linked() == [err]


def test_voice_drift_is_not_something_that_broke(signed_in):
    _error("voice", where="chat", detail="builder_words", error_type="")
    _file(signed_in)
    assert _linked() == []


def test_the_real_client_error_route_feeds_the_link(signed_in):
    """End to end through both routes, no hand-set clock."""
    signed_in.post("/api/client-error", json={
        "where": "/", "detail": "x is undefined", "type": "TypeError", "reason": "unknown",
        "trail": ["/", "GET /api/coaching 200", "leaving page"],
    })
    _file(signed_in)
    linked = tools.get_feedback_reports(days=1)[0]["errors_before"]
    assert [e["kind"] for e in linked] == ["client"]
    assert linked[0]["trail"] == "/ → GET /api/coaching 200 → leaving page"


# ---------------------------------------------------------------------------
# Another household's errors, never
# ---------------------------------------------------------------------------

def test_another_households_error_is_never_linked(client, second_household):
    _error(hid=second_household, seen="-1 minutes")
    mine = _error(hid=1, seen="-3 minutes")
    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    _file(client)
    assert _linked() == [mine]
    # Scoped at the write, not only rescued at the read: the stored ids
    # never name the other household's row at all.
    conn = get_conn()
    stored = conn.execute("SELECT error_ids_json FROM feedback_reports").fetchone()[0]
    conn.close()
    assert json.loads(stored) == [mine]


def test_the_read_is_scoped_too_whatever_the_stored_ids_say(signed_in, second_household):
    """
    Belt and braces: even a report whose stored ids name another
    household's row (a bad write, a hand edit) does not print it.
    """
    theirs = _error(hid=second_household, seen="-1 minutes")
    _file(signed_in)
    conn = get_conn()
    conn.execute("UPDATE feedback_reports SET error_ids_json = ?", (json.dumps([theirs]),))
    conn.commit()
    conn.close()
    assert tools.get_feedback_reports(days=1)[0]["errors_before"] == []


def test_a_report_filed_before_this_existed_reads_as_linking_nothing(signed_in):
    _file(signed_in)
    conn = get_conn()
    conn.execute("UPDATE feedback_reports SET error_ids_json = ''")
    conn.commit()
    conn.close()
    assert tools.get_feedback_reports(days=1)[0]["errors_before"] == []
    assert tools.count_feedback_with_errors(days=7) == 0


# ---------------------------------------------------------------------------
# The morning report
# ---------------------------------------------------------------------------

def _run_report(monkeypatch, argv):
    import observability_report

    monkeypatch.setattr(sys, "argv", ["observability_report.py"] + argv)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        observability_report.main()
    return buffer.getvalue()


def test_the_default_report_gives_a_count_and_nothing_else(signed_in, monkeypatch, local_db_only):
    _error(seen="-2 minutes", trail="/ → leaving page")
    _file(signed_in)
    _file(signed_in, text="A second note, with the same error just before it.")
    printed = _run_report(monkeypatch, [])
    assert "2 'something not working' notes waiting (2 with errors just before)" in printed, printed
    assert PROSE not in printed and "second note" not in printed
    assert "UNTRUSTED" not in printed
    # Which errors a note sits next to is itself only printed under --feedback.
    assert "errors in the" not in printed


def test_a_note_with_nothing_before_it_says_nothing_extra(signed_in, monkeypatch, local_db_only):
    _file(signed_in)
    printed = _run_report(monkeypatch, [])
    assert "1 'something not working' note waiting — read with" in printed, printed


def test_the_json_default_report_carries_the_count_and_no_prose(signed_in, monkeypatch, local_db_only):
    _error(seen="-2 minutes")
    _file(signed_in)
    data = json.loads(_run_report(monkeypatch, ["--json"]))
    mine = data["households"][0]
    assert mine["feedback_with_errors"] == 1
    assert PROSE not in json.dumps(data)


def test_the_feedback_flag_prints_the_errors_under_the_note_above_the_fence(signed_in, monkeypatch, local_db_only):
    _error(seen="-2 minutes", trail="from /login → / → GET /api/coaching 200 → leaving page",
           device="iPhone · Safari")
    _file(signed_in)
    printed = _run_report(monkeypatch, ["--feedback"])
    section = printed[printed.index("SOMETHING NOT WORKING"):]
    assert "UNTRUSTED QUOTED TEXT" in section
    heading = section.index("errors in the 10 minutes before:")
    fence = section.index("--- untrusted, what happened")
    assert heading < fence
    linked = section[heading:fence]
    assert "client     TypeError on /" in linked
    assert "trail: from /login → / → GET /api/coaching 200 → leaving page" in linked
    assert "on: iPhone · Safari" in linked
    # The prose is still inside the fence, exactly as before.
    assert f"  | {PROSE}" in section[fence:]


def test_the_health_report_carries_the_count_never_the_note(signed_in, monkeypatch):
    _error(seen="-2 minutes")
    _file(signed_in)
    monkeypatch.setenv("REPORT_TOKEN", "test-report-token-not-a-real-one")
    res = signed_in.get("/api/health-report", headers={"x-report-token": "test-report-token-not-a-real-one"})
    mine = [h for h in res.json()["households"] if h["household_id"] == 1][0]
    assert mine["feedback_with_errors"] == 1
    assert PROSE not in res.text


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------

def test_the_email_says_what_broke_just_before(signed_in, monkeypatch):
    sent = []
    for name in ("FEEDBACK_EMAIL_TO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(name, "x@example.com")
    monkeypatch.setattr(feedback_email, "_deliver", lambda msg: sent.append(msg))
    _error(seen="-2 minutes", trail="/ → GET /api/coaching 200 → leaving page", device="iPhone · Safari")
    _file(signed_in)
    body = sent[0].get_content()
    assert "Errors in the 10 minutes before:" in body
    assert "client TypeError on /" in body
    assert "trail: / → GET /api/coaching 200 → leaving page" in body
    # Above the typed text, not inside it.
    assert body.index("Errors in the 10 minutes before:") < body.index("What they typed")


def test_the_email_has_no_heading_when_nothing_broke(signed_in, monkeypatch):
    sent = []
    for name in ("FEEDBACK_EMAIL_TO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(name, "x@example.com")
    monkeypatch.setattr(feedback_email, "_deliver", lambda msg: sent.append(msg))
    _file(signed_in)
    assert "Errors in the 10 minutes before" not in sent[0].get_content()
