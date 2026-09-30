"""
Household coordination and trust: plan conflicts, explaining a choice,
feedback nudges, and who is in the household.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from ..db import get_conn
from ._shared import household_id, household_initials
from . import household as _household
from . import memory as _memory
from . import plates as _plates
from . import recipes as _recipes
from . import taste_verdict as _taste_verdict
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")


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
# A HAND-MAINTAINED LIST, not a complete one, and this is the only place
# it lives. It covers the common allergens a household is likely to write
# down and the ingredient names a recipe is likely to list them under; add
# to it when a real miss shows up rather than trying to enumerate every
# food in advance. Widened on 2026-09-21 after the verifier of the
# allergen hard-block found real model-written ingredients slipping past
# ("parmesan" under a dairy note, "calamari" under shellfish, "miso" under
# soy, and no fish family at all). Plurals are not listed —
# _keyword_variants already handles those.
#
# Every entry is a claim that the ingredient CONTAINS the allergen as a
# matter of course — pesto has pine nuts, hummus has tahini, soy sauce is
# brewed with wheat — not that it might. A household that has said
# otherwise ("…but peanuts are fine") is honoured by _match_terms, which
# removes an excepted word from any family it sits in.
# Widened again on 2026-09-30, when the name stopped being a backstop for
# the list. A dish named "Dairy-Free Cheese Sauce" used to be held on its
# NAME even when its list said only "sharp cheddar" — a word this table
# didn't know. Once "dairy-free" in a name stopped counting as dairy, the
# list had to know every common way the allergen is written, so each
# family below was audited for the named cheeses, sauces and dishes that
# are the allergen by definition (carbonara is egg and pecorino; teriyaki
# is soy sauce; satay is peanut). Accents are folded before matching
# (check_meal_conflicts), so "gruyère" and "crème fraîche" are listed
# plain.
_WHEAT_WORDS = {
    "flour", "bread", "pasta", "noodles", "couscous", "seitan", "barley", "rye", "bulgur",
    "breadcrumb", "soy sauce", "wheat", "spelt", "farro", "freekeh", "semolina", "durum",
    "panko", "orzo", "udon", "ramen", "spaghetti", "linguine", "fettuccine", "penne",
    "macaroni", "lasagna", "lasagne", "pita", "naan", "bagel", "croissant", "crouton",
    "malt", "teriyaki", "hoisin",
    # Second audit (2026-09-30, verifier round 2).
    "rigatoni", "ravioli", "tortellini", "fusilli", "farfalle", "ziti", "pappardelle",
    "gnocchi", "dough", "tempura", "wonton", "pastry", "phyllo", "filo", "roux", "beer",
    "sourdough", "focaccia", "brioche", "challah", "lo mein",
}
_ALLERGEN_ALIASES: dict[str, set[str]] = {
    "nut": {"peanut", "walnut", "almond", "cashew", "pecan", "hazelnut",
            "pistachio", "macadamia", "pesto", "marzipan", "praline",
            # Peanut dishes and spreads belong to "nut" too — a nut allergy
            # written the ordinary way must still reach satay. A household
            # that says "…but peanuts are fine" is honoured by _match_terms,
            # which lifts the excepted word's own family (satay, groundnut)
            # out along with it.
            "satay", "groundnut", "nutella", "gianduja", "frangipane", "amaretti",
            "baklava", "nougat", "romesco", "dukkah",
            "muhammara", "amaretto", "pignoli", "kung pao", "korma", "orgeat", "mole",
            "chestnut", "filbert", "marcona", "biscotti"},
    "peanut": {"satay", "groundnut"},
    "shellfish": {"shrimp", "prawn", "crab", "lobster", "clam", "mussel",
                  "scallop", "oyster", "squid", "calamari", "crawfish", "crayfish",
                  "langoustine", "langostino", "scampi", "octopus", "cockle", "whelk",
                  "bouillabaisse", "cioppino", "gumbo", "belacan", "xo sauce", "ebi",
                  "abalone", "conch", "krill", "cuttlefish", "crawdad", "mudbug",
                  "tom yum paste", "bagoong", "kapi", "escargot", "geoduck", "periwinkle"},
    # No "sole", "pike" or "bass": ordinary words ("The Sole Survivor",
    # "Pike Place", "Bass-Boosted") that held dishes with no fish in them.
    # "sea bass" is kept whole. No "caesar" in any family: the dressing's
    # anchovy, egg and parmesan are on its list, and a vegan caesar isn't.
    "fish": {"salmon", "tuna", "cod", "haddock", "halibut", "trout",
             "mackerel", "sardine", "anchovy", "anchovies", "tilapia", "fish sauce",
             "sea bass", "snapper", "pollock", "mahi", "swordfish", "catfish",
             "herring", "flounder", "grouper", "monkfish", "branzino", "carp", "perch",
             "lox", "caviar", "roe", "bonito", "dashi", "worcestershire",
             "nam pla", "surimi", "caesar dressing", "puttanesca"},
    # "buttermilk" is here rather than left to "butter"/"milk": whole-word
    # matching reaches neither half of it, and it is unambiguously dairy —
    # the opposite call from "peanut butter", which is spelled with a dairy
    # word and contains none (see _COMPOUND_EXCEPTIONS).
    "dairy": {"milk", "cheese", "butter", "buttermilk", "cream", "yogurt",
              "yoghurt", "whey", "parmesan", "ghee", "paneer",
              "cheddar", "mozzarella", "ricotta", "feta", "brie", "camembert", "gouda",
              "gruyere", "parmigiano", "pecorino", "provolone", "mascarpone", "halloumi",
              "burrata", "manchego", "emmental", "havarti", "monterey jack", "colby",
              "queso", "quark", "labneh", "skyr", "kefir", "casein", "creme fraiche",
              "custard", "gelato", "cheesecake", "buttercream", "alfredo", "bechamel",
              "tzatziki", "raita", "tiramisu", "carbonara",
              "asiago", "fontina", "gorgonzola", "stilton", "cotija", "romano", "chevre",
              "taleggio", "pepper jack", "velveeta", "fromage", "curds", "beurre",
              "half and half", "half-and-half", "ranch", "dulce de leche", "panna cotta",
              "korma", "lassi", "flan", "creme brulee", "mousse",
              # Round 3. "2%" is folded to "2 percent" (see _fold).
              "grana", "oaxaca", "evaporated", "clotted", "cool whip", "whipped topping",
              "lactose", "boursin", "american slices", "2 percent", "1 percent", "skim"},
    "gluten": set(_WHEAT_WORDS),
    "wheat": set(_WHEAT_WORDS),
    # "whites" is matched as written (see _VERBATIM_ALIASES), never as
    # "white": white wine and white beans are not egg.
    "egg": {"eggs", "mayonnaise", "mayo", "aioli", "meringue", "custard", "hollandaise",
            "bearnaise", "carbonara", "frittata", "quiche", "omelet", "omelette", "souffle",
            "eggnog", "pavlova", "tiramisu",
            "yolk", "brioche", "challah", "remoulade", "tartar sauce", "flan",
            "creme brulee", "mousse", "shakshuka", "lo mein", "ranch", "coleslaw dressing",
            "lemon curd", "zabaglione", "sabayon", "clafoutis", "crepe", "french toast",
            "dutch baby", "albumen", "tamagoyaki", "yorkshire pudding"},
    "soy": {"soya", "tofu", "tempeh", "edamame", "soy sauce", "miso", "tamari", "shoyu",
            "natto", "teriyaki", "hoisin", "ponzu", "yuba", "okara"},
    "sesame": {"tahini", "hummus", "halva", "halvah", "zaatar", "za atar", "gomasio", "benne"},
}
# Aliases matched exactly as written, with no singular/plural twin: an
# egg-white line says "3 whites", and the singular would reach "white
# wine" and "white beans".
# "florentines" (the lace cookie) likewise: chicken or eggs FLORENTINE is
# spinach, not almonds.
_VERBATIM_ALIASES: dict[str, set[str]] = {
    "egg": {"whites"}, "eggs": {"whites"},
    "nut": {"florentines"}, "nuts": {"florentines"},
}
_ALLERGEN_ALIASES["nuts"] = _ALLERGEN_ALIASES["nut"]
_ALLERGEN_ALIASES["peanuts"] = _ALLERGEN_ALIASES["peanut"]
_ALLERGEN_ALIASES["eggs"] = _ALLERGEN_ALIASES["egg"]

# The table as it stood before the 2026-09-30 widening, kept for ONE
# reader: plan_quality's title rule builds its "known food word"
# vocabulary from it, and a word in that vocabulary becomes judgeable
# ("with Orzo" over a list without orzo gets renamed). Widening the allergy
# table must never make the title rule louder (test_title_names_a_real_
# ingredient pins it), so the rule keeps the vocabulary it was tuned on.
_TITLE_VOCABULARY_ALIASES: dict[str, frozenset[str]] = {
    "nut": frozenset({"peanut", "walnut", "almond", "cashew", "pecan", "hazelnut",
                      "pistachio", "macadamia", "pesto", "marzipan", "praline"}),
    "peanut": frozenset({"satay"}),
    "shellfish": frozenset({"shrimp", "prawn", "crab", "lobster", "clam", "mussel",
                            "scallop", "oyster", "squid", "calamari"}),
    "fish": frozenset({"salmon", "tuna", "cod", "haddock", "halibut", "trout",
                       "mackerel", "sardine", "anchovy", "tilapia", "fish sauce"}),
    "dairy": frozenset({"milk", "cheese", "butter", "buttermilk", "cream", "yogurt",
                        "yoghurt", "whey", "parmesan", "ghee", "paneer"}),
    "gluten": frozenset({"flour", "bread", "pasta", "noodles", "couscous", "seitan",
                         "barley", "rye", "bulgur", "breadcrumb", "soy sauce"}),
    "wheat": frozenset({"flour", "bread", "pasta", "noodles", "couscous", "seitan",
                        "barley", "rye", "bulgur", "breadcrumb", "soy sauce"}),
    "egg": frozenset({"eggs", "mayonnaise", "mayo", "aioli", "meringue"}),
    "soy": frozenset({"soya", "tofu", "tempeh", "edamame", "soy sauce", "miso"}),
    "sesame": frozenset({"tahini", "hummus"}),
    "nuts": frozenset(), "peanuts": frozenset(), "eggs": frozenset(),
}


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
_DAIRY_PLANT_WORDS = frozenset({
    "dairy", "dairies", "milk", "milks", "butter", "butters", "cheese", "cheeses",
    "cream", "creams", "yogurt", "yogurts", "yoghurt", "yoghurts",
    "custard", "custards", "alfredo", "alfredos",
    "mousse", "mousses", "flan", "flans", "panna cotta", "panna cottas",
})
_EGG_PLANT_WORDS = frozenset({"egg", "eggs", "mayonnaise", "mayonnaises", "mayo", "mayos", "aioli", "aiolis"})
# The family's own name inside a qualifier ("DAIRY-free butter"). On an
# ingredient line it is discounted with the plant word it qualifies; in a
# name over a list it never is — "Dairy-Free …" in a name is the claim the
# 2026-09-20 rule is about, and the list alone decides past it.
_FAMILY_WORDS = frozenset({"dairy", "dairies", "egg", "eggs"})
_EGG_LABELS = frozenset({"egg", "eggs"})
_PLANT_MOD = (
    r"(?:greek|greek-style|sour|whipped|heavy|plain|shredded|grated|cream|ice|"
    r"coconut|oat|soy|soya|almond|cashew|rice|chocolate|vanilla)"
)

_COMPOUND_EXCEPTIONS: tuple[tuple[frozenset[str], re.Pattern], ...] = (
    (frozenset({"butter", "butters"}), re.compile(
        r"\b(?:peanut|almond|cashew|hazelnut|pistachio|pecan|walnut|macadamia|"
        r"sunflower|pumpkin|sesame|seed|nut|apple|cocoa|cacao|shea|coconut)s?\s+butters?\b"
    )),
    (frozenset({"milk", "milks"}), re.compile(
        r"\b(?:almond|cashew|coconut|hazelnut|hemp|oat|pea|rice|soy|soya)\s+milks?\b"
    )),
    # The plant versions of the other dairy words (2026-09-30, a household
    # with a dairy-free member lost "Coconut Yogurt Parfait"-shaped dishes
    # to the gate). Cream of tartar is a baking acid, not cream.
    (frozenset({"cream", "creams"}), re.compile(
        r"\b(?:coconut|oat|soy|soya|cashew|almond|rice)\s+creams?\b|\bcream\s+of\s+tartar\b"
    )),
    (frozenset({"yogurt", "yogurts", "yoghurt", "yoghurts"}), re.compile(
        r"\b(?:coconut|oat|soy|soya|cashew|almond)\s+yogh?urts?\b"
    )),
    (frozenset({"cheese", "cheeses"}), re.compile(r"\bcashew\s+cheeses?\b")),
    (frozenset({"sugar", "sugars"}), re.compile(r"\bsugar\s+snaps?\b")),
    # Second-round false positives (2026-09-30): foods named after an
    # allergen they don't contain.
    (frozenset({"beer", "beers"}), re.compile(r"\b(?:root|ginger)\s+beers?\b")),
    (frozenset({"chestnut", "chestnuts"}), re.compile(r"\bwater\s+chestnuts?\b")),
    # Round 3: tofu is bean curd, not milk curd; ranch-style beans are a
    # chili-sauce bean; a vegetable gumbo has no shellfish.
    (frozenset({"curds", "curd"}), re.compile(r"\b(?:tofu|bean|soy|soya)\s+curds?\b")),
    (frozenset({"ranch", "ranches"}), re.compile(r"\branch[\s-]style\b")),
    (frozenset({"gumbo", "gumbos"}), re.compile(
        r"\b(?:vegan|vegetable|veggie|plant[\s-]based)\s+(?:okra\s+)?gumbos?\b"
        r"|\bgumbos?\s+vegan$"
    )),
    (frozenset({"caviar", "caviars"}), re.compile(r"\b(?:eggplant|aubergine)\s+caviars?\b")),
    (frozenset({"dashi", "dashis"}), re.compile(r"\b(?:kombu|vegan|shiitake|mushroom|kelp)\s+dashis?\b")),
    # Olive oil is a cooking fat, not an olive. A household that avoids
    # olives got a hard clash on essentially every dinner, because olive
    # oil is in most of them — which puts an allergy-shaped gate in front
    # of Approve every week and teaches the household to click past it.
    # That is the failure this whole check exists to prevent, so the false
    # positive matters more here than the rarity of the restriction.
    (frozenset({"olive", "olives"}), re.compile(r"\bolive\s+oils?\b")),
    # Gluten/wheat's own false positive: a naturally gluten-free flour,
    # noodle or pasta still contains the word that means "gluten" to the
    # alias table above. "Gluten-Free Pasta" made with rice flour flagged a
    # dish that is, by definition, safe for the restriction it tripped.
    (frozenset({"flour", "flours"}), re.compile(
        r"\b(?:rice|almond|chickpea|buckwheat|corn|oat|coconut|tapioca)\s+flours?\b"
    )),
    (frozenset({"noodle", "noodles"}), re.compile(
        r"\b(?:rice|glass|soba|buckwheat)\s+noodles?\b"
    )),
    (frozenset({"pasta", "pastas"}), re.compile(
        r"\b(?:chickpea|lentil|rice)\s+pastas?\b"
    )),
)

# The plant versions a QUALIFIER makes of a dairy or egg word. Kept apart
# from _COMPOUND_EXCEPTIONS because they are a claim, not a food name:
# "coconut milk" is never dairy wherever it is written, but "vegan cheese"
# is only as true as whoever wrote it. So these count on an ingredient
# line (what the dish is made of) and in a label that will be checked
# against a list, and NOT in a name that is final without a list — "Vegan
# Cheese Pizza" planned from chat on its name alone is cheese.
_PLANT_QUALIFIER_EXCEPTIONS: tuple[tuple, ...] = (
    # A dairy word the line or the name itself says is a plant version:
    # "dairy-free butter", "vegan cream cheese", "non-dairy milk". The
    # qualifier has to come FIRST and sit right against the word (one
    # describing word allowed between: "dairy-free GREEK yogurt",
    # "dairy-free COCONUT milk"), so "Dairy-Free Chicken with Cream Sauce"
    # is still held on "cream" — the label covers the words it touches, not
    # the whole dish. A qualifier AFTER the word is not in this table: on
    # an ingredient line it is an option, not a fact ("2 tbsp butter
    # (dairy-free if needed)", "cheese, vegan or regular") and the butter
    # is real butter until the list says otherwise. (A name or a draft's
    # note may use that form — see _TRAILING_PLANT_RE.) Deliberately NOT in
    # the list: buttermilk, whey, ghee, paneer, parmesan and the named
    # cheeses. Emily's 2026-09-20 rule is that a "-free" label can't sneak
    # an allergen in, and "Dairy-Free Buttermilk Pancakes" is the dish that
    # tests it. Nor "lactose-free": lactose-free milk is still milk.
    # The "dairy" of the qualifier itself is in the words too, so the line
    # "dairy-free butter" doesn't trip the family word on its way past.
    (_DAIRY_PLANT_WORDS, re.compile(
        r"\b(?:vegan|plant[\s-]based)\s+"
        r"(?:" + _PLANT_MOD + r"\s+)?"
        r"(?:milk|butter|cheese|cream|yogh?urt|custard|alfredo|mousse|flan|panna\s+cotta)s?(?![-a-z0-9])"
    )),
    # "dairy-free custard" still has its eggs: this one says nothing to an
    # egg allergy (the third element: the avoidances it never speaks for).
    (_DAIRY_PLANT_WORDS, re.compile(
        r"\b(?:dairy[\s-]free|non[\s-]dairy)\s+"
        r"(?:" + _PLANT_MOD + r"\s+)?"
        r"(?:milk|butter|cheese|cream|yogh?urt|custard|alfredo|mousse|flan|panna\s+cotta)s?(?![-a-z0-9])"
    ), _EGG_LABELS),
    # Coconut custard is set with starch, not cream — but usually still
    # with egg, so it is dairy's exception only.
    (frozenset({"custard", "custards"}), re.compile(r"\bcoconut\s+custards?\b"), _EGG_LABELS),
    # A vegan caesar dressing has no anchovy (nor egg or parmesan).
    (frozenset({"caesar dressing", "caesar dressings"}), re.compile(
        r"\b(?:vegan|plant[\s-]based)\s+caesar\s+dressings?\b"
    )),
    # The same for egg: vegan or egg-free mayonnaise is made without it.
    (_EGG_PLANT_WORDS, re.compile(
        r"\b(?:vegan|plant[\s-]based|egg[\s-]free|eggless)\s+(?:mayonnaise|mayo|aioli)s?(?![-a-z0-9])"
    )),
)


# The other half of that same fix: a dish or ingredient line that says
# outright that it is gluten-free doesn't need a per-flour-type entry above
# to be believed. Only cancels the GLUTEN/WHEAT alias words for the segment
# it appears in — a segment naming another allergen (nuts, dairy...) is
# untouched, so "Almond Flour Cake" is still a clash for a nut allergy even
# though it is none for gluten.
_GLUTEN_FREE_SEGMENT_RE = re.compile(r"\bgluten[\s-]?free\b|\bgf\b")
_GLUTEN_WHEAT_LABELS = frozenset({"gluten", "wheat"})


# A dish's NAME and the planner's dish_note are labels: they say what the
# dish is, and they say what it leaves out in the same breath — "Dairy-Free
# Pancakes", "keep it dairy-free", "no cheese", "olive oil instead of
# butter". Matching those words as if they were ingredients held back five
# scrambles, three parfaits and a teriyaki bowl in one household's first
# week (2026-09-30), and every one of them cost a re-pick call or an empty
# slot. So in a name or a note, an allergen word that is itself negated
# doesn't count:
#
#   "<word>-free" / "<word> free"         dairy-free, nut-free, egg free
#                                         (never "free range" / "free-range")
#   "non-<word>" / "non <word>"           non-dairy
#   "no/without/instead of/rather than/in place of <word>"
#                                         no cheese, instead of butter
#   "<dairy word> dairy-free/vegan"       Greek Yogurt (Dairy-Free) Parfait
#
# Only the negated OCCURRENCE of that ONE word is cancelled: "no cheese,
# finish with butter" is still butter, "no nuts or peanut sauce" is still
# peanut, "Nut Free Satay" is still satay. The word has to come straight
# after the negator (an article or "added"/"extra" between is fine), so "no
# fuss cheese toastie" and "No-Bake Cheese Tart" are still cheese.
#
# WHEN it applies is the safety half (see check_meal_conflicts'
# `negate_labels`): ONLY a draft dish with no ingredient list yet — the
# menu draft, whose recipe pass will match a real list before anyone shops
# or cooks. A dish WITH a list keeps a strict name (the name backstops any
# gap in the alias table: "Dairy-Free Soup" over "half-and-half"), and a
# dish that is final without a list (a freeform name planned from chat, a
# recipe the recipe pass failed to write) has nothing but its name. An
# INGREDIENT LINE never gets this. Like the compounds above, it only
# applies to a single-word avoidance. (Verifier rounds 1–2, 2026-09-30.)
_FREE_AFTER_RE = re.compile(r"[\s-]free(?![-a-z0-9]|\s+range)")
_NON_BEFORE_RE = re.compile(r"(?:^|\s)non[\s-]$")
_NEG_BEFORE_RE = re.compile(
    r"(?:^|\s)(?:no|without|instead\s+of|rather\s+than|in\s+place\s+of)\s+"
    r"(?:(?:the|any|added|extra)\s+)?$"
)
# The plant qualifier written after the word. On an ingredient line that
# is an option ("butter (dairy-free if needed)") and never counts; in a
# name or a draft's note it is the dish describing itself.
_TRAILING_PLANT_RE = re.compile(
    r"\s+(?:dairy[\s-]free|non[\s-]dairy|vegan)(?![-a-z0-9])"
)
_TRAILING_PLANT_WORDS = _DAIRY_PLANT_WORDS - {"dairy", "dairies"}


def _negated(variant: str, match: re.Match, text: str) -> bool:
    """Whether this occurrence in a name or a note is the thing left out."""
    if _FREE_AFTER_RE.match(text, match.end()):
        return True
    if variant in _TRAILING_PLANT_WORDS and _TRAILING_PLANT_RE.match(text, match.end()):
        return True
    before = text[:match.start()]
    return bool(_NON_BEFORE_RE.search(before) or _NEG_BEFORE_RE.search(before))


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
        # "…but peanuts are fine" lifts the dishes that are peanut by
        # definition (satay) out of the "nut" family too.
        for base in _ALLERGEN_ALIASES.get(word, set()):
            excluded |= _keyword_variants(base)
    out: list[tuple[str, list[set[str]]]] = []
    for phrase in phrases:
        groups: list[set[str]] = []
        kept: list[str] = []
        for word in phrase:
            variants: set[str] = set()
            for base in {word} | _ALLERGEN_ALIASES.get(word, set()):
                variants |= _keyword_variants(base)
            variants |= _VERBATIM_ALIASES.get(word, set())
            variants -= excluded
            if variants:
                groups.append(variants)
                kept.append(word)
        if groups:
            out.append((" ".join(kept), groups))
    return out


def _discounted(variant: str, match: re.Match, text: str, mode: str = "line", label: str = "") -> bool:
    """Whether this occurrence sits inside a compound that neutralises it.

    `mode` is the segment's kind (see check_meal_conflicts): a "strict"
    name reads only the food-name compounds, never a plant qualifier; a
    "listed" name reads plant qualifiers but never lets the family's own
    name ("Dairy-Free …") off; a "line" or a draft "label" reads them all.
    An entry's third element names the avoidances it never speaks for."""
    tables = _COMPOUND_EXCEPTIONS + (_PLANT_QUALIFIER_EXCEPTIONS if mode != "strict" else ())
    if mode == "listed" and variant in _FAMILY_WORDS:
        tables = _COMPOUND_EXCEPTIONS
    for entry in tables:
        words, compound = entry[0], entry[1]
        if variant not in words:
            continue
        if len(entry) > 2 and label in entry[2]:
            continue
        for found in compound.finditer(text):
            if found.start() <= match.start() and match.end() <= found.end():
                return True
    return False


