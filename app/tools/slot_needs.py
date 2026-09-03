"""
Per-slot planning needs (away / quick / ready_made) and away-stretch range
derivation — Loop Board "Week planning: away-stretches and per-meal needs
(the road-trip weekend)". Extends the existing planned_empty/"nobody home"
dinner concept (meal_plan_entries.slot_state, see weekly_plan.py) to any
slot of any day, and adds two edge states dinner's plain on/off never
needed: 'quick' (the last real meal before a stretch away) and
'ready_made' (the first real meal back, earmarked with a batch/defrost
recommendation rather than freshly cooked). See schema.sql's comments on
slot_needs/away_stretches for the full data model and Emily's decisions
(Notion, 2026-09-03).

A slot with no row in slot_needs IS 'normal' — there is deliberately no
stored 'normal' row (see set_slot_need), same "absence has one meaning"
discipline meal_plan_entries.slot_state already follows for a different
reason.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from ..db import get_conn
from ._shared import household_id
from . import week_intake as _week_intake
from . import weekly_plan as _weekly_plan


NEEDS = ("normal", "away", "quick", "ready_made")

# Canonical per-day ordering the away-stretch range walks to find "the meal
# right before"/"the meal right after" it. Mirrors weekly_plan.WEEK_SLOTS
# exactly — snack deliberately isn't part of this sequence, same reasoning
# audit_plan_slots already uses: snacks "ride along" in the same table but
# aren't part of the day's real meal sequence, so they don't participate in
# away-stretch edge derivation. A snack can still be tagged directly via
# set_slot_need.
_SEQUENCE_SLOTS = _weekly_plan.WEEK_SLOTS
_ALL_SLOTS = (*_weekly_plan.WEEK_SLOTS, "snack")


def _validate_slot(slot: str, *, allow_snack: bool = True) -> None:
    valid = _ALL_SLOTS if allow_snack else _SEQUENCE_SLOTS
    if slot not in valid:
        raise ValueError(f"slot must be one of {valid}, not {slot!r}.")


def _default_reason(need: str) -> str:
    return {
        "away": "You’re away — I’ve planned nothing and bought nothing.",
        "quick": "Last one before you head out — keeping it quick and grab-and-go.",
        "ready_made": "First one back — covering this with something already made rather than cooking fresh.",
    }.get(need, "")


def _row_to_dict(row) -> dict:
    for_member_ids = json.loads((row["for_member_ids_json"] or "[]"))
    return {
        "date": row["date"],
        "slot": row["slot"],
        "need": row["need"],
        "reason": row["reason"],
        "away_stretch_id": row["away_stretch_id"],
        "recommended_batch_from_entry_id": row["recommended_batch_from_entry_id"],
        "recommended_defrost_item": row["recommended_defrost_item"] or None,
        "recommendation_confirmed": bool(row["recommendation_confirmed"]),
        # [] means the whole household — see schema.sql on
        # slot_needs.for_member_ids_json.
        "for_member_ids": for_member_ids,
        "for_member_names": _member_names(for_member_ids),
    }


def _member_names(member_ids: list[int]) -> list[str]:
    if not member_ids:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name FROM members WHERE household_id = ? ORDER BY id", (household_id(),)
    ).fetchall()
    conn.close()
    by_id = {r["id"]: r["name"] for r in rows}
    return [by_id[i] for i in member_ids if i in by_id]


def _plan_id_for_date(conn, meal_date: str, slot: str) -> int | None:
    """
    The weekly_plan a given date/slot belongs to, if any exists yet — an
    existing entry's own plan first (it's the authoritative one for that
    exact slot), falling back to whichever plan covers that date's week.
    None if the household hasn't generated that week at all, which is the
    normal case for a need declared ahead of generation (this is meant to
    be usable at intake time, exactly like week_intake.night_tags today).
    """
    row = conn.execute(
        "SELECT weekly_plan_id FROM meal_plan_entries "
        "WHERE household_id = ? AND date = ? AND slot = ? AND component_category IS NULL "
        "AND weekly_plan_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        (household_id(), meal_date, slot),
    ).fetchone()
    if row and row["weekly_plan_id"]:
        return row["weekly_plan_id"]
    d = date.fromisoformat(meal_date)
    monday = (d - timedelta(days=d.weekday())).isoformat()
    return _weekly_plan.get_plan_id_for_week(monday)


def set_slot_need(
    date_str: str, slot: str, need: str, reason: str = "",
    away_stretch_id: int | None = None, for_member_ids: list[int] | None = None,
) -> dict:
    """
    Set (or clear, with need='normal') one slot's planning need directly —
    the per-meal override layer from Emily's design, shown only once a trip
    or override has actually created the need (progressive disclosure, her
    decision 2026-09-03). set_away_stretch is the usual way a whole trip
    gets marked in one gesture; use this for a single slot, or to
    hand-correct one slot a range produced.

    need='away' converts any meal already generated for this exact slot to
    planned_empty immediately — clearing it and reversing whatever it put
    on the grocery list — the same "no planning, no groceries" guarantee
    'planned_empty' already gives a nobody-home dinner, extended here to
    any slot, applied the moment the need is declared rather than only at
    the next generation. If no plan covers this date yet, the need is
    simply recorded; generation applies it when the week is built (see
    apply_slot_needs_to_plan).

    need='normal' deletes the row outright rather than storing it — see
    the module docstring on why an explicit 'normal' row would just be a
    second way to say nothing.

    `for_member_ids` scopes the need to specific people (None/[] = the
    whole household). A partial trip's edges are per-traveler: if only
    Vineeth is away, the meal before he leaves is 'quick' FOR HIM while
    everyone else eats normally.
    """
    date.fromisoformat(date_str)
    if need not in NEEDS:
        raise ValueError(f"need must be one of {NEEDS}, not {need!r}.")
    _validate_slot(slot)
    if need == "normal":
        return clear_slot_need(date_str, slot)

    resolved_reason = (reason or "").strip() or _default_reason(need)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO slot_needs (household_id, date, slot, need, reason, away_stretch_id, for_member_ids_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(household_id, date, slot) DO UPDATE SET
            need = excluded.need, reason = excluded.reason,
            away_stretch_id = excluded.away_stretch_id,
            for_member_ids_json = excluded.for_member_ids_json,
            updated_at = datetime('now')
        """,
        (household_id(), date_str, slot, need, resolved_reason, away_stretch_id,
         json.dumps(sorted(for_member_ids or []))),
    )
    conn.commit()

    plan_id = _plan_id_for_date(conn, date_str, slot) if need == "away" else None
    conn.close()

    converted = False
    if need == "away" and plan_id is not None:
        _weekly_plan.clear_plan_slot(plan_id, date_str, slot)
        _weekly_plan.plan_slot_empty(
            weekly_plan_id=plan_id, meal_date=date_str, slot=slot,
            reason=resolved_reason,
            derived_from={"need": "away", "away_stretch_id": away_stretch_id},
        )
        converted = True

    return {
        "date": date_str, "slot": slot, "need": need, "reason": resolved_reason,
        "converted_existing_plan_slot": converted,
        "for_member_ids": sorted(for_member_ids or []),
    }


