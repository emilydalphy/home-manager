"""
Plan the week around what's actually on the household's calendar (Loop
Board: "Meals: plan the week around what's actually on the household's
calendar", 2026-09-11).

First version reads a calendar's private SUBSCRIBE LINK — the iCal/ICS
address Google ("Secret address in iCal format"), Apple (a public/shared
calendar link) and Outlook ("Publish calendar") all hand out — rather than
Google OAuth, which needs a Google Cloud app and Google's multi-week
sensitive-scope review. Everything downstream is the same either way:
events → per-day planning context → a reason line that names the real
commitment. Read-only by construction: this module only ever GETs.

Three parts, in the order the data flows:

1. FETCHING the feed. The link is a URL the household typed, so it goes
   through app/recipe_import.py's guarded fetcher (public addresses only,
   pinned connect, redirect and size caps, a wall-clock deadline) — one
   fetcher, not a second one drifting from the first. The link is also a
   SECRET: it grants read access to their calendar. It is stored, and
   nothing here ever logs it, puts it in an error message, hands it to a
   prompt or returns it to the client in full (see `status`).

2. PARSING the ICS text with the standard library. The format is
   line-based: folded lines, VEVENT blocks, DTSTART/DTEND as date-times
   (with a TZID, or Z for UTC, or floating), all-day DATE values, DURATION,
   RRULE (DAILY/WEEKLY with INTERVAL/COUNT/UNTIL/BYDAY; plain MONTHLY and
   YEARLY for the birthday case) expanded only inside the window asked for,
   EXDATE, RECURRENCE-ID overrides, STATUS:CANCELLED skipped. Anything
   more exotic is skipped and counted rather than guessed at. Titles are
   untrusted text: capped, control characters stripped, and labelled as
   data in the prompt.

3. TURNING events into a planning input. Per day of the period: the timed
   commitments (title, start, end), the all-day items, and one derived hint
   — `evening_busy_from`/`evening_busy_until` when a commitment eats an hour
   or more of the 5–9pm window — that the generation prompt acts on
   (quicker dinner, or cook-ahead/leftovers) and names in its reason. The
   household's own answers win on conflict: a night they tagged is theirs,
   and the calendar only ever tightens a day, never loosens one and never
   removes a meal.

A feed that can't be reached at planning time degrades to the last
successful read (a compact cache of already-parsed events) and, failing
that, to "no calendar" with a calm note — a plan is never blocked on a
calendar.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from . import recipe_import
from .db import get_conn
from .recipe_import import RecipeImportError
from .tools._shared import household_id
from .tools.week_intake import period_dates

logger = logging.getLogger(__name__)


# ---------- limits and copy ----------

MAX_FEED_BYTES = 3 * 1024 * 1024
MAX_TITLE_CHARS = 60
MAX_LABEL_CHARS = 40
MAX_EVENTS_PER_WINDOW = 300          # a day-by-day menu never needs more than this to reason with
MAX_OCCURRENCES_PER_RULE = 5000      # a daily rule from a decade ago is ~3,700 steps; this is the ceiling
MAX_RULE_INTERVAL = 1000             # every N days/weeks/months/years; anything past this is a broken feed
MAX_EXPANDED_PER_FEED = 2000         # day-instances walked per feed before it stops reading, whatever the window
CACHE_DAYS_AHEAD = 56                # what one read keeps: eight weeks, so any period a household plans is covered
FRESH_SECONDS = 30 * 60              # two generations inside half an hour read the feed once
EVENING_START = time(17, 0)
EVENING_END = time(21, 0)
EVENING_BUSY_MINUTES = 60            # an hour or more of the evening gone is what makes a dinner "quick"

# Sentences the household sees. Calm, plain, paired with the way out
# (DESIGN_SYSTEM §8); the way out is drawn by the settings card.
MSG_NOT_A_CALENDAR = "That link works, but it isn't a calendar feed — look for the iCal or ICS link."
MSG_NO_FEED = "No calendar is connected."


class CalendarFeedError(Exception):
    """A refusal or failure with a sentence the household can be shown."""

    def __init__(self, message: str, kind: str = "failed"):
        super().__init__(message)
        self.kind = kind


# ---------- the link ----------

def normalise_url(url: str) -> str:
    """Apple hands out webcal:// links; they are https underneath."""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    lowered = url.lower()
    for scheme in ("webcals://", "webcal://"):
        if lowered.startswith(scheme):
            return "https://" + url[len(scheme):]
    return url


