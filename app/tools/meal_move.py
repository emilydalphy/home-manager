"""
Move one meal to another day (Emily, 2026-09-28 — Option A of the "swap
meals between days" mockups, answered in chat that day).

    Tap Move on a meal → "Move the stew to which day?" lists every other
    day with its meal of that kind → pick a day → the two meals trade
    places. The toast names what moved, with Undo.

Her decisions, each of which is a rule below:

  1. ONE meal moves, not a whole day.
  2. Dinners and lunches (MOVABLE_SLOTS) — not breakfasts or snacks.
  3. A dinner's leftovers night moves WITH it, keeping the same gap: stew
     Monday + leftovers Tuesday, moved to Thursday, is stew Thursday +
     leftovers Friday — and Thursday's and Friday's meals come back to
     Monday and Tuesday. A day where that can't work is shown dimmed with
     the reason, never hidden (`move_options`).
  4. A long dish may move onto a short-on-time day; the picker says so
     ("40 min on a short-on-time day") and nothing else asks.
  5. Drafts and approved weeks alike. The grocery list stays the same
     (rows keep their ids, so their grocery links ride along); a thaw that
     now has to start earlier is said in one line (`thaw_notes`).

THIS IS NOT A SECOND MOVER. The write is weekly_plan._redate_plan_rows, the
one the nights swap has always used (lifted out of it for this card): rows
re-dated in place, "date:slot" chain references rewritten, defrost
reminders moved by the same number of days, the Undo token on each moved
row. What this module adds is the PLACEMENT — which rows go where when a
meal travels with its leftovers — and the checks a hand-made move owes the
week that generation already keeps:

  * nobody-home and left-out days, cooked meals and days already gone
    never take part;
  * a leftovers chain never runs backwards (the writer's own check) and
    never ends up more than MAX_LEFTOVER_DAYS from its cook;
  * no dish lands on a third lunch-or-dinner in a row
    (leftovers.MAX_MEALS_IN_A_ROW, Emily's rule);
  * somebody at the new table who has said no to the dish is a veto, the
    same one draft_flags.move_target and cap_enforce refuse trades on.

The placement is a permutation, so it works for any chain shape: every
row of the moving meal shifts by the same number of days; each row it
lands on goes to the place one of the moving rows left, in eating order.
For the ordinary case (one cook, one leftovers night, both landing on
ordinary meals) that is exactly two trades — the mockup's four nights.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta

from ..db import get_conn
from ._shared import household_id
from . import leftovers as _leftovers
from . import weekly_plan as _weekly_plan
from . import tonight as _tonight
from . import usual_week as _usual_week

logger = logging.getLogger("home_manager")

MOVABLE_SLOTS = ("lunch", "dinner")
_SLOT_PLURALS = {"lunch": "lunches", "dinner": "dinners"}

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _weekday(d: str) -> str:
    return _WEEKDAYS[date.fromisoformat(d).weekday()]


def _shift(d: str, days: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def short_name(dish: str | None) -> str:
    """"Thai Basil Chicken" for "Thai Basil Chicken (Pad Kra Pao) with
    Jasmine Rice" — the main dish, which is how the sheet and the toast
    name it: the title before its first " with ", with anything in
    brackets taken out, in the dish's own capitals. A title with no
    "with" is said in full ("Creamy Chicken and Vegetable Stew").

    QA walk 2, 2026-10-02: this used to be the title's LAST word, and most
    generated titles end in a side, so the sheet warned "The rice would end
    up after its leftovers" about a basil chicken. The same rule as
    shell.js dishShortName."""
    text = " ".join(re.sub(r"[(\[][^)\]]*[)\]]", " ", dish or "").split())
    main = re.split(r"(?:^|\s)with\s", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    # Nothing before "with" (a title that is all brackets, or starts with
    # "with"): the whole title as written; no title at all: "meal".
    return main or " ".join((dish or "").split()) or "meal"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


# ---------- the week, read once ----------

def _snapshot(weekly_plan_id: int, conn) -> dict:
    plan = conn.execute(
        "SELECT id, week_start_date, content_start_date, day_count, planning_mode, status "
        "FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    if plan is None:
        raise ValueError(f"No weekly plan with id {weekly_plan_id}.")
    if plan["planning_mode"] == "component_based":
        raise ValueError("That plan is built from components, not days — there's nothing to move.")
    start, day_count = _weekly_plan.plan_period(plan)
    end = _weekly_plan.period_end_date(start, day_count)
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal,
               r.prep_time_minutes, r.cook_time_minutes
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date ASC, mpe.id ASC
        """,
        (weekly_plan_id, household_id()),
    ).fetchall()
    rows = [dict(r) for r in rows]
    chains = _leftovers.plan_leftover_chains(weekly_plan_id, conn=conn)
    return {"plan": dict(plan), "start": start, "end": end, "rows": rows, "chains": chains,
            "home": _home_at_empty_slots(conn, start, end, rows)}


