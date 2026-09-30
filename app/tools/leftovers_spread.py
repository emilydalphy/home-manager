"""
Fewer dinner dishes than nights: each dish covers its fair share of the
week, and a weekend lunch eats last night's dinner.

Emily, 2026-09-29, answering the two calls left on the Loop Board card
"The draft doesn't make good use of leftovers" (her week: Dinners 3 over
six nights Mon–Sat, lunches prepped Sunday and Tuesday, Friday's lunch
"Leftovers from dinner", Saturday's lunch not on that screen):

  1. A weekend lunch comes from the previous night's dinner leftovers, not
     a portion from the freezer — when food safety (leftovers.MAX_LEFTOVER_
     DAYS of the cook) and "at most two meals in a row of one dish" allow
     it. Friday's dinner then Saturday's lunch is two in a row, so
     Saturday's dinner is a different dish.
  2. With N dinner dishes over M nights, each dish covers about M/N nights:
     3 dinners over 6 nights is each dish cooked once and eaten twice. When
     M/N isn't whole the dishes differ by at most one night.

Why a pass of its own. The count fold (meal_variety._plan_batches) decides
a week night by night from whatever the model sent: a dish's later night
eats its own cook within three days or is cooked again, a freed night eats
the smallest batch nearby. Nothing in it aims at an even share, so the
model's "Stew, Tacos, Curry, Stew, Tacos, Curry" came out as four cooks
(Stew cooked Monday alone and again Thursday), and "Stew ×3, Tacos, Curry,
Pasta" as Curry on three nights and Tacos on one. And a lunch the count
fold handles only eats a dinner whose dish is already one of the week's
lunches (meal_variety._outside_cooks), so a Saturday lunch fell to "Leftovers
from the freezer — Monday's soup" with Friday's dinner right there.

What this does, once the week's dishes are settled (agent._finish_week_slots,
right after the final count guard): it keeps the week's dinner DISHES
exactly as they are and only decides which night holds which, and which
nights are cooks. Every layout of the dishes over the nights is tried (a
week is at most eight nights, so this is small), keeping the rules every
other pass keeps:

  - each dish on floor(M/N) or ceil(M/N) nights (relaxed only when no even
    layout exists, and then as even as possible);
  - a dish's later nights eat its cook within MAX_LEFTOVER_DAYS, else it is
    cooked again (counted, and avoided);
  - a cook fits its night's time cap (a rush night reheats instead);
  - a night tagged Leftovers is a reheat; a night they asked for by name
    keeps its dish;
  - a lunch that eats a dinner (a "leftovers from dinner" day, a prepped
    lunch whose batch is the prep day's dinner) follows the dinner it eats
    and stays within three days of that dinner's cook;
  - no dish on more than two lunches and dinners in a row.

The layout that is even (no dish more than one night over another) wins,
then the one with the fewest cooks, then the most even, then the one
where the most weekend lunches eat last night's dinner, then the one that
moves the fewest nights from what the week already had. It is written only
when it is strictly better than the week as it stands, so a week that is
already right is not touched at all.

Only for a household that set its "Dinners" number (a week with no number
is never batch cooked, so there is nothing to spread), and a weekend lunch
is pointed at last night's dinner only when it was going to eat a portion
from the freezer — a weekend lunch that is a cook of its own may be the
week's other lunch dish, and the lunch count keeps its number.

Stands down (the week is left exactly as it is) for anything it cannot
re-lay safely: an approved week, a night already cooked, a dinner cooking
extra portions for the freezer, a night with two dinner rows, a chain it
doesn't recognise (a dinner feeding a breakfast, a lunch feeding a dinner).
A night eating a portion from the freezer and a night nobody is home are
not nights to lay: they hold their dish (or nothing) and only count for
the two-in-a-row rule.

Writes go through the same doors the count fold uses: _replace_slot_entries
for a night whose dish changes (with its non-chain derived_from keys, such
as a prep day's prep_date, carried over), and meal_variety._write_cook_sides
for each cook's make_double_for. Never raises.
"""
from __future__ import annotations

import itertools
import json
import logging
import math
from datetime import date

from ..db import get_conn
from ._shared import household_id
from . import leftovers as _leftovers

logger = logging.getLogger("home_manager")

# The dinner nights a layout can be searched over. A period is at most
# eight days; this is a guard, not a limit anyone reaches.
MAX_NIGHTS = 8

