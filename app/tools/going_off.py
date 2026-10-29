"""
Today, mid-week: food going off (Loop Board B5, Option B screen 4 —
"The week changes, Pomona notices", Emily 2026-10-26).

Two things, and never more than one of each:

- **The card.** A dinner that was going to use something going off didn't
  happen, and that thing turns today or tomorrow:

      The spinach is still home
      Tuesday's Spinach Pasta didn't happen, and the spinach turns
      tomorrow. Swap tonight to the Spinach Pasta and do the Chili
      Thursday?
      [Swap them] [Leave it]

  One card, for the most urgent item that has a swap to offer.

- **The line.** Something going off with no plan for it at all: one quiet
  sentence, no button — "Greek yogurt is best today."

THE SWAP IS NOT A NEW MOVER. Every existing "this meal is now on another
day" door refuses a night that has gone by (meal_move.plan_move,
weekly_plan.swap_dinner_nights — overnight 2026-10-20), on purpose, so
none of them can carry Tuesday's dish onto tonight by itself. What this
does is chain the existing writers, each under its own rules, in one
transaction:

  1. Tonight's dinner moves on by the night off's own decision
     (tonight._night_off_plan): to the next free night through the nights
     swap (`move`), or — cooked double for a later night and nothing free —
     onto the first night it was feeding (`cook_on_fed`,
     weekly_plan.move_cook_onto_fed_night), tonight's share going in the
     freezer exactly as the night off freezes it ("The extra goes in the
     freezer."). A REHEAT tonight (the mockup's "Leftover chili") moves to
     the next free night through the nights swap too, whose own age rule
     (leftovers.MAX_LEFTOVER_DAYS) refuses leftovers too old on the new
     night. A cook already started, or nowhere for it to go: no card.
     An `open` tonight needs nothing moved.
  2. The skipped dinner is re-dated onto tonight by
     weekly_plan._redate_plan_rows, the one re-dating writer (chain
     references, fridge moves, a leftovers chain that would run backwards
     refused). Its skipped_at is cleared: it is tonight's dinner now.
  3. The night it left is `planned_empty`, saying where the dish went —
     never absent — with yesterday_ask's own "skipped and moved" marker,
     so yesterday's card never asks about it.

Undo is plan_undo's snapshot on that holder row, under yesterday_ask's own
key — restored by the same plan_undo calls undo_skipped_move makes, plus
the freezer portion taken back the way the night off's undo does.

While the "Did you have it?" card is asking about yesterday's dinner, that
dinner is still the plan for its food: no card and no line about it until
it's answered.

"USES" is decided in one place, `dinner_uses`: a dish-stamped use-up tag
(B2/B3, derived_from["use_up"], read through use_up.live_uses) by
inventory id, or one of its recipe's ingredients being the same food
(`_matches`: same name, or the same head noun with only descriptor words
added — "Baby spinach" is spinach, "peanut butter" is not butter).
"""
from __future__ import annotations

import datetime
import json
import logging

from ..db import get_conn, write
from ._shared import household_id

logger = logging.getLogger("home_manager")

# "Turns within 1 day": today or tomorrow. Food already past its date is
# Kitchen's to show (the same lower bound the bell's expiring notice keeps,
# notifications.py #2) — "turns tomorrow" about yesterday's milk is untrue.
TURNS_WITHIN_DAYS = 1

# notification_dismissals key for "Leave it" — per household, per item,
# per local day. Tomorrow is a different key, so tomorrow can ask again.
LEAVE_KEY = "going-off-leave:{date}:{item_id}"

# On the undo record, so the undo's sentence can name both dishes.
SWAP_KIND = "going_off_swap"  # == yesterday_ask.GOING_OFF_SWAP_KIND


# ---------- reading ----------

def _today() -> datetime.date:
    from . import cooker as _cooker
    return _cooker.household_today()


# A trailing word that names the form, not the food: "spinach leaves" is
# spinach. Dropped from the end of a longer name before the head noun is read.
_FORM_TAILS = frozenset({"leaf", "leaves"})


