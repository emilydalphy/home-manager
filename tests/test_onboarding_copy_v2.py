"""
Onboarding, second pass — Julia's beta feedback (2026-09-08).

Julia is the first beta tester to go through onboarding cold. What she hit,
and what this file guards:

1. Too much typing. Every step that REQUIRED free text now leads with
   preset chips and keeps the box as an optional "Anything else?".
2. Wording. Six steps were reworded (Emily's exact strings), and the
   typical-week step was cut from three blocks of prose to one line.
3. A household of one adult was asked which meals it eats together and who
   does the cooking — two questions with one possible answer each.
4. Snacks were asked per WEEK as a count of distinct recipes, which is not
   a number anyone thinks in. Asked per DAY now, on chips.
5. The reveal showed an empty sample menu while the plan generated.
6. "I asked for next week and it planned this week."

The JS ones are source-level checks, for the reason tests/
test_onboarding_inputs.py states at length: this repo has no JavaScript
test harness, and putting a browser in CI is a bigger change than this
warrants. They can catch a string being changed back or a guard being
removed; they cannot catch a subtle logic error inside a handler. The
behaviour they stand in for was checked by reading the extracted script
under `node --check` and by driving the real endpoints below.
"""
from __future__ import annotations

import datetime
import json
import re
import types
from pathlib import Path

import pytest

from app import agent, main as main_module, tools


ONBOARDING = (Path(__file__).resolve().parent.parent / "static" / "onboarding.html").read_text()

# The same source with HTML comments and full-line JS comments removed. A
# decision-log comment naming the old copy it replaced is exactly what this
# file wants people to keep writing, so "the old wording is gone" has to
# mean gone from the SCREEN, not gone from the file.
ONBOARDING_VISIBLE = re.sub(r"<!--.*?-->", "", ONBOARDING, flags=re.S)
ONBOARDING_VISIBLE = re.sub(r"^\s*//.*$", "", ONBOARDING_VISIBLE, flags=re.M)


def _step_markup(step_id: str) -> str:
    """The markup of one wizard step div, brace-… tag-matched by depth."""
    start = ONBOARDING.index(f'<div id="{step_id}"')
    i, depth = start, 0
    for m in re.finditer(r"<div\b|</div>", ONBOARDING[start:]):
        depth += 1 if m.group(0) != "</div>" else -1
        if depth == 0:
            i = start + m.end()
            break
    assert i > start, f"could not find the end of #{step_id}"
    return ONBOARDING[start:i]


def _function_body(name: str) -> str:
    """The body of a top-level `function name(...) { ... }`, brace-matched."""
    start = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", ONBOARDING)
    assert start, f"{name} is missing from onboarding.html"
    i, depth = start.end(), 1
    while i < len(ONBOARDING) and depth:
        if ONBOARDING[i] == "{":
            depth += 1
        elif ONBOARDING[i] == "}":
            depth -= 1
        i += 1
    return ONBOARDING[start.end(): i - 1]


# ---------- 2. the rewordings, each one pinned to its own step ----------

REWORDINGS = [
    # (what Emily wrote, which step it belongs to, what it replaced)
    ("When do you want the meal plan for the week ready",
     "step-rhythm-2", "When should your weekly plan be ready?"),
    ("Do you like meal prepping?",
     "step-rhythm-2", "Do you like to prep ahead?"),
    ("Dietary preferences or restrictions",
     "step-restrictions", "Anyone I cook carefully around?"),
    ("Anything I should never recommend?",
     "step-wont-eat", "Anything the house just won't eat, no matter what?"),
    ("How many different breakfasts do you want?", "step-dinners", "Breakfasts"),
    ("How many different lunches do you want?", "step-dinners", "Lunches"),
    ("How many different dinners do you want?", "step-dinners", "Dinners"),
]


@pytest.mark.parametrize("wording, step_id, replaced", REWORDINGS)
def test_each_reworded_step_says_the_new_words(wording, step_id, replaced):
    """Emily's exact strings, each on the step that asks the question."""
    assert wording in _step_markup(step_id), (
        f"{step_id} no longer asks {wording!r} — it was reworded on 2026-09-08 "
        f"from {replaced!r} after Julia went through onboarding cold."
    )


