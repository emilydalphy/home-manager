"""
Three cards from Emily's walk of a real draft, 2026-09-27 (Sunday, 3:53pm).

CARD 1 — "Draft: no open 'Your call' slots for meals I've already
answered" (decision A). A draft only has an open dinner when nobody is home
or an allergy blocks every option. A night tagged Leftovers reheats an
earlier cook; a same-day lunch → dinner chain is real; a broken dinner
chain is re-pointed or re-picked, never opened. See app/tools/dinner_gaps.py.

CARD 2 — "Draft: 'today' means from now". Meals of today already gone by
are not planned and are not gaps; "today" / "tonight" / "tomorrow" land on
one exact meal, the dish is moved there if the model put it elsewhere, and
"…and have leftovers" is a real chain. See app/tools/today_meals.py and
typed_requests.place_day_requests.

CARD 3 — "Draft: no dish on more than two meals in a row" (decision B).
See leftovers.MAX_MEALS_IN_A_ROW and dinner_gaps.break_long_runs.

Each test says CATCH (red on origin/main 8e046a6, for the behaviour and not
only a missing name) or GUARD (green on main, pinning what must not move).
The model and the swap picker are stubbed throughout — nothing here is the
model's behaviour.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import leftovers, plan_quality
from app.tools import swap_in_place as sip
from conftest import household_date, household_pin


# ---------- helpers ----------

def _next_monday() -> str:
    today = datetime.date.fromisoformat(household_date())
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def _entry(date, slot, meal, minutes=20, **extra):
    return {"date": date, "slot": slot, "meal_name": meal, "is_new_recipe": True,
            "ingredients": [{"item": f"{meal} base", "qty": "1", "category": "pantry"}],
            "reasoning": f"{meal} fits the week", "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": minutes // 2, "cook_time_minutes": minutes - minutes // 2, **extra}


BREAKFASTS = ["Oatmeal", "Eggs on Toast"]
LUNCHES = ["Turkey Wraps", "Lentil Soup"]
DINNERS = ["Lemon Chicken", "Salmon Bowls", "Pork Stir Fry", "Chickpea Curry",
           "Beef Tacos", "Shrimp Pasta", "Tofu Noodles"]


def _week(dates, drop=()) -> list[dict]:
    out = []
    for i, d in enumerate(dates):
        for slot, meal in (("breakfast", BREAKFASTS[i % 2]), ("lunch", LUNCHES[i % 2]), ("dinner", DINNERS[i])):
            if (d, slot) not in drop:
                out.append(_entry(d, slot, meal))
    return out


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


@pytest.fixture
def picker(monkeypatch):
    """The swap's picker, stubbed: quick new dishes in order, every call recorded."""
    calls = []
    names = iter(["Quick Veggie Frittata", "Ten-Minute Fried Rice", "Speedy Bean Tostadas",
                  "Fast Pesto Gnocchi", "Rapid Egg Curry", "Snappy Tuna Melts"])

    def pick(context):
        calls.append(context)
        name = next(names)
        return {"meal_name": name, "reason": "quick and new",
                "ingredients": [{"item": f"{name} base", "qty": "1", "category": "pantry"}],
                "instructions": ["Cook it.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 5, "cook_time_minutes": 10}

    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return calls


def _rows(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.derived_from_json, mpe.open_reason, "
        "       COALESCE(r.name, mpe.freeform_meal) AS meal, r.prep_time_minutes, r.cook_time_minutes "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.component_category IS NULL ORDER BY mpe.date, mpe.id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _at(plan_id, date, slot):
    found = [r for r in _rows(plan_id) if r["date"] == date and r["slot"] == slot]
    assert len(found) == 1, found
    return found[0]


def _derived(row):
    return json.loads(row["derived_from_json"] or "{}")


def _reheats(plan_id, date, slot="dinner"):
    """The (date, slot) of the cook this meal reheats, or None."""
    chains = tools.plan_leftover_chains(plan_id)
    row = _at(plan_id, date, slot)
    link = chains["leftovers"].get(row["id"])
    return (link["source"]["date"], link["source"]["slot"]) if link else None


# =====================================================================
# CARD 1 — a draft never hands back a meal the household is home for
# =====================================================================

def test_a_night_tagged_leftovers_reheats_the_night_before_even_when_the_model_planned_a_new_dinner(stub_model, picker):
    """CATCH. On main the `left` tag is a prompt line only: the model's new
    Wednesday dinner is saved as a fresh cook."""
    week = _next_monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, night_tags={dates[2]: ["left"]})
    stub_model(_week(dates))

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _at(plan_id, dates[2], "dinner")["slot_state"] == "planned"
    assert _reheats(plan_id, dates[2]) == (dates[1], "dinner"), "the nearest cook, one day before"
    source = _at(plan_id, dates[1], "dinner")
    assert f"{dates[2]}:dinner" in _derived(source)["make_double_for"], "the batch is sized up"
    assert _derived(_at(plan_id, dates[2], "dinner"))["tags"] == ["left"]


def test_a_night_tagged_leftovers_that_the_model_handed_back_is_a_reheat_not_a_question(stub_model, picker):
    """CATCH. Emily's screen: Wednesday tagged Leftovers came back "Your call"."""
    week = _next_monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, night_tags={dates[2]: ["left"]})
    days = _week(dates, drop={(dates[2], "dinner")})
    days.append({"date": dates[2], "slot": "dinner", "meal_name": "", "is_new_recipe": False,
                 "reasoning": "", "slot_state": "open", "open_reason": "Wednesday I'd rather ask than guess."})
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _reheats(plan_id, dates[2]) == (dates[1], "dinner")
    assert picker == [], "a reheat needs no model call"


def test_a_leftovers_night_past_three_days_eats_a_frozen_portion(stub_model, picker):
    """CATCH. Mon cooks; Tue–Thu nobody is home; Friday is Leftovers. The
    only cook is four days back, so Friday is a portion frozen on Monday."""
    week = _next_monday()
    dates = tools._week_dates(week)
    tags = {d: ["out"] for d in dates[1:4]}
    tags[dates[4]] = ["left"]
    tools.save_week_intake(week, night_tags=tags)
    stub_model([d for d in _week(dates) if not (d["slot"] == "dinner" and d["date"] in dates[1:4])])

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    friday = _at(plan_id, dates[4], "dinner")
    assert friday["slot_state"] == "planned"
    assert leftovers.frozen_portion_on(_derived(friday)) == DINNERS[0]
    assert "freezer" in friday["meal"].lower()


def test_a_lunch_cooked_big_feeds_that_evenings_dinner(stub_model, picker):
    """CATCH. A same-day lunch → dinner chain was compared by DATE alone
    and reopened as "a meal that hasn't happened yet"."""
    week = _next_monday()
    dates = tools._week_dates(week)
    days = _week(dates, drop={(dates[1], "lunch"), (dates[1], "dinner")})
    days.append(_entry(dates[1], "lunch", "Big Pot Chili", 40))
    days.append(_entry(dates[1], "dinner", "Chili leftovers", 0,
                       derived_from={"links_to": f"{dates[1]}:lunch"}))
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _at(plan_id, dates[1], "dinner")["slot_state"] == "planned"
    assert _reheats(plan_id, dates[1]) == (dates[1], "lunch")
    entries = plan_quality._load_plan_entries(plan_id)
    assert plan_quality._leftover_direction(entries, {}) == []


def test_the_quality_rule_reads_eating_order_not_the_date():
    """CATCH (plan_quality._leftover_direction). Lunch → dinner is fine;
    dinner → that day's lunch is still backwards."""
    ok = [{"date": "2026-10-06", "slot": "dinner", "slot_state": "planned", "meal_name": "Chili",
           "links_to": "2026-10-06:lunch"}]
    bad = [{"date": "2026-10-06", "slot": "lunch", "slot_state": "planned", "meal_name": "Chili",
            "links_to": "2026-10-06:dinner"}]
    assert plan_quality._leftover_direction(ok, {}) == []
    assert len(plan_quality._leftover_direction(bad, {})) == 1


def test_a_broken_dinner_chain_is_repointed_at_the_nearest_earlier_cook(stub_model, picker):
    """CATCH. Wednesday "leftovers" of Friday's cook: on main, reopened as a
    question. Now it reheats Tuesday's dinner, the nearest earlier cook."""
    week = _next_monday()
    dates = tools._week_dates(week)
    days = _week(dates, drop={(dates[2], "dinner")})
    days.append(_entry(dates[2], "dinner", "Taco leftovers", 0, derived_from={"links_to": f"{dates[4]}:dinner"}))
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    wednesday = _at(plan_id, dates[2], "dinner")
    assert wednesday["slot_state"] == "planned"
    assert _reheats(plan_id, dates[2]) == (dates[1], "dinner")
    assert _derived(wednesday)["repointed"]["was"] == f"{dates[4]}:dinner"


def test_a_broken_chain_with_no_earlier_cook_is_repicked_to_fit_the_nights_cap(stub_model, picker):
    """CATCH. Monday — the first night — "reheats" Wednesday. Nothing earlier
    to reheat, so it is re-picked fresh, and Monday is short on time."""
    week = _next_monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, night_tags={dates[0]: ["rush"]})
    days = _week(dates, drop={(dates[0], "dinner")})
    days.append(_entry(dates[0], "dinner", "Chili leftovers", 0, derived_from={"links_to": f"{dates[2]}:dinner"}))
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    monday = _at(plan_id, dates[0], "dinner")
    assert monday["slot_state"] == "planned"
    assert monday["meal"] == "Quick Veggie Frittata"
    assert (monday["prep_time_minutes"] or 0) + (monday["cook_time_minutes"] or 0) <= 30
    assert picker, "the pick went through the swap's picker"


