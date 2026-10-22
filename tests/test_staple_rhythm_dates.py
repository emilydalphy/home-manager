"""
Two dates a staple's rhythm is anchored on (defect hunt, 2026-10-22).

- A cadence the household TELLS for a staple that already exists moves its
  next due date with it. It used to change "about every 3 weeks" to "about
  every week" and leave "due in 3 weeks" standing.
- A staple made from the household's buying history learns from the day
  each line was TICKED, not the day it was listed. A week's line is made at
  approval and bought days later; anchoring on the approval made milk bought
  today "last bought Sunday, due" again within days.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import tools
from app.db import get_conn
from app.tools import staples as st

WEDNESDAY = date(2026, 9, 16)

pytestmark = pytest.mark.today(WEDNESDAY)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(st, "_TODAY_OVERRIDE", WEDNESDAY)
    yield


def _iso(days: int) -> str:
    return (WEDNESDAY + timedelta(days=days)).isoformat()


# ------------------------------------------------- a told cadence ----


def test_telling_a_cadence_for_an_existing_staple_moves_its_due_date():
    first = tools.add_staple("Coffee beans", category="pantry")
    assert first["cadence_days"] == 21 and first["next_due_at"] == _iso(21)
    told = tools.add_staple("coffee beans", every_days=7)
    assert told["created"] is False
    assert told["cadence_days"] == 7 and told["cadence_source"] == "told"
    # A week from the last purchase, not the three weeks the default set.
    assert told["next_due_at"] == _iso(7)
    assert told["due_words"] == "due next week"


def test_a_longer_told_cadence_pushes_the_due_date_out_from_the_last_purchase():
    tools.add_staple("Dish soap", category="household", every_days=14)
    st._TODAY_OVERRIDE = WEDNESDAY + timedelta(days=5)
    told = tools.add_staple("Dish soap", every_days=30)
    # Last bought is still the Wednesday it was added; the new rhythm runs
    # from there, not from today.
    assert told["last_bought_at"] == WEDNESDAY.isoformat()
    assert told["next_due_at"] == _iso(30)


def test_a_told_cadence_for_a_staple_never_bought_leaves_it_due():
    tools.add_staple("Milk", category="dairy", running_low=True)
    told = tools.add_staple("Milk", every_days=10)
    assert told["cadence_days"] == 10
    assert told["last_bought_at"] is None
    assert told["due"] is True and told["next_due_at"] == WEDNESDAY.isoformat()


# ------------------------------------------- seeded from history ----


def _bought_line(item: str, listed: str, ticked: str | None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, status, created_at, inventory_added_at) "
        "VALUES (?, ?, 'purchased', ?, ?)",
        (tools.household_id(), item, listed, ticked),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def test_a_staple_made_from_a_bought_row_learns_from_the_days_things_were_bought():
    # Three weeks of milk: each line made at Sunday's approval, ticked at
    # Wednesday's shop — the last one today.
    line = None
    for listed, ticked in (("2026-08-30", "2026-09-02"), ("2026-09-06", "2026-09-09"),
                           ("2026-09-13", "2026-09-16")):
        line = _bought_line("Milk", listed + " 15:00:00", ticked + " 16:00:00")
    s = tools.add_staple("Milk", grocery_item_id=line)
    assert s["cadence_source"] == "learned" and s["cadence_days"] == 7
    assert s["last_bought_at"] == WEDNESDAY.isoformat()
    assert s["next_due_at"] == _iso(7)
    assert s["due"] is False and s["due_words"] == "due next week"


def test_a_late_evening_tick_is_the_households_day_not_the_servers():
    # 02:30 UTC on the 10th is 22:30 on the 9th in Toronto (the default
    # zone) — the household bought it on the 9th.
    for ticked in ("2026-08-26 15:00:00", "2026-09-02 15:00:00", "2026-09-10 02:30:00"):
        _bought_line("Bread", ticked, ticked)
    s = tools.add_staple("Bread", category="bakery")
    assert s["cadence_source"] == "learned" and s["cadence_days"] == 7
    assert s["last_bought_at"] == "2026-09-09"
    assert s["next_due_at"] == WEDNESDAY.isoformat()


def test_a_line_bought_before_the_tick_stamp_still_counts_by_its_listing_day():
    for listed in ("2026-08-19", "2026-08-26", "2026-09-02"):
        _bought_line("Yogurt", listed + " 10:00:00", None)
    s = tools.add_staple("Yogurt", category="dairy")
    assert s["cadence_source"] == "learned" and s["cadence_days"] == 7
    assert s["last_bought_at"] == "2026-09-02"