# derived_from keys that describe a chain — rebuilt here, never carried
# over when a night changes dish or role.
_CHAIN_KEYS = ("links_to", "make_double_for", "make_double_note", _leftovers.BATCH_KEY,
               _leftovers.FROM_FREEZER_KEY)

# How many layouts the search scores at most. A six-night week with three
# dishes is 90 strict layouts; this only bounds a long week with many
# dishes (the best found so far is used, and only if it beats the week).
MAX_SCORED = 4000

# A night whose dish the household chose — it keeps that dish whatever the
# layout (its role, cook or leftovers, may still change, as the count fold
# already does for a dish they asked for: cooked once).
_THEIR_DISH_KEYS = ("freeform", "holiday_dish", "brought_over", "cuisine_repick")


def _derived(row) -> dict:
    try:
        value = json.loads(row["derived_from_json"] or "{}") or {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _load(plan_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.food_groups_json,
                   mpe.derived_from_json, mpe.reasoning, COALESCE(r.name, mpe.freeform_meal) AS meal,
                   r.prep_time_minutes, r.cook_time_minutes
            FROM meal_plan_entries mpe
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
              AND mpe.slot IN ('lunch', 'dinner')
            ORDER BY mpe.date ASC, mpe.id ASC
            """,
            (plan_id, household_id()),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        row = dict(r)
        row["derived"] = _derived(r)
        out.append(row)
    return out


def _is_weekend(iso: str) -> bool:
    return date.fromisoformat(iso).weekday() >= 5


def _evening_before(iso: str) -> str:
    return date.fromordinal(date.fromisoformat(iso).toordinal() - 1).isoformat()


def _approved(plan_id: int) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
                           (plan_id, household_id())).fetchone()
    finally:
        conn.close()
    return bool(row) and row["status"] == "approved"


class _StandDown(Exception):
    """The week has something this pass doesn't re-lay; leave it as it is."""


def _read_week(plan_id: int, intake: dict | None, kinds: dict[str, str]) -> dict:
    """Everything the search needs, read off the plan as it stands.
    `kinds` is the week's weekday-lunches answer by date."""
    from . import weekday_lunches as _weekday_lunches

    answered = set(kinds)
    prep_days = ((intake or {}).get("weekday_lunches") or {}).get("prep_days") or []
    rows = _load(plan_id)
    chains = _leftovers.plan_leftover_chains(plan_id)
    by_id = {r["id"]: r for r in rows}
    tags = (intake or {}).get("night_tags") or {}

    def dish_of(row) -> str:
        reheat = chains["leftovers"].get(row["id"])
        if reheat:
            return _leftovers.dish_identity(reheat["source"]["meal"])
        return _leftovers.dish_identity(_leftovers.frozen_portion_on(row["derived"]) or row["meal"])

    # ---- the dinner nights to lay ----
    dinners_by_date: dict[str, list[dict]] = {}
    for r in rows:
        if r["slot"] == "dinner" and r["slot_state"] == "planned" and (r["meal"] or "").strip():
            dinners_by_date.setdefault(r["date"], []).append(r)
    nights: list[dict] = []
    dishes: dict[str, dict] = {}
    for d in sorted(dinners_by_date):
        found = dinners_by_date[d]
        if len(found) != 1:
            raise _StandDown(f"{d} has {len(found)} dinners")
        r = found[0]
        derived = r["derived"]
        if (r["cooked_status"] or "") == "done":
            raise _StandDown(f"{d} dinner is cooked")
        if _leftovers.freezer_servings(derived):
            raise _StandDown(f"{d} dinner cooks for the freezer")
        if _leftovers.frozen_portion_on(derived) or derived.get("freezer_portion"):
            continue  # eats a portion from the freezer: not a night to lay
        reheat = chains["leftovers"].get(r["id"])
        if reheat and (reheat["source"]["slot"] != "dinner" or reheat["source"]["entry_id"] not in by_id):
            raise _StandDown(f"{d} dinner reheats a {reheat['source']['slot']}")
        for t in (chains["sources"].get(r["id"]) or {}).get("targets") or []:
            if t["slot"] not in ("lunch", "dinner"):
                raise _StandDown(f"{d} dinner feeds a {t['slot']}")
        key = dish_of(r)
        if not key:
            raise _StandDown(f"{d} dinner has no dish")
        dish = dishes.setdefault(key, {"key": key, "name": None, "minutes": None, "food_groups": None,
                                       "reasoning": ""})
        if not reheat:
            dish["name"] = dish["name"] or r["meal"]
            dish["reasoning"] = dish["reasoning"] or (r.get("reasoning") or "")
            if dish["minutes"] is None and (r.get("prep_time_minutes") or r.get("cook_time_minutes")):
                dish["minutes"] = int(r.get("prep_time_minutes") or 0) + int(r.get("cook_time_minutes") or 0)
            if dish["food_groups"] is None:
                try:
                    dish["food_groups"] = json.loads(r["food_groups_json"] or "[]") or None
                except (TypeError, ValueError):
                    pass
        nights.append({
            "row": r, "id": r["id"], "date": d, "dish": key,
            "cook": not reheat,
            "pinned": any(derived.get(k) for k in _THEIR_DISH_KEYS),
            "reheat_only": "left" in (tags.get(d) or []),
            "must_cook": False,
        })
    for dish in dishes.values():
        if dish["name"] is None:
            # Every night of it is a reheat of a cook outside the nights laid.
            raise _StandDown(f"{dish['key']} has no cook among the nights")
    night_by_id = {n["id"]: n for n in nights}
    night_by_date = {n["date"]: n for n in nights}

    # ---- lunches that eat a dinner, and weekend lunches that could ----
    riders: list[dict] = []
    weekend: list[dict] = []
    lunch_rows = [r for r in rows if r["slot"] == "lunch"]
    for r in lunch_rows:
        if r["slot_state"] != "planned" or not (r["meal"] or "").strip():
            continue
        reheat = chains["leftovers"].get(r["id"])
        if reheat and reheat["source"]["slot"] == "dinner":
            if reheat["source"]["entry_id"] not in night_by_id:
                raise _StandDown(f"{r['date']} lunch eats a dinner outside the nights laid")
            # The dinner this lunch follows: last night's, for a day they
            # said is "leftovers from dinner"; the prep day's, for a prepped
            # lunch whose batch is that dinner (a prep-day cook, which then
            # stays a cook and keeps its dish — its prep_date is the batch);
            # otherwise the cook it eats now.
            anchor = night_by_id[reheat["source"]["entry_id"]]["date"]
            evening = _evening_before(r["date"])
            prep_date = _weekday_lunches.prep_date_for(r["date"], prep_days) if prep_days else None
            if kinds.get(r["date"]) == "leftovers" and evening in night_by_date:
                anchor = evening
            elif kinds.get(r["date"]) == "prepped" and prep_date in night_by_date:
                anchor = prep_date
            if anchor == prep_date or night_by_date[anchor]["row"]["derived"].get("prep_date"):
                night_by_date[anchor]["must_cook"] = True
                night_by_date[anchor]["pinned"] = True
            riders.append({"row": r, "id": r["id"], "date": r["date"], "anchor": anchor,
                           "optional": False})
            continue
        if r["id"] in chains["sources"]:
            # A lunch cook that feeds later lunches: it is theirs to keep.
            for t in chains["sources"][r["id"]]["targets"]:
                if t["slot"] == "dinner":
                    raise _StandDown(f"{r['date']} lunch feeds a dinner")
            continue
        # A weekend lunch (Emily's decision 1): not on the weekday-lunches
        # screen, not theirs, eating a portion the week's own cook froze for
        # it (the count fold's answer when no lunch batch was in reach). A
        # lunch that is a cook of its own is left alone — it may be the
        # week's other lunch dish, and the lunch count keeps its number.
        derived = r["derived"]
        evening = _evening_before(r["date"])
        if (_is_weekend(r["date"]) and r["date"] not in answered and evening in night_by_date
                and (r["cooked_status"] or "") != "done"
                and not any(derived.get(k) for k in _THEIR_DISH_KEYS + ("freezer_portion",))
                and isinstance(derived.get(_leftovers.FROM_FREEZER_KEY), dict)):
            weekend.append({"row": r, "id": r["id"], "date": r["date"], "anchor": evening,
                            "optional": True})
    return {"rows": rows, "chains": chains, "nights": nights, "dishes": dishes,
            "riders": riders, "weekend": weekend, "run_keys": _leftovers.run_keys(plan_id)}


def _groups(assign: list[str], nights: list[dict]) -> list[int]:
    """For each night, the index of the night whose cook it eats (itself
    when it is a cook): a dish's later night eats its latest cook within
    MAX_LEFTOVER_DAYS, else is cooked again."""
    cook_of: list[int] = []
    last_cook: dict[str, int] = {}
    for i, n in enumerate(nights):
        c = last_cook.get(assign[i])
        if c is not None and 1 <= _leftovers.days_apart(nights[c]["date"], n["date"]) <= _leftovers.MAX_LEFTOVER_DAYS:
            cook_of.append(c)
        else:
            cook_of.append(i)
            last_cook[assign[i]] = i
    return cook_of


def _score(week: dict, assign: list[str], on: tuple[bool, ...], caps: dict | None,
           actual_cooks: bool = False):
    """(rules broken, spread beyond one night, cooks, spread, weekend lunches
    not eating last night's dinner, nights moved) for a layout, or None when it breaks a rule. With
    `actual_cooks` it scores the week as it stands — its real chains, and
    a broken rule counted rather than refused."""
    nights, dishes = week["nights"], week["dishes"]
    index = {n["date"]: i for i, n in enumerate(nights)}
    if actual_cooks:
        cook_of = []
        by_id = {n["id"]: i for i, n in enumerate(nights)}
        for i, n in enumerate(nights):
            reheat = week["chains"]["leftovers"].get(n["id"])
            cook_of.append(by_id.get(reheat["source"]["entry_id"], i) if reheat else i)
    else:
        cook_of = _groups(assign, nights)
    # The week as it stands is scored too, and may already break a rule
    # (a cook over its night's cap, a Leftovers night cooked): each break is
    # counted there, ahead of everything else, so a layout that mends it wins.
    broken = 0
    for i, n in enumerate(nights):
        if (n["reheat_only"] and cook_of[i] == i) or (n["must_cook"] and cook_of[i] != i):
            broken += 1
        if cook_of[i] == i and caps is not None:
            cap = caps.get((n["date"], "dinner"), caps.get(n["date"]))
            minutes = dishes[assign[i]]["minutes"]
            if cap is not None and minutes is not None and minutes > cap:
                if not actual_cooks:
                    return None
                # A cook the week already has over its cap was left there
                # on purpose (cap_enforce leaves a batch's cook standing —
                # an open call of Emily's, tests/test_rush_cap_enforced.py),
                # so it is not a break this pass mends.
    if broken and not actual_cooks:
        return None
    keys = dict(week["run_keys"])
    scope: set = set()
    for i, n in enumerate(nights):
        keys[(n["date"], "dinner")] = assign[i]
        scope.add((n["date"], "dinner"))
    eaters = list(week["riders"]) + [w for w, yes in zip(week["weekend"], on) if yes]
    for r in eaters:
        a = index[r["anchor"]]
        cook = nights[cook_of[a]]
        if _leftovers.days_apart(cook["date"], r["date"]) > _leftovers.MAX_LEFTOVER_DAYS:
            if not actual_cooks:
                return None
            broken += 1
        keys[(r["date"], "lunch")] = assign[a]
        scope.add((r["date"], "lunch"))
    for w in week["weekend"]:
        scope.add((w["date"], "lunch"))
    for run in _leftovers.long_runs(keys):
        if scope.intersection(run):
            if not actual_cooks:
                return None
            broken += 1
    counts = [assign.count(k) for k in dishes]
    cooks = sum(1 for i, c in enumerate(cook_of) if c == i)
    moved = sum(1 for i, n in enumerate(nights) if assign[i] != n["dish"])
    spread = max(counts) - min(counts)
    # Even first when even can't be exact (Emily: "differ by at most one
    # night"), then fewest cooks, then the rest.
    return (broken, max(0, spread - 1), cooks, spread, sum(1 for yes in on if not yes), moved)


def _layouts(week: dict, strict: bool):
    """Every assignment of the week's dishes to its nights: every dish kept,
    a pinned night keeping its dish, and — strict — each dish on floor(M/N)
    or ceil(M/N) nights."""
    nights, keys = week["nights"], list(week["dishes"])
    m, k = len(nights), len(keys)
    low, high = (m // k, math.ceil(m / k)) if strict else (1, m - k + 1)
    counts = {key: 0 for key in keys}
    assign: list[str] = []

    def walk(i):
        if i == m:
            if all(low <= c <= high for c in counts.values()):
                yield list(assign)
            return
        left = m - i
        need = sum(max(0, low - c) for c in counts.values())
        if need > left:
            return
        options = [nights[i]["dish"]] if nights[i]["pinned"] else keys
        for key in options:
            if counts[key] >= high:
                continue
            counts[key] += 1
            assign.append(key)
            yield from walk(i + 1)
            assign.pop()
            counts[key] -= 1

    yield from walk(0)


def best_layout(week: dict, caps: dict | None) -> dict | None:
    """The best layout, or None when none keeps every rule."""
    best = None
    choices = list(itertools.product((True, False), repeat=len(week["weekend"])))
    scored = 0
    for strict in (True, False):
        for assign in _layouts(week, strict):
            for on in choices:
                scored += 1
                score = _score(week, assign, on, caps)
                if score is not None and (best is None or score < best[0]):
                    best = (score, assign, on)
            if scored >= MAX_SCORED:
                break
        if best is not None or scored >= MAX_SCORED:
            break
    if best is None:
        return None
    return {"score": best[0], "assign": best[1], "weekend_on": best[2]}


def _carry(derived: dict) -> dict:
    return {k: v for k, v in (derived or {}).items() if k not in _CHAIN_KEYS}


def _drop_freezer_portion(conn, cook_id: int, night_key: str, eaters: int) -> None:
    """The cook no longer freezes a portion for `night_key` (that night now
    eats last night's dinner instead) — weekly_plan.freeze_a_portion undone."""
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                       (cook_id, household_id())).fetchone()
    if row is None:
        return
    derived = _derived(row)
    extra = dict(derived.get(_leftovers.FREEZER_EXTRA_KEY) or {})
    if night_key not in (extra.get("for") or []):
        return
    extra["for"] = [f for f in extra["for"] if f != night_key]
    extra["servings"] = max(0, _leftovers.freezer_servings(derived) - max(0, eaters))
    if not extra["for"] or not extra["servings"]:
        derived.pop(_leftovers.FREEZER_EXTRA_KEY, None)
    else:
        derived[_leftovers.FREEZER_EXTRA_KEY] = extra
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                 (json.dumps(derived), cook_id, household_id()))


def _write(plan_id: int, week: dict, layout: dict) -> dict:
    """Write a layout in two phases. First every row whose DISH changes is
    replaced (the swap's own door, one transaction each, chain keys left
    off). Then every chain — each night's role, each lunch's link, each
    cook's make_double_for, a weekend lunch's freezer portion coming off its
    cook — is written in ONE transaction, so a failure there leaves the
    chains exactly as they were rather than half-written."""
    from . import weekly_plan as _weekly_plan

    nights, dishes = week["nights"], week["dishes"]
    assign = layout["assign"]
    cook_of = _groups(assign, nights)
    eaters = list(week["riders"]) + [w for w, yes in zip(week["weekend"], layout["weekend_on"]) if yes]
    out = {"moved": [], "weekend": [], "batched": []}
    index = {n["date"]: i for i, n in enumerate(nights)}

    # Phase 1: the dishes. Date order, dinners then the lunches following them.
    ids: list[int] = []
    for i, n in enumerate(nights):
        dish = dishes[assign[i]]
        if assign[i] == n["dish"]:
            ids.append(n["id"])
            continue
        row = _weekly_plan._replace_slot_entries(
            plan_id, [n["id"]], n["date"], "dinner", dish["name"], food_groups=dish["food_groups"],
            reasoning=dish["reasoning"] if cook_of[i] == i else "",
            derived_from=dict(_carry(n["row"]["derived"]), replaced=n["row"]["meal"]),
        )
        ids.append(row.get("entry_id"))
        out["moved"].append({"date": n["date"], "from": n["row"]["meal"], "to": dish["name"]})
    eater_ids: list[int] = []
    for r in eaters:
        a = index[r["anchor"]]
        dish = dishes[assign[a]]
        if r["optional"] or _leftovers.dish_identity(r["row"]["meal"]) != assign[a]:
            row = _weekly_plan._replace_slot_entries(
                plan_id, [r["id"]], r["date"], "lunch", dish["name"], food_groups=dish["food_groups"],
                reasoning="", derived_from=dict(_carry(r["row"]["derived"]), replaced=r["row"]["meal"]),
            )
            eater_ids.append(row.get("entry_id"))
            if r["optional"]:
                out["weekend"].append({"date": r["date"], "from": r["row"]["meal"], "to": dish["name"]})
        else:
            eater_ids.append(r["id"])

    # Phase 2: the chains, in one transaction.
    targets: dict[int, list[str]] = {}
    links: dict[int, int] = {}
    for i, n in enumerate(nights):
        if cook_of[i] != i:
            links[ids[i]] = ids[cook_of[i]]
            targets.setdefault(ids[cook_of[i]], []).append(f"{n['date']}:dinner")
    for r, eid in zip(eaters, eater_ids):
        cook_id = ids[cook_of[index[r["anchor"]]]]
        links[eid] = cook_id
        targets.setdefault(cook_id, []).append(f"{r['date']}:lunch")
    batch_ids = {ids[i] for i in range(len(nights)) if cook_of[i] != i}
    batch_ids |= {eid for r, eid in zip(eaters, eater_ids) if r["optional"]}
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for w in eaters:
            frozen = w["row"]["derived"].get(_leftovers.FROM_FREEZER_KEY) if w["optional"] else None
            cook_id = _leftovers_ref(frozen.get("cook")) if isinstance(frozen, dict) else None
            if cook_id is not None:
                _drop_freezer_portion(conn, cook_id, f"{w['date']}:lunch",
                                      _leftovers.eaters_at(w["date"], "lunch", conn=conn))
        for eid in list(ids) + eater_ids:
            row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                               (eid, household_id())).fetchone()
            if row is None:
                raise RuntimeError(f"entry {eid} went away while the week was being re-laid")
            derived = _carry(_derived(row))
            if eid in links:
                derived["links_to"] = f"entry_id:{links[eid]}"
            if eid in batch_ids:
                derived[_leftovers.BATCH_KEY] = True
            if targets.get(eid):
                merged = sorted(set(targets[eid]))
                derived["make_double_for"] = merged
                derived["make_double_note"] = _weekly_plan._make_double_note_text(merged)
                out["batched"].append({"cook": eid, "covers": merged})
            conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                         (json.dumps(derived), eid, household_id()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return out


def _leftovers_ref(ref) -> int | None:
    text = str(ref or "")
    if text.startswith("entry_id:"):
        try:
            return int(text.split(":", 1)[1])
        except ValueError:
            return None
    return None


def spread_dinners(plan_id: int, intake: dict | None, caps: dict | None = None,
                   target: int | None = None) -> dict:
    """
    Re-lay the week's dinners so each dish covers its share of the nights,
    and point a weekend lunch at last night's dinner (see the module
    docstring). `caps` is {(date, "dinner"): minutes or None} for a dish
    COOKED on the night. `target` is the household's own "Dinners" number
    (only a number they set — a week with no number is never batch cooked,
    so there is nothing to spread). Returns {"moved", "weekend", "batched", "skipped"};
    never raises.
    """
    from . import weekday_lunches as _weekday_lunches

    out: dict = {"moved": [], "weekend": [], "batched": [], "skipped": None}
    try:
        if not target:
            out["skipped"] = "no dinner number set"
            return out
        if _approved(plan_id):
            out["skipped"] = "approved"
            return out
        week = _read_week(plan_id, intake, _weekday_lunches.kinds_by_date(intake))
        nights, dishes = week["nights"], week["dishes"]
        if not dishes or len(nights) > MAX_NIGHTS:
            out["skipped"] = "nothing to lay"
            return out
        if len(nights) <= len(dishes) and not week["weekend"]:
            out["skipped"] = "a dish a night"
            return out
        layout = best_layout(week, caps)
        if layout is None:
            out["skipped"] = "no layout keeps every rule"
            return out
        current = [n["dish"] for n in nights]
        now = _score(week, current, tuple(False for _ in week["weekend"]), caps, actual_cooks=True)
        if layout["score"] >= now:
            out["skipped"] = "already as good"
            return out
        written = _write(plan_id, week, layout)
        out.update(written)
        logger.info("Plan %s dinners re-laid %s -> %s; weekend lunches %s", plan_id, current,
                    layout["assign"], written["weekend"])
    except _StandDown as why:
        out["skipped"] = str(why)
    except Exception:
        # Phase 2 of _write is one transaction; a failure in phase 1 can
        # leave some nights with their new dish and no chain yet (each a
        # plain cook) — never a half-written chain.
        logger.exception("Spreading the dinners of plan %s failed part-way; chains are whole, "
                         "some nights may be plain cooks", plan_id)
        out["skipped"] = "error"
    return out
