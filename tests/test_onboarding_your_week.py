"""
Onboarding's "How your week runs" screens (Emily's approved mockups,
2026-09-30, Loop Board card A):

  "Does anyone else help run the house?" moved up to step 1; step 2
     opens with "Who's eating, and when?" (meals x days x who), then "Do you
     like to cook ahead?", one "How many different ...?" screen per meal
     that's on, then dinner time. "Which meals should I plan?" and "How do
     you feel about leftovers?" are gone, and the loading screen reads back
     a fuller "What I'm using".

Settings › Your rhythm (card B) is tests/test_settings_your_rhythm.py.

The page's own functions run under node (the house pattern — see
tests/test_onboarding_go_back.py, whose helpers this reuses); the saves run
through the real routes with the test client.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("test_onboarding_go_back", _HERE / "test_onboarding_go_back.py")
_go_back = importlib.util.module_from_spec(_spec)
sys.modules["test_onboarding_go_back"] = _go_back
_spec.loader.exec_module(_go_back)
_needs_node, _fn, _const, _run = _go_back._needs_node, _go_back._fn, _go_back._const, _go_back._run
_nav_harness, _step_markup = _go_back._nav_harness, _go_back._step_markup

ONBOARDING = (_HERE.parent / "static" / "onboarding.html").read_text()


def _script() -> str:
    return ONBOARDING[ONBOARDING.index("<script>\n// \"Allergy\" and \"Other\""):]


# ---------- the order ----------


def test_the_screens_run_in_the_storyboards_order():
    steps = json.loads(_const("ALL_STEPS").split("=", 1)[1].strip().rstrip(";").replace("'", '"'))
    assert steps[4:] == [
        # UPDATED 2026-10-05 (the main person): the household step is TWO
        # screens -- "What's your name?" then "Who else lives with you?"
        # -- so the main person is somebody who said so rather than
        # whichever row happened to be first. TRIPWIRE FIRED: this file
        # hard-codes the flow's order. The claim is unchanged; the flow
        # gained a step.
        "your-name",
        "household", "helpers", "restrictions",
        "meals-days", "prep", "variety-breakfast", "variety-lunch", "variety-dinner", "dinner-time",
        # UPDATED 2026-10-05 (grocery shop day): the two clock questions
        # together. The claim is unchanged; the flow gained a step.
        "shop-day",
        "eating-style", "wont-eat", "excited-about", "kit-repeats",
        # UPDATED 2026-10-05: "Anything else I should know?" is setup's
        # last answer, after the consent card and before the reveal. The
        # claim is unchanged; the flow gained a step at the end.
        "ai-consent", "anything-else", "reveal",
    ]


def test_the_intro_still_lists_the_four_steps_in_that_order():
    know = _step_markup("step-intro-know")
    titles = ["Who&rsquo;s eating", "How your week runs", "What you eat", "Your first week"]
    positions = [know.index(t) for t in titles]
    assert positions == sorted(positions)


@_needs_node
def test_each_question_names_its_step_in_the_eyebrow():
    out = _run(_nav_harness() + """
console.log(JSON.stringify(%s.map(function (k) { return stepEyebrow(k); })));
""" % json.dumps(["your-name", "household", "helpers", "restrictions", "meals-days", "prep", "variety-lunch",
                  "dinner-time", "eating-style", "kit-repeats", "ai-consent", "reveal"]))
    assert out == [
        # UPDATED 2026-10-05: both halves of the split household step are
        # in the first stop, so both name it. The claim is unchanged.
        "1 of 4 · Who’s eating",
        "1 of 4 · Who’s eating", "1 of 4 · Who’s eating", "1 of 4 · Who’s eating",
        "2 of 4 · How your week runs", "2 of 4 · How your week runs", "2 of 4 · How your week runs",
        "2 of 4 · How your week runs", "3 of 4 · What you eat", "3 of 4 · What you eat",
        "4 of 4 · Your first week", "",
    ]


# ---------- skip logic ----------


@_needs_node
def test_a_meal_off_all_week_skips_its_variety_screen_both_ways():
    out = _run(_nav_harness() + """
