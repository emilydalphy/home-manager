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


# ==========================================================================
# Slice 3 (server): a change made while re-planning is for THIS week only
# (Emily's default, 2026-10-18): it rides on the week's intake revision as
# week_answers, the draft reads it, and Settings keeps the usual answer.
# ==========================================================================

from app.tools import allergen_gate, member_needs, swap_in_place as sip, usual_week  # noqa: E402

WEEK = "2026-10-17"          # the period after the first week (Sat - Fri)


def _slot(date, slot, name):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
            "food_groups": ["protein", "vegetable", "carb"]}


def _week_of(dates, dinners):
    out = []
    for i, d in enumerate(dates):
        out += [_slot(d, "breakfast", "Oats"), _slot(d, "lunch", "Wrap"), _slot(d, "dinner", dinners[i % len(dinners)])]
    return out


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen

    names = iter(["Moussaka", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal", "Laksa", "Tagine"] * 4)

    def quick(context):
        return {"meal_name": next(names), "ingredients": ["Stuff"], "dish_note": "Simple.",
                "food_groups": ["protein", "vegetable", "carb"]}

    monkeypatch.setattr(allergen_gate, "quick_pick", quick)
    monkeypatch.setattr(sip, "_pick_replacement", lambda c: {
        "meal_name": next(names), "reason": "new", "ingredients": [{"item": "x", "qty": "1", "category": "pantry"}],
        "instructions": ["Cook."], "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 10})
    return _stub


def _household():
    people = {n: tools.add_member(n)["member_id"] for n in ("Emily", "Vineeth")}
    tools.save_usual_week(variety={"dinner": "few_in_rotation"}, prep={"days": ["sunday"], "length": "longer"})
    return people


class TestThisWeekOnly:
    def test_the_change_rides_on_the_weeks_intake_and_settings_keep_the_usual(self, signed_in):
        _household()
        before = tools.get_usual_week()
        res = signed_in.post(f"/api/week/{WEEK}/intake", json={"day_count": 7, "week_answers": {
            "variety": {"dinner": "new_every_day"}, "prep": {"days": []},
            "grid": {"dinner": {"friday": ["Emily"]}},
        }})
        assert res.status_code == 200, res.text
        saved = res.json()["week_answers"]
        assert saved["variety"] == {"dinner": "new_every_day"} and saved["prep"] == {"days": []}
        emily = [m["id"] for m in before["members"] if m["name"] == "Emily"]
        assert saved["grid"]["dinner"]["friday"] == emily
        assert tools.get_usual_week() == before                       # Settings untouched
        # A later week starts from the usual answers, not from this one's:
        # "last week" carries no week_answers.
        nxt = tools.get_week_intake_prefill("2026-10-31")
        assert nxt["intake"] is None and "week_answers" not in nxt["last_intake"]
        assert nxt["usual"]["variety"]["dinner"]["choice"] == "few_in_rotation"
        assert nxt["usual"]["prep"]["days"] == ["sunday"]

    def test_a_later_revision_keeps_the_weeks_changes(self):
        _household()
        tools.save_week_intake(WEEK, week_answers={"variety": {"breakfast": "go_to_or_two"}})
        tools.save_week_intake(WEEK, moods=["Comfort food"])
        assert tools.get_week_intake(WEEK)["week_answers"] == {"variety": {"breakfast": "go_to_or_two"}}

    def test_a_malformed_change_is_refused_before_anything_is_written(self, signed_in):
        _household()
        res = signed_in.post(f"/api/week/{WEEK}/intake", json={"week_answers": {"variety": {"dinner": "lots"}}})
        assert res.status_code == 400
        assert tools.get_week_intake(WEEK) is None

    def test_the_draft_plans_the_week_as_changed(self, stub_model):
        _household()
        dates = tools.period_dates(WEEK, 7)
        friday = dates[6]
        tools.save_week_intake(WEEK, week_answers={
            "variety": {"dinner": "new_every_day"}, "prep": {"days": []},
            "grid": {"dinner": {"friday": ["Emily"]}},
        })
        seen = stub_model(_week_of(dates, ["Chili", "Tacos", "Curry", "Stew", "Pho", "Paella", "Risotto"]))
        agent.generate_weekly_plan(WEEK)
        memory = seen["ctx"]["household_memory"]
        assert memory["dinners_per_week"] == 7                 # "Something new every night", this week
        assert memory["rhythm"]["prep_days"] == []             # no prep day this week
        assert tools.get_slot_attendance(friday, "dinner")["present_names"] == ["Emily"]
        assert tools.get_slot_attendance(dates[5], "dinner")["headcount"] == 2

    def test_a_week_with_no_changes_drafts_from_the_usual_answers(self, stub_model):
        _household()
        dates = tools.period_dates(WEEK, 7)
        tools.save_week_intake(WEEK, moods=["Comfort food"])
        seen = stub_model(_week_of(dates, ["Chili", "Tacos", "Curry", "Stew"]))
        agent.generate_weekly_plan(WEEK)
        memory = seen["ctx"]["household_memory"]
        assert memory["dinners_per_week"] == 4
        assert [d["weekday"] for d in memory["rhythm"]["prep_days"]] == ["sunday"]

    def test_after_the_draft_the_menu_still_reads_the_weeks_own_grid(self, stub_model):
        """A swap, the menu's left-out meals and Today's moves read the usual
        week outside any draft; for a week changed for itself they read that
        week's answer (usual_week.week_answers_on), and the week after is
        the usual one again."""
        _household()
        dates = tools.period_dates(WEEK, 7)
        saturday = dates[0]
        tools.save_week_intake(WEEK, week_answers={"grid": {"breakfast": {"saturday": "off"}}})
        stub_model(_week_of(dates, ["Chili", "Tacos", "Curry", "Stew"]))
        agent.generate_weekly_plan(WEEK)
        assert (saturday, "breakfast") in usual_week.off_slots_on([saturday])
        assert ("2026-10-24", "breakfast") not in usual_week.off_slots_on(["2026-10-24"])

    def test_this_weeks_lunch_needs_pack_this_weeks_lunches(self):
        _household()
        monday = tools.period_dates(WEEK, 7)[2]
        assert member_needs.lunch_packing([monday]) == {}
        with usual_week.this_week({"lunch_needs": {"Emily": {"needs": ["thermos"], "days": {}}}}):
            packing = member_needs.lunch_packing([monday])
            assert [(p["name"], p["how"]) for p in packing[monday]] == [("Emily", "warm in a thermos")]
        assert member_needs.lunch_packing([monday]) == {}

    def test_a_nut_free_answer_for_this_week_only_adds_to_the_safety_list(self):
        _household()
        with usual_week.this_week({"lunch_needs": {"Vineeth": {"needs": ["nut_free"], "days": {}}}}):
            assert member_needs.nut_free_member_names() == ["Vineeth"]
        assert member_needs.nut_free_member_names() == []
