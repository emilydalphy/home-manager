"""
Holidays, slice 1 (Loop Board "Holidays: Pomona knows 12 October is coming
and asks how you're spending it", Emily, 2026-09-11).

Pomona knows the household's holidays by rule (country + province) and
from its own calendar feed, asks once how the day is being spent, and each
answer has exactly one planning consequence — all built on machinery that
already existed (attendance, the intake's Hosting tag, plan_meal).
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app import agent, calendar_feed as cf, households, tools
from app.db import get_conn
from app.tools import holidays as hol
from app.tools._shared import DEFAULT_HOUSEHOLD_ID

REPO = Path(__file__).resolve().parents[1]
PLAN_WEEK = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
AGENT = (REPO / "app" / "agent.py").read_text(encoding="utf-8")


# ---------- the rules ----------

@pytest.mark.parametrize("year,expected", [(2026, "2026-10-12"), (2027, "2027-10-11")])
def test_thanksgiving_is_the_second_monday_of_october(year, expected):
    assert _named(tools.rule_holidays(year), "Thanksgiving") == expected


@pytest.mark.parametrize("year,expected", [(2026, "2026-04-05"), (2027, "2027-03-28")])
def test_easter_is_computed_not_listed(year, expected):
    found = tools.rule_holidays(year)
    assert _named(found, "Easter Sunday") == expected
    good_friday = date.fromisoformat(expected) - timedelta(days=2)
    assert _named(found, "Good Friday") == good_friday.isoformat()
    assert _named(found, "Easter Monday") == (date.fromisoformat(expected) + timedelta(days=1)).isoformat()


def test_the_other_moveable_days_land_where_the_calendar_says():
    found = tools.rule_holidays(2026, "CA", "ON")
    assert _named(found, "Family Day") == "2026-02-16"
    assert _named(found, "Victoria Day") == "2026-05-18"
    assert _named(found, "Labour Day") == "2026-09-07"
    assert _named(found, "Civic Holiday") == "2026-08-03"
    assert _named(found, "Mother’s Day") == "2026-05-10"
    assert _named(found, "Father’s Day") == "2026-06-21"
    assert _named(found, "Canada Day") == "2026-07-01"
    assert _named(found, "Christmas Day") == "2026-12-25"


def test_the_february_day_follows_the_province():
    assert _named(tools.rule_holidays(2027, "CA", "MB"), "Louis Riel Day") == "2027-02-15"
    assert _named(tools.rule_holidays(2027, "CA", "PE"), "Islander Day") == "2027-02-15"
    assert _named(tools.rule_holidays(2027, "CA", "NS"), "Heritage Day") == "2027-02-15"
    assert _named(tools.rule_holidays(2027, "CA", "BC"), "Family Day") == "2027-02-15"
    quebec = tools.rule_holidays(2027, "CA", "QC")
    assert not [h for h in quebec if h["date"] == "2027-02-15"], "Quebec has no February holiday"
    assert _named(quebec, "Thanksgiving") == "2027-10-11", "the national days still apply"


def test_quebec_and_newfoundland_keep_their_own_days():
    qc = tools.rule_holidays(2026, "CA", "QC")
    assert _named(qc, "National Patriots’ Day") == "2026-05-18" and _named(qc, "Victoria Day") is None
    assert _named(qc, "Fête nationale") == "2026-06-24" and _named(qc, "Civic Holiday") is None
    nl = tools.rule_holidays(2026, "CA", "NL")
    assert _named(nl, "Civic Holiday") is None and _named(nl, "Victoria Day") == "2026-05-18"
    blank = tools.rule_holidays(2026, "CA", "")
    assert _named(blank, "Victoria Day") and _named(blank, "Civic Holiday"), "a national day with no province on record"
    assert _named(blank, "Family Day") is None and _named(blank, "Fête nationale") is None


def test_a_country_with_no_table_gets_no_holidays_rather_than_canadas():
    assert tools.rule_holidays(2026, "US") == []
    with pytest.raises(ValueError):
        tools.set_holiday_region(country="US")


def test_the_region_is_a_household_setting_with_ontario_as_the_default():
    assert hol._region() == ("CA", "ON")
    assert tools.set_holiday_region(province="Nova Scotia") == {"country": "CA", "province": "NS"}
    year = date.today().year + 1
    assert _named(tools.holidays_for_period(f"{year}-02-01", 28), "Heritage Day")
    assert tools.set_holiday_region(province="bc")["province"] == "BC"
    assert _named(tools.holidays_for_period(f"{year}-02-01", 28), "Family Day")
    with pytest.raises(ValueError):
        tools.set_holiday_region(province="Narnia")


def test_a_holiday_in_the_period_comes_with_its_question_and_no_answer_yet():
    tg = _thanksgiving()
    found = tools.holidays_for_period(tg, 7)
    assert [h["name"] for h in found] == ["Thanksgiving"]
    assert found[0]["asks"] is True and found[0]["answer"] is None
    assert found[0]["question"] == "How are you spending Thanksgiving?"
    assert tools.holiday_on(tg)["date"] == tg
    assert tools.holiday_on(_shift(tg, 1)) is None


# ---------- the household's own calendar ----------

def _feed_with(day: str, title: str, *, all_day=True) -> str:
    from tests.test_calendar_feed import _google, _vevent
    d = date.fromisoformat(day)
    stamp = d.strftime("%Y%m%d")
    if all_day:
        return _google(_vevent(f"DTSTART;VALUE=DATE:{stamp}", "UID:h1", f"SUMMARY:{title}"))
    return _google(_vevent(f"DTSTART;TZID=America/Toronto:{stamp}T180000",
                           f"DTEND;TZID=America/Toronto:{stamp}T190000", "UID:h1", f"SUMMARY:{title}"))


def _connect(monkeypatch, ics: str):
    from tests.test_calendar_feed import SECRET_URL
    monkeypatch.setattr(cf, "fetch_feed", lambda url: ics)
    cf.connect(SECRET_URL, "Family")


def test_a_holiday_on_the_households_own_calendar_counts_the_same_way(monkeypatch):
    tg = _thanksgiving()
    diwali = _shift(tg, 3)  # an ordinary Thursday to the rule table
    _connect(monkeypatch, _feed_with(diwali, "Diwali"))
    found = {h["date"]: h for h in tools.holidays_for_period(tg, 7)}
    assert found[diwali]["name"] == "Diwali" and found[diwali]["source"] == "calendar"
    assert found[diwali]["asks"] is True and found[diwali]["question"] == "How are you spending Diwali?"
    assert found[tg]["source"] == "rule"
    # And it can be answered like any other.
    assert tools.answer_holiday(diwali, "just_us")["holiday"]["name"] == "Diwali"


def test_the_rule_table_wins_when_the_calendar_names_the_same_day(monkeypatch):
    tg = _thanksgiving()
    _connect(monkeypatch, _feed_with(tg, "Thanksgiving Day (Canada)"))
    found = tools.holidays_for_period(tg, 7)
    assert len(found) == 1 and found[0]["name"] == "Thanksgiving" and found[0]["source"] == "rule"


def test_a_pa_day_or_a_timed_event_is_not_a_holiday(monkeypatch):
    tg = _thanksgiving()
    _connect(monkeypatch, _feed_with(_shift(tg, 2), "PA day"))
    assert [h["date"] for h in tools.holidays_for_period(tg, 7)] == [tg]
    _connect(monkeypatch, _feed_with(_shift(tg, 2), "Christmas party", all_day=False))
    assert [h["date"] for h in tools.holidays_for_period(tg, 7)] == [tg]
    assert hol.looks_like_holiday("Birthday — Nana") is False


@pytest.mark.parametrize("title", [
    "Reid's birthday", "Heidi visiting", "Holiday Inn checkout", "Summer holidays",
    "School holiday", "Christmas concert rehearsal", "Easter egg hunt at school",
    "Family dinner", "Victoria arriving", "Eid party at Sam's",
])
def test_a_title_that_only_mentions_a_holiday_is_not_one(title, monkeypatch):
    """The verifier's false positives (2026-09-11): a bare substring match
    made every one of these an asking holiday. The whole title has to be
    the holiday's name."""
    assert hol.looks_like_holiday(title) is False
    tg = _thanksgiving()
    _connect(monkeypatch, _feed_with(_shift(tg, 2), title))
    assert [h["date"] for h in tools.holidays_for_period(tg, 7)] == [tg]


