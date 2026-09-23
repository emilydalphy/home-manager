"""
Was the "Maybe already home" check RIGHT? The counts behind that question.

Emily, 2026-09-22: "We should also track the number of times people say
they actually want to keep it on vs they do have it (if it's easy to track
in the report) so that we can measure the accuracy."

WHAT WAS MISSING. The DROP was already countable — drop_grocery_item_pre_
shop writes status='removed', removed_by and removed_at. The KEEP was not:
"Buy it anyway" (mark_grocery_item_already_have_reviewed) and "Keep all N"
(keep_all_pre_shop_flags) each set already_have_reviewed = 1 and nothing
else. No timestamp, so keeps could not be counted over a window at all;
and no record that a flag had ever been RAISED, so a card nobody answered
was indistinguishable from one that was never shown. A rate with no
denominator is not a rate — and this check takes lines off the list the
household actually shops from, so being wrong costs them an ingredient at
dinner.

WHAT "FLAGGED" COUNTS, since the whole number hangs off it: grocery lines
the card held off the list, counted the FIRST time each was held.
get_pre_shop_flags is computed on read and runs on every Shop-tab load and
every /api/grocery-list read, so a per-read count would be many times the
truth — test_re_reading_the_card_does_not_inflate_flagged is the one that
pins that, and it is the test to keep if any of these are ever trimmed.

Every test here is a CATCH: none of this exists on main.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app import tools
from app.db import _run_migrations, get_conn
from app.tools import defrost as defrost_module

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- helpers


def _need(item: str, qty: str, category: str = "produce") -> int:
    return tools.add_grocery_item(item, quantity=qty, category=category)["item_id"]


def _have(item: str, qty: str, category: str = "produce") -> None:
    tools.update_inventory(item, "add", quantity=qty, category=category)


def _flagged_line(item: str, wanted: str = "1 lb", on_hand: str = "4 lbs") -> int:
    """One grocery line the pre-shop card will raise, already raised."""
    item_id = _need(item, wanted)
    _have(item, on_hand)
    names = {f["name"] for f in tools.get_pre_shop_flags()}
    assert item in names, f"{item} should be behind the card for this test to mean anything"
    return item_id


def _ledger(item_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM pre_shop_decisions WHERE household_id = 1 AND grocery_item_id = ?",
            (item_id,),
        ).fetchone()
    finally:
        conn.close()


def _backdate(item_id: int, modifier: str, *, flagged: bool = True, decided: bool = True) -> None:
    """Move one ledger row's stamps into the past, in SQLite's own arithmetic."""
    sets = []
    if flagged:
        sets.append(f"flagged_at = datetime('now', '{modifier}')")
    if decided:
        sets.append(f"decided_at = datetime('now', '{modifier}')")
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE pre_shop_decisions SET {', '.join(sets)} "
            "WHERE household_id = 1 AND grocery_item_id = ?",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _counts(days: int = 7) -> dict:
    return tools.get_usage_summary(days=days)["pre_shop"]


# ------------------------------------------------- every decision is recorded


def test_raising_the_card_records_the_flag():
    """The denominator. Nothing recorded a raise at all before this."""
    item_id = _flagged_line("Butter")

    row = _ledger(item_id)
    assert row is not None, "a raised flag should be on the ledger"
    assert row["decision"] == "", "raised is not yet answered"
    assert row["decided_at"] is None
    assert row["flagged_at"], "a raise without a timestamp cannot be counted over a window"


def test_re_reading_the_card_does_not_inflate_flagged():
    """
    THE ONE THAT MATTERS. get_pre_shop_flags is computed on read and the
    Shop tab reads it on every load; counting a raise per read would make
    the accuracy rate look many times better than it is.
    """
    _flagged_line("Butter")
    for _ in range(5):
        tools.get_pre_shop_flags()

    assert _counts()["flagged"] == 1


def test_buy_it_anyway_records_a_keep():
    """"Keep" was the half that could not be counted at all."""
    item_id = _flagged_line("Butter")
    tools.mark_grocery_item_already_have_reviewed(item_id)

    row = _ledger(item_id)
    assert row["decision"] == "kept"
    assert row["decided_at"], "a keep with no timestamp cannot be counted over a window"


def test_keep_all_records_every_flag_as_kept_all():
    """
    One tap dismissing several cards is recorded as 'kept_all', not as
    several people disagreeing with several flags. The report adds the two
    together; the column keeps the difference.
    """
    first = _flagged_line("Butter")
    second = _flagged_line("Rice", wanted="1 cup", on_hand="6 cups")

    tools.keep_all_pre_shop_flags()

    assert _ledger(first)["decision"] == "kept_all"
    assert _ledger(second)["decision"] == "kept_all"
    assert _counts()["kept"] == 2, "the report adds keep and keep-all together"


def test_drop_it_records_a_drop():
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")

    row = _ledger(item_id)
    assert row["decision"] == "dropped"
    assert row["decided_at"]


def test_dropping_twice_is_still_one_decision():
    """The drop is idempotent, and a second tap is not a second decision."""
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")
    first_at = _ledger(item_id)["decided_at"]
    tools.drop_grocery_item_pre_shop(item_id, author="user")

    assert _ledger(item_id)["decided_at"] == first_at
    assert _counts()["dropped"] == 1


def test_an_undo_is_recorded_as_put_back_and_not_also_as_dropped():
    """
    A drop the household took back is a WRONG flag, and the one kind of
    wrong this app can observe. Its own bucket, so the three answers
    partition the flags and nothing has to be subtracted from anything.
    """
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")
    tools.undo_pre_shop_drop(item_id)

    assert _ledger(item_id)["decision"] == "undone"
    counts = _counts()
    assert counts["undone"] == 1
    assert counts["dropped"] == 0, "a line put back is not still a drop"
    assert counts["flagged"] == 1


# ------------------------------------------- only answers to the card's question


def test_a_keep_on_a_line_the_card_never_raised_is_not_counted():
    """
    mark_grocery_item_already_have_reviewed is also a chat tool and the
    older "Already have this?" section's confirm button. A keep on a line
    the card never asked about is not an answer to a question it never
    asked — and counting it would let kept exceed flagged.
    """
    item_id = _need("Butter", "1 lb")  # no inventory, so never flagged
    tools.mark_grocery_item_already_have_reviewed(item_id)

    assert _ledger(item_id) is None
    assert _counts() == {"flagged": 0, "kept": 0, "dropped": 0, "undone": 0}


def test_have_it_on_an_unflagged_row_is_not_counted_as_a_drop():
    """
    The Shop tab's "Wait, I already have this" posts the same pre-shop
    drop on ANY list row (static/shell.js, case 'have-it'). Those rows
    were never behind the card, so they are not evidence about it.
    """
    item_id = _need("Butter", "1 lb")
    tools.drop_grocery_item_pre_shop(item_id, author="user")

    assert _ledger(item_id) is None
    assert _counts()["dropped"] == 0


def test_the_freezer_step_setting_a_line_aside_is_not_a_pre_shop_drop():
    """
    defrost.py calls this same drop with author='freezer' to set aside a
    line the household already has in the FREEZER. Different question,
    different right answer; it must not land in this accuracy count even
    when the card happened to raise the same line.
    """
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author=defrost_module.FREEZER_REMOVED_BY)

    assert _ledger(item_id)["decision"] == "", "the freezer did not answer the card"
    counts = _counts()
    assert counts["dropped"] == 0
    assert counts["flagged"] == 1, "the card still asked; nobody answered"


def test_clearing_the_flag_on_an_already_dropped_line_does_not_overwrite_the_drop():
    """
    Found by the build verifier, 2026-09-23. mark_grocery_item_already_
    have_reviewed clears a flag; it does not put anything back on the
    list. The chat tool and the old "Already have this?" confirm can both
    call it on a line that is already dropped — and filing that line as
    "the household kept it" would be a wrong answer inside the one number
    this ledger exists to produce. Only undo_pre_shop_drop takes a drop
    back, and it records 'undone'.
    """
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")

    tools.mark_grocery_item_already_have_reviewed(item_id)

    assert _ledger(item_id)["decision"] == "dropped", "the line is still off the list"
    counts = _counts()
    assert counts["dropped"] == 1
    assert counts["kept"] == 0


def test_undoing_a_freezer_set_aside_is_not_a_put_back():
    """The other half of the same rule — nothing was dropped to take back."""
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author=defrost_module.FREEZER_REMOVED_BY)
    tools.undo_pre_shop_drop(item_id)

    assert _ledger(item_id)["decision"] == ""
    assert _counts()["undone"] == 0


# ------------------------------------------------------------- the window


def test_the_summary_counts_decisions_inside_the_window():
    butter = _flagged_line("Butter")
    rice = _flagged_line("Rice", wanted="1 cup", on_hand="6 cups")
    oats = _flagged_line("Oats", wanted="1 cup", on_hand="6 cups")
    _flagged_line("Beans", wanted="1 cup", on_hand="6 cups")  # raised, never answered

    tools.mark_grocery_item_already_have_reviewed(butter)
    tools.drop_grocery_item_pre_shop(rice, author="user")
    tools.drop_grocery_item_pre_shop(oats, author="user")
    tools.undo_pre_shop_drop(oats)

    assert _counts() == {"flagged": 4, "kept": 1, "dropped": 1, "undone": 1}


def test_decisions_older_than_the_window_are_not_counted():
    """Otherwise the number is "ever", not "this week", and never moves."""
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")
    _backdate(item_id, "-9 days")

    assert _counts(days=7) == {"flagged": 0, "kept": 0, "dropped": 0, "undone": 0}
    assert _counts(days=30)["dropped"] == 1, "a wider window still finds it"


def test_the_window_boundary_compares_an_instant_against_an_instant():
    """
    Both sides are datetime('now', ...) — UTC instants — so a row stamped
    exactly seven days ago is inside a seven-day window and one a second
    older is out. A comparison against a DATE would put the edge at
    midnight UTC instead, which is not midnight anywhere a household
    lives.
    """
    inside = _flagged_line("Butter")
    outside = _flagged_line("Rice", wanted="1 cup", on_hand="6 cups")
    tools.drop_grocery_item_pre_shop(inside, author="user")
    tools.drop_grocery_item_pre_shop(outside, author="user")

    _backdate(inside, "-7 days")
    _backdate(outside, "-7 days', '-1 second")

    counts = _counts(days=7)
    assert counts["dropped"] == 1
    assert counts["flagged"] == 1


def test_a_household_that_never_saw_the_card_reports_zeros():
    """SUM over no rows is NULL in SQLite, and "None flagged" is not a number."""
    assert _counts() == {"flagged": 0, "kept": 0, "dropped": 0, "undone": 0}


def test_the_counts_are_scoped_to_the_household():
    """Like every other read in this app."""
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")

    # try/finally, not two bare statements: run against a build without
    # this table the second INSERT raises, and a leaked open connection
    # then costs every later test SQLite's busy timeout on every table the
    # clean_state fixture wipes — minutes of stall instead of one red test.
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO households (id, name) VALUES (2, 'Someone else') "
            "ON CONFLICT(id) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO pre_shop_decisions (household_id, grocery_item_id, decision, decided_at) "
            "VALUES (2, 9999, 'dropped', datetime('now'))"
        )
        conn.commit()
    finally:
        conn.close()

    assert _counts()["dropped"] == 1, "the other household's decision is not ours"


# --------------------------------------------------------- it reaches the report


def test_the_block_reaches_the_health_report(signed_in, monkeypatch):
    """
    /api/health-report -> observability_report._collect_from_db -> the
    printed line. The route is the overnight routine's only way in.
    """
    from observability_report import _collect_from_db

    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")

    monkeypatch.setenv("REPORT_TOKEN", "test-report-token")
    res = signed_in.get("/api/health-report", headers={"x-report-token": "test-report-token"})
    assert res.status_code == 200
    households = res.json()["households"]
    assert households[0]["usage"]["pre_shop"] == {
        "flagged": 1, "kept": 0, "dropped": 1, "undone": 0
    }

    # ...and the same shape from the collector the route calls.
    assert _collect_from_db(1)[0]["usage"]["pre_shop"]["dropped"] == 1


def test_the_printed_line_says_what_was_counted(capsys):
    """One plain line per household, counts only — never an item's name."""
    from observability_report import _print_human

    _print_human(
        [{
            "household_id": 1,
            "household": "Dalphy",
            "errors": {"total": 0, "by_kind": {}},
            "usage": {
                "days": 7, "chat_turns": 0, "meals_cooked": 0, "plans_generated": 0,
                "plans_approved": 0, "looks_inactive": False, "last_active_at": None,
                "pre_shop": {"flagged": 9, "kept": 2, "dropped": 6, "undone": 1},
            },
        }],
        1,
        "a local database",
    )

    out = capsys.readouterr().out
    assert "Maybe already home: 9 flagged · 6 dropped · 2 kept · 1 put back" in out


