"""
Shared test setup.

Every test runs against a throwaway SQLite file, never the real database.
app/db.py reads DB_PATH at import time, so the env var has to be set before
anything under app/ is imported — hence the os.environ writes at module
scope here, above the imports that depend on them.
"""
import os
import tempfile
import time

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="home-manager-tests-"), "test.db")
os.environ["DB_PATH"] = _TMP_DB
os.environ["HOME_MANAGER_PASSWORD"] = "test-password"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
# The daily backup loop is real behaviour, not test behaviour — tests that
# want it exercise app.backup directly (see test_backup.py).
os.environ["DISABLE_BACKUPS"] = "1"
# Same for the morning-text loop (app/tools/digest.py) — tests that want it
# call run_morning_texts_once directly with a stubbed sender.
os.environ["DISABLE_MORNING_TEXT"] = "1"

import ast as _ast  # noqa: E402  (agent_function_source, below)
import contextlib  # noqa: E402
import datetime as _dt  # noqa: E402
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import freezegun  # noqa: E402
from freezegun import freeze_time  # noqa: E402

import nodeharness  # noqa: E402  (tests/ is on pythonpath — see pytest.ini)
import sqlite_clock  # noqa: E402

from app import ratelimit  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app import tools as _tools  # noqa: E402  (household_id, for household_today below)
from app.tools import cooker as _cooker  # noqa: E402  (household_today, below)

# Tables are wiped between tests rather than the file being recreated, so
# the schema and migrations run once and each test still starts clean.
_TABLES = [
    # week_intake comes after weekly_plans: a plan references the intake it
    # was generated from, so the referencing rows go first. slot_needs
    # references both meal_plan_entries and away_stretches, so it goes
    # before both of those.
    # slot_attendance references away_stretches and members, so it is wiped
    # before both.
    "slot_attendance", "slot_needs", "away_stretches", "household_rhythm",
    "meal_plan_grocery_links", "prep_tasks", "meal_plan_entries", "weekly_plans", "week_intake",
    "grocery_substitutions", "grocery_items", "inventory_items", "member_recipe_feedback", "recipe_notes", "recipe_photos", "recipes",
    "chore_instances", "chores", "chores_profile", "attention_items",
    "member_notes", "member_share_links", "share_links", "facts",
    "preference_events", "notification_dismissals", "item_store_preferences",
    "shopping_trips", "stores", "meal_preferences", "pets", "members",
    "chat_turns", "api_calls", "error_events", "plan_quality_events", "feedback_reports",
    "calendar_feeds", "staple_events", "staples", "morning_text_sends",
    "holiday_answers", "held_things",
]


@pytest.fixture(scope="session", autouse=True)
def _database():
    init_db()


