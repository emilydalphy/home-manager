"""
"This week" survives a "Not now" to sharing with Claude (2026-10-14).

Found driving the app: a household that picked "This week" at the end of
setup and then said "Not now" on the consent step landed on Plan offered
the NEXT period and the one after. The start answer only ever travelled in
the first-plan request, which "Not now" never makes, so nothing on the
server knew it had been given and Plan fell back to its Friday rule.

Pinned to Wednesday 2026-10-14 with a Saturday shop (plan ready Friday, so
the household's week runs Sat 10 - Fri 16 and Wednesday is already past
PLAN_AHEAD_FROM_WEEKDAY): exactly the case where the default and the answer
disagree.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import agent, tools

pytestmark = pytest.mark.today("2026-10-14 12:00")

ONBOARDING = (Path(__file__).resolve().parent.parent / "static" / "onboarding.html").read_text()


def _finish_setup(client, start: str | None, *, allow: bool) -> None:
    """The end-of-setup writes in finishSetupAndReveal's order."""
    assert client.post("/api/ai-consent", json={"allow": allow}).status_code == 200
    assert client.post("/api/onboarding/rhythm", json={"shop_day": "saturday"}).status_code == 200
    body = {"member_names": ["Ada"], "dinners_per_week": 5}
    if start is not None:
        body["first_plan_start"] = start
    assert client.post("/api/onboarding/answers", json=body).status_code == 200


def test_not_now_keeps_this_week_on_plan_and_now(signed_in):
    # The Friday rule alone would offer Sat Oct 17 here; that is the bug.
    _finish_setup(signed_in, "this_week", allow=False)
    period = signed_in.get("/api/week/planning-period").json()
    # The rest of THIS period from today, at the household's own seven-day
    # horizon — what any current-period suggestion offers.
    assert (period["start_date"], period["day_count"]) == ("2026-10-14", 7), (
        "said 'This week' then 'Not now', and Plan offered "
        f"{period['label']} instead of the rest of this week"
    )
    assert period["is_current_period"] is True
    # Now's nudge reads the same one source of "which week".
    nudge = tools.get_week_planning_nudge()
    assert nudge["week_start"] == "2026-10-14" and nudge["is_current_week"] is True


def test_the_yes_path_still_files_the_first_week_as_before(signed_in, monkeypatch):
    # The remembered answer must not feed back into _first_plan_window
    # (plan_ahead=False): the first plan stays filed under Saturday's key,
    # content from today, three days.
    _finish_setup(signed_in, "this_week", allow=True)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: [
        {"date": d, "slot": s, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
        for d in ("2026-10-14", "2026-10-15", "2026-10-16") for s in tools.WEEK_SLOTS
    ])
    res = signed_in.post("/api/onboarding/generate-first-plan", json={"start": "this_week"})
    assert res.status_code == 200
    body = res.json()
    assert body["week_start_date"] == "2026-10-10"
    assert body["first_planned_date"] == "2026-10-14"
    # And once a week exists, the remembered answer has done its job: Plan
    # is back on its ordinary rule.
    assert signed_in.get("/api/week/planning-period").json()["start_date"] == "2026-10-17"


def test_the_answer_lapses_once_its_days_have_gone(signed_in):
    # Never planned, back the following Wednesday: "this week" (Sat 10 -
    # Fri 16) has gone, so the ordinary Friday rule is back and offers the
    # period after the one running (Sat 24), not the rest of this one.
    _finish_setup(signed_in, "this_week", allow=False)
    later = tools.suggest_planning_period(from_date="2026-10-21")
    assert later["start_date"] == "2026-10-24"
    assert later["is_current_period"] is False


def test_an_answer_never_sent_changes_nothing(signed_in):
    _finish_setup(signed_in, None, allow=False)
    assert signed_in.get("/api/week/planning-period").json()["start_date"] == "2026-10-17"


def test_the_page_sends_the_start_answer_with_the_answers():
    start = re.search(r"async function saveOnboardingAnswers\(\)\s*\{", ONBOARDING)
    body = ONBOARDING[start.end(): ONBOARDING.index("const res = await Api.fetch('/api/onboarding/answers'", start.end())]
    assert "first_plan_start: firstPlanStart" in body, (
        "the answers post is the one write both consent answers make; without "
        "the start answer in it, 'Not now' drops it"
    )
