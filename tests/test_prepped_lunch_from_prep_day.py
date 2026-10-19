"""
A weekday lunch the household marked "prepped" with a prep day is planned
from that prep day's cook — never left as a generation_gap (Loop Board,
High bug, 2026-10-19).

Tester Home, plan 72: weekday lunches prepped 4 / leftovers 1, prep days
Sunday + Thursday, Thursday's lunch the leftovers of Wednesday's dinner and
Friday's lunch prepped Thursday. Thursday's dinner carried "Cook this
Thursday for Friday's lunch." and prep_day_cook, but its make_double_for
had no Friday lunch, and Friday's lunch was LUNCH · Your call · Pick with
open_reason "I couldn't settle this lunch without guessing…".

Root cause: the draft came back with no Friday lunch, and
weekday_lunches.apply_to_plan's batch loop skipped a batch day with no
planned row (`if not rows: continue`) after it had already written the
cook's note. The repeat fill can't take a weekday lunch the household
prepped (its fresh-cook cap is 20 minutes and every lunch in the week is a
batch), so the end-of-generation audit opened it.

Every test stubs the model at agent.generate_weekly_plan_llm; nothing here
makes a model call.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_today
from app import agent, tools
from app.db import get_conn
from app.tools import weekday_lunches


def _slot(date, slot, name, minutes=30):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} bits", "qty": "2 lb", "category": "pantry"}],
            "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": minutes - 10}


def _next_monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)


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


def _draft(monkeypatch, thursday_reheats_wednesday=False):
    """The Tester Home shape, with the draft sending no Friday lunch."""
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    tools.set_household_meal_preferences(dinners_per_week=4, lunches_per_week=3, mark_complete=False)
    mon = _next_monday()
    dates = [(mon + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    tools.save_week_intake(mon.isoformat(), weekday_lunches={
        "prep_days": ["sunday", "thursday"],
        "days": [{"date": dates[0], "kind": "prepped"}, {"date": dates[1], "kind": "prepped"},
                 {"date": dates[2], "kind": "prepped"}, {"date": dates[3], "kind": "leftovers"},
                 {"date": dates[4], "kind": "prepped"}],
    })
    dinners = ["Roast chicken", "Salmon", "Tofu stir-fry", "Thai Basil Beef Stir-Fry", "Pizza", "Curry", "Tacos"]
    # Every lunch is a 30-minute batch, so no weekday lunch fits the repeat
    # fill's 20-minute fresh-cook cap — the live shape.
    lunches = ["Chicken grain bowls"] * 3 + ["Tofu stir-fry", None, "Lentil soup", "Lentil soup"]
    days = []
    for i, d in enumerate(dates):
        days.append(_slot(d, "breakfast", "Oats"))
        dinner = _slot(d, "dinner", dinners[i])
        if thursday_reheats_wednesday and i == 3:
            dinner = _slot(d, "dinner", dinners[2])
            dinner["derived_from"] = {"links_to": f"{dates[2]}:dinner"}
        days.append(dinner)
        if lunches[i]:
            days.append(_slot(d, "lunch", lunches[i]))
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    plan_id = agent.generate_weekly_plan(mon.isoformat())["weekly_plan_id"]
    return plan_id, dates


@pytest.fixture
def tester_home(monkeypatch):
    return _draft(monkeypatch)


def test_fridays_prepped_lunch_is_planned_from_thursdays_dinner(tester_home):
    """CATCH: on main Friday's lunch is open (generation_gap) and missing
    from Thursday's make_double_for."""
    plan_id, dates = tester_home
    thu, fri = dates[3], dates[4]
    lunch, dinner = _rows(plan_id, "lunch"), _rows(plan_id, "dinner")
    cook = dinner[thu]
    assert cook["derived"].get("prep_day_cook") is True
    assert lunch[fri]["slot_state"] == "planned"
    assert lunch[fri]["derived"].get("constraint") != "generation_gap"
    assert lunch[fri]["meal"] == cook["meal"]
    assert lunch[fri]["derived"]["links_to"] == f"entry_id:{cook['id']}"
    # Sized to include it: the cook's extra names Friday's lunch.
    assert f"{fri}:lunch" in cook["derived"]["make_double_for"]
    assert "Friday" in cook["derived"]["make_double_note"]
    chains = tools.plan_leftover_chains(plan_id)
    assert chains["leftovers"][lunch[fri]["id"]]["source"]["entry_id"] == cook["id"]


