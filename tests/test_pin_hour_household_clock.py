"""
A `--today` pin names an hour. This is what the HOUSEHOLD reads when it does.

The pin freezes the PROCESS's local wall clock (conftest._freeze_args), and
every screen in this app reads the household's (cooker.household_now, and
digest/tonight/holidays behind it). Those are two different clocks, and for
most of 2026-09 they were further apart under a pin than they are in
production: freezegun applied the process's tz_offset on top of an already-
correct tz conversion, so an aware `datetime.now(timezone.utc)` came back as
the pin's local wall time wearing a UTC label — four hours adrift from
`utcnow()` and `time.time()`, which are the same clock's own answers.

What that cost was coverage, not a wrong answer in production. A Toronto
household under a 09:00 pin sat at 05:00, so all four pinned `clock` CI jobs
exercised a house that had not yet had its 07:00 morning text and could never
reach the 18:30 after which tonight's shop move closes — the evening branch of
moves.py, cooker.py, weekly_plan.py, defrost.py and digest.py, all moved onto
that clock in the week of 2026-09-14, was unreachable under a pin.

Every test here freezes deliberately and sets TZ itself, so each one says which
two zones it is talking about rather than inheriting the runner's.
"""
import datetime
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.db import get_conn
from app.tools import cooker
from conftest import household_pin, household_today

REPO = Path(__file__).resolve().parents[1]

# 09:00 is --today's own bare-date default, which is the hour this is about.
PIN_DAY = "2026-09-21"
PIN = f"{PIN_DAY}T09:00"
HOUSEHOLD_ZONE = "America/Toronto"  # the column default, and CI's pinned zone


@pytest.fixture
def in_zone():
    """Run the body with the process on a named timezone, then put it back."""
    was = os.environ.get("TZ")

    def _set(name):
        os.environ["TZ"] = name
        time.tzset()

    yield _set
    if was is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = was
    time.tzset()


