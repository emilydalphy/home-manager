"""
The frozen clock itself: --today, @pytest.mark.today, and the frozen_today
fixture (tests/conftest.py).

This file exists because the clock is now infrastructure other tests lean on.
If it silently stops pinning, every date-sensitive test goes back to reading
the real calendar and passing by luck — which is the state this whole ticket
was written about, and the failure would be invisible: the tests would still
be green.

It also pins the ONE thing the clock cannot do (SQLite's own `now`), so that
is a written-down fact with a test behind it rather than a surprise somebody
re-discovers at two in the morning.
"""
import datetime
import os

import pytest


ISO_PIN = "2026-09-13"  # a Sunday: the day PLAN_AHEAD and the fold-forward both fire


def test_with_no_pin_a_test_reads_the_real_calendar(request):
    """
    The default has to stay the real date.

    A suite that is always pinned can never notice a fixture whose hard-coded
    date has aged into the past — which is the other half of this hazard, and
    the half that took main red on 2026-09-14. That is also why CI keeps one
    unpinned job.
    """
    if request.config.getoption("--today") or os.environ.get("POMONA_TEST_TODAY"):
        pytest.skip("this run is pinned on purpose; there is no real clock to check")
    import time

    assert datetime.date.today() == datetime.date.fromtimestamp(time.time())


@pytest.mark.today(ISO_PIN)
def test_the_marker_pins_pythons_own_clock():
    assert datetime.date.today() == datetime.date(2026, 9, 13)
    assert datetime.date.today().weekday() == 6, "2026-09-13 is a Sunday"
    assert datetime.datetime.now().date() == datetime.date(2026, 9, 13)


@pytest.mark.today(ISO_PIN)
def test_the_pin_reaches_a_module_that_imported_date_BY_NAME():
    """
    The reason this is freezegun and not a monkeypatch.

    `app/tools/weekly_plan.py` line 9 is `from datetime import date, ...` — it
    holds the class itself, not the module, so patching `weekly_plan.datetime`
    would do nothing and patching `weekly_plan.date` would have to be repeated
    for each of the ~30 modules that import it that way. The one that got
    missed would be the one that mattered. freezegun replaces the class, so an
    import style nobody thought about is pinned anyway.
    """
    from app import tools

    # suggest_planning_period reads date.today() through that by-name import.
    # Sunday is the far side of PLAN_AHEAD_FROM_WEEKDAY = 4, so the suggestion
    # is next week rather than the one containing today.
    period = tools.suggest_planning_period()
    assert period["start_date"] == "2026-09-14", period
    assert period["is_current_period"] is False


@pytest.mark.today("2026-09-13T18:45")
def test_a_pin_can_carry_a_time_of_day():
    """Some cliffs are hours, not days — the shop move for tonight's dinner
    closes at 18:30, and a test about that has to be able to say so."""
    assert datetime.datetime.now().hour == 18
    assert datetime.datetime.now().minute == 45


def test_a_bare_date_lands_mid_morning(frozen_today):
    """09:00, deliberately: inside every window the app reasons about, so an
    ordinary pinned test exercises the ordinary case rather than an edge."""
    frozen_today(ISO_PIN)
    assert datetime.datetime.now().hour == 9


def test_the_fixture_form_pins_and_hands_back_the_date(frozen_today):
    today = frozen_today(ISO_PIN)
    assert today == datetime.date(2026, 9, 13)
    assert datetime.date.today() == today


def test_a_weekday_name_resolves_to_the_next_one(frozen_today):
    """
    What CI's matrix uses. A name never ages the way a fixed date does.
    """
    real_today = datetime.date.today()
    pinned = frozen_today("sunday")
    assert pinned.weekday() == 6
    assert pinned >= real_today, "on or after today, never behind it"
    assert (pinned - real_today).days < 7


def test_a_pin_nobody_can_read_is_a_usage_error(frozen_today):
    """Silently running live because a pin was typo'd is the worst outcome
    here: the run looks pinned, the header says nothing, and a weekday cliff
    goes unmeasured."""
    with pytest.raises(pytest.UsageError):
        frozen_today("next tuesday-ish")


