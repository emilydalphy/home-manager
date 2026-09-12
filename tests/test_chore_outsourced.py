"""
A chore somebody outside the house does — the fourth mode, `outsourced`.

Loop Board "Chores v1: Tag a chore as outsourced". A household with a
cleaner, a lawn service or a laundry pickup wants those chores on the week
— knowing Thursday is cleaner day is the point — without anybody in the
house being asked for them or credited for them.

So `chores.mode` gains a fourth value beside owned/shared/whoever, and
`chores.outsourced_to` optionally names who does it ("Maria", "the lawn
people"). The promises this file holds:

    - it can be tagged at setup, on the row, or by saying so
    - it keeps its frequency and still gets its instances, with no
      assignee and no tick
    - it counts for nobody
    - handing it back returns it to owned with no owner, and asks who
    - the starter list reads the profile's existing-help answer rather
      than asking a second time
    - a slipped occurrence does not stop it from generating (Loop Board
      "A slipped outsourced chore stops being scheduled, and nothing on
      any screen can clear it", Emily) — see section 4b

Both files' style: run the real functions and the real routes, and say in
each docstring whether a test is a catch (it fails on main) or a guard.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from pathlib import Path

import pytest

from app import agent, tools
from app.db import _run_migrations, get_conn
from app.tools._shared import household_id

TODAY = datetime.date.today

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
CHORES_SETUP = (REPO / "static" / "chores-setup.html").read_text(encoding="utf-8")


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


# --- 1. schema + migration ---------------------------------------------------

def test_the_label_column_is_added_once_and_survives_a_rerun():
    """Guard: the migration is idempotent, like every other one here."""
    conn = get_conn()
    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(chores)")]
    conn.close()
    assert cols.count("outsourced_to") == 1


def _schema_without_the_new_column() -> str:
    """
    Today's schema.sql with this card's column taken back out — i.e. the
    chores table exactly as it stood before this work.

    Deliberately NOT `git show origin/main:app/schema.sql`, which is how
    the owner card's equivalent test built its "before" database. That
    pattern is self-invalidating: the moment the branch merged, main grew
    the column its precondition asserts is absent, and the test has been
    failing on main ever since for a reason with nothing to do with the
    migration. It also makes a unit test depend on a fetched git remote.
    Stripping a line out of the file in front of us has neither problem —
    and the strip is asserted to have actually happened, so this can never
    quietly go vacuous by testing today's schema against itself.
    """
    schema = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    head, rest = schema.split("CREATE TABLE IF NOT EXISTS chores (", 1)
    chores_ddl, tail = rest.split(");", 1)
    before = "\n".join(l for l in chores_ddl.split("\n") if "outsourced_to" not in l)
    assert "outsourced_to" in chores_ddl and "outsourced_to" not in before, "the strip did nothing"
    return head + "CREATE TABLE IF NOT EXISTS chores (" + before + ");" + tail


def test_migration_against_a_database_made_by_the_previous_schema(tmp_path):
    """
    Catch: the real upgrade path. A database created without
    outsourced_to, with chores already carrying the three owner modes,
    opened by this build. Nothing is retagged: no older fact says a chore
    was outsourced, so inventing one would be inventing history.
    """
    schema = _schema_without_the_new_column()
    conn = sqlite3.connect(tmp_path / "before.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    conn.execute("INSERT INTO members (household_id, name) VALUES (1, 'Emily'), (1, 'Vineeth')")
    conn.executemany(
        "INSERT INTO chores (household_id, name, default_assignee_id, rotation_member_ids_json, mode) "
        "VALUES (1, ?, ?, ?, ?)",
        [("Bathrooms", 1, "[1]", "owned"), ("Vacuuming", 1, "[1, 2]", "shared"), ("Bins", None, "[]", "whoever")],
    )
    conn.commit()

    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    got = {r["name"]: (r["mode"], r["outsourced_to"])
           for r in conn.execute("SELECT name, mode, outsourced_to FROM chores")}
    conn.close()
    assert got == {
        "Bathrooms": ("owned", ""),
        "Vacuuming": ("shared", ""),
        "Bins": ("whoever", ""),
    }


def test_the_schema_documents_the_fourth_mode():
    """Guard: schema.sql's comment block is where the modes are written down."""
    text = (REPO / "app" / "schema.sql").read_text(encoding="utf-8")
    block = text.split("CREATE TABLE IF NOT EXISTS chores (")[0].rsplit("-- A chore definition", 1)[1]
    assert "outsourced" in block
    assert "outsourced_to TEXT NOT NULL DEFAULT ''" in text


# --- 2. tagging it -----------------------------------------------------------

