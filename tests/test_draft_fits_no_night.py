"""
Emily's snag rule 5 (2026-09-23, "When your requests and your week don't
line up"): "A requested dish that fits no night goes on the night with the
most time, and Pomona says so. It's never dropped silently." Her line, word
for word:

    The braise takes 90 minutes, and your longest night is Sunday, so it's there.

WHAT MAIN DID BEFORE THIS, measured through generation with the model
stubbed: a dish they asked for with NO day named, over every night's cap,
stayed wherever the model had put it (cap_enforce leaves `theirs` alone)
and the draft said scenario 3's "Long Braise takes 90 minutes, and
Wednesday is short on time." about a Wednesday nobody chose. So the
catches here are the PLACEMENT (it ends up on the longest night) and the
LINE (rule 5's sentence, no buttons). A dish whose words named a day is
scenario 3's and must not move — that guard is asserted beside them.

Same fixtures as tests/test_draft_snag_flags.py: the model and the re-pick
are stubbed (test_rush_cap_enforced's `stub_model` / `run`), everything
else is the real generation.
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


def _busy_week(week, dates, *, out=None, freeform=None):
    """Every night short on time: with Emily's 20-minute weeknight answer
    (`capped`) Monday-Friday cap at 20 and the weekend at 30 — so a
    90-minute braise fits none of them, and the weekend is the longest."""
    tags = {d: ["rush"] for d in dates}
    if out:
        tags[out] = ["rush", "out"]
    tools.save_week_intake(week, night_tags=tags, freeform=freeform)


def _asked(span: str) -> dict:
    return {"derived_from": {"freeform": span}}


def _by_date(plan_id):
    return {r["date"]: r for r in _dinners(plan_id)}


def test_a_dish_that_fits_no_night_goes_on_the_longest_one_and_says_so(capped, stub_model, run):
    """
    CATCH (main: the braise stays on Wednesday, and the line is scenario
    3's). Saturday is out, so Sunday is the one longest night — never the
    night nobody is home for.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    _busy_week(week, dates, out=dates[5], freeform="A long braise this week")
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked("A long braise this week")}))

    plan_id, seen = run(week, None)

    rows = _by_date(plan_id)
    assert rows[dates[6]]["meal"] == "Long Braise"
    assert rows[dates[2]]["meal"] != "Long Braise"
    assert rows[dates[5]]["slot_state"] == "planned_empty"
    assert [r["meal"] for r in rows.values()].count("Long Braise") == 1, "moved, never copied or dropped"
    flags = draft_flags.plan_flags(plan_id)
    assert [f["text"] for f in flags] == [
        "Long Braise takes 90 minutes, and your longest night is Sunday, so it's there."
    ]
    assert flags[0]["kind"] == draft_flags.FITS_NOWHERE
    assert flags[0]["fixes"] == [], "no buttons were decided for rule 5"
    # ...and the draft payload carries it, which is what the screen draws.
    menu = tools.get_week_menu(plan_id)
    assert [f["text"] for f in menu.get("draft_flags") or []] == [flags[0]["text"]]


def test_equal_longest_nights_go_to_the_earliest(capped, stub_model, run):
    """
    CATCH for the tie-break. Saturday and Sunday both have 30 minutes: a
    braise drafted on Wednesday goes to Saturday, the earliest.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    _busy_week(week, dates, freeform="A long braise this week")
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked("A long braise this week")}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[5]]["meal"] == "Long Braise"
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [
        "Long Braise takes 90 minutes, and your longest night is Saturday, so it's there."
    ]


def test_a_dish_already_on_a_longest_night_stays_and_is_still_said(capped, stub_model, run):
    """GUARD. Nothing needs to move, so nothing does — but it is never
    silent: the line is said where it already is."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    _busy_week(week, dates, freeform="A long braise this week")
    stub_model(_week(dates, ["Quick Eggs"] * 6 + ["Long Braise"],
                     **{dates[6]: _asked("A long braise this week")}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[6]]["meal"] == "Long Braise"
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [
        "Long Braise takes 90 minutes, and your longest night is Sunday, so it's there."
    ]


def test_a_dish_whose_words_named_a_day_stays_on_that_day(capped, stub_model, run):
    """
    GUARD — scenario 3, not rule 5. The model quoted only half her
    sentence ("a long braise"); the half it dropped named Wednesday. Her
    choice of night stands and scenario 3's line is said. Pinned by the
    mutation that reads only the model's span for a day.
    """
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    _busy_week(week, dates, freeform="A long braise on Wednesday please")
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked("a long braise")}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[2]]["meal"] == "Long Braise"
    texts = [f["text"] for f in draft_flags.plan_flags(plan_id)]
    assert texts == ["Long Braise takes 90 minutes, and Wednesday is short on time."]