def redact_url(url: str) -> str:
    """The most a client ever sees of a saved link: the host and the last
    few characters of the secret part, enough to recognise which link it
    was and not enough to use it."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "…"
    secret = re.sub(r"/[^/]*\.ics$", "", parts.path or "", flags=re.I).rstrip("/")
    token = secret.rsplit("/", 1)[-1]
    # At most four characters, and never more than a quarter of the token
    # itself — a short-token provider must not have most of its secret
    # printed back as a "hint".
    keep = min(4, len(token) // 4)
    tail = token[-keep:] if keep else ""
    return f"{host}/…{tail}" if tail else host


def fetch_feed(url: str) -> str:
    """GET the feed under recipe_import's rules and return its text. Every
    refusal is a CalendarFeedError with the sentence to show; the log line
    (inside fetch_text) names the host only."""
    url = normalise_url(url)
    try:
        _final, text = recipe_import.fetch_text(
            url,
            accept="text/calendar,text/plain;q=0.9,*/*;q=0.5",
            content_types=None,  # feeds arrive as text/calendar, text/plain, even octet-stream
            max_bytes=MAX_FEED_BYTES,
            label="Calendar feed",
        )
    except RecipeImportError as e:
        raise CalendarFeedError(str(e), e.kind)
    if not looks_like_ics(text):
        raise CalendarFeedError(MSG_NOT_A_CALENDAR, "not_calendar")
    return text


def looks_like_ics(text: str) -> bool:
    return bool(text) and "BEGIN:VCALENDAR" in text.lstrip("\ufeff \r\n\t")[:200].upper()


# ---------- parsing ----------

def _unfold(text: str) -> list[str]:
    """RFC 5545 line folding: a line starting with a space or tab continues
    the previous one. Handles CRLF, LF and stray CR."""
    lines: list[str] = []
    for raw in text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_line(line: str) -> tuple[str, dict, str] | None:
    """`NAME;PARAM=VALUE;PARAM2="quoted:value":the value` →
    (NAME, {PARAM: VALUE}, value). Quoted parameter values may hold ':'
    and ';', which is why this is a small state machine and not a split."""
    name, params, i, in_quotes, key, buf = "", {}, 0, False, None, []
    stage = "name"
    while i < len(line):
        c = line[i]
        if stage == "name":
            if c == ";":
                stage = "param_key"
            elif c == ":":
                stage = "value"
            else:
                name += c
        elif stage == "param_key":
            if c == "=":
                key = "".join(buf).strip().upper()
                buf = []
                stage = "param_value"
            elif c == ":":  # a parameter with no value; tolerate it
                params["".join(buf).strip().upper()] = ""
                buf = []
                stage = "value"
            elif c == ";":
                params["".join(buf).strip().upper()] = ""
                buf = []
            else:
                buf.append(c)
        elif stage == "param_value":
            if c == '"':
                in_quotes = not in_quotes
            elif c in (";", ":") and not in_quotes:
                params[key] = "".join(buf)
                buf = []
                stage = "param_key" if c == ";" else "value"
            else:
                buf.append(c)
        else:
            return name.strip().upper(), params, line[i:]
        i += 1
    if stage == "value":
        return name.strip().upper(), params, ""
    return None


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u2028\u2029]")


def clean_title(value: str, limit: int = MAX_TITLE_CHARS) -> str:
    """A title is the household's own words for a commitment — and, being
    text from a feed, untrusted. Short, one line, no control characters."""
    text = _CONTROL_RE.sub("", _unescape(value or "")).replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# Outlook publishes Windows zone names. The common ones a household in
# North America or the UK would have; anything else falls through to
# "floating", i.e. read as the household's own wall clock (see _zone).
_WINDOWS_ZONES = {
    "eastern standard time": "America/Toronto",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "pacific standard time": "America/Los_Angeles",
    "atlantic standard time": "America/Halifax",
    "newfoundland standard time": "America/St_Johns",
    "alaskan standard time": "America/Anchorage",
    "hawaiian standard time": "Pacific/Honolulu",
    "gmt standard time": "Europe/London",
    "w. europe standard time": "Europe/Berlin",
    "romance standard time": "Europe/Paris",
    "central europe standard time": "Europe/Warsaw",
    "aus eastern standard time": "Australia/Sydney",
    "india standard time": "Asia/Kolkata",
    "utc": "UTC",
}


def _zone(tzid: str | None):
    """A ZoneInfo for a TZID, or None when it can't be resolved (a Windows
    name not in the table above, a home-made VTIMEZONE id, a server with no
    zone database). None means 'read as the household's wall clock'."""
    if not tzid:
        return None
    name = tzid.strip().strip('"')
    if name.startswith("/"):  # Apple's older "/mozilla.org/..." style ids end in the IANA name
        name = name.rsplit("/", 1)[-1] if "/" in name.lstrip("/") else name.lstrip("/")
    try:
        return ZoneInfo(name)
    except Exception:
        pass
    mapped = _WINDOWS_ZONES.get(name.lower())
    if mapped:
        try:
            return ZoneInfo(mapped)
        except Exception:
            return None
    return None


