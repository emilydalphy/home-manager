"""
Switched on after the morning time, the first morning text is tomorrow's
(Emily, 2026-10-31).

The pass's eight-hour catch-up is for an app that was down at seven. It
could not tell that from an adult who switched the text on at 2:50pm, so it
sent them that morning's text within five minutes — right under a sheet
that had just said "Saved. Next one's at 7:00."
"""
from __future__ import annotations

import datetime as dt

import pytest

from app import tools
from app.tools import digest
from conftest import household_today
from test_morning_text import _Sender, _adults, _local, _member_id, _rows, _seed_day

TODAY = household_today()
TOMORROW = TODAY + dt.timedelta(days=1)


@pytest.fixture
def switched_on_at(monkeypatch):
    def at(hour, minute=0):
        monkeypatch.setattr(digest, "_switched_on_at",
                            lambda conn: dt.datetime.combine(TODAY, dt.time(hour, minute)))
    return at


def test_switched_on_at_2_50pm_the_first_text_is_tomorrows(switched_on_at):
    """CATCH: the card's own case."""
    _adults("Emily")
    _seed_day()
    switched_on_at(14, 50)
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    sender = _Sender()

    assert tools.run_morning_texts_once(now_utc=_local(14, 55), send=sender) == []
    assert sender.calls == []
    assert [(r["sent_on"], r["status"]) for r in _rows()] == [
        (TODAY.isoformat(), digest.TURNED_ON_LATE)]

    # Tomorrow at seven it goes as normal.
    results = tools.run_morning_texts_once(now_utc=_local(7, 3, day=TOMORROW), send=sender)
    assert [r["status"] for r in results] == ["ok"]
    assert len(sender.calls) == 1


def test_switched_on_before_the_morning_time_today_still_goes(switched_on_at):
    """GUARD: on at 6:30 for a 7:00 text is today's text."""
    _adults("Emily")
    _seed_day()
    switched_on_at(6, 30)
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    sender = _Sender()
    assert [r["status"] for r in tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)] == ["ok"]


def test_a_missed_morning_still_catches_up_for_someone_already_on(switched_on_at):
    """GUARD: the catch-up the window exists for — the app down at seven,
    back at ten, for an adult switched on days ago — is untouched."""
    _adults("Emily")
    _seed_day()
    switched_on_at(6, 0)
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    # Saving again later in the day (a new number, the same "on") is not a
    # switch-ON, so it does not use up today.
    switched_on_at(9, 30)
    tools.set_morning_text(phone="416-555-0101", on=True, name="Emily")
    sender = _Sender()
    assert [r["status"] for r in tools.run_morning_texts_once(now_utc=_local(10, 0), send=sender)] == ["ok"]


def test_off_and_on_again_after_todays_text_went_sends_nothing_twice(switched_on_at):
    _adults("Emily")
    _seed_day()
    switched_on_at(6, 0)
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    sender = _Sender()
    tools.run_morning_texts_once(now_utc=_local(7, 3), send=sender)
    switched_on_at(15, 0)
    tools.set_morning_text(on=False, name="Emily")
    tools.set_morning_text(on=True, name="Emily")
    assert tools.run_morning_texts_once(now_utc=_local(15, 5), send=sender) == []
    assert len(sender.calls) == 1
    assert [r["status"] for r in _rows()] == ["ok"]


def test_through_the_preferences_route(signed_in, switched_on_at):
    """The sheet's own save, the way the person actually does it."""
    _adults("Emily")
    _seed_day()
    switched_on_at(14, 50)
    res = signed_in.post("/api/morning-text", json={
        "member_id": _member_id("Emily"), "phone": "416-555-0100", "on": True})
    assert res.status_code == 200, res.text
    sender = _Sender()
    assert tools.run_morning_texts_once(now_utc=_local(14, 55), send=sender) == []
    assert sender.calls == []
