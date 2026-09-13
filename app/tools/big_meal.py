"""
The big meal — hosting a holiday dinner (Loop Board "Holidays: Pomona knows
12 October is coming and asks how you're spending it", slice 2).

Slice 1 (holidays.py) asks how the household is spending the day and, for
"Hosting", records the headcount through the intake's own guests tag. This
module is what "Hosting" now MEANS for the plan: a menu rather than a
dish, the shopping in two trips, the make-ahead work spread over the days
before, and a day-of timeline that works back from the time they want it
on the table. Nothing here is Thanksgiving-specific — a holiday is a date
with a name, an answer and a headcount.

WHERE THE MENU LIVES, and why nothing new was invented for it. A dinner in
this app is ONE meal_plan_entries row per (date, slot) — audit_plan_slots
enforces it, the leftover chain resolves on it, the Cook and Plan screens
draw it. So the big meal is one dinner entry:

  - the MAIN is the entry's recipe (a saved recipe, default_servings = the
    table), so the grocery ingest scales it to the headcount attendance
    already carries (attendance.servings_scale_factor), the Cook screen
    shows its steps, and leftovers chain off it like any other night;
  - the SIDES and the SWEET ride in the entry's `sides_json` — plates.py's
    "this dish, on this night" column — each carrying a `role` ("side" |
    "sweet") and the timing the day-of needs (`minutes`, `cook_minutes`,
    `oven`, `ahead_days`). Every reader of sides_json already treats a
    side as part of the entry: approval buys its ingredients under the
    same entry id (so dropping the dinner drops them), the Cook view lists
    its steps "Alongside", the allergy check reads its ingredients.

The menu's own record — which entry it built, the main's timing, whether
the proposal came back whole — is `holiday_answers.menu_json`, beside the
answer it belongs to. The dinner entry also carries `holiday_menu: true`
in derived_from, the way a brought dish carries `holiday_dish`, so the
unwind can tell its own work from a dinner the household planned by hand.

THE PROPOSAL is one model call (agent.generate_big_meal_llm, injected at
call time like plates.complete_plate's side_generator) and it degrades:
no API, a refusal or a malformed answer leaves the night with whatever
main it already had (status "main_only") or as an open question naming
the reason (status "none") — never a crash, never a blank.

THE SHOP SPLIT is derived at read time, not stored: a grocery line linked
(meal_plan_grocery_links) to the menu's entry is "early" if its category
keeps — pantry, frozen, other — and "fresh" otherwise, unless another meal
needs it before the early trip anyway. Read-time means nothing to keep in
sync at approval, swap or reversal, and nothing to unwind: when the entry
goes, its links go, and the split goes with them.

THE PREP SPREAD writes prep_tasks rows with task_type='holiday' — this
module's own producer, deleting only its own rows, exactly the contract
the table's comment sets out for defrost and prep_cut — so each piece
lands on its day on Now and in the Cook screen's prep list through
today_moves, which reads every prep_tasks row for the day regardless of
who wrote it.

THE DAY-OF TIMELINE is data plus a chat readback (get_big_meal). Drawing
it on the Cook tab is deferred until the Cook-D redesign merges — see the
CLAUDE.md entry.

THE RULES THAT HOLD IT TOGETHER (each one a verifier's catch, 2026-09-13):
  - A trip already covering the day wins: nothing is built for a dinner
    nobody is home for (slice 1's rule, now the menu's too).
  - A dinner the household already had in the slot is ADOPTED, not
    replaced, and leaving hosting gives it back as it was — sides off,
    marks off, its own shopping re-bought for the household. Only a main
    the menu itself PROPOSED is handed back as an open question.
  - The menu follows the answer: a new count, time or guest note refreshes
    it in place (rescale, re-check every dish including the main, drop
    what clashes, propose replacements once) rather than rebuilding.
  - Prep rows are written only once the week is approved — approval is
    when a week becomes real — and are read only while their entry exists.
  - A menu whose dinner was moved to another night (swap_dinner_nights) is
    gone, not followed: its prep rows go, the moved dinner is left alone.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta

from ..db import get_conn
from ._shared import household_id
from . import attendance as _attendance
from . import coordination as _coordination
from . import grocery as _grocery
from . import household as _household
from . import meal_plans as _meal_plans
from . import memory as _memory
from . import plates as _plates
from . import quantities as _quantities
from . import recipes as _recipes
from . import rhythm as _rhythm
from . import weekly_plan as _weekly_plan

logger = logging.getLogger(__name__)

# ---------- the shape ----------

ROLES = ("side", "sweet")

# ASSUMPTION — the menu's shape when the household hasn't said otherwise:
# one main, three sides, one sweet. Changed in chat dish by dish.
DEFAULT_SIDE_COUNT = 3

# ASSUMPTION — the two trips. The keeps-well shop three days out (a
# Thursday/Friday for a Monday holiday), the fresh shop the day before.
EARLY_SHOP_DAYS_AHEAD = 3
FRESH_SHOP_DAYS_AHEAD = 1

# ASSUMPTION — which grocery sections keep. The store sections the recipe
# path already classifies every ingredient into (plates._VALID_CATEGORIES);
# "other" (foil, drinks, candles) is treated as keeping.
EARLY_CATEGORIES = frozenset({"pantry", "frozen", "other"})

# The producer mark on prep_tasks rows this module writes; it deletes only
# these. See the table's own comment on task_type.
TASK_TYPE = "holiday"

# How far ahead a dish can be made. Two days is a cranberry sauce or a pie
# crust; further out and it's freezer cooking, which is a different
# feature.
MAX_AHEAD_DAYS = 2

# Warming a made-ahead dish through on the day, when the proposal gave no
# better number.
DEFAULT_REHEAT_MINUTES = 30

# The guests' note is one line in the host's words; the Days screen's box
# holds this many characters and chat is held to the same.
GUEST_NOTES_MAX = 160

# ASSUMPTION — for a grocery line whose section is "other" (a recipe that
# named no section), what reads as perishable and so goes on the fresh
# trip: raw meat, poultry, fish and seafood, fresh herbs, salad greens,
# berries, bread. Anything else in "other" (foil, drinks, candles) keeps.
PERISHABLE_WORDS = frozenset({
    "chicken", "turkey", "duck", "goose", "beef", "steak", "roast", "lamb", "pork", "ham", "veal",
    "sausage", "sausages", "bacon", "mince", "ground",
    "fish", "salmon", "trout", "cod", "halibut", "tuna", "shrimp", "prawns", "prawn", "scallops",
    "mussels", "clams", "oysters", "lobster", "crab",
    "thyme", "rosemary", "sage", "parsley", "cilantro", "coriander", "dill", "basil", "chives", "mint",
    "tarragon", "oregano", "herbs",
    "lettuce", "greens", "arugula", "rocket", "spinach", "kale", "romaine", "salad",
    "berries", "strawberries", "raspberries", "blueberries", "blackberries",
    "bread", "loaf", "loaves", "rolls", "buns", "baguette",
    "milk", "cream", "yogurt", "yoghurt", "cheese", "eggs",
    # Common produce a recipe may leave sectionless.
    "cranberries", "cranberry", "sprouts", "sprout", "squash", "potatoes", "potato", "carrots", "carrot",
    "celery", "apples", "apple", "pears", "pear", "lemons", "lemon", "oranges", "orange", "limes", "lime",
    "cabbage", "leeks", "leek", "parsnips", "parsnip", "mushrooms", "mushroom", "avocado", "avocados",
    "tomatoes", "tomato", "peppers", "pepper", "cucumber", "cucumbers", "corn", "onions", "onion",
    "garlic", "ginger", "beans", "peas", "broccoli", "cauliflower", "asparagus", "zucchini", "eggplant",
})

# When nothing says otherwise — no `oven`, no cook time — a side is
# assumed to take this long on the day.
DEFAULT_DISH_MINUTES = 20

MENU_STATUSES = ("full", "main_only", "none")


# While answer_holiday is mid-write, attendance changes it makes itself
# (the count through the intake) must not refresh the menu half-way; it
# refreshes once at the end. A plain module flag: one process, one write.
_REFRESHING_SUPPRESSED = False


class suppress_refresh:
    """`with big_meal.suppress_refresh():` — attendance's hook stays quiet inside."""

    def __enter__(self):
        global _REFRESHING_SUPPRESSED
        self._was = _REFRESHING_SUPPRESSED
        _REFRESHING_SUPPRESSED = True

    def __exit__(self, *exc):
        global _REFRESHING_SUPPRESSED
        _REFRESHING_SUPPRESSED = self._was
        return False


def on_attendance_changed(date_str: str, slot: str) -> None:
    """
    attendance.set_slot_attendance calls this after every write. A hosted
    holiday's dinner whose count just changed refreshes its menu (the
    shopping rescales, the prep re-spreads); everything else is a no-op.
    Never raises — an attendance write must not fail over a menu.
    """
    if _REFRESHING_SUPPRESSED or slot != "dinner":
        return
    try:
        row = _answer_row(date_str)
        if row is None or row["answer"] != "hosting" or not _row_menu(row).get("entry_id"):
            return
        from . import holidays as _holidays
        refresh_menu(_holidays.get_holiday_answer(date_str))
    except Exception:
        logger.exception("The big meal for %s could not follow the attendance change", date_str)


def _row_menu(row) -> dict:
    try:
        menu = json.loads(row["menu_json"] or "{}") if "menu_json" in row.keys() else {}
    except (TypeError, ValueError):
        menu = {}
    return menu if isinstance(menu, dict) else {}


# ---------- reading the answer ----------

