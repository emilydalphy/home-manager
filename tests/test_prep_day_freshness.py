"""
Prepped food is eaten first (Emily, 2026-10-31) — three Loop Board cards.

1. A make-ahead breakfast is made on the prep day and covers the mornings
   right after it while it keeps; the rest are fresh. Her draft (Nov 1-7,
   prep Sunday) had egg bites Sunday and Monday and scrambled eggs Tuesday
   to Saturday: "I'll make the egg bites on the Sunday then can eat them
   Monday - Wednesday while they're fresh."
2. The general rule for every meal: a prep day's batch lands on the days
   right after it, inside its keep window; fresh-only dishes go later. A
   post-generation check (prep_freshness.keep_prepped_first) trades a draft
   that breaks it — moves, not re-picks, no model call.
3. The draft's "Couldn't fit the 5 things going off." names the food and
   is the one tap to Freeze it / Let it go (use_up.count_line, shell.js).
"""
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from conftest import household_today, on_the_weeks_monday
from app import agent, db, tools
from app.tools import breakfast_prep, cook_ahead, prep_freshness, prep_sessions, use_up, weekday_lunches

ON_MONDAY = on_the_weeks_monday()


def _sunday() -> datetime.date:
    """A Sunday a week or so ahead, so a Sunday-to-Saturday plan is all future."""
    today = household_today()
    return today + datetime.timedelta(days=(6 - today.weekday()) % 7 + 7)


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _days(start: datetime.date, n: int = 7) -> list[str]:
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def _plan(start: str, meals) -> tuple[int, dict]:
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    ids = {}
    for date_str, dish, slot in meals:
        ids[(date_str, slot)] = tools.plan_meal(date_str, dish, slot=slot, weekly_plan_id=plan_id)["entry_id"]
    return plan_id, ids


def _slot(plan_id: int, slot: str) -> dict[str, str]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? AND mpe.slot = ?",
        (plan_id, slot),
    ).fetchall()
    conn.close()
    return {r["date"]: r["meal"] for r in rows}


def _derived(entry_id: int) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


def _sunday_prep():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])


BITES = "Egg White and Cottage Cheese Bites"
SCRAMBLED = "Veggie Scrambled Eggs"


def _emilys_draft():
    """Her Nov 1-7 draft: bites Sun, Mon; scrambled Tue-Sat."""
    days = _days(_sunday())
    meals = [(d, BITES if i < 2 else SCRAMBLED, "breakfast") for i, d in enumerate(days)]
    plan_id, ids = _plan(days[0], meals)
    return plan_id, ids, days


# ---------- the keep table ----------

@pytest.mark.parametrize("name,keeps,after", [
    ("Egg White and Cottage Cheese Bites", 4, 3),
    ("Spinach Frittata", 4, 3),
    ("Baked Oatmeal Cups", 4, 3),
    ("Blueberry Muffins", 4, 3),
    ("Overnight Oats with Berries", 3, 2),
    ("Greek Yogurt Parfait", 2, 1),
    ("Overnight Oats Parfait", 2, 1),     # the shorter keep wins
    ("Chia Pudding", 4, 3),
    ("Cottage Cheese Bowl", 4, 3),        # unknown: the default, never past three days
])
def test_how_long_a_breakfast_keeps(name, keeps, after):
    assert breakfast_prep.keeps_days(name) == keeps
    assert breakfast_prep.mornings_after(name) == after


def test_prep_dates_include_the_day_before_a_monday_week():
    days = _days(_monday())
    # The Sunday before the week feeds Monday; the week's own last Sunday
    # feeds nothing in it.
    got = prep_freshness.prep_dates(days, [{"weekday": "sunday"}])
    assert got == [(_monday() - datetime.timedelta(days=1)).isoformat()]
    assert prep_freshness.prep_dates(days, []) == []


# ---------- card 1: Emily's breakfast ----------

