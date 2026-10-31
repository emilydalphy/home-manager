"""
Prepped food is eaten first (Emily, 2026-10-31).

Her draft for Nov 1-7 had Egg White and Cottage Cheese Bites on Sunday and
Monday and Veggie Scrambled Eggs Tuesday to Saturday: "What's the logic
with this divide? ... I'll make the egg bites on the Sunday then can eat
them Monday - Wednesday while they're fresh. Then I can make the other
days fresh." And as the general rule: "prioritize things that are going
to be prepped on prep day for freshness."

THE RULE, for every meal. Food is prepped on the evening of a prep day and
eaten from the next day on (weekday_lunches.prep_day_for, Emily
2026-09-28), for as long as it keeps:

  breakfast    breakfast_prep.mornings_after — egg bites, frittata,
               baked oatmeal, muffins: 3 days; overnight oats: 2;
               a parfait: 1.
  lunch/dinner leftovers.MAX_LEFTOVER_DAYS (3), the rule every batch
               already keeps. No second table: a cooked batch keeps what
               a cooked portion keeps (inventory.THAWED_KEEPS_DAYS is the
               same 3). defrost.thawed_keeps_days is RAW meat thawed, a
               different question.

So a prep day's batch sits on the days right after it, inside that window,
and what only works fresh fills the days after the window. The prompt says
so (agent.py, "PREPPED FOOD IS EATEN FIRST"); keep_prepped_first makes it
true of a draft the model got wrong.

WHAT keep_prepped_first DOES, per prep date P (a prep weekday whose next
day is in the period, so the Sunday before a Monday-start week counts) and
per meal:

  1. Picks the batch: the dish the period repeats that sits most inside
     P's window (breakfast: one that keeps — breakfast_prep — and never
     for a household that makes breakfast fresh). One batch per prep day
     per meal.
  2. MOVES it there. Each of its days outside the window (later in P's
     stretch, or before the first prep day's window — Emily's Sunday) is
     traded with a different dish inside the window. A trade, never a
     re-pick: both dishes have been through every gate already, the
     counts don't change and no model is called.
  3. Breakfast only, and only from card 1: a window morning still holding
     another dish becomes the batch too, when that dish is still on
     another morning of the week — so the batch covers every morning it
     keeps and no dish leaves the week. Never the other way: a morning
     past the window is never turned INTO the batch, and a fresh dish is
     never added, so no dish ends up on more mornings than it was (the
     variety cap on desktop/variety-1031 counts a prep-day batch inside
     its window as fine).

What it never touches: a meal the household chose (meal_variety.theirs, a
named day, a holiday or guests dinner, a held thing), a meal already in a
chain (leftovers, a prepped-lunch batch), a cooked one, an open one; the
lunches of a week whose weekday lunches the household answered
(weekday_lunches decides those); a trade that breaks a meal's time cap
(`caps`, `batch_caps`) or makes three of one dish in a row
(leftovers.long_runs). No prep days: nothing at all.

Never raises: a week that stands as generated is better than no week.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import breakfast_prep as _breakfast_prep
from . import leftovers as _leftovers

logger = logging.getLogger("home_manager")

SLOTS = ("breakfast", "lunch", "dinner")

# On a breakfast this pass turned into the prep-day batch (step 3).
FILL_CONSTRAINT = "prep_day_batch"

# Night tags that are only about the clock: the caps already say what they
# mean, so a dinner on one of those nights can still trade. Any other tag
# (guests, left, out) is the household's own word about that night.
_CLOCK_TAGS = {"normal", "rush", "unrushed"}

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _iso(d: date) -> str:
    return d.isoformat()


def _derived(row) -> dict:
    try:
        d = json.loads(row["derived_from_json"] or "{}") or {}
    except (TypeError, ValueError):
        d = {}
    return d if isinstance(d, dict) else {}


def _key(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def prep_weekdays(prep_days) -> set[str]:
    """rhythm.prep_days ({"weekday", "minutes"} rows, or bare names) as
    lowercase weekday names."""
    out = set()
    for p in prep_days or []:
        name = p.get("weekday") if isinstance(p, dict) else p
        name = str(name or "").strip().lower()
        if name in _WEEKDAYS:
            out.add(name)
    return out


def prep_dates(period: list[str], prep_days) -> list[str]:
    """Every prep date whose next morning is in the period, in order — the
    day before the period included (a Sunday prep for a Monday week)."""
    names = prep_weekdays(prep_days)
    if not names or not period:
        return []
    first = date.fromisoformat(min(period)) - timedelta(days=1)
    last = date.fromisoformat(max(period)) - timedelta(days=1)
    out, d = [], first
    while d <= last:
        if _WEEKDAYS[d.weekday()] in names:
            out.append(_iso(d))
        d += timedelta(days=1)
    return out


def reach(slot: str, dish: str) -> int:
    """How many days after its prep day a batch of this dish is eaten."""
    if slot == "breakfast":
        return _breakfast_prep.mornings_after(dish)
    return _leftovers.MAX_LEFTOVER_DAYS


def window(prep_date: str, slot: str, dish: str, period: list[str]) -> list[str]:
    """The days right after the prep day this dish keeps for, in the period."""
    p = date.fromisoformat(prep_date)
    days = [_iso(p + timedelta(days=n)) for n in range(1, reach(slot, dish) + 1)]
    return [d for d in days if d in set(period)]


def _batchable(slot: str, dish: str, breakfast_prep: str | None) -> bool:
    if slot != "breakfast":
        return True
    return _breakfast_prep.may_batch_breakfast(dish, breakfast_prep)


# ---------- the post-check ----------

def _load(plan_id: int, slot: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.food_groups_json,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal,
               r.prep_time_minutes, r.cook_time_minutes
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.slot = ?
          AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (plan_id, household_id(), slot),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _minutes(row: dict) -> int | None:
    if row.get("prep_time_minutes") is None and row.get("cook_time_minutes") is None:
        return None
    return (row.get("prep_time_minutes") or 0) + (row.get("cook_time_minutes") or 0)


def _held(derived: dict) -> bool:
    inputs = derived.get("inputs") or []
    return isinstance(inputs, list) and any(str(i).startswith("held:") for i in inputs)


def _movable(row: dict, chains: dict, night_tags: dict) -> bool:
    """Whether this pass may put this meal on another day."""
    from . import cap_enforce as _cap_enforce
    from . import meal_variety as _meal_variety

    derived = _derived(row)
    if row["slot_state"] != "planned" or not (row["meal"] or "").strip():
        return False
    if (row.get("cooked_status") or "") == "done":
        return False
    if row["id"] in chains["leftovers"] or row["id"] in chains["sources"]:
        return False
    if (derived.get("links_to") or derived.get("make_double_for") or derived.get("prep_date")
            or derived.get("prep_day_cook") or derived.get(_leftovers.FROM_FREEZER_KEY)
            or derived.get(_leftovers.BATCH_KEY)):
        return False
    if _meal_variety.theirs(derived) or _held(derived):
        return False
    if (derived.get(_cap_enforce.NAMED_DAY_KEY) or derived.get(_cap_enforce.DAY_OUT_KEY)
            or derived.get("holiday_dish") or derived.get("brought_over")):
        return False
    if row["slot"] == "dinner" and set(night_tags.get(row["date"]) or []) - _CLOCK_TAGS:
        return False
    return True


def _cap(caps: dict | None, d: str, slot: str) -> int | None:
    return (caps or {}).get((d, slot)) if caps else None


def _fits(minutes: int | None, cap: int | None) -> bool:
    return not cap or minutes is None or minutes <= cap


def _runs(keys: dict) -> int:
    return len(_leftovers.long_runs(keys))


def keep_prepped_first(plan_id: int, period: list[str], intake: dict | None = None,
                       caps: dict | None = None, batch_caps: dict | None = None) -> dict:
    """
    Put each prep day's batch on the days right after it, inside how long
    it keeps, and the fresh dishes after (module docstring). `caps` is
    {(date, slot): minutes} for a dish cooked on the day (the generator's
    fill_caps), `batch_caps` the same with the batch lifts — a day the
    batch covers is a reheat, not a cook. Returns {"moved": [...],
    "filled": [...]}.
    """
    from . import rhythm as _rhythm
    from . import weekday_lunches as _weekday_lunches
    from . import plan_quality as _plan_quality

    out = {"moved": [], "filled": []}
    try:
        rhythm = _rhythm.get_household_rhythm()
        preps = prep_dates(period, rhythm.get("prep_days"))
        if not preps:
            return out
        breakfast_prep = rhythm.get("breakfast_prep")
        night_tags = (intake or {}).get("night_tags") or {}
        lunches_answered = bool(_weekday_lunches.kinds_by_date(intake))
        for slot in SLOTS:
            if slot == "breakfast" and breakfast_prep == "fresh":
                continue
            if slot == "lunch" and lunches_answered:
                continue
            _arrange_slot(plan_id, slot, period, preps, breakfast_prep, night_tags,
                          caps, batch_caps, out, _plan_quality._swap_entry_dates)
        if out["moved"] or out["filled"]:
            logger.info("Plan %s: prepped food first — moved %s; filled %s", plan_id,
                        out["moved"], out["filled"])
    except Exception:
        logger.exception("Prepped-first pass failed for plan %s; the week stands as generated", plan_id)
    return out


def _arrange_slot(plan_id, slot, period, preps, breakfast_prep, night_tags, caps, batch_caps,
                  out, swap_dates) -> None:
    from . import weekly_plan as _weekly_plan  # lazy: it imports half the package

    period_set = set(period)
    first_window_start = min(_iso(date.fromisoformat(p) + timedelta(days=1)) for p in preps)
    claimed: set[str] = set()   # days a batch now holds
    for i, prep in enumerate(preps):
        nxt = preps[i + 1] if i + 1 < len(preps) else None
        # This prep day's stretch: the mornings after it up to the next
        # prep day's (whose own evening comes after its meals).
        stretch = [d for d in period if d > prep and (nxt is None or d <= nxt)]
        # Before the first window (Emily's Sunday breakfast, ahead of the
        # Sunday prep): no batch can reach it, so a batch day there is a
        # day to bring into the window.
        before = [d for d in period if d < first_window_start] if i == 0 else []
        if not stretch:
            continue
        chains = _leftovers.plan_leftover_chains(plan_id)
        rows = _load(plan_id, slot)
        by_date: dict[str, list[dict]] = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        # One meal per day per slot, or the day is left alone.
        single = {d: rs[0] for d, rs in by_date.items() if len(rs) == 1 and d in period_set}
        movable = {d: r for d, r in single.items() if _movable(r, chains, night_tags)}

        dish = _pick_batch(slot, prep, stretch, before, single, movable, period, breakfast_prep, claimed)
        if not dish:
            continue
        win = [d for d in window(prep, slot, dish, period) if d in stretch]
        if not win:
            continue

        # 2. Move: a batch day outside the window trades with a window day
        #    holding something else. The latest strays first, into the
        #    earliest window days.
        strays = sorted((d for d in stretch + before
                         if d not in win and d not in claimed and d in movable
                         and _key(movable[d]["meal"]) == dish),
                        reverse=True)
        for w in win:
            if w in claimed or w not in movable or _key(movable[w]["meal"]) == dish:
                continue
            for s in list(strays):
                if not _trade_ok(plan_id, slot, movable[s], movable[w], s, w, win, dish, single,
                                 caps, batch_caps):
                    continue
                swap_dates(movable[s]["id"], movable[w]["id"])
                out["moved"].append({"slot": slot, "dish": movable[s]["meal"], "from": s, "to": w,
                                     "other": movable[w]["meal"], "prep_date": prep})
                movable[s], movable[w] = movable[w], movable[s]
                single[s], single[w] = movable[s], movable[w]
                movable[s]["date"], movable[w]["date"] = s, w
                strays.remove(s)
                break

        # 3. Breakfast: a make-ahead batch covers every morning it keeps.
        if slot == "breakfast" and _breakfast_prep.breakfast_keeps(_name_of(single, dish)) is True:
            source = next((single[d] for d in win if d in single and _key(single[d]["meal"]) == dish), None)
            for w in win:
                if source is None or w in claimed or w not in movable:
                    continue
                here = movable[w]
                if _key(here["meal"]) == dish:
                    continue
                elsewhere = [d for d, r in single.items() if d != w and _key(r["meal"]) == _key(here["meal"])]
                if not elsewhere:
                    continue  # it would leave the week
                if not _fits(_minutes(source), _cap(batch_caps, w, slot)):
                    continue
                try:
                    food_groups = json.loads(source.get("food_groups_json") or "null")
                except (TypeError, ValueError):
                    food_groups = None
                _weekly_plan._replace_slot_entries(
                    plan_id, [here["id"]], w, slot, source["meal"],
                    food_groups=food_groups if isinstance(food_groups, list) else None,
                    reasoning="",
                    derived_from={"constraint": FILL_CONSTRAINT, "repeat_of": source["meal"]},
                )
                out["filled"].append({"slot": slot, "date": w, "was": here["meal"], "now": source["meal"],
                                      "prep_date": prep})
                # The row is new; re-read before anything else trades it.
                fresh = [r for r in _load(plan_id, slot) if r["date"] == w]
                if len(fresh) == 1:
                    single[w] = movable[w] = fresh[0]
        claimed.update(w for w in win if w in single and _key(single[w]["meal"]) == dish)


def _name_of(single: dict, dish: str) -> str:
    """The dish's name as the plan writes it, for a key."""
    return next((r["meal"] for r in single.values() if _key(r["meal"]) == dish), dish)


