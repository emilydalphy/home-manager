"""
What needs using up — the ONE list the pre-draft step and the draft share.

Loop Board, Work group Inventory (Emily, 2026-10-26): "making sure to use
things before they go bad to avoid food waste… take the mental load off of
the user." Option B, "plan around what's home":

  B1 "Use these up" (the pre-draft step) shows `candidates()` as chips, all
     switched on; a tap switches one off, saved on the week's intake as
     `use_up_off` (save_week_intake — append-only, like every answer).
  B2 the draft plans every switched-on item it can fit, soonest-to-turn in
     the earliest nights, freezer food oldest first, and says so: a tag
     under each dinner ("Uses the spinach and feta") and a count line
     ("Uses 6 of the 8 things going off.").
  B3 whatever could not fit is listed under the days: Freeze it / Add as a
     side / Let it go. Never dropped in silence.

Two kinds of row are candidates:

  going_off — a fridge or pantry row whose date (entered or estimated) is
      today or later and no later than the period's last day. Soonest
      first. Food already PAST its date is not planned into a dinner (a
      date is a food-safety signal, and estimates err early): that is the
      kitchen check's job (A4), not the planner's.
  freezer — a raw freezer row frozen at least FREEZER_AFTER_DAYS ago,
      oldest first (frozen_on, else created_at), at most MAX_FREEZER of
      them. Cooked portions a night off froze are NOT here: those are
      freezer_portions' to plan, a whole night each.

Never a staple (the staples table's names — staples.py promises never to
read or write inventory; this module reads the staples table, not the other
way round) and never a spice (spices.is_spice).

Not re-exported from app/tools/__init__.py as bare names that could collide
— callers use `from app.tools import use_up` / `tools.use_up_candidates`.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import quantities as _quantities

logger = logging.getLogger("home_manager")

# A freezer row is worth planning once it has been in this long. An
# ASSUMPTION, one line to change: two weeks means something frozen from a
# draft's "Couldn't fit these" comes back two or three weeks later.
FREEZER_AFTER_DAYS = 14
# How many freezer rows one week is asked to use. The fresh food is the
# urgent part; the freezer is a nudge.
MAX_FREEZER = 3

_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ---------- what can happen to a thing that didn't fit (B3) ----------
#
# Small word tables, easy to change. Matched as whole words against the
# lowercased item name. "Add as a side" is for herbs and salad greens (the
# mockup's cilantro); everything else is offered "Freeze it" unless it is
# on the does-not-freeze list, in which case only "Let it go" is offered.
SIDE_WORDS = (
    "cilantro", "coriander", "parsley", "basil", "mint", "dill", "chives",
    "scallion", "scallions", "green onion", "green onions", "tarragon",
    "lettuce", "arugula", "rocket", "spinach", "kale", "greens", "spring mix",
    "mixed greens", "salad", "watercress", "cucumber", "cucumbers",
    "tomato", "tomatoes", "cherry tomatoes", "radish", "radishes", "avocado",
    "sprouts",
)
NO_FREEZE_WORDS = (
    "lettuce", "arugula", "rocket", "spring mix", "mixed greens", "salad",
    "watercress", "cucumber", "cucumbers", "radish", "radishes", "sprouts",
    "avocado", "egg", "eggs", "yogurt", "yoghurt", "sour cream",
    "cream cheese", "mayonnaise", "mayo", "watermelon", "melon", "potato",
    "potatoes", "tomato", "tomatoes", "cherry tomatoes",
)


def _has_word(name: str, words) -> bool:
    text = " " + " ".join(re.findall(r"[a-z]+", (name or "").lower())) + " "
    return any(f" {w} " in text for w in words)


def side_ok(name: str) -> bool:
    """Whether "Add as a side" is offered for this item (herbs, greens)."""
    return _has_word(name, SIDE_WORDS)


def freeze_ok(name: str, location: str = "") -> bool:
    """Whether "Freeze it" is offered: not already frozen, and not a thing
    that turns to mush in a freezer."""
    if (location or "").strip().lower() == "freezer":
        return False
    return not _has_word(name, NO_FREEZE_WORDS)


# ---------- the list ----------

# A variety → the food it is, for the dislike/allergy check only.
_FAMILY = {
    "portobello": "mushroom", "cremini": "mushroom", "crimini": "mushroom", "shiitake": "mushroom",
    "chanterelle": "mushroom", "enoki": "mushroom", "porcini": "mushroom", "morel": "mushroom",
    "prawn": "shrimp", "scallop": "shellfish", "crab": "shellfish", "lobster": "shellfish",
    "mussel": "shellfish", "clam": "shellfish", "oyster": "shellfish",
}

def _name_key(name: str) -> str:
    words = re.findall(r"[a-z]+", (name or "").lower())
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def _staple_words(name: str) -> list[str]:
    """A name as the staples list reads it (staples._name_words: lowercase,
    singular), less anything in brackets — "Milk (1 L)" is milk."""
    from . import staples as _staples
    return _staples._name_words(re.sub(r"\([^)]*\)", " ", name or ""))


def _staple_keys(conn) -> list[list[str]]:
    try:
        rows = conn.execute("SELECT item FROM staples WHERE household_id = ?", (household_id(),)).fetchall()
    except Exception:
        return []
    return [w for w in (_staple_words(r["item"]) for r in rows if r["item"]) if w]


def is_staple(name: str, staples: list[list[str]]) -> bool:
    """Whether a kitchen row is one of the household's staples: the same
    words, or every word of the staple with the same last word (the thing
    itself) — "Whole milk" is the "Milk" staple, "Coffee beans" the
    "Coffee bean" one. Verify finding 7 (2026-10-27): exact names let
    "Whole milk" through beside a "Milk" staple."""
    words = _staple_words(name)
    if not words:
        return False
    for staple in staples:
        if words == staple:
            return True
        if words[-1] == staple[-1] and set(staple) <= set(words):
            return True
    return False


def _today(conn=None) -> date:
    from .cooker import household_today
    return household_today(conn=conn)


def _period_first(period_start: str | None, today: date) -> date:
    try:
        start = date.fromisoformat(period_start) if period_start else today
    except ValueError:
        start = today
    return max(start, today)


def _period_end(period_start: str | None, day_count: int, today: date) -> date:
    try:
        start = date.fromisoformat(period_start) if period_start else today
    except ValueError:
        start = today
    # Counted from the period's own first day, not from today: a week
    # starting Monday opened on Tuesday still ends on Sunday (final check,
    # 2026-10-27 — counting from today ran it into next week's Monday).
    return max(start + timedelta(days=max(1, int(day_count or 7)) - 1), max(start, today))


def claimed_elsewhere(period_start: str | None = None, day_count: int = 7, conn=None) -> set[int]:
    """
    Inventory rows a dish already uses on ANOTHER stretch of days: a
    planned, not-yet-cooked entry of a live plan (draft or approved), dated
    today or later, whose "Uses the…" tag still
    stands (live_uses). The rule (re-verify, 2026-10-27, where this week's
    draft planned the thighs and beef next week's draft already had):
    food a live plan has put on a dish on another day is that plan's, so it
    is not offered to this period and not shown to its generator. Entries
    of the plan this draft replaces (the one holding the period's first
    day) that fall inside this period don't count; every other live plan's
    dishes do, even on a day two periods share. Cooking the dish or
    retiring its plan frees the row.
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        today = _today(conn)
        first = _period_first(period_start, today).isoformat()
        end = _period_end(period_start, day_count, today).isoformat()
        rows = conn.execute(
            """
            SELECT mpe.date, mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal,
                   COALESCE(NULLIF(wp.content_start_date, ''), wp.week_start_date) AS plan_start,
                   wp.day_count AS plan_days
            FROM meal_plan_entries mpe
            JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id
            LEFT JOIN recipes r ON r.id = mpe.recipe_id
            WHERE mpe.household_id = ? AND wp.status IN ('draft', 'approved')
              AND mpe.slot_state = 'planned' AND COALESCE(mpe.cooked_status, '') != 'done'
              AND mpe.date >= ?
              AND mpe.derived_from_json LIKE '%use_up%'
            """,
            (household_id(), today.isoformat()),
        ).fetchall()
    finally:
        if own:
            conn.close()
    out: set[int] = set()
    for r in rows:
        # The plan this draft replaces is the one holding the period's
        # first day; its dishes inside the period are not "elsewhere".
        # Any OTHER plan's dishes are, whatever their date — two periods
        # can share a day (final check, 2026-10-27: next week's Monday
        # thighs sat on this week's last day and were offered again).
        try:
            p_start = date.fromisoformat((r["plan_start"] or "")[:10])
            p_end = p_start + timedelta(days=(int(r["plan_days"] or 0) or 7) - 1)
            replaced = p_start.isoformat() <= first <= p_end.isoformat()
        except ValueError:
            replaced = False
        if replaced and first <= r["date"] <= end:
            continue
        for u in live_uses(r["derived_from_json"], r["meal"]):
            try:
                out.add(int(u.get("id")))
            except (TypeError, ValueError):
                continue
    return out


def candidates(period_start: str | None = None, day_count: int = 7, conn=None) -> list[dict]:
    """
    Everything the week should use up, going-off rows soonest first, then
    freezer rows oldest first. Each item:

      {"id": inventory_items.id, "item": "Baby spinach", "quantity": "1 bag",
       "category": "produce", "location": "fridge" | "pantry" | "freezer",
       "kind": "going_off" | "freezer",
       "turns_on": "2026-10-29" | None,     # the row's date (going_off)
       "use_by": "2026-10-28" | None,       # plan it on or before this day
       "turns_day": "Thu" | None,
       "frozen_on": "2026-10-01" | None,    # freezer rows
       "label": "Baby spinach · Thu" | "Ground beef · freezer",
       "freeze_ok": bool, "side_ok": bool}

    `period_start`/`day_count` are the period about to be drafted: a row
    going off after its last day is not this week's to use, and a row that
    turns before the period's FIRST day can't be used in it at all (verify
    finding 2, 2026-10-27: planning next week offered spinach turning on the
    Friday before it, and the model put it in Monday's breakfast). Its
    use_by (the day before it turns) has to be on or after the first day.

    Never an item a household rule rules out (rule_clash: an allergy or a
    dislike) — it can never be planned, so it is not offered, not shown on
    the pre-draft step and not counted (verify finding 5).
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        today = _today(conn)
        end = _period_end(period_start, day_count, today)
        first = _period_first(period_start, today)
        staples = _staple_keys(conn)
        from . import spices as _spices
        from . import tonight as _tonight

        rows = conn.execute(
            "SELECT id, item, quantity, category, location, expiration_date, created_at, frozen_on, source "
            "FROM inventory_items WHERE household_id = ? ORDER BY id",
            (household_id(),),
        ).fetchall()
        taken = claimed_elsewhere(period_start, day_count, conn=conn)
        going, frozen = [], []
        for r in rows:
            row = dict(r)
            if row["id"] in taken:
                continue
            name = (row["item"] or "").strip()
            if not name or is_staple(name, staples) or _spices.is_spice(name):
                continue
            if (row["quantity"] or "").strip() in ("0", "0.0"):
                continue
            where = _quantities._display_location(row).strip().lower()
            if where == "freezer":
                if row.get("source") == _tonight.NIGHT_OFF_FROZEN_SOURCE:
                    continue  # freezer_portions plans cooked portions
                frozen_on = (row.get("frozen_on") or row.get("created_at") or "")[:10]
                try:
                    age = (today - date.fromisoformat(frozen_on)).days
                except ValueError:
                    continue
                if age < FREEZER_AFTER_DAYS:
                    continue
                frozen.append({
                    "id": row["id"], "item": name, "quantity": row["quantity"] or "",
                    "category": row["category"] or "other", "location": "freezer",
                    "kind": "freezer", "turns_on": None, "use_by": None, "turns_day": None,
                    "frozen_on": frozen_on, "label": f"{name} · freezer",
                    "freeze_ok": False, "side_ok": False,
                })
                continue
            turns = (row.get("expiration_date") or "")[:10]
            try:
                turns_d = date.fromisoformat(turns)
            except ValueError:
                continue
            if turns_d < today or turns_d > end or turns_d - timedelta(days=1) < first:
                continue
            going.append({
                "id": row["id"], "item": name, "quantity": row["quantity"] or "",
                "category": row["category"] or "other", "location": where,
                "kind": "going_off", "turns_on": turns,
                "use_by": (turns_d - timedelta(days=1)).isoformat(),
                "turns_day": _DOW[turns_d.weekday()], "frozen_on": None,
                "label": f"{name} · {_DOW[turns_d.weekday()]}",
                "freeze_ok": freeze_ok(name, where), "side_ok": side_ok(name),
            })
        going.sort(key=lambda c: (c["turns_on"], c["id"]))
        frozen.sort(key=lambda c: (c["frozen_on"], c["id"]))
        rules = _rule_context()
        going = [c for c in going if not rule_clash(c, *rules)]
        frozen = [c for c in frozen if not rule_clash(c, *rules)]
        return going + frozen[:MAX_FREEZER]
    finally:
        if own:
            conn.close()


def selected(intake: dict | None, period_start: str | None = None, day_count: int = 7, conn=None) -> list[dict]:
    """candidates() less what the household switched off for this week
    (intake["use_up_off"], written by save_week_intake). What B2's
    generation plans."""
    off = set()
    for i in (intake or {}).get("use_up_off") or []:
        try:
            off.add(int(i))
        except (TypeError, ValueError):
            continue
    return [c for c in candidates(period_start, day_count, conn=conn) if c["id"] not in off]


def rule_clash(c: dict, dislikes: list[str] | None = None, avoidances: list[dict] | None = None) -> str | None:
    """
    "dislike" or "allergy" when using this item would break a household
    rule, else None. Household rules win (B2): measured on a real draft
    2026-10-26, a disliked cabbage in the list came back as "Cabbage
    Slaw" — telling the model the dislike was not enough once the same
    prompt said "plan every one". So an item like that is never handed to
    the generator; it goes straight to "Couldn't fit these", where it can
    be frozen or let go. Fails open on a lookup error (the item is
    offered; the allergen gate still checks every dish).
    """
    from . import allergen_gate as _allergen_gate
    from . import coordination as _coordination
    from . import everyday_snacks as _everyday_snacks

    name = c.get("item") or ""
    # The family a variety belongs to, so "Portobellos" answers to a
    # mushrooms dislike (verify finding 5). Small and easy to grow.
    words = set(re.findall(r"[a-z]+", name.lower()))
    family = sorted({fam for w in words for key, fam in _FAMILY.items() if w == key or w == key + "s"})
    said = " ".join([name] + family)
    try:
        if dislikes is None:
            from .memory import get_household_memory
            dislikes = (get_household_memory() or {}).get("dislikes") or []
        if _allergen_gate.hard_clashes(said, [{"item": said}], avoidances=avoidances):
            return "allergy"
        if _everyday_snacks.disliked(said, dislikes):
            return "dislike"
        # The app's own matcher (coordination.check_meal_conflicts — the
        # one the draft's banner uses), which reads "Mushroom caps" as a
        # mushrooms dislike where the plain word match does not.
        clashes = _coordination.check_meal_conflicts(said, [{"item": said}])
        if any(x.get("severity") == "hard" for x in clashes):
            return "allergy"
        if any(x.get("source") == "dislike" for x in clashes):
            return "dislike"
    except Exception:
        logger.exception("Use-up rule check failed for %r", name)
    return None


def _rule_context() -> tuple[list[str] | None, list[dict] | None]:
    """The household's dislikes and hard avoidances, read once for a list."""
    try:
        from . import allergen_gate as _allergen_gate
        from .memory import get_household_memory
        return ((get_household_memory() or {}).get("dislikes") or [], _allergen_gate.hard_avoidances())
    except Exception:
        logger.exception("Use-up rule lookup failed")
        return (None, None)


def for_prompt(offered: list[dict]) -> list[dict]:
    """What the generator is told to plan: the offered items no household
    rule holds back, as prompt_item shapes them."""
    from . import allergen_gate as _allergen_gate
    from .memory import get_household_memory

    try:
        dislikes = (get_household_memory() or {}).get("dislikes") or []
        avoidances = _allergen_gate.hard_avoidances()
    except Exception:
        logger.exception("Use-up rule lookup failed; offering the whole list")
        return [prompt_item(c) for c in offered]
    return [prompt_item(c) for c in offered if not rule_clash(c, dislikes, avoidances)]


def filter_stock(rows: list[dict], intake: dict | None, period_start: str | None,
                 day_count: int = 7) -> list[dict]:
    """
    An inventory list as generation may see it for this plan: never a row
    the household switched off (intake["use_up_off"]), and never a fridge
    or pantry row whose date falls before the period's first day — it will
    be gone or past it before any dish here is eaten (verify findings 2
    and 3, 2026-10-27: a switched-off zucchini became "Zucchini and
    Cheddar Beef Tacos", and spinach turning Friday went into the next
    Monday's breakfast, both from these lists). Freezer rows keep.

    Never a row a household rule rules out either (rule_clash): a real
    next-week draft (2026-10-27) made "Portobello and Chicken Thigh Fajita
    Skillet" for a mushrooms-disliking house out of current_inventory. The
    allergen gate still checks every dish whatever this list says.
    """
    off = set()
    for i in (intake or {}).get("use_up_off") or []:
        try:
            off.add(int(i))
        except (TypeError, ValueError):
            continue
    try:
        first = _period_first(period_start, _today()).isoformat()
    except Exception:
        first = ""
    try:
        off |= claimed_elsewhere(period_start, day_count)
    except Exception:
        logger.exception("Use-up claimed-elsewhere lookup failed")
    rules = _rule_context()
    out = []
    for r in rows or []:
        if r.get("id") in off:
            continue
        if rule_clash(r, *rules):
            continue
        where = (r.get("location") or "").strip().lower()
        if where != "freezer" and first and (r.get("expiration_date") or "")[:10]:
            # Turning ON the first day counts as gone, the same rule as a
            # candidate's use_by (the day before it turns).
            if r["expiration_date"][:10] <= first:
                continue
        out.append(r)
    return out


def prompt_item(c: dict) -> dict:
    """One candidate as the generation prompt sees it — the id it reports
    back, and the date it has to be eaten by."""
    out = {"id": c["id"], "item": c["item"], "kind": c["kind"]}
    if c.get("quantity"):
        out["quantity"] = c["quantity"]
    if c.get("use_by"):
        out["use_by"] = c["use_by"]
    if c.get("frozen_on"):
        out["frozen_on"] = c["frozen_on"]
    return out


# ---------- names ----------

_CONTAINERS = ("bag", "bags", "pack", "packs", "package", "box", "tub", "block", "jar", "bunch",
               "head", "carton", "container", "can", "loaf", "bottle", "punnet", "clamshell")
_HALF_OF_RE = re.compile(r"^(?:half|a half|some|leftover|the rest of)\s+(?:an?\s+)?(?:" + "|".join(_CONTAINERS) + r")\s+of\s+")
_A_OF_RE = re.compile(r"^(?:an?|one|\d+)\s+(?:" + "|".join(_CONTAINERS) + r")\s+of\s+")
_HALF_A_RE = re.compile(r"^half\s+(?:an?\s+)")
_LEAD_RE = re.compile(r"^(?:baby|fresh|leftover|opened|some)\s+")


def short_name(item: str) -> str:
    """How a tag says it: "Baby spinach" → "spinach", "Half a bag of rice" →
    "rice", "Half an onion" → "half onion" (the mockup's own words)."""
    name = re.sub(r"\([^)]*\)", "", (item or "").lower()).strip()
    name = " ".join(name.split())
    name = _HALF_OF_RE.sub("", name)
    name = _A_OF_RE.sub("", name)
    name = _HALF_A_RE.sub("half ", name)
    while _LEAD_RE.match(name):
        name = _LEAD_RE.sub("", name, count=1)
    return name.strip() or (item or "").strip().lower()


def _food(item: str) -> str:
    """The words a dish has to carry for it to be using this item."""
    name = short_name(item)
    return re.sub(r"^half\s+", "", name).strip()


def tag_text(names: list[str]) -> str:
    """ "Uses the spinach and feta" / "Uses the peppers, eggs, half onion"
    (Option B mockup, verbatim shapes)."""
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        body = names[0]
    elif len(names) == 2:
        body = f"{names[0]} and {names[1]}"
    else:
        body = ", ".join(names)
    return f"Uses the {body}"


def _dish_key(meal: str | None) -> str:
    return " ".join((meal or "").lower().split())


def live_uses(derived, meal: str | None) -> list[dict]:
    """
    The use-up items this dish genuinely uses NOW: derived_from.use_up
    entries stamped with the dish they were checked against, kept only
    while the slot still holds that dish. A swap, a re-pick, a chat
    replace — any path that puts a different dish on the slot and copies
    derived_from forward — leaves the old stamp behind, and the stamp no
    longer matches (verify finding 1, 2026-10-27: swapped-in Beef Tacos
    still said "Uses the spinach and feta"). Telling is not preventing:
    no path has to remember to clear it.
    """
    if isinstance(derived, str):
        try:
            derived = json.loads(derived or "{}")
        except (TypeError, ValueError):
            return []
    if not isinstance(derived, dict):
        return []
    now = _dish_key(meal)
    return [u for u in (derived.get(USE_UP_KEY) or [])
            if isinstance(u, dict) and _dish_key(u.get("dish")) == now and now]


def tag_for(derived, meal: str | None = None) -> str:
    """The celadon line under a dish, from what it uses now (live_uses)."""
    return tag_text([short_name(u.get("item") or "") for u in live_uses(derived, meal)])


def must_use_for(derived, meal: str | None) -> list[str]:
    """What the recipe pass must write this dish around, from use-up: the
    foods it was checked to use, while it is still that dish."""
    out = []
    for u in live_uses(derived, meal):
        food = _food(u.get("item") or "")
        if food and food not in out:
            out.append(food)
    return out


def count_line(used: int, total: int) -> str:
    """The draft's subtitle (Option B mockup): "Uses 6 of the 8 things
    going off." / "Uses all 8 things going off." """
    if total <= 0:
        return ""
    if used >= total:
        return "Uses the one thing going off." if total == 1 else f"Uses all {total} things going off."
    if used == 0:
        return "Couldn’t fit the one thing going off." if total == 1 else f"Couldn’t fit the {total} things going off."
    return f"Uses {used} of the {total} things going off."


# ---------- the draft: what each dish uses (B2) ----------

# derived_from key on a meal_plan_entries row: [{"id", "item"}] — written
# by reconcile and by "Add as a side", read by tag_for. One name, here.
USE_UP_KEY = "use_up"


def _derived(raw) -> dict:
    try:
        value = json.loads(raw or "{}") if not isinstance(raw, dict) else raw
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _plan_entries(conn, plan_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.recipe_id, mpe.freeform_meal,
               mpe.derived_from_json, mpe.sides_json, mpe.cooked_status,
               COALESCE(r.name, mpe.freeform_meal) AS meal,
               r.dish_note, r.main_protein, r.ingredients_json
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ?
        ORDER BY mpe.date, CASE mpe.slot WHEN 'breakfast' THEN 0 WHEN 'lunch' THEN 1
                 WHEN 'dinner' THEN 2 ELSE 3 END, mpe.id
        """,
        (plan_id, household_id()),
    ).fetchall()
    return [dict(r) for r in rows]


def _is_cook(entry: dict, reheats: set[int]) -> bool:
    """A planned dish that is cooked that day — never a reheat night, a
    frozen portion, takeout, or an empty slot."""
    from . import leftovers as _leftovers
    from . import weekly_plan as _weekly_plan

    if entry["slot_state"] != "planned" or not (entry.get("meal") or "").strip():
        return False
    if entry["id"] in reheats:
        return False
    derived = _derived(entry.get("derived_from_json"))
    if derived.get("freezer_portion") or derived.get(_leftovers.FROM_FREEZER_KEY) or derived.get("links_to"):
        return False
    if not entry.get("recipe_id") and _weekly_plan.written_in_not_a_cook(entry.get("freeform_meal")):
        return False
    return True


def _dish_words(entry: dict) -> str:
    """What the dish itself says it is — name, note, protein, ingredients,
    sides. NOT derived_from.inventory / use_up_ids: those are the model's
    claim, and the claim is what this checks."""
    parts = [entry.get("meal") or "", entry.get("dish_note") or "", entry.get("main_protein") or ""]
    try:
        ingredients = json.loads(entry.get("ingredients_json") or "[]") or []
    except (TypeError, ValueError):
        ingredients = []
    parts += [str(i.get("item") or "") for i in ingredients if isinstance(i, dict)]
    try:
        sides = json.loads(entry.get("sides_json") or "[]") or []
    except (TypeError, ValueError):
        sides = []
    for side in sides if isinstance(sides, list) else []:
        if not isinstance(side, dict):
            continue
        parts.append(str(side.get("name") or ""))
        parts += [str(i.get("item") or "") for i in (side.get("ingredients") or []) if isinstance(i, dict)]
    return " ".join(parts)


# Words that describe WHICH one, not what it is: "Red peppers" are used
# by a "Pepper and Onion Frittata". Tried only after the full name fails.
_MODIFIERS = {"red", "green", "yellow", "orange", "purple", "white", "large", "small", "mini",
              "organic", "ripe", "english", "roma", "cherry", "grape", "plum", "medium"}


def _matches(words: str, item: str) -> bool:
    from . import typed_requests as _typed_requests
    food = _food(item)
    if _typed_requests.dish_carries(words, food):
        return True
    core = " ".join(w for w in food.split() if w not in _MODIFIERS)
    return bool(core) and core != food and _typed_requests.dish_carries(words, core)


def _carries(entry: dict, item: str) -> bool:
    return _matches(_dish_words(entry), item)


def _claims(entry: dict) -> tuple[set[int], list[str]]:
    derived = _derived(entry.get("derived_from_json"))
    ids = set()
    for i in derived.get("use_up_ids") or []:
        try:
            ids.add(int(i))
        except (TypeError, ValueError):
            continue
    names = [str(n) for n in (derived.get("inventory") or []) if isinstance(n, (str, int))]
    return ids, names


def _claimed(entry: dict, cand: dict) -> bool:
    ids, names = _claims(entry)
    if cand["id"] in ids:
        return True
    return any(_matches(n, cand["item"]) for n in names)


def _fits_date(entry: dict, cand: dict, today: str) -> bool:
    if (entry.get("date") or "") < today:
        return False
    use_by = cand.get("use_by")
    return not use_by or (entry.get("date") or "") <= use_by


def _snapshot(c: dict) -> dict:
    keep = ("id", "item", "quantity", "category", "kind", "turns_on", "use_by", "frozen_on",
            "freeze_ok", "side_ok")
    return {k: c.get(k) for k in keep}


def reconcile(plan_id: int, offered: list[dict], model_report: dict | None = None,
              intake: dict | None = None, memory: dict | None = None) -> dict:
    """
    After every pass that can change a dish: which offered item each dish
    genuinely uses, written onto the dish (derived_from.use_up, which is
    the tag; derived_from.must_use, so the recipe pass writes the dish
    around it), and the plan's record (weekly_plans.use_up_json).

    Telling is not preventing (CLAUDE.md): the model's claims are checked,
    never counted. An item counts as used on a dish only when the dish's
    own words carry it (name, dish_note, ingredients, sides — never the
    claim lists), the dish is a cook (not a reheat night), and, for food
    going off, it is eaten on or before its use_by. Claimed dishes are
    preferred; an unclaimed dish that carries it (a saved spinach pasta
    on Monday) counts too. Anything else is couldn't-fit, with why.

    Returns {"used": [{id, item, entry_id}], "missed": {id: reason}}.
    Never raises: a draft that generated is never lost to this.
    """
    out = {"used": [], "missed": {}}
    try:
        from . import leftovers as _leftovers

        conn = get_conn()
        try:
            today = _today(conn).isoformat()
            entries = _plan_entries(conn, plan_id)
        finally:
            conn.close()
        reheats = set((_leftovers.plan_leftover_chains(plan_id).get("leftovers") or {}).keys())
        cooks = [e for e in entries if _is_cook(e, reheats)]
        model_missed = {}
        for row in (model_report or {}).get("couldnt_fit") or []:
            try:
                model_missed[int(row.get("id"))] = str(row.get("reason") or "")
            except (TypeError, ValueError, AttributeError):
                continue
        uses: dict[int, list[dict]] = {}
        from . import allergen_gate as _allergen_gate
        from .memory import get_household_memory
        dislikes = (get_household_memory() or {}).get("dislikes") or []
        avoidances = _allergen_gate.hard_avoidances()
        for cand in offered:
            held = rule_clash(cand, dislikes, avoidances)
            if held:
                out["missed"][cand["id"]] = "household_rule: " + held
                continue
            fits = [e for e in cooks if _fits_date(e, cand, today) and _carries(e, cand["item"])]
            claimed = [e for e in fits if _claimed(e, cand)]
            pick = (claimed or fits or [None])[0]
            if pick is None:
                if any(_claimed(e, cand) for e in cooks):
                    reason = "too_late" if any(_claimed(e, cand) and _carries(e, cand["item"]) for e in cooks) \
                        else "not_in_the_dish"
                else:
                    reason = "no_dish"
                out["missed"][cand["id"]] = reason
                if cand["id"] in model_missed and model_missed[cand["id"]]:
                    out["missed"][cand["id"]] = reason + ": " + model_missed[cand["id"]]
                continue
            uses.setdefault(pick["id"], []).append({"id": cand["id"], "item": cand["item"], "dish": pick.get("meal") or ""})
            out["used"].append({"id": cand["id"], "item": cand["item"], "entry_id": pick["id"]})
        conn = get_conn()
        try:
            for e in entries:
                derived = _derived(e.get("derived_from_json"))
                mine = uses.get(e["id"]) or []
                if not mine and USE_UP_KEY not in derived:
                    continue
                if mine:
                    # Not copied into must_use: the recipe pass reads these
                    # through must_use_for, which drops them once the slot
                    # holds a different dish (verify finding 1).
                    derived[USE_UP_KEY] = mine
                else:
                    derived.pop(USE_UP_KEY, None)
                conn.execute(
                    "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                    (json.dumps(derived), e["id"], household_id()),
                )
            record = {
                "offered": [_snapshot(c) for c in offered],
                "missed": {str(k): v for k, v in out["missed"].items()},
                "resolved": {},
                "past_date": _past_date_dishes(cooks),
            }
            conn.execute(
                "UPDATE weekly_plans SET use_up_json = ? WHERE id = ? AND household_id = ?",
                (json.dumps(record), plan_id, household_id()),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("Plan %s uses %d of %d use-up item(s); couldn't fit %s",
                    plan_id, len(out["used"]), len(offered), out["missed"])
        out["freezer_swaps"] = _freezer_oldest_first(plan_id, offered, intake, memory)
    except Exception:
        logger.exception("Use-up reconcile failed for plan %s; the week stands as generated", plan_id)
    return out


def _past_date_dishes(cooks: list[dict]) -> list[dict]:
    """
    Dishes that name kitchen food which will be past its date on the dish's
    own day — the model drew on a row it was told about (its claim lists
    name it) and the dish carries it, but the row turns on or before that
    day (verify finding 2). Not trusted, not hidden: the draft says so
    under the days, so the household buys fresh or swaps it. Each:
    {"entry_id", "item", "dish", "date", "slot", "turns_on"}.
    """
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, item, location, category, expiration_date FROM inventory_items WHERE household_id = ?",
            (household_id(),),
        ).fetchall()]
    finally:
        conn.close()
    dated = [r for r in rows if (r.get("expiration_date") or "")[:10]
             and _quantities._display_location(r).strip().lower() != "freezer"]
    out = []
    for e in cooks:
        ids, names = _claims(e)
        for r in dated:
            named = r["id"] in ids or any(_matches(n, r["item"]) for n in names)
            if not named or not _carries(e, r["item"]):
                continue
            if r["expiration_date"][:10] <= (e.get("date") or ""):
                out.append({"entry_id": e["id"], "item": r["item"], "dish": e.get("meal") or "",
                            "date": e["date"], "slot": e["slot"], "turns_on": r["expiration_date"][:10]})
    return out


# Most nights-swaps one draft may make to put freezer food in age order.
MAX_FREEZER_SWAPS = 2


def _cap_rule_refusal(plan_id: int, intake: dict | None, memory: dict | None, a: dict, b: dict) -> str | None:
    """Why the time-cap pass's rules forbid trading these two dinners'
    nights, or None. Fails closed: a rule that can't be read refuses."""
    from . import cap_enforce as _cap_enforce
    try:
        if memory is None:
            from .memory import get_household_memory
            memory = get_household_memory() or {}
        by_date = {n["date"]: n for n in _cap_enforce.nights(plan_id, intake, memory)}
        na, nb = by_date.get(a["date"]), by_date.get(b["date"])
        if not na or not nb:
            return "a night isn't one the cap pass knows"
        if not (na["movable"] and nb["movable"]):
            return "the cap pass would not move one of these dinners"
        for dish, new_night in ((na, nb), (nb, na)):
            cap = new_night["cap"]
            if cap and dish["minutes"] and dish["minutes"] > cap:
                return f"{dish['meal']} is {dish['minutes']} min against {new_night['date']}'s {cap}-minute cap"
        offends = (_cap_enforce.would_offend(nb["meal"], a["date"])
                   or _cap_enforce.would_offend(na["meal"], b["date"]))
        if offends:
            return offends
    except Exception:
        logger.exception("Freezer-order rule check failed for plan %s", plan_id)
        return "the rules couldn't be read"
    return None


def _freezer_oldest_first(plan_id: int, offered: list[dict], intake: dict | None,
                          memory: dict | None = None) -> list[tuple[str, str]]:
    """
    Freezer food is eaten oldest first (B2), in the NIGHTS, not just in the
    list the model was handed (verify finding 4: 90-day thighs landed after
    30-day beef in both real runs). When two cooked dinners use freezer
    items in the wrong order, their nights are traded with the existing
    swap_dinner_nights — which already refuses a cooked dinner, a night
    nobody is home and a leftover chain that would run backwards — and
    only when, on top of that, neither night carries a household answer
    (any night tag: rush, guests, unrushed…), neither dish was placed by
    their own words or a binding constraint, and any going-off food either
    dish uses is still good on its new night. Anything else: the order
    stands as generated. Returns the (date_a, date_b) pairs traded.
    """
    done: list[tuple[str, str]] = []
    try:
        from . import weekly_plan as _weekly_plan

        frozen_on = {c["id"]: c.get("frozen_on") or "" for c in offered if c.get("kind") == "freezer"}
        use_by = {c["id"]: c.get("use_by") for c in offered if c.get("kind") == "going_off"}
        tags = (intake or {}).get("night_tags") or {}
        for _ in range(MAX_FREEZER_SWAPS):
            conn = get_conn()
            try:
                entries = _plan_entries(conn, plan_id)
            finally:
                conn.close()
            dinners = []
            for e in entries:
                if e["slot"] != "dinner" or e["slot_state"] != "planned":
                    continue
                uses = live_uses(e.get("derived_from_json"), e.get("meal"))
                ages = [frozen_on[u["id"]] for u in uses if u.get("id") in frozen_on and frozen_on[u["id"]]]
                if ages:
                    dinners.append((e, min(ages), uses))
            dinners.sort(key=lambda d: d[0]["date"])
            pair = None
            for i in range(len(dinners)):
                for j in range(i + 1, len(dinners)):
                    if dinners[i][1] > dinners[j][1]:
                        pair = (dinners[i], dinners[j])
                        break
                if pair:
                    break
            if pair is None:
                break
            (a, _, a_uses), (b, _, b_uses) = pair

            def safe(entry, uses, new_date):
                if tags.get(entry["date"]) or tags.get(new_date):
                    return False
                d = _derived(entry.get("derived_from_json"))
                if d.get("freeform") or d.get("constraint") or d.get("links_to"):
                    return False
                return all(not use_by.get(u.get("id")) or new_date <= use_by[u["id"]] for u in uses)

            if not (safe(a, a_uses, b["date"]) and safe(b, b_uses, a["date"])):
                break
            # The rules the time-cap pass (cap_enforce.rearrange) holds a
            # trade to, asked the same way, because this trade runs AFTER it
            # and nothing re-checks (re-verify, 2026-10-27: a 120-minute
            # braise traded onto a 30-minute weeknight). Each dish within
            # its NEW night's cap, both nights movable by that pass's own
            # reading, and nobody at either table has said no to the dish.
            why = _cap_rule_refusal(plan_id, intake, memory, a, b)
            if why:
                logger.info("Plan %s: freezer food stays out of age order (%s / %s): %s",
                            plan_id, a["date"], b["date"], why)
                break
            try:
                result = _weekly_plan.swap_dinner_nights(plan_id, a["date"], b["date"])
            except Exception:
                logger.exception("Freezer-order swap of %s and %s failed on plan %s", a["date"], b["date"], plan_id)
                break
            if result.get("status") != "swapped":
                logger.info("Plan %s: freezer food stays out of age order (%s / %s): %s",
                            plan_id, a["date"], b["date"], result.get("message") or result.get("status"))
                break
            done.append((a["date"], b["date"]))
            logger.info("Plan %s: traded dinners %s and %s so the older freezer food is eaten first",
                        plan_id, a["date"], b["date"])
    except Exception:
        logger.exception("Freezer-order pass failed for plan %s; the order stands", plan_id)
    return done


def past_date_line(row: dict) -> str:
    """ "The spinach turns before Monday's breakfast." """
    try:
        weekday = date.fromisoformat(row["date"]).strftime("%A")
    except (KeyError, ValueError):
        return ""
    return f"The {short_name(row.get('item') or '')} turns before {weekday}’s {row.get('slot') or 'dinner'}."


# ---------- the draft's record, read back ----------

def _record(conn, plan_id: int) -> dict:
    row = conn.execute(
        "SELECT use_up_json FROM weekly_plans WHERE id = ? AND household_id = ?",
        (plan_id, household_id()),
    ).fetchone()
    if row is None:
        raise ValueError(f"No weekly plan {plan_id} in this household.")
    value = _derived(row["use_up_json"])
    value.setdefault("offered", [])
    value.setdefault("missed", {})
    value.setdefault("resolved", {})
    return value


def _save_record(conn, plan_id: int, record: dict) -> None:
    conn.execute(
        "UPDATE weekly_plans SET use_up_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(record), plan_id, household_id()),
    )


def _used_ids(entries: list[dict]) -> set[int]:
    used = set()
    for e in entries:
        if e["slot_state"] != "planned":
            continue
        for u in live_uses(e.get("derived_from_json"), e.get("meal")):
            try:
                used.add(int(u.get("id")))
            except (TypeError, ValueError, AttributeError):
                continue
    return used


def primary_action(c: dict) -> str | None:
    """The one pill a couldn't-fit row offers: "side" for herbs and greens
    (the mockup's cilantro), else "freeze" for things that freeze, else
    none (only "Let it go")."""
    if c.get("kind") == "freezer":
        return None
    if c.get("side_ok"):
        return "side"
    if c.get("freeze_ok"):
        return "freeze"
    return None


def plan_summary(plan_id: int) -> dict | None:
    """
    What the draft shows (B2/B3), read from the plan as it stands now —
    so a swapped dish loses its tag and its item comes back to "Couldn't
    fit these" rather than vanishing:

      {"total": 8, "used": 6, "line": "Uses 6 of the 8 things going off.",
       "couldnt_fit": [{"id", "item", "quantity", "kind", "primary": "freeze"|"side"|None}]}

    None for a plan drafted before this, or with nothing to use up.
    """
    conn = get_conn()
    try:
        record = _record(conn, plan_id)
        offered = record["offered"]
        if not offered and not record.get("past_date"):
            return None
        entries = _plan_entries(conn, plan_id)
        live = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, location, category FROM inventory_items WHERE household_id = ?", (household_id(),),
        ).fetchall()}
    finally:
        conn.close()
    used = _used_ids(entries)
    resolved = record["resolved"]
    rows = []
    for c in offered:
        if c["id"] in used:
            continue
        if (resolved.get(str(c["id"])) or {}).get("action") in ("frozen", "let_go"):
            continue
        row = live.get(c["id"])
        if row is None:
            continue  # eaten, thrown out — nothing left to decide
        if c.get("kind") == "going_off" and _quantities._display_location(row).strip().lower() == "freezer":
            continue  # frozen some other way since
        rows.append({
            "id": c["id"], "item": c["item"], "quantity": c.get("quantity") or "",
            "kind": c.get("kind"), "primary": primary_action(c),
        })
    n_used = sum(1 for c in offered if c["id"] in used)
    by_id = {e["id"]: e for e in entries}
    past = []
    for p in record.get("past_date") or []:
        e = by_id.get(p.get("entry_id"))
        if e is None or e["slot_state"] != "planned" or _dish_key(e.get("meal")) != _dish_key(p.get("dish")):
            continue
        line = past_date_line(dict(p, date=e["date"], slot=e["slot"]))
        if line and line not in past:
            past.append(line)
    return {"total": len(offered), "used": n_used, "line": count_line(n_used, len(offered)),
            "couldnt_fit": rows, "past_date": past}


