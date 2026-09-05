"""
Household coordination and trust: plan conflicts, explaining a choice,
feedback nudges, and who is in the household.
"""
from __future__ import annotations

import re
from ..db import get_conn
from ._shared import household_id
from . import household as _household
from . import memory as _memory
from . import recipes as _recipes
from . import weekly_plan as _weekly_plan


# Words that describe a restriction rather than name the food it is about.
# Two groups, and the second one is the bug fix:
#
#   1. Severity/negation words ("allergy", "free", "no") — these were always
#      filtered, because keeping them would flag every recipe.
#   2. Ordinary sentence glue ("to", "the", "any", "eat", "of", "with"). A
#      structured restriction is a noun phrase ("peanut allergy") and has
#      almost none of this. A freeform What-we-know fact is a whole sentence
#      ("Emily is allergic to pineapple") and is full of it. "to" is the one
#      that actually bit: it survived the old filter, and whole-word matched
#      "salt to taste" — an ingredient in a large share of every recipe
#      collection — so a single allergy fact would have flagged most of the
#      week for the wrong reason. A warning that fires on everything is a
#      warning nobody reads, which for an allergy check is worse than none.
_CONFLICT_STOPWORDS = frozenset({
    "allergy", "allergies", "allergic", "free", "intolerance", "intolerant",
    "sensitivity", "sensitive", "no", "not", "never", "avoid", "avoids",
    "avoiding", "cant", "cannot", "wont", "doesnt", "dont", "must", "only",
    "a", "an", "the", "any", "anything", "everything", "and", "or", "but",
    "of", "with", "without", "to", "is", "are", "am", "be", "been", "was",
    "were", "has", "have", "had", "eat", "eats", "eating", "ate", "food",
    "foods", "in", "on", "at", "for", "from", "it", "its", "this", "that",
    "these", "those", "she", "he", "they", "we", "i", "you", "her", "his",
    "their", "our", "my", "your", "them", "us", "me", "who", "does", "do",
})


def _conflict_keywords(phrase: str, drop: set[str] | None = None) -> list[str]:
    """
    The food words inside a restriction or a freeform fact, normalised for
    whole-word matching. `drop` additionally removes the words of whichever
    member's name the phrase is about, so "Emily is allergic to pineapple"
    can't flag a recipe that happens to be named after Emily.
    """
    cleaned = re.sub(r"[^a-z0-9\s-]", " ", (phrase or "").lower())
    words = [w for w in cleaned.replace("-", " ").split() if len(w) > 1]
    drop = drop or set()
    return [w for w in words if w not in _CONFLICT_STOPWORDS and w not in drop]


def _keyword_variants(keyword: str) -> set[str]:
    """
    A keyword plus its obvious singular/plural twin. A restriction saved as
    "peanuts" has to match an ingredient listed as "peanut butter" — whole-word
    matching alone would miss it, and missing an allergen is the failure this
    check exists to prevent.
    """
    variants = {keyword}
    if keyword.endswith("es") and len(keyword) > 4:
        variants.add(keyword[:-2])
    if keyword.endswith("s") and len(keyword) > 3:
        variants.add(keyword[:-1])
    else:
        variants.add(keyword + "s")
        variants.add(keyword + "es")
    return variants


def _matches(keywords: list[str], text: str) -> str | None:
    """The first keyword that appears in `text` as a whole word, or None."""
    for kw in keywords:
        for variant in _keyword_variants(kw):
            if re.search(r"\b" + re.escape(variant) + r"\b", text):
                return kw
    return None


def _avoidances() -> list[dict]:
    """
    Everything the household has told us to keep off the table, from all
    three places it can live, flattened into one shape.

    Before this, only the first of the three was ever read — so an allergy
    written down as a What-we-know note (which is exactly where add_fact and
    the What-we-know screen put it) was invisible to this check.
    """
    members = _household.list_members()
    member_names = [m["name"] for m in members if (m["name"] or "").strip()]

    out: list[dict] = []
    for m in members:
        name_words = {w for w in re.sub(r"[^a-z0-9\s]", " ", (m["name"] or "").lower()).split()}
        for restriction in m["dietary_restrictions"]:
            if not restriction.strip():
                continue
            out.append({
                "member": m["name"], "label": restriction.strip().lower(),
                "source": "dietary_restriction", "severity": "hard",
                "keywords": _conflict_keywords(restriction, drop=name_words),
            })

    # Hard facts — the What-we-know notes flagged as must-avoid. The person
    # is parsed out of the sentence when one of them is named in it
    # ("Emily is allergic to pineapple"); otherwise the fact stands for the
    # whole household ("no shellfish in this house").
    for fact in _memory.get_facts():
        if not fact.get("hard"):
            continue
        text = fact.get("text") or ""
        lowered = text.lower()
        named = next(
            (n for n in member_names if re.search(r"\b" + re.escape(n.lower()) + r"\b", lowered)),
            None,
        )
        name_words = {w for w in re.sub(r"[^a-z0-9\s]", " ", (named or "").lower()).split()}
        keywords = _conflict_keywords(text, drop=name_words)
        if not keywords:
            continue
        out.append({
            "member": named, "label": text.strip(), "source": "fact",
            "severity": "hard", "keywords": keywords,
        })

    # Standing dislikes are not a safety matter, so they ride along at a
    # lower severity — worth mentioning, never worth alarming about.
    for dislike in _memory.get_household_memory().get("dislikes") or []:
        keywords = _conflict_keywords(dislike)
        if not keywords:
            continue
        out.append({
            "member": None, "label": dislike.strip().lower(), "source": "dislike",
            "severity": "soft", "keywords": keywords,
        })
    return out


