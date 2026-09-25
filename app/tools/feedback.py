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
MAX_SCREEN = 60

_KEEP_ROWS = 500
_KEEP_DAYS = 180

# "Something not working?" is written AFTER the thing that did not work —
# usually a minute or two, sometimes after a retry or two. Ten minutes is
# the card's number (Loop Board, 2026-09-25): long enough to reach back past
# a person trying again, short enough that an unrelated error from their
# previous visit is not presented as the cause.
LINK_WINDOW_MINUTES = 10
# A ceiling, so a page stuck in an error loop cannot turn one note into a
# list nobody reads. Newest first, so the ones kept are the closest.
MAX_LINKED_ERRORS = 10

# The same shape columns, under the same names, that tools.get_recent_errors
# returns — so the report prints a linked error with exactly the function it
# prints any other error with.
_ERROR_COLUMNS = (
    "id, kind, where_ AS location, detail, error_type, source, stack_shape, reason, "
    "request_shape, trail, device, display_mode, lang, app_version, occurrences, "
    "last_seen_at, created_at"
)


def _errors_just_before(conn, hid: int) -> list[dict]:
    """
    This household's errors seen in the LINK_WINDOW_MINUTES before now.

    "Seen" is last_seen_at, not created_at, because of the 24h dedupe in
    record_error: a TypeError first filed at breakfast and hit again a
    minute before the note is one row created at breakfast. Judged by
    created_at it would be missed exactly when it matters most — the bug
    that is still happening. (last_seen_at is '' only on rows from before
    that column, which were seen once, at created_at.)

    Voice drift is left out: it is a note about a chat reply's wording,
    not something that broke, and the morning report keeps it out of
    BROKEN for the same reason.
    """
    rows = conn.execute(
        f"SELECT {_ERROR_COLUMNS} FROM error_events "
        "WHERE household_id = ? AND kind != 'voice' "
        "AND COALESCE(NULLIF(last_seen_at, ''), created_at) >= datetime('now', ?) "
        "ORDER BY COALESCE(NULLIF(last_seen_at, ''), created_at) DESC, id DESC LIMIT ?",
        (hid, f"-{LINK_WINDOW_MINUTES} minutes", MAX_LINKED_ERRORS),
    ).fetchall()
    return [dict(r) for r in rows]


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
    screen: str = "",
) -> list[dict]:
    """
    File one report against the current household. Never raises.

    The caller is responsible for having redacted `route_pattern` and
    shape-checked `error_shapes` — this module does not re-derive those
    rules, so there is exactly one definition of each (app/main.py's
    _safe_client_where / _safe_client_detail, the same two the browser
    error reporter goes through).

    `what_happened` and `trying_to_do` are stored exactly as typed, minus a
    length cap. That is the feature.

    `screen` is the screen's own name for itself ("Week 1") — set by the
    app's code, never typed, and shape-checked by the caller
    (app/main.py's _safe_feedback_screen) like `route_pattern` is.

    The household's errors from the LINK_WINDOW_MINUTES before it are
    linked to the report BY ID, at the moment it is filed, and returned (so
    the email can say what they were). By id rather than by a query at read
    time because a read-time window drifts: the 24h dedupe moves a row's
    last_seen_at every time the bug repeats, so the same TypeError hit
    again an hour after the note would fall OUT of the note's window when
    the report is read — and the one link that mattered would vanish. The
    ids pin what was true when the person pressed send. A linked row the
    error prune later removes (30 days) simply stops printing. Returns []
    when nothing was filed.
    """
    conn = None
    try:
        text = str(what_happened or "").strip()[:MAX_WHAT_HAPPENED]
        if not text:
            return []
        trying = str(trying_to_do or "").strip()[:MAX_TRYING_TO_DO] or None
        extra = json.dumps({"error_shapes": list(error_shapes or [])[:5]})
        conn = get_conn()
        # Same reasoning as record_error: give up on a locked database
        # quickly rather than parking a threadpool worker for sqlite3's
        # default five seconds on a path that is already about something
        # going wrong.
        conn.execute("PRAGMA busy_timeout = 500")
        hid = household_id()
        linked = _errors_just_before(conn, hid)
        conn.execute(
            """
            INSERT INTO feedback_reports
                (household_id, what_happened, trying_to_do, route_pattern,
                 app_version, user_agent, extra_json, screen, error_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hid,
                text,
                trying,
                str(route_pattern or "")[:120],
                str(app_version or "")[:80],
                str(user_agent or "")[:MAX_USER_AGENT],
                extra,
                str(screen or "")[:MAX_SCREEN],
                json.dumps([row["id"] for row in linked]),
            ),
        )
        _prune(conn, hid)
        conn.commit()
        return linked
    except Exception:
        logging.getLogger("home_manager").exception("Recording a feedback report failed")
        return []
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


def count_feedback_with_errors(days: int = 7) -> int:
    """
    How many of the waiting reports had errors just before them — a
    number, and for the same reason count_feedback_reports is one: the
    default morning report may say THAT a note sits next to a crash, and
    must never say a word of what the note says.
    """
    days = max(1, int(days))
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM feedback_reports WHERE household_id = ? "
            f"AND created_at >= datetime('now', '-{days} days') "
            f"AND error_ids_json NOT IN ('', '[]')",
            (household_id(),),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def _linked_errors(conn, hid: int, rows) -> dict[int, dict]:
    """Every error any of these reports links to, by id — this household's only."""
    wanted: set[int] = set()
    for row in rows:
        try:
            wanted.update(int(i) for i in json.loads(row["error_ids_json"] or "[]"))
        except (ValueError, TypeError):
            continue
    if not wanted:
        return {}
    ids = sorted(wanted)
    marks = ",".join("?" for _ in ids)
    # household_id in the WHERE as well as in how the ids were chosen: the
    # ids came out of this household's own rows, and this line makes that a
    # property of the read rather than a promise about the write.
    found = conn.execute(
        f"SELECT {_ERROR_COLUMNS} FROM error_events WHERE household_id = ? AND id IN ({marks})",
        (hid, *ids),
    ).fetchall()
    return {r["id"]: dict(r) for r in found}


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
        hid = household_id()
        rows = conn.execute(
            f"SELECT id, what_happened, trying_to_do, route_pattern, app_version, "
            f"user_agent, extra_json, screen, error_ids_json, created_at FROM feedback_reports "
            f"WHERE household_id = ? AND created_at >= datetime('now', '-{days} days') "
            f"ORDER BY id DESC LIMIT ?",
            (hid, limit),
        ).fetchall()
        errors_by_id = _linked_errors(conn, hid, rows)
    finally:
        conn.close()

    out = []
    for row in rows:
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except (ValueError, TypeError):
            extra = {}
        try:
            linked_ids = [int(i) for i in json.loads(row["error_ids_json"] or "[]")]
        except (ValueError, TypeError):
            linked_ids = []
        out.append(
            {
                "id": row["id"],
                "what_happened": row["what_happened"],
                "trying_to_do": row["trying_to_do"],
                "route_pattern": row["route_pattern"],
                "app_version": row["app_version"],
                "user_agent": row["user_agent"],
                "error_shapes": extra.get("error_shapes") or [],
                "screen": row["screen"] or "",
                "created_at": row["created_at"],
                # The errors this household hit in the LINK_WINDOW_MINUTES
                # before the report, newest first — shapes only, the same
                # fields the morning report prints. Not untrusted text:
                # every one was re-derived server-side when it was stored.
                "errors_before": [errors_by_id[i] for i in linked_ids if i in errors_by_id],
            }
        )
    return out
