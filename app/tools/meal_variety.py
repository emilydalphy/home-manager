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
furthest from it, so repeats spread out instead of landing back to back.

WHAT THIS DELIBERATELY DOES NOT DO. It does not pull the count DOWN to
fewer dishes than asked — fewer is a different, milder failure, and
inventing a dish deterministically is not possible here. It does not
touch breakfasts, lunches or snacks: the same rule applies to them in the
prompt, but a lunch that reheats a dinner reads under the dinner's name,
so whether it "counts" as a distinct lunch is a real question, and snacks
have their own per-day repair. Dinners are the case that was reported, and
the code takes the slot as a parameter so widening it is a one-line
decision rather than a rewrite. And it stands down for a week whose own
asks name a number of dinners ("five different dinners this week") — the
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
import re

from ..db import get_conn
from ._shared import household_id
from . import leftovers as _leftovers
from . import meal_plans as _meal_plans
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")

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
_COUNT_ASK = re.compile(
    rf"\b(\d+|{_COUNT_WORD})\b"
    r"(?:\s+(?:different|new|distinct|separate|unique|fresh|proper|main|big))?"
    r"\s+(?:dinners|meals|dishes|recipes)\b",
    re.IGNORECASE,
)


def asks_for_a_count(*texts: str | None) -> bool:
    """
    Does the household's own wording for THIS week name a number of
    dinners/meals/dishes? If so the standing preference is not the last
    word, and the enforcing pass stands down rather than overrule them.
    """
    return any(_COUNT_ASK.search(t) for t in texts if t)


def _display_word(n: int, noun: str) -> str:
    words = {v: k for k, v in _COUNT_WORDS.items()}
    return f"{words.get(n, n)} {noun}{'s' if n != 1 else ''}"


def _load_slot_entries(plan_id: int, slot: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot_state, mpe.cooked_status, mpe.food_groups_json,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal
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
            "food_groups": None,
        })
        dish["nights"].append(e)
        if (derived.get("freeform") or "").strip() or (e["cooked_status"] or "") == "done":
            dish["protected"] = True
        if reheat or e["id"] in chains["sources"]:
            dish["chained"] = True
        if dish["food_groups"] is None and not reheat:
            groups = json.loads(e["food_groups_json"] or "[]")
            if groups:
                dish["food_groups"] = groups
    return list(dishes.values())


def _pick_surplus(dishes: list[dict], target: int) -> list[dict]:
    """
    Which dishes go, so that what stays numbers `target`. Never a protected
    dish; chained ones only after every unchained candidate is gone; the
    latest-starting first within each tier.
    """
    excess = len(dishes) - target
    unchained = [d for d in dishes if not d["protected"] and not d["chained"]]
    chained = [d for d in dishes if not d["protected"] and d["chained"]]
    # Latest first appearance first; entries are already in date order so
    # nights[0] is the first night.
    candidates = (
        sorted(unchained, key=lambda d: d["nights"][0]["date"], reverse=True)
        + sorted(chained, key=lambda d: d["nights"][0]["date"], reverse=True)
    )
    return candidates[:excess]


def _spread_pick(kept: list[dict], date: str) -> dict:
    """The kept dish whose nights sit furthest from `date`; ties to the one
    covering the fewest nights, then to the earlier dish."""
    day = datetime.date.fromisoformat(date)

    def distance(dish: dict) -> int:
        return min(abs((datetime.date.fromisoformat(n["date"]) - day).days) for n in dish["nights"])

    return max(
        enumerate(kept),
        key=lambda pair: (distance(pair[1]), -len(pair[1]["nights"]), -pair[0]),
    )[1]


def enforce_distinct_count(
    plan_id: int, target: int | None, slot: str = "dinner", asks: tuple[str | None, ...] = (),
) -> dict:
    """
    Cap one slot's distinct dishes at `target` for this plan — see the
    module docstring for what goes, what stays and what fills the gap.
    Returns {"before", "after", "replaced": [{"date", "dropped", "with"}]}
    and never raises: a plan with one dish too many is a far better
    outcome than a lost week, so any failure is logged and the plan is
    left as it stands.
    """
    result = {"before": None, "after": None, "replaced": [], "skipped": None}
    try:
        if not target or target <= 0:
            result["skipped"] = "no target"
            return result
        if asks_for_a_count(*asks):
            result["skipped"] = "week asks for its own count"
            logger.info("Distinct %s count not enforced for plan %s: the week's own words name a count", slot, plan_id)
            return result
        chains = _leftovers.plan_leftover_chains(plan_id)
        dishes = _group_dishes(_load_slot_entries(plan_id, slot), chains)
        result["before"] = result["after"] = len(dishes)
        if len(dishes) <= target:
            return result
        surplus = _pick_surplus(dishes, target)
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
        # why the repeat is there.
        reasoning = f"on again — you asked for {_display_word(target, slot)} a week"
        for dish in surplus:
            for night in dish["nights"]:
                fill = _spread_pick(kept, night["date"])
                _weekly_plan.clear_plan_slot(plan_id, night["date"], slot)
                _meal_plans.plan_meal(
                    meal_date=night["date"],
                    meal=fill["name"],
                    slot=slot,
                    food_groups=fill["food_groups"],
                    weekly_plan_id=plan_id,
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
        result["after"] = len(kept)
        logger.info(
            "Plan %s came back with %d distinct %ss against a preference of %d; replaced %s",
            plan_id, len(dishes), slot, target,
            ", ".join(f"{r['date']} {r['dropped']} -> {r['with']}" for r in result["replaced"]),
        )
    except Exception:
        logger.exception("Distinct %s count enforcement failed for plan %s; leaving the plan as generated", slot, plan_id)
    return result
