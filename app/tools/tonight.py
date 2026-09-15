"""
Tonight, mid-afternoon: "Still good?" — the Now tab's top card (Loop Board
"Tonight's dinner, mid-day: walk me to sorting it out instead of the
day-editor workaround", Emily's decisions of 2026-09-13).

The anticipate phase of the mental-load model (SKILL.md): it is the
afternoon, dinner is hours away, and the app should be the one to notice
and ask — not the person, and not by way of the day sheet. Three answers:

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
- **Not tonight — we're going out** (Loop Board, Emily 2026-09-14: "a night
  off doesn't mean fighting the app into swapping for a dish I'm not going
  to cook either"). One answer, no follow-up question. tonight_night_off
  moves the dish to the next free night of this plan when there is one and
  drops it when there isn't, and either way leaves tonight `planned_empty`
  — a night that needs no decision, so nothing anywhere reads it as missed,
  skipped or overdue. Whatever was already bought for a dropped dish and
  won't keep is flagged "use soon" rather than silently going off in the
  fridge, and the night itself counts toward the intake's learned takeout
  hint, so next week's plan gets lighter on that day without anyone saying
  so.

Nothing here calls the model. Favourites are deliberately not in this card
(Emily, 2026-09-13), and neither was chat — until the third answer, which
is a thing people say rather than tap, so it alone is also an agent tool
(take_the_night_off).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import get_conn
from ._shared import household_id
from . import notifications as _notifications
from . import week_intake as _week_intake
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

# What a called-off night leaves on the plan, on the planned_empty row's
# derived_from. One string, three readers: this card (so Now can say
# "Night off" instead of re-asking), week_intake's learned takeout hint,
# and anything later that needs to tell a night the household called off
# from a night they were never home for.
NIGHT_OFF_CONSTRAINT = "night_off"

# The sentence that stands in for the meal wherever the plan is drawn.
# A statement, never an ask — plan_slot_empty's own rule.
NIGHT_OFF_REASON = "Night off — nothing planned for this one."

# attention_items kind for "this was bought for a dinner that came off the
# plan, and it won't keep". The queue is the app's existing place for
# something the household should look at rather than have guessed at for
# them (add_attention_item), so it reaches Cook's fold and the morning text
# with no new surface of its own.
USE_SOON_KIND = "use_soon"


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


def _plan_covering(conn, day: str):
    """
    The plan whose period holds the household's LOCAL `day` — the same
    period arithmetic _current_weekly_plan_row uses, minus its fall-back to
    "the latest plan" (a plan that doesn't cover tonight has nothing to say
    about it) and with the household's date rather than the server's, which
    is already tomorrow in UTC by eight in the evening in Toronto.

    Shared by the card and the night-off answer on purpose: the two have to
    agree about which plan tonight belongs to, or the sheet offers nights
    off a plan the write then can't find.
    """
    return conn.execute(
        f"SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' "
        f"AND date({_weekly_plan._SQL_PERIOD_START}) <= date(?) "
        f"AND date({_weekly_plan._SQL_PERIOD_START}, '+' || {_weekly_plan._SQL_PERIOD_LAST_OFFSET} || ' days') >= date(?) "
        f"ORDER BY {_weekly_plan._SQL_APPROVED_FIRST}, created_at DESC, id DESC LIMIT 1",
        (household_id(), day, day),
    ).fetchone()


def _dinner_rows(conn, plan_id: int):
    """Every night's dinner on one plan, earliest first."""
    return conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?
          AND mpe.slot = 'dinner' AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (plan_id, household_id()),
    ).fetchall()


