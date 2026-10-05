"""Settings -> Your rhythm shows and edits both time limits (Loop Board
"Time limits", criterion 3, 2026-10-05).

"Weeknight dinner: 45 min or less" and "Weekday lunch: 20 min or less", each
with the same chips setup asks. Unset lunch reads 20 (never "no limit"), 0
reads "no limit", and a number no chip holds (35) stays on the line and lights
no chip. Saves go through /api/memory/edit -> edit_preference, which already
validates both fields.
"""
from __future__ import annotations

import json
import re

from tests.test_preferences_align import MEMORY, SHELL_JS, _function, _harness, _run

DINNER = [20, 30, 45, 60, 0]
LUNCH = [10, 20, 30, 0]


def _html(**over) -> str:
    mem = {**MEMORY, **over}
    return _run(_harness() + f"""
prefsState.memory = {json.dumps(mem)};
console.log(JSON.stringify(wwkRhythmHtml(prefsState.memory)));
""")


def _chips(html: str, field: str):
    """[(value, lit)] for one limit's chip row, in order."""
    out = []
    for m in re.finditer(r'<button type="button" class="wwk-chip( is-on)?" aria-pressed="(\w+)" '
                         r'data-wwk="time-limit" data-field="%s" data-value="(\d+)">' % field, html):
        out.append((int(m.group(3)), bool(m.group(1))))
    return out


def test_both_limits_read_back_in_the_card_words():
    html = _html(weeknight_max_minutes=45, weekday_lunch_max_minutes=20)
    assert "Weeknight dinner: 45 min or less" in html
    assert "Weekday lunch: 20 min or less" in html
    assert html.index("Weeknight dinner:") < html.index("Weekday lunch:")


def test_each_row_carries_its_own_chips_with_the_current_one_lit():
    html = _html(weeknight_max_minutes=60, weekday_lunch_max_minutes=30)
    d = _chips(html, "weeknight_max_minutes")
    l = _chips(html, "weekday_lunch_max_minutes")
    assert [k for k, _ in d] == DINNER and [k for k, on in d if on] == [60]
    assert [k for k, _ in l] == LUNCH and [k for k, on in l if on] == [30]
    for label in ("20 min", "1 hour", "No limit", "10 min"):
        assert f">{label}</button>" in html


def test_no_limit_reads_back_right_and_lights_its_chip():
    html = _html(weeknight_max_minutes=0, weekday_lunch_max_minutes=0)
    assert "Weeknight dinner: no limit" in html and "Weekday lunch: no limit" in html
    assert "0 min or less" not in html
    assert [k for k, on in _chips(html, "weeknight_max_minutes") if on] == [0]
    assert [k for k, on in _chips(html, "weekday_lunch_max_minutes") if on] == [0]


def test_a_missing_lunch_value_falls_back_to_20_not_no_limit():
    mem = {k: v for k, v in MEMORY.items()}
    html = _run(_harness() + f"""
prefsState.memory = {json.dumps(mem)};
console.log(JSON.stringify(wwkRhythmHtml(prefsState.memory)));
""")
    assert "weekday_lunch_max_minutes" not in mem
    assert "Weekday lunch: 20 min or less" in html
    assert [k for k, on in _chips(html, "weekday_lunch_max_minutes") if on] == [20]


def test_an_out_of_chip_value_stays_readable_and_lights_no_chip():
    html = _html(weeknight_max_minutes=35, weekday_lunch_max_minutes=15)
    assert "Weeknight dinner: 35 min or less" in html
    assert "Weekday lunch: 15 min or less" in html
    assert not any(on for _, on in _chips(html, "weeknight_max_minutes"))
    assert not any(on for _, on in _chips(html, "weekday_lunch_max_minutes"))


def test_tapping_a_chip_saves_that_field_and_the_lit_chip_saves_nothing():
    out = _run(_harness("wwkSetTimeLimit") + f"""
var saves = [];
function wwkSavePreference(section, field, value, apply) {{ saves.push([section, field, value]); apply(); }}
prefsState.memory = {json.dumps({**MEMORY, "weekday_lunch_max_minutes": 20})};
wwkSetTimeLimit('weekday_lunch_max_minutes', 30);
wwkSetTimeLimit('weekday_lunch_max_minutes', 30);   // already 30: nothing
wwkSetTimeLimit('weeknight_max_minutes', 0);        // 45 -> no limit
wwkSetTimeLimit('weekday_lunch_max_minutes', NaN);  // junk: nothing
wwkSetTimeLimit('bogus', 5);                        // unknown field: nothing
console.log(JSON.stringify(saves));
""")
    assert out == [["rhythm", "weekday_lunch_max_minutes", 30], ["rhythm", "weeknight_max_minutes", 0]]


def test_the_chips_are_wired_and_the_save_route_validates_both_fields():
    assert "case 'time-limit': return wwkSetTimeLimit(" in SHELL_JS
    assert "wwkSetWeeknight" not in SHELL_JS and "WWK_WEEKNIGHT" not in SHELL_JS
    # /api/memory/edit is a thin wrapper over tools.edit_preference (a
    # ValueError becomes its 400), so the validation is exercised there.
    import pytest
    from app import tools
    tools.edit_preference("weekday_lunch_max_minutes", 30)
    assert tools.get_household_memory()["weekday_lunch_max_minutes"] == 30
    tools.edit_preference("weekday_lunch_max_minutes", 0)
    assert tools.get_household_memory()["weekday_lunch_max_minutes"] == 0
    for bad in (-5, "soon"):
        with pytest.raises(ValueError):
            tools.edit_preference("weekday_lunch_max_minutes", bad)
    assert tools.get_household_memory()["weekday_lunch_max_minutes"] == 0
