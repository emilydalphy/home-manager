"""
"Something not working?" — the household's own words, kept.

The error tables know what broke. They cannot know what it looked like
from the other side of the screen: "the plan came out empty and I couldn't
tell whether it was still thinking" is a sentence no stack trace contains,
and it is the sentence that says which of three problems this was.

So this is deliberately the one place in the app that stores free text
from the browser end, and everything about it is arranged around that:

- The prose is stored VERBATIM. Not categorised, not summarised, not
  keyword-matched. A category picker is the app deciding in advance what
  can go wrong, which is exactly the thing it is bad at.
- Everything around the prose is shape-only, on the old rules — a
  redacted route pattern rather than a URL, JS error class names rather
  than error messages. See the schema comment and app/main.py's
  POST /api/feedback.
- Nothing here is an agent tool, and nothing here is re-exported into
  agent.TOOL_FUNCTIONS. The reader is a person, at a terminal, running
  observability_report.py --feedback. See that flag's own note for why the
  default report does not print a word of it.

Writes are best-effort and swallowed, for the same reason record_error's
are: this is reached from a "something is already wrong" path, and a
failure to record a complaint must not become the second complaint.
"""
from __future__ import annotations

import json
import logging

from ..db import get_conn
from ._shared import household_id

# Long enough for somebody to actually describe what happened — a couple of
# paragraphs — and short enough that the column can't be used as storage.
MAX_WHAT_HAPPENED = 2000
MAX_TRYING_TO_DO = 1000
MAX_USER_AGENT = 200

_KEEP_ROWS = 500
_KEEP_DAYS = 180


def _prune(conn, hid: int) -> None:
    """
    A ceiling, per household, so this table cannot be grown without bound
    by whoever holds a passphrase.

    Far gentler than error_events' prune (1000 rows / 30 days, swept every
    50 writes): these arrive at human speed, a handful a week at most, and
    a report from two months ago is still worth reading — an error from two
    months ago mostly isn't. Runs on every write because the write rate is
    low enough that it costs nothing.
    """
    conn.execute(
        f"DELETE FROM feedback_reports WHERE household_id = ? "
        f"AND created_at < datetime('now', '-{_KEEP_DAYS} days')",
        (hid,),
    )
    conn.execute(
        "DELETE FROM feedback_reports WHERE household_id = ? AND id NOT IN "
        "(SELECT id FROM feedback_reports WHERE household_id = ? ORDER BY id DESC LIMIT ?)",
        (hid, hid, _KEEP_ROWS),
    )


def record_feedback_report(
    what_happened: str,
    trying_to_do: str | None = None,
    route_pattern: str = "",
    app_version: str = "",
    user_agent: str = "",
    error_shapes: list[str] | None = None,
) -> None:
    """
    File one report against the current household. Never raises.

    The caller is responsible for having redacted `route_pattern` and
    shape-checked `error_shapes` — this module does not re-derive those
    rules, so there is exactly one definition of each (app/main.py's
    _safe_client_where / _safe_client_detail, the same two the browser
    error reporter goes through).

    `what_happened` and `trying_to_do` are stored exactly as typed, minus a
    length cap. That is the feature.
    """
    conn = None
    try:
        text = str(what_happened or "").strip()[:MAX_WHAT_HAPPENED]
        if not text:
            return
        trying = str(trying_to_do or "").strip()[:MAX_TRYING_TO_DO] or None
        extra = json.dumps({"error_shapes": list(error_shapes or [])[:5]})
        conn = get_conn()
        # Same reasoning as record_error: give up on a locked database
        # quickly rather than parking a threadpool worker for sqlite3's
        # default five seconds on a path that is already about something
        # going wrong.
        conn.execute("PRAGMA busy_timeout = 500")
        hid = household_id()
        conn.execute(
            """
            INSERT INTO feedback_reports
                (household_id, what_happened, trying_to_do, route_pattern,
                 app_version, user_agent, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hid,
                text,
                trying,
                str(route_pattern or "")[:120],
                str(app_version or "")[:80],
                str(user_agent or "")[:MAX_USER_AGENT],
                extra,
            ),
        )
        _prune(conn, hid)
        conn.commit()
    except Exception:
        logging.getLogger("home_manager").exception("Recording a feedback report failed")
    finally:
        if conn is not None:
            conn.close()


def count_feedback_reports(days: int = 7) -> int:
    """
    How many reports are waiting — a number, never a word of what they say.

    This is the only thing about feedback the default morning report is
    allowed to know, and the reason it can be: a count carries no prose, so
    it can be printed into an agent's context without carrying anything a
    person typed.
    """
    days = max(1, int(days))
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM feedback_reports WHERE household_id = ? "
            f"AND created_at >= datetime('now', '-{days} days')",
            (household_id(),),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def get_feedback_reports(days: int = 30, limit: int = 50) -> list[dict]:
    """
    The reports themselves, newest first, for this household only.

    Household-scoped like every other read in this app — there is
    deliberately no all-households view, and this is not the place to
    invent one.

    Whatever prints this owes the reader a warning that the two prose
    fields are untrusted quoted text. See observability_report.py's
    --feedback.
    """
    days = max(1, int(days))
    limit = max(1, min(int(limit), 200))
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT id, what_happened, trying_to_do, route_pattern, app_version, "
            f"user_agent, extra_json, created_at FROM feedback_reports "
            f"WHERE household_id = ? AND created_at >= datetime('now', '-{days} days') "
            f"ORDER BY id DESC LIMIT ?",
            (household_id(), limit),
        ).fetchall()
    finally:
        conn.close()

    out = []
    for row in rows:
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except (ValueError, TypeError):
            extra = {}
        out.append(
            {
                "id": row["id"],
                "what_happened": row["what_happened"],
                "trying_to_do": row["trying_to_do"],
                "route_pattern": row["route_pattern"],
                "app_version": row["app_version"],
                "user_agent": row["user_agent"],
                "error_shapes": extra.get("error_shapes") or [],
                "created_at": row["created_at"],
            }
        )
    return out
