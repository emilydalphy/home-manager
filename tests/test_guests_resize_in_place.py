"""
A table that changes size after the week is approved changes the list —
resized IN PLACE (attendance.resize_for_new_headcount, 2026-10-22).

"My parents are coming Thursday" after approval moved the Cook card to
"Cooking for 9" and left the list at three people's beef: the ingest read
attendance only at approval. A first fix unbought and rebought the
recipe-week and was pulled: every tap gave every plan line a new id, and a
line the household had dropped came back. Here the ledger's shares are
rewritten on the rows already there and each line is re-read from them.
"""
import datetime

from app import tools
from app.db import get_conn
from app.tools import defrost as _defrost
from conftest import household_today, on_the_weeks_monday


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


pytestmark = on_the_weeks_monday(_monday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


TUE, WED, THU = _day(1), _day(2), _day(3)


def _household():
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)
    tools.add_recipe("Bulgogi Wraps", default_servings=3, ingredients=[
        {"item": "beef", "qty": "1 lb"}, {"item": "lettuce", "qty": "1 head"}])


def _chain(approve=True) -> int:
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(THU, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{TUE}:dinner"})
    tools.repair_leftover_chains(plan_id)
    if approve:
        tools.approve_weekly_plan(plan_id, "Alex")
    return plan_id


def _one_dinner() -> int:
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(WED, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Alex")
    return plan_id


def _list(status="needed") -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status=status)}


def _rows() -> list[tuple]:
    conn = get_conn()
    rows = [tuple(r) for r in conn.execute(
        "SELECT id, item, quantity, status, removed_by, excluded_from_list FROM grocery_items ORDER BY id")]
    conn.close()
    return rows


def _ledger() -> list[tuple]:
    conn = get_conn()
    rows = [tuple(r) for r in conn.execute(
        "SELECT id, meal_plan_entry_id, grocery_item_id, quantity FROM meal_plan_grocery_links ORDER BY id")]
    conn.close()
    return rows


def _line(item: str) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT id, quantity FROM grocery_items WHERE item = ?", (item,)).fetchone()
    conn.close()
    return dict(row)


def test_guests_on_a_reheat_night_grow_the_cook_nights_batch_in_place_and_come_back_exactly():
    _household()
    _chain()
    rows, ledger = _rows(), _ledger()
    assert _list() == {"beef": "2 lbs", "lettuce": "2 heads"}

    tools.set_guest_count(THU, "dinner", 3)
    assert _list() == {"beef": "3 lbs", "lettuce": "3 heads"}, "3 Tuesday + 6 Thursday = 9 from a recipe for 3"
    assert [r[0] for r in _rows()] == [r[0] for r in rows], "the same lines, resized — never new ids"
    assert [r[0] for r in _ledger()] == [r[0] for r in ledger]

    tools.set_guest_count(THU, "dinner", 0)
    assert _rows() == rows and _ledger() == ledger


def test_one_person_out_of_an_ordinary_dinner_shrinks_it_and_back_restores_it():
    _household()
    _one_dinner()
    rows = _rows()
    assert _list()["beef"] == "1 lb"

    tools.set_member_attendance(WED, "dinner", "Rae", present=False)
    assert _list()["beef"] == "10.75 oz", "two of three eat it: 2/3 lb"

    tools.set_member_attendance(WED, "dinner", "Rae", present=True)
    assert _rows() == rows


def test_a_pre_shop_drop_stays_off_through_guests_on_and_off_and_its_undo_does_not_double():
    _household()
    _one_dinner()
    beef, lettuce = _line("beef")["id"], _line("lettuce")["id"]

    tools.set_guest_count(WED, "dinner", 3)
    tools.drop_grocery_item_pre_shop(lettuce, author="Alex")
    tools.set_guest_count(WED, "dinner", 0)
    assert "lettuce" not in _list(), "the household said it has it"
    tools.set_guest_count(WED, "dinner", 3)
    assert "lettuce" not in _list()

    tools.undo_pre_shop_drop(lettuce)
    assert _list() == {"beef": "2 lbs", "lettuce": "2 heads"}, "one line, sized for six — not two"
    tools.set_guest_count(WED, "dinner", 0)
    assert _list() == {"beef": "1 lb", "lettuce": "1 head"}
    assert [r[0] for r in _rows()] == [beef, lettuce]


