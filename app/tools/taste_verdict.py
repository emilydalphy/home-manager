"""
The SHARED verdict on a dish, for the people actually eating it.

Emily's decision on the Loop Board's "Taste UI: whose verdict?" card
(2026-09-08), in her own words: "If I say I loved it, and then Vineeth
said he hated it, yes you can suggest it on a night that's just for me.
Otherwise, make it a shared verdict, so likely won't make unless there's
an overrule."

So: one hater at the table vetoes the dish for that table, however many
people love it. The overrule isn't a stronger opinion, it's a smaller
table — a night the hater simply isn't eating. That falls straight out of
computing the verdict against a slot's eaters rather than against the
household, which is what every function here does.

This module is deterministic and read-only. It decides nothing about a
plan: generation is nudged by it (see agent's `taste_verdicts` context key
and its prompt bullet), the post-generation check LOGS a soft conflict
from it (see coordination.check_plan_conflicts), and a chat swap reports
it back (see weekly_plan.swap_meal_in_plan). Nothing here blocks anything
— a household that wants to cook the thing one of them hates is allowed
to, exactly as with every other soft warning in the app.

WHAT COUNTS AS A HATE. member_recipe_feedback stores exactly two ratings,
'liked' and 'disliked' (see schema.sql). 'disliked' IS the strong,
permanent verdict here — the prompt already tells generation that "only an
actual 'disliked' rating should exclude a recipe from suggestion," and
that a one-off note (recipe_notes) is deliberately weaker than one. There
is no third, angrier rating to reach for and this module does not invent
one.

WHAT IS UNTOUCHED. The household-level rating (recipes.rating /
feedback_notes, set by mark_recipe_feedback) keeps working exactly as it
does today, for exactly the recipes and people it always did. A recipe
with a household rating and no per-person rows produces a `neutral`
verdict here — "this module knows nothing about who" — never an `avoid`,
so nothing downstream starts double-counting a household dislike as a
personal veto.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id

AVOID = "avoid"
NEUTRAL = "neutral"
FAVOURITE = "favourite"


def _name_of(person_or_recipe) -> str:
    """A member/recipe given as a dict, a row, or already as a name."""
    if isinstance(person_or_recipe, str):
        return person_or_recipe.strip()
    try:
        return str(person_or_recipe["name"]).strip()
    except (TypeError, KeyError, IndexError):
        return str(person_or_recipe).strip()


def per_person_feedback() -> dict[str, dict[str, str]]:
    """
    Every per-person rating in the household, as
    {recipe_name_lower: {member_name: 'liked' | 'disliked'}}.

    One query for the whole household rather than one per dish: a week is
    ~28 slots and a candidate list is every saved recipe, and asking the
    database per (dish, night) pair is how a cheap deterministic check
    turns into hundreds of round trips. Callers that look at more than one
    dish should load this once and pass it into dish_verdict.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT r.name AS recipe_name, m.name AS member_name, mrf.rating
        FROM member_recipe_feedback mrf
        JOIN recipes r ON r.id = mrf.recipe_id
        JOIN members m ON m.id = mrf.member_id
        WHERE mrf.household_id = ?
        """,
        (household_id(),),
    ).fetchall()
    conn.close()
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        out.setdefault(row["recipe_name"].lower(), {})[row["member_name"]] = row["rating"]
    return out


def dish_verdict(recipe, eaters: list, feedback: dict | None = None) -> dict:
    """
    The shared verdict on one dish for one table.

    `recipe` is a recipe dict (anything with a "name") or just its name;
    `eaters` is the people actually at that meal, as member dicts or names
    — attendance.get_slot_attendance's `present_names`, or
    leftovers.eaters_at's underlying attendance, is where those come from.
    `feedback` is per_person_feedback()'s map, loaded here when omitted;
    pass it in when checking more than one dish.

    Returns {"verdict", "reason", "vetoed_by", "loved_by"}:

      * anyone AT THIS MEAL with a 'disliked' row -> "avoid". One hater
        vetoes, regardless of how many people at the same table love it —
        the shared-verdict half of Emily's decision.
      * nobody eating hates it and at least one eater loves it ->
        "favourite". A nudge, not an instruction.
      * everything else, including a dish nobody at this table has ever
        rated -> "neutral", with an empty reason. That is the honest
        answer for a cold start, and it's also the answer for a dish only
        an ABSENT person hates — the solo-night overrule, which needs no
        special case because the hater simply isn't in `eaters`.

    `reason` is written for a person to read (it ends up in a logged soft
    conflict and in a swap's tool result), so it names names.
    """
    name = _name_of(recipe)
    eater_names = [_name_of(e) for e in eaters if _name_of(e)]
    ratings = (per_person_feedback() if feedback is None else feedback).get(name.lower(), {})
    # Case-insensitively, since a name typed into chat ("vineeth") and the
    # stored member row ("Vineeth") are the same person.
    by_lower = {member.lower(): rating for member, rating in ratings.items()}

    vetoed_by = [n for n in eater_names if by_lower.get(n.lower()) == "disliked"]
    loved_by = [n for n in eater_names if by_lower.get(n.lower()) == "liked"]

    if vetoed_by:
        return {
            "verdict": AVOID,
            "reason": f"{_join_names(vetoed_by)} {_dont(vetoed_by)} like {name}.",
            "vetoed_by": vetoed_by,
            "loved_by": loved_by,
        }
    if loved_by:
        return {
            "verdict": FAVOURITE,
            "reason": f"{_join_names(loved_by)} {_love(loved_by)} {name}.",
            "vetoed_by": [],
            "loved_by": loved_by,
        }
    return {"verdict": NEUTRAL, "reason": "", "vetoed_by": [], "loved_by": loved_by}


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _dont(names: list[str]) -> str:
    return "doesn’t" if len(names) == 1 else "don’t"


def _love(names: list[str]) -> str:
    return "loves" if len(names) == 1 else "love"


# ---------- generation: the compact per-table block ----------

def _rated_dishes(feedback: dict) -> list[str]:
    """
    The saved recipes anyone has actually rated personally, by their real
    stored name, in a stable order. Only these can ever produce a non-
    neutral verdict, so only these are worth a line in the prompt — which
    is the whole reason the block stays small on a household with a
    hundred recipes and three opinions.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT name FROM recipes WHERE household_id = ? ORDER BY name ASC",
        (household_id(),),
    ).fetchall()
    conn.close()
    return [r["name"] for r in rows if r["name"].lower() in feedback]


