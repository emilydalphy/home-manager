"""
A save that moves the morning time to before now starts the text tomorrow
(defect hunt, 2026-11-03).

The switch-on rule (test_morning_text_starts_tomorrow.py, Emily 2026-10-31)
through the other door: the text on for 10:00, moved to 8:00 at 9:00, and
the pass's eight-hour catch-up sent today's text within five minutes -- to
every adult who gets it -- under a sheet that had just said "Saved. Next
one's at 8:00." The same for a new zone that puts the hour behind the clock.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app import tools
from app.tools import cooker, digest
from test_morning_text import TODAY, _Sender, _adults, _local, _member_id, _rows, _seed_day

TOMORROW = TODAY + dt.timedelta(days=1)


@pytest.fixture
def saved_at(monkeypatch):
    """The save happens at this wall-clock time on the household's clock."""
    def at(hour, minute=0):
        monkeypatch.setattr(digest, "_switched_on_at",
                            lambda conn: dt.datetime.combine(TODAY, dt.time(hour, minute)))
    return at


def _both_on_for(time: str, saved_at):
    _adults("Emily", "Vineeth")
    _seed_day()
    saved_at(6, 0)
    tools.set_morning_text(phone="416-555-0100", on=True, time=time, name="Emily")
    tools.set_morning_text(phone="416-555-0101", on=True, name="Vineeth")


def test_moving_the_hour_to_before_now_starts_tomorrow_for_everyone(saved_at):
    """CATCH: on for 10:00; at 9:00 moved to 8:00. Nothing today, to either
    adult; tomorrow at 8:00 both go."""
    _both_on_for("10:00", saved_at)
    saved_at(9, 0)
    tools.set_morning_text(time="08:00", name="Emily")
    sender = _Sender()
    assert tools.run_morning_texts_once(now_utc=_local(9, 5), send=sender) == []
    assert sender.calls == []
    assert sorted((r["sent_on"], r["status"]) for r in _rows()) == [
        (TODAY.isoformat(), digest.MOVED_PAST_NOW)] * 2

    results = tools.run_morning_texts_once(now_utc=_local(8, 3, day=TOMORROW), send=sender)
    assert [r["status"] for r in results] == ["ok", "ok"]


def test_through_the_sheet_which_posts_the_time_with_every_row(signed_in, saved_at):
    """The Preferences sheet's own save: one POST per adult, each carrying
    the household's hour."""
    _both_on_for("10:00", saved_at)
    saved_at(9, 0)
    for name, phone in (("Emily", "416-555-0100"), ("Vineeth", "416-555-0101")):
        res = signed_in.post("/api/morning-text", json={
            "member_id": _member_id(name), "phone": phone, "on": True, "time": "08:00"})
        assert res.status_code == 200, res.text
    sender = _Sender()
    assert tools.run_morning_texts_once(now_utc=_local(9, 5), send=sender) == []
    assert sender.calls == []


def test_a_new_hour_still_ahead_of_now_sends_today(saved_at):
    """GUARD: at 9:00, 10:00 moved to 9:30 -- still to come, still today's."""
    _both_on_for("10:00", saved_at)
    saved_at(9, 0)
    tools.set_morning_text(time="09:30", name="Emily")
    assert _rows() == []
    sender = _Sender()
    assert [r["status"] for r in tools.run_morning_texts_once(now_utc=_local(9, 33), send=sender)] == ["ok", "ok"]


def test_a_morning_already_due_keeps_its_catch_up(saved_at):
    """GUARD: the catch-up is for a morning that was already due (the app
    down at seven). Saving the hour that day -- even moving it -- does not
    cancel what was already owed."""
    _both_on_for("07:00", saved_at)
    saved_at(9, 0)
    tools.set_morning_text(time="06:30", name="Emily")
    assert _rows() == []
    sender = _Sender()
    assert [r["status"] for r in tools.run_morning_texts_once(now_utc=_local(9, 5), send=sender)] == ["ok", "ok"]


def test_a_new_zone_that_puts_the_hour_behind_the_clock_starts_tomorrow(signed_in, monkeypatch):
    """CATCH: on for 7:00 on the Toronto default; at 5:00 Toronto (10:00 in
    London) the household says it lives in London. 7:00 London has passed
    and nothing was owed yet, so the first text is tomorrow's 7:00."""
    london = ZoneInfo("Europe/London")
    save_utc = _local(10, 0, zone=london)
    monkeypatch.setattr(digest, "_switched_on_at",
                        lambda conn: cooker.household_now(now_utc=save_utc, conn=conn))
    _adults("Emily")
    _seed_day()
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    assert _rows() == []
    res = signed_in.post("/api/household/clock", json={"timezone": "Europe/London"})
    assert res.status_code == 200, res.text
    sender = _Sender()
    assert tools.run_morning_texts_once(now_utc=_local(10, 5, zone=london), send=sender) == []
    assert [r["status"] for r in _rows()] == [digest.MOVED_PAST_NOW]
