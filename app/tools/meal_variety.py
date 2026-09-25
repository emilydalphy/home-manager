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
surplus dishes and feed each freed night from a KEPT dish's batch — as its
leftovers, never as a second cooking of it (Emily, 2026-09-23, "Decision
E"; see "fewer recipes than meals means batch cooking" below). The
household ends up with exactly the number of dishes they set, every night
still fed, and the dishes that go are the ones with the least claim to
stay:

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
    than the tail does; a quick dish sitting on a capped night (a `rush`
    night, the weeknight cap) is the last to be dropped.

Until 2026-09-23 a freed night got the kept dish whose nights were
FURTHEST from it, as an independent second cook — bought and cooked twice,
and, because a dish they asked for is kept, sometimes a second cooking of
the very dish they asked for once (prod plan 61, a second Korean Chicken
Pancake). That is what the batch rules below replace.

SINCE 2026-09-21 (Emily: the counts are targets, not caps) this runs
BOTH WAYS and for EVERY count on the "Each week I plan" screen. Too few
distinct dishes: a repeated night is re-picked quietly into a new dish
(_fill_up, through the swap's own picker, on the shared call budget) —
only for a household whose counts are answers (meal_counts_set). Too
many: the fold above. Either way, a slot with fewer recipes than meals
is batch cooked (below). Breakfasts and lunches get the same pass as
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
        derived = json.loads(e["derived_from_json"] or "{}")
        # A night eating a portion frozen on an earlier cook is filed under
        # that dish too (Emily, 2026-09-23) — it is not a new recipe.
        frozen = derived.get(_leftovers.FROM_FREEZER_KEY)
        frozen = frozen if isinstance(frozen, dict) and (frozen.get("dish") or "").strip() else None
        name = reheat["source"]["meal"] if reheat else (frozen["dish"] if frozen else e["meal"])
        key = name.strip().lower()
        dish = dishes.setdefault(key, {
            "name": name, "nights": [], "protected": False, "chained": False,
            "food_groups": None, "minutes": None,
        })
        dish["nights"].append(e)
        if dish["minutes"] is None and not reheat and (e.get("prep_time_minutes") or e.get("cook_time_minutes")):
            dish["minutes"] = int(e.get("prep_time_minutes") or 0) + int(e.get("cook_time_minutes") or 0)
        if (derived.get("freeform") or "").strip() or (e["cooked_status"] or "") == "done":
            dish["protected"] = True
        if reheat or frozen or e["id"] in chains["sources"]:
            dish["chained"] = True
        if dish["food_groups"] is None and not reheat and not frozen:
            groups = json.loads(e["food_groups_json"] or "[]")
            if groups:
                dish["food_groups"] = groups
    return list(dishes.values())


def _cap_at(caps: dict | None, date: str, slot: str) -> int | None:
    """
    The time cap on one meal, read the one way this module reads `caps`.
    The generator hands {(date, slot): minutes or None} from
    time_caps.minutes_cap (2026-09-23: a rush dinner and a weekday fresh
    lunch have different caps on the same date); older callers and tests
    hand {date: minutes or None}. Both shapes answer here, so nothing else
    in this module has to know which one it was given.
    """
    if not caps:
        return None
    if (date, slot) in caps:
        return caps[(date, slot)]
    return caps.get(date)


def _fits(dish: dict, cap: int | None) -> bool:
    """Whether a dish may be COOKED on a night with this time cap. Unknown
    minutes (an older recipe with none recorded) can't be judged and are
    let through, as the plate pass lets an unknown plate through. A night
    that reheats has no cap to fit: nothing is cooked on it."""
    return cap is None or dish.get("minutes") is None or dish["minutes"] <= cap


def _holds_a_capped_night(dish: dict, caps: dict | None) -> bool:
    """A dish that fits a capped night it is already on is the quick dish
    that night needs — the last to drop (verifier, 2026-09-21)."""
    for n in dish["nights"]:
        cap = _cap_at(caps, n["date"], n.get("slot") or "dinner")
        if cap is not None and _fits(dish, cap):
            return True
    return False


def _pick_surplus(dishes: list[dict], target: int, caps: dict | None = None) -> list[dict]:
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


# ---------- fewer recipes than meals means batch cooking ----------
#
# Emily, 2026-09-23 ("Decision E"): "yes - encourage double batch for
# leftovers, that's the point of indicating less meal types for number of
# meals. If I want 2 types of lunches, but need 4 lunches, you should
# assume Im making double of each of the recipes. thats the batch cooking
# point". Also decided that day: a dish the household asked for once is
# planned once — never copied as a second fresh cook (prod plan 61 got a
# second Korean Chicken Pancake from the old fold, which put the kept dish
# FURTHEST from the freed night on it as an independent cook); leftovers
# are eaten within leftovers.MAX_LEFTOVER_DAYS (3) days of the cook, else
# frozen; and Pomona does this planning itself and says what it did in
# one plain line (draft_opener.batch_line).
#
# So the fold no longer writes a second cooking of a kept dish. Every night
# of a slot whose count is below its nights is one of:
#   a COOK      — a kept dish's first night; a later night of the same
#                 dish more than 3 days on (a second batch — never for a
#                 dish they asked for by name);
#   LEFTOVERS   — linked to a cook of the same slot 1–3 days earlier (a
#                 lunch may also eat the dinner cooked the evening before,
#                 when that dinner is one of the week's lunches already —
#                 repair_leftover_chains' rule: the source is a lunch or a
#                 dinner). A kept dish's own later night eats its own cook;
#                 a freed night eats the nearest cook with the smallest
#                 batch, so two recipes over four lunches come out as each
#                 cooked double rather than one cooked triple;
#   a FREEZER PORTION — only when no cook sits 1–3 days earlier and no
#                 re-laying of the cooks (below) can put one there: the
#                 nearest earlier cook makes the portion extra for the
#                 freezer (leftovers.FREEZER_EXTRA_KEY).
# Re-laying: when some night has no cook in reach, the dishes that are
# free to move (not asked for by name, not in a chain the model wrote) are
# laid out again in even runs of cook, leftovers, cook, leftovers, each
# cook on a night its time cap allows. Only when that can't cover the
# week does a night go to the freezer.
# Both halves of each chain are written the way repair_leftover_chains and
# cook_ahead.set_cook_ahead write them (links_to on the night,
# make_double_for + make_double_note on the cook), so the grocery ingest
# buys one scaled batch on the cook and nothing on the leftovers nights.


def _key(name: str | None) -> str:
    return (name or "").strip().lower()


def _gap(earlier: str, later: str) -> int:
    return _leftovers.days_apart(earlier, later)


def _in_reach(cook: dict, night: dict) -> bool:
    """A cook this night may eat: 1–3 days earlier in the same slot, or
    (a lunch) the dinner the evening before."""
    gap = _gap(cook["date"], night["date"])
    if cook["slot"] != night["slot"]:
        return gap == 1
    return 1 <= gap <= _leftovers.MAX_LEFTOVER_DAYS


def _slot_nights(dishes: list[dict], chains: dict) -> list[dict]:
    """Every planned night of the slot, in date order, each knowing its
    dish, whether it is already a reheat (a chain or a freezer night) and
    whether it is a chain SOURCE (which must stay a cook)."""
    out = []
    for dish in dishes:
        for e in dish["nights"]:
            derived = json.loads(e.get("derived_from_json") or "{}") or {}
            reheat = chains["leftovers"].get(e["id"])
            frozen = derived.get(_leftovers.FROM_FREEZER_KEY) if isinstance(derived.get(_leftovers.FROM_FREEZER_KEY), dict) else None
            out.append({
                "entry": e, "id": e["id"], "date": e["date"], "slot": e.get("slot"), "dish": dish,
                "meal": e["meal"], "derived": derived,
                "reheat_of": reheat["source"]["entry_id"] if reheat else (
                    _ref_id(frozen.get("cook")) if frozen else None),
                "source": e["id"] in chains["sources"],
                "done": (e.get("cooked_status") or "") == "done",
            })
    out.sort(key=lambda n: (n["date"], n["id"]))
    return out


def _ref_id(ref) -> int | None:
    m = re.match(r"^entry_id:(\d+)$", str(ref or ""))
    return int(m.group(1)) if m else None


def _new_cook(night: dict, dish: dict, size: int = 1) -> dict:
    return {"id": night["id"], "date": night["date"], "slot": night["slot"], "dish": dish,
            "size": size, "night": night}


def _best_cook(cooks: list[dict], night: dict, allowed: set[str]) -> dict | None:
    """The cook a freed night eats: in reach, of a kept dish; the smallest
    batch first (each recipe doubled before any is tripled), then the
    nearest."""
    fits = [c for c in cooks if _key(c["dish"]["name"]) in allowed and _in_reach(c, night)]
    if not fits:
        return None
    return min(fits, key=lambda c: (c["size"], _gap(c["date"], night["date"])))


def _plan_batches(nights: list[dict], kept: list[dict], caps: dict | None, slot: str,
                  outside: list[dict], relay: bool) -> tuple[list[dict], list[dict]]:
    """
    Decide every night: ("keep" | "cook" | "link" | "freezer" | "stand").
    `outside` are cooks from another slot a night here may eat (the
    evening-before dinners, for lunches). `relay` lays the movable dishes
    out again in even runs (see the block comment above). Returns
    (decisions, unresolved nights).
    """
    kept_keys = {_key(d["name"]) for d in kept}
    movable = [d for d in kept if not d["protected"] and not d["chained"]] if relay else []
    movable_ids = {id(d) for d in movable}
    cooks: list[dict] = [dict(c) for c in outside]
    decisions: list[dict] = []
    unresolved: list[dict] = []

    def cooks_of(dish):
        return [c for c in cooks if c["dish"] is dish]

    # A requested dish's later night with none of its own cooks in reach
    # (its cooks never move: the first night, a night cooked, a chain
    # source) — fed like a freed night, never cooked again.
    fixed_cooks: dict[int, list[str]] = {}
    for n in nights:
        if _key(n["dish"]["name"]) in kept_keys and n["reheat_of"] is None and n["dish"]["protected"]:
            if n["done"] or n["source"] or id(n["dish"]) not in fixed_cooks:
                fixed_cooks.setdefault(id(n["dish"]), []).append(n["date"])
    freed_ids = {
        n["id"] for n in nights
        if _key(n["dish"]["name"]) in kept_keys and n["reheat_of"] is None and n["dish"]["protected"]
        and n["date"] not in fixed_cooks.get(id(n["dish"]), [])
        and not any(1 <= _gap(d, n["date"]) <= _leftovers.MAX_LEFTOVER_DAYS for d in fixed_cooks[id(n["dish"])])
    }
    # The nights re-laying may move, and how many remain as it walks.
    flexible = [n for n in nights if id(n["dish"]) in movable_ids or n["id"] in freed_ids
                or (_key(n["dish"]["name"]) not in kept_keys)]
    unused = list(movable)
    block: dict | None = None
    seen_flexible = 0

    for n in nights:
        dish = n["dish"]
        is_kept = _key(dish["name"]) in kept_keys
        if is_kept and n["reheat_of"] is not None:
            src = next((c for c in cooks if c["id"] == n["reheat_of"]), None)
            if src:
                src["size"] += 1
            decisions.append({"kind": "keep", "night": n})
            continue

        if relay and (id(dish) in movable_ids or not is_kept or n["id"] in freed_ids):
            seen_flexible += 1
            if block and _in_reach(block, n) and block["size"] < block["cap"]:
                block["size"] += 1
                decisions.append({"kind": "link", "night": n, "cook": block})
                continue
            if unused:
                own = next((d for d in unused if d is dish and _fits(d, _cap_at(caps, n["date"], slot))), None)
                pick = own or next((d for d in unused if _fits(d, _cap_at(caps, n["date"], slot))), None)
                if pick is not None:
                    remaining = len(flexible) - seen_flexible + 1
                    block = _new_cook(n, pick)
                    block["cap"] = math.ceil(remaining / len(unused))
                    unused.remove(pick)
                    cooks.append(block)
                    decisions.append({"kind": "cook", "night": n, "cook": block})
                    continue
            any_cook = _best_cook(cooks, n, kept_keys)
            if any_cook:
                any_cook["size"] += 1
                decisions.append({"kind": "link", "night": n, "cook": any_cook})
                continue
            unresolved.append(n)
            decisions.append({"kind": "unresolved", "night": n})
            continue

        if is_kept:
            if n["done"] or n["source"] or not cooks_of(dish):
                c = _new_cook(n, dish)
                cooks.append(c)
                decisions.append({"kind": "keep", "night": n, "cook": c})
                continue
            own = [c for c in cooks_of(dish) if _in_reach(c, n)]
            if own:
                c = min(own, key=lambda c: _gap(c["date"], n["date"]))
                c["size"] += 1
                decisions.append({"kind": "link", "night": n, "cook": c})
                continue
            if not dish["protected"]:
                # More than three days on: a second batch of its own.
                c = _new_cook(n, dish)
                cooks.append(c)
                decisions.append({"kind": "keep", "night": n, "cook": c})
                continue
            # A dish they asked for is cooked once (Emily, 2026-09-23):
            # this night is fed like a freed one.
        c = _best_cook(cooks, n, kept_keys)
        if c:
            c["size"] += 1
            decisions.append({"kind": "link", "night": n, "cook": c})
            continue
        unresolved.append(n)
        decisions.append({"kind": "unresolved", "night": n})

    if relay and unused:
        # A kept dish that got no run would vanish from the week, and the
        # count with it — not a layout to use.
        unresolved.append(None)
    if not relay:
        # The freezer, for what nothing reaches: the nearest earlier cook
        # of a kept dish makes the portion extra. With no earlier cook at
        # all, the night stands as generated — one dish too many beats a
        # night with nothing on it.
        for d in decisions:
            if d["kind"] != "unresolved":
                continue
            n = d["night"]
            earlier = [c for c in cooks if c["date"] < n["date"] and _key(c["dish"]["name"]) in kept_keys
                       and c["slot"] == slot]
            if earlier:
                # Its own dish first: a requested dish's later night eats
                # that dish from the freezer, not some other one.
                own = [c for c in earlier if c["dish"] is n["dish"]]
                d["kind"] = "freezer"
                d["cook"] = max(own or earlier, key=lambda c: c["date"])
            else:
                d["kind"] = "stand"
    return decisions, unresolved


def _outside_cooks(plan_id: int, slot: str, kept: list[dict], chains: dict) -> list[dict]:
    """The dinners a lunch may eat the evening after (repair_leftover_
    chains lets a lunch reheat a dinner) — only a dinner that is already
    one of the week's lunches, so the lunch count stays the count."""
    if slot != "lunch":
        return []
    kept_by_key = {_key(d["name"]): d for d in kept}
    out = []
    for e in _load_slot_entries(plan_id, "dinner"):
        if e["slot_state"] != "planned" or not e["meal"] or e["id"] in chains["leftovers"]:
            continue
        dish = kept_by_key.get(_key(e["meal"]))
        if dish is None:
            continue
        size = 1 + len((chains["sources"].get(e["id"]) or {}).get("targets") or [])
        # Shifted to the lunch's own clock: the evening before is 1 day.
        out.append({"id": e["id"], "date": e["date"], "slot": "dinner", "dish": dish, "size": size, "night": None})
    return out


def _link_derived(night: dict, cook_id: int, slot: str, extra: dict | None = None) -> dict:
    derived = dict(night["derived"]) if extra is None else dict(extra)
    derived["links_to"] = f"entry_id:{cook_id}"
    derived[_leftovers.BATCH_KEY] = True
    if slot == "breakfast":
        # repair_leftover_chains takes a breakfast source only as a
        # cook-ahead ("Made ahead — Monday's Egg Bites"); a morning batch
        # is exactly that.
        derived["cook_ahead"] = True
    return derived


def _write_batches(plan_id: int, slot: str, decisions: list[dict], target: int) -> dict:
    """Write what _plan_batches decided, in date order (a moved cook gets
    its new row before any night links to it). Every change to a night
    goes through _replace_slot_entries, the write every swap uses."""
    out = {"replaced": [], "batched": [], "stood": 0}
    targets: dict[int, list[str]] = {}
    frozen: list[tuple[int, str, str]] = []
    count = f"{slot}s_per_week:{target}"
    try:
        for d in decisions:
            n = d["night"]
            kind = d["kind"]
            if kind == "stand":
                out["stood"] += 1
                continue
            if kind == "keep":
                continue
            cook = d["cook"]
            if kind == "cook":
                if _key(n["meal"]) != _key(cook["dish"]["name"]):
                    row = _weekly_plan._replace_slot_entries(
                        plan_id, [n["id"]], n["date"], slot, cook["dish"]["name"],
                        food_groups=cook["dish"]["food_groups"], reasoning="",
                        derived_from={"constraint": _leftovers.BATCH_KEY, "count": count, "replaced": n["meal"]},
                    )
                    cook["id"] = row.get("entry_id")
                    out["replaced"].append({"date": n["date"], "dropped": n["meal"], "with": cook["dish"]["name"], "as": "cook"})
                continue
            if kind == "link":
                if _key(n["meal"]) == _key(cook["dish"]["name"]) and not n["source"]:
                    conn = get_conn()
                    conn.execute(
                        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                        (json.dumps(_link_derived(n, cook["id"], slot)), n["id"], household_id()),
                    )
                    conn.commit()
                    conn.close()
                else:
                    _weekly_plan._replace_slot_entries(
                        plan_id, [n["id"]], n["date"], slot, cook["dish"]["name"],
                        food_groups=cook["dish"]["food_groups"], reasoning="",
                        derived_from=_link_derived(n, cook["id"], slot, extra={
                            "constraint": _leftovers.BATCH_KEY, "count": count, "replaced": n["meal"],
                        }),
                    )
                    out["replaced"].append({"date": n["date"], "dropped": n["meal"], "with": cook["dish"]["name"], "as": "leftovers"})
                targets.setdefault(cook["id"], []).append(f"{n['date']}:{slot}")
                continue
            if kind == "freezer":
                name = cook["dish"]["name"]
                _weekly_plan._replace_slot_entries(
                    plan_id, [n["id"]], n["date"], slot, _leftovers.freezer_night_name(name, cook["date"]),
                    reasoning="",
                    derived_from={
                        _leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{cook['id']}", "dish": name},
                        _leftovers.BATCH_KEY: True, "constraint": _leftovers.BATCH_KEY, "count": count,
                        "replaced": n["meal"],
                    },
                )
                frozen.append((cook["id"], n["date"], slot))
                out["replaced"].append({"date": n["date"], "dropped": n["meal"], "with": name, "as": "freezer"})
    finally:
        # The cook side of whatever nights were written, even when a later
        # night's write failed: a leftovers night whose cook doesn't name
        # it back is not a chain (plan_leftover_chains), and would be bought
        # and cooked on its own.
        _write_cook_sides(plan_id, targets, frozen, out)
    return out


def _write_cook_sides(plan_id: int, targets: dict, frozen: list, out: dict) -> None:
    conn = get_conn()
    try:
        for cook_id, keys in targets.items():
            row = conn.execute(
                "SELECT date, slot, derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                (cook_id, household_id()),
            ).fetchone()
            if row is None:
                continue
            derived = json.loads(row["derived_from_json"] or "{}")
            have = derived.get("make_double_for") or []
            if isinstance(have, str):  # tolerate the pre-fix scalar shape
                have = [have]
            merged = sorted(set(have) | set(keys), key=lambda t: t.split(":")[0])
            derived["make_double_for"] = merged
            derived["make_double_note"] = _weekly_plan._make_double_note_text(merged)
            conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                         (json.dumps(derived), cook_id))
            out["batched"].append({"cook": row["date"], "slot": row["slot"], "covers": sorted(keys)})
        for cook_id, night_date, night_slot in frozen:
            _weekly_plan.freeze_a_portion(conn, cook_id, night_date, night_slot)
        conn.commit()
    finally:
        conn.close()

    touched = set(targets) | {c for c, _, _ in frozen}
    if touched and _weekly_plan._weekly_plan_is_approved(plan_id):
        # An approved week's list was bought per night; the recipe group is
        # re-bought as one batch on the cook (leftovers nights buy nothing).
        for cook_id in touched:
            _weekly_plan._rescale_leftover_source_grocery(cook_id, 0)


def enforce_distinct_count(
    plan_id: int, target: int | None, slot: str = "dinner", asks: tuple[str | None, ...] = (),
    budget=None, picker=None, fill_up: bool = True, usual: int | None = None, day_count: int = 7,
    caps: dict | None = None,
) -> dict:
    """
    Make one slot's distinct dishes NUMBER `target` for this plan — see
    the module docstring for what goes and what stays. Too many: the
    surplus dishes' nights become leftovers of the kept ones (no model
    call; see "fewer recipes than meals means batch cooking" above). Too
    few (Emily, 2026-09-21: the count is a target, not a cap): a repeated
    night is re-picked quietly into a dish the week does not have yet,
    through the swap's own picker against `budget`, until the number is
    met or the budget is spent — see _fill_up. `fill_up` False keeps that
    half off: the caller passes the household's meal_counts_set, because
    chasing a column default up to seven distinct breakfasts would spend
    real calls on a number nobody chose.

    Whichever way the count went, when the slot has more nights than the
    target, the week's repeats are batch cooked (Emily, 2026-09-23): a
    night repeating a dish eats its cook's leftovers within three days,
    and a dish they asked for is never cooked a second time.

    `caps` is the meals' time caps (a rush night, the weeknight cap),
    keyed {date: minutes} or {(date, slot): minutes} — read through
    _cap_at — and a cook is never moved onto a meal it is too long for.
    `usual` and `day_count` are no longer read (they sized the old "On
    again" line) and are accepted so the caller need not change.
    Returns {"before", "after", "replaced": [{"date", "dropped", "with",
    "as"}], "batched": [{"cook", "slot", "covers"}], "added": [...]} and
    never raises: a plan with one dish too many is a far better outcome
    than a lost week, so any failure is logged and the plan is left as it
    stands.
    """
    result = {"before": None, "after": None, "replaced": [], "added": [], "batched": [], "skipped": None}
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
        surplus: list[dict] = []
        if len(dishes) < target:
            if fill_up:
                result["added"] = _fill_up(plan_id, slot, dishes, target, budget, picker)
                result["after"] = result["before"] + len(result["added"])
                if result["added"]:
                    chains = _leftovers.plan_leftover_chains(plan_id)
                    dishes = _group_dishes(_load_slot_entries(plan_id, slot), chains)
            else:
                result["skipped"] = "counts are defaults, not answers"
        elif len(dishes) > target:
            surplus = _pick_surplus(dishes, target, caps)
            if len(surplus) < len(dishes) - target:
                logger.warning(
                    "Plan %s has %d distinct %ss against a preference of %d, but only %d can be dropped "
                    "(the rest are protected); dropping what can be",
                    plan_id, len(dishes), slot, target, len(surplus),
                )
        nights_total = sum(len(d["nights"]) for d in dishes)
        if nights_total <= target:
            # As many recipes as meals (or more): nothing to batch.
            return result
        surplus_keys = {_key(d["name"]) for d in surplus}
        kept = [d for d in dishes if _key(d["name"]) not in surplus_keys]
        nights = _slot_nights(dishes, chains)
        outside = _outside_cooks(plan_id, slot, kept, chains)
        decisions, unresolved = _plan_batches(nights, kept, caps, slot, outside, relay=False)
        if any(d["kind"] in ("freezer", "stand") for d in decisions):
            relaid, missed = _plan_batches(nights, kept, caps, slot, outside, relay=True)
            if not missed:
                decisions = relaid
        written = _write_batches(plan_id, slot, decisions, target)
        result["replaced"] = written["replaced"]
        result["batched"] = written["batched"]
        result["after"] = len(_group_dishes(_load_slot_entries(plan_id, slot), _leftovers.plan_leftover_chains(plan_id)))
        if result["replaced"] or result["batched"]:
            logger.info(
                "Plan %s %ss: %d distinct against a target of %d; batched %s",
                plan_id, slot, result["before"], target,
                ", ".join(f"{r['date']} {r['dropped']} -> {r['with']} ({r['as']})" for r in result["replaced"])
                or f"{len(result['batched'])} repeat(s)",
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
    derived_key: str, picker=None, context_extra: dict | None = None, reject_pick=None,
    derived_extra: dict | None = None, reason_line: str | None = None,
    also: list[dict] | None = None,
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

    `context_extra` rides into the swap context as it is (the typed-
    ingredient pass hands `must_contain` this way — typed_requests); a
    `reject_pick(candidate)` sees the WHOLE pick, ingredients included, and
    answers with why it is no good or None; `derived_extra` is written onto
    the new row's derived_from beside the `derived_key` note.
    `reason_line` is the sentence to put under the dish in place of the
    pick's own reason — for a pass whose answer to "why is this here" is
    about the dish that LEFT rather than the one that arrived
    (repick_recent_repeats). Left unset, the pick speaks for itself.

    `also` is the dish's OTHER nights, when what is being replaced is a
    whole dish rather than one slot: the model is asked once and every
    night takes the answer, written through weekly_plan.replace_dish_on_days
    — one transaction, and the function that already knows how to carry a
    cook + reheat shape across a swap. Doing it as N swaps is the thing
    that function's own docstring warns against: the first would see the
    reheat night still holding the old dish, unlink the pair, and buy the
    new cook for one table.

    The returned dict is the write's own, with `meal` always on it: what
    was picked, so a caller can say what landed whichever write was used.
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
            if context_extra:
                context.update(context_extra)
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
        if why is None and reject_pick is not None:
            why = reject_pick(candidate)
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
    if derived_extra:
        derived.update(derived_extra)
    groups = [g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS]
    reasoning = reason_line if reason_line is not None else (pick.get("reason") or "").strip()
    if also:
        written = _weekly_plan.replace_dish_on_days(plan_id, [
            {"old_entry_id": night["id"], "date": night["date"], "slot": night["slot"],
             "new_meal": pick["meal_name"], "food_groups": groups, "reasoning": reasoning,
             "derived_from": dict(json.loads(night.get("derived_from_json") or "{}") or {},
                                  **{derived_key: {"dropped": entry["meal"], "because": because}},
                                  **(derived_extra or {}))}
            for night in [entry] + list(also)
        ])
    else:
        written = _weekly_plan._replace_slot_entries(
            plan_id, [entry["id"]], entry["date"], entry["slot"], pick["meal_name"],
            food_groups=groups, reasoning=reasoning, derived_from=derived,
        )
    # replace_dish_on_days answers {"entry_ids": [...]} and nothing else, so
    # on that path what was picked has to be said here or the caller cannot
    # know it without reading the plan back. _replace_slot_entries already
    # answers with `meal`, and this writes the same value over it, so the
    # single-slot path is unchanged for every existing caller.
    return dict(written, meal=pick["meal_name"])


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


# ---------- a dinner or lunch from the last two weeks ----------
#
# The drafting prompt has said it for as long as the window has been
# written down: "The no-repeat rule against recent_history is about DINNER
# and LUNCH — not breakfast or snack." plan_quality has DETECTED the
# breach since the same day, at severity "warn", and the draft's own
# opening line has REPORTED it in the household's face ("… Chili back from
# the last two weeks") — and nothing anywhere ever put it right. Emily,
# 2026-09-20: "you're continuously giving me the same food recommendations
# as previous weeks." Telling the generator something is not the same as
# preventing it, which is the rule every pass in _finish_week_slots exists
# for; this is that pass for the no-repeat rule, and it runs for every
# household every week rather than only under Surprise me.
#
# THE WINDOW IS draft_opener.recent_dish_names, READ RATHER THAN
# RE-DERIVED, and that is the whole reason this is safe to run always: the
# repair and the opening line ask ONE function what the last two weeks
# hold, so the line cannot report a repeat this pass decided to ignore.
# (Approved plans only — a draft nobody approved was never last week's
# food — other plans only, and dates strictly before this period. All
# three of those are that function's rules, not this one's.) A second
# reading of "the last two weeks" here is free to disagree with the
# sentence the household actually reads, which is exactly what this card
# was raised about.
#
# What it cannot do it leaves alone and logs, per the rule this module
# already follows twice: a week carrying one repeat is a far better
# outcome than a lost week.

# The sentence under the replacement. ONE constant, Emily's to change.
# Her suggested default was "Swapped in — you had [dish] last week." — the
# window is two weeks, so half the dishes it would name were not had last
# week, and §8 does not let the app say a thing that isn't. The window's
# own words (variety_window_words) are the same sentence, true either way.
REPEAT_REASON = "Swapped in — you had {dish} in {window}."

# Punctuation out, case down, and a trailing filler word off the end:
# "Taco Night" and "tacos night" both read as the dish. Emily, 2026-09-25,
# on what "a near-identical variant" means for this first pass: "exact name
# plus a light normalisation … Do NOT build fuzzy matching." Two dishes
# that merely share words ("Chicken Tacos", "Chicken Taco Bowls") are two
# dishes, and a rule reading them as one would drop a dinner nobody
# repeated — the expensive direction, since the household never asked for
# the dish that replaced it.
#
# It is deliberately a SUPERSET of the opener's own comparison, which
# lowercases and strips and does nothing else. That keeps the two in step
# in the one direction that matters: anything the OPENER would call a
# repeat, this pass has already seen and replaced. Widening it further is
# safe for the line and costs a dish; narrowing it past the opener's is
# what would make the line lie.
_REPEAT_FILLER_TAIL = ("night", "nights")
_REPEAT_PUNCT = re.compile(r"[^\w\s]+")


def repeat_key(name: str | None) -> str:
    words = _REPEAT_PUNCT.sub(" ", (name or "").lower()).split()
    while words and words[-1] in _REPEAT_FILLER_TAIL:
        words.pop()
    return " ".join(words)


# A dish word too common to read as a request on its own: "salad on
# Tuesday" is not "keep the Greek salad". The full name still protects
# ("greek salad please"), so this list only decides what the LAST word of a
# name may stand in for.
_TOO_GENERIC_TO_ASK_BY = {
    "salad", "soup", "bowl", "bowls", "stew", "curry", "bake", "dinner",
    "lunch", "meal", "meals", "dish", "dishes", "night", "leftovers",
}


# A row another pass wrote BECAUSE the household said so. `holiday_dish`
# is the dish they told Pomona they were taking to a holiday
# (holidays._plan_dish) — their words as plainly as derived_from.freeform
# is, and the model never stamps it, since that row is not the model's.
# Measured before it was added: a household bringing a chili they had
# eaten eight days earlier had it swapped away for a dish nobody named.
_THEIR_OWN_KEYS = ("freeform", "holiday_dish")


def theirs_by_hand(dish: dict) -> bool:
    """Does any night of this dish carry another pass's record that the
    household asked for it? _group_dishes' `protected` reads `freeform`
    and a cooked night and is shared with the count pass; this is the
    same question widened for this one only, so nothing else's behaviour
    moves with it."""
    for night in dish["nights"]:
        derived = json.loads(night.get("derived_from_json") or "{}") or {}
        if any(derived.get(key) for key in _THEIR_OWN_KEYS):
            return True
    return False


def asked_for_by_name(name: str, texts: tuple[str | None, ...] = ()) -> bool:
    """
    Did the household's own words for THIS week name this dish?

    The model is asked to stamp derived_from.freeform on a slot a request
    shaped, and _group_dishes already protects a dish that carries one —
    but this module's own first rule is that telling the generator
    something is not the same as preventing it, and a dish they typed and
    the model failed to mark is exactly the dish that must not be swapped
    away. So the text is read too: the whole name inside it ("chili on
    Monday" keeps Chili), or the name's last word as a whole word when
    that word says anything at all ("chili again please" keeps Bean chili).

    It errs toward KEEPING, deliberately and in both halves. A dish left
    standing is a repeat the household reads a line about; a dish taken
    away is one they asked for and did not get.
    """
    key = repeat_key(name)
    if not key:
        return False
    tail = key.split()[-1]
    for text in texts:
        said = repeat_key(text)
        if not said:
            continue
        if re.search(rf"\b{re.escape(key)}\b", said):
            return True
        if len(tail) >= 4 and tail not in _TOO_GENERIC_TO_ASK_BY and re.search(rf"\b{re.escape(tail)}\b", said):
            return True
    return False


def _replaceable(dish: dict, chains: dict) -> list[dict] | None:
    """
    Every night this dish holds, when the whole dish can go at once — else
    None, and it is left as generated and logged.

    A cook-once-eat-twice chain goes as a WHOLE or not at all: re-picking
    the batch night and leaving the nights eating from it is a reheat of
    nothing, which is a worse plan than the repeat it was fixing. The
    write (weekly_plan.replace_dish_on_days) carries the shape across —
    the cook stays the cook and feeds exactly the same nights — but only
    for a chain that is entirely inside the group handed to it, so that is
    what this checks.

    Two shapes are refused rather than guessed at, each because the
    replacement would have to answer something this pass has no answer to:
      * a night eating a portion frozen on an earlier cook. `from_freezer`
        is not one of weekly_plan._CHAIN_KEYS, so it would be carried
        across verbatim and point at a row that has gone;
      * a chain reaching out of this dish — a lunch eating the evening
        before's dinner, or a cook feeding a night filed under some other
        dish. The other end is a different slot's business, and the two
        ends are looked at on different passes, so taking one would leave
        the other reheating a dish nobody is cooking.
    """
    nights = dish["nights"]
    ids = {n["id"] for n in nights}
    for night in nights:
        derived = json.loads(night.get("derived_from_json") or "{}") or {}
        if derived.get(_leftovers.FROM_FREEZER_KEY):
            return None
        reheat = chains["leftovers"].get(night["id"])
        if reheat and reheat["source"]["entry_id"] not in ids:
            return None
        fed = {t["entry_id"] for t in (chains["sources"].get(night["id"]) or {}).get("targets") or []}
        if fed - ids:
            return None
    return nights


def _replace_whole_dish(plan_id: int, dish: dict, nights: list[dict], budget, picker,
                        refuse: set[str], because: str) -> str | None:
    """
    Put ONE new dish on every night this one holds, in one transaction.

    The model is asked once, for the dish's first night, and the rest take
    the same answer: they were the same dish, and paying for a second pick
    would spend the shared budget to make the week less coherent rather
    than more. It also keeps the week the shape the model composed, so the
    distinct-count pass after this still sees one dish on N nights.
    """
    new = _repick_entry(
        plan_id, nights[0], budget,
        avoid=[], because=because,
        reject=lambda name: repeat_key(name) in refuse,
        derived_key="repeat_repick", picker=picker,
        reason_line=REPEAT_REASON.format(dish=dish["name"], window=variety_window_words()),
        also=nights[1:] or None,
    )
    return None if new is None else new["meal"]


def repick_recent_repeats(plan_id: int, period_start: str | None, budget, picker=None,
                          asks: tuple[str | None, ...] = ()) -> dict:
    """
    Every dinner and lunch on this draft that the household ate inside the
    variety window is replaced with one they did not — the whole dish, all
    its nights together, through the swap's own picker and the one write
    every swap in the app uses, so the grocery list follows.

    Left exactly as generated, each logged by name: a dish they asked for
    in their own words (derived_from.freeform, the dish they said they are
    taking to a holiday, or their typed words — theirs_by_hand and
    asked_for_by_name), a night already cooked, a chain this pass cannot
    move whole (_replaceable), and any dish the picker could not better
    inside swap_in_place.MAX_PICK_ATTEMPTS or the shared call budget. The
    budget is the generation's, shared with the allergen re-pick and the
    count passes after this: at most allergen_gate.MAX_REPICK_CALLS model
    calls for the whole week, which is what bounds a week the model filled
    entirely with last fortnight's dinners.

    Breakfast and snack are NOT checked, on purpose: the prompt asks for
    them to repeat, and a household eating the same oats every morning is
    the rhythm working rather than a rule being broken (NO_REPEAT_SLOTS).

    Never raises: counts come back for the log and for tests, and any
    failure leaves the plan as the model wrote it.
    """
    from . import draft_opener as _draft_opener

    out = {"repeats": 0, "repicked": 0, "left": []}
    if not period_start:
        return out
    try:
        recent = _draft_opener.recent_dish_names(period_start, plan_id)
        had = {k for k in (repeat_key(n) for n in (recent or ())) if k}
        if not had:
            return out
        refuse = set(had)
        because = f"you had it in {variety_window_words()}"
        chains = _leftovers.plan_leftover_chains(plan_id)
        for slot in NO_REPEAT_SLOTS:
            for dish in _group_dishes(_load_slot_entries(plan_id, slot), chains):
                if repeat_key(dish["name"]) not in had:
                    continue
                out["repeats"] += 1
                # Their words beat the rule, exactly as they beat the
                # dinners-per-week count. Three readings of "they asked
                # for this", because no one of them is complete: the
                # model's own stamp and a cooked night (`protected`),
                # another pass's record that they said so (theirs_by_hand),
                # and their typed words, for a request the model was told
                # to mark and did not.
                if dish["protected"] or theirs_by_hand(dish) or asked_for_by_name(dish["name"], asks):
                    out["left"].append(dish["name"])
                    continue
                nights = _replaceable(dish, chains)
                if nights is None:
                    out["left"].append(dish["name"])
                    continue
                name = _replace_whole_dish(plan_id, dish, nights, budget, picker, refuse, because)
                if name is None:
                    out["left"].append(dish["name"])
                    continue
                out["repicked"] += 1
                # What this pass has just put on the week joins what the
                # picker must not offer again — two repeats answered with
                # one dish is a new duplicate — but NOT what counts as a
                # repeat: a later slot honestly holding that dish was not
                # eaten in the window, and the line under it would say
                # they had it when they did not (§8).
                refuse.add(repeat_key(name))
                logger.info("No repeat: plan %s %s %r -> %r (%s)", plan_id, slot, dish["name"], name, because)
                # The chain map is what this pass reads to decide which
                # nights belong to a dish; the write above moved rows, so
                # the dishes still to judge are read against a fresh one.
                chains = _leftovers.plan_leftover_chains(plan_id)
        if out["left"]:
            logger.info("No repeat: %d dish(es) left as generated on plan %s: %s",
                        len(out["left"]), plan_id, ", ".join(out["left"]))
    except Exception:
        logger.exception("No-repeat enforcement failed for plan %s; the week stands as generated", plan_id)
    return out
