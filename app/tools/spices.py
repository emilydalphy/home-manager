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

from datetime import date, timedelta

from ..db import get_conn
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
                    "UPDATE grocery_items SET status = 'needed', staple_id = ? WHERE id = ?", (staple["id"], r["id"])
                )
                ticked = linked = True
        # "Probably running low": Pomona's own tick (the line carries the
        # staple) while the rack still says due — an untick's plenty clears it.
        due = bool(ticked and linked and staple is not None and _due(staple, today))
        items.append({
            "id": r["id"], "item": r["item"], "quantity": r["quantity"] or "",
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
        conn.execute("UPDATE grocery_items SET status = 'needed' WHERE id = ?", (item_id,))
        # Ticking a jar back after unticking Pomona's pre-tick is "actually,
        # I do need it": the plenty is taken back and the staple is due
        # again — the pre-shop screen's own undo (staples.reverse_last_answer).
        if row["staple_id"]:
            _staples.reverse_last_answer(conn, row["staple_id"])
    elif not ticked and row["status"] == "needed" and is_spice(row["item"]):
        conn.execute("UPDATE grocery_items SET status = 'spice' WHERE id = ?", (item_id,))
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
