"""
Food-safety fixes to the prep list after independent review (2026-11-01,
branch prep-list-redesign). One test (or a pair) per finding:

  1. better fresh wins over a pinned cook-ahead chain;
  2. the whole-dish fallback never makes a seafood dish or a raw salad ahead;
  3. meat still frozen on the prep day is still marinated that day, frozen,
     and the raw dish is never turned into a prep-day cook;
  4. a pinned "Marinate the flank steak" past two days is frozen in the bag
     with its thaw booked;
  5. marinade thaws are reconciled (booked, moved, removed — own rows only);
  6. seafood-named condiments are not seafood.
Plus the nits: summed dressings, "or add N min", "1 hr 30".
"""
import datetime

from app import db, tools
from app.tools import prep_list

from test_prep_list import _d, _on, _plan, _recipe, _session, _skewers, _stir_fry, _week


def _thaws(plan_id, kind_only=True):
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, task_date, description, meal_plan_entry_id, status, "
        "COALESCE(json_extract(detail_json, '$.kind'), '') AS kind FROM prep_tasks "
        "WHERE task_type = 'defrost' ORDER BY id"
    ).fetchall()
    conn.close()
    rows = [dict(r) for r in rows]
    return [r for r in rows if r["kind"] == prep_list.MARINADE_THAW_KIND] if kind_only else rows


def _defrost_row(plan_id, entry, day, item="chicken thighs", meal="Chicken Skewers"):
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id, quantity) VALUES (1, ?, ?, ?, ?, 'pending', 'defrost', ?, '')",
        (plan_id, _d(day), f"Move the {item} to the fridge — for the {meal}.", meal, entry),
    )
    conn.commit()
    conn.close()


# 1 -----------------------------------------------------------------------