MEALS_OFF = ['lunch'];
const flow = stepFlow();
showStep('variety-dinner');
const label = backLabel('variety-dinner');
tapBack('variety-dinner');
console.log(JSON.stringify({ flow: flow, label: label, landed: currentStep, asked: showStep('variety-lunch') || currentStep }));
""")
    assert "variety-lunch" not in out["flow"]
    assert out["flow"].index("variety-breakfast") + 1 == out["flow"].index("variety-dinner")
    assert out["label"] == "‹ Breakfast"
    assert out["landed"] == "variety-breakfast"
    # Asked for by name (history, a stale entry): the nearest step before it.
    assert out["asked"] == "variety-breakfast"


def _variety_harness() -> str:
    return "\n".join([
        _const("UW_MEALS"), _const("VARIETY_OPTIONS"), _const("VARIETY_DEFAULT"), _const("VARIETY_STEPS"),
        _const("UW_WEEKDAYS"),
        "var prepAnswer = ''; var prepDayKeys = []; var varietyChoice = { breakfast: 'few_in_rotation', lunch: 'few_in_rotation', dinner: 'few_in_rotation' };",
        _fn("currentPrepDayKeys"), _fn("hasPrepDay"), _fn("varietyOptionsFor"), _fn("currentVarietyChoice"),
        _fn("varietyFoot"),
    ])


@_needs_node
def test_meal_prep_ahead_is_only_offered_with_a_prep_day():
    out = _run(_variety_harness() + """
const without = varietyOptionsFor('lunch', hasPrepDay()).map(o => o.key);
varietyChoice.lunch = 'meal_prep_ahead';
const fallsBack = currentVarietyChoice('lunch');
prepAnswer = 'yes'; prepDayKeys = ['sunday'];
const withDay = varietyOptionsFor('lunch', hasPrepDay()).map(o => o.key);
console.log(JSON.stringify({ without: without, fallsBack: fallsBack, withDay: withDay, kept: currentVarietyChoice('lunch') }));
""")
    assert out["without"] == ["last_nights_dinner", "few_in_rotation", "new_every_day"]
    assert out["fallsBack"] == "few_in_rotation", "a pick that's no longer offered must not be sent"
    assert out["withDay"] == ["last_nights_dinner", "meal_prep_ahead", "few_in_rotation", "new_every_day"]
    assert out["kept"] == "meal_prep_ahead"


@_needs_node
def test_the_variety_pips_caption_and_button_follow_the_screens_asked():
    out = _run(_variety_harness() + """
const all = ['meals-days', 'prep', 'variety-breakfast', 'variety-lunch', 'variety-dinner', 'dinner-time'];
const noLunch = all.filter(k => k !== 'variety-lunch');
console.log(JSON.stringify([varietyFoot(all, 'variety-breakfast'), varietyFoot(all, 'variety-lunch'),
  varietyFoot(all, 'variety-dinner'), varietyFoot(noLunch, 'variety-breakfast'),
  varietyFoot(['variety-dinner', 'dinner-time'], 'variety-dinner')]));
""")
    assert out[0] == {"pips": 3, "on": 1, "caption": "Breakfast · then lunch · then dinner", "nextLabel": "Next: lunch"}
    assert out[1] == {"pips": 3, "on": 2, "caption": "Breakfast · lunch · then dinner", "nextLabel": "Next: dinner"}
    assert out[2] == {"pips": 3, "on": 3, "caption": "", "nextLabel": "Continue"}
    assert out[3] == {"pips": 2, "on": 1, "caption": "Breakfast · then dinner", "nextLabel": "Next: dinner"}
    assert out[4]["pips"] == 0 and out[4]["nextLabel"] == "Continue"


def test_the_variety_options_are_the_mockups_words_and_the_servers_keys():
    from app.tools.usual_week import VARIETY_CHOICES

    options = _const("VARIETY_OPTIONS")
    for meal, choices in VARIETY_CHOICES.items():
        for key in choices:
            assert f"key: '{key}'" in options, f"{meal} {key} isn't offered"
    for words in ("1–2 breakfasts", "A go-to or two", "Same thing most mornings, something different on the weekend.",
                  "3–4 breakfasts", "Enough to mix it up, each one made more than once.",
                  "Something new every morning", "The most variety, and the most cooking.",
                  "Mostly leftovers", "Last night’s dinner", "I’ll make dinner big enough to cover the next day’s lunch.",
                  "Meal prep ahead", "Made on your prep day, enough for the week.", "Each one made more than once.",
                  "Something new every day", "2–3 dinners", "Cook big, eat twice",
                  "Most dinners come back as leftovers the next night.", "4–5 dinners",
                  "Mostly fresh, with a leftovers night or two.", "Something new every night"):
        assert words in options, f"missing {words!r}"


# ---------- who's eating, and when ----------


def _grid_harness() -> str:
    return "\n".join([
        _fn("revealJoinWords"),
        _const("UW_MEALS"), _const("UW_WEEKDAYS"), _const("UW_DAY_NAMES"), _const("UW_UNITS"), _const("UW_QUICK"),
        _fn("uwAllRow"), _fn("uwIsOn"), _fn("uwDaysOn"), _fn("uwCleanCell"), _fn("uwWho"), _fn("uwCountLabel"),
        _fn("uwSetMany"), _fn("uwQuickOn"), _fn("uwPickerOptions"), _fn("uwApplyPick"), _fn("uwTotalMeals"),
        _fn("uwSummary"), _fn("uwLunchLine"),
    ])


@_needs_node
def test_the_day_picker_for_two_people_and_for_three():
    out = _run(_grid_harness() + """