def test_a_chore_can_be_added_outsourced_with_a_label(two_adults):
    """Catch: mode 'outsourced' doesn't exist on main."""
    result = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    assert result["mode"] == "outsourced"
    assert result["outsourced"] is True
    assert result["outsourced_to"] == "Maria"
    assert result["who_label"] == "Maria"
    assert result["owner"] is None
    assert result["assignees"] == []
    row = _chore_row(result["chore_id"])
    assert row["mode"] == "outsourced"
    assert row["default_assignee_id"] is None
    assert json.loads(row["rotation_member_ids_json"]) == []
    assert row["outsourced_to"] == "Maria"


def test_the_label_is_optional(two_adults):
    """Catch: a household may not want to name who comes in."""
    result = tools.add_chore("Lawn", mode="outsourced")
    assert result["mode"] == "outsourced" and result["outsourced_to"] == ""
    # Never "either of you" — that is the blank for a chore that is still
    # ours, and this one is precisely nobody here's.
    assert result["who_label"] == "someone else"


def test_naming_who_comes_in_is_the_tag(two_adults):
    """
    Catch: "the lawn people do the mowing" doesn't also say the word
    outsourced, so outsourced_to on its own implies the mode.
    """
    result = tools.add_chore("Mowing", outsourced_to="the lawn people")
    assert result["mode"] == "outsourced"
    assert result["who_label"] == "the lawn people"


def test_the_label_is_never_looked_up_as_a_member(two_adults):
    """
    Catch: a cleaner is not in the household, so 'Maria' must not go
    through the member resolver — which would answer "I don't know anyone
    called Maria" to a household that has just said she doesn't live here.
    """
    result = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    assert result["mode"] == "outsourced"
    assert {m["name"] for m in tools.list_members()} == {"Emily", "Vineeth"}, "and no member was invented"


def test_an_owner_name_alongside_outsourced_does_not_win(two_adults):
    """
    Guard: mode is the explicit statement, so a stray owner_name on the
    same call cannot quietly hand the chore back to somebody in the house.
    """
    result = tools.add_chore("Bathrooms", mode="outsourced", owner_name="Emily", outsourced_to="Maria")
    assert result["mode"] == "outsourced"
    assert result["owner"] is None


def test_an_unknown_mode_still_says_all_four(two_adults):
    """Guard: the refusal names the fourth option now, or it teaches a lie."""
    with pytest.raises(ValueError, match="owned, shared, whoever or outsourced"):
        tools.add_chore("Bathrooms", mode="hired")


def test_tagging_an_existing_chore_takes_it_off_whoever_had_it(two_adults):
    """
    Catch: "the cleaner does the bathrooms now". The chore was Emily's
    with a schedule already generated; tagging it takes her off every
    instance not yet done.
    """
    chore = tools.add_chore("Bathrooms", owner_name="Emily", frequency="weekly")
    tools.generate_chore_schedule(days_ahead=14)
    assert {i["assignee_id"] for i in _instances(chore["chore_id"])} == {two_adults["Emily"]}

    result = tools.update_chore(chore["chore_id"], mode="outsourced", outsourced_to="Maria")
    assert result["mode"] == "outsourced" and result["outsourced_to"] == "Maria"
    assert result["owner"] is None
    assert {i["assignee_id"] for i in _instances(chore["chore_id"])} == {None}


def test_a_done_instance_keeps_who_did_it(two_adults):
    """
    Guard: the done ones are history. Tagging a chore outsourced today
    does not rewrite who did it last Tuesday — the same rule the owner
    card set for an owner change.
    """
    chore = tools.add_chore("Bathrooms", owner_name="Emily")
    tools.generate_chore_schedule(days_ahead=14)
    first = _instances(chore["chore_id"])[0]
    tools.complete_chore(first["id"], done_by="Vineeth")

    tools.update_chore(chore["chore_id"], mode="outsourced", outsourced_to="Maria")
    after = {i["id"]: i for i in _instances(chore["chore_id"])}
    assert after[first["id"]]["status"] == "done"
    assert after[first["id"]]["completed_by_member_id"] == two_adults["Vineeth"]
    assert after[first["id"]]["assignee_id"] == two_adults["Emily"]


def test_relabelling_keeps_the_tag_and_an_omitted_label_is_left_alone(two_adults):
    """Catch: "it's Maria now, not the agency" is a relabel, not an untag."""
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="the agency")
    assert tools.update_chore(chore["chore_id"], outsourced_to="Maria")["outsourced_to"] == "Maria"
    # ...and changing something else entirely leaves the label standing.
    again = tools.update_chore(chore["chore_id"], frequency="biweekly")
    assert again["mode"] == "outsourced" and again["outsourced_to"] == "Maria"
    assert _chore_row(chore["chore_id"])["frequency"] == "biweekly"


