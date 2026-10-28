"""
"Quick check: still have these?" — the few kitchen rows Pomona is least sure
of, asked once, when the household plans the week.

Loop Board A4 (Emily, 2026-10-26, decision 3: "It should ask when they want
their plan and grocery list made."). The kitchen keeps itself from shopping
and cooking, and it drifts: snacks get eaten, things get thrown out. This
is the short check that fixes the drift — at most MAX_CHIPS rows, a tap on
anything that's gone, and one button.

  candidates()      the rows to ask about, least sure first.
  answer(gone, confirmed)
                    gone   → the row comes out of the kitchen (deleted, the
                             same as the Kitchen view's remove).
                    confirmed → inventory_items.confirmed_on = household
                             today, so this check leaves it alone for
                             RECHECK_AFTER_DAYS and B4's "bought or
                             confirmed in the last 14 days" can read it.

ONE function for the list on purpose: the Kitchen tab's freezer segment
(card C4) shows the same chips, and an answer in either place answers both
— a confirmed row drops off this list wherever it was confirmed.

How "least sure" is decided (constants below, each one line to change):
  - past its date (fridge/pantry): first. use_up.candidates leaves those
    out on purpose ("A4's check owns those"); a date is a food-safety
    signal, so the check asks rather than the planner planning it.
  - otherwise, days since the last thing that made Pomona sure of the row:
    bought (created_at / updated_at — a grocery tick merges into the row),
    used (cooking's depletion writes updated_at), counted (counted_on) or
    confirmed here (confirmed_on). A row touched in the last UNSURE_AFTER_DAYS
    (freezer: FREEZER_UNSURE_AFTER_DAYS) is not asked about at all.
  - a row no household recipe uses (snacks, the hummus, the apples) gets
    NO_RECIPE_BONUS_DAYS added: nothing Pomona plans would ever notice it
    went.
Never a staple and never a spice (spices.is_spice). A staple is matched on
the staples code's own key (grocery._merge_key, as staples._find_by_name)
and also covers kinds of it: "Whole milk" under a "Milk" staple (_is_staple).
Cooked night-off portions are freezer_portions' and are left out, as in
use_up.
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

# Never more than five chips (the card).
MAX_CHIPS = 5
# A fridge/pantry row bought, used, counted or confirmed this recently is
# one Pomona is sure of. An ASSUMPTION: a week, so last weekend's shop is
# not asked about on Sunday.
UNSURE_AFTER_DAYS = 7
# Freezer food keeps; asking about last month's peas every week would be
# noise. An ASSUMPTION.
FREEZER_UNSURE_AFTER_DAYS = 45
# A confirmed row is left alone this long (also B4's window).
RECHECK_AFTER_DAYS = 14
# Weight for "no recipe uses it" — counted as this many extra days of
# not-knowing. An ASSUMPTION.
NO_RECIPE_BONUS_DAYS = 7

# A quantity made only of words ("half a bag", "a little", "the end of a
# jar") is shown with the name; a number is never shown and never made up.
# Only these container/portion words take an "of": "Half a bag of rice".
_OF_WORDS = {
    "bag", "bags", "box", "boxes", "jar", "jars", "bottle", "bottles", "can", "cans",
    "tin", "tins", "carton", "cartons", "tub", "tubs", "block", "blocks", "loaf",
    "bunch", "bunches", "head", "heads", "pack", "packs", "packet", "packets",
    "container", "containers", "bowl", "bowls", "bit", "lot", "piece", "pieces",
    "half", "quarter", "third", "end", "rest", "wedge", "slab", "stick", "sticks",
}


def _name_key(name: str) -> str:
    """Lowercase words, a simple plural dropped — one chip per thing, and
    the key recipe ingredients are compared on. A copy of use_up's own, so
    this module does not lean on another module's private helper."""
    words = re.findall(r"[a-z]+", (name or "").lower())
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def _today(conn=None) -> date:
    from .cooker import household_today
    return household_today(conn=conn)


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def label_for(item: str, quantity: str) -> str:
    """The chip's words: the name, with the row's own amount WORDS in front
    when it has some ("Half a bag of rice"); a numeric amount is left off
    rather than echoed or rounded."""
    name = (item or "").strip()
    q = (quantity or "").strip()
    if not q or re.search(r"\d|[¼½¾⅓⅔⅛]", q) or not re.search(r"[a-zA-Z]", q):
        return name
    words = q.lower().split()
    if words[-1] == "of":
        joined = f"{q} {name}"
    elif words[-1] in _OF_WORDS:
        joined = f"{q} of {name}"
    else:
        return name
    # The name sits mid-sentence: "Half a bag of rice", not "of Rice" —
    # unless it reads as a proper name or an acronym (second letter capital).
    if len(name) > 1 and name[0].isupper() and not name[1].isupper():
        joined = joined[: len(joined) - len(name)] + name[0].lower() + name[1:]
    return joined[0].upper() + joined[1:]


