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
# Three groups now, and the third one is the fix that matters most:
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
#      week for the wrong reason.
#   3. The furniture of a sentence about a household: places, times, meals,
#      wanting verbs. A fact is written the way a person talks, so it carries
#      words that look exactly like food words to a keyword match and are
#      not: "no pork in this house" flagged House Salad on "house".
#      A warning that fires on everything is a warning nobody reads, which
#      for an allergy check is worse than none.
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
    # Group 3 — place, time, meal, and wanting words.
    "house", "home", "here", "kitchen", "table", "week", "weeknights",
    "weekends", "night", "nights", "day", "days", "meal", "meals",
    "dinner", "dinners", "breakfast", "breakfasts", "lunch", "lunches",
    "snack", "snacks", "needs", "need", "likes", "wants", "prefers",
    "high", "low", "tree",
    # "no red meat DURING the week" — a stretch-of-time word sitting inside
    # the avoidance span itself, so it survived into the phrase and made
    # "red meat" unmatchable as written. Time words that can appear before
    # the trigger are already above; this one comes after it.
    "during", "throughout", "while",
})


# The one place a keyword is allowed to mean more than itself.
#
# "nut allergy" has to reach peanut butter, walnuts and almonds — whole-word
# matching finds none of them, and an allergen the household wrote down in
# the ordinary way is exactly what this check exists to catch. Kept as an
# explicit table rather than any kind of stemming so the expansion is
# readable and arguable: every entry below is a deliberate claim about food,
# not a string trick. Whole-word matching still applies to each expansion,
# which is what keeps "nut" off coconut, nutmeg and butternut squash.
#
# A STARTING LIST, not a complete one. It covers the common allergens a
# household is likely to write down; add to it when a real miss shows up
# rather than trying to enumerate every food in advance. Plurals are not
# listed — _keyword_variants already handles those.
_ALLERGEN_ALIASES: dict[str, set[str]] = {
    "nut": {"peanut", "walnut", "almond", "cashew", "pecan", "hazelnut",
            "pistachio", "macadamia"},
    "shellfish": {"shrimp", "prawn", "crab", "lobster", "clam", "mussel",
                  "scallop", "oyster"},
    # "buttermilk" is here rather than left to "butter"/"milk": whole-word
    # matching reaches neither half of it, and it is unambiguously dairy —
    # the opposite call from "peanut butter", which is spelled with a dairy
    # word and contains none (see _COMPOUND_EXCEPTIONS).
    "dairy": {"milk", "cheese", "butter", "buttermilk", "cream", "yogurt",
              "yoghurt", "whey"},
    "gluten": {"flour", "bread", "pasta", "noodles", "couscous", "seitan"},
    "wheat": {"flour", "bread", "pasta", "noodles", "couscous", "seitan"},
    "egg": {"eggs", "mayonnaise", "mayo"},
    "soy": {"soya", "tofu", "tempeh", "edamame", "soy sauce"},
    "sesame": {"tahini"},
}
_ALLERGEN_ALIASES["nuts"] = _ALLERGEN_ALIASES["nut"]
_ALLERGEN_ALIASES["eggs"] = _ALLERGEN_ALIASES["egg"]


# The compound food names where an allergen word is not the allergen.
#
# Whole-word matching already handles the ones written as a single word —
# coconut, nutmeg, butternut, eggplant are not "nut" or "egg" and never
# match. These are the ones written as TWO words, where the allergen word
# really is standing there on its own and still doesn't mean the allergen:
# "peanut butter" is not dairy, "coconut milk" is not dairy, "sugar snap
# peas" are a pea. Emily's own week flagged Peanut Butter Toast for a dairy
# note and Sugar Snap Peas for "avoid sugar".
#
# Each entry is (the words it neutralises, the compound it neutralises them
# inside). Only that OCCURRENCE is discounted, so "sugar snap peas tossed in
# brown sugar" still trips a sugar avoidance on the second one.
#
# Two deliberate limits, both about not turning a false positive into a
# false negative — which is the worse bug here:
#
#   1. Only the listed word is discounted, never the whole compound. A NUT
#      allergy still catches "peanut butter" on "peanut"; it is only a DAIRY
#      note that stops catching it on "butter".
#   2. A discount only applies to a single-word avoidance. If the household
#      wrote the compound itself — "allergic to coconut milk" — they are
#      taken at their word and the phrase matches.
#
# Short on purpose, and a list a person can argue with. Extend it when a
# real false positive shows up, the same way _ALLERGEN_ALIASES is extended
# when a real miss does.
_COMPOUND_EXCEPTIONS: tuple[tuple[frozenset[str], re.Pattern], ...] = (
    (frozenset({"butter", "butters"}), re.compile(
        r"\b(?:peanut|almond|cashew|hazelnut|pistachio|pecan|walnut|macadamia|"
        r"sunflower|pumpkin|sesame|seed|nut|apple|cocoa|cacao|shea)s?\s+butters?\b"
    )),
    (frozenset({"milk", "milks"}), re.compile(
        r"\b(?:almond|cashew|coconut|hazelnut|hemp|oat|pea|rice|soy|soya)\s+milks?\b"
    )),
    (frozenset({"sugar", "sugars"}), re.compile(r"\bsugar\s+snaps?\b")),
)


