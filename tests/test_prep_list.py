"""
The prep list: a prep day built from STEPS that fit the household's time,
not from whole dishes that happened to be dated on it (Emily, 2026-11-01,
branch prep-list-redesign; app/tools/prep_list.py).

Pinned here: the budget fill, hands-on-only counting, marinating always on
the prep day (and frozen in the bag with a thaw booked when the meal is
more than two days out), better-fresh food never prepped, take off / add
back surviving a reload, the section order, and the screen's own words.
"""
import datetime
import pathlib
import re

import pytest

from conftest import household_today

from app import db, tools
from app.tools import cooker, prep_list, prep_sessions

SHELL_JS = (pathlib.Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")


WORDS = ["lemon", "herb", "tahini", "miso", "ranch", "caesar"]


def _d(n: int) -> str:
    return (household_today() + datetime.timedelta(days=n)).isoformat()


def _recipe(name, ingredients, instructions, prep=15, cook=20):
    tools.add_recipe(name, ingredients=ingredients, instructions=instructions, default_servings=2,
                     prep_time_minutes=prep, cook_time_minutes=cook)


def _stir_fry():
    _recipe("Veg Stir Fry", [
        {"item": "Yellow onion", "qty": "1", "category": "produce"},
        {"item": "Mushrooms", "qty": "8 oz", "category": "produce"},
        {"item": "Jasmine rice", "qty": "1 cup", "category": "pantry"},
        {"item": "Soy sauce", "qty": "3 tbsp", "category": "pantry"},
    ], [
        "Cook the rice.",
        "Whisk the soy sauce, honey and ginger into the stir-fry sauce.",
        "Chop the onion and mushrooms and stir-fry them.",
    ])


def _soup():
    _recipe("Onion Soup", [
        {"item": "Yellow onion", "qty": "2", "category": "produce"},
        {"item": "Carrots", "qty": "2", "category": "produce"},
    ], ["Slice the onions and carrots.", "Simmer for 30 minutes."])


def _skewers():
    _recipe("Chicken Skewers", [
        {"item": "Boneless skinless chicken thighs", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Lemon", "qty": "1", "category": "produce"},
    ], ["Marinate the chicken in the lemon and oil.", "Grill the skewers."])


def _shrimp():
    _recipe("Garlic Shrimp", [
        {"item": "Large shrimp", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Garlic", "qty": "4 cloves", "category": "produce"},
        {"item": "Cucumber", "qty": "1", "category": "produce"},
    ], ["Mince the garlic.", "Sear the shrimp in the pan."])


def _week(minutes=120, start_offset=0):
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    today = household_today()
    tools.set_prep_days([{"weekday": today.strftime("%A").lower(), "minutes": minutes}])
    plan_id = tools.create_weekly_plan(
        today.isoformat(), content_start_date=today.isoformat(), day_count=7
    )["weekly_plan_id"]
    return plan_id


def _plan(plan_id, day, dish, slot="dinner"):
    return tools.plan_meal(_d(day), dish, slot=slot, weekly_plan_id=plan_id)["entry_id"]


def _session(plan_id):
    [session] = prep_sessions.prep_sessions_for_plan(plan_id)
    return session


def _on(session):
    return [i for i in session["items"] if i["on_list"]]


def _by_title(session, needle):
    return [i for i in session["items"] if needle.lower() in i["title"].lower()]


# ---------- the words ----------

def test_the_header_says_the_budget_and_the_hands_on_time():
    assert prep_list.header_line(120, 85) == "Up to 2 hr · about 1 hr 25 hands-on"
    assert prep_list.header_line(60, 40) == "Up to 1 hr · about 40 min hands-on"
    assert prep_list.header_line(60, 70) == "Up to 1 hr · about 1 hr 10 hands-on · 10 min over"


def test_made_on_the_night_line_follows_the_budget():
    assert prep_list.made_on_the_night_line(120) == (
        "These didn’t fit in your 2 hours, so they’re cooked fresh on the day. Tap Add it to prep one now.")
    assert "your 1 hour," in prep_list.made_on_the_night_line(60)


def test_section_line_and_feeds_line():
    assert prep_list.section_line(2, 25) == "2 things · 25 min hands-on"
    assert prep_list.section_line(1, 5) == "1 thing · 5 min hands-on"
    mon = datetime.date(2026, 11, 2).isoformat()
    tue = datetime.date(2026, 11, 3).isoformat()
    wed = datetime.date(2026, 11, 4).isoformat()
    assert prep_list.feeds_line([{"date": tue, "slot": "lunch"}, {"date": wed, "slot": "dinner"}]) == "Tue lunch, Wed dinner"
    assert prep_list.feeds_line([{"date": mon, "slot": "lunch"}, {"date": tue, "slot": "lunch"}]) == "Mon, Tue lunches"


# ---------- steps, not dishes ----------

def test_a_meal_is_read_into_steps_with_shared_amounts_summed():
    plan_id = _week()
    _stir_fry()
    _soup()
    _plan(plan_id, 1, "Veg Stir Fry")
    _plan(plan_id, 2, "Onion Soup")

    session = _session(plan_id)
    titles = [i["title"] for i in _on(session)]
    # The onion both dinners chop is one step, summed and saying what it feeds.
    onion = next(i for i in _on(session) if "onion" in i["title"].lower() and i["shared"])
    assert onion["title"].startswith("3 ")
    assert onion["line"].startswith(prep_list.feeds_line([{"date": _d(1), "slot": "dinner"}, {"date": _d(2), "slot": "dinner"}]))
    assert any(t.startswith("Jasmine rice, 1 cup dry") for t in titles), titles
    assert any("Stir-fry sauce, 1 jar" == t for t in titles), titles
    # The bottle of soy sauce is an ingredient, not a sauce to make.
    assert not any(t.lower().startswith("soy sauce") for t in titles)


def test_only_hands_on_minutes_count_against_the_time():
    plan_id = _week(minutes=60)
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")

    session = _session(plan_id)
    rice = next(i for i in session["items"] if i["title"].startswith("Jasmine rice"))
    assert rice["passive"] == 20 and rice["hands_on"] == 5
    assert session["hands_on_minutes"] == sum(i["hands_on"] for i in _on(session))
    assert session["total_minutes_estimate"] == session["hands_on_minutes"]
    assert session["hands_on_minutes"] < sum(i["hands_on"] + i["passive"] for i in _on(session))


def test_steps_fill_up_to_the_budget_and_the_rest_is_made_on_the_night():
    plan_id = _week(minutes=30)
    for n in range(5):
        _recipe(f"Dish {n}", [
            {"item": "Carrots", "qty": "2", "category": "produce"},
            {"item": "Celery", "qty": "2 stalks", "category": "produce"},
            {"item": "Bell pepper", "qty": "1", "category": "produce"},
            {"item": "Zucchini", "qty": "1", "category": "produce"},
        ], ["Chop everything.", f"Whisk the {WORDS[n]} dressing together."], prep=20, cook=10)
    for n in range(5):
        _plan(plan_id, 1 + (n % 4), f"Dish {n}", slot="dinner" if n < 4 else "lunch")
    session = _session(plan_id)
    assert session["hands_on_minutes"] <= 30
    late = [i for i in session["items"] if i["state"] == "didnt_fit"]
    assert late, "something should not have fit in half an hour"
    for item in late:
        assert item["off_line"].startswith(prep_list.duration_words(item["hands_on"] + item["passive"]) + ". Make it ")
        # The real shortfall, rounded up to 5 minutes (review, 2026-11-01).
        assert re.search(r", or add \d+ min$", item["off_line"]), item["off_line"]
    assert session["made_fresh"][0] == late[0]["key"] or late[0]["key"] in session["made_fresh"]

    tools.set_prep_days([{"weekday": household_today().strftime("%A").lower(), "minutes": 240}])
    assert not [i for i in _session(plan_id)["items"] if i["state"] == "didnt_fit"]


def test_a_step_two_meals_share_ranks_first():
    plan_id = _week(minutes=60)
    _soup()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")
    _plan(plan_id, 2, "Onion Soup")
    session = _session(plan_id)
    items = {i["key"]: i for i in session["items"]}
    shared = [i for i in items.values() if i["shared"]]
    assert shared and all(i["on_list"] for i in shared)

    # A budget so small only one derived step fits: the shared one wins
    # over the rice (higher saving per minute, but one meal).
    shared_hands = shared[0]["hands_on"]
    assert prep_list.fit([dict(i, state="on_list", on_list=True, done=False, added_back=False)
                          for i in items.values() if i["kind"] == "step"], shared_hands, {}) == shared_hands


# ---------- marinating ----------

def test_a_marinade_is_always_on_the_list_and_cannot_come_off():
    plan_id = _week(minutes=60)
    _skewers()
    _plan(plan_id, 1, "Chicken Skewers")
    # Fill the hour with something pinned the household chose.
    tools.save_prep_tasks(plan_id, [
        {"task_date": _d(0), "description": "Bake the granola", "related_meal": "Breakfast"},
    ])
    session = _session(plan_id)
    mar = next(i for i in session["items"] if i["kind"] == "marinate")
    assert mar["title"] == "Marinate the chicken thighs, 1 lb"
    assert mar["on_list"] and mar["always"] and not mar["can_remove"]
    assert mar["section"] == "sauces"
    assert "Keeps in the fridge until" in mar["line"]
    with pytest.raises(ValueError):
        tools.set_prep_list_mark(plan_id, session["date"], mar["key"], "off")
    # No freezer step and no thaw for a meal one day out.
    assert not [i for i in session["items"] if i["kind"] == "freeze"]
    assert not [t for t in tools.get_prep_schedule(plan_id) if "marinated" in t["description"]]


def test_a_marinade_more_than_two_days_out_is_frozen_and_its_thaw_booked():
    plan_id = _week()
    _skewers()
    entry = _plan(plan_id, 4, "Chicken Skewers")
    session = _session(plan_id)
    mar = next(i for i in session["items"] if i["kind"] == "marinate")
    night_before = (datetime.date.fromisoformat(_d(4)) - datetime.timedelta(days=1)).strftime("%A")
    assert f"Freeze it in the bag. It thaws in the fridge {night_before} night." in mar["line"]
    freeze = next(i for i in session["items"] if i["kind"] == "freeze")
    assert freeze["section"] == "freezer" and not freeze["can_remove"] and freeze["on_list"]

    thaws = [t for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "defrost"]
    assert len(thaws) == 1
    assert thaws[0]["task_date"] == _d(3)
    assert thaws[0]["meal_plan_entry_id"] == entry
    assert thaws[0]["description"].startswith("Move the marinated chicken thighs to the fridge")

    # Reading again books nothing twice, and a defrost resync leaves it be.
    _session(plan_id)
    tools.sync_defrost_tasks(plan_id)
    assert len([t for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "defrost"]) == 1


def test_a_thaw_on_the_prep_day_is_listed_where_defrost_dated_it_and_never_comes_off():
    plan_id = _week()
    _skewers()
    entry = _plan(plan_id, 2, "Chicken Skewers")
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id, quantity) VALUES (1, ?, ?, 'Move the pork chops to the fridge', "
        "'Chicken Skewers', 'pending', 'defrost', ?, '')",
        (plan_id, _d(0), entry),
    )
    conn.commit()
    conn.close()
    session = _session(plan_id)
    thaw = next(i for i in session["items"] if i["section"] == "thaw")
    assert thaw["on_list"] and not thaw["can_remove"]
    assert session["sections"][0]["key"] == "thaw"
    assert [t["task_date"] for t in tools.get_prep_schedule(plan_id) if t["description"].startswith("Move the pork")] == [_d(0)]


# ---------- better fresh ----------

def test_seafood_and_cut_cucumber_are_never_prepped():
    plan_id = _week()
    _shrimp()
    _plan(plan_id, 2, "Garlic Shrimp")
    session = _session(plan_id)
    on_titles = " ".join(i["title"].lower() for i in _on(session))
    assert "shrimp" not in on_titles and "cucumber" not in on_titles
    shrimp = next(i for i in session["items"] if i["state"] == "better_fresh" and "shrimp" in i["title"].lower())
    assert shrimp["off_line"] == "Better fresh. Shrimp goes rubbery after a day in the fridge."
    assert not shrimp["on_list"] and not shrimp["can_remove"]
    with pytest.raises(ValueError):
        tools.set_prep_list_mark(plan_id, session["date"], shrimp["key"], "add")
    # The garlic for the same dish is still prepped.
    assert "garlic" in on_titles


def test_a_prepped_lunch_of_shrimp_is_cooked_on_its_day_not_the_prep_day():
    """The reported case: a prepped lunch put shrimp on Sunday for Tuesday.
    The batch is listed as better fresh, and its card stops saying it was
    made on the prep day."""
    plan_id = _week()
    _shrimp()
    entry = _plan(plan_id, 2, "Garlic Shrimp", slot="lunch")
    conn = db.get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        ('{"prep_date": "%s", "prep_day": "%s"}' % (_d(0), household_today().strftime("%A").lower()), entry),
    )
    conn.commit()
    conn.close()
    session = _session(plan_id)
    batch = next(i for i in session["items"] if i["key"] == f"cook:{entry}")
    assert batch["state"] == "better_fresh" and not batch["on_list"]
    assert batch["off_line"].startswith("Better fresh. Shrimp")
    # Its garlic can still be prepped.
    assert any("garlic" in i["title"].lower() for i in _on(session))
    card = next(m for m in cooker.get_cooker_view(plan_id)["meals"] if m.get("entry_id") == entry)
    assert not card.get("prepped_ahead")


# ---------- take off / add back ----------

def test_take_off_and_add_back_survive_a_reload():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 3, "Veg Stir Fry")
    session = _session(plan_id)
    rice = next(i for i in session["items"] if i["title"].startswith("Jasmine rice"))
    assert rice["on_list"] and rice["can_remove"]

    tools.set_prep_list_mark(plan_id, session["date"], rice["key"], "off")
    again = _session(plan_id)
    rice = next(i for i in again["items"] if i["key"] == rice["key"])
    day = datetime.date.fromisoformat(_d(3)).strftime("%A")
    assert (rice["state"], rice["on_list"], rice["off_line"]) == ("taken_off", False, f"Cooked fresh on {day}")
    assert rice["key"] in again["made_fresh"]

    tools.set_prep_list_mark(plan_id, session["date"], rice["key"], "add")
    rice = next(i for i in _session(plan_id)["items"] if i["key"] == rice["key"])
    assert rice["on_list"] and rice["added_back"]


