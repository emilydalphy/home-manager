"""
Loop Board "Defrost check: ask at approval" (Emily, 2026-09-04, High).

Every test in tests/test_defrost.py exercises the INVENTORY-matched path:
a defrost task only ever appears there for a household that tracks a
freezer item, and inventory is deferred policy (2026-09-01) -- most
households never track one. This file covers the other half: asking
directly, once, right after the week is approved, and scheduling from the
plan's own recipes instead of from inventory at all. See
app/tools/defrost.py's "Ask instead of infer" section for the design note.
"""
import datetime

from app import tools
from app.tools import defrost


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _week_start(offset_weeks: int = 1) -> str:
    return (_monday() + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _meat_recipe(name="Chicken Skewers", item="Chicken Thighs", qty="1 lb"):
    tools.add_recipe(
        name,
        ingredients=[{"item": item, "qty": qty, "category": "meat/seafood"}],
        prep_time_minutes=10, cook_time_minutes=15,
    )


# ---------- meat_items_for_plan: the ask card's own chip list ----------

def test_meat_items_for_plan_lists_the_plans_own_meat_ingredient_with_its_night():
    _meat_recipe()
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    items = defrost.meat_items_for_plan(plan["weekly_plan_id"])

    assert len(items) == 1
    assert items[0]["item"] == "Chicken Thighs"
    assert len(items[0]["nights"]) == 1
    assert items[0]["nights"][0]["date"] == dates[3]
    assert items[0]["nights"][0]["meal"] == "Chicken Skewers"


def test_meat_items_for_plan_ignores_a_non_meat_ingredient():
    tools.add_recipe(
        "Veggie Bowl",
        ingredients=[{"item": "Rice", "qty": "2 cups", "category": "pantry"}],
    )
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Veggie Bowl", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    assert defrost.meat_items_for_plan(plan["weekly_plan_id"]) == []


def test_meat_items_for_plan_excludes_the_leftover_reheat_night():
    """A reheat night is not itself a 'feeds' night -- only the cook night
    is named, same reasoning _candidates_from_plan documents for the
    scheduled task itself."""
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[{"item": "Beef", "qty": "1 lb", "category": "meat/seafood"}],
        default_servings=3,
    )
    week = _week_start()
    dates = tools._week_dates(week)
    tue, thu = dates[1], dates[3]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tue, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.plan_meal(
        thu, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan["weekly_plan_id"],
        derived_from={"links_to": f"{tue}:dinner"},
    )
    tools.repair_leftover_chains(plan["weekly_plan_id"])

    items = defrost.meat_items_for_plan(plan["weekly_plan_id"])

    assert len(items) == 1
    assert [n["date"] for n in items[0]["nights"]] == [tue]


def test_meat_items_for_plan_is_empty_for_a_plan_with_no_meals():
    plan = tools.create_weekly_plan(_week_start())
    assert defrost.meat_items_for_plan(plan["weekly_plan_id"]) == []


def test_meat_items_for_plan_does_not_crash_for_a_component_based_plan(monkeypatch):
    """
    A component-based plan's entries are freeform component names (a
    protein/vegetable/carb build-your-own night) that usually have no
    saved recipe backing them at all -- meat_items_for_plan must simply
    find nothing there, not error.
    """
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    from app.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET planning_mode = 'component_based' WHERE id = ?", (plan["weekly_plan_id"],))
    conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, freeform_meal, weekly_plan_id, component_category) "
        "VALUES (1, ?, 'dinner', 'Grilled Chicken', ?, 'protein')",
        (tools._week_dates(week)[0], plan["weekly_plan_id"]),
    )
    conn.commit()
    conn.close()

    assert defrost.meat_items_for_plan(plan["weekly_plan_id"]) == []
    assert defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Grilled Chicken"]) == {"created": [], "notes": []}


# ---------- confirm_frozen_items: schedule from a household's own answer ----------

def test_confirming_a_frozen_item_creates_a_defrost_task_at_the_right_lead():
    _meat_recipe()
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    result = defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Chicken Thighs"])

    assert result["notes"] == []
    assert len(result["created"]) == 1
    created = result["created"][0]
    assert created["lead_tier"] == "standard"
    assert created["lead_hours"] == 24.0
    # No dinner_window set -> whole-day fallback, 24h rounds up to exactly
    # one day before the meal, same arithmetic as the inventory path.
    assert created["task_date"] == (datetime.date.fromisoformat(dates[3]) - datetime.timedelta(days=1)).isoformat()

    tasks = tools.get_prep_schedule(plan["weekly_plan_id"])
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "defrost"
    assert tasks[0]["inventory_item_id"] is None, "never written to inventory -- Emily's optional bonus stayed off"
    assert "Chicken Thighs" in tasks[0]["description"]


