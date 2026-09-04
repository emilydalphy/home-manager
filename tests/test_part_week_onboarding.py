"""
A real part-week for households that onboard mid-week.

Onboarding already files its first plan under the correct Monday (fixed
2026-09-02, see test_onboarding_week_key.py) but that fix always started
the plan's CONTENT on that Monday too — so a household onboarding on, say,
a Wednesday got a plan whose first two days had already gone by, and the
slot audit (which had no concept of "past") would turn those elapsed days
into open questions asking what the household ate before they'd even
signed up.

This suite covers the actual part-week: which days are in scope (still
filed under Monday, but content starting on the real join day), that the
days before it get no slot at all — not planned, not open, simply absent
— how "X meals a week" prorates to fewer days without distorting a
household's real cooking frequency, that a part-week's grocery list only
ever contains its own days, and the floor rule for the degenerate one-day
case (onboarding on a Sunday).
"""
from __future__ import annotations

import datetime
import types

import pytest

from app import agent, tools


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _week_days(week: str, start: int, count: int) -> list[str]:
    return tools._week_dates(week)[start:start + count]


def _full_part_week(week: str, start: int, count: int, meal: str = "Chili", **extra) -> list[dict]:
    """A complete, well-behaved model response covering only [start, start+count)."""
    return [
        {
            "date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False,
            "reasoning": "fits the week", **extra,
        }
        for day in _week_days(week, start, count)
        for slot in tools.WEEK_SLOTS
    ]


@pytest.fixture
def recipe():
    tools.add_recipe(
        "Chili",
        ingredients=[{"item": "beans", "qty": "1 tin"}],
        prep_time_minutes=10, cook_time_minutes=20,
    )


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


def _slots_for(plan_id: int) -> set[tuple[str, str]]:
    from app.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, slot FROM meal_plan_entries WHERE weekly_plan_id = ? AND component_category IS NULL",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {(r["date"], r["slot"]) for r in rows}


# ---------- _prorate_meal_count: the rule itself ----------

class TestProrateMealCount:
    def test_a_full_week_passes_through_unchanged(self):
        assert agent._prorate_meal_count(7, 7) == 7
        assert agent._prorate_meal_count(3, 7) == 3

    def test_zero_stays_zero_never_floored_up_to_one(self):
        # A count of 0 means "plan none of this meal", handled entirely
        # outside proration (_finish_week_slots's zero-count pass) — this
        # must never turn a real "none, thanks" into "one, thanks".
        assert agent._prorate_meal_count(0, 5) == 0
        assert agent._prorate_meal_count(0, 1) == 0

    def test_a_five_day_week_scales_toward_the_same_ratio(self):
        # 7/7 (something different every night) -> 5/5: same ratio.
        assert agent._prorate_meal_count(7, 5) == 5
        # 4/7 (~0.57 of the week) -> round(4*5/7) = round(2.857) = 3.
        assert agent._prorate_meal_count(4, 5) == 3

    def test_a_low_frequency_preference_does_not_become_nightly_in_a_short_week(self):
        # "cook twice a week, mostly leftovers" onboarding on a Saturday
        # (2 days left) must not become "something different every night"
        # just because the day count happens to equal the raw preference.
        assert agent._prorate_meal_count(2, 2) == 1

    def test_never_exceeds_the_days_actually_available(self):
        for pref in range(1, 8):
            for days in range(1, 8):
                assert agent._prorate_meal_count(pref, days) <= days

    def test_a_nonzero_preference_never_rounds_down_to_nothing(self):
        for pref in range(1, 8):
            for days in range(1, 8):
                assert agent._prorate_meal_count(pref, days) >= 1


# ---------- generation: a genuine part-week, still filed under Monday ----------

