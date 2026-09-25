"""
"Bring over from last week" — the meals the household didn't get to.

Emily, 2026-09-25 (option A of the mockup, Loop Board "Plan a week: bring
over last week's meals you didn't cook"): at the top of "Same as last
week?", last week's dinners and lunches that were never ticked cooked are
listed with a tick box each, nothing ticked for them. What they tick goes
to the planner as a fixed meal for the new week; Pomona picks the night.

Four jobs, one per public function, in the order a week meets them:

  last_week_uncooked — the offer, for get_week_intake_prefill. "Last week"
      is the plan drafted from the period of `last_intake` (the answers the
      household gave the time before), when that plan was approved; else
      the most recent approved plan starting before this period. A draft
      nobody approved was never last week's food. Only dinners and lunches,
      only a real dish (a recipe or a freeform meal), never a reheat night
      (a confirmed leftovers chain, a freezer portion, or a freeform
      "leftovers"/takeout line), and only nights already gone by: today's
      dinner isn't one they didn't get to yet. A dish on several nights is
      ONE row, "Was <its first night>". `groceries_bought` is True only
      when every grocery line that meal put on the list is ticked
      purchased — the screen says "· groceries bought" on nothing less.

  resolve — what save_week_intake stores. The screen sends back which
      offered rows were ticked (their entry_ids); the stored list is
      rebuilt from the offer itself, so a stale or hand-written id can't
      bring over something that was cooked, or wasn't last week's.

  choose_nights — Pomona picks the night, BEFORE the model is asked, from
      the rules the planner already follows: the earliest night of the
      period (the groceries are freshest then), never a night the
      household is out, away, left out of the plan, reheating, or holding
      a holiday; within that night's time cap (a rush Tuesday can't take a
      75-minute braise); a dinner stays a dinner and a lunch a lunch, and a
      lunch never lands on a weekday the household said is prepped or
      leftovers (weekday_lunches.apply_to_plan would write over it). The
      model is then told those slots are taken (agent's `brought_over`
      bullet), and

  apply_to_plan — writes them after generation, clearing whatever the
      model sent there anyway (telling is not preventing — the rule every
      pass in agent._finish_week_slots follows), through
      weekly_plan._replace_slot_entries, the one plan write. The entry
      carries derived_from.brought_over = {"from_date", "entry_ids"},
      which is what exempts it from the no-repeat passes
      (meal_variety.theirs), marks it on the draft ("From last week") and
      keeps what was already bought off the new list (bought_items, read
      by recipes._add_recipe_ingredients_for_entries).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

# The derived_from key a brought-over entry carries. Read by meal_variety,
# typed_requests, plan_quality, draft_opener, weekly_plan.get_week_menu and
# recipes' grocery ingest — one name, here.
KEY = "brought_over"
CONSTRAINT = "brought_over"
SLOTS = ("dinner", "lunch")

# The "why this?" line under a brought-over meal on the draft. Emily's to
# change; the weekday is the night it was planned for last week.
REASON = "Brought over from last week — it was on {weekday}."

# A freeform line that is not a dish of its own: a reheat, a takeaway.
_NOT_A_DISH = re.compile(r"leftovers?\b|take[\s-]?out|delivery|order in", re.IGNORECASE)
# The two night tags that mean no fresh dinner is cooked that night.
_NO_COOK_TAGS = ("out", "left")
# The two weekday-lunch answers whose lunch weekday_lunches.apply_to_plan
# rewrites after generation — a brought-over lunch there would be lost.
_REWRITTEN_LUNCH_KINDS = ("prepped", "leftovers")


def _weekday(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%A")


def _key(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def is_brought_over(derived: dict | None) -> bool:
    """Whether an entry's derived_from says it was brought over."""
    return bool(isinstance(derived, dict) and derived.get(KEY))


