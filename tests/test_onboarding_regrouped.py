"""
Onboarding, regrouped (Loop Board, Emily's locked flow of 2026-10-05):
household -> schedule -> breakfast -> lunch -> dinner -> snacks -> the
rest, with two new screens (Weekday lunches, Snacks a day), "Dietary
restrictions" with an Allergy picker, Cook ahead without "How long?",
and Dinner timings as the dinner half of the old dinner-time step.

Slice 1 (branch overnight/onboarding-regrouped). The page's own functions
run under node, the saves through the real routes.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("test_onboarding_go_back", _HERE / "test_onboarding_go_back.py")
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["test_onboarding_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_needs_node, _fn, _const, _run = _go_back._needs_node, _go_back._fn, _go_back._const, _go_back._run
_step_markup = _go_back._step_markup

ONBOARDING = (_HERE.parent / "static" / "onboarding.html").read_text()


def _steps():
    return json.loads(_const("ALL_STEPS").split("=", 1)[1].strip().rstrip(";").replace("'", '"'))


# ---------- the order and the sections ----------


def test_the_flow_is_household_schedule_then_meal_by_meal_then_the_rest():
    assert _steps()[4:] == [
        "your-name", "household", "helpers", "restrictions",          # Your household
        "meals-days", "shop-day", "prep",                             # Your schedule
        "variety-breakfast",                                          # Breakfast
        "lunch-needs", "variety-lunch",                               # Lunch
        "dinner-time", "variety-dinner",                              # Dinner
        "snacks",                                                     # Snacks
        "eating-style", "wont-eat", "excited-about", "kit-repeats", "ai-consent", "anything-else",  # The rest
        "reveal",
    ]


@_needs_node
def test_the_header_names_the_section():
    out = _run(_const("QUESTION_SECTIONS") + _const("SECTION_NAMES") + _const("SECTION_COUNT") + _fn("stepEyebrow") + """
console.log(JSON.stringify(['your-name', 'restrictions', 'meals-days', 'prep', 'variety-breakfast', 'lunch-needs',
  'variety-lunch', 'dinner-time', 'variety-dinner', 'snacks', 'eating-style', 'anything-else'].map(stepEyebrow)));
""")
    assert out == ["Your household", "Your household", "Your schedule", "Your schedule", "Breakfast", "Lunch",
                   "Lunch", "Dinner", "Dinner", "Snacks", "The rest", "The rest"]


# ---------- screen changes ----------


def test_dietary_restrictions_title_and_the_allergy_picker():
    markup = _step_markup("step-restrictions")
    assert '<h1 class="q-title">Dietary restrictions</h1>' in markup
    assert "Allergies, must-avoids, the way someone eats." in markup  # the subtitle stays
    assert _const("ALLERGEN_OPTIONS") == (
        "const ALLERGEN_OPTIONS = ['Nuts', 'Peanuts', 'Dairy', 'Eggs', 'Gluten', 'Sesame', 'Fish', 'Shellfish', 'Soy'];"
    )
    builder = _fn("buildRestrictionsStep")
    assert 'placeholder="Something else? Type it here"' in builder
    assert 'placeholder="Anything else I should always follow?"' in builder


@_needs_node
def test_a_picked_allergen_is_stored_as_an_allergy_like_a_typed_one():
    out = _run("""
let restrictionAnswers = { Asha: { chips: ['Allergy'], allergens: ['Shellfish', 'Nuts'], allergy: 'kiwi', other: '' },
                           Ravi: { chips: ['Vegetarian'], allergens: ['Eggs'], allergy: '', other: '' } };