def test_a_dinner_the_model_left_out_is_planned_not_asked(stub_model, picker):
    """CATCH. On main a missing dinner is an open question (generation_gap)."""
    week = _next_monday()
    dates = tools._week_dates(week)
    stub_model(_week(dates, drop={(dates[3], "dinner")}))

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    thursday = _at(plan_id, dates[3], "dinner")
    assert thursday["slot_state"] == "planned"
    assert _reheats(plan_id, dates[3]) == (dates[2], "dinner")
    assert not [r for r in _rows(plan_id) if r["slot_state"] == "open"]


def test_a_week_is_never_left_with_an_open_dinner_the_household_is_home_for(stub_model, picker):
    """CATCH, the card's own acceptance line (d): the model hands back
    two dinners and forgets a third; none of the three is a question."""
    week = _next_monday()
    dates = tools._week_dates(week)
    days = _week(dates, drop={(dates[0], "dinner"), (dates[3], "dinner"), (dates[5], "dinner")})
    for d in (dates[0], dates[3]):
        days.append({"date": d, "slot": "dinner", "meal_name": "", "is_new_recipe": False, "reasoning": "",
                     "slot_state": "open", "open_reason": "I'd rather ask than guess."})
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    dinners = [r for r in _rows(plan_id) if r["slot"] == "dinner"]
    assert {r["slot_state"] for r in dinners} == {"planned"}
    entries = plan_quality._load_plan_entries(plan_id)
    assert plan_quality._open_slot_budget(entries, {}) == []


