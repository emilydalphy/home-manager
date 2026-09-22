"""
"Four dinners a week" — make it true, not merely asked for.

Emily, 2026-09-13, from her phone: "it's giving me 5 types of dinners when
I asked for 4 types for the week." Her household has dinners_per_week = 4
and the draft came back with five distinct dishes.

The count has always reached generation as one bullet in a very long
prompt (see generate_weekly_plan_llm: "counts of DISTINCT meals, not
counts of days to plan"), and — unlike `out` nights, zero counts, slot
needs, duplicates and leftover chains, every one of which has a pass in
_finish_week_slots that enforces it after the model has answered —
nothing ever read the result back against the number. The model mostly
respects it; being told is not the same as being prevented, which is the
rule every other pass in that function already exists for.

What this does: after generation, count the distinct dishes on the
period's dinners. If there are more than the household asked for, drop the
surplus dishes and put one of the KEPT dishes on each freed night as a
second cooking of it. The household ends up with exactly the number of
dishes they set, every night still fed, and the dishes that go are the
ones with the least claim to stay:

  - a dish the household asked for in their own words (derived_from.
    freeform) is never dropped — the prompt calls that the week's anchor,
    and a rule about counts must not undo a rule about their words;
  - a night somebody has already cooked is never touched (a freshly
    generated week has none, but the invariant costs nothing);
  - a dish that is part of a cook-once-eat-twice chain is dropped only as
    a last resort, and then every night of it goes together — a batch
    with nothing eating it, or a reheat of nothing, is a worse plan than
    one dish too many;
  - among the rest, the dish that first appears LATEST in the week goes
    first: the model composed the week from its start, so the early
    nights carry more of its reasoning ("lighter after burger night")
    than the tail does.

Which kept dish fills a freed night: the one whose existing nights are
furthest from it, so repeats spread out instead of landing back to back —
and never one over that night's time cap (a `rush` night, the weeknight
cap): a 120-minute braise does not land on a night tagged rush, and a
quick dish sitting on a capped night is the last to be dropped. A freed
night no kept dish fits is left as generated — one dish too many beats a
braise on a rush night (verifier, 2026-09-21).

SINCE 2026-09-21 (Emily: the counts are targets, not caps) this runs
BOTH WAYS and for EVERY count on the "Each week I plan" screen. Too few
distinct dishes: a repeated night is re-picked quietly into a new dish
(_fill_up, through the swap's own picker, on the shared call budget) —
only for a household whose counts are answers (meal_counts_set). Too
many: the fold above. Breakfasts and lunches get the same pass as
dinners (a lunch that reheats a dinner is filed under the dinner, as the
Plan tab files it); snacks are held to "snacks a day", per day
(enforce_snacks_per_day). It stands down, per slot, for a week whose own
words name that slot's number ("five different dinners this week" stands
down dinners and nothing else; "4 meals" stands down all three) — the
standing preference is the default, not a cap on what they said today.
See asks_for_a_count.

Reheat nights count under the dish they reheat, exactly as the Plan tab
groups them (shell.js reviewEatingGroups, via mealDisplayName), so the
number this enforces is the number the household sees.
"""
from __future__ import annotations

import datetime
import json
import logging
import math
import re

from ..db import get_conn
from ._shared import household_id
from . import leftovers as _leftovers
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")

# How far back "you've had this recently" reaches, in weeks. ONE constant,
# read by everything that has an opinion about repeats: the drafting
# prompt's recent_history (agent.py), plan_quality's repeat check, and the
# draft's own opening line ("Nine new dishes — nothing from the last two
# weeks", draft_opener.py). Emily, 2026-09-20: "you're continuously giving
# me the same food recommendations as previous weeks" — the window was
# three weeks and lived in three places, none of which was enforced; two
# weeks is the session's recommended default (2026-09-21), and a dish from
# inside it is only drafted again when the household asked for it.
VARIETY_WINDOW_WEEKS = 2


# ---------- "Each week I plan" numbers are targets, not caps ----------
#
# Emily, 2026-09-21. Her household: Dinners 4 · Breakfasts 3 · Lunches 3 ·
# Snacks a day 2. A four-day draft (Tue–Fri) came back with 2 distinct
# breakfasts, 4 lunches, 2 dinners: "Why isn't it following the guidelines
# we set — fix it." Two things were wrong. The counts reached the model as
# a CEILING (only "too many" was ever enforced, and only for dinners), and
# a part-week's count was rounded to nearest (4 dinners over 4 days →
# round(2.29) = 2), so the two dinners she got were, by the old rule,
# correct.
#
# ASSUMPTION, built to be flipped in one place (Emily was asked; answer
# pending): the settings are DISTINCT dishes per 7-day week, scaled to the
# days planned and rounded UP — 4 dinners on a 4-day plan = ceil(4×4/7) =
# 3 distinct dinners. "Snacks a day" is per day and never scaled. The two
# knobs below are the whole of that assumption:
#   PRORATE_TO_DAYS_PLANNED — False makes the number literal (4 dinners on
#     a 4-day plan = 4 distinct dinners, capped at the days there are);
#   PRORATE_ROUNDING — round() makes 4-over-4-days 2 again.
PRORATE_TO_DAYS_PLANNED = True
PRORATE_ROUNDING = math.ceil