def test_a_household_with_no_flags_prints_no_line(capsys):
    """A line of four zeros every morning is noise, not a signal."""
    from observability_report import _print_human

    _print_human(
        [{
            "household_id": 1,
            "household": "Dalphy",
            "errors": {"total": 0, "by_kind": {}},
            "usage": {
                "days": 7, "chat_turns": 0, "meals_cooked": 0, "plans_generated": 0,
                "plans_approved": 0, "looks_inactive": False, "last_active_at": None,
                "pre_shop": {"flagged": 0, "kept": 0, "dropped": 0, "undone": 0},
            },
        }],
        1,
        "a local database",
    )

    assert "Maybe already home" not in capsys.readouterr().out


def test_an_older_deployment_without_the_key_still_prints(capsys):
    """
    The report reads a REMOTE app. A deployment older than this work
    answers without the key, and a morning report that crashes tells Emily
    less than one that omits a line.
    """
    from observability_report import _print_human

    _print_human(
        [{
            "household_id": 1,
            "household": "Dalphy",
            "errors": {"total": 0, "by_kind": {}},
            "usage": {
                "days": 7, "chat_turns": 0, "meals_cooked": 0, "plans_generated": 0,
                "plans_approved": 0, "looks_inactive": False, "last_active_at": None,
            },
        }],
        1,
        "a local database",
    )

    out = capsys.readouterr().out
    assert "Maybe already home" not in out
    assert "Dalphy" in out


