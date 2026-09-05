"""
The deterministic, log-and-warn-only week quality checker.

check_week is pure — no database, no model — so most of this file hands it
small hand-built fixtures and asserts on which rule fired (or didn't).
The one integration test at the bottom confirms the wiring: that a real
generation run actually calls the checker, and that doing so never changes
the plan itself (this is a log-only pass — see app/tools/plan_quality.py).
"""
import datetime

import pytest

from app import agent, tools
from app.tools import plan_quality
from app.tools.plan_quality import check_week


def _entry(date: str, slot: str = "dinner", **overrides) -> dict:
    base = {
        "date": date,
        "slot": slot,
        "slot_state": "planned",
        "meal_name": "Some Meal",
        "reasoning": "You said you love this one, and it's quick on a weeknight.",
        "food_groups": ["protein", "vegetable"],
        "main_protein": None,
        "prep_time_minutes": None,
        "cook_time_minutes": None,
        # True by default so tests unrelated to novelty_floor don't
        # incidentally trip it — novelty_floor's own tests below override
        # this explicitly on every entry they care about.
        "is_new_recipe": True,
        "links_to": None,
    }
    base.update(overrides)
    return base


def _rule_ids(violations) -> set[str]:
    return {v.rule for v in violations}


# A Monday, so date-arithmetic-sensitive fixtures (weeknight cap, protein
# runs) land on predictable weekdays without depending on today's date.
MON = "2026-09-07"
TUE = "2026-09-08"
WED = "2026-09-09"
THU = "2026-09-10"
FRI = "2026-09-11"
SAT = "2026-09-12"
SUN = "2026-09-13"


# ---------- rush_cap_respected ----------

def test_rush_cap_fires_when_a_rush_night_runs_long():
    entries = [_entry(TUE, prep_time_minutes=15, cook_time_minutes=20)]  # 35 min
    context = {"rush_max_minutes": 20, "rush_dates": {TUE}}
    violations = check_week(entries, context)
    assert "rush_cap_respected" in _rule_ids(violations)


def test_rush_cap_does_not_fire_within_the_cap():
    entries = [_entry(TUE, prep_time_minutes=5, cook_time_minutes=10)]  # 15 min
    context = {"rush_max_minutes": 20, "rush_dates": {TUE}}
    assert _rule_ids(check_week(entries, context)) == set()


def test_rush_cap_ignores_a_night_that_was_never_tagged_rush():
    entries = [_entry(TUE, prep_time_minutes=30, cook_time_minutes=30)]
    context = {"rush_max_minutes": 20, "rush_dates": set()}
    assert "rush_cap_respected" not in _rule_ids(check_week(entries, context))


# ---------- weeknight_cap_respected ----------

def test_weeknight_cap_fires_on_a_long_weeknight_dinner():
    entries = [_entry(TUE, prep_time_minutes=30, cook_time_minutes=30)]  # 60 min
    context = {"weeknight_max_minutes": 30, "rush_dates": set()}
    assert "weeknight_cap_respected" in _rule_ids(check_week(entries, context))


def test_weeknight_cap_ignores_weekends():
    entries = [_entry(SAT, prep_time_minutes=30, cook_time_minutes=30)]
    context = {"weeknight_max_minutes": 30, "rush_dates": set()}
    assert "weeknight_cap_respected" not in _rule_ids(check_week(entries, context))


def test_weeknight_cap_is_off_when_the_household_never_set_one():
    entries = [_entry(TUE, prep_time_minutes=90, cook_time_minutes=90)]
    context = {"weeknight_max_minutes": None, "rush_dates": set()}
    assert _rule_ids(check_week(entries, context)) == set()


def test_weeknight_cap_defers_to_the_rush_check_on_a_rush_night():
    """A rush night is already checked at the stricter rush cap — the
    weeknight rule staying quiet about the same night isn't a miss, it's
    not double-counting the same violation under two rule names."""
    entries = [_entry(TUE, prep_time_minutes=90, cook_time_minutes=90)]
    context = {"weeknight_max_minutes": 30, "rush_dates": {TUE}, "rush_max_minutes": 20}
    assert "weeknight_cap_respected" not in _rule_ids(check_week(entries, context))
    assert "rush_cap_respected" in _rule_ids(check_week(entries, context))


# ---------- no_protein_run ----------

