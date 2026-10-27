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

def _name_key(name: str) -> str:
    words = re.findall(r"[a-z]+", (name or "").lower())
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def _staple_keys(conn) -> set[str]:
    try:
        rows = conn.execute("SELECT item FROM staples WHERE household_id = ?", (household_id(),)).fetchall()
    except Exception:
        return set()
    return {_name_key(r["item"]) for r in rows if r["item"]}


def _today(conn=None) -> date:
    from .cooker import household_today
    return household_today(conn=conn)


def _period_end(period_start: str | None, day_count: int, today: date) -> date:
    try:
        start = date.fromisoformat(period_start) if period_start else today
    except ValueError:
        start = today
    return max(start, today) + timedelta(days=max(1, int(day_count or 7)) - 1)


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
    going off after its last day is not this week's to use.
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        today = _today(conn)
        end = _period_end(period_start, day_count, today)
        staples = _staple_keys(conn)
        from . import spices as _spices
        from . import tonight as _tonight

        rows = conn.execute(
            "SELECT id, item, quantity, category, location, expiration_date, created_at, frozen_on, source "
            "FROM inventory_items WHERE household_id = ? ORDER BY id",
            (household_id(),),
        ).fetchall()
        going, frozen = [], []
        for r in rows:
            row = dict(r)
            name = (row["item"] or "").strip()
            if not name or _name_key(name) in staples or _spices.is_spice(name):
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
            if turns_d < today or turns_d > end:
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
