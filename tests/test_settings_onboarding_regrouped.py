"""
Onboarding, regrouped — slice 3: Settings → Your rhythm with the same screens
(Loop Board, Emily 2026-10-05, LOCKED).

Settings offers the answers the new onboarding asks, on the same server
tools: age chips Adult · Teen · Child with "How old is [name]?" and an
infant's "Include in meals?"; each person's weekday lunch needs and snacks a
day (/api/member-needs); Cook ahead with no "How long?" (every prep day is 2
hours); the made-fresh lunch limit 20 / 30 / 45 / No limit. And the card's
"Existing 'toddler' members map to Child, with the age asked the next time
Settings opens."
"""
from __future__ import annotations

import json

from app import tools
from app.agent import TOOL_DEFINITIONS as TOOLS
from tests.test_preferences_align import MEMORY, SHELL_JS, _function, _harness, _run
from tests.test_settings_main_person_row import _people_block


# --- the server half ----------------------------------------------------------

def test_settings_sets_a_childs_age_and_an_infant_starts_out_of_meals(signed_in):
    tools.add_member("Arjun")
    tools.set_member_age_group("Arjun", "child")
    res = signed_in.post("/api/memory/member/age", json={"name": "Arjun", "age_years": 0.5})
    assert res.status_code == 200
    arjun = [m for m in res.json()["members"] if m["name"] == "Arjun"][0]
    # Still listed (allergies bind), but off by default: not in meals.
    assert arjun["age_years"] == 0.5 and arjun["in_meals"] is False
    assert "Arjun" not in [m["name"] for m in tools.get_member_needs()]

    res = signed_in.post("/api/memory/member/age", json={"name": "Arjun", "age_years": 0.5, "include_in_meals": True})
    assert [m for m in res.json()["members"] if m["name"] == "Arjun"][0]["in_meals"] is True

    bad = signed_in.post("/api/memory/member/age", json={"name": "Arjun", "age_years": 300})
    assert bad.status_code == 400 and "0 to 120" in bad.json()["detail"]


def test_a_little_one_said_in_chat_is_stored_as_a_child_and_the_tool_stops_offering_it():
    tools.add_member("Bug")
    assert tools.set_member_age_group("Bug", "Toddler")["age_group"] == "child"
    tool = [t for t in TOOLS if t["name"] == "set_member_age_group"][0]
    assert "'toddler'," not in tool["description"] and "'toddler', or" not in tool["description"]
    assert "'child'" in tool["description"]


# --- Who's here: the ages -------------------------------------------------------

def _people(members) -> str:
    res = _run(_people_block() + "console.log(JSON.stringify(wwkPeopleHtml(%s)));\n" % json.dumps({"members": members}))
    return res


def test_the_age_chips_are_adult_teen_child_and_a_child_is_asked_their_age():
    html = _people([
        {"id": 1, "name": "Ravi", "age_group": "adult", "dietary_restrictions": []},
        {"id": 2, "name": "Arjun", "age_group": "child", "age_years": None, "in_meals": True, "dietary_restrictions": []},
    ])
    assert "Little one" not in html and "label: 'Little one'" not in SHELL_JS
    assert html.count('data-wwk="age"') == 6   # three chips each
    assert "How old is Arjun?" in html and "How old is Ravi?" not in html
    assert 'data-wwk-input="age" data-member="Arjun"' in html
    assert "Include in meals" not in html   # no age yet: not an infant


def test_an_infant_gets_the_include_in_meals_switch_off_by_default():
    html = _people([{"id": 3, "name": "Mira", "age_group": "child", "age_years": 0.5, "in_meals": False,
                     "dietary_restrictions": []}])
    assert 'aria-pressed="false" data-wwk="include-meals" data-member="Mira">Include in meals<' in html
    assert "Their allergies still count." in html


def test_settings_asks_the_age_of_a_child_with_none_on_record():
    out = _run(_function("wwkAgeKey") + _function("prefsAgeAskWho") + """
console.log(JSON.stringify([
  prefsAgeAskWho({ members: [{ name: 'Ravi', age_group: 'adult' }, { name: 'Arjun', age_group: 'child', age_years: null }] }),
  prefsAgeAskWho({ members: [{ name: 'Arjun', age_group: 'child', age_years: 6 }] })
]));
""")
    assert out[0]["name"] == "Arjun" and out[1] is None
    assert "renderPrefsAgeAsk();" in _function("renderPrefsRows")


# --- Your rhythm: weekday lunches, snacks, cook ahead ---------------------------

def _rhythm(lunch_grid: dict, needs: list, **mem) -> str:
    return _run(_harness() + """
uwState.data = {members: [{id: 1, name: 'Ravi'}, {id: 2, name: 'Arjun'}], grid: {lunch: %s}, variety: {}, prep: {days: []}, snacks_per_day: 2};
wwkState.needs = %s;
prefsState.memory = %s;
console.log(JSON.stringify(wwkRhythmHtml(prefsState.memory)));
""" % (json.dumps(lunch_grid), json.dumps(needs), json.dumps({**MEMORY, **mem})))


