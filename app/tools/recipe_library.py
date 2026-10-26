"""
The Shared Recipe Library, step 1 (Loop Board, Emily 2026-10-18).

Research was 72% of a fresh household's AI spend, and every household paid
for "Seared Salmon" again: dish_research and recipes are both per
household. The library is one shared copy of each dish Pomona researched
or wrote, keyed by recipe_research.dish_key, with no household in it.

Emily's decisions, the later one winning:
  * No opt-in. Every recipe Pomona researched, wrote or helped tweak goes
    in. A tweak is its own entry, a variant of the base dish. Step 1 (review,
    2026-10-19) files only research and recipes written for the DISH — a
    recipe written around one household (its must-avoids, kit, children,
    eating style) or rewritten from its own words files nothing; variants
    are the next step.
  * Nothing personal ever goes in — only the dish: name, ingredients,
    steps, servings, times, cuisine, protein, method notes and the credited
    sources. A recipe the household typed in or imported from a link never
    goes in (only research and the recipe pass call file_entry), nor does a
    dish named for someone ("Nana's Lasagne") or asked for by name.
  * Sources live here, lead first, with ratings and link status, and are
    shown exactly as researched — copied, never looked up again.
  * A table in the existing database, so backups, deploys and the
    isolation tests cover it.
  * ONE write path — file_entry — for live research, a household's tweak
    and (later) the 500-dish seed.

Copy on use: a household planning a library dish gets its OWN recipes row
filled from the entry (recipes.library_id says which). Its servings,
tweaks, ratings, times cooked and exclusions live on that row and never
flow back; the allergen gate runs on the entry's ingredients for THIS
household before anything is copied (agent._write_one_pending_recipe).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading

from ..db import get_conn, write
from . import recipes as _recipes
from ._shared import household_display_name, household_id
from .recipe_research import dish_key

logger = logging.getLogger("home_manager")

ORIGINS = ("research", "written", "tweak", "seed")

# The ingredient keys an entry keeps. The writer's own tool asks for these
# and nothing else (agent._RECIPE_DETAILS_TOOL); cook_qty is the settled
# cooking amount. A key outside this list is dropped, never carried.
_INGREDIENT_KEYS = ("item", "qty", "category", "cook_qty")

# How long a household waits for another household's research of the SAME
# dish before giving up on it (its own research step has a deadline too).
_WAIT_FOR_OTHER_RESEARCH_SECONDS = 60.0


def library_enabled() -> bool:
    """RECIPE_LIBRARY=off stops both reading and filing — every dish is
    then researched and written per household, as before this existed."""
    return os.environ.get("RECIPE_LIBRARY", "on").strip().lower() not in ("off", "0", "false", "no")


# ---------- nothing personal ----------

# Words in a household's name that name nobody.
_HOUSEHOLD_NAME_STOP = frozenset({
    "the", "and", "our", "household", "house", "home", "family", "families", "kitchen", "crew", "team",
})

# A dish named for a person in the family ("Nana's Lasagne", "Mum's dal")
# is that family's dish, not the library's. Matched as whole words in the
# dish's name, across the words families actually use.
_FAMILY_WORDS = frozenset({
    "nana", "nanna", "nanny", "grandma", "grandmas", "granny", "gran", "grandpa", "grandad",
    "granddad", "grandmother", "grandfather", "mum", "mums", "mom", "moms", "mama", "mommy",
    "mummy", "mother", "dad", "dads", "daddy", "father", "nonna", "nonno", "abuela", "abuelo",
    "abuelita", "oma", "opa", "ammamma", "ammumma", "ammachi", "amma", "appa", "achan", "paati",
    "patti", "thatha", "dadi", "nani", "auntie", "aunty", "aunt", "uncle", "bubbe", "bubby",
    "yiayia", "lola", "lolo", "tita", "tito", "sister", "brother", "cousin", "wife", "husband",
    "family", "familys",
})

# The possessive dish names everyone knows, which name no one in a family.
_SHARED_POSSESSIVES = frozenset({
    "shepherd", "hunter", "tso", "devil", "chef", "ploughman", "plowman", "fisherman",
    "farmer", "baker", "angel", "bird", "pig", "cook", "butcher", "miner", "sailor", "beggar",
    "monkey", "lion", "king", "queen", "cottager", "workingman", "buddha",
})
_POSSESSIVE = re.compile(r"\b([a-z]+)['\u2019]s\b")
_CAPITALISED = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")


def _words_of(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower()).split() if len(w) >= 3}


def _household_words() -> list[str]:
    """
    Every word the filing household could be named or recognised by: each
    member's name split into its words (3+ letters), the household's own
    name, its pets, and the capitalised words of its hard facts ("Asha is
    allergic to sesame") and its hosting guests' notes ("Sam's vegetarian").
    The writer is told names (must_not_contain is "Asha: peanuts"), so one
    could reach a step — "leave the chilli off Asha's plate" — and an entry
    carrying any of these is not filed. Over-broad on purpose: a member
    called Ginger costs that household's ginger dishes their filing, never
    anyone's privacy.
    """
    hid = household_id()
    words: set[str] = set()
    conn = get_conn()
    try:
        for r in conn.execute("SELECT name FROM members WHERE household_id = ?", (hid,)).fetchall():
            words |= _words_of(r["name"])
        for r in conn.execute("SELECT name FROM pets WHERE household_id = ?", (hid,)).fetchall():
            words |= _words_of(r["name"])
        row = conn.execute("SELECT name FROM households WHERE id = ?", (hid,)).fetchone()
        if row:
            # Through household_display_name: every email sign-up is called
            # "Your household", and "your" would keep "Build-Your-Own Tacos"
            # out of the shared library for all of them (2026-10-21).
            words |= _words_of(household_display_name(row["name"])) - _HOUSEHOLD_NAME_STOP
        typed = [r["text"] for r in conn.execute(
            "SELECT text FROM facts WHERE household_id = ? AND hard = 1", (hid,)).fetchall()]
        typed += [r["guest_notes"] for r in conn.execute(
            "SELECT guest_notes FROM holiday_answers WHERE household_id = ? AND guest_notes != ''",
            (hid,)).fetchall()]
    finally:
        conn.close()
    for text in typed:
        words |= {w.lower() for w in _CAPITALISED.findall(str(text or ""))}
    return sorted(words)


def _names_someone(texts: list[str], words: list[str]) -> bool:
    blob = "\n".join(str(t or "") for t in texts).lower()
    return any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", blob) for w in words)


def _typed_asks() -> str:
    """Everything this household typed about what to plan: each week's
    freeform answer and constraints, and the standing next-week notes."""
    hid = household_id()
    conn = get_conn()
    try:
        parts = [r["freeform"] for r in conn.execute(
            "SELECT freeform FROM week_intake WHERE household_id = ? AND freeform != ''", (hid,)).fetchall()]
        parts += [r["constraints_notes"] for r in conn.execute(
            "SELECT constraints_notes FROM weekly_plans WHERE household_id = ? AND constraints_notes != ''",
            (hid,)).fetchall()]
        row = conn.execute("SELECT next_week_notes FROM meal_preferences WHERE household_id = ?",
                           (hid,)).fetchone()
        if row and row["next_week_notes"]:
            parts.append(row["next_week_notes"])
    finally:
        conn.close()
    return " ".join(parts)


def private_dish_name(dish_name: str, words: list[str] | None = None, *, household: bool = True) -> bool:
    """
    Whether a dish's NAME is the household's own rather than a dish: it
    names someone in it, a family word ("Nana's", "Mum's dal"), a
    possessive that isn't one of the dishes everyone knows ("Shepherd's
    Pie" is fine, "Kevin's Chili" is not), or it is a dish they asked for
    by name in their own words — whatever they typed stays theirs.
    `household=False` (the seed, filed in no household's name) checks the
    name alone, never a household's people or asks.
    """
    from . import typed_requests as _typed_requests
    name = str(dish_name or "")
    lower = name.lower().replace("\u2019", "'")
    if household and _names_someone([name], _household_words() if words is None else words):
        return True
    if _words_of(lower.replace("'", "")) & _FAMILY_WORDS or set(re.findall(r"[a-z]+", lower)) & _FAMILY_WORDS:
        return True
    if any(m not in _SHARED_POSSESSIVES for m in _POSSESSIVE.findall(lower)):
        return True
    asks = _typed_requests._norm(_typed_asks()) if household else ""
    if asks:
        head = re.split(r"\s+with\s+", lower, maxsplit=1)[0]
        for phrase in {lower, head}:
            norm = _typed_requests._norm(phrase)
            if norm and any(True for _ in _typed_requests._typed(norm, asks)):
                return True
    return False


# What the recipe writer is told about ONE household. A recipe written
# with any of these in its spec was written for that table, not for the
# dish, and is never filed (Shared Recipe Library review, 2026-10-19:
# the first household to write a dish around a peanut allergy must not
# file a peanut-free base for everyone).
_HOUSEHOLD_SPEC_KEYS = ("must_not_contain", "children_at_table", "eating_style", "kitchen_kit",
                        "must_use", "current_inventory", "change_requested")


def written_for_a_household(spec: dict) -> bool:
    """True when the writer's spec carried anything about the household —
    the keys above, or a name in the planner's dish_note."""
    if any(spec.get(k) for k in _HOUSEHOLD_SPEC_KEYS):
        return True
    return _names_someone([spec.get("dish_note") or ""], _household_words())


def _clean_ingredients(ingredients) -> list[dict]:
    out = []
    for ing in ingredients or []:
        if not isinstance(ing, dict) or not str(ing.get("item") or "").strip():
            continue
        out.append({k: str(ing[k]) for k in _INGREDIENT_KEYS if ing.get(k) not in (None, "")})
    return out


def _int_or_none(value):
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


# ---------- the one write path ----------

def file_entry(dish_name: str, *, ingredients=None, instructions=None, default_servings=None,
               prep_time_minutes=None, cook_time_minutes=None, cuisine: str = "",
               main_protein: str = "", advance_prep_notes: str = "",
               advance_prep_step_indices=None, sources=None, method_notes: str = "",
               fallback_used: bool = False, origin: str = "research",
               variant: bool = False) -> int | None:
    """
    File a dish in the library; returns the entry's id, or None when
    nothing was filed. Every column is built from the named dish fields —
    there is no way to pass a household's notes, ratings or changes in.

    A base (variant=False) is one per dish: with none yet, a new row; with
    one already there, its GAPS are filled — sources onto a base written
    without research, a recipe onto a base filed from research alone — and
    nothing it already holds is overwritten, so the copies made from it
    never disagree with it. A variant is always a new row, pointing at the
    base when there is one.

    Called inside a household's request, never with one bound for it: the
    only household read is the filing household's member names, and an
    entry that names one of them is not filed (logged, not raised — the
    household's own recipe is saved either way).
    """
    if not library_enabled():
        return None
    if origin not in ORIGINS:
        raise ValueError(f"unknown library origin {origin!r}")
    key = dish_key(dish_name)
    if not key:
        return None
    ingredients = _clean_ingredients(ingredients)
    instructions = [str(s) for s in (instructions or []) if str(s or "").strip()]
    has_body = bool(ingredients and instructions)
    sources = [s for s in (sources or []) if isinstance(s, dict) and str(s.get("url") or "").strip()]
    if not has_body and not sources:
        return None
    method_notes = str(method_notes or "").strip()
    advance_prep_notes = str(advance_prep_notes or "").strip()
    # The seed (its own card) is filed in no household's name: nothing of a
    # household to check it against, and household 1 must not be read for it.
    from_household = origin != "seed"
    words = _household_words() if from_household else []
    if has_body and _names_someone(
        [dish_name, advance_prep_notes, json.dumps(ingredients)] + instructions, words,
    ):
        logger.warning("Not filing %r in the recipe library: it names someone in the household", dish_name)
        ingredients, instructions, has_body = [], [], False
        if not sources:
            return None
    if private_dish_name(dish_name, words, household=from_household):
        return None
    if _names_someone([method_notes], words):
        method_notes = ""
    body = (
        json.dumps(ingredients), json.dumps(instructions), _recipes.positive_servings(default_servings),
        _int_or_none(prep_time_minutes), _int_or_none(cook_time_minutes),
        str(cuisine or "").strip(), str(main_protein or "").strip(), advance_prep_notes,
        json.dumps([i for i in (advance_prep_step_indices or []) if isinstance(i, int)]),
    )
    with write() as conn:
        conn.execute("BEGIN IMMEDIATE")
        base = conn.execute(
            "SELECT id, ingredients_json, instructions_json, method_notes, "
            "(SELECT COUNT(*) FROM recipe_library_sources s WHERE s.library_id = l.id) AS n "
            "FROM recipe_library l WHERE dish_key = ? AND kind = 'base' ORDER BY id LIMIT 1",
            (key,),
        ).fetchone()
        if variant or not base:
            entry_id = conn.execute(
                "INSERT INTO recipe_library (dish_key, dish_name, kind, variant_of, origin, "
                "ingredients_json, instructions_json, default_servings, prep_time_minutes, "
                "cook_time_minutes, cuisine, main_protein, advance_prep_notes, "
                "advance_prep_step_indices_json, method_notes, fallback_used) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, str(dish_name or "").strip(), "variant" if variant else "base",
                 base["id"] if (variant and base) else None, origin)
                + (body if has_body else ("[]", "[]", None, None, None, body[5], body[6], "", "[]"))
                + (method_notes, 1 if fallback_used else 0),
            ).lastrowid
            if sources:
                _insert_sources(conn, entry_id, sources)
            return entry_id
        entry_id = base["id"]
        base_has_body = json.loads(base["ingredients_json"] or "[]") and json.loads(base["instructions_json"] or "[]")
        if has_body and not base_has_body:
            conn.execute(
                "UPDATE recipe_library SET ingredients_json = ?, instructions_json = ?, "
                "default_servings = ?, prep_time_minutes = ?, cook_time_minutes = ?, "
                "cuisine = CASE WHEN cuisine = '' THEN ? ELSE cuisine END, "
                "main_protein = CASE WHEN main_protein = '' THEN ? ELSE main_protein END, "
                "advance_prep_notes = ?, advance_prep_step_indices_json = ? WHERE id = ?",
                body + (entry_id,),
            )
        if sources and not base["n"]:
            _insert_sources(conn, entry_id, sources)
            conn.execute(
                "UPDATE recipe_library SET method_notes = CASE WHEN method_notes = '' THEN ? "
                "ELSE method_notes END, fallback_used = ? WHERE id = ?",
                (method_notes, 1 if fallback_used else 0, entry_id),
            )
        return entry_id


def _insert_sources(conn, entry_id: int, sources: list[dict]) -> None:
    for pos, s in enumerate(sources, start=1):
        conn.execute(
            "INSERT INTO recipe_library_sources (library_id, position, name, title, url, rating, "
            "rating_count, is_lead, link_ok, link_checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, pos, str(s.get("name") or ""), str(s.get("title") or ""), str(s["url"]),
             s.get("rating"), s.get("rating_count"), 1 if s.get("lead") else 0,
             0 if s.get("link_ok") is False else 1, s.get("link_checked_at")),
        )


