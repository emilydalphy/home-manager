"""
The Review stepper's "−" on a batch-cooked dish: it re-plans the night that
was eating off it, instead of saying "change that first".

Loop Board, 2026-09-24. Until now `drop_dish_from_day` answered a dish
cooked double for a later night with a refusal naming that night —
"Beef Chili on Thursday also feeds Friday's lunch — change that first and
I'll take this one off" — and wrote nothing. Emily's standing rule since
2026-09-22, when the night off learned the same lesson: "the job of Pomona
is to do all that planning work", so no "go change X first", ever.

The answer is the night off's own (tonight._night_off_plan's 'cook_on_fed',
Emily's option A): the cook MOVES ONTO THE FIRST NIGHT IT WAS FEEDING, at
that night's place in the week, the later fed nights keep their leftovers
now from the new cook night, and the night the household stepped down comes
back as a question like every other "−" leaves one. The decision (which
night) and the move (delete the leftovers row, shift the cook, re-point the
chain, carry the fridge moves) are ONE implementation shared by both
answers — weekly_plan.fed_nights_in_eating_order and
weekly_plan.move_cook_onto_fed_night — because two implementations of one
rule is the failure this codebase's log records more often than any other.

What differs, and it is the whole of the difference: the night off is a
night nobody is eating, so the batch keeps its size and tonight's share goes
in the freezer. This is the household asking for one FEWER night of the
dish, so the batch really does shrink and an approved week's line comes down
with it.

These drive the real functions on a real database. Nothing here reads source
text for a marker: the risk in an answer that moves a cook, deletes a row,
re-points a chain and re-quantifies a shopping line is what it does, and a
grep cannot see any of it.

RED AGAINST main (cbcf043), 23 of the 26 — and that number is worth less
than it looks, so it is decomposed here rather than quoted:

  * 11 are behaviour CATCHES, red on the assertion they are named for.
  * 10 die on a name main has not got — five on `tools.drop_dish_undo`,
    one each on `fed_nights_in_eating_order` and
    `_drop_by_cooking_on_the_fed_night`, two on a result key main does not
    emit (`said`, `moved_to_label`), one on a route main does not serve.
    That is the only kind of red a new function can have.
  * 2 are red at an EARLIER assertion than the one they are named for, and
    each says so in its own docstring.

The other 3 are green on main and say in their own docstrings why.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import weekly_plan as _wp


TODAY = household_today()
WEEK_START = (TODAY - datetime.timedelta(days=1)).isoformat()


def D(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- helpers

def _plan() -> int:
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _people(*names: str) -> None:
    """Members on record, so batch_for_source has a table to count — with
    nobody on file it answers 0 servings and leaves the recipe's own
    quantities alone, which would make every grocery assertion below
    vacuous."""
    for name in names or ("Emily", "Vineeth"):
        tools.add_member(name)


def _ids(day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot)).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _row(entry_id: int) -> dict | None:
    conn = get_conn()
    r = conn.execute(
        "SELECT mpe.*, COALESCE(rec.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes rec ON rec.id = mpe.recipe_id WHERE mpe.id = ? AND mpe.household_id = ?",
        (entry_id, tools.household_id())).fetchone()
    conn.close()
    return {k: r[k] for k in r.keys()} if r else None


def _derived(entry_id: int) -> dict:
    row = _row(entry_id)
    return json.loads((row or {}).get("derived_from_json") or "{}")


def _state(day: str, slot: str) -> str:
    conn = get_conn()
    r = conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot)).fetchone()
    conn.close()
    return r["slot_state"] if r else ""


def _list() -> dict[str, str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity FROM grocery_items WHERE household_id = ? AND status = 'needed'",
        (tools.household_id(),)).fetchall()
    conn.close()
    return {r["item"]: r["quantity"] for r in rows}


def _chain(plan: int, cook_day: str, cook_slot: str, dish: str,
           fed: list[tuple[str, str]], recipe: str | None = None) -> int:
    """
    A confirmed cook-once-eat-later chain, written the way generation writes
    one and then put through the app's own validator, so every test below is
    about a shape the app really produces. Returns the cook's entry id.

    Cross-SLOT on purpose in most of these (a dinner cooked double for later
    LUNCHES): that is the shape where the SOURCE is the last day the dish
    covers in its own meal-type group, which is what puts the Review
    stepper's "−" on the cook night rather than on a reheat.
    """
    tools.plan_meal(cook_day, recipe or dish, slot=cook_slot, weekly_plan_id=plan)
    source = _ids(cook_day, cook_slot)[-1]
    reheats = []
    for day, slot in fed:
        tools.plan_meal(day, recipe or dish, slot=slot, weekly_plan_id=plan)
        reheats.append(_ids(day, slot)[-1])
    # Every plan_meal above opens a connection of its own, so the chain is
    # written afterwards rather than in the loop — one open write
    # transaction here and another inside plan_meal is "database is locked",
    # which is the harness's own version of the trap this package's `conn`
    # parameters exist for.
    conn = get_conn()
    for entry_id in reheats:
        conn.execute(
            "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
            (json.dumps({"links_to": f"{cook_day}:{cook_slot}"}), entry_id),
        )
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps({"make_double_for": [f"{d}:{s}" for d, s in fed]}), source),
    )
    conn.commit()
    conn.close()
    chains = tools.plan_leftover_chains(plan)
    assert source in chains["sources"], "the harness has to seed a chain the app honours"
    return source


def _recipe(name: str = "Beef Chili") -> None:
    tools.add_recipe(
        name,
        ingredients=[{"item": "Black beans", "qty": "1 can", "category": "pantry"}],
        instructions=["Simmer."], prep_time_minutes=10, cook_time_minutes=20,
    )


# ======================================================== 1. the reported bug

class TestItNeverSaysChangeThatFirst:
    def test_a_dish_that_feeds_a_later_night_is_not_refused(self):
        """
        CATCH — the reported bug, reproduced on the parent commit: this
        answered `refused` with "…also feeds Friday's lunch — change that
        first and I'll take this one off", and wrote nothing.
        """
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])

        out = tools.drop_dish_from_day(plan, source)

        assert out["status"] == "dropped"
        assert "change that first" not in json.dumps(out)

    def test_the_cook_moves_onto_the_first_night_it_was_feeding(self):
        """CATCH. The night that was eating off it cooks it now — the row
        that was reheating there is gone, and it is the same entry, so its
        recipe, its grocery links and its cooked tick all travel."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])
        reheat = _ids(D(1), "lunch")[0]

        tools.drop_dish_from_day(plan, source)

        moved = _row(source)
        assert (moved["date"], moved["slot"]) == (D(1), "lunch")
        assert moved["slot_state"] == "planned"
        assert _row(reheat) is None, "the leftovers row it landed on is gone"
        assert _ids(D(1), "lunch") == [source], "one row on that slot, not two"

    def test_the_night_that_was_stepped_down_comes_back_as_a_question(self):
        """CATCH. `open` and never `planned_empty` and never absent: cutting
        a dish back is a decision handed back, and something still has to go
        on that plate."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])

        out = tools.drop_dish_from_day(plan, source)

        assert _state(D(0), "dinner") == "open"
        holder = _row(out["undo_entry_id"])
        assert holder["slot_state"] == "open"
        assert holder["open_reason"] == out["open_reason"]
        assert "Beef Chili" in holder["open_reason"]

    def test_the_later_fed_nights_keep_their_leftovers_from_the_new_cook(self):
        """CATCH. The thing the old refusal was protecting against, closed
        by moving rather than by declining to move: a night left holding a
        reheat of a batch nobody cooks. Saturday still reheats — from
        Friday's lunch now, not from a Thursday dinner that isn't there."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])
        later = _ids(D(2), "lunch")[0]

        tools.drop_dish_from_day(plan, source)

        assert _derived(later)["links_to"] == f"{D(1)}:lunch"
        chains = tools.plan_leftover_chains(plan)
        assert chains["leftovers"][later]["source"]["entry_id"] == source
        assert [t["date"] for t in chains["sources"][source]["targets"]] == [D(2)]

    def test_the_batch_comes_down_by_the_night_that_was_stepped_away(self):
        """CATCH. The new cook night is the night that COOKS the batch now,
        so it stops being a night the batch feeds. That is the difference
        from the night off, which keeps the batch its size and freezes the
        share — here the household has asked for one fewer night of it."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])

        tools.drop_dish_from_day(plan, source)

        derived = _derived(source)
        assert derived["make_double_for"] == [f"{D(2)}:lunch"]
        assert f"{D(1)}:lunch" not in json.dumps(derived)
        assert "freezer_extra" not in derived, "nobody freezes a meal nobody has cooked"

    def test_an_approved_weeks_shopping_line_comes_down_with_it(self):
        """CATCH. Two adults, three sittings of a recipe written for four:
        6 servings buys two cans, 4 buys one. The line has to follow the
        batch or the week over-buys a night's worth for ever."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])
        tools.approve_weekly_plan(plan)
        assert _list() == {"Black beans": "2 cans"}

        tools.drop_dish_from_day(plan, source)

        assert _list() == {"Black beans": "1 can"}


