"""
The first-open tour (Loop Board "Build the in-app tour (Option A)", Emily,
2026-10-18): six spotlight stops over the household's first draft — Swap and
Tweak, Move and Pick, the Pomona button, then Shop, Cook and Today — ending
back on the draft. The stops themselves live in static/tour.js; this module
only answers "should it start?" and records "it's over".

WHO SEES IT, stated once:
  - Nobody whose household has ever had an approved week. That is the card's
    rule, and it is what keeps every household already using Pomona (Emily's
    included) from getting a walkthrough out of nowhere — a column that
    arrives as '' can't tell them apart, an approved week can.
  - Nobody who has finished or skipped it. Stored per PERSON (members),
    because the second adult opening their first draft hasn't seen it
    either; on the HOUSEHOLD only when the session resolves to nobody (more
    than one adult and no "Who's this?" pick yet). Server-side, not
    localStorage: a person who skipped it on their phone mustn't get it again
    on the laptop.
"Show me around again" in Preferences replays it for anyone, any time —
that is the shell's call and needs nothing from here.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import current_member, household_id

# What tour_ended_how can hold. '' means it hasn't ended for this person.
ENDED_DONE = "done"
ENDED_SKIPPED = "skipped"


def _has_approved_week(conn) -> bool:
    # approved_at as well as status: a week approved and later retired
    # (replaced, discarded) still means this household has been here before.
    row = conn.execute(
        "SELECT 1 FROM weekly_plans WHERE household_id = ? "
        "AND (status = 'approved' OR approved_at IS NOT NULL) LIMIT 1",
        (household_id(),),
    ).fetchone()
    return row is not None


def tour_state() -> dict:
    """Whether the tour should start on this person's draft, and why not."""
    member = current_member()
    conn = get_conn()
    try:
        if member:
            row = conn.execute(
                "SELECT tour_ended_how FROM members WHERE id = ? AND household_id = ?",
                (member["id"], household_id()),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT tour_ended_how FROM households WHERE id = ?", (household_id(),)
            ).fetchone()
        ended = (row["tour_ended_how"] if row else "") or ""
        approved = _has_approved_week(conn)
    finally:
        conn.close()
    return {
        "show": not ended and not approved,
        "ended": ended,
        "has_approved_week": approved,
    }


def end_tour(skipped: bool) -> dict:
    """
    The tour was finished or skipped: never start it on its own again for
    this person (or household, with nobody picked). The FIRST ending is the
    one kept — a replay from Preferences that is skipped halfway doesn't
    rewrite a tour that was once watched to the end.
    """
    how = ENDED_SKIPPED if skipped else ENDED_DONE
    member = current_member()
    conn = get_conn()
    try:
        if member:
            conn.execute(
                "UPDATE members SET tour_ended_how = ?, tour_ended_at = datetime('now') "
                "WHERE id = ? AND household_id = ? AND tour_ended_how = ''",
                (how, member["id"], household_id()),
            )
        else:
            conn.execute(
                "UPDATE households SET tour_ended_how = ?, tour_ended_at = datetime('now') "
                "WHERE id = ? AND tour_ended_how = ''",
                (how, household_id()),
            )
        conn.commit()
    finally:
        conn.close()
    return tour_state()