def test_a_nobody_home_night_is_still_planned_empty(stub_model, picker):
    """GUARD. An out night is an answer, not a gap."""
    week = _next_monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, night_tags={dates[4]: ["out"]})
    stub_model(_week(dates, drop={(dates[4], "dinner")}))

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _at(plan_id, dates[4], "dinner")["slot_state"] == "planned_empty"


def test_an_allergen_question_keeps_its_words():
    """GUARD on the carve-out (allergen_gate.py): the dinner pass never
    fills an open slot whose question says what an allergy ruled out."""
    from app.tools import dinner_gaps, allergen_gate
    assert dinner_gaps.keeps_its_question({"constraint": allergen_gate.ALLERGEN_CONSTRAINT})
    assert dinner_gaps.keeps_its_question({"need": "away", "undone_by": "attendance"})
    assert dinner_gaps.keeps_its_question({"holiday": "Thanksgiving", "constraint": "hosting"})
    assert not dinner_gaps.keeps_its_question({"constraint": "generation_gap"})
    assert not dinner_gaps.keeps_its_question({"repaired": "leftovers_backwards"})


# =====================================================================
# CARD 2 — "today" means from now
# =====================================================================

SUNDAY = "2026-09-27"
PERIOD = [(datetime.date(2026, 9, 27) + datetime.timedelta(days=i)).isoformat() for i in range(6)]  # Sun–Fri


