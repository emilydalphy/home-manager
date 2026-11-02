"""
Cook's root, Option A — "every meal the same size" (Emily, 2026-11-01, the
Cook declutter canvas; mockups A-AlsoOpen, A-NightList, Main, A-Wed, A-Thu).

Pinned here, running shell.js's own functions under node:
  - today's meals are one even list in eating order (Breakfast, Lunch,
    Snack, Dinner, Snack); "next" is the first one not done — no clock;
  - no clock time is ever drawn on the root (minutes only);
  - a thaw shows once: in the night-before list, never also as a row or a
    card of its own, and never when today's prep list already holds it;
  - the prep session leads as the big card only on a prep day before the
    prep is done; done, it is one line;
  - a leftovers row says just "Leftovers";
  - a day with nothing on it says "Night off" and names the next cook;
  - the dock names the next meal's action.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from tests import nodeharness

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(name: str, source: str = SHELL_JS) -> str:
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


def _between(start_marker: str, end_marker: str) -> str:
    a = SHELL_JS.index(start_marker)
    return SHELL_JS[a : SHELL_JS.index(end_marker, a)]


ROOT_BLOCK = _between("  // ---------- Cook's root: today, in the order it is eaten", "  function renderKitchen()")

_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "const REHEAT_ACTION_LABEL = 'Mark eaten';\n"
    "function dayName(dateStr, opts){ return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', opts); }\n"
    + _function("addDaysLocal") + "\n"
    + _function("daysBetweenLocal") + "\n"
    + _function("cookPreppedAhead") + "\n"
    + _function("cookSessionOn") + "\n"
    + _between("  function kitchenTodayRows(", "  function cookBandEyebrow(")
    + _function("kitchenNextCookLine") + "\n"
    + ROOT_BLOCK
)

TUE, WED, FRI = "2026-09-15", "2026-09-16", "2026-09-18"  # 2026-09-15 is a Tuesday


def _meal(entry_id, date, meal, slot="dinner", **extra):
    m = {"entry_id": entry_id, "date": date, "slot": slot, "meal": meal,
         "cooked_status": "pending", "is_leftovers": False,
         "prep_time_minutes": 15, "cook_time_minutes": 30}
    m.update(extra)
    return m


def _fridge(task_id, entry_id=20, title="Move the chicken thighs to the fridge", **extra):
    m = {"id": f"fridge:{task_id}", "kind": "fridge", "title": title, "done": False,
         "tickable": True, "task_id": task_id, "entry_id": entry_id, "related_meal": "",
         "date": TUE, "too_late_note": ""}
    m.update(extra)
    return m


def _root(meals, moves=None, today=TUE, sessions=None, bed_open=None):
    data = {"meals": meals, "prep_sessions": sessions or [], "prep_tasks": [], "weekly_plan_id": 1}
    script = (
        "var parts = cookRootParts(" + json.dumps(data) + ", " + json.dumps(moves or []) + ", "
        + json.dumps(today) + ", " + json.dumps(bed_open) + ");\n"
        "console.log(JSON.stringify(parts));\n"
    )
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _slots(html):
    return re.findall(r'<span class="cook-today-slot">([^<]*)</span>', html)


def _dishes(html):
    return re.findall(r'<span class="cook-today-dish">([^<]*)</span>', html)


def _a_full_day():
    return [
        _meal(4, TUE, "Sheet-Pan Chicken Thighs", "dinner"),
        # Snacks keep the plan's own order: the first is the afternoon one.
        _meal(3, TUE, "Orange Ginger Shots", "snack", prep_time_minutes=12, cook_time_minutes=0),
        _meal(5, TUE, "Veg sticks and hummus", "snack", is_leftovers=True, served_cold=True,
              leftovers_headline="Made ahead — Sunday’s Veg sticks and hummus",
              leftovers_from={"meal": "Veg sticks and hummus", "date": "2026-09-13"},
              prep_time_minutes=0, cook_time_minutes=0),
        _meal(2, TUE, "Chicken Caesar Wraps", "lunch", prep_time_minutes=15, cook_time_minutes=0),
        _meal(1, TUE, "Overnight Oats with Berries", "breakfast", cooked_status="done"),
    ]


# --------------------------------------------------------------------------
# The list, and "next"
# --------------------------------------------------------------------------

@_needs_node
def test_todays_meals_are_one_list_in_eating_order_with_next_marked():
    out = _root(_a_full_day())
    html = out["html"]
    assert html.count('class="cook-today"') == 1
    # Breakfast, Lunch, the first snack, Dinner, the later snack.
    assert _slots(html) == ["Breakfast", "Lunch · next", "Snack", "Dinner", "Snack"]
    assert _dishes(html) == ["Overnight Oats with Berries", "Chicken Caesar Wraps", "Orange Ginger Shots",
                             "Sheet-Pan Chicken Thighs", "Veg sticks and hummus"]
    # Every row the same shape: one button each, a tap opens that meal.
    assert html.count('class="cook-today-row') == 5
    assert html.count('data-cook="focus"') == 5
    # One next, one dot; the done breakfast is a check.
    assert html.count("is-next") == 1 and html.count("cook-today-dot") == 1
    assert 'cook-today-row is-done' in html and 'aria-label="Done"' in html
    # Minutes on the right; food made ahead says Ready.
    assert '<span class="cook-today-right">15 min</span>' in html
    assert '<span class="cook-today-right">45 min</span>' in html
    assert '<span class="cook-today-right is-ready">Ready</span>' in html
    # No per-row box, Cook button or second line any more.
    for gone in ("cook-box", "cook-badge", "cook-week", "cook-tonight", "shelf-tile", "cook-ready"):
        assert gone not in html, gone
    assert 'data-cook="start-tonight"' in out["dock"] and ">Make lunch<" in out["dock"]


@_needs_node
def test_next_is_the_first_meal_not_done_whatever_the_hour():
    meals = [_meal(1, TUE, "Pancakes", "breakfast"), _meal(4, TUE, "Chili", "dinner")]
    out = _root(meals)
    assert _slots(out["html"]) == ["Breakfast · next", "Dinner"]
    assert ">Make breakfast<" in out["dock"]
    # Breakfast done: dinner is next, and the dock says Start cooking.
    meals[0]["cooked_status"] = "done"
    out = _root(meals)
    assert _slots(out["html"]) == ["Breakfast", "Dinner · next"]
    assert ">Start cooking<" in out["dock"]
    # A cook under way keeps going.
    meals[1]["cook_started_at"] = "2026-09-15T17:50:00"
    assert ">Keep cooking<" in _root(meals)["dock"]


@_needs_node
def test_no_clock_time_is_ever_drawn():
    meals = _a_full_day()
    moves = [
        {"id": "cook:4", "kind": "cook", "entry_id": 4, "done": False,
         "chips": ["Start by 5:35", "Started 5:50"], "time_label": "6:30 pm", "planned_start": "2026-09-15T17:35:00"},
        {"id": "cook:2", "kind": "cook", "entry_id": 2, "done": False, "chips": ["Start by 11:45"], "time_label": "12:00 pm"},
    ]
    out = _root(meals, moves)
    text = out["html"] + out["dock"]
    assert not re.search(r"\b\d{1,2}:\d{2}\b", text), "a clock time on Cook's root"
    for words in ("Start by", "start by", "On the table", "on the table", "Started", " pm", " am"):
        assert words not in text, words


# --------------------------------------------------------------------------
# Leftovers, takeout, night off
# --------------------------------------------------------------------------

@_needs_node
def test_a_leftovers_row_says_just_leftovers():
    meals = [_meal(7, TUE, "", "dinner", is_leftovers=True,
                   leftovers_headline="Leftovers — Monday’s Beef Chili",
                   leftovers_from={"meal": "Beef Chili", "date": "2026-09-14"},
                   prep_time_minutes=0, cook_time_minutes=0),
             _meal(8, TUE, "", "lunch", is_leftovers=True,
                   leftovers_headline="Leftovers from the freezer — Chili")]
    out = _root(meals)
    assert _dishes(out["html"]) == ["Leftovers", "Leftovers"]
    assert "Monday" not in out["html"] and "freezer" not in out["html"] and "from " not in out["html"]
    assert '<span class="cook-today-right">Reheat</span>' in out["html"]
    assert ">Mark eaten<" in out["dock"] and 'data-cook="check-meal"' in out["dock"]


@_needs_node
def test_a_day_with_nothing_on_it_is_a_night_off_and_names_the_next_cook():
    meals = [_meal(9, FRI, "Salmon Tacos")]
    out = _root(meals)
    assert '<h2 class="cook-off-title">Night off</h2>' in out["html"]
    assert "Nothing to cook today." in out["html"]
    assert '<p class="cook-quiet-line">Next: Friday, Salmon Tacos</p>' in out["html"]
    assert "cook-today-row" not in out["html"]
    assert out["dock"] == ""


@_needs_node
def test_takeout_is_named_and_ticked_never_cooked():
    out = _root([_meal(6, TUE, "Pizza night", "dinner", is_takeout=True)])
    assert '<span class="cook-today-right">Takeout</span>' in out["html"]
    assert ">Mark eaten<" in out["dock"] and "Start cooking" not in out["dock"]


# --------------------------------------------------------------------------
# The night before: a thaw shows once
# --------------------------------------------------------------------------

@_needs_node
def test_during_the_day_the_night_before_is_one_quiet_line():
    meals = [_meal(4, TUE, "Chili", "dinner"), _meal(20, WED, "Sheet-Pan Chicken")]
    out = _root(meals, [_fridge(12), {"id": "prep:13", "kind": "prep", "title": "Make overnight oats",
                                      "done": False, "tickable": True, "task_id": 13, "entry_id": 21}])
    html = out["html"]
    assert 'id="kit-get-ready"' in html and 'data-cook="bed-open"' in html
    assert "<span>Before bed: 2 things for Wednesday</span>" in html
    # The thaw itself is not on the screen yet — and never twice.
    assert "Move the chicken thighs" not in html
    assert "cook-bed\"" not in html and "cook-thaw" not in html


@_needs_node
def test_after_the_last_meal_the_night_before_is_the_spruce_checklist_and_the_thaw_is_there_once():
    meals = [_meal(4, TUE, "Chili", "dinner", cooked_status="done"), _meal(20, WED, "Sheet-Pan Chicken")]
    out = _root(meals, [_fridge(12)])
    html = out["html"]
    assert html.index('class="cook-bed"') < html.index('class="cook-today"'), "the checklist leads"
    assert '<span class="cook-bed-eyebrow">Before bed</span>' in html
    assert '>Get ready for Wednesday</h2>' in html
    assert html.count("Move the chicken thighs to the fridge") == 1
    assert 'type="checkbox" class="cook-bed-input" data-move-id="fridge:12"' in html
    assert "Before bed:" not in html, "no line as well as the card"
    assert ">All done<" in out["dock"] and 'data-cook="bed-all"' in out["dock"]
    # Every box ticked: one quiet line, and no dock.
    out = _root(meals, [_fridge(12, done=True)])
    assert "<span>All set for Wednesday</span>" in out["html"] and 'class="cook-bed"' not in out["html"]
    assert out["dock"] == ""
    # Opened by hand earlier in the day: the card sits under the list.
    meals[0]["cooked_status"] = "pending"
    out = _root(meals, [_fridge(12)], bed_open=TUE)
    assert out["html"].index('class="cook-today"') < out["html"].index('class="cook-bed"')


@_needs_node
def test_prep_for_todays_own_meal_and_todays_prep_list_stay_off_the_night_list():
    meals = [_meal(4, TUE, "Chili", "dinner", cooked_status="done"), _meal(20, WED, "Sheet-Pan Chicken")]
    sessions = [{"date": TUE, "weekday": "Tuesday", "items_total": 2, "items_done": 2, "covers": [WED],
                 "items": [{"prep_task_id": 12}], "sections": []}]
    moves = [
        _fridge(12),  # already on today's prep list
        {"id": "prep:14", "kind": "prep", "title": "Marinate the beef", "done": False, "tickable": True,
         "task_id": 14, "entry_id": 4},  # for tonight's own dinner
        {"id": "prep:15", "kind": "prep", "title": "Soak the beans", "done": False, "tickable": True,
         "task_id": 15, "entry_id": None, "related_meal": "chili"},  # tonight's, by name
    ]
    html = _root(meals, moves, sessions=sessions)["html"]
    assert "cook-bed" not in html and "Before bed" not in html
    assert "Move the chicken" not in html and "Marinate" not in html and "Soak" not in html


@_needs_node
def test_a_thaw_too_late_for_the_fridge_says_so_in_the_list():
    meals = [_meal(4, TUE, "Chili", "dinner", cooked_status="done")]
    note = "Too late to thaw safely in the fridge."
    html = _root(meals, [_fridge(12, too_late_note=note)])["html"]
    assert '<span class="cook-bed-note">' + note + "</span>" in html


# --------------------------------------------------------------------------
# Prep days
# --------------------------------------------------------------------------

def _session(done, total=13):
    return {"date": TUE, "weekday": "Tuesday", "items_total": total, "items_done": done,
            "covers": [TUE, "2026-09-16", "2026-09-17"], "header": "Up to 1.5 hr · about 1 hr hands-on",
            "items": [], "sections": [
                {"key": "thaw", "title": "Move to thaw", "line": "1 · first", "done": False},
                {"key": "chop", "title": "Chop and wash", "line": "5 · 25 min", "done": False}]}


@_needs_node
def test_the_prep_card_leads_only_on_a_prep_day_before_the_prep_is_done():
    meals = [_meal(4, TUE, "Classic Beef Burgers", "dinner")]
    out = _root(meals, sessions=[_session(0)])
    html = out["html"]
    assert html.startswith('<button type="button" class="cook-prepday" data-cook="session" data-date="' + TUE + '">')
    assert '<span class="cook-prepday-eyebrow">Up to 1.5 hr</span>' in html
    assert '<span class="cook-prepday-title">Tuesday prep</span>' in html
    assert '<span class="cook-prepday-for">For Tuesday to Thursday</span>' in html
    assert '0 of 13 done' in html and 'Move to thaw' in html and '5 · 25 min' in html
    assert ">Start prep<" in out["dock"] and 'data-cook="session"' in out["dock"]
    assert ">Keep prepping<" in _root(meals, sessions=[_session(4)])["dock"]
    # Done: one quiet line, no big card, and the dock is the meal's again.
    out = _root(meals, sessions=[_session(13)])
    assert "cook-prepday" not in out["html"]
    assert "<span>Tuesday prep is done · 13 of 13</span>" in out["html"]
    assert ">Start cooking<" in out["dock"]
    # Not a prep day: no card at all — a later prep day is one line.
    later = dict(_session(0), date="2026-09-17", weekday="Thursday")
    out = _root(meals, sessions=[later])
    assert "cook-prepday" not in out["html"]
    assert "<span>Thursday prep · 13 things</span>" in out["html"]


# --------------------------------------------------------------------------
# Wiring and drawing
# --------------------------------------------------------------------------

def test_the_root_draws_only_the_new_parts():
    root = _function("renderKitchen")
    assert "cookRootParts(data, kitchenState.moves, todayIso, cookState.bedOpenOn)" in root
    for gone in ("cookShelfHtml", "cookTonightCardHtml", "cookThawCardHtml", "cookGetReadyRowsHtml",
                 "kitchenCookingTodayHtml"):
        assert gone not in SHELL_JS, gone + " should be gone with the old root"


def test_a_tick_on_the_night_list_posts_the_same_done_today_did():
    tick = _function("cookBedTick")
    assert "'/api/today/moves/' + encodeURIComponent(ids[i]) + '/done'" in tick
    assert "body: { done: on }, quiet: true" in tick
    assert "panel.addEventListener('change', onCookBedChange);" in SHELL_JS
    click = _function("onCookClick")
    assert "what === 'bed-open'" in click and "what === 'bed-all'" in click


def test_the_new_root_is_drawn_in_tokens_only():
    start = SHELL_CSS.index("/* ---------- Cook's root: today, in the order it is eaten")
    block = SHELL_CSS[start: SHELL_CSS.index("/* \"More ···\"", start)]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "a literal colour in the root's CSS"
    for need in ("background: var(--surface)", "background: var(--spruce)", "background: var(--apricot)",
                 "color: var(--apricot-label)", "min-height: 44px"):
        assert need in block, need


@_needs_node
def test_a_dish_plated_at_the_prep_list_says_plate():
    meals = [_meal(8, TUE, "Chicken Salad", "lunch", is_leftovers=True, served_cold=True,
                   made_at_prep={"date": "2026-09-13", "weekday": "Sunday"}, finish_action="plate",
                   leftovers_headline="Chicken Salad — made at Sunday’s prep")]
    out = _root(meals)
    assert _dishes(out["html"]) == ["Chicken Salad"]
    assert '<span class="cook-today-right">Plate</span>' in out["html"]
    assert ">Mark eaten<" in out["dock"]


# --------------------------------------------------------------------------
# Whose turn (Emily 2026-11-01, "go with your picks": "Dinner · Vineeth")
# --------------------------------------------------------------------------

def _root_with(meals, moves, cook_name):
    data = {"meals": meals, "prep_sessions": [], "prep_tasks": [], "weekly_plan_id": 1,
            "cook_name": cook_name}
    script = (
        "var parts = cookRootParts(" + json.dumps(data) + ", " + json.dumps(moves) + ", "
        + json.dumps(TUE) + ", null);\nconsole.log(JSON.stringify(parts));\n"
    )
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _cook_move(entry_id, owner_name, done=False):
    return {"kind": "cook", "entry_id": entry_id, "owner_name": owner_name, "done": done}


@_needs_node
def test_a_household_that_takes_turns_sees_whose_night_it_is():
    meals = [_meal(2, TUE, "Chicken Caesar Wraps", "lunch", cooked_status="done"),
             _meal(4, TUE, "Sheet-Pan Chicken Thighs", "dinner")]
    html = _root_with(meals, [_cook_move(4, "Vineeth")], None)["html"]
    assert "Dinner · Vineeth · next" in _slots(html)


@_needs_node
def test_one_cook_households_and_done_meals_name_nobody():
    meals = [_meal(2, TUE, "Chicken Caesar Wraps", "lunch", cooked_status="done"),
             _meal(4, TUE, "Sheet-Pan Chicken Thighs", "dinner")]
    one_cook = _root_with(meals, [_cook_move(4, "Emily")], "Emily")["html"]
    assert "Dinner · next" in _slots(one_cook)
    done = _root_with(meals, [_cook_move(2, "Vineeth", done=True)], None)["html"]
    assert "Lunch" in _slots(done)
    nobody = _root_with(meals, [_cook_move(4, None)], None)["html"]
    assert "Dinner · next" in _slots(nobody)
