"""
A column added to schema.sql without a migration is missing on every
database that already exists.

THE HAZARD. A live database — Emily's on Railway, the beta tester's — is
never rebuilt from app/schema.sql. `db.init_db()` runs schema.sql, whose
every statement is CREATE TABLE IF NOT EXISTS, so it creates new TABLES
and cannot touch an existing one's columns; then it runs `_MIGRATIONS`,
which is the only thing that can add a COLUMN to a table already there.

So a column added to schema.sql and forgotten in _MIGRATIONS exists on
every database built from scratch — every test run, every fresh checkout,
every reviewer's sandbox — and on no real one. The whole suite passes and
the deployed app raises "no such column". There are 104 entries in
_MIGRATIONS and, before this file, not one test compared them against
schema.sql at all.

WHY IT NEEDS A FIXTURE. The fault is invisible in the "after": a fresh
database has the column either way. Catching it needs a database built
before the column existed, so tests/fixtures/schema_snapshot.sql pins one.
Its own header says when to update it (rarely, and never merely to make
this go green).

WHAT IT CATCHES, EXACTLY. Measured by mutation on 2026-09-12, not assumed:

  - Remove ("chores", "mode") from _MIGRATIONS and test 1 goes RED. That
    column is declared in schema.sql, so a fresh database has it and an
    upgraded one would not — the asymmetric case, the dangerous one, and
    the one this file exists for.
  - Remove ("households", "timezone") and test 1 stays green. That column
    is in _MIGRATIONS and NOT in schema.sql — 33 of the 104 are like that —
    so dropping the migration removes it from fresh and upgraded alike. No
    asymmetry, and nothing quiet about it either: a fresh database loses
    the column too, so the ordinary suite goes red immediately. This guard
    is deliberately not the thing that catches that.

The snapshot is the OLDEST schema.sql reachable in the clone rather than a
copy of today's, which is what keeps test 1 from being vacuous: a snapshot
equal to the current schema can catch nothing until the next schema
change. Test 2 makes the mistake on purpose and proves the comparison
still works even if the snapshot ever drifts forward into uselessness.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import _MIGRATIONS, _run_migrations

REPO = Path(__file__).resolve().parent.parent
SCHEMA = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
SNAPSHOT = (REPO / "tests" / "fixtures" / "schema_snapshot.sql").read_text(encoding="utf-8")


def _columns(conn) -> dict[str, set[str]]:
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {t: {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")} for t in tables}


def _build(path: Path, *scripts: str) -> dict[str, set[str]]:
    """
    Run the real startup path over a database, exactly as db.init_db does:
    schema.sql, then _MIGRATIONS. Passing the snapshot first is what makes
    it an UPGRADE rather than a fresh build.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        for script in scripts:
            conn.executescript(script)
        _run_migrations(conn)
        conn.commit()
        return _columns(conn)
    finally:
        conn.close()


def test_an_existing_database_upgrades_to_the_same_columns_as_a_fresh_one(tmp_path):
    """
    The guard. A database built before today's schema, then started by
    today's code, must end up with every column a brand-new database has.
    A gap here means that column reaches nobody who already uses the app.
    """
    fresh = _build(tmp_path / "fresh.db", SCHEMA)
    upgraded = _build(tmp_path / "upgraded.db", SNAPSHOT, SCHEMA)

    gaps = {
        table: sorted(fresh[table] - upgraded.get(table, set()))
        for table in fresh
        if table in upgraded and fresh[table] - upgraded[table]
    }
    assert gaps == {}, (
        "these columns exist on a fresh database and NOT on an upgraded one — "
        "add each to _MIGRATIONS in app/db.py, or every database that already "
        f"exists will be missing it: {gaps}"
    )


def test_the_guard_would_catch_a_column_added_without_a_migration(tmp_path):
    """
    Proves test 1 can fail, by making the mistake on purpose: a column
    added to the schema and to no migration. Without this, a snapshot that
    drifted into being a copy of the live schema would leave test 1 unable
    to catch anything, and nothing would say so.
    """
    broken = SCHEMA.replace(
        "CREATE TABLE IF NOT EXISTS households (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,",
        "CREATE TABLE IF NOT EXISTS households (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    forgotten_column TEXT NOT NULL DEFAULT '',",
        1,
    )
    assert "forgotten_column" in broken, "the households CREATE TABLE no longer matches"

    fresh = _build(tmp_path / "fresh.db", broken)
    upgraded = _build(tmp_path / "upgraded.db", SNAPSHOT, broken)

    assert "forgotten_column" in fresh["households"]
    assert "forgotten_column" not in upgraded["households"], (
        "a column added only to schema.sql reached an existing database — "
        "if this ever passes, CREATE TABLE IF NOT EXISTS has started "
        "altering existing tables and the hazard this file guards is gone"
    )


def test_every_migration_names_a_table_the_schema_actually_creates():
    """
    A cheap sibling: a migration pointing at a table that no longer exists
    would raise on every startup, for everyone, at the ALTER. Nothing else
    checks this.
    """
    created = {
        line.split("CREATE TABLE IF NOT EXISTS ")[1].split(" (")[0].strip()
        for line in SCHEMA.splitlines()
        if "CREATE TABLE IF NOT EXISTS " in line
    }
    unknown = sorted({t for t, _c, _d in _MIGRATIONS if t not in created})
    assert unknown == [], f"_MIGRATIONS names tables schema.sql does not create: {unknown}"
