"""
"Before you shop" — the pass that runs in front of sorting the list.

Loop Board 'Shop: "Before you shop" — regulars, then spices and oils,
then already-have-it, ending on Sort the list' (High, Phase 1).

This module is small on purpose: it owns ONE fact, which is whether the
household has already been through the pass for the week they are
shopping. Everything the steps themselves do already has a home —
staples in staples.py, spices in spices.py, the already-home flags in
pre_shop.py — and a second implementation of any of those is how two
screens end up disagreeing about one household's list.

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
from ._shared import household_id

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
