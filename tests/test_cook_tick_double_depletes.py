"""
A meal's ingredients come out of the kitchen once, however many times the
box is tapped.

THE BUG (2026-09-13). cooker.check_off_meal ran deplete_inventory_for_meal
on every single call with status='done', with nothing anywhere recording
that it had already run for that entry. Driven live:

    20 tortillas -> tick -> 12 -> tick -> 4 -> tick -> the row deleted

WHY A STATUS GUARD ALONE IS NOT THE FIX, and the reason this file exists
rather than a one-line mirror of grocery.mark_grocery_item's precedent: the
Kitchen cook checkbox is a TOGGLE. shell.js renders data-next="pending"
once a meal is done and posts whatever data-next says, so the ordinary way
to send 'done' twice is tick -> untick -> tick — three taps on one control,
in which the status genuinely changed in between and a status comparison
sees nothing wrong. The second route in is Today: moves.set_move_done
dispatches a cook tick to this same function, and Today and Kitchen are
separate build-once panels that can disagree about whether tonight is
cooked, so two unticked boxes can each post 'done'.

So the memory is its own column, meal_plan_entries.inventory_depleted_at,
and not cooked_at (which an untick sets back to NULL) — and "at most once"
has to survive two THREADS and a whole component BATCH, not just one row
read a moment earlier. The first cut of this fix got both of those wrong
(17/20 concurrent pairs double-depleted; tick(A) -> untick(B) -> tick(B)
took a second batch every time), so the depletion is claimed before it runs
rather than after it is read.

UN-TICKING PUTS NOTHING BACK, and that is a decision, not an oversight —
see check_off_meal's docstring for the argument. The tests below pin it as
behaviour so that changing it later has to be deliberate.
"""
from __future__ import annotations

import datetime
import threading

import pytest

from app import households, tools
from app.db import get_conn

DAY = datetime.date.today().isoformat()


def _tacos(qty="8"):
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": qty}], default_servings=1)


def _stocked(quantity="20"):
    tools.update_inventory("Tortillas", "add", quantity=quantity, location="pantry")


def _entry():
    return tools.plan_meal(DAY, "Tacos", slot="dinner")["entry_id"]


def _tortillas():
    return {i["item"]: i["quantity"] for i in tools.get_inventory()}.get("Tortillas")


