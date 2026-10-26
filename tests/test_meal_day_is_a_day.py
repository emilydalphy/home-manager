"""
A meal's day is a YYYY-MM-DD string, whatever shape the model sends it in
(2026-10-22 defect hunt).

Every screen finds a meal by its bare day string. plan_meal saved
meal_date "2026-10-23T00:00" (or "Friday") as given, so the chat said the
dinner was planned and no screen ever drew it; swap_meal_in_plan with the
same timestamp wrote a SECOND dinner row beside the one it was asked to
replace, because it compares days as strings and the timestamp sorts
inside the week. A timestamp is now read as its day before the tool runs
(agent.read_tool_input), and a day that is no day is refused at the write.
"""
import datetime as _dt

import pytest

from app import agent, tools
from app.db import get_conn
from conftest import household_today, on_the_weeks_monday
from test_tool_args_read_as_schema import _tool_turn

# Friday's dinner has to be ahead of the household, or a swap of it is
# refused as gone: red on Friday evening through Sunday.
pytestmark = on_the_weeks_monday()


def _week_with_a_dinner():
    today = household_today()
    monday = today - _dt.timedelta(days=today.weekday())
    plan_id = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    night = (monday + _dt.timedelta(days=4)).isoformat()
    tools.plan_meal(night, "Pesto pasta", slot="dinner", weekly_plan_id=plan_id)
    return plan_id, night


def _dinners(night):
    conn = get_conn()
    rows = conn.execute(
        "SELECT freeform_meal AS meal FROM meal_plan_entries WHERE date = ? AND slot = 'dinner'", (night,)
    ).fetchall()
    stray = conn.execute(
        "SELECT date FROM meal_plan_entries WHERE length(date) != 10"
    ).fetchall()
    conn.close()
    return [r["meal"] for r in rows], [r["date"] for r in stray]


def test_a_swap_sent_with_a_timestamp_replaces_that_nights_dinner(signed_in, monkeypatch):
    plan_id, night = _week_with_a_dinner()
    result = _tool_turn(monkeypatch, signed_in, "swap_meal_in_plan", {
        "weekly_plan_id": plan_id, "meal_date": f"{night}T00:00:00Z",
        "new_meal": "Fish tacos", "slot": "dinner",
    })
    assert not result.get("is_error"), result
    dinners, stray = _dinners(night)
    assert dinners == ["Fish tacos"]
    assert stray == []


def test_a_meal_planned_with_a_timestamp_lands_on_its_day(signed_in, monkeypatch):
    _plan_id, night = _week_with_a_dinner()
    result = _tool_turn(monkeypatch, signed_in, "plan_meal", {
        "meal_date": f"{night}T18:00", "meal": "Fish tacos", "slot": "lunch",
    })
    assert not result.get("is_error"), result
    conn = get_conn()
    lunch = conn.execute(
        "SELECT freeform_meal AS meal FROM meal_plan_entries WHERE date = ? AND slot = 'lunch'", (night,)
    ).fetchall()
    conn.close()
    assert [r["meal"] for r in lunch] == ["Fish tacos"]
    assert _dinners(night)[1] == []


@pytest.mark.parametrize("bad", ["Friday", "tomorrow", "20261023", "2026-02-30", ""])
def test_a_day_that_is_no_day_is_refused_and_nothing_is_written(bad):
    plan_id, night = _week_with_a_dinner()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        tools.plan_meal(bad, "Fish tacos", slot="dinner")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        tools.swap_meal_in_plan(plan_id, bad, "Fish tacos", slot="dinner")
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM meal_plan_entries WHERE freeform_meal = 'Fish tacos'").fetchone()[0]
    conn.close()
    assert n == 0
    assert _dinners(night)[0] == ["Pesto pasta"]


def test_only_a_day_field_has_its_clock_taken_off():
    # A note that happens to start with a timestamp is the household's words.
    args = agent.read_tool_input("log_recipe_note", {"recipe_name": "x", "note": "2026-10-23T18:00 was great"})
    assert args["note"] == "2026-10-23T18:00 was great"
    args = agent.read_tool_input("set_slot_need", {"date_str": " 2026-10-23 18:00 ", "slot": "dinner", "need": "away"})
    assert args["date_str"] == "2026-10-23"


def test_a_moment_with_a_zone_is_not_trimmed_onto_the_wrong_day():
    from app.agent import _DAY_WITH_A_TIME
    # 02:00Z is 22:00 the evening before in Toronto: refused, not guessed.
    assert _DAY_WITH_A_TIME.fullmatch("2026-10-24T02:00:00Z") is None
    assert _DAY_WITH_A_TIME.fullmatch("2026-10-24T00:00:00Z").group(1) == "2026-10-24"
    assert _DAY_WITH_A_TIME.fullmatch("2026-10-24T18:00").group(1) == "2026-10-24"


def test_the_needs_you_card_answers_a_bad_day_with_a_400(signed_in):
    res = signed_in.post("/api/needs-you/dinner", json={"date": "Friday", "meal": "Tacos"})
    assert res.status_code == 400, res.text