def _at_353pm(monkeypatch, frozen_today):
    """Sunday 27 September 2026, 3:53pm in Toronto — the whole clock pinned
    there. The tests that call this carry @pytest.mark.real_time_of_day, so
    conftest leaves the time of day alone (it pins every other test to just
    after midnight), and nothing here names a module main has not got: these
    are behaviour catches against main, not import errors."""
    frozen_today(household_pin(15, 53, on=datetime.date(2026, 9, 27)))


def test_the_past_meals_of_today_are_the_ones_whose_time_has_come():
    """CATCH (name-level on main: today_meals is new). Breakfast 8:00,
    lunch 12:30, dinner 18:30 — the Now screen's own clock."""
    from app.tools import today_meals
    at = lambda h, m: datetime.datetime(2026, 9, 27, h, m)  # noqa: E731
    assert today_meals.past_meals(PERIOD, at(15, 53)) == [
        {"date": SUNDAY, "slot": "breakfast"}, {"date": SUNDAY, "slot": "lunch"}]
    assert today_meals.past_meals(PERIOD, at(7, 0)) == []
    assert today_meals.past_meals(PERIOD[1:], at(15, 53)) == [], "a plan starting tomorrow has no past"
    assert today_meals.first_meal_ahead(at(15, 53)) == "dinner"
    assert today_meals.first_meal_ahead(at(21, 0)) is None


def test_today_tonight_and_tomorrow_resolve_to_one_exact_meal():
    """CATCH (name-level on main). The parser behind freeform_on_a_day."""
    past = [{"date": SUNDAY, "slot": "breakfast"}, {"date": SUNDAY, "slot": "lunch"}]

    def resolve(text, first="dinner"):
        return [(r["date"], r["slot"], r["leftovers"])
                for r in tools.freeform_day_requests(text, PERIOD, SUNDAY, first, past=past)]

    assert resolve("I want to make a Japanese curry today and have leftovers for it") == [(SUNDAY, "dinner", True)]
    assert resolve("Tacos tonight.") == [(SUNDAY, "dinner", False)]
    assert resolve("Pancakes tomorrow morning.") == [(PERIOD[1], "breakfast", False)]
    assert resolve("Soup for lunch tomorrow.") == [(PERIOD[1], "lunch", False)]
    assert resolve("Let's do the lamb tomorrow") == [(PERIOD[1], "dinner", False)]
    assert resolve("Salad for lunch today") == [], "lunch has gone by"
    assert resolve("Not tonight, we're busy") == []
    assert resolve("I have corn that I need to use") == []
    assert resolve("Curry today", first=None) == [], "nothing left of today"