# What turns a sentence into an avoidance. A hard What-we-know fact is
# freeform prose and not every one of them is a "keep this off the table" —
# "Emily needs high-protein dinners" is hard, and true, and names no food to
# avoid. Only the span AFTER one of these triggers is read as a food to keep
# away from; a fact with no trigger contributes no match terms at all.
# Word boundaries are written per-alternative on purpose: a trailing \b
# after the whole group would silently kill "allergies:", because there is
# no word boundary between a colon and the space after it.
_AVOIDANCE_TRIGGER_RE = re.compile(
    r"\ballerg(?:ic|y|ies)\s+to\b"
    r"|\ballergies\s*:"
    r"|\bcan(?:no|')?t\s+(?:have|eat)\b"
    r"|\bintoleran(?:t|ce)\s+to\b"
    r"|\bavoids?\b|\bavoiding\b|\bnever\b|\bno\b"
)

# The same claim written backwards — "Emily has a nut allergy", "she's
# lactose intolerant", "we cook dairy-free". Not in the trigger list above
# because the food comes BEFORE the word, and dropping this shape would
# have quietly lost the single most common way an allergy gets written
# down. A tight window (the two words before an allergy noun, one before an
# adjective) rather than the whole clause, because everything further back
# is sentence, not food.
_ALLERGY_NOUN_RE = re.compile(r"\ballerg(?:y|ies|ic)\b")
_ALLERGY_ADJ_RE = re.compile(r"\bintoleran(?:t|ce)\b|(?<=[a-z])-free\b")

# A stated exception. "allergic to tree nuts but peanuts are fine" says two
# things, and reading only the first half is how a household gets warned
# about the exact food they just told us was safe. The span after one of
# these is not an avoidance — it is subtracted from the match terms, so it
# also cancels anything the alias table above expanded into it.
_CONTRAST_RE = re.compile(
    r"\b(?:but|except|excepting|unless|though|although|apart\s+from|"
    r"other\s+than|aside\s+from)\b"
)
_FINE_RE = re.compile(r"\b(?:fine|ok|okay|alright|allowed|welcome)\b")


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


# Where one avoidance ends and the next begins. "allergic to pineapple,
# shellfish and eggs" is three things to avoid, not one three-word food —
# and getting that wrong in the other direction is a false NEGATIVE, which
# is the failure this whole file exists to prevent. "and"/"or" are already
# stopwords; they are listed here as well because they mark a boundary, not
# only a word to ignore.
_PHRASE_SPLIT_RE = re.compile(r"[,;/]|\band\b|\bor\b|\bplus\b|\bas\s+well\s+as\b")


def _conflict_phrases(text: str, drop: set[str] | None = None) -> list[list[str]]:
    """
    A restriction or an avoidance span read as PHRASES rather than as a bag
    of loose words — the fix for the check's loudest false positives.

    Every non-stopword used to become an independent thing to hunt for, so a
    multi-word avoidance leaked its common words: "no red meat during the
    week" searched for "red" on its own and flagged Red Lentil Dahl, and any
    two-word food did the same. A phrase now has to be found as a phrase —
    all of its words, in order, inside one stretch of text (see _matches) —
    and only a genuinely single-word avoidance matches on a single word.

    Splitting on commas and "and"/"or" first is what keeps that from
    becoming a miss: "allergic to pineapple, shellfish and eggs" is three
    one-word phrases, not one phrase that matches nothing.
    """
    out: list[list[str]] = []
    for piece in _PHRASE_SPLIT_RE.split((text or "").lower()):
        words = _conflict_keywords(piece, drop=drop)
        if words and words not in out:
            out.append(words)
    return out


