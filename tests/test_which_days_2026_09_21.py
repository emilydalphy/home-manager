"""Which days? — pick the start, then tap the days (Emily, 2026-09-21, board D1).

The default is the household's horizon from the chosen start ("confirming
it's not going to assume 5 days off the bat?" — it doesn't: seven, or
three as-we-go); the tiles let her drop days. A dropped day is saved with
the intake as `skipped_days`, is planned for nothing and shopped for
nothing, and reads "Not planned" — never "Away" — on the draft.

Server side here (the intake, the generation contract, the menu); the
screen's own functions run under node in
tests/test_intake_design_2026_09_21.py. Every test is red on main.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.tools import week_intake
from app.tools import swap_in_place as _sip


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _full_week(week: str, meal: str = "Chili") -> list[dict]:
    """A disobedient model: every meal AND a snack on every day, the
    skipped one included."""
    days = []
    for day in tools._week_dates(week):
        for slot in tools.WEEK_SLOTS:
            days.append({"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False,
                         "reasoning": "fits the week"})
        days.append({"date": day, "slot": "snack", "meal_name": "Apple", "is_new_recipe": False,
                     "reasoning": "a snack"})
    return days


@pytest.fixture
def recipe():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Apple", ingredients=[{"item": "apples", "qty": "2"}])


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def _fake(context):
            seen["context"] = context
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", _fake)
        return seen

    return _stub


def _slots_for(plan_id: int) -> dict:
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, slot, slot_state, reasoning FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND component_category IS NULL", (plan_id,),
    ).fetchall()
    conn.close()
    out: dict = {}
    for r in rows:
        out.setdefault((r["date"], r["slot"]), []).append({"slot_state": r["slot_state"], "reasoning": r["reasoning"]})
    return out


def _grocery_links_for_date(plan_id: int, day: str) -> list:
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpgl.item FROM meal_plan_grocery_links mpgl "
        "JOIN meal_plan_entries mpe ON mpe.id = mpgl.meal_plan_entry_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ?", (plan_id, day),
    ).fetchall()
    conn.close()
    return [r["item"] for r in rows]


# ==========================================================================
# The intake carries the dropped days
# ==========================================================================

class TestTheIntake:
    def test_a_dropped_day_is_saved_with_the_intake_and_inherited_by_later_saves(self):
        week = _week_start()
        sat = tools._week_dates(week)[5]
        saved = tools.save_week_intake(week, skipped_days=[sat])
        assert saved["skipped_days"] == [sat]
        # The lunch step saves its own half; the dropped day rides along.
        later = tools.save_week_intake(week, packed_lunch_days=[tools._week_dates(week)[1]])
        assert later["skipped_days"] == [sat]
        assert tools.get_week_intake(week)["skipped_days"] == [sat]
        # Bringing it back is an empty list, not None.
        back = tools.save_week_intake(week, skipped_days=[])
        assert back["skipped_days"] == []

    def test_a_day_outside_the_period_or_every_day_is_refused(self):
        week = _week_start()
        outside = (datetime.date.fromisoformat(week) + datetime.timedelta(days=9)).isoformat()
        with pytest.raises(ValueError):
            tools.save_week_intake(week, skipped_days=[outside])
        with pytest.raises(ValueError):
            tools.save_week_intake(week, skipped_days=tools._week_dates(week))
        with pytest.raises(ValueError):
            tools.save_week_intake(week, skipped_days="2026-09-27")
        # Six of seven is fine: one day stays in the plan.
        assert len(tools.save_week_intake(week, skipped_days=tools._week_dates(week)[1:])["skipped_days"]) == 6

    def test_a_dropped_day_carries_no_answer_about_its_meals(self):
        week = _week_start()
        days = tools._week_dates(week)
        tue, wed = days[1], days[2]
        tools.save_week_intake(
            week, night_tags={tue: ["rush"], wed: ["left"]},
            guest_counts={wed: {"adults": 2, "children": 0}}, packed_lunch_days=[tue, wed],
        )
        saved = tools.save_week_intake(week, skipped_days=[wed])
        assert saved["night_tags"] == {tue: ["rush"]}
        assert saved["guest_counts"] == {}
        assert saved["packed_lunch_days"] == [tue]
        # And the other way round: an answer saved later for a dropped day
        # is dropped too, so the building screen never reads one back.
        again = tools.save_week_intake(week, night_tags={tue: ["rush"], wed: ["unrushed"]})
        assert again["night_tags"] == {tue: ["rush"]}

    def test_the_prefill_hands_the_screen_the_dropped_days_inside_the_period(self):
        week = _week_start()
        days = tools._week_dates(week)
        tools.save_week_intake(week, skipped_days=[days[6]], day_count=7)
        prefill = tools.get_week_intake_prefill(week, 7)
        assert prefill["intake"]["skipped_days"] == [days[6]]
        # Asked about a shorter period, a dropped day past its end is not shown.
        assert tools.get_week_intake_prefill(week, 5)["intake"]["skipped_days"] == []

    def test_the_endpoint_accepts_them(self, signed_in):
        week = _week_start()
        days = tools._week_dates(week)
        res = signed_in.post(f"/api/week/{week}/intake", json={"skipped_days": [days[5], days[6]], "day_count": 7})
        assert res.status_code == 200, res.text
        assert res.json()["skipped_days"] == [days[5], days[6]]
        bad = signed_in.post(f"/api/week/{week}/intake", json={"skipped_days": days, "day_count": 7})
        assert bad.status_code == 400
        assert "At least one day" in bad.json()["detail"]


# ==========================================================================
# The draft, the menu and the list agree
# ==========================================================================

class TestTheDraft:
    def test_the_planner_is_told_and_the_day_is_enforced_empty_whatever_it_sends(self, recipe, stub_model):
        week = _week_start()
        days = tools._week_dates(week)
        wed = days[2]
        intake = tools.save_week_intake(week, skipped_days=[wed])
        seen = stub_model(_full_week(week))

        plan = agent.generate_weekly_plan(week, intake_id=intake["intake_id"])
        plan_id = plan["weekly_plan_id"]

        # Told, in so many words.
        assert seen["context"]["intake"]["skipped_days"] == [wed]
        # Enforced: every meal on the day is deliberately empty, the snack is
        # gone, and no slot holds two rows.
        slots = _slots_for(plan_id)
        for slot in ("breakfast", "lunch", "dinner"):
            assert [s["slot_state"] for s in slots[(wed, slot)]] == ["planned_empty"]
            assert slots[(wed, slot)][0]["reasoning"] == week_intake.SKIPPED_DAY_REASON
        assert (wed, "snack") not in slots
        audit = tools.audit_plan_slots(plan_id)
        assert audit["duplicated"] == [] and audit["complete"] is True
        # The day before is untouched.
        assert [s["slot_state"] for s in slots[(days[1], "dinner")]] == ["planned"]

        # Nothing bought for it.
        tools.approve_weekly_plan(plan_id, approved_by="Emily")
        assert _grocery_links_for_date(plan_id, wed) == []
        assert _grocery_links_for_date(plan_id, days[1]) != []

    def test_the_menu_says_not_planned_never_away(self, recipe, stub_model):
        week = _week_start()
        days = tools._week_dates(week)
        sat = days[5]
        intake = tools.save_week_intake(week, skipped_days=[sat])
        stub_model(_full_week(week))
        plan = agent.generate_weekly_plan(week, intake_id=intake["intake_id"])

        menu = tools.get_week_menu(plan["weekly_plan_id"])
        day = next(d for d in menu["days"] if d["date"] == sat)
        for slot in ("breakfast", "lunch", "dinner"):
            assert day[slot]["state"] == "planned_empty"
            assert day[slot]["title"] == "Not planned"
            assert day[slot].get("need") != "away"
            assert "Away" not in (day[slot]["title"] or "")
        # An out night keeps its own words.
        other = next(d for d in menu["days"] if d["date"] == days[1])
        assert other["dinner"]["state"] == "planned"

    def test_the_prompt_names_the_rule(self):
        # The prompt is written with line continuations; read it as one line.
        prompt = open(agent.__file__, encoding="utf-8").read().replace("\\\n", "")
        assert "`intake.skipped_days` are days the household left out of this plan on purpose" in prompt
        assert "every meal and snack on a day in `intake.skipped_days`, are the exceptions" in prompt

    def test_a_rhythm_only_week_has_no_dropped_days(self):
        ctx = agent._intake_generation_context({"skipped_days": ["2026-09-27", "2026-10-30"]}, ["2026-09-27"])
        assert ctx["skipped_days"] == ["2026-09-27"]
        assert agent._intake_generation_context({})["skipped_days"] == []


# ==========================================================================
# The counts scale to the days kept (verifier, 2026-09-21)
# ==========================================================================

def _slot(date, slot, name):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
            "food_groups": ["protein", "vegetable", "carb"]}


def _pick(name: str) -> dict:
    return {
        "meal_name": name, "reason": "something new",
        "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
        "instructions": ["Cook.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 20,
    }


class TestTheCountsScaleToTheDaysKept:
    """Emily's rule is "per 7-day week, scaled to the days planned", and a
    dropped day is not planned: settings 4/3/3 on seven days with the
    weekend dropped are a FIVE-day plan — ceil(4×5/7) = 3 dinners, 3
    breakfasts, 3 lunches — not a seven-day one folded to 4/3/3. Found by
    the verifier: the model's five distinct Mon–Fri dinners were folded to
    four, "you asked for four dinners a week"."""

    def test_the_targets_the_fold_and_the_note_follow_the_kept_days(self, monkeypatch):
        tools.set_household_meal_preferences(dinners_per_week=4, breakfasts_per_week=3, lunches_per_week=3,
                                             snacks_per_day=2, snacks_per_week=7)
        week = _week_start()
        days = tools._week_dates(week)
        kept = days[:5]
        intake = tools.save_week_intake(week, skipped_days=days[5:])

        seen = {}
        def fake(ctx):
            seen["ctx"] = ctx
            out = []
            for i, d in enumerate(days):   # a disobedient model: every day, the dropped ones too
                out.append(_slot(d, "breakfast", ["Oats", "Eggs", "Toast", "Oats", "Eggs", "Oats", "Eggs"][i]))
                out.append(_slot(d, "lunch", ["Wrap", "Soup", "Salad", "Wrap", "Soup", "Wrap", "Soup"][i]))
                out.append(_slot(d, "dinner", ["Chili", "Tacos", "Curry", "Stew", "Pasta", "Chili", "Tacos"][i]))
                out.append(_slot(d, "snack", "Apple")); out.append(_slot(d, "snack", "Nuts"))
            return out
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        picks = []
        monkeypatch.setattr(_sip, "_pick_replacement", lambda ctx: (picks.append(ctx), _pick("Moussaka"))[1])

        plan = agent.generate_weekly_plan(week, intake_id=intake["intake_id"], day_count=7)
        plan_id = plan["weekly_plan_id"]

        # The model was handed the five-day targets.
        memory = seen["ctx"]["household_memory"]
        assert (memory["dinners_per_week"], memory["breakfasts_per_week"], memory["lunches_per_week"]) == (3, 3, 3)
        assert memory["snacks_per_day"] == 2

        meals = [m for m in tools.get_weekly_plan(plan_id)["meals"] if m["slot_state"] == "planned"]
        def distinct(slot):
            return {m["meal"].lower() for m in meals if m["slot"] == slot}
        # Five distinct dinners folded to three, not four.
        assert len(distinct("dinner")) == 3
        assert not any(m["date"] in days[5:] for m in meals), "nothing planned on a dropped day"
        # Two on every kept day, none on a dropped one.
        snacks = {}
        for m in meals:
            if m["slot"] == "snack":
                snacks.setdefault(m["date"], []).append(m["meal"])
        assert all(len(snacks.get(d, [])) == 2 for d in kept)
        # The folded repeats say the true scale.
        folded = {m["reasoning"] for m in meals if m["slot"] == "dinner" and m["reasoning"].startswith("On again")}
        assert folded == {"On again — four dinners a week, scaled to a five-day plan"}
        # And the opener's count note, when it speaks, names the five-day plan.
        lines = tools.get_week_menu(plan_id)["draft_opener"]
        assert not any("seven-day plan" in line for line in lines)
        from app.tools import draft_opener as _draft_opener
        note = _draft_opener.count_note(5, tools.get_household_memory())
        assert note == "Three dinners this week, not four — it’s a five-day plan."

    def test_a_full_week_with_nothing_dropped_is_unchanged(self):
        assert agent._planned_day_count({"skipped_days": []}, "2026-09-21", 7) == 7
        assert agent._planned_day_count({"skipped_days": ["2026-09-26", "2026-09-27", "2026-10-30"]}, "2026-09-21", 7) == 5
        assert agent._planned_day_count(None, "2026-09-21", 3) == 3