NEEDS = [
    {"id": 1, "name": "Ravi", "lunch_needs": {"needs": ["cold_packed"], "days": {}}, "snacks_per_day": 1, "snacks_set": False},
    {"id": 2, "name": "Arjun", "lunch_needs": {"needs": ["thermos", "nut_free"], "days": {}}, "snacks_per_day": 2, "snacks_set": False},
]


def test_each_person_gets_the_five_lunch_needs_and_no_lunch_limit_without_made_fresh():
    html = _rhythm({}, NEEDS)
    assert "Weekday lunches" in html
    for label in ("Cold packed", "Warm in a thermos", "Something to reheat", "Made fresh", "Nut-free environment"):
        assert html.count(f">{label}</button>") == 2
    assert 'is-on" aria-pressed="true" data-wwk="lunch-need" data-member="Arjun" data-day="" data-value="nut_free"' in html
    assert "Different on some days?" in html
    assert "Made fresh: how long can it take?" not in html and "weekday_lunch_max_minutes" not in html


def test_weekday_lunches_only_when_lunch_is_on_for_a_weekday():
    off = {d: "off" for d in ("monday", "tuesday", "wednesday", "thursday", "friday")}
    assert "Weekday lunches" not in _rhythm(off, NEEDS)
    # Only Arjun on Wednesday: only Arjun's row.
    html = _rhythm({**off, "wednesday": [2]}, NEEDS)
    assert 'data-member="Arjun"' in html and 'data-wwk="lunch-need" data-member="Ravi"' not in html


def test_made_fresh_brings_the_20_to_no_limit_chips_and_never_10():
    needs = [dict(NEEDS[0], lunch_needs={"needs": [], "days": {"tuesday": ["made_fresh"]}}), NEEDS[1]]
    html = _rhythm({}, needs)
    assert "Made fresh: how long can it take?" in html
    assert "Same every day" in html   # Ravi is day by day
    lunch = html[html.index("Made fresh: how long can it take?"):]
    for label in ("20 min", "30 min", "45 min", "No limit"):
        assert f">{label}</button>" in lunch
    assert ">10 min<" not in html


def test_a_day_tap_sends_all_five_days_with_mondays_as_the_standing_answer():
    out = _run(_harness("wwkToggleLunchNeed", "wwkSameNeeds") + """
var sent = [];
function wwkSaveLunchEntry(name, entry) { sent.push([name, entry]); }
wwkState.needs = %s;
wwkState.lunchByDay = { Ravi: true };
wwkToggleLunchNeed('Ravi', 'tuesday', 'made_fresh');
wwkToggleLunchNeed('Arjun', '', 'reheat');
console.log(JSON.stringify(sent));
""" % json.dumps(NEEDS))
    ravi, arjun = out
    assert ravi[1]["needs"] == ["cold_packed"]
    assert ravi[1]["days"]["tuesday"] == ["cold_packed", "made_fresh"] and ravi[1]["days"]["friday"] == ["cold_packed"]
    assert arjun[1] == {"needs": ["thermos", "reheat", "nut_free"], "days": {}}


def test_snacks_are_per_person_and_the_household_number_follows_the_most():
    html = _run(_harness() + """
uwState.data = {members: [], grid: {}, variety: {}, prep: {days: []}, snacks_per_day: 2};
uwState.row = 'snacks';
wwkState.needs = %s;
console.log(JSON.stringify(wwkUsualWeekHtml({rhythm: {}})));
""" % json.dumps([dict(n, snacks_set=True) for n in NEEDS]))
    assert "Ravi 1 · Arjun 2" in html
    assert 'is-on" aria-pressed="true" data-wwk="member-snacks" data-member="Arjun" data-value="2">2<' in html
    assert "uwPost({ snacks_per_day: most })" in _function("wwkSaveMemberSnacks")


def test_cook_ahead_has_no_how_long_and_every_prep_day_is_two_hours():
    html = _run(_harness("wwkPrepDaysHtml", "wwkPrepPayload") + """
var WWK_PREP_DAYS = [{ key: 'sunday', label: 'Sun' }, { key: 'monday', label: 'Mon' }];
console.log(JSON.stringify([
  wwkPrepDaysHtml({ rhythm: { prep_days: [{ weekday: 'sunday', minutes: 60 }] } }),
  wwkPrepPayload(['monday', 'sunday'])
]));
""")
    assert "Roughly how long" not in html[0] and "About an hour" not in SHELL_JS
    assert "up to 2 hours" in html[0]
    assert html[1] == [{"weekday": "sunday", "minutes": 120}, {"weekday": "monday", "minutes": 120}]