def test_an_add_back_may_go_over_the_time_and_the_header_says_so():
    plan_id = _week(minutes=30)
    for n in range(6):
        _recipe(f"Bowl {n}", [
            {"item": "Carrots", "qty": "2", "category": "produce"},
            {"item": "Kale", "qty": "1 bunch", "category": "produce"},
            {"item": "Broccoli", "qty": "1 head", "category": "produce"},
            {"item": "Red cabbage", "qty": "1/2 head", "category": "produce"},
        ], ["Chop it all.", f"Whisk the {WORDS[n]} dressing together."])
        _plan(plan_id, 1 + n % 3, f"Bowl {n}", slot=("dinner", "lunch")[n // 3])
    session = _session(plan_id)
    late = [i for i in session["items"] if i["state"] == "didnt_fit"]
    assert late
    for item in late:
        tools.set_prep_list_mark(plan_id, session["date"], item["key"], "add")
    after = _session(plan_id)
    assert after["hands_on_minutes"] > 30
    assert after["over_budget_minutes"] == after["hands_on_minutes"] - 30
    assert after["header"].endswith(" over")


def test_a_snack_taken_off_is_cooked_fresh_each_night():
    item = prep_list._base_item(key="x", first_date=_d(2), first_slot="snack")
    assert prep_list._fresh_line(item) == "Cooked fresh each night"


def test_the_route_takes_a_step_off_and_answers_with_the_cook_view(signed_in):
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 2, "Veg Stir Fry")
    session = _session(plan_id)
    rice = next(i for i in session["items"] if i["title"].startswith("Jasmine rice"))
    res = signed_in.post("/api/cooker/prep-list", json={
        "weekly_plan_id": plan_id, "prep_date": session["date"], "key": rice["key"], "action": "off"})
    assert res.status_code == 200, res.text
    body = res.json()
    s = next(s for s in body["prep_sessions"] if s["date"] == session["date"])
    assert rice["key"] in s["made_fresh"]
    bad = signed_in.post("/api/cooker/prep-list", json={
        "weekly_plan_id": plan_id, "prep_date": session["date"], "key": "nope", "action": "off"})
    assert bad.status_code == 404
    junk = signed_in.post("/api/cooker/prep-list", json={
        "weekly_plan_id": plan_id, "prep_date": session["date"], "key": rice["key"], "action": "explode"})
    assert junk.status_code == 422


def test_a_derived_step_ticks_on_the_prep_list_itself():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 2, "Veg Stir Fry")
    session = _session(plan_id)
    rice = next(i for i in session["items"] if i["title"].startswith("Jasmine rice"))
    tools.set_prep_list_mark(plan_id, session["date"], rice["key"], "done")
    again = _session(plan_id)
    assert next(i for i in again["items"] if i["key"] == rice["key"])["done"] is True
    assert again["items_done"] == 1