def test_onboarding_wednesday_plans_only_wednesday_through_sunday(recipe, stub_model):
    week = _monday()
    stub_model(_full_part_week(week, start=2, count=5))

    plan = agent.generate_weekly_plan(week, day_count=5, skip_days=2)

    assert plan["week_start_date"] == week, "still filed under the week's Monday"
    expected_start = tools._week_dates(week)[2]
    assert plan["first_planned_date"] == expected_start
    assert plan["is_part_week"] is True

    slots = _slots_for(plan["weekly_plan_id"])
    assert len(slots) == 15, "5 days x 3 meals — nothing for Monday or Tuesday"
    monday, tuesday = tools._week_dates(week)[0], tools._week_dates(week)[1]
    assert not any(d in (monday, tuesday) for d, _ in slots), (
        "the days before onboarding must have NO slot at all — not planned, "
        "not open, simply absent"
    )


def test_the_audit_does_not_invent_questions_about_days_already_gone_by(recipe, stub_model):
    """
    The ticket's core complaint: without the skip_days offset,
    audit_plan_slots would treat Monday/Tuesday as "missing" against the
    plan's filed week_start_date and turn them into open questions asking
    what the household ate before it had even signed up.
    """
    week = _monday()
    stub_model(_full_part_week(week, start=2, count=5))

    plan = agent.generate_weekly_plan(week, day_count=5, skip_days=2)

    audit = tools.audit_plan_slots(plan["weekly_plan_id"], day_count=5, skip_days=2)
    assert audit["complete"] is True
    assert audit["expected"] == 15
    assert audit["missing"] == []


def test_a_saturday_onboarding_is_a_real_two_day_part_week(recipe, stub_model):
    week = _monday()
    stub_model(_full_part_week(week, start=5, count=2))

    plan = agent.generate_weekly_plan(week, day_count=2, skip_days=5)

    assert plan["week_start_date"] == week
    assert plan["first_planned_date"] == tools._week_dates(week)[5]
    assert plan["is_part_week"] is True
    assert len(_slots_for(plan["weekly_plan_id"])) == 6


def test_grocery_list_is_scoped_to_the_part_weeks_real_days(recipe, stub_model):
    """
    There's no independent date-range logic in the grocery path to get
    wrong (it only ever follows a plan's actual meal_plan_entries rows —
    see the investigation notes), but this pins the end-to-end behaviour
    rather than trusting that by inspection alone.
    """
    week = _monday()
    stub_model(_full_part_week(week, start=2, count=5))
    plan = agent.generate_weekly_plan(week, day_count=5, skip_days=2)

    tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")

    beans = [i for i in tools.list_grocery_list() if i["item"] == "beans"]
    assert beans, "the part-week's own 5 dinners still buy their ingredients"


def test_get_week_menu_flags_the_days_before_the_household_joined(recipe, stub_model):
    week = _monday()
    stub_model(_full_part_week(week, start=2, count=5))
    plan = agent.generate_weekly_plan(week, day_count=5, skip_days=2)

    menu = tools.get_week_menu(plan["weekly_plan_id"])
    assert len(menu["days"]) == 7, "still the fixed 7-day grid the Week tab renders"
    before = [d["before_plan_start"] for d in menu["days"]]
    assert before == [True, True, False, False, False, False, False]
    assert menu["days"][0]["dinner"] is None
    assert menu["days"][1]["dinner"] is None


def test_an_away_need_on_an_in_scope_day_is_still_enforced(recipe, stub_model):
    """
    Alignment check for the part-week window itself: apply_slot_needs_to_plan
    and generation_context_for_week are handed the CONTENT start date (not
    the filed Monday), so an away slot declared on one of the real days of
    a Wednesday-onboarding part-week must still be enforced — proving the
    offset window lines up, not just that it's the right length.
    """
    week = _monday()
    friday = tools._week_dates(week)[4]  # a real day of the Wed-Sun part-week
    tools.set_slot_need(friday, "dinner", "away", reason="Out for dinner")
    stub_model(_full_part_week(week, start=2, count=5))

    plan = agent.generate_weekly_plan(week, day_count=5, skip_days=2)

    from app.db import get_conn
    conn = get_conn()
    row = conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'",
        (plan["weekly_plan_id"], friday),
    ).fetchone()
    conn.close()
    assert row["slot_state"] == "planned_empty"