# --- 3. handing it back ------------------------------------------------------

def test_untagging_returns_it_to_owned_with_nobody_and_asks_who(two_adults):
    """
    Catch: the acceptance criterion. "We're doing the bathrooms ourselves
    again" lands — the tag and the label both go — and the owner is left
    blank with needs_owner saying so, rather than one being inferred. The
    household has just taken a chore back off somebody; who picks it up is
    exactly the thing they haven't said.
    """
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    tools.generate_chore_schedule(days_ahead=14)

    result = tools.update_chore(chore["chore_id"], mode="owned")
    assert result["mode"] == "owned"
    assert result["owner"] is None
    assert result["needs_owner"] is True
    assert result["outsourced"] is False and result["outsourced_to"] == ""
    row = _chore_row(chore["chore_id"])
    assert row["outsourced_to"] == "", "the label cannot outlive the tag"
    assert row["default_assignee_id"] is None


def test_an_unclaimed_chore_does_not_print_either_of_you(two_adults):
    """
    Catch: found driving the real app. After an untag the row had no name
    to print and fell back to _nobody_label — "either of you", which is
    the label for a chore the household has DECIDED is nobody's in
    particular. This one is a question nobody has answered yet, so it
    says what Meals already says for a decision handed back.
    """
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    back = tools.update_chore(chore["chore_id"], mode="owned")
    assert back["who_label"] == "Your call"
    # ...and a chore that really is nobody's in particular still says so.
    assert tools.add_chore("Bins", mode="whoever")["who_label"] == "either of you"


def test_untagging_by_naming_someone_needs_no_second_question(two_adults):
    """Catch: "I'll take the bathrooms back" is both halves in one go."""
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    tools.generate_chore_schedule(days_ahead=14)

    result = tools.update_chore(chore["chore_id"], owner_name="Vineeth")
    assert result["mode"] == "owned" and result["owner"] == "Vineeth"
    assert result["needs_owner"] is False
    assert {i["assignee_id"] for i in _instances(chore["chore_id"])} == {two_adults["Vineeth"]}


def test_untagging_to_shared_or_whoever_still_works(two_adults):
    """Guard: owned is only the default landing, not the only one."""
    a = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    assert tools.update_chore(a["chore_id"], mode="shared")["mode"] == "shared"
    b = tools.add_chore("Bins", mode="outsourced")
    back = tools.update_chore(b["chore_id"], mode="whoever")
    assert back["mode"] == "whoever" and back["who_label"] == "either of you"


def test_an_ordinary_owned_chore_does_not_claim_to_need_an_owner(two_adults):
    """Guard: needs_owner is the untagged-and-unclaimed shape only."""
    assert tools.add_chore("Dishes", owner_name="Emily")["needs_owner"] is False
    assert tools.add_chore("Bins", mode="whoever")["needs_owner"] is False


# --- 4. the schedule: on the week, with no assignee --------------------------

def test_it_keeps_its_frequency_and_still_gets_its_day(two_adults):
    """
    Catch: the whole point. An outsourced chore is still scheduled — we
    know Thursday is cleaner day — it just goes to nobody.
    """
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria", frequency="weekly")
    created = tools.generate_chore_schedule(days_ahead=14)
    mine = [c for c in created if c["chore"] == "Bathrooms"]
    assert len(mine) == 3, "today, +7, +14 — the same cadence any weekly chore gets"
    assert [i["assignee_id"] for i in _instances(chore["chore_id"])] == [None, None, None]


def test_todays_row_is_tagged_and_carries_the_label(two_adults):
    """Catch: what a screen needs to draw the tag and leave the tick off."""
    tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    tools.add_chore("Dishes", owner_name="Emily")
    tools.generate_chore_schedule(days_ahead=0)
    today = {r["chore"]: r for r in tools.get_chores_due_today()}
    assert today["Bathrooms"]["outsourced"] is True
    assert today["Bathrooms"]["completable"] is False
    assert today["Bathrooms"]["outsourced_to"] == "Maria"
    assert today["Bathrooms"]["who_label"] == "Maria"
    assert today["Bathrooms"]["assignee"] is None
    assert today["Dishes"]["outsourced"] is False and today["Dishes"]["completable"] is True


def test_an_unlabelled_outsourced_row_never_says_either_of_you(two_adults):
    """
    Catch: the bug the label fallback exists to stop. With no assignee,
    the old row builder reached for _nobody_label, which with two adults
    says "either of you" — the opposite of what an outsourced chore means.
    """
    tools.add_chore("Lawn", mode="outsourced")
    tools.generate_chore_schedule(days_ahead=0)
    row = [r for r in tools.get_chores_due_today() if r["chore"] == "Lawn"][0]
    assert row["who_label"] == "someone else"


