"""
The household's people change after the week is approved — someone added,
a baby switched into meals, an adult taken out — and the shopping follows
the table, resized in place exactly as guests are
(attendance.resize_for_new_headcount; defect hunt 2026-10-26).

Before this, Cook re-counted at read time ("Cooking for 3") while the list
stayed at what approval bought for two: every membership write re-derived
attendance (reconcile_membership) and none of them told the list.
"""
import datetime

from app import household_deletion, households, invites, tools
from app.db import get_conn
from conftest import household_today, on_the_weeks_monday


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


pytestmark = on_the_weeks_monday(_monday())

WED = (_monday() + datetime.timedelta(days=2)).isoformat()


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


def _ids() -> list[int]:
    conn = get_conn()
    ids = [r[0] for r in conn.execute("SELECT id FROM grocery_items ORDER BY id")]
    conn.close()
    return ids


def test_a_person_added_after_approval_is_bought_for_and_the_lines_keep_their_ids():
    _approved_week()
    assert _list() == {"beef": "1 lb", "lettuce": "1 head"}
    ids = _ids()

    tools.add_member("Rae")

    assert _list() == {"beef": "1.5 lbs", "lettuce": "2 heads"}, "three at the table, a recipe for two"
    assert _ids() == ids, "resized in place, never new lines"


def test_a_baby_switched_into_meals_from_settings_is_bought_for(client, signed_in):
    _approved_week()
    tools.add_member("Bo")
    tools.set_member_age_group("Bo", "child")
    r = client.post("/api/memory/member/age", json={"name": "Bo", "age_years": 0, "include_in_meals": False})
    assert r.status_code == 200, r.text
    assert _list()["beef"] == "1 lb", "left out of meals: two at the table"

    r = client.post("/api/memory/member/age", json={"name": "Bo", "age_years": 0, "include_in_meals": True})
    assert r.status_code == 200, r.text
    assert _list()["beef"] == "1.5 lbs"


def test_an_adult_taken_out_of_the_household_is_no_longer_bought_for():
    # Household 1 is protected from removals; a beta household is not.
    hid = households.create_household("The Beta Testers", "beta-tester-passphrase")
    with tools.use_household(hid):
        _approved_week()
        rae = tools.add_member("Rae")["member_id"]
        tools.set_member_age_group("Rae", "adult")
        assert _list()["beef"] == "1.5 lbs"

    household_deletion.remove_member(hid, rae)

    with tools.use_household(hid):
        assert _list() == {"beef": "1 lb", "lettuce": "1 head"}


def test_a_helper_invited_who_does_not_eat_here_leaves_the_list_alone():
    _approved_week()
    before = _list()
    invites.add_adult(1, "Nina", eats_here=False)
    assert _list() == before


def test_a_snapshot_that_fails_does_not_block_the_change(monkeypatch):
    # The list following is a courtesy; adding the person is the job.
    from app.tools import attendance
    def boom():
        raise RuntimeError("locked")
    monkeypatch.setattr(attendance, "_upcoming_headcounts", boom)
    tools.add_member("Zed")
    assert any(m["name"] == "Zed" for m in tools.list_members())
