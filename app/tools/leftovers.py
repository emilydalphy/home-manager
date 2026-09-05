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


def plan_leftover_chains(weekly_plan_id: int) -> dict:
    """
    Every confirmed cook-once-eat-twice chain on this plan.

    Returns:
      {
        "sources":   {source_entry_id: {"entry_id", "date", "slot", "meal",
                                        "targets": [{"entry_id","date","slot"}, ...]}},
        "leftovers": {leftover_entry_id: {"entry_id", "date", "slot",
                                          "source": {"entry_id","date","slot","meal"}}},
      }

    Targets are sorted by (date, slot) so a source that feeds two nights
    always reads in the order the nights actually fall. Both maps are
    empty for a plan with no chains, which is most plans.
    """
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
    conn.close()

    by_date_slot = {(r["date"], r["slot"]): r for r in rows}
    by_id = {r["id"]: r for r in rows}

    sources: dict[int, dict] = {}
    leftovers: dict[int, dict] = {}
    for r in rows:
        if r["slot_state"] != "planned":
            continue
        derived = json.loads(r["derived_from_json"] or "{}")
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

        entry = sources.setdefault(source["id"], {
            "entry_id": source["id"], "date": source["date"], "slot": source["slot"],
            "meal": source["meal"], "note": source_derived.get("make_double_note") or "",
            "targets": [],
        })
        entry["targets"].append({"entry_id": r["id"], "date": r["date"], "slot": r["slot"]})
        leftovers[r["id"]] = {
            "entry_id": r["id"], "date": r["date"], "slot": r["slot"],
            "source": {
                "entry_id": source["id"], "date": source["date"],
                "slot": source["slot"], "meal": source["meal"],
            },
        }

    for entry in sources.values():
        entry["targets"].sort(key=lambda t: (t["date"], t["slot"]))
    return {"sources": sources, "leftovers": leftovers}


def eaters_at(date_str: str, slot: str) -> int:
    """
    How many people this one meal actually feeds — attendance's headcount
    (members present plus guests). get_slot_attendance already falls back
    to "everyone's home" when no attendance was ever recorded for the
    slot, so an ordinary week answers with the household's own size. 0
    only for a household with no members yet, or a slot everyone is away
    for; callers treat 0 as "don't scale" rather than "cook nothing".
    """
    try:
        return int(_attendance.get_slot_attendance(date_str, slot)["headcount"] or 0)
    except Exception:
        return 0


def batch_for_source(source: dict) -> dict:
    """
    What one source night actually has to cook: its own table plus every
    night eating its leftovers.

    Returns {"servings", "cook_eaters", "targets": [{date, slot, eaters}]}.
    `servings` is 0 when nothing could be counted (no members on record) —
    the signal to leave the recipe's own quantities alone rather than
    scale to nothing.
    """
    cook_eaters = eaters_at(source["date"], source["slot"])
    targets = [
        {**t, "eaters": eaters_at(t["date"], t["slot"])}
        for t in source["targets"]
    ]
    total = cook_eaters + sum(t["eaters"] for t in targets)
    return {"servings": total, "cook_eaters": cook_eaters, "targets": targets}


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
    days = _join_days([_weekday(t["date"]) for t in source["targets"]])
    return f"Cooking for {servings} — covers {cook_label} and leftovers on {days}."


def leftovers_headline(source_meal: str, source_date: str) -> str:
    """"Leftovers — Wednesday's Korean Beef Bulgogi Lettuce Wraps"."""
    return f"Leftovers — {_weekday(source_date)}’s {source_meal}"


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
