"""
POST /api/cooker/start refuses every reheat night the Cook card calls one
(overnight/test-gaps-1025).

The Cook card marks three kinds of night `is_leftovers` and offers no
"Start cooking" on them (cooker._apply_leftover_chains; shell.js only
posts a start when `!meal.is_leftovers`): a confirmed chain's night — a
planner's leftovers or a household's own cook-ahead day, both of which
carry the dish's recipe_id — a portion out of the freezer, and a freeform
night named as leftovers. The server's own guard in start_cooking said "a
reheat night has no cook in it to start" but tested only a recipe-less
name STARTING with "Leftover", so a made-ahead day posted from a screen
built before the batch was set took a start: cook_started_at written on a
reheat, which every move and swap door then reads as "cooking"
(weekly_plan.cook_begun) and so refuses to move.

Also pinned: the route's happy path, household isolation, and that a bad
entry id is refused (404 / 422) rather than answered with a 500.
"""
from __future__ import annotations

import datetime
import json

import pytest
from fastapi.testclient import TestClient

from conftest import household_today

from app import households, tools
from app.db import get_conn
from app.main import app
from app.tools import leftovers

# A Wednesday afternoon: yesterday and today are both inside one week.
pytestmark = pytest.mark.today("2026-10-14 16:00")


def _week() -> tuple[int, str, str]:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    return plan, today.isoformat(), (today - datetime.timedelta(days=1)).isoformat()


def _chili():
    tools.add_recipe("Bean Chili", ingredients=[{"item": "black beans", "qty": "1 can", "category": "pantry"}],
                     instructions=["Simmer everything for half an hour."],
                     cook_time_minutes=30, default_servings=4)


def _started(entry_id: int):
    conn = get_conn()
    try:
        return conn.execute("SELECT cook_started_at FROM meal_plan_entries WHERE id = ?",
                            (entry_id,)).fetchone()["cook_started_at"]
    finally:
        conn.close()


def _set_derived(entry_id: int, derived: dict) -> None:
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps(derived), entry_id))
    conn.commit()
    conn.close()


def test_tonights_own_cook_starts(signed_in):
    _chili()
    plan, today, _ = _week()
    tonight = tools.plan_meal(today, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]

    res = signed_in.post("/api/cooker/start", json={"entry_id": tonight})

    assert res.status_code == 200
    body = res.json()
    assert body.get("status") != "refused"
    assert body["started_at"] and body["already_started"] is False
    assert _started(tonight)


def test_a_made_ahead_day_is_refused_and_nothing_is_written(signed_in):
    _chili()
    plan, today, yesterday = _week()
    cook = tools.plan_meal(yesterday, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tonight = tools.plan_meal(today, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    assert isinstance(tools.set_cook_ahead(cook, [tonight]), dict)
    card = next(m for m in tools.get_cooker_view()["meals"] if m["entry_id"] == tonight)
    assert card["is_leftovers"], "precondition: the Cook card reads tonight as a reheat"

    res = signed_in.post("/api/cooker/start", json={"entry_id": tonight})

    assert res.status_code == 200
    assert res.json() == {"status": "refused", "entry_id": tonight,
                          "message": "Nothing to start on a reheat night."}
    assert _started(tonight) is None


def test_the_day_the_batch_is_cooked_still_starts(signed_in):
    # The other half of the rule: the cook day of a chain is not a reheat.
    # A guard that refused anything in a chain would pass every test above.
    _chili()
    plan, today, _ = _week()
    tomorrow = (household_today() + datetime.timedelta(days=1)).isoformat()
    cook = tools.plan_meal(today, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    later = tools.plan_meal(tomorrow, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    assert isinstance(tools.set_cook_ahead(cook, [later]), dict)

    res = signed_in.post("/api/cooker/start", json={"entry_id": cook})

    assert res.json().get("status") != "refused"
    assert _started(cook)


def test_a_portion_out_of_the_freezer_is_refused(signed_in):
    plan, today, _ = _week()
    tonight = tools.plan_meal(today, "Bean chili from the freezer", slot="dinner", weekly_plan_id=plan)["entry_id"]
    _set_derived(tonight, {leftovers.FROM_FREEZER_KEY: {"dish": "Bean Chili"}})

    res = signed_in.post("/api/cooker/start", json={"entry_id": tonight})

    assert res.json().get("status") == "refused"
    assert _started(tonight) is None


def test_a_night_named_as_leftovers_is_refused_wherever_the_word_falls(signed_in):
    plan, today, _ = _week()
    tonight = tools.plan_meal(today, "Chili leftovers", slot="dinner", weekly_plan_id=plan)["entry_id"]

    res = signed_in.post("/api/cooker/start", json={"entry_id": tonight})

    assert res.json().get("status") == "refused"
    assert _started(tonight) is None


def test_another_household_cannot_start_this_households_cook(signed_in):
    _chili()
    plan, today, _ = _week()
    tonight = tools.plan_meal(today, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    households.create_household("Start Cooking B", "start-cooking-b-passphrase")
    with TestClient(app) as b:
        assert b.post("/login", data={"password": "start-cooking-b-passphrase", "next": "/"},
                      follow_redirects=False).status_code == 303
        res = b.post("/api/cooker/start", json={"entry_id": tonight})
    assert res.status_code == 404
    assert "Bean Chili" not in res.text
    assert _started(tonight) is None


@pytest.mark.parametrize("entry_id, code", [(987654, 404), ("tonight", 422), (None, 422)])
def test_a_bad_entry_id_is_refused_not_a_500(signed_in, entry_id, code):
    res = signed_in.post("/api/cooker/start", json={"entry_id": entry_id})
    assert res.status_code == code