function currentMembers() { return [{ name: 'Asha' }, { name: 'Ravi' }]; }
function pruneRestrictionAnswers() {}
""" + _const("ALLERGEN_OPTIONS") + _fn("currentRestrictions") + """
console.log(JSON.stringify(currentRestrictions()));
""")
    # Picker order, lower-case, then the typed box. Ravi's allergen tap
    # without the Allergy chip on says nothing (the picker is hidden).
    assert out == {"Asha": ["allergy: nuts", "allergy: shellfish", "allergy: kiwi"], "Ravi": ["Vegetarian"]}


def test_whos_eating_loses_the_weekday_lunch_line_and_the_snacks_row():
    for gone in ("uwLunchLine", "uwSnacksCardHtml", "data-uw-snacks", "data-uw-lunch", "packs it"):
        assert gone not in ONBOARDING, gone


def test_cook_ahead_has_no_how_long_and_every_prep_day_is_two_hours():
    assert "How long?" not in _step_markup("step-prep")
    assert "const PREP_LENGTH = 'longer';" in ONBOARDING
    assert "length: PREP_LENGTH" in _fn("usualWeekPayload")


def test_dinner_timings_is_dinner_time_and_the_weeknight_limit_only():
    markup = _step_markup("step-dinner-time")
    assert 'id="rhythm-dinner-window-chips"' in markup and 'id="weeknight-max-chips"' in markup
    assert "lunch-max" not in markup
    assert "'dinner-time': 'Dinner timings'" in ONBOARDING


def test_weekday_lunches_screen_words_and_options():
    markup = _step_markup("step-lunch-needs")
    assert "What does each person need for weekday lunches?" in markup
    assert "So I only plan lunches that work where they&rsquo;re eaten." in markup
    assert "Made fresh: how long can it take?" in markup
    opts = _const("LUNCH_NEED_OPTIONS")
    for words in ("Cold packed", "Warm in a thermos", "Something to reheat", "Made fresh", "Nut-free environment"):
        assert words in opts
    assert "Different on some days?" in _fn("buildLunchNeedsStep")
    lunch_max = _const("LUNCH_MAX_OPTIONS")
    assert "10 min" not in lunch_max
    assert [l for l in ("20 min", "30 min", "45 min", "No limit") if l in lunch_max] == ["20 min", "30 min", "45 min", "No limit"]


def _lunch_harness(grid_lunch):
    return """
const UW_WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
let usualGrid = { lunch: %s };
let lunchNeeds = {};
function currentMembers() { return [{ name: 'Gowthami', age_group: 'adult' }, { name: 'Ravi', age_group: 'adult' }, { name: 'Arjun', age_group: 'child' }]; }
""" % json.dumps(grid_lunch) + _const("LUNCH_NEED_OPTIONS") + _const("LUNCH_NEED_DAYS") + "".join(
        _fn(n) for n in ("uwCleanCell", "lunchPeople", "lunchEntry", "lunchNeedsAnswered", "toggleNeed",
                         "someoneHasMadeFresh", "lunchNeedsPayload", "uwIsOn", "uwWeekdayLunchOn"))


@_needs_node
def test_lunch_needs_rows_and_payload_follow_whos_eating():
    # Ravi buys lunch: he is off every weekday on Who's eating, so no row.
    row = [["Gowthami", "Arjun"]] * 5 + ["all", "all"]
    out = _run(_lunch_harness(row) + """
const people = lunchPeople();
lunchEntry('Gowthami').needs = toggleNeed([], 'reheat');
lunchEntry('Arjun').needs = toggleNeed(toggleNeed([], 'nut_free'), 'thermos');
const before = someoneHasMadeFresh();
lunchEntry('Arjun').days = { monday: ['thermos', 'nut_free'], tuesday: ['made_fresh'], wednesday: [], thursday: [], friday: [] };
console.log(JSON.stringify({ people: people, before: before, after: someoneHasMadeFresh(), payload: lunchNeedsPayload(),
  weekdayOn: uwWeekdayLunchOn(usualGrid) }));
""")
    assert out["people"] == ["Gowthami", "Arjun"]
    assert out["before"] is False and out["after"] is True
    assert out["payload"]["Gowthami"] == {"needs": ["reheat"], "days": {}}
    # Stored in Emily's order whatever order they were tapped in.
    assert out["payload"]["Arjun"]["needs"] == ["thermos", "nut_free"]
    assert out["payload"]["Arjun"]["days"]["tuesday"] == ["made_fresh"]
    assert "Ravi" not in out["payload"]
    assert out["weekdayOn"] is True


@_needs_node
def test_snacks_defaults_children_two_adults_one_and_the_household_number_is_the_most():
    out = _run("""