def _answer_row(date_str: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM holiday_answers WHERE household_id = ? AND date = ?",
        (household_id(), date_str),
    ).fetchone()
    conn.close()
    return row


def _save_menu(date_str: str, menu: dict) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE holiday_answers SET menu_json = ? WHERE household_id = ? AND date = ?",
        (json.dumps(menu), household_id(), date_str),
    )
    conn.commit()
    conn.close()


def eaters_for(date_str: str, headcount: int) -> int:
    """
    How many at the table: the household members home for that dinner plus
    the extra guests attendance carries (slice 1 put the headcount there).
    A household with no members on record yet counts the extras alone.
    """
    att = _attendance.get_slot_attendance(date_str, "dinner")
    if att["household_size"]:
        return max(int(att["headcount"] or 0), int(headcount or 0) + int(att["household_size"]))
    return max(1, int(headcount or 0))


# ---------- the entry ----------

def _dinner_entry(plan_id: int, date_str: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, mpe.recipe_id, mpe.freeform_meal, mpe.slot_state, mpe.derived_from_json, mpe.sides_json, "
        "mpe.reasoning, r.name AS recipe_name, r.prep_time_minutes, r.cook_time_minutes, r.default_servings, "
        "r.ingredients_json, r.instructions_json "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = 'dinner' "
        "AND mpe.component_category IS NULL ORDER BY mpe.id DESC LIMIT 1",
        (household_id(), plan_id, date_str),
    ).fetchone()
    conn.close()
    return row


def _derived(row) -> dict:
    try:
        d = json.loads(row["derived_from_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _entry_by_id(entry_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, mpe.weekly_plan_id, mpe.date, mpe.recipe_id, mpe.freeform_meal, mpe.slot_state, "
        "mpe.derived_from_json, mpe.sides_json, mpe.reasoning, r.name AS recipe_name, r.prep_time_minutes, "
        "r.cook_time_minutes, r.default_servings, r.ingredients_json, r.instructions_json "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.id = ? AND mpe.household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    conn.close()
    return row


def menu_entry(date_str: str):
    """
    The dinner entry the menu built (or adopted), looked up by the id the
    menu recorded, if it is still there, still ours and still on the
    holiday. A dinner moved to another night (swap_dinner_nights) is the
    household's move: the menu is treated as gone — its prep rows deleted,
    its mark taken off the moved dinner, the record cleared — never
    followed to the other night.
    """
    row = _answer_row(date_str)
    if row is None or row["answer"] != "hosting":
        return None
    menu = _row_menu(row)
    entry_id = menu.get("entry_id")
    if not entry_id:
        return None
    entry = _entry_by_id(int(entry_id))
    if entry is None or not _derived(entry).get("holiday_menu"):
        if menu:
            _delete_prep(int(entry_id), date_str)
            _save_menu(date_str, {})
        return None
    if entry["date"] != date_str:
        _forget_moved(entry, date_str)
        return None
    return entry


def _forget_moved(entry, date_str: str) -> None:
    """The menu's dinner was moved off the holiday: clean up after it and leave the dinner where it went."""
    _delete_prep(entry["id"], date_str)
    conn = get_conn()
    derived = _derived(entry)
    for key in ("holiday_menu", "holiday", "menu_adopted"):
        derived.pop(key, None)
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(derived), entry["id"], household_id()),
    )
    conn.commit()
    conn.close()
    _save_menu(date_str, {})


def _mark_entry(entry_id: int, holiday_name: str, reasoning: str | None = None) -> str:
    """Mark an ADOPTED dinner as the big meal's; returns the reasoning it had, so the unwind can put it back."""
    conn = get_conn()
    row = conn.execute(
        "SELECT derived_from_json, reasoning FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        return ""
    try:
        derived = json.loads(row["derived_from_json"] or "{}")
    except (TypeError, ValueError):
        derived = {}
    derived.update({"holiday": holiday_name, "holiday_menu": True, "menu_adopted": True})
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ?, reasoning = ? WHERE id = ? AND household_id = ?",
        (json.dumps(derived), reasoning if reasoning is not None else row["reasoning"], entry_id, household_id()),
    )
    conn.commit()
    conn.close()
    return row["reasoning"] or ""


def _unmark_entry(entry_id: int, reasoning: str) -> None:
    """Give an adopted dinner back: sides off, marks off, its own reasoning back."""
    conn = get_conn()
    row = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        return
    try:
        derived = json.loads(row["derived_from_json"] or "{}")
    except (TypeError, ValueError):
        derived = {}
    for key in ("holiday_menu", "holiday", "menu_adopted"):
        derived.pop(key, None)
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ?, reasoning = ?, sides_json = '[]' "
        "WHERE id = ? AND household_id = ?",
        (json.dumps(derived), reasoning, entry_id, household_id()),
    )
    conn.commit()
    conn.close()


def _write_sides(entry_id: int, dishes: list[dict]) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET sides_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(dishes), entry_id, household_id()),
    )
    conn.commit()
    conn.close()


def _main_reasoning(holiday_name: str, eaters: int) -> str:
    return f"The big meal for {holiday_name} — {eaters} at the table."


# ---------- cleaning a proposed dish ----------

_VALID_CATEGORIES = _plates._VALID_CATEGORIES


def _int_or(value, default=None, floor: int = 0, ceiling: int | None = None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < floor:
        return default
    if ceiling is not None and n > ceiling:
        n = ceiling
    return n


def clean_dish(raw: dict, role: str | None = None, servings: int | None = None) -> dict | None:
    """
    One side or sweet as proposed (by the model or in chat), reduced to
    the shape sides_json holds plus what the day-of needs. Defensive for
    the same reason plates._clean_side is: this goes straight into a
    column the shopping list and the Cook screen read. None when it isn't
    usable — a dish with no ingredients buys nothing and cooks nothing.
    """
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or "").strip()
    if not name:
        return None
    role = role or (raw.get("role") or "side")
    role = role if role in ROLES else "side"
    ingredients = []
    for ing in raw.get("ingredients") or []:
        if not isinstance(ing, dict):
            continue
        item = (ing.get("item") or "").strip()
        if not item:
            continue
        category = (ing.get("category") or "").strip().lower()
        ingredients.append({
            "item": item,
            "qty": (ing.get("qty") or "").strip(),
            "category": category if category in _VALID_CATEGORIES else "other",
        })
    if not ingredients:
        return None
    covers = [g for g in (raw.get("covers") or []) if g in _plates.ALL_GROUPS]
    instructions = [str(s).strip() for s in (raw.get("instructions") or []) if str(s).strip()]
    minutes = _int_or(raw.get("minutes"), None)
    cook_minutes = _int_or(raw.get("cook_minutes"), None)
    ahead_days = _int_or(raw.get("ahead_days"), None, ceiling=MAX_AHEAD_DAYS)
    if ahead_days is None:
        ahead_days = default_ahead_days(name, role)
    return {
        "name": name,
        "role": role,
        "covers": covers,
        "ingredients": ingredients,
        "instructions": instructions,
        "minutes": minutes,
        "cook_minutes": cook_minutes,
        "oven": bool(raw.get("oven")),
        "ahead_days": ahead_days,
        "ahead_step": (raw.get("ahead_step") or "").strip(),
        # Written for the whole table: the grocery ingest anchors on this
        # the way it anchors on a recipe's default_servings, so the dish is
        # bought once for the table rather than scaled up again by the
        # guests attendance already counts (weekly_plan._entry_side_ingredient_groups).
        "servings": int(servings) if servings else _int_or(raw.get("servings"), None, floor=1),
    }


# ASSUMPTION — a small, readable rule for what is made ahead when the
# proposal (or the person) didn't say. Sauces, stuffings, casseroles,
# pies and rolls keep; anything green or crisp is made on the day.
_AHEAD_BY_WORD = (
    (("cranberry", "sauce", "gravy", "chutney", "relish", "dressing"), 1),
    (("stuffing", "casserole", "gratin", "mash", "mashed", "bake", "soup"), 1),
    (("pie", "tart", "cake", "cheesecake", "crumble", "cobbler", "pudding", "brownie", "cookie", "trifle"), 1),
    (("roll", "bread", "bun", "biscuit"), 1),
)
_DAY_OF_WORDS = ("salad", "greens", "slaw", "roast", "roasted", "steamed", "sautéed", "sauteed", "glazed", "grilled")


def default_ahead_days(name: str, role: str) -> int:
    words = re.sub(r"[^a-z\s-]", " ", (name or "").lower()).split()
    if any(w in _DAY_OF_WORDS for w in words):
        return 0
    for keywords, days in _AHEAD_BY_WORD:
        if any(w in keywords or w.rstrip("s") in keywords for w in words):
            return days
    return 1 if role == "sweet" else 0


# ---------- what the table can't have ----------

def restrictions_context(guest_notes: str) -> dict:
    """The household's own restrictions and dislikes, plus the guests' notes, for the proposal."""
    memory = _memory.get_household_memory()
    restrictions = []
    for member in memory.get("members") or []:
        for r in member.get("dietary_restrictions") or []:
            if r and r.strip():
                restrictions.append(f"{member.get('name') or 'someone'}: {r.strip()}")
    for fact in _memory.get_facts():
        if fact.get("hard") and (fact.get("text") or "").strip():
            restrictions.append(fact["text"].strip())
    return {
        "dietary_restrictions": restrictions,
        "guest_notes": (guest_notes or "").strip(),
        "dislikes": memory.get("dislikes") or [],
        "eating_style": memory.get("eating_style") or "",
    }


