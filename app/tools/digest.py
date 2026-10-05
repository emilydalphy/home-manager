"""
The morning text — "Reach me before the moment" (Loop Board, 2026-09-11).

Pomona's whole job is to take the noticing off the person who usually does
it, and until now its bell only rang when someone opened the app: the person
carrying the load was still the one who had to remember to look. This
module carries anticipation OUT of the app — one short text each morning,
at a time the household picks, saying what today needs.

Three pieces, in the order the loop uses them:

- ``build_morning_text``: one digest builder, reading ``today_moves`` and
  the live notification feed exactly as the Today screen does. A text is a
  line, not a page — one or two SMS segments (``MAX_TEXT_CHARS``), plain
  language, and nothing the Today screen wouldn't also show. Nothing to
  say means no text at all, not a text saying so.
- ``send_digest``: the channel seam. Text goes through Twilio's REST API
  with the stdlib (no new dependency); email is a documented fallback
  with no code behind it yet. Missing keys make the sender a no-op that
  records why, never a crash.
- ``run_morning_texts_once``: one pass over every household — the daily
  in-process loop in app/main.py calls it every few minutes. Every send
  and every skip is a row in ``morning_text_sends`` keyed by the
  household's LOCAL date, which is what makes a container restart safe
  (it checks before it sends) and a missed window catch up the same day.

Household time (``households.timezone``, ``households.morning_text_time``)
is the one new piece of household state. The repo had no per-household
zone before this (meal_plans.py:189 notes the gap); the default for every
existing household is Toronto, and that is an assumption, not a fact.
Nothing else reads it yet — this is deliberately not the pass that fixes
every ``date.today()``.

Numbers are keyed by member row (``members.phone``,
``members.morning_text_on``), read for adults only, so the per-adult login
being built on another branch composes with this rather than replacing it.

The EVENING NUDGE (Loop Board, 2026-09-21) rides the same path: one line at
the start of the household's dinner window — "Tonight: lemon chicken &
orzo — 35 min. Tap to start." — to the same numbers, unless the person
switched it off (``members.evening_nudge_on``). ``build_evening_nudge``
reads tonight off today_moves exactly as the morning text does;
``run_evening_nudges_once`` is the pass the same in-process loop makes;
``members.evening_nudge_sent_on`` (the household's local date) is what
keeps a restart at 18:05 from sending twice.

PUSH (2026-09-27): an adult with the iPhone app who allowed notifications
gets both on the lock screen instead (app/push.py, "push, ahead of text"
below); text stays the fallback. With the APNS_* variables unset, push is
off and everything here is text exactly as before.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import get_conn
from ._shared import PUBLIC_BASE_URL, household_id, use_household
from . import attendance as _attendance
from . import attention as _attention
from . import cooker as _cooker
from . import moves as _moves
from . import notifications as _notifications
from . import rhythm as _rhythm
from . import slot_needs as _slot_needs

logger = logging.getLogger("home_manager")

DEFAULT_TIMEZONE = "America/Toronto"
DEFAULT_SEND_TIME = "07:00"

# Two SMS segments of GSM-7 is 306 characters; a little under that so a
# stray accented character (which forces the costlier UCS-2 encoding at 67
# per segment) still lands in a small number of parts. Assumption, stated
# on the card: a text is a line, not a page.
MAX_TEXT_CHARS = 300

# How late the loop will still send a missed morning. The app was down at
# seven and came back at ten: still worth a "tonight: tacos". Came back at
# nine at night: not a morning text any more, and a row records that it was
# skipped rather than leaving the day blank. Assumption, stated on the card.
LATE_WINDOW_HOURS = 8

# How often the in-process loop looks. Minutes, not seconds — the loop
# reads every household's plan on every pass.
POLL_SECONDS = 5 * 60

TWILIO_ENV = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")

_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", re.I)


# ---------- what the message includes (Loop Board, 2026-10-04) ----------
#
# "For the notifications, we want to have the notification customize what
# kind of details to include" — Gowthami's household, 2026-10-04. Six parts
# the household ticks; the message is those parts only, in this order.
#
# ONE setting for the whole household, not one per member (Emily,
# 2026-10-04: one setting "to keep it simple"). Everyone who gets the
# message gets the same parts, which is why it lives on `households` beside
# the hour and the zone rather than on `members` beside the number.
#
# EVERY SENTENCE A PERSON READS ABOUT THIS IS IN THIS ONE BLOCK, because
# the card asks for exactly that: "Each part's sentence is a placeholder:
# keep the strings in one place so they're easy to change, because Emily
# will review the wording." The checkbox labels, the line under each of
# them and the one line this file composes itself (MORNING_START_LINE) are
# all here. Every other part's sentence is the move's or the feed's own and
# is still said where the fact is known, which is where it has always been
# said — copying them here would be two wordings for one line.

# The order of the message, which is the card's own order for the boxes.
# `kitchen` ("Food to use up": the attention queue's one item and the
# use-it-up nudge) was an always-on part with no box until Emily's call of
# 2026-10-05 made it the seventh box, on by default. Nothing is always in
# any more: unticking all seven means no message, ever.
MORNING_PART_CHOICES = ("meals", "freezer", "prep", "start", "shop", "away", "kitchen")

# Parts nobody is offered a box for, and which are therefore always in.
# Empty since 2026-10-05 (see above); kept as the seam rather than deleted.
MORNING_PARTS_ALWAYS: tuple = ()

# Everything, in the order it is composed.
MORNING_PARTS = MORNING_PART_CHOICES + MORNING_PARTS_ALWAYS

# What a household that has never been asked gets. Emily, 2026-10-05:
# Shopping and Food to use up are ON by default, so nobody on the defaults
# loses a line the morning text already carried (the timed shop line, the
# use-it-up nudge). Change this tuple and every unanswered household
# changes with it.
MORNING_PART_DEFAULTS = ("meals", "freezer", "prep", "shop", "kitchen")

# The screen's words. `label` is the checkbox; `says` is the line under it
# saying what that part puts in the message. PLACEHOLDERS, all of them.
MORNING_PART_WORDS = {
    "meals": {"label": "Today's meals", "says": "What you're eating today."},
    "freezer": {"label": "What to take out of the freezer", "says": "Anything to move to the fridge."},
    "prep": {"label": "Prep to do today", "says": "Anything to get ready ahead."},
    "start": {"label": "When to start cooking dinner", "says": "The time tonight's cook has to begin."},
    "shop": {"label": "Shopping", "says": "Your shopping day, or a shop tonight's cook is waiting on."},
    "away": {"label": "Who's away tonight", "says": "Who's at the table and who isn't."},
    "kitchen": {"label": "Food to use up", "says": "Anything to use before it goes off."},
}

# The sentences this module composes itself rather than reading off a
# move or the feed. PLACEHOLDERS, like the rest of this block.
#
# The start line is a line of its own and never a clause on "Tonight:" —
# they are two separate boxes, so each has to be able to stand without the
# other, and "Start Chicken Skewers by 5:45" would name the dish the
# household had just unticked.
MORNING_START_LINE = "Start cooking at {clock}."

# Shopping on the household's own shop day (rhythm `shop_day` /
# `top_up_shop_day`, the same is_shop_day Today's Shop section reads). One
# line even when a cook is also waiting on the shop: `{by}` is then
# MORNING_SHOP_DAY_BY with that cook's deadline, else ''.
MORNING_SHOP_DAY_LINE = "You shop today{by}. {things}"
MORNING_SHOP_DAY_BY = ", {deadline}"
MORNING_SHOP_THINGS_NONE = "Nothing on the list yet."
MORNING_SHOP_THINGS_ONE = "1 thing on the list."
MORNING_SHOP_THINGS_MANY = "{count} things on the list."


def _read_parts(raw: str | None) -> list[str]:
    """
    The stored answer, read back as part keys in message order.

    '' is "nobody has answered" and reads as MORNING_PART_DEFAULTS; '[]' is
    "none of them", a real answer, and reads as none. A stored key this
    version does not know is dropped rather than carried, so renaming a
    part is a rename and not a migration. A blob nothing can read falls back
    to the defaults and says so in the log — the stance _zone already takes
    towards a timezone nobody can parse: a bad stored value must not cost
    everybody their morning.
    """
    text = (raw or "").strip()
    if not text:
        return list(MORNING_PART_DEFAULTS)
    try:
        stored = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("Morning text: %r is not a readable list of parts; using the defaults", text[:60])
        return list(MORNING_PART_DEFAULTS)
    if not isinstance(stored, list):
        logger.warning("Morning text: the stored parts are not a list; using the defaults")
        return list(MORNING_PART_DEFAULTS)
    return _known_parts(stored)


def _known_parts(parts) -> list[str]:
    """The keys this version knows, in message order. Anything else is
    dropped: a stored key from a renamed part, or a stale one a screen is
    still holding, must not stop the message being built."""
    chosen = {str(k).strip().lower() for k in (parts or [])}
    return [k for k in MORNING_PART_CHOICES if k in chosen]


def _checked_parts(parts) -> list[str]:
    """Known keys, in message order, REFUSING anything else in a sentence —
    the other side of _known_parts, which drops. A caller writing a key is
    saying a word; a caller reading one may be holding a stale screen."""
    chosen = {str(k).strip().lower() for k in (parts or [])}
    unknown = chosen - set(MORNING_PART_CHOICES)
    if unknown:
        known = ", ".join(MORNING_PART_CHOICES)
        raise ValueError(
            f"I don't know what {sorted(unknown)[0]!r} is — the morning message's parts are {known}."
        )
    return [k for k in MORNING_PART_CHOICES if k in chosen]


def _write_parts(parts) -> str:
    """The answer as stored: known keys only, in message order, as JSON. An
    empty list is stored as '[]' and never as '', or "none of them" would
    read back next time as the three defaults."""
    return json.dumps(_checked_parts(parts))


def morning_text_parts() -> list[str]:
    """
    Which of the seven this household's morning message includes, in message
    order. The always-on parts are NOT in here: this is the household's own
    answer, which is what the screen ticks. What the message is actually
    built from is _included_parts.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT morning_text_parts FROM households WHERE id = ?", (household_id(),)
        ).fetchone()
    finally:
        conn.close()
    return _read_parts(row["morning_text_parts"] if row else "")