def file_from_recipe(recipe_id: int, *, research: dict | None = None, origin: str = "written",
                     spec: dict | None = None) -> int | None:
    """
    File this household's just-written recipe row as the dish's BASE, read
    back from the database so the entry is exactly what was saved, and
    point the row at the entry. `research` is the research it was written
    from (its sources and method notes go in with it).

    Only a recipe written for the DISH is filed: `spec`, the writer's own
    input, must carry nothing about the household (written_for_a_household)
    and the writer must have changed nothing for them ("Changed for your
    household" lines). Anything else files nothing — the research was
    filed when it landed, which is most of the cost, and a version written
    around one table's peanut allergy or air fryer is not everyone's base.
    Typed and imported recipes are never filed.
    """
    if not library_enabled() or not recipe_id:
        return None
    if spec is None or written_for_a_household(spec):
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT name, ingredients_json, instructions_json, default_servings, prep_time_minutes, "
            "cook_time_minutes, cuisine, main_protein, advance_prep_notes, "
            "advance_prep_step_indices_json, household_changes_json, source_url, source_book "
            "FROM recipes WHERE id = ? AND household_id = ?",
            (int(recipe_id), household_id()),
        ).fetchone()
    finally:
        conn.close()
    if not row or (row["source_url"] or "").strip() or (row["source_book"] or "").strip():
        return None
    try:
        if json.loads(row["household_changes_json"] or "[]"):
            return None
    except (TypeError, ValueError):
        return None
    sources = (research or {}).get("sources") or []
    entry_id = file_entry(
        row["name"],
        ingredients=json.loads(row["ingredients_json"] or "[]"),
        instructions=json.loads(row["instructions_json"] or "[]"),
        default_servings=row["default_servings"],
        prep_time_minutes=row["prep_time_minutes"], cook_time_minutes=row["cook_time_minutes"],
        cuisine=row["cuisine"], main_protein=row["main_protein"],
        advance_prep_notes=row["advance_prep_notes"],
        advance_prep_step_indices=json.loads(row["advance_prep_step_indices_json"] or "[]"),
        sources=sources, method_notes=(research or {}).get("method_notes") or "",
        fallback_used=bool((research or {}).get("fallback_used")),
        origin=origin,
    )
    if entry_id:
        _link(recipe_id, entry_id)
    return entry_id