def _table_line(label: str, dishes: list[str], eaters: list[str], feedback: dict) -> str | None:
    """One table's verdicts as a single compact line, or None if it has none."""
    avoid, loved = [], []
    for dish in dishes:
        v = dish_verdict(dish, eaters, feedback=feedback)
        if v["verdict"] == AVOID:
            avoid.append(dish)
        elif v["verdict"] == FAVOURITE:
            loved.append(dish)
    if not avoid and not loved:
        return None
    parts = []
    if avoid:
        parts.append("avoid: " + ", ".join(avoid))
    if loved:
        parts.append("loved: " + ", ".join(loved))
    return f"{label} — " + " | ".join(parts)


def generation_taste_lines(attendance_context: dict) -> list[str]:
    """
    The `taste_verdicts` block handed to generation: one line for the
    whole household's table, plus a line for each slot whose table is
    DIFFERENT and whose verdicts therefore differ too.

    Takes the attendance context that was already built for this
    generation (attendance.context_for_week) rather than re-reading
    attendance per slot — the slots that deviate are exactly the ones it
    already lists, and they are the only ones that can disagree with the
    whole-table line.

    Kept deliberately cheap, because this rides on every generation call
    of every household against a $1/household/month budget:

      * only dishes with real per-person feedback appear at all;
      * a deviating slot whose verdicts match the whole-table line is
        left out — repeating the household answer 27 times would be the
        entire cost of this feature for none of its value;
      * a slot with nothing to say (no avoid, no loved) is left out too;
      * no prose, no JSON — one line per table.

    Returns [] when the household has no per-person feedback at all, which
    is the cold-start case and costs the prompt nothing.
    """
    feedback = per_person_feedback()
    if not feedback:
        return []
    dishes = _rated_dishes(feedback)
    if not dishes:
        return []

    everyone = list(attendance_context.get("household_members") or [])
    lines: list[str] = []
    whole_table = _table_line("whole table", dishes, everyone, feedback)
    if whole_table:
        lines.append(whole_table)

    for slot in attendance_context.get("slots_with_a_different_table") or []:
        present = list(slot.get("present") or [])
        if sorted(n.lower() for n in present) == sorted(n.lower() for n in everyone):
            continue  # a guests-only slot is still the whole household's taste
        label = f"{slot.get('date')} {slot.get('slot')} ({_join_names(present) or 'nobody'})"
        line = _table_line(label, dishes, present, feedback)
        # Only when it actually disagrees with the household answer — a
        # subset night that lands on the same verdicts adds tokens and no
        # information.
        if line and line.split(" — ", 1)[-1] != (whole_table or "").split(" — ", 1)[-1]:
            lines.append(line)
    return lines