def _find_variant(
    variant: str, text: str, start: int, discountable: bool, mode: str = "line", label: str = "",
) -> re.Match | None:
    """The first whole-word occurrence at or after `start` that actually
    counts. Only a draft "label" segment reads negations (see _negated)."""
    for m in re.finditer(r"\b" + re.escape(variant) + r"\b", text):
        if m.start() < start:
            continue
        if discountable and _discounted(variant, m, text, mode, label):
            continue
        if discountable and mode == "label" and _negated(variant, m, text):
            continue
        return m
    return None


def _phrase_in(groups: list[set[str]], text: str, mode: str = "line", label: str = "") -> str | None:
    """
    Whether every word of a phrase appears in `text`, in order — and if so,
    the words that were actually found ("buttermilk", not the "dairy" it
    was found for), so a held-back dish can say what it was held for.

    In order rather than strictly adjacent, so "red meat" still finds "red
    minced meat" — but within ONE stretch of text (see _matches), so it
    cannot be assembled out of a word in the dish's name and another in an
    unrelated ingredient three lines down.
    """
    pos = 0
    discountable = len(groups) == 1
    found: list[str] = []
    for variants in groups:
        best: re.Match | None = None
        for variant in variants:
            m = _find_variant(variant, text, pos, discountable, mode, label)
            if m and (best is None or m.start() < best.start()):
                best = m
        if best is None:
            return None
        found.append(best.group(0))
        pos = best.end()
    return " ".join(found)