def _included_parts(chosen) -> list[str]:
    """What the message is built from: the chosen subset plus the parts
    nobody is offered a choice about, in message order."""
    keep = {str(k).strip().lower() for k in chosen} | set(MORNING_PARTS_ALWAYS)
    return [k for k in MORNING_PARTS if k in keep]


def set_morning_text_parts(parts) -> list[str]:
    """
    Replace the household's answer, and hand it back as read. An unknown key
    is refused in a sentence rather than dropped quietly: a screen or a
    model sending one has a bug, and storing fewer parts than were asked for
    is how that bug survives being noticed.
    """
    stored = _write_parts(parts)
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE households SET morning_text_parts = ? WHERE id = ?", (stored, household_id())
        )
        conn.commit()
    finally:
        conn.close()
    return _read_parts(stored)


# ---------- settings: the clock, the hour, the numbers ----------

def normalise_phone(raw: str | None) -> str:
    """
    A mobile number the way Twilio wants it (E.164), or '' for "none".

    Ten digits are assumed to be North American (+1) — this household is in
    Toronto and so is the beta. Anything written with a leading + is taken
    as already international. Everything else is refused with a sentence a
    person can act on, rather than stored and texted into the void.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # A North American number's area code and exchange both start 2-9;
    # anything else would be stored, fail at Twilio every morning, and
    # never say why at the one moment the person could fix it.
    if len(digits) == 10 and digits[0] in "23456789" and digits[3] in "23456789":
        return "+1" + digits
    raise ValueError("That doesn't look like a mobile number — something like 416-555-0100 works.")


def normalise_time(raw: str | None) -> str:
    """'7', '7am', '7:30 am', '07:00', '19:00' -> 'HH:MM' (24h)."""
    m = _TIME_RE.match(raw or "")
    if not m:
        raise ValueError("I didn't catch the time — something like 7am or 07:30 works.")
    hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError("I didn't catch the time — something like 7am or 07:30 works.")
        hour = hour % 12 + (12 if suffix.startswith("p") else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("I didn't catch the time — something like 7am or 07:30 works.")
    return f"{hour:02d}:{minute:02d}"


def _zone(name: str | None) -> ZoneInfo:
    """The household's zone, or Toronto when the stored name is unusable —
    a bad name must never stop the morning for everyone else."""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def _household_row(conn) -> dict:
    row = conn.execute(
        "SELECT timezone, morning_text_time, morning_text_parts FROM households WHERE id = ?",
        (household_id(),),
    ).fetchone()
    return {
        "timezone": (row["timezone"] if row else None) or DEFAULT_TIMEZONE,
        "time": (row["morning_text_time"] if row else None) or DEFAULT_SEND_TIME,
        # Raw, not read: _read_parts tells '' (nobody asked) from '[]'
        # (none of them), and a `or` here would turn one into the other.
        "parts": (row["morning_text_parts"] if row else "") or "",
    }


def _adult_rows(conn) -> list:
    # LOWER() because onboarding writes "Adult" and older rows say "adult";
    # '' is a member from before age groups existed, read as an adult
    # rather than silently left off the list.
    return conn.execute(
        """
        SELECT id, name, phone, morning_text_on, evening_nudge_on, evening_nudge_sent_on
        FROM members
        WHERE household_id = ? AND LOWER(age_group) IN ('adult', '') AND TRIM(name) != ''
        ORDER BY id
        """,
        (household_id(),),
    ).fetchall()


def get_morning_text_settings() -> dict:
    """
    Who gets the morning text, at what hour, on which clock, and what it
    includes — the Preferences row and its sheet read exactly this.

    `parts` is the household's own answer and `part_choices` is the seven
    boxes to draw, each already carrying its label, its one-line
    description and whether it is ticked. The screen renders the section
    off part_choices rather than holding its own copy of the seven, so
    adding or rewording one is a change to MORNING_PART_WORDS and nothing
    else.
    """
    conn = get_conn()
    hh = _household_row(conn)
    adults = _adult_rows(conn)
    conn.close()
    chosen = _read_parts(hh["parts"])
    return {
        "time": hh["time"],
        "timezone": hh["timezone"],
        "parts": chosen,
        "part_choices": [
            {
                "key": key,
                "label": MORNING_PART_WORDS[key]["label"],
                "says": MORNING_PART_WORDS[key]["says"],
                "on": key in chosen,
                "default": key in MORNING_PART_DEFAULTS,
            }
            for key in MORNING_PART_CHOICES
        ],
        "adults": [
            {
                "member_id": r["id"],
                "name": r["name"],
                "phone": r["phone"] or "",
                "on": bool(r["morning_text_on"]) and bool(r["phone"]),
            }
            for r in adults
        ],
    }


def _resolve_member(conn, name: str | None, phone: str) -> int:
    adults = _adult_rows(conn)
    if name:
        for r in adults:
            if r["name"].strip().lower() == name.strip().lower():
                return r["id"]
        raise ValueError(f"I don't have {name.strip()} down as an adult here.")
    if phone:
        for r in adults:
            if r["phone"] == phone:
                return r["id"]
    if len(adults) == 1:
        return adults[0]["id"]
    if not adults:
        raise ValueError("I don't have any adults on record yet — add who's in the house first.")
    names = ", ".join(r["name"] for r in adults)
    raise ValueError(f"Which of you is this for? ({names})")


def set_morning_text(
    phone: str | None = None,
    time: str | None = None,
    on: bool | None = None,
    name: str | None = None,
    timezone: str | None = None,
    parts=None,
    add_parts=None,
    drop_parts=None,
) -> dict:
    """
    Set up or change the morning text for one adult. Every argument is
    optional and only what's given changes: a number, the household's hour,
    on/off, (rarely) the household's time zone, and what the message
    includes. `name` says whose number it is; left out, it resolves to the
    only adult, or to the adult already holding that number, and otherwise
    asks.

    Turning it on with no number on record is refused rather than stored
    as a promise nothing can keep. Turning it off is always accepted, and
    a number that is off is never texted.

    THE PARTS ARE THE HOUSEHOLD'S, not this adult's (Emily, 2026-10-04:
    one setting "to keep it simple"), so they change for everybody who
    gets the message however this is addressed — the same way the hour and
    the zone always have. Three ways in, because a household says all
    three: `parts` replaces the lot ("just tell me the meals"), `add_parts`
    adds ("also tell me when to start cooking") and `drop_parts` removes
    ("stop telling me about the shop"). A delta works in one turn, which
    matters because nothing hands the model the current set to edit. All
    three together compose in that order and never need a refusal.
    """
    conn = get_conn()
    new_phone = normalise_phone(phone) if phone is not None else None
    member_id = _resolve_member(conn, name, new_phone or "")
    conn.close()
    return set_morning_text_for_member(
        member_id, phone=phone, time=time, on=on, timezone=timezone,
        parts=parts, add_parts=add_parts, drop_parts=drop_parts,
    )


def set_morning_text_for_member(
    member_id: int,
    phone: str | None = None,
    time: str | None = None,
    on: bool | None = None,
    timezone: str | None = None,
    parts=None,
    add_parts=None,
    drop_parts=None,
) -> dict:
    """
    The same change, addressed by member row — what the Preferences sheet
    posts. Not an agent tool (the model names people, it doesn't hold ids).
    The id must be an adult in THIS household; anything else is refused,
    so a foreign or child id can't be written to by accident.

    `parts` / `add_parts` / `drop_parts` are the household's, not this
    member's; see set_morning_text.
    """
    conn = get_conn()
    if not any(r["id"] == member_id for r in _adult_rows(conn)):
        conn.close()
        raise ValueError("I don't have that person down as an adult here.")
    new_phone = normalise_phone(phone) if phone is not None else None
    if new_phone is not None:
        conn.execute(
            "UPDATE members SET phone = ? WHERE id = ? AND household_id = ?",
            (new_phone, member_id, household_id()),
        )
        if new_phone == "":
            conn.execute(
                "UPDATE members SET morning_text_on = 0 WHERE id = ? AND household_id = ?",
                (member_id, household_id()),
            )
    if on is not None:
        if on:
            current = conn.execute(
                "SELECT phone FROM members WHERE id = ? AND household_id = ?",
                (member_id, household_id()),
            ).fetchone()["phone"]
            if not current:
                conn.close()
                raise ValueError("I need a mobile number before I can text you — which number should it go to?")
        conn.execute(
            "UPDATE members SET morning_text_on = ? WHERE id = ? AND household_id = ?",
            (1 if on else 0, member_id, household_id()),
        )
    if time is not None:
        conn.execute(
            "UPDATE households SET morning_text_time = ? WHERE id = ?",
            (normalise_time(time), household_id()),
        )
    if timezone is not None:
        try:
            ZoneInfo(timezone.strip())
        except (ZoneInfoNotFoundError, ValueError):
            conn.close()
            raise ValueError(f"I don't know the time zone {timezone!r} — it wants a name like America/Toronto.")
        conn.execute("UPDATE households SET timezone = ? WHERE id = ?", (timezone.strip(), household_id()))
    if parts is not None or add_parts is not None or drop_parts is not None:
        # Composed off what is STORED rather than off the defaults, so a
        # delta on a household that has never answered adds to the three
        # the card switches on rather than replacing them with one part.
        row = conn.execute(
            "SELECT morning_text_parts FROM households WHERE id = ?", (household_id(),)
        ).fetchone()
        chosen = list(_read_parts(row["morning_text_parts"] if row else ""))
        if parts is not None:
            chosen = _checked_parts(parts)
        chosen = [k for k in chosen + _checked_parts(add_parts) if k not in set(_checked_parts(drop_parts))]
        try:
            stored = _write_parts(chosen)
        except ValueError:
            conn.close()
            raise
        conn.execute(
            "UPDATE households SET morning_text_parts = ? WHERE id = ?", (stored, household_id())
        )
    conn.commit()
    conn.close()
    settings = get_morning_text_settings()
    me = next((a for a in settings["adults"] if a["member_id"] == member_id), None)
    return {
        "member_id": member_id,
        "name": me["name"] if me else "",
        "phone": me["phone"] if me else "",
        "on": bool(me and me["on"]),
        "time": settings["time"],
        "timezone": settings["timezone"],
        "parts": settings["parts"],
        "configured": twilio_configured(),
    }


# ---------- the digest ----------

def _app_link(tab: str = "") -> str:
    """Into Today, or into one tab by its path ("kitchen" for Cook — the
    shell routes /kitchen to the Cook tab and nothing deeper: there is no
    per-meal query the link could carry, and Cook's root already leads
    with tonight). HOME_MANAGER_URL is the report's name for the live app;
    PUBLIC_BASE_URL is the app's own. Either; neither means no link."""
    base = (os.environ.get("HOME_MANAGER_URL") or PUBLIC_BASE_URL or "").strip().rstrip("/")
    return (base + "/" + tab.strip("/")) if base else ""


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" · ", ", ")).strip().rstrip(".?!")