def test_a_seafood_cook_ahead_chain_is_made_on_the_night_not_pinned():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _recipe("Salmon Bake", [
        {"item": "Salmon fillets", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Lemon", "qty": "1", "category": "produce"},
    ], ["Heat the oven to 400F.", "Bake the salmon for 15 minutes."])
    ids = [_plan(plan_id, d, "Salmon Bake") for d in (0, 2, 3)]
    tools.set_cook_ahead(ids[0], ids[1:])

    session = _session(plan_id)
    chain = next(i for i in session["items"] if i["key"] == f"cook:{ids[0]}")
    assert chain["state"] == "better_fresh" and not chain["on_list"]
    assert chain["off_line"] == "Better fresh. Fish is best cooked the day you eat it."
    assert not [i for i in _on(session) if "salmon" in i["title"].lower()]


# 2 -----------------------------------------------------------------------

def test_a_raw_tomato_cucumber_salad_is_never_a_whole_dish_made_ahead():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _recipe("Tomato Cucumber Salad", [
        {"item": "Tomatoes", "qty": "3", "category": "produce"},
        {"item": "Cucumber", "qty": "1", "category": "produce"},
        {"item": "Olive oil", "qty": "2 tbsp", "category": "pantry"},
    ], ["Cut the tomatoes and cucumber.", "Toss with the oil and salt."], prep=10, cook=None)
    _plan(plan_id, 1, "Tomato Cucumber Salad")
    items = prep_list_items = [i for s in tools.prep_sessions_for_plan(plan_id) for i in s["items"]]
    assert not [i for i in items if i["kind"] == "whole_dish"], prep_list_items
    assert [i for i in items if i["state"] == "better_fresh"]


def test_shrimp_with_no_category_still_keeps_the_dish_off_the_stove():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _recipe("Garlic Noodles", [
        {"item": "Shrimp", "qty": "1 lb"},
        {"item": "Egg noodles", "qty": "8 oz", "category": "pantry"},
        {"item": "Butter", "qty": "4 tbsp", "category": "dairy"},
    ], ["Boil the noodles.", "Cook the shrimp in the butter in a pan."])
    _plan(plan_id, 1, "Garlic Noodles")
    assert prep_list.dish_fresh_reason("Garlic Noodles", {"ingredients": [{"item": "Shrimp"}]}).startswith(
        "Better fresh. Shrimp")
    session = _session(plan_id)
    assert not [i for i in session["items"] if i["kind"] == "whole_dish"]
    assert any(i["state"] == "better_fresh" and "shrimp" in i["title"].lower() for i in session["items"])


# 3 -----------------------------------------------------------------------

def test_meat_still_frozen_on_prep_day_is_marinated_frozen_and_never_cooked_ahead():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _skewers()
    entry = _plan(plan_id, 2, "Chicken Skewers")
    _defrost_row(plan_id, entry, 1)

    session = _session(plan_id)
    mar = next(i for i in session["items"] if i["kind"] == "marinate")
    assert mar["title"] == "Marinate the frozen chicken thighs, 1 lb"
    assert mar["on_list"] and not mar["can_remove"]
    assert "Put the frozen chicken thighs in the marinade bag; it marinates as it thaws in the fridge." in mar["line"]
    assert not [i for i in session["items"] if i["kind"] == "whole_dish"]
    assert not _thaws(plan_id)


def test_frozen_meat_for_a_later_meal_goes_back_in_the_freezer_with_its_own_thaw():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _skewers()
    entry = _plan(plan_id, 5, "Chicken Skewers")
    _defrost_row(plan_id, entry, 3)
    session = _session(plan_id)
    mar = next(i for i in session["items"] if i["kind"] == "marinate")
    assert "back in the freezer" in mar["line"]
    assert any(i["kind"] == "freeze" for i in _on(session))
    # defrost.py's row is its thaw; no second one, and its row is untouched.
    assert not _thaws(plan_id)
    assert [r["task_date"] for r in _thaws(plan_id, kind_only=False)] == [_d(3)]


# 4 -----------------------------------------------------------------------

def test_a_pinned_marinate_task_five_days_out_is_frozen_and_its_thaw_booked():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _recipe("Steak Tacos", [{"item": "Flank steak", "qty": "1 lb", "category": "meat/seafood"}],
            ["Slice and sear the steak."])
    entry = _plan(plan_id, 5, "Steak Tacos")
    tools.save_prep_tasks(plan_id, [
        {"task_date": _d(0), "description": "Marinate the flank steak", "related_meal": "Steak Tacos"},
    ])
    session = _session(plan_id)
    task = next(i for i in session["items"] if i["title"] == "Marinate the flank steak")
    assert task["always"] and not task["can_remove"]
    assert "Freeze it in the bag." in task["line"]
    assert any(i["kind"] == "freeze" and i["on_list"] for i in session["items"])
    [thaw] = _thaws(plan_id)
    assert (thaw["meal_plan_entry_id"], thaw["task_date"]) == (entry, _d(4))


# 5 -----------------------------------------------------------------------

def test_marinade_thaws_follow_the_meal_and_go_with_it_leaving_defrosts_rows_alone():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _skewers()
    entry = _plan(plan_id, 4, "Chicken Skewers")
    other = _plan(plan_id, 6, "Pork Chops", slot="lunch")
    _defrost_row(plan_id, other, 5, item="pork chops", meal="Pork")
    _session(plan_id)
    assert [(t["meal_plan_entry_id"], t["task_date"]) for t in _thaws(plan_id)] == [(entry, _d(3))]

    def move(day):
        conn = db.get_conn()
        conn.execute("UPDATE meal_plan_entries SET date = ? WHERE id = ?", (_d(day), entry))
        conn.commit()
        conn.close()
        _session(plan_id)

    move(5)   # still frozen in the bag: the thaw follows
    assert [(t["meal_plan_entry_id"], t["task_date"]) for t in _thaws(plan_id)] == [(entry, _d(4))]
    move(1)   # now inside two days: it stays in the fridge, no thaw
    assert _thaws(plan_id) == []
    move(4)
    assert len(_thaws(plan_id)) == 1
    conn = db.get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (entry,))
    conn.commit()
    conn.close()
    _session(plan_id)
    assert _thaws(plan_id) == []
    # defrost.py's own row for the other meal was never touched.
    assert [r["description"] for r in _thaws(plan_id, kind_only=False)] == [
        "Move the pork chops to the fridge — for the Pork."]


