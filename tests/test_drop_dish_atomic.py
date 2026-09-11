"""
Stepping a dish down is ONE transaction (Bug, Phase 1 — Beta).

drop_dish_from_day did four things in sequence — unlink any leftover chain,
reverse the meal's grocery contribution, delete the row, write the `open`
row that replaces it — and committed after each. An independent reviewer
forced a RuntimeError inside plan_slot_open and got the one state this
app's own rule says can never exist: the meal gone, no `open` row in its
place, the grocery line already reversed, and a 500 under a screen reading
"That didn't work just now — nothing changed." Which was false.

The fix is the one atomic-period-takeover already wrote down for
retire_overlapping_plans (2026-09-06): the helpers take a connection, the
whole sequence runs on it, and it commits once. So these tests are its
tests, one screen along — a forced failure at each seam, a snapshot of
everything the drop can touch, and a count of how many connections the
drop opens, because a nested get_conn inside an open write transaction
fails as an intermittent "database is locked" rather than as anything a
deterministic test would catch.

The last test in this file was a characterisation of the "+" on the same
screen having the same seam one level down, in swap_meal_in_plan, and NOT
being fixed here. It was inverted when that seam was closed (see
tests/test_swap_atomic.py); it stays in this file so the history reads.
"""
from __future__ import annotations

import datetime

import pytest

from app import tools
from app.db import get_conn
from app.tools import grocery, meal_plans, weekly_plan


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED, FRI = _day(0), _day(1), _day(2), _day(4)


# ---------------------------------------------------------------- helpers

def _snapshot() -> dict:
    """
    Everything a drop can touch, read straight out of the database.

    Compared as a whole rather than field by field, for the reason
    _takeover_snapshot gives: the property under test is "nothing moved",
    and a test that lists the columns it checks can only catch the damage
    it thought of.
    """
    conn = get_conn()

    def rows(sql):
        return [tuple(r) for r in conn.execute(sql).fetchall()]

    snap = {
        "entries": rows(
            "SELECT id, weekly_plan_id, date, slot, recipe_id, freeform_meal, slot_state, "
            "open_reason, derived_from_json, cooked_status FROM meal_plan_entries ORDER BY id"
        ),
        "grocery": rows(
            "SELECT id, item, quantity, status, source_weekly_plan_id FROM grocery_items ORDER BY id"
        ),
        "ledger": rows(
            "SELECT id, meal_plan_entry_id, grocery_item_id, item, quantity "
            "FROM meal_plan_grocery_links ORDER BY id"
        ),
    }
    conn.close()
    return snap


def _household():
    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)


def _wraps():
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[
            {"item": "beef", "qty": "1 lb"},
            {"item": "lettuce", "qty": "1 head"},
        ],
        default_servings=3,
    )


def _plain_plan() -> tuple[int, int]:
    """An approved one-dinner week. Returns (plan_id, the dinner's entry id)."""
    _household()
    _wraps()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entry_id = tools.plan_meal(MON, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, entry_id


def _chain_plan() -> tuple[int, int, int, int]:
    """
    An approved Monday-cooks / Wednesday-and-Friday-reheat chain — the shape
    that makes _unlink_leftover_target (and its approved-plan grocery
    rescale) part of a drop at all. Returns (plan, mon, wed, fri).
    """
    _household()
    _wraps()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    mon = tools.plan_meal(MON, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    wed = tools.plan_meal(
        WED, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{MON}:dinner"},
    )["entry_id"]
    fri = tools.plan_meal(
        FRI, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
        derived_from={"links_to": f"{MON}:dinner"},
    )["entry_id"]
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, mon, wed, fri


def _grocery_by_item() -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def _rows_on(day: str, slot: str) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) c FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchone()["c"]
    conn.close()
    return n


# ------------------------------------------------- a failure at each seam

def test_a_failure_writing_the_open_row_leaves_the_week_exactly_as_it_was(monkeypatch):
    """
    The reviewer's own reproduction, and the whole ticket stated once. The
    delete had already committed by the time plan_slot_open ran, so this
    left a genuinely ABSENT slot with its grocery line reversed under a
    screen saying nothing had changed.
    """
    plan_id, entry_id = _plain_plan()
    before = _snapshot()
    assert before["grocery"], "the week has to be on the list for the reversal to be worth anything"

    monkeypatch.setattr(
        weekly_plan, "plan_slot_open",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("database is locked")),
    )
    with pytest.raises(RuntimeError):
        tools.drop_dish_from_day(plan_id, entry_id)

    assert _snapshot() == before
    # Said again in the terms the household would notice, because a dict
    # comparison that goes wrong is hard to read.
    assert _rows_on(MON, "dinner") == 1
    assert _grocery_by_item() == {"beef": "1 lb", "lettuce": "1 head"}