def _digest_parts(now_local: datetime, included) -> list[tuple[str, str]]:
    """
    Everything today asks of the house that the household asked to hear
    about, each line tagged with the part it belongs to, in the order of
    MORNING_PARTS. Read entirely off today_moves and the live feed —
    nothing here that Today wouldn't also show.

    `included` is the part keys to build, which is the chosen subset
    plus the always-on ones (_included_parts). A part that is not in it is
    not BUILT rather than built and filtered, so a household that doesn't
    want to know who's away pays nothing for the attendance read — which is
    also what makes "built from the chosen parts only" literally true of
    this function rather than merely true of its caller.
    """
    want = set(included)
    payload = _moves.today_moves(day=now_local.date(), now=now_local)
    moves = [m for m in payload["moves"] if not m["done"]]
    by_kind: dict[str, list[dict]] = {}
    for m in moves:
        by_kind.setdefault(m["kind"], []).append(m)

    kinds: dict[str, dict] = {}
    if {"meals", "kitchen"} & want:
        # One read for two parts, and only when one of them wants it. The
        # meals part reads the dinner gap out of it; the kitchen part reads
        # the use-it-up nudge.
        try:
            feed = _notifications.get_active_notifications()
        except Exception:
            logger.exception("Morning text: the notification feed failed; texting without it")
            feed = []
        kinds = {n["type"]: n for n in feed}

    parts: list[tuple[str, str]] = []

    # The dinner cook is read whatever was chosen: the meals part names the
    # dish and the start part names its clock, and both have to be looking
    # at the same move or the text could say when to start cooking something
    # it never mentioned.
    dinner_cook = next((m for m in by_kind.get("cook", []) if m["slot"] == "dinner"), None)

    # 1. TODAY'S MEALS. Tonight first — a cook, a reheat, or an honest gap,
    # never all three — then the day's other cooks, then a lunch somebody
    # made on an earlier day. One part, so one block: "Lunch: Chili" used to
    # come after the shop and the prep and now sits under "Tonight:", which
    # is where the rest of what you're eating today belongs.
    if "meals" in want:
        dinner_reheat = next((m for m in by_kind.get("reheat", []) if m["slot"] == "dinner"), None)
        if dinner_cook:
            parts.append(("meals", f"Tonight: {_tidy(dinner_cook['title'])}."))
        elif dinner_reheat:
            provenance = _tidy(dinner_reheat["detail"]).split(",")[0]
            parts.append(("meals", f"Tonight: {_tidy(dinner_reheat['title'])}, {provenance}."))
        elif kinds.get("dinner_decision", {}).get("key") == f"dinner_gap:{now_local.date().isoformat()}":
            # The feed's nudge covers tonight OR tomorrow; only tonight's gap
            # belongs in today's text. Two shapes, two lines, because they are
            # two different pieces of news: nothing is planned for tonight at
            # all, against a night the app deliberately handed back with a
            # reason. Saying "nothing's planned yet" about the second is
            # saying a thing that isn't true, which §8 doesn't allow.
            parts.append(("meals", "Tonight's still open — nothing's planned yet."))
        elif kinds.get("dinner_open", {}).get("key") == f"dinner_open:{now_local.date().isoformat()}":
            # The card carries the app's own reason for opening the slot and
            # the bell repeats it word for word; this line deliberately does
            # not. A text is a line, not a page — and the numbers below are
            # measured, because the first version of this comment overstated
            # both of them in defence of a decision that did not need it:
            #
            #  - TWO of plan_slot_open's EIGHT call sites open on a weekday
            #    name ("Thursday I'd rather ask than guess: …"), where it
            #    argues with "Tonight" two words earlier: the leftovers repair
            #    (weekly_plan.py) and the generation gap (agent.py). The other
            #    five with fixed text do not, and the eighth is the model's own
            #    sentence, so unknowable. Not "half". Two is still enough,
            #    because this line cannot know which of them opened the night.
            #
            #  - LENGTH is the bigger half. Four of the seven fixed reasons run
            #    past 120 characters and the leftovers repair runs 147 to 179
            #    depending on the clause it interpolates. This is the FIRST
            #    line, the one build_morning_text always keeps, so it spends the
            #    budget before anything else can: measured at the production
            #    shape (a 46-character link, budget 253), a 179-character first
            #    line leaves room for ONE of the day's other three jobs where
            #    the line below leaves room for all three. And the trimmer SKIPS
            #    a line that doesn't fit and still keeps a later one that does,
            #    so what goes is whatever is longest, not the tail — with no
            #    link the same first line drops the shop and keeps the prep
            #    after it. "Crowds the fridge move and the shop out" is not
            #    what the algorithm does.
            #
            # A length test on the reason would be copy that reads differently
            # depending on which day opened the night, which is worse than
            # either. "your call" is the band's own words
            # (get_needs_you_items' title, "Tonight's dinner needs your
            # call"); the reason itself is one tap away on the card this text
            # links to — which also keeps the one model-authored open_reason
            # (agent.py's per-slot pass) off the SMS channel entirely, a
            # standing property worth not undoing.
            parts.append(("meals", "Tonight's still open — it's your call."))

        # The other meals being cooked today, after tonight's.
        for m in by_kind.get("cook", []):
            if m is dinner_cook or m["slot"] == "dinner":
                continue
            parts.append(("meals", f"{(m['slot'] or 'Meal').capitalize()}: {_tidy(m['title'])}."))

        # A lunch made on an earlier day: not a job, but worth one line —
        # "Lunch: Chili — prepped Sunday." (Emily, 2026-09-30, option (a): the
        # text says it's ready instead of "start by noon"). The words after the
        # dish are the move's own meta, so the text and Today cannot differ.
        for m in by_kind.get("reheat", []):
            if m.get("prepped_ahead") and m["slot"] != "dinner":
                parts.append(("meals", f"{(m['slot'] or 'Meal').capitalize()}: {_tidy(m['title'])} — {_tidy(m['meta'])}."))

    # 2. THE FREEZER. "Move the chicken thighs to the fridge — for
    # Thursday's skewers." Today is implied: this is today's text.
    if "freezer" in want:
        for m in by_kind.get("fridge", []):
            reason = _tidy(m.get("reason") or "")
            parts.append(("freezer", f"{_tidy(m['title'])} — {reason}." if reason else f"{_tidy(m['title'])} today."))

    # 3. THE PREP.
    if "prep" in want:
        for m in by_kind.get("prep", []):
            parts.append(("prep", f"{_tidy(m['title'])} — {_tidy(m['time_label'])}."))

    # 4. WHEN TO START COOKING DINNER (2026-10-04). The clock, on a line of
    # its own — see MORNING_START_LINE for why it is never a clause on
    # "Tonight:". Two gates, and both are moves.py's own rather than a second
    # reading of the same question: it is the PLANNED start (a cook already
    # under way has no start left to name, and moves' chip says "Started
    # 6:02" there rather than "Start by"), and only when the recipe carries
    # minutes (with none there is nothing to count back from, which is
    # exactly when that chip is left off too). A reheat night never has one:
    # a reheat is a line, never "Tap to start".
    if "start" in want and dinner_cook and not dinner_cook.get("started_at"):
        planned = dinner_cook.get("planned_start")
        if planned and dinner_cook.get("duration_min"):
            # moves._clock is the one implementation of "the time the way a
            # person says it aloud" — "5:45", and "noon" rather than 12:00.
            # Reached across the module deliberately: the alternative is
            # either a second formatter (two spellings of one clock, which
            # this repo keeps having to unpick) or parsing moves' own
            # "Start by 5:45" chip back out of an English string.
            clock = _moves._clock(datetime.fromisoformat(planned).time())
            parts.append(("start", MORNING_START_LINE.format(clock=clock)))

    # 5. THE SHOP, when there is a cook close enough for it to matter. A
    # shop move with no deadline is the standing list saying it is still
    # there (moves._standing_list_move) — true, and not one of today's
    # JOBS, which is all this text is for. (It is one of today's moves:
    # by_kind is built from today_moves, which is why this has to skip it.)
    #
    # On the household's own shop day (Emily, 2026-10-05: the box is
    # "Shopping" and covers both) it is the shop-day line instead, ONE line
    # even when a cook is also waiting on the list: that cook's deadline
    # rides on it as a clause ("You shop today, by 5:55. 14 things on the
    # list."), never a second line saying the same errand twice. The
    # deadline only rides when it is a clock today — "by tomorrow" on a shop
    # day would argue with "today", and "still to do" is already what
    # "You shop today" says.
    if "shop" in want:
        timed = [m for m in by_kind.get("shop", []) if m.get("timed", True)]
        block = payload.get("shop") or {}
        if block.get("is_shop_day"):
            count = int(block.get("count") or 0)
            things = (
                MORNING_SHOP_THINGS_NONE if not count
                else MORNING_SHOP_THINGS_ONE if count == 1
                else MORNING_SHOP_THINGS_MANY.format(count=count)
            )
            when = str(timed[0].get("time_label") or "") if timed else ""
            by = (
                MORNING_SHOP_DAY_BY.format(deadline=when)
                if when.startswith("by ") and when != "by tomorrow" else ""
            )
            parts.append(("shop", MORNING_SHOP_DAY_LINE.format(by=by, things=things)))
        else:
            for m in timed:
                parts.append(("shop", f"{_tidy(m['title'])} — {_tidy(m['detail'])}."))

    # 6. WHO'S AWAY TONIGHT (2026-10-04) — attendance's own sentence, word
    # for word. summary_line is where "Dinner for 3 — Vineeth's out." lives
    # and it is stamped on every attendance read; writing a second one here
    # is the two-implementations trap, and that function's own docstring
    # records the last time this sentence had two spellings on one screen.
    # It is '' when everyone is home with no guests, so "a part with nothing
    # today is left out" falls out rather than being coded. Guests are in it
    # ("Dinner for 5 — with 2 guests."): the box asks who's at the table,
    # and an away-only variant would be that second sentence again.
    if "away" in want:
        try:
            att = _attendance.get_slot_attendance(now_local.date().isoformat(), "dinner")
        except Exception:
            logger.exception("Morning text: tonight's attendance could not be read; leaving it out")
            att = {}
        summary = str(att.get("summary") or "").strip()
        if summary:
            parts.append(("away", summary))

    # 7. FOOD TO USE UP ("kitchen"): one thing from the attention queue, and
    # the use-it-up nudge. A box since 2026-10-05, on by default.
    if "kitchen" in want:
        try:
            items = _attention.get_attention_items()
        except Exception:
            logger.exception("Morning text: the attention queue failed; texting without it")
            items = []
        if items:
            summary = str(items[0].get("summary") or "").rstrip()
            if summary:
                parts.append(("kitchen", _tidy(summary) + ("?" if summary.endswith("?") else ".")))
        if "expiring_soon" in kinds:
            parts.append(("kitchen", _tidy(kinds["expiring_soon"]["title"]) + "."))

    return parts