def _link(recipe_id: int, entry_id: int) -> None:
    with write() as conn:
        conn.execute(
            "UPDATE recipes SET library_id = ? WHERE id = ? AND household_id = ? AND library_id IS NULL",
            (int(entry_id), int(recipe_id), household_id()),
        )


# ---------- reading ----------

def _entry(conn, row) -> dict:
    sources = conn.execute(
        "SELECT name, title, url, rating, rating_count, is_lead, link_ok, link_checked_at "
        "FROM recipe_library_sources WHERE library_id = ? ORDER BY position",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "dish_key": row["dish_key"],
        "dish_name": row["dish_name"],
        "kind": row["kind"],
        "origin": row["origin"],
        "ingredients": json.loads(row["ingredients_json"] or "[]"),
        "instructions": json.loads(row["instructions_json"] or "[]"),
        "default_servings": row["default_servings"],
        "prep_time_minutes": row["prep_time_minutes"],
        "cook_time_minutes": row["cook_time_minutes"],
        "cuisine": row["cuisine"],
        "main_protein": row["main_protein"],
        "advance_prep_notes": row["advance_prep_notes"],
        "advance_prep_step_indices": json.loads(row["advance_prep_step_indices_json"] or "[]"),
        "method_notes": row["method_notes"],
        "fallback_used": bool(row["fallback_used"]),
        "sources": [
            {"name": s["name"], "title": s["title"], "url": s["url"], "rating": s["rating"],
             "rating_count": s["rating_count"], "lead": bool(s["is_lead"]),
             "link_ok": bool(s["link_ok"]), "link_checked_at": s["link_checked_at"]}
            for s in sources
        ],
    }