const two = uwPickerOptions('all', ['Emily', 'Greg']).map(o => o.label);
const three = uwPickerOptions(['Greg'], ['Emily', 'Greg', 'Ava']).map(o => [o.label, o.on]);
const justGreg = uwApplyPick('all', 'just:Greg', ['Emily', 'Greg']);
let c = 'all';
c = uwApplyPick(c, 'toggle:Emily', ['Emily', 'Greg', 'Ava']);
const afterOne = c;
c = uwApplyPick(c, 'toggle:Greg', ['Emily', 'Greg', 'Ava']);
c = uwApplyPick(c, 'toggle:Ava', ['Emily', 'Greg', 'Ava']);
console.log(JSON.stringify({ two: two, three: three, justGreg: justGreg, afterOne: afterOne, empty: c,
  back: uwApplyPick(['Greg', 'Ava'], 'toggle:Emily', ['Emily', 'Greg', 'Ava']) }));
""")
    assert out["two"] == ["Everyone", "Just Emily", "Just Greg", "Don’t plan"]
    assert out["three"] == [["Everyone", False], ["Emily", False], ["Greg", True], ["Ava", False], ["Don’t plan", False]]
    assert out["justGreg"] == ["Greg"]
    assert out["afterOne"] == ["Greg", "Ava"]
    assert out["empty"] == "off", "toggling everybody off is Don't plan"
    assert out["back"] == "all", "toggling the last person back on is Everyone"


@_needs_node
def test_quick_picks_labels_and_the_running_total():
    out = _run(_grid_harness() + """
let row = uwSetMany(uwAllRow(), [0, 1, 2, 3, 4]);
const weekdays = UW_QUICK.map(q => uwQuickOn(row, q.days));
const grid = { breakfast: uwAllRow(), lunch: row, dinner: uwAllRow() };
grid.dinner[4] = 'off';
console.log(JSON.stringify({
  weekdays: weekdays,
  count: [uwCountLabel('breakfast', 7), uwCountLabel('lunch', 5), uwCountLabel('dinner', 1), uwCountLabel('dinner', 0)],
  sum: uwSummary(grid, 2), one: uwSummary(grid, 1), none: uwSummary(grid, 0),
  nothing: uwSummary({ breakfast: uwSetMany(row, []), lunch: uwSetMany(row, []), dinner: uwSetMany(row, []) }, 2),
  who: [uwWho('all', ['Emily', 'Greg']), uwWho(['Greg'], ['Emily', 'Greg']), uwWho('off', ['Emily']),
        uwWho('all', ['A', 'B', 'C', 'D'])],
  lunch: uwLunchLine(['Emily', 'Greg'], { Greg: 'out' }),
}));
""")
    assert out["weekdays"] == [False, True, False, False]
    assert out["count"] == ["7 mornings", "5 days", "1 night", "Not planned"]
    assert out["sum"] == "That’s 18 meals and 2 snacks a day."
    assert out["one"] == "That’s 18 meals and 1 snack a day."
    assert out["none"] == "That’s 18 meals and no snacks."
    assert out["nothing"] == "Nothing to plan yet. Tap a day to add it."
    assert out["who"] == ["E G", "G", "–", "All"]
    assert out["lunch"] == "Weekdays: Emily at home · Greg packs it"


def test_the_grid_screen_carries_the_legend_the_snacks_and_the_line():
    markup = _step_markup("step-meals-days")
    for words in ("Everyone", "Some of you", "Not planned"):
        assert words in markup
    script = _script()
    assert "{ value: 0, label: 'None' }, { value: 1, label: '1' }, { value: 2, label: '2' }, { value: 3, label: '3 a day' }" in script
    assert "'Every day'" in script and "'Weekdays'" in script and "'Weekends'" in script and "'None'" in script
    assert "· who’s eating?" in script


# ---------- cook ahead ----------


@_needs_node
def test_the_prep_read_back_line():
    out = _run("\n".join([
        _fn("revealJoinWords"), _const("UW_WEEKDAYS"), _const("UW_DAY_NAMES"), _fn("uwDayName"), _fn("prepReadback"),
    ]) + """
