"""
"Before you shop" — the pass that runs in front of sorting the list.

Loop Board 'Shop: "Before you shop" — regulars, then spices and oils,
then already-have-it, ending on Sort the list' (High, Phase 1).

This module owns ONE stored fact, which is whether the household has
already been through the pass for the week they are shopping, plus the one
READ the three steps draw from (before_shop_steps, below) and step 1's
add. Everything the steps act on already has a home — staples in
staples.py, spices in spices.py, the already-home flags in pre_shop.py —
and a second implementation of any of those is how two screens end up
disagreeing about one household's list, so the steps write through them.

WHERE THE "ONCE PER WEEK" LIVES, and why it is not a new table.
`weekly_plans.before_shop_asked_at` is the third column of its exact
kind: `defrost_asked_at` and `cook_ahead_asked_at` are both "this
household has answered this ask for this plan", both NULL until they
are, and both read only as "is it set". A new week is a new plan row, so
"once per week" is a property of the row rather than a date anything has
to compare — which is also what makes it right for a household planning
a five-day period or two weeks at once, where "a week" is not seven days
and a date comparison would have to guess.

It is stamped when the household FINISHES the pass or taps past its last
step, not when they open it: opening it and backing out has answered
nothing, and the pass coming back is the correct behaviour there.
"""
from __future__ import annotations

import logging

from ..db import get_conn
from ._shared import acting_name, household_id

logger = logging.getLogger("home_manager")


def _current_plan_row(conn):
    """
    The plan the household is shopping for — the same one the Shop tab's
    list was built from. Deliberately `weekly_plan`'s own answer rather
    than a second query: "which plan is current" drifted once already
    (see CLAUDE.md's _current_weekly_plan_row entry) and this module has
    no business having a different opinion about it.
    """
    from . import weekly_plan as _weekly_plan

    return _weekly_plan._current_weekly_plan_row(conn)


def before_shop_state() -> dict:
    """
    {"done": bool, "weekly_plan_id": int | None} — whether the pass has
    been run for the plan being shopped for.

    A household with no plan at all gets done=False and no plan id: there
    is nothing to stamp, so the pass is offered and finishing it stamps
    nothing. That is the honest answer rather than hiding the pass from a
    household whose list came from somewhere other than a week.
    """
    # The `try` opens BEFORE get_conn, not after it: a connection that
    # cannot be opened at all is exactly the failure this function most
    # has to swallow, and the first cut left it outside — so a locked
    # database raised out of the grocery payload, which is the one thing
    # the except below exists to prevent. `conn` is closed only if it was
    # ever assigned.
    conn = None
    try:
        conn = get_conn()
        row = _current_plan_row(conn)
        if not row:
            return {"done": False, "weekly_plan_id": None}
        return {"done": bool(row["before_shop_asked_at"]),
                "weekly_plan_id": int(row["id"])}
    except Exception:
        # The pass being offered one time too many is a smaller harm than
        # a list that will not load, which is what raising here would do:
        # the only caller is the grocery payload.
        logger.exception("Reading the before-you-shop state failed")
        return {"done": False, "weekly_plan_id": None}
    finally:
        if conn is not None:
            conn.close()