def test_emilys_week_becomes_bites_monday_to_wednesday():
    _sunday_prep()
    plan_id, ids, d = _emilys_draft()

    out = prep_freshness.keep_prepped_first(plan_id, d)

    got = _slot(plan_id, "breakfast")
    assert [got[x] for x in d] == [SCRAMBLED, BITES, BITES, BITES, SCRAMBLED, SCRAMBLED, SCRAMBLED]
    # One trade (Sunday's bites into Tuesday) and one copy (Wednesday).
    assert [(m["from"], m["to"]) for m in out["moved"]] == [(d[0], d[2])]
    assert [f["date"] for f in out["filled"]] == [d[3]]
    # Running it again changes nothing.
    again = prep_freshness.keep_prepped_first(plan_id, d)
    assert again == {"moved": [], "filled": []}


def test_bites_later_in_the_week_come_forward():
    _sunday_prep()
    d = _days(_sunday())
    names = [SCRAMBLED, SCRAMBLED, SCRAMBLED, SCRAMBLED, BITES, BITES, SCRAMBLED]
    plan_id, _ = _plan(d[0], [(x, n, "breakfast") for x, n in zip(d, names)])

    prep_freshness.keep_prepped_first(plan_id, d)

    got = _slot(plan_id, "breakfast")
    assert [got[x] for x in d[1:4]] == [BITES, BITES, BITES]
    assert got[d[4]] == SCRAMBLED and got[d[5]] == SCRAMBLED


def test_overnight_oats_cover_two_mornings():
    _sunday_prep()
    d = _days(_sunday())
    oats = "Overnight Oats with Berries"
    names = [oats, oats, SCRAMBLED, SCRAMBLED, SCRAMBLED, SCRAMBLED, SCRAMBLED]
    plan_id, _ = _plan(d[0], [(x, n, "breakfast") for x, n in zip(d, names)])

    prep_freshness.keep_prepped_first(plan_id, d)

    got = _slot(plan_id, "breakfast")
    assert [got[x] for x in d] == [SCRAMBLED, oats, oats, SCRAMBLED, SCRAMBLED, SCRAMBLED, SCRAMBLED]


def test_no_prep_day_leaves_the_week_alone():
    plan_id, _, d = _emilys_draft()
    before = _slot(plan_id, "breakfast")
    assert prep_freshness.keep_prepped_first(plan_id, d) == {"moved": [], "filled": []}
    assert _slot(plan_id, "breakfast") == before


def test_a_fresh_breakfast_household_is_left_alone():
    _sunday_prep()
    tools.set_breakfast_prep("fresh")
    plan_id, _, d = _emilys_draft()
    before = _slot(plan_id, "breakfast")
    prep_freshness.keep_prepped_first(plan_id, d)
    assert _slot(plan_id, "breakfast") == before


def test_a_breakfast_they_asked_for_stays_on_its_day():
    _sunday_prep()
    plan_id, ids, d = _emilys_draft()
    # Sunday's bites were their own words: not this pass's to move.
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"freeform": "egg bites on Sunday"}), ids[(d[0], "breakfast")]))
    conn.commit()
    conn.close()

    prep_freshness.keep_prepped_first(plan_id, d)

    got = _slot(plan_id, "breakfast")
    assert got[d[0]] == BITES


def test_a_morning_whose_dish_would_leave_the_week_is_not_copied_over():
    _sunday_prep()
    d = _days(_sunday())
    names = [SCRAMBLED, BITES, "Shakshuka", SCRAMBLED, SCRAMBLED, SCRAMBLED, SCRAMBLED]
    plan_id, _ = _plan(d[0], [(x, n, "breakfast") for x, n in zip(d, names)])

    prep_freshness.keep_prepped_first(plan_id, d)

    got = _slot(plan_id, "breakfast")
    # Shakshuka is the week's only one: it stays. Wednesday's scrambled is
    # on other mornings too, so it becomes the batch.
    assert got[d[2]] == "Shakshuka"
    assert got[d[1]] == BITES and got[d[3]] == BITES


def test_the_variety_cap_is_never_made_worse():
    """No dish ends up on more mornings than it was, except the batch inside
    its window (desktop/variety-1031's cap counts that as fine)."""
    _sunday_prep()
    plan_id, _, d = _emilys_draft()
    before = list(_slot(plan_id, "breakfast").values())
    prep_freshness.keep_prepped_first(plan_id, d)
    after = list(_slot(plan_id, "breakfast").values())
    assert after.count(SCRAMBLED) <= before.count(SCRAMBLED)
    assert after.count(BITES) <= 1 + breakfast_prep.mornings_after(BITES)


# ---------- card 2: every meal ----------