def clear_slot_need(date_str: str, slot: str) -> dict:
    """Reset one slot back to 'normal' — deletes its slot_needs row outright, if any. Does not touch any already-planned meal; only the declared need."""
    date.fromisoformat(date_str)
    _validate_slot(slot)
    conn = get_conn()
    conn.execute(
        "DELETE FROM slot_needs WHERE household_id = ? AND date = ? AND slot = ?",
        (household_id(), date_str, slot),
    )
    conn.commit()
    conn.close()
    return {"date": date_str, "slot": slot, "need": "normal"}


def get_slot_need(date_str: str, slot: str) -> dict:
    """The declared need for one slot, or the implicit 'normal' default if nothing's been recorded."""
    date.fromisoformat(date_str)
    _validate_slot(slot)
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM slot_needs WHERE household_id = ? AND date = ? AND slot = ?",
        (household_id(), date_str, slot),
    ).fetchone()
    conn.close()
    if not row:
        return {
            "date": date_str, "slot": slot, "need": "normal", "reason": "",
            "away_stretch_id": None, "recommended_batch_from_entry_id": None,
            "recommended_defrost_item": None, "recommendation_confirmed": False,
            "for_member_ids": [], "for_member_names": [],
        }
    return _row_to_dict(row)


def get_week_slot_needs(week_start: str) -> dict:
    """
    Every declared (non-'normal') slot need for the 7 days starting
    week_start, as {date: {slot: need_dict}}. A day/slot with nothing
    declared simply doesn't appear — same "absence means normal"
    convention the row itself follows.
    """
    start = date.fromisoformat(week_start)
    week_end = (start + timedelta(days=7)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM slot_needs WHERE household_id = ? AND date >= ? AND date < ?",
        (household_id(), week_start, week_end),
    ).fetchall()
    conn.close()
    by_date: dict[str, dict] = {}
    for row in rows:
        by_date.setdefault(row["date"], {})[row["slot"]] = _row_to_dict(row)
    return by_date


