"""
Two adults approving the week at the same instant buy everything twice
(Bug, Phase 0).

THE BUG. approve_weekly_plan had exactly the shape the repo has already
closed three times — atomic-period-takeover, swap-atomic, drop-dish-atomic —
just one level up: its two "safe to call twice" guards were each a READ on
one connection, acted on by a WRITE on a later, separate connection (or a
separate implicit transaction on the same one). The status flip into
'approved' committed on its own, and only THEN did the recipe-week grocery
ingest run — unguarded, off whatever `entries` a fresh, unlocked query
happened to return. Two calls that both read "not approved yet" before
either had written anything went on to ingest the whole week's groceries
once each. Measured with two threaded calls released by a barrier (a 0ms
gap): 6/6 trials doubled every grocery line; at a 20ms gap the first call
had already committed and 0/6 doubled — which is exactly why one impatient
tap never showed this, and two people tapping Approve within the same
render frame did.

THE FIX. Both guards now live on ONE conditional UPDATE
(`WHERE status != 'approved'`), and everything the transition does — the
carry-over set-aside (today's `set_aside_carried_over_items`), the
recipe-week grocery ingest, and the `approved_grocery_added/skipped` +
`carried_over_count` receipt — runs inside the SAME transaction as that
flip, on one connection, opened with an explicit `BEGIN IMMEDIATE`. A
caller whose UPDATE flips 0 rows (already approved, or lost the race for
the write lock) rolls back having written nothing and returns the same
"was_already_approved" shape a second, unhurried call always has.
"""
from __future__ import annotations

import datetime
import threading

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery, meal_plans, recipes, weekly_plan


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _future_week_start(index: int) -> str:
    """
    A week far enough out that no other trial's plan (also on a distinct
    future week) is ever "started" by grocery.set_aside_carried_over_items —
    see its own tests/test_grocery_carry_over.py: a plan whose period
    hasn't begun is not a leftover. Keeps each trial's grocery lines
    isolated from every other trial's, inside one test, without wiping the
    database between them.
    """
    return (_monday() + datetime.timedelta(weeks=10 + index)).isoformat()


def _new_plan(index: int) -> tuple[int, str, str]:
    """A fresh household-unique recipe, plan and day for one trial."""
    beans, onions = f"beans{index}", f"onions{index}"
    tools.add_recipe(
        f"Chili {index}",
        ingredients=[{"item": beans, "qty": "1 tin"}, {"item": onions, "qty": "2"}],
    )
    week = _future_week_start(index)
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(week, f"Chili {index}", slot="dinner", weekly_plan_id=plan_id)
    return plan_id, beans, onions


def _grocery_row(item: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, status FROM grocery_items WHERE item = ?", (item,)
    ).fetchall()
    conn.close()
    return rows


def _ledger_count(plan_id: int) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) c FROM meal_plan_grocery_links mpgl "
        "JOIN meal_plan_entries mpe ON mpe.id = mpgl.meal_plan_entry_id "
        "WHERE mpe.weekly_plan_id = ?",
        (plan_id,),
    ).fetchone()["c"]
    conn.close()
    return n


