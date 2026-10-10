"""
The "first meal back" double-batch suggestion follows the dinner it names.

1. Swapped (Feature, Phase 1 — Beta; Emily, 2026-10-03, in chat): when the
   dinner Pomona suggested doubling is swapped for another dish, the
   suggestion MOVES to the new dish instead of being cleared (main 6b70856
   cleared it). A confirmed double stays confirmed — the new dish is the one
   to cook double. Deletes still clear it.

2. Moved (Bug, Phase 1 — Beta, card "After a night off moves the dinner to
   a later night..."): a night off that moves the recommended dinner to a
   night on or after the meal it covers recomputes the suggestion (the
   card's default) instead of leaving it naming a later dinner.

Every test is a CATCH — red on main 6b70856 — unless its docstring says GUARD.
"""
from __future__ import annotations

import datetime

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import slot_needs, weekly_plan
from app.tools import tonight as _tonight

START = household_today()
DINNER_DAY = (START + datetime.timedelta(days=1)).isoformat()


# ------------------------------------------------------------ 1. swapped

def _approved_week_with_alex_away() -> tuple[int, int, dict]:
    """Two adults, an approved chili on DINNER_DAY, Alex away for it, so
    the first meal back suggests doubling the chili. Returns (plan, chili
    entry, the ready-made edge)."""
    tools.add_member("Alex")
    tools.add_member("Sam")
    tools.add_recipe(
        "Bean Chili",
        ingredients=[{"item": "Black beans", "qty": "1 can"}, {"item": "Onion", "qty": "1"}],
        default_servings=2,
    )
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "8"}], default_servings=2)
    plan_id = tools.create_weekly_plan(START.isoformat())["weekly_plan_id"]
    entry_id = tools.plan_meal(DINNER_DAY, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    slot_needs.set_away_stretch(DINNER_DAY, "dinner", DINNER_DAY, "dinner", member_names=["Alex"])
    conn = get_conn()
    edge = dict(conn.execute(
        "SELECT date, slot FROM slot_needs WHERE household_id = ? AND recommended_batch_from_entry_id = ?",
        (tools.household_id(), entry_id),
    ).fetchone())
    conn.close()
    return plan_id, entry_id, edge


def _dinner_id(day: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner' "
        "AND component_category IS NULL",
        (tools.household_id(), day),
    ).fetchone()
    conn.close()
    return row["id"]


def _needed() -> dict[str, str]:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def test_the_everyday_swap_moves_the_suggestion_to_the_new_dish():
    plan_id, chili_id, edge = _approved_week_with_alex_away()

    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Tacos", slot="dinner")

    tacos_id = _dinner_id(DINNER_DAY)
    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["need"] == "ready_made"
    assert need["recommended_batch_from_entry_id"] == tacos_id
    said = slot_needs.describe_ready_made(edge["date"], edge["slot"])
    assert said and "tacos" in said["label"]
    assert not said["confirmed"], "an unconfirmed suggestion stays a suggestion"


def test_a_confirmed_double_stays_confirmed_on_the_new_dish():
    plan_id, _chili_id, edge = _approved_week_with_alex_away()
    slot_needs.confirm_slot_recommendation(edge["date"], edge["slot"])

    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Tacos", slot="dinner")

    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["recommended_batch_from_entry_id"] == _dinner_id(DINNER_DAY)
    assert need["recommendation_confirmed"]


def test_the_multi_day_swap_moves_a_confirmed_suggestion_too():
    plan_id, chili_id, edge = _approved_week_with_alex_away()
    slot_needs.confirm_slot_recommendation(edge["date"], edge["slot"])

    out = weekly_plan.replace_dish_on_days(plan_id, [
        {"old_entry_id": chili_id, "date": DINNER_DAY, "slot": "dinner", "new_meal": "Tacos"},
    ])

    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["recommended_batch_from_entry_id"] == out["entry_ids"][0]
    assert need["recommendation_confirmed"]


def test_groceries_follow_the_swap_the_same_way_they_did_before():
    """GUARD — green on both sides. The new dish buys exactly what it would on any swap, and the old dish's
    lines go. A confirmed batch suggestion scales nothing on the list (it
    never has — the list scales by leftover chains), so following the swap
    must not start to."""
    plan_id, _chili_id, edge = _approved_week_with_alex_away()
    slot_needs.confirm_slot_recommendation(edge["date"], edge["slot"])

    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Tacos", slot="dinner")

    needed = _needed()
    assert "Black beans" not in needed and "Onion" not in needed
    assert "Tortillas" in needed
    # Same amount as an unconfirmed swap of the same week buys.
    before_tortillas = needed["Tortillas"]
    conn = get_conn()
    conn.execute("UPDATE slot_needs SET recommendation_confirmed = 0 WHERE household_id = ?", (tools.household_id(),))
    conn.commit()
    conn.close()
    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Bean Chili", slot="dinner")
    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Tacos", slot="dinner")
    assert _needed()["Tortillas"] == before_tortillas


def test_undoing_by_swapping_back_takes_the_suggestion_back_with_it():
    plan_id, _chili_id, edge = _approved_week_with_alex_away()

    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Tacos", slot="dinner")
    weekly_plan.swap_meal_in_plan(plan_id, DINNER_DAY, "Bean Chili", slot="dinner")

    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["recommended_batch_from_entry_id"] == _dinner_id(DINNER_DAY)
    assert "chili" in slot_needs.describe_ready_made(edge["date"], edge["slot"])["label"]


def test_a_delete_still_clears_the_suggestion():
    """GUARD — green on both sides. Deleting the dinner is not a swap."""
    plan_id, chili_id, edge = _approved_week_with_alex_away()
    slot_needs.confirm_slot_recommendation(edge["date"], edge["slot"])

    weekly_plan.drop_dish_from_day(plan_id, chili_id)

    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["recommended_batch_from_entry_id"] is None
    assert not need["recommendation_confirmed"]


def test_nobody_home_still_clears_the_suggestion():
    """GUARD — green on both sides. Sam away too empties the dinner."""
    _plan_id, _chili_id, edge = _approved_week_with_alex_away()

    slot_needs.set_away_stretch(DINNER_DAY, "dinner", DINNER_DAY, "dinner", member_names=["Sam"])

    assert slot_needs.get_slot_need(edge["date"], edge["slot"])["recommended_batch_from_entry_id"] is None


# ------------------------------------------------------------ 2. moved

def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = _monday().isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)
AFTERNOON = datetime.datetime.fromisoformat(f"{WED}T15:50:00")


