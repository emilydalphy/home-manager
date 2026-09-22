"""
The draft's opening lines — what it did with what you told it.

Emily, 2026-09-15, walking flow 2: "the draft doesn't say what it did with
what you told it. No opening line, no reason next to a dish. Trust has to
be earned by reading twenty rows." Her 2026-09-20 round widened it: a
typed instruction has to be visibly reflected when it was used and named
when it couldn't be — never silently dropped — and the draft should say
when nothing in it is a repeat of the last two weeks.

Two short lines, built HERE from what was stored — the model's own
REPORT of what it did with the typed requests (weekly_plans.requests_json:
the requests it honoured, each with a short label, and the ones it could
not, each with a reason), the slots' derived_from, the intake and the
rows — never from a guess about the words. A line that says a request
was used when it wasn't, or wasn't when it was, costs more trust than a
line that says nothing: a request the model neither cited nor listed as
unmet gets no line at all.

  Line 1 — what it planned around, from short labels ("Mexican lunches",
           "chicken-and-potato dinners", "pizza Friday"), the days left
           free and a bigger table, ending "as you asked"; capped at
           LINE_BUDGET by dropping whole items from the end, never a
           word. With nothing special: "An ordinary week — seven dinners,
           none repeated." With items that don't fit at all: "I planned
           around what you told me."
  Line 2 — the one thing worth knowing: a slot handed back for their call;
           a request the model reported it could not honour ("I couldn’t
           fit “…” in this week"); else the novelty line over dinners and
           lunches — the slots the no-repeat rule is about — ("Nine new
           dishes — nothing from the last two weeks"), only said when
           there IS a window to compare against. Numbers are words up to
           twelve, then numerals, on both lines. With Surprise me as the
           mood the comparison is everything they've ever had from Pomona
           ("Nine new dishes — nothing you've had from me before"), since
           surprise means new to them (Emily, 2026-09-21).
  A third, only on a shorter period whose counts were scaled: "Three
           dinners this week, not four — it's a four-day plan." (count_note;
           Emily, 2026-09-21).

`asked_fact` is the sibling of this for one row: the one short fact the
dish carries beside its days ("Mexican, as asked", "packs cold"), read off
the entry's own derived_from rather than guessed from its name.

The window is meal_variety.VARIETY_WINDOW_WEEKS — one constant for the
prompt, the quality check and this line.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import meal_variety as _meal_variety
from . import week_intake as _week_intake

# About two lines of the band's 15px type at 390px. The second line is a
# short sentence by construction. Over budget, whole items go from the
# end — never a truncated phrase, never a cut word.
LINE_BUDGET = 110
FALLBACK_LINE = "I planned around what you told me."
_NOUN = {"breakfast": "breakfasts", "lunch": "lunches", "dinner": "dinners", "snack": "snacks"}

_WORD_NUMBERS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                 "nine", "ten", "eleven", "twelve"]
_WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_STOPWORDS = {
    "a", "an", "and", "the", "for", "at", "on", "in", "of", "to", "i", "we", "want", "would",
    "like", "some", "this", "that", "with", "please", "is", "it", "be", "have", "our", "my",
    "week", "night", "nights", "day", "days", "lunch", "lunches", "dinner", "dinners",
    "breakfast", "breakfasts", "snack", "snacks", "as", "asked",
}
_LEFTOVER_RE = re.compile(r"leftovers?\b|take[\s-]?out|delivery|order in", re.IGNORECASE)


def number_word(n: int) -> str:
    return _WORD_NUMBERS[n] if 0 <= n < len(_WORD_NUMBERS) else str(n)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def days_phrase(dates: list[str], period: list[str]) -> str:
    """
    "all week" when the dates are the whole period; a single weekday in
    full; runs of three or more collapsed ("Mon–Thu"); the rest listed.
    """
    dates = sorted(set(d for d in dates if d in period))
    if not dates:
        return ""
    if len(dates) == len(period) and len(period) > 1:
        return "all week" if len(period) == 7 else "every day"
    if len(dates) == 1:
        return _WEEKDAY_LONG[date.fromisoformat(dates[0]).weekday()]
    idx = [period.index(d) for d in dates]
    out: list[str] = []
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        a = _WEEKDAY_SHORT[date.fromisoformat(period[idx[i]]).weekday()]
        b = _WEEKDAY_SHORT[date.fromisoformat(period[idx[j]]).weekday()]
        out.append(f"{a}–{b}" if j - i >= 2 else (a if i == j else f"{a}, {b}"))
        i = j + 1
    return ", ".join(out)


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in _STOPWORDS}


def _cited(request: str, span: str) -> bool:
    """Did the model's quoted span come from this request? Word overlap,
    either way round, so a span that quotes half the sentence still counts."""
    a, b = _content_words(request), _content_words(span)
    if not a or not b:
        return False
    shared = len(a & b)
    return shared >= max(1, len(b) // 2) or shared >= max(1, len(a) // 2)


def _derived(entry: dict) -> dict:
    raw = entry.get("derived_from")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}") or {}
    except (TypeError, ValueError):
        return {}


def asked_fact(entry: dict) -> str | None:
    """
    The one short fact a planned row carries beside its days: what the
    household asked for that shaped it. From derived_from, never from the
    dish name — "Mexican, as asked" is only said when the cuisine input or
    their own words drove the slot.
    """
    if entry.get("slot_state", "planned") != "planned":
        return None
    d = _derived(entry)
    constraint = str(d.get("constraint") or "").lower()
    if "packed_lunch" in constraint:
        return "packs cold"
    for item in d.get("inputs") or []:
        item = str(item)
        if item.lower().startswith("cuisines:"):
            cuisine = item.split(":", 1)[1].strip().replace("_", " ")
            if cuisine:
                return f"{_cap(cuisine)}, as asked"
    if str(d.get("freeform") or "").strip():
        return "as asked"
    return None


# ---------- the two lines ----------

def _plan_entries(rows) -> list[dict]:
    out = []
    for r in rows:
        meal = r["meal"] if "meal" in r.keys() else None
        out.append({
            "date": r["date"], "slot": r["slot"], "meal": meal,
            "slot_state": r["slot_state"] or "planned",
            "derived_from": r["derived_from_json"] if "derived_from_json" in r.keys() else None,
            "freeform_meal": r["freeform_meal"] if "freeform_meal" in r.keys() else None,
        })
    return out


def _is_dish(e: dict) -> bool:
    return bool(e.get("meal")) and e.get("slot_state", "planned") == "planned" and \
        not _LEFTOVER_RE.search(e.get("freeform_meal") or "")


def _dish_names(entries: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    for e in entries:
        if _is_dish(e):
            seen.setdefault(e["meal"].strip().lower(), e["meal"].strip())
    return list(seen.values())


def recent_dish_names(period_start: str, plan_id: int | None) -> set[str] | None:
    """
    Dishes eaten in the variety window before this period, from OTHER,
    approved plans (a draft nobody approved was never last week's food).
    None when there is nothing to compare against — a first week — so the
    novelty line stays unsaid rather than claiming "nothing repeated" of a
    household with no past.
    """
    start = date.fromisoformat(period_start)
    since = (start - timedelta(weeks=_meal_variety.VARIETY_WINDOW_WEEKS)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.freeform_meal, mpe.slot_state
        FROM meal_plan_entries mpe
        JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.date >= ? AND mpe.date < ?
          AND wp.status = 'approved' AND mpe.weekly_plan_id != ?
        """,
        (household_id(), since, period_start, plan_id or -1),
    ).fetchall()
    conn.close()
    names = {
        r["meal"].strip().lower() for r in rows
        if r["meal"] and (r["slot_state"] or "planned") == "planned"
        and not _LEFTOVER_RE.search(r["freeform_meal"] or "")
    }
    return names or None


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _fallback_label(words: str, slots: set[str], single: bool = False) -> str:
    """
    A label for an honoured request the model gave no label for (an
    older plan): its content words, at most four, and the meal noun when
    one meal is named — "Mexican lunches", "chicken breast potatoes veggies
    dinners", "Friday pizza dinner" for a one-day request. Plain, never a
    cut word.
    """
    content = [w for w in re.findall(r"[A-Za-z0-9'\-]+", words) if w.lower() not in _STOPWORDS][:4]
    label = " ".join(content)
    if len(slots) == 1:
        slot = next(iter(slots))
        label = (label + " " + (slot if single else _NOUN[slot])).strip()
    return label