def _trim_to_budget(tagged: list[tuple[str, str]], budget: int) -> tuple[list[str], list[str], list[str]]:
    """
    The lines that fit, in order, the parts that said something, and the
    parts that got none of theirs in.

    ADMISSION IS BY PART, not by position: every part's FIRST line is
    offered before any part's SECOND. That is the answer to what seven
    switchable parts do to a character budget — before this, lines were
    offered strictly in order, so three fridge moves could eat the room a
    part the household had explicitly ticked was waiting for, and the part
    that lost its only line was always the one furthest down. The lines
    still come OUT in part order; only which ones get in changes, and on an
    ordinary day (every line fits) the answer is identical either way.

    The first line is still always kept, trimmed if it must be — a text
    that says "Tonight: chicken tacos" and nothing else is still the text.
    It is still line 0: the first line of the first part is also first in
    the admission order, so nothing about that rule moved.

    `dropped` is the parts with NOTHING kept, which is the one worth saying
    on a screen ("the shop didn't fit today"). A part that got its first
    line and lost its third is not in it — that is what the budget has
    always done to a long day, and naming it would be a list of fridge
    moves nobody can act on.
    """
    first: list[int] = []
    rest: list[int] = []
    seen: set[str] = set()
    for i, (part, _line) in enumerate(tagged):
        (rest if part in seen else first).append(i)
        seen.add(part)

    out = list(tagged)
    kept: set[int] = set()
    length = 0
    for i in first + rest:
        line = out[i][1]
        extra = len(line) + (1 if kept else 0)
        if length + extra <= budget:
            kept.add(i)
            length += extra
        elif not kept:
            out[i] = (out[i][0], line[: max(budget - 1, 1)].rstrip() + "…")
            kept.add(i)
            length = len(out[i][1])
    order = sorted(kept)
    lines = [out[i][1] for i in order]
    spoke = {out[i][0] for i in order}
    said = [p for p in MORNING_PARTS if p in spoke]
    dropped = [p for p in MORNING_PARTS if p in seen and p not in spoke]
    return lines, said, dropped


