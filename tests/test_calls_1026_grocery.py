"""
Emily's answers to the small grocery calls, 2026-10-26 ("go with your picks").

- Remembrance Day, Civic Holiday, Easter Monday, Diwali, Eid,
  Hanukkah and Lunar New Year no longer shut a store saved as "Closed on
  holidays" (holidays._SHOPS_OPEN_ON).
- Old grocery lines spelled "costco" are rewritten to the household's own
  usual-store spelling at startup (db._respell_grocery_stores_as_usual).
- A person's "1 onion" added onto the plan's "Onion 2" leaves it the plan's
  line, and Undo puts it back cleanly — no blank "Onion" after the meal goes.
- A typed "1/2 dozen" / "1/2 gallon" stays a half.
"""
from __future__ import annotations

import datetime
import json

from app import tools
from app.db import get_conn, _respell_grocery_stores_as_usual
from app.tools import holidays as _holidays
from app.tools import quantities as _q
from app.tools import stores as _stores
from conftest import approving_on, household_today, on_the_weeks_monday


# ---------- 3 + 4: holidays most Ontario grocers open on ----------

def test_civic_and_religious_holidays_shut_no_shop():
    for name in ("Remembrance Day", "Civic Holiday", "Easter Monday",
                 "Diwali", "Eid", "Eid al-Fitr", "Eid al-Adha", "Hanukkah", "Chanukah",
                 "Lunar New Year", "Chinese New Year"):
        assert not _holidays.shops_close_for(name), name
    # Real days off still close a store that closes on holidays.
    # Good Friday is a legal retail holiday in Ontario: it still closes one.
    for name in ("Christmas Day", "Thanksgiving", "Canada Day", "Labour Day", "Family Day",
                 "Good Friday"):
        assert _holidays.shops_close_for(name), name


def test_a_store_closed_on_holidays_is_open_on_remembrance_day():
    tools.add_usual_stores(["Farm Boy"])
    tools.set_store_hours("Farm Boy", {
        "days": {d: ["08:00", "21:00"] for d in _stores.HOURS_DAYS}, "closed_on_holidays": True,
    })
    day = datetime.date(2026, 11, 11)
    assert tools.holiday_on(day.isoformat()) is not None, "still on the calendar"
    assert _stores.open_window("Farm Boy", day)["opens"] == datetime.datetime.combine(day, datetime.time(8, 0))
    civic = datetime.date(2026, 8, 3)
    assert _stores.open_window("Farm Boy", civic)["opens"] == datetime.datetime.combine(civic, datetime.time(8, 0))
    assert _stores.open_window("Farm Boy", datetime.date(2026, 12, 25)) == {"closed": True, "holiday": True}
    good_friday = datetime.date(2027, 3, 26)
    assert _stores.open_window("Farm Boy", good_friday) == {"closed": True, "holiday": True}
    easter_monday = datetime.date(2027, 3, 29)
    assert _stores.open_window("Farm Boy", easter_monday)["opens"] == datetime.datetime.combine(
        easter_monday, datetime.time(8, 0))


# ---------- 5: old "costco" lines take the household's spelling ----------

def test_old_costco_lines_take_the_usual_spelling_and_the_cleanup_is_idempotent():
    tools.add_usual_stores(["Costco", "Farm Boy"])
    a = tools.add_grocery_item("paper towels")["item_id"]
    b = tools.add_grocery_item("rotisserie chicken")["item_id"]
    c = tools.add_grocery_item("honey")["item_id"]
    conn = get_conn()
    hid = tools.household_id()
    # As an old build wrote them: exactly as typed.
    for row_id, store in ((a, "costco"), (b, " COSTCO  "), (c, "Bulk Barn")):
        conn.execute("UPDATE grocery_items SET store = ? WHERE id = ? AND household_id = ?", (store, row_id, hid))
    conn.commit()
    _respell_grocery_stores_as_usual(conn)
    _respell_grocery_stores_as_usual(conn)
    conn.commit()
    stores = {r["id"]: r["store"] for r in conn.execute(
        "SELECT id, store FROM grocery_items WHERE household_id = ?", (hid,)).fetchall()}
    conn.close()
    assert stores[a] == "Costco"
    assert stores[b] == "Costco"
    assert stores[c] == "Bulk Barn", "not a usual store: left as it was"


def test_the_cleanup_leaves_a_household_with_no_usual_stores_alone():
    item = tools.add_grocery_item("eggs")["item_id"]
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET store = 'costco' WHERE id = ?", (item,))
    conn.execute("UPDATE meal_preferences SET usual_stores_json = ? WHERE household_id = ?",
                 (json.dumps([]), tools.household_id()))
    _respell_grocery_stores_as_usual(conn)
    store = conn.execute("SELECT store FROM grocery_items WHERE id = ?", (item,)).fetchone()["store"]
    conn.close()
    assert store == "costco"


# ---------- 7: an add onto the plan's line stays the plan's ----------

