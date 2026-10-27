"""
The seams between the nine inventory branches merged on 2026-10-27
(merge-inventory-2026-10-27). Each branch was right on its own; these are
the places two of them disagreed once they met. Every test here fails at
3402d1b (the merge before these fixes) and passes after.

1. Today's going-off line reads B2/B3's dish-stamped use-up tags.
2. Today's going-off line never names a staple or a spice (Kitchen hides them).
3. C3 "Almost gone" beside a B4 set-aside line puts THAT line back, not a second.
4. A2's Undo finds its twin by the Kitchen's own "same food" key.
5. A2's Undo leaves alone a row the household took out after dinner.
6. Today's going-off card is re-read after every Kitchen-tab write.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from conftest import household_today
from app import tools
from app.db import get_conn
from app.tools import already_home, use_up, whats_home

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _d(n):
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _approved_plan(start=-1, days=6):
    plan = tools.create_weekly_plan(_d(start), day_count=days)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    return plan


def _row_id(item):
    conn = get_conn()
    r = conn.execute("SELECT id FROM inventory_items WHERE item = ? ORDER BY id DESC LIMIT 1", (item,)).fetchone()
    conn.close()
    return r["id"]


def _rows_like(pattern):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, item, quantity, location FROM inventory_items WHERE LOWER(item) LIKE ? ORDER BY id",
        (pattern,))]
    conn.close()
    return rows


def _fn(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    nxt = re.search(r"\n  (?:async )?function \w+\(|\n  var \w+ = ", SHELL_JS[start + 10:])
    return SHELL_JS[start: start + 10 + (nxt.start() if nxt else len(SHELL_JS))]


# ---- 1. B5 reads B2/B3's dish-stamped use_up tag -----------------------------

def test_a_b3_side_tag_is_a_plan_for_the_food_on_today():
    plan = _approved_plan()
    tools.add_recipe("Beef Tacos", ingredients=[{"item": "Ground beef", "qty": "1 lb", "category": "meat"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    tools.update_inventory("Cilantro", "add", quantity="1 bunch", expiration_date=_d(1),
                           category="produce", location="fridge")
    cil = _row_id("Cilantro")
    # Exactly what use_up.resolve(..., "side") writes onto tomorrow's dinner.
    derived = {"use_up": [{"id": cil, "item": "Cilantro", "dish": "Beef Tacos"}]}
    tools.plan_meal(_d(1), "Beef Tacos", slot="dinner", weekly_plan_id=plan, derived_from=derived)
    conn = get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan,)).fetchone()
    conn.close()
    assert use_up.tag_for(row["derived_from_json"], "Beef Tacos") == "Uses the cilantro"
    assert tools.today_going_off()["line"] is None


def test_a_stale_tag_from_another_dish_is_no_plan():
    plan = _approved_plan()
    tools.add_recipe("Beef Tacos", ingredients=[{"item": "Ground beef", "qty": "1 lb", "category": "meat"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    tools.update_inventory("Cilantro", "add", quantity="1 bunch", expiration_date=_d(1),
                           category="produce", location="fridge")
    cil = _row_id("Cilantro")
    derived = {"use_up": [{"id": cil, "item": "Cilantro", "dish": "Cilantro Rice"}]}
    tools.plan_meal(_d(1), "Beef Tacos", slot="dinner", weekly_plan_id=plan, derived_from=derived)
    line = tools.today_going_off()["line"]
    assert line is not None and line["item_id"] == cil


# ---- 2. B5 never names a staple the Kitchen hides ----------------------------

def test_today_line_never_names_a_staple_kitchen_hides():
    tools.add_staple("Milk")
    tools.update_inventory("Whole milk", "add", quantity="1 L", expiration_date=_d(0),
                           category="dairy", location="fridge")
    mid = _row_id("Whole milk")
    home = tools.get_whats_home()
    shown = {v["id"] for loc in home["locations"].values() for v in loc}
    assert mid not in shown
    line = tools.today_going_off()["line"]
    assert line is None or line["item_id"] != mid, line


# ---- 3. C3 "Almost gone" vs a B4 set-aside line ------------------------------

def _beef_set_aside():
    for name in ("Emily", "Vineeth"):
        if not any(m["name"] == name for m in tools.list_members()):
            tools.add_member(name)
    tools.add_recipe("Beef Tacos", ingredients=[{"item": "Ground beef", "qty": "1 lb", "category": "meat"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2, instructions=["Cook."])
    tools.update_inventory("Ground beef", "add", quantity="2 lbs", location="fridge", category="meat")
    plan = tools.create_weekly_plan(_d(0), day_count=7)["weekly_plan_id"]
    tools.plan_meal(_d(1), "Beef Tacos", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    assert [r["item"] for r in tools.already_home_lines()] == ["Ground beef"]
    return plan, _row_id("Ground beef")


def _beef_lines():
    conn = get_conn()
    lines = [dict(r) for r in conn.execute(
        "SELECT id, item, status, removed_by, source_weekly_plan_id FROM grocery_items "
        "WHERE LOWER(item) = 'ground beef' ORDER BY id")]
    conn.close()
    return lines


def test_almost_gone_puts_the_set_aside_line_back_not_a_second_one():
    plan, beef = _beef_set_aside()
    aside_id = tools.already_home_lines()[0]["id"]
    out = whats_home.set_amount_left(beef, "almost_gone")
    assert out["added_to_list"] and out["grocery_item_id"] == aside_id
    already_home.leave_off_what_is_home(plan)  # any later ingest
    lines = _beef_lines()
    assert [(l["id"], l["status"]) for l in lines] == [(aside_id, "needed")], lines
    assert tools.already_home_lines() == []


def test_undo_of_almost_gone_sets_the_line_aside_again():
    plan, beef = _beef_set_aside()
    aside_id = tools.already_home_lines()[0]["id"]
    out = whats_home.set_amount_left(beef, "almost_gone")
    back = whats_home.restore_item(out["undo"])
    assert back["undone"]
    lines = _beef_lines()
    assert [(l["id"], l["status"], l["removed_by"]) for l in lines] == [(aside_id, "removed", "kitchen")], lines
    # The put-back is forgotten too, so the next pass still judges it.
    already_home.leave_off_what_is_home(plan)
    assert [r["id"] for r in tools.already_home_lines()] == [aside_id]


def test_an_almost_gone_row_covers_nothing_for_the_already_home_pass():
    plan, beef = _beef_set_aside()
    aside_id = tools.already_home_lines()[0]["id"]
    # Put back by hand ("add it back"), then forget the put-back so the
    # pass judges the line on the kitchen alone.
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET amount_left = 'almost_gone', amount_left_qty = quantity WHERE id = ?",
                 (beef,))
    conn.execute("UPDATE grocery_items SET status = 'needed', removed_by = '', removed_at = NULL WHERE id = ?",
                 (aside_id,))
    conn.commit()
    conn.close()
    already_home.leave_off_what_is_home(plan)
    assert tools.already_home_lines() == []


# ---- 4. A2 Undo vs a C2 add under a same-food name ---------------------------

def _eat_lemons():
    tools.add_recipe("Lemon water", ingredients=[{"item": "Lemons", "qty": "2"}], default_servings=1)
    tools.update_inventory("Lemons", "add", quantity="2", category="produce", location="fridge")
    entry = tools.plan_meal(_d(0), "Lemon water", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")
    assert _rows_like("lemon%") == [], "premise: used up and deleted"
    return entry


def test_eaten_undo_after_a_kitchen_add_goes_onto_the_same_food():
    entry = _eat_lemons()
    tools.add_kitchen_items([{"item": "Lemon", "quantity": "3", "location": "fridge"}], "say")
    tools.undo_eaten_meal(entry)
    rows = _rows_like("lemon%")
    assert len(rows) == 1, rows
    assert rows[0]["item"] == "Lemon" and rows[0]["quantity"] == "5", rows


# ---- 5. C3 "Used it up" then A2 Undo -----------------------------------------

def test_used_it_up_after_dinner_survives_the_eaten_undo():
    tools.add_recipe("Rice bowl", ingredients=[{"item": "Jasmine rice", "qty": "1 cup"}], default_servings=1)
    tools.update_inventory("Jasmine rice", "add", quantity="3 cup", category="pantry", location="pantry")
    rid = _row_id("Jasmine rice")
    entry = tools.plan_meal(_d(0), "Rice bowl", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")
    used = whats_home.used_up_item(rid)
    tools.undo_eaten_meal(entry)
    assert _rows_like("%rice%") == []
    # C3's own Undo still works: the household's answer was theirs to undo.
    assert whats_home.restore_item(used["undo"])["undone"] is True
    assert [r["quantity"] for r in _rows_like("%rice%")] == ["2 cups"]


def test_eaten_undo_still_brings_back_a_row_dinner_used_up():
    entry = _eat_lemons()
    tools.undo_eaten_meal(entry)
    assert [(r["item"], r["quantity"]) for r in _rows_like("lemon%")] == [("Lemons", "2")]


def test_undo_of_the_first_of_two_dinners_puts_back_only_its_share():
    """Re-verify of 617241a: a row a later DINNER used up is not the
    household's answer — Undo of the first dinner gives back what it took."""
    tools.add_recipe("Rice bowl", ingredients=[{"item": "Jasmine rice", "qty": "1 cup"}], default_servings=1)
    tools.add_recipe("Fried rice", ingredients=[{"item": "Jasmine rice", "qty": "2 cup"}], default_servings=1)
    tools.update_inventory("Jasmine rice", "add", quantity="3 cup", category="pantry", location="pantry")
    first = tools.plan_meal(_d(0), "Rice bowl", slot="dinner")["entry_id"]
    tools.check_off_meal(first, "done")  # 2 cups left
    second = tools.plan_meal(_d(0), "Fried rice", slot="lunch")["entry_id"]
    tools.check_off_meal(second, "done")  # used up, deleted by a tick
    assert _rows_like("%rice%") == []
    tools.undo_eaten_meal(first)
    rows = _rows_like("%rice%")
    assert len(rows) == 1 and rows[0]["quantity"].startswith("1 cup"), rows
    tools.undo_eaten_meal(second)
    rows = _rows_like("%rice%")
    assert len(rows) == 1 and rows[0]["quantity"].startswith("3 cup"), rows