def _compose_morning(now_local: datetime | None, link: bool, parts) -> dict:
    """
    One composer, two readers: build_morning_text takes `text` out of this
    and morning_text_preview hands the whole thing to the screen. There is
    deliberately no second builder beside _digest_parts — the preview's
    whole promise is that it is the same text the sender would send, and
    two composers is how that stops being true.

    `parts` is the chosen subset; None reads the household's answer.
    """
    # The default is the HOUSEHOLD's clock, never the server's. The container
    # runs UTC and households default to America/Toronto, so from 8pm local
    # datetime.now() is already tomorrow — a text reasoning about a different
    # day from every screen that produced it. The sending loop has always
    # passed now_local, so nothing in production ever took the old default;
    # it was the next caller's trap, and it contradicted the line above.
    #
    # household_now opens its own connection, so a caller that omits the
    # clock must not be inside an open write transaction. The sending loop
    # holds one open across this call but passes the clock and has only
    # read on it, so nothing here nests a write.
    now_local = now_local if now_local is not None else _cooker.household_now()
    if now_local.tzinfo is not None:
        now_local = now_local.replace(tzinfo=None)
    chosen = morning_text_parts() if parts is None else _known_parts(parts)
    tagged = _digest_parts(now_local, _included_parts(chosen))
    if not tagged:
        return {"text": None, "lines": [], "parts": [], "chosen": chosen, "dropped": [], "would_send": False}

    # `link=False` is the push notification's body (app/push.py): the tap
    # on the notification is the way in, so the address would be noise.
    url = _app_link() if link else ""
    budget = MAX_TEXT_CHARS - (len(url) + 1 if url else 0)
    lines, said, dropped = _trim_to_budget(tagged, budget)
    body = " ".join(lines)
    text = f"{body} {url}" if url else body
    return {
        "text": text,
        "lines": lines,
        "parts": said,
        "chosen": chosen,
        "dropped": dropped,
        "would_send": True,
    }


def build_morning_text(now_local: datetime | None = None, link: bool = True, parts=None) -> str | None:
    """
    The text, or None when there is nothing worth a text. `now_local` is
    the household's own clock, naive (today_moves compares naive
    timestamps); the loop passes it, tests pass what they like, and
    omitting it reads the household's clock rather than the server's.

    `parts` is the chosen parts; omitted, it reads the household's answer
    (MORNING_PART_CHOICES / morning_text_parts), which is what the sending
    loop and push both take. Passing it is for the preview's "as it would
    read if you ticked this" and for a test that wants one part at a time.
    Nothing to say in the chosen parts is no text at all, exactly as an
    empty day has always been.
    """
    return _compose_morning(now_local, link, parts)["text"]


