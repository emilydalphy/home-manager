"""
Undo after the "−" on a cook whose SIDE is also on the reheat it feeds
keeps the household's own lettuce (2026-11-09, Emily: protect the
household's number).

The week: a salad on the cook night and on the reheat it feeds, and a
hand-added "Lettuce 1 head" — a standing want the plan buys onto. Step the
cook down on the Review stepper: the cook moves onto the reheat's night,
the reheat row goes (taking its salad's head off the line), and the
post-commit rescale re-writes the line. Then Undo.

On main the "after" image plan_undo recorded was taken BEFORE that rescale
rewrote the line, so _restore_lines read the rewrite as a household edit
and left the reheat's head off — while restore still re-linked the reheat
to the line, and the Undo's own rescale then subtracted a share the line no
longer carried: out of the household's own head. Now _restore_lines names
the standing-want lines it declined to put back, and restore re-links
nothing to them (the Undo's rescale re-ingests the reheat's side anyway).
Trade-off, accepted: a hand-edited line may lose its link to a reheat's
share; its number never changes on Undo.
"""
from __future__ import annotations

import datetime

from conftest import household_today, on_the_weeks_monday

from app import tools
from app.db import get_conn
from app.tools import plates as _plates

pytestmark = on_the_weeks_monday()


def _day(offset: int) -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=offset)).isoformat()


SALAD = {
    "name": "Green salad",
    "ingredients": [{"item": "Lettuce", "qty": "1 head", "category": "produce"}],
    "covers": ["vegetable"],
}


def _lines() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, source_weekly_plan_id IS NOT NULL AS plan FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' ORDER BY item, plan, quantity",
        (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [(r["item"], r["quantity"], bool(r["plan"])) for r in rows]


def _week():
    tools.add_member("Emily")
    tools.add_member("Jamie")
    tools.add_recipe(
        "Turkey Chili",
        ingredients=[{"item": "Ground turkey", "qty": "1.5 lbs", "category": "meat"}],
        default_servings=2,
    )
    plan = tools.create_weekly_plan(_day(0))["weekly_plan_id"]
    cook = tools.plan_meal(_day(0), "Turkey Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    reheat = tools.plan_meal(_day(3), "Turkey Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    assert isinstance(tools.set_cook_ahead(cook, [reheat]), dict)
    _plates.attach_sides(cook, [SALAD], ["vegetable"])
    _plates.attach_sides(reheat, [SALAD], ["vegetable"])
    tools.add_grocery_item("Lettuce", "1 head", category="produce")
    tools.approve_weekly_plan(plan, approved_by="Emily")
    return plan, cook, reheat


def _lettuce() -> list[tuple]:
    return [l for l in _lines() if l[0] == "Lettuce"]


def test_undo_after_the_minus_on_the_cook_puts_every_line_back():
    """CATCH — the card's repro: the lettuce read one head short after Undo."""
    plan, cook, _reheat = _week()
    before = _lines()
    assert ("Lettuce", "4 heads", False) in before, "2 for the batch + 1 reheat + the household's 1"
    out = tools.drop_dish_from_day(plan, cook)
    assert out["status"] == "dropped" and out.get("can_undo"), out
    assert ("Lettuce", "2 heads", False) in _lines(), "the '−' has to change the line"
    assert tools.drop_dish_undo(plan, out["undo_entry_id"])["status"] == "restored"
    assert _lines() == before  # main: Lettuce 3 heads


def test_clearing_the_cook_night_after_the_undo_keeps_the_households_own_head():
    """CATCH (follow-on) — on main the reheat's head stayed on the ledger
    of a line that no longer carried it, so the next reversal took the
    household's own head instead."""
    plan, cook, reheat = _week()
    out = tools.drop_dish_from_day(plan, cook)
    assert tools.drop_dish_undo(plan, out["undo_entry_id"])["status"] == "restored"
    tools.clear_plan_slot(plan, _day(0), "dinner")
    lettuce = _lettuce()
    # The household's own head + the reheat's salad (main: 1 head).
    assert lettuce == [("Lettuce", "2 heads", False)], lettuce


def test_removing_a_side_between_the_answer_and_its_undo_refuses_the_undo():
    """sides_json is fingerprinted: a side taken off a night the answer
    touched means the Undo refuses rather than putting the side back."""
    plan, cook, _reheat = _week()
    out = tools.drop_dish_from_day(plan, cook)
    # The cook now stands on the reheat's night; take its salad off.
    conn = get_conn()
    moved = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner' "
        "AND slot_state = 'planned'", (tools.household_id(), _day(3))).fetchone()["id"]
    conn.close()
    assert tools.remove_component(moved, "Green salad", plan).get("status") != "gone"
    after_removal = _lines()
    res = tools.drop_dish_undo(plan, out["undo_entry_id"])
    assert res["status"] == "refused", res
    assert _lines() == after_removal
    assert _plates.get_sides(moved) == []
