"""
Holidays — Pomona knows one is coming and asks how the household is
spending it (Loop Board "Holidays: Pomona knows 12 October is coming and
asks how you're spending it", Emily, 2026-09-11).

Emily's framing: "it's a lot to assume that we would be making the big
meal, we might be going over to someone's house. It's more that it should
be aware of and accommodate holidays." So nothing here assumes. Three
parts:

1. KNOWING. The household's country and province (households.country /
   .province) pick a rule table; every date is computed from the rule —
   "second Monday of October", the Easter formula — so 2027 needs no code
   change. Canada is the only table so far; a US one is a second entry in
   `_RULES_BY_COUNTRY` with the same row shape. A holiday-looking all-day
   event on the household's own calendar feed counts the same way (one
   path, two sources), so a household that keeps its own list is covered
   by that list.

2. ASKING. The weekly intake shows the question for a holiday in its
   period, once, with four answers: hosting / going to someone's / just us
   / not sure yet. "Not sure yet" is a fine answer; Now asks again from
   three days out, at most once a day, until it has one. Chat records the
   same answer through `answer_holiday`.

3. ACCOMMODATING. Each answer has exactly one planning consequence, and
   every one of them reuses machinery that already exists:
   - out           -> that dinner is nobody-home in attendance (the same
                      write a trip makes), so the slot is planned empty and
                      nothing is bought for it. A dish they are bringing is
                      planned INTO that dinner slot instead — it is what
                      they cook that day — so it reaches Kitchen, Today and
                      the shopping list the way any planned meal does.
   - hosting       -> the intake's own "Hosting guests" tag and headcount
                      (week_intake.save_week_intake, which pushes the
                      number into attendance), or attendance directly when
                      no intake exists yet. The planner plans a normal
                      dinner they host. The big-meal path — menu, split
                      shop, spread prep, day-of timeline — is slice 2 and
                      reads the stored answer + headcount from here.
   - just_us       -> an ordinary day. Nothing.
   - unsure        -> nothing yet; asked again closer to the day.
   The planner is told the holiday and the answer for every day of the
   period (`generation_context`), and the plan and Now carry a quiet label
   on the day.

Nothing in this file is Thanksgiving-specific: a holiday is a date with a
name and an answer.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from ..db import get_conn
from ._shared import acting_name, household_id
from . import week_intake as _week_intake

logger = logging.getLogger(__name__)


# ---------- the answers ----------

ANSWERS = ("hosting", "out", "just_us", "unsure")

# The four answers as the household sees them. One place, so the intake
# screen, the Now card and chat all say the same words.
ANSWER_LABELS = {
    "hosting": "Hosting",
    "out": "Going to someone’s",
    "just_us": "Just us",
    "unsure": "Not sure yet",
}

# The question itself, given the holiday's name.
def question_for(name: str) -> str:
    return f"How are you spending {name}?"


# How far ahead Now starts asking again. "Not sure yet" at the intake is a
# real answer; this is when it stops being one. Three days is far enough
# out to shop for a dish and near enough that most people know by then.
REASK_DAYS_AHEAD = 3


# ---------- the rules ----------

def _easter(year: int) -> date:
    """Easter Sunday (Gregorian), the anonymous algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0) of a month — "second Monday of October"."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _weekday_on_or_before(year: int, month: int, day: int, weekday: int) -> date:
    """The last `weekday` on or before a date — Victoria Day is the Monday on or before May 24."""
    d = date(year, month, day)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