def _words(text: str) -> list[str]:
    """The name's words, lowercased and singular — except a _DESCRIPTORS
    word, which is checked BEFORE singularizing ("boneless" must not become
    "boneles"). A form tail ("leaves") is dropped from a longer name."""
    from . import cooker as _cooker
    clean = (text or "").split(",")[0].lower()
    raw = [w.strip("()-.") for w in clean.split() if w.strip("()-.")]
    while len(raw) > 1 and raw[-1] in _FORM_TAILS:
        raw.pop()
    return [w if w in _DESCRIPTORS else _cooker._singularize(w) for w in raw]


# Words that say how much, how fresh or how it's cut — never WHAT the food
# is. "Baby spinach" is spinach; "peanut butter" is not butter, "coconut
# milk" is not milk, "sour cream" is not cream, "sweet potato" is not a
# potato. A loose match may only differ by these.
_DESCRIPTORS = frozenset({
    "baby", "fresh", "large", "small", "medium", "jumbo", "ripe", "organic", "whole",
    "boneless", "skinless", "chopped", "sliced", "diced", "minced", "shredded", "grated",
    "crumbled", "plain", "unsalted", "salted", "lean", "extra", "trimmed", "packed",
    "bag", "bunch", "head", "of", "a", "an",
})


def _matches(name: str, item: dict) -> bool:
    """
    Whether an ingredient (or a stored `uses` name) is this inventory row.

    The same name (allowing a plural: "egg" / "Eggs"), or the same food
    with only descriptor words added on one side ("Spinach" / "Baby
    spinach, chopped"): the head noun — the last word — must agree, and
    every extra word must be a _DESCRIPTORS word. So butter never matches
    peanut butter, milk never matches coconut milk (verify pass,
    2026-10-27: the any-shared-word rule claimed all three).
    """
    a, b = _words(name), _words(item.get("item") or "")
    if not a or not b:
        return False
    if a == b:
        return True
    if a[-1] != b[-1]:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if not set(short) <= set(long_):
        return False
    return all(w in _DESCRIPTORS for w in set(long_) - set(short))


def _ingredients(conn, recipe_id) -> list[str]:
    if not recipe_id:
        return []
    row = conn.execute(
        "SELECT ingredients_json FROM recipes WHERE id = ? AND household_id = ?",
        (recipe_id, household_id()),
    ).fetchone()
    try:
        items = json.loads(row["ingredients_json"] or "[]") if row else []
    except (TypeError, ValueError):
        return []
    return [str(i.get("item") or "") for i in items if isinstance(i, dict)]


def dinner_uses(conn, row, items: list[dict]) -> list[dict]:
    """
    The going-off `items` this planned meal would use, in `items`' order.

    THE ONE PLACE "uses" is decided. Two sources, merged: the dish-stamped
    use-up tags B2/B3 write at derived_from["use_up"] (read only through
    use_up.live_uses, so a swapped dish's stale tag counts for nothing),
    matched on inventory id — a side B3 added ("Uses the cilantro") is a
    plan for the cilantro even though the recipe never names it; then the
    recipe's ingredients matched against the inventory rows. `row` must
    carry the meal name (`meal`) for the tag to be read.
    """
    from . import use_up as _use_up
    try:
        meal = row["meal"]
    except (IndexError, KeyError):
        meal = None
    tagged = {u.get("id") for u in _use_up.live_uses(row["derived_from_json"], meal)}
    names = _ingredients(conn, row["recipe_id"])
    return [it for it in items
            if it.get("id") in tagged or any(_matches(n, it) for n in names)]


def going_off_items(today: datetime.date) -> list[dict]:
    """Fridge and pantry rows that turn today or tomorrow, soonest first
    (inventory.get_expiring_soon — the freezer is never in it). Never a
    staple or a spice: the Kitchen tab hides both (whats_home._shown_rows),
    so Today must not name one ("Whole milk is best today." beside a Milk
    staple the Kitchen never lists — merge seams, 2026-10-27)."""
    from . import inventory as _inventory
    from . import spices as _spices
    from . import use_up as _use_up
    start = today.isoformat()
    conn = get_conn()
    try:
        staples = _use_up._staple_keys(conn)
    finally:
        conn.close()
    return [e for e in _inventory.get_expiring_soon(days=TURNS_WITHIN_DAYS)
            if (e.get("expiration_date") or "") >= start
            and not _use_up.is_staple(e.get("item") or "", staples)
            and not _spices.is_spice(e.get("item") or "")]


