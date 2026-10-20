"""
Trading two nights' dinners never puts a dish on a night that has gone by.

Overnight hunt, 2026-10-20. `swap_dinner_nights` — the chat tool "move
Thursday's dinner to Monday", POST /swap-nights, a draft flag's "Move it" —
read no clock. On a Wednesday, trading Monday with Thursday re-dated
Thursday's tacos onto Monday: off every screen that looks ahead, never
cooked, its groceries still on the list. Move (meal_move.plan_move) and
every swap door already refuse a night gone by on the HOUSEHOLD's clock
(weekly_plan.night_has_gone); this was the door the 2026-09-17 set missed.
"""
from __future__ import annotations

from conftest import household_date

from app import agent, tools
from app.db import get_conn

YESTERDAY, TODAY, TOMORROW = household_date(-1), household_date(0), household_date(1)
START = household_date(-3)


def _setup() -> int:
    for name in ("Bean Chili", "Fish Tacos", "Pasta Bake"):
        tools.add_recipe(name, ingredients=[{"item": f"{name} stuff", "qty": "1 lb"}],
                         prep_time_minutes=10, cook_time_minutes=20)
    plan = tools.create_weekly_plan(START)["weekly_plan_id"]
    tools.plan_meal(YESTERDAY, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(TODAY, "Pasta Bake", slot="dinner", weekly_plan_id=plan)
    tools.plan_meal(TOMORROW, "Fish Tacos", slot="dinner", weekly_plan_id=plan)
    return plan


def _dinners() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.slot = 'dinner'"
    ).fetchall()
    conn.close()
    return {r["date"]: r["meal"] for r in rows}


def test_the_route_will_not_put_tomorrows_dinner_on_yesterday(signed_in):
    plan = _setup()
    week = tools.get_weekly_plan(plan)["week_start_date"]
    before = _dinners()

    res = signed_in.post(f"/api/week/{week}/swap-nights", json={"date_a": YESTERDAY, "date_b": TOMORROW})

    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "refused"
    assert "gone" in out["message"]
    assert _dinners() == before, "nothing moved"


def test_the_chat_tool_refuses_either_way_round():
    plan = _setup()
    before = _dinners()

    out = agent.TOOL_FUNCTIONS["swap_dinner_nights"](plan, TOMORROW, YESTERDAY)

    assert out["status"] == "refused"
    assert _dinners() == before


def test_tonight_still_trades_with_tomorrow():
    """Strictly BEFORE today: tonight is the most ordinary night to change."""
    plan = _setup()

    out = tools.swap_dinner_nights(plan, TODAY, TOMORROW)

    assert out["status"] == "swapped"
    assert _dinners()[TODAY] == "Fish Tacos"
    assert _dinners()[TOMORROW] == "Pasta Bake"