let memberSnacks = {};
let members = [{ name: 'Gowthami', age_group: 'adult' }, { name: 'Arjun', age_group: 'child' }];
function currentMembers() { return members; }
""" + _const("SNACK_OPTIONS") + _const("INFANT_UNDER_YEARS") + "".join(_fn(n) for n in ("isInfant", "defaultSnacksFor", "snacksFor", "householdSnacksPerDay", "memberSnacksPayload")) + """
const a = memberSnacksPayload(), ah = householdSnacksPerDay();
memberSnacks.Arjun = 3; memberSnacks.Gowthami = 0;
console.log(JSON.stringify([a, ah, memberSnacksPayload(), householdSnacksPerDay()]));
""")
    assert out[0] == {"Gowthami": 1, "Arjun": 2} and out[1] == 2
    assert out[2] == {"Gowthami": 0, "Arjun": 3} and out[3] == 3


def test_snacks_screen_words():
    markup = _step_markup("step-snacks")
    assert "How many snacks a day?" in markup


# ---------- the server ----------


def test_onboarding_saves_lunch_needs_and_snacks_per_person_and_bridges_lunch_location(signed_in):
    from app import tools

    client = signed_in
    res = client.post("/api/onboarding/answers", json={
        "member_names": ["Gowthami", "Ravi", "Arjun"],
        "lunch_needs": {
            "Gowthami": {"needs": ["reheat"], "days": {}},
            "Arjun": {"needs": ["nut_free", "thermos"],
                      "days": {"wednesday": ["made_fresh"], "monday": ["thermos", "nut_free"]}},
        },
        "member_snacks": {"Gowthami": 1, "Ravi": 0, "Arjun": 3},
    })
    assert res.status_code == 200, res.text
    by = {m["name"]: m for m in res.json()["member_needs"]}
    assert by["Gowthami"]["lunch_needs"] == {"needs": ["reheat"], "days": {}}
    # Monday equals the standing answer, so only Wednesday is kept as different.
    assert by["Arjun"]["lunch_needs"] == {"needs": ["thermos", "nut_free"], "days": {"wednesday": ["made_fresh"]}}
    assert by["Ravi"]["lunch_needs"] is None
    assert [by[n]["snacks_per_day"] for n in ("Gowthami", "Ravi", "Arjun")] == [1, 0, 3]
    loc = tools.get_household_rhythm()["lunch_location"]
    flat = json.dumps(loc)
    assert "Arjun" in flat and "Gowthami" in flat
    from app.tools import rhythm

    assert rhythm.effective_lunch_location("Arjun", "Monday") == "out"
    assert rhythm.effective_lunch_location("Arjun", "Wednesday") == "home"
    assert rhythm.effective_lunch_location("Gowthami", "Tuesday") == "home"


def test_a_bad_lunch_need_is_a_400_before_anything_is_written(signed_in):
    from app import tools

    client = signed_in
    before = len(tools.list_members())
    res = client.post("/api/onboarding/answers", json={
        "member_names": ["Nobody New"], "lunch_needs": {"Nobody New": {"needs": ["picnic"]}},
    })
    assert res.status_code == 400
    assert len(tools.list_members()) == before
    res = client.post("/api/onboarding/answers", json={"member_names": ["Nobody New"], "member_snacks": {"Nobody New": 4}})
    assert res.status_code == 400


def test_default_snacks_by_age():
    from app.tools.member_needs import default_snacks

    assert [default_snacks(a) for a in ("child", "toddler", "adult", "teen", "")] == [2, 2, 1, 1, 1]


def test_the_run_once_migration_moves_one_hour_prep_to_two_and_ten_minute_lunches_to_twenty(tmp_path):
    from app import db as _db

    conn = sqlite3.connect(tmp_path / "m.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(Path(_db.SCHEMA_PATH).read_text())
    _db._run_migrations(conn)
    conn.execute("INSERT INTO households (id, name) VALUES (7, 'h')")
    conn.execute(
        "INSERT INTO household_rhythm (household_id, member_name, weekday, fact_type, value) VALUES (7, '', '', 'prep_days', ?)",
        (json.dumps([{"weekday": "sunday", "minutes": 60, "note": None}, {"weekday": "wednesday", "minutes": None, "note": None}]),),
    )
    conn.execute("INSERT INTO meal_preferences (household_id, weekday_lunch_max_minutes) VALUES (7, 10)")
    conn.execute(f"PRAGMA user_version = {_db._DATA_VERSION_ONBOARDING_REGROUPED - 1}")
    _db._run_migrations(conn)
    days = json.loads(conn.execute("SELECT value FROM household_rhythm WHERE household_id = 7").fetchone()[0])
    assert [d["minutes"] for d in days] == [120, None]
    assert conn.execute("SELECT weekday_lunch_max_minutes FROM meal_preferences WHERE household_id = 7").fetchone()[0] == 20
    # Once only: a 60 saved after it ships is the household's own.
    conn.execute("UPDATE meal_preferences SET weekday_lunch_max_minutes = 10 WHERE household_id = 7")
    _db._run_migrations(conn)
    assert conn.execute("SELECT weekday_lunch_max_minutes FROM meal_preferences WHERE household_id = 7").fetchone()[0] == 10


# ---------- slice 2: ages (branch overnight/onboarding-ages) ----------


def test_the_age_chips_are_adult_teen_child():
    opts = _const("AGE_GROUP_OPTIONS")
    assert [l for l in ("'Adult'", "'Teen'", "'Child'") if l in opts] == ["'Adult'", "'Teen'", "'Child'"]
    assert "Little one" not in opts
    assert "'How old is ' + name + '?'" in _fn("renderMemberAgeExtras")
    assert "Include in meals?" in _fn("renderMemberAgeExtras")


@_needs_node
def test_an_infant_left_out_is_not_a_meal_member_and_has_no_snacks():
    out = _run(_const("INFANT_UNDER_YEARS") + "".join(_fn(n) for n in ("memberAgeYears", "isInfant")) + """
