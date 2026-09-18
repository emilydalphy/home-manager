"""
Marking a night away is ONE transaction (Bug, Phase 0 — Beta-ready).

slot_needs.set_slot_need's away conversion did two things in sequence —
clear whatever the week had planned for that slot, reversing its grocery
contribution, then write the deliberate planned_empty row that stands in
its place — and committed after each. Forcing a RuntimeError between them
gave the one state schema.sql, audit_plan_slots and plan_slot_open's own
docstring all say cannot exist: the dinner gone, nothing in its place, the
shopping list already stripped of its ingredients, under a route answering
500 and a screen saying nothing had been saved. Which was false.

_reopen_away_slot — somebody comes back, so the night is handed back as an
open question — had the identical pair and the identical seam, so it is
converted in the same commit. A half-converted module is a new bug rather
than a smaller one.

These are drop-dish-atomic's tests one screen along, and they are its tests
on purpose: a forced failure at each seam against a whole-database snapshot,
a count of how many connections the conversion opens (a nested WRITING
get_conn inside an open write transaction sits out SQLite's busy timeout
and fails as an intermittent "database is locked" rather than as anything a
deterministic test would catch — a nested READ does not, and the one such
count here is a consistency guard rather than a deadlock one, which its own
docstring says), and a check that the lock is held from before the read
that decides which plan gets written to.

Every test says in its own docstring whether it is red against main's app/
(CATCH), green on both sides and pinned by a named mutation (GUARD), or a
CHARACTERISATION of something this ticket deliberately leaves — and the two
races say what their measured rate is, because it moves with the machine.
Measured here, 20 whole-file runs against main's app/: 13 red every time,
the two races red 18 and 19 times, and one CHARACTERISATION red every time
for a reason other than the one it is named after (it says so). So the
honest count is 13 deterministic catches plus two timing-dependent ones.
"""
from __future__ import annotations

import datetime
import threading

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import attendance, grocery, slot_needs, weekly_plan


# The HOUSEHOLD's today, for the reason test_drop_dish_atomic gives at
# length: a week anchored on this calendar week's Monday puts most of its
# offsets behind today on most weekdays, and behind the household's today
# under a straddling runner even on a Monday. Nothing here asserts a
# weekday — these tests are about a failure mid-write — so the days only
# have to be ones the app will still take.
START = household_today()


def _day(offset: int) -> str:
    return (START + datetime.timedelta(days=offset)).isoformat()


AWAY, OTHER = _day(0), _day(1)


# ---------------------------------------------------------------- helpers

def _snapshot() -> dict:
    """
    Everything the conversion can touch, read straight out of the database.

    Compared as a whole rather than field by field, for the reason the
    drop's own snapshot gives: the property under test is "nothing moved",
    and a test that lists the columns it checks can only catch the damage
    it thought of. slot_needs is in here because the need row and the
    conversion it causes are now one write, so a rollback has to take both.
    """
    conn = get_conn()

    def rows(sql):
        return [tuple(r) for r in conn.execute(sql).fetchall()]

    snap = {
        "entries": rows(
            "SELECT id, weekly_plan_id, date, slot, recipe_id, freeform_meal, slot_state, "
            "reasoning, open_reason, derived_from_json, cooked_status FROM meal_plan_entries ORDER BY id"
        ),
        "grocery": rows(
            "SELECT id, item, quantity, status, source_weekly_plan_id FROM grocery_items ORDER BY id"
        ),
        "ledger": rows(
            "SELECT id, meal_plan_entry_id, grocery_item_id, item, quantity "
            "FROM meal_plan_grocery_links ORDER BY id"
        ),
        "needs": rows(
            "SELECT id, date, slot, need, reason, away_stretch_id FROM slot_needs ORDER BY id"
        ),
    }
    conn.close()
    return snap


def _household() -> list[int]:
    return [tools.add_member(n)["member_id"] for n in ("Alex", "Sam", "Rae")]


def _chili():
    tools.add_recipe(
        "Bean Chili",
        ingredients=[
            {"item": "Black beans", "qty": "1 can"},
            {"item": "Onion", "qty": "1"},
        ],
        default_servings=3,
    )


