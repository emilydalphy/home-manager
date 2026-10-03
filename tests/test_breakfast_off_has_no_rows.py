"""
A meal the household switched off for its whole usual week has NO row — on the
onboarding first-week reveal or on Plan (Loop Board "Breakfast turned off reads
'Nothing planned' on the reveal and 'Out — nothing to cook' on Plan",
2026-10-02; the "no row at all" default is an assumption Emily can overrule).

The test for "switched off" is the usual week (usual_week.switched_off_meals:
no day of the grid has the meal on, or a count of 0 for a household that never
saved a grid) — never "the slot happens to be empty". A meal that is normally
on but empty on one day (someone away, a night off) keeps its row and wording.
"""
from __future__ import annotations

import json

from app import tools
from app.db import get_conn
from app.tools import usual_week

import test_first_week_reveal_two_snacks_and_setup_pick as reveal
import test_plan_cards_2026_09_18 as plan_cards

WEEKDAYS = usual_week.WEEKDAYS
MONDAY = "2026-10-05"


def _plan_with_empty_breakfast(day_index=0):
    dates = tools._week_dates(MONDAY)
    plan_id = tools.create_weekly_plan(dates[0])["weekly_plan_id"]
    for i, d in enumerate(dates):
        tools.plan_meal(d, "Tacos", slot="dinner", weekly_plan_id=plan_id)
    return plan_id, dates


def _empty(plan_id, date, slot, constraint):
    tools.plan_slot_empty(weekly_plan_id=plan_id, meal_date=date, slot=slot,
                          reason="empty", derived_from={"constraint": constraint})


def _menu_day(plan_id, date):
    return next(d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == date)


# ---------- server: the flag ----------

def test_a_count_of_zero_marks_the_empty_breakfast_meal_off():
    tools.set_household_meal_preferences(breakfasts_per_week=0)
    assert usual_week.switched_off_meals() == {"breakfast"}
    plan_id, dates = _plan_with_empty_breakfast()
    _empty(plan_id, dates[0], "breakfast", "breakfasts_per_week:0")
    day = _menu_day(plan_id, dates[0])
    assert day["breakfast"]["state"] == "planned_empty" and day["breakfast"]["meal_off"] is True
    assert "meal_off" not in (day["dinner"] or {})


def test_a_grid_with_every_breakfast_off_marks_it_off_too():
    tools.save_usual_week(grid={"breakfast": {d: "off" for d in WEEKDAYS}})
    plan_id, dates = _plan_with_empty_breakfast()
    _empty(plan_id, dates[2], "breakfast", "usual_week_off")
    assert _menu_day(plan_id, dates[2])["breakfast"]["meal_off"] is True
    flat = tools.get_weekly_plan(plan_id)["meals"]
    empties = [m for m in flat if m["slot"] == "breakfast"]
    assert empties and all(m.get("meal_off") for m in empties)


def test_a_meal_that_is_on_most_days_keeps_its_row_on_the_day_it_is_empty():
    tools.save_usual_week(grid={"breakfast": {"saturday": "off"}})
    assert usual_week.switched_off_meals() == set()
    plan_id, dates = _plan_with_empty_breakfast()
    _empty(plan_id, dates[5], "breakfast", "usual_week_off")      # grid has Saturday off
    _empty(plan_id, dates[1], "breakfast", "already_past")        # an ordinary empty
    out = _empty(plan_id, dates[2], "breakfast", "need:away")      # someone away
    sat = _menu_day(plan_id, dates[5])["breakfast"]
    assert sat["title"] == "Not planned" and "meal_off" not in sat
    assert "meal_off" not in _menu_day(plan_id, dates[2])["breakfast"]
    assert _menu_day(plan_id, dates[2])["breakfast"]["title"] == "Out — nothing to cook"
    assert not any(m.get("meal_off") for m in tools.get_weekly_plan(plan_id)["meals"])


def test_a_default_household_has_nothing_switched_off():
    assert usual_week.switched_off_meals() == set()


# ---------- the reveal ----------

def _menu_days():
    off = {"title": "Nothing planned", "state": "planned_empty", "entry_id": 9, "meal_off": True}
    away = {"title": "Out — nothing to cook", "state": "planned_empty", "entry_id": 10}
    return [
        {"date": "2026-10-05", "breakfast": off,
         "lunch": {"title": "Wrap", "state": "planned", "entry_id": 2},
         "dinner": {"title": "Chili", "state": "planned", "entry_id": 3}, "snacks": []},
        {"date": "2026-10-06", "breakfast": {"title": "Oats", "state": "planned", "entry_id": 4},
         "lunch": away, "dinner": {"title": "Stew", "state": "planned", "entry_id": 5}, "snacks": []},
    ]


