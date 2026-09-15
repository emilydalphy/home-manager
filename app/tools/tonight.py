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
        "answered": bool,       # "Yes" already given today — read before
                                #   the plan, so it is reported whatever
                                #   the plan lookup finds (2026-09-15)
        "week_start": str|None, # the plan's week_start_date, for the swap route
        "dinner": {"entry_id", "meal", "is_leftovers", "leftovers_from"} | None,
        "options": [{"entry_id", "date", "weekday", "meal", "is_leftovers",
                     "leftovers_from"}],   # up to TONIGHT_OPTION_LIMIT
      }

    `ask` is True only when every one of these holds: it is the afternoon;
    a plan covers today and tonight's dinner on it is a real, planned,
    not-yet-cooked dish; and nobody has said "Yes" today. `answered` is
    NOT one of those conditions read backwards: it is a fact about the
    day on its own, true whenever a Yes is on file, and it is reported
    alongside every `reason` rather than only the ones that get far
    enough to look. `reason` names the first condition that fails —
    'morning', 'no_plan', 'components', 'unplanned', 'away', 'open',
    'cooked', 'answered', 'night_off' — so a screen (or a test) can tell a
    quiet card from a broken one. 'night_off' is the one quiet reason the
    screen still draws something for: `night_off` is True, `use_soon`
    lists anything already bought for the dropped dish that won't keep,
    and the card states the night rather than asking again.
    `night_off_moves_to` / `night_off_blocked_message` are the "Not tonight
    — we're going out" row's own preview: where that answer would put
    tonight's dish, or the sentence it would refuse with — both from the
    same functions tonight_night_off uses, never a second reading. A day with no dinner
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
        # … and when the answer would be REFUSED, the sentence it would be
        # refused with, so the row states that instead of a promise it
        # cannot keep. The preview used to run without this check, so a
        # dinner cooked double for a later night read "comes off the week"
        # and then refused on the tap — §8 rule 7 inverted, not served.
        "night_off_blocked": False, "night_off_blocked_message": None,
    }

    conn = get_conn()
    try:
        # The answer is read FIRST, and that ordering is the fix for a
        # real bug (Loop Board, 2026-09-14): this used to be read after
        # the plan, so the `no_plan` and `components` returns below gave
        # up before ever looking, and a Yes already on file came back as
        # `answered: False`. The record is keyed by the household and the
        # day and nothing else — it does not depend on a plan existing —
        # so whether somebody has answered is knowable here whatever the
        # plan lookup goes on to find, and reporting it is simply telling
        # the truth. `ask` is False down every one of those paths anyway,
        # so no card starts appearing that didn't before; what stops is
        # the app forgetting an answer it has.
        #
        # `no_plan` is reachable in ordinary use, not just in theory: it
        # resolves the plan against the HOUSEHOLD's date while other
        # things still resolve it against the server's, and the deployed
        # container is UTC. For the hours when Toronto has rolled over
        # and the server has not, the two land in different weeks.
        answered = conn.execute(
            "SELECT 1 FROM notification_dismissals WHERE household_id = ? AND key = ?",
            (household_id(), tonight_ok_key(today)),
        ).fetchone() is not None
        out["answered"] = answered

        plan = _plan_covering(conn, today)
        if plan is None:
            out["reason"] = "no_plan"
            return out
        out["week_start"] = plan["week_start_date"]
        if plan["planning_mode"] == "component_based":
            out["reason"] = "components"
            return out

        rows = _dinner_rows(conn, plan["id"])
    finally:
        conn.close()

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
    else:
        # Exactly the check the answer itself makes, in the same words —
        # one function, so the card and the write cannot disagree about
        # what the tap will do. Only reachable with nowhere to move the
        # dish to: a chain the swap could keep intact IS a free night.
        blocked = _chain_refusal(tonight_row)
        if blocked:
            out["night_off_blocked"] = True
            out["night_off_blocked_message"] = blocked

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


