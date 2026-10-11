"""
"Away this week" covers the right days on a phone east of Greenwich
(defect hunt, 2026-10-11).

The away sheet on /plan-week turns "away from Monday after dinner, back for
Wednesday breakfast" into the meals in between. Its day step read the date
back through toISOString(), which is UTC. Local midnight is the PREVIOUS
day in UTC anywhere east of Greenwich (London in summer, all of Europe,
Asia, Australia), so the day after Monday came back as Monday: the sheet
said "Covers 100 meals — Monday breakfast through Monday breakfast" (the
loop guard), and Set this range sent Monday breakfast..Monday dinner — the
household marked away on the day they were home, and home on the day they
were gone. Reproduced at 390px in Chromium with timezoneId Europe/London.

Runs the page's own functions under node, in four zones.
"""
from __future__ import annotations

import json
import pathlib
import shutil

import pytest

import nodeharness

PAGE = (pathlib.Path(__file__).resolve().parent.parent / "static" / "plan-week.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _extract(name: str) -> str:
    start = PAGE.index(f"function {name}(")
    i = PAGE.index("{", start)
    depth, j = 0, i
    while True:
        if PAGE[j] == "{":
            depth += 1
        elif PAGE[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return PAGE[start:j + 1]


def _covered(tz: str, from_date: str, from_after: str, to_date: str, back_for: str) -> dict:
    fns = "\n".join(_extract(n) for n in (
        "isoLocal", "addDaysIso", "nextSlot", "prevSlot", "slotRank", "coveredSlots",
    ))
    script = f"""
    process.env.TZ = {json.dumps(tz)};
    var MEAL_SEQ = ['breakfast', 'lunch', 'dinner'];
    {fns}
    var first = nextSlot({{ date: {json.dumps(from_date)}, slot: {json.dumps(from_after)} }});
    var last = prevSlot({{ date: {json.dumps(to_date)}, slot: {json.dumps(back_for)} }});
    console.log(JSON.stringify({{ first: first, last: last,
      covered: coveredSlots(first, last).map(function (p) {{ return p.date + ' ' + p.slot; }}) }}));
    """
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


ZONES = ["America/Toronto", "Europe/London", "Europe/Berlin", "Australia/Sydney"]


@_needs_node
@pytest.mark.parametrize("tz", ZONES)
def test_after_dinner_to_back_for_breakfast_is_the_day_between(tz):
    # Monday 2026-10-05 after dinner, back Wednesday for breakfast: Tuesday.
    got = _covered(tz, "2026-10-05", "dinner", "2026-10-07", "breakfast")
    assert got["first"] == {"date": "2026-10-06", "slot": "breakfast"}
    assert got["last"] == {"date": "2026-10-06", "slot": "dinner"}
    assert got["covered"] == ["2026-10-06 breakfast", "2026-10-06 lunch", "2026-10-06 dinner"]


@_needs_node
@pytest.mark.parametrize("tz", ZONES)
def test_a_stretch_across_the_clocks_changing_counts_every_meal(tz):
    # Europe's clocks go back on 2026-10-25, Toronto's on 2026-11-01 and
    # Sydney's forward on 2026-10-04: Saturday after lunch to Monday lunch
    # straddles the European change.
    got = _covered(tz, "2026-10-24", "lunch", "2026-10-26", "lunch")
    assert got["covered"] == [
        "2026-10-24 dinner",
        "2026-10-25 breakfast", "2026-10-25 lunch", "2026-10-25 dinner",
        "2026-10-26 breakfast",
    ]


def test_the_page_never_reads_a_date_back_through_utc():
    assert ".toISOString().slice(0, 10)" not in PAGE