# ------------------------------------------- it reaches databases that exist


def test_the_table_reaches_a_database_that_already_exists(tmp_path):
    """
    The hazard test_schema_migration_drift.py exists for, one table over:
    a live database is never rebuilt from schema.sql. A new TABLE is
    covered by CREATE TABLE IF NOT EXISTS, which a new COLUMN is not — but
    "it should work" is what that card was about, so this runs the real
    startup path over a database built before any of this existed.
    """
    schema = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    snapshot = (REPO / "tests" / "fixtures" / "schema_snapshot.sql").read_text(encoding="utf-8")

    path = tmp_path / "already-exists.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(snapshot)  # the database as it was before
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pre_shop_decisions'"
        ).fetchall(), "the snapshot should predate this table, or this test proves nothing"

        conn.executescript(schema)  # ...started by today's code
        _run_migrations(conn)
        conn.commit()

        columns = {r["name"] for r in conn.execute("PRAGMA table_info(pre_shop_decisions)")}
        assert columns == {
            "id", "household_id", "grocery_item_id", "flagged_at", "decision", "decided_at"
        }
        # And it is usable, not merely present — including the foreign key
        # to a household that was already there.
        hid = conn.execute("SELECT id FROM households ORDER BY id LIMIT 1").fetchone()["id"]
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO pre_shop_decisions (household_id, grocery_item_id) VALUES (?, 1)",
            (hid,),
        )
        conn.commit()
        assert conn.execute(
            "SELECT decision, decided_at, flagged_at FROM pre_shop_decisions"
        ).fetchone()["decision"] == ""
    finally:
        conn.close()


def test_the_ledger_outlives_the_grocery_line_it_is_about():
    """
    WHY THIS IS A TABLE AND NOT COLUMNS ON grocery_items. Grocery rows are
    hard-deleted when the week turns over, and the report's window is a
    week — so counts kept on those rows would shrink every week by an
    amount depending on which day the report ran, with nothing to say so.
    """
    item_id = _flagged_line("Butter")
    tools.drop_grocery_item_pre_shop(item_id, author="user")

    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

    assert _counts()["dropped"] == 1, "the week turning over must not erase the measurement"