def _honoured_items(entries: list[dict], report: dict | None, period: list[str]) -> list[str]:
    """
    One short item per honoured request: the model's label (or a plain
    one), plus the days when it did not reach the whole week. Which
    requests count as honoured: those in the report's `honoured` list,
    or — for a plan with no report — those some slot cites in
    derived_from.freeform. A request in neither is not mentioned.
    """
    cited: dict[str, dict] = {}
    for e in entries:
        if e.get("slot_state", "planned") != "planned":
            continue
        span = str(_derived(e).get("freeform") or "").strip()
        if span:
            c = cited.setdefault(span.lower(), {"words": span, "dates": set(), "slots": set()})
            c["dates"].add(e["date"])
            c["slots"].add(e["slot"])
    honoured = (report or {}).get("honoured") or []
    if honoured:
        found = []
        for r in honoured:
            words = str(r.get("words") or "").strip()
            if not words:
                continue
            match = next((c for c in cited.values() if _cited(words, c["words"])), None)
            found.append({
                "label": str(r.get("label") or "").strip() or _fallback_label(
                    words, match["slots"] if match else set(), single=bool(match and len(match["dates"]) == 1)),
                "dates": sorted(match["dates"]) if match else [],
            })
    else:
        found = [
            {"label": _fallback_label(c["words"], c["slots"], single=len(c["dates"]) == 1), "dates": sorted(c["dates"])}
            for c in cited.values()
        ]
    items = []
    for f in found:
        if not f["label"]:
            continue
        where = days_phrase(f["dates"], period) if f["dates"] else ""
        if where and where not in ("all week", "every day") and where.lower() not in f["label"].lower():
            items.append((f["label"], where))
        else:
            items.append((f["label"], ""))
    return items