def _matches(
    terms: list[tuple[str, list[set[str]]]],
    segments: list[str],
    gluten_free_segments: set[int] | None = None,
    label_segments: set[int] | None = None,
    strict_segments: set[int] | None = None,
    listed_segments: set[int] | None = None,
) -> str | None:
    """The label of the first avoidance found — see _match_detail."""
    detail = _match_detail(terms, segments, gluten_free_segments, label_segments, strict_segments,
                           listed_segments)
    return detail[0] if detail else None


def _match_detail(
    terms: list[tuple[str, list[set[str]]]],
    segments: list[str],
    gluten_free_segments: set[int] | None = None,
    label_segments: set[int] | None = None,
    strict_segments: set[int] | None = None,
    listed_segments: set[int] | None = None,
) -> tuple[str, str] | None:
    """
    The first avoidance found in any one of `segments`, or None.

    Segments, not one joined blob: a phrase has to land inside a single
    stretch of text — the dish's name, or one ingredient line — because a
    two-word food spread across two unrelated ingredients is a coincidence,
    not a clash.

    `gluten_free_segments` names the indices of segments that said outright
    they're gluten-free ("Gluten-Free Pasta", "GF flour tortillas"). Those
    segments are skipped only for a GLUTEN or WHEAT avoidance — a nut or
    dairy restriction still has to look at them.

    `label_segments` names the segments that are a name or a planner's note
    rather than an ingredient line; only those read "dairy-free" / "no
    cheese" as the thing left out (see _negated). `strict_segments` are a
    name or note matched strictly: not even a plant qualifier counts there.
    `listed_segments` are a name over a real list: no negation, and the
    family's own name in a qualifier never counts as left out.

    Returns (the avoidance's label, the words that matched it), or None.
    """
    gluten_free_segments = gluten_free_segments or set()
    label_segments = label_segments or set()
    strict_segments = strict_segments or set()
    listed_segments = listed_segments or set()
    for label, groups in terms:
        skip_if_gluten_free = label in _GLUTEN_WHEAT_LABELS
        # The name is read LAST: when a dish trips on both, the word from
        # its list ("buttermilk") is the one worth saying, not the label
        # ("Dairy-Free …") that tripped too.
        order = list(range(1, len(segments))) + [0] if segments else []
        for i in order:
            segment = segments[i]
            if skip_if_gluten_free and i in gluten_free_segments:
                continue
            mode = ("label" if i in label_segments else "strict" if i in strict_segments
                    else "listed" if i in listed_segments else "line")
            word = _phrase_in(groups, segment, mode, label)
            if word:
                return label, word
    return None


