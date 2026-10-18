"""
Plan a week's step 3 ("Weekday lunches") neither counts nor asks about a
lunch the household's usual week has off (defect hunt, 2026-10-16).

Found walking Plan a week for a two-adult household that does not plan
lunch (lunches_per_week 0, or every lunch "off" in the usual week): step 3
said "5 lunches, Monday to Friday", "Same as last week?" said "Weekday
lunches · 5 cooked on the day", and the draft had no lunches at all — the
planner writes a usual-week-off slot empty whatever step 3 said
(usual_week.settle_off_slots / the zero-count pass). A grid with lunch on
Monday to Wednesday only was asked about all five the same way, and its
Thursday and Friday answers were dropped.

The prefill now carries `usual_week_off` ([{date, slot}] for the period's
lunches and dinners), and the page leaves those lunches out of step 3 —
skipping the step when none is left — and never offers a lunch as
leftovers of a dinner the usual week has off.
"""
from __future__ import annotations

import datetime
import json
import shutil

import pytest

import nodeharness
from conftest import household_today
from app import tools
from app.tools import usual_week

import test_weekday_lunches as wl

WEEKDAYS = usual_week.WEEKDAYS

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _monday(offset_weeks: int = 1) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _plus(iso: str, n: int) -> str:
    return (datetime.date.fromisoformat(iso) + datetime.timedelta(days=n)).isoformat()


# ---------- server: what the prefill says ----------

def test_a_household_with_no_lunches_has_every_lunch_of_the_period_off(signed_in):
    tools.set_household_meal_preferences(lunches_per_week=0)
    mon = _monday()
    body = signed_in.get(f"/api/week/{mon}/intake").json()
    off = body["usual_week_off"]
    assert {o["date"] for o in off if o["slot"] == "lunch"} == {_plus(mon, i) for i in range(7)}
    assert not [o for o in off if o["slot"] == "dinner"]


def test_a_grid_says_exactly_the_days_it_has_off():
    grid = {
        "lunch": {d: ("off" if d in ("thursday", "friday") else "everyone") for d in WEEKDAYS},
        "dinner": {d: ("off" if d == "tuesday" else "everyone") for d in WEEKDAYS},
    }
    tools.save_usual_week(grid=grid)
    mon = _monday()
    off = tools.get_week_intake_prefill(mon)["usual_week_off"]
    assert sorted((o["date"], o["slot"]) for o in off) == sorted([
        (_plus(mon, 1), "dinner"),
        (_plus(mon, 3), "lunch"), (_plus(mon, 4), "lunch"),
    ])


def test_a_count_set_to_zero_after_the_grid_wins_as_the_planner_reads_it():
    """Review, 2026-10-16: lunch Mon-Wed in a saved grid, then lunches set to
    0 from chat or What we know. The grid keeps Mon-Wed on, but the
    planner's zero-count pass reads the count and empties every lunch — so
    step 3 must not ask about Mon-Wed either."""
    grid = {
        "lunch": {d: ("everyone" if d in ("monday", "tuesday", "wednesday") else "off") for d in WEEKDAYS},
        "dinner": {d: "everyone" for d in WEEKDAYS},
    }
    tools.save_usual_week(grid=grid)
    tools.set_household_meal_preferences(lunches_per_week=0)
    mon = _monday()
    off = tools.get_week_intake_prefill(mon)["usual_week_off"]
    assert sorted(o["date"] for o in off if o["slot"] == "lunch") == [_plus(mon, i) for i in range(7)]
    assert not [o for o in off if o["slot"] == "dinner"]


def test_a_household_that_never_said_has_nothing_off_and_a_long_period_is_covered():
    mon = _monday()
    assert tools.get_week_intake_prefill(mon)["usual_week_off"] == []
    tools.set_household_meal_preferences(lunches_per_week=0)
    off = tools.get_week_intake_prefill(mon, day_count=10)["usual_week_off"]
    assert len(off) == 10 and off[-1] == {"date": _plus(mon, 9), "slot": "lunch"}


# ---------- the page: step 3 reads it ----------

# Mon 28 Sep – Sun 4 Oct 2026.
PERIOD = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]


def _page(script: str, off: list[tuple[str, str]], out_names: dict | None = None) -> object:
    names = ("usualWeekOff", "noMealFor", "nobodyHomeFor", "dinnerOff", "currentLunchDates", "lunchOk")
    stubs = (
        "var data = " + json.dumps({
            "days": [{"date": d} for d in PERIOD],
            "usual_week_off": [{"date": d, "slot": s} for d, s in off],
        }) + ";\n"
        "var attendance = { members: [{name: 'Maya'}, {name: 'Theo'}], byDate: "
        + json.dumps(out_names or {}) + " };\n"
        "var answers = { night_tags: {} };\n"
        "function plannedDays() { return data.days; }\n"
    )
    prelude = (
        wl._var("PREP_WEEKDAYS") + stubs
        + "\n".join(wl._extract(f) for f in ("isoLocal", "addDaysIso", "isWeekdayIso", "prevIso",
                                             "lunchDates", "leftoversOk"))
        + "\n" + "\n".join(wl._extract(f) for f in names) + "\n"
    )
    res = nodeharness.run_node(prelude + f"console.log(JSON.stringify({script}));", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_step_three_leaves_out_the_lunches_the_usual_week_has_off():
    off = [(d, "lunch") for d in PERIOD[3:]]
    assert _page("currentLunchDates()", off) == PERIOD[:3]
    # Every lunch off: nothing left, so skipsStep(3) skips the step and
    # "Same as last week?" has no lunch rows (both read currentLunchDates).
    assert _page("currentLunchDates()", [(d, "lunch") for d in PERIOD]) == []
    # Nothing off is the five weekdays it always was.
    assert _page("currentLunchDates()", []) == PERIOD[:5]


@_needs_node
def test_nobody_home_still_counts_and_a_dinner_off_is_not_offered_as_leftovers():
    away = {PERIOD[1]: {"lunch": {"absent_names": ["Maya", "Theo"]}}}
    assert _page("currentLunchDates()", [(PERIOD[4], "lunch")], away) == [PERIOD[0], PERIOD[2], PERIOD[3]]
    # Tuesday's dinner is off in the usual week: Wednesday's lunch can't be
    # Tuesday's leftovers; Thursday's (Wednesday's dinner) still can.
    off = [(PERIOD[1], "dinner")]
    assert _page(f"[lunchOk('{PERIOD[2]}'), lunchOk('{PERIOD[3]}')]", off) == [False, True]