def guest_avoidances(guest_notes: str) -> list[dict]:
    """
    The guests' notes read the way a hard What-we-know fact is — "no nuts",
    "Sam can't have dairy", "shellfish allergy" — so the same matcher that
    guards the household's own table guards the guests'. A note with no
    avoidance in it ("Sam's vegetarian") contributes no match terms; that
    one is honoured in the proposal itself, where the model reads it.
    """
    text = (guest_notes or "").strip()
    if not text:
        return []
    phrases, excepted = _coordination._fact_keywords(text, drop=_people_words(text))
    terms = _coordination._match_terms(phrases, excepted)
    if not terms:
        return []
    return [{"member": "a guest", "label": text, "source": "guest", "severity": "hard", "terms": terms}]


def _people_words(text: str) -> set[str]:
    """
    The words in a guest note that name people, not food, so "no nuts for
    Grandma" is a rule about nuts and never a match on a dish called
    "Grandma's rolls": every household member's name, every possessive
    ("Priya's"), and every capitalised word that isn't starting a sentence.
    """
    words: set[str] = set()
    try:
        for m in _household.list_members():
            words |= _coordination._name_words(m.get("name") or "")
    except Exception:
        pass
    sentence_start = True
    for token in re.findall(r"[A-Za-z][A-Za-z'’]*|[.!?;]", text):
        if token in ".!?;":
            sentence_start = True
            continue
        bare = re.sub(r"[’']s?$", "", token)
        possessive = token.endswith(("’s", "'s"))
        # A capital mid-sentence reads as a name — unless the word is food
        # the matcher knows ("No Nuts", "no Peanuts for the kids") or it's
        # shouted in caps ("NO NUTS"), which is emphasis, not a person.
        looks_like_name = token[0].isupper() and not sentence_start and not token.isupper()
        if (possessive or looks_like_name) and not _is_food_word(bare.lower()):
            words.add(bare.lower())
        sentence_start = False
    return words


def _is_food_word(word: str) -> bool:
    """Is this a word the allergy matcher would treat as food — an allergen family, an alias, or its plural twin?"""
    if not word:
        return False
    aliases = getattr(_coordination, "_ALLERGEN_ALIASES", {}) or {}
    known = set(aliases)
    for family in aliases.values():
        known |= set(family)
    known |= PERISHABLE_WORDS
    variants = {word} | set(_coordination._keyword_variants(word))
    return any(v in known for v in variants)


def dish_conflicts(name: str, ingredients: list[dict], guest_notes: str) -> list[dict]:
    """Every hard clash one dish trips — the household's restrictions and the guests' notes together."""
    avoidances = _coordination._avoidances() + guest_avoidances(guest_notes)
    hits = _coordination.check_meal_conflicts(name, ingredients, avoidances=avoidances)
    return [h for h in hits if h.get("severity") == "hard"]


# ---------- the proposal ----------

def proposal_context(
    saved: dict, existing_main: dict | None, side_count: int = DEFAULT_SIDE_COUNT,
    *, want_sweet: bool = True, avoid: list[dict] | None = None, keep: list[str] | None = None,
) -> dict:
    eaters = eaters_for(saved["date"], saved["headcount"])
    ctx = {
        "holiday": saved["holiday_name"],
        "date": saved["date"],
        "eaters": eaters,
        "on_table_at": saved.get("on_table_at") or "",
        "side_count": side_count,
        "want_sweet": want_sweet,
        "want_main": existing_main is None,
        **restrictions_context(saved.get("guest_notes") or ""),
    }
    if avoid:
        # Dishes already tried that clashed with the table — never again.
        ctx["avoid"] = avoid
    if keep:
        ctx["already_on_the_menu"] = keep
    if existing_main is not None:
        ctx["main"] = existing_main
    memory = _memory.get_household_memory()
    if memory.get("kitchen_kit"):
        ctx["kitchen_kit"] = memory["kitchen_kit"]
    if memory.get("cuisines"):
        ctx["cuisines"] = memory["cuisines"]
    return ctx


def _default_proposer():
    # Imported at call time, not import time: agent imports this package,
    # so an import-time reach back the other way would make the cycle real.
    from .. import agent as _agent
    return _agent.generate_big_meal_llm


def _propose(context: dict, proposer=None) -> dict | None:
    proposer = proposer or _default_proposer()
    try:
        raw = proposer(context)
    except Exception:
        logger.exception("The big-meal proposal for %s could not be made; degrading", context.get("date"))
        return None
    return raw if isinstance(raw, dict) else None


def _clean_main(raw: dict | None, eaters: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or "").strip()
    ingredients = []
    for ing in raw.get("ingredients") or []:
        if isinstance(ing, dict) and (ing.get("item") or "").strip():
            category = (ing.get("category") or "").strip().lower()
            ingredients.append({
                "item": ing["item"].strip(), "qty": (ing.get("qty") or "").strip(),
                "category": category if category in _VALID_CATEGORIES else "other",
            })
    if not name or not ingredients:
        return None
    return {
        "name": name,
        "ingredients": ingredients,
        "instructions": [str(s).strip() for s in (raw.get("instructions") or []) if str(s).strip()],
        "food_groups": [g for g in (raw.get("food_groups") or []) if g in _plates.ALL_GROUPS],
        "cuisine": (raw.get("cuisine") or "").strip(),
        "main_protein": (raw.get("main_protein") or "").strip(),
        "prep_minutes": _int_or(raw.get("prep_minutes"), None),
        "cook_minutes": _int_or(raw.get("cook_minutes"), None),
        "rest_minutes": _int_or(raw.get("rest_minutes"), 0),
        "oven": bool(raw.get("oven", True)),
        "ahead_days": _int_or(raw.get("ahead_days"), 0, ceiling=MAX_AHEAD_DAYS),
        "ahead_step": (raw.get("ahead_step") or "").strip(),
        "default_servings": eaters,
    }


def _main_timing(raw: dict | None, entry) -> dict:
    """The main's day-of timing, from the proposal when there was one, else the recipe's own times."""
    raw = raw or {}
    return {
        "prep_minutes": _int_or(raw.get("prep_minutes"), None) or (entry["prep_time_minutes"] if entry is not None else None),
        "cook_minutes": _int_or(raw.get("cook_minutes"), None) or (entry["cook_time_minutes"] if entry is not None else None),
        "rest_minutes": _int_or(raw.get("rest_minutes"), 0),
        "oven": bool(raw.get("oven", True)),
        "ahead_days": _int_or(raw.get("ahead_days"), 0, ceiling=MAX_AHEAD_DAYS),
        "ahead_step": (raw.get("ahead_step") or "").strip(),
    }


def _existing_main_summary(entry) -> dict | None:
    if entry is None or entry["slot_state"] != "planned":
        return None
    name = entry["recipe_name"] or entry["freeform_meal"]
    if not name:
        return None
    try:
        ingredients = json.loads(entry["ingredients_json"] or "[]") if entry["recipe_id"] else []
    except (TypeError, ValueError):
        ingredients = []
    return {
        "name": name,
        "ingredients": [i.get("item") for i in ingredients if isinstance(i, dict) and i.get("item")],
        "prep_time_minutes": entry["prep_time_minutes"],
        "cook_time_minutes": entry["cook_time_minutes"],
    }


