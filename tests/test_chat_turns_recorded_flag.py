"""
The morning report stopped reading a column default as a measurement.

`chat_turns.tools_called_json` arrived by ALTER TABLE with a NOT NULL
DEFAULT, and SQLite materialises such a default into every row that was
already there. So a chat turn from before that column landed -- one that
may well have swapped three meals -- reads back '[]', exactly like a turn
that genuinely called nothing. `_chat_tool_counts` counted those as
talk-only, so the 2026-09-26 report told Emily "16 of 16 turns called
nothing -- just talk" about a month whose 43 model rounds say otherwise,
and a Loop Board card went looking for 27 rounds that were never missing.

`tools_recorded` is the answered-flag that tells the two apart, the same
shape and the same reason as meal_preferences.snacks_per_week_set. It is
deliberately never backfilled: the only honest value for a row written
before the recording existed is "we do not know".

Red against main: the two counting tests and the two report tests. The
rest are guards -- each says in its own docstring what pins it.
"""
import json
import sqlite3
import subprocess
import sys
import types

import observability_report as report
from app import agent, tools
from app.db import get_conn
from app.tools import usage as usage_tools
from tests.test_agent_turn_recording import _Usage, _text_block, _tool_block, _stub_client


def _seed(rounds=1, tools_called_json="[]", tools_recorded=1, household_id=1):
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_turns (household_id, rounds, input_tokens, cache_read_tokens, "
        "cache_write_tokens, output_tokens, seconds, tools_called_json, tools_recorded) "
        "VALUES (?, ?, 0, 0, 0, 0, 0.0, ?, ?)",
        (household_id, rounds, tools_called_json, tools_recorded),
    )
    conn.commit()
    conn.close()


def _counts():
    conn = get_conn()
    try:
        return usage_tools._chat_tool_counts(conn, 1, "-7 days")
    finally:
        conn.close()


# --------------------------------------------------------------------------
# CATCH -- a row from before the recording is an unknown, not a talk-only turn.
# --------------------------------------------------------------------------

def test_a_turn_from_before_the_recording_is_not_counted_as_talk_only(client):
    """
    THE BUG. On main this row lands in talk_only_turns and the report
    says the household asked and got only conversation back, about a turn
    nobody ever looked at.
    """
    _seed(rounds=4, tools_called_json="[]", tools_recorded=0)

    counts = _counts()
    assert counts["talk_only_turns"] == 0
    assert counts["unrecorded_turns"] == 1


def test_a_recorded_turn_that_called_nothing_is_still_talk_only(client):
    """
    The other half, and the one that keeps the fix from being a blunt
    "stop counting talk-only": a turn recorded by today's code with an
    empty list really did call nothing, and that is worth counting.
    """
    _seed(rounds=1, tools_called_json="[]", tools_recorded=1)

    counts = _counts()
    assert counts["talk_only_turns"] == 1
    assert counts["unrecorded_turns"] == 0


def test_an_unrecorded_turns_tools_are_not_added_to_the_counts(client):
    """
    A pre-column row cannot carry real names today, but a future writer
    that forgets the flag must not have its names silently folded into
    "what chat was for" as if they had been measured.
    """
    _seed(tools_called_json=json.dumps(["swap_meal_in_plan"]), tools_recorded=0)

    counts = _counts()
    assert counts["counts"] == {}
    assert counts["turns_with_a_tap"] == 0
    assert counts["unrecorded_turns"] == 1


# --------------------------------------------------------------------------
# The write side.
# --------------------------------------------------------------------------

def test_a_real_turn_records_that_its_tool_list_is_a_measurement(signed_in, monkeypatch):
    """
    GUARD, pinned by the mutation that drops the column from the INSERT:
    without it every new row defaults to 0 and the report goes permanently
    quiet about what chat is for.
    """
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_text_block("Evening.")],
            stop_reason="end_turn",
            usage=_Usage(output_tokens=42),
        ),
    ])

    assert signed_in.post("/api/chat", json={"session_id": "default", "message": "hi"}).status_code == 200

    conn = get_conn()
    row = conn.execute("SELECT tools_recorded, tools_called_json FROM chat_turns").fetchone()
    conn.close()
    assert row["tools_recorded"] == 1
    assert row["tools_called_json"] == "[]"


def test_a_turn_that_called_a_tool_is_recorded_and_counted(signed_in, monkeypatch):
    """GUARD: the flag must not change what the names themselves say."""
    _stub_client(monkeypatch, [
        types.SimpleNamespace(
            content=[_tool_block("get_grocery_list", {})],
            stop_reason="tool_use",
            usage=_Usage(output_tokens=10),
        ),
        types.SimpleNamespace(
            content=[_text_block("Here it is.")],
            stop_reason="end_turn",
            usage=_Usage(output_tokens=10),
        ),
    ])

    assert signed_in.post("/api/chat", json={"session_id": "default", "message": "list?"}).status_code == 200

    counts = _counts()
    assert counts["unrecorded_turns"] == 0
    assert counts["talk_only_turns"] == 0
    assert counts["counts"].get("get_grocery_list") == 1


