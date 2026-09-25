"""
Made ahead and eaten cold is "Prepped", not "Reheat".

Emily, 2026-09-25, on Cook's checklist row "Made ahead — Wednesday's
Cucumber Slices with Tzatziki" wearing a Reheat pill and the line "made
ahead Wednesday · reheat · 3:30": "The snacks don't need to be "reheated"
they're cold. Just cause they're made ahead doesn't mean they need to be
reheated. This could be "prepped" instead if we know they're ready to go."

Recipes have no serve-temperature field, so leftovers.served_cold reads the
signals already on the row (see its docstring). A hot dish keeps Reheat.
"""
from __future__ import annotations

import datetime
from datetime import time
from pathlib import Path

from app import tools
from app.db import get_conn
from app.tools import leftovers as _leftovers

TODAY = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()
ISO_TODAY = TODAY.isoformat()
ISO_YESTERDAY = YESTERDAY.isoformat()

SHELL_JS = Path(__file__).resolve().parent.parent / "static" / "shell.js"


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _made_ahead(name: str, slot: str, **recipe) -> dict:
    """`name` made yesterday for today's `slot`; returns today's reheat move."""
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    tools.add_recipe(name, ingredients=[{"item": "Cucumber", "qty": "1"}], default_servings=2, **recipe)
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(ISO_YESTERDAY, name, slot=slot, weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, name, slot=slot, weekly_plan_id=plan_id)
    tools.set_cook_ahead(_entry_id(ISO_YESTERDAY, slot), [_entry_id(ISO_TODAY, slot)])
    payload = tools.today_moves(now=datetime.datetime.combine(TODAY, time(7)))
    return next(m for m in payload["moves"] if m["kind"] == "reheat")


def _cooker_card(entry_id: int) -> dict:
    view = tools.get_cooker_view()
    return next(m for m in view["meals"] if m["entry_id"] == entry_id)


# ---------- the reported row ----------

def test_a_made_ahead_cold_snack_says_made_ahead_and_not_reheat():
    move = _made_ahead(
        "Cucumber Slices with Tzatziki", "snack",
        instructions=["Slice the cucumber into rounds.", "Serve with tzatziki for dipping."],
    )
    assert move["served_cold"] is True
    assert "reheat" not in move["detail"], move["detail"]
    assert "reheat" not in move["meta"], move["meta"]
    assert move["meta"] == f"made ahead {YESTERDAY.strftime('%A')}"
    assert move["detail"].startswith(f"made ahead {YESTERDAY.strftime('%A')} · ")
    card = _cooker_card(move["entry_id"])
    assert card["is_leftovers"] is True
    assert card["served_cold"] is True


def test_a_made_ahead_hot_breakfast_still_says_reheat():
    move = _made_ahead(
        "Egg White Bites", "breakfast",
        instructions=["Whisk the eggs.", "Bake at 350°F for 20 minutes until set."],
        cook_time_minutes=20,
    )
    assert move["served_cold"] is False
    assert "· reheat ·" in move["detail"], move["detail"]
    assert move["meta"].endswith("· reheat")
    assert _cooker_card(move["entry_id"])["served_cold"] is False


def test_a_made_ahead_no_heat_lunch_is_prepped_too():
    """Not only snacks: a lunch whose method never meets heat is eaten cold."""
    move = _made_ahead(
        "Greek Chickpea Salad", "lunch",
        instructions=["Chop the cucumber and tomato.", "Toss with chickpeas, feta and dressing."],
    )
    assert move["served_cold"] is True
    assert "reheat" not in move["detail"]


# ---------- the rule ----------

def test_served_cold_rule():
    no_heat = {"instructions": ["Slice the cucumber.", "Serve with tzatziki."]}
    hot = {"instructions": ["Sear the chicken in a pan until golden."], "cook_time_minutes": 15}
    assert _leftovers.served_cold(no_heat, "dinner") is True
    assert _leftovers.served_cold(hot, "dinner") is False
    # A snack is eaten as it comes, even one that was baked.
    assert _leftovers.served_cold(hot, "snack") is True
    assert _leftovers.served_cold(None, "snack") is True
    # The recipe's own reheating advice wins over everything.
    assert _leftovers.served_cold({"notes": "Reheat gently before serving."}, "snack") is False
    # No-cook by its recorded time; unknown time is not zero.
    assert _leftovers.served_cold({"instructions": [], "cook_time_minutes": 0}, "lunch") is True
    assert _leftovers.served_cold({"instructions": [], "cook_time_minutes": None}, "lunch") is False
    # Nothing to go on: stays Reheat.
    assert _leftovers.served_cold(None, "dinner") is False
    assert _leftovers.served_cold({}, "breakfast") is False


# ---------- the Cook row (source-level; no JS harness in this repo) ----------

def test_cook_row_badge_says_prepped_for_a_cold_made_ahead_row():
    src = SHELL_JS.read_text()
    assert "(meal.served_cold ? 'Prepped' : 'Reheat')" in src
