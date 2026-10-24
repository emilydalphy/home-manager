"""
Changing a batch after the shop buys only what is still missing.

_rescale_leftover_source_grocery used to reverse the whole recipe-week —
deleting the ledger rows on lines the shopper had already ticked — and buy
the whole of it again: Turkey Chili batched Monday for Wednesday, shopped,
Monday cooked, "cook the chili fresh on Wednesday" put 2 lbs of turkey and
two cans of beans back on the list and said "Your list has changed to
match." (Loop Board, 2026-10-24.)
"""
from __future__ import annotations

import datetime

from conftest import household_today, on_the_weeks_monday
from app import db, tools
from app.tools import batch_undo, weekly_plan

ON_MONDAY = on_the_weeks_monday()


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


WEEK = _monday().isoformat()
MON, WED, FRI = _day(0), _day(2), _day(4)


def _chili_week(days=(MON, WED), hand=None, recipe=None):
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
    name, ingredients = recipe or ("Turkey Chili", [
        {"item": "Ground turkey", "qty": "1 lb", "category": "meat"},
        {"item": "Black beans", "qty": "1 can", "category": "pantry"},
    ])
    tools.add_recipe(name, ingredients=ingredients, default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=30, instructions=["Cook."])
    for item, qty in (hand or []):
        tools.add_grocery_item(item, quantity=qty, category="other")
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {d: tools.plan_meal(d, name, slot="dinner", weekly_plan_id=plan_id)["entry_id"] for d in days}
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    first, *rest = days
    assert isinstance(tools.set_cook_ahead(ids[first], [ids[d] for d in rest]), dict)
    return plan_id, ids


def _rows():
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, item, quantity, status, source_weekly_plan_id FROM grocery_items ORDER BY id").fetchall()]
    conn.close()
    return rows


def _needed():
    return {r["item"]: r["quantity"] for r in _rows() if r["status"] == "needed"}


def _tick_all():
    for r in _rows():
        if r["status"] == "needed":
            tools.mark_grocery_item(r["id"], "purchased")
    # Ticking also shelves the food in the inventory beta, whose "already
    # have" would skip the re-buy and hide the bug on its own. The list has
    # to be right without it: most households don't keep the shelf.
    conn = db.get_conn()
    conn.execute("DELETE FROM inventory_items")
    conn.commit()
    conn.close()


def _line(item, status="needed"):
    return next(r for r in _rows() if r["item"].lower() == item.lower() and r["status"] == status)


@ON_MONDAY
def test_unbatching_after_the_shop_and_the_cook_adds_nothing():
    _plan_id, ids = _chili_week()
    _tick_all()
    tools.check_off_meal(ids[MON])
    before = _rows()
    result = batch_undo.unbatch("chili")
    assert result["status"] == "unbatched"
    assert _needed() == {}
    assert _rows() == before
    assert result["list_changed"] is False
    assert "list has changed" not in result["said"]


@ON_MONDAY
def test_a_partial_shop_leaves_exactly_the_difference():
    _plan_id, ids = _chili_week()
    turkey = _line("Ground turkey")
    assert turkey["quantity"] == "2 lbs"
    tools.update_grocery_item(turkey["id"], quantity="1 lb")
    _tick_all()
    result = batch_undo.unbatch("chili")
    assert _needed() == {"Ground turkey": "1 lb"}
    assert result["list_changed"] is True
    # And again: the shortfall is not bought twice.
    batch_undo.rebatch(result["undo"])
    batch_undo.unbatch("chili")
    assert _needed() == {"Ground turkey": "1 lb"}


@ON_MONDAY
def test_clearing_the_reheat_night_after_a_partial_shop_buys_nothing_more():
    _plan_id, ids = _chili_week()
    turkey = _line("Ground turkey")
    tools.update_grocery_item(turkey["id"], quantity="1 lb")
    _tick_all()
    tools.clear_plan_slot(_plan_id, WED, "dinner")
    # One pound bought, one night of chili left wanting one pound.
    assert _needed() == {}


