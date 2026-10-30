"""
"We had it" on Today's yesterday card is the eaten tick, so it says what came
out of the kitchen and offers the same Undo (defect hunt 2026-10-28).

Inventory card A2 (2026-10-26) gave every "eaten" tick a receipt — "Took the
chicken, spinach, rice and lemons out of the kitchen." with Undo — but
tools.answer_yesterday threw check_off_meal's `kitchen_took` away, so this
one door still emptied the shelves in silence, with nothing to take it back.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from conftest import household_today

from app import tools
from app.db import get_conn

TODAY = household_today()
YESTERDAY = (TODAY - datetime.timedelta(days=1)).isoformat()
JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")


def _yesterdays_dinner(stock: bool = True) -> int:
    plan = tools.create_weekly_plan(YESTERDAY, day_count=2)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    tools.add_recipe("Lemon chicken", ingredients=[
        {"item": "Chicken thighs", "qty": "2 lbs"},
        {"item": "Lemons", "qty": "2"},
    ], default_servings=4)
    if stock:
        tools.update_inventory("Chicken thighs", "add", quantity="3 lbs", category="meat/seafood", location="fridge")
        tools.update_inventory("Lemons", "add", quantity="2", category="produce", location="fridge")
    return tools.plan_meal(YESTERDAY, "Lemon chicken", slot="dinner", weekly_plan_id=plan)["entry_id"]


def _stock():
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity FROM inventory_items WHERE household_id = ? ORDER BY id",
        (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [(r["item"], r["quantity"]) for r in rows]


def test_we_had_it_reports_what_came_out_of_the_kitchen(signed_in):
    entry = _yesterdays_dinner()
    r = signed_in.post("/api/today/yesterday/answer", json={"entry_id": entry, "answer": "had"})
    assert r.status_code == 200
    assert r.json()["kitchen_took"]["line"] == "Took the chicken and lemons out of the kitchen."


def test_its_undo_puts_the_food_back_and_asks_again(signed_in):
    entry = _yesterdays_dinner()
    before = _stock()
    signed_in.post("/api/today/yesterday/answer", json={"entry_id": entry, "answer": "had"})
    assert _stock() != before
    u = signed_in.post("/api/cooker/undo-eaten", json={"entry_id": entry})
    assert u.status_code == 200
    assert _stock() == before
    asked = signed_in.get("/api/today/yesterday").json()["meals"]
    assert [m["entry_id"] for m in asked] == [entry]


def test_nothing_tracked_says_nothing():
    entry = _yesterdays_dinner(stock=False)
    assert "kitchen_took" not in tools.answer_yesterday(entry, "had", today=TODAY)


def test_a_skip_does_not_take_anything_or_say_so():
    entry = _yesterdays_dinner()
    before = _stock()
    out = tools.answer_yesterday(entry, "skipped", today=TODAY)
    assert "kitchen_took" not in out
    assert _stock() == before


def test_the_card_offers_the_eaten_toast_and_the_undo_reasks_the_card():
    body = re.search(r"async function answerYesterday\b.*?\n  }\n", JS, re.S).group(0)
    assert "toastMarkedEaten(meal.meal, out.kitchen_took, entryId)" in body
    undo = re.search(r"async function undoMarkedEaten\b.*?\n  }\n", JS, re.S).group(0)
    assert "refreshYesterdayCheck()" in undo
