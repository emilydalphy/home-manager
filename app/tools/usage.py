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

import statistics
import time

from ..db import get_conn
from ._shared import household_id


_TURN_FIELDS = ("rounds", "input_tokens", "cache_read_tokens",
                "cache_write_tokens", "output_tokens", "seconds")

_CALL_TOKEN_FIELDS = ("input_tokens", "cache_read_tokens",
                      "cache_write_tokens", "output_tokens")


# US dollars per million tokens, per model, from Anthropic's list prices.
# Cache reads bill at 0.1x the input rate and cache writes at 1.25x, which
# is why those are spelled out rather than derived -- if the multipliers
# ever change, the change belongs in one visible table and not in
# arithmetic buried in a function.
_RATES_PER_MTOK = {
    "claude-sonnet-5": {
        "input": 2.00, "cache_read": 0.20, "cache_write": 2.50, "output": 10.00,
    },
    # The chat-fallback model (agent.CHAT_FALLBACK_MODEL) -- keyed to the
    # exact dated snapshot it's pinned to, since that's the literal string
    # api_calls.model records for a fallback call. $1/$5 per MTok, verified
    # 2026-09-03 against Anthropic's published Claude Haiku 4.5 rate (same
    # 0.1x/1.25x cache multipliers as every other model in this table). If
    # CHAT_FALLBACK_MODEL is ever overridden to a different model string,
    # add a matching row here too -- otherwise price_tokens raises (below)
    # the moment a fallback call needs pricing, rather than silently
    # mis-billing it.
    "claude-haiku-4-5-20251001": {
        "input": 1.00, "cache_read": 0.10, "cache_write": 1.25, "output": 5.00,
    },
}

# The model the chat loop actually runs on. Repeated here rather than
# imported from agent.py, which imports this package -- importing it back
# would make the cycle real. tests/test_usage.py pins this to agent.MODEL
# and pins that a rate exists for it, so swapping the model fails loudly
# instead of quietly billing the new model at the old one's prices.
_PRICED_MODEL = "claude-sonnet-5"


def price_tokens(tokens: dict, model: str = _PRICED_MODEL) -> dict:
    """
    Turn a bag of token counts into dollars.

    Cost is derived on read rather than stored on the row at write time,
    and that is deliberate: prices change and models change, but a dollar
    figure written into a row is frozen at whatever was true that day and
    no later correction can reach it. Tokens are the fact worth storing;
    money is a view over them, recomputed from a rate table anyone can see.

    Rounded to six decimal places -- a household's week of chat costs
    cents, so rounding to the nearest cent would report most real weeks as
    zero.
    """
    try:
        rates = _RATES_PER_MTOK[model]
    except KeyError:
        # Naming the model beats a bare KeyError, because the realistic way
        # to get here is swapping agent.MODEL and forgetting the rate row --
        # and the fix is to add one, not to debug a dictionary lookup.
        raise ValueError(
            f"No published token rate for {model!r}. Add it to "
            f"_RATES_PER_MTOK before pricing it."
        ) from None
    costs = {
        field: round(int(tokens.get(field, 0)) * rate / 1_000_000, 6)
        for field, rate in rates.items()
    }
    # Summed from the unrounded parts, so the total doesn't drift from its
    # own components at the sixth decimal place.
    costs["total"] = round(
        sum(int(tokens.get(f, 0)) * r / 1_000_000 for f, r in rates.items()), 6
    )
    costs["model"] = model
    return costs


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


def record_api_call(call_site: str, model: str, usage: dict | None = None,
                     seconds: float = 0.0) -> None:
    """
    Record one Anthropic API call, whatever it was for -- chat, weekly-plan
    generation, a photo scan, a chore recommendation, anything that goes
    through agent._create_with_retry. See schema.sql on api_calls for why
    this is a separate table from chat_turns rather than a replacement
    for it.

    Same shape and same reasoning as record_chat_turn: one dict of
    whatever the caller tallied (an unknown key is ignored, not fatal),
    and never raises -- this is bookkeeping wrapped around a call that
    already succeeded, and it must not be what turns that success into a
    500.
    """
    values = usage or {}
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO api_calls (household_id, call_site, model, input_tokens, "
            "cache_read_tokens, cache_write_tokens, output_tokens, seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (household_id(), str(call_site), str(model),
             *(int(values.get(f, 0)) for f in _CALL_TOKEN_FIELDS), float(seconds)),
        )
        conn.commit()
    except Exception:
        import logging
        logging.getLogger("home_manager").exception("Recording an API call failed")
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


