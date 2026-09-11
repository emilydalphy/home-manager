"""
Staples — the things a household buys on a rhythm, put on the list before
they run out (Loop Board "Staples: tell me before we run out", Emily,
2026-09-11).

This is the first piece of Pomona built directly against the mental load
model: "keeping track of when we need more" is the research's own example
of anticipation load, and it is the phase that falls on one person. So the
app takes the noticing. It does NOT take on inventory to do it — that is
policy (2026-09-01, restated 2026-09-11: inventory stays an in-development
beta feature, and nobody does inventory work to complete a loop). There
are no counts and no locations here, and nothing in this module reads or
writes `inventory_items`.

The shape:

    a staple  = an item + a cadence ("about every three weeks") + when it
                was last bought, from which "probably due" follows.
    an event  = one dated fact about a staple: bought, plenty, skipped.
                Cadence is LEARNED from the bought dates once there are
                enough of them (two intervals); before that a per-category
                default applies, or whatever the household said.

When a staple is due, sync_due_staples() puts one ordinary grocery line on
the list — the same add path a typed item uses — and links it back with
grocery_items.staple_id so the Grocery screen can show it as a suggestion
("probably running low") with the two one-tap answers that matter:
"we have plenty" (pushes the next due date a whole cadence out) and "not
this trip" (a week). Keeping it is doing nothing: it is on the list.
Buying it, by any route, is what teaches the cadence.

Learning etiquette (DESIGN_SYSTEM.md §7): the suggestion is silent
learning with a visible flag and an undo at the point of use — the
reference pattern. It gets quieter on its own only in one way: three
skips running pauses the staple, and the pause is shown in the Staples
card with one-tap resume, never hidden.

Dates are ISO strings (YYYY-MM-DD) throughout, compared as text, which is
safe for that format. "Today" comes from _today() so tests can pin it.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from ..db import get_conn
from ._shared import household_id
from . import grocery as _grocery
from . import quantities as _quantities

# How often a household buys a thing before Pomona has seen it buy it.
# Days. Deliberately coarse — the point of a default is only to make the
# first suggestion land somewhere plausible; the learned cadence replaces
# it after two real intervals.
DEFAULT_CADENCE_DAYS = {
    "produce": 7,
    "dairy": 7,
    "meat/seafood": 14,
    "meat": 14,
    "seafood": 14,
    "frozen": 21,
    "pantry": 21,
    "household": 30,
    "other": 21,
}
FALLBACK_CADENCE_DAYS = 21

# A learned cadence is clamped to this range. Under three days it is a
# daily run, not a staple; over 120 it is a once-a-season purchase that
# a list suggestion would only ever get wrong.
MIN_CADENCE_DAYS = 3
MAX_CADENCE_DAYS = 120

# "Not this trip" comes back in a week — the next trip for most households
# — rather than waiting a whole cadence, because the person said "not
# now", not "we have enough".
SKIP_DAYS = 7

# Three skips in a row pause the staple (see pause_staple). Visible in the
# Staples card with a one-tap resume.
SKIPS_BEFORE_PAUSE = 3

# How many bought intervals it takes before the cadence is learned rather
# than defaulted or told. One interval is a coincidence; two is a rhythm.
INTERVALS_TO_LEARN = 2

# Written into grocery_items.added_by for a line the app put there itself,
# so the screen can tell "Pomona thinks you're low" from "somebody added
# this" even after staple_id is gone.
ADDED_BY_STAPLE = "staple"

_TODAY_OVERRIDE: date | None = None


def _today() -> date:
    return _TODAY_OVERRIDE or date.today()


def _iso(d: date) -> str:
    return d.isoformat()


def _parse(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


_KNOWN_CATEGORIES = set(_quantities._GROCERY_SECTION_ORDER) | {"household"}


def _clean_category(category: str | None) -> str:
    """A grocery section name, or "other" — the API takes a free string and
    the screen only knows these."""
    cat = (category or "other").strip().lower()
    cat = _quantities._GROCERY_CATEGORY_ALIASES.get(cat, cat)
    return cat if cat in _KNOWN_CATEGORIES else "other"


def _default_cadence(category: str) -> int:
    return DEFAULT_CADENCE_DAYS.get((category or "other").strip().lower(), FALLBACK_CADENCE_DAYS)


def _clamp(days: int) -> int:
    return max(MIN_CADENCE_DAYS, min(MAX_CADENCE_DAYS, int(days)))


def _cadence_words(days: int) -> str:
    """"About every week" / "about every 3 weeks" / "about every 2 months" —
    how a person says it, not a number of days."""
    if days <= 1:
        return "about every day"
    if days < 10:
        return "about every week" if days >= 6 else f"about every {days} days"
    weeks = round(days / 7)
    if weeks <= 1:
        return "about every week"
    if weeks <= 3:
        return f"about every {weeks} weeks"
    months = max(1, round(days / 30))
    return "about every month" if months <= 1 else f"about every {months} months"


def _due_words(next_due: str) -> str:
    d = _parse(next_due)
    if d is None:
        return ""
    today = _today()
    delta = (d - today).days
    if delta <= 0:
        return "probably running low"
    if delta == 1:
        return "due tomorrow"
    if delta < 7:
        return f"due {d.strftime('%A')}"
    if delta < 14:
        return "due next week"
    return f"due in {round(delta / 7)} weeks"


def _row(conn, staple_id: int):
    return conn.execute(
        "SELECT * FROM staples WHERE id = ? AND household_id = ?", (staple_id, household_id())
    ).fetchone()


def _find_by_name(conn, item: str):
    """The staple whose name is the same thing as `item`, on the grocery
    list's own merge key — so "Coffee beans" and "coffee bean" are one
    staple, exactly as they would be one grocery line."""
    wanted = _grocery._merge_key(item)
    if not wanted:
        return None
    rows = conn.execute("SELECT * FROM staples WHERE household_id = ?", (household_id(),)).fetchall()
    return next((r for r in rows if _grocery._merge_key(r["item"]) == wanted), None)


def _shape(r) -> dict:
    next_due = r["next_due_at"]
    return {
        "id": r["id"],
        "item": r["item"],
        "category": r["category"],
        "quantity": r["quantity"] or "",
        "cadence_days": r["cadence_days"],
        "cadence_source": r["cadence_source"],
        "cadence_words": _cadence_words(r["cadence_days"]),
        "last_bought_at": r["last_bought_at"],
        "next_due_at": next_due,
        "due": bool(r["paused"] == 0 and next_due <= _iso(_today())),
        "due_words": "paused" if r["paused"] else _due_words(next_due),
        "skip_streak": r["skip_streak"],
        "paused": bool(r["paused"]),
    }


def _event(conn, staple_id: int, kind: str, source: str, on_date: str | None = None) -> None:
    conn.execute(
        "INSERT INTO staple_events (household_id, staple_id, kind, source, on_date) VALUES (?, ?, ?, ?, ?)",
        (household_id(), staple_id, kind, source, on_date or _iso(_today())),
    )


def _relearn(conn, staple_id: int) -> None:
    """
    Recompute the cadence from the bought dates, and the next due date from
    the last one. Median of the intervals, not the mean — one long gap (a
    holiday, a bulk buy) should not drag the rhythm with it. Nothing is
    learned from fewer than INTERVALS_TO_LEARN intervals: a told or default
    cadence stands until the household has actually shown one.
    """
    r = _row(conn, staple_id)
    if r is None:
        return
    dates = sorted({
        row["on_date"][:10]
        for row in conn.execute(
            "SELECT on_date FROM staple_events WHERE household_id = ? AND staple_id = ? AND kind = 'bought'",
            (household_id(), staple_id),
        ).fetchall()
    })
    parsed = [d for d in (_parse(s) for s in dates) if d is not None]
    intervals = [(b - a).days for a, b in zip(parsed, parsed[1:]) if (b - a).days > 0]
    cadence = r["cadence_days"]
    source = r["cadence_source"]
    if len(intervals) >= INTERVALS_TO_LEARN:
        cadence = _clamp(round(statistics.median(intervals)))
        source = "learned"
    last_bought = _iso(parsed[-1]) if parsed else r["last_bought_at"]
    anchor = _parse(last_bought) or _parse(r["created_at"]) or _today()
    next_due = _iso(anchor + timedelta(days=cadence))
    conn.execute(
        "UPDATE staples SET cadence_days = ?, cadence_source = ?, last_bought_at = ?, next_due_at = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (cadence, source, last_bought, next_due, staple_id),
    )


def _seed_history(conn, staple_id: int, item: str) -> int:
    """
    What Pomona already knows about buying this, so a household that has
    been shopping with it for weeks doesn't start from a default. Purchased
    grocery lines carry no purchase timestamp — only when the line was
    made — so each purchased line's created_at counts as ONE approximate
    bought date. That is honest enough to learn a cadence from (it is off
    by the few days between listing and buying, the same way every time)
    and is never used for anything finer than "about every N weeks".
    Returns how many dates were seeded.
    """
    wanted = _grocery._merge_key(item)
    rows = conn.execute(
        "SELECT item, created_at FROM grocery_items WHERE household_id = ? AND status = 'purchased'",
        (household_id(),),
    ).fetchall()
    seen = set()
    for row in rows:
        if _grocery._merge_key(row["item"]) != wanted:
            continue
        d = (row["created_at"] or "")[:10]
        if not d or d in seen:
            continue
        seen.add(d)
        _event(conn, staple_id, "bought", "seed", d)
    return len(seen)


# ---------------------------------------------------------------- tools ----


def add_staple(
    item: str,
    every_days: int | None = None,
    quantity: str = "",
    category: str = "other",
    running_low: bool = False,
) -> dict:
    """
    Remember something the household buys on a rhythm — "we always get
    coffee", "we go through dish soap about every month", "add toilet paper
    to our staples". Food or not. Pomona will put it on the grocery list
    just before it's probably due, and learn the real rhythm from when it
    gets bought. every_days is only for a rhythm the person actually said
    (a month = 30, a fortnight = 14); leave it unset otherwise and a sensible
    default applies until the rhythm is learned. Set running_low=True when
    they say they're out or nearly out now — it goes on the list today.
    Never creates or reads inventory. If the staple already exists, this
    updates it rather than making a second one.
    """
    name = " ".join((item or "").strip().split())
    if not name:
        raise ValueError("A staple needs a name.")
    cat = _clean_category(category)
    conn = get_conn()
    existing = _find_by_name(conn, name)
    today = _iso(_today())
    if existing:
        staple_id = existing["id"]
        fields, params = [], []
        if every_days:
            fields += ["cadence_days = ?", "cadence_source = 'told'"]
            params.append(_clamp(every_days))
        if quantity:
            fields.append("quantity = ?")
            params.append(quantity)
        if category and category != "other":
            fields.append("category = ?")
            params.append(cat)
        if running_low:
            fields.append("next_due_at = ?")
            params.append(today)
        fields += ["paused = 0", "skip_streak = 0", "updated_at = datetime('now')"]
        conn.execute(f"UPDATE staples SET {', '.join(fields)} WHERE id = ?", (*params, staple_id))
        created = False
    else:
        cadence = _clamp(every_days) if every_days else _default_cadence(cat)
        source = "told" if every_days else "default"
        # Adding a staple you're not out of means you have some now, so
        # the first due date is a cadence away. Running low means today.
        next_due = today if running_low else _iso(_today() + timedelta(days=cadence))
        cur = conn.execute(
            "INSERT INTO staples (household_id, item, category, quantity, cadence_days, cadence_source, "
            "last_bought_at, next_due_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (household_id(), name, cat, quantity or "", cadence, source, None if running_low else today, next_due),
        )
        staple_id = cur.lastrowid
        _event(conn, staple_id, "added", "chat")
        seeded = _seed_history(conn, staple_id, name)
        if seeded >= INTERVALS_TO_LEARN + 1:
            # Enough history to learn from: let the learned cadence and
            # the real last-bought date set next_due, unless they said
            # they're low right now.
            _relearn(conn, staple_id)
            if running_low:
                conn.execute("UPDATE staples SET next_due_at = ? WHERE id = ?", (today, staple_id))
        created = True
    conn.commit()
    out = _shape(_row(conn, staple_id))
    conn.close()
    out["created"] = created
    return out


def list_staples() -> list[dict]:
    """
    Everything the household has told Pomona it buys on a rhythm, with each
    one's cadence in words, when it was last bought, when it's probably due,
    and whether it's paused. Use this to answer "what are our staples?" and
    before adding one that might already be there.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM staples WHERE household_id = ? ORDER BY paused, next_due_at, item",
        (household_id(),),
    ).fetchall()
    conn.close()
    return [_shape(r) for r in rows]