def _home_at_empty_slots(conn, start: str, end: str, rows: list[dict]) -> dict:
    """{(date, slot): True if somebody is home} for the lunch-and-dinner
    positions this week holds NO ROW for (2026-10-05, card 7).

    Read HERE, once, so plan_move stays pure over the snapshot — the
    module's own rule, and the one that lets the picker ask plan_move of
    every day at once and the write ask it again inside its transaction.
    Normally this reads nothing at all: a generated week has a row in every
    slot (audit_plan_slots asserts exactly that), so an empty position is
    the leftover of a delete path and the ordinary case is zero queries.

    `conn` is passed down rather than opened: this runs inside
    move_meal's BEGIN IMMEDIATE, where a nested get_conn is the
    "database is locked" trap. get_slot_attendance only ever reads on a
    connection it was handed.
    """
    from . import attendance as _attendance  # lazy: it imports weekly_plan, which reaches back here
    filled = {(r["date"], r["slot"]) for r in rows}
    out: dict[tuple[str, str], bool] = {}
    d = start
    while d <= end:
        for slot in MOVABLE_SLOTS:
            if (d, slot) in filled:
                continue
            try:
                att = _attendance.get_slot_attendance(d, slot, conn=conn)
            except Exception:
                # A clock or a member read that fails must not make the
                # whole Move sheet refuse: an unreadable table reads as
                # the app's own default, everybody's home.
                logger.exception("Reading attendance for %s %s failed", d, slot)
                out[(d, slot)] = True
                continue
            # household_size 0 is "nobody has told us who lives here",
            # NOT "nobody is home" — attendance.nobody_home is a headcount
            # test, so an empty members table makes it true of every slot
            # of every day. Reading it as a refusal would have blocked
            # every empty day for a household mid-onboarding, which is the
            # bug this card is about wearing the other hat. The app's own
            # stance elsewhere (grocery_scale_factor, servings_scale_factor)
            # is the same: size 0 means unknown, so take the default.
            out[(d, slot)] = att["household_size"] == 0 or not att["nobody_home"]
        d = _shift(d, 1)
    return out


def _somebody_home(snap: dict, d: str, slot: str) -> bool:
    """Whether a position the week holds no row for has anybody at it. An
    unasked position reads as the app's own default — everybody's home."""
    return bool(snap.get("home", {}).get((d, slot), True))