console.log(JSON.stringify([
  prepReadback(['sunday'], 'longer', true),
  prepReadback(['sunday', 'wednesday'], 'hour', true),
  prepReadback(['sunday'], 'longer', false),
  prepReadback([], 'hour', true),
]));
""")
    assert out[0] == "Sunday, a longer stretch. I’ll put lunch prep and a big batch cook there, and keep weeknights quick."
    assert out[1] == "Sunday and Wednesday, about an hour. I’ll put lunch prep there, and keep weeknights quick."
    assert out[2] == "Sunday, a longer stretch. I’ll put a big batch cook there, and keep weeknights quick."
    assert out[3] == ""


# ---------- what onboarding sends ----------


def _payload_harness(members) -> str:
    return "\n".join([
        _grid_harness(), _const("VARIETY_OPTIONS"), _const("VARIETY_DEFAULT"),
        "var MEMBERS = %s; function currentMembers() { return MEMBERS; }" % json.dumps(members),
        "var usualGrid = { breakfast: uwAllRow(), lunch: uwAllRow(), dinner: uwAllRow() };",
        "var snacksPerDay = 2; var prepAnswer = ''; var prepDayKeys = []; var prepLength = '';",
        "var varietyChoice = { breakfast: 'few_in_rotation', lunch: 'few_in_rotation', dinner: 'few_in_rotation' };",
        "var lunchLocation = {}; var lunchLocationTouched = false;",
        "var breakfastsPerWeek = 7, lunchesPerWeek = 7, dinnersPerWeek = 5;",
        _fn("uwMealOn"), _fn("currentPrepDayKeys"), _fn("hasPrepDay"), _fn("varietyOptionsFor"),
        _fn("currentVarietyChoice"), _fn("usualWeekPayload"), _fn("plannedMealCounts"), _fn("lunchLocationPayload"),
        _const("LEFTOVERS_STANCE_BY_DINNER_VARIETY"), _fn("leftoversStanceFromVariety"),
    ])


@_needs_node
def test_the_payload_onboarding_sends():
    out = _run(_payload_harness([{"name": "Emily"}, {"name": "Greg"}]) + """
usualGrid.breakfast = uwSetMany(usualGrid.breakfast, []);        // no breakfast at all
usualGrid.dinner[4] = 'off';                                      // no Friday dinner
usualGrid.dinner[3] = ['Greg'];                                   // Thursday: just Greg
usualGrid.lunch[0] = ['Emily', 'Greg'];                           // both named = everyone
prepAnswer = 'yes'; prepDayKeys = ['wednesday', 'sunday']; prepLength = 'longer';
varietyChoice.breakfast = 'go_to_or_two';                         // off: not sent
varietyChoice.lunch = 'meal_prep_ahead';
varietyChoice.dinner = 'cook_big_eat_twice';
snacksPerDay = 1;
console.log(JSON.stringify({ usual: usualWeekPayload(), counts: plannedMealCounts(),
  stance: leftoversStanceFromVariety(uwMealOn('dinner'), currentVarietyChoice('dinner')),
  lunch: lunchLocationPayload() }));
""")
    usual = out["usual"]
    assert set(usual["grid"]) == {"breakfast", "lunch", "dinner"}
    assert set(usual["grid"]["breakfast"].values()) == {"off"}
    assert usual["grid"]["dinner"]["friday"] == "off"
    assert usual["grid"]["dinner"]["thursday"] == ["Greg"]
    assert usual["grid"]["lunch"]["monday"] == "everyone"
    assert usual["variety"] == {"lunch": "meal_prep_ahead", "dinner": "cook_big_eat_twice"}
    assert usual["snacks_per_day"] == 1
    assert usual["prep"] == {"days": ["wednesday", "sunday"], "length": "longer"}
    assert out["counts"] == {"breakfasts_per_week": 0, "lunches_per_week": 7, "dinners_per_week": 5, "snacks_per_day": 1}
    assert out["stance"] == "love_them", "Cook big, eat twice is a household that loves leftovers"
    assert out["lunch"] is None, "an untouched weekday-lunch line sends nothing"


@_needs_node
def test_no_prep_day_sends_the_empty_answer_and_a_prep_lunch_falls_back():
    out = _run(_payload_harness([{"name": "Emily"}]) + """