def _staple_merge_keys(conn) -> list:
    """The household's staples, read the ONE way the Kitchen tab and the
    planner read them (use_up._staple_keys / is_staple). The check used to
    keep its own rule (grocery._merge_key + last words), and the two
    disagreed: a "Wheat bread" staple hid "Whole wheat sourdough bread"
    from the freezer rows while the chip still asked about it (C4 verify,
    item 3)."""
    from . import use_up as _use_up
    return _use_up._staple_keys(conn)


def _is_staple(name: str, staple_keys: list) -> bool:
    """use_up.is_staple: the staple's words with the same last word (the
    thing itself) — "Whole milk" is the "Milk" staple, "Milk chocolate" is
    not. Errs toward not asking ("Coconut milk" under "Milk" is left out)."""
    from . import use_up as _use_up
    return _use_up.is_staple(name, staple_keys)


def _recipe_keys(conn) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT ingredients_json FROM recipes WHERE household_id = ?", (household_id(),)
        ).fetchall()
    except Exception:
        return []
    keys = set()
    for r in rows:
        try:
            ings = json.loads(r["ingredients_json"] or "[]")
        except (TypeError, ValueError):
            continue
        for ing in ings if isinstance(ings, list) else []:
            name = ing.get("item") if isinstance(ing, dict) else ing
            key = _name_key(str(name or ""))
            if key:
                keys.add(key)
    return list(keys)


def _recipe_uses(key: str, recipe_keys: list[str]) -> bool:
    """"Baby spinach" is used by a recipe calling for "spinach", and "Rice"
    by one calling for "jasmine rice" — whole words either way round."""
    padded = f" {key} "
    for rk in recipe_keys:
        if f" {rk} " in padded or padded in f" {rk} ":
            return True
    return False


