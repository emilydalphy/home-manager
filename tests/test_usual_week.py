"""
The household's usual week drives every plan (Loop Board "Your week:
meals × days × who's eating, prep day, and variety drive every plan",
2026-09-30). See app/tools/usual_week.py.

Every generation stubs the model (the week) and the picker (a re-pick).
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import allergen_gate, swap_in_place as sip, usual_week, meal_variety


WEEKDAYS = usual_week.WEEKDAYS


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _slot(date, slot, name):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "1"}], "reasoning": f"{name} because",
            "food_groups": ["protein", "vegetable", "carb"]}


def _days(dates, *, breakfasts, lunches, dinners, snacks=None) -> list[dict]:
    out = []
    for i, date in enumerate(dates):
        if breakfasts and breakfasts[i]:
            out.append(_slot(date, "breakfast", breakfasts[i]))
        if lunches and lunches[i]:
            out.append(_slot(date, "lunch", lunches[i]))
        if dinners and dinners[i]:
            out.append(_slot(date, "dinner", dinners[i]))
        for s in (snacks[i] if snacks else []):
            out.append(_slot(date, "snack", s))
    return out


def _pick(name: str) -> dict:
    return {
        "meal_name": name, "reason": "something new",
        "ingredients": [{"item": f"{name} stuff", "qty": "1", "category": "pantry"}],
        "instructions": ["Cook.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 10,
    }


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen.setdefault("ctxs", []).append(ctx)
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen
    return _stub


@pytest.fixture
def picker(monkeypatch):
    calls = []
    names = iter(["Moussaka", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal", "Laksa",
                  "Tagine", "Shakshuka", "Congee", "Frittata", "Porridge"])

    def pick(context):
        calls.append(context)
        return _pick(next(names))

    monkeypatch.setattr(sip, "_pick_replacement", pick)

    def quick(context):
        calls.append(context)
        full = _pick(next(names))
        return {"meal_name": full["meal_name"], "ingredients": ["Stuff"], "dish_note": "Simple.",
                "food_groups": full["food_groups"]}

    # The fast path (first-week gaps, held-back re-picks) never reaches the API.
    monkeypatch.setattr(allergen_gate, "quick_pick", quick)
    return calls


def _distinct(plan_id: int, slot: str) -> set[str]:
    dishes = meal_variety._group_dishes(meal_variety._load_slot_entries(plan_id, slot),
                                        tools.plan_leftover_chains(plan_id))
    return {d["name"].lower() for d in dishes}


def _meals(plan_id: int) -> list[dict]:
    return tools.get_weekly_plan(plan_id)["meals"]


def _two_people() -> dict[str, int]:
    return {n: tools.add_member(n)["member_id"] for n in ("Emily", "Vineeth")}


# ---------- the mapping ----------

def test_the_variety_mapping_is_emilys_numbers_in_one_place():
    c = usual_week.VARIETY_CHOICES
    assert c["breakfast"] == {"go_to_or_two": 2, "few_in_rotation": 3, "new_every_day": "days"}
    assert c["lunch"] == {"last_nights_dinner": "leftovers", "meal_prep_ahead": 2,
                          "few_in_rotation": 3, "new_every_day": "days"}
    assert c["dinner"] == {"cook_big_eat_twice": 2, "few_in_rotation": 4, "new_every_day": "days"}
    # Clamped to the days the meal is on.
    assert usual_week.resolve_dishes("dinner", "few_in_rotation", 3) == 3
    assert usual_week.resolve_dishes("breakfast", "new_every_day", 5) == 5
    assert usual_week.resolve_dishes("breakfast", "go_to_or_two", 0) == 0


def test_saving_stores_the_choice_and_the_number():
    tools.save_usual_week(
        grid={"breakfast": {d: "off" for d in WEEKDAYS[:5]}},
        variety={"breakfast": "new_every_day", "lunch": "few_in_rotation", "dinner": "cook_big_eat_twice"},
    )
    week = tools.get_usual_week()
    assert week["answered"] is True
    assert week["variety"]["breakfast"] == {"choice": "new_every_day", "dishes": 2, "days_on": 2}
    assert week["variety"]["lunch"]["dishes"] == 3 and week["variety"]["dinner"]["dishes"] == 2
    memory = tools.get_household_memory()
    assert (memory["breakfasts_per_week"], memory["lunches_per_week"], memory["dinners_per_week"]) == (2, 3, 2)
    assert memory["meal_counts_set"] is True
    conn = get_conn()
    stored = conn.execute("SELECT usual_week_json FROM meal_preferences").fetchone()[0]
    conn.close()
    assert '"choice": "new_every_day"' in stored and '"dishes": 2' in stored


# ---------- existing households ----------

def test_an_existing_household_reads_a_grid_derived_from_its_settings():
    tools.set_household_meal_preferences(dinners_per_week=3, breakfasts_per_week=1, lunches_per_week=0)
    week = tools.get_usual_week()
    assert week["answered"] is False
    assert set(week["grid"]["dinner"].values()) == {"everyone"}
    assert set(week["grid"]["lunch"].values()) == {"off"}
    assert {m: v["dishes"] for m, v in week["variety"].items()} == {"breakfast": 1, "lunch": 0, "dinner": 3}
    assert all(v["choice"] is None for v in week["variety"].values())
    # Reading writes nothing.
    conn = get_conn()
    assert conn.execute("SELECT usual_week_json FROM meal_preferences").fetchone()[0] == ""
    conn.close()


def test_an_existing_household_gets_the_same_generation_inputs_before_and_after(stub_model, picker):
    """Today's settings (3 dinners / 1 breakfast / 2 lunches, answered):
    the model is handed the same context, and the count passes spend the
    same re-picks, whether the usual week is only derived (before) or saved
    exactly as derived (after)."""
    tools.set_household_meal_preferences(dinners_per_week=3, breakfasts_per_week=1, lunches_per_week=2,
                                         snacks_per_day=2, snacks_per_week=7)
    week = _monday()
    dates = tools._week_dates(week)
    days = _days(dates, breakfasts=["Oats", "Eggs"] * 3 + ["Oats"], lunches=["Wrap"] * 7,
                 dinners=["Chili", "Tacos", "Curry", "Stew", "Chili", "Tacos", "Curry"],
                 snacks=[["Apple", "Nuts"]] * 7)
    seen = stub_model(days)

    def run():
        agent.generate_weekly_plan(week)
        ctx = dict(seen["ctx"])
        conn = get_conn()
        for table in ("meal_plan_grocery_links", "prep_tasks", "meal_plan_entries", "weekly_plans", "recipes"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()
        ctx.pop("time_now", None)
        # How many preference writes this month (the What-we-know counter):
        # saving any answer moves it by one, and it is not a planning input.
        ctx["household_memory"] = {k: v for k, v in ctx["household_memory"].items()
                                   if k != "growth_count_this_month"}
        return ctx

    before = run()
    calls_before = len(picker)
    derived = tools.get_usual_week()
    tools.save_usual_week(grid=derived["grid"], variety={m: v["choice"] for m, v in derived["variety"].items()})
    assert tools.get_usual_week()["answered"] is True
    after = run()
    diff = {k for k in set(before["household_memory"]) | set(after["household_memory"])
            if before["household_memory"].get(k) != after["household_memory"].get(k)}
    assert diff == set(), {k: (before["household_memory"].get(k), after["household_memory"].get(k)) for k in diff}
    assert after == before
    assert len(picker) - calls_before == calls_before
    assert "usual_week_off" not in after and "variety_answered" not in after["household_memory"]


# ---------- variety is hit, whatever the number ----------

def test_something_new_every_morning_is_seven_different_breakfasts(stub_model, picker):
    tools.save_usual_week(variety={"breakfast": "new_every_day"})
    week = _monday()
    dates = tools._week_dates(week)
    seen = stub_model(_days(dates, breakfasts=["Oats", "Eggs", "Toast", "Granola", "Pancakes", "Oats", "Eggs"],
                            lunches=["Wrap"] * 7, dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    plan = agent.generate_weekly_plan(week)
    memory = seen["ctx"]["household_memory"]
    assert memory["breakfasts_per_week"] == 7
    assert "breakfast" in memory["variety_answered"]
    assert len(_distinct(plan["weekly_plan_id"], "breakfast")) == 7
    assert len([c for c in picker if c["slot"] == "breakfast"]) == 2


def test_a_go_to_or_two_is_two_breakfasts(stub_model, picker):
    tools.save_usual_week(variety={"breakfast": "go_to_or_two"})
    week = _monday()
    dates = tools._week_dates(week)
    seen = stub_model(_days(dates, breakfasts=["Oats", "Eggs", "Toast", "Granola", "Pancakes", "Waffles", "Muesli"],
                            lunches=["Wrap"] * 7, dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    plan = agent.generate_weekly_plan(week)
    assert seen["ctx"]["household_memory"]["breakfasts_per_week"] == 2
    assert len(_distinct(plan["weekly_plan_id"], "breakfast")) == 2


def test_the_prompt_no_longer_tells_the_model_breakfasts_repeat_regardless():
    import inspect
    source = inspect.getsource(agent.generate_weekly_plan_llm)
    assert "normal and expected for the same breakfast/lunch/snack idea to repeat 2-3 times" not in source
    assert "usual_week_off" in source and "variety_answered" in source


# ---------- turned-off slots ----------

def test_a_meal_turned_off_is_never_planned(stub_model, picker):
    tools.save_usual_week(
        grid={"breakfast": {d: "off" for d in WEEKDAYS[:5]}, "lunch": {"saturday": "off"}},
        variety={"breakfast": "few_in_rotation"},
    )
    week = _monday()
    dates = tools._week_dates(week)
    # The model plans every breakfast anyway; told is not prevented.
    seen = stub_model(_days(dates, breakfasts=["Oats", "Eggs"] * 3 + ["Oats"], lunches=["Wrap", "Soup"] * 3 + ["Wrap"],
                            dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    plan = agent.generate_weekly_plan(week)
    off = {(s["date"], s["slot"]) for s in seen["ctx"]["usual_week_off"]}
    assert off == {(d, "breakfast") for d in dates[:5]} | {(dates[5], "lunch")}
    # Two mornings on: "a few in rotation" (3) clamps to 2.
    assert seen["ctx"]["household_memory"]["breakfasts_per_week"] == 2
    meals = _meals(plan["weekly_plan_id"])
    for d, slot in off:
        here = [m for m in meals if m["date"] == d and m["slot"] == slot]
        assert [m["slot_state"] for m in here] == ["planned_empty"], (d, slot, here)
    menu = tools.get_week_menu(plan["weekly_plan_id"])
    assert "Not planned" in str(menu)
    assert tools.audit_plan_slots(plan["weekly_plan_id"])["complete"] is True


def test_a_meal_off_every_day_is_none_thanks():
    tools.save_usual_week(grid={"lunch": {d: "off" for d in WEEKDAYS}})
    assert tools.get_household_memory()["lunches_per_week"] == 0
    assert tools.get_usual_week()["variety"]["lunch"]["dishes"] == 0


# ---------- who's eating ----------

def test_a_thursday_dinner_for_one_is_sized_for_one(stub_model, picker):
    people = _two_people()
    tools.save_usual_week(grid={"dinner": {"thursday": [people["Emily"]]}})
    week = _monday()
    dates = tools._week_dates(week)
    thursday = dates[3]
    seen = stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7,
                            dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    agent.generate_weekly_plan(week)
    table = seen["ctx"]["attendance"]["slots_with_a_different_table"]
    thursday_dinner = [s for s in table if s["date"] == thursday and s["slot"] == "dinner"]
    assert len(thursday_dinner) == 1
    assert {k: thursday_dinner[0][k] for k in ("serves", "present", "away", "guests")} == {
        "serves": 1, "present": ["Emily"], "away": ["Vineeth"], "guests": 0,
    }
    assert len(table) == 1, table
    assert tools.get_slot_attendance(thursday, "dinner")["headcount"] == 1
    # Every other dinner is the whole table.
    assert tools.get_slot_attendance(dates[2], "dinner")["headcount"] == 2


def test_the_weeks_own_attendance_wins_over_the_usual_week(stub_model, picker):
    people = _two_people()
    week = _monday()
    dates = tools._week_dates(week)
    tools.set_member_attendance(dates[3], "dinner", "Emily", present=False)  # the day sheet: Emily's out
    tools.save_usual_week(grid={"dinner": {"thursday": [people["Emily"]]}})
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7))
    agent.generate_weekly_plan(week)
    assert tools.get_slot_attendance(dates[3], "dinner")["present_names"] == ["Vineeth"]


def test_a_subset_naming_everyone_is_everyone():
    people = _two_people()
    tools.save_usual_week(grid={"dinner": {"monday": ["Emily", "Vineeth"], "tuesday": [people["Vineeth"]]}})
    grid = tools.get_usual_week()["grid"]["dinner"]
    assert grid["monday"] == "everyone" and grid["tuesday"] == [people["Vineeth"]]


# ---------- lunch choices ----------

def test_last_nights_dinner_makes_weekday_lunches_leftovers(stub_model, picker):
    tools.save_usual_week(variety={"lunch": "last_nights_dinner"})
    week = _monday()
    dates = tools._week_dates(week)
    seen = stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap", "Soup", "Salad"] * 2 + ["Wrap"],
                            dinners=["Chili", "Tacos", "Curry", "Stew", "Pasta", "Pizza", "Fish"]))
    plan = agent.generate_weekly_plan(week)
    answer = seen["ctx"]["intake"]["weekday_lunches"]
    # Monday's lunch has no dinner the evening before in this period.
    assert [(d["date"], d["kind"]) for d in answer["days"]] == [(d, "leftovers") for d in dates[1:5]]
    links = tools.plan_leftover_chains(plan["weekly_plan_id"])["leftovers"]
    fed = {s["date"] for s in links.values() if s["slot"] == "lunch" and s["source"]["slot"] == "dinner"}
    assert set(dates[1:5]) <= fed


def test_meal_prep_ahead_needs_a_prep_day_and_preps_on_it(stub_model, picker):
    with pytest.raises(ValueError, match="prep day"):
        tools.save_usual_week(variety={"lunch": "meal_prep_ahead"}, prep={"days": []})
    tools.save_usual_week(variety={"lunch": "meal_prep_ahead"}, prep={"days": ["sunday"], "length": "longer"})
    week = tools.get_usual_week()
    assert week["prep"] == {"days": ["sunday"], "length": "longer"}
    assert tools.get_household_rhythm()["prep_days"] == [{"weekday": "sunday", "minutes": 120, "note": None}]
    assert week["variety"]["lunch"]["dishes"] == 2
    monday = _monday()
    dates = tools._week_dates(monday)
    seen = stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili", "Tacos"] * 3 + ["Curry"]))
    agent.generate_weekly_plan(monday)
    kinds = {d["date"]: d["kind"] for d in seen["ctx"]["intake"]["weekday_lunches"]["days"]}
    assert kinds == {d: "prepped" for d in dates[:5]}


def test_the_prep_answer_reads_back_as_an_hour():
    tools.save_usual_week(prep={"days": ["sunday", "wednesday"], "length": "hour"})
    assert tools.get_usual_week()["prep"] == {"days": ["sunday", "wednesday"], "length": "hour"}
    assert tools.prep_days_summary() == "Preps on Sunday and Wednesday (about an hour)."  # one length, said once (2026-10-06)
    tools.save_usual_week(prep={"days": []})
    assert tools.get_usual_week()["prep"] == {"days": [], "length": None}


# ---------- the first week is never left open ----------

def test_the_first_plan_fills_an_open_slot_before_it_returns(stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)
    # The model sends no breakfasts at all: nothing to repeat, so on its
    # own the gap audit opens all seven as "I'd rather ask than guess".
    stub_model(_days(dates, breakfasts=None, lunches=["Wrap"] * 7, dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    plan = agent.generate_weekly_plan(week)
    meals = _meals(plan["weekly_plan_id"])
    assert [m for m in meals if m["slot_state"] == "open"] == []
    assert all(any(m["date"] == d and m["slot"] == "breakfast" and m["slot_state"] == "planned" for m in meals)
               for d in dates)


def test_a_later_plan_is_not_the_first(stub_model, picker):
    week = _monday()
    dates = tools._week_dates(week)
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7))
    agent.generate_weekly_plan(week)
    assert usual_week.household_has_a_plan() is True


# ---------- the API ----------

def test_the_api_reads_and_saves_the_usual_week(signed_in):
    tools.add_member("Emily")
    res = signed_in.get("/api/usual-week")
    assert res.status_code == 200
    body = res.json()
    assert body["answered"] is False and body["weekdays"] == list(WEEKDAYS)
    assert {c["key"] for c in body["variety_choices"]["lunch"]} == {
        "last_nights_dinner", "meal_prep_ahead", "few_in_rotation", "new_every_day"}
    res = signed_in.post("/api/usual-week", json={
        "grid": {"breakfast": {"saturday": "off", "sunday": ["Emily"]}},
        "variety": {"dinner": "few_in_rotation"}, "snacks_per_day": 1,
        "prep": {"days": ["sunday"], "length": "hour"},
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answered"] is True
    assert body["grid"]["breakfast"]["saturday"] == "off"
    assert body["variety"]["dinner"] == {"choice": "few_in_rotation", "dishes": 4, "days_on": 7}
    assert body["snacks_per_day"] == 1
    assert body["prep"] == {"days": ["sunday"], "length": "hour"}
    assert signed_in.post("/api/usual-week", json={"snacks_per_day": 4}).status_code == 400
    assert signed_in.post("/api/usual-week", json={"variety": {"dinner": "whatever"}}).status_code == 400
    assert signed_in.post("/api/usual-week", json={"grid": {"dinner": {"monday": []}}}).status_code == 400


def test_onboarding_answers_still_take_the_old_counts(signed_in):
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily"], "dinners_per_week": 5, "breakfasts_per_week": 7, "lunches_per_week": 7,
        "snacks_per_day": 2,
    })
    assert res.status_code == 200, res.text
    assert res.json()["dinners_per_week"] == 5
    assert "usual_week" not in res.json()


def test_onboarding_answers_take_the_usual_week(signed_in):
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Emily", "Vineeth"], "dinners_per_week": 5,
        "usual_week": {
            "grid": {"lunch": {d: "off" for d in WEEKDAYS[:5]}, "dinner": {"thursday": ["Emily"]}},
            "variety": {"breakfast": "new_every_day", "lunch": "few_in_rotation", "dinner": "cook_big_eat_twice"},
            "snacks_per_day": 0,
            "prep": {"days": []},
        },
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["breakfasts_per_week"], body["lunches_per_week"], body["dinners_per_week"]) == (7, 2, 2)
    assert body["usual_week"]["answered"] is True
    assert body["usual_week"]["snacks_per_day"] == 0
    assert body["meal_counts_set"] is True


# ---------- one prep session, two lunch dishes ----------

def test_normalize_deals_a_sessions_lunches_between_its_dishes():
    from app.tools import weekday_lunches
    dates = tools._week_dates(_monday())
    answer = weekday_lunches.normalize(
        {"days": [{"date": d, "kind": "prepped"} for d in dates[:5]], "prep_days": ["sunday"],
         "dishes_per_prep_day": 2}, dates)
    assert [d["batch"] for d in answer["days"]] == [0, 1, 0, 1, 0]
    assert answer["dishes_per_prep_day"] == 2
    # One dish a session (the screen's own answer) stores exactly what it always did.
    plain = weekday_lunches.normalize(
        {"days": [{"date": d, "kind": "prepped"} for d in dates[:5]], "prep_days": ["sunday"]}, dates)
    assert "dishes_per_prep_day" not in plain and all("batch" not in d for d in plain["days"])


def test_meal_prep_ahead_with_one_sunday_prep_cooks_two_lunch_dishes(stub_model, picker):
    tools.save_usual_week(variety={"lunch": "meal_prep_ahead"}, prep={"days": ["sunday"], "length": "longer"})
    week = _monday()
    dates = tools._week_dates(week)
    sunday_before = (datetime.date.fromisoformat(dates[0]) - datetime.timedelta(days=1)).isoformat()
    seen = stub_model(_days(dates, breakfasts=["Oats"] * 7,
                            lunches=["Curry", "Chili", "Curry", "Chili", "Curry", "Wrap", "Wrap"],
                            dinners=["Tacos", "Stew", "Pasta", "Fish", "Pizza", "Roast", "Soup"]))
    plan = agent.generate_weekly_plan(week)
    answer = seen["ctx"]["intake"]["weekday_lunches"]
    assert [(d["date"], d["batch"]) for d in answer["days"]] == list(zip(dates[:5], [0, 1, 0, 1, 0]))
    from app.tools import weekday_lunches
    batches = weekday_lunches.prepped_batches(plan["weekly_plan_id"])
    assert len(batches) == 2
    assert {b["prep_date"] for b in batches} == {sunday_before}, "both cooked in the one Sunday session"
    assert {b["meal"].lower() for b in batches} == {"curry", "chili"}
    fed = sorted(d for b in batches for d in b["lunch_dates"])
    assert fed == sorted(dates[:5])


# ---------- the draft's count line on a part-week ----------

def test_the_count_line_scales_by_the_days_the_meal_is_on():
    from app.tools import draft_opener
    memory = {"breakfasts_per_week": 5, "lunches_per_week": 2, "dinners_per_week": 2, "meal_counts_set": True}
    # Breakfast on the five weekdays; a Tue-Fri plan has four of them.
    line = draft_opener.count_note(4, memory, slot_days={"breakfast": (4, 5), "lunch": (4, 7), "dinner": (4, 7)})
    assert line.startswith("Four breakfasts this week, not five"), line
    # Scaled by seven (the old rule), the same week said three.
    assert draft_opener.count_note(4, memory).startswith("Three breakfasts")


def test_a_part_week_draft_says_the_grids_number(stub_model, picker):
    tools.save_usual_week(grid={"breakfast": {"saturday": "off", "sunday": "off"}},
                          variety={"breakfast": "new_every_day", "lunch": "last_nights_dinner",
                                   "dinner": "cook_big_eat_twice"})
    week = _monday()
    dates = tools._week_dates(week)[1:5]  # Tue-Fri
    seen = stub_model(_days(dates, breakfasts=["Oats", "Eggs", "Toast", "Granola"], lunches=["Wrap", "Soup", "Wrap", "Soup"],
                            dinners=["Chili", "Tacos", "Curry", "Stew"]))
    plan = agent.generate_weekly_plan(week, day_count=4, period_start=dates[0])
    assert seen["ctx"]["household_memory"]["breakfasts_per_week"] == 4
    opener = tools.get_week_menu(plan["weekly_plan_id"])["draft_opener"]
    assert any(line.startswith("Four breakfasts this week, not five") for line in opener), opener


# ---------- verification round (2026-09-30) ----------

def _skip_day(plan_id, day):
    for slot in ("breakfast", "lunch", "dinner"):
        tools.plan_slot_empty(weekly_plan_id=plan_id, meal_date=day, slot=slot,
                              reason=tools.SKIPPED_DAY_REASON,
                              derived_from={"constraint": tools.SKIPPED_DAY_CONSTRAINT})


def test_build_a_plan_on_a_left_out_day_leaves_the_grids_off_meals_and_sizes_the_subset():
    people = _two_people()
    tools.save_usual_week(grid={"breakfast": {"saturday": "off"}, "dinner": {"saturday": [people["Emily"]]}})
    dates = tools._week_dates(_monday())
    saturday = dates[5]
    plan_id = tools.create_weekly_plan(dates[0])["weekly_plan_id"]
    tools.plan_meal(dates[0], "Tacos", slot="dinner", weekly_plan_id=plan_id)
    _skip_day(plan_id, saturday)
    day = next(d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == saturday)
    assert day["breakfast"]["can_fill"] is False and day["dinner"]["can_fill"] is True
    seen = []

    def pick(context):
        seen.append(context)
        return _pick(f"Filled {context['slot']}")

    out = sip.fill_empty_day(plan_id, saturday, picker=pick)
    assert out["status"] == "filled"
    assert [f["slot"] for f in out["filled"]] == ["lunch", "dinner"]
    rows = [m for m in _meals(plan_id) if m["date"] == saturday and m["slot"] == "breakfast"]
    assert [m["slot_state"] for m in rows] == ["planned_empty"]
    assert tools.get_slot_attendance(saturday, "dinner")["headcount"] == 1
    assert next(c for c in seen if c["slot"] == "dinner")  # picked for the smaller table


def test_a_count_set_elsewhere_turns_a_meal_that_was_off_all_week_back_on():
    tools.save_usual_week(grid={"breakfast": {d: "off" for d in WEEKDAYS}}, variety={"dinner": "few_in_rotation"})
    assert tools.get_household_memory()["breakfasts_per_week"] == 0
    tools.set_household_meal_preferences(breakfasts_per_week=3)
    week = tools.get_usual_week()
    assert set(week["grid"]["breakfast"].values()) == {"everyone"}
    assert week["variety"]["breakfast"] == {"choice": None, "dishes": 3, "days_on": 7}
    assert week["variety"]["dinner"]["choice"] == "few_in_rotation", "the other meals keep their answers"
    plan = usual_week.generation_plan(tools._week_dates(_monday()))
    assert plan["off_slots"] == [] and plan["targets"]["breakfast"] == 3
    assert usual_week.scale_to_period(3, 0, 0) == 0


def test_something_new_every_day_over_two_weeks_is_one_a_day():
    tools.save_usual_week(grid={"breakfast": {"saturday": "off", "sunday": "off"}},
                          variety={"breakfast": "new_every_day"})
    start = _monday()
    dates = tools.period_dates(start, 14)
    assert usual_week.generation_plan(dates)["targets"]["breakfast"] == 10
    assert usual_week.generation_plan(dates[:7])["targets"]["breakfast"] == 5


def test_taking_the_prep_days_away_moves_meal_prep_ahead_to_a_few_in_rotation():
    tools.save_usual_week(variety={"lunch": "meal_prep_ahead"}, prep={"days": ["sunday"], "length": "hour"})
    tools.set_prep_days([])  # the Settings chips / chat
    week = tools.get_usual_week()
    assert week["variety"]["lunch"] == {"choice": "few_in_rotation", "dishes": 3, "days_on": 7}
    assert tools.get_household_memory()["lunches_per_week"] == 3
    # The same through a usual-week save that only clears the prep days.
    tools.save_usual_week(variety={"lunch": "meal_prep_ahead"}, prep={"days": ["sunday"], "length": "hour"})
    week = tools.save_usual_week(prep={"days": []})
    assert week["variety"]["lunch"]["choice"] == "few_in_rotation"
    # Sending both at once is still refused.
    with pytest.raises(ValueError, match="prep day"):
        tools.save_usual_week(variety={"lunch": "meal_prep_ahead"}, prep={"days": []})


def test_onboarding_refuses_a_bad_usual_week_before_writing_anything(signed_in):
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Zed"], "dinners_per_week": 5, "usual_week": {"snacks_per_day": 9},
    })
    assert res.status_code == 400
    assert [m["name"] for m in tools.list_members()] == []
    assert tools.get_household_memory()["dinners_per_week"] == 7
    # A grid naming someone this same request adds is fine.
    res = signed_in.post("/api/onboarding/answers", json={
        "member_names": ["Zed", "Ana"], "usual_week": {"grid": {"dinner": {"friday": ["Zed"]}}},
    })
    assert res.status_code == 200, res.text
    zed = next(m["id"] for m in res.json()["usual_week"]["members"] if m["name"] == "Zed")
    assert res.json()["usual_week"]["grid"]["dinner"]["friday"] == [zed]


def test_concurrent_partial_saves_keep_each_others_cells():
    import threading
    errors = []

    def save(meal, day):
        try:
            for _ in range(5):
                tools.save_usual_week(grid={meal: {day: "off"}})
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=save, args=(m, d)) for m, d in
               (("breakfast", "monday"), ("lunch", "tuesday"), ("dinner", "wednesday"), ("breakfast", "thursday"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    grid = tools.get_usual_week()["grid"]
    assert (grid["breakfast"]["monday"], grid["lunch"]["tuesday"], grid["dinner"]["wednesday"],
            grid["breakfast"]["thursday"]) == ("off", "off", "off", "off")


def test_a_guests_row_takes_the_grids_subset_as_its_base(stub_model, picker):
    people = _two_people()
    dates = tools._week_dates(_monday())
    thursday = dates[3]
    tools.set_guest_count(thursday, "dinner", 2)
    tools.save_usual_week(grid={"dinner": {"thursday": [people["Emily"]]}})
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    agent.generate_weekly_plan(_monday())
    att = tools.get_slot_attendance(thursday, "dinner")
    assert (att["present_names"], att["guest_count"], att["headcount"]) == (["Emily"], 2, 3)


def test_a_rejected_first_week_pick_is_tried_again_and_every_lunch_is_filled(stub_model, monkeypatch):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["allergy: peanuts"], replace=True)
    dates = tools._week_dates(_monday())
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=None, dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    asked: dict[str, int] = {}
    import threading
    lock = threading.Lock()

    def quick(context):
        with lock:
            asked[context["date"]] = asked.get(context["date"], 0) + 1
            first = asked[context["date"]] == 1
            n = sum(asked.values())
        if first:
            return {"meal_name": "Peanut noodles", "ingredients": ["Peanuts", "Noodles"], "dish_note": "Satay."}
        return {"meal_name": f"Lunch bowl {n}", "ingredients": ["Rice", "Beans"], "dish_note": "Simple."}

    monkeypatch.setattr(allergen_gate, "quick_pick", quick)
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: pytest.fail("the slow write-up is not used"))
    plan = agent.generate_weekly_plan(_monday())
    meals = _meals(plan["weekly_plan_id"])
    assert [m for m in meals if m["slot_state"] == "open"] == []
    lunches = [m for m in meals if m["slot"] == "lunch" and m["slot_state"] == "planned"]
    assert sorted(m["date"] for m in lunches) == dates
    assert not any("peanut" in (m["meal"] or "").lower() for m in lunches)
    assert all(v >= 2 for v in asked.values()), "a rejection was not the end of the group"
    conn = get_conn()
    pending = conn.execute(
        "SELECT COUNT(*) FROM recipes WHERE name LIKE 'Lunch bowl%' AND details_pending = 1").fetchone()[0]
    conn.close()
    assert pending >= 1


def test_with_no_pick_left_a_first_week_gap_is_a_repeat_of_the_weeks_own_dish(stub_model, picker):
    dates = tools._week_dates(_monday())
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7))
    plan_id = agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    for d in dates[1:]:
        tools.clear_plan_slot(plan_id, d, "lunch")
        tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=d, slot="lunch", open_reason="Still deciding",
                             derived_from={"constraint": "generation_gap"})
    out = usual_week.fill_first_plan_gaps(plan_id, dates, picker=lambda ctx: {})
    assert out["left"] == []
    lunches = {m["date"]: m["meal"] for m in _meals(plan_id) if m["slot"] == "lunch" and m["slot_state"] == "planned"}
    assert set(lunches) == set(dates) and set(lunches.values()) == {"Wrap"}


def test_an_off_slot_is_never_held_or_re_picked(stub_model, picker):
    tools.add_member("Emily")
    tools.set_member_dietary_restrictions("Emily", ["allergy: peanuts"], replace=True)
    tools.save_usual_week(grid={"breakfast": {"monday": "off"}})
    dates = tools._week_dates(_monday())
    days = _days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7)
    for d in days:
        if d["date"] == dates[0] and d["slot"] == "breakfast":
            d["meal_name"] = "Peanut butter toast"
            d["ingredients"] = [{"item": "Peanut butter", "qty": "1"}]
    stub_model(days)
    plan = agent.generate_weekly_plan(_monday())
    assert not [c for c in picker if c.get("date") == dates[0] and c.get("slot") == "breakfast"]
    monday_breakfast = [m for m in _meals(plan["weekly_plan_id"]) if m["date"] == dates[0] and m["slot"] == "breakfast"]
    assert [m["slot_state"] for m in monday_breakfast] == ["planned_empty"]


def test_the_schema_declares_the_usual_week_column():
    from pathlib import Path
    schema = (Path(agent.__file__).parent / "schema.sql").read_text(encoding="utf-8")
    assert "usual_week_json TEXT NOT NULL DEFAULT ''" in schema


def test_first_week_picks_go_through_the_gates_own_pick_check(stub_model, picker, monkeypatch):
    """The same check repick_held uses (allergen_gate._pick_clashes: strict
    when the pick has a list, draft mode only when it has none)."""
    seen = []
    real = allergen_gate._pick_clashes
    monkeypatch.setattr(allergen_gate, "_pick_clashes", lambda pick, av: (seen.append(pick["meal_name"]), real(pick, av))[1])
    dates = tools._week_dates(_monday())
    stub_model(_days(dates, breakfasts=None, lunches=["Wrap"] * 7, dinners=["Chili", "Tacos"] * 3 + ["Stew"]))
    agent.generate_weekly_plan(_monday())
    assert seen, "every first-week pick is gated"


# ---------- re-verification round (2026-09-30) ----------

def test_a_person_toggled_back_on_stays_on_through_a_regeneration(stub_model, picker):
    """The repro: grid says just Emily on Thursday; they tap Vineeth back
    on for this week; the next draft of the same week keeps him."""
    people = _two_people()
    tools.save_usual_week(grid={"dinner": {"thursday": [people["Emily"]]}})
    week = _monday()
    dates = tools._week_dates(week)
    thursday = dates[3]
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    agent.generate_weekly_plan(week)
    assert tools.get_slot_attendance(thursday, "dinner")["present_names"] == ["Emily"]
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=True)
    assert tools.get_slot_attendance(thursday, "dinner")["present_names"] == ["Emily", "Vineeth"]
    agent.generate_weekly_plan(week)
    att = tools.get_slot_attendance(thursday, "dinner")
    assert att["present_names"] == ["Emily", "Vineeth"] and att["headcount"] == 2


def test_guests_set_before_the_first_draft_sit_on_the_grids_table_once(stub_model, picker):
    people = _two_people()
    week = _monday()
    dates = tools._week_dates(week)
    thursday = dates[3]
    tools.set_guest_count(thursday, "dinner", 2)          # before any draft
    tools.save_usual_week(grid={"dinner": {"thursday": [people["Emily"]]}})
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili", "Tacos", "Curry", "Stew"] * 2))
    agent.generate_weekly_plan(week)
    att = tools.get_slot_attendance(thursday, "dinner")
    assert (att["present_names"], att["guest_count"], att["source"]) == (["Emily"], 2, "guests")
    # Then they add Vineeth for this week; a regeneration keeps their answer.
    tools.set_member_attendance(thursday, "dinner", "Vineeth", present=True)
    agent.generate_weekly_plan(week)
    att = tools.get_slot_attendance(thursday, "dinner")
    assert (att["present_names"], att["guest_count"]) == (["Emily", "Vineeth"], 2)


def test_a_first_week_dinner_on_several_nights_is_cooked_once_and_eaten_again(stub_model, picker):
    dates = tools._week_dates(_monday())
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7))
    plan_id = agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    # Tuesday and Wednesday dinners open, as one group (dinners number 1).
    tools.set_household_meal_preferences(dinners_per_week=1)
    for d in dates[1:3]:
        tools.clear_plan_slot(plan_id, d, "dinner")
        tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=d, slot="dinner", open_reason="Still deciding",
                             derived_from={"constraint": "generation_gap"})
    out = usual_week.fill_first_plan_gaps(plan_id, dates[1:3])
    assert out["left"] == [] and len(out["filled"]) == 2
    chains = tools.plan_leftover_chains(plan_id)
    wednesday = [e for e in chains["leftovers"].values() if e["date"] == dates[2] and e["slot"] == "dinner"]
    assert wednesday and wednesday[0]["source"]["date"] == dates[1]


def test_no_test_can_reach_the_anthropic_api():
    from app import agent as _agent
    import anthropic
    with pytest.raises(anthropic.AuthenticationError, match="never reach"):
        _agent._client().messages.create(model="x", max_tokens=1, messages=[])


def test_a_first_week_dinner_pick_over_the_weeknight_limit_is_not_used(stub_model):
    """Card "The draft breaks the household's own rules" (2026-10-06): a NEW
    household's first week, weeknight limit 45. The first-week fill runs
    after cap_enforce, so a 55-minute quick pick on a Tuesday used to land
    untouched; it now goes to the repeat pass, which holds to the cap."""
    tools.edit_preference("weeknight_max_minutes", 45)
    dates = tools._week_dates(_monday())
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7))
    plan_id = agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    tuesday = dates[1]
    tools.clear_plan_slot(plan_id, tuesday, "dinner")
    tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=tuesday, slot="dinner", open_reason="Still deciding",
                         derived_from={"constraint": "generation_gap"})
    slow = {"meal_name": "Slow tilapia rice bake", "ingredients": ["Tilapia", "Rice"], "dish_note": "Baked.",
            "prep_time_minutes": 20, "cook_time_minutes": 35}
    usual_week.fill_first_plan_gaps(plan_id, dates, picker=lambda ctx: dict(slow))
    tue = [m for m in _meals(plan_id) if m["date"] == tuesday and m["slot"] == "dinner"]
    assert "Slow tilapia rice bake" not in [m["meal"] for m in tue]


def test_a_first_week_pick_inside_the_limit_is_still_used(stub_model):
    tools.edit_preference("weeknight_max_minutes", 45)
    dates = tools._week_dates(_monday())
    stub_model(_days(dates, breakfasts=["Oats"] * 7, lunches=["Wrap"] * 7, dinners=["Chili"] * 7))
    plan_id = agent.generate_weekly_plan(_monday())["weekly_plan_id"]
    tuesday = dates[1]
    tools.clear_plan_slot(plan_id, tuesday, "dinner")
    tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=tuesday, slot="dinner", open_reason="Still deciding",
                         derived_from={"constraint": "generation_gap"})
    quick = {"meal_name": "Quick tilapia tacos", "ingredients": ["Tilapia", "Tortillas"], "dish_note": "Fast.",
             "prep_time_minutes": 10, "cook_time_minutes": 15}
    usual_week.fill_first_plan_gaps(plan_id, dates, picker=lambda ctx: dict(quick))
    tue = [m for m in _meals(plan_id) if m["date"] == tuesday and m["slot"] == "dinner"]
    assert "Quick tilapia tacos" in [m["meal"] for m in tue]