def _name_words(name: str) -> set[str]:
    """A member's name, lowercased and split into words, for `drop=`."""
    return {w for w in re.sub(r"[^a-z0-9\s]", " ", (name or "").lower()).split()}


def _named_member(text: str, member_names: list[str]) -> str | None:
    """
    Which household member (if any) a freeform sentence names —
    "Emily is allergic to pineapple" names Emily; "no shellfish in this
    house" names nobody. Whole-word matched, case-insensitive.

    Factored out of `_avoidances()` so `db._backfill_allergy_notes_from_facts`
    can ask the identical question when deciding whose member record a
    fact's phrases belong on — a backfill that used a different rule for
    "who is this about" than the live check would drift from it silently.
    """
    lowered = (text or "").lower()
    return next(
        (n for n in member_names if re.search(r"\b" + re.escape(n.lower()) + r"\b", lowered)),
        None,
    )


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
        name_words = _name_words(m["name"])
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
        named = _named_member(text, member_names)
        phrases, excepted = _fact_keywords(text, drop=_name_words(named))
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


# ---------- The draft's "One thing to settle" card (flows 3) -------------
# Emily's approved 2026-09-08 Meals design replaces the old review band with
# the week card itself, and gives a HARD allergen clash one urgent-tint card
# above it. That card's sentence is written here, beside the data, for the
# same reason _conflicts_note is: the UI must not be the place that decides
# what a clash means.
#
# Two sentences rather than one, because the two moments are different:
# _conflicts_note ends "worth a look before you approve" (a nudge attached
# to a button); this one is the whole content of a card that already says
# "One thing to settle" above it and offers the two ways out below it, so
# it only has to state the fact.

