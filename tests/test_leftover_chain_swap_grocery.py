"""
Swapping the SOURCE of an approved leftovers chain (Loop Board follow-up
to fix-leftovers-ordering / "core loop handoffs").

swap_meal_in_plan already reverses the source's WHOLE batch-scaled grocery
contribution correctly — see recipes._add_recipe_ingredients_for_entries,
whose scaling factor already covers everyone the chain fed, source's own
table included. And once the swap lands, leftovers.plan_leftover_chains
stops confirming the old pairing on its own: the new entry in the source's
slot carries no make_double_for (repair_leftover_chains, the only writer
of that field, runs at generation time — see weekly_plan.py — not here),
so the night that used to eat the source's leftovers is now, honestly,
just an ordinary planned meal.

The gap this file pins down: that night has NEVER had its own ingredients
bought — a leftovers night contributes nothing to the list on its own (see
recipes._add_recipe_ingredients_for_entries's docstring) — and nothing
re-bought them for real once the chain broke. swap_meal_in_plan's new
_reingest_unlinked_entries call is the fix.
"""
import datetime

from app import tools
from app.db import get_conn


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


TUE, THU = _day(1), _day(3)


def _household():
    for n in ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _wraps():
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[
            {"item": "beef", "qty": "1 lb"},
            {"item": "lettuce", "qty": "1 head"},
            {"item": "salt", "qty": "to taste"},
        ],
        default_servings=3,
    )


def _soup():
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 l"}], default_servings=3)


def _chain() -> tuple[int, int, int]:
    """
    A validated Tuesday-cooks/Thursday-reheats chain, same shape
    test_leftovers_batch.py's own _chain() builds. Returns
    (weekly_plan_id, tuesday_entry_id, thursday_entry_id).
    """
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tue_entry = tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    thu_entry = tools.plan_meal(
        THU, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{TUE}:dinner"},
    )["entry_id"]
    tools.repair_leftover_chains(plan_id)
    return plan_id, tue_entry, thu_entry


def _grocery_by_item():
    return {g["item"]: g["quantity"] for g in tools.list_grocery_list()}


def _ledger_items_for(entry_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
        (entry_id,),
    ).fetchall()
    conn.close()
    return {r["item"]: r["quantity"] for r in rows}


def test_swapping_the_source_reingests_the_former_reheat_nights_own_ingredients():
    _household()
    _wraps()
    _soup()
    plan_id, tue_entry, thu_entry = _chain()
    tools.approve_weekly_plan(plan_id, "Emily")
    # The batch bought the doubled amount, and Thursday itself bought
    # nothing — the starting point this fix has to leave intact for the
    # source, and correct for the reheat night.
    assert _grocery_by_item() == {"beef": "2 lbs", "lettuce": "2 heads", "salt": "to taste"}
    assert _ledger_items_for(thu_entry) == {}

    tools.swap_meal_in_plan(plan_id, TUE, "Soup", slot="dinner")

    # Tuesday is Soup now — its old Bulgogi contribution is gone, and Soup's
    # own (unscaled: nobody else eats Soup's leftovers) is on the list.
    # Thursday reverted to an ordinary cook the moment the chain broke, and
    # this is the fix: it finally buys its OWN single share, not the
    # doubled amount that used to cover it and not nothing at all.
    on_list = _grocery_by_item()
    assert on_list["stock"] == "1 l"
    assert on_list["beef"] == "1 lb"
    assert on_list["lettuce"] == "1 head"
    assert on_list["salt"] == "to taste"
    assert "stock" in on_list and len(on_list) == 4

    # The ledger actually recorded Thursday's own contribution — not just a
    # coincidentally-matching grocery line — which is what lets it reverse
    # symmetrically below.
    assert _ledger_items_for(thu_entry) == {"beef": "1 lb", "lettuce": "1 head", "salt": "to taste"}


def test_clearing_after_the_swap_leaves_the_list_empty():
    _household()
    _wraps()
    _soup()
    plan_id, _, _ = _chain()
    tools.approve_weekly_plan(plan_id, "Emily")
    tools.swap_meal_in_plan(plan_id, TUE, "Soup", slot="dinner")

    tools.clear_weekly_plan(plan_id)

    assert tools.list_grocery_list() == []


def test_swapping_the_source_in_an_unapproved_draft_reingests_nothing():
    """
    The re-ingestion only makes sense once the list is actually live —
    exactly the same gate swap_meal_in_plan already applies to the swap's
    own new meal (add_ingredients_to_grocery_list=_weekly_plan_is_approved).
    A draft's chain breaking is not a grocery event at all.
    """
    _household()
    _wraps()
    _soup()
    plan_id, _, thu_entry = _chain()

    tools.swap_meal_in_plan(plan_id, TUE, "Soup", slot="dinner")

    assert tools.list_grocery_list() == []
    assert _ledger_items_for(thu_entry) == {}


def test_swapping_a_night_that_was_never_a_leftovers_source_is_unaffected():
    """
    The common case: swapping an ordinary meal that nothing links to must
    not trigger the extra re-ingestion pass or touch any other night's
    groceries — was_a_leftovers_source guards exactly this.
    """
    _household()
    _wraps()
    _soup()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")

    tools.swap_meal_in_plan(plan_id, TUE, "Soup", slot="dinner")

    assert _grocery_by_item() == {"stock": "1 l"}