def _line_one(entries: list[dict], intake: dict | None, period: list[str], days: list[dict],
              report: dict | None) -> str:
    asked = _honoured_items(entries, report, period)
    extras: list[str] = []
    free = [d["date"] for d in days if (d.get("dinner") or {}).get("state") == "planned_empty"]
    if free:
        extras.append(f"{days_phrase(free, period)} left free")
    for d in days:
        for slot in ("breakfast", "lunch", "dinner"):
            e = d.get(slot) or {}
            if e.get("guest_count") and e.get("serves"):
                extras.append(f"{number_word(e['serves'])} for {slot} {_WEEKDAY_LONG[date.fromisoformat(d['date']).weekday()]}")
                break

    if not asked and not extras:
        dinners = [e for e in entries if e["slot"] == "dinner" and _is_dish(e)]
        distinct = len({e["meal"].strip().lower() for e in dinners})
        nights = len({e["date"] for e in dinners})
        if not dinners:
            return "Your week’s here."
        if distinct == nights:
            return f"An ordinary week — {number_word(distinct)} dinners, none repeated."
        return f"An ordinary week — {number_word(distinct)} dinners across {number_word(nights)} nights."

    def build(ask: list[tuple[str, str]], more: list[str], with_days: bool) -> str:
        parts = [f"{label} {where}".strip() if with_days else label for label, where in ask]
        if parts:
            parts[-1] = parts[-1] + ", as you asked"
        return _cap(", ".join(parts + more)) + "."

    # Over the budget: first the days go off the labels ("Mexican lunches"
    # is still true without "Mon–Thu"), then whole items from the end —
    # the extras first, then the requests. Never a cut phrase.
    ask, more, with_days = list(asked), list(extras), True
    while len(build(ask, more, with_days)) > LINE_BUDGET and (ask or more):
        if with_days:
            with_days = False
        elif more:
            more.pop()
        else:
            ask.pop()
    if not ask and not more:
        return FALLBACK_LINE
    return build(ask, more, with_days)


