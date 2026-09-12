"""
No guilt pile — a slipped chore is due, not overdue.

Loop Board "Chores v1: No guilt pile" (Emily, 2026-09-11). Somebody had a
bad week. Opening the app should not be being told off, or they stop
opening it. Four things follow from that, and this file is one section per
thing:

    1. A pending occurrence whose day has gone by is simply DUE, shown
       like anything else due today, with no label, no colour and no
       count of days late.
    2. A whole backlog of one chore is ONE due row, and one tick settles
       it. Nobody is asked to tick the same mop four times.
    3. The next occurrence counts from the day it was actually DONE — so
       doing it late moves the rhythm along instead of instantly
       producing another row that is already in the past.
    4. Nothing anywhere says "overdue", "missed" or "late", and nothing
       about a slipped chore reaches a notification.

Where a test is a GUARD rather than a catch (it passes on main too,
because it pins something that was already true and must stay true) its
docstring says so.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from app import agent, tools
from app.db import _run_migrations, get_conn
from app.tools._shared import household_id

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

TODAY = datetime.date.today


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


@pytest.fixture
def emily():
    return _adult("Emily")


def _instances(chore_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, due_date, status, done_on, completed_at, completed_by_member_id, assignee_id "
        "FROM chore_instances WHERE chore_id = ? ORDER BY due_date ASC, id ASC",
        (chore_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _slipped_weekly(name: str = "Mop", weeks: int = 4, owner: str | None = "Emily") -> int:
    """
    A weekly chore whose last four occurrences all went by untouched — the
    pile this card is about. Written straight to the table so the dates are
    genuinely in the past rather than depending on a frozen clock.
    """
    chore_id = tools.add_chore(name, owner_name=owner, frequency="weekly")["chore_id"]
    conn = get_conn()
    owner_id = conn.execute("SELECT default_assignee_id FROM chores WHERE id = ?", (chore_id,)).fetchone()[0]
    for n in range(weeks, 0, -1):
        conn.execute(
            "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
            (household_id(), chore_id, owner_id, (TODAY() - datetime.timedelta(days=7 * n)).isoformat()),
        )
    conn.commit()
    conn.close()
    return chore_id


# --- 1. the column, and where the day it was done comes from ----------------

def test_done_on_is_added_once_and_survives_a_rerun():
    """The migration is idempotent: running it twice neither duplicates the
    column nor raises."""
    conn = get_conn()
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(chore_instances)")}
    assert "done_on" in columns
    assert "completed_at" in columns, "the tick's timestamp is kept, not replaced"
    _run_migrations(conn)
    _run_migrations(conn)
    again = {r["name"] for r in conn.execute("PRAGMA table_info(chore_instances)")}
    assert again == columns
    conn.close()


def test_the_real_upgrade_path_from_a_database_without_the_column(tmp_path):
    """
    The migration driven for real: a file built from today's schema.sql with
    the done_on line taken back out — i.e. the shape every existing
    household's database is in — opened by this build.

    The "before" schema is derived by STRIPPING the column this work adds,
    not fetched from origin/main. A test that asserts main lacks a column
    self-invalidates the day its own branch merges, which is exactly why
    test_chore_owner_mode's equivalent is red on main right now. The strip
    is asserted so this can never quietly become a test of nothing.
    """
    import sqlite3

    from app.db import _run_migrations

    schema = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    kept = [l for l in schema.splitlines() if "done_on TEXT" not in l]
    # Counted, not compared as strings: joining the lines back up drops the
    # trailing newline, so `before != schema` is true even when nothing was
    # stripped — which is exactly how this test would go vacuous.
    assert len(kept) == len(schema.splitlines()) - 1, "the done_on line has to actually come out"
    before = "\n".join(kept)
    assert "done_on" not in before.split("CREATE TABLE IF NOT EXISTS chore_instances (")[1].split(");")[0]

    conn = sqlite3.connect(tmp_path / "before.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(before)
    conn.execute("INSERT INTO members (household_id, name) VALUES (1, 'Emily')")
    conn.execute("INSERT INTO chores (household_id, name, frequency) VALUES (1, 'Mop', 'weekly')")
    ticked = (TODAY() - datetime.timedelta(days=9)).isoformat()
    conn.executemany(
        "INSERT INTO chore_instances (household_id, chore_id, due_date, status, completed_at) VALUES (1, 1, ?, ?, ?)",
        [
            ((TODAY() - datetime.timedelta(days=9)).isoformat(), "done", ticked + " 18:30:00"),
            ((TODAY() - datetime.timedelta(days=2)).isoformat(), "done", None),  # done, never timestamped
            (TODAY().isoformat(), "pending", None),
        ],
    )
    conn.commit()

    _run_migrations(conn)
    conn.commit()
    rows = conn.execute("SELECT due_date, status, done_on FROM chore_instances ORDER BY due_date").fetchall()
    conn.close()

    assert rows[0]["done_on"] == ticked, "derived from the tick's timestamp"
    assert rows[1]["done_on"] is None, "no timestamp to derive from — left alone rather than guessed at"
    assert rows[2]["done_on"] is None, "nothing pending ever claims a day"


def test_an_already_ticked_chore_gets_its_done_day_derived_not_re_asked(emily):
    """A chore ticked before done_on existed has a completed_at and nothing
    else. The day is DERIVED from it, the way _migrate_chore_modes derives a
    mode from the rotation it already had."""
    chore_id = tools.add_chore("Bins", owner_name="Emily", frequency="weekly")["chore_id"]
    then = (TODAY() - datetime.timedelta(days=13)).isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, due_date, status, completed_at, done_on) "
        "VALUES (1, ?, ?, 'done', ?, NULL)",
        (chore_id, then, then + " 14:05:00"),
    )
    conn.commit()
    from app.db import _backfill_chore_done_on

    _backfill_chore_done_on(conn)
    conn.commit()
    row = conn.execute("SELECT done_on FROM chore_instances WHERE chore_id = ?", (chore_id,)).fetchone()
    conn.close()
    assert row["done_on"] == then


def test_a_back_dated_done_day_is_never_overwritten_by_the_backfill(emily):
    """"I did it yesterday" is the household's answer; a migration that ran
    afterwards and reset it to the tick's timestamp would overrule them."""
    chore_id = tools.add_chore("Bins", owner_name="Emily", frequency="weekly")["chore_id"]
    asked_for = (TODAY() - datetime.timedelta(days=13)).isoformat()
    said_so = (TODAY() - datetime.timedelta(days=14)).isoformat()
    ticked_at = (TODAY() - datetime.timedelta(days=10)).isoformat() + " 14:05:00"
    conn = get_conn()
    conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, due_date, status, completed_at, done_on) "
        "VALUES (1, ?, ?, 'done', ?, ?)",
        (chore_id, asked_for, ticked_at, said_so),
    )
    conn.commit()
    from app.db import _backfill_chore_done_on

    _backfill_chore_done_on(conn)
    conn.commit()
    row = conn.execute("SELECT done_on FROM chore_instances WHERE chore_id = ?", (chore_id,)).fetchone()
    conn.close()
    assert row["done_on"] == said_so


