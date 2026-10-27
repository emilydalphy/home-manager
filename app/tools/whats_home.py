"""
What's home — the Kitchen TAB (Loop Board C1 + C3, 2026-10-26).

NAMING: the tab the household sees as "Kitchen" is `home` everywhere in
code (TABS key 'home', path /home, this module). `kitchen` in this codebase
already means the COOK tab (TABS key 'kitchen', path /kitchen, chat actions
tagged tab 'kitchen'), and renaming that is not this card's job.

Emily: "I'm envisioning it almost as an 'inventory management screen' for
your kitchen." The screen is mostly for reading, so this module is mostly a
read, `get_whats_home()`:

  - the count in the band ("41 things home") is every row the three
    segments show — spices and the household's regulars (the staples
    table) are never shown, they live with Staples;
  - "Use soon": up to USE_SOON_MAX rows going off within USE_SOON_DAYS,
    soonest first, from use_up.candidates (the one going-off list B1/B2
    plan from, so the Kitchen and the planner never disagree), each with
    its plan ("in Tue dinner", "snacks") or, with nothing planned, a
    "Freeze it" offer;
  - Fridge / Freezer / Pantry: each row shows its plan, else its amount as
    a word — "plenty", "about half", "almost gone" — never a number.

And C3's one-tap fixes, each returning an `undo` to hand back unchanged:
  set_amount_left (the "How much is left?" chips; "Almost gone" on food a
  planned meal needs puts it on the list, once), used_up_item,
  threw_out_item, move_item (fridge <-> pantry, or out of the freezer),
  restore_item (the Undo for all of them). "Freeze it" is
  inventory.freeze_item / unfreeze_item — this module does not write a
  second freeze.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from ..db import get_conn, write
from ._shared import household_id, require_household_row
from . import quantities as _quantities
from . import use_up as _use_up

# "Use soon" is the going-off rows within this many days (today counts as
# day 0), at most USE_SOON_MAX of them. The mockup shows three.
USE_SOON_DAYS = 4
USE_SOON_MAX = 3
# How far ahead a row's "plan" is looked for: the meals of the coming week.
PLAN_AHEAD_DAYS = 7
# How long a "Used it up" / "Threw it out" can still be undone. The toast
# lasts seconds; this bounds a token replayed later.
UNDO_WINDOW_HOURS = 24

# The "How much is left?" chips. Stored on inventory_items.amount_left.
AMOUNT_LEVELS = ("plenty", "half", "almost_gone")
AMOUNT_WORDS = {"plenty": "plenty", "half": "about half", "almost_gone": "almost gone"}

LOCATIONS = ("fridge", "freezer", "pantry")
LOCATION_LABELS = {"fridge": "Fridge", "freezer": "Freezer", "pantry": "Pantry"}

_DOW_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DOW_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_SLOT_ORDER = {"breakfast": 0, "lunch": 1, "snack": 2, "dinner": 3}

# Bought, not just added: these sources are a shop.
_BOUGHT_SOURCES = ("grocery_checkoff", "scan_receipt")


class InvalidAmount(ValueError):
    """set_amount_left with a level that isn't one of the three chips."""


def _today(conn=None) -> date:
    from .cooker import household_today
    return household_today(conn=conn)


def _local_day(utc_text: str | None, conn=None) -> date | None:
    """A UTC `datetime('now')` stamp as the household's calendar day."""
    if not utc_text:
        return None
    try:
        when = datetime.fromisoformat(str(utc_text).replace("Z", "").strip()[:19])
    except ValueError:
        try:
            return date.fromisoformat(str(utc_text)[:10])
        except ValueError:
            return None
    from .cooker import household_zone
    return when.replace(tzinfo=timezone.utc).astimezone(household_zone(conn=conn)).date()


def _day_word(d: date, today: date, long: bool = True) -> str:
    """'today' / 'yesterday' / 'tomorrow' / 'Thursday' within the week / 'Oct 30'."""
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == -1:
        return "yesterday"
    if delta == 1:
        return "tomorrow"
    if -6 <= delta <= 6:
        return (_DOW_LONG if long else _DOW_SHORT)[d.weekday()]
    return f"{_MONTHS[d.month - 1]} {d.day}"


# ---------- what is shown ----------

