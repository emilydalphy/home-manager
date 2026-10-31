"""
Today & Cook timing — Emily's answers to the small calls, 2026-10-26 ("go
with your picks"). One test (or two) per pick:

33. A missed 48-hour thaw whose meal is tomorrow says it's too late, not
    "still to do" (covered in test_missed_fridge_move_carries.py).
34. On the cook day, Today and Cook's Tonight card say the same thing about
    a missed fridge move: one rule (moves.fridge_move_too_late), stamped on
    the cooker view's prep task as `too_late_note` for Cook.
35. Ticking a carried move toasts "… was ticked off" with Undo.
36. "Shop for tonight" for a cook whose time has gone by goes once the
    evening is over (moves.evening_over, the "Still good?" cutoff).
37. A cook's "Start by" chip goes at the same moment.
41. "Tonight's dinner needs your call" stops asking at 9pm household time;
    so does Today's own open-slot row.
42. A prep step for tonight's cook is late at the cook's start, not at
    dinner time.
43. Tomorrow's "leftovers from Thursday", read on Thursday, says "from
    tonight".
"""
from __future__ import annotations

from pathlib import Path
import datetime
import json
import shutil

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import cooker, moves, weekly_plan

from test_plan_cards_2026_09_18 import _run
SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
from test_skipped_cook_moves_on import _chain, _plan as _chain_plan
from test_today_small_truths import _at, _shop, _tonight_planned
from test_week_seven_tiles import _extract, _extract_async

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the functions")


# ---------- 34. Today and Cook agree on the cook day ----------

def _missed_move_for_tonight() -> tuple[datetime.date, int]:
    today = _tonight_planned()
    conn = get_conn()
    row = conn.execute("SELECT id, weekly_plan_id FROM meal_plan_entries WHERE date = ? AND slot = 'dinner'",
                       (today.isoformat(),)).fetchone()
    task = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id) VALUES (?, ?, ?, ?, 'Weeknight Chili', 'pending', 'defrost', ?)",
        (tools.household_id(), row["weekly_plan_id"], (today - datetime.timedelta(days=2)).isoformat(),
         "Move the ground beef to the fridge — for Monday's Weeknight Chili.", row["id"]),
    ).lastrowid
    conn.commit()
    conn.close()
    return today, task


def test_on_the_cook_day_today_and_cooks_task_carry_the_same_too_late_note():
    today, task = _missed_move_for_tonight()
    now = _at(today, 8)
    [row] = [m for m in moves.today_moves(day=today, now=now)["moves"] if m["kind"] == "fridge"]
    assert row["time_label"] == "too late to thaw in the fridge"
    # Cook's view is stamped by the same function on the household's real
    # clock; ask it for 8am directly, the way the view does.
    view = cooker.get_cooker_view()
    [t] = [t for t in view["prep_tasks"] if t["id"] == task]
    assert "too_late_note" in t
    meal = next(m for m in view["meals"] if m["entry_id"] == t["meal_plan_entry_id"])
    assert moves.fridge_move_too_late(t, meal, now, moves._dinner_clock()) == row["too_late_note"]
    assert row["too_late_note"].startswith("Too late to thaw safely for tonight")


def test_a_move_on_its_own_day_is_never_too_late():
    today, task = _missed_move_for_tonight()
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET task_date = ? WHERE id = ?", (today.isoformat(), task))
    conn.commit()
    conn.close()
    view = cooker.get_cooker_view()
    [t] = [t for t in view["prep_tasks"] if t["id"] == task]
    assert t["too_late_note"] == ""


@_needs_node
def test_cooks_tonight_card_says_the_too_late_note_instead_of_still_to_do():
    out = _run(_extract("cookTonightNote", SHELL_JS) + "\n"
               + _extract("cookThawTitle", SHELL_JS) + "\n" + """
function cookPreppedAhead() { return null; }
var note = 'Too late to thaw safely for tonight — a cold-water thaw takes about an hour per pound, or cook something else.';
function cookFocusPrepTasks() { return TASKS; }
var TASKS = [{ task_type: 'defrost', status: 'pending', too_late_note: note,
               description: 'Move the ground beef to the fridge — for Monday\\'s Weeknight Chili.' }];
var a = cookTonightNote({}, {});
TASKS = [{ task_type: 'defrost', status: 'pending', too_late_note: '',
           description: 'Move the ground beef to the fridge — for Monday\\'s Weeknight Chili.' }];
var b = cookTonightNote({}, {});
console.log(JSON.stringify([a, b]));
""")
    assert out[0].startswith("Too late to thaw safely for tonight")
    assert out[1] == "Move the ground beef to the fridge — still to do."