def remove_staple(item: str) -> dict:
    """
    Stop treating something as a staple — "we don't buy that any more". The
    staple and its history are deleted; a line already on the grocery list
    for it is left alone (it is still a thing to buy this trip).
    """
    conn = get_conn()
    r = _find_by_name(conn, item)
    if r is None:
        conn.close()
        return {"found": False, "item": item}
    conn.execute("UPDATE grocery_items SET staple_id = NULL WHERE household_id = ? AND staple_id = ?", (household_id(), r["id"]))
    conn.execute("DELETE FROM staple_events WHERE household_id = ? AND staple_id = ?", (household_id(), r["id"]))
    conn.execute("DELETE FROM staples WHERE id = ?", (r["id"],))
    conn.commit()
    conn.close()
    return {"found": True, "item": r["item"], "removed": True}


def mark_staple_plenty(item: str) -> dict:
    """
    "We've got plenty of coffee" — Pomona was early. Pushes the next due
    date a whole cadence out from today and takes any suggestion line for
    it off the grocery list. Doesn't change the learned rhythm.
    """
    conn = get_conn()
    r = _find_by_name(conn, item)
    if r is None:
        conn.close()
        return {"found": False, "item": item}
    out = _plenty(conn, r["id"], source="chat")
    conn.commit()
    conn.close()
    out["found"] = True
    return out