def _line_two(entries: list[dict], report: dict | None, recent: set[str] | None,
              surprise: bool = False) -> str:
    open_slots = [e for e in entries if e.get("slot_state") == "open"]
    if len(open_slots) == 1:
        return "One slot I’d like your call on."
    if open_slots:
        return f"{_cap(number_word(len(open_slots)))} slots I’d like your call on."
    unmet = [str(r.get("words") or "").strip() for r in ((report or {}).get("unmet") or [])]
    unmet = [u for u in unmet if u]
    if unmet:
        return f"I couldn’t fit “{unmet[0]}” in this week."
    names = _dish_names([e for e in entries if e["slot"] in ("dinner", "lunch")])
    if surprise:
        # Surprise me means new to you (Emily, 2026-09-21): the comparison
        # is everything they've ever had from Pomona, not the window — but
        # a dish they asked for by name this week ("chili again") is
        # theirs, not a repeat: line 1 already credits it, and it is
        # neither new nor "had" here (verifier, 2026-09-21).
        asked = {
            e["meal"].strip().lower() for e in entries
            if _is_dish(e) and str(_derived(e).get("freeform") or "").strip()
        }
        names = [n for n in names if n.lower() not in asked]
    if recent is None or not names:
        return ""
    back = [n for n in names if n.lower() in recent]
    new = len(names) - len(back)
    if surprise:
        if not back:
            return f"{_cap(number_word(new))} new dishes — nothing you’ve had from me before."
        if len(back) <= 2:
            return f"{_cap(number_word(new))} new dishes; {_join(back)} you’ve had from me before."
        return f"{_cap(number_word(new))} new dishes, {number_word(len(back))} you’ve had from me before."
    window = _meal_variety.variety_window_words()
    if not back:
        return f"{_cap(number_word(new))} new dishes — nothing from {window}."
    if len(back) <= 2:
        return f"{_cap(number_word(new))} new dishes; {_join(back)} back from {window}."
    return f"{_cap(number_word(new))} new dishes, {number_word(len(back))} back from {window}."


def count_note(day_count: int, memory: dict | None, said: str = "") -> str:
    """
    "Three dinners this week, not four — it's a four-day plan." Said only
    when a count on the household's "Each week I plan" screen was scaled
    to a shorter period (meal_variety.prorate_meal_count) and came out
    different; dinners when they differ, else the first meal that does.
    Nothing for a full week, a household with no counts set, or when line
    1 (`said`) already states that count ("three dinners across four
    nights") — a number said twice reads as a stammer.
    """
    if not memory or day_count >= 7:
        return ""
    for slot, field in _meal_variety.COUNT_FIELDS.items():
        usual = memory.get(field)
        if usual is None or int(usual) <= 0:
            continue
        target = _meal_variety.prorate_meal_count(int(usual), day_count)
        if target != int(usual):
            noun = _NOUN[slot] if target != 1 else slot
            if f"{number_word(target)} {noun}" in said.lower():
                return ""
            return (f"{_cap(number_word(target))} {noun} this week, not {number_word(int(usual))} — "
                    f"it’s a {number_word(day_count)}-day plan.")
    return ""


def build_opener(rows, intake: dict | None, period_start: str, day_count: int, days: list[dict],
                 plan_id: int | None = None, report: dict | None = None,
                 memory: dict | None = None) -> list[str]:
    """
    The draft's two lines, for get_week_menu. `rows` are the plan's own
    entry rows (date, slot, meal, slot_state, derived_from_json,
    freeform_meal); `days` the decorated day dicts the screen gets (their
    dinner state and headcounts are read here); `report` the stored
    account of the typed requests (weekly_plan.plan_requests); `memory`
    the household's own counts (get_household_memory), for the count note.
    """
    period = _week_intake.period_dates(period_start, day_count)
    entries = _plan_entries(rows)
    first = _line_one(entries, intake, period, days, report)
    surprise = _meal_variety.is_surprise_me(intake)
    if surprise:
        # Against everything they've had from Pomona, drafted or approved
        # (meal_variety.household_dish_history) — None with no history at
        # all, so a first week claims nothing.
        had = {h["name"].lower() for h in _meal_variety.household_dish_history(exclude_plan_id=plan_id)}
        recent = had or None
    else:
        recent = recent_dish_names(period_start, plan_id)
    second = _line_two(entries, report, recent, surprise=surprise)
    third = count_note(day_count, memory, said=first)
    return [line for line in (first, second, third) if line]