# --- review fixes (2026-10-06) ---------------------------------------------------

def _snacks(house: int, script: str) -> list:
    return _run(_harness("wwkSaveMemberSnacks") + """
uwState.data = {members: [], grid: {}, variety: {}, prep: {days: []}, snacks_per_day: %d};
uwState.row = 'snacks';
wwkState.needs = %s;
var posted = [], uw = [];
function wwkSaveNeeds(body, apply, said, then) { posted.push(JSON.parse(JSON.stringify(body))); apply(); return then(); }
function uwPost(body) { uw.push(body); return Object.assign({}, uwState.data, body); }
function wwkMem() { return null; }
%s
""" % (house, json.dumps(NEEDS), script))


def test_a_household_on_no_snacks_stays_on_none_until_someone_says_otherwise():
    # Nobody answered; the household is on 0. Everyone shows 0, not the age
    # defaults (Ravi 1, Arjun 2), and setting Arjun to 0 turns nothing on.
    out = _snacks(0, """
var line = wwkSnacksValue(uwState.data);
var html = wwkMemberSnacksHtml();
wwkSaveMemberSnacks('Arjun', 0);
console.log(JSON.stringify([line, html, posted, uw]));
""")
    line, html, posted, uw = out
    assert line == "Ravi 0 · Arjun 0"
    assert 'is-on" aria-pressed="true" data-wwk="member-snacks" data-member="Arjun" data-value="0"' in html
    assert posted == [{"snacks": {"Ravi": 0, "Arjun": 0}}] and uw == []


def test_editing_one_person_pins_the_others_at_the_household_number():
    out = _snacks(2, """
wwkSaveMemberSnacks('Arjun', 3);
console.log(JSON.stringify([posted, uw, wwkSnacksValue(uwState.data)]));
""")
    posted, uw, line = out
    # Ravi never answered: saved at the household's 2, so the household
    # moving to 3 (the most anyone has) doesn't move him.
    assert posted == [{"snacks": {"Ravi": 2, "Arjun": 3}}]
    assert uw == [{"snacks_per_day": 3}]
    assert line == "Ravi 2 · Arjun 3"


def test_the_age_route_never_creates_a_member_or_takes_an_adult_out_of_meals(signed_in):
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    before = len(tools.get_household_memory_for_display()["members"])
    assert signed_in.post("/api/memory/member/age", json={"name": "   ", "age_years": 3}).status_code == 400
    assert signed_in.post("/api/memory/member/age", json={"name": "", "age_years": 3}).status_code == 400
    assert signed_in.post("/api/memory/member/age", json={"name": "Nobody", "age_years": 3}).status_code == 404
    assert len(tools.get_household_memory_for_display()["members"]) == before
    res = signed_in.post("/api/memory/member/age", json={"name": "Emily", "age_years": 0.5, "include_in_meals": False})
    emily = [m for m in res.json()["members"] if m["name"] == "Emily"][0]
    assert emily["in_meals"] is True


def test_an_old_lunch_place_shows_as_needs_until_the_household_taps():
    out = _run(_harness("wwkToggleLunchNeed", "wwkSameNeeds") + """
uwState.data = {members: [{id: 1, name: 'Ravi'}], grid: {lunch: {}}, variety: {}, prep: {days: []}, snacks_per_day: 1};
wwkState.needs = [{id: 1, name: 'Ravi', lunch_needs: null, snacks_per_day: 1, snacks_set: false}];
prefsState.memory = %s;
prefsState.memory.rhythm.lunch_location = {Ravi: {standing: 'out', overrides: {Tuesday: 'home'}}};
var html = wwkRhythmHtml(prefsState.memory);
var sent = [];
function wwkSaveLunchEntry(name, entry) { sent.push(entry); }
wwkToggleLunchNeed('Ravi', 'friday', 'nut_free');
console.log(JSON.stringify([html, sent]));
""" % json.dumps(MEMORY))
    html, sent = out
    assert "From what you told me before." in html and "Same every day" in html
    assert 'is-on" aria-pressed="true" data-wwk="lunch-need" data-member="Ravi" data-day="monday" data-value="cold_packed"' in html
    assert 'is-on" aria-pressed="true" data-wwk="lunch-need" data-member="Ravi" data-day="tuesday" data-value="reheat"' in html
    # The first tap keeps the old answer and adds the new one.
    assert sent == [{"needs": ["cold_packed"], "days": {
        "monday": ["cold_packed"], "tuesday": ["reheat"], "wednesday": ["cold_packed"],
        "thursday": ["cold_packed"], "friday": ["cold_packed", "nut_free"]}}]


def test_two_hour_prep_days_read_up_to_2_hours_once():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 120}, {"weekday": "wednesday", "minutes": 120}])
    assert tools.prep_days_summary() == "Preps on Sunday and Wednesday (up to 2 hours)."