@pytest.mark.real_time_of_day
def test_emilys_sunday_at_353pm(monkeypatch, frozen_today, stub_model, picker):
    """
    CATCH — Emily's walk, as a regression test. Sunday 27 September 2026 at
    3:53pm Toronto; the week is Sunday to Friday; Wednesday is Leftovers,
    Friday short on time; the note: "I have corn that I need to use so
    suggest a dish with that. And I want to make a Japanese curry heavy on
    veggies today to use up stuff in the fridge and have leftovers for it."
    The model put the curry on Sunday LUNCH and left Sunday dinner open.
    """
    _at_353pm(monkeypatch, frozen_today)
    note = ("I have corn that I need to use so suggest a dish with that. And I want to make a Japanese "
            "curry heavy on veggies today to use up stuff in the fridge and have leftovers for it.")
    tools.save_week_intake(SUNDAY, day_count=6, freeform=note,
                           night_tags={PERIOD[3]: ["left"], PERIOD[5]: ["rush"]})
    curry_words = "I want to make a Japanese curry heavy on veggies today"
    days = []
    for i, d in enumerate(PERIOD):
        days.append(_entry(d, "breakfast", BREAKFASTS[i % 2], 10))
        if i == 0:
            days.append(_entry(d, "lunch", "Japanese Vegetable Curry", 45, derived_from={"freeform": curry_words}))
            days.append({"date": d, "slot": "dinner", "meal_name": "", "is_new_recipe": False, "reasoning": "",
                         "slot_state": "open", "open_reason": "Sunday I'd rather ask than guess."})
            continue
        days.append(_entry(d, "lunch", LUNCHES[i % 2], 15))
    days += [
        _entry(PERIOD[1], "dinner", "Corn and Black Bean Tacos", 25, derived_from={"inventory": ["corn"]}),
        _entry(PERIOD[2], "dinner", "Lemon Chicken", 30),
        _entry(PERIOD[3], "dinner", "Salmon Bowls", 25),
        _entry(PERIOD[4], "dinner", "Pork Stir Fry", 25),
        _entry(PERIOD[5], "dinner", "Cheese Quesadillas", 15),
    ]
    stub_model(days)

    plan = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)
    plan_id = plan["weekly_plan_id"]

    # Breakfast and lunch had gone by: not planned, and not questions.
    for slot in ("breakfast", "lunch"):
        row = _at(plan_id, SUNDAY, slot)
        assert row["slot_state"] == "planned_empty", slot
        assert _derived(row)["constraint"] == "already_past"
    # "Today" at 3:53pm is dinner: the curry is moved there.
    dinner = _at(plan_id, SUNDAY, "dinner")
    assert (dinner["slot_state"], dinner["meal"]) == ("planned", "Japanese Vegetable Curry")
    # "…and have leftovers for it": Wednesday's Leftovers night eats it
    # (three days on, the furthest reach), not Tuesday's chicken.
    assert _reheats(plan_id, PERIOD[3]) == (SUNDAY, "dinner")
    # No open slot anywhere, and the audit is whole.
    assert not [r for r in _rows(plan_id) if r["slot_state"] == "open"]
    assert tools.audit_plan_slots(plan_id)["complete"] is True
    # The draft says the one move it had to make, in one plain line.
    menu = tools.get_week_menu(plan_id)
    assert "I moved Japanese Vegetable Curry to Sunday dinner, as you asked." in menu["draft_opener"]
    # The menu names the past meals "Not planned", not "Out".
    sunday = next(d for d in menu["days"] if d["date"] == SUNDAY)
    assert sunday["lunch"]["title"] == "Not planned"


@pytest.mark.real_time_of_day
def test_a_dish_already_where_it_was_asked_is_not_announced(monkeypatch, frozen_today, stub_model, picker):
    """GUARD on "states only moves it had to make" (green on main, which
    has no such line at all): the model put tonight's curry on tonight."""
    _at_353pm(monkeypatch, frozen_today)
    tools.save_week_intake(SUNDAY, day_count=6, freeform="Japanese curry tonight please.")
    days = [_entry(d, s, m) for d, s, m in (
        (d, s, BREAKFASTS[0] if s == "breakfast" else LUNCHES[i % 2] if s == "lunch" else DINNERS[i])
        for i, d in enumerate(PERIOD) for s in ("breakfast", "lunch", "dinner"))
        if not (d == SUNDAY and s == "dinner")]
    days.append(_entry(SUNDAY, "dinner", "Japanese Curry", 40))
    stub_model(days)

    plan_id = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]

    assert _at(plan_id, SUNDAY, "dinner")["meal"] == "Japanese Curry"
    assert not any("moved" in line for line in tools.get_week_menu(plan_id)["draft_opener"])