def _parse_dt(value: str, params: dict) -> tuple[datetime, bool, object] | None:
    """One DTSTART/DTEND/EXDATE/RECURRENCE-ID/UNTIL value →
    (naive wall-clock datetime, is_all_day, zone). `zone` is a ZoneInfo, the
    string 'utc', or None for floating / unresolvable — the wall clock is
    kept naive throughout so a weekly 6pm practice stays 6pm across a DST
    change; the zone is applied when it is placed on the household's clock."""
    value = value.strip()
    if not value:
        return None
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            return datetime.strptime(value[:8], "%Y%m%d"), True, None
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{8})T(\d{2})(\d{2})(\d{2})?(Z?)", value)
    if not m:
        return None
    try:
        wall = datetime.strptime(m.group(1) + m.group(2) + m.group(3) + (m.group(4) or "00"), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if m.group(5):
        return wall, False, "utc"
    return wall, False, _zone(params.get("TZID"))


_DURATION_RE = re.compile(
    r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)


def _parse_duration(value: str) -> timedelta | None:
    m = _DURATION_RE.match(value.strip())
    if not m or value.strip() in ("P", "PT"):
        return None
    sign, w, d, h, mi, s = m.groups()
    try:
        delta = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    except OverflowError:
        return None  # P99999999D is not a duration anyone meant
    return -delta if sign == "-" else delta


def _to_aware(wall: datetime, zone, display_tz) -> datetime:
    """Place a wall-clock time on the household's clock."""
    if zone == "utc":
        return wall.replace(tzinfo=timezone.utc).astimezone(display_tz)
    if zone is None:
        return wall.replace(tzinfo=display_tz)
    return wall.replace(tzinfo=zone).astimezone(display_tz)


def _wall_key(wall: datetime, zone, event_zone) -> datetime:
    """EXDATE and RECURRENCE-ID values are matched against occurrences by
    wall clock in the EVENT's zone, so a UTC-written exception still lines
    up with a TZID-written rule."""
    if zone == event_zone or (zone is None) or (event_zone is None):
        return wall
    if zone == "utc":
        aware = wall.replace(tzinfo=timezone.utc)
    else:
        aware = wall.replace(tzinfo=zone)
    if event_zone == "utc":
        return aware.astimezone(timezone.utc).replace(tzinfo=None)
    return aware.astimezone(event_zone).replace(tzinfo=None)


_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_SUPPORTED_RULE_KEYS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "WKST"}


def _parse_rrule(value: str) -> dict | None:
    """RRULE → dict, or None when it uses something this parser doesn't do
    (BYSETPOS, BYMONTHDAY, BYHOUR, ordinal BYDAY like '2TU'...). Skipping a
    rule it doesn't understand beats expanding it wrong."""
    rule: dict = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        rule[k.strip().upper()] = v.strip()
    freq = rule.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return None
    if any(k not in _SUPPORTED_RULE_KEYS for k in rule):
        return None
    if "BYDAY" in rule:
        if freq not in ("DAILY", "WEEKLY"):
            return None
        days = []
        for token in rule["BYDAY"].upper().split(","):
            token = token.strip()
            if token not in _WEEKDAYS:
                return None  # an ordinal ('-1FR') is a monthly idea; not here
            days.append(_WEEKDAYS[token])
        rule["BYDAY"] = sorted(set(days))
    try:
        rule["INTERVAL"] = max(1, int(rule.get("INTERVAL", "1")))
        rule["COUNT"] = int(rule["COUNT"]) if "COUNT" in rule else None
    except ValueError:
        return None
    if rule["INTERVAL"] > MAX_RULE_INTERVAL:
        return None  # "every 2^31 weeks" is a broken feed, not a schedule
    rule["FREQ"] = freq
    if "UNTIL" in rule:
        parsed = _parse_dt(rule["UNTIL"], {})
        if not parsed:
            return None
        rule["UNTIL"] = parsed  # (wall, is_date, zone)
    else:
        rule["UNTIL"] = None
    return rule


def _add_months(d: datetime, months: int) -> datetime | None:
    month0 = d.month - 1 + months
    year, month = d.year + month0 // 12, month0 % 12 + 1
    try:
        return d.replace(year=year, month=month)
    except ValueError:
        return None  # no such day in that month (the 31st, Feb 29)


