"""
Holidays, the calendar feed, the first plan's window and the chat's date
block all ran on the SERVER's day (UTC) — already tomorrow from 8pm Toronto.
Each spot below is pinned at an instant where the two clocks disagree and
asserts the household's day reaches it (Loop Board, 2026-10-05; sister of
the cook card's leftovers._household_today_iso fix).

STRADDLE is 01:00 UTC on Monday 2026-10-05 = Sunday 21:00 Toronto. Every
test is a CATCH: red on the server's date.today(). The first-plan window
needs a pin where the day CHANGES the answer (Saturday evening, one day
left in the period on the server's reckoning), so it has its own.
"""
import datetime
import types

import freezegun
import pytest

from app import agent, calendar_feed as cf, main, tools
from app.tools import holidays

STRADDLE = "2026-10-05T01:00:00Z"
HOUSEHOLD_DAY = datetime.date(2026, 10, 4)


def test_the_pin_really_separates_the_two_clocks():
    with freezegun.freeze_time(STRADDLE):
        assert datetime.date.today() == datetime.date(2026, 10, 5)
        assert tools.cooker.household_today() == HOUSEHOLD_DAY


def test_upcoming_holidays_start_from_the_households_day(monkeypatch):
    seen = []
    monkeypatch.setattr(holidays, "holidays_for_period", lambda start, n=7: seen.append(start) or [])
    with freezegun.freeze_time(STRADDLE):
        tools.get_upcoming_holidays("", 30)
        tools.get_upcoming_holidays("2026-12-01", 30)  # a named date is untouched
    assert seen == ["2026-10-04", "2026-12-01"]


def _spy_reads(monkeypatch):
    seen = []

    def fake_read(url, start, end):
        seen.append((start, end))
        return {"events": [], "timezone": "America/Toronto", "timezone_source": "feed", "skipped": 0, "recurring_skipped": 0}

    monkeypatch.setattr(cf, "read_feed_now", fake_read)
    monkeypatch.setattr(cf, "fetch_feed", lambda url: "")
    return seen


URL = "https://calendar.google.com/calendar/ical/x%40example.com/private-abc/basic.ics"


def test_check_reads_the_coming_week_from_the_households_day(monkeypatch):
    seen = _spy_reads(monkeypatch)
    with freezegun.freeze_time(STRADDLE):
        cf.check(URL)
    assert seen == [(HOUSEHOLD_DAY, HOUSEHOLD_DAY + datetime.timedelta(days=6))]


def test_connect_and_refresh_window_starts_the_day_before_the_households_day(monkeypatch):
    seen = _spy_reads(monkeypatch)
    with freezegun.freeze_time(STRADDLE):
        cf.connect(URL, "Family")
        cf.refresh()
    expected = HOUSEHOLD_DAY - datetime.timedelta(days=1)
    assert [s for s, _ in seen] == [expected, expected]


def test_events_for_period_window_follows_the_households_day(monkeypatch):
    seen = _spy_reads(monkeypatch)
    with freezegun.freeze_time(STRADDLE):
        cf.connect(URL, "Family")
        seen.clear()
        # a period past what the cache covers forces a real read
        cf.events_for_period("2026-12-01", 7)
    assert seen[0][0] == HOUSEHOLD_DAY - datetime.timedelta(days=1)


def test_the_first_plan_window_uses_the_households_day():
    # Saturday 21:00 Toronto = Sunday 01:00 UTC. On the household's day the
    # part-week has two days left; on the server's, one — which folds forward
    # to next Monday.
    with freezegun.freeze_time("2026-10-04T01:00:00Z"):
        assert main._first_plan_window(False) == ("2026-09-28", 2, "2026-10-03")


def _stub_model(monkeypatch, sink):
    class _Messages:
        def create(self, **kwargs):
            sink.append(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="Hi.")],
                stop_reason="end_turn",
                usage=types.SimpleNamespace(
                    input_tokens=0, cache_read_input_tokens=0,
                    cache_creation_input_tokens=0, output_tokens=0,
                ),
            )

    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=_Messages()))


def test_the_chat_date_block_is_the_households_day_and_stays_uncached(monkeypatch):
    sent = []
    _stub_model(monkeypatch, sent)
    with freezegun.freeze_time(STRADDLE):
        agent.run_agent_turn([], "what day is it?")
    blocks = sent[0]["system"]
    assert "cache_control" in blocks[0]
    date_block = next(b for b in blocks if b["text"].startswith("Today's date is"))
    assert "Today's date is 2026-10-04 (Sunday)" in date_block["text"]
    assert "cache_control" not in date_block