def _approved_week() -> tuple[int, int]:
    """An approved week with one dinner on the day we go away. (plan, entry)."""
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    entry_id = tools.plan_meal(AWAY, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, entry_id


def _grocery_by_item() -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def _dinner_rows(day: str = AWAY) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, slot_state, recipe_id FROM meal_plan_entries "
        "WHERE household_id = ? AND date = ? AND slot = 'dinner' ORDER BY id",
        (tools.household_id(), day),
    )]
    conn.close()
    return rows


def _boom(*_args, **_kwargs):
    raise RuntimeError("database is locked")


# ------------------------------------------- a failure at each seam: away

def test_a_failure_writing_the_empty_row_leaves_the_week_exactly_as_it_was(monkeypatch):
    """
    CATCH — red on main. The ticket's own reproduction. clear_plan_slot had
    already committed by the time plan_slot_empty ran, so this left the day
    with no dinner row at all and its ingredients off the list.
    """
    _approved_week()
    before = _snapshot()
    assert before["grocery"], "the week has to be on the list for the reversal to be worth anything"

    monkeypatch.setattr(weekly_plan, "plan_slot_empty", _boom)
    with pytest.raises(RuntimeError):
        tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert _snapshot() == before
    # Said again in the terms the household would notice, because a dict
    # comparison that goes wrong is hard to read.
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned"]
    assert _grocery_by_item() == {"Black beans": "1 can", "Onion": "1"}


def test_a_failure_after_the_grocery_reversal_puts_the_list_back(monkeypatch):
    """
    CATCH — red on main. The seam one step earlier: the reversal really
    runs, and then the write behind it dies. Its own commit used to make
    that permanent — the dinner still on the plan, its ingredients off the
    list, and nothing anywhere to say so.
    """
    _approved_week()
    before = _snapshot()

    real = grocery._reverse_meal_grocery_contributions

    def flaky(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(grocery, "_reverse_meal_grocery_contributions", flaky)
    with pytest.raises(RuntimeError):
        tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert _snapshot() == before
    assert _grocery_by_item() == {"Black beans": "1 can", "Onion": "1"}


def test_the_need_itself_rolls_back_with_the_conversion(monkeypatch):
    """
    CATCH — red on main, where the slot_needs row was committed before the
    conversion was even attempted.

    The need and the conversion it causes are one write now, so the 500's
    "nothing was saved" is true of both. Recording the away and leaving the
    dinner standing is a milder wrong than an absent slot, but it is still
    the two halves of the app disagreeing on screen — and it is the state
    apply_slot_needs_to_plan would then never correct, because that pass
    only runs when a week is generated.
    """
    _approved_week()
    monkeypatch.setattr(weekly_plan, "plan_slot_empty", _boom)
    with pytest.raises(RuntimeError):
        tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert tools.get_slot_need(AWAY, "dinner")["need"] == "normal"


# ----------------------------------------- a failure at each seam: coming back

def _away_night() -> int:
    """An approved week with the dinner already converted to planned_empty."""
    plan_id, _entry = _approved_week()
    tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]
    return plan_id


def test_a_failure_writing_the_open_row_leaves_the_away_night_as_it_was(monkeypatch):
    """
    CATCH — red on main. _reopen_away_slot's own seam, and the sibling this
    ticket insists on fixing in the same commit: the planned_empty row was
    deleted and committed, then plan_slot_open died, and the slot was
    absent — from a write nobody asked for directly, since this runs off
    an ordinary attendance change.
    """
    _away_night()
    before = _snapshot()

    monkeypatch.setattr(weekly_plan, "plan_slot_open", _boom)
    with pytest.raises(RuntimeError):
        slot_needs._reopen_away_slot(AWAY, "dinner", {"present_names": ["Alex"], "guest_count": 0})

    assert _snapshot() == before
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]