def _left_today(conn, today: str) -> set[int]:
    prefix = LEAVE_KEY.format(date=today, item_id="")
    return {
        int(r["key"][len(prefix):]) for r in conn.execute(
            "SELECT key FROM notification_dismissals WHERE household_id = ? AND key LIKE ?",
            (household_id(), prefix + "%"),
        ).fetchall() if r["key"][len(prefix):].isdigit()
    }


def _planned_from_today(conn, today: str):
    """Every real COOK still ahead on any live plan, today included — what
    "a plan for it" means. A leftovers night or a freezer portion is not
    one: it reheats a batch, it uses nothing from the fridge — and the
    leftovers of a cook that never happened would otherwise read as a plan
    for exactly the food that cook left behind (verify pass, 2026-10-27:
    no card and no line while the spinach went off)."""
    from . import leftovers as _leftovers
    out = []
    for r in conn.execute(
        """
        SELECT mpe.id, mpe.recipe_id, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.household_id = ? AND wp.status != 'retired' AND mpe.date >= ?
          AND mpe.slot_state = 'planned' AND mpe.cooked_status != 'done'
          AND mpe.component_category IS NULL
        """,
        (household_id(), today),
    ).fetchall():
        try:
            derived = json.loads(r["derived_from_json"] or "{}")
        except (TypeError, ValueError):
            derived = {}
        if isinstance(derived, dict) and (derived.get("links_to") or derived.get(_leftovers.FROM_FREEZER_KEY)):
            continue
        out.append(r)
    return out


def _asked_about_yesterday(conn, today: datetime.date) -> list:
    """The meals the "Did you have it?" card is asking about right now. Until
    it's answered, that meal is still the plan for its food: neither the
    swap card nor the quiet line may contradict the question above them."""
    from . import yesterday_ask as _yesterday_ask
    ids = [m["entry_id"] for m in _yesterday_ask.yesterday_check(today)["meals"]]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT mpe.id, mpe.recipe_id, mpe.derived_from_json, "
        f"COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        f"LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        f"WHERE mpe.household_id = ? AND mpe.id IN ({marks})",
        (household_id(), *ids),
    ).fetchall()


def _plan_rows(conn, plan_id: int):
    from . import weekly_plan as _weekly_plan
    return conn.execute(
        f"""
        SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.cooked_status, mpe.cook_started_at,
               mpe.skipped_at, mpe.recipe_id, mpe.freeform_meal, mpe.derived_from_json,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe
        LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date, {_weekly_plan.slot_order_sql('mpe.slot')}, mpe.id
        """,
        (plan_id, household_id()),
    ).fetchall()


def _skipped_dinners(conn, plan, rows, today: str) -> list:
    """
    Dinners on this plan that didn't happen: a past night, a real dish with
    a recipe, never ticked and never started, not a reheat. Latest first —
    the most recent miss is the one whose food is freshest in mind.

    One exclusion: yesterday's dinner nobody has answered for yet. Today's
    "Did you have it?" card is asking exactly that, and "didn't happen" is
    not ours to assume while it is (the household may simply not have
    ticked it). "We skipped it" answers it, and then it counts.
    """
    from . import bring_over as _bring_over
    from . import leftovers as _leftovers
    from . import weekly_plan as _weekly_plan

    yesterday = (datetime.date.fromisoformat(today) - datetime.timedelta(days=1)).isoformat()
    chains = _leftovers.plan_leftover_chains(plan["id"], conn=conn)
    out = []
    for r in rows:
        if r["slot"] != "dinner" or r["date"] >= today or (r["slot_state"] or "") != "planned":
            continue
        if not r["recipe_id"] or not (r["meal"] or "").strip():
            continue
        if _weekly_plan.cook_begun(r) or r["id"] in chains["leftovers"]:
            continue
        derived = _bring_over._derived(r["derived_from_json"])
        if derived.get("links_to") or derived.get(_leftovers.FROM_FREEZER_KEY):
            continue
        if r["date"] == yesterday and not r["skipped_at"]:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r["date"], reverse=True)