def test_a_failure_after_the_grocery_reversal_puts_the_list_back(monkeypatch):
    """
    The seam one step earlier: the reversal really runs, and then the write
    behind it dies. Its own commit used to make that permanent — the meal
    still on the plan, its ingredients off the list, and nothing to say so.
    """
    plan_id, entry_id = _plain_plan()
    before = _snapshot()

    real = grocery._reverse_meal_grocery_contributions

    def flaky(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(grocery, "_reverse_meal_grocery_contributions", flaky)
    with pytest.raises(RuntimeError):
        tools.drop_dish_from_day(plan_id, entry_id)

    assert _snapshot() == before
    assert _grocery_by_item() == {"beef": "1 lb", "lettuce": "1 head"}


def test_a_failure_after_the_chain_unlink_leaves_the_chain_intact(monkeypatch):
    """
    The first seam. Dropping a reheat night tells the cook night that fed it
    first; a failure after that used to leave Monday claiming a batch for a
    Wednesday that is still, in fact, on the plan.
    """
    plan_id, mon, wed, fri = _chain_plan()
    before = _snapshot()
    assert tools.plan_leftover_chains(plan_id)["sources"], "the chain has to be confirmed"

    real = weekly_plan._unlink_leftover_target

    def flaky(*args, **kwargs):
        out = real(*args, **kwargs)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(weekly_plan, "_unlink_leftover_target", flaky)
    with pytest.raises(RuntimeError):
        tools.drop_dish_from_day(plan_id, wed)

    assert _snapshot() == before
    chains = tools.plan_leftover_chains(plan_id)
    assert wed in chains["leftovers"], "Wednesday stopped being a reheat over a failed drop"
    assert sorted(t["entry_id"] for t in chains["sources"][mon]["targets"]) == sorted([wed, fri])


def test_audit_plan_slots_reports_no_gap_after_a_failed_drop(monkeypatch):
    """
    The rule stated in the app's own words. audit_plan_slots is what says a
    slot is never absent, and a failed drop used to make it say otherwise.
    """
    plan_id, entry_id = _plain_plan()
    missing_before = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(
        weekly_plan, "plan_slot_open",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("database is locked")),
    )
    with pytest.raises(RuntimeError):
        tools.drop_dish_from_day(plan_id, entry_id)

    after = tools.audit_plan_slots(plan_id)
    assert after["missing"] == missing_before
    assert {"date": MON, "slot": "dinner"} not in after["missing"]
    assert after["hollow"] == []


def test_the_route_says_nothing_changed_and_is_telling_the_truth(signed_in, monkeypatch):
    """
    The half of this the household actually sees. The screen's line on a
    failure is "That didn't work just now — nothing changed."; the 500 is
    fine, the sentence under it has to be true.
    """
    plan_id, entry_id = _plain_plan()
    before = _snapshot()

    monkeypatch.setattr(
        weekly_plan, "plan_slot_open",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("database is locked")),
    )
    res = signed_in.post(
        f"/api/week/{_monday().isoformat()}/drop-dish-day", json={"entry_id": entry_id}
    )

    assert res.status_code == 500
    assert _snapshot() == before


# ------------------------------------------------------ the deadlock half

def _count_get_conn(monkeypatch) -> dict:
    """Count every connection opened, per module, for as long as the test runs."""
    opened = {"weekly_plan": 0, "grocery": 0, "meal_plans": 0}
    for name, module in (("weekly_plan", weekly_plan), ("grocery", grocery), ("meal_plans", meal_plans)):
        real = module.get_conn

        def counting(_name=name, _real=real):
            opened[_name] += 1
            return _real()

        monkeypatch.setattr(module, "get_conn", counting)
    return opened