def test_the_old_wordings_are_gone_from_the_whole_page():
    """A reworded question that is still asked in the old words somewhere
    else on the page is not reworded, it is duplicated."""
    for _, _, replaced in REWORDINGS:
        if replaced in ("Breakfasts", "Lunches", "Dinners"):
            continue  # ordinary words; they legitimately appear elsewhere
        assert replaced not in ONBOARDING_VISIBLE, (
            f"the old wording {replaced!r} is still on the page"
        )


def test_the_counts_step_asks_per_meal_type_not_once_generically():
    """
    One heading ("How many different recipes a week?") was doing the work
    of three questions. Emily's call: ask per meal type.
    """
    assert "How many different recipes a week?" not in ONBOARDING_VISIBLE
    step = _step_markup("step-dinners")
    for meal in ("breakfasts", "lunches", "dinners"):
        assert f"How many different {meal} do you want?" in step


def test_the_typical_week_step_is_one_short_line_now():
    """
    "Far too many words" (Emily, relaying Julia). The why-panel, the
    five-bullet "worth mentioning" list and the second explanatory
    paragraph are gone; the example in the box is one short sentence.
    """
    step = _step_markup("step-typical-week")
    assert "why-panel" not in step, "the why-panel is back on the typical-week step"
    assert "typical-week-hints" not in step and "Worth mentioning" not in step, (
        "the 'worth mentioning' prompt list is back — it was five more things to type"
    )
    placeholder = re.search(r'id="typical-week"[^>]*placeholder="([^"]*)"', step)
    assert placeholder, "the typical-week textarea lost its example placeholder"
    example = placeholder.group(1)
    assert example.count(".") == 1, f"the example should be ONE sentence; got {example!r}"
    assert len(example) <= 60, f"the example is still long: {example!r}"


# ---------- 1. chips first, typing optional ----------

@pytest.mark.parametrize(
    "step_id, chips_container, box_id",
    [
        ("step-eating-style", "eating-style-chips", "eating-style"),
        ("step-wont-eat", "wont-eat-presets", "wont-eat-input"),
        ("step-excited-about", "excited-chips", "excited-custom"),
    ],
)
def test_every_free_text_step_leads_with_presets_and_keeps_the_box_optional(
    step_id, chips_container, box_id
):
    """
    Julia: "less typing, more clicking of preset options; typing is
    optional for extra detail, never mandatory." Two of these three steps
    were a bare text box before.
    """
    step = _step_markup(step_id)
    assert f'id="{chips_container}"' in step, f"{step_id} offers no preset chips"
    assert f'id="{box_id}"' in step, f"{step_id} lost its optional text box"
    assert "Anything else?" in step, (
        f"{step_id}'s text box is no longer labelled as the optional extra"
    )
    # The chips must come FIRST — a preset row underneath the box is not
    # "chips first", it is a box with an afterthought.
    assert step.index(f'id="{chips_container}"') < step.index(f'id="{box_id}"')


def test_the_preset_answers_actually_reach_the_saved_payload():
    """
    Wiring the chips up and then submitting only the typed text would be
    the same bug tests/test_onboarding_inputs.py exists for, one level up.
    """
    save = _function_body("saveOnboardingAnswers")
    assert "currentEatingStyle()" in save and "currentWontEat()" in save, (
        "saveOnboardingAnswers no longer reads the chip-aware collectors, so a "
        "household that only TAPPED presets submits nothing"
    )
    assert "eating-style-chips" in _function_body("currentEatingStyle")
    assert "wont-eat-presets" in _function_body("currentWontEat")


# ---------- 3. a household of one adult ----------

