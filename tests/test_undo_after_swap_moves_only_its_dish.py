"""
A Move's Undo, and a nights swap's Undo, carry back only the dish they
moved (overnight 2026-10-24).

Found on a throwaway DB: move Tuesday's dinner onto an open Thursday, Swap
Thursday's dish in place (Beef Bulgogi now), then tap the Move's Undo. It
carried Beef Bulgogi back to Tuesday and said "Back as it was". The swap
writes a NEW row and copies the old row's derived_from onto it — the Move's
Undo token (derived_from.moved_from) with it — so the Undo found its token
on a dish it never moved. The nights swap's Undo reads the same token and
had the same hole.

The token now names the row and the dish it moved (_redate_plan_rows), and
both Undos refuse while the token sits on a different row holding a
different dish (weekly_plan.moved_dish_changed). Swap, then Undo the swap,
and the Move's Undo works again: the dish it moved is back. The same row
under a new name (a recipe edited in place) still undoes.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import meal_move
from app.tools import swap_in_place as sip


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)


def _recipe(name: str) -> None:
    tools.add_recipe(name, ingredients=[{"item": f"{name} onion", "qty": "1", "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=20, default_servings=4)


def _dinners() -> dict[str, list[str]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.slot = 'dinner' ORDER BY mpe.date, mpe.id",
        (tools.household_id(),),
    ).fetchall()
    conn.close()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["date"], []).append(r["meal"] if r["slot_state"] == "planned" else r["slot_state"])
    return out


def _entry_on(plan: int, day: str) -> int:
    conn = get_conn()
    r = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND weekly_plan_id = ? "
        "AND date = ? AND slot = 'dinner' AND slot_state = 'planned'",
        (tools.household_id(), plan, day),
    ).fetchone()
    conn.close()
    return r["id"]


def _week() -> dict:
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    dishes = {MON: "Chicken Stew", TUE: "Tacos", WED: "Pasta Bake", FRI: "Curry"}
    for d in list(dishes.values()) + ["Beef Bulgogi"]:
        _recipe(d)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {day: tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)["entry_id"]
           for day, dish in dishes.items()}
    return {"plan": plan, "ids": ids}


def _swap(plan: int, day: str, to: str) -> dict:
    return sip.apply_pick(plan, sip._entry(plan, _entry_on(plan, day)),
                          {"meal_name": to, "food_groups": [], "reason": "Something else."},
                          correct_title=False)


def test_a_moves_undo_refuses_once_the_moved_dish_has_been_swapped():
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], THU)
    assert out["status"] == "moved"
    _swap(w["plan"], THU, "Beef Bulgogi")
    before = _dinners()

    with pytest.raises(ValueError, match="changed since"):
        meal_move.undo_meal_move(w["plan"], out["move_id"])

    assert _dinners() == before, "a refused Undo writes nothing"
    assert before[THU] == ["Beef Bulgogi"]


def test_swap_then_undo_the_swap_and_the_moves_undo_works_again():
    """Only what the Move did: once the swap is taken back, the dish the
    Move moved is the one on the row, and its Undo is honest again."""
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], THU)
    swapped = _swap(w["plan"], THU, "Beef Bulgogi")
    sip.undo_meal_swap(w["plan"], swapped["entry_id"])

    u = meal_move.undo_meal_move(w["plan"], out["move_id"])

    assert u["status"] == "restored"
    assert _dinners()[TUE] == ["Tacos"]
    assert THU not in _dinners()


def test_a_nights_swaps_undo_refuses_once_one_dinner_has_been_swapped():
    w = _week()
    tools.swap_dinner_nights(w["plan"], TUE, WED)
    _swap(w["plan"], WED, "Beef Bulgogi")  # Tacos is on Wednesday now
    before = _dinners()

    with pytest.raises(ValueError, match="changed since"):
        tools.undo_dinner_nights_swap(w["plan"], TUE, WED)

    assert _dinners() == before
    assert before[TUE] == ["Pasta Bake"] and before[WED] == ["Beef Bulgogi"]


def test_an_untouched_moves_undo_still_puts_it_back():
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], THU)
    u = meal_move.undo_meal_move(w["plan"], out["move_id"])
    assert u["status"] == "restored"
    assert _dinners()[TUE] == ["Tacos"]


def test_the_same_row_renamed_in_place_still_undoes():
    """Not every new name is a new dish: a recipe edited in place
    (recipe_change re-points the row's recipe_id) or a freezer night re-said
    for its cook's new day keeps the row. Only a swap writes a new one."""
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], THU)
    _recipe("Tacos (less salt)")
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET recipe_id = (SELECT id FROM recipes WHERE name = ?) WHERE id = ?",
        ("Tacos (less salt)", w["ids"][TUE]),
    )
    conn.commit()
    conn.close()

    u = meal_move.undo_meal_move(w["plan"], out["move_id"])

    assert u["status"] == "restored"
    assert _dinners()[TUE] == ["Tacos (less salt)"]