def _derived(row: dict) -> dict:
    try:
        return json.loads(row.get("derived_from_json") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def _minutes(row: dict) -> int | None:
    prep, cook = row.get("prep_time_minutes"), row.get("cook_time_minutes")
    if prep is None and cook is None:
        return None
    return ((prep or 0) + (cook or 0)) or None


def _is_reheat(row: dict, chains: dict) -> bool:
    """A night that eats another cook's food — a chain's reheat, a freezer
    portion, or a "Leftovers …" line the model wrote as a name. It moves
    only with the meal it comes from."""
    derived = _derived(row)
    if row["id"] in chains["leftovers"] or (derived.get("links_to") or "").strip():
        return True
    if _leftovers.frozen_portion_on(derived):
        return True
    return bool(re.search(r"\bleftovers?\b", row.get("meal") or "", re.IGNORECASE))


def _slot_row(snap: dict, d: str, slot: str) -> dict | None:
    """The row a day holds for this meal — the first by id, the rule every
    reader of (date, slot) uses."""
    for r in snap["rows"]:
        if r["date"] == d and r["slot"] == slot:
            return r
    return None


def _nobody_home_row(row: dict) -> bool:
    """Whether a planned_empty row is empty because nobody is eating.

    Read from the CONSTRAINT, never from _empty_reason's sentence. That
    sentence used to be the discriminator, and re-wording it for card 7
    (2026-10-05) silently turned a leftovers night landing on a
    nobody-home day from "and nobody’s home" into "which isn’t
    planned" — a reader of a shared string broken by a change to the
    string, which is the trap this repo keeps writing down. One fact, one
    reader.
    """
    derived = _derived(row)
    constraint = derived.get("constraint")
    return not (constraint == "already_past" or constraint == _tonight.NIGHT_OFF_CONSTRAINT
                or _meal_not_asked_for(constraint))


def _meal_not_asked_for(constraint) -> bool:
    """The household is home but asked for no plan here: a day skipped in
    the week's answers, a meal their usual week has off, or a meal they
    asked for none of all week ("dinners_per_week:0", agent._finish_week_slots).
    All three fell through to "Nobody’s home" — people ARE home, so saying
    otherwise contradicts the day they can see (review of the night-off
    fix, 2026-10-09)."""
    return (constraint in (_weekly_plan._week_intake.SKIPPED_DAY_CONSTRAINT,
                           _usual_week.OFF_CONSTRAINT)
            or (isinstance(constraint, str) and constraint.endswith("s_per_week:0")))


def _empty_reason(row: dict, slot: str) -> str:
    """Why a day's meal can't take part, when nobody's eating it.

    Named for the slot ("Nobody’s home for dinner") since 2026-10-05:
    the reason is now a line of its own under the day's label rather than
    standing IN its place, so it has to read as a sentence about this
    meal rather than about the day.
    """
    derived = _derived(row)
    constraint = derived.get("constraint")
    if constraint == "already_past":
        return "Already gone by"
    if _meal_not_asked_for(constraint):
        return f"No {slot} planned that day"
    # A night called off from Today (tonight.tonight_night_off) — the
    # household is home, just not cooking. It fell through to "Nobody’s
    # home" while Today said "Night off — enjoy." (defect hunt, 2026-10-09).
    if constraint == _tonight.NIGHT_OFF_CONSTRAINT:
        return "Night off"
    return f"Nobody’s home for {slot}"


# ---------- the placement ----------

def plan_move(snap: dict, entry_id: int, to_date: str, today: str) -> dict:
    """
    Where every row goes if this meal moves to `to_date`, or why it can't.

    Pure over `snap` (the week as read once) — no reads, no writes — so the
    picker can ask it of every day at once and the write can ask it again
    inside its transaction. `today` is the household's date (ISO).

    Returns {"ok": True, "placement": {entry_id: new_date}, "members",
    "displaced", "delta", "opens"} or {"ok": False, "reason": short
    sentence}. `opens` is the (date, slot) positions the meal leaves that
    nothing comes back to — a day the week held no row for is a valid
    destination (card 7, 2026-10-05), and when the meal lands on one the
    day it left is handed back as an open question rather than left
    absent. Ordinarily empty.
    Raises ValueError for a request no screen should make (a breakfast, a
    day off the plan, the same day, a meal that isn't on this plan).
    """
    rows = snap["rows"]
    by_id = {r["id"]: r for r in rows}
    source = by_id.get(entry_id)
    if source is None:
        raise ValueError("That meal isn't on this week any more.")
    slot = source["slot"]
    if slot not in MOVABLE_SLOTS:
        raise ValueError("Only lunches and dinners move between days.")
    try:
        date.fromisoformat(to_date)
    except (TypeError, ValueError):
        raise ValueError("The day must be an ISO date (YYYY-MM-DD).")
    if not (snap["start"] <= to_date <= snap["end"]):
        raise ValueError(f"{_weekday(to_date)} ({to_date}) isn't on this plan.")
    if to_date == source["date"]:
        raise ValueError("That's the day it's already on.")
    if source["slot_state"] != "planned" or not (source["meal"] or "").strip():
        raise ValueError("There's no meal there to move.")
    chains = snap["chains"]
    if _is_reheat(source, chains):
        return {"ok": False, "reason": "Leftovers move with the meal they come from"}
    if (source["cooked_status"] or "") == "done":
        return {"ok": False, "reason": "Already cooked"}
    if source["date"] < today:
        return {"ok": False, "reason": "Already gone by"}

    delta = (date.fromisoformat(to_date) - date.fromisoformat(source["date"])).days
    members = [source]
    for t in (chains["sources"].get(source["id"]) or {}).get("targets") or []:
        row = by_id.get(t["entry_id"])
        if row is not None:
            members.append(row)
    member_pos = {(m["date"], m["slot"]) for m in members}
    dish = short_name(source["meal"])

    # The day tapped is one of the dish's own leftovers nights.
    if (to_date, slot) in member_pos:
        # Shown dimmed with no line of its own: the row already reads
        # "Chicken Stew leftovers", which is the reason.
        return {"ok": False, "reason": ""}

    targets: dict[int, str] = {}
    for m in members:
        new = _shift(m["date"], delta)
        if new > snap["end"]:
            return {"ok": False, "reason": "Its leftovers would land after this week"}
        if new < snap["start"]:
            return {"ok": False, "reason": "Its leftovers would land before this week"}
        if (m["cooked_status"] or "") == "done":
            return {"ok": False, "reason": "Its leftovers were already had"}
        targets[m["id"]] = new

    # The rows the moving meal lands on, and the places it leaves.
    displaced: list[dict] = []
    for m in members:
        pos = (targets[m["id"]], m["slot"])
        if pos in member_pos:
            continue
        there = _slot_row(snap, *pos)
        # The tapped day speaks for itself; a leftovers night landing on
        # another day names that day.
        tapped = pos[0] == to_date
        wd = _weekday(pos[0])
        if there is None:
            # A position the week holds NO row for is a valid destination
            # when somebody is home for it (Emily, card 7, 2026-10-05 —
            # the tester's "only giving certain days"). Nothing is
            # displaced from it, so the day the meal LEAVES ends up with
            # nothing coming back: it is handed back as an open question
            # below, the way drop_dish_from_day hands one back.
            #
            # planned_empty is a different answer and stays refused: it
            # means nobody is home, or the household asked for none of
            # that meal, and such a slot must never be offered as a
            # decision.
            if not _somebody_home(snap, *pos):
                reason = f"Nobody’s home for {m['slot']}"
                return {"ok": False, "reason": reason if tapped
                        else f"Its leftovers would land on {wd}, and nobody’s home"}
            if pos[0] < today:
                return {"ok": False, "reason": "Already gone by" if tapped
                        else f"Its leftovers would land on {wd}, which has gone by"}
            continue
        if there["slot_state"] == "planned_empty":
            reason = _empty_reason(there, m["slot"])
            if tapped:
                return {"ok": False, "reason": reason}
            if _nobody_home_row(there):
                return {"ok": False, "reason": f"Its leftovers would land on {wd}, and nobody’s home"}
            return {"ok": False, "reason": f"Its leftovers would land on {wd}, which isn’t planned"}
        if (there["cooked_status"] or "") == "done":
            return {"ok": False, "reason": "Already cooked" if tapped else f"{wd}’s {m['slot']} is already cooked"}
        if pos[0] < today:
            return {"ok": False, "reason": "Already gone by"}
        if tapped and there["slot_state"] == "planned" and \
                _leftovers.dish_identity(there["meal"]) == _leftovers.dish_identity(source["meal"]):
            return {"ok": False, "reason": "Same dish"}
        displaced.append(there)
    target_pos = {(targets[m["id"]], m["slot"]) for m in members}
    vacated = sorted(
        [(m["date"], m["slot"]) for m in members if (m["date"], m["slot"]) not in target_pos],
        key=lambda p: (p[0], _leftovers._SLOT_ORDER.get(p[1], 9)),
    )
    displaced.sort(key=lambda r: (r["date"], _leftovers._SLOT_ORDER.get(r["slot"], 9), r["id"]))
    # Same slot for each pair: a lunch only ever trades with a lunch. The
    # chain's own rows keep their slots, so the counts per slot agree.
    placement = dict(targets)
    opens: list[tuple[str, str]] = []
    for slot_name in {p[1] for p in vacated}:
        free = [p for p in vacated if p[1] == slot_name]
        going = [r for r in displaced if r["slot"] == slot_name]
        # More rows coming back than places for them is still a refusal.
        # FEWER is the empty-destination case: the meal landed somewhere
        # the week held no row, so one of the places it left has nothing
        # coming back to it and is handed back as a question.
        if len(going) > len(free):
            return {"ok": False, "reason": "That would leave a meal with nowhere to go"}
        for r, p in zip(going, free):
            placement[r["id"]] = p[0]
        opens.extend(free[len(going):])

    after = _after_rows(rows, placement)
    said = _structural_refusal(snap, rows, after, placement, source)
    if said:
        return {"ok": False, "reason": said}
    return {"ok": True, "placement": placement, "members": [m["id"] for m in members],
            "displaced": [r["id"] for r in displaced], "delta": delta,
            "opens": sorted(opens, key=lambda p: (p[0], _leftovers._SLOT_ORDER.get(p[1], 9)))}


def _after_rows(rows: list[dict], placement: dict[int, str]) -> list[dict]:
    """The week with the placement applied and every "date:slot" reference
    rewritten, in memory — what the checks below are asked of."""
    by_id = {r["id"]: r for r in rows}
    mapping = {f"{by_id[i]['date']}:{by_id[i]['slot']}": f"{d}:{by_id[i]['slot']}"
               for i, d in placement.items()}
    out = []
    for r in rows:
        derived = _derived(r)
        if "links_to" in derived:
            derived["links_to"] = _weekly_plan._rewrite_chain_ref(derived["links_to"], mapping)
        fed = derived.get("make_double_for")
        if fed:
            fed_list = [fed] if isinstance(fed, str) else list(fed)
            derived["make_double_for"] = [_weekly_plan._rewrite_chain_ref(t, mapping) for t in fed_list]
        out.append({**r, "date": placement.get(r["id"], r["date"]), "derived": derived,
                    "derived_from_json": json.dumps(derived)})
    return out


def _chain_pairs(rows: list[dict], frozen_only: bool = False) -> dict[int, int]:
    """{reheat id: cook id} for every chain in these rows — the fridge
    leftovers (links_to), or with `frozen_only` the freezer portions
    (from_freezer), which have an order but no age limit."""
    by_date_slot = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        by_date_slot.setdefault((r["date"], r["slot"]), r)
    by_id = {r["id"]: r for r in rows}
    pairs = {}
    for r in rows:
        if r["slot_state"] != "planned":
            continue
        derived = r.get("derived") if "derived" in r else _derived(r)
        links_to = (derived.get("links_to") or "").strip()
        if links_to and not frozen_only and not isinstance(derived.get(_leftovers.FROM_FREEZER_KEY), dict):
            src = _weekly_plan._resolve_leftover_source(links_to, by_date_slot, by_id)
            if src is not None and src["id"] != r["id"]:
                pairs[r["id"]] = src["id"]
        frozen = derived.get(_leftovers.FROM_FREEZER_KEY)
        if frozen_only and isinstance(frozen, dict):
            ref = str(frozen.get("cook") or "")
            if ref.startswith("entry_id:"):
                try:
                    cook = int(ref.split(":", 1)[1])
                except ValueError:
                    cook = None
                if cook in by_id and cook != r["id"]:
                    pairs.setdefault(r["id"], cook)
    return pairs


def _structural_refusal(snap: dict, before: list[dict], after: list[dict],
                        placement: dict[int, str], source: dict) -> str | None:
    by_id_after = {r["id"]: r for r in after}
    by_id_before = {r["id"]: r for r in before}
    # Leftovers never before their cook; never further from it than the
    # food-safety days (only where the move made the gap longer — a chain
    # the week already had is not this move's to judge).
    pairs_before = _chain_pairs([{**r, "derived": _derived(r)} for r in before])
    for reheat_id, cook_id in _chain_pairs(after, frozen_only=True).items():
        if reheat_id not in placement and cook_id not in placement:
            continue
        if _weekly_plan._eaten_at(by_id_after[cook_id]) >= _weekly_plan._eaten_at(by_id_after[reheat_id]):
            return f"The {short_name(by_id_after[cook_id]['meal'])} would end up after its frozen portion"
    for reheat_id, cook_id in _chain_pairs(after).items():
        if reheat_id not in placement and cook_id not in placement:
            continue
        reheat, cook = by_id_after[reheat_id], by_id_after[cook_id]
        if _weekly_plan._eaten_at(cook) >= _weekly_plan._eaten_at(reheat):
            return f"The {short_name(cook['meal'])} would end up after its leftovers"
        gap = _leftovers.days_apart(cook["date"], reheat["date"])
        if gap > _leftovers.MAX_LEFTOVER_DAYS:
            was = None
            if pairs_before.get(reheat_id) == cook_id:
                was = _leftovers.days_apart(by_id_before[cook_id]["date"], by_id_before[reheat_id]["date"])
            if was is None or gap > was:
                return f"The {short_name(cook['meal'])}’s leftovers would be {gap} days old"
    # A prepped lunch is cooked on its prep day; it can't be eaten before it.
    for i, d in placement.items():
        prep_date = str(_derived(by_id_before[i]).get("prep_date") or "")
        if prep_date and d < prep_date:
            return f"It’s prepped on {_weekday(prep_date)}"
        if prep_date and _leftovers.days_apart(prep_date, d) > _leftovers.MAX_LEFTOVER_DAYS \
                and _leftovers.days_apart(prep_date, d) > _leftovers.days_apart(prep_date, by_id_before[i]["date"]):
            return f"It’s prepped on {_weekday(prep_date)} — that’s too many days before"
    # At most two lunches and dinners in a row of the same dish.
    keys_before = _leftovers.run_keys_from_rows(before)
    keys_after = _leftovers.run_keys_from_rows(after)
    old_runs = {tuple(run) for run in _leftovers.long_runs(keys_before)}
    for run in _leftovers.long_runs(keys_after):
        if tuple(run) in old_runs:
            continue
        dish = keys_after.get(run[0]) or ""
        name = next((r["meal"] for r in after
                     if (r["date"], r["slot"]) == run[0] and r["slot_state"] == "planned"), dish)
        if _leftovers.dish_identity(name) != dish:
            # A reheat row: name the dish, not "Leftovers — Monday's …".
            name = dish
        return f"That makes three {short_name(name)} meals in a row"
    return None


# ---------- what the picker shows ----------

def _veto(meal: str | None, d: str, slot: str) -> str | None:
    """Somebody at that table has said no to the dish — the one-veto rule
    (cap_enforce.would_offend, asked for the meal's own slot)."""
    if not (meal or "").strip():
        return None
    verdict = _weekly_plan._taste_verdict_for_slot(meal, d, slot)
    if verdict and verdict.get("verdict") == "avoid":
        who = ", ".join(verdict.get("vetoed_by") or []) or "Someone"
        return f"{who} would rather not have the {short_name(meal)}"
    return None


def _caps(snap: dict) -> dict:
    """{(date, slot): minutes cap or None} for this week's days."""
    from . import memory as _memory
    from . import time_caps as _time_caps
    try:
        intake = _weekly_plan._week_intake.get_week_intake(snap["plan"]["week_start_date"]) or {}
    except Exception:
        intake = {}
    try:
        memory = _memory.get_household_memory() or {}
    except Exception:
        memory = {}
    tags = intake.get("night_tags") or {}
    out = {}
    d = snap["start"]
    while d <= snap["end"]:
        out[(d, "dinner")] = _time_caps.minutes_cap(d, "dinner", tags.get(d) or [], memory)
        d = _shift(d, 1)
    return out


def _time_note(row: dict, lands_on: str, caps: dict, own: bool) -> str | None:
    """"40 min on a short-on-time day" — a dish that would land over its
    new day's cap. Said, never refused (Emily's default, decision 4)."""
    if row["slot"] != "dinner":
        return None
    minutes = _minutes(row)
    cap = caps.get((lands_on, row["slot"]))
    if not (minutes and cap and minutes > cap):
        return None
    if own:
        return f"{minutes} min on a short-on-time day"
    return f"{minutes} min on short-on-time {_weekday(lands_on)}"


def _day_meal_label(row: dict | None, snap: dict) -> str:
    """What a day's meal of this kind is, as the picker row says it."""
    if row is None:
        return "Nothing planned"
    if row["slot_state"] == "planned_empty":
        return "Not planned"
    if row["slot_state"] == "open":
        return "Your call"
    meal = (row["meal"] or "").strip()
    src = snap["chains"]["leftovers"].get(row["id"])
    if src:
        return f"{src['source']['meal']} leftovers"
    return meal or "Nothing planned"


def move_options(weekly_plan_id: int, entry_id: int) -> dict:
    """
    The Move sheet for one meal: every other day of the week, in order,
    with what it holds now and whether the meal can go there.

    {"entry_id", "slot", "meal", "date", "title", "sub",
     "days": [{"date", "weekday", "meal", "minutes", "ok", "reason", "note"}]}

    `reason` is why a dimmed day can't take it (shown, never hidden —
    Emily's decision 3); `note` is the plain short-on-time line on a day
    that can (decision 4). No model call; one read of the week.
    """
    from . import cooker as _cooker
    today = _cooker.household_today().isoformat()
    conn = get_conn()
    try:
        snap = _snapshot(weekly_plan_id, conn)
    finally:
        conn.close()
    by_id = {r["id"]: r for r in snap["rows"]}
    source = by_id.get(entry_id)
    if source is None:
        raise ValueError("That meal isn't on this week any more.")
    if source["slot"] not in MOVABLE_SLOTS:
        raise ValueError("Only lunches and dinners move between days.")
    caps = _caps(snap)
    slot = source["slot"]
    meal = source["meal"] or ""
    targets = (snap["chains"]["sources"].get(source["id"]) or {}).get("targets") or []
    days = []
    d = snap["start"]
    while d <= snap["end"]:
        if d != source["date"]:
            here = _slot_row(snap, d, slot)
            label = _day_meal_label(here, snap)
            minutes = _minutes(here) if here and here["slot_state"] == "planned" else None
            entry = {"date": d, "weekday": _weekday(d), "meal": label, "minutes": minutes,
                     "ok": False, "reason": "", "note": ""}
            try:
                out = plan_move(snap, entry_id, d, today)
            except ValueError as e:
                out = {"ok": False, "reason": str(e)}
            if out["ok"]:
                by_after = out["placement"]
                # The veto, asked of every dish landing somewhere new —
                # reads, so asked here, outside plan_move.
                vetoed = None
                for i, new_d in by_after.items():
                    row = by_id[i]
                    if row["id"] in out["members"] and row["id"] != source["id"]:
                        continue  # a reheat of our own dish, judged with it
                    vetoed = _veto(row["meal"], new_d, row["slot"])
                    if vetoed:
                        break
                if vetoed:
                    entry["reason"] = vetoed
                else:
                    entry["ok"] = True
                    note = _time_note(source, d, caps, own=True)
                    if not note and here is not None and here["id"] in by_after:
                        note = _time_note(here, by_after[here["id"]], caps, own=False)
                    entry["note"] = note or ""
            else:
                # The reason is a LINE of its own under the day's label,
                # for every blocked day alike (card 7, 2026-10-05: "each
                # shows its reason as a visible line under the day, not
                # only on tap"). It used to be written OVER the label for
                # an empty or nobody-home day and under it for every
                # other, so which of the two lines a household had to
                # read depended on why the day was out.
                entry["reason"] = out["reason"]
            days.append(entry)
        d = _shift(d, 1)
    dish = short_name(meal)
    sub = f"The two {_SLOT_PLURALS.get(slot, slot + 's')} trade places."
    if targets:
        nights = _leftovers._join_days([_weekday(t["date"]) for t in targets])
        sub += f" {nights}’s leftovers move with the {dish}." if len(targets) == 1 else \
            f" The leftovers on {nights} move with the {dish}."
    return {
        "entry_id": entry_id, "slot": slot, "meal": meal, "date": source["date"],
        "title": f"Move the {dish} to which day?",
        "sub": sub,
        "days": days,
    }


# ---------- the write ----------

MOVE_TOKEN_KEY = "move"


def _thaw_notes(thaw: list[dict], today: str, also: list[str] = ()) -> list[str]:
    """One line per fridge move that now has to happen EARLIER than it
    did — the only thaw change worth a word (a later one simply shows up
    on its day). "Move the chicken thighs to the fridge today."

    The sentence itself is defrost.thaw_move_sentence, which this had
    first and which the swap doors now share (2026-10-01): three wordings
    for one fact is how two screens come to tell a household different
    things about the same chicken.

    `also` is what the post-commit recomputation found still owed today or
    earlier (defrost.resync_plan_thaws), merged in rather than reported as
    a second field — shell.js reads `thaw_notes` and nothing else, and a
    move that NEWLY earns a fridge move (the chain shrank, so a cook that
    was a reheat has its own ingredients now) has no before-and-after for
    the diff above to see. Deduplicated, since the ordinary case is the
    same sentence from both halves.
    """
    from . import defrost as _defrost  # lazy: it imports weekly_plan, which imports this

    notes = []
    for t in thaw:
        if not t.get("to") or not t.get("from") or t["to"] >= t["from"] or t.get("status") == "done":
            continue
        note = _defrost.thaw_move_sentence(t.get("description") or "", t["to"], today)
        if note and note not in notes:
            notes.append(note)
    for note in also or ():
        if note not in notes:
            notes.append(note)
    return notes


def _said(snap: dict, placement: dict[int, str], members: list[int], source_id: int) -> str:
    """"Stew was moved to Thursday, leftovers to Friday" / "Stew was moved
    to Thursday, tacos to Monday" — the house toast pattern, naming both
    halves of the trade (the mockup's own line)."""
    by_id = {r["id"]: r for r in snap["rows"]}
    src = by_id[source_id]
    dish = _cap(short_name(src["meal"]))
    line = f"{dish} was moved to {_weekday(placement[source_id])}"
    leftovers = [i for i in members if i != source_id]
    if leftovers:
        days = _leftovers._join_days([_weekday(placement[i]) for i in leftovers])
        return f"{line}, leftovers to {days}"
    other = [i for i in placement if i not in members]
    if len(other) == 1 and by_id[other[0]]["slot_state"] == "planned":
        o = by_id[other[0]]
        if o["id"] in snap["chains"]["leftovers"]:
            return f"{line}, leftovers to {_weekday(placement[o['id']])}"
        return f"{line}, {short_name(o['meal'])} to {_weekday(placement[o['id']])}"
    return line


def move_meal(weekly_plan_id: int, entry_id: int, to_date: str) -> dict:
    """
    Move one lunch or dinner (and its leftovers) to another day, trading
    places with what is there.

    Returns {"status": "moved", "said", "moved": [{entry_id, meal, slot,
    from, to}], "move_id", "thaw_notes", "days", "can_undo"}; or
    {"status": "refused", "message"} with nothing written. Raises
    ValueError for a request no screen should make.
    """
    from . import cooker as _cooker
    today = _cooker.household_today().isoformat()
    # The veto reads attendance and taste on connections of their own, so
    # it is asked BEFORE the write transaction (the nested-get_conn trap),
    # of the placement as it stands; the transaction then re-plans and
    # refuses if the week changed underneath.
    conn = get_conn()
    try:
        snap = _snapshot(weekly_plan_id, conn)
    finally:
        conn.close()
    first = plan_move(snap, entry_id, to_date, today)
    if not first["ok"]:
        return {"status": "refused", "message": _refusal_sentence(snap, entry_id, to_date, first["reason"])}
    by_id = {r["id"]: r for r in snap["rows"]}
    for i, d in first["placement"].items():
        if i in first["members"] and i != entry_id:
            continue
        vetoed = _veto(by_id[i]["meal"], d, by_id[i]["slot"])
        if vetoed:
            return {"status": "refused", "message": f"{vetoed}."}

    move_id = uuid.uuid4().hex[:12]
    at = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snap = _snapshot(weekly_plan_id, conn)
        planned = plan_move(snap, entry_id, to_date, today)
        if not planned["ok"] or planned["placement"] != first["placement"]:
            conn.rollback()
            return {"status": "refused", "message": "The week just changed — try that again."}
        placement = planned["placement"]
        opens = planned.get("opens") or []
        n = len(placement)
        cuts = _prep_cuts_that_will_shift(conn, weekly_plan_id, snap, placement)
        done = _weekly_plan._redate_plan_rows(
            conn, weekly_plan_id, snap["rows"], placement,
            token_for=lambda r: {"date": r["date"], "at": at, MOVE_TOKEN_KEY: move_id,
                                 "to": placement[r["id"]], "n": n, "source": entry_id,
                                 **({"opened": [f"{d}:{sl}" for d, sl in opens]} if opens else {}),
                                 **({"prep_cuts": cuts[r["id"]]} if cuts.get(r["id"]) else {})},
            move_prep_cuts=True,
        )
        if "refused" in done:
            conn.rollback()
            return {"status": "refused", "message": done["refused"]}
        _open_the_days_left_behind(conn, weekly_plan_id, snap, opens, entry_id, placement, move_id)
        _resay_freezer_nights(conn, snap, placement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    by_id = {r["id"]: r for r in snap["rows"]}
    moved = [{"entry_id": i, "meal": by_id[i]["meal"], "slot": by_id[i]["slot"],
              "from": by_id[i]["date"], "to": d} for i, d in placement.items()]
    still_owed = _after_move(weekly_plan_id, list(placement))
    dates = sorted({m["from"] for m in moved} | {m["to"] for m in moved})
    return {
        "status": "moved",
        "said": _said(snap, placement, planned["members"], entry_id),
        "moved": moved,
        "move_id": move_id,
        "thaw_notes": _thaw_notes(done["thaw"], today, still_owed),
        "prep_tasks_moved": done["prep_moved"],
        "days": _weekly_plan._menu_days_for(weekly_plan_id, dates),
        "can_undo": True,
    }


MOVE_OPENED_CONSTRAINT = "moved_away"


def _opened_reason(dish: str, to_date: str) -> str:
    """The question a day left behind asks: "You moved the Tacos to
    Thursday, so this one is yours to fill."

    drop_dish_from_day's own sentence, one verb over ("You cut X back, so
    this one is yours to fill") — one pattern for the one thing both doors
    do, which is take a meal off a day and hand the day back.
    """
    return f"You moved the {dish} to {_weekday(to_date)}, so this one is yours to fill."


def _open_the_days_left_behind(conn, weekly_plan_id: int, snap: dict,
                               opens: list, source_id: int, placement: dict[int, str],
                               move_id: str) -> None:
    """Hand back every position the move left with nothing coming to it.

    `open`, never `planned_empty` and never nothing: planned_empty means
    nobody is home or the household asked for none of that meal and must
    never be offered as a decision, and a slot that is simply ABSENT is
    the bug plan_slot_open exists to prevent. Written on the move's own
    connection and inside its transaction — plan_slot_open takes a `conn`
    for exactly this, and the gap between re-dating the rows and stating
    the day empty would otherwise be a genuinely missing slot.

    The move id goes on the row so Undo can find what this move opened
    and take it back off (undo_meal_move).
    """
    if not opens:
        return
    by_id = {r["id"]: r for r in snap["rows"]}
    dish = short_name(by_id[source_id]["meal"]) if source_id in by_id else "that meal"
    landed = placement.get(source_id) or ""
    for d, slot in opens:
        _weekly_plan.plan_slot_open(
            weekly_plan_id, d, slot, _opened_reason(dish, landed),
            derived_from={"constraint": MOVE_OPENED_CONSTRAINT, MOVE_TOKEN_KEY: move_id,
                          "moved": source_id},
            conn=conn,
        )


def _prep_cuts_that_will_shift(conn, weekly_plan_id: int, snap: dict, placement: dict[int, str]) -> dict:
    """{entry id: {prep task id: its date now}} for the prep cuts this move
    will carry earlier (weekly_plan._shift_late_prep_cuts: a cut that would
    fall after its meal) — kept on the Undo token so Undo puts each back on
    its own day rather than guessing."""
    from . import prep_sessions as _prep_sessions  # lazy: it imports weekly_plan
    out: dict[int, dict] = {}
    for i, new in placement.items():
        rows = conn.execute(
            "SELECT id, task_date FROM prep_tasks WHERE household_id = ? AND weekly_plan_id = ? "
            "AND task_type = ? AND meal_plan_entry_id = ?",
            (household_id(), weekly_plan_id, _prep_sessions.PREP_CUT_TASK_TYPE, i),
        ).fetchall()
        late = {str(t["id"]): t["task_date"] for t in rows if t["task_date"] and t["task_date"] > new}
        if late:
            out[i] = late
    return out


def _resay_freezer_nights(conn, snap: dict, placement: dict[int, str]) -> None:
    """A night eating a portion frozen on a cook that just moved names that
    cook's day ("Leftovers from the freezer — Monday’s Chili"); re-say it
    for the day the cook is on now. Only a name that is still exactly the
    one Pomona wrote is touched."""
    by_id = {r["id"]: r for r in snap["rows"]}
    for r in snap["rows"]:
        frozen = _derived(r).get(_leftovers.FROM_FREEZER_KEY)
        if not isinstance(frozen, dict):
            continue
        ref = str(frozen.get("cook") or "")
        if not ref.startswith("entry_id:"):
            continue
        try:
            cook_id = int(ref.split(":", 1)[1])
        except ValueError:
            continue
        cook = by_id.get(cook_id)
        if cook is None or cook_id not in placement:
            continue
        dish = frozen.get("dish") or cook["meal"] or ""
        old = _leftovers.freezer_night_name(dish, cook["date"])
        new = _leftovers.freezer_night_name(dish, placement[cook_id])
        if old != new:
            conn.execute(
                "UPDATE meal_plan_entries SET freeform_meal = ? WHERE id = ? AND household_id = ? "
                "AND freeform_meal = ?",
                (new, r["id"], household_id(), old),
            )


def _refusal_sentence(snap: dict, entry_id: int, to_date: str, reason: str) -> str:
    """A picker reason as a sentence of its own, for a refusal said in a
    toast: "Friday: Nobody’s home."."""
    return f"{_weekday(to_date)}: {reason}."


def undo_meal_move(weekly_plan_id: int, move_id: str) -> dict:
    """
    Put back every row the move `move_id` re-dated. Only while each of them
    still carries that move's token and still sits where the move put it —
    a row moved again since means the Undo would move the wrong thing, so
    it refuses (ValueError) and changes nothing.
    """
    if not move_id:
        raise ValueError("Nothing to put back.")
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snap = _snapshot(weekly_plan_id, conn)
        placement: dict[int, str] = {}
        expected = None
        source_id = None
        restore_cuts: dict[str, str] = {}
        opened: list[str] = []
        for r in snap["rows"]:
            token = _derived(r).get(_weekly_plan.NIGHTS_MOVED_KEY) or {}
            if not isinstance(token, dict) or token.get(MOVE_TOKEN_KEY) != move_id:
                continue
            if token.get("to") != r["date"]:
                raise ValueError("That meal has moved again since, so there’s nothing to put back.")
            if (r["cooked_status"] or "") == "done":
                raise ValueError("That meal has been cooked since, so it stays where it is.")
            placement[r["id"]] = token.get("date")
            restore_cuts.update(token.get("prep_cuts") or {})
            for pos in token.get("opened") or []:
                if pos not in opened:
                    opened.append(pos)
            expected = token.get("n")
            source_id = token.get("source")
        if not placement or (expected and expected != len(placement)):
            raise ValueError("That move has changed since, so there’s nothing to put back.")
        # A day the move handed back as a question has to be taken back off
        # before the meal returns to it, or the day ends up holding two rows
        # for one slot — the DUPLICATES audit_plan_slots reports, and how a
        # night nobody is home ends up with groceries bought for it.
        #
        # Only this move's own open rows, and only while they are still
        # exactly what the move left. One the household has answered since
        # is a real decision, and putting the old meal back on top of it
        # would throw that away: it refuses and changes nothing, the same
        # answer this Undo already gives a row that moved again.
        take_off: list[int] = []
        for pos in opened:
            d, _, slot = pos.partition(":")
            row = _slot_row(snap, d, slot)
            if row is None:
                continue  # already gone — nothing to take off, nothing in the way
            derived = _derived(row)
            if row["slot_state"] != "open" or derived.get(MOVE_TOKEN_KEY) != move_id:
                raise ValueError(
                    f"{_weekday(d)}’s {slot} has been answered since, so there’s "
                    "nothing to put back.")
            take_off.append(row["id"])
        # Taken off BEFORE the meals are re-dated, and kept out of the
        # picture _redate_plan_rows judges: the row sits on exactly the
        # position a meal is about to come back to, so leaving it in would
        # have two rows claiming one (date, slot) in the week that
        # function's chain checks read.
        for row_id in take_off:
            conn.execute(
                "DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                (row_id, household_id()),
            )
        rows = [r for r in snap["rows"] if r["id"] not in set(take_off)]
        done = _weekly_plan._redate_plan_rows(
            conn, weekly_plan_id, rows, placement, token_for=None, move_prep_cuts=True,
        )
        if "refused" in done:
            conn.rollback()
            return {"status": "refused", "message": done["refused"]}
        # A prep cut the move carried earlier goes back to the day it was on.
        for task_id, was in restore_cuts.items():
            conn.execute(
                "UPDATE prep_tasks SET task_date = ? WHERE id = ? AND household_id = ?",
                (was, int(task_id), household_id()),
            )
        _resay_freezer_nights(conn, snap, placement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    # The same post-commit pass the move itself makes. Its answer is
    # carried rather than dropped: an undo is a move too, and keeping one
    # of _after_move's two callers deaf to it is the asymmetry that becomes
    # the next bug. No screen reads this one yet (shell.js reads
    # thaw_notes on the move's own toast only) — the chat tool and the
    # sheet's undo line are the follow-up on this card.
    still_owed = _after_move(weekly_plan_id, list(placement))
    by_id = {r["id"]: r for r in snap["rows"]}
    dates = sorted(set(placement.values()) | {by_id[i]["date"] for i in placement})
    src = by_id.get(source_id) if source_id in placement else None
    said = (f"{_cap(short_name(src['meal']))} was moved back to {_weekday(placement[source_id])}"
            if src else "Back as it was")
    out = {
        "status": "restored",
        "said": said,
        "moved": [{"entry_id": i, "meal": by_id[i]["meal"], "from": by_id[i]["date"], "to": d}
                  for i, d in placement.items()],
        "days": _weekly_plan._menu_days_for(weekly_plan_id, dates),
    }
    if still_owed:
        out["thaw_notes"] = still_owed
    return out


def _after_move(weekly_plan_id: int, moved_ids: list[int]) -> list[str]:
    """
    Everything the week owes once a move has committed, and only then —
    both halves open their own connections, and a nested get_conn inside
    the move's write transaction is the "database is locked" trap.

    The draft's snag lines, re-said for the week as it now stands — a
    meal that moved onto a short-on-time night gets its line, one that
    moved off loses it. Never fails the move: the move is written.

    And the week's fridge moves, recomputed against the days the meals are
    on now (defrost.resync_plan_thaws, 2026-10-01). _redate_plan_rows has
    already carried each EXISTING reminder by the same number of days,
    which is the same answer recomputing gives for its date; what this
    adds is a move that should now exist and did not, one that should not
    and does, and a quantity that changed because a chain's shape did.
    Returns whatever it now has to start today or earlier, for the caller
    to merge into its own thaw line.
    """
    try:
        from . import draft_flags as _draft_flags
        _draft_flags.refresh(weekly_plan_id, moved_ids)
    except Exception:
        logger.exception("Refreshing the draft's flags after a move failed (plan %s)", weekly_plan_id)
    from . import defrost as _defrost
    return _defrost.resync_plan_thaws(weekly_plan_id, moved_ids)