@pytest.mark.parametrize("title", [
    "Thanksgiving Day (Canada)", "Christmas Day", "New Year’s Day (observed)", "Eid al-Fitr",
    "Diwali", "Boxing Day", "Rosh Hashanah", "Family Day",
])
def test_a_title_that_is_a_holidays_name_counts(title):
    assert hol.looks_like_holiday(title) is True


def test_a_multi_day_all_day_event_is_a_stretch_not_a_holiday(monkeypatch):
    """"Reid at camp" Thu–Sun used to spawn one asking holiday per expanded
    day. A span is skipped outright; a one-day holiday beside it still counts."""
    from tests.test_calendar_feed import _google, _vevent
    tg = _thanksgiving()
    start = date.fromisoformat(_shift(tg, 3))
    end = start + timedelta(days=4)  # DTEND is exclusive: four days
    ics = _google(
        _vevent(f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}", "UID:camp", "SUMMARY:Diwali")
        + _vevent(f"DTSTART;VALUE=DATE:{date.fromisoformat(_shift(tg, 1)):%Y%m%d}", "UID:one", "SUMMARY:Eid")
    )
    _connect(monkeypatch, ics)
    found = tools.holidays_for_period(tg, 7)
    assert [h["name"] for h in found] == ["Thanksgiving", "Eid"]
    assert sum(1 for h in found if h["source"] == "calendar") <= 1