def test_a_line_removed_from_the_list_stays_off_and_its_undo_brings_back_the_resized_row():
    _household()
    _one_dinner()
    lettuce = _line("lettuce")["id"]
    tools.remove_grocery_item(lettuce)

    tools.set_guest_count(WED, "dinner", 3)
    assert "lettuce" not in _list()
    assert tools.undo_remove_grocery_item(lettuce)["restored"]
    assert _list() == {"beef": "2 lbs", "lettuce": "2 heads"}
    assert len(_rows()) == 2


def test_an_excluded_line_stays_hidden_and_a_freezer_set_aside_stays_off():
    _household()
    _one_dinner()
    beef, lettuce = _line("beef")["id"], _line("lettuce")["id"]
    tools.exclude_grocery_item(beef)            # "getting it at the butcher"
    tools.drop_grocery_item_pre_shop(lettuce, author=_defrost.FREEZER_REMOVED_BY)

    tools.set_guest_count(WED, "dinner", 3)
    tools.set_guest_count(WED, "dinner", 5)
    assert _list() == {}
    assert len(_rows()) == 2, "nothing new appeared beside them"
    assert _line("beef")["quantity"] == "2.75 lbs", "the butcher's amount follows the table"
    tools.set_guest_count(WED, "dinner", 0)
    tools.include_grocery_item(beef)
    tools.undo_pre_shop_drop(lettuce)
    assert _list() == {"beef": "1 lb", "lettuce": "1 head"}


def test_a_standing_want_under_a_plan_line_never_creeps():
    _household()
    tools.add_grocery_item("beef", "1 lb", category="meat/seafood")
    _one_dinner()
    start = _list()
    assert start["beef"] == "2 lbs"
    tools.set_guest_count(WED, "dinner", 3)
    assert _list()["beef"] == "3 lbs", "the household's own pound plus two for six"
    for guests in (0, 3, 0, 5, 1, 0):
        tools.set_guest_count(WED, "dinner", guests)
    assert _list() == start


def test_a_draft_buys_nothing():
    _household()
    _chain(approve=False)
    tools.set_guest_count(THU, "dinner", 3)
    assert _list() == {}


def test_a_batch_already_cooked_is_left_alone():
    _household()
    plan_id = _chain()
    tue = next(m for m in tools.get_cooker_view(plan_id)["meals"] if m["date"] == TUE)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_at = datetime('now') WHERE id = ?", (tue["entry_id"],))
    conn.commit()
    conn.close()

    tools.set_guest_count(THU, "dinner", 3)
    assert _list() == {"beef": "2 lbs", "lettuce": "2 heads"}, "Tuesday's pot is already cooked"


def test_a_night_gone_by_is_left_alone(frozen_today):
    _household()
    _one_dinner()
    rows = _rows()
    frozen_today(datetime.datetime.fromisoformat(THU + "T12:00:00"))
    tools.set_guest_count(WED, "dinner", 3)
    assert _rows() == rows


def test_a_line_in_the_cart_or_bought_holds_its_whole_recipe_week():
    _household()
    _chain()
    beef = _line("beef")["id"]
    tools.mark_grocery_item(beef, "in_cart")
    tools.set_guest_count(THU, "dinner", 3)
    assert _list() == {"lettuce": "2 heads"}
    tools.mark_grocery_item(beef, "purchased")
    tools.set_guest_count(THU, "dinner", 5)
    assert _list() == {"lettuce": "2 heads"}
    assert len(_rows()) == 2, "no second beef beside the bag"


def test_everyone_out_is_the_away_conversion_not_a_resize():
    _household()
    _one_dinner()
    for name in ("Alex", "Sam", "Rae"):
        tools.set_member_attendance(WED, "dinner", name, present=False)
    assert _list() == {}, "nobody home: nothing bought, as before"