def test_a_batch_lunch_past_its_window_trades_into_it():
    _sunday_prep()
    d = _days(_monday())
    bowls = "Chicken Burrito Bowls"
    names = [bowls, bowls, "Lentil Soup", "Tuna Salad", bowls, "Pasta Salad", "Grain Bowl"]
    plan_id, _ = _plan(d[0], [(x, n, "lunch") for x, n in zip(d, names)])

    out = prep_freshness.keep_prepped_first(plan_id, d)

    got = _slot(plan_id, "lunch")
    assert [got[x] for x in d[:3]] == [bowls, bowls, bowls]
    assert got[d[4]] == "Lentil Soup"           # the fresh one went later
    assert out["filled"] == []                   # lunches are only ever traded
    assert sorted(got.values()) == sorted(names)  # same dishes, same counts


def test_a_trade_that_breaks_a_cap_is_not_made():
    _sunday_prep()
    d = _days(_monday())
    tools.add_recipe("Slow Braise", ingredients=[{"item": "beef", "qty": "2 lbs"}], default_servings=2,
                     prep_time_minutes=20, cook_time_minutes=150, instructions=["Braise."])
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 can"}], default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=30, instructions=["Simmer."])
    names = ["Chili", "Tacos", "Slow Braise", "Fish", "Chili", "Pizza", "Curry"]
    plan_id, _ = _plan(d[0], [(x, n, "dinner") for x, n in zip(d, names)])
    # Friday is a rush night: the braise can't go there.
    caps = {(x, "dinner"): None for x in d}
    caps[(d[4], "dinner")] = 30
    batch_caps = dict(caps)

    prep_freshness.keep_prepped_first(plan_id, d, caps=caps, batch_caps=batch_caps)

    got = _slot(plan_id, "dinner")
    assert got[d[4]] != "Slow Braise"
    # Tacos (no cap) traded instead: Chili Mon and Tue, Tacos Friday.
    assert got[d[1]] == "Chili" and got[d[4]] == "Tacos"


def test_a_dinner_on_a_guests_night_stays():
    _sunday_prep()
    d = _days(_monday())
    names = ["Chili", "Roast Chicken", "Tacos", "Fish", "Chili", "Pizza", "Curry"]
    plan_id, _ = _plan(d[0], [(x, n, "dinner") for x, n in zip(d, names)])
    intake = {"night_tags": {d[1]: ["guests"]}}

    prep_freshness.keep_prepped_first(plan_id, d, intake)

    got = _slot(plan_id, "dinner")
    assert got[d[1]] == "Roast Chicken"
    assert got[d[2]] == "Chili"


def test_lunches_the_household_answered_are_theirs():
    _sunday_prep()
    d = _days(_monday())
    bowls = "Chicken Burrito Bowls"
    names = [bowls, bowls, "Lentil Soup", "Tuna Salad", bowls, "Pasta Salad", "Grain Bowl"]
    plan_id, _ = _plan(d[0], [(x, n, "lunch") for x, n in zip(d, names)])
    intake = {"weekday_lunches": {"days": [{"date": d[0], "kind": "cooked"}]}}
    assert weekday_lunches.kinds_by_date(intake)

    prep_freshness.keep_prepped_first(plan_id, d, intake)

    assert _slot(plan_id, "lunch")[d[4]] == bowls


# ---------- approval: the batch keeps, and is made on the prep day ----------

@ON_MONDAY
def test_approval_batches_a_breakfast_only_as_far_as_it_keeps():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    _sunday_prep()
    d = _days(_monday())
    oats = "Overnight Oats with Berries"
    tools.add_recipe(oats, ingredients=[{"item": "rolled oats", "qty": "2 cups"}], default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=0, instructions=["Stir and chill overnight."])
    plan_id, ids = _plan(d[0], [(x, oats, "breakfast") for x in d[:4]])

    out = cook_ahead.apply_prep_day_batches(plan_id)

    # Overnight oats keep three days counting the day they're made: made
    # Sunday, they're Monday and Tuesday. Wednesday is a batch of its own.
    assert _derived(ids[(d[0], "breakfast")])["make_double_for"] == [f"{d[1]}:breakfast"]
    assert _derived(ids[(d[2], "breakfast")])["make_double_for"] == [f"{d[3]}:breakfast"]
    assert [a["dish"] for a in out["applied"]] == [oats, oats]
    # Only the batch made the day after the prep day is "made Sunday".
    assert "prep_date" in _derived(ids[(d[0], "breakfast")])
    assert "prep_date" not in _derived(ids[(d[2], "breakfast")])