# One row per holiday: (name, rule(year) -> date, provinces, asks).
#   provinces: None = the whole country; a set = only those provinces.
#   asks: whether the intake and Now ask how the household is spending
#         it. A day people gather and cook around asks; a day that is
#         mostly a label on the calendar (Halloween, Remembrance Day, the
#         long-weekend Mondays) is shown on its day and not asked about —
#         one row to flip if a household's testers say otherwise.
_CANADA = [
    ("New Year’s Day", lambda y: date(y, 1, 1), None, True),
    # The February day, by name: the third Monday, called something
    # different in each province that keeps it. Quebec has none.
    ("Family Day", lambda y: _nth_weekday(y, 2, 0, 3), {"ON", "BC", "AB", "SK", "NB"}, False),
    ("Louis Riel Day", lambda y: _nth_weekday(y, 2, 0, 3), {"MB"}, False),
    ("Islander Day", lambda y: _nth_weekday(y, 2, 0, 3), {"PE"}, False),
    ("Heritage Day", lambda y: _nth_weekday(y, 2, 0, 3), {"NS"}, False),
    ("Good Friday", lambda y: _easter(y) - timedelta(days=2), None, False),
    ("Easter Sunday", _easter, None, True),
    ("Easter Monday", lambda y: _easter(y) + timedelta(days=1), None, False),
    ("Mother’s Day", lambda y: _nth_weekday(y, 5, 6, 2), None, True),
    ("Victoria Day", lambda y: _weekday_on_or_before(y, 5, 24, 0), None, False),
    ("Father’s Day", lambda y: _nth_weekday(y, 6, 6, 3), None, True),
    ("Canada Day", lambda y: date(y, 7, 1), None, True),
    ("Civic Holiday", lambda y: _nth_weekday(y, 8, 0, 1), None, False),
    ("Labour Day", lambda y: _nth_weekday(y, 9, 0, 1), None, False),
    ("Thanksgiving", lambda y: _nth_weekday(y, 10, 0, 2), None, True),
    ("Halloween", lambda y: date(y, 10, 31), None, False),
    ("Remembrance Day", lambda y: date(y, 11, 11), None, False),
    ("Christmas Day", lambda y: date(y, 12, 25), None, True),
    ("Boxing Day", lambda y: date(y, 12, 26), None, True),
]

# A US table is a second key here with the same row shape (Thanksgiving is
# the fourth Thursday of November, Independence Day July 4, and so on). It
# is deliberately not shipped: the beta households are Canadian, and a
# half-checked table is worse than a missing one.
_RULES_BY_COUNTRY = {"CA": _CANADA}

SUPPORTED_COUNTRIES = tuple(_RULES_BY_COUNTRY)


def rule_holidays(year: int, country: str = "CA", province: str = "") -> list[dict]:
    """
    Every holiday from the rule table for one year, in date order:
    [{"date", "name", "asks", "source": "rule"}]. An unknown country has
    no table and gets an empty list — not an error, and not Canada's list
    by default, which would put Canada Day on an American's plan.
    """
    rules = _RULES_BY_COUNTRY.get((country or "").upper())
    if not rules:
        return []
    province = (province or "").upper()
    out = []
    for name, rule, provinces, asks in rules:
        if provinces is not None and province not in provinces:
            continue
        out.append({"date": rule(year).isoformat(), "name": name, "asks": asks, "source": "rule"})
    return sorted(out, key=lambda h: h["date"])


# ---------- the household's own calendar ----------

# Words that make an all-day event on the household's calendar read as a
# holiday. Deliberately a list of holiday NAMES, not "day" or "party": a
# birthday or a PA day is context for the planner (calendar_feed already
# hands those over) but not a day to ask hosting/out/just-us about.
CALENDAR_HOLIDAY_WORDS = (
    "thanksgiving", "christmas", "xmas", "boxing day", "new year", "easter",
    "good friday", "canada day", "family day", "victoria day", "labour day",
    "labor day", "civic holiday", "remembrance day", "halloween",
    "mother's day", "mother’s day", "mothers day", "father's day", "father’s day", "fathers day",
    "hanukkah", "chanukah", "passover", "rosh hashanah", "yom kippur",
    "diwali", "eid", "ramadan", "lunar new year", "chinese new year", "nowruz",
    "vaisakhi", "kwanzaa", "st. patrick", "st patrick", "valentine",
    "independence day", "holiday",
)

_CALENDAR_WORD_RE = re.compile(
    "|".join(re.escape(w) for w in CALENDAR_HOLIDAY_WORDS), re.IGNORECASE
)


def looks_like_holiday(title: str) -> bool:
    return bool(title) and bool(_CALENDAR_WORD_RE.search(title))


def _calendar_holidays(dates: list[str]) -> list[dict]:
    """
    All-day events on the household's calendar feed whose title names a
    holiday, for the given dates. Reads through calendar_feed's own path
    (cache first, then the feed), and never raises — a calendar that can't
    be read means "no calendar holidays", not a failed screen.
    """
    if not dates:
        return []
    from .. import calendar_feed  # deferred: calendar_feed imports week_intake from this package

    try:
        info = calendar_feed.events_for_period(dates[0], len(dates))
    except Exception:
        logger.exception("Calendar holidays could not be read; continuing without them")
        return []
    if not info:
        return []
    out = []
    seen: set[str] = set()
    for ev in info.get("events") or []:
        if not ev.get("all_day") or ev["date"] not in dates or ev["date"] in seen:
            continue
        if not looks_like_holiday(ev.get("title") or ""):
            continue
        seen.add(ev["date"])
        out.append({"date": ev["date"], "name": ev["title"], "asks": True, "source": "calendar"})
    return out