def test_a_one_off_instance_scheduled_by_hand_goes_to_nobody_too(two_adults):
    """Guard: schedule_chore_instance follows the mode like the engine does."""
    tools.add_chore("Deep clean", mode="outsourced", outsourced_to="Maria", frequency="once")
    tools.schedule_chore_instance("Deep clean", "2030-01-01")
    row = [r for r in tools.list_chores(status="all", days_ahead=10000) if r["chore"] == "Deep clean"][0]
    assert row["assignee"] is None and row["outsourced"] is True


def test_list_chore_definitions_says_which_ones_are_not_ours(two_adults):
    """Catch: the definition list is what a Plan | Chores screen reads."""
    tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    tools.add_chore("Dishes", owner_name="Emily")
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["outsourced"] is True
    assert by_name["Bathrooms"]["outsourced_to"] == "Maria"
    assert by_name["Dishes"]["outsourced"] is False
    assert by_name["Dishes"]["outsourced_to"] == ""


# --- 4b. a slipped occurrence does not stop the rhythm -----------------------
#
# Loop Board "A slipped outsourced chore stops being scheduled, and nothing
# on any screen can clear it" (Emily). Two correct-on-their-own rules used
# to meet badly here: an outsourced chore is never ticked (section 5
# below), and _next_due_date holds back a chore that is already due until
# the slipped occurrence is settled — see test_chores_no_guilt.py. Put
# together, a cleaner day that slips stopped generating future cleaner
# days, and nothing anywhere could clear the slipped row to restart it.
#
# The fix: the no-guilt hold exists so a PERSON never opens the app to a
# pile they feel behind on. An outsourced chore can't be handed a pile —
# nobody in the house is the one behind — so the hold doesn't apply to it.
# It keeps generating on schedule, reckoned from the slipped due date
# itself stepped forward by the interval until the result is after today,
# so a weekly cleaner who missed this Thursday gets next Thursday, not a
# run of back-dated Thursdays and not a drift onto a different weekday.
# The slipped row itself is untouched by any of this — still pending,
# still due, still un-tickable (section 5) — this section is only about
# whether a FUTURE occurrence also gets written.


def _slipped_chore(
    name: str,
    weeks: int,
    *,
    mode: str | None = None,
    owner_name: str | None = None,
    outsourced_to: str | None = None,
    frequency: str = "weekly",
) -> int:
    """
    A weekly chore whose most recent `weeks` occurrences have already gone
    by, unsettled — written straight to the table, like _slipped_weekly in
    test_chores_no_guilt.py, so the dates are genuinely in the past rather
    than depending on a frozen clock.
    """
    chore = tools.add_chore(name, mode=mode, owner_name=owner_name, outsourced_to=outsourced_to, frequency=frequency)
    chore_id = chore["chore_id"]
    conn = get_conn()
    assignee_id = conn.execute(
        "SELECT default_assignee_id FROM chores WHERE id = ?", (chore_id,)
    ).fetchone()["default_assignee_id"]
    for n in range(weeks, 0, -1):
        conn.execute(
            "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
            (household_id(), chore_id, assignee_id, (TODAY() - datetime.timedelta(days=7 * n)).isoformat()),
        )
    conn.commit()
    conn.close()
    return chore_id


def test_a_missed_cleaner_day_still_gets_next_weeks(two_adults):
    """
    Catch: on main, an outsourced chore with a slipped occurrence
    generates nothing further — the schedule silently stops — because
    'skipped' (the only thing that would lift the no-guilt hold) has no
    control on any screen or chat tool. A missed cleaner day should still
    put next week's on the calendar.
    """
    _slipped_chore("Bathrooms", weeks=1, mode="outsourced", outsourced_to="Maria")
    created = tools.generate_chore_schedule(days_ahead=14)
    due_dates = sorted(c["due_date"] for c in created if c["chore"] == "Bathrooms")
    assert due_dates, "the slipped occurrence must not be the last one ever written"
    # anchor (last week's slipped Thursday) + one interval lands exactly on
    # today in this setup, which is still not "after today" — the rhythm
    # steps one more week to land on the household's usual day.
    assert due_dates[0] == (TODAY() + datetime.timedelta(days=7)).isoformat()


