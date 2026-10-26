"""
A staple that is due still comes onto the list when a week's recipe uses the
same thing (defect hunt, 2026-10-24).

Seen on the Shop tab: an approved week whose chili wants "Milk · 1/2 cup",
then the household's weekly 2 L of milk, running low. sync_due_staples
counted ANY line by that name as the household having it covered, so the
staple never came on: the list asked for half a cup of milk, no "Probably
running low", and ticking the half cup then taught the staple it had been
bought — the household's 2 L was never on the list that week. In the other
order (the staple's line first, then the approval) the meals' share joins
the staple's line and both are bought.

Now a week's line does not cover a staple by name. The staple's line joins
it when the amounts add up — the same line the other order makes, which
"We have plenty" trims back to the meals' share — and stands beside it when
they don't. A person's own line still covers it, a line set aside as
"getting it elsewhere" still does, and so does the week's line a staple was
made from (⋯ → "Make it a staple" on a plan row).
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


def _needed(name: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, staple_id, added_by, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = 1 AND lower(item) = lower(?) AND status = 'needed' ORDER BY id",
        (name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _read_list(signed_in=None):
    if signed_in is not None:
        return signed_in.get("/api/grocery-list/by-store").json()
    return tools.get_grocery_list_by_section(status="needed")


def test_a_running_low_staple_stands_beside_a_recipes_half_cup(signed_in):
    _plan_id = _plan()
    tools.add_grocery_item("Milk", quantity="1/2 cup", category="dairy", source_weekly_plan_id=_plan_id)
    s = tools.add_staple("Milk", quantity="2 L", category="dairy", running_low=True)

    _read_list(signed_in)
    rows = _needed("Milk")
    assert [(r["quantity"], r["staple_id"]) for r in rows] == [("1/2 cup", None), ("2 L", s["id"])]
    # It reads as the staple's suggestion ("Probably running low").
    assert rows[1]["added_by"] == st.ADDED_BY_STAPLE
    # And only once, however often the list is read.
    _read_list(signed_in)
    assert len(_needed("Milk")) == 2


def test_amounts_that_add_up_make_one_line_and_plenty_trims_it_back_to_the_meals(signed_in):
    # A real approved week, so the onion line carries its dinner's share on
    # the ledger (meal_plan_grocery_links), which is what plenty trims to.
    tools.add_recipe("Onion soup", ingredients=[{"item": "Onion", "qty": "4", "category": "produce"}])
    plan_id = tools.create_weekly_plan(TODAY.isoformat(), day_count=7)["weekly_plan_id"]
    tools.plan_meal(TODAY.isoformat(), "Onion soup", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    (week,) = _needed("Onion")
    s = tools.add_staple("Onion", quantity="2", category="produce", running_low=True)

    _read_list(signed_in)
    (line,) = _needed("Onion")
    assert line["id"] == week["id"]
    assert float(line["quantity"]) == float(week["quantity"]) + 2
    assert line["staple_id"] == s["id"] and line["added_by"] == st.ADDED_BY_STAPLE

    res = signed_in.post(f"/api/grocery-list/{line['id']}/staple", json={"decision": "plenty"})
    assert res.status_code == 200
    (left,) = _needed("Onion")
    assert left["id"] == line["id"]
    assert left["quantity"] == week["quantity"]  # the week's onions are still bought
    assert left["staple_id"] is None


def test_a_persons_own_line_still_covers_the_staple():
    plan_id = _plan()
    tools.add_grocery_item("Coffee", quantity="1 bag", source_weekly_plan_id=plan_id)
    tools.add_grocery_item("coffee", quantity="2 bags")  # merges: the line is theirs now
    tools.add_staple("Coffee", quantity="1 bag", running_low=True)
    _read_list()
    assert [r["quantity"] for r in _needed("Coffee")] == ["3 bags"]


def test_a_weeks_line_set_aside_as_elsewhere_still_covers_it():
    plan_id = _plan()
    line = tools.add_grocery_item("Eggs", quantity="1 dozen", category="dairy", source_weekly_plan_id=plan_id)
    tools.exclude_grocery_item(line["item_id"])
    tools.add_staple("Eggs", quantity="1 dozen", category="dairy", running_low=True)
    _read_list()
    assert _needed("Eggs") == [] or all(r["id"] == line["item_id"] for r in _needed("Eggs"))
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM grocery_items WHERE lower(item) = 'eggs'").fetchone()[0]
    conn.close()
    assert count == 1


def test_a_staple_made_from_the_weeks_line_is_covered_by_it():
    plan_id = _plan()
    line_id = tools.add_grocery_item("Butter", quantity="1 block", category="dairy",
                                     source_weekly_plan_id=plan_id)["item_id"]
    tools.add_staple("Butter", quantity="1 block", category="dairy", grocery_item_id=line_id)
    _read_list()
    assert [(r["id"], r["quantity"]) for r in _needed("Butter")] == [(line_id, "1 block")]