@pytest.mark.real_time_of_day
def test_the_generation_context_carries_the_time_and_the_exact_meal(monkeypatch, frozen_today, picker):
    """CATCH. The model is told what the code knows: the time, the meals
    gone by, and where "today" lands."""
    _at_353pm(monkeypatch, frozen_today)
    tools.save_week_intake(SUNDAY, day_count=6, freeform="Curry today.")
    seen = {}

    def fake(ctx):
        seen.update(ctx)
        return _week(PERIOD)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)

    agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)

    assert seen["time_now"] == "15:53"
    assert seen["today"].startswith(SUNDAY)
    assert seen["past_meals_today"] == [{"date": SUNDAY, "slot": "breakfast"}, {"date": SUNDAY, "slot": "lunch"}]
    assert [(r["date"], r["slot"]) for r in seen["freeform_on_a_day"]] == [(SUNDAY, "dinner")]


# =====================================================================
# CARD 3 — no dish on more than two meals in a row
# =====================================================================

def test_the_rule_counts_lunches_and_dinners_in_eating_order():
    """CATCH (name-level on main). Thu dinner + Fri lunch is two; Fri dinner
    would be the third."""
    keys = {("2026-10-08", "dinner"): "chili", ("2026-10-09", "lunch"): "chili",
            ("2026-10-09", "dinner"): "tacos"}
    assert leftovers.run_through(keys, "2026-10-09", "lunch") == 2
    assert not leftovers.too_many_in_a_row(keys, "2026-10-09", "lunch")
    assert leftovers.too_many_in_a_row(keys, "2026-10-09", "dinner", "Chili leftovers")
    assert leftovers.long_runs(keys) == []
    keys[("2026-10-09", "dinner")] = "chili"
    assert leftovers.long_runs(keys) == [[("2026-10-08", "dinner"), ("2026-10-09", "lunch"), ("2026-10-09", "dinner")]]
    assert leftovers.dish_identity("Leftovers from the freezer — Monday’s Chili") == "chili"


def test_the_quality_tripwire_names_a_third_meal_in_a_row():
    """CATCH. plan_quality._no_long_runs — a reheat counts as its cook."""
    week = [
        {"entry_id": 1, "date": "2026-10-08", "slot": "dinner", "slot_state": "planned", "meal_name": "Chili"},
        {"entry_id": 2, "date": "2026-10-09", "slot": "lunch", "slot_state": "planned", "meal_name": "Chili",
         "links_to": "entry_id:1"},
        {"entry_id": 3, "date": "2026-10-09", "slot": "dinner", "slot_state": "planned",
         "meal_name": "Leftover chili", "links_to": "2026-10-08:dinner"},
    ]
    said = [v for v in plan_quality.check_week(week, {}) if v.rule == "no_long_runs"]
    assert len(said) == 1 and "3 meals in a row" in said[0].message
    week[2]["meal_name"], week[2]["links_to"] = "Tacos", None
    assert [v for v in plan_quality.check_week(week, {}) if v.rule == "no_long_runs"] == []