_ALLERGY_LABEL_RE = re.compile(r"\ballerg\w*", re.I)


def _weekday(date_str: str | None) -> str:
    """"Wednesday" for an ISO date, or "" for a plan with no real dates."""
    if not date_str:
        return ""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")
    except (TypeError, ValueError):
        return ""


def _clash_sentence(c: dict) -> str:
    """
    "Wednesday's Pineapple Salsa has pineapple, which Emily is allergic to."

    The dish is named as the plan spells it, never shortened — a guessed
    short form ("the salsa") is a guess about someone's food, and getting it
    wrong on an allergy card is the one place this app cannot afford to be
    approximately right.

    "is allergic to" only when the restriction actually says allergy;
    anything else the household wrote down is "can't have", which is true of
    every avoidance including an allergy.
    """
    meal = c["meal"]
    member = c["member"]
    when = _weekday(c.get("date"))
    subject = f"{when}’s {meal}" if when else meal
    matched = c.get("matched") or []
    if isinstance(matched, str):
        matched = [matched]
    terms = [t for t in matched if t]
    # The matched term only earns a clause when it says something the dish's
    # own name doesn't already say. "Pineapple Salsa has pineapple" is
    # noise; "Pineapple Salsa" plus the person is the whole fact.
    term = terms[0] if terms else ""
    # Always a full sentence, even when the dish name already says it:
    # "Pineapple Salsa Bowls has pineapple, which Emily is allergic to."
    # A fragment is the wrong shape for the one card about someone's allergy.
    has = f"{subject} has {term}" if term else f"{subject} is on the plan"
    if member:
        tail = (
            f"which {member} is allergic to"
            if _ALLERGY_LABEL_RE.search(c.get("restriction") or "")
            else f"which {member} can’t have"
        )
    else:
        tail = "which you’ve asked me to avoid"
    return f"{has}, {tail}."


