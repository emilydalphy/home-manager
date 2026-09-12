"""
Every chore has a chosen owner — owned / shared / whoever.

Loop Board "Chores v1: Every chore has a chosen owner" (Emily, 2026-09-11).
Before this, who did a chore was only ever implied by how many people sat
in its rotation (rotation_member_ids_json, round-robined by
generate_chore_schedule). Now a chore says so outright — `chores.mode` —
and the two older columns say who:

    owned    one named person, every time (the default)
    shared   the named people take turns (the round-robin as before)
    whoever  nobody in particular; first to tick it

This file covers the data model and engine, the chat tools, the starter
list and its save route, and the one place a screen already prints a chore
row (the hidden Now card). The Plan | Chores screen is a later card.
"""
from __future__ import annotations

import datetime
import json
import re
import types
from pathlib import Path

import pytest

from app import agent, tools
from app.db import _MIGRATIONS, _migrate_chore_modes, _run_migrations, get_conn

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


@pytest.fixture
def two_adults():
    return {"Emily": _adult("Emily"), "Vineeth": _adult("Vineeth")}


def _chore_row(chore_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM chores WHERE id = ?", (chore_id,)).fetchone()
    conn.close()
    return row


def _instances(chore_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, assignee_id, status, completed_by_member_id, due_date FROM chore_instances "
        "WHERE chore_id = ? ORDER BY due_date ASC, id ASC",
        (chore_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _legacy_chore(name: str, rotation: list[int], default: int | None = None) -> int:
    """A chore row exactly as the pre-mode build wrote it: mode still ''."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO chores (household_id, name, default_assignee_id, rotation_member_ids_json, mode) "
        "VALUES (1, ?, ?, ?, '')",
        (name, default if default is not None else (rotation[0] if rotation else None), json.dumps(rotation)),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


# --- 1. schema + migration ------------------------------------------------

def test_the_columns_are_added_once_and_survive_a_rerun():
    conn = get_conn()
    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    chores = [r["name"] for r in conn.execute("PRAGMA table_info(chores)")]
    instances = [r["name"] for r in conn.execute("PRAGMA table_info(chore_instances)")]
    conn.close()
    assert chores.count("mode") == 1
    assert instances.count("completed_by_member_id") == 1


def test_migration_one_person_rotation_becomes_owned(two_adults):
    chore_id = _legacy_chore("Bathrooms", [two_adults["Vineeth"]])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "owned"
    assert row["default_assignee_id"] == two_adults["Vineeth"]
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Vineeth"]]


def test_migration_two_person_rotation_becomes_shared(two_adults):
    chore_id = _legacy_chore("Vacuuming", [two_adults["Emily"], two_adults["Vineeth"]])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "shared"
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Emily"], two_adults["Vineeth"]]


def test_migration_empty_rotation_becomes_whoever(two_adults):
    chore_id = _legacy_chore("Bins", [])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "whoever"
    assert row["default_assignee_id"] is None


def test_migration_default_assignee_only_counts_as_one_person(two_adults):
    """The oldest rows had a default_assignee_id and an empty rotation —
    that was one named person too, not nobody."""
    chore_id = _legacy_chore("Litter", [], default=two_adults["Emily"])
    conn = get_conn()
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "owned"
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Emily"]]


def test_migration_is_idempotent_and_leaves_decided_rows_alone(two_adults):
    chore_id = _legacy_chore("Vacuuming", [two_adults["Emily"], two_adults["Vineeth"]])
    conn = get_conn()
    _migrate_chore_modes(conn)
    # The household then decides Emily owns it; a restart must not undo that.
    conn.execute(
        "UPDATE chores SET mode = 'owned', rotation_member_ids_json = ?, default_assignee_id = ? WHERE id = ?",
        (json.dumps([two_adults["Emily"]]), two_adults["Emily"], chore_id),
    )
    _migrate_chore_modes(conn)
    conn.commit()
    conn.close()
    row = _chore_row(chore_id)
    assert row["mode"] == "owned"
    assert json.loads(row["rotation_member_ids_json"]) == [two_adults["Emily"]]


def test_a_row_the_migration_has_not_reached_still_reads_by_the_same_rule(two_adults):
    """Nothing depends on the backfill having run: a '' row reads as what
    its rotation implies, everywhere."""
    _legacy_chore("Bathrooms", [two_adults["Vineeth"]])
    _legacy_chore("Bins", [])
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["mode"] == "owned"
    assert by_name["Bathrooms"]["owner"] == "Vineeth"
    assert by_name["Bins"]["mode"] == "whoever"


# The two columns this work added. Named explicitly rather than derived from
# every _MIGRATIONS entry for these tables, because rotation_member_ids_json
# is also a migration and predates this one — and it is this migration's
# INPUT, the thing mode is derived from. Stripping it too would describe a
# database older than the one under test and leave nothing to derive from.
ADDED_BY_CHORE_MODES = (("chores", "mode"), ("chore_instances", "completed_by_member_id"))


def _without_column(schema: str, table: str, column: str) -> tuple[str, int]:
    """Drop one column's declaration line from one CREATE TABLE block."""
    head = f"CREATE TABLE IF NOT EXISTS {table} ("
    start = schema.index(head)
    end = schema.index(");", start)
    block = schema[start:end]
    stripped, count = re.subn(rf"^\s*{re.escape(column)}\s+[^\n]*\n", "", block, flags=re.M)
    return schema[:start] + stripped + schema[end:], count


def _schema_before_chore_modes() -> str:
    """
    The database as it stood before this work, derived from TODAY's
    schema.sql by removing exactly the columns db._MIGRATIONS adds to the
    two chores tables.

    This used to read `git show origin/main:app/schema.sql`, and that was
    self-invalidating: it asserted main had no `mode` column, which stopped
    being true the moment this branch merged — so the test could never pass
    again once it had done its job, and it failed on main from the merge
    onwards for a reason that had nothing to do with the migration. It also
    made a unit test depend on a fetched git remote.

    Deriving the "before" from the "after" keeps the upgrade path honestly
    tested and stays true however main moves. The assert inside is what
    stops it going vacuous: if a column is renamed or stops being a
    migration, the fixture stops being a real "before" and this fails
    loudly rather than testing a migration against a schema that already
    has its own result in it.
    """
    schema = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    for table, column in ADDED_BY_CHORE_MODES:
        assert (table, column) in [(t, c) for t, c, _ in _MIGRATIONS], (
            f"{table}.{column} is no longer a migration, so removing it no "
            f"longer describes a database this build would have to upgrade"
        )
        schema, count = _without_column(schema, table, column)
        assert count == 1, (
            f"{table}.{column} is in _MIGRATIONS but is not declared on its "
            f"own line in schema.sql — the pre-migration fixture can no "
            f"longer be derived, so this test would stop testing the upgrade"
        )
    return schema


def test_migration_against_a_database_made_before_modes_existed(tmp_path):
    """The real upgrade path: a file created before the mode column existed,
    opened by this build. Every shape a row could be in, including two that
    were never valid JSON."""
    import sqlite3

    schema = _schema_before_chore_modes()
    assert "mode TEXT" not in schema.split("CREATE TABLE IF NOT EXISTS chores (")[1].split(");")[0]
    conn = sqlite3.connect(tmp_path / "main.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    conn.execute("INSERT INTO members (household_id, name) VALUES (1, 'Emily'), (1, 'Vineeth')")
    rows = [
        ("one", 1, "[1]"), ("two", 1, "[1, 2]"), ("none", None, "[]"), ("default-only", 2, "[]"),
        ("not-json", None, "not json"), ("blank", None, ""), ("null-and-two", None, "[null, 2]"),
    ]
    conn.executemany(
        "INSERT INTO chores (household_id, name, default_assignee_id, rotation_member_ids_json) VALUES (1, ?, ?, ?)",
        rows,
    )
    conn.commit()

    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    got = {r["name"]: (r["mode"], json.loads(r["rotation_member_ids_json"]), r["default_assignee_id"])
           for r in conn.execute("SELECT name, mode, rotation_member_ids_json, default_assignee_id FROM chores")}
    assert "completed_by_member_id" in [r["name"] for r in conn.execute("PRAGMA table_info(chore_instances)")]
    conn.close()
    assert got == {
        "one": ("owned", [1], 1),
        "two": ("shared", [1, 2], 1),
        "none": ("whoever", [], None),
        "default-only": ("owned", [2], 2),
        "not-json": ("whoever", [], None),
        "blank": ("whoever", [], None),
        "null-and-two": ("owned", [2], 2),
    }


# --- 2. add_chore: owned is the default ----------------------------------

def test_add_chore_defaults_to_owned_by_the_only_adult():
    emily = _adult("Emily")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "child")
    result = tools.add_chore("Dishes")
    assert result["mode"] == "owned"
    assert result["owner"] == "Emily"
    assert _chore_row(result["chore_id"])["default_assignee_id"] == emily


def test_add_chore_with_an_owner_name_is_owned(two_adults):
    result = tools.add_chore("Bathrooms", owner_name="Vineeth")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"
    assert result["who_label"] == "Vineeth"


def test_add_chore_with_one_assignee_name_is_owned_by_them(two_adults):
    """The shape the old tool accepted still means what it meant."""
    result = tools.add_chore("Bathrooms", assignee_names=["Vineeth"])
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"


def test_add_chore_with_several_names_is_shared(two_adults):
    result = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"])
    assert result["mode"] == "shared"
    assert result["owner"] is None
    assert result["assignees"] == ["Emily", "Vineeth"]
    assert result["up_next"] == "Emily"


def test_add_chore_whoever_names_nobody(two_adults):
    result = tools.add_chore("Bins", mode="whoever")
    assert result["mode"] == "whoever"
    assert result["assignees"] == []
    assert result["who_label"] == "either of you"


def test_add_chore_with_two_adults_and_no_name_falls_back_to_shared(two_adults):
    """No owner can be inferred between two adults, so rather than guess
    one, the chore is shared across them — the assumption the card's
    'owned is the default' leaves open for this exact case."""
    result = tools.add_chore("Dishes")
    assert result["mode"] == "shared"
    assert result["assignees"] == ["Emily", "Vineeth"]


def test_add_chore_draws_on_the_rotation_named_in_setup(two_adults):
    tools.add_member("Grandma")
    tools.set_chores_profile(rotation_members=["Vineeth"])
    result = tools.add_chore("Lawn")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"


def test_add_chore_rejects_a_made_up_mode(two_adults):
    with pytest.raises(ValueError):
        tools.add_chore("Bins", mode="rotating")


# --- 2b. names resolve against the people already here — never invented ----

def test_an_owner_is_matched_exactly_case_aside(two_adults):
    assert tools.add_chore("Bathrooms", owner_name="vineeth")["owner"] == "Vineeth"


def test_an_owner_is_matched_by_first_name_when_that_is_unique():
    _adult("Emily Dalphy")
    _adult("Vineeth Kumar")
    result = tools.add_chore("Bathrooms", owner_name="Vineeth")
    assert result["owner"] == "Vineeth Kumar"
    assert [m["name"] for m in tools.list_members()] == ["Emily Dalphy", "Vineeth Kumar"], "no duplicate Vineeth"


def test_two_people_with_the_same_first_name_is_a_question():
    _adult("Sam Lee")
    _adult("Sam Roy")
    with pytest.raises(ValueError, match="more than one Sam"):
        tools.add_chore("Bathrooms", owner_name="Sam")
    assert tools.list_chore_definitions() == []


def test_an_unknown_owner_is_a_question_and_creates_nobody(two_adults):
    with pytest.raises(ValueError, match="don't know anyone called Vinneth"):
        tools.add_chore("Bathrooms", owner_name="Vinneth")
    chore_id = tools.add_chore("Bathrooms", owner_name="Emily")["chore_id"]
    with pytest.raises(ValueError, match="don't know anyone called Vinneth"):
        tools.update_chore(chore_id, owner_name="Vinneth")
    assert tools.list_chore_definitions()[0]["owner"] == "Emily"
    assert sorted(m["name"] for m in tools.list_members()) == ["Emily", "Vineeth"]


def test_the_legacy_assignee_names_go_through_the_same_resolver(two_adults):
    with pytest.raises(ValueError, match="don't know anyone called Nobody"):
        tools.add_chore("Vacuuming", assignee_names=["Emily", "Nobody"])
    tools.add_chore("Bins", mode="whoever")
    with pytest.raises(ValueError, match="don't know anyone called Nobody"):
        tools.schedule_chore_instance("Bins", datetime.date.today().isoformat(), assignee_name="Nobody")
    assert sorted(m["name"] for m in tools.list_members()) == ["Emily", "Vineeth"]


def test_a_setup_rotation_name_that_matches_nobody_is_skipped_not_invented(two_adults):
    tools.set_chores_profile(rotation_members=["Vineeth", "Cleaner Co"])
    result = tools.add_chore("Lawn")
    assert result["mode"] == "owned" and result["owner"] == "Vineeth"
    assert sorted(m["name"] for m in tools.list_members()) == ["Emily", "Vineeth"]


# --- 3. generate_chore_schedule assigns by mode ----------------------------

def test_schedule_owned_goes_to_the_owner_every_time(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Vineeth", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    assert len(rows) >= 4
    assert {r["assignee_id"] for r in rows} == {two_adults["Vineeth"]}


def test_schedule_shared_takes_turns(two_adults):
    chore_id = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    e, v = two_adults["Emily"], two_adults["Vineeth"]
    assert [r["assignee_id"] for r in rows][:4] == [e, v, e, v]


def test_schedule_shared_continues_the_turn_order_when_topped_up(two_adults):
    chore_id = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=7)
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    e, v = two_adults["Emily"], two_adults["Vineeth"]
    assert [r["assignee_id"] for r in rows][:4] == [e, v, e, v]


def test_schedule_whoever_has_no_assignee(two_adults):
    chore_id = tools.add_chore("Bins", mode="whoever", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    rows = _instances(chore_id)
    assert rows
    assert {r["assignee_id"] for r in rows} == {None}


def test_a_one_off_instance_follows_the_mode_too(two_adults):
    tools.add_chore("Bathrooms", owner_name="Vineeth")
    tools.add_chore("Bins", mode="whoever")
    tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"])
    day = datetime.date.today().isoformat()
    owned = tools.schedule_chore_instance("Bathrooms", day)["instance_id"]
    nobody = tools.schedule_chore_instance("Bins", day)["instance_id"]
    first = tools.schedule_chore_instance("Vacuuming", day)["instance_id"]
    second = tools.schedule_chore_instance("Vacuuming", day)["instance_id"]
    conn = get_conn()
    by_id = {r["id"]: r["assignee_id"] for r in conn.execute("SELECT id, assignee_id FROM chore_instances")}
    conn.close()
    assert by_id[owned] == two_adults["Vineeth"]
    assert by_id[nobody] is None
    assert [by_id[first], by_id[second]] == [two_adults["Emily"], two_adults["Vineeth"]]


# --- 4. update_chore changes the owner by saying so ------------------------

def test_give_the_bathrooms_to_vineeth(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Emily")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    result = tools.update_chore(chore_id, owner_name="Vineeth")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"
    assert result["upcoming_moved"] >= 3
    assert {r["assignee_id"] for r in _instances(chore_id)} == {two_adults["Vineeth"]}


def test_lets_take_turns_on_the_vacuuming(two_adults):
    chore_id = tools.add_chore("Vacuuming", owner_name="Emily")["chore_id"]
    tools.generate_chore_schedule(days_ahead=28)
    result = tools.update_chore(chore_id, mode="shared")
    assert result["mode"] == "shared"
    assert result["assignees"] == ["Emily", "Vineeth"]
    e, v = two_adults["Emily"], two_adults["Vineeth"]
    assert [r["assignee_id"] for r in _instances(chore_id)][:4] == [e, v, e, v]


def test_either_of_us_can_do_the_bins(two_adults):
    chore_id = tools.add_chore("Bins", owner_name="Emily")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    result = tools.update_chore(chore_id, mode="whoever")
    assert result["mode"] == "whoever"
    assert result["who_label"] == "either of you"
    assert {r["assignee_id"] for r in _instances(chore_id)} == {None}
    assert _chore_row(chore_id)["default_assignee_id"] is None


def test_make_it_owned_without_a_name_asks_rather_than_guesses(two_adults):
    chore_id = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"])["chore_id"]
    with pytest.raises(ValueError):
        tools.update_chore(chore_id, mode="owned")
    assert _chore_row(chore_id)["mode"] == "shared", "a refused change changes nothing"


def test_make_it_owned_keeps_the_one_person_already_on_it(two_adults):
    chore_id = _legacy_chore("Bathrooms", [two_adults["Vineeth"]])
    result = tools.update_chore(chore_id, mode="owned")
    assert result["owner"] == "Vineeth"


def test_the_old_assignee_names_shape_still_sets_the_mode(two_adults):
    chore_id = tools.add_chore("Bins", mode="whoever")["chore_id"]
    assert tools.update_chore(chore_id, assignee_names=["Emily", "Vineeth"])["mode"] == "shared"
    assert tools.update_chore(chore_id, assignee_names=["Emily"])["mode"] == "owned"
    assert tools.update_chore(chore_id, assignee_names=[])["mode"] == "whoever"


def test_a_refused_owner_change_writes_nothing_and_lets_the_connection_go(two_adults):
    """Found in verification: frequency was written before the owner
    question was asked, and the open connection then locked the database
    for the next write."""
    chore_id = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")["chore_id"]
    with pytest.raises(ValueError, match="Whose should it be"):
        tools.update_chore(chore_id, frequency="daily", mode="owned")
    row = _chore_row(chore_id)
    assert row["frequency"] == "weekly" and row["mode"] == "shared"
    # The next write must not hit "database is locked".
    assert tools.update_chore(chore_id, frequency="daily")["mode"] == "shared"
    assert _chore_row(chore_id)["frequency"] == "daily"


def test_changing_frequency_alone_leaves_the_owner_alone(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Vineeth")["chore_id"]
    result = tools.update_chore(chore_id, frequency="biweekly")
    assert result["mode"] == "owned"
    assert result["owner"] == "Vineeth"
    assert "upcoming_moved" not in result


# --- 5. history is never rewritten -----------------------------------------

def test_done_instances_keep_the_person_who_did_them(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Emily", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    rows = _instances(chore_id)
    done_id = rows[0]["id"]
    with tools.use_member(two_adults["Emily"]):
        tools.complete_chore(done_id)

    tools.update_chore(chore_id, owner_name="Vineeth")

    after = {r["id"]: r for r in _instances(chore_id)}
    assert after[done_id]["status"] == "done"
    assert after[done_id]["assignee_id"] == two_adults["Emily"]
    assert after[done_id]["completed_by_member_id"] == two_adults["Emily"]
    pending = [r for r in after.values() if r["status"] == "pending"]
    assert pending and {r["assignee_id"] for r in pending} == {two_adults["Vineeth"]}


def test_an_owner_change_touches_no_other_chore(two_adults):
    bath = tools.add_chore("Bathrooms", owner_name="Emily", frequency="weekly")["chore_id"]
    vac = tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    before = _instances(vac)
    tools.update_chore(bath, owner_name="Vineeth")
    assert _instances(vac) == before


def test_owned_stays_with_the_owner_over_several_generations(two_adults):
    chore_id = tools.add_chore("Bathrooms", owner_name="Vineeth", frequency="weekly")["chore_id"]
    for days in (7, 21, 35, 35):
        tools.generate_chore_schedule(days_ahead=days)
    rows = _instances(chore_id)
    assert len(rows) == 6
    assert {r["assignee_id"] for r in rows} == {two_adults["Vineeth"]}
    assert len({r["due_date"] for r in rows}) == 6, "no date generated twice"


def test_going_shared_continues_after_whoever_actually_did_the_last_one(two_adults):
    """The doer, not the person it was scheduled for, is the fairness fact:
    Emily's chore that Vineeth did means Emily is up next."""
    chore_id = tools.add_chore("Vacuuming", owner_name="Emily", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    first = _instances(chore_id)[0]["id"]
    tools.complete_chore(first, done_by="Vineeth")
    tools.update_chore(chore_id, mode="shared", assignee_names=["Emily", "Vineeth"])
    pending = [r["assignee_id"] for r in _instances(chore_id) if r["status"] == "pending"]
    assert pending[0] == two_adults["Emily"]


def test_going_shared_picks_up_the_turn_after_the_last_done(two_adults):
    """Handing a shared chore to the person who just did it would be the
    unfairness the card exists to stop."""
    chore_id = tools.add_chore("Vacuuming", owner_name="Emily", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    first = _instances(chore_id)[0]["id"]
    tools.complete_chore(first)
    tools.update_chore(chore_id, mode="shared", assignee_names=["Emily", "Vineeth"])
    pending = [r["assignee_id"] for r in _instances(chore_id) if r["status"] == "pending"]
    assert pending[0] == two_adults["Vineeth"]


# --- 6. both are recorded: whose it was, and who did it ---------------------

def test_a_tick_is_credited_to_the_session_adult(two_adults):
    chore_id = tools.add_chore("Bins", mode="whoever")["chore_id"]
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    with tools.use_member(two_adults["Vineeth"]):
        tools.complete_chore(instance_id)
    row = _instances(chore_id)[0]
    assert row["assignee_id"] is None, "whose it was: nobody's"
    assert row["completed_by_member_id"] == two_adults["Vineeth"], "who did it: Vineeth"
    assert tools.list_chores(status="done")[0]["completed_by"] == "Vineeth"


def test_vineeth_did_the_bins_credits_vineeth(two_adults):
    tools.add_chore("Bins", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    with tools.use_member(two_adults["Emily"]):
        tools.complete_chore(instance_id, done_by="Vineeth")
    conn = get_conn()
    row = conn.execute("SELECT assignee_id, completed_by_member_id FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["assignee_id"] == two_adults["Emily"]
    assert row["completed_by_member_id"] == two_adults["Vineeth"]


def test_a_name_that_is_nobody_in_the_house_is_a_question_not_the_signed_in_adult(two_adults):
    """Found in verification: "Vinneth did the bins" with Emily signed in
    went down as Emily. Now it's a question back, and nothing is recorded."""
    tools.add_chore("Bins", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    with tools.use_member(two_adults["Emily"]):
        with pytest.raises(ValueError, match="don't know anyone called Vinneth"):
            tools.complete_chore(instance_id, done_by="Vinneth")
    conn = get_conn()
    row = conn.execute("SELECT status, completed_by_member_id FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["status"] == "pending" and row["completed_by_member_id"] is None
    assert all(m["name"] != "Vinneth" for m in tools.list_members())
    # ...and the connection was let go: the next write goes through.
    with tools.use_member(two_adults["Emily"]):
        tools.complete_chore(instance_id, done_by="Vineeth")
    assert tools.list_chores(status="done")[0]["completed_by"] == "Vineeth"


def test_a_first_name_is_enough_to_credit_a_tick(two_adults):
    tools.add_member("Grandma Jo")
    tools.add_chore("Bins", owner_name="Emily")
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    tools.complete_chore(instance_id, done_by="grandma")
    assert tools.list_chores(status="done")[0]["completed_by"] == "Grandma Jo"


def test_the_now_card_tick_records_the_picked_adult_and_unticking_clears_it(client, two_adults):
    tools.add_chore("Bins", mode="whoever")
    instance_id = tools.schedule_chore_instance("Bins", datetime.date.today().isoformat())["instance_id"]
    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    client.post("/api/whoami/pick", json={"member_id": two_adults["Vineeth"]})

    assert client.post(f"/api/chores/{instance_id}/status", json={"status": "done"}).status_code == 200
    conn = get_conn()
    row = conn.execute("SELECT completed_by_member_id FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["completed_by_member_id"] == two_adults["Vineeth"]

    assert client.post(f"/api/chores/{instance_id}/status", json={"status": "pending"}).status_code == 200
    conn = get_conn()
    row = conn.execute("SELECT completed_by_member_id, completed_at FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()
    conn.close()
    assert row["completed_by_member_id"] is None and row["completed_at"] is None


# --- 7. what the screens will need is on the way out -----------------------

def test_definitions_say_whose_and_whose_turn(two_adults):
    tools.add_chore("Bathrooms", owner_name="Vineeth")
    tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")
    tools.add_chore("Bins", mode="whoever")
    tools.generate_chore_schedule(days_ahead=7)
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["who_label"] == "Vineeth"
    assert by_name["Vacuuming"]["mode"] == "shared"
    assert by_name["Vacuuming"]["up_next"] == "Emily"
    assert by_name["Vacuuming"]["who_label"] == "Emily"
    assert by_name["Bins"]["who_label"] == "either of you"


def test_whose_turn_moves_on_once_this_one_is_done(two_adults):
    tools.add_chore("Vacuuming", assignee_names=["Emily", "Vineeth"], frequency="weekly")
    tools.generate_chore_schedule(days_ahead=14)
    first = tools.list_chores()[0]["id"]
    tools.complete_chore(first)
    assert tools.list_chore_definitions()[0]["up_next"] == "Vineeth"


def test_instances_carry_a_first_name_or_either_of_you(two_adults):
    tools.add_member("Grandma Jo")
    tools.add_chore("Bathrooms", owner_name="Grandma Jo")
    tools.add_chore("Bins", mode="whoever")
    day = datetime.date.today().isoformat()
    tools.schedule_chore_instance("Bathrooms", day)
    tools.schedule_chore_instance("Bins", day)
    by_name = {r["chore"]: r for r in tools.get_chores_due_today()}
    assert by_name["Bathrooms"]["who_label"] == "Grandma"
    assert by_name["Bathrooms"]["mode"] == "owned"
    assert by_name["Bins"]["who_label"] == "either of you"
    assert by_name["Bins"]["mode"] == "whoever"


def test_either_of_you_is_anyone_when_there_are_not_two_adults():
    _adult("Emily")
    tools.add_chore("Bins", mode="whoever")
    assert tools.list_chore_definitions()[0]["who_label"] == "anyone"


def test_the_today_route_serves_the_label(signed_in, two_adults):
    tools.add_chore("Bathrooms", owner_name="Vineeth")
    tools.schedule_chore_instance("Bathrooms", datetime.date.today().isoformat())
    body = signed_in.get("/api/chores/today").json()
    assert body["chores"][0]["who_label"] == "Vineeth"
    assert body["chores"][0]["completed_by"] is None


def test_the_hidden_now_card_prints_the_owner_behind_its_flag():
    assert "SHOW_CHORES_ON_TODAY = false" in SHELL_JS
    assert "c.who_label" in SHELL_JS
    assert 'class="chore-who"' in SHELL_JS
    assert ".chore-who" in SHELL_CSS


# --- 8. the starter list proposes an owner for every row --------------------

def test_normaliser_fills_in_an_owner_for_every_row():
    rows = agent._normalize_chore_recommendations(
        [
            {"name": "Bathrooms", "category": "cleaning", "frequency": "weekly"},
            {"name": "Kitchen", "category": "cleaning", "frequency": "daily"},
            {"name": "Vacuuming", "category": "cleaning", "frequency": "weekly", "mode": "shared"},
            {"name": "Bins", "category": "cleaning", "frequency": "weekly", "mode": "whoever"},
            {"name": "Litter", "category": "cleaning", "frequency": "daily", "assignee_names": ["vineeth"]},
            "not a row",
        ],
        ["Emily", "Vineeth"],
    )
    by_name = {r["name"]: r for r in rows}
    assert len(rows) == 5
    # Owned by default, dealt round the rotation rather than piled on one person.
    assert by_name["Bathrooms"]["mode"] == "owned" and by_name["Bathrooms"]["owner_name"] == "Emily"
    assert by_name["Kitchen"]["mode"] == "owned" and by_name["Kitchen"]["owner_name"] == "Vineeth"
    assert by_name["Vacuuming"]["assignee_names"] == ["Emily", "Vineeth"]
    assert by_name["Bins"] == {**by_name["Bins"], "mode": "whoever", "owner_name": "", "assignee_names": []}
    assert by_name["Litter"]["mode"] == "owned" and by_name["Litter"]["owner_name"] == "Vineeth"


def test_normaliser_drops_a_name_that_is_not_in_the_rotation():
    """Found in verification: an owner_name of "Nobody" survived. A name
    the household didn't give falls back to the dealt-round rule."""
    rows = agent._normalize_chore_recommendations(
        [
            {"name": "Bathrooms", "category": "cleaning", "frequency": "weekly", "mode": "owned", "owner_name": "Nobody"},
            {"name": "Vacuuming", "category": "cleaning", "frequency": "weekly", "mode": "shared", "assignee_names": ["Emily", "Cleaner"]},
            {"name": "Kitchen", "category": "cleaning", "frequency": "daily", "mode": "owned", "owner_name": "vineeth"},
        ],
        ["Emily", "Vineeth Kumar"],
    )
    by_name = {r["name"]: r for r in rows}
    assert by_name["Bathrooms"]["owner_name"] == "Emily"
    assert by_name["Vacuuming"]["assignee_names"] == ["Emily", "Vineeth Kumar"]
    assert by_name["Kitchen"]["owner_name"] == "Vineeth Kumar", "a first name still finds the person"


def test_normaliser_with_nobody_named_proposes_whoever():
    rows = agent._normalize_chore_recommendations(
        [{"name": "Bathrooms", "category": "cleaning", "frequency": "weekly", "mode": "owned"}], []
    )
    assert rows[0]["mode"] == "whoever"


def _stub_recommendation(monkeypatch, chores):
    block = types.SimpleNamespace(type="tool_use", name="submit_chore_recommendations", input={"chores": chores}, id="tu_1")
    response = types.SimpleNamespace(content=[block])
    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", lambda client, **kwargs: response)


def test_the_recommend_route_returns_mode_and_owner_per_row(signed_in, two_adults, monkeypatch):
    _stub_recommendation(monkeypatch, [
        {"name": "Bathrooms", "category": "cleaning", "frequency": "weekly", "mode": "owned", "owner_name": "Vineeth"},
        {"name": "Kitchen", "category": "cleaning", "frequency": "daily"},
    ])
    res = signed_in.post("/api/onboarding/chores/recommend", json={"rotation_members": ["Emily", "Vineeth"]})
    assert res.status_code == 200
    rows = res.json()["chores"]
    assert rows[0]["mode"] == "owned" and rows[0]["owner_name"] == "Vineeth"
    assert rows[1]["mode"] == "owned" and rows[1]["owner_name"] in ("Emily", "Vineeth")


def test_the_recommend_route_draws_on_the_adults_when_setup_named_nobody(signed_in, two_adults, monkeypatch):
    seen = {}

    def fake(profile):
        seen.update(profile)
        return []

    from app import main
    monkeypatch.setattr(main, "generate_chore_recommendations", fake)
    signed_in.post("/api/onboarding/chores/recommend", json={"rotation_members": []})
    assert seen["rotation_members"] == ["Emily", "Vineeth"]


def test_the_recommend_prompt_asks_for_an_owner_by_default():
    # Four values since Loop Board "Chores v1: Tag a chore as outsourced" —
    # outsourced is a fourth mode beside the three ways a chore can belong
    # to someone in the house, not a flag on top of them. Asserted in full
    # rather than as a prefix, so adding a fifth is a decision somebody
    # makes here rather than something a slice quietly widens.
    props = agent._RECOMMEND_CHORES_TOOL["input_schema"]["properties"]["chores"]["items"]
    assert props["properties"]["mode"]["enum"] == ["owned", "shared", "whoever", "outsourced"]
    assert "owner_name" in props["properties"]
    assert "mode" in props["required"]


def test_save_accepts_per_row_owner_overrides(signed_in, two_adults):
    res = signed_in.post("/api/onboarding/chores/save", json={"chores": [
        {"name": "Bathrooms", "mode": "owned", "owner_name": "Vineeth"},
        {"name": "Vacuuming", "mode": "shared", "assignee_names": ["Emily", "Vineeth"]},
        {"name": "Bins", "mode": "whoever"},
        {"name": "Kitchen", "assignee_names": ["Emily"]},
    ]})
    assert res.status_code == 200
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["owner"] == "Vineeth"
    assert by_name["Vacuuming"]["mode"] == "shared"
    assert by_name["Bins"]["mode"] == "whoever"
    assert by_name["Kitchen"]["mode"] == "owned" and by_name["Kitchen"]["owner"] == "Emily"
    # ...and the schedule that followed respects each.
    today = {r["chore"]: r for r in tools.get_chores_due_today()}
    assert today["Bathrooms"]["assignee"] == "Vineeth"
    assert today["Bins"]["assignee"] is None


def test_save_skips_a_row_naming_nobody_and_keeps_the_rest(signed_in, two_adults):
    # A stray name (a typo, or one the wizard never offered) used to invent
    # a member on main, and would 500 with a half-save once ownership stopped
    # doing that. Now the row is skipped and reported; its neighbours save.
    res = signed_in.post("/api/onboarding/chores/save", json={"chores": [
        {"name": "Trash", "mode": "owned", "owner_name": "Emily"},
        {"name": "Bathrooms", "mode": "owned", "owner_name": "Nobody"},
        {"name": "Vacuuming", "mode": "shared", "assignee_names": ["Emily", "Vineeth"]},
    ]})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 2
    assert [s["name"] for s in body["skipped"]] == ["Bathrooms"]
    assert "Nobody" in body["skipped"][0]["reason"]
    names = {d["name"] for d in tools.list_chore_definitions()}
    assert names == {"Trash", "Vacuuming"}
    assert {m["name"] for m in tools.list_members()} == {"Emily", "Vineeth"}


# --- 9. the chat tools know the words ----------------------------------------

def test_the_chat_tools_carry_mode_and_owner():
    by_name = {t["name"]: t for t in agent.TOOL_DEFINITIONS}
    add = by_name["add_chore"]["input_schema"]["properties"]
    upd = by_name["update_chore"]["input_schema"]["properties"]
    # Four values — see the note on the recommend tool above.
    assert add["mode"]["enum"] == ["owned", "shared", "whoever", "outsourced"]
    assert "owner_name" in add and "owner_name" in upd
    assert upd["mode"]["enum"] == ["owned", "shared", "whoever", "outsourced"]
    # The phrases the household will actually say are in the description,
    # so the model maps them without guessing.
    desc = by_name["update_chore"]["description"].lower()
    assert "take turns" in desc and "either of us" in desc and "give the" in desc
    assert "done_by" in by_name["complete_chore"]["input_schema"]["properties"]


# --- 10. households stay separate on every new path -------------------------

def test_the_new_paths_never_cross_households(two_adults):
    from app import households

    beta = households.create_household("The Beta Testers", "beta-passphrase-long-enough")
    bath = tools.add_chore("Bathrooms", owner_name="Emily", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=14)
    with tools.use_household(beta):
        # Household 1's people are nobody here.
        with pytest.raises(ValueError, match="don't know anyone called Emily"):
            tools.add_chore("Bins", owner_name="Emily")
        tools.add_member("Priya")
        tools.set_member_age_group("Priya", "Adult")
        beta_chore = tools.add_chore("Bins")
        assert beta_chore["owner"] == "Priya", "the only adult HERE, not in household 1"
        tools.generate_chore_schedule(days_ahead=14)
        beta_id = tools.list_chores()[0]["id"]
        with pytest.raises(ValueError, match="don't know anyone called Vineeth"):
            tools.complete_chore(beta_id, done_by="Vineeth")
        assert [d["name"] for d in tools.list_chore_definitions()] == ["Bins"]
        assert tools.add_chore("Sweep", mode="whoever")["who_label"] == "anyone", "one adult here, not two"
        beta_before = _instances(beta_chore["chore_id"])

    # An owner change in household 1 leaves the other household's schedule as it was.
    tools.update_chore(bath, owner_name="Vineeth")
    with tools.use_household(beta):
        assert _instances(beta_chore["chore_id"]) == beta_before
        assert {r["assignee_id"] for r in _instances(beta_chore["chore_id"])} != {two_adults["Vineeth"]}
