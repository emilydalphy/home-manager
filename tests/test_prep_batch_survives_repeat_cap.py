"""
A prepped lunch batch keeps its cook when the no-three-in-a-row pass
rewrites the prep-day dinner that cooks it.

Loop Board card (found 2026-09-28 by the prep-day-ready-next-day builder,
raised to High the same day because it can hit Emily's real week — prep
Sunday + Tuesday, prepped lunches Monday to Friday, three dinners): "When
the no-three-in-a-row pass rewrites a prep-day dinner that is a lunch
batch's cook, the later lunches lose their batch."
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.tools import leftovers, weekday_lunches
from app.db import get_conn


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, minutes=30):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} bits", "qty": "2 lb", "category": "pantry"}],
            "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": minutes - 10}


def _week_of(dates, lunches, dinners):
    out = []
    for i, d in enumerate(dates):
        out.append(_slot(d, "breakfast", "Oats"))
        out.append(_slot(d, "lunch", lunches[i]))
        out.append(_slot(d, "dinner", dinners[i]))
    return out


def _rows(plan_id: int, slot: str) -> dict[str, dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot_state, mpe.derived_from_json, mpe.reasoning,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.slot = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date, mpe.id
        """,
        (plan_id, slot),
    ).fetchall()
    conn.close()
    return {r["date"]: dict(r, derived=json.loads(r["derived_from_json"] or "{}")) for r in rows}


@pytest.fixture
def two_adults():
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")


def _generate(monkeypatch, lunches, dinners, prep_days, start=None, count=3):
    """Emily's own week: prepped lunches Monday to Friday, three dinners."""
    if count:
        tools.set_household_meal_preferences(dinners_per_week=count)
    mon = start or _monday()
    dates = tools._week_dates(mon)
    tools.save_week_intake(mon, weekday_lunches={
        "prep_days": prep_days,
        "days": [{"date": d, "kind": "prepped"} for d in dates if weekday_lunches.is_weekday(d)],
    })
    week = _week_of(dates, lunches=lunches, dinners=dinners)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: week)
    return agent.generate_weekly_plan(mon)["weekly_plan_id"], dates


def _cook_of(plan_id, entry_id):
    link = tools.plan_leftover_chains(plan_id)["leftovers"].get(entry_id)
    return link and link["source"]["entry_id"]


