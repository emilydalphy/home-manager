"""
Swapping (or clearing) a TARGET of a confirmed leftovers chain — the
reverse gap from test_leftover_chain_swap_grocery.py, which covers
swapping the SOURCE.

repair_leftover_chains writes the pairing onto the SOURCE's
derived_from_json as make_double_for (a list of "date:slot" targets) plus
make_double_note, once per generation. Nothing ever told the source when
one of ITS targets went away later — swap_meal_in_plan, clear_plan_slot
and resolve_open_slot all just deleted the reheat night outright, leaving
the source still claiming a batch (and, once approved, a grocery line)
sized for a night that no longer exists. weekly_plan._unlink_leftover_target
is the fix; weekly_plan._rescale_leftover_source_grocery is its
approved-plan half.
"""
import json
import datetime

from app import tools
from app.db import get_conn


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


TUE, WED, FRI = _day(1), _day(2), _day(4)


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


def _two_target_chain() -> tuple[int, int, int, int]:
    """
    A validated Tuesday-cooks / Wednesday-and-Friday-reheat chain — the
    shape needed to prove that swapping (or clearing) ONE target leaves
    the other, and the source's chain, intact.

    Returns (weekly_plan_id, tuesday_entry_id, wednesday_entry_id, friday_entry_id).
    """
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tue_entry = tools.plan_meal(TUE, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    wed_entry = tools.plan_meal(
        WED, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{TUE}:dinner"},
    )["entry_id"]
    fri_entry = tools.plan_meal(
        FRI, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{TUE}:dinner"},
    )["entry_id"]
    tools.repair_leftover_chains(plan_id)
    return plan_id, tue_entry, wed_entry, fri_entry


def _derived_from(entry_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


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


def test_swapping_one_of_two_targets_shrinks_the_sources_make_double_for():
    _household()
    _wraps()
    _soup()
    plan_id, tue, wed, fri = _two_target_chain()

    # Both eaters counted before anything is swapped: Tuesday's own table
    # plus both reheat nights.
    chains = tools.plan_leftover_chains(plan_id)
    batch = tools.batch_for_source(chains["sources"][tue])
    assert batch["servings"] == 3 + 3 + 3

    tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    derived = _derived_from(tue)
    assert derived["make_double_for"] == [f"{FRI}:dinner"]
    assert "Friday" in derived["make_double_note"]
    assert "Wednesday" not in derived["make_double_note"]

    # Both-sides-agree view: Wednesday dropped out entirely, Friday is
    # still confirmed, and the batch is Tuesday + Friday only.
    chains = tools.plan_leftover_chains(plan_id)
    assert wed not in chains["leftovers"]
    assert fri in chains["leftovers"]
    assert [t["entry_id"] for t in chains["sources"][tue]["targets"]] == [fri]
    batch = tools.batch_for_source(chains["sources"][tue])
    assert batch["servings"] == 3 + 3


def test_swapping_the_last_target_turns_the_source_into_an_ordinary_cook():
    _household()
    _wraps()
    _soup()
    plan_id, tue, wed, fri = _two_target_chain()
    tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    tools.swap_meal_in_plan(plan_id, FRI, "Soup", slot="dinner")

    derived = _derived_from(tue)
    assert "make_double_for" not in derived
    assert "make_double_note" not in derived
    assert tools.plan_leftover_chains(plan_id) == {"sources": {}, "leftovers": {}}

    # Cook view: Tuesday reads as a plain, unscaled cook — no batch chip,
    # no covers note, and nothing else claims to be its leftovers.
    view = tools.get_cooker_view(plan_id)
    by_entry = {m["entry_id"]: m for m in view["meals"]}
    tue_card = by_entry[tue]
    assert tue_card["is_leftovers"] is False
    assert tue_card["servings"] is None
    assert "covers_note" not in tue_card
    assert not any(m.get("is_leftovers") and m.get("leftovers_from", {}).get("entry_id") == tue for m in view["meals"])


def test_swapping_a_target_in_an_approved_plan_rescales_the_sources_grocery_line():
    _household()
    _wraps()
    _soup()
    plan_id, tue, wed, fri = _two_target_chain()
    tools.approve_weekly_plan(plan_id, "Emily")

    # The full batch of 3 (Tue + Wed + Fri, 3 eaters apiece) against a
    # recipe written for 3 is a 3x scale.
    assert _grocery_by_item() == {"beef": "3 lbs", "lettuce": "3 heads", "salt": "to taste"}
    assert _ledger_items_for(tue) == {"beef": "3 lbs", "lettuce": "3 heads", "salt": "to taste"}
    assert _ledger_items_for(wed) == {}
    assert _ledger_items_for(fri) == {}

    tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    # The beef line is rescaled down to the new (smaller) batch — Tuesday
    # + Friday only — not left sized for a Wednesday reheat that's gone,
    # and not dropped either. Soup's own (unscaled) ingredient joins it.
    on_list = _grocery_by_item()
    assert on_list["beef"] == "2 lbs"
    assert on_list["lettuce"] == "2 heads"
    assert on_list["salt"] == "to taste"
    assert on_list["stock"] == "1 l"
    assert len(on_list) == 4

    # The ledger actually recorded the new amount against the source — the
    # one line on the list and the one row in the ledger agree, which is
    # what "the ledger sums to the line" means once there's only one
    # contributor left.
    assert _ledger_items_for(tue) == {"beef": "2 lbs", "lettuce": "2 heads", "salt": "to taste"}
    assert _ledger_items_for(fri) == {}


def test_clearing_a_target_slot_behaves_the_same_as_swapping_it():
    _household()
    _wraps()
    _soup()
    plan_id, tue, wed, fri = _two_target_chain()
    tools.approve_weekly_plan(plan_id, "Emily")
    tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    # Clearing Friday outright (the "I'm out" / away path — clear_plan_slot
    # — rather than swapping it to a new meal) has to unwind the chain
    # exactly the same way a swap does.
    tools.clear_plan_slot(plan_id, FRI, "dinner")

    derived = _derived_from(tue)
    assert "make_double_for" not in derived
    assert "make_double_note" not in derived
    assert tools.plan_leftover_chains(plan_id) == {"sources": {}, "leftovers": {}}

    # Tuesday's line is back to its own single, unscaled share, and
    # Friday — which never bought anything of its own — leaves nothing
    # behind when it's cleared.
    on_list = _grocery_by_item()
    assert on_list["beef"] == "1 lb"
    assert on_list["lettuce"] == "1 head"
    assert on_list["salt"] == "to taste"
    assert on_list["stock"] == "1 l"
    assert len(on_list) == 4
    assert _ledger_items_for(tue) == {"beef": "1 lb", "lettuce": "1 head", "salt": "to taste"}
    assert _ledger_items_for(fri) == {}
