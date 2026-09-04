"""
The defrost flow — Loop Board "First-class 'defrost' prep step: say
exactly what to take out and when."

Before this, generate_prep_schedule never saw inventory at all (it built
its context from only week_start_date/planning_mode/meals), and even the
LLM-driven prep pass only fired when a recipe's advance_prep_notes called
for something — which every sampled recipe left blank, so defrost never
happened in practice. These tests cover the deterministic replacement:
app/tools/defrost.py's freezer-inventory-to-meal matching, its documented
category lead-time table, the dinner_window-aware backward scheduling, the
ready_made confirm wiring, and the Today-tile read/write surface — plus the
plumbing fix itself (a week with a frozen item but no advance_prep_notes
now still gets a defrost reminder).
"""
import datetime

import pytest

from app import agent, tools
from app.tools import defrost


def _week_start(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


@pytest.fixture
def chicken_recipe():
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=15,
    )


def _freeze(item="Chicken Thighs", quantity="1 lb", category="meat/seafood"):
    tools.update_inventory(item, "add", quantity=quantity, category=category, location="freezer")


# ---------- lead_hours_for_item: the documented rule-of-thumb table ----------

@pytest.mark.parametrize("item,expected_hours,expected_tier", [
    ("Whole Chicken", 48.0, "large"),
    ("Turkey", 48.0, "large"),
    ("Pork Shoulder", 48.0, "large"),
    ("Chicken Thighs", 24.0, "standard"),
    ("Ground Beef", 24.0, "standard"),
    ("Shrimp", 18.0, "small_thin"),
    ("Salmon Fillet", 18.0, "small_thin"),
])
def test_lead_hours_for_item_tiers(item, expected_hours, expected_tier):
    hours, tier = defrost.lead_hours_for_item(item)
    assert hours == expected_hours
    assert tier == expected_tier


# ---------- _move_date: backward scheduling, with and without dinner_window ----------

def test_move_date_with_a_known_dinner_window_uses_clock_time():
    # Thursday 6-8pm dinner (mapped to 19:00), 24h lead -> Wednesday.
    assert defrost._move_date("2026-09-10", 24.0, "6_8") == "2026-09-09"


def test_move_date_short_lead_can_stay_same_day_with_a_late_dinner_window():
    # A 6h lead against an 8pm-ish dinner still lands same-day (14:00) --
    # this is exactly the case a whole-day fallback would get needlessly
    # conservative about, which is why a known dinner_window is used when
    # it's available.
    assert defrost._move_date("2026-09-10", 6.0, "later") == "2026-09-10"


def test_move_date_without_a_dinner_window_falls_back_to_whole_days():
    # 'all_over' and an unset dinner_window have no honest clock to
    # subtract from, per the household rhythm docs -- whole-day counting,
    # rounded UP so the fallback never under-shoots the table's lead time.
    assert defrost._move_date("2026-09-10", 24.0, "all_over") == "2026-09-09"
    assert defrost._move_date("2026-09-10", 24.0, None) == "2026-09-09"
    # A 6h lead with no known dinner time still rounds up to a full day,
    # unlike the known-window case above -- the honest-default direction
    # is "a bit early," never "cutting it close."
    assert defrost._move_date("2026-09-10", 6.0, None) == "2026-09-09"
    assert defrost._move_date("2026-09-10", 48.0, None) == "2026-09-08"


# ---------- defrost_candidates_for_plan / sync_defrost_tasks ----------

def test_a_freezer_item_used_by_a_planned_meal_produces_a_defrost_candidate(chicken_recipe):
    _freeze()
    week = _week_start()
    dates = tools._week_dates(week)
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    candidates = defrost.defrost_candidates_for_plan(plan["weekly_plan_id"])

    assert len(candidates) == 1
    c = candidates[0]
    assert c["quantity"] == "1 lb"
    assert c["related_meal"] == "Chicken Skewers"
    assert "Chicken Thighs" in c["description"]
    assert "for " in c["description"] and "Chicken Skewers" in c["description"]
    # No dinner_window set for this household -> whole-day fallback, 24h
    # (standard cut) rounds up to exactly one day before the meal.
    assert c["task_date"] == (datetime.date.fromisoformat(dates[3]) - datetime.timedelta(days=1)).isoformat()