# ================================================== 2. what it says afterwards

class TestItSaysWhatItDid:
    def test_one_line_naming_the_night_back_and_the_night_the_cook_moved_to(self):
        """CATCH. Two facts in one breath, the night off's own shape
        ("Tonight's off. Beef Chili moved to Friday's lunch."). A night that
        quietly changes under somebody is the thing this replaces."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])
        thursday = datetime.date.fromisoformat(D(0)).strftime("%A")
        friday = datetime.date.fromisoformat(D(1)).strftime("%A")

        out = tools.drop_dish_from_day(plan, source)

        assert out["said"] == f"{thursday}’s yours to fill. Beef Chili moved to {friday}’s lunch."
        assert out["moved_to"] == D(1)
        assert out["moved_to_label"] == f"{friday}’s lunch"

    def test_a_fed_dinner_is_named_by_its_weekday_alone(self):
        """GUARD on the one way this app names a fed night
        (weekly_plan.fed_night_label, which the night off reads too): a
        dinner is "Friday", any other meal is "Friday's lunch". Pinned by
        the mutation that drops the slot clause."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "lunch", "Beef Chili", [(D(1), "dinner")])
        friday = datetime.date.fromisoformat(D(1)).strftime("%A")

        out = tools.drop_dish_from_day(plan, source)

        assert out["moved_to_label"] == friday

    def test_an_ordinary_drop_says_nothing_new(self):
        """GUARD. A "−" with no chain behind it is byte-for-byte the answer
        it always was — no `said`, no undo, nothing extra for a screen to
        have to branch on. Green either way; pinned by the mutation that
        routes every drop through the chain path."""
        _recipe()
        plan = _plan()
        tools.plan_meal(D(0), "Beef Chili", slot="dinner", weekly_plan_id=plan)

        out = tools.drop_dish_from_day(plan, _ids(D(0), "dinner")[0])

        assert out["status"] == "dropped"
        assert "said" not in out and "can_undo" not in out and "moved_to" not in out


