"""
A meal moved to another day of an approved week is bought for the table on
the day it is on now (2026-10-24, overnight plan hunt).

Move and the Plan tab's nights swap re-date rows in place and keep their
grocery links — "the grocery list is not touched". But a night's amounts
are sized for that night's table: a dish bought for three that traded onto
Thursday's six-person dinner kept three people's shopping while Cook said
"6 salmon fillets", and the dish it traded with kept six people's. Both
doors now resize the moved meals after the move commits
(attendance.resize_after_redate, on resize_for_new_headcount), and Undo
puts the list back exactly.
"""
import datetime

from app import tools
from app.db import get_conn
from app.tools import meal_move
from conftest import household_today, on_the_weeks_monday


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


pytestmark = on_the_weeks_monday(_monday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


WED, THU = _day(2), _day(3)


def _week(approve=True) -> tuple[int, dict]:
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)
    tools.add_recipe("Bulgogi Wraps", default_servings=3, ingredients=[{"item": "beef", "qty": "1 lb"}])
    tools.add_recipe("Salmon Bowls", default_servings=3, ingredients=[{"item": "salmon fillets", "qty": "3"}])
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(WED, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(THU, "Salmon Bowls", slot="dinner", weekly_plan_id=plan_id)
    tools.set_guest_count(THU, "dinner", 3)  # Thursday's table is six
    if approve:
        tools.approve_weekly_plan(plan_id, "Alex")
    return plan_id, _dinner_ids(plan_id)


def _dinner_ids(plan_id: int) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, date FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner'",
        (plan_id,)).fetchall()
    conn.close()
    return {r["date"]: r["id"] for r in rows}


def _list() -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def _rows() -> list[tuple]:
    conn = get_conn()
    rows = [tuple(r) for r in conn.execute(
        "SELECT id, item, quantity, status FROM grocery_items ORDER BY id")]
    conn.close()
    return rows


def _ledger() -> list[tuple]:
    conn = get_conn()
    rows = [tuple(r) for r in conn.execute(
        "SELECT id, meal_plan_entry_id, grocery_item_id, quantity FROM meal_plan_grocery_links ORDER BY id")]
    conn.close()
    return rows


def test_a_move_onto_a_bigger_table_buys_for_it_and_undo_puts_the_list_back():
    plan_id, ids = _week()
    rows, ledger = _rows(), _ledger()
    assert _list() == {"beef": "1 lb", "salmon fillets": "6"}

    out = meal_move.move_meal(plan_id, ids[WED], THU)
    assert out["status"] == "moved"
    # FAILS ON MAIN: {"beef": "1 lb", "salmon fillets": "6"} — the bulgogi
    # was bought for three and is now cooked for six.
    assert _list() == {"beef": "2 lbs", "salmon fillets": "3"}
    assert [r[0] for r in _rows()] == [r[0] for r in rows], "the same lines, resized — never new ids"

    meal_move.undo_meal_move(plan_id, out["move_id"])
    assert _rows() == rows and _ledger() == ledger


def test_the_plan_tabs_nights_swap_follows_the_tables_too_and_its_undo_restores():
    plan_id, _ = _week()
    rows, ledger = _rows(), _ledger()

    assert tools.swap_dinner_nights(plan_id, WED, THU)["status"] == "swapped"
    assert _list() == {"beef": "2 lbs", "salmon fillets": "3"}

    assert tools.undo_dinner_nights_swap(plan_id, WED, THU)["status"] == "restored"
    assert _rows() == rows and _ledger() == ledger


def test_a_bought_line_is_left_as_it_was():
    """A recipe-week with something already in the cart or bought is not
    resized (resize_for_new_headcount's rule) — the move still happens."""
    plan_id, ids = _week()
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE item = 'beef'")
    conn.commit()
    conn.close()

    assert meal_move.move_meal(plan_id, ids[WED], THU)["status"] == "moved"
    assert _list() == {"salmon fillets": "3"}, "the salmon follows its new table"
    assert {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="purchased")} == {"beef": "1 lb"}


def test_a_move_in_a_draft_writes_nothing_to_the_list():
    plan_id, ids = _week(approve=False)
    assert meal_move.move_meal(plan_id, ids[WED], THU)["status"] == "moved"
    assert _rows() == [] and _ledger() == []
