"""
Two writes that replaced a dinner by hand now go through
weekly_plan._replace_slot_entries, so each is ONE transaction (Loop Board
bug, built 2026-09-21).

That function exists because a swap has to be all or nothing — the
2026-09-11 `overnight/swap-atomic` work, after reproducing a day left with
no row at all. Two call sites did the same job themselves:

  * holidays._plan_dish — clear_plan_slot then plan_meal, two commits,
    reached by a real tap ("going to someone's, bringing a dish");
  * meal_variety.enforce_distinct_count — the same pair inside the
    surplus-repeat repair, generation-time.

Reproduced on an APPROVED week before anything was touched, with plan_meal
made to raise:

    before: ([(1, 'planned')], [('Black beans', '1 can', 'needed')])
    after : ([], [])

The dinner row gone, the slot in the absent state schema.sql says cannot
exist, AND the approved week's shopping line gone with it — for food the
household may already have bought — under an answer_holiday that raised and
a screen saying nothing had been saved. Strictly worse than the sibling
holidays._reopen bug (fixed 2026-09-18), where nothing had reached the list.

The meal_variety half was reproduced too, on an approved plan: the surplus
night was left with NO row and its line reversed, while the function
swallowed the exception and reported `replaced: []` — a hole in the week
nothing said anything about.

Every test says in its own docstring whether it is a CATCH (red against
main's app/) or a GUARD (green either way, pinned by a named mutation).
"""
import sqlite3
from datetime import date, timedelta

import pytest

from app import tools
from app.db import DB_PATH, get_conn
from app.tools import grocery, holidays as hol, meal_plans, meal_variety as mv
from app.tools import recipes, weekly_plan as wp

from conftest import household_today


# ---------- fixtures ----------

@pytest.fixture
def family():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")