def _recipe_for_main(main: dict, holiday_name: str) -> int:
    """A saved recipe for the proposed main — reused by name when one exists, else written for this table."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM recipes WHERE household_id = ? AND lower(name) = lower(?)",
        (household_id(), main["name"]),
    ).fetchone()
    conn.close()
    if row:
        return row["id"]
    saved = _recipes.add_recipe(
        name=main["name"],
        ingredients=main["ingredients"],
        tags=["big meal", holiday_name.lower()],
        food_groups=main["food_groups"],
        cuisine=main["cuisine"],
        main_protein=main["main_protein"],
        instructions=main["instructions"],
        default_servings=max(1, int(main["default_servings"] or 1)),
        prep_time_minutes=main["prep_minutes"],
        cook_time_minutes=main["cook_minutes"],
    )
    return saved["recipe_id"]


def _is_reheat(entry) -> bool:
    return bool(_derived(entry).get("links_to")) if entry is not None else False


def build_menu(saved: dict, proposer=None, side_count: int = DEFAULT_SIDE_COUNT) -> dict:
    """
    Turn a 'hosting' answer into the big meal on the plan that covers the
    day. Idempotent: a menu that is already there is kept (a second call —
    from the intake's steppers, from Now, from generation — must not throw
    away dishes the household has since changed). Returns what happened:
    {"menu": "built" | "kept" | "waiting_for_plan", "status", ...}.

    The main is whatever the planner (or the household) already put in
    that dinner slot, when there is one — the answer builds AROUND the
    dinner rather than replacing it, so a hosting week generated with the
    holiday in view keeps the dish the model chose for the day. Only an
    empty or open slot gets a main proposed.
    """
    from . import weekly_plan as _wp

    d, name = saved["date"], saved["holiday_name"]
    notes = saved.get("guest_notes") or ""
    att = _attendance.get_slot_attendance(d, "dinner")
    if att["explicit"] and att["nobody_home"]:
        # A trip already has this dinner as nobody-home (slice 1: the trip
        # wins, the headcount is only recorded). Nothing to build around.
        return {"menu": "trip", "status": "none",
                "note": f"You’re away over {name} on the calendar — I’ve kept the count and planned nothing. Tell me if the trip’s changed."}
    plan_id = _wp.get_plan_id_for_date(d)
    if plan_id is None:
        return {"menu": "waiting_for_plan", "status": "none"}
    if menu_entry(d) is not None:
        return {"menu": "kept", "status": _row_menu(_answer_row(d)).get("status", "full")}

    plan = _wp.get_weekly_plan(plan_id)
    approved = plan.get("status") == "approved"
    entry = _dinner_entry(plan_id, d)
    existing = None if _is_reheat(entry) else _existing_main_summary(entry)
    eaters = eaters_for(d, saved["headcount"])

    raw = _propose(proposal_context(saved, existing, side_count), proposer)
    main_raw = None if existing is not None else (raw or {}).get("main")
    main = _clean_main(main_raw, eaters) if main_raw else None
    dropped: list[dict] = []
    conflicts: list[dict] = []
    if main is not None:
        # The main is checked like every other dish. One more try with the
        # clash named; then the slot is handed back rather than a shrimp
        # boil landing on a shellfish-allergic table.
        clashes = dish_conflicts(main["name"], main["ingredients"], notes)
        if clashes:
            dropped.append({"name": main["name"], "restriction": clashes[0]["restriction"], "role": "main"})
            raw2 = _propose(proposal_context(saved, None, side_count, avoid=dropped), proposer)
            main2 = _clean_main((raw2 or {}).get("main"), eaters) if raw2 else None
            if main2 is not None and not dish_conflicts(main2["name"], main2["ingredients"], notes):
                main, raw = main2, raw2
            else:
                if main2 is not None:
                    dropped.append({"name": main2["name"], "restriction": dish_conflicts(main2["name"], main2["ingredients"], notes)[0]["restriction"], "role": "main"})
                main = None
    if existing is not None:
        # An adopted dinner is the household's own choice: a clash with a
        # guest's note is SAID, never acted on.
        for hit in dish_conflicts(existing["name"], [{"item": i} for i in existing["ingredients"]], notes):
            conflicts.append({"dish": existing["name"], "restriction": hit["restriction"], "member": hit.get("member")})

    # --- the main ---
    adopted = existing is not None
    prior_reasoning = ""
    if adopted:
        entry_id = entry["id"]
        prior_reasoning = _mark_entry(entry_id, name, reasoning=_main_reasoning(name, eaters))
        main_timing = _main_timing((raw or {}).get("main_timing") or {}, entry)
    elif main is not None:
        _recipe_for_main(main, name)
        _wp.clear_plan_slot(plan_id, d, "dinner")
        planned = _meal_plans.plan_meal(
            d, main["name"], slot="dinner", weekly_plan_id=plan_id,
            add_ingredients_to_grocery_list=False, food_groups=main["food_groups"] or None,
            reasoning=_main_reasoning(name, eaters),
            derived_from={"holiday": name, "holiday_menu": True, "constraint": "hosting"},
        )
        entry_id = planned["entry_id"]
        main_timing = _main_timing(main, _entry_by_id(entry_id))
    else:
        # No main to be had: the slot is handed back as a question that
        # names why, never left blank. Nothing else to build around.
        why = ""
        if dropped:
            why = f" The one I had in mind clashed with {dropped[-1]['restriction']}."
        if entry is None or entry["slot_state"] != "planned":
            _wp.clear_plan_slot(plan_id, d, "dinner")
            _wp.plan_slot_open(
                weekly_plan_id=plan_id, meal_date=d, slot="dinner",
                open_reason=f"You’re hosting {name} for {eaters} — what’s the main?{why} Tell me and I’ll build the rest around it.",
                derived_from={"holiday": name, "holiday_menu": True, "constraint": "hosting"},
            )
        menu = {"entry_id": None, "status": "none", "eaters": eaters, "dropped": dropped,
                "note": f"I couldn’t put the menu together just now.{why} Tell me the main and I’ll build the rest around it."}
        _save_menu(d, menu)
        return {"menu": "built", **menu}

    # --- the sides and the sweet ---
    dishes = _usable_dishes((raw or {}).get("dishes") or [], eaters, notes, dropped)
    kept_sides = [dict(s, role=s.get("role") or "side") for s in _plates.get_sides(entry_id)]
    _write_sides(entry_id, kept_sides + dishes)

    if approved:
        _buy(entry_id, plan_id)

    status = "full" if dishes else "main_only"
    note = ""
    if not dishes:
        note = "I’ve got the main. I couldn’t put the sides together just now — tell me what you’d like alongside and I’ll add them."
    menu = {
        "entry_id": entry_id, "status": status, "eaters": eaters, "main": main_timing,
        "adopted": adopted, "prior_reasoning": prior_reasoning,
        "dropped": dropped, "conflicts": conflicts, "note": note,
    }
    _save_menu(d, menu)
    spread_prep(d)
    return {"menu": "built", **menu}


def _usable_dishes(raw_dishes: list, eaters: int, notes: str, dropped: list[dict]) -> list[dict]:
    """Clean each proposed side/sweet and keep the ones that don't clash; the rest are recorded in `dropped`."""
    dishes = []
    for raw_dish in raw_dishes:
        dish = clean_dish(raw_dish, servings=eaters)
        if dish is None:
            continue
        clashes = dish_conflicts(dish["name"], dish["ingredients"], notes)
        if clashes:
            dropped.append({"name": dish["name"], "restriction": clashes[0]["restriction"], "role": dish["role"]})
            continue
        dishes.append(dish)
    return dishes


def refresh_menu(saved: dict, proposer=None) -> dict:
    """
    The answer changed under a standing menu — a new count, a new time, a
    guest's note — so the menu follows, in place:
      - every dish, the main included, is re-checked against the table's
        restrictions and the guests' notes; a side or sweet that clashes
        comes off, and one proposal is made for replacements (the roles
        that were lost, avoiding what just clashed); a PROPOSED main that
        clashes is proposed again once, else the slot is handed back with
        the reason; an ADOPTED main that clashes is said, not touched;
      - the shopping is re-bought for the new table (the main scales by
        attendance, each dish by the count it was written for — see
        weekly_plan._entry_side_ingredient_groups), for an approved week;
      - the prep is spread again.
    Returns {"menu": "refreshed", "dropped", "conflicts", "replaced", "eaters"}.
    """
    from . import weekly_plan as _wp

    d, name = saved["date"], saved["holiday_name"]
    notes = saved.get("guest_notes") or ""
    entry = menu_entry(d)
    if entry is None:
        return {"menu": "none"}
    menu = _row_menu(_answer_row(d))
    plan_id = entry["weekly_plan_id"]
    approved = _plan_approved(plan_id)
    eaters = eaters_for(d, saved["headcount"])
    dropped: list[dict] = []
    conflicts: list[dict] = []
    entry_id = entry["id"]

    # The main.
    main_name = entry["recipe_name"] or entry["freeform_meal"] or ""
    try:
        main_ingredients = json.loads(entry["ingredients_json"] or "[]") if entry["recipe_id"] else []
    except (TypeError, ValueError):
        main_ingredients = []
    main_clashes = dish_conflicts(main_name, main_ingredients, notes) if main_name else []
    if main_clashes and menu.get("adopted"):
        conflicts.append({"dish": main_name, "restriction": main_clashes[0]["restriction"], "member": main_clashes[0].get("member")})
    elif main_clashes:
        dropped.append({"name": main_name, "restriction": main_clashes[0]["restriction"], "role": "main"})
        raw = _propose(proposal_context(saved, None, 0, want_sweet=False, avoid=dropped), proposer)
        main = _clean_main((raw or {}).get("main"), eaters) if raw else None
        sides = _plates.get_sides(entry_id)
        _delete_prep(entry_id, d)
        if main is not None and not dish_conflicts(main["name"], main["ingredients"], notes):
            _recipe_for_main(main, name)
            _wp.clear_plan_slot(plan_id, d, "dinner")
            planned = _meal_plans.plan_meal(
                d, main["name"], slot="dinner", weekly_plan_id=plan_id,
                add_ingredients_to_grocery_list=False, food_groups=main["food_groups"] or None,
                reasoning=_main_reasoning(name, eaters),
                derived_from={"holiday": name, "holiday_menu": True, "constraint": "hosting"},
            )
            entry_id = planned["entry_id"]
            _write_sides(entry_id, sides)
            menu["entry_id"] = entry_id
            menu["main"] = _main_timing(main, _entry_by_id(entry_id))
        else:
            _wp.clear_plan_slot(plan_id, d, "dinner")
            _wp.plan_slot_open(
                weekly_plan_id=plan_id, meal_date=d, slot="dinner",
                open_reason=f"You’re hosting {name} for {eaters} — what’s the main? {main_name} clashed with {main_clashes[0]['restriction']}. Tell me and I’ll build the rest around it.",
                derived_from={"holiday": name, "holiday_menu": True, "constraint": "hosting"},
            )
            menu.update({"entry_id": None, "status": "none", "eaters": eaters, "dropped": dropped, "conflicts": conflicts,
                         "note": f"I’ve taken {main_name} off — it clashed with {main_clashes[0]['restriction']}. Tell me the main and I’ll build the rest around it."})
            _save_menu(d, menu)
            return {"menu": "refreshed", "dropped": dropped, "conflicts": conflicts, "replaced": [], "eaters": eaters}

    # The sides and the sweet.
    current = [dict(s, role=s.get("role") or "side") for s in _plates.get_sides(entry_id)]
    kept, lost_roles = [], []
    for dish in current:
        clashes = dish_conflicts(dish["name"], dish.get("ingredients") or [], notes)
        if clashes:
            dropped.append({"name": dish["name"], "restriction": clashes[0]["restriction"], "role": dish["role"]})
            lost_roles.append(dish["role"])
        else:
            kept.append(dish)
    replaced: list[str] = []
    if lost_roles:
        existing = _existing_main_summary(_entry_by_id(entry_id))
        raw = _propose(proposal_context(
            saved, existing or {"name": main_name, "ingredients": []},
            lost_roles.count("side"), want_sweet="sweet" in lost_roles,
            avoid=dropped, keep=[k["name"] for k in kept],
        ), proposer)
        for dish in _usable_dishes((raw or {}).get("dishes") or [], eaters, notes, dropped):
            if dish["role"] in lost_roles:
                lost_roles.remove(dish["role"])
                kept.append(dish)
                replaced.append(dish["name"])
    _write_sides(entry_id, kept)
    menu.update({
        "eaters": eaters, "dropped": dropped, "conflicts": conflicts,
        "status": "full" if kept else "main_only",
        "note": "" if kept else "I’ve got the main. Tell me what you’d like alongside and I’ll add it.",
    })
    _save_menu(d, menu)
    if approved:
        _buy(entry_id, plan_id)
    spread_prep(d)
    return {"menu": "refreshed", "dropped": dropped, "conflicts": conflicts, "replaced": replaced, "eaters": eaters}


