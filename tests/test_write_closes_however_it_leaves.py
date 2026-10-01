"""
db.write() closes its connection however the block leaves — and the
property that makes this class expensive: a raise inside a write window
still leaves error_events able to record the failure.

WHY THAT IS THE TEST AND NOT "no connection is leaked". A leaked
connection on its own is an invisible, survivable cost. What is expensive
is the SECOND failure it causes: the connection is leaked HOLDING SQLite's
write lock, so tools.record_error — which has to write — cannot, and the
one failure the morning report most needs to see is the one it cannot see.
Measured 2026-09-26 on a real uvicorn, before
recipes._maybe_auto_attribute_solo_night grew its try/finally (CLAUDE.md,
the recipe-rating entry): the household's very next write waited out
sqlite3's full 5-second busy timeout and then 500'd, and error_events held
nothing at all for the crash that caused it. Nothing in this repo pinned
that sentence until this file.

HOW THE LEAK IS REPRODUCED, and it matters that it is reproduced rather
than described. CPython's refcounting closes a connection the moment the
last reference goes, so a function that raises mid-write leaks nothing in
a test that lets the exception fall on the floor — the frame dies with it.
What keeps it alive in production is the TRACEBACK: FastAPI holds it for
the response, the traceback holds the frame, the frame holds the local
`conn`. So these tests hold the traceback too (`sys.exc_info()[2]`), which
is the honest reproduction, and gc.collect() is called to show it does not
help. Each one drops the traceback in a `finally` — conftest wipes tables
between tests through get_conn(), which needs the write lock, so a leak
left standing here would fail the NEXT test instead of this one.

RED AGAINST MAIN IS 9 OF 11 AND IS WORTH ALMOST NOTHING FOR THIS FILE, so
it is decomposed rather than quoted. Measured by reverting app/ and
re-running: 9 red, and every one of the 9 dies on `AttributeError: module
'app.db' has no attribute 'write'` — the only kind of red a test of a new
symbol can have. NOT ONE of them reaches the assertion it is named after,
including the two labelled CATCH below, which are catches by MUTATION and
NAMEs against main. The 2 green on main are
test_a_raise_inside_an_unprotected_write_window_silences_error_events
(which asserts the BUG, so it is green on both trees and should be) and
test_get_conn_still_hands_back_a_plain_sqlite3_connection (a guard on what
this branch deliberately did NOT change).

THE MUTATIONS ARE THE EVIDENCE, measured per test rather than as one
figure, because the honest picture is a conjunction:
  * db.write() reverted to the unsafe shape (no rollback, NO close in a
    finally): 3 red — both property tests and the closed-on-the-way-out
    shape test. Run ONE TEST AT A TIME: pytest retains a failed test's
    traceback, which retains the generator frame, which retains the leaked
    connection — so under this mutation the first failure hangs every
    later test in the session on conftest's own table wipe. That is the
    production mechanism reproduced inside the suite, and it is the
    clearest demonstration on this branch that the class is real.
  * the close moved OUT of the finally, rollback kept: 1 red.
  * the close deleted entirely, rollback kept: 2 red.
  * the rollback deleted, close kept: 0 red — see
    test_the_explicit_rollback_is_defence_in_depth_and_is_not_pinned,
    which says what that does and does not mean.
  * the commit on a clean exit deleted: 1 red.
  * BEGIN IMMEDIATE added to the opener: 1 red.
  * get_conn() made to return a thin forwarding proxy: 1 red (the guard
    written for it). Made to return contextlib.closing(conn) instead: 11
    ERRORS at setup, because conftest's own init_db() goes through
    get_conn — which is the 1367-call-sites argument, not a clean catch.

WHY IT IS FAST. The locked-out case costs about half a second, and that
number is production's rather than a shortcut: record_error sets `PRAGMA
busy_timeout = 500` itself, deliberately, so a 500 never parks a
threadpool worker for five seconds. The one test that measures an ordinary
app write (which does get the 5s default) lowers the timeout on its own
probe connection and says so, so nothing here waits out five seconds.
"""
import gc
import sqlite3
import sys

import pytest

from app import db
from app.tools import usage
from app.tools._shared import household_id


def _rows():
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT kind, where_ FROM error_events WHERE household_id = ?", (household_id(),)
        ).fetchall()
    finally:
        conn.close()


def _leak_the_unsafe_way():
    """
    The shape this card is about, written out: open, write, raise before
    the close. 171 writing functions in app/ are this shape today; this is
    a copy rather than a call into one of them, because what is being
    pinned is the SHAPE, and a copy cannot stop reproducing it because
    somebody fixed a particular function.
    """
    conn = db.get_conn()
    conn.execute("UPDATE households SET goals = 'mid-write' WHERE id = ?", (household_id(),))
    raise RuntimeError("something raised mid-write")
    conn.close()  # noqa: F841  — unreachable, which is the whole bug


def _leak_the_blessed_way():
    """The same body, same raise, under db.write()."""
    with db.write() as conn:
        conn.execute("UPDATE households SET goals = 'mid-write' WHERE id = ?", (household_id(),))
        raise RuntimeError("something raised mid-write")


