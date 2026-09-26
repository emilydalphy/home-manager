"""
No statement anywhere in app/ reaches a household-owned row BY A ROW ID
without naming household_id too.

READ THE SEVERITY HONESTLY. NOTHING IS LEAKING. Every one of the statements
this file is about was traced, and not one is reachable with a foreign id:
in every case the id has already come out of a household-filtered read, or
through require_household_row, a few lines above. grocery.remove_grocery_item
calls require_household_row and THEN deletes by bare id; stores.py and
recipes.py have no require_household_room anywhere and are safe because both
resolve their ids from a WHERE household_id = ? read first.

So this closes a hole in the GUARD, not a leak in the app, and it is Low for
that reason. What is genuinely wrong is that the scoping only holds because
each call site happened to do the right thing first — and _shared.household_id
exists precisely so that scoping is never something a caller has to remember.

THIS IS THE ONE SWEEP. It generalises the single-module sweep that used to
live in test_leftover_chain_household_filter.py (meal_plan_entries in
weekly_plan.py, 2026-09-24) to every household-owned table across the whole
of app/, and that file's sweep and shape tests moved here rather than a
second sweep being written beside it. The ast machinery is that file's,
unchanged, and so is its lesson: its FIRST design read the file's quoted runs
line by line and was defeated by a PURE REFORMAT — rewriting one guarded
statement as a triple-quoted block took the guard off with the whole suite
green. Parse, never grep.

MEASURED, 2026-09-26, against main (6f6a5b3): 107 statements in 23 files.
The card said 73 in 14; the real number is higher because this sweep covers
every owned table rather than a chosen few. 56 are fixed here, in eight
modules (staples 19, grocery 11, digest 6, recipes 6, household 5,
week_intake 5, stores 3, holidays 1); 45 are left for a later tranche and
are listed in _LATER_TRANCHE below; 6 must never be scoped at all and are in
_CROSS_HOUSEHOLD_ON_PURPOSE.

WHAT IT CANNOT SEE, written down rather than left to be found:
  * SQL assembled through a LOCAL VARIABLE — knowing what the variable holds
    needs dataflow this does not do. Asserted, not described, by
    test_what_the_sweep_still_cannot_see, so that if a later _sql_text learns
    to follow one, whoever does it gets a red test and deletes a limitation
    instead of discovering it.
  * A statement handed to something other than .execute/.executemany/
    .executescript — a helper of our own that takes SQL, say.
  * A table this schema does not create. _OWNED_TABLES is derived from
    app/schema.sql at import, so a new table with a household_id column is
    covered the moment it is declared; a table created only by a migration
    ALTER is not, because there is nothing to read the column off.
  * WHICH household a statement names. It checks the column is mentioned,
    never that the value is right — "AND household_id = 1" would pass.
  * household_id mentioned for some OTHER reason. The column only has to
    appear somewhere in the statement, so a JOIN whose ON clause says
    "ON m.household_id = h.id", or an INSERT ... SELECT that names the
    column, reads as guarded. Found by writing the JOIN shape case below,
    and left: the alternative is parsing the WHERE clause, which is a SQL
    parser, and the false NEGATIVE it buys is narrower than the false
    positives that would come with one. Asserted by
    test_a_household_id_in_a_join_reads_as_guarded so it stays known.
"""
import ast
import collections
import json
import os
import re

import pytest

from app import households
from app.db import get_conn
from app.tools import digest, grocery, household, staples
from app.tools._shared import DEFAULT_HOUSEHOLD_ID, use_household

_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
_SCHEMA = os.path.join(_APP, "schema.sql")


# --------------------------------------------------------------------------
# What counts as a household-owned table, read off the schema rather than
# kept by hand — so a table added tomorrow is swept without anybody
# remembering to add it here.
# --------------------------------------------------------------------------

