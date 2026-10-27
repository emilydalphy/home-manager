"""
Route gaps, 2026-10-27 (standing work, fourth route pass after the
2026-10-22, 10-24 and 10-25 files).

How these were picked. Every route on main is already reached by path by
some test, so this pass asked two narrower questions.

  1. Which routes did the isolation attack (test_isolation_attack.py) aim
     at nothing? It throws every id household A owns at every route, but A
     owned no prep step, no recipe-change request and no week intake — so
     POST /api/cooker/check-prep (prep_task_id), POST /api/meal-recipe/undo
     (request_id) and the generate routes' intake_id were only ever sent
     ids from OTHER tables. check-prep was worse: its 422 for a bad status
     named its words with commas, which the harness's word-picker missed,
     so it never got past validation at all. The harness now seeds all
     three and reads commas; the two body-id routes are also pinned here
     by hand, where a failure names the route.

  2. Which writes answer 200 to a blank string and change something?
     Driven over every POST body with "   " in each text field. Two were
     real:
       * POST /api/memory/store-items/add with a blank store FORGOT where
         the item is bought — the sync treats '' as "forget" — and said 200.
       * POST /api/needs-you/dinner with a blank meal planned a dinner with
         no name; every later pick for that night was then refused with
         "That dinner's already decided — ."
"""
from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from conftest import household_date, household_today

import test_tweak_something_else as tweak

from app import agent, households, tools
from app.db import get_conn
from app.main import app

B_PASSPHRASE = "route-gaps-1027-b-passphrase"


def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


@pytest.fixture
def household_b():
    """A second household, signed in. Returns its client."""
    households.create_household("Route Gaps B", B_PASSPHRASE)
    c = TestClient(app)
    res = c.post("/login", data={"password": B_PASSPHRASE, "next": "/"}, follow_redirects=False)
    assert res.status_code == 303
    return c


def _row(table: str, row_id: int) -> dict:
    conn = get_conn()
    try:
        return dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())
    finally:
        conn.close()


# ---------- 1. the ids the isolation attack never aimed ----------

def test_household_b_cannot_tick_household_as_prep_step(household_b):
    plan = tools.create_weekly_plan(_week_start())
    plan_id = plan.get("weekly_plan_id") or plan.get("id")
    tools.add_prep_cut(plan_id, household_date(0), "Chop the onions")
    conn = get_conn()
    task_id = conn.execute("SELECT id FROM prep_tasks ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()
    before = _row("prep_tasks", task_id)

    for status in ("done", "skipped"):
        res = household_b.post("/api/cooker/check-prep", json={"prep_task_id": task_id, "status": status})
        assert res.status_code == 404, res.text
        assert "Chop the onions" not in res.text
    assert _row("prep_tasks", task_id) == before


def test_household_b_cannot_undo_household_as_recipe_change(signed_in, household_b, monkeypatch):
    """A real rewrite of A's, with its snapshot stored, so an unscoped undo
    would have something to put back — and A's own undo still works."""
    plan = tweak._plan("approved")
    tweak._recipe("Chicken Tacos", [("Chicken thighs", "1 lb"), ("Cilantro", "1 bunch")])
    entry = tweak._meal(plan, tweak.TUE, "Chicken Tacos")
    monkeypatch.setattr(agent, "generate_recipe_details_llm", tweak._writer(tweak._NO_CILANTRO))
    body = tweak._post(signed_in, entry, "No cilantro").json()
    assert body["status"] == "rewritten", body
    rewritten_to = tweak._entry_recipe_id(entry)
    before = _row("recipe_change_requests", body["request_id"])

    res = household_b.post("/api/meal-recipe/undo", json={"request_id": body["request_id"]})
    assert res.status_code == 200
    assert res.json()["status"] == "refused"
    assert "Tacos" not in res.text and "cilantro" not in res.text.lower()
    assert _row("recipe_change_requests", body["request_id"]) == before
    assert tweak._entry_recipe_id(entry) == rewritten_to

    undone = signed_in.post("/api/meal-recipe/undo", json={"request_id": body["request_id"]}).json()
    assert undone["status"] == "undone", undone


def test_a_prep_step_ticked_by_its_own_household_still_lands(signed_in):
    """The other half of the 404 above: the same call from A works."""
    plan = tools.create_weekly_plan(_week_start())
    plan_id = plan.get("weekly_plan_id") or plan.get("id")
    tools.add_prep_cut(plan_id, household_date(0), "Chop the onions")
    conn = get_conn()
    task_id = conn.execute("SELECT id FROM prep_tasks ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()

    res = signed_in.post("/api/cooker/check-prep", json={"prep_task_id": task_id, "status": "done"})
    assert res.status_code == 200, res.text
    assert _row("prep_tasks", task_id)["status"] == "done"


# ---------- 2a. a blank store must not forget where things are bought ----------

@pytest.mark.parametrize("store,item", [("   ", "paper towels"), ("", "paper towels"), ("Costco", "   ")])
def test_store_items_add_refuses_a_blank_and_forgets_nothing(signed_in, store, item):
    assert signed_in.post("/api/memory/store-items/add",
                          json={"store": "Costco", "item": "paper towels"}).status_code == 200
    assert tools.get_item_store_preferences().get("paper towels") == "Costco"

    res = signed_in.post("/api/memory/store-items/add", json={"store": store, "item": item})
    assert res.status_code == 400
    assert tools.get_item_store_preferences().get("paper towels") == "Costco"
    memory = signed_in.get("/api/memory").json()
    assert memory["store_typical_items"] == {"Costco": ["paper towels"]}


def test_store_items_remove_refuses_a_blank_and_files_no_empty_store(signed_in):
    signed_in.post("/api/memory/store-items/add", json={"store": "Costco", "item": "paper towels"})
    res = signed_in.post("/api/memory/store-items/remove", json={"store": "  ", "item": "paper towels"})
    assert res.status_code == 400
    assert signed_in.get("/api/memory").json()["store_typical_items"] == {"Costco": ["paper towels"]}


def test_the_chat_tool_refuses_a_blank_store_too(signed_in):
    """add_store_typical_items is also a chat tool; the guard lives in it."""
    tools.add_store_typical_items("Costco", ["paper towels"])
    with pytest.raises(ValueError):
        tools.add_store_typical_items("", ["paper towels"])
    assert tools.get_item_store_preferences().get("paper towels") == "Costco"


# ---------- 2b. a blank dinner pick plans nothing ----------

def test_needs_you_dinner_refuses_a_blank_meal_and_writes_nothing(signed_in):
    day = household_date(2)
    res = signed_in.post("/api/needs-you/dinner", json={"date": day, "meal": "   ", "add_ingredients": True})
    assert res.status_code == 400
    assert res.json()["detail"] == "Pick a dinner first."
    conn = get_conn()
    rows = conn.execute("SELECT * FROM meal_plan_entries WHERE date = ?", (day,)).fetchall()
    conn.close()
    assert rows == []

    # And the night is still free for a real pick.
    res = signed_in.post("/api/needs-you/dinner", json={"date": day, "meal": "Tacos"})
    assert res.status_code == 200
    assert res.json().get("status") != "refused", res.text