def _plenty(conn, staple_id: int, source: str, drop_lines: bool = True) -> dict:
    r = _row(conn, staple_id)
    next_due = _iso(_today() + timedelta(days=r["cadence_days"]))
    conn.execute(
        "UPDATE staples SET next_due_at = ?, skip_streak = 0, updated_at = datetime('now') WHERE id = ?",
        (next_due, staple_id),
    )
    _event(conn, staple_id, "plenty", source)
    removed = _drop_suggestion_lines(conn, staple_id) if drop_lines else None
    out = _shape(_row(conn, staple_id))
    out["removed_line"] = removed
    return out


def _skip(conn, staple_id: int, source: str, drop_lines: bool = True) -> dict:
    r = _row(conn, staple_id)
    streak = (r["skip_streak"] or 0) + 1
    paused = 1 if streak >= SKIPS_BEFORE_PAUSE else 0
    next_due = _iso(_today() + timedelta(days=SKIP_DAYS))
    conn.execute(
        "UPDATE staples SET next_due_at = ?, skip_streak = ?, paused = ?, updated_at = datetime('now') WHERE id = ?",
        (next_due, streak, paused, staple_id),
    )
    _event(conn, staple_id, "skipped", source)
    if paused:
        _event(conn, staple_id, "paused", "auto")
    removed = _drop_suggestion_lines(conn, staple_id) if drop_lines else None
    out = _shape(_row(conn, staple_id))
    out["removed_line"] = removed
    out["just_paused"] = bool(paused)
    return out


