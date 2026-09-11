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
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import get_conn
from ._shared import PUBLIC_BASE_URL, household_id, use_household
from . import attention as _attention
from . import moves as _moves
from . import notifications as _notifications

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
        "SELECT timezone, morning_text_time FROM households WHERE id = ?", (household_id(),)
    ).fetchone()
    return {
        "timezone": (row["timezone"] if row else None) or DEFAULT_TIMEZONE,
        "time": (row["morning_text_time"] if row else None) or DEFAULT_SEND_TIME,
    }


def _adult_rows(conn) -> list:
    # LOWER() because onboarding writes "Adult" and older rows say "adult";
    # '' is a member from before age groups existed, read as an adult
    # rather than silently left off the list.
    return conn.execute(
        """
        SELECT id, name, phone, morning_text_on FROM members
        WHERE household_id = ? AND LOWER(age_group) IN ('adult', '') AND TRIM(name) != ''
        ORDER BY id
        """,
        (household_id(),),
    ).fetchall()


def get_morning_text_settings() -> dict:
    """Who gets the morning text, at what hour, on which clock — the
    Preferences row and its sheet read exactly this."""
    conn = get_conn()
    hh = _household_row(conn)
    adults = _adult_rows(conn)
    conn.close()
    return {
        "time": hh["time"],
        "timezone": hh["timezone"],
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
) -> dict:
    """
    Set up or change the morning text for one adult. Every argument is
    optional and only what's given changes: a number, the household's hour,
    on/off, and (rarely) the household's time zone. `name` says whose
    number it is; left out, it resolves to the only adult, or to the adult
    already holding that number, and otherwise asks.

    Turning it on with no number on record is refused rather than stored
    as a promise nothing can keep. Turning it off is always accepted, and
    a number that is off is never texted.
    """
    conn = get_conn()
    new_phone = normalise_phone(phone) if phone is not None else None
    member_id = _resolve_member(conn, name, new_phone or "")
    conn.close()
    return set_morning_text_for_member(member_id, phone=phone, time=time, on=on, timezone=timezone)


def set_morning_text_for_member(
    member_id: int,
    phone: str | None = None,
    time: str | None = None,
    on: bool | None = None,
    timezone: str | None = None,
) -> dict:
    """
    The same change, addressed by member row — what the Preferences sheet
    posts. Not an agent tool (the model names people, it doesn't hold ids).
    The id must be an adult in THIS household; anything else is refused,
    so a foreign or child id can't be written to by accident.
    """
    conn = get_conn()
    if not any(r["id"] == member_id for r in _adult_rows(conn)):
        conn.close()
        raise ValueError("I don't have that person down as an adult here.")
    new_phone = normalise_phone(phone) if phone is not None else None
    if new_phone is not None:
        conn.execute("UPDATE members SET phone = ? WHERE id = ?", (new_phone, member_id))
        if new_phone == "":
            conn.execute("UPDATE members SET morning_text_on = 0 WHERE id = ?", (member_id,))
    if on is not None:
        if on:
            current = conn.execute("SELECT phone FROM members WHERE id = ?", (member_id,)).fetchone()["phone"]
            if not current:
                conn.close()
                raise ValueError("I need a mobile number before I can text you — which number should it go to?")
        conn.execute("UPDATE members SET morning_text_on = ? WHERE id = ?", (1 if on else 0, member_id))
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
        "configured": twilio_configured(),
    }


# ---------- the digest ----------

def _app_link() -> str:
    """Into Today. HOME_MANAGER_URL is the report's name for the live app;
    PUBLIC_BASE_URL is the app's own. Either; neither means no link."""
    base = (os.environ.get("HOME_MANAGER_URL") or PUBLIC_BASE_URL or "").strip().rstrip("/")
    return (base + "/") if base else ""


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" · ", ", ")).strip().rstrip(".?!")