def _night_off_row(row) -> bool:
    """Is this dinner row a night the household called off (as against one
    they were simply never home for)? The marker, not the wording."""
    if row is None or (row["slot_state"] or "") != "planned_empty":
        return False
    try:
        derived = json.loads(row["derived_from_json"] or "{}")
    except (TypeError, ValueError):
        return False
    return derived.get("constraint") == NIGHT_OFF_CONSTRAINT


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
    'unplanned', 'away', 'open', 'cooked', 'answered', 'night_off' — so a
    screen (or a test) can tell a quiet card from a broken one. 'night_off'
    is the one quiet reason the screen still draws something for: `night_off`
    is True, `use_soon` lists anything already bought for the dropped dish
    that won't keep, and the card states the night rather than asking again. A day with no dinner
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
        # Set only by a night the household called off (tonight_night_off):
        # whether tonight is one, and anything already bought for a dropped
        # dish that won't keep.
        "night_off": False, "use_soon": [],
        # What "Not tonight — we're going out" would do with tonight's
        # dish: the night it would move to, or None for "it comes off the
        # week". Answered here so the row can say it before it is tapped
        # (§8 rule 7: a control whose effect you have to guess has failed),
        # and by the same dry run the answer itself will use.
        "night_off_moves_to": None, "night_off_moves_to_weekday": None,
    }

    conn = get_conn()
    try:
        plan = _plan_covering(conn, today)
        if plan is None:
            out["reason"] = "no_plan"
            return out
        out["week_start"] = plan["week_start_date"]
        if plan["planning_mode"] == "component_based":
            out["reason"] = "components"
            return out

        rows = _dinner_rows(conn, plan["id"])
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
        # A night the household called off says so, so Now can state it
        # ("Night off — enjoy") instead of going quiet and leaving the tap
        # looking like it did nothing. A night they were simply never home
        # for stays 'away', which the card has always been silent about.
        if _night_off_row(tonight_row):
            out["reason"] = "night_off"
            out["night_off"] = True
            out["use_soon"] = _row_use_soon(tonight_row)
            return out
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

    free = _next_free_night(plan, rows, today)
    if free:
        out["night_off_moves_to"] = free
        out["night_off_moves_to_weekday"] = _weekly_plan._weekday_of(free)

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


# ---------- "Not tonight — we're going out" ----------


def _row_use_soon(row) -> list[str]:
    """What a called-off night wrote down as "already bought, won't keep",
    read back off its own planned_empty row. Recorded rather than recomputed:
    the links it was derived from are gone by the time anything reads it."""
    try:
        derived = json.loads(row["derived_from_json"] or "{}")
    except (TypeError, ValueError):
        return []
    items = derived.get("use_soon") or []
    return [str(i) for i in items if str(i).strip()]


def _fresh_bought_for(entry_id: int) -> list[str]:
    """
    What this meal put on the list that the household has ALREADY got — in
    a cart or through the till — and that won't keep.

    Those are exactly the lines _reverse_meal_grocery_contributions leaves
    alone (the shopper has acted on them), so dropping the dinner takes the
    reason for them off the plan and leaves the food in the house with
    nothing pointing at it. "Won't keep" is big_meal.keeps, the same
    reading of a grocery line the holiday shop split already makes — one
    answer to "is this fresh?", not a second table that could disagree.

    A thing already TAKEN OUT OF THE FREEZER for this dinner counts too, and
    it is the one case the list can't see: a defrost row ticked done means
    the food is thawing on a shelf for a meal nobody is now cooking, and it
    may well have been bought weeks ago. Read off the row's own
    inventory_item_id rather than out of its sentence — defrost writes the
    prose, and parsing it back would be reading the app's own words as data.

    Read BEFORE the reversal clears the ledger and before the prep rows go
    with the entry, and never after.
    """
    from . import big_meal as _big_meal

    conn = get_conn()
    try:
        thawed = conn.execute(
            """
            SELECT i.item
            FROM prep_tasks t JOIN inventory_items i ON i.id = t.inventory_item_id
            WHERE t.household_id = ? AND t.meal_plan_entry_id = ?
              AND t.task_type = 'defrost' AND t.status = 'done'
            ORDER BY t.id ASC
            """,
            (household_id(), entry_id),
        ).fetchall()
        rows = conn.execute(
            """
            SELECT gi.item, gi.category, gi.status
            FROM meal_plan_grocery_links l
            JOIN grocery_items gi ON gi.id = l.grocery_item_id
            WHERE l.household_id = ? AND l.meal_plan_entry_id = ?
            ORDER BY l.id ASC
            """,
            (household_id(), entry_id),
        ).fetchall()
    finally:
        conn.close()
    seen: set[str] = set()
    out: list[str] = []
    for r in thawed:
        item = (r["item"] or "").strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    for r in rows:
        if r["status"] not in ("in_cart", "purchased"):
            continue
        # A household line (foil, dish soap) is not food, and big_meal.keeps
        # reads that section as fresh — its own table lists the sections
        # that keep and "household" isn't one, because a holiday MENU's
        # ingredients never land there. Asking it about foil is asking it a
        # question it wasn't written for, so this drops those first rather
        # than widening a table another feature reads.
        if (r["category"] or "").strip().lower() == "household":
            continue
        item = (r["item"] or "").strip()
        key = item.lower()
        if not item or key in seen:
            continue
        try:
            if _big_meal.keeps(item, r["category"]):
                continue
        except Exception:
            logger.exception("Could not tell whether %s keeps; leaving it off the use-soon note", item)
            continue
        seen.add(key)
        out.append(item)
    return out


