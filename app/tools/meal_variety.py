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
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.food_groups_json,
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
        result["after"] = len(kept)
        logger.info(
            "Plan %s came back with %d distinct %ss against a preference of %d; replaced %s",
            plan_id, len(dishes), slot, target,
            ", ".join(f"{r['date']} {r['dropped']} -> {r['with']}" for r in result["replaced"]),
        )
    except Exception:
        logger.exception("Distinct %s count enforcement failed for plan %s; leaving the plan as generated", slot, plan_id)
    return result


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


def household_dish_history(exclude_plan_id: int | None = None) -> list[dict]:
    """
    Every dinner and lunch dish this household has had from Pomona, oldest
    first — every plan, drafted or approved (a drafted dish was still
    suggested to them, which is what "I've had these through Pomona"
    means), all time. One row per distinct name, at its FIRST date, with
    `recent` set when it also falls inside the two-week window (by its
    latest date). A leftovers line is not a dish. `exclude_plan_id` leaves
    one plan out — the draft being described, when this is read for its
    own opening line.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.freeform_meal
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


def surprise_context(intake: dict | None) -> dict | None:
    """
    What the drafting prompt is handed under `surprise_me` when the mood
    is Surprise me, else None: `dont_repeat`, every dish they've had from
    Pomona (oldest first, capped at SURPRISE_HISTORY_CAP newest), and
    `never`, the ones from the last two weeks. None too when there is no
    history at all — a first week has nothing to be new against.
    """
    if not is_surprise_me(intake):
        return None
    history = household_dish_history()
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