# ---------- what the household knows ----------

def _region(conn=None) -> tuple[str, str]:
    own = conn is None
    conn = conn or get_conn()
    row = conn.execute(
        "SELECT country, province FROM households WHERE id = ?", (household_id(),)
    ).fetchone()
    if own:
        conn.close()
    if row is None:
        return "CA", ""
    return (row["country"] or "").upper(), (row["province"] or "").upper()


def set_holiday_region(country: str | None = None, province: str | None = None) -> dict:
    """
    Where the household is, for its holidays — "we're in BC", "we're in
    Manitoba". Two-letter codes; a province name is accepted and mapped.
    Only what's given changes. The country must be one there is a holiday
    table for (Canada so far), so nobody is silently given no holidays.
    """
    updates = {}
    if country is not None:
        code = (country or "").strip().upper()
        code = {"CANADA": "CA"}.get(code, code)
        if code not in SUPPORTED_COUNTRIES:
            raise ValueError(
                f"I only know the holidays for {', '.join(SUPPORTED_COUNTRIES)} so far, not {country!r}."
            )
        updates["country"] = code
    if province is not None:
        code = _PROVINCE_CODES.get((province or "").strip().lower(), (province or "").strip().upper())
        if code and code not in _PROVINCE_CODES.values():
            raise ValueError(f"{province!r} isn't a province or territory I know.")
        updates["province"] = code
    if updates:
        conn = get_conn()
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE households SET {sets} WHERE id = ?", (*updates.values(), household_id()))
        conn.commit()
        conn.close()
    country_now, province_now = _region()
    return {"country": country_now, "province": province_now}


_PROVINCE_CODES = {
    "alberta": "AB", "british columbia": "BC", "manitoba": "MB", "new brunswick": "NB",
    "newfoundland and labrador": "NL", "newfoundland": "NL", "nova scotia": "NS",
    "northwest territories": "NT", "nunavut": "NU", "ontario": "ON",
    "prince edward island": "PE", "pei": "PE", "quebec": "QC", "québec": "QC",
    "saskatchewan": "SK", "yukon": "YT",
}


def _answer_dict(row) -> dict:
    headcount = int(row["headcount"] or 0)
    if row["answer"] == "hosting" and not headcount:
        # Answered "hosting" without a number, then counted on the intake's
        # own guest steppers: that count lives in attendance, and it is
        # the headcount slice 2 will want, so it is the one reported.
        headcount = _dinner_guests(row["date"])
    return {
        "date": row["date"],
        "holiday_name": row["holiday_name"],
        "answer": row["answer"],
        "answer_label": ANSWER_LABELS.get(row["answer"], row["answer"]),
        "headcount": headcount,
        "bring_dish": row["bring_dish"] or "",
        "bring_dish_recipe_id": row["bring_dish_recipe_id"],
        "answered_by": row["answered_by"] or "",
        "updated_at": row["updated_at"],
    }


def _dinner_guests(date_str: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT guest_count FROM slot_attendance WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (household_id(), date_str),
    ).fetchone()
    conn.close()
    return int(row["guest_count"] or 0) if row else 0


def get_holiday_answer(date_str: str) -> dict | None:
    date.fromisoformat(date_str)
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM holiday_answers WHERE household_id = ? AND date = ?",
        (household_id(), date_str),
    ).fetchone()
    conn.close()
    return _answer_dict(row) if row else None


def _answers_between(first: str, last: str) -> dict[str, dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM holiday_answers WHERE household_id = ? AND date >= ? AND date <= ?",
        (household_id(), first, last),
    ).fetchall()
    conn.close()
    return {r["date"]: _answer_dict(r) for r in rows}