def _shown_rows(conn) -> list[dict]:
    """Every inventory row the Kitchen tab shows: not a staple, not a
    spice, not an emptied "0" row."""
    from . import spices as _spices
    from . import freezer_portions as _freezer_portions

    staples = _use_up._staple_keys(conn)
    today = _today(conn)
    uncooked = _freezer_portions._skipped_cooks_portion_ids(conn) | _not_yet_cooked_portion_ids(conn, today)
    rows = conn.execute(
        "SELECT id, item, quantity, source, category, location, expiration_date, created_at, "
        "updated_at, frozen_on, amount_left, amount_left_qty FROM inventory_items "
        "WHERE household_id = ? ORDER BY id",
        (household_id(),),
    ).fetchall()
    shown = []
    for r in rows:
        row = dict(r)
        name = (row["item"] or "").strip()
        # use_up.is_staple, not a name-key lookup: since the use-up verify
        # fixes, _staple_keys is a list of word lists (head-noun match), and
        # `key in staples` silently matched nothing (found merging C4).
        if not name or _use_up.is_staple(name, staples) or _spices.is_spice(name):
            continue
        if (row["quantity"] or "").strip() in ("0", "0.0"):
            continue
        where = _quantities._display_location(row).strip().lower()
        row["location"] = where if where in LOCATIONS else "pantry"
        row["item"] = name
        if row["location"] == "freezer" and row.get("source") == _NIGHT_OFF_SOURCE:
            portion = _portion_of(row)
            # A night off's spare portion of a cook that hasn't happened yet
            # (cook_on_fed writes it ahead of the cook, dated the cook's
            # night) or that was then skipped is not in the freezer yet.
            # Either signal hides it: a cook entry still pointing at it, or
            # a cook night still ahead on the household's clock (the entry
            # can be gone — plan deleted or re-drafted — re-verify, C4).
            if portion is not None and (row["id"] in uncooked or (
                    portion["cooked_on"] and portion["cooked_on"] > today.isoformat())):
                continue
            row["portion"] = portion
        shown.append(row)
    return shown


# ---------- the freezer segment (C4) ----------
#
# "9 things in the freezer" / "Oldest first. Next week's plan starts from
# the top." Rows sort by use_up.freezer_order_key, the key the planner's
# freezer candidates sort by, so the top of this list IS the planner's next
# pick. Each row's right-hand words: its plan ("in next Mon's chili"), else
# the day it went in ("frozen 28 Sep") or, for a night off's cooked
# portion, the night it was cooked ("from last Wed"), else its amount word
# (bought frozen: "plenty").

_NIGHT_OFF_SOURCE = "night_off"  # tonight.NIGHT_OFF_FROZEN_SOURCE (a test pins the two equal)
# A dish is named in a freezer row's plan line only when it is this short;
# a longer one reads as the slot ("in Thu's dinner").
_PLAN_DISH_MAX_WORDS = 3
_PORTION_RE = re.compile(r"^\s*(\d+)\s*(?:servings?|portions?)\b", re.I)


def _not_yet_cooked_portion_ids(conn, today: date) -> set[int]:
    """
    Freezer rows a night off wrote for a batch not cooked yet: the
    `cook_on_fed` night off writes its spare portion when tapped, ahead of
    the cook — on a later night, or tonight. A cook entry not ticked done,
    dated today or later, that lists the row under leftovers'
    FREEZER_EXTRA_KEY is that batch. Once the cook is ticked done, or its
    night has gone by, the portion is real and shows.

    The same rule as calls-1026/all's freezer_portions.not_yet_cooked_
    portion_ids (not on this branch's base): on merge, call that instead.
    """
    from . import leftovers as _leftovers

    out: set[int] = set()
    for r in conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE household_id = ? "
        "AND COALESCE(cooked_status, '') != 'done' AND date >= ? AND derived_from_json LIKE ?",
        (household_id(), today.isoformat(), f"%{_leftovers.FREEZER_EXTRA_KEY}%"),
    ).fetchall():
        try:
            extra = (json.loads(r["derived_from_json"] or "{}") or {}).get(_leftovers.FREEZER_EXTRA_KEY)
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(extra, dict):
            out |= {i for i in extra.get("inventory_item_ids") or []
                    if isinstance(i, int) and not isinstance(i, bool)}
    return out


def _portion_of(row: dict) -> dict | None:
    """A night off's cooked portion as the freezer shows it: "Chili · 3
    portions". The dish comes off the row's name the way freezer_portions
    reads it; the cook's night off the row's use-by, which
    tonight._freeze_portion sets FROZEN_COOKED_KEEPS_DAYS after it."""
    from . import tonight as _tonight

    dish = _tonight.frozen_portion_dish(row.get("item") or "")
    if not dish:
        return None
    m = _PORTION_RE.match(row.get("quantity") or "")
    count = int(m.group(1)) if m else None
    try:
        cooked_on = (date.fromisoformat((row.get("expiration_date") or "")[:10])
                     - timedelta(days=_tonight.FROZEN_COOKED_KEEPS_DAYS)).isoformat()
    except ValueError:
        cooked_on = (row.get("created_at") or "")[:10] or None
    label = dish if not count else f"{dish} · {count} portion{'' if count == 1 else 's'}"
    return {"dish": dish, "count": count, "cooked_on": cooked_on, "label": label}