# --------------------------------------------------------------------------
# THE PROPERTY. One pair, said both ways.
# --------------------------------------------------------------------------

def test_a_raise_inside_an_unprotected_write_window_silences_error_events(client):
    """
    CATCH — red on main for the reason it is named after, and it is the
    measurement this card is sized by rather than a restatement of the
    fix. The unsafe shape is reproduced, the traceback is held the way a
    request holds it, and record_error then writes NOTHING: it is denied
    the write lock and swallows the failure, exactly as its own docstring
    says it must.

    This test asserts the BUG, so it stays green once db.write() exists —
    its sibling below is what goes red if the helper stops closing. Kept
    because it is the only place the cost is written down as behaviour: a
    reader who deletes the sibling and keeps this one has a file that says
    the leak is fine.
    """
    held = None
    try:
        try:
            _leak_the_unsafe_way()
        except RuntimeError:
            held = sys.exc_info()[2]
        gc.collect()  # does not help: the reference is on the traceback

        usage.record_error("server", where="/api/recipe-feedback", detail="500")
        assert _rows() == [], (
            "error_events recorded something while the write lock was leaked — "
            "either record_error stopped needing the lock, or the leak is not "
            "being reproduced (check that the traceback is still held)."
        )
    finally:
        del held
        gc.collect()


def test_a_raise_inside_a_db_write_block_still_lets_error_events_record(client):
    """
    CATCH BY MUTATION — the acceptance criterion, and the one test that
    goes red if db.write() ever stops closing on the way out. Same body,
    same raise, same held traceback; the connection is closed by the time
    the exception reaches the caller, so the lock is free and record_error
    writes its row.

    Against MAIN it is a NAME, not a catch: db.write does not exist there,
    so it dies on an AttributeError without reaching this assertion. What
    shows it bites is the first mutation in the module docstring —
    db.write() reverted to the unsafe shape — which reddens it on its own
    assertion with error_events empty.
    """
    held = None
    try:
        with pytest.raises(RuntimeError):
            try:
                _leak_the_blessed_way()
            except RuntimeError:
                held = sys.exc_info()[2]
                raise
        gc.collect()

        usage.record_error("server", where="/api/recipe-feedback", detail="500")
        rows = _rows()
        assert [(r["kind"], r["where_"]) for r in rows] == [
            ("server", "/api/recipe-feedback")
        ], f"error_events should hold the one failure; holds {[dict(r) for r in rows]}"
    finally:
        del held
        gc.collect()


def test_the_next_ordinary_write_is_not_locked_out_either(client):
    """
    CATCH BY MUTATION, a NAME against main for the same reason as its
    sibling above. The other half of the measured fallout: record_error is
    the expensive casualty, and the household's own next write is the
    visible one (measured 5.53s then a 500, 2026-09-26).

    The probe connection sets busy_timeout = 300 rather than taking
    sqlite3's 5-second default, so this test costs a third of a second
    instead of five. What is being asserted is whether the lock is HELD,
    and the timeout only decides how long we are willing to wait to find
    that out.
    """
    held = None
    try:
        try:
            _leak_the_unsafe_way()
        except RuntimeError:
            held = sys.exc_info()[2]
        probe = db.get_conn()
        probe.execute("PRAGMA busy_timeout = 300")
        with pytest.raises(sqlite3.OperationalError):
            probe.execute("BEGIN IMMEDIATE")
        probe.close()
    finally:
        del held
        gc.collect()

    held = None
    try:
        with pytest.raises(RuntimeError):
            try:
                _leak_the_blessed_way()
            except RuntimeError:
                held = sys.exc_info()[2]
                raise
        probe = db.get_conn()
        probe.execute("PRAGMA busy_timeout = 300")
        probe.execute("BEGIN IMMEDIATE")  # free: no raise
        probe.execute("ROLLBACK")
        probe.close()
    finally:
        del held
        gc.collect()


# --------------------------------------------------------------------------
# The helper's own contract.
# --------------------------------------------------------------------------

def test_a_clean_block_commits(client):
    """NAME — db.write does not exist on main, so this cannot run there."""
    with db.write() as conn:
        conn.execute("UPDATE households SET goals = 'kept' WHERE id = ?", (household_id(),))
    after = db.get_conn()
    try:
        assert after.execute(
            "SELECT goals FROM households WHERE id = ?", (household_id(),)
        ).fetchone()["goals"] == "kept"
    finally:
        after.close()


def test_a_block_that_raises_commits_nothing(client):
    """
    NAME. The rollback half. Note what this really pins: that the write
    does not land. It does NOT pin the explicit conn.rollback() in the
    helper — closing a connection with an open transaction discards it
    anyway, so deleting that line leaves this green. See the next test.
    """
    before = db.get_conn()
    try:
        goals = before.execute(
            "SELECT goals FROM households WHERE id = ?", (household_id(),)
        ).fetchone()["goals"]
    finally:
        before.close()

    with pytest.raises(RuntimeError):
        with db.write() as conn:
            conn.execute("UPDATE households SET goals = 'lost' WHERE id = ?", (household_id(),))
            raise RuntimeError("no")

    after = db.get_conn()
    try:
        assert after.execute(
            "SELECT goals FROM households WHERE id = ?", (household_id(),)
        ).fetchone()["goals"] == goals
    finally:
        after.close()


