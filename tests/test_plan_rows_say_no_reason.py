"""
No meal row says a reason any more — only schedule facts (Emily,
2026-09-27, decision C: "cut it everything").

The rows used to print the model's stored `reasoning` after the time
("35 min · A household favorite, keeps it simple"), or the slot's asked
fact ("Mexican, as asked"), and an open slot printed its open_reason
("Sunday I'd rather ask than guess: …"). What stays is what says WHEN a
meal is cooked: a prepped lunch batch's "Cook this Sunday for Monday and
Tuesday's lunches" — built by get_week_menu from what the plan records
(`schedule_note`), not read back out of free text — and a reheat night's
"from Monday" (leftover_from, unchanged).

Fails on main 8e046a6: there is no `schedule_note`, and the row renderers
print `reason`, `asked` and `open_reason`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import agent, tools

from test_weekday_lunches import _monday, _week_of, two_adults, seen_context  # noqa: F401 (fixtures)
from test_week_seven_tiles import _extract

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the renderers")


def _menu_lunches(plan_id: int) -> dict[str, dict]:
    menu = tools.get_week_menu(plan_id)
    return {d["date"]: d["lunch"] for d in menu["days"] if d.get("lunch")}, menu


def test_a_prepped_batch_says_when_it_is_cooked_and_nothing_else_does(two_adults, seen_context):
    _seen, stub = seen_context
    mon = _monday()
    dates = tools._week_dates(mon)
    tools.save_week_intake(mon, weekday_lunches={
        "prep_days": ["sunday", "wednesday"],
        "days": [
            {"date": dates[0], "kind": "prepped"},
            {"date": dates[1], "kind": "prepped"},
            {"date": dates[2], "kind": "leftovers"},
            {"date": dates[3], "kind": "prepped"},
            {"date": dates[4], "kind": "cooked"},
        ],
    })
    stub(_week_of(dates, lunches=["Chili", "Soup", "Salad", "Curry", "Sandwich", "Pita", "Toastie"],
                  dinners=["Tacos", "Roast Chicken", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"]))
    plan_id = agent.generate_weekly_plan(mon)["weekly_plan_id"]

    lunch, menu = _menu_lunches(plan_id)
    assert lunch[dates[0]]["schedule_note"] == "Cook this Sunday for Monday and Tuesday’s lunches."
    assert lunch[dates[3]]["schedule_note"] == "Cook this Wednesday for Thursday’s lunch."
    # Tuesday reheats Monday's batch: its fact is leftover_from, not a note.
    assert lunch[dates[1]]["schedule_note"] == ""
    assert lunch[dates[1]]["leftover_from"]["date"] == dates[0]
    # The model's reasoning ("Tacos because") is never a schedule note.
    for d in menu["days"]:
        for slot in ("breakfast", "dinner"):
            if d.get(slot) and d[slot].get("state") == "planned":
                assert d[slot]["schedule_note"] == "", (d["date"], slot)


def test_a_batch_that_freezes_a_portion_still_names_every_lunch(two_adults, seen_context):
    _seen, stub = seen_context
    mon = _monday()
    dates = tools._week_dates(mon)
    tools.save_week_intake(mon, weekday_lunches={
        "prep_days": ["sunday"],
        "days": [{"date": d, "kind": "prepped"} for d in dates[:4]],
    })
    stub(_week_of(dates, lunches=["Chili", "Soup", "Salad", "Curry", "Wrap", "Pita", "Toastie"],
                  dinners=["Tacos", "Roast", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"]))
    plan_id = agent.generate_weekly_plan(mon)["weekly_plan_id"]

    lunch, _menu = _menu_lunches(plan_id)
    assert lunch[dates[0]]["schedule_note"] == (
        "Cook this Sunday for Monday, Tuesday, Wednesday and Thursday’s lunches."
    )


def _row_prelude() -> str:
    return (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + _extract("wkRowMetaLine", SHELL_JS) + "\n"
        + _extract("wkRowMetaHtml", SHELL_JS) + "\n"
    )


@_needs_node
def test_the_row_line_is_the_time_and_the_schedule_fact_only():
    import nodeharness
    script = _row_prelude() + """
var reasonOnly = { state: 'planned', reason: 'A household favorite, keeps it simple.', asked: 'Mexican, as asked' };
var batch = { state: 'planned', reason: 'Cook this Sunday for Monday and Tuesday’s lunches.',
              schedule_note: 'Cook this Sunday for Monday and Tuesday’s lunches.' };
console.log(JSON.stringify({
  reasonOnly: wkRowMetaHtml(reasonOnly, wkRowMetaLine(reasonOnly, '35 min')),
  batch: wkRowMetaHtml(batch, wkRowMetaLine(batch, '40 min')),
  from: wkRowMetaHtml({ state: 'planned' }, wkRowMetaLine({ state: 'planned' }, 'from Monday'))
}));"""
    out = json.loads(nodeharness.run_node(script).stdout)
    assert out["reasonOnly"] == '<span class="wk-row-meta">35 min</span>'
    assert out["batch"] == '<span class="wk-row-meta">40 min · Cook this Sunday for Monday and Tuesday’s lunches</span>'
    assert out["from"] == '<span class="wk-row-meta">from Monday</span>'


def test_no_renderer_prints_a_reason_an_asked_fact_or_an_open_reason():
    assert "entry.reason" not in SHELL_JS
    assert "entry.asked" not in SHELL_JS
    assert "entry.open_reason" not in SHELL_JS
    open_card = SHELL_JS[SHELL_JS.index("function openSlotCardHtml("):]
    open_card = open_card[:open_card.index("\n  }\n")]
    assert "'Nothing planned for this ' + slotWord(slot) + ' yet.'" in open_card
    today_card = SHELL_JS[SHELL_JS.index("if (item.type === 'dinner_open') {"):]
    today_card = today_card[:today_card.index("data-card-type=\"dinner_open\"") + 400]
    assert "item.body" not in today_card
