"""
Surprise me means new to you: nothing you've had through Pomona before.

Emily, 2026-09-21: "I put surprise me, but they seem similar to other
suggestions I've received before … I've had all these recipes before
through Pomona." With Surprise me as the mood:

  * the drafting prompt carries every dinner and lunch from every past
    Pomona plan for this household (drafted or approved, all time) as
    don't-repeat, oldest first, with the last two weeks marked never
    (meal_variety.surprise_context);
  * a dish the model sends anyway is re-picked quietly after generation,
    with the repeat on avoid (meal_variety.repick_repeats) — never a card,
    never an open slot;
  * the opener says it when true: "Nine new dishes — nothing you've had
    from me before." (draft_opener).

With any other mood, none of this runs and the two-week window stays the
default. Every test here stubs the model.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import draft_opener, meal_variety, swap_in_place as sip
from conftest import prompt_literals


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, **extra):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
         "food_groups": ["protein", "vegetable", "carb"]}
    d.update(extra)
    return d


def _week(week: str, dinners: list[str], lunches: list[str] | None = None) -> list[dict]:
    out = []
    dates = tools._week_dates(week)
    lunches = lunches or ["Chickpea salad"] * 7
    for date, dinner, lunch in zip(dates, dinners, lunches):
        out.append(_slot(date, "breakfast", "Overnight oats"))
        out.append(_slot(date, "snack", "Apple"))
        out.append(_slot(date, "lunch", lunch))
        out.append(_slot(date, "dinner", dinner))
    return out


PAST_DINNERS = ["Chili", "Tacos", "Salmon", "Kofte", "Burgers", "Shrimp", "Pad Thai"]
NEW_DINNERS = ["Lamb stew", "Miso cod", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal"]


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


@pytest.fixture
def past_week(stub_model):
    """Last week's plan, drafted and never approved — still food Pomona
    suggested them, which is what "had it through Pomona" means."""
    week = _monday(0)
    stub_model(_week(week, PAST_DINNERS, ["Greek salad"] * 7))
    return agent.generate_weekly_plan(week)


def _plan_id_for(week: str) -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM weekly_plans WHERE week_start_date = ? ORDER BY id DESC", (week,)).fetchone()
    conn.close()
    return row["id"]


def _dinner(plan_id: int, date: str) -> dict:
    return next(m for m in tools.get_weekly_plan(plan_id)["meals"] if m["date"] == date and m["slot"] == "dinner")


def _pick(name: str) -> dict:
    return {
        "meal_name": name, "reason": "new to you",
        "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
        "instructions": ["Cook.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 20,
    }


# ---------- the history handed over ----------

def test_with_surprise_me_the_prompt_carries_everything_they_have_had_from_pomona(past_week, stub_model):
    week = _monday(1)
    tools.save_week_intake(week, moods=[tools.SURPRISE_MOOD])
    seen = stub_model(_week(week, NEW_DINNERS))

    agent.generate_weekly_plan(week)

    surprise = seen["ctx"]["surprise_me"]
    # Every past dinner and lunch, oldest first; breakfast and snack are
    # not the no-repeat rule's business.
    assert surprise["dont_repeat"] == ["Greek salad"] + PAST_DINNERS
    assert "Overnight oats" not in surprise["dont_repeat"] and "Apple" not in surprise["dont_repeat"]
    # Last week is inside the two-week window, so it is never repeated.
    assert set(surprise["never"]) == set(surprise["dont_repeat"])
    # The two-week history the prompt already had is still there.
    assert seen["ctx"]["recent_history"]


def test_without_surprise_me_nothing_changes(past_week, stub_model):
    week = _monday(1)
    tools.save_week_intake(week, moods=["Something warm"])
    seen = stub_model(_week(week, NEW_DINNERS))
    agent.generate_weekly_plan(week)
    assert "surprise_me" not in seen["ctx"]


def test_a_first_week_has_no_history_to_be_new_against(stub_model):
    week = _monday(1)
    tools.save_week_intake(week, moods=[tools.SURPRISE_MOOD])
    assert meal_variety.surprise_context({"moods": [tools.SURPRISE_MOOD]}) is None
    seen = stub_model(_week(week, NEW_DINNERS))
    agent.generate_weekly_plan(week)
    assert "surprise_me" not in seen["ctx"]
    # No line claiming "nothing you've had before" of a household with no past.
    assert all("had from me" not in line for line in tools.get_week_menu(_plan_id_for(week))["draft_opener"])


def test_the_history_is_capped_at_the_newest_names(monkeypatch):
    monkeypatch.setattr(meal_variety, "SURPRISE_HISTORY_CAP", 3)
    monkeypatch.setattr(meal_variety, "household_dish_history", lambda exclude_plan_id=None: [
        {"name": n, "date": "2026-01-01", "recent": False} for n in ["A", "B", "C", "D", "E"]
    ])
    ctx = meal_variety.surprise_context({"moods": [tools.SURPRISE_MOOD]})
    assert ctx["dont_repeat"] == ["C", "D", "E"], "the oldest fall off — they are the ones a thin week may reach for"


def test_the_prompt_says_what_surprise_me_means():
    src = prompt_literals(agent.generate_weekly_plan_llm)
    assert "`surprise_me`" in src
    assert "NEW" in src and "dont_repeat" in src and "surprise_me.never" in src
    assert "oldest" in src.lower()


# ---------- a repeat is re-picked quietly ----------

def test_a_dish_they_have_had_before_is_repicked_with_the_repeat_on_avoid(past_week, stub_model, monkeypatch):
    week = _monday(1)
    tools.save_week_intake(week, moods=[tools.SURPRISE_MOOD])
    dates = tools._week_dates(week)
    dinners = list(NEW_DINNERS)
    dinners[1] = "Chili"  # had it last week
    stub_model(_week(week, dinners))
    picks = []

    def picker(context):
        picks.append(context)
        return _pick("Moussaka")

    monkeypatch.setattr(sip, "_pick_replacement", picker)

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    tuesday = _dinner(plan_id, dates[1])
    assert tuesday["meal"] == "Moussaka" and tuesday["slot_state"] == "planned"
    assert len(picks) == 1
    ctx = picks[0]
    assert ctx["slot"] == "dinner" and ctx["date"] == dates[1]
    assert "Chili" in ctx["avoid"]
    assert "Chili" in ctx["replacing_because"] and "surprised" in ctx["replacing_because"]
    conn = get_conn()
    derived = json.loads(conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (tuesday["entry_id"],)
    ).fetchone()[0] or "{}")
    conn.close()
    assert derived["surprise_repick"]["dropped"] == "Chili"
    # Nothing else was touched, and the week is whole.
    assert _dinner(plan_id, dates[0])["meal"] == "Lamb stew"
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    # And the opener says so — nothing they've had before.
    menu = tools.get_week_menu(plan_id)
    assert menu["draft_opener"][-1] == "Eight new dishes — nothing you’ve had from me before."


def test_a_repick_that_is_also_a_repeat_is_tried_again_then_left(past_week, stub_model, monkeypatch):
    week = _monday(1)
    tools.save_week_intake(week, moods=[tools.SURPRISE_MOOD])
    dates = tools._week_dates(week)
    dinners = list(NEW_DINNERS)
    dinners[2] = "Tacos"
    stub_model(_week(week, dinners))
    offered = ["Salmon", "Kofte"]  # both had before
    picks = []

    def stubborn(context):
        picks.append(context)
        return _pick(offered[len(picks) - 1])

    monkeypatch.setattr(sip, "_pick_replacement", stubborn)

    plan = agent.generate_weekly_plan(week)
    plan_id = plan["weekly_plan_id"]

    assert len(picks) == sip.MAX_PICK_ATTEMPTS
    assert "Salmon" in picks[1]["avoid"], "the failed attempt joins avoid"
    # A repeat beats an open slot: the dish stands, and the opener is honest.
    wednesday = _dinner(plan_id, dates[2])
    assert wednesday["meal"] == "Tacos" and wednesday["slot_state"] == "planned"
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    assert tools.get_week_menu(plan_id)["draft_opener"][-1] == "Seven new dishes; Tacos you’ve had from me before."


def test_a_dish_they_asked_for_by_name_is_never_repicked(past_week, stub_model, monkeypatch):
    week = _monday(1)
    tools.save_week_intake(week, moods=[tools.SURPRISE_MOOD], freeform="chili on Monday please")
    dates = tools._week_dates(week)
    days = _week(week, ["Chili"] + NEW_DINNERS[1:])
    next(d for d in days if d["date"] == dates[0] and d["slot"] == "dinner")["derived_from"] = {"freeform": "chili on Monday please"}
    stub_model(days)
    picks = []
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: (picks.append(ctx), _pick("Moussaka"))[1])

    plan = agent.generate_weekly_plan(week)
    assert _dinner(plan["weekly_plan_id"], dates[0])["meal"] == "Chili"
    assert picks == []


def test_the_repick_spends_the_shared_budget_and_stops(past_week, stub_model, monkeypatch):
    week = _monday(1)
    tools.save_week_intake(week, moods=[tools.SURPRISE_MOOD])
    stub_model(_week(week, PAST_DINNERS))  # every dinner a repeat
    calls = []

    def stubborn(context):
        calls.append(1)
        return _pick("Chili")  # always a repeat

    monkeypatch.setattr(sip, "_pick_replacement", stubborn)

    plan = agent.generate_weekly_plan(week)
    from app.tools import allergen_gate
    assert len(calls) == allergen_gate.MAX_REPICK_CALLS
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True
    assert all(m["slot_state"] == "planned" for m in tools.get_weekly_plan(plan["weekly_plan_id"])["meals"])


def test_without_surprise_me_a_repeat_from_last_week_is_reported_not_repicked(past_week, stub_model, monkeypatch):
    week = _monday(1)
    tools.save_week_intake(week, moods=["Something warm"])
    dinners = list(NEW_DINNERS)
    dinners[1] = "Chili"
    stub_model(_week(week, dinners))
    picks = []
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: (picks.append(ctx), _pick("Moussaka"))[1])
    # Last week has to be approved for the window line to count it.
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (past_week["weekly_plan_id"],))
    conn.commit()
    conn.close()

    plan = agent.generate_weekly_plan(week)
    assert picks == []
    assert tools.get_week_menu(plan["weekly_plan_id"])["draft_opener"][-1] == \
        "Seven new dishes; Chili back from the last two weeks."


# ---------- the opener, on its own ----------

def test_the_surprise_line_counts_against_all_time_not_the_window(monkeypatch):
    rows = [
        {"date": "2026-10-05", "slot": "dinner", "meal": "Lamb stew", "slot_state": "planned",
         "derived_from_json": None, "freeform_meal": None},
        {"date": "2026-10-06", "slot": "dinner", "meal": "Chili", "slot_state": "planned",
         "derived_from_json": None, "freeform_meal": None},
        {"date": "2026-10-06", "slot": "lunch", "meal": "Greek salad", "slot_state": "planned",
         "derived_from_json": None, "freeform_meal": None},
    ]
    # Chili was on a plan months ago — outside the window, inside the history.
    monkeypatch.setattr(meal_variety, "household_dish_history", lambda exclude_plan_id=None: [
        {"name": "Chili", "date": "2026-03-02", "recent": False},
    ])
    monkeypatch.setattr(draft_opener, "recent_dish_names", lambda period_start, plan_id: None)
    days = [{"date": "2026-10-05"}, {"date": "2026-10-06"}]
    lines = draft_opener.build_opener(rows, {"moods": [tools.SURPRISE_MOOD]}, "2026-10-05", 2, days, plan_id=9)
    assert lines[-1] == "Two new dishes; Chili you’ve had from me before."
    lines = draft_opener.build_opener(rows, {"moods": ["Comfort food"]}, "2026-10-05", 2, days, plan_id=9)
    assert len(lines) == 1, "any other mood: the window line, and there is no window here"