def _slot_sequence(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Every (date, slot) pair, in canonical order, from start_date to end_date inclusive."""
    d0, d1 = date.fromisoformat(start_date), date.fromisoformat(end_date)
    out = []
    d = d0
    while d <= d1:
        for slot in _SEQUENCE_SLOTS:
            out.append((d.isoformat(), slot))
        d += timedelta(days=1)
    return out


def _edge_reason(need: str, traveler_names: list[str], whole_household: bool) -> str:
    """
    The reason line for a derived edge, in the house voice — naming the
    traveler when the trip is only theirs, because "keeping it quick" reads
    as nonsense on a meal the rest of the household is sitting down to
    normally. See DESIGN_SYSTEM.md §7.
    """
    if whole_household or not traveler_names:
        return _default_reason(need)
    from . import attendance as _attendance
    who = _attendance._join_names(traveler_names)
    if need == "quick":
        return f"Last one before {who} heads out — keeping it quick."
    if need == "ready_made":
        return f"First one back for {who} — something already made rather than cooking fresh."
    return _default_reason(need)


def _traveler_edge_indices(sequence, idx_from: int, idx_to: int, traveler_id: int) -> tuple[int | None, int | None]:
    """
    One traveler's own two edges: the last slot before this stretch they're
    actually present for, and the first slot after it they're back for.

    This walks rather than just taking the immediate neighbours, because a
    person can already be out for reasons that have nothing to do with this
    trip — Vineeth out Friday dinner, then away from Saturday lunch. His
    last real meal at home is Friday *lunch*, not a Friday dinner he was
    never going to eat. Taking the neighbour blindly would tag a meal he
    isn't at as his grab-and-go.
    """
    from . import attendance as _attendance

    def present_at(i: int) -> bool:
        d, s = sequence[i]
        return traveler_id in _attendance.get_slot_attendance(d, s)["present_member_ids"]

    quick_idx = None
    for i in range(idx_from - 1, -1, -1):
        if present_at(i):
            quick_idx = i
            break
    ready_idx = None
    for i in range(idx_to + 1, len(sequence)):
        if present_at(i):
            ready_idx = i
            break
    return quick_idx, ready_idx


def set_away_stretch(
    from_date: str, from_slot: str, to_date: str, to_slot: str, reason: str = "",
    member_names: list | None = None,
) -> dict:
    """
    Mark a whole away stretch in one gesture — "away Saturday lunch through
    Sunday lunch" — the range primitive from Emily's design (trips as the
    primitive, not slots; Notion, 2026-09-03). Every slot from
    from_date/from_slot to to_date/to_slot INCLUSIVE is marked 'away' (see
    set_slot_need: no planning, no groceries, converted immediately if a
    plan already covers it). Two more slots are then derived automatically,
    one on each side of the range:

    - The slot immediately BEFORE the range starts becomes 'quick' — the
      last real meal before heading out.
    - The slot immediately AFTER the range ends becomes 'ready_made' — the
      first real meal back, earmarked with a recommendation (see
      _recommend_ready_made) rather than freshly cooked.

    Emily's own scenario, exactly: from_date/from_slot = Saturday/lunch,
    to_date/to_slot = Sunday/lunch marks 4 slots away (Sat lunch, Sat
    dinner, Sun breakfast, Sun lunch), tags Saturday breakfast 'quick', and
    tags Sunday dinner 'ready_made' with a recommendation attached.

    Both edges always land somewhere — ISO dates are unbounded, so even a
    stretch covering every slot of a week still derives its quick edge on
    the day before and its ready_made edge on the day after, crossing into
    the adjacent week rather than being omitted. That's correct: the last
    real meal before an 8-day trip really is the day before it starts.

    `member_names` scopes the trip to specific travelers (names or ids);
    None or empty means the whole household, which is both the common case
    and the behavior before attendance existed. What changes with a partial
    trip (Emily's deepened model, 2026-09-03):

    - The covered slots are NOT blanked. The travelers come out of each
      slot's attendance; the slot only becomes 'away' if that empties it.
      A Thursday dinner Vineeth is out for is still a dinner — for one.
    - Each traveler derives their OWN edges, from their own attendance
      rather than from the range's boundaries (see
      _traveler_edge_indices), so two people leaving at different times
      get different grab-and-go meals.
    """
    date.fromisoformat(from_date)
    date.fromisoformat(to_date)
    _validate_slot(from_slot, allow_snack=False)
    _validate_slot(to_slot, allow_snack=False)

    from . import attendance as _attendance
    travelers = _attendance.resolve_member_ids(member_names)
    all_member_ids = _attendance._household_member_ids()
    # '[]' is the stored form of "all of us" — see schema.sql on
    # away_stretches.member_ids_json. Normalizing here means a trip that
    # happens to name everyone reads, and phrases itself, as a household
    # trip rather than as a coincidence of two individual ones.
    whole_household = not member_names or set(travelers) == set(all_member_ids)
    scope_ids: list[int] = [] if whole_household else sorted(travelers)

    # Pad two days on each side: one so the derived edges have somewhere to
    # land when the stretch starts on a week's first slot, and one more
    # because a per-traveler edge WALKS outward past meals that person was
    # already out for, so the immediate neighbour may not be far enough.
    pad_start = (date.fromisoformat(from_date) - timedelta(days=2)).isoformat()
    pad_end = (date.fromisoformat(to_date) + timedelta(days=2)).isoformat()
    sequence = _slot_sequence(pad_start, pad_end)
    try:
        idx_from = sequence.index((from_date, from_slot))
        idx_to = sequence.index((to_date, to_slot))
    except ValueError as exc:
        raise ValueError(f"Invalid away-stretch slot: {exc}") from exc
    if idx_from > idx_to:
        raise ValueError("An away stretch's start must come before (or be) its end.")

    away_reason = (reason or "").strip() or _default_reason("away")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO away_stretches (household_id, from_date, from_slot, to_date, to_slot, reason, member_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (household_id(), from_date, from_slot, to_date, to_slot, away_reason, json.dumps(scope_ids)),
    )
    conn.commit()
    stretch_id = cur.lastrowid
    conn.close()

    covered = sequence[idx_from:idx_to + 1]
    emptied: list[dict] = []
    reduced: list[dict] = []
    for d, s in covered:
        if travelers:
            att = _attendance.remove_members_from_slot(
                d, s, travelers, source="away_stretch", away_stretch_id=stretch_id,
            )
        else:
            # No members recorded at all (a household mid-onboarding, and
            # every test that predates attendance). There is nobody to take
            # out of the meal, so fall back to the household-level meaning
            # of the gesture directly: this range is away.
            att = _attendance.set_slot_attendance(
                d, s, present_member_ids=[], source="away_stretch", away_stretch_id=stretch_id,
            )
        if att["nobody_home"]:
            # _sync_away_need already wrote the 'away' need; re-stamp it so
            # the caller's reason and this stretch's id are what's stored.
            set_slot_need(d, s, "away", reason=away_reason, away_stretch_id=stretch_id)
            emptied.append({"date": d, "slot": s})
        else:
            reduced.append({"date": d, "slot": s, "serves": att["headcount"], "present": att["present_names"]})

    # --- The two derived edges, per traveler -------------------------------
    # Each traveler's own last-meal-before and first-meal-back. When the
    # whole household travels together these coincide and merge into one
    # household-level edge; when they don't, two people can genuinely have
    # different grab-and-go meals.
    quick_edges: dict[tuple[str, str], list[int]] = {}
    ready_edges: dict[tuple[str, str], list[int]] = {}
    if travelers:
        for traveler_id in travelers:
            q_idx, r_idx = _traveler_edge_indices(sequence, idx_from, idx_to, traveler_id)
            if q_idx is not None:
                quick_edges.setdefault(sequence[q_idx], []).append(traveler_id)
            if r_idx is not None:
                ready_edges.setdefault(sequence[r_idx], []).append(traveler_id)
    else:
        if idx_from > 0:
            quick_edges[sequence[idx_from - 1]] = []
        if idx_to + 1 < len(sequence):
            ready_edges[sequence[idx_to + 1]] = []

    def _apply_edge(slot_key: tuple[str, str], edge_travelers: list[int], need: str) -> bool:
        d, s = slot_key
        existing = get_slot_need(d, s)
        if existing["need"] == "away":
            # Nobody is home for this meal, so it cannot be anybody's
            # grab-and-go or first meal back. Leave the away slot alone.
            return False
        scope = [] if (not edge_travelers or set(edge_travelers) == set(all_member_ids)) else sorted(edge_travelers)
        if existing["need"] == need and existing["for_member_ids"] and scope:
            # A second trip landing its edge on the same meal — add its
            # travelers rather than replacing the first trip's.
            scope = sorted(set(existing["for_member_ids"]) | set(scope))
        set_slot_need(
            d, s, need,
            reason=_edge_reason(need, _member_names(scope), not scope),
            away_stretch_id=stretch_id, for_member_ids=scope,
        )
        return True

    for slot_key, edge_travelers in quick_edges.items():
        _apply_edge(slot_key, edge_travelers, "quick")

    ready_made_result = None
    first_ready: tuple[str, str] | None = None
    for slot_key in sorted(ready_edges):
        if not _apply_edge(slot_key, ready_edges[slot_key], "ready_made"):
            continue
        d, s = slot_key
        recommendation = _recommend_ready_made(d, s)
        if recommendation["recommended_batch_from_entry_id"] or recommendation["recommended_defrost_item"]:
            set_slot_recommendation(
                d, s,
                batch_from_entry_id=recommendation["recommended_batch_from_entry_id"],
                defrost_item=recommendation["recommended_defrost_item"],
            )
        if first_ready is None:
            first_ready = slot_key
            ready_made_result = get_slot_need(d, s)

    first_quick = sorted(quick_edges)[0] if quick_edges else None
    return {
        "away_stretch_id": stretch_id,
        "from": {"date": from_date, "slot": from_slot},
        "to": {"date": to_date, "slot": to_slot},
        "member_ids": scope_ids,
        "member_names": _member_names(scope_ids),
        "whole_household": whole_household,
        # Slots nobody is home for — the "nothing planned, nothing bought"
        # set. For a partial trip this is usually empty.
        "away_slots": emptied,
        # Slots that still happen, just for fewer people.
        "reduced_slots": reduced,
        "quick_slot": {"date": first_quick[0], "slot": first_quick[1]} if first_quick else None,
        "quick_slots": [{"date": d, "slot": s} for d, s in sorted(quick_edges)],
        "ready_made_slot": {"date": first_ready[0], "slot": first_ready[1]} if first_ready else None,
        "ready_made_slots": [{"date": d, "slot": s} for d, s in sorted(ready_edges)],
        "ready_made_recommendation": ready_made_result,
    }


def _recommend_ready_made(date_str: str, slot: str) -> dict:
    """
    A first pass at what could cover a ready_made slot without fresh
    cooking — a freezer item to defrost, or an earlier dinner this week
    worth having doubled. NEVER auto-applied: Emily's rule is that the
    system recommends and the household confirms (see
    slot_needs.recommendation_confirmed) — this only computes and returns
    the candidate; set_away_stretch and apply_slot_needs_to_plan are what
    actually store it, and confirm_slot_recommendation is what a person
    accepts.

    A freezer item wins when one exists — a defrost is normally less
    effort than a double batch. Falls back to the most recent real dinner
    planned before this date in the household's current plan (if any) as
    a "you cooked extra of this — save it for then" candidate. Neither may
    exist yet (nothing frozen, no plan generated) — that's a real "nothing
    to recommend yet" answer, not an error; the reminder machinery that
    actually acts on a confirmed recommendation belongs to the separate
    defrost-flow ticket, which reads this rather than duplicating it.
    """
    conn = get_conn()
    freezer_item = conn.execute(
        "SELECT item FROM inventory_items WHERE household_id = ? AND location = 'freezer' "
        "AND TRIM(quantity) != '' ORDER BY updated_at DESC, id DESC LIMIT 1",
        (household_id(),),
    ).fetchone()
    if freezer_item:
        conn.close()
        return {"recommended_defrost_item": freezer_item["item"], "recommended_batch_from_entry_id": None}

    batch_from = conn.execute(
        """
        SELECT id FROM meal_plan_entries
        WHERE household_id = ? AND slot = 'dinner' AND slot_state = 'planned'
          AND date < ? AND component_category IS NULL
        ORDER BY date DESC, id DESC LIMIT 1
        """,
        (household_id(), date_str),
    ).fetchone()
    conn.close()
    if batch_from:
        return {"recommended_batch_from_entry_id": batch_from["id"], "recommended_defrost_item": None}
    return {"recommended_batch_from_entry_id": None, "recommended_defrost_item": None}


def set_slot_recommendation(
    date_str: str, slot: str, batch_from_entry_id: int | None = None, defrost_item: str | None = None,
) -> dict:
    """
    Store (or hand-override) the ready_made recommendation for one slot.
    Setting a new recommendation always resets recommendation_confirmed
    back to 0 — a changed recommendation is a new thing to confirm, never
    the same one silently re-approved. Pass both as None/blank to clear it.
    """
    date.fromisoformat(date_str)
    _validate_slot(slot)
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM slot_needs WHERE household_id = ? AND date = ? AND slot = ?",
        (household_id(), date_str, slot),
    ).fetchone()
    if not existing:
        conn.close()
        raise ValueError(f"No slot need recorded for {date_str} {slot} yet — set one (e.g. 'ready_made') first.")
    conn.execute(
        "UPDATE slot_needs SET recommended_batch_from_entry_id = ?, recommended_defrost_item = ?, "
        "recommendation_confirmed = 0, updated_at = datetime('now') WHERE id = ?",
        (batch_from_entry_id, (defrost_item or "").strip(), existing["id"]),
    )
    conn.commit()
    conn.close()
    return get_slot_need(date_str, slot)


def confirm_slot_recommendation(date_str: str, slot: str, confirmed: bool = True) -> dict:
    """
    Record the household's yes/no on a ready_made recommendation — Emily's
    rule that the system recommends but never acts without confirmation.
    Only flips the flag; the actual defrost reminder / batch-cook nudge
    this unlocks is the separate defrost-flow ticket's territory, and reads
    this flag rather than duplicating it.
    """
    date.fromisoformat(date_str)
    _validate_slot(slot)
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM slot_needs WHERE household_id = ? AND date = ? AND slot = ?",
        (household_id(), date_str, slot),
    ).fetchone()
    if not existing:
        conn.close()
        raise ValueError(f"No slot need recorded for {date_str} {slot} yet.")
    conn.execute(
        "UPDATE slot_needs SET recommendation_confirmed = ?, updated_at = datetime('now') WHERE id = ?",
        (1 if confirmed else 0, existing["id"]),
    )
    conn.commit()
    conn.close()
    return get_slot_need(date_str, slot)


def apply_slot_needs_to_plan(plan_id: int, week_start_date: str, day_count: int = 7) -> dict:
    """
    Enforce every declared slot need against a just-generated plan — the
    generalized form of agent.py:_finish_week_slots' `out`-night pass,
    extended from dinner-only to any slot. Called from that same
    slot-finishing step for every generated week, so the invariant holds
    regardless of whether the model was even told about (or honored) the
    need — exactly the belt-and-suspenders the `out` night already gets:

    - 'away' — CLEAR then write planned_empty, so a slot the household
      will not be home for never carries a real meal or a grocery
      contribution, no matter what got generated. This is the
      non-negotiable half of this ticket, and it does not depend on the
      generation prompt knowing about away slots at all.
    - 'ready_made' — if nothing has been recommended yet (the need was set
      before this plan existed, so set_away_stretch had no plan to look
      at), compute one now with the freshly generated week as context.
      Never overwrites an existing recommendation, confirmed or not — that
      would silently replace something the household may already have
      acted on.
    - 'quick' — recorded for visibility only; nothing here forces the
      model's actual meal choice to be quick. See
      generation_context_for_week's docstring for why that half is a
      documented hook rather than built here.

    Returns a small summary rather than raising — a week that generated
    fine must not fail over enforcing a need on it.
    """
    dates = _week_intake._week_dates(week_start_date)[:day_count]
    needs = get_week_slot_needs(week_start_date)
    away_enforced = []
    ready_made_recommended = []
    for d in dates:
        for slot, info in (needs.get(d) or {}).items():
            if slot not in _SEQUENCE_SLOTS:
                continue
            if info["need"] == "away":
                _weekly_plan.clear_plan_slot(plan_id, d, slot)
                _weekly_plan.plan_slot_empty(
                    weekly_plan_id=plan_id, meal_date=d, slot=slot,
                    reason=info["reason"] or _default_reason("away"),
                    derived_from={"need": "away", "away_stretch_id": info["away_stretch_id"]},
                )
                away_enforced.append({"date": d, "slot": slot})
            elif (
                info["need"] == "ready_made"
                and not info["recommended_batch_from_entry_id"]
                and not info["recommended_defrost_item"]
            ):
                rec = _recommend_ready_made(d, slot)
                if rec["recommended_batch_from_entry_id"] or rec["recommended_defrost_item"]:
                    set_slot_recommendation(
                        d, slot,
                        batch_from_entry_id=rec["recommended_batch_from_entry_id"],
                        defrost_item=rec["recommended_defrost_item"],
                    )
                    ready_made_recommended.append({"date": d, "slot": slot})
    return {"plan_id": plan_id, "away_enforced": away_enforced, "ready_made_recommended": ready_made_recommended}


def generation_context_for_week(week_start_date: str, day_count: int = 7) -> dict:
    """
    Slot needs reshaped for the generator's context dict — the data-layer
    half of "generation respects the needs".

    The prompt-cooperation half is now wired too (agent.py's generation
    prompt has a `slot_needs` bullet): skip 'away' slots, keep 'quick'
    slots inside the same minute cap `rush` night tags use, and cover a
    'ready_made' slot with its stored recommendation instead of a fresh
    cook. That was previously a documented TODO held back to avoid
    colliding with the concurrent streaming rewrite of
    generate_weekly_plan_llm; that work merged (8316a86), so it's closed.

    The 'away' guarantee still does not rest on the model reading any of
    this — apply_slot_needs_to_plan enforces it afterwards either way. What
    the prompt buys is the cooperative half: a genuinely quick meal before
    a departure, and a return meal that leans on the earmark.

    away_slots is a full breakfast/lunch/dinner removal list, the same
    intent as intake's skip_dinner_dates but not limited to dinner (which
    skip_dinner_dates structurally can't express).
    """
    dates = _week_intake._week_dates(week_start_date)[:day_count]
    needs = get_week_slot_needs(week_start_date)
    away, quick, ready_made = [], [], []
    for d in dates:
        for slot, info in (needs.get(d) or {}).items():
            if slot not in _SEQUENCE_SLOTS:
                continue
            entry = {"date": d, "slot": slot, "reason": info["reason"]}
            if info["need"] == "away":
                away.append(entry)
            elif info["need"] == "quick":
                quick.append(entry)
            elif info["need"] == "ready_made":
                ready_made.append({
                    **entry,
                    "recommended_batch_from_entry_id": info["recommended_batch_from_entry_id"],
                    "recommended_defrost_item": info["recommended_defrost_item"],
                    "recommendation_confirmed": info["recommendation_confirmed"],
                })
    return {"away_slots": away, "quick_slots": quick, "ready_made_slots": ready_made}