def _occurrences(start: datetime, rule: dict, is_date: bool, zone, window_end: date):
    """Yield the wall-clock starts of a recurring event, in order, up to
    the end of the window. COUNT counts every generated occurrence from
    DTSTART onward, as the spec says, so the early ones are stepped through
    rather than skipped."""
    freq, interval, count, until = rule["FREQ"], rule["INTERVAL"], rule["COUNT"], rule["UNTIL"]
    produced = 0
    steps = 0

    def past_until(wall: datetime) -> bool:
        if not until:
            return False
        u_wall, u_is_date, u_zone = until
        if u_is_date:
            return wall.date() > u_wall.date()
        if is_date:
            return wall.date() > u_wall.date()
        return _wall_key(wall, zone, u_zone if u_zone is not None else zone) > u_wall

    def emit(wall: datetime):
        nonlocal produced
        produced += 1
        return wall

    if freq == "WEEKLY":
        days = rule.get("BYDAY") or [start.weekday()]
        week0 = start - timedelta(days=start.weekday())
        n = 0
        while True:
            base = week0 + timedelta(weeks=n * interval)
            for wd in days:
                cand = base + timedelta(days=wd)
                if cand < start:
                    continue
                if past_until(cand) or cand.date() > window_end or (count is not None and produced >= count):
                    return
                yield emit(cand)
            n += 1
            steps += 1
            if steps > MAX_OCCURRENCES_PER_RULE or base.date() > window_end:
                return
    elif freq == "DAILY":
        days = rule.get("BYDAY")
        n = 0
        while True:
            cand = start + timedelta(days=n * interval)
            n += 1
            if past_until(cand) or cand.date() > window_end or (count is not None and produced >= count) or n > MAX_OCCURRENCES_PER_RULE:
                return
            if days and cand.weekday() not in days:
                continue
            yield emit(cand)
    elif freq == "MONTHLY":
        n = 0
        while True:
            cand = _add_months(start, n * interval)
            n += 1
            if n > MAX_OCCURRENCES_PER_RULE:
                return
            if cand is None:
                continue
            if past_until(cand) or cand.date() > window_end or (count is not None and produced >= count):
                return
            yield emit(cand)
    else:  # YEARLY
        n = 0
        while True:
            try:
                cand = start.replace(year=start.year + n * interval)
            except ValueError:
                cand = None  # Feb 29 in a non-leap year
            n += 1
            if n > MAX_OCCURRENCES_PER_RULE:
                return
            if cand is None:
                continue
            if past_until(cand) or cand.date() > window_end or (count is not None and produced >= count):
                return
            yield emit(cand)


def _split_components(lines: list[str]) -> tuple[dict, list[list[tuple[str, dict, str]]]]:
    """The calendar's own properties (X-WR-TIMEZONE, X-WR-CALNAME) and one
    property list per VEVENT. Anything nested inside a VEVENT (VALARM) is
    dropped; VTIMEZONE and the rest are skipped."""
    calendar: dict = {}
    events: list[list[tuple[str, dict, str]]] = []
    current: list | None = None
    depth = 0  # how deep inside a non-VEVENT component we are
    for line in lines:
        parsed = _parse_line(line)
        if not parsed:
            continue
        name, params, value = parsed
        kind = value.strip().upper() if name in ("BEGIN", "END") else ""
        if name == "BEGIN":
            if kind == "VCALENDAR":
                pass  # the root; its properties are the calendar's own
            elif kind == "VEVENT" and current is None and depth == 0:
                current = []
            else:
                depth += 1
            continue
        if name == "END":
            if kind == "VCALENDAR":
                pass
            elif depth > 0:
                depth -= 1
            elif kind == "VEVENT" and current is not None:
                events.append(current)
                current = None
            continue
        if depth > 0:
            continue
        if current is not None:
            current.append((name, params, value))
        elif name in ("X-WR-TIMEZONE", "X-WR-CALNAME"):
            calendar[name] = value
    return calendar, events


def resolve_display_zone(calendar_props: dict, event_zones: list) -> tuple[object, str]:
    """The clock event times are read in, and where it came from:
    the feed's own declared zone (Google always sets X-WR-TIMEZONE), else
    the zone most of its timed events are written in, else the
    HOUSEHOLD_TIMEZONE environment variable, else the server's local zone.
    There is no per-household timezone setting yet (see the card)."""
    declared = _zone(calendar_props.get("X-WR-TIMEZONE"))
    if declared is not None:
        return declared, "feed"
    named = Counter(str(z.key) for z in event_zones if isinstance(z, ZoneInfo))
    if named:
        return ZoneInfo(named.most_common(1)[0][0]), "events"
    env_zone = _zone(os.environ.get("HOUSEHOLD_TIMEZONE"))
    if env_zone is not None:
        return env_zone, "env"
    return datetime.now().astimezone().tzinfo or timezone.utc, "server"


