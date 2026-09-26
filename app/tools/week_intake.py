"""
The week's intake: the answers to the planning questions, stored as a
first-class object rather than as chat history.
"""
from __future__ import annotations

import json
import re
import sqlite3  # for IntegrityError -- see save_week_intake's retry loop
from datetime import date, timedelta
from ..db import get_conn
from ._shared import acting_name, household_id
from . import rhythm as _rhythm
from . import weekly_plan as _weekly_plan
from . import holidays as _holidays
from . import weekday_lunches as _weekday_lunches
from . import bring_over as _bring_over
from .time_caps import RUSH_MAX_MINUTES, WEEKDAY_LUNCH_MAX_MINUTES  # noqa: F401


# The answers to the two question screens, as a first-class object rather
# than as chat history. Read DATA_MODEL.md before changing anything here:
# the append-only revisions, the two snapshots, and the per-slot provenance
# all exist to enable things that cannot be retrofitted afterwards.

# Closed set. Each tag has exactly one planning consequence — if two tags
# would produce the same behaviour, one of them shouldn't exist.
NIGHT_TAGS = {
    # Ordinary cooked dinner. Mutually exclusive with the others. Exists so
    # someone working down the list can AFFIRM a night rather than skip it:
    # an affirmed night is data, a skipped night is a guess.
    "normal": "plan a normal dinner you cook that evening.",
    # Dinner slot planned empty, and nothing for it reaches the shopping
    # list. The only deliberately empty slot in a week.
    "out": "plan nothing and buy nothing for this night.",
    # Not a vague "busy" flag: a hard cap on cook time AND a trigger for
    # cook-once-eat-twice. Dinner only, 30 minutes including prep (Emily,
    # 2026-09-23) — RUSH_MAX_MINUTES, see time_caps.
    "rush": f"dinner in {RUSH_MAX_MINUTES} minutes or less, prep included, or make the night before stretch to cover it.",
    # Opens the guest follow-up. Scales recipe AND shopping quantities.
    "guests": "scale the recipe and the shopping to the bigger table.",
    # No new dinner; the previous night's batch is increased instead.
    "left": "cook enough the night before instead of planning something new.",
    # The opposite of `rush`, and the only tag that RAISES a limit rather
    # than lowering one: the household's standing weeknight cap does not
    # apply to this dinner. Permits a longer recipe; never demands one
    # (Emily, 2026-09-10: "permit, don't push"). Combinable with `guests`
    # — that one scales quantity, this one lifts effort — but never with
    # `rush`, which it contradicts.
    "unrushed": "let this dinner take longer than a weeknight usually gets.",
}


# What each mood pill means to the planner (Emily, 2026-09-04: "the label
# the user taps should map to a detailed prompt behind the scenes — not the
# literal two words"). The label is what a person taps; this is what the
# generator reads (agent.py's intake context carries `mood_guidance`). A
# mood with no entry here is passed through as its own two words.
MOOD_GUIDANCE = {
    "Something warm": "lean towards warming, comforting dishes — soups, stews, braises, bakes — without making every night heavy.",
    "Lighter than usual": "lean lighter: more vegetables and salads, lighter proteins like fish and chicken, fewer rich sauces and fried things, smaller starch portions.",
    "Comfort food": "lean towards the household's comfort dishes — familiar, generous, satisfying — a couple of nights, not every night.",
    "On the grill": "put two or three dinners on the grill or under the broiler where the weather and the recipes allow.",
    "Protein-heavy": "make protein the centre of most meals — 30g+ per adult serving at lunch and dinner from meat, fish, eggs, dairy or legumes — and keep starches modest.",
    "Veggie-heavy": "make vegetables the bulk of the plate at most meals — at least two vegetables per dinner, vegetable-forward lunches — with protein and starch in supporting roles; not necessarily vegetarian.",
    "Fibre-focused": "favour high-fibre ingredients across the week — whole grains, beans and lentils, vegetables with skins, fruit — and say so in a slot's reasoning where it drove the choice.",
    "Something new": "include two or three dishes the household has not had before, alongside familiar ones, never a whole week of unknowns.",
    "Try a new cuisine": "pick one cuisine the household has not cooked recently (check what they usually eat) and build two dinners from it, with the rest of the week familiar.",
    "Keep it cheap": "favour inexpensive proteins and pantry staples, batch cooking, and ingredients that stretch across several meals.",
    # The card at the top of the mood screen (Emily, 2026-09-21, "Surprise
    # me is a card at the top"): a real answer, not the absence of one.
    # Saved as the one mood so the planner is told, in so many words, that
    # the household handed the lean over — and so the building screen and
    # next week's prefill can read it back as what they chose.
    "Surprise me": "the household asked to be surprised: no lean this week — pick from what they like, keep the week varied, and lead with something they haven't had lately.",
}

# The mood the Surprise me card records. Never combined with a steering
# mood: the screen drops it the moment a mood chip is tapped, and a save
# carrying both would be two answers to one question.
SURPRISE_MOOD = "Surprise me"


# What every meal on a day tapped off "Which days?" says for itself once
# the week is drafted (2026-09-21, board D1). Written as the planned_empty
# reason by agent._finish_week_slots, and the menu titles those slots "Not
# planned" (weekly_plan.get_week_menu reads the constraint below) — never
# "Away": nobody is travelling, the day was left out on purpose.
SKIPPED_DAY_REASON = "Not planned — you left this day out."
SKIPPED_DAY_CONSTRAINT = "skipped_day"


# The hard cap a `rush` night imposes, in minutes, and the weekday
# fresh-lunch cap beside it. Named rather than inlined because the
# acknowledgement copy, the generator prompt and the draft screen's
# per-slot reasons all have to agree on the same number. Both are written
# in time_caps (it imports nothing from the app, so weekly_plan can read
# them at import time without a cycle through this module) and are
# re-exported here, at the top, where every caller has always found
# RUSH_MAX_MINUTES.


def _week_dates(week_start: str) -> list[str]:
    return period_dates(week_start, 7)


# ---------- what a typed request reaches ----------
# Emily, 2026-09-20, from her phone: "I gave it a detailed description —
# Mexican for lunch, chicken breast, potatoes and veggies for dinner — and
# it didn't listen." Only Monday got both. The free-text answer reached the
# drafting prompt as one opaque string with a rule to "put it exactly where
# they said", and a request that names a MEAL and no DAY has no "where" for
# that rule to bite on — so the model satisfied it once and moved on.
#
# freeform_meal_scopes makes the reach explicit for the ONE case where a
# sentence leaves no doubt: a meal word, no day, no count, no range, no
# "no". Such a request applies to every slot of that meal in the period,
# and the result rides into the generation context as intake.freeform_scope
# next to the words themselves (agent._intake_generation_context). Every
# other sentence — a count ("twice"), a day or range ("Mon–Thu"), an
# exclusion ("no fish"), a ramble — is left to the model with the plain
# rules the prompt spells out, and gets NO scope here: a regex is the wrong
# tool for natural language, and a wrong scope handed to the model as fact
# is worse than none (the verifier's table, 2026-09-21).