# The slots a per-week count applies to, with its household_memory field.
COUNT_FIELDS = {"dinner": "dinners_per_week", "lunch": "lunches_per_week", "breakfast": "breakfasts_per_week"}


def prorate_meal_count(preference: int, day_count: int) -> int:
    """
    Scale a full-week DISTINCT-dish target to the days actually planned.

    household_memory's dinners_per_week/breakfasts_per_week/lunches_per_week
    are counts of distinct dishes across 7 days, not days to plan — "4
    dinners" means four different recipes repeated to fill the week — so
    the ratio, not the raw number, is what survives a shorter period.
    Rounded up (PRORATE_ROUNDING), floored at 1 so a real answer never
    rounds away, capped at day_count since there cannot be more distinct
    dishes than days to cook them. Zero passes through: "none, thanks" is
    handled elsewhere (_finish_week_slots's zero-count pass) and must not
    become "one, thanks". A full week (day_count >= 7) is unchanged.
    """
    if day_count >= 7 or preference <= 0:
        return preference
    if not PRORATE_TO_DAYS_PLANNED:
        return max(1, min(preference, day_count))
    prorated = PRORATE_ROUNDING(preference * day_count / 7)
    return max(1, min(int(prorated), day_count))


def count_targets(memory: dict, day_count: int) -> dict[str, int | None]:
    """{slot: the distinct-dish target for this period} for the three
    per-week counts, from the household's own settings; None where unset."""
    out: dict[str, int | None] = {}
    for slot, field in COUNT_FIELDS.items():
        value = memory.get(field)
        out[slot] = prorate_meal_count(int(value), day_count) if value is not None else None
    return out


def variety_window_words() -> str:
    """The window as a person says it: "last week", "the last two weeks"."""
    n = VARIETY_WINDOW_WEEKS
    if n == 1:
        return "last week"
    words = {v: k for k, v in _COUNT_WORDS.items()}
    return f"the last {words.get(n, n)} weeks"


_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
}
_COUNT_WORD = "|".join(_COUNT_WORDS)

# "5 different dinners", "five dinners", "three new recipes", "4 meals this
# week". A number, optionally ONE describing word from a short list, then a
# PLURAL dish word. Deliberately narrow: an earlier draft allowed any two
# words between and a singular noun, and "under 30 minutes for dinner",
# "we have 4 kids eating dinner" and "we are 5 for dinner" all matched —
# each one silently switching the whole pass off for that week. A number
# of people or minutes is not a number of dishes.
# The dish words a count can name, per slot. The generic ones ("4 meals",
# "three dishes") stand every slot down; a slot's own word stands down
# that slot alone ("just two lunches this week" is about lunches — the
# dinners are still held to their number; verifier, 2026-09-21).
_GENERIC_DISH_WORDS = "meals|dishes|recipes"
_SLOT_DISH_WORDS = {"dinner": "dinners", "lunch": "lunches", "breakfast": "breakfasts", "snack": "snacks"}


def _count_ask(nouns: str) -> re.Pattern:
    return re.compile(
        rf"\b(\d+|{_COUNT_WORD})\b"
        r"(?:\s+(?:different|new|distinct|separate|unique|fresh|proper|main|big))?"
        rf"\s+(?:{nouns})\b",
        re.IGNORECASE,
    )


_COUNT_ASK = _count_ask(f"dinners|{_GENERIC_DISH_WORDS}")
_COUNT_ASKS = {slot: _count_ask(f"{word}|{_GENERIC_DISH_WORDS}") for slot, word in _SLOT_DISH_WORDS.items()}
# Snacks are per day, and "two snacks a day" is not a count to stand down
# for — it IS the setting. Only a per-week-shaped or generic snack count
# stands the snack pass down.
_COUNT_ASKS["snack"] = _count_ask(f"snacks(?!\s+(?:a|per|each)\s+day)|{_GENERIC_DISH_WORDS}")


def asks_for_a_count(*texts: str | None, slot: str = "dinner") -> bool:
    """
    Does the household's own wording for THIS week name a number of this
    slot's dishes (or of meals/dishes generally)? If so the standing
    preference is not the last word, and the enforcing pass for that slot
    stands down rather than overrule them.
    """
    pattern = _COUNT_ASKS.get(slot, _COUNT_ASK)
    return any(pattern.search(t) for t in texts if t)


_PLURALS = {"dinner": "dinners", "lunch": "lunches", "breakfast": "breakfasts", "snack": "snacks", "dish": "dishes"}


def _display_word(n: int, noun: str) -> str:
    words = {v: k for k, v in _COUNT_WORDS.items()}
    return f"{words.get(n, n)} {noun if n == 1 else _PLURALS.get(noun, noun + 's')}"


