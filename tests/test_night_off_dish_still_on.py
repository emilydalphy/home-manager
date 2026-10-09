"""
Loop Board bug (Low), 2026-10-09: "'Lemon Pasta is off the week' is said
for a night off when the same dish is still on Sunday".

Two nights carry the same dish, no night is free, and tonight's cook feeds
nobody — so the night off drops tonight's row (kind 'drop'). The dish is
NOT off the week: Sunday still has it. Both things the household reads
said it was — the sheet's sub-line (`night_off_line`, read off
_night_off_plan before the tap) and the toast (`said`, after it).
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.tools import tonight as _tonight


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = _monday().isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS
TONIGHT = WED
AFTERNOON = datetime.datetime.fromisoformat(f"{TONIGHT}T15:50:00")
PASTA = "Lemon Pasta"


def _recipe(name: str) -> None:
    tools.add_recipe(
        name,
        ingredients=[{"item": f"Main for {name}", "qty": "1 lb", "category": "pantry"}],
        prep_time_minutes=10, cook_time_minutes=20, default_servings=3,
    )


def _week(pasta_days: tuple[str, ...]) -> int:
    """A full week, no free night, with PASTA cooked fresh (no leftover
    link) on each of `pasta_days` and a dinner of its own every other night."""
    for name in ("Emily", "Julia", "Rae"):
        tools.add_member(name)
    _recipe(PASTA)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day in DAYS:
        dish = PASTA if day in pasta_days else f"Filler {day}"
        if dish != PASTA:
            _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan)
    return plan


def test_the_dish_still_on_sunday_is_not_said_to_be_off_the_week():
    _week((TONIGHT, SUN))
    check = _tonight.tonight_check(now=AFTERNOON)
    assert "off the week" not in check["night_off_line"]
    assert check["night_off_line"] == f"{PASTA} is still on Sunday."

    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["kind"] == "drop"
    assert out["said"] == f"Tonight’s off. {PASTA} is still on Sunday."
    # The fact the sentence rests on, for the client's own fallback.
    assert out["still_on"] == ["Sunday"]


def test_two_later_nights_are_both_named():
    _week((TONIGHT, FRI, SUN))
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["said"] == f"Tonight’s off. {PASTA} is still on Friday and Sunday."


def test_an_earlier_night_does_not_count_as_still_on():
    """Monday has been and gone — the dish really is off the rest of the
    week, and the sentence that says so stays."""
    _week((MON, TONIGHT))
    check = _tonight.tonight_check(now=AFTERNOON)
    assert check["night_off_line"] == f"{PASTA} comes off the week."
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["said"] == f"Tonight’s off. {PASTA} is off the week."
    assert out["still_on"] == []


def test_a_dish_only_tonight_is_still_off_the_week():
    _week((TONIGHT,))
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["kind"] == "drop"
    assert out["said"] == f"Tonight’s off. {PASTA} is off the week."


def test_the_screen_s_own_sentence_reads_still_on_too():
    """shell.js builds its own sentence only when the server sent no `said`;
    it reads `still_on` off the payload rather than re-deciding, and joins
    the nights the way the server does."""
    import json
    from pathlib import Path

    import nodeharness

    shell = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text(encoding="utf-8")
    start = shell.index("function tonightNightOffSaid(")
    end = shell.index("// Undo on the night-off toast", start)
    script = shell[start:end] + """
console.log(JSON.stringify([
  tonightNightOffSaid({dish: 'Lemon Pasta', still_on: ['Sunday']}),
  tonightNightOffSaid({dish: 'Lemon Pasta', still_on: ['Thursday', 'Friday', 'Sunday']}),
  tonightNightOffSaid({dish: 'Lemon Pasta', still_on: []}),
  tonightNightOffSaid({dish: 'Lemon Pasta'}),
]));
"""
    said = json.loads(nodeharness.run_node(script, timeout=30).stdout)
    assert said == [
        "Tonight’s off. Lemon Pasta is still on Sunday.",
        "Tonight’s off. Lemon Pasta is still on Thursday, Friday, and Sunday.",
        "Tonight’s off. Lemon Pasta is off the week.",
        "Tonight’s off. Lemon Pasta is off the week.",
    ]
    assert tools.weekly_plan._join_with_and(["Thursday", "Friday", "Sunday"]) in said[1]