def _derived(row) -> dict:
    try:
        value = json.loads(row["derived_from_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _frozen_dish(row) -> str | None:
    """The dish a freezer-portion night eats, or None for any other row."""
    from . import leftovers as _leftovers
    frozen = _derived(row).get(_leftovers.FROM_FREEZER_KEY)
    if not frozen:
        return None
    return (frozen.get("dish") if isinstance(frozen, dict) else None) or row["meal"] or ""


def _swap_step(conn, plan, rows, skipped, today: str, ask_veto: bool = True) -> dict | None:
    """
    What "Swap them" would do with tonight, or None when there is no swap
    to offer. {"kind": "open"} (tonight is a question handed back — the dish
    just takes it), or the night off's own 'move' / 'cook_on_fed' step for
    tonight's dinner. Never a planned_empty tonight (nobody home, or no
    dinner asked for), never a reheat, a started cook or a dinner with
    nowhere to go. The skipped dish's own leftovers have to still come after
    tonight, and nobody at tonight's table can have said no to it.
    """
    from . import leftovers as _leftovers
    from . import meal_move as _meal_move
    from . import tonight as _tonight
    from . import weekly_plan as _weekly_plan

    tonight_rows = [r for r in rows if r["date"] == today and r["slot"] == "dinner"]
    if len(tonight_rows) != 1:
        return None
    t = tonight_rows[0]
    state = t["slot_state"] or "planned"
    chains = _leftovers.plan_leftover_chains(plan["id"], conn=conn)
    fed = (chains["sources"].get(skipped["id"]) or {}).get("targets") or []
    if any(f["date"] <= today for f in fed):
        return None
    # The veto reads taste on connections of its own, so the write (which
    # holds the lock) skips it: the card's read a moment before asked it.
    if ask_veto and _meal_move._veto(skipped["meal"], today, "dinner"):
        return None
    if state == "open":
        return {"kind": "open", "tonight": t}
    if state != "planned" or not (t["meal"] or "").strip():
        return None
    if (t["cooked_status"] or "") == "done" or _weekly_plan.cook_begun(t):
        return None
    dinner_rows = [r for r in rows if r["slot"] == "dinner"]
    reheat = chains["leftovers"].get(t["id"])
    frozen = _frozen_dish(t)
    t_derived = _derived(t)
    if not reheat and frozen is None and t_derived.get("links_to"):
        return None  # a leftovers night with no cook on this plan to age it by
    if reheat:
        src = next((r for r in rows if r["id"] == reheat["source"]["entry_id"]), None)
        if src is None or src["skipped_at"]:
            return None  # its cook didn't happen: there are no leftovers to move
    if reheat or frozen is not None:
        # Tonight is a reheat (Option B screen 4's "Leftover chili"). The
        # night off would freeze it rather than push leftovers down the week;
        # here the household is choosing to eat it later, so it may move to
        # the next free night — through the nights swap, whose own age rule
        # (leftovers.MAX_LEFTOVER_DAYS, _leftovers_too_old_after_redate) is
        # what refuses leftovers older than that on their new night. A
        # freezer portion has no age. Nowhere it can go: no card.
        if reheat and (_leftovers.dish_identity(reheat["source"].get("meal") or t["meal"])
                       == _leftovers.dish_identity(skipped["meal"])):
            return None
        free = _tonight._next_free_night(plan, dinner_rows, today, conn=conn)
        if free is None:
            return None
        return {"kind": "move", "free": free, "to": free, "tonight": t,
                "reheat": "frozen" if frozen is not None else "leftovers",
                "reheat_dish": frozen or (reheat["source"].get("meal") if reheat else None) or t["meal"]}
    step = _tonight._night_off_plan(conn, plan, dinner_rows, t, today)
    if step["kind"] == "cook_on_fed" and _weekly_plan.fed_night_is_cooked(conn, step["target"]):
        return None
    if step["kind"] not in ("move", "cook_on_fed"):
        return None
    if _leftovers.dish_identity(t["meal"]) == _leftovers.dish_identity(skipped["meal"]):
        return None
    step["tonight"] = t
    step["to"] = step["free"] if step["kind"] == "move" else step["target"]["date"]
    return step


# ---------- the words ----------

def _verb(name: str, one: str, many: str) -> str:
    from .typed_requests import _is_plural_dish_noun
    plural = _is_plural_dish_noun(name) and not name.lower().endswith("us")
    return many if plural else one


def _mid(name: str) -> str:
    """An inventory item mid-sentence: every word lowercased ("the cremini
    mushrooms", never "the cremini Mushrooms"), except a word that is a
    name — a cuisine or one of typed_requests' proper first words ("the
    Greek yogurt", "the Brussels sprouts")."""
    from .typed_requests import _PROPER_FIRST_WORDS, _is_cuisine_adjective
    out = []
    for w in (name or "").split():
        keep = w.lower() in _PROPER_FIRST_WORDS or _is_cuisine_adjective(w)
        out.append(w[:1].upper() + w[1:].lower() if keep else w.lower())
    return " ".join(out)


def _skipped_day(skipped_date: str, to: str | None, today: str) -> str:
    """ "Tuesday", or "Last Tuesday" when a bare weekday could be misread:
    a week or more ago, or the same weekday as the night tonight's dinner
    moves to ("Wednesday’s … didn’t happen … do the Chili Wednesday?")."""
    from . import weekly_plan as _weekly_plan
    day = _weekly_plan._weekday_of(skipped_date)
    ago = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(skipped_date)).days
    if ago >= 7 or (to and _weekly_plan._weekday_of(to) == day):
        return f"Last {day}"
    return day


def _tonights_dish(step: dict) -> str:
    """How the card and the toast name what tonight had: the dish, or its
    leftovers / frozen portion ("the leftover Chili")."""
    from . import meal_move as _meal_move
    kind = step.get("reheat")
    if kind == "leftovers":
        return f"leftover {_meal_move.short_name(step['reheat_dish'])}"
    if kind == "frozen":
        return f"frozen {_meal_move.short_name(step['reheat_dish'])}"
    return _meal_move.short_name(step["tonight"]["meal"])


def _when(expires: str, today: str) -> str:
    return "today" if expires <= today else "tomorrow"


def line_text(item: dict, today: str) -> str:
    """ "Greek yogurt is best today." / "…is best used by tomorrow." """
    name = " ".join(item["item"].split())
    name = name[:1].upper() + name[1:]
    verb = _verb(name, "is", "are")
    if _when(item["expiration_date"], today) == "today":
        return f"{name} {verb} best today."
    return f"{name} {verb} best used by tomorrow."


def _card_words(item: dict, skipped, step: dict, today: str) -> dict:
    from . import meal_move as _meal_move
    from . import weekly_plan as _weekly_plan
    thing = _mid(item["item"])
    dish = _meal_move.short_name(skipped["meal"])
    day = _skipped_day(skipped["date"], step.get("to"), today)
    turns = _verb(thing, "turns", "turn")
    lead = f"{day}’s {dish} didn’t happen, and the {thing} {turns} {_when(item['expiration_date'], today)}."
    if step["kind"] == "open":
        body = f"{lead} Make the {dish} tonight?"
        label = "Make it tonight"
    else:
        other = _tonights_dish(step)
        body = f"{lead} Swap tonight to the {dish} and do the {other} {_weekly_plan._weekday_of(step['to'])}?"
        label = "Swap them"
    return {
        "title": f"The {thing} {_verb(thing, 'is', 'are')} still home",
        "body": body,
        "swap_label": label,
        "leave_label": "Leave it",
    }


# ---------- the card and the line ----------

def today_going_off(today: datetime.date | None = None) -> dict:
    """
    What Today shows about food going off.

    Returns {"date", "card": {...} | None, "line": {...} | None}.
    card: {item_id, item, entry_id (the skipped dinner), tonight_entry_id,
    kind, to (the night tonight's dinner moves to, or None), title, body,
    swap_label, leave_label}. line: {item_id, item, text}.
    At most one of each; both None when nothing is urgent.
    """
    from . import yesterday_ask as _yesterday_ask
    today = today or _today()
    day = today.isoformat()
    items = going_off_items(today)
    out = {"date": day, "card": None, "line": None}
    if not items:
        return out
    conn = get_conn()
    try:
        left = _left_today(conn, day)
        items = [it for it in items if it["id"] not in left]
        if not items:
            return out
        planned = [*_planned_from_today(conn, day), *_asked_about_yesterday(conn, today)]
        has_plan = {it["id"] for row in planned for it in dinner_uses(conn, row, items)}

        plan = _yesterday_ask._plan_covering(conn, day)
        card_item = None
        if plan is not None:
            rows = _plan_rows(conn, plan["id"])
            skipped = _skipped_dinners(conn, plan, rows, day)
            uses = {s["id"]: {it["id"] for it in dinner_uses(conn, s, items)} for s in skipped}
            for it in items:
                if it["id"] in has_plan:
                    continue
                for s in skipped:
                    if it["id"] not in uses[s["id"]]:
                        continue
                    step = _swap_step(conn, plan, rows, s, day)
                    if step is None:
                        continue
                    card_item = it
                    out["card"] = {
                        "item_id": it["id"], "item": it["item"],
                        "weekly_plan_id": plan["id"],
                        "entry_id": s["id"], "tonight_entry_id": step["tonight"]["id"],
                        "kind": step["kind"], "to": step.get("to"),
                        **_card_words(it, s, step, day),
                    }
                    break
                if card_item:
                    break
        for it in items:
            if it["id"] in has_plan or (card_item and it["id"] == card_item["id"]):
                continue
            out["line"] = {"item_id": it["id"], "item": it["item"], "text": line_text(it, day)}
            break
    finally:
        conn.close()
    return out


def leave_going_off(item_id: int, today: datetime.date | None = None) -> dict:
    """ "Leave it": the card stops asking about this item for the rest of the
    household's day (and the quiet line leaves it out too — the household
    has just said they know). Nothing on the plan changes."""
    from . import notifications as _notifications
    today = today or _today()
    _notifications.dismiss_notification(LEAVE_KEY.format(date=today.isoformat(), item_id=int(item_id)))
    return today_going_off(today)


class WeekChanged(ValueError):
    """The card on screen no longer matches the week — the route answers
    409 and the screen re-reads."""


def swap_for_going_off(item_id: int, entry_id: int, today: datetime.date | None = None) -> dict:
    """
    "Swap them": the skipped dinner comes onto tonight, and tonight's
    dinner moves on (see the module docstring for which writers do what).

    Only the swap the card is offering right now (`item_id` and the skipped
    `entry_id` it named) — anything else is WeekChanged. One transaction:
    either both nights moved and the empty night stands where the dish was,
    or nothing changed. Returns {"status": "swapped", "said", "undo_entry_id",
    "can_undo", "thaw_now", "going_off": fresh card/line}.
    """
    from . import attendance as _attendance
    from . import defrost as _defrost
    from . import leftovers as _leftovers
    from . import meal_move as _meal_move
    from . import plan_undo as _plan_undo
    from . import weekly_plan as _weekly_plan
    from . import yesterday_ask as _yesterday_ask

    today = today or _today()
    day = today.isoformat()
    current = today_going_off(today)
    card = current["card"]
    if not card or card["item_id"] != int(item_id) or card["entry_id"] != int(entry_id):
        raise WeekChanged("The week just changed — have another look.")
    plan_id = card["weekly_plan_id"]

    with write() as conn:
        conn.execute("BEGIN IMMEDIATE")
        plan = conn.execute(
            "SELECT * FROM weekly_plans WHERE id = ? AND household_id = ?", (plan_id, household_id()),
        ).fetchone()
        rows = _plan_rows(conn, plan_id)
        skipped = next((r for r in rows if r["id"] == card["entry_id"]), None)
        step = _swap_step(conn, plan, rows, skipped, day, ask_veto=False) if skipped is not None else None
        if step is None or step["kind"] != card["kind"] or step["tonight"]["id"] != card["tonight_entry_id"] \
                or step.get("to") != card["to"]:
            raise WeekChanged("The week just changed — have another look.")
        t = step["tonight"]
        dish = skipped["meal"]
        old_date = skipped["date"]

        # The undo snapshot, before the first write: every row either half
        # touches, and every row naming one of their nights.
        refs = [f"{old_date}:dinner", f"{day}:dinner"]
        direct = {skipped["id"], t["id"]}
        if step["kind"] == "move":
            free_row = next((r for r in rows if r["date"] == step["free"] and r["slot"] == "dinner"), None)
            if free_row is not None:
                direct.add(free_row["id"])
            refs.append(f"{step['free']}:dinner")
        elif step["kind"] == "cook_on_fed":
            direct |= {step["target"]["entry_id"], *[x["entry_id"] for x in step["rest"]]}
            refs.append(f"{step['target']['date']}:{step['target']['slot']}")
        all_rows = conn.execute(
            "SELECT id, derived_from_json FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? "
            "AND component_category IS NULL",
            (plan_id, household_id()),
        ).fetchall()
        touched = _plan_undo.touched_ids(all_rows, direct, refs)
        snap = _plan_undo.snapshot(conn, touched)

        # 1. Tonight's dinner moves on.
        frozen = None
        if step["kind"] == "open":
            _weekly_plan.delete_plan_entry(conn, t["id"])
        elif step["kind"] == "move":
            moved = _weekly_plan._apply_dinner_nights_swap(plan_id, day, step["free"], undo=False, conn=conn, today=day)
            if moved.get("status") == "refused":
                raise WeekChanged(moved.get("message") or "The week just changed — have another look.")
            # A free night is `open` or holds no row. An `open` row now
            # stands on tonight, where the skipped dish is about to land; a
            # question nobody needs answering goes (it is in the snapshot).
            for r in conn.execute(
                "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND household_id = ? AND date = ? "
                "AND slot = 'dinner' AND slot_state = 'open' AND component_category IS NULL",
                (plan_id, household_id(), day),
            ).fetchall():
                _weekly_plan.delete_plan_entry(conn, r["id"])
        else:
            # The night off's own cook_on_fed, freezer and all: the batch was
            # bought for two nights, the first night it fed now eats the cook
            # itself, and tonight's share — the night it was planned for —
            # goes in the freezer at the same size (tonight._freeze_portion /
            # _shrink_chain_into_freezer), so nothing bought is quietly lost.
            from . import tonight as _tonight
            target = step["target"]
            servings = _leftovers.eaters_at(day, "dinner", conn=conn)
            frozen = _tonight._freeze_portion(conn, t["meal"], servings, target["date"])
            _weekly_plan.move_cook_onto_fed_night(conn, plan_id, t["id"], day, "dinner", target)
            _tonight._shrink_chain_into_freezer(
                conn, t["id"], f"{target['date']}:{target['slot']}", servings, frozen["id"], day)

        # 2. The skipped dinner lands on tonight — the one re-dating writer.
        now_rows = [dict(r) for r in _plan_rows(conn, plan_id)]
        done = _weekly_plan._redate_plan_rows(conn, plan_id, now_rows, {skipped["id"]: day})
        if "refused" in done:
            raise WeekChanged(done["refused"])
        conn.execute(
            "UPDATE meal_plan_entries SET skipped_at = NULL WHERE id = ? AND household_id = ?",
            (skipped["id"], household_id()),
        )
        # A fridge move for it that was due on a day now gone is due now.
        conn.execute(
            "UPDATE prep_tasks SET task_date = ? WHERE household_id = ? AND meal_plan_entry_id = ? "
            "AND task_type = 'defrost' AND status = 'pending' AND task_date < ?",
            (day, household_id(), skipped["id"], day),
        )

        # 3. The night it left says where it went — never absent.
        holder_id = _weekly_plan.plan_slot_empty(
            plan_id, old_date, "dinner",
            reason=f"Skipped — the {dish} moved to {_weekly_plan._weekday_of(day)}.",
            derived_from={"constraint": _yesterday_ask.SKIPPED_MOVED_CONSTRAINT,
                          "moved_entry_id": skipped["id"]},
            conn=conn,
        )["entry_id"]
        _plan_undo.stamp(conn, holder_id, _yesterday_ask.SKIPPED_UNDO_KEY, {
            "kind": SWAP_KIND, "dish": dish,
            "other": _tonights_dish(step) if step["kind"] != "open" else None,
            "moved": {str(skipped["id"]): {"from": old_date, "to": day},
                      **({str(t["id"]): {"from": day, "to": step["to"]}} if step["kind"] != "open" else {})},
            **snap,
            "after": _plan_undo.fingerprint(conn, touched, holder_id),
            "grocery": _plan_undo.lines_changed(conn, snap),
            # The freezer portion the cook_on_fed shape wrote — Undo takes
            # back exactly that row, while nobody has touched it (the night
            # off's own rule, tonight.tonight_night_off_undo).
            "inventory": {"id": frozen["id"], "rev": frozen["rev"]} if frozen else None,
        })

    # After the commit — each of these opens connections of its own.
    moved_from = {skipped["id"]: old_date}
    if step["kind"] != "open":
        moved_from[t["id"]] = day
    try:
        _attendance.resize_after_redate(moved_from)
    except Exception:
        logger.exception("Resizing after the going-off swap failed (plan %s)", plan_id)
    thaw_now = _defrost.resync_plan_thaws(plan_id, list(moved_from), today=day, too_late_today=True)

    short = _meal_move.short_name(dish)
    if step["kind"] == "open":
        said = f"{short} moved to tonight."
    else:
        said = f"{short} moved to tonight and {_tonights_dish(step)} to " \
               f"{_weekly_plan._weekday_of(step['to'])}."
        if frozen:
            # The night off's own words for the same portion.
            said += " The extra goes in the freezer."
    return {"status": "swapped", "said": said, "undo_entry_id": holder_id, "can_undo": True,
            "thaw_now": thaw_now, "going_off": today_going_off(today)}


def undo_going_off_swap(holder_entry_id: int, today: datetime.date | None = None) -> dict:
    """
    Undo on the swap's toast: both nights back exactly as they were —
    plan_undo's restore, the same record and the same "only while nothing
    has changed since" rule as yesterday_ask.undo_skipped_move, plus the
    freezer portion the cook_on_fed shape wrote (deleted only while nobody
    has touched it, as the night off's undo does). Then each moved dish's
    lines are sized back for the table it is on again.
    """
    from . import attendance as _attendance
    from . import meal_move as _meal_move
    from . import plan_undo as _plan_undo
    from . import yesterday_ask as _yesterday_ask

    nothing = {"status": "refused", "message": "There’s nothing to put back."}
    changed = {"status": "refused", "message": "That’s changed since, so I’ve left it as it is."}
    with write() as conn:
        conn.execute("BEGIN IMMEDIATE")
        holder = conn.execute(
            "SELECT * FROM meal_plan_entries WHERE id = ? AND household_id = ?",
            (int(holder_entry_id), household_id()),
        ).fetchone()
        record = _plan_undo.read(holder, _yesterday_ask.SKIPPED_UNDO_KEY) if holder is not None else None
        if not record or record.get("kind") != SWAP_KIND:
            out = nothing
        elif not _plan_undo.still_as_left(conn, record, holder["id"]):
            out = changed
        else:
            out = None
            inv = record.get("inventory")
            if inv:
                row = conn.execute(
                    "SELECT rev FROM inventory_items WHERE id = ? AND household_id = ?",
                    (inv["id"], household_id()),
                ).fetchone()
                if row is None or row["rev"] != inv["rev"]:
                    out = changed
                else:
                    conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?",
                                 (inv["id"], household_id()))
            if out is None:
                _plan_undo.restore(conn, record, holder["id"])
    if out is not None:
        return {**out, "going_off": today_going_off(today)}
    # Each dish is on its old night again; the resize wants the night it
    # was on a moment ago.
    try:
        _attendance.resize_after_redate({int(k): v["to"] for k, v in (record.get("moved") or {}).items()})
    except Exception:
        logger.exception("Resizing after undoing the going-off swap failed")
    first = _meal_move.short_name(record.get("dish"))
    other = record.get("other")
    said = f"{first} and {other} are back where they were." if other else f"{first} is back where it was."
    return {"status": "restored", "said": said, "going_off": today_going_off(today)}
