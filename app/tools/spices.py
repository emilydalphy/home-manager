"""
Spices and dried herbs: one opt-in section on the grocery list (Loop Board,
Emily, 2026-09-13).

    "put all the spices together under one section and make it a section
    where it's 'All the spices you need for the recipes this week, select
    the ones you want to add to the list to buy' ... we can assume that
    spices and herbs they probably already have some at home ... It also
    takes up a lot of space and scrolling to go through each spice."

The list assumes a spice rack. A recipe's cumin, paprika or oregano no
longer lands under Pantry as a thing to buy: at ingest it becomes a
grocery line with status 'spice' — on the list's "Spices this week"
section, UNTICKED, off every count, merge, sort queue and trip — and a
tick is what makes it an ordinary 'needed' line in its store. Untick puts
it back. That is the whole state machine: 'spice' <-> 'needed', and the
rest of the grocery code never has to know a spice from a pepper.

What counts is a maintained LIST OF NAMES below, not a heuristic on the
word "spice": the classifier is deliberately boring so it can be argued
with one line at a time. Fresh herbs stay in produce — a bunch of
cilantro is bought each week — so a name that is fresh by default (basil,
cilantro, parsley, mint, dill, chives, rosemary, thyme, sage, tarragon)
counts only when the recipe says "dried" or "dry", and "fresh" anything
never counts. Salt, pepper and cooking oils are treated as spice-rack
things too: they are the same "you almost certainly have this" shape
(assumption, recorded in the ticket report — one line to change).

Memory across weeks IS the staples (Loop Board "Staples gets sections —
and the spice rack is one of them", Emily, 2026-09-13): every spice the
household has bought through the list is a staple in the Spices section
(staples.seed_spice_staples / record_staple_purchase), with a cadence
that starts at RECENTLY_BOUGHT_DAYS and is learned from real purchases.
The card reads that one record three ways: a spice bought within its
cadence is at home and not offered — the section says which ones it is
leaving out, so "all the spices the recipes need" stays true; a spice
whose cadence has run out is PRE-TICKED (probably running low), which is
the same one-tap check a staple gets on the list; and an untick on a
pre-ticked spice is "we have plenty", in the staple's own words. No
counts, no jars to enter: the rack is inferred from what came home.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

from ..db import get_conn, write
from ._shared import household_id
from . import grocery as _grocery
from . import staples as _staples

# A spice staple's first cadence, before Pomona has seen the jar bought
# twice: bought within this many days means "at home, not offered". Eight
# weeks — a jar lasts months, and being wrong here costs one manual add
# ("cumin"), which merges straight onto the pending line and ticks it.
RECENTLY_BOUGHT_DAYS = 56

# The classifier. Names are matched on their merge key (lowercased,
# last-word singularised — see grocery._merge_key), with descriptor words
# stripped first (ground, whole, smoked, sweet, hot, dried, dry, flaked,
# crushed, kosher, sea, black, white, fine, coarse, red, powdered).
# Multi-word entries match as a whole; single words match the last word of
# the cleaned name ("smoked spanish paprika" -> paprika).
_SPICES = {
    # spices
    "cumin", "paprika", "coriander", "turmeric", "cinnamon", "nutmeg", "clove",
    "cardamom", "allspice", "fennel seed", "fenugreek", "mustard seed",
    "caraway", "anise", "star anise", "saffron", "sumac", "mace", "juniper",
    "chili powder", "chilli powder", "chili flake", "chilli flake",
    "red pepper flake", "pepper flake", "crushed red pepper", "red chili flake",
    "red chili powder", "dried red chili", "cayenne", "cayenne pepper",
    "chipotle powder", "ancho powder", "curry powder", "garam masala",
    "lemon pepper", "vanilla bean", "vanilla pod", "dill weed", "steak spice",
    "onion flake", "spice",  # "... spice" is a blend: steak spice, cajun spice, five spice
    "five spice", "five-spice", "chinese five spice", "za'atar", "zaatar",
    "ras el hanout", "berbere", "harissa powder", "old bay", "taco seasoning",
    "italian seasoning", "herbes de provence", "poultry seasoning",
    "pumpkin pie spice", "everything bagel seasoning", "cajun seasoning",
    "creole seasoning", "jerk seasoning", "steak seasoning", "seasoning",
    "garlic powder", "onion powder", "ginger powder", "ground ginger",
    "celery seed", "celery salt", "sesame seed", "poppy seed", "nigella seed",
    "peppercorn", "black peppercorn", "white pepper", "black pepper", "pepper",
    "salt", "sea salt", "kosher salt", "flaky salt", "table salt",
    "msg", "bouillon", "bouillon cube", "stock cube", "vanilla extract",
    "vanilla", "almond extract", "baking soda", "baking powder", "cream of tartar",
    "bay leaf",
    # dried herbs (dried by default when written bare)
    "oregano", "marjoram", "dried basil", "dried cilantro", "dried parsley",
    "dried mint", "dried dill", "dried chive", "dried rosemary", "dried thyme",
    "dried sage", "dried tarragon", "dried oregano", "dried marjoram",
    "dried chili",  # every spelling folds to this one — see _CHILI
    "salt and pepper",
    # oils (spice-rack things, by assumption)
    "olive oil", "extra virgin olive oil", "vegetable oil", "canola oil",
    "sunflower oil", "sesame oil", "toasted sesame oil", "coconut oil",
    "avocado oil", "peanut oil", "grapeseed oil", "cooking oil", "oil",
    "cooking spray",
}

# Herbs that are FRESH when written bare — a bunch of mint is produce.
_FRESH_BY_DEFAULT = {
    "basil", "cilantro", "coriander leaf", "parsley", "mint", "dill", "chive",
    "rosemary", "thyme", "sage", "tarragon", "lemongrass", "curry leaf",
}

_DESCRIPTORS = {
    "ground", "whole", "smoked", "sweet", "hot", "mild", "flaked", "crushed",
    "kosher", "sea", "fine", "coarse", "powdered", "cracked", "freshly", "grated",
    "spanish", "hungarian", "organic", "toasted", "thai", "indian",
    # a bouillon cube's flavour, a chili powder's chili
    "beef", "chicken", "vegetable", "fish", "chipotle", "ancho", "guajillo",
}
_DRIED_WORDS = {"dried", "dry"}


def is_spice(name: str) -> bool:
    """
    Whether a recipe ingredient belongs in the "Spices this week" section
    rather than a store aisle. Name-based, from the list above, tried
    from the most specific reading to the least:

      1. the whole name ("ground ginger", "black pepper");
      2. the name with descriptors stripped ("smoked spanish paprika" ->
         "paprika"), which is also where a fresh-by-default herb answers
         "only if dried";
      3. the name without its form word ("cumin seeds" -> "cumin",
         "cinnamon sticks" -> "cinnamon");
      4. the last word alone, for the single-word entries that survive a
         modifier ("flaky sea salt") — never for the words in _NOT_ALONE,
         which is what keeps a bell pepper and a garlic clove in produce.
    """
    cleaned = " ".join((name or "").strip().lower().replace(",", " ").split())
    if not cleaned:
        return False
    words = ["chili" if w in _CHILI else w for w in cleaned.split(" ")]
    # Anything the recipe calls fresh is bought fresh, whatever it is.
    if "fresh" in words:
        return False
    dried = any(w in _DRIED_WORDS for w in words)
    words = [w for w in words if w not in _DRIED_WORDS]
    if not words:
        return False
    whole = _leaf(_grocery._merge_key(" ".join(words)))
    if whole in _SPICES or (dried and ("dried " + whole) in _SPICES):
        return True
    stripped = [w for w in words if w not in _DESCRIPTORS] or words
    key = _leaf(_grocery._merge_key(" ".join(stripped)))
    if not key:
        return False
    last = key.split(" ")[-1]
    # A fresh-by-default herb counts only when the recipe said dried.
    if key in _FRESH_BY_DEFAULT or last in _FRESH_BY_DEFAULT:
        return dried
    if key in _SPICES or (dried and ("dried " + key) in _SPICES):
        return True
    if last in _FORM_WORDS and len(stripped) > 1:
        base = _grocery._merge_key(" ".join(stripped[:-1]))
        if base in _SPICES and base not in _NOT_ALONE:
            return True
    return last in _SPICES and last not in _NOT_ALONE


# The word a spice comes as: "cumin seeds", "cinnamon sticks", "vanilla
# pods", "paprika powder", "saffron threads". Dropped so the spice
# underneath can be matched.
_FORM_WORDS = {"seed", "stick", "pod", "powder", "flake", "extract", "leaf", "thread", "strand"}


def _leaf(key: str) -> str:
    """The merge key singularises "leaves" to "leave"; every list here says "leaf"."""
    return key[:-6] + " leaf" if key.endswith(" leave") else key

# Single words that are spices only inside a longer name. "pepper" alone is
# the black kind (grocery._NUMBER_CHANGES_MEANING says why "peppers" is
# not), but "bell pepper" and "jalapeño pepper" end in it and are produce;
# "cloves" alone are the spice, "garlic cloves" are not; a bare "chili" is
# a fresh pepper, only a dried one is a spice-rack thing.
# One spelling for the chili pepper, whichever the recipe used (the merge
# key leaves these alone on purpose — grocery._NUMBER_CHANGES_MEANING).
_CHILI = {"chili", "chilli", "chile", "chilies", "chillies", "chiles", "chilis"}

_NOT_ALONE = {
    "pepper", "clove", "chili",
    "seed", "powder", "leaf", "cube", "extract", "flake", "stick", "pod",
    "thread", "strand", "vanilla",
}


def _rack(conn) -> dict[str, dict]:
    """The spice rack as the staples know it: merge key -> the staple row,
    for every staple in the Spices section, seeding first so a spice bought
    before this existed counts too."""
    _staples.seed_spice_staples(conn)
    rows = conn.execute("SELECT * FROM staples WHERE household_id = ?", (household_id(),)).fetchall()
    return {
        _grocery._merge_key(r["item"]): r
        for r in rows
        if _staples.section_for(r["item"], r["category"]) == _staples.SECTION_SPICES
    }


def _bought_lately(staple, today: date) -> bool:
    """Bought within its own cadence — the jar is at home. A "we have
    plenty" answer moves the due date, not this: it was not bought."""
    last = _staples._parse(staple["last_bought_at"])
    return last is not None and last + timedelta(days=staple["cadence_days"]) > today


