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

import json
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


# Chat tools that ALSO exist as a tap somewhere in the app. The point of
# the list is the cheapest question there is to ask of this data: how much
# of what people pay a chat turn for could already have been one cent's
# worth of tapping? A high count there is not a bug, it is a signpost --
# either the tap is too hard to find or chat is simply the nicer door.
#
# A maintained list, deliberately, not something derived: there is nothing
# in the code that links a tool to a control, and a wrong entry here costs
# one number in a report rather than any behaviour. Each is named with the
# screen it is on, so a future reader can check it rather than trust it.
TOOLS_WITH_A_TAP = {
    "swap_meal_in_plan",        # Plan -> a dish row -> Swap
    "swap_dinner_nights",       # Plan -> the day tiles, dragged
    "add_grocery_item",         # Shop -> "+ Add something"
    "add_grocery_items",        # Shop -> the same sheet
    "mark_grocery_item",        # Shop -> a row's tick
    "remove_grocery_item",      # Shop -> a row's dots -> Remove
    "check_off_meal",           # Cook -> "Mark it cooked"
    "check_off_prep_step",      # Today -> a prep or fridge move's tick
    "approve_weekly_plan",      # Plan -> Approve
    "generate_weekly_plan",     # Plan -> Plan the week
    "discard_draft_plan",       # Plan -> More -> Drop this draft
    "take_the_night_off",       # Today -> Tonight still good? -> Not tonight
    "set_member_attendance",    # Plan the week -> Is anyone out?
}
# Checked against agent.TOOL_FUNCTIONS by a test, and that test earned its
# keep immediately: the first draft of this list named resolve_open_slot
# and set_slot_attendance, which are functions in tools/ that the model
# has never been given. Both would have sat here matching nothing, and the
# count they feed would have read low for ever with nothing saying so.


def _tool_names_json(names) -> str:
    """
    The turn's tool names as the JSON list the column stores.

    Defensive about its input for record_chat_turn's own reason: this is
    bookkeeping attached to a reply that already worked, so a tally that
    arrived in an unexpected shape must cost an empty list, never the row.
    Anything that isn't a string is dropped rather than coerced -- a tool
    name is an identifier the app itself wrote, and something else turning
    up here means the tally was wrong, not that it wants rescuing.
    """
    if not isinstance(names, (list, tuple)):
        return "[]"
    return json.dumps([n for n in names if isinstance(n, str)])