# ---------- post-generation: soft conflicts, never a block ----------

def plan_taste_conflicts(meals: list[dict]) -> list[dict]:
    """
    Soft conflicts for a generated plan: every planned dish that somebody
    eating that night is on record as disliking.

    Shaped exactly like coordination.check_plan_conflicts' allergy
    conflicts (meal/member/restriction/severity/source/matched/date/
    component_category) so everything already reading that list — the
    draft payload the Meals screen renders, the post-generation log, the
    approval warning — picks these up with no change of its own. Always
    severity 'soft': a taste veto is a preference, never a safety matter,
    and it must never gate approval the way a hard allergen clash does.

    `meals` is a plan's meals as get_weekly_plan returns them. A slot with
    no dish, or one deliberately left empty/open, has nothing to warn
    about. A meal with no date (a component-based plan's items) is checked
    against the whole household, since there is no one night to ask about.
    """
    from . import attendance as _attendance  # deferred: attendance imports back

    feedback = per_person_feedback()
    if not feedback:
        return []

    everyone = [m["name"] for m in _members()]
    eaters_cache: dict[tuple, list[str]] = {}
    conflicts: list[dict] = []
    for meal in meals:
        name = (meal.get("meal") or "").strip()
        if not name or meal.get("slot_state") in ("planned_empty", "open"):
            continue
        if name.lower() not in feedback:
            continue  # nobody has an opinion on this dish — nothing to check
        date_str, slot = meal.get("date"), meal.get("slot") or "dinner"
        key = (date_str, slot)
        if key not in eaters_cache:
            if not date_str:
                eaters_cache[key] = everyone
            else:
                try:
                    eaters_cache[key] = _attendance.get_slot_attendance(date_str, slot)["present_names"]
                except Exception:
                    eaters_cache[key] = everyone
        verdict = dish_verdict(name, eaters_cache[key], feedback=feedback)
        if verdict["verdict"] != AVOID:
            continue
        for member in verdict["vetoed_by"]:
            conflicts.append({
                "meal": name,
                "member": member,
                # The human-readable half, under the key every existing
                # reader of a conflict already looks at.
                "restriction": f"{member} doesn’t like it",
                "source": "member_taste",
                "severity": "soft",
                "matched": [name],
                "date": date_str,
                "component_category": meal.get("component_category"),
            })
    return conflicts


def _members() -> list[dict]:
    from . import household as _household

    return _household.list_members()


def conflict_sentence(conflict: dict, weekday: str = "") -> str:
    """
    One taste conflict as a line a person would actually say — "Vineeth
    doesn’t like Mushroom Risotto, and he’s home Thursday." Used for the
    post-generation log; the weekday is dropped when there is no date to
    name (a component-based plan).
    """
    who, meal = conflict["member"], conflict["meal"]
    if weekday:
        return f"{who} doesn’t like {meal}, and they’re home {weekday}."
    return f"{who} doesn’t like {meal}."
