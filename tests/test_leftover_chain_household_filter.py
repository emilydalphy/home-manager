"""
Every statement in weekly_plan.py that reaches a meal_plan_entries row BY ID
is scoped to the household as well.

READ THE SEVERITY HONESTLY, because this log has had to unpick an
over-claimed one before. Four statements carried no household guard
(repair_leftover_chains' read-back and its note write, _unlink_leftover_target's
note write, and swap_component_in_plan's delete), and NONE of them was
reachable with a foreign id: in all four the id had already come out of a
household-filtered read a few lines above. So this closes a hole in the
guard, not a leak in the app. What was really wrong is that the scoping had
become a property of whoever called the statement rather than of the
statement — and the whole point of _shared.household_id is that a caller
cannot be the thing that remembers.

The reproduction is still worth having in the file: handed a foreign id,
the statements as they stood really did read, rewrite and delete another
household's row. That is what they would do the first time somebody
resolved an id some other way.

The sweep at the bottom is the part that earns its keep. It is the shape
tests/test_last_clock_pockets.py uses for the clock reads: it does not
assert that today's four sites are fixed, it asserts that no fifth one can
appear.
"""
import json
import os
import re

import pytest

from app import households
from app.db import get_conn
from app.tools import weekly_plan
from app.tools._shared import DEFAULT_HOUSEHOLD_ID


def _seed_entry(conn, hid: str | int, dish: str) -> int:
    """One draft plan and one dinner in it, for the given household."""
    conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status) "
        "VALUES (?, '2026-09-21', 'draft')",
        (hid,),
    )
    plan = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        "INSERT INTO meal_plan_entries (household_id, weekly_plan_id, date, slot, "
        "slot_state, freeform_meal, derived_from_json) "
        "VALUES (?, ?, '2026-09-21', 'dinner', 'planned', ?, ?)",
        (hid, plan, dish, json.dumps({"owner": dish})),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


@pytest.fixture
def two_households():
    """Household 1's dinner and another household's dinner, side by side."""
    other = households.create_household("The Other Family", "other-passphrase")
    conn = get_conn()
    mine = _seed_entry(conn, DEFAULT_HOUSEHOLD_ID, "Emily's chili")
    theirs = _seed_entry(conn, other, "Their chili")
    conn.commit()
    conn.close()
    return {"other": other, "mine": mine, "theirs": theirs}


# --------------------------------------------------------------------------
# The reproduction: what the four statements did before the guard.
# --------------------------------------------------------------------------

def test_an_unguarded_statement_really_does_cross_the_boundary(two_households):
    """
    CHARACTERISATION, green on main and on the branch alike — it drives the
    OLD statement text by hand rather than any function, because no function
    can be made to pass a foreign id. It is here so the severity claim above
    is measured rather than asserted: the statements were not harmless in
    themselves, they were unreachable.
    """
    theirs = two_households["theirs"]
    conn = get_conn()
    row = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (theirs,)
    ).fetchone()
    assert row is not None, "the unguarded read reaches another household's row"
    assert json.loads(row["derived_from_json"])["owner"] == "Their chili"

    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps({"make_double_note": "written by household 1"}), theirs),
    )
    conn.commit()
    after = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (theirs,)
    ).fetchone()
    assert "written by household 1" in after["derived_from_json"]
    conn.close()


def test_the_same_statements_with_the_guard_touch_nothing(two_households):
    """
    The fix, stated as behaviour: the identical statements, scoped the way
    weekly_plan.py now writes them, find and change nothing for a foreign id
    while still finding the household's own row.
    """
    theirs, mine = two_households["theirs"], two_households["mine"]
    conn = get_conn()

    foreign = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (theirs, DEFAULT_HOUSEHOLD_ID),
    ).fetchone()
    assert foreign is None, "the guarded read must not see another household's row"

    own = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (mine, DEFAULT_HOUSEHOLD_ID),
    ).fetchone()
    assert own is not None, "and must still see this household's own row"

    changed = conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps({"make_double_note": "nope"}), theirs, DEFAULT_HOUSEHOLD_ID),
    )
    conn.commit()
    assert changed.rowcount == 0

    gone = conn.execute(
        "DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (theirs, DEFAULT_HOUSEHOLD_ID),
    )
    conn.commit()
    assert gone.rowcount == 0

    still_there = conn.execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE household_id = ?",
        (two_households["other"],),
    ).fetchone()["n"]
    assert still_there == 1, "the other household's dinner is untouched"
    conn.close()


