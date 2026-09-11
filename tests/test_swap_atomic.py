"""
Swapping a meal is ONE transaction (Bug, Phase 1 — Beta).

The same seam tests/test_drop_dish_atomic.py closed for the stepper going
down, one level below it: swap_meal_in_plan deleted the displaced row,
committed, and then called plan_meal to write the replacement — so a
failure in between left a day with NO row at all, the one state schema.sql
says can never exist. And swap_meal_in_plan is the app's central meal
write: add_dish_day (the Check-the-week "+"), every chat swap, swap_in_place
and its undo, and the generation's snack repair all go through it, and
resolve_open_slot carried its own copy of the same four commits.

Now _replace_slot_entries owns one connection and one commit, and the whole
grocery ingest tree takes a connection — so the forced failures here land
at every stage (after the delete, inside the row insert, inside the ingest,
inside the chain rescale, inside the re-buy) and the day, the list and the
ledger are asserted byte-for-byte unchanged. One test per caller, a count
of connections opened inside the transaction (a nested get_conn fails as
an intermittent "database is locked", not as anything deterministic), and
the happy paths measured against the parent commit.
"""
from __future__ import annotations

import datetime
import sqlite3

import pytest

from app import agent, tools
from app.db import DB_PATH, get_conn
from app.tools import attendance, grocery, leftovers, meal_plans, recipes, weekly_plan
from app.tools import swap_in_place as sip


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED, FRI = _day(0), _day(1), _day(2), _day(4)


# ---------------------------------------------------------------- helpers

def _snapshot() -> dict:
    """
    Everything a swap can touch, read straight out of the database and
    compared as a whole — the property under test is "nothing moved", and
    a test that lists the columns it checks only catches the damage it
    thought of. Recipe cook counters are in here because plan_meal bumps
    them on the same transaction now.
    """
    conn = get_conn()

    def rows(sql):
        return [tuple(r) for r in conn.execute(sql).fetchall()]

    snap = {
        "entries": rows(
            "SELECT id, weekly_plan_id, date, slot, recipe_id, freeform_meal, slot_state, "
            "open_reason, derived_from_json, reasoning, cooked_status FROM meal_plan_entries ORDER BY id"
        ),
        "grocery": rows(
            "SELECT id, item, quantity, status, source_weekly_plan_id FROM grocery_items ORDER BY id"
        ),
        "ledger": rows(
            "SELECT id, meal_plan_entry_id, grocery_item_id, item, quantity "
            "FROM meal_plan_grocery_links ORDER BY id"
        ),
        "counters": rows("SELECT id, times_cooked, last_cooked_date FROM recipes ORDER BY id"),
    }
    conn.close()
    return snap


def _household():
    for name in ("Alex", "Sam", "Rae"):
        tools.add_member(name)


def _recipes():
    tools.add_recipe(
        "Bulgogi Wraps",
        ingredients=[{"item": "beef", "qty": "1 lb"}, {"item": "lettuce", "qty": "1 head"}],
        default_servings=3,
    )
    tools.add_recipe(
        "Soup",
        ingredients=[{"item": "stock", "qty": "1 l"}, {"item": "carrots", "qty": "3"}],
        default_servings=3,
    )


def _plain_plan(approve: bool = True) -> tuple[int, int]:
    """A one-dinner week (approved by default). Returns (plan_id, the dinner's entry id)."""
    _household()
    _recipes()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    entry_id = tools.plan_meal(MON, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    if approve:
        tools.approve_weekly_plan(plan_id, "Emily")
    return plan_id, entry_id


def _chain_plan() -> tuple[int, int, int, int]:
    """
    An approved Monday-cooks / Wednesday-and-Friday-reheat chain — the
    shape that puts _unlink_leftover_target, the source's grocery rescale
    and (for a swap OF the source) _reingest_unlinked_entries inside the
    swap. Returns (plan, mon, wed, fri).
    """
    _household()
    _recipes()
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


def _rows_on(day: str, slot: str) -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, slot_state, recipe_id, freeform_meal FROM meal_plan_entries "
        "WHERE household_id = ? AND date = ? AND slot = ? ORDER BY id",
        (tools.household_id(), day, slot),
    ).fetchall()]
    conn.close()
    return rows


def _boom(*_a, **_kw):
    raise RuntimeError("database is locked")


