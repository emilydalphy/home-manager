"""
The grocery shop day: an eighth household rhythm fact.

Emily, 2026-10-04, adding to Gowthami's household's first-week feedback:
"would be great if there was a screen in the onboarding that also asked
when the typical grocery shop day is during the week, so that you can have
that context for the day summary." Plus her same-evening comment: "a
reminder to plan before shop day."

What this file covers, and what it deliberately does not:

  A — the ANSWER. One onboarding screen and one Settings row writing two
      household-level rows ('shop_day', 'top_up_shop_day'), with the
      "it changes week to week" answer storing nothing, and the
      planning_anchor DEFAULT that rides along with a main shop day.
  B — the REMINDER. Two days before the shop day, the existing plan-week
      nudge opens early and says why.

  C — Today's shop line and the morning message's shopping-day option are
      NOT here: they are the two sibling tester-batch cards that READ this
      fact. What this file pins is that the fact is on the payload those
      screens read (/api/memory's rhythm), so neither of them has to
      invent a second source for it.

The load-bearing claims, since "null means varies" is easy to break by
giving a field an innocent-looking default: an unrelated rhythm save must
never clear a stored shop day, and a stored planning_anchor must never be
overwritten by one.

SEVEN MUTATIONS RUN, AND EVERY ONE BITES (red counts read off the runs):
shop_days_summary returning "" (4 red); get_household_rhythm dropping
shop_day from its return (11); setting a shop day no longer defaulting
the plan-ready day (3); shop_reminder_on forced True, i.e. the OFF answer
ignored on the payload (1); the reminder ignoring its lead-time window so
it fires every day (8); the reminder ignoring the OFF toggle (1); and the
nudge no longer opening early, i.e. main's own behaviour (1).

THREE MORE, on the rule the browser drive turned up (a derived plan-ready
day following a changed shop day while an answered one does not): the
source check dropped so a person's answer is overruled (2 red); never
re-defaulting, i.e. the shipped defect (1); and clearing the shop day
deleting the plan-ready day with it (2).
"""
import datetime

import pytest


from conftest import household_today

from app import db, tools
from app.tools import rhythm as _rhythm


def _rhythm_rows() -> dict:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT fact_type, value, source FROM household_rhythm WHERE household_id = 1"
    ).fetchall()
    conn.close()
    return {r["fact_type"]: (r["value"], r["source"]) for r in rows}


# ---------------------------------------------------------------- A: the answer


def test_a_main_shop_day_is_stored_and_read_back():
    """CATCH — nothing stored a shop day at all before this."""
    tools.set_shop_days(shop_day="saturday")
    r = tools.get_household_rhythm()
    assert r["shop_day"] == "saturday"
    assert r["top_up_shop_day"] is None
    assert r["shop_days_summary"] == "Grocery shop: Saturday"


def test_a_top_up_day_rides_beside_it():
    """CATCH — the optional second, smaller shop."""
    tools.set_shop_days(shop_day="saturday", top_up_day="wednesday")
    r = tools.get_household_rhythm()
    assert (r["shop_day"], r["top_up_shop_day"]) == ("saturday", "wednesday")
    assert r["shop_days_summary"] == "Grocery shop: Saturday · top-up Wednesday"


def test_it_changes_week_to_week_stores_nothing_and_says_nothing():
    """
    CATCH. The escape chip is a real answer and it CLEARS, rather than
    leaving the old day standing — the same distinction set_prep_days
    draws between an omitted list and an empty one. An unanswered question
    reads as silence, not as "no shop day".
    """
    tools.set_shop_days(shop_day="saturday", top_up_day="wednesday")
    tools.set_shop_days(shop_day="")
    r = tools.get_household_rhythm()
    assert r["shop_day"] is None
    # A top-up with no main shop says nothing, so it goes with it.
    assert r["top_up_shop_day"] is None
    assert r["shop_days_summary"] == ""
    assert "shop_day" not in _rhythm_rows()