def test_protein_run_fires_on_three_consecutive_nights():
    entries = [
        _entry(MON, main_protein="chicken"),
        _entry(TUE, main_protein="chicken"),
        _entry(WED, main_protein="Chicken"),  # case-insensitive match
    ]
    context = {}
    assert "no_protein_run" in _rule_ids(check_week(entries, context))


def test_protein_run_does_not_fire_on_two_nights():
    entries = [_entry(MON, main_protein="chicken"), _entry(TUE, main_protein="chicken")]
    assert "no_protein_run" not in _rule_ids(check_week(entries, {}))


def test_protein_run_does_not_fire_across_a_gap_day():
    entries = [
        _entry(MON, main_protein="chicken"),
        _entry(TUE, main_protein="chicken"),
        # Wednesday skipped entirely
        _entry(THU, main_protein="chicken"),
    ]
    assert "no_protein_run" not in _rule_ids(check_week(entries, {}))


# ---------- dinner_repeat_in_history ----------

def test_dinner_repeat_fires_against_recent_dinner_history():
    entries = [_entry(MON, meal_name="Chili")]
    context = {"recent_history": [{"date": "2026-08-20", "slot": "dinner", "meal": "Chili"}]}
    assert "dinner_repeat_in_history" in _rule_ids(check_week(entries, context))


def test_dinner_repeat_ignores_a_match_in_a_different_slot():
    entries = [_entry(MON, meal_name="Oatmeal")]
    context = {"recent_history": [{"date": "2026-08-20", "slot": "breakfast", "meal": "Oatmeal"}]}
    assert "dinner_repeat_in_history" not in _rule_ids(check_week(entries, context))


def test_dinner_repeat_does_not_fire_with_no_match():
    entries = [_entry(MON, meal_name="Tacos")]
    context = {"recent_history": [{"date": "2026-08-20", "slot": "dinner", "meal": "Chili"}]}
    assert "dinner_repeat_in_history" not in _rule_ids(check_week(entries, context))


# ---------- reasoning_is_specific ----------

def test_reasoning_fires_on_a_blank_reason():
    entries = [_entry(MON, reasoning="")]
    assert "reasoning_is_specific" in _rule_ids(check_week(entries, {}))


def test_reasoning_fires_on_banned_filler():
    entries = [_entry(MON, reasoning="A balanced, tasty option.")]
    assert "reasoning_is_specific" in _rule_ids(check_week(entries, {}))


def test_reasoning_allows_the_prompts_own_honest_fallback():
    """The generation prompt explicitly tells the model to say 'it fit the
    week' when there's truly nothing more specific — flagging that exact
    phrase would punish the model for following its own instructions."""
    entries = [_entry(MON, reasoning="it fit the week")]
    assert "reasoning_is_specific" not in _rule_ids(check_week(entries, {}))


def test_reasoning_allows_a_genuinely_specific_reason():
    entries = [_entry(MON, reasoning="Uses up the ground beef that's about to expire.")]
    assert "reasoning_is_specific" not in _rule_ids(check_week(entries, {}))


# ---------- novelty_floor ----------

def test_novelty_floor_fires_when_nothing_is_new():
    entries = [_entry(MON, is_new_recipe=False), _entry(TUE, is_new_recipe=False)]
    assert "novelty_floor" in _rule_ids(check_week(entries, {}))


def test_novelty_floor_is_satisfied_by_one_new_recipe():
    entries = [_entry(MON, is_new_recipe=False), _entry(TUE, is_new_recipe=True)]
    assert "novelty_floor" not in _rule_ids(check_week(entries, {}))


def test_novelty_floor_does_not_fire_with_no_dinners_at_all():
    entries = [_entry(MON, slot="breakfast", is_new_recipe=False)]
    assert "novelty_floor" not in _rule_ids(check_week(entries, {}))


# ---------- open_slot_budget ----------

def test_open_slot_budget_allows_a_single_dinner_open():
    entries = [_entry(MON, slot_state="open", meal_name=None)]
    assert _rule_ids(check_week(entries, {})) == set()


def test_open_slot_budget_fires_past_one_open_slot():
    entries = [
        _entry(MON, slot_state="open", meal_name=None),
        _entry(TUE, slot_state="open", meal_name=None),
    ]
    assert "open_slot_budget" in _rule_ids(check_week(entries, {}))


def test_open_slot_budget_never_allows_breakfast_open():
    entries = [_entry(MON, slot="breakfast", slot_state="open", meal_name=None)]
    assert "open_slot_budget" in _rule_ids(check_week(entries, {}))


# ---------- leftover_direction ----------