def mark_before_shop_done() -> dict:
    """
    Record that the pass has been run for this plan. Idempotent, and set
    unconditionally rather than only-if-unset for mark_defrost_asked's
    reason: there is no meaningful difference between the first finish's
    timestamp and a later one for what this column exists to do, which is
    hide an automatic pass once.

    Never raises. A household that finished the pass and whose stamp did
    not land sees it once more; refusing to let them on to the sort
    screen because a write failed would be the worse answer.
    """
    conn = None
    try:
        conn = get_conn()
        row = _current_plan_row(conn)
        if not row:
            return {"done": False, "weekly_plan_id": None}
        conn.execute(
            "UPDATE weekly_plans SET before_shop_asked_at = datetime('now') "
            "WHERE id = ? AND household_id = ?",
            (int(row["id"]), household_id()),
        )
        conn.commit()
        return {"done": True, "weekly_plan_id": int(row["id"])}
    except Exception:
        logger.exception("Marking the before-you-shop pass done failed")
        return {"done": False, "weekly_plan_id": None}
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# The steps' content (2026-10-05, finishing card 13)
# ---------------------------------------------------------------------------
#
# ONE read for the whole pass, so the three screens are drawn from one
# moment of the list rather than three. Nothing here keeps state of its own:
# regulars are staples (staples.py), spices are the "Spices this week" lines
# (spices.py), and "already have these" are the pre-shop flags (pre_shop.py).
# What this adds is the two things the card asks for that none of those
# modules says: WHY Pomona ticked or didn't tick a regular, and WHO put a
# line on the list and WHEN ("Milk · you added it Wednesday").
#
# Inventory IS read here, and that is deliberate: staples.py is the module
# that promises never to read inventory_items (its docstring), and it still
# doesn't. The card's own rule is "unticked if inventory says it's there",
# and the pass is the place that rule lives.

# A household with no regulars of its own sees these to tick (the card's
# list, in its order). Anything ticked becomes a staple.
STARTER_REGULARS = [
    "Coffee", "Tea", "Milk", "Cream", "Bread", "Eggs", "Butter", "Dish soap", "Paper towels",
]

# The card's rule of thumb for "usually lasts", used while a staple's own
# rhythm is still the default rather than learned or told.
_GAP_FRIDGE_OR_BREAD = 7
_GAP_COFFEE_TEA = 14
_GAP_HOUSEHOLD = 30

_LOCATION_WORDS = {"fridge": "in the fridge", "freezer": "in the freezer", "pantry": "in the pantry"}


def _household_date_of(stamp: str | None, zone) -> "date | None":
    """A UTC `datetime('now')` stamp as the household's calendar day."""
    from datetime import datetime, timezone

    if not stamp:
        return None
    try:
        when = datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            from datetime import date as _date
            return _date.fromisoformat(stamp[:10])
        except ValueError:
            return None
    return when.astimezone(zone).date()


def _day_words(day, today) -> str:
    """'today' / 'yesterday' / 'Wednesday' (this past week) / 'Sep 26'."""
    if day is None:
        return ""
    delta = (today - day).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if delta < 7:
        return day.strftime("%A")
    return f"{day.strftime('%b')} {day.day}"


def _who_words(added_by: str | None, day_words: str, me: str | None) -> str:
    """
    Who put a line on the list, said the way a person would: "you added it
    Wednesday", "Ravi added it Monday", "Pomona added it today" (a due
    regular), "for this week's meals" (the plan's own line). A chat add is
    recorded under the session's adult (acting_name), so it reads as theirs
    — the database does not say which door a line came through.
    """
    who = (added_by or "").strip()
    low = who.lower()
    when = f" {day_words}" if day_words else ""
    if low == "ai":
        return "for this week's meals"
    if low == "staple":
        return f"Pomona added it{when}"
    if low in ("", "user"):
        return f"added{when}" if when else "on the list"
    if me and low == me.strip().lower():
        return f"you added it{when}"
    return f"{who} added it{when}"


def _gap_days(staple: dict | None, name: str) -> int:
    """How long one usually lasts. A learned or told rhythm wins; the card's
    rule of thumb fills in while it is still a default."""
    from . import staples as _staples

    if staple is not None and staple.get("cadence_source") in ("learned", "told"):
        return int(staple["cadence_days"])
    words = set(_staples._name_words(name))
    if words & {"coffee", "tea"}:
        return _GAP_COFFEE_TEA
    section = _staples.section_for(name, (staple or {}).get("category"))
    if section == "household":
        return _GAP_HOUSEHOLD
    if section == "fridge" or "bread" in words:
        return _GAP_FRIDGE_OR_BREAD
    return int(staple["cadence_days"]) if staple is not None else _GAP_FRIDGE_OR_BREAD