def test_an_unanswered_household_reads_as_silence():
    """GUARD — pinned by the mutation that makes shop_days_summary say
    "no shop day" for an empty answer, which reddens this and the test
    above."""
    r = tools.get_household_rhythm()
    assert r["shop_day"] is None
    assert r["shop_days_summary"] == ""
    assert r["shop_reminder_on"] is True


def test_a_top_up_day_can_be_cleared_on_its_own():
    """CATCH — Settings has to be able to drop the second shop without
    dropping the first."""
    tools.set_shop_days(shop_day="saturday", top_up_day="wednesday")
    tools.set_shop_days(top_up_day="")
    r = tools.get_household_rhythm()
    assert (r["shop_day"], r["top_up_shop_day"]) == ("saturday", None)


def test_a_day_that_is_not_a_weekday_is_refused_and_writes_nothing():
    """CATCH — the same refusal every other rhythm answer gives, so a typo
    from chat never lands as a day nothing reads back."""
    tools.set_shop_days(shop_day="saturday")
    with pytest.raises(ValueError):
        tools.set_shop_days(shop_day="caturday")
    assert tools.get_household_rhythm()["shop_day"] == "saturday"


def test_none_means_not_part_of_this_answer():
    """
    CATCH, and the one that matters most: /api/onboarding/rhythm is the
    route EVERY Settings rhythm chip posts to, so a shop_day that
    defaulted to '' rather than None would make editing "who cooks" clear
    the household's shop day.
    """
    tools.set_shop_days(shop_day="saturday", top_up_day="wednesday")
    tools.save_rhythm_answers(cooking_role="whoever_free")
    r = tools.get_household_rhythm()
    assert (r["shop_day"], r["top_up_shop_day"]) == ("saturday", "wednesday")


def test_the_route_leaves_a_stored_shop_day_alone_when_the_body_omits_it(signed_in):
    """CATCH — the same claim over HTTP, where the pydantic default lives."""
    tools.set_shop_days(shop_day="saturday")
    res = signed_in.post("/api/onboarding/rhythm", json={"dinner_window": "6_8"})
    assert res.status_code == 200, res.text
    assert res.json()["shop_day"] == "saturday"


