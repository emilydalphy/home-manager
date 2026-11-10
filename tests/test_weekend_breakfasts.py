"""
Weekend breakfasts (Emily, 2026-11-09).

The 1,000-recipe spot check asked about Croissants (6 hours), Brioche,
Tsoureki, Ensaimada, Conchas and Beignets listed as breakfasts. Emily, on
the spot-check page: "Keep and add a weekend tag now". A library breakfast
of 2 hours or more carries "weekend": true on the starter list and never
lands on a Monday-to-Friday morning (breakfast_variety.weekend_only).
"""
from __future__ import annotations

import json
from pathlib import Path

from app import agent
from app.tools import breakfast_variety, recipe_library
from test_no_repeat_enforced import (  # noqa: F401 — fixtures
    FRESH_DINNERS, _monday, _names, _week, stub_model,
)
from test_variety_1031 import picker  # noqa: F401 — fixture

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "data" / "recipe_library_seed.json").read_text(encoding="utf-8"))
EMILYS_SIX = ("Croissants", "Brioche", "Tsoureki", "Ensaimada", "Conchas", "Beignets")


def _batch_minutes() -> dict[str, int]:
    from app.tools.recipe_research import dish_key
    out: dict[str, int] = {}
    for path in (ROOT / "data" / "recipe_library_batches").glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("recipes") or raw.get("dishes") or [raw]
        for r in items:
            if not isinstance(r, dict):
                continue
            rec = r.get("recipe", r)
            name = r.get("dish_name") or rec.get("dish_name")
            key = r.get("dish_key") or rec.get("dish_key") or (dish_key(name) if name else None)
            minutes = int(rec.get("prep_time_minutes") or 0) + int(rec.get("cook_time_minutes") or 0)
            out[key] = max(out.get(key, 0), minutes)
    return out


def test_emilys_six_are_weekend_breakfasts():
    for name in EMILYS_SIX:
        assert breakfast_variety.weekend_only(name), name
    assert not breakfast_variety.weekend_only("Overnight Oats")


def test_every_library_breakfast_of_two_hours_or_more_is_tagged_and_only_those():
    minutes = _batch_minutes()
    for d in SEED["dishes"]:
        if d.get("meal") != "breakfast":
            assert not d.get("weekend"), d["dish_name"]
            continue
        long = minutes.get(d["dish_key"], 0) >= breakfast_variety.WEEKEND_MIN_MINUTES
        assert bool(d.get("weekend")) == long, (d["dish_name"], minutes.get(d["dish_key"]))


def test_weekdays_are_monday_to_friday():
    monday = _monday(0)
    assert breakfast_variety.is_weekday(monday)
    import datetime as dt
    saturday = (dt.date.fromisoformat(monday) + dt.timedelta(days=5)).isoformat()
    assert not breakfast_variety.is_weekday(saturday)
    assert not breakfast_variety.is_weekday("not a date")


def test_a_croissant_morning_on_a_weekday_moves(stub_model, picker):
    """The draft put Croissants on Tuesday and Saturday: Saturday keeps
    them, Tuesday gets another of the week's breakfasts."""
    week = _monday(0)
    breakfasts = ["Overnight oats", "Croissants", "Yogurt parfait", "Overnight oats",
                  "Yogurt parfait", "Croissants", "Overnight oats"]
    stub_model(_week(week, FRESH_DINNERS, breakfasts=breakfasts))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    from app.tools import meal_variety
    days = {e["date"]: e["meal"] for e in meal_variety._load_slot_entries(plan_id, "breakfast")
            if e["slot_state"] == "planned"}
    for date, meal in days.items():
        if breakfast_variety.is_weekday(date):
            assert not breakfast_variety.weekend_only(meal), (date, meal)
    assert "Croissants" in _names(plan_id, "breakfast")


def test_a_library_top_up_skips_weekend_bakes_on_weekdays(stub_model, picker):
    """Oats on all seven mornings: the library fills the spare mornings, and
    the brioche only ever lands on a Saturday or Sunday."""
    for name in ("Brioche", "Egg Muffin Cups", "Blueberry Muffins", "Baked Oatmeal"):
        recipe_library.file_entry(name, ingredients=[{"item": "flour", "qty": "2 cups"}],
                                  instructions=["Mix.", "Bake."], default_servings=4, origin="seed")
    week = _monday(0)
    stub_model(_week(week, FRESH_DINNERS, breakfasts=["Overnight oats"] * 7))
    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]
    from app.tools import meal_variety
    for e in meal_variety._load_slot_entries(plan_id, "breakfast"):
        if e["meal"] == "Brioche":
            assert not breakfast_variety.is_weekday(e["date"]), e["date"]


def test_the_draft_is_told_which_breakfasts_are_for_the_weekend():
    names = ["Croissants", "Overnight Oats", "Beignets"]
    assert breakfast_variety.weekend_only_breakfasts(names) == ["Croissants", "Beignets"]
    # The drafting instructions name the list and the rule.
    source = (ROOT / "app" / "agent.py").read_text(encoding="utf-8")
    assert "`weekend_only_breakfasts` (a bake of 2 hours or more) goes on a Saturday or Sunday" in source
    assert 'context["weekend_only_breakfasts"] = weekend' in source
