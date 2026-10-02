"""
QA walk 2 (2026-10-02), two Loop Board cards:

1. "Plan next week: the lunch step ignores "a few in rotation", then the
   draft quietly turns Thursday's lunch into Tuesday's leftovers."
   - Step 3 (plan-week.html lunchPrefill) opens on the household's saved
     lunch variety when neither this week nor last week answered it — the
     server hands it over as usual_lunch.
   - A lunch the household answered "Cooked that day" is never left a
     reheat of an earlier cook (weekday_lunches._uncook_cooked_lunches).

2. ""The rice would end up after its leftovers" — the short dish name picks
   the side, not the dish." meal_move.short_name and shell.js
   dishShortName say the main dish: the title before " with ", brackets out.

Every test here fails on main (926bab4).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import agent, tools
from app.tools import meal_move, usual_week

from test_weekday_lunches import (  # noqa: F401  (fixtures)
    _extract, _monday, _rows, _slot, _var, seen_context, two_adults,
)

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page's own functions")

_FUNCS = ("isoLocal", "addDaysIso", "isoWeekday", "titleDay", "isWeekdayIso", "prevIso", "prepDayFor",
          "leftoversOk", "lunchPrefill")

# Mon 5 – Fri 9 Oct 2026, the period Monday to Sunday.
WEEK = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
PERIOD = WEEK + ["2026-10-10", "2026-10-11"]
OK = f"function (d) {{ return leftoversOk(d, {json.dumps(PERIOD)}, null); }}"


def _node(script: str):
    prelude = (_var("LUNCH_KINDS") + _var("LUNCH_KEEP_DAYS") + _var("PREP_WEEKDAYS")
               + "\n".join(_extract(f) for f in _FUNCS) + "\n")
    res = nodeharness.run_node(prelude + f"console.log(JSON.stringify({script}));", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _prefill(dates, usual, carry=None, prep=()):
    return _node(f"lunchPrefill({json.dumps(dates)}, {json.dumps(carry)}, {OK}, {json.dumps(list(prep))}, "
                 f"{json.dumps(usual)})")


# ==========================================================================
# Card 1a — the step opens on the saved lunch variety
# ==========================================================================

class TestTheStepOpensOnTheVarietyAnswer:
    def test_the_server_hands_the_screen_the_lunch_variety(self):
        usual_week.save_usual_week(variety={"lunch": "few_in_rotation"})
        got = tools.get_week_intake_prefill(_monday())["usual_lunch"]
        assert got == {"choice": "few_in_rotation", "dishes": 3}

    def test_never_answered_is_no_choice(self):
        assert tools.get_week_intake_prefill(_monday())["usual_lunch"]["choice"] is None

    def test_both_doors_into_the_step_pass_it(self):
        assert PAGE.count("data.rhythm_prep_days, data.usual_lunch)") == 2

    @_needs_node
    def test_a_few_in_rotation_cooks_that_many_and_the_rest_are_dinner_leftovers(self):
        # The walk: Wednesday away, four lunches, "a few in rotation" = 3.
        four = [WEEK[0], WEEK[1], WEEK[3], WEEK[4]]
        got = _prefill(four, {"choice": "few_in_rotation", "dishes": 3})
        assert got["kinds"] == {WEEK[0]: "cooked", WEEK[1]: "cooked", WEEK[3]: "cooked", WEEK[4]: "leftovers"}
        got = _prefill(WEEK, {"choice": "few_in_rotation", "dishes": 3})
        assert list(got["kinds"].values()).count("cooked") == 3
        assert got["kinds"][WEEK[3]] == got["kinds"][WEEK[4]] == "leftovers"

    @_needs_node
    def test_a_few_in_rotation_with_a_prep_day_is_still_the_rotation(self):
        got = _prefill(WEEK, {"choice": "few_in_rotation", "dishes": 3}, prep=["sunday"])
        assert "prepped" not in got["kinds"].values() and got["prepDays"] == []

    @_needs_node
    def test_last_nights_dinner_is_leftovers_where_there_is_a_dinner(self):
        got = _prefill(WEEK, {"choice": "last_nights_dinner", "dishes": 1})
        # Monday's evening before is outside the period: cooked.
        assert got["kinds"] == {WEEK[0]: "cooked", **{d: "leftovers" for d in WEEK[1:]}}

    @_needs_node
    def test_meal_prep_ahead_and_new_every_day(self):
        got = _prefill(WEEK, {"choice": "meal_prep_ahead", "dishes": 2}, prep=["sunday"])
        assert set(got["kinds"].values()) == {"prepped"} and got["prepDays"] == ["sunday"]
        got = _prefill(WEEK, {"choice": "new_every_day", "dishes": 7}, prep=["sunday"])
        assert set(got["kinds"].values()) == {"cooked"}

    @_needs_node
    def test_last_weeks_own_answer_still_wins(self):
        carry = {"prep_days": [], "kinds": {d: "cooked" for d in
                                            ("monday", "tuesday", "wednesday", "thursday", "friday")}}
        got = _prefill(WEEK, {"choice": "last_nights_dinner", "dishes": 1}, carry=carry)
        assert set(got["kinds"].values()) == {"cooked"}


# ==========================================================================
# Card 1b — "Cooked that day" is never a quiet reheat
# ==========================================================================

SALAD = "Grilled Chicken and Avocado Salad"


def test_a_lunch_answered_cooked_is_not_left_a_reheat(two_adults, seen_context):
    seen, stub = seen_context
    tools.set_household_meal_preferences(lunches_per_week=3)
    mon = _monday()
    dates = tools._week_dates(mon)
    tools.save_week_intake(mon, weekday_lunches={"prep_days": [], "days": [
        {"date": dates[i], "kind": "cooked"} for i in (0, 1, 3, 4)
    ]})
    lunches = ["Egg Wrap", SALAD, "Soup", SALAD, "Noodle Bowl", "Pita", "Toastie"]
    dinners = ["Tacos", "Roast Chicken", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"]
    days = []
    for i, d in enumerate(dates):
        days.append(_slot(d, "breakfast", "Oats"))
        lunch = _slot(d, "lunch", lunches[i], minutes=15)
        if i == 3:
            # The model made Thursday's lunch Tuesday's leftovers.
            lunch["derived_from"] = {"links_to": f"{dates[1]}:lunch"}
        days.append(lunch)
        days.append(_slot(d, "dinner", dinners[i]))
    stub(days)

    plan_id = agent.generate_weekly_plan(mon)["weekly_plan_id"]

    lunch = _rows(plan_id, "lunch")
    chains = tools.plan_leftover_chains(plan_id)
    thu, tue = lunch[dates[3]], lunch[dates[1]]
    assert thu["slot_state"] == "planned"
    assert thu["id"] not in chains["leftovers"], "Thursday was answered cooked that day"
    assert not (thu["derived"].get("links_to") or "")
    fed = [t["date"] for t in (chains["sources"].get(tue["id"]) or {}).get("targets") or []]
    assert dates[3] not in fed
    assert f"{dates[3]}:lunch" not in (tue["derived"].get("make_double_for") or [])


# ==========================================================================
# Card 2 — the short name is the dish, not its side
# ==========================================================================

CASES = [
    ("Thai Basil Chicken (Pad Kra Pao) with Jasmine Rice", "Thai Basil Chicken"),
    ("Weeknight Spaghetti with Turkey Bolognese", "Weeknight Spaghetti"),
    ("Black Bean Tacos with Black Beans and Rice", "Black Bean Tacos"),
    ("Creamy Chicken and Vegetable Stew", "Creamy Chicken and Vegetable Stew"),
    ("Salmon Bowl WITH Side Salad", "Salmon Bowl"),
    ("Tacos", "Tacos"),
]


@pytest.mark.parametrize("title,short", CASES)
def test_the_server_names_the_main_dish(title, short):
    assert meal_move.short_name(title) == short


def test_the_server_never_says_nothing():
    assert meal_move.short_name("") == "meal" and meal_move.short_name(None) == "meal"


@_needs_node
def test_the_shell_says_the_same():
    m = re.search(r"  function dishShortName\(meal\) \{[\s\S]*?\n  \}\n", SHELL_JS)
    assert m
    script = m.group(0) + f"console.log(JSON.stringify({json.dumps([c[0] for c in CASES])}.map(dishShortName)));"
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip().splitlines()[-1]) == [c[1] for c in CASES]
