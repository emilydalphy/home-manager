"""
Reading back the leftover chains a generated week left behind.

`repair_leftover_chains` (weekly_plan.py) is the WRITE side: it validates
every "this night eats an earlier night's batch" claim and, for the ones
that hold up, records the pairing on the SOURCE entry's derived_from_json
as `make_double_for` (a sorted list of "YYYY-MM-DD:slot" targets) plus a
`make_double_note`. The leftover entries keep their own
`derived_from.links_to` pointing back at the source.

This module is the READ side, and nothing in it writes. It exists because
four separate places need the same answer — "is this entry a cook that
feeds other nights, a night that only reheats, or an ordinary meal?" — and
each of them was previously free to treat a leftovers night as a second,
independent cook:

- the Cook view (cooker.get_cooker_view): showed the same dish twice, once
  per night, each "for 3";
- the grocery contribution (recipes._add_recipe_ingredients_to_grocery_list
  and approve_weekly_plan's caller): bought the ingredients twice and
  scaled neither to the batch;
- defrost (defrost._candidates_from_plan): a second "move the beef to the
  fridge" for a night nothing is cooked on;
- the prep-schedule context (agent.generate_prep_schedule): prep tasks for
  a reheat night, sized to a single night's portion.

Emily's rule, 2026-09-04, seeing the same dish on two nights of the Cook
view: "It should show the one night it's being cooked as 6 servings, make
a little note that this covers tonight + leftovers, and not have it on
another night for cooking."

A chain is only honoured here when BOTH halves agree — the leftover entry
points at the source AND the source lists that leftover in
`make_double_for`. That is deliberate rather than belt-and-braces. A plan
whose chains were never validated (nothing ran repair_leftover_chains over
it) has no `make_double_for` at all and so behaves exactly as it did
before this module existed; and a source still pointing at a slot that has
since been cleared or reopened (see _finish_week_slots on exactly that
hazard) contributes nothing to the batch, because that slot no longer
points back.
"""
from __future__ import annotations

import json
import re
from datetime import date

from ..db import get_conn
from ._shared import household_id
from . import attendance as _attendance


