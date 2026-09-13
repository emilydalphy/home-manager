"""
Holidays, slice 2 — hosting the big meal (Loop Board "Holidays: Pomona
knows 12 October is coming and asks how you're spending it").

"Hosting" now means a menu rather than a dish — the main, sides and
something sweet on that dinner's one plan entry, every guest's restriction
honoured — the shop in two trips, the make-ahead work spread over the days
before, and a day-of timeline working back from the time it's on the
table. Changing the answer away from hosting unwinds all of it. Nothing
here is Thanksgiving-specific; a holiday is a date with a name and an
answer. See app/tools/big_meal.py.
"""
import json
from datetime import date, datetime, timedelta

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import big_meal as bm
from app.tools import holidays as hol
from tests.test_holidays import _full_week, _named, _shift, _thanksgiving, stub_model  # noqa: F401


# ---------- fixtures ----------

@pytest.fixture
def family():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Vineeth")
    tools.set_member_age_group("Vineeth", "adult")


@pytest.fixture
def recipes():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=2)
    tools.add_recipe("Roast Chicken", ingredients=[{"item": "whole chicken", "qty": "1", "category": "meat/seafood"}],
                     prep_time_minutes=20, cook_time_minutes=90, default_servings=2)


# A menu the way the model would send it: a main (used only when asked
# for), three sides and a sweet, with honest make-ahead and oven flags.
MAIN = {
    "name": "Herb Roast Turkey",
    "ingredients": [{"item": "turkey", "qty": "12 lb", "category": "meat/seafood"},
                    {"item": "butter", "qty": "1 lb", "category": "dairy"},
                    {"item": "fresh thyme", "qty": "1 bunch", "category": "produce"}],
    "instructions": ["Butter the bird.", "Roast.", "Rest."],
    "food_groups": ["protein"], "cuisine": "Canadian", "main_protein": "turkey",
    "prep_minutes": 30, "cook_minutes": 180, "rest_minutes": 30, "oven": True, "ahead_days": 0,
}
DISHES = [
    {"name": "Sage and Onion Stuffing", "role": "side", "covers": ["carb"],
     "ingredients": [{"item": "bread", "qty": "2 loaves", "category": "pantry"},
                     {"item": "onions", "qty": "3", "category": "produce"}],
     "instructions": ["Cube the bread.", "Bake."], "minutes": 20, "cook_minutes": 40, "oven": True,
     "ahead_days": 1, "ahead_step": "Make the stuffing"},
    {"name": "Maple Roasted Carrots", "role": "side", "covers": ["vegetable"],
     "ingredients": [{"item": "carrots", "qty": "3 lb", "category": "produce"},
                     {"item": "maple syrup", "qty": "1 bottle", "category": "pantry"}],
     "instructions": ["Toss.", "Roast."], "minutes": 10, "cook_minutes": 25, "oven": True, "ahead_days": 0},
    {"name": "Green Beans with Lemon", "role": "side", "covers": ["vegetable"],
     "ingredients": [{"item": "green beans", "qty": "2 lb", "category": "produce"}],
     "instructions": ["Blanch.", "Toss."], "minutes": 15, "cook_minutes": 5, "oven": False, "ahead_days": 0},
    {"name": "Pumpkin Pie", "role": "sweet", "covers": [],
     "ingredients": [{"item": "pumpkin puree", "qty": "2 cans", "category": "pantry"},
                     {"item": "frozen pie shells", "qty": "2", "category": "frozen"}],
     "instructions": ["Fill.", "Bake."], "minutes": 20, "cook_minutes": 50, "oven": True,
     "ahead_days": 1, "ahead_step": "Bake the pumpkin pie"},
]


@pytest.fixture
def proposer(monkeypatch):
    """The model call, stubbed: records the context, answers with MAIN + DISHES (or what the test sets)."""
    seen = {"calls": 0}
    answer = {"main": MAIN, "dishes": DISHES}

    def _fake(context):
        seen["calls"] += 1
        seen["context"] = context
        if isinstance(answer.get("raise"), Exception):
            raise answer["raise"]
        return {k: v for k, v in answer.items() if k != "raise"}

    monkeypatch.setattr(agent, "generate_big_meal_llm", _fake)
    seen["answer"] = answer
    return seen


def _week_with_dinner(tg: str, dish: str = "Chili", approve: bool = False) -> int:
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, dish, slot="dinner", weekly_plan_id=plan_id)
    if approve:
        tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id


def _entry(plan_id: int, day: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.*, r.name AS recipe_name, r.default_servings FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (plan_id, day),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"{day} dinner holds {len(rows)} rows — a slot is exactly one row"
    return rows[0]


def _sides(row) -> list[dict]:
    return json.loads(row["sides_json"] or "[]")


def _list() -> dict[str, str]:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list()}


def _holiday_tasks() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM prep_tasks WHERE task_type = 'holiday' ORDER BY task_date, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- a menu, not a dish ----------

