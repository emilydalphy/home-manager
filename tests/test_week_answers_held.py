"""
"Telling the generator something is not the same as preventing it"
(CLAUDE.md), for three of the household's own answers that only the prompt
carried (overnight hunt, 2026-10-29). Each test stubs the model's week to
BREAK the answer and runs it through the real generate_weekly_plan:

  * "No fish this week" typed on the week — a salmon dinner stood.
    week_intake.freeform_exclusions + typed_requests.keep_exclusions_off.
  * a protein set to Skip in Settings (rating 1) — pork chops stood.
    typed_requests.skipped_proteins, the same pass.
  * "Tacos Tuesday" — the tacos stood on Saturday and the draft said
    "Tacos Tuesday Saturday, as you asked." cap_enforce.place_on_named_days.

Plus a pin for an answer that WAS enforced with no test through generation:
Dinners 0 empties every dinner the model sent anyway.
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import swap_in_place as sip
from app.tools import typed_requests


def _monday(offset_weeks: int = 1) -> str:
    from conftest import household_today
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name, protein=None, **extra):
    d = {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
         "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
         "instructions": [f"Cook the {name.lower()}.", "Serve."],
         "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
         "prep_time_minutes": 10, "cook_time_minutes": 20}
    if protein:
        d["main_protein"] = protein
    d.update(extra)
    return d


DINNERS = [("Greek chicken", "chicken"), ("Beef chili", "beef"), ("Tofu stir fry", "tofu"),
           ("Lentil dal", "lentils"), ("Turkey meatballs", "turkey"), ("Bean tacos", "beans"),
           ("Chickpea curry", "chickpeas")]


def _week(week, override=None):
    out = []
    for i, d in enumerate(tools._week_dates(week)):
        name, protein, extra = (override or {}).get(i) or (*DINNERS[i], {})
        out.append(_slot(d, "dinner", name, protein, **extra))
    return out


def _dinners(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, mpe.slot_state, mpe.derived_from_json, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' ORDER BY mpe.date", (plan_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture
def week(monkeypatch):
    """Stub the week's model call; return the plan id it generates."""
    def _run(days, report=None):
        out = agent.GeneratedDays(days)
        out.report = report or {}
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: out)
        return agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    return _run


@pytest.fixture
def picker(monkeypatch):
    """The swap's one-dish picker, stubbed: hands back `queue` in order,
    then a lentil soup."""
    seen, queue = [], []

    def pick(context):
        seen.append(context)
        if queue:
            return dict(queue.pop(0))
        return {"meal_name": "Red lentil soup", "ingredients": [{"item": "Red lentils", "qty": "1 cup"}],
                "prep_time_minutes": 5, "cook_time_minutes": 20, "main_protein": "lentils",
                "food_groups": ["protein", "vegetable", "carb"]}
    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return seen, queue


# ---------- "No fish this week" ----------


def test_a_fish_dinner_on_a_no_fish_week_is_replaced(week, picker):
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="No fish this week please")
    plan_id = week(_week(wk, {2: ("Baked salmon with rice", "salmon", {})}))
    meals = [d["meal"] for d in _dinners(plan_id)]
    assert "Baked salmon with rice" not in meals and "Red lentil soup" in meals, meals
    seen, _ = picker
    assert any("fish" in (c.get("dislikes") or []) for c in seen), "the picker is told, too"


def test_a_pick_that_still_has_the_fish_is_thrown_away_and_the_draft_says_what_stood(week, picker):
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="no fish this week")
    _, queue = picker
    queue.extend([{"meal_name": "Cod tacos", "main_protein": "cod", "prep_time_minutes": 5,
                   "cook_time_minutes": 10}] * 20)
    plan_id = week(_week(wk, {2: ("Baked salmon with rice", "salmon", {})}))
    meals = [d["meal"] for d in _dinners(plan_id)]
    assert "Cod tacos" not in meals, meals
    assert "Baked salmon with rice" in meals, "nothing without fish came back, so it stands"
    opener = tools.get_week_menu(plan_id)["draft_opener"]
    assert any("Baked salmon with rice is still on Wednesday" in line and "without fish" in line
               for line in opener), opener


def test_a_fish_dish_they_asked_for_by_name_stays(week, picker):
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="No fish this week. Salmon on Wednesday for Nana though")
    plan_id = week(_week(wk, {2: ("Baked salmon with rice", "salmon",
                                  {"derived_from": {"freeform": "Salmon on Wednesday for Nana though"}})}))
    assert "Baked salmon with rice" in [d["meal"] for d in _dinners(plan_id)]