def test_emilys_rush_friday_after_a_leftovers_lunch_is_a_different_dinner(stub_model, picker):
    """
    CATCH — Emily's example. Thursday's dinner is cooked; Friday's lunch is
    its leftovers (the household said so on step 3); Friday is short on
    time and the model scaled Thursday up for it too. On main Friday dinner
    is the same dish a third meal running. Now it is something else, quick.
    """
    week = _next_monday()
    dates = tools._week_dates(week)
    thu, fri = dates[3], dates[4]
    tools.save_week_intake(week, night_tags={fri: ["rush"]},
                           weekday_lunches={"days": [{"date": fri, "kind": "leftovers"}]})
    days = _week(dates, drop={(thu, "dinner"), (fri, "dinner")})
    days.append(_entry(thu, "dinner", "Beef Chili", 50))
    days.append(_entry(fri, "dinner", "Chili leftovers", 0, derived_from={"links_to": f"{thu}:dinner"}))
    stub_model(days)

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert _reheats(plan_id, fri, "lunch") == (thu, "dinner"), "the lunch they asked for stands"
    friday = _at(plan_id, fri, "dinner")
    reheat = _reheats(plan_id, fri)
    assert friday["slot_state"] == "planned"
    assert reheat != (thu, "dinner") and "chili" not in friday["meal"].lower(), \
        "Friday dinner is not the third Beef Chili in a row"
    assert reheat is not None or (friday["prep_time_minutes"] or 0) + (friday["cook_time_minutes"] or 0) <= 30, \
        "and it is quick: a reheat, or a cook inside the rush cap"
    entries = plan_quality._load_plan_entries(plan_id)
    assert [v for v in plan_quality.check_week(entries, {}) if v.rule == "no_long_runs"] == []


def test_a_weekday_leftovers_lunch_is_not_made_the_third_in_a_row():
    """CATCH (weekday_lunches.apply_to_plan). Mon dinner, Tue lunch and Tue
    dinner already all the same dish: Wednesday's leftovers lunch is left as
    drafted rather than made a fourth."""
    from app.tools import weekday_lunches
    week = _next_monday()
    dates = tools._week_dates(week)
    plan_id = tools.create_weekly_plan(week, content_start_date=week, day_count=7)["weekly_plan_id"]
    cook = tools.plan_meal(dates[0], "Beef Stew", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(dates[1], "Beef Stew", slot="lunch", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"entry_id:{cook['entry_id']}"})
    tools.plan_meal(dates[1], "Beef Stew", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"entry_id:{cook['entry_id']}"})
    tools.plan_meal(dates[2], "Turkey Wraps", slot="lunch", weekly_plan_id=plan_id)
    intake = {"weekday_lunches": {"days": [{"date": dates[2], "kind": "leftovers"}]}}

    out = weekday_lunches.apply_to_plan(plan_id, intake)

    assert _at(plan_id, dates[2], "lunch")["meal"] == "Turkey Wraps"
    assert any("in a row" in s["why"] for s in out["skipped"])


def test_the_fold_does_not_link_a_night_into_a_third_meal_in_a_row():
    """CATCH (meal_variety._plan_batches). A lunch that would eat the
    dinner before it when that evening's dinner is the same dish again."""
    from app.tools import meal_variety
    dish = {"name": "Chili", "nights": [], "protected": False, "chained": False, "food_groups": None, "minutes": 20}
    night = {"id": 2, "date": "2026-10-09", "slot": "lunch", "dish": {"name": "Wraps", "protected": False,
             "chained": False, "nights": [], "food_groups": None, "minutes": 10},
             "meal": "Wraps", "derived": {}, "reheat_of": None, "source": False, "done": False, "entry": {}}
    outside = [{"id": 1, "date": "2026-10-08", "slot": "dinner", "dish": dish, "size": 1, "night": None}]
    keys = {("2026-10-08", "dinner"): "chili", ("2026-10-09", "dinner"): "chili"}

    decisions, _ = meal_variety._plan_batches([night], [dish], None, "lunch", outside, relay=False, run_keys=keys)
    assert decisions[0]["kind"] != "link"
    decisions, _ = meal_variety._plan_batches([night], [dish], None, "lunch", outside, relay=False,
                                               run_keys={("2026-10-08", "dinner"): "chili"})
    assert decisions[0]["kind"] == "link", "without the third meal it links as before"