def test_a_one_person_household_is_not_asked_the_two_pointless_questions():
    """
    Both cards are hidden and both answers are filled in — hiding them
    without answering them would just move the same block from the screen
    into rhythm1Complete().
    """
    body = _function_body("applySoloAdultDefaults")
    assert "rhythm-meals-together-card" in body and "rhythm-cooking-card" in body, (
        "applySoloAdultDefaults no longer hides the two cards"
    )
    assert "most_meals" in body and "one_person" in body, (
        "the two questions are hidden but not answered, so Continue stays disabled "
        "on a question nobody can see"
    )
    solo = _function_body("isSoloAdultHousehold")
    assert "length === 1" in solo and "'adult'" in solo, (
        "isSoloAdultHousehold no longer means 'exactly one adult and nobody else' — "
        "a household with a toddler in it eats together and must still be asked"
    )
    assert "applySoloAdultDefaults()" in _function_body("buildRhythmStep1")


def test_adding_a_second_person_takes_the_filled_in_answers_back():
    """
    Going back and adding someone must not ship an answer they never saw.
    """
    body = _function_body("applySoloAdultDefaults")
    assert "rhythmSoloDefaultsApplied" in body, (
        "nothing tracks that the two answers were filled in FOR the household, so "
        "adding a second person leaves a solo default saved as their answer"
    )


def test_the_defaults_a_solo_household_gets_are_saved_like_any_other_answer(signed_in):
    """
    The endpoint half: 'most_meals' + 'one_person' + the person's name are
    a valid rhythm save, and What we know reads them back as given
    answers, because that is what they are.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Alex", "age_group": "adult"}], "pets": [], "goals": "",
    })
    res = signed_in.post("/api/onboarding/rhythm", json={
        "lunch_location": {"Alex": "home"},
        "meals_together": "most_meals",
        "cooking_role": "one_person",
        "cooking_role_who": "Alex",
        "dinner_window": "6_8",
        "planning_anchor": "sunday",
        "leftovers_stance": "love_them",
    })
    assert res.status_code == 200
    rhythm = res.json()
    assert rhythm["meals_together"] == "most_meals"
    assert rhythm["cooking_role"] == {"value": "one_person", "who": "Alex"}


# ---------- 4. snacks are a per-day question ----------

def test_the_wizard_asks_snacks_per_day_on_chips():
    step = _step_markup("step-dinners")
    assert "How many snacks a day?" in step
    assert 'id="snacks-per-day-chips"' in step
    assert 'id="snacks-value"' not in step, "the per-week snacks stepper is back"
    m = re.search(r"const SNACKS_PER_DAY_OPTIONS = \[(.*?)\];", ONBOARDING, re.S)
    assert m, "SNACKS_PER_DAY_OPTIONS is gone"
    assert [int(k) for k in re.findall(r"key:\s*(\d+)", m.group(1))] == [0, 1, 2, 3]
    assert re.search(r"let snacksPerDay = 2;", ONBOARDING), "the default is no longer 2"
    assert "snacks_per_day: snacksPerDay" in _function_body("saveOnboardingAnswers")


def test_onboarding_saves_snacks_per_day_and_derives_the_per_week_count(signed_in):
    """
    The new field is stored, and snacks_per_week is written alongside it so
    nothing downstream (generation's proration, the setup steppers,
    edit_preference) changes shape. Capped at 7 deliberately — see
    preferences.snacks_per_week_from_per_day: that column means DISTINCT
    snack recipes and is validated 0-7 everywhere, so "2 a day" is a
    distinct snack every day, not fourteen recipes.
    """
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily"],
        "household_restrictions": {},
        "eating_style": "",
        "wont_eat": [],
        "excited_about": [],
        "dinners_per_week": 5,
        "snacks_per_day": 2,
    })
    assert res.status_code == 200

    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_day"] == 2
    assert memory["snacks_per_day_set"] is True
    assert memory["snacks_per_week"] == 7
    assert memory["snacks_per_week_set"] is True


def test_no_snacks_is_an_answer_and_derives_to_none(signed_in):
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily"], "household_restrictions": {}, "eating_style": "",
        "wont_eat": [], "excited_about": [], "dinners_per_week": 5, "snacks_per_day": 0,
    })
    assert res.status_code == 200
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_day"] == 0
    assert memory["snacks_per_day_set"] is True
    assert memory["snacks_per_week"] == 0


def test_a_household_that_never_answered_is_not_told_it_snacks_twice_a_day(signed_in):
    """
    snacks_per_day is NOT NULL DEFAULT 2, so it needs its own answered-flag
    for exactly the reason snacks_per_week_set was added on 2026-09-08 —
    otherwise the Preferences sheet reads a schema default back as a fact.
    """
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_day"] == 2
    assert memory["snacks_per_day_set"] is False


def test_what_we_know_can_correct_the_per_day_answer(signed_in):
    """Both numbers move together, so the two can never disagree."""
    signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily"], "household_restrictions": {}, "eating_style": "",
        "wont_eat": [], "excited_about": [], "dinners_per_week": 5, "snacks_per_day": 2,
    })
    res = signed_in.post("/api/memory/edit", json={"field": "snacks_per_day", "value": 0})
    assert res.status_code == 200
    memory = signed_in.get("/api/memory").json()
    assert memory["snacks_per_day"] == 0
    assert memory["snacks_per_week"] == 0
    assert signed_in.get("/api/facts?category=taste").json()["preferences"]["snacks_per_day"] == 0


def test_the_preferences_sheet_says_snacks_a_day():
    """shell.js's "How you eat" row — the one line that reads this back."""
    shell = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text()
    assert "snacks_per_day_set" in shell, (
        "prefsEatingLine no longer reads the per-day answered flag"
    )
    assert "' a day'" in shell, "the 'How you eat' row no longer says 'a day'"