def _freezer_day(d: date, today: date) -> str:
    """'today' / 'yesterday' / 'last Wed' (within the week) / '28 Sep': the
    mockup's own date shape for the freezer."""
    delta = (today - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if 2 <= delta <= 6:
        return f"last {_DOW_SHORT[d.weekday()]}"
    if delta < 0:
        return _day_word(d, today, long=False)
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _freezer_plan_line(plan: dict, today: date, portion: bool) -> str:
    """'in next Mon’s chili', 'in Thu’s dinner', 'in tonight’s dinner'; for
    a cooked portion, which IS the meal, 'for Thu’s dinner'. Typographic
    apostrophes, as the rest of the app's copy (C4 verify, item 8)."""
    d = date.fromisoformat(plan["date"])
    slot = plan["slot"] if plan["slot"] != "snack" else "snacks"
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", plan.get("meal") or "")
    what = slot
    if not portion and words and len(words) <= _PLAN_DISH_MAX_WORDS:
        what = " ".join(words).lower()
    lead = "for" if portion else "in"
    if d == today:
        day = "tonight’s" if plan["slot"] == "dinner" else "today’s"
    else:
        weeks_ahead = (d - (today - timedelta(days=today.weekday()))).days // 7
        if weeks_ahead >= 2:
            # Further out than next week (a plan drafted early): the date.
            return f"{lead} the {what} on {d.day} {_MONTHS[d.month - 1]}"
        day = f"{'next ' if weeks_ahead == 1 else ''}{_DOW_SHORT[d.weekday()]}’s"
    return f"{lead} {day} {what}"


def _portion_plans(conn, today: date) -> dict:
    """{inventory_item_id: plan} for the cooked portions a live plan's
    (draft or approved — freezer_portions' own claim rule) uneaten night,
    today or later, is eating; freezer_portions writes the claim onto the
    entry's derived_from_json. Same reach as raw freezer food's tags
    (_tagged_plans): next week's draft already counts (C4 verify, item 5)."""
    from . import freezer_portions as _freezer_portions

    out: dict = {}
    for r in conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.freeform_meal, mpe.derived_from_json "
        "FROM meal_plan_entries mpe JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        "WHERE mpe.household_id = ? AND wp.household_id = ? AND wp.status IN ('draft', 'approved') "
        "AND COALESCE(mpe.slot_state, 'planned') = 'planned' AND COALESCE(mpe.cooked_status, '') != 'done' "
        "AND mpe.date >= ? AND mpe.derived_from_json LIKE ? "
        "ORDER BY mpe.date, mpe.id",
        (household_id(), household_id(), today.isoformat(), f"%{_freezer_portions.KEY}%"),
    ).fetchall():
        claim = _freezer_portions.portion_on(r["derived_from_json"])
        if claim and claim["inventory_item_id"] not in out:
            slot = (r["slot"] or "dinner").strip().lower()
            d = date.fromisoformat(r["date"])
            if d == today:
                short = long = "tonight's dinner" if slot == "dinner" else f"today's {slot}"
            else:
                short = f"{_DOW_SHORT[d.weekday()]} {slot}"
                long = f"{_DOW_LONG[d.weekday()]}'s {slot}"
            out[claim["inventory_item_id"]] = {
                "entry_id": r["id"], "date": r["date"], "slot": slot,
                "meal": r["freeform_meal"] or "", "label": short, "long": long,
            }
    return out


def _tagged_plans(conn, today: date) -> dict:
    """
    {inventory_item_id: plan} for food a live plan (draft OR approved) has
    put on a dish — its checked "Uses the…" tag (use_up.live_uses), on any
    day from today on. The same rows use_up.claimed_elsewhere keeps out of
    another week's offer, so a freezer row next week's draft already uses
    reads "in next Mon's chili" here rather than looking forgotten (and
    rather than looking like the top of the next plan). Earliest dish wins.
    """
    out: dict = {}
    for r in conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.derived_from_json, "
        "COALESCE(rcp.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        "LEFT JOIN recipes rcp ON rcp.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND wp.household_id = ? AND wp.status IN ('draft', 'approved') "
        "AND COALESCE(mpe.slot_state, 'planned') = 'planned' AND COALESCE(mpe.cooked_status, '') != 'done' "
        "AND mpe.date >= ? AND mpe.derived_from_json LIKE '%use_up%' ORDER BY mpe.date, mpe.id",
        (household_id(), household_id(), today.isoformat()),
    ).fetchall():
        for u in _use_up.live_uses(r["derived_from_json"], r["meal"]):
            try:
                item_id = int(u.get("id"))
            except (TypeError, ValueError):
                continue
            if item_id in out:
                continue
            slot = (r["slot"] or "dinner").strip().lower()
            d = date.fromisoformat(r["date"])
            if d == today:
                short = long = "tonight's dinner" if slot == "dinner" else f"today's {slot}"
            else:
                short, long = f"{_DOW_SHORT[d.weekday()]} {slot}", f"{_DOW_LONG[d.weekday()]}'s {slot}"
            out[item_id] = {
                "entry_id": r["id"], "date": r["date"], "slot": slot, "meal": r["meal"] or "",
                "label": short, "long": long,
            }
    return out


def _freezer_age_row(row: dict) -> dict:
    """The row as the oldest-first key should read it: a night off's
    portion went in on its cook night (the night its row says, "from last
    Wed"), whatever moment the row itself was written (C4 verify, item 7)."""
    portion = row.get("portion")
    if portion and portion.get("cooked_on"):
        return {**row, "frozen_on": portion["cooked_on"]}
    return row


