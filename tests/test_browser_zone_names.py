"""
A phone's own name for its zone is the household's clock, whichever
spelling the browser uses.

Defect hunt 2026-10-16: Chrome reports some zones by their OLD names —
measured on Chromium 141, a phone in India says
Intl.DateTimeFormat().resolvedOptions().timeZone == "Asia/Calcutta" (and
Vietnam "Asia/Saigon", Ukraine "Europe/Kiev", Nepal "Asia/Katmandu",
Argentina "America/Buenos_Aires"). Those are only links in the zone
database, and a server whose zone data is built without them (this
sandbox's is) refused them in rhythm.checked_timezone. Setup's last tap
posts the phone's zone with the rest of the rhythm, so "Build my first
week" answered 400 and the household saw "Couldn't save that — try again in
a moment." on every try; Today's "Use Calcutta time" failed the same way.

Each test says whether it is a CATCH (red before the fix) or a GUARD.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import tools
from app.db import get_conn
from app.tools import rhythm

ROOT = Path(__file__).resolve().parents[1]
SHELL_JS = (ROOT / "static" / "shell.js").read_text()


def _row():
    conn = get_conn()
    row = conn.execute("SELECT timezone, timezone_set FROM households WHERE id = ?",
                       (tools.household_id(),)).fetchone()
    conn.close()
    return row["timezone"], row["timezone_set"]


def test_setup_from_a_chrome_phone_in_india_saves_and_stores_kolkata(signed_in):
    """CATCH: the exact request setup's finish sends from Chrome in India."""
    res = signed_in.post("/api/onboarding/rhythm", json={"dinner_window": "6_8", "timezone": "Asia/Calcutta"})
    assert res.status_code == 200, res.text
    assert _row() == ("Asia/Kolkata", 1)
    assert tools.get_household_rhythm()["dinner_window"] == "6_8"


def test_todays_use_my_time_takes_the_browsers_name(signed_in):
    """CATCH: Today's one-time "Use Saigon time" posts the phone's name."""
    res = signed_in.post("/api/household/clock", json={"timezone": "Asia/Saigon"})
    assert res.status_code == 200, res.text
    assert res.json() == {"timezone": "Asia/Ho_Chi_Minh", "timezone_set": True}


def test_the_morning_text_settings_take_it_too():
    """CATCH: the third writer of the column goes through the same validator."""
    tools.add_member("Olena")
    tools.set_member_age_group("Olena", "adult")
    from app.tools import digest
    digest.set_morning_text_for_member(tools.household_adults()[0]["id"], timezone="Europe/Kiev")
    assert _row() == ("Europe/Kyiv", 1)


@pytest.mark.parametrize("old,new", sorted(rhythm._BROWSER_ZONE_NAMES.items()))
def test_every_browser_name_lands_on_a_zone_this_server_knows(old, new):
    """GUARD: the table's right-hand side is a real zone here, so a typo in
    it is a red test rather than a household that can't finish setup."""
    assert rhythm.checked_timezone(old) == new
    assert rhythm.checked_timezone(new) == new


def test_a_name_that_is_no_zone_is_still_refused(signed_in):
    """GUARD: the table widens nothing else."""
    for bad in ("Asia/Nowhere", "Calcutta", "localtime"):
        assert signed_in.post("/api/household/clock", json={"timezone": bad}).status_code == 400
    assert _row() == ("America/Toronto", 0)


def test_whoami_names_the_browsers_spelling_of_the_clock_on_file(signed_in):
    """CATCH: without it the shell compares "Asia/Calcutta" to "Asia/Kolkata",
    and Preferences offers "This phone: Calcutta" beside "Kolkata" — the same
    clock, shown as somewhere else."""
    assert signed_in.get("/api/whoami").json()["timezone_aliases"] == []
    signed_in.post("/api/household/clock", json={"timezone": "Asia/Kolkata"})
    assert signed_in.get("/api/whoami").json()["timezone_aliases"] == ["Asia/Calcutta"]


def test_the_shell_asks_and_offers_only_when_the_phone_is_really_elsewhere():
    """GUARD: Today's ask, Preferences' "This phone" chip and the set call
    all compare through isClockOnFile, which reads the aliases."""
    assert "shellWho.timezone_aliases = Array.isArray(data.timezone_aliases)" in SHELL_JS
    assert "(phone && shellWho.timezone && !isClockOnFile(phone))" in SHELL_JS
    assert "if (phone && !isClockOnFile(phone)) {" in SHELL_JS
    assert "if (!zone || isClockOnFile(zone)) return;" in SHELL_JS
    assert "phone !== shellWho.timezone" not in SHELL_JS


def test_today_does_not_ask_a_calcutta_phone_about_a_kolkata_household():
    """CATCH, run in node: the one-time ask (for a household whose zone
    nobody answered for) stays down for the browser's old name of the zone
    on file, and still comes up for a phone that is really elsewhere."""
    import json

    import nodeharness

    def fn(name):
        start = SHELL_JS.index("  function %s(" % name)
        return SHELL_JS[start:SHELL_JS.index("\n  }\n", start) + 4]

    out = json.loads(nodeharness.run_node(fn("isClockOnFile") + fn("clockAskZone") + """
var PHONE = '';
function phoneTimeZone() { return PHONE; }
var shellWho = { loaded: true, timezone_set: false, timezone: 'Asia/Kolkata', timezone_aliases: ['Asia/Calcutta'] };
var r = {};
PHONE = 'Asia/Calcutta'; r.alias = clockAskZone();
PHONE = 'Asia/Kolkata'; r.same = clockAskZone();
PHONE = 'America/Vancouver'; r.elsewhere = clockAskZone();
console.log(JSON.stringify(r));
""", timeout=30).stdout)
    assert out == {"alias": "", "same": "", "elsewhere": "America/Vancouver"}