def test_hosting_builds_the_menu_around_the_dinner_already_planned(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")

    result = tools.answer_holiday(tg, "hosting", headcount=5, on_table_at="5pm", guest_notes="Sam’s vegetarian")

    assert result["big_meal"]["menu"] == "built" and result["big_meal"]["status"] == "full"
    assert result["on_table_at"] == "17:00" and result["guest_notes"] == "Sam’s vegetarian"
    # The main is what was already there — the answer builds around it.
    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Roast Chicken" and row["slot_state"] == "planned"
    assert json.loads(row["derived_from_json"])["holiday_menu"] is True
    assert row["reasoning"] == "The big meal for Thanksgiving — 7 at the table."
    sides = _sides(row)
    assert [s["name"] for s in sides] == ["Sage and Onion Stuffing", "Maple Roasted Carrots", "Green Beans with Lemon", "Pumpkin Pie"]
    assert [s["role"] for s in sides] == ["side", "side", "side", "sweet"]
    # The model was asked for sides only, for the whole table, with the guests' note.
    ctx = proposer["context"]
    assert ctx["want_main"] is False and ctx["main"]["name"] == "Roast Chicken"
    assert ctx["eaters"] == 7 and ctx["guest_notes"] == "Sam’s vegetarian" and ctx["side_count"] == 3
    # The Plan card reads the menu through the label every side already has.
    menu = tools.get_week_menu(plan_id)
    day = [d for d in menu["days"] if d["date"] == tg][0]
    assert day["dinner"]["title"] == "Roast Chicken"
    assert day["dinner"]["plate_note"].startswith("with Sage and Onion Stuffing, ")
    assert day["holiday"]["label"] == "Thanksgiving · hosting · 5 more"


def test_hosting_an_open_night_proposes_the_main_too_written_for_the_table(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=tg, slot="dinner", open_reason="Not sure yet.")

    result = tools.answer_holiday(tg, "hosting", headcount=4)

    assert result["big_meal"]["status"] == "full"
    assert proposer["context"]["want_main"] is True
    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Herb Roast Turkey" and row["slot_state"] == "planned"
    assert row["default_servings"] == 6, "the recipe is written for the table, so shopping scales 1:1"
    recipe = tools.get_recipe("Herb Roast Turkey")
    assert recipe["prep_time_minutes"] == 30 and recipe["cook_time_minutes"] == 180
    assert len(_sides(row)) == 4


def test_a_guests_restriction_and_the_households_own_are_both_honoured(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken")
    tools.set_member_dietary_restrictions("Vineeth", ["shellfish allergy"])
    proposer["answer"]["dishes"] = DISHES + [
        {"name": "Walnut Salad", "role": "side", "covers": ["vegetable"],
         "ingredients": [{"item": "walnuts", "qty": "1 cup", "category": "pantry"}, {"item": "greens", "qty": "1 bag", "category": "produce"}],
         "instructions": ["Toss."], "minutes": 5, "cook_minutes": 0, "oven": False, "ahead_days": 0},
        {"name": "Shrimp Cocktail", "role": "side", "covers": ["protein"],
         "ingredients": [{"item": "shrimp", "qty": "2 lb", "category": "meat/seafood"}],
         "instructions": ["Chill."], "minutes": 10, "cook_minutes": 0, "oven": False, "ahead_days": 0},
    ]

    result = tools.answer_holiday(tg, "hosting", headcount=5, guest_notes="Aunt May can’t have nuts")

    names = [d["name"] for d in tools.get_big_meal(tg)["dishes"]]
    assert "Walnut Salad" not in names, "the guest's note is a restriction, not a suggestion"
    assert "Shrimp Cocktail" not in names, "the household's own allergy still guards the table"
    dropped = {d["name"]: d["restriction"] for d in result["big_meal"]["dropped"]}
    assert dropped == {"Walnut Salad": "Aunt May can’t have nuts", "Shrimp Cocktail": "shellfish allergy"}
    assert "Aunt May can’t have nuts" in proposer["context"]["guest_notes"]
    assert any("shellfish" in r for r in proposer["context"]["dietary_restrictions"])


def test_a_new_guest_note_on_a_standing_menu_drops_the_clashing_dish_and_proposes_another(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert "pumpkin puree" in _list()
    proposer["calls"] = 0
    proposer["answer"]["dishes"] = [
        {"name": "Apple Crumble", "role": "sweet", "covers": [],
         "ingredients": [{"item": "apples", "qty": "8", "category": "produce"}],
         "instructions": ["Bake."], "minutes": 15, "cook_minutes": 40, "oven": True, "ahead_days": 1},
    ]

    result = tools.answer_holiday(tg, "hosting", guest_notes="no pumpkin")

    assert result["big_meal"]["menu"] == "refreshed", "the menu follows the note in place — never thrown away"
    assert result["headcount"] == 5, "the count is inherited within the same answer"
    assert result["big_meal"]["dropped"] == [{"name": "Pumpkin Pie", "restriction": "no pumpkin", "role": "sweet"}]
    assert result["big_meal"]["replaced"] == ["Apple Crumble"]
    assert result["big_meal_said"] == "Left off the pumpkin pie — no pumpkin. Added apple crumble instead."
    assert proposer["calls"] == 1 and proposer["context"]["avoid"][0]["name"] == "Pumpkin Pie"
    assert proposer["context"]["want_sweet"] is True and proposer["context"]["side_count"] == 0
    assert "Sage and Onion Stuffing" in proposer["context"]["already_on_the_menu"]
    names = [d["name"] for d in tools.get_big_meal(tg)["dishes"]]
    assert "Apple Crumble" in names and "Pumpkin Pie" not in names
    items = _list()
    assert "apples" in items and "pumpkin puree" not in items, "the shopping followed"


def test_the_planner_builds_the_menu_when_the_week_is_generated(family, recipes, proposer, stub_model):
    tg = _thanksgiving()
    tools.answer_holiday(tg, "hosting", headcount=3, on_table_at="18:00")
    assert tools.get_big_meal(tg)["status"] == "none", "no week yet — nothing to build into"
    seen = stub_model(_full_week(tg, meal="Chili"))

    plan = agent.generate_weekly_plan(tg)

    line = seen["context"]["holidays"][0]
    assert line["answer"] == "hosting" and line["extra_guests"] == 3 and line["on_table_at"] == "18:00"
    assert "send the MAIN" in line["plan"]
    row = _entry(plan["weekly_plan_id"], tg)
    assert row["recipe_name"] == "Chili", "built around the dinner the model chose for the day"
    assert json.loads(row["derived_from_json"])["holiday_menu"] is True
    assert len(_sides(row)) == 4
    assert tools.get_big_meal(tg)["status"] == "full"


# ---------- the shop split ----------

def test_the_shopping_reads_in_two_trips_keeps_well_first(family, recipes, proposer, signed_in):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)

    items = _list()
    for name in ("whole chicken", "bread", "onions", "carrots", "maple syrup", "green beans", "pumpkin puree", "frozen pie shells"):
        assert name in items, f"{name} should be on the list"
    # Each dish was written for the table of seven, so it's bought ONCE for
    # seven — not scaled up again by the guests attendance already counts.
    assert items["bread"] == "2 loaves" and items["pumpkin puree"] == "2 cans" and items["onions"] == "3", items
    assert items["whole chicken"] == "4", "the main recipe was written for 2, so 7 eaters buys 3.5 → 4 — the usual scaling"
    split = tools.big_meal_shop_split(today=date.fromisoformat(_shift(tg, -10)))
    by_id = {i["id"]: i["item"] for i in tools.list_grocery_list()}
    early = {by_id[i] for i in split["early"]["item_ids"]}
    fresh = {by_id[i] for i in split["fresh"]["item_ids"]}
    assert early == {"bread", "maple syrup", "pumpkin puree", "frozen pie shells"}
    assert fresh == {"whole chicken", "onions", "carrots", "green beans"}
    assert split["early"]["date"] == _shift(tg, -3) and split["fresh"]["date"] == _shift(tg, -1)
    assert split["early"]["label"] == f"For Thanksgiving — buy by {date.fromisoformat(_shift(tg, -3)).strftime('%A')}"
    assert split["fresh"]["label"] == f"For Thanksgiving — buy fresh on {date.fromisoformat(_shift(tg, -1)).strftime('%A')}"

    # The Shop screen's payloads carry it: a timing on every menu line and the two trips.
    payload = signed_in.get("/api/grocery-list/by-store").json()
    assert payload["shop_split"]["holiday_name"] == "Thanksgiving"
    timings = {it["item"]: it.get("shop_timing") for s in payload["stores"] for sec in s["sections"] for it in sec["items"]}
    assert timings["bread"] == "early" and timings["carrots"] == "fresh"
    flat = signed_in.get("/api/grocery-list").json()
    assert flat["shop_split"]["fresh"]["date"] == _shift(tg, -1)


def test_a_line_another_meal_needs_first_goes_on_the_early_trip(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    # A soup planned five days before the holiday wants onions too.
    tools.add_recipe("Onion Soup", ingredients=[{"item": "onions", "qty": "2", "category": "produce"}], default_servings=2)
    early_plan = tools.create_weekly_plan(_shift(tg, -7))["weekly_plan_id"]
    tools.plan_meal(_shift(tg, -5), "Onion Soup", slot="dinner", weekly_plan_id=early_plan)
    tools.approve_weekly_plan(early_plan, approved_by="Emily")
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.answer_holiday(tg, "hosting", headcount=5)

    split = tools.big_meal_shop_split(today=date.fromisoformat(_shift(tg, -10)))
    by_id = {i["id"]: i["item"] for i in tools.list_grocery_list()}
    assert "onions" in {by_id[i] for i in split["early"]["item_ids"]}, "needed on the 5th anyway"


def test_once_the_early_trip_is_past_there_is_one_shop_and_nothing_to_split(family, recipes, proposer, signed_in):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)
    split = tools.big_meal_shop_split(today=date.fromisoformat(_shift(tg, -1)))
    assert split["early"]["date"] is None and split["early"]["item_ids"] == []
    assert len(split["fresh"]["item_ids"]) == 8 and split["fresh"]["label"] == "For Thanksgiving — buy fresh today"
    # No hosted holiday ahead at all: the payload says so plainly.
    tools.answer_holiday(tg, "just_us")
    assert signed_in.get("/api/grocery-list/by-store").json()["shop_split"] is None


# ---------- the prep spread ----------

def test_make_ahead_work_lands_on_the_days_before_through_today_moves(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert _holiday_tasks() == [], "a draft is a proposal: nothing on Now until the week is approved"
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    tasks = _holiday_tasks()
    by_day = {}
    for t in tasks:
        by_day.setdefault(t["task_date"], []).append(t["description"])
    weekday = date.fromisoformat(tg).strftime("%A")
    assert by_day[_shift(tg, -1)] == [
        f"Make the stuffing — for the big meal on {weekday}.",
        f"Bake the pumpkin pie — for the big meal on {weekday}.",
        "The fresh shop for Thanksgiving — produce, dairy, meat and fish.",
    ]
    assert all(t["related_meal"] == "Shop" for t in tasks if "shop" in t["description"].lower())
    assert by_day[_shift(tg, -3)] == ["The keeps-well shop for Thanksgiving — pantry and freezer things, so the fresh trip stays short."]
    assert tg not in by_day, "the day itself is the timeline, not a prep task"
    entry = _entry(plan_id, tg)
    assert all(t["meal_plan_entry_id"] == entry["id"] for t in tasks), "every row is tied to the dinner, so it can be found and taken back"

    # Now, the day before: the pieces are prep moves, tickable like any other.
    moves = tools.today_moves(_shift(tg, -1), now=datetime.combine(date.fromisoformat(_shift(tg, -1)), datetime.min.time()))
    prep = [m for m in moves["moves"] if m["kind"] == "prep"]
    assert [m["title"] for m in prep] == ["Make the stuffing", "Bake the pumpkin pie"]
    shop = [m for m in moves["moves"] if m["kind"] == "shop"]
    assert [m["title"] for m in shop] == ["The fresh shop for Thanksgiving"], "the shop reads as a shop, not a prep"
    assert shop[0]["tickable"] and shop[0]["id"].startswith("prep:")
    assert prep[0]["reason"] == f"for the big meal on {weekday}"
    assert prep[0]["tickable"] and prep[0]["action"]["target"]["kind"] == "check_prep"
    tools.set_move_done(prep[0]["id"], True)
    assert [t for t in _holiday_tasks() if t["description"].startswith("Make the stuffing")][0]["status"] == "done"


def test_the_days_before_a_monday_holiday_belong_to_the_week_before_and_still_show(family, recipes, proposer):
    tg = _thanksgiving()  # a Monday, always
    assert date.fromisoformat(tg).weekday() == 0
    previous = tools.create_weekly_plan(_shift(tg, -7))["weekly_plan_id"]
    tools.plan_meal(_shift(tg, -1), "Chili", slot="dinner", weekly_plan_id=previous)
    plan_id = _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)

    sunday_tasks = [t for t in _holiday_tasks() if t["task_date"] == _shift(tg, -1)]
    assert sunday_tasks and all(t["weekly_plan_id"] == previous for t in sunday_tasks), "dated into the plan that holds Sunday"
    assert {t["description"] for t in tools.get_prep_schedule(previous)} >= {t["description"] for t in sunday_tasks}
    # And the holiday's own plan reads them too, since they're its dinner's.
    assert {t["description"] for t in tools.get_prep_schedule(plan_id)} >= {t["description"] for t in sunday_tasks}


def test_a_standing_prep_day_in_the_window_takes_the_make_ahead_work(family, recipes, proposer):
    tg = _thanksgiving()
    saturday = _shift(tg, -2)
    tools.set_prep_days([{"weekday": date.fromisoformat(saturday).strftime("%A").lower(), "minutes": 90}])
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)

    by_day = {}
    for t in _holiday_tasks():
        by_day.setdefault(t["task_date"], []).append(t["description"])
    assert any(d.startswith("Make the stuffing") for d in by_day[saturday])
    assert any(d.startswith("Bake the pumpkin pie") for d in by_day[saturday])
    assert not any(d.startswith("Make the stuffing") for d in by_day.get(_shift(tg, -1), []))


def test_the_default_make_ahead_rule_is_small_and_readable():
    assert bm.default_ahead_days("Cranberry sauce", "side") == 1
    assert bm.default_ahead_days("Sage stuffing", "side") == 1
    assert bm.default_ahead_days("Green salad", "side") == 0
    assert bm.default_ahead_days("Roasted squash", "side") == 0
    assert bm.default_ahead_days("Apple crumble", "sweet") == 1
    assert bm.default_ahead_days("Fruit platter", "sweet") == 1, "a sweet keeps unless it says otherwise"
    assert bm.default_ahead_days("Buttered peas", "side") == 0


# ---------- the day of ----------

def test_the_timeline_works_back_from_the_time_they_said(family, recipes, proposer):
    tg = _thanksgiving()
    tools.create_weekly_plan(tg)
    tools.answer_holiday(tg, "hosting", headcount=4, on_table_at="17:00")

    tl = tools.big_meal_timeline(tg)
    assert tl["on_table_say"] == "5:00 pm" and tl["on_table_default"] is False
    steps = [(s["say"], s["step"]) for s in tl["steps"]]
    assert steps == [
        ("1:00 pm", "Start on the herb roast turkey"),
        ("1:30 pm", "Herb Roast Turkey into the oven"),
        ("4:25 pm", "Prep the maple roasted carrots"),
        ("4:30 pm", "Herb Roast Turkey out to rest"),
        ("4:30 pm", "Warm the sage and onion stuffing through"),        # made ahead; warms in the rest window
        ("4:35 pm", "Maple Roasted Carrots into the oven"),            # 25 min, fits the rest window
        ("4:40 pm", "Start on the green beans with lemon"),           # stovetop, 15 + 5
        ("4:45 pm", "Out of the fridge: the pumpkin pie"),            # made ahead, no oven needed
        ("5:00 pm", "On the table"),
    ]
    assert tl["spoken"].startswith("Working back from 5:00 pm: 1:00 pm — Start on the herb roast turkey; 1:30 pm — Herb Roast Turkey into the oven")
    assert tl["start_say"] == "1:00 pm"


def test_an_oven_dish_that_does_not_fit_the_rest_goes_in_before_the_main(family, recipes, proposer):
    tg = _thanksgiving()
    tools.create_weekly_plan(tg)
    proposer["answer"]["dishes"] = [
        {"name": "Scalloped Potatoes", "role": "side", "covers": ["carb"],
         "ingredients": [{"item": "potatoes", "qty": "5 lb", "category": "produce"}],
         "instructions": ["Layer.", "Bake."], "minutes": 20, "cook_minutes": 75, "oven": True, "ahead_days": 0},
    ]
    tools.answer_holiday(tg, "hosting", headcount=4, on_table_at="5pm")

    steps = [(s["say"], s["step"]) for s in tools.big_meal_timeline(tg)["steps"]]
    assert ("3:45 pm", "Scalloped Potatoes into the oven (with the main)") in steps, "too long for the rest window: the second rack"
    assert ("3:25 pm", "Prep the scalloped potatoes") in steps
    assert steps[-1] == ("5:00 pm", "On the table")


def test_no_time_given_means_the_households_own_dinner_clock(family, recipes, proposer):
    tg = _thanksgiving()
    tools.set_dinner_window("6_8")
    tools.create_weekly_plan(tg)
    tools.answer_holiday(tg, "hosting", headcount=4)
    from app.tools import moves
    tl = tools.big_meal_timeline(tg)
    assert tl["on_table_default"] is True
    assert tl["on_table_at"] == moves._dinner_clock().strftime("%H:%M")
    assert "your usual dinner time" in tl["spoken"]


def test_a_time_that_cannot_be_read_is_refused_not_guessed(family):
    tg = _thanksgiving()
    with pytest.raises(ValueError):
        tools.answer_holiday(tg, "hosting", headcount=2, on_table_at="dinnerish")
    assert bm.normalise_on_table_at("5:30 pm") == "17:30"
    assert bm.normalise_on_table_at("12 pm") == "12:00"
    assert bm.normalise_on_table_at("") == ""


def test_get_big_meal_reads_it_all_back_in_plain_words(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5, on_table_at="17:00")

    info = tools.get_big_meal(tg)
    assert info["hosting"] and info["eaters"] == 7 and info["status"] == "full"
    assert [d["role"] for d in info["dishes"]] == ["main", "side", "side", "side", "sweet"]
    assert info["dishes"][1]["made_ahead_on"] == _shift(tg, -1)
    assert info["timeline"]["on_table_say"] == "5:00 pm"
    assert info["spoken"].startswith("Thanksgiving, 7 at the table. The main is Roast Chicken, with Sage and Onion Stuffing, Maple Roasted Carrots and Green Beans with Lemon, and Pumpkin Pie for after.")
    assert "start around" in info["spoken"] and "5:00 pm on the table" in info["spoken"]
    # Not hosting: it says so, and says how.
    tools.answer_holiday(tg, "just_us")
    assert tools.get_big_meal(tg) == {"date": tg, "hosting": False,
                                      "spoken": "You haven’t said you’re hosting Thanksgiving. Say so and I’ll plan the big meal."}


# ---------- degrading, never crashing ----------

def test_no_model_means_the_main_stays_and_the_night_says_why(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    proposer["answer"]["raise"] = RuntimeError("no API key")

    result = tools.answer_holiday(tg, "hosting", headcount=5)

    assert result["big_meal"]["status"] == "main_only"
    assert result["big_meal"]["note"].startswith("I’ve got the main. I couldn’t put the sides together just now")
    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Roast Chicken" and row["slot_state"] == "planned" and _sides(row) == []
    assert tools.get_big_meal(tg)["status"] == "main_only"


def test_no_model_and_no_main_hands_the_night_back_as_a_question_naming_the_reason(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    proposer["answer"]["raise"] = RuntimeError("no API key")

    result = tools.answer_holiday(tg, "hosting", headcount=5)

    assert result["big_meal"]["status"] == "none"
    row = _entry(plan_id, tg)
    assert row["slot_state"] == "open"
    assert row["open_reason"] == "You’re hosting Thanksgiving for 7 — what’s the main? Tell me and I’ll build the rest around it."
    assert "Tell me the main" in tools.get_big_meal(tg)["spoken"]


def test_a_malformed_proposal_degrades_the_same_way(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken")
    proposer["answer"]["dishes"] = [{"name": "", "ingredients": []}, "not a dish", {"name": "Air", "ingredients": []}]
    result = tools.answer_holiday(tg, "hosting", headcount=5)
    assert result["big_meal"]["status"] == "main_only"


# ---------- unwinding ----------

def test_changing_the_answer_away_from_hosting_unwinds_everything_once(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert "bread" in _list() and "whole chicken" in _list() and _holiday_tasks()

    tools.answer_holiday(tg, "out")

    row = _entry(plan_id, tg)
    assert row["slot_state"] == "planned_empty", "out: nobody home, exactly as slice 1 does it"
    assert _sides(row) == []
    items = _list()
    assert "bread" not in items and "pumpkin puree" not in items and "whole chicken" not in items
    assert _holiday_tasks() == []
    assert tools.get_holiday_answer(tg)["headcount"] == 0
    assert json.loads(bm._answer_row(tg)["menu_json"]) == {}
    assert tools.big_meal_shop_split(today=date.fromisoformat(_shift(tg, -10))) is None
    assert tools.audit_plan_slots(plan_id)["duplicated"] == []


def test_hosting_then_just_us_hands_the_dinner_back_as_a_question(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert _entry(plan_id, tg)["recipe_name"] == "Herb Roast Turkey"

    tools.answer_holiday(tg, "just_us")

    row = _entry(plan_id, tg)
    assert row["slot_state"] == "open" and row["open_reason"] == "Plans for Thanksgiving changed — what would you like for dinner?"
    assert _holiday_tasks() == []
    assert tools.get_slot_attendance(tg, "dinner")["guest_count"] == 0
    # And the household's own recipe is still theirs: nothing deletes a saved recipe.
    assert tools.get_recipe("Herb Roast Turkey")["name"] == "Herb Roast Turkey"


def test_a_dinner_planned_by_hand_after_hosting_is_not_ours_to_take_back(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    tools.answer_holiday(tg, "hosting", headcount=5)
    # The household replaces the whole dinner themselves, outside the menu.
    tools.clear_plan_slot(plan_id, tg, "dinner")
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=plan_id)

    tools.answer_holiday(tg, "just_us")

    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Chili" and row["slot_state"] == "planned", "left exactly as they planned it"
    assert _holiday_tasks() == []


def test_leftovers_from_the_big_meal_chain_into_the_days_after_as_normal(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    tools.plan_meal(_shift(tg, 1), "Roast Chicken", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{tg}:dinner"})
    tools.repair_leftover_chains(plan_id)
    tools.answer_holiday(tg, "hosting", headcount=5)

    menu = tools.get_week_menu(plan_id)
    by_date = {d["date"]: d for d in menu["days"]}
    assert by_date[tg]["dinner"]["title"] == "Roast Chicken" and len(_sides(_entry(plan_id, tg))) == 4
    reheat = by_date[_shift(tg, 1)]["dinner"]
    assert reheat.get("is_leftovers") or "leftover" in json.dumps(reheat).lower()
    # The cook view still sees one dinner that night and one reheat the next.
    view = tools.get_cooker_view(plan_id)
    kinds = {(m["date"], bool(m.get("is_leftovers"))) for m in view["meals"] if m["date"] in (tg, _shift(tg, 1))}
    assert kinds == {(tg, False), (_shift(tg, 1), True)}


# ---------- changing it in chat ----------

def test_swap_the_dessert_and_drop_a_side(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)

    result = tools.set_big_meal_dish(
        tg, "Apple Crumble", role="sweet",
        ingredients=[{"item": "apples", "qty": "8", "category": "produce"}, {"item": "oats", "qty": "1 bag", "category": "pantry"}],
        instructions=["Slice.", "Bake."], minutes=15, cook_minutes=40, oven=True, ahead_days=1,
    )
    assert result["changed"] == "replaced" and result["replaced"] == "Pumpkin Pie"
    names = [d["name"] for d in tools.get_big_meal(tg)["dishes"]]
    assert "Apple Crumble" in names and "Pumpkin Pie" not in names
    items = _list()
    assert "apples" in items and "pumpkin puree" not in items, "the shopping followed the swap"
    assert any(t["description"].startswith("Make the apple crumble") for t in _holiday_tasks())

    removed = tools.remove_big_meal_dish(tg, "Green Beans with Lemon")
    assert removed["removed"] == "Green Beans with Lemon"
    assert "green beans" not in _list()
    assert len(_sides(_entry(plan_id, tg))) == 3
    with pytest.raises(ValueError):
        tools.remove_big_meal_dish(tg, "Roast Chicken")


def test_a_dish_that_clashes_with_a_guests_note_is_refused(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken")
    tools.answer_holiday(tg, "hosting", headcount=5, guest_notes="no nuts")
    with pytest.raises(ValueError, match="clashes with no nuts"):
        tools.set_big_meal_dish(tg, "Pecan Pie", role="sweet",
                                ingredients=[{"item": "pecans", "qty": "2 cups", "category": "pantry"}])


def test_make_the_stuffing_two_days_before_moves_its_prep(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)

    result = tools.set_big_meal_prep_day(tg, "Sage and Onion Stuffing", "two_days_before")

    assert result["ahead_days"] == 2
    stuffing = [t for t in _holiday_tasks() if t["description"].startswith("Make the stuffing")]
    assert [t["task_date"] for t in stuffing] == [_shift(tg, -2)]
    # And back on the day: no prep row at all, the timeline does it fresh.
    tools.set_big_meal_prep_day(tg, "Sage and Onion Stuffing", "day_of")
    assert not [t for t in _holiday_tasks() if t["description"].startswith("Make the stuffing")]
    assert any(s["step"].startswith("Sage and Onion Stuffing into the oven") for s in tools.big_meal_timeline(tg)["steps"])
    with pytest.raises(ValueError):
        tools.set_big_meal_prep_day(tg, "Sage and Onion Stuffing", _shift(tg, -5))


def test_swapping_the_main_keeps_the_sides(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5, on_table_at="5pm")

    result = tools.set_big_meal_dish(
        tg, "Glazed Ham", role="main",
        ingredients=[{"item": "ham", "qty": "8 lb", "category": "meat/seafood"}],
        instructions=["Glaze.", "Bake."], minutes=15, cook_minutes=120,
    )
    assert result["changed"] == "main"
    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Glazed Ham" and json.loads(row["derived_from_json"])["holiday_menu"] is True
    assert [s["name"] for s in _sides(row)] == ["Sage and Onion Stuffing", "Maple Roasted Carrots", "Green Beans with Lemon", "Pumpkin Pie"]
    items = _list()
    assert "ham" in items and "whole chicken" not in items and "bread" in items
    steps = [(s["say"], s["step"]) for s in tools.big_meal_timeline(tg)["steps"]]
    assert steps[0] == ("2:45 pm", "Start on the glazed ham"), "a ham with no rest: 120 minutes back from five, prep before"
    assert ("4:35 pm", "Maple Roasted Carrots into the oven (with the main)") in steps, "no rest window, so the second rack"


def test_propose_again_keeps_the_main_and_replaces_the_rest(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)
    proposer["answer"]["dishes"] = [DISHES[1]]

    result = tools.propose_big_meal(tg)

    assert result["proposed"] == 1
    assert [d["name"] for d in result["dishes"]] == ["Roast Chicken", "Maple Roasted Carrots"]
    assert "bread" not in _list() and "carrots" in _list()


def test_the_big_meal_tools_are_offered_to_chat_and_refresh_the_plan():
    from app import main as main_mod
    names = {d["name"] for d in agent.TOOL_DEFINITIONS}
    for name in ("get_big_meal", "set_big_meal_dish", "remove_big_meal_dish", "set_big_meal_prep_day", "propose_big_meal"):
        assert name in names and name in agent.TOOL_FUNCTIONS
        if name != "get_big_meal":
            assert main_mod._categorize_tool(name) == ("week", "week", None)
    assert main_mod._categorize_tool("answer_holiday") == ("week", "week", None)
    schema = next(d for d in agent.TOOL_DEFINITIONS if d["name"] == "answer_holiday")
    assert {"on_table_at", "guest_notes"} <= set(schema["input_schema"]["properties"])
    assert "event mode" not in json.dumps([d for d in agent.TOOL_DEFINITIONS if "big_meal" in d["name"]]).lower().replace('never "event mode"', "")


def test_the_route_takes_the_time_and_the_guests_notes(signed_in, family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken")
    res = signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "headcount": 3,
                                                       "on_table_at": "6pm", "guest_notes": "Sam’s vegetarian"})
    assert res.status_code == 200
    body = res.json()
    assert body["on_table_at"] == "18:00" and body["guest_notes"] == "Sam’s vegetarian"
    assert body["big_meal"]["status"] == "full"
    bad = signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "on_table_at": "soonish"})
    assert bad.status_code == 400


def test_the_copy_never_says_event_mode():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    for rel in ("app/tools/big_meal.py", "app/tools/holidays.py", "static/plan-week.html", "static/shell.js"):
        text = (repo / rel).read_text(encoding="utf-8").lower()
        assert "event mode" not in text.replace('never "event mode"', "").replace("never \\\"event mode\\\"", ""), rel


# ---------- the verifier's catches (2026-09-13) ----------

def test_a_trip_already_covering_the_day_builds_nothing(family, recipes, proposer):
    """Slice 1's rule — the trip wins — is the menu's too: a hosting answer
    on a night nobody is home records the count and builds NOTHING: no
    turkey into an empty house, no shopping, no prep."""
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Chili", approve=True)
    tools.set_away_stretch(_shift(tg, -1), "dinner", _shift(tg, 1), "lunch")
    row_before = dict(_entry(plan_id, tg))
    need_before = tools.get_slot_need(tg, "dinner")
    assert row_before["slot_state"] == "planned_empty" and need_before["away_stretch_id"]
    list_before = _list()

    result = tools.answer_holiday(tg, "hosting", headcount=4)

    assert result["hosting"] == "already_out" and result["big_meal"]["menu"] == "trip"
    assert proposer["calls"] == 0, "nothing was even proposed"
    assert dict(_entry(plan_id, tg)) == row_before, "the away placeholder is untouched"
    assert tools.get_slot_need(tg, "dinner") == need_before
    assert _list() == list_before and _holiday_tasks() == []
    assert tools.get_holiday_answer(tg)["headcount"] == 4, "the count is kept for when the trip changes"
    assert "away over Thanksgiving" in result["big_meal_said"]
    tools.answer_holiday(tg, "just_us")
    assert tools.get_slot_need(tg, "dinner") == need_before


def test_an_adopted_dinner_survives_leaving_hosting(family, recipes, proposer):
    """Regression vs slice 1: the household's own Roast Chicken was adopted
    as the main; leaving hosting gives it back as it was — sides off, the
    sides' shopping off, the dinner and its own shopping kept."""
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Roast Chicken", slot="dinner", weekly_plan_id=plan_id, reasoning="Emily’s pick for a Monday.")
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert "bread" in _list() and _list()["whole chicken"] == "4"

    tools.answer_holiday(tg, "just_us")

    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Roast Chicken" and row["slot_state"] == "planned", "their dinner, still theirs"
    assert _sides(row) == [] and "holiday_menu" not in json.loads(row["derived_from_json"])
    assert row["reasoning"] == "Emily’s pick for a Monday.", "its own reasoning back"
    items = _list()
    assert "whole chicken" in items and "bread" not in items and "pumpkin puree" not in items
    assert items["whole chicken"] == "1", "re-bought for the household alone, the guests gone"
    assert _holiday_tasks() == [] and json.loads(bm._answer_row(tg)["menu_json"]) == {}


def test_a_proposed_main_is_still_handed_back_as_a_question(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert _entry(plan_id, tg)["recipe_name"] == "Herb Roast Turkey"
    tools.answer_holiday(tg, "out")
    assert _entry(plan_id, tg)["slot_state"] == "planned_empty"


def test_a_bigger_table_rescales_the_whole_shop(family, recipes, proposer):
    """7 → 22 at the table: every line follows, the main by attendance and
    each dish by the count it was written for — through the answer AND
    through a bare set_guest_count (the intake's steppers)."""
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)
    before = _list()
    assert before["bread"] == "2 loaves" and before["whole chicken"] == "4"

    result = tools.answer_holiday(tg, "hosting", headcount=20)
    assert result["big_meal"]["menu"] == "refreshed" and result["big_meal"]["eaters"] == 22
    items = _list()
    assert items["bread"] == "7 loaves" and items["whole chicken"] == "11", items
    sides = _sides(bm.menu_entry(tg))
    assert all(s["servings"] == 7 for s in sides), "a dish keeps the count it was WRITTEN for; the table scales it"

    tools.set_guest_count(tg, "dinner", 5)
    items = _list()
    assert items["bread"] == "2 loaves" and items["whole chicken"] == "4", "the steppers reach the shop too"


def test_the_proposed_main_is_checked_against_the_table(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.add_fact("household", "No shellfish — Emily is allergic", hard=True)
    shrimp = {**MAIN, "name": "Shrimp Boil", "ingredients": [{"item": "shrimp", "qty": "5 lb", "category": "meat/seafood"}]}
    proposer["answer"]["main"] = shrimp
    calls = {"n": 0}
    original = agent.generate_big_meal_llm

    def _second_try(context):
        calls["n"] += 1
        if context.get("avoid"):
            assert context["avoid"][0]["name"] == "Shrimp Boil"
            return {"main": MAIN, "dishes": DISHES}
        return original(context)
    agent.generate_big_meal_llm = _second_try

    result = tools.answer_holiday(tg, "hosting", headcount=5)

    assert calls["n"] == 2, "one more try, with the clash named"
    row = _entry(plan_id, tg)
    assert row["recipe_name"] == "Herb Roast Turkey" and "shrimp" not in _list()
    assert result["big_meal"]["dropped"][0]["name"] == "Shrimp Boil"
    assert result["big_meal_said"].startswith("Left off the shrimp boil — ")


def test_a_main_that_clashes_twice_leaves_the_night_open_with_the_reason(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    proposer["answer"]["main"] = {**MAIN, "name": "Shrimp Boil", "ingredients": [{"item": "shrimp", "qty": "5 lb", "category": "meat/seafood"}]}
    result = tools.answer_holiday(tg, "hosting", headcount=5, guest_notes="no shellfish")
    assert result["big_meal"]["status"] == "none"
    row = _entry(plan_id, tg)
    assert row["slot_state"] == "open" and "clashed with no shellfish" in row["open_reason"]
    assert "shrimp" not in _list()


def test_an_adopted_main_that_clashes_is_said_not_touched(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    result = tools.answer_holiday(tg, "hosting", headcount=5, guest_notes="Priya can’t have chicken")
    assert _entry(plan_id, tg)["recipe_name"] == "Roast Chicken"
    assert result["big_meal"]["conflicts"][0]["dish"] == "Roast Chicken"
    assert result["big_meal_said"] == "Heads up: Roast Chicken has Priya can’t have chicken in it — your call."


def test_a_hand_swap_of_the_dinner_leaves_no_orphan_prep(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert len(_holiday_tasks()) == 4

    tools.swap_meal_in_plan(plan_id, tg, "Chili")

    sunday = tools.today_moves(_shift(tg, -1), now=datetime.combine(date.fromisoformat(_shift(tg, -1)), datetime.min.time()))
    assert not [m for m in sunday["moves"] if "stuffing" in m["title"].lower()], "no reminder for a menu that no longer exists"
    assert not [t for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "holiday"]
    assert bm.menu_entry(tg) is None and json.loads(bm._answer_row(tg)["menu_json"]) == {}
    # And clear_plan_slot, which this module uses itself, cleans as it goes.
    tools.answer_holiday(tg, "hosting", headcount=5)
    tools.clear_plan_slot(plan_id, tg, "dinner")
    assert _holiday_tasks() == []


def test_a_retired_plans_prep_does_not_surface_in_the_plan_that_replaced_it(family, recipes):
    tg = _thanksgiving()
    old = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Chili", slot="dinner", weekly_plan_id=old)
    tools.save_prep_tasks(old, [{"task_date": tg, "description": "Chop the onions — for the chili.", "related_meal": "Chili"}])
    new = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.retire_overlapping_plans(new, tg, 7)
    tools.plan_meal(tg, "Roast Chicken", slot="dinner", weekly_plan_id=new)
    assert tools.get_prep_schedule(new) == []
    assert tools.get_cooker_view(new)["prep_total"] == 0


def test_a_step_before_the_day_is_said_as_the_evening_before(family, recipes, proposer):
    tg = _thanksgiving()
    tools.create_weekly_plan(tg)
    proposer["answer"]["main"] = {**MAIN, "name": "Slow Brisket", "prep_minutes": 30, "cook_minutes": 600, "rest_minutes": 30}
    tools.answer_holiday(tg, "hosting", headcount=4, on_table_at="10am")
    tl = tools.big_meal_timeline(tg)
    first = tl["steps"][0]
    assert first["say"] == "the evening before, 11:00 pm" and first["day_before"] is True
    assert first["step"] == "Start on the slow brisket"
    ats = [s["at"] for s in tl["steps"]]
    assert ats == sorted(ats), "ordered by the real clock, not the printed one"
    assert tl["steps"][-1]["say"] == "10:00 am"


def test_a_bare_hour_on_a_dinner_reads_as_the_evening():
    assert bm.normalise_on_table_at("6") == "18:00"
    assert bm.normalise_on_table_at("12") == "12:00"
    assert bm.normalise_on_table_at("6am") == "06:00"
    assert bm.normalise_on_table_at("6:00") == "06:00", "a colon is taken as written"
    assert bm.normalise_on_table_at("5 p.m.") == "17:00"


def test_a_guest_note_is_kept_to_one_line(family):
    tg = _thanksgiving()
    with pytest.raises(ValueError, match="under 160"):
        tools.answer_holiday(tg, "hosting", headcount=2, guest_notes="x" * 161)


def test_chicken_and_thyme_with_no_section_are_fresh_and_saturdays_onions_go_early(family, proposer):
    tg = _thanksgiving()
    # A recipe that named no sections at all: everything lands in "other".
    tools.add_recipe("Roast Chicken", ingredients=[{"item": "whole chicken", "qty": "1"}, {"item": "fresh thyme", "qty": "1 bunch"},
                                                   {"item": "foil", "qty": "1 roll"}], default_servings=2)
    tools.add_recipe("Onion Soup", ingredients=[{"item": "onions", "qty": "2", "category": "produce"}], default_servings=2)
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    earlier = tools.create_weekly_plan(_shift(tg, -7))["weekly_plan_id"]
    tools.plan_meal(_shift(tg, -2), "Onion Soup", slot="dinner", weekly_plan_id=earlier)  # the Saturday between the trips
    tools.approve_weekly_plan(earlier, approved_by="Emily")
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.answer_holiday(tg, "hosting", headcount=5)

    split = tools.big_meal_shop_split(today=date.fromisoformat(_shift(tg, -10)))
    by_id = {i["id"]: i["item"] for i in tools.list_grocery_list()}
    early = {by_id[i] for i in split["early"]["item_ids"]}
    fresh = {by_id[i] for i in split["fresh"]["item_ids"]}
    assert {"whole chicken", "fresh thyme"} <= fresh, "raw poultry and fresh herbs don't keep, whatever section they landed in"
    assert "foil" in early
    assert "onions" in early, "needed on Saturday, before the fresh trip — bought on the early one"
    assert bm.keeps("candles", "other") and not bm.keeps("salmon fillets", "other") and bm.keeps("flour", "pantry")


def test_now_asks_for_the_shop_once_on_the_day_of_the_fresh_trip(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.answer_holiday(tg, "hosting", headcount=5)
    sunday = _shift(tg, -1)
    now = datetime.combine(date.fromisoformat(sunday), datetime.min.time().replace(hour=9))
    moves = tools.today_moves(sunday, now=now)["moves"]
    shops = [m for m in moves if m["kind"] == "shop"]
    assert len(shops) == 1, [m["title"] for m in shops]
    assert shops[0]["title"] == "The fresh shop for Thanksgiving"
    assert shops[0]["detail"].startswith(str(len(tools.list_grocery_list()))) and "item" in shops[0]["detail"]


def test_a_reheat_night_buys_nothing_new_for_the_sides(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    tools.plan_meal(_shift(tg, 1), "Roast Chicken", slot="dinner", weekly_plan_id=plan_id, derived_from={"links_to": f"{tg}:dinner"})
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.answer_holiday(tg, "hosting", headcount=5)
    items = _list()
    assert items["frozen pie shells"] == "2" and items["pumpkin puree"] == "2 cans" and items["bread"] == "2 loaves"
    assert items["whole chicken"] == "5", "the MAIN still cooks for the reheat night (7 + 2 eaters, recipe for 2)"


def test_a_guests_note_matches_food_not_people(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken")
    proposer["answer"]["dishes"] = DISHES + [
        {"name": "Grandma’s Rolls", "role": "side", "covers": ["carb"],
         "ingredients": [{"item": "flour", "qty": "1 bag", "category": "pantry"}],
         "instructions": ["Bake."], "minutes": 20, "cook_minutes": 20, "oven": True, "ahead_days": 1},
        {"name": "Walnut Salad", "role": "side", "covers": ["vegetable"],
         "ingredients": [{"item": "walnuts", "qty": "1 cup", "category": "pantry"}],
         "instructions": ["Toss."], "minutes": 5, "cook_minutes": 0, "oven": False, "ahead_days": 0},
    ]
    result = tools.answer_holiday(tg, "hosting", headcount=5, guest_notes="no nuts for Grandma; Priya’s vegetarian")
    names = [d["name"] for d in tools.get_big_meal(tg)["dishes"]]
    assert "Grandma’s Rolls" in names, "a person's name is never a match term"
    assert "Walnut Salad" not in names
    assert bm._people_words("no nuts for Grandma; Priya’s vegetarian") == {"grandma", "priya", "emily", "vineeth"}


def test_a_dinner_moved_to_another_night_means_the_menu_is_gone_not_followed(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken", approve=True)
    tools.plan_meal(_shift(tg, 2), "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert len(_holiday_tasks()) == 4

    tools.swap_dinner_nights(plan_id, tg, _shift(tg, 2))

    assert bm.menu_entry(tg) is None and _holiday_tasks() == []
    moved = _entry(plan_id, _shift(tg, 2))
    assert moved["recipe_name"] == "Roast Chicken" and "holiday_menu" not in json.loads(moved["derived_from_json"])
    # "out" now empties the HOLIDAY's dinner, never the moved one.
    tools.answer_holiday(tg, "out")
    assert _entry(plan_id, tg)["slot_state"] == "planned_empty"
    assert _entry(plan_id, _shift(tg, 2))["recipe_name"] == "Roast Chicken" and _entry(plan_id, _shift(tg, 2))["slot_state"] == "planned"
    # Re-hosting builds one fresh menu on the holiday, not a second one.
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert _entry(plan_id, tg)["recipe_name"] == "Herb Roast Turkey"
    assert len(_sides(_entry(plan_id, _shift(tg, 2)))) == 4, "the moved dinner keeps what it carried"


def test_prep_waits_for_approval_and_lands_with_it(family, recipes, proposer):
    tg = _thanksgiving()
    plan_id = _week_with_dinner(tg, "Roast Chicken")
    tools.answer_holiday(tg, "hosting", headcount=5)
    assert _holiday_tasks() == [] and tools.get_big_meal(tg)["status"] == "full"
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert len(_holiday_tasks()) == 4 and "bread" in _list()


def test_the_days_screen_tap_records_and_the_save_builds(signed_in, family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken")
    tap = signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "build_menu": False}).json()
    assert tap["big_meal"]["menu"] == "deferred" and proposer["calls"] == 0
    assert tools.get_holiday_answer(tg)["answer"] == "hosting", "the answer itself is a fact, recorded on the tap"
    save = signed_in.post("/api/holidays/answer", json={"date": tg, "answer": "hosting", "headcount": 5,
                                                        "on_table_at": "17:00", "guest_notes": "no pumpkin"}).json()
    assert save["big_meal"]["menu"] == "built" and proposer["calls"] == 1
    assert proposer["context"]["eaters"] == 7 and proposer["context"]["guest_notes"] == "no pumpkin"
    assert save["big_meal_said"] == "Left off the pumpkin pie — no pumpkin."
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "static" / "plan-week.html").read_text(encoding="utf-8")
    assert "body.build_menu = false" in page and "holiday-said" in page and "big_meal_said" in page


def test_the_copy_has_no_raw_tokens(family, recipes, proposer):
    tg = _thanksgiving()
    _week_with_dinner(tg, "Roast Chicken")
    tools.answer_holiday(tg, "hosting", headcount=5)
    with pytest.raises(ValueError) as e:
        tools.set_big_meal_prep_day(tg, "Sage and Onion Stuffing", "whenever")
    assert "day_of" not in str(e.value) and "_" not in str(e.value)
    with pytest.raises(ValueError) as e:
        tools.remove_big_meal_dish(tg, "Roast Chicken")
    assert "set_big_meal_dish" not in str(e.value) and "role" not in str(e.value)


def test_a_misspelled_migration_table_still_fails_loudly():
    import sqlite3
    from app import db as db_mod
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    saved = db_mod._MIGRATIONS
    try:
        db_mod._MIGRATIONS = [("holiday_answerz", "on_table_at", "TEXT NOT NULL DEFAULT ''")]
        with pytest.raises(RuntimeError, match="holiday_answerz"):
            db_mod._run_migrations(conn)
    finally:
        db_mod._MIGRATIONS = saved