def test_a_calendar_that_cannot_be_read_is_a_missing_label_not_a_missing_week(monkeypatch):
    tg = _thanksgiving()
    _connect(monkeypatch, _feed_with(_shift(tg, 3), "Diwali"))
    conn = get_conn()
    conn.execute("UPDATE calendar_feeds SET last_fetched_at = NULL, cache_json = '[]', cache_from = '', cache_to = ''")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cf, "fetch_feed", lambda url: (_ for _ in ()).throw(cf.CalendarFeedError("down")))
    assert [h["date"] for h in tools.holidays_for_period(tg, 7)] == [tg]


# ---------- the intake asks, once ----------

def test_the_intake_prefill_carries_the_holiday_and_the_screen_asks_with_four_answers(signed_in):
    tg = _thanksgiving()
    body = signed_in.get(f"/api/week/{tg}/intake").json()
    assert [h["name"] for h in body["holidays"]] == ["Thanksgiving"]
    assert body["holidays"][0]["answer"] is None
    # The four answers, as the household sees them, in one place.
    assert tools.HOLIDAY_ANSWER_LABELS == {
        "hosting": "Hosting", "out": "Going to someone’s", "just_us": "Just us", "unsure": "Not sure yet",
    }
    for label in tools.HOLIDAY_ANSWER_LABELS.values():
        assert label in PLAN_WEEK, label
    assert "function holidayBlockHtml(" in PLAN_WEEK
    assert "/api/holidays/answer" in PLAN_WEEK
    assert "event mode" not in PLAN_WEEK.lower()
    # Once answered, the prefill hands the answer back — the block renders
    # it selected instead of asking again.
    signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "just_us"})
    body = signed_in.get(f"/api/week/{tg}/intake").json()
    assert body["holidays"][0]["answer"]["answer"] == "just_us"


def test_now_asks_again_from_three_days_out_and_at_most_once_a_day():
    tg = _thanksgiving()
    tg_date = date.fromisoformat(tg)
    assert tools.holiday_needs_you_item(today=tg_date - timedelta(days=4)) is None, "too far out"
    ask = tools.holiday_needs_you_item(today=tg_date - timedelta(days=3))
    assert ask and ask["type"] == "holiday_ask" and ask["date"] == tg
    assert ask["title"] == "Thanksgiving is on Monday — how are you spending it?"
    assert [o["answer"] for o in ask["options"]] == ["hosting", "out", "just_us", "unsure"]
    assert "!" not in ask["title"]

    # "Not sure yet" is a real answer — for the day it was given.
    tools.answer_holiday(tg, "unsure")
    _answered_on(tg_date - timedelta(days=2))
    assert tools.holiday_needs_you_item(today=tg_date - timedelta(days=2)) is None
    # The next day it asks again.
    assert tools.holiday_needs_you_item(today=tg_date - timedelta(days=1))["date"] == tg
    assert tools.holiday_needs_you_item(today=tg_date)["title"].startswith("Thanksgiving is today")
    # A real answer ends the asking.
    tools.answer_holiday(tg, "just_us")
    assert tools.holiday_needs_you_item(today=tg_date - timedelta(days=1)) is None