def _freezer_line(row: dict, plan: dict | None, today: date, conn) -> str:
    portion = row.get("portion")
    if plan:
        return _freezer_plan_line(plan, today, portion=bool(portion))
    if portion and portion.get("cooked_on"):
        try:
            return f"from {_freezer_day(date.fromisoformat(portion['cooked_on']), today)}"
        except ValueError:
            pass
    try:
        frozen = date.fromisoformat((row.get("frozen_on") or "")[:10])
    except ValueError:
        frozen = None
    if frozen:
        return f"frozen {_freezer_day(frozen, today)}"
    return AMOUNT_WORDS[amount_level(row)]


_HALF_RE = re.compile(r"\bhalf\b|½|\b0?\.5\b|\b1/2\b", re.I)
_LOW_RE = re.compile(r"\balmost\b|\bnearly\b|\ba little\b|\ba bit\b|\blow\b|\bscraps?\b|¼|\b1/4\b|\b0?\.25\b", re.I)


def amount_level(row: dict) -> str:
    """
    plenty / half / almost_gone for one row. The household's own chip wins
    while the row's quantity is what it was when they tapped it; a re-buy
    (a tick merging into the row) or a cook that changed the quantity
    makes the chip stale, and the quantity text is read instead. A
    quantity says "about half" or "almost gone" only when its words do;
    anything else — a count, a weight, nothing at all — is "plenty",
    because nobody is ever asked to count.
    """
    chip = (row.get("amount_left") or "").strip()
    if chip in AMOUNT_LEVELS and (row.get("amount_left_qty") or "") == (row.get("quantity") or ""):
        return chip
    qty = (row.get("quantity") or "").strip()
    if _LOW_RE.search(qty):
        return "almost_gone"
    if _HALF_RE.search(qty):
        return "half"
    return "plenty"


def _upcoming_meals(conn, today: date) -> list[dict]:
    """
    The approved plans' meals from today through PLAN_AHEAD_DAYS, not yet
    eaten, earliest first, each with the ingredient names it uses (a
    saved recipe's list, or a freeform meal's own words). A draft is not a
    plan the household has said yes to, so it gives nothing a plan here.
    Leftover nights are skipped: the food went in on the night it was
    cooked.
    """
    from . import leftovers as _leftovers

    end = (today + timedelta(days=PLAN_AHEAD_DAYS - 1)).isoformat()
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.weekly_plan_id, mpe.freeform_meal, "
        "r.name AS recipe_name, r.ingredients_json "
        "FROM meal_plan_entries mpe JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND wp.household_id = ? AND wp.status = 'approved' "
        "AND COALESCE(mpe.slot_state, 'planned') = 'planned' AND mpe.cooked_status != 'done' "
        "AND mpe.date >= ? AND mpe.date <= ?",
        (household_id(), household_id(), today.isoformat(), end),
    ).fetchall()
    leftover_ids: set[int] = set()
    for plan_id in {r["weekly_plan_id"] for r in rows if r["weekly_plan_id"]}:
        try:
            leftover_ids |= set(_leftovers.plan_leftover_chains(plan_id, conn=conn)["leftovers"].keys())
        except Exception:
            continue
    meals = []
    for r in rows:
        if r["id"] in leftover_ids:
            continue
        names = []
        try:
            for ing in json.loads(r["ingredients_json"] or "[]"):
                n = (ing.get("item") if isinstance(ing, dict) else str(ing)) or ""
                if n.strip():
                    names.append(n.strip())
        except (ValueError, TypeError):
            pass
        meals.append({
            "entry_id": r["id"], "date": r["date"], "slot": (r["slot"] or "dinner").strip().lower(),
            "meal": r["recipe_name"] or r["freeform_meal"] or "",
            "ingredients": names, "freeform": (r["freeform_meal"] or "") if not r["ingredients_json"] else "",
        })
    meals.sort(key=lambda m: (m["date"], _SLOT_ORDER.get(m["slot"], 9), m["entry_id"]))
    return meals


def _uses(meal: dict, item: str) -> bool:
    """Whether a meal uses this row: one of its ingredients is the same
    thing (cooker's confident match — the one cooking depletes by), or a
    freeform meal names it outright."""
    from .cooker import _find_inventory_match

    row = {"item": item}
    for ing in meal["ingredients"]:
        match, confident = _find_inventory_match(ing, [row])
        if match is not None and confident:
            return True
    text = " " + " ".join(re.findall(r"[a-z]+", (meal.get("freeform") or "").lower())) + " "
    key = _use_up._name_key(item)
    if key and meal.get("freeform"):
        singular = " " + " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in text.split()) + " "
        return f" {key} " in singular
    return False


def _plan_for(item: str, meals: list[dict], today: date) -> dict | None:
    for meal in meals:
        if _uses(meal, item):
            d = date.fromisoformat(meal["date"])
            if meal["slot"] == "snack":
                short = "snacks"
                long = "snacks"
            elif d == today and meal["slot"] == "dinner":
                short = "tonight's dinner"
                long = "tonight's dinner"
            elif d == today:
                short = f"today's {meal['slot']}"
                long = f"today's {meal['slot']}"
            else:
                short = f"{_DOW_SHORT[d.weekday()]} {meal['slot']}"
                long = f"{_DOW_LONG[d.weekday()]}'s {meal['slot']}"
            return {"entry_id": meal["entry_id"], "date": meal["date"], "slot": meal["slot"],
                    "meal": meal["meal"], "label": short, "long": long}
    return None