def _split_exception(span: str) -> tuple[str, str]:
    """Split "tree nuts but peanuts are fine" into what to avoid and what not to."""
    m = _CONTRAST_RE.search(span)
    if m:
        return span[:m.start()], span[m.end():]
    # The comma form: "no cow milk for Emily, oat milk is fine".
    for m in re.finditer(r",", span):
        tail = span[m.end():]
        if _FINE_RE.search(tail):
            return span[:m.start()], tail
    return span, ""


def _fact_keywords(text: str, drop: set[str]) -> tuple[list[list[str]], list[str]]:
    """
    Read a freeform hard fact as (things to avoid, things explicitly fine).

    The first list is a list of PHRASES — each one an ordered run of words
    that all have to be found together (see _conflict_phrases) — because a
    fact is a sentence and its avoidances arrive as noun phrases, not as
    loose words. The second is a flat list of words the sentence called
    fine, subtracted from whatever the first produced.

    Both can be empty, and an empty first list is the important case: it
    means the fact says nothing about avoiding a food, so it must produce
    no warning at all. Every non-stopword in the sentence used to become a
    match term, which is how a fact about wanting protein flagged a Protein
    Bowl and a fact about the house flagged a House Salad.
    """
    lowered = (text or "").lower().replace("’", "'")
    spans: list[str] = []
    exception_spans: list[str] = []

    for m in _AVOIDANCE_TRIGGER_RE.finditer(lowered):
        rest = re.split(r"[.;!?]", lowered[m.end():], maxsplit=1)[0]
        # "no pork, no shellfish" is two avoidances, not one long one.
        nxt = _AVOIDANCE_TRIGGER_RE.search(rest)
        if nxt:
            rest = rest[:nxt.start()]
        avoid, exception = _split_exception(rest)
        spans.append(avoid)
        if exception:
            exception_spans.append(exception)

    for regex, window in ((_ALLERGY_NOUN_RE, 2), (_ALLERGY_ADJ_RE, 1)):
        for m in regex.finditer(lowered):
            before = re.split(r"[.,;!?]", lowered[:m.start()])[-1]
            before = re.split(r"\b(?:and|or|but|with)\b", before)[-1]
            spans.append(" ".join(before.split()[-window:]))

    phrases: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for span in spans:
        for words in _conflict_phrases(span, drop=drop):
            key = tuple(words)
            if key not in seen:
                seen.add(key)
                phrases.append(words)
    excepted = [w for span in exception_spans for w in _conflict_keywords(span, drop=drop)]
    return phrases, excepted


def _keyword_variants(keyword: str) -> set[str]:
    """
    A keyword plus its plausible singular/plural twin. A restriction saved as
    "peanuts" has to match an ingredient listed as "peanut butter" — whole-word
    matching alone would miss it, and missing an allergen is the failure this
    check exists to prevent.

    English enough to stop inventing words. The first version appended both
    "s" and "es" to everything, so "nut" also searched for "nutes" and
    "shellfish" for "shellfishs" — never harmful (nothing matches a non-word)
    but noise in the one function whose output a person may end up reading.
    """
    variants = {keyword}
    if len(keyword) > 4 and keyword.endswith("ies"):
        variants.add(keyword[:-3] + "y")
    elif len(keyword) > 4 and keyword.endswith(("ches", "shes", "sses", "xes", "zes")):
        variants.add(keyword[:-2])
    elif len(keyword) > 3 and keyword.endswith("es"):
        variants.add(keyword[:-1])
        variants.add(keyword[:-2])
    elif len(keyword) > 3 and keyword.endswith("s"):
        variants.add(keyword[:-1])
    elif keyword.endswith("y") and len(keyword) > 2 and keyword[-2] not in "aeiou":
        variants.add(keyword[:-1] + "ies")
    elif keyword.endswith(("s", "x", "z", "ch", "sh")):
        variants.add(keyword + "es")
    else:
        variants.add(keyword + "s")
    return variants


