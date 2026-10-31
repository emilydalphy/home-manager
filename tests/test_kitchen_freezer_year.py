"""
A bag frozen last November read as frozen the day after tomorrow (defect
hunt 2026-10-31, walking the month and year rollover).

On Oct 31 the Kitchen's freezer row for chicken frozen 2025-11-02 said

    frozen 2 Nov

while the same row's sheet said "Freezer · frozen Nov 2, 2025".
whats_home._freezer_day dropped the year however old the date was; freezer
food is exactly what sits for months. It now names the year half a year
back or more — _day_word's own threshold — and only then.
"""
from __future__ import annotations

import datetime

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import whats_home


def _freeze(item: str, frozen_on: datetime.date) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, location, frozen_on) "
        "VALUES (1, ?, '1', 'freezer', ?)",
        (item, frozen_on.isoformat()),
    )
    conn.commit()
    conn.close()


def _line(item: str) -> str:
    rows = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("item") == item and "line" in o:
                rows.append(o["line"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(tools.get_whats_home())
    assert rows, f"{item} is not on the Kitchen tab"
    return rows[0]


def test_a_bag_frozen_most_of_a_year_ago_says_the_year():
    """CATCH: red before the fix ("frozen 2 Nov")."""
    today = household_today()
    frozen = today - datetime.timedelta(days=363)
    _freeze("Chicken thighs", frozen)
    assert _line("Chicken thighs") == f"frozen {frozen.day} {whats_home._MONTHS[frozen.month - 1]} {frozen.year}"


def test_a_bag_frozen_this_season_stays_short():
    """GUARD: inside half a year the mockup's short shape is unchanged."""
    today = household_today()
    frozen = today - datetime.timedelta(days=20)
    _freeze("Peas", frozen)
    assert _line("Peas") == f"frozen {frozen.day} {whats_home._MONTHS[frozen.month - 1]}"