def test_the_needs_you_band_carries_the_ask(signed_in, monkeypatch):
    tg = _thanksgiving()
    monkeypatch.setattr(tools.weekly_plan, "date", _FrozenDate.at(date.fromisoformat(tg) - timedelta(days=2)))
    items = tools.get_needs_you_items()
    assert items and items[0]["type"] == "holiday_ask" and items[0]["date"] == tg
    assert "holiday_ask" in SHELL_JS and "data-card-type=\"holiday_ask\"" in SHELL_JS


def test_a_label_only_holiday_is_shown_but_never_asked_about():
    year = date.today().year + 1
    halloween = f"{year}-10-31"
    h = tools.holiday_on(halloween)
    assert h["name"] == "Halloween" and h["asks"] is False
    assert tools.holiday_needs_you_item(today=date.fromisoformat(halloween)) is None
    assert tools.holiday_day_label(h) == "Halloween"


# ---------- each answer's one consequence ----------

@pytest.fixture
def family():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Vineeth")
    tools.set_member_age_group("Vineeth", "adult")


@pytest.fixture
def recipe():
    tools.add_recipe("Sweet Potato Casserole", ingredients=[{"item": "sweet potatoes", "qty": "4"}],
                     prep_time_minutes=20, cook_time_minutes=40, default_servings=2)
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}], prep_time_minutes=10, cook_time_minutes=20)


def test_going_to_someones_takes_that_dinner_off_the_week_through_attendance(family, recipe):
    tg = _thanksgiving()
    plan = tools.create_weekly_plan(tg)
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"], add_ingredients_to_grocery_list=True)
    assert "beans" in _list()

    result = tools.answer_holiday(tg, "out")

    assert result["dinner"] == "out"
    assert result["ack"] == hol.ANSWER_ACKS["out"]
    att = tools.get_slot_attendance(tg, "dinner")
    assert att["nobody_home"] and att["source"] == "holiday"
    need = tools.get_slot_need(tg, "dinner")
    assert need["need"] == "away" and need["reason"] == "You’re out for Thanksgiving — nothing to cook or buy."
    assert _slot(plan["weekly_plan_id"], tg)["slot_state"] == "planned_empty"
    assert "beans" not in _list(), "nothing bought for a dinner they're out for"
    # Lunch is untouched: they're home for it.
    assert tools.get_slot_attendance(tg, "lunch")["everyone_home"]


def test_going_to_someones_before_any_plan_exists_is_enforced_at_generation(family, recipe, stub_model):
    tg = _thanksgiving()
    tools.answer_holiday(tg, "out")
    seen = stub_model(_full_week(tg, meal="Chili"))
    plan = agent.generate_weekly_plan(tg)
    assert seen["context"]["holidays"] == [{
        "date": tg, "name": "Thanksgiving", "answer": "out",
        "plan": "they eat dinner elsewhere; send no dinner entry for this date",
    }]
    assert {s["date"] for s in seen["context"]["slot_needs"]["away_slots"]} == {tg}
    # The model sent a dinner anyway; the answer won.
    assert _slot(plan["weekly_plan_id"], tg)["slot_state"] == "planned_empty"
    assert _slot(plan["weekly_plan_id"], _shift(tg, 1))["slot_state"] == "planned"


def test_bringing_a_dish_plans_it_into_that_dinner_with_its_groceries(family, recipe):
    tg = _thanksgiving()
    plan = tools.create_weekly_plan(tg)
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")
    assert "beans" in _list()

    result = tools.answer_holiday(tg, "out", bring_dish="sweet potato casserole")

    assert result["dinner"] == "dish" and result["dish_planned"] is True
    assert result["bring_dish"] == "Sweet Potato Casserole", "matched the saved recipe by name"
    assert result["bring_dish_recipe_id"] is not None
    row = _slot(plan["weekly_plan_id"], tg)
    assert row["slot_state"] == "planned" and row["meal"] == "Sweet Potato Casserole"
    assert row["reasoning"] == "You’re taking this to Thanksgiving."
    assert json.loads(row["derived_from_json"])["holiday_dish"] is True
    items = _list()
    assert "sweet potatoes" in items and "beans" not in items, "the chili it replaced came off the list"
    # Attendance is left alone: the dish is what they cook that day.
    assert tools.get_slot_attendance(tg, "dinner")["everyone_home"]
    # The Meals screen sees it, with the holiday label on the day.
    menu = tools.get_week_menu(plan["weekly_plan_id"])
    day = [d for d in menu["days"] if d["date"] == tg][0]
    assert day["dinner"]["title"] == "Sweet Potato Casserole"
    assert day["holiday"] == {"name": "Thanksgiving", "answer": "out", "asks": True,
                              "label": "Thanksgiving · going to someone’s"}
    assert "holiday" not in [d for d in menu["days"] if d["date"] != tg][0]


