"""
Moving a dinner between nights (Emily, 2026-09-12 — Plan › Which days as
seven tiles, "drag to move a night").

The write is tools.swap_dinner_nights: the two nights' DINNER rows are
re-dated in place, so everything keyed by entry id (grocery links, the
cooked tick) rides along, and everything keyed by DATE (a leftover chain's
"date:slot" references, a defrost reminder) is moved by hand. The grocery
list is never touched. These tests pin each of those down, plus the undo
token and the route in front of it — every one of them fails on main,
where the function does not exist.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import tools
from app.db import get_conn
from app.tools import defrost


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


# Next week, so nothing here is "already today" for a defrost reminder and
# nothing is in the past.
WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS


def _plan() -> int:
    return tools.create_weekly_plan(WEEK)["weekly_plan_id"]


def _recipe(name, minutes=30, meat=False):
    ingredients = [{"item": "Onion", "qty": "1", "category": "produce"}]
    if meat:
        ingredients.append({"item": "Chicken Thighs", "qty": "1 lb", "category": "meat/seafood"})
    tools.add_recipe(name, ingredients=ingredients, prep_time_minutes=10,
                     cook_time_minutes=minutes - 10, default_servings=4)


def _dinner(day: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot_state, mpe.cooked_status, mpe.derived_from_json, "
        "COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    out = dict(row)
    out["derived"] = json.loads(out.pop("derived_from_json") or "{}")
    return out


def _grocery_snapshot() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? "
        "ORDER BY item, quantity", (tools.household_id(),),
    ).fetchall()
    links = conn.execute(
        "SELECT meal_plan_entry_id, grocery_item_id, quantity FROM meal_plan_grocery_links "
        "WHERE household_id = ? ORDER BY id", (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows] + [tuple(r) for r in links]


# ---------------------------------------------------------------- the swap

def test_two_nights_trade_dinners_and_keep_their_entry_ids():
    _recipe("Chicken Traybake")
    _recipe("Bean Chili")
    plan = _plan()
    a = tools.plan_meal(TUE, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)["entry_id"]
    b = tools.plan_meal(FRI, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(TUE, "Toast", slot="breakfast", weekly_plan_id=plan)

    out = tools.swap_dinner_nights(plan, TUE, FRI)

    assert out["status"] == "swapped"
    assert out["can_undo"] is True
    assert _dinner(TUE)["meal"] == "Bean Chili" and _dinner(TUE)["id"] == b
    assert _dinner(FRI)["meal"] == "Chicken Traybake" and _dinner(FRI)["id"] == a
    # Only the dinners: Tuesday's breakfast is still Tuesday's.
    conn = get_conn()
    bf = conn.execute("SELECT date FROM meal_plan_entries WHERE slot = 'breakfast'").fetchone()
    conn.close()
    assert bf["date"] == TUE
    # And the answer carries both changed days in get_week_menu's shape.
    assert sorted(d["date"] for d in out["days"]) == [TUE, FRI]
    by_date = {d["date"]: d for d in out["days"]}
    assert by_date[TUE]["dinner"]["title"] == "Bean Chili"
    assert by_date[FRI]["dinner"]["title"] == "Chicken Traybake"


def test_the_dinner_moves_to_an_empty_night_and_leaves_that_night_empty():
    """A night with no dinner row yet is a real target: the one row moves
    and the other night ends up with nothing, exactly as it was."""
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(WED, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    out = tools.swap_dinner_nights(plan, WED, SAT)

    assert out["status"] == "swapped"
    assert _dinner(WED) is None
    assert _dinner(SAT)["meal"] == "Bean Chili"


def test_the_grocery_list_is_not_touched_on_an_approved_week():
    """Same dishes, same lines: the links are by entry id and the entries
    keep their ids, so nothing is reversed or re-bought."""
    _recipe("Chicken Traybake")
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(TUE, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(FRI, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan)
    before = _grocery_snapshot()
    assert before, "approval should have put something on the list"

    tools.swap_dinner_nights(plan, TUE, FRI)

    assert _grocery_snapshot() == before


def test_a_defrost_reminder_moves_by_the_same_number_of_days():
    """A defrost date is the cook date minus a lead time, so it moves by
    exactly the days the dinner moved — status untouched, weekday re-said."""
    _recipe("Chicken Skewers", meat=True)
    _recipe("Bean Chili")
    plan = _plan()
    skewers = tools.plan_meal(WED, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(SAT, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    created = defrost.confirm_frozen_items(plan, ["Chicken Thighs"])["created"]
    assert len(created) == 1
    task_id = created[0]["prep_task_id"]
    conn = get_conn()
    conn.execute("UPDATE prep_tasks SET status = 'done' WHERE id = ?", (task_id,))
    conn.commit()
    old = conn.execute("SELECT task_date, description FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    assert "Wednesday" in old["description"]

    out = tools.swap_dinner_nights(plan, WED, SAT)

    assert out["prep_tasks_moved"] == 1
    conn = get_conn()
    new = conn.execute(
        "SELECT task_date, description, status, meal_plan_entry_id FROM prep_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    conn.close()
    assert new["meal_plan_entry_id"] == skewers
    assert new["status"] == "done"
    delta = (datetime.date.fromisoformat(new["task_date"]) - datetime.date.fromisoformat(old["task_date"])).days
    assert delta == 3
    assert "Saturday" in new["description"] and "Wednesday" not in new["description"]


def test_a_leftover_chain_follows_its_dish_when_the_cook_night_moves_earlier():
    """Tuesday's chili feeds Thursday. Moving the cook to Monday keeps the
    pairing: Thursday now points at Monday, and Monday names Thursday."""
    _recipe("Bean Chili")
    _recipe("Chicken Traybake")
    plan = _plan()
    tools.plan_meal(MON, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(TUE, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(THU, "Bean Chili", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{TUE}:dinner"})
    tools.repair_leftover_chains(plan)
    assert tools.plan_leftover_chains(plan)["leftovers"], "the chain should be confirmed first"

    out = tools.swap_dinner_nights(plan, MON, TUE)

    assert out["status"] == "swapped"
    assert _dinner(MON)["meal"] == "Bean Chili"
    assert _dinner(MON)["derived"]["make_double_for"] == [f"{THU}:dinner"]
    assert _dinner(THU)["derived"]["links_to"] == f"{MON}:dinner"
    chains = tools.plan_leftover_chains(plan)
    assert list(chains["leftovers"].values())[0]["source"]["date"] == MON


def test_a_chain_that_would_run_backwards_is_refused_and_nothing_is_written():
    """Tuesday's chili feeds Thursday; moving the cook to Friday would put
    the reheat before the cook. An answer, not an error — and no write."""
    _recipe("Bean Chili")
    _recipe("Chicken Traybake")
    plan = _plan()
    tools.plan_meal(TUE, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(THU, "Bean Chili", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{TUE}:dinner"})
    tools.plan_meal(FRI, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)

    out = tools.swap_dinner_nights(plan, TUE, FRI)

    assert out["status"] == "refused"
    assert "Bean Chili" in out["message"] and "Thursday" in out["message"]
    assert _dinner(TUE)["meal"] == "Bean Chili"
    assert _dinner(FRI)["meal"] == "Chicken Traybake"
    assert "moved_from" not in _dinner(TUE)["derived"]


def test_a_night_nobody_is_home_refuses_to_take_part():
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(TUE, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_empty(plan, FRI, "dinner", "Out — nothing to cook")

    out = tools.swap_dinner_nights(plan, TUE, FRI)

    assert out["status"] == "refused"
    assert "Friday" in out["message"]
    assert _dinner(TUE)["meal"] == "Bean Chili"
    assert _dinner(FRI)["slot_state"] == "planned_empty"


def test_a_dinner_already_cooked_stays_where_it_was():
    _recipe("Bean Chili")
    _recipe("Chicken Traybake")
    plan = _plan()
    cooked = tools.plan_meal(TUE, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(FRI, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.check_off_meal(cooked)

    out = tools.swap_dinner_nights(plan, TUE, FRI)

    assert out["status"] == "refused"
    assert "Bean Chili" in out["message"] and "cooked" in out["message"]
    assert _dinner(TUE)["id"] == cooked


def test_a_night_off_the_plan_and_the_same_night_twice_are_errors():
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(TUE, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    next_monday = (datetime.date.fromisoformat(SUN) + datetime.timedelta(days=1)).isoformat()

    with pytest.raises(ValueError):
        tools.swap_dinner_nights(plan, TUE, next_monday)
    with pytest.raises(ValueError):
        tools.swap_dinner_nights(plan, TUE, TUE)
    with pytest.raises(ValueError):
        tools.swap_dinner_nights(plan, TUE, "not-a-date")
    assert _dinner(TUE)["meal"] == "Bean Chili"


def test_a_period_that_is_not_a_monday_week_is_respected():
    """Planning periods, not weeks (2026-09-04): a Thursday-to-Wednesday
    plan can move Thursday to the following Wednesday, and Monday of the
    filing-key week — before the period starts — is off the plan."""
    _recipe("Bean Chili")
    _recipe("Chicken Traybake")
    plan = tools.create_weekly_plan(WEEK, content_start_date=THU, day_count=7)["weekly_plan_id"]
    next_wed = (datetime.date.fromisoformat(THU) + datetime.timedelta(days=6)).isoformat()
    tools.plan_meal(THU, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(next_wed, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)

    out = tools.swap_dinner_nights(plan, THU, next_wed)
    assert out["status"] == "swapped"
    assert _dinner(next_wed)["meal"] == "Bean Chili"

    with pytest.raises(ValueError):
        tools.swap_dinner_nights(plan, MON, THU)


# ------------------------------------------------------------------ undo

def test_undo_puts_both_dinners_back_and_forgets_the_move():
    _recipe("Chicken Traybake")
    _recipe("Bean Chili")
    plan = _plan()
    a = tools.plan_meal(TUE, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)["entry_id"]
    b = tools.plan_meal(FRI, "Bean Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.swap_dinner_nights(plan, TUE, FRI)
    assert _dinner(TUE)["derived"]["moved_from"]["date"] == FRI

    out = tools.undo_dinner_nights_swap(plan, TUE, FRI)

    assert out["status"] == "restored" and out["can_undo"] is False
    assert _dinner(TUE)["id"] == a and _dinner(FRI)["id"] == b
    assert "moved_from" not in _dinner(TUE)["derived"]
    assert "moved_from" not in _dinner(FRI)["derived"]


def test_undo_refuses_nights_that_were_not_just_moved():
    _recipe("Chicken Traybake")
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(TUE, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(FRI, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    with pytest.raises(ValueError):
        tools.undo_dinner_nights_swap(plan, TUE, FRI)
    assert _dinner(TUE)["meal"] == "Chicken Traybake"


def test_undo_moves_a_defrost_reminder_back_too():
    _recipe("Chicken Skewers", meat=True)
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(WED, "Chicken Skewers", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(SAT, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    task_id = defrost.confirm_frozen_items(plan, ["Chicken Thighs"])["created"][0]["prep_task_id"]
    conn = get_conn()
    before = conn.execute("SELECT task_date, description FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()

    tools.swap_dinner_nights(plan, WED, SAT)
    tools.undo_dinner_nights_swap(plan, WED, SAT)

    conn = get_conn()
    after = conn.execute("SELECT task_date, description FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    assert tuple(after) == tuple(before)


# ----------------------------------------------------------------- routes

def test_the_route_moves_a_night_and_its_undo_puts_it_back(signed_in):
    _recipe("Chicken Traybake")
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(TUE, "Chicken Traybake", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(FRI, "Bean Chili", slot="dinner", weekly_plan_id=plan)

    res = signed_in.post(f"/api/week/{WEEK}/swap-nights", json={"date_a": TUE, "date_b": FRI})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "swapped"
    assert _dinner(TUE)["meal"] == "Bean Chili"

    res = signed_in.post(f"/api/week/{WEEK}/swap-nights-undo", json={"date_a": TUE, "date_b": FRI})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "restored"
    assert _dinner(TUE)["meal"] == "Chicken Traybake"


def test_the_route_answers_400_for_a_night_off_the_plan(signed_in):
    _recipe("Bean Chili")
    plan = _plan()
    tools.plan_meal(TUE, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    next_monday = (datetime.date.fromisoformat(SUN) + datetime.timedelta(days=1)).isoformat()

    res = signed_in.post(f"/api/week/{WEEK}/swap-nights", json={"date_a": TUE, "date_b": next_monday})
    assert res.status_code == 400
    res = signed_in.post(f"/api/week/{WEEK}/swap-nights", json={"date_a": TUE, "date_b": "2026-13-40"})
    assert res.status_code == 400
    assert _dinner(TUE)["meal"] == "Bean Chili"


def test_the_route_404s_a_week_with_no_plan(signed_in):
    res = signed_in.post(f"/api/week/{WEEK}/swap-nights", json={"date_a": TUE, "date_b": FRI})
    assert res.status_code == 404


# ------------------------------------------------------------- chat wiring

def test_chat_can_move_a_night_and_the_action_card_points_at_plan():
    """Registered as an assistant tool beside swap_meal_in_plan, and tagged
    as a `week` write so the Plan panel refreshes after a chat turn."""
    from app import agent, main

    assert "swap_dinner_nights" in agent.TOOL_FUNCTIONS
    names = {t["name"] for t in agent.TOOL_DEFINITIONS}
    assert "swap_dinner_nights" in names
    assert "swap_dinner_nights" in main._WEEK_TOOLS