@ON_MONDAY
def test_a_partial_shop_then_clearing_the_reheat_night_with_more_short():
    # Edited all the way down to half: half a pound short of Monday alone.
    _plan_id, ids = _chili_week()
    turkey = _line("Ground turkey")
    tools.update_grocery_item(turkey["id"], quantity="8 oz")
    _tick_all()
    tools.clear_plan_slot(_plan_id, WED, "dinner")
    assert _needed() == {"Ground turkey": "8 oz"}


@ON_MONDAY
def test_a_line_never_ticked_still_shrinks_with_the_batch():
    _plan_id, ids = _chili_week()
    assert _needed() == {"Ground turkey": "2 lbs", "Black beans": "2 cans"}
    tools.clear_plan_slot(_plan_id, WED, "dinner")
    assert _needed() == {"Ground turkey": "1 lb", "Black beans": "1 can"}


@ON_MONDAY
def test_unbatching_an_unshopped_week_keeps_the_list_as_it_was():
    _plan_id, ids = _chili_week()
    batch_undo.unbatch("chili")
    assert _needed() == {"Ground turkey": "2 lbs", "Black beans": "2 cans"}


@ON_MONDAY
def test_a_hand_added_line_does_not_climb_over_repeated_unbatch_cycles():
    _plan_id, ids = _chili_week(hand=[("ground turkey", "1 lb")])
    start = _needed()
    assert start["ground turkey"] == "3 lbs"
    for _ in range(4):
        result = batch_undo.unbatch("chili")
        batch_undo.rebatch(result["undo"])
        assert _needed() == start


@ON_MONDAY
def test_a_hand_added_line_beside_a_ticked_one_keeps_its_own_amount():
    # Shopped (turkey edited to one pound and ticked), THEN the household
    # adds a pound of its own. Unbatching buys the missing pound onto that
    # line once — and the cycle never walks it further.
    _plan_id, ids = _chili_week()
    turkey = _line("Ground turkey")
    tools.update_grocery_item(turkey["id"], quantity="1 lb")
    _tick_all()
    tools.add_grocery_item("ground turkey", quantity="1 lb", category="meat")
    assert _line("ground turkey")["source_weekly_plan_id"] is None
    for _ in range(4):
        result = batch_undo.unbatch("chili")
        needed = _needed()
        assert needed == {"ground turkey": "2 lbs"}, needed
        batch_undo.rebatch(result["undo"])


@ON_MONDAY
def test_a_fractional_onion_beside_a_hand_added_one_does_not_climb():
    recipe = ("Onion Soup", [{"item": "Onion", "qty": "0.75", "category": "produce"}])
    _plan_id, ids = _chili_week(days=(MON, WED, FRI), hand=[("onion", "1")], recipe=recipe)
    start = _needed()
    assert start == {"onion": "4"}, start
    for _ in range(4):
        result = batch_undo.unbatch("soup")
        assert _needed() == start
        batch_undo.rebatch(result["undo"])


@ON_MONDAY
def test_a_fractional_onion_partly_shopped_beside_a_hand_added_one():
    recipe = ("Onion Soup", [{"item": "Onion", "qty": "0.75", "category": "produce"}])
    _plan_id, ids = _chili_week(days=(MON, WED, FRI), recipe=recipe)
    onion = _line("Onion")
    assert onion["quantity"] == "3"
    tools.update_grocery_item(onion["id"], quantity="2")
    _tick_all()
    tools.add_grocery_item("onion", quantity="1", category="produce")
    for _ in range(4):
        result = batch_undo.unbatch("soup")
        # 2.25 wanted, 2 bought: one more onion, on top of the household's own.
        assert _needed() == {"onion": "2"}, _needed()
        batch_undo.rebatch(result["undo"])