def _next_free_night(plan, rows, today: str) -> str | None:
    """
    The first night after tonight, inside this plan's period, that tonight's
    dish could move onto without displacing a dinner.

    Free means the plan is not feeding anyone that night: its dinner slot is
    `open` (a decision already handed back) or has no dinner row at all. A
    `planned_empty` night is NOT free — nobody is home for it, so moving
    dinner there would cook for an empty table — and neither is a night with
    a real dish, which is what the "Something else" swap is for.

    Each candidate is proven by the swap's own dry run rather than by a
    second copy of its rules, exactly as the sheet's options are: a leftover
    chain that would end up running backwards simply isn't offered.
    """
    start, day_count = _weekly_plan.plan_period(plan)
    if day_count < 1:
        return None
    period = _week_intake.period_dates(start, day_count)
    taken = {r["date"]: r for r in rows}
    for d in period:
        if d <= today:
            continue
        row = taken.get(d)
        if row is not None and (row["slot_state"] or "planned") != "open":
            continue
        try:
            probe = _weekly_plan._apply_dinner_nights_swap(
                plan["id"], today, d, undo=False, dry_run=True,
            )
        except ValueError:
            continue
        except Exception:
            logger.exception("Dry-run move of tonight's dinner onto %s failed", d)
            continue
        if probe.get("status") == "ok":
            return d
    return None


def _settle_night_off(plan_id: int, today: str, use_soon: list[str]) -> None:
    """
    Leave tonight `planned_empty`, whatever is sitting on it now.

    `planned_empty` and not `open`, deliberately, and this is the whole
    point of the answer: an open slot is a decision handed back, so Now
    would turn straight round and ask "Tonight needs a dinner" — the
    question the household has just said no to. planned_empty needs no
    decision and must never be offered as one, so nothing anywhere reads
    the night as missed, skipped or overdue. It is the same pair of writes
    slot_needs.set_slot_need makes when a night goes 'away', in the same
    order and for the same reason — clear whatever is there (reversing its
    grocery contribution, leaving anything already in a cart or through the
    till alone), then state the empty night.
    """
    _weekly_plan.clear_plan_slot(plan_id, today, "dinner")
    _weekly_plan.plan_slot_empty(
        plan_id, today, "dinner", reason=NIGHT_OFF_REASON,
        derived_from={"constraint": NIGHT_OFF_CONSTRAINT, "use_soon": use_soon},
    )