def test_leftover_direction_allows_a_link_to_an_earlier_date():
    entries = [_entry(WED, links_to=f"{MON}:dinner")]
    assert "leftover_direction" not in _rule_ids(check_week(entries, {}))


def test_leftover_direction_fires_on_a_same_day_link():
    entries = [_entry(WED, links_to=f"{WED}:dinner")]
    assert "leftover_direction" in _rule_ids(check_week(entries, {}))


def test_leftover_direction_fires_on_a_forward_link():
    entries = [_entry(MON, links_to=f"{WED}:dinner")]
    assert "leftover_direction" in _rule_ids(check_week(entries, {}))


def test_leftover_direction_ignores_an_unparseable_link():
    entries = [_entry(MON, links_to="not-a-date:dinner")]
    assert "leftover_direction" not in _rule_ids(check_week(entries, {}))


# ---------- full_plate ----------

def test_full_plate_fires_when_a_dinner_has_no_vegetable():
    entries = [_entry(MON, food_groups=["protein", "carb"])]
    violations = check_week(entries, {})
    assert "full_plate" in _rule_ids(violations)
    assert next(v for v in violations if v.rule == "full_plate").severity == "info"


def test_full_plate_is_quiet_with_protein_and_vegetable():
    entries = [_entry(MON, food_groups=["protein", "vegetable"])]
    assert "full_plate" not in _rule_ids(check_week(entries, {}))


def test_full_plate_is_quiet_with_no_food_groups_data_at_all():
    """Empty food_groups means no data was recorded, not a verified-empty
    plate — this rule has no business guessing which one it is."""
    entries = [_entry(MON, food_groups=[])]
    assert "full_plate" not in _rule_ids(check_week(entries, {}))


# ---------- integration: wired into _finish_week_slots, read-only ----------

def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _full_week_with_quality_problems(week: str) -> list[dict]:
    """
    A complete, 21-slot week that is nonetheless full of the exact things
    this checker looks for: the same main_protein every dinner (a run) and
    the same generic reasoning everywhere, on a household with no new
    recipe anywhere. Deliberately bad, so the checker has something real
    to find.
    """
    return [
        {
            "date": day, "slot": slot, "meal_name": "Chili", "is_new_recipe": False,
            "main_protein": "beef", "reasoning": "a balanced choice",
        }
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


def test_finish_week_slots_calls_the_checker(monkeypatch):
    """
    The hook itself: _finish_week_slots must call plan_quality.check_and_log
    exactly once, with the plan it just finished and the generation
    context that plan was built from.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}], main_protein="beef")
    week = _week_start()

    calls = []
    real_check_and_log = plan_quality.check_and_log

    def _spy(plan_id, generation_context):
        calls.append((plan_id, generation_context))
        return real_check_and_log(plan_id, generation_context)

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week_with_quality_problems(week))
    monkeypatch.setattr(plan_quality, "check_and_log", _spy)

    plan = agent.generate_weekly_plan(week)

    assert len(calls) == 1
    assert calls[0][0] == plan["weekly_plan_id"]
    assert "recent_history" in calls[0][1]


def test_finish_week_slots_checker_is_read_only(monkeypatch, caplog):
    """
    The checker must find real violations here (proving it actually ran,
    not just that it was called) AND the plan on disk must come out
    exactly as generation produced it — this pass logs, it never repairs.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}], main_protein="beef")
    week = _week_start()
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _full_week_with_quality_problems(week))

    with caplog.at_level("WARNING", logger="home_manager"):
        plan = agent.generate_weekly_plan(week)

    # It actually found something -- several somethings.
    assert "no_protein_run" in caplog.text
    assert "reasoning_is_specific" in caplog.text
    assert "novelty_floor" in caplog.text

    # And changed nothing: every one of the 21 slots is still exactly the
    # meal the stubbed model returned.
    audit = tools.audit_plan_slots(plan["weekly_plan_id"])
    assert audit["complete"] is True
    assert audit["present"] == 21
    result = tools.get_weekly_plan(plan["weekly_plan_id"])
    dinners = [m for m in result["meals"] if m["slot"] == "dinner"]
    assert len(dinners) == 7
    assert all(m["meal"] == "Chili" for m in dinners)


def test_check_and_log_never_raises_into_the_caller(monkeypatch):
    """
    This is an optional, log-only pass — a bug in it must never take down
    a real generation.
    """
    def _explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(plan_quality, "check_week", _explode)
    result = plan_quality.check_and_log(999999, {})
    assert result == []