# ---------- 5. nothing menu-shaped until a day arrives ----------

def test_the_reveal_ships_no_menu_markup_at_all():
    """
    The day list is empty AND hidden in the source. An empty .reveal-days
    is still a flex column with a 14px top margin — the blank sample menu
    Julia saw sitting under a title that promised a week.
    """
    step = _step_markup("step-reveal")
    m = re.search(r'<div id="reveal-days"[^>]*>(.*?)</div>', step, re.S)
    assert m, "#reveal-days is gone"
    assert m.group(1).strip() == "", "#reveal-days ships with markup inside it"
    assert re.search(r'<div id="reveal-days"[^>]*\bhidden\b', step), (
        "#reveal-days is not hidden, so the empty menu shape is on screen while "
        "the plan generates"
    )
    assert ".reveal-days[hidden]" in ONBOARDING, (
        "display:flex beats the hidden attribute without an explicit rule — the "
        "same guard pair .reveal-status already carries"
    )


def test_the_title_does_not_promise_a_week_before_there_is_one():
    step = _step_markup("step-reveal")
    assert "Here&#39;s your sample week." not in step and "Here's your sample week." not in step, (
        "the reveal's static title still announces a sample week that hasn't "
        "been generated yet"
    )
    assert "REVEAL_TITLE_READY" in ONBOARDING and "REVEAL_TITLE_WAITING" in ONBOARDING


def test_the_first_real_day_is_what_reveals_the_menu():
    """
    Not a timer, not the end of the stream: the day that actually arrived.
    """
    assert "revealShowDays()" in _function_body("upsertRevealDay"), (
        "the streamed day handler no longer un-hides the day list, so a streaming "
        "reveal fills an invisible box"
    )
    show = _function_body("revealShowDays")
    assert "hidden = false" in show and "REVEAL_TITLE_READY" in show


def test_the_waiting_lines_are_what_shows_instead():
    """The wait is not a blank screen — it's the shared waiting-line
    helper, which is the whole reason that component exists."""
    gen = _function_body("generateFirstPlanAndReveal")
    assert "startWaitingLines" in gen
    assert "daysDiv.hidden = true" in gen, (
        "'Try again' no longer re-hides the day list, so a retry starts from the "
        "shape of the week that just failed"
    )


# ---------- 6. asked for next week, planned this week ----------