@pytest.mark.today(ISO_PIN)
def test_sqlite_is_pinned_too_or_the_pin_is_worse_than_nothing():
    """
    Both clocks or neither.

    `datetime('now')` runs inside SQLite, below anything freezegun can reach,
    and app/ has 183 of them — every created_at, every updated_at, every "has
    this been touched today?". Pinning Python alone had the app reasoning on
    the pinned date while the rows under it were stamped with the real one, and
    ten tests failed on a ONE-DAY pin for no other reason. tests/sqlite_clock.py
    closes it by handing SQLite the pinned instant where it would have read its
    own clock.

    MEASURED AGAINST PYTHON'S FROZEN CLOCK, NOT AGAINST ISO_PIN, and that is
    what makes this test mean the same thing in every zone. SQLite's `'now'`
    is UTC; a pin is local wall time; east of UTC+9 those are different
    calendar days (see conftest.pinned_utc_now). Asserting that the UTC read
    carried the LOCAL date is what made Kiritimati unusable as a CI zone, and
    it was never the claim this test is named after. The claim is "both clocks
    or neither" — so it is held against Python's frozen clock, to the second.

    That is strictly STRONGER than the date prefix it replaces, which matters
    because the tempting wrong fix for the Kiritimati problem is to make
    `_sqlite_now` hand over local wall time instead of UTC, and the old form
    could not see it: measured, that change takes the three tests the card
    named from 3 failed to 1 under Kiritimati (only node survives, because
    node is pinned from time.time() rather than from _sqlite_now) and from 0
    failed to 0 under Toronto — while making 184 `datetime('now')` call sites
    in app/ stamp rows in a timezone SQLite does not mean. It fails this form
    in every zone, on the hours.
    """
    from app.db import get_conn

    from conftest import pinned_utc_now

    utc = pinned_utc_now()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT date('now'), datetime('now'), date('now', 'localtime'), "
            "datetime('now', '-7 days'), strftime('%Y', 'now')"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == utc.date().isoformat(), "SQLite's UTC day is Python's frozen UTC day"
    # The instant, within a few seconds — tick=True, so the clock moves between
    # the two reads. An unpinned SQLite is months out and a local-for-UTC mix-up
    # is hours; neither hides inside this tolerance.
    read_at = datetime.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
    assert abs(read_at - utc) < datetime.timedelta(seconds=5), (row[1], utc)
    # ...and the day the pin NAMES is the local wall clock's, in every zone.
    assert row[2] == ISO_PIN, "the pin is local wall time, whatever UTC reads"
    assert row[3].startswith(
        (utc - datetime.timedelta(days=7)).date().isoformat()
    ), "modifiers still SQLite's own answer, not a reimplementation"
    assert row[4] == str(utc.year)


@pytest.mark.today(ISO_PIN)
def test_the_shim_leaves_a_real_date_argument_alone():
    """
    It swaps the word 'now' and delegates everything else.

    The danger in a shim like this is that it starts answering questions of its
    own. A call with a real timestamp in it must come back byte for byte the
    way an unpinned SQLite would answer it.
    """
    from app.db import get_conn

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT date('2024-02-29'), datetime('2024-02-29 13:45:00', '+1 day'), "
            "strftime('%w', '2026-09-13'), julianday('2026-09-13') - julianday('2026-09-06')"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "2024-02-29"
    assert row[1] == "2024-03-01 13:45:00"
    assert row[2] == "0", "2026-09-13 is a Sunday, and SQLite counts Sunday as 0"
    assert row[3] == 7.0


def test_with_no_pin_sqlite_is_left_completely_alone(request):
    """The shim must be inert on an ordinary run — it is test-only scaffolding,
    and scaffolding that is always up is scaffolding nobody checks."""
    import sqlite3

    import sqlite_clock

    if request.config.getoption("--today") or os.environ.get("POMONA_TEST_TODAY"):
        pytest.skip("this run is pinned on purpose")
    assert sqlite3.connect is sqlite_clock._real_connect