def parse_ics(text: str, window_start: date, window_end: date) -> dict:
    """
    Parse a feed and return the events that touch [window_start, window_end]
    (inclusive), each on the household's clock:

        {"events": [{"date", "title", "all_day", "start", "end"}...],
         "timezone": "America/Toronto", "timezone_source": "feed",
         "calendar_name": "Family", "skipped": 2}

    `skipped` counts events this parser chose not to guess at (an RRULE it
    doesn't do, an unreadable date). A timed event that runs past midnight
    appears once per day it touches, clipped to that day.
    """
    if not looks_like_ics(text or ""):
        raise CalendarFeedError(MSG_NOT_A_CALENDAR, "not_calendar")
    calendar_props, raw_events = _split_components(_unfold(text))

    parsed_events = []
    recurrence_ids: dict[str, set] = {}
    skipped = 0
    for props in raw_events:
        ev: dict = {"exdates": [], "rrule": None, "status": "", "uid": "", "summary": "", "recurrence_id": None}
        bad = False
        for name, params, value in props:
            if name == "DTSTART":
                parsed = _parse_dt(value, params)
                if not parsed:
                    bad = True
                    break
                ev["start"], ev["all_day"], ev["zone"] = parsed
            elif name == "DTEND":
                parsed = _parse_dt(value, params)
                if parsed:
                    ev["end"] = parsed
            elif name == "DURATION":
                ev["duration"] = _parse_duration(value)
            elif name == "SUMMARY":
                ev["summary"] = clean_title(value)
            elif name == "STATUS":
                ev["status"] = value.strip().upper()
            elif name == "UID":
                ev["uid"] = value.strip()
            elif name == "RRULE":
                ev["rrule"] = value
            elif name == "EXDATE":
                for one in value.split(","):
                    parsed = _parse_dt(one, params)
                    if parsed:
                        ev["exdates"].append(parsed)
            elif name == "RECURRENCE-ID":
                parsed = _parse_dt(value, params)
                if parsed:
                    ev["recurrence_id"] = parsed
        if bad or "start" not in ev:
            skipped += 1
            continue
        if ev["status"] == "CANCELLED":
            continue
        if ev["recurrence_id"] and ev["uid"]:
            wall, _is_date, zone = ev["recurrence_id"]
            recurrence_ids.setdefault(ev["uid"], set()).add(_wall_key(wall, zone, ev["zone"]))
        parsed_events.append(ev)

    display_tz, tz_source = resolve_display_zone(
        calendar_props, [e["zone"] for e in parsed_events if not e["all_day"]]
    )

    out: list[dict] = []
    expanded = 0
    for ev in parsed_events:
        start, all_day, zone = ev["start"], ev["all_day"], ev["zone"]
        if "end" in ev and ev["end"][1] == all_day:
            duration = ev["end"][0] - start  # two datetimes: cannot overflow
        elif ev.get("duration") is not None:
            duration = ev["duration"]
        else:
            duration = timedelta(days=1) if all_day else timedelta(0)
        if duration < timedelta(0):
            duration = timedelta(0)
        if all_day and duration < timedelta(days=1):
            duration = timedelta(days=1)

        starts: list[datetime]
        if ev["rrule"]:
            rule = _parse_rrule(ev["rrule"])
            if rule is None:
                skipped += 1
                continue
            excluded = {_wall_key(w, z, zone) for (w, _d, z) in ev["exdates"]}
            excluded |= recurrence_ids.get(ev["uid"], set()) if ev["uid"] and not ev["recurrence_id"] else set()
            try:
                starts = [
                    s for s in _occurrences(start, rule, all_day, zone, window_end)
                    if s not in excluded
                ]
            except (OverflowError, ValueError):
                skipped += 1
                continue
        else:
            starts = [start]

        # Every walk below is clamped to the window: an event that runs to
        # the year 9999 (a real thing feeds do) is looked at for the days
        # asked about and no others. `expanded` counts every day-instance
        # walked across the whole feed, so a feed built to be expensive
        # stops being read rather than holding a worker thread.
        try:
            for s in starts:
                if (s + duration).date() < window_start:
                    continue
                if s.date() > window_end:
                    break
                if all_day:
                    d = max(s.date(), window_start)
                    last = min((s + duration - timedelta(days=1)).date(), window_end)
                    while d <= last and len(out) < MAX_EVENTS_PER_WINDOW and expanded < MAX_EXPANDED_PER_FEED:
                        out.append({"date": d.isoformat(), "title": ev["summary"], "all_day": True, "start": None, "end": None})
                        d += timedelta(days=1)
                        expanded += 1
                else:
                    a = _to_aware(s, zone, display_tz)
                    b = _to_aware(s + duration, zone, display_tz)
                    if b <= a:
                        b = a
                    last_date = b.date()
                    if b.time() == time(0, 0) and b.date() > a.date():
                        last_date = b.date() - timedelta(days=1)  # ends at midnight: nothing on the next day
                    d = max(a.date(), window_start)
                    last_date = min(last_date, window_end)
                    while d <= last_date and len(out) < MAX_EVENTS_PER_WINDOW and expanded < MAX_EXPANDED_PER_FEED:
                        day_start = a.strftime("%H:%M") if d == a.date() else "00:00"
                        day_end = b.strftime("%H:%M") if d == b.date() else "23:59"
                        out.append({
                            "date": d.isoformat(), "title": ev["summary"], "all_day": False,
                            "start": day_start, "end": day_end,
                        })
                        d += timedelta(days=1)
                        expanded += 1
                if len(out) >= MAX_EVENTS_PER_WINDOW or expanded >= MAX_EXPANDED_PER_FEED:
                    break
        except (OverflowError, ValueError):
            # A date arithmetic overflow (a duration or offset past year
            # 9999) is that event's problem, not the feed's: skip it.
            skipped += 1
            continue
        if len(out) >= MAX_EVENTS_PER_WINDOW or expanded >= MAX_EXPANDED_PER_FEED:
            break

    out.sort(key=lambda e: (e["date"], e["start"] or "", e["title"]))
    tz_name = getattr(display_tz, "key", None) or str(display_tz)
    return {
        "events": out,
        "timezone": tz_name,
        "timezone_source": tz_source,
        "calendar_name": clean_title(calendar_props.get("X-WR-CALNAME", ""), MAX_LABEL_CHARS),
        "skipped": skipped,
    }