prepAnswer = 'yes'; prepDayKeys = [];        // yes, but no day picked: no prep
varietyChoice.lunch = 'meal_prep_ahead';
varietyChoice.dinner = 'new_every_day';
console.log(JSON.stringify({ usual: usualWeekPayload(),
  stance: leftoversStanceFromVariety(true, 'new_every_day'), none: leftoversStanceFromVariety(false, 'cook_big_eat_twice'),
  few: leftoversStanceFromVariety(true, 'few_in_rotation') }));
""")
    assert out["usual"]["prep"] == {"days": []}
    assert out["usual"]["variety"]["lunch"] == "few_in_rotation"
    assert out["stance"] == "fresh_each_night" and out["none"] == "" and out["few"] == "fine_sometimes"


def test_saving_sends_the_usual_week_and_no_longer_the_prep_days_or_a_stance_question():
    save = _fn("saveOnboardingAnswers")
    assert "usual_week: usualWeekPayload()" in save
    rhythm = _fn("saveRhythmAnswers")
    assert "prep_days" not in rhythm, "the prep answer is saved with the usual week now"
    assert "leftoversStanceFromVariety(" in rhythm


def test_the_onboarding_payload_saves_through_the_real_route(signed_in):
    """The shape the page sends, through /api/onboarding/answers: names in
    the grid resolve because the usual week is saved after member_names."""
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}, {"name": "Greg", "age_group": "adult"}],
        "pets": [], "goals": "",
    })
    week = {d: "everyone" for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily", "Greg"], "household_restrictions": {}, "eating_style": "", "wont_eat": [],
        "excited_about": [], "breakfasts_per_week": 7, "lunches_per_week": 7, "dinners_per_week": 5,
        "snacks_per_day": 2,
        "usual_week": {
            "grid": {"breakfast": dict(week), "lunch": dict(week),
                     "dinner": dict(week, friday="off", thursday=["Greg"])},
            "variety": {"breakfast": "few_in_rotation", "lunch": "meal_prep_ahead", "dinner": "few_in_rotation"},
            "snacks_per_day": 2,
            "prep": {"days": ["sunday"], "length": "longer"},
        },
    })
    assert res.status_code == 200, res.text
    saved = res.json()["usual_week"]
    assert saved["answered"] is True
    assert saved["grid"]["dinner"]["friday"] == "off"
    greg = next(m["id"] for m in saved["members"] if m["name"] == "Greg")
    assert saved["grid"]["dinner"]["thursday"] == [greg]
    assert saved["variety"]["dinner"] == {"choice": "few_in_rotation", "dishes": 4, "days_on": 6}
    assert saved["prep"] == {"days": ["sunday"], "length": "longer"}


# ---------- helpers ----------


@_needs_node
def test_the_helper_options_and_picks():
    out = _run("\n".join([
        "var MEMBERS = []; function currentMembers() { return MEMBERS; }",
        # 2026-10-05: helperAdults excludes the MAIN PERSON by name rather
        # than members[0] by position -- "does anyone else help run the
        # house?" is a question about the others, and the main person is a
        # real thing now (the pinned "You" row; primary_member_id
        # server-side) instead of whichever row came first. helperAdults is
        # an ALREADY-EXTRACTED function that gained a callee, which is the
        # one shape no grep for a new symbol can find (this file never
        # names primaryMemberName).
        #
        # A stand-in that STATES who the main person is, not an empty stub:
        # primaryMemberName() returning '' would make helperAdults list
        # EVERYBODY including the main person -- the bug the assertion
        # below is about -- and the test would pass while asserting less.
        # The real one reads #your-name-input, which this harness has no
        # DOM for. Naming Emily makes the claim STRONGER than it was: it
        # was "whoever is first is left out", it is now "the main person
        # is left out".
        "var PRIMARY_NAME = 'Emily'; function primaryMemberName() { return PRIMARY_NAME; }",
        _const("HELPER_SOMEONE"), _const("HELPER_ME"),
        "var helperPicks = []; var helperContacts = {}; var helperSomeoneName = '';",
        _fn("helperAdults"), _fn("helperOptions"), _fn("helperToggle"), _fn("pruneHelperAnswers"), _fn("helpersToInvite"),
    ]) + """