def test_a_dish_named_before_the_week_exists_lands_when_the_week_is_generated(family, recipe, stub_model):
    tg = _thanksgiving()
    result = tools.answer_holiday(tg, "out", bring_dish="my mom’s stuffing")
    assert result["dish_planned"] is False and result["bring_dish_recipe_id"] is None
    seen = stub_model(_full_week(tg, meal="Chili"))
    plan = agent.generate_weekly_plan(tg)
    assert seen["context"]["holidays"][0]["bring_dish"] == "my mom’s stuffing"
    row = _slot(plan["weekly_plan_id"], tg)
    assert row["slot_state"] == "planned" and row["meal"] == "my mom’s stuffing"
    # Kitchen's cook that day is the dish, and the day's label says why.
    assert tools.today_moves(tg)["holiday"]["label"] == "Thanksgiving · going to someone’s"


def test_just_us_changes_nothing(family, recipe):
    tg = _thanksgiving()
    plan = tools.create_weekly_plan(tg)
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    result = tools.answer_holiday(tg, "just_us")
    assert result["dinner"] == "unchanged"
    assert _slot(plan["weekly_plan_id"], tg)["slot_state"] == "planned"
    assert not tools.get_slot_attendance(tg, "dinner")["explicit"]
    assert tools.get_slot_need(tg, "dinner")["need"] == "normal"
    assert tools.get_week_intake(tg) is None
    assert tools.holiday_day_label(tools.holiday_on(tg)) == "Thanksgiving · just us"


def test_hosting_sets_the_intakes_hosting_tag_and_headcount(family, recipe):
    tg = _thanksgiving()
    tools.save_week_intake(tg, night_tags={_shift(tg, 2): ["rush"]})

    result = tools.answer_holiday(tg, "hosting", headcount=5)

    assert result["dinner"] == "hosting" and result["hosting"] == "intake_tag"
    intake = tools.get_week_intake(tg)
    assert intake["night_tags"][tg] == ["guests"] and intake["night_tags"][_shift(tg, 2)] == ["rush"]
    assert intake["guest_counts"][tg] == {"adults": 5, "children": 0}
    assert intake["revision"] == 2, "through save_week_intake — a new revision, never an edit in place"
    att = tools.get_slot_attendance(tg, "dinner")
    assert att["guest_count"] == 5 and att["headcount"] == 7
    assert tools.holiday_day_label(tools.holiday_on(tg)) == "Thanksgiving · hosting · 5 more"
    # The seam for slice 2: the answer and the headcount are stored.
    assert tools.get_holiday_answer(tg)["headcount"] == 5


def test_hosting_with_no_intake_yet_reaches_attendance_directly(family):
    tg = _thanksgiving()
    result = tools.answer_holiday(tg, "hosting", headcount=3)
    assert result["hosting"] == "attendance"
    assert tools.get_slot_attendance(tg, "dinner")["guest_count"] == 3
    assert tools.get_week_intake(tg) is None


def test_the_planner_is_told_the_holiday_and_the_answer(family, recipe, stub_model):
    tg = _thanksgiving()
    tools.answer_holiday(tg, "hosting", headcount=4)
    seen = stub_model(_full_week(tg, meal="Chili"))
    agent.generate_weekly_plan(tg)
    line = seen["context"]["holidays"][0]
    assert line["name"] == "Thanksgiving" and line["answer"] == "hosting" and line["extra_guests"] == 4
    assert "`holidays`, when present" in AGENT
    assert "never \"event mode\"" in AGENT or 'never "event mode"' in AGENT


def test_a_period_with_no_holiday_sends_nothing_new_to_the_prompt(family, recipe, stub_model):
    week = _shift(_thanksgiving(), 21)  # the first week of November: nothing on it
    seen = stub_model(_full_week(week, meal="Chili"))
    agent.generate_weekly_plan(week)
    assert "holidays" not in seen["context"]


