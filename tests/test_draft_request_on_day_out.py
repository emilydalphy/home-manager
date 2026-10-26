"""
Emily's snag rule 8 (2026-09-23, "When your requests and your week don't
line up"): "A request on a day out or away moves to the nearest night that
fits the time rules, always flagged." Her line, word for word:

    You're out Friday, so pizza moved to Saturday.

WHAT MAIN DID BEFORE THIS, measured through generation with the model
stubbed. The prompt asks the model to move a dish off a night nobody is
home and record why; nothing checked. So:
  * a model that put the pizza ON the out night lost it — the out-night
    pass writes `planned_empty` over whatever is there (the tag wins), and
    the dish was nowhere on the week, with nothing said;
  * a model that DID move it put it wherever it liked (Monday, here), and
    the draft said nothing about the move.
The catches are the PLACEMENT (the nearest night someone is home whose time
fits the dish; a tie goes to the later night, which is Emily's own Friday →
Saturday) and the LINE in the draft's top block.

Same fixtures as tests/test_draft_fits_no_night.py: the model and the
re-pick are stubbed (test_rush_cap_enforced's `stub_model` / `run`),
everything else is the real generation.
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


def _monday(offset_weeks: int = 1) -> str:
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _asked(span: str) -> dict:
    return {"derived_from": {"freeform": span}}


def _by_date(plan_id):
    return {r["date"]: r for r in _dinners(plan_id)}


def _texts(plan_id):
    return [f["text"] for f in draft_flags.plan_flags(plan_id)]


def test_a_request_on_a_night_out_moves_to_the_nearest_night_and_says_so(stub_model, run):
    """
    CATCH (main: the pizza is cleared off Friday with the night and is
    nowhere on the week). No time limits, so Thursday and Saturday are both
    one night away: the tie goes to the later night — Emily's own example.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Pizza", 25)
    _recipe("Quick Eggs", 10)
    said = "Pizza on Friday please"
    tools.save_week_intake(week, night_tags={dates[4]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Quick Eggs"] * 4 + ["Pizza"] + ["Quick Eggs"] * 2,
                     **{dates[4]: _asked(said)}))

    plan_id, _ = run(week, None)

    rows = _by_date(plan_id)
    assert rows[dates[4]]["slot_state"] == "planned_empty", "the out night stays out"
    assert rows[dates[5]]["meal"] == "Pizza"
    assert [r["meal"] for r in rows.values()].count("Pizza") == 1, "moved, never copied or dropped"
    assert _texts(plan_id) == ["You're out Friday, so Pizza moved to Saturday."]
    # ...and the draft payload carries it, which is what the screen draws.
    menu = tools.get_week_menu(plan_id)
    assert [f["text"] for f in menu.get("draft_flags") or []] == _texts(plan_id)
    # Its words leave line one: the flag says it, so "Pizza Friday
    # Saturday, as you asked" is not said over it.
    assert "as you asked" not in str(menu.get("draft_opener")), menu.get("draft_opener")


