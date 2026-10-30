"""
The Plan Day step's cards and the root's "+ Add a meal" offer only what can apply.

Found by reading a seeded week at 390px (overnight hunt, 2026-10-29):

  - A planned_empty slot — Saturday's night off, a lunch the usual week
    has off — carried "Swap · I'll pick" on the Day step. It is never a
    decision (CLAUDE.md), and the tap could only fail: swap_in_place
    answers every planned_empty slot "There's no meal on that slot to
    swap." (404). Only an `away` need stood down.
  - Sunday's freezer night, seen on a Thursday, offered "Mark eaten" on
    the Day step while the same meal's Meal step (mealDockHtml,
    leftoversEatenNow) rightly did not.
  - "+ Add a meal" on a day whose only empty meal was planned_empty
    started "For Saturday's lunch, I'd like " in chat — and chat's
    plan_meal refuses exactly that slot ("deliberately empty").
"""
from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.tools import tonight, usual_week
from conftest import household_today

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    i = SHELL_JS.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS[j] == "{":
            depth += 1
        elif SHELL_JS[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS[start : j + 1]


def _run(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _acts_furniture(status: str = "approved") -> str:
    return (
        "var SWAP_LABEL = 'Swap \\u00b7 I\\u2019ll pick';\n"
        "var REHEAT_ACTION_LABEL = 'Mark eaten';\n"
        + "var weekState = { data: { status: " + json.dumps(status) + " } };\n"
        + _extract("escapeHtml") + "\n"
        + "function isSnackSlot(s){ return s === 'snack'; }\n"
        + "function daySlotEntry(d, s){ return d[s]; }\n"
        + "function swapLineHtml(){ return '<div class=\"wk-swap-line\"></div>'; }\n"
        + "function isRealCook(){ return true; }\n"
        + "function cookTimeChip(){ return ''; }\n"
        + "function planCookableNow(){ return true; }\n"
        + _extract("leftoversEatenNow") + "\n"
        + _extract("slotActionsHtml") + "\n"
    )


def _acts(day: dict, slot: str = "dinner", status: str = "approved"):
    return _run(
        _acts_furniture(status)
        + "var html = slotActionsHtml(" + json.dumps(day) + ", " + json.dumps(slot) + ", false);\n"
        + "console.log(JSON.stringify({ html: html,"
          " swap: html.indexOf('data-wk-swap=') !== -1,"
          " cook: html.indexOf('data-wk-cook=') !== -1 }));\n"
    )


def _seed_next_week() -> tuple[int, list[dict]]:
    """Next week (every day still ahead), approved, with a night off, a
    lunch the usual week has off, an out night and a freezer night."""
    today = household_today()
    start = today - timedelta(days=today.weekday()) + timedelta(days=7)
    d = lambda i: (start + timedelta(days=i)).isoformat()  # noqa: E731
    plan_id = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    for i in range(7):
        tools.plan_meal(d(i), "Oatmeal", slot="breakfast", weekly_plan_id=plan_id)
        if i >= 5:
            tools.plan_slot_empty(plan_id, d(i), "lunch", usual_week.OFF_REASON,
                                  derived_from={"constraint": usual_week.OFF_CONSTRAINT})
        else:
            tools.plan_meal(d(i), "Sandwiches", slot="lunch", weekly_plan_id=plan_id)
    for i in range(4):
        tools.plan_meal(d(i), f"Dinner {i}", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_slot_empty(plan_id, d(4), "dinner", "You’re out — I’ve planned nothing and bought nothing.",
                          derived_from={"tags": ["out"], "constraint": "nobody_home"})
    tools.plan_slot_empty(plan_id, d(5), "dinner", tonight.NIGHT_OFF_REASON,
                          derived_from={"constraint": tonight.NIGHT_OFF_CONSTRAINT})
    tools.plan_meal(d(6), "Leftovers from the freezer — Beef Stew", slot="dinner",
                    weekly_plan_id=plan_id, derived_from={"from_freezer": {"dish": "Beef Stew"}})
    tools.approve_weekly_plan(plan_id)
    days = tools.get_week_menu(plan_id)["days"]
    for day in days:
        day["isPast"], day["isToday"] = day["is_past"], day["is_today"]
    return plan_id, days


@_needs_node
def test_no_planned_empty_slot_of_a_real_week_is_offered_swap():
    """CATCH. Every planned_empty slot on a real approved week, all of it
    ahead: the out night, the night off, the usual-week lunches. On main
    the last four carried Swap."""
    _plan_id, days = _seed_next_week()
    empties = [(day, s) for day in days for s in ("breakfast", "lunch", "dinner")
               if day[s] and day[s]["state"] == "planned_empty"]
    assert len(empties) == 4
    for day, slot in empties:
        out = _acts(day, slot)
        assert out["html"] == "", (day["date"], slot, out["html"])


def test_the_swap_that_button_called_is_refused_by_the_server():
    """GUARD, green either way — why the button had to go rather than be
    fixed: the server has no swap for a planned_empty slot to perform."""
    plan_id, days = _seed_next_week()
    night_off = days[5]["dinner"]
    assert night_off["state"] == "planned_empty"
    with pytest.raises(ValueError, match="no meal on that slot"):
        tools.swap_meal_in_place(plan_id, night_off["entry_id"])


@_needs_node
def test_a_freezer_night_ahead_offers_no_mark_eaten():
    """CATCH. The real payload's Sunday freezer night, read a week early:
    the swap is the row's only action, as on its Meal step."""
    _plan_id, days = _seed_next_week()
    sunday = days[6]
    assert sunday["dinner"]["source"] == "leftovers"
    out = _acts(sunday, "dinner")
    assert out["cook"] is False
    assert out["swap"] is True


@_needs_node
def test_a_reheat_night_today_still_offers_mark_eaten():
    """GUARD for today (the narrowing is days ahead and drafts only); the
    draft half is a CATCH — main offered Mark eaten on a draft's reheat."""
    entry = {"title": "Leftovers — Monday’s Chili", "state": "planned", "source": "leftovers",
             "meta": "reheat", "entry_id": 4}
    today = {"date": "2026-10-29", "isPast": False, "isToday": True, "dinner": entry}
    assert _acts(today)["cook"] is True
    assert _acts(today, status="draft")["cook"] is False


@_needs_node
def test_an_ordinary_cook_ahead_keeps_cook_this():
    """GUARD — a real cook on a day ahead still opens its recipe."""
    entry = {"title": "Chili", "state": "planned", "source": "plan", "meta": "35 min", "entry_id": 5}
    ahead = {"date": "2026-10-31", "isPast": False, "isToday": False, "dinner": entry}
    out = _acts(ahead)
    assert out["cook"] is True and out["swap"] is True


def _add_meal(day: dict):
    return _run(
        "var WEEK_SLOTS = ['breakfast', 'lunch', 'dinner'];\n"
        "var asked = null;\n"
        "function openAskSheet(t){ asked = t; }\n"
        "function daySlotEntry(d, s){ return d[s]; }\n"
        "function slotWord(s){ return s; }\n"
        + _extract("dayName") + "\n"
        + _extract("wkAddMealFor") + "\n"
        + "var steps = { querySelector: function(){ return null; } };\n"
        + "wkAddMealFor(null, steps, " + json.dumps(day) + ");\n"
        + "console.log(JSON.stringify({ asked: asked }));\n"
    )


@_needs_node
def test_add_a_meal_skips_a_planned_empty_lunch():
    """CATCH. Saturday of the real week: breakfast planned, lunch off in the
    usual week, dinner a night off. Main started "For Saturday’s lunch,
    I’d like " — a slot chat refuses."""
    _plan_id, days = _seed_next_week()
    saturday = days[5]
    assert saturday["lunch"]["state"] == "planned_empty"
    out = _add_meal(saturday)
    assert out["asked"] == "Add something to Saturday — "


@_needs_node
def test_add_a_meal_still_goes_to_an_open_slot():
    """A real gap is still where Add a meal goes. Red on main too: the
    planned_empty lunch came first and took the sentence."""
    day = {"date": "2026-10-31", "breakfast": {"state": "planned", "title": "Oatmeal"},
           "lunch": {"state": "planned_empty", "title": "Not planned"},
           "dinner": {"state": "open", "title": "Your call"}}
    assert _add_meal(day)["asked"] == "For Saturday’s dinner, I’d like "