def test_the_explicit_rollback_is_defence_in_depth_and_is_not_pinned(client):
    """
    GUARD, and it is a statement about coverage rather than about
    behaviour — written down because this repo has twice had to unpick a
    claim that something was covered when it was not.

    MEASURED, and the first version of this docstring got it backwards,
    which is why the measurement is written out. Delete the
    `conn.rollback()` from db.write and every test in this file stays
    green, because `finally: conn.close()` discards the open transaction
    on its own (CLAUDE.md's away-night-atomic entry records measuring
    exactly that). So the rollback is unpinned — BUT ONLY WHILE THE CLOSE
    IS THERE, and that qualification is not a quibble. The mutations say
    so: close moved out of the finally, or deleted outright, with the
    rollback kept, reddens ONE or TWO tests and NEITHER property test,
    because the rollback releases the write lock on its own even though
    the connection is leaked. It takes removing BOTH — db.write() reverted
    to the shape this card is about — to redden the two property tests.

    So the two mechanisms are belt and braces for EACH OTHER rather than
    one being decoration, and the property tests pin the conjunction. The
    one test that pins the close on its own is
    test_the_connection_is_closed_on_the_way_out_either_way.

    What this test can honestly assert is the one thing that is checkable
    about the rollback itself: that it cannot replace the caller's
    exception with one of its own, however broken the connection is by
    then.
    """
    class Broken(RuntimeError):
        pass

    with pytest.raises(Broken):
        with db.write() as conn:
            conn.close()  # the caller closed it; rollback() will raise
            raise Broken("the caller's failure is the news")


def test_the_block_may_commit_for_itself_and_the_helpers_commit_is_a_no_op(client):
    """
    GUARD — the measurement behind the helper's choice to commit, so a
    future reader does not have to re-derive it. A second commit() on a
    connection with no open transaction is a no-op and does not raise, so
    the 171 functions on the sweep's census can be converted WITHOUT
    having their own conn.commit() removed in the same edit.

    MUTATION that pins it: make db.write() raise instead of committing on
    a clean exit (or drop the commit entirely) — the first reddens this
    and test_a_clean_block_commits, the second reddens that one alone.
    """
    with db.write() as conn:
        conn.execute("UPDATE households SET goals = 'twice' WHERE id = ?", (household_id(),))
        conn.commit()
        assert conn.in_transaction is False
    after = db.get_conn()
    try:
        assert after.execute(
            "SELECT goals FROM households WHERE id = ?", (household_id(),)
        ).fetchone()["goals"] == "twice"
    finally:
        after.close()


@pytest.mark.parametrize("raises", [False, True])
def test_the_connection_is_closed_on_the_way_out_either_way(client, raises):
    """NAME — the shape, asserted directly rather than through the lock."""
    escaped = {}

    def run():
        with db.write() as conn:
            escaped["conn"] = conn
            if raises:
                raise RuntimeError("boom")

    if raises:
        with pytest.raises(RuntimeError):
            run()
    else:
        run()

    with pytest.raises(sqlite3.ProgrammingError):
        escaped["conn"].execute("SELECT 1")


def test_db_write_does_not_open_a_transaction_of_its_own(client):
    """
    GUARD. The helper is about closing, never about transaction
    boundaries: a block that needs the write lock from its first READ
    still has to say BEGIN IMMEDIATE itself. If write() took the lock for
    every caller it would change lock-acquisition timing at all 171
    conversion sites at once, and three separate CLAUDE.md entries
    (swap-atomic, atomic-period-takeover, away-night-atomic) are three
    reasons that call belongs to the write and not to the opener.

    MUTATION that pins it: add `conn.execute("BEGIN IMMEDIATE")` to
    db.write before the yield.
    """
    with db.write() as conn:
        assert conn.in_transaction is False, (
            "db.write() opened a transaction before the block ran — see the "
            "docstring; the caller decides whether to BEGIN IMMEDIATE."
        )
        other = db.get_conn()
        try:
            other.execute("PRAGMA busy_timeout = 300")
            other.execute("BEGIN IMMEDIATE")  # free, because write() took nothing
            other.execute("ROLLBACK")
        finally:
            other.close()


def test_get_conn_still_hands_back_a_plain_sqlite3_connection(client):
    """
    GUARD, and the thing most worth guarding on this branch. 1367 call
    sites take get_conn()'s return value and treat it as a real
    sqlite3.Connection; db.write() was added BESIDE it rather than folded
    INTO it so that none of them had to change. A later tidy-up that made
    get_conn() return a closing wrapper would break that quietly — and
    would also redefine `with get_conn() as c:`, which already means
    commit-and-keep-open in the standard library.

    MUTATION that pins it: make get_conn return contextlib.closing(conn)
    or any wrapper object.
    """
    conn = db.get_conn()
    try:
        assert type(conn) is sqlite3.Connection
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()