@pytest.mark.today(ISO_PIN)
def test_node_is_pinned_too_because_it_is_a_whole_other_process():
    """
    The third clock.

    48 files run shell.js's own functions under node, and node hears
    nothing about freezegun — so Python built "tomorrow" from the pinned date
    while the browser code answered with the real one, and nine tests failed on
    a one-day pin looking exactly like real bugs ("show me tomorrow opened
    today"). tests/nodeharness.py hands the subprocess the pinned instant.

    `toISOString()` is UTC, so it is held against Python's frozen UTC clock
    rather than against ISO_PIN — east of UTC+9 the pin's instant is on the
    previous UTC day and the two are honestly different (see
    conftest.pinned_utc_now). node's LOCAL getters are what carry the pinned
    date, in every zone, because node reads TZ from the environment it
    inherits; both halves are asserted, which is more than the old form did.
    """
    import json

    import nodeharness

    from conftest import pinned_utc_now

    utc = pinned_utc_now()
    res = nodeharness.run_node(
        "const d = new Date();\n"
        "console.log(JSON.stringify({"
        "  today: d.toISOString().slice(0, 10),"
        "  fromNow: new Date(Date.now()).toISOString().slice(0, 10),"
        "  epoch: Date.now(),"
        "  local: [d.getFullYear(), d.getMonth() + 1, d.getDate()],"
        "  localHour: d.getHours(),"
        "  real: new Date('2024-02-29T12:00:00Z').toISOString().slice(0, 10),"
        "  parsed: Date.parse('2024-02-29T00:00:00Z'),"
        "}));\n"
    )
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["today"] == utc.date().isoformat(), "node's UTC day is Python's frozen UTC day"
    assert got["fromNow"] == got["today"], "Date.now() is pinned, not just the constructor"
    # The instant itself. node's pin is one fixed value for the whole
    # subprocess, taken when the harness was built, so it lags Python's ticking
    # clock by however long node took to start — seconds, never hours.
    pinned_epoch = utc.replace(tzinfo=datetime.timezone.utc).timestamp()
    assert abs(got["epoch"] / 1000 - pinned_epoch) < 30, (got["epoch"], pinned_epoch)
    # ...and the day the pin NAMES is node's local wall clock, in every zone.
    pin = datetime.date.fromisoformat(ISO_PIN)
    assert got["local"] == [pin.year, pin.month, pin.day], got["local"]
    assert got["localHour"] == 9, "09:00 local, the same hour Python is pinned to"
    # ...and the real Date is still doing all the work underneath.
    assert got["real"] == "2024-02-29", "a real argument is still answered by the real Date"
    assert got["parsed"] == 1709164800000


def test_without_a_pin_node_gets_the_script_byte_for_byte(request):
    """The prelude is empty on an ordinary run — 48 files' harnesses must
    be the exact scripts they have always been."""
    import nodeharness

    if request.config.getoption("--today") or os.environ.get("POMONA_TEST_TODAY"):
        pytest.skip("this run is pinned on purpose")
    assert nodeharness._clock_prelude() == ""


@pytest.mark.today(ISO_PIN)
@pytest.mark.live_clock("checking the escape hatch actually escapes")
def test_live_clock_beats_a_pin_including_the_marker_beside_it():
    """
    The escape hatch, for the one clock nothing pins: the filesystem.

    It has to win over a pin in the same place, or a file written during the
    test still reads as a day old — which is the whole reason
    test_recipe_photo_import.py carries it.

    Measured against the filesystem rather than against `time.time()`: under a
    pin those two are frozen TOGETHER, so comparing them proves nothing and
    would pass whether the hatch worked or not. The gap between Python's clock
    and a file's mtime is the actual quantity `sweep_pending` reads, and it is
    seconds when the hatch works and days when it does not.
    """
    import os
    import tempfile
    import time

    handle, path = tempfile.mkstemp(prefix="pomona-clock-check-")
    os.close(handle)
    try:
        drift = abs(time.time() - os.path.getmtime(path))
    finally:
        os.unlink(path)
    assert drift < 300, (
        f"the clock is {drift / 3600:.1f}h away from a file written this second — "
        "live_clock did not take, and sweep_pending would eat its own input"
    )


def test_the_pinned_clock_fixture_reports_what_the_run_is_on(_pinned_clock, request):
    """How a fixture asks "are we pinned, and to what?" without guessing."""
    pinned = request.config.getoption("--today") or os.environ.get("POMONA_TEST_TODAY")
    if pinned:
        assert _pinned_clock == datetime.date.today()
    else:
        assert _pinned_clock is None


