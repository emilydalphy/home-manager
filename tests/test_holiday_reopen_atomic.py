"""
Changing a holiday answer hands the dinner back as a question in ONE
transaction (Loop Board bug, filed and built 2026-09-18).

`holidays._reopen` made the same pair of writes slot_needs and tonight.py
make — clear whatever is in the slot, then state the question — as TWO
COMMITS. The gap between them is a genuinely ABSENT slot: the one state
schema.sql, audit_plan_slots and plan_slot_open's own docstring all say
cannot exist. A failure in the gap left the day with no dinner row at all,
its grocery line already reversed, while answer_holiday raised and the
screen said nothing had been saved, which was false.

The catches below force a failure inside the SECOND write and then look at
what is left. Every one of them is red against the unmodified app; the
guards say so in their own docstrings and name the mutation that pins them
instead.
"""
import threading
from datetime import date, timedelta

import pytest

from app import tools
from app.db import get_conn
from app.tools import holidays as hol
from app.tools import weekly_plan as wp


# ---------- fixtures ----------

@pytest.fixture
def family():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Vineeth")
    tools.set_member_age_group("Vineeth", "adult")


@pytest.fixture
def recipe():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Sweet Potato Casserole", ingredients=[{"item": "sweet potatoes", "qty": "4"}],
                     prep_time_minutes=20, cook_time_minutes=40, default_servings=2)


@pytest.fixture
def approved_week(family, recipe):
    """An approved week with a real dinner on Thanksgiving and its real shopping line."""
    tg = _thanksgiving()
    plan = tools.create_weekly_plan(tg)
    pid = plan["weekly_plan_id"]
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    assert "beans" in _list(), "premise: the dinner really did reach the list"
    assert _rows(pid, tg) == [("planned", "Chili")], "premise: one planned dinner"
    return pid, tg


# ---------- the catches ----------

def test_a_failure_in_the_second_write_leaves_the_dinner_exactly_as_it_was(approved_week, monkeypatch):
    """
    CATCH — red against the unmodified app, where this leaves `[]`.

    The whole bug in one assertion: the slot must never be observable with
    no row on it.
    """
    pid, tg = approved_week
    monkeypatch.setattr(wp, "plan_slot_open", _boom)

    with pytest.raises(RuntimeError):
        hol._reopen(pid, tg, "Thanksgiving")

    assert _rows(pid, tg) == [("planned", "Chili")]


def test_a_failure_in_the_second_write_leaves_the_approved_weeks_shopping_line(approved_week, monkeypatch):
    """
    CATCH — red against the unmodified app, where the line is gone.

    This is the half with teeth: clear_plan_slot reverses the grocery
    contribution, so before the fix a crash took an approved week's line off
    the list for food the household may already have bought.
    """
    pid, tg = approved_week
    before = sorted(_list())
    monkeypatch.setattr(wp, "plan_slot_open", _boom)

    with pytest.raises(RuntimeError):
        hol._reopen(pid, tg, "Thanksgiving")

    assert sorted(_list()) == before
    assert "beans" in _list()


def test_a_failure_in_the_second_write_leaves_the_ledger_alone(approved_week, monkeypatch):
    """CATCH — the per-meal ledger is what a later reversal re-derives the line from."""
    pid, tg = approved_week
    before = _ledger_rows()
    assert before, "premise: the dinner has a ledger row behind it"
    monkeypatch.setattr(wp, "plan_slot_open", _boom)

    with pytest.raises(RuntimeError):
        hol._reopen(pid, tg, "Thanksgiving")

    assert _ledger_rows() == before


def test_a_failure_in_the_second_write_leaves_the_audit_clean(approved_week, monkeypatch):
    """
    CATCH — audit_plan_slots is the app's own statement of what cannot exist,
    and before the fix that date's dinner was in its `missing` list.
    """
    pid, tg = approved_week
    monkeypatch.setattr(wp, "plan_slot_open", _boom)

    with pytest.raises(RuntimeError):
        hol._reopen(pid, tg, "Thanksgiving")

    audit = tools.audit_plan_slots(pid)
    assert {"date": tg, "slot": "dinner"} not in audit["missing"]
    assert {"date": tg, "slot": "dinner"} not in audit["duplicated"]


def test_the_real_door_leaves_the_dinner_alone_when_the_second_write_fails(approved_week, monkeypatch):
    """
    CATCH, through the door a household actually taps: answering the holiday
    with a dish and then changing the answer runs _undo_effects -> _reopen.
    """
    pid, tg = approved_week
    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")
    assert _rows(pid, tg) == [("planned", "Sweet Potato Casserole")], "premise: the dish is on the day"
    monkeypatch.setattr(wp, "plan_slot_open", _boom)

    with pytest.raises(RuntimeError):
        tools.answer_holiday(tg, "unsure")

    assert _rows(pid, tg) == [("planned", "Sweet Potato Casserole")]