def _settle(conflicts: list[dict]) -> dict | None:
    """
    The hard clash the draft has to settle, or None when there isn't one.

    `count` is in MEALS, same as _conflicts_note counts them: one dish on
    five nights is one thing to settle, and one dish tripping two facts is
    also one. Above one meal the card falls back to _conflicts_note's own
    wording rather than inventing a second multi-clash sentence — and
    `meal`/`date` still name the FIRST one, because "Swap it" has to land
    somewhere and the earliest clash is the one the household meets first.
    """
    hard = [c for c in conflicts if c["severity"] == "hard"]
    if not hard:
        return None
    hard = sorted(hard, key=lambda c: (c.get("date") or "", c["meal"]))
    meals: list[str] = []
    for c in hard:
        if c["meal"] not in meals:
            meals.append(c["meal"])
    first = hard[0]
    return {
        "note": _clash_sentence(first) if len(meals) == 1 else _conflicts_note(hard),
        "meal": first["meal"],
        "date": first.get("date"),
        "member": first["member"],
        "count": len(meals),
    }


def _soft_note(conflicts: list[dict]) -> str | None:
    """
    "Vineeth isn't keen on Thursday's Thai Green Curry." — the one quiet line
    under the week card. A taste veto is a preference, so it never gets a
    card and never gates approval; it is said once, plainly, and left there.

    Above one it stops naming names: three sentences about who dislikes what
    is a list, and a list of preferences under a week nobody has to change is
    exactly the chrome this screen's redesign removed.
    """
    soft = [c for c in conflicts if c["severity"] == "soft" and c["member"]]
    if not soft:
        return None
    soft = sorted(soft, key=lambda c: (c.get("date") or "", c["meal"]))
    seen = {(c["member"], c["date"], c["meal"]) for c in soft}
    if len(seen) == 1:
        c = soft[0]
        when = _weekday(c.get("date"))
        dish = f"{when}’s {c['meal']}" if when else c["meal"]
        return f"{c['member']} isn’t keen on {dish}."
    n = len({(c["date"], c["meal"]) for c in soft})
    subject = _spell(n)
    return (
        f"{subject[0].upper()}{subject[1:]} meals have somebody at the table "
        "who isn’t keen."
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


def _fold(text: str) -> str:
    """Lowercase, zero-width and soft-hyphen characters dropped, accents
    and full-width characters folded ("Gruyère", "crème fraîche", "Ｄairy"),
    punctuation to spaces,
    whitespace collapsed — the one shape every segment is matched in."""
    # Every invisible format character (Unicode Cf: zero-width space and
    # joiners, soft hyphen, bidi marks, BOM…) is dropped, so it can't split
    # a word the matcher is looking for.
    text = "".join(ch for ch in (text or "") if unicodedata.category(ch) != "Cf")
    text = unicodedata.normalize("NFKD", text)
    # "2%" is milk on an ingredient line ("2 cups 2%").
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s-]", " ", text)).strip()


