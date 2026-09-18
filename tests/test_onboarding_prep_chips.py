"""
Onboarding's prep-days step, Card 4 (Loop Board board 06-prep-days,
2026-09-18): "Which days do you want to do your meal prepping?" replaces
"Do you like meal prepping?", and the two-day cap is gone — "any number of
days can be on." "I don't prep ahead" moved from a skip link in the foot
into the chip row itself: it's ON exactly when no day is, tapping it clears
every day, and tapping any day turns it back off on its own.

Behavioural, under node, over the page's own renderPrepDayChips /
renderPrepMinutesChips / currentPrepDays — the same harness style
test_onboarding_go_back.py already uses for this file, reused here rather
than re-implemented (_DOM_STUB, _fn, _const).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import nodeharness

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "test_onboarding_go_back", _HERE / "test_onboarding_go_back.py"
)
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["test_onboarding_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_needs_node, _fn, _const = _go_back._needs_node, _go_back._fn, _go_back._const

ONBOARDING_PATH = _HERE.parent / "static" / "onboarding.html"
ONBOARDING = ONBOARDING_PATH.read_text()


def _harness() -> str:
    return "\n".join([
        _go_back._DOM_STUB,
        # The shared stub has no setAttribute (renderPrepDayChips is the
        # first caller in this file's harnesses to need one, for
        # aria-pressed) — patched on here rather than in the shared stub,
        # the same way other harnesses in this repo add it locally.
        """
(function () {
  const _origMakeEl = makeEl;
  makeEl = function (tag) {
    const built = _origMakeEl(tag);
    built.setAttribute = function (k, v) { built._attrs = built._attrs || {}; built._attrs[k] = v; };
    return built;
  };
})();
""",
        "el('rhythm-prep-day-chips'); el('rhythm-prep-minutes-wrap'); el('rhythm-prep-minutes-chips');",
        _const("PREP_DAY_OPTIONS"),
        _const("PREP_MINUTES_OPTIONS"),
        "var rhythmPrepDays = []; var rhythmPrepMinutes = 0;",
        _fn("buildSingleSelectChips"),
        _fn("renderPrepDayChips"),
        _fn("renderPrepMinutesChips"),
        _fn("currentPrepDays"),
        """
function row() { return document.getElementById('rhythm-prep-day-chips'); }
function chip(value) {
  return row()._children.filter(function (c) { return c.dataset.value === value; })[0];
}
function noneChip() {
  return row()._children.filter(function (c) { return c.textContent === "I don\\u2019t prep ahead"; })[0];
}
function tapDay(key) { chip(key).click(); }
function tapNone() { noneChip().click(); }
""",
    ])


def _run(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


@_needs_node
def test_i_dont_prep_ahead_is_the_first_chip_and_starts_on():
    out = _run(_harness() + """
renderPrepDayChips();
console.log(JSON.stringify({
  firstLabel: row()._children[0].textContent,
  firstOn: row()._children[0]._classes.has('active'),
  count: row()._children.length,
}));
""")
    assert out["firstLabel"] == "I don’t prep ahead"
    assert out["firstOn"] is True
    # One "I don't prep ahead" chip plus the seven weekdays.
    assert out["count"] == 8


@_needs_node
def test_tapping_a_day_turns_off_i_dont_prep_ahead():
    out = _run(_harness() + """
renderPrepDayChips();
tapDay('monday');
console.log(JSON.stringify({
  noneOn: noneChip()._classes.has('active'),
  mondayOn: chip('monday')._classes.has('active'),
  days: rhythmPrepDays,
}));
""")
    assert out == {"noneOn": False, "mondayOn": True, "days": ["monday"]}


@_needs_node
def test_more_than_two_days_can_all_be_on_at_once():
    """The two-day cap left with Card 4 — any number of days can be on."""
    out = _run(_harness() + """
renderPrepDayChips();
['sunday', 'monday', 'wednesday', 'friday'].forEach(tapDay);
console.log(JSON.stringify({
  days: rhythmPrepDays,
  allOn: ['sunday', 'monday', 'wednesday', 'friday'].every(function (d) { return chip(d)._classes.has('active'); }),
}));
""")
    assert out["days"] == ["sunday", "monday", "wednesday", "friday"]
    assert out["allOn"] is True


@_needs_node
def test_tapping_i_dont_prep_ahead_clears_every_day():
    out = _run(_harness() + """
renderPrepDayChips();
['sunday', 'wednesday'].forEach(tapDay);
tapNone();
console.log(JSON.stringify({
  days: rhythmPrepDays,
  noneOn: noneChip()._classes.has('active'),
  sundayOn: chip('sunday')._classes.has('active'),
}));
""")
    assert out == {"days": [], "noneOn": True, "sundayOn": False}


@_needs_node
def test_untapping_the_last_day_turns_i_dont_prep_ahead_back_on():
    out = _run(_harness() + """
renderPrepDayChips();
tapDay('sunday');
tapDay('sunday');
console.log(JSON.stringify({ days: rhythmPrepDays, noneOn: noneChip()._classes.has('active') }));
""")
    assert out == {"days": [], "noneOn": True}


@_needs_node
def test_current_prep_days_reads_back_in_week_order_not_tap_order():
    out = _run(_harness() + """
renderPrepDayChips();
['wednesday', 'sunday'].forEach(tapDay);
console.log(JSON.stringify(currentPrepDays().map(function (d) { return d.weekday; })));
""")
    assert out == ["sunday", "wednesday"]


def test_the_markup_carries_no_separate_skip_link_for_prep_days():
    """
    "I don't prep ahead" moved into the chip row; the foot's skip span
    (#prep-skip) and its handler are both gone, and Continue is the only
    control left in the foot.
    """
    step = _go_back._step_markup("step-prep")
    assert 'id="prep-skip"' not in step
    assert step.count("btn-primary") == 1
    assert "document.getElementById('prep-skip')" not in ONBOARDING


def test_the_two_day_cap_is_gone_from_the_source():
    assert "MAX_PREP_DAYS" not in ONBOARDING
    assert "Up to two days." not in ONBOARDING