def _weekday(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A")


def _resolve(links_to: str, by_date_slot: dict, by_id: dict):
    """
    The row a leftover entry's links_to names, or None. Both accepted
    shapes are parsed by weekly_plan's own regexes rather than a second
    copy of them here — links_to has one agreed format and one place that
    defines it. Imported at call time, not import time: weekly_plan
    reaches back into this package's other modules, and the alias
    convention (see app/tools/__init__.py) is what keeps those cycles
    resolvable.
    """
    from . import weekly_plan as _weekly_plan

    m = _weekly_plan._LINKS_TO_DATE_SLOT_RE.match(links_to)
    if m:
        return by_date_slot.get((m.group(1), m.group(2)))
    m = _weekly_plan._LINKS_TO_ENTRY_ID_RE.match(links_to)
    if m:
        return by_id.get(int(m.group(1)))
    return None


# The portions a cook makes on purpose for the freezer, on the entry's own
# derived_from — {"servings": 3, ...}. Written by tonight.tonight_night_off
# (Emily, 2026-09-22: "the job of Pomona is to do all that planning work")
# when a night off moves a batch onto the night it was feeding, or takes a
# reheat night's portion out of a chain: the batch stays the SAME SIZE (the
# groceries for it are already bought or on the list), and whatever the
# called-off night would have eaten goes in the freezer instead. Read by
# every batch reader through batch_for_source / batch_for_entry, so the Cook
# card, the fridge-move quantities and any later grocery rescale all keep
# counting those portions rather than quietly cooking less.
FREEZER_EXTRA_KEY = "freezer_extra"

# Leftovers are eaten within this many days of the cook (Emily,
# 2026-09-23, the food-safety default). A later night either starts a
# batch of its own or eats a portion frozen on the cook night. One
# constant for every place that writes or checks a chain: the fold
# (meal_variety), the repeated-dates expansion (agent._expand_repeated_
# dates), the chain repair (weekly_plan.repair_leftover_chains) and the
# prep-day batches (cook_ahead.apply_prep_day_batches).
MAX_LEFTOVER_DAYS = 3

# On a night Pomona turned into leftovers because the household asked for
# fewer recipes than meals (Emily, 2026-09-23, "Decision E": "If I want 2
# types of lunches, but need 4 lunches, you should assume Im making double
# of each of the recipes. thats the batch cooking point"). The chain itself
# is the ordinary one (links_to / make_double_for); this only says Pomona
# made it, so the draft's opener can say so in one line.
BATCH_KEY = "batch_leftovers"

# On a night that eats a portion frozen on an earlier cook: the cook's
# "entry_id:N". The night itself is a freeform "Leftovers from the
# freezer — Monday’s Chili" row, which buys nothing (freeform never
# reaches the list) and reads as a reheat on every screen (the leftovers
# regex); the cook carries the portion as FREEZER_EXTRA_KEY, so the batch
# is bought and cooked big enough.
FROM_FREEZER_KEY = "from_freezer"


def days_apart(earlier: str, later: str) -> int:
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


def freezer_night_name(dish: str, cook_date: str) -> str:
    """"Leftovers from the freezer — Monday’s Chili"."""
    return f"Leftovers from the freezer — {_weekday(cook_date)}’s {dish}"


def freezer_servings(derived) -> int:
    """How many portions of this entry's cook are meant for the freezer —
    0 for nearly every entry. Takes the parsed derived_from or its JSON."""
    if isinstance(derived, str) or derived is None:
        try:
            derived = json.loads(derived or "{}")
        except (TypeError, ValueError):
            return 0
    extra = (derived or {}).get(FREEZER_EXTRA_KEY) or {}
    try:
        return max(0, int(extra.get("servings") or 0)) if isinstance(extra, dict) else 0
    except (TypeError, ValueError):
        return 0


def plan_leftover_chains(weekly_plan_id: int, conn=None) -> dict:
    """
    Every confirmed cook-once-eat-twice chain on this plan.

    Returns:
      {
        "sources":   {source_entry_id: {"entry_id", "date", "slot", "meal",
                                        "targets": [{"entry_id","date","slot"}, ...]}},
        "leftovers": {leftover_entry_id: {"entry_id", "date", "slot",
                                          "source": {"entry_id","date","slot","meal"}}},
        "freezer":   {entry_id: servings},   # only when any; FREEZER_EXTRA_KEY
      }

    `freezer` (2026-09-22) lists every planned cook carrying portions for
    the freezer, chained or not; a SOURCE also carries its own count as
    `freezer_servings`, which batch_for_source adds to the batch. A cook
    whose only extra is the freezer is NOT a source — it feeds no night,
    and every reader of `sources` means "feeds a night" — so a batch reader
    asks batch_for_entry, which covers both.

    Targets are sorted by (date, slot) so a source that feeds two nights
    always reads in the order the nights actually fall. Both maps are
    empty for a plan with no chains, which is most plans.

    `conn` is for the grocery ingest and is not part of the assistant-facing
    API: recipes._add_recipe_ingredients_for_entries asks this whether the
    meal it is buying for is a reheat, and when that runs inside
    swap_meal_in_plan's one write transaction the plan has to be read on
    that same connection — both so the read sees the rows the transaction
    has just written and deleted, and because a nested get_conn inside an
    open write transaction is the "database is locked" trap. Given a
    connection this only reads on it and never closes it; left unset it
    behaves exactly as before.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.recipe_id, mpe.freeform_meal,
               mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        """,
        (weekly_plan_id, household_id()),
    ).fetchall()
    if own_conn:
        conn.close()

    by_date_slot = {(r["date"], r["slot"]): r for r in rows}
    by_id = {r["id"]: r for r in rows}

    sources: dict[int, dict] = {}
    leftovers: dict[int, dict] = {}
    freezer: dict[int, int] = {}
    for r in rows:
        if r["slot_state"] != "planned":
            continue
        derived = json.loads(r["derived_from_json"] or "{}")
        extra = freezer_servings(derived)
        if extra and (r["recipe_id"] or r["freeform_meal"]):
            freezer[r["id"]] = extra
        links_to = (derived.get("links_to") or "").strip()
        if not links_to:
            continue
        source = _resolve(links_to, by_date_slot, by_id)
        if source is None or source["id"] == r["id"]:
            continue
        if source["slot_state"] != "planned" or not (source["recipe_id"] or source["freeform_meal"]):
            continue
        if source["date"] >= r["date"]:
            continue
        # The source has to name this night back. Without the agreement
        # check a half-written chain would still scale a batch up — see
        # the module docstring.
        source_derived = json.loads(source["derived_from_json"] or "{}")
        confirmed = source_derived.get("make_double_for") or []
        if isinstance(confirmed, str):  # tolerate the pre-fix scalar shape
            confirmed = [confirmed]
        if f"{r['date']}:{r['slot']}" not in confirmed:
            continue

        # Was this pairing the household's own "cook these days now" pick
        # (cook_ahead.set_cook_ahead) rather than the planner's leftovers?
        # The chain itself is identical either way — this only decides
        # which words the screens use for it, so it is read here rather
        # than making every caller open derived_from a second time.
        chosen_ahead = bool(derived.get("cook_ahead"))

        entry = sources.setdefault(source["id"], {
            "entry_id": source["id"], "date": source["date"], "slot": source["slot"],
            "meal": source["meal"], "note": source_derived.get("make_double_note") or "",
            "cook_ahead": False, "targets": [],
        })
        # Only when there is any: the shape every other plan has always
        # had is left exactly as it was (callers read it with .get).
        if freezer_servings(source_derived):
            entry["freezer_servings"] = freezer_servings(source_derived)
        entry["cook_ahead"] = entry["cook_ahead"] or chosen_ahead
        entry["targets"].append({
            "entry_id": r["id"], "date": r["date"], "slot": r["slot"], "cook_ahead": chosen_ahead,
        })
        leftovers[r["id"]] = {
            "entry_id": r["id"], "date": r["date"], "slot": r["slot"],
            "cook_ahead": chosen_ahead,
            "source": {
                "entry_id": source["id"], "date": source["date"],
                "slot": source["slot"], "meal": source["meal"],
            },
        }

    for entry in sources.values():
        entry["targets"].sort(key=lambda t: (t["date"], t["slot"]))
    out = {"sources": sources, "leftovers": leftovers}
    if freezer:
        # Present only when some cook carries portions for the freezer —
        # the two-key shape is what every plan without one has always had.
        out["freezer"] = freezer
    return out


