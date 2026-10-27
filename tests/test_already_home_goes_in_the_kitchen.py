"""
Inventory A3 (Emily, 2026-10-25/26): the answers a household already gives
Pomona — "already have it" on a grocery line, a tapped chip in the freezer
step — count as what's home, so it never has to say so twice. Policy
2026-09-01 ("never an inventory write") is lifted for these two taps.

- "Already have it" (pre-shop or a list row's ⋯, both POST .../pre-shop
  {decision: 'drop'}) confirms a kitchen row that is there, or adds one in
  its likely place; its Undo (.../pre-shop-undo) removes only a row the tap
  added, and only while nobody has touched it since.
- A freezer-step chip records the item in the Freezer; un-tapping, or
  "Put back on the list" on the line it set aside, reverses that.
- A staple's "We have plenty" / "Not this trip" never touches the kitchen
  (decision 6, 2026-10-26).

Driven over HTTP, the routes the shell already calls.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from conftest import household_pin, household_today, on_the_weeks_monday

ON_MONDAY = on_the_weeks_monday()


def _monday() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def _needed() -> dict[str, dict]:
    return {i["item"]: i for i in tools.list_grocery_list()}


def _kitchen(item: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, location, source, rev FROM inventory_items WHERE LOWER(item) = LOWER(?) ORDER BY id",
        (item,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture
def week() -> int:
    tools.add_recipe("Greek salad", ingredients=[
        {"item": "Cucumber", "qty": "1", "category": "produce"},
        {"item": "Feta", "qty": "200 g", "category": "dairy"},
    ])
    tools.add_recipe("Lemon chicken", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
    ])
    plan_id = tools.create_weekly_plan(_monday())["weekly_plan_id"]
    days = tools._week_dates(_monday())
    tools.plan_meal(days[3], "Greek salad", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(days[5], "Lemon chicken", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


# ---------- "already have it" on a grocery line ----------

@ON_MONDAY
def test_already_have_adds_the_line_to_the_kitchen_and_undo_takes_it_back(signed_in, week):
    cucumber = _needed()["Cucumber"]
    assert _kitchen("Cucumber") == [], "premise: nothing tracked"

    res = signed_in.post(f"/api/grocery-list/{cucumber['id']}/pre-shop", json={"decision": "drop", "author": "user"})
    assert res.status_code == 200
    (row,) = _kitchen("Cucumber")
    assert row["quantity"] == "1"
    assert row["location"] == "fridge", "its likely place"

    # A second tap is not a second answer.
    signed_in.post(f"/api/grocery-list/{cucumber['id']}/pre-shop", json={"decision": "drop", "author": "user"})
    assert len(_kitchen("Cucumber")) == 1

    assert signed_in.post(f"/api/grocery-list/{cucumber['id']}/pre-shop-undo").status_code == 200
    assert _kitchen("Cucumber") == []
    assert _needed()["Cucumber"]["quantity"] == "1"


@ON_MONDAY
def test_already_have_confirms_a_row_that_is_there_and_undo_leaves_it(signed_in, week):
    tools.update_inventory("Feta", "add", "300 g", category="dairy", location="fridge")
    (before,) = _kitchen("Feta")
    feta = _needed().get("Feta")
    if feta is None:
        pytest.skip("premise: approval left feta on the list")
    signed_in.post(f"/api/grocery-list/{feta['id']}/pre-shop", json={"decision": "drop", "author": "user"})
    assert _kitchen("Feta") == [before], "confirmed, not added to: 300 g stays 300 g"
    signed_in.post(f"/api/grocery-list/{feta['id']}/pre-shop-undo")
    assert _kitchen("Feta") == [before], "the household's own row is never taken away"


@ON_MONDAY
def test_undo_leaves_a_row_the_household_has_changed_since(signed_in, week):
    cucumber = _needed()["Cucumber"]
    signed_in.post(f"/api/grocery-list/{cucumber['id']}/pre-shop", json={"decision": "drop", "author": "user"})
    (row,) = _kitchen("Cucumber")
    tools.step_inventory_quantity(row["id"], 1)
    signed_in.post(f"/api/grocery-list/{cucumber['id']}/pre-shop-undo")
    assert [r["quantity"] for r in _kitchen("Cucumber")] == ["2"], "their later word is the truer record"


@ON_MONDAY
def test_two_lines_behind_one_row_add_once_and_undo_in_either_order(signed_in, week):
    a = tools.add_grocery_item("Lemons", "2", category="produce")["item_id"]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, status) VALUES (1, 'Lemons', '1', 'produce', 'needed')"
    )
    b = cur.lastrowid
    conn.commit()
    conn.close()
    for one in (a, b):
        signed_in.post(f"/api/grocery-list/{one}/pre-shop", json={"decision": "drop", "author": "user"})
    assert len(_kitchen("Lemons")) == 1
    # The line that created the row is undone first: the row stays for the
    # other answer, then goes with it.
    signed_in.post(f"/api/grocery-list/{a}/pre-shop-undo")
    assert len(_kitchen("Lemons")) == 1
    signed_in.post(f"/api/grocery-list/{b}/pre-shop-undo")
    assert _kitchen("Lemons") == []


# ---------- the freezer step ----------

@ON_MONDAY
def test_freezer_tap_files_it_in_the_freezer_and_untap_takes_it_out(signed_in, week):
    url = f"/api/week/{_monday()}/defrost-confirm"
    res = signed_in.post(url, json={"items": ["Chicken thighs"], "weekly_plan_id": week})
    assert res.status_code == 200
    (row,) = _kitchen("Chicken thighs")
    assert row["location"] == "freezer"
    assert row["quantity"] == "2 lbs"
    # Answering again with the same chips writes nothing more.
    signed_in.post(url, json={"items": ["Chicken thighs"], "weekly_plan_id": week})
    assert len(_kitchen("Chicken thighs")) == 1

    signed_in.post(url, json={"items": [], "weekly_plan_id": week})
    assert _kitchen("Chicken thighs") == []
    assert "Chicken thighs" in _needed(), "back on the list"


@ON_MONDAY
def test_freezer_tap_confirms_the_freezer_row_that_is_there(signed_in, week):
    tools.update_inventory("Chicken thighs", "add", "3 lb", category="meat/seafood", location="freezer")
    (before,) = _kitchen("Chicken thighs")
    url = f"/api/week/{_monday()}/defrost-confirm"
    signed_in.post(url, json={"items": ["Chicken thighs"], "weekly_plan_id": week})
    assert _kitchen("Chicken thighs") == [before]
    signed_in.post(url, json={"items": [], "weekly_plan_id": week})
    assert _kitchen("Chicken thighs") == [before]


@ON_MONDAY
def test_put_back_on_the_list_takes_the_freezer_record_with_it(signed_in, week):
    signed_in.post(f"/api/week/{_monday()}/defrost-confirm", json={"items": ["Chicken thighs"], "weekly_plan_id": week})
    assert len(_kitchen("Chicken thighs")) == 1
    summary = signed_in.get("/api/grocery-list/already-have-summary").json()
    line = next(i for i in summary["already_have"] if i["item"] == "Chicken thighs")
    assert signed_in.post(f"/api/grocery-list/{line['id']}/pre-shop-undo").status_code == 200
    assert _kitchen("Chicken thighs") == []


@ON_MONDAY
def test_next_weeks_list_does_not_spend_what_this_week_said_is_home(signed_in, week):
    """The row the answer wrote is this week's dinner, not spare stock."""
    signed_in.post(f"/api/week/{_monday()}/defrost-confirm", json={"items": ["Chicken thighs"], "weekly_plan_id": week})
    nxt = (datetime.date.fromisoformat(_monday()) + datetime.timedelta(days=7)).isoformat()
    plan2 = tools.create_weekly_plan(nxt)["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(nxt)[1], "Lemon chicken", slot="dinner", weekly_plan_id=plan2)
    result = tools.approve_weekly_plan(plan2, approved_by="Emily")
    assert "Chicken thighs" not in (result.get("already_have_skipped") or [])
    assert "Chicken thighs" in _needed(), "next week's chicken is bought"


def _last_weeks_chicken_answered(signed_in, frozen_today, how: str) -> int:
    """Last week: Lemon chicken on the Wednesday, answered "already home"
    (`how`: the freezer chip or the list's Have it) on its Monday; then
    the clock moves to that Friday with the dinner never ticked cooked —
    what most households do. Returns next week's plan id, approved."""
    mon = datetime.date.fromisoformat(_monday()) - datetime.timedelta(days=7)
    frozen_today(household_pin(9, on=mon))
    tools.add_recipe("Lemon chicken", ingredients=[{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"}])
    p1 = tools.create_weekly_plan(mon.isoformat())["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(mon.isoformat())[2], "Lemon chicken", slot="dinner", weekly_plan_id=p1)
    tools.approve_weekly_plan(p1, approved_by="Emily")
    if how == "freezer":
        signed_in.post(f"/api/week/{mon.isoformat()}/defrost-confirm", json={"items": ["Chicken thighs"], "weekly_plan_id": p1})
    else:
        line = _needed()["Chicken thighs"]
        signed_in.post(f"/api/grocery-list/{line['id']}/pre-shop", json={"decision": "drop", "author": "user"})
    assert _kitchen("Chicken thighs"), "premise: the answer put it in the kitchen"
    frozen_today(household_pin(9, on=mon + datetime.timedelta(days=4)))
    nxt = (mon + datetime.timedelta(days=7)).isoformat()
    p2 = tools.create_weekly_plan(nxt)["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(nxt)[1], "Lemon chicken", slot="dinner", weekly_plan_id=p2)
    result = tools.approve_weekly_plan(p2, approved_by="Emily")
    assert "Chicken thighs" not in (result.get("already_have_skipped") or [])
    return p2


@pytest.mark.parametrize("how", ["freezer", "have_it"])
def test_a_past_night_nobody_ticked_still_ate_what_was_said_to_be_home(signed_in, frozen_today, how):
    """Review, 2026-10-27: the night passed unticked and its row read as
    spare, so next week bought no chicken. Claimed as eaten, the way a
    bought line's past meal is."""
    _last_weeks_chicken_answered(signed_in, frozen_today, how)
    assert "Chicken thighs" in _needed(), "next week's chicken is bought"


@ON_MONDAY
def test_another_plan_does_not_thaw_this_plans_freezer_chip(signed_in, week):
    """Review, 2026-10-27: next week bought its chicken AND got a fridge
    move off this week's chip row."""
    signed_in.post(f"/api/week/{_monday()}/defrost-confirm", json={"items": ["Chicken thighs"], "weekly_plan_id": week})
    nxt = (datetime.date.fromisoformat(_monday()) + datetime.timedelta(days=7)).isoformat()
    plan2 = tools.create_weekly_plan(nxt)["weekly_plan_id"]
    tools.plan_meal(tools._week_dates(nxt)[1], "Lemon chicken", slot="dinner", weekly_plan_id=plan2)
    tools.approve_weekly_plan(plan2, approved_by="Emily")
    tools.sync_defrost_tasks(plan2)
    conn = get_conn()
    booked = conn.execute(
        "SELECT COUNT(*) FROM prep_tasks WHERE weekly_plan_id = ? AND task_type = 'defrost'", (plan2,),
    ).fetchone()[0]
    conn.close()
    assert booked == 0


# ---------- staples stay out ----------

@ON_MONDAY
def test_a_staples_answers_never_touch_the_kitchen(signed_in):
    for name, decision in (("Coffee beans", "plenty"), ("Dish soap", "skip")):
        tools.add_staple(name, category="pantry", running_low=True)
        tools.get_grocery_list_by_section(status="needed")  # reading the list puts it there
        line = _needed()[name]
        res = signed_in.post(f"/api/grocery-list/{line['id']}/staple", json={"decision": decision})
        assert res.status_code == 200
        assert _kitchen(name) == []
    # And "Have it" on a staple's line is the staple's "we have plenty".
    tools.add_staple("Oat milk", category="dairy", running_low=True)
    tools.get_grocery_list_by_section(status="needed")
    line = _needed()["Oat milk"]
    signed_in.post(f"/api/grocery-list/{line['id']}/pre-shop", json={"decision": "drop", "author": "user"})
    assert _kitchen("Oat milk") == []
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) FROM already_home_receipts").fetchone()[0] == 0
    conn.close()
