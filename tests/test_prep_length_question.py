"""
Cook ahead asks "How long?" again (2026-11-01): up to 1, 2, 3 or 4 hours per
prep day, stored as that day's minutes (60/120/180/240) in the prep_days
fact, and this week's answer reaches the prep sessions as `minutes`.
"""
import json
import pathlib
import re

import pytest

from app import tools
from app.tools import usual_week

_ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = (_ROOT / "static" / "setup-screens.js").read_text(encoding="utf-8")
ONBOARDING = (_ROOT / "static" / "onboarding.html").read_text(encoding="utf-8")
PLAN_WEEK = (_ROOT / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL = (_ROOT / "static" / "shell.js").read_text(encoding="utf-8")


# ---------- the words ----------


@pytest.mark.parametrize("minutes,words", [
    (60, "up to 1 hour"), (120, "up to 2 hours"), (180, "up to 3 hours"), (240, "up to 4 hours"),
])
def test_prep_minutes_label_says_the_four_lengths(minutes, words):
    assert tools.prep_minutes_label(minutes) == words


def test_prep_minutes_label_keeps_sensible_words_for_legacy_numbers():
    assert tools.prep_minutes_label(None) == ""
    assert tools.prep_minutes_label(30) == "about half an hour"
    assert tools.prep_minutes_label(45) == "about an hour"
    assert tools.prep_minutes_label(90) == "up to 2 hours"
    assert tools.prep_minutes_label(300) == "up to 5 hours"


def test_the_summary_names_each_days_own_length():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 180}, {"weekday": "wednesday", "minutes": 60}])
    assert tools.prep_days_summary() == "Preps on Sunday (up to 3 hours) and Wednesday (up to 1 hour)."


# ---------- stored per day ----------


def test_the_usual_week_saves_each_days_minutes():
    usual_week.save_usual_week(prep={"days": ["sunday", "wednesday"], "minutes": {"sunday": 180, "wednesday": 60}})
    days = tools.get_household_rhythm()["prep_days"]
    assert [(d["weekday"], d["minutes"]) for d in days] == [("sunday", 180), ("wednesday", 60)]
    answer = usual_week.get_usual_week()["prep"]
    assert answer["minutes"] == {"sunday": 180, "wednesday": 60}


def test_a_day_with_no_minutes_is_two_hours_and_the_old_length_word_still_works():
    usual_week.save_usual_week(prep={"days": ["sunday", "wednesday"], "minutes": {"sunday": 240}})
    days = {d["weekday"]: d["minutes"] for d in tools.get_household_rhythm()["prep_days"]}
    assert days == {"sunday": 240, "wednesday": 120}
    usual_week.save_usual_week(prep={"days": ["sunday"], "length": "hour"})
    assert tools.get_household_rhythm()["prep_days"][0]["minutes"] == 60


def test_a_length_that_is_not_a_chip_is_refused_in_words():
    with pytest.raises(ValueError, match="up to 1, 2, 3 or 4 hours"):
        usual_week.save_usual_week(prep={"days": ["sunday"], "minutes": {"sunday": 90}})


def test_a_legacy_odd_number_reads_as_the_nearest_chip_up():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])
    assert usual_week.get_usual_week()["prep"]["minutes"] == {"sunday": 120}


# ---------- this week only ----------


def test_this_weeks_answer_reaches_the_prep_session_minutes():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 120}])
    week = usual_week.normalize_week_answers({"prep": {"days": ["sunday", "wednesday"], "minutes": {"sunday": 180, "wednesday": 60}}})
    assert week["prep"] == {"days": ["sunday", "wednesday"], "minutes": {"sunday": 180, "wednesday": 60}}
    with usual_week.this_week(week):
        days = tools.get_household_rhythm()["prep_days"]
        assert [(d["weekday"], d["minutes"]) for d in days] == [("sunday", 180), ("wednesday", 60)]
        assert usual_week.get_usual_week()["prep"]["minutes"] == {"sunday": 180, "wednesday": 60}
    # The standing answer is untouched.
    assert [(d["weekday"], d["minutes"]) for d in tools.get_household_rhythm()["prep_days"]] == [("sunday", 120)]


def test_a_this_week_answer_with_no_minutes_is_two_hours_a_day():
    week = usual_week.normalize_week_answers({"prep": {"days": ["sunday"]}})
    with usual_week.this_week(week):
        assert tools.get_household_rhythm()["prep_days"][0]["minutes"] == 120


# ---------- the screens ----------


def test_the_cook_ahead_screen_has_a_how_long_block_with_four_chips():
    assert 'id="prep-length"' in ONBOARDING
    assert "const PREP_MINUTES = [60, 120, 180, 240];" in SETUP
    assert "'How long?'" in SETUP and "'How long on ' + uwDayName(d) + '?'" in SETUP
    assert "Up to ${m / 60} hr" in SETUP
    assert "repeat(4, minmax(0, 1fr))" in ONBOARDING
    assert "minutes: currentPrepMinutes()" in ONBOARDING
    assert "PREP_LENGTH" not in SETUP and "PREP_LENGTH" not in ONBOARDING


def test_the_same_as_last_week_row_says_the_length_picked():
    assert "up to 2 hours" not in PLAN_WEEK.split("var SAME_COPY")[1][:6000]
    assert "prepLength" not in PLAN_WEEK
    assert "keepWeek('prep', { days: days, minutes: currentPrepMinutes() }" in PLAN_WEEK
    assert "prepHoursWords" in PLAN_WEEK


def test_settings_says_the_real_length_not_a_fixed_two_hours():
    assert "Each prep day is planned for up to 2 hours." not in SHELL
    assert "UW_PREP_LENGTH_WORDS" not in SHELL
    assert "uwPrepHours" in SHELL


_NODE = __import__("shutil").which("node")


@pytest.mark.skipif(not _NODE, reason="node not installed")
def test_the_readback_and_row_words_in_node():
    import subprocess

    def grab(src, name):
        m = re.search(r"^(?:async )?function %s\(.*?^}" % name, src, re.S | re.M)
        assert m, name
        return m.group(0)

    code = "\n".join([
        "const PREP_MINUTES = [60,120,180,240]; const PREP_MINUTES_DEFAULT = 120;",
        "const UW_WEEKDAYS=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];",
        "const UW_DAY_NAMES=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];",
        grab(ONBOARDING, "revealJoinWords"),
        grab(SETUP, "uwDayName"), grab(SETUP, "prepHoursWords"), grab(SETUP, "prepReadback"),
        "console.log(JSON.stringify([prepReadback(['sunday'], true, {sunday: 120}),"
        " prepReadback(['sunday'], false, {sunday: 60}),"
        " prepReadback(['sunday','wednesday'], true, {sunday: 120, wednesday: 60})]));",
    ])
    out = json.loads(subprocess.run([_NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
    assert out[0].startswith("Sunday, up to 2 hours. ")
    assert out[1].startswith("Sunday, up to 1 hour. ")
    assert out[2].startswith("Sunday, up to 2 hours and Wednesday, up to 1 hour. ")
