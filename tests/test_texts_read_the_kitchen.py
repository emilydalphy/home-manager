"""
What Pomona says outside the screens, read against the Kitchen tab
(overnight hunt, 2026-10-28).

1. A fridge move for food the Kitchen tab has thrown out, used up or moved
   to the fridge is not a move any more — not on Today, not in the morning
   text, not ahead of the evening nudge. Undo brings it back.
2. The bell's "turns soon" (which the morning text reads) never names a
   staple or a spice, the rule Today's going-off line and the Kitchen tab
   already keep — and its list reads as a list.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime

from app import tools
from app.db import get_conn
from app.tools import defrost, digest, prep_sessions, whats_home
from conftest import household_today

TODAY = household_today()


def _iso(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, dt.time(hour, minute))


def _chicken_id() -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM inventory_items WHERE item = 'Chicken Thighs'").fetchone()
    conn.close()
    return row["id"]


def _thaw_tomorrow(tonight: bool = False) -> int:
    """Frozen chicken for tomorrow's skewers: today's fridge move."""
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=15, default_servings=2)
    tools.add_recipe("Pasta", ingredients=[{"item": "Penne", "qty": "1 lb"}],
                     prep_time_minutes=5, cook_time_minutes=15, default_servings=2)
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="freezer")
    plan_id = tools.create_weekly_plan(_iso(-1))["weekly_plan_id"]
    if tonight:
        tools.plan_meal(_iso(0), "Pasta", slot="dinner", weekly_plan_id=plan_id,
                        add_ingredients_to_grocery_list=False)
    tools.plan_meal(_iso(1), "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=False)
    defrost.sync_defrost_tasks(plan_id)
    return plan_id


def _fridge_titles() -> list[str]:
    return [m["title"] for m in tools.today_moves(day=TODAY, now=_at(7))["moves"] if m["kind"] == "fridge"]


# ---------- 1. a fridge move for food that's gone ----------

def test_thrown_out_freezer_food_is_no_fridge_move_anywhere_and_undo_brings_it_back():
    plan_id = _thaw_tomorrow(tonight=True)
    assert _fridge_titles() == ["Move the Chicken Thighs to the fridge"]
    assert "Move the Chicken Thighs" in digest.build_morning_text(_at(7), link=False)

    undo = whats_home.threw_out_item(_chicken_id())["undo"]
    assert _fridge_titles() == []
    assert "Chicken Thighs" not in (digest.build_morning_text(_at(7), link=False) or "")
    # The evening nudge's "first" is the same read.
    assert digest.tonight_for_nudge(_at(17, 30))["first"] is None
    assert digest.build_evening_nudge(_at(17, 30), link=False) == "Tonight: Pasta — 20 min. Tap to start."
    # Chat's "what do I need to defrost?" says the same.
    assert defrost.get_defrost_schedule() == []
    # And the prep session, which reads the rows on its own.
    rows = prep_sessions._prep_task_rows(plan_id)
    assert all(r["task_type"] != "defrost" for r in rows)

    whats_home.restore_item(undo)
    assert _fridge_titles() == ["Move the Chicken Thighs to the fridge"]


def test_used_up_or_moved_to_the_fridge_on_the_kitchen_tab_is_no_move_either():
    _thaw_tomorrow()
    whats_home.move_item(_chicken_id(), "fridge")
    assert _fridge_titles() == []


def test_a_ticked_move_stays_as_the_record_when_the_food_is_gone():
    _thaw_tomorrow()
    task = next(m for m in tools.today_moves(day=TODAY, now=_at(7))["moves"] if m["kind"] == "fridge")
    tools.check_off_prep_step(task["task_id"], "done")
    whats_home.used_up_item(_chicken_id())
    rows = [r for r in tools.get_prep_schedule() if r["task_type"] == "defrost"]
    assert [r["status"] for r in rows] == ["done"]


# ---------- 2. "turns soon" never names a staple or a spice ----------

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
    assert bell["body"].startswith("Greek yogurt and spinach turn soon") or \
        bell["body"].startswith("Spinach and greek yogurt turn soon")

    for name in ("Cilantro", "Cream"):
        tools.update_inventory(name, "add", quantity="1", expiration_date=_iso(1),
                               category="produce", location="fridge")
    bell = next(n for n in tools.get_active_notifications() if n["type"] == "expiring_soon")
    assert bell["title"] == "4 things to use this week"
    assert " and 2 more turn soon — " in bell["body"]