def test_moved_off_its_longest_night_by_hand_it_is_said_as_scenario_3(capped, stub_model, run):
    """GUARD on read-time honesty: once the household moves it to a short
    night, "your longest night is Saturday, so it's there" is no longer
    true — the night's own line is said instead."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    _busy_week(week, dates, freeform="A long braise this week")
    stub_model(_week(dates, ["Quick Eggs"] * 6 + ["Long Braise"],
                     **{dates[6]: _asked("A long braise this week")}))
    plan_id, _ = run(week, None)

    from app.tools import meal_move
    result = meal_move.move_meal(plan_id, _by_date(plan_id)[dates[6]]["id"], dates[1])
    assert result.get("status") == "moved", result

    texts = [f["text"] for f in draft_flags.plan_flags(plan_id)]
    assert texts == ["Long Braise takes 90 minutes, and Tuesday is short on time."]


# ---------------------------------------------------------------- review, 2026-10-25

def test_a_day_typed_short_is_still_a_day_named(capped, stub_model, run):
    """CATCH (review: "Long braise Wed please" moved the braise to
    Saturday). A short form is a day; a named day outranks a time limit."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    _busy_week(week, dates, freeform="Long braise Wed please")
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked("Long braise")}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[2]]["meal"] == "Long Braise"
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [
        "Long Braise takes 90 minutes, and Wednesday is short on time."
    ]


def test_a_day_named_for_another_dish_does_not_pin_this_one(capped, stub_model, run):
    """CATCH (review: Friday was the tacos', and it kept the braise off
    rule 5). Only the clause about this dish counts."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    said = "A long braise sometime, and tacos on Friday"
    _busy_week(week, dates, freeform=said)
    stub_model(_week(dates, ["Quick Eggs"] * 2 + ["Long Braise"] + ["Quick Eggs"] * 4,
                     **{dates[2]: _asked("A long braise sometime")}))

    plan_id, _ = run(week, None)

    assert _by_date(plan_id)[dates[5]]["meal"] == "Long Braise"
    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [
        "Long Braise takes 90 minutes, and your longest night is Saturday, so it's there."
    ]


def test_a_day_inside_the_dish_name_is_not_a_day_named():
    """GUARD on the name carve-out, and on "sun-dried"."""
    from app.tools.cap_enforce import named_a_day
    assert not named_a_day({"freeform": "Sunday roast please"}, (), "Sunday Roast")
    assert named_a_day({"freeform": "Sunday roast on Sunday"}, (), "Sunday Roast")
    assert not named_a_day({"freeform": "sun-dried tomato pasta"}, (), "Tomato Pasta")
    assert named_a_day({"freeform": "braise on thu"}, (), "Long Braise")


def test_swapping_nights_by_hand_re_says_the_line(capped, stub_model, run, signed_in):
    """CATCH (review: the swap-nights route never refreshed the flags, so a
    swap that put the braise on a short night said nothing until Undo)."""
    week = _monday()
    dates = tools._week_dates(week)
    _filler()
    _recipe("Long Braise", 90)
    _recipe("Quick Eggs", 10)
    _busy_week(week, dates, out=dates[5], freeform="A long braise this week")
    stub_model(_week(dates, ["Quick Eggs"] * 6 + ["Long Braise"],
                     **{dates[6]: _asked("A long braise this week")}))
    plan_id, _ = run(week, None)

    res = signed_in.post(f"/api/week/{week}/swap-nights", json={"date_a": dates[6], "date_b": dates[1]})
    assert res.status_code == 200 and res.json()["status"] == "swapped", res.text

    assert [f["text"] for f in draft_flags.plan_flags(plan_id)] == [
        "Long Braise takes 90 minutes, and Tuesday is short on time."
    ]
