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

Where a prepped batch is cooked (Emily, 2026-09-27: "Sunday's prep feeds
Monday"). When the prep day is inside the plan and earlier than the
batch's lunches, the prep day's own lunch — else its dinner — is the cook,
when it is a real one, and every lunch of the batch reheats it
(_prep_day_cook). Otherwise — a Sunday prep for a Monday-start week, or a
prep day with nothing cooked on it — the batch is cooked on its FIRST
lunch's entry, since a chain can't hold a cook on a day the dish isn't
eaten (cook_ahead.apply_prep_day_batches says the same); that cook entry
carries the prep day on its derived_from and its reasoning says when to
cook it. Either way the sentence is kept on derived_from.prep_note too, so
the schedule fact survives whatever the row shows.

The lunch COUNT on a week with this answer is enforce_lunch_count: the
answered lunches and the prep-day cooks are the household's own and stay;
a lunch that is a dinner's leftovers is not a lunch dish; the rest (a
weekend lunch, an unanswered one) fold into the kept dishes.
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
    `prep_days` STRICTLY before the lunch, and how many days back it is
    (1-7). Food made on a prep day is first eaten the NEXT day, never the
    same day — prep happens in the evening (Emily, 2026-09-28: "if I'm
    doing my meal prep after work, it won't be done in time for
    tuesday"), so a Tuesday prep feeds Wednesday on, and Tuesday's lunch
    comes from the prep day before it. A lone prep day that is also the
    lunch's own weekday is last week's (7 back — past the three-day reach,
    so plan-week's ensurePrepReach brings in the evening before).
    None when there are no prep days. plan-week.html's prepDayFor mirrors
    this exactly; tests/test_weekday_lunches.py pins the two together.
    """
    lunch = date.fromisoformat(lunch_date)
    best = None
    for name in prep_days or []:
        if name not in PREP_WEEKDAYS:
            continue
        back = (lunch.weekday() - _WEEKDAYS_FROM_MONDAY.index(name) - 1) % 7 + 1
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
    # How many different dishes ONE prep session cooks (the usual week's
    # "Meal prep ahead" = 2 with a single prep day, 2026-09-30). 1 — the
    # screen's own answer — is one dish per prep day, as before, and adds
    # nothing to the stored shape. Above 1, each prepped lunch carries a
    # `batch` (0, 1, …) dealt in turn by date within its prep day, so the
    # week alternates between the session's dishes: Mon A, Tue B, Wed A…
    try:
        per_session = max(1, min(3, int(answer.get("dishes_per_prep_day") or 1)))
    except (TypeError, ValueError):
        refuse("dishes_per_prep_day must be a number.")
        per_session = 1
    if any(d["kind"] == "prepped" for d in ordered):
        if not prep_days:
            refuse("Prepped lunches need a prep day.")
            ordered = [d for d in ordered if d["kind"] != "prepped"]
        dealt: dict[str, int] = {}
        for d in ordered:
            if d["kind"] == "prepped":
                d["prep_day"] = prep_day_for(d["date"], prep_days)[0]
                if per_session > 1:
                    d["batch"] = dealt.get(d["prep_day"], 0) % per_session
                    dealt[d["prep_day"]] = dealt.get(d["prep_day"], 0) + 1
    if not any(d["kind"] == "prepped" for d in ordered):
        # No prepped lunch, no prep day: the chips aren't on screen then,
        # and a day kept here would be carried into next week unseen.
        prep_days = []
    if not ordered:
        return {}
    out = {
        "counts": {k: sum(1 for d in ordered if d["kind"] == k) for k in KINDS},
        "prep_days": prep_days,
        "days": ordered,
    }
    if per_session > 1 and any(d.get("batch") for d in ordered):
        out["dishes_per_prep_day"] = per_session
    return out


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


def _prep_day_cook(prep_date: str, first_lunch: str, lunches: dict, dinners: dict, chains: dict) -> dict | None:
    """
    The meal ON the prep day that can be a prepped batch's cook: the
    day's lunch, else its dinner — one row, planned, a real dish, not
    itself a reheat or a portion from the freezer. Only when the prep day
    is inside this plan and strictly before the batch's first lunch (a
    reheat has to come after its cook; a prep day that IS the first
    lunch's day is the ordinary cook-on-the-first-lunch case). None
    otherwise, and the caller cooks on the first lunch as before.
    """
    if prep_date >= first_lunch:
        return None
    for rows in (lunches.get(prep_date) or [], dinners.get(prep_date) or []):
        planned = [r for r in rows if r["slot_state"] == "planned"]
        if len(planned) != 1:
            continue
        row = planned[0]
        if not _cookable(row) or row["id"] in chains["leftovers"]:
            continue
        if row["derived"].get("links_to"):
            continue
        # Already an earlier batch's cook — either a real prep-day cook
        # (Sun + Mon prep on a Monday-start week: Sunday's batch is cooked
        # on Monday's lunch, and Monday's own prep is that evening) or a
        # "cooked that day" fallback for a batch whose true prep day was
        # stale/out of plan (Mon + Wed prep where Monday's own prep day —
        # last Wednesday — is unreachable, so Monday is cooked fresh).
        # Both write `prep_note` (build review, 2026-09-28: checking only
        # `prep_date` missed the stale-fallback case, so a later batch
        # stole Monday's fresh cook and overwrote its note, losing any
        # mention of who else it already fed). Taking either kind over
        # would orphan or misdescribe the earlier batch.
        if row["derived"].get("constraint") == CONSTRAINT and "prep_note" in row["derived"]:
            continue
        from . import leftovers as _leftovers
        if row["derived"].get(_leftovers.FROM_FREEZER_KEY):
            continue
        return row
    return None


def _request_words(derived: dict, intake: dict | None) -> str | None:
    """
    The household's own words a dish was placed for, or None — a dish
    this pass must not silently overwrite (integration review, 2026-09-27:
    routing Sunday's prep to Monday replaced the model's Corn pancakes, the
    week's only corn, and the corn request then had nowhere to land). Their
    typed words or a meal they chose (meal_variety.theirs); a dish chosen
    to use an ingredient they typed ("I have corn"); a dish chosen for a
    cuisine chip they picked this week.
    """
    from . import meal_variety as _meal_variety
    from . import typed_requests as _typed_requests
    from . import week_intake as _week_intake

    if _meal_variety.theirs(derived):
        return str(derived.get("freeform") or "").strip() or "theirs"
    stock = " ".join(str(x) for x in (derived.get("inventory") or []) + (derived.get("must_use") or []))
    if stock:
        for req in _week_intake.freeform_ingredient_requests((intake or {}).get("freeform") or ""):
            if _typed_requests.dish_carries(stock, req["ingredient"]):
                return req["words"]
    chips = {str(c).strip().lower() for c in (intake or {}).get("cuisines") or []}
    for item in derived.get("inputs") or []:
        item = str(item)
        if item.lower().startswith("cuisines:") and item.split(":", 1)[1].strip().lower() in chips:
            return item
    return None


def _keep_request_dish(plan_id: int, row: dict, intake: dict | None, dinners: dict, chains: dict) -> str | None:
    """
    Before a lunch the household asked for something on is overwritten:
    move its dish to the first dinner, that day or after, that is an
    ordinary cook (one planned row, nobody's request, not either end of a
    chain, not a night tagged Leftovers), carrying their words so every
    later pass keeps it. Returns "moved", "kept" (nowhere to move it — the
    caller leaves the lunch alone) or None (not a request dish).
    """
    from . import weekly_plan as _weekly_plan

    from . import leftovers as _leftovers

    words = _request_words(row["derived"], intake)
    if words is None:
        return None
    # Read fresh: an earlier move in this same pass may have rewritten a
    # dinner the caller's rows still hold.
    _lunches, dinners = _lunch_and_dinner_rows(plan_id)
    chains = _leftovers.plan_leftover_chains(plan_id)
    eligible = []
    for day in sorted(d for d in dinners if d >= row["date"]):
        planned = [r for r in dinners[day] if r["slot_state"] == "planned"]
        if len(planned) != 1 or len(dinners[day]) != 1:
            continue
        night = planned[0]
        if not _cookable(night) or night["id"] in chains["sources"] or night["id"] in chains["leftovers"]:
            continue
        if _spoken_for(night, intake):
            continue
        eligible.append(night)
    if not eligible:
        return "kept"
    # The first night the dish FITS (its time against that night's cap —
    # a 60-minute chowder is not moved onto a 30-minute rush night), else
    # the first ordinary night (integration review, 2026-09-27).
    minutes = _entry_minutes(row["id"])
    night = next((n for n in eligible if _fits_night(minutes, n["date"], intake)), eligible[0])
    day = night["date"]
    derived = dict(row["derived"])
    derived.pop("links_to", None)
    if not str(derived.get("freeform") or "").strip() and words != "theirs" and not words.startswith("cuisines:"):
        derived["freeform"] = words
    derived["moved_from"] = f"{row['date']}:lunch"
    _weekly_plan._replace_slot_entries(
        plan_id, [night["id"]], day, "dinner", row["meal"], food_groups=_food_groups(row),
        reasoning="", derived_from=dict(derived, replaced=night["meal"]),
    )
    logger.info("Plan %s: %s lunch %r moved to %s dinner so the batch doesn't overwrite it",
                plan_id, row["date"], row["meal"], day)
    return "moved"


def repoint_leftover_lunches(plan_id: int, intake: dict | None) -> dict:
    """
    After the DINNER count has folded the week (integration review,
    2026-09-27: a dinner feeding a "leftovers from dinner" lunch used to be
    kept whatever the count said, and the probe with Dinners = 2 kept four):
    every lunch the household said is last night's leftovers is pointed at
    a cook again — the evening before's dinner, or the cook it reheats
    within three days, else another cook in reach, all under the
    two-meals-in-a-row rule (apply_to_plan's own leftovers branch, run on
    those days only).

    A lunch that branch can't point anywhere is never left a fresh cook of
    a dish the fold took off the week (final review, 2026-09-27): it
    reheats any dinner still on the week one to three days back that the
    run rule allows, else eats a portion frozen on an earlier one. Only
    when neither can be done is it said, in one plain line naming the dish
    and the true reason (`said`, see _unpointed_line).
    """
    from . import leftovers as _leftovers

    answer = (intake or {}).get("weekday_lunches") or {}
    days = [d for d in answer.get("days") or [] if isinstance(d, dict) and d.get("kind") == "leftovers"]
    out: dict = {"said": []}
    if not days:
        return out
    only = dict(intake or {}, weekday_lunches=dict(answer, days=days, prep_days=[]))
    out = apply_to_plan(plan_id, only)
    skipped = {s["date"]: s for s in out.get("skipped") or []}
    try:
        for d in days:
            chains = _leftovers.plan_leftover_chains(plan_id)
            lunches, dinners = _lunch_and_dinner_rows(plan_id)
            rows = [r for r in lunches.get(d["date"], []) if r["slot_state"] == "planned"]
            if len(rows) != 1 or any(r["id"] in chains["leftovers"] for r in rows):
                continue
            row = rows[0]
            if _request_words(row["derived"], intake) or _leftovers.frozen_portion_on(row["derived"]):
                continue  # kept for their other request (kept_line said so), or already a portion
            if _lunch_from_a_kept_dinner(plan_id, row, dinners, chains):
                continue
            out["said"].append(_unpointed_line(row, skipped.get(d["date"]) or {}, dinners))
    except Exception:
        logger.exception("Checking the leftovers lunches of plan %s failed", plan_id)
    return out


def dinner_count_line(plan_id: int, target: int | None) -> str:
    """
    One plain line when the week still cooks more dinner dishes than the
    household's number (final review, 2026-09-27: Dinners = 1 with every
    weekday lunch last night's leftovers — one pot can't be every dinner
    and every lunch without a third meal of it in a row): "Two dinner
    dishes this week, not one." No reason is given: more than one pass can
    leave a dish over the number, and a line never says a false one.
    Nothing when the number is met or wasn't set.
    """
    from . import draft_opener as _draft_opener
    from . import leftovers as _leftovers

    if not target:
        return ""
    chains = _leftovers.plan_leftover_chains(plan_id)
    _lunches, dinners = _lunch_and_dinner_rows(plan_id)
    cooked = {_leftovers.dish_identity(r["meal"]) for rows in dinners.values() for r in rows
              if _cookable(r) and r["id"] not in chains["leftovers"] and not r["derived"].get("links_to")
              and not _leftovers.frozen_portion_on(r["derived"])}
    if len(cooked) <= int(target):
        return ""
    n, want = _draft_opener.number_word(len(cooked)), _draft_opener.number_word(int(target))
    return f"{n[:1].upper() + n[1:]} dinner dishes this week, not {want}."


def _lunch_from_a_kept_dinner(plan_id: int, row: dict, dinners: dict, chains: dict) -> bool:
    """
    The fallback for a "leftovers from dinner" lunch with no evening-before
    cook to eat: the nearest dinner still COOKED on the week one to three
    days back (fridge leftovers), else the nearest further back (a portion
    frozen on it), either only when the two-meals-in-a-row rule allows it.
    Writes the chain the fold's own way and says True, or False.
    """
    from . import leftovers as _leftovers
    from . import meal_variety as _meal_variety
    from . import weekly_plan as _weekly_plan

    keys = _leftovers.run_keys(plan_id)
    cooks = [r for rows in dinners.values() for r in rows
             if r["date"] < row["date"] and _cookable(r) and r["id"] not in chains["leftovers"]
             and not r["derived"].get("links_to") and not _leftovers.frozen_portion_on(r["derived"])
             and not _leftovers.too_many_in_a_row(keys, row["date"], "lunch", r["meal"])]
    cooks.sort(key=lambda r: r["date"], reverse=True)
    near = [c for c in cooks if _leftovers.days_apart(c["date"], row["date"]) <= _leftovers.MAX_LEFTOVER_DAYS]
    if near:
        cook = near[0]
        _weekly_plan._replace_slot_entries(
            plan_id, [row["id"]], row["date"], "lunch", cook["meal"], food_groups=_food_groups(cook),
            reasoning="", derived_from={"links_to": f"entry_id:{cook['id']}", "constraint": CONSTRAINT,
                                        "replaced": row["meal"]},
        )
        _meal_variety._write_cook_sides(plan_id, {cook["id"]: [f"{row['date']}:lunch"]}, [], {"batched": []})
        return True
    if cooks:
        cook = cooks[0]
        _weekly_plan._replace_slot_entries(
            plan_id, [row["id"]], row["date"], "lunch", _leftovers.freezer_night_name(cook["meal"], cook["date"]),
            reasoning="", derived_from={
                _leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{cook['id']}", "dish": cook["meal"]},
                "constraint": CONSTRAINT, "replaced": row["meal"]},
        )
        _meal_variety._write_cook_sides(plan_id, {}, [(cook["id"], row["date"], "lunch")], {"batched": []})
        return True
    return False


def _unpointed_line(row: dict, skipped: dict, dinners: dict) -> str:
    """
    The one line for a "leftovers from dinner" lunch nothing could feed,
    naming the dish and the TRUE reason (final review, 2026-09-27):
      * the two-in-a-row rule refused the evening's pot — "Friday's lunch
        is Ratatouille, so Tuesday's Miso cod isn't three meals running.";
      * nobody is home the evening before — "…— nobody's home Thursday
        night to cook extra.";
      * otherwise — "…— nothing from the days before is left to reheat."
    """
    day = _title(_weekday(row["date"]))
    dish = row["meal"]
    run = skipped.get("run") or {}
    if run.get("dish"):
        return (f"{day}’s lunch is {dish}, so {_title(_weekday(run['cook_date']))}’s {run['dish']} "
                "isn’t three meals running.")
    evening = (date.fromisoformat(row["date"]) - timedelta(days=1)).isoformat()
    before = dinners.get(evening) or []
    if before and all(r["slot_state"] == "planned_empty" for r in before):
        constraint = str(before[0]["derived"].get("constraint") or "")
        if "nobody_home" in constraint or "away" in constraint or before[0]["derived"].get("need") == "away" \
                or "out" in (before[0]["derived"].get("tags") or []):
            return f"{day}’s lunch is {dish} — nobody’s home {_title(_weekday(evening))} night to cook extra."
    return f"{day}’s lunch is {dish} — nothing from the days before is left to reheat."


def request_phrase(derived: dict, intake: dict | None, dish: str = "") -> str:
    """
    Which of their requests a dish is there for, said plainly — "for your
    corn", "for your Burgers pick", or "as you asked" only when their own
    words named the dish (final review, 2026-09-27: never "as you asked"
    for a dish they didn't name).
    """
    from . import meal_variety as _meal_variety
    from . import typed_requests as _typed_requests
    from . import week_intake as _week_intake

    freeform = (intake or {}).get("freeform") or ""
    stock = " ".join(str(x) for x in (derived.get("inventory") or []) + (derived.get("must_use") or []))
    for req in _week_intake.freeform_ingredient_requests(freeform):
        if _typed_requests.dish_carries(f"{stock} {dish}", req["ingredient"]):
            return f"for your {req['ingredient']}"
    chips = {str(c).strip().lower(): str(c).strip() for c in (intake or {}).get("cuisines") or []}
    for item in derived.get("inputs") or []:
        item = str(item)
        if item.lower().startswith("cuisines:"):
            chip = chips.get(item.split(":", 1)[1].strip().lower())
            if chip:
                return f"for your {chip} pick"
    if dish and _meal_variety.asked_for_by_name(dish, (freeform,)):
        return "as you asked"
    return "as you asked" if _meal_variety.theirs(derived) else "for something you asked for"


def kept_line(lunch_date: str, dish: str, kind: str, prep_date: str | None = None,
              phrase: str = "as you asked") -> str:
    """
    The one plain line for a lunch the household said how to make that
    Pomona left as their other request instead (final review, 2026-09-27):
    "Monday's lunch stays Corn pancakes, for your corn — Sunday's prep
    doesn't cover it." / "…, for your Burgers pick — it isn't last night's
    leftovers." `phrase` is request_phrase's.
    """
    day = _title(_weekday(lunch_date))
    if kind == "prepped" and prep_date:
        why = f"{_title(_weekday(prep_date))}’s prep doesn’t cover it"
    else:
        why = "it isn’t last night’s leftovers"
    return f"{day}’s lunch stays {dish}, {phrase} — {why}."


def _spoken_for(night: dict, intake: dict | None) -> bool:
    """A dinner that is not an ordinary cook to give away: a request of its
    own, anything the household chose (meal_variety._THEIR_OWN_KEYS — a
    holiday dish they are bringing among them), a holiday or hosting night,
    a chain end, a prepped batch, a night tagged Leftovers, or a night whose
    declared need is ready-made, quick or away."""
    from . import meal_variety as _meal_variety
    from . import slot_needs as _slot_needs

    nd = night["derived"]
    if _request_words(nd, intake) or any(nd.get(k) for k in _meal_variety._THEIR_OWN_KEYS):
        return True
    if nd.get("holiday") or nd.get("holiday_menu") or str(nd.get("constraint") or "") in (
            "hosting", "holiday_answer_changed", "bring_a_dish"):
        return True
    if nd.get("links_to") or nd.get("prep_date") or "left" in (nd.get("tags") or []):
        return True
    try:
        need = (_slot_needs.get_slot_need(night["date"], "dinner") or {}).get("need")
    except Exception:
        need = None
    return need in ("ready_made", "quick", "away")


def _entry_minutes(entry_id: int) -> int | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT r.prep_time_minutes, r.cook_time_minutes FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.id = ? AND mpe.household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    conn.close()
    if not row:
        return None
    total = int(row["prep_time_minutes"] or 0) + int(row["cook_time_minutes"] or 0)
    return total or None


def _fits_night(minutes: int | None, meal_date: str, intake: dict | None) -> bool:
    """Whether a dish of `minutes` fits that dinner's own cap (time_caps) —
    unknown minutes or no cap fit."""
    from . import memory as _memory
    from . import time_caps as _time_caps

    if not minutes:
        return True
    try:
        tags = ((intake or {}).get("night_tags") or {}).get(meal_date) or []
        cap = _time_caps.minutes_cap(meal_date, "dinner", tags, _memory.get_household_memory() or {})
    except Exception:
        return True
    return cap is None or minutes <= cap


def _uncook_cooked_lunches(plan_id: int, days: list[dict], lunches: dict, chains: dict,
                           intake: dict | None, out: dict) -> bool:
    """
    Every lunch the household answered "Cooked that day" that the draft has
    as a reheat — a leftovers chain off an earlier cook, or a frozen
    portion — is rewritten as a cook of that same dish on its own day, so
    the chain's source stops being sized for it (_replace_slot_entries
    unlinks it). A lunch carrying their own words is left alone. Returns
    whether anything was written.
    """
    from . import leftovers as _leftovers
    from . import weekly_plan as _weekly_plan

    wrote = False
    for d in days:
        if d.get("kind") != "cooked":
            continue
        rows = [r for r in lunches.get(d["date"], []) if r["slot_state"] == "planned"]
        if len(rows) != 1:
            continue
        row = rows[0]
        derived = row["derived"]
        frozen_dish = _leftovers.frozen_portion_on(derived)
        reheat = row["id"] in chains["leftovers"] or (derived.get("links_to") or "").strip() or frozen_dish
        if not reheat or row["id"] in chains["sources"] or _request_words(derived, intake):
            continue
        dish = frozen_dish or row["meal"]
        if not dish:
            continue
        fresh = {k: v for k, v in derived.items()
                 if k not in ("links_to", "cook_ahead", "repointed", _leftovers.FROM_FREEZER_KEY)}
        fresh["constraint"] = CONSTRAINT
        cook_ref = str(((derived.get(_leftovers.FROM_FREEZER_KEY) or {}) if frozen_dish else {}).get("cook") or "")
        if cook_ref.startswith("entry_id:") and cook_ref[9:].isdigit():
            # The cook stops freezing a portion for this lunch.
            from . import leftovers_spread as _leftovers_spread
            conn = get_conn()
            try:
                _leftovers_spread._drop_freezer_portion(
                    conn, int(cook_ref[9:]), f"{d['date']}:lunch",
                    _leftovers.eaters_at(d["date"], "lunch", conn=conn))
                conn.commit()
            finally:
                conn.close()
        _weekly_plan._replace_slot_entries(
            plan_id, [row["id"]], d["date"], "lunch", dish,
            food_groups=_food_groups(row), reasoning="", derived_from=fresh,
        )
        out.setdefault("cooked", []).append({"date": d["date"], "dish": dish})
        wrote = True
    return wrote


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
    - cooked: the lunch's 20-minute cap is what makes it true
      (time_caps.minutes_cap, read by the fold, the swap and the quality
      check) — and a cooked lunch the draft left as a reheat is made its
      own cook of the same dish first (_uncook_cooked_lunches).

    Never raises: a week with a lunch left as drafted is better than a
    lost week. Returns what it changed, for the log.
    """
    from . import leftovers as _leftovers
    from . import meal_variety as _meal_variety
    from . import weekly_plan as _weekly_plan

    out = {"leftovers": [], "prepped": [], "frozen": [], "skipped": [], "said": []}
    answer = (intake or {}).get("weekday_lunches") or {}
    days = [d for d in answer.get("days") or [] if isinstance(d, dict)]
    if not days:
        return out
    prep_days = answer.get("prep_days") or []
    try:
        lunches, dinners = _lunch_and_dinner_rows(plan_id)
        chains = _leftovers.plan_leftover_chains(plan_id)
        # For the two-meals-in-a-row rule (leftovers.MAX_MEALS_IN_A_ROW).
        # The household's lunch wins over a dinner AFTER it — that dinner
        # is dinner_gaps.break_long_runs' to change — but a lunch that
        # would be the third of one dish counting back is left as drafted.
        keys = _leftovers.run_keys(plan_id)
        targets: dict[int, list[str]] = {}
        frozen: list[tuple[int, str, str]] = []

        # Cooked that day means cooked that day: a lunch the draft left as a
        # reheat is made its own cook of the same dish (QA walk 2,
        # 2026-10-02: the step said "4 lunches cooked on the day", the model
        # pointed Thursday's lunch at an away Wednesday, and
        # repair_leftover_chains re-pointed it at Tuesday's lunch — two-day-old
        # salad nobody was told about).
        if _uncook_cooked_lunches(plan_id, days, lunches, chains, intake, out):
            lunches, dinners = _lunch_and_dinner_rows(plan_id)
            chains = _leftovers.plan_leftover_chains(plan_id)
            keys = _leftovers.run_keys(plan_id)

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
            # The night the household TAGGED Leftovers wins over this lunch
            # when both want the same pot (review, 2026-09-27; flagged to
            # Emily as a default): the lunch reheats another cook instead —
            # the lunch before, a dinner within reach — or stays as drafted.
            # So does a lunch that would END a run of three counting back.
            tonight = next((r for r in dinners.get(d["date"], []) if r["slot_state"] == "planned"), None)
            tagged_tonight = bool(tonight) and (
                "left" in (tonight["derived"].get("tags") or [])
                or tonight["derived"].get("constraint") == "leftovers_night"
            )
            if _leftovers.ends_too_long_a_run(keys, d["date"], "lunch", cook["meal"]) or (
                tagged_tonight and _leftovers.too_many_in_a_row(keys, d["date"], "lunch", cook["meal"])
            ):
                every_row = [r for rows_ in list(lunches.values()) + list(dinners.values()) for r in rows_]
                other = _weekly_plan._nearest_cook(every_row, keys, d["date"], "lunch",
                                                   exclude={cook["id"]} | set(_ids(rows)))
                if other is None:
                    out["skipped"].append({"date": d["date"], "why": "that would be a third meal of it in a row",
                                           "run": {"dish": cook["meal"], "cook_date": cook["date"]}})
                    continue
                cook = other
            if len(rows) == 1 and rows[0]["meal"].strip().lower() != cook["meal"].strip().lower():
                kept = _keep_request_dish(plan_id, rows[0], intake, dinners, chains)
                if kept == "kept":
                    out["skipped"].append({"date": d["date"], "why": "the lunch is a dish they asked for"})
                    out["said"].append(kept_line(
                        d["date"], rows[0]["meal"], "leftovers",
                        phrase=request_phrase(rows[0]["derived"], intake, rows[0]["meal"])))
                    continue
                if kept == "moved":
                    # A later day's evening-before dinner may be the one it moved to.
                    _l, dinners = _lunch_and_dinner_rows(plan_id)
                    keys = _leftovers.run_keys(plan_id)
            keys[(d["date"], "lunch")] = _leftovers.dish_identity(cook["meal"])
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

        # Prepped: one dish per prep date (per `batch` within it, when one
        # session cooks several — normalize's dishes_per_prep_day), cooked
        # on its first lunch.
        batches: dict[tuple, list[dict]] = {}
        for d in days:
            if d["kind"] != "prepped":
                continue
            prep_date = prep_date_for(d["date"], prep_days)
            if prep_date is None:
                continue
            batches.setdefault((prep_date, int(d.get("batch") or 0)), []).append(d)
        session_dishes: dict[str, set] = {}
        # Re-read: the leftovers writes above replaced rows.
        lunches, dinners = _lunch_and_dinner_rows(plan_id)
        chains = _leftovers.plan_leftover_chains(plan_id)
        for n, ((prep_date, batch_no), members) in enumerate(sorted(batches.items())):
            if n:
                # Re-read between batches: an earlier batch's lunches can
                # fall ON a later batch's prep day (Sun + Tue prep: Tuesday's
                # lunch reheats Sunday's batch, and Tuesday evening is the
                # next prep), and a stale row there would make a replaced
                # entry the next batch's cook.
                lunches, dinners = _lunch_and_dinner_rows(plan_id)
                chains = _leftovers.plan_leftover_chains(plan_id)
            members.sort(key=lambda d: d["date"])
            rows_by_date = {
                d["date"]: [r for r in lunches.get(d["date"], []) if r["slot_state"] == "planned"]
                for d in members
            }
            # The batch's dish: the first of its lunches the model drafted
            # as a real cook (not a reheat of something else) — and, for a
            # session's second dish, not the dish its first batch took.
            taken = session_dishes.setdefault(prep_date, set())
            model_cook = next(
                (rows[0] for d in members for rows in [rows_by_date[d["date"]]]
                 if len(rows) == 1 and _cookable(rows[0]) and rows[0]["id"] not in chains["leftovers"]
                 and rows[0]["meal"].strip().lower() not in taken),
                None,
            )
            first = members[0]
            first_rows = rows_by_date[first["date"]]
            note = (f"Cook this {_title(_weekday(prep_date))} for "
                    f"{batch_lunch_phrase([d['date'] for d in members])}.")
            # `prep_note` keeps the schedule fact on the cook's own data,
            # whatever later writes the reasoning line or hides it on the
            # row (Emily, 2026-09-27: the Sunday line was hidden under
            # "as asked"). get_week_menu hands it out as schedule_note.
            cook_derived = {"constraint": CONSTRAINT, "prep_day": _weekday(prep_date), "prep_date": prep_date,
                            "prep_note": note}
            # The prep day inside the week, earlier than every lunch it
            # feeds (Emily, 2026-09-27: Sunday's curry fed nothing and
            # Monday got a fresh 25-minute pancake): that day's own lunch —
            # else its dinner — when it is a real cook IS the batch, and
            # every lunch of the batch reheats it. Else, as before, the
            # batch is cooked on its first lunch's entry.
            # A batch whose FIRST lunch is past the three-day reach of its
            # prep day is cooked on that lunch instead (below) — the same
            # rule plan-week's lunchLine reads ("Cooked that day").
            stale = _leftovers.days_apart(prep_date, first["date"]) > _leftovers.MAX_LEFTOVER_DAYS
            # The prep day's own meal can be ONE batch's cook: a session's
            # second dish is cooked on its own first lunch's entry.
            prep_cook = None if stale or batch_no else _prep_day_cook(prep_date, first["date"], lunches, dinners, chains)
            # What the three-day reach counts from: the prep day, unless the
            # batch turns out to be cooked on its own first lunch (below).
            fresh_from = prep_date
            if prep_cook is not None:
                cook_id, cook_date = prep_cook["id"], prep_cook["date"]
                dish, groups = prep_cook["meal"], _food_groups(prep_cook)
                derived = dict(prep_cook["derived"])
                derived.update(cook_derived)
                # The prep day's own meal, not one of the batch's lunches:
                # prepped_batches leaves its day out of the lunches it feeds.
                derived["prep_day_cook"] = True
                if prep_cook["slot"] == "lunch":
                    _save_derived(cook_id, derived, reasoning=note)
                else:
                    # A dinner keeps its own line; its make_double_note says
                    # what the extra is for.
                    derived.pop("constraint", None)
                    _save_derived(cook_id, derived)
                rest = members
            else:
                if model_cook is None or not first_rows:
                    out["skipped"].append({"date": first["date"], "why": "no prepped lunch to build the batch from"})
                    continue
                dish, groups = model_cook["meal"], _food_groups(model_cook)
                cook_date = first["date"]
                if first["date"] == prep_date or stale:
                    # Nothing made on that prep day can still feed this
                    # lunch — it is LAST week's prep (a lone Wednesday prep
                    # and a Monday lunch: 5 days back), which this plan never
                    # made. So the batch is simply cooked that day, on its
                    # first lunch, with no prep stamp: the Cook tab shows an
                    # ordinary cook with its start-by, never "Prepped
                    # Wednesday" a week late (build review, 2026-09-28).
                    # plan-week's lunchLine says "Cooked that day" for it.
                    # The line names only the lunches it makes besides
                    # itself (integration review, 2026-09-27).
                    later = [d["date"] for d in members[1:]]
                    note = f"Makes {batch_lunch_phrase(later)} too." if later else ""
                    cook_derived = {"constraint": CONSTRAINT, "prep_note": note}
                    fresh_from = cook_date
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
                rest = members[1:]
            for d in rest:
                rows = rows_by_date[d["date"]]
                if not rows:
                    continue
                if any(r["id"] in chains["sources"] for r in rows):
                    out["skipped"].append({"date": d["date"], "why": "the lunch already feeds another meal"})
                    continue
                if len(rows) == 1 and rows[0]["meal"].strip().lower() != dish.strip().lower() and \
                        _keep_request_dish(plan_id, rows[0], intake, dinners, chains) == "kept":
                    out["skipped"].append({"date": d["date"], "why": "the lunch is a dish they asked for"})
                    out["said"].append(kept_line(
                        d["date"], rows[0]["meal"], "prepped", prep_date,
                        phrase=request_phrase(rows[0]["derived"], intake, rows[0]["meal"])))
                    continue
                if _leftovers.days_apart(fresh_from, d["date"]) > _leftovers.MAX_LEFTOVER_DAYS:
                    # Past three days from the prep day: a portion frozen on
                    # the cook (the fold's freezer night, same row).
                    _weekly_plan._replace_slot_entries(
                        plan_id, _ids(rows), d["date"], "lunch",
                        _leftovers.freezer_night_name(dish, cook_date), reasoning="",
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
            taken.add(dish.strip().lower())
            out["prepped"].append({"prep_date": prep_date, "dish": dish, "lunches": [d["date"] for d in members]})

        if targets or frozen:
            written = {"batched": []}
            _meal_variety._write_cook_sides(plan_id, targets, frozen, written)
    except Exception:
        logger.exception("Weekday lunches not applied to plan %s; the lunches stand as drafted", plan_id)
    if out["leftovers"] or out["prepped"] or out["frozen"] or out.get("cooked"):
        logger.info("Plan %s weekday lunches: %s", plan_id, out)
    return out


def rehome_prep_batch(plan_id: int, old_cook: dict) -> dict:
    """
    A prep-day DINNER that cooked a prepped lunch batch (apply_to_plan's
    `prep_cook`, stamped prep_day_cook) has just been replaced by a later
    pass — the no-three-in-a-row pass (dinner_gaps.break_long_runs) is the
    one that does it: Sun + Tue prep, Chili drafted for every lunch AND for
    Tuesday's dinner, so Tue lunch / Tue dinner / Wed lunch is three Chilis
    in a row and Tuesday's dinner is the only meal of the run it may change.
    _replace_slot_entries unlinks the nights the old cook fed, which left
    the batch's lunches as three separately cooked Chilis under a sheet that
    says "Prepped Tuesday" (Loop Board, 2026-09-28).

    So the batch moves to its first lunch — the shape apply_to_plan writes
    when the prep day has no meal of its own to cook it on: that lunch is
    the cook, stamped with the prep day (prepped_batches puts the work back
    on the prep day's session), and every other lunch of the batch reheats
    it or eats a portion frozen on it, as before. The dishes stay what they
    were, so no run the break pass just fixed comes back.

    `old_cook` is the replaced row as read BEFORE the replace: its id, meal
    and derived_from (`derived`). A no-op for anything that was not a
    prep-day cook. Never raises.
    """
    from . import leftovers as _leftovers
    from . import meal_variety as _meal_variety
    from . import weekly_plan as _weekly_plan

    out = {"cook": None, "linked": [], "frozen": []}
    try:
        derived = old_cook.get("derived") or {}
        prep_date = str(derived.get("prep_date") or "").strip()
        if not prep_date or not derived.get("prep_day_cook"):
            return out
        old_ref = f"entry_id:{old_cook['id']}"
        dish = old_cook.get("meal") or ""
        same = _leftovers.dish_identity(dish)
        fed = derived.get("make_double_for") or []
        if isinstance(fed, str):
            fed = [fed]
        fed_dates = sorted({t.split(":")[0] for t in fed if t.endswith(":lunch")})
        lunches, _ = _lunch_and_dinner_rows(plan_id)

        def planned(d):
            return [r for r in lunches.get(d, []) if r["slot_state"] == "planned"]

        # The lunches it fed from the fridge: unlinked by the replace, still
        # the batch's dish, still the household's weekday-lunch answer.
        reheats = []
        for d in fed_dates:
            rows = planned(d)
            if (len(rows) == 1 and not rows[0]["derived"].get("links_to")
                    and rows[0]["derived"].get("constraint") == CONSTRAINT
                    and _leftovers.dish_identity(rows[0]["meal"]) == same):
                reheats.append(rows[0])
        # …and from the freezer: the replace leaves those pointing at a row
        # that no longer exists.
        frozen_rows = [
            r for d in sorted(lunches) for r in planned(d)
            if isinstance(r["derived"].get(_leftovers.FROM_FREEZER_KEY), dict)
            and r["derived"][_leftovers.FROM_FREEZER_KEY].get("cook") == old_ref
        ]
        if not reheats:
            return out
        first, rest = reheats[0], reheats[1:]
        note = (f"Cook this {_title(_weekday(prep_date))} for "
                f"{batch_lunch_phrase([r['date'] for r in reheats + frozen_rows])}.")
        cook = dict(first["derived"])
        cook.pop("cook_ahead", None)
        cook.update({"constraint": CONSTRAINT, "prep_day": derived.get("prep_day") or _weekday(prep_date),
                     "prep_date": prep_date, "prep_note": note})
        _save_derived(first["id"], cook, reasoning=note)
        out["cook"] = first["date"]
        targets: dict[int, list[str]] = {first["id"]: []}
        for r in rest:
            link = dict(r["derived"])
            link.update({"links_to": f"entry_id:{first['id']}", "cook_ahead": True, "constraint": CONSTRAINT})
            _save_derived(r["id"], link)
            targets[first["id"]].append(f"{r['date']}:lunch")
            out["linked"].append(r["date"])
        frozen = []
        for r in frozen_rows:
            _weekly_plan._replace_slot_entries(
                plan_id, [r["id"]], r["date"], "lunch",
                _leftovers.freezer_night_name(first["meal"], first["date"]), reasoning="",
                derived_from=dict(r["derived"], **{
                    _leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{first['id']}", "dish": first["meal"]},
                }),
            )
            frozen.append((first["id"], r["date"], "lunch"))
            out["frozen"].append(r["date"])
        if targets[first["id"]] or frozen:
            _meal_variety._write_cook_sides(plan_id, {k: v for k, v in targets.items() if v}, frozen,
                                            {"batched": []})
        logger.info("Plan %s: prepped batch of %s moved to %s's lunch: %s", plan_id, dish, first["date"], out)
    except Exception:
        logger.exception("Moving the prepped batch off replaced entry %s on plan %s failed",
                         old_cook.get("id"), plan_id)
    return out


def enforce_lunch_count(plan_id: int, intake: dict | None, target: int | None,
                        asks: tuple[str | None, ...] = (), caps: dict | None = None) -> dict:
    """
    The lunch count ("Lunches" under Different dishes a week) on a week
    whose weekday lunches were answered — after apply_to_plan, in place of
    the ordinary count pass (Emily, 2026-09-27: Lunches = 2, and her week
    came out with four lunch dishes).

    - A lunch on an answered day, and a prepped batch's cook on its prep
      day, is the household's own: counted, never rewritten (a "cooked
      that day" lunch folded into a reheat would undo what they said).
    - A lunch that is a DINNER's leftovers is not a lunch dish: not
      counted, not touched.
    - Everything else — a weekend lunch, a weekday lunch they didn't
      answer — folds into the kept dishes through the ordinary fold
      (meal_variety.enforce_distinct_count, pinned/ignored as above), so
      it eats a kept batch within three days, else a frozen portion.

    No model call, never adds a dish (a count above what the week has is
    left there). Never raises. Returns the fold's result.
    """
    from . import leftovers as _leftovers
    from . import meal_variety as _meal_variety

    out: dict = {"skipped": None}
    try:
        answered = set(kinds_by_date(intake))
        chains = _leftovers.plan_leftover_chains(plan_id)
        lunches, _dinners = _lunch_and_dinner_rows(plan_id)
        pinned, ignore = set(), set()
        for rows in lunches.values():
            for r in rows:
                reheat = chains["leftovers"].get(r["id"])
                if reheat and reheat["source"]["slot"] == "dinner":
                    ignore.add(r["id"])
                elif r["date"] in answered or r["derived"].get("prep_date"):
                    pinned.add(r["id"])
        out = _meal_variety.enforce_distinct_count(
            plan_id, target, slot="lunch", asks=asks, fill_up=False, caps=caps,
            pinned_ids=pinned, ignore_ids=ignore,
        )
    except Exception:
        logger.exception("Lunch count not applied to plan %s; the lunches stand as they are", plan_id)
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
        # Lunches only: a prep day's DINNER can be a batch's cook
        # (_prep_day_cook), and its own day and any dinner reheating it are
        # not lunches the batch feeds.
        if not ref.startswith("entry_id:") or row["slot"] != "lunch":
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
            # A prep day's own meal as the cook (apply_to_plan's
            # _prep_day_cook) is not one of the lunches the batch is FOR —
            # the line reads "For Monday's lunch", as the cook's reasoning does.
            "prep_day_cook": bool(row["derived"].get("prep_day_cook")),
            "lunch_dates": sorted(
                ({row["date"]} if row["slot"] == "lunch" and not row["derived"].get("prep_day_cook") else set())
                | fed.get(row["id"], set())
            ),
        })
    out.sort(key=lambda b: (b["prep_date"], b["cook_date"], b["cook_entry_id"]))
    return out


def prepped_batch_by_cook(plan_id: int) -> dict[int, dict]:
    """prepped_batches keyed by the cook's entry id — what a card reader
    wants (cooker._apply_prepped_lunches)."""
    return {b["cook_entry_id"]: b for b in prepped_batches(plan_id)}