def test_the_nearest_night_is_the_nearest_that_fits_the_time(capped, stub_model, run):
    """
    CATCH for "that fits the time rules". Weeknights cap at 20 minutes, so
    a 25-minute pizza asked for on an out Wednesday passes Tuesday,
    Thursday, Monday and Friday and goes to Saturday.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Pizza", 25)
    _recipe("Quick Eggs", 10)
    said = "Pizza on Wednesday"
    tools.save_week_intake(week, night_tags={dates[2]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Pizza"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked(said)}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[5]]["meal"] == "Pizza"
    assert _texts(plan_id) == ["You're out Wednesday, so Pizza moved to Saturday."]


def test_a_dish_the_model_moved_itself_is_put_on_the_nearest_night_and_said(stub_model, run):
    """
    CATCH (main: the model's Monday stands and nothing is said). The model
    did as its prompt asks and moved the pizza off the out Friday — to
    Monday. The rule is the nearest night, and the draft says so either way.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Pizza", 25)
    _recipe("Quick Eggs", 10)
    said = "Pizza on Friday please"
    tools.save_week_intake(week, night_tags={dates[4]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Pizza"] + ["Quick Eggs"] * 6, **{dates[0]: _asked(said)}))

    plan_id, _ = run(week, None)

    rows = _by_date(plan_id)
    assert rows[dates[5]]["meal"] == "Pizza"
    assert rows[dates[0]]["meal"] != "Pizza"
    assert _texts(plan_id) == ["You're out Friday, so Pizza moved to Saturday."]


def test_an_away_night_says_away(stub_model, run):
    """The same move for a night nobody is home because of an away need
    (a trip), said as "away" — the household's own word for that state."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Pizza", 25)
    _recipe("Quick Eggs", 10)
    said = "Pizza on Friday please"
    tools.save_week_intake(week, freeform=said)
    tools.set_slot_need(dates[4], "dinner", "away")
    stub_model(_week(dates, ["Quick Eggs"] * 4 + ["Pizza"] + ["Quick Eggs"] * 2,
                     **{dates[4]: _asked(said)}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[5]]["meal"] == "Pizza"
    assert _texts(plan_id) == ["You're away Friday, so Pizza moved to Saturday."]


def test_no_night_fits_it_goes_on_the_longest_and_both_lines_are_said(capped, stub_model, run):
    """
    The fallback. Every night is short on time (weeknights 20, weekend 30),
    so a 90-minute braise asked for on an out Wednesday fits nowhere: it
    goes on the longest night nearest Wednesday (Saturday, not Sunday) —
    rule 5's placement — and both true things are said.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    said = "A long braise on Wednesday"
    tags = {d: ["rush"] for d in dates}
    tags[dates[2]] = ["rush", "out"]
    tools.save_week_intake(week, night_tags=tags, freeform=said)
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked(said)}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[5]]["meal"] == "Long Braise"
    assert _texts(plan_id) == [
        "You're out Wednesday, so Long Braise moved to Saturday.",
        "Long Braise takes 90 minutes, and your longest night is Saturday, so it's there.",
    ]


def test_a_dish_already_on_the_nearest_night_stays_and_is_still_said(stub_model, run):
    """GUARD: nothing needs to move, so nothing does — but it is never
    silent. And a request whose words named no day is not said at all."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Pizza", 25)
    _recipe("Tacos", 15)
    _recipe("Quick Eggs", 10)
    said = "Pizza on Friday please. Tacos sometime"
    tools.save_week_intake(week, night_tags={dates[4]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Tacos"] + ["Quick Eggs"] * 4 + ["Pizza", "Quick Eggs"],
                     **{dates[5]: _asked("Pizza on Friday please"), dates[0]: _asked("Tacos sometime")}))

    plan_id, _ = run(week, None)

    rows = _by_date(plan_id)
    assert rows[dates[5]]["meal"] == "Pizza"
    assert rows[dates[0]]["meal"] == "Tacos"
    assert _texts(plan_id) == ["You're out Friday, so Pizza moved to Saturday."]


def test_moved_by_hand_afterwards_the_line_goes(stub_model, run):
    """GUARD on read-time honesty: "moved to Saturday" is about Saturday.
    Once the household moves it themselves, the line has nothing to say."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Pizza", 25)
    _recipe("Quick Eggs", 10)
    said = "Pizza on Friday please"
    tools.save_week_intake(week, night_tags={dates[4]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["Quick Eggs"] * 4 + ["Pizza"] + ["Quick Eggs"] * 2,
                     **{dates[4]: _asked(said)}))
    plan_id, _ = run(week, None)

    result = tools.swap_dinner_nights(plan_id, dates[5], dates[6])
    assert result.get("status") == "swapped", result

    assert _texts(plan_id) == []


# ---------------------------------------------------------------- review, 2026-10-25

def test_a_day_out_said_for_nothing_in_particular_does_not_move_a_dish(stub_model, run):
    """CATCH (review: Tacos were traded Wednesday -> Saturday and the draft
    said "You're out Friday, so Tacos moved to Saturday." — nobody asked
    for tacos on Friday). Rule 8 acts only on a day named for THIS dish."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    for n in ["Tacos", "A1", "A2", "A3", "A4", "A5", "A6"]:
        _recipe(n, 15)
    said = "Something mexican please, we're out Friday"
    tools.save_week_intake(week, night_tags={dates[4]: ["out"]}, freeform=said)
    stub_model(_week(dates, ["A1", "A2", "Tacos", "A3", "A4", "A5", "A6"],
                     **{dates[2]: _asked("Something mexican please")}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[2]]["meal"] == "Tacos"
    assert _texts(plan_id) == []