# --------------------------------------------------------------------------
# The sweep — the part that catches the NEXT one.
# --------------------------------------------------------------------------

_MODULE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "tools", "weekly_plan.py")

# A statement that reaches meal_plan_entries and keys on a bare `id`. The
# household guard may sit on either side of it, so the check is for the
# column appearing anywhere in the same statement rather than in a fixed
# position.
_BY_ID = re.compile(r"(?:FROM|UPDATE|INTO)\s+meal_plan_entries\b[^\"']*?\bWHERE\b[^\"']*?\bid\s*=\s*\?")


def _statements(source: str) -> list[str]:
    """
    Every SQL string literal in the file, with Python's implicit
    concatenation of adjacent literals joined back up — a statement split
    across two quoted lines is ONE statement, and reading the halves apart
    is how a guard on the second line goes unnoticed.

    Only a line that is ITSELF nothing but a string literal continues a
    statement. An earlier version joined every quoted run on any following
    line, which swept the params tuple in with it — so a statement whose
    params happened to mention "household_id" (row["household_id"]) would
    have read as guarded when it was not. A false negative in the one test
    whose whole job is not to have one.
    """
    only_a_literal = re.compile(r'^(?:[rbf]*"(?:[^"\\]|\\.)*"\s*)+,?$')
    out, buf = [], []
    for raw in source.splitlines():
        line = raw.strip()
        if only_a_literal.match(line):
            buf.extend(re.findall(r'"((?:[^"\\]|\\.)*)"', line))
            continue
        # Not a bare literal: the statement (if any) ended on the line before.
        if buf:
            out.append(" ".join(buf))
            buf = []
        # A one-line call like conn.execute("SELECT ...", (a, b)) still has
        # its statement as the FIRST literal on the line.
        quoted = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        if quoted and "meal_plan_entries" in quoted[0]:
            out.append(quoted[0])
    if buf:
        out.append(" ".join(buf))
    return [s for s in out if "meal_plan_entries" in s]


def test_no_statement_reaches_a_meal_plan_entry_by_id_alone():
    """
    THE GUARD. Not "the four sites are fixed" — that would go quiet the day
    a fifth appears. Every statement in the module that addresses
    meal_plan_entries by id must name household_id too.

    Red against main: four statements (repair_leftover_chains' read-back at
    ~:2021 and its write at ~:2042, _unlink_leftover_target's write at
    ~:2191, swap_component_in_plan's delete at ~:7219).
    """
    source = open(_MODULE, encoding="utf-8").read()
    offenders = [
        stmt for stmt in _statements(source)
        if _BY_ID.search(stmt) and "household_id" not in stmt
    ]
    assert offenders == [], (
        "these statements reach a meal_plan_entries row by id with no household "
        "guard — scope them like their neighbours:\n  " + "\n  ".join(offenders)
    )


def test_the_sweep_can_actually_see_an_unguarded_statement():
    """
    The sweep is only worth having if it fails when it should, and a regex
    over source is exactly the kind of test that quietly matches nothing.
    Hand it the offending shape and require it to object.
    """
    bad = 'UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?'
    good = 'UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?'
    assert _BY_ID.search(bad) and "household_id" not in bad
    assert _BY_ID.search(good) and "household_id" in good


def test_the_statement_splitter_joins_a_wrapped_statement():
    """
    The one way this sweep could go vacuously green: a statement whose guard
    is on the continuation line read as two separate statements, the first
    of which has no household_id in it. Pin the joining.
    """
    wrapped = '''        row = conn.execute(
            "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? "
            "AND household_id = ?",
            (entry_id, household_id()),
        ).fetchone()'''
    stmts = [s for s in _statements(wrapped) if "meal_plan_entries" in s]
    assert stmts, "the splitter found no statement at all"
    assert all("household_id" in s for s in stmts), (
        "a wrapped statement was read as two, so its guard went unseen: %r" % stmts
    )