def _owned_tables() -> set[str]:
    src = open(_SCHEMA, encoding="utf-8").read()
    out = set()
    for block in re.split(r"(?=CREATE TABLE)", src):
        m = re.search(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", block)
        if m and re.search(r"\bhousehold_id\b", block):
            out.add(m.group(1))
    return out


_OWNED_TABLES = _owned_tables()


# --------------------------------------------------------------------------
# The reader. This is test_leftover_chain_household_filter.py's, generalised
# only in that it also reports which function a statement sits in (the
# exemptions below are per function, not per file) and its line number.
# --------------------------------------------------------------------------

def _sql_text(node: ast.AST) -> str:
    """
    The SQL a node evaluates to, as near as can be read without running it.

    Handles plain and triple-quoted strings, implicit concatenation (already
    one Constant by the time ast sees it), f-strings, `+` concatenation and
    %-formatting. An interpolated value becomes {} — what it holds cannot be
    known here, and a statement is judged on the text around it. That is
    what makes the three interpolated-SET statements visible:
    inventory.py's two and plan_undo.py's read as
    "UPDATE ... SET {} WHERE id = ?".
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.JoinedStr):
        return "".join(_sql_text(v) if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _sql_text(node.left) + " " + _sql_text(node.right)
    return ""


def _statements(source: str) -> list[tuple[int, str, str]]:
    """Every (lineno, enclosing function, SQL) handed to a cursor call."""
    tree = ast.parse(source)
    funcs = [
        (n.lineno, n.end_lineno or n.lineno, n.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("execute", "executemany", "executescript"):
            continue
        if not node.args:
            continue
        text = " ".join(_sql_text(node.args[0]).split())
        if not text:
            continue
        owning = [f for f in funcs if f[0] <= node.lineno <= f[1]]
        out.append((node.lineno, owning[-1][2] if owning else "<module>", text))
    return out


# A statement that reaches a table and keys on a ROW ID — with or without a
# table alias in front of it, singly or by a list, by ? or by a named
# parameter. The household guard may sit on either side of the id, so the
# check is whether the column appears anywhere in the same statement rather
# than in a fixed position.
def _owned_tables_by_id(stmt: str) -> set[str]:
    hit = set()
    for table in _OWNED_TABLES:
        pattern = (
            r"\b(?:FROM|UPDATE|INTO|JOIN)\s+" + table + r"\b.*?\bWHERE\b.*?"
            r"(?:\b\w+\.)?\bid\s*(?:=\s*[?:]|IN\s*\()"
        )
        if re.search(pattern, stmt, re.IGNORECASE | re.DOTALL):
            hit.add(table)
    return hit


def _python_files() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(_APP):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _rel(path: str) -> str:
    return "app/" + os.path.relpath(path, _APP).replace(os.sep, "/")


# --------------------------------------------------------------------------
# THE TWO LISTS. They are different in kind and must not be merged: one is
# "not yet", the other is "never".
# --------------------------------------------------------------------------

# NEVER. These run across every household ON PURPOSE, and scoping them
# would break the app rather than fix it. Keyed by (file, function) so the
# exemption is as narrow as the reason for it — a NEW statement elsewhere in
# either file is still swept.
#
# If this sweep goes red because one of these grew a household guard, the
# answer is to take the guard off, not to widen the exemption.
_CROSS_HOUSEHOLD_ON_PURPOSE = {
    ("app/db.py", "_backfill_member_colors"):
        "a startup migration: it colours the first two adults of EVERY household",
    ("app/db.py", "_backfill_allergy_notes_from_facts"):
        "a startup migration over every household's facts (see the 2026-09-05 log entry)",
    ("app/db.py", "_migrate_chore_modes"):
        "a startup migration deriving chores.mode for every household",
    ("app/db.py", "_merge_duplicate_item_store_preferences"):
        "a startup migration collapsing duplicate rows in every household",
    ("app/db.py", "_backfill_recipe_cook_counters_from_ticks"):
        "a startup migration recounting cooks for every household",
    ("app/invites.py", "redeem_invite"):
        "runs BEFORE any household is bound — the invite is what establishes "
        "which household this is, so household_id() here would be the "
        "ContextVar's default (1) and every invite into any other household "
        "would be silently refused. It scopes itself the honest way: the id "
        "is the invite's own primary key, resolved from a hashed secret, and "
        "the statements after it use row['household_id'], never household_id()",
}

# NOT YET — the later tranches. Exact normalised SQL with a count, so that
# fixing one forces the entry off this list (the assertion is equality, not
# containment) and a NEW offender in an already-listed file is still caught.
# This list only ever SHRINKS; when it is empty, delete it and the branch on
# it in the sweep.
_LATER_TRANCHE = {
    "app/tools/attention.py": [
        (1, "DELETE FROM inventory_items WHERE id = ?"),
        (1, "SELECT rev FROM inventory_items WHERE id = ?"),
        (1, "UPDATE attention_items SET detail_json = ? WHERE id = ?"),
        (1, "UPDATE attention_items SET status = 'pending', summary = ?, detail_json = ?, resolved_at = NULL WHERE id = ?"),
        (1, "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?"),
    ],
    "app/tools/cooker.py": [
        (1, "DELETE FROM inventory_items WHERE id = ?"),
        (1, "SELECT * FROM weekly_plans WHERE id = ?"),
        (1, "SELECT cook_started_at FROM meal_plan_entries WHERE id = ?"),
        (1, "SELECT planning_mode FROM weekly_plans WHERE id = ?"),
        (1, "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?"),
    ],
    "app/tools/defrost.py": [
        (5, "DELETE FROM prep_tasks WHERE id = ?"),
        (1, "UPDATE prep_tasks SET description = ?, related_meal = ?, quantity = ? WHERE id = ?"),
        (1, "UPDATE prep_tasks SET task_date = ?, description = ?, inventory_item_id = ? WHERE id = ?"),
        (1, "UPDATE prep_tasks SET task_date = ?, quantity = ? WHERE id = ?"),
    ],
    "app/tools/first_open.py": [
        (2, "UPDATE members SET first_open_seen_at = ? WHERE id = ?"),
        (1, "UPDATE members SET first_open_seen_at = ? WHERE id = ? AND first_open_seen_at = ''"),
    ],
    "app/tools/inventory.py": [
        (1, "DELETE FROM inventory_items WHERE id = ?"),
        (1, "UPDATE inventory_items SET expiration_date = ?, updated_at = datetime('now') WHERE id = ?"),
        (2, "UPDATE inventory_items SET quantity = ?, updated_at = datetime('now') WHERE id = ?"),
        # The two interpolated SETs the card warns need care rather than a
        # regex pass. Seen, and left for whoever owns that module.
        (2, "UPDATE inventory_items SET {} WHERE id = ?"),
    ],
    "app/tools/meal_variety.py": [
        (1, "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?"),
    ],
    "app/tools/memory.py": [
        (1, "UPDATE facts SET text = ?, hard = ?, updated_at = datetime('now') WHERE id = ?"),
    ],
    "app/tools/plan_undo.py": [
        (1, "SELECT 1 FROM grocery_items WHERE id = ?"),
        (1, "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?"),
        # The third interpolated SET. The card names it as tonight.py's; the
        # 2026-09-24 drop-dish work LIFTED that machinery into plan_undo.py,
        # so this is where it is now.
        (1, "UPDATE meal_plan_entries SET {} WHERE id = ?"),
    ],
    "app/tools/slot_needs.py": [
        (1, "UPDATE slot_needs SET recommendation_confirmed = ?, updated_at = datetime('now') WHERE id = ?"),
        (1, "UPDATE slot_needs SET recommended_batch_from_entry_id = ?, recommended_defrost_item = ?, recommendation_confirmed = 0, updated_at = datetime('now') WHERE id = ?"),
    ],
    "app/tools/spices.py": [
        (1, "UPDATE grocery_items SET status = 'needed' WHERE id = ?"),
        (1, "UPDATE grocery_items SET status = 'needed', staple_id = ? WHERE id = ?"),
        (1, "UPDATE grocery_items SET status = 'spice' WHERE id = ?"),
    ],
    "app/tools/tonight.py": [
        (1, "DELETE FROM inventory_items WHERE id = ?"),
        (1, "SELECT rev FROM inventory_items WHERE id = ?"),
        (2, "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?"),
    ],
    "app/tools/usage.py": [
        # The reader shows this one with a dangling comma before WHERE,
        # because its SET clause is a ", ".join(...) — a Call, which
        # _sql_text cannot evaluate. It is still seen, and still unguarded.
        (1, "UPDATE error_events SET occurrences = occurrences + 1, last_seen_at = datetime('now'), WHERE id = ?"),
    ],
    "app/tools/weekly_plan.py": [
        (1, "SELECT name FROM recipes WHERE id = ?"),
        (1, "SELECT week_start_date FROM weekly_plans WHERE id = ?"),
        (1, "UPDATE prep_tasks SET task_date = ?, description = ? WHERE id = ?"),
    ],
}

# The eight modules this branch scoped. Named so the sweep can say, when it
# goes red in one of them, that the module is supposed to be finished.
_DONE_MODULES = (
    "app/tools/digest.py",
    "app/tools/grocery.py",
    "app/tools/holidays.py",
    "app/tools/household.py",
    "app/tools/recipes.py",
    "app/tools/staples.py",
    "app/tools/stores.py",
    "app/tools/week_intake.py",
)


def _offenders() -> collections.Counter:
    """Every unguarded by-id statement in app/, minus the deliberate exemptions."""
    found: collections.Counter = collections.Counter()
    for path in _python_files():
        rel = _rel(path)
        source = open(path, encoding="utf-8").read()
        for _lineno, func, stmt in _statements(source):
            if not _owned_tables_by_id(stmt):
                continue
            if "household_id" in stmt:
                continue
            if (rel, func) in _CROSS_HOUSEHOLD_ON_PURPOSE:
                continue
            found[(rel, stmt)] += 1
    return found


# --------------------------------------------------------------------------
# THE SWEEP.
# --------------------------------------------------------------------------

def test_no_statement_reaches_a_household_owned_row_by_id_alone():
    """
    THE GUARD. Not "these 56 sites are fixed" — that goes quiet the day a
    57th appears. Every statement in app/ that addresses a household-owned
    table by a row id must name household_id too, unless it is one of the
    six that run across households on purpose.

    Equality rather than containment, deliberately: fixing something on the
    later-tranche list makes this go red saying "take it off the list", which
    is what keeps the list shrinking honestly instead of outliving the work.
    """
    expected: collections.Counter = collections.Counter()
    for rel, entries in _LATER_TRANCHE.items():
        for count, stmt in entries:
            expected[(rel, stmt)] += count

    found = _offenders()
    if found == expected:
        return

    new = found - expected
    fixed = expected - found
    lines = []
    for (rel, stmt), n in sorted(new.items()):
        note = " (this module is supposed to be finished)" if rel in _DONE_MODULES else ""
        lines.append(f"  NOT SCOPED{note}: {rel}\n    {stmt}" + (f"   x{n}" if n > 1 else ""))
    for (rel, stmt), n in sorted(fixed.items()):
        lines.append(f"  ALREADY SCOPED, take it off _LATER_TRANCHE: {rel}\n    {stmt}"
                     + (f"   x{n}" if n > 1 else ""))
    hint = ""
    if any(rel in ("app/db.py", "app/invites.py") for (rel, _s) in new):
        hint = (
            "\n\nSTOP before scoping anything in app/db.py or app/invites.py. "
            "Those SIX statements run across every household ON PURPOSE — db.py's "
            "are startup migrations and backfills, and invites.redeem_invite runs "
            "before any household is bound, so household_id() there is the "
            "ContextVar's default (1) and every invite into any other household "
            "would be silently refused. They are meant to be in "
            "_CROSS_HOUSEHOLD_ON_PURPOSE; if this is why the sweep went red, put "
            "the exemption back rather than 'fixing' the migrations.\n"
        )
    raise AssertionError(
        "the household scope sweep disagrees with _LATER_TRANCHE.\n\n"
        "A statement that reaches a household-owned row by a bare id must "
        "name household_id as well — scope it like its neighbours "
        "(WHERE id = ? AND household_id = ?, with household_id() in the "
        "params). If it genuinely has to run across households, add it to "
        "_CROSS_HOUSEHOLD_ON_PURPOSE with the reason." + hint + "\n\n"
        + "\n".join(lines)
    )


# --------------------------------------------------------------------------
# THE GUARD ON THE GUARD. This repo has recorded a sweep silently stopping
# matching twice; a sweep that quietly finds nothing passes for ever and
# says nothing.
# --------------------------------------------------------------------------

_SWEEP_FLOOR = 40


def test_the_sweep_still_finds_the_statements_it_is_named_for():
    """
    Measured 107 across 23 files on main, 51 after this branch, of which 6
    are the deliberate exemptions — so 45 reach _offenders(). The floor is
    well under that so an ordinary later tranche does not have to move it;
    when the list is genuinely nearly empty, this test and _LATER_TRANCHE
    come out together.
    """
    found = _offenders()
    assert sum(found.values()) >= _SWEEP_FLOOR, (
        f"the sweep found only {sum(found.values())} statements, under the floor of "
        f"{_SWEEP_FLOOR}. Either a tranche has landed (lower the floor, shrink "
        f"_LATER_TRANCHE) or the sweep has stopped matching — check _sql_text and "
        f"_owned_tables_by_id before believing the good news."
    )


@pytest.mark.parametrize("rel,stmt", [
    # One by `id = ?`, one interpolated, in two different modules — so a
    # change that blinds the reader to either shape is caught by name.
    ("app/tools/defrost.py", "DELETE FROM prep_tasks WHERE id = ?"),
    ("app/tools/inventory.py", "UPDATE inventory_items SET {} WHERE id = ?"),
])
def test_the_sweep_still_names_two_specific_statements(rel, stmt):
    found = _offenders()
    assert found[(rel, stmt)] >= 1, (
        f"the sweep no longer sees {stmt!r} in {rel}. If it was scoped, take it "
        f"off _LATER_TRANCHE and name a different one here; if not, the reader is "
        f"broken."
    )


def test_the_three_interpolated_set_statements_are_all_seen():
    """
    The card singles these three out as needing care rather than a regex
    pass, and says a sweep blind to the hardest shapes is worth little. They
    are in another builder's modules, so none is fixed here — what is
    checked is that the sweep SEES all three, which was not assumed.

    The card names two in inventory.py and one in tonight.py. tonight.py's
    has MOVED: the 2026-09-24 drop-dish work lifted that snapshot/restore
    machinery into plan_undo.py, and `grep "SET {" app/` finds it there.
    """
    found = _offenders()
    assert found[("app/tools/inventory.py", "UPDATE inventory_items SET {} WHERE id = ?")] == 2
    assert found[("app/tools/plan_undo.py", "UPDATE meal_plan_entries SET {} WHERE id = ?")] == 1


def test_every_exemption_still_matches_something():
    """
    An exemption for a function that no longer exists, or that has been
    scoped, is a hole in the sweep that looks like a rule. Each of the six
    has to still be doing a job.
    """
    seen = set()
    for path in _python_files():
        rel = _rel(path)
        for _lineno, func, stmt in _statements(open(path, encoding="utf-8").read()):
            if _owned_tables_by_id(stmt) and "household_id" not in stmt:
                seen.add((rel, func))
    stale = sorted(set(_CROSS_HOUSEHOLD_ON_PURPOSE) - seen)
    assert stale == [], (
        "these exemptions no longer cover an unguarded statement — either the "
        "code moved and the key wants updating, or the exemption can go:\n  "
        + "\n  ".join(f"{rel}::{func}" for rel, func in stale)
    )


def test_the_migrations_are_exempt_deliberately_and_say_why():
    """
    app/db.py's five and app/invites.py's one run across households ON
    PURPOSE. The card's warning is the point of this test: an exemption
    nobody wrote a reason for is one that gets deleted the first time this
    sweep goes red for the right reason, and then the migrations get
    "fixed" into uselessness.
    """
    db = [k for k in _CROSS_HOUSEHOLD_ON_PURPOSE if k[0] == "app/db.py"]
    assert len(db) == 5, db
    for key, reason in _CROSS_HOUSEHOLD_ON_PURPOSE.items():
        assert len(reason) > 30, f"{key} has no real reason written down: {reason!r}"
    assert "BEFORE any household is bound" in _CROSS_HOUSEHOLD_ON_PURPOSE[
        ("app/invites.py", "redeem_invite")
    ]


def test_dropping_the_exemption_reports_the_migrations():
    """
    The other direction, so the exemption is known to be load-bearing
    rather than decoration: without it the sweep reports all six, and the
    reader can tell from the message that they are migrations.
    """
    unexempted: collections.Counter = collections.Counter()
    for path in _python_files():
        rel = _rel(path)
        if rel not in ("app/db.py", "app/invites.py"):
            continue
        for _lineno, func, stmt in _statements(open(path, encoding="utf-8").read()):
            if _owned_tables_by_id(stmt) and "household_id" not in stmt:
                unexempted[(rel, func)] += 1
    assert sum(unexempted.values()) == 6, unexempted
    assert all("backfill" in f or "migrate" in f or "merge_duplicate" in f or f == "redeem_invite"
               for _rel, f in unexempted), unexempted


# --------------------------------------------------------------------------
# WHAT THE READER SEES, and what it does not. Moved here from
# test_leftover_chain_household_filter.py with the sweep, because they are
# tests OF the reader. Every shape below defeated the line-based sweep that
# preceded it.
# --------------------------------------------------------------------------

_SEEN = {
    "one line": 'conn.execute("DELETE FROM grocery_items WHERE id = ?", (x,))',
    "implicit concatenation": (
        'conn.execute(\n'
        '    "DELETE FROM grocery_items "\n'
        '    "WHERE id = ?",\n'
        '    (x,),\n'
        ')'
    ),
    "triple quoted": 'conn.execute("""\nDELETE FROM staples WHERE id = ?\n""", (x,))',
    "single quoted": "conn.execute('DELETE FROM staples WHERE id = ?', (x,))",
    "f-string": 'conn.execute(f"DELETE FROM members WHERE id IN ({marks})", ids)',
    "interpolated SET": 'conn.execute(f"UPDATE inventory_items SET {fields} WHERE id = ?", p)',
    "plus concatenation": 'conn.execute(head + "FROM recipes WHERE id = ?", (x,))',
    "percent formatting": 'conn.execute("DELETE FROM facts WHERE id IN (%s)" % marks, ids)',
    "aliased id": 'conn.execute("SELECT 1 FROM prep_tasks pt WHERE pt.id = ?", (x,))',
    "id IN a list": 'conn.execute("DELETE FROM week_intake WHERE id IN (?, ?)", ids)',
    "lowercase sql": 'conn.execute("delete from stores where id = ?", (x,))',
    "a sql literal before the id": (
        'conn.execute("UPDATE chores SET name = \'\' WHERE id = ?", (x,))'
    ),
    "named parameter": 'conn.execute("DELETE FROM pets WHERE id = :id", d)',
    "table on a continuation line": (
        'conn.execute(\n'
        '    "DELETE FROM "\n'
        '    "slot_needs WHERE id = ?",\n'
        '    (x,),\n'
        ')'
    ),
    "params on the last literal line": (
        'conn.execute(\n'
        '    "DELETE FROM shopping_trips "\n'
        '    "WHERE id = ?", (x,),\n'
        ')'
    ),
    "executemany": 'conn.executemany("DELETE FROM staple_events WHERE id = ?", rows)',
    "a join reaching the owned table second": (
        'conn.execute("SELECT 1 FROM weekly_plans w JOIN meal_plan_entries m '
        'ON m.weekly_plan_id = w.id WHERE m.id = ?", (x,))'
    ),
}


@pytest.mark.parametrize("name", sorted(_SEEN))
def test_the_reader_sees_every_shape_app_could_use(name):
    stmts = _statements(_SEEN[name])
    assert stmts, f"the reader did not find the statement at all: {name}"
    assert any(_owned_tables_by_id(s) and "household_id" not in s for _l, _f, s in stmts), (
        f"the reader read {name} as guarded when it is not: {stmts}"
    )


@pytest.mark.parametrize("guarded", [
    'conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?", (x, h))',
    ('conn.execute(\n'
     '    "DELETE FROM staples WHERE id = ? "\n'
     '    "AND household_id = ?",\n'
     '    (x, h),\n'
     ')'),
    'conn.execute(f"DELETE FROM members WHERE id IN ({m}) AND household_id = ?", a)',
    'conn.execute("""\nDELETE FROM facts\nWHERE id = ? AND household_id = ?\n""", (x, h))',
    # household_id before the id, which is how several of this app's
    # existing guarded statements are written.
    'conn.execute("DELETE FROM prep_tasks WHERE household_id = ? AND id = ?", (h, x))',
])
def test_a_guarded_statement_is_not_reported(guarded):
    """
    The other direction, and not decoration: the line-based sweep this
    replaced reported a CORRECTLY guarded statement whenever the params
    tuple shared the final literal's line, telling whoever reformatted next
    to add a guard that was already there.
    """
    bad = [s for _l, _f, s in _statements(guarded)
           if _owned_tables_by_id(s) and "household_id" not in s]
    assert bad == [], bad


def test_the_households_table_itself_is_not_swept():
    """
    `UPDATE households SET ... WHERE id = ?` with household_id() as the
    param IS the scoping — holidays.py and digest.py both do it. households
    has no household_id column, so it is not in _OWNED_TABLES and a sweep
    that flagged it would be telling people to break it.
    """
    assert "households" not in _OWNED_TABLES
    stmt = 'conn.execute(f"UPDATE households SET {sets} WHERE id = ?", (v, household_id()))'
    bad = [s for _l, _f, s in _statements(stmt) if _owned_tables_by_id(s)]
    assert bad == [], bad


def test_a_guard_hiding_in_a_comment_does_not_count():
    """
    CLAUDE.md records three separate source tests that were satisfiable by
    their own prose. ast reads the statement, not the file, so a comment
    cannot reach it — pinned rather than assumed.
    """
    commented = (
        'conn.execute(  # household_id checked above\n'
        '    "DELETE FROM grocery_items WHERE id = ?",  # see household_id\n'
        '    (x,),\n'
        ')'
    )
    bad = [s for _l, _f, s in _statements(commented)
           if _owned_tables_by_id(s) and "household_id" not in s]
    assert len(bad) == 1, bad


def test_what_the_sweep_still_cannot_see():
    """
    Written down rather than left for the next person: SQL assembled through
    a LOCAL VARIABLE is invisible, because knowing what it holds needs
    dataflow this does not do.

    Asserted rather than described so it stays true — if a later _sql_text
    learns to follow a variable, this goes red and whoever did it gets to
    delete a limitation instead of discovering one.
    """
    via_variable = (
        'sql = "DELETE FROM grocery_items WHERE id = ?"\n'
        'conn.execute(sql, (x,))'
    )
    assert [s for _l, _f, s in _statements(via_variable)] == [], (
        "the reader can follow a variable now — take this limitation out of the "
        "module docstring and off the card"
    )


def test_the_owned_table_list_is_derived_and_not_hand_kept():
    """
    46 tables carry household_id today. The point is that the list comes off
    schema.sql, so a table added tomorrow is swept without anybody editing
    this file — the floor here is only a tripwire for the parsing breaking.
    """
    assert len(_OWNED_TABLES) >= 40, sorted(_OWNED_TABLES)
    for expected in ("grocery_items", "staples", "members", "meal_plan_entries",
                     "held_things", "holiday_answers"):
        assert expected in _OWNED_TABLES


# --------------------------------------------------------------------------
# BEHAVIOUR. Every site fixed here already had the right household in hand,
# so the guard must be a no-op in practice. Three of them are driven on a
# real two-household database: the owning household's before/after, and the
# same statement handed a foreign id.
#
# The foreign-id half is driven by the STATEMENT TEXT rather than through
# the function, for the reason the severity note at the top gives: every one
# of these functions refuses a foreign id before the statement is reached,
# so no function can be made to pass one. That refusal is the belt; this is
# the braces, and the sweep is what keeps them on.
# --------------------------------------------------------------------------

@pytest.fixture
def two_households():
    other = households.create_household("The Other Family", "other-passphrase")
    return {"other": other, "mine": DEFAULT_HOUSEHOLD_ID}


def _seed(hid, fn):
    with use_household(hid):
        return fn()


def test_pausing_a_staple_still_works_for_its_own_household(two_households):
    """staples.pause_staple — UPDATE staples ... WHERE id = ? AND household_id = ?"""
    mine = _seed(DEFAULT_HOUSEHOLD_ID, lambda: staples.add_staple("Dish soap", every_days=30))
    theirs = _seed(two_households["other"], lambda: staples.add_staple("Dish soap", every_days=30))
    assert mine["id"] != theirs["id"]

    assert staples.pause_staple(mine["id"], True)["paused"] is True
    assert staples.pause_staple(mine["id"], False)["paused"] is False

    # The other household's identical staple is untouched throughout.
    with use_household(two_households["other"]):
        mirror = {s["id"]: s["paused"] for s in staples.list_staples()}
    assert mirror[theirs["id"]] is False


def test_a_foreign_staple_id_is_refused_and_the_guarded_write_moves_nothing(two_households):
    """
    Both halves: pause_staple refuses the id (the read above the write is
    household-scoped), and the guarded statement itself changes no row when
    handed one.
    """
    theirs = _seed(two_households["other"], lambda: staples.add_staple("Cling film", every_days=30))
    with pytest.raises(ValueError):
        staples.pause_staple(theirs["id"], True)

    conn = get_conn()
    changed = conn.execute(
        "UPDATE staples SET paused = 1, updated_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (theirs["id"], DEFAULT_HOUSEHOLD_ID),
    )
    conn.commit()
    assert changed.rowcount == 0
    still = conn.execute(
        "SELECT paused FROM staples WHERE id = ?", (theirs["id"],)
    ).fetchone()["paused"]
    conn.close()
    assert still == 0, "the other household's staple is untouched"


def test_updating_a_grocery_line_still_works_for_its_own_household(two_households):
    """grocery.update_grocery_item — UPDATE grocery_items ... WHERE id = ? AND household_id = ?"""
    mine = _seed(DEFAULT_HOUSEHOLD_ID, lambda: grocery.add_grocery_item("Oat milk", "1"))
    theirs = _seed(two_households["other"], lambda: grocery.add_grocery_item("Oat milk", "1"))

    out = grocery.update_grocery_item(mine["item_id"], quantity="2", category="dairy")
    assert out["quantity"] == "2" and out["category"] == "dairy" and out["found"] is True

    with pytest.raises(ValueError):
        grocery.update_grocery_item(theirs["item_id"], quantity="99")

    conn = get_conn()
    changed = conn.execute(
        "UPDATE grocery_items SET quantity = ?, category = ? WHERE id = ? AND household_id = ?",
        ("99", "other", theirs["item_id"], DEFAULT_HOUSEHOLD_ID),
    )
    conn.commit()
    row = conn.execute(
        "SELECT quantity FROM grocery_items WHERE id = ?", (theirs["item_id"],)
    ).fetchone()
    conn.close()
    assert changed.rowcount == 0
    assert row["quantity"] == "1", "the other household's line keeps its own quantity"


def test_a_members_restriction_still_lands_on_the_right_person(two_households):
    """
    household.set_member_dietary_restrictions — the read AND the write.
    Two households with a member of the SAME NAME, which is the shape that
    makes this worth driving: the app's only member identity is the name.
    """
    _seed(DEFAULT_HOUSEHOLD_ID, lambda: household.add_member("Sam"))
    _seed(two_households["other"], lambda: household.add_member("Sam"))

    out = household.set_member_dietary_restrictions("Sam", ["allergy: peanuts"])
    assert out["dietary_restrictions"] == ["allergy: peanuts"]
    again = household.set_member_dietary_restrictions("Sam", ["vegetarian"])
    assert sorted(again["dietary_restrictions"]) == ["allergy: peanuts", "vegetarian"]

    # Read the column directly: this is a test about which ROW the write
    # landed on, and get_household_people filters on age_group.
    conn = get_conn()
    rows = {
        r["household_id"]: r["dietary_restrictions_json"]
        for r in conn.execute("SELECT household_id, dietary_restrictions_json FROM members "
                              "WHERE name = 'Sam'")
    }
    conn.close()
    assert sorted(json.loads(rows[DEFAULT_HOUSEHOLD_ID])) == ["allergy: peanuts", "vegetarian"]
    assert json.loads(rows[two_households["other"]] or "[]") == [], (
        "the other household's Sam has no restrictions at all"
    )


def test_a_members_phone_write_stays_in_its_own_household(two_households):
    """digest.set_morning_text_for_member — the members write the sheet posts."""
    _seed(DEFAULT_HOUSEHOLD_ID, lambda: household.set_member_age_group("Alex", "adult"))
    _seed(two_households["other"], lambda: household.set_member_age_group("Alex", "adult"))

    conn = get_conn()
    mine = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND name = 'Alex'",
        (DEFAULT_HOUSEHOLD_ID,),
    ).fetchone()["id"]
    theirs = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND name = 'Alex'",
        (two_households["other"],),
    ).fetchone()["id"]
    conn.close()

    digest.set_morning_text_for_member(mine, phone="416-555-0100")
    with pytest.raises(ValueError):
        digest.set_morning_text_for_member(theirs, phone="416-555-0199")

    conn = get_conn()
    changed = conn.execute(
        "UPDATE members SET phone = ? WHERE id = ? AND household_id = ?",
        ("416-555-0199", theirs, DEFAULT_HOUSEHOLD_ID),
    )
    conn.commit()
    rows = {
        r["household_id"]: r["phone"]
        for r in conn.execute("SELECT household_id, phone FROM members WHERE name = 'Alex'")
    }
    conn.close()
    assert changed.rowcount == 0
    assert rows[DEFAULT_HOUSEHOLD_ID] == "+14165550100"
    assert rows[two_households["other"]] == "", "the other household's Alex has no number"


# --------------------------------------------------------------------------
# Two more limitations and one inherited claim, kept at the foot because
# each was written after a specific thing went wrong rather than planned.
# --------------------------------------------------------------------------

def test_a_household_id_in_a_join_reads_as_guarded():
    """
    The blind spot the JOIN shape case above turned up, asserted so it stays
    known rather than being rediscovered: household_id only has to APPEAR in
    the statement, so a join that mentions it in its ON clause reads as
    guarded even though nothing in the WHERE narrows the row to a household.

    Left rather than fixed. Telling a WHERE clause from an ON clause means
    parsing SQL, and no statement in app/ has this shape today — `grep -n
    "ON .*household_id" app/` finds only correctly-scoped joins. If one
    appears, this test is where to start.
    """
    joined = (
        'conn.execute("SELECT 1 FROM households h JOIN members m '
        'ON m.household_id = h.id WHERE m.id = ?", (x,))'
    )
    bad = [s for _l, _f, s in _statements(joined)
           if _owned_tables_by_id(s) and "household_id" not in s]
    assert bad == [], (
        "the reader can tell a WHERE from an ON now — take this limitation out "
        "of the module docstring"
    )


def test_the_claim_the_single_module_sweep_used_to_make_still_holds():
    """
    The sweep this generalises made one narrower claim: no statement in
    weekly_plan.py reaches meal_plan_entries by a row id without naming
    household_id. That claim is carried by the equality assertion above (none
    of weekly_plan.py's three remaining offenders is on meal_plan_entries),
    but it is restated here so that removing the old file's sweep took no
    coverage with it, and so the six statements it fixed on 2026-09-24 cannot
    quietly come unguarded.
    """
    source = open(os.path.join(_APP, "tools", "weekly_plan.py"), encoding="utf-8").read()
    bad = [
        stmt for _l, _f, stmt in _statements(source)
        if "meal_plan_entries" in _owned_tables_by_id(stmt) and "household_id" not in stmt
    ]
    assert bad == [], bad