MEMBERS = [{ name: 'Emily', age_group: 'adult' }, { name: 'Greg', age_group: 'adult' }, { name: 'Ava', age_group: 'child' }];
const titles = helperOptions(helperAdults(MEMBERS)).map(o => o.title);
helperPicks = helperToggle(helperPicks, 'adult:Greg');
helperPicks = helperToggle(helperPicks, 'someone');
helperContacts['adult:Greg'] = ' greg@example.com ';
helperSomeoneName = 'Maria';
const invites = helpersToInvite();
const justMe = helperToggle(helperPicks, 'me');
MEMBERS = [{ name: 'Emily', age_group: 'adult' }, { name: 'Ava', age_group: 'child' }];   // Greg went
console.log(JSON.stringify({ titles: titles, invites: invites, justMe: justMe, pruned: helpersToInvite() }));
""")
    assert out["titles"] == ["Yes, Greg does", "Someone not eating here", "Just me"]
    assert out["invites"] == [{"name": "Greg", "contact": "greg@example.com", "eatsHere": True},
                              {"name": "Maria", "contact": "", "eatsHere": False}]
    assert out["justMe"] == ["me"]
    assert out["pruned"] == [{"name": "Maria", "contact": "", "eatsHere": False}], "a helper who left the list is not invited"


@_needs_node
def test_a_phone_or_email_opens_a_text_or_an_email():
    out = _run(_fn("inviteContactHref") + """