@pytest.mark.parametrize("chained", [False, True], ids=["plain", "a reheat night"])
def test_the_drop_opens_no_second_connection_inside_its_transaction(monkeypatch, chained):
    """
    The reason these helpers take a `conn` at all, and the same guard
    test_the_takeover_opens_no_second_connection puts on the takeover.
    SQLite gives one writer at a time, so a helper that opened its own
    connection inside the drop's write transaction would sit behind that
    transaction's lock and fail with "database is locked" — atomicity and
    deadlock-avoidance are one requirement, and this pins it against a
    well-meaning future edit that adds a nested get_conn.

    Counted over a WINDOW rather than over the whole call: the reads before
    the transaction (the entry, the chains) and get_week_menu after it are
    ordinary connections outside it, and a total would fail for a reason
    this test is not about. The window runs from entry to
    _unlink_leftover_target — the first thing inside the transaction — to
    the return of plan_slot_open, the last.
    """
    if chained:
        plan_id, _mon, entry_id, _fri = _chain_plan()
    else:
        plan_id, entry_id = _plain_plan()

    opened = _count_get_conn(monkeypatch)
    marks = {}
    real_unlink, real_open = weekly_plan._unlink_leftover_target, weekly_plan.plan_slot_open

    def marking_unlink(*args, **kwargs):
        marks["start"] = dict(opened)
        return real_unlink(*args, **kwargs)

    def marking_open(*args, **kwargs):
        out = real_open(*args, **kwargs)
        marks["end"] = dict(opened)
        return out

    monkeypatch.setattr(weekly_plan, "_unlink_leftover_target", marking_unlink)
    monkeypatch.setattr(weekly_plan, "plan_slot_open", marking_open)
    tools.drop_dish_from_day(plan_id, entry_id)

    assert set(marks) == {"start", "end"}, "the transaction body never ran"
    assert marks["end"] == marks["start"], (
        "something inside the drop's write transaction opened its own connection: "
        f"{marks['start']} -> {marks['end']}"
    )


def test_the_transaction_is_open_while_the_open_row_is_written(monkeypatch):
    """
    Said from the other side, because "no second connection" is only half
    the property: the open row has to be written on the SAME connection the
    delete was, or the two are two transactions however few connections got
    opened. Asserted by reading the deleted row back from inside
    plan_slot_open — visible as gone only to the connection that deleted it.
    """
    plan_id, entry_id = _plain_plan()
    seen = {}
    real = weekly_plan.plan_slot_open

    def watching(*args, **kwargs):
        conn = kwargs.get("conn")
        seen["got_conn"] = conn is not None
        if conn is not None:
            seen["sees_the_delete"] = conn.execute(
                "SELECT COUNT(*) c FROM meal_plan_entries WHERE id = ?", (entry_id,)
            ).fetchone()["c"] == 0
        return real(*args, **kwargs)

    monkeypatch.setattr(weekly_plan, "plan_slot_open", watching)
    tools.drop_dish_from_day(plan_id, entry_id)

    assert seen == {"got_conn": True, "sees_the_delete": True}


# ------------------------------------------------------------ happy paths

def test_the_happy_path_lands_where_it_always_did():
    """
    The counterpart to the rollback tests: one transaction has to produce
    the same end state the four-commit version produced. A no-regression
    guard, green on both sides by design — every assertion here was
    measured against the parent commit.
    """
    plan_id, entry_id = _plain_plan()

    out = tools.drop_dish_from_day(plan_id, entry_id)

    assert out["status"] == "dropped"
    assert out["dish"] == "Bulgogi Wraps"
    assert "Bulgogi Wraps" in out["open_reason"]
    assert _rows_on(MON, "dinner") == 1
    day = [d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == MON][0]
    assert day["dinner"]["state"] == "open"
    assert day["dinner"]["open_reason"] == out["open_reason"]
    # The meal's ingredients came back off the list, and the ledger went
    # with them.
    assert _grocery_by_item() == {}
    conn = get_conn()
    assert conn.execute(
        "SELECT COUNT(*) c FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?", (entry_id,)
    ).fetchone()["c"] == 0
    conn.close()


