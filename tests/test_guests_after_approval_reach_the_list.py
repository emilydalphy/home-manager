"""
A table that changes size after the week is approved changes the list.

Defect hunt, 2026-10-22: "my parents are coming Thursday" after approval
(chat's set_guest_count, whose own description promises "portions and
grocery quantities both scale") moved the Cook card to "Cooking for 9" and
left the list at three people's beef — the ingest read attendance only at
approval. attendance.rebuy_for_new_headcount re-buys the recipe-week.
"""
import datetime

from app import tools
from app.db import get_conn
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


def _list(status="needed") -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status=status)}


def test_guests_on_a_reheat_night_resize_the_cook_nights_batch_and_come_off_again():
    _household()
    _chain()
    assert _list() == {"beef": "2 lbs", "lettuce": "2 heads"}

    tools.set_guest_count(THU, "dinner", 3)
    assert _list() == {"beef": "3 lbs", "lettuce": "3 heads"}, "3 Tuesday + 6 Thursday = 9 from a recipe for 3"

    tools.set_guest_count(THU, "dinner", 0)
    assert _list() == {"beef": "2 lbs", "lettuce": "2 heads"}


def test_one_person_out_of_an_ordinary_approved_dinner_shrinks_it_and_back_restores_it():
    _household()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(WED, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Alex")
    assert _list()["beef"] == "1 lb"

    tools.set_member_attendance(WED, "dinner", "Rae", present=False)
    assert _list()["beef"] == "10.75 oz", "two of three eat it: 2/3 lb"

    tools.set_member_attendance(WED, "dinner", "Rae", present=True)
    assert _list()["beef"] == "1 lb"


def test_a_line_already_bought_is_left_alone_not_bought_again():
    _household()
    _chain()
    beef = next(i for i in tools.list_grocery_list() if i["item"] == "beef")
    tools.mark_grocery_item(beef["id"], "purchased")

    tools.set_guest_count(THU, "dinner", 3)

    assert "beef" not in _list(), "no second beef line beside the bag"
    assert _list()["lettuce"] == "2 heads"


def test_a_draft_buys_nothing():
    _household()
    _chain(approve=False)
    tools.set_guest_count(THU, "dinner", 3)
    assert _list() == {}


def test_a_batch_already_cooked_buys_nothing_more():
    _household()
    plan_id = _chain()
    tue = next(m for m in tools.get_cooker_view(plan_id)["meals"] if m["date"] == TUE)
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_at = datetime('now') WHERE id = ?", (tue["entry_id"],))
    conn.commit()
    conn.close()

    tools.set_guest_count(THU, "dinner", 3)
    assert _list() == {"beef": "2 lbs", "lettuce": "2 heads"}, "Tuesday's pot is already cooked"
