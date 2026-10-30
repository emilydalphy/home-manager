"""
A holiday dinner cooked big for later nights, then taken off the plan.

The big meal's main is an ordinary cook, and a later night can eat it
("Leftover turkey" on Tuesday). Three doors take that main away WITHOUT a
swap: leaving hosting hands a proposed main back as an open question
(holidays._reopen), a guest's note that clashes swaps the main
(big_meal.refresh_menu), and "make the main a ham" (set_big_meal_dish).
Each reversed the cook's WHOLE batch off the list and left Tuesday still
linked to Monday — over an open Monday with nothing bought for it, or
naming a ham it was never going to eat. The swap's rule now applies
(weekly_plan.release_fed_nights): the fed night becomes an ordinary night
of its own dish and, on an approved week, buys for itself.
"""
import json

from app import tools
from app.db import get_conn
from app.tools import leftovers
from tests.test_big_meal import MAIN, family, proposer  # noqa: F401
from tests.test_holidays import _shift, _thanksgiving
from conftest import on_the_weeks_monday

ON_MONDAY = on_the_weeks_monday()

HAM = dict(MAIN, name="Glazed Ham", main_protein="pork",
           ingredients=[{"item": "ham", "qty": "8 lb", "category": "meat/seafood"}],
           instructions=["Glaze.", "Bake."])


def _hosted_with_leftovers(approve: bool = True) -> tuple[int, str, str, int]:
    """Thanksgiving hosted (the menu proposes the turkey); Tuesday eats it. (plan, tg, tue, tuesday id)."""
    tg = _thanksgiving()
    tue = _shift(tg, 1)
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_slot_open(weekly_plan_id=plan_id, meal_date=tg, slot="dinner", open_reason="What’s for dinner?")
    tools.answer_holiday(tg, "hosting", headcount=4)
    tue_id = tools.plan_meal(tue, MAIN["name"], slot="dinner", weekly_plan_id=plan_id,
                             derived_from={"links_to": f"{tg}:dinner"})["entry_id"]
    assert tools.repair_leftover_chains(plan_id)["confirmed"], "the chain the test is about"
    if approve:
        tools.approve_weekly_plan(plan_id, approved_by="Emily")
    return plan_id, tg, tue, tue_id


def _derived(entry_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT derived_from_json FROM meal_plan_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.close()
    return json.loads(row["derived_from_json"] or "{}")


def _dinner(plan_id: int, day: str):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.slot_state, r.name FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'", (plan_id, day),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"{day} holds {len(rows)} dinner rows"
    return rows[0]


def _ledger(entry_id: int) -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT item, quantity FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
                        (entry_id,)).fetchall()
    conn.close()
    return {r["item"]: r["quantity"] for r in rows}


def _items() -> set[str]:
    return {i["item"].lower() for i in tools.list_grocery_list()}


def _is_reheat(plan_id: int, entry_id: int) -> bool:
    chains = leftovers.plan_leftover_chains(plan_id)
    return any(t["entry_id"] == entry_id for s in chains["sources"].values() for t in s.get("targets") or [])


@ON_MONDAY
def test_leaving_hosting_makes_the_fed_night_its_own_cook_and_buys_it(family, proposer):
    plan_id, tg, tue, tue_id = _hosted_with_leftovers()
    assert _is_reheat(plan_id, tue_id) and _ledger(tue_id) == {}

    tools.answer_holiday(tg, "just_us")

    assert _dinner(plan_id, tg)["slot_state"] == "open"
    assert "links_to" not in _derived(tue_id), "Tuesday still eating a Monday that cooks nothing"
    assert not _is_reheat(plan_id, tue_id)
    # Tuesday is the turkey's own cook now, bought for its own table — the
    # menu's sides and the batch for six went with Monday.
    assert "turkey" in _ledger(tue_id)
    assert "turkey" in _items() and "pumpkin puree" not in _items()
    audit = tools.audit_plan_slots(plan_id)
    assert audit["duplicated"] == [] and audit["hollow"] == []


@ON_MONDAY
def test_a_clashing_guest_note_swaps_the_main_and_frees_the_night_it_fed(family, proposer):
    plan_id, tg, tue, tue_id = _hosted_with_leftovers()
    proposer["answer"]["main"] = HAM

    tools.answer_holiday(tg, "hosting", guest_notes="Sam can’t eat turkey")

    assert _dinner(plan_id, tg)["name"] == "Glazed Ham"
    assert "links_to" not in _derived(tue_id), "Tuesday's leftovers now name the ham"
    assert "turkey" in _ledger(tue_id)
    assert {"ham", "turkey"} <= _items()


@ON_MONDAY
def test_a_clashing_note_with_no_replacement_still_buys_the_fed_night(family, proposer):
    plan_id, tg, tue, tue_id = _hosted_with_leftovers()
    proposer["answer"]["main"] = None

    tools.answer_holiday(tg, "hosting", guest_notes="Sam can’t eat turkey")

    assert _dinner(plan_id, tg)["slot_state"] == "open"
    assert "links_to" not in _derived(tue_id)
    assert "turkey" in _ledger(tue_id)


@ON_MONDAY
def test_changing_the_main_by_hand_frees_the_night_it_fed(family, proposer):
    plan_id, tg, tue, tue_id = _hosted_with_leftovers()

    tools.set_big_meal_dish(tg, "Glazed Ham", role="main", ingredients=HAM["ingredients"])

    assert _dinner(plan_id, tg)["name"] == "Glazed Ham"
    assert "links_to" not in _derived(tue_id)
    assert "turkey" in _ledger(tue_id)


@ON_MONDAY
def test_a_draft_frees_the_night_and_buys_nothing(family, proposer):
    plan_id, tg, tue, tue_id = _hosted_with_leftovers(approve=False)

    tools.answer_holiday(tg, "just_us")

    assert "links_to" not in _derived(tue_id)
    assert tools.list_grocery_list() == []


@ON_MONDAY
def test_an_adopted_dinner_given_back_keeps_its_chain(family, proposer):
    """Leaving hosting gives an adopted dinner back as it was — the chain included."""
    tools.add_recipe("Roast Chicken", ingredients=[{"item": "whole chicken", "qty": "1", "category": "meat/seafood"}],
                     default_servings=2)
    tg = _thanksgiving()
    tue = _shift(tg, 1)
    plan_id = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Roast Chicken", slot="dinner", weekly_plan_id=plan_id)
    tue_id = tools.plan_meal(tue, "Roast Chicken", slot="dinner", weekly_plan_id=plan_id,
                             derived_from={"links_to": f"{tg}:dinner"})["entry_id"]
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    tools.answer_holiday(tg, "hosting", headcount=4)

    tools.answer_holiday(tg, "just_us")

    assert _derived(tue_id).get("links_to") == f"{tg}:dinner"
    assert _is_reheat(plan_id, tue_id)
    assert {i["item"]: i["quantity"] for i in tools.list_grocery_list()} == {"whole chicken": "2"}