# ---------- sections ----------

def test_sections_come_in_the_set_order_and_only_when_they_hold_something():
    assert prep_list.SECTION_ORDER == [
        "thaw", "soak", "oven", "stove", "slow_cooker", "chop", "sauces", "chill",
        "freezer", "special", "pack",
    ]
    assert [t for _k, t in prep_list.SECTIONS] == [
        "Move to thaw", "Set to soak", "In the oven", "On the stove", "In the slow cooker",
        "Chop and wash", "Sauces, marinades, drinks", "Mix and chill", "Move to freezer",
        "Special occasion prep", "Pack it up",
    ]
    plan_id = _week()
    _stir_fry()
    _skewers()
    _plan(plan_id, 1, "Veg Stir Fry")
    _plan(plan_id, 4, "Chicken Skewers")
    keys = [s["key"] for s in _session(plan_id)["sections"]]
    assert keys == [k for k in prep_list.SECTION_ORDER if k in keys]
    assert keys == ["stove", "chop", "sauces", "freezer"]


# ---------- the screen ----------

def test_the_screen_folds_sections_and_carries_the_made_on_the_night_card():
    body = SHELL_JS[SHELL_JS.index("function cookSessionHtml(data, session, meals)"):]
    body = body[:body.index('// ---------- "Prep-cut on Sunday?" ----------')]
    assert 'data-cook="prep-section"' in body and "aria-expanded" in body
    assert "'Take off the list: '" in body
    assert 'data-cook="prep-add"' in body and ">Add it</button>" in body
    assert "Made on the night" in body
    assert "session.made_fresh_line" in body
    assert "if (what === 'prep-off') return cookPrepListMark(el, 'off');" in SHELL_JS
