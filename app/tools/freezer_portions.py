"""
The cooked portions a night off put in the freezer, planned back into a week.

Taking the night off already saves the extra portion: tonight.tonight_night_off
writes an inventory_items row at location 'freezer', item "<dish> (cooked)",
source 'night_off' (see _freeze_portion there). What it could not do was get
it eaten. Measured on a throwaway database before this module existed — a
night off, then the next week drafted — the portion reached the planner only
inside `current_inventory`, the general "what's on hand" list the prompt
explicitly tells the model NOT to let sway which dishes it picks; the week
came back seven fresh cooks, and the plan's prep_tasks were empty, because
defrost matches a recipe's INGREDIENTS against the freezer by name and a
cooked portion is nobody's ingredient. So a portion frozen on a Tuesday was
food the household had paid for, cooked, and would never be reminded about.

Four jobs, one per public function, in the order a week meets them — the
shape bring_over.py already follows, deliberately, because this is the same
kind of pass (a meal the household effectively chose, placed by Pomona
rather than by the model, written after generation whatever the model sent):

  portions_to_plan — the portions still waiting. A freezer row this app
      froze, oldest first, that no live plan already stands a night on.

  choose_nights — Pomona picks the night, BEFORE the model is asked: a
      short-on-time night first (time_caps is the app's one idea of "busy",
      and a portion that only needs reheating fits any cap, so the cap
      RANKS here rather than filtering as it does for bring_over), never a
      night the household is out, away, reheating already, left out of the
      plan or holding a holiday. The model is then told the slot is taken.

  apply_to_plan — writes them after generation, clearing whatever the model
      sent there (telling is not preventing — the rule every pass in
      agent._finish_week_slots follows). The night is a FREEFORM row named
      leftovers.frozen_portion_night_name, which is the shape a within-week
      freezer night already takes (meal_variety's fold writes one): freeform
      never reaches the shopping list, the leftovers regex in
      weekly_plan.build_slot reads it as a reheat, and weekly_plan._is_cook
      counts it as a meal and not a cook. It carries
      leftovers.FROM_FREEZER_KEY for every reader that already knows that
      shape, plus KEY for the three things only this pass can answer: which
      inventory row it is eating, that the fridge move has to be booked for
      it, and that the no-repeat and count passes must leave it standing.

  defrost_candidates — read by defrost._candidates_from_plan, so the fridge
      move is booked, re-synced and swept by the machinery that already
      does that for a frozen ingredient. Nothing here writes a prep row.

Two more, off that path:

  portion_eaten — ticking the night done takes the row out of the freezer.
      That is how a portion is planned ONCE: the entry claiming it keeps
      other weeks off it, and the row leaving means no later week can even
      see it. Called from cooker.deplete_inventory_for_meal, which is where
      "this meal was cooked, so this food is gone" already lives.

  sweep_use_soon — a portion nobody has planned in USE_SOON_AFTER_DAYS is
      said once, on the attention queue, the same way a night off says it
      about food it dropped.

NOT re-exported from app/tools/__init__.py, deliberately, the way
bring_over and weekday_lunches are not: every caller imports the module.
That package's namespace already holds `apply_holiday_answers_to_plan`,
which is holidays.apply_to_plan under an alias precisely because three
passes now have a function of that name, and a bare one would silently be
whichever module was imported last. Nothing here is a chat tool, so the
"add it to __init__ too" rule does not apply. And no function in this file
is called `freezer_portions`: a module whose name is also an exported
function's costs the package its own import convention (CLAUDE.md,
2026-09-24 — `unbatch.py` had to become `batch_undo.py` for that).
"""
from __future__ import annotations

import json
import logging
from datetime import date

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

# The derived_from key a planned portion carries: {"inventory_item_id",
# "dish", "quantity"}. Read by meal_variety (as the household's own
# choice), defrost (to book the move), cooker (to take the row out of the
# freezer once it is eaten) and this module. One name, here.
KEY = "freezer_portion"
CONSTRAINT = "freezer_portion"

# A portion is dinner. Emily's card says so, and it is the honest slot: a
# cooked dinner's leftovers are a dinner, and a weekday lunch the household
# answered for (weekday_lunches) is a different question with its own pass.
SLOT = "dinner"

# How many frozen portions one drafted week may be asked to eat. An
# ASSUMPTION, and one line to change: a week is the household's cooking,
# and a second reheat night nobody asked for is the app taking the week
# over. With one a week the freezer clears at the rate it fills, and
# sweep_use_soon is what says something when it doesn't.
MAX_PORTIONS_PER_WEEK = 1