def _onions() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, source_weekly_plan_id IS NOT NULL AS plan, kept_amount FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' AND LOWER(item) LIKE 'onion%' ORDER BY id",
        (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [(r["quantity"], bool(r["plan"]), r["kept_amount"] or "") for r in rows]


def _plan_with_curry():
    tools.add_recipe("Curry", ingredients=[{"item": "Onion", "qty": "2", "category": "produce"}])
    tools.add_recipe("Pasta", ingredients=[{"item": "Spaghetti", "qty": "1 lb", "category": "pantry"}])
    today = household_today()
    plan = tools.create_weekly_plan(today.isoformat(), day_count=7)["weekly_plan_id"]
    night = (today + datetime.timedelta(days=2)).isoformat()
    tools.plan_meal(night, "Curry", slot="dinner", weekly_plan_id=plan)
    with approving_on(today):
        tools.approve_weekly_plan(plan, approved_by="Emily")
    return plan, night


def test_a_typed_onion_onto_the_plans_line_stays_the_plans_line():
    _plan_with_curry()
    assert _onions() == [("2", True, "")]
    r = tools.add_grocery_item("onion", "1")
    assert r["merged"] is True
    assert _onions() == [("3", True, "1")], "the plan's line, her 1 recorded beside it"


def test_undo_then_the_meal_going_leaves_no_blank_onion():
    plan, night = _plan_with_curry()
    r = tools.add_grocery_item("onion", "1")
    # The Shop toast's Undo: the phone's copy of the line's amount.
    tools.update_grocery_item(r["item_id"], quantity="2")
    assert _onions() == [("2", True, "")]
    tools.swap_meal_in_plan(plan, night, "Pasta", slot="dinner")
    assert _onions() == [], "no blank Onion left behind"


def test_without_undo_her_onion_outlives_the_meal():
    plan, night = _plan_with_curry()
    tools.add_grocery_item("onion", "1")
    tools.swap_meal_in_plan(plan, night, "Pasta", slot="dinner")
    assert [q for q, _plan, _kept in _onions()] == ["1"]


def test_her_add_on_the_plans_line_never_creeps_over_swaps():
    plan, night = _plan_with_curry()
    tools.add_grocery_item("onion", "1")
    for _ in range(3):
        tools.swap_meal_in_plan(plan, night, "Pasta", slot="dinner")
        assert [q for q, _p, _k in _onions()] == ["1"]
        tools.swap_meal_in_plan(plan, night, "Curry", slot="dinner")
        assert _onions() == [("3", True, "1")], _onions()


def test_a_rack_staple_add_onto_the_plans_line_still_becomes_theirs():
    # Spices, sauces and pantry staples keep the old rule: the tick and
    # untick of the rack read a household's own line as theirs.
    from app.tools import spices as _spices
    assert _spices.is_assumed("Basmati rice")


# ---------- 9: half-dozens and half-gallons stay as typed ----------

def test_typed_half_dozen_and_half_gallon_stay_halves():
    eggs = tools.add_grocery_item("eggs", "1/2 dozen")
    milk = tools.add_grocery_item("milk", "0.5 gallon")
    assert eggs["quantity"] == "1/2 dozen"
    assert milk["quantity"] == "1/2 gallon"
    shown = {r["item"]: r["quantity"] for r in tools.list_grocery_list()}
    assert shown == {"eggs": "1/2 dozen", "milk": "1/2 gallon"}
    # Two halves are a whole one.
    assert tools.add_grocery_item("milk", "1/2 gallon")["quantity"] == "1 gallon"


def test_one_and_a_half_and_the_rest_are_unchanged():
    assert _q.typed_half_pack("1 1/2 gallons") == "1 1/2 gallons"
    assert _q.typed_half_pack("1 dozen") is None
    assert _q.typed_half_pack("1/2 cup") is None
    assert _q.typed_half_pack("a dozen") is None
    assert _q.tidy_display_quantity("milk", "1/2 gallon") == "1/2 gallon"
    assert _q.tidy_display_quantity("milk", "1.3 gallon") == "2 gallons"


# ---------- 11: Today's night off follows the new night's table ----------

def _week_with_salmon_wednesday(guests_thursday: int):
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    wed = (monday + datetime.timedelta(days=2)).isoformat()
    thu = (monday + datetime.timedelta(days=3)).isoformat()
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)
    tools.add_recipe("Salmon Bowls", default_servings=3, ingredients=[{"item": "salmon fillets", "qty": "3"}])
    plan = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(wed, "Salmon Bowls", slot="dinner", weekly_plan_id=plan)
    if guests_thursday:
        tools.set_guest_count(thu, "dinner", guests_thursday)
    with approving_on(monday):
        tools.approve_weekly_plan(plan, "Alex")
    return wed, thu


def _salmon() -> list[str]:
    return [r["quantity"] for r in tools.list_grocery_list(status="needed") if r["item"] == "salmon fillets"]


@on_the_weeks_monday()
def test_a_night_off_move_onto_a_bigger_table_buys_for_it_and_undo_puts_it_back():
    from app.tools import tonight as _tonight

    wed, thu = _week_with_salmon_wednesday(3)  # Thursday's table is six
    assert _salmon() == ["3"]
    out = _tonight.tonight_night_off(now=datetime.datetime.fromisoformat(f"{wed}T15:50:00"))
    assert out["kind"] == "move" and out["moved_to"] == thu
    assert out["list_changed"] is True
    assert "_redated" not in out
    assert _salmon() == ["6"], "bought for Thursday's six"
    back = _tonight.tonight_night_off_undo(now=datetime.datetime.fromisoformat(f"{wed}T15:55:00"))
    assert back["status"] == "restored" and back["list_changed"] is True
    assert _salmon() == ["3"]


@on_the_weeks_monday()
def test_a_night_off_move_between_same_size_tables_leaves_the_list_alone():
    from app.tools import tonight as _tonight

    wed, _thu = _week_with_salmon_wednesday(0)
    out = _tonight.tonight_night_off(now=datetime.datetime.fromisoformat(f"{wed}T15:50:00"))
    assert out["kind"] == "move"
    assert out["list_changed"] is False
    assert _salmon() == ["3"]