def _due(staple, today: date) -> bool:
    return not staple["paused"] and staple["next_due_at"] <= today.isoformat()


def bought_recently(item: str, conn=None) -> bool:
    """Whether the spice rack says this jar came home within its cadence."""
    own = conn is None
    if own:
        conn = get_conn()
    staple = _rack(conn).get(_grocery._merge_key(item))
    if own:
        conn.close()
    return bool(staple) and _bought_lately(staple, _staples._today())


def list_spices_this_week() -> dict:
    """
    The "Spices this week" section: every spice line the week's recipes put
    on the list (status 'spice', unticked) plus any spice already ticked
    onto the list (a 'needed' or 'in_cart' line whose name is a spice),
    each with `ticked`. The spice rack (the Spices section of staples)
    pre-answers it: a pending spice bought within its cadence is left out
    and named in `recently_bought`; one whose cadence has run out is ticked
    here and now — an ordinary needed line carrying its staple_id, `due`
    so the card can say "probably running low" — and stays ticked until
    someone says otherwise (tick_spice's untick is "we have plenty").
    """
    conn = get_conn()
    rack = _rack(conn)
    today = _staples._today()
    rows = conn.execute(
        "SELECT id, item, quantity, category, status, store, staple_id FROM grocery_items "
        "WHERE household_id = ? AND status IN ('spice', 'needed', 'in_cart') AND excluded_from_list = 0 "
        "ORDER BY item",
        (household_id(),),
    ).fetchall()
    # How much the week's recipes use, from the ledger — the row's amount.
    measures = week_measures(conn, [r["id"] for r in rows if r["status"] == "spice" or is_spice(r["item"])])
    items, left_out = [], []
    for r in rows:
        if r["status"] != "spice" and not is_spice(r["item"]):
            continue
        ticked, linked = r["status"] != "spice", bool(r["staple_id"])
        staple = rack.get(_grocery._merge_key(r["item"]))
        if not ticked and staple is not None:
            if _bought_lately(staple, today):
                left_out.append(r["item"])
                continue
            if _due(staple, today) and not linked:
                # Pre-ticked: made the same way a due staple's line is
                # (status 'needed' + staple_id), so the list's own
                # We-have-plenty / Not-this-trip work on it too.
                conn.execute(
                    "UPDATE grocery_items SET status = 'needed', staple_id = ? WHERE id = ? AND household_id = ?",
                    (staple["id"], r["id"], household_id()),
                )
                ticked = linked = True
        # "Probably running low": Pomona's own tick (the line carries the
        # staple) while the rack still says due — an untick's plenty clears it.
        due = bool(ticked and linked and staple is not None and _due(staple, today))
        # The measured total when the recipes gave one ("Ground cumin ·
        # 2½ tsp"), ticked or not — the jar a tick buys is the shop list's
        # line, never this row's. A person's own add keeps its own words.
        week = measures.get(r["id"])
        quantity = amount_text(r["item"], week["tsp"], week["estimated"], week["little_only"]) if week else (r["quantity"] or "")
        items.append({
            "id": r["id"], "item": r["item"], "quantity": quantity,
            "category": r["category"], "store": r["store"] or "",
            "ticked": ticked, "due": due,
        })
    conn.commit()
    conn.close()
    return {"items": items, "recently_bought": left_out}