# ---------- 35. A carried move's tick toasts, with Undo ----------

_TICK_STUBS = """
var CALLS = [];
var TOASTS = [];
var POSTS = [];
function refreshKitchenPanel() {}
function refreshWeekPanel() {}
function renderTodayMoves() {}
function todayAnimateNodeSettle() {}
function savedName(s) { return s; }
function savedLine(n, v) { return n + ' was ' + v; }
function showToast(t, action, hold) { TOASTS.push({ text: t, label: action && action.label, hold: hold || null, action: action }); }
var Api = { fetch: async function (url, opts) {
  POSTS.push(JSON.parse(opts.body).done);
  return { ok: true, json: async function () { return FRESH; } };
} };
"""


@_needs_node
def test_ticking_a_carried_move_toasts_with_undo_and_undo_unticks_it():
    out = _run(_TICK_STUBS + _extract_async("toggleTodayMove", SHELL_JS) + "\n"
               + _extract_async("undoCarriedTodayMove", SHELL_JS) + "\n" + """
var FRESH = { moves: [] };  // carried rows only show while pending: gone once ticked
var move = { id: 'fridge:7', kind: 'fridge', done: false, tickable: true, title: 'Move the ground beef to the fridge' };
var panel = { _moves: { moves: [move] }, _featured: null };
(async function () {
  await toggleTodayMove(panel, 'fridge:7', true);
  var t = TOASTS[0];
  await t.action.onClick();
  console.log(JSON.stringify({ text: t.text, label: t.label, hold: t.hold, posts: POSTS }));
})();
""")
    assert out == {"text": "Move the ground beef to the fridge was ticked off", "label": "Undo",
                   "hold": 6000, "posts": [True, False]}


@_needs_node
def test_a_tick_the_server_did_not_take_still_says_nothing():
    """A shop row (not tickable) leaving the list is not a tick to confirm."""
    out = _run(_TICK_STUBS + _extract_async("toggleTodayMove", SHELL_JS) + "\n" + """
var FRESH = { moves: [] };
var move = { id: 'shop:x', kind: 'shop', done: false, tickable: false, title: 'Shop for tonight' };
var panel = { _moves: { moves: [move] }, _featured: null };
toggleTodayMove(panel, 'shop:x', true).then(function () { console.log(JSON.stringify(TOASTS)); });
""")
    assert out == []


# ---------- 36 + 37. The shop reminder and "Start by" close with the evening ----------

def test_shop_for_tonight_goes_once_the_evening_is_over():
    today = _tonight_planned(add_ingredients_to_grocery_list=True)
    # No dinner_window: dinner lands 6:30, the evening closes at 7:00
    # (the "Still good?" cutoff).
    assert _shop(today, _at(today, 18, 45))["title"] == "Shop for tonight"
    assert _shop(today, _at(today, 19, 0)) is not None
    assert _shop(today, _at(today, 19, 1)) is None
    assert _shop(today, _at(today, 21)) is None


def _cook(today, now):
    return next(m for m in moves.today_moves(day=today, now=now)["moves"]
                if m["kind"] == "cook" and m["slot"] == "dinner")


def test_start_by_goes_once_the_evening_is_over():
    today = _tonight_planned()
    assert "Start by 5:55" in _cook(today, _at(today, 17))["chips"]
    assert "Start by 5:55" in _cook(today, _at(today, 18, 45))["chips"]
    late = _cook(today, _at(today, 21))
    assert not [c for c in late["chips"] if c.startswith("Start by")]
    assert late["planned_start"], "the plan's own start is still on the move"
    assert not late["done"], "the cook itself stays, to tick"


def test_the_evening_rule_is_the_one_still_good_reads():
    today = _tonight_planned()
    for hour, minute in ((18, 59), (19, 1), (21, 0)):
        now = _at(today, hour, minute)
        assert moves.evening_over(now, None) == (tools.tonight_check(now=now)["reason"] == "evening_over")


# ---------- 41. Tonight's dinner stops being a question at 9pm ----------

def _open_tonight() -> datetime.date:
    today = household_today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=1)["weekly_plan_id"]
    tools.plan_slot_open(plan, today.isoformat(), "dinner", "Nothing fit tonight.")
    return today