def eaters_at(date_str: str, slot: str, conn=None) -> int:
    """
    How many people this one meal actually feeds — attendance's headcount
    (members present plus guests). get_slot_attendance already falls back
    to "everyone's home" when no attendance was ever recorded for the
    slot, so an ordinary week answers with the household's own size. 0
    only for a household with no members yet, or a slot everyone is away
    for; callers treat 0 as "don't scale" rather than "cook nothing".
    """
    try:
        return int(_attendance.get_slot_attendance(date_str, slot, conn=conn)["headcount"] or 0)
    except Exception:
        return 0


def batch_for_source(source: dict, conn=None) -> dict:
    """
    What one source night actually has to cook: its own table plus every
    night eating its leftovers.

    Returns {"servings", "cook_eaters", "targets": [{date, slot, eaters}]}.
    `servings` is 0 when nothing could be counted (no members on record) —
    the signal to leave the recipe's own quantities alone rather than
    scale to nothing.

    `conn` rides through to the attendance reads for the grocery ingest —
    see plan_leftover_chains.
    """
    cook_eaters = eaters_at(source["date"], source["slot"], conn=conn)
    targets = [
        {**t, "eaters": eaters_at(t["date"], t["slot"], conn=conn)}
        for t in source["targets"]
    ]
    # Portions for the freezer (FREEZER_EXTRA_KEY) are part of the batch:
    # they were bought for, and the cook makes them — they just aren't
    # eaten on a night of this plan.
    extra = int(source.get("freezer_servings") or 0)
    total = cook_eaters + sum(t["eaters"] for t in targets) + extra
    out = {"servings": total, "cook_eaters": cook_eaters, "targets": targets}
    if extra:
        out["freezer"] = extra
    return out


