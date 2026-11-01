"""
Loop Board "a cold packed lunch is offered 'Last night's dinner' on Plan the
Week" — Emily, 2026-11-01: when ANYONE at a weekday lunch packs cold, not
only when everyone does.

The server's rule is anyone (draft_rules.enforce_cold_packed_lunches: a
lunch whose people include one Cold packed with no warm container, and
which reheats last night's dinner, is re-picked cold). The lunch is ONE
slot for everyone at it (member_needs.generation_context: "ONE dish must
suit all of them"), and the re-pick replaces that slot — so on a mixed day
the person at home has the cold lunch too, not leftovers. The screen says
exactly that:

  - a mixed day opens on no leftovers, like an everyone-cold day;
  - its cooked lunch reads "Cold, made that morning" (not "Packed cold",
    since someone at the lunch isn't packing cold — at home, or a thermos);
  - tapped to leftovers anyway: "I'll swap this for one cold lunch for
    everyone, since Maya's is packed."
"""
from __future__ import annotations

import json
import shutil

import pytest

import nodeharness
from pathlib import Path

from test_weekday_lunches import PAGE, _extract, _var

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")

_FUNCS = ("isoLocal", "addDaysIso", "isoWeekday", "titleDay", "isWeekdayIso", "prevIso", "prepDayFor",
          "leftoversOk", "lunchPrefill", "lunchLine", "coldPackers", "allColdPacked", "coldDay",
          "coldSwapNote", "lunchColdHold")

WEEK = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
PERIOD = WEEK + ["2026-10-10", "2026-10-11"]
OK = f"function (d) {{ return leftoversOk(d, {json.dumps(PERIOD)}, null); }}"
ROTATION = {"choice": "few_in_rotation", "dishes": 3}
COLD = {"needs": ["cold_packed"]}
NEEDS = {"Maya": COLD, "Sam": {"needs": []}}


def _node(script: str):
    prelude = (_var("LUNCH_KINDS") + _var("LUNCH_KEEP_DAYS") + _var("PREP_WEEKDAYS") + _var("WARM_LUNCH_NEEDS")
               + "\n".join(_extract(f) for f in _FUNCS) + "\n")
    res = nodeharness.run_node(prelude + f"console.log(JSON.stringify({script}));", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _day(eating, needs=NEEDS, weekday="thursday"):
    return _node(f"coldDay({json.dumps(eating)}, {json.dumps(needs)}, '{weekday}')")


def test_anyone_packing_cold_makes_it_a_cold_day():
    assert _day(["Maya", "Sam"]) == {"who": ["Maya"], "all": False}
    assert _day(["Maya"]) == {"who": ["Maya"], "all": True}
    assert _day(["Sam"]) is None
    assert _day([]) is None
    # A warm container ticked: any works, so that person isn't cold (server's WARM_NEEDS).
    assert _day(["Maya", "Sam"], {"Maya": {"needs": ["cold_packed", "thermos"]}}) is None
    # Day by day wins on its day.
    byday = {"Maya": {"needs": ["cold_packed"], "days": {"thursday": []}}}
    assert _day(["Maya", "Sam"], byday) is None
    assert _day(["Maya", "Sam"], byday, "friday") == {"who": ["Maya"], "all": False}


def test_a_mixed_day_opens_with_no_leftovers():
    # The page's coldPackedDay hands lunchColdHold a coldDay per date.
    cold = f"function (d) {{ return coldDay(['Maya', 'Sam'], {json.dumps(NEEDS)}, isoWeekday(d)); }}"
    got = _node(f"lunchColdHold(lunchPrefill({json.dumps(WEEK)}, null, {OK}, [], {json.dumps(ROTATION)}), "
                f"{json.dumps(WEEK)}, {cold}, [])")
    assert set(got["kinds"].values()) == {"cooked"}
    got = _node(f"lunchColdHold(lunchPrefill({json.dumps(WEEK)}, null, {OK}, ['sunday'], {json.dumps(ROTATION)}), "
                f"{json.dumps(WEEK)}, {cold}, ['sunday'])")
    assert [got["kinds"][d] for d in WEEK] == ["cooked", "cooked", "cooked", "prepped", "prepped"]


def test_the_mixed_day_lines():
    mixed = json.dumps({"who": ["Maya"], "all": False})
    everyone = json.dumps({"who": ["Maya"], "all": True})
    assert _node(f"lunchLine('{WEEK[3]}', 'cooked', [], null, {mixed})") == "Cold, made that morning"
    assert _node(f"lunchLine('{WEEK[3]}', 'cooked', [], null, {everyone})") == "Packed cold, made that morning"
    assert _node(f"lunchLine('{WEEK[3]}', 'cooked', [], null, null)") == "Cooked that day"
    assert _node(f"coldSwapNote({mixed})") == "I’ll swap this for one cold lunch for everyone, since Maya’s is packed."
    assert _node("coldSwapNote({who: ['Maya', 'Theo'], all: false})") == \
        "I’ll swap this for one cold lunch for everyone, since Maya’s and Theo’s are packed."
    assert _node(f"coldSwapNote({everyone})") == "I’ll swap this for a cold lunch, since it’s packed."
    # The row's note is the one coldSwapNote says.
    assert "note.textContent = coldSwapNote(cold);" in PAGE


SETUP_JS = (Path(__file__).resolve().parent.parent / "static" / "setup-screens.js").read_text(encoding="utf-8")


def _cold_packed_day(iso, away=()):
    # The page's own coldPackedDay over the week's grid (lunch: everyone),
    # step 2's day sheet and the week's needs, stubbed.
    stubs = (
        "function memberNames() { return ['Maya', 'Sam']; }\n"
        "function weekCells() { return { lunch: ['all', 'all', 'all', 'all', 'all', 'off', 'off'] }; }\n"
        "function weekNeeds() { return " + json.dumps(NEEDS) + "; }\n"
        "var attendance = { byDate: " + json.dumps({iso: {"lunch": {"absent_names": list(away)}}}) + " };\n"
        + _extract("weekdayIndex") + "\n" + _extract("uwCleanCell", SETUP_JS) + "\n"
        + _extract("coldPackedDay") + "\n"
    )
    return _node("(function () {\n" + stubs + f"return coldPackedDay('{iso}');\n}})()")


def test_the_page_reads_a_mixed_day_as_cold():
    # Maya packs cold, Sam is at home: a cold day, not everyone's.
    assert _cold_packed_day(WEEK[3]) == {"who": ["Maya"], "all": False}
    # Maya away that day: nobody at it packs cold.
    assert _cold_packed_day(WEEK[3], away=["Maya"]) is None
    # Sam away: everyone at it packs cold.
    assert _cold_packed_day(WEEK[3], away=["Sam"]) == {"who": ["Maya"], "all": True}