def test_a_done_row_with_no_timestamp_at_all_still_reads_back(emily):
    """Nothing depends on the backfill having run: readers coalesce down to
    the due date rather than guessing a day and writing it."""
    chore_id = tools.add_chore("Bins", owner_name="Emily", frequency="weekly")["chore_id"]
    then = (TODAY() - datetime.timedelta(days=13)).isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, due_date, status) VALUES (1, ?, ?, 'done')",
        (chore_id, then),
    )
    conn.commit()
    conn.close()
    done = [c for c in tools.list_chores(status="done", days_ahead=0) if c["chore"] == "Bins"]
    assert done and done[0]["done_on"] == then


# --- 2. a slipped chore is DUE ---------------------------------------------

def test_a_chore_whose_day_has_gone_by_is_on_todays_card(emily):
    """The headline. It used to be due_date = today on the nose, so a chore
    that slipped fell off the screen entirely."""
    _slipped_weekly("Mop", weeks=1)
    due = tools.get_chores_due_today()
    assert [c["chore"] for c in due] == ["Mop"]
    assert due[0]["status"] == "pending"


def test_nothing_on_a_due_row_says_how_late_it_is(emily):
    """No overdue flag, no days-late number, nothing a screen could turn
    into a telling-off."""
    _slipped_weekly("Mop", weeks=4)
    row = tools.get_chores_due_today()[0]
    blob = json.dumps(row).lower()
    for word in ("overdue", "late", "missed", "behind", "days_ago", "days_late"):
        assert word not in blob, f"a due chore row must not carry {word!r}"


