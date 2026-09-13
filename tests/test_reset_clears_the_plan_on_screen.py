"""
"Start over" clears the plan the Plan tab is showing — Loop Board 2026-09-13.

What happened on Emily's phone, Sunday 2026-09-13 (server log 19:51 UTC:
"Self-service reset: meal_plan=10 grocery_list=54"): she had just approved
Mon–Sun (66 lines on the list), tapped Start over with both boxes, and the
reset cleared LAST week's dying draft — the plan covering today, which is
what tools.clear_weekly_plan() resolves to when no plan is named — while
the week on screen kept all its meals and lost every grocery line to the
list clear. The plan and the list disagreed from then on.

Pinned here: the dialog's preview and the reset both take the plan the tab
is showing, the preview names that week, and — the regression — an
approved plan for next week beside a draft for this week clears next
week's plan, not this week's.
"""
import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import reset as _reset
from app.tools import weekly_plan as _weekly_plan

SUNDAY = "2026-09-13"
THIS_MONDAY = "2026-09-07"
NEXT_MONDAY = "2026-09-14"


class _FixedToday(datetime.date):
    _value = None

    @classmethod
    def today(cls):
        return cls._value


@pytest.fixture
def sunday(monkeypatch):
    _FixedToday._value = datetime.date.fromisoformat(SUNDAY)
    monkeypatch.setattr(_weekly_plan, "date", _FixedToday)


def _meal_count(plan_id: int) -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)).fetchone()[0]
    conn.close()
    return n


def _plan_row(plan_id: int) -> dict:
    conn = get_conn()
    row = dict(conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone())
    conn.close()
    return row


def _needed() -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def _emilys_sunday() -> tuple[int, int]:
    """Last week's draft still covering today (two dinners), and next week's
    plan approved a minute ago (seven dinners of eggs on the list).
    Returns (dying_draft_id, approved_id)."""
    tools.add_member("Emily")
    tools.add_member("Sam")
    tools.add_recipe("Boiled eggs", ingredients=[{"item": "Eggs", "qty": "4"}], default_servings=2)
    tools.add_recipe("Chili", ingredients=[{"item": "Beans", "qty": "1 tin"}], default_servings=2)

    dying = tools.create_weekly_plan(THIS_MONDAY)["weekly_plan_id"]
    for day in ("2026-09-12", "2026-09-13"):
        tools.plan_meal(day, "Chili", slot="dinner", weekly_plan_id=dying)

    approved = tools.create_weekly_plan(NEXT_MONDAY)["weekly_plan_id"]
    for day in tools.period_dates(NEXT_MONDAY, 7):
        tools.plan_meal(day, "Boiled eggs", slot="dinner", weekly_plan_id=approved)
    tools.approve_weekly_plan(approved, "Emily")
    assert _needed()["Eggs"] == "3 dozen"  # 7 × 4 = 28 eggs
    return dying, approved


def test_the_default_resolver_is_last_weeks_draft_on_a_sunday(sunday):
    # The premise of the bug, stated so a change to _current_weekly_plan_row
    # that makes it go away is noticed.
    dying, approved = _emilys_sunday()
    assert tools.get_weekly_plan()["weekly_plan_id"] == dying


def test_the_reset_clears_the_plan_it_is_told_and_not_the_one_covering_today(sunday):
    dying, approved = _emilys_sunday()

    result = tools.clear_weekly_plan(approved)

    assert result["weekly_plan_id"] == approved
    assert result["meals_cleared"] == 7
    assert _meal_count(approved) == 0
    assert _plan_row(approved)["status"] == "draft"
    # The reversal took the week's groceries with it.
    assert "Eggs" not in _needed()
    # Last week's draft is exactly as it was.
    assert _meal_count(dying) == 2


def test_the_preview_counts_and_names_the_plan_it_is_told(sunday):
    dying, approved = _emilys_sunday()

    preview = tools.get_reset_preview(approved)
    assert preview["weekly_plan_id"] == approved
    assert preview["meal_count"] == 7
    assert preview["plan_status"] == "approved"
    assert preview["week_label"] == _weekly_plan._format_period_range(NEXT_MONDAY, 7)
    assert preview["grocery_count"] == 1

    # Unnamed, it still answers the old way — an older client gets the old
    # behaviour rather than an error — but says which week it counted.
    unnamed = tools.get_reset_preview()
    assert unnamed["weekly_plan_id"] == dying
    assert unnamed["meal_count"] == 2
    assert unnamed["week_label"] == _weekly_plan._format_period_range(THIS_MONDAY, 7)


def test_a_plan_of_another_household_is_not_resolved(sunday):
    dying, approved = _emilys_sunday()
    conn = get_conn()
    neighbour = conn.execute("INSERT INTO households (name) VALUES ('Next door')").lastrowid
    other = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (?, ?, 'approved')",
        (neighbour, NEXT_MONDAY),
    ).lastrowid
    conn.commit()
    conn.close()
    assert tools.get_reset_preview(other)["weekly_plan_id"] is None
    with pytest.raises(ValueError):
        tools.clear_weekly_plan(other)


def test_the_routes_carry_the_plan_from_preview_to_reset(sunday, signed_in):
    dying, approved = _emilys_sunday()

    preview = signed_in.get(f"/api/reset/preview?weekly_plan_id={approved}").json()
    assert preview["weekly_plan_id"] == approved
    assert preview["meal_count"] == 7

    res = signed_in.post(
        "/api/reset",
        json={"meal_plan": True, "grocery_list": True, "weekly_plan_id": preview["weekly_plan_id"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["meal_plan"]["weekly_plan_id"] == approved
    assert body["meal_plan"]["meals_cleared"] == 7
    assert _meal_count(approved) == 0
    assert _meal_count(dying) == 2
    assert _needed() == {}
