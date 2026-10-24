"""
An allergy saved AFTER the week was approved is said on the dish's row.

Defect hunt 2026-10-24: Theo's peanut allergy added in Preferences, Peanut
Noodles on the approved week tonight, and Today still read "Cook · Peanut
Noodles", the morning text "Tonight: Peanut Noodles." — nothing anywhere.
The gate keeps a clash off every write, but an already-approved week was
checked once, at approval, and never again. See
allergen_gate.clashes_since_approval.
"""
from __future__ import annotations

import datetime

from app import tools
from app.tools import moves
from conftest import household_today


def _week_with_peanut_noodles_tonight() -> int:
    tools.add_member("Emily")
    tools.add_member("Theo")
    tools.add_recipe(
        "Peanut Noodles",
        ingredients=[{"item": "peanut butter", "qty": "3 tbsp"}, {"item": "noodles", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=15,
    )
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(today.isoformat(), "Peanut Noodles", slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def _tonight(day=None) -> dict:
    day = day or household_today()
    noon = datetime.datetime.combine(day, datetime.time(12, 0))
    return next(m for m in moves.moves_for_day(day=day, now=noon) if m["title"] == "Peanut Noodles")


def test_an_allergy_added_after_approval_is_said_on_today_and_in_the_morning_text(signed_in):
    plan_id = _week_with_peanut_noodles_tonight()
    assert tools.approve_weekly_plan(plan_id, approved_by="Emily")["status"] == "approved"
    assert _tonight()["reason"] == "", "nothing to say before the allergy exists"

    res = signed_in.post("/api/memory/member/restrictions",
                         json={"name": "Theo", "restrictions": ["peanut allergy"]})
    assert res.status_code == 200

    row = _tonight()
    assert row["reason"].startswith("Has peanut, which Theo can’t have — swap it on Plan")
    text = signed_in.get("/api/morning-text/preview").json()["text"]
    assert "Tonight: Peanut Noodles. It has peanut, which Theo can’t have — swap it on Plan." in text


def test_a_clash_confirmed_at_approval_is_not_raised_again():
    plan_id = _week_with_peanut_noodles_tonight()
    tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    blocked = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert blocked["status"] == "needs_confirmation"
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    # The household tapped "Approve anyway — I've seen the clash".
    assert _tonight()["reason"] == ""
    assert _tonight()["allergy_note"] == ""


def test_the_line_goes_when_the_allergy_does_or_the_dinner_is_eaten():
    plan_id = _week_with_peanut_noodles_tonight()
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    assert _tonight()["allergy_note"]

    tools.remove_member_dietary_restriction("Theo", "peanut allergy")
    assert _tonight()["reason"] == ""

    tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    tools.check_off_meal(_tonight()["entry_id"], "done")
    assert _tonight()["reason"] == ""


def test_a_hard_fact_written_after_approval_counts_too():
    plan_id = _week_with_peanut_noodles_tonight()
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.add_fact("people", "Emily is allergic to peanuts", hard=True)
    assert "which Emily can’t have" in _tonight()["reason"]


def test_the_warning_rides_on_the_tonight_line_so_a_full_text_never_trims_it(signed_in):
    from app.tools import digest
    plan_id = _week_with_peanut_noodles_tonight()
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    tonight_line = next(l for l in signed_in.get("/api/morning-text/preview").json()["lines"]
                        if l.startswith("Tonight:"))
    assert tonight_line.endswith("It has peanut, which Theo can’t have — swap it on Plan.")
    # A busy morning: every other part has a long line competing for the
    # production budget (MAX_TEXT_CHARS less a link).
    filler = "x" * 70
    tagged = [("meals", tonight_line), ("freezer", "Move the beef to the fridge " + filler),
              ("prep", "Marinate the chicken " + filler), ("start", "Start by 5:45 " + filler),
              ("shop", "Shop for tonight " + filler), ("kitchen", "Use the spinach " + filler)]
    lines, _said, _dropped = digest._trim_to_budget(tagged, digest.MAX_TEXT_CHARS - 47)
    assert lines[0] == tonight_line


def test_a_week_approved_before_the_record_existed_starts_quiet():
    from app.db import get_conn
    from app.tools import allergen_gate
    plan_id = _week_with_peanut_noodles_tonight()
    tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    tools.approve_weekly_plan(plan_id, approved_by="Emily", confirm_hard_conflicts=True)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET avoidances_at_approval_json = '' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()

    assert allergen_gate.snapshot_unrecorded_approvals() == 1
    assert _tonight()["reason"] == "", "the clash approved past before the deploy stays quiet"
    assert allergen_gate.snapshot_unrecorded_approvals() == 0, "a recorded week is never re-snapshotted"

    tools.add_fact("people", "Emily is allergic to peanuts", hard=True)
    assert "which Emily can’t have" in _tonight()["reason"], "an allergy added after it still is said"
