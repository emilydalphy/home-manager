"""
Every statement in weekly_plan.py that reaches a meal_plan_entries row BY A
ROW ID is scoped to the household as well.

READ THE SEVERITY HONESTLY, because this log has had to unpick an
over-claimed one before. Six statements carried no household guard, and
NONE of them was reachable with a foreign id: in all six the id had already
come out of a household-filtered read a few lines above. Every one was
driven with another household's ids, on both trees, and nothing crossed.
So this closes a hole in the guard, not a leak in the app. What was really
wrong is that the scoping had become a property of whoever called the
statement rather than of the statement — and the whole point of
_shared.household_id is that a caller cannot be the thing that remembers.

The six: repair_leftover_chains' read-back and its note write,
_unlink_leftover_target's note write, swap_component_in_plan's delete, and
— found by the review of the first four — clear_plan_slot's `id IN (...)`
delete and _dedupe_duplicate_slots'. That last pair is the reason the
headline above says "a row id" rather than "id = ?": the first version of
this file said the class was closed while two statements in the same module
were still open, because its sweep could only see one spelling.

The reproduction is still worth having in the file: handed a foreign id,
the statements as they stood really did read, rewrite and delete another
household's row. That is what they would do the first time somebody
resolved an id some other way.

THE SWEEP THAT USED TO BE AT THE BOTTOM OF THIS FILE HAS MOVED, 2026-09-26,
to tests/test_household_scope_sweep.py, which generalises it from this one
module and one table to every household-owned table across the whole of
app/ — there is exactly one sweep in tests/, not two. Its second sentence is
still worth reading wherever it lives: the FIRST design read this file's
quoted runs line by line, and an independent review defeated it with a PURE
REFORMAT — rewriting one of the four fixed statements as a triple-quoted
block took the guard off with the whole 6633-test suite still green. It is
built on `ast` now; every shape that defeated the old one is a test case,
and the shapes it still cannot see are test cases too, so they stay true
rather than being described.
"""
import json

import pytest

from app import households
from app.db import get_conn
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
    CHARACTERISATION, green on main and on the branch alike — the pair to
    the test above. It drives the NEW statement text by hand for the same
    reason that one drives the old: no function can be made to pass a
    foreign id, so neither of these can be a catch.

    Labelled honestly after a review pointed out that calling it "the fix,
    stated as behaviour" claimed more than it does — what it actually shows
    is that the guard clause does what a guard clause does, which is worth
    having beside the reproduction and is not evidence the fix landed. The
    sweep is what carries that.
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
# The sweep MOVED, 2026-09-26, and there is now exactly one in tests/.
#
# tests/test_household_scope_sweep.py generalises it from this one module and
# one table to every household-owned table across the whole of app/. The ast
# reader, all twelve shape cases, the guarded-statement cases, the
# comment-cannot-count case and the local-variable blind spot went with it
# unchanged — they are tests OF the reader, so they belong beside it — and
# that file restates this one's narrower claim by name
# (test_the_claim_the_single_module_sweep_used_to_make_still_holds), so
# nothing here is uncovered.
#
# What stays in this file is the pair of characterisations below: what the
# unguarded statements really did when handed a foreign id, and what the
# guarded ones do. They are the measured basis for the severity note at the
# top and are green on main and on the branch alike.
# --------------------------------------------------------------------------