# ---------- the happy path, unchanged ----------

def test_the_slot_still_comes_back_as_a_question(approved_week):
    """
    GUARD — green either way. Pinned by mutation: drop the plan_slot_open
    call and this fails.
    """
    pid, tg = approved_week
    hol._reopen(pid, tg, "Thanksgiving")

    rows = _rows(pid, tg)
    assert len(rows) == 1 and rows[0][0] == "open"
    assert _open_reason(pid, tg) == "Plans for Thanksgiving changed — what would you like for dinner?"


def test_the_groceries_are_still_reversed(approved_week):
    """
    GUARD — green either way. Pinned by mutation: drop the clear_plan_slot
    call and this fails.
    """
    pid, tg = approved_week
    hol._reopen(pid, tg, "Thanksgiving")
    assert "beans" not in _list()


def test_changing_a_holiday_answer_still_hands_the_night_back(approved_week):
    """GUARD on the whole reachable path, end to end, with nothing mutated."""
    pid, tg = approved_week
    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")
    tools.answer_holiday(tg, "unsure")

    rows = _rows(pid, tg)
    assert len(rows) == 1 and rows[0][0] == "open"


# ---------- the mechanics that make it hold ----------

def test_the_whole_settle_is_one_connection(approved_week, monkeypatch):
    """
    GUARD, pinned by mutation rather than by redness — the failure mode of a
    nested get_conn is an intermittent "database is locked", not a wrong
    answer, so it has to be counted rather than waited for.

    Give either writer its own connection back and this goes to 2.
    """
    pid, tg = approved_week
    seen = _count_connections(monkeypatch)
    hol._reopen(pid, tg, "Thanksgiving")
    assert seen["n"] == 1, f"_reopen opened {seen['n']} connections"


def test_a_transaction_is_already_open_at_the_first_read(approved_week):
    """
    GUARD, pinned by mutation: with no explicit BEGIN at all this fails,
    because sqlite3's default opens the transaction at the first WRITE, and
    by then the slot has already been read.

    It deliberately does NOT claim to pin the lock MODE. `in_transaction` is
    True for a DEFERRED transaction too, so this cannot tell IMMEDIATE from
    DEFERRED — the race test above is what does that, by refusing to
    tolerate a writer that dies.
    """
    pid, tg = approved_week
    real = wp.clear_plan_slot
    seen = {}

    def _watch(*a, **k):
        seen["in_transaction"] = k["conn"].in_transaction
        return real(*a, **k)

    wp.clear_plan_slot = _watch
    try:
        hol._reopen(pid, tg, "Thanksgiving")
    finally:
        wp.clear_plan_slot = real
    assert seen["in_transaction"] is True


def test_the_plan_is_resolved_before_the_transaction_opens():
    """
    GUARD on the rule rather than on a value: _reopen takes an already-resolved
    plan_id, so it never calls _plan_for (which opens a connection of its own)
    from inside its own write transaction.
    """
    code = _code_of(hol._reopen)
    assert "_plan_for" not in code, "the clock/plan lookup belongs above the transaction"
    assert "BEGIN IMMEDIATE" in code


RACE_TRIALS = 8


def test_two_adults_changing_the_same_answer_leave_one_row(approved_week):
    """
    GUARD, pinned by mutation, and it asserts TWO things because the two
    wrong transactions fail differently.

    Delete the transaction altogether and both writers can read the pre-tap
    slot, so two `open` rows land on one day — audit_plan_slots'
    `duplicated`, which this repo's own notes call "how a night nobody is
    home ends up with groceries bought for it". That one is a RACE, so one
    trial catches it about a third of the time; eight trials is what makes
    it reliable, and the first version of this test ran one and its entry
    duly called the result "deterministic", which it was not.

    Weaken BEGIN IMMEDIATE to a plain (DEFERRED) BEGIN and the row count
    stays right while the loser dies of `database is locked` — the exact
    intermittent failure _reopen's own docstring says this repo has earned
    twice. That is why a raised exception is a FAILURE here rather than a
    tolerated loss: measured, DEFERRED raises on 5 runs out of 5, so this is
    the assertion that makes the lock mode testable at all.
    """
    pid, tg = approved_week
    for trial in range(RACE_TRIALS):
        _reset_slot(pid, tg)
        ready = threading.Barrier(2)
        errors: list[Exception] = []

        def _go():
            ready.wait()
            try:
                hol._reopen(pid, tg, "Thanksgiving")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = _rows(pid, tg)
        assert not errors, f"trial {trial}: a writer died: {errors!r}"
        assert len(rows) == 1, f"trial {trial}: {len(rows)} rows on one slot"
        assert rows[0][0] == "open"