def _plan_line(plan: dict) -> str:
    """The row's right-hand words for a planned row: 'in Tue dinner', 'snacks'."""
    return plan["label"] if plan["slot"] == "snack" else f"in {plan['label']}"


def _detail_line(row: dict, plan: dict | None, today: date, conn) -> str:
    """'Fridge · bought Saturday · best by Thursday · in Tuesday's dinner'."""
    parts = [LOCATION_LABELS.get(row["location"], "Pantry")]
    portion = row.get("portion")
    if row["location"] == "freezer" and portion and portion.get("cooked_on"):
        # A night off's portion: the night it was cooked, as its row says
        # ("from last Wed"), not the row's own stamp.
        parts.append(f"cooked {_day_word(date.fromisoformat(portion['cooked_on']), today)}")
    elif row["location"] == "freezer":
        frozen = None
        try:
            frozen = date.fromisoformat((row.get("frozen_on") or "")[:10])
        except ValueError:
            frozen = _local_day(row.get("created_at"), conn)
        if frozen:
            parts.append(f"frozen {_day_word(frozen, today)}")
    else:
        got = _local_day(row.get("created_at"), conn)
        if got:
            verb = "bought" if (row.get("source") or "") in _BOUGHT_SOURCES else "added"
            parts.append(f"{verb} {_day_word(got, today)}")
    try:
        best = date.fromisoformat((row.get("expiration_date") or "")[:10])
    except ValueError:
        best = None
    if best and row["location"] != "freezer":
        parts.append("past its date" if best < today else f"best by {_day_word(best, today)}")
    if plan:
        parts.append("for snacks" if plan["slot"] == "snack" else f"in {plan['long']}")
    return " · ".join(parts)


def _row_view(row: dict, plan: dict | None, today: date, conn) -> dict:
    level = amount_level(row)
    portion = row.get("portion")
    if row["location"] == "freezer":
        line = _freezer_line(row, plan, today, conn)
    else:
        line = _plan_line(plan) if plan else AMOUNT_WORDS[level]
    return {
        "id": row["id"],
        # A night off's portion is the dish ("Chili", not "Chili (cooked)"):
        # the sheet's title and every toast name it that way.
        "item": portion["dish"] if portion else row["item"],
        # The row's left-hand words: "Chili · 3 portions", else the item.
        "label": portion["label"] if portion else row["item"],
        "portion": portion,
        "location": row["location"],
        "category": row.get("category") or "other",
        "amount": level,
        "amount_word": AMOUNT_WORDS[level],
        "plan": plan,
        "line": line,
        "detail": _detail_line(row, plan, today, conn),
        "expiration_date": row.get("expiration_date"),
        # Freezer rows: the day it went in, else the day it arrived (a row
        # frozen before frozen_on existed) — the freezer lists oldest first.
        "frozen_on": (row.get("frozen_on") or ((row.get("created_at") or "")[:10] or None))
        if row["location"] == "freezer" else None,
        # The same rule Use soon and the draft's "Couldn't fit" use: not
        # already frozen, and not a thing a freezer ruins (yogurt, eggs…).
        "freeze_ok": _use_up.freeze_ok(row["item"], row["location"]),
        "move_to": "pantry" if row["location"] == "fridge" else "fridge",
    }


def _sort_key(view: dict):
    """Fridge and pantry: soonest date first. (The freezer sorts by
    use_up.freezer_order_key — see get_whats_home.)"""
    return ((view.get("expiration_date") or "9999")[:10], view["item"].lower())


def _shop_phrase(conn, today: date) -> str | None:
    row = conn.execute(
        "SELECT MAX(inventory_added_at) AS at FROM grocery_items WHERE household_id = ? AND inventory_added_at IS NOT NULL",
        (household_id(),),
    ).fetchone()
    d = _local_day(row["at"] if row else None, conn)
    if not d or (today - d).days > 6 or d > today:
        return None
    if d == today:
        return "today's shop"
    if (today - d).days == 1:
        return "yesterday's shop"
    return f"{_DOW_LONG[d.weekday()]}'s shop"


def _dinner_phrase(conn, today: date) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM meal_plan_entries WHERE household_id = ? AND slot = 'dinner' "
        "AND cooked_status = 'done' AND inventory_depleted_at IS NOT NULL AND date <= ?",
        (household_id(), today.isoformat()),
    ).fetchone()
    try:
        d = date.fromisoformat((row["d"] or "")[:10]) if row else None
    except ValueError:
        d = None
    if not d or (today - d).days > 6:
        return None
    if d == today:
        return "tonight's dinner"
    if (today - d).days == 1:
        return "last night's dinner"
    return f"{_DOW_LONG[d.weekday()]}'s dinner"


