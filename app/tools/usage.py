"""
Is the household actually using the app, and what is it costing?

Two questions the app could not answer before this module existed. Both
are beta questions first and cost questions second:

- **Usage.** Once someone else is testing this, the only alternative to
  measuring is waiting for them to volunteer that they've stopped, which
  people don't do. Everything here is counted from rows the app already
  writes for its own reasons -- meals cooked, plans generated, preferences
  taught -- plus chat turns, which are the one thing nothing recorded.
  No analytics SDK, no third-party tracker, no per-tap event stream: this
  is a household app holding real personal data, and counts from data it
  already stores are enough to answer the question honestly.

- **Cost.** Judged per *completed job*, not per request -- a cheaper call
  that needs more rounds to finish isn't cheaper. That's why chat_turns
  stores `rounds` next to the tokens.

- **Breakage.** Did anything go wrong, and can anyone find out without
  waiting for the tester to mention it? Errors already reach the logs; what
  they could not do is be read back. The overnight routine that reports
  each morning runs in the cloud with the repo and Notion -- not with the
  running app's stdout -- so a log it cannot open is, for the purpose of
  anybody hearing about it, no record at all. record_error puts a row in
  the database instead, and get_recent_errors reads it out.
"""
from __future__ import annotations

import time

from ..db import get_conn
from ._shared import household_id


_TURN_FIELDS = ("rounds", "input_tokens", "cache_read_tokens",
                "cache_write_tokens", "output_tokens", "seconds")


def record_chat_turn(usage: dict | None = None) -> None:
    """
    Record that a chat turn happened, and what it cost. No message content
    -- see schema.sql on chat_turns for why.

    Takes one dict rather than keyword arguments on purpose. The caller
    hands over whatever agent.run_agent_turn tallied, and anything this
    table doesn't have a column for is ignored here rather than raising at
    the call site -- where it would be outside this function's own error
    handling and would 500 a chat turn that had already succeeded. Adding
    a new measure in agent.py can therefore never break chat; it just
    isn't stored until a column exists for it.

    Never raises, for the same reason: this is bookkeeping attached to a
    reply that already worked, and it must not be what fails. A broken
    write shows up in the logs instead -- agent.py's tool-failure logging
    is the same principle, since silence is the thing being fixed.
    """
    values = usage or {}
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO chat_turns (household_id, rounds, input_tokens, cache_read_tokens, "
            "cache_write_tokens, output_tokens, seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (household_id(), *(values.get(f, 0) for f in _TURN_FIELDS)),
        )
        conn.commit()
    except Exception:
        import logging
        logging.getLogger("home_manager").exception("Recording a chat turn failed")
    finally:
        if conn is not None:
            conn.close()


# How long to go before writing last_active_at again for the same
# household, and the in-process record of when we last did.
#
# Every authenticated request could touch the column, but that would mean
# a write on every page load and every poll, to store a value only ever
# read at day granularity. Fifteen minutes keeps "when were they last
# here" accurate to the hour while making the write rare. The cache being
# per-process (and lost on restart) is fine: the worst case is one extra
# write after a deploy.
_ACTIVE_TOUCH_INTERVAL_SECONDS = 15 * 60
_last_touched: dict[int, float] = {}


def touch_household_active(household: int) -> None:
    """
    Note that this household is currently using the app.

    Takes the household explicitly rather than reading `household_id()`,
    because the one caller is the middleware that *sets* that ContextVar --
    see security._call_as_household. Never raises, for the same reason as
    record_chat_turn: this is bookkeeping wrapped around a real request.
    """
    now = time.time()
    if now - _last_touched.get(household, 0.0) < _ACTIVE_TOUCH_INTERVAL_SECONDS:
        return
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE households SET last_active_at = datetime('now') WHERE id = ?",
            (household,),
        )
        conn.commit()
        # Marked as done only once it actually succeeded. Recording the
        # attempt up front would mean a single transient failure (a locked
        # database) silently costs a full interval of activity tracking,
        # for a household that was in fact active.
        _last_touched[household] = now
    except Exception:
        import logging
        logging.getLogger("home_manager").exception("Touching last_active_at failed")
    finally:
        if conn is not None:
            conn.close()


def get_usage_summary(days: int = 7) -> dict:
    """
    What this household has actually done over the last `days` days, plus
    what its chat has cost -- the numbers behind the morning check-in.

    Scoped to the current household like every other tool. Run it per
    household to compare them; there is deliberately no all-households
    variant here, because that would be the one query in the app that
    reads across the isolation boundary.
    """
    # Clamped, not just cast. A negative value would build
    # datetime('now', '--7 days'), which SQLite returns as NULL rather than
    # erroring -- every comparison against NULL is false, so the answer
    # comes back as a household that did nothing at all. Reporting an
    # active household as dead is the one wrong answer this function must
    # never give.
    days = max(1, int(days))
    since = f"-{days} days"
    conn = get_conn()
    try:
        return _summarize(conn, household_id(), days, since)
    finally:
        conn.close()