# ================================================================== 3. Undo

class TestUndo:
    def test_undo_puts_the_week_back_exactly(self):
        """CATCH. Every row as it read, the leftovers row it deleted back
        with its own id, the later night's chain re-pointed home, and the
        approved week's line back at the batch's full size."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])
        tools.approve_weekly_plan(plan)
        before = {
            "rows": {e: (_row(e)["date"], _row(e)["slot"], _derived(e))
                     for e in (source, *_ids(D(1), "lunch"), *_ids(D(2), "lunch"))},
            "list": _list(),
        }

        out = tools.drop_dish_from_day(plan, source)
        back = tools.drop_dish_undo(plan, out["undo_entry_id"])

        assert back["status"] == "restored"
        assert {e: (_row(e)["date"], _row(e)["slot"], _derived(e)) for e in before["rows"]} == before["rows"]
        assert _list() == before["list"]
        assert _row(out["undo_entry_id"]) is None, "the question it left behind goes with it"

    def test_undo_says_the_dish_is_back_on_that_night(self):
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])
        thursday = datetime.date.fromisoformat(D(0)).strftime("%A")

        out = tools.drop_dish_from_day(plan, source)
        back = tools.drop_dish_undo(plan, out["undo_entry_id"])

        assert back["said"] == f"Beef Chili is back on {thursday}."

    def test_undo_refuses_rather_than_undoing_somebody_elses_change_too(self):
        """
        CATCH. The fingerprint, the night off's own rule: an Undo tapped
        after somebody has cooked, swapped or moved one of these nights
        must not quietly take THEIR change back with it. An answer, not an
        error — nothing is written and the sentence says so.
        """
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])
        out = tools.drop_dish_from_day(plan, source)
        # Somebody cooks the dish on its new night.
        tools.check_off_meal(source, status="done")

        back = tools.drop_dish_undo(plan, out["undo_entry_id"])

        assert back["status"] == "refused"
        assert "changed since" in back["message"]
        assert _row(source)["date"] == D(1), "nothing was moved back"
        assert _row(source)["cooked_status"] == "done", "and their tick is still theirs"

    def test_undo_twice_is_an_answer_not_an_error(self):
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])
        out = tools.drop_dish_from_day(plan, source)
        assert tools.drop_dish_undo(plan, out["undo_entry_id"])["status"] == "restored"

        again = tools.drop_dish_undo(plan, out["undo_entry_id"])

        assert again["status"] == "refused"
        assert again["message"] == "There’s nothing to put back."

    def test_undo_on_a_row_carrying_no_record_writes_nothing(self):
        """GUARD. An `open` row from somewhere else entirely — a generation
        gap, the allergen sweep — is not something to put back. Pinned by
        the mutation that restores whatever record it finds."""
        _recipe()
        plan = _plan()
        tools.plan_slot_open(plan, D(0), "dinner", "Thursday I'd rather ask than guess.")
        stray = _ids(D(0), "dinner")[0]

        assert tools.drop_dish_undo(plan, stray)["status"] == "refused"
        assert _row(stray) is not None

    def test_the_deleted_leftovers_rows_prep_comes_back_with_it(self):
        """
        CATCH. delete_plan_entry takes a row's prep with it, so the undo has
        to put those back too — a ticked fridge move destroyed by an Undo is
        exactly the loss the snapshot exists to prevent.

        Red against main at its FIRST assertion rather than its own: there
        the drop is refused, so the prep row is never taken away and the
        undo half is never reached. Pinned by the mutation that drops
        `prep` from plan_undo.restore.
        """
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])
        reheat = _ids(D(1), "lunch")[0]
        conn = get_conn()
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, meal_plan_entry_id, task_date, "
            "task_type, description, status) VALUES (?, ?, ?, ?, 'general', 'Warm the chili', 'done')",
            (tools.household_id(), plan, reheat, D(1)))
        conn.commit()
        conn.close()

        out = tools.drop_dish_from_day(plan, source)
        conn = get_conn()
        gone = conn.execute("SELECT COUNT(*) c FROM prep_tasks WHERE meal_plan_entry_id = ?",
                            (reheat,)).fetchone()["c"]
        conn.close()
        assert gone == 0

        tools.drop_dish_undo(plan, out["undo_entry_id"])

        conn = get_conn()
        row = conn.execute("SELECT description, status FROM prep_tasks WHERE meal_plan_entry_id = ?",
                           (reheat,)).fetchone()
        conn.close()
        assert (row["description"], row["status"]) == ("Warm the chili", "done")


# ============================================ 4. what the move carries with it

class TestTheMoveCarriesTheRestOfTheNight:
    def test_the_cooks_fridge_moves_travel_with_it(self):
        """CATCH. _shift_defrost_tasks, the nights swap's own rule rather
        than a second copy: a reminder to move something to the fridge for
        Thursday's dinner is wrong the moment the dinner is on Friday."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])
        thursday = datetime.date.fromisoformat(D(0)).strftime("%A")
        conn = get_conn()
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, meal_plan_entry_id, task_date, "
            "task_type, description, status) VALUES (?, ?, ?, ?, 'defrost', ?, 'pending')",
            (tools.household_id(), plan, source, D(-1),
             f"Move the beef to the fridge — for {thursday}’s chili."))
        conn.commit()
        conn.close()

        tools.drop_dish_from_day(plan, source)

        conn = get_conn()
        row = conn.execute("SELECT task_date, description FROM prep_tasks WHERE meal_plan_entry_id = ?",
                           (source,)).fetchone()
        conn.close()
        friday = datetime.date.fromisoformat(D(1)).strftime("%A")
        assert row["task_date"] == D(0), "moved by the same number of days the dinner did"
        assert f"for {friday}’s" in row["description"]

    def test_the_week_is_still_whole_afterwards(self):
        """CATCH. audit_plan_slots is the app's own check that no slot is
        absent and none is doubled — "two rows for one slot is how a night
        nobody is home ends up with groceries bought for it". The move
        deletes a row and writes another, which is precisely when that can
        go wrong."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])

        tools.drop_dish_from_day(plan, source)

        audit = tools.audit_plan_slots(plan)
        assert audit["duplicated"] == [], audit
        assert _ids(D(1), "lunch") == [source]


# ===================================================== 5. which night it picks

class TestWhichNightTheCookLandsOn:
    def test_the_first_one_in_EATING_order_not_in_string_order(self):
        """
        CATCH. plan_leftover_chains sorts its targets by (date, slot) as
        STRINGS, which puts a Friday dinner before a Friday lunch. The cook
        has to land on the first meal that EATS from it, or the meal before
        it is a reheat of a batch not yet cooked. One reading, shared with
        the night off (fed_nights_in_eating_order).
        """
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "dinner"), (D(1), "lunch")])

        out = tools.drop_dish_from_day(plan, source)

        assert (_row(source)["date"], _row(source)["slot"]) == (D(1), "lunch"), \
            "lunch is eaten before dinner, whatever the strings sort like"
        assert out["moved_to_label"].endswith("lunch")

    def test_the_night_off_and_the_stepper_pick_the_same_night(self):
        """
        CATCH on acceptance criterion 2, asserted by DRIVING both rather
        than by reading either: the two answers ask one function which
        night a cook with nowhere free to go lands on, so they cannot
        disagree about it.
        """
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "dinner"), (D(1), "lunch")])
        chains = tools.plan_leftover_chains(plan)
        shared = _wp.fed_nights_in_eating_order(chains["sources"][source], D(0))
        # Read before the drop: the row the shared decision names is the one
        # the cook replaces, so afterwards it is gone.
        lunch = _ids(D(1), "lunch")[0]

        out = tools.drop_dish_from_day(plan, source)

        assert shared[0]["entry_id"] == lunch
        assert out["moved_to"] == shared[0]["date"]
        assert (_row(source)["date"], _row(source)["slot"]) == (shared[0]["date"], shared[0]["slot"])
        assert _row(lunch) is None

    def test_a_make_double_for_nothing_honours_is_an_ordinary_drop(self):
        """
        CATCH on the trigger. The branch fires on the chain the app
        actually ACTS on (plan_leftover_chains, which needs both halves to
        agree), not on a raw make_double_for. A source naming a night that
        never said it was reheating is not a chain: that night is an
        ordinary cook buying its own ingredients, so there is nothing to
        move onto it and nothing left holding a reheat of a missing batch.
        The old refusal fired on the raw reading and refused this.
        """
        _people()
        _recipe()
        plan = _plan()
        tools.plan_meal(D(0), "Beef Chili", slot="dinner", weekly_plan_id=plan)
        tools.plan_meal(D(1), "Beef Chili", slot="dinner", weekly_plan_id=plan)
        source = _ids(D(0), "dinner")[0]
        conn = get_conn()
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"make_double_for": [f"{D(1)}:dinner"]}), source))
        conn.commit()
        conn.close()

        out = tools.drop_dish_from_day(plan, source)

        assert out["status"] == "dropped"
        assert "moved_to" not in out
        assert _row(source) is None
        assert _state(D(1), "dinner") == "planned"


# ============================================== 6. the refusals that do remain

class TestTheOtherRefusalsAreUntouched:
    def test_a_night_already_cooked_is_still_refused(self):
        """GUARD, and the ordering with it: a tick is a record of something
        that happened, and no arithmetic on a plan gets to delete one — now
        least of all, since the chain branch below it WRITES."""
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])
        tools.check_off_meal(source, status="done")

        out = tools.drop_dish_from_day(plan, source)

        assert out["status"] == "refused"
        assert "already been cooked" in out["message"]
        assert _row(source)["date"] == D(0), "nothing may be written on a refusal"

    def test_a_night_that_has_gone_by_is_still_refused(self):
        """
        CATCH on the ordering. The past check sits ABOVE the chain branch,
        and that mattered before because the chain refusal named a remedy
        nobody could act on; it matters MORE now, because below it a night
        that is over would have the week re-planned around it.
        """
        _people()
        _recipe()
        plan = tools.create_weekly_plan((TODAY - datetime.timedelta(days=4)).isoformat())["weekly_plan_id"]
        source = _chain(plan, D(-3), "dinner", "Beef Chili", [(D(-2), "lunch")])

        out = tools.drop_dish_from_day(plan, source)

        assert out["status"] == "refused"
        assert out["message"] == "That night’s already gone."
        assert _row(source)["date"] == D(-3)


# =================================================================== 7. atomic

class TestNothingHalfWritten:
    def test_a_failure_part_way_through_leaves_the_week_exactly_as_it_was(self, monkeypatch):
        """
        CATCH. One transaction, taken before the first read. The move
        deletes a row, shifts a cook, re-points a chain and writes a
        question; the gap between any two of those is a week nobody can
        read. Either all of it landed or none of it did — and the caller
        gets an error rather than a cheerful answer over a broken plan.

        Red against main on DID NOT RAISE rather than on the state it
        asserts: there the drop is refused before plan_slot_open is reached
        at all. Pinned by the mutation that commits between the move and
        the question.
        """
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch"), (D(2), "lunch")])
        tools.approve_weekly_plan(plan)
        before = {
            "rows": {e: (_row(e)["date"], _row(e)["slot"], _derived(e))
                     for e in (source, *_ids(D(1), "lunch"), *_ids(D(2), "lunch"))},
            "list": _list(),
        }
        monkeypatch.setattr(
            _wp, "plan_slot_open",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the power went out")))

        with pytest.raises(RuntimeError):
            tools.drop_dish_from_day(plan, source)

        assert {e: (_row(e)["date"], _row(e)["slot"], _derived(e)) for e in before["rows"]} == before["rows"]
        assert _list() == before["list"]
        assert _state(D(0), "dinner") == "planned"

    def test_the_decision_is_taken_again_under_the_lock(self):
        """
        CATCH. The night this answer chose was chosen on a connection of
        its own, and the week can move underneath it — the other phone, a
        chat turn, a retried POST. Driven by breaking the chain between the
        decision and the write, which is what a second writer does.
        """
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])
        target = {"entry_id": _ids(D(1), "lunch")[0], "date": D(1), "slot": "lunch"}
        # Somebody else frees the chain in the gap — the other phone, a chat
        # turn, a retried POST. Written the way the app leaves it: the cook
        # feeds nobody, so plan_leftover_chains stops honouring the pairing.
        conn = get_conn()
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = '{}' WHERE id = ?", (source,))
        conn.commit()
        conn.close()

        out = _wp._drop_by_cooking_on_the_fed_night(
            plan, source, D(0), "dinner", "Beef Chili", target, "yours to fill")

        assert out["status"] == "refused"
        assert out["message"] == _wp.DROP_DISH_CHANGED
        assert _row(source)["date"] == D(0)


# ==================================================================== 8. routes

class TestOverHTTP:
    def test_the_route_moves_the_cook_and_the_undo_route_puts_it_back(self, signed_in):
        _people()
        _recipe()
        plan = _plan()
        source = _chain(plan, D(0), "dinner", "Beef Chili", [(D(1), "lunch")])

        res = signed_in.post(f"/api/week/{WEEK_START}/drop-dish-day", json={"entry_id": source})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "dropped" and body["moved_to"] == D(1)
        assert body["day"] is not None, "the changed day comes back for the screen to splice in"

        back = signed_in.post(
            f"/api/week/{WEEK_START}/drop-dish-day-undo", json={"entry_id": body["undo_entry_id"]})
        assert back.status_code == 200, back.text
        assert back.json()["status"] == "restored"
        assert (_row(source)["date"], _row(source)["slot"]) == (D(0), "dinner")

    def test_the_undo_route_refuses_a_row_that_is_not_on_this_weeks_plan(self, signed_in):
        _recipe()
        _plan()

        res = signed_in.post(f"/api/week/{WEEK_START}/drop-dish-day-undo", json={"entry_id": 999999})

        assert res.status_code == 200
        assert res.json()["status"] == "refused"