def _drop_suggestion_lines(conn, staple_id: int) -> dict | None:
    """
    Take the still-needed line(s) Pomona itself put on the list for this
    staple off the list — softly, the way a pre-shop "Drop it" does (status
    'removed', removed_at stamped), so an Undo puts back the very same row
    with its store and quantity intact, and so sync_due_staples can see
    that this trip already has an answer. A line a person added (no
    staple_id) is never touched here.
    """
    rows = conn.execute(
        "SELECT id, item, quantity, category, store FROM grocery_items "
        "WHERE household_id = ? AND staple_id = ? AND status = 'needed'",
        (household_id(), staple_id),
    ).fetchall()
    if not rows:
        return None
    conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
        "WHERE household_id = ? AND staple_id = ? AND status = 'needed'",
        (ADDED_BY_STAPLE, household_id(), staple_id),
    )
    first = rows[0]
    return {"item": first["item"], "quantity": first["quantity"] or "", "category": first["category"], "store": first["store"] or ""}


def note_line_removed(conn, line_row, how: str) -> None:
    """
    A person took a staple's line off the list by some OTHER route — the
    row's ⋯ → Remove, or the pre-shop "Drop it" — rather than the two
    staple buttons. Without this the next list read would put the line
    straight back, because the staple is still due (the boomerang the
    2026-09-11 verifier found). Remove means "not this trip"; a pre-shop
    drop means "we already have it". Called with the row (must carry
    staple_id) inside the caller's connection, before the caller's own
    write; commits nothing itself.
    """
    if line_row is None or not line_row["staple_id"]:
        return
    if _row(conn, line_row["staple_id"]) is None:
        return
    # drop_lines=False: the caller owns the row and writes it itself; this
    # only moves the staple's dates and streak.
    if how == "plenty":
        _plenty(conn, line_row["staple_id"], source="tap", drop_lines=False)
    else:
        _skip(conn, line_row["staple_id"], source="tap", drop_lines=False)


