"""
Per-person, per-meal attendance — who is actually at which meal.

Emily's deepened model (Notion, 2026-09-03), in her words: "this flow feels
like it's one layer too shallow... Two people in the home might also not
have the exact same schedule... just Emily is home for dinner on Thursday,
but Vineeth is out."

This module holds the atomic fact everything else in week planning derives
from. It sits UNDER slot_needs rather than beside it:

    attendance (who is present)  ->  slot_needs.need (what to do about it)

Concretely, the three cases and what each derives:

* **Nobody present, no guests** -> the slot's need becomes 'away': nothing
  planned, nothing bought. "Away" stops being a flag a person sets and
  becomes what an empty attendance *means*. `_sync_away_need` keeps that
  true in both directions, so un-toggling the last person back in also
  clears the away need — a one-way sync would leave a slot permanently
  blanked by a tap someone took back.
* **Some present** -> plan for the real headcount. `grocery_scale_factor`
  turns that into actual smaller (or bigger) shopping quantities, and
  `context_for_week` hands generation the number so the meal itself is
  planned for the right table.
* **Guests** -> the same model with the headcount up. The "Hosting guests"
  night-tag chip writes `guest_count` here (see
  week_intake.save_week_intake) instead of being a second, parallel notion
  of table size that generation and groceries would each have to know
  about separately.

A slot with NO row means the ordinary case: every current member present,
no guests. Same "absence has one meaning" discipline slot_needs already
follows, and load-bearing for a reason beyond tidiness — it means a member
added next month is present by default at every meal, rather than being
retroactively absent from every meal already planned.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import weekly_plan as _weekly_plan
from . import quantities as _quantities


_SEQUENCE_SLOTS = _weekly_plan.WEEK_SLOTS
_ALL_SLOTS = (*_weekly_plan.WEEK_SLOTS, "snack")


def _validate_slot(slot: str) -> None:
    if slot not in _ALL_SLOTS:
        raise ValueError(f"slot must be one of {_ALL_SLOTS}, not {slot!r}.")


def _member_rows(conn) -> list:
    return conn.execute(
        "SELECT id, name FROM members WHERE household_id = ? ORDER BY id",
        (household_id(),),
    ).fetchall()


def _names_for(rows, ids: list[int]) -> list[str]:
    by_id = {r["id"]: r["name"] for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def resolve_member_ids(members: list | None) -> list[int]:
    """
    Turn whatever a caller has — member names, member ids, or nothing —
    into a list of real members.id for this household.

    None or an empty list means THE WHOLE HOUSEHOLD. That default is what
    lets "we're away this weekend" and "Vineeth's away this weekend" be the
    same call with one argument different, and it's why away_stretches
    stores '[]' for an everyone-trip rather than an expanded list: the
    household is a live thing, and a trip recorded as "all of us" should
    still mean all of us if someone moves in next week.

    Names match case-insensitively; an unknown name is an error rather than
    a silent no-op, because silently dropping "Vineeth" would produce a
    trip that marks nobody away and looks like it worked.
    """
    conn = get_conn()
    rows = _member_rows(conn)
    conn.close()
    if not members:
        return [r["id"] for r in rows]

    by_lower_name = {r["name"].strip().lower(): r["id"] for r in rows}
    valid_ids = {r["id"] for r in rows}
    resolved: list[int] = []
    for m in members:
        if isinstance(m, int) or (isinstance(m, str) and m.isdigit()):
            mid = int(m)
            if mid not in valid_ids:
                raise ValueError(f"No household member with id {mid}.")
            resolved.append(mid)
            continue
        key = str(m).strip().lower()
        if key not in by_lower_name:
            known = ", ".join(r["name"] for r in rows) or "nobody yet"
            raise ValueError(f"No household member named {m!r}. Members are: {known}.")
        resolved.append(by_lower_name[key])
    # Preserve household order and drop duplicates, so two spellings of the
    # same person don't count as two travelers.
    return [r["id"] for r in rows if r["id"] in set(resolved)]


def _attendance_dict(rows, row, date_str: str, slot: str) -> dict:
    all_ids = [r["id"] for r in rows]
    if row is None:
        present = list(all_ids)
        guests = 0
        explicit = False
        source = ""
        stretch_id = None
    else:
        stored = json.loads(row["present_member_ids_json"] or "[]")
        # Intersect with live members: someone removed from the household
        # shouldn't keep counting toward a headcount.
        present = [i for i in all_ids if i in set(stored)]
        guests = int(row["guest_count"] or 0)
        explicit = True
        source = row["source"] or ""
        stretch_id = row["away_stretch_id"]
    absent = [i for i in all_ids if i not in set(present)]
    headcount = len(present) + guests
    return {
        "date": date_str,
        "slot": slot,
        "present_member_ids": present,
        "present_names": _names_for(rows, present),
        "absent_member_ids": absent,
        "absent_names": _names_for(rows, absent),
        "guest_count": guests,
        "headcount": headcount,
        "household_size": len(all_ids),
        "everyone_home": not absent and guests == 0,
        "nobody_home": headcount == 0,
        "explicit": explicit,
        "source": source,
        "away_stretch_id": stretch_id,
    }


def get_slot_attendance(date_str: str, slot: str) -> dict:
    """Who is at one meal — the stored row, or the implicit everyone's-home default."""
    date.fromisoformat(date_str)
    _validate_slot(slot)
    conn = get_conn()
    rows = _member_rows(conn)
    row = conn.execute(
        "SELECT * FROM slot_attendance WHERE household_id = ? AND date = ? AND slot = ?",
        (household_id(), date_str, slot),
    ).fetchone()
    conn.close()
    return _attendance_dict(rows, row, date_str, slot)