def up_to_date_line(conn=None) -> str:
    """
    'Up to date after Saturday's shop and last night's dinner.' — the two
    things that keep the kitchen without anyone typing: the last tick at
    the shop (grocery_items.inventory_added_at) and the last dinner marked
    eaten that took food out (meal_plan_entries.inventory_depleted_at).
    Each half only within the last week; '' when neither is known, and
    the screen then shows no line rather than a guess.
    """
    own = conn is None
    if own:
        conn = get_conn()
    try:
        today = _today(conn)
        parts = [p for p in (_shop_phrase(conn, today), _dinner_phrase(conn, today)) if p]
    finally:
        if own:
            conn.close()
    if not parts:
        return ""
    return "Up to date after " + " and ".join(parts) + "."


def get_whats_home() -> dict:
    """
    The Kitchen tab in one read:

      {"count": 41,                       # rows shown (no staples/spices)
       "up_to_date": "Up to date after Saturday's shop.",   # or ""
       "use_soon": [{"id", "item", "location", "turns_on", "dot": "today"|"soon",
                     "plan": {...}|None, "line": "today · snacks"|"in Tue dinner"|"",
                     "freeze_ok": bool}],          # at most USE_SOON_MAX
       "counts": {"fridge": 18, "freezer": 9, "pantry": 14},
       "locations": {"fridge": [row, ...], "freezer": [...], "pantry": [...]}}

    Each row: {"id", "item", "location", "amount", "amount_word", "plan",
    "line" (the right-hand words), "detail" (the sheet's line),
    "expiration_date", "frozen_on", "freeze_ok", "move_to"}.
    """
    conn = get_conn()
    try:
        today = _today(conn)
        rows = _shown_rows(conn)
        meals = _upcoming_meals(conn, today)
        portion_plans = _portion_plans(conn, today)
        tagged = _tagged_plans(conn, today)
        views = {}
        by_id = {}
        for row in rows:
            by_id[row["id"]] = row
            if row.get("portion"):
                # A cooked portion's plan is the night freezer_portions put
                # it on, never an ingredient match.
                plan = portion_plans.get(row["id"])
            elif row["location"] == "freezer" and row["id"] in tagged:
                # Freezer food a live plan's dish was checked to use (any
                # week, draft or approved — use_up.claimed_elsewhere's rule).
                plan = tagged[row["id"]]
            else:
                plan = _plan_for(row["item"], meals, today)
            views[row["id"]] = _row_view(row, plan, today, conn)
        locations = {loc: [] for loc in LOCATIONS}
        for v in views.values():
            locations[v["location"]].append(v)
        for loc in LOCATIONS:
            if loc == "freezer":
                # Oldest first by the planner's own key (use_up), so "Next
                # week's plan starts from the top" stays true.
                locations[loc].sort(key=lambda v: _use_up.freezer_order_key(_freezer_age_row(by_id[v["id"]])))
            else:
                locations[loc].sort(key=_sort_key)

        soon_cutoff = today + timedelta(days=USE_SOON_DAYS)
        use_soon = []
        # kitchen_view: Use soon is what's HOME. The planner's filters —
        # use_by on or after the first day (drops food turning TODAY, the
        # yogurt), a dislike/allergy, another plan's claim — are for
        # offering food to a draft, not for saying what's going off here.
        # Found merging C4 and in its verify (items 1-2).
        for c in _use_up.candidates(today.isoformat(), PLAN_AHEAD_DAYS, conn=conn, kitchen_view=True):
            if c["kind"] != "going_off" or c["id"] not in views:
                continue
            turns = date.fromisoformat(c["turns_on"])
            if turns > soon_cutoff:
                continue
            v = views[c["id"]]
            is_today = turns <= today
            if v["plan"]:
                snack = v["plan"]["slot"] == "snack"
                line = "today · snacks" if (is_today and snack) else _plan_line(v["plan"])
            elif c.get("freeze_ok"):
                line = ""  # the row shows the "Freeze it" pill instead
            else:
                line = "today" if is_today else f"by {_DOW_SHORT[turns.weekday()]}"
            use_soon.append({
                "id": c["id"], "item": v["item"], "location": v["location"],
                "turns_on": c["turns_on"], "dot": "today" if is_today else "soon",
                "plan": v["plan"], "line": line,
                "freeze_ok": bool(c.get("freeze_ok")) and not v["plan"],
            })
            if len(use_soon) >= USE_SOON_MAX:
                break
        return {
            "count": len(views),
            "up_to_date": up_to_date_line(conn) if views else "",
            "use_soon": use_soon,
            "counts": {loc: len(locations[loc]) for loc in LOCATIONS},
            "locations": locations,
        }
    finally:
        conn.close()


def get_whats_home_item(item_id: int) -> dict:
    """One row as the tab shows it (the C3 sheet's read)."""
    home = get_whats_home()
    for loc in LOCATIONS:
        for v in home["locations"][loc]:
            if v["id"] == item_id:
                return v
    raise ValueError("That isn't in the kitchen any more.")


# ---------- the one-tap fixes (C3) ----------