def test_a_slipped_chore_is_a_pending_row_in_list_chores_too(emily):
    """The assistant's own read of the chores sees it as due, not as a
    separate overdue bucket."""
    _slipped_weekly("Mop", weeks=2)
    pending = tools.list_chores(status="pending", days_ahead=14)
    assert [c["chore"] for c in pending] == ["Mop"]
    assert pending[0]["status"] == "pending"


# --- 3. one chore, one due row, one tick ------------------------------------

def test_four_missed_mops_are_one_due_row(emily):
    """The pile itself: four pending rows in the table, one job in the
    house."""
    _slipped_weekly("Mop", weeks=4)
    due = tools.get_chores_due_today()
    assert len(due) == 1
    assert due[0]["stands_for"] == 4
    assert due[0]["due_date"] == (TODAY() - datetime.timedelta(days=7)).isoformat(), (
        "the row that stands for the group is the occurrence actually due now"
    )


def test_list_chores_collapses_the_same_pile(emily):
    """Both reads collapse, or the screen and the assistant would disagree
    about how much is outstanding."""
    _slipped_weekly("Mop", weeks=4)
    pending = tools.list_chores(status="pending", days_ahead=14)
    assert len(pending) == 1 and pending[0]["stands_for"] == 4


def test_one_tick_clears_the_whole_pile(emily):
    """Nobody is asked to tick the same mop four times."""
    chore_id = _slipped_weekly("Mop", weeks=4)
    row = tools.get_chores_due_today()[0]
    tools.complete_chore(row["id"])
    assert tools.get_chores_due_today()[0]["status"] == "done"
    assert [c for c in tools.list_chores(status="pending", days_ahead=0)] == []
    left_pending = [r for r in _instances(chore_id) if r["status"] == "pending" and r["due_date"] <= TODAY().isoformat()]
    assert left_pending == []


def test_the_occurrences_a_tick_sweeps_up_are_not_recorded_as_done(emily):
    """One mop is one mop. Writing four would be false history the fairness
    view would then count."""
    chore_id = _slipped_weekly("Mop", weeks=4)
    tools.complete_chore(tools.get_chores_due_today()[0]["id"])
    statuses = [r["status"] for r in _instances(chore_id) if r["due_date"] <= TODAY().isoformat()]
    assert statuses.count("done") == 1
    assert statuses.count("skipped") == 3
    swept = [r for r in _instances(chore_id) if r["status"] == "skipped"]
    assert all(r["completed_by_member_id"] is None and r["done_on"] is None for r in swept), (
        "a swept occurrence did not happen and must not claim a doer or a day"
    )


def test_a_tick_from_the_today_card_clears_the_pile_the_same_way(emily):
    """The tap on the shell's chores card goes through the same write as
    chat — two implementations could disagree about the same mop."""
    chore_id = _slipped_weekly("Mop", weeks=3)
    tools.set_chore_instance_status(tools.get_chores_due_today()[0]["id"], "done")
    statuses = [r["status"] for r in _instances(chore_id) if r["due_date"] <= TODAY().isoformat()]
    assert statuses.count("done") == 1 and statuses.count("skipped") == 2


def test_collapsing_is_per_chore(emily):
    """Two chores that both slipped are two due rows, not one."""
    _slipped_weekly("Mop", weeks=3)
    _slipped_weekly("Bins", weeks=2)
    due = tools.get_chores_due_today()
    assert sorted(c["chore"] for c in due) == ["Bins", "Mop"]
    assert {c["chore"]: c["stands_for"] for c in due} == {"Mop": 3, "Bins": 2}