@pytest.mark.parametrize("count", [3, None])
def test_emilys_week_keeps_tuesdays_batch_when_tuesdays_dinner_is_changed(two_adults, monkeypatch, count):
    """CATCH. Sun + Tue prep, Monday-start week, Chili drafted for every
    lunch and for Tuesday's dinner. Tuesday's dinner is the Tuesday batch's
    cook (the prep day's own meal), which makes Tue lunch / Tue dinner / Wed
    lunch three Chilis in a row; the no-three-in-a-row pass changes
    Tuesday's dinner — the only meal of that run it may change. On
    prep-day-ready-next-day (1aad128) Wed/Thu/Fri then held three unlinked
    Chilis, each cooked on its own, and no cook said "Prepped Tuesday"."""
    plan_id, dates = _generate(
        monkeypatch, lunches=["Chili"] * 5 + ["Pita", "Toastie"],
        dinners=["Tacos", "Chili", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"],
        prep_days=["sunday", "tuesday"], count=count,
    )
    mon, tue, wed, thu, fri = dates[:5]
    lunch, dinner = _rows(plan_id, "lunch"), _rows(plan_id, "dinner")

    # The rule held: Tuesday's dinner is no longer Chili.
    assert leftovers.dish_identity(dinner[tue]["meal"]) != "chili"
    assert not leftovers.long_runs(leftovers.run_keys(plan_id))

    # Tuesday's batch is one cook: Wednesday's lunch, stamped with the
    # Tuesday prep, and Thursday and Friday reheat it.
    cook = lunch[wed]
    assert cook["meal"] == "Chili"
    assert cook["derived"]["prep_date"] == tue
    assert cook["derived"]["prep_day"] == "tuesday"
    assert cook["reasoning"] == "Cook this Tuesday for Wednesday, Thursday and Friday’s lunches."
    assert _cook_of(plan_id, lunch[thu]["id"]) == cook["id"]
    assert _cook_of(plan_id, lunch[fri]["id"]) == cook["id"]
    assert lunch[thu]["derived"].get("cook_ahead") is True
    assert sorted(cook["derived"]["make_double_for"]) == [f"{thu}:lunch", f"{fri}:lunch"]

    # Sunday's batch is untouched.
    assert _cook_of(plan_id, lunch[tue]["id"]) == lunch[mon]["id"]

    # And the Cook tab reads one Tuesday batch for Wed–Fri.
    tue_batches = [b for b in weekday_lunches.prepped_batches(plan_id) if b["prep_date"] == tue]
    assert len(tue_batches) == 1
    assert tue_batches[0]["cook_entry_id"] == cook["id"]


def test_a_batch_cook_the_pass_leaves_alone_stays_on_the_prep_day(two_adults, monkeypatch):
    """GUARD: Tuesday's dinner is a different dish, so no run — the batch
    stays cooked on Tuesday's dinner (prep-day-ready-next-day's shape)."""
    plan_id, dates = _generate(
        monkeypatch, lunches=["Chili"] * 5 + ["Pita", "Toastie"],
        dinners=["Tacos", "Curry", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"],
        prep_days=["sunday", "tuesday"],
    )
    tue, wed, thu, fri = dates[1:5]
    lunch, dinner = _rows(plan_id, "lunch"), _rows(plan_id, "dinner")
    assert dinner[tue]["meal"] == "Curry" and dinner[tue]["derived"]["prep_day_cook"] is True
    for d in (wed, thu, fri):
        assert _cook_of(plan_id, lunch[d]["id"]) == dinner[tue]["id"]


def test_any_replace_of_the_batch_cook_can_hand_the_batch_on(two_adults, monkeypatch):
    """Unit, for a pass other than the run rule: the prep-day dinner is
    replaced through _replace_slot_entries (the write every pass uses) and
    rehome_prep_batch is handed the row as it was. The first lunch becomes
    the cook, the next reheats it, and a lunch eating a portion frozen on
    the old cook now eats one frozen on the new one, renamed for its day."""
    from app.tools import weekly_plan
    plan_id, dates = _generate(
        monkeypatch, lunches=["Chili"] * 5 + ["Pita", "Toastie"],
        dinners=["Tacos", "Curry", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"],
        prep_days=["sunday", "tuesday"],
    )
    tue, wed, thu, fri = dates[1:5]
    lunch, dinner = _rows(plan_id, "lunch"), _rows(plan_id, "dinner")
    old = dinner[tue]
    # Friday eats a frozen portion of Tuesday's Curry instead of a reheat.
    weekly_plan._replace_slot_entries(
        plan_id, [lunch[fri]["id"]], fri, "lunch", leftovers.freezer_night_name("Curry", tue),
        derived_from={leftovers.FROM_FREEZER_KEY: {"cook": f"entry_id:{old['id']}", "dish": "Curry"},
                      "constraint": weekday_lunches.CONSTRAINT},
    )
    old = _rows(plan_id, "dinner")[tue]
    weekly_plan._replace_slot_entries(plan_id, [old["id"]], tue, "dinner", "Tacos", derived_from={})
    moved = weekday_lunches.rehome_prep_batch(plan_id, {"id": old["id"], "meal": old["meal"], "derived": old["derived"]})

    assert moved == {"cook": wed, "linked": [thu], "frozen": [fri]}
    lunch = _rows(plan_id, "lunch")
    cook = lunch[wed]
    assert cook["meal"] == "Curry" and cook["derived"]["prep_date"] == tue
    assert "cook_ahead" not in cook["derived"] and "links_to" not in cook["derived"]
    assert cook["reasoning"] == "Cook this Tuesday for Wednesday, Thursday and Friday’s lunches."
    assert _cook_of(plan_id, lunch[thu]["id"]) == cook["id"]
    frozen = lunch[fri]
    assert frozen["meal"] == leftovers.freezer_night_name("Curry", wed)
    assert frozen["derived"][leftovers.FROM_FREEZER_KEY]["cook"] == f"entry_id:{cook['id']}"
    assert frozen["derived"]["constraint"] == weekday_lunches.CONSTRAINT


def test_a_row_that_was_no_batch_cook_is_left_alone(two_adults, monkeypatch):
    """GUARD: rehome_prep_batch is a no-op for an ordinary dinner."""
    plan_id, dates = _generate(
        monkeypatch, lunches=["Chili"] * 5 + ["Pita", "Toastie"],
        dinners=["Tacos", "Curry", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"],
        prep_days=["sunday", "tuesday"],
    )
    before = _rows(plan_id, "lunch")
    wed_dinner = _rows(plan_id, "dinner")[dates[2]]
    out = weekday_lunches.rehome_prep_batch(
        plan_id, {"id": wed_dinner["id"], "meal": wed_dinner["meal"], "derived": wed_dinner["derived"]})
    assert out == {"cook": None, "linked": [], "frozen": []}
    assert _rows(plan_id, "lunch") == before