def _fresh_bought_for(entry_id: int, conn=None) -> list[str]:
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

    A line ANOTHER meal still on the plan also holds a link to is left out.
    Two dinners can share one purchased bag of spinach; calling tonight off
    does not free it, and "use the spinach soon" would have the household
    eat Friday's dinner out of the fridge on the app's own instruction. The
    test is a live link held by a DIFFERENT entry — whatever state that
    entry is in, because the safe direction here is to say less.

    Read BEFORE the reversal clears the ledger and before the prep rows go
    with the entry, and never after. `conn` is the caller's transaction
    (see tonight_night_off): this only reads, and it has to read what that
    transaction can see.
    """
    from . import big_meal as _big_meal

    own_conn = conn is None
    if own_conn:
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
              AND NOT EXISTS (
                SELECT 1 FROM meal_plan_grocery_links o
                JOIN meal_plan_entries e ON e.id = o.meal_plan_entry_id
                WHERE o.household_id = l.household_id
                  AND o.grocery_item_id = l.grocery_item_id
                  AND o.meal_plan_entry_id != l.meal_plan_entry_id
              )
            ORDER BY l.id ASC
            """,
            (household_id(), entry_id),
        ).fetchall()
    finally:
        if own_conn:
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


def _next_free_night(plan, rows, today: str, conn=None) -> str | None:
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

    `conn` is the caller's open transaction. It matters: a dry run left to
    open its own connection would block on that transaction's write lock,
    and the answer has to be computed against the world the transaction has
    already locked, not the one it started in.
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
                plan["id"], today, d, undo=False, dry_run=True, conn=conn,
            )
        except ValueError:
            continue
        except Exception:
            logger.exception("Dry-run move of tonight's dinner onto %s failed", d)
            continue
        if probe.get("status") == "ok":
            return d
    return None


def _settle_night_off(plan_id: int, today: str, use_soon: list[str], conn) -> None:
    """
    Leave tonight `planned_empty`, whatever is sitting on it now, on the
    caller's already-open transaction.

    `planned_empty` and not `open`, deliberately, and this is the whole
    point of the answer: an open slot is a decision handed back, so Now
    would turn straight round and ask "Tonight needs a dinner" — the
    question the household has just said no to. planned_empty needs no
    decision and must never be offered as one, so nothing anywhere reads
    the night as missed, skipped or overdue.

    It is the same pair of writes slot_needs.set_slot_need makes when a
    night goes 'away' — clear whatever is there (reversing its grocery
    contribution, leaving anything already in a cart or through the till
    alone), then state the empty night. The difference, and it is the whole
    reason both of those functions grew a `conn` for this: that pair is TWO
    COMMITS there, and the gap between them is a genuinely ABSENT slot —
    the one state schema.sql, audit_plan_slots and plan_slot_open's own
    docstring all say cannot exist. A failure in the gap left the day with
    no dinner row at all, its groceries already reversed, under a screen
    reading "That didn't work — the plan is as it was", which was false.
    Here the two are one write: either the meal is gone and the empty night
    stands in its place, or nothing moved.
    """
    _weekly_plan.clear_plan_slot(plan_id, today, "dinner", conn=conn)
    _weekly_plan.plan_slot_empty(
        plan_id, today, "dinner", reason=NIGHT_OFF_REASON,
        derived_from={"constraint": NIGHT_OFF_CONSTRAINT, "use_soon": use_soon},
        conn=conn,
    )