def holidays_for_dates(dates: list[str]) -> list[dict]:
    """
    The holidays on any of these dates — the rule table for the household's
    country and province, plus holiday-looking all-day events on its own
    calendar — each with the household's answer so far, in date order:

        [{"date", "name", "asks", "source", "answer": {...} | None}]

    One holiday per date. The rule table wins when both name the same day
    (a household subscribed to a public holidays calendar would otherwise
    get every holiday twice, under two spellings).
    """
    if not dates:
        return []
    for d in dates:
        date.fromisoformat(d)
    country, province = _region()
    wanted = set(dates)
    years = sorted({int(d[:4]) for d in dates})
    by_date: dict[str, dict] = {}
    for year in years:
        for h in rule_holidays(year, country, province):
            if h["date"] in wanted and h["date"] not in by_date:
                by_date[h["date"]] = h
    for h in _calendar_holidays(sorted(wanted)):
        by_date.setdefault(h["date"], h)
    if not by_date:
        return []
    answers = _answers_between(min(by_date), max(by_date))
    out = []
    for d in sorted(by_date):
        h = dict(by_date[d])
        h["answer"] = answers.get(d)
        h["question"] = question_for(h["name"])
        out.append(h)
    return out


def holidays_for_period(start_date: str, day_count: int = 7) -> list[dict]:
    """holidays_for_dates over a planning period — the intake and the plan both ask this way."""
    return holidays_for_dates(_week_intake.period_dates(start_date, day_count))


def get_upcoming_holidays(start_date: str = "", day_count: int = 60) -> list[dict]:
    """The holidays in a window from a date (today by default) — the chat's "what's coming up"."""
    start = start_date or date.today().isoformat()
    date.fromisoformat(start)
    return holidays_for_period(start, max(1, min(int(day_count or 60), 366)))


def holiday_on(date_str: str) -> dict | None:
    """The holiday on one date, with the answer, or None — Now's label."""
    found = holidays_for_dates([date_str])
    return found[0] if found else None


# ---------- answering ----------

# What each answer means to the planner, in the sentence the household
# sees when they tap it (COPY.md's acknowledgement shape: a bare "I'll…").
ANSWER_ACKS = {
    "hosting": "plan a dinner you host that day — tell me how many and I’ll shop for the bigger table.",
    "out": "plan nothing for that dinner. If you’re bringing a dish, name it and I’ll add it to the week.",
    "just_us": "plan that day like any other.",
    "unsure": "leave it for now and ask again closer to the day.",
}