def test_a_fridge_item_of_the_same_name_is_not_a_defrost_candidate(chicken_recipe):
    tools.update_inventory("Chicken Thighs", "add", quantity="1 lb", category="meat/seafood", location="fridge")
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    assert defrost.defrost_candidates_for_plan(plan["weekly_plan_id"]) == []


def test_an_ambiguous_inventory_match_is_not_acted_on(chicken_recipe):
    # "chicken broth" is not a confident match for the ingredient "Chicken
    # Thighs" (cooker._find_inventory_match's bar: exact/plural-only) --
    # same safety threshold deplete_inventory_for_meal uses before it will
    # touch inventory automatically.
    tools.update_inventory("Chicken Broth", "add", quantity="1", category="pantry", location="freezer")
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    assert defrost.defrost_candidates_for_plan(plan["weekly_plan_id"]) == []


def test_sync_defrost_tasks_is_idempotent(chicken_recipe):
    _freeze()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])

    first = defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    second = defrost.sync_defrost_tasks(plan["weekly_plan_id"])

    assert first == {"weekly_plan_id": plan["weekly_plan_id"], "inserted": 1, "updated": 0, "removed": 0}
    assert second == {"weekly_plan_id": plan["weekly_plan_id"], "inserted": 0, "updated": 1, "removed": 0}
    tasks = tools.get_prep_schedule(plan["weekly_plan_id"])
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "defrost"


def test_regenerating_defrost_tasks_preserves_done_status(chicken_recipe):
    """
    The whole point of matching existing rows by key before rewriting:
    re-syncing (a swapped meal elsewhere in the week, a manual prep
    regenerate) must not silently un-defrost something the household
    already marked done.
    """
    _freeze()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    task = tools.get_prep_schedule(plan["weekly_plan_id"])[0]
    tools.check_off_prep_step(task["id"], "done")

    defrost.sync_defrost_tasks(plan["weekly_plan_id"])

    tasks = tools.get_prep_schedule(plan["weekly_plan_id"])
    assert len(tasks) == 1
    assert tasks[0]["id"] == task["id"]
    assert tasks[0]["status"] == "done"


def test_a_stale_defrost_task_is_removed_when_its_meal_is_swapped_away(chicken_recipe):
    _freeze()
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf"}])
    week = _week_start()
    d = tools._week_dates(week)[3]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(d, "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    assert len(tools.get_prep_schedule(plan["weekly_plan_id"])) == 1

    tools.swap_meal_in_plan(plan["weekly_plan_id"], d, "Toast", slot="dinner")
    result = defrost.sync_defrost_tasks(plan["weekly_plan_id"])

    assert result["removed"] == 1
    assert tools.get_prep_schedule(plan["weekly_plan_id"]) == []


def test_save_prep_tasks_never_touches_defrost_rows(chicken_recipe):
    """save_prep_tasks (the LLM/general pass) must not wipe out defrost's own rows, and vice versa -- each producer only manages its own task_type."""
    _freeze()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])

    tools.save_prep_tasks(plan["weekly_plan_id"], [
        {"task_date": tools._week_dates(week)[0], "description": "Marinate something", "related_meal": "Chicken Skewers"},
    ])

    tasks = tools.get_prep_schedule(plan["weekly_plan_id"])
    types = sorted(t["task_type"] for t in tasks)
    assert types == ["defrost", "general"]

    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    tasks = tools.get_prep_schedule(plan["weekly_plan_id"])
    types = sorted(t["task_type"] for t in tasks)
    assert types == ["defrost", "general"], "re-syncing defrost must not remove the general task"


# ---------- get_defrost_today / get_defrost_schedule ----------

