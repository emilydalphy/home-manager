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

# The three later-locked rhythm questions (Loop Board "Onboarding: household
# rhythm without traditional assumptions", Emily's "LOCKED: the six rhythm
# questions" note, 2026-09-03) — added additively to the same
# household_rhythm table as the original three above (household-level facts,
# member_name='' and weekday=''), not a schema change.
DINNER_WINDOWS = ("5_6ish", "6_8", "later", "all_over")

# Emily's decision, 2026-09-05 (Loop Board "Rhythm: rename 'When should
# your week be ready?' ... and decide what 'As we go' actually does"):
# planning_anchor is a WEEKDAY the plan and list are final on ("ready by
# Friday" — her example), not an abstract cadence. 'as_we_go' is the one
# non-weekday escape and means something concrete now too: short
# horizons, plan a few days at a time, rather than a whole week (see
# weekly_plan.suggest_planning_period). Replaces the original three-chip
# cadence ('sunday_before'/'midweek'/'as_we_go') — see db._migrate_planning_anchor_values
# for how an existing household's old answer maps onto this.
PLANNING_ANCHOR_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
PLANNING_ANCHORS = PLANNING_ANCHOR_WEEKDAYS + ("as_we_go",)
LEFTOVERS_STANCES = ("love_them", "fine_sometimes", "fresh_each_night")


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


def set_dinner_window(value: str, source: str = "onboarding") -> dict:
    """
    Set when dinner usually lands: '5_6ish', '6_8', 'later', or 'all_over'
    (all over the place, no real pattern). Household-level. Times prep
    schedules, defrost reminders, and "tonight" surfacing around an actual
    target time instead of assuming a default dinner hour.
    """
    if value not in DINNER_WINDOWS:
        raise ValueError(f"value must be one of {DINNER_WINDOWS}, not {value!r}.")
    conn = get_conn()
    _upsert(conn, "", "", "dinner_window", value, "", source)
    conn.commit()
    conn.close()
    _household._log_preference_event("rhythm:dinner_window", "write")
    return {"dinner_window": value}


def set_planning_anchor(value: str, source: str = "onboarding") -> dict:
    """
    Set the day the household wants its plan and list FINAL by: one of
    PLANNING_ANCHOR_WEEKDAYS ('monday' ... 'sunday'), or 'as_we_go' for a
    household that doesn't want a weekly ready day at all. Household-level.

    A weekday value means the week itself starts the NEXT morning — "ready
    by Friday" is a Saturday-start week (see
    weekly_plan.suggest_planning_period, which reads this to seed the
    default plan period and time the planning nudge). 'as_we_go' means
    something concrete, not merely "no answer": short horizons — plan two
    or three days at a time instead of a full week.
    """
    if value not in PLANNING_ANCHORS:
        raise ValueError(f"value must be one of {PLANNING_ANCHORS}, not {value!r}.")
    conn = get_conn()
    _upsert(conn, "", "", "planning_anchor", value, "", source)
    conn.commit()
    conn.close()
    _household._log_preference_event("rhythm:planning_anchor", "write")
    return {"planning_anchor": value}


def planning_anchor_label(value: str) -> str:
    """
    The human-readable form of a stored planning_anchor value, for
    anywhere What We Know or chat needs to say it back plainly: "Ready on
    Friday" for a weekday, or the short-horizon description for
    'as_we_go'. Returns '' for an unset/unrecognized value rather than
    guessing — an unanswered rhythm question should read as unanswered,
    not as a default.
    """
    if value in PLANNING_ANCHOR_WEEKDAYS:
        return f"Ready on {value.capitalize()}"
    if value == "as_we_go":
        return "As we go — planned a few days at a time"
    return ""


def set_leftovers_stance(value: str, source: str = "onboarding") -> dict:
    """
    Set how the household feels about leftovers: 'love_them' (cook once,
    eat twice), 'fine_sometimes', or 'fresh_each_night'. Household-level.
    Powers batch-cooking and ready-made recommendations — see its use in
    generate_weekly_plan_llm's prompt guidance.
    """
    if value not in LEFTOVERS_STANCES:
        raise ValueError(f"value must be one of {LEFTOVERS_STANCES}, not {value!r}.")
    conn = get_conn()
    _upsert(conn, "", "", "leftovers_stance", value, "", source)
    conn.commit()
    conn.close()
    _household._log_preference_event("rhythm:leftovers_stance", "write")
    return {"leftovers_stance": value}


def get_household_rhythm() -> dict:
    """
    Everything on record about the household's standing rhythm: the six
    locked onboarding facts (lunch location per person, meals eaten
    together, who cooks, when dinner lands, when the week should be ready,
    leftovers stance) plus any per-weekday lunch-location overrides learned
    since. Powers the getting-to-know-you hero's Rhythm count, the
    completeness scoring (see memory._build_context_completeness /
    rhythm_completeness_signals below), the packed-lunch default (see
    week_intake.get_week_intake_prefill), and — for dinner_window and
    leftovers_stance — the week/prep generation prompts (see agent.py's
    generate_weekly_plan_llm and generate_prep_schedule_llm).
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM household_rhythm WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()

    lunch_location: dict[str, dict] = {}
    meals_together = None
    cooking_role = None
    dinner_window = None
    planning_anchor = None
    leftovers_stance = None
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
        elif row["fact_type"] == "dinner_window":
            dinner_window = row["value"]
        elif row["fact_type"] == "planning_anchor":
            planning_anchor = row["value"]
        elif row["fact_type"] == "leftovers_stance":
            leftovers_stance = row["value"]

    return {
        "lunch_location": lunch_location,
        "meals_together": meals_together,
        "cooking_role": cooking_role,
        "dinner_window": dinner_window,
        "planning_anchor": planning_anchor,
        "planning_anchor_label": planning_anchor_label(planning_anchor or ""),
        "leftovers_stance": leftovers_stance,
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
    yet for the other's lunches), and whether each of the five remaining
    household-level rhythm facts is set — the three from the original
    build (meals_together, cooking_role) plus the three locked later
    (dinner_window, planning_anchor, leftovers_stance) so a household that
    answered the newer onboarding steps but not the older ones (or vice
    versa) is scored on the honest total, not just the original three.

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
        "dinner_window_set": bool(rhythm["dinner_window"]),
        "planning_anchor_set": bool(rhythm["planning_anchor"]),
        "leftovers_stance_set": bool(rhythm["leftovers_stance"]),
    }