def test_dropping_a_reheat_on_an_approved_week_still_rescales_the_source():
    """
    The one step that cannot ride the transaction: the source's grocery
    line shrinking to the smaller batch re-ingests through add_grocery_item
    and the whole recipe ingest tree, each of which opens its own
    connection. It runs after the commit instead — and it still has to run,
    which is what this pins. A no-regression guard, green on both sides by
    design: the numbers are the ones measured against the parent commit,
    and the same ones test_leftover_chain_target_swap records for a SWAP of
    the same night.
    """
    plan_id, mon, wed, fri = _chain_plan()
    # Monday cooks for three nights of three eaters against a recipe
    # written for three.
    assert _grocery_by_item() == {"beef": "3 lbs", "lettuce": "3 heads"}

    out = tools.drop_dish_from_day(plan_id, wed)

    assert out["status"] == "dropped"
    assert _grocery_by_item() == {"beef": "2 lbs", "lettuce": "2 heads"}
    chains = tools.plan_leftover_chains(plan_id)
    assert [t["entry_id"] for t in chains["sources"][mon]["targets"]] == [fri]


def test_a_failed_rescale_still_hands_the_day_back(monkeypatch):
    """
    The deliberate judgement call in the fix, written down. The rescale is
    after the commit, so it cannot roll the drop back — and raising over it
    would put the screen's "nothing changed" over a change that did happen,
    which is the exact lie this ticket exists to remove. The cost of
    swallowing it is one night's share of over-buying on an approved list;
    the cost of raising is a household told their week is unchanged when a
    day has just been handed back to them.
    """
    plan_id, mon, wed, fri = _chain_plan()
    monkeypatch.setattr(
        weekly_plan, "_rescale_leftover_source_grocery",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("database is locked")),
    )

    out = tools.drop_dish_from_day(plan_id, wed)

    assert out["status"] == "dropped"
    assert _rows_on(WED, "dinner") == 1
    day = [d for d in tools.get_week_menu(plan_id)["days"] if d["date"] == WED][0]
    assert day["dinner"]["state"] == "open"
    # The line is over-bought rather than wrong: still there, still the
    # three-night batch, because the trim is what failed.
    assert _grocery_by_item() == {"beef": "3 lbs", "lettuce": "3 heads"}


def test_the_ordinary_unlink_still_rescales_for_every_other_caller():
    """
    _unlink_leftover_target only defers the rescale when it is handed a
    connection. Every other caller — clear_plan_slot, swap_meal_in_plan,
    resolve_open_slot — passes none and must behave exactly as it did, so
    this drives the swap path the deferral could have broken. A
    no-regression guard, green on both sides by design.
    """
    plan_id, mon, wed, fri = _chain_plan()
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 l"}], default_servings=3)

    tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    on_list = _grocery_by_item()
    assert on_list["beef"] == "2 lbs"
    assert on_list["lettuce"] == "2 heads"
    assert on_list["stock"] == "1 l"


# ------------------------------------------------------- the "+", recorded

def test_the_stepper_going_UP_had_the_same_seam_and_is_now_atomic_too():
    """
    Was the characterisation test `..._has_the_same_seam_and_is_NOT_fixed_here`,
    INVERTED the same morning by the swap-atomic ticket
    (tests/test_swap_atomic.py, which is where the full set lives).
    add_dish_day was checked for the same shape and had it — one level down,
    in swap_meal_in_plan, which deleted the displaced row, committed, and
    then called plan_meal to write the replacement. Force plan_meal to fail
    and the target day was genuinely ABSENT, exactly as a drop used to leave
    one. Kept here, in the same file and the same shape, so the history
    reads: the drop-dish fix wrote the "+" down as not fixed; this is the
    record of it being fixed.
    """
    _household()
    _wraps()
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 l"}], default_servings=3)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    source = tools.plan_meal(MON, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    target = tools.plan_meal(TUE, "Soup", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    before = _snapshot()

    real = meal_plans.plan_meal
    weekly_plan._meal_plans.plan_meal = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("database is locked")
    )
    try:
        with pytest.raises(RuntimeError):
            tools.add_dish_day(plan_id, source, target)
    finally:
        weekly_plan._meal_plans.plan_meal = real

    assert _rows_on(TUE, "dinner") == 1
    assert _snapshot() == before
    assert {"date": TUE, "slot": "dinner"} not in tools.audit_plan_slots(plan_id)["missing"]