# ---------- from events to a planning input ----------

def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def day_contexts(events: list[dict], dates: list[str]) -> dict[str, dict]:
    """
    {date: {"commitments": [...timed...], "all_day": [...titles...],
            "evening_busy_from": "18:00", "evening_busy_until": "19:30"}}
    for the dates that have anything on them. The evening hint appears when
    timed commitments take EVENING_BUSY_MINUTES or more out of the
    EVENING_START–EVENING_END window — the stretch a dinner gets cooked in.
    """
    wanted = set(dates)
    by_date: dict[str, dict] = {}
    for ev in events:
        if ev["date"] not in wanted:
            continue
        day = by_date.setdefault(ev["date"], {"commitments": [], "all_day": []})
        if ev["all_day"]:
            if ev["title"] and ev["title"] not in day["all_day"]:
                day["all_day"].append(ev["title"])
        else:
            day["commitments"].append({"title": ev["title"], "start": ev["start"], "end": ev["end"]})
    ev_start, ev_end = _minutes(EVENING_START.strftime("%H:%M")), _minutes(EVENING_END.strftime("%H:%M"))
    for d, day in by_date.items():
        busy_minutes, first, last = 0, None, None
        for c in day["commitments"]:
            s, e = _minutes(c["start"]), _minutes(c["end"])
            overlap = min(e, ev_end) - max(s, ev_start)
            if overlap <= 0:
                continue
            busy_minutes += overlap
            first = s if first is None else min(first, s)
            last = e if last is None else max(last, e)
        if busy_minutes >= EVENING_BUSY_MINUTES and first is not None:
            day["evening_busy_from"] = _hhmm(first)
            day["evening_busy_until"] = _hhmm(last)
            day["evening_minutes_busy"] = min(busy_minutes, ev_end - ev_start)
        if not day["all_day"]:
            del day["all_day"]
        if not day["commitments"]:
            del day["commitments"]
    return {d: day for d, day in by_date.items() if day}


# Night tags that are the household's own decision about that evening. A
# calendar hint is withheld on those dates — they already said what the
# night is — and the tag stays in `household_said` so the model can see
# why. `rush` is left off the list on purpose: it agrees with the hint.
_HOUSEHOLD_DECIDED_TAGS = ("unrushed", "out", "left", "guests", "normal")


def apply_household_answers(days: dict[str, dict], intake_ctx: dict | None, slot_needs_ctx: dict | None) -> dict[str, dict]:
    """
    The acceptance criterion made structural: self-reported attendance,
    night tags and slot needs WIN over the calendar. A date the household
    tagged, or one where nobody is home for dinner, keeps its events as
    context but loses the derived hint, and says which answer took
    precedence. The calendar only ever adds a constraint; it never removes
    a meal or reverses something the household said.
    """
    tags = (intake_ctx or {}).get("night_tags") or {}
    out_dates = set((intake_ctx or {}).get("skip_dinner_dates") or [])
    away_dinner = {
        s["date"] for s in (slot_needs_ctx or {}).get("away_slots", []) if s.get("slot") == "dinner"
    }
    for d, day in days.items():
        said = [t for t in tags.get(d, []) if t in _HOUSEHOLD_DECIDED_TAGS]
        if d in out_dates:
            said.append("out")
        if d in away_dinner:
            said.append("nobody home for dinner")
        if said and "evening_busy_from" in day:
            for key in ("evening_busy_from", "evening_busy_until", "evening_minutes_busy"):
                day.pop(key, None)
            day["household_said"] = said
    return days


# ---------- storage ----------