def record_chat_turn(usage: dict | None = None) -> int | None:
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

    Returns the new row's id, or None when the write failed, so the theme
    call (app/chat_themes.py) can land its label on this row a moment
    later without holding the reply up.
    """
    values = usage or {}
    conn = None
    try:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO chat_turns (household_id, rounds, input_tokens, cache_read_tokens, "
            "cache_write_tokens, output_tokens, seconds, tools_called_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (household_id(), *(values.get(f, 0) for f in _TURN_FIELDS),
             _tool_names_json(values.get("tools_called"))),
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        import logging
        logging.getLogger("home_manager").exception("Recording a chat turn failed")
        return None
    finally:
        if conn is not None:
            conn.close()


def record_chat_theme(turn_id: int, theme: str) -> None:
    """
    Land a chat turn's theme on its row. Never raises -- it runs on a
    background thread after the reply has gone, and there is nobody left
    to hand an exception to.

    Scoped to the current household as well as the id, so a wrong id can
    only ever miss, never label another household's turn. The caller has
    already checked `theme` against the fixed list; this is a plain write.
    """
    conn = None
    try:
        conn = get_conn()
        conn.execute("PRAGMA busy_timeout = 500")
        conn.execute(
            "UPDATE chat_turns SET theme = ? WHERE id = ? AND household_id = ?",
            (str(theme), int(turn_id), household_id()),
        )
        conn.commit()
    except Exception:
        import logging
        logging.getLogger("home_manager").exception("Recording a chat theme failed")
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


def _chat_tool_counts(conn, hid: int, since: str) -> dict:
    """
    What chat was FOR over the window: how many times each tool was called,
    how many turns called nothing at all, and how many turns asked for
    something that already has a tap.

    Counted here rather than in the report because the report reads every
    household through one HTTP call and should not be parsing JSON out of
    rows; and counted from the stored names rather than from a live tally
    so that it still answers for days the server has since restarted.

    `talk_only` -- turns that called no tool -- is the number worth
    watching beside the rest. A household asking and getting only
    conversation back is either being answered well or is stuck, and
    which one it is cannot be read from here; it is a prompt to go and
    look, not a verdict.

    Read defensively: `tools_called_json` is written by _tool_names_json
    above and so is always a list of strings, but this function also runs
    over rows written before that column existed (default '[]') and over
    any row a future writer gets wrong. A row it cannot read is counted as
    unreadable rather than dropped silently, because a count that quietly
    shrinks is the failure this whole card exists to stop.
    """
    rows = conn.execute(
        "SELECT tools_called_json FROM chat_turns "
        f"WHERE household_id = ? AND created_at >= datetime('now', '{since}')",
        (hid,),
    ).fetchall()
    counts: dict[str, int] = {}
    talk_only = 0
    tappable_turns = 0
    unreadable = 0
    for row in rows:
        try:
            names = json.loads(row["tools_called_json"] or "[]")
        except (TypeError, ValueError):
            unreadable += 1
            continue
        if not isinstance(names, list):
            unreadable += 1
            continue
        names = [n for n in names if isinstance(n, str)]
        if not names:
            talk_only += 1
            continue
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        if any(n in TOOLS_WITH_A_TAP for n in names):
            tappable_turns += 1
    return {
        # Busiest first, then by name, so the line reads the same way twice
        # running and a reader can compare two mornings.
        "counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "talk_only_turns": talk_only,
        "turns_with_a_tap": tappable_turns,
        "unreadable_turns": unreadable,
    }


def _chat_theme_counts(conn, hid: int, since_sql: str) -> dict[str, int]:
    """
    What chat was ABOUT, by theme label, busiest first -- layer 2 of the
    same card as _chat_tool_counts above. Only turns that were classified:
    '' means the flag was off or the call failed, and counting those as
    anything would print zeros that read like a quiet household.
    """
    rows = conn.execute(
        "SELECT theme, COUNT(*) AS n FROM chat_turns "
        f"WHERE household_id = ? AND theme != '' AND created_at >= {since_sql} "
        "GROUP BY theme",
        (hid,),
    ).fetchall()
    return dict(sorted(((r["theme"], r["n"]) for r in rows), key=lambda kv: (-kv[1], kv[0])))


def _pre_shop_counts(conn, hid: int, since: str) -> dict:
    """
    Was the "Maybe already home" check RIGHT? (Emily, 2026-09-22.)

    The pre-shop check takes a line off the list the household actually
    shops from. Wrong, it costs them an ingredient at dinner — so the rate
    has to be measurable, and the only honest way to measure it is with
    the denominator beside the answers.

    FOUR COUNTS, over the same rolling window as everything else in this
    summary:

      flagged  — grocery lines the card HELD OFF THE LIST, counted at the
                 first time each was held, never per read (see
                 pre_shop._record_flags_raised: the flags are computed on
                 every list load, so a per-read count would be a dozen
                 times the truth). Questions asked, not answered: the gap
                 between this and the other three is cards nobody replied
                 to, which is a thing worth seeing rather than a thing to
                 hide.
      kept     — "Buy it anyway" and "Keep all N" together. The household
                 saying the check was wrong.
      dropped  — "Drop it", and still dropped. The check was right.
      undone   — dropped, then put back. The check was wrong and they
                 caught it. Its own bucket, NOT also inside `dropped`, so
                 the three answers partition the answered flags and
                 nothing has to be subtracted from anything.

    Each count is LINES, one row per line (schema.sql on
    pre_shop_decisions), so the answers can never exceed the flags.

    Both sides of every comparison are UTC instants — flagged_at and
    decided_at are datetime('now'), the boundary is datetime('now', '-N
    days') — so this is an instant against an instant and introduces no
    household-day arithmetic. The household's own clock matters where a
    DAY is the unit (pre_shop._household_day_start_utc, which turns a
    household date into the instant it began); it does not apply to a
    rolling window that never names a date.

    A line first flagged before the window and answered inside it counts
    as an answer with no flag, so the four numbers can fail to add up over
    a short window. Left as it is deliberately: re-stamping the flag at
    decision time would count one question twice, and moving the window
    for one of the four would make it a different window from the rest of
    the report.
    """
    # SUM(CASE ...) rather than COUNT(*) FILTER: the FILTER clause wants
    # SQLite 3.30+, this box has 3.53 and the deployment's base image is
    # somebody else's decision. Nothing else in app/ uses FILTER either.
    row = conn.execute(
        "SELECT "
        f"  SUM(CASE WHEN flagged_at >= datetime('now', '{since}') THEN 1 ELSE 0 END) AS flagged, "
        "  SUM(CASE WHEN decision IN ('kept', 'kept_all') "
        f"       AND decided_at >= datetime('now', '{since}') THEN 1 ELSE 0 END) AS kept, "
        "  SUM(CASE WHEN decision = 'dropped' "
        f"       AND decided_at >= datetime('now', '{since}') THEN 1 ELSE 0 END) AS dropped, "
        "  SUM(CASE WHEN decision = 'undone' "
        f"       AND decided_at >= datetime('now', '{since}') THEN 1 ELSE 0 END) AS undone "
        "FROM pre_shop_decisions WHERE household_id = ?",
        (hid,),
    ).fetchone()
    # SUM over no rows is NULL, not 0 — a household that has never seen
    # the card must report zeros, not nulls the printer would render as
    # "None flagged".
    if row is None or row["flagged"] is None:
        return {"flagged": 0, "kept": 0, "dropped": 0, "undone": 0}
    return {
        "flagged": row["flagged"],
        "kept": row["kept"],
        "dropped": row["dropped"],
        "undone": row["undone"],
    }


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

    chat_tools = _chat_tool_counts(conn, hid, since)
    # Same window as chat_tools for the household's own line, plus the
    # calendar month so the report can roll themes up across households
    # beside the month's cost.
    chat_themes = {
        "counts": _chat_theme_counts(conn, hid, f"datetime('now', '{since}')"),
        "month_counts": _chat_theme_counts(conn, hid, "datetime('now', 'start of month')"),
    }

    household_row = conn.execute(
        "SELECT last_active_at FROM households WHERE id = ?", (hid,)
    ).fetchone()

    summary = {
        "days": int(days),
        # A household row that isn't there is a caller error, not a crash:
        # returning None reads as "never seen", which is the truth.
        "last_active_at": (household_row["last_active_at"] if household_row else None),
        "chat_turns": chat["turns"],
        "chat_tools": chat_tools,
        "chat_themes": chat_themes,
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
        # Was the "Maybe already home" check right? A real rate over the
        # same window as everything above -- see _pre_shop_counts for what
        # each of the four honestly counts.
        "pre_shop": _pre_shop_counts(conn, hid, since),
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
# The shape columns. Same discipline one level finer: a type name, a script
# file and line, a handful of stack frames. Sanitised at capture by the
# caller that has an untrusted end (main._safe_client_shape); capped again
# here, because every other caller in this app is trusted and this function
# is the one place that has to hold for all of them.
_MAX_ERROR_TYPE = 40
_MAX_SOURCE = 80
_MAX_STACK = 240
# When a run of dropped requests stops being a blip and becomes the news.
# See get_recent_errors -- five is a judgement, not a measurement.
NETWORK_CLUSTER_THRESHOLD = 5

_MAX_REASON = 20
_MAX_REQUEST = 80
# Eight steps of at most a method, a route pattern and a status each. A
# ceiling, not a target: main._safe_client_trail has already dropped
# anything that is not a closed-vocabulary step.
_MAX_TRAIL = 800

# How long an identical shape keeps counting into one row rather than
# starting a new one. 24 hours, because the report reads a day at a time: a
# window any longer and "12 in the last 1d" would be counting last week's
# occurrences too, a window any shorter and a slow leak is still one row per
# hour. See schema.sql's comment on error_events.occurrences.
_DEDUPE_WINDOW = "-1 day"

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


def record_error(
    kind: str,
    where: str = "",
    detail: str = "",
    error_type: str = "",
    source: str = "",
    stack_shape: str = "",
    reason: str = "",
    request_shape: str = "",
    trail: str = "",
) -> None:
    """
    Record that something broke. Never raises.

    Called from error paths, so it cannot be allowed to fail there -- an
    exception here would replace a handled 500 with an unhandled one, and
    turn "something went wrong" into "something went wrong twice, and the
    second one is ours". Everything is best-effort and swallowed.

    The last five are the SHAPE of a browser error -- a type, a script file
    and line, a few stack frames, and, for the one case none of those can
    describe, why there is no location and which route was being fetched.
    They are optional because the other three callers (a status code, a tool
    name, a bucket) have no shape to give. They must already be sanitised:
    the caller with an untrusted end is the one that knows what a valid
    shape looks like.

    An identical shape seen again inside _DEDUPE_WINDOW bumps that row's
    occurrences instead of writing a second one. A render loop fires these
    as fast as it paints, and the prune evicts oldest-first -- so without
    this, one broken screen quietly deletes every other error in the table.

    `trail` -- the last few steps before a browser error, already reduced
    by main._safe_client_trail -- is deliberately NOT in that key. It is
    different every time by nature (a request that took 200 then 304, one
    more screen visited), so keying on it would turn "the same bug, 12
    times" back into 12 rows. On a repeat the row keeps the LATEST
    non-empty trail instead: the freshest account of how somebody reached
    a bug that is still happening, beside a count that says how often.
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
        row = (
            hid,
            str(kind)[:40],
            str(where)[:_MAX_WHERE],
            str(detail)[:_MAX_DETAIL],
            str(error_type)[:_MAX_ERROR_TYPE],
            str(source)[:_MAX_SOURCE],
            str(stack_shape)[:_MAX_STACK],
            str(reason)[:_MAX_REASON],
            str(request_shape)[:_MAX_REQUEST],
        )
        trail = str(trail)[:_MAX_TRAIL]
        existing = conn.execute(
            "SELECT id FROM error_events WHERE household_id = ? AND kind = ? AND where_ = ? "
            "AND detail = ? AND error_type = ? AND source = ? AND stack_shape = ? "
            # In the dedupe key on purpose: the row this was built for is a
            # bare "TypeError on /" with nothing else to tell two of them
            # apart, so without these a network blip and a real bug on the
            # same page would fold into one row and the count would say
            # neither.
            "AND reason = ? AND request_shape = ? "
            f"AND created_at >= datetime('now', '{_DEDUPE_WINDOW}') "
            "ORDER BY id DESC LIMIT 1",
            row,
        ).fetchone()
        if existing:
            # The latest trail wins, but an empty one never erases a real
            # one: a repeat from a page still running last week's reporter
            # has no trail to give, and that is not news about the bug.
            conn.execute(
                "UPDATE error_events SET occurrences = occurrences + 1, "
                "last_seen_at = datetime('now'), "
                "trail = CASE WHEN ? != '' THEN ? ELSE trail END WHERE id = ?",
                (trail, trail, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO error_events "
                "(household_id, kind, where_, detail, error_type, source, stack_shape, "
                " reason, request_shape, trail, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                row + (trail,),
            )
            _prune(conn, hid)
        conn.commit()
    except Exception:
        import logging

        logging.getLogger("home_manager").exception("Recording an error event failed")
    finally:
        if conn is not None:
            conn.close()


def record_plan_quality(plan_id, violations) -> None:
    """Persist what a generated week got wrong about the food.

    Same never-raise discipline as record_error above, and for the same
    reason: a plan that would otherwise generate fine must not fail because
    an optional observation could not be written.
    """
    if not violations:
        return
    conn = None
    try:
        conn = get_conn()
        conn.execute("PRAGMA busy_timeout = 500")
        hid = household_id()
        conn.executemany(
            "INSERT INTO plan_quality_events "
            "(household_id, weekly_plan_id, rule, severity, date, slot, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (hid, plan_id, str(v.rule)[:60], str(v.severity)[:10],
                 str(v.date or "")[:20], str(v.slot or "")[:20],
                 str(v.message or "")[:_MAX_DETAIL])
                for v in violations
            ],
        )
        conn.commit()
    except Exception:
        import logging

        logging.getLogger("home_manager").exception("Recording plan quality events failed")
    finally:
        if conn is not None:
            conn.close()