def test_the_route_saves_both_days_and_the_summary_comes_back(signed_in):
    """CATCH — onboarding's own call."""
    res = signed_in.post(
        "/api/onboarding/rhythm",
        json={"shop_day": "saturday", "top_up_shop_day": "wednesday"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["shop_days_summary"] == "Grocery shop: Saturday · top-up Wednesday"


def test_the_route_refuses_a_bad_day_with_a_400_and_writes_nothing(signed_in):
    """CATCH — and nothing else in the same body lands either, which is
    save_rhythm_answers' whole guarantee."""
    res = signed_in.post(
        "/api/onboarding/rhythm",
        json={"shop_day": "caturday", "dinner_window": "6_8"},
    )
    assert res.status_code == 400
    r = tools.get_household_rhythm()
    assert r["shop_day"] is None
    assert r["dinner_window"] is None


def test_the_fact_is_on_the_payload_every_screen_reads(signed_in):
    """
    GUARD for the two sibling cards (Today's shop line, the morning
    message's shopping-day option): they read /api/memory, so the fact has
    to be there rather than behind a route of its own. Pinned by the
    mutation that drops shop_day from get_household_rhythm's return, which
    reddens most of this file.
    """
    tools.set_shop_days(shop_day="saturday", top_up_day="wednesday")
    mem = signed_in.get("/api/memory").json()
    assert mem["rhythm"]["shop_day"] == "saturday"
    assert mem["rhythm"]["top_up_shop_day"] == "wednesday"
    assert mem["rhythm"]["shop_days_summary"] == "Grocery shop: Saturday · top-up Wednesday"
    assert mem["rhythm"]["shop_reminder_on"] is True


# -------------------------------------------- A: the plan-ready day it implies


def test_setting_a_shop_day_defaults_the_plan_ready_day_to_the_day_before():
    """
    CATCH. Emily, 2026-10-04: the screen promises "I'll have the list ready
    the day before", so the anchor has to follow — otherwise the promise is
    one the app never kept.
    """
    tools.set_shop_days(shop_day="saturday")
    r = tools.get_household_rhythm()
    assert r["planning_anchor"] == "friday"
    assert _rhythm_rows()["planning_anchor"][1] == "shop_day_default"


def test_a_monday_shop_day_wraps_the_plan_ready_day_round_to_sunday():
    """CATCH — the off-by-one a week's worth of modulo arithmetic invites."""
    tools.set_shop_days(shop_day="monday")
    assert tools.get_household_rhythm()["planning_anchor"] == "sunday"


def test_a_plan_ready_day_they_chose_is_never_overwritten():
    """
    CATCH, and the one Emily named in the comment: "Never overwrite one
    they chose." A household that said "ready by Wednesday" and shops on
    Saturday meant both.
    """
    tools.set_planning_anchor("wednesday")
    tools.set_shop_days(shop_day="saturday")
    assert tools.get_household_rhythm()["planning_anchor"] == "wednesday"


def test_clearing_the_shop_day_leaves_the_plan_ready_day_where_it_is():
    """
    GUARD. "It changes week to week" is a statement about shopping, not
    about when the plan should be final — taking the anchor away with it
    would silently un-answer a different question. Pinned by the mutation
    that deletes planning_anchor alongside shop_day.
    """
    tools.set_shop_days(shop_day="saturday")
    tools.set_shop_days(shop_day="")
    assert tools.get_household_rhythm()["planning_anchor"] == "friday"


# ------------------------------------------------------------- B: the reminder


def _pin_to(weekday_name: str) -> datetime.date:
    """
    The next date that falls on `weekday_name`, from the HOUSEHOLD's today
    — not the process's. The reminder is weekday arithmetic, so a test
    that seeded off the server's clock would be asserting the two clocks
    agree, which for four hours of every evening they do not (CLAUDE.md,
    "A dated test seeds off the HOUSEHOLD's clock").
    """
    names = list(_rhythm.SHOP_DAY_WEEKDAYS)
    today = household_today()
    return today + datetime.timedelta(days=(names.index(weekday_name) - today.weekday()) % 7)


def test_the_reminder_fires_exactly_two_days_before_the_shop_day():
    """CATCH — nothing read the shop day in the nudge before this."""
    tools.set_shop_days(shop_day="saturday")
    thursday = _pin_to("thursday")
    assert _rhythm.PLAN_BEFORE_SHOP_LEAD_DAYS == 2
    assert tools.weekly_plan._plan_before_shop_day(thursday) == {
        "shop_day": "saturday",
        "shop_day_label": "Saturday",
    }


@pytest.mark.parametrize("day", ["friday", "saturday", "sunday", "monday", "tuesday", "wednesday"])
def test_it_fires_on_one_day_a_week_and_no_other(day):
    """
    CATCH — "only one reminder per week" (Emily) falls out of the
    arithmetic rather than needing a dismissal of its own: a weekday comes
    round once every seven days. Saturday itself is deliberately quiet —
    the reminder is about a shop that has not happened yet.
    """
    tools.set_shop_days(shop_day="saturday")
    assert tools.weekly_plan._plan_before_shop_day(_pin_to(day)) is None


def test_the_toggle_off_stops_it():
    """CATCH — Settings' "Remind me to plan before shop day"."""
    tools.set_shop_days(shop_day="saturday", remind_before_shop=False)
    assert tools.get_household_rhythm()["shop_reminder_on"] is False
    assert tools.weekly_plan._plan_before_shop_day(_pin_to("thursday")) is None
    # And back on again — the row goes rather than being written 'on', so
    # "never said" and "said yes" stay one state.
    tools.set_shop_days(remind_before_shop=True)
    assert "shop_reminder" not in _rhythm_rows()
    assert tools.weekly_plan._plan_before_shop_day(_pin_to("thursday")) is not None


def test_no_shop_day_means_no_reminder():
    """GUARD — pinned by the mutation that drops the `if not shop_day`
    guard, which then reads an empty string as a weekday and raises."""
    assert tools.weekly_plan._plan_before_shop_day(_pin_to("thursday")) is None


def test_changing_the_shop_day_moves_the_reminder():
    """CATCH — Emily's own fourth test on the card."""
    tools.set_shop_days(shop_day="saturday")
    assert tools.weekly_plan._plan_before_shop_day(_pin_to("thursday")) is not None
    tools.set_shop_days(shop_day="tuesday")
    assert tools.weekly_plan._plan_before_shop_day(_pin_to("thursday")) is None
    assert tools.weekly_plan._plan_before_shop_day(_pin_to("sunday")) is not None


# --------------------------------------------------- B: the nudge it rides on


def _seed_week_covering_today() -> None:
    """An approved plan over the household's own week, so the ordinary
    Friday rule has something to be early of."""
    today = household_today()
    start = today - datetime.timedelta(days=today.weekday())
    plan_id = tools.create_weekly_plan(start.isoformat(), day_count=7)["weekly_plan_id"]
    conn = db.get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


def test_the_nudge_carries_the_shop_day_so_the_screen_can_say_why():
    """CATCH — the reminder is a REASON on the existing nudge, never a
    second card."""
    _seed_week_covering_today()
    tools.set_shop_days(shop_day="saturday")
    monkey_day = _pin_to("thursday")
    nudge = _nudge_on(monkey_day)
    assert nudge["show"] is True
    assert nudge["shop_day_label"] == "Saturday"


def test_the_nudge_says_nothing_about_a_shop_on_an_ordinary_day():
    """GUARD — pinned by the mutation that puts shop_reminder on the
    payload unconditionally."""
    _seed_week_covering_today()
    tools.set_shop_days(shop_day="saturday")
    nudge = _nudge_on(_pin_to("sunday"))
    assert "shop_day_label" not in nudge


def _nudge_on(day: datetime.date) -> dict:
    """get_week_planning_nudge with the household's clock pinned to `day`.
    It resolves its clock once, through _household_today (CLAUDE.md,
    2026-09-15), which is the one seam to hold still."""
    wp = tools.weekly_plan
    real = wp._household_today
    wp._household_today = lambda: day
    try:
        return wp.get_week_planning_nudge()
    finally:
        wp._household_today = real


def test_a_derived_plan_ready_day_follows_a_changed_shop_day():
    """
    CATCH — found by driving the real screens, not by reading the code. A
    household that answers Saturday and later corrects it to Wednesday was
    left reading "plan ready Fridays" over a Wednesday shop: the list
    promised two days AFTER the trip it is for, which is the one thing this
    card exists to prevent.
    """
    tools.set_shop_days(shop_day="saturday")
    assert tools.get_household_rhythm()["planning_anchor"] == "friday"
    tools.set_shop_days(shop_day="wednesday")
    assert tools.get_household_rhythm()["planning_anchor"] == "tuesday"


def test_a_plan_ready_day_a_person_gave_never_follows_the_shop_day():
    """
    GUARD, and the other half of the rule above — the household answered
    that question themselves, so a later shop day must not quietly overrule
    them. Pinned by the mutation that drops the source check and re-defaults
    unconditionally, which reddens this.
    """
    tools.set_planning_anchor("sunday")
    before = tools.get_household_rhythm()["planning_anchor"]
    tools.set_shop_days(shop_day="wednesday")
    assert tools.get_household_rhythm()["planning_anchor"] == before


def test_clearing_the_shop_day_leaves_the_plan_ready_day_alone():
    """
    GUARD — the plan-ready day is its own question with its own chip row in
    Settings, and the household can see the answer. Taking it away when the
    shop day goes would lose an answer they are looking at.
    """
    tools.set_shop_days(shop_day="saturday")
    tools.set_shop_days(shop_day="")
    r = tools.get_household_rhythm()
    assert r["shop_day"] is None
    assert r["planning_anchor"] == "friday"