def test_a_failure_coming_back_through_attendance_leaves_it_as_it_was(monkeypatch):
    """
    CATCH — red on main. The same seam driven the way a household reaches
    it: nobody was home, then somebody is, so attendance undoes the away.
    Nothing here calls _reopen_away_slot by name.

    It compares the ENTRIES and not the whole snapshot, deliberately, and
    that narrowing is exactly the difference between what this ticket fixed
    and what it did not. _sync_away_need clears the away need on its OWN
    commit and only then calls _reopen_away_slot, so by the time the seam
    below is reached the slot_needs row has genuinely moved and is not
    coming back. Comparing the whole snapshot would fail here for a reason
    this ticket is not about — and the state it leaves behind has a
    characterisation test of its own,
    test_coming_back_is_still_two_transactions_at_the_attendance_level.
    """
    ids = None
    plan_id, _entry = _approved_week()
    conn = get_conn()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM members WHERE household_id = ? ORDER BY id", (tools.household_id(),))]
    conn.close()
    tools.set_slot_attendance(AWAY, "dinner", present_member_ids=[], guest_count=0)
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]
    before = _snapshot()

    monkeypatch.setattr(weekly_plan, "plan_slot_open", _boom)
    with pytest.raises(RuntimeError):
        tools.set_slot_attendance(AWAY, "dinner", present_member_ids=ids)

    assert _snapshot()["entries"] == before["entries"]
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]


# ------------------------------------- the same seam, NOT fixed here

def test_coming_back_is_still_two_transactions_at_the_attendance_level(monkeypatch):
    """
    CHARACTERISATION — what this ticket does NOT fix, written down so it is
    not reported as new. NOT a regression: main is worse in the same case.

    It is red against main, and NOT as a catch: there the slot comes out
    genuinely absent, so it dies on `[] == ['planned_empty']` rather than
    on anything it is named after. Read the file's catch count as 13
    deterministic plus the two races, and this one as neither.

    _reopen_away_slot is one transaction now. Coming back, as a household
    reaches it, is not: attendance._sync_away_need clears the away need on
    its own commit and THEN calls the reopen, so a failure in the reopen
    leaves the slot planned_empty with the need already back to 'normal' —
    the dinner blanked with everyone home, which is word for word the state
    _reopen_away_slot's own docstring says it exists to prevent.

    And it never heals, which is what makes it worth a test rather than a
    note: the only retry the app offers is the same tap, and _sync_away_need
    reads current["need"] != "away" and falls straight to "unchanged".

    Measured on main in the same shape: rows [] (genuinely absent, so
    audit_plan_slots reports the slot MISSING) and need 'normal', also never
    healing. So this is strictly the milder of the two. Invert this test
    when _sync_away_need's need write and the reopen become one
    transaction.
    """
    _plan_id, _entry = _approved_week()
    conn = get_conn()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM members WHERE household_id = ? ORDER BY id", (tools.household_id(),))]
    conn.close()
    tools.set_slot_attendance(AWAY, "dinner", present_member_ids=[], guest_count=0)
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]

    monkeypatch.setattr(weekly_plan, "plan_slot_open", _boom)
    with pytest.raises(RuntimeError):
        tools.set_slot_attendance(AWAY, "dinner", present_member_ids=ids)
    monkeypatch.undo()

    # Blanked, with everyone home and no away on record to explain it.
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]
    assert tools.get_slot_need(AWAY, "dinner")["need"] == "normal"

    # And the same tap again does nothing at all.
    att = tools.get_slot_attendance(AWAY, "dinner")
    assert attendance._sync_away_need(AWAY, "dinner", att) == "unchanged"
    tools.set_slot_attendance(AWAY, "dinner", present_member_ids=ids)
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]
    assert tools.get_slot_need(AWAY, "dinner")["need"] == "normal"