def test_tonights_open_dinner_is_not_asked_from_nine():
    today = _open_tonight()
    asked = [i for i in weekly_plan.get_needs_you_items(now=_at(today, 20, 59)) if i["type"] == "dinner_open"]
    assert asked and asked[0]["title"] == "Tonight’s dinner needs your call"
    late = weekly_plan.get_needs_you_items(now=_at(today, 21))
    assert not [i for i in late if i.get("date") == today.isoformat() and i["type"].startswith("dinner")]


def test_todays_open_slot_row_goes_at_nine():
    today = _open_tonight()
    def rows(now):
        return [m for m in moves.today_moves(day=today, now=now)["moves"] if m["kind"] == "open_slot"]
    assert [m["title"] for m in rows(_at(today, 20, 30))] == ["Dinner needs your call"]
    assert rows(_at(today, 21)) == []
    assert rows(_at(today, 23, 55)) == []


# ---------- 42. A prep step is late at the cook's start ----------

def test_a_prep_step_for_tonight_is_late_at_the_cooks_start_time():
    today = _tonight_planned()  # 35 minutes for a 6:30 dinner: start by 5:55
    conn = get_conn()
    row = conn.execute("SELECT id, weekly_plan_id FROM meal_plan_entries WHERE date = ? AND slot = 'dinner'",
                       (today.isoformat(),)).fetchone()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id) VALUES (?, ?, ?, 'Marinate the chicken', 'Weeknight Chili', "
        "'pending', 'general', ?)",
        (tools.household_id(), row["weekly_plan_id"], today.isoformat(), row["id"]),
    )
    conn.commit()
    conn.close()

    def prep(now):
        return next(m for m in moves.today_moves(day=today, now=now)["moves"] if m["kind"] == "prep")
    assert prep(_at(today, 17, 55))["time_label"] == "by tonight"
    late = prep(_at(today, 17, 56))
    assert late["overdue"] and late["time_label"] == "still to do"


def test_a_prep_step_for_a_later_meal_keeps_the_evening():
    today = household_today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=2)["weekly_plan_id"]
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=4)
    entry = tools.plan_meal((today + datetime.timedelta(days=1)).isoformat(), "Bean Chili",
                            slot="dinner", weekly_plan_id=plan)["entry_id"]
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id) VALUES (?, ?, ?, 'Soak the beans', 'Bean Chili', "
        "'pending', 'general', ?)",
        (tools.household_id(), plan, today.isoformat(), entry),
    )
    conn.commit()
    conn.close()
    prep = next(m for m in moves.today_moves(day=today, now=_at(today, 18))["moves"] if m["kind"] == "prep")
    assert prep["time_label"] == "by tonight"


# ---------- 43. "leftovers from tonight" on tomorrow's card ----------

def test_tomorrows_leftovers_from_today_say_from_tonight():
    plan, today, _yday = _chain_plan()
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    cook, _fed = _chain(plan, today.isoformat(), [tomorrow])
    tools.check_off_meal(cook, "done")
    payload = moves.today_moves(day=today, now=_at(today, 20))
    assert payload["featured"] is None
    card = payload["tomorrow"]
    assert card is not None and card["kind"] == "reheat"
    assert card["detail"].startswith("leftovers from tonight")
    assert today.strftime("%A") not in card["detail"]
    # Read on tomorrow itself, the source night is a weekday again.
    on_the_day = next(m for m in moves.today_moves(day=tomorrow, now=_at(today + datetime.timedelta(days=1), 9))["moves"]
                      if m["kind"] == "reheat")
    assert on_the_day["meta"].startswith(f"leftovers from {today.strftime('%A')}")


def test_provenance_note_says_tonight_only_when_asked_about_a_later_day():
    from app.tools import day_meals
    meal = {"is_leftovers": True, "leftovers_headline": "Leftovers — Thursday's Chili",
            "leftovers_from": {"date": "2026-10-22", "slot": "dinner"}}
    assert day_meals.provenance_note(meal) == "leftovers from Thursday"
    assert day_meals.provenance_note(meal, today="2026-10-22") == "leftovers from tonight"
    lunch_src = dict(meal, leftovers_from={"date": "2026-10-22", "slot": "lunch"})
    assert day_meals.provenance_note(lunch_src, today="2026-10-22") == "leftovers from today"
    prepped = {"date": "2026-10-23", "prepped_ahead": {"date": "2026-10-22"}}
    assert day_meals.provenance_note(prepped, today="2026-10-22") == "prepped today"
    assert json.dumps(day_meals.provenance_note(prepped)) == '"prepped Thursday"'
