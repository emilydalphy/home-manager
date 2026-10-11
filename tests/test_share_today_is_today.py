"""
The share page's "Today's Table" is today on the phone holding it
(defect hunt, 2026-10-11).

share.html worked out today with toISOString(), which is UTC. From 8pm in
Toronto (EDT; 7pm in winter) UTC is already tomorrow, so a grandparent
opening the link at 9pm on Wednesday saw Thursday's dinner under "Today's
Table", and on Sunday evening — tomorrow being outside the week — the page
fell back to Monday. Reproduced in Chromium (timezoneId America/Toronto,
clock fixed at 21:00).

Runs the page's own todayIsoDate / pickDefaultDayIndex under node with the
clock pinned.
"""
from __future__ import annotations

import json
import pathlib
import shutil

import pytest

import nodeharness

PAGE = (pathlib.Path(__file__).resolve().parent.parent / "static" / "share.html").read_text(encoding="utf-8")

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


MENU = [{"date": f"2026-10-{d:02d}", "dinner": f"Dinner {d}"} for d in range(5, 12)]  # Mon 5 .. Sun 11


def _at(tz: str, instant: str) -> dict:
    script = f"""
    process.env.TZ = {json.dumps(tz)};
    const RealDate = Date;
    const PIN = new RealDate({json.dumps(instant)}).getTime();
    Date = class extends RealDate {{
      constructor(...a) {{ if (a.length) super(...a); else super(PIN); }}
      static now() {{ return PIN; }}
    }};
    const SLOT_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'];
    {_extract("todayIsoDate")}
    {_extract("pickDefaultDayIndex")}
    const menu = {json.dumps(MENU)};
    console.log(JSON.stringify({{ today: todayIsoDate(), opens_on: menu[pickDefaultDayIndex(menu)].date }}));
    """
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_nine_pm_wednesday_in_toronto_is_still_wednesday():
    got = _at("America/Toronto", "2026-10-07T21:00:00-04:00")
    assert got == {"today": "2026-10-07", "opens_on": "2026-10-07"}


@_needs_node
def test_sunday_evening_opens_on_sunday_not_back_on_monday():
    got = _at("America/Toronto", "2026-10-11T21:30:00-04:00")
    assert got == {"today": "2026-10-11", "opens_on": "2026-10-11"}


@_needs_node
def test_early_morning_east_of_greenwich_is_already_today():
    # 00:30 in Berlin is still yesterday in UTC — the mirror image.
    got = _at("Europe/Berlin", "2026-10-08T00:30:00+02:00")
    assert got == {"today": "2026-10-08", "opens_on": "2026-10-08"}


def test_the_page_never_reads_a_date_back_through_utc():
    assert ".toISOString().slice(0, 10)" not in PAGE