def test_a_row_saved_with_no_location_comes_back_on_its_own_shelf():
    """A blank-location row is read on its category's shelf: its twin is
    never a same-name row in the freezer."""
    tools.add_recipe("Thigh dinner", ingredients=[{"item": "Chicken thighs", "qty": "2 lbs"}], default_servings=2)
    tools.update_inventory("Chicken thighs", "add", quantity="2 lbs", category="meat/seafood", location="fridge")
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET location = ''")
    conn.commit()
    conn.close()
    entry = tools.plan_meal(_d(0), "Thigh dinner", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")
    assert _rows_like("chicken%") == [], "premise: used up and deleted"
    tools.add_kitchen_items([{"item": "Chicken thighs", "quantity": "3 lbs", "location": "freezer"}], "freezer")
    tools.undo_eaten_meal(entry)
    rows = {(r["location"], r["quantity"]) for r in _rows_like("chicken%")}
    assert rows == {("", "2 lbs"), ("freezer", "3 lbs")}, rows


# ---- 6. Today's going-off card follows the Kitchen tab -----------------------

def test_today_going_off_is_reread_after_every_kitchen_tab_write():
    assert "refreshGoingOff()" in _fn("homeItemAct")
    assert "refreshGoingOff()" in _fn("homeFreeze")
    assert "refreshGoingOff()" in _fn("homeCheckTap")
    assert "refreshGoingOff()" in _fn("refreshHomeAndGoingOff")
    assert "refreshHomeAndGoingOff()" in _fn("homeToastWithUndo")
    add = _fn("openKitchenAdd")
    assert add.count("refreshGoingOff()") == 2  # the save and its Undo
    stale = _fn("refreshStaleTabsFromActions")
    home_once = stale[stale.index("function refreshHomeOnce("):]
    home_once = home_once[:home_once.index("\n    }") + 6]
    assert "refreshGoingOff()" in home_once
