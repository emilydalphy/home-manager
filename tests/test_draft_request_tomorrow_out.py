"""
Snag rule 8 for a dish asked for "tomorrow" (or "tonight") when that night
is a night out (hunt, 2026-10-28).

Emily's rule: "A request on a day out or away moves to the nearest night
that fits the time rules, always flagged." It held for a weekday their
words named ("Tacos on Friday please") and not for "Tacos tomorrow
please" with tomorrow tagged Out. That "tomorrow" is deliberately not
pinned at generation (pinning put the dish on the night and the out pass
deleted it), so it reached rule 8 as an ordinary typed dish, and rule 8
reads only weekday names. On main:
  * the model's own night stood (Saturday here, not the nearest night),
    nothing was said, and line one told them "Tacos … Saturday, as you
    asked" — which is not what they asked;
  * a dish the model put ON the out night was rescued to the nearest
    night, but stamped `said: False`, so no line said it had moved.

Same fixtures as tests/test_draft_request_on_day_out.py: the model and the
re-pick are stubbed, everything else is the real generation.
"""
from __future__ import annotations

import datetime

from app import tools
from app.tools import draft_flags

from conftest import household_today
from test_rush_cap_enforced import (  # noqa: E402 — the fixtures this is about
    _dinners, _filler, _recipe, _week,
    capped, picker, run, stub_model,  # noqa: F401 — pytest fixtures
)


def _asked(span: str) -> dict:
    return {"derived_from": {"freeform": span}}


def _by_date(plan_id):
    return {r["date"]: r for r in _dinners(plan_id)}


def _texts(plan_id):
    return [f["text"] for f in draft_flags.plan_flags(plan_id)]


def _weekday(iso: str) -> str:
    return datetime.date.fromisoformat(iso).strftime("%A")


def _this_period():
    """A seven-day period starting on the household's today, with
    tomorrow tagged Out. Today's dinner and the day after tomorrow are
    both one night from tomorrow, and the tie goes to the later night —
    so the nearest night is always dates[2], whatever the hour."""
    week = household_today().isoformat()
    dates = tools.period_dates(week, 7)
    _filler()
    _recipe("Tacos", 25)
    _recipe("Quick Eggs", 10)
    return week, dates


def test_a_dish_asked_for_tomorrow_on_a_night_out_goes_to_the_nearest_night_and_says_so(stub_model, run):
    """CATCH (main: Tacos stays on the model's far night, no line, and the
    opener says "…, as you asked" about a night they never named)."""
    week, dates = _this_period()
    said = "Tacos tomorrow please"
    tools.save_week_intake(week, night_tags={dates[1]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Quick Eggs"] * 5 + ["Tacos"] + ["Quick Eggs"], **{dates[5]: _asked(said)}))

    plan_id, _ = run(week, None)

    rows = _by_date(plan_id)
    assert rows[dates[1]]["slot_state"] == "planned_empty", "the out night stays out"
    assert rows[dates[2]]["meal"] == "Tacos"
    assert [r["meal"] for r in rows.values()].count("Tacos") == 1, "moved, never copied or dropped"
    assert _texts(plan_id) == [
        f"You're out {_weekday(dates[1])}, so Tacos moved to {_weekday(dates[2])}."]
    menu = tools.get_week_menu(plan_id)
    assert "as you asked" not in str(menu.get("draft_opener")), menu.get("draft_opener")
    assert tools.audit_plan_slots(plan_id)["duplicated"] == []


def test_a_dish_the_model_put_on_the_night_out_itself_is_said_when_it_moves(stub_model, run):
    """CATCH (main: rescued to the nearest night, but stamped unsaid, so
    the draft had no line)."""
    week, dates = _this_period()
    said = "Tacos tomorrow please"
    tools.save_week_intake(week, night_tags={dates[1]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Quick Eggs", "Tacos"] + ["Quick Eggs"] * 5, **{dates[1]: _asked(said)}))

    plan_id, _ = run(week, None)

    rows = _by_date(plan_id)
    assert rows[dates[1]]["slot_state"] == "planned_empty"
    assert rows[dates[2]]["meal"] == "Tacos"
    assert _texts(plan_id) == [
        f"You're out {_weekday(dates[1])}, so Tacos moved to {_weekday(dates[2])}."]


def test_being_out_tomorrow_is_not_asking_for_the_dish_tomorrow(stub_model, run):
    """GUARD: "We're out tomorrow, so tacos on <day>" names the day for the
    tacos, and tomorrow only for being out — the tacos stay where they were
    asked for, with no rule-8 line."""
    week, dates = _this_period()
    said = f"We're out tomorrow, so tacos on {_weekday(dates[4])}"
    tools.save_week_intake(week, night_tags={dates[1]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Quick Eggs"] * 4 + ["Tacos"] + ["Quick Eggs"] * 2, **{dates[4]: _asked(said)}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[4]]["meal"] == "Tacos"
    assert not [t for t in _texts(plan_id) if t.startswith("You're out")]


def test_only_a_dinner_named_today_or_tomorrow_counts():
    """GUARD, on the reader itself: tomorrow's DINNER is what a night out
    takes away, so "for lunch tomorrow" names no dinner, and neither does a
    "not tomorrow"."""
    from app.tools import cap_enforce

    tomorrow = (household_today() + datetime.timedelta(days=1)).isoformat()
    read = cap_enforce.dinners_named_relatively
    assert read({"freeform": "Tacos tomorrow please"}, (), "Tacos") == {tomorrow}
    assert read({"freeform": "Tacos for lunch tomorrow"}, (), "Tacos") == set()
    assert read({"freeform": "Tacos, but not tomorrow"}, (), "Tacos") == set()
    assert read({"freeform": "Tacos on Friday"}, (), "Tacos") == set()