def test_a_tick_leaves_another_chores_backlog_alone(emily):
    """Sweeping is scoped to the chore that was ticked."""
    _slipped_weekly("Mop", weeks=3)
    bins = _slipped_weekly("Bins", weeks=2)
    before = _instances(bins)
    mop_row = [c for c in tools.get_chores_due_today() if c["chore"] == "Mop"][0]
    tools.complete_chore(mop_row["id"])
    assert _instances(bins) == before


def test_a_future_occurrence_is_never_collapsed_or_swept(emily):
    """Next week's mop hasn't happened yet — it is not part of the pile and
    a tick today must leave it standing."""
    chore_id = _slipped_weekly("Mop", weeks=2)
    tools.generate_chore_schedule(days_ahead=0)  # nothing new; the chore is already due
    conn = get_conn()
    conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, due_date) VALUES (1, ?, ?)",
        (chore_id, (TODAY() + datetime.timedelta(days=7)).isoformat()),
    )
    conn.commit()
    conn.close()
    due = tools.get_chores_due_today()
    assert len(due) == 1 and due[0]["stands_for"] == 2
    tools.complete_chore(due[0]["id"])
    future = [r for r in _instances(chore_id) if r["due_date"] > TODAY().isoformat()]
    assert [r["status"] for r in future] == ["pending"]


# --- 4. the clock runs from the day it was DONE -----------------------------

def test_ticking_a_slipped_chore_makes_the_next_one_a_whole_interval_away(emily):
    """The bug this card is really about: reckoning from the last DUE date
    put the next occurrence in the past too, so generating the schedule
    dealt out a fresh pile nobody could ever have been on time for."""
    chore_id = _slipped_weekly("Mop", weeks=4)
    tools.complete_chore(tools.get_chores_due_today()[0]["id"])
    tools.generate_chore_schedule(days_ahead=14)
    pending = [r["due_date"] for r in _instances(chore_id) if r["status"] == "pending"]
    assert pending and min(pending) == (TODAY() + datetime.timedelta(days=7)).isoformat()
    assert all(d > TODAY().isoformat() for d in pending), "no occurrence is ever written into the past"


def test_the_tick_itself_puts_the_next_one_on_the_calendar(emily):
    """Ticking is when the next occurrence becomes knowable, so it is
    written then rather than waiting for somebody to regenerate."""
    chore_id = _slipped_weekly("Mop", weeks=2)
    result = tools.complete_chore(tools.get_chores_due_today()[0]["id"])
    assert result["next_due"] == (TODAY() + datetime.timedelta(days=7)).isoformat()
    assert result["next_due"] in [r["due_date"] for r in _instances(chore_id)]


def test_i_did_it_yesterday_moves_the_rhythm_not_just_the_record(emily):
    """Back-dating has to reach the schedule, or saying so is only being
    humoured."""
    chore_id = _slipped_weekly("Mop", weeks=3)
    yesterday = (TODAY() - datetime.timedelta(days=1)).isoformat()
    result = tools.complete_chore(tools.get_chores_due_today()[0]["id"], done_on=yesterday)
    assert result["done_on"] == yesterday
    tools.generate_chore_schedule(days_ahead=21)
    pending = sorted(r["due_date"] for r in _instances(chore_id) if r["status"] == "pending")
    assert pending[0] == (TODAY() + datetime.timedelta(days=6)).isoformat()


def test_a_day_that_has_not_happened_is_a_question_back(emily):
    """A future done date is nonsense; guessing which day was meant is
    worse than asking."""
    _slipped_weekly("Mop", weeks=1)
    row_id = tools.get_chores_due_today()[0]["id"]
    with pytest.raises(ValueError, match="hasn't happened yet"):
        tools.complete_chore(row_id, done_on=(TODAY() + datetime.timedelta(days=1)).isoformat())
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        tools.complete_chore(row_id, done_on="last tuesday")
    assert tools.get_chores_due_today()[0]["status"] == "pending", "a refusal writes nothing"


def test_generating_the_schedule_never_tops_up_a_chore_that_is_already_due(emily):
    """A chore that slipped is due now; when the NEXT one falls is not
    knowable until it is done. Writing more dates rebuilds the pile."""
    chore_id = _slipped_weekly("Mop", weeks=2)
    before = _instances(chore_id)
    tools.generate_chore_schedule(days_ahead=28)
    assert _instances(chore_id) == before