def _recipe_named(dish: str) -> tuple[str, int | None]:
    """A saved recipe matching the dish, by name (case-insensitive), or the dish as typed."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, name FROM recipes WHERE household_id = ? AND lower(name) = lower(?)",
        (household_id(), dish.strip()),
    ).fetchone()
    conn.close()
    if row:
        return row["name"], row["id"]
    return dish.strip(), None


def answer_holiday(
    date_str: str,
    answer: str,
    headcount: int | None = None,
    bring_dish: str | None = None,
    answered_by: str = "",
) -> dict:
    """
    Record how the household is spending the holiday on `date_str`, and
    make the plan follow: 'out' takes that dinner off the week (and plans
    the dish they're bringing into it, if they named one), 'hosting' turns
    on the week's Hosting-guests tag with the headcount, 'just_us' and
    'unsure' leave the day ordinary. Answering again replaces the answer
    and undoes what the previous one did, so "actually we're staying home"
    puts the dinner back as a question rather than leaving it blank.

    `headcount` is EXTRA people beyond the household (the same number the
    intake's guest steppers collect). `bring_dish` is the dish by name; ''
    clears it. Both are kept only with the answer they belong to. The date
    has to be a holiday Pomona knows about (rule table or calendar) — an
    answer for an ordinary Tuesday would be a fact nothing reads.
    """
    date.fromisoformat(date_str)
    if answer not in ANSWERS:
        raise ValueError(f"answer must be one of {', '.join(ANSWERS)}, not {answer!r}.")
    holiday = holiday_on(date_str)
    if holiday is None:
        raise ValueError(f"{date_str} isn't a holiday I know about.")
    if headcount is not None:
        headcount = max(0, int(headcount))
    if headcount and answer != "hosting":
        headcount = 0
    dish_name, dish_recipe_id = "", None
    if answer == "out" and bring_dish is not None and bring_dish.strip():
        dish_name, dish_recipe_id = _recipe_named(bring_dish)

    previous = get_holiday_answer(date_str)
    if previous is not None:
        # Fields not passed are inherited within the same answer, so chat
        # can say "we're bringing the casserole" after "we're going out"
        # without restating the answer.
        if answer == previous["answer"]:
            if headcount is None:
                headcount = previous["headcount"]
            if bring_dish is None:
                dish_name, dish_recipe_id = previous["bring_dish"], previous["bring_dish_recipe_id"]
        _undo_effects(previous)
    headcount = headcount or 0

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO holiday_answers
            (household_id, date, holiday_name, answer, headcount, bring_dish, bring_dish_recipe_id, answered_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(household_id, date) DO UPDATE SET
            holiday_name = excluded.holiday_name, answer = excluded.answer,
            headcount = excluded.headcount, bring_dish = excluded.bring_dish,
            bring_dish_recipe_id = excluded.bring_dish_recipe_id,
            answered_by = excluded.answered_by, updated_at = datetime('now')
        """,
        (household_id(), date_str, holiday["name"], answer, headcount, dish_name, dish_recipe_id,
         acting_name(answered_by)),
    )
    conn.commit()
    conn.close()

    saved = get_holiday_answer(date_str)
    effects = _apply_effects(saved)
    return {
        **saved,
        "holiday": {"date": holiday["date"], "name": holiday["name"], "source": holiday["source"]},
        "ack": ANSWER_ACKS[answer],
        **effects,
    }


# ---------- the consequences ----------

# Marks this module leaves on what it writes, so it can find its own work
# again and take it back when the answer changes — and never anyone else's.
_SOURCE = "holiday"


def _out_reason(name: str) -> str:
    return f"You’re out for {name} — nothing to cook or buy."


def _dish_reason(name: str) -> str:
    return f"You’re taking this to {name}."


def _plan_for(date_str: str) -> int | None:
    from . import weekly_plan as _weekly_plan
    return _weekly_plan.get_plan_id_for_date(date_str)


def _apply_effects(saved: dict) -> dict:
    """Make the plan follow the answer. Returns what changed, for the caller's sentence."""
    from . import attendance as _attendance
    from . import slot_needs as _slot_needs

    d, name, answer = saved["date"], saved["holiday_name"], saved["answer"]
    changed: dict = {"dinner": "unchanged"}

    if answer == "out" and not saved["bring_dish"]:
        # The same fact a trip writes: nobody home for that dinner. The
        # need is set first with the holiday's own sentence, so the sync
        # attendance runs finds it already away and keeps the wording.
        try:
            att = _attendance.get_slot_attendance(d, "dinner")
            if att["explicit"] and att["nobody_home"]:
                changed["dinner"] = "already_out"  # a trip already covers it; leave its record alone
            else:
                _slot_needs.set_slot_need(d, "dinner", "away", reason=_out_reason(name))
                if att["household_size"]:
                    _attendance.set_slot_attendance(d, "dinner", present_member_ids=[], source=_SOURCE)
                changed["dinner"] = "out"
        except ValueError:
            logger.exception("Holiday 'out' could not reach attendance for %s", d)

    elif answer == "out" and saved["bring_dish"]:
        changed["dinner"] = "dish"
        changed["dish_planned"] = _plan_dish(saved)

    elif answer == "hosting":
        changed["dinner"] = "hosting"
        changed["hosting"] = _set_hosting(d, saved["headcount"])

    return changed


def _undo_effects(previous: dict) -> None:
    """Take back what the previous answer did, and only that."""
    from . import attendance as _attendance
    from . import slot_needs as _slot_needs
    from . import weekly_plan as _weekly_plan

    d, name = previous["date"], previous["holiday_name"]
    if previous["answer"] == "out" and not previous["bring_dish"]:
        att = _attendance.get_slot_attendance(d, "dinner")
        if att["explicit"] and att["source"] == _SOURCE:
            # clear_slot_attendance's sync clears the away need and hands
            # the slot back as an open question — the right state for a
            # dinner nobody has chosen yet.
            _attendance.clear_slot_attendance(d, "dinner")
        need = _slot_needs.get_slot_need(d, "dinner")
        if need["need"] == "away" and need["reason"] == _out_reason(name):
            _slot_needs.clear_slot_need(d, "dinner")
            plan_id = _plan_for(d)
            if plan_id is not None:
                _reopen(plan_id, d, name)
    elif previous["answer"] == "out" and previous["bring_dish"]:
        plan_id = _plan_for(d)
        entry = _dish_entry(plan_id, d) if plan_id is not None else None
        if entry is not None:
            _reopen(plan_id, d, name)
    elif previous["answer"] == "hosting":
        _clear_hosting(d)


def _reopen(plan_id: int, d: str, name: str) -> None:
    """The dinner slot back as a question, groceries reversed, never a blank."""
    from . import weekly_plan as _weekly_plan
    _weekly_plan.clear_plan_slot(plan_id, d, "dinner")
    _weekly_plan.plan_slot_open(
        weekly_plan_id=plan_id, meal_date=d, slot="dinner",
        open_reason=f"Plans for {name} changed — what would you like for dinner?",
        derived_from={"holiday": name, "constraint": "holiday_answer_changed"},
    )


def _dish_entry(plan_id: int, d: str):
    """The dinner entry this module planned for a brought dish, if it is still there."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, derived_from_json FROM meal_plan_entries WHERE household_id = ? AND weekly_plan_id = ? "
        "AND date = ? AND slot = 'dinner' AND slot_state = 'planned' ORDER BY id DESC LIMIT 1",
        (household_id(), plan_id, d),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    import json
    derived = json.loads(row["derived_from_json"] or "{}")
    return row if derived.get("holiday_dish") else None


def _plan_dish(saved: dict) -> bool:
    """
    Put the dish they're bringing into that day's dinner slot of the plan
    that covers it — the entity a cook, a shop and a Kitchen card already
    hang off — clearing whatever the model put there first (the answer
    wins over the model; see agent._finish_week_slots on why "clear first"
    is what makes that true). Ingredients reach the list the way any
    planned meal's do: at approval for a draft, now for an approved week.
    Returns False when no plan covers the day yet — generation calls
    apply_to_plan, which lands it then.
    """
    from . import meal_plans as _meal_plans
    from . import weekly_plan as _weekly_plan

    d, name = saved["date"], saved["holiday_name"]
    plan_id = _plan_for(d)
    if plan_id is None:
        return False
    if _dish_entry(plan_id, d) is not None:
        return True
    plan = _weekly_plan.get_weekly_plan(plan_id)
    _weekly_plan.clear_plan_slot(plan_id, d, "dinner")
    _meal_plans.plan_meal(
        d, saved["bring_dish"], slot="dinner", weekly_plan_id=plan_id,
        add_ingredients_to_grocery_list=(plan.get("status") == "approved"),
        reasoning=_dish_reason(name),
        derived_from={"holiday": name, "holiday_dish": True, "constraint": "bring_a_dish"},
    )
    return True


def _set_hosting(d: str, headcount: int) -> str:
    """
    The intake's own Hosting-guests tag with the headcount — Build 4's
    per-meal tag, reached the way the day sheet reaches it — when the week
    has an intake; the same headcount straight into attendance when it
    doesn't (the intake, when it comes, joins what attendance already
    says). Returns which of the two happened.
    """
    from . import attendance as _attendance

    intake, week_start, day_count = _intake_covering(d)
    if intake is not None:
        tags = {k: list(v) for k, v in (intake["night_tags"] or {}).items()}
        day_tags = [t for t in tags.get(d, []) if t not in ("out", "normal")]
        if "guests" not in day_tags:
            day_tags.append("guests")
        tags[d] = day_tags
        counts = dict(intake["guest_counts"] or {})
        if headcount:
            counts[d] = {"adults": headcount, "children": 0}
        _week_intake.save_week_intake(week_start, night_tags=tags, guest_counts=counts, day_count=day_count)
        return "intake_tag"
    if headcount:
        try:
            _attendance.set_guest_count(d, "dinner", headcount, source=_SOURCE)
        except ValueError:
            logger.exception("Holiday headcount could not reach attendance for %s", d)
    return "attendance"


def _clear_hosting(d: str) -> None:
    from . import attendance as _attendance

    intake, week_start, day_count = _intake_covering(d)
    if intake is not None and "guests" in (intake["night_tags"] or {}).get(d, []):
        tags = {k: [t for t in v if not (k == d and t == "guests")] for k, v in intake["night_tags"].items()}
        counts = {k: v for k, v in (intake["guest_counts"] or {}).items() if k != d}
        _week_intake.save_week_intake(week_start, night_tags=tags, guest_counts=counts, day_count=day_count)
    att = _attendance.get_slot_attendance(d, "dinner")
    if att["explicit"] and att["guest_count"]:
        _attendance.set_guest_count(d, "dinner", 0, source=att["source"] or _SOURCE)


def _intake_covering(d: str) -> tuple[dict | None, str, int]:
    """The current intake whose period holds this date: (intake, its start, its length)."""
    from . import weekly_plan as _weekly_plan

    plan_id = _plan_for(d)
    candidates: list[tuple[str, int]] = []
    if plan_id is not None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
        conn.close()
        if row is not None:
            start, days = _weekly_plan.plan_period(row)
            candidates.append((row["week_start_date"], max(days, 7)))
    monday = (date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())).isoformat()
    candidates.append((monday, 7))
    for week_start, day_count in candidates:
        intake = _week_intake.get_week_intake(week_start)
        if intake is not None and d in _week_intake.period_dates(week_start, day_count):
            return intake, week_start, day_count
    return None, monday, 7


def apply_to_plan(plan_id: int, start_date: str, day_count: int = 7) -> dict:
    """
    Land the answers on a just-generated plan — the one consequence that
    needs a plan to exist: the dish they're bringing goes into that day's
    dinner. Everything else was written when they answered (attendance,
    the intake tag) and is enforced by the passes that already run
    (apply_slot_needs_to_plan). Never raises: a week that generated fine
    must not fail over a casserole.
    """
    planned = []
    try:
        for h in holidays_for_period(start_date, day_count):
            a = h.get("answer")
            if a and a["answer"] == "out" and a["bring_dish"] and _plan_dish(a):
                planned.append({"date": h["date"], "dish": a["bring_dish"]})
    except Exception:
        logger.exception("Holiday answers could not be applied to plan %s", plan_id)
    return {"plan_id": plan_id, "dishes_planned": planned}


# ---------- what the planner and the screens read ----------

def generation_context(start_date: str, day_count: int = 7) -> list[dict]:
    """
    The `holidays` key of the generation context: one line per holiday in
    the period, with the answer and what it means for that day. Absent
    (empty) for a period with none, so nothing new reaches the prompt.
    """
    out = []
    for h in holidays_for_period(start_date, day_count):
        a = h.get("answer")
        answer = a["answer"] if a else "not_asked"
        line = {"date": h["date"], "name": h["name"], "answer": answer}
        if answer == "hosting":
            line["extra_guests"] = a["headcount"]
            line["plan"] = "a dinner they host, scaled to the bigger table; make it feel like the day"
        elif answer == "out" and a["bring_dish"]:
            line["bring_dish"] = a["bring_dish"]
            line["plan"] = "they eat dinner elsewhere; the dish they're bringing is already planned into that dinner, so send no dinner entry for this date"
        elif answer == "out":
            line["plan"] = "they eat dinner elsewhere; send no dinner entry for this date"
        elif answer == "just_us":
            line["plan"] = "an ordinary day at home; a little nicer is fine, nothing big"
        else:
            line["plan"] = "not decided yet; plan a normal dinner and keep it easy to change"
        out.append(line)
    return out


def holiday_needs_you_item(today: date | None = None) -> dict | None:
    """
    The Now card: the nearest holiday within REASK_DAYS_AHEAD that still
    has no real answer (none, or "not sure yet"), asked at most once a day
    — a "not sure yet" given today isn't asked again until tomorrow.
    """
    today = today or date.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(REASK_DAYS_AHEAD + 1)]
    for h in holidays_for_dates(dates):
        if not h["asks"]:
            continue
        a = h.get("answer")
        if a and a["answer"] != "unsure":
            continue
        if a and (a["updated_at"] or "")[:10] >= today.isoformat():
            continue
        when = _when_label(h["date"], today)
        return {
            "type": "holiday_ask",
            "kicker": "THE HOLIDAY",
            "title": f"{h['name']} is {when} — how are you spending it?",
            "urgency": "warn",
            "date": h["date"],
            "holiday_name": h["name"],
            "options": [{"answer": k, "label": v} for k, v in ANSWER_LABELS.items()],
        }
    return None


def _when_label(date_str: str, today: date) -> str:
    d = date.fromisoformat(date_str)
    days = (d - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return "on " + d.strftime("%A")


def holiday_day_label(h: dict) -> str:
    """"Thanksgiving · going to someone’s" — the quiet pill on a day."""
    a = h.get("answer")
    if a and a["answer"] != "unsure":
        label = ANSWER_LABELS[a["answer"]].lower()
        if a["answer"] == "hosting" and a["headcount"]:
            label = f"hosting · {a['headcount']} more"
        return f"{h['name']} · {label}"
    return h["name"]