def _summarize(conn, hid: int, days: int, since: str) -> dict:
    def _count(sql: str) -> int:
        return conn.execute(sql, (hid,)).fetchone()[0]

    chat = conn.execute(
        "SELECT COUNT(*) AS turns, COALESCE(SUM(rounds), 0) AS rounds, "
        "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
        "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "COALESCE(SUM(seconds), 0) AS seconds "
        f"FROM chat_turns WHERE household_id = ? AND created_at >= datetime('now', '{since}')",
        (hid,),
    ).fetchone()

    household_row = conn.execute(
        "SELECT last_active_at FROM households WHERE id = ?", (hid,)
    ).fetchone()

    summary = {
        "days": int(days),
        # A household row that isn't there is a caller error, not a crash:
        # returning None reads as "never seen", which is the truth.
        "last_active_at": (household_row["last_active_at"] if household_row else None),
        "chat_turns": chat["turns"],
        "chat_rounds": chat["rounds"],
        "chat_seconds": round(chat["seconds"], 1),
        "tokens": {
            "input": chat["input_tokens"],
            "cache_read": chat["cache_read_tokens"],
            "cache_write": chat["cache_write_tokens"],
            "output": chat["output_tokens"],
        },
        "plans_generated": _count(
            "SELECT COUNT(*) FROM weekly_plans WHERE household_id = ? "
            f"AND created_at >= datetime('now', '{since}')"
        ),
        "plans_approved": _count(
            "SELECT COUNT(*) FROM weekly_plans WHERE household_id = ? "
            f"AND approved_at IS NOT NULL AND approved_at >= datetime('now', '{since}')"
        ),
        "meals_cooked": _count(
            "SELECT COUNT(*) FROM meal_plan_entries WHERE household_id = ? "
            f"AND cooked_status = 'done' AND cooked_at >= datetime('now', '{since}')"
        ),
        "preferences_taught": _count(
            "SELECT COUNT(*) FROM preference_events WHERE household_id = ? "
            f"AND created_at >= datetime('now', '{since}')"
        ),
        # A point-in-time count, not a rate: grocery rows are deleted when
        # the list is rebuilt for a new week, and nothing records *when* an
        # item was checked off. Named "currently" so it can't be misread as
        # "checked off during the window".
        "grocery_items_currently_purchased": _count(
            "SELECT COUNT(*) FROM grocery_items WHERE household_id = ? AND status = 'purchased'"
        ),
    }
    # An empty week is the signal worth naming out loud, since it is the
    # one a summary of counts is easiest to skim straight past.
    summary["looks_inactive"] = (
        summary["chat_turns"] == 0
        and summary["meals_cooked"] == 0
        and summary["plans_generated"] == 0
    )
    return summary


# ---------- errors, so somebody finds out ----------

# What a single error row is allowed to say. Deliberately narrow: a class
# name or short reason, and a route or tool name. NO request bodies, no
# tool arguments, no tracebacks, no user text -- the same rule chat_turns
# follows, and for the same reason. A table of everything that went wrong,
# holding the content of what people typed, would quietly become the most
# sensitive thing in the database.
_MAX_DETAIL = 200
_MAX_WHERE = 120


def record_error(kind: str, where: str = "", detail: str = "") -> None:
    """
    Record that something broke. Never raises.

    Called from error paths, so it cannot be allowed to fail there -- an
    exception here would replace a handled 500 with an unhandled one, and
    turn "something went wrong" into "something went wrong twice, and the
    second one is ours". Everything is best-effort and swallowed.
    """
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO error_events (household_id, kind, where_, detail) VALUES (?, ?, ?, ?)",
            (household_id(), str(kind)[:40], str(where)[:_MAX_WHERE], str(detail)[:_MAX_DETAIL]),
        )
        conn.commit()
    except Exception:
        import logging

        logging.getLogger("home_manager").exception("Recording an error event failed")
    finally:
        if conn is not None:
            conn.close()


def get_recent_errors(days: int = 1, limit: int = 50) -> dict:
    """
    What broke for this household recently — the part of the morning
    report that leads, when there is anything to lead with.

    Returns counts by kind plus the most recent rows, newest first, so a
    report can say "11 tool failures" without printing eleven lines.
    """
    days = max(1, int(days))
    limit = max(1, min(int(limit), 200))
    since = f"-{days} days"
    conn = get_conn()
    try:
        hid = household_id()
        by_kind = {
            r["kind"]: r["n"]
            for r in conn.execute(
                "SELECT kind, COUNT(*) AS n FROM error_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "GROUP BY kind ORDER BY n DESC",
                (hid,),
            ).fetchall()
        }
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT kind, where_ AS location, detail, created_at FROM error_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "ORDER BY id DESC LIMIT ?",
                (hid, limit),
            ).fetchall()
        ]
        return {
            "days": days,
            "total": sum(by_kind.values()),
            "by_kind": by_kind,
            "recent": recent,
        }
    finally:
        conn.close()
