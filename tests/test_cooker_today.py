"""
Cooker View opens on today's meal, not on Monday breakfast.

The whole week renders on one page, so without this you arrive at the top
and scroll past everything to reach what you're actually cooking — on a
phone, one-handed, mid-cook. Which meal it lands on is decided by
`todaysMealIndex` in static/cooker.html.

These run that real function under node rather than checking the source
text for keywords. The repo has no JavaScript test harness, but this
particular function is pure — meals in, index out — so it can be lifted
out and executed, which makes these behaviour tests rather than the
"assert the code mentions the right word" kind. `node` is present on the
GitHub runner the workflow uses; if it ever isn't, these skip rather than
fail, and the browser verification stands behind them.

The rule under test is deliberately clock-driven, not checkbox-driven.
"The next meal nobody has ticked off" sounds more correct and is worse in
practice: households reliably forget to check breakfast off, so that rule
parks the screen on breakfast all day. Time of day is true whether or not
anyone kept the checkboxes tidy.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
from pathlib import Path

import pytest


COOKER = Path(__file__).resolve().parent.parent / "static" / "cooker.html"
TODAY = datetime.date.today().isoformat()
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the page's own function"
)


def _extract(name: str, source: str) -> str:
    """Lift one brace-balanced `function name(...) {...}` out of the page."""
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _pick(meals: list[dict], hour: int):
    """Run the page's real todaysMealIndex under node; returns index or None."""
    src = COOKER.read_text()
    harness = (
        "const SLOT_ORDER = ['breakfast','lunch','dinner','snack'];\n"
        + _extract("todayIso", src)
        + "\n"
        + _extract("currentSlotIndex", src)
        + "\n"
        + _extract("todaysMealIndex", src)
        + "\n"
        + f"const out = todaysMealIndex({json.dumps(meals)}, {hour});\n"
        + "console.log(JSON.stringify(out));\n"
    )
    res = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _meal(date, slot, name, cooked="pending"):
    return {"date": date, "slot": slot, "meal": name, "cooked_status": cooked}


FULL_DAY = [
    _meal(YESTERDAY, "dinner", "Chili", "done"),
    _meal(TODAY, "breakfast", "Toast"),
    _meal(TODAY, "lunch", "Salad"),
    _meal(TODAY, "dinner", "Curry"),
]


@pytest.mark.parametrize(
    "hour, expected",
    [(6, "Toast"), (10, "Toast"), (11, "Salad"), (15, "Salad"), (16, "Curry"), (21, "Curry")],
)
def test_it_opens_on_the_meal_you_are_actually_about_to_cook(hour, expected):
    idx = _pick(FULL_DAY, hour)
    assert idx is not None
    assert FULL_DAY[idx]["meal"] == expected, (
        f"at {hour}:00 the screen should open on {expected}, got {FULL_DAY[idx]['meal']}"
    )


def test_an_unticked_breakfast_does_not_trap_the_screen_all_day():
    """
    The reason this is clock-driven. Breakfast is still marked pending at
    7pm, because nobody checks breakfast off — and the screen must still
    open on dinner.
    """
    idx = _pick(FULL_DAY, 19)
    assert FULL_DAY[idx]["meal"] == "Curry", (
        "an uncooked earlier meal pulled the screen backwards; that is the "
        "checkbox-driven rule this one exists to avoid"
    )


def test_it_skips_past_a_meal_already_cooked():
    meals = [
        _meal(TODAY, "breakfast", "Toast", "done"),
        _meal(TODAY, "lunch", "Salad", "done"),
        _meal(TODAY, "dinner", "Curry"),
    ]
    assert meals[_pick(meals, 12)]["meal"] == "Curry", (
        "lunch is already cooked at noon, so the next thing is dinner"
    )


def test_when_today_is_finished_it_still_lands_on_today():
    """Not back at the start of the week, which is what no scroll would do."""
    meals = [
        _meal(YESTERDAY, "dinner", "Chili", "done"),
        _meal(TODAY, "breakfast", "Toast", "done"),
        _meal(TODAY, "dinner", "Curry", "done"),
    ]
    idx = _pick(meals, 21)
    assert meals[idx]["date"] == TODAY, "landed on a day that isn't today"


def test_nothing_planned_today_forces_no_scroll():
    """A week with no meals today should be left exactly where it opens."""
    assert _pick([_meal(YESTERDAY, "dinner", "Chili", "done")], 19) is None
    assert _pick([], 19) is None


def test_an_unknown_slot_name_does_not_break_the_ordering():
    """
    Slots come from the model via the plan, so an unexpected value is a
    real possibility. It should sort last rather than throw or win.
    """
    meals = [
        _meal(TODAY, "dinner", "Curry"),
        _meal(TODAY, "brunch", "Eggs"),
    ]
    idx = _pick(meals, 19)
    assert idx is not None
    assert meals[idx]["meal"] == "Curry", "an unrecognised slot outranked a real one"