def test_no_past_dated_row_is_ever_written_for_it(two_adults):
    """
    Catch: the exemption must not reopen the door to the very pile the
    no-guilt rule exists to prevent. Even with several missed weeks in a
    row, nothing newly written may fall before today.
    """
    _slipped_chore("Lawn", weeks=3, mode="outsourced")
    created = tools.generate_chore_schedule(days_ahead=21)
    mine = [c["due_date"] for c in created if c["chore"] == "Lawn"]
    assert mine, "the lawn should still be back on the schedule"
    assert all(d > TODAY().isoformat() for d in mine), "no newly written date may be today or in the past"


def test_a_manual_future_one_off_does_not_swallow_the_intervening_weeks(two_adults):
    """
    Catch: an independent review found that anchoring on the most recent
    due_date across EVERY instance (rather than on the slipped row's own
    due date) breaks when a manual future one-off exists.

    Repro: a weekly outsourced chore has one slipped pending row 14 days
    ago, plus a one-off instance scheduled by hand 30 days out
    (schedule_chore_instance). Anchoring on the day-30 row (the most
    recent row on file) instead of the day-(-14) slipped row silently
    swallowed every weekly slot in between — days 7, 14, 21 and 28 never
    got written, a 5-week gap collapsing into a single row at day 37.
    """
    chore_id = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria", frequency="weekly")["chore_id"]
    conn = get_conn()
    conn.execute(
        "INSERT INTO chore_instances (household_id, chore_id, assignee_id, due_date) VALUES (?, ?, ?, ?)",
        (household_id(), chore_id, None, (TODAY() - datetime.timedelta(days=14)).isoformat()),
    )
    conn.commit()
    conn.close()
    tools.schedule_chore_instance("Bathrooms", (TODAY() + datetime.timedelta(days=30)).isoformat())

    created = tools.generate_chore_schedule(days_ahead=40)
    mine = sorted(c["due_date"] for c in created if c["chore"] == "Bathrooms")

    expected = [(TODAY() + datetime.timedelta(days=n)).isoformat() for n in (7, 14, 21, 28, 35)]
    assert mine == expected, "every weekly slot between the slip and the manual one-off must still be written"

    on_day_30 = [
        r for r in _instances(chore_id) if r["due_date"] == (TODAY() + datetime.timedelta(days=30)).isoformat()
    ]
    assert len(on_day_30) == 1, "the manual one-off must not be duplicated by the generated cadence"


def test_a_slipped_owned_chore_still_generates_nothing(two_adults):
    """
    Guard: the hold this ticket is carving an exception out of still
    applies to an ordinary owned chore — this fix is about outsourced
    chores only. See test_chores_no_guilt.py for the full contract.
    """
    _slipped_chore("Dishes", weeks=2, owner_name="Emily")
    created = tools.generate_chore_schedule(days_ahead=14)
    assert [c for c in created if c["chore"] == "Dishes"] == []


def test_an_outsourced_chore_due_today_still_generates_normally(two_adults):
    """Guard: this fix only changes what happens once a chore has slipped
    into the past — a chore due today (not yet late) was never held back
    and must not start being treated differently."""
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria", frequency="weekly")
    created = tools.generate_chore_schedule(days_ahead=14)
    mine = sorted(c["due_date"] for c in created if c["chore"] == "Bathrooms")
    assert mine == [
        TODAY().isoformat(),
        (TODAY() + datetime.timedelta(days=7)).isoformat(),
        (TODAY() + datetime.timedelta(days=14)).isoformat(),
    ]


def test_a_backlog_of_outsourced_rows_still_reads_as_one_row(two_adults):
    """Guard: the collapse-into-one-row behaviour for a backlog (see
    test_chores_no_guilt.py's test_four_missed_mops_are_one_due_row) must
    keep holding for an outsourced chore — this fix does not touch it."""
    _slipped_chore("Bathrooms", weeks=4, mode="outsourced", outsourced_to="Maria")
    due = tools.get_chores_due_today()
    assert len(due) == 1
    assert due[0]["stands_for"] == 4
    assert due[0]["due_date"] == (TODAY() - datetime.timedelta(days=7)).isoformat()
    assert due[0]["outsourced"] is True and due[0]["completable"] is False


def test_ticking_the_slipped_outsourced_row_is_still_refused(two_adults):
    """
    Guard: this ticket deliberately does not add a 'didn't happen' or
    'skip' control — that belongs to "Chores v1: Skip, swap, or 'not this
    week'". The slipped row stays exactly as un-tickable as any other
    outsourced row.
    """
    _slipped_chore("Bathrooms", weeks=2, mode="outsourced", outsourced_to="Maria")
    row = tools.get_chores_due_today()[0]
    with pytest.raises(ValueError, match="Maria's, not ours"):
        tools.complete_chore(row["id"])
    assert tools.get_chores_due_today()[0]["status"] == "pending"