def _freeze_main_clock(monkeypatch, frozen: datetime.date):
    """Same technique as tests/test_part_week_onboarding.py."""
    class _FrozenDate(datetime.date):
        @classmethod
        def today(cls):
            return frozen

    monkeypatch.setattr(
        main_module,
        "datetime",
        types.SimpleNamespace(
            date=_FrozenDate,
            timedelta=datetime.timedelta,
            datetime=datetime.datetime,
            timezone=datetime.timezone,
        ),
    )


def _monday(offset_weeks: int = 1) -> datetime.date:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return monday + datetime.timedelta(days=7 * offset_weeks)


def _week_of(start: datetime.date, count: int) -> list[dict]:
    return [
        {"date": (start + datetime.timedelta(days=i)).isoformat(), "slot": slot,
         "meal_name": "Chili", "is_new_recipe": False, "reasoning": "fits"}
        for i in range(count)
        for slot in tools.WEEK_SLOTS
    ]


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events, pending = [], None
    for line in body.split("\n"):
        if line.startswith("event:"):
            pending = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((pending, json.loads(line[len("data:"):].strip())))
    return events


def test_the_start_choice_is_asked_and_sent(signed_in):
    """
    The missing question. Onboarding's own copy had been talking about the
    week ahead for two steps running while both endpoints computed the
    current calendar week, and nothing carried an answer across because
    nothing asked.
    """
    step = _step_markup("step-typical-week")
    assert 'id="first-plan-start-chips"' in step
    m = re.search(r"const FIRST_PLAN_START_OPTIONS = \[(.*?)\];", ONBOARDING, re.S)
    assert m, "FIRST_PLAN_START_OPTIONS is gone"
    assert "'this_week'" in m.group(1) and "'next_week'" in m.group(1)
    assert "start: firstPlanStart" in _function_body("streamFirstPlan"), (
        "the reveal's stream request no longer carries the household's answer, so "
        "asking the question changes nothing"
    )


@pytest.fixture
def recipe():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)


def test_asked_for_next_week_the_plain_endpoint_files_next_week(signed_in, recipe, monkeypatch):
    """The bug, stated as the property: next week means next Monday."""
    this_monday = _monday(0)
    wednesday = this_monday + datetime.timedelta(days=2)
    next_monday = this_monday + datetime.timedelta(days=7)
    _freeze_main_clock(monkeypatch, wednesday)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda ctx: _week_of(next_monday, 7))

    res = signed_in.post("/api/onboarding/generate-first-plan", json={"start": "next_week"})
    assert res.status_code == 200
    body = res.json()

    assert body["week_start_date"] == next_monday.isoformat(), (
        f"onboarding on Wednesday {wednesday} asked for NEXT week and got "
        f"{body['week_start_date']}"
    )
    assert body["first_planned_date"] == next_monday.isoformat()
    assert body["is_part_week"] is False
    assert len(body["meals"]) == 21, "a whole week, not the rest of this one"


def test_asked_for_this_week_the_plain_endpoint_still_files_this_week(signed_in, recipe, monkeypatch):
    """The other half — the fix must not make every first plan next week."""
    this_monday = _monday(0)
    wednesday = this_monday + datetime.timedelta(days=2)
    _freeze_main_clock(monkeypatch, wednesday)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda ctx: _week_of(wednesday, 5))

    res = signed_in.post("/api/onboarding/generate-first-plan", json={"start": "this_week"})
    assert res.status_code == 200
    body = res.json()

    assert body["week_start_date"] == this_monday.isoformat()
    assert body["first_planned_date"] == wednesday.isoformat(), (
        "the part-week still starts on the day the household actually joined"
    )
    assert len(body["meals"]) == 15


def test_the_streaming_endpoint_agrees_with_the_plain_one_about_next_week(
    signed_in, recipe, monkeypatch
):
    """
    The reveal calls the STREAMING route, and that route had none of the
    week arithmetic its plain twin's docstring describes — it filed seven
    days from this week's Monday no matter what. Both go through
    _first_plan_window now, so this is the property that they agree.
    """
    this_monday = _monday(0)
    wednesday = this_monday + datetime.timedelta(days=2)
    next_monday = this_monday + datetime.timedelta(days=7)
    _freeze_main_clock(monkeypatch, wednesday)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda ctx: _week_of(next_monday, 7))

    res = signed_in.post("/api/onboarding/generate-first-plan/stream", json={"start": "next_week"})
    assert res.status_code == 200
    done = next(p for n, p in _parse_sse(res.text) if n == "done")

    assert done["week_start_date"] == next_monday.isoformat()
    assert done["first_planned_date"] == next_monday.isoformat()
    assert len(done["meals"]) == 21