# How long a cooked portion needs in the fridge. Deliberately NOT
# defrost.lead_hours_for_item, whose table keys on words for RAW cuts of
# family-pack size — "Roast Chicken (cooked)" would read as a 72-hour whole
# roast. This is a few servings in a container, and Emily's card asks for
# "the night before", which is what 24 hours against any dinner window
# comes to (see defrost._move_date, which floors at a full day regardless).
PORTION_LEAD_HOURS = 24.0

# When a portion nobody has planned is worth mentioning — ~4 weeks, an
# ASSUMPTION, changeable here in one line. This is a NUDGE, not food
# safety: tonight.FROZEN_COOKED_KEEPS_DAYS (90) is how long the portion
# actually keeps, and it is the row's own use-by. Four weeks is only how
# long the app will watch a portion go unplanned before saying so.
USE_SOON_AFTER_DAYS = 28

# The "why this?" line under a planned portion. Emily's to change.
REASON = "You froze a portion of {dish} — this is the night to eat it."

# What the attention queue says about one nobody has planned. Uses the same
# kind as a night off's own use-soon note (tonight.USE_SOON_KIND), so it
# lands on the surfaces that note already reaches — Cook's fold and the
# morning text — rather than inventing a second queue for the same fact.
USE_SOON_SUMMARY = "{dish} has been in the freezer {weeks} — worth eating soon."

# The two night tags that mean no dinner is eaten at home that night.
_NO_COOK_TAGS = ("out", "left")
# A plan whose nights still count as claimed. A retired plan's are not: the
# week was replaced, so nothing on it is going to be eaten.
_LIVE_PLAN_STATUSES = ("draft", "approved")