_MEAL_WORDS = {
    "breakfast": ("breakfast", "breakfasts", "brunch"),
    "lunch": ("lunch", "lunches", "lunchtime"),
    "dinner": ("dinner", "dinners", "supper", "suppers"),
    "snack": ("snack", "snacks"),
}
_MEAL_WORD_RE = re.compile(
    r"\b(" + "|".join(w for words in _MEAL_WORDS.values() for w in words) + r")\b", re.IGNORECASE
)
_MEAL_OF_WORD = {w: meal for meal, words in _MEAL_WORDS.items() for w in words}

# Any of these in a sentence and the sentence is the model's to scope.
_DAY_WORD_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"
    r"|weekend|weekends|weeknight|weeknights|weekday|weekdays|tonight|today|tomorrow|night|nights|evening|evenings"
    r"|morning|mornings|day|days)\b",
    re.IGNORECASE,
)
_COUNT_WORD_RE = re.compile(
    r"\b(once|twice|thrice|one|two|three|four|five|six|seven|couple|few|several|some|every other|\d+|\d+x)\b",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(no|not|nothing|never|none|without|skip|avoid|don't|dont|do not|less|fewer|only)\b|\bn't\b",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(r"[–\-]|\bto\b|\bthrough\b|\bthru\b", re.IGNORECASE)
_REQUEST_SPLIT_RE = re.compile(r"[.;!?\n]+")
# A request longer than this is a paragraph, not an instruction to scope.
_MAX_REQUEST_WORDS = 16


def _clauses_by_meal(request: str) -> list[tuple[str, str]]:
    """
    One (clause, meal) per meal word in a clean request, in order. "Mexican
    for lunch, chicken breast, potatoes and veggies for dinner" is two: the
    words up to and including each meal word belong to that meal; whatever
    trails the last one ("for dinner I want chicken") stays with it.
    """
    matches = list(_MEAL_WORD_RE.finditer(request))
    out: list[tuple[str, str]] = []
    start = 0
    for i, m in enumerate(matches):
        end = m.end() if i + 1 < len(matches) else len(request)
        clause = request[start:end].strip(" ,;:-–—\t")
        if clause:
            out.append((clause, _MEAL_OF_WORD[m.group(1).lower()]))
        start = m.end()
    return out


def _unambiguous(request: str) -> bool:
    words = request.split()
    if not words or len(words) > _MAX_REQUEST_WORDS:
        return False
    if _DAY_WORD_RE.search(request) or _COUNT_WORD_RE.search(request):
        return False
    if _NEGATION_RE.search(request) or _RANGE_RE.search(request):
        return False
    return True


def freeform_meal_scopes(text: str | None, dates: list[str]) -> list[dict]:
    """
    The meal-type requests whose reach is beyond doubt, each in the
    household's own words with every date of that meal in the period.
    Only a sentence with a meal word and no day, no count, no range and no
    negation qualifies; anything else gets no entry and is the model's to
    scope by the prompt's rules (never an exclusion — "no fish for dinner"
    is left out, not handed over as a request).
    """
    scopes: list[dict] = []
    for request in _REQUEST_SPLIT_RE.split(text or ""):
        request = request.strip(" ,;")
        if not request or not _unambiguous(request):
            continue
        for clause, meal in _clauses_by_meal(request):
            scopes.append({"words": clause, "meal": meal, "applies_to": "every", "dates": list(dates)})
    return scopes


# ---------- a typed INGREDIENT request ----------
#
# "I have some corn so incorporate that into a meal" (Emily, 2026-09-21:
# no dish that week had corn in it, and nothing said so). The same stance
# as freeform_meal_scopes above: only the shapes that are beyond doubt
# get parsed — "I have some X", "there's X in the freezer", "use (up) the
# X", "incorporate X" — and only when X is a food this app knows. A
# sentence with a negation in it is left alone entirely ("I don't have
# corn"; "use the lamb, not the chicken"), because a wrong must-use handed
# on as fact is worse than none. Everything else is the model's to read.

_ING_LEAD_RE = re.compile(
    r"(?:\b(?:i|we)(?:'ve| have| got|'ve got| have got)\s+(?:got\s+)?"
    r"(?:a lot of|lots of|loads of|plenty of|too much|too many|some|a few|a couple of|a bag of|"
    r"a can of|a bunch of|a box of|a pack of|a packet of|a head of|a tub of|a jar of|half a|"
    r"extra|leftover|spare|a|an|the)?"
    r"|\b(?:there's|there is|there are)\s+(?:some|a few|a lot of|lots of|a|an|the)?"
    r"|\b(?:use up|use|incorporate|work in|finish off|finish|include)\s+"
    r"(?:the rest of the|the last of the|up the|all the|all of the|the|some|my|our|that|those|these)?)"
    r"\s*(?P<item>[a-z][a-z\-']*(?:\s+[a-z][a-z\-']*){0,2})",
    re.IGNORECASE,
)
# Where the ingredient's name stops. "corn so incorporate" → corn; "lamb in
# the freezer" → lamb; "chicken breast this week" → chicken breast.
_ING_STOP_WORDS = {
    "so", "and", "that", "which", "to", "in", "for", "this", "from", "on", "at", "with", "into",
    "i", "we", "it", "they", "needs", "need", "is", "are", "was", "were", "before", "left", "sitting",
    "going", "as", "but", "or", "if", "because", "since", "up", "please", "too", "also", "again",
    "somewhere", "somehow", "of", "by", "them", "those", "these", "there", "here", "week", "tonight",
    "today", "tomorrow", "coming", "over", "leftover", "leftovers", "lying", "around", "already",
    "still", "some", "more", "any", "a", "an", "the", "my", "our",
}
# Words that describe the ingredient without being it.
_ING_MODIFIERS = {"fresh", "frozen", "leftover", "spare", "extra", "cooked", "raw", "ripe", "big", "little", "small",
                  "large", "whole", "half", "lot", "lots", "bag", "can", "bunch", "box", "pack", "packet", "head",
                  "tub", "jar", "few", "couple", "nice", "good", "great"}


def _ingredient_words(item: str) -> list[str]:
    words = []
    for w in re.findall(r"[a-z][a-z\-']*", item.lower()):
        if w in _ING_STOP_WORDS:
            break
        words.append(w)
    while words and words[-1] in _ING_MODIFIERS:
        words.pop()
    while words and words[0] in _ING_MODIFIERS:
        words.pop(0)
    return words[:3]


def freeform_ingredient_requests(text: str | None) -> list[dict]:
    """
    The ingredients the household typed that they want USED this week,
    each with the sentence it came from: [{"words": "I have some corn so
    incorporate that into a meal", "ingredient": "corn"}]. Only the
    unmistakable shapes (see the note above), only foods this app knows,
    never a sentence with a negation in it; "I have guests Friday" and
    "I have some time on Sunday" yield nothing. One entry per ingredient.
    """
    from . import plan_quality as _plan_quality  # lazy: it pulls in the heavier half of the package
    known = _plan_quality._known_food_words()
    out: list[dict] = []
    seen: set[str] = set()
    for sentence in _REQUEST_SPLIT_RE.split(text or ""):
        sentence = sentence.strip(" ,;")
        if not sentence or _NEGATION_RE.search(sentence):
            continue
        for m in _ING_LEAD_RE.finditer(sentence):
            words = _ingredient_words(m.group("item"))
            if not words or not any(_plan_quality._stem(w) in known for w in words):
                continue
            ingredient = " ".join(words)
            if ingredient in seen:
                continue
            seen.add(ingredient)
            out.append({"words": sentence, "ingredient": ingredient})
    return out


def period_dates(start_date: str, day_count: int = 7) -> list[str]:
    """
    The ISO dates of a planning period: `day_count` days from `start_date`,
    inclusive. `_week_dates` is this with day_count pinned to 7.

    Exists because the old idiom for "the days of a shorter window" was
    `_week_dates(start)[:day_count]`, which silently CAPS at seven — fine
    while every period was a Monday week or a part of one, wrong the moment
    a household plans Thursday to next Thursday (Loop Board "Planning
    periods, not weeks"). That slice returned 7 days for an 8-day period and
    the eighth day would have been generated for, never audited, and never
    rendered. Slicing a list can't express a window longer than the list, so
    the window is built at the length it actually is.

    day_count below 1 yields no dates rather than raising: a plan that has
    surrendered every one of its days to a newer period (see
    retire_overlapping_plans) is a real, queryable row with an empty window,
    and every caller here loops over the result.
    """
    start = date.fromisoformat(start_date)
    return [(start + timedelta(days=i)).isoformat() for i in range(max(0, day_count))]


def _household_composition() -> dict:
    """
    How many adults and children are on record, for the guest maths and the
    preferences snapshot. Counted from members.age_group, which is freeform
    — anything that isn't recognisably an adult or a child is left out of
    both counts rather than guessed into one.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT age_group FROM members WHERE household_id = ?", (household_id(),)
    ).fetchall()
    conn.close()
    adults = children = 0
    for r in rows:
        group = (r["age_group"] or "").strip().lower()
        if group == "adult":
            adults += 1
        elif group in ("child", "teen", "toddler", "kid"):
            children += 1
    return {"adults": adults, "children": children}


def _build_preferences_snapshot(conn) -> dict:
    """
    A COPY of the preference set a plan was generated under, taken at
    generation time. Not a reference: if a plan only points at live
    preferences then the day someone edits "won't eat", every past plan's
    reasoning becomes unreadable and unreproducible — you can no longer tell
    whether a strange choice was a bug or a preference that has since
    changed. A few hundred bytes buys that.
    """
    prefs = conn.execute(
        "SELECT * FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    if not prefs:
        return {}
    return {
        "meal_counts": {
            "breakfasts": prefs["breakfasts_per_week"],
            "lunches": prefs["lunches_per_week"],
            "dinners": prefs["dinners_per_week"],
        },
        "wont_eat": json.loads(prefs["dislikes_json"]),
        "protein": json.loads(prefs["protein_preferences_json"]),
        "weeknight_max_minutes": prefs["weeknight_max_minutes"],
        "cooking_time_preference": prefs["cooking_time_preference"],
        "repeats": prefs["repeats_tolerance"],
        "kit": json.loads(prefs["kitchen_kit_json"]),
        "cuisines": json.loads(prefs["cuisine_preferences_json"]),
        "table_style": prefs["table_style"],
        "eating_style": prefs["eating_style"],
        "novelty": prefs["novelty_preference"],
        "typical_week": prefs["typical_week"],
    }


def _intake_row_to_dict(row) -> dict:
    return {
        "intake_id": row["id"],
        "week_start": row["week_start"],
        "revision": row["revision"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "night_tags": json.loads(row["night_tags_json"]),
        "guest_counts": json.loads(row["guest_counts_json"]),
        "packed_lunch_days": json.loads(row["packed_lunch_days_json"]),
        "skipped_days": json.loads(row["skipped_days_json"] or "[]"),
        # Step 3, "Weekday lunches" — {} when not answered. The shape is
        # tools/weekday_lunches.normalize's; see that module.
        "weekday_lunches": _weekday_lunches.load(row["weekday_lunches_json"]),
        # "Bring over from last week" (2026-09-25) — [] when nothing was.
        # The shape is tools/bring_over.resolve's; see that module.
        "brought_over": json.loads(row["brought_over_json"] or "[]"),
        "moods": json.loads(row["moods_json"]),
        "cuisines": json.loads(row["cuisines_json"]),
        "freeform": row["freeform"],
        "household_snapshot": json.loads(row["household_snapshot_json"]),
        "preferences_snapshot": json.loads(row["preferences_snapshot_json"]),
    }


def _current_intake_row(conn, week_start: str):
    """
    The current intake for a week: the highest revision that hasn't been
    superseded. Never "the most recent row" — a superseded revision can
    have a later created_at than nothing at all, and the whole point of
    append-only is that old rows stay.
    """
    return conn.execute(
        "SELECT * FROM week_intake WHERE household_id = ? AND week_start = ? AND superseded_at IS NULL "
        "ORDER BY revision DESC LIMIT 1",
        (household_id(), week_start),
    ).fetchone()


def get_week_intake(week_start: str) -> dict | None:
    """
    The answers currently in force for one week, or None if nobody has
    started. Use this rather than reading week_intake directly — it applies
    the "highest revision, not superseded" rule that append-only depends on.
    """
    conn = get_conn()
    row = _current_intake_row(conn, week_start)
    conn.close()
    return _intake_row_to_dict(row) if row else None


def get_week_intake_history(week_start: str) -> list[dict]:
    """
    Every revision of a week's answers, oldest first — what makes "the week
    you had before you redid it" recoverable. Nothing in the UI reads this
    yet; it exists because the data is only collectable as it happens.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM week_intake WHERE household_id = ? AND week_start = ? ORDER BY revision ASC",
        (household_id(), week_start),
    ).fetchall()
    conn.close()
    return [_intake_row_to_dict(r) for r in rows]


def save_week_intake(
    week_start: str,
    night_tags: dict | None = None,
    guest_counts: dict | None = None,
    packed_lunch_days: list | None = None,
    moods: list | None = None,
    cuisines: list | None = None,
    freeform: str | None = None,
    created_by: str = "",
    day_count: int = 7,
    skipped_days: list | None = None,
    weekday_lunches: dict | None = None,
    brought_over: list | None = None,
) -> dict:
    """
    Record the household's answers for a week, as a NEW REVISION.

    Append-only, always: a week_intake row is never updated in place. This
    call copies the current revision, applies whichever answers were passed,
    and inserts the result as revision+1, stamping superseded_at on the one
    it replaced. Arguments left as None are inherited unchanged — that's how
    Q1 and Q2 each save their own half without either clobbering the other,
    and how a chat instruction can change one answer without restating the
    rest.

    Every caller that changes an ANSWER must come through here. The rule of
    thumb is: would this have changed if they'd said it during the
    questions? If yes, it's a new revision. If chat edited only the plan and
    not the intake, "Try again" would regenerate from stale answers and
    silently revert whatever the household just said.

    Both snapshots are retaken on every revision, so each one records what
    was true when that answer was given.

    day_count is the length of the PERIOD these answers are for (Loop Board
    "Planning periods, not weeks"), defaulting to the seven this always
    assumed. It is not cosmetic: the in-range check below is the reason a
    household planning Thursday to next Thursday could not tag the eighth
    day of their own period at all — the save was refused outright, so
    question 1 could not be answered and the whole flow stopped. Found by
    running the round trip, not by reading the code.

    skipped_days are the days tapped off "Which days?" (2026-09-21, board
    D1): ISO dates inside the period, and never every day of it — a period
    with nothing to plan is not a period. Whichever revision holds them,
    the day-keyed answers for those days (night tags, guest counts, packed
    lunches) are dropped from the revision saved: a day that isn't planned
    has nothing to say about its dinner, and the building screen reads the
    saved answers back as fact.

    weekday_lunches is step 3's answer (2026-09-25): {"days": [{"date",
    "kind"}], "prep_days": [...]} from the screen, stored in the shape
    tools/weekday_lunches.normalize writes. A new answer is checked
    strictly; one carried forward from the revision before is re-read
    against this revision's period and skipped days, and a day that no
    longer fits is dropped rather than refusing the save.

    brought_over is "Bring over from last week" (2026-09-25): the rows of
    get_week_intake_prefill's `last_week_uncooked` the household ticked,
    each naming its entry_ids. What is stored is rebuilt from that offer
    (tools/bring_over.resolve), never taken from the request; [] clears it.
    """
    date.fromisoformat(week_start)  # fail loudly on a malformed week
    if brought_over is not None:
        brought_over = _bring_over.resolve(brought_over, week_start)
    period = period_dates(week_start, day_count)
    week_days = set(period)
    if weekday_lunches is not None:
        # Strict, before anything is read: a malformed answer is the
        # client error it is, not a revision.
        _weekday_lunches.normalize(weekday_lunches, period, skipped_days or [])
    if skipped_days is not None:
        if not isinstance(skipped_days, list) or not all(isinstance(d, str) for d in skipped_days):
            raise ValueError("skipped_days must be a list of ISO dates.")
        for day in skipped_days:
            date.fromisoformat(day)
            if day not in week_days:
                raise ValueError(
                    f"{day} isn't in the {day_count}-day period starting {week_start}."
                )
        skipped_days = sorted(set(skipped_days))
        if len(skipped_days) >= len(week_days):
            raise ValueError("At least one day has to stay in the plan.")
    for day, tags in (night_tags or {}).items():
        date.fromisoformat(day)  # keyed by ISO date, never by weekday
        # A tag on a date outside the PERIOD it's being saved for would
        # produce a planned_empty entry stranded outside the plan's days —
        # visible nowhere, and impossible to clear from any screen. The
        # check stays; only its idea of the window widened.
        if day not in week_days:
            raise ValueError(
                f"{day} isn't in the {day_count}-day period starting {week_start}."
            )
        # A bare string here would iterate its characters and report
        # "Unknown night tag(s): r, u, s, h"; a None would raise a TypeError
        # and surface as a 500. Both are only reachable by hand-written API
        # calls, and both should read as the client error they are.
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError(f"Tags for {day} must be a list of tag names.")
        unknown = [t for t in tags if t not in NIGHT_TAGS]
        if unknown:
            raise ValueError(f"Unknown night tag(s): {', '.join(unknown)}.")
        # "Regular night" is mutually exclusive — affirming a night and
        # constraining it are different answers, and holding both would
        # leave the generator with no way to tell which the household meant.
        if "normal" in tags and len(tags) > 1:
            raise ValueError("'normal' is exclusive — a regular night can't also carry another tag.")
        # `rush` caps the dinner at RUSH_MAX_MINUTES; `unrushed` lifts the
        # cap. Holding both would make the generator pick which promise to
        # break, and the household was shown both.
        if "rush" in tags and "unrushed" in tags:
            raise ValueError("A night can't be both short on time and unrushed — pick one.")

    # Read-modify-write, retried. Two adults saving at the same moment both
    # read the same current revision and both try to write revision+1; the
    # UNIQUE index on (household_id, week_start, revision) makes the loser's
    # insert fail rather than letting it land as a second live row with the
    # first one's answers silently lost. Re-reading and retrying merges the
    # loser's answers on top of the winner's — which is what "the second
    # adult joins the first one's intake" has to mean when they arrive
    # together rather than an hour apart.
    for attempt in range(5):
        conn = get_conn()
        try:
            current = _current_intake_row(conn, week_start)
            base = _intake_row_to_dict(current) if current else {
                "night_tags": {}, "guest_counts": {}, "packed_lunch_days": [],
                "skipped_days": [], "moods": [], "cuisines": [], "freeform": "",
                "weekday_lunches": {}, "brought_over": [],
            }

            def pick(new, key, _base=base):
                return _base[key] if new is None else new

            # A day left out of the plan carries no answer about its meals,
            # whichever save brought the two together (see the docstring).
            skipped = set(pick(skipped_days, "skipped_days"))
            night_tags_saved = {d: t for d, t in pick(night_tags, "night_tags").items() if d not in skipped}
            guest_counts_saved = {d: c for d, c in pick(guest_counts, "guest_counts").items() if d not in skipped}
            packed_saved = [d for d in pick(packed_lunch_days, "packed_lunch_days") if d not in skipped]
            lunches_saved = _weekday_lunches.normalize(
                pick(weekday_lunches, "weekday_lunches"), period, sorted(skipped), strict=False,
            )

            household_snapshot = _household_composition()
            preferences_snapshot = _build_preferences_snapshot(conn)
            # NOT current["revision"] + 1: current is the highest UNSUPERSEDED
            # revision, and clear_week_intake can supersede the current row
            # with no replacement (Loop Board, 2026-09-25 review) — the next
            # save after a clear would then see current = None and try
            # revision 1 again, which the UNIQUE (household_id, week_start,
            # revision) index already holds (superseded, but still there),
            # 500ing every save for that week forever. The next revision is
            # always one past the highest revision EVER written for this
            # week, superseded or not.
            max_revision = conn.execute(
                "SELECT MAX(revision) AS m FROM week_intake WHERE household_id = ? AND week_start = ?",
                (household_id(), week_start),
            ).fetchone()["m"]
            revision = (max_revision or 0) + 1
            if current:
                conn.execute(
                    "UPDATE week_intake SET superseded_at = datetime('now') WHERE id = ?",
                    (current["id"],),
                )
            cursor = conn.execute(
                """
                INSERT INTO week_intake (
                    household_id, week_start, revision, created_by,
                    night_tags_json, guest_counts_json, packed_lunch_days_json,
                    skipped_days_json, moods_json, cuisines_json, freeform,
                    household_snapshot_json, preferences_snapshot_json,
                    weekday_lunches_json, brought_over_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    household_id(), week_start, revision,
                    # The adult on this device, when the caller did not name
                    # one; else the name given; else whoever started the
                    # revision before (see _shared.acting_name).
                    (acting_name(created_by) or (current["created_by"] if current else "")).strip(),
                    json.dumps(night_tags_saved),
                    json.dumps(guest_counts_saved),
                    json.dumps(packed_saved),
                    json.dumps(sorted(skipped)),
                    json.dumps(pick(moods, "moods")),
                    json.dumps(pick(cuisines, "cuisines")),
                    pick(freeform, "freeform"),
                    json.dumps(household_snapshot),
                    json.dumps(preferences_snapshot),
                    json.dumps(lunches_saved),
                    json.dumps(pick(brought_over, "brought_over")),
                ),
            )
            conn.commit()
            intake_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM week_intake WHERE id = ?", (intake_id,)).fetchone()
            saved = _intake_row_to_dict(row)
            _sync_guest_attendance(saved)
            return saved
        except sqlite3.IntegrityError:
            # Somebody took this revision number between our read and our
            # write. Undo our supersede and start again from what they left.
            conn.rollback()
            if attempt == 4:
                raise
        finally:
            conn.close()


def clear_week_intake(week_start: str) -> dict:
    """
    "Start over"'s third option (app/tools/reset.py, POST /api/reset):
    clears this ONE week's answers to the planning questions, and nothing
    else. Household setup — meal_preferences, members, the kitchen kit,
    everything the Preferences sheet holds — was never in week_intake and
    is untouched; this only ever reaches the rows this module itself
    writes, keyed to this one week_start.

    Not a delete: the current revision is marked superseded_at with no
    replacement inserted, the same half of save_week_intake's
    read-modify-write that retires an old revision, just without a new
    one landing on top of it. get_week_intake(week_start) then answers
    None — "nobody has started" — exactly as if the questions had never
    been opened for this week, while every earlier revision stays on the
    table for history's sake, append-only as ever.

    A week with no revision on file yet returns cleared: False — there
    was nothing to clear — same shape as get_reset_preview's other counts
    answering 0.
    """
    date.fromisoformat(week_start)  # fail loudly on a malformed week
    conn = get_conn()
    current = _current_intake_row(conn, week_start)
    if not current:
        conn.close()
        return {"week_start": week_start, "cleared": False}
    conn.execute(
        "UPDATE week_intake SET superseded_at = datetime('now') WHERE id = ?",
        (current["id"],),
    )
    conn.commit()
    conn.close()
    return {"week_start": week_start, "cleared": True}


def _sync_guest_attendance(intake: dict) -> None:
    """
    Push the "Hosting guests" answers into attendance, so a bigger table is
    the SAME fact as a smaller one rather than a parallel notion of it.

    Emily's deepened model unifies guests into attendance: members present
    ± guests. Without this, "hosting 3 on Saturday" would live only in
    week_intake.guest_counts_json, where the grocery path can't see it —
    which is exactly how the guests chip's "and shop for that" promise
    ends up depending entirely on the model choosing to write bigger
    quantities. Writing it here means the headcount reaches groceries
    structurally, the same way a presence toggle does.

    Only dinner: the guest steppers are a night-tag follow-up, and a night
    tag is a dinner concept in this app. Deliberately tolerant of failure —
    a household mid-onboarding may have no members yet, and an intake save
    must not fail over a headcount echo.
    """
    from . import attendance as _attendance

    for day, extras in (intake.get("guest_counts") or {}).items():
        if not isinstance(extras, dict):
            continue
        total_guests = int(extras.get("adults", 0) or 0) + int(extras.get("children", 0) or 0)
        try:
            current = _attendance.get_slot_attendance(day, "dinner")
            if current["guest_count"] == total_guests:
                continue
            if total_guests == 0 and not current["explicit"]:
                continue
            _attendance.set_guest_count(day, "dinner", total_guests)
        except ValueError:
            continue


def _observed_day_patterns(week_start: str, day_count: int = 7) -> dict:
    """
    What the app already knows about each weekday, so the household isn't
    re-answering things it has already been told. The grey hint line under
    each day row.

    Two sources, both real rather than invented:
      - A recurring rhythm fact from What We Know that names this weekday
        ("Tuesdays are tee-ball so we eat at 5").
      - An observed pattern in the plan history: the same weekday resolved
        to takeout or leftovers in most of the last four weeks. A night the
        household called off outright ("Not tonight — we're going out", see
        tools/tonight.py) counts the same way: nobody cooked, and the point
        of the hint is that next week's plan goes lighter on that day
        without anyone having to say so again.
    A day with neither gets no hint, and the row still works — it just
    reads "Nothing on the calendar."
    A period longer than a week can repeat a weekday (Thursday to next
    Thursday has two Thursdays). The hints are keyed by DATE, and the same
    weekday's hint simply lands on both — which is right: "Tuesdays are
    tee-ball" is true of every Tuesday in the window.
    """
    dates = period_dates(week_start, day_count)
    weekday_names = [date.fromisoformat(d).strftime("%A") for d in dates]

    conn = get_conn()
    facts = conn.execute(
        "SELECT text FROM facts WHERE household_id = ? AND category = 'rhythm'", (household_id(),)
    ).fetchall()
    lookback_start = (date.fromisoformat(week_start) - timedelta(days=28)).isoformat()
    history = conn.execute(
        """
        SELECT mpe.date, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND mpe.slot = 'dinner'
          AND mpe.date >= ? AND mpe.date < ?
        """,
        (household_id(), lookback_start, week_start),
    ).fetchall()
    conn.close()

    # Imported here rather than at module scope: tonight.py reads this
    # module's period_dates, so a top-level import either way is a cycle.
    from .tonight import NIGHT_OFF_CONSTRAINT

    takeout_by_weekday: dict[str, int] = {}
    for row in history:
        text = (row["meal"] or "").lower()
        called_off = False
        try:
            called_off = (
                json.loads(row["derived_from_json"] or "{}").get("constraint")
                == NIGHT_OFF_CONSTRAINT
            )
        except (TypeError, ValueError):
            called_off = False
        if called_off or re.search(r"take[\s-]?out|delivery|order in", text):
            name = date.fromisoformat(row["date"]).strftime("%A")
            takeout_by_weekday[name] = takeout_by_weekday.get(name, 0) + 1

    hints = {}
    for iso, weekday in zip(dates, weekday_names):
        # A rhythm fact naming this weekday wins — it's something the
        # household said outright, not something inferred from behaviour.
        stated = next(
            (f["text"] for f in facts if weekday.lower() in (f["text"] or "").lower()), None
        )
        if stated:
            hints[iso] = stated
            continue
        count = takeout_by_weekday.get(weekday, 0)
        if count >= 3:
            hints[iso] = "Takeout four weeks running"
        elif count == 2:
            hints[iso] = "Takeout two of the last four weeks"
    return hints


# The cuisines offered when a household hasn't saved any of its own. Only a
# fallback — Q2 reads the household's real list from What We Know first, so
# it visibly reflects its own stored profile and never re-asks a question it
# has already been answered. Taps on this fallback are written back.
ONBOARDING_CUISINES = [
    "Italian", "Mexican", "Thai", "Indian", "Japanese",
    "Greek", "Chinese", "Middle Eastern", "American", "French",
]

# The cuisines "Add a cuisine" can offer as you type (Loop Board "Add a
# cuisine that isn't on your list", 2026-09-21) — a spelling to fill the
# field with, never a fence: free text that matches nothing is still
# added, as typed. Kept alphabetical so a new one has an obvious place.
KNOWN_CUISINES = [
    "American", "Argentinian", "Brazilian", "British", "Cajun", "Caribbean",
    "Chinese", "Cuban", "Ethiopian", "Filipino", "French", "German", "Greek",
    "Hawaiian", "Indian", "Indonesian", "Irish", "Israeli", "Italian",
    "Jamaican", "Japanese", "Korean", "Lebanese", "Malaysian", "Mediterranean",
    "Mexican", "Middle Eastern", "Moroccan", "Nepalese", "Pakistani",
    "Persian", "Peruvian", "Polish", "Portuguese", "Russian", "Scandinavian",
    "Southern", "Spanish", "Sri Lankan", "Szechuan", "Taiwanese", "Tex-Mex",
    "Thai", "Turkish", "Ukrainian", "Vietnamese",
]

# The most letters a typed cuisine may run to — a chip, not a paragraph.
CUISINE_MAX_LENGTH = 40


def cuisine_suggestions(typed: str, limit: int = 3) -> list[str]:
    """
    The "Did you mean" row for what's been typed so far: known cuisines
    that start with it first (Mex -> Mexican), then ones that contain it
    (-> Tex-Mex), then ones that share its first two letters (->
    Mediterranean), up to `limit`. Empty for an empty field. Case never
    matters. The client asks this of the prefill's `known_cuisines`
    rather than the server on every keystroke — see cuisineSuggestions in
    static/plan-week.html, which mirrors this exactly; the test pins
    the two to the same answers.
    """
    q = " ".join((typed or "").split()).lower()
    if not q:
        return []
    starts = [c for c in KNOWN_CUISINES if c.lower().startswith(q)]
    contains = [c for c in KNOWN_CUISINES if q in c.lower() and c not in starts]
    close = [
        c for c in KNOWN_CUISINES
        if len(q) >= 2 and c.lower()[:2] == q[:2] and c not in starts and c not in contains
    ]
    return (starts + contains + close)[:limit]


def _clean_cuisine(name: str) -> str:
    """One line, single-spaced, capped, first letter up — as typed otherwise."""
    cleaned = " ".join(str(name or "").split())[:CUISINE_MAX_LENGTH].strip()
    if not cleaned:
        raise ValueError("Type a cuisine first.")
    return cleaned[0].upper() + cleaned[1:]


def add_household_cuisine(name: str) -> dict:
    """
    Put a cuisine on the household's own list — the one the mood screen
    reads (What we know's cuisine_preferences) — so it is there next week
    without being asked. A known cuisine's spelling wins over the typed
    one (mexican -> Mexican); a cuisine already on the list, in any case,
    is not added twice, and the saved spelling is the one handed back. A
    new cuisine goes FIRST, which is where the mood screen shows it.

    Returns {"cuisine": the spelling saved, "added": whether it was new,
    "cuisines": the whole list as it now stands}.
    """
    from . import preferences as _preferences

    cleaned = _clean_cuisine(name)
    known = {c.lower(): c for c in KNOWN_CUISINES}
    canonical = known.get(cleaned.lower(), cleaned)
    conn = get_conn()
    prefs = conn.execute(
        "SELECT cuisine_preferences_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    conn.close()
    saved = [c for c in (json.loads(prefs["cuisine_preferences_json"]) if prefs else []) if isinstance(c, str)]
    for existing in saved:
        if existing.strip().lower() == canonical.lower():
            return {"cuisine": existing, "added": False, "cuisines": saved}
    cuisines = [canonical] + saved
    _preferences.set_household_meal_preferences(cuisine_preferences=cuisines, mark_complete=False)
    return {"cuisine": canonical, "added": True, "cuisines": cuisines}


def _rhythm_packed_lunch_suggestions(week_start: str, day_count: int = 7) -> list[dict]:
    """
    Per-day packed-lunch suggestions derived from household rhythm (Loop
    Board "Onboarding: household rhythm..."): "the weekly intake's 'which
    lunches leave the house?' becomes conditional — pre-answered by
    rhythm". Only ever a SUGGESTION for the intake screen's prefill (the
    actual answer, once given, lives in week_intake.packed_lunch_days) —
    the household can always override it there.

    Per adult member with a lunch_location fact on record, resolves their
    effective location for that weekday (a per-weekday override if one's
    been learned, else the standing answer — see
    rhythm.effective_lunch_location). A day is suggested "packed" only when
    at least one adult resolves 'out' and none resolve 'home' — a mixed
    household (one home, one out) is a real judgment call about what
    "packed lunch day" even means for two people eating differently, so
    it's surfaced as a mix rather than the suggestion silently picking a
    side; flagged here as a product decision for whoever builds the
    prefill UI, not resolved by this function. A member whose rhythm was
    never set contributes to neither bucket (never asked yet is not the
    same as 'varies', which IS a real answer and also contributes to
    neither bucket for this specific suggestion, since it doesn't clearly
    say home or out).

    Returns [] entirely once no adult has any lunch_location on record —
    a household that hasn't done rhythm onboarding gets no suggestion, not
    a wrong one.

    day_count covers a planning period that isn't seven days (Loop Board
    "Planning periods, not weeks"). It defaults to 7, so every existing
    caller asks the same question; the generation path passes the real
    length so a longer period's last days get suggestions too, and a
    shorter one stops suggesting for days nobody asked to plan.
    """
    rhythm = _rhythm.get_household_rhythm()["lunch_location"]
    if not rhythm:
        return []
    dates = period_dates(week_start, day_count)
    suggestions = []
    for d in dates:
        weekday = date.fromisoformat(d).strftime("%A")
        out_members, home_members, varies_members = [], [], []
        for member_name in rhythm:
            location = _rhythm.effective_lunch_location(member_name, weekday)
            if location == "out":
                out_members.append(member_name)
            elif location == "home":
                home_members.append(member_name)
            elif location == "varies":
                varies_members.append(member_name)
        if not (out_members or home_members or varies_members):
            continue
        suggestions.append({
            "date": d,
            "weekday": weekday,
            "out": out_members,
            "home": home_members,
            "varies": varies_members,
            "suggested_packed": bool(out_members) and not home_members,
        })
    return suggestions


def _last_period_intake(conn, week_start: str) -> dict | None:
    """
    The answers the household gave for the period BEFORE this one — the
    current revision of the most recent intake whose week starts earlier
    than `week_start` — or None for a first week.

    What the intake's mood screen opens already knowing (Emily, 2026-09-15,
    "one screen of what Pomona already knows": moods and cuisines are
    asked every week and "it says 'I'll remember' but week 30 looks like
    week 1"; reconciled into the 2026-09-21 one-question-a-screen intake
    as a step that shows last week's answer already chosen and can be
    continued past in one tap). Only the answers that carry across weeks
    travel: the night tags and guest counts are about specific dates, and
    the typed note was about that week. The weekday lunches (step 3,
    2026-09-25) travel BY WEEKDAY — `weekday_lunches` is
    tools/weekday_lunches.carryover's shape, with the on-the-go days as
    weekdays too — so this week's dates can be laid out from last week's
    Tuesdays, and so the "Same as last week?" page can show them.

    Its presence is what opens Plan a week on "Same as last week?" rather
    than the five questions: a first week (None here) always asks them all.
    """
    row = conn.execute(
        "SELECT * FROM week_intake WHERE household_id = ? AND week_start < ? AND superseded_at IS NULL "
        "ORDER BY week_start DESC, revision DESC LIMIT 1",
        (household_id(), week_start),
    ).fetchone()
    if not row:
        return None
    # How many days that period ran, off the plan drafted from it — the
    # "Same as last week?" page carries it over (Emily, 2026-09-25, option
    # A). The intake itself never stored a length; the latest live plan
    # drafted from any revision of that week is the one that did. None when
    # nothing was drafted from it (answers given, never planned).
    plan = conn.execute(
        "SELECT * FROM weekly_plans WHERE household_id = ? AND status != 'retired' AND intake_id IN "
        "(SELECT id FROM week_intake WHERE household_id = ? AND week_start = ?) ORDER BY id DESC LIMIT 1",
        (household_id(), household_id(), row["week_start"]),
    ).fetchone()
    day_count = _weekly_plan.plan_period(plan)[1] if plan else 0
    return {
        "week_start": row["week_start"],
        "day_count": day_count or None,
        "moods": json.loads(row["moods_json"]),
        "cuisines": json.loads(row["cuisines_json"]),
        "weekday_lunches": _weekday_lunches.carryover(
            _weekday_lunches.load(row["weekday_lunches_json"]),
            json.loads(row["packed_lunch_days_json"] or "[]"),
        ),
    }


def _recent_dinners_on_record(week_start: str) -> bool:
    """
    Whether any dinner was planned in the weeks before this period — what
    lets the building screen's "Nothing you had last week" line be true
    (the generator's no-repeat rule reads the same recent_history) rather
    than a promise made to a household with no last week.
    """
    from . import meal_plans as _meal_plans
    return any(
        (m.get("slot") == "dinner") and (m.get("date") or "") < week_start
        for m in _meal_plans.get_recent_meal_history(weeks=3)
    )


# How far back an intake nobody has drafted from may have been FILED and
# still be the one a period opens on: the overnight straddle (one adult
# starts on Saturday night, the other opens it on Sunday, when the screen
# starts from today) — never a set of answers abandoned weeks ago.
IN_FLIGHT_REACH_DAYS = 7


def _plan_for_period(week_start: str, day_count: int):
    """
    The live plan a period is ABOUT: the one whose days include the
    period's first day, else an approved one it overlaps (the one that
    re-planning would cost something), else any live plan it overlaps.
    None when the period touches no plan.

    Not "the plan filed under this date". The intake screen opens on today
    even when the plan it re-plans started yesterday (plan-week.html's
    clampStart, 2026-09-21: yesterday is eaten), so "Re-plan this week" on
    an approved Sat–Fri plan asks about Sun–Sat, and an exact key found
    no plan at all — the approved warning went unsaid, and "Change my
    answers" opened blank. Returns the overlap record from
    find_overlapping_plans plus the plan's intake_id.
    """
    overlapping = _weekly_plan.find_overlapping_plans(week_start, day_count)
    if not overlapping:
        return None
    covering = [
        o for o in overlapping
        if o["period_start_date"] <= week_start <= _weekly_plan.period_end_date(o["period_start_date"], o["day_count"])
    ]
    approved = [o for o in overlapping if o["status"] == "approved"]
    chosen = (covering or approved or overlapping)[-1]
    conn = get_conn()
    row = conn.execute("SELECT intake_id FROM weekly_plans WHERE id = ?", (chosen["weekly_plan_id"],)).fetchone()
    conn.close()
    return {**chosen, "intake_id": row["intake_id"] if row else None,
            "approved_overlap": bool(approved)}


def _intake_for_period(conn, week_start: str, day_count: int, plan) -> dict | None:
    """
    The answers a period opens on: the intake filed under its first day;
    else the current revision of the intake its plan was drafted from (so
    "Change my answers" on Tuesday still has Sunday's night tags, guests
    and typed note); else an intake nobody has drafted from yet, filed
    within IN_FLIGHT_REACH_DAYS before — the second adult joining the
    first across midnight. Dated answers outside the period are left out,
    since the save that follows would refuse them; the days still in range
    keep every answer they had.
    """
    row = _current_intake_row(conn, week_start)
    if row is None and plan and plan.get("intake_id"):
        filed = conn.execute("SELECT week_start FROM week_intake WHERE id = ?", (plan["intake_id"],)).fetchone()
        if filed:
            row = _current_intake_row(conn, filed["week_start"])
    if row is None and plan is None:
        floor = (date.fromisoformat(week_start) - timedelta(days=IN_FLIGHT_REACH_DAYS)).isoformat()
        row = conn.execute(
            "SELECT * FROM week_intake wi WHERE household_id = ? AND week_start < ? AND week_start >= ? "
            "AND superseded_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM weekly_plans wp WHERE wp.intake_id = wi.id) "
            "ORDER BY week_start DESC, revision DESC LIMIT 1",
            (household_id(), week_start, floor),
        ).fetchone()
    if row is None:
        return None
    intake = _intake_row_to_dict(row)
    days = set(period_dates(week_start, day_count))
    intake["night_tags"] = {d: t for d, t in intake["night_tags"].items() if d in days}
    intake["guest_counts"] = {d: c for d, c in intake["guest_counts"].items() if d in days}
    intake["packed_lunch_days"] = [d for d in intake["packed_lunch_days"] if d in days]
    intake["skipped_days"] = [d for d in intake.get("skipped_days") or [] if d in days]
    intake["weekday_lunches"] = _weekday_lunches.normalize(
        intake.get("weekday_lunches"), period_dates(week_start, day_count), intake["skipped_days"], strict=False,
    )
    return intake


def get_week_intake_prefill(week_start: str, day_count: int = 7) -> dict:
    """
    Everything the two question screens need to open already knowing what
    the app knows: per-day hints, the household's own saved cuisines, its
    composition for the guest maths, and any intake already in flight.

    The plan and the intake are the ones that COVER the period's first
    day, not ones filed under it (_plan_for_period, _intake_for_period):
    since the screen never opens before today, a plan started yesterday
    is found by the day it still holds.

    `in_flight` is the soft lock from DATA_MODEL.md → One intake in flight.
    Both adults are nudged on Sunday, so both can start; the second to open
    Q1 joins the first one's answers rather than starting a blank set. Two
    intakes racing to generate the same week is the one concurrency case
    that will actually happen, most likely on a Sunday evening.

    `household_known` is false when nobody's age group is on record. The
    guest panel switches to asking for the WHOLE TABLE in that case rather
    than for extras added to a base of zero, which would silently produce a
    wrong number and an acknowledgement that confidently states it.

    day_count is the length of the planning period being asked about (Loop
    Board "Planning periods, not weeks"), defaulting to the seven this
    always assumed. It matters more here than almost anywhere else: these
    screens ask the household to tag each day, and asking about seven days
    when they chose four is asking four real questions and three imaginary
    ones — whose answers would then be saved as intake for days no plan
    covers, and read back the next time that week IS planned.
    """
    date.fromisoformat(week_start)
    household = _household_composition()
    conn = get_conn()
    prefs = conn.execute(
        "SELECT cuisine_preferences_json FROM meal_preferences WHERE household_id = ?", (household_id(),)
    ).fetchone()
    plan = _plan_for_period(week_start, day_count)
    intake = _intake_for_period(conn, week_start, day_count, plan)
    last_intake = _last_period_intake(conn, week_start)
    # "Bring over from last week" (2026-09-25): shown on "Same as last
    # week?" only, so only looked up when that page will open.
    last_week_uncooked = _bring_over.last_week_uncooked(conn, week_start) if last_intake else []
    conn.close()

    saved_cuisines = json.loads(prefs["cuisine_preferences_json"]) if prefs else []
    # "In flight" means somebody has answered something for this week that
    # hasn't been turned into a plan yet. Once a plan has been generated
    # from this revision, carrying on from it is a redo, not a join.
    in_flight = bool(intake and (not plan or plan["intake_id"] != intake["intake_id"]))

    return {
        "week_start": week_start,
        # The period's real dates, not seven days from its start. This is
        # the eyebrow above the questions ("Question 1 of 2 · Sep 10–17"),
        # so a 7-day label over an 8-day set of day cards would have the
        # screen naming a window it is visibly not asking about.
        "week_label": _weekly_plan._format_period_range(week_start, day_count),
        "days": [
            {
                "date": d,
                "weekday": date.fromisoformat(d).strftime("%A"),
                "short": date.fromisoformat(d).strftime("%a"),
                "day_of_month": date.fromisoformat(d).day,
                "hint": hint,
            }
            for d, hint in (
                (d, _observed_day_patterns(week_start, day_count).get(d, "")) for d in period_dates(week_start, day_count)
            )
        ],
        "household": household,
        # False when nobody's age group is recorded — see the docstring.
        "household_known": bool(household["adults"] or household["children"]),
        "cuisines": saved_cuisines or ONBOARDING_CUISINES,
        "cuisines_are_fallback": not saved_cuisines,
        # What "Add a cuisine" can offer as you type — see cuisine_suggestions.
        "known_cuisines": KNOWN_CUISINES,
        "intake": intake,
        "in_flight": in_flight,
        "plan_exists": bool(plan),
        "plan_id": plan["weekly_plan_id"] if plan else None,
        "plan_status": plan["status"] if plan else None,
        # True when any approved plan holds a day of this period — the
        # warning that re-planning makes a new draft beside it fires on
        # this, whichever plan the period opens on.
        "approved_overlap": bool(plan and plan["approved_overlap"]),
        # Loop Board "Onboarding: household rhythm..." — a suggestion only,
        # not an answer; see _rhythm_packed_lunch_suggestions.
        "rhythm_packed_lunch_suggestions": _rhythm_packed_lunch_suggestions(week_start, day_count),
        # Step 3, "Weekday lunches": the household's standing prep days
        # (rhythm.prep_days, lowercase weekday names, in week order from
        # Sunday) — where the prep-day chips start when neither this week
        # nor last week has an answer.
        "rhythm_prep_days": [
            d for d in _weekday_lunches.PREP_WEEKDAYS
            if d in {(p.get("weekday") if isinstance(p, dict) else str(p)).lower()
                     for p in (_rhythm.get_household_rhythm().get("prep_days") or [])}
        ],
        # What the mood screen opens already knowing: the moods and cuisines
        # the household chose for the period before this one, or None for a
        # first week. See _last_period_intake.
        "last_intake": last_intake,
        # Last week's dinners and lunches nobody ticked cooked — the "Bring
        # over from last week" list at the top of "Same as last week?"
        # (Emily, 2026-09-25). [] hides it. See tools/bring_over.py.
        "last_week_uncooked": last_week_uncooked,
        # True once a dinner has been planned in the last three weeks — the
        # building screen says "Nothing you had last week" only then.
        "recent_dinners_on_record": _recent_dinners_on_record(week_start),
        # The period these questions are about, echoed back so the screen
        # can name it ("Sep 11-18") instead of calling every window "your
        # week" regardless of what the household actually picked.
        "period_start_date": week_start,
        "day_count": day_count,
        # The holidays in this period, each with the household's answer so
        # far (Loop Board "Holidays: Pomona knows 12 October is coming...").
        # The Days screen asks about the ones that ask, once, and shows the
        # rest as a quiet label on their day. See holidays.py.
        "holidays": _holidays.holidays_for_period(week_start, day_count),
    }
