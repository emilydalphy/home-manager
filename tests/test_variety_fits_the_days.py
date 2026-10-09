"""
The "how many different lunches?" choices fit the days lunch is on.

Found 2026-10-09 (Loop Board, Bug, Low): lunch on weekends only (two days)
and the next screen still offered "3–4 lunches" -- more different lunches
than there are lunches. The options were filtered by the prep day and
nothing else. Now an option whose top count is more than the days the meal
is on isn't offered, and with ONE day there is no choice to make, so the
screen is skipped (one day, one lunch). Breakfast had the same bug and has
the same fix. Dinner is left alone (its answer also sets the leftovers
stance; see the decision log).

The page's own functions run under node, lifted the way
tests/test_onboarding_your_week.py lifts them.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
_spec = importlib.util.spec_from_file_location("test_onboarding_go_back", _HERE / "test_onboarding_go_back.py")
_go_back = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_go_back)
_needs_node, _fn, _const, _run = _go_back._needs_node, _go_back._fn, _go_back._const, _go_back._run
_nav_harness = _go_back._nav_harness


def _harness() -> str:
    return "\n".join([
        _const("UW_MEALS"), _const("UW_WEEKDAYS"), _const("VARIETY_OPTIONS"), _const("VARIETY_DEFAULT"),
        _fn("uwAllRow"), _fn("uwIsOn"), _fn("uwDaysOn"), _fn("uwSetMany"),
        "var usualGrid = { breakfast: uwAllRow(), lunch: uwAllRow(), dinner: uwAllRow() };",
        "var prepAnswer = ''; var prepDayKeys = [];",
        "var varietyChoice = { breakfast: 'few_in_rotation', lunch: 'few_in_rotation', dinner: 'few_in_rotation' };",
        _fn("currentPrepDayKeys"), _fn("hasPrepDay"), _fn("varietyOptionsFor"), _fn("currentVarietyChoice"),
        _fn("varietyOffered"),
    ])


# The top of each option's range, read off its own words: "3–4 lunches" is
# 4, "All different" is one a day. Mostly leftovers plans no lunches of
# its own.
def _most(num: str, days: int) -> int:
    if num.startswith("All different"):
        return days
    if num.startswith("Mostly leftovers"):
        return 0
    return int(num.split("–")[1].split()[0])


@_needs_node
def test_no_lunch_or_breakfast_choice_offers_more_than_the_days_it_is_on():
    out = _run(_harness() + """
const rows = [];
['breakfast', 'lunch'].forEach(meal => {
  [false, true].forEach(prep => {
    prepAnswer = prep ? 'yes' : ''; prepDayKeys = prep ? ['sunday'] : [];
    for (let n = 2; n <= 7; n++) {
      usualGrid[meal] = uwSetMany(usualGrid[meal], [0, 1, 2, 3, 4, 5, 6].slice(7 - n));
      rows.push({ meal: meal, days: n, offered: varietyOffered(meal).map(o => o.num) });
    }
  });
});
console.log(JSON.stringify(rows));
""")
    for row in out:
        for num in row["offered"]:
            assert _most(num, row["days"]) <= row["days"], f"{row['meal']} on {row['days']} days offers {num!r}"
        assert any(n.startswith("All different") for n in row["offered"])
    weekends = [r for r in out if r["meal"] == "lunch" and r["days"] == 2]
    assert all("3–4 lunches" not in r["offered"] for r in weekends)
    full = [r for r in out if r["days"] >= 4]
    assert all(any(n.startswith("3–4") for n in r["offered"]) for r in full), "a week with room for a rotation lost it"


@_needs_node
def test_a_pick_that_no_longer_fits_sends_one_a_day_not_the_default():
    """
    "A few in rotation" is the default. On two lunch days it isn't offered,
    so what is sent is "something new every day" -- which plans the same
    two lunches the server would have clamped the rotation to, and is the
    option the screen shows lit.
    """
    out = _run(_harness() + """
usualGrid.lunch = uwSetMany(usualGrid.lunch, [5, 6]);
const weekends = currentVarietyChoice('lunch');
varietyChoice.lunch = 'last_nights_dinner';
const leftovers = currentVarietyChoice('lunch');
usualGrid.lunch = uwAllRow();
varietyChoice.lunch = 'few_in_rotation';
const everyDay = currentVarietyChoice('lunch');
console.log(JSON.stringify({ weekends: weekends, leftovers: leftovers, everyDay: everyDay }));
""")
    assert out == {"weekends": "new_every_day", "leftovers": "last_nights_dinner", "everyDay": "few_in_rotation"}


@_needs_node
def test_one_lunch_day_skips_the_lunch_variety_screen():
    out = _run(_nav_harness() + """
usualGrid = { breakfast: [1, 1, 1, 1, 1, 1, 1], lunch: [0, 0, 0, 0, 0, 1, 0], dinner: [1, 1, 1, 1, 1, 1, 1] };
function uwIsOn(cell) { return cell !== 0; }
function uwDaysOn(row) { return (row || []).filter(uwIsOn).length; }
const one = stepFlow();
usualGrid.lunch = [0, 0, 0, 0, 0, 1, 1];
const two = stepFlow();
usualGrid.breakfast = [0, 0, 0, 0, 0, 0, 1];
const oneBreakfast = stepFlow();
usualGrid.dinner = [0, 0, 0, 0, 1, 0, 0];
const oneDinner = stepFlow();
console.log(JSON.stringify({ one: one, two: two, oneBreakfast: oneBreakfast, oneDinner: oneDinner }));
""")
    assert "variety-lunch" not in out["one"], "one lunch day still asks how many different lunches"
    assert "variety-lunch" in out["two"]
    assert "variety-breakfast" not in out["oneBreakfast"]
    assert "variety-dinner" in out["oneDinner"], "dinner is out of this card's scope"


def test_the_screen_draws_from_the_same_list_the_payload_reads():
    build = _fn("buildVarietyStep")
    assert "varietyOffered(meal)" in build
    # currentVarietyChoice reads the same day count (typeof-guarded: the
    # older harnesses lift it without a grid).
    assert "uwDaysOn(usualGrid[meal])" in _fn("currentVarietyChoice")