def morning_text_preview(now_local: datetime | None = None, parts=None) -> dict:
    """
    Exactly what the sender would send today, for the screen under the
    boxes. `text` is the text channel's body, link and all — the same
    string _run_household hands to `send`, from the same composer, so the
    preview cannot drift from the message. `push_text` is the push body,
    which is the same lines without the address (app/push.py).

    Also, because the screen can say more than one sentence about a
    message it is showing: `lines` (the kept lines, in order), `parts` (the
    parts that actually contributed one), `chosen` (the answer as read, so
    an override is visible), `dropped` (parts with something to say today
    that the character budget cut) and `would_send` (false = nothing today,
    so no message goes out at all).
    """
    out = _compose_morning(now_local, True, parts)
    out["push_text"] = _compose_morning(now_local, False, parts)["text"]
    return out


# ---------- sending: the channel seam ----------

def twilio_configured() -> bool:
    return all((os.environ.get(k) or "").strip() for k in TWILIO_ENV)


def send_sms(to: str, body: str) -> dict:
    """
    One text through Twilio's REST API. Returns {status, detail} and never
    raises: the loop records the outcome and moves on to the next number.
    `detail` is for the report — a status code and Twilio's own reason —
    and never carries the auth token or the text.
    """
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    sender = (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()
    if not (sid and token and sender):
        return {"status": "skipped-no-keys", "detail": "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN or TWILIO_FROM_NUMBER unset"}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(sid)}/Messages.json"
    data = urllib.parse.urlencode({"To": to, "From": sender, "Body": body}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii"))
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
        message_sid = str(payload.get("sid") or "")
        return {"status": "ok", "detail": f"twilio {message_sid[:10]}" if message_sid else "twilio accepted"}
    except urllib.error.HTTPError as e:
        reason = ""
        try:
            err = json.loads(e.read().decode("utf-8") or "{}")
            reason = f" {err.get('code', '')} {err.get('message', '')}".rstrip()
        except Exception:
            pass
        return {"status": "failed", "detail": _redact(f"HTTP {e.code}{reason}")[:160]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"status": "failed", "detail": _redact(f"{type(e).__name__}: {e}")[:160]}


# The seam. One digest, more than one way to deliver it: text is built,
# email is the documented fallback for a household that would rather (no
# code behind it yet — a name here, not a promise), push comes with the
# PWA and the App Store. Adding a channel is adding an entry.
CHANNELS: dict[str, Callable[[str, str], dict]] = {
    "text": send_sms,
}


def send_digest(channel: str, to: str, body: str) -> dict:
    sender = CHANNELS.get(channel)
    if sender is None:
        return {"status": "failed", "detail": f"no {channel} channel yet"}
    try:
        return sender(to, body)
    except Exception as e:  # a sender must never take the loop down
        logger.exception("Morning text: the %s channel raised", channel)
        return {"status": "failed", "detail": f"{type(e).__name__}"[:160]}


# ---------- push, ahead of text (2026-09-27) ----------
#
# Loop Board "App Store: push notifications on the iPhone app". An adult
# who has the iPhone app and allowed notifications gets the morning note
# and the evening nudge on the lock screen instead of by text. Text is the
# fallback: for everyone else, and for anyone whose push didn't go through
# (every phone refused, Apple unreachable) — in which case they get the
# text exactly as before, if they have it on.
#
# With push off (APNS_* unset — see app/push.py) `_push_ready` is False for
# everyone and both passes below do exactly what they did before push
# existed. Any error reading push state reads as "not ready", never as a
# failed pass.

# Where a tap on each notification lands (app/push.py ALLOWED_PATHS).
MORNING_PUSH_PATH = "/"
EVENING_PUSH_PATH = "/kitchen"
# How long Apple should keep trying a phone that's off. A morning note is
# stale by the afternoon; a "start dinner" nudge after the late window is
# noise.
MORNING_PUSH_TTL_SECONDS = LATE_WINDOW_HOURS * 3600
EVENING_PUSH_TTL_SECONDS = 90 * 60


def _push_ready(member_id: int) -> bool:
    try:
        from .. import push as _push

        return _push.can_push(member_id)
    except Exception:
        logger.exception("Push state for member %s could not be read; using text", member_id)
        return False


def _push(member_id: int, body: str, path: str, ttl_seconds: int) -> dict:
    try:
        from .. import push as _push_mod

        return _push_mod.send_to_member(member_id, body, path=path, ttl_seconds=ttl_seconds)
    except Exception as e:
        logger.exception("Push to member %s raised; falling back to text", member_id)
        return {"status": "failed", "detail": f"push {type(e).__name__}"[:160]}


def _texts_on(r) -> bool:
    """The morning text's own yes: switched on, with a number."""
    return bool(r["morning_text_on"]) and bool(r["phone"])


# ---------- the daily pass ----------

# A phone number as Twilio writes one (+14165550100), a bare run of ten
# or more digits, or a formatted one (416-555-0100, (416) 555 0100). Not
# an HTTP status or a five-digit Twilio error code, which the report needs.
_NUMBER_RE = re.compile(r"\+\d{7,}|\b\d{10,}\b|\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b")


def _redact(detail: str) -> str:
    """Twilio's own error text names the number it refused ("The 'To'
    number +1416... is not a valid phone number"). The row and the log
    keep the reason, not the number."""
    return _NUMBER_RE.sub("[number]", detail or "")


def _record(conn, member_id: int, sent_on: str, status: str, detail: str = "") -> None:
    detail = _redact(detail)
    conn.execute(
        "INSERT OR IGNORE INTO morning_text_sends (household_id, member_id, sent_on, status, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (household_id(), member_id, sent_on, status, (detail or "")[:160]),
    )
    conn.commit()


def _already_handled(conn, member_id: int, sent_on: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM morning_text_sends WHERE household_id = ? AND member_id = ? AND sent_on = ?",
        (household_id(), member_id, sent_on),
    ).fetchone() is not None


def _run_household(now_utc: datetime, send: Callable[[str, str], dict]) -> list[dict]:
    """One household, already bound with use_household. Returns what it did."""
    conn = get_conn()
    hh = _household_row(conn)
    local_now = now_utc.astimezone(_zone(hh["timezone"]))
    hour, minute = (int(x) for x in hh["time"].split(":"))
    due_at = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now < due_at:
        conn.close()
        return []
    sent_on = local_now.date().isoformat()

    waiting = [
        r for r in _adult_rows(conn)
        if (_texts_on(r) or _push_ready(r["id"])) and not _already_handled(conn, r["id"], sent_on)
    ]
    if not waiting:
        conn.close()
        return []

    done: list[dict] = []
    if local_now - due_at > timedelta(hours=LATE_WINDOW_HOURS):
        for r in waiting:
            _record(conn, r["id"], sent_on, "skipped-late", f"{int((local_now - due_at).total_seconds() // 3600)}h past {hh['time']}")
            done.append({"member_id": r["id"], "status": "skipped-late"})
        conn.close()
        return done

    text = build_morning_text(local_now.replace(tzinfo=None))
    if not text:
        for r in waiting:
            _record(conn, r["id"], sent_on, "skipped-empty", "nothing to say today")
            done.append({"member_id": r["id"], "status": "skipped-empty"})
        conn.close()
        return done

    push_body = None
    for r in waiting:
        result = None
        if _push_ready(r["id"]):
            if push_body is None:
                push_body = build_morning_text(local_now.replace(tzinfo=None), link=False) or text
            result = _push(r["id"], push_body, MORNING_PUSH_PATH, MORNING_PUSH_TTL_SECONDS)
            if result.get("status") != "ok" and _texts_on(r):
                logger.info(
                    "Morning note for household %s member %s: push %s, texting instead",
                    household_id(), r["id"], result.get("status"),
                )
                result = None
        if result is None:
            result = send(r["phone"], text)
        status = result.get("status") or "failed"
        _record(conn, r["id"], sent_on, status, result.get("detail") or "")
        done.append({"member_id": r["id"], "status": status})
        if status != "ok":
            # Member id, never the number: the log is not the place for it.
            logger.warning(
                "Morning text for household %s member %s: %s (%s)",
                household_id(), r["id"], status, _redact(result.get("detail") or ""),
            )
    conn.close()
    return done


def run_morning_texts_once(
    now_utc: datetime | None = None,
    send: Callable[[str, str], dict] | None = None,
) -> list[dict]:
    """
    One pass over every household. The loop in app/main.py calls this every
    POLL_SECONDS; a household whose local time has passed its hour, and
    which has an opted-in adult with no row for today, gets its text.

    `send` is the channel (tests hand in a stub); left out, it is the text
    channel — which, with no Twilio keys, records skipped-no-keys rather
    than sending, so a pass is safe to run anywhere.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    sender = send or (lambda to, body: send_digest("text", to, body))

    conn = get_conn()
    households = [r["id"] for r in conn.execute("SELECT id FROM households ORDER BY id").fetchall()]
    conn.close()

    results: list[dict] = []
    for hid in households:
        with use_household(hid):
            try:
                for item in _run_household(now_utc, sender):
                    results.append({"household_id": hid, **item})
            except Exception:
                # One household's bad morning must not cost the next one
                # theirs. Nothing was recorded for it, so the next pass
                # tries again.
                logger.exception("Morning text pass failed for household %s", hid)
    return results


# ---------- the evening nudge ----------
#
# Loop Board "Evening cook nudge" (2026-09-21). The morning text says what
# the day needs; this is the one line at the moment the cook is due —
# "Pomona, hold this" carried to the stove. It is deliberately the same
# path as the morning text (same numbers, same channel, same loop), so
# nothing here is a second notification system; when push lands it slots
# into send_evening_nudge below and this module doesn't otherwise change.

# When the nudge goes: the START of the household's dinner window
# (rhythm.dinner_window), as against defrost._DINNER_CLOCK_BY_WINDOW, which
# is when dinner LANDS and is what the plan counts backward from. A nudge
# at the landing time is a nudge after the cook should have begun. 'all_over'
# and an unanswered question have no window to start, so they take the
# middle of the road — stated here as a default, not a household fact.
EVENING_NUDGE_CLOCK_BY_WINDOW = {
    "5_6ish": time(17, 0),
    "6_8": time(18, 0),
    "later": time(19, 0),
}
EVENING_NUDGE_DEFAULT_CLOCK = time(17, 30)

# How long after the window opens the loop will still send. Ninety minutes
# covers a container that was down at the hour; past that the cook is
# under way or the night has moved on, and "Tap to start" at half past
# eight is noise. Nothing is recorded for a night that never sent — see
# _run_household_evening for why.
EVENING_NUDGE_LATE_WINDOW_MINUTES = 90


def evening_nudge_clock(dinner_window: str | None) -> time:
    """The clock the nudge goes at for one dinner_window answer."""
    return EVENING_NUDGE_CLOCK_BY_WINDOW.get((dinner_window or "").strip()) or EVENING_NUDGE_DEFAULT_CLOCK


def _household_dinner_window() -> str | None:
    try:
        return _rhythm.get_household_rhythm().get("dinner_window")
    except Exception:
        logger.exception("Evening nudge: the rhythm could not be read; using the default clock")
        return None


def tonight_for_nudge(now_local: datetime) -> dict:
    """
    What the nudge would be about, and why not when it wouldn't.

    Returns {"dinner": move | None, "first": move | None, "reason": str}.
    `reason` is '' when there is a nudge to send, else the first thing that
    rules it out: 'no_dinner' (nothing planned, a night off, an open slot,
    a leftovers-only night), 'away' (nobody home for dinner — the
    attendance answer or an 'away' need, whichever said so), 'done'
    (tonight's dinner already ticked) or 'started' (the cook is under way).
    `first` is an undone fridge move or prep step on today's timeline, when
    there is one — the thing to do before the dish.

    Read entirely off today_moves and the day's own tags, so this never
    says anything Today wouldn't also show.
    """
    day = now_local.date()
    iso = day.isoformat()
    try:
        att = _attendance.get_slot_attendance(iso, "dinner")
        need = _slot_needs.get_slot_need(iso, "dinner").get("need")
    except Exception:
        logger.exception("Evening nudge: tonight's attendance could not be read; assuming everyone's home")
        att, need = {"nobody_home": False}, "normal"
    if att.get("nobody_home") or need == "away":
        return {"dinner": None, "first": None, "reason": "away"}

    payload = _moves.today_moves(day=day, now=now_local)
    moves = payload["moves"]
    dinner = next((m for m in moves if m["kind"] == "cook" and m["slot"] == "dinner"), None)
    if dinner is None:
        # A reheat night is a line, never the card (Emily, 2026-09-08) —
        # and never a nudge to start cooking either.
        return {"dinner": None, "first": None, "reason": "no_dinner"}
    if dinner["done"]:
        return {"dinner": dinner, "first": None, "reason": "done"}
    if dinner.get("started_at"):
        return {"dinner": dinner, "first": None, "reason": "started"}
    first = next((m for m in moves if m["kind"] == "fridge" and not m["done"]), None) \
        or next((m for m in moves if m["kind"] == "prep" and not m["done"]), None)
    return {"dinner": dinner, "first": first, "reason": ""}


def build_evening_nudge(now_local: datetime | None = None, link: bool = True) -> str | None:
    """
    The nudge, or None when tonight doesn't want one. One breath:

        Tonight: lemon chicken & orzo — 35 min. Tap to start. <link>
        Move the chicken thighs to the fridge first — then lemon chicken & orzo. <link>

    The dish is named as stored; the minutes are the plan's own total
    (cooker.cook_total_minutes, via the move) and are left out when the
    recipe has none. The link opens Cook, whose root leads with tonight.
    `now_local` is the household's clock, as for build_morning_text.
    """
    now_local = now_local if now_local is not None else _cooker.household_now()
    if now_local.tzinfo is not None:
        now_local = now_local.replace(tzinfo=None)
    tonight = tonight_for_nudge(now_local)
    dinner = tonight["dinner"]
    if dinner is None or tonight["reason"]:
        return None
    dish = _tidy(dinner["title"])
    first = tonight["first"]
    if first is not None:
        text = f"{_tidy(first['title'])} first — then {dish}."
    else:
        minutes = int(dinner.get("duration_min") or 0)
        text = f"Tonight: {dish} — {minutes} min. Tap to start." if minutes else f"Tonight: {dish}. Tap to start."
    # No address in a push notification (link=False): tapping it opens Cook.
    url = _app_link("kitchen") if link else ""
    return f"{text} {url}" if url else text


def send_evening_nudge(member: dict, text: str) -> dict:
    """
    The TEXT seam: one nudge to one person, as a text to members.phone
    through the morning text's own channel (send_digest → send_sms). Push
    (2026-09-27) is tried before this, in _run_household_evening, for an
    adult with the iPhone app who allowed notifications; this function is
    what everyone else gets, and the fallback when a push doesn't go
    through. `member` is {member_id, name, phone}; returns
    {status, detail} and never raises (send_digest guarantees that).
    """
    return send_digest("text", member.get("phone") or "", text)


def get_evening_nudge_settings() -> dict:
    """
    Who gets the nudge, and when. Per adult, `on` is their own switch
    (members.evening_nudge_on, on by default) and `active` is whether one
    will actually go: their switch AND the morning text on with a number
    — the nudge rides on the morning text's number and never has one of
    its own. `clock` is 'HH:MM' local, from the dinner window.
    """
    conn = get_conn()
    hh = _household_row(conn)
    adults = _adult_rows(conn)
    conn.close()
    window = _household_dinner_window()
    clock = evening_nudge_clock(window)
    return {
        "clock": f"{clock.hour:02d}:{clock.minute:02d}",
        "dinner_window": window or "",
        "timezone": hh["timezone"],
        "adults": [
            {
                "member_id": r["id"],
                "name": r["name"],
                "on": bool(r["evening_nudge_on"]),
                "morning_on": bool(r["morning_text_on"]) and bool(r["phone"]),
                "active": bool(r["evening_nudge_on"]) and bool(r["morning_text_on"]) and bool(r["phone"]),
            }
            for r in adults
        ],
    }


def set_evening_nudge_for_member(member_id: int, on: bool) -> dict:
    """
    Flip one adult's evening nudge, by member row — the Preferences sheet's
    row beside the morning text's. Same guard as set_morning_text_for_member:
    the id must be an adult in THIS household.
    """
    conn = get_conn()
    if not any(r["id"] == member_id for r in _adult_rows(conn)):
        conn.close()
        raise ValueError("I don't have that person down as an adult here.")
    conn.execute(
        "UPDATE members SET evening_nudge_on = ? WHERE id = ? AND household_id = ?",
        (1 if on else 0, member_id, household_id()),
    )
    conn.commit()
    conn.close()
    settings = get_evening_nudge_settings()
    me = next((a for a in settings["adults"] if a["member_id"] == member_id), None)
    return {
        "member_id": member_id,
        "name": me["name"] if me else "",
        "on": bool(me and me["on"]),
        "active": bool(me and me["active"]),
        "clock": settings["clock"],
        "configured": twilio_configured(),
    }


def _run_household_evening(now_utc: datetime, send: Callable[[dict, str], dict]) -> list[dict]:
    """
    One household's evening, already bound with use_household. Returns
    what it sent (and nothing for a pass that sent nothing).

    Only a send stamps members.evening_nudge_sent_on — a night with no
    dinner at the hour is NOT stamped, so a dinner planned twenty minutes
    into the window still gets its nudge on the next pass, and the late
    window above is what stops the checking. A failed or keyless send IS
    stamped, exactly as the morning text records and moves on: retrying a
    refused number every five minutes is not a kindness.
    """
    conn = get_conn()
    hh = _household_row(conn)
    local_now = now_utc.astimezone(_zone(hh["timezone"]))
    clock = evening_nudge_clock(_household_dinner_window())
    due_at = local_now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    if local_now < due_at or local_now - due_at > timedelta(minutes=EVENING_NUDGE_LATE_WINDOW_MINUTES):
        conn.close()
        return []
    sent_on = local_now.date().isoformat()

    waiting = [
        r for r in _adult_rows(conn)
        if r["evening_nudge_on"] and r["evening_nudge_sent_on"] != sent_on
        and (_texts_on(r) or _push_ready(r["id"]))
    ]
    if not waiting:
        conn.close()
        return []

    text = build_evening_nudge(local_now.replace(tzinfo=None))
    if not text:
        conn.close()
        return []

    done: list[dict] = []
    push_body = None
    for r in waiting:
        result = None
        if _push_ready(r["id"]):
            if push_body is None:
                push_body = build_evening_nudge(local_now.replace(tzinfo=None), link=False) or text
            result = _push(r["id"], push_body, EVENING_PUSH_PATH, EVENING_PUSH_TTL_SECONDS)
            if result.get("status") != "ok" and _texts_on(r):
                logger.info(
                    "Evening nudge for household %s member %s: push %s, texting instead",
                    household_id(), r["id"], result.get("status"),
                )
                result = None
        if result is None:
            result = send({"member_id": r["id"], "name": r["name"], "phone": r["phone"]}, text)
        status = result.get("status") or "failed"
        conn.execute(
            "UPDATE members SET evening_nudge_sent_on = ? WHERE id = ? AND household_id = ?",
            (sent_on, r["id"], household_id()),
        )
        conn.commit()
        done.append({"member_id": r["id"], "status": status})
        if status != "ok":
            logger.warning(
                "Evening nudge for household %s member %s: %s (%s)",
                household_id(), r["id"], status, _redact(result.get("detail") or ""),
            )
    conn.close()
    return done


def run_evening_nudges_once(
    now_utc: datetime | None = None,
    send: Callable[[dict, str], dict] | None = None,
) -> list[dict]:
    """
    One pass over every household, the evening twin of
    run_morning_texts_once: the loop in app/main.py calls both on each
    tick. A household inside its nudge window with an opted-in adult not
    yet nudged today, and a dinner to start, gets its line.

    `send` is the seam (tests hand in a stub); left out, it is
    send_evening_nudge — text, and with no Twilio keys a recorded
    skipped-no-keys rather than a send.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    sender = send or send_evening_nudge

    conn = get_conn()
    households = [r["id"] for r in conn.execute("SELECT id FROM households ORDER BY id").fetchall()]
    conn.close()

    results: list[dict] = []
    for hid in households:
        with use_household(hid):
            try:
                for item in _run_household_evening(now_utc, sender):
                    results.append({"household_id": hid, **item})
            except Exception:
                logger.exception("Evening nudge pass failed for household %s", hid)
    return results


# ---------- for the morning report ----------

def get_morning_text_report(days: int = 1) -> dict:
    """Counts for observability_report.py — never a number, never a body."""
    conn = get_conn()
    opted_in = conn.execute(
        "SELECT COUNT(*) AS n FROM members WHERE household_id = ? AND morning_text_on = 1 AND phone != ''",
        (household_id(),),
    ).fetchone()["n"]
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM morning_text_sends "
        f"WHERE household_id = ? AND created_at >= datetime('now', '-{int(days)} days') "
        f"GROUP BY status",
        (household_id(),),
    ).fetchall()
    last_failed = conn.execute(
        "SELECT detail FROM morning_text_sends WHERE household_id = ? AND status = 'failed' "
        "ORDER BY id DESC LIMIT 1",
        (household_id(),),
    ).fetchone()
    conn.close()
    by_status = {r["status"]: r["n"] for r in rows}
    return {
        "configured": twilio_configured(),
        "opted_in": opted_in,
        "days": int(days),
        "total": sum(by_status.values()),
        "by_status": by_status,
        "last_failure": (last_failed["detail"] if last_failed else "") or "",
    }
