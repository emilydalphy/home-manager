"""
A household's own amount on a grocery line survives a "−" on a batch-cooked
dish and its Undo — and a chain swap (2026-10-07, overnight review).

Found by a reviewer running scenario scripts on main: a household of four,
Beef Ragu (12 oz ground beef, 1 onion) on several nights, and a hand-added
"Onion 2" on the list. Step the COOK night down on the Review stepper — the
cook moves onto the night it fed and the batch shrinks
(weekly_plan._drop_by_cooking_on_the_fed_night, then
_rescale_after_a_chain_moved) — and tap Undo. The onions read 4 before the
"−" and 2 after the Undo: the household's own two were gone (5 -> 3 over
three nights; 4 -> 3 when the chain was made after approval).

Why: plan_undo.restore put the snapshot's grocery links back BESIDE the
links the post-"−" rescale had written for the same cook, so the Undo's own
rescale took the old batch's share off a line that no longer carried it.
A plan line hides that (it recomputes from its ledger); a standing want
cannot, it subtracts (grocery._restate_standing_want) — out of the
household's own amount.

Card B's swap (replace_dish_on_days across a cooked-double chain) is pinned
here too, in both orders. It did NOT reproduce as a loss: chained after
approval, the 8 oz stays on its own line and Friday's 12 oz lands on the
plan's own line beside it ("8 oz" + "12 oz", 20 oz in all), where chained
before it is one "1.25 lbs" line. A list read as {item: quantity} shows
only one of two same-name lines, which is how that reads as "12 oz".
"""
from __future__ import annotations

import datetime

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _wp


TODAY = household_today()
WEEK_START = (TODAY - datetime.timedelta(days=1)).isoformat()


def D(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


def _lines() -> list[tuple]:
    """Every line still to buy, as (item, quantity, owned-by-a-plan) —
    a LIST, not a dict keyed by name: two same-name lines are the whole of
    what a dict would hide."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, source_weekly_plan_id IS NOT NULL AS plan FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' ORDER BY item, plan, quantity",
        (tools.household_id(),),
    ).fetchall()
    conn.close()
    return [(r["item"], r["quantity"], bool(r["plan"])) for r in rows]


def _dinner(day: str) -> int:
    conn = get_conn()
    r = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner' "
        "ORDER BY id DESC", (tools.household_id(), day)).fetchone()
    conn.close()
    return r["id"]


def _week(days: list[str], wants: list[tuple[str, str, str]]) -> tuple[int, list[int]]:
    for name in ("Emily", "Vineeth", "Asha", "Ravi"):
        tools.add_member(name)
    tools.add_recipe(
        "Beef Ragu",
        ingredients=[
            {"item": "Ground beef", "qty": "12 oz", "category": "meat"},
            {"item": "Onion", "qty": "1", "category": "produce"},
        ],
        instructions=["Cook."], prep_time_minutes=10, cook_time_minutes=20, default_servings=4,
    )
    plan = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day in days:
        tools.plan_meal(day, "Beef Ragu", slot="dinner", weekly_plan_id=plan)
    for item, qty, category in wants:
        tools.add_grocery_item(item, qty, category=category)
    return plan, [_dinner(d) for d in days]


def _chain(ids: list[int]) -> None:
    out = tools.set_cook_ahead(ids[0], ids[1:])
    assert not isinstance(out, str), out


WANTS = [("Onion", "2", "produce"), ("Ground beef", "8 oz", "meat")]


@pytest.mark.parametrize("nights, chain_first, onions", [
    (2, True, "4"),    # reported: 4 -> 2
    (3, True, "5"),    # reported: 5 -> 3
    (2, False, "4"),   # reported: 4 -> 3 (chained after approval)
])
def test_undoing_a_moved_cook_puts_every_line_back(nights, chain_first, onions):
    plan, ids = _week([D(1), D(4), D(5)][:nights], WANTS)
    if chain_first:
        _chain(ids)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    if not chain_first:
        _chain(ids)
    before = _lines()
    assert ("Onion", onions, False) in before, "the harness has to buy the batch onto the want"

    out = tools.drop_dish_from_day(plan, ids[0])
    assert out["status"] == "dropped" and out.get("can_undo"), out
    # The "−" really shrank the batch, so the Undo below is undoing a rescale.
    assert ("Onion", str(int(onions) - 1), False) in _lines()

    assert tools.drop_dish_undo(plan, out["undo_entry_id"])["status"] == "restored"
    assert _lines() == before


def test_a_second_drop_and_undo_does_not_drift():
    """The ledger the Undo leaves has to be one the NEXT "−" can read: a
    phantom share left on the cook would surface on the second round."""
    plan, ids = _week([D(1), D(4), D(5)], WANTS)
    _chain(ids)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    before = _lines()
    for _ in range(2):
        out = tools.drop_dish_from_day(plan, ids[0])
        assert tools.drop_dish_undo(plan, out["undo_entry_id"])["status"] == "restored"
        assert _lines() == before


def test_a_draft_week_drop_and_undo_leaves_the_want_alone():
    """No rescale runs on a draft, so the restore's links are the
    snapshot's — the path the night off also takes. Nothing is bought yet,
    and the household's own lines must read exactly as typed."""
    plan, ids = _week([D(1), D(4)], WANTS)
    _chain(ids)
    before = _lines()
    out = tools.drop_dish_from_day(plan, ids[0])
    assert tools.drop_dish_undo(plan, out["undo_entry_id"])["status"] == "restored"
    assert _lines() == before == [("Ground beef", "8 oz", False), ("Onion", "2", False)]


@pytest.mark.parametrize("chain_first, expected_beef", [
    (True, [("Ground beef", "1.25 lbs", False)]),
    (False, [("Ground beef", "8 oz", False), ("Ground beef", "12 oz", True)]),
])
def test_a_chain_swap_keeps_the_households_own_amount(chain_first, expected_beef):
    """Card B: three nights chained, the cook and the first reheat swapped
    to another dish, Friday left behind to cook for itself. The 8 oz the
    household asked for is still on the list in BOTH orders — on its own
    line beside the plan's 12 oz when the chain came after approval, merged
    with it when it came before (oz onto lbs does not add up cleanly,
    grocery._merge_target, so the plan's line at approval stood apart)."""
    days = [D(1), D(4), D(5)]
    plan, ids = _week(days, WANTS)
    tools.add_recipe(
        "Chicken Curry", ingredients=[{"item": "Chicken thighs", "qty": "1 lb", "category": "meat"}],
        instructions=["Cook."], prep_time_minutes=10, cook_time_minutes=20, default_servings=4,
    )
    if chain_first:
        _chain(ids)
    tools.approve_weekly_plan(plan, approved_by="Emily")
    if not chain_first:
        _chain(ids)

    _wp.replace_dish_on_days(plan, [
        {"old_entry_id": ids[i], "date": days[i], "slot": "dinner", "new_meal": "Chicken Curry",
         "food_groups": [], "reasoning": "swap", "derived_from": {}}
        for i in range(2)
    ])

    lines = _lines()
    assert [l for l in lines if l[0] == "Ground beef"] == expected_beef
    # And the onions: the household's 2 plus Friday's own 1.
    assert ("Onion", "3", False) in lines
