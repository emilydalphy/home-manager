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



def _eggs_with_two_frittatas_approved() -> tuple[dict, int, dict]:
    """An Eggs regular bought today, and an approved week of two frittatas
    whose eggs are on the list. Returns (staple, plan id, the week's line)."""
    from datetime import timedelta

    from conftest import household_today

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
        "SELECT id, quantity, source_weekly_plan_id FROM grocery_items WHERE lower(item) = 'eggs' AND status = 'needed'"
    ).fetchall()
    conn.close()
    assert week["source_weekly_plan_id"] == plan_id
    return s, plan_id, dict(week)


def _week_line(item_id: int) -> tuple:
    conn = get_conn()
    row = conn.execute(
        "SELECT status, quantity, staple_id, source_weekly_plan_id FROM grocery_items WHERE id = ?", (item_id,)
    ).fetchone()
    conn.close()
    return tuple(row)


def test_step_1s_take_off_takes_only_the_regular_never_the_meals_eggs(signed_in):
    """Review (2026-11-02). Amounts that add ("1 dozen" + the frittatas'
    eggs) used to JOIN into one row, and step 1's "Take it off the list?"
    (POST /remove on that row) took all of it — two frittatas, no eggs. The
    regular now stands beside the week's line, so taking it off is "not
    this trip" for the regular and nothing else."""
    s, plan_id, week = _eggs_with_two_frittatas_approved()
    added = signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Eggs"]}).json()["added"]
    [regular] = [a["item_id"] for a in added]
    assert regular != week["id"]
    assert _week_line(week["id"]) == ("needed", week["quantity"], None, plan_id)
    assert _week_line(regular)[:3] == ("needed", "1 dozen", s["id"])

    steps = signed_in.get("/api/grocery-list/before-shop").json()["regulars"]
    assert [r["item_id"] for r in steps["on_list"]] == [regular]
    off = signed_in.post(f"/api/grocery-list/{regular}/remove").json()
    assert off["staple_id"] == s["id"]
    # The frittatas' eggs are still to buy, exactly as approved.
    assert _week_line(week["id"]) == ("needed", week["quantity"], None, plan_id)
    assert _week_line(regular)[0] == "removed"
    # And the toast's Undo (the staple's) puts the regular back beside it.
    signed_in.post(f"/api/staples/{s['id']}/undo")
    assert _week_line(regular)[0] == "needed"
    assert _week_line(week["id"]) == ("needed", week["quantity"], None, plan_id)


def test_undo_of_the_regulars_add_leaves_the_weeks_line(signed_in):
    s, plan_id, week = _eggs_with_two_frittatas_approved()
    added = signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Eggs"]}).json()["added"]
    signed_in.post("/api/grocery-list/before-shop/regulars-undo", json={"item_ids": [a["item_id"] for a in added]})
    assert _week_line(week["id"]) == ("needed", week["quantity"], None, plan_id)
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM grocery_items WHERE lower(item) = 'eggs' AND status = 'needed'").fetchone()[0]
    conn.close()
    assert n == 1


def test_a_starter_regular_with_no_amount_gets_a_line_of_its_own(signed_in):
    """Review, minor: a starter regular has no amount, and joined onto the
    recipe's "Milk · 1/2 cup" it added nothing anyone could see."""
    plan_id = _plan()
    tools.add_grocery_item("Milk", quantity="1/2 cup", category="dairy", source_weekly_plan_id=plan_id)
    added = signed_in.post("/api/grocery-list/before-shop/regulars", json={"items": ["Milk"]}).json()["added"]
    assert [a["item"] for a in added] == ["Milk"]
    staple_id = next(x["id"] for x in tools.list_staples() if x["item"] == "Milk")
    assert _milk_lines() == [("1/2 cup", None, plan_id), ("", staple_id, None)]