def test_a_chore_never_done_and_long_unscheduled_comes_back_due_today(emily):
    """A monthly chore last done four months ago. Reckoned from the last
    due date alone that is a run of occurrences stretching back months,
    every one of them already missed. It is one, today."""
    chore_id = tools.add_chore("Filters", owner_name="Emily", frequency="monthly")["chore_id"]
    conn = get_conn()
    conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, due_date, status, done_on, completed_at) "
        "VALUES (1, ?, ?, 'done', ?, datetime('now'))",
        (chore_id, (TODAY() - datetime.timedelta(days=120)).isoformat(),
         (TODAY() - datetime.timedelta(days=120)).isoformat()),
    )
    conn.commit()
    conn.close()
    tools.generate_chore_schedule(days_ahead=0)
    pending = [r["due_date"] for r in _instances(chore_id) if r["status"] == "pending"]
    assert pending == [TODAY().isoformat()]


def test_a_schedule_that_is_running_ahead_is_left_exactly_as_it_was(emily):
    """A no-regression promise: a household keeping up must see the same
    dates and the same turns as before the card. It does go red on main,
    but only because the _instances helper reads the done_on column — the
    dates it asserts are main's own, so treat it as a guard rather than
    counting it as a catch."""
    chore_id = tools.add_chore("Vacuuming", owner_name="Emily", frequency="weekly")["chore_id"]
    tools.generate_chore_schedule(days_ahead=21)
    first = _instances(chore_id)
    assert [r["due_date"] for r in first] == [
        (TODAY() + datetime.timedelta(days=n)).isoformat() for n in (0, 7, 14, 21)
    ]
    tools.complete_chore(first[0]["id"])
    tools.generate_chore_schedule(days_ahead=35)
    assert [r["due_date"] for r in _instances(chore_id)] == [
        (TODAY() + datetime.timedelta(days=n)).isoformat() for n in (0, 7, 14, 21, 28, 35)
    ]


# --- 5. what the Today card shows after a tick ------------------------------

def test_a_slipped_chore_ticked_today_stays_on_todays_card(emily):
    """Otherwise the row vanishes under the finger and "1 of 1" becomes
    "0 of 0" — the app forgetting what somebody just did."""
    _slipped_weekly("Mop", weeks=2)
    tools.complete_chore(tools.get_chores_due_today()[0]["id"])
    due = tools.get_chores_due_today()
    assert len(due) == 1 and due[0]["status"] == "done" and due[0]["chore"] == "Mop"
    assert due[0]["done_on"] == TODAY().isoformat()


def test_a_chore_done_last_week_is_not_on_todays_card(emily):
    """Widening the read to past dates must not drag finished history onto
    the screen."""
    chore_id = tools.add_chore("Bins", owner_name="Emily", frequency="weekly")["chore_id"]
    conn = get_conn()
    old = (TODAY() - datetime.timedelta(days=7)).isoformat()
    conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, due_date, status, done_on, completed_at) "
        "VALUES (1, ?, ?, 'done', ?, datetime('now'))",
        (chore_id, old, old),
    )
    conn.commit()
    conn.close()
    assert tools.get_chores_due_today() == []


def test_unticking_brings_back_the_one_row_not_the_pile(emily):
    """A mis-tap must not resurrect three weeks of dates — the exact thing
    this card exists to stop."""
    chore_id = _slipped_weekly("Mop", weeks=3)
    row_id = tools.get_chores_due_today()[0]["id"]
    tools.set_chore_instance_status(row_id, "done")
    tools.set_chore_instance_status(row_id, "pending")
    due = tools.get_chores_due_today()
    assert len(due) == 1 and due[0]["status"] == "pending" and due[0]["stands_for"] == 1
    back = {r["id"]: r for r in _instances(chore_id)}[row_id]
    assert back["done_on"] is None and back["completed_at"] is None


# --- 6. nothing says overdue ------------------------------------------------

