"""
The draft's opening lines — what it did with what you told it.

Emily, 2026-09-15, walking flow 2: "the draft doesn't say what it did with
what you told it. No opening line, no reason next to a dish. Trust has to
be earned by reading twenty rows." Her 2026-09-20 round widened it: a
typed instruction has to be visibly reflected when it was used and named
when it couldn't be — never silently dropped — and the draft should say
when nothing in it is a repeat of the last two weeks.

Two short lines, built HERE from what was actually stored — the intake's
answers and the plan's own rows — rather than written by the model at
generation time. A model-written summary can describe a plan it did not
make ("Mexican at lunch Mon–Thu" over a week with one Mexican lunch); a
line built from the rows cannot. The cost is plainer prose: a request is
echoed in the household's own words with the days it landed on, not
rewritten.

  Line 1 — what it planned around, in priority order and within a phone's
           two-line budget: each typed request that shaped a slot, with
           the days it reached ("Mexican for lunch Mon–Thu"); the cuisines
           they tapped; the days left free; a bigger table. With nothing
           special: "An ordinary week — seven dinners, none repeated."
  Line 2 — the one thing worth knowing: a slot handed back for their call;
           a typed request nothing used ("I couldn’t fit “…” in this
           week"); else the novelty line ("Nine new dishes — nothing from
           the last two weeks"), which is only said when there IS a
           window to compare against.

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

# The approved board's own opener (C2, 2026-09-21) is 125 characters, and
# that is the budget for the first line: about two and a half lines of
# the band's 15px type at 390px. The second line is a short sentence by
# construction. Over budget, the least important parts go first (the
# table, the free days), then the requests' own words are shortened.
LINE_BUDGET = 125

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


def _requests(freeform: str) -> list[str]:
    return [r.strip(" ,;") for r in _week_intake._REQUEST_SPLIT_RE.split(freeform or "") if r.strip(" ,;")]


def _shorten(words: str, limit: int = 6) -> str:
    parts = words.split()
    return words if len(parts) <= limit else " ".join(parts[:limit]) + "…"


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


def _line_one(entries: list[dict], intake: dict | None, period: list[str], days: list[dict]) -> tuple[str, list[str]]:
    """The first line and the requests nothing used."""
    freeform = (intake or {}).get("freeform") or ""
    requests = _requests(freeform)
    used: list[tuple[str, str]] = []
    unused: list[str] = []
    for req in requests:
        cited_dates = sorted({
            e["date"] for e in entries
            if e.get("slot_state", "planned") == "planned" and _cited(req, str(_derived(e).get("freeform") or ""))
        })
        if cited_dates:
            used.append((req, days_phrase(cited_dates, period)))
        else:
            unused.append(req)

    def asked(shorten: bool) -> str:
        return _join([f"{_shorten(r) if shorten else r} {w}".strip() for r, w in used]) + ", as you asked"

    parts: list[str] = []
    if used:
        parts.append(asked(False))
    cuisines = [c for c in ((intake or {}).get("cuisines") or []) if c and c.lower() not in freeform.lower()]
    if cuisines:
        parts.append(_join([_cap(c) for c in cuisines[:3]]) + " in the mix")
    free = [d["date"] for d in days if (d.get("dinner") or {}).get("state") == "planned_empty"]
    if free:
        parts.append(f"{days_phrase(free, period)} left free")
    for d in days:
        for slot in ("breakfast", "lunch", "dinner"):
            e = d.get(slot) or {}
            if e.get("guest_count") and e.get("serves"):
                parts.append(f"{number_word(e['serves'])} for {slot} {_WEEKDAY_LONG[date.fromisoformat(d['date']).weekday()]}")
                break

    if not parts:
        dinners = [e for e in entries if e["slot"] == "dinner" and _is_dish(e)]
        distinct = len({e["meal"].strip().lower() for e in dinners})
        nights = len({e["date"] for e in dinners})
        if not dinners:
            return "Your week’s here.", unused
        if distinct == nights:
            return f"An ordinary week — {number_word(distinct)} dinners, none repeated.", unused
        return f"An ordinary week — {number_word(distinct)} dinners across {number_word(nights)} nights.", unused

    # Trim to the budget from the least important part backwards, then
    # shorten the requests' own words if they alone run long.
    def build(ps: list[str]) -> str:
        return _cap(", ".join(ps)) + "."
    while len(build(parts)) > LINE_BUDGET and len(parts) > 1:
        parts.pop()
    if len(build(parts)) > LINE_BUDGET and used:
        parts[0] = asked(True)
    return build(parts), unused


def _line_two(entries: list[dict], unused: list[str], recent: set[str] | None) -> str:
    open_slots = [e for e in entries if e.get("slot_state") == "open"]
    if len(open_slots) == 1:
        return "One slot I’d like your call on."
    if open_slots:
        return f"{_cap(number_word(len(open_slots)))} slots I’d like your call on."
    if unused:
        return f"I couldn’t fit “{_shorten(unused[0], 8)}” in this week."
    names = _dish_names(entries)
    if recent is None or not names:
        return ""
    back = [n for n in names if n.lower() in recent]
    new = len(names) - len(back)
    window = _meal_variety.variety_window_words()
    if not back:
        return f"{_cap(number_word(new))} new dishes — nothing from {window}."
    if len(back) <= 2:
        return f"{_cap(number_word(new))} new dishes; {_join(back)} back from {window}."
    return f"{_cap(number_word(new))} new dishes, {number_word(len(back))} back from {window}."


def build_opener(rows, intake: dict | None, period_start: str, day_count: int, days: list[dict],
                 plan_id: int | None = None) -> list[str]:
    """
    The draft's two lines, for get_week_menu. `rows` are the plan's own
    entry rows (date, slot, meal, slot_state, derived_from_json,
    freeform_meal); `days` the decorated day dicts the screen gets (their
    dinner state and headcounts are read here).
    """
    period = _week_intake.period_dates(period_start, day_count)
    entries = _plan_entries(rows)
    first, unused = _line_one(entries, intake, period, days)
    second = _line_two(entries, unused, recent_dish_names(period_start, plan_id))
    return [line for line in (first, second) if line]
