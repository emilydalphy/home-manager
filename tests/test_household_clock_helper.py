"""
The suite's own clock helper: `conftest.household_today()`.

Why this file exists. Since overnight/moves-household-clock (2026-09-14) the
app's screens run on the HOUSEHOLD's clock, while the test process runs on
whatever `TZ` it was given. A test that seeds a date with
`datetime.date.today()` and then asks a screen about "today" is therefore
asserting that those two clocks are on the same day — which they are not for
four hours of every UTC day, seven under Pacific/Niue and eighteen under
Pacific/Kiritimati. Twenty-eight tests were in that state, and CI was pinned to
`TZ=America/Toronto` to hide it, i.e. to the one configuration in which the
whole bug class cannot occur.

`household_today()` is the fix, and it is one function rather than twenty-eight
local edits so that the next dated test gets it right by default. That makes it
load-bearing, and a helper that quietly answered the server's date again would
put every one of those tests back where it started while leaving the suite
green — which is precisely the failure this file is here to catch. Each test
below says which mutation it bites.

These run on the real clock and on a pin, and they never assert that two
particular dates differ on a particular day — a guard that only works between
04:00 and 11:00 UTC is not a guard. Where a difference is needed it is
manufactured: `_a_zone_on_another_day()` picks one that is genuinely on another
date at this instant, whatever this instant is.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.db import get_conn
from app.tools import cooker
from conftest import household_date, household_today


def _a_zone_on_another_day() -> ZoneInfo:
    """
    A fixed-offset zone whose calendar date right now is NOT this process's.

    One always exists and this is not luck: UTC-11 and UTC+14 are twenty-five
    hours apart, so they cannot both be on the process's own date. That is what
    lets the tests below assert a real disagreement at any hour of any day
    rather than only inside some window.
    """
    for hours in (14, -11):
        zone = ZoneInfo(f"Etc/GMT{-hours:+d}")  # Etc/GMT+5 means UTC-5
        if dt.datetime.now(zone).date() != dt.date.today():
            return zone
    raise AssertionError("UTC-11 and UTC+14 cannot both share the process's date")


def _set_zone(name: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE households SET timezone = ? WHERE id = 1", (name,))
    conn.commit()
    conn.close()


def test_it_agrees_with_the_app_on_the_seeded_household():
    """
    The whole contract: whatever the app's screens will call today, this calls
    today. Weak on its own — at most hours the two clocks agree anyway — which
    is why the next test manufactures a disagreement.
    """
    assert household_today() == cooker.household_today()


def test_it_follows_the_households_zone_and_is_not_the_processs_date():
    """
    Bites the obvious wrong implementation, `return date.today()`, and every
    variant of it, at any hour: the household is moved to a zone that is
    provably on another date right now.
    """
    zone = _a_zone_on_another_day()
    _set_zone(str(zone))

    expected = dt.datetime.now(dt.timezone.utc).astimezone(zone).date()
    assert household_today() == expected
    assert household_today() != dt.date.today()
    assert household_today() == cooker.household_today(), "must still match the app"


def test_an_unreadable_zone_falls_back_the_way_the_app_does():
    """
    cooker.household_now answers a broken `households.timezone` with the
    default rather than raising — a bad setting is worth a wrong hour, never a
    blank screen — and the helper has to make the same choice or a test on such
    a household asks about a different day than the app does.
    """
    _set_zone("Not/AZone")
    assert household_today() == cooker.household_today()
    expected = dt.datetime.now(dt.timezone.utc).astimezone(
        ZoneInfo(cooker.DEFAULT_TIMEZONE)
    ).date()
    assert household_today() == expected


def test_with_no_database_it_still_converts_rather_than_giving_up(monkeypatch):
    """
    THE ONE THAT MATTERS, and the reason this is not simply a call to
    `cooker.household_today()`.

    A dozen test modules hold `TODAY = household_today()` at MODULE scope, and
    module scope is collection — before the session fixture that calls
    init_db(), so there is no `households` table to read. `cooker`'s own
    version swallows that and returns `date.today()`, the SERVER's date, which
    is exactly the answer the helper exists to stop a test from using: every
    one of those constants would silently go back to being wrong while the
    helper looked like it was working.

    So with the database unreachable it must still convert through the column's
    default zone. Mutation-checked: replacing the helper's body with
    `cooker.household_today()` fails this.
    """
    import conftest

    monkeypatch.setattr(conftest, "_seeded_timezone", lambda: None)
    monkeypatch.setattr(cooker, "DEFAULT_TIMEZONE", str(_a_zone_on_another_day()))

    expected = dt.datetime.now(dt.timezone.utc).astimezone(
        ZoneInfo(cooker.DEFAULT_TIMEZONE)
    ).date()
    assert household_today() == expected
    assert household_today() != dt.date.today()


@pytest.mark.today("2026-09-13")
def test_under_a_pin_it_reports_the_pinned_instant_not_a_second_clock():
    """
    It composes with --today / @pytest.mark.today / frozen_today rather than
    competing with them: the freeze is already on, so every clock the helper
    reads is the pinned one. A helper that started its own freeze, or that
    reached past the pin to the wall clock, would answer about a different day
    than the app under test.
    """
    assert household_today() == cooker.household_today()
    assert dt.date.today() == dt.date(2026, 9, 13), "the pin itself still holds"


def test_household_date_is_the_iso_string_the_api_speaks():
    assert household_date() == household_today().isoformat()
    assert household_date(3) == (household_today() + dt.timedelta(days=3)).isoformat()
    assert household_date(-2) == (household_today() - dt.timedelta(days=2)).isoformat()
