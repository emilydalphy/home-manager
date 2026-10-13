"""
The household's clock is asked for, not assumed.

Loop Board "Nothing ever asks the household where they live, so everyone's
clock is Toronto" — Emily, 2026-09-21 (take it from the phone, confirm it in
one line, travel never re-zones, an existing household asked once only if the
phone disagrees) and 2026-10-12 ("yes to the written proposal: ask on the
dinner-time step, with the was-it-ever-answered flag").
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import db, tools
from app.db import get_conn

ROOT = Path(__file__).resolve().parents[1]
ONBOARDING = (ROOT / "static" / "onboarding.html").read_text()
SHELL_JS = (ROOT / "static" / "shell.js").read_text()


def _row():
    conn = get_conn()
    row = conn.execute("SELECT timezone, timezone_set FROM households WHERE id = ?",
                       (tools.household_id(),)).fetchone()
    conn.close()
    return row["timezone"], row["timezone_set"]


def test_a_new_household_is_on_the_default_and_never_answered(signed_in):
    assert _row() == ("America/Toronto", 0)
    who = signed_in.get("/api/whoami").json()
    assert who["timezone"] == "America/Toronto" and who["timezone_set"] is False


def test_setup_saves_the_confirmed_zone_as_an_answer(signed_in):
    res = signed_in.post("/api/onboarding/rhythm", json={"dinner_window": "6_8", "timezone": "America/Vancouver"})
    assert res.status_code == 200, res.text
    assert _row() == ("America/Vancouver", 1)
    assert signed_in.get("/api/whoami").json()["timezone_set"] is True


def test_an_unknown_zone_refuses_the_whole_rhythm_save(signed_in):
    res = signed_in.post("/api/onboarding/rhythm", json={"dinner_window": "6_8", "timezone": "Mars/Olympus"})
    assert res.status_code == 400
    assert "I don't know the time zone" in res.json()["detail"]
    assert _row() == ("America/Toronto", 0)
    assert tools.get_household_rhythm()["dinner_window"] is None, "nothing half-saved"


def test_keeping_the_zone_on_file_is_an_answer_too(signed_in):
    """The one-time ask's "Keep Toronto": the zone stays, and the ask never
    fires again — so the same phone on a trip is never asked."""
    res = signed_in.post("/api/household/clock", json={"keep": True})
    assert res.status_code == 200
    assert res.json() == {"timezone": "America/Toronto", "timezone_set": True}
    assert _row() == ("America/Toronto", 1)


def test_the_clock_route_sets_a_zone_and_refuses_one_it_does_not_know(signed_in):
    assert signed_in.post("/api/household/clock", json={"timezone": "America/Denver"}).json() == {
        "timezone": "America/Denver", "timezone_set": True}
    bad = signed_in.post("/api/household/clock", json={"timezone": "Nowhere"})
    assert bad.status_code == 400
    assert _row() == ("America/Denver", 1)


def test_the_morning_text_zone_is_an_answer_and_uses_the_one_validator():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    member_id = tools.household_adults()[0]["id"]
    from app.tools import digest
    digest.set_morning_text_for_member(member_id, timezone="America/Chicago")
    assert _row() == ("America/Chicago", 1)
    with pytest.raises(ValueError, match="I don't know the time zone"):
        digest.set_morning_text_for_member(member_id, timezone="Not/AZone")


def test_a_zone_somebody_already_chose_is_backfilled_as_answered():
    conn = get_conn()
    conn.execute("UPDATE households SET timezone = 'America/Halifax', timezone_set = 0 WHERE id = ?",
                 (tools.household_id(),))
    db._backfill_timezone_set(conn)
    conn.commit()
    conn.close()
    assert _row() == ("America/Halifax", 1)

    conn = get_conn()
    conn.execute("UPDATE households SET timezone = 'America/Toronto', timezone_set = 0 WHERE id = ?",
                 (tools.household_id(),))
    db._backfill_timezone_set(conn)
    conn.commit()
    conn.close()
    assert _row() == ("America/Toronto", 0), "the default is the question, not an answer"


def test_setup_sends_the_clock_only_when_the_step_was_continued():
    assert "if (clockSaid && clockZone) body.timezone = clockZone;" in ONBOARDING
    assert '<p class="uw-sum" id="tz-line" hidden></p>' in ONBOARDING
    # Today asks only a household nobody answered for, and only when the
    # phone disagrees with the zone on file.
    ask = SHELL_JS[SHELL_JS.index("function clockAskZone()"):]
    ask = ask[:ask.index("\n  }\n")]
    assert "if (!shellWho.loaded || shellWho.timezone_set) return '';" in ask
    assert "phone !== shellWho.timezone" in ask


def test_continue_with_the_city_box_open_never_saves_a_zone_nobody_confirmed():
    """Review 2026-10-13: Change → an unknown city (or nothing) → Continue
    used to save the PHONE's zone as answered, so Today never asked again."""
    import json

    import nodeharness

    def fn(name):
        start = ONBOARDING.index("function %s(" % name)
        return ONBOARDING[start:ONBOARDING.index("\n}\n", start) + 3]

    start = ONBOARDING.index("document.getElementById('dinner-time-next').onclick")
    handler = ONBOARDING[start:ONBOARDING.index("\n};\n", start) + 4]
    out = json.loads(nodeharness.run_node(fn("zoneForCity") + fn("takeTypedCity") + """
var EL = { 'tz-input': { value: '' }, 'tz-note': { hidden: true, textContent: '' }, 'dinner-time-next': {} };
var document = { getElementById: function (id) { return EL[id]; } };
var clockZone = 'America/Vancouver', clockAsking = true, clockSaid = false, went = 0;
function buildClockLine() {}
function goForward() { went++; }
""" + handler + """
var r = {};
EL['tz-input'].value = 'Ottawa'; EL['dinner-time-next'].onclick();
r.unknown = { went: went, said: clockSaid, note: !EL['tz-note'].hidden, zone: clockZone };
EL['tz-input'].value = ''; EL['dinner-time-next'].onclick();
r.empty = { went: went, said: clockSaid, zone: clockZone };
clockAsking = true; EL['tz-input'].value = 'Denver'; EL['dinner-time-next'].onclick();
r.ok = { went: went, said: clockSaid, zone: clockZone };
console.log(JSON.stringify(r));
""", timeout=30).stdout)
    assert out["unknown"] == {"went": 0, "said": False, "note": True, "zone": "America/Vancouver"}, "stays on the step"
    assert out["empty"] == {"went": 1, "said": False, "zone": "America/Vancouver"}, "moves on, sends no zone"
    assert out["ok"] == {"went": 2, "said": True, "zone": "America/Denver"}


@pytest.mark.parametrize("name", ["localtime", "posixrules", "Factory"])
def test_files_that_are_not_places_are_not_a_clock(name):
    with pytest.raises(ValueError, match="I don't know the time zone"):
        tools.checked_timezone(name)