def test_get_defrost_today_only_returns_todays_pending_defrost_tasks(chicken_recipe):
    _freeze()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    today = datetime.date.today().isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    # Force the one real candidate's date to today for a clean assertion,
    # and hand-insert a couple more directly to cover the filter itself.
    from app.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET task_date = ? WHERE weekly_plan_id = ?", (today, plan["weekly_plan_id"]))
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, task_type, status) "
        "VALUES (1, ?, ?, 'Move the salmon to the fridge.', 'defrost', 'pending')",
        (plan["weekly_plan_id"], tomorrow),
    )
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, task_type, status) "
        "VALUES (1, ?, ?, 'Already handled.', 'defrost', 'done')",
        (plan["weekly_plan_id"], today),
    )
    conn.commit()
    conn.close()

    today_tasks = tools.get_defrost_today()

    assert len(today_tasks) == 1
    assert "Chicken Thighs" in today_tasks[0]["description"]
    assert today_tasks[0]["status"] == "pending"


def test_get_defrost_schedule_windows_by_days_ahead(chicken_recipe):
    from app.db import get_conn
    conn = get_conn()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    near = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    far = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, task_type, status) "
        "VALUES (1, ?, ?, 'Near task', 'defrost', 'pending')", (plan["weekly_plan_id"], near),
    )
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, task_type, status) "
        "VALUES (1, ?, ?, 'Far task', 'defrost', 'pending')", (plan["weekly_plan_id"], far),
    )
    conn.commit()
    conn.close()

    within_a_week = defrost.get_defrost_schedule(days=7)
    assert [t["description"] for t in within_a_week] == ["Near task"]

    within_two_weeks = defrost.get_defrost_schedule(days=14)
    assert [t["description"] for t in within_two_weeks] == ["Near task", "Far task"]


# ---------- check_off_prep_step: done | skipped | pending ----------

def test_check_off_prep_step_accepts_skipped(chicken_recipe):
    _freeze()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    task_id = tools.get_prep_schedule(plan["weekly_plan_id"])[0]["id"]

    result = tools.check_off_prep_step(task_id, "skipped")

    assert result == {"prep_task_id": task_id, "status": "skipped"}
    assert tools.get_prep_schedule(plan["weekly_plan_id"])[0]["status"] == "skipped"


def test_check_off_prep_step_rejects_an_unknown_status(chicken_recipe):
    _freeze()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    task_id = tools.get_prep_schedule(plan["weekly_plan_id"])[0]["id"]

    with pytest.raises(ValueError):
        tools.check_off_prep_step(task_id, "archived")


# ---------- Wiring: a week with no advance_prep_notes still gets a defrost reminder ----------

def test_generating_a_week_creates_a_defrost_task_even_with_no_advance_prep_notes(chicken_recipe, monkeypatch):
    """
    The ticket's actual bug: _generate_prep_schedule_if_needed gates the
    LLM-driven general pass on advance_prep_notes, and this recipe has
    none -- so the old behaviour was zero prep tasks, defrost included.
    _sync_defrost_tasks_if_needed must run regardless of that gate.
    """
    _freeze()
    week = _week_start()

    def fake_plan_llm(ctx):
        return [{
            "date": tools._week_dates(week)[3], "slot": "dinner", "meal_name": "Chicken Skewers",
            "is_new_recipe": False, "reasoning": "fits the week", "food_groups": [],
        }]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake_plan_llm)

    general_calls = []
    monkeypatch.setattr(agent, "generate_prep_schedule", lambda plan_id: general_calls.append(plan_id))

    plan = agent.generate_weekly_plan(week)

    assert general_calls == [], "no advance_prep_notes means the LLM prep pass still correctly stays off"
    tasks = tools.get_prep_schedule(plan["weekly_plan_id"])
    defrost_tasks = [t for t in tasks if t["task_type"] == "defrost"]
    assert len(defrost_tasks) == 1
    assert "Chicken Thighs" in defrost_tasks[0]["description"]