console.log(JSON.stringify([
  inviteContactHref('greg@example.com', 'Hi.', 'https://x/join#t'),
  inviteContactHref('(613) 555-0199', 'Hi.', 'https://x/join#t'),
  inviteContactHref('Greg', 'Hi.', 'https://x/join#t'),
  inviteContactHref('', 'Hi.', 'https://x/join#t'),
]));
""")
    assert out[0].startswith("mailto:greg@example.com?subject=") and "Hi.%20https%3A%2F%2Fx%2Fjoin%23t" in out[0]
    assert out[1] == "sms:6135550199?&body=Hi.%20https%3A%2F%2Fx%2Fjoin%23t"
    assert out[2] == "" and out[3] == ""


def test_the_helpers_screen_words():
    markup = _step_markup("step-helpers")
    assert "Does anyone else help run the house?" in markup
    assert "If someone else shops or cooks too, I&rsquo;ll give them their own way in once your first week is ready." in markup
    script = _script()
    for words in ("A nanny, a parent who helps with dinners", "You can add someone later in Settings",
                  "’s phone or email (optional)", "Sent when your week is ready"):
        assert words in script


def test_the_loading_screen_no_longer_asks_about_helpers():
    reveal = _step_markup("step-reveal")
    assert "help run the house" not in reveal
    assert "function renderInviteOffer" not in ONBOARDING
    assert 'id="invite-yes"' not in ONBOARDING and "invite-choice" not in _script()
    # The invites go out once the week is on screen, not on a failed one.
    body = _fn("generateFirstPlanAndReveal")
    assert "if (!failed) renderHelperInvites();" in body


# ---------- removed screens ----------


def test_the_old_screens_are_gone():
    for gone in ('id="step-meals"', 'id="step-leftovers"', "Which meals should I plan?</h1>",
                 "How do you feel about leftovers?</h1>", "Which days do you want to do your meal prepping?</h1>",
                 "function buildMealsStep", "function buildLeftoversStep", "LEFTOVERS_STANCE_OPTIONS",
                 'class="q-pager"'):
        assert gone not in ONBOARDING, gone


# ---------- the read-back ----------


def _using_harness() -> str:
    return "\n".join([
        _fn("escapeHtmlLocal"), _fn("revealTitleCase"), _fn("revealJoinWords"), _fn("revealJoinOr"),
        _const("UW_MEALS"), _const("UW_WEEKDAYS"), _const("UW_DAY_NAMES"), _const("UW_UNITS"), _const("UW_MEAL_PLURALS"),
        _fn("uwCountLabel"), _fn("uwDayName"),
        _const("REVEAL_DINNER_WINDOW_FACT"), _fn("revealMealsLine"), _const("REVEAL_RESTRICTION_WORDS"),
        _fn("revealRestrictionPhrase"), _fn("revealAvoidLine"), _fn("revealVarietyLine"), _fn("revealDinnerLine"),
        _fn("revealUsingLines"),
    ])


_WEEK = {d: "everyone" for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}


@_needs_node
def test_the_read_back_lines_match_the_mockup():
    answers = {
        "member_names": ["Emily", "Greg"],
        "household_restrictions": {"Greg": ["Dairy-free"]},
        "wont_eat": ["Olives", "bell peppers"],
        "dinner_window": "6_8",
        "usual_week": {
            "grid": {"breakfast": _WEEK, "lunch": _WEEK, "dinner": dict(_WEEK, friday="off", thursday=["Greg"])},
            "prep": {"days": ["sunday"], "length": "longer"},
        },
        "usual_week_saved": {"variety": {
            "breakfast": {"choice": "few_in_rotation", "dishes": 3},
            "lunch": {"choice": "meal_prep_ahead", "dishes": 2},
            "dinner": {"choice": "few_in_rotation", "dishes": 4},
        }},
    }
    out = _run(_using_harness() + "console.log(JSON.stringify(revealUsingLines(%s)));" % json.dumps(answers))
    assert out == [
        {"icon": "people", "text": "Emily and Greg"},
        {"icon": "days", "text": "20 meals · no Friday dinner"},
        {"icon": "avoid", "text": "Greg: no dairy · no olives, no bell peppers"},
        {"icon": "variety", "text": "3 breakfasts · lunches prepped Sunday · 4 dinners"},
        {"icon": "dinner", "text": "Dinner between 6 and 8 · Emily out Thursday"},
    ]


@_needs_node
def test_the_read_back_for_a_quieter_week():
    answers = {
        "member_names": ["Robin"],
        "household_restrictions": {"Robin": ["allergy: peanuts"]},
        "wont_eat": [],
        "dinner_window": "all_over",
        "usual_week": {
            "grid": {"breakfast": {d: "off" for d in _WEEK}, "lunch": dict(_WEEK, saturday="off", sunday="off"),
                     "dinner": _WEEK},
            "prep": {"days": []},
        },
        "usual_week_saved": {"variety": {
            "breakfast": {"choice": None, "dishes": 0},
            "lunch": {"choice": "last_nights_dinner", "dishes": 1},
            "dinner": {"choice": "new_every_day", "dishes": 7},
        }},
    }
    out = _run(_using_harness() + "console.log(JSON.stringify(revealUsingLines(%s)));" % json.dumps(answers))
    assert out == [
        {"icon": "people", "text": "Robin"},
        {"icon": "days", "text": "12 meals · no breakfast · no weekend lunch"},
        {"icon": "avoid", "text": "Robin: no peanuts"},
        {"icon": "variety", "text": "Lunches from last night’s dinner · 7 dinners"},
    ]


def test_some_of_you_differs_from_everyone_in_lightness_in_dark_mode_only():
    """Emily, 2026-09-30: in dark mode the celadon tint sat too close to dark
    spruce, so "some of you" read as a hue of "everyone". Dark only, scoped
    to the grid: the light --celadon fill with --on-accent-ink, the same as
    Settings' day row. Light mode and the tokens are untouched."""
    css = ONBOARDING[ONBOARDING.index("<style>"): ONBOARDING.index("</style>")]
    rule = "background: var(--celadon); border-color: var(--celadon); color: var(--on-accent-ink);"
    media = css[css.index("@media (prefers-color-scheme: dark) {\n    :where(:root:not([data-theme=\"light\"])) .uw-day.is-some"):]
    assert rule in media[: media.index("}\n  }") + 1]
    assert ':where(:root[data-theme="dark"]) .uw-day.is-some, :where(:root[data-theme="dark"]) .uw-sw.is-some { ' + rule in css
    # Light keeps the tint.
    assert ".uw-day.is-some { background: var(--celadon-tint); border-color: var(--celadon-edge); color: var(--ink-on-celadon); }" in css
    assert "--celadon:" not in css and "--celadon-tint:" not in css, "no token is redefined here"


# ---------- verification follow-ups (2026-09-30) ----------


def _reveal_harness() -> str:
    return "\n".join([
        _fn("escapeHtmlLocal"), _const("REVEAL_WEEK_SLOTS"), _const("REVEAL_SLOT_LABELS"),
        _const("REVEAL_DAY_SHORT"), _const("REVEAL_DAY_LONG"), _const("REVEAL_SWAP_ICON"),
        _fn("revealWeekdayIndex"), _fn("formatPlanDate"), _fn("revealMinutesMeta"),
        _fn("revealSlotFromStream"), _fn("revealSlotFromMenu"), _fn("revealDaysFromMenu"),
        _fn("revealSlotDishHtml"), _fn("revealOpenSlotHtml"), _fn("revealJoinWords"), _const("UW_WEEKDAYS"),
        "var lastFirstPlanAnswers = null;", _fn("revealDaySubsetNote"),
        _fn("revealMakes"), _fn("revealSlotMeta"), _fn("revealSnackKey"), _fn("revealIsSnackKey"), _fn("revealPlaceSlot"), _fn("revealDaySlotKeys"),
        _fn("revealDayCardHtml"),
    ])