def _depleted_at(entry_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT inventory_depleted_at FROM meal_plan_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()
    return row["inventory_depleted_at"]


# ---------- the reproduction ----------

def test_tick_untick_retick_takes_one_meals_ingredients_not_two():
    """The reported sequence, exactly: three ordinary taps on one checkbox."""
    _tacos()
    _stocked()
    entry = _entry()

    tools.check_off_meal(entry, "done")
    assert _tortillas() == "12", "the first tick should take the 8 the recipe names"

    tools.check_off_meal(entry, "pending")
    tools.check_off_meal(entry, "done")

    assert _tortillas() == "12", "a re-tick must not take another 8 for the same meal"


def test_ticking_an_already_done_meal_takes_nothing_more():
    _tacos()
    _stocked()
    entry = _entry()
    tools.check_off_meal(entry, "done")

    result = tools.check_off_meal(entry, "done")

    assert _tortillas() == "12"
    assert result["inventory_depleted"] == []


def test_tapping_the_box_all_day_never_empties_the_shelf():
    """
    The end state of the original bug was not a wrong number, it was the
    inventory row being DELETED once repeated subtraction reached zero.
    """
    _tacos()
    _stocked()
    entry = _entry()

    for _ in range(6):
        tools.check_off_meal(entry, "done")
        tools.check_off_meal(entry, "pending")
    tools.check_off_meal(entry, "done")

    assert _tortillas() == "12", "one meal's worth, whatever the tap count"


def test_the_first_tick_still_depletes_normally():
    """The no-regression half: the guard must not switch depletion off."""
    _tacos()
    _stocked()
    entry = _entry()

    result = tools.check_off_meal(entry, "done")

    assert _tortillas() == "12"
    assert [d["ingredient"] for d in result["inventory_depleted"]] == ["Tortillas"]


# ---------- the un-tick decision, pinned ----------

def test_unticking_puts_nothing_back_and_says_so():
    """
    Emily's to revisit, but not to discover by accident: there is no ledger
    of what a depletion took, so nothing is restored — and the result says
    inventory_restored: False rather than being silent about it.
    """
    _tacos()
    _stocked()
    entry = _entry()
    tools.check_off_meal(entry, "done")

    result = tools.check_off_meal(entry, "pending")

    assert _tortillas() == "12", "an untick is 'not cooked yet', not 'put the food back'"
    assert result["inventory_restored"] is False


def test_unticking_something_that_was_never_ticked_says_nothing_about_inventory():
    """Green on main too — a guard that the new note is scoped to a real un-tick."""
    _tacos()
    _stocked()
    entry = _entry()

    result = tools.check_off_meal(entry, "pending")

    assert "inventory_restored" not in result
    assert _tortillas() == "20"


# ---------- an unchanged status writes nothing ----------

def test_an_unchanged_status_is_a_no_op_and_leaves_the_cook_time_alone():
    """
    The same rule grocery.mark_grocery_item has. Returning early also keeps
    cooked_at where it was, so a second tap does not move the time the meal
    was actually cooked.
    """
    _tacos()
    _stocked()
    entry = _entry()
    tools.check_off_meal(entry, "done")
    conn = get_conn()
    first = conn.execute("SELECT cooked_at FROM meal_plan_entries WHERE id = ?", (entry,)).fetchone()[0]
    conn.close()

    result = tools.check_off_meal(entry, "done")

    conn = get_conn()
    again = conn.execute("SELECT cooked_at FROM meal_plan_entries WHERE id = ?", (entry,)).fetchone()[0]
    conn.close()
    assert result["unchanged"] is True
    assert again == first
    assert result["inventory_depleted"] == [], "the key stays on the result, empty"


# ---------- Today's tick is the same write ----------

def test_todays_move_tick_cannot_deplete_twice_either():
    """
    moves.set_move_done dispatches a cook tick to check_off_meal, and Today
    and Kitchen are separate build-once panels — so the two screens can each
    post 'done' for the same dinner.
    """
    _tacos()
    _stocked()
    entry = _entry()

    tools.set_move_done(f"cook:{entry}", True)
    assert _tortillas() == "12"

    tools.set_move_done(f"cook:{entry}", False)
    tools.set_move_done(f"cook:{entry}", True)
    tools.set_move_done(f"cook:{entry}", True)

    assert _tortillas() == "12"


def test_today_and_kitchen_both_posting_done_take_one_meals_worth():
    _tacos()
    _stocked()
    entry = _entry()

    tools.set_move_done(f"cook:{entry}", True)
    tools.check_off_meal(entry, "done")

    assert _tortillas() == "12"


# ---------- leftovers: a reheat is still not a cook ----------

def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _chain_plan():
    """A Tuesday cook whose batch also feeds Thursday, validated as generation validates it."""
    tue = (_monday() + datetime.timedelta(days=1)).isoformat()
    thu = (_monday() + datetime.timedelta(days=3)).isoformat()
    tools.add_recipe("Bulgogi Wraps", ingredients=[{"item": "beef", "qty": "1 lb"}], default_servings=3)
    plan_id = tools.create_weekly_plan(_monday().isoformat())["weekly_plan_id"]
    tools.plan_meal(tue, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(thu, "Bulgogi Wraps", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{tue}:dinner"})
    tools.repair_leftover_chains(plan_id)
    view = {m["date"]: m for m in tools.get_cooker_view(plan_id)["meals"]}
    return view[tue]["entry_id"], view[thu]["entry_id"]


def _beef():
    return {i["item"]: i["quantity"] for i in tools.get_inventory()}.get("beef")


def test_a_chain_source_ticked_unticked_and_ticked_again_cooks_once():
    """
    The leftovers half of the reproduction. tests/test_leftovers_batch.py
    covers the REHEAT night being ticked twice; this is the cook night,
    toggled.
    """
    tools.add_member("Alex")
    tools.update_inventory("beef", "add", quantity="4 lbs", category="meat/seafood")
    source, _reheat = _chain_plan()

    tools.check_off_meal(source, "done")
    after_first = _beef()
    tools.check_off_meal(source, "pending")
    tools.check_off_meal(source, "done")

    assert _beef() == after_first
    assert after_first != "4 lbs", "the cook night really did take its batch out"


def test_a_reheat_night_still_depletes_nothing_however_it_is_tapped():
    """
    Green on main too (tests/test_leftovers_batch.py already pins the
    twice-ticked reheat) — here as the guard that the new guard did not
    change it.
    """
    tools.add_member("Alex")
    tools.update_inventory("beef", "add", quantity="4 lbs", category="meat/seafood")
    _source, reheat = _chain_plan()

    tools.check_off_meal(reheat, "done")
    tools.check_off_meal(reheat, "pending")
    tools.check_off_meal(reheat, "done")

    assert _beef() == "4 lbs", "a reheat is not a second cook"


def test_a_night_that_took_nothing_is_not_marked_as_having_been_depleted():
    """
    The column means "this meal's ingredients have been taken", not "the
    pass ran". A reheat, a freeform meal and a recipe nothing is tracked for
    all take nothing, and leaving them unstamped is what lets a night that
    later stops being a reheat still deplete the first time it is really
    cooked.
    """
    tools.add_member("Alex")
    tools.update_inventory("beef", "add", quantity="4 lbs", category="meat/seafood")
    _source, reheat = _chain_plan()

    tools.check_off_meal(reheat, "done")

    assert _depleted_at(reheat) is None


def test_a_meal_that_did_deplete_is_stamped():
    _tacos()
    _stocked()
    entry = _entry()

    tools.check_off_meal(entry, "done")

    assert _depleted_at(entry) is not None


# ---------- component-based plans ----------

def _component_plan(count=2):
    tools.add_recipe("Jello Bowl", ingredients=[{"item": "Jello", "qty": "1 box"}], default_servings=2)
    tools.set_planning_mode("component_based")
    week = _monday().isoformat()
    plan_id = tools.create_weekly_plan(week)["weekly_plan_id"]
    ids = [
        tools.plan_meal(week, "Jello Bowl", weekly_plan_id=plan_id, component_category="treat")["entry_id"]
        for _ in range(count)
    ]
    return plan_id, week, ids


def _statuses(ids):
    conn = get_conn()
    rows = {
        r["id"]: r["cooked_status"]
        for r in conn.execute(
            f"SELECT id, cooked_status FROM meal_plan_entries WHERE id IN ({','.join('?' * len(ids))})", ids
        )
    }
    conn.close()
    return rows


def test_a_sibling_planned_after_the_batch_was_cooked_still_gets_the_tick():
    """
    The guard is "every linked entry already reads this way", not "this one
    row does" — a component card is done only when all its siblings are (see
    get_cooker_view's merge), so a wholesale early return on the ticked row
    would leave a later-planned sibling pending forever. Green on main —
    this is the guard against the FIX being too broad, not a catch.
    """
    tools.update_inventory("Jello", "add", quantity="10 boxes")
    plan_id, week, ids = _component_plan(count=2)
    tools.check_off_meal(ids[0], "done")
    late = tools.plan_meal(week, "Jello Bowl", weekly_plan_id=plan_id, component_category="treat")["entry_id"]

    tools.check_off_meal(ids[0], "done")

    assert _statuses(ids + [late])[late] == "done"


def test_syncing_that_sibling_does_not_take_the_batchs_ingredients_again():
    tools.update_inventory("Jello", "add", quantity="10 boxes")
    plan_id, week, ids = _component_plan(count=2)
    tools.check_off_meal(ids[0], "done")
    after_cook = {i["item"]: i["quantity"] for i in tools.get_inventory()}.get("Jello")
    tools.plan_meal(week, "Jello Bowl", weekly_plan_id=plan_id, component_category="treat")

    tools.check_off_meal(ids[0], "done")

    assert {i["item"]: i["quantity"] for i in tools.get_inventory()}.get("Jello") == after_cook


# ---------- household isolation ----------

def test_one_households_tick_never_reads_or_stamps_anothers_entry():
    other = households.create_household("The Beta Testers", "a-distinct-beta-passphrase")
    _tacos()
    _stocked()
    mine = _entry()
    with tools.use_household(other):
        _tacos()
        _stocked()
        theirs = _entry()

    tools.check_off_meal(mine, "done")
    tools.check_off_meal(mine, "done")

    assert _tortillas() == "12"
    with tools.use_household(other):
        assert _tortillas() == "20", "nothing of theirs moved"
        assert _depleted_at(theirs) is None
        tools.check_off_meal(theirs, "done")
        assert _tortillas() == "12", "and their own first tick still works"


def test_another_households_entry_is_still_refused_outright():
    """Green on main — the isolation that must survive the extra reads."""
    other = households.create_household("The Beta Testers", "a-distinct-beta-passphrase")
    with tools.use_household(other):
        _tacos()
        _stocked()
        theirs = _entry()

    with pytest.raises(ValueError):
        tools.check_off_meal(theirs, "done")


# ---------- the column reaches databases that already exist ----------

def test_the_new_column_is_in_schema_and_in_the_migrations():
    """
    A column added to schema.sql and forgotten in _MIGRATIONS exists on
    every fresh database and on no real one — see
    tests/test_schema_migration_drift.py for the whole argument. This is the
    narrow version for this one column. Structural rather than behavioural:
    it is red on main only because the column is not there yet.
    """
    from app.db import _MIGRATIONS

    assert ("meal_plan_entries", "inventory_depleted_at", "TEXT") in _MIGRATIONS
    conn = get_conn()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(meal_plan_entries)")}
    conn.close()
    assert "inventory_depleted_at" in cols


# ---------- two at once ----------

def _fresh_meal(n: int) -> tuple[int, str]:
    """A trial's own recipe, own tracked item and own entry, so trials can't bleed."""
    recipe, item = f"Trial {n}", f"Ingredient {n}"
    tools.add_recipe(recipe, ingredients=[{"item": item, "qty": "8"}], default_servings=1)
    tools.update_inventory(item, "add", quantity="100", location="pantry")
    return tools.plan_meal(DAY, recipe, slot="dinner")["entry_id"], item


def _quantity_of(item):
    return {i["item"]: i["quantity"] for i in tools.get_inventory()}.get(item)


def _tick_together(entry_id, threads=2):
    """Both callers block on a barrier, then post 'done' in the same instant."""
    barrier = threading.Barrier(threads)
    errors = []

    def go():
        barrier.wait()
        try:
            tools.check_off_meal(entry_id, "done")
        except Exception as exc:  # pragma: no cover - a raise here is the failure
            errors.append(exc)

    workers = [threading.Thread(target=go) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert not errors, f"a concurrent tick raised: {errors}"


def test_two_panels_ticking_at_the_same_instant_take_one_meals_worth():
    """
    THE GUARANTEE, under the exact case the docstring names as motivation:
    Today and Kitchen are separate build-once panels, and
    /api/cooker/check-meal is a sync `def` route, so Starlette runs it in a
    threadpool and two posts really are concurrent. Reading
    inventory_depleted_at and then stamping it is two steps; between them a
    second thread reads the same NULL. Measured at 17/20 before the claim
    was made atomic, so several trials here is a real test and not a ritual.
    """
    for n in range(8):
        entry, item = _fresh_meal(n)

        _tick_together(entry)

        assert _quantity_of(item) == "92", f"trial {n}: one meal, one helping"


def test_only_one_of_two_simultaneous_ticks_reports_having_depleted():
    """The other one must say so, rather than quietly claiming the same work."""
    entry, item = _fresh_meal(99)
    results = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(tools.check_off_meal(entry, "done"))

    workers = [threading.Thread(target=go) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    depleted = [r for r in results if r.get("inventory_depleted")]
    assert len(depleted) == 1, "exactly one caller should have done the work"
    assert _quantity_of(item) == "92"


def test_four_at_once_is_still_one_helping():
    entry, item = _fresh_meal(4444)

    _tick_together(entry, threads=4)

    assert _quantity_of(item) == "92"


# ---------- one batch, several siblings ----------

def test_ticking_one_sibling_after_unticking_another_does_not_take_a_second_batch():
    """
    Deterministic, and the reason the claim is taken over every LINKED entry
    rather than over the row that was tapped. A component batch is cooked
    once for all its siblings, so a stamp on any one of them means the food
    is gone. Reachable from chat, where check_off_meal's own docstring sends
    the model to get_weekly_plan — which lists every sibling separately.
    """
    tools.update_inventory("Jello", "add", quantity="10 boxes")
    _plan_id, _week, ids = _component_plan(count=2)
    a, b = ids

    tools.check_off_meal(a, "done")
    after_cook = _quantity_of("Jello")
    tools.check_off_meal(b, "pending")
    tools.check_off_meal(b, "done")

    assert _quantity_of("Jello") == after_cook, "the batch was already cooked once"


def test_the_claim_is_stamped_on_every_sibling_not_just_the_one_tapped():
    tools.update_inventory("Jello", "add", quantity="10 boxes")
    _plan_id, _week, ids = _component_plan(count=3)

    tools.check_off_meal(ids[0], "done")

    assert all(_depleted_at(i) is not None for i in ids)


def test_two_different_siblings_ticked_at_the_same_instant_take_one_batch():
    """
    Timing-dependent, and green against the pre-amend code about as often as
    not — the unchanged-status early return sometimes swallows the second
    tick on its own. The two deterministic tests above are the real guards
    for this; this one is here because it is the shape a real household
    hits, and it must never be red on the fixed code.
    """
    tools.update_inventory("Jello", "add", quantity="10 boxes")
    _plan_id, _week, ids = _component_plan(count=2)
    before = _quantity_of("Jello")
    barrier = threading.Barrier(2)

    def go(entry_id):
        barrier.wait()
        tools.check_off_meal(entry_id, "done")

    workers = [threading.Thread(target=go, args=(i,)) for i in ids]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert _quantity_of("Jello") != before, "somebody should have cooked it"
    assert _quantity_of("Jello") == "9 boxes", "but only once"


# ---------- a pass that moved nothing leaves no stamp ----------

def test_a_quantity_too_imprecise_to_subtract_from_is_not_recorded_as_taken():
    """
    _use_inventory_row_by_id deliberately writes nothing when the tracked
    quantity can't be parsed ("a big carton") — but it still comes back in
    `depleted`, because the recipe did name an amount and the match was
    confident. Stamping that would record a depletion that did not happen.
    """
    tools.add_recipe("Soup", ingredients=[{"item": "Broth", "qty": "2 cups"}], default_servings=1)
    tools.update_inventory("Broth", "add", quantity="a big carton")
    entry = tools.plan_meal(DAY, "Soup", slot="dinner")["entry_id"]

    result = tools.check_off_meal(entry, "done")

    assert result["inventory_depleted"], "the pass did report the ingredient"
    assert _quantity_of("Broth") == "a big carton", "and moved nothing"
    assert _depleted_at(entry) is None, "so nothing may claim it was taken"


def test_tidying_that_quantity_up_lets_the_real_depletion_happen():
    """
    The harm of a wrong stamp: it blocks a depletion that should run.

    Green on main, which has no stamp to get wrong — so this one is not a
    catch for the original bug. It is red against the FIRST cut of the fix,
    where the stamp was set for a pass that had written nothing, and that
    is what it guards.
    """
    tools.add_recipe("Soup", ingredients=[{"item": "Broth", "qty": "2 cups"}], default_servings=1)
    tools.update_inventory("Broth", "add", quantity="a big carton")
    entry = tools.plan_meal(DAY, "Soup", slot="dinner")["entry_id"]
    tools.check_off_meal(entry, "done")

    tools.update_inventory("Broth", "set", quantity="4 cups")
    tools.check_off_meal(entry, "pending")
    tools.check_off_meal(entry, "done")

    assert _quantity_of("Broth") == "2 cups"


def test_unticking_through_a_sibling_planned_after_the_cook_still_says_nothing_came_back():
    """
    was_done is read across the whole linked set, not off the row that was
    tapped. The reachable case: a sibling planned AFTER the batch was cooked
    is itself pending while its siblings are done, so unticking the card
    through that id is still an untick of something that really was cooked.
    """
    tools.update_inventory("Jello", "add", quantity="10 boxes")
    plan_id, week, ids = _component_plan(count=2)
    tools.check_off_meal(ids[0], "done")
    late = tools.plan_meal(week, "Jello Bowl", weekly_plan_id=plan_id,
                           component_category="treat")["entry_id"]

    result = tools.check_off_meal(late, "pending")

    assert result["inventory_restored"] is False