# --------------------------------------------------------------------------
# Never backfilled.
# --------------------------------------------------------------------------

def test_the_migration_leaves_an_existing_row_saying_we_do_not_know(tmp_path):
    """
    The reproduction of the original defect, one level down: a row that
    predates the column gets the DEFAULT materialised into it, which is
    why a default cannot be read as an answer. Driven over the app's own
    _MIGRATIONS rather than described.
    """
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE chat_turns ("
        "  id INTEGER PRIMARY KEY, household_id INTEGER, rounds INTEGER,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now')));"
    )
    conn.execute("INSERT INTO chat_turns (household_id, rounds) VALUES (1, 4)")
    conn.commit()

    from app import db as db_module
    for table, column, decl in db_module._MIGRATIONS:
        if table != "chat_turns":
            continue
        conn.execute(f"ALTER TABLE chat_turns ADD COLUMN {column} {decl}")
    conn.commit()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM chat_turns").fetchone()
    conn.close()

    # The whole point: '[]' here is the default, not an observation, and
    # the flag beside it is what says so.
    assert row["tools_called_json"] == "[]"
    assert row["tools_recorded"] == 0


def test_nothing_backfills_the_flag(client):
    """
    GUARD, pinned by the mutation that adds a well-meaning
    "UPDATE chat_turns SET tools_recorded = 1": a row from before the
    recording would then claim to have been measured, which is the
    invented history this column exists to prevent.
    """
    _seed(rounds=4, tools_recorded=0)

    from app.db import init_db
    init_db()

    conn = get_conn()
    still_unknown = conn.execute(
        "SELECT COUNT(*) FROM chat_turns WHERE tools_recorded = 0"
    ).fetchone()[0]
    conn.close()
    assert still_unknown == 1


# --------------------------------------------------------------------------
# The rounds spread -- the instrument the card asked for.
# --------------------------------------------------------------------------

def test_the_summary_says_how_the_rounds_were_spread(client):
    """
    CATCH. "43 rounds over 16 turns" is 2.7 each and reads like every turn
    looping; one runaway turn beside fifteen ordinary ones is a different
    fact and wants a different response. The average cannot tell them
    apart, so the report stops being handed only the average.
    """
    _seed(rounds=1)
    _seed(rounds=1)
    _seed(rounds=28)

    summary = tools.get_usage_summary(days=7)
    assert summary["chat_rounds"] == 30
    assert summary["chat_round_spread"] == {"1": 2, "28": 1}


def test_the_report_names_the_turns_that_went_round_again(capsys):
    """CATCH: red on main, which has no such line to print."""
    report._print_chat_rounds({"1": 15, "28": 1}, 43)
    out = capsys.readouterr().out
    assert "43 over 16 turns" in out
    assert "1 took 28" in out


def test_the_report_says_nothing_when_every_turn_took_one_round(capsys):
    """
    GUARD, pinned by the mutation that prints unconditionally: the
    ordinary case is one round a turn, and a line that appears every
    morning saying nothing happened is a line nobody reads.
    """
    report._print_chat_rounds({"1": 16}, 16)
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# What the report says.
# --------------------------------------------------------------------------

def test_the_report_says_which_turns_it_could_not_answer_for(capsys):
    """CATCH: red on main, which prints all 16 as "called nothing"."""
    report._print_chat_tools(
        {"counts": {}, "talk_only_turns": 0, "turns_with_a_tap": 0,
         "unreadable_turns": 0, "unrecorded_turns": 16},
        16,
    )
    out = capsys.readouterr().out
    assert "16 of 16 turns are from before this was recorded" in out
    assert "called nothing" not in out


def test_an_older_deployment_still_reports(capsys):
    """
    GUARD. The report reads a REMOTE app, so a deployment that predates
    this work answers without the new keys. A morning report that crashes
    tells Emily less than one that omits a line.
    """
    report._print_chat_tools(
        {"counts": {}, "talk_only_turns": 3, "turns_with_a_tap": 0, "unreadable_turns": 0},
        3,
    )
    report._print_chat_rounds(None, 0)
    out = capsys.readouterr().out
    assert "3 of 3 turns called nothing" in out
    assert "from before this was recorded" not in out


def test_the_counts_are_household_scoped(client):
    """
    GUARD, pinned by dropping household_id from the new spread query:
    every other number in this report is the household's own.
    """
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (2, 'The Testers')")
    conn.commit()
    conn.close()

    _seed(rounds=9, household_id=2)
    _seed(rounds=1, household_id=1)

    summary = tools.get_usage_summary(days=7)
    assert summary["chat_round_spread"] == {"1": 1}
    assert summary["chat_rounds"] == 1
