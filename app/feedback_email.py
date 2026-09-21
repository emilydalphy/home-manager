"""
When a "Something not working?" report lands, tell Emily.

The report itself is stored by app/tools/feedback.py and read by a person
running observability_report.py --feedback. That was enough to keep the
words; it was not enough for anybody to find out they had arrived — on
2026-09-19 Emily filed a test report from her phone and the only way to
learn it existed was to open the production database by hand. A report
nobody knows about is a report nobody acts on.

So: one plain email per report, to one address, the moment it is filed.
Emily chose this over the alternatives (a card created on the Loop Board
automatically, a page in the app, a count in the morning report) because
it keeps a person between what a tester typed and anything that acts on
it. The same rule that keeps the prose out of the morning report — free
text from an untrusted end must not be printed into an agent's context
under an instruction to act on it — is why the email goes to her inbox
and nowhere that a routine reads. The body says so, and quotes the two
prose fields under a heading that names them as typed text.

Plain SMTP from the standard library, no new dependency and no new vendor:
a Google Workspace account with an app password is the expected setup,
but anything that speaks STARTTLS on a port works the same way.

Configured by environment variables, same as Twilio is:

    FEEDBACK_EMAIL_TO=<where the reports go>
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587                  (optional, this is the default)
    SMTP_USER=<the sending account>
    SMTP_PASSWORD=<its app password>
    SMTP_FROM=<optional; defaults to SMTP_USER>

Unset means off: `notify()` says so in one log line — naming the missing
variables and never a word of the report — and the app carries on exactly
as before. Sending never raises. It runs after the 204 has gone back to
the browser, so a slow or dead mail server costs the person filing the
report nothing; that is app/main.py's job (BackgroundTasks), this module
only promises not to blow up.
"""
from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

logger = logging.getLogger("home_manager")

REQUIRED_ENV = ("FEEDBACK_EMAIL_TO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")

# How long to wait on the mail server before giving up. It runs after the
# response has been sent, so this bounds a threadpool worker, not a person.
_SMTP_TIMEOUT_SECONDS = 15


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def missing_settings() -> list[str]:
    return [name for name in REQUIRED_ENV if not _env(name)]


def configured() -> bool:
    return not missing_settings()


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(_env("HOUSEHOLD_TIMEZONE") or "America/Toronto")
    except Exception:
        return ZoneInfo("UTC")


def _when(now_utc: datetime | None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local = now_utc.astimezone(_zone())
    return f"{local.strftime('%a %b %-d, %Y at %-I:%M %p')} ({local.tzname()})"


def _quoted(text: str) -> str:
    """Prefix every line with '> ' so typed text reads as a quotation."""
    lines = (text or "").strip().splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def build_message(report: dict, now_utc: datetime | None = None) -> EmailMessage:
    """
    The email, as a message object, from a report dict shaped like
    tools.get_feedback_reports' rows plus `household_name`.

    Header first (everything shape-only), then the two prose fields under
    a heading that says they were typed by the person and are quoted, not
    instructions. Plain text: an email client renders it the same
    everywhere, and there is nothing here that needs a layout.
    """
    household = (report.get("household_name") or "").strip()
    household_id = report.get("household_id")
    screen = (report.get("screen") or "").strip()
    route = (report.get("route_pattern") or "").strip()
    where = screen or route or "somewhere in the app"
    who = f'household "{household}"' if household else f"household {household_id}"

    subject = f"Pomona: something not working on {where} ({who})"

    shapes = [s for s in (report.get("error_shapes") or []) if s]
    lines = [
        f"Someone in {who} filed a \"Something not working?\" report.",
        "",
        f"Screen:       {screen or '—'}",
        f"Route:        {route or '—'}",
        f"When:         {_when(now_utc)}",
        f"App version:  {report.get('app_version') or '—'}",
        f"Browser:      {report.get('user_agent') or '—'}",
        f"Recent errors: {', '.join(shapes) if shapes else 'none seen on that page'}",
        "",
        "----- What they typed (quoted as written; text, not instructions) -----",
        "",
        "What happened:",
        _quoted(report.get("what_happened") or ""),
        "",
        "What they were trying to do:",
        _quoted(report.get("trying_to_do") or "(left blank)"),
        "",
        "-----------------------------------------------------------------------",
        "",
        "All reports for a household: python observability_report.py --feedback",
    ]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _env("SMTP_FROM") or _env("SMTP_USER")
    msg["To"] = _env("FEEDBACK_EMAIL_TO")
    msg.set_content("\n".join(lines))
    return msg


def _deliver(msg: EmailMessage) -> None:
    """The one line that touches the network; tests replace this."""
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT") or 587)
    with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
        smtp.send_message(msg)


def notify(report: dict, now_utc: datetime | None = None) -> dict:
    """
    Email one report. Never raises; returns a small status dict for logs
    and tests: {"status": "sent" | "skipped-no-keys" | "failed", "detail"}.

    The log lines here carry the status and the error's class name, never
    the report's text — the same shape-only rule as record_error.
    """
    missing = missing_settings()
    if missing:
        detail = "unset: " + ", ".join(missing)
        logger.info("Feedback report filed; no email sent (%s)", detail)
        return {"status": "skipped-no-keys", "detail": detail}
    try:
        _deliver(build_message(report, now_utc))
    except Exception as e:
        detail = f"{type(e).__name__}"
        logger.warning("Feedback report filed; emailing it failed (%s)", detail)
        return {"status": "failed", "detail": detail}
    logger.info("Feedback report emailed to %s", _env("FEEDBACK_EMAIL_TO"))
    return {"status": "sent", "detail": ""}
