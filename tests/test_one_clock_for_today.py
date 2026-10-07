"""
Today and Cook used the phone's date while Plan used the household's — they
disagreed every evening west of Toronto (defect hunt 2026-10-07, High).

    households.timezone America/Toronto, browser in Pacific/Honolulu, the
    Toronto small hours: Today's band read "Tuesday, Oct 6" over
    Wednesday's meals, Cook read "Tuesday, Oct 6 · TONIGHT <Tuesday's
    dinner>" and filed Wednesday's thaw as "Tomorrow", while Plan's TODAY
    was Wed 7.

The shell's "today" (todayLocalStr, bandDateLabel, tomorrowLocalStr,
thisWeekStartLocal, cookTonightIndex's hour) now reads the household's
zone, which /api/whoami sends before any tab renders; the phone's clock is
only the fallback. Run under node with TZ set to a zone other than the
household's and the clock pinned.
"""
from __future__ import annotations

import shutil

import pytest

from app.tools import cooker as _cooker

from test_plan_cards_2026_09_18 import _run
from test_week_seven_tiles import _extract
from test_plan_next_on_draft import SHELL_JS

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the functions")

# 05:30 UTC on Wed Oct 7: 01:30 Wednesday in Toronto, 19:30 Tuesday in Honolulu.
_PIN = """
var FIXED = Date.UTC(2026, 9, 7, 5, 30);
var RealDate = Date;
Date = class extends RealDate {
  constructor() { if (arguments.length) super(...arguments); else super(FIXED); }
  static now() { return FIXED; }
};
var BAND_IDENTITY = 'wordmark';
"""


def _clock_fns() -> str:
    return "\n".join(_extract(n, SHELL_JS) for n in (
        "todayLocalStr", "householdHourNow", "bandDateLabel",
        "tomorrowLocalStr", "thisWeekStartLocal", "cookBandEyebrow",
    )) + "\nfunction dayName() { return 'x'; }\n"


@_needs_node
def test_every_today_on_screen_is_the_households_day(monkeypatch):
    """CATCH."""
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    out = _run(_PIN + "var shellWho = { timezone: 'America/Toronto' };\n" + _clock_fns() + """
console.log(JSON.stringify({
  phone: new Date().getDate(),
  today: todayLocalStr(), band: bandDateLabel(), tomorrow: tomorrowLocalStr(),
  monday: thisWeekStartLocal(), cook: cookBandEyebrow(todayLocalStr()),
  hour: householdHourNow()
}));""")
    assert out["phone"] == 6, "the harness really is a day behind the household"
    assert out["today"] == "2026-10-07"
    assert out["band"] == "Wednesday, Oct 7", "Today's band"
    assert out["cook"] == "Wednesday, Oct 7", "Cook's eyebrow"
    assert out["tomorrow"] == "2026-10-08"
    assert out["monday"] == "2026-10-05"
    assert out["hour"] == 1


@_needs_node
def test_without_a_zone_the_phone_clock_is_the_fallback(monkeypatch):
    """GUARD. Before whoami lands (or an older server, or a zone Intl
    doesn't know) the screen still has a date: the phone's."""
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    out = _run(_PIN + "var shellWho = { timezone: '' };\n" + _clock_fns() + """
var a = todayLocalStr();
shellWho.timezone = 'Not/AZone';
console.log(JSON.stringify([a, todayLocalStr(), householdHourNow()]));""")
    assert out == ["2026-10-06", "2026-10-06", 19]


def test_the_shell_reads_the_zone_off_whoami_and_nowhere_reads_the_phone_for_today():
    assert "shellWho.timezone = typeof data.timezone === 'string' ? data.timezone : '';" in SHELL_JS
    for name in ("bandDateLabel", "thisWeekStartLocal", "tomorrowLocalStr"):
        assert "new Date()" not in _extract(name, SHELL_JS), name
    assert "householdHourNow()" in _extract("cookTonightIndex", SHELL_JS)


def test_whoami_carries_the_households_zone_and_day(signed_in):
    from app.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE households SET timezone = 'America/Vancouver' WHERE id = 1")
    conn.commit()
    conn.close()
    who = signed_in.get("/api/whoami").json()
    assert who["timezone"] == "America/Vancouver"
    assert who["today"] == _cooker.household_today().isoformat()
