"""
Loop Board "a cold packed lunch is offered 'Last night's dinner' on Plan the
Week, then quietly swapped for something cold" (Emily, 2026-10-31).

A household set to "A few in rotation" whose lunches are all Cold packed
opened step 3 on Thursday and Friday as "Last night's dinner" — and the
draft then re-picked both cold (draft_rules.enforce_cold_packed_lunches,
the 2026-10-06 rule, which stands). On a day everyone at lunch is Cold
packed with no warm container ticked, plan-week.html now:

  1. never fills in leftovers — made on a prep day where the household
     preps, else cooked that day (lunchColdHold over lunchPrefill);
  2. says a cooked lunch is "Packed cold, made that morning" (lunchLine);
  3. says "I'll swap this for a cold lunch, since it's packed." on a day
     tapped to leftovers anyway (paintLunchStep).
"""
from __future__ import annotations

import json
import shutil

import pytest

import nodeharness
from test_weekday_lunches import PAGE, _extract, _var

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")

_FUNCS = ("isoLocal", "addDaysIso", "isoWeekday", "titleDay", "isWeekdayIso", "prevIso", "prepDayFor",
          "leftoversOk", "lunchPrefill", "lunchLine", "coldPackers", "allColdPacked", "lunchColdHold")

# Mon 5 – Fri 9 Oct 2026, the period Monday to Sunday.
WEEK = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
PERIOD = WEEK + ["2026-10-10", "2026-10-11"]
OK = f"function (d) {{ return leftoversOk(d, {json.dumps(PERIOD)}, null); }}"
ROTATION = {"choice": "few_in_rotation", "dishes": 3}


def _node(script: str):
    prelude = (_var("LUNCH_KINDS") + _var("LUNCH_KEEP_DAYS") + _var("PREP_WEEKDAYS") + _var("WARM_LUNCH_NEEDS")
               + "\n".join(_extract(f) for f in _FUNCS) + "\n")
    res = nodeharness.run_node(prelude + f"console.log(JSON.stringify({script}));", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _opening(cold_dates, prep=(), usual=ROTATION):
    cold = f"function (d) {{ return {json.dumps(list(cold_dates))}.indexOf(d) !== -1; }}"
    return _node(f"lunchColdHold(lunchPrefill({json.dumps(WEEK)}, null, {OK}, {json.dumps(list(prep))}, "
                 f"{json.dumps(usual)}), {json.dumps(WEEK)}, {cold}, {json.dumps(list(prep))})")


def test_the_walk_a_few_in_rotation_all_cold_packed_opens_with_no_leftovers():
    # Before: Mon–Wed cooked, Thu–Fri "Last night's dinner".
    assert _opening([])["kinds"][WEEK[4]] == "leftovers"
    got = _opening(WEEK)
    assert set(got["kinds"].values()) == {"cooked"} and got["prepDays"] == []


def test_a_household_that_preps_gets_made_on_a_prep_day():
    got = _opening(WEEK, prep=["sunday"])
    assert [got["kinds"][d] for d in WEEK] == ["cooked", "cooked", "cooked", "prepped", "prepped"]
    assert got["prepDays"] == ["sunday"]
    # "Last night's dinner" as the usual answer, too.
    got = _opening(WEEK, prep=["sunday"], usual={"choice": "last_nights_dinner", "dishes": 1})
    assert "leftovers" not in got["kinds"].values()


def test_only_the_cold_days_move():
    got = _opening([WEEK[4]])
    assert got["kinds"][WEEK[3]] == "leftovers" and got["kinds"][WEEK[4]] == "cooked"


def test_who_counts_as_cold_packed():
    cold = {"needs": ["cold_packed"]}
    assert _node(f"allColdPacked(['Maya'], {json.dumps({'Maya': cold})}, 'monday')") is True
    # A warm container ticked: any of them works, so leftovers can go.
    warm = {"needs": ["cold_packed", "thermos"]}
    assert _node(f"allColdPacked(['Maya'], {json.dumps({'Maya': warm})}, 'monday')") is False
    # Everyone eating: an adult at home too is not everyone (since 2026-11-01
    # the screen holds anyone's day — tests/test_cold_packed_anyone.py).
    assert _node(f"allColdPacked(['Maya', 'Sam'], {json.dumps({'Maya': cold})}, 'monday')") is False
    # A day-by-day answer wins on its day, even an empty one.
    byday = {"needs": ["cold_packed"], "days": {"friday": []}}
    assert _node(f"allColdPacked(['Maya'], {json.dumps({'Maya': byday})}, 'friday')") is False
    assert _node(f"allColdPacked(['Maya'], {json.dumps({'Maya': byday})}, 'thursday')") is True
    assert _node("allColdPacked([], {}, 'monday')") is False


def test_the_lines():
    assert _node(f"lunchLine('{WEEK[2]}', 'cooked', [], null, true)") == "Packed cold, made that morning"
    assert _node(f"lunchLine('{WEEK[2]}', 'cooked', [], null, false)") == "Cooked that day"
    assert _node(f"lunchLine('{WEEK[2]}', 'leftovers', [], null, true)") == "Last night’s dinner"
    assert "I’ll swap this for a cold lunch, since it’s packed." in PAGE
    # All three doors into the step (fresh, carried, variety tap) hold the prefill.
    assert PAGE.count("lunchColdHold(lunchPrefill(") == 3
