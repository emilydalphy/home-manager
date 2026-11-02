"""
A regular ticked on "Before you shop" goes on the list beside a week's line
for the same thing (defect hunt, 2026-11-02).

Seen walking a shop: the household buys 2 L of milk every week, and this
week's chili wants "Milk · 1/2 cup". Step 1 of "Before you shop" listed Milk
under "Already on the list · added today" — that was the chili's half cup —
so there was nothing to tick, and posting Milk anyway added nothing: a name
already on the list counted, whoever's line it was. The household's 2 L
never reached the list unless the staple happened to be due that day.

The rule is the one a due staple already follows (staples._what_covers,
2026-10-24): a week's line is what its recipes use, not the household's
regular. A person's own line, a line set aside for elsewhere, the staple's
own line, and the plan row a staple was made from still answer it.
"""
from __future__ import annotations

from datetime import date

import pytest

from app import tools
from app.db import get_conn
from app.tools import staples as st

TODAY = date(2026, 9, 16)

pytestmark = pytest.mark.today(TODAY)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(st, "_TODAY_OVERRIDE", TODAY)
    yield


def _plan() -> int:
    conn = get_conn()
    plan_id = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (1, ?, 'approved', ?, 7)",
        (TODAY.isoformat(), TODAY.isoformat()),
    ).lastrowid
    conn.commit()
    conn.close()
    return plan_id


def _milk_lines() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT quantity, staple_id, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = 1 AND lower(item) = 'milk' AND status = 'needed' ORDER BY id"
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def _weekly_milk_bought_today() -> dict:
    """A Milk regular bought today, so it is not due and only a tick on
    step 1 would put it on this trip's list."""
    s = tools.add_staple("Milk", quantity="2 L", category="dairy")
    line = tools.add_grocery_item("Milk", quantity="2 L", category="dairy")
    tools.mark_grocery_item(line["item_id"], "purchased")
    return s


def test_a_recipes_half_cup_does_not_hide_the_milk_regular(signed_in):
    plan_id = _plan()
    s = _weekly_milk_bought_today()
    tools.add_grocery_item("Milk", quantity="1/2 cup", category="dairy", source_weekly_plan_id=plan_id)

    steps = signed_in.get("/api/grocery-list/before-shop").json()["regulars"]
    # Was: no choice, and on_list "Milk · 1/2 cup · added today".
    assert [c["name"] for c in steps["choices"]] == ["Milk"]
    assert steps["on_list"] == []

    added = signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Milk"]}).json()["added"]
    # Was: [] — the half cup counted as the household having milk on the list.
    assert [a["item"] for a in added] == ["Milk"]
    assert _milk_lines() == [("1/2 cup", None, plan_id), ("2 L", s["id"], None)]

    # Once it is on, step 1 shows the regular's own line, not the recipe's.
    steps = signed_in.get("/api/grocery-list/before-shop").json()["regulars"]
    assert steps["choices"] == []
    assert [(r["name"], r["quantity"]) for r in steps["on_list"]] == [("Milk", "2 L")]
    # And a second tick adds nothing.
    again = signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Milk"]}).json()
    assert again["added"] == []
    assert len(_milk_lines()) == 2


def test_a_persons_own_line_still_answers_the_regular(signed_in):
    _weekly_milk_bought_today()
    tools.add_grocery_item("Milk", quantity="1 L", category="dairy")

    steps = signed_in.get("/api/grocery-list/before-shop").json()["regulars"]
    assert steps["choices"] == []
    assert [r["quantity"] for r in steps["on_list"]] == ["1 L"]
    assert signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Milk"]}).json()["added"] == []
    assert _milk_lines() == [("1 L", None, None)]


def test_the_week_line_a_staple_was_made_from_still_answers_it(signed_in):
    """⋯ → "Make it a staple" on the week's own row: that row is the
    staple's line this trip, so step 1 shows it and adds no second one."""
    plan_id = _plan()
    week_line = tools.add_grocery_item("Milk", quantity="2 L", category="dairy", source_weekly_plan_id=plan_id)
    tools.add_staple("Milk", category="dairy", grocery_item_id=week_line["item_id"])

    steps = signed_in.get("/api/grocery-list/before-shop").json()["regulars"]
    assert steps["choices"] == []
    assert [r["item_id"] for r in steps["on_list"]] == [week_line["item_id"]]
    assert signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Milk"]}).json()["added"] == []
    assert len(_milk_lines()) == 1


def test_undo_of_a_regular_that_joined_the_weeks_line_keeps_the_weeks_part(signed_in):
    """When the amounts add up, the regular joins the week's line (as a due
    staple's does) — so the toast's Undo must cut it back to what the meals
    need, not delete the row: that took two frittatas' eggs off the list."""
    from conftest import household_today
    from datetime import timedelta

    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)
    s = tools.add_staple("Eggs", quantity="1 dozen", category="dairy")
    bought = tools.add_grocery_item("Eggs", quantity="1 dozen", category="dairy")
    tools.mark_grocery_item(bought["item_id"], "purchased")
    tools.add_recipe("Frittata", ingredients=[{"item": "eggs", "qty": "8"}], default_servings=3)
    today = household_today()
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    for d in (1, 3):
        tools.plan_meal((monday + timedelta(days=d)).isoformat(), "Frittata", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Alex")
    conn = get_conn()
    [week] = conn.execute(
        "SELECT id, quantity FROM grocery_items WHERE lower(item) = 'eggs' AND status = 'needed'"
    ).fetchall()
    conn.close()

    added = signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Eggs"]}).json()["added"]
    assert [a["item_id"] for a in added] == [week["id"]]  # joined, one line
    conn = get_conn()
    joined = conn.execute("SELECT quantity, staple_id FROM grocery_items WHERE id = ?", (week["id"],)).fetchone()
    conn.close()
    assert joined["staple_id"] == s["id"] and joined["quantity"] != week["quantity"]

    signed_in.post("/api/grocery-list/before-shop/regulars-undo", json={"item_ids": [week["id"]]})
    conn = get_conn()
    after = conn.execute(
        "SELECT quantity, status, staple_id, source_weekly_plan_id FROM grocery_items WHERE id = ?", (week["id"],)
    ).fetchone()
    conn.close()
    # Was: the row deleted — no eggs on the list for two frittatas.
    assert after is not None
    assert (after["status"], after["quantity"], after["staple_id"], after["source_weekly_plan_id"]) == (
        "needed", week["quantity"], None, plan_id)