def _derived(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def portion_on(derived) -> dict | None:
    """The portion an entry is eating, or None — the one door onto KEY, so
    no reader has to know the key's own name or guard its shape."""
    value = _derived(derived).get(KEY)
    if not isinstance(value, dict):
        return None
    try:
        item_id = int(value.get("inventory_item_id"))
    except (TypeError, ValueError):
        return None
    return {**value, "inventory_item_id": item_id}


# ---------- the portions ----------

def _frozen_rows(conn) -> list[dict]:
    """
    Every cooked portion this app put in the freezer, oldest first. Keyed
    on the `source` a night off stamps, not on the item's name: the name is
    copy and a household can edit it, and a row somebody typed in by hand
    is not one this app knows the shape of.
    """
    from . import tonight as _tonight

    rows = conn.execute(
        "SELECT id, item, quantity, created_at, expiration_date FROM inventory_items "
        "WHERE household_id = ? AND source = ? AND location = 'freezer' "
        "ORDER BY created_at ASC, id ASC",
        (household_id(), _tonight.NIGHT_OFF_FROZEN_SOURCE),
    ).fetchall()
    out = []
    for r in rows:
        dish = _tonight.frozen_portion_dish(r["item"])
        if not dish:
            continue
        out.append({
            "inventory_item_id": r["id"], "item": r["item"], "dish": dish,
            "quantity": (r["quantity"] or "").strip(),
            "frozen_on": (r["created_at"] or "")[:10],
            "expiration_date": r["expiration_date"] or "",
        })
    return out


def _claimed_item_ids(conn, ignore_dates: set[str] | frozenset = frozenset()) -> set[int]:
    """
    The portions a live plan already stands a night on — which is what makes
    one planned ONCE rather than onto two weeks at a time.

    Two things are deliberately NOT claims.

    A claim inside `ignore_dates`, the period about to be drafted: that
    night belongs to the very week being replaced (retire_overlapping_plans
    runs at the END of generation, so the outgoing draft is still 'draft'
    when choose_nights asks), and reading it as a claim would lose the
    portion every time a household re-drafted the same week.

    And a claim on a night that has already gone by. Eating the portion
    deletes the row (portion_eaten), so a night ticked done leaves nothing
    here to claim at all — which means a past claim can only be a night
    nobody ticked, and as far as the app's own records go that portion is
    still in the freezer and still unplanned. Offering it again is the
    consistent answer: the worst this gets wrong is a portion eaten without
    a tick being offered a second time, which costs a swap, against the
    thing this whole module exists to stop — a portion nothing ever mentions
    again. (yesterday_check does not ask about a reheat, so there is no
    other door through which the app could learn.)
    """
    from . import cooker as _cooker

    today = _cooker.household_today(conn=conn).isoformat()
    claimed = set()
    for r in conn.execute(
        "SELECT mpe.date, mpe.derived_from_json FROM meal_plan_entries mpe "
        "JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        "WHERE mpe.household_id = ? AND mpe.slot_state = 'planned' AND mpe.date >= ? "
        "AND wp.status IN ({})".format(",".join("?" * len(_LIVE_PLAN_STATUSES))),
        (household_id(), today, *_LIVE_PLAN_STATUSES),
    ).fetchall():
        portion = portion_on(r["derived_from_json"])
        if portion and r["date"] not in ignore_dates:
            claimed.add(portion["inventory_item_id"])
    return claimed


def portions_to_plan(dates: list[str] | None = None, conn=None) -> list[dict]:
    """
    The portions waiting for a night, oldest first — what choose_nights is
    handed. `dates` is the period about to be drafted (see
    _claimed_item_ids). [] for a household that has never taken a night
    off, which is nearly all of them.
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        claimed = _claimed_item_ids(conn, set(dates or []))
        return [p for p in _frozen_rows(conn) if p["inventory_item_id"] not in claimed]
    finally:
        if own:
            conn.close()


# ---------- placing them ----------

def choose_nights(
    portions: list[dict] | None,
    dates: list[str],
    intake: dict | None,
    *,
    slot_needs: dict | None = None,
    holidays: list[dict] | None = None,
    zero_slots: set[str] | frozenset = frozenset(),
    cap_for=None,
    taken: set[tuple[str, str]] | frozenset = frozenset(),
) -> tuple[list[dict], list[dict]]:
    """
    Where each portion goes: (placed, not_placed). Each placed row is the
    portion plus `on` (the date). `cap_for(date, slot)` is the planner's own
    time cap (agent._meal_minutes_cap) — used to ORDER the nights, tightest
    first, never to refuse one: a portion is reheated, so it fits any cap,
    and a short-on-time night is exactly where it earns its keep.

    `taken` is the slots another pass has already claimed for this period
    (bring_over's, which runs first). At most MAX_PORTIONS_PER_WEEK are
    placed; the rest wait for next week and sweep_use_soon says so if they
    wait too long.
    """
    portions = [p for p in (portions or []) if isinstance(p, dict) and p.get("dish")]
    if not portions or SLOT in zero_slots:
        return [], []
    intake = intake or {}
    skipped = set(intake.get("skipped_days") or [])
    tags = intake.get("night_tags") or {}
    blocked = set(taken)
    for key in ("away_slots", "ready_made_slots"):
        for s in (slot_needs or {}).get(key) or []:
            blocked.add((s.get("date"), s.get("slot")))
    for h in holidays or []:
        if ((h.get("answer") or {}).get("answer")) in ("out", "hosting"):
            blocked.add((h.get("date"), "dinner"))

    def open_night(d: str) -> bool:
        if (d, SLOT) in blocked or d in skipped:
            return False
        return not any(t in (tags.get(d) or []) for t in _NO_COOK_TAGS)

    # A night with a cap on it is a night the household said is short on
    # time (time_caps.minutes_cap: a rush tag, or their own weeknight
    # limit). Tightest cap first, then earliest — so the portion lands on
    # the busiest night of the week rather than merely the first one.
    order = sorted(
        (d for d in dates if open_night(d)),
        key=lambda d: (
            (1, 0) if (cap_for(d, SLOT) if cap_for else None) is None else (0, cap_for(d, SLOT)),
            d,
        ),
    )
    placed, missed = [], []
    for portion in portions:
        if len(placed) >= MAX_PORTIONS_PER_WEEK or not order:
            missed.append(portion)
            continue
        placed.append({**portion, "on": order.pop(0)})
    if missed:
        logger.info(
            "Freezer portions: %s left in the freezer this week",
            ", ".join(str(m.get("dish")) for m in missed),
        )
    return placed, missed


def prompt_lines(placed: list[dict]) -> list[dict]:
    """What the model is told about the slots already taken."""
    from . import leftovers as _leftovers

    return [
        {"date": p["on"], "slot": SLOT,
         "meal": _leftovers.frozen_portion_night_name(p["dish"])}
        for p in placed
    ]


def apply_to_plan(plan_id: int, placed: list[dict] | None) -> list[dict]:
    """
    Put each placed portion on its night of the just-generated plan,
    clearing whatever the model sent there. Never raises: a portion that
    cannot be written is logged and the week stands — it is still in the
    freezer, and next week's draft will offer it again.
    """
    from . import leftovers as _leftovers
    from . import weekly_plan as _weekly_plan

    written = []
    for p in placed or []:
        try:
            conn = get_conn()
            try:
                ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM meal_plan_entries WHERE household_id = ? AND weekly_plan_id = ? "
                    "AND date = ? AND slot = ? AND component_category IS NULL ORDER BY id",
                    (household_id(), plan_id, p["on"], SLOT),
                ).fetchall()]
            finally:
                conn.close()
            result = _weekly_plan._replace_slot_entries(
                plan_id, ids, p["on"], SLOT,
                _leftovers.frozen_portion_night_name(p["dish"]),
                reasoning=REASON.format(dish=p["dish"]),
                derived_from={
                    # The vocabulary a from-freezer night already has, so
                    # every reader that knows it (meal_variety's grouping,
                    # bring_over's "not a meal they missed", the Plan card)
                    # keeps working without learning a fourth shape. No
                    # `cook`: this portion's cook is not on this plan, and
                    # naming a row that isn't there is worse than saying
                    # nothing (meal_variety._slot_nights reads a missing
                    # `cook` as no reference, which is the truth here).
                    _leftovers.FROM_FREEZER_KEY: {"dish": p["dish"]},
                    KEY: {
                        "inventory_item_id": p["inventory_item_id"],
                        "dish": p["dish"], "quantity": p.get("quantity") or "",
                    },
                    "constraint": CONSTRAINT,
                },
            )
            written.append({"date": p["on"], "slot": SLOT, "dish": p["dish"],
                            "inventory_item_id": p["inventory_item_id"],
                            "entry_id": result.get("entry_id")})
        except Exception:
            logger.exception("Freezer portion %r could not be put on %s of plan %s",
                             p.get("dish"), p.get("on"), plan_id)
    if written:
        logger.info("Freezer portions: plan %s %s", plan_id, written)
    return written


# ---------- the fridge move ----------

def defrost_candidates(plan: dict, dinner_window: str | None) -> list[dict]:
    """
    One fridge move per planned portion, in the shape
    defrost._candidates_from_plan returns — so sync_defrost_tasks books it,
    keeps its status across a re-sync and sweeps it when the night is
    swapped away, exactly as it does for a frozen ingredient. Nothing here
    writes a prep row.

    The portion is matched by the inventory id the entry carries, never by
    name: a cooked portion is nobody's ingredient, which is the whole
    reason defrost's own name matching never fired for one.
    """
    from . import defrost as _defrost

    meals = plan.get("meals") or []
    if not meals:
        return []
    conn = get_conn()
    try:
        # The LIKE does the filtering, so the common case — a plan with no
        # portion on it, which is nearly every plan — is one cheap query and
        # no second one at all.
        portions = {}
        for r in conn.execute(
            "SELECT id, derived_from_json FROM meal_plan_entries "
            "WHERE household_id = ? AND weekly_plan_id = ? AND derived_from_json LIKE ?",
            (household_id(), plan.get("weekly_plan_id"), f'%"{KEY}"%'),
        ).fetchall():
            portion = portion_on(r["derived_from_json"])
            if portion:
                portions[r["id"]] = portion
        if not portions:
            return []
        rows = {
            r["id"]: r for r in conn.execute(
                "SELECT id, item, quantity FROM inventory_items WHERE household_id = ? AND location = 'freezer'",
                (household_id(),),
            ).fetchall()
        }
    finally:
        conn.close()
    out = []
    for m in meals:
        entry_id = m.get("entry_id")
        portion = portions.get(entry_id)
        if not portion:
            continue
        row = rows.get(portion["inventory_item_id"])
        if row is None:
            # Eaten, or taken out of the freezer by hand. A reminder to
            # move something that isn't there is worse than none.
            continue
        out.append({
            "inventory_item_id": row["id"],
            "meal_plan_entry_id": entry_id,
            "task_date": _defrost._move_date(m["date"], PORTION_LEAD_HOURS, dinner_window),
            "description": _defrost.portion_move_description(portion["dish"], m["date"]),
            "related_meal": portion["dish"],
            "quantity": (row["quantity"] or "").strip(),
            "lead_hours": PORTION_LEAD_HOURS,
            "lead_tier": "cooked_portion",
        })
    return out


# ---------- eaten ----------

def portion_eaten(entry_id: int) -> dict | None:
    """
    Take the portion this night ate out of the freezer. Called from
    cooker.deplete_inventory_for_meal, which is where "this meal was
    cooked, so this food is gone" already lives — so the tick's own
    claim/release machinery covers it and a re-tick cannot take a second
    portion out of a freezer that only ever held one.

    DELETED outright rather than subtracted from. A portion is one row this
    app wrote whole (tonight._freeze_portion never merges into an existing
    one, precisely so it can be reasoned about), and eating it eats all of
    it — so the leniency deplete_inventory_for_meal refuses for an
    ingredient with an unparseable quantity ("a big bag") does not apply:
    there is no shelf here to be mostly still full.

    None when the entry is not eating a portion, which is nearly always.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
        portion = portion_on(row["derived_from_json"]) if row else None
        if not portion:
            return None
        item = conn.execute(
            "SELECT id, item FROM inventory_items WHERE id = ? AND household_id = ?",
            (portion["inventory_item_id"], household_id()),
        ).fetchone()
        if item is None:
            return None
        conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?",
                     (item["id"], household_id()))
        conn.commit()
    finally:
        conn.close()
    # The shape deplete_inventory_for_meal reports a depletion in, so the
    # tick keeps its claim (cooker._changed_any_inventory_row reads
    # units_reconciled) and says what came out of the kitchen.
    return {
        "ingredient": portion["dish"], "item": item["item"],
        "result": {"item_id": item["id"], "item": item["item"],
                   "removed": True, "units_reconciled": True},
    }


# ---------- the nudge ----------

def _weeks_phrase(days: int) -> str:
    weeks = max(1, days // 7)
    return "a week" if weeks == 1 else f"{weeks} weeks"


def sweep_use_soon() -> list[dict]:
    """
    Say once, on the attention queue, that a portion has been in the
    freezer USE_SOON_AFTER_DAYS without ever being planned. Lazy — run from
    the queue's own reader (attention.get_attention_items), the shape
    retire_expired_drafts and sync_due_staples already use, so there is no
    scheduler to own.

    ONCE, ever, per portion: an existing row of this kind naming the same
    inventory id stops a second, whatever its status. add_attention_item's
    own dedupe would re-queue after the household answered, and being told
    a third time about a portion you have decided to keep frozen is how a
    nudge stops being read.

    Never raises: this is a reminder, and the queue it rides on answers
    real questions.
    """
    from . import cooker as _cooker
    from . import tonight as _tonight

    queued = []
    try:
        conn = get_conn()
        try:
            today = _cooker.household_today(conn=conn)
            said = set()
            for r in conn.execute(
                "SELECT detail_json FROM attention_items WHERE household_id = ? AND kind = ?",
                (household_id(), _tonight.USE_SOON_KIND),
            ).fetchall():
                detail = _derived(r["detail_json"])
                try:
                    said.add(int(detail.get("inventory_item_id")))
                except (TypeError, ValueError):
                    continue
            waiting = portions_to_plan(conn=conn)
        finally:
            conn.close()
        for portion in waiting:
            item_id = portion["inventory_item_id"]
            if item_id in said or not portion["frozen_on"]:
                continue
            try:
                days = (today - date.fromisoformat(portion["frozen_on"])).days
            except ValueError:
                continue
            if days < USE_SOON_AFTER_DAYS:
                continue
            _queue(portion, days)
            queued.append({"inventory_item_id": item_id, "dish": portion["dish"], "days": days})
    except Exception:
        logger.exception("Could not sweep the freezer for portions to use soon")
    return queued


def _queue(portion: dict, days: int) -> None:
    from . import attention as _attention
    from . import tonight as _tonight

    _attention.add_attention_item(
        _tonight.USE_SOON_KIND,
        USE_SOON_SUMMARY.format(dish=portion["dish"], weeks=_weeks_phrase(days)),
        {"items": [portion["item"]], "dish": portion["dish"],
         "inventory_item_id": portion["inventory_item_id"]},
    )


__all__ = [
    "KEY", "CONSTRAINT", "SLOT", "MAX_PORTIONS_PER_WEEK", "PORTION_LEAD_HOURS",
    "USE_SOON_AFTER_DAYS", "portion_on", "portions_to_plan", "choose_nights",
    "prompt_lines", "apply_to_plan", "defrost_candidates", "portion_eaten",
    "sweep_use_soon",
]