def _row(conn=None):
    own = conn is None
    conn = conn or get_conn()
    row = conn.execute("SELECT * FROM calendar_feeds WHERE household_id = ?", (household_id(),)).fetchone()
    if own:
        conn.close()
    return row


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _store_read(events: list[dict], cache_from: date, cache_to: date, tz_name: str, week_count: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE calendar_feeds SET last_fetched_at = ?, last_error = '', last_event_count = ?,
           cache_json = ?, cache_from = ?, cache_to = ?, timezone = ?
           WHERE household_id = ?""",
        (_now(), week_count, json.dumps(events), cache_from.isoformat(), cache_to.isoformat(), tz_name, household_id()),
    )
    conn.commit()
    conn.close()


def _store_error(message: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE calendar_feeds SET last_error = ? WHERE household_id = ?", (message, household_id()))
    conn.commit()
    conn.close()


def _coming_week_count(events: list[dict], today: date | None = None) -> int:
    today = today or date.today()
    end = today + timedelta(days=6)
    return sum(1 for e in events if today.isoformat() <= e["date"] <= end.isoformat())


def read_feed_now(url: str, window_start: date, window_end: date) -> dict:
    """Fetch and parse. Raises CalendarFeedError with a sentence to show."""
    text = fetch_feed(url)
    try:
        return parse_ics(text, window_start, window_end)
    except CalendarFeedError:
        raise
    except Exception:
        # A feed this parser chokes on is a refusal, never a 500 — and the
        # traceback is logged without the link.
        logger.exception("Calendar feed could not be parsed")
        raise CalendarFeedError(MSG_NOT_A_CALENDAR, "not_calendar")


def _describe_age(iso: str | None) -> str:
    """'this morning', 'yesterday', '3 days ago' — for the degrade note."""
    if not iso:
        return "a while ago"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "a while ago"
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - then
    if age < timedelta(hours=12):
        return "earlier today"
    days = age.days
    if days < 1:
        return "earlier today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def events_for_period(start_date: str, day_count: int, *, today: date | None = None) -> dict | None:
    """
    The events this household's calendar has for a planning period, on the
    household's clock — or None when no calendar is connected (so a
    household that connected nothing is untouched: no key, no note).

    Reads the feed unless it was read successfully in the last FRESH_SECONDS
    and that read covers the period. A read that fails falls back to the
    last successful one when it covers the period, with a note saying so;
    otherwise the result is {"unavailable": True, "note": ...}. Never
    raises: a plan is never blocked on a calendar.
    """
    row = _row()
    if row is None:
        return None
    today = today or date.today()
    dates = period_dates(start_date, day_count)
    if not dates:
        return {"events": [], "timezone": row["timezone"], "note": None, "unavailable": False}
    need_from, need_to = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])

    def cache_covers() -> bool:
        return bool(row["cache_from"] and row["cache_to"]) and row["cache_from"] <= need_from.isoformat() and row["cache_to"] >= need_to.isoformat()

    def cached_events() -> list[dict]:
        try:
            return [e for e in json.loads(row["cache_json"] or "[]") if dates[0] <= e["date"] <= dates[-1]]
        except (ValueError, TypeError, KeyError):
            return []

    if row["last_fetched_at"] and cache_covers():
        try:
            fetched = datetime.fromisoformat(row["last_fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched < timedelta(seconds=FRESH_SECONDS):
                return {"events": cached_events(), "timezone": row["timezone"], "note": None, "unavailable": False}
        except ValueError:
            pass

    window_start = min(today - timedelta(days=1), need_from)
    window_end = max(today + timedelta(days=CACHE_DAYS_AHEAD), need_to)
    try:
        parsed = read_feed_now(row["url"], window_start, window_end)
    except CalendarFeedError as e:
        _store_error(str(e))
        if cache_covers():
            return {
                "events": cached_events(),
                "timezone": row["timezone"],
                "note": f"Couldn't reach the calendar just now, so this is what it said {_describe_age(row['last_fetched_at'])}.",
                "unavailable": False,
            }
        return {
            "events": [],
            "timezone": row["timezone"],
            "note": "Couldn't reach the calendar just now, so this week is planned without it.",
            "unavailable": True,
        }
    except Exception:
        logger.exception("Calendar read failed unexpectedly")
        return {"events": [], "timezone": row["timezone"], "note": "Couldn't read the calendar just now, so this week is planned without it.", "unavailable": True}

    _store_read(parsed["events"], window_start, window_end, parsed["timezone"], _coming_week_count(parsed["events"], today))
    return {
        "events": [e for e in parsed["events"] if dates[0] <= e["date"] <= dates[-1]],
        "timezone": parsed["timezone"],
        "note": None,
        "unavailable": False,
    }


def generation_context(start_date: str, day_count: int, intake_ctx: dict | None, slot_needs_ctx: dict | None) -> dict | None:
    """
    The `calendar` key of the generation context, or None when no calendar
    is connected (the key is then simply absent — nothing new reaches the
    prompt for a household that connected nothing). Shape:

        {"timezone": "America/Toronto",
         "note": "...only when the read degraded...",
         "days": {"2026-09-15": {"commitments": [{"title","start","end"}],
                                 "all_day": ["PA day"],
                                 "evening_busy_from": "18:00",
                                 "evening_busy_until": "19:30",
                                 "evening_minutes_busy": 90}}}

    Only days with something on them appear. Never raises.
    """
    try:
        info = events_for_period(start_date, day_count)
    except Exception:
        logger.exception("Calendar context could not be built; generation continues without it")
        return None
    if info is None:
        return None
    dates = period_dates(start_date, day_count)
    days = apply_household_answers(day_contexts(info["events"], dates), intake_ctx, slot_needs_ctx)
    ctx: dict = {"timezone": info.get("timezone") or "", "days": days}
    if info.get("note"):
        ctx["note"] = info["note"]
    return ctx


# ---------- the settings surface ----------

def status() -> dict:
    """What the client is allowed to know. Never the link itself."""
    row = _row()
    if row is None:
        return {"connected": False}
    return {
        "connected": True,
        "label": row["label"],
        "link_hint": redact_url(row["url"]),
        "timezone": row["timezone"],
        "last_fetched_at": row["last_fetched_at"],
        "last_error": row["last_error"],
        "coming_week_count": row["last_event_count"],
    }


def _label_for(parsed: dict, url: str) -> str:
    name = parsed.get("calendar_name") or ""
    if name:
        return name
    try:
        return (urlsplit(normalise_url(url)).hostname or "Calendar")[:MAX_LABEL_CHARS]
    except ValueError:
        return "Calendar"


def check(url: str, *, today: date | None = None) -> dict:
    """
    "Check it": read the link once, without saving, and say what was found
    in the coming week so the household knows it worked. Raises
    CalendarFeedError with the sentence to show.
    """
    url = normalise_url(url)
    today = today or date.today()
    parsed = read_feed_now(url, today, today + timedelta(days=6))
    events = parsed["events"]
    return {
        "coming_week_count": len(events),
        "sample": [
            {"date": e["date"], "title": e["title"], "all_day": e["all_day"], "start": e["start"], "end": e["end"]}
            for e in events[:4]
        ],
        "label": _label_for(parsed, url),
        "timezone": parsed["timezone"],
        "timezone_source": parsed["timezone_source"],
        "skipped": parsed["skipped"],
    }


def connect(url: str, label: str = "", *, today: date | None = None) -> dict:
    """
    Save the link for this household (one calendar per household in this
    version; connecting again replaces it), reading it first so a link
    that doesn't work is refused rather than stored. Returns status().
    """
    url = normalise_url(url)
    today = today or date.today()
    window_start, window_end = today - timedelta(days=1), today + timedelta(days=CACHE_DAYS_AHEAD)
    parsed = read_feed_now(url, window_start, window_end)
    label = clean_title(label or "", MAX_LABEL_CHARS) or _label_for(parsed, url)
    conn = get_conn()
    conn.execute("DELETE FROM calendar_feeds WHERE household_id = ?", (household_id(),))
    conn.execute(
        """INSERT INTO calendar_feeds (household_id, url, label, timezone, last_fetched_at, last_error,
           last_event_count, cache_json, cache_from, cache_to)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?)""",
        (
            household_id(), url, label, parsed["timezone"], _now(),
            _coming_week_count(parsed["events"], today), json.dumps(parsed["events"]),
            window_start.isoformat(), window_end.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return status()


def refresh(*, today: date | None = None) -> dict:
    """"Check again" on a connected calendar: a fresh read, stored. Returns
    status() with the new count, or the stored error sentence in
    `last_error` when the read failed (the old cache is kept)."""
    row = _row()
    if row is None:
        raise CalendarFeedError(MSG_NO_FEED, "no_feed")
    today = today or date.today()
    window_start, window_end = today - timedelta(days=1), today + timedelta(days=CACHE_DAYS_AHEAD)
    try:
        parsed = read_feed_now(row["url"], window_start, window_end)
    except CalendarFeedError as e:
        _store_error(str(e))
        return status()
    _store_read(parsed["events"], window_start, window_end, parsed["timezone"], _coming_week_count(parsed["events"], today))
    return status()


def disconnect() -> dict:
    """Delete the record — link, cache, all of it. The app keeps working."""
    conn = get_conn()
    conn.execute("DELETE FROM calendar_feeds WHERE household_id = ?", (household_id(),))
    conn.commit()
    conn.close()
    return {"connected": False}
