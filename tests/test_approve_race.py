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
from app.tools import attendance, grocery, leftovers, meal_plans, recipes, weekly_plan


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


# ---------------------------------------------------------- harder adversarial cases


def test_three_way_race_still_has_exactly_one_winner():
    """
    Not just two callers — three, on the same plan, released together.
    The conditional UPDATE is a row-level guard, not a two-caller special
    case, so this should generalize with no extra work: exactly one
    winner, the other two honest no-ops, and the grocery lines still add
    up once each.
    """
    plan_id, beans, onions = _new_plan(0)
    barrier = threading.Barrier(3)
    results: list[dict | Exception] = [None, None, None]

    def go(i: int, name: str):
        try:
            barrier.wait(timeout=5)
            results[i] = tools.approve_weekly_plan(plan_id, approved_by=name)
        except Exception as e:  # pragma: no cover - surfaced below
            results[i] = e

    names = ["Emily", "Marcus", "Jordan"]
    threads = [threading.Thread(target=go, args=(i, n)) for i, n in enumerate(names)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    for r in results:
        if isinstance(r, Exception):
            raise r

    winners = [r for r in results if r["was_already_approved"] is False]
    losers = [r for r in results if r["was_already_approved"] is True]
    assert len(winners) == 1, results
    assert len(losers) == 2, results
    beans_rows = _grocery_row(beans)
    onions_rows = _grocery_row(onions)
    assert len(beans_rows) == 1 and beans_rows[0]["quantity"] == "1 tin", beans_rows
    assert len(onions_rows) == 1 and onions_rows[0]["quantity"] == "2", onions_rows
    assert _ledger_count(plan_id) == 2


def test_a_hard_conflict_race_never_runs_the_ingest_twice(monkeypatch):
    """
    One caller has already tapped "approve anyway" past a hard allergy
    clash, the other has not — the two are racing with different
    `confirm_hard_conflicts`. Whichever call the actual conditional UPDATE
    lets through, the ingest must run at most once: the un-confirmed
    caller's gate (run before the lock, off the un-locked read) either
    catches the clash and bails with needs_confirmation before ever
    reaching the transaction, or — in the one-in-a-million case its own
    docstring names — loses the UPDATE race after having confirmed
    nothing, which the guard still turns into a clean no-op.
    """
    plan_id, beans, onions = _new_plan(0)
    from app.tools import coordination as _coordination

    fake_conflict = [{
        "severity": "hard", "member": "Kid", "meal": beans, "restriction": "peanuts",
        "source": "dietary", "matched": beans, "date": _future_week_start(0),
        "component_category": "main",
    }]
    monkeypatch.setattr(
        _coordination, "check_plan_conflicts",
        lambda *a, **k: {"conflicts": fake_conflict, "note": "hard conflict", "settle": None, "soft_note": None},
    )

    barrier = threading.Barrier(2)
    results: list[dict | Exception] = [None, None]

    def go(i: int, name: str, confirm: bool):
        try:
            barrier.wait(timeout=5)
            results[i] = tools.approve_weekly_plan(plan_id, approved_by=name, confirm_hard_conflicts=confirm)
        except Exception as e:  # pragma: no cover - surfaced below
            results[i] = e

    t1 = threading.Thread(target=go, args=(0, "Emily", True))
    t2 = threading.Thread(target=go, args=(1, "Marcus", False))
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)
    for r in results:
        if isinstance(r, Exception):
            raise r

    # Never doubled, regardless of which caller the DB let through.
    beans_rows = _grocery_row(beans)
    onions_rows = _grocery_row(onions)
    assert len(beans_rows) <= 1 and (not beans_rows or beans_rows[0]["quantity"] == "1 tin"), beans_rows
    assert len(onions_rows) <= 1 and (not onions_rows or onions_rows[0]["quantity"] == "2"), onions_rows
    approved = [r for r in results if r.get("status") == "approved" and r["was_already_approved"] is False]
    assert len(approved) <= 1, results


def test_a_failure_inside_the_carry_over_step_rolls_back_the_whole_approval(monkeypatch):
    """
    Same check tests/test_swap_atomic.py and tests/test_drop_dish_atomic.py
    make for their own atomic transactions: an exception raised partway
    through must undo everything the transaction had written so far, not
    just skip the rest. `set_aside_carried_over_items` runs first inside
    _settle_weekly_plan_approval, so a failure there should leave the
    status flip itself rolled back too.
    """
    plan_id, beans, onions = _new_plan(0)

    def boom(*_a, **_kw):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(grocery, "set_aside_carried_over_items", boom)
    with pytest.raises(RuntimeError):
        tools.approve_weekly_plan(plan_id, approved_by="Emily")

    plan = tools.get_weekly_plan(plan_id)
    assert plan["status"] != "approved"
    assert _grocery_row(beans) == []
    assert _grocery_row(onions) == []