def _cost_breakdown(conn, hid: int, since_sql: str) -> dict:
    """
    All-in cost across every call site, for one household, for rows with
    created_at >= since_sql -- a SQL expression the WHERE clause can
    compare against directly, e.g. "datetime('now', '-7 days')" or
    "datetime('now', 'start of month')".

    Grouped by call_site (and priced per-row at the model that row
    actually ran on, never an assumed single model) so a report can show
    where the money is actually going -- the same "measure jobs, not
    calls" correction this ticket already applied to chat now applies to
    the whole bill.
    """
    rows = conn.execute(
        "SELECT call_site, model, COUNT(*) AS calls, "
        "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
        "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "COALESCE(SUM(seconds), 0) AS seconds "
        f"FROM api_calls WHERE household_id = ? AND created_at >= {since_sql} "
        "GROUP BY call_site, model",
        (hid,),
    ).fetchall()

    by_call_site: dict[str, dict] = {}
    total = {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0, "total": 0.0}
    for r in rows:
        # price_tokens' rate table is keyed "input"/"cache_read"/"cache_write"/
        # "output" (see _RATES_PER_MTOK); api_calls' columns are
        # "input_tokens" etc. Remapped here rather than renaming one side
        # to match the other, since each name is the right one for its own
        # table/function.
        tokens = {
            "input": r["input_tokens"], "cache_read": r["cache_read_tokens"],
            "cache_write": r["cache_write_tokens"], "output": r["output_tokens"],
        }
        cost = price_tokens(tokens, model=r["model"] or _PRICED_MODEL)
        entry = by_call_site.setdefault(r["call_site"], {
            "calls": 0, "seconds": 0.0,
            "tokens": {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0},
            "cost": {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0, "total": 0.0},
        })
        entry["calls"] += r["calls"]
        entry["seconds"] += r["seconds"]
        for f in ("input", "cache_read", "cache_write", "output"):
            entry["tokens"][f] += tokens[f]
        for k in total:
            entry["cost"][k] = round(entry["cost"][k] + cost[k], 6)
            total[k] += cost[k]

    for entry in by_call_site.values():
        entry["seconds"] = round(entry["seconds"], 1)

    return {
        "by_call_site": by_call_site,
        "total_cost": {k: round(v, 6) for k, v in total.items()},
    }


def get_month_to_date_cost() -> dict:
    """
    All-in cost so far this calendar month, across every call site -- the
    number Emily's $1/household/month target (set 2026-09-03) is judged
    against. Deliberately the calendar month, not a rolling 30-day window:
    the target is a monthly bill, and a rolling window would drift out of
    sync with it (and would keep showing spend from a month that's over).
    """
    conn = get_conn()
    try:
        return _cost_breakdown(conn, household_id(), "datetime('now', 'start of month')")
    finally:
        conn.close()


