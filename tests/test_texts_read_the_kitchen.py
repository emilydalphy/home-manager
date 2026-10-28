"""
The bell's "turns soon" — which the morning text reads — never names a
staple or a spice, the rule Today's going-off line and the Kitchen tab
already keep, and its list reads as a list (overnight hunt, 2026-10-28).
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime

from app import tools
from app.tools import digest
from conftest import household_today

TODAY = household_today()


def _iso(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, dt.time(hour, minute))


# ---------- "turns soon" never names a staple or a spice ----------

def test_the_bell_and_the_morning_text_never_say_a_staple_or_spice_turns_soon():
    tools.add_staple("Milk")
    tools.update_inventory("Whole milk", "add", quantity="1", expiration_date=_iso(1),
                           category="dairy", location="fridge")
    tools.update_inventory("Paprika", "add", quantity="1", expiration_date=_iso(1),
                           category="pantry", location="pantry")
    assert all(n["type"] != "expiring_soon" for n in tools.get_active_notifications())
    assert digest.build_morning_text(_at(7), link=False) is None

    tools.update_inventory("Spinach", "add", quantity="1 bag", expiration_date=_iso(1),
                           category="produce", location="fridge")
    bell = next(n for n in tools.get_active_notifications() if n["type"] == "expiring_soon")
    assert bell["title"] == "Your spinach turns soon"
    assert digest.build_morning_text(_at(7), link=False) == "Your spinach turns soon."


def test_the_list_of_things_turning_reads_as_a_list():
    for name in ("Spinach", "Greek yogurt"):
        tools.update_inventory(name, "add", quantity="1", expiration_date=_iso(1),
                               category="produce", location="fridge")
    bell = next(n for n in tools.get_active_notifications() if n["type"] == "expiring_soon")
    assert bell["title"] == "2 things to use this week"
    assert bell["body"].startswith(("Greek yogurt and Spinach turn soon", "Spinach and Greek yogurt turn soon"))
    assert "greek" not in bell["body"]

    for name in ("Cilantro", "Cream"):
        tools.update_inventory(name, "add", quantity="1", expiration_date=_iso(1),
                               category="produce", location="fridge")
    bell = next(n for n in tools.get_active_notifications() if n["type"] == "expiring_soon")
    assert bell["title"] == "4 things to use this week"
    assert " and 2 more turn soon — " in bell["body"]
