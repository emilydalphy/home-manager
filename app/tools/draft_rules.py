"""
Two household rules held on the draft rather than only asked for (Loop
Board "The draft breaks the household's own rules", 2026-10-06 — a new
household's first week had the child's cold packed lunch as a hot rice
skillet, and tilapia three times).

  - enforce_cold_packed_lunches: a weekday lunch someone at it needs Cold
    packed (and has no warm container ticked — several ticked means any
    works, member_needs.how_line) must be a cold dish. A lunch that
    reheats last night's dinner, or whose name reads as a hot dish
    (HOT_DISH_WORDS), is re-picked as a cold packed lunch. Nut-free is
    NOT handled here: it is already a household-wide hard avoidance
    (coordination.py's member_needs.NUT_FREE_AVOIDANCES, swap_in_place.
    _hard_exclusions), checked by the allergen gate on every pick.
  - enforce_protein_variety: one main protein (recipes.main_protein) is
    cooked at most MAX_PER_PROTEIN times a week across dinners AND lunches.
    A reheat is the same cook and is not counted again; a dish the
    household asked for by name (meal_variety.theirs) is never moved.

Both re-pick through the first-week fill's own machinery (swap_in_place.
build_swap_context, allergen_gate._pick_for_group / _save_pick,
weekly_plan._replace_slot_entries). A slot nothing better came back for
stands as drafted: a lunch is never handed back as a question. Neither
pass raises.
"""
from __future__ import annotations

import json
import logging
import re

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger(__name__)

MAX_PER_PROTEIN = 2
PICK_ATTEMPTS = 2
CONSTRAINT = "household_rule_repick"

# Words in a dish name that mean it is served hot. Deliberately short: a
# dish that isn't caught is the drafted lunch standing, as before.
HOT_DISH_WORDS = (
    "skillet", "soup", "stew", "curry", "chili", "chilli", "stir-fry", "stir fry", "casserole",
    "bake", "baked", "roast", "roasted", "risotto", "fried rice", "ramen", "pho", "congee",
    "braise", "braised", "pot pie", "lasagna", "lasagne", "mac and cheese", "dal", "hotpot",
    "hot pot", "tagine", "goulash", "gratin", "sheet pan", "sheet-pan",
)
WARM_NEEDS = frozenset({"thermos", "reheat", "made_fresh"})


def _is_hot(name: str | None) -> bool:
    text = (name or "").lower()
    return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in HOT_DISH_WORDS)


