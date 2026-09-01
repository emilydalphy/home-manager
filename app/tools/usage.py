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
"""
from __future__ import annotations

import time

from ..db import get_conn
from ._shared import household_id


def record_chat_turn(
    rounds: int = 0,
    input_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 0,
    seconds: float = 0.0,
) -> None:
    """
    Record that a chat turn happened, and what it cost. No message content
    -- see schema.sql on chat_turns for why.

    Never raises: this is bookkeeping attached to a chat turn that has
    already succeeded, and a failure to write a stats row must not turn a
    good reply into an error for the person waiting on it. A broken write
    now shows up in the logs instead (agent.py's tool-failure logging is
    the same principle -- silence is the thing being fixed).
    """
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO chat_turns (household_id, rounds, input_tokens, cache_read_tokens, "
            "cache_write_tokens, output_tokens, seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (household_id(), rounds, input_tokens, cache_read_tokens,
             cache_write_tokens, output_tokens, seconds),
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
    _last_touched[household] = now
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE households SET last_active_at = datetime('now') WHERE id = ?",
            (household,),
        )
        conn.commit()
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
    conn = get_conn()
    hid = household_id()
    since = f"-{int(days)} days"

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

    summary = {
        "days": int(days),
        "last_active_at": conn.execute(
            "SELECT last_active_at FROM households WHERE id = ?", (hid,)
        ).fetchone()["last_active_at"],
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
    conn.close()
    return summary
