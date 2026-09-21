"""
"The draft says what it did with what you told it" (Loop Board, High —
widened 2026-09-21), the server half:

  * a typed instruction about a MEAL with no day named reaches the drafting
    prompt as a scope over EVERY one of that meal's slots — Emily,
    2026-09-20: "Mexican for lunch, chicken breast, potatoes and veggies
    for dinner" and only Monday got both;
  * the variety window is ONE constant (two weeks), read by the history
    the prompt is handed, the prompt's own words, the quality check and the
    draft's opening line;
  * the draft opens with two lines built from what was actually stored:
    what it planned around (each typed request with the days it reached,
    the days left free) and one thing worth knowing (a request nothing
    used, named — never silently dropped; else "Nine new dishes — nothing
    from the last two weeks");
  * every planned row carries the one fact it was asked for ("Mexican, as
    asked", "packs cold") beside its stored reason.

The screen half is in tests/test_draft_front_door.py.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import draft_opener, meal_variety, plan_quality
from conftest import agent_source, prompt_literals


# ---------------------------------------------------------------------------
# 1. The reach of a typed request
# ---------------------------------------------------------------------------

WEEK = tools.period_dates("2026-09-21", 7)  # Monday to Sunday


def test_a_meal_type_instruction_with_no_day_reaches_every_slot_of_that_meal():
    scopes = tools.freeform_meal_scopes(
        "Mexican for lunch, chicken breast, potatoes and veggies for dinner", WEEK
    )
    assert [(s["meal"], s["applies_to"]) for s in scopes] == [("lunch", "every"), ("dinner", "every")]
    assert scopes[0]["words"] == "Mexican for lunch"
    assert scopes[1]["words"] == "chicken breast, potatoes and veggies for dinner"
    assert scopes[0]["dates"] == WEEK and scopes[1]["dates"] == WEEK, "all seven, not Monday"


def test_a_named_day_reaches_that_day_only_and_a_count_leaves_the_picking_open():
    friday = tools.freeform_meal_scopes("Friday is pizza night. I want to use the lamb in the freezer.", WEEK)
    assert friday == [{"words": "Friday is pizza night", "meal": "dinner", "applies_to": "named", "dates": ["2026-09-25"]}]
    weekend = tools.freeform_meal_scopes("big breakfast on the weekend", WEEK)
    assert weekend[0]["dates"] == ["2026-09-26", "2026-09-27"]
    twice = tools.freeform_meal_scopes("Mexican twice for dinner", WEEK)
    assert twice[0]["applies_to"] == "some" and twice[0]["dates"] == []


def test_a_sentence_that_names_no_meal_is_not_a_meal_type_request():
    assert tools.freeform_meal_scopes("nothing special, keep it cheap", WEEK) == []
    assert tools.freeform_meal_scopes("", WEEK) == [] and tools.freeform_meal_scopes(None, WEEK) == []


def test_the_intake_context_carries_the_scope_and_the_prompt_says_what_it_means():
    intake = {"freeform": "Mexican for lunch", "night_tags": {}, "guest_counts": {}, "household_snapshot": {}}
    ctx = agent._intake_generation_context(intake, WEEK)
    assert ctx["freeform"] == "Mexican for lunch"
    assert ctx["freeform_scope"] == [{"words": "Mexican for lunch", "meal": "lunch", "applies_to": "every", "dates": WEEK}]

    text = prompt_literals(agent.generate_weekly_plan_llm)
    assert "`intake.freeform_scope` spells out the REACH of each request" in text
    assert "every lunch is Mexican, every dinner is built on chicken and potatoes" in text
    assert "Satisfying it on Monday and planning the rest of the week" in text
    assert "Quote the request's words in derived_from.freeform on EVERY" in text
    # The context builder is handed the period's dates, so the scope is
    # over the days actually being planned.
    assert "_intake_generation_context(intake, tools.period_dates(content_start_date, day_count))" in agent_source()


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen
    return _stub


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, **extra):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because"}
    d.update(extra)
    return d


def _full_week(week: str, lunch=None, dinner=None) -> list[dict]:
    """Every slot filled; `lunch`/`dinner` are per-date builders."""
    out = []
    for date in tools._week_dates(week):
        out.append(_slot(date, "breakfast", "Overnight oats"))
        out.append(_slot(date, "snack", "Apple"))
        out.append(lunch(date) if lunch else _slot(date, "lunch", "Chickpea salad"))
        out.append(dinner(date) if dinner else _slot(date, "dinner", "Lemon chicken"))
    return out


def test_generation_hands_the_model_the_scope_over_every_matching_slot(stub_model):
    week = _monday()
    tools.save_week_intake(week, freeform="Mexican for lunch, chicken breast, potatoes and veggies for dinner")
    seen = stub_model(_full_week(week))
    agent.generate_weekly_plan(week)
    scope = seen["ctx"]["intake"]["freeform_scope"]
    assert [s["meal"] for s in scope] == ["lunch", "dinner"]
    assert all(s["applies_to"] == "every" and len(s["dates"]) == 7 for s in scope)


# ---------------------------------------------------------------------------
# 2. One window, everywhere
# ---------------------------------------------------------------------------

def test_the_variety_window_is_one_constant_of_two_weeks():
    assert meal_variety.VARIETY_WINDOW_WEEKS == 2
    assert meal_variety.variety_window_words() == "the last two weeks"
    src = agent_source()
    assert "get_recent_meal_history(weeks=_meal_variety.VARIETY_WINDOW_WEEKS)" in src
    assert "weeks=3" not in src
    text = prompt_literals(agent.generate_weekly_plan_llm)
    assert "within the last 3 weeks" not in text
    assert "is NOT drafted again unless the household asked for" in text
    # The quality check's message names the same window.
    v = plan_quality._dinner_repeat_in_history(
        [{"date": "2026-09-21", "slot": "dinner", "slot_state": "planned", "meal_name": "Chili"}],
        {"recent_history": [{"slot": "dinner", "meal": "Chili"}]},
    )
    assert len(v) == 1 and "the last two weeks" in v[0].message


# ---------------------------------------------------------------------------
# 3. The opener and the row's fact — pure helpers
# ---------------------------------------------------------------------------

def test_days_phrase_collapses_runs_and_names_the_whole_week():
    assert draft_opener.days_phrase(WEEK[:4], WEEK) == "Mon–Thu"
    assert draft_opener.days_phrase([WEEK[0], WEEK[2]], WEEK) == "Mon, Wed"
    assert draft_opener.days_phrase([WEEK[4]], WEEK) == "Friday"
    assert draft_opener.days_phrase(WEEK, WEEK) == "all week"
    assert draft_opener.days_phrase([WEEK[0], WEEK[1], WEEK[2], WEEK[5]], WEEK) == "Mon–Wed, Sat"


def test_the_row_fact_is_read_off_what_the_household_asked_never_the_name():
    fact = draft_opener.asked_fact
    assert fact({"derived_from": {"inputs": ["cuisines:mexican"], "freeform": "Mexican for lunch"}}) == "Mexican, as asked"
    assert fact({"derived_from": {"constraint": "packed_lunch"}}) == "packs cold"
    assert fact({"derived_from": json.dumps({"freeform": "chicken and potatoes for dinner"})}) == "as asked"
    assert fact({"derived_from": {"inputs": ["mood:comfort_food"]}}) is None
    assert fact({"derived_from": None}) is None
    assert fact({"slot_state": "open", "derived_from": {"freeform": "x"}}) is None


# ---------------------------------------------------------------------------
# 4. The opener, end to end through get_week_menu
# ---------------------------------------------------------------------------

def _menu_for(week: str) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT id FROM weekly_plans WHERE week_start_date = ? ORDER BY id DESC", (week,)).fetchone()
    conn.close()
    return tools.get_week_menu(row["id"])


def test_the_draft_says_where_each_typed_request_went_and_every_row_carries_its_fact(stub_model):
    week = _monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, freeform="Mexican for lunch. Chicken and potatoes for dinner.",
                           night_tags={dates[4]: ["out"]})
    mexican = ["Chicken al pastor tacos", "Black bean bowls", "Chicken al pastor tacos", "Black bean bowls"]

    def lunch(date):
        i = dates.index(date)
        if i < 4:
            return _slot(date, "lunch", mexican[i], derived_from={"freeform": "Mexican for lunch", "inputs": ["cuisines:mexican"]})
        return _slot(date, "lunch", "Chickpea salad")

    def dinner(date):
        return _slot(date, "dinner", "Seared chicken, roast potatoes",
                     derived_from={"freeform": "Chicken and potatoes for dinner"})

    stub_model(_full_week(week, lunch=lunch, dinner=dinner))
    agent.generate_weekly_plan(week)
    menu = _menu_for(week)
    assert menu["status"] != "approved"
    lines = menu["draft_opener"]
    assert lines[0] == "Mexican for lunch Mon–Thu and Chicken and potatoes for dinner Mon–Thu, Sat, Sun, as you asked, Friday left free."
    # Every row says the one thing it was asked for, beside its stored reason.
    monday = menu["days"][0]
    assert monday["lunch"]["asked"] == "Mexican, as asked"
    assert monday["dinner"]["asked"] == "as asked"
    assert monday["breakfast"]["asked"] is None
    assert monday["lunch"]["reason"] == "Chicken al pastor tacos because"
    # A first week has no window to compare against, so no novelty claim.
    assert len(lines) == 1


def test_a_request_nothing_used_is_named_not_dropped(stub_model):
    week = _monday()
    tools.save_week_intake(week, freeform="Friday is pizza night. Use the lamb in the freezer.")

    def dinner(date):
        if date == tools._week_dates(week)[4]:
            return _slot(date, "dinner", "Margherita pizza", derived_from={"freeform": "Friday is pizza night"})
        return _slot(date, "dinner", "Lemon chicken")

    stub_model(_full_week(week, dinner=dinner))
    agent.generate_weekly_plan(week)
    lines = _menu_for(week)["draft_opener"]
    assert lines[0] == "Friday is pizza night Friday, as you asked."
    assert lines[1] == "I couldn’t fit “Use the lamb in the freezer” in this week."


def test_an_ordinary_week_says_so_and_counts_the_dinners(stub_model):
    week = _monday()
    dinners = ["Chili", "Salmon", "Kofte", "Halloumi salad", "Burgers", "Shrimp", "Tacos"]
    stub_model(_full_week(week, dinner=lambda d: _slot(d, "dinner", dinners[tools._week_dates(week).index(d)])))
    agent.generate_weekly_plan(week)
    assert _menu_for(week)["draft_opener"] == ["An ordinary week — seven dinners, none repeated."]


def test_the_novelty_line_counts_against_the_last_two_weeks_of_approved_food(stub_model):
    last_week = _monday(0)
    this_week = _monday(1)
    # Last week, approved: Chili on Monday, Tacos on Tuesday.
    stub_model(_full_week(last_week, dinner=lambda d: _slot(
        d, "dinner", {0: "Chili", 1: "Tacos"}.get(tools._week_dates(last_week).index(d), "Salmon"))))
    prev = agent.generate_weekly_plan(last_week)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (prev["weekly_plan_id"],))
    conn.commit()
    conn.close()
    # This week's draft brings the chili back.
    dinners = ["Chili", "Kofte", "Halloumi salad", "Burgers", "Shrimp", "Kofte", "Burgers"]
    stub_model(_full_week(this_week, dinner=lambda d: _slot(d, "dinner", dinners[tools._week_dates(this_week).index(d)])))
    agent.generate_weekly_plan(this_week)
    lines = _menu_for(this_week)["draft_opener"]
    # Breakfast (Overnight oats), snack (Apple) and lunch (Chickpea salad)
    # were on last week's approved plan too: with Chili that is four back,
    # and Kofte, Halloumi salad, Burgers, Shrimp are the four new ones.
    assert lines[1] == "Four new dishes, four back from the last two weeks."

    # With nothing repeated, the line says so — the words Emily asked for.
    fresh = ["Kofte", "Halloumi salad", "Burgers", "Shrimp", "Kofte", "Burgers", "Shrimp"]

    def dinner(d):
        return _slot(d, "dinner", fresh[tools._week_dates(this_week).index(d)])

    def lunch(d):
        return _slot(d, "lunch", "Greek salad")

    week_two = [s for s in _full_week(this_week, lunch=lunch, dinner=dinner)]
    for s in week_two:
        if s["slot"] == "breakfast":
            s["meal_name"] = "Boiled eggs"
        if s["slot"] == "snack":
            s["meal_name"] = "Carrots and hummus"
    stub_model(week_two)
    agent.generate_weekly_plan(this_week)
    assert _menu_for(this_week)["draft_opener"][1] == "Seven new dishes — nothing from the last two weeks."


def test_an_approved_week_has_no_opener(stub_model):
    week = _monday()
    stub_model(_full_week(week))
    plan = agent.generate_weekly_plan(week)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan["weekly_plan_id"],))
    conn.commit()
    conn.close()
    assert _menu_for(week)["draft_opener"] == []
