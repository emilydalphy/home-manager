"""
After "Start over" wipes the list, re-approving says "64 items ready to
shop" but rebuilds nothing (Bug, Phase 0 — Emily, 2026-09-13, from her
phone: "it's saying that my list is filled but it's not … the chat is
obviously not reading the shop list").

WHAT HAPPENED. The Meals tab's "Start over" (main.py /api/reset →
tools.clear_grocery_list) deletes every needed line and leaves the week
approved. The next "approve it" / "build my list" then hit
approve_weekly_plan's re-approval guard — correct for two adults tapping
at once, wrong here — which added nothing and handed back the ORIGINAL
approval's `groceries_added_count`. summarize_chat_actions read that as
news ("64 items ready to shop"), and the assistant, with nothing in the
result about the actual list, said "nothing new to add" over a Shop tab
holding one jar of allspice.

THE FIX, in three parts:
1. A re-approval whose first approval put lines on the list, and none of
   them remain in ANY state (spices aside — they have their own section
   and survive a clear), rebuilds the list from the same week and says so
   (`list_rebuilt`). One line left — bought, or removed by hand — and it
   stays the no-op it always was.
2. A plain no-op re-approval reports `list_needed_count` and a `note`
   describing the list as it is, so the assistant has something true to
   say.
3. The chat cards: a no-op re-approval gets one week card ("Already
   approved — nothing changed") and no grocery card; a rebuild gets a
   week card that says so plus the real count.
"""
from __future__ import annotations

import datetime
import json

from app import tools, main as app_main
from app.db import get_conn


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _new_plan(index: int) -> tuple[int, str, str]:
    """A household-unique recipe, plan and (far-future, so never a
    carry-over candidate) week — the same isolation tests/test_approve_race.py uses."""
    beans, onions = f"wbeans{index}", f"wonions{index}"
    tools.add_recipe(
        f"Wiped Chili {index}",
        ingredients=[{"item": beans, "qty": "1 tin"}, {"item": onions, "qty": "2"}],
    )
    week = (_monday() + datetime.timedelta(weeks=20 + index)).isoformat()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(week, f"Wiped Chili {index}", slot="dinner", weekly_plan_id=plan_id)
    return plan_id, beans, onions


def _rows(item: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, status, source_weekly_plan_id FROM grocery_items WHERE item = ?", (item,)
    ).fetchall()
    conn.close()
    return rows


