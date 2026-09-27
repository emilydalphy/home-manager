"""
Snacks follow a real "different snacks a week" count (Loop Board "Draft:
snacks follow a real 'different snacks a week' count", 2026-09-27).

Before this there was no such count: COUNT_FIELDS had no snacks,
enforce_snacks_per_day only set how many land on a day, snacks_per_week is
written as min(7, per_day * 7) by every snacks-a-day answer, and the
prompt still called that the distinct count. Now
meal_preferences.snack_dishes_per_week (default 2) is the "Snacks" stepper
under Different dishes a week, and meal_variety.enforce_snack_dishes folds
the draft's snacks to that many dishes while every day keeps its snacks a
day, none repeated on one day.

Every test stubs the model at agent.generate_weekly_plan_llm; the fold
itself makes no model call.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import meal_variety
from conftest import prompt_literals


def _monday(offset_weeks: int = 1) -> str:
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, **extra):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
         "food_groups": ["protein", "vegetable"]}
    d.update(extra)
    return d


SNACKS = [["Apple", "Almonds"], ["Yogurt", "Pear"], ["Hummus", "Carrots"], ["Cheese", "Crackers"],
          ["Popcorn", "Grapes"], ["Edamame", "Banana"], ["Trail mix", "Orange"]]


def _week(week, snacks=SNACKS, extra=None):
    out = []
    for i, date in enumerate(tools._week_dates(week)):
        out.append(_slot(date, "breakfast", "Overnight oats"))
        out.append(_slot(date, "lunch", "Chickpea salad"))
        out.append(_slot(date, "dinner", "Lemon chicken"))
        for s in snacks[i]:
            out.append(_slot(date, "snack", s, **((extra or {}).get((i, s)) or {})))
    return out


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _snacks(plan_id) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in tools.get_weekly_plan(plan_id)["meals"]:
        if m["slot"] == "snack" and m["slot_state"] == "planned":
            out.setdefault(m["date"], []).append(m["meal"])
    return out


def test_every_household_starts_at_two():
    assert tools.get_household_memory()["snack_dishes_per_week"] == 2


def test_the_count_is_a_whole_number_from_one_to_seven():
    tools.edit_preference("snack_dishes_per_week", 4)
    assert tools.get_household_memory()["snack_dishes_per_week"] == 4
    for bad in (0, 8, "lots"):
        with pytest.raises(ValueError):
            tools.edit_preference("snack_dishes_per_week", bad)


def test_the_draft_folds_fourteen_snacks_to_two_and_every_day_keeps_two(stub_model):
    """CATCH: on main the week keeps all fourteen."""
    week = _monday()
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)
    stub_model(_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    by_day = _snacks(plan_id)
    assert len({s.lower() for day in by_day.values() for s in day}) == 2
    assert all(len(day) == 2 and len(set(day)) == 2 for day in by_day.values()), by_day
    assert len(by_day) == 7


def test_three_different_snacks_when_they_say_three(stub_model):
    week = _monday()
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)
    tools.edit_preference("snack_dishes_per_week", 3)
    stub_model(_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    by_day = _snacks(plan_id)
    assert len({s.lower() for day in by_day.values() for s in day}) == 3
    assert all(len(day) == 2 and len(set(day)) == 2 for day in by_day.values()), by_day


def test_one_dish_a_week_never_makes_a_day_eat_the_same_snack_twice(stub_model):
    """With two a day, one dish would repeat on every day: the count is
    held at the most snacks one day carries (ASSUMPTION for Emily). The
    screen can no longer store this (below); an older row still could."""
    week = _monday()
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)
    conn = get_conn()
    conn.execute("UPDATE meal_preferences SET snack_dishes_per_week = 1")
    conn.commit()
    conn.close()
    stub_model(_week(week))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    by_day = _snacks(plan_id)
    assert all(len(set(day)) == len(day) == 2 for day in by_day.values())
    assert len({s.lower() for day in by_day.values() for s in day}) == 2


def test_a_snack_they_asked_for_is_kept(stub_model):
    week = _monday()
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)
    tools.save_week_intake(week, freeform="popcorn on Friday")
    stub_model(_week(week, extra={(4, "Popcorn"): {"derived_from": {"freeform": "popcorn on Friday"}}}))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    names = {s for day in _snacks(plan_id).values() for s in day}
    assert "Popcorn" in names
    assert len(names) == 2


def test_a_week_that_names_its_own_snack_count_is_left_alone():
    assert meal_variety.enforce_snack_dishes(1, 2, ["2026-10-05"], asks=("five snacks this week",))["skipped"]


def test_the_prompt_names_the_new_count():
    text = prompt_literals(agent.generate_weekly_plan_llm)
    assert "snack_dishes_per_week follows the exact same rule" in text
    assert "snacks_per_week follows the" not in text.replace("snack_dishes_per_week follows the", "")


def test_the_migration_adds_the_column_with_two_for_an_existing_row():
    conn = get_conn()
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(meal_preferences)").fetchall()}
    conn.close()
    assert "snack_dishes_per_week" in cols
    assert str(cols["snack_dishes_per_week"]["dflt_value"]) == "2"


def test_chat_can_set_both_snack_numbers():
    """The chat tool's allowed fields carry both snack numbers, and the
    tool itself accepts them (memory.edit_preference)."""
    tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "edit_preference")
    fields = tool["input_schema"]["properties"]["field"]["enum"]
    assert "snacks_per_day" in fields and "snack_dishes_per_week" in fields
    tools.edit_preference("snacks_per_day", 3)
    tools.edit_preference("snack_dishes_per_week", 4)
    mem = tools.get_household_memory()
    assert mem["snacks_per_day"] == 3 and mem["snack_dishes_per_week"] == 4


def test_snacks_never_goes_below_snacks_a_day_and_rises_with_it():
    """What we know never shows fewer different snacks than the draft keeps
    (reviewer, 2026-09-27)."""
    tools.edit_preference("snacks_per_day", 2)
    with pytest.raises(ValueError):
        tools.edit_preference("snack_dishes_per_week", 1)
    tools.edit_preference("snacks_per_day", 4)
    assert tools.get_household_memory()["snack_dishes_per_week"] == 4
    tools.edit_preference("snack_dishes_per_week", 6)
    tools.edit_preference("snacks_per_day", 3)
    assert tools.get_household_memory()["snack_dishes_per_week"] == 6, "lowering a day's snacks leaves it"


def test_the_prompt_has_one_distinct_snack_count():
    text = prompt_literals(agent.generate_weekly_plan_llm)
    assert "lunches_per_week (0-7) and snack_dishes_per_week (1-7) are counts of DISTINCT meals" in text
    assert "snacks_per_week (0-7) are counts of DISTINCT" not in text


def test_every_write_path_keeps_snacks_at_least_snacks_a_day():
    """Onboarding and the setup screens write through
    set_household_meal_preferences, not edit_preference (review round 2)."""
    tools.set_household_meal_preferences(snacks_per_day=4, mark_complete=False)
    assert tools.get_household_memory()["snack_dishes_per_week"] == 4
    tools.save_onboarding_answers(["Emily"], {}, "", [], [], 4, snacks_per_day=5)
    assert tools.get_household_memory()["snack_dishes_per_week"] == 5


def test_the_startup_backfill_raises_an_older_row():
    from app import db
    tools.set_household_meal_preferences(mark_complete=False)
    conn = get_conn()
    conn.execute("UPDATE meal_preferences SET snacks_per_day = 3, snack_dishes_per_week = 1")
    conn.commit()
    db._backfill_snack_dishes(conn)
    conn.commit()
    row = conn.execute("SELECT snack_dishes_per_week FROM meal_preferences").fetchone()
    conn.close()
    assert row["snack_dishes_per_week"] == 3