def base_recipe(name: str) -> dict | None:
    """The library's recipe for this dish — the base, written up in full —
    or None. What a household's recipe pass copies instead of writing."""
    if not library_enabled():
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM recipe_library WHERE dish_key = ? AND kind = 'base' "
            "AND ingredients_json NOT IN ('', '[]') AND instructions_json NOT IN ('', '[]') "
            "ORDER BY id LIMIT 1",
            (dish_key(name),),
        ).fetchone()
        return _entry(conn, row) if row else None
    finally:
        conn.close()


def research_for(name: str) -> dict | None:
    """The library's research for this dish — the sources and method notes
    of the base, else of the earliest variant that has some — or None.
    Research is about the dish, not the version, so any entry's will do."""
    if not library_enabled():
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT l.* FROM recipe_library l WHERE l.dish_key = ? AND EXISTS "
            "(SELECT 1 FROM recipe_library_sources s WHERE s.library_id = l.id) "
            "ORDER BY (l.kind = 'base') DESC, l.id LIMIT 1",
            (dish_key(name),),
        ).fetchone()
        return _entry(conn, row) if row else None
    finally:
        conn.close()


# ---------- the morning report's counts ----------

USE_OUTCOMES = ("recipe", "research", "new")


def record_use(name: str, outcome: str) -> None:
    if outcome not in USE_OUTCOMES:
        return
    try:
        with write() as conn:
            conn.execute(
                "INSERT INTO recipe_library_uses (household_id, dish_key, outcome) VALUES (?, ?, ?)",
                (household_id(), dish_key(name), outcome),
            )
    except Exception:
        logger.exception("Recording a recipe library use failed")