# ---------- "Couldn't fit these": the three taps (B3) ----------

def _offered(record: dict, item_id: int) -> dict:
    for c in record["offered"]:
        if c["id"] == item_id:
            return c
    raise ValueError("That isn't one of this week's things to use up.")


# Names that end in s but are one thing.
_SINGULAR_S = {"hummus", "asparagus", "couscous", "molasses", "swiss", "brussels", "citrus", "bass",
               "watercress", "lemongrass", "harissa", "salsa", "feta", "pasta"}


def _verb(name: str) -> str:
    """ "was" or "were" for a toast's "<thing> was <verbed>" (copywriter
    rule 3: "Two items were moved"): "Red peppers were moved…",
    "Half a cabbage was moved…"."""
    words = re.findall(r"[a-z]+", re.sub(r"\([^)]*\)", "", (name or "").lower()))
    last = words[-1] if words else ""
    if last in _SINGULAR_S or not last.endswith("s") or last.endswith("ss") or last.endswith("us"):
        return "was"
    return "were"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def fitting_dinner(entries: list[dict], cand: dict, today: str, reheats: set[int]) -> dict | None:
    """
    "Add as a side" goes on the most fitting dinner: the earliest dinner
    that is cooked (not a reheat or takeout night), still ahead, and on or
    before the item's use_by — preferring one that isn't already using
    something up, so the week's use spreads. Failing any before its
    use_by, the earliest cooked dinner still ahead (sooner is better than
    never). None when the week has no cooked dinner left.
    """
    dinners = [e for e in entries if e["slot"] == "dinner" and _is_cook(e, reheats)
               and (e.get("date") or "") >= today]
    in_time = [e for e in dinners if _fits_date(e, cand, today)]
    pool = in_time or dinners
    free = [e for e in pool if not live_uses(e.get("derived_from_json"), e.get("meal"))]
    return (free or pool or [None])[0]