def test_the_holiday_reopen_has_the_identical_seam_and_is_NOT_fixed_here():
    """
    CHARACTERISATION — the same two commits, live, one module over, and
    reached by an ordinary household tap rather than by a forced failure in
    a private helper.

    holidays._reopen is clear_plan_slot then plan_slot_open with a commit
    between them, and answering a holiday a second time runs it: "we're
    going out and we're bringing the crumble", then "actually it's just
    us". Forced here, that leaves the dinner GONE, its ingredients already
    off the shopping list and audit_plan_slots reporting the slot MISSING —
    the ticket's own reproduction, in a module the ticket does not name.

    Left because widening this ticket into holidays.py means threading a
    connection through _undo_effects and its four branches, which is a
    bigger claim than "the away conversion is atomic". Its own card, and
    big_meal.py has two more of the same shape (the no-main and the
    clashing-main arms of the hosting menu) which are NOT driven here and
    are named on that card rather than measured.

    agent.py's pair in _finish_week_slots is the same shape and is safe in
    practice for a reason worth knowing: it only runs inside
    generate_weekly_plan, whose finally block calls discard_failed_plan, so
    a failure there deletes the whole half-built plan and nothing survives
    to be inconsistent.

    Invert this test when holidays._reopen becomes one transaction.
    """
    from app.tools import holidays

    hol = "2026-10-12"  # Canadian Thanksgiving: second Monday of October, by rule
    assert holidays.holiday_on(hol)["name"] == "Thanksgiving"

    _household()
    _chili()
    tools.add_recipe("Apple Crumble", ingredients=[{"item": "Apples", "qty": "6"}],
                     default_servings=6)
    plan_id = tools.create_weekly_plan(hol)["weekly_plan_id"]
    tools.plan_meal(hol, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    holidays.answer_holiday(hol, "out", bring_dish="Apple Crumble", answered_by="Emily")
    assert [r["slot_state"] for r in _dinner_rows(hol)] == ["planned"]
    assert _grocery_by_item() == {"Apples": "3"}

    real = weekly_plan.plan_slot_open
    weekly_plan.plan_slot_open = _boom
    try:
        with pytest.raises(RuntimeError):
            holidays.answer_holiday(hol, "just_us", answered_by="Emily")
    finally:
        weekly_plan.plan_slot_open = real

    # The state this ticket's own fix exists to make impossible, still here.
    assert _dinner_rows(hol) == []
    assert _grocery_by_item() == {}
    assert {"date": hol, "slot": "dinner"} in tools.audit_plan_slots(plan_id)["missing"]


# ------------------------------------- a failure enforcing needs at generation

def test_a_failure_enforcing_one_away_slot_at_generation_leaves_that_slot_alone(monkeypatch):
    """
    CATCH — red on main. The third copy of the same pair, in
    apply_slot_needs_to_plan — the belt-and-braces pass that runs over a
    just-generated week. The ticket names the other two; this one is the
    same two commits in the same module, so leaving it would have been the
    half-converted module this repo's own rule warns about.

    One transaction per slot rather than per week, deliberately: these
    slots are independent, and what must never happen is that one of them
    is left absent.
    """
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    tools.plan_meal(AWAY, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    # The need on its own, with no plan for it to convert — set directly so
    # set_slot_need's own conversion is not what does the work here.
    conn = get_conn()
    conn.execute(
        "INSERT INTO slot_needs (household_id, date, slot, need, reason, for_member_ids_json, updated_at) "
        "VALUES (?, ?, 'dinner', 'away', 'we are out', '[]', datetime('now'))",
        (tools.household_id(), AWAY),
    )
    conn.commit()
    conn.close()
    before = _snapshot()

    monkeypatch.setattr(weekly_plan, "plan_slot_empty", _boom)
    with pytest.raises(RuntimeError):
        tools.apply_slot_needs_to_plan(plan_id, START.isoformat(), 7)

    assert _snapshot() == before
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned"]


# ------------------------------------------------------ the deadlock half

def _count_get_conn(monkeypatch) -> dict:
    """Count every connection opened, per module, for as long as the test runs."""
    opened = {"slot_needs": 0, "weekly_plan": 0, "grocery": 0, "attendance": 0}
    for name, module in (
        ("slot_needs", slot_needs), ("weekly_plan", weekly_plan),
        ("grocery", grocery), ("attendance", attendance),
    ):
        real = module.get_conn

        def counting(_name=name, _real=real):
            opened[_name] += 1
            return _real()

        monkeypatch.setattr(module, "get_conn", counting)
    return opened


def _window(monkeypatch, opened, *, first, last):
    """
    Mark the connection counts at the first and last thing inside the write
    transaction.

    Counted over a WINDOW rather than over the whole call, the way the
    drop's own guard does it: the reads before and after the transaction
    are ordinary connections outside it, and a total would fail for a
    reason this is not about.
    """
    marks = {}
    first_name, last_name = first, last
    real_first = getattr(weekly_plan, first_name)
    real_last = getattr(weekly_plan, last_name)

    def marking_first(*args, **kwargs):
        marks.setdefault("start", dict(opened))
        return real_first(*args, **kwargs)

    def marking_last(*args, **kwargs):
        out = real_last(*args, **kwargs)
        marks["end"] = dict(opened)
        return out

    monkeypatch.setattr(weekly_plan, first_name, marking_first)
    monkeypatch.setattr(weekly_plan, last_name, marking_last)
    return marks


def test_the_away_conversion_opens_no_second_connection_inside_its_transaction(monkeypatch):
    """
    CATCH — red on main, where clear_plan_slot and plan_slot_empty each
    open one of their own.

    SQLite gives one writer at a time, so a helper that opened its own
    connection inside this write transaction would sit behind that
    transaction's lock and fail with "database is locked" — atomicity and
    deadlock-avoidance are one requirement. The window runs from
    clear_plan_slot, the first thing inside the transaction, to the return
    of plan_slot_empty, the last.
    """
    _approved_week()
    opened = _count_get_conn(monkeypatch)
    marks = _window(monkeypatch, opened, first="clear_plan_slot", last="plan_slot_empty")

    tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert set(marks) == {"start", "end"}, "the transaction body never ran"
    assert marks["end"] == marks["start"], (
        "something inside the away conversion's write transaction opened its own "
        f"connection: {marks['start']} -> {marks['end']}"
    )


def test_the_plan_lookup_opens_no_second_connection_either(monkeypatch):
    """
    CATCH — red on main for a different reason: there the lookup runs
    outside any transaction at all, so the connection it opens is harmless
    and the count still moves.

    AND IT IS A CONSISTENCY GUARD, NOT A DEADLOCK ONE — unlike its two
    neighbours, which count the connections around a WRITE. This lookup is
    a SELECT, and SQLite lets a reader in alongside a writer holding
    RESERVED, so measured, letting it open its own connection from inside
    the transaction costs 0.83s and this one test, with no hang anywhere.
    What it buys is that "one connection inside the transaction" is true of
    this function rather than nearly true, and nothing else would notice
    losing it.

    _plan_id_for_date falls through to weekly_plan.get_plan_id_for_date
    when no entry exists for the slot yet — a day in a generated week that
    the plan left blank. That read is now inside the lock, so it had to
    join the connection rather than open one of its own. Measured from the
    BEGIN to the end of the lookup.
    """
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    tools.plan_meal(OTHER, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert _dinner_rows(AWAY) == [], "the lookup has to take its fallback branch"

    opened = _count_get_conn(monkeypatch)
    marks = {}
    real = slot_needs._plan_id_for_date

    def watching(*args, **kwargs):
        marks["start"] = dict(opened)
        out = real(*args, **kwargs)
        marks["end"] = dict(opened)
        return out

    monkeypatch.setattr(slot_needs, "_plan_id_for_date", watching)
    tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert set(marks) == {"start", "end"}
    assert marks["end"] == marks["start"], (
        f"the plan lookup opened its own connection: {marks['start']} -> {marks['end']}"
    )


def test_the_reopen_opens_no_second_connection_inside_its_transaction(monkeypatch):
    """CATCH — red on main, for the same reason as its away twin."""
    _away_night()
    opened = _count_get_conn(monkeypatch)
    marks = _window(monkeypatch, opened, first="clear_plan_slot", last="plan_slot_open")

    slot_needs._reopen_away_slot(AWAY, "dinner", {"present_names": ["Alex"], "guest_count": 0})

    assert set(marks) == {"start", "end"}, "the transaction body never ran"
    assert marks["end"] == marks["start"], (
        "something inside the reopen's write transaction opened its own connection: "
        f"{marks['start']} -> {marks['end']}"
    )


def test_the_transaction_is_open_while_the_empty_row_is_written(monkeypatch):
    """
    CATCH — red on main. "No second connection" is only half the property:
    the empty row has to be written on the SAME connection the delete was,
    AND that connection has to still be in the transaction the delete was
    made in. One connection used twice is two transactions.

    Which assertion pins which half is worth being exact about, because an
    earlier version of this test named a property it could not see. Reading
    the deleted row back from inside plan_slot_empty proves only that the
    delete already happened — a COMMITTED delete is gone as seen from any
    connection, so that read is satisfied by a split transaction too.
    `conn.in_transaction` is the one that can tell them apart. Measured:
    put a conn.commit() between the clear and the empty row in
    _settle_slot_empty — genuinely two transactions on one connection —
    and the row-read assertion stays green while this one goes red.
    """
    _plan_id, entry_id = _approved_week()
    seen = {}
    real = weekly_plan.plan_slot_empty

    def watching(*args, **kwargs):
        conn = kwargs.get("conn")
        seen["got_conn"] = conn is not None
        if conn is not None:
            seen["still_in_transaction"] = conn.in_transaction
            seen["sees_the_delete"] = conn.execute(
                "SELECT COUNT(*) c FROM meal_plan_entries WHERE id = ?", (entry_id,)
            ).fetchone()["c"] == 0
        return real(*args, **kwargs)

    monkeypatch.setattr(weekly_plan, "plan_slot_empty", watching)
    tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert seen == {
        "got_conn": True, "still_in_transaction": True, "sees_the_delete": True,
    }


def test_the_lock_is_taken_before_the_plan_is_looked_up(monkeypatch):
    """
    CATCH — red on main, where the lookup runs after a commit and is under
    no lock at all, so a second writer could move this slot onto another
    plan between the lookup that chose plan A and the clear that empties
    it — and the clear would then empty a slot on a plan that no longer
    owns the day.

    What it pins is that the lookup happens INSIDE the transaction, not
    that BEGIN IMMEDIATE is what put it there: measured, taking that BEGIN
    out of set_slot_need leaves this green, because the slot_needs INSERT
    two lines above it opens a transaction implicitly and takes the write
    lock anyway. The explicit BEGIN is what keeps this true if those
    statements are ever reordered, and it is pinned on its own by
    test_the_away_conversion_opens_its_transaction_immediately below. Its
    sibling in _reopen_away_slot is not belt-and-braces at all — there the
    first statement is a SELECT, and dropping the BEGIN there reddens
    test_the_lock_is_taken_before_the_reopen_reads_its_row.
    """
    _approved_week()
    seen = {}
    real = slot_needs._plan_id_for_date

    def watching(conn, *args, **kwargs):
        seen["in_transaction"] = conn.in_transaction
        return real(conn, *args, **kwargs)

    monkeypatch.setattr(slot_needs, "_plan_id_for_date", watching)
    tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert seen == {"in_transaction": True}


class _LoggingConn:
    """A connection that remembers the statements run on it, and is otherwise itself."""

    def __init__(self, real):
        self._real = real
        self.statements = []

    def execute(self, sql, *args, **kwargs):
        self.statements.append(" ".join(sql.split())[:40])
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_the_away_conversion_opens_its_transaction_immediately(monkeypatch):
    """
    CATCH — red on main, which opens no transaction of its own at all.

    The half its neighbour above cannot see. sqlite3's legacy
    isolation_level="" opens a DEFERRED transaction at the first write, so
    a function whose first statement happens to be a write is covered by
    accident; one whose first statement is a read is not. Naming the BEGIN
    is what makes the guarantee a property of the function rather than of
    the order its statements happen to be in today.
    """
    _approved_week()
    real_get_conn = slot_needs.get_conn
    opened = []

    def logging_get_conn():
        conn = _LoggingConn(real_get_conn())
        opened.append(conn)
        return conn

    monkeypatch.setattr(slot_needs, "get_conn", logging_get_conn)
    tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert opened, "set_slot_need opened no connection of its own"
    assert opened[0].statements[0] == "BEGIN IMMEDIATE"


def test_the_lock_is_taken_before_the_reopen_reads_its_row(monkeypatch):
    """
    CATCH — red on main, where the row is read on a connection that is then
    closed before either write. The reopen's own version of the rule above:
    that read decides which plan gets the open row.
    """
    _away_night()
    seen = {}
    real = weekly_plan.clear_plan_slot

    def watching(*args, **kwargs):
        conn = kwargs.get("conn")
        seen["got_conn"] = conn is not None
        # The row was read on this same connection, under the same lock —
        # which is only observable as "the transaction was already open".
        seen["in_transaction"] = conn is not None and conn.in_transaction
        return real(*args, **kwargs)

    monkeypatch.setattr(weekly_plan, "clear_plan_slot", watching)
    slot_needs._reopen_away_slot(AWAY, "dinner", {"present_names": ["Alex"], "guest_count": 0})

    assert seen == {"got_conn": True, "in_transaction": True}


# ------------------------------------------------ two writers at the same time

def test_two_aways_at_once_leave_exactly_one_empty_row():
    """
    A TIMING-DEPENDENT CATCH — real evidence, and weaker than the
    forced-failure tests above, which are deterministic. Read the file's
    count as 13 solid catches plus this one and its sibling below,
    test_going_away_and_coming_back_at_once_leave_exactly_one_row.

    THE RATE MOVES WITH THE MACHINE, so take that sentence and not a
    number. An earlier version of this docstring gave two, "red on main 3
    whole-file runs out of 3" and "red only 2 runs in 10 when run alone",
    and both were wrong — the second in this test's own favour, which is
    the more dangerous direction, since it made the test look weaker than
    it is and so made the file's count look more honest than it was.
    Measured here: against main's app/ it is red in 38 of 40 runs alone and
    in 42 of 46 whole-file runs, and the 4 whole-file runs where it is
    green are the only reason that file's failure count is ever 13 rather
    than 14. An independent reviewer on another machine measured 12 of 20
    alone and 11 of 16 whole-file. Green on this branch 20 runs out of 20
    on its own.

    What it is about: on main both callers can clear before either writes,
    leaving TWO planned_empty rows on one slot — audit_plan_slots'
    `duplicated`, which CLAUDE.md calls "how a night nobody is home ends up
    with groceries bought for it". Here the lock is held from the first
    read, so the loser sees the world the winner left.
    """
    plan_id, _entry = _approved_week()
    results = {}
    barrier = threading.Barrier(2)

    def go(tag):
        barrier.wait()
        try:
            results[tag] = tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")
        except Exception as exc:  # pragma: no cover - a crash is a failure below
            results[tag] = {"raised": repr(exc)}

    threads = [threading.Thread(target=go, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all("raised" not in r for r in results.values()), results
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]
    assert tools.audit_plan_slots(plan_id)["duplicated"] == []
    assert {"date": AWAY, "slot": "dinner"} not in tools.audit_plan_slots(plan_id)["missing"]


def test_going_away_and_coming_back_at_once_leave_exactly_one_row():
    """
    A TIMING-DEPENDENT CATCH, and the other race — the one the first cut of
    this file left uncovered while claiming the class was closed.

    Its sibling above races two AWAYS. This races an away against a COMING
    BACK, which is the shape a household actually produces: one phone
    extends the trip over a night that is already away (set_away_stretch
    loops set_slot_need over every night in the range, already-away ones
    included) while the other taps somebody's avatar back on. On main
    neither write holds a lock across its own read, so both can clear
    before either writes and the slot ends up with TWO rows — one
    planned_empty and one open, in whichever order won — which is
    audit_plan_slots' `duplicated`, what CLAUDE.md calls "how a night
    nobody is home ends up with groceries bought for it".

    Measured against main's app/: 39 of 40 runs left two rows. On this
    branch: 40 of 40 left exactly one, and which one it is depends on who
    won, which is the correct answer to a genuine race.
    """
    plan_id, _entry = _approved_week()
    tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]

    errs = []
    barrier = threading.Barrier(2)

    def going_away():
        barrier.wait()
        try:
            tools.set_slot_need(AWAY, "dinner", "away", reason="still out")
        except Exception as exc:  # pragma: no cover - a crash is a failure below
            errs.append(("away", repr(exc)))

    def coming_back():
        barrier.wait()
        try:
            slot_needs._reopen_away_slot(
                AWAY, "dinner", {"present_names": ["Alex"], "guest_count": 0}
            )
        except Exception as exc:  # pragma: no cover
            errs.append(("back", repr(exc)))

    threads = [threading.Thread(target=going_away), threading.Thread(target=coming_back)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errs == [], errs
    states = [r["slot_state"] for r in _dinner_rows()]
    assert len(states) == 1, f"the slot ended up with {states}"
    # Either winner is correct; what must never happen is both, or neither.
    assert states[0] in ("planned_empty", "open"), states
    assert tools.audit_plan_slots(plan_id)["duplicated"] == []
    assert {"date": AWAY, "slot": "dinner"} not in tools.audit_plan_slots(plan_id)["missing"]


# ------------------------------------------------------------ happy paths

def test_marking_a_night_away_still_empties_the_slot_and_the_list():
    """
    GUARD — green on both sides by design. One transaction has to produce
    the same end state the two-commit version produced. Pinned by mutation:
    drop the plan_slot_empty call out of _settle_slot_empty and this fails
    on the slot_state assertion.
    """
    _approved_week()

    out = tools.set_slot_need(AWAY, "dinner", "away", reason="we're out")

    assert out["converted_existing_plan_slot"] is True
    assert [r["slot_state"] for r in _dinner_rows()] == ["planned_empty"]
    assert tools.get_slot_need(AWAY, "dinner")["need"] == "away"
    assert _grocery_by_item() == {}


def test_a_need_with_no_plan_behind_it_is_still_just_recorded():
    """
    GUARD — green on both sides. The ordinary case for a need declared
    before the week is generated: nothing to convert, so nothing is, and
    the need stands on its own. Pinned by mutation: make _plan_id_for_date
    answer with any plan id and this fails, because converted flips.
    """
    _household()
    _chili()

    out = tools.set_slot_need(_day(40), "dinner", "away", reason="we're away")

    assert out["converted_existing_plan_slot"] is False
    assert tools.get_slot_need(_day(40), "dinner")["need"] == "away"


def test_coming_back_still_hands_the_night_back_as_an_open_question():
    """
    GUARD — green on both sides. Pinned by mutation: drop the
    plan_slot_open call out of _reopen_away_slot and this fails, because
    the slot comes back absent rather than open.
    """
    _away_night()

    assert slot_needs._reopen_away_slot(
        AWAY, "dinner", {"present_names": ["Alex", "Sam"], "guest_count": 0}
    ) is True

    rows = _dinner_rows()
    assert [r["slot_state"] for r in rows] == ["open"]
    conn = get_conn()
    reason = conn.execute(
        "SELECT open_reason FROM meal_plan_entries WHERE id = ?", (rows[0]["id"],)
    ).fetchone()["open_reason"]
    conn.close()
    assert "Alex and Sam will be here after all" in reason


def test_there_is_nothing_to_reopen_when_the_slot_was_never_emptied():
    """
    GUARD — green on both sides. The early return still returns False and
    still writes nothing, now that it has a transaction to roll back out
    of. Pinned by mutation: replace that conn.rollback() with a commit and
    this still passes — but replace the early return itself with a fall
    through and it fails, because a planned dinner would be cleared.
    """
    _approved_week()
    before = _snapshot()

    assert slot_needs._reopen_away_slot(
        AWAY, "dinner", {"present_names": ["Alex"], "guest_count": 0}
    ) is False
    assert _snapshot() == before


def test_generation_still_enforces_every_away_slot():
    """
    GUARD — green on both sides. apply_slot_needs_to_plan's pass over a
    just-generated week, now one transaction per slot. Pinned by mutation:
    drop the clear_plan_slot call out of _settle_slot_empty and this fails
    with two rows on the slot.
    """
    _household()
    _chili()
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    tools.plan_meal(AWAY, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(OTHER, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    for day in (AWAY, OTHER):
        conn.execute(
            "INSERT INTO slot_needs (household_id, date, slot, need, reason, for_member_ids_json, updated_at) "
            "VALUES (?, ?, 'dinner', 'away', 'we are out', '[]', datetime('now'))",
            (tools.household_id(), day),
        )
    conn.commit()
    conn.close()

    out = tools.apply_slot_needs_to_plan(plan_id, START.isoformat(), 7)

    assert sorted(a["date"] for a in out["away_enforced"]) == sorted([AWAY, OTHER])
    for day in (AWAY, OTHER):
        assert [r["slot_state"] for r in _dinner_rows(day)] == ["planned_empty"]
    assert tools.audit_plan_slots(plan_id)["duplicated"] == []