def _plan_generation_stats(conn, hid: int, since_sql: str) -> dict:
    """
    How long, and how much, week generation actually takes -- Emily asked
    for this explicitly ("how long it takes to generate the week"), and
    it's also the single most expensive call in the app (this ticket's
    2026-08-31 baseline measured ~18,000 uncached input tokens, ~37
    seconds, once per week).

    p50/max rather than an average: one slow outlier would otherwise hide
    inside a mean, and "how long does it usually take, and how bad does
    it get" is the actual question being asked.
    """
    rows = conn.execute(
        "SELECT model, seconds, input_tokens, cache_read_tokens, cache_write_tokens, "
        "output_tokens FROM api_calls WHERE household_id = ? "
        "AND call_site = 'generate_weekly_plan_llm' "
        f"AND created_at >= {since_sql}",
        (hid,),
    ).fetchall()
    if not rows:
        return {"count": 0}

    seconds = sorted(r["seconds"] for r in rows)
    total_cost = {"input": 0.0, "cache_read": 0.0, "cache_write": 0.0, "output": 0.0, "total": 0.0}
    for r in rows:
        tokens = {
            "input": r["input_tokens"], "cache_read": r["cache_read_tokens"],
            "cache_write": r["cache_write_tokens"], "output": r["output_tokens"],
        }
        cost = price_tokens(tokens, model=r["model"] or _PRICED_MODEL)
        for k in total_cost:
            total_cost[k] += cost[k]

    return {
        "count": len(rows),
        "p50_seconds": round(statistics.median(seconds), 1),
        "max_seconds": round(max(seconds), 1),
        "total_cost": {k: round(v, 6) for k, v in total_cost.items()},
    }


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
    # Tokens are what happened; this is what they cost. Derived here so the
    # figure always reflects today's rate table rather than whatever was
    # true when the rows were written -- see price_tokens.
    #
    # Chat-only, from chat_turns -- kept for backward compatibility with
    # what already reads this key. The all-in figure below (every call
    # site, from api_calls) is the one the $1/household/month target is
    # judged against.
    summary["cost"] = price_tokens(summary["tokens"])

    # All-in, every call site, this calendar month -- see get_month_to_date_cost.
    summary["month_to_date_cost"] = _cost_breakdown(
        conn, hid, "datetime('now', 'start of month')"
    )
    # Week-generation latency + cost, same calendar-month window so the two
    # numbers on the report line up with each other.
    summary["plan_generation"] = _plan_generation_stats(
        conn, hid, "datetime('now', 'start of month')"
    )

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

# Nothing else deletes these rows, and a table that only grows is a slow
# disk-fill on a Railway volume holding the household's real data. Kept
# small on purpose: this is a "what broke recently" signal for a morning
# report, not an archive. Pruned every _PRUNE_EVERY inserts rather than on
# each one, so a burst of errors doesn't pay for a DELETE per row.
_KEEP_ROWS = 1000
_KEEP_DAYS = 30
_PRUNE_EVERY = 50
# Counted PER HOUSEHOLD, not globally. A single shared counter meant the
# prune fired for whichever household happened to make the 50th call, so
# two households interleaving left one of them permanently unpruned —
# measured at ~3x the cap and still climbing. A dict keyed by household is
# the smallest thing that makes the cap mean what it says.
_since_prune: dict[int, int] = {}


def _prune(conn, hid: int) -> None:
    _since_prune[hid] = _since_prune.get(hid, 0) + 1
    if _since_prune[hid] < _PRUNE_EVERY:
        return
    _since_prune[hid] = 0
    conn.execute(
        f"DELETE FROM error_events WHERE household_id = ? "
        f"AND created_at < datetime('now', '-{_KEEP_DAYS} days')",
        (hid,),
    )
    conn.execute(
        "DELETE FROM error_events WHERE household_id = ? AND id NOT IN "
        "(SELECT id FROM error_events WHERE household_id = ? ORDER BY id DESC LIMIT ?)",
        (hid, hid, _KEEP_ROWS),
    )


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
        # Give up on a locked database quickly instead of waiting out
        # sqlite3's 5s default. This runs on an error path, and the case
        # that matters is the database *being* the thing that broke: then
        # every 500 would park a threadpool worker for five seconds, in the
        # same pool the app's sync routes run in, and error-recording would
        # help convert one failure into an outage. A missed row is the
        # cheaper loss.
        conn.execute("PRAGMA busy_timeout = 500")
        hid = household_id()
        conn.execute(
            "INSERT INTO error_events (household_id, kind, where_, detail) VALUES (?, ?, ?, ?)",
            (hid, str(kind)[:40], str(where)[:_MAX_WHERE], str(detail)[:_MAX_DETAIL]),
        )
        _prune(conn, hid)
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