def test_the_note_and_the_plan_agree_and_nothing_weekday_is_open(tester_home):
    plan_id, dates = tester_home
    dinner = _rows(plan_id, "dinner")
    assert dinner[dates[3]]["derived"]["prep_note"] == "Cook this Thursday for Friday’s lunch."
    menu = tools.get_week_menu(plan_id)
    fri = next(d for d in menu["days"] if d["date"] == dates[4])
    assert fri["lunch"]["state"] == "planned"
    for d in dates[:5]:
        assert _rows(plan_id, "lunch")[d]["slot_state"] == "planned", d
    # Mon–Wed still come from Sunday's batch (cooked on Monday's lunch,
    # since this Sunday is the day before the week).
    batches = {b["prep_date"]: b for b in weekday_lunches.prepped_batches(plan_id)}
    assert batches[dates[3]]["lunch_dates"] == [dates[4]]
    assert batches[(datetime.date.fromisoformat(dates[0]) - datetime.timedelta(days=1)).isoformat()][
        "lunch_dates"] == dates[:3]


def test_a_prep_day_dinner_that_lost_its_lunch_stops_claiming_it(tester_home):
    """If Friday can't be covered, the note doesn't claim it is: a later
    write takes Friday's lunch off the cook, and sync_prep_notes takes the
    batch's stamps and note off Thursday's dinner with it."""
    plan_id, dates = tester_home
    from app.tools import weekly_plan
    fri_row = _rows(plan_id, "lunch")[dates[4]]
    weekly_plan._replace_slot_entries(plan_id, [fri_row["id"]], dates[4], "lunch", "Toastie")
    weekday_lunches.sync_prep_notes(plan_id)
    cook = _rows(plan_id, "dinner")[dates[3]]["derived"]
    assert "prep_note" not in cook and "prep_date" not in cook and "prep_day_cook" not in cook
    assert dates[3] not in {b["prep_date"] for b in weekday_lunches.prepped_batches(plan_id)}


def test_a_lunch_cook_whose_batch_shrank_names_only_what_it_still_feeds(tester_home):
    plan_id, dates = tester_home
    from app.tools import weekly_plan
    weekly_plan._replace_slot_entries(plan_id, [_rows(plan_id, "lunch")[dates[2]]["id"]], dates[2], "lunch", "Toastie")
    weekday_lunches.sync_prep_notes(plan_id)
    mon = _rows(plan_id, "lunch")[dates[0]]
    assert mon["derived"]["prep_note"] == "Cook this Sunday for Monday and Tuesday’s lunches."
    assert mon["reasoning"] == mon["derived"]["prep_note"]


def test_a_prep_day_with_nothing_to_cook_on_still_plans_fridays_lunch(monkeypatch):
    """CATCH (review, 2026-10-19): Thursday's dinner is itself Wednesday's
    leftovers, so the prep day has no cook of its own, and the draft sent
    no Friday lunch: the batch had nothing to build from and Friday was a
    generation_gap. One of the week's own cooks is cooked on Friday's lunch,
    stamped with Thursday's prep."""
    plan_id, dates = _draft(monkeypatch, thursday_reheats_wednesday=True)
    thu, fri = dates[3], dates[4]
    dinner = _rows(plan_id, "dinner")
    assert dinner[thu]["derived"].get("links_to"), "the shape: Thursday's dinner is a reheat"
    assert not dinner[thu]["derived"].get("prep_day_cook")
    lunch = _rows(plan_id, "lunch")[fri]
    assert lunch["slot_state"] == "planned" and lunch["meal"]
    assert lunch["derived"].get("constraint") != "generation_gap"
    assert lunch["derived"].get("prep_date") == thu
    batches = {b["prep_date"]: b for b in weekday_lunches.prepped_batches(plan_id)}
    assert batches[thu]["lunch_dates"] == [fri]
    # Its schedule line and its stored note say the same thing.
    menu = tools.get_week_menu(plan_id)
    row = next(d for d in menu["days"] if d["date"] == fri)["lunch"]
    assert row["schedule_note"] == lunch["derived"]["prep_note"] == "Cook this Thursday for Friday’s lunch."


def test_a_lunch_empty_on_purpose_is_never_a_batch_day():
    """GUARD: planned_empty (nobody home, a day left out) is not a gap."""
    lunches = {"2026-10-23": [{"id": 1, "slot_state": "planned_empty"}],
               "2026-10-24": [{"id": 2, "slot_state": "open"}]}
    assert weekday_lunches._gap_ids(lunches, "2026-10-23") is None
    assert weekday_lunches._gap_ids(lunches, "2026-10-24") == [2]
    assert weekday_lunches._gap_ids(lunches, "2026-10-25") == []