def _buy(entry_id: int, plan_id: int) -> None:
    """
    Put the whole dinner — main and every dish on it — on the list for an
    approved week, once: whatever the entry already contributed comes off
    first, then the entry goes through the same recipe-plus-sides ingest
    approval uses. A draft's dinner waits for approval like every other.
    """
    _grocery._reverse_meal_grocery_contributions(entry_id)
    _weekly_plan._reingest_unlinked_entries(plan_id)


def clear_menu(date_str: str, holiday_name: str) -> dict:
    """
    Take the big meal back off the plan when the answer stops being
    'hosting' — the prep this module spread, the dinner it built (handed
    back as an open question, groceries reversed, never a blank) and the
    menu record. Only its own work: a dinner without the holiday_menu mark
    is left exactly as it is, and the shop split, being read-time, has
    nothing to clear.
    """
    from . import holidays as _holidays

    row = _answer_row(date_str)
    menu = _row_menu(row) if row is not None else {}
    entry_id = menu.get("entry_id")
    removed_prep = _delete_prep(entry_id, date_str)
    reopened = restored = False
    if entry_id:
        entry = _entry_by_id(int(entry_id))
        if entry is not None and entry["date"] != date_str:
            # Moved to another night by the household: theirs now.
            _forget_moved(entry, date_str)
        elif entry is not None and _derived(entry).get("holiday_menu"):
            if menu.get("adopted") or _derived(entry).get("menu_adopted"):
                # Their own dinner, given back as it was: sides off, marks
                # off, and — for an approved week — its shopping re-bought
                # for the household alone (the sides' lines come off with
                # the reversal; the main's go back on).
                _unmark_entry(entry["id"], menu.get("prior_reasoning") or "")
                if _plan_approved(entry["weekly_plan_id"]):
                    _buy(entry["id"], entry["weekly_plan_id"])
                restored = True
            else:
                _holidays._reopen(entry["weekly_plan_id"], entry["date"], holiday_name)
                reopened = True
    if row is not None and menu:
        _save_menu(date_str, {})
    return {"prep_removed": removed_prep, "dinner_reopened": reopened, "dinner_restored": restored}


# ---------- the prep spread ----------

def _plan_covering(date_str: str, fallback_plan_id: int) -> int:
    plan_id = _weekly_plan.get_plan_id_for_date(date_str)
    return plan_id if plan_id is not None else fallback_plan_id


def _weekday(date_str: str) -> str:
    return date.fromisoformat(date_str).strftime("%A")


def _prep_anchor(date_str: str) -> str | None:
    """
    The household's standing prep day, when one falls in the three days
    before the holiday: the make-ahead work lands there rather than on
    "the day before", because that IS the day they cook ahead. None when
    they have none, or none falls in the window.
    """
    try:
        prep_days = _rhythm.get_household_rhythm().get("prep_days") or []
    except Exception:
        return None
    weekdays = {(p.get("weekday") or "").lower() for p in prep_days if isinstance(p, dict)}
    if not weekdays:
        return None
    d = date.fromisoformat(date_str)
    for back in range(1, MAX_AHEAD_DAYS + 2):
        day = d - timedelta(days=back)
        if day.strftime("%A").lower() in weekdays:
            return day.isoformat()
    return None


def _delete_prep(entry_id: int | None, date_str: str) -> int:
    conn = get_conn()
    if entry_id:
        cur = conn.execute(
            "DELETE FROM prep_tasks WHERE household_id = ? AND task_type = ? AND meal_plan_entry_id = ?",
            (household_id(), TASK_TYPE, int(entry_id)),
        )
    else:
        cur = conn.execute(
            "DELETE FROM prep_tasks WHERE household_id = ? AND task_type = ? AND related_meal = ?",
            (household_id(), TASK_TYPE, f"{date_str}:dinner"),
        )
    conn.commit()
    conn.close()
    return cur.rowcount


def shop_dates(date_str: str, today: date | None = None) -> dict:
    """The two trips' dates. The early trip is dropped once it is already too late for it."""
    today = today or date.today()
    d = date.fromisoformat(date_str)
    early = d - timedelta(days=EARLY_SHOP_DAYS_AHEAD)
    fresh = d - timedelta(days=FRESH_SHOP_DAYS_AHEAD)
    if fresh < today:
        fresh = min(today, d)
    return {
        "early": early.isoformat() if early >= today and early < fresh else None,
        "fresh": fresh.isoformat(),
    }


def spread_prep(date_str: str, today: date | None = None) -> list[dict]:
    """
    Rewrite this menu's prep_tasks rows from the menu as it stands: one row
    per make-ahead dish on its day, and the two shops. Each row is dated
    into the plan whose period holds THAT DAY (the days before a Monday
    holiday belong to the week before), so today_moves finds it where it
    looks. Nothing is written for a day already gone.
    """
    row = _answer_row(date_str)
    if row is None or row["answer"] != "hosting":
        return []
    menu = _row_menu(row)
    entry_id = menu.get("entry_id")
    entry = _entry_by_id(int(entry_id)) if entry_id else None
    if entry is None:
        return []
    name = row["holiday_name"]
    _delete_prep(entry_id, date_str)
    if not _plan_approved(entry["weekly_plan_id"]):
        # A draft is a proposal. The prep goes on Now when the week is
        # approved (approve_weekly_plan calls spread_prep_for_plan), the
        # same moment its shopping does.
        return []
    today = today or date.today()
    d = date.fromisoformat(date_str)
    anchor = _prep_anchor(date_str)
    weekday = _weekday(date_str)
    tasks: list[dict] = []

    def _add(task_date: str, description: str, related: str) -> None:
        if date.fromisoformat(task_date) < today:
            return
        tasks.append({"task_date": task_date, "description": description, "related_meal": related})

    # The dishes made ahead.
    for dish in dishes_of(entry, menu):
        ahead = int(dish.get("ahead_days") or 0)
        if ahead < 1:
            continue
        task_date = (d - timedelta(days=ahead)).isoformat()
        if anchor and date.fromisoformat(anchor) <= d - timedelta(days=1):
            task_date = anchor
        step = dish.get("ahead_step") or f"Make the {dish['name'].lower() if dish['role'] != 'main' else dish['name']}"
        _add(task_date, f"{step.rstrip('.')} — for the big meal on {weekday}.", dish["name"])

    # The two shops. related_meal SHOP_MARK is how moves.py knows to read
    # these as a shop, not a prep.
    trips = shop_dates(date_str, today)
    if trips["early"]:
        _add(trips["early"], f"The keeps-well shop for {name} — pantry and freezer things, so the fresh trip stays short.", SHOP_MARK)
    if trips["fresh"] and trips["fresh"] != date_str:
        _add(trips["fresh"], f"The fresh shop for {name} — produce, dairy, meat and fish.", SHOP_MARK)

    conn = get_conn()
    for t in tasks:
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, task_type, meal_plan_entry_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (household_id(), _plan_covering(t["task_date"], entry["weekly_plan_id"]), t["task_date"],
             t["description"], t["related_meal"], TASK_TYPE, int(entry_id)),
        )
    conn.commit()
    conn.close()
    return tasks


# related_meal on a holiday shop row. moves.py renders such a row as a
# shop (kind "shop") and lets it stand in for the week's own shop move on
# that day, so Now never asks for the same trip twice.
SHOP_MARK = "Shop"


def spread_prep_for_plan(weekly_plan_id: int) -> list[str]:
    """
    approve_weekly_plan's hook: every hosted holiday whose menu lives on
    this plan gets its prep spread now that the week is real. Never raises.
    """
    out = []
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT date FROM holiday_answers WHERE household_id = ? AND answer = 'hosting' AND menu_json != '{}'",
            (household_id(),),
        ).fetchall()
        conn.close()
        for r in rows:
            entry = menu_entry(r["date"])
            if entry is not None and entry["weekly_plan_id"] == weekly_plan_id:
                spread_prep(r["date"])
                out.append(r["date"])
    except Exception:
        logger.exception("The big meal's prep could not be spread at approval of plan %s", weekly_plan_id)
    return out


