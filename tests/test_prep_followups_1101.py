"""
Prep list follow-ups (Emily, 2026-11-01; branch prep-followups-1101).

A — a dish made whole on a prep day (the prep list's whole-dish step, or a
    prepped-lunch batch) is not a second cook on its own night: once made
    (its step ticked, or the prep day gone by) the night is "Reheat" or
    "Plate"; before that it says it is made at the prep.
B — "Prep list: clear a marinade thaw when its meal moves before the prep
    day": the thaw goes when the marinade never happened, is never left
    dated in the past, and a pinned marinade on still-frozen meat is
    matched by meal, not by words.
"""
import datetime

from conftest import household_today

from app import db, tools
from app.tools import cooker, prep_list

from test_prep_list import _d, _plan, _recipe, _session, _skewers, _week
from test_prep_list_food_safety import _defrost_row, _move, _thaws, _with_stir_fry


def _stew():
    _recipe("Beef Stew", [
        {"item": "Beef chuck", "qty": "2 lb", "category": "meat/seafood"},
        {"item": "Beef broth", "qty": "4 cups", "category": "pantry"},
    ], ["Brown the beef in a pot.", "Simmer for two hours."], prep=20, cook=120)


def _noodle_salad():
    _recipe("Sesame Noodle Salad", [
        {"item": "Soba noodles", "qty": "8 oz", "category": "pantry"},
        {"item": "Sesame oil", "qty": "2 tbsp", "category": "pantry"},
    ], ["Boil the noodles in a pot.", "Toss with the oil and chill."], prep=10, cook=10)


def _card(plan_id, entry):
    return next(m for m in cooker.get_cooker_view(plan_id)["meals"] if m.get("entry_id") == entry)


def _dish_step(plan_id, entry):
    session = _session(plan_id)
    return session, next(i for i in session["items"] if i["key"] == f"dish:{entry}")


def _week_from_yesterday():
    """A plan whose prep day was yesterday — the prep day has gone by."""
    for n in ("Alex", "Sam"):
        tools.add_member(n)
    start = household_today() - datetime.timedelta(days=1)
    tools.set_prep_days([{"weekday": start.strftime("%A").lower(), "minutes": 120}])
    return tools.create_weekly_plan(start.isoformat(), content_start_date=start.isoformat(), day_count=7)[
        "weekly_plan_id"]


# ---------- A ----------

def test_before_it_is_made_the_night_says_it_is_made_at_the_prep():
    plan_id = _with_stir_fry()
    _stew()
    entry = _plan(plan_id, 2, "Beef Stew")
    session, step = _dish_step(plan_id, entry)
    assert step["on_list"]
    card = _card(plan_id, entry)
    assert not card.get("is_leftovers")
    assert card["prepped_ahead"]["date"] == session["date"]


def test_once_ticked_a_hot_dish_is_a_reheat_not_a_second_cook():
    plan_id = _with_stir_fry()
    _stew()
    entry = _plan(plan_id, 2, "Beef Stew")
    session, step = _dish_step(plan_id, entry)
    tools.set_prep_list_mark(plan_id, session["date"], step["key"], "done")
    card = _card(plan_id, entry)
    weekday = datetime.date.fromisoformat(session["date"]).strftime("%A")
    assert card["is_leftovers"] and card["finish_action"] == "reheat"
    assert card["leftovers_headline"] == f"Made ahead — {weekday}’s Beef Stew"
    assert card["made_at_prep"] == {"date": session["date"], "weekday": weekday}
    assert card["ingredients"] == [] and card["instructions"] == [] and not card["has_full_recipe"]
    assert not card.get("served_cold")


def test_once_ticked_a_cold_dish_is_plated():
    plan_id = _with_stir_fry()
    _noodle_salad()
    entry = _plan(plan_id, 2, "Sesame Noodle Salad")
    session, step = _dish_step(plan_id, entry)
    tools.set_prep_list_mark(plan_id, session["date"], step["key"], "done")
    card = _card(plan_id, entry)
    assert card["finish_action"] == "plate" and card["served_cold"]
    assert card["serve_step"] == cooker.PLATE_SERVE_STEP