def test_a_broken_defrost_sync_does_not_lose_the_week(monkeypatch):
    """Same safety net as _generate_prep_schedule_if_needed: an optional reminder pass failing must not take a successfully generated week down with it."""
    tools.add_recipe("Toast", ingredients=[{"item": "Bread", "qty": "1 loaf"}])
    week = _week_start()

    def fake_plan_llm(ctx):
        return [{"date": tools._week_dates(week)[0], "slot": "dinner", "meal_name": "Toast", "food_groups": []}]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake_plan_llm)

    def explode(plan_id):
        raise RuntimeError("defrost sync blew up")
    monkeypatch.setattr(tools, "sync_defrost_tasks", explode)

    plan = agent.generate_weekly_plan(week)

    assert plan["weekly_plan_id"]
    assert tools.get_weekly_plan(plan["weekly_plan_id"])["meals"]


# ---------- Wiring: the ready_made recommendation path ----------

def test_confirming_a_ready_made_defrost_recommendation_creates_a_task():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.update_inventory("Frozen Lasagna", "add", quantity="1", category="frozen", location="freezer")
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[4], "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.set_away_stretch(saturday, "lunch", sunday, "lunch")
    ready_made_date, ready_made_slot = sunday, "dinner"
    assert tools.get_slot_need(ready_made_date, ready_made_slot)["need"] == "ready_made"

    # Not yet confirmed -- nothing acts on a recommendation until the
    # household says yes (Emily's rule).
    assert tools.get_defrost_today() == []

    tools.confirm_slot_recommendation(ready_made_date, ready_made_slot, confirmed=True)

    conn_tasks = [t for t in tools.get_prep_schedule(plan["weekly_plan_id"]) if t["task_type"] == "defrost"]
    assert len(conn_tasks) == 1
    assert "Frozen Lasagna" in conn_tasks[0]["description"]


def test_declining_a_ready_made_recommendation_removes_any_created_task():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.update_inventory("Frozen Lasagna", "add", quantity="1", category="frozen", location="freezer")
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[4], "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.set_away_stretch(saturday, "lunch", sunday, "lunch")
    tools.confirm_slot_recommendation(sunday, "dinner", confirmed=True)
    assert any(t["task_type"] == "defrost" for t in tools.get_prep_schedule(plan["weekly_plan_id"]))

    tools.confirm_slot_recommendation(sunday, "dinner", confirmed=False)

    assert not any(t["task_type"] == "defrost" for t in tools.get_prep_schedule(plan["weekly_plan_id"]))


def test_confirming_a_batch_recommendation_creates_no_defrost_task():
    """A ready_made slot recommended as a batch-from-earlier-dinner (no freezer item involved) has nothing to defrost -- confirming it must not create a phantom task."""
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    week = _week_start()
    dates = tools._week_dates(week)
    saturday, sunday = dates[5], dates[6]
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(dates[4], "Chili", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    tools.set_away_stretch(saturday, "lunch", sunday, "lunch")
    assert tools.get_slot_need(sunday, "dinner")["recommended_batch_from_entry_id"] is not None

    tools.confirm_slot_recommendation(sunday, "dinner", confirmed=True)

    assert tools.get_defrost_today() == []
    assert not any(t["task_type"] == "defrost" for t in tools.get_prep_schedule(plan["weekly_plan_id"]))


# ---------- The endpoint ----------

def test_defrost_today_endpoint(signed_in, chicken_recipe):
    _freeze()
    week = _week_start()
    plan = tools.create_weekly_plan(week)
    tools.plan_meal(tools._week_dates(week)[3], "Chicken Skewers", slot="dinner", weekly_plan_id=plan["weekly_plan_id"])
    defrost.sync_defrost_tasks(plan["weekly_plan_id"])
    task = tools.get_prep_schedule(plan["weekly_plan_id"])[0]
    from app.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET task_date = ? WHERE id = ?", (datetime.date.today().isoformat(), task["id"]))
    conn.commit()
    conn.close()

    res = signed_in.get("/api/prep/defrost-today")

    assert res.status_code == 200
    body = res.json()
    assert len(body["tasks"]) == 1
    assert "Chicken Thighs" in body["tasks"][0]["description"]

    done = signed_in.post("/api/cooker/check-prep", json={"prep_task_id": task["id"], "status": "done"})
    assert done.status_code == 200
    res2 = signed_in.get("/api/prep/defrost-today")
    assert res2.json()["tasks"] == []