def tonight_night_off(day: str | None = None, now: datetime | None = None) -> dict:
    """
    "Not tonight — we're going out." Settles tonight in one answer, with no
    second question: takeout, leftovers, cereal, out — the app doesn't ask
    which, because none of them changes what it has to do.

    What happens to the dish:
      - it MOVES to the next free night of this plan when there is one
        (_next_free_night, through the same swap_dinner_nights the sheet's
        other rows use — so its groceries, its cooked tick, its leftover
        chain and its defrost reminders all travel with it and the shopping
        list is not touched);
      - otherwise it is DROPPED, which reverses whatever it put on the list
        that is still waiting to be bought and leaves alone anything already
        in a cart or through the till. What was bought and won't keep comes
        back as `use_soon`, and is queued on the attention list so it
        surfaces past this one card.
    Either way tonight ends `planned_empty` — see _settle_night_off.

    Moving is the default when a free night exists because it keeps both
    the food and the plan. **To make it always drop instead, pass None for
    `free` below** — one line.

    Refusals are answers, not errors: `status` 'refused' with a `message`
    written for the household, and nothing written. A dinner already cooked,
    and a dinner cooked double for a later night with nowhere to move to (a
    drop would leave that night holding a reheat with no batch behind it —
    drop_dish_from_day refuses the same thing for the same reason).
    """
    now = now or _household_now()
    today = day or now.date().isoformat()
    try:
        date.fromisoformat(today)
    except (TypeError, ValueError):
        raise ValueError("That isn't a date I recognise.")

    conn = get_conn()
    try:
        plan = _plan_covering(conn, today)
        if plan is None:
            return {
                "status": "refused", "date": today,
                "message": "There's no plan covering tonight, so there's nothing to call off.",
            }
        if plan["planning_mode"] == "component_based":
            return {
                "status": "refused", "date": today,
                "message": "That plan is built from components, not nights.",
            }
        plan_id = plan["id"]
        week_start = plan["week_start_date"]
        rows = _dinner_rows(conn, plan_id)
    finally:
        conn.close()

    tonight_row = next((r for r in rows if r["date"] == today), None)
    state = (tonight_row["slot_state"] or "planned") if tonight_row is not None else None

    out = {
        "status": "night_off", "date": today, "week_start": week_start,
        "dish": None, "moved_to": None, "moved_to_weekday": None,
        "use_soon": [], "already": False,
    }

    if state == "planned_empty":
        # Already settled — a second tap, or the other phone. Say so and
        # write nothing rather than tearing the row down and building it
        # back, which would lose the use-soon note with it.
        out["already"] = True
        out["use_soon"] = _row_use_soon(tonight_row)
        return out

    if tonight_row is not None and (tonight_row["cooked_status"] or "") == "done":
        return {
            "status": "refused", "date": today,
            "message": (
                f"{_dish_name(tonight_row) or 'That one'} is already ticked off as cooked — "
                "I’ll leave tonight as it is."
            ),
        }

    dish = _dish_name(tonight_row) if tonight_row is not None else ""
    if tonight_row is None or state != "planned" or not dish:
        # Nothing to keep: an open dinner, or a night the plan never filled.
        # Saying "we're going out" still settles it, which is the point —
        # otherwise Now goes on asking "Tonight needs a dinner".
        _settle_night_off(plan_id, today, [])
        return out

    out["dish"] = dish
    free = _next_free_night(plan, rows, today)
    if free is not None:
        moved = _weekly_plan.swap_dinner_nights(plan_id, today, free)
        if moved.get("status") == "refused":
            # The dry run said this night was fine, so this is a race — the
            # week changed under the tap. The server's own sentence, and
            # nothing written.
            return {"status": "refused", "date": today, "message": moved.get("message") or ""}
        _settle_night_off(plan_id, today, [])
        out["moved_to"] = free
        out["moved_to_weekday"] = _weekly_plan._weekday_of(free)
        return out

    # Nothing to move it to — the dish comes off the week.
    fed = _chain_targets(tonight_row)
    if fed:
        return {
            "status": "refused", "date": today,
            "message": (
                f"{dish} also feeds {_weekly_plan._join_with_and(fed)} — change that "
                "first and I’ll take tonight off."
            ),
        }
    use_soon = _fresh_bought_for(tonight_row["id"])
    _settle_night_off(plan_id, today, use_soon)
    out["use_soon"] = use_soon
    if use_soon:
        _queue_use_soon(dish, use_soon)
    return out


def _chain_targets(row) -> list[str]:
    """The nights this dinner was cooked double for, said as weekdays —
    drop_dish_from_day's own reading of make_double_for, including its
    tolerance of the pre-fix scalar shape."""
    try:
        fed = json.loads(row["derived_from_json"] or "{}").get("make_double_for") or []
    except (TypeError, ValueError):
        return []
    if isinstance(fed, str):
        fed = [fed]
    nights = []
    for target in fed:
        part = str(target).split(":")
        if not part[0].strip():
            continue
        try:
            weekday = date.fromisoformat(part[0]).strftime("%A")
        except ValueError:
            continue
        nights.append(f"{weekday}’s {part[1]}" if len(part) > 1 else weekday)
    return nights


def _queue_use_soon(dish: str, items: list[str]) -> None:
    """
    Put "use these soon" on the attention queue — the app's existing place
    for something the household should look at rather than have guessed at
    for them. It reaches Cook's fold and the morning text from there, so
    the note outlives the one card on Now that first said it.

    Never allowed to fail the answer: the night is already off, and a
    reminder that didn't queue must not report the night as unsettled.
    """
    from . import attention as _attention

    said = _weekly_plan._join_with_and(items)
    try:
        _attention.add_attention_item(
            USE_SOON_KIND,
            f"Use the {said} soon — {dish} came off the plan.",
            {"items": items, "dish": dish},
        )
    except Exception:
        logger.exception("Could not queue the use-soon note for %s", dish)


__all__ = [
    "TONIGHT_ASK_HOUR", "TONIGHT_OPTION_LIMIT", "NIGHT_OFF_CONSTRAINT",
    "NIGHT_OFF_REASON", "USE_SOON_KIND",
    "tonight_check", "tonight_keep", "tonight_night_off", "tonight_ok_key",
]