def _lasts_words(days: int) -> str:
    if days < 6:
        return f"usually lasts {days} days"
    if days < 10:
        return "usually lasts a week"
    if days < 26:
        return f"usually lasts {round(days / 7)} weeks"
    months = round(days / 30)
    return "usually lasts a month" if months <= 1 else f"usually lasts {months} months"


def _bought_words(days: int) -> str:
    if days <= 0:
        return "bought today"
    if days == 1:
        return "bought yesterday"
    return f"bought {days} days ago"


def _regular_guess(name: str, staple: dict | None, last_bought, inventory: list, today) -> tuple[bool, str]:
    """
    (ticked, reason) for one regular — the card's rule, in order:
    in any inventory location -> unticked, "in the fridge" (a row with a
      blank quantity counts: it means "there, amount unknown" — a chat
      "picked up coffee" — and a used-up row is deleted, not blanked);
    no purchase history -> unticked, no reason;
    last bought longer ago than it usually lasts -> ticked, "usually lasts 2 weeks";
    otherwise unticked, "bought 6 days ago".
    """
    from . import cooker as _cooker

    match, confident = _cooker._find_inventory_match(name, inventory)
    if match is not None and confident:
        return False, _LOCATION_WORDS.get(match.get("location") or "", "at home")
    if last_bought is None:
        return False, ""
    gap = _gap_days(staple, name)
    since = (today - last_bought).days
    if since >= gap:
        return True, _lasts_words(gap)
    return False, _bought_words(since)


