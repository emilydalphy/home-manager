"""
Draft: weekday lunches follow my answers — Sunday's prep feeds Monday, and
the lunch count matches What we know (Loop Board, Emily 2026-09-27).

Her setup: weekday lunches 4 made on a prep day (Sun + Tue), 1 leftover
from dinner, 0 cooked that day; What we know > Different dishes a week >
Lunches = 2. Week Sun Sep 27 – Fri Oct 2. The draft came back with
Sunday's lunch (Japanese curry) feeding nothing, Monday a fresh 25-minute
corn pancake, Tue–Thu chicken salad prepped Tuesday, and Friday beef bowls
from Thursday's dinner — four lunch dishes against her two.

Root causes (origin/main 8e046a6): weekday_lunches.apply_to_plan built each
prepped batch from the weekday lunches alone and cooked it on the first
of them, so a prep day inside the week was never the cook; and the count
pass skipped the lunch slot entirely once weekday lunches were answered.

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
from app.tools import meal_variety, weekday_lunches


def _slot(date, slot, name, minutes=30, **extra):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": [{"item": f"{name} bits", "qty": "2 lb", "category": "pantry"}],
         "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
         "prep_time_minutes": 10, "cook_time_minutes": minutes - 10}
    d.update(extra)
    return d


def _next(name: str) -> datetime.date:
    want = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday").index(name)
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return monday + datetime.timedelta(days=7 + want)


def _dates(start: datetime.date, n: int) -> list[str]:
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(n)]


@pytest.fixture
def two_adults():
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


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


def _lunch_dishes(plan_id: int) -> set[str]:
    """Lunch dishes as the count reads them: a reheat is the dish it
    reheats, and a lunch that is a DINNER's leftovers is not a lunch dish."""
    chains = tools.plan_leftover_chains(plan_id)
    out = set()
    for row in _rows(plan_id, "lunch").values():
        if row["slot_state"] != "planned" or not row["meal"]:
            continue
        reheat = chains["leftovers"].get(row["id"])
        if reheat and reheat["source"]["slot"] == "dinner":
            continue
        out.add((reheat["source"]["meal"] if reheat else row["meal"]).lower())
    return out


def _emilys_week(stub_model):
    tools.set_household_meal_preferences(lunches_per_week=2, mark_complete=False)
    sun = _next("sunday")
    dates = _dates(sun, 6)  # Sun – Fri
    s = sun.isoformat()
    tools.save_week_intake(s, weekday_lunches={
        "prep_days": ["sunday", "tuesday"],
        "days": [
            {"date": dates[1], "kind": "prepped"},
            {"date": dates[2], "kind": "prepped"},
            {"date": dates[3], "kind": "prepped"},
            {"date": dates[4], "kind": "prepped"},
            {"date": dates[5], "kind": "leftovers"},
        ],
    }, day_count=6)
    lunches = ["Japanese curry", "Corn pancakes", "Chicken salad", "Chicken salad", "Chicken salad", "Beef bowls"]
    dinners = ["Roast chicken", "Salmon", "Tofu stir-fry", "Pasta", "Beef bowls", "Pizza"]
    days = []
    for i, d in enumerate(dates):
        days.append(_slot(d, "breakfast", "Oats"))
        days.append(_slot(d, "lunch", lunches[i], minutes=25 if i == 1 else 30))
        days.append(_slot(d, "dinner", dinners[i]))
    stub_model(days)
    plan_id = agent.generate_weekly_plan(s, day_count=6, confirm_takeover=True)["weekly_plan_id"]
    return plan_id, dates


def test_sundays_prep_is_cooked_on_sunday_and_feeds_monday(two_adults, stub_model):
    """CATCH: on main Monday is a fresh corn pancake and Sunday's curry
    feeds nothing."""
    plan_id, dates = _emilys_week(stub_model)
    lunch = _rows(plan_id, "lunch")
    chains = tools.plan_leftover_chains(plan_id)

    assert lunch[dates[1]]["meal"] == "Japanese curry"
    assert chains["leftovers"][lunch[dates[1]]["id"]]["source"]["entry_id"] == lunch[dates[0]]["id"]
    assert lunch[dates[1]]["derived"].get("cook_ahead") is True
    assert f"{dates[1]}:lunch" in lunch[dates[0]]["derived"]["make_double_for"]
    assert lunch[dates[0]]["derived"]["prep_date"] == dates[0]
    assert lunch[dates[0]]["reasoning"] == "Cook this Sunday for Monday’s lunch."
    # Tuesday's prep: cooked Tuesday, Wednesday and Thursday reheat it.
    for d in dates[3:5]:
        assert lunch[d]["meal"] == "Chicken salad"
        assert chains["leftovers"][lunch[d]["id"]]["source"]["entry_id"] == lunch[dates[2]]["id"]


