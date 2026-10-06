"""
Per-person needs setup asks for (Loop Board "Onboarding, regrouped",
Emily 2026-10-05, from Gowthami's household: a nut-free school, a cold
office lunch, warm leftovers at home, two or three snacks for the child).

TWO ANSWERS, BOTH ON THE MEMBER ROW:

  - members.lunch_needs_json — what this person needs for a weekday lunch,
    {"needs": [...], "days": {"monday": [...], ...}}. `needs` is the
    standing answer; `days` holds only the weekdays that were set to
    something different ("Different on some days?"). '' = never said.
  - members.snacks_per_day — 0 to 3 (NULL = never said; default_snacks
    gives the default by age: children 2, adults 1).

THE BRIDGE TO WHAT PLANNING ALREADY READS. Until the planner reads these
needs directly, a person's lunch need is ALSO written as the rhythm fact
the planner already reads for "where is lunch eaten": lunch_location
'out' (packable — the weekly questions pre-tick a packed lunch) when the
need is Cold packed, Warm in a thermos or Nut-free environment, else
'home'. Day-by-day answers become that fact's per-weekday overrides. One
save writes both, so the two can't disagree.
"""
from __future__ import annotations

import json

from ..db import get_conn
from ._shared import EATS_HERE_SQL, IN_MEALS_SQL, household_id

# Emily's five, in her order. The keys are what is stored; the words are
# the screen's.
LUNCH_NEEDS = ("cold_packed", "thermos", "reheat", "made_fresh", "nut_free")
LUNCH_NEED_LABELS = {
    "cold_packed": "Cold packed",
    "thermos": "Warm in a thermos",
    "reheat": "Something to reheat",
    "made_fresh": "Made fresh",
    "nut_free": "Nut-free environment",
}
# The needs that mean the lunch travels: eaten away from the kitchen.
PACKED_NEEDS = frozenset({"cold_packed", "thermos", "nut_free"})
LUNCH_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")

SNACKS_MAX = 3
# Emily, 2026-10-05: "Defaults: children 2, adults 1."
CHILD_AGE_GROUPS = frozenset({"child", "toddler"})


def default_snacks(age_group: str | None) -> int:
    return 2 if (age_group or "").strip().lower() in CHILD_AGE_GROUPS else 1


def _clean_needs(value, who: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{who}'s lunch needs must be a list.")
    out: list[str] = []
    for v in value:
        key = str(v or "").strip().lower()
        if key not in LUNCH_NEEDS:
            raise ValueError(f"{v!r} isn't a lunch need — one of {', '.join(LUNCH_NEEDS)}.")
        if key not in out:
            out.append(key)
    # Stored in Emily's order whatever order they were tapped in.
    return [k for k in LUNCH_NEEDS if k in out]


def _clean_entry(entry, who: str) -> dict:
    if isinstance(entry, list):
        entry = {"needs": entry}
    if not isinstance(entry, dict):
        raise ValueError(f"{who}'s lunch needs must be an object with needs and days.")
    needs = _clean_needs(entry.get("needs"), who)
    days_in = entry.get("days") or {}
    if not isinstance(days_in, dict):
        raise ValueError(f"{who}'s day-by-day lunch needs must be an object of weekday → needs.")
    days: dict[str, list[str]] = {}
    for day, value in days_in.items():
        key = str(day or "").strip().lower()
        if key not in LUNCH_WEEKDAYS:
            raise ValueError(f"{day!r} isn't a weekday (lunch needs are Monday to Friday).")
        cleaned = _clean_needs(value, who)
        # A day the same as the standing answer isn't different.
        if cleaned != needs:
            days[key] = cleaned
    return {"needs": needs, "days": days}


def _members_by_name(conn) -> dict[str, dict]:
    return {
        r["name"].strip().lower(): {"id": r["id"], "name": r["name"], "age_group": r["age_group"]}
        for r in conn.execute(
            f"SELECT id, name, age_group FROM members WHERE household_id = ? AND {IN_MEALS_SQL}",
            (household_id(),),
        ).fetchall()
    }


def validate_member_needs(lunch_needs: dict | None = None, snacks: dict | None = None) -> None:
    """Check both answers without writing anything (names are not checked:
    onboarding validates before the people exist). Raises ValueError."""
    if lunch_needs is not None:
        if not isinstance(lunch_needs, dict):
            raise ValueError("lunch_needs must be an object of person → needs.")
        for name, entry in lunch_needs.items():
            _clean_entry(entry, str(name))
    if snacks is not None:
        if not isinstance(snacks, dict):
            raise ValueError("snacks must be an object of person → snacks a day.")
        for name, n in snacks.items():
            _clean_snacks(n, str(name))


def _clean_snacks(n, who: str) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"{who}'s snacks a day must be 0 to {SNACKS_MAX}.")
    if not 0 <= n <= SNACKS_MAX:
        raise ValueError(f"{who}'s snacks a day must be 0 to {SNACKS_MAX}.")
    return n