def _chain_refusal(row) -> str | None:
    """
    The sentence a night off is refused with when tonight's dinner was
    cooked double for a later night — or None when it feeds nobody.

    ONE function, because the card has to say it before the tap and the
    write has to say it on the tap, and those two disagreeing is exactly
    the defect this exists to close: the sheet promised "Bean Chili comes
    off the week" over a dish it would then refuse to take off. Reading the
    chain is weekly_plan.chain_fed_nights, shared with the Review
    stepper's own refusal, so the two cannot name different nights.
    """
    dish = _dish_name(row) or "Tonight’s dinner"
    try:
        fed = _weekly_plan.chain_fed_nights(row["derived_from_json"])
    except ValueError:
        # A token nobody can read is still a night being fed. Refuse
        # without naming one rather than raise — this is an answer.
        return (
            f"{dish} also feeds a later night — change that first and I’ll "
            "take tonight off."
        )
    if not fed:
        return None
    return (
        f"{dish} also feeds {_weekly_plan._join_with_and(fed)} — change that "
        "first and I’ll take tonight off."
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
    drop_dish_from_day refuses the same thing for the same reason). The
    card runs that same check before the tap (_chain_refusal, read by both)
    so the row never promises something this would then decline.

    `already` means the night was ALREADY empty and nothing was written —
    a second tap, the other phone, or a night nobody was ever home for.
    `already_reason` says which, because "that's already a night off" about
    a trip is the app telling the household something untrue.

    ONE transaction, and the write lock is taken before the first read. See
    the comment on the connection below: without it two taps at once each
    computed the free night against the same pre-tap week, and the second
    swap put the dish back onto tonight for the second clear to delete.
    """
    now = now or _household_now()
    today = day or now.date().isoformat()
    try:
        date.fromisoformat(today)
    except (TypeError, ValueError):
        raise ValueError("That isn't a date I recognise.")

    out = {
        "status": "night_off", "date": today, "week_start": None,
        "dish": None, "moved_to": None, "moved_to_weekday": None,
        "use_soon": [], "already": False, "already_reason": None,
    }
    use_soon: list[str] = []
    dish = ""

    # ONE transaction, and the lock is taken before the first read — the
    # shape _replace_slot_entries and _apply_dinner_nights_swap both use,
    # and for the reason this function learned the hard way. Two phones (or
    # a phone and a chat turn, or one retried POST) used to compute "the
    # next free night" against the same pre-tap week, and the second swap
    # put the dish straight back onto tonight for the second clear to
    # delete: the dish gone from the week, the free night empty, the
    # grocery line reversed, and BOTH callers told "it moves to Thursday".
    # A wider window left two planned_empty rows on one slot, which is
    # audit_plan_slots' `duplicated` — "how a night nobody is home ends up
    # with groceries bought for it". Holding the lock from the first read
    # is what makes the loser see the world the winner left: an already
    # planned_empty tonight, which is `already`, or a swap the swap's own
    # rules refuse.
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        plan = _plan_covering(conn, today)
        if plan is None:
            conn.rollback()
            return {
                "status": "refused", "date": today,
                "message": "There's no plan covering tonight, so there's nothing to call off.",
            }
        if plan["planning_mode"] == "component_based":
            conn.rollback()
            return {
                "status": "refused", "date": today,
                "message": "That plan is built from components, not nights.",
            }
        plan_id = plan["id"]
        out["week_start"] = plan["week_start_date"]
        rows = _dinner_rows(conn, plan_id)
        tonight_row = next((r for r in rows if r["date"] == today), None)
        state = (tonight_row["slot_state"] or "planned") if tonight_row is not None else None

        if state == "planned_empty":
            # Already settled — a second tap, the other phone, or a night
            # nobody was ever home for. Nothing is written; saying which of
            # those it is keeps the assistant from calling a trip a night
            # off.
            conn.rollback()
            out["already"] = True
            out["already_reason"] = (
                NIGHT_OFF_CONSTRAINT if _night_off_row(tonight_row) else "away"
            )
            out["use_soon"] = _row_use_soon(tonight_row)
            return out

        if tonight_row is not None and (tonight_row["cooked_status"] or "") == "done":
            conn.rollback()
            return {
                "status": "refused", "date": today,
                "message": (
                    f"{_dish_name(tonight_row) or 'That one'} is already ticked off as cooked — "
                    "I’ll leave tonight as it is."
                ),
            }

        dish = _dish_name(tonight_row) if tonight_row is not None else ""
        if tonight_row is None or state != "planned" or not dish:
            # Nothing to keep: an open dinner, or a night the plan never
            # filled. Saying "we're going out" still settles it, which is
            # the point — otherwise Now goes on asking "Tonight needs a
            # dinner".
            _settle_night_off(plan_id, today, [], conn)
            conn.commit()
            return out

        out["dish"] = dish
        free = _next_free_night(plan, rows, today, conn=conn)
        if free is not None:
            moved = _weekly_plan._apply_dinner_nights_swap(
                plan_id, today, free, undo=False, conn=conn,
            )
            if moved.get("status") == "refused":
                # Under the lock the swap's own rules are the last word —
                # the dry run that chose this night ran inside this very
                # transaction, so this is a night that changed between the
                # household opening the sheet and tapping it.
                conn.rollback()
                return {"status": "refused", "date": today, "message": moved.get("message") or ""}
            _settle_night_off(plan_id, today, [], conn)
            out["moved_to"] = free
            out["moved_to_weekday"] = _weekly_plan._weekday_of(free)
            conn.commit()
            return out

        # Nothing to move it to — the dish comes off the week.
        blocked = _chain_refusal(tonight_row)
        if blocked:
            conn.rollback()
            return {"status": "refused", "date": today, "message": blocked}
        use_soon = _fresh_bought_for(tonight_row["id"], conn=conn)
        _settle_night_off(plan_id, today, use_soon, conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # After the commit, and only then: the attention queue opens its own
    # connection, and a note that failed to queue must never report the
    # night as unsettled — it is already off.
    out["use_soon"] = use_soon
    if use_soon:
        _queue_use_soon(dish, use_soon)
    return out


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