def test_changing_the_answer_undoes_the_old_one(family, recipe):
    tg = _thanksgiving()
    plan = tools.create_weekly_plan(tg)
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.answer_holiday(tg, "out")
    assert _slot(plan["weekly_plan_id"], tg)["slot_state"] == "planned_empty"

    tools.answer_holiday(tg, "just_us")

    assert not tools.get_slot_attendance(tg, "dinner")["explicit"]
    assert tools.get_slot_need(tg, "dinner")["need"] == "normal"
    row = _slot(plan["weekly_plan_id"], tg)
    assert row["slot_state"] == "open", "a dinner nobody has chosen yet is a question, not a blank"

    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")
    assert _slot(plan["weekly_plan_id"], tg)["meal"] == "Sweet Potato Casserole"
    tools.answer_holiday(tg, "hosting", headcount=2)
    assert _slot(plan["weekly_plan_id"], tg)["slot_state"] == "open"
    assert tools.get_slot_attendance(tg, "dinner")["guest_count"] == 2
    tools.answer_holiday(tg, "unsure")
    assert tools.get_slot_attendance(tg, "dinner")["guest_count"] == 0
    assert tools.get_holiday_answer(tg)["headcount"] == 0


def test_a_trip_already_covering_the_day_is_left_alone(family):
    tg = _thanksgiving()
    tools.set_away_stretch(_shift(tg, -1), "dinner", tg, "dinner")
    result = tools.answer_holiday(tg, "out")
    assert result["dinner"] == "already_out"
    assert tools.get_slot_attendance(tg, "dinner")["source"] != "holiday"
    tools.answer_holiday(tg, "just_us")
    assert tools.get_slot_attendance(tg, "dinner")["nobody_home"], "the trip is theirs, not the holiday's"


def test_hosting_then_just_us_on_a_trip_covered_day_leaves_the_trip_intact(family, recipe):
    """The verifier's find: the hosting fallback wrote over the trip's
    attendance row, and clearing it re-derived a generic away with no
    stretch link — so a dinner planned later bought groceries for a night
    the household is away."""
    tg = _thanksgiving()
    stretch = tools.set_away_stretch(_shift(tg, -1), "dinner", tg, "dinner")
    before_att = tools.get_slot_attendance(tg, "dinner")
    before_need = tools.get_slot_need(tg, "dinner")
    assert before_att["nobody_home"] and before_need["need"] == "away" and before_need["away_stretch_id"]

    assert tools.answer_holiday(tg, "hosting", headcount=4)["hosting"] == "already_out"
    assert tools.get_holiday_answer(tg)["headcount"] == 4, "recorded for slice 2"
    tools.answer_holiday(tg, "just_us")

    assert tools.get_slot_attendance(tg, "dinner") == before_att
    assert tools.get_slot_need(tg, "dinner") == before_need
    plan = tools.create_weekly_plan(tg)
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.apply_slot_needs_to_plan(plan["weekly_plan_id"], tg)
    tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")
    assert _slot(plan["weekly_plan_id"], tg)["slot_state"] == "planned_empty"
    assert "beans" not in _list()


def test_out_covers_a_quick_need_and_undo_puts_it_back(family):
    tg = _thanksgiving()
    tools.set_slot_need(tg, "dinner", "quick", reason="Grab-and-go before the drive.")
    tools.answer_holiday(tg, "out")
    need = tools.get_slot_need(tg, "dinner")
    assert need["need"] == "away" and need["superseded_need"] == "quick"
    tools.answer_holiday(tg, "just_us")
    need = tools.get_slot_need(tg, "dinner")
    assert need["need"] == "quick" and need["reason"] == "Grab-and-go before the drive."


def test_out_covers_a_quick_need_with_no_members_on_record_too():
    tg = _thanksgiving()
    tools.set_slot_need(tg, "dinner", "quick")
    tools.answer_holiday(tg, "out")
    assert tools.get_slot_need(tg, "dinner")["need"] == "away"
    tools.answer_holiday(tg, "unsure")
    assert tools.get_slot_need(tg, "dinner")["need"] == "quick"


