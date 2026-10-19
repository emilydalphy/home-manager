"""
A meal count is 0-7 (snacks a day 0-6) whichever door it comes in by.

Defect hunt, 2026-10-19. The range lived only in edit_preference (Settings,
What we know). Onboarding's answers and the chat tool write through
set_household_meal_preferences, which stored what arrived: POST
/api/onboarding/answers with dinners_per_week 50, breakfasts_per_week -3 and
snacks_per_day 40 answered 200, and /api/usual-week then read back "50
different dinners", "-3 breakfasts" and 40 snacks a day.
"""
import pytest

from app import tools


def _counts():
    return tools.get_meal_planning_preferences()["meal_counts"]


@pytest.mark.parametrize("field, value", [
    ("dinners_per_week", 50),
    ("breakfasts_per_week", -3),
    ("lunches_per_week", 8),
    ("snacks_per_day", 40),
])
def test_onboarding_refuses_a_count_outside_the_week_and_writes_nothing(signed_in, field, value):
    before_members = len(tools.list_members())
    before_counts = _counts()
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Nobody New"], "dinners_per_week": 4, field: value,
    })
    assert res.status_code == 400, res.text
    assert field in res.json()["detail"]
    assert len(tools.list_members()) == before_members
    assert _counts() == before_counts


def test_onboarding_still_saves_the_edges_of_the_range(signed_in):
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Ana"], "dinners_per_week": 7, "breakfasts_per_week": 0,
        "lunches_per_week": 7, "snacks_per_day": 3,
    })
    assert res.status_code == 200, res.text
    counts = _counts()
    assert (counts["dinners_per_week"], counts["breakfasts_per_week"], counts["snacks_per_day"]) == (7, 0, 3)


def test_the_chat_tool_refuses_a_count_outside_the_week():
    tools.set_household_meal_preferences(dinners_per_week=4, mark_complete=False)
    with pytest.raises(ValueError, match="dinners_per_week"):
        tools.set_household_meal_preferences(dinners_per_week=10, mark_complete=False)
    with pytest.raises(ValueError, match="snacks_per_day"):
        tools.set_household_meal_preferences(snacks_per_day=7, mark_complete=False)
    assert _counts()["dinners_per_week"] == 4


def test_settings_keeps_its_own_messages():
    with pytest.raises(ValueError, match=r"dinners_per_week must be from 0 to 7, not 9\."):
        tools.edit_preference("dinners_per_week", 9)
    with pytest.raises(ValueError, match=r"snacks_per_day must be a whole number from 0 to 6\."):
        tools.edit_preference("snacks_per_day", "lots")
    assert tools.edit_preference("dinners_per_week", "5") and _counts()["dinners_per_week"] == 5
