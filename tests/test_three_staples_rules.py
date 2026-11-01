"""
Three small staples rules (Emily, 2026-10-31: "go with the recommendations").

1. Making a staple again by name while its old line is still to buy takes
   that line over and counts the staple due now, the way "Make it a staple"
   on a Shop row does. It used to record "last bought today, due in a week"
   beside an unbought line.
2. "Not this trip" waits the shorter of 7 days and the staple's own rhythm,
   so for milk every 4 days it never waits longer than "We have plenty".
3. Removing a line that's in the cart: left alone (low reach).
"""

from __future__ import annotations

from datetime import date, timedelta

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


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _lines(name: str) -> list:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, staple_id, status FROM grocery_items WHERE household_id = 1 AND LOWER(item) = LOWER(?) "
            "AND status IN ('needed', 'in_cart')", (name,)).fetchall()]
    finally:
        conn.close()


def _due_line(name: str) -> int:
    tools.get_grocery_list_by_section(status="needed")  # sync puts the due line on
    (line,) = _lines(name)
    return line["id"]


# ---------- 1. re-adding a staple takes over its unbought line ----------

def test_removing_then_re_adding_a_staple_takes_over_its_line_and_is_due_now():
    tools.add_staple("Oat milk", category="dairy", running_low=True)
    line_id = _due_line("Oat milk")
    tools.remove_staple("Oat milk")
    assert _lines("Oat milk")[0]["staple_id"] is None, "removing leaves the line, unattached"
    again = tools.add_staple("Oat milk", category="dairy")
    assert again["due"] is True
    assert again["last_bought_at"] is None, "no purchase invented"
    assert again["next_due_at"] == _iso(0)
    (line,) = _lines("Oat milk")
    assert line["id"] == line_id and line["staple_id"] == again["id"]
    # And the next read of the list adds no second line for it.
    tools.get_grocery_list_by_section(status="needed")
    assert len(_lines("Oat milk")) == 1


def test_a_weeks_line_is_not_taken_over():
    """A line the week's meals put there stays the plan's."""
    conn = get_conn()
    plan_id = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date) VALUES (1, ?)", (_iso(-2),)
    ).lastrowid
    conn.execute("INSERT INTO grocery_items (household_id, item, quantity, category, status, source_weekly_plan_id) "
                 "VALUES (1, 'Oat milk', '1 carton', 'dairy', 'needed', ?)", (plan_id,))
    conn.commit()
    conn.close()
    s = tools.add_staple("Oat milk", category="dairy")
    assert _lines("Oat milk")[0]["staple_id"] is None
    assert s["last_bought_at"] == TODAY.isoformat()


def test_an_existing_staple_said_again_takes_over_nothing():
    kept = tools.add_staple("Coffee beans", category="pantry")
    tools.add_grocery_item("Coffee beans", category="pantry")
    again = tools.add_staple("Coffee beans")
    assert again["created"] is False and again["next_due_at"] == kept["next_due_at"]
    assert _lines("Coffee beans")[0]["staple_id"] is None


# ---------- 2. "Not this trip" waits the shorter of a week and the rhythm ----------

def test_not_this_trip_for_milk_every_four_days_asks_again_in_four():
    tools.add_staple("Milk", category="dairy", every_days=4, running_low=True)
    skipped = tools.decide_staple_line(_due_line("Milk"), "skip")
    assert skipped["next_due_at"] == _iso(4)


def test_not_this_trip_never_waits_longer_than_we_have_plenty():
    tools.add_staple("Milk", category="dairy", every_days=4, running_low=True)
    plenty = tools.mark_staple_plenty("Milk")
    tools.add_staple("Milk", running_low=True)
    skipped = tools.decide_staple_line(_due_line("Milk"), "skip")
    assert skipped["next_due_at"] <= plenty["next_due_at"]


def test_not_this_trip_for_a_slow_staple_is_still_a_week():
    tools.add_staple("Dish soap", category="household", every_days=30, running_low=True)
    skipped = tools.decide_staple_line(_due_line("Dish soap"), "skip")
    assert skipped["next_due_at"] == _iso(st.SKIP_DAYS)
