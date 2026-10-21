"""
A week's snacks come from a real list of good everyday snacks (Loop Board,
2026-10-21: "not the same apple-and-peanut-butter ideas").

The list is data/everyday_snacks.json. The drafting call is handed this
household's shortlist (everyday_snacks.prompt_context), and after the
counts are held everyday_snacks.enforce_snacks_from_list replaces any
snack that is off the list, one this house can't have, or one from the
no-repeat window — least recently used first, a nut butter becoming its
nut-free swap. A snack they asked for stays.

Every test stubs the model at agent.generate_weekly_plan_llm, and the
small re-pick call at swap_in_place._pick_replacement.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.tools import everyday_snacks, swap_in_place
from conftest import household_today

OFF_LIST = ["Apple with Peanut Butter", "Granola Bar and Grapes", "Banana with Peanut Butter",
            "Orange Slices with Pumpkin Seeds"]


def _monday(weeks_ahead: int = 1) -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7 * weeks_ahead)).isoformat()


def _slot(date, slot, name, ingredients=None, **extra):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": ingredients or [{"item": f"{name} stuff", "qty": "1"}],
         "reasoning": "", "food_groups": ["protein", "vegetable"]}
    d.update(extra)
    return d


def _week(week, snacks=OFF_LIST, tag="", extra=None):
    """Seven days; the four snacks two a day, each on three or four days."""
    out = []
    for i, date in enumerate(tools._week_dates(week)):
        out.append(_slot(date, "breakfast", f"Overnight Oats{tag}"))
        out.append(_slot(date, "lunch", f"Lentil Soup {date}"))
        out.append(_slot(date, "dinner", f"Roast Chicken {date}",
                         [{"item": "chicken thighs", "qty": "1 lb"}, {"item": "potatoes", "qty": "2"}]))
        for name in (snacks[i % len(snacks)], snacks[(i + 1) % len(snacks)]):
            out.append(_slot(date, "snack", name, **((extra or {}).get(name) or {})))
    return out


@pytest.fixture
def household(monkeypatch):
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.set_household_meal_preferences(snacks_per_day=2, mark_complete=False)
    tools.edit_preference("snack_dishes_per_week", 4)
    monkeypatch.setattr(swap_in_place, "_pick_replacement", lambda ctx: {})


def _snacks(plan_id) -> list[dict]:
    return [m for m in tools.get_weekly_plan(plan_id)["meals"]
            if m["slot"] == "snack" and m["slot_state"] == "planned"]


def _plan(monkeypatch, week, days) -> int:
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return agent.generate_weekly_plan(week)["weekly_plan_id"]


def _list_names() -> set[str]:
    with open(everyday_snacks.LIST_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)["snacks"]
    return {s["name"] for s in raw} | {s["nut_free_swap"]["name"] for s in raw if s.get("nut_free_swap")}


def test_a_planned_weeks_snacks_are_all_on_the_list(household, monkeypatch):
    """The card's test. CATCH: on main the model's invented snacks stand."""
    plan_id = _plan(monkeypatch, _monday(), _week(_monday()))
    snacks = _snacks(plan_id)
    assert len(snacks) == 14
    names = {m["meal"] for m in snacks}
    assert names <= _list_names(), names - _list_names()
    # The count and the day rules still hold: four snacks, two different a day.
    assert len(names) == 4, names
    per_day: dict[str, list[str]] = {}
    for m in snacks:
        per_day.setdefault(m["date"], []).append(m["meal"])
    assert all(len(v) == 2 and len(set(v)) == 2 for v in per_day.values()), per_day
    # And the list snacks are shopped for: each puts its own food on the list.
    tools.approve_weekly_plan(plan_id)
    bought = " | ".join(r["item"].lower() for r in tools.list_grocery_list())
    by_name = {v["name"]: v for s in everyday_snacks.load_list() for v in (s, s["swap"]) if v}
    for name in names:
        assert any(line[0].lower() in bought for line in by_name[name]["lines"]), (name, bought)


def test_four_weeks_rotate_through_the_list(household, monkeypatch):
    """Two snacks a day, four different a week, the model sending the same
    four every week — one of them a list snack it keeps reaching for: at
    least 12 different snacks over four weeks, and none repeated inside the
    two-week no-repeat window."""
    weeks = []
    favourite = OFF_LIST[:3] + ["Roasted Chickpeas"]
    for k in range(1, 5):
        week = _monday(k)
        plan_id = _plan(monkeypatch, week, _week(week, snacks=favourite, tag=f" {k}"))
        weeks.append({m["meal"] for m in _snacks(plan_id)})
        tools.approve_weekly_plan(plan_id)
    used = set().union(*weeks)
    assert len(used) >= 12, weeks
    assert used <= _list_names()
    for i in range(1, 4):
        window = weeks[max(0, i - 2):i]
        for earlier in window:
            assert not (weeks[i] & earlier), (i, weeks[i] & earlier)


def test_a_nut_allergy_gets_the_nut_free_swap_and_never_a_nut(household, monkeypatch):
    tools.set_member_dietary_restrictions("Sam", ["tree nut allergy", "peanut allergy"])
    nutty = ["Rice Cakes with Almond Butter and Banana", "Dates Stuffed with Peanut Butter",
             "Muhammara with Veg Sticks and Pita", "Ants on a Log with Almond Butter and Dried Cherries"]
    shortlist = everyday_snacks.prompt_context(tools.get_household_memory(), _monday())["pick_from"]
    assert not any("almond" in s["name"].lower() or "peanut" in s["name"].lower()
                   or "muhammara" in s["name"].lower() for s in shortlist), shortlist
    plan_id = _plan(monkeypatch, _monday(), _week(_monday(), snacks=nutty))
    names = {m["meal"] for m in _snacks(plan_id)}
    nut_list = {s["name"] for s in everyday_snacks.load_list()
                if {"nuts", "peanuts"} & set(s["allergens"])}
    assert not names & nut_list, names
    assert "Rice Cakes with Sunflower Butter and Banana" in names, names
    assert "Ants on a Log with Sunflower Butter and Dried Cherries" in names, names
    assert "Dates Stuffed with Goat Cheese" in names, names
    assert names <= _list_names()


def test_a_snack_they_asked_for_stays_off_list(household, monkeypatch):
    week = _monday()
    tools.save_week_intake(week, freeform="orange slices with pumpkin seeds for snack please")
    days = _week(week, extra={OFF_LIST[3]: {"derived_from": {"freeform": "orange slices for snack"}}})
    names = {m["meal"] for m in _snacks(_plan(monkeypatch, week, days))}
    assert OFF_LIST[3] in names
    assert names - {OFF_LIST[3]} <= _list_names(), names


def test_the_list_is_forty_real_snacks_each_two_food_groups():
    snacks = everyday_snacks.load_list()
    assert len(snacks) == 40
    known = {"nuts", "peanuts", "dairy", "egg", "gluten", "fish", "soy", "sesame", "legumes", "corn", "citrus"}
    for s in snacks:
        for v in (s, s["swap"]):
            if v:
                assert len(set(v["food_groups"]) & {"protein", "vegetable", "carb"}) >= 2, v["name"]
                assert set(v["allergens"]) <= known, v["name"]
                assert v["lines"], v["name"]
    by = {s["name"]: s for s in snacks}
    assert "nuts" in by["Muhammara with Veg Sticks and Pita"]["allergens"]
    assert not {"nuts", "peanuts"} & set(
        by["Homemade Trail Mix with Seeds, Dried Fruit and Dark Chocolate"]["allergens"])