def resolve(plan_id: int, item_id: int, action: str) -> dict:
    """
    One tap on a "Couldn't fit these" row. `action` is "freeze", "side" or
    "let_go". Returns {"action", "item_id", "toast", "undo", ...}; hand
    `undo` back to undo_resolve to take it back.

      freeze — inventory.freeze_item: out of the fridge, into the Freezer
               dated today. A later week's use_up.candidates offers it
               again, oldest first.
      side   — attached to fitting_dinner as a side (no shopping: it is
               already home), and that dinner gets the "Uses the …" tag.
      let_go — left untouched in the kitchen; only this week's card
               stops asking.
    """
    from . import inventory as _inventory
    from . import leftovers as _leftovers
    from . import plates as _plates

    if action not in ("freeze", "side", "let_go"):
        raise ValueError("Freeze it, add it as a side, or let it go.")
    conn = get_conn()
    try:
        record = _record(conn, plan_id)
    finally:
        conn.close()
    cand = _offered(record, int(item_id))
    name = cand["item"]
    out = {"action": action, "item_id": cand["id"], "item": name}
    if action == "freeze":
        frozen = _inventory.freeze_item(cand["id"])
        out["undo"] = {"freeze": frozen["undo"]}
        # The house toast pattern, "<thing> was <verbed>" (copywriter rule 3).
        out["toast"] = f"{_cap(name)} {_verb(name)} moved to the freezer."
        resolution = {"action": "frozen"}
    elif action == "side":
        conn = get_conn()
        try:
            today = _today(conn).isoformat()
            entries = _plan_entries(conn, plan_id)
        finally:
            conn.close()
        reheats = set((_leftovers.plan_leftover_chains(plan_id).get("leftovers") or {}).keys())
        dinner = fitting_dinner(entries, cand, today, reheats)
        if dinner is None:
            raise ValueError("There’s no dinner left this week to put it with.")
        side = {"name": _cap(short_name(name)), "covers": [], "ingredients": [], "instructions": [],
                "minutes": 0, "added_by": "household", "use_up_id": cand["id"]}
        _plates.attach_sides(dinner["id"], [side], [])
        derived = _derived(dinner.get("derived_from_json"))
        tagged = [u for u in (derived.get(USE_UP_KEY) or []) if isinstance(u, dict)]
        tagged.append({"id": cand["id"], "item": name, "dish": dinner.get("meal") or ""})
        derived[USE_UP_KEY] = tagged
        conn = get_conn()
        try:
            conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
                         (json.dumps(derived), dinner["id"], household_id()))
            conn.commit()
        finally:
            conn.close()
        weekday = date.fromisoformat(dinner["date"]).strftime("%A")
        out.update(entry_id=dinner["id"], undo={"entry_id": dinner["id"]},
                   toast=f"{_cap(name)} {_verb(name)} added to {weekday}’s dinner.")
        resolution = {"action": "side", "entry_id": dinner["id"]}
    else:
        out["undo"] = {}
        out["toast"] = f"{_cap(name)} {_verb(name)} left off the plan."
        resolution = {"action": "let_go"}
    conn = get_conn()
    try:
        record = _record(conn, plan_id)
        record["resolved"][str(cand["id"])] = resolution
        _save_record(conn, plan_id, record)
        conn.commit()
    finally:
        conn.close()
    return out