@pytest.mark.today(ISO_PIN)
def test_a_row_written_now_carries_the_pinned_date_from_the_schemas_own_default():
    """
    55 columns in schema.sql are `DEFAULT (datetime('now'))`, so most
    `created_at` values in this app never pass through Python at all. A pin
    that reached only the queries and not the defaults would leave every row
    stamped with the real day while the code that reads it thought otherwise.

    The column is `datetime('now')`, so what lands in it is UTC — held against
    Python's frozen UTC clock rather than against ISO_PIN, which is the local
    day and is a different day east of UTC+9 (see conftest.pinned_utc_now).
    Read back through 'localtime' it is the pinned day again, in every zone.
    """
    from app.db import get_conn

    from conftest import pinned_utc_now

    utc = pinned_utc_now()
    conn = get_conn()
    try:
        conn.execute("INSERT INTO members (household_id, name) VALUES (1, 'Clockcheck')")
        conn.commit()
        written, local_day = conn.execute(
            "SELECT created_at, date(created_at, 'localtime') "
            "FROM members WHERE name = 'Clockcheck'"
        ).fetchone()
    finally:
        conn.close()
    assert written.startswith(utc.date().isoformat()), written
    stamped = datetime.datetime.strptime(written, "%Y-%m-%d %H:%M:%S")
    assert abs(stamped - utc) < datetime.timedelta(seconds=5), (written, utc)
    assert local_day == ISO_PIN, (local_day, written)


def test_a_pin_does_not_flatten_local_and_utc_together(frozen_today):
    """
    A pin is LOCAL wall time, and UTC stays however far from it the machine is.

    freezegun reads a naive datetime as UTC, so freezing "09:00" with no
    tz_offset makes `now()` and `utcnow()` the same instant — local and UTC
    collapse into each other. Invisible on a UTC runner, which is what CI is,
    and quietly wrong on a developer's laptop: SQLite's `datetime('now',
    'localtime')` still converts by the real offset, so it would disagree with
    Python's "now" by exactly that many hours. Measured at 11 under
    TZ=Pacific/Niue before this was fixed.

    Niue is UTC-11 and has no daylight saving, so the arithmetic here is the
    same in every month — and it is far enough from UTC that a flattened pin
    cannot look correct by accident.
    """
    import os
    import time as _time

    from app.db import get_conn

    was = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Niue"
    _time.tzset()
    try:
        frozen_today(ISO_PIN)
        assert datetime.datetime.now().hour == 9, "the pin is the local wall clock"
        assert datetime.datetime.utcnow().hour == 20, "...and UTC is eleven hours ahead of it"
        conn = get_conn()
        try:
            utc, local = conn.execute(
                "SELECT datetime('now'), datetime('now', 'localtime')"
            ).fetchone()
        finally:
            conn.close()
        assert utc.endswith("20:00:00"), utc
        assert local.endswith("09:00:00"), local

        # THE SEAM, asserted so it is written down rather than discovered.
        # freezegun applies tz_offset on top of the tz conversion, so an AWARE
        # now() comes back as the local wall time wearing a UTC label instead
        # of the instant it stands for: 09:00+00:00 here, where the honest
        # answer is 20:00+00:00. Practical impact today is nil — `household_now()`
        # (cooker.py) reads identically on a UTC machine, which is what the
        # container and both CI jobs are — but it is a real hole in a fix whose
        # whole headline is that local and UTC do not collapse.
        #
        # The cheapest fix if it ever bites: force TZ=UTC for the duration of a
        # pin, which makes the offset zero and the seam arithmetically
        # impossible. Not done here because it would make a run under an
        # explicitly-set TZ quietly not be that TZ, which trades a latent
        # surprise for an active one.
        aware = datetime.datetime.now(datetime.timezone.utc)
        assert aware.hour == 9, (
            "freezegun's aware now() still double-counts the offset; if this "
            "starts failing, the seam is closed and this test should assert 20"
        )
        # ...and the naive pair above is the one every reader in app/ uses, so
        # it is the one that has to be right.
        assert datetime.datetime.utcnow().hour == 20
    finally:
        if was is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = was
        _time.tzset()