def _pick_batch(slot, prep, stretch, before, single, movable, period, breakfast_prep, claimed) -> str | None:
    """
    The dish this prep day batches for this meal, or None. A dish the
    period repeats (or, at breakfast, one that keeps — one morning of egg
    bites is still a make-ahead breakfast to bring forward), whose every
    day this pass could see is free to move. Most days already in its
    window first, then most days in the stretch, then the earliest.
    """
    reachable = [d for d in stretch + before if d in single and d not in claimed]
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for d, r in single.items():
        k = _key(r["meal"])
        if r["slot_state"] == "planned" and k:
            counts[k] = counts.get(k, 0) + 1
            names.setdefault(k, r["meal"])
    best, best_score = None, None
    for k in {_key(single[d]["meal"]) for d in reachable if _key(single[d]["meal"])}:
        name = names[k]
        if not _batchable(slot, name, breakfast_prep):
            continue
        keeps = _breakfast_prep.breakfast_keeps(name) if slot == "breakfast" else None
        if counts[k] < 2 and keeps is not True:
            continue
        days = [d for d in reachable if _key(single[d]["meal"]) == k]
        # A batch with a day this pass may not move stays where the model
        # put it — half a batch moved is a worse week than none.
        if any(d not in movable for d in days):
            continue
        win = window(prep, slot, name, period)
        score = (keeps is True, sum(1 for d in days if d in win), sum(1 for d in days if d in stretch),
                 -period.index(min(days)) if min(days) in period else 0)
        if best_score is None or score > best_score:
            best, best_score = k, score
    return best