GONE_ALREADY = "That's already gone from the kitchen."


def _snapshot(conn, item_id: int) -> dict:
    try:
        require_household_row(conn, "inventory_items", item_id, label="inventory item")
    except ValueError:
        raise ValueError(GONE_ALREADY)
    row = conn.execute(
        "SELECT * FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())
    ).fetchone()
    return dict(row)


def _on_the_list(conn, item: str) -> int | None:
    """The id of a line for this thing already waiting on the list, if any."""
    from . import grocery as _grocery

    key = _grocery._merge_key(item)
    for r in conn.execute(
        "SELECT id, item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'in_cart') "
        "AND COALESCE(excluded_from_list, 0) = 0",
        (household_id(),),
    ).fetchall():
        if _grocery._merge_key(r["item"]) == key:
            return r["id"]
    return None


def set_amount_left(item_id: int, level: str) -> dict:
    """
    The "How much is left?" chips: plenty / half / almost_gone. Stored on
    the row with the quantity it was read against (see amount_level).

    "Almost gone" on a food a planned meal in the coming week needs puts
    it on the list — the same add a person's own "add milk" makes
    (grocery.add_grocery_item, no plan id, so it is a standing want) —
    unless a line for it is already waiting there. Returns
    {"item_id", "item", "amount", "amount_word", "added_to_list": bool,
     "grocery_item_id": id|None, "needed_for": "Tuesday's dinner"|None,
     "undo": {...}}; hand `undo` to restore_item.
    """
    level = (level or "").strip().lower().replace(" ", "_")
    if level == "about_half":
        level = "half"
    if level not in AMOUNT_LEVELS:
        raise InvalidAmount("How much is left has to be plenty, about half or almost gone.")
    conn = get_conn()
    try:
        before = _snapshot(conn, item_id)
        conn.execute(
            "UPDATE inventory_items SET amount_left = ?, amount_left_qty = ?, updated_at = datetime('now') "
            "WHERE id = ? AND household_id = ?",
            (level, before.get("quantity") or "", item_id, household_id()),
        )
        conn.commit()
        needed_for = None
        on_list = None
        if level == "almost_gone":
            today = _today(conn)
            plan = _plan_for(before["item"], _upcoming_meals(conn, today), today)
            if plan:
                needed_for = plan["long"]
                on_list = _on_the_list(conn, before["item"])
    finally:
        conn.close()
    added_id = None
    if needed_for and on_list is None:
        from . import grocery as _grocery
        added = _grocery.add_grocery_item(before["item"], "", before.get("category") or "other", added_by="user")
        added_id = added.get("item_id")
    return {
        "item_id": item_id,
        "item": before["item"],
        "amount": level,
        "amount_word": AMOUNT_WORDS[level],
        "added_to_list": added_id is not None,
        "grocery_item_id": added_id,
        "needed_for": needed_for,
        "undo": {
            "kind": "amount", "item_id": item_id,
            "amount_left": before.get("amount_left") or "",
            "amount_left_qty": before.get("amount_left_qty"),
            "grocery_item_id": added_id,
        },
    }