def test_meal_variety_preference_reaches_generation_already_prorated(recipe, stub_model):
    tools.set_household_meal_preferences(dinners_per_week=4)
    week = _monday()
    seen = stub_model(_full_part_week(week, start=2, count=5))

    agent.generate_weekly_plan(week, day_count=5, skip_days=2)

    # round(4 * 5 / 7) == 3 — see TestProrateMealCount above for the rule.
    assert seen["context"]["household_memory"]["dinners_per_week"] == 3


def test_a_full_week_generation_is_unaffected_by_the_new_parameters(recipe, stub_model):
    """skip_days defaults to 0 — every existing (non-onboarding) caller is untouched."""
    week = _monday()
    stub_model(_full_part_week(week, start=0, count=7))

    plan = agent.generate_weekly_plan(week)

    assert plan["is_part_week"] is False
    assert plan["first_planned_date"] == week
    assert len(_slots_for(plan["weekly_plan_id"])) == 21


# ---------- the Sunday floor rule, exercised through the real endpoint ----------

def _freeze_main_clock(monkeypatch, frozen: datetime.date):
    """Same technique as test_onboarding_week_key.py's _FrozenDate."""
    class _FrozenDate(datetime.date):
        @classmethod
        def today(cls):
            return frozen

    from app import main as main_module
    monkeypatch.setattr(
        main_module,
        "datetime",
        types.SimpleNamespace(
            date=_FrozenDate,
            timedelta=datetime.timedelta,
            datetime=datetime.datetime,
            timezone=datetime.timezone,
        ),
    )


def test_onboarding_on_sunday_skips_the_degenerate_one_day_part_week(signed_in, monkeypatch):
    """
    A 1-day part-week (onboarding on Sunday) is judged not worth its own
    machinery (Emily's judgment call to revisit, see the ticket write-up)
    — it folds forward into a normal, full 7-day plan starting the very
    next day instead.
    """
    monday = datetime.date.fromisoformat(_monday())
    sunday = monday + datetime.timedelta(days=6)
    _freeze_main_clock(monkeypatch, sunday)
    next_monday = monday + datetime.timedelta(days=7)

    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm",
        lambda ctx: _full_part_week(next_monday.isoformat(), start=0, count=7),
    )

    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200
    body = res.json()

    assert body["week_start_date"] == next_monday.isoformat()
    assert body["first_planned_date"] == next_monday.isoformat()
    assert body["is_part_week"] is False
    assert len(body["meals"]) == 21, "a full week, not a token 1-day plan"


def test_onboarding_on_wednesday_produces_a_real_part_week_through_the_endpoint(signed_in, monkeypatch):
    monday = datetime.date.fromisoformat(_monday())
    wednesday = monday + datetime.timedelta(days=2)
    _freeze_main_clock(monkeypatch, wednesday)

    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm",
        lambda ctx: _full_part_week(monday.isoformat(), start=2, count=5),
    )

    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200
    body = res.json()

    assert body["week_start_date"] == monday.isoformat(), "still filed under Monday"
    assert body["first_planned_date"] == wednesday.isoformat()
    assert body["is_part_week"] is True
    assert len(body["meals"]) == 15, "5 days x 3 meals, nothing for Mon/Tue"


def test_onboarding_on_saturday_produces_a_real_two_day_part_week_through_the_endpoint(signed_in, monkeypatch):
    monday = datetime.date.fromisoformat(_monday())
    saturday = monday + datetime.timedelta(days=5)
    _freeze_main_clock(monkeypatch, saturday)

    monkeypatch.setattr(
        agent, "generate_weekly_plan_llm",
        lambda ctx: _full_part_week(monday.isoformat(), start=5, count=2),
    )

    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200
    body = res.json()

    assert body["week_start_date"] == monday.isoformat()
    assert body["first_planned_date"] == saturday.isoformat()
    assert body["is_part_week"] is True
    assert len(body["meals"]) == 6, "2 days x 3 meals — a real part-week, not folded into next week"