@ON_MONDAY
def test_a_breakfast_batch_after_a_prep_day_is_made_on_it():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    _sunday_prep()
    tools.add_recipe(BITES, ingredients=[{"item": "egg whites", "qty": "1 cup"}], default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=20, instructions=["Bake."])
    d = _days(_monday())
    sunday_before = (_monday() - datetime.timedelta(days=1)).isoformat()
    plan_id, ids = _plan(d[0], [(x, BITES, "breakfast") for x in d[:3]])

    cook_ahead.apply_prep_day_batches(plan_id)

    mon = _derived(ids[(d[0], "breakfast")])
    assert mon["prep_date"] == sunday_before and mon["prep_day"] == "sunday"
    batch = weekday_lunches.prepped_batch_by_cook(plan_id)[ids[(d[0], "breakfast")]]
    assert batch["lunch_dates"] == d[:3] and batch["slot"] == "breakfast"
    # Sunday's prep session lists it, in breakfast words.
    sessions = prep_sessions.prep_sessions_for_plan(plan_id)
    sunday = next(s for s in sessions if s["date"] == sunday_before)
    item = next(i for i in sunday["items"] if i["entry_id"] == ids[(d[0], "breakfast")])
    assert item["line"] == "For Monday, Tuesday and Wednesday’s breakfasts."
    # Un-batched, it is Monday's own cook again — the stamp goes with it.
    cook_ahead.set_cook_ahead(ids[(d[0], "breakfast")], [])
    assert ids[(d[0], "breakfast")] not in weekday_lunches.prepped_batch_by_cook(plan_id)
    assert "prep_date" not in _derived(ids[(d[0], "breakfast")])


@ON_MONDAY
def test_a_second_prep_days_batch_keeps_its_own_window():
    """Sunday and Wednesday prep, bites every morning: Thursday's batch was
    made Wednesday, so it reaches Saturday, not Sunday."""
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}, {"weekday": "wednesday", "minutes": 60}])
    tools.add_recipe(BITES, ingredients=[{"item": "egg whites", "qty": "1 cup"}], default_servings=2,
                     prep_time_minutes=10, cook_time_minutes=20, instructions=["Bake."])
    d = _days(_monday())
    plan_id, ids = _plan(d[0], [(x, BITES, "breakfast") for x in d])

    cook_ahead.apply_prep_day_batches(plan_id)

    assert _derived(ids[(d[3], "breakfast")])["make_double_for"] == [f"{d[4]}:breakfast", f"{d[5]}:breakfast"]
    assert "links_to" not in _derived(ids[(d[6], "breakfast")])


def test_a_dish_using_food_going_off_is_never_moved_later():
    _sunday_prep()
    d = _days(_monday())
    names = ["Chili", "Spinach Pasta", "Tacos", "Fish", "Chili", "Pizza", "Curry"]
    plan_id, ids = _plan(d[0], [(x, n, "dinner") for x, n in zip(d, names)])
    conn = db.get_conn()
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"use_up_ids": [1]}), ids[(d[1], "dinner")]))
    conn.commit()
    conn.close()

    prep_freshness.keep_prepped_first(plan_id, d)

    got = _slot(plan_id, "dinner")
    assert got[d[1]] == "Spinach Pasta"
    assert got[d[2]] == "Chili" and got[d[4]] == "Tacos"   # the next window night traded instead


def test_the_prep_evenings_own_dinner_stays():
    """A Sunday-start week: Sunday's dinner is cooked on the prep evening."""
    _sunday_prep()
    d = _days(_sunday())
    names = ["Chili", "Tacos", "Fish", "Pizza", "Chili", "Curry", "Dal"]
    plan_id, _ = _plan(d[0], [(x, n, "dinner") for x, n in zip(d, names)])

    prep_freshness.keep_prepped_first(plan_id, d)

    assert _slot(plan_id, "dinner")[d[0]] == "Chili"


# ---------- the draft says it ----------

