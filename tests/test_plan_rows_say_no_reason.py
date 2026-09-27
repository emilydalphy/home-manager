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
    today_card = SHELL_JS[SHELL_JS.index("if (item.type === 'dinner_open') {"):]
    today_card = today_card[:today_card.index("data-card-type=\"dinner_open\"") + 500]
    assert "item.body" not in today_card
    assert "(item.question ? '<div class=\"ny-summary\">' + escapeHtml(item.question) + '</div>' : '')" in today_card


# ---------- open slots: a real question stays, an explanation goes ----------

HOSTING = "You’re hosting Thanksgiving for 12 — what’s the main? Tell me and I’ll build the rest around it."
ALLERGY = "I couldn’t find a dinner without peanuts for Sam."
ALLERGY_OLD = "I couldn’t find a dinner without peanuts for Sam — I’d rather ask than guess."
HOLIDAY = "Plans for Thanksgiving changed — what would you like for dinner?"
EXPLAINING = "Sunday I’d rather ask than guess: I’d pencilled in leftovers from a meal that hasn’t happened yet."


def test_the_allergen_sentence_is_the_fact_without_the_tail():
    from app.tools import allergen_gate
    said = allergen_gate.open_reason("dinner", [{"food": "peanuts", "restriction": "peanuts", "member": "Sam"}])
    assert "rather ask" not in said and said.endswith(".")
    assert allergen_gate.is_open_reason(said)


def test_only_a_real_question_survives_as_the_open_slots_line():
    from app.tools.weekly_plan import open_slot_question as q
    assert q(HOSTING, json.dumps({"constraint": "hosting", "holiday": "Thanksgiving"})) == HOSTING
    assert q(HOLIDAY, json.dumps({"constraint": "holiday_answer_changed", "holiday": "Thanksgiving"})) == HOLIDAY
    assert q(ALLERGY, json.dumps({"constraint": "allergen"})) == ALLERGY
    # A row written before the tail went is said without it.
    assert q(ALLERGY_OLD, json.dumps({"constraint": "allergen"})) == ALLERGY
    # The allergen sweep drops through the stepper, filed as a cut-back:
    # the sentence is what marks it.
    assert q(ALLERGY, json.dumps({"constraint": "household_cut_back", "dish": "Satay"})) == ALLERGY
    assert q(ALLERGY_OLD, json.dumps({"constraint": "household_cut_back", "dish": "Satay"})) == ALLERGY
    assert q(EXPLAINING, json.dumps({})) == ""
    assert q("You cut Chili back, so this one is yours to fill.",
             json.dumps({"constraint": "household_cut_back"})) == ""
    assert q("", json.dumps({"constraint": "hosting"})) == ""


def test_the_menu_and_today_carry_the_question_or_nothing(two_adults, seen_context):
    from app.tools import weekly_plan
    _seen, stub = seen_context
    mon = _monday(0)
    dates = tools._week_dates(mon)
    stub(_week_of(dates, lunches=["Chili", "Soup", "Salad", "Curry", "Wrap", "Pita", "Toastie"],
                  dinners=["Tacos", "Roast", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"]))
    plan_id = agent.generate_weekly_plan(mon)["weekly_plan_id"]
    for d, reason, derived in ((dates[5], HOSTING, {"constraint": "hosting", "holiday": "Thanksgiving"}),
                               (dates[6], EXPLAINING, {})):
        weekly_plan.clear_plan_slot(plan_id, d, "dinner")
        weekly_plan.plan_slot_open(weekly_plan_id=plan_id, meal_date=d, slot="dinner",
                                   open_reason=reason, derived_from=derived)
    days = {d["date"]: d for d in tools.get_week_menu(plan_id)["days"]}
    assert days[dates[5]]["dinner"]["open_question"] == HOSTING
    assert days[dates[6]]["dinner"]["open_question"] == ""


@_needs_node
def test_the_open_card_says_the_question_or_the_plain_line():
    import nodeharness
    script = (
        _extract("escapeHtml", SHELL_JS) + "\n"
        + "function isSnackSlot(s) { return false; }\n"
        + _extract("slotWord", SHELL_JS) + "\n"
        + _extract("openSlotCardHtml", SHELL_JS) + "\n"
        + f"""console.log(JSON.stringify({{
  hosting: openSlotCardHtml('2026-10-08', 'dinner', {{ open_reason: {json.dumps(HOSTING)}, open_question: {json.dumps(HOSTING)}, options: [] }}),
  explaining: openSlotCardHtml('2026-10-04', 'lunch', {{ open_reason: {json.dumps(EXPLAINING)}, open_question: '', options: [] }})
}}));"""
    )
    out = json.loads(nodeharness.run_node(script).stdout)
    assert "what’s the main?" in out["hosting"] and "Nothing planned" not in out["hosting"]
    assert '<div class="week-open-reason">Nothing planned for this lunch yet.</div>' in out["explaining"]
    assert "rather ask" not in out["explaining"]
