"""
A "Something not working?" report reaches Emily's inbox.

The table kept the words; nothing told anyone they had arrived (Emily,
2026-09-19: her own test report sat unseen in production until somebody
opened the database). So each report is emailed, and these tests pin the
three things that make that safe to add to a path that is already about
something going wrong:

- With the settings present, one email goes out carrying the report.
- With them absent, nothing is sent and nothing breaks — the app is the
  same app it was.
- A dead mail server costs the person filing the report nothing: still a
  204, still a row in the table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from app import feedback_email, tools
from app.db import get_conn

SETTINGS = {
    "FEEDBACK_EMAIL_TO": "emily@example.com",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_USER": "pomona@example.com",
    "SMTP_PASSWORD": "app-password",
}


def _count():
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM feedback_reports").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def mail_configured(monkeypatch):
    for k, v in SETTINGS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SMTP_FROM", raising=False)


@pytest.fixture
def mail_unconfigured(monkeypatch):
    for k in list(SETTINGS) + ["SMTP_FROM", "SMTP_PORT"]:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def outbox(monkeypatch):
    """Catch what would have gone to the SMTP server."""
    sent = []
    monkeypatch.setattr(feedback_email, "_deliver", lambda msg: sent.append(msg))
    return sent


# ---------- the happy path ----------

def test_a_report_is_emailed_with_what_was_typed(signed_in, mail_configured, outbox):
    typed = "The plan came out empty and I couldn't tell whether it was still thinking."
    trying = "Building next week on Sunday night."
    before = _count()

    res = signed_in.post(
        "/api/feedback",
        json={"what_happened": typed, "trying_to_do": trying, "where": "/week",
              "screen": "Plan", "error_shapes": ["TypeError"]},
        headers={"user-agent": "TestPhone/1.0"},
    )
    assert res.status_code == 204
    assert _count() == before + 1, "the email must not replace the row"

    assert len(outbox) == 1, "exactly one email per report"
    msg = outbox[0]
    body = msg.get_content()
    assert msg["To"] == "emily@example.com"
    assert msg["From"] == "pomona@example.com", "From falls back to the sending account"
    assert "Plan" in msg["Subject"]
    assert typed in body and trying in body
    assert "Plan" in body and "/week" in body
    assert "TypeError" in body
    assert "TestPhone/1.0" in body
    assert "quoted as written" in body, "the prose is labelled as typed text"
    assert "> " + typed in body, "the prose is quoted, not pasted as if it were ours"


def test_the_email_names_the_household(signed_in, mail_configured, outbox):
    signed_in.post("/api/feedback", json={"what_happened": "x", "where": "/"})
    conn = get_conn()
    try:
        name = conn.execute(
            "SELECT name FROM households WHERE id = 1"
        ).fetchone()["name"]
    finally:
        conn.close()
    assert name and f'household "{name}"' in outbox[0].get_content()


def test_smtp_from_overrides_the_sender(monkeypatch, mail_configured):
    monkeypatch.setenv("SMTP_FROM", "reports@example.com")
    msg = feedback_email.build_message({"what_happened": "x", "household_id": 1})
    assert msg["From"] == "reports@example.com"


def test_the_time_is_local_and_readable():
    msg = feedback_email.build_message(
        {"what_happened": "x", "household_id": 1},
        now_utc=datetime(2026, 9, 19, 15, 48, tzinfo=timezone.utc),
    )
    assert "Sat Sep 19, 2026 at 11:48 AM (EDT)" in msg.get_content()


# ---------- nothing configured ----------

def test_nothing_is_sent_when_email_is_not_configured(signed_in, mail_unconfigured, outbox, caplog):
    before = _count()
    with caplog.at_level(logging.INFO, logger="home_manager"):
        assert signed_in.post(
            "/api/feedback", json={"what_happened": "secret words here", "where": "/"}
        ).status_code == 204
    assert _count() == before + 1
    assert outbox == []
    line = next((r for r in caplog.records if "no email sent" in r.getMessage()), None)
    assert line is not None, "one clear log line says why"
    assert "FEEDBACK_EMAIL_TO" in line.getMessage()
    assert "secret words" not in line.getMessage(), "the log never carries the prose"


def test_missing_settings_are_named(mail_unconfigured):
    assert feedback_email.missing_settings() == list(SETTINGS)
    assert not feedback_email.configured()


# ---------- the mail server is down ----------

def test_a_dead_mail_server_is_still_a_204_and_a_row(signed_in, mail_configured, monkeypatch, caplog):
    def _down(msg):
        raise ConnectionRefusedError("nobody home")

    monkeypatch.setattr(feedback_email, "_deliver", _down)
    before = _count()
    with caplog.at_level(logging.WARNING, logger="home_manager"):
        assert signed_in.post(
            "/api/feedback", json={"what_happened": "private text", "where": "/"}
        ).status_code == 204
    assert _count() == before + 1
    line = next((r for r in caplog.records if "emailing it failed" in r.getMessage()), None)
    assert line is not None
    assert "ConnectionRefusedError" in line.getMessage()
    assert "private text" not in line.getMessage()


def test_notify_never_raises(mail_configured, monkeypatch):
    monkeypatch.setattr(feedback_email, "_deliver", lambda msg: (_ for _ in ()).throw(TimeoutError()))
    assert feedback_email.notify({"what_happened": "x", "household_id": 1})["status"] == "failed"


# ---------- an empty report sends nothing ----------

def test_an_empty_report_sends_no_email(signed_in, mail_configured, outbox):
    assert signed_in.post(
        "/api/feedback", json={"what_happened": "   ", "where": "/"}
    ).status_code == 204
    assert outbox == []


# ---------- the words stay out of the agent's reach ----------

def test_the_email_module_is_not_an_agent_tool():
    from app import agent

    names = set(getattr(agent, "TOOL_FUNCTIONS", {}) or {})
    assert not any("email" in n for n in names)
    assert not hasattr(tools, "notify")
