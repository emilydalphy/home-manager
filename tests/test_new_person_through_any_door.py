"""
A person first named in chat is ADDED, the way add_member adds them,
whichever member write named them (defect hunt 2026-10-28).

set_member_dietary_restrictions ("Linda's staying with us, she's allergic to
shellfish") and set_member_age_group ("add Linda as an adult") both
get-or-create by name. They used to insert the row themselves, which skipped
what adding a person does:

  - the stored slots' away needs were never re-derived, so a dinner everyone
    else is out of stayed "nobody's home, nothing planned" with Linda at the
    table (attendance.reconcile_membership);
  - the restrictions write also skipped list_follows_membership, so an
    approved week's shopping stayed bought for two while Cook counted three.
"""
import datetime

from app import tools
from app.db import get_conn
from conftest import household_today, on_the_weeks_monday


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


pytestmark = on_the_weeks_monday(_monday())

WED = (_monday() + datetime.timedelta(days=2)).isoformat()
THU = (_monday() + datetime.timedelta(days=3)).isoformat()


def _approved_week():
    for n in ("Alex", "Sam"):
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")
    tools.add_recipe("Bulgogi Wraps", default_servings=2, ingredients=[
        {"item": "beef", "qty": "1 lb"}, {"item": "lettuce", "qty": "1 head"}])
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(WED, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Alex")


def _list() -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def _thursday_needs() -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT need FROM slot_needs WHERE date = ? AND slot = 'dinner'", (THU,)
    ).fetchall()
    conn.close()
    return [r["need"] for r in rows]


def _everyone_out_thursday():
    for n in ("Alex", "Sam"):
        tools.set_member_attendance(THU, "dinner", n, present=False)
    assert _thursday_needs() == ["away"]


def test_a_restriction_about_someone_new_buys_for_them():
    _approved_week()
    assert _list() == {"beef": "1 lb", "lettuce": "1 head"}

    tools.set_member_dietary_restrictions("Linda", ["shellfish allergy"])

    assert tools.get_slot_attendance(WED, "dinner")["headcount"] == 3
    assert _list() == {"beef": "1.5 lbs", "lettuce": "2 heads"}, "three at the table, bought for three"
    assert [m["dietary_restrictions"] for m in tools.list_members() if m["name"] == "Linda"] == [["shellfish allergy"]]


def test_a_restriction_about_someone_new_clears_a_nobody_home_night():
    _approved_week()
    _everyone_out_thursday()

    tools.set_member_dietary_restrictions("Linda", ["shellfish allergy"])

    att = tools.get_slot_attendance(THU, "dinner")
    assert att["present_names"] == ["Linda"] and not att["nobody_home"]
    assert _thursday_needs() == [], "Linda is home: the night is no longer 'away'"


def test_an_age_group_about_someone_new_clears_a_nobody_home_night():
    _approved_week()
    _everyone_out_thursday()

    tools.set_member_age_group("Linda", "adult")

    assert _thursday_needs() == []
    assert _list() == {"beef": "1.5 lbs", "lettuce": "2 heads"}


def test_someone_already_here_is_not_added_again():
    _approved_week()
    tools.set_member_dietary_restrictions("alex", ["peanut allergy"])
    tools.set_member_age_group("SAM", "adult")

    assert [m["name"] for m in tools.list_members()] == ["Alex", "Sam"]
    assert _list() == {"beef": "1 lb", "lettuce": "1 head"}, "nobody new at the table"


# ---------- the morning text's name, chat side (same night) ----------

def test_chat_morning_text_finds_an_adult_named_without_her_accent():
    from app.tools import digest
    tools.add_member("Zoé")
    tools.set_member_age_group("Zoé", "adult")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "adult")

    result = digest.set_morning_text(phone="416-555-0100", on=True, name="zoe")

    assert result["name"] == "Zoé" and result["on"] is True
    sam = next(a for a in digest.get_morning_text_settings()["adults"] if a["name"] == "Sam")
    assert sam["phone"] == "", "nobody else's number was touched"


def test_chat_morning_text_still_refuses_a_child_by_name():
    import pytest
    from app.tools import digest
    for n, group in (("Emily", "adult"), ("Vic", "adult"), ("Theo", "child")):
        tools.add_member(n)
        tools.set_member_age_group(n, group)

    with pytest.raises(ValueError, match="down as an adult"):
        digest.set_morning_text(phone="416-555-0100", name="theo")
