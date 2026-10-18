"""A reopened week is not a draft the list never heard of — overnight/discard-reopened-week.

`reopen_weekly_plan` puts an approved week back to `status = 'draft'` and,
deliberately (DECISIONS.md #2), leaves every grocery line its approval
added. `discard_draft_plan` refused only `status = 'approved'`, on the
assumption that a draft reaches the list only at approval — so reopen then
"drop it" retired a week that had been shopped for and left its lines on
the list for meals that no longer exist. The approved-week refusal's own
reason ("dropping a week that has been shopped for would take the list's
own reason away with it") is exactly as true of the reopened week, so it
is refused the same way. The signal is the ledger, not the status: reopen
clears every approval stamp, but the plan's `meal_plan_grocery_links` rows
are what "this week is on the list" actually means.
"""
import pytest

from app import tools
from app.db import get_conn
from tests.test_draft_waits_for_approval import _approved_week, _draft_over, _needed
from tests.test_planning_periods import _plan_row, recipes, stub_model  # noqa: F401


def test_a_reopened_week_is_refused_and_its_list_stays_explained(recipes, stub_model):
    week, plan_id = _approved_week(stub_model)
    before = _needed()
    assert "beans" in before
    tools.reopen_weekly_plan(plan_id)
    assert _plan_row(plan_id)["status"] == "draft"

    with pytest.raises(tools.SlotRefused, match="already on your shopping list"):
        tools.discard_draft_plan(plan_id)

    assert _plan_row(plan_id)["status"] == "draft", "still the week, still editable"
    assert _needed() == before


def test_the_route_answers_the_refusal_as_a_200_that_says_no(recipes, stub_model, signed_in):
    week, plan_id = _approved_week(stub_model)
    assert signed_in.post(f"/api/week/{week}/reopen").status_code == 200
    res = signed_in.post(f"/api/week/{week}/discard", json={"weekly_plan_id": plan_id})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "refused"
    assert "already on your shopping list" in res.json()["message"]
    assert _plan_row(plan_id)["status"] == "draft"


def test_a_reopened_week_whose_lines_are_all_gone_can_be_dropped(recipes, stub_model):
    """The ledger is the test, so a week with nothing left on the list is an
    ordinary draft again — the refusal is about the list, not the history."""
    week, plan_id = _approved_week(stub_model)
    tools.reopen_weekly_plan(plan_id)
    conn = get_conn()
    conn.execute(
        "DELETE FROM meal_plan_grocery_links WHERE meal_plan_entry_id IN "
        "(SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ?)", (plan_id,),
    )
    conn.commit()
    conn.close()
    assert tools.discard_draft_plan(plan_id)["status"] == "retired"


def test_a_never_approved_draft_over_an_approved_week_still_drops(recipes, stub_model):
    week, approved = _approved_week(stub_model)
    before = _needed()
    draft = _draft_over(stub_model, tools.period_dates(week, 7)[3], 4)
    assert tools.discard_draft_plan(draft["weekly_plan_id"])["status"] == "retired"
    assert _needed() == before
