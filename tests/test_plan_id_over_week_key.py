"""
Two live plans can share one week key — overnight/plan-id-over-week-key.

week_start_date is a filing key, not a unique one: a takeover that cuts days
off the front of a plan leaves it under its old start, and a draft over the
front of an approved week is filed under the same day. Every week-scoped
route resolved the bare key to the NEWEST plan, so approving, reopening,
settling a slot or swapping on the older plan acted on the other one. The
screen now sends the plan id it is showing (as /discard always did) and the
route prefers it, checked against the session's household.
"""
import pytest

from app import main as _main, tools
from app.db import get_conn
from tests.test_draft_waits_for_approval import _approved_week, _draft_over
from tests.test_planning_periods import _plan_row, recipes, stub_model  # noqa: F401


def _shared_key(stub_model):
    """An approved seven-day week and a two-day draft filed under the same
    Monday — the draft is the newest, so a bare key resolves to it."""
    week, approved = _approved_week(stub_model)
    draft = _draft_over(stub_model, week, 2)["weekly_plan_id"]
    assert _plan_row(draft)["week_start_date"] == week
    assert tools.get_plan_id_for_week(week) == draft
    return week, approved, draft


def _later_dinner(plan_id, week):
    """A dinner only the approved week holds (past the draft's two days)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, date FROM meal_plan_entries WHERE weekly_plan_id = ? AND slot = 'dinner' "
        "AND slot_state = 'planned' AND date >= date(?, '+3 days') ORDER BY date LIMIT 1",
        (plan_id, week),
    ).fetchone()
    conn.close()
    assert row
    return row["id"], row["date"]


def test_reopen_and_approve_act_on_the_plan_named(recipes, stub_model, signed_in):
    week, approved, draft = _shared_key(stub_model)

    res = signed_in.post(f"/api/week/{week}/reopen", json={"weekly_plan_id": approved})
    assert res.status_code == 200, res.text
    assert _plan_row(approved)["status"] == "draft"
    assert _plan_row(draft)["status"] == "draft"

    res = signed_in.post(f"/api/week/{week}/approve", json={"approved_by": "Emily", "weekly_plan_id": approved})
    assert res.status_code == 200, res.text
    assert res.json()["weekly_plan_id"] == approved
    assert _plan_row(approved)["status"] == "approved"
    # Approving the draft instead would have taken the week's front two
    # days; approving the week keeps all seven, and the draft is not approved.
    assert tools.plan_period(_plan_row(approved)) == (week, 7)
    assert _plan_row(draft)["status"] != "approved"


def test_slot_settles_on_the_plan_named(recipes, stub_model, signed_in):
    week, approved, draft = _shared_key(stub_model)
    _, date = _later_dinner(approved, week)
    # Without the id the key lands on the draft, which has no such night.
    assert signed_in.post(f"/api/week/{week}/slot", json={"date": date, "choice": "Tacos"}).status_code == 400
    res = signed_in.post(f"/api/week/{week}/slot", json={"date": date, "choice": "Tacos", "weekly_plan_id": approved})
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("route,tool,body", [
    ("swap-options", "swap_options", {}),
    ("swap-choose", "choose_swap_option", {"option": 0}),
    ("swap-in-place", "swap_meal_in_place", {}),
    ("swap-undo", "undo_meal_swap", {}),
])
def test_swap_routes_hand_the_named_plan_to_the_tool(recipes, stub_model, signed_in, monkeypatch, route, tool, body):
    week, approved, draft = _shared_key(stub_model)
    entry_id, _ = _later_dinner(approved, week)
    seen = []
    monkeypatch.setattr(_main.tools, tool, lambda plan_id, *a, **k: seen.append(plan_id) or {"status": "swapped"})

    signed_in.post(f"/api/week/{week}/{route}", json={"entry_id": entry_id, **body})
    signed_in.post(f"/api/week/{week}/{route}", json={"entry_id": entry_id, "weekly_plan_id": approved, **body})
    assert seen == [draft, approved], "no id: the key's answer, as before; an id: that plan"


def test_another_households_plan_id_is_refused(recipes, stub_model, signed_in):
    from app import households
    week, approved, draft = _shared_key(stub_model)
    beta = households.create_household("Beta", "a-safe-distinct-passphrase")
    conn = get_conn()
    other = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (?, ?, 'draft')",
        (beta, week),
    ).lastrowid
    conn.commit()
    conn.close()

    for path, body in [("approve", {"approved_by": "Emily"}), ("reopen", {}), ("discard", {}),
                       ("slot", {"date": week, "choice": "Tacos"}), ("swap-undo", {"entry_id": 1}),
                       ("defrost-confirm", {"items": []})]:
        res = signed_in.post(f"/api/week/{week}/{path}", json={**body, "weekly_plan_id": other})
        assert res.status_code == 404, (path, res.status_code, res.text)
    assert signed_in.get(f"/api/week/{week}/defrost-items?weekly_plan_id={other}").status_code == 404
    with tools.use_household(beta):
        assert _plan_row(other)["status"] == "draft"


def test_a_retired_plan_id_cannot_be_approved_back_to_life(recipes, stub_model, signed_in):
    week, approved, draft = _shared_key(stub_model)
    tools.discard_draft_plan(draft)
    res = signed_in.post(f"/api/week/{week}/approve", json={"weekly_plan_id": draft})
    assert res.status_code == 404
    assert _plan_row(draft)["status"] == "retired"
    # Discarding twice stays the no-op it always was.
    assert signed_in.post(f"/api/week/{week}/discard", json={"weekly_plan_id": draft}).status_code == 200


def test_start_over_will_not_clear_answers_a_same_key_plan_shares(recipes, stub_model):
    """No shared DAY, one shared intake row: the approved week cut to its
    back days, a draft on its front, both filed under the same Monday."""
    week, approved, draft = _shared_key(stub_model)
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET content_start_date = date(?, '+3 days'), day_count = 4 WHERE id = ?",
        (week, approved),
    )
    conn.commit()
    conn.close()
    assert not tools.find_overlapping_plans(*tools.plan_period(_plan_row(draft)), exclude_plan_id=draft)

    assert tools.get_reset_preview(draft)["intake_shared"] is True
    with pytest.raises(ValueError, match="overlaps another plan"):
        tools.check_week_answers_clearable(draft)
