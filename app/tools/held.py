"""
Held things — what Pomona keeps when it can't act yet (Loop Board "'Noted'
must never note nothing", Emily, 2026-09-15; flow H1 in PRODUCT_FLOWS.md,
"Pomona, hold this", the founding anchor).

The gap this closes, from the 2026-09-15 walk: "We ended up going to the
in-laws for dinner last night" got "Noted — …" and nothing was saved. The
chat had every tool for things it CAN act on (a grocery line, a plan
change, a staple, a preference, a fact about the household) and no tool
at all for a thing it can't act on yet — so the model acknowledged and
dropped it, which is the one thing an offload must never do.

The shape is deliberately tiny:

    a held thing = the person's own words + who said it + when.

No category, no due date, no reminder. It is shown (Now's "Holding for
you" strip, the "Holding for you" section under What we know), handed to
the weekly planner as context so it can raise it at the right moment
("You mentioned Nana's coming the 28th — that's a Monday"), and taken off
the list with one tap ("Done with this"). It is never a nag: a thing that
never becomes useful just sits there. Household-scoped, like everything
else in this package — the other adult sees the same list.

Who said it is the session's adult (_shared.current_member — the
verified pick, never a name the model typed), recorded as members.id and
read back as a name on every listing so a renamed member still reads
right. When nobody is picked (a one-adult household resolves on its own;
a script or test has no pick) it is simply nobody's.
"""
from __future__ import annotations

from datetime import date

from ..db import get_conn
from ._shared import current_member, household_id, require_household_row

# The one line the chat says when it holds something. The system prompt
# quotes it too; the tool hands it back so the two can't drift apart.
HOLD_REPLY = "Holding that. I'll bring it up when it's useful."

# Longer than this and it isn't a thing to hold, it's a message — the
# model is told to paraphrase, and this keeps a runaway paste out of the
# Now strip.
MAX_TEXT_LEN = 280


def _today() -> date:
    # The household's own day (cooker.household_today), lazily imported:
    # cooker imports a lot, and this module is imported by tools/__init__.
    from .cooker import household_today

    return household_today()


def when_label(said_on: str, today: date | None = None) -> str:
    """
    "today" / "yesterday" / "Monday" (within the week) / "Sep 12" — the
    way a person would say when something was mentioned, never a
    timestamp (DESIGN_SYSTEM.md §8).
    """
    today = today or _today()
    try:
        day = date.fromisoformat(said_on)
    except (TypeError, ValueError):
        return ""
    delta = (today - day).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if delta < 7:
        return day.strftime("%A")
    label = day.strftime("%b %-d")
    if day.year != today.year:
        label += day.strftime(" %Y")
    return label


def _row_dict(row, today: date | None = None) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "member_id": row["member_id"],
        "said_by": (row["said_by"] or "").strip(),
        "said_on": row["said_on"],
        "when": when_label(row["said_on"], today),
    }


_SELECT = (
    "SELECT h.id, h.text, h.member_id, h.said_on, m.name AS said_by "
    "FROM held_things h LEFT JOIN members m ON m.id = h.member_id "
)


def hold_thing(text: str) -> dict:
    """
    Keep something the person said that Pomona can't act on yet, in their
    words. Returns the held row plus the one-line reply the chat should
    give. Blank text is refused (nothing is held, and the result says
    so). The same words already held and unresolved are not held twice —
    the existing row comes back with already_held=True.
    """
    words = " ".join((text or "").split())
    if not words:
        return {"held": False, "reason": "Nothing to hold — the text was empty."}
    if len(words) > MAX_TEXT_LEN:
        words = words[: MAX_TEXT_LEN - 1].rstrip() + "…"
    member = current_member()
    member_id = member["id"] if member else None
    today = _today()
    conn = get_conn()
    existing = conn.execute(
        _SELECT + "WHERE h.household_id = ? AND h.resolved_at IS NULL AND LOWER(h.text) = LOWER(?) "
        "ORDER BY h.id DESC LIMIT 1",
        (household_id(), words),
    ).fetchone()
    if existing is not None:
        conn.close()
        return {"held": True, "already_held": True, "reply": HOLD_REPLY, **_row_dict(existing, today)}
    cur = conn.execute(
        "INSERT INTO held_things (household_id, member_id, text, said_on) VALUES (?, ?, ?, ?)",
        (household_id(), member_id, words, today.isoformat()),
    )
    conn.commit()
    row = conn.execute(_SELECT + "WHERE h.id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return {"held": True, "already_held": False, "reply": HOLD_REPLY, **_row_dict(row, today)}


def list_held_things() -> list[dict]:
    """Everything the household has asked Pomona to hold and not yet finished with, most recently said first."""
    today = _today()
    conn = get_conn()
    rows = conn.execute(
        _SELECT + "WHERE h.household_id = ? AND h.resolved_at IS NULL ORDER BY h.said_on DESC, h.id DESC",
        (household_id(),),
    ).fetchall()
    conn.close()
    return [_row_dict(r, today) for r in rows]


def resolve_held_thing(held_id: int) -> dict:
    """"Done with this" — take one held thing off the list. Nothing is deleted, so a mis-tap can be put back (restore_held_thing)."""
    conn = get_conn()
    require_household_row(conn, "held_things", held_id, label="held thing")
    conn.execute(
        "UPDATE held_things SET resolved_at = datetime('now') WHERE id = ? AND household_id = ? AND resolved_at IS NULL",
        (held_id, household_id()),
    )
    conn.commit()
    row = conn.execute(_SELECT + "WHERE h.id = ?", (held_id,)).fetchone()
    conn.close()
    return {"resolved": True, "id": held_id, "text": row["text"] if row else ""}


def restore_held_thing(held_id: int) -> dict:
    """The Undo on "Done with this": put a resolved thing back on the list."""
    conn = get_conn()
    require_household_row(conn, "held_things", held_id, label="held thing")
    conn.execute(
        "UPDATE held_things SET resolved_at = NULL WHERE id = ? AND household_id = ?",
        (held_id, household_id()),
    )
    conn.commit()
    row = conn.execute(_SELECT + "WHERE h.id = ?", (held_id,)).fetchone()
    conn.close()
    return {"restored": True, **_row_dict(row)}


def generation_context() -> list[dict]:
    """
    What the weekly planner is handed (agent._generate_weekly_plan's
    `held_things`): each thing in the person's words with who said it and
    when, oldest first so a run of related remarks reads in order. Empty
    when nothing is held — the caller leaves the key off entirely then,
    the way `holidays` and `calendar` do.
    """
    items = list_held_things()
    items.reverse()
    return [
        {"said": h["text"], "who": h["said_by"] or None, "when": h["when"]}
        for h in items
    ]