@pytest.mark.parametrize("text,food,slots", [
    ("No fish this week", "fish", ("breakfast", "lunch", "dinner", "snack")),
    ("No fish for dinner this week", "fish", ("dinner",)),
    ("We don't want mushrooms this week", "mushrooms", ("breakfast", "lunch", "dinner", "snack")),
    ("Friday is pizza night. No shellfish.", "shellfish", ("breakfast", "lunch", "dinner", "snack")),
])
def test_the_exclusions_read_off_their_words(text, food, slots):
    assert [(e["food"], e["slots"]) for e in tools.freeform_exclusions(text)] == [(food, slots)]


@pytest.mark.parametrize("text", [
    "no fish for Mia",                     # a person's, not the week's
    "no red meat during the week",         # a stretch of days
    "no pasta two nights in a row",        # a count
    "no takeout", "no repeats", "no leftovers this week", "skip lunch on the go",
    "nothing spicy at breakfast", "I have no time",
])
def test_anything_that_is_not_plainly_a_food_kept_off_the_week_is_left_to_the_model(text):
    assert tools.freeform_exclusions(text) == []


# ---------- a protein set to Skip ----------


def test_a_protein_set_to_skip_is_replaced(week, picker):
    tools.add_member("Emily")
    tools.set_household_meal_preferences(protein_preferences={"pork": 1}, mark_complete=False)
    plan_id = week(_week(_monday(), {2: ("Pork chops with apples", "pork", {})}))
    meals = [d["meal"] for d in _dinners(plan_id)]
    assert "Pork chops with apples" not in meals and "Red lentil soup" in meals, meals


def test_a_protein_rated_two_is_not_a_skip(week, picker):
    tools.add_member("Emily")
    tools.set_household_meal_preferences(protein_preferences={"pork": 2}, mark_complete=False)
    plan_id = week(_week(_monday(), {2: ("Pork chops with apples", "pork", {})}))
    assert "Pork chops with apples" in [d["meal"] for d in _dinners(plan_id)]


def test_a_skipped_protein_is_about_the_main_protein_not_an_ingredient():
    """Skip Eggs is not "no cake": the chip is the meal's protein."""
    [skip] = typed_requests.skipped_proteins({"protein_preferences": {"Eggs": 1, "Chicken": 5}})
    avoid = typed_requests._exclusion_avoidance(skip)
    assert typed_requests._carries({"meal": "Spinach frittata", "main_protein": "eggs"}, avoid)
    assert not typed_requests._carries(
        {"meal": "Chicken pot pie", "main_protein": "chicken",
         "ingredients": [{"item": "Egg"}, {"item": "Chicken thighs"}]}, avoid)


# ---------- "Tacos Tuesday" ----------


def _tacos_on_saturday(wk, tuesday_extra=None):
    report = {"honoured_requests": [{"words": "Tacos Tuesday please", "label": "tacos Tuesday"}],
              "unmet_requests": []}
    override = {5: ("Beef tacos", "beef", {"derived_from": {"freeform": "Tacos Tuesday please"}})}
    if tuesday_extra:
        override[1] = tuesday_extra
    return _week(wk, override), report


def test_a_dish_asked_for_on_a_named_night_goes_on_that_night(week):
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="Tacos Tuesday please")
    plan_id = week(*_tacos_on_saturday(wk))
    by_date = {d["date"]: d["meal"] for d in _dinners(plan_id)}
    dates = tools._week_dates(wk)
    assert by_date[dates[1]] == "Beef tacos" and by_date[dates[5]] == "Beef chili", by_date
    assert tools.get_week_menu(plan_id)["draft_opener"][0] == "Tacos Tuesday, as you asked."


def test_a_named_night_that_cannot_take_it_is_said_never_as_asked(week):
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="Tacos Tuesday please. Chili Tuesday too")
    days, report = _tacos_on_saturday(
        wk, ("Beef chili", "beef", {"derived_from": {"freeform": "Chili Tuesday too"}}))
    plan_id = week(days, report)
    by_date = {d["date"]: d["meal"] for d in _dinners(plan_id)}
    dates = tools._week_dates(wk)
    assert by_date[dates[1]] == "Beef chili" and by_date[dates[5]] == "Beef tacos", by_date
    opener = " ".join(tools.get_week_menu(plan_id)["draft_opener"])
    assert "Saturday, as you asked" not in opener, opener
    assert "Beef tacos is on Saturday, not Tuesday" in opener, opener


# ---------- pinned: Dinners 0 ----------


def test_dinners_zero_empties_every_dinner_the_model_sent_anyway(week):
    """Enforced in _finish_week_slots' zero-count pass and tested for
    breakfasts and lunches, never for dinners through generation."""
    tools.add_member("Emily")
    tools.set_household_meal_preferences(dinners_per_week=0, mark_complete=False)
    plan_id = week(_week(_monday()))
    dinners = _dinners(plan_id)
    assert len(dinners) == 7 and all(d["slot_state"] == "planned_empty" for d in dinners), dinners
    assert all('"dinners_per_week:0"' in (d["derived_from_json"] or "") for d in dinners)