def candidates(location: str | None = None, conn=None) -> list[dict]:
    """
    The rows to ask "still have these?" about, least sure first, at most
    MAX_CHIPS. `location` ("fridge" | "pantry" | "freezer") narrows it —
    the Kitchen tab's freezer segment asks for "freezer". Each item:

      {"id": inventory_items.id, "item": "Rice", "quantity": "half a bag",
       "location": "pantry", "label": "Half a bag of rice",
       "reason": "past_date" | "unsure", "days_unsure": 23,
       "recipe_uses": False}
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        today = _today(conn)
        staples = _staple_merge_keys(conn)
        recipe_keys = _recipe_keys(conn)
        from . import spices as _spices
        from . import tonight as _tonight

        rows = conn.execute(
            "SELECT id, item, quantity, category, location, expiration_date, created_at, "
            "updated_at, counted_on, confirmed_on, source "
            "FROM inventory_items WHERE household_id = ? ORDER BY id",
            (household_id(),),
        ).fetchall()
        want = (location or "").strip().lower() or None
        scored, seen = [], set()
        for r in rows:
            row = dict(r)
            name = (row["item"] or "").strip()
            key = _name_key(name)
            if not name or not key or _is_staple(name, staples) or _spices.is_spice(name):
                continue
            if (row["quantity"] or "").strip() in ("0", "0.0"):
                continue
            where = _quantities._display_location(row).strip().lower()
            if want and where != want:
                continue
            if where == "freezer" and row.get("source") == _tonight.NIGHT_OFF_FROZEN_SOURCE:
                continue
            confirmed = _day(row.get("confirmed_on"))
            if confirmed and (today - confirmed).days < RECHECK_AFTER_DAYS:
                continue
            sure_days = [d for d in (_day(row.get("created_at")), _day(row.get("updated_at")),
                                     _day(row.get("counted_on")), confirmed) if d]
            last_sure = max(sure_days) if sure_days else None
            age = (today - last_sure).days if last_sure else 365
            uses = _recipe_uses(key, recipe_keys)
            turns = _day(row.get("expiration_date"))
            past = where != "freezer" and turns is not None and turns < today
            if not past:
                floor = FREEZER_UNSURE_AFTER_DAYS if where == "freezer" else UNSURE_AFTER_DAYS
                if age < floor:
                    continue
            if key in seen:
                continue  # one chip per thing; the first row answers for it
            seen.add(key)
            weight = age + (0 if uses else NO_RECIPE_BONUS_DAYS)
            scored.append(((0 if past else 1, -weight, row["id"]), {
                "id": row["id"], "item": name, "quantity": row["quantity"] or "",
                "location": where, "label": label_for(name, row["quantity"] or ""),
                "reason": "past_date" if past else "unsure", "days_unsure": age,
                "recipe_uses": uses,
            }))
        scored.sort(key=lambda s: s[0])
        return [c for _, c in scored[:MAX_CHIPS]]
    finally:
        if own:
            conn.close()


def _ids(values) -> list[int]:
    out = []
    for v in values or []:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if i not in out:
            out.append(i)
    return out


def answer(gone=None, confirmed=None) -> dict:
    """
    The check's one write. `gone` rows come out of the kitchen; `confirmed`
    rows are stamped confirmed_on = household today. An id that is no
    longer there (answered in the other place, or removed since) is skipped,
    not an error — answering in either place answers both. An id in both
    lists counts as gone. Returns {"removed": [{id, item, undo}], "confirmed":
    [ids]}; each `undo` is the Kitchen tab's (whats_home.restore_item), which
    the freezer segment's chips use when a gone chip is tapped again.
    """
    gone_ids = _ids(gone)
    confirm_ids = [i for i in _ids(confirmed) if i not in gone_ids]
    conn = get_conn()
    try:
        today = _today(conn).isoformat()
        removed, stamped = [], []
        # Kept in inventory_removals (how = 'gone'), the Kitchen tab's record
        # of a row taken out, so its Undo can put this one back too (C4).
        for i in gone_ids:
            row = conn.execute(
                "SELECT * FROM inventory_items WHERE id = ? AND household_id = ?", (i, household_id())
            ).fetchone()
            if not row:
                continue
            cur = conn.execute(
                "INSERT INTO inventory_removals (household_id, inventory_item_id, item, how, row_json) "
                "VALUES (?, ?, ?, 'gone', ?)",
                (household_id(), row["id"], row["item"], json.dumps(dict(row))),
            )
            conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (i, household_id()))
            removed.append({"id": row["id"], "item": row["item"],
                            "undo": {"kind": "removed", "item_id": row["id"], "removal_id": cur.lastrowid}})
        for i in confirm_ids:
            # updated_at is left alone on purpose: it orders same-name rows
            # (inventory._find_row_by_name) and stands for bought/used.
            cur = conn.execute(
                "UPDATE inventory_items SET confirmed_on = ? WHERE id = ? AND household_id = ?",
                (today, i, household_id()),
            )
            if cur.rowcount:
                stamped.append(i)
        conn.commit()
    finally:
        conn.close()
    if removed:
        settle_live_plans_after_kitchen_change()
    return {"removed": removed, "confirmed": stamped}


def settle_live_plans_after_kitchen_change() -> None:
    """
    After a kitchen row is said to be gone (this check, the Kitchen tab's
    Used it up / Threw it out) or put back by Undo: every live plan still
    running re-reads the kitchen for the two things it booked off it.

      - its fridge moves (defrost.resync_plan_thaws): a pending "Move the
        chicken thighs to the fridge" booked off a freezer row the
        household just said is gone is swept, and comes back on Undo;
      - Shop's "Already home" (already_home.leave_off_what_is_home,
        put_back_only): a line set aside because that row covered it goes
        back on the list. Only ever back ON — this answer also stamps the
        untapped rows confirmed, and the list does not grow a second
        "Already home" section because someone answered a check.

    Before this (hunt 2026-10-28) neither re-read happened until some
    unrelated change to the plan: tapping "Chicken Thighs · gone" on
    Sunday's check left this week's move on Today and the evening text,
    and the spinach the check said was gone stayed off the list.

    Called after the caller's connection is closed (both passes open their
    own — a nested get_conn in an open write is how this repo earns
    "database is locked"). Never raises: the answer is written either way.
    """
    from . import already_home as _already_home
    from . import cooker as _cooker
    from . import defrost as _defrost
    from . import weekly_plan as _weekly_plan

    try:
        today = _cooker.household_today()
        conn = get_conn()
        try:
            plans = conn.execute(
                "SELECT * FROM weekly_plans WHERE household_id = ? AND status IN ('draft', 'approved')",
                (household_id(),),
            ).fetchall()
        finally:
            conn.close()
        for plan in plans:
            start, days = _weekly_plan.plan_period(plan)
            if days <= 0 or date.fromisoformat(start) + timedelta(days=days - 1) < today:
                continue
            _defrost.resync_plan_thaws(plan["id"])
            if plan["status"] == "approved":
                _already_home.leave_off_what_is_home(plan["id"], put_back_only=True)
    except Exception:
        logger.exception("Re-reading the live plans after a kitchen change failed; the change is written")