def _race(plan_id: int, names=("Emily", "Marcus")) -> list[dict]:
    """Two approvals released together by a barrier — a 0ms gap."""
    barrier = threading.Barrier(2)
    results: list[dict | Exception] = [None, None]

    def go(i: int, name: str):
        try:
            barrier.wait(timeout=5)
            results[i] = tools.approve_weekly_plan(plan_id, approved_by=name)
        except Exception as e:  # pragma: no cover - surfaced by the assertion below
            results[i] = e

    threads = [threading.Thread(target=go, args=(i, name)) for i, name in enumerate(names)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    for r in results:
        if isinstance(r, Exception):
            raise r
    return results


# ---------------------------------------------------------- the repro


def test_two_simultaneous_approvals_add_the_groceries_once():
    """
    The headline claim, measured rather than assumed: run the race for real
    (no monkeypatching, no forced ordering) across several trials and count
    how many double. This fails on the pre-fix code — 6/6 doubled at a 0ms
    gap when this was written — and passes here.
    """
    doubled = 0
    for trial in range(8):
        plan_id, beans, onions = _new_plan(trial)

        results = _race(plan_id)

        beans_rows = _grocery_row(beans)
        onions_rows = _grocery_row(onions)
        assert len(beans_rows) == 1, f"trial {trial}: {beans_rows}"
        assert len(onions_rows) == 1, f"trial {trial}: {onions_rows}"
        if beans_rows[0]["quantity"] != "1 tin" or onions_rows[0]["quantity"] != "2":
            doubled += 1
        # The ledger: one link per ingredient, not one per (ingredient, caller).
        assert _ledger_count(plan_id) == 2, f"trial {trial}: {results}"
    assert doubled == 0


def test_exactly_one_caller_does_the_work_and_the_other_is_the_honest_no_op():
    """
    Said from the callers' own side, not just the database's: of the two
    results, exactly one says `was_already_approved: False` (it did the
    ingest and can name what it added) and the other says True with EMPTY
    groceries_added — never two winners, never two losers, never a winner
    that also reports was_already_approved.
    """
    plan_id, beans, onions = _new_plan(0)

    results = _race(plan_id)

    flags = sorted(r["was_already_approved"] for r in results)
    assert flags == [False, True]
    winner = next(r for r in results if r["was_already_approved"] is False)
    loser = next(r for r in results if r["was_already_approved"] is True)
    assert sorted(winner["groceries_added"]) == sorted([beans, onions])
    assert loser["groceries_added"] == []
    assert loser["already_have_skipped"] == []
    # The receipt names whoever actually settled it — the loser's approver
    # name is the winner's, not its own (same rule test_re_approving_keeps_
    # the_original_approver in tests/test_tools.py pins for a slower race).
    assert loser["approved_by"] == winner["approved_by"]
    assert loser["approved_at"] == winner["approved_at"]
    plan = tools.get_weekly_plan(plan_id)
    assert plan["status"] == "approved"
    assert plan["approved_by"] == winner["approved_by"]


def test_the_carried_over_receipt_is_not_doubled_either():
    """
    Today's merge added set_aside_carried_over_items on the transition into
    approved, and carried_over_count on the result. Both are side effects
    of the same guard and have to be inside the same lock: an EARLIER,
    already-started plan is approved and left with an unbought line, then
    two callers race to approve a SECOND, also-started plan. Only the
    winner may report having set anything aside.
    """
    tools.add_recipe("Chicken curry", ingredients=[
        {"item": "chicken thighs", "qty": "2 lb"}, {"item": "onion", "qty": "2"},
    ])
    week_a = _monday().isoformat()
    plan_a = tools.create_weekly_plan(week_a)["weekly_plan_id"]
    tools.plan_meal(week_a, "Chicken curry", slot="dinner", weekly_plan_id=plan_a)
    tools.approve_weekly_plan(plan_a, approved_by="Emily")

    plan_b = tools.create_weekly_plan(week_a)["weekly_plan_id"]
    day_b = tools._week_dates(week_a)[1]
    tools.plan_meal(day_b, "Chicken curry", slot="dinner", weekly_plan_id=plan_b)

    results = _race(plan_b)

    winner = next(r for r in results if r["was_already_approved"] is False)
    loser = next(r for r in results if r["was_already_approved"] is True)
    assert winner["carried_over_count"] == 2
    assert {c["item"] for c in winner["carried_over"]} == {"chicken thighs", "onion"}
    assert loser["carried_over_count"] == 0
    assert loser["carried_over"] == []
    # Set aside exactly once: one 'carried' row per item, not two.
    conn = get_conn()
    carried = conn.execute(
        "SELECT item FROM grocery_items WHERE status = 'carried'"
    ).fetchall()
    conn.close()
    assert sorted(r["item"] for r in carried) == ["chicken thighs", "onion"]


# ---------------------------------------------------------- the guard's shape


def test_a_plain_re_approval_still_adds_nothing_and_keeps_the_receipt():
    """
    The slow, single-caller path the ticket says must still behave — no
    race at all, just calling it twice, one after the other."""
    plan_id, beans, onions = _new_plan(0)
    first = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert first["was_already_approved"] is False

    second = tools.approve_weekly_plan(plan_id, approved_by="Marcus")

    assert second["was_already_approved"] is True
    assert second["groceries_added"] == []
    assert second["approved_by"] == "Emily"
    assert _grocery_row(beans)[0]["quantity"] == "1 tin"
    assert _grocery_row(onions)[0]["quantity"] == "2"


_MODULES = (("weekly_plan", weekly_plan), ("grocery", grocery), ("recipes", recipes), ("meal_plans", meal_plans))


def test_the_transaction_opens_exactly_one_connection(monkeypatch):
    """
    The reason every helper down the grocery-ingest tree already took a
    `conn` (swap-atomic threaded it there first): SQLite gives one writer
    at a time, so anything inside this transaction that opened its own
    connection would sit behind its own lock and die of "database is
    locked" instead of failing deterministically. Pinned the same way
    tests/test_swap_atomic.py pins _replace_slot_entries: from entering
    _settle_weekly_plan_approval (the transaction) to leaving it, count
    every module-level get_conn() and require exactly one — the
    transaction's own. (approve_weekly_plan's own separate, pre-lock status
    read is deliberately not in scope here — see its docstring.)
    """
    plan_id, beans, onions = _new_plan(0)

    opened = {name: 0 for name, _ in _MODULES}
    for name, module in _MODULES:
        real = module.get_conn

        def counting(_name=name, _real=real):
            opened[_name] += 1
            return _real()

        monkeypatch.setattr(module, "get_conn", counting)

    marks = {}
    real_settle = weekly_plan._settle_weekly_plan_approval

    def marking(*args, **kwargs):
        marks["start"] = dict(opened)
        out = real_settle(*args, **kwargs)
        marks["end"] = dict(opened)
        return out

    monkeypatch.setattr(weekly_plan, "_settle_weekly_plan_approval", marking)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert set(marks) == {"start", "end"}, "the transaction never ran"
    delta = {k: marks["end"][k] - marks["start"][k] for k in opened}
    assert delta == {"weekly_plan": 1, "grocery": 0, "recipes": 0, "meal_plans": 0}, (
        f"something inside the transaction opened its own connection: {delta}"
    )
    assert _grocery_row(beans)[0]["quantity"] == "1 tin"