def _after(real):
    """A stand-in that does the real work and THEN fails — the seam one step later."""
    def flaky(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("database is locked")
    return flaky


def _assert_untouched(before: dict, plan_id: int, day: str, slot: str, missing_before: list):
    assert _snapshot() == before
    after = tools.audit_plan_slots(plan_id)
    assert after["missing"] == missing_before
    assert {"date": day, "slot": slot} not in after["missing"]
    assert after["hollow"] == []


# ------------------------------------------- a failure at EVERY stage

def test_a_failure_after_the_delete_leaves_the_day_exactly_as_it_was(monkeypatch):
    """
    The seam itself, stated once: the old row was deleted and committed
    before plan_meal ever ran, so a plan_meal that failed left the day with
    no row at all and the old meal's groceries already gone.
    """
    plan_id, entry_id = _plain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]
    assert before["grocery"], "the week has to be on the list for the reversal to matter"

    monkeypatch.setattr(weekly_plan._meal_plans, "plan_meal", _boom)
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    _assert_untouched(before, plan_id, MON, "dinner", missing)
    assert [r["id"] for r in _rows_on(MON, "dinner")] == [entry_id]
    assert _grocery_by_item() == {"beef": "1 lb", "lettuce": "1 head"}


def test_a_failure_inside_the_row_insert_leaves_the_day_exactly_as_it_was(monkeypatch):
    """
    Inside plan_meal, at the INSERT itself — not a stand-in for plan_meal
    but a connection that refuses that one statement, so the delete has
    genuinely happened on the transaction by the time this fails.
    """
    plan_id, entry_id = _plain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    class Refusing(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith("INSERT INTO MEAL_PLAN_ENTRIES"):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    def refusing_conn():
        conn = sqlite3.connect(DB_PATH, factory=Refusing)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(weekly_plan, "get_conn", refusing_conn)
    with pytest.raises(sqlite3.OperationalError):
        tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    _assert_untouched(before, plan_id, MON, "dinner", missing)
    assert [r["id"] for r in _rows_on(MON, "dinner")] == [entry_id]


def test_a_failure_inside_the_grocery_ingest_puts_the_list_back(monkeypatch):
    """
    The deepest seam, and the one that needed the whole ingest tree to
    take a connection: the new row is in, the old groceries are off, and
    one of the new meal's lines has already landed when the next one dies.
    Before, that left the new meal on the plan half-bought — and the row
    insert had committed on its own, so nothing could take it back.
    """
    plan_id, entry_id = _plain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(recipes, "_record_grocery_link", _after(recipes._record_grocery_link))
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    _assert_untouched(before, plan_id, MON, "dinner", missing)
    assert _grocery_by_item() == {"beef": "1 lb", "lettuce": "1 head"}, "no Soup line, and the wraps' lines back"


def test_a_failure_after_the_grocery_reversal_puts_the_list_back(monkeypatch):
    plan_id, entry_id = _plain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(
        grocery, "_reverse_meal_grocery_contributions",
        _after(grocery._reverse_meal_grocery_contributions),
    )
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    _assert_untouched(before, plan_id, MON, "dinner", missing)


def test_a_failure_after_the_chain_unlink_leaves_the_chain_intact(monkeypatch):
    """Swapping a reheat night: the cook night was told first, and then the swap died."""
    plan_id, mon, wed, fri = _chain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(weekly_plan, "_unlink_leftover_target", _after(weekly_plan._unlink_leftover_target))
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    _assert_untouched(before, plan_id, WED, "dinner", missing)
    chains = tools.plan_leftover_chains(plan_id)
    assert wed in chains["leftovers"]
    assert sorted(t["entry_id"] for t in chains["sources"][mon]["targets"]) == sorted([wed, fri])
    assert _grocery_by_item() == {"beef": "3 lbs", "lettuce": "3 heads"}


def test_a_failure_inside_the_source_rescale_leaves_the_list_and_chain_intact(monkeypatch):
    """
    The step drop_dish_from_day had to run AFTER its commit, logged not
    raised. Here it joins the transaction, so a failure inside it is not a
    night's over-buying to live with — it is nothing at all.
    """
    plan_id, mon, wed, fri = _chain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(
        weekly_plan, "_rescale_leftover_source_grocery",
        _after(weekly_plan._rescale_leftover_source_grocery),
    )
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    _assert_untouched(before, plan_id, WED, "dinner", missing)
    assert _grocery_by_item() == {"beef": "3 lbs", "lettuce": "3 heads"}


def test_a_failure_inside_the_rebuy_for_stranded_reheat_nights_rolls_the_swap_back(monkeypatch):
    """
    Swapping the SOURCE of a chain: the nights that were eating off it get
    bought for on their own (_reingest_unlinked_entries), inside the same
    transaction — so a failure there takes the whole swap back with it
    rather than leaving Wednesday and Friday as cooks with nothing bought.
    """
    plan_id, mon, wed, fri = _chain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(weekly_plan, "_reingest_unlinked_entries", _after(weekly_plan._reingest_unlinked_entries))
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    _assert_untouched(before, plan_id, MON, "dinner", missing)
    assert mon in tools.plan_leftover_chains(plan_id)["sources"]


# ---------------------------------------------------------- every caller

def test_the_stepper_going_UP_leaves_the_day_intact_on_a_failure(monkeypatch):
    """add_dish_day — the Check-the-week "+" — over an approved week."""
    _household()
    _recipes()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    source = tools.plan_meal(MON, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    target = tools.plan_meal(TUE, "Soup", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(recipes, "_record_grocery_link", _after(recipes._record_grocery_link))
    with pytest.raises(RuntimeError):
        tools.add_dish_day(plan_id, source, target)

    _assert_untouched(before, plan_id, TUE, "dinner", missing)
    assert [r["id"] for r in _rows_on(TUE, "dinner")] == [target]


def test_the_add_dish_route_says_nothing_changed_and_is_telling_the_truth(signed_in, monkeypatch):
    """The half the household sees: a 500, under a screen saying nothing changed. True now."""
    _household()
    _recipes()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    source = tools.plan_meal(MON, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    target = tools.plan_meal(TUE, "Soup", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.approve_weekly_plan(plan_id, "Emily")
    before = _snapshot()

    monkeypatch.setattr(weekly_plan._meal_plans, "plan_meal", _boom)
    res = signed_in.post(
        f"/api/week/{_monday().isoformat()}/add-dish-day",
        json={"entry_id": source, "target_entry_id": target},
    )

    assert res.status_code == 500
    assert _snapshot() == before


def test_a_chat_swap_of_one_of_two_snacks_leaves_both_snacks_on_a_failure(monkeypatch):
    """
    The chat tool's own shape: old_meal names which of a day's two snacks
    is going. A failure must leave BOTH, not zero and not one.
    """
    _household()
    _recipes()
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    a = tools.plan_meal(MON, "Apple slices", slot="snack", weekly_plan_id=plan_id)["entry_id"]
    b = tools.plan_meal(MON, "Cheese and crackers", slot="snack", weekly_plan_id=plan_id)["entry_id"]
    before = _snapshot()

    monkeypatch.setattr(weekly_plan._meal_plans, "plan_meal", _boom)
    with pytest.raises(RuntimeError):
        tools.swap_meal_in_plan(plan_id, MON, "Carrot sticks", slot="snack", old_meal="Apple slices")

    assert _snapshot() == before
    assert [r["id"] for r in _rows_on(MON, "snack")] == [a, b]


def test_swap_in_place_leaves_the_day_intact_on_a_failure(monkeypatch):
    """
    swap_meal_in_place: the picker is stubbed the way its own tests do, the
    new recipe is saved, and then the write behind it dies. The dinner is
    still Bulgogi Wraps, still bought for.
    """
    plan_id, entry_id = _plain_plan()
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]
    pick = {
        "meal_name": "Lemon Chicken Traybake", "reason": "Lighter.", "is_new_recipe": True,
        "ingredients": [{"item": "Chicken thighs", "qty": "2 lb", "category": "meat/seafood"}],
        "instructions": ["Roast."], "food_groups": ["protein"], "cuisine": "", "main_protein": "chicken",
        "prep_time_minutes": 10, "cook_time_minutes": 35, "default_servings": 3,
    }

    monkeypatch.setattr(recipes, "_record_grocery_link", _after(recipes._record_grocery_link))
    with pytest.raises(RuntimeError):
        sip.swap_meal_in_place(plan_id, entry_id, picker=lambda ctx: pick)

    after = _snapshot()
    # The picked recipe was saved before the swap — that is swap_in_place's
    # own behaviour and not this ticket's — so it is the one difference
    # allowed between the two snapshots.
    assert after["entries"] == before["entries"]
    assert after["grocery"] == before["grocery"]
    assert after["ledger"] == before["ledger"]
    assert tools.audit_plan_slots(plan_id)["missing"] == missing
    assert [r["id"] for r in _rows_on(MON, "dinner")] == [entry_id]
    assert _grocery_by_item() == {"beef": "1 lb", "lettuce": "1 head"}


def test_resolve_open_slot_leaves_the_question_open_on_a_failure(monkeypatch):
    """
    resolve_open_slot carried its own copy of the four commits rather than
    calling swap_meal_in_plan; it goes through the same one write now. A
    failure settling an open slot leaves it open — not absent.
    """
    plan_id, entry_id = _plain_plan()
    tools.plan_slot_open(plan_id, TUE, "dinner", "Tuesday I'd rather ask than guess.")
    before = _snapshot()
    missing = tools.audit_plan_slots(plan_id)["missing"]

    monkeypatch.setattr(weekly_plan._meal_plans, "plan_meal", _boom)
    with pytest.raises(RuntimeError):
        tools.resolve_open_slot(plan_id, TUE, "dinner", "Soup")

    _assert_untouched(before, plan_id, TUE, "dinner", missing)
    assert [r["slot_state"] for r in _rows_on(TUE, "dinner")] == ["open"]


def test_resolve_open_slot_route_says_nothing_changed_and_is_telling_the_truth(signed_in, monkeypatch):
    plan_id, entry_id = _plain_plan()
    tools.plan_slot_open(plan_id, TUE, "dinner", "Tuesday I'd rather ask than guess.")
    before = _snapshot()

    monkeypatch.setattr(recipes, "_record_grocery_link", _after(recipes._record_grocery_link))
    res = signed_in.post(
        f"/api/week/{_monday().isoformat()}/slot",
        json={"date": TUE, "slot": "dinner", "choice": "Soup"},
    )

    assert res.status_code == 500
    assert _snapshot() == before


def test_the_generation_snack_repair_leaves_the_day_intact_on_a_failure(monkeypatch, caplog):
    """
    The one generation path that swaps: repair_snack_clashes, taking another
    day's snack for a day whose own snack repeats its breakfast when no
    trade fits. The model is stubbed as tests/test_snack_swap.py does.
    The repair never raises into a generation, so the failure is logged —
    and Monday must still hold its one snack row, clash and all, rather
    than none.
    """
    week = (_monday() + datetime.timedelta(days=7)).isoformat()
    days_of_week = tools._week_dates(week)
    monday = days_of_week[0]
    plan_days = []
    for day in days_of_week:
        for slot in tools.WEEK_SLOTS:
            plan_days.append({
                "date": day, "slot": slot,
                "meal_name": "Oatmeal" if slot == "breakfast" else "Chili",
                "is_new_recipe": False, "reasoning": "fits the week",
            })
        plan_days.append({
            "date": day, "slot": "snack",
            "meal_name": "Oatmeal cookies" if day == monday else "Apple slices",
            "is_new_recipe": False, "reasoning": "something small",
        })
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: plan_days)

    real = meal_plans.plan_meal

    def failing_only_inside_the_repair(*args, **kwargs):
        # Generation plans every slot through plan_meal too; the repair's
        # swap is the one call that carries its own "why this?".
        if kwargs.get("reasoning") == "something different from the rest of the day":
            raise RuntimeError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(weekly_plan._meal_plans, "plan_meal", failing_only_inside_the_repair)
    with caplog.at_level("ERROR", logger="home_manager"):
        plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    assert "Snack repair failed" in caplog.text
    snacks = [
        m["meal"] for m in tools.get_weekly_plan(plan_id)["meals"]
        if m["date"] == monday and m["slot"] == "snack"
    ]
    assert snacks == ["Oatmeal cookies"], "the clash is left in place, not a hole"
    assert {"date": monday, "slot": "snack"} not in tools.audit_plan_slots(plan_id)["missing"]


# ------------------------------------------------------ the deadlock half

_MODULES = (
    ("weekly_plan", weekly_plan), ("grocery", grocery), ("meal_plans", meal_plans),
    ("recipes", recipes), ("leftovers", leftovers), ("attendance", attendance),
)


def _count_get_conn(monkeypatch) -> dict:
    """Count every connection opened, per module, for as long as the test runs."""
    opened = {name: 0 for name, _ in _MODULES}
    for name, module in _MODULES:
        real = module.get_conn

        def counting(_name=name, _real=real):
            opened[_name] += 1
            return _real()

        monkeypatch.setattr(module, "get_conn", counting)
    return opened


@pytest.mark.parametrize(
    "shape", ["plain", "a reheat night", "the cook night"],
)
def test_the_swap_opens_exactly_one_connection_for_its_transaction(monkeypatch, shape):
    """
    The reason every helper down the ingest tree takes a `conn` at all, and
    the same guard test_the_takeover_opens_no_second_connection and
    test_the_drop_opens_no_second_connection_inside_its_transaction put on
    theirs. SQLite gives one writer at a time, so anything opening its own
    connection inside the swap's write transaction would sit behind that
    transaction's lock and fail with "database is locked" — and a read on
    a second connection would not even see the row the transaction has
    just written. Pinned by counting: from entering _replace_slot_entries
    to leaving it, EXACTLY ONE connection is opened — the transaction's —
    across every module the swap reaches into. All three shapes: a plain
    approved swap (the ingest), swapping a reheat night (the unlink and the
    source rescale), and swapping the cook night (the re-buy for the nights
    it fed).
    """
    if shape == "plain":
        plan_id, entry_id = _plain_plan()
        target_day = MON
    else:
        plan_id, mon, wed, fri = _chain_plan()
        target_day = WED if shape == "a reheat night" else MON

    opened = _count_get_conn(monkeypatch)
    marks = {}
    real = weekly_plan._replace_slot_entries

    def marking(*args, **kwargs):
        marks["start"] = dict(opened)
        out = real(*args, **kwargs)
        marks["end"] = dict(opened)
        return out

    monkeypatch.setattr(weekly_plan, "_replace_slot_entries", marking)
    tools.swap_meal_in_plan(plan_id, target_day, "Soup", slot="dinner")

    assert set(marks) == {"start", "end"}, "the transaction body never ran"
    delta = {k: marks["end"][k] - marks["start"][k] for k in opened}
    assert delta == {**{k: 0 for k in opened}, "weekly_plan": 1}, (
        f"something inside the swap's write transaction opened its own connection: {delta}"
    )


def test_the_transaction_is_open_while_the_new_row_is_written(monkeypatch):
    """
    Said from the other side: the replacement has to be written on the SAME
    connection the delete was, or the two are two transactions however few
    connections got opened. From inside plan_meal, the deleted row is gone
    on the connection it was handed and still there on a fresh one — which
    is what "not committed yet" looks like.
    """
    plan_id, entry_id = _plain_plan()
    seen = {}
    real = meal_plans.plan_meal

    def watching(*args, **kwargs):
        conn = kwargs.get("conn")
        seen["got_conn"] = conn is not None
        if conn is not None:
            seen["gone_inside"] = conn.execute(
                "SELECT COUNT(*) c FROM meal_plan_entries WHERE id = ?", (entry_id,)
            ).fetchone()["c"] == 0
            outside = get_conn()
            seen["still_there_outside"] = outside.execute(
                "SELECT COUNT(*) c FROM meal_plan_entries WHERE id = ?", (entry_id,)
            ).fetchone()["c"] == 1
            outside.close()
        return real(*args, **kwargs)

    monkeypatch.setattr(weekly_plan._meal_plans, "plan_meal", watching)
    tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    assert seen == {"got_conn": True, "gone_inside": True, "still_there_outside": True}


# ------------------------------------------------------------ happy paths

def test_the_happy_path_lands_where_it_always_did():
    """
    One transaction has to produce the same end state the four-commit
    version produced: the new meal on the day, the old meal's lines off
    the list and the new one's on, the ledger moved over, the recipe
    counters bumped. A no-regression guard, green on both sides by design —
    every number here was measured against the parent commit.
    """
    plan_id, entry_id = _plain_plan()

    out = tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    rows = _rows_on(MON, "dinner")
    assert [r["id"] for r in rows] == [out["entry_id"]]
    assert rows[0]["slot_state"] == "planned"
    assert _grocery_by_item() == {"stock": "1 l", "carrots": "3"}
    conn = get_conn()
    assert conn.execute(
        "SELECT COUNT(*) c FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?", (entry_id,)
    ).fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?", (out["entry_id"],)
    ).fetchone()["c"] == 2
    assert conn.execute("SELECT times_cooked FROM recipes WHERE name = 'Soup'").fetchone()[0] == 1
    conn.close()
    assert {"date": MON, "slot": "dinner"} not in tools.audit_plan_slots(plan_id)["missing"]


def test_swapping_a_reheat_night_still_rescales_the_source():
    """
    The rescale now runs INSIDE the transaction rather than after
    _unlink_leftover_target's own commit; the numbers must not move. Same
    numbers test_the_ordinary_unlink_still_rescales_for_every_other_caller
    records for this swap on the drop-dish side.
    """
    plan_id, mon, wed, fri = _chain_plan()
    assert _grocery_by_item() == {"beef": "3 lbs", "lettuce": "3 heads"}

    tools.swap_meal_in_plan(plan_id, WED, "Soup", slot="dinner")

    on_list = _grocery_by_item()
    assert on_list["beef"] == "2 lbs"
    assert on_list["lettuce"] == "2 heads"
    assert on_list["stock"] == "1 l"
    assert [t["entry_id"] for t in tools.plan_leftover_chains(plan_id)["sources"][mon]["targets"]] == [fri]


def test_swapping_the_cook_night_still_buys_for_the_nights_it_fed():
    """_reingest_unlinked_entries, inside the transaction now, still does its job."""
    plan_id, mon, wed, fri = _chain_plan()

    out = tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    assert sorted(out["reingested_groceries_added"]) == ["beef", "lettuce"]
    on_list = _grocery_by_item()
    # Wednesday and Friday, three eaters each against a recipe for three:
    # one lb of beef a night, bought together as one recipe-week.
    assert on_list["beef"] == "2 lbs"
    assert on_list["lettuce"] == "2 heads"
    assert on_list["stock"] == "1 l"
    assert tools.plan_leftover_chains(plan_id) == {"sources": {}, "leftovers": {}}


def test_a_draft_swap_still_leaves_the_grocery_list_alone():
    plan_id, entry_id = _plain_plan(approve=False)
    assert _grocery_by_item() == {}

    tools.swap_meal_in_plan(plan_id, MON, "Soup", slot="dinner")

    assert _grocery_by_item() == {}
    assert [r["recipe_id"] is not None for r in _rows_on(MON, "dinner")] == [True]


def test_resolve_open_slot_still_settles_the_slot_and_buys_for_it():
    plan_id, entry_id = _plain_plan()
    tools.plan_slot_open(plan_id, TUE, "dinner", "Tuesday I'd rather ask than guess.")

    out = tools.resolve_open_slot(plan_id, TUE, "dinner", "Soup")

    assert out["was_open"] is True
    assert out["groceries_added"] == ["stock", "carrots"]
    assert [r["slot_state"] for r in _rows_on(TUE, "dinner")] == ["planned"]
    assert _grocery_by_item() == {"beef": "1 lb", "lettuce": "1 head", "stock": "1 l", "carrots": "3"}
    conn = get_conn()
    derived = conn.execute(
        "SELECT derived_from_json, reasoning FROM meal_plan_entries WHERE id = ?", (out["entry_id"],)
    ).fetchone()
    conn.close()
    assert derived["reasoning"] == "you chose this one"
    assert "settled_by_household" in derived["derived_from_json"]


def test_plan_meal_on_its_own_still_commits_as_it_always_did():
    """
    Left unset, `conn` changes nothing: a one-off chat plan_meal commits its
    row and buys for it exactly as before. The counterpart to every
    conn-threading change in this ticket.
    """
    _household()
    _recipes()
    out = tools.plan_meal(MON, "Soup", slot="dinner", add_ingredients_to_grocery_list=True)

    assert _grocery_by_item() == {"stock": "1 l", "carrots": "3"}
    assert [r["id"] for r in _rows_on(MON, "dinner")] == [out["entry_id"]]