def before_shop_steps() -> dict:
    """
    Everything the three steps show, from one read of the list:

      regulars: {choices: [{name, staple_id, ticked, reason}],
                 on_list: [{item_id, name, who}], starter: bool}
      spices:   {choices: [{item_id, name}], on_list: [{item_id, name, who}]}
      have:     {rows: [{item_id, name, ticked, reason}]}

    A thing already on this week's list is never a choice — it goes to its
    step's quiet "Already on the list" section with who added it and when
    (Emily's change on the card, 2026-10-04). Reads only, apart from what
    the two existing readers it reuses already write on every list read
    (a due spice pre-ticked, a pre-shop flag stamped as raised).
    """
    from datetime import date as _date

    from . import cooker as _cooker
    from . import grocery as _grocery
    from . import inventory as _inventory
    from . import pre_shop as _pre_shop
    from . import spices as _spices
    from . import staples as _staples
    from ._shared import current_member

    today = _cooker.household_today()
    zone = _cooker.household_zone()
    member = current_member()
    me = member["name"] if member else None
    inventory = _inventory.get_inventory()
    staples = _staples.list_staples()
    spice_view = _spices.list_spices_this_week()
    flags = _pre_shop.get_pre_shop_flags()

    conn = get_conn()
    try:
        live = conn.execute(
            "SELECT id, item, quantity, category, added_by, created_at, source_weekly_plan_id, staple_id, status "
            "FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'in_cart') "
            "AND excluded_from_list = 0",
            (household_id(),),
        ).fetchall()
        purchased = conn.execute(
            # When it was BOUGHT: inventory_added_at is stamped by the tick
            # (mark_grocery_item); created_at is only when the line was
            # made, and is the fallback for an older row without the stamp.
            "SELECT item, COALESCE(inventory_added_at, created_at) AS bought_at FROM grocery_items "
            "WHERE household_id = ? AND status = 'purchased'",
            (household_id(),),
        ).fetchall()
        # Set aside as "getting it elsewhere": still a line (add_regulars
        # won't add a second one), so the step shows it as already handled
        # rather than offering a tick that would do nothing.
        elsewhere = conn.execute(
            "SELECT id, item, quantity, category FROM grocery_items WHERE household_id = ? "
            "AND status IN ('needed', 'in_cart') AND excluded_from_list = 1",
            (household_id(),),
        ).fetchall()
        bought_events = conn.execute(
            "SELECT staple_id, MAX(on_date) AS last FROM staple_events "
            "WHERE household_id = ? AND kind = 'bought' GROUP BY staple_id",
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()

    def who(row) -> str:
        day = _household_date_of(row["created_at"], zone)
        return _who_words(row["added_by"], _day_words(day, today), me)

    live_by_id = {r["id"]: r for r in live}
    live_by_key: dict[str, object] = {}
    for r in live:
        live_by_key.setdefault(_grocery._merge_key(r["item"]), r)
    elsewhere_by_key: dict[str, object] = {}
    for r in elsewhere:
        elsewhere_by_key.setdefault(_grocery._merge_key(r["item"]), r)
    last_by_staple = {}
    for r in bought_events:
        try:
            last_by_staple[r["staple_id"]] = _date.fromisoformat((r["last"] or "")[:10])
        except ValueError:
            pass
    last_by_key: dict[str, object] = {}
    for r in purchased:
        d = _household_date_of(r["bought_at"], zone)
        k = _grocery._merge_key(r["item"])
        if d is not None and (k not in last_by_key or d > last_by_key[k]):
            last_by_key[k] = d

    # ---- step 1: regulars ----
    own = [s for s in staples if s["section"] != _staples.SECTION_SPICES and not s["paused"]]
    starter = not [s for s in staples if s["section"] != _staples.SECTION_SPICES]
    candidates = (
        [(s["item"], s) for s in own] if not starter else [(n, None) for n in STARTER_REGULARS]
    )
    reg_choices, reg_on_list = [], []
    for name, staple in candidates:
        key = _grocery._merge_key(name)
        row = live_by_key.get(key)
        if row is None and staple is not None:
            row = next((r for r in live if r["staple_id"] == staple["id"]), None)
        if row is not None:
            reg_on_list.append({
                "item_id": row["id"], "name": row["item"], "who": who(row),
                # For the screen's Undo after "Take it off the list?": a
                # removed line comes back as the same name and amount.
                "quantity": row["quantity"] or "", "category": row["category"] or "other",
            })
            continue
        aside = elsewhere_by_key.get(key)
        if aside is not None:
            reg_on_list.append({
                "item_id": aside["id"], "name": aside["item"], "who": "getting it elsewhere",
                "quantity": aside["quantity"] or "", "category": aside["category"] or "other",
            })
            continue
        last = last_by_staple.get(staple["id"]) if staple is not None else None
        if last is None:
            last = last_by_key.get(key)
        ticked, reason = _regular_guess(name, staple, last, inventory, today)
        reg_choices.append({
            "name": name,
            "staple_id": staple["id"] if staple is not None else None,
            "ticked": ticked,
            "reason": reason,
        })

    # ---- step 2: spices and oils ----
    sp_choices, sp_on_list = [], []
    for sp in spice_view.get("items", []):
        if not sp.get("ticked"):
            sp_choices.append({"item_id": sp["id"], "name": sp["item"]})
            continue
        row = live_by_id.get(sp["id"])
        # A spice line the week's recipes made carries the approval's
        # created_at, not the moment somebody ticked it — so only a line a
        # person put there says when ("Ghee · since Tuesday").
        since = ""
        if row is not None and row["source_weekly_plan_id"] is None and (row["added_by"] or "").lower() not in ("ai", "staple"):
            day_words = _day_words(_household_date_of(row["created_at"], zone), today)
            since = f"since {day_words}" if day_words and day_words not in ("today", "yesterday") else (
                f"added {day_words}" if day_words else "")
        sp_on_list.append({"item_id": sp["id"], "name": sp["item"], "who": since})

    # ---- step 3: already have these? ----
    have_rows = []
    for f in flags:
        row = live_by_id.get(f["itemId"])
        if row is None:
            continue
        hand_added = row["source_weekly_plan_id"] is None and (row["added_by"] or "").lower() not in ("ai", "staple")
        if hand_added:
            # Never offered to drop without saying so: a person put this
            # here themselves, so it starts unticked and says who.
            have_rows.append({"item_id": row["id"], "name": row["item"], "ticked": False, "reason": who(row)})
            continue
        match, _ok = _cooker._find_inventory_match(row["item"], inventory)
        reason = ""
        if match is not None:
            loc = match.get("location") or ""
            if loc == "freezer":
                reason = "in the freezer"
            elif match.get("source") == "grocery_checkoff":
                day = _household_date_of(match.get("created_at"), zone)
                reason = f"bought {_day_words(day, today)}" if day else _LOCATION_WORDS.get(loc, "")
            else:
                reason = _LOCATION_WORDS.get(loc, "")
        have_rows.append({"item_id": row["id"], "name": row["item"], "ticked": True, "reason": reason})

    return {
        "regulars": {"choices": reg_choices, "on_list": reg_on_list, "starter": starter},
        "spices": {"choices": sp_choices, "on_list": sp_on_list},
        "have": {"rows": have_rows},
    }


def add_regulars(names: list[str]) -> dict:
    """
    Step 1's "Add N to the list": each ticked regular goes on the list as a
    staple's line (staples._put_on_list — the same line a due staple gets,
    linked by staple_id, so buying it teaches the rhythm). A starter name
    that is not a staple yet becomes one first — "anything ticked becomes a
    staple". Never a second line: a name already on the list is left alone.
    Returns {"added": [{item_id, item}]} for the toast and its Undo.
    """
    from . import grocery as _grocery
    from . import staples as _staples

    wanted = []
    for raw in names or []:
        name = " ".join(str(raw or "").strip().split())
        if name and _grocery._merge_key(name) not in {_grocery._merge_key(w) for w in wanted}:
            wanted.append(name)
    for name in wanted:
        conn = get_conn()
        try:
            exists = _staples._find_by_name(conn, name)
        finally:
            conn.close()
        if exists is None:
            _staples.add_staple(name)
    added = []
    conn = get_conn()
    try:
        live_keys = {
            _grocery._merge_key(r["item"])
            for r in conn.execute(
                "SELECT item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'in_cart')",
                (household_id(),),
            ).fetchall()
        }
        for name in wanted:
            key = _grocery._merge_key(name)
            if key in live_keys:
                continue
            staple = _staples._find_by_name(conn, name)
            if staple is None:
                continue
            put = _staples._put_on_list(conn, staple)
            # The person ticked it, so it is theirs ("you added it today"),
            # not Pomona's "probably running low" suggestion — that wording
            # is for a line the app put there on its own.
            conn.execute(
                "UPDATE grocery_items SET added_by = ? WHERE id = ? AND household_id = ?",
                (acting_name(""), put["item_id"], household_id()),
            )
            live_keys.add(key)
            added.append({"item_id": put["item_id"], "item": put["item"]})
        conn.commit()
    finally:
        conn.close()
    return {"added": added}


def undo_add_regulars(item_ids: list[int]) -> dict:
    """Undo for step 1's add: the lines it just made come off again. Only a
    still-needed line linked to a staple — never something bought."""
    ids = [int(i) for i in (item_ids or []) if str(i).isdigit() or isinstance(i, int)]
    if not ids:
        return {"removed": 0}
    conn = get_conn()
    try:
        cur = conn.execute(
            f"DELETE FROM grocery_items WHERE household_id = ? AND status = 'needed' AND staple_id IS NOT NULL "
            f"AND id IN ({','.join('?' * len(ids))})",
            (household_id(), *ids),
        )
        conn.commit()
        return {"removed": cur.rowcount}
    finally:
        conn.close()