def test_after_the_prep_day_the_dish_counts_as_made_without_a_tick():
    plan_id = _week_from_yesterday()
    _stir_fry_recipe_and_plan(plan_id)
    _stew()
    entry = tools.plan_meal(_d(1), "Beef Stew", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    card = _card(plan_id, entry)
    assert card["is_leftovers"] and card["finish_action"] == "reheat"
    assert card["made_at_prep"]["date"] == _d(-1)


def test_a_prepped_lunch_made_at_a_past_prep_day_is_a_reheat_on_its_day():
    plan_id = _week_from_yesterday()
    _stew()
    entry = tools.plan_meal(_d(1), "Beef Stew", slot="lunch", weekly_plan_id=plan_id)["entry_id"]
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 ('{"prep_date": "%s", "prep_day": "x"}' % _d(-1), entry))
    conn.commit()
    conn.close()
    card = _card(plan_id, entry)
    assert card["is_leftovers"] and card["finish_action"] == "reheat"
    assert not card.get("prepped_ahead")


def test_ticking_a_prepped_batch_on_prep_day_means_made_not_eaten():
    """The prep-day tick is MADE, stored on the prep list — the night's own
    reheat stays to do until it is marked eaten that night."""
    plan_id = _with_stir_fry()
    _stew()
    entry = _plan(plan_id, 2, "Beef Stew", slot="lunch")
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 ('{"prep_date": "%s", "prep_day": "x"}' % _d(0), entry))
    conn.commit()
    conn.close()
    session = _session(plan_id)
    item = next(i for i in session["items"] if i["key"] == f"cook:{entry}")
    assert item["ticks_on_list"] and not item["done"]
    assert not _card(plan_id, entry).get("is_leftovers")

    tools.set_prep_list_mark(plan_id, session["date"], item["key"], "done")
    assert next(i for i in _session(plan_id)["items"] if i["key"] == item["key"])["done"] is True
    card = _card(plan_id, entry)
    assert card["is_leftovers"] and card["finish_action"] == "reheat"
    assert card["cooked_status"] == "pending", "made is not eaten"

    tools.check_off_meal(entry, "done")
    assert _card(plan_id, entry)["cooked_status"] == "done"


def test_the_prep_list_box_for_a_batch_made_ahead_ticks_the_list_not_the_cook():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text()
    body = src[src.index("function cookSessionItemHtml("):]
    body = body[:body.index("\n  function ", 1)]
    assert "item.ticks_on_list" in body and "data-cook=\"prep-tick\"" in body


def test_a_reheat_badge_says_plate_for_a_plated_dish():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text()
    assert "meal.finish_action === 'plate' ? 'Plate'" in src
    assert "' — made at ' + meal.made_at_prep.weekday + '’s prep.'" in src


def _stir_fry_recipe_and_plan(plan_id):
    from test_prep_list import _stir_fry
    _stir_fry()
    tools.plan_meal(_d(0), "Veg Stir Fry", slot="dinner", weekly_plan_id=plan_id)


# ---------- B ----------

def test_a_meal_moved_to_its_prep_day_before_the_marinade_loses_its_thaw():
    plan_id = _with_stir_fry()
    _skewers()
    entry = _plan(plan_id, 4, "Chicken Skewers")
    _session(plan_id)
    assert len(_thaws(plan_id)) == 1
    # To the day BEFORE the prep day — outside every reach this read covers.
    _move(entry, -1)
    _session(plan_id)
    assert _thaws(plan_id) == []


def test_a_done_marinade_whose_night_before_has_passed_is_due_today():
    plan_id = _with_stir_fry()
    _skewers()
    entry = _plan(plan_id, 4, "Chicken Skewers")
    session = _session(plan_id)
    mar = next(i for i in session["items"] if i["kind"] == "marinate")
    tools.set_prep_list_mark(plan_id, session["date"], mar["key"], "done")
    _move(entry, 0)
    _session(plan_id)
    [thaw] = _thaws(plan_id)
    assert thaw["task_date"] == _d(0) and thaw["status"] == "pending"


def test_a_pinned_marinade_on_still_frozen_meat_is_matched_by_meal_not_words():
    plan_id = _with_stir_fry()
    _recipe("Steak Tacos", [{"item": "Flank steak", "qty": "1 lb", "category": "meat/seafood"}],
            ["Slice and sear the steak."])
    entry = _plan(plan_id, 5, "Steak Tacos")
    _defrost_row(plan_id, entry, 3, item="beef", meal="Steak Tacos")
    tools.save_prep_tasks(plan_id, [
        {"task_date": _d(0), "description": "Marinate the flank steak", "related_meal": "Steak Tacos"},
    ])
    session = _session(plan_id)
    task = next(i for i in session["items"] if i["title"] == "Marinate the flank steak")
    assert "Put the frozen flank steak in the marinade bag" in task["line"]
    assert _thaws(plan_id) == []
    assert prep_list.MARINADE_THAW_KIND  # the kind the check above counts