def undo_resolve(plan_id: int, item_id: int, undo: dict | None = None) -> dict:
    """The Undo on a "Couldn't fit these" toast: the row comes back."""
    from . import inventory as _inventory

    conn = get_conn()
    try:
        record = _record(conn, plan_id)
    finally:
        conn.close()
    cand = _offered(record, int(item_id))
    resolution = record["resolved"].get(str(cand["id"])) or {}
    action = resolution.get("action")
    undo = undo or {}
    if action == "frozen" and undo.get("freeze"):
        _inventory.unfreeze_item(undo["freeze"])
    elif action == "side":
        entry_id = resolution.get("entry_id")
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT sides_json, derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
                (entry_id, household_id()),
            ).fetchone()
            if row is not None:
                try:
                    sides = json.loads(row["sides_json"] or "[]") or []
                except (TypeError, ValueError):
                    sides = []
                sides = [s for s in sides if not (isinstance(s, dict) and s.get("use_up_id") == cand["id"])]
                derived = _derived(row["derived_from_json"])
                tagged = [u for u in (derived.get(USE_UP_KEY) or [])
                          if isinstance(u, dict) and u.get("id") != cand["id"]]
                if tagged:
                    derived[USE_UP_KEY] = tagged
                else:
                    derived.pop(USE_UP_KEY, None)
                conn.execute(
                    "UPDATE meal_plan_entries SET sides_json = ?, derived_from_json = ? WHERE id = ? AND household_id = ?",
                    (json.dumps(sides), json.dumps(derived), entry_id, household_id()),
                )
                conn.commit()
        finally:
            conn.close()
    conn = get_conn()
    try:
        record = _record(conn, plan_id)
        record["resolved"].pop(str(cand["id"]), None)
        _save_record(conn, plan_id, record)
        conn.commit()
    finally:
        conn.close()
    return {"item_id": cand["id"], "undone": True}