def _digest_lines(now_local: datetime) -> list[str]:
    """
    Everything today asks of the house, most important first, each a
    sentence a person would say. Read entirely off today_moves and the
    live feed — nothing here that Today wouldn't also show.
    """
    payload = _moves.today_moves(day=now_local.date(), now=now_local)
    moves = [m for m in payload["moves"] if not m["done"]]
    by_kind: dict[str, list[dict]] = {}
    for m in moves:
        by_kind.setdefault(m["kind"], []).append(m)

    try:
        feed = _notifications.get_active_notifications()
    except Exception:
        logger.exception("Morning text: the notification feed failed; texting without it")
        feed = []
    kinds = {n["type"]: n for n in feed}

    lines: list[str] = []

    # 1. Tonight. A cook, a reheat, or an honest gap — never all three.
    dinner_cook = next((m for m in by_kind.get("cook", []) if m["slot"] == "dinner"), None)
    dinner_reheat = next((m for m in by_kind.get("reheat", []) if m["slot"] == "dinner"), None)
    if dinner_cook:
        lines.append(f"Tonight: {_tidy(dinner_cook['title'])}.")
    elif dinner_reheat:
        provenance = _tidy(dinner_reheat["detail"]).split(",")[0]
        lines.append(f"Tonight: {_tidy(dinner_reheat['title'])}, {provenance}.")
    elif kinds.get("dinner_decision", {}).get("key") == f"dinner_gap:{now_local.date().isoformat()}":
        # The feed's nudge covers tonight OR tomorrow; only tonight's gap
        # belongs in today's text.
        lines.append("Tonight's still open — nothing's planned yet.")

    # 2. The freezer. "Move the chicken thighs to the fridge — for
    # Thursday's skewers." Today is implied: this is today's text.
    for m in by_kind.get("fridge", []):
        reason = _tidy(m.get("reason") or "")
        lines.append(f"{_tidy(m['title'])} — {reason}." if reason else f"{_tidy(m['title'])} today.")

    # 3. The shop, when there is a cook close enough for it to matter.
    for m in by_kind.get("shop", []):
        lines.append(f"{_tidy(m['title'])} — {_tidy(m['detail'])}.")

    # 4. The prep.
    for m in by_kind.get("prep", []):
        lines.append(f"{_tidy(m['title'])} — {_tidy(m['time_label'])}.")

    # 5. The other meals being cooked today, after tonight's.
    for m in by_kind.get("cook", []):
        if m is dinner_cook or m["slot"] == "dinner":
            continue
        lines.append(f"{(m['slot'] or 'Meal').capitalize()}: {_tidy(m['title'])}.")

    # 6. One thing from the attention queue, and the use-it-up nudge.
    try:
        items = _attention.get_attention_items()
    except Exception:
        logger.exception("Morning text: the attention queue failed; texting without it")
        items = []
    if items:
        summary = str(items[0].get("summary") or "").rstrip()
        if summary:
            lines.append(_tidy(summary) + ("?" if summary.endswith("?") else "."))
    if "expiring_soon" in kinds:
        lines.append(_tidy(kinds["expiring_soon"]["title"]) + ".")

    return lines


def build_morning_text(now_local: datetime | None = None) -> str | None:
    """
    The text, or None when there is nothing worth a text. `now_local` is
    the household's own clock, naive (today_moves compares naive
    timestamps); the loop passes it, tests pass what they like.

    Lines go in most-important-first and each one is kept only if the
    whole thing still fits in MAX_TEXT_CHARS with the link. The first line
    is always kept, trimmed if it must be — a text that says "Tonight:
    chicken tacos" and nothing else is still the text.
    """
    now_local = now_local or datetime.now()
    if now_local.tzinfo is not None:
        now_local = now_local.replace(tzinfo=None)
    lines = _digest_lines(now_local)
    if not lines:
        return None

    link = _app_link()
    budget = MAX_TEXT_CHARS - (len(link) + 1 if link else 0)
    kept: list[str] = []
    length = 0
    for line in lines:
        extra = len(line) + (1 if kept else 0)
        if length + extra <= budget:
            kept.append(line)
            length += extra
        elif not kept:
            kept.append(line[: max(budget - 1, 1)].rstrip() + "…")
            length = len(kept[0])
    text = " ".join(kept)
    return f"{text} {link}" if link else text


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
        if r["morning_text_on"] and r["phone"] and not _already_handled(conn, r["id"], sent_on)
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

    for r in waiting:
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