def get_recent_plan_quality(days: int = 7, limit: int = 50) -> dict:
    """What the last few weeks of plans got wrong about the food.

    Seven days by default rather than one: a week is generated about once a
    week, so a one-day window would report nothing on six mornings out of
    seven and look like good news.
    """
    days = max(1, int(days))
    limit = max(1, min(int(limit), 200))
    since = f"-{days} days"
    conn = get_conn()
    try:
        hid = household_id()
        by_rule = {
            r["rule"]: r["n"]
            for r in conn.execute(
                "SELECT rule, COUNT(*) AS n FROM plan_quality_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "GROUP BY rule ORDER BY n DESC",
                (hid,),
            ).fetchall()
        }
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT rule, severity, date, slot, message, created_at "
                "FROM plan_quality_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "ORDER BY id DESC LIMIT ?",
                (hid, limit),
            ).fetchall()
        ]
        return {
            "days": days,
            "total": sum(by_rule.values()),
            "by_rule": by_rule,
            "recent": recent,
        }
    finally:
        conn.close()


def get_recent_errors(days: int = 1, limit: int = 50) -> dict:
    """
    What broke for this household recently — the part of the morning
    report that leads, when there is anything to lead with.

    Returns counts by kind plus the most recent rows, newest first, so a
    report can say "11 tool failures" without printing eleven lines.

    Kind 'voice' — a chat reply that drifted from the voice rules (see
    agent._note_voice_drift) — is set aside under `voice_drift` and kept
    out of `total`, `by_kind` and `recent`: a reply that said "inventory"
    is worth a line in the morning report, but it is not breakage, and
    it must not turn the report's exit code into "something broke".

    A browser error whose `reason` is 'network' -- a request that never
    reached the server, recognised by the browser's own message (see
    main._NETWORK_FAILURE_MESSAGES) -- is set aside under `network` the same
    way, for the same reason: one tester walking into a lift is not an
    outage, and it must not turn the exit code into "something broke".

    UNLESS THEY CLUSTER. At NETWORK_CLUSTER_THRESHOLD or more in the window
    they are counted like anything else and stay in `recent`, because at
    that point they have stopped being a blip and started being the news --
    a tester whose phone cannot reach the app all evening has a problem
    worth waking up to, even though every individual row is "just" a
    dropped request. Five is a judgement, not a measurement: it is more
    than a lift or a tunnel and fewer than an evening.

    The counts are SUM(occurrences), not COUNT(*) -- eleven of one failure
    is eleven failures however many rows record_error folded them into, and
    a count that shrank when deduping landed would have read as the app
    getting better.
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
                "SELECT kind, SUM(occurrences) AS n FROM error_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "AND kind != 'voice' GROUP BY kind ORDER BY n DESC",
                (hid,),
            ).fetchall()
        }
        voice_flags = {
            r["detail"]: r["n"]
            for r in conn.execute(
                "SELECT detail, SUM(occurrences) AS n FROM error_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "AND kind = 'voice' GROUP BY detail ORDER BY n DESC",
                (hid,),
            ).fetchall()
        }
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT kind, where_ AS location, detail, error_type, source, stack_shape, "
                "reason, request_shape, trail, occurrences, last_seen_at, created_at FROM error_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "AND kind != 'voice' "
                # Newest first means most recently SEEN, not most recently
                # filed: a row deduping a failure that is still happening is
                # the freshest news in the table, whatever its id. NULLIF for
                # rows written before last_seen_at existed, which have only
                # ever been seen once, at created_at.
                "ORDER BY COALESCE(NULLIF(last_seen_at, ''), created_at) DESC, id DESC LIMIT ?",
                (hid, limit),
            ).fetchall()
        ]
        network = {
            r["request_shape"]: r["n"]
            for r in conn.execute(
                "SELECT request_shape, SUM(occurrences) AS n FROM error_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "AND kind != 'voice' AND reason = 'network' "
                "GROUP BY request_shape ORDER BY n DESC",
                (hid,),
            ).fetchall()
        }
        network_total = sum(network.values())
        network_by_kind = {
            r["kind"]: r["n"]
            for r in conn.execute(
                "SELECT kind, SUM(occurrences) AS n FROM error_events "
                f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') "
                "AND kind != 'voice' AND reason = 'network' GROUP BY kind",
                (hid,),
            ).fetchall()
        }
        if network_total and network_total < NETWORK_CLUSTER_THRESHOLD:
            # Below the threshold they are a blip, so they come back out of
            # the counts and the rows -- deliberately AFTER the queries
            # rather than as another WHERE clause, so the two can never
            # disagree about which rows are the network ones.
            #
            # Subtracted PER KIND, read from the rows themselves, rather
            # than taken off "client" on the assumption that every network
            # row is one. Nothing enforces that: `reason` and `kind` are
            # independent columns, and only report_client_error passes a
            # reason today. Found on review of this branch -- one real
            # client error beside three network rows recorded under any
            # other kind gave `by_kind {'server': 3}`, i.e. 1 - 3 = -2,
            # filtered out by the `n > 0` below, and the real error was
            # GONE from the morning report. That is the exact direction
            # this whole block exists to prevent.
            by_kind = {
                k: n - network_by_kind.get(k, 0)
                for k, n in by_kind.items()
            }
            by_kind = {k: n for k, n in by_kind.items() if n > 0}
            recent = [r for r in recent if r.get("reason") != "network"]
        return {
            "days": days,
            "total": sum(by_kind.values()),
            "by_kind": by_kind,
            "recent": recent,
            "voice_drift": {"total": sum(voice_flags.values()), "by_flags": voice_flags},
            "network": {
                "total": network_total,
                "by_request": network,
                "clustered": network_total >= NETWORK_CLUSTER_THRESHOLD,
            },
        }
    finally:
        conn.close()