def _take_out(item_id: int, how: str) -> dict:
    conn = get_conn()
    try:
        before = _snapshot(conn, item_id)
        cur = conn.execute(
            "INSERT INTO inventory_removals (household_id, inventory_item_id, item, how, row_json) VALUES (?, ?, ?, ?, ?)",
            (household_id(), item_id, before["item"], how, json.dumps(before)),
        )
        removal_id = cur.lastrowid
        conn.execute("DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id()))
        conn.commit()
    finally:
        conn.close()
    return {"item_id": item_id, "item": before["item"], "removed": True, "how": how,
            "undo": {"kind": "removed", "item_id": item_id, "removal_id": removal_id}}


def used_up_item(item_id: int) -> dict:
    """'Used it up': the row leaves the kitchen. Undo puts it back as it was."""
    return _take_out(item_id, "used")


def threw_out_item(item_id: int) -> dict:
    """'Threw it out': the row leaves the kitchen. Undo puts it back as it was.
    (Recorded no differently from 'Used it up' today; nothing reads waste yet.)"""
    return _take_out(item_id, "thrown")


def move_item(item_id: int, location: str) -> dict:
    """
    'Move to pantry' / 'Move to fridge' — inventory.set_inventory_location
    underneath, so the freezer-door date rule is the one every move
    follows. Moving INTO the freezer is freeze_item's door, not this one.
    """
    from . import inventory as _inventory

    where = (location or "").strip().lower()
    if where == "freezer":
        raise _inventory.InvalidInventoryLocation("Freezing has its own button: Freeze it.")
    conn = get_conn()
    try:
        before = _snapshot(conn, item_id)
    finally:
        conn.close()
    moved = _inventory.set_inventory_location(item_id, where)
    return {
        "item_id": item_id, "item": before["item"], "location": moved["location"],
        "undo": {
            "kind": "moved", "item_id": item_id,
            "location": before.get("location") or "",
            "expiration_date": before.get("expiration_date"),
            "frozen_on": before.get("frozen_on"),
        },
    }


def restore_item(undo: dict) -> dict:
    """
    The Undo on every C3 toast. `undo` is what the action returned:
      removed — the row goes back exactly as it was, same id (AUTOINCREMENT
                never hands an id out twice, so nothing else can hold it);
      moved   — location, date and frozen_on put back;
      amount  — the chip put back, and the list line it added (if any, and
                still waiting to be bought) taken off.
    Returns {"item_id", "undone": bool}.
    """
    undo = undo or {}
    kind = undo.get("kind")
    try:
        item_id = int(undo.get("item_id"))
    except (TypeError, ValueError):
        raise ValueError("I couldn't tell which item to put back.")
    if kind not in ("removed", "moved", "amount"):
        raise ValueError("I couldn't tell what to undo.")
    if kind == "removed":
        try:
            removal_id = int(undo.get("removal_id"))
        except (TypeError, ValueError):
            raise ValueError("I couldn't tell which item to put back.")
    if kind == "moved" and (undo.get("location") or "") not in ("",) + LOCATIONS:
        raise ValueError("I couldn't tell where to put it back.")
    gid = None
    with write() as conn:
        if kind == "removed":
            rec = conn.execute(
                "SELECT inventory_item_id, row_json, restored_at, removed_at, "
                "removed_at >= datetime('now', ?) AS fresh FROM inventory_removals "
                "WHERE id = ? AND household_id = ?",
                (f"-{UNDO_WINDOW_HOURS} hours", removal_id, household_id()),
            ).fetchone()
            if rec is None or rec["inventory_item_id"] != item_id:
                raise ValueError("I couldn't tell which item to put back.")
            exists = conn.execute(
                "SELECT 1 FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())
            ).fetchone()
            if rec["restored_at"] or exists or not rec["fresh"]:
                return {"item_id": item_id, "undone": False}
            row = json.loads(rec["row_json"])
            # Back since (bought again at the shop, a scan, chat): the row
            # that is there now IS the kitchen's stock, so the old one is not
            # put back beside it — that made two Milks (verify, 2026-10-27).
            # Only a row created or written AFTER the removal counts as "back":
            # a second batch that was already on the shelf (two freezer bags
            # of chicken thighs) is not the same food come back (re-verify).
            where = _quantities._display_location(row).strip().lower()
            key = _use_up._name_key(row.get("item") or "")
            for other in conn.execute(
                "SELECT id, item, category, location FROM inventory_items WHERE household_id = ? "
                "AND MAX(created_at, updated_at) >= ?",
                (household_id(), rec["removed_at"]),
            ).fetchall():
                o = dict(other)
                if _use_up._name_key(o["item"] or "") == key and \
                        _quantities._display_location(o).strip().lower() == where:
                    conn.execute(
                        "UPDATE inventory_removals SET restored_at = datetime('now') WHERE id = ? AND household_id = ?",
                        (removal_id, household_id()),
                    )
                    return {"item_id": item_id, "item": row.get("item"), "undone": False,
                            "already_back": True, "back_as": o["id"]}
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(inventory_items)").fetchall()]
            row["household_id"] = household_id()
            use = [c for c in cols if c in row]
            conn.execute(
                f"INSERT INTO inventory_items ({', '.join(use)}) VALUES ({', '.join('?' * len(use))})",
                [row[c] for c in use],
            )
            conn.execute(
                "UPDATE inventory_removals SET restored_at = datetime('now') WHERE id = ? AND household_id = ?",
                (removal_id, household_id()),
            )
            return {"item_id": item_id, "item": row.get("item"), "undone": True}
        found = conn.execute(
            "SELECT item FROM inventory_items WHERE id = ? AND household_id = ?", (item_id, household_id())
        ).fetchone()
        if not found:
            return {"item_id": item_id, "undone": False}
        if kind == "moved":
            conn.execute(
                "UPDATE inventory_items SET location = ?, expiration_date = ?, frozen_on = ?, updated_at = datetime('now') "
                "WHERE id = ? AND household_id = ?",
                (undo.get("location") or "", undo.get("expiration_date"), undo.get("frozen_on"), item_id, household_id()),
            )
        else:
            conn.execute(
                "UPDATE inventory_items SET amount_left = ?, amount_left_qty = ?, updated_at = datetime('now') "
                "WHERE id = ? AND household_id = ?",
                (undo.get("amount_left") or "", undo.get("amount_left_qty"), item_id, household_id()),
            )
            gid = undo.get("grocery_item_id")
            if gid:
                still = conn.execute(
                    "SELECT item FROM grocery_items WHERE id = ? AND household_id = ? AND status = 'needed'",
                    (int(gid), household_id()),
                ).fetchone()
                from . import grocery as _grocery
                # Only the line this chip put there: the same thing, still unbought.
                if not (still and _grocery._merge_key(still["item"]) == _grocery._merge_key(found["item"])):
                    gid = None
    if gid:
        from . import grocery as _grocery
        _grocery.remove_grocery_item(int(gid))
    return {"item_id": item_id, "item": found["item"], "undone": True}