def _derived(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ---------- which plan was last week ----------

def _last_week_plan(conn, week_start: str):
    """The approved plan the household ate from before `week_start` — see
    the module docstring. None when there isn't one."""
    from . import weekly_plan as _weekly_plan

    last = conn.execute(
        "SELECT week_start FROM week_intake WHERE household_id = ? AND week_start < ? "
        "AND superseded_at IS NULL ORDER BY week_start DESC, revision DESC LIMIT 1",
        (household_id(), week_start),
    ).fetchone()
    if last:
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE household_id = ? AND status = 'approved' AND intake_id IN "
            "(SELECT id FROM week_intake WHERE household_id = ? AND week_start = ?) ORDER BY id DESC LIMIT 1",
            (household_id(), household_id(), last["week_start"]),
        ).fetchone()
        if plan:
            return plan
    best = None
    for plan in conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? AND status = 'approved' ORDER BY id",
        (household_id(),),
    ).fetchall():
        start = _weekly_plan.plan_period(plan)[0]
        if start < week_start and (best is None or start >= best[0]):
            best = (start, plan)
    return best[1] if best else None


# ---------- the offer ----------

def _statuses_for(conn, entry_ids: list[int]) -> list[str]:
    if not entry_ids:
        return []
    marks = ",".join("?" * len(entry_ids))
    return [
        r["status"] for r in conn.execute(
            f"SELECT DISTINCT g.id, g.status FROM meal_plan_grocery_links l "
            f"JOIN grocery_items g ON g.id = l.grocery_item_id "
            f"WHERE l.household_id = ? AND l.meal_plan_entry_id IN ({marks})",
            (household_id(), *entry_ids),
        ).fetchall()
    ]


def bought_items(entry_ids: list[int], conn=None) -> set[str]:
    """
    The ingredient names last week's list bought for these entries — every
    grocery line their ledger points at that is ticked purchased — as the
    lowercased names the ledger recorded (the recipe's own ingredient
    names, so the same recipe matches itself exactly). Empty when nothing
    can be shown to have been bought: a line unbought, removed, or gone
    from the list says nothing, and the ingredient is bought again.
    """
    ids = [int(i) for i in entry_ids or [] if isinstance(i, int) and not isinstance(i, bool)]
    if not ids:
        return set()
    own = conn is None
    if own:
        conn = get_conn()
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT l.item FROM meal_plan_grocery_links l JOIN grocery_items g ON g.id = l.grocery_item_id "
            f"WHERE l.household_id = ? AND l.meal_plan_entry_id IN ({marks}) AND g.status = 'purchased'",
            (household_id(), *ids),
        ).fetchall()
    finally:
        if own:
            conn.close()
    return {(r["item"] or "").strip().lower() for r in rows if (r["item"] or "").strip()}


def last_week_uncooked(conn, week_start: str) -> list[dict]:
    """
    Last week's dinners and lunches that were never cooked — the offer on
    "Same as last week?". [] when there is no last week or nothing was
    missed; the page hides the section then. Each row:
      {"entry_ids": [...], "meal", "recipe_id", "slot", "date", "weekday",
       "groceries_bought"}
    `date`/`weekday` are the dish's first uncooked night. Dinners first.
    """
    from . import cooker as _cooker
    from . import leftovers as _leftovers

    plan = _last_week_plan(conn, week_start)
    if plan is None:
        return []
    today = _cooker.household_today(conn=conn).isoformat()
    before = min(week_start, today)
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.recipe_id, mpe.freeform_meal, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ? AND mpe.slot IN ('dinner', 'lunch')
          AND mpe.slot_state = 'planned' AND mpe.cooked_status = 'pending'
          AND mpe.component_category IS NULL AND mpe.date < ?
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (household_id(), plan["id"], before),
    ).fetchall()
    chains = _leftovers.plan_leftover_chains(plan["id"], conn=conn)
    dishes: dict[str, dict] = {}
    for r in rows:
        meal = (r["meal"] or "").strip()
        if not meal or r["id"] in chains["leftovers"]:
            continue
        derived = _derived(r["derived_from_json"])
        if derived.get("links_to") or derived.get(_leftovers.FROM_FREEZER_KEY):
            continue
        if not r["recipe_id"] and _NOT_A_DISH.search(r["freeform_meal"] or ""):
            continue
        dish = dishes.setdefault(_key(meal), {
            "entry_ids": [], "meal": meal, "recipe_id": r["recipe_id"], "slot": r["slot"],
            "date": r["date"], "weekday": _weekday(r["date"]),
        })
        dish["entry_ids"].append(r["id"])
    out = []
    for dish in dishes.values():
        statuses = _statuses_for(conn, dish["entry_ids"])
        dish["groceries_bought"] = bool(statuses) and all(s == "purchased" for s in statuses)
        out.append(dish)
    # Dinners first, then lunches — the meals most likely missed, and the
    # ones with the most bought for them — each in the order they fell.
    return sorted(out, key=lambda d: (SLOTS.index(d["slot"]), d["date"]))