# Every test built on _night_off_week is pinned to WEDNESDAY, the day its
# night off is called (AFTERNOON). The suggestion only names a dinner still
# to cook (slot_needs._batch_candidate, 2026-10-10), so read on a Saturday
# the whole week is gone by and there is nothing for it to name.
def _night_off_week(edge_day: str) -> tuple[int, int, int]:
    """Pasta Monday, chili Wednesday (tonight), Thursday free. A ready-made
    breakfast on `edge_day` suggesting a double of the chili, confirmed.
    Returns (plan, pasta, chili)."""
    tools.add_member("Alex")
    tools.add_member("Sam")
    for name in ("Pasta", "Bean Chili"):
        tools.add_recipe(name, ingredients=[{"item": f"Main for {name}", "qty": "1"}], default_servings=2)
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    pasta = tools.plan_meal(MON, "Pasta", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    chili = tools.plan_meal(WED, "Bean Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    for day in (FRI, SAT, SUN):
        tools.plan_meal(day, "Pasta", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    slot_needs.set_slot_need(edge_day, "breakfast", "ready_made", reason="Alex is back")
    slot_needs.set_slot_recommendation(edge_day, "breakfast", batch_from_entry_id=chili)
    slot_needs.confirm_slot_recommendation(edge_day, "breakfast")
    return plan_id, pasta, chili


@pytest.mark.today(WED)
def test_a_night_off_that_moves_the_dinner_too_late_recomputes_the_suggestion():
    """
    Pinned to Wednesday, the day the night off is called. This used to
    assert the recompute landed on MONDAY's pasta — "the latest dinner
    before it" — which is a double batch of a dinner eaten two days ago
    (overnight/ready-made-names-a-cook-ahead, 2026-10-10). With the chili
    gone to Thursday and Wednesday off, nothing still to cook comes before
    Thursday breakfast, so the honest recompute is no suggestion.
    """
    _plan, pasta, chili = _night_off_week(THU)

    out = _tonight.tonight_night_off(now=AFTERNOON)

    assert out.get("moved_to") == THU, out
    need = slot_needs.get_slot_need(THU, "breakfast")
    assert need["need"] == "ready_made"
    assert need["recommended_batch_from_entry_id"] != chili, "Thursday's chili can't cover Thursday breakfast"
    assert need["recommended_batch_from_entry_id"] != pasta, "Monday's pasta is already gone by on Wednesday"
    assert need["recommended_batch_from_entry_id"] is None, "recomputed: nothing still to cook comes before it"
    assert not need["recommendation_confirmed"], "a new suggestion is a new thing to confirm"


@pytest.mark.today(WED)
def test_a_night_off_that_moves_the_dinner_but_still_in_time_keeps_it():
    """GUARD — green on both sides. Chili moves Wed -> Thu, still before Saturday breakfast."""
    _plan, _pasta, chili = _night_off_week(SAT)

    _tonight.tonight_night_off(now=AFTERNOON)

    need = slot_needs.get_slot_need(SAT, "breakfast")
    assert need["recommended_batch_from_entry_id"] == chili
    assert need["recommendation_confirmed"]


@pytest.mark.today(WED)
def test_undoing_the_night_off_leaves_a_suggestion_valid_for_the_restored_dates():
    """GUARD — green on both sides (main never re-pointed it, so the chili is valid again)."""
    _plan, _pasta, chili = _night_off_week(THU)
    _tonight.tonight_night_off(now=AFTERNOON)

    _tonight.tonight_night_off_undo(now=AFTERNOON)

    need = slot_needs.get_slot_need(THU, "breakfast")
    conn = get_conn()
    row = conn.execute(
        "SELECT date, slot FROM meal_plan_entries WHERE id = ?", (need["recommended_batch_from_entry_id"],),
    ).fetchone()
    chili_date = conn.execute("SELECT date FROM meal_plan_entries WHERE id = ?", (chili,)).fetchone()["date"]
    conn.close()
    assert chili_date == WED, "the undo put the chili back"
    assert row is not None and row["slot"] == "dinner" and row["date"] < THU


@pytest.mark.today(WED)
def test_moving_the_dinner_with_the_nights_swap_recomputes_too():
    """The same rule from Plan's nights swap, the other door into the re-dating write."""
    plan_id, pasta, chili = _night_off_week(THU)

    weekly_plan.swap_dinner_nights(plan_id, WED, FRI)

    need = slot_needs.get_slot_need(THU, "breakfast")
    # Friday's pasta is now Wednesday's: the latest dinner before Thursday.
    assert need["recommended_batch_from_entry_id"] not in (None, chili)
    conn = get_conn()
    d = conn.execute("SELECT date FROM meal_plan_entries WHERE id = ?",
                     (need["recommended_batch_from_entry_id"],)).fetchone()["date"]
    conn.close()
    assert d < THU


def test_a_swap_onto_a_night_that_cooks_nothing_releases_the_suggestion():
    """CATCH for the verifier's repro (red before the _cooks_that_night check):
    a frozen portion replacing the chili is not something to double, so the
    suggestion is released, not moved onto "leftovers from the freezer"."""
    from app.tools import freezer_portions

    plan_id, _chili_id, edge = _approved_week_with_alex_away()
    slot_needs.confirm_slot_recommendation(edge["date"], edge["slot"])

    written = freezer_portions.apply_to_plan(plan_id, [{"dish": "Chili", "on": DINNER_DAY, "inventory_item_id": 1, "quantity": "2 portions"}])

    assert written, "the portion landed on the night"
    need = slot_needs.get_slot_need(edge["date"], edge["slot"])
    assert need["recommended_batch_from_entry_id"] is None
    assert not need["recommendation_confirmed"]
    assert slot_needs.describe_ready_made(edge["date"], edge["slot"]) is None