def _receipt(plan_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT approved_by, approved_grocery_added FROM weekly_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------- the tool


def test_start_over_then_re_approve_puts_the_weeks_ingredients_back():
    """Emily's exact path: approve, Start over (list only), ask again."""
    plan_id, beans, onions = _new_plan(0)
    first = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert first["groceries_added_count"] == 2 and _rows(beans) and _rows(onions)

    wiped = tools.clear_grocery_list(status="needed")
    assert wiped["removed_count"] >= 2
    assert _rows(beans) == [] and _rows(onions) == []

    again = tools.approve_weekly_plan(plan_id, approved_by="Marcus")

    assert again["status"] == "approved"
    assert again["was_already_approved"] is True
    assert again["list_rebuilt"] is True
    assert again["groceries_added_count"] == 2
    assert sorted(n.lower() for n in again["groceries_added"]) == sorted([beans, onions])
    assert _rows(beans)[0]["quantity"] == "1 tin" and _rows(beans)[0]["status"] == "needed"
    assert _rows(onions)[0]["quantity"] == "2"
    # The yes that settled the week is unchanged — only the list is new.
    assert again["approved_by"] == "Emily"
    assert _receipt(plan_id)["approved_by"] == "Emily"


def test_a_rebuild_does_not_double_up_and_a_third_call_is_a_plain_no_op():
    plan_id, beans, onions = _new_plan(1)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.clear_grocery_list(status="needed")
    tools.approve_weekly_plan(plan_id, approved_by="Emily")

    third = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert third["was_already_approved"] is True and third["list_rebuilt"] is False
    assert third["groceries_added"] == []
    assert len(_rows(beans)) == 1 and _rows(beans)[0]["quantity"] == "1 tin"
    assert len(_rows(onions)) == 1 and _rows(onions)[0]["quantity"] == "2"


def test_a_list_the_household_is_still_working_from_is_left_alone():
    """One line bought, one removed by hand — that is a list in use, not a
    wiped one. Re-approving must not bring the removed line back."""
    plan_id, beans, onions = _new_plan(2)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE item = ?", (beans,))
    conn.execute("DELETE FROM grocery_items WHERE item = ?", (onions,))
    conn.commit()
    conn.close()

    again = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert again["was_already_approved"] is True and again["list_rebuilt"] is False
    assert again["groceries_added"] == []
    assert _rows(onions) == [], "the hand-removed line came back"
    assert _rows(beans)[0]["status"] == "purchased"


def test_a_no_op_re_approval_says_what_the_list_holds_right_now():
    plan_id, beans, onions = _new_plan(3)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.add_grocery_item("wmilk3", "1 L")
    conn = get_conn()
    needed = conn.execute(
        "SELECT COUNT(*) FROM grocery_items WHERE status = 'needed' AND excluded_from_list = 0"
    ).fetchone()[0]
    conn.close()

    again = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert again["list_rebuilt"] is False
    assert again["list_needed_count"] == needed
    assert str(needed) in again["note"]
    assert "already approved" in again["note"].lower()
    assert "nothing was added" in again["note"].lower()


def test_a_recipe_with_a_spice_in_it_still_rebuilds_the_whole_meal():
    """The verifier's case (2026-09-13), and Emily's: the Shop tab she
    photographed held one jar of allspice — a spice row (status 'spice',
    its own section) survives Start over and still carries the meal's
    ledger link, so the meal used to be skipped as already ingested. The
    rebuild reported success, added nothing, and wrote 0 to the receipt."""
    # is_spice reads the last word alone, so this is a spice AND unique.
    beans, allspice = "sbeans5", "test5 allspice"
    tools.add_recipe(
        "Spiced Chili 5",
        ingredients=[{"item": beans, "qty": "1 tin"}, {"item": allspice, "qty": "1 tsp"}],
    )
    week = (_monday() + datetime.timedelta(weeks=25)).isoformat()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(week, "Spiced Chili 5", slot="dinner", weekly_plan_id=plan_id)
    first = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _rows(beans) and _rows(allspice)[0]["status"] == "spice"

    tools.clear_grocery_list(status="needed")
    assert _rows(beans) == [] and _rows(allspice), "the spice row should survive the clear"

    again = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert again["list_rebuilt"] is True
    assert _rows(beans)[0]["quantity"] == "1 tin" and _rows(beans)[0]["status"] == "needed"
    assert len(_rows(allspice)) == 1 and _rows(allspice)[0]["status"] == "spice"
    assert again["groceries_added_count"] == first["groceries_added_count"]
    assert _receipt(plan_id)["approved_grocery_added"] == first["groceries_added_count"]


def test_a_have_it_line_does_not_stop_a_wiped_list_rebuilding():
    """"Have it" leaves a 'removed' row behind (with the meal's link). It is
    bookkeeping, not a list — a household that wiped the list after a
    pre-shop check still gets its week back, and the ingest's own
    already-have skip decides whether that line returns."""
    plan_id, beans, onions = _new_plan(6)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.move_grocery_item_to_inventory(_rows(onions)[0]["id"])
    assert _rows(onions)[0]["status"] == "removed"
    tools.clear_grocery_list(status="needed")
    assert _rows(beans) == []

    again = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert again["list_rebuilt"] is True
    assert _rows(beans)[0]["status"] == "needed"
    assert not any(r["status"] == "removed" for r in _rows(onions))


def test_a_standing_line_the_week_merged_onto_counts_by_the_ledger_not_the_plan_id():
    """Verifier, second pass (2026-09-13): "add eggs to the list" in chat,
    then approve a week whose recipe uses eggs — the plan adds quantity to
    the hand-added line but never takes ownership of it (source stays
    NULL, grocery.py's keep_standing), yet the meal is linked to it. Two
    things follow. Bought (or "have it") before Start over, that line is a
    live line the household is working from → no rebuild, exactly like a
    plan-owned one. And once it IS wiped with everything else, its link
    must not hide the meal from the rebuild — which used to skip the whole
    meal and write 0 to the receipt, locking the week out for good."""
    eggs, flour = "seggs7", "sflour7"
    tools.add_grocery_item(eggs, "6")
    tools.add_recipe("Pancakes 7", ingredients=[{"item": eggs, "qty": "2"}, {"item": flour, "qty": "1 cup"}])
    week = (_monday() + datetime.timedelta(weeks=27)).isoformat()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    tools.plan_meal(week, "Pancakes 7", slot="dinner", weekly_plan_id=plan_id)
    first = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _rows(eggs)[0]["source_weekly_plan_id"] is None, "the plan must not take the standing line"
    assert _rows(flour)

    # Bought the eggs, then Start over: a list in use, not a wiped one.
    tools.mark_grocery_item(_rows(eggs)[0]["id"], status="purchased")
    tools.clear_grocery_list(status="needed")
    assert _rows(flour) == [] and _rows(eggs)[0]["status"] == "purchased"
    held = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert held["list_rebuilt"] is False and held["groceries_added"] == []
    assert _receipt(plan_id)["approved_grocery_added"] == first["groceries_added_count"]

    # Now genuinely wiped (the bought eggs gone too): the meal comes back
    # whole, standing-line ingredient included.
    conn = get_conn()
    conn.execute("DELETE FROM grocery_items WHERE item = ?", (eggs,))
    conn.commit()
    conn.close()
    again = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert again["list_rebuilt"] is True
    assert _rows(flour)[0]["quantity"] == "1 cup"
    # Buying the eggs put them in the pantry, so the ingest's own
    # already-have skip keeps them off the list — the meal was still
    # considered, which is the point.
    assert eggs in {n.lower() for n in again["already_have_skipped"]}
    # The receipt describes the list that now exists: one line put back.
    assert again["groceries_added_count"] == 1
    assert _receipt(plan_id)["approved_grocery_added"] == 1


def test_a_rebuild_that_puts_nothing_back_keeps_the_first_receipt():
    """Belt and braces: if the ingest re-adds nothing (say the pantry now
    has it all), the receipt must not drop to 0 — that would fail the
    wiped test forever after."""
    plan_id, beans, onions = _new_plan(8)
    first = tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.clear_grocery_list(status="needed")
    tools.update_inventory(beans, "add", quantity="3 tins")
    tools.update_inventory(onions, "add", quantity="5")

    again = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert again["list_rebuilt"] is True and again["groceries_added_count"] == 0
    assert _receipt(plan_id)["approved_grocery_added"] == first["groceries_added_count"]


def test_a_week_whose_first_approval_added_nothing_never_rebuilds():
    """The guard's original reason (see approve_weekly_plan's docstring):
    a week whose ingredients were all skipped as already-in-the-pantry
    leaves no lines, and an empty list must not read as a wiped one."""
    plan_id, beans, onions = _new_plan(4)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET approved_grocery_added = 0 WHERE id = ?", (plan_id,))
    conn.execute("DELETE FROM grocery_items WHERE source_weekly_plan_id = ?", (plan_id,))
    conn.commit()
    conn.close()

    again = tools.approve_weekly_plan(plan_id, approved_by="Emily")

    assert again["list_rebuilt"] is False and again["groceries_added"] == []
    assert _rows(beans) == []


# --------------------------------------------------------------- the cards


def _cards_for(result: dict):
    before, after = [], [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "approve_weekly_plan", "input": {"weekly_plan_id": 1}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": json.dumps(result), "is_error": False},
        ]},
    ]
    return {a.tab: a.change for a in app_main.summarize_chat_actions(before, after)}


def test_a_no_op_re_approval_gets_no_grocery_card_and_an_honest_week_card():
    cards = _cards_for({
        "status": "approved", "was_already_approved": True, "list_rebuilt": False,
        "groceries_added_count": 64, "list_needed_count": 1,
    })
    assert cards == {"week": "Already approved — nothing changed"}


def test_a_rebuild_says_so_and_carries_the_real_count():
    cards = _cards_for({
        "status": "approved", "was_already_approved": True, "list_rebuilt": True,
        "groceries_added_count": 64,
    })
    assert cards == {"week": "Week already approved — list rebuilt", "grocery": "64 items ready to shop"}


def test_a_first_approval_is_unchanged():
    cards = _cards_for({
        "status": "approved", "was_already_approved": False, "list_rebuilt": False,
        "groceries_added_count": 64,
    })
    assert cards == {"week": "Week approved — your list is ready", "grocery": "64 items ready to shop"}


def test_an_older_result_without_the_new_fields_still_reads_as_a_first_approval():
    cards = _cards_for({"status": "approved", "groceries_added_count": 3})
    assert cards == {"week": "Week approved — your list is ready", "grocery": "3 items ready to shop"}
