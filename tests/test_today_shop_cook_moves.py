"""
The server half of "Today: Shop / Cook, Morning · Afternoon · Evening"
(Emily, 2026-09-17; the screen itself is tests/test_today_shop_cook.py).

Three things app/tools/moves.py hands Today now, so the screen never has
to re-derive them:

    `meta`  — the one clock-free line under a row's title ("for Thursday's
              skewers", "35 min", "made ahead Sunday · reheat"). The clock
              is the Morning / Afternoon / Evening tag's job (shell.js
              moveTimeOfDay); `detail` keeps it for the morning text.
    `stops` — on the shop move: one per store with something to buy, with
              the first few things, for "Costco · 6 things" rows.
    the shop's action word — "Go shopping", never "Open the list".

And the fact the tag leans on: dinner lands in the Evening for every
dinner_window a household can pick.
"""
from __future__ import annotations

import datetime
from datetime import time

from app import tools
from app.db import get_conn
from app.tools import defrost as _defrost
from app.tools import moves as _moves


TODAY = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
ISO_TODAY = TODAY.isoformat()
ISO_YESTERDAY = YESTERDAY.isoformat()
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(TODAY, time(hour, minute))


def _household():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)


def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _fridge_task(plan_id: int, description: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', 'defrost')",
        (tools.household_id(), plan_id, ISO_TODAY, description, "Chicken Skewers"),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def _entry_id(day: str, slot: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _by_kind(payload: dict) -> dict:
    return {m["kind"]: m for m in payload["moves"]}


# ---------- meta: the clock-free line ----------

def test_every_move_carries_a_meta_line_and_none_of_them_says_a_clock():
    _household()
    tools.add_recipe("Egg White Bites", ingredients=[{"item": "Eggs", "qty": "6"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2)
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=3)
    tools.add_recipe("Chopped Salad", ingredients=[{"item": "Lettuce", "qty": "1"}],
                     default_servings=2)
    plan_id = _plan()
    tools.plan_meal(ISO_YESTERDAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)
    tools.set_cook_ahead(_entry_id(ISO_YESTERDAY, "breakfast"), [_entry_id(ISO_TODAY, "breakfast")])
    tools.plan_meal(ISO_TODAY, "Chopped Salad", slot="lunch", weekly_plan_id=plan_id)
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    _fridge_task(plan_id, "Move the chicken thighs to the fridge — for Thursday’s skewers.")

    moves = tools.today_moves(now=_at(7))["moves"]
    by_id = {m["id"]: m for m in moves}
    reheat = [m for m in moves if m["kind"] == "reheat"][0]
    cooks = {m["slot"]: m for m in moves if m["kind"] == "cook"}
    fridge = [m for m in moves if m["kind"] == "fridge"][0]
    shop = [m for m in moves if m["kind"] == "shop"][0]

    assert reheat["meta"] == "made ahead " + YESTERDAY.strftime("%A") + " · reheat"
    assert cooks["dinner"]["meta"] == "35 min"
    assert cooks["lunch"]["meta"] == "lunch", "no minutes on the card, so the slot"
    assert fridge["meta"] == "for Thursday’s skewers"
    assert shop["meta"] == "Chicken Thighs"
    for m in by_id.values():
        assert "meta" in m, m["id"]
        assert ":" not in m["meta"] and "noon" not in m["meta"], (m["id"], m["meta"])
    # `detail` still says the clock — the morning text reads it.
    assert "6:30" in cooks["dinner"]["detail"] or "6:30" in cooks["dinner"]["time_label"]


def test_a_fridge_move_running_late_says_so_on_its_meta_line():
    _household()
    plan_id = _plan()
    _fridge_task(plan_id, "Move the chicken thighs to the fridge — for Thursday’s skewers.")
    tools.set_dinner_window("5_6ish")

    fridge = _by_kind(tools.today_moves(now=_at(19, 30)))["fridge"]

    assert fridge["overdue"] is True
    assert fridge["meta"] == "for Thursday’s skewers · still to do"
    assert fridge["detail"] == "fridge move · still to do"


# ---------- stops: one row per store ----------

def test_the_shop_move_names_its_stops_with_the_first_few_things():
    _household()
    tools.add_recipe("Garlic Butter Shrimp", ingredients=[{"item": "Shrimp", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=15, default_servings=2)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    for name in ("Orzo", "Salmon", "Black beans", "Lemons", "Dish soap"):
        tools.add_grocery_item(name, quantity="1")
    for item in tools.list_grocery_list(status="needed"):
        if item["item"] in ("Orzo", "Salmon", "Black beans", "Lemons"):
            tools.set_grocery_item_store(item["id"], "Costco")

    shop = _by_kind(tools.today_moves(now=_at(7)))["shop"]

    assert shop["action"]["label"] == "Go shopping"
    assert shop["action"]["target"] == {"tab": "grocery"}
    costco, loose = shop["stops"]
    assert costco["store"] == "Costco" and costco["count"] == 4
    assert len(costco["items"]) == 3 and set(costco["items"]) <= {"Orzo", "Salmon", "Black beans", "Lemons"}
    assert loose == {"store": "", "count": 1, "items": ["Dish soap"]}, "untagged rows are one stop of their own, last"
    assert shop["meta"].endswith("…") and shop["meta"].count(",") == 2


def test_a_list_nothing_is_tagged_on_is_one_stop_its_whole_self():
    _household()
    tools.add_recipe("Garlic Butter Shrimp", ingredients=[{"item": "Shrimp", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=15, default_servings=2)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Garlic Butter Shrimp", slot="dinner", weekly_plan_id=plan_id)
    tools.add_grocery_item("Rice", quantity="1 bag")
    tools.add_grocery_item("Dish soap", quantity="1")

    shop = _by_kind(tools.today_moves(now=_at(7)))["shop"]

    assert shop["stops"] == [{"store": "", "count": 2, "items": ["Dish soap", "Rice"]}]
    assert shop["meta"] == "Dish soap, Rice"
    assert shop["detail"] == "", "the untimed line still claims no stops the rows don't name"


def test_a_timed_shop_carries_stops_too():
    _household()
    tools.add_recipe("Sunday Roast", ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=20, cook_time_minutes=90, default_servings=2)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Sunday Roast", slot="dinner", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)

    shop = _by_kind(tools.today_moves(now=_at(12)))["shop"]

    assert shop.get("timed") is True
    assert shop["stops"] == [{"store": "", "count": 1, "items": ["Chicken Thighs"]}]
    assert shop["action"]["label"] == "Go shopping"


def test_no_move_ever_says_open_the_list():
    src = (_moves.__file__ and open(_moves.__file__, encoding="utf-8").read())
    assert '"label": "Open the list"' not in src
    assert _moves.SHOP_ACTION_LABEL == "Go shopping"


# ---------- dinner is the Evening ----------

def test_every_dinner_window_lands_dinner_in_the_evening():
    """The Morning / Afternoon / Evening tag on Today (shell.js
    moveTimeOfDay) reads Evening from 17:00. Dinner's clock is the
    dinner_window rhythm fact through moves._dinner_clock, so this is what
    keeps "dinner sits in Evening" true for every answer a household can
    give — and the default when they gave none."""
    clocks = list(_defrost._DINNER_CLOCK_BY_WINDOW.values()) + [_moves.DEFAULT_SLOT_HOURS["dinner"]]
    assert clocks, "no dinner clocks to check"
    for clock in clocks:
        assert clock >= time(17, 0), clock
    for window in list(_defrost._DINNER_CLOCK_BY_WINDOW) + ["all_over", ""]:
        if window:
            tools.set_dinner_window(window)
        assert _moves._dinner_clock() >= time(17, 0), window


def test_a_planned_dinner_is_on_the_table_at_five_or_later_for_every_window():
    """Off the real move: window_start + duration is when the plate lands
    (what the tag reads), and it is never before five."""
    _household()
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=3)
    plan_id = _plan()
    tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)
    for window in ("5_6ish", "6_8", "later", "all_over"):
        tools.set_dinner_window(window)
        cook = _by_kind(tools.today_moves(now=_at(9)))["cook"]
        start = datetime.datetime.fromisoformat(cook["window_start"])
        lands = start + datetime.timedelta(minutes=cook["duration_min"])
        assert lands.time() >= time(17, 0), (window, lands)