def _conflicts_note(conflicts: list[dict]) -> str | None:
    """
    One plain sentence for the draft's review band, or None when there is
    nothing to say. Written here rather than in the UI so the wording lives
    with the data it describes — and only ever about the hard ones, because
    a dislike is a preference, not a warning.
    """
    hard = [c for c in conflicts if c["severity"] == "hard"]
    if not hard:
        return None
    # One dish planned on five nights is one thing to look at, not five, and
    # the sentence should name it rather than retreat into a count.
    distinct = {(c["meal"], c["restriction"]): c for c in hard}
    if len(distinct) > 1:
        meals = len({c["meal"] for c in hard})
        subject = "One meal" if meals == 1 else f"{meals} meals"
        verb = "looks" if meals == 1 else "look"
        return f"{subject} {verb} like a clash with something you’ve asked me to avoid — worth a look before you approve."
    c = next(iter(distinct.values()))
    if c["source"] == "fact":
        return f"{c['meal']} looks like a clash with something you’ve told me — “{c['restriction']}”. Worth a look before you approve."
    if c["member"]:
        return f"{c['meal']} looks like a clash with {c['member']}’s {c['restriction']} — worth a look before you approve."
    return f"{c['meal']} looks like a clash with the {c['restriction']} you’ve asked me to avoid — worth a look before you approve."