# ---------- what this does NOT close ----------

def test_the_commonest_holiday_answer_is_atomic_one_module_over(approved_week, monkeypatch):
    """
    CHARACTERISATION, and the most important test in this file: it says what
    this branch does not fix.

    `_reopen` is only reached for a holiday answer the module itself planned
    a dinner into. For the ORDINARY "we're out" answer on a household with
    members on record, `_undo_effects` calls
    `attendance.clear_slot_attendance` first, and that reaches
    `slot_needs._reopen_away_slot` — which is the identical clear-then-open
    pair, still TWO COMMITS, and still leaves the slot genuinely absent.

    Measured here, not reasoned: `_reopen` is called ZERO times, so this
    file's fix cannot be what protects the path. `overnight/away-night-atomic`
    made `_reopen_away_slot` one transaction, and this test was INVERTED
    when both landed together (2026-09-18): a failure in the second write now
    rolls the first back, and the night stays off rather than going absent.
    """
    pid, tg = approved_week
    tools.answer_holiday(tg, "out")
    assert _rows(pid, tg) == [("planned_empty", None)], "premise: the night is off"

    seen = {"n": 0}
    real = hol._reopen
    monkeypatch.setattr(hol, "_reopen", lambda *a, **k: (seen.__setitem__("n", seen["n"] + 1), real(*a, **k))[1])
    monkeypatch.setattr(wp, "plan_slot_open", _boom)

    with pytest.raises(RuntimeError):
        tools.answer_holiday(tg, "unsure")

    assert seen["n"] == 0, "this path does not reach _reopen, so this file's fix is not what protects it"
    assert _rows(pid, tg) == [("planned_empty", None)], "the night stays off — nothing lost, nothing absent"


# ---------- helpers ----------

def _reset_slot(plan_id: int, day: str) -> None:
    """Put one planned dinner back on the slot, for the next race trial."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan_id, day),
    )
    conn.execute(
        "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, freeform_meal, slot_state) "
        "VALUES (1, ?, ?, 'dinner', 'Chili', 'planned')",
        (plan_id, day),
    )
    conn.commit()
    conn.close()


def _code_of(fn) -> str:
    """
    A function's CODE with its docstring and comments taken off.

    Not fussiness: the first cut of the guard below searched the raw source
    and was satisfied by its own docstring, which names `_plan_for` while
    explaining why `_plan_for` must not be called there. An assertion prose
    can satisfy is not an assertion — this file's own sibling branches have
    had to unpick that twice.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def _boom(*a, **k):
    raise RuntimeError("simulated failure between the two writes")


def _thanksgiving() -> str:
    found = tools.rule_holidays(date.today().year + 1)
    return next(h["date"] for h in found if h["name"] == "Thanksgiving")


def _rows(plan_id: int, day: str, slot: str = "dinner") -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id",
        (plan_id, day, slot),
    ).fetchall()
    conn.close()
    return [(r["slot_state"], r["meal"]) for r in rows]


def _open_reason(plan_id: int, day: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT open_reason FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan_id, day),
    ).fetchone()
    conn.close()
    return row["open_reason"]


def _list() -> list[str]:
    return [i["item"] for i in tools.list_grocery_list()]


def _ledger_rows() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id, grocery_item_id, quantity FROM meal_plan_grocery_links ORDER BY id"
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def _count_connections(monkeypatch):
    """
    Count get_conn calls made inside _reopen.

    It patches three bindings, not every one: most modules do
    `from ..db import get_conn` at import, so `grocery`, `recipes`,
    `attendance` and the rest of the ingest tree are invisible here. The
    count is still 1 when every binding in `app.*` is patched (measured on
    review), and the hazard this is really about — a nested connection
    inside the open transaction — fails loudly on its own anyway: it waits
    out SQLite's busy timeout, so the file goes from under a second to
    nearly a minute and eleven of twelve tests go red.
    """
    import app.db as db
    seen = {"n": 0}
    real = db.get_conn

    def _counted(*a, **k):
        seen["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(db, "get_conn", _counted)
    monkeypatch.setattr(hol, "get_conn", _counted)
    monkeypatch.setattr(wp, "get_conn", _counted)
    return seen