def test_the_assistant_is_told_a_slipped_chore_is_due_not_overdue():
    """The words a person reads come from the model, so the rule has to be
    in the prompt, not only in the data."""
    blob = agent.SYSTEM_PROMPT.lower()
    assert "due, not overdue" in blob
    assert "you missed" in blob, "the forbidden phrasings are named, so they can't be re-invented"
    assert "what did we miss" in blob, "the one question that IS answered, plainly and once"


def test_the_chore_tools_describe_the_no_guilt_contract():
    by_name = {t["name"]: t for t in agent.TOOL_DEFINITIONS}
    assert "done_on" in by_name["complete_chore"]["input_schema"]["properties"]
    complete = by_name["complete_chore"]["description"].lower()
    assert "yesterday" in complete, "the sentence a household actually says"
    listing = by_name["list_chores"]["description"].lower()
    assert "no overdue status" in listing
    assert "stands_for" in listing and "tally" in listing


def test_the_chores_card_never_paints_a_chore_urgent():
    """Guard, not a catch: terracotta is reserved (DESIGN_SYSTEM hard rule)
    and no chore rule has ever used it. Apricot means "wants doing"; that
    is as strong as chores ever get."""
    chore_rules = [
        line for line in SHELL_CSS.splitlines()
        if line.strip().startswith(".chore") or line.strip().startswith(".chores")
    ]
    joined = "\n".join(chore_rules)
    assert "--urgent" not in joined
    block = SHELL_CSS[SHELL_CSS.index("/* Your chores */"): SHELL_CSS.index("/* Your chores */") + 3000]
    assert "--urgent" not in block


def test_the_chores_card_markup_has_no_overdue_language():
    """Guard: the row prints the chore and whose it is, and no date at all,
    so there is nothing for a "3 weeks late" to be computed from."""
    start = SHELL_JS.index("function renderChores(")
    end = SHELL_JS.index("\n  async function toggleChore(", start)
    block = SHELL_JS[start:end].lower()
    # "missed"/"behind" are deliberately not in this list: the function
    # carries a contrast comment about the tick's stroke that uses the word
    # in another sense, and a guard that trips on prose is one somebody
    # loosens rather than reads.
    for word in ("overdue", "days late", "days ago", "due_date"):
        assert word not in block, f"the chores card must not print {word!r}"


def test_no_notification_is_ever_about_a_slipped_chore():
    """Guard: reminders are a separate v2 card, owner-only and off by
    default. Nothing in the notification feed reads chores at all, and this
    fails the day something starts to."""
    source = (REPO / "app" / "tools" / "notifications.py").read_text(encoding="utf-8")
    assert "chore" not in source.lower()


def test_a_slipped_chore_puts_nothing_in_the_notification_feed(emily):
    """Guard, and the same promise driven rather than read: a four-week-old
    mop produces no notification. Green on main too — nothing regressed
    here, and nothing is allowed to."""
    _slipped_weekly("Mop", weeks=4)
    keys = " ".join(json.dumps(n) for n in tools.get_active_notifications()).lower()
    assert "mop" not in keys and "chore" not in keys


# --- 7. households stay separate -------------------------------------------

def test_the_collapse_and_the_sweep_never_cross_households(emily):
    from app import households

    beta = households.create_household("The Beta Testers", "beta-passphrase-long-enough")
    mine = _slipped_weekly("Mop", weeks=3)
    with tools.use_household(beta):
        tools.add_member("Priya")
        tools.set_member_age_group("Priya", "Adult")
        theirs = _slipped_weekly("Mop", weeks=2, owner="Priya")
        assert len(tools.get_chores_due_today()) == 1
        assert tools.get_chores_due_today()[0]["stands_for"] == 2, "counts only their own occurrences"
        tools.complete_chore(tools.get_chores_due_today()[0]["id"])
        theirs_after = _instances(theirs)

    assert [r["status"] for r in _instances(mine)] == ["pending"] * 3, "their tick swept nothing of ours"
    assert tools.get_chores_due_today()[0]["stands_for"] == 3
    tools.complete_chore(tools.get_chores_due_today()[0]["id"])
    with tools.use_household(beta):
        assert _instances(theirs) == theirs_after
