"""
A tin ticked off on Shop said it was best by TODAY (defect hunt 2026-10-29).

Walking a shop end to end — approve a week, sort it into two stores, tick it
off, open the Kitchen — the coconut milk bought that evening read

    Pantry · bought today · best by Oct 28 · in Friday's dinner

on Oct 28. Its row was right (expiration 2027-10-28: a tin's default shelf
life is 365 days); the words dropped the year, so every can bought on any
day read as going off that same day. whats_home._day_word now names the
year half a year or more out, either way, and only then.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import whats_home


def _tick(item: str, quantity: str, category: str) -> dict:
    """Put a line on the list and tick it off, the way the Shop checklist
    does — the kitchen row is the tick's own (source grocery_checkoff)."""
    line = tools.add_grocery_item(item, quantity, category=category)
    tools.mark_grocery_item(line["item_id"], status="purchased")
    conn = get_conn()
    row = conn.execute(
        "SELECT id, expiration_date FROM inventory_items WHERE household_id = 1 AND item = ?", (item,),
    ).fetchone()
    conn.close()
    return dict(row)


def test_a_tin_ticked_off_today_is_not_best_by_today():
    today = household_today()
    row = _tick("Coconut milk", "1 can", "canned")
    best = datetime.date.fromisoformat(row["expiration_date"])
    assert (best - today).days >= 365  # the row was always right

    detail = tools.get_whats_home_item(row["id"])["detail"]
    assert detail.startswith("Pantry · bought today · best by ")
    assert f"{best.day}, {best.year}" in detail, detail
    # The bug's exact words: today's month and day, with no year after it.
    same_day = f"best by {whats_home._MONTHS[today.month - 1]} {today.day}"
    assert same_day + "," in detail or same_day not in detail, detail


def test_a_date_within_half_a_year_stays_short():
    """Pantry's 180-day default: "Apr 26", no year — it can only mean the
    one coming."""
    today = household_today()
    row = _tick("Tortilla chips", "1 bag", "pantry")
    best = datetime.date.fromisoformat(row["expiration_date"])
    assert 7 <= (best - today).days < whats_home._YEAR_SHOWN_FROM_DAYS
    detail = tools.get_whats_home_item(row["id"])["detail"]
    assert f"best by {whats_home._MONTHS[best.month - 1]} {best.day}" in detail
    assert str(best.year) not in detail, detail


def test_day_word_names_the_year_from_half_a_year_either_way():
    today = datetime.date(2026, 10, 28)
    day = whats_home._day_word
    assert day(today, today) == "today"
    assert day(datetime.date(2026, 10, 31), today) == "Saturday"
    assert day(datetime.date(2026, 11, 11), today) == "Nov 11"
    assert day(datetime.date(2027, 4, 26), today) == "Apr 26"  # 180 days
    assert day(datetime.date(2027, 10, 28), today) == "Oct 28, 2027"  # a tin
    # Across New Year but close: still just the month and day.
    assert day(datetime.date(2027, 1, 2), datetime.date(2026, 12, 20)) == "Jan 2"
    # Bought a year ago is a year ago, not today.
    assert day(datetime.date(2025, 10, 28), today) == "Oct 28, 2025"