def test_a_pin_east_of_utc_plus_9_holds_every_clock_on_one_instant(frozen_today):
    """
    The zone band the pin used to be unusable in, written down as arithmetic.

    A pin is 09:00 LOCAL, so the UTC instant behind it is `09:00 - offset` —
    which falls on the PREVIOUS calendar day for every zone east of UTC+9.
    Kiritimati is +14, so a pin of 2026-09-13 freezes at 2026-09-12 19:00 UTC,
    and SQLite's `'now'`, node's `toISOString()` and every
    `DEFAULT (datetime('now'))` column honestly read the 12th while
    `date.today()` reads the 13th. That is not a broken pin: it is what a real
    server in Kiritimati reads at nine in the morning.

    Three tests in this file used to assert the UTC reads carried the LOCAL
    date, which is only true at or west of UTC+9. That made
    `Pacific/Kiritimati` unusable as a CI zone — and Kiritimati is the one zone
    that closes the straddle job's four-hour blind spot, because it is a
    different day from Toronto for eighteen hours out of twenty-four. This
    test is what stops that assertion coming back, in either direction: it
    fails if the UTC clocks are made to answer local, and it fails if the
    default pin hour is moved (which cannot fix it anyway — no hour makes the
    local and UTC dates agree across -12..+14; see conftest.pinned_utc_now).

    Kiritimati has no daylight saving, so this arithmetic is the same in every
    month.
    """
    import json
    import os
    import time as _time

    import nodeharness
    from app.db import get_conn

    was = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"
    _time.tzset()
    try:
        frozen_today(ISO_PIN)
        # Python: local is the pinned day at 09:00; UTC is fourteen hours behind.
        assert datetime.date.today() == datetime.date(2026, 9, 13)
        assert datetime.datetime.now().hour == 9
        assert datetime.datetime.utcnow().date() == datetime.date(2026, 9, 12)
        assert datetime.datetime.utcnow().hour == 19

        # SQLite: 'now' is UTC and says so; 'localtime' puts it back on the pin.
        conn = get_conn()
        try:
            conn.execute("INSERT INTO members (household_id, name) VALUES (1, 'Kiribati')")
            conn.commit()
            utc_day, local_day, written = conn.execute(
                "SELECT date('now'), date('now', 'localtime'), created_at "
                "FROM members WHERE name = 'Kiribati'"
            ).fetchone()
        finally:
            conn.close()
        assert utc_day == "2026-09-12", utc_day
        assert local_day == ISO_PIN, local_day
        assert written.startswith("2026-09-12 19:"), written

        # node: same instant, same split.
        res = nodeharness.run_node(
            "const d = new Date();\n"
            "console.log(JSON.stringify({"
            "  utc: d.toISOString().slice(0, 10),"
            "  local: [d.getFullYear(), d.getMonth() + 1, d.getDate()],"
            "  localHour: d.getHours(),"
            "}));\n"
        )
        assert res.returncode == 0, res.stderr
        got = json.loads(res.stdout)
        assert got["utc"] == "2026-09-12", got
        assert got["local"] == [2026, 9, 13], got
        assert got["localHour"] == 9, got

        # THE SEAM, one zone further out than test_a_pin_does_not_flatten_local_
        # and_utc_together measures it, because here it costs a whole DAY rather
        # than eleven hours: freezegun applies tz_offset on top of an already
        # tz-aware conversion, so the AWARE now() hands back the local wall time
        # wearing a UTC label. Honest answer is 2026-09-12 19:00+00:00.
        # Deliberately not fixed here — the only fixes are patching freezegun or
        # forcing TZ=UTC for the duration of a pin, and the second would make a
        # run under an explicitly-set TZ quietly not be that TZ, which is the
        # whole point of the straddle job. It is invisible today because
        # conftest.household_today() and cooker.household_now() both read the
        # aware form, so they are wrong together and agree.
        aware = datetime.datetime.now(datetime.timezone.utc)
        assert aware.date() == datetime.date(2026, 9, 13) and aware.hour == 9, (
            "freezegun's aware now() still double-counts the offset; if this "
            "starts failing, the seam is closed and this should assert "
            "2026-09-12 19:00+00:00"
        )
        # ...and the naive pair above is the one every guard in this file and
        # every reader in app/ measures against, so it is the one that matters.
        assert datetime.datetime.utcnow().date() == datetime.date(2026, 9, 12)
    finally:
        if was is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = was
        _time.tzset()
