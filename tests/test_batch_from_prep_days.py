"""
Batch cooking is assumed from prep days (Emily, 2026-09-18, Loop Board
"Batch cooking is assumed from prep days").

The approval-time "Do you want to batch cook…" ask is gone from All set and
the Plan root. In its place, one rule at approval (cook_ahead.
apply_prep_day_batches, called from approve_weekly_plan): a dish on more
than one day in the same slot, in a household with at least one prep day,
is cooked once on its first day and the later days come from that batch —
the chain a "yes" on the old ask wrote. With no prep days nothing is
batched. Either way cook_ahead_asked_at is set so nothing re-asks, and the
approve route never answers with a cook-ahead ask.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today
from app import db, tools
from app.tools import cook_ahead


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(n: int) -> str:
    return (_monday() + datetime.timedelta(days=n)).isoformat()


WEEK = _monday().isoformat()
MON, TUE, WED, THU = _day(0), _day(1), _day(2), _day(3)


def _household():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)


def _eggs():
    tools.add_recipe(
        "Egg White Bites", ingredients=[{"item": "egg whites", "qty": "1 cup"}],
        default_servings=2, prep_time_minutes=10, cook_time_minutes=20,
        instructions=["Heat the oven.", "Bake 20 minutes."],
    )


def _plan(*meals):
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {}
    for date_str, dish, slot in meals:
        ids[date_str] = tools.plan_meal(date_str, dish, slot=slot, weekly_plan_id=plan_id)["entry_id"]
    return plan_id, ids


def _derived(entry_id):
    conn = db.get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


def _asked_at(plan_id):
    conn = db.get_conn()
    row = conn.execute("SELECT cook_ahead_asked_at FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return row["cook_ahead_asked_at"]


def _sunday_prep():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 90}])


# ---------- the rule ----------

def test_a_repeated_dish_with_a_prep_day_is_batched_from_its_first_day():
    _household()
    _eggs()
    _sunday_prep()
    plan_id, ids = _plan((MON, "Egg White Bites", "breakfast"), (TUE, "Egg White Bites", "breakfast"),
                         (WED, "Egg White Bites", "breakfast"))

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert out["prep_days"] is True and out["refused"] == []
    assert [a["dish"] for a in out["applied"]] == ["Egg White Bites"]
    assert out["applied"][0]["source_entry_id"] == ids[MON]
    assert out["applied"][0]["covered_entry_ids"] == sorted([ids[TUE], ids[WED]])
    # The exact chain shape a "yes" on the old ask wrote — both halves, so
    # plan_leftover_chains honours it and the cook view scales the batch.
    assert _derived(ids[MON])["make_double_for"] == [f"{TUE}:breakfast", f"{WED}:breakfast"]
    for d in (TUE, WED):
        assert _derived(ids[d])["links_to"] == f"entry_id:{ids[MON]}"
        assert _derived(ids[d])["cook_ahead"] is True
    chains = tools.plan_leftover_chains(plan_id)
    assert set(chains["leftovers"]) == {ids[TUE], ids[WED]}
    assert _asked_at(plan_id) is not None


def test_with_no_prep_days_nothing_is_batched_and_nothing_asks():
    _household()
    _eggs()
    plan_id, ids = _plan((MON, "Egg White Bites", "breakfast"), (TUE, "Egg White Bites", "breakfast"))

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert out == {"prep_days": False, "applied": [], "refused": []}
    assert "make_double_for" not in _derived(ids[MON])
    assert "links_to" not in _derived(ids[TUE])
    assert tools.plan_leftover_chains(plan_id)["leftovers"] == {}
    # ...and the question is still marked answered, so no surface re-asks.
    assert _asked_at(plan_id) is not None


def test_a_dish_planned_once_is_left_alone():
    _household()
    _eggs()
    _sunday_prep()
    plan_id, ids = _plan((MON, "Egg White Bites", "breakfast"))

    out = cook_ahead.apply_prep_day_batches(plan_id)

    assert out["applied"] == [] and out["refused"] == []
    assert "make_double_for" not in _derived(ids[MON])


# ---------- at approval ----------

def test_approval_applies_the_batch_and_never_returns_an_ask(signed_in):
    _household()
    _eggs()
    _sunday_prep()
    plan_id, ids = _plan((MON, "Egg White Bites", "breakfast"), (TUE, "Egg White Bites", "breakfast"))

    res = signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "approved"
    assert "cook_ahead" not in body and "ask" not in body
    assert _derived(ids[TUE])["links_to"] == f"entry_id:{ids[MON]}"
    assert _asked_at(plan_id) is not None
    # The week reads the later day as made ahead, and the screens' own
    # gate says the question is answered.
    menu = signed_in.get("/api/week-menu").json()
    assert menu["cook_ahead_asked_at"] is not None
    tue = [d for d in menu["days"] if d["date"] == TUE][0]
    assert tue["breakfast"]["leftover_from"] == {"date": MON, "meal": "Egg White Bites", "cook_ahead": True}
    assert signed_in.get(f"/api/week/{WEEK}/cook-ahead-items").json()["items"] == []


def test_approval_without_prep_days_batches_nothing_and_still_marks_asked(signed_in):
    _household()
    _eggs()
    plan_id, ids = _plan((MON, "Egg White Bites", "breakfast"), (TUE, "Egg White Bites", "breakfast"))

    res = signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})

    assert res.status_code == 200 and res.json()["status"] == "approved"
    assert "links_to" not in _derived(ids[TUE])
    assert _asked_at(plan_id) is not None


def test_a_re_approval_does_not_run_the_rule_again(signed_in):
    _household()
    _eggs()
    plan_id, ids = _plan((MON, "Egg White Bites", "breakfast"), (TUE, "Egg White Bites", "breakfast"))
    signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})
    # Prep days set AFTER the first approval: a no-op re-approval must not
    # quietly rewrite the week around them.
    _sunday_prep()

    res = signed_in.post(f"/api/week/{WEEK}/approve", json={"approved_by": "Emily"})

    assert res.json()["was_already_approved"] is True
    assert "links_to" not in _derived(ids[TUE])