def check_plan_conflicts(weekly_plan_id: int | None = None) -> dict:
    """
    Flag (don't block) any meals on a plan that look like they clash with
    something the household has said to keep off the table:

      - a member's saved dietary restriction/allergy
        (set_member_dietary_restrictions),
      - a What-we-know fact marked hard (add_fact with hard=true) — an
        allergy written as a note is still an allergy,
      - a standing household dislike, at a lower severity.

    Matched by keyword against BOTH the meal's name and, when it's a saved
    recipe, its ingredient list. The name matters on its own: "Pineapple
    Chicken" is a clash even if the recipe's ingredients were never filled
    in, and a freeform one-off meal has no ingredients at all — skipping
    those was how the most obvious clash of all went unflagged.

    Still a warning, not a block: the plan can be approved as-is if the
    conflict is intentional or a false positive from the keyword match.
    This runs automatically after generation and again at approval, so a
    warning no longer depends on anyone remembering to ask for one.

    Returns `conflicts` (each with meal/member/restriction/severity/source/
    matched/date/component_category) and `note` — a single ready-to-show
    sentence for the review band, or None when there is nothing to warn about.
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {"weekly_plan_id": None, "conflicts": [], "note": None}

    avoidances = _avoidances()
    if not avoidances:
        return {"weekly_plan_id": plan["weekly_plan_id"], "conflicts": [], "note": None}

    recipes_by_name = {r["name"].lower(): r for r in _recipes.list_recipes()}
    conflicts = []
    for meal in plan["meals"]:
        name = (meal.get("meal") or "").strip()
        # A slot with nothing in it, or one deliberately left empty/open,
        # has no dish to clash with.
        if not name or meal.get("slot_state") in ("planned_empty", "open"):
            continue
        recipe = recipes_by_name.get(name.lower())
        ingredient_text = ""
        if recipe:
            ingredient_text = " ".join((i.get("item") or "") for i in recipe.get("ingredients", []))
        haystack = re.sub(r"[^a-z0-9\s-]", " ", f"{name} {ingredient_text}".lower())
        for avoidance in avoidances:
            matched = _matches(avoidance["keywords"], haystack)
            if not matched:
                continue
            conflicts.append({
                "meal": name,
                "member": avoidance["member"],
                # Kept under the original key so existing callers/readers of
                # this result don't have to change.
                "restriction": avoidance["label"],
                "source": avoidance["source"],
                "severity": avoidance["severity"],
                "matched": matched,
                "date": meal.get("date"),
                "component_category": meal.get("component_category"),
            })
    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "conflicts": conflicts,
        "note": _conflicts_note(conflicts),
    }


def explain_meal_choice(meal_name: str) -> dict:
    """
    Get the full signal picture behind why a meal is/isn't a natural
    suggestion — use when the user asks "why did you suggest this?" or "why
    haven't we had X in a while?" Returns rating, feedback notes, recent
    one-off notes/deviations, times cooked, last cooked date, tags, cuisine,
    main protein, whether it's temporarily excluded, and the household's
    current novelty_preference setting (for context on how much new-recipe
    exposure the plan is aiming for generally).
    """
    try:
        recipe = _recipes.get_recipe(meal_name)
    except ValueError:
        return {"meal_name": meal_name, "found": False, "reason": "Not a saved recipe — likely a freeform/one-off meal with no tracked history."}
    memory = _memory.get_household_memory()
    return {
        "meal_name": recipe["name"],
        "found": True,
        "rating": recipe["rating"],
        "feedback_notes": recipe["feedback_notes"],
        "recent_one_off_notes": recipe["recent_one_off_notes"],
        "times_cooked": recipe["times_cooked"],
        "last_cooked_date": recipe["last_cooked_date"],
        "tags": recipe["tags"],
        "cuisine": recipe["cuisine"],
        "main_protein": recipe["main_protein"],
        "temporarily_excluded": bool(recipe["temporarily_excluded"]),
        "household_novelty_preference": memory.get("novelty_preference", "balanced"),
    }


def get_feedback_nudge() -> dict:
    """
    Check whether there's a good moment to gently ask for feedback on
    something recently cooked — call once near the start of a new
    conversation (not on every message) and, if it returns a meal, work a
    single low-key ask into the response rather than a separate prompt.
    Only surfaces a meal that's been checked off as cooked (check_off_meal)
    in the last 7 days AND whose recipe has never been rated — once a
    recipe has any rating this stops nudging about it.
    """
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COALESCE(r.name, mpe.freeform_meal) AS meal, mpe.recipe_id, mpe.cooked_at
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.cooked_status = 'done' AND mpe.cooked_at IS NOT NULL
          AND mpe.cooked_at >= datetime('now', '-7 days')
          AND mpe.recipe_id IS NOT NULL
          AND (SELECT rating FROM recipes WHERE id = mpe.recipe_id) = ''
        ORDER BY mpe.cooked_at DESC LIMIT 1
        """,
        (household_id(),),
    ).fetchone()
    conn.close()
    if not row:
        return {"has_nudge": False}
    return {"has_nudge": True, "meal": row["meal"], "cooked_at": row["cooked_at"]}


def get_household_people() -> list[dict]:
    """
    The household's adults, each with the avatar initial + color the
    design package uses for "who added it" on desktop grocery rows (the
    People token: first adult spruce #1B3328, second deep apricot #C4703C —
    see db._backfill_member_colors). Powers the identity
    switcher (there's no real login in this app — see FIRST_RUN.md's
    "two adult accounts" note and the Phase 2 judgment call to use a
    lightweight, no-password picker instead) and the avatar rendered next
    to whichever name a grocery item's added_by holds.
    """
    conn = get_conn()
    rows = conn.execute(
        # LOWER(TRIM(...)): age_group is freeform and onboarding writes
        # "Adult", not "adult", so the exact match this used to do returned
        # an empty list for a real household — see db._backfill_member_colors
        # for the same fix and the fuller note.
        "SELECT name, color FROM members WHERE household_id = ? AND LOWER(TRIM(age_group)) = 'adult' ORDER BY id ASC",
        (household_id(),),
    ).fetchall()
    conn.close()
    # A color stored on the row (set by db._backfill_member_colors at the
    # next app restart after this adult was added) always wins; a
    # not-yet-backfilled adult still gets the right color *this* request by
    # falling back to its ordinal position among adults, not a flat
    # default — otherwise a second adult added since the last restart would
    # incorrectly show the first adult's colour instead of the household's
    # second colour.
    # These two values intentionally mirror db._ADULT_COLORS — kept as a
    # literal rather than an import because app.db importing back into
    # app.tools is exactly the cycle _shared.py exists to avoid.
    fallback_colors = ["#1B3328", "#C4703C"]
    out = []
    for i, r in enumerate(rows):
        color = r["color"] or (fallback_colors[i] if i < len(fallback_colors) else "#7E7360")
        out.append({"name": r["name"], "initial": (r["name"].strip()[:1] or "?").upper(), "color": color})
    return out
