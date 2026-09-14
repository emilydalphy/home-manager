"""
Tonight, mid-afternoon: "Still good?" — the Now tab's top card (Loop Board
"Tonight's dinner, mid-day: walk me to sorting it out instead of the
day-editor workaround", Emily's decisions of 2026-09-13).

The anticipate phase of the mental-load model (SKILL.md): it is the
afternoon, dinner is hours away, and the app should be the one to notice
and ask — not the person, and not by way of the day sheet. Two answers:

- **Yes** keeps the plan and stops the question for the rest of the day.
  The memory is one row in notification_dismissals (household-wide, keyed
  by the date), the same table the bell and the plan-a-week offer use for
  "don't ask again", so it survives a reload and every phone in the house
  hears the same answer.
- **Something else** offers 2–3 dinners already on this plan — later
  nights first, a leftovers night named as one — and one tap trades tonight
  with that night (weekly_plan.swap_dinner_nights). A swap, never a delete:
  tonight's dish moves to the slot the chosen dish came from, every dish's
  grocery lines ride along by entry id, and the list itself is not touched.

Nothing here calls the model. Chat and favourites are deliberately not in
this card (Emily, 2026-09-13).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import get_conn
from ._shared import household_id
from . import notifications as _notifications
from . import weekly_plan as _weekly_plan

logger = logging.getLogger(__name__)

# From this hour of the household's local day, Now asks about tonight.
# One constant, on purpose: Emily's own moment was 3:50pm on a Sunday
# ("it's mid-day, I want to include sorting out dinner for tonight"), and
# Now's next-up rule already starts talking about tonight's cook around
# two (moves.LOOKAHEAD_HOURS before a 6:30 dinner minus the cooking).
# Two o'clock is the earlier of those — it errs toward asking while there
# is still time to change the answer. Change the number here and nothing
# else has to.
TONIGHT_ASK_HOUR = 14

# At most this many other nights on the sheet — "2–3 dishes already in
# this week's plan" (Emily). Enough to be a real choice, few enough to be
# one tap rather than a browse.
TONIGHT_OPTION_LIMIT = 3

# notification_dismissals key for "Yes, still good" — per household, per
# local day. Tomorrow is a different key, so tomorrow asks again.
TONIGHT_OK_KEY = "tonight-ok:{date}"

DEFAULT_TIMEZONE = "America/Toronto"


def _household_now() -> datetime:
    """
    Wall-clock now where the household lives (households.timezone, the
    column the morning text already keys its "once a morning" on). The
    server runs in UTC, and "is it afternoon yet" is a question about
    their kitchen, not the container.
    """
    conn = get_conn()
    row = conn.execute("SELECT timezone FROM households WHERE id = ?", (household_id(),)).fetchone()
    conn.close()
    name = (row["timezone"] if row else None) or DEFAULT_TIMEZONE
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(zone).replace(tzinfo=None)


def tonight_ok_key(day: str) -> str:
    return TONIGHT_OK_KEY.format(date=day)


def _dish_name(row) -> str:
    return (row["meal"] or "").strip()


def tonight_check(now: datetime | None = None) -> dict:
    """
    What Now's top card should say about tonight, and what "Something
    else" may offer.

    Returns:
      {
        "date": "2026-09-13",
        "ask": bool,            # show the question at all
        "reason": str,          # why not, when `ask` is False (see below)
        "afternoon": bool,      # TONIGHT_ASK_HOUR has passed, locally
        "answered": bool,       # "Yes" already given today
        "week_start": str|None, # the plan's week_start_date, for the swap route
        "dinner": {"entry_id", "meal", "is_leftovers", "leftovers_from"} | None,
        "options": [{"entry_id", "date", "weekday", "meal", "is_leftovers",
                     "leftovers_from"}],   # up to TONIGHT_OPTION_LIMIT
      }

    `ask` is True only when every one of these holds: it is the afternoon;
    a plan covers today and tonight's dinner on it is a real, planned,
    not-yet-cooked dish; and nobody has said "Yes" today. `reason` names
    the first that fails — 'morning', 'no_plan', 'components',
    'unplanned', 'away', 'open', 'cooked', 'answered' — so a screen (or a
    test) can tell a quiet card from a broken one. A day with no dinner
    planned is `reason` 'unplanned' with `dinner` None: Now's existing
    "Tonight needs a dinner" card already leads there, so this card stays
    out of its way rather than asking a second question about the same
    night.

    `options` are the other nights of THIS plan whose dinner would really
    trade with tonight's: later nights first (the natural "have that
    tonight, this later"), each one checked against the swap's own rules
    by a dry run (a night nobody is home, a dinner already cooked, a
    leftovers chain that would run backwards are all left off rather than
    offered and refused). Earlier nights are never offered — moving
    tonight's dish onto a night that has passed would make it vanish
    from the week, and this card is a swap, not a delete. A leftovers
    night is offered as what it is: `is_leftovers` True and
    `leftovers_from` naming the cook night, so the card can say
    "Bulgogi · leftovers from Wednesday".

    `now` is for tests; the app passes nothing and gets the household's
    local time.
    """
    now = now or _household_now()
    today = now.date().isoformat()
    afternoon = now.hour >= TONIGHT_ASK_HOUR
    out: dict = {
        "date": today, "ask": False, "reason": "", "afternoon": afternoon,
        "answered": False, "week_start": None, "dinner": None, "options": [],
    }

    conn = get_conn()
    try:
        # The plan whose period holds the household's LOCAL today — the
        # same period arithmetic _current_weekly_plan_row uses, minus its
        # fall-back to "the latest plan" (a plan that doesn't cover tonight
        # has nothing to say about it) and with the household's date
        # rather than the server's, which is already tomorrow in UTC by
        # eight in the evening in Toronto.
        plan = conn.execute(
            f"SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
            f"AND date({_weekly_plan._SQL_PERIOD_START}) <= date(?) "
            f"AND date({_weekly_plan._SQL_PERIOD_START}, '+' || {_weekly_plan._SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
            f"ORDER BY {_weekly_plan._SQL_APPROVED_FIRST}, created_at DESC, id DESC LIMIT 1",
            (household_id(), today, today),
        ).fetchone()
        if plan is None:
            out["reason"] = "no_plan"
            return out
        out["week_start"] = plan["week_start_date"]
        if plan["planning_mode"] == "component_based":
            out["reason"] = "components"
            return out

        rows = conn.execute(
            """
            SELECT mpe.id, mpe.date, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json,
                   COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?
              AND mpe.slot = 'dinner' AND mpe.component_category IS NULL
            ORDER BY mpe.date ASC, mpe.id ASC
            """,
            (plan["id"], household_id()),
        ).fetchall()
        answered = conn.execute(
            "SELECT 1 FROM notification_dismissals WHERE household_id = ? AND key = ?",
            (household_id(), tonight_ok_key(today)),
        ).fetchone() is not None
    finally:
        conn.close()

    out["answered"] = answered
    tonight = [r for r in rows if r["date"] == today]
    tonight_row = tonight[0] if tonight else None
    if tonight_row is None:
        out["reason"] = "unplanned"
        return out
    state = tonight_row["slot_state"] or "planned"
    if state == "planned_empty":
        out["reason"] = "away"
        return out
    if state == "open" or not _dish_name(tonight_row):
        out["reason"] = "open" if state == "open" else "unplanned"
        return out

    chains = {}
    try:
        from . import leftovers as _leftovers
        chains = _leftovers.plan_leftover_chains(plan["id"])["leftovers"]
    except Exception:
        logger.exception("Leftover chains could not be read for tonight's card")

    def _describe(r) -> dict:
        chained = chains.get(r["id"]) or {}
        source = chained.get("source") or {}
        return {
            "entry_id": r["id"],
            "date": r["date"],
            "weekday": _weekly_plan._weekday_of(r["date"]),
            "meal": _dish_name(r),
            "is_leftovers": bool(chained),
            "leftovers_from": _weekly_plan._weekday_of(source["date"]) if source.get("date") else None,
        }

    out["dinner"] = _describe(tonight_row)
    if (tonight_row["cooked_status"] or "") == "done":
        out["reason"] = "cooked"
        return out
    if not afternoon:
        out["reason"] = "morning"
    elif answered:
        out["reason"] = "answered"
    else:
        out["ask"] = True

    # The other nights, later first, each proven swappable by a dry run.
    later = [r for r in rows if r["date"] > today]
    options: list[dict] = []
    for r in later:
        if (r["slot_state"] or "planned") != "planned" or not _dish_name(r):
            continue
        if (r["cooked_status"] or "") == "done":
            continue
        try:
            probe = _weekly_plan._apply_dinner_nights_swap(
                plan["id"], today, r["date"], undo=False, dry_run=True,
            )
        except ValueError:
            continue
        except Exception:
            logger.exception("Dry-run swap for tonight's card failed on %s", r["date"])
            continue
        if probe.get("status") != "ok":
            continue
        options.append(_describe(r))
        if len(options) >= TONIGHT_OPTION_LIMIT:
            break
    out["options"] = options
    return out


def tonight_keep(day: str | None = None) -> dict:
    """
    "Yes, still good." Remembers the answer for the rest of the household's
    local day and nothing else — the plan is exactly as it was. Idempotent:
    a second Yes is the same row (INSERT OR IGNORE).
    """
    day = day or _household_now().date().isoformat()
    try:
        date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ValueError("That isn't a date I recognise.")
    _notifications.dismiss_notification(tonight_ok_key(day))
    return {"date": day, "answered": True}


__all__ = ["TONIGHT_ASK_HOUR", "TONIGHT_OPTION_LIMIT", "tonight_check", "tonight_keep", "tonight_ok_key"]