def test_the_exclusion_cited_on_the_dish_it_excludes_does_not_protect_it(week, picker):
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="No fish this week")
    plan_id = week(_week(wk, {2: ("Baked salmon with rice", "salmon",
                                  {"derived_from": {"freeform": "No fish this week"}})}))
    assert "Baked salmon with rice" not in [d["meal"] for d in _dinners(plan_id)]


# ---------- independent review, 2026-10-29 ----------


def test_a_fish_dish_citing_some_other_request_is_not_protected(week, picker):
    """1. Only words ABOUT the dish protect it — not "Something quick Wednesday"."""
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="No fish this week. Something quick Wednesday")
    plan_id = week(_week(wk, {2: ("Quick salmon bowls", "salmon",
                                  {"derived_from": {"freeform": "Something quick Wednesday"}})}))
    assert "Quick salmon bowls" not in [d["meal"] for d in _dinners(plan_id)]


@pytest.mark.parametrize("text", [
    "No fish, unless it's salmon",          # 2. a carve-out is the model's
    "no pork except bacon",
    "Busy week. No salt please",            # 5. pantry basics and adjectives
    "no hot", "no sugar", "no oil this week", "no fresh",
])
def test_carve_outs_and_pantry_basics_are_left_to_the_model(text):
    assert tools.freeform_exclusions(text) == []


def test_a_named_weekday_outside_the_period_never_reads_as_asked(monkeypatch):
    """3. A Wednesday-to-Sunday plan has no Tuesday: say where the tacos are."""
    tools.add_member("Emily")
    wk = _monday()
    dates = tools._week_dates(wk)
    tools.save_week_intake(wk, freeform="Tacos Tuesday please", day_count=7)
    days = [_slot(d, "dinner", *DINNERS[i]) for i, d in enumerate(dates) if i >= 2]
    days[3] = _slot(dates[5], "dinner", "Beef tacos", "beef", derived_from={"freeform": "Tacos Tuesday please"})
    out = agent.GeneratedDays(days)
    out.report = {"honoured_requests": [{"words": "Tacos Tuesday please", "label": "tacos Tuesday"}],
                  "unmet_requests": []}
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: out)
    plan_id = agent.generate_weekly_plan(wk, period_start=dates[2], day_count=5)["weekly_plan_id"]
    opener = " ".join(tools.get_week_menu(plan_id)["draft_opener"])
    assert "as you asked" not in opener, opener
    assert "Beef tacos is on Saturday — there’s no Tuesday in this plan." in opener, opener


@pytest.mark.parametrize("bad", ["Greek chicken", "Chicken pesto pasta"])
def test_an_exclusion_re_pick_never_repeats_a_dish_or_a_third_protein(week, picker, bad):
    """4. "Greek chicken" is already Monday; a third chicken cook is past the rule."""
    tools.add_member("Emily")
    wk = _monday()
    tools.save_week_intake(wk, freeform="No fish this week")
    _, queue = picker
    queue.append({"meal_name": bad, "main_protein": "chicken", "prep_time_minutes": 5, "cook_time_minutes": 20})
    plan_id = week(_week(wk, {2: ("Baked salmon with rice", "salmon", {}),
                              3: ("Chicken fajitas", "chicken", {})}))
    meals = [d["meal"] for d in _dinners(plan_id)]
    assert meals.count("Greek chicken") == 1 and "Chicken pesto pasta" not in meals, meals
    assert "Red lentil soup" in meals, meals


@pytest.mark.parametrize("food,dish,protein", [
    ("fish", "Thai green curry", "tofu"),       # 6. fish sauce is the allergy gate's, not a taste no
    ("fish", "Worcestershire burgers", "beef"),
    ("pork", "Pork-free chili", "beans"),
    ("chicken", "Tofu stir fry", "tofu"),
    ("beans", "Chicken with green beans", "chicken"),   # Skip Beans is the legume
])
def test_an_exclusion_is_about_what_the_dish_is(food, dish, protein):
    avoid = typed_requests._exclusion_avoidance({"food": food, "words": f"no {food}"})
    assert not typed_requests._carries({"meal": dish, "main_protein": protein}, avoid)


def test_only_an_exact_skip_word_is_a_skip():
    found = typed_requests.skipped_proteins({"protein_preferences": {
        "pork": "avoid", "beef": "none this month", "lamb": "no strong feelings", "tofu": "skip"}})
    assert sorted(e["food"] for e in found) == ["pork", "tofu"]