def use_counts(conn, hid: int, since: str) -> dict:
    """{"recipe": n, "research": n, "new": n} over the window — library
    hits (whole recipe / research only) against new research billed."""
    rows = conn.execute(
        "SELECT outcome, COUNT(*) AS n FROM recipe_library_uses "
        f"WHERE household_id = ? AND created_at >= datetime('now', '{since}') GROUP BY outcome",
        (hid,),
    ).fetchall()
    out = {k: 0 for k in USE_OUTCOMES}
    out.update({r["outcome"]: r["n"] for r in rows if r["outcome"] in out})
    return out


# ---------- one research per dish, across households ----------
#
# Two households planning the same new dish at the same moment would each
# miss the library and each pay for the research. In memory, like the
# recipe claims in agent.py: the second waits for the first, then reads
# what it filed. A redeploy drops the claims along with the threads.
_RESEARCHING: dict[str, threading.Event] = {}
_RESEARCHING_GUARD = threading.Lock()


def claim_research(name: str) -> threading.Event | None:
    """None when this caller now owns the dish's research (release it with
    release_research); otherwise the Event of the one already running."""
    key = dish_key(name)
    with _RESEARCHING_GUARD:
        held = _RESEARCHING.get(key)
        if held is not None:
            return held
        _RESEARCHING[key] = threading.Event()
        return None


def release_research(name: str) -> None:
    with _RESEARCHING_GUARD:
        held = _RESEARCHING.pop(dish_key(name), None)
    if held is not None:
        held.set()
