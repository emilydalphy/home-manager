"""
The evening nudge says an allergy saved since approval, as the morning text
and Today already do (defect hunt, 2026-11-02).

Theo's peanut allergy added after the week was approved: Today's row and
that morning's text said "It has peanut, which Theo can’t have — swap it on
Plan." The 5:30 nudge — the one sent as the cook begins — still said
"Tonight: Peanut Noodles — 25 min. Tap to start." Now it says the clause
after the dish, drops "Tap to start", and links to Plan, where the swap is.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone

import pytest

from app import tools
from app.db import get_conn
from app.tools import digest
from conftest import household_today
from test_allergy_after_approval import _week_with_peanut_noodles_tonight

TODAY = household_today()
LINK = "https://pomona.example"
CLAUSE = "It has peanut, which Theo can’t have — swap it on Plan."


@pytest.fixture(autouse=True)
def link(monkeypatch):
    monkeypatch.setenv("HOME_MANAGER_URL", LINK)


def _local(hour: int, minute: int = 0) -> datetime:
    zone = digest._cooker.household_zone()
    return datetime.combine(TODAY, dt.time(hour, minute), tzinfo=zone).astimezone(timezone.utc)


def _approved_week(allergy_after_approval: bool = True) -> int:
    plan_id = _week_with_peanut_noodles_tonight()
    tools.set_member_age_group("Emily", "adult")
    assert tools.approve_weekly_plan(plan_id, approved_by="Emily")["status"] == "approved"
    if allergy_after_approval:
        tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    tools.set_morning_text(phone="416-555-0100", on=True, name="Emily")
    return plan_id


def _nudges_at(hour: int, minute: int = 0) -> list[str]:
    sent: list[str] = []
    digest.run_evening_nudges_once(
        now_utc=_local(hour, minute),
        send=lambda member, text: (sent.append(text), {"status": "ok", "detail": ""})[1],
    )
    return sent


def test_the_nudge_says_the_allergy_and_does_not_say_tap_to_start():
    """CATCH: the defect as a household met it, through the real pass."""
    _approved_week()
    assert _nudges_at(17, 30) == [f"Tonight: Peanut Noodles — 25 min. {CLAUSE} {LINK}/week"]


def test_a_thaw_first_still_carries_the_allergy():
    plan_id = _approved_week()
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
        "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', 'defrost')",
        (tools.household_id(), plan_id, TODAY.isoformat(),
         "Move the beef to the fridge — for Thursday’s chili.", "Chili"),
    )
    conn.commit()
    conn.close()
    text = digest.build_evening_nudge(datetime.combine(TODAY, dt.time(17, 30)), link=False)
    assert text == f"Move the beef to the fridge for Thursday’s chili. Tonight: Peanut Noodles — 25 min. {CLAUSE}"


def test_no_allergy_since_approval_is_the_nudge_it_always_was():
    """GUARD: nothing new to say -> the plain nudge, into Cook."""
    _approved_week(allergy_after_approval=False)
    assert _nudges_at(17, 30) == [f"Tonight: Peanut Noodles — 25 min. Tap to start. {LINK}/kitchen"]