def tick_spice(item_id: int, ticked: bool = True) -> dict:
    """
    Tick: the spice becomes an ordinary 'needed' line — on the list, in
    its store, counted. Untick: back to the section (status 'spice'). A
    line already bought or in a cart is left alone; so is a line that is
    not a spice at all.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, status, staple_id FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if ticked and row["status"] == "spice":
        conn.execute(
            "UPDATE grocery_items SET status = 'needed' WHERE id = ? AND household_id = ?",
            (item_id, household_id()),
        )
        # Ticking a jar back after unticking Pomona's pre-tick is "actually,
        # I do need it": the plenty is taken back and the staple is due
        # again — the pre-shop screen's own undo (staples.reverse_last_answer).
        if row["staple_id"]:
            _staples.reverse_last_answer(conn, row["staple_id"])
    elif not ticked and row["status"] == "needed" and is_spice(row["item"]):
        conn.execute(
            "UPDATE grocery_items SET status = 'spice' WHERE id = ? AND household_id = ?",
            (item_id, household_id()),
        )
        # Unticking a pre-ticked jar is "we have plenty": the staple's next
        # due date moves a whole cadence out, so the card won't tick it
        # again next week. The row stays this week's recipe's spice,
        # unticked, still linked for a re-tick.
        if row["staple_id"]:
            _staples.note_line_removed(conn, row, "plenty")
    else:
        conn.close()
        return {"item_id": item_id, "item": row["item"], "ticked": row["status"] != "spice", "unchanged": True}
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "ticked": ticked}


# ---------- how much of each spice the week needs (Loop Board "Spices in
# measures", 2026-10-19) ----------
#
# "Spices this week" used to total a spice the way the aisles total a box
# of pasta: the generation prompt wrote spices as the package they come in,
# so seven snacks that each salt with "a pinch" — or name "1 box" of flaky
# salt — read "Flaky salt · 7 boxes". The row now says how much the week's
# recipes USE, in teaspoons and tablespoons ("Ground cumin · 2½ tsp"), and
# the jar is a separate answer, asked only once the spice is ticked onto
# the list (store_quantity below).
#
# The arithmetic stays the grocery list's: a recipe's spice is converted to
# TEASPOONS at ingest (spice_measure, called by
# recipes._add_recipe_ingredients_for_entries) and then goes down the
# ordinary per-portion path — scaled to the eaters and the batch, summed
# across the week, rounded ONCE on the line, with each meal's UNROUNDED
# share in the ledger. The one thing the ledger cannot say from a number
# is whether that number was a guess, so a converted pinch or "to taste"
# writes its row with `estimated` set (1 a guess, 2 "a little"), and the
# row reads "about".

# Teaspoons in one of each volume unit. Weights (g, oz) are left alone:
# a spice by weight has no honest teaspoon reading without its density.
_TSP_IN = {"tsp": 1.0, "tbsp": 3.0, "cup": 48.0, "ml": 1 / 4.92892, "l": 1000 / 4.92892}

# The decided table (Emily, on the card): a dash is ⅛ tsp, a pinch 1/16,
# a smidgen 1/32. Each one is an estimate however exactly it is written.
_TINY_MEASURES = {
    "pinch": 1 / 16, "pinches": 1 / 16,
    "dash": 1 / 8, "dashes": 1 / 8,
    "smidgen": 1 / 32, "smidgens": 1 / 32, "smidgeon": 1 / 32, "smidgeons": 1 / 32,
}

# "To taste": a quarter teaspoon of salt per serving the recipe makes, an
# eighth of pepper (the card's numbers). Anything else written to taste,
# blank, or as a package is "a little", counted as a quarter teaspoon.
_SALT_TSP_PER_SERVING = 0.25
_PEPPER_TSP_PER_SERVING = 0.125
A_LITTLE_TSP = 0.25

# A package is not an amount: "1 jar" of paprika says what to buy, never
# how much goes in. Wider than quantities._PACKAGE_UNITS on purpose — that
# set answers "does one last a week", this one "is this a container".
_PACKAGE_WORDS = {
    "jar", "box", "container", "bottle", "bag", "tub", "pack", "packet",
    "sachet", "can", "tin", "shaker", "carton",
}

# Measure kinds a contribution can be. "little" is the package / unknown
# reading: it counts as A_LITTLE_TSP but a row made only of these says
# "a little" rather than inventing a number.
MEASURED, ESTIMATE, LITTLE = "measured", "estimate", "little"
# How each kind is written in meal_plan_grocery_links.estimated, the one
# thing a ledger row's number cannot say about itself.
ESTIMATE_FLAG = {MEASURED: 0, ESTIMATE: 1, LITTLE: 2}
_FLAG_KIND = {flag: kind for kind, flag in ESTIMATE_FLAG.items()}


def is_oil(name: str) -> bool:
    """An oil (or a spray of one) — measured in tbsp and cups, bought by the bottle."""
    words = (name or "").lower().replace(",", " ").split()
    return "oil" in words or "spray" in words


def _to_tsp(text: str) -> float | None:
    """A volume measure in teaspoons ("1 1/2 tbsp" -> 4.5), or None."""
    found = _leading_tsp(text)
    return found[0] if found else None


def _leading_tsp(text: str) -> tuple[float, str] | None:
    """
    (teaspoons, what follows) for a quantity that STARTS with a volume
    measure — "1/2 tsp freshly ground", "2 tsp plus more to taste" — or
    None. Recipes hang words after the amount that the quantity parser
    reads as part of the unit; the shortest run of words that parses as a
    volume is the amount, the rest a note (review, 2026-10-19).
    """
    from . import recipes as _recipes  # lazy: recipes imports this module
    core, _note = _quantities_split(text)
    words = core.replace(",", " , ").split()
    for end in range(1, min(len(words), 4) + 1):
        parsed = _recipes._parse_cook_qty(" ".join(words[:end]))
        if not parsed or parsed[0] is None or not parsed[1]:
            continue
        unit = parsed[1].split(" ")[0]
        if unit in _TSP_IN:
            return parsed[0] * _TSP_IN[unit], " ".join(words[end:]).strip(" ,")
    return None


def _quantities_split(text: str) -> tuple[str, str]:
    from . import quantities as _quantities
    return _quantities._split_quantity_note((text or "").strip())


def _tiny_tsp(text: str) -> float | None:
    """'a pinch', 'pinch', '2 pinches', 'a dash of', 'smidgen' -> teaspoons, or None."""
    words = (text or "").strip().lower().replace(",", " ").split()
    if not words:
        return None
    count = 1.0
    if words[0] in ("a", "an", "one"):
        words = words[1:]
    elif len(words) > 1:
        from . import recipes as _recipes  # lazy: recipes imports this module
        parsed = _recipes._parse_cook_qty(" ".join(words))
        if not parsed or parsed[0] is None or not parsed[1]:
            return None
        count, words = parsed[0], parsed[1].split(" ")
    if not words or words[0] not in _TINY_MEASURES:
        return None
    return count * _TINY_MEASURES[words[0]]


def _is_package(text: str) -> bool:
    from . import recipes as _recipes  # lazy: recipes imports this module
    core, _note = _quantities_split(text)
    parsed = _recipes._parse_cook_qty(core) if core else None
    if not parsed or not parsed[1]:
        return False
    word = parsed[1].split(" ")[0].lower()
    return word in _PACKAGE_WORDS or word.rstrip("s") in _PACKAGE_WORDS or word[:-2] in _PACKAGE_WORDS


def spice_measure(item: str, qty: str, servings: int | None = None, cook_qty: str = "") -> tuple[float, str] | None:
    """
    (teaspoons, kind) for one recipe line of a spice, at the recipe's own
    servings, or None when the line is something else and is left exactly
    as it was: a count ("2 bay leaves", "1 cinnamon stick") or a weight.

    In order:
      - a volume measure is MEASURED and simply converted ("1 tbsp" = 3);
      - a pinch / dash / smidgen is an ESTIMATE, by the table above;
      - a recipe's own stored cook_qty, when the shopping qty is a package
        or nothing: that is the measured amount the recipe-fill saved and
        the Cook screen already shows, so the row agrees with the cook
        rather than guessing — not a backfill, the recipe said it;
      - "to taste", blank, "season with": salt and pepper per serving
        (an ESTIMATE); any other spice "a little";
      - a package unit ("1 jar", "1 box") is LITTLE — counted as ¼ tsp,
        never as a box — until the recipe is regenerated with a measure.
    """
    text = (qty or "").strip()
    lead = _leading_tsp(text)
    if lead is not None:
        # "2 tsp plus more to taste" is two teaspoons and a bit: about.
        return lead[0], ESTIMATE if "taste" in lead[1].lower() else MEASURED
    tiny = _tiny_tsp(text)
    if tiny is not None:
        return tiny, ESTIMATE
    package = _is_package(text)
    if package or not _has_number(text):
        stored = _to_tsp(cook_qty or "")
        if stored is not None:
            return stored, MEASURED
        stored_tiny = _tiny_tsp(cook_qty or "")
        if stored_tiny is not None:
            return stored_tiny, ESTIMATE
    if package:
        return A_LITTLE_TSP, LITTLE
    if _has_number(text):
        return None  # a count or a weight: not ours to convert
    per_serving = _per_serving_to_taste(item)
    if per_serving and servings:
        return per_serving * servings, ESTIMATE
    return A_LITTLE_TSP, LITTLE


def _has_number(text: str) -> bool:
    return any(ch.isdigit() for ch in text or "") or any(ch in "¼½¾⅓⅔⅛⅜⅝⅞" for ch in text or "")


def _per_serving_to_taste(item: str) -> float:
    words = set((item or "").lower().replace(",", " ").split())
    salt, pepper = "salt" in words, bool(words & {"pepper", "peppercorn", "peppercorns"})
    return (_SALT_TSP_PER_SERVING if salt else 0.0) + (_PEPPER_TSP_PER_SERVING if pepper else 0.0)


def _ledger_measure(item: str, quantity: str, estimated: int) -> tuple[float, str] | None:
    """
    One ledger row read as (teaspoons, kind). A row written since this
    change is already teaspoons (or a tsp-family unit); `estimated` says
    whether it was a guess. A row written BEFORE it carries the recipe's
    own words — "1 box", "a pinch", "to taste" — and is read through the
    same table, servings unknown, so last week's list stops saying boxes
    without anything being rewritten.
    """
    tsp = _to_tsp(quantity)
    if tsp is not None:
        return tsp, _FLAG_KIND.get(estimated or 0, ESTIMATE)
    return spice_measure(item, quantity)


def week_measures(conn, item_ids) -> dict[int, dict]:
    """
    grocery item id -> {"tsp", "estimated", "little_only"} summed over the
    per-meal ledger (meal_plan_grocery_links) — the UNROUNDED shares, so a
    week of pinches is seven sixteenths and not seven quarters. A line
    with no ledger rows (a person's own add) or with a row that is not a
    volume at all (a count of bay leaves) is left out: its own quantity
    is the honest answer there.
    """
    ids = [int(i) for i in item_ids]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT grocery_item_id, item, quantity, estimated FROM meal_plan_grocery_links "
        f"WHERE household_id = ? AND grocery_item_id IN ({marks})",
        (household_id(), *ids),
    ).fetchall()
    out: dict[int, dict] = {}
    unreadable: set[int] = set()
    for r in rows:
        gid = r["grocery_item_id"]
        measure = _ledger_measure(r["item"], r["quantity"] or "", r["estimated"])
        if measure is None:
            unreadable.add(gid)
            continue
        tsp, kind = measure
        total = out.setdefault(gid, {"tsp": 0.0, "estimated": False, "little_only": True})
        total["tsp"] += tsp
        total["estimated"] = total["estimated"] or kind != MEASURED
        total["little_only"] = total["little_only"] and kind == LITTLE
    for gid in unreadable:
        out.pop(gid, None)
    return out


def _round_half_up(x: float) -> int:
    """Half rounds UP, as the shop line's rounding does — Python's round()
    sends 2.5 to 2, which made a row disagree with its own line."""
    return int(math.floor(x + 0.5 + 1e-9))


_EIGHTHS = {0: "", 1: "⅛", 2: "¼", 3: "⅜", 4: "½", 5: "⅝", 6: "¾", 7: "⅞"}
_QUARTERS = {0: "", 1: "¼", 2: "½", 3: "¾"}


def _mixed(whole: int, frac: str) -> str:
    return f"{whole}{frac}" if whole and frac else (frac or str(whole))


def amount_text(item: str, tsp: float, estimated: bool = False, little_only: bool = False) -> str:
    """
    The Spices row's amount: "2½ tsp", "about 1½ tbsp", "⅔ cup".
    Teaspoons to the nearest eighth (a pinch never rounds away to
    nothing), tablespoons from 1 tbsp up in quarters — the shop line's own
    step, so the two agree — and an oil past 4 tbsp
    in measuring-cup steps (quarters and thirds, the cups in the drawer).
    "about" whenever any contribution was an estimate.
    """
    if little_only:
        return "a little"
    about = "about " if estimated else ""
    if is_oil(item) and tsp > 12 + 1e-9:
        cups = tsp / 48
        steps = [(n / d, label) for n, d, label in (
            (1, 4, "¼"), (1, 3, "⅓"), (1, 2, "½"), (2, 3, "⅔"), (3, 4, "¾"),
        )]
        whole = int(cups)
        rest = cups - whole
        best = min([(0.0, "")] + steps + [(1.0, "")], key=lambda s: abs(s[0] - rest))
        if best[0] == 1.0:
            whole, best = whole + 1, (0.0, "")
        return f"{about}{_mixed(whole, best[1])} cup" if whole or best[1] else f"{about}¼ cup"
    if tsp + 1e-9 >= 3:
        quarters = _round_half_up(tsp / 3 * 4)
        return f"{about}{_mixed(quarters // 4, _QUARTERS[quarters % 4])} tbsp"
    eighths = max(1, _round_half_up(tsp * 8))
    if eighths >= 24:
        return f"{about}1 tbsp"
    return f"{about}{_mixed(eighths // 8, _EIGHTHS[eighths % 8])} tsp"


# What a typical jar of ground spice holds, and a bottle of oil: past it a
# week genuinely needs a second one. A guess, said here so it can be argued
# with — ½ cup is the card's number for a jar; 2 cups (about 500 ml) is
# this builder's for a bottle.
_JAR_HOLDS_TSP = 24.0
_BOTTLE_HOLDS_TSP = 96.0


def store_quantity(item: str, quantity: str, plan_line: bool = False) -> str | None:
    """
    What a TICKED spice line says on the shop list: one store unit ("1
    jar", "1 bottle"), two only past what one holds — never the measured
    total, which is a cooking amount and stays on the Spices row.

    A volume becomes the jar on any line, a person's typed "2 tbsp" salt
    included: it is bought by the jar either way. A PLAN's line that is not
    a volume becomes one jar too — a line already on a list at deploy
    still says "7 boxes" from the old package wording, and that was never
    seven of anything (review, 2026-10-19) — unless it already counts the
    store unit itself ("2 bottles", an old recipe that really asked for
    two). Anything else a person wrote (their "2 jars", a count of
    cinnamon sticks) is None: left as written.
    """
    oil = is_oil(item)
    word = "bottle" if oil else "jar"
    tsp = _to_tsp(quantity or "")
    if tsp is None:
        if not plan_line or re.fullmatch(rf"\s*\d+\s+{word}s?\s*", quantity or "", flags=re.I):
            return None
        return f"1 {word}"
    holds = _BOTTLE_HOLDS_TSP if oil else _JAR_HOLDS_TSP
    return f"2 {word}s" if tsp > holds + 1e-9 else f"1 {word}"


def restate_plan_line(item_id: int, conn=None) -> None:
    """
    Re-read a PLAN's spice line in teaspoons, ledger and all — run by the
    ingest when a measured spice share has just merged onto a line.

    A line already on a list when this change shipped was written in the
    recipes' old package words: "7 boxes" of flaky salt, a ledger of seven
    "1 box" rows. A meal swapped in afterwards adds "0.25 tsp" to it, and
    the list's own merge can only glue the two ("6 boxes + 0.25 tsp",
    review 2026-10-19). So every ledger row of the line is rewritten in
    the reading week_measures already gives it (a box is "a little", a
    pinch 1/16 tsp — the same table, nothing new guessed) and the line is
    rounded once from the total, as the ingest would have written it.

    Left alone: a person's own line (it has no ledger to restate), and a
    line with a row that is not a volume at all (a count of bay leaves).
    """
    from . import quantities as _quantities
    if conn is None:
        with write() as own_conn:
            restate_plan_line(item_id, conn=own_conn)
        return
    line = conn.execute(
        "SELECT id, item, source_weekly_plan_id, status FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    rows = conn.execute(
        "SELECT id, item, quantity, estimated FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ?",
        (household_id(), item_id),
    ).fetchall() if line else []
    readings = [(r, _ledger_measure(r["item"], r["quantity"] or "", r["estimated"])) for r in rows]
    if (
        line and line["source_weekly_plan_id"] is not None and line["status"] in ("needed", "spice")
        and readings and all(m is not None for _r, m in readings)
    ):
        from . import recipes as _recipes  # lazy: recipes imports this module
        for r, (tsp, kind) in readings:
            conn.execute(
                "UPDATE meal_plan_grocery_links SET quantity = ?, estimated = ? WHERE id = ? AND household_id = ?",
                (_recipes._ledger_share(tsp, "tsp", "tsp"), ESTIMATE_FLAG[kind], r["id"], household_id()),
            )
        total = sum(m[0] for _r, m in readings)
        conn.execute(
            "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
            (_quantities._format_quantity(*_quantities._shopping_round(total, "tsp")), item_id, household_id()),
        )
