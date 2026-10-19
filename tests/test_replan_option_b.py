"""
Re-plan Option B: "Same as last week?" in onboarding's look (Emily, live
session 2026-10-18 — the approved mockup's Option B, the no-redesign gate
lifted for this card only).

Slice 1, the fix underneath: when onboarding finishes, its answers are
kept as the first period's week answers — the "last week" Plan a week
reads — so the first re-plan after onboarding opens on the summary page
instead of all five steps. Before this nothing wrote a week_intake row for
the first week, so last_intake was None for the period after it.

Pinned like tests/test_onboarding_not_now_keeps_this_week.py: Wednesday
2026-10-14, a Saturday shop, so the household's week runs Sat 10 - Fri 16
and "this week" is the three days Wed 14 - Fri 16.
"""
from __future__ import annotations

import pytest

from app import agent, tools

pytestmark = pytest.mark.today("2026-10-14 12:00")


def _finish_setup(client, start="this_week", allow=True, excited=("Thai", "Italian")):
    """The end-of-setup writes in finishSetupAndReveal's order."""
    assert client.post("/api/ai-consent", json={"allow": allow}).status_code == 200
    assert client.post("/api/onboarding/rhythm", json={"shop_day": "saturday"}).status_code == 200
    body = {"member_names": ["Ada"], "dinners_per_week": 5, "excited_about": list(excited),
            "first_plan_start": start}
    assert client.post("/api/onboarding/answers", json=body).status_code == 200


def _draft_first_week(client, monkeypatch):
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: [
        {"date": d, "slot": s, "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
        for d in ("2026-10-14", "2026-10-15", "2026-10-16") for s in tools.WEEK_SLOTS
    ])
    res = client.post("/api/onboarding/generate-first-plan", json={"start": "this_week"})
    assert res.status_code == 200
    return res.json()


class TestSlice1SetupIsLastWeek:
    def test_the_first_replan_after_onboarding_has_a_last_week(self, signed_in, monkeypatch):
        _finish_setup(signed_in)
        plan = _draft_first_week(signed_in, monkeypatch)
        # The door under the first week: the period after it, at the
        # household's own length (next_period_after).
        nxt = signed_in.get("/api/week/planning-period").json()
        assert nxt["start_date"] == "2026-10-17"
        prefill = signed_in.get(f"/api/week/{nxt['start_date']}/intake?day_count={nxt['day_count']}").json()
        last = prefill["last_intake"]
        assert last is not None, "fresh out of onboarding, Plan a week would ask all five steps again"
        assert last["week_start"] == plan["week_start_date"]
        assert last["cuisines"] == ["Thai", "Italian"]
        # The first plan was drafted FROM those answers, so it says how long it ran.
        assert last["day_count"] == 3
        assert prefill["intake"] is None          # next week's own answers start empty

    def test_the_first_week_itself_is_not_left_half_answered(self, signed_in, monkeypatch):
        """The setup answers are attached to the first plan, so re-planning
        the first week is a re-plan of answered questions, not somebody's
        unfinished intake (in_flight)."""
        _finish_setup(signed_in)
        _draft_first_week(signed_in, monkeypatch)
        prefill = signed_in.get("/api/week/2026-10-14/intake?day_count=3").json()
        assert prefill["plan_exists"] is True
        assert prefill["intake"]["cuisines"] == ["Thai", "Italian"]
        assert prefill["in_flight"] is False

    def test_a_reposted_finish_adds_no_revision(self, signed_in):
        _finish_setup(signed_in)
        _finish_setup(signed_in, excited=("Mexican",))
        history = tools.get_week_intake_history("2026-10-10")
        assert [h["revision"] for h in history] == [1]
        assert history[0]["cuisines"] == ["Thai", "Italian"]

    def test_not_now_still_keeps_the_answers(self, signed_in):
        """"Not now" to sharing with Claude drafts nothing, but setup did
        finish: the answers are there for whichever week is planned next."""
        _finish_setup(signed_in, allow=False)
        assert tools.get_week_intake("2026-10-10")["cuisines"] == ["Thai", "Italian"]