@_needs_node
def test_a_slot_week_1_left_for_the_person_shows_its_question_and_options():
    day = {
        "date": "2026-10-01", "before_plan_start": False, "breakfast": None, "snacks": [], "snack": None,
        "lunch": {"title": "Oats", "meta": "10 min", "state": "planned", "entry_id": 1},
        "dinner": {"title": "I’d like your call on this one", "state": "open", "entry_id": 9, "source": "open",
                   "open_question": "Nothing I know is safe for Greg on Thursday — what would you like?",
                   "options": [{"label": "Grilled salmon", "meta": "25 min"}, {"label": "Takeout"}]},
    }
    out = _run(_reveal_harness() + """
const days = revealDaysFromMenu([%s]);
const streamed = revealSlotFromStream({ date: '2026-10-01', slot: 'dinner', slot_state: 'open' });
console.log(JSON.stringify({ html: revealDayCardHtml(days[0], days),
  stream: revealDayCardHtml({ date: '2026-10-01', slots: { dinner: streamed } }, []) }));
""" % json.dumps(day))
    html = out["html"]
    assert "Nothing I know is safe for Greg on Thursday — what would you like?" in html
    assert 'class="reveal-open-option" data-open-date="2026-10-01" data-open-slot="dinner" data-choice="Grilled salmon">Grilled salmon<span>25 min</span>' in html
    assert 'data-choice="Takeout">Takeout</button>' in html
    assert "reveal-open-talk" not in html, "options offered: no need for the chat line"
    assert '<span class="reveal-day-count">2 meals</span>' in html, "the open slot counts"
    # A frame the stream calls open while the week is still landing isn't drawn.
    assert 'data-slot="dinner"' not in out["stream"]


def test_answering_an_open_slot_uses_the_plan_tabs_write():
    body = _fn("resolveRevealOpen")
    assert "'/slot'" in body and "choice: btn.getAttribute('data-choice')" in body
    assert "weekly_plan_id: firstPlanId" in body
    assert "renderRevealDays(days)" in body and "' was settled'" in body
    # An allergy refusal is a 200 {"status": "refused", "message"}: said, not
    # reported as settled.
    assert "settled.status === 'refused'" in body and "revealToast(settled.message" in body
    assert body.index("'refused'") < body.index("' was settled'")
    wire = _fn("wireRevealCarousel")
    assert "resolveRevealOpen(opt)" in wire and "revealGoTweak(revealSlotName(" in wire


@_needs_node
def test_the_day_header_names_who_eats_a_part_dinner():
    grid = {"dinner": dict(_WEEK, thursday=["Greg"], friday="off")}
    out = _run(_reveal_harness() + """
console.log(JSON.stringify([revealDaySubsetNote('2026-10-01', %s), revealDaySubsetNote('2026-10-02', %s),
  revealDaySubsetNote('2026-09-28', %s), revealDaySubsetNote('2026-10-01', null)]));
""" % (json.dumps(grid), json.dumps(grid), json.dumps(grid)))
    assert out == ["just Greg for dinner", "", "", ""]
    assert "' · ' + escapeHtmlLocal(note)" in _fn("revealDayCardHtml")


@_needs_node
def test_someone_not_eating_here_needs_a_name_before_continue():
    out = _run("\n".join([_const("HELPER_SOMEONE"), _fn("helpersNeedName")]) + """
console.log(JSON.stringify([helpersNeedName(['someone'], ''), helpersNeedName(['someone'], '  '),
  helpersNeedName(['someone'], 'Maria'), helpersNeedName(['adult:Greg'], ''), helpersNeedName([], '')]));
""")
    assert out == [True, True, False, False, False]
    assert "if (helpersNeedName(helperPicks, helperSomeoneName)) return;" in ONBOARDING


def test_the_contact_placeholder_fits_at_375():
    assert "const HELPER_CONTACT_PLACEHOLDER = 'Sent when your week is ready';" in ONBOARDING


def test_the_using_card_takes_the_full_width_back():
    css = ONBOARDING[ONBOARDING.index("  .reveal-using {"):]
    assert "margin-right: -56px;" in css[: css.index("}")]
    assert "padding-right: 56px;" in ONBOARDING[ONBOARDING.index("  .reveal-head {"):][:200]