def check_meal_conflicts(
    meal_name: str,
    ingredients: list[dict] | None = None,
    sides: list[dict] | None = None,
    avoidances: list[dict] | None = None,
    negate_labels: bool | None = None,
) -> list[dict]:
    """
    Every avoidance ONE dish trips — the per-dish half of
    check_plan_conflicts, pulled out so a dish can be checked before it is
    ever written to a plan.

    swap_in_place is why this exists as its own function: it has a dish the
    model just picked and no entry yet, and the honest moment to refuse an
    allergen is before the swap lands, not after. Sharing the matcher rather
    than writing a second one is the whole point — two keyword checks that
    disagree about what counts as a clash is exactly the bug this app cannot
    afford, and a swap that slipped past a looser copy would land on the
    plan and only be caught by the draft's own banner later.

    Returns one dict per clash, with meal/member/restriction/source/
    severity/matched/matched_word — no date and no component_category, which belong to
    the entry a dish is planned on, not to the dish.

    Pass `avoidances` when checking many dishes in a row (check_plan_conflicts
    does) so the household's restrictions are read once, not per meal.

    `negate_labels=True` is only for a DRAFT dish that will get a checked
    ingredient list before anyone shops or cooks, and only takes effect
    when no list is given: then the name (and the planner's dish_note) may
    read "dairy-free" / "no cheese" as what the dish leaves out (see
    _negated). Everywhere else — any dish with a list, and any dish that is
    final without one — the name is matched strictly, so a "-free" label
    never sneaks the allergen in and still backstops the alias table
    (2026-09-30: "Dairy-Free Soup" over "half-and-half").
    """
    name = (meal_name or "").strip()
    if not name:
        return []
    if avoidances is None:
        avoidances = _avoidances()
    if not avoidances:
        return []
    # The dish's name and each ingredient line SEPARATELY, not one joined
    # blob. A multi-word avoidance has to be found inside a single one of
    # them — "red meat" assembled out of "red pepper" in the name and
    # "minced meat" four lines later is a coincidence, not a clash.
    # Whitespace collapsed as well as punctuation stripped, so a two-word
    # term ("soy sauce") still matches an ingredient written with odd
    # spacing.
    # Each segment carries whether it is a LABEL (the name, or a planner's
    # dish_note handed over as an `is_dish_note` line by
    # allergen_gate.ingredients_for) or an ingredient line; only a label
    # reads "dairy-free" / "no cheese" as what the dish leaves out.
    lines = [
        (i, False) if isinstance(i, str) else ((i.get("item") or ""), bool(i.get("is_dish_note")))
        for i in (ingredients or []) if isinstance(i, (str, dict))
    ]
    has_list = any(text.strip() and not is_note for text, is_note in lines)
    # The name's (and a note's) kind:
    #   "label"  — a DRAFT dish with NO list: reads "-free", "no X" and plant
    #              qualifiers, because the recipe pass will match a real list
    #              before anyone shops or cooks;
    #   "listed" — any dish WITH a list: the list decides; the name reads a
    #              leading plant qualifier ("Vegan Alfredo") but never lets
    #              "Dairy-Free …" off, so it still backstops the alias table;
    #   "strict" — no list and not a draft: the name is all there is.
    # An ingredient line is "line": leading plant qualifiers only.
    if has_list:
        name_kind = "listed"
    elif negate_labels:
        name_kind = "label"
    else:
        name_kind = "strict"
    raw_segments = [(name, name_kind)]
    raw_segments += [(text, name_kind if is_note else "line") for text, is_note in lines]
    raw_segments += [((i.get("item") or ""), "line") for i in _plates.side_ingredients(sides)]
    normalised = [(_fold(s), kind) for s, kind in raw_segments]
    normalised = [(s, kind) for s, kind in normalised if s]
    segments = [s for s, _ in normalised]
    label_segments = {i for i, (_, kind) in enumerate(normalised) if kind == "label"}
    strict_segments = {i for i, (_, kind) in enumerate(normalised) if kind == "strict"}
    listed_segments = {i for i, (_, kind) in enumerate(normalised) if kind == "listed"}
    gluten_free_segments = {
        i for i, s in enumerate(segments) if _GLUTEN_FREE_SEGMENT_RE.search(s)
    }
    hits = []
    for avoidance in avoidances:
        detail = _match_detail(avoidance["terms"], segments, gluten_free_segments,
                               label_segments, strict_segments, listed_segments)
        if not detail:
            continue
        matched, matched_word = detail
        hits.append({
            "meal": name,
            "member": avoidance["member"],
            # Kept under the original key so existing callers/readers of
            # this result don't have to change.
            "restriction": avoidance["label"],
            "source": avoidance["source"],
            "severity": avoidance["severity"],
            "matched": matched,
            # The words actually found ("buttermilk" for a "dairy"
            # avoidance) — for the log line that says why a dish was held.
            "matched_word": matched_word,
        })
    return hits


