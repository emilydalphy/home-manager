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

The sweep at the bottom is the part that earns its keep, and it is on its
second design. The first read the file's quoted runs line by line, and an
independent review defeated it with a PURE REFORMAT — rewriting one of the
four fixed statements as a triple-quoted block took the guard off with the
whole 6633-test suite still green. It is built on `ast` now, which is both
shorter and sees the shapes a line reader cannot; every shape that defeated
the old one is a test case, and the one shape it still cannot see is a test
case too, so it stays true rather than being described.
"""
import ast
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
# The sweep — the part that catches the NEXT one.
# --------------------------------------------------------------------------

_MODULE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "tools", "weekly_plan.py")

# A statement that reaches meal_plan_entries and keys on a row id, with or
# without a table alias in front of it, singly or by a list. The household
# guard may sit on either side, so the check is whether the column appears
# anywhere in the same statement rather than in a fixed position.
_BY_ID = re.compile(
    r"\b(?:FROM|UPDATE|INTO)\s+meal_plan_entries\b.*?\bWHERE\b.*?"
    r"(?:\b\w+\.)?\bid\s*(?:=\s*[?:]|IN\s*\()",
    re.IGNORECASE | re.DOTALL,
)


def _sql_text(node: ast.AST) -> str:
    """
    The SQL a node evaluates to, as near as can be read without running it.

    Built on ast rather than on the file's lines, and that is the whole
    lesson of this helper. The first version read quoted runs line by line
    and rebuilt Python's implicit concatenation by hand — which meant it
    could only see a statement written the one way the four sites happened
    to be written. An independent review defeated it with a PURE REFORMAT:
    rewriting one of the four as a triple-quoted block took the household
    guard off with the whole 6633-test suite still green. Python's own
    parser already joins adjacent literals into a single Constant, so
    asking it is both shorter and catches the shapes a line reader cannot.

    Handles: plain and triple-quoted strings, implicit concatenation
    (already one Constant by the time ast sees it), f-strings, `+`
    concatenation, and %-formatting. An interpolated value becomes {} —
    what it holds cannot be known here, and a statement is judged on the
    text around it.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        return "".join(_sql_text(v) if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _sql_text(node.left) + " " + _sql_text(node.right)
    return ""


def _statements(source: str) -> list[str]:
    """Every argument to a .execute()/.executemany() call that mentions the table."""
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("execute", "executemany", "executescript"):
            continue
        if not node.args:
            continue
        text = " ".join(_sql_text(node.args[0]).split())
        if "meal_plan_entries" in text:
            out.append(text)
    return out


def test_no_statement_reaches_a_meal_plan_entry_by_id_alone():
    """
    THE GUARD. Not "the six sites are fixed" — that would go quiet the day
    a seventh appears. Every statement in the module that addresses
    meal_plan_entries by a row id must name household_id too.

    Red against main: six statements. Four by `id = ?`
    (repair_leftover_chains' read-back and its write, _unlink_leftover_target's
    write, swap_component_in_plan's delete) and two by `id IN (...)`
    (clear_plan_slot's delete, _dedupe_duplicate_slots' delete) — the second
    pair found by the review of the first, and conspicuous because in
    clear_plan_slot the statement four lines above it is guarded.
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


# Every shape this sweep is claimed to see, and the ones it does not. The
# review that found the reformat hole defeated the old sweep with ten
# shapes; these are the same ten, so a future change to _sql_text is
# measured against them rather than argued about.
_SEEN = {
    "one line": 'conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (x,))',
    "implicit concatenation": (
        'conn.execute(\n'
        '    "DELETE FROM meal_plan_entries "\n'
        '    "WHERE id = ?",\n'
        '    (x,),\n'
        ')'
    ),
    "triple quoted": 'conn.execute("""\nDELETE FROM meal_plan_entries WHERE id = ?\n""", (x,))',
    "f-string": 'conn.execute(f"DELETE FROM meal_plan_entries WHERE id IN ({marks})", ids)',
    "plus concatenation": 'conn.execute(head + "FROM meal_plan_entries WHERE id = ?", (x,))',
    "percent formatting": (
        'conn.execute("DELETE FROM meal_plan_entries WHERE id IN (%s)" % marks, ids)'
    ),
    "aliased id": (
        'conn.execute("SELECT 1 FROM meal_plan_entries mpe WHERE mpe.id = ?", (x,))'
    ),
    "id IN a list": 'conn.execute("DELETE FROM meal_plan_entries WHERE id IN (?, ?)", ids)',
    "lowercase sql": 'conn.execute("delete from meal_plan_entries where id = ?", (x,))',
    "a sql literal before the id": (
        'conn.execute("UPDATE meal_plan_entries SET reasoning = \'\' WHERE id = ?", (x,))'
    ),
    "named parameter": 'conn.execute("DELETE FROM meal_plan_entries WHERE id = :id", d)',
    "params on the last literal line": (
        'conn.execute(\n'
        '    "DELETE FROM meal_plan_entries "\n'
        '    "WHERE id = ?", (x,),\n'
        ')'
    ),
}


@pytest.mark.parametrize("name", sorted(_SEEN))
def test_the_sweep_sees_every_shape_this_module_could_use(name):
    """
    Each of these removed the guard and went unseen by the line-based
    sweep this replaced; two of them (a triple-quoted block, `+`
    concatenation) are shapes weekly_plan.py already uses elsewhere, so
    the hole was not hypothetical — a pure reformat of one of the four
    fixed statements passed the whole suite.
    """
    found = _statements(_SEEN[name])
    assert found, f"the sweep did not find the statement at all: {name}"
    assert any(_BY_ID.search(f) and "household_id" not in f for f in found), (
        f"the sweep read {name} as guarded when it is not: {found}"
    )


@pytest.mark.parametrize("guarded", [
    'conn.execute("DELETE FROM meal_plan_entries WHERE id = ? AND household_id = ?", (x, h))',
    ('conn.execute(\n'
     '    "DELETE FROM meal_plan_entries WHERE id = ? "\n'
     '    "AND household_id = ?",\n'
     '    (x, h),\n'
     ')'),
    'conn.execute(f"DELETE FROM meal_plan_entries WHERE id IN ({m}) AND household_id = ?", a)',
    'conn.execute("""\nDELETE FROM meal_plan_entries\nWHERE id = ? AND household_id = ?\n""", (x, h))',
])
def test_a_guarded_statement_is_not_reported(guarded):
    """
    The other direction, and it is not decoration: the sweep this replaced
    reported a CORRECTLY guarded statement as an offender whenever the
    params tuple shared the final literal's line, telling whoever
    reformatted next to add a guard that was already there.
    """
    offenders = [s for s in _statements(guarded) if _BY_ID.search(s) and "household_id" not in s]
    assert offenders == [], offenders


def test_a_guard_hiding_in_a_comment_does_not_count():
    """
    CLAUDE.md records three separate source tests that were satisfiable by
    their own prose. ast reads the statement, not the file, so a comment
    cannot reach it — pinned rather than assumed.
    """
    commented = (
        'conn.execute(  # household_id checked above\n'
        '    "DELETE FROM meal_plan_entries WHERE id = ?",  # see household_id\n'
        '    (x,),\n'
        ')'
    )
    offenders = [s for s in _statements(commented) if _BY_ID.search(s) and "household_id" not in s]
    assert len(offenders) == 1, offenders


def test_what_the_sweep_still_cannot_see():
    """
    Written down rather than left for the next person, which is the half
    of this that the review said was cheaper than the code: a statement
    assembled through a LOCAL VARIABLE is invisible, because knowing what
    it holds needs dataflow this does not do.

    It is asserted rather than described so that it stays true — if a
    later _sql_text learns to follow a variable, this test goes red and
    whoever did it gets to delete a limitation instead of discovering one.
    """
    via_variable = (
        'sql = "DELETE FROM meal_plan_entries WHERE id = ?"\n'
        'conn.execute(sql, (x,))'
    )
    assert _statements(via_variable) == [], (
        "the sweep can follow a variable now — take this limitation out of "
        "the docstring above and off the card"
    )