def test_the_weekend_lunch_on_a_prep_day_is_the_batch_not_an_extra_dish(two_adults, stub_model):
    """CATCH: main counts four lunch dishes against Lunches = 2 — Sunday's
    curry, Monday's pancake, the chicken salad and Friday's beef bowls."""
    plan_id, dates = _emilys_week(stub_model)
    assert _lunch_dishes(plan_id) == {"japanese curry", "chicken salad"}
    # Friday is Thursday's dinner, reheated — not a lunch dish, not touched.
    lunch = _rows(plan_id, "lunch")
    dinner = _rows(plan_id, "dinner")
    chains = tools.plan_leftover_chains(plan_id)
    assert chains["leftovers"][lunch[dates[5]]["id"]]["source"]["entry_id"] == dinner[dates[4]]["id"]


def test_the_schedule_line_survives_in_data_whatever_the_row_shows(two_adults, stub_model):
    """CATCH: main has the sentence only in `reasoning`, which the row hides
    under an `asked` fact. It now rides on derived_from.prep_note and the
    menu hands it out as schedule_note."""
    plan_id, dates = _emilys_week(stub_model)
    menu = tools.get_week_menu(plan_id)
    sunday = next(d for d in menu["days"] if d["date"] == dates[0])
    assert sunday["lunch"]["schedule_note"] == "Cook this Sunday for Monday’s lunch."
    tuesday = next(d for d in menu["days"] if d["date"] == dates[2])
    assert tuesday["lunch"]["schedule_note"] == "Makes Wednesday and Thursday’s lunches too."


def test_the_prep_session_names_only_the_lunches_the_batch_is_for(two_adults, stub_model):
    plan_id, dates = _emilys_week(stub_model)
    batches = {b["prep_date"]: b for b in weekday_lunches.prepped_batches(plan_id)}
    assert batches[dates[0]]["lunch_dates"] == [dates[1]]
    assert batches[dates[2]]["lunch_dates"] == dates[2:5]


def test_a_monday_start_week_with_a_sunday_prep_before_it_is_as_before(two_adults, stub_model):
    """GUARD: the prep day outside the plan still cooks on the first lunch."""
    mon = _next("monday")
    dates = _dates(mon, 7)
    tools.save_week_intake(mon.isoformat(), weekday_lunches={
        "prep_days": ["sunday"],
        "days": [{"date": dates[0], "kind": "prepped"}, {"date": dates[1], "kind": "prepped"}],
    })
    days = []
    for i, d in enumerate(dates):
        days.append(_slot(d, "breakfast", "Oats"))
        days.append(_slot(d, "lunch", ["Chili", "Soup", "A", "B", "C", "D", "E"][i]))
        days.append(_slot(d, "dinner", f"Dinner {i}"))
    stub_model(days)
    plan_id = agent.generate_weekly_plan(mon.isoformat())["weekly_plan_id"]
    lunch = _rows(plan_id, "lunch")
    assert lunch[dates[0]]["meal"] == "Chili" and lunch[dates[1]]["meal"] == "Chili"
    assert lunch[dates[0]]["reasoning"] == "Cook this Sunday for Monday and Tuesday’s lunches."
    assert not lunch[dates[0]]["derived"].get("prep_day_cook")


def test_weekend_lunches_fold_to_the_count_and_cooked_that_day_lunches_stay(two_adults, stub_model):
    """CATCH: main skipped the lunch count whole once the weekday lunches
    were answered, so the weekend lunches were extra dishes. The answered
    lunches are the household's and stand as drafted; the weekend folds
    into what's kept."""
    tools.set_household_meal_preferences(lunches_per_week=2, mark_complete=False)
    mon = _next("monday")
    dates = _dates(mon, 7)
    tools.save_week_intake(mon.isoformat(), weekday_lunches={
        "prep_days": ["sunday"],
        "days": [{"date": dates[i], "kind": "prepped"} for i in range(3)]
        + [{"date": dates[3], "kind": "cooked"}, {"date": dates[4], "kind": "cooked"}],
    })
    days = []
    for i, d in enumerate(dates):
        days.append(_slot(d, "breakfast", "Oats"))
        days.append(_slot(d, "lunch", ["Chili", "Chili", "Chili", "Wrap", "Salad", "Pita", "Toastie"][i],
                          minutes=15 if i in (3, 4) else 30))
        days.append(_slot(d, "dinner", f"Dinner {i}"))
    stub_model(days)
    plan_id = agent.generate_weekly_plan(mon.isoformat())["weekly_plan_id"]
    lunch = _rows(plan_id, "lunch")
    chains = tools.plan_leftover_chains(plan_id)

    assert lunch[dates[3]]["meal"] == "Wrap" and lunch[dates[3]]["id"] not in chains["leftovers"]
    assert lunch[dates[4]]["meal"] == "Salad" and lunch[dates[4]]["id"] not in chains["leftovers"]
    # Chili, Wrap and Salad are theirs, so three is as low as it goes —
    # and Pita and Toastie are not new dishes on top.
    assert _lunch_dishes(plan_id) == {"chili", "wrap", "salad"}


def test_a_prepped_cook_is_never_the_one_a_count_folds_away():
    """The fold keeps a prepped batch's cook, whatever the count
    (meal_variety.enforce_distinct_count reads derived_from.prep_date)."""
    import inspect
    src = inspect.getsource(meal_variety.enforce_distinct_count)
    assert '.get("prep_date")' in src