def test_confirming_two_items_uses_each_ones_own_lead_tier():
    tools.add_recipe(
        "Surf and Turf",
        ingredients=[
            {"item": "Whole Chicken", "qty": "1", "category": "meat/seafood"},
            {"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"},
        ],
    )
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[5], "Surf and Turf", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    result = defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Whole Chicken", "Shrimp"])

    by_item = {c["item"]: c for c in result["created"]}
    assert by_item["Whole Chicken"]["lead_tier"] == "large"
    assert by_item["Whole Chicken"]["task_date"] == (datetime.date.fromisoformat(dates[5]) - datetime.timedelta(days=2)).isoformat()
    assert by_item["Shrimp"]["lead_tier"] == "small_thin"
    assert by_item["Shrimp"]["task_date"] == (datetime.date.fromisoformat(dates[5]) - datetime.timedelta(days=1)).isoformat()


def test_confirming_an_item_used_on_two_separate_cook_nights_creates_a_task_per_night():
    _meat_recipe()
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[1], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.plan_meal(dates[4], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    result = defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Chicken Thighs"])

    assert len(result["created"]) == 2
    task_dates = sorted(c["task_date"] for c in result["created"])
    assert task_dates == sorted([
        (datetime.date.fromisoformat(dates[1]) - datetime.timedelta(days=1)).isoformat(),
        (datetime.date.fromisoformat(dates[4]) - datetime.timedelta(days=1)).isoformat(),
    ])


def test_confirming_a_leftover_chain_source_creates_only_the_cook_nights_task():
    """A leftovers night eats nothing on its own -- the cook night's batch
    already covers it, so only ONE task should exist, dated for the cook
    night, not the reheat night."""
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[{"item": "Beef", "qty": "1 lb", "category": "meat/seafood"}],
        default_servings=3,
    )
    week = _week_start()
    dates = tools._week_dates(week)
    tue, thu = dates[1], dates[3]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tue, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.plan_meal(
        thu, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan["weekly_plan_id"],
        derived_from={"links_to": f"{tue}:dinner"},
    )
    tools.repair_leftover_chains(plan["weekly_plan_id"])

    result = defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Beef"])

    assert len(result["created"]) == 1
    assert result["created"][0]["date"] == tue
    assert result["created"][0]["task_date"] == (datetime.date.fromisoformat(tue) - datetime.timedelta(days=1)).isoformat()


def test_confirming_an_unselected_item_creates_nothing():
    _meat_recipe()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    assert defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Ground Beef"]) == {"created": [], "notes": []}


def test_confirming_with_an_empty_list_is_the_all_fresh_answer_and_creates_nothing():
    _meat_recipe()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    assert defrost.confirm_frozen_items(plan["weekly_plan_id"], []) == {"created": [], "notes": []}


def test_confirming_a_meal_happening_today_is_too_late_and_creates_no_task():
    """
    _move_date always leaves at least one full calendar day between the
    move and the meal (see its own docstring) -- so a meal happening TODAY
    can never have an honest move date that isn't already in the past.
    Confirming it should never create a task dated in the past; it should
    say so plainly instead, in voice, with a real way out.
    """
    _meat_recipe()
    today = datetime.date.today().isoformat()
    plan = tools.create_weekly_plan(_week_start(offset_weeks=0))
    tools.plan_meal(today, "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    result = defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Chicken Thighs"])

    assert result["created"] == []
    assert len(result["notes"]) == 1
    assert result["notes"][0]["item"] == "Chicken Thighs"
    assert result["notes"][0]["note"] == defrost.TOO_LATE_TO_THAW_NOTE
    assert "cold-water thaw" in result["notes"][0]["note"]
    tasks = [t for t in tools.get_prep_schedule(plan["weekly_plan_id"]) if t["task_type"] == "defrost"]
    assert tasks == []


