"""
A household's own second amount adds up in its unit family (2026-10-20).

Adding "500 g" of flour and then "1 kg" read "500 g + 1 kg" on the list,
and "8 oz" of beef then "1 lb" read "8 oz + 1 lb": two numbers to add in
the aisle. Volumes already added within their family on a household's own
line (the 2026-10-19 part-cups work, grocery._sum_measures); weights,
metric masses and metric volumes did not. Grams never become pounds here:
families only, as quantities._convert_to_unit draws the line.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import household_today

from app import tools
from app.db import get_conn


def _lines(name: str) -> list[str]:
    return [i["quantity"] for i in tools.list_grocery_list() if i["item"] == name]


@pytest.mark.parametrize("a,b,expected", [
    ("500 g", "1 kg", "1.5 kg"),
    ("8 oz", "1 lb", "1.5 lbs"),
    ("1 lb", "4 oz", "1.25 lbs"),
    ("500 ml", "1 l", "1.5 l"),
    ("250 g", "250 g", "500 g"),       # one word: unchanged
])
def test_a_households_two_amounts_in_one_family_add_up(a, b, expected):
    tools.add_grocery_item("Flour", quantity=a, category="pantry")
    tools.add_grocery_item("Flour", quantity=b, category="pantry")
    assert _lines("Flour") == [expected]


def test_the_shop_tabs_add_route_answers_the_summed_amount(signed_in):
    signed_in.post("/api/grocery-list/add", json={"item": "ground beef", "quantity": "8 oz"})
    res = signed_in.post("/api/grocery-list/add", json={"item": "Ground beef", "quantity": "1 lb"})
    assert res.status_code == 200
    body = res.json()
    assert body["merged"] is True
    assert body["units_reconciled"] is True
    assert body["quantity"] == "1.5 lbs"


@pytest.mark.parametrize("a,b,expected", [
    ("1 lb", "2 oz", "1 lb + 2 oz"),
    ("1 kg", "100 g", "1 kg + 100 g"),
    ("2 oz", "1 lb", "2 oz + 1 lb"),
])
def test_a_small_amount_that_would_round_away_stays_on_the_line(a, b, expected):
    """GUARD: weights round to the nearest quarter, and a sum that reads no
    more than the line did would swallow what was just typed."""
    tools.add_grocery_item("Beef", quantity=a, category="meat/seafood")
    tools.add_grocery_item("Beef", quantity=b, category="meat/seafood")
    assert _lines("Beef") == [expected]


def test_grams_and_pounds_are_still_both_shown():
    """GUARD: across systems nothing is guessed — both amounts stay."""
    tools.add_grocery_item("Butter", quantity="1 lb", category="dairy")
    tools.add_grocery_item("Butter", quantity="250 g", category="dairy")
    assert _lines("Butter") == ["1 lb + 250 g"]


def test_a_plans_grams_beside_a_households_kilo_keep_their_own_line_and_leave_cleanly():
    """GUARD: a plan's share in another unit word still gets a plan line,
    so dropping the dinner gives the household's kilo back untouched."""
    conn = get_conn()
    conn.execute("DELETE FROM members WHERE household_id = ? AND name != 'Emily'", (tools.household_id(),))
    conn.commit()
    conn.close()
    tools.add_recipe("Stir fry", ingredients=[{"item": "Chicken thighs", "qty": "300 g", "category": "meat/seafood"}], default_servings=1)
    tools.add_grocery_item("Chicken thighs", quantity="500 g", category="meat/seafood")
    tools.add_grocery_item("Chicken thighs", quantity="500 g", category="meat/seafood")
    assert _lines("Chicken thighs") == ["1 kg"]
    today = household_today()
    start = today - timedelta(days=today.weekday()) + timedelta(days=7)
    plan_id = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    tools.plan_meal(start.isoformat(), "Stir fry", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    assert sorted(_lines("Chicken thighs")) == sorted(["1 kg", "300 g"])
    tools.clear_plan_slot(plan_id, start.isoformat(), "dinner")
    assert _lines("Chicken thighs") == ["1 kg"]


def test_a_line_written_before_this_change_folds_on_the_next_add():
    """A "500 g + 1 kg" line already in a household's list reads "2 kg" after
    another 500 g — not "1 kg + 1 kg" (main's refold, same unit only)."""
    tools.add_grocery_item("Flour", quantity="500 g", category="pantry")
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET quantity = '500 g + 1 kg' WHERE item = 'Flour'")
    conn.commit()
    conn.close()
    res = tools.add_grocery_item("Flour", quantity="500 g", category="pantry")
    assert res["quantity"] == "2 kg"
    conn = get_conn()
    stored = conn.execute("SELECT quantity FROM grocery_items WHERE item = 'Flour'").fetchone()["quantity"]
    conn.close()
    assert stored == "2 kg"