# --- 5. no tick, and it counts for nobody ------------------------------------

def test_it_cannot_be_ticked_off_by_anybody(two_adults):
    """
    Catch: a tick means a person in this house did a thing, and it is what
    the fairness view will count. Allowing one would either credit
    somebody who didn't do it or record a completion belonging to nobody.
    A calm sentence, and nothing written.
    """
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    tools.generate_chore_schedule(days_ahead=0)
    instance = _instances(chore["chore_id"])[0]

    with pytest.raises(ValueError, match="Maria's, not ours"):
        tools.complete_chore(instance["id"])
    with pytest.raises(ValueError, match="Maria's, not ours"):
        tools.complete_chore(instance["id"], done_by="Emily")
    with pytest.raises(ValueError, match="Maria's, not ours"):
        tools.set_chore_instance_status(instance["id"], "done")

    after = _instances(chore["chore_id"])[0]
    assert after["status"] == "pending"
    assert after["completed_by_member_id"] is None


def test_the_refusal_reads_plainly_with_no_label(two_adults):
    """Guard: the sentence is for a person, not a stack trace."""
    tools.add_chore("Lawn", mode="outsourced")
    tools.generate_chore_schedule(days_ahead=0)
    instance = tools.get_chores_due_today()[0]
    with pytest.raises(ValueError, match="Lawn is somebody else's — there's nothing to tick off."):
        tools.complete_chore(instance["id"])


def test_it_can_still_be_skipped(two_adults):
    """
    Catch: the cleaner not coming is a real thing that happens to a real
    Thursday, so 'skipped' stays available — only the tick is refused.
    """
    chore = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    tools.generate_chore_schedule(days_ahead=0)
    instance = _instances(chore["chore_id"])[0]
    tools.set_chore_instance_status(instance["id"], "skipped")
    assert _instances(chore["chore_id"])[0]["status"] == "skipped"


def test_an_ordinary_chore_can_still_be_ticked(two_adults):
    """Guard: the refusal is narrow — everything else works as it did."""
    chore = tools.add_chore("Dishes", owner_name="Emily")
    tools.generate_chore_schedule(days_ahead=0)
    instance = _instances(chore["chore_id"])[0]
    tools.complete_chore(instance["id"], done_by="Vineeth")
    assert _instances(chore["chore_id"])[0]["completed_by_member_id"] == two_adults["Vineeth"]


def test_is_outsourced_is_the_one_question_a_counter_asks(two_adults):
    """
    Catch: the fairness view is v2 and doesn't exist yet, so what this
    card owes it is one answer in one place — readable off a chores row
    and off an instance joined to one, so the two can never disagree.
    """
    outsourced = tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")["chore_id"]
    ours = tools.add_chore("Dishes", owner_name="Emily")["chore_id"]
    assert tools.is_outsourced(_chore_row(outsourced)) is True
    assert tools.is_outsourced(_chore_row(ours)) is False

    tools.generate_chore_schedule(days_ahead=0)
    conn = get_conn()
    rows = conn.execute(
        tools.chores._INSTANCE_SELECT + " WHERE ci.household_id = 1"
    ).fetchall()
    conn.close()
    assert {r["chore"]: tools.is_outsourced(r) for r in rows} == {"Bathrooms": True, "Dishes": False}