def repeat_reason(target: int, slot: str, usual: int | None = None, day_count: int = 7) -> str:
    """
    The line under a folded repeat on the draft, and it has to be TRUE
    (verifier, 2026-09-21: a four-day plan said "you asked for two
    lunchs a week" — misspelt, and she asked for three). On a full week,
    or a period where the number wasn't scaled: "On again — you asked for
    three lunches a week." Where it was scaled: "On again — three lunches a
    week, scaled to a four-day plan."
    """
    words = {v: k for k, v in _COUNT_WORDS.items()}
    if usual is not None and usual != target and day_count < 7:
        return (f"On again — {_display_word(usual, slot)} a week, scaled to a "
                f"{words.get(day_count, day_count)}-day plan")
    return f"On again — you asked for {_display_word(target, slot)} a week"


def _load_slot_entries(plan_id: int, slot: str) -> list[dict]:
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


def _group_dishes(entries: list[dict], chains: dict) -> list[dict]:
    """
    The period's distinct dishes for one slot, in order of first
    appearance, each with the nights it covers. A reheat night is filed
    under the dish it reheats (see the module docstring).
    """
    dishes: dict[str, dict] = {}
    for e in entries:
        if e["slot_state"] != "planned" or not e["meal"]:
            continue
        reheat = chains["leftovers"].get(e["id"])
        name = reheat["source"]["meal"] if reheat else e["meal"]
        key = name.strip().lower()
        derived = json.loads(e["derived_from_json"] or "{}")
        dish = dishes.setdefault(key, {
            "name": name, "nights": [], "protected": False, "chained": False,
            "food_groups": None, "minutes": None,
        })
        dish["nights"].append(e)
        if dish["minutes"] is None and not reheat and (e.get("prep_time_minutes") or e.get("cook_time_minutes")):
            dish["minutes"] = int(e.get("prep_time_minutes") or 0) + int(e.get("cook_time_minutes") or 0)
        if (derived.get("freeform") or "").strip() or (e["cooked_status"] or "") == "done":
            dish["protected"] = True
        if reheat or e["id"] in chains["sources"]:
            dish["chained"] = True
        if dish["food_groups"] is None and not reheat:
            groups = json.loads(e["food_groups_json"] or "[]")
            if groups:
                dish["food_groups"] = groups
    return list(dishes.values())


def _fits(dish: dict, cap: int | None) -> bool:
    """Whether a dish may land on a night with this time cap. Unknown
    minutes (an older recipe with none recorded) can't be judged and are
    let through, as the plate pass lets an unknown plate through."""
    return cap is None or dish.get("minutes") is None or dish["minutes"] <= cap


def _holds_a_capped_night(dish: dict, caps: dict[str, int | None]) -> bool:
    """A dish that fits a capped night it is already on is the quick dish
    that night needs — the last to drop (verifier, 2026-09-21)."""
    return any(caps.get(n["date"]) is not None and _fits(dish, caps[n["date"]]) for n in dish["nights"])


def _pick_surplus(dishes: list[dict], target: int, caps: dict[str, int | None] | None = None) -> list[dict]:
    """
    Which dishes go, so that what stays numbers `target`. Never a protected
    dish; chained ones only after every unchained candidate is gone; a
    dish holding a capped night it fits only after those; the
    latest-starting first within each tier.
    """
    caps = caps or {}
    excess = len(dishes) - target
    unchained = [d for d in dishes if not d["protected"] and not d["chained"]]
    chained = [d for d in dishes if not d["protected"] and d["chained"]]
    # Latest first appearance first; entries are already in date order so
    # nights[0] is the first night.
    # reverse=True, so the bool is flipped: a dish on a capped night it
    # fits sorts LAST and goes only when nothing else can.
    candidates = (
        sorted(unchained, key=lambda d: (not _holds_a_capped_night(d, caps), d["nights"][0]["date"]), reverse=True)
        + sorted(chained, key=lambda d: (not _holds_a_capped_night(d, caps), d["nights"][0]["date"]), reverse=True)
    )
    return candidates[:excess]


def _spread_pick(kept: list[dict], date: str, cap: int | None = None) -> dict | None:
    """The kept dish whose nights sit furthest from `date`, among those
    that fit the night's time cap; ties to the one covering the fewest
    nights, then to the earlier dish. None when nothing kept fits."""
    day = datetime.date.fromisoformat(date)
    fitting = [(i, d) for i, d in enumerate(kept) if _fits(d, cap)]
    if not fitting:
        return None

    def distance(dish: dict) -> int:
        return min(abs((datetime.date.fromisoformat(n["date"]) - day).days) for n in dish["nights"])

    return max(fitting, key=lambda pair: (distance(pair[1]), -len(pair[1]["nights"]), -pair[0]))[1]