def decide_staple_line(item_id: int, decision: str) -> dict:
    """
    The two one-tap answers on a "probably running low" line on the Grocery
    screen: 'plenty' (we have enough — next due a whole cadence from today)
    or 'skip' (not this trip — ask again in a week; three in a row pauses
    the staple). Either takes the line off the list. Keeping it needs no
    tap: it's on the list.
    """
    if decision not in ("plenty", "skip"):
        raise ValueError("decision must be 'plenty' or 'skip'.")
    conn = get_conn()
    line = conn.execute(
        "SELECT id, item, staple_id FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id())
    ).fetchone()
    if line is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    staple = _row(conn, line["staple_id"]) if line["staple_id"] else _find_by_name(conn, line["item"])
    if staple is None:
        conn.close()
        raise ValueError(f"{line['item']} isn't a staple.")
    out = _plenty(conn, staple["id"], "tap") if decision == "plenty" else _skip(conn, staple["id"], "tap")
    conn.commit()
    conn.close()
    out["decision"] = decision
    return out


def undo_staple_decision(staple_id: int) -> dict:
    """
    Put the line back and reverse the last plenty/skip on this staple —
    the Undo on the toast. The event is deleted, the skip streak and pause
    are recomputed from what's left, and the due date goes back to today so
    the next list read re-suggests it.
    """
    conn = get_conn()
    r = _row(conn, staple_id)
    if r is None:
        conn.close()
        raise ValueError(f"No staple with id {staple_id}.")
    last = conn.execute(
        "SELECT id, kind FROM staple_events WHERE household_id = ? AND staple_id = ? AND kind IN ('plenty', 'skipped') "
        "ORDER BY id DESC LIMIT 1",
        (household_id(), staple_id),
    ).fetchone()
    if last is None:
        # Nothing to undo: say so, change nothing. (A raw call used to
        # force the staple due — the 2026-09-11 verifier's case g.)
        out = _shape(r)
        conn.close()
        out["undone"] = False
        return out
    conn.execute("DELETE FROM staple_events WHERE id = ?", (last["id"],))
    if last["kind"] == "skipped":
        # The auto-pause that may have come with that skip goes too.
        conn.execute(
            "DELETE FROM staple_events WHERE id = (SELECT id FROM staple_events WHERE household_id = ? AND staple_id = ? "
            "AND kind = 'paused' AND source = 'auto' ORDER BY id DESC LIMIT 1)",
            (household_id(), staple_id),
        )
    streak = max(0, (r["skip_streak"] or 0) - (1 if last["kind"] == "skipped" else 0))
    conn.execute(
        "UPDATE staples SET next_due_at = ?, skip_streak = ?, paused = 0, updated_at = datetime('now') WHERE id = ?",
        (_iso(_today()), streak, staple_id),
    )
    # Put back the very row that was taken off — store, quantity and all —
    # rather than letting the next list read make a new, blank one.
    restored = conn.execute(
        "SELECT id FROM grocery_items WHERE household_id = ? AND staple_id = ? AND status = 'removed' "
        "ORDER BY removed_at DESC, id DESC LIMIT 1",
        (household_id(), staple_id),
    ).fetchone()
    if restored:
        conn.execute(
            "UPDATE grocery_items SET status = 'needed', removed_by = '', removed_at = NULL WHERE id = ?",
            (restored["id"],),
        )
    conn.commit()
    conn.close()
    if not restored:
        sync_due_staples()
    conn = get_conn()
    out = _shape(_row(conn, staple_id))
    conn.close()
    out["undone"] = True
    return out


def pause_staple(staple_id: int, paused: bool = True) -> dict:
    """Pause (stop suggesting) or resume a staple by id. Resuming resets the
    skip streak and makes it due today if its date has passed."""
    conn = get_conn()
    r = _row(conn, staple_id)
    if r is None:
        conn.close()
        raise ValueError(f"No staple with id {staple_id}.")
    if paused:
        conn.execute("UPDATE staples SET paused = 1, updated_at = datetime('now') WHERE id = ?", (staple_id,))
        _event(conn, staple_id, "paused", "tap")
        _drop_suggestion_lines(conn, staple_id)
    else:
        conn.execute(
            "UPDATE staples SET paused = 0, skip_streak = 0, updated_at = datetime('now') WHERE id = ?", (staple_id,)
        )
        _event(conn, staple_id, "resumed", "tap")
    conn.commit()
    out = _shape(_row(conn, staple_id))
    conn.close()
    return out