def test_the_draft_row_says_made_sunday():
    _sunday_prep()
    plan_id, _, d = _emilys_draft()
    prep_freshness.keep_prepped_first(plan_id, d)

    menu = tools.get_week_menu(plan_id)
    notes = {day["date"]: (day.get("breakfast") or {}).get("schedule_note") for day in menu["days"]}
    assert [notes[x] for x in d[1:4]] == ["Made Sunday"] * 3
    assert not notes[d[0]] and not notes[d[4]]


def test_the_plan_row_shows_the_note_for_breakfast():
    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text()
    assert "slot === 'breakfast'\n          ? wkRowMetaLine(first.entry" in js


def test_the_prompt_carries_the_rule():
    src = pathlib.Path(agent.__file__).read_text()
    assert "PREPPED FOOD IS EATEN FIRST" in src
    flat = " ".join(src.replace("\\\n", " ").split())
    assert "ONE make-ahead breakfast covers the mornings in each prep day's window" in flat
    assert "(egg bites Monday to Wednesday after a Sunday prep)" in flat
    assert "_prep_freshness.keep_prepped_first(" in src


# ---------- card 3: the going-off line ----------

def test_the_line_names_the_food():
    assert use_up.count_line(0, 5, ["water", "spinach", "milk", "eggs", "feta"]) == \
        "The water, spinach and 3 more are going off and nothing this week uses them."
    assert use_up.count_line(0, 1, ["spinach"]) == "The spinach is going off and nothing this week uses it."
    assert use_up.count_line(0, 2, ["spinach", "milk"]) == \
        "The spinach and milk are going off and nothing this week uses them."
    assert use_up.count_line(7, 9, ["half cabbage", "cilantro"]) == \
        "Uses 7 of the 9 things going off. Nothing uses the half cabbage or cilantro."
    # Every one frozen or let go: nothing left to say.
    assert use_up.count_line(0, 5, []) == ""
    assert use_up.count_line(3, 5, []) == "Uses 3 of the 5 things going off."
    # The old two-argument shape is unchanged.
    assert use_up.count_line(6, 8) == "Uses 6 of the 8 things going off."


def test_the_line_is_the_one_tap_to_the_rows():
    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text()
    line = js[js.index("function wkUseUpLineHtml"):js.index("var USE_UP_ACTIONS")]
    assert 'data-wk-useup-open="1"' in line and "wkUnfitRowsHtml(rows)" in line
    assert "weekState.useUpOpen" in js
    # Said once: no second "Couldn't fit these" heading.
    assert "Couldn’t fit these</h2>" not in js
    rows = js[js.index("function wkUnfitRowsHtml"):js.index("function wireCouldntFit")]
    assert "data-wk-useup=\"let_go\"" in rows and "USE_UP_ACTIONS[r.primary]" in rows


# ---------- end to end: a stubbed draft ----------

def test_a_drafted_week_comes_out_prepped_first(monkeypatch):
    """The model (stubbed) sends bites Friday and Saturday and scrambled
    the rest, on a Monday week with a Sunday prep. The draft that's saved
    has bites Monday to Wednesday, and its rows say "Made Sunday"."""
    from test_freezer_portion_planned import _slot as _gen_slot

    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    _sunday_prep()
    week = (_monday() + datetime.timedelta(days=7)).isoformat()
    d = tools._week_dates(week)
    days = []
    for i, x in enumerate(d):
        days.append(_gen_slot(x, "breakfast", BITES if i in (4, 5) else SCRAMBLED))
        days.append(_gen_slot(x, "snack", "Apple slices"))
        days.append(_gen_slot(x, "lunch", f"Grain salad {i}"))
        days.append(_gen_slot(x, "dinner", ["Lamb Stew", "Miso Cod", "Bibimbap", "Laksa",
                                            "Ramen", "Gumbo", "Dal"][i]))
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    tools.save_week_intake(week, moods=["Comfort food"])

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    got = _slot(plan_id, "breakfast")
    assert [got[x] for x in d[:3]] == [BITES, BITES, BITES]
    assert BITES not in [got[x] for x in d[3:]]
    menu = tools.get_week_menu(plan_id)
    notes = [(day.get("breakfast") or {}).get("schedule_note") for day in menu["days"][:3]]
    assert notes == ["Made Sunday"] * 3