def test_skipping_prep_this_week_takes_its_marinade_thaw_away():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _skewers()
    _plan(plan_id, 4, "Chicken Skewers")
    _session(plan_id)
    assert len(_thaws(plan_id)) == 1
    tools.set_skip_prep_this_week(True, plan_id)
    assert tools.prep_sessions_for_plan(plan_id) == []
    assert _thaws(plan_id) == []


# 6 -----------------------------------------------------------------------

def test_seafood_condiments_are_not_seafood():
    for name in ("Thai Fish Sauce Chicken", "fish sauce", "shrimp paste", "anchovy paste",
                 "oyster sauce", "oyster mushrooms", "clam juice"):
        assert prep_list.seafood_family(name) is None, name
    assert prep_list.seafood_family("salmon fillets") == "fish"
    assert prep_list.seafood_family("tuna steak") == "fish"
    assert prep_list.seafood_family("cod") == "fish"
    assert prep_list.seafood_family("prawns") == "shrimp"
    assert prep_list.seafood_family("sea scallops") == "shrimp"
    assert prep_list.seafood_family("mussels") == "seafood"
    assert prep_list.seafood_family("squid") == "seafood"


def test_a_fish_sauce_chicken_is_still_marinated_and_not_called_fish():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    _recipe("Thai Fish Sauce Chicken", [
        {"item": "Chicken thighs", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Fish sauce", "qty": "2 tbsp"},
        {"item": "Shrimp paste", "qty": "1 tsp"},
    ], ["Marinate the chicken in the fish sauce.", "Grill it."])
    _plan(plan_id, 1, "Thai Fish Sauce Chicken")
    session = _session(plan_id)
    assert any(i["kind"] == "marinate" and i["on_list"] for i in session["items"])
    assert not [i for i in session["items"] if i["state"] == "better_fresh"]


# nits --------------------------------------------------------------------

def test_the_same_dressing_in_eight_meals_is_one_shared_step():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")  # something to prep, so the day is a session
    for n in range(8):
        _recipe(f"Grain Bowl {chr(65 + n)}", [{"item": "Farro", "qty": "1 cup", "category": "pantry"}],
                ["Whisk the lemon-herb dressing together.", "Toss."])
        _plan(plan_id, 1 + n // 2, f"Grain Bowl {chr(65 + n)}", slot=("lunch", "dinner")[n % 2])
    session = _session(plan_id)
    dressings = [i for i in session["items"] if "dressing" in i["title"].lower()]
    assert len(dressings) == 1
    assert dressings[0]["title"] == "Lemon-herb dressing, 8 jars"
    assert dressings[0]["shared"] and dressings[0]["on_list"]


def test_the_didnt_fit_line_says_the_real_shortfall_and_odd_budgets_read_as_hours():
    item = prep_list._base_item(key="x", hands_on=12, passive=0, first_date=_d(2), first_slot="dinner")
    day = datetime.date.fromisoformat(_d(2)).strftime("%A")
    assert prep_list._didnt_fit_line(item, 55, 60) == f"12 min. Make it {day}, or add 10 min"
    assert prep_list._didnt_fit_line(item, 115, 60) == f"12 min. Make it {day}, or add 1 hr 10"
    assert prep_list.hours_phrase(90) == "1 hr 30"
    assert prep_list.header_line(90, 30).startswith("Up to 1 hr 30 ·")
    assert "your 1 hr 30," in prep_list.made_on_the_night_line(90)


# re-verification of 3f41dd4 ------------------------------------------------

def _move(entry, day):
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET date = ? WHERE id = ?", (_d(day), entry))
    conn.commit()
    conn.close()


def _with_stir_fry():
    plan_id = _week()
    _stir_fry()
    _plan(plan_id, 1, "Veg Stir Fry")
    return plan_id


def test_a_marinade_already_frozen_keeps_its_thaw_when_the_meal_moves_closer():
    """Bag frozen for day 5; the marinade is ticked done; the meal moves to
    day 2. The bag is still in the freezer, so the thaw follows the meal to
    the night before — it is never just dropped."""
    plan_id = _with_stir_fry()
    _skewers()
    entry = _plan(plan_id, 5, "Chicken Skewers")
    session = _session(plan_id)
    mar = next(i for i in session["items"] if i["kind"] == "marinate")
    tools.set_prep_list_mark(plan_id, session["date"], mar["key"], "done")
    _move(entry, 2)
    _session(plan_id)
    assert [(t["meal_plan_entry_id"], t["task_date"]) for t in _thaws(plan_id)] == [(entry, _d(1))]


def test_a_marinade_whose_prep_day_has_passed_keeps_its_thaw():
    plan_id = _with_stir_fry()
    _skewers()
    entry = _plan(plan_id, 5, "Chicken Skewers")
    _session(plan_id)
    conn = db.get_conn()
    conn.execute("UPDATE prep_tasks SET detail_json = json_set(detail_json, '$.prep_date', ?) "
                 "WHERE json_extract(detail_json, '$.kind') = ?", (_d(-1), prep_list.MARINADE_THAW_KIND))
    conn.commit()
    conn.close()
    _move(entry, 2)
    _session(plan_id)
    assert [(t["meal_plan_entry_id"], t["task_date"]) for t in _thaws(plan_id)] == [(entry, _d(1))]


def test_a_cooked_dish_with_avocado_in_it_is_never_made_ahead_whole():
    plan_id = _with_stir_fry()
    _recipe("Chicken Avocado Wrap", [
        {"item": "Chicken breast", "qty": "1 lb", "category": "meat/seafood"},
        {"item": "Avocado", "qty": "1", "category": "produce"},
        {"item": "Tortillas", "qty": "4", "category": "pantry"},
    ], ["Cook the chicken in a pan.", "Slice the avocado and fill the wraps."], prep=10, cook=10)
    _plan(plan_id, 2, "Chicken Avocado Wrap")
    session = _session(plan_id)
    assert not [i for i in session["items"] if i["kind"] == "whole_dish"]
    assert any(i["state"] == "better_fresh" and "avocado" in i["title"].lower() for i in session["items"])


def test_shrimp_filed_under_pantry_is_still_seafood():
    plan_id = _with_stir_fry()
    _recipe("Shrimp Noodles", [
        {"item": "Shrimp", "qty": "1 lb", "category": "pantry"},
        {"item": "Noodles", "qty": "8 oz", "category": "pantry"},
    ], ["Boil the noodles.", "Toss with the shrimp in the pan."])
    _plan(plan_id, 2, "Shrimp Noodles")
    assert prep_list.dish_fresh_reason("Noodles", {"ingredients": [{"item": "Shrimp", "category": "pantry"}]})
    session = _session(plan_id)
    assert not [i for i in session["items"] if i["kind"] == "whole_dish"]
    assert any(i["state"] == "better_fresh" and "shrimp" in i["title"].lower() for i in session["items"])


def test_a_pinned_marinate_task_for_frozen_meat_is_worded_frozen_with_no_second_thaw():
    plan_id = _with_stir_fry()
    _recipe("Steak Tacos", [{"item": "Flank steak", "qty": "1 lb", "category": "meat/seafood"}],
            ["Slice and sear the steak."])
    entry = _plan(plan_id, 5, "Steak Tacos")
    _defrost_row(plan_id, entry, 3, item="flank steak", meal="Steak Tacos")
    tools.save_prep_tasks(plan_id, [
        {"task_date": _d(0), "description": "Marinate the flank steak", "related_meal": "Steak Tacos"},
    ])
    session = _session(plan_id)
    task = next(i for i in session["items"] if i["title"] == "Marinate the flank steak")
    assert "Put the frozen flank steak in the marinade bag" in task["line"]
    assert _thaws(plan_id) == []