@pytest.fixture
def recipe(family):
    tools.add_recipe("Bean Chili", ingredients=[{"item": "Black beans", "qty": "1 can", "category": "pantry"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Sweet Potato Casserole",
                     ingredients=[{"item": "Sweet potatoes", "qty": "4", "category": "produce"}],
                     prep_time_minutes=20, cook_time_minutes=40)


@pytest.fixture
def approved_holiday(recipe):
    """An approved week with a real dinner on the holiday and its real shopping line."""
    tg = _thanksgiving()
    pid = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Bean Chili", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    assert _rows(pid, tg) == [("planned", "Bean Chili")], "premise: one planned dinner"
    assert "Black beans" in _list(), "premise: the dinner really did reach the list"
    assert _ledger(), "premise: it has a ledger row behind it"
    return pid, tg


@pytest.fixture
def approved_five_dinners(family):
    """Five distinct dinners on an approved week — one over a target of four."""
    start = household_today() + timedelta(days=2)
    names = ["Chili", "Tacos", "Salmon", "Pasta", "Curry"]
    for i, n in enumerate(names):
        tools.add_recipe(n, ingredients=[{"item": f"ing{i}", "qty": "1 can", "category": "pantry"}],
                         prep_time_minutes=10, cook_time_minutes=20)
    pid = tools.create_weekly_plan(start.isoformat())["weekly_plan_id"]
    for i, n in enumerate(names):
        tools.plan_meal((start + timedelta(days=i)).isoformat(), n, slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    # Curry is the latest-starting unprotected dish, so it is the one that goes.
    return pid, (start + timedelta(days=4)).isoformat()


@pytest.fixture
def approved_holiday_with_a_thaw(family):
    """
    The shape the prep-row blocker needed, built through real doors only.

    A near holiday (so the chat defrost tool's 30-day window reaches it), a
    plan that STARTS before it (so the fridge move's own date falls inside
    the period and the Cook tab draws a session for it), the week shopped
    (meat_items_for_plan stays silent while a line is still to buy), and
    then the freezer ask answered — which is what writes the prep row.
    """
    tools.set_prep_days(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"])
    tools.add_recipe("Roast Chicken", ingredients=[{"item": "Whole chicken", "qty": "1", "category": "meat"}],
                     prep_time_minutes=15, cook_time_minutes=60)
    tools.add_recipe("Casserole", ingredients=[{"item": "Sweet potatoes", "qty": "4", "category": "produce"}],
                     prep_time_minutes=20, cook_time_minutes=40)
    tg = _next_thanksgiving()
    start = (date.fromisoformat(tg) - timedelta(days=3)).isoformat()
    pid = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.plan_meal(tg, "Roast Chicken", slot="dinner", weekly_plan_id=pid)
    tools.approve_weekly_plan(pid, approved_by="Emily")
    for item in tools.list_grocery_list():
        tools.mark_grocery_item(item["id"], "purchased")
    offered = tools.meat_items_for_plan(pid)
    assert offered, "premise: the freezer ask has something to offer"
    tools.confirm_frozen_items(pid, [offered[0].get("item") or offered[0].get("name")])
    assert _prep_count() == 1, "premise: confirming wrote exactly one fridge move"
    return pid, tg


# ---------- the catches: holidays ----------

def test_a_failure_in_the_second_write_leaves_the_dinner_row(approved_holiday, monkeypatch):
    """
    CATCH — red against main's app/, where this leaves `[]`.

    The whole bug in one assertion: the slot must never be observable with
    no row on it.
    """
    pid, tg = approved_holiday
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    with pytest.raises(RuntimeError):
        hol._plan_dish(_bringing(tg))

    assert _rows(pid, tg) == [("planned", "Bean Chili")]


def test_a_failure_in_the_second_write_leaves_the_approved_weeks_shopping_line(approved_holiday, monkeypatch):
    """
    CATCH — red against main's app/, where the line is gone.

    This is the half with teeth, and the one that makes this worse than the
    sibling _reopen bug: clear_plan_slot reverses the grocery contribution,
    so a crash took an approved week's line off the list for food the
    household may already have bought.
    """
    pid, tg = approved_holiday
    before = _list_rows()
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    with pytest.raises(RuntimeError):
        hol._plan_dish(_bringing(tg))

    assert _list_rows() == before
    assert "Black beans" in _list()


def test_a_failure_in_the_second_write_leaves_the_ledger_alone(approved_holiday, monkeypatch):
    """CATCH — the per-meal ledger is what a later reversal re-derives the line from."""
    pid, tg = approved_holiday
    before = _ledger()
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    with pytest.raises(RuntimeError):
        hol._plan_dish(_bringing(tg))

    assert _ledger() == before


def test_a_failure_in_the_second_write_leaves_the_audit_clean(approved_holiday, monkeypatch):
    """
    CATCH — audit_plan_slots is the app's own statement of what cannot
    exist, and against main that date's dinner lands in its `missing` list.
    """
    pid, tg = approved_holiday
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    with pytest.raises(RuntimeError):
        hol._plan_dish(_bringing(tg))

    audit = tools.audit_plan_slots(pid)
    assert {"date": tg, "slot": "dinner"} not in audit["missing"]
    assert {"date": tg, "slot": "dinner"} not in audit["duplicated"]


def test_the_real_door_leaves_the_dinner_and_the_line_alone(approved_holiday, monkeypatch):
    """
    CATCH, through the door a household actually taps: answering the holiday
    with "going to someone's, bringing a dish". Against main this is the
    reproduction in the module docstring, end to end.
    """
    pid, tg = approved_holiday
    before = (_rows(pid, tg), _list_rows())
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    with pytest.raises(RuntimeError):
        tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")

    assert (_rows(pid, tg), _list_rows()) == before


def test_a_failure_inside_the_row_insert_leaves_the_day_alone(approved_holiday, monkeypatch):
    """
    CATCH, one seam deeper — not a stand-in for plan_meal but a connection
    that refuses that one INSERT, so the delete has genuinely happened on
    the transaction by the time this fails. The shape
    test_swap_atomic.py's own insert test uses.

    Red against main for a reason other than the one it is named after, and
    that is worth knowing: there the refusal never fires at all (`DID NOT
    RAISE`), because main's plan_meal opens a connection of its OWN through
    meal_plans.get_conn, so the insert was never on the transaction's
    connection in the first place. Which is the bug, from underneath.
    """
    pid, tg = approved_holiday
    before = (_rows(pid, tg), _list_rows(), _ledger())
    monkeypatch.setattr(wp, "get_conn", _refusing("INSERT INTO MEAL_PLAN_ENTRIES"))

    with pytest.raises(sqlite3.OperationalError):
        hol._plan_dish(_bringing(tg))

    assert (_rows(pid, tg), _list_rows(), _ledger()) == before


def test_a_failure_inside_the_grocery_ingest_puts_everything_back(approved_holiday, monkeypatch):
    """
    CATCH, the deepest seam: the new row is in, the old groceries are off,
    and one of the NEW dish's lines has already landed when the next one
    dies. Against main the delete had committed on its own long before, so
    nothing could take it back.
    """
    pid, tg = approved_holiday
    before = (_rows(pid, tg), _list_rows(), _ledger())
    monkeypatch.setattr(recipes, "_record_grocery_link", _after(recipes._record_grocery_link))

    with pytest.raises(RuntimeError):
        hol._plan_dish(_bringing(tg))

    assert (_rows(pid, tg), _list_rows(), _ledger()) == before


# ---------- the catches: meal_variety ----------

def test_a_failed_repeat_repair_leaves_the_surplus_night_fed(approved_five_dinners, monkeypatch):
    """
    CATCH — red against main's app/, where the night is left with NO row and
    its grocery line reversed.

    enforce_distinct_count swallows its own exceptions by design (a plan
    with one dish too many beats a lost week), so against main this reported
    `replaced: []` — nothing happened — over a hole in the week.
    """
    pid, curry_night = approved_five_dinners
    before = (_dinners(pid), _list_rows(), _ledger())
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    out = mv.enforce_distinct_count(pid, 4, slot="dinner")

    assert out["replaced"] == [], "it still swallows the failure, as its docstring says"
    assert (_dinners(pid), _list_rows(), _ledger()) == before
    assert any(d[0] == curry_night for d in _dinners(pid)), "the surplus night still has a dinner on it"


def test_a_failed_repeat_repair_leaves_the_audit_clean(approved_five_dinners, monkeypatch):
    """CATCH — against main the freed night is in audit_plan_slots' `missing`."""
    pid, curry_night = approved_five_dinners
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    mv.enforce_distinct_count(pid, 4, slot="dinner")

    audit = tools.audit_plan_slots(pid)
    assert {"date": curry_night, "slot": "dinner"} not in audit["missing"]


def test_a_failed_repeat_repair_stops_at_the_first_night(approved_five_dinners, monkeypatch):
    """
    CATCH — two dishes over the target, so the loop has two nights to do and
    dies on the first. Against main BOTH the killed night's row and its line
    are gone; here the whole week is exactly as it was.

    Said precisely, because it is the honest limit of this fix: what is
    atomic is one NIGHT, not the whole loop. A failure part-way still leaves
    earlier nights already replaced — same as before — and that is fine,
    because every one of them is a complete, consistent replacement.
    """
    pid, _curry = approved_five_dinners
    before = (_dinners(pid), _list_rows())
    monkeypatch.setattr(meal_plans, "plan_meal", _boom)

    mv.enforce_distinct_count(pid, 3, slot="dinner")

    assert (_dinners(pid), _list_rows()) == before


# ---------- the happy paths, unchanged ----------

def test_the_dish_still_lands_on_an_approved_week(approved_holiday):
    """
    GUARD — green either way. Pinned by mutation: drop the
    _replace_slot_entries call and this fails.
    """
    pid, tg = approved_holiday
    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")

    assert _rows(pid, tg) == [("planned", "Sweet Potato Casserole")]
    assert "Black beans" not in _list(), "the old dinner's line came off"
    assert "Sweet potatoes" in _list(), "an approved week buys the dish it is bringing"


def test_a_draft_week_still_buys_nothing(recipe):
    """
    GUARD — green either way, and the acceptance criterion in one
    assertion: add_ingredients_to_grocery_list fires only on an approved
    week. Pinned by mutation: hard-wire _replace_slot_entries' `approved` to
    True and this fails.
    """
    tg = _thanksgiving()
    pid = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Bean Chili", slot="dinner", weekly_plan_id=pid)
    assert _list() == [], "premise: a draft has bought nothing"

    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")

    assert _rows(pid, tg) == [("planned", "Sweet Potato Casserole")]
    assert _list() == [], "a draft still reaches the list only at approval"


def test_an_empty_dinner_slot_still_takes_the_dish(recipe):
    """
    GUARD — green either way, and the case that says the fix handles an
    EMPTY slot: with no row to replace, `old_entry_ids` is `[]`, the
    rowcount check passes on 0 of 0, and the dish is simply planned.
    """
    tg = _thanksgiving()
    pid = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.approve_weekly_plan(pid, approved_by="Emily")
    assert _rows(pid, tg) == [], "premise: nothing on that dinner"

    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")

    assert _rows(pid, tg) == [("planned", "Sweet Potato Casserole")]
    assert "Sweet potatoes" in _list()


def test_answering_the_same_way_twice_changes_nothing(approved_holiday):
    """
    GUARD — _dish_entry's early return still short-circuits, so a second
    identical answer never replaces the dish with itself.
    """
    pid, tg = approved_holiday
    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")
    after_first = (_rows(pid, tg), _list_rows())

    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")

    assert (_rows(pid, tg), _list_rows()) == after_first


def test_the_repeat_repair_still_caps_the_count(approved_five_dinners):
    """
    GUARD — green either way. Pinned by mutation: drop the
    _replace_slot_entries call and this fails.
    """
    pid, curry_night = approved_five_dinners
    out = mv.enforce_distinct_count(pid, 4, slot="dinner")

    assert out["before"] == 5 and out["after"] == 4
    assert [r["date"] for r in out["replaced"]] == [curry_night]
    names = {m for _d, _s, m in _dinners(pid)}
    assert "Curry" not in names and len(names) == 4
    assert len(_dinners(pid)) == 5, "every night is still fed"


def test_the_repeat_repair_still_records_why(approved_five_dinners):
    """
    GUARD — the reasoning and derived_from are what the draft card reads,
    and they ride through _replace_slot_entries rather than being set by a
    plan_meal call of this module's own.
    """
    pid, curry_night = approved_five_dinners
    mv.enforce_distinct_count(pid, 4, slot="dinner")

    row = _entry_meta(pid, curry_night)
    assert row["reasoning"] == "On again — you asked for four dinners a week"
    assert row["derived"]["constraint"] == "dinners_per_week:4"
    assert row["derived"]["replaced"] == "Curry"
    assert row["derived"]["repeat_of"]


# ---------- the mechanics that make it hold ----------

_MODULES = ("weekly_plan", "grocery", "meal_plans", "recipes", "leftovers", "attendance")


def test_the_holiday_dish_opens_exactly_one_connection_for_its_transaction(approved_holiday, monkeypatch):
    """
    GUARD, pinned by counting rather than by redness — the failure mode of a
    nested get_conn inside an open write transaction is an intermittent
    "database is locked", not a wrong answer, so it has to be counted.

    From entering _replace_slot_entries to leaving it, EXACTLY ONE
    connection is opened across every module the write reaches into. The
    same guard test_swap_atomic.py puts on the swap. Mutation: read the
    entry ids from inside the transaction instead of above it and this goes
    to 2.

    It is red against main, but only because _replace_slot_entries is never
    reached there ("the transaction body never ran") — so it says nothing
    about main's own count, and the mutation above is what pins it.
    """
    pid, tg = approved_holiday
    delta = _connections_inside(monkeypatch, lambda: hol._plan_dish(_bringing(tg)))
    assert delta == {**{m: 0 for m in _MODULES}, "weekly_plan": 1}, (
        f"something inside the transaction opened its own connection: {delta}"
    )


def test_the_repeat_repair_opens_exactly_one_connection_per_night(approved_five_dinners, monkeypatch):
    """
    GUARD, the same count on the other call site. Red against main for the
    same non-reason as its sibling — the marked function is never called
    there — so the mutation is what pins it.
    """
    pid, _curry = approved_five_dinners
    delta = _connections_inside(monkeypatch, lambda: mv.enforce_distinct_count(pid, 4, slot="dinner"))
    assert delta == {**{m: 0 for m in _MODULES}, "weekly_plan": 1}, (
        f"something inside the transaction opened its own connection: {delta}"
    )


def test_the_transaction_is_open_while_the_new_row_is_written(approved_holiday, monkeypatch):
    """
    GUARD, pinned by mutation, and said from the other side: the replacement
    has to be written on the SAME connection the delete was, or the two are
    two transactions however few connections got opened. From inside
    plan_meal the deleted row is gone on the connection it was handed and
    still there on a fresh one — which is what "not committed yet" looks
    like. Put a conn.commit() between the delete and the insert and this
    fails.

    Red against main on its FIRST assertion only — `got_conn` is False
    there, because plan_meal is called with no connection at all — so the
    three claims it is named for are never reached.
    """
    pid, tg = approved_holiday
    old_id = _entry_ids(pid, tg)[0]
    seen = {}
    real = meal_plans.plan_meal

    def watching(*args, **kwargs):
        conn = kwargs.get("conn")
        seen["got_conn"] = conn is not None
        if conn is not None:
            seen["in_transaction"] = conn.in_transaction
            seen["gone_inside"] = conn.execute(
                "SELECT COUNT(*) c FROM meal_plan_entries WHERE id = ?", (old_id,)
            ).fetchone()["c"] == 0
            outside = get_conn()
            seen["still_there_outside"] = outside.execute(
                "SELECT COUNT(*) c FROM meal_plan_entries WHERE id = ?", (old_id,)
            ).fetchone()["c"] == 1
            outside.close()
        return real(*args, **kwargs)

    monkeypatch.setattr(meal_plans, "plan_meal", watching)
    hol._plan_dish(_bringing(tg))

    assert seen == {"got_conn": True, "in_transaction": True,
                    "gone_inside": True, "still_there_outside": True}


def test_neither_site_replaces_a_dinner_by_hand_any_more():
    """
    GUARD on the rule rather than on a value, over comment-stripped code so
    a comment naming the old pair cannot satisfy it — this repo's own notes
    have had to unpick that three times.
    """
    for fn in (hol._plan_dish, mv.enforce_distinct_count):
        code = _code_of(fn)
        assert "_replace_slot_entries" in code, f"{fn.__name__} should go through the shared write"
        assert "clear_plan_slot" not in code, f"{fn.__name__} still clears the slot by hand"
        assert "plan_meal" not in code, f"{fn.__name__} still plans the replacement by hand"


def test_the_holiday_dish_resolves_its_ids_above_the_transaction():
    """
    GUARD on the rule: _dinner_entry_ids opens a connection of its own, so
    it has to be called as an ARGUMENT to _replace_slot_entries — i.e.
    before that function opens anything — never from inside it.

    It errors against main rather than failing — that module has no such
    name — so it is pinned by mutation, not by redness: move the read
    inside and the connection count above goes to 2.
    """
    code = _code_of(hol._dinner_entry_ids)
    assert "get_conn" in code and "conn.close" in code, "it owns its own short-lived connection"
    assert "component_category IS NULL" in code, (
        "a component plan's rows are not a day's dinner — clear_plan_slot left them out too"
    )


# ---------- what this deliberately does NOT do ----------

def test_the_replaced_dinners_prep_rows_go_with_it(approved_holiday_with_a_thaw):
    """
    CATCH against THIS BRANCH'S FIRST CUT; GREEN against main, which never
    had this bug. It is the one test in this file with a baseline other
    than main, and it says so rather than letting a label-versus-redness
    audit trip over it.

    That cut left the prep row standing and argued it was harmless from ONE
    reader: get_prep_schedule drops a dangling row. Three others do not, and
    two of them are live screens. Reproduced through real doors, no
    hand-inserted rows — shop the week, tap "Something in the freezer?",
    confirm the chicken, then answer the holiday:

        first cut: session ["Move the Whole chicken to the fridge — for
                            Monday's Roast Chicken."]   chat defrost: same
        main:      []                                    []

    …tickable, for a roast chicken no longer on the plan. So this asserts
    the two live readers, not the one that was already safe.

    Mutation: drop `delete_prep_rows=True` at the holidays call site, or
    the parameter's body in _replace_slot_entries, and this fails.
    """
    pid, tg = approved_holiday_with_a_thaw
    assert _session_items(pid), "premise: the Cook tab really is showing the fridge move"
    assert _chat_defrost(), "premise: chat really would name it"

    tools.answer_holiday(tg, "out", bring_dish="Casserole")

    assert _session_items(pid) == [], "the Cook tab's prep session still shows it"
    assert _chat_defrost() == [], '"what do I need to defrost?" still names it'
    assert tools.get_prep_schedule(pid) == [], "the one reader that always filtered"
    assert _prep_count() == 0, "and it is gone from disk, not merely hidden"


def test_a_ticked_fridge_move_goes_with_the_meal_too(approved_holiday_with_a_thaw):
    """
    CHARACTERISATION of the cost of the line above, named rather than
    hidden: a fridge move somebody has already TICKED is destroyed with the
    meal. That is clear_plan_slot's own long-standing behaviour — its
    docstring calls it "a real loss to know about" — and this keeps it,
    byte-identical to main, rather than changing it.

    It is also the reason `delete_prep_rows` is opt-in rather than the
    default: turning it on for every swap in the app would spread this loss
    to paths that do not have it today.
    """
    pid, tg = approved_holiday_with_a_thaw
    task_id = _prep_ids()[0]
    tools.check_off_prep_step(task_id, status="done")
    assert _prep_statuses() == ["done"], "premise: the work is recorded as done"

    tools.answer_holiday(tg, "out", bring_dish="Casserole")

    assert _prep_count() == 0, "the record of the work goes with the meal"


def test_the_opt_in_is_off_for_every_other_caller():
    """
    GUARD, pinned by mutation: flip `delete_prep_rows`'s default to True and
    this fails. It is what keeps an ordinary chat swap behaving exactly as
    it does on main — which has the opposite problem (a stale row), and
    which is the whole app's decision rather than this ticket's.
    """
    import inspect

    sig = inspect.signature(wp._replace_slot_entries)
    assert sig.parameters["delete_prep_rows"].default is False
    assert "delete_prep_rows=True" in _code_of(hol._plan_dish), "holidays asks for it"
    assert "delete_prep_rows" not in _code_of(mv.enforce_distinct_count), (
        "meal_variety runs at generation time, before any prep row exists — "
        "asking for it would be a claim nothing can exercise"
    )


def test_a_swap_still_leaves_its_prep_row_standing(approved_holiday_with_a_thaw):
    """
    CHARACTERISATION, and the honest statement of this branch's scope: the
    identical stale row already exists on main through an ordinary chat
    swap, and this branch does not change it. Invert this when somebody
    decides what the whole app should do about it.
    """
    pid, tg = approved_holiday_with_a_thaw
    tools.swap_meal_in_plan(pid, tg, "Casserole", slot="dinner")

    assert _prep_count() == 1, "a swap leaves it — same as main"
    assert _chat_defrost(), "and chat still names it, for a dish no longer planned"


def test_the_repeat_repair_now_buys_for_the_night_it_fills_on_an_approved_week(approved_five_dinners):
    """
    CHARACTERISATION of the other behaviour change, and it is unreachable
    from the app today.

    meal_variety passed add_ingredients_to_grocery_list=False outright, so
    on an APPROVED plan it reversed the surplus dish's line and bought
    nothing for the night it filled — a week left under-bought.
    _replace_slot_entries buys when the plan is approved, so it does now.

    Only generation calls enforce_distinct_meal_count, and it calls it on a
    draft (see agent._finish_week_slots), where both answer identically and
    the draft test above says so. The function is exported publicly, though,
    so this is what a direct call gets. It is the criterion's own wording —
    "add_ingredients_to_grocery_list only firing on an approved week" — and
    the more correct of the two answers, which is why it is characterised
    rather than worked around.
    """
    pid, _curry = approved_five_dinners
    assert _list_rows()[0] == ("ing0", "1 can"), "premise: one can of Chili's ingredient"

    mv.enforce_distinct_count(pid, 4, slot="dinner")

    assert ("ing0", "2 cans") in _list_rows(), "the second night of Chili is bought for"
    assert not any(item == "ing4" for item, _q in _list_rows()), "Curry's line came off"


def test_a_holiday_dinner_that_was_a_LEFTOVERS_SOURCE_re_buys_for_the_night_it_stranded(recipe):
    """
    CHARACTERISATION of the one happy-path shape that is NOT byte-identical
    to main, named because this log's rule is that a new line on an
    already-shopped list gets named.

    When the holiday dinner is a leftovers SOURCE — a later night reheats
    it — replacing it strands that night: it is an ordinary planned meal
    now, and a reheat night never had its own ingredients bought.
    _replace_slot_entries runs _reingest_unlinked_entries, so it buys for
    it. Measured both ways on an approved week:

        main  : ['Sweet potatoes']                   <- the reheat night left under-bought
        branch: ['Black beans', 'Sweet potatoes']    <- and now bought for

    Very likely the RIGHT answer — it is what every swap in the app has
    done since `swap-atomic`, and swap_meal_in_plan's docstring is written
    around exactly this — but it puts a line on a list an approved week has
    already been shopped from, which is a thing to know rather than
    discover. The reverse shape (the holiday dinner as the reheat TARGET)
    is identical to main.
    """
    tg = _thanksgiving()
    nxt = (date.fromisoformat(tg) + timedelta(days=1)).isoformat()
    pid = tools.create_weekly_plan(tg)["weekly_plan_id"]
    tools.plan_meal(tg, "Bean Chili", slot="dinner", weekly_plan_id=pid,
                    derived_from={"make_double_for": [f"{nxt}:dinner"]})
    tools.plan_meal(nxt, "Bean Chili", slot="dinner", weekly_plan_id=pid,
                    derived_from={"links_to": f"{tg}:dinner"})
    tools.approve_weekly_plan(pid, approved_by="Emily")
    assert tools.plan_leftover_chains(pid)["sources"], "premise: the chain is confirmed"
    assert _list() == ["Black beans"], "premise: the batch is bought once, on the cook night"

    tools.answer_holiday(tg, "out", bring_dish="Sweet Potato Casserole")

    assert sorted(_list()) == ["Black beans", "Sweet potatoes"], (
        "the stranded reheat night is bought for (main leaves it under-bought)"
    )


def test_the_other_hand_rolled_pairs_are_NOT_fixed_here():
    """
    CHARACTERISATION — the third and fourth instances the ticket names, left
    alone deliberately, so nobody reports them as new or as this branch's.

    agent.py's two (the out-night and zero-count passes of
    _finish_week_slots) are clear_plan_slot + plan_slot_EMPTY, and they are
    safe in practice for the reason CLAUDE.md's away-night entry already
    records: they only run inside generate_weekly_plan, whose `finally`
    calls discard_failed_plan, so a failure there deletes the whole
    half-built plan and nothing survives to be inconsistent.

    big_meal's are FIVE, not the two the ticket names — three
    clear_plan_slot + plan_meal and two clear_plan_slot + plan_slot_open.
    Every one of the plan_meal ones passes
    add_ingredients_to_grocery_list=False explicitly, so routing them
    through _replace_slot_entries would start buying for a hosting menu on
    an approved week. That is a behaviour decision, not a refactor, and it
    is why they are their own card.
    """
    import inspect

    agent_src = inspect.getsource(__import__("app.agent", fromlist=["agent"]))
    # Three since 2026-09-21: the skipped-day pass clears a dropped day's
    # SNACKS with nothing to put in their place (a snack slot is outside
    # the 21-slot guarantee, so an absence there is the intended state) —
    # a lone clear, not a pair; its three meals go through
    # slot_needs._settle_slot_empty, the one-transaction pair.
    assert agent_src.count("tools.clear_plan_slot(") == 3
    assert agent_src.count("_slot_needs._settle_slot_empty(") == 1
    big_meal_src = inspect.getsource(__import__("app.tools.big_meal", fromlist=["big_meal"]))
    assert big_meal_src.count("clear_plan_slot(") == 5
    assert big_meal_src.count("add_ingredients_to_grocery_list=False") == 3


# ---------- helpers ----------

def _bringing(day: str) -> dict:
    return {"date": day, "holiday_name": "Thanksgiving", "bring_dish": "Sweet Potato Casserole"}


def _thanksgiving() -> str:
    found = tools.rule_holidays(household_today().year + 1)
    return next(h["date"] for h in found if h["name"] == "Thanksgiving")


def _boom(*_a, **_kw):
    raise RuntimeError("simulated failure between the two writes")


def _after(real):
    """A stand-in that does the real work and THEN fails — the seam one step later."""
    def flaky(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("simulated failure between the two writes")
    return flaky


def _refusing(statement: str):
    """A get_conn whose connections refuse one statement — the seam inside plan_meal."""
    class Refusing(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith(statement):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    def factory():
        conn = sqlite3.connect(DB_PATH, factory=Refusing)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    return factory


def _connections_inside(monkeypatch, run) -> dict:
    """
    Connections opened between entering _replace_slot_entries and leaving
    it, per module.

    WHAT IT REALLY COVERS, said precisely because the shape it is lifted
    from (test_swap_atomic.py's own counter) claims more: SIX named
    modules, and only those. Each holds its own `get_conn` from a
    `from ..db import get_conn` at import, so a patch of app.db's name
    reaches none of them and each binding has to be patched by hand — which
    means a nested connection opened from a module NOT in this list is
    invisible here. A reviewer proved that by injecting one from `staples`,
    which the ingest tree can reach, and this guard saw nothing.

    It is still worth having: it covers the six modules the write actually
    walks today, and the hazard fails loudly on its own anyway — a nested
    connection inside the open transaction waits out SQLite's busy timeout,
    so the file goes from a second to a minute and most of it goes red.
    """
    import app.tools as tools_pkg

    opened = {m: 0 for m in _MODULES}
    for name in _MODULES:
        module = getattr(tools_pkg, name)
        real = module.get_conn

        def counting(_name=name, _real=real):
            opened[_name] += 1
            return _real()

        monkeypatch.setattr(module, "get_conn", counting)

    marks = {}
    real_replace = wp._replace_slot_entries

    def marking(*args, **kwargs):
        marks.setdefault("start", dict(opened))
        out = real_replace(*args, **kwargs)
        marks["end"] = dict(opened)
        return out

    monkeypatch.setattr(wp, "_replace_slot_entries", marking)
    run()
    assert set(marks) == {"start", "end"}, "the transaction body never ran"
    return {k: marks["end"][k] - marks["start"][k] for k in opened}


def _code_of(fn) -> str:
    """
    A function's CODE with its docstring and comments taken off.

    Not fussiness: a source marker that reads raw source is satisfied by a
    comment naming the very thing it is checking is gone.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def _rows(plan_id: int, day: str, slot: str = "dinner") -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id",
        (plan_id, day, slot),
    ).fetchall()
    conn.close()
    return [(r["slot_state"], r["meal"]) for r in rows]


def _entry_ids(plan_id: int, day: str, slot: str = "dinner") -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ? ORDER BY id",
        (plan_id, day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _entry_meta(plan_id: int, day: str) -> dict:
    import json
    conn = get_conn()
    row = conn.execute(
        "SELECT reasoning, derived_from_json FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND date = ? AND slot = 'dinner' ORDER BY id DESC LIMIT 1",
        (plan_id, day),
    ).fetchone()
    conn.close()
    return {"reasoning": row["reasoning"], "derived": json.loads(row["derived_from_json"] or "{}")}


def _dinners(plan_id: int) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' ORDER BY mpe.date, mpe.id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [(r["date"], r["slot_state"], r["meal"]) for r in rows]


def _list() -> list[str]:
    return [i["item"] for i in tools.list_grocery_list()]


def _list_rows() -> list[tuple]:
    return [(i["item"], i["quantity"]) for i in tools.list_grocery_list()]


def _ledger() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id, grocery_item_id, quantity FROM meal_plan_grocery_links "
        "ORDER BY meal_plan_entry_id, grocery_item_id"
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def _next_thanksgiving() -> str:
    """The next Thanksgiving still ahead of the household's today."""
    today = household_today()
    found = [h["date"] for year in (today.year, today.year + 1)
             for h in tools.rule_holidays(year) if h["name"] == "Thanksgiving"]
    return next(d for d in sorted(found) if d > today.isoformat())


def _session_items(plan_id: int) -> list[str]:
    """What the Cook tab's prep session would draw — one of the two live
    readers that does NOT drop a dangling prep row."""
    return [i.get("title") or i.get("text") or ""
            for s in tools.prep_sessions_for_plan(plan_id) for i in s.get("items", [])]


def _chat_defrost() -> list[str]:
    """What "what do I need to defrost?" answers — the other one, and a
    chat tool SYSTEM_PROMPT routes that question straight to."""
    return [t["description"] for t in tools.get_defrost_schedule(30)]


def _prep_ids() -> list[int]:
    conn = get_conn()
    rows = conn.execute("SELECT id FROM prep_tasks ORDER BY id").fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _prep_statuses() -> list[str]:
    conn = get_conn()
    rows = conn.execute("SELECT status FROM prep_tasks ORDER BY id").fetchall()
    conn.close()
    return [r["status"] for r in rows]


def _prep_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM prep_tasks").fetchone()["c"]
    conn.close()
    return n