@reveal._needs_node
def test_the_reveal_leaves_out_a_switched_off_meal_but_keeps_an_empty_one():
    out = reveal._run(reveal._reveal_harness() + f"""
const days = revealDaysFromMenu({json.dumps(_menu_days())});
const cards = renderAll(days);
console.log(JSON.stringify(cards.map(c => rows(c))));
""")
    assert [r[0] for r in out[0]] == ["lunch", "dinner"], "no breakfast row at all on day one"
    assert [r[0] for r in out[1]] == ["breakfast", "lunch", "dinner"]
    assert out[1][1][2] == "Nothing planned", "an empty lunch on a normal-on meal keeps its wording"


@reveal._needs_node
def test_the_reveals_flat_fallback_also_leaves_it_out():
    meals = [
        {"entry_id": 9, "date": "2026-10-05", "slot": "breakfast", "meal": "", "slot_state": "planned_empty", "meal_off": True},
        {"entry_id": 3, "date": "2026-10-05", "slot": "dinner", "meal": "Chili", "slot_state": "planned"},
        {"entry_id": 10, "date": "2026-10-06", "slot": "lunch", "meal": "", "slot_state": "planned_empty"},
    ]
    out = reveal._run(reveal._reveal_harness() + f"""
const days = groupRevealMealsByDay({json.dumps(meals)});
console.log(JSON.stringify(renderAll(days).map(c => rows(c).map(r => r[0]))));
""")
    assert out == [["dinner"], ["lunch"]]


# ---------- Plan ----------

def _plan_days():
    off = {"title": "Out — nothing to cook", "state": "planned_empty", "source": "empty", "entry_id": 9, "meal_off": True}
    away = {"title": "Out — nothing to cook", "state": "planned_empty", "source": "empty", "entry_id": 10}
    mon = plan_cards._day("2026-10-05", breakfast=off,
                          lunch=plan_cards._entry("Wrap", entry_id=2), dinner=plan_cards._entry("Chili", entry_id=3))
    tue = plan_cards._day("2026-10-06", breakfast=plan_cards._entry("Oats", entry_id=4),
                          lunch=away, dinner=plan_cards._entry("Stew", entry_id=5))
    return [mon, tue]


@plan_cards._needs_node
def test_plan_draws_no_row_for_a_switched_off_meal_and_keeps_the_empty_one():
    days = _plan_days()
    html = plan_cards._run(plan_cards._prelude() + f"""
console.log(JSON.stringify([wkDayCardHtml({json.dumps(days[0])}, 0, {{}}), wkDayCardHtml({json.dumps(days[1])}, 1, {{}})]));""")
    mon, tue = html
    assert "Breakfast" not in mon and "Out — nothing to cook" not in mon
    assert "Lunch" in mon and "Dinner" in mon
    assert "<span class=\"wk-card-count\">2 meals</span>" in mon
    assert "Out — nothing to cook" in tue and "Breakfast" in tue, "an empty lunch on a normal-on meal keeps its row"


@plan_cards._needs_node
def test_the_week_strip_tile_has_no_dot_for_a_switched_off_meal():
    days = _plan_days()
    out = plan_cards._run(plan_cards._prelude()
        + plan_cards._extract("weekTileHtml", plan_cards.SHELL_JS) + "\n"
        + plan_cards._extract("slotDotClass", plan_cards.SHELL_JS) + "\n"
        + f"console.log(JSON.stringify([weekTileHtml({json.dumps(days[0])}, 0, 0), weekTileHtml({json.dumps(days[1])}, 1, 0)].map(h => (h.match(/wk-dot /g) || []).length)));")
    assert out == [2, 3]


# ---------- the two other doors the verifier found ----------

@plan_cards._needs_node
def test_add_a_meal_never_points_at_a_switched_off_meal():
    off = {"state": "planned_empty", "title": "Out — nothing to cook", "meal_off": True}
    day = plan_cards._day("2026-10-05", breakfast=off, lunch=plan_cards._entry("Wrap"),
                          dinner=plan_cards._entry("Chili"))
    out = plan_cards._run(plan_cards._prelude()
        + "var asked = [];\nfunction openAskSheet(t) { asked.push(t); }\n"
        + plan_cards._extract("wkAddMealFor", plan_cards.SHELL_JS) + "\n"
        + f"wkAddMealFor(null, null, {json.dumps(day)});\nconsole.log(JSON.stringify(asked));")
    assert len(out) == 1 and "breakfast" not in out[0].lower(), out


def test_the_whole_week_sheet_prints_no_words_for_a_switched_off_meal():
    src = plan_cards.SHELL_JS
    i = src.index("week-sheet-rows').innerHTML")
    block = src[i:i + 2500]
    assert "if (entry.meal_off) return '<span class=\"' + cellClass + ' blank\"></span>';" in block