def _write(date_str: str, slot: str, present_ids: list[int], guest_count: int,
           source: str, away_stretch_id: int | None) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO slot_attendance
            (household_id, date, slot, present_member_ids_json, guest_count, source, away_stretch_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(household_id, date, slot) DO UPDATE SET
            present_member_ids_json = excluded.present_member_ids_json,
            guest_count = excluded.guest_count,
            source = excluded.source,
            away_stretch_id = COALESCE(excluded.away_stretch_id, slot_attendance.away_stretch_id),
            updated_at = datetime('now')
        """,
        (household_id(), date_str, slot, json.dumps(present_ids), int(guest_count), source, away_stretch_id),
    )
    conn.commit()
    conn.close()


def _sync_away_need(date_str: str, slot: str, att: dict, away_stretch_id: int | None = None) -> str:
    """
    Keep slot_needs' 'away' in step with attendance, in BOTH directions —
    the join that makes "away means an empty attendance" true rather than
    merely intended.

    Empty attendance sets the need to 'away' (which, via set_slot_need,
    also converts any meal already generated for that slot to
    planned_empty and takes its ingredients back off the list). Attendance
    that is no longer empty clears an 'away' need — and only an 'away' one:
    a 'quick' or 'ready_made' edge, or anything a person set by hand, is
    left exactly as it is. Without the reverse direction, tapping a
    presence avatar back on would leave the slot blanked forever with no
    way to explain why.
    """
    from . import slot_needs as _slot_needs  # deferred: slot_needs reaches back into this module

    current = _slot_needs.get_slot_need(date_str, slot)
    if att["nobody_home"]:
        if current["need"] != "away":
            _slot_needs.set_slot_need(
                date_str, slot, "away",
                reason=_slot_needs._default_reason("away"),
                away_stretch_id=away_stretch_id,
            )
            return "set_away"
        return "already_away"
    if current["need"] == "away":
        _slot_needs.clear_slot_need(date_str, slot)
        return "cleared_away"
    return "unchanged"


def set_slot_attendance(
    date_str: str, slot: str, present_member_ids: list | None = None,
    guest_count: int | None = None, source: str = "", away_stretch_id: int | None = None,
) -> dict:
    """
    Set who is at one meal outright. `present_member_ids` accepts names or
    ids; None leaves the current present set alone (so guest count can be
    changed on its own, and vice versa).

    Setting attendance to nobody is exactly how a slot becomes 'away' —
    there is no separate "mark this away" write to keep consistent with
    this one.
    """
    date.fromisoformat(date_str)
    _validate_slot(slot)
    current = get_slot_attendance(date_str, slot)
    present = (
        current["present_member_ids"] if present_member_ids is None
        else resolve_member_ids(present_member_ids) if present_member_ids
        else []  # an explicit empty list means nobody — not "default to everyone"
    )
    guests = current["guest_count"] if guest_count is None else max(0, int(guest_count))
    _write(date_str, slot, present, guests, source or current["source"] or "", away_stretch_id)
    att = get_slot_attendance(date_str, slot)
    att["away_need"] = _sync_away_need(date_str, slot, att, away_stretch_id)
    return att


def set_member_attendance(date_str: str, slot: str, member: str, present: bool = False) -> dict:
    """
    Mark one person in or out of one meal — the small gesture. This is what
    a presence avatar tap on a day card does, and what "Vineeth's out
    Thursday" does in chat: the same write, reached two ways.

    For an extended absence use set_away_stretch instead; it covers a whole
    range in one gesture and derives the quick/ready-made edges around it,
    which repeated single-meal toggles cannot do.
    """
    date.fromisoformat(date_str)
    _validate_slot(slot)
    member_ids = resolve_member_ids([member])
    current = get_slot_attendance(date_str, slot)
    present_set = set(current["present_member_ids"])
    if present:
        present_set |= set(member_ids)
    else:
        present_set -= set(member_ids)
    ordered = [i for i in _household_member_ids() if i in present_set]
    return set_slot_attendance(date_str, slot, present_member_ids=ordered or [], source="toggle")


def _household_member_ids() -> list[int]:
    conn = get_conn()
    rows = _member_rows(conn)
    conn.close()
    return [r["id"] for r in rows]


def set_guest_count(date_str: str, slot: str = "dinner", guest_count: int = 0, source: str = "guests") -> dict:
    """
    How many extra mouths beyond the household are at this meal — "Hosting
    guests", as a headcount rather than as a separate concept. Portions and
    shopping both read the same number that a presence toggle moves.
    """
    return set_slot_attendance(date_str, slot, guest_count=guest_count, source=source)


def clear_slot_attendance(date_str: str, slot: str) -> dict:
    """Forget any explicit attendance for one meal, returning it to everyone's-home."""
    date.fromisoformat(date_str)
    _validate_slot(slot)
    conn = get_conn()
    conn.execute(
        "DELETE FROM slot_attendance WHERE household_id = ? AND date = ? AND slot = ?",
        (household_id(), date_str, slot),
    )
    conn.commit()
    conn.close()
    att = get_slot_attendance(date_str, slot)
    att["away_need"] = _sync_away_need(date_str, slot, att)
    return att


def remove_members_from_slot(
    date_str: str, slot: str, member_ids: list[int], source: str = "", away_stretch_id: int | None = None,
) -> dict:
    """Take specific people out of one meal, leaving everyone else as they are — the primitive set_away_stretch walks a range with."""
    current = get_slot_attendance(date_str, slot)
    remaining = [i for i in current["present_member_ids"] if i not in set(member_ids)]
    return set_slot_attendance(
        date_str, slot, present_member_ids=remaining or [],
        source=source or "away_stretch", away_stretch_id=away_stretch_id,
    )


def get_week_attendance(week_start: str, day_count: int = 7) -> dict:
    """
    Attendance for a week as {date: {slot: attendance}}, including only the
    slots that actually deviate from everyone's-home. A day with nothing
    unusual simply doesn't appear — the UI reads the same "absence means
    normal" convention the table itself follows, and progressive disclosure
    (Emily's decision) then has an easy question to ask: is there a row?
    """
    start = date.fromisoformat(week_start)
    week_end = (start + timedelta(days=day_count)).isoformat()
    conn = get_conn()
    rows = _member_rows(conn)
    stored = conn.execute(
        "SELECT * FROM slot_attendance WHERE household_id = ? AND date >= ? AND date < ? ORDER BY date, slot",
        (household_id(), week_start, week_end),
    ).fetchall()
    conn.close()
    out: dict[str, dict] = {}
    for row in stored:
        att = _attendance_dict(rows, row, row["date"], row["slot"])
        if att["everyone_home"]:
            continue
        out.setdefault(row["date"], {})[row["slot"]] = att
    return out


def headcount_for_slot(date_str: str, slot: str) -> int:
    """How many people to actually cook for at this meal — members present plus guests."""
    return get_slot_attendance(date_str, slot)["headcount"]


def grocery_scale_factor(date_str: str, slot: str) -> float:
    """
    What to multiply this meal's ingredient quantities by, so the shopping
    matches the table.

    Deliberately relative to the FULL HOUSEHOLD, not to the recipe's own
    default_servings: today nothing in the grocery path scales at all
    (quantities are used exactly as the recipe writes them), so anchoring
    to the household means a week where everyone is home shops precisely as
    it always has — 1.0, byte for byte — and only a meal whose attendance
    actually deviates moves. A recipe-servings anchor would silently
    re-quantify every meal in the app the moment this shipped, which is a
    much bigger claim than this ticket gets to make on its own.

    Returns 1.0 whenever there's no explicit attendance, when the household
    has no members recorded yet, or when nobody is home (an away slot
    contributes nothing to the list at all, so its factor is never used —
    and 0.0 would be a trap for any future caller that did use it).
    """
    att = get_slot_attendance(date_str, slot)
    if not att["explicit"] or att["household_size"] == 0 or att["nobody_home"]:
        return 1.0
    return att["headcount"] / att["household_size"]


def scale_ingredients(ingredients: list[dict], factor: float) -> list[dict]:
    """
    Apply a headcount factor to a recipe's ingredient list. Quantities that
    parse cleanly (a number, optionally a known unit) scale; anything
    freeform — "a pinch", "to taste", "1 clove or so" — is left exactly as
    written rather than guessed at, the same call scale_recipe already
    makes for a cook standing at the counter.
    """
    if factor == 1.0:
        return [dict(i) for i in ingredients]
    out = []
    for ing in ingredients:
        parsed = _quantities._parse_quantity(ing.get("qty", "") or "")
        if parsed:
            amount, unit = parsed
            out.append({**ing, "qty": _quantities._format_quantity(amount * factor, unit)})
        else:
            out.append(dict(ing))
    return out


def summary_line(att: dict) -> str:
    """
    The one-line answer a day card shows under its presence avatars —
    "Dinner for 1 — Vineeth's out." Written the way a person would say it
    (DESIGN_SYSTEM.md §7), naming whoever is actually missing rather than
    reporting a count in the abstract.
    """
    slot_label = att["slot"].capitalize()
    if att["nobody_home"]:
        return f"{slot_label} skipped — nobody's home. Nothing planned, nothing bought."
    bits = []
    if att["absent_names"]:
        names = _join_names(att["absent_names"])
        verb = "'s out" if len(att["absent_names"]) == 1 else " are out"
        bits.append(f"{names}{verb}")
    if att["guest_count"]:
        n = att["guest_count"]
        bits.append(f"{n} guest{'s' if n != 1 else ''}")
    if not bits:
        return ""
    return f"{slot_label} for {att['headcount']} — {' and '.join(bits)}."


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def context_for_week(week_start: str, day_count: int = 7) -> dict:
    """
    Attendance reshaped for the generator's context — the headcount half of
    "plan for who's actually there".

    Only deviating slots are listed. That keeps the prompt small, but more
    importantly it keeps the instruction unambiguous: every slot NOT named
    here is the household's ordinary table, so the model never has to
    reconcile a per-slot number against a household default that says
    something different.
    """
    start = date.fromisoformat(week_start)
    conn = get_conn()
    rows = _member_rows(conn)
    conn.close()
    week = get_week_attendance(week_start, day_count)
    slots = []
    for i in range(day_count):
        d = (start + timedelta(days=i)).isoformat()
        for slot in _SEQUENCE_SLOTS:
            att = (week.get(d) or {}).get(slot)
            if not att or att["nobody_home"]:
                continue  # an away slot is already covered by slot_needs.away_slots
            slots.append({
                "date": d,
                "slot": slot,
                "serves": att["headcount"],
                "present": att["present_names"],
                "away": att["absent_names"],
                "guests": att["guest_count"],
            })
    return {
        "household_size": len(rows),
        "household_members": [r["name"] for r in rows],
        "default_serves": len(rows),
        "slots_with_a_different_table": slots,
    }