def _set_household_zone(name):
    conn = get_conn()
    conn.execute("UPDATE households SET timezone = ? WHERE id = 1", (name,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------- the headline


def test_a_pin_puts_the_household_at_the_hour_it_names(in_zone, frozen_today):
    """
    CATCH. The card, in one line: on CI's own pinned zone, a 09:00 pin is the
    household's 09:00. It read 05:00 — 09:00 minus Toronto's UTC offset.
    """
    in_zone(HOUSEHOLD_ZONE)
    frozen_today(PIN)
    now = cooker.household_now()
    assert now.hour == 9, now
    assert now.date().isoformat() == PIN_DAY, now


def test_the_household_is_past_the_morning_text_and_short_of_the_shop_cutoff(
    in_zone, frozen_today
):
    """
    CATCH, and it is the docstring's own rationale asserted of the clock the
    app actually reads. `_parse_pin` says 09:00 is "past the 07:00 morning
    text, well short of the 18:30 after which tonight's shop move closes" —
    true of the process, and false of the household at 05:00.
    """
    in_zone(HOUSEHOLD_ZONE)
    frozen_today(PIN)
    minutes = cooker.household_now().hour * 60 + cooker.household_now().minute
    assert 7 * 60 <= minutes < 18 * 60 + 30, cooker.household_now()


def test_an_explicit_evening_pin_reaches_the_household(in_zone, frozen_today):
    """
    CATCH. The other half: pinning an edge on purpose has to land on that edge.
    18:30 is the hour tonight's shop move closes at; before this it arrived as
    14:30 and the branch stayed unreachable however explicitly it was asked for.
    """
    in_zone(HOUSEHOLD_ZONE)
    frozen_today(f"{PIN_DAY}T18:30")
    assert (cooker.household_now().hour, cooker.household_now().minute) == (18, 30)


# ------------------------------------------------- why it was wrong, at the root


def test_the_pinned_clock_has_one_answer_for_the_utc_instant(in_zone, frozen_today):
    """
    CATCH, and the root of it. `datetime.now(timezone.utc)`, `datetime.utcnow()`
    and `time.time()` are three ways of asking one clock the same question, and
    under a pin the first disagreed with the other two by the process's offset.
    Niue is UTC-11 with no DST, so the arithmetic is the same in every month and
    far enough out that a collapsed pin cannot look right by accident.
    """
    in_zone("Pacific/Niue")
    frozen_today(PIN)
    aware = datetime.datetime.now(datetime.timezone.utc)
    naive_utc = datetime.datetime.utcnow()
    epoch_utc = datetime.datetime.fromtimestamp(time.time(), datetime.timezone.utc)
    assert aware.replace(microsecond=0) == naive_utc.replace(
        microsecond=0, tzinfo=datetime.timezone.utc
    ), (aware, naive_utc)
    assert abs((aware - epoch_utc).total_seconds()) < 2, (aware, epoch_utc)
    # ...and 20:00 is the honest instant behind 09:00 at UTC-11.
    assert aware.hour == 20, aware


def test_another_households_zone_is_converted_and_not_copied(in_zone, frozen_today):
    """
    CATCH, and the guard against "fixing" this by handing every household the
    pin's own hour. A house three hours west of the pinned zone is three hours
    behind it, which is the whole reason there are two clocks.
    """
    in_zone(HOUSEHOLD_ZONE)
    _set_household_zone("America/Vancouver")
    frozen_today(PIN)
    assert cooker.household_now().hour == 6, cooker.household_now()


# ------------------------------------------------------- what must not have moved


def test_the_pin_is_still_the_process_wall_clock(in_zone, frozen_today):
    """
    GUARD, green before and after — pinned by mutation, since the fix could
    have been written by shifting the naive clock instead. It must not be:
    `--today=...T09:00` means the process reads 09:00 local, SQLite's
    localtime agrees with it, and `date.today()` is the day it names. A dozen
    files pin `weekly_plan.date` by hand on exactly that promise.
    """
    in_zone("Pacific/Niue")
    frozen_today(PIN)
    assert datetime.datetime.now().hour == 9
    assert datetime.date.today().isoformat() == PIN_DAY
    assert datetime.datetime.utcnow().hour == 20
    conn = get_conn()
    try:
        utc, local = conn.execute(
            "SELECT datetime('now'), datetime('now', 'localtime')"
        ).fetchone()
    finally:
        conn.close()
    assert utc.endswith("20:00:00"), utc
    assert local.endswith("09:00:00"), local


def test_an_unpinned_run_reads_the_wall_clock_for_both(in_zone):
    """
    GUARD, and it is worth saying what it cannot see. The three CI jobs that
    run UNPINNED have to be untouched by any of this, and they are — but not
    because of the `frozen is None` clause in `_fg_aware_now`. freezegun only
    installs `FakeDatetime` into `datetime` while a freeze is up, so with
    nothing frozen the patched method is not on the call path at all, and
    deleting that clause leaves this green. It is kept as defence in depth,
    mirroring freezegun's own fallback, and is pinned by nothing. Said here
    rather than claimed as coverage.
    """
    in_zone(HOUSEHOLD_ZONE)
    aware = datetime.datetime.now(datetime.timezone.utc)
    epoch_utc = datetime.datetime.fromtimestamp(time.time(), datetime.timezone.utc)
    assert abs((aware - epoch_utc).total_seconds()) < 2


def test_conftests_household_today_still_agrees_with_the_app(in_zone, frozen_today):
    """
    GUARD. `conftest.household_today()` computes the household's date the same
    way `cooker.household_today()` does, on purpose — the two must not part
    company under a pin, or every dated test seeded off the helper is asserting
    against a different day from the screen it asks.
    """
    in_zone("Pacific/Niue")
    frozen_today(PIN)
    assert household_today() == cooker.household_today()


# ------------------------------------------------------------- household_pin()


@pytest.mark.parametrize("process_zone", ["America/Toronto", "UTC", "Asia/Tokyo"])
def test_household_pin_names_the_households_hour_in_any_zone(
    in_zone, frozen_today, process_zone
):
    """
    CATCH in two of its three cases, and the third says so rather than being
    counted. A pin is the process's wall time, so a test that means the
    household's hour has to convert — and these are the three zones CI runs
    in. Tokyo is the one that matters: thirteen hours from Toronto, so a test
    pinning 10:00 and meaning the household's got 21:00 the evening before and
    lost tonight's shop move.

    [UTC] IS A GUARD, NOT A CATCH, and cannot be red. The seam was the process
    offset applied twice, and UTC's offset is zero, so the bug could never
    manifest there. It is parametrized in anyway because a future change that
    got the conversion backwards would break it, and because dropping it would
    leave the one zone the deployed container runs in untested here.
    """
    in_zone(process_zone)
    frozen_today(household_pin(10))
    assert cooker.household_now().hour == 10, cooker.household_now()


def test_household_pin_carries_the_minutes_and_a_named_day(in_zone, frozen_today):
    """CATCH. The edge cases are the point of having it: 18:45, on a day said."""
    in_zone("Asia/Tokyo")
    day = household_today() + datetime.timedelta(days=3)
    frozen_today(household_pin(18, 45, on=day))
    now = cooker.household_now()
    assert (now.hour, now.minute) == (18, 45), now
    assert now.date() == day, now


def test_household_pin_follows_the_household_and_not_the_default(in_zone, frozen_today):
    """
    CATCH. It reads the zone on the household row, not the column default, so a
    test that moved the household somewhere still gets that household's hour.
    """
    in_zone(HOUSEHOLD_ZONE)
    _set_household_zone("Asia/Tokyo")
    frozen_today(household_pin(7))
    assert cooker.household_now().hour == 7, cooker.household_now()


# ------------------------------------------------------------------ the CI file


def test_the_pinned_jobs_still_run_in_the_households_own_zone():
    """
    GUARD, and it is a tripwire on a line that just became load-bearing again.
    The `clock` matrix reads TZ: America/Toronto, and its own comment has
    called that "belt and braces, not the belt" since the date-shaped tests
    moved onto the household's clock. It is the belt again for a different
    reason: it is what makes a pinned hour the HOUSEHOLD's hour. Drop it and
    those four jobs go quietly back to exercising a 05:00 house.
    """
    workflow = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    clock = workflow[workflow.index("\n  clock:") : workflow.index("\n  straddle:")]
    assert "TZ: America/Toronto" in clock
    assert "POMONA_TEST_TODAY" in clock
