"""
A plan line corrected into the other measuring system keeps the
household's number through a dropped night (2026-10-18, overnight hunt in
the Shop tab).

Repro, through the real routes on a throwaway database: two curries and a
stir-fry put "chicken breast · 4 lbs" on the list (1.5 + 1.5 + 1). The
household corrects it to "3 kg" — what the pack says, about 6.6 lbs, extra
for the freezer — and drops one curry night. The line read "2.5 lbs": kg
and lb did not convert, so the edit was never recorded beside the line
(grocery._household_edit_columns answered None) and the recompute read
only the meals. A same-system edit ("6.5 lbs") has kept its extra since
2026-10-13.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import quantities


def _next_week(day: int) -> str:
    today = household_today()
    return (today - timedelta(days=today.weekday()) + timedelta(days=7 + day)).isoformat()


def _line(item: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, quantity, kept_amount, short_amount FROM grocery_items WHERE item = ?", (item,)
    ).fetchone()
    conn.close()
    return dict(row)


def _week() -> tuple[int, list[int]]:
    tools.add_recipe(
        "Curry",
        ingredients=[{"item": "chicken breast", "qty": "1.5 lb"}, {"item": "rice", "qty": "1 cup"}],
        default_servings=4,
    )
    tools.add_recipe(
        "Stir Fry",
        ingredients=[{"item": "chicken breast", "qty": "1 lb"}, {"item": "rice", "qty": "2 cups"}],
        default_servings=4,
    )
    plan_id = tools.create_weekly_plan(_next_week(0))["weekly_plan_id"]
    entries = [
        tools.plan_meal(_next_week(d), name, slot="dinner", weekly_plan_id=plan_id)["entry_id"]
        for d, name in ((1, "Curry"), (2, "Stir Fry"), (3, "Curry"))
    ]
    tools.approve_weekly_plan(plan_id, "Alex")
    assert _line("chicken breast")["quantity"] == "4 lbs"
    return plan_id, entries


def _edit(client, item: str, quantity: str) -> None:
    line_id = _line(item)["id"]
    assert client.post(f"/api/grocery-list/{line_id}/update", json={"quantity": quantity}).status_code == 200


def test_a_kg_correction_keeps_its_extra_through_a_dropped_night(signed_in):
    plan_id, entries = _week()
    _edit(signed_in, "chicken breast", "3 kg")
    tools.drop_dish_from_day(plan_id, entries[2])
    # Meals left want 2.5 lbs; the household's extra (3 kg less the 4 lbs
    # its meals wanted, ~2.6 lbs) rides on top, as a "6.5 lbs" edit's would.
    assert _line("chicken breast")["quantity"] == "5 lbs"


def test_the_same_edit_in_pounds_reads_the_same(signed_in):
    """The bar the kg edit is held to — and a guard that the bridge changed
    nothing for an edit that never needed it."""
    plan_id, entries = _week()
    _edit(signed_in, "chicken breast", "6.5 lbs")
    tools.drop_dish_from_day(plan_id, entries[2])
    assert _line("chicken breast")["quantity"] == "5 lbs"


def test_mass_never_converts_to_volume(signed_in):
    plan_id, entries = _week()
    _edit(signed_in, "rice", "1 kg")
    line = _line("rice")
    assert (line["kept_amount"], line["short_amount"]) == ("", "")
    assert quantities._convert_for_household_edit(1, "kg", "cup") is None
    assert quantities._convert_for_household_edit(1, "cup", "g") is None


def test_the_bridges_are_exact():
    assert quantities._convert_for_household_edit(1, "kg", "lb") == pytest.approx(2.20462, rel=1e-5)
    assert quantities._convert_for_household_edit(1, "lb", "g") == pytest.approx(453.592, rel=1e-5)
    assert quantities._convert_for_household_edit(1, "l", "cup") == pytest.approx(4.22675, rel=1e-5)
    assert quantities._convert_for_household_edit(1, "cup", "ml") == pytest.approx(236.588, rel=1e-5)