function block(name, group, age, inc) {
  return { dataset: { ageGroup: group, ageYears: age, includeMeals: inc },
           classList: { contains: function () { return false; } },
           querySelector: function () { return { value: name }; } };
}
var membersDiv = { querySelectorAll: function () { return [
  block('Gowthami', 'adult', '', '0'), block('Baby', 'child', '0.5', '0'),
  block('Mira', 'child', '0.5', '1'), block('Arjun', 'child', '7', '0')]; } };
function primaryMemberName() { return ''; }
""" + _fn("currentMembers") + """
var all = currentMembers(), meals = currentMembers({ forMeals: true });
console.log(JSON.stringify({ all: all, meals: meals.map(m => m.name), ages: ['', '0', '0.5', '-1', 'x'].map(memberAgeYears) }));
""")
    assert [m["name"] for m in out["all"]] == ["Gowthami", "Baby", "Mira", "Arjun"]
    assert out["meals"] == ["Gowthami", "Mira", "Arjun"], "an infant switched off is out of meals; one switched on is in"
    baby = out["all"][1]
    assert baby["age_years"] == 0.5 and baby["include_in_meals"] is False
    assert out["all"][3]["include_in_meals"] is True, "a seven-year-old is always counted"
    assert out["ages"] == [None, 0, 0.5, None, None]


def test_an_infant_left_out_of_meals_is_out_of_every_count(signed_in):
    from app import tools

    res = signed_in.post("/api/onboarding/household", json={"members": [
        {"name": "Gowthami", "age_group": "adult"},
        {"name": "Baby", "age_group": "child", "age_years": 0.5, "include_in_meals": False},
        {"name": "Mira", "age_group": "child", "age_years": 0.5, "include_in_meals": True},
        {"name": "Arjun", "age_group": "child", "age_years": 7, "include_in_meals": False},
    ], "pets": [], "goals": ""})
    assert res.status_code == 200, res.text
    names = {m["name"] for m in tools.list_members()}
    assert {"Gowthami", "Mira", "Arjun"} <= names and "Baby" not in names
    assert [tools.age_stage("child", a) for a in (0.5, 2, 7, None)] == ["infant", "toddler", "child", "child"]
    bad = signed_in.post("/api/onboarding/household", json={"members": [{"name": "Zed", "age_group": "child", "age_years": -2}]})
    assert bad.status_code == 400
    assert "Zed" not in {m["name"] for m in tools.list_members()}


def test_little_one_members_become_child_once(tmp_path):
    from app import db as _db

    conn = sqlite3.connect(tmp_path / "a.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(Path(_db.SCHEMA_PATH).read_text())
    _db._run_migrations(conn)
    conn.execute("INSERT INTO households (id, name) VALUES (9, 'h')")
    conn.execute("INSERT INTO members (household_id, name, age_group) VALUES (9, 'Tot', 'toddler')")
    conn.execute(f"PRAGMA user_version = {_db._DATA_VERSION_LITTLE_ONE_IS_CHILD - 1}")
    _db._run_migrations(conn)
    row = conn.execute("SELECT age_group, age_years, include_in_meals FROM members WHERE name = 'Tot'").fetchone()
    assert (row["age_group"], row["age_years"], row["include_in_meals"]) == ("child", None, 1)