@pytest.fixture(autouse=True)
def clean_state():
    ratelimit.reset()
    conn = get_conn()
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in _TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass  # a table this build doesn't have yet is not a test failure
    # Households themselves are wiped too, except the seeded id=1 that
    # schema.sql creates and every existing test implicitly runs against.
    # Without this, a test that creates a second household leaks it into
    # every test that runs after it in the session — and an isolation test
    # that silently shares state with its neighbours is worse than none,
    # because it still passes.
    try:
        conn.execute("DELETE FROM household_credentials WHERE household_id != 1")
        conn.execute("DELETE FROM households WHERE id != 1")
        # The morning-text settings live on the household row, which survives
        # the wipe above — put them back to their defaults so one test's
        # Vancouver clock doesn't become the next test's.
        # chores_enabled too: it is off by default for every household,
        # and a test that switches it on for household 1 must not hand the
        # next test a house that can see chores.
        conn.execute(
            "UPDATE households SET timezone = 'America/Toronto', morning_text_time = '07:00', "
            "country = 'CA', province = 'ON', chores_enabled = 0 WHERE id = 1"
        )
    except Exception:
        pass
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def signed_in(client):
    res = client.post(
        "/login",
        data={"password": "test-password", "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303, "sign-in should redirect on success"
    return client


# ---------------------------------------------------------------------------
# The clock (2026-09-14)
# ---------------------------------------------------------------------------
# The app's behaviour genuinely changes with the weekday — `_first_plan_window`
# folds a Sunday sign-up forward, `PLAN_AHEAD_FROM_WEEKDAY = 4` makes Friday
# onward suggest next week, `retire_expired_drafts` sweeps a draft the morning
# after its last day, and the holiday window opens three days out. A test that
# reads the real clock sits on whichever side of those cliffs the calendar puts
# it, so a green suite means something different on a Tuesday than on a Sunday.
# Main has been red twice for behaviour that was deliberate.
#
# So: `pytest --today=2026-09-13` pins the whole run, and `@pytest.mark.today(...)`
# (or the `frozen_today` fixture) pins one test. Both go through freezegun
# rather than a hand-rolled monkeypatch, because half this codebase writes
# `from datetime import date` — a monkeypatch would have to find and patch the
# `date` name in every one of ~30 modules, and the one it missed would be the
# one that mattered. freezegun replaces the class itself, so an import style it
# has never seen still gets the pinned date.
#
# SQLite's clock is pinned with it — `datetime('now')` runs in C, below
# anything freezegun can reach, and app/ has 183 of them. Pinning Python alone
# leaves the app reasoning on one date and stamping every `created_at` with
# another, which cost ten false failures on a ONE-DAY pin before this was
# closed. tests/sqlite_clock.py does that half; read its docstring before
# changing either. node is the third clock — the 48 files that run
# shell.js's own functions do it in a subprocess, which hears nothing about any
# of this until tests/nodeharness.py hands it the pinned instant. The fourth is
# the filesystem, which is not pinned at all; see @pytest.mark.live_clock.
#
# A pinned run also starts on an exact second boundary, which is worth
# knowing when a failure will not reproduce: a short test that has to straddle
# one behaves the same way every time under a pin and is a coin flip without,
# so `--today` is a sharper instrument for a sub-second timing bug than an
# unpinned rerun — and a bug that only shows up in one of the two is probably
# about that boundary rather than about the date.
#
# And one trap that is not a clock at all: freezegun's default ignore list
# hands back the REAL time to any caller with a "threading" frame five levels
# up, which is every sync route in this app. See the configure() call below
# before adding anything to that list.
_DEFAULT_FREEZE_TIME = "09:00:00"


def pytest_addoption(parser):
    parser.addoption(
        "--today",
        action="store",
        default=None,
        metavar="YYYY-MM-DD[THH:MM[:SS]]",
        help=(
            "Pin the clock for the whole run, e.g. --today=2026-09-13, or a "
            "weekday name (--today=sunday) for the next one of those. "
            "Time of day defaults to " + _DEFAULT_FREEZE_TIME + " local. "
            "POMONA_TEST_TODAY sets the same thing from the environment."
        ),
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "today(when): pin this test's clock, e.g. @pytest.mark.today('2026-09-13'). "
        "Beats --today for that test.",
    )
    config.addinivalue_line(
        "markers",
        "live_clock(why): run this test on the real clock even under --today. "
        "For a test measuring against a clock we do not pin — say which.",
    )
    raw = config.getoption("--today") or os.environ.get("POMONA_TEST_TODAY")
    if not raw:
        return
    # The freeze starts HERE, not in a session fixture, and that is the whole
    # difference between a pin that works and one that quietly does nothing.
    # Dozens of test modules open with `TODAY = datetime.date.today()` at
    # module scope — collection imports them, and collection runs before any
    # fixture. A session fixture therefore pinned the app while every one of
    # those constants still held the real date, and the two disagreed: 28 of
    # test_moves.py's assertions failed on a pinned run for no reason but
    # that. pytest_configure runs before collection, so the constants are
    # pinned too. A typo'd pin is a usage error here for the same reason —
    # at startup, once, rather than 4,600 identical errors.
    #
    # _REAL_EPOCH_AT_PIN is captured BEFORE the freeze and is the only way back
    # to the real clock once it is on: tick=True means the frozen clock
    # advances at the real one's rate, so real-now is this epoch plus however
    # far the frozen one has moved. @pytest.mark.live_clock needs that.
    global _REAL_EPOCH_AT_PIN, _FROZEN_EPOCH_AT_PIN
    pinned = _parse_pin(raw)
    at, offset = _freeze_args(raw)
    _REAL_EPOCH_AT_PIN = time.time()
    config._pomona_freezer = freeze_time(at, tz_offset=offset, tick=True)
    config._pomona_freezer.start()
    _FROZEN_EPOCH_AT_PIN = time.time()
    # Said out loud, and with print() rather than a pytest report header
    # because CI runs `pytest -q`, which suppresses the header — and a
    # weekday-name pin resolves to a different date every week, so a CI failure
    # has to be reproducible from the log rather than from the command.
    print(f"clock: pinned to {pinned:%Y-%m-%d %H:%M} ({pinned:%A}) via {raw!r}")
    # And SQLite's clock with it — see tests/sqlite_clock.py. Python and the
    # database have to agree about what day it is or the pin is worse than no
    # pin: the app reasons on one date and stamps rows with another.
    # utcnow() rather than now() because SQLite's 'now' is UTC.
    sqlite_clock.install(_sqlite_now)
    # ...and node's, for the 48 files that run shell.js's own functions in a
    # subprocess. See tests/nodeharness.py.
    nodeharness.pin_clock(time.time)


def pytest_unconfigure(config):
    freezer = getattr(config, "_pomona_freezer", None)
    if freezer is not None:
        nodeharness.unpin_clock()
        sqlite_clock.uninstall()
        freezer.stop()
        config._pomona_freezer = None


_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _parse_pin(raw):
    """
    Read a --today / marker value into the instant to freeze at.

    Two forms. An ISO date is exact and is what a test's own marker should use,
    because a test that reproduces a bug wants the day the bug was on. A
    weekday NAME ("sunday") resolves to the next such day on or after the real
    today, and is what CI's matrix uses — a fixed pin in CI would age the same
    way the fixtures it is meant to protect do, so in three months every new
    test written against a plausible date would fail the pinned jobs and the
    matrix would be turned off. A name never ages.

    A bare date gets a mid-morning time rather than midnight: 09:00 is inside
    every window the app reasons about (past the 07:00 morning text, well short
    of the 18:30 after which tonight's shop move closes), so a pinned run
    exercises the ordinary case rather than an edge of the day. Pass a time
    explicitly to test an edge on purpose.

    WHICH CLOCK THAT IS TRUE OF, said plainly, because for a while it was only
    true of the wrong one. The pin is the PROCESS's local wall time (see
    _freeze_args). Every screen in this app reads the HOUSEHOLD's clock, and
    those two coincide only when the process runs in the household's zone —
    which is why CI's `clock` matrix sets TZ: America/Toronto, the households'
    own default, and why that line is load-bearing rather than belt-and-braces.
    Run a pin from a machine on another zone and the household is the pinned
    hour converted into its zone, which is the honest answer and need not be
    inside those windows: a 09:00 pin on a Tokyo laptop puts a Toronto
    household at 20:00 the evening before. `household_pin()` below is how a
    test names the HOUSEHOLD's hour and gets it in any zone.

    AND SO A WEEKDAY PIN STOPS NAMING THE HOUSEHOLD'S WEEKDAY OFF-ZONE, which
    is the real cost of closing the aware seam on 2026-09-17 and is worth
    knowing before debugging a weekday cliff on a laptop. Until then the seam
    made `household_today()` equal the pinned date in EVERY zone — the bug was
    zone-independent, which is most of why it survived — and now the
    household's date moves with the process's zone, as it does in production.
    Measured: `TZ=Asia/Tokyo POMONA_TEST_TODAY=sunday` puts the process on
    Sunday and the household on SATURDAY 20:00. CI is unaffected (`clock` pins
    TZ: America/Toronto, and test_pin_hour_household_clock.py guards that
    line), but `pytest --today=sunday` from anywhere else no longer exercises
    Sunday from the app's point of view. Use `household_pin(..., on=...)` when
    the weekday is the thing under test.
    """
    if isinstance(raw, _dt.datetime):
        return raw
    raw = str(raw).strip()
    if raw.lower() in _WEEKDAY_NAMES:
        wanted = _WEEKDAY_NAMES.index(raw.lower())
        today = _dt.date.today()
        day = today + _dt.timedelta(days=(wanted - today.weekday()) % 7)
        raw = day.isoformat()
    raw = raw.replace(" ", "T")
    if "T" not in raw:
        raw = raw + "T" + _DEFAULT_FREEZE_TIME
    try:
        return _dt.datetime.fromisoformat(raw)
    except ValueError as exc:  # a typo'd pin must say so, not silently run live
        raise pytest.UsageError(
            f"--today/@pytest.mark.today: {raw!r} is not a date I can read ({exc}). "
            f"Give an ISO date, or one of {', '.join(_WEEKDAY_NAMES)}."
        )


@pytest.fixture(scope="session")
def _pinned_clock(request):
    """
    The date this run is pinned to, or None if it is running live.

    The freeze itself is already on by the time any fixture runs — see
    pytest_configure. This only reports it, so a fixture that wants to build
    dates around "today" can say so rather than guessing.

    On tick=True: time still moves forward from the pinned instant, because
    the session cookie's age, the rate limiter and the "is this a new
    sitting?" gap are all real elapsed-time arithmetic and a clock that never
    advances makes them answer questions nobody asked. What is pinned is the
    DATE, which is the thing the app branches on. 09:00 gives a run about
    fifteen hours before it could roll into the next day.
    """
    if getattr(request.config, "_pomona_freezer", None) is None:
        return None
    return _dt.date.today()


def _sqlite_now():
    """The pinned instant as SQLite's own 'now' means it: UTC."""
    return _dt.datetime.utcnow().isoformat(sep=" ", timespec="milliseconds")


def pinned_utc_now() -> _dt.datetime:
    """
    The instant every clock in this run is holding, as UTC.

    A pin is LOCAL wall time — `--today=2026-09-13` freezes at 09:00 on the
    machine's own clock (see `_freeze_args`). But three of the clocks a test
    has to check answer in UTC, because UTC is what they mean: SQLite's
    `'now'`, node's `toISOString()`, and every `DEFAULT (datetime('now'))`
    column in schema.sql. This is the value to hold those against — never the
    pinned date itself.

    THE TWO ARE NOT THE SAME CALENDAR DAY EVERYWHERE, and that is the whole
    reason this function exists rather than a literal. The UTC instant behind
    09:00 local is `09:00 - offset`, which falls on the PREVIOUS day for any
    zone east of UTC+9 — Kiritimati (+14) freezes at 19:00 the day before, so
    `date('now')` is honestly the 12th while `date.today()` is the 13th. That
    is not a broken pin; it is what a real server in that zone reads at nine
    in the morning, and it is arithmetically unavoidable. For the local and
    UTC dates to coincide the pinned hour H needs `0 <= H - offset < 24` at
    every offset a real zone has: H at or after 14:00 to survive UTC+14, and
    before 12:00 to survive UTC-12. There is no such H, and reading the pin as
    UTC instead only mirrors the problem (H at or after 12:00, and before
    10:00). So moving 09:00 cannot fix this, and would cost the one hour that
    is inside every window the app reasons about.

    `utcnow()`, deliberately, NOT `now(timezone.utc)`. The two agree now
    (the freezegun.configure block below closed the seam that once had the
    aware form answering local wall time wearing a UTC label), but the naive
    pair is the one `_sqlite_now` and node's pin are built from, so it is
    the one a guard has to measure against.
    """
    return _dt.datetime.utcnow()


# freezegun's default ignore list is a call-stack sniffer: `_should_use_real_time`
# walks five frames up and, if any of them belongs to a module named in that
# list, hands back the REAL clock instead of the pinned one. "threading" is in
# it by default, and this app's sync routes run in Starlette's threadpool — so
# `time.time()` inside a request returned the real epoch while
# `date.today()` right beside it returned the pinned one. The session cookie is
# signed with `time.time()` and checked against it, so a pin far enough from
# today put every signed-in test past COOKIE_MAX_AGE: 385 failures at a
# five-month pin, all of them 401s, none of them a bug. Emptying the list makes
# the pin mean the same thing everywhere — and `_should_use_real_time` then
# returns on its first line rather than inspecting a stack per call, so it is
# cheaper too. Safe because tick=True: the list exists so a thread waiting on a
# timeout is not frozen solid, and here the clock still advances.
freezegun.configure(default_ignore_list=[])


# ...and one more of freezegun's own, because the pinned clock had TWO answers
# for what UTC instant it was standing on. `FakeDatetime.now(tz)` computes
# `tz.fromutc(frozen)` — already the right instant in the right zone — and then
# adds `tz_offset` on top of it, which is the process's offset and has no
# business in a conversion that has already happened. So an AWARE
# `datetime.now(timezone.utc)` came back as the pin's LOCAL wall time wearing a
# UTC label, while `datetime.utcnow()` and `time.time()` — the same clock's own
# answers, and the ones SQLite's 'now' is built from — came back as the real
# instant. Measured at TZ=America/Toronto under `--today=...T09:00`:
# utcnow() and time.time() both 13:00 UTC, now(timezone.utc) 09:00+00:00.
# Four hours apart, from one clock, with nothing saying so.
#
# THE READERS THAT CARED, named exactly — an earlier draft of this comment got
# the list wrong in both directions.
#   cooker.household_now()  `datetime.now(timezone.utc).astimezone(zone)`
#   digest.py:574           the same, for the morning text's sending loop
#   tonight.py:107          `datetime.now(zone)` — a different call SHAPE, the
#                           same patched branch, the same correction
#   calendar_feed.py:804/856/902  `datetime.now(timezone.utc)` for the feed's
#                           freshness arithmetic. Write and read go through one
#                           call, so it was internally consistent either way;
#                           what changes is that the instant it records now
#                           matches utcnow(), SQLite's 'now' and time.time().
# NOT holidays.py, whose live clock is `date.today()` (:447, :924) with :968
# converting a stored stamp — it never reads the aware form at all.
#
# Those readers were getting `pin_hour + the household's UTC offset`: a Toronto
# household sat at 05:00 under a 09:00 pin, so all four pinned `clock` CI jobs
# exercised a household that had not yet had its 07:00 morning text and could
# never reach the 18:30 after which tonight's shop move closes — the evening
# branch of every reader moved onto that clock in the week of 2026-09-14 was
# unreachable under a pin. Dropping the offset from the aware branch closes
# that: now(tz) agrees with utcnow() and time.time(), and a household in the
# process's own zone reads the hour the pin names.
#
# WHAT IT COSTS, because it is not free: the household's DATE used to equal the
# pinned date in EVERY process zone — the bug was zone-independent, which is
# most of why it survived — and now it moves with the process's zone, honestly.
# So a WEEKDAY pin stops naming the household's weekday off-zone. See
# _parse_pin.
#
# Deliberately NOT the fix the 2026-09-14 entry proposed (force TZ=UTC for the
# duration of a pin). That one makes the offset zero, so the three answers
# agree — at 09:00 UTC, where a Toronto household is honestly at 05:00. It
# closes the CONTRADICTION and leaves the coverage hole exactly where it was,
# and it would make a run under an explicitly-set TZ quietly not be that TZ.
#
# A no-op when nothing is frozen (`_time_to_freeze()` is None), so this is
# scoped to pins however they arrive — --today, the marker, frozen_today.
_fg_unpatched_now = freezegun.api.FakeDatetime.now.__func__


def _fg_aware_now(cls, tz=None):
    """
    freezegun's own `now`, minus the tz_offset it must not add twice.

    The `frozen is None` arm is defence in depth and nothing pins it: freezegun
    only installs FakeDatetime into `datetime` while a freeze is up, so with
    nothing frozen this method is not on the call path. It mirrors freezegun's
    own fallback so a change to that rule can never land us on a None.
    """
    frozen = cls._time_to_freeze()
    if tz is None or frozen is None:
        return _fg_unpatched_now(cls, tz)
    return freezegun.api.datetime_to_fakedatetime(tz.fromutc(frozen.replace(tzinfo=tz)))


freezegun.api.FakeDatetime.now = classmethod(_fg_aware_now)


def _freeze_args(when):
    """
    Turn a pin into the (instant, tz_offset) freezegun needs to behave like a
    real clock in THIS machine's timezone.

    freezegun reads a naive datetime as UTC and adds `tz_offset` to produce
    local time, so freezing "09:00" with no offset makes `datetime.now()` and
    `datetime.utcnow()` the same instant — local and UTC collapse. That is
    fine on a UTC runner and quietly wrong anywhere else: `datetime('now',
    'localtime')` in SQLite still converts by the real offset, so it would
    disagree with Python's "now" by exactly that many hours. Measured under
    TZ=Pacific/Niue: a pin of 09:00 gave Python 09:00 and SQLite localtime
    22:00 the previous day.
    
    A pin is therefore read as LOCAL wall time — which is what somebody typing
    --today=2026-09-13 means — and frozen at the UTC instant behind it, with
    the offset handed to freezegun. `.astimezone()` on a naive datetime reads
    it as local and is DST-aware for that particular date, so a pin either side
    of a clock change gets the offset that actually applied.

    That offset used to leak into the AWARE clock as well, so `now(timezone.utc)`
    disagreed with `utcnow()` by exactly this much and every household clock in
    app/ read the wrong one. Fixed above, at freezegun.configure — read that
    before changing anything here, because the two are one mechanism.
    """
    local = _parse_pin(when)
    offset = local.astimezone().utcoffset() or _dt.timedelta(0)
    return local - offset, offset


@contextlib.contextmanager
def _pin(when):
    """
    Hold every clock we can reach at one instant.

    All three, always, together — Python's, SQLite's and node's. A
    half-pinned world is worse than an unpinned one, because the app goes on
    reasoning about "today" while the rows it writes are stamped with a
    different day and the browser code answers with a third, and nothing says
    so.
    """
    at, offset = _freeze_args(when)
    freezer = freeze_time(at, tz_offset=offset, tick=True)
    freezer.start()
    sqlite_clock.install(_sqlite_now)
    nodeharness.pin_clock(time.time)
    try:
        yield _dt.date.today()
    finally:
        nodeharness.unpin_clock()
        sqlite_clock.uninstall()
        freezer.stop()


_REAL_EPOCH_AT_PIN = None
_FROZEN_EPOCH_AT_PIN = None


def _real_now():
    """
    The wall clock, reachable from inside a freeze.

    tick=True means the frozen clock runs at the real clock's rate, so the gap
    between them is whatever it was when the freeze started. Nothing else can
    answer this once freezegun is on — time.time() is the frozen one.
    """
    if _REAL_EPOCH_AT_PIN is None:
        return _dt.datetime.now()
    return _dt.datetime.fromtimestamp(_REAL_EPOCH_AT_PIN + (time.time() - _FROZEN_EPOCH_AT_PIN))


@pytest.fixture(autouse=True)
def _marked_clock(request):
    """
    `@pytest.mark.today('2026-09-13')` — one test, pinned, session pin or not.
    `@pytest.mark.live_clock('why')` — one test, back on the real clock.

    live_clock is the escape hatch, and it exists because there is a fourth
    clock nobody pins: the FILESYSTEM. `recipe_photos.sweep_pending` compares
    `time.time()` against `os.path.getmtime`, and a pinned run therefore reads
    a file written seconds ago as a day old and sweeps it — three tests in
    test_recipe_photo_import.py failed that way on a one-day pin, none of them
    about anything a household would notice.

    Shimming `os.path.getmtime` (and `os.utime` with it, or the arithmetic
    double-counts) was the other option and was not taken: `os.path` is reached
    by pytest's own machinery and by importlib, so it is the riskiest thing in
    this process to patch, and the payoff is three tests that have nothing to
    do with what day it is. A named exemption that says why is the smaller
    claim — and those tests still run, in full, in CI's unpinned job.
    """
    if request.node.get_closest_marker("live_clock") is not None:
        if getattr(request.config, "_pomona_freezer", None) is None:
            yield None  # already live
            return
        with _pin(_real_now()) as today:
            yield today
        return
    marker = request.node.get_closest_marker("today")
    if marker is None:
        yield None
        return
    with _pin(marker.args[0]) as today:
        yield today


@pytest.fixture
def frozen_today():
    """
    Pin the clock from inside a test: `today = frozen_today("2026-09-13")`.

    The marker is the normal way; this is for a test that has to do setup
    before the pin lands, or that wants the date back as a value. Returns the
    pinned `datetime.date`, and unfreezes when the test ends.
    """
    with contextlib.ExitStack() as stack:

        def _freeze(when):
            return stack.enter_context(_pin(when))

        yield _freeze


# ---------------------------------------------------------------------------
# The household's clock (2026-09-15) — READ THIS BEFORE WRITING A DATED TEST
# ---------------------------------------------------------------------------
# Use `household_today()`, not `datetime.date.today()`, for any date a SCREEN
# will be asked about.
#
#     from conftest import household_today
#     TODAY = household_today()                       # module scope is fine
#     tomorrow = household_today() + timedelta(days=1)
#
# Why there are two answers at all. Since overnight/moves-household-clock
# (2026-09-14) the screens run on the HOUSEHOLD's clock — moves.py's timeline,
# get_cooker_view's staleness guard, weekly_plan._household_today, the morning
# text. `tests/conftest.py` seeds that household at America/Toronto (the
# column default, restored before every test by clean_state), while the test
# PROCESS runs in whatever TZ it was given: UTC on CI, and anything at all on
# a laptop. So `date.today()` is the SERVER's day, and for some hours of every
# day it is not the day the app is talking about — four hours under UTC
# (00:00–03:59, the Toronto evening), seven under Pacific/Niue, eighteen under
# Pacific/Kiritimati.
#
# A test that seeds a date with `date.today()` and then asks a screen about
# "today" is therefore not asserting what it looks like it is asserting: it is
# asserting that the server's day and the household's day are the same day.
# That was true of the code until 2026-09-14 and is not true of it now, and it
# is why CI had to be pinned to TZ=America/Toronto — i.e. to the one
# configuration in which this entire class of bug cannot occur. Production is
# a UTC container with a Toronto household. Seeding off the household closes
# that gap so the pin stops being load-bearing.
#
# THIS IS NOT A SECOND CLOCK MECHANISM. It composes with --today /
# @pytest.mark.today / frozen_today rather than competing with them: under a
# pin every clock this function reads is already frozen, so it returns the
# household's date AT THE PINNED INSTANT. Nothing here starts or stops a
# freeze.
#
# It is also NOT the thing to reach for when a test is ABOUT the two clocks
# disagreeing. tests/test_moves_household_clock.py and
# tests/test_cooker_household_clock.py set households.timezone themselves and
# freeze cooker.datetime at a chosen UTC instant so the two genuinely differ
# inside one test — that is the point of those files and they must keep doing
# it. This helper is for the other 100 files, which mean "today" plainly.
def household_today() -> _dt.date:
    """
    Today where the seeded household lives — the date the app's screens will
    use, whatever timezone this process happens to be running in.

    Computed the same way `cooker.household_now` computes it (UTC now, read
    into households.timezone), so it agrees with the app by construction
    rather than by two pieces of arithmetic being kept in step. It is
    deliberately NOT a call to `cooker.household_today()`: that one swallows
    any failure and falls back to `date.today()`, which is the server's date
    — exactly the answer this helper exists to stop a test from using. At
    module scope, which is where a dozen `TODAY = ...` constants live, the
    throwaway database has not been created yet (init_db runs in a session
    fixture, after collection), so that fallback would fire on every one of
    them and the helper would silently do nothing.
    """
    name = _seeded_timezone() or _cooker.DEFAULT_TIMEZONE
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(_cooker.DEFAULT_TIMEZONE)
    return _dt.datetime.now(_dt.timezone.utc).astimezone(zone).date()


def household_date(offset_days: int = 0) -> str:
    """`household_today()` plus N days, as the ISO string the API speaks."""
    return (household_today() + _dt.timedelta(days=offset_days)).isoformat()


def household_pin(hour: int, minute: int = 0, on: _dt.date | None = None) -> _dt.datetime:
    """
    The instant to freeze at so the HOUSEHOLD's clock reads `hour:minute`.

        frozen_today(household_pin(10))          # the household's mid-morning
        frozen_today(household_pin(18, 45))      # ...and after the shop cutoff

    A pin is the PROCESS's local wall time, always (see _freeze_args), so a
    test that names an hour and MEANS the household's — anything about the
    07:00 morning text, the 18:30 after which tonight's shop move closes, a
    cook's start-by time — gets that hour only while the two zones happen to
    coincide. They do on CI's pinned jobs and they do not on a laptop in
    another zone, which is the quietest way for such a test to be green for
    the wrong reason. This converts, so it is right in either.

    Returns a naive process-local datetime, which is what `frozen_today` and
    `@pytest.mark.today` take. Reads the clock as it stands, so call it
    OUTSIDE the pin it is computing — which is the natural way round anyway.

    ONE EDGE, named rather than handled: the round trip is household-aware ->
    process-naive, and `.astimezone()` resolves an ambiguous wall time at
    fold=0. So during the process zone's repeated hour — one hour, twice a
    year, and only when the household's hour maps into it — this picks the
    first of the two. Nothing in this suite is about that hour; if something
    ever is, it wants an explicit fold rather than this helper.
    """
    day = on or household_today()
    # The same three lines as household_today() above, deliberately not shared:
    # extracting them is a refactor of live test infrastructure for two
    # callers. If a third appears, share it — a silent divergence between two
    # copies of "which zone is this household in" is exactly the class of bug
    # this file exists to stop.
    name = _seeded_timezone() or _cooker.DEFAULT_TIMEZONE
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(_cooker.DEFAULT_TIMEZONE)
    at_home = _dt.datetime.combine(day, _dt.time(hour, minute), tzinfo=zone)
    return at_home.astimezone().replace(tzinfo=None)


def pin_household_clock(monkeypatch) -> None:
    """
    Make a pin of `weekly_plan.date` mean ONE date, for the household's
    clock as well as the server's.

    For the file that pins a weekday by hand — `monkeypatch.setattr(
    weekly_plan, "date", _FixedToday)`, which four files do — that seam is
    only half the clock now. `weekly_plan._household_today()` is written as
    a SHIFT: `date.today()` (the pin) plus however many whole days
    `cooker.household_now()` is from the server's real `datetime.now()`.
    Under a straddling timezone that shift is a day, so a test pinned to
    "Thursday" asks the app about Wednesday or Friday and then asserts the
    Thursday answer. This sets the shift to zero.

    It is a pin of the HOUSEHOLD's clock onto the process's, which is the
    right way round for these files: each one is about a WEEKDAY RULE (the
    Friday plan-ahead shift, a draft expiring, the span a link offers), and
    picks one date to say it with. None of them is about the two clocks
    disagreeing — the files that ARE
    (test_moves_household_clock, test_cooker_household_clock,
    test_weekly_plan_household_clock, test_weekly_plan_last_clock_reads)
    set households.timezone and freeze cooker.datetime themselves, and must
    never call this.

    A half-pinned test fails wrongly AND passes wrongly, which is why this
    is shared rather than four local copies. Measured on
    `overnight/weekly-plan-last-clock-reads` at TZ=Pacific/Niue: fourteen
    tests red for this reason and no app defect among them — and one of
    them, test_stale_draft_front_page's `test_thursday_still_offers_this_
    week`, was GREEN before that branch for the wrong reason, because its
    fixture described the server's clock and the server's clock was the one
    the app read. A fifth file wanting a hand pin wants this line too.
    """
    monkeypatch.setattr(_cooker, "household_now", lambda *a, **kw: _dt.datetime.now())


def _seeded_timezone():
    """
    households.timezone for the household under test, or None if it cannot be
    read yet.

    None rather than a raise, and the caller falls back to the column's own
    default: at collection time there is no database, and a test that changed
    the zone on purpose is inside a test, where there is. Both answers are the
    right one for their moment.
    """
    try:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT timezone FROM households WHERE id = ?", (_tools.household_id(),)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    return (row["timezone"] if row else None) or None


# Deliberately NOT also a fixture. A fixture named `today` would be shadowed
# by `@pytest.mark.parametrize("today", ...)` in test_stale_draft_front_page.py
# — which pytest allows, and which would leave two things called `today` in one
# suite meaning different answers. A plain function reads the same at module
# scope and inside a test, and composes with `frozen_today` either way.


# ---------------------------------------------------------------------------
# Prompt text (2026-09-16) — READ THIS BEFORE ASSERTING ON app/agent.py
# ---------------------------------------------------------------------------
# Use `prompt_literals(fn)`, never `inspect.getsource(fn)`, for any assertion
# about what a prompt SAYS.
#
#     from conftest import prompt_literals
#     assert "COOK, DON'T ASSEMBLE" in prompt_literals(agent.generate_weekly_plan_llm)
#
# Why. `inspect.getsource` pairs the function's line numbers — baked in at
# import time — against app/agent.py as it reads on disk AT THE MOMENT THE
# TEST RUNS, via linecache. So a merge landing on the checkout mid-run can
# hand a test the right text under the wrong function: on 2026-09-15 a
# prompt-text test got `_stream_forced_tool_call`'s body back instead of
# `generate_weekly_plan_llm`'s, with nothing in this suite's own state to
# explain it. `prompt_literals` reads `fn.__code__.co_consts` — whatever the
# interpreter already compiled into the function object — so nothing that
# later happens to the file can change its answer.
#
# It is also more honest about what a prompt IS. Three differences from
# source text, each of which has already mattered here:
#   * Line-continuation backslashes are gone, because the compiler resolved
#     them. `"AT MOST 3"` is split across a wrapped line in agent.py and is
#     NOT findable in getsource output; it is findable here. Two files used
#     to carry `.replace("\\\n", "")` to paper over exactly that.
#   * Comments, identifiers and code text are gone. A negative assertion is
#     therefore narrower — `"repeats_tolerance" not in prompt_literals(fn)`
#     says the PROMPT never names it, not that the function's code never
#     does. That is the claim those tests are making; it is not the same
#     claim.
#   * An f-string's `{interpolation}` is gone, and so is whatever it pulls
#     in. `{COOK_DONT_ASSEMBLE}` is a LOAD_GLOBAL, not a constant, so it
#     appears in neither the literals of the function nor their expansion.
#     A test about WHERE a block is spliced is a test about code structure,
#     not about prompt text — those use `agent_function_source` below.
def prompt_literals(fn) -> str:
    """
    The string literals baked into `fn` at compile time, newline-joined.

    Everything the function's own code object holds, walking into nested
    defs/lambdas/comprehensions, in the order the compiler stored them —
    which for a prompt function is the docstring, then the instructions,
    then a handful of short code strings (dict keys, tool names).
    """
    def _walk(code, seen):
        if id(code) in seen:
            return
        seen.add(id(code))
        for const in code.co_consts:
            if isinstance(const, str):
                yield const
            elif hasattr(const, "co_consts"):  # a nested def/lambda/comprehension
                yield from _walk(const, seen)

    return "\n".join(_walk(fn.__code__, set()))


# A handful of tests are about the SHAPE of app/agent.py rather than about
# what a prompt says: that `{COOK_DONT_ASSEMBLE}` is spliced inside the
# cached `instructions` f-string and before `context_block`, that
# `WRITE_IT_DOWN = ` is assigned exactly once. None of that survives
# compilation, so those genuinely need the file's text — but they do not
# need it read afresh at each call, at whatever moment that call happens.
#
# It is read ONCE, here, while conftest is imported at the start of
# collection, and every consumer gets that same string. That does not make a
# torn read impossible; it makes it a single early event with one answer
# instead of a scatter of reads that can disagree with each other. And
# `agent_function_source` takes its line numbers from PARSING THAT STRING
# rather than from the compiled function object, so a file that arrived torn
# raises a SyntaxError instead of quietly handing back the wrong function's
# body — which is the failure that started all this.
_AGENT_AST = []
_AGENT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "agent.py")
try:
    _AGENT_SOURCE = open(_AGENT_PATH, encoding="utf-8").read()
except OSError as exc:  # pragma: no cover - the suite is unusable anyway
    _AGENT_SOURCE = None
    _AGENT_SOURCE_ERROR = exc


def agent_source() -> str:
    """app/agent.py's text, read once at collection time and cached."""
    if _AGENT_SOURCE is None:  # pragma: no cover
        raise RuntimeError("could not read %s: %s" % (_AGENT_PATH, _AGENT_SOURCE_ERROR))
    return _AGENT_SOURCE


def agent_function_source(name: str) -> str:
    """
    The text of `def <name>(...)` in app/agent.py — for the handful of tests
    that are about the file's SHAPE rather than about what a prompt says.

    The line numbers come from parsing the CACHED STRING, never from the
    compiled function object, so the two halves of the answer can never come
    from two different versions of the file. A file that arrived torn raises
    a SyntaxError or a named ValueError here; it can never quietly hand back
    a different function's body, which is the failure that started all this.
    """
    tree = _agent_ast()
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == name:
            lines = agent_source().splitlines(keepends=True)
            return "".join(lines[node.lineno - 1:node.end_lineno])
    raise ValueError("no top-level `def %s(` in %s" % (name, _AGENT_PATH))


def _agent_ast():
    if not _AGENT_AST:
        _AGENT_AST.append(_ast.parse(agent_source()))
    return _AGENT_AST[0]