def _rows(plan_id: int, slots: tuple[str, ...]) -> list[dict]:
    conn = get_conn()
    try:
        marks = ",".join("?" * len(slots))
        rows = conn.execute(
            f"""
            SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json,
                   COALESCE(r.name, mpe.freeform_meal) AS meal, r.main_protein
            FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.slot IN ({marks})
              AND mpe.component_category IS NULL AND mpe.slot_state = 'planned'
            ORDER BY mpe.date ASC, CASE mpe.slot WHEN 'lunch' THEN 0 ELSE 1 END, mpe.id ASC
            """,
            (plan_id, household_id(), *slots),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["derived"] = json.loads(d.get("derived_from_json") or "{}") or {}
        except (TypeError, ValueError):
            d["derived"] = {}
        out.append(d)
    return out


def _is_reheat(row: dict, chains: dict) -> bool:
    from . import leftovers as _leftovers
    derived = row["derived"]
    frozen = derived.get(_leftovers.FROM_FREEZER_KEY)
    return bool(row["id"] in chains["leftovers"] or derived.get("links_to")
                or (isinstance(frozen, dict) and (frozen.get("dish") or "").strip()))


def _repick(plan_id: int, row: dict, because: str, extra: dict, picker, ok) -> str | None:
    """One slot re-picked; the new dish's name, or None (the slot stands)."""
    from . import allergen_gate as _allergen_gate
    from . import plates as _plates
    from . import swap_in_place as _swap
    from . import weekly_plan as _weekly_plan

    entry = {"date": row["date"], "slot": row["slot"], "meal": row["meal"] or "", "entry_id": row["id"]}
    try:
        context = _swap.build_swap_context(plan_id, entry, [])
        context["replacing_because"] = because
        context.update(extra)
        tried: list[str] = []
        for _ in range(PICK_ATTEMPTS):
            context["avoid"] = list(dict.fromkeys(list(context.get("avoid") or []) + tried))
            outcome = _allergen_gate._pick_for_group(context, row["meal"] or "", picker or _allergen_gate.quick_pick,
                                                     _allergen_gate.hard_avoidances(), 1)
            pick = outcome.get("pick")
            if not pick:
                return None
            if not ok(pick):
                tried.append(pick["meal_name"])
                continue
            serves = _swap._table_for(row["date"], row["slot"])["serves"]
            pick["meal_name"] = _swap.honest_meal_name(pick)
            _allergen_gate._save_pick(pick, serves)
            _weekly_plan._replace_slot_entries(
                plan_id, [row["id"]], row["date"], row["slot"], pick["meal_name"],
                food_groups=[g for g in (pick.get("food_groups") or []) if g in _plates.ALL_GROUPS],
                reasoning=because, derived_from={"constraint": CONSTRAINT},
            )
            return pick["meal_name"]
    except Exception:
        logger.exception("Re-picking %s %s for a household rule failed; it stands", row["date"], row["slot"])
    return None


def enforce_cold_packed_lunches(plan_id: int, dates: list[str], picker=None) -> list[dict]:
    from . import leftovers as _leftovers
    from . import member_needs as _member_needs
    from . import meal_variety as _meal_variety

    out: list[dict] = []
    try:
        packing = _member_needs.lunch_packing(dates)
        cold_dates = {
            d: [p["name"] for p in people if "cold_packed" in p["needs"] and not (set(p["needs"]) & WARM_NEEDS)]
            for d, people in packing.items()
        }
        cold_dates = {d: names for d, names in cold_dates.items() if names}
        if not cold_dates:
            return out
        chains = _leftovers.plan_leftover_chains(plan_id)
        for row in _rows(plan_id, ("lunch",)):
            names = cold_dates.get(row["date"])
            if not names or (row["cooked_status"] or "") == "done" or _meal_variety.theirs(row["derived"]):
                continue
            if not (_is_reheat(row, chains) or _is_hot(row["meal"])):
                continue
            who = " and ".join(names)
            because = f"{who} needs a cold packed lunch."
            new = _repick(plan_id, row, because,
                          {"lunch_needs": [{"date": row["date"], "people": [{"name": n, "how": "cold packed"} for n in names]}],
                           "must_be": "a cold lunch that packs in a lunchbox and is eaten cold — no reheating"},
                          picker, lambda pick: not _is_hot(pick.get("meal_name")))
            if new:
                out.append({"date": row["date"], "was": row["meal"], "now": new})
    except Exception:
        logger.exception("Holding plan %s's cold packed lunches failed; the lunches stand", plan_id)
    return out


def enforce_protein_variety(plan_id: int, picker=None) -> list[dict]:
    from . import leftovers as _leftovers
    from . import meal_variety as _meal_variety

    out: list[dict] = []
    try:
        chains = _leftovers.plan_leftover_chains(plan_id)
        rows = [r for r in _rows(plan_id, ("lunch", "dinner")) if not _is_reheat(r, chains)]
        rows.sort(key=lambda r: (r["date"], 0 if r["slot"] == "lunch" else 1, r["id"]))
        seen: dict[str, int] = {}
        for row in rows:
            protein = (row.get("main_protein") or "").strip().lower()
            if not protein or protein in ("vegetarian", "vegan", "none", "mixed"):
                continue
            seen[protein] = seen.get(protein, 0) + 1
            if seen[protein] <= MAX_PER_PROTEIN:
                continue
            if (row["cooked_status"] or "") == "done" or _meal_variety.theirs(row["derived"]) \
                    or row["derived"].get("make_double_for") or row["id"] in chains["sources"]:
                continue
            because = f"not {protein} again this week."
            new = _repick(plan_id, row, because, {"avoid_main_protein": protein}, picker,
                          lambda pick, p=protein: (pick.get("main_protein") or "").strip().lower() != p)
            if new:
                seen[protein] -= 1
                out.append({"date": row["date"], "slot": row["slot"], "was": row["meal"], "now": new})
    except Exception:
        logger.exception("Holding plan %s's protein variety failed; the week stands", plan_id)
    return out