def test_a_failure_inside_the_grocery_ingest_rolls_back_the_whole_approval(monkeypatch):
    """
    Same as above, but the injected failure comes AFTER
    _add_recipe_ingredients_for_entries has already written its grocery
    lines and ledger rows for one recipe group — the rollback has to undo
    those writes too, not just the status flip that came before them.
    """
    plan_id, beans, onions = _new_plan(0)
    real = recipes._add_recipe_ingredients_for_entries

    def flaky(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(recipes, "_add_recipe_ingredients_for_entries", flaky)
    with pytest.raises(RuntimeError):
        tools.approve_weekly_plan(plan_id, approved_by="Emily")

    plan = tools.get_weekly_plan(plan_id)
    assert plan["status"] != "approved"
    assert _grocery_row(beans) == []
    assert _grocery_row(onions) == []


def test_reopen_racing_approve_leaves_no_torn_state():
    """
    approve_weekly_plan racing reopen_weekly_plan on the same plan — a
    different write than another approval, but still one competing for
    the same row's write lock. Neither call is guarded against the other
    (reopen has no conditional UPDATE of its own), so this isn't pinning
    one specific winner — only that SQLite's single-writer lock serializes
    the two instead of interleaving them: no exception escapes, the
    grocery lines from whichever approval actually ran are never doubled,
    and the plan ends up in one of the two states a serialized run could
    produce, not something in between.
    """
    plan_id, beans, onions = _new_plan(0)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    barrier = threading.Barrier(2)
    results: list[dict | Exception] = [None, None]

    def go_reopen():
        try:
            barrier.wait(timeout=5)
            results[0] = tools.reopen_weekly_plan(plan_id)
        except Exception as e:  # pragma: no cover - surfaced below
            results[0] = e

    def go_approve():
        try:
            barrier.wait(timeout=5)
            results[1] = tools.approve_weekly_plan(plan_id, approved_by="Marcus")
        except Exception as e:  # pragma: no cover - surfaced below
            results[1] = e

    t1 = threading.Thread(target=go_reopen)
    t2 = threading.Thread(target=go_approve)
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)
    for r in results:
        if isinstance(r, Exception):
            raise r

    beans_rows = _grocery_row(beans)
    onions_rows = _grocery_row(onions)
    assert len(beans_rows) == 1 and beans_rows[0]["quantity"] == "1 tin", beans_rows
    assert len(onions_rows) == 1 and onions_rows[0]["quantity"] == "2", onions_rows
    plan = tools.get_weekly_plan(plan_id)
    assert plan["status"] in ("draft", "approved"), plan["status"]


def test_swap_racing_approve_leaves_no_duplicate_entry():
    """
    approve_weekly_plan racing swap_meal_in_plan on an entry inside the
    very plan being approved — two already-atomic transactions (this
    fix's, and swap-atomic's own) competing for the same row's lock.
    SQLite's single-writer model serializes them either order; the point
    here is that neither order leaves a torn read behind: exactly one
    meal_plan_entries row for the slot survives, and every grocery line
    from whichever meal actually ended up on the plan lands exactly once.
    """
    plan_id, beans, onions = _new_plan(0)
    week = _future_week_start(0)
    tools.add_recipe("Tacos race", ingredients=[
        {"item": "tortillas race", "qty": "1 pkg"}, {"item": "salsa race", "qty": "1 jar"},
    ])

    barrier = threading.Barrier(2)
    results: list[dict | Exception] = [None, None]

    def go_approve():
        try:
            barrier.wait(timeout=5)
            results[0] = tools.approve_weekly_plan(plan_id, approved_by="Emily")
        except Exception as e:  # pragma: no cover - surfaced below
            results[0] = e

    def go_swap():
        try:
            barrier.wait(timeout=5)
            results[1] = tools.swap_meal_in_plan(
                plan_id, week, "Tacos race", slot="dinner", old_meal="Chili 0",
            )
        except Exception as e:  # pragma: no cover - surfaced below
            results[1] = e

    t1 = threading.Thread(target=go_approve)
    t2 = threading.Thread(target=go_swap)
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)
    for r in results:
        if isinstance(r, Exception):
            raise r

    conn = get_conn()
    entries = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)
    ).fetchall()
    conn.close()
    assert len(entries) == 1, entries  # no duplicate/torn entry row for the slot

    conn = get_conn()
    rows = {r["item"]: r["quantity"] for r in conn.execute(
        "SELECT item, quantity FROM grocery_items WHERE item IN (?, ?, 'tortillas race', 'salsa race')",
        (beans, onions),
    ).fetchall()}
    conn.close()
    assert rows.get(beans, "1 tin") == "1 tin", rows
    assert rows.get(onions, "2") == "2", rows
    assert rows.get("tortillas race", "1 pkg") == "1 pkg", rows
    assert rows.get("salsa race", "1 jar") == "1 jar", rows


_MODULES = (
    ("weekly_plan", weekly_plan), ("grocery", grocery), ("recipes", recipes),
    ("meal_plans", meal_plans), ("leftovers", leftovers), ("attendance", attendance),
)


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
    transaction's own. `leftovers`/`attendance` are watched too, not just
    the modules the ingest calls directly — servings_scale_factor and
    plan_leftover_chains/batch_for_source sit one level further down the
    same tree (an independent verifier's adversarial pass checked this).
    (approve_weekly_plan's own separate, pre-lock status read is
    deliberately not in scope here — see its docstring.)
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
    assert delta == {
        "weekly_plan": 1, "grocery": 0, "recipes": 0, "meal_plans": 0, "leftovers": 0, "attendance": 0,
    }, f"something inside the transaction opened its own connection: {delta}"
    assert _grocery_row(beans)[0]["quantity"] == "1 tin"
