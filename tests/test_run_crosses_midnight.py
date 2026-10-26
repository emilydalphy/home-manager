"""
A test run that crosses midnight gives the same answer as one that doesn't.

84 files hold `TODAY = household_today()` at module scope, computed at
collection, so a run that crossed household midnight partway through had
every later test seeding yesterday — 57 and 87 false failures on two real
runs on 2026-10-23. conftest's `_a_run_that_crosses_midnight` holds the run
to the day it started. See that block for why it pins to the collection
instant rather than shifting a whole day.
"""
import datetime as _dt
import os
import subprocess
import sys
import textwrap

import freezegun

import conftest
from conftest import household_today


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORONTO = conftest._COLLECTION_ZONE


def _epoch(y, m, d, hh, mm, ss=0, zone=TORONTO):
    return _dt.datetime(y, m, d, hh, mm, ss, tzinfo=zone).timestamp()


def _held(monkeypatch, epoch):
    home_day = _dt.datetime.fromtimestamp(epoch, TORONTO).date()
    process_day = freezegun.api.real_datetime.fromtimestamp(epoch).date()
    monkeypatch.setattr(conftest, "_COLLECTION_CLOCK", (epoch, process_day, home_day))
    return conftest._held_to_the_starting_day().timestamp()


def test_a_mid_morning_start_is_held_at_the_instant_it_started(monkeypatch):
    start = _epoch(2026, 10, 22, 10, 0)
    assert _held(monkeypatch, start) == start


def test_a_start_just_before_household_midnight_is_drawn_back_from_the_line(monkeypatch):
    # 23:58:30 in Toronto — pinned there, the ticking clock would cross again
    # inside the next test. Five minutes short of midnight instead, whatever
    # zone this process runs in.
    held = _held(monkeypatch, _epoch(2026, 10, 22, 23, 58, 30))
    assert _dt.datetime.fromtimestamp(held, TORONTO) == _dt.datetime(
        2026, 10, 22, 23, 55, tzinfo=TORONTO
    )


_PROBE = textwrap.dedent(
    """
    import datetime, time
    from conftest import household_today

    TODAY = household_today()   # at collection, like the 84 real files
    SERVER_TODAY = datetime.date.today()

    def test_1_the_clock_crosses_midnight():
        time.sleep(3)

    def test_2_a_later_test_still_sees_the_day_its_constants_were_made():
        assert household_today() == TODAY
        assert datetime.date.today() == SERVER_TODAY
    """
)


def test_a_run_started_two_seconds_before_midnight_still_agrees_with_its_constants(tmp_path):
    # A real inner run: conftest loaded as a plugin, the session pinned to
    # 23:59:58 Toronto with a ticking clock, a three-second test, and then a
    # test that compares its collection-time constants with the clock.
    probe = tmp_path / "probe_midnight.py"
    probe.write_text(_PROBE)
    env = dict(
        os.environ,
        TZ="America/Toronto",
        POMONA_TEST_TODAY="2026-10-22T23:59:58",
    )
    env.pop("PYTEST_ADDOPTS", None)
    out = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-c", os.path.join(REPO, "pytest.ini"), "--rootdir", REPO,
            "-p", "conftest", str(probe),
        ],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    assert "2 passed" in out.stdout, out.stdout[-3000:] + out.stderr[-3000:]