def test_the_once_a_day_gate_runs_on_the_households_clock():
    """A "not sure yet" at nine in the evening in Toronto is stamped as
    the next day in UTC. It still counts as today's answer, and tomorrow
    still asks."""
    tg = _thanksgiving()
    tg_date = date.fromisoformat(tg)
    tools.answer_holiday(tg, "unsure")
    evening = tg_date - timedelta(days=2)
    conn = get_conn()
    # 21:00 Toronto (EDT, UTC-4) is 01:00 UTC the following day.
    conn.execute("UPDATE holiday_answers SET updated_at = ?", ((evening + timedelta(days=1)).isoformat() + " 01:00:00",))
    conn.commit()
    conn.close()
    assert hol._answered_on(tools.get_holiday_answer(tg)["updated_at"]) == evening
    assert tools.holiday_needs_you_item(today=evening) is None, "answered this evening, local time"
    assert tools.holiday_needs_you_item(today=evening + timedelta(days=1))["date"] == tg


def test_an_ordinary_day_cannot_be_answered():
    with pytest.raises(ValueError):
        tools.answer_holiday(_shift(_thanksgiving(), 1), "out")
    with pytest.raises(ValueError):
        tools.answer_holiday(_thanksgiving(), "party")


# ---------- routes and households ----------

def test_the_routes_need_a_signed_in_household(client):
    tg = _thanksgiving()
    assert client.get("/api/holidays").status_code in (401, 303)
    assert client.post("/api/holidays/answer", json={"date": tg, "answer": "out"}).status_code in (401, 303)


def test_the_routes_round_trip(signed_in, family):
    tg = _thanksgiving()
    body = signed_in.get(f"/api/holidays?start={tg}&days=7").json()
    assert [h["name"] for h in body["holidays"]] == ["Thanksgiving"]
    res = signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "headcount": 6})
    assert res.status_code == 200 and res.json()["answer_label"] == "Hosting"
    assert signed_in.get(f"/api/holidays?start={tg}&days=7").json()["holidays"][0]["answer"]["headcount"] == 6
    assert signed_in.post("/api/holidays/answer", json={"date": _shift(tg, 1), "answer": "out"}).status_code == 400
    assert signed_in.get("/api/holidays?start=not-a-date").status_code == 400


def test_one_households_answer_never_reaches_another():
    tg = _thanksgiving()
    other = households.create_household("The Beta Testers", "beta-tester-passphrase")
    tools.answer_holiday(tg, "out")
    with tools.use_household(other):
        assert tools.holiday_on(tg)["answer"] is None
        assert not tools.get_slot_attendance(tg, "dinner")["explicit"]
        tools.answer_holiday(tg, "hosting", headcount=9)
        assert tools.get_holiday_answer(tg)["headcount"] == 9
    with tools.use_household(DEFAULT_HOUSEHOLD_ID):
        assert tools.get_holiday_answer(tg)["answer"] == "out"


# ---------- the label on the day ----------

def test_the_plan_and_now_carry_a_quiet_label_in_the_neutral_pill():
    assert "day.holiday" in SHELL_JS and "wk-holiday" in SHELL_JS
    assert ".wk-holiday" in SHELL_CSS and ".today-holiday" in SHELL_CSS
    assert "today-holiday" in SHELL_JS
    for src in (SHELL_JS, PLAN_WEEK):
        assert "event mode" not in src.lower()


# ---------- helpers ----------

def _thanksgiving() -> str:
    """Next year's Thanksgiving — always ahead of today, so the plan it sits in is live."""
    return _named(tools.rule_holidays(date.today().year + 1), "Thanksgiving")


def _named(found: list[dict], name: str) -> str | None:
    return next((h["date"] for h in found if h["name"] == name), None)


def _shift(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def _answered_on(day: date) -> None:
    conn = get_conn()
    conn.execute("UPDATE holiday_answers SET updated_at = ?", (day.isoformat() + " 12:00:00",))
    conn.commit()
    conn.close()


def _list() -> list[str]:
    return [i["item"] for i in tools.list_grocery_list()]


def _slot(plan_id: int, day: str, slot: str = "dinner"):
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.slot_state, mpe.reasoning, mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = ?",
        (plan_id, day, slot),
    ).fetchall()
    conn.close()
    assert len(row) == 1, f"{day} {slot} holds {len(row)} rows"
    return row[0]


def _full_week(week: str, meal: str = "Chili") -> list[dict]:
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False, "reasoning": "fits the week"}
        for day in tools._week_dates(week)
        for slot in tools.WEEK_SLOTS
    ]


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


class _FrozenDate(date):
    """date.today() pinned, for the needs-you band's own clock."""
    _today = None

    @classmethod
    def at(cls, day: date):
        cls._today = day
        return cls

    @classmethod
    def today(cls):
        return cls._today