def enforce_distinct_count(
    plan_id: int, target: int | None, slot: str = "dinner", asks: tuple[str | None, ...] = (),
    budget=None, picker=None, fill_up: bool = True, usual: int | None = None, day_count: int = 7,
    caps: dict[str, int | None] | None = None,
) -> dict:
    """
    Make one slot's distinct dishes NUMBER `target` for this plan — see
    the module docstring for what goes, what stays and what fills the gap.
    Too many: the surplus dishes fold into repeats of the kept ones (no
    model call). Too few (Emily, 2026-09-21: the count is a target, not a
    cap): a repeated night is re-picked quietly into a dish the week does
    not have yet, through the swap's own picker against `budget`, until
    the number is met or the budget is spent — see _fill_up. `fill_up`
    False keeps that half off: the caller passes the household's
    meal_counts_set, because chasing a column default up to seven
    distinct breakfasts would spend real calls on a number nobody chose.
    `usual` and `day_count` are the household's full-week number and the
    period's length, for the repeat's line (repeat_reason); `caps` is
    {date: max minutes or None} — a rush night, the weeknight cap — and a
    repeat never lands on a night it is too long for.
    Returns {"before", "after", "replaced": [{"date", "dropped", "with"}],
    "added": [{"date", "dropped", "with"}]} and never raises: a plan with
    one dish too many is a far better outcome than a lost week, so any
    failure is logged and the plan is left as it stands.
    """
    caps = caps or {}
    result = {"before": None, "after": None, "replaced": [], "added": [], "skipped": None}
    try:
        if not target or target <= 0:
            result["skipped"] = "no target"
            return result
        if asks_for_a_count(*asks, slot=slot):
            result["skipped"] = "week asks for its own count"
            logger.info("Distinct %s count not enforced for plan %s: the week's own words name a count", slot, plan_id)
            return result
        chains = _leftovers.plan_leftover_chains(plan_id)
        dishes = _group_dishes(_load_slot_entries(plan_id, slot), chains)
        result["before"] = result["after"] = len(dishes)
        if len(dishes) < target:
            if not fill_up:
                result["skipped"] = "counts are defaults, not answers"
                return result
            result["added"] = _fill_up(plan_id, slot, dishes, target, budget, picker)
            result["after"] = result["before"] + len(result["added"])
            return result
        if len(dishes) == target:
            return result
        surplus = _pick_surplus(dishes, target, caps)
        if len(surplus) < len(dishes) - target:
            logger.warning(
                "Plan %s has %d distinct %ss against a preference of %d, but only %d can be dropped "
                "(the rest are protected); dropping what can be",
                plan_id, len(dishes), slot, target, len(surplus),
            )
        if not surplus:
            return result
        surplus_keys = {d["name"].strip().lower() for d in surplus}
        kept = [d for d in dishes if d["name"].strip().lower() not in surplus_keys]
        # Wording is an assumption (Emily's call — see the Loop Board card):
        # the line sits under the dish name on the draft, so it says only
        # why the repeat is there — and the true number (repeat_reason).
        reasoning = repeat_reason(target, slot, usual, day_count)
        left_standing = 0
        for dish in surplus:
            for night in dish["nights"]:
                fill = _spread_pick(kept, night["date"], caps.get(night["date"]))
                if fill is None:
                    # Nothing kept fits this night's cap: the dish stands.
                    left_standing += 1
                    logger.info("Plan %s: %s %s %r kept — no shorter dish fits its %s-minute cap",
                                plan_id, night["date"], slot, dish["name"], caps.get(night["date"]))
                    continue
                # ONE transaction per night, because it is the write every
                # swap in the app goes through (weekly_plan.
                # _replace_slot_entries) rather than the clear-then-plan pair
                # this used to be. Reproduced before it changed, with
                # plan_meal made to raise: the surplus night was left with NO
                # row at all and its grocery line reversed, while this
                # function swallowed the exception and reported `replaced:
                # []` — a hole in the week nothing said anything about.
                # By id, not by (date, slot): the row this loop is about is
                # already in hand, and naming it is what lets that function
                # check the delete landed before it plans anything on top.
                _weekly_plan._replace_slot_entries(
                    plan_id,
                    [night["id"]],
                    night["date"],
                    slot,
                    fill["name"],
                    food_groups=fill["food_groups"],
                    reasoning=reasoning,
                    derived_from={
                        "constraint": f"{slot}s_per_week:{target}",
                        "repeat_of": fill["nights"][0]["date"],
                        "replaced": dish["name"],
                    },
                )
                fill["nights"].append({"date": night["date"], "id": None})
                fill["nights"].sort(key=lambda n: n["date"])
                result["replaced"].append({"date": night["date"], "dropped": dish["name"], "with": fill["name"]})
        result["after"] = len(kept) + left_standing
        logger.info(
            "Plan %s came back with %d distinct %ss against a preference of %d; replaced %s",
            plan_id, len(dishes), slot, target,
            ", ".join(f"{r['date']} {r['dropped']} -> {r['with']}" for r in result["replaced"]),
        )
    except Exception:
        logger.exception("Distinct %s count enforcement failed for plan %s; leaving the plan as generated", slot, plan_id)
    return result