def check_plan_conflicts(weekly_plan_id: int | None = None) -> dict:
    """
    Flag (don't block) any meals on a plan that look like they clash with
    something the household has said to keep off the table:

      - a member's saved dietary restriction/allergy
        (set_member_dietary_restrictions),
      - a What-we-know fact marked hard (add_fact with hard=true) — an
        allergy written as a note is still an allergy,
      - a standing household dislike, at a lower severity,
      - a dish somebody EATING THAT NIGHT is personally on record as
        disliking (see taste_verdict.plan_taste_conflicts), also soft.
        One hater at the table vetoes the dish for that table, and a night
        they aren't eating is the overrule — which is why this one is
        computed per slot, against that slot's attendance, rather than
        once against the household.

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
    sentence, or None when there is nothing to warn about — plus the two
    pieces the Meals draft renders in different places: `settle` (the hard
    clash's card: note/meal/date/member/count, or None) and `soft_note` (the
    one quiet line about a taste veto, or None).
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if plan.get("weekly_plan_id") is None:
        return {
            "weekly_plan_id": None, "conflicts": [], "note": None,
            "settle": None, "soft_note": None,
        }

    # Computed whatever the household has (or hasn't) written down to
    # avoid: a per-person taste veto is a different question from an
    # allergy, and a household with no restrictions on file at all still
    # has people with opinions. Never allowed to cost the check its
    # allergy half if it fails.
    try:
        taste_conflicts = _taste_verdict.plan_taste_conflicts(plan["meals"])
    except Exception:
        logger.exception("Per-person taste check failed for plan %s", plan["weekly_plan_id"])
        taste_conflicts = []

    avoidances = _avoidances()
    if not avoidances:
        return {
            "weekly_plan_id": plan["weekly_plan_id"],
            "conflicts": taste_conflicts,
            # Only ever about the hard ones (see _conflicts_note), and a
            # taste veto is never hard.
            "note": None,
            "settle": None,
            "soft_note": _soft_note(taste_conflicts),
        }

    recipes_by_name = {r["name"].lower(): r for r in _recipes.list_recipes()}
    conflicts = list(taste_conflicts)
    for meal in plan["meals"]:
        name = (meal.get("meal") or "").strip()
        # A slot with nothing in it, or one deliberately left empty/open,
        # has no dish to clash with.
        if not name or meal.get("slot_state") in ("planned_empty", "open"):
            continue
        recipe = recipes_by_name.get(name.lower())
        for hit in check_meal_conflicts(
            name,
            ingredients=(recipe or {}).get("ingredients"),
            # A dish on a DRAFT whose recipe hasn't been written yet may be
            # read on its label ("Dairy-Free Pancakes"): the recipe pass
            # matches its real list at approval. Anything else is matched
            # the ordinary way (strictly, unless it has a list).
            negate_labels=True if (
                (recipe or {}).get("details_pending") and not plan.get("approved_at")
            ) else None,
            # Any side the app attached to complete this plate (see
            # plates.py) counts too — its ingredients are what's actually
            # on the table, same as the dish's own. Without this, a clean
            # dinner with a side containing the allergen slipped through
            # entirely: the check only ever looked at the meal name and
            # the recipe's own ingredients.
            sides=meal.get("sides"),
            avoidances=avoidances,
        ):
            conflicts.append({
                **hit,
                "date": meal.get("date"),
                "component_category": meal.get("component_category"),
            })
    return {
        "weekly_plan_id": plan["weekly_plan_id"],
        "conflicts": conflicts,
        "note": _conflicts_note(conflicts),
        # The two halves the Meals draft renders separately (flows 3): a
        # hard clash is a card above the week with two ways out, a soft one
        # is a single line under it.
        "settle": _settle(conflicts),
        "soft_note": _soft_note(conflicts),
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
        "SELECT id, name, color FROM members WHERE household_id = ? AND LOWER(TRIM(age_group)) = 'adult' ORDER BY id ASC",
        (household_id(),),
    ).fetchall()
    # One rule for the letters (_shared.display_initials): two adults who
    # share a first letter are told apart here the same way the day sheet
    # and the "Who's this?" pick tell them apart.
    initials = household_initials(conn)
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
        out.append({"name": r["name"], "initial": initials.get(r["id"], "?"), "color": color})
    return out
