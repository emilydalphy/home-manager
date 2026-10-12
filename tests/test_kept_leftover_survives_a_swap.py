"""
"Keep" on last week's leftover survives this week's meal changing
(2026-10-12, overnight Shop hunt).

    Last week's 2 lbs of chicken thighs went unbought. This week also has a
    stir fry wanting 2 lbs, so Shop asks "Still on the list from last week —
    keep or drop?". Keep: the line reads 4 lbs. Swap the stir fry for pasta
    and the chicken was gone from the list altogether — the 2 lbs the
    household had just said they still wanted with it.

keep_carried_over_item merged the kept amount onto this week's PLAN-owned
line, and a plan line is recomputed from its meals' ledger (and deleted with
the last of them). The kept amount is now recorded on the line
(grocery_items.kept_amount) and the reversal counts it in beside the ledger.
The line stays the plan's, so after its week it is set aside and asked about
again like any leftover (a first cut made it a standing want, and an
unbought line then climbed 2 → 4 → 6 lbs a week, owned by nobody — review,
2026-10-12). The keep's undo works on the exact row it went onto
(kept_into_item_id), never a same-named line found later.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery as _grocery
from conftest import household_today
from unittest import mock


def _approve(start: datetime.date, meal: str = "Stir Fry", night_offset: int = 2, today: datetime.date | None = None) -> dict:
    plan = tools.create_weekly_plan(start.isoformat(), day_count=7)["weekly_plan_id"]
    night = (start + datetime.timedelta(days=night_offset)).isoformat()
    tools.plan_meal(night, meal, slot="dinner", weekly_plan_id=plan)
    if today is None:
        res = tools.approve_weekly_plan(plan, approved_by="Emily")
    else:
        with mock.patch.object(_grocery, "_household_today", lambda conn=None: today):
            res = tools.approve_weekly_plan(plan, approved_by="Emily")
    return {"plan": plan, "night": night, "carried": res["carried_over"]}


@pytest.fixture
def recipes():
    tools.add_recipe("Stir Fry", ingredients=[{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"}])
    tools.add_recipe("Pasta", ingredients=[{"item": "Spaghetti", "qty": "1 lb", "category": "pantry"}])


@pytest.fixture
def weeks(recipes):
    today = household_today()
    _approve(today - datetime.timedelta(days=8), night_offset=3)
    this = _approve(today)
    assert [c["item"] for c in this["carried"]] == ["Chicken thighs"]
    return {"this": this["plan"], "night": this["night"], "carried_id": this["carried"][0]["item_id"]}


def _chicken(status: str = "needed") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, quantity, source_weekly_plan_id, kept_amount FROM grocery_items "
        "WHERE item = 'Chicken thighs' AND status = ? ORDER BY id", (status,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_catch_swapping_the_meal_leaves_the_kept_amount(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    assert [r["quantity"] for r in _chicken()] == ["4 lbs"]
    tools.swap_meal_in_plan(weeks["this"], weeks["night"], "Pasta", slot="dinner")
    assert [r["quantity"] for r in _chicken()] == ["2 lbs"], "the kept 2 lbs were asked for out loud"


def test_the_kept_line_stays_the_weeks(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    line = _chicken()[0]
    assert line["source_weekly_plan_id"] == weeks["this"]
    assert line["kept_amount"] == "2 lbs"


def test_catch_w1_w2_w3_the_kept_line_is_asked_about_again_not_stacked(recipes):
    # W1's 2 lbs unbought -> W2 keeps it (4 lbs) -> W2 goes unbought too ->
    # approving W3 asks keep-or-drop about the 4 lbs again, and W3's own
    # 2 lbs sit on a line of their own. Never one 6 lb line owned by nobody.
    today = household_today()
    w1_start = today - datetime.timedelta(days=14)
    w2_start = today - datetime.timedelta(days=7)
    _approve(w1_start, today=w2_start)
    w2 = _approve(w2_start, today=w2_start)
    tools.keep_carried_over_item(w2["carried"][0]["item_id"])
    assert [r["quantity"] for r in _chicken()] == ["4 lbs"]
    w3 = _approve(today)
    assert [(c["item"], c["quantity"]) for c in w3["carried"]] == [("Chicken thighs", "4 lbs")]
    assert [(r["quantity"], r["source_weekly_plan_id"]) for r in _chicken()] == [("2 lbs", w3["plan"])]


def test_undo_takes_the_kept_amount_back_off(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    tools.undo_carried_over_decision(weeks["carried_id"])
    assert [(r["quantity"], r["kept_amount"]) for r in _chicken()] == [("2 lbs", "")]
    # Plain plan line again: the meal going takes the line with it.
    tools.swap_meal_in_plan(weeks["this"], weeks["night"], "Pasta", slot="dinner")
    assert _chicken() == []


def test_catch_undo_works_on_the_row_the_keep_went_onto(weeks):
    # After the keep the 4 lbs are bought, and somebody types their own
    # "chicken thighs · 2 lbs" for another night. Undo is too late for the
    # bought line — and must not take the kept 2 lbs off the person's own
    # line instead (it used to re-find "this week's line" by name).
    tools.keep_carried_over_item(weeks["carried_id"])
    kept_line = _chicken()[0]["id"]
    tools.mark_grocery_item(kept_line, "purchased")
    mine = tools.add_grocery_item("Chicken thighs", quantity="2 lbs", category="meat/seafood")["item_id"]
    answer = tools.undo_carried_over_decision(weeks["carried_id"])
    assert answer.get("unchanged") is True and answer.get("reason") == "acted_on"
    assert [(r["id"], r["quantity"], r["source_weekly_plan_id"]) for r in _chicken()] == [(mine, "2 lbs", None)]
    assert tools.list_carried_over_items() == [], "not asked twice"


def test_undo_after_the_meal_went_leaves_no_blank_line(weeks):
    tools.keep_carried_over_item(weeks["carried_id"])
    tools.swap_meal_in_plan(weeks["this"], weeks["night"], "Pasta", slot="dinner")
    tools.undo_carried_over_decision(weeks["carried_id"])
    assert _chicken() == []
    assert [c["item"] for c in tools.list_carried_over_items()] == ["Chicken thighs"], "asked again"
