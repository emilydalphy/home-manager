"""
Guest counts have a ceiling: 30 (Loop Board, Emily 2026-10-31).

Hosting and the week's questions saved any number of extra people — even
1,000,000 — and sized the big meal and the shopping for it. Past 30 the
screen's doors now refuse with "That’s a lot of people. For more than 30,
tell me in chat." Also on the card: swap and change-part check the week's
date is real before they ask the model.
"""
import pytest

from app import tools
from conftest import household_date
from tests.test_holidays import _thanksgiving

TOO_MANY = "That’s a lot of people. For more than 30, tell me in chat."


def _week():
    return household_date(0)


def test_hosting_31_is_refused_and_nothing_is_saved(signed_in):
    tg = _thanksgiving()
    res = signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "headcount": 31})
    assert res.status_code == 400 and res.json()["detail"] == TOO_MANY
    assert tools.get_holiday_answer(tg) is None


def test_hosting_30_is_fine(signed_in):
    tg = _thanksgiving()
    res = signed_in.post("/api/holidays/answer",
                         json={"date": tg, "answer": "hosting", "headcount": 30, "build_menu": False})
    assert res.status_code == 200, res.text
    assert res.json()["headcount"] == 30


def test_the_weeks_questions_refuse_a_night_of_more_than_30(signed_in):
    day = household_date(1)
    # Adults and children together: 20 + 11 is 31.
    res = signed_in.post(f"/api/week/{_week()}/intake",
                         json={"guest_counts": {day: {"adults": 20, "children": 11}}})
    assert res.status_code == 400 and res.json()["detail"] == TOO_MANY
    ok = signed_in.post(f"/api/week/{_week()}/intake",
                        json={"guest_counts": {day: {"adults": 20, "children": 10}}})
    assert ok.status_code == 200, ok.text


def test_a_million_guests_on_the_day_sheet_is_refused(signed_in):
    day = household_date(1)
    one = signed_in.post(f"/api/week/{_week()}/attendance",
                         json={"date": day, "slot": "dinner", "guest_count": 1_000_000})
    assert one.status_code == 400 and one.json()["detail"] == TOO_MANY
    sheet = signed_in.post(f"/api/week/{_week()}/day-attendance",
                           json={"date": day, "slots": {"dinner": {"guest_count": 31}}})
    assert sheet.status_code == 400 and sheet.json()["detail"] == TOO_MANY


@pytest.mark.parametrize("path, body, tool", [
    ("swap-in-place", {"entry_id": 1, "weekly_plan_id": 1}, "swap_meal_in_place"),
    ("swap-options", {"entry_id": 1, "weekly_plan_id": 1}, "swap_options"),
    ("change-part", {"entry_id": 1, "weekly_plan_id": 1, "choice": "tofu"}, "change_part"),
])
def test_a_junk_week_is_refused_before_the_model_is_asked(signed_in, monkeypatch, path, body, tool):
    plan_id = tools.create_weekly_plan(household_date(0))["weekly_plan_id"]
    body = {**body, "weekly_plan_id": plan_id}
    monkeypatch.setattr(tools, tool, lambda *a, **k: pytest.fail(f"{tool} ran on a junk week"))

    res = signed_in.post(f"/api/week/not-a-week/{path}", json=body)

    assert res.status_code == 400
    assert "week_start" in res.json()["detail"]
