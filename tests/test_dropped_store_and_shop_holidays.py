"""
Two store defects found in the 2026-10-22 holidays/stores hunt.

1. Taking a usual store off the list dropped its typical items and hours
   but left every item_store_preferences row pointing at it, so lines kept
   being filed under the store the household had just removed.
2. "Closed on holidays" counted every day the holidays module knows by
   name — Halloween, Mother's Day, Father's Day — so a store open as usual
   read "closed for the holiday".
"""
from __future__ import annotations

import datetime

from app import tools
from app.db import get_conn
from app.tools import stores as _stores


def _lines() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT item, store, store_decided, status FROM grocery_items").fetchall()
    conn.close()
    return {r["item"]: dict(r) for r in rows}


def _prefs() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT item, store FROM item_store_preferences").fetchall()
    conn.close()
    return {r["item"]: r["store"] for r in rows}


def _costco_and_farm_boy():
    tools.add_usual_stores(["Costco", "Farm Boy"])
    tools.set_item_store("paper towels", "Costco")
    tools.set_item_store("milk", "Farm Boy")
    tools.add_grocery_item("paper towels", "1")
    tools.add_grocery_item("milk", "1")
    tools.add_grocery_item("batteries", "1")
    tools.set_grocery_item_store(_lines_id("batteries"), "Costco")
    conn = get_conn()
    # A line already in the cart at Costco keeps the store it is bought at.
    conn.execute("UPDATE grocery_items SET status = 'in_cart' WHERE item = 'batteries'")
    conn.commit()
    conn.close()


def _lines_id(item: str) -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM grocery_items WHERE item = ?", (item,)).fetchone()
    conn.close()
    return row["id"]


def test_removing_a_store_unfiles_what_was_filed_there(signed_in):
    _costco_and_farm_boy()
    res = signed_in.post("/api/memory/delete", json={"field": "usual_stores", "item": "costco"})
    assert res.status_code == 200, res.text

    lines = _lines()
    assert lines["paper towels"]["store"] == "" and lines["paper towels"]["store_decided"] == 0
    assert lines["milk"]["store"] == "Farm Boy", "another store's filing is untouched"
    assert lines["batteries"]["store"] == "Costco", "a line in the cart keeps its store"
    assert "paper towels" not in _prefs() and _prefs()["milk"] == "Farm Boy"
    assert sorted(s["store"] for s in tools.get_grocery_list_by_store()["stores"]) == ["Farm Boy", "Unassigned"]

    # Bought, then added again: it no longer goes to the store that's gone.
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE item = 'paper towels'")
    conn.commit()
    conn.close()
    tools.add_grocery_item("paper towels", "2")
    assert _lines()["paper towels"]["store"] == ""


def test_un_picking_a_store_on_shop_does_the_same(signed_in):
    _costco_and_farm_boy()
    res = signed_in.post("/api/memory/edit", json={"field": "usual_stores", "value": ["Farm Boy"]})
    assert res.status_code == 200, res.text
    assert _lines()["paper towels"]["store"] == ""
    assert _prefs() == {"milk": "Farm Boy"}


def test_a_store_that_was_never_a_usual_one_is_not_forgotten():
    # Tagged ad hoc ("we got it at the market") without ever being picked:
    # removing some other store must not throw that filing away.
    tools.add_usual_stores(["Farm Boy"])
    tools.set_item_store("honey", "Market")
    tools.add_grocery_item("honey", "1")
    tools.delete_preference("usual_stores", "Market")
    assert _lines()["honey"]["store"] == "Market"
    assert _prefs()["honey"] == "Market"


def _every_day(closed_on_holidays=True):
    return {"days": {d: ["08:00", "21:00"] for d in _stores.HOURS_DAYS}, "closed_on_holidays": closed_on_holidays}


def test_halloween_and_mothers_day_shut_no_shop():
    tools.add_usual_stores(["Farm Boy"])
    tools.set_store_hours("Farm Boy", _every_day())
    for d in (datetime.date(2026, 10, 31), datetime.date(2027, 5, 9), datetime.date(2027, 6, 20)):
        assert tools.holiday_on(d.isoformat()) is not None, "still a holiday on the plan"
        assert _stores.open_window("Farm Boy", d)["opens"] == datetime.datetime.combine(d, datetime.time(8, 0))
    # The evening before Halloween (a Saturday) opens tomorrow, not Sunday.
    assert _stores.open_now_said("Farm Boy", datetime.datetime(2026, 10, 30, 22, 0)) == "closed — opens 8:00 tomorrow"
    # A real day off still closes it.
    assert _stores.open_window("Farm Boy", datetime.date(2026, 12, 25)) == {"closed": True, "holiday": True}
    assert _stores.open_window("Farm Boy", datetime.date(2026, 10, 12)) == {"closed": True, "holiday": True}