def test_a_legacy_row_with_no_mode_is_still_ours(two_adults):
    """
    Guard: chore_mode()'s derivation for a '' row can only ever answer
    owned/shared/whoever, so nothing that predates this card can be read
    as outsourced by accident.
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO chores (household_id, name, default_assignee_id, rotation_member_ids_json, mode) "
        "VALUES (1, 'Legacy', NULL, '[]', '')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM chores WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    assert tools.chores.chore_mode(row) == "whoever"
    assert tools.is_outsourced(row) is False


# --- 6. the routes -----------------------------------------------------------

def test_the_today_route_refuses_a_tick_on_an_outsourced_chore(signed_in, two_adults):
    """Catch: over the real HTTP route, not just the tool."""
    tools.set_chores_enabled(True)
    tools.add_chore("Bathrooms", mode="outsourced", outsourced_to="Maria")
    tools.generate_chore_schedule(days_ahead=0)
    res = signed_in.get("/api/chores/today")
    assert res.status_code == 200
    row = res.json()["chores"][0]
    assert row["outsourced"] is True and row["completable"] is False and row["who_label"] == "Maria"

    done = signed_in.post(f"/api/chores/{row['id']}/status", json={"status": "done"})
    assert done.status_code != 200
    assert "Maria" in done.json()["detail"]
    assert signed_in.get("/api/chores/today").json()["chores"][0]["status"] == "pending"


def test_the_setup_save_route_takes_an_outsourced_row(signed_in, two_adults):
    """Catch: tagging at setup, through the route the wizard posts to."""
    res = signed_in.post("/api/onboarding/chores/save", json={"chores": [
        {"name": "Bathrooms", "mode": "outsourced", "outsourced_to": "Maria"},
        {"name": "Mowing", "mode": "outsourced"},
        {"name": "Dishes", "mode": "owned", "owner_name": "Emily"},
    ]})
    assert res.status_code == 200 and res.json()["created"] == 3
    by_name = {d["name"]: d for d in tools.list_chore_definitions()}
    assert by_name["Bathrooms"]["outsourced_to"] == "Maria"
    assert by_name["Mowing"]["mode"] == "outsourced" and by_name["Mowing"]["outsourced_to"] == ""
    assert by_name["Dishes"]["owner"] == "Emily"
    # ...and the schedule that followed gave the outsourced ones to nobody.
    today = {r["chore"]: r for r in tools.get_chores_due_today()}
    assert today["Bathrooms"]["assignee"] is None and today["Dishes"]["assignee"] == "Emily"


def test_an_outsourced_row_is_never_skipped_for_naming_a_stranger(signed_in, two_adults):
    """
    Catch: the save route skips a row naming somebody who isn't a member.
    "Maria" is exactly that and must NOT trip it — she isn't meant to be
    one.
    """
    res = signed_in.post("/api/onboarding/chores/save", json={"chores": [
        {"name": "Bathrooms", "mode": "outsourced", "outsourced_to": "Maria"},
    ]})
    assert res.json() == {"saved": True, "created": 1, "skipped": []}


# --- 7. the starter list reads the help they already told us about -----------

def test_the_recommendation_prompt_reads_the_existing_help_answer():
    """
    Catch: the acceptance criterion — propose which chores to tag, rather
    than asking a second time.
    """
    calls = {}

    class _FakeBlock:
        type = "tool_use"
        input = {"chores": []}

    class _FakeResponse:
        content = [_FakeBlock()]

    def fake_create(client, **kwargs):
        calls.update(kwargs)
        return _FakeResponse()

    import app.agent as agent_mod
    original_create, original_client = agent_mod._create_with_retry, agent_mod._client
    agent_mod._create_with_retry = fake_create
    agent_mod._client = lambda: None
    try:
        agent.generate_chore_recommendations({"existing_help": "a cleaner", "rotation_members": ["Emily"]})
    finally:
        agent_mod._create_with_retry, agent_mod._client = original_create, original_client

    prompt = calls["messages"][0]["content"]
    assert "existing_help" in prompt
    assert "outsourced" in prompt
    assert "do not ask again" in prompt.lower()
    assert "outsourced_to" in prompt


def test_the_recommendation_schema_offers_the_fourth_mode_and_a_label():
    """Guard: the model cannot propose what the schema won't take."""
    props = agent._RECOMMEND_CHORES_TOOL["input_schema"]["properties"]["chores"]["items"]["properties"]
    assert "outsourced" in props["mode"]["enum"]
    assert "outsourced_to" in props


def test_a_proposed_outsourced_row_belongs_to_nobody_in_the_house():
    """
    Catch: the normalizer deals owned rows round the rotation. An
    outsourced row must fall out of that — nobody here does it — and keep
    its free-text label, which is not one of rotation_members.
    """
    rows = agent._normalize_chore_recommendations(
        [
            {"name": "Bathrooms", "mode": "outsourced", "outsourced_to": "the cleaner"},
            {"name": "Mowing", "mode": "outsourced", "owner_name": "Emily", "assignee_names": ["Emily"]},
            {"name": "Dishes", "mode": "owned"},
        ],
        ["Emily", "Vineeth"],
    )
    by_name = {r["name"]: r for r in rows}
    assert by_name["Bathrooms"]["outsourced_to"] == "the cleaner"
    assert by_name["Bathrooms"]["owner_name"] == "" and by_name["Bathrooms"]["assignee_names"] == []
    assert by_name["Mowing"]["mode"] == "outsourced" and by_name["Mowing"]["owner_name"] == ""
    # ...and an ordinary row is untouched, label included.
    assert by_name["Dishes"]["owner_name"] in ("Emily", "Vineeth")
    assert by_name["Dishes"]["outsourced_to"] == ""