def test_regenerating_the_plans_own_defrost_sync_does_not_delete_a_confirmed_freezer_task():
    """
    Regression guard for the exact bug class sync_defrost_tasks already
    fixed once for the ready_made path (see its own docstring): a
    confirm_frozen_items task has meal_plan_entry_id set (so it can say
    "for Thursday's skewers") but inventory_item_id NULL (never written to
    inventory) -- sync_defrost_tasks must not mistake it for one of its own
    stale freezer-derived candidates and sweep it away on the next
    generate/regenerate.
    """
    _meat_recipe()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.confirm_frozen_items(plan["weekly_plan_id"], ["Chicken Thighs"])
    assert len(tools.get_prep_schedule(plan["weekly_plan_id"])) == 1

    result = defrost.sync_defrost_tasks(plan["weekly_plan_id"])

    assert result["removed"] == 0
    tasks = tools.get_prep_schedule(plan["weekly_plan_id"])
    assert len(tasks) == 1
    assert "Chicken Thighs" in tasks[0]["description"]


# ---------- mark_defrost_asked / defrost_asked_at plumbing ----------

def test_mark_defrost_asked_is_reflected_on_the_plan():
    plan = tools.create_weekly_plan(_week_start())
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["defrost_asked_at"] is None

    tools.mark_defrost_asked(plan["weekly_plan_id"])

    assert tools.get_weekly_plan(plan["weekly_plan_id"])["defrost_asked_at"] is not None


def test_defrost_asked_at_is_settable_more_than_once_for_the_cook_view_re_ask():
    plan = tools.create_weekly_plan(_week_start())
    tools.mark_defrost_asked(plan["weekly_plan_id"])
    first = tools.get_weekly_plan(plan["weekly_plan_id"])["defrost_asked_at"]

    tools.mark_defrost_asked(plan["weekly_plan_id"])

    second = tools.get_weekly_plan(plan["weekly_plan_id"])["defrost_asked_at"]
    assert second is not None
    assert first is not None


# ---------- The endpoints ----------

def test_defrost_items_endpoint_lists_the_plans_meat_ingredients(signed_in):
    _meat_recipe()
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    res = signed_in.get(f"/api/week/{week}/defrost-items")

    assert res.status_code == 200
    body = res.json()
    assert body["weekly_plan_id"] == plan["weekly_plan_id"]
    assert len(body["items"]) == 1
    assert body["items"][0]["item"] == "Chicken Thighs"


def test_defrost_items_endpoint_404s_for_a_week_with_no_plan(signed_in):
    res = signed_in.get("/api/week/2099-01-05/defrost-items")
    assert res.status_code == 404


def test_defrost_confirm_endpoint_creates_tasks_and_marks_asked(signed_in):
    _meat_recipe()
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    res = signed_in.post(f"/api/week/{week}/defrost-confirm", json={"items": ["Chicken Thighs"]})

    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]) == 1
    assert body["notes"] == []
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["defrost_asked_at"] is not None
    week_data = tools.get_week_menu(plan["weekly_plan_id"])
    assert week_data["defrost_asked_at"] is not None


def test_defrost_confirm_endpoint_with_no_items_dismisses_and_creates_nothing(signed_in):
    """Covers both 'None -- all fresh' and a quiet dismiss: both are a
    complete answer to the ask card, and both just mark it answered."""
    _meat_recipe()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    res = signed_in.post(f"/api/week/{week}/defrost-confirm", json={"items": []})

    assert res.status_code == 200
    body = res.json()
    assert body["created"] == []
    assert body["notes"] == []
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["defrost_asked_at"] is not None


def test_defrost_confirm_endpoint_can_be_answered_again_from_cook_view(signed_in):
    """Re-asking from the Cook view isn't a one-shot -- a household that
    already answered can confirm a second item found later in the week."""
    tools.add_recipe(
        "Surf and Turf",
        ingredients=[
            {"item": "Chicken Thighs", "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"},
        ],
    )
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Surf and Turf", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    signed_in.post(f"/api/week/{week}/defrost-confirm", json={"items": []})
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["defrost_asked_at"] is not None

    res = signed_in.post(f"/api/week/{week}/defrost-confirm", json={"items": ["Shrimp"]})

    assert res.status_code == 200
    assert len(res.json()["created"]) == 1


def test_defrost_confirm_endpoint_too_late_answer_returns_the_note(signed_in):
    _meat_recipe()
    today = datetime.date.today().isoformat()
    plan = tools.create_weekly_plan(_week_start(offset_weeks=0))
    tools.plan_meal(today, "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    res = signed_in.post(
        f"/api/week/{plan['week_start_date']}/defrost-confirm", json={"items": ["Chicken Thighs"]},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["created"] == []
    assert len(body["notes"]) == 1
    assert body["notes"][0]["note"] == defrost.TOO_LATE_TO_THAW_NOTE