def _fill_up(plan_id: int, slot: str, dishes: list[dict], target: int, budget, picker=None) -> list[dict]:
    """
    Too few distinct dishes: re-pick repeated nights into new ones until
    the slot numbers `target`. The night that goes is the LAST night of
    the dish covering the most nights (its first night carries the
    model's reasoning; a later repeat is the one with the least claim),
    never a protected night (asked for by name, or cooked), never a night
    in a leftover chain (re-picking one end strands the other). Every
    dish already on the slot is on `avoid`, and a pick that lands on one
    of them anyway is refused. Each re-pick is at most
    swap_in_place.MAX_PICK_ATTEMPTS calls against the shared budget;
    when it runs dry, the week stands with fewer dishes than asked — a
    repeat is a far better outcome than an open slot.
    """
    from . import allergen_gate as _allergen_gate

    budget = budget or _allergen_gate.CallBudget()
    added: list[dict] = []
    have = {d["name"].strip().lower() for d in dishes}
    # Nights that may be re-picked, most-repeated dish first, latest night
    # first within it — recomputed each round because a re-pick changes
    # the counts.
    while len(have) < target:
        candidates = [
            d for d in dishes if len(d["nights"]) > 1 and not d["protected"] and not d["chained"]
        ]
        if not candidates:
            break
        dish = max(candidates, key=lambda d: (len(d["nights"]), d["nights"][0]["date"]))
        night = dish["nights"][-1]
        if night.get("id") is None:
            break
        entry = dict(night)
        entry["slot"] = slot
        entry["meal"] = dish["name"]
        if budget.left <= 0:
            logger.warning("Re-pick budget spent; plan %s keeps %d distinct %ss against a target of %d",
                           plan_id, len(have), slot, target)
            break
        replaced = _repick_entry(
            plan_id, entry, budget,
            avoid=sorted(d["name"] for d in dishes),
            because=f"you asked for {_display_word(target, slot)} this period, and this night was a repeat",
            reject=lambda name: name.strip().lower() in have,
            derived_key="count_repick", picker=picker,
        )
        if replaced is None:
            # Nothing usable came back for this night; don't spend the
            # rest of the budget circling it.
            dish["nights"].pop()
            dish["protected"] = True
            continue
        new_name = replaced.get("meal") or ""
        dish["nights"].pop()
        have.add(new_name.strip().lower())
        dishes.append({"name": new_name, "nights": [{"date": night["date"], "id": replaced.get("entry_id")}],
                       "protected": True, "chained": False, "food_groups": None})
        added.append({"date": night["date"], "dropped": dish["name"], "with": new_name})
        logger.info("Plan %s had too few distinct %ss (target %d): %s %r -> %r",
                    plan_id, slot, target, night["date"], dish["name"], new_name)
    return added


def _load_snacks_by_day(plan_id: int) -> dict[str, list[dict]]:
    by_day: dict[str, list[dict]] = {}
    for e in _load_slot_entries(plan_id, "snack"):
        by_day.setdefault(e["date"], []).append(e)
    return by_day


def _day_food(plan_id: int, dates: list[str]) -> dict[str, dict[str, str]]:
    """Everything planned on each day, {lowercased: as written} — what a
    snack on that day must not repeat."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.date, mpe.slot, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        """,
        (plan_id, household_id()),
    ).fetchall()
    conn.close()
    out: dict[str, dict[str, str]] = {d: {} for d in dates}
    for r in rows:
        if r["date"] in out and r["slot_state"] == "planned" and r["meal"]:
            out[r["date"]].setdefault(r["meal"].strip().lower(), r["meal"].strip())
    return out