def resolve(choices, week_start: str) -> list[dict]:
    """
    The ticked rows, as stored on week_intake.brought_over_json: each
    choice names an offered row by any of its entry_ids, and the stored
    row is the offer's own (entry_ids, meal, recipe_id, slot, date). A
    choice matching nothing on offer is dropped, not refused — a meal
    cooked since the page opened simply isn't brought over. A malformed
    choice is the client error it is.
    """
    if not isinstance(choices, list):
        raise ValueError("brought_over must be a list of meals.")
    wanted: list[set[int]] = []
    for choice in choices:
        ids = choice.get("entry_ids") if isinstance(choice, dict) else None
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            raise ValueError("Each meal brought over needs the entry_ids it was offered with.")
        wanted.append(set(ids))
    if not wanted:
        return []
    conn = get_conn()
    try:
        offer = last_week_uncooked(conn, week_start)
    finally:
        conn.close()
    out = []
    for dish in offer:
        if any(set(dish["entry_ids"]) & w for w in wanted):
            out.append({k: dish[k] for k in ("entry_ids", "meal", "recipe_id", "slot", "date")})
    return out


# ---------- placing them ----------

def _minutes(recipe_ids: list[int]) -> dict[int, int | None]:
    ids = [i for i in recipe_ids if i]
    if not ids:
        return {}
    conn = get_conn()
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, prep_time_minutes, cook_time_minutes FROM recipes WHERE household_id = ? AND id IN ({marks})",
            (household_id(), *ids),
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        total = int(r["prep_time_minutes"] or 0) + int(r["cook_time_minutes"] or 0)
        out[r["id"]] = total or None
    return out


