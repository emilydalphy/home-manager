"""
Household rhythm — Loop Board "Onboarding: household rhythm without
traditional assumptions". This is the structured half of Emily's decided
architecture (Notion, 2026-09-03):

    Rhythm (onboarding, once) -> Exceptions (weekly intake, see
    slot_needs.py) -> Corrections (chat, anytime, permanent)

Three behavior-based questions, never identity labels or traditional
assumptions (no "do you work?", no commuter default, no who-cooks gender
default): where's everyone at lunchtime, which meals are eaten together,
and who cooks. Distinct from the freeform `facts` table's category='rhythm'
notes (see memory.add_fact) — those stay display-only text for the What We
Know screen; this is the structured half generation can actually branch
logic on (see week_intake.get_week_intake_prefill's rhythm-derived packed
lunch suggestion).

Her stated principle behind all of it: "learn more about their rhythm
first as the first important, and their habits, then their preferences —
this is the key benefit of this app." That hierarchy (rhythm -> habits ->
preferences) is what memory._CONTEXT_SIGNALS' weighting reflects.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id
from . import household as _household


LUNCH_LOCATIONS = ("home", "out", "varies")
MEALS_TOGETHER_OPTIONS = ("dinner_only", "dinner_and_breakfast", "most_meals", "varies")
COOKING_ROLES = ("one_person", "turns", "whoever_free")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _upsert(conn, member_name: str, weekday: str, fact_type: str, value: str, who: str, source: str) -> None:
    conn.execute(
        """
        INSERT INTO household_rhythm (household_id, member_name, weekday, fact_type, value, who, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(household_id, member_name, weekday, fact_type) DO UPDATE SET
            value = excluded.value, who = excluded.who, source = excluded.source, updated_at = datetime('now')
        """,
        (household_id(), member_name, weekday, fact_type, value, who, source),
    )


def set_lunch_location(member_name: str, location: str, weekday: str = "", source: str = "onboarding") -> dict:
    """
    Set where a household member typically is at lunchtime: 'home' (a real
    planned meal), 'out' (needs to travel/hold up, i.e. packable), or
    'varies'. Omit weekday for the STANDING answer (what onboarding asks
    once); pass a specific weekday (e.g. 'Tuesday') to record a per-day
    override without touching the standing pattern — this is how a hybrid
    schedule gets learned exactly when it comes up rather than asked
    upfront (Emily's explicit call, 2026-09-03): "Marcus is in the office
    Tuesdays now" is set_lunch_location('Marcus', 'out', weekday='Tuesday').
    Calling this again for the same member (and weekday, if any) is how a
    correction works — it's the same write, not a separate "fix a mistake"
    tool; the newer value simply replaces the older one.
    """
    member_name = (member_name or "").strip()
    if not member_name:
        raise ValueError("member_name is required — lunch location is a per-person fact.")
    if location not in LUNCH_LOCATIONS:
        raise ValueError(f"location must be one of {LUNCH_LOCATIONS}, not {location!r}.")
    if weekday and weekday not in WEEKDAYS:
        raise ValueError(f"weekday must be one of {WEEKDAYS} or blank (for the standing answer), not {weekday!r}.")
    conn = get_conn()
    _upsert(conn, member_name, weekday, "lunch_location", location, "", source)
    conn.commit()
    conn.close()
    field = f"rhythm:lunch_location:{member_name}" + (f":{weekday}" if weekday else "")
    _household._log_preference_event(field, "write")
    return {"member_name": member_name, "weekday": weekday or None, "lunch_location": location}


def clear_lunch_location_override(member_name: str, weekday: str) -> dict:
    """
    Remove a per-weekday lunch-location override, reverting that day back
    to the member's standing answer. Does not touch the standing answer
    itself — to correct that, call set_lunch_location with no weekday.
    """
    member_name = (member_name or "").strip()
    if not weekday:
        raise ValueError(
            "weekday is required to clear an override. To correct the standing answer instead, "
            "call set_lunch_location with the new value and no weekday."
        )
    conn = get_conn()
    conn.execute(
        "DELETE FROM household_rhythm WHERE household_id = ? AND member_name = ? AND weekday = ? AND fact_type = 'lunch_location'",
        (household_id(), member_name, weekday),
    )
    conn.commit()
    conn.close()
    _household._log_preference_event(f"rhythm:lunch_location:{member_name}:{weekday}", "delete")
    return {"member_name": member_name, "weekday": weekday, "cleared": True}


def set_meals_together(value: str, source: str = "onboarding") -> dict:
    """
    Set which meals the household usually eats together: 'dinner_only',
    'dinner_and_breakfast', 'most_meals', or 'varies'. Household-level, not
    per-person — drives portioning and what "a family dinner" means here.
    """
    if value not in MEALS_TOGETHER_OPTIONS:
        raise ValueError(f"value must be one of {MEALS_TOGETHER_OPTIONS}, not {value!r}.")
    conn = get_conn()
    _upsert(conn, "", "", "meals_together", value, "", source)
    conn.commit()
    conn.close()
    _household._log_preference_event("rhythm:meals_together", "write")
    return {"meals_together": value}


def set_cooking_role(value: str, who: str = "", source: str = "onboarding") -> dict:
    """
    Set who does the cooking: 'one_person' (pass who=that person's name),
    'turns', or 'whoever_free'. Household-level. No default is ever assumed
    either way — this is always asked, never guessed at from who answered
    the question.
    """
    if value not in COOKING_ROLES:
        raise ValueError(f"value must be one of {COOKING_ROLES}, not {value!r}.")
    who = (who or "").strip()
    if value == "one_person" and not who:
        raise ValueError("who is required when value='one_person'.")
    if value != "one_person":
        who = ""
    conn = get_conn()
    _upsert(conn, "", "", "cooking_role", value, who, source)
    conn.commit()
    conn.close()
    _household._log_preference_event("rhythm:cooking_role", "write")
    return {"cooking_role": value, "who": who or None}


def get_household_rhythm() -> dict:
    """
    Everything on record about the household's standing rhythm: the three
    onboarding facts plus any per-weekday overrides learned since. Powers
    the getting-to-know-you hero's Rhythm count, the completeness scoring
    (see memory._build_context_completeness / rhythm_completeness_signals
    below), and the packed-lunch default (see
    week_intake.get_week_intake_prefill).
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM household_rhythm WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()

    lunch_location: dict[str, dict] = {}
    meals_together = None
    cooking_role = None
    for row in rows:
        if row["fact_type"] == "lunch_location":
            entry = lunch_location.setdefault(row["member_name"], {"standing": None, "overrides": {}})
            if row["weekday"]:
                entry["overrides"][row["weekday"]] = row["value"]
            else:
                entry["standing"] = row["value"]
        elif row["fact_type"] == "meals_together":
            meals_together = row["value"]
        elif row["fact_type"] == "cooking_role":
            cooking_role = {"value": row["value"], "who": row["who"] or None}

    return {
        "lunch_location": lunch_location,
        "meals_together": meals_together,
        "cooking_role": cooking_role,
    }


def effective_lunch_location(member_name: str, weekday: str) -> str | None:
    """
    A member's lunch location for one specific weekday: the override for
    that day if one's been learned, else the standing answer, else None
    (never asked/answered at all — not the same as 'varies', which is a
    real answer).
    """
    rhythm = get_household_rhythm()["lunch_location"].get((member_name or "").strip())
    if not rhythm:
        return None
    return rhythm["overrides"].get(weekday) or rhythm["standing"]


def rhythm_completeness_signals() -> dict:
    """
    Plain booleans for the completeness scorer (see
    memory._build_context_completeness): whether every ADULT member has a
    standing lunch location on record (not just one of several — a
    household with two adults and only one answered isn't actually known
    yet for the other's lunches), and whether the two household-level
    rhythm facts are set.

    Queries `members` directly rather than going through list_members():
    that helper doesn't return age_group (see household.list_members), and
    memory.get_household_memory's own age_group read does the same direct
    query for the same reason.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT name, age_group FROM members WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()
    adults = [r["name"] for r in rows if (r["age_group"] or "").strip().lower() == "adult"]

    rhythm = get_household_rhythm()
    lunch_location_set = bool(adults) and all(
        (rhythm["lunch_location"].get(name) or {}).get("standing") for name in adults
    )
    return {
        "lunch_location_set": lunch_location_set,
        "meals_together_set": bool(rhythm["meals_together"]),
        "cooking_role_set": bool(rhythm["cooking_role"]),
    }