def _nobody_home(plan_id: int, date: str) -> bool:
    """A day whose every real meal is planned_empty has nobody home; it
    gets no snacks added."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? "
        "AND date = ? AND slot IN ('breakfast', 'lunch', 'dinner') AND component_category IS NULL",
        (plan_id, household_id(), date),
    ).fetchall()
    conn.close()
    return bool(rows) and all(r["slot_state"] == "planned_empty" for r in rows)


def enforce_snacks_per_day(plan_id: int, per_day: int | None, dates: list[str], budget=None, picker=None,
                           asks: tuple[str | None, ...] = ()) -> dict:
    """
    "Snacks a day" is exact (Emily, 2026-09-21): every planned day gets
    `per_day` snack entries, no more, no fewer. Too many on a day: the
    extras go — a duplicate of that day's other snack first, then the
    latest-written — with any grocery line reversed. Too few: the gap is
    filled from the week's own snacks first (an idea from another day
    that repeats nothing on this one — no model call, and nothing the
    generation's restriction handling never saw), and only when none fits
    from one small picker call against `budget`. A day nobody is home
    for is left alone. Never raises.
    """
    from . import allergen_gate as _allergen_gate
    from . import meal_plans as _meal_plans
    from . import grocery as _grocery
    from . import plates as _plates
    from . import swap_in_place as _swap

    out = {"removed": [], "added": [], "left_short": [], "skipped": None}
    if not per_day or per_day <= 0 or not dates:
        return out
    if asks_for_a_count(*asks, slot="snack"):
        out["skipped"] = "week asks for its own count"
        logger.info("Snacks a day not enforced for plan %s: the week's own words name a snack count", plan_id)
        return out
    budget = budget or _allergen_gate.CallBudget()
    try:
        by_day = _load_snacks_by_day(plan_id)
        day_food = _day_food(plan_id, dates)
        # Too many first, so the supply the fill-up draws on is the kept one.
        for date in dates:
            snacks = [e for e in by_day.get(date, []) if e["slot_state"] == "planned" and e["meal"]]
            if len(snacks) <= per_day:
                continue
            keep: list[dict] = []
            seen: set[str] = set()
            extras: list[dict] = []
            for e in snacks:
                key = e["meal"].strip().lower()
                if key in seen:
                    extras.append(e)
                else:
                    seen.add(key)
                    keep.append(e)
            while len(keep) > per_day:
                extras.append(keep.pop())
            conn = get_conn()
            for e in extras:
                _grocery._reverse_meal_grocery_contributions(e["id"], conn=conn)
                conn.execute("DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?", (e["id"], household_id()))
                out["removed"].append({"date": date, "meal": e["meal"]})
            conn.commit()
            conn.close()
            by_day[date] = keep
            gone = {e["meal"].strip().lower() for e in extras} - {e["meal"].strip().lower() for e in keep}
            day_food[date] = {k: v for k, v in day_food[date].items() if k not in gone}
        # The week's own supply: every distinct snack, with how many days it is on.
        supply: dict[str, dict] = {}
        for date, snacks in by_day.items():
            for e in snacks:
                if e["slot_state"] != "planned" or not e["meal"]:
                    continue
                row = supply.setdefault(e["meal"].strip().lower(), {"name": e["meal"], "days": 0, "food_groups": None})
                row["days"] += 1
                if row["food_groups"] is None:
                    groups = json.loads(e["food_groups_json"] or "[]")
                    row["food_groups"] = groups or None
        for date in dates:
            snacks = [e for e in by_day.get(date, []) if e["slot_state"] == "planned" and e["meal"]]
            short = per_day - len(snacks)
            if short <= 0 or _nobody_home(plan_id, date):
                continue
            for _ in range(short):
                fits = [r for k, r in supply.items() if k not in day_food[date]]
                if fits:
                    pick = min(fits, key=lambda r: (r["days"], r["name"]))
                    name, groups, reason = pick["name"], pick["food_groups"], ""
                    pick["days"] += 1
                else:
                    name, groups, reason = _pick_a_snack(plan_id, date, sorted(day_food[date].values()), budget, picker)
                    if not name:
                        out["left_short"].append(date)
                        break
                    supply[name.strip().lower()] = {"name": name, "days": 1, "food_groups": groups}
                _meal_plans.plan_meal(
                    meal_date=date, meal=name, slot="snack",
                    food_groups=[g for g in (groups or []) if g in _plates.ALL_GROUPS] or None,
                    weekly_plan_id=plan_id, reasoning=reason,
                    derived_from={"constraint": f"snacks_per_day:{per_day}"},
                )
                day_food[date][name.strip().lower()] = name
                out["added"].append({"date": date, "meal": name})
        if out["removed"] or out["added"]:
            logger.info("Plan %s snacks a day (%d): removed %d, added %d%s", plan_id, per_day,
                        len(out["removed"]), len(out["added"]),
                        f"; still short on {', '.join(out['left_short'])}" if out["left_short"] else "")
    except Exception:
        logger.exception("Snacks-per-day enforcement failed for plan %s; the week stands as generated", plan_id)
    return out


def _pick_a_snack(plan_id: int, date: str, avoid: list[str], budget, picker=None) -> tuple[str, list | None, str]:
    """One small picker call for a snack on `date` that repeats nothing on
    that day. Returns (name, food_groups, reason), or ("", None, "") when
    nothing usable came back or the budget is spent."""
    from . import swap_in_place as _swap

    pick_one = picker or _swap._pick_replacement
    if not budget.take():
        return "", None, ""
    slot_entry = {"date": date, "slot": "snack", "meal": "", "entry_id": None}
    try:
        context = _swap.build_swap_context(plan_id, slot_entry, avoid)
        context["replacing_because"] = "this day is a snack short"
        candidate = pick_one(context) or {}
    except Exception:
        logger.exception("Snack pick for %s failed", date)
        return "", None, ""
    name = (candidate.get("meal_name") or "").strip()
    if not name or name.lower() in {a.lower() for a in avoid} or _swap.pick_gate(candidate, slot_entry):
        return "", None, ""
    candidate["meal_name"] = name
    _swap._save_recipe_if_new(candidate, _swap._table_for(date, "snack")["serves"])
    return name, candidate.get("food_groups"), (candidate.get("reason") or "").strip()

# ---------- Surprise me means new to you ----------
#
# Emily, 2026-09-21: "I put surprise me, but they seem similar to other
# suggestions I've received before … I've had all these recipes before
# through Pomona." With Surprise me as the mood, the two-week window is
# not the rule: EVERY dinner and lunch this household has had from Pomona
# — every plan, drafted or approved, all time — is handed to the drafting
# prompt as don't-repeat (surprise_context), and a dish the model sends
# anyway is re-picked quietly after generation (repick_repeats), the same
# shape as the allergen re-pick. When Surprise me is off, nothing here
# runs and the two-week window stays the default.

# The slots the no-repeat rule is about (the prompt's own words: "DINNER
# and LUNCH — not breakfast or snack").
NO_REPEAT_SLOTS = ("dinner", "lunch")

# How many past dish names the prompt is handed at most, NEWEST kept. A
# household years in has more names than a prompt should carry, and the
# ones that fall off the front are the oldest — exactly the ones the
# prompt is told to reach for first if new ideas run thin.
SURPRISE_HISTORY_CAP = 400

# Words for the prompt and the log, in one place.
SURPRISE_BECAUSE = "you asked to be surprised, and you've had it from me before"


def is_surprise_me(intake: dict | None) -> bool:
    """Did the household tap Surprise me for this week?"""
    from .week_intake import SURPRISE_MOOD
    return bool(intake) and SURPRISE_MOOD in (intake.get("moods") or [])


def household_dish_history(exclude_plan_id: int | None = None, replacing: tuple[str, str] | None = None) -> list[dict]:
    """
    Every dinner and lunch dish this household has had from Pomona, oldest
    first — every plan, drafted or approved (a drafted dish was still
    suggested to them, which is what "I've had these through Pomona"
    means), all time. One row per distinct name, at its FIRST date, with
    `recent` set when it also falls inside the two-week window (by its
    latest date). A leftovers line is not a dish. `exclude_plan_id` leaves
    one plan out — the draft being described, when this is read for its
    own opening line.

    `replacing` is (period_start, period_end) when a draft is being
    RE-PLANNED: a DRAFT's days inside that period are the draft about to
    be taken over, and the household did not "have" a draft they sent
    back — so those days are left out, and the re-pick never churns
    against them (ASSUMPTION for Emily, verifier 2026-09-21; her case: a
    Tue–Sat draft re-planned from Tuesday with Surprise me). An APPROVED
    week's days in the period still count: that food was on the list.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.freeform_meal, wp.status
        FROM meal_plan_entries mpe
        JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.slot IN (?, ?) AND mpe.slot_state = 'planned'
          AND mpe.component_category IS NULL AND mpe.weekly_plan_id != ?
          AND (mpe.recipe_id IS NOT NULL OR (mpe.freeform_meal IS NOT NULL AND mpe.freeform_meal != ''))
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (household_id(), *NO_REPEAT_SLOTS, exclude_plan_id or -1),
    ).fetchall()
    conn.close()
    if replacing:
        start, end = replacing
        rows = [r for r in rows if not (r["status"] == "draft" and start <= r["date"] <= end)]
    since = (datetime.date.today() - datetime.timedelta(weeks=VARIETY_WINDOW_WEEKS)).isoformat()
    seen: dict[str, dict] = {}
    for r in rows:
        name = (r["meal"] or "").strip()
        if not name or _LEFTOVER_LINE.search(r["freeform_meal"] or ""):
            continue
        key = name.lower()
        entry = seen.setdefault(key, {"name": name, "date": r["date"], "recent": False})
        if r["date"] >= since:
            entry["recent"] = True
    return list(seen.values())