def choose_nights(
    items: list[dict] | None,
    dates: list[str],
    intake: dict | None,
    *,
    slot_needs: dict | None = None,
    holidays: list[dict] | None = None,
    zero_slots: set[str] | frozenset = frozenset(),
    cap_for=None,
) -> tuple[list[dict], list[dict]]:
    """
    Where each brought-over meal goes: (placed, not_placed). Each placed
    row is the stored row plus `on` (the new date) — `date` stays the
    night it was planned for last week. See the module docstring for the
    rules; `cap_for(date, slot)` is the planner's own time cap
    (agent._meal_minutes_cap), None meaning no cap.

    Dinners are placed before lunches, each in the order they fell last
    week, each on the earliest night left that fits. A lunch that finds
    no other day may go on a day lunch travels (packed_lunch_days — food
    that packs cold is a preference, not a promise); nothing else is
    relaxed, and a meal with nowhere to go is left out and logged rather
    than put on a night that breaks the household's own answer.
    """
    items = [i for i in (items or []) if isinstance(i, dict) and i.get("slot") in SLOTS and i.get("meal")]
    if not items:
        return [], []
    intake = intake or {}
    skipped = set(intake.get("skipped_days") or [])
    tags = intake.get("night_tags") or {}
    packed = set(intake.get("packed_lunch_days") or [])
    kinds = {}
    for d in ((intake.get("weekday_lunches") or {}).get("days") or []):
        if isinstance(d, dict) and d.get("date"):
            kinds[d["date"]] = d.get("kind")
    blocked: set[tuple[str, str]] = set()
    for key in ("away_slots", "ready_made_slots"):
        for s in (slot_needs or {}).get(key) or []:
            blocked.add((s.get("date"), s.get("slot")))
    for h in holidays or []:
        answer = (h.get("answer") or {}).get("answer")
        if answer in ("out", "hosting"):
            blocked.add((h.get("date"), "dinner"))

    minutes = _minutes([i.get("recipe_id") for i in items])
    taken: set[tuple[str, str]] = set()
    placed, missed = [], []

    def open_night(d: str, slot: str, allow_packed: bool) -> bool:
        if (d, slot) in taken or (d, slot) in blocked or d in skipped or slot in zero_slots:
            return False
        if slot == "dinner" and any(t in (tags.get(d) or []) for t in _NO_COOK_TAGS):
            return False
        if slot == "lunch":
            if kinds.get(d) in _REWRITTEN_LUNCH_KINDS:
                return False
            if d in packed and not allow_packed:
                return False
        return True

    ordered = sorted(items, key=lambda i: (SLOTS.index(i["slot"]), i.get("date") or ""))
    for item in ordered:
        slot = item["slot"]
        need = minutes.get(item.get("recipe_id"))
        chosen = None
        for allow_packed in ((False, True) if slot == "lunch" else (False,)):
            for d in dates:
                if not open_night(d, slot, allow_packed):
                    continue
                cap = cap_for(d, slot) if cap_for else None
                if cap is not None and need is not None and need > cap:
                    continue
                chosen = d
                break
            if chosen:
                break
        if chosen is None:
            missed.append(item)
            continue
        taken.add((chosen, slot))
        placed.append({**item, "on": chosen})
    if missed:
        logger.info("Bring over: no night fits %s", ", ".join(f"{m['meal']} ({m['slot']})" for m in missed))
    return placed, missed


def prompt_lines(placed: list[dict]) -> list[dict]:
    """What the model is told about the slots already taken."""
    return [{"date": p["on"], "slot": p["slot"], "meal": p["meal"]} for p in placed]


def apply_to_plan(plan_id: int, placed: list[dict] | None) -> list[dict]:
    """
    Put each placed meal on its night of the just-generated plan, clearing
    whatever the model sent there. Never raises: a meal that can't be
    written is logged, and the week stands. Returns what was written.
    """
    from . import weekly_plan as _weekly_plan

    written = []
    for p in placed or []:
        try:
            conn = get_conn()
            try:
                ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM meal_plan_entries WHERE household_id = ? AND weekly_plan_id = ? "
                    "AND date = ? AND slot = ? AND component_category IS NULL ORDER BY id",
                    (household_id(), plan_id, p["on"], p["slot"]),
                ).fetchall()]
            finally:
                conn.close()
            result = _weekly_plan._replace_slot_entries(
                plan_id, ids, p["on"], p["slot"], p["meal"],
                reasoning=REASON.format(weekday=_weekday(p["date"])),
                derived_from={
                    KEY: {"from_date": p["date"], "entry_ids": list(p.get("entry_ids") or [])},
                    "constraint": CONSTRAINT,
                },
            )
            written.append({"date": p["on"], "slot": p["slot"], "meal": p["meal"],
                            "entry_id": result.get("entry_id")})
        except Exception:
            logger.exception("Bring over: %r could not be put on %s %s of plan %s",
                             p.get("meal"), p.get("on"), p.get("slot"), plan_id)
    if written:
        logger.info("Bring over: plan %s %s", plan_id, written)
    return written