def _match_terms(
    phrases: list[list[str]], excepted: list[str] | None = None,
) -> list[tuple[str, list[set[str]]]]:
    """
    Each avoidance phrase turned into what it takes to find it: the phrase's
    label, plus one set of acceptable spellings PER WORD, in order. A word's
    set is its own plural/singular twin plus the alias table's expansion when
    it names a whole allergen family.

    Anything the household explicitly called fine is removed — including from
    an alias expansion, which is what lets "allergic to tree nuts but peanuts
    are fine" still catch walnuts. A word whose every spelling was excepted
    drops out of the phrase rather than killing it: "no cow milk for Emily,
    oat milk is fine" is left looking for "cow".
    """
    excluded: set[str] = set()
    for word in excepted or []:
        excluded |= _keyword_variants(word)
    out: list[tuple[str, list[set[str]]]] = []
    for phrase in phrases:
        groups: list[set[str]] = []
        kept: list[str] = []
        for word in phrase:
            variants: set[str] = set()
            for base in {word} | _ALLERGEN_ALIASES.get(word, set()):
                variants |= _keyword_variants(base)
            variants -= excluded
            if variants:
                groups.append(variants)
                kept.append(word)
        if groups:
            out.append((" ".join(kept), groups))
    return out


def _discounted(variant: str, match: re.Match, text: str) -> bool:
    """Whether this occurrence sits inside a compound that neutralises it."""
    for words, compound in _COMPOUND_EXCEPTIONS:
        if variant not in words:
            continue
        for found in compound.finditer(text):
            if found.start() <= match.start() and match.end() <= found.end():
                return True
    return False


def _find_variant(variant: str, text: str, start: int, discountable: bool) -> re.Match | None:
    """The first whole-word occurrence at or after `start` that actually counts."""
    for m in re.finditer(r"\b" + re.escape(variant) + r"\b", text):
        if m.start() < start:
            continue
        if discountable and _discounted(variant, m, text):
            continue
        return m
    return None


def _phrase_in(groups: list[set[str]], text: str) -> bool:
    """
    Whether every word of a phrase appears in `text`, in order.

    In order rather than strictly adjacent, so "red meat" still finds "red
    minced meat" — but within ONE stretch of text (see _matches), so it
    cannot be assembled out of a word in the dish's name and another in an
    unrelated ingredient three lines down.
    """
    pos = 0
    discountable = len(groups) == 1
    for variants in groups:
        best: re.Match | None = None
        for variant in variants:
            m = _find_variant(variant, text, pos, discountable)
            if m and (best is None or m.start() < best.start()):
                best = m
        if best is None:
            return False
        pos = best.end()
    return True