_LEFTOVER_LINE = re.compile(r"leftovers?\b|take[\s-]?out|delivery|order in", re.IGNORECASE)


def surprise_context(intake: dict | None, period_start: str | None = None, day_count: int = 7) -> dict | None:
    """
    What the drafting prompt is handed under `surprise_me` when the mood
    is Surprise me, else None: `dont_repeat`, every dish they've had from
    Pomona (oldest first, capped at SURPRISE_HISTORY_CAP newest), and
    `never`, the ones from the last two weeks. None too when there is no
    history at all — a first week has nothing to be new against. With
    `period_start`, a draft's days inside the period being planned are
    left out — they are the draft being replaced, not food they had (see
    household_dish_history).
    """
    if not is_surprise_me(intake):
        return None
    replacing = None
    if period_start:
        end = (datetime.date.fromisoformat(period_start) + datetime.timedelta(days=max(day_count, 1) - 1)).isoformat()
        replacing = (period_start, end)
    history = household_dish_history(replacing=replacing)
    if not history:
        return None
    names = [h["name"] for h in history][-SURPRISE_HISTORY_CAP:]
    return {
        "dont_repeat": names,
        "never": [h["name"] for h in history if h["recent"]],
    }


def _repick_entry(
    plan_id: int, entry: dict, budget, *, avoid: list[str], because: str, reject,
    derived_key: str, picker=None,
) -> dict | None:
    """
    Re-pick ONE planned slot quietly through the swap's own picker — the
    allergen re-pick's shape (allergen_gate.repick_slot), for a slot that
    already has a row. Up to swap_in_place.MAX_PICK_ATTEMPTS calls against
    `budget`; `reject(name)` says whether a pick is still no good (a repeat
    of the history, say) on top of the allergen and taste gates every pick
    passes. The winner replaces the row in the one transaction every swap
    uses, with `derived_from[derived_key]` recording what it replaced and
    why. Returns the new row's dict, or None when the slot stands as it was
    — a repeat is a far better outcome than an open slot, so nothing here
    ever hands a slot back as a question.
    """
    from . import swap_in_place as _swap
    from . import plates as _plates

    pick_one = picker or _swap._pick_replacement
    slot_entry = {"date": entry["date"], "slot": entry["slot"], "meal": entry["meal"], "entry_id": entry["id"]}
    tried = _swap._dedup([entry["meal"]] + list(avoid))
    pick = None
    for attempt in range(1, _swap.MAX_PICK_ATTEMPTS + 1):
        if not budget.take():
            logger.warning("Re-pick budget spent; %s %s stays as generated", entry["date"], entry["slot"])
            return None
        try:
            context = _swap.build_swap_context(plan_id, slot_entry, tried)
            context["replacing_because"] = f"{entry['meal']} was dropped: {because}."
            candidate = pick_one(context) or {}
        except Exception:
            logger.exception("Re-pick for %s %s failed (attempt %d)", entry["date"], entry["slot"], attempt)
            candidate = {}
        name = (candidate.get("meal_name") or "").strip()
        if not name:
            return None
        candidate["meal_name"] = name
        why = _swap.pick_gate(candidate, slot_entry)
        if why is None and reject(name):
            why = "still one they've had"
        if why is None:
            pick = candidate
            break
        logger.info("Re-pick offered %r for %s %s: %s (attempt %d)", name, entry["date"], entry["slot"], why, attempt)
        tried.append(name)
    if pick is None:
        return None
    serves = _swap._table_for(entry["date"], entry["slot"])["serves"]
    pick["meal_name"] = _swap.honest_meal_name(pick)
    _swap._save_recipe_if_new(pick, serves)
    derived = dict(json.loads(entry.get("derived_from_json") or "{}") or {})
    derived[derived_key] = {"dropped": entry["meal"], "because": because}
    return _weekly_plan._replace_slot_entries(
        plan_id, [entry["id"]], entry["date"], entry["slot"], pick["meal_name"],
        food_groups=[g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS],
        reasoning=(pick.get("reason") or "").strip(),
        derived_from=derived,
    )


