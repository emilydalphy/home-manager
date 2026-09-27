"""
A draft never hands back a meal the household is home for — and never puts
one dish on more than two meals in a row.

Emily, 2026-09-27, walking a real draft on her phone (decision A): "no open
'Your call' slots for meals I've already answered." She had tagged
Wednesday Leftovers and the draft asked her what Wednesday was; the model
had also left Sunday's dinner open. Every one of those was a question
Pomona could answer itself. The rule she chose: a draft only has an open
dinner when nobody is home (a planned_empty row, never a question) or an
allergy blocks every option (allergen_gate's own open slot, which says
so). Breakfasts and lunches were already never open
(meal_variety.fill_gaps_with_a_repeat).

Decision B, the same walk: "no dish on more than two meals in a row" —
Thursday's dinner and Friday's lunch off it is fine, Friday's dinner is
something else. The rule is leftovers.MAX_MEALS_IN_A_ROW; every writer
asks it first, and break_long_runs below makes it true of whatever
slipped through.

Three passes, all called from agent._finish_week_slots, all deterministic
first and a model call only when nothing on the week will do:

- apply_leftovers_nights — a night tagged Leftovers (week_intake's `left`
  tag) reheats an earlier cook: the nearest dinner one to three days
  before, its batch sized up (the ordinary chain: links_to on the night,
  make_double_for on the cook). Further back than three days, a portion
  frozen on the nearest cook. The tag used to be a prompt instruction only.

- fill_open_dinners — every dinner still missing or open (and not one of
  the questions above that says something true) is, in order: a reheat of
  the nearest earlier cook; a fresh dish picked through the swap's own
  picker and held to the night's time cap; a repeat of one of the week's
  own dinners that fits the cap. Only when all three fail does the
  question stand.

- break_long_runs — the two-meals-in-a-row rule, made true.

Every pass swallows its own failures: a week that stands as generated is a
warning in the morning report; a week that fails to save is not a week.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from ..db import get_conn
from ._shared import household_id
from . import allergen_gate as _allergen_gate
from . import draft_flags as _draft_flags
from . import leftovers as _leftovers
from . import meal_variety as _meal_variety
from . import weekday_lunches as _weekday_lunches
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")

# What each pass records on the rows it writes, so the draft, the tests and
# a later reader can tell its rows from the model's.
LEFTOVERS_NIGHT_CONSTRAINT = "leftovers_night"
REHEAT_FILLS_GAP = "reheat_fills_gap"
REPICK_FILLS_GAP = "repick_fills_gap"
NOT_THREE_IN_A_ROW = "not_three_in_a_row"

# The question the gap audit asks, word for word — written here too when a
# missing dinner needs a row for the re-pick to replace. Left standing only
# when nothing at all could be planned.
def _gap_reason(meal_date: str, slot: str) -> str:
    day_name = date.fromisoformat(meal_date).strftime("%A")
    return (f"{day_name} I’d rather ask than guess: I couldn’t settle this {slot} without guessing "
            "at what you’d want. What would you prefer?")


class _Reserved:
    """
    The generation's shared re-pick budget, seen by a pass that must leave
    the last `keep` calls for the allergen sweep (review, 2026-09-27): these
    passes run BEFORE allergen_gate.sweep_plan, and a sweep with no budget
    left opens a clashing slot rather than re-picking it. A dish that stays
    a repeat is a far smaller cost than a clash handed back as a question.
    """

    def __init__(self, budget, keep: int):
        self._budget = budget
        self._keep = keep

    @property
    def left(self) -> int:
        return max(0, self._budget.left - self._keep)

    def take(self) -> bool:
        if self._budget.left <= self._keep:
            return False
        return self._budget.take()


def _sweep_reserve() -> int:
    """What the allergen sweep needs for one dish: its own two attempts."""
    from . import swap_in_place as _swap
    return _swap.MAX_PICK_ATTEMPTS


def _plan_rows(plan_id: int) -> list[dict]:
    """Every day-slot row of the plan, in eating order, with what the
    passes here read: the dish, its chain bookkeeping and its minutes."""
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.recipe_id, mpe.freeform_meal,
               mpe.cooked_status, mpe.derived_from_json, mpe.food_groups_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal,
               r.prep_time_minutes, r.cook_time_minutes
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, {_weekly_plan.slot_order_sql('mpe.slot')} ASC, mpe.id ASC
        """,
        (plan_id, household_id()),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _derived(row: dict) -> dict:
    try:
        return json.loads(row.get("derived_from_json") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def _minutes(row: dict) -> int | None:
    total = int(row.get("prep_time_minutes") or 0) + int(row.get("cook_time_minutes") or 0)
    return total or None


def _groups(row: dict) -> list[str] | None:
    try:
        return json.loads(row.get("food_groups_json") or "[]") or None
    except (TypeError, ValueError):
        return None


def keeps_its_question(derived: dict) -> bool:
    """
    An open slot that says something TRUE the household needs to know, and
    so stays a question: an allergy blocked every dish tried
    (allergen_gate — the carve-out fill_gaps_with_a_repeat keeps too), a
    night that was nobody-home until attendance changed (slot_needs), a
    holiday they are hosting or whose answer changed (big_meal, holidays).
    Emily's rule names the first two; the holiday ones are answers only
    the household can give.
    """
    constraint = str(derived.get("constraint") or "").lower()
    if constraint == _allergen_gate.ALLERGEN_CONSTRAINT or "allerg" in constraint:
        return True
    if derived.get("need") == "away" or derived.get("undone_by") == "attendance":
        return True
    if any(word in constraint for word in ("nobody_home", "attendance", "away")):
        return True
    return bool(derived.get("holiday")) or constraint in ("hosting", "holiday_answer_changed")


def _reheat(plan_id: int, old_ids: list[int], meal_date: str, slot: str, cook: dict, extra: dict) -> dict:
    """Put a reheat of `cook` on (meal_date, slot) in place of `old_ids`,
    and size the cook up for it — the fold's own two writes."""
    derived = dict(extra)
    derived["links_to"] = f"entry_id:{cook['id']}"
    written = _weekly_plan._replace_slot_entries(
        plan_id, old_ids, meal_date, slot, cook["meal"],
        food_groups=_groups(cook), reasoning="", derived_from=derived,
    )
    _meal_variety._write_cook_sides(plan_id, {cook["id"]: [f"{meal_date}:{slot}"]}, [], {"batched": []})
    return written


def _freezer_night(plan_id: int, old_ids: list[int], meal_date: str, slot: str, cook: dict, extra: dict) -> None:
    """A night eating a portion frozen on `cook` — more than three days
    after it (leftovers.MAX_LEFTOVER_DAYS), so not out of the fridge."""
    derived = dict(extra)
    derived[_leftovers.FROM_FREEZER_KEY] = {"cook": f"entry_id:{cook['id']}", "dish": cook["meal"]}
    _weekly_plan._replace_slot_entries(
        plan_id, old_ids, meal_date, slot, _leftovers.freezer_night_name(cook["meal"], cook["date"]),
        reasoning="", derived_from=derived,
    )
    _meal_variety._write_cook_sides(plan_id, {}, [(cook["id"], meal_date, slot)], {"batched": []})


# ---------- a night tagged Leftovers ----------

def apply_leftovers_nights(plan_id: int, intake: dict | None, dates: list[str],
                           prefer: list[tuple[str, str]] | None = None) -> dict:
    """
    Every date tagged `left` ("Leftovers" on the day sheet) in `dates` gets
    a dinner that reheats an earlier cook — the nearest dinner one to three
    days before, or a cook in `prefer` (the (date, slot) of a dish the
    household asked to have leftovers of) when one is in reach — never a
    new cook and never a question. A night already reheating a real cook
    is left alone unless a preferred cook is in reach. Past three days, a
    portion frozen on the nearest earlier dinner. With no earlier dinner at
    all (the period's first night), the model's own dinner stands.

    Runs after repair_leftover_chains, so the chains it reads are real.
    Never raises.
    """
    out = {"reheated": [], "frozen": [], "kept": [], "left": []}
    tags = (intake or {}).get("night_tags") or {}
    nights = sorted(d for d, t in tags.items() if "left" in (t or []) and "out" not in (t or []) and d in dates)
    for night in nights:
        try:
            rows = _plan_rows(plan_id)
            here = [r for r in rows if r["date"] == night and r["slot"] == "dinner"]
            if any(r["slot_state"] == "planned_empty" for r in here):
                continue  # nobody home, a day left out, a meal already past
            if any(keeps_its_question(_derived(r)) for r in here if r["slot_state"] == "open"):
                continue
            if any((r.get("cooked_status") or "") == "done" for r in here):
                continue
            chains = _leftovers.plan_leftover_chains(plan_id)
            keys = _leftovers.run_keys(plan_id)
            prefer_ids = {r["id"] for r in rows if (r["date"], r["slot"]) in set(prefer or [])}
            ids = [r["id"] for r in here]
            cook = _weekly_plan._nearest_cook(rows, keys, night, "dinner", exclude=set(ids),
                                              prefer=prefer_ids, min_days=1)
            current = next((chains["leftovers"][i] for i in ids if i in chains["leftovers"]), None)
            if current and (cook is None or current["source"]["entry_id"] == cook["id"]
                            or cook["id"] not in prefer_ids):
                out["kept"].append(night)
                continue
            extra = {"tags": ["left"], "constraint": LEFTOVERS_NIGHT_CONSTRAINT}
            if cook is not None:
                _reheat(plan_id, ids, night, "dinner", cook, extra)
                out["reheated"].append({"date": night, "from": cook["date"], "dish": cook["meal"]})
                continue
            earlier = [r for r in rows if r["slot"] == "dinner" and r["date"] < night
                       and _weekly_plan._is_cook_row(r)
                       and not _leftovers.too_many_in_a_row(keys, night, "dinner", r["meal"])]
            if earlier:
                far = max(earlier, key=lambda r: r["date"])
                _freezer_night(plan_id, ids, night, "dinner", far, extra)
                out["frozen"].append({"date": night, "from": far["date"], "dish": far["meal"]})
                continue
            out["left"].append(night)
        except Exception:
            logger.exception("Leftovers night %s not applied to plan %s; it stands as generated", night, plan_id)
    if out["reheated"] or out["frozen"]:
        logger.info("Plan %s leftovers nights: %s", plan_id, out)
    return out


# ---------- a dinner still missing or open ----------

def _dinner_gaps(rows: list[dict], dates: list[str]) -> list[dict]:
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        if r["slot"] == "dinner":
            by_date.setdefault(r["date"], []).append(r)
    gaps = []
    for d in dates:
        here = by_date.get(d) or []
        if not here:
            gaps.append({"date": d, "rows": []})
        elif all(r["slot_state"] == "open" for r in here) and not any(keeps_its_question(_derived(r)) for r in here):
            gaps.append({"date": d, "rows": here})
    return gaps


def _week_dinners(rows: list[dict]) -> list[dict]:
    """The week's own dinner cooks, one per dish, with how many nights
    each is on — the supply a repeat is chosen from."""
    supply: dict[str, dict] = {}
    for r in rows:
        if r["slot"] != "dinner" or not _weekly_plan._is_cook_row(r):
            continue
        row = supply.setdefault(r["meal"].strip().lower(), dict(r, nights=0, first=r["date"]))
        row["nights"] += 1
    return list(supply.values())


def _fresh_pick(plan_id: int, entry: dict, cap: int | None, keys: dict, week: set, budget, picker,
                because: str, derived_key: str, extra: dict, reason_line: str | None = None) -> dict | None:
    """One re-pick through the swap's picker, held to `cap` and to the
    two-meals-in-a-row rule, never a dish the week already has."""
    from . import swap_in_place as _swap

    def _too_long(candidate):
        minutes = _swap._pick_minutes(candidate)
        if cap and minutes and minutes > cap:
            return f"takes {minutes} minutes, and this meal only has {cap}"
        return None

    return _meal_variety._repick_entry(
        plan_id, entry, budget,
        avoid=sorted(week), because=because,
        reject=lambda name: (name.strip().lower() in week
                             or _leftovers.too_many_in_a_row(keys, entry["date"], entry["slot"], name)),
        reject_pick=_too_long, derived_key=derived_key, picker=picker,
        context_extra={"replacing_because": because[:1].upper() + because[1:] + "."},
        derived_extra=extra, reason_line=reason_line,
    )


def fill_open_dinners(plan_id: int, dates: list[str], caps: dict | None = None, budget=None,
                      picker=None, reserve: int | None = None) -> dict:
    """
    Make "a draft never leaves a dinner open that the household is home
    for" true (Emily's decision A, 2026-09-27). Every dinner in `dates`
    with no row, or only an `open` one that does not keep its question
    (keeps_its_question), becomes — first that works —

      1. a reheat of the nearest earlier dinner one to three days before
         (weekly_plan._nearest_cook: in reach, and never a third meal of
         one dish in a row) — free, and the cook is sized up for it;
      2. a fresh dish through the swap's own picker (meal_variety.
         _repick_entry), held to the night's cap from `caps` — the
         fresh-cook caps, {(date, slot): minutes} — and never a dish the
         week already has;
      3. a reheat of an earlier cook that already feeds another meal —
         better a bigger batch than a second full cook of it next door;
      4. a repeat of one of the week's own dinners that fits the cap.

    `reserve` calls of `budget` are left untouched for the allergen sweep
    that runs after this (default: the sweep's own two attempts; 0 for the
    call AFTER the sweep). A planned_empty row is never touched: nobody
    home, a day left out, a meal already past are all answers. Never raises.
    """
    out = {"reheated": [], "repicked": [], "repeated": [], "left": []}
    budget = _Reserved(budget or _allergen_gate.CallBudget(),
                       _sweep_reserve() if reserve is None else reserve)
    try:
        rows = _plan_rows(plan_id)
        gaps = _dinner_gaps(rows, dates)
    except Exception:
        logger.exception("Reading plan %s for open dinners failed; the week stands as generated", plan_id)
        return out
    for gap in gaps:
        d = gap["date"]
        try:
            rows = _plan_rows(plan_id)
            here = [r for r in rows if r["date"] == d and r["slot"] == "dinner"]
            ids = [r["id"] for r in here]
            keys = _leftovers.run_keys(plan_id)
            # A cook that already feeds another meal is not asked to feed a
            # third: a gap is a double batch at most, so the week does not
            # turn into four nights of one pot.
            feeding = set(_leftovers.plan_leftover_chains(plan_id)["sources"])
            cook = _weekly_plan._nearest_cook(rows, keys, d, "dinner", exclude=set(ids) | feeding)
            if cook is not None:
                _reheat(plan_id, ids, d, "dinner", cook, {"constraint": REHEAT_FILLS_GAP})
                out["reheated"].append({"date": d, "from": cook["date"], "dish": cook["meal"]})
                continue
            if not ids:
                ids = [_weekly_plan.plan_slot_open(
                    weekly_plan_id=plan_id, meal_date=d, slot="dinner", open_reason=_gap_reason(d, "dinner"),
                    derived_from={"constraint": "generation_gap"},
                )["entry_id"]]
            cap = _meal_variety._cap_at(caps, d, "dinner")
            week = {r["meal"].strip().lower() for r in rows if r["slot"] == "dinner" and (r["meal"] or "").strip()}
            entry = {"id": ids[0], "date": d, "slot": "dinner", "meal": "", "derived_from_json": "{}"}
            picked = None
            if len(ids) == 1:
                picked = _fresh_pick(
                    plan_id, entry, cap, keys, week, budget, picker,
                    because="nothing was planned for this dinner yet", derived_key="gap_repick",
                    extra={"constraint": REPICK_FILLS_GAP},
                )
            if picked is not None:
                out["repicked"].append({"date": d, "meal": picked.get("meal")})
                continue
            rows = _plan_rows(plan_id)
            again = _weekly_plan._nearest_cook(rows, keys, d, "dinner", exclude=set(ids))
            if again is not None:
                _reheat(plan_id, ids, d, "dinner", again, {"constraint": REHEAT_FILLS_GAP})
                out["reheated"].append({"date": d, "from": again["date"], "dish": again["meal"]})
                continue
            supply = [s for s in _week_dinners(rows)
                      if _meal_variety._fits({"minutes": _minutes(s)}, cap)
                      and not _leftovers.too_many_in_a_row(keys, d, "dinner", s["meal"])]
            if supply:
                pick = min(supply, key=lambda s: (s["nights"], s["first"], s["meal"]))
                _weekly_plan._replace_slot_entries(
                    plan_id, ids, d, "dinner", pick["meal"], food_groups=_groups(pick),
                    reasoning=_meal_variety.GAP_FILL_REASON,
                    derived_from={"constraint": _meal_variety.GAP_FILL_CONSTRAINT, "repeat_of": pick["meal"]},
                )
                out["repeated"].append({"date": d, "meal": pick["meal"]})
                continue
            out["left"].append(d)
        except Exception:
            logger.exception("Filling the %s dinner on plan %s failed; it stands as it was", d, plan_id)
            out["left"].append(d)
    if any(out.values()):
        logger.info("Plan %s open dinners: %s", plan_id, out)
    return out


# ---------- no dish on more than two meals in a row ----------

def _changeable(row: dict) -> bool:
    """A meal this pass may change: not cooked, not the household's own
    (their typed words, a meal brought over, a freezer portion they froze —
    meal_variety.theirs — or a weekday lunch they said is leftovers)."""
    derived = _derived(row)
    if (row.get("cooked_status") or "") == "done" or _meal_variety.theirs(derived):
        return False
    return derived.get("constraint") != _weekday_lunches.CONSTRAINT


def _tagged_leftovers(row: dict) -> bool:
    """A night the household tagged Leftovers — changed last, since the
    tag is theirs (review, 2026-09-27)."""
    derived = _derived(row)
    return derived.get("constraint") == LEFTOVERS_NIGHT_CONSTRAINT or "left" in (derived.get("tags") or [])


_CHANGED = "(changed)"


def _breaks_alone(keys: dict, run: list, pos) -> bool:
    """Whether changing the one meal at `pos` leaves nothing of `run` too
    long — so a run of five is broken by its middle meal, one change,
    rather than by re-picking two of its later ones (review, 2026-09-27)."""
    trial = dict(keys)
    trial[pos] = _CHANGED
    return not any(set(r) & set(run) for r in _leftovers.long_runs(trial))


def _run_left_flag(run: list, at: dict) -> dict | None:
    """The one line for a run this pass had to leave: on the first meal
    past the limit, naming the dish and the day it started ("Friday dinner
    is Thursday’s Beef Bowls again")."""
    pos = run[_leftovers.MAX_MEALS_IN_A_ROW]
    row, first = at.get(pos), at.get(run[0])
    if row is None or first is None:
        return None
    dish = (first["meal"] or "").strip()
    return {"kind": _draft_flags.RUN_LEFT, "entry_id": row["id"], "date": pos[0], "slot": pos[1],
            "dish": row["meal"], "text": _draft_flags.run_left_text(pos[0], pos[1], dish, run[0][0])}


def break_long_runs(plan_id: int, caps: dict | None = None, budget=None, picker=None,
                    reserve: int | None = None, targets: dict | None = None) -> dict:
    """
    Make "no dish on more than two lunches and dinners in a row" true
    (Emily, 2026-09-27, decision B). For each run longer than
    leftovers.MAX_MEALS_IN_A_ROW, the first meal past the limit that this
    pass may change (_changeable; a cook other meals still eat from comes
    last) becomes — first that works — a reheat of a different earlier
    cook in reach, a repeat of another of the week's own dishes for that
    meal that fits its cap (`caps`, the fresh-cook caps), or a fresh dish
    held to that cap. The two free answers come first because they keep
    the household's distinct-dish count as the count pass left it; a fresh
    pick adds a dish and spends a model call — so it is not made when
    `targets` ({slot: distinct dishes the household set}) says the slot
    already has as many as they asked for. Thursday dinner + Friday lunch stay; Friday's dinner is the
    one that changes, and on a rush night the cap keeps it quick.
    Never raises.
    """
    out = {"changed": [], "left": []}
    budget = _Reserved(budget or _allergen_gate.CallBudget(),
                       _sweep_reserve() if reserve is None else reserve)
    flags: list = []       # one line per run left standing
    changed: set = set()   # meals this pass has already changed
    gave_up: set = set()   # meals of a run it could not break
    try:
        for _ in range(24):
            keys = _leftovers.run_keys(plan_id)
            runs = [run for run in _leftovers.long_runs(keys) if not set(run) & gave_up]
            if not runs:
                break
            run = runs[0]
            rows = _plan_rows(plan_id)
            chains = _leftovers.plan_leftover_chains(plan_id)
            at = {}
            for r in rows:
                if r["slot_state"] == "planned":
                    at.setdefault((r["date"], r["slot"]), r)
            limit = _leftovers.MAX_MEALS_IN_A_ROW
            order = list(run[limit:]) + list(reversed(run[:limit]))
            candidates = [p for p in order if p in at and p not in changed and _changeable(at[p])]
            # The ONE change that breaks the whole run first (review,
            # 2026-09-27); then a night they did not tag Leftovers; then
            # anything but a cook others still eat from. Stable, so the
            # eating order above breaks ties.
            candidates.sort(key=lambda p: (not _breaks_alone(keys, run, p), _tagged_leftovers(at[p]),
                                           at[p]["id"] in chains["sources"]))
            if not candidates:
                gave_up.update(run)
                out["left"].append(run)
                flags.append(_run_left_flag(run, at))
                continue
            # One attempt per run: a run whose first changeable meal cannot
            # be changed is left and logged, rather than spending the week's
            # re-pick budget on every other meal of it in turn.
            pos = candidates[0]
            changed.add(pos)
            row = at[pos]
            d, slot = pos
            ids = [r["id"] for r in rows if r["date"] == d and r["slot"] == slot]
            dish = keys[pos]
            cook = _weekly_plan._nearest_cook(rows, keys, d, slot, exclude=set(ids))
            if cook is not None:
                _reheat(plan_id, ids, d, slot, cook, {"constraint": NOT_THREE_IN_A_ROW, "replaced": row["meal"]})
                out["changed"].append({"date": d, "slot": slot, "was": row["meal"], "now": cook["meal"], "as": "reheat"})
                continue
            cap = _meal_variety._cap_at(caps, d, slot)
            supply = [s for s in rows
                      if s["slot"] == slot and _weekly_plan._is_cook_row(s)
                      and _leftovers.dish_identity(s["meal"]) != dish
                      and _meal_variety._fits({"minutes": _minutes(s)}, cap)
                      and not _leftovers.too_many_in_a_row(keys, d, slot, s["meal"])]
            if supply:
                pick = supply[0]
                _weekly_plan._replace_slot_entries(
                    plan_id, ids, d, slot, pick["meal"], food_groups=_groups(pick),
                    reasoning=_meal_variety.GAP_FILL_REASON,
                    derived_from={"constraint": NOT_THREE_IN_A_ROW, "repeat_of": pick["meal"], "replaced": row["meal"]},
                )
                out["changed"].append({"date": d, "slot": slot, "was": row["meal"], "now": pick["meal"], "as": "repeat"})
                continue
            week = {r["meal"].strip().lower() for r in rows if r["slot"] == slot and (r["meal"] or "").strip()}
            week.add(dish)
            entry = {"id": row["id"], "date": d, "slot": slot, "meal": row["meal"], "derived_from_json": "{}"}
            picked = None
            # A fresh dish ADDS one to the slot. When the household set how
            # many dishes they want and the week is already there, their
            # number wins and the run stands (logged, and the quality
            # tripwire says so): Pomona does not add a dish they didn't ask
            # for to keep a rule they didn't state that way.
            target = (targets or {}).get(slot)
            distinct = {_leftovers.dish_identity(r["meal"]) for r in rows
                        if r["slot"] == slot and r["slot_state"] == "planned" and (r["meal"] or "").strip()}
            if len(ids) == 1 and not (target and len(distinct) >= target):
                picked = _fresh_pick(
                    plan_id, entry, cap, keys, week, budget, picker,
                    because=f"{row['meal']} would be on a third meal in a row",
                    derived_key="run_repick", extra={"constraint": NOT_THREE_IN_A_ROW},
                )
            if picked is not None:
                out["changed"].append({"date": d, "slot": slot, "was": row["meal"], "now": picked.get("meal"), "as": "repick"})
                continue
            gave_up.update(run)
            out["left"].append(run)
            flags.append(_run_left_flag(run, at))
    except Exception:
        logger.exception("Breaking long runs failed for plan %s; the week stands as it was", plan_id)
    # A run left standing is said, never silent (review, 2026-09-27): one
    # plain line on the meal past the limit, through the draft's own flags.
    try:
        _draft_flags.add(plan_id, [f for f in flags if f])
    except Exception:
        logger.exception("Recording the runs left on plan %s failed", plan_id)
    if out["changed"] or out["left"]:
        logger.info("Plan %s runs of one dish: %s", plan_id, out)
    return out
