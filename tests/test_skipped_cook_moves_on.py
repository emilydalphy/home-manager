"""
"We skipped it" on a dinner cooked double moves the dish to the night it
was feeding (Loop Board, Emily's option A, 2026-10-12).

Found 2026-10-10: Friday's tacos cooked double for Saturday, answered
"skipped" on Saturday morning — Today, the morning text and the reheat
move all still said "Tacos, leftovers from Friday", a batch nobody cooked.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import leftovers, moves


def _plan(days: int = 3):
    """An approved plan from yesterday through tomorrow (household clock)."""
    today = household_today()
    yday = today - datetime.timedelta(days=1)
    plan = tools.create_weekly_plan(yday.isoformat(), day_count=days)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan,))
    conn.commit()
    conn.close()
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)
    return plan, today, yday.isoformat()


def _chain(plan, yday, fed_days):
    keys = [f"{d}:dinner" for d in fed_days]
    cook = tools.plan_meal(yday, "Tacos", slot="dinner", weekly_plan_id=plan,
                           derived_from={"make_double_for": keys})["entry_id"]
    fed = [tools.plan_meal(d, "Tacos", slot="dinner", weekly_plan_id=plan,
                           derived_from={"links_to": f"{yday}:dinner"})["entry_id"] for d in fed_days]
    chains = leftovers.plan_leftover_chains(plan)
    assert set(chains["leftovers"]) == set(fed), "premise: a real cook-once-eat-twice chain"
    return cook, fed


def _rows(plan, day):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, slot_state, recipe_id, skipped_at, reasoning, derived_from_json FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner'", (plan, day))]
    conn.close()
    return rows


def test_skipping_the_double_cook_moves_the_dish_to_the_night_it_fed():
    plan, today, yday = _plan()
    cook, (saturday,) = _chain(plan, yday, [today.isoformat()])

    out = tools.answer_yesterday(cook, "skipped", today)

    assert out["moved_to"]["date"] == today.isoformat()
    # Tonight is the cook now — the dish itself, not a reheat of it.
    [tonight] = _rows(plan, today.isoformat())
    assert tonight["id"] == cook and tonight["slot_state"] == "planned"
    assert tonight["skipped_at"] is None
    assert leftovers.plan_leftover_chains(plan)["leftovers"] == {}
    assert "make_double_for" not in json.loads(tonight["derived_from_json"] or "{}")
    payload = moves.today_moves(day=today.isoformat(),
                                now=datetime.datetime.combine(today, datetime.time(8, 0)))
    dinner = [m for m in payload["moves"] if m["slot"] == "dinner"]
    assert [m["kind"] for m in dinner] == ["cook"]
    assert "leftovers" not in dinner[0]["title"].lower()
    # Yesterday is never an absent slot: an empty night saying where it went.
    [last_night] = _rows(plan, yday)
    assert last_night["slot_state"] == "planned_empty"
    assert "moved to" in last_night["reasoning"]
    audit = tools.audit_plan_slots(plan, day_count=3)
    assert not [d for d in audit["duplicated"] if d["slot"] == "dinner"]
    # Answered: the card has nothing left to ask.
    assert out["meals"] == []


def test_a_second_fed_night_now_eats_from_the_new_cook_night():
    plan, today, yday = _plan()
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    cook, (_sat, sunday) = _chain(plan, yday, [today.isoformat(), tomorrow])

    tools.answer_yesterday(cook, "skipped", today)

    chains = leftovers.plan_leftover_chains(plan)
    assert list(chains["leftovers"]) == [sunday]
    assert chains["leftovers"][sunday]["source"]["entry_id"] == cook
    assert chains["leftovers"][sunday]["source"]["date"] == today.isoformat()


def test_an_ordinary_dinner_skipped_is_only_stamped():
    plan, today, yday = _plan()
    entry = tools.plan_meal(yday, "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    out = tools.answer_yesterday(entry, "skipped", today)
    assert "moved_to" not in out
    [row] = _rows(plan, yday)
    assert row["id"] == entry and row["skipped_at"] is not None


def _thaw(plan, entry_id, task_date):
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id) VALUES (?, ?, ?, ?, 'Tacos', 'pending', 'defrost', ?)",
        (tools.household_id(), plan, task_date,
         "Move the ground beef to the fridge — for Monday's Tacos.", entry_id),
    )
    conn.commit()
    conn.close()


def _thaw_dates(entry_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT task_date FROM prep_tasks WHERE meal_plan_entry_id = ? AND task_type = 'defrost' "
        "AND status = 'pending'", (entry_id,)).fetchall()
    conn.close()
    return [r["task_date"] for r in rows]


def test_the_moved_cooks_fridge_move_is_due_today_and_said(monkeypatch):
    """Review catch: the thaw travelled +1 day with the cook and landed on
    YESTERDAY (a thaw is never dated on the cook's day), so nothing said to
    take the beef out before "Tap to start"."""
    from app.tools import defrost
    monkeypatch.setattr(defrost, "sync_defrost_tasks", lambda plan_id: {})
    plan, today, yday = _plan()
    cook, _fed = _chain(plan, yday, [today.isoformat()])
    before_yday = (today - datetime.timedelta(days=2)).isoformat()
    _thaw(plan, cook, before_yday)

    out = tools.answer_yesterday(cook, "skipped", today)

    assert _thaw_dates(cook) == [today.isoformat()]
    assert out["thaw_now"] == ["Move the ground beef to the fridge today."]


def test_undo_puts_both_nights_back_and_asks_again():
    plan, today, yday = _plan()
    cook, (fed,) = _chain(plan, yday, [today.isoformat()])
    before = {d: _rows(plan, d) for d in (yday, today.isoformat())}

    out = tools.answer_yesterday(cook, "skipped", today)
    assert out["can_undo"] is True
    back = tools.undo_skipped_move(out["undo_entry_id"])

    assert back["status"] == "restored"
    assert {d: _rows(plan, d) for d in (yday, today.isoformat())} == before
    assert set(leftovers.plan_leftover_chains(plan)["leftovers"]) == {fed}
    assert [m["entry_id"] for m in tools.yesterday_check(today)["meals"]] == [cook]
    # Once only.
    assert tools.undo_skipped_move(out["undo_entry_id"])["status"] == "refused"


def test_the_screen_says_where_it_went_refreshes_and_offers_undo():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text(encoding="utf-8")
    start = js.index("async function answerYesterday(")
    body = js[start:js.index("async function undoYesterdaySkip(", start)]
    assert "out.moved_to" in body and "' moved to '" in body and "out.thaw_now" in body
    assert "label: 'Undo'" in body and "undoYesterdaySkip(panel" in body
    assert "refreshTodayMoves()" in body
    assert "refreshStaleTabsFromActions([{ tab: 'week' }, { tab: 'kitchen' }])" in body
    undo = js[js.index("async function undoYesterdaySkip("):]
    undo = undo[:undo.index("\n  }\n") + 4]
    assert "/api/today/yesterday/undo" in undo and "loadYesterdayCheck(panel)" in undo


def test_the_undo_route(signed_in):
    plan, today, yday = _plan()
    cook, _fed = _chain(plan, yday, [today.isoformat()])
    out = signed_in.post("/api/today/yesterday/answer", json={"entry_id": cook, "answer": "skipped"}).json()
    res = signed_in.post("/api/today/yesterday/undo", json={"undo_entry_id": out["undo_entry_id"]})
    assert res.status_code == 200 and res.json()["status"] == "restored"