def _trade_ok(plan_id, slot, batch_row, other_row, s, w, win, dish, single, caps, batch_caps) -> bool:
    """Whether the batch day at `s` and the other dish at `w` may change
    places: each fits its new day's cap, and no dish ends up on three
    lunches and dinners in a row that wasn't already."""
    batch_days = sorted([d for d in win if d in single and _key(single[d]["meal"]) == dish] + [w])
    # The first batch day cooks; the others reheat it.
    cap_here = _cap(caps, w, slot) if w == batch_days[0] else _cap(batch_caps, w, slot)
    if not _fits(_minutes(batch_row), cap_here):
        return False
    if not _fits(_minutes(other_row), _cap(caps, s, slot)):
        return False
    if slot in _leftovers.RUN_SLOTS:
        keys = _leftovers.run_keys(plan_id)
        before = _runs(keys)
        after = dict(keys)
        a, b = keys.get((s, slot)), keys.get((w, slot))
        if a is not None:
            after[(w, slot)] = a
        else:
            after.pop((w, slot), None)
        if b is not None:
            after[(s, slot)] = b
        else:
            after.pop((s, slot), None)
        if _runs(after) > before:
            return False
    return True


# ---------- what the draft says about it ----------

def draft_batch_notes(rows, prep_days, breakfast_prep: str | None, period: list[str]) -> dict[int, str]:
    """
    {entry_id: "Made Sunday"} for each breakfast of a draft that a prep
    day's batch covers: the same dish on the mornings right after the prep
    day, inside how long it keeps, on two or more of them (one morning is
    not a batch). What the Plan row says beside its days, so "Mon–Wed ·
    Made Sunday" reads as the plan it is. `rows` carry id, date, slot,
    slot_state and meal.
    """
    notes: dict[int, str] = {}
    if breakfast_prep == "fresh":
        return notes
    by_date: dict[str, list] = {}
    for r in rows:
        if r["slot"] == "breakfast" and r["slot_state"] == "planned" and (r["meal"] or "").strip():
            by_date.setdefault(r["date"], []).append(r)
    for prep in prep_dates(period, prep_days):
        first = _iso(date.fromisoformat(prep) + timedelta(days=1))
        if len(by_date.get(first) or []) != 1:
            continue
        name = by_date[first][0]["meal"]
        if not _breakfast_prep.may_batch_breakfast(name, breakfast_prep):
            continue
        run = []
        for d in window(prep, "breakfast", name, period):
            here = by_date.get(d) or []
            if len(here) != 1 or _key(here[0]["meal"]) != _key(name):
                break
            run.append(here[0])
        if len(run) < 2:
            continue
        weekday = date.fromisoformat(prep).strftime("%A")
        for r in run:
            notes.setdefault(r["id"], f"Made {weekday}")
    return notes
