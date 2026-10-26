"""
Once tonight's dinner is cooked, the Cook card stays on it — it does not
put the afternoon snack up to "Start cooking" (overnight e2e hunt,
2026-10-23).

Walked as a new household at phone width: Friday 5:30pm, dinner cooked
through cook mode. On the next load of Cook the main card read "FRIDAY ·
TODAY  Hummus and Carrots  START 3:00 · ON THE TABLE 3:30" with a "Start
cooking" button, and still did at 11:30pm. cookTonightIndex ranked a snack
after dinner (COOK_SLOT_ORDER), so the evening's "dinner onwards" took the
3pm snack in, and with dinner done it was the only thing left "to do".
"""
from __future__ import annotations

import shutil

import pytest

from test_plan_cards_2026_09_18 import _run
from test_week_seven_tiles import _extract
from test_plan_next_on_draft import SHELL_JS

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the functions")


def _pick(meals_js: str, hour: int):
    """cookTonightIndex over today's meals (dated with the harness's own
    today) at `hour` on the household's clock; the picked meal's slot."""
    fns = "\n".join(_extract(n, SHELL_JS) for n in (
        "todayLocalStr", "householdHourNow", "cookSlotRank",
        "cookCurrentSlotIndex", "cookTonightIndex",
    ))
    return _run(
        "var COOK_SLOT_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'];\n"
        "var shellWho = { timezone: '' };\n" + fns + "\n"
        "var T = todayLocalStr();\n"
        "var meals = " + meals_js + ";\n"
        f"var i = cookTonightIndex(meals, {hour});\n"
        "console.log(JSON.stringify(i === null ? null : meals[i].slot + ':' + meals[i].cooked_status));"
    )


_DAY = """[
  {date: T, slot: 'breakfast', cooked_status: 'pending'},
  {date: T, slot: 'lunch', cooked_status: 'pending'},
  {date: T, slot: 'snack', cooked_status: 'pending'},
  {date: T, slot: 'dinner', cooked_status: '%s'}
]"""


@_needs_node
@pytest.mark.parametrize("hour", [17, 19, 23])
def test_a_cooked_dinner_keeps_the_card_not_the_afternoon_snack(hour):
    """CATCH: on main this is 'snack:pending'."""
    assert _pick(_DAY % "done", hour) == "dinner:done"


@_needs_node
def test_an_uncooked_dinner_is_still_the_evening_card():
    assert _pick(_DAY % "pending", 18) == "dinner:pending"


@_needs_node
def test_earlier_in_the_day_nothing_moves():
    """GUARD: the morning and afternoon picks are what they were — lunch at
    noon, and once lunch is done, dinner before the snack."""
    assert _pick(_DAY % "pending", 12) == "lunch:pending"
    after_lunch = _DAY.replace("slot: 'lunch', cooked_status: 'pending'", "slot: 'lunch', cooked_status: 'done'")
    assert _pick(after_lunch % "pending", 13) == "dinner:pending"
    assert _pick(_DAY % "pending", 8) == "breakfast:pending"


_NO_DINNER = """[
  {date: T, slot: 'breakfast', cooked_status: 'pending'},
  {date: T, slot: 'lunch', cooked_status: 'pending'},
  {date: T, slot: 'snack', cooked_status: 'pending'}
]"""


@_needs_node
def test_a_night_with_no_dinner_does_not_fall_back_to_the_snack():
    """CATCH (review, 2026-10-23): dinner planned_empty — nobody home — left
    nothing from dinner on, and the fallback (the day's last meal) was the
    snack: "START 3:25 … Start cooking" at 11pm. Same rule: not the snack."""
    assert _pick(_NO_DINNER, 23) == "lunch:pending"


@_needs_node
def test_a_snack_only_evening_pins_nothing():
    assert _pick("[{date: T, slot: 'snack', cooked_status: 'pending'}]", 21) is None
    # GUARD: in the afternoon the snack is still a fine card.
    assert _pick("[{date: T, slot: 'snack', cooked_status: 'pending'}]", 14) == "snack:pending"