def repick_repeats(plan_id: int, surprise: dict | None, budget, picker=None) -> dict:
    """
    With Surprise me on: any dinner or lunch the model sent that the
    household has had from Pomona before is re-picked quietly, with the
    repeat (and every failed attempt) on `avoid`, so the draft is new to
    them rather than the opener reporting "X back from before". Left as
    generated: a dish they asked for in their own words (derived_from.
    freeform), a night already cooked, and a reheat night or the batch it
    eats from (re-picking one end of a chain strands the other). Never
    raises; returns counts for the log and for tests.
    """
    out = {"repeats": 0, "repicked": 0, "left": []}
    if not surprise:
        return out
    had = {n.strip().lower() for n in (surprise.get("dont_repeat") or [])}
    if not had:
        return out
    try:
        chains = _leftovers.plan_leftover_chains(plan_id)
        for slot in NO_REPEAT_SLOTS:
            for entry in _load_slot_entries(plan_id, slot):
                if entry["slot_state"] != "planned" or not entry["meal"]:
                    continue
                if entry["meal"].strip().lower() not in had:
                    continue
                out["repeats"] += 1
                derived = json.loads(entry["derived_from_json"] or "{}") or {}
                if (derived.get("freeform") or "").strip() or (entry["cooked_status"] or "") == "done":
                    out["left"].append(entry["meal"])
                    continue
                if entry["id"] in chains["leftovers"] or entry["id"] in chains["sources"]:
                    out["left"].append(entry["meal"])
                    continue
                replaced = _repick_entry(
                    plan_id, entry, budget,
                    avoid=[], because=SURPRISE_BECAUSE,
                    reject=lambda name: name.strip().lower() in had,
                    derived_key="surprise_repick", picker=picker,
                )
                if replaced is None:
                    out["left"].append(entry["meal"])
                else:
                    out["repicked"] += 1
                    logger.info(
                        "Surprise me: %s %s %r -> %r (had it from Pomona before)",
                        entry["date"], slot, entry["meal"], replaced.get("meal") or replaced.get("meal_name"),
                    )
        if out["left"]:
            logger.info("Surprise me: %d repeat(s) left as generated on plan %s: %s",
                        len(out["left"]), plan_id, ", ".join(out["left"]))
    except Exception:
        logger.exception("Surprise-me re-pick failed for plan %s; the week stands as generated", plan_id)
    return out