def batch_for_entry(entry_id: int, chains: dict, conn=None) -> dict | None:
    """
    The batch one cook has to make, or None when it is an ordinary cook for
    its own table (or a reheat, which cooks nothing).

    A chain SOURCE answers batch_for_source. A cook that feeds no night but
    carries portions for the freezer (chains["freezer"], 2026-09-22) is a
    batch too — its own table plus the freezer's share — and this is the
    one place that says so, so the Cook card, the fridge-move quantities
    and the grocery ingest cannot disagree about how much it makes.
    """
    source = (chains.get("sources") or {}).get(entry_id)
    if source:
        return batch_for_source(source, conn=conn)
    extra = int((chains.get("freezer") or {}).get(entry_id) or 0)
    if not extra:
        return None
    own_conn = conn is None
    c = get_conn() if own_conn else conn
    try:
        row = c.execute(
            "SELECT date, slot FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()
    finally:
        if own_conn:
            c.close()
    if row is None:
        return None
    cook_eaters = eaters_at(row["date"], row["slot"], conn=conn)
    return {
        "servings": cook_eaters + extra, "cook_eaters": cook_eaters,
        "targets": [], "freezer": extra,
    }


def _join_days(days: list[str]) -> str:
    if len(days) == 1:
        return days[0]
    if len(days) == 2:
        return f"{days[0]} and {days[1]}"
    return ", ".join(days[:-1]) + f", and {days[-1]}"


def covers_note(source: dict, servings: int, today: str | None = None) -> str:
    """
    The little note that goes under the "for 6" chip on the one night this
    is cooked: "Cooking for 6 — covers tonight and leftovers on Thursday."

    Names the cook night as "tonight" only when it really is today, and as
    the weekday otherwise, so the same card is honest read on Sunday and
    read on the night itself (DESIGN_SYSTEM.md §8: time the way a person
    would say it). The leftover nights are always named — "and leftovers"
    on its own would leave the person counting.
    """
    today = today or date.today().isoformat()
    cook_label = "tonight" if source["date"] == today else _weekday(source["date"])
    # Portions for the freezer are said, not hidden inside the number: a
    # cook told "for 6" at a table of 3 with no reason given halves it.
    extra = int(source.get("freezer_servings") or 0)
    frozen = f", plus {extra} for the freezer" if extra else ""
    if not source.get("targets"):
        # A cook whose only extra is the freezer (batch_for_entry).
        return f"Cooking for {servings} — covers {cook_label}{frozen}."
    days = _join_days([_weekday(t["date"]) for t in source["targets"]])
    return f"Cooking for {servings} — covers {cook_label} and leftovers on {days}{frozen}."


def cook_ahead_note(source: dict, servings: int) -> str:
    """
    covers_note's wording for a batch the household chose to cook ahead
    (cook_ahead.set_cook_ahead): "Cooking for 6 — enough for Monday,
    Wednesday, and Friday."

    Same sentence, different truth. covers_note says "covers tonight and
    leftovers on Thursday" because that is what a planner-written chain
    is: one dinner, then what is left of it. A household that ticked three
    mornings is not making leftovers, it is making three mornings' worth
    at once — so this names every day the batch is for, the cook day
    included, and never says "leftovers" about portions nobody has eaten
    yet.
    """
    days = _join_days([_weekday(source["date"])] + [_weekday(t["date"]) for t in source["targets"]])
    extra = int(source.get("freezer_servings") or 0)
    frozen = f", plus {extra} for the freezer" if extra else ""
    return f"Cooking for {servings} — enough for {days}{frozen}."


def leftovers_headline(source_meal: str, source_date: str) -> str:
    """"Leftovers — Wednesday's Korean Beef Bulgogi Lettuce Wraps"."""
    return f"Leftovers — {_weekday(source_date)}’s {source_meal}"


def made_ahead_headline(source_meal: str, source_date: str) -> str:
    """"Made ahead — Monday's Egg White Bites" — leftovers_headline for a
    day the household deliberately cooked the portions for in advance.
    Same shape, one honest word different: a portion set aside on purpose
    on Monday morning isn't leftovers by Wednesday, it's what Wednesday
    was always going to be."""
    return f"Made ahead — {_weekday(source_date)}’s {source_meal}"


def served_cold(recipe: dict | None, slot: str | None) -> bool:
    """
    Is a made-ahead portion eaten as it comes out of the fridge, with no
    reheating? (Emily, 2026-09-25, on "Made ahead — Wednesday's Cucumber
    Slices with Tzatziki" wearing a Reheat pill: "The snacks don't need to
    be reheated they're cold. Just cause they're made ahead doesn't mean
    they need to be reheated.")

    Recipes carry no serve-temperature field, so this reads the best
    signals already on the row, conservatively — a dish only counts as cold
    on positive evidence, and anything unclear keeps "Reheat":

      - The recipe's own notes talk about reheating (reheat_note) -> hot.
        That is the household's or the recipe's own word, and it wins.
      - A snack -> cold. A made-ahead snack is grabbed from the fridge;
        even a baked one (a muffin, an oat bar) is eaten as it is.
      - A method that exists and never applies heat -> cold. The same
        heat vocabulary plan_quality uses to tell a cold plate from a
        cooked one (_APPLIES_HEAT_WORDS): no oven, pan, pot, boil, roast...
      - cook_time_minutes recorded as exactly 0 -> cold (a no-cook
        recipe). None means "not known", not zero.
    """
    if reheat_note(recipe):
        return False
    if (slot or "").strip().lower() == "snack":
        return True
    if not recipe:
        return False
    steps = [s for s in (recipe.get("instructions") or []) if str(s).strip()]
    if steps:
        # Imported here, not at module top: plan_quality imports half the
        # tools package, and leftovers is imported early by most of it.
        from .plan_quality import _APPLIES_HEAT_WORDS
        words = set(re.findall(r"[a-zé]+", " ".join(str(s) for s in steps).lower()))
        if not (words & _APPLIES_HEAT_WORDS):
            return True
    return recipe.get("cook_time_minutes") == 0


def reheat_note(recipe: dict | None) -> str:
    """
    A recipe's own reheating advice, if it happens to carry any.

    There is no reheat field on recipes (see schema.sql) and adding one is
    a bigger change than this needs, so this reads the freeform `notes`
    and returns the first sentence that is actually about reheating.
    Returns "" for the overwhelming majority of recipes, and the reheat
    card simply shows the source link instead — which is the honest
    outcome, not a degraded one.
    """
    notes = ((recipe or {}).get("notes") or "").strip()
    if not notes:
        return ""
    for sentence in [s.strip() for s in notes.replace("\n", ". ").split(".") if s.strip()]:
        low = sentence.lower()
        if "reheat" in low or "warm through" in low or "warms up" in low:
            return sentence if sentence.endswith(("!", "?")) else sentence + "."
    return ""