def _location(needs: list[str]) -> str:
    return "out" if PACKED_NEEDS.intersection(needs) else "home"


def save_member_needs(lunch_needs: dict | None = None, snacks: dict | None = None,
                      source: str = "onboarding") -> dict:
    """
    Save per-person lunch needs and/or snacks a day, keyed by member NAME
    (case-insensitive). Omitted people and omitted answers stay as they
    are. A name that isn't a member eating here is a ValueError, checked
    before anything is written.

    lunch_needs: {name: {"needs": [key, ...], "days": {"monday": [...]}}}
      (a bare list is the standing answer). Keys are LUNCH_NEEDS.
    snacks: {name: 0..3}.
    """
    from . import rhythm as _rhythm

    validate_member_needs(lunch_needs, snacks)
    conn = get_conn()
    try:
        by_name = _members_by_name(conn)
        plan: list[tuple] = []
        for name, entry in (lunch_needs or {}).items():
            m = by_name.get(str(name).strip().lower())
            if not m:
                raise ValueError(f"{name!r} isn't someone who eats here.")
            plan.append(("lunch", m, _clean_entry(entry, str(name))))
        for name, n in (snacks or {}).items():
            m = by_name.get(str(name).strip().lower())
            if not m:
                raise ValueError(f"{name!r} isn't someone who eats here.")
            plan.append(("snacks", m, _clean_snacks(n, str(name))))
        for kind, m, value in plan:
            if kind == "lunch":
                conn.execute("UPDATE members SET lunch_needs_json = ? WHERE id = ? AND household_id = ?",
                             (json.dumps(value), m["id"], household_id()))
            else:
                conn.execute("UPDATE members SET snacks_per_day = ? WHERE id = ? AND household_id = ?",
                             (value, m["id"], household_id()))
        conn.commit()
    finally:
        conn.close()
    # The bridge (module docstring): the standing location, and an override
    # for exactly the weekdays that differ from it — any other override this
    # person had is cleared, so an old "Tuesdays in the office" can't outlive
    # the answer that replaced it.
    conn = get_conn()
    try:
        overrides = {
            (r["member_name"], r["weekday"])
            for r in conn.execute(
                "SELECT member_name, weekday FROM household_rhythm WHERE household_id = ? "
                "AND fact_type = 'lunch_location' AND weekday != ''",
                (household_id(),),
            ).fetchall()
        }
    finally:
        conn.close()
    for kind, m, value in plan:
        if kind != "lunch":
            continue
        standing = _location(value["needs"])
        _rhythm.set_lunch_location(m["name"], standing, source=source)
        for day in LUNCH_WEEKDAYS:
            weekday = day.capitalize()
            if day in value["days"] and _location(value["days"][day]) != standing:
                _rhythm.set_lunch_location(m["name"], _location(value["days"][day]), weekday=weekday, source=source)
            elif (m["name"], weekday) in overrides:
                _rhythm.clear_lunch_location_override(m["name"], weekday)
    return get_member_needs()


def get_member_needs() -> list[dict]:
    """Every member who eats here with their lunch needs (None = never
    said) and snacks a day (`snacks_set` false = the default by age)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT id, name, age_group, lunch_needs_json, snacks_per_day FROM members "
            f"WHERE household_id = ? AND {IN_MEALS_SQL} ORDER BY id",
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            lunch = json.loads(r["lunch_needs_json"] or "null")
        except (TypeError, ValueError):
            lunch = None
        if lunch is not None:
            try:
                lunch = _clean_entry(lunch, r["name"])
            except ValueError:
                lunch = None
        out.append({
            "id": r["id"], "name": r["name"], "age_group": r["age_group"],
            "lunch_needs": lunch,
            "snacks_per_day": r["snacks_per_day"] if r["snacks_per_day"] is not None else default_snacks(r["age_group"]),
            "snacks_set": r["snacks_per_day"] is not None,
        })
    return out


# "Nut-free environment" is a SAFETY answer, not only a place (review,
# 2026-10-06): a school that is nut-free means no nuts in what that person
# carries in. Until the planner packs lunches per person, the whole
# household's planning treats it as these two allergies for that person —
# over-safe on purpose: a dinner's leftovers can become the lunch.
NUT_FREE_AVOIDANCES = ("allergy: peanuts", "allergy: nuts")


def nut_free_member_names() -> list[str]:
    """Everyone living here whose weekday lunch need, on any day, includes
    "Nut-free environment"."""
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT name, lunch_needs_json FROM members WHERE household_id = ? AND {EATS_HERE_SQL} ORDER BY id",
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            entry = json.loads(r["lunch_needs_json"] or "null")
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        needs = list(entry.get("needs") or [])
        for day in (entry.get("days") or {}).values():
            needs.extend(day or [])
        if "nut_free" in needs:
            out.append(r["name"])
    return out
