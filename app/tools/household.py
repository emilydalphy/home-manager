"""
The household itself: onboarding status, the people in it, and pets.
"""
from __future__ import annotations

import json
from ..db import get_conn
from ._shared import HOUSEHOLD_ID


# Junk "no answer" values that sometimes get written into a restrictions
# list instead of just an empty list — filtered out in
# set_member_dietary_restrictions so they never persist as if they were a
# real restriction (see Phase 4, §4.1 follow-up fix).
_NON_RESTRICTION_VALUES = {"none", "n/a", "na", "no restrictions", "no allergies", "nothing", "no", "-", ""}


def get_household_setup_status() -> dict:
    """
    Check how far onboarding has gotten: whether any members and any chores
    exist yet, plus household basics (pets, goals). Call this at the start
    of a conversation to decide whether to walk the user through setup
    before doing anything else.
    """
    conn = get_conn()
    members = conn.execute(
        "SELECT id, name, age_group FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    pets = conn.execute(
        "SELECT id, name, pet_type FROM pets WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    household = conn.execute(
        "SELECT goals FROM households WHERE id = ?", (HOUSEHOLD_ID,)
    ).fetchone()
    chore_count = conn.execute(
        "SELECT COUNT(*) AS c FROM chores WHERE household_id = ? AND active = 1", (HOUSEHOLD_ID,)
    ).fetchone()["c"]
    conn.close()
    return {
        "has_members": len(members) > 0,
        "members": [dict(m) for m in members],
        "pets": [dict(p) for p in pets],
        "goals": household["goals"] if household else "",
        "has_chores": chore_count > 0,
        "chore_count": chore_count,
        "onboarding_complete": len(members) > 0 and chore_count > 0,
    }


def add_member(name: str) -> dict:
    """Add a household member (person who can be assigned chores)."""
    conn = get_conn()
    member_id = _get_or_create_member(conn, name)
    conn.close()
    return {"member_id": member_id, "name": name}


def list_members() -> list[dict]:
    """List all household members, including any saved dietary restrictions."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, dietary_restrictions_json FROM members WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    return [
        {"id": r["id"], "name": r["name"], "dietary_restrictions": json.loads(r["dietary_restrictions_json"])}
        for r in rows
    ]


def set_member_dietary_restrictions(name: str, restrictions: list[str], replace: bool = False) -> dict:
    """
    Add dietary restrictions/allergies for a household member (e.g.
    ['vegetarian', 'peanut allergy']). Defaults to fetch-then-merge — merges
    with whatever's already saved for this person rather than overwriting
    it, deduplicated case-insensitively — so it's safe to call from a
    one-off mid-conversation mention ("my partner doesn't eat shellfish")
    without risking anything previously saved for them being silently lost
    (Phase 4, §4.1 Fix 2). Pass replace=True only when the person is
    stating their complete, authoritative list right now and anything not
    listed should be dropped — this is the right choice during onboarding
    (initial setup or a full redo), not for an ad hoc mention. Pass an
    empty list with replace=True to clear all restrictions for someone.
    """
    conn = get_conn()
    member_id = _get_or_create_member(conn, name)
    # Placeholder non-answers (an LLM occasionally writes "None" instead of
    # an empty list when someone says they have no restrictions) should
    # never actually get stored as a restriction — filtered out on both
    # merge and replace so this can't silently accumulate as real entries.
    restrictions = [r for r in restrictions if r.strip().lower() not in _NON_RESTRICTION_VALUES]
    if replace:
        merged = list(restrictions)
    else:
        existing_row = conn.execute(
            "SELECT dietary_restrictions_json FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        existing = json.loads(existing_row["dietary_restrictions_json"]) if existing_row else []
        existing = [r for r in existing if r.strip().lower() not in _NON_RESTRICTION_VALUES]
        seen_lower = {r.strip().lower() for r in existing}
        merged = list(existing)
        for r in restrictions:
            if r.strip() and r.strip().lower() not in seen_lower:
                merged.append(r)
                seen_lower.add(r.strip().lower())
    conn.execute(
        "UPDATE members SET dietary_restrictions_json = ? WHERE id = ?",
        (json.dumps(merged), member_id),
    )
    conn.commit()
    conn.close()
    _log_preference_event(f"member:{name}:dietary_restrictions", "write")
    return {"name": name, "dietary_restrictions": merged}


def set_member_age_group(name: str, age_group: str) -> dict:
    """Set a member's general age group (e.g. 'adult', 'teen', 'child', 'toddler', or anything freeform)."""
    conn = get_conn()
    member_id = _get_or_create_member(conn, name)
    conn.execute("UPDATE members SET age_group = ? WHERE id = ?", (age_group, member_id))
    conn.commit()
    conn.close()
    return {"name": name, "age_group": age_group}


def set_household_goals(goals: str) -> dict:
    """Save freeform household goals for using this app (e.g. 'stay on top of chores, eat healthier, waste less food')."""
    conn = get_conn()
    conn.execute("UPDATE households SET goals = ? WHERE id = ?", (goals, HOUSEHOLD_ID))
    conn.commit()
    conn.close()
    _log_preference_event("goals", "write")
    return {"goals": goals}


def _log_preference_event(field: str, action: str) -> None:
    """
    Record one row in the append-only preference_events log — powers the
    Memory view's growth counter ("You've taught me N things this month").
    Every write counts on purpose (Phase 6 PRD §6): a correction is still a
    sign of active engagement, so this doesn't dedupe repeated edits to the
    same field. Called only from the specific correction/addition entry
    points below (not from set_household_meal_preferences directly, which
    onboarding calls in bulk) so getting through onboarding doesn't inflate
    "this month" on day one.
    """
    conn = get_conn()
    conn.execute(
        "INSERT INTO preference_events (household_id, field, action) VALUES (?, ?, ?)",
        (HOUSEHOLD_ID, field, action),
    )
    conn.commit()
    conn.close()


def count_preference_events_this_month() -> int:
    """Count preference-write events (create/update/delete) in the current calendar month — backs the Memory view's growth counter."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM preference_events "
        "WHERE household_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')",
        (HOUSEHOLD_ID,),
    ).fetchone()
    conn.close()
    return row["n"]


def add_pet(name: str, pet_type: str) -> dict:
    """Add a household pet. pet_type is freeform, e.g. 'dog', 'cat', 'rabbit'. Pets can influence chores (litter box, walks) and grocery/household shopping lists (food, litter, etc.)."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO pets (household_id, name, pet_type) VALUES (?, ?, ?)",
        (HOUSEHOLD_ID, name, pet_type),
    )
    conn.commit()
    pet_id = cur.lastrowid
    conn.close()
    return {"pet_id": pet_id, "name": name, "pet_type": pet_type}


def list_pets() -> list[dict]:
    """List all household pets."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, pet_type FROM pets WHERE household_id = ?", (HOUSEHOLD_ID,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_or_create_member(conn, name: str) -> int:
    # Case-insensitive lookup, matching the pattern used for recipes and
    # grocery items elsewhere in this file — an exact-match lookup here let
    # something like "my partner" vs. the saved name "Alex" (or even just
    # "alex" vs. "Alex") silently create a duplicate member row instead of
    # attaching to the existing person (Phase 4, §4.1 Fix 1).
    row = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND LOWER(name) = LOWER(?)", (HOUSEHOLD_ID, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO members (household_id, name) VALUES (?, ?)", (HOUSEHOLD_ID, name))
    conn.commit()
    return cur.lastrowid