def test_the_setup_page_asks_in_the_households_words():
    """
    Catch: "Anyone come in to help?", not "Configure outsourced tasks".
    The word "outsourced" is ours — it may sit in a comment explaining
    why, and must never appear in anything a person reads. Checked
    against the visible labels and placeholders rather than the whole
    file, so the comment above them doesn't make this pass or fail by
    accident.
    """
    assert "Anyone come in to help?" in CHORES_SETUP
    assert "Any existing help (cleaning service, etc.)?" not in CHORES_SETUP
    visible = re.findall(r'<label[^>]*>(.*?)</label>|placeholder="([^"]*)"', CHORES_SETUP)
    words = " ".join(a + b for a, b in visible).lower()
    assert "help" in words and "outsourc" not in words


# --- 8. the chat tools -------------------------------------------------------

def test_the_chat_tools_can_say_it():
    """Catch: "the cleaner does the bathrooms now" has to map to something."""
    by_name = {t["name"]: t for t in agent.TOOL_DEFINITIONS}
    add = by_name["add_chore"]["input_schema"]["properties"]
    upd = by_name["update_chore"]["input_schema"]["properties"]
    assert "outsourced" in add["mode"]["enum"] and "outsourced" in upd["mode"]["enum"]
    assert "outsourced_to" in add and "outsourced_to" in upd
    # The phrases a household actually says, so the model maps them
    # without guessing — and the one that hands it back.
    upd_desc = by_name["update_chore"]["description"].lower()
    assert "the cleaner does the bathrooms now" in upd_desc
    assert "ourselves again" in upd_desc
    # And the label is explicitly not a person to create, which is the
    # mistake the owner card had to fix once already.
    assert "never pass it to add_member" in upd["outsourced_to"]["description"].lower()
    assert "never be passed to add_member" in add["outsourced_to"]["description"].lower()


def test_the_tools_are_reachable_from_the_agent():
    """Guard: a tool the package doesn't re-export is one agent.py can't see."""
    assert agent.TOOL_FUNCTIONS["add_chore"] is tools.add_chore
    assert agent.TOOL_FUNCTIONS["update_chore"] is tools.update_chore
    assert tools.is_outsourced is tools.chores.is_outsourced


# --- 9. the one screen that already prints a chore row -----------------------

def _slice(js: str, start: str, end: str) -> str:
    assert start in js, start
    return js.split(start, 1)[1].split(end, 1)[0]


def test_the_now_card_draws_the_tag_and_no_tick():
    """
    Catch: source markers for the Now card (per-household since 2026-09-12;
    the card renders only where the switch is on). The Plan | Chores
    screen is its own card; what this one owes is that the row that DOES
    exist prints honestly.
    """
    # The row builder is choreRowHtml since Plan | Chores landed (2026-09-12)
    # — one builder behind both screens, so this is where the row's shape
    # is decided; renderChores only maps the list through it.
    body = _slice(SHELL_JS, "function choreRowHtml(", "\n  function ")
    assert "choreRowHtml(c)" in _slice(SHELL_JS, "function renderChores(", "\n  function ")
    outsourced_branch = _slice(body, "if (outsourced) {", "return '<div class=\"chore-row' + (isDone")
    assert "chore-tick" not in outsourced_branch, "no tick on a chore nobody here does"
    assert "tick-empty" in outsourced_branch, "a spacer keeps the names lined up with the ticked rows"
    assert "pill pill-neutral" in body[:body.index("if (outsourced) {")], "a quiet label, never apricot (Rule 5)"
    assert "outsourced ? '<span class=\"pill pill-neutral chore-tag\">Not us</span>'" in body
    # Who does it — the same `who` span every row prints, built from the
    # server's who_label above the branch since the rows were re-cut
    # against the shared tick (2026-09-12).
    assert "who_label" in body[:body.index("if (outsourced) {")]
    assert "main +" in outsourced_branch and "who +" in body[:body.index("if (outsourced) {")]
    # The tick handler is wired to the rows that have one.
    assert ".chore-row:not(.is-outsourced)" in _slice(SHELL_JS, "function renderChores(", "\n  function ")


def test_the_count_leaves_out_what_is_not_ours():
    """
    Catch: "1 of 3 done" that includes cleaner day tells a household it is
    behind on something it never had to do. This is the one effort total
    the app has today.
    """
    body = _slice(SHELL_JS, "function renderChores(", "\n  function ")
    counting = _slice(body, "var ours =", "if (!chores.length)")
    assert "!c.outsourced" in counting
    # Both halves of the fraction come off the filtered list — a
    # denominator still reading chores.length would put cleaner day back
    # in, which is the whole bug.
    assert "chores.length" not in counting, "count both halves off `ours`, not the raw list"


def test_the_row_style_uses_tokens_only():
    """Guard: DESIGN_SYSTEM — no raw hex on a new rule."""
    rule = _slice(SHELL_CSS, ".chore-row.is-outsourced", "}")
    assert "#" not in rule