def remove_staple_by_id(staple_id: int) -> dict:
    conn = get_conn()
    r = _row(conn, staple_id)
    if r is None:
        conn.close()
        raise ValueError(f"No staple with id {staple_id}.")
    name = r["item"]
    conn.close()
    return remove_staple(name)


def record_staple_purchase(item: str, source: str = "grocery", staple_id: int | None = None) -> dict | None:
    """
    Something got bought. Called from mark_grocery_item on every line that
    turns 'purchased', whatever put it there — so a staple a person added
    by hand still teaches the rhythm. No-op for anything that isn't a
    staple. One bought date per day per staple: a receipt and a tick for the
    same thing on the same day are one purchase.
    """
    conn = get_conn()
    r = _row(conn, staple_id) if staple_id else None
    if r is None:
        r = _find_by_name(conn, item)
    if r is None:
        conn.close()
        return None
    today = _iso(_today())
    already = conn.execute(
        "SELECT 1 FROM staple_events WHERE household_id = ? AND staple_id = ? AND kind = 'bought' AND on_date = ?",
        (household_id(), r["id"], today),
    ).fetchone()
    if not already:
        _event(conn, r["id"], "bought", source, today)
    conn.execute(
        "UPDATE staples SET last_bought_at = ?, skip_streak = 0, updated_at = datetime('now') WHERE id = ?",
        (today, r["id"]),
    )
    _relearn(conn, r["id"])
    conn.commit()
    out = _shape(_row(conn, r["id"]))
    conn.close()
    return out


def sync_due_staples() -> dict:
    """
    Put every due, unpaused staple on the grocery list as one ordinary line
    — unless something by that name is already on the list (needed or in
    the cart), in which case the household has it covered and Pomona says
    nothing. Idempotent; safe to call on every read of the list, which is
    where it is called from (get_grocery_list_by_section and
    get_grocery_list_by_store, for status 'needed').
    """
    conn = get_conn()
    today = _iso(_today())
    due = conn.execute(
        "SELECT * FROM staples WHERE household_id = ? AND paused = 0 AND next_due_at <= ? ORDER BY item",
        (household_id(), today),
    ).fetchall()
    if not due:
        conn.close()
        return {"added": []}
    live = conn.execute(
        "SELECT item, staple_id FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'in_cart')",
        (household_id(),),
    ).fetchall()
    live_keys = {_grocery._merge_key(row["item"]) for row in live}
    live_staple_ids = {row["staple_id"] for row in live if row["staple_id"]}
    # A line taken off today — by the staple buttons, ⋯ → Remove, or a
    # pre-shop drop — is this trip's answer; don't ask again until
    # tomorrow at the earliest. Older removed lines are just leftovers of
    # earlier answers, and go.
    # One clock: the module's _today(), not SQLite's UTC date('now').
    # removed_at is stamped in UTC, so near midnight a line can count as
    # "today" for a few extra hours — which errs toward staying quiet.
    conn.execute(
        "DELETE FROM grocery_items WHERE household_id = ? AND staple_id IS NOT NULL AND status = 'removed' "
        "AND date(removed_at) < ?",
        (household_id(), today),
    )
    answered_today = {
        row["staple_id"]
        for row in conn.execute(
            "SELECT staple_id FROM grocery_items WHERE household_id = ? AND staple_id IS NOT NULL AND status = 'removed'",
            (household_id(),),
        ).fetchall()
    }
    added = []
    for s in due:
        if s["id"] in live_staple_ids or s["id"] in answered_today or _grocery._merge_key(s["item"]) in live_keys:
            continue
        res = _grocery.add_grocery_item(
            s["item"], quantity=s["quantity"] or "", category=s["category"], added_by=ADDED_BY_STAPLE, conn=conn
        )
        conn.execute("UPDATE grocery_items SET staple_id = ? WHERE id = ?", (s["id"], res["item_id"]))
        added.append({"item_id": res["item_id"], "item": s["item"], "staple_id": s["id"]})
    conn.commit()
    conn.close()
    return {"added": added}