def _matches(terms: list[tuple[str, list[set[str]]]], segments: list[str]) -> str | None:
    """
    The first avoidance found in any one of `segments`, or None.

    Segments, not one joined blob: a phrase has to land inside a single
    stretch of text — the dish's name, or one ingredient line — because a
    two-word food spread across two unrelated ingredients is a coincidence,
    not a clash.
    """
    for label, groups in terms:
        for segment in segments:
            if _phrase_in(groups, segment):
                return label
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
            # Phrases here too, not only for facts. A saved restriction is
            # meant to be one restriction per list entry, so "red meat" is a
            # food and not two — and the comma/"and" split above still reads
            # "no dairy, no eggs" typed into a single box as two things.
            terms = _match_terms(_conflict_phrases(restriction, drop=name_words))
            if not terms:
                continue
            out.append({
                "member": m["name"], "label": restriction.strip().lower(),
                "source": "dietary_restriction", "severity": "hard",
                "terms": terms,
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
        phrases, excepted = _fact_keywords(text, drop=name_words)
        terms = _match_terms(phrases, excepted)
        if not terms:
            continue
        out.append({
            "member": named, "label": text.strip(), "source": "fact",
            "severity": "hard", "terms": terms,
        })

    # Standing dislikes are not a safety matter, so they ride along at a
    # lower severity — worth mentioning, never worth alarming about.
    for dislike in _memory.get_household_memory().get("dislikes") or []:
        terms = _match_terms(_conflict_phrases(dislike))
        if not terms:
            continue
        out.append({
            "member": None, "label": dislike.strip().lower(), "source": "dislike",
            "severity": "soft", "terms": terms,
        })
    return out


# The closing half of the warning sentence. Two of them, because the same
# clash is read at two different moments: on the review band there is still
# a decision to make, and after approval there isn't — telling a household
# to look "before you approve" once they already have is the app not
# listening.
_DRAFT_CLOSING = "worth a look before you approve"
_APPROVED_CLOSING = "worth a look before you shop"

# A label that already says what kind of thing it is ("pineapple allergy",
# "dairy-free") can hang off a name; a bare food ("shellfish") cannot —
# "a clash with Emily's shellfish" is not a sentence anyone would say.
_SELF_DESCRIBING_LABEL_RE = re.compile(
    r"\b(?:allerg\w*|intoleran\w*|sensitivit\w*|free|diet|vegan|vegetarian|"
    r"pescatarian|halal|kosher)\b"
)

_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _spell(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def _one_meal_sentence(c: dict, closing: str) -> str:
    """The full sentence when a single dish trips a single thing to avoid."""
    meal, member, label = c["meal"], c["member"], c["restriction"]
    if c["source"] == "fact":
        # The fact is a whole sentence, so it is quoted rather than
        # grammatically absorbed — and named to a person when it names one.
        whose = f"something {member} can’t have" if member else "something you’ve told me"
        return f"{meal} looks like a clash with {whose} — “{label}”. {closing[0].upper()}{closing[1:]}."
    if member:
        if _SELF_DESCRIBING_LABEL_RE.search(label):
            return f"{meal} looks like a clash with {member}’s {label} — {closing}."
        return f"{meal} looks like a clash with the {label} {member} can’t have — {closing}."
    return f"{meal} looks like a clash with the {label} you’ve asked me to avoid — {closing}."


def _conflicts_note(conflicts: list[dict], closing: str = _DRAFT_CLOSING) -> str | None:
    """
    One plain sentence for the draft's review band, or None when there is
    nothing to say. Written here rather than in the UI so the wording lives
    with the data it describes — and only ever about the hard ones, because
    a dislike is a preference, not a warning.

    Counted in MEALS, not in clashes. One dish planned on five nights is one
    thing to look at, not five; one dish that trips two separate facts is
    also one thing to look at, and the sentence knows its name — retreating
    to "One meal looks like a clash" when the dish is sitting right there
    was the app being vaguer than it needed to be.
    """
    hard = [c for c in conflicts if c["severity"] == "hard"]
    if not hard:
        return None

    meals: list[str] = []
    for c in hard:
        if c["meal"] not in meals:
            meals.append(c["meal"])

    if len(meals) == 1:
        distinct = {c["restriction"]: c for c in hard}
        if len(distinct) == 1:
            return _one_meal_sentence(next(iter(distinct.values())), closing)
        # Two facts, one dish. Still name the dish, and still name the
        # person when every clash is about the same one.
        members = {c["member"] for c in hard}
        whose = (
            f"{_spell(len(distinct))} things {members.pop()} can’t have"
            if len(members) == 1 and None not in members
            else f"{_spell(len(distinct))} things you’ve asked me to avoid"
        )
        return f"{meals[0]} looks like a clash with {whose} — {closing}."

    if len(meals) == 2:
        return (
            f"{meals[0]} and {meals[1]} look like a clash with something "
            f"you’ve asked me to avoid — {closing}."
        )
    subject = _spell(len(meals))
    return (
        f"{subject[0].upper()}{subject[1:]} meals look like a clash with something "
        f"you’ve asked me to avoid — {closing}."
    )


def conflicts_note_after_approval(conflicts: list[dict]) -> str | None:
    """
    The same warning, worded for a week that has already been approved.

    approve_weekly_plan passes its clashes through here instead of using
    check_plan_conflicts' own note: the draft's sentence ends "before you
    approve", and repeating that back to someone who just approved reads as
    the app not having noticed.
    """
    return _conflicts_note(conflicts, closing=_APPROVED_CLOSING)


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
        # The dish's name and each ingredient line SEPARATELY, not one
        # joined blob. A multi-word avoidance has to be found inside a
        # single one of them — "red meat" assembled out of "red pepper" in
        # the name and "minced meat" four lines later is a coincidence, not
        # a clash. Whitespace collapsed as well as punctuation stripped, so
        # a two-word term ("soy sauce") still matches an ingredient written
        # with odd spacing.
        raw_segments = [name]
        if recipe:
            raw_segments += [(i.get("item") or "") for i in recipe.get("ingredients", [])]
        segments = [
            re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s-]", " ", s.lower())).strip()
            for s in raw_segments
        ]
        segments = [s for s in segments if s]
        for avoidance in avoidances:
            matched = _matches(avoidance["terms"], segments)
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