def test_the_streaming_endpoint_builds_a_real_part_week_for_this_week(
    signed_in, recipe, monkeypatch
):
    """
    The half of the same bug nobody asked about: the streaming route had no
    part-week logic either, so a Wednesday household's reveal showed Monday
    and Tuesday — two days that had already gone by.
    """
    this_monday = _monday(0)
    wednesday = this_monday + datetime.timedelta(days=2)
    _freeze_main_clock(monkeypatch, wednesday)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda ctx: _week_of(wednesday, 5))

    res = signed_in.post("/api/onboarding/generate-first-plan/stream", json={"start": "this_week"})
    assert res.status_code == 200
    done = next(p for n, p in _parse_sse(res.text) if n == "done")

    assert done["week_start_date"] == this_monday.isoformat(), "still filed under Monday"
    assert done["first_planned_date"] == wednesday.isoformat()
    assert done["is_part_week"] is True
    assert len(done["meals"]) == 15, "5 days x 3 meals — nothing for Mon/Tue"


def test_the_plan_ready_day_decides_which_period_next_week_means(
    signed_in, recipe, monkeypatch
):
    """
    "Next week" is one period on from the household's OWN week, not one
    calendar week on from Monday. A household whose plan is ready on
    Friday starts its week on Saturday (tools.suggest_planning_period), so
    their next one starts the Saturday after that.
    """
    this_monday = _monday(0)
    wednesday = this_monday + datetime.timedelta(days=2)
    # Ready by Friday -> the week starts Saturday; the current one began
    # last Saturday, so the next begins this coming Saturday.
    tools.set_planning_anchor("friday", source="onboarding")
    last_saturday = this_monday - datetime.timedelta(days=2)
    next_saturday = last_saturday + datetime.timedelta(days=7)
    _freeze_main_clock(monkeypatch, wednesday)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda ctx: _week_of(next_saturday, 7))

    res = signed_in.post("/api/onboarding/generate-first-plan", json={"start": "next_week"})
    assert res.status_code == 200
    body = res.json()

    assert body["week_start_date"] == next_saturday.isoformat(), (
        "the household said their plan is ready on Friday, so their week starts "
        "Saturday — 'next week' has to mean their next week, not the calendar's"
    )


def test_a_request_with_no_body_still_means_this_week(signed_in, recipe, monkeypatch):
    """
    Back-compat, and the reason both endpoints take an optional body: every
    pre-existing caller (and every test written before this) posts nothing.
    """
    this_monday = _monday(0)
    wednesday = this_monday + datetime.timedelta(days=2)
    _freeze_main_clock(monkeypatch, wednesday)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm",
                        lambda ctx: _week_of(wednesday, 5))

    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200
    assert res.json()["week_start_date"] == this_monday.isoformat()


def test_snacks_per_day_default_is_not_mistaken_for_an_answer():
    """The column is NOT NULL DEFAULT 2, so only the _set flag says the
    household answered; an explicit weekly answer still wins over it."""
    from app.tools import preferences
    assert preferences.resolve_snacks_per_day({"snacks_per_day": 2, "snacks_per_day_set": 0, "snacks_per_week": 5, "snacks_per_week_set": 1}) == 1
    assert preferences.resolve_snacks_per_day({"snacks_per_day": 3, "snacks_per_day_set": 1, "snacks_per_week": 7, "snacks_per_week_set": 1}) == 3
    assert preferences.resolve_snacks_per_day({"snacks_per_day": 2, "snacks_per_day_set": 0, "snacks_per_week": 3, "snacks_per_week_set": 0}) == 2