def prep_rows(entry_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, task_date, description, related_meal, status, weekly_plan_id FROM prep_tasks "
        "WHERE household_id = ? AND task_type = ? AND meal_plan_entry_id = ? ORDER BY task_date, id",
        (household_id(), TASK_TYPE, entry_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- the dishes, as one list ----------

def dishes_of(entry, menu: dict) -> list[dict]:
    """
    Every dish on the big meal as one list — the main first (from the
    entry's recipe plus the menu's timing), then the sides and the sweet
    (from sides_json), each with role, minutes, cook_minutes, oven and
    ahead_days filled in from the small default rules where nothing said.
    """
    out = []
    main_name = entry["recipe_name"] or entry["freeform_meal"]
    if main_name:
        timing = dict(menu.get("main") or {})
        out.append({
            "name": main_name,
            "role": "main",
            "recipe_id": entry["recipe_id"],
            "minutes": timing.get("prep_minutes") or entry["prep_time_minutes"] or 30,
            "cook_minutes": timing.get("cook_minutes") or entry["cook_time_minutes"] or 60,
            "rest_minutes": timing.get("rest_minutes") or 0,
            "oven": timing.get("oven", True),
            "ahead_days": int(timing.get("ahead_days") or 0),
            "ahead_step": timing.get("ahead_step") or "",
        })
    try:
        sides = json.loads(entry["sides_json"] or "[]")
    except (TypeError, ValueError):
        sides = []
    for s in sides if isinstance(sides, list) else []:
        if not isinstance(s, dict) or not (s.get("name") or "").strip():
            continue
        role = s.get("role") if s.get("role") in ROLES else "side"
        ahead = s.get("ahead_days")
        out.append({
            "name": s["name"].strip(),
            "role": role,
            "minutes": _int_or(s.get("minutes"), None),
            "cook_minutes": _int_or(s.get("cook_minutes"), None),
            "oven": bool(s.get("oven")),
            "ahead_days": _int_or(ahead, None, ceiling=MAX_AHEAD_DAYS) if ahead is not None else default_ahead_days(s["name"], role),
            "ahead_step": (s.get("ahead_step") or "").strip(),
            "ingredients": s.get("ingredients") or [],
        })
    return out


# ---------- the shop split ----------

def _upcoming_menus(today: date | None = None) -> list[tuple[dict, object]]:
    """Every hosting answer from today on whose menu entry is still there, nearest first."""
    today = today or date.today()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM holiday_answers WHERE household_id = ? AND answer = 'hosting' AND date >= ? ORDER BY date",
        (household_id(), today.isoformat()),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        entry = menu_entry(row["date"])
        if entry is not None:
            out.append((dict(row), entry))
    return out


def shop_split(today: date | None = None) -> dict | None:
    """
    The nearest upcoming big meal's shopping in two trips — which grocery
    lines (by id) to buy on the keeps-well trip and which fresh, with each
    trip's date and label. None when there's no big meal ahead, or the
    list doesn't hold its lines yet (a draft not approved), or the early
    trip's day has passed (one trip left: nothing to split).

    A line is early when every menu ingredient on it keeps (EARLY_CATEGORIES)
    OR another meal needs it before the early trip anyway; fresh otherwise.
    """
    today = today or date.today()
    for answer, entry in _upcoming_menus(today):
        trips = shop_dates(answer["date"], today)
        conn = get_conn()
        links = conn.execute(
            "SELECT DISTINCT grocery_item_id FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id = ?",
            (household_id(), entry["id"]),
        ).fetchall()
        ids = [r["grocery_item_id"] for r in links]
        if not ids:
            conn.close()
            continue
        marks = ",".join("?" * len(ids))
        items = conn.execute(
            f"SELECT id, item, category FROM grocery_items WHERE household_id = ? AND id IN ({marks}) AND status = 'needed'",
            (household_id(), *ids),
        ).fetchall()
        # A line another meal needs BEFORE the fresh trip has to be bought
        # on the early one — Saturday's onions can't wait for Sunday.
        needed_before = set()
        if trips["early"]:
            rows = conn.execute(
                f"SELECT DISTINCT l.grocery_item_id FROM meal_plan_grocery_links l "
                f"JOIN meal_plan_entries e ON e.id = l.meal_plan_entry_id "
                f"WHERE l.household_id = ? AND l.grocery_item_id IN ({marks}) AND e.id != ? AND e.date < ?",
                (household_id(), *ids, entry["id"], trips["fresh"]),
            ).fetchall()
            needed_before = {r["grocery_item_id"] for r in rows}
        conn.close()
        if not items:
            continue
        early, fresh = [], []
        for it in items:
            if not trips["early"]:
                fresh.append(it["id"])
            elif it["id"] in needed_before or keeps(it["item"], it["category"]):
                early.append(it["id"])
            else:
                fresh.append(it["id"])
        name = answer["holiday_name"]
        return {
            "holiday_name": name,
            "date": answer["date"],
            "entry_id": entry["id"],
            "early": {
                "date": trips["early"],
                "label": f"For {name} — buy by {_weekday(trips['early'])}" if trips["early"] else "",
                "item_ids": early,
            },
            "fresh": {
                "date": trips["fresh"],
                "label": f"For {name} — buy fresh {_relative_day(trips['fresh'], today)}",
                "item_ids": fresh,
            },
        }
    return None


def keeps(item: str, category: str | None) -> bool:
    """
    Does this grocery line keep until the early trip? By its store section
    when the recipe named one; a line in "other" (no section named) is
    read by PERISHABLE_WORDS — raw chicken and fresh thyme are fresh, foil
    and candles keep.
    """
    category = (category or "other").strip().lower()
    category = _quantities._GROCERY_CATEGORY_ALIASES.get(category, category)
    if category == "other":
        words = set(re.sub(r"[^a-z\s-]", " ", (item or "").lower()).replace("-", " ").split())
        return not (words & PERISHABLE_WORDS)
    return category in EARLY_CATEGORIES


def _relative_day(date_str: str, today: date) -> str:
    d = date.fromisoformat(date_str)
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return "on " + d.strftime("%A")


def annotate_shop_split(items: list[dict], today: date | None = None) -> dict | None:
    """Stamp `shop_timing` ('early' | 'fresh') on the list's rows in place; returns the split summary or None."""
    split = shop_split(today)
    if split is None:
        return None
    early = set(split["early"]["item_ids"])
    fresh = set(split["fresh"]["item_ids"])
    for it in items:
        if it.get("id") in early:
            it["shop_timing"] = "early"
        elif it.get("id") in fresh:
            it["shop_timing"] = "fresh"
    return split


# ---------- the day-of timeline ----------

def _say_time(t: time) -> str:
    """"4:15 pm", "noon" — the way shell.js's humanTime says it."""
    if t.hour == 12 and t.minute == 0:
        return "noon"
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def _parse_clock(value: str) -> time | None:
    """
    "5pm", "17:30", "5:30 pm", "6" -> a time. A bare hour from 1 to 11 with
    no am/pm reads as the evening ("6" is six o'clock dinner, not dawn);
    "12" is noon; anything with a colon or an am/pm is taken as written.
    """
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*$", (value or "").lower())
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not ampm and m.group(2) is None and 1 <= hour <= 11:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def default_on_table_at() -> time:
    """The household's own dinner clock when nothing was said — moves.py already owns that mapping."""
    from . import moves as _moves
    try:
        return _moves._dinner_clock()
    except Exception:
        return time(18, 0)


def normalise_on_table_at(value: str | None) -> str:
    """'5pm', '17:00', '5:30 pm' -> '17:00' / '17:30'; '' stays ''. Anything else is refused."""
    if value is None or not str(value).strip():
        return ""
    t = _parse_clock(str(value))
    if t is None:
        raise ValueError(f"I didn’t catch that time ({value!r}) — try something like 5pm or 17:30.")
    return t.strftime("%H:%M")


def timeline(date_str: str) -> dict | None:
    """
    The day of, working back from the time it's on the table: an ordered
    list of steps with a clock time each, one oven. None when there's no
    big meal on that date.

    The rule, in the order a host thinks: the main comes out to rest at
    T − rest and goes in at T − rest − cook, hands-on prep before that.
    Oven dishes that fit in the rest window warm through then; those that
    don't stack in front of the main, latest first, and are noted as
    keeping warm. Stovetop and no-cook dishes finish at T. A dish made
    ahead only needs warming (DEFAULT_REHEAT_MINUTES) or dressing on the
    day. Nothing here is more precise than the recipe times it was given.
    """
    row = _answer_row(date_str)
    entry = menu_entry(date_str)
    if row is None or entry is None:
        return None
    menu = _row_menu(row)
    dishes = dishes_of(entry, menu)
    if not dishes:
        return None
    on_table = _parse_clock(row["on_table_at"]) if row["on_table_at"] else None
    on_table = on_table or default_on_table_at()
    T = datetime.combine(date.fromisoformat(date_str), on_table)
    steps: list[dict] = []
    oven_busy: list[tuple[datetime, datetime]] = []

    holiday = date.fromisoformat(date_str)

    def _step(at: datetime, dish: str, what: str, oven: bool = False, ahead: bool = False) -> None:
        # A step that falls before the day itself is said as such — a
        # brisket that goes in at ten the night before is "the evening
        # before, 10:00 pm", never a clock that wrapped past midnight.
        say = _say_time(at.time())
        if at.date() < holiday:
            say = ("the evening before, " if at.hour >= 17 else "the day before, ") + say
        steps.append({"at": at.isoformat(), "time": at.strftime("%H:%M"), "say": say, "dish": dish,
                      "step": what, "oven": oven, "made_ahead": ahead, "day_before": at.date() < holiday})

    main = next((x for x in dishes if x["role"] == "main"), None)
    oven_free_until = T  # the latest an oven dish that isn't the main can finish
    if main is not None:
        rest = int(main.get("rest_minutes") or 0)
        cook = int(main.get("cook_minutes") or 60)
        prep = int(main.get("minutes") or 30)
        out_at = T - timedelta(minutes=rest)
        in_at = out_at - timedelta(minutes=cook)
        start_at = in_at - timedelta(minutes=prep)
        if main.get("ahead_days"):
            _step(T - timedelta(minutes=DEFAULT_REHEAT_MINUTES), main["name"], f"Warm the {main['name'].lower()} through", oven=bool(main.get("oven")), ahead=True)
            oven_busy.append((T - timedelta(minutes=DEFAULT_REHEAT_MINUTES), T))
        else:
            _step(start_at, main["name"], f"Start on the {main['name'].lower()}")
            if main.get("oven"):
                _step(in_at, main["name"], f"{main['name']} into the oven", oven=True)
                oven_busy.append((in_at, out_at))
            else:
                _step(in_at, main["name"], f"Start cooking the {main['name'].lower()}")
            if rest:
                _step(out_at, main["name"], f"{main['name']} out to rest")
        _step(T, main["name"], "On the table")

    # The rest, longest cook first so the tightest fit is placed first.
    # ASSUMPTION — one oven, two racks: a dish that doesn't fit the rest
    # window can share the oven with the main ONCE; anything further goes
    # in before the main and is kept warm.
    others = [x for x in dishes if x["role"] != "main"]
    others.sort(key=lambda x: -int((x.get("cook_minutes") or 0) + (x.get("minutes") or 0)))
    main_in = next((w[0] for w in oven_busy), None)
    main_out = next((w[1] for w in oven_busy), None)
    before_main_cursor = main_in
    alongside_used = False
    for dish in others:
        ahead = bool(dish.get("ahead_days"))
        oven = bool(dish.get("oven"))
        cook = int(dish.get("cook_minutes") or 0)
        hands = int(dish.get("minutes") or 0)
        lower = dish["name"].lower()
        if ahead:
            # Made ahead: a sweet comes out of the fridge, anything else
            # warms through in the last half hour — beside the resting main
            # or on the second rack; it never competes for the oven hours
            # early. Nothing more precise than that is honest.
            if dish["role"] == "sweet":
                _step(T - timedelta(minutes=15), dish["name"], f"Out of the fridge: the {lower}", ahead=True)
            else:
                _step(T - timedelta(minutes=DEFAULT_REHEAT_MINUTES), dish["name"], f"Warm the {lower} through",
                      oven=oven, ahead=True)
            continue
        if not oven:
            total = (hands + cook) or DEFAULT_DISH_MINUTES
            _step(T - timedelta(minutes=total), dish["name"], f"Start on the {lower}")
            continue
        minutes = cook or DEFAULT_DISH_MINUTES
        what = f"{dish['name']} into the oven"
        rest_window = (T - main_out).total_seconds() / 60 if main_out is not None else None
        note = ""
        if rest_window is None or minutes <= rest_window:
            at = T - timedelta(minutes=minutes)
        elif not alongside_used:
            at = T - timedelta(minutes=minutes)
            # The second rack: it shares the oven from the moment the main
            # is in — said as "with the main" when the main is already in,
            # "the main joins it" when this goes in first.
            note = " (with the main)" if main_in is not None and at >= main_in else " (the main joins it)"
            alongside_used = True
        else:
            at = before_main_cursor - timedelta(minutes=minutes)
            before_main_cursor = at
            note = " (keep it warm)"
        _step(at, dish["name"], what + note, oven=True)
        if hands:
            _step(at - timedelta(minutes=hands), dish["name"], f"Prep the {lower}")

    steps.sort(key=lambda s: (s["at"], s["dish"] != (main or {}).get("name")))
    first = steps[0] if steps else None
    return {
        "date": date_str,
        "holiday_name": row["holiday_name"],
        "on_table_at": on_table.strftime("%H:%M"),
        "on_table_say": _say_time(on_table),
        "on_table_default": not bool(row["on_table_at"]),
        "steps": steps,
        "start_say": first["say"] if first else "",
        "spoken": _spoken(steps, on_table, bool(row["on_table_at"])),
    }


def _spoken(steps: list[dict], on_table: time, said: bool) -> str:
    if not steps:
        return ""
    lead = f"Working back from {_say_time(on_table)}"
    if not said:
        lead += " (your usual dinner time — tell me if the big meal’s different)"
    parts = [f"{s['say']} — {s['step']}" for s in steps]
    return lead + ": " + "; ".join(parts) + "."


# ---------- what to tell the person ----------

def said(result: dict | None) -> str:
    """
    One line, in kitchen-table words, about what building or refreshing
    the menu did that the person should hear: what was left off and why,
    what was swapped in, a clash with their own dinner. "" when there is
    nothing worth saying — a menu that just built is its own news.
    """
    if not result:
        return ""
    bits: list[str] = []

    def _say(line: str) -> None:
        # Two tries at the same dish, or three sides off for one note,
        # are one thing to say, not three.
        if line not in bits:
            bits.append(line)

    for d in result.get("dropped") or []:
        _say(f"Left off the {d['name'].lower()} — {d['restriction']}.")
    replaced = result.get("replaced") or []
    if replaced:
        _say(f"Added {_and([r.lower() for r in replaced])} instead.")
    for c in result.get("conflicts") or []:
        _say(f"Heads up: {c['dish']} has {c['restriction']} in it — your call.")
    if result.get("menu") == "trip" and result.get("note"):
        _say(result["note"])
    elif result.get("note") and not bits:
        _say(result["note"])
    return " ".join(bits)


# ---------- reading it all back ----------

def get_big_meal(date_str: str) -> dict:
    """
    Everything about the big meal on one date, in one read: the answer
    and headcount, the menu (main, sides, sweet) with what each is made
    ahead, the two shops, the prep already on the days before, and the
    day-of timeline — plus `spoken`, the whole thing in a few plain
    sentences for chat. A date that isn't a hosted holiday says so.
    """
    from . import holidays as _holidays

    date.fromisoformat(date_str)
    row = _answer_row(date_str)
    if row is None or row["answer"] != "hosting":
        h = _holidays.holiday_on(date_str)
        name = h["name"] if h else date_str
        return {"date": date_str, "hosting": False,
                "spoken": f"You haven’t said you’re hosting {name}. Say so and I’ll plan the big meal."}
    menu = _row_menu(row)
    entry = menu_entry(date_str)
    name = row["holiday_name"]
    eaters = eaters_for(date_str, int(row["headcount"] or 0))
    out = {
        "date": date_str, "hosting": True, "holiday_name": name, "headcount": int(row["headcount"] or 0),
        "eaters": eaters, "on_table_at": row["on_table_at"] or "", "guest_notes": row["guest_notes"] or "",
        "status": menu.get("status", "none"), "note": menu.get("note") or "",
        "dishes": [], "prep": [], "shop": None, "timeline": None,
    }
    if entry is None:
        if menu.get("note"):
            why = menu["note"]
        elif _weekly_plan.get_plan_id_for_date(date_str) is None:
            why = "There’s no week planned over that day yet — plan the week and I’ll build the menu into it."
        else:
            why = "The big meal isn’t on the plan any more — say hosting again and I’ll build one."
        out["spoken"] = f"You’re hosting {name} for {eaters}. {why}"
        return out
    dishes = dishes_of(entry, menu)
    out["dishes"] = [
        {"name": x["name"], "role": x["role"], "ahead_days": x["ahead_days"],
         "made_ahead_on": (date.fromisoformat(date_str) - timedelta(days=x["ahead_days"])).isoformat() if x["ahead_days"] else None}
        for x in dishes
    ]
    out["prep"] = prep_rows(entry["id"])
    out["shop"] = shop_split()
    if out["shop"] and out["shop"]["date"] != date_str:
        out["shop"] = None
    out["timeline"] = timeline(date_str)
    out["spoken"] = _spoken_summary(out)
    return out


def _spoken_summary(info: dict) -> str:
    name, eaters = info["holiday_name"], info["eaters"]
    dishes = info["dishes"]
    main = next((d["name"] for d in dishes if d["role"] == "main"), None)
    sides = [d["name"] for d in dishes if d["role"] == "side"]
    sweets = [d["name"] for d in dishes if d["role"] == "sweet"]
    bits = [f"{name}, {eaters} at the table."]
    if main:
        line = f"The main is {main}"
        if sides:
            line += ", with " + _and(sides)
        if sweets:
            line += f", and {_and(sweets)} for after"
        bits.append(line + ".")
    ahead = [d for d in dishes if d["ahead_days"]]
    if ahead:
        by_day: dict[str, list[str]] = {}
        for d in ahead:
            by_day.setdefault(d["made_ahead_on"], []).append(d["name"].lower())
        bits.append(" ".join(f"{_weekday(day)}: {_and(names)}." for day, names in sorted(by_day.items())))
    shop = info.get("shop")
    if shop:
        if shop["early"]["date"]:
            bits.append(f"Shop in two trips — the keeps-well things by {_weekday(shop['early']['date'])}, the fresh things {_relative_day(shop['fresh']['date'], date.today())}.")
        else:
            bits.append(f"One shop left, {_relative_day(shop['fresh']['date'], date.today())}.")
    if info.get("note"):
        bits.append(info["note"])
    tl = info.get("timeline")
    if tl and tl["steps"]:
        bits.append(f"On the day, start around {tl['start_say']} for {tl['on_table_say']} on the table — ask me for the timeline when you want it step by step.")
    return " ".join(bits)


def _and(names: list[str]) -> str:
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


# ---------- changing it ----------

def _require_menu(date_str: str) -> tuple[dict, object, dict]:
    date.fromisoformat(date_str)
    row = _answer_row(date_str)
    if row is None or row["answer"] != "hosting":
        raise ValueError(f"You haven’t said you’re hosting on {date_str} — answer the holiday first.")
    entry = menu_entry(date_str)
    if entry is None:
        raise ValueError("There’s no menu on the plan for that day yet — plan the week and I’ll build it in.")
    return dict(row), entry, _row_menu(row)


def _plan_approved(plan_id: int) -> bool:
    return _weekly_plan.get_weekly_plan(plan_id).get("status") == "approved"


def set_big_meal_dish(
    date_str: str,
    name: str,
    role: str = "side",
    ingredients: list[dict] | None = None,
    instructions: list[str] | None = None,
    minutes: int | None = None,
    cook_minutes: int | None = None,
    oven: bool | None = None,
    ahead_days: int | None = None,
    replaces: str | None = None,
) -> dict:
    """
    Add a dish to the big meal, or swap one out ("swap the dessert for a
    pumpkin pie", "add roasted carrots"). `role` is side | sweet | main.
    `replaces` names the dish it takes the place of; a sweet with no
    `replaces` replaces the sweet (there's one), a side is added. A main
    becomes the dinner's recipe (saved by that name if it isn't already),
    the sides staying put. Shopping follows for an approved week.
    """
    row, entry, menu = _require_menu(date_str)
    holiday_name = row["holiday_name"]
    role = (role or "side").strip().lower()
    if role not in ("main",) + ROLES:
        raise ValueError("role has to be main, side or sweet.")
    ingredients = ingredients or []
    plan_id = entry["weekly_plan_id"]
    approved = _plan_approved(plan_id)
    guest_notes = row["guest_notes"] or ""

    if role == "main":
        if not ingredients:
            existing = _recipes.list_recipes()
            match = next((r for r in existing if r["name"].lower() == name.strip().lower()), None)
            if match is None:
                raise ValueError(f"I don’t have a recipe called {name!r} — give me its ingredients and I’ll save it.")
            ingredients = match["ingredients"]
        clashes = dish_conflicts(name, ingredients, guest_notes)
        if clashes:
            raise ValueError(f"{name} clashes with {clashes[0]['restriction']} — pick something else for the table.")
        eaters = eaters_for(date_str, int(row["headcount"] or 0))
        main = _clean_main({
            "name": name, "ingredients": ingredients, "instructions": instructions or [],
            "prep_minutes": minutes, "cook_minutes": cook_minutes, "oven": True if oven is None else oven,
            "ahead_days": ahead_days or 0,
        }, eaters)
        if main is None:
            raise ValueError("A main needs a name and at least one ingredient.")
        _recipe_for_main(main, holiday_name)
        sides = _plates.get_sides(entry["id"])
        _delete_prep(entry["id"], date_str)
        _weekly_plan.clear_plan_slot(plan_id, date_str, "dinner")
        planned = _meal_plans.plan_meal(
            date_str, main["name"], slot="dinner", weekly_plan_id=plan_id,
            add_ingredients_to_grocery_list=False, food_groups=main["food_groups"] or None,
            reasoning=_main_reasoning(holiday_name, eaters),
            derived_from={"holiday": holiday_name, "holiday_menu": True, "constraint": "hosting"},
        )
        entry_id = planned["entry_id"]
        _write_sides(entry_id, sides)
        menu["entry_id"] = entry_id
        menu["main"] = _main_timing(main, _entry_by_id(entry_id))
        menu["status"] = "full" if sides else "main_only"
        menu["note"] = ""
        _save_menu(date_str, menu)
        if approved:
            _buy(entry_id, plan_id)
        spread_prep(date_str)
        return {"date": date_str, "changed": "main", "name": main["name"], **get_big_meal(date_str)}

    dish = clean_dish({
        "name": name, "role": role, "ingredients": ingredients, "instructions": instructions or [],
        "minutes": minutes, "cook_minutes": cook_minutes, "oven": bool(oven),
        "ahead_days": ahead_days,
    }, role, servings=eaters_for(date_str, int(row["headcount"] or 0)))
    if dish is None:
        raise ValueError(f"{name} needs at least one ingredient so I can shop for it.")
    clashes = dish_conflicts(dish["name"], dish["ingredients"], guest_notes)
    if clashes:
        raise ValueError(f"{name} clashes with {clashes[0]['restriction']} — pick something else for the table.")
    current = [dict(s, role=s.get("role") or "side") for s in _plates.get_sides(entry["id"])]
    target = (replaces or "").strip().lower()
    if not target and role == "sweet":
        sweet = next((s for s in current if s["role"] == "sweet"), None)
        target = sweet["name"].lower() if sweet else ""
    replaced = None
    if target:
        for i, s in enumerate(current):
            if s["name"].strip().lower() == target:
                replaced = s["name"]
                current[i] = dish
                break
        if replaced is None:
            current.append(dish)
    else:
        current.append(dish)
    _write_sides(entry["id"], current)
    menu["status"] = "full"
    menu["note"] = ""
    _save_menu(date_str, menu)
    if approved:
        _buy(entry["id"], plan_id)
    spread_prep(date_str)
    return {"date": date_str, "changed": "replaced" if replaced else "added", "name": dish["name"],
            "replaced": replaced, **get_big_meal(date_str)}


def remove_big_meal_dish(date_str: str, name: str) -> dict:
    """Take one side or sweet off the big meal, its shopping with it. The main can be swapped, not removed."""
    row, entry, menu = _require_menu(date_str)
    target = (name or "").strip().lower()
    current = [dict(s, role=s.get("role") or "side") for s in _plates.get_sides(entry["id"])]
    kept = [s for s in current if s["name"].strip().lower() != target]
    if len(kept) == len(current):
        main_name = (entry["recipe_name"] or entry["freeform_meal"] or "").lower()
        if main_name == target:
            raise ValueError(f"{name} is the main — name another main and I’ll swap it, but the table needs one.")
        raise ValueError(f"There’s no {name} on the menu.")
    _write_sides(entry["id"], kept)
    menu["status"] = "full" if kept else "main_only"
    _save_menu(date_str, menu)
    if _plan_approved(entry["weekly_plan_id"]):
        _buy(entry["id"], entry["weekly_plan_id"])
    spread_prep(date_str)
    return {"date": date_str, "removed": name, **get_big_meal(date_str)}


def set_big_meal_prep_day(date_str: str, dish: str, when: str) -> dict:
    """
    When a dish gets made: "make the stuffing the day before". `when` is
    'day_of', 'day_before', 'two_days_before' or an ISO date up to two days
    before. The prep_tasks row moves with it.
    """
    row, entry, menu = _require_menu(date_str)
    target = (dish or "").strip().lower()
    d = date.fromisoformat(date_str)
    words = {"day_of": 0, "day of": 0, "day_before": 1, "day before": 1, "the day before": 1,
             "two_days_before": 2, "two days before": 2}
    key = (when or "").strip().lower()
    if key in words:
        ahead = words[key]
    else:
        try:
            ahead = (d - date.fromisoformat(key)).days
        except ValueError:
            raise ValueError("Tell me the day — on the day, the day before, or two days before.")
    if not 0 <= ahead <= MAX_AHEAD_DAYS:
        raise ValueError(f"I can spread the cooking over the {MAX_AHEAD_DAYS} days before, not further out.")
    main_name = (entry["recipe_name"] or entry["freeform_meal"] or "").strip().lower()
    if target == main_name:
        menu["main"] = dict(menu.get("main") or {}, ahead_days=ahead)
        _save_menu(date_str, menu)
    else:
        current = [dict(s, role=s.get("role") or "side") for s in _plates.get_sides(entry["id"])]
        hit = next((s for s in current if s["name"].strip().lower() == target), None)
        if hit is None:
            raise ValueError(f"There’s no {dish} on the menu.")
        hit["ahead_days"] = ahead
        _write_sides(entry["id"], current)
    spread_prep(date_str)
    return {"date": date_str, "dish": dish, "ahead_days": ahead, **get_big_meal(date_str)}


def propose_big_meal(date_str: str, keep_main: bool = True, proposer=None) -> dict:
    """
    Propose the menu again — "start the sides over". Keeps the main unless
    told otherwise; the sides and sweet are replaced by a fresh proposal,
    shopping and prep following. Degrades the same way build_menu does.
    """
    from . import holidays as _holidays

    row, entry, menu = _require_menu(date_str)
    saved = _holidays.get_holiday_answer(date_str)
    plan_id = entry["weekly_plan_id"]
    approved = _plan_approved(plan_id)
    if keep_main:
        _write_sides(entry["id"], [])
        _delete_prep(entry["id"], date_str)
        existing = _existing_main_summary(entry)
        eaters = eaters_for(date_str, saved["headcount"])
        raw = _propose(proposal_context(saved, existing), proposer)
        dishes, dropped = [], []
        for raw_dish in (raw or {}).get("dishes") or []:
            dish = clean_dish(raw_dish, servings=eaters)
            if dish is None:
                continue
            clashes = dish_conflicts(dish["name"], dish["ingredients"], saved.get("guest_notes") or "")
            if clashes:
                dropped.append({"name": dish["name"], "restriction": clashes[0]["restriction"]})
                continue
            dishes.append(dish)
        _write_sides(entry["id"], dishes)
        menu.update({
            "status": "full" if dishes else "main_only", "dropped": dropped, "eaters": eaters,
            "note": "" if dishes else "I couldn’t put the sides together just now — tell me what you’d like alongside and I’ll add them.",
        })
        _save_menu(date_str, menu)
        if approved:
            _buy(entry["id"], plan_id)
        spread_prep(date_str)
        return {"date": date_str, "proposed": len(dishes), **get_big_meal(date_str)}
    # Start over entirely: clear what's ours, then build as if just answered.
    clear_menu(date_str, row["holiday_name"])
    result = build_menu(saved, proposer=proposer)
    return {"date": date_str, "proposed": result.get("status"), **get_big_meal(date_str)}
