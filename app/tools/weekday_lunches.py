"""
Weekday lunches: how many are made on a prep day, how many are leftovers
from dinner, how many are cooked that day.

Emily, 2026-09-25 (Loop Board "Plan a week step 3: weekday lunches, how
many prepped, leftovers or cooked that day", mockup A1): step 3 of Plan a
week stops asking only which lunches travel and asks how each Monday-to-
Friday lunch gets made. Three counts that add up to the week's weekday
lunches, the days that are cooked fresh, the prep days, and a day-by-day
list the household can tap to change. Weekend lunches aren't asked about.

The answer is stored on the week's intake (week_intake.weekday_lunches_json)
in one shape, written only by `normalize`:

    {
      "counts":    {"prepped": 3, "leftovers": 1, "cooked": 1},
      "prep_days": ["sunday"],             # this week's, lowercase, in week order from Sunday
      "days": [                            # one per weekday lunch the household answered, by date
        {"date": "2026-09-28", "weekday": "monday",    "kind": "prepped",   "prep_day": "sunday"},
        {"date": "2026-09-30", "weekday": "wednesday", "kind": "leftovers", "from_dinner": "2026-09-29"},
        {"date": "2026-10-02", "weekday": "friday",    "kind": "cooked"},
        ...
      ]
    }

`{}` means the question was never answered for that week, and the planner
behaves exactly as it did before this existed. The dates are what planning
reads; `weekday` and `prep_days` are what the NEXT week reads to open
already answered ("Same as last week?"), because a date never repeats and
a Tuesday does — see `carryover`.

What each kind means to the planner (apply_to_plan, and the generation
prompt's `intake.weekday_lunches` bullet):

- prepped: one cook for the prepped lunches that share a prep day, sized
  for all of them, eaten within leftovers.MAX_LEFTOVER_DAYS (3) of the
  prep day; a lunch further out than that eats a portion frozen on the
  cook. No time cap (time_caps.minutes_cap, lunch_kind="prepped").
- leftovers: that lunch reheats the previous evening's dinner, so the
  dinner is cooked bigger (the ordinary leftovers chain:
  links_to on the lunch, make_double_for on the dinner).
- cooked: cooked that day, WEEKDAY_LUNCH_MAX_MINUTES (20) or less — even
  on a prep weekday, because the household said so for this day.

Nothing here invents a chain shape. The writes are the ones the fold in
meal_variety makes (weekly_plan._replace_slot_entries for a night whose
dish changes, meal_variety._write_cook_sides for the cook's
make_double_for and a frozen portion), so every reader of a chain — the
Cook screen, the shopping list, defrost, prep sessions — already knows
what they mean.

One limit, named rather than hidden: a prepped batch is cooked on its
FIRST lunch's entry, not on the prep day itself. A chain can't hold a cook
on a day the dish isn't eaten (cook_ahead.apply_prep_day_batches says the
same), so the cook entry carries the prep day on its derived_from and its
reasoning says when to cook it.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

KINDS = ("prepped", "leftovers", "cooked")

# Week order from Sunday — the order the prep-day chips are drawn in, and
# the order prep_days is stored in, so two saves of the same days compare
# equal.
PREP_WEEKDAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
_WEEKDAYS_FROM_MONDAY = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# The derived_from constraint on a lunch this module wrote, so a later
# reader can tell "the household said leftovers" from the fold's own
# batch_leftovers.
CONSTRAINT = "weekday_lunches"


def _weekday(iso: str) -> str:
    return _WEEKDAYS_FROM_MONDAY[date.fromisoformat(iso).weekday()]


def _title(weekday: str) -> str:
    return weekday[:1].upper() + weekday[1:]


def is_weekday(iso: str) -> bool:
    return date.fromisoformat(iso).weekday() < 5


def prep_day_for(lunch_date: str, prep_days: list[str]) -> tuple[str, int] | None:
    """
    The prep day a prepped lunch comes from: the most recent of
    `prep_days` on or before the lunch (the same day counts — a Wednesday
    prep can be Wednesday's lunch), and how many days back it is (0-6).
    None when there are no prep days. plan-week.html's prepDayFor mirrors
    this exactly; tests/test_weekday_lunches.py pins the two together.
    """
    lunch = date.fromisoformat(lunch_date)
    best = None
    for name in prep_days or []:
        if name not in PREP_WEEKDAYS:
            continue
        back = (lunch.weekday() - _WEEKDAYS_FROM_MONDAY.index(name)) % 7
        if best is None or back < best[1]:
            best = (name, back)
    return best


def prep_date_for(lunch_date: str, prep_days: list[str]) -> str | None:
    found = prep_day_for(lunch_date, prep_days)
    if found is None:
        return None
    return (date.fromisoformat(lunch_date) - timedelta(days=found[1])).isoformat()


def normalize(answer, period: list[str], skipped: list[str] | None = None, strict: bool = True) -> dict:
    """
    The stored shape from what the screen sent — {"days": [{"date",
    "kind"}], "prep_days": [...]} is enough; everything else is worked out
    here, so there is one place that decides what a prep day or a
    leftovers lunch means. `{}` (or None) stays `{}`: not answered.

    strict (a new answer from the screen): anything the screen would never
    send raises ValueError — a day outside the period, a weekend, an
    unknown kind, a day twice, a leftovers lunch with no dinner the
    evening before in the period, a prepped lunch with no prep day.
    Not strict (an answer carried into a new revision, when the period or
    its skipped days have moved under it): the day that no longer fits is
    dropped instead. Either way a day left out of the plan (skipped) is
    dropped quietly, the same as the packed-lunch days are.
    """
    if not answer:
        return {}

    def refuse(message: str):
        if strict:
            raise ValueError(message)

    if not isinstance(answer, dict):
        refuse("weekday_lunches must be an object.")
        return {}
    raw_days = answer.get("days") or []
    if not isinstance(raw_days, list):
        refuse("weekday_lunches.days must be a list.")
        return {}
    in_period = set(period)
    skipped_set = set(skipped or [])

    raw_prep = answer.get("prep_days") or []
    if not isinstance(raw_prep, list):
        refuse("weekday_lunches.prep_days must be a list of weekdays.")
        raw_prep = []
    prep = set()
    for name in raw_prep:
        key = str(name or "").strip().lower()
        if key not in PREP_WEEKDAYS:
            refuse(f"{name!r} isn't a day of the week.")
            continue
        prep.add(key)
    prep_days = [d for d in PREP_WEEKDAYS if d in prep]

    days: dict[str, dict] = {}
    for item in raw_days:
        if not isinstance(item, dict):
            refuse("Each weekday lunch must be an object with a date and a kind.")
            continue
        iso = str(item.get("date") or "")
        try:
            date.fromisoformat(iso)
        except ValueError:
            refuse(f"{iso!r} isn't a date.")
            continue
        if iso not in in_period:
            refuse(f"{iso} isn't in the period being planned.")
            continue
        if not is_weekday(iso):
            refuse(f"{iso} is a weekend — weekend lunches aren't on this screen.")
            continue
        if iso in skipped_set:
            continue
        kind = item.get("kind")
        if kind not in KINDS:
            refuse(f"A weekday lunch is one of {', '.join(KINDS)}, not {kind!r}.")
            continue
        if iso in days:
            refuse(f"{iso} was answered twice.")
            continue
        entry = {"date": iso, "weekday": _weekday(iso), "kind": kind}
        if kind == "leftovers":
            evening = (date.fromisoformat(iso) - timedelta(days=1)).isoformat()
            if evening not in in_period or evening in skipped_set:
                refuse(f"{_title(_weekday(iso))}’s lunch can’t be leftovers — the dinner before it isn’t planned.")
                continue
            entry["from_dinner"] = evening
        days[iso] = entry

    ordered = [days[d] for d in sorted(days)]
    if any(d["kind"] == "prepped" for d in ordered):
        if not prep_days:
            refuse("Prepped lunches need a prep day.")
            ordered = [d for d in ordered if d["kind"] != "prepped"]
        for d in ordered:
            if d["kind"] == "prepped":
                d["prep_day"] = prep_day_for(d["date"], prep_days)[0]
    if not any(d["kind"] == "prepped" for d in ordered):
        # No prepped lunch, no prep day: the chips aren't on screen then,
        # and a day kept here would be carried into next week unseen.
        prep_days = []
    if not ordered:
        return {}
    return {
        "counts": {k: sum(1 for d in ordered if d["kind"] == k) for k in KINDS},
        "prep_days": prep_days,
        "days": ordered,
    }


def load(raw: str | None) -> dict:
    """The stored JSON, read leniently: anything unreadable is unanswered."""
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def kinds_by_date(intake: dict | None) -> dict[str, str]:
    """{date: kind} for every answered weekday lunch — what the time caps
    read. Accepts the stored intake or the generation context (both carry
    `weekday_lunches`)."""
    answer = (intake or {}).get("weekday_lunches") or {}
    return {
        d["date"]: d["kind"]
        for d in answer.get("days") or []
        if isinstance(d, dict) and d.get("kind") in KINDS and d.get("date")
    }


def carryover(answer: dict | None, packed_lunch_days: list[str] | None = None) -> dict | None:
    """
    Last week's answer the way next week can use it: by WEEKDAY, since no
    date repeats. {"counts", "prep_days", "kinds": {"monday": "prepped",
    ...}, "on_the_go": ["monday", ...]} — or None when the question was
    never answered. The intake screen lays this week's dates out from it
    (plan-week.html's lunchPrefill), and the "Same as last week?" page
    reads the same thing.
    """
    answer = answer or {}
    if not answer.get("days"):
        return None
    kinds: dict[str, str] = {}
    for d in answer["days"]:
        # A two-week period has two Mondays; the first one speaks for it.
        kinds.setdefault(d.get("weekday") or _weekday(d["date"]), d["kind"])
    on_the_go = []
    for iso in packed_lunch_days or []:
        try:
            name = _weekday(iso)
        except (TypeError, ValueError):
            continue
        if is_weekday(iso) and name not in on_the_go:
            on_the_go.append(name)
    return {
        "counts": dict(answer.get("counts") or {}),
        "prep_days": list(answer.get("prep_days") or []),
        "kinds": kinds,
        "on_the_go": [d for d in _WEEKDAYS_FROM_MONDAY if d in on_the_go],
    }


# ---------- making the plan follow the answer ----------

def _lunch_and_dinner_rows(plan_id: int) -> tuple[dict, dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.food_groups_json,
               mpe.derived_from_json, mpe.recipe_id, mpe.freeform_meal,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.slot IN ('lunch', 'dinner')
          AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (plan_id, household_id()),
    ).fetchall()
    conn.close()
    lunches: dict[str, list[dict]] = {}
    dinners: dict[str, list[dict]] = {}
    for r in rows:
        row = dict(r)
        row["derived"] = json.loads(row["derived_from_json"] or "{}") or {}
        (lunches if row["slot"] == "lunch" else dinners).setdefault(row["date"], []).append(row)
    return lunches, dinners


def _cookable(row: dict | None) -> bool:
    return bool(row) and row["slot_state"] == "planned" and bool(row["meal"])


def _ids(rows: list[dict]) -> list[int]:
    return [r["id"] for r in rows]


def _food_groups(row: dict) -> list[str] | None:
    try:
        groups = json.loads(row.get("food_groups_json") or "[]")
    except (TypeError, ValueError):
        return None
    return groups or None


def _links_to_id(row: dict, cook_id: int) -> bool:
    return (row["derived"].get("links_to") or "") == f"entry_id:{cook_id}"


def apply_to_plan(plan_id: int, intake: dict | None) -> dict:
    """
    Make a freshly drafted week's weekday lunches what the household said
    — after the model has answered, the same way every other rule in
    agent._finish_week_slots is made true rather than merely asked for.
    Runs after repair_leftover_chains, so the chains it reads are real.

    - leftovers: the lunch becomes the previous evening's dinner, reheated
      (or, when that dinner is itself a reheat, the cook it reheats, if
      that is within three days). A dinner that isn't there — nobody home,
      out, not planned — leaves the lunch as it was drafted.
    - prepped: the prepped lunches sharing a prep date are one dish, cooked
      on the first of them and eaten on the rest; one more than three days
      after the prep day eats a portion frozen on that cook.
    - cooked: nothing is written — the lunch's 20-minute cap is what makes
      it true (time_caps.minutes_cap, read by the fold, the swap and the
      quality check).

    Never raises: a week with a lunch left as drafted is better than a
    lost week. Returns what it changed, for the log.
    """
    from . import leftovers as _leftovers
    from . import meal_variety as _meal_variety
    from . import weekly_plan as _weekly_plan

    out = {"leftovers": [], "prepped": [], "frozen": [], "skipped": []}
    answer = (intake or {}).get("weekday_lunches") or {}
    days = [d for d in answer.get("days") or [] if isinstance(d, dict)]
    if not days:
        return out
    prep_days = answer.get("prep_days") or []
    try:
        lunches, dinners = _lunch_and_dinner_rows(plan_id)
        chains = _leftovers.plan_leftover_chains(plan_id)
        targets: dict[int, list[str]] = {}
        frozen: list[tuple[int, str, str]] = []

        # Leftovers from the evening before.
        for d in days:
            if d["kind"] != "leftovers":
                continue
            rows = [r for r in lunches.get(d["date"], []) if r["slot_state"] == "planned"]
            evening = (date.fromisoformat(d["date"]) - timedelta(days=1)).isoformat()
            dinner = next((r for r in dinners.get(evening, []) if _cookable(r)), None)
            if not rows or dinner is None:
                out["skipped"].append({"date": d["date"], "why": "no dinner the evening before"})
                continue
            cook = dinner
            fed = chains["leftovers"].get(dinner["id"])
            if fed:
                src = fed["source"]
                if _leftovers.days_apart(src["date"], d["date"]) > _leftovers.MAX_LEFTOVER_DAYS:
                    out["skipped"].append({"date": d["date"], "why": "that dinner is itself leftovers"})
                    continue
                cook = next((r for r in dinners.get(src["date"], []) + lunches.get(src["date"], [])
                             if r["id"] == src["entry_id"]), None)
                if cook is None:
                    out["skipped"].append({"date": d["date"], "why": "that dinner is itself leftovers"})
                    continue
            if any(r["id"] in chains["sources"] for r in rows):
                # This lunch already feeds something else; turning it into a
                # reheat would strand what it feeds.
                out["skipped"].append({"date": d["date"], "why": "the lunch already feeds another meal"})
                continue
            if len(rows) == 1 and _links_to_id(rows[0], cook["id"]):
                continue
            if len(rows) == 1 and rows[0]["meal"].strip().lower() == cook["meal"].strip().lower():
                derived = dict(rows[0]["derived"])
                derived["links_to"] = f"entry_id:{cook['id']}"
                derived["constraint"] = CONSTRAINT
                _save_derived(rows[0]["id"], derived)
            else:
                _weekly_plan._replace_slot_entries(
                    plan_id, _ids(rows), d["date"], "lunch", cook["meal"],
                    food_groups=_food_groups(cook), reasoning="",
                    derived_from={"links_to": f"entry_id:{cook['id']}", "constraint": CONSTRAINT,
                                  "replaced": rows[0]["meal"]},
                )
            targets.setdefault(cook["id"], []).append(f"{d['date']}:lunch")
            out["leftovers"].append({"date": d["date"], "from": cook["date"], "dish": cook["meal"]})

        # Prepped: one dish per prep date, cooked on its first lunch.
        batches: dict[str, list[dict]] = {}
        for d in days:
            if d["kind"] != "prepped":
                continue
            prep_date = prep_date_for(d["date"], prep_days)
            if prep_date is None:
                continue
            batches.setdefault(prep_date, []).append(d)
        # Re-read: the leftovers writes above replaced rows.
        lunches, dinners = _lunch_and_dinner_rows(plan_id)
        chains = _leftovers.plan_leftover_chains(plan_id)
        for prep_date, members in sorted(batches.items()):
            members.sort(key=lambda d: d["date"])
            rows_by_date = {
                d["date"]: [r for r in lunches.get(d["date"], []) if r["slot_state"] == "planned"]
                for d in members
            }
            # The batch's dish: the first of its lunches the model drafted
            # as a real cook (not a reheat of something else).
            model_cook = next(
                (rows[0] for d in members for rows in [rows_by_date[d["date"]]]
                 if len(rows) == 1 and _cookable(rows[0]) and rows[0]["id"] not in chains["leftovers"]),
                None,
            )
            first = members[0]
            first_rows = rows_by_date[first["date"]]
            if model_cook is None or not first_rows:
                out["skipped"].append({"date": first["date"], "why": "no prepped lunch to build the batch from"})
                continue
            dish, groups = model_cook["meal"], _food_groups(model_cook)
            note = (f"Cook this {_title(_weekday(prep_date))} for "
                    f"{batch_lunch_phrase([d['date'] for d in members])}.")
            cook_derived = {"constraint": CONSTRAINT, "prep_day": _weekday(prep_date), "prep_date": prep_date}
            if (len(first_rows) == 1 and first_rows[0]["meal"].strip().lower() == dish.strip().lower()
                    and first_rows[0]["id"] not in chains["leftovers"]):
                cook_id = first_rows[0]["id"]
                derived = dict(first_rows[0]["derived"])
                derived.update(cook_derived)
                _save_derived(cook_id, derived, reasoning=note)
            else:
                if any(r["id"] in chains["sources"] for r in first_rows):
                    out["skipped"].append({"date": first["date"], "why": "the lunch already feeds another meal"})
                    continue
                row = _weekly_plan._replace_slot_entries(
                    plan_id, _ids(first_rows), first["date"], "lunch", dish,
                    food_groups=groups, reasoning=note,
                    derived_from=dict(cook_derived, replaced=first_rows[0]["meal"]),
                )
                cook_id = row.get("entry_id")
                if not cook_id:
                    continue
            for d in members[1:]:
                rows = rows_by_date[d["date"]]
                if not rows:
                    continue
                if any(r["id"] in chains["sources"] for r in rows):
                    out["skipped"].append({"date": d["date"], "why": "the lunch already feeds another meal"})
                    continue
                if _leftovers.days_apart(prep_date, d["date"]) > _leftovers.MAX_LEFTOVER_DAYS:
                    # Past three days from the prep day: a portion frozen on
                    # the cook (the fold's freezer night, same row).
                    _weekly_plan._replace_slot_entries(
                        plan_id, _ids(rows), d["date"], "lunch",
                        _leftovers.freezer_night_name(dish, first["date"]), reasoning="",
                        derived_from={
                            _leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{cook_id}", "dish": dish},
                            "constraint": CONSTRAINT, "replaced": rows[0]["meal"],
                        },
                    )
                    frozen.append((cook_id, d["date"], "lunch"))
                    out["frozen"].append({"date": d["date"], "dish": dish})
                    continue
                link = {"links_to": f"entry_id:{cook_id}", "cook_ahead": True, "constraint": CONSTRAINT}
                if len(rows) == 1 and rows[0]["meal"].strip().lower() == dish.strip().lower():
                    derived = dict(rows[0]["derived"])
                    derived.update(link)
                    _save_derived(rows[0]["id"], derived)
                else:
                    _weekly_plan._replace_slot_entries(
                        plan_id, _ids(rows), d["date"], "lunch", dish,
                        food_groups=groups, reasoning="",
                        derived_from=dict(link, replaced=rows[0]["meal"]),
                    )
                targets.setdefault(cook_id, []).append(f"{d['date']}:lunch")
            out["prepped"].append({"prep_date": prep_date, "dish": dish, "lunches": [d["date"] for d in members]})

        if targets or frozen:
            written = {"batched": []}
            _meal_variety._write_cook_sides(plan_id, targets, frozen, written)
    except Exception:
        logger.exception("Weekday lunches not applied to plan %s; the lunches stand as drafted", plan_id)
    if out["leftovers"] or out["prepped"] or out["frozen"]:
        logger.info("Plan %s weekday lunches: %s", plan_id, out)
    return out


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def batch_lunch_phrase(dates: list[str]) -> str:
    """
    "Monday and Tuesday’s lunches" — the days one prepped batch feeds.

    One function because two sentences say it: the cook entry's own
    reasoning ("Cook this Sunday for Monday and Tuesday’s lunches.",
    written by apply_to_plan) and the prep session's line under the batch
    ("For Monday and Tuesday’s lunches.", read back by
    prep_sessions._prepped_lunch_items). Two copies of one phrase is how
    the two screens end up naming different days.
    """
    names = [_title(_weekday(d)) for d in sorted(dates)]
    return f"{_join(names)}’s lunch{'es' if len(names) > 1 else ''}"


def _save_derived(entry_id: int, derived: dict, reasoning: str | None = None) -> None:
    conn = get_conn()
    if reasoning is None:
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
            (json.dumps(derived), entry_id, household_id()),
        )
    else:
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ?, reasoning = ? WHERE id = ? AND household_id = ?",
            (json.dumps(derived), reasoning, entry_id, household_id()),
        )
    conn.commit()
    conn.close()


# ---------- reading a prepped batch back: whose day the cook belongs to ----------

def prepped_batches(plan_id: int, window: tuple[str, str] | None = None) -> list[dict]:
    """
    Every prepped-lunch batch the household's answer put on the plan, read
    back off the cook entries apply_to_plan stamped (derived_from.prep_date
    / .prep_day). Nothing new is written and nothing is inferred: this is
    the one place that says what a prepped batch IS, so the prep session
    and the Cook card cannot disagree about whose day the cook belongs to.

    Each batch:
        {"cook_entry_id", "cook_date", "slot", "meal", "cooked_status",
         "prep_date", "prep_day", "lunch_dates", "weekly_plan_id"}

    `lunch_dates` is every day the batch feeds, read off the plan AS IT
    STANDS rather than off the answer: the cook's own day, the chain
    targets that link back to it, and any day eating a portion frozen on
    it. A day swapped away since takes itself out of the list, which is the
    point of reading the rows rather than the intake.

    `window` widens the read to OTHER LIVE PLANS whose batch preps inside
    (first, last) — because a Sunday prep day for a Monday-start week falls
    the day BEFORE that week, so the Cook tab standing on that Sunday is
    showing the plan that CONTAINS the Sunday while the batch belongs to
    the plan being prepped for. cooker.get_prep_schedule already reads
    across plans for the same reason (a Monday holiday's make-ahead work
    lands in the week before) and with the same two guards: only a live
    plan counts, and a row whose entry is gone is not read at all.

    Batches with no prep_date stamp — which is every batch on every week
    with no weekday-lunches answer — produce nothing, so every reader of
    this is inert for them.
    """
    conn = get_conn()
    if window:
        rows = conn.execute(
            """
            SELECT mpe.id, mpe.weekly_plan_id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status,
                   mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.household_id = ? AND mpe.component_category IS NULL
              AND (mpe.weekly_plan_id = ?
                   OR mpe.weekly_plan_id IN (SELECT id FROM weekly_plans
                                             WHERE household_id = ? AND status IN ('draft', 'approved')))
            ORDER BY mpe.date ASC, mpe.id ASC
            """,
            (household_id(), plan_id, household_id()),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT mpe.id, mpe.weekly_plan_id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status,
                   mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ? AND mpe.component_category IS NULL
            ORDER BY mpe.date ASC, mpe.id ASC
            """,
            (household_id(), plan_id),
        ).fetchall()
    conn.close()

    entries = []
    for r in rows:
        row = dict(r)
        try:
            row["derived"] = json.loads(row["derived_from_json"] or "{}") or {}
        except (TypeError, ValueError):
            row["derived"] = {}
        if not isinstance(row["derived"], dict):
            row["derived"] = {}
        entries.append(row)

    # Which days eat off which cook, by the cook's entry id — the two links
    # apply_to_plan writes for a batch's later days.
    from . import leftovers as _leftovers
    fed: dict[int, set[str]] = {}
    for row in entries:
        derived = row["derived"]
        ref = str(derived.get("links_to") or "").strip()
        frozen = derived.get(_leftovers.FROM_FREEZER_KEY) or {}
        if not ref and isinstance(frozen, dict):
            ref = str(frozen.get("cook") or "").strip()
        if not ref.startswith("entry_id:"):
            continue
        try:
            cook_id = int(ref.split(":", 1)[1])
        except (TypeError, ValueError):
            continue
        fed.setdefault(cook_id, set()).add(row["date"])

    out: list[dict] = []
    for row in entries:
        prep_date = str(row["derived"].get("prep_date") or "").strip()
        if not prep_date or row["slot_state"] != "planned" or not row["meal"]:
            continue
        try:
            date.fromisoformat(prep_date)
        except ValueError:
            continue
        if row["weekly_plan_id"] != plan_id:
            # Another plan's batch only counts while it preps INTO this
            # plan's period — see `window` above.
            if not window or not (window[0] <= prep_date <= window[1]):
                continue
        out.append({
            "weekly_plan_id": row["weekly_plan_id"],
            "cook_entry_id": row["id"],
            "cook_date": row["date"],
            "slot": row["slot"],
            "meal": row["meal"],
            "cooked_status": row["cooked_status"],
            "prep_date": prep_date,
            "prep_day": str(row["derived"].get("prep_day") or _weekday(prep_date)),
            "lunch_dates": sorted({row["date"]} | fed.get(row["id"], set())),
        })
    out.sort(key=lambda b: (b["prep_date"], b["cook_date"], b["cook_entry_id"]))
    return out


def prepped_batch_by_cook(plan_id: int) -> dict[int, dict]:
    """prepped_batches keyed by the cook's entry id — what a card reader
    wants (cooker._apply_prepped_lunches)."""
    return {b["cook_entry_id"]: b for b in prepped_batches(plan_id)}
