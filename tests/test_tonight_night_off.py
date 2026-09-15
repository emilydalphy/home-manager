"""
"Not tonight — we're going out" — the third answer on Now's tonight card
(Loop Board, Emily 2026-09-14: *"a night off doesn't mean fighting the app
into swapping for a dish I'm not going to cook either"*).

Server side: tools.tonight_night_off settles tonight in one call — the dish
moves to the next free night of this plan when there is one and comes off
the week when there isn't — and either way leaves the night
`planned_empty`, so nothing anywhere reads it as missed, skipped or
overdue. Anything already bought for a dropped dish that won't keep comes
back as `use_soon` and goes on the attention queue; the night itself counts
toward the intake's learned takeout hint. Client side: the sheet's last
row, the settled card, and the copy in shell.js/shell.html/shell.css.

Every test says in its own docstring whether it is a CATCH (red on main,
where none of this exists) or a GUARD (green on main — a promise that
something this change could have broken didn't move).
"""
from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn
from app.tools import tonight as _tonight
from app.tools import week_intake as _week_intake

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


WEEK = _monday().isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS
# "Tonight" is Wednesday of the current week, so there are nights on both
# sides of it. The clock is injected, never read.
TONIGHT = WED
AFTERNOON = datetime.datetime.fromisoformat(f"{TONIGHT}T15:50:00")


def _plan() -> int:
    return tools.create_weekly_plan(WEEK)["weekly_plan_id"]


def _recipe(name, ingredients=None):
    tools.add_recipe(
        name,
        ingredients=ingredients or [
            # Shrimp and Rice are on every dish of the week, so the whole
            # week's lines merge and a drop has to recompute them. "Greens
            # for X" is this dish's alone, which is what a dropped dinner
            # can honestly free up.
            {"item": "Shrimp", "qty": "1 lb", "category": "meat"},
            {"item": "Rice", "qty": "1 cup", "category": "pantry"},
            {"item": f"Greens for {name}", "qty": "1 bag", "category": "produce"},
        ],
        prep_time_minutes=10, cook_time_minutes=20, default_servings=4,
    )


DISHES = ["Toast Soldiers", "Bean Chili", "Garlic Shrimp",
          "Lentil Soup", "Fish Tacos", "Pizza Night", "Roast Veg"]


def _full_week(plan: int, approve: bool = True) -> None:
    """Every night of the week has a real dinner — so there is no free
    night and a called-off night has to drop its dish."""
    for day, dish in zip(DAYS, DISHES):
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    if approve:
        tools.approve_weekly_plan(plan)


def _week_with_a_free_night(plan: int, free_day: str = THU, approve: bool = True) -> None:
    """The same week, except one later night is an `open` slot — a decision
    the plan handed back, which is what "free" means here."""
    for day, dish in zip(DAYS, DISHES):
        if day == free_day:
            tools.plan_slot_open(plan, day, "dinner", "Nothing settled for this one yet.")
            continue
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    if approve:
        tools.approve_weekly_plan(plan)


def _fill_rest(plan: int, skip: set[str]) -> None:
    """A dinner on every other night of the week, each with an ingredient of
    its own — so there is no FREE night and a called-off tonight has to drop
    its dish rather than move it. (A night with no dinner row counts as free,
    which is what makes a sparsely-seeded week the wrong fixture for testing
    the drop.)"""
    for day in DAYS:
        if day in skip:
            continue
        dish = f"Filler {day}"
        tools.add_recipe(dish, ingredients=[
            {"item": f"Filler greens {day}", "qty": "1 bag", "category": "produce"},
        ], prep_time_minutes=5, cook_time_minutes=20, default_servings=4)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)


def _dinner_row(day: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT mpe.id, mpe.slot_state, mpe.reasoning, mpe.derived_from_json, "
        "       COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchone()
    conn.close()
    return row


def _dinner_rows_count(day: str) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) n FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), day),
    ).fetchone()["n"]
    conn.close()
    return n


def _grocery_snapshot():
    return [
        (r["item"], r["quantity"], r["status"])
        for r in tools.list_grocery_list(status="all")
    ]


def _buy_everything():
    for row in tools.list_grocery_list():
        tools.mark_grocery_item(row["id"], "purchased")


# ------------------------------------------------- the dish: moved or dropped

def test_a_free_night_takes_the_dish_and_tonight_goes_empty():
    """CATCH. The default when a later night is free: the plan keeps the
    dish, so nothing has to be re-decided or re-bought."""
    plan = _plan()
    _week_with_a_free_night(plan, free_day=THU)
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "night_off"
    assert out["dish"] == DISHES[2]
    assert out["moved_to"] == THU and out["moved_to_weekday"] == "Thursday"
    assert _dinner_row(THU)["meal"] == DISHES[2]
    assert _dinner_row(TONIGHT)["slot_state"] == "planned_empty"
    # And exactly one row on the night — the open slot that came back the
    # other way is gone rather than sitting beside the empty one.
    assert _dinner_rows_count(TONIGHT) == 1


def test_a_night_with_no_dinner_row_at_all_counts_as_free():
    """CATCH. The 21-slot rule says a plan fills every slot, but a hand-made
    or part-cleared plan can leave a night with no dinner row; that night is
    free in exactly the same sense."""
    plan = _plan()
    for day, dish in zip(DAYS, DISHES):
        if day == FRI:
            continue
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan)
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["moved_to"] == FRI
    assert _dinner_row(FRI)["meal"] == DISHES[2]
    assert _dinner_row(TONIGHT)["slot_state"] == "planned_empty"


def test_the_first_free_night_wins_not_the_last():
    """CATCH. "The next free night", in order — a household should not find
    tonight's dinner parked at the far end of the week."""
    plan = _plan()
    for day, dish in zip(DAYS, DISHES):
        if day in (THU, SAT):
            tools.plan_slot_open(plan, day, "dinner", "Yours to fill.")
            continue
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    assert _tonight.tonight_night_off(now=AFTERNOON)["moved_to"] == THU


def test_a_night_nobody_is_home_is_not_a_free_night():
    """CATCH. planned_empty means nobody is eating, so moving dinner there
    would cook for an empty table. With nothing else free the dish drops.
    Belt and braces: _next_free_night only reads `open` as free, and the
    swap's own dry run refuses a planned_empty night anyway — removing
    either guard alone leaves this green, which is why it is here and why
    the docstring says so."""
    plan = _plan()
    for day, dish in zip(DAYS, DISHES):
        if day == THU:
            tools.plan_slot_empty(plan, day, "dinner", reason="You're out Thursday.")
            continue
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["moved_to"] is None
    assert _dinner_row(THU)["slot_state"] == "planned_empty"
    assert _dinner_row(THU)["meal"] is None


def test_an_earlier_free_night_is_never_used():
    """CATCH. Moving tonight's dinner onto a night that has already gone by
    would make it vanish from the week — the same rule the swap sheet's
    options follow."""
    plan = _plan()
    for day, dish in zip(DAYS, DISHES):
        if day == MON:
            tools.plan_slot_open(plan, day, "dinner", "Yours to fill.")
            continue
        _recipe(dish)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["moved_to"] is None
    assert _dinner_row(MON)["slot_state"] == "open"


def test_no_free_night_drops_the_dish_and_leaves_the_night_deliberately_empty():
    """CATCH. The ordinary fully-planned week: the dish comes off rather
    than displacing another night's dinner."""
    plan = _plan()
    _full_week(plan)
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "night_off"
    assert out["moved_to"] is None and out["dish"] == DISHES[2]
    row = _dinner_row(TONIGHT)
    assert row["slot_state"] == "planned_empty"
    assert row["reasoning"] == _tonight.NIGHT_OFF_REASON
    assert json.loads(row["derived_from_json"])["constraint"] == _tonight.NIGHT_OFF_CONSTRAINT
    # Every other night is exactly as it was.
    for day, dish in zip(DAYS, DISHES):
        if day == TONIGHT:
            continue
        assert _dinner_row(day)["meal"] == dish


def test_the_night_is_planned_empty_and_never_open():
    """CATCH. `open` is a decision handed back, so Now would turn round and
    ask "Tonight needs a dinner" — the question just answered. planned_empty
    needs no decision and must never be offered as one."""
    plan = _plan()
    _full_week(plan)
    _tonight.tonight_night_off(now=AFTERNOON)
    assert _dinner_row(TONIGHT)["slot_state"] == "planned_empty"
    kinds = [i["type"] for i in tools.get_needs_you_items()]
    assert "dinner_decision" not in kinds


# ------------------------------------------------------------ the grocery list

def test_a_dropped_dish_puts_back_what_nobody_has_bought_yet():
    """CATCH. The week no longer needs tonight's share, so the still-needed
    lines come back down — through the plan's own ledger reversal, not a
    second arithmetic."""
    plan = _plan()
    _full_week(plan)
    before = dict((item, qty) for item, qty, _ in _grocery_snapshot())
    assert before["Shrimp"] == "7 lbs"
    _tonight.tonight_night_off(now=AFTERNOON)
    after = dict((item, qty) for item, qty, _ in _grocery_snapshot())
    assert after["Shrimp"] == "6 lbs"


def test_the_list_is_not_touched_when_the_shop_already_happened():
    """CATCH. A line in a cart or through the till has been acted on — this
    never yanks it back, whatever happens to the dinner it was for."""
    plan = _plan()
    _full_week(plan)
    _buy_everything()
    before = _grocery_snapshot()
    _tonight.tonight_night_off(now=AFTERNOON)
    assert _grocery_snapshot() == before


def test_a_dish_that_moves_leaves_the_list_completely_alone():
    """CATCH. Same dishes, same week, same shopping — the move is a re-date
    and nothing else."""
    plan = _plan()
    _week_with_a_free_night(plan, free_day=THU)
    before = _grocery_snapshot()
    assert _tonight.tonight_night_off(now=AFTERNOON)["moved_to"] == THU
    assert _grocery_snapshot() == before


# --------------------------------------------------------------- "use soon"

def test_what_was_already_bought_and_wont_keep_is_flagged():
    """CATCH. The fresh things are in the house with nothing pointing at
    them now, so they are named — and the pantry ones, which keep, are
    not."""
    plan = _plan()
    for day, dish in zip(DAYS, DISHES):
        # Rice keeps (pantry) and foil isn't food at all; the shrimp is
        # fresh but every other dinner this week wants it too. Only this
        # dish's own greens are freed by dropping it.
        _recipe(dish, ingredients=[
            {"item": "Shrimp", "qty": "1 lb", "category": "meat"},
            {"item": "Rice", "qty": "1 cup", "category": "pantry"},
            # Both of this dish's own: the foil is freed by dropping it and
            # is still never named, because foil is not food.
            {"item": f"Foil for {dish}", "qty": "1 roll", "category": "household"},
            {"item": f"Greens for {dish}", "qty": "1 bag", "category": "produce"},
        ])
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.approve_weekly_plan(plan)
    _buy_everything()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["use_soon"] == [f"Greens for {DISHES[2]}"]
    # And it outlives the one card that first said it.
    summaries = [i["summary"] for i in tools.get_attention_items() if i["kind"] == _tonight.USE_SOON_KIND]
    assert summaries == [
        f"Use the Greens for {DISHES[2]} soon — {DISHES[2]} came off the plan."
    ]


def test_nothing_bought_means_nothing_to_use_up():
    """CATCH. The lines came back off the list instead, so there is nothing
    in the house to warn about and no note is queued."""
    plan = _plan()
    _full_week(plan)
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["use_soon"] == []
    assert [i for i in tools.get_attention_items() if i["kind"] == _tonight.USE_SOON_KIND] == []


def test_the_use_soon_note_is_read_back_off_the_night_itself():
    """CATCH. The ledger it was derived from is gone by the time anything
    reads the card, so the night carries its own note."""
    plan = _plan()
    _full_week(plan)
    _buy_everything()
    _tonight.tonight_night_off(now=AFTERNOON)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["reason"] == "night_off" and out["night_off"] is True
    assert out["use_soon"] == [f"Greens for {DISHES[2]}"]
    assert out["ask"] is False


def test_a_dish_that_moves_flags_nothing():
    """CATCH. The food is still for that dish — just on another night."""
    plan = _plan()
    _week_with_a_free_night(plan, free_day=THU)
    _buy_everything()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["moved_to"] == THU and out["use_soon"] == []


def test_something_already_thawed_for_the_dropped_dish_is_flagged_too():
    """CATCH. The list can't see this one — the chicken may have been
    bought weeks ago — but a ticked fridge move means it is on a shelf for
    a dinner nobody is cooking now."""
    plan = _plan()
    _full_week(plan)
    entry = _dinner_row(TONIGHT)["id"]
    conn = get_conn()
    inv = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, location) "
        "VALUES (?, 'Chicken thighs', '2 lbs', 'freezer')",
        (tools.household_id(),),
    ).lastrowid
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, meal_plan_entry_id, inventory_item_id, "
        "task_date, description, task_type, status, related_meal) "
        "VALUES (?, ?, ?, ?, ?, ?, 'defrost', 'done', ?)",
        (tools.household_id(), plan, entry, inv, TUE,
         "Move the chicken thighs to the fridge — for Wednesday’s dinner.", DISHES[2]),
    )
    conn.commit()
    conn.close()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["use_soon"] == ["Chicken thighs"]


def test_a_fridge_move_still_pending_is_not_a_thing_to_use_up():
    """CATCH. Still frozen, so it keeps — only a ticked move means the food
    is out."""
    plan = _plan()
    _full_week(plan)
    entry = _dinner_row(TONIGHT)["id"]
    conn = get_conn()
    inv = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, location) "
        "VALUES (?, 'Chicken thighs', '2 lbs', 'freezer')",
        (tools.household_id(),),
    ).lastrowid
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, meal_plan_entry_id, inventory_item_id, "
        "task_date, description, task_type, status, related_meal) "
        "VALUES (?, ?, ?, ?, ?, ?, 'defrost', 'pending', ?)",
        (tools.household_id(), plan, entry, inv, TONIGHT,
         "Move the chicken thighs to the fridge — for Wednesday’s dinner.", DISHES[2]),
    )
    conn.commit()
    conn.close()
    assert _tonight.tonight_night_off(now=AFTERNOON)["use_soon"] == []


# ------------------------------------------------------------ what Now shows

def test_tonights_cook_and_its_fridge_move_come_off_now():
    """CATCH. The night is off, so nothing about it is still offered — and
    nothing is left reading as overdue either."""
    plan = _plan()
    _full_week(plan)
    entry = _dinner_row(TONIGHT)["id"]
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, meal_plan_entry_id, task_date, "
        "description, task_type, status, related_meal) VALUES (?, ?, ?, ?, ?, 'defrost', 'pending', ?)",
        (tools.household_id(), plan, entry, TONIGHT,
         f"Move the shrimp to the fridge — for Wednesday’s {DISHES[2]}.", DISHES[2]),
    )
    conn.commit()
    conn.close()
    before = tools.today_moves(TONIGHT)["moves"]
    assert any(m["kind"] == "cook" for m in before)
    assert any(m["kind"] == "fridge" for m in before)
    _tonight.tonight_night_off(now=AFTERNOON)
    after = tools.today_moves(TONIGHT)["moves"]
    assert [m for m in after if m["kind"] in ("cook", "fridge")] == []
    assert not any(m.get("overdue") for m in after)


def test_a_fridge_move_already_done_stays_done_when_the_dish_moves():
    """CATCH. Nothing here un-ticks work somebody actually did; the reminder
    travels with the dish, keeping its status."""
    plan = _plan()
    _week_with_a_free_night(plan, free_day=THU)
    entry = _dinner_row(TONIGHT)["id"]
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, meal_plan_entry_id, task_date, "
        "description, task_type, status, related_meal) VALUES (?, ?, ?, ?, ?, 'defrost', 'done', ?)",
        (tools.household_id(), plan, entry, TUE,
         f"Move the shrimp to the fridge — for Wednesday’s {DISHES[2]}.", DISHES[2]),
    )
    conn.commit()
    conn.close()
    _tonight.tonight_night_off(now=AFTERNOON)
    conn = get_conn()
    row = conn.execute(
        "SELECT task_date, status FROM prep_tasks WHERE household_id = ? AND task_type = 'defrost'",
        (tools.household_id(),),
    ).fetchone()
    conn.close()
    assert row["status"] == "done"
    assert row["task_date"] == WED  # moved by the same one day the dinner did


# --------------------------------------------------------------- refusals

def test_a_dinner_already_cooked_is_refused_in_words_and_nothing_changes():
    """CATCH. A tick is a record of something that happened, and no answer
    about tonight gets to delete one."""
    plan = _plan()
    _full_week(plan)
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET cooked_status = 'done' WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), TONIGHT),
    )
    conn.commit()
    conn.close()
    before = _grocery_snapshot()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "refused"
    assert "already ticked off as cooked" in out["message"]
    assert _dinner_row(TONIGHT)["meal"] == DISHES[2]
    assert _grocery_snapshot() == before


def test_a_dinner_cooked_double_for_a_later_night_is_refused():
    """CATCH. Dropping it would leave that night holding a reheat with no
    batch behind it — drop_dish_from_day refuses the same thing."""
    plan = _plan()
    _full_week(plan)
    entry = _dinner_row(TONIGHT)["id"]
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
        (json.dumps({"make_double_for": [f"{FRI}:dinner"]}), entry),
    )
    conn.commit()
    conn.close()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "refused"
    assert "Friday" in out["message"]
    assert _dinner_row(TONIGHT)["meal"] == DISHES[2]


def test_no_plan_covering_tonight_is_an_answer_not_a_crash():
    """CATCH. Refusals are sentences a person reads, at 200 — the codebase's
    own rule for a write that declined on purpose."""
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "refused"
    assert "no plan covering tonight" in out["message"]


# --------------------------------------------------------- the quieter cases

def test_an_open_dinner_tonight_is_settled_too():
    """CATCH. Saying "we're going out" answers the "Tonight needs a dinner"
    question as well — otherwise Now goes on asking it."""
    plan = _plan()
    tools.plan_slot_open(plan, TONIGHT, "dinner", "Yours to fill.")
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "night_off" and out["dish"] is None
    assert _dinner_row(TONIGHT)["slot_state"] == "planned_empty"
    assert _dinner_rows_count(TONIGHT) == 1


def test_saying_it_twice_changes_nothing_and_keeps_the_note():
    """CATCH. A second tap, or the other phone: the answer stands, and the
    use-soon note is not torn down and rebuilt empty."""
    plan = _plan()
    _full_week(plan)
    _buy_everything()
    first = _tonight.tonight_night_off(now=AFTERNOON)
    again = _tonight.tonight_night_off(now=AFTERNOON)
    assert again["status"] == "night_off" and again["already"] is True
    assert again["already_reason"] == _tonight.NIGHT_OFF_CONSTRAINT
    assert again["use_soon"] == first["use_soon"] == [f"Greens for {DISHES[2]}"]
    assert _dinner_rows_count(TONIGHT) == 1


def test_an_away_night_is_not_reported_as_a_night_off_already_taken():
    """CATCH. Nothing is written either way — but the assistant reads this
    result out loud, and "that's already a night off" about a trip is the
    app telling the household something that isn't true."""
    plan = _plan()
    tools.plan_slot_empty(plan, TONIGHT, "dinner", reason="You're away.")
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["status"] == "night_off" and out["already"] is True
    assert out["already_reason"] == "away"


def test_a_done_fridge_move_record_goes_with_a_dropped_dinner():
    """CATCH — and a CHARACTERISATION, stated plainly because the ticket's
    criterion says "a fridge move already done stays done" and this is the
    one branch where the RECORD does not.

    The food itself is handled (it lands in use_soon). What goes is the
    prep_tasks row saying somebody did the work — clear_plan_slot deletes a
    meal's prep rows with the meal, by its own documented rule, and this
    answer uses clear_plan_slot rather than inventing a second removal.
    Nothing is ever un-ticked and nothing reads as missed; the history is
    simply gone. Invert this test if that is ever deemed worth keeping."""
    plan = _plan()
    _full_week(plan)
    entry = _dinner_row(TONIGHT)["id"]
    conn = get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, meal_plan_entry_id, task_date, "
        "description, task_type, status, related_meal) VALUES (?, ?, ?, ?, ?, 'defrost', 'done', ?)",
        (tools.household_id(), plan, entry, TUE, "Move the shrimp to the fridge.", DISHES[2]),
    )
    conn.commit()
    conn.close()
    _tonight.tonight_night_off(now=AFTERNOON)
    conn = get_conn()
    left = conn.execute(
        "SELECT COUNT(*) n FROM prep_tasks WHERE household_id = ? AND task_type = 'defrost'",
        (tools.household_id(),),
    ).fetchone()["n"]
    conn.close()
    assert left == 0


def test_a_night_the_household_was_simply_away_for_is_not_a_night_off():
    """CATCH. The card has always been silent about an away night; only a
    night they called off says "Night off — enjoy"."""
    plan = _plan()
    tools.plan_slot_empty(plan, TONIGHT, "dinner", reason="You're out.")
    out = tools.tonight_check(now=AFTERNOON)
    assert out["reason"] == "away" and out["night_off"] is False


def test_use_soon_never_names_food_another_planned_dinner_still_needs():
    """CATCH. Two dinners share one purchased bag of spinach. Calling
    tonight off doesn't free it — and "use the spinach soon" would have the
    household eat Friday's dinner out of the fridge on the app's own
    instruction, leaving Friday short."""
    plan = _plan()
    shared = [{"item": "Baby spinach", "qty": "1 bag", "category": "produce"}]
    for day, dish in ((TONIGHT, "Tonight's dish"), (FRI, "Friday's dish")):
        tools.add_recipe(dish, ingredients=list(shared), prep_time_minutes=5,
                         cook_time_minutes=20, default_servings=4)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    _fill_rest(plan, {TONIGHT, FRI})
    tools.approve_weekly_plan(plan)
    _buy_everything()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["moved_to"] is None  # the premise: the dish was dropped, not moved
    assert out["use_soon"] == []
    assert [i for i in tools.get_attention_items() if i["kind"] == _tonight.USE_SOON_KIND] == []
    # Friday is untouched and still needs it.
    assert _dinner_row(FRI)["meal"] == "Friday's dish"


def test_a_line_only_tonight_wanted_is_still_named():
    """CATCH. The guard above must not silence the whole note — a line no
    other meal holds is exactly what a dropped dinner frees."""
    plan = _plan()
    tools.add_recipe("Tonight's dish", ingredients=[
        {"item": "Baby spinach", "qty": "1 bag", "category": "produce"},
    ], prep_time_minutes=5, cook_time_minutes=20, default_servings=4)
    tools.plan_meal(TONIGHT, "Tonight's dish", slot="dinner", weekly_plan_id=plan)
    _fill_rest(plan, {TONIGHT})
    tools.approve_weekly_plan(plan)
    _buy_everything()
    out = _tonight.tonight_night_off(now=AFTERNOON)
    assert out["moved_to"] is None  # the premise: there was nowhere to move it
    assert out["use_soon"] == ["Baby spinach"]


# ------------------------------------------------------------ the preview

def test_the_card_says_where_the_dish_would_go_before_it_is_tapped():
    """CATCH. §8 rule 7 — a control whose effect you have to guess has
    failed. The preview is the same dry run the answer itself uses."""
    plan = _plan()
    _week_with_a_free_night(plan, free_day=THU)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["night_off_moves_to"] == THU
    assert out["night_off_moves_to_weekday"] == "Thursday"


def test_the_preview_says_nothing_to_move_to_on_a_full_week():
    """CATCH. The row then reads "comes off the week", which is what will
    happen."""
    plan = _plan()
    _full_week(plan)
    out = tools.tonight_check(now=AFTERNOON)
    assert out["night_off_moves_to"] is None
    assert out["night_off_blocked"] is False


def test_the_preview_never_promises_something_the_tap_would_refuse():
    """CATCH. §8 rule 7 inverted: with tonight's dish cooked double for a
    later night and nowhere to move it, the row read "Dish comes off the
    week" and then refused on the tap. The card and the write run the same
    check now, and say the same sentence."""
    plan = _plan()
    _full_week(plan)
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (json.dumps({"make_double_for": [f"{FRI}:dinner"]}), tools.household_id(), TONIGHT),
    )
    conn.commit()
    conn.close()
    preview = tools.tonight_check(now=AFTERNOON)
    assert preview["night_off_moves_to"] is None
    assert preview["night_off_blocked"] is True
    tapped = _tonight.tonight_night_off(now=AFTERNOON)
    assert tapped["status"] == "refused"
    # One sentence, one source.
    assert preview["night_off_blocked_message"] == tapped["message"]
    assert "Friday" in tapped["message"]


# ------------------------------------------------------- the learned hint

def test_a_called_off_night_feeds_the_learned_takeout_hint():
    """CATCH. Nobody cooked, so next week's plan should go lighter on that
    weekday without anyone saying so again — through the intake's existing
    hint, not a parallel one."""
    next_week = (_monday() + datetime.timedelta(days=7)).isoformat()
    for weeks_back in (1, 2):
        start = (_monday() - datetime.timedelta(days=7 * weeks_back)).isoformat()
        plan = tools.create_weekly_plan(start)["weekly_plan_id"]
        day = tools._week_dates(start)[DAYS.index(TONIGHT)]
        tools.plan_slot_empty(
            plan, day, "dinner", reason=_tonight.NIGHT_OFF_REASON,
            derived_from={"constraint": _tonight.NIGHT_OFF_CONSTRAINT, "use_soon": []},
        )
    hints = _week_intake._observed_day_patterns(next_week)
    wednesday = tools._week_dates(next_week)[DAYS.index(TONIGHT)]
    assert hints.get(wednesday) == "Takeout two of the last four weeks"


def test_a_night_nobody_was_home_for_does_not_feed_the_hint():
    """GUARD. An away night is not a takeout night — being on a plane is
    not a pattern the planner should lighten Wednesdays for."""
    next_week = (_monday() + datetime.timedelta(days=7)).isoformat()
    for weeks_back in (1, 2):
        start = (_monday() - datetime.timedelta(days=7 * weeks_back)).isoformat()
        plan = tools.create_weekly_plan(start)["weekly_plan_id"]
        day = tools._week_dates(start)[DAYS.index(TONIGHT)]
        tools.plan_slot_empty(plan, day, "dinner", reason="You're away.")
    hints = _week_intake._observed_day_patterns(next_week)
    wednesday = tools._week_dates(next_week)[DAYS.index(TONIGHT)]
    assert hints.get(wednesday) in (None, "")


def test_the_word_takeout_in_a_meal_name_still_feeds_the_hint():
    """GUARD. The original signal is untouched."""
    next_week = (_monday() + datetime.timedelta(days=7)).isoformat()
    for weeks_back in (1, 2):
        start = (_monday() - datetime.timedelta(days=7 * weeks_back)).isoformat()
        plan = tools.create_weekly_plan(start)["weekly_plan_id"]
        day = tools._week_dates(start)[DAYS.index(TONIGHT)]
        tools.plan_meal(day, "Takeout", slot="dinner", weekly_plan_id=plan)
    hints = _week_intake._observed_day_patterns(next_week)
    wednesday = tools._week_dates(next_week)[DAYS.index(TONIGHT)]
    assert hints.get(wednesday) == "Takeout two of the last four weeks"


# ------------------------------------------------- two taps at the same time

def test_two_taps_at_once_settle_the_night_once_and_keep_the_dish():
    """CATCH. The whole answer is one BEGIN IMMEDIATE, so the loser sees the
    world the winner left rather than a half-finished one. Before this, both
    callers computed "the next free night" against the same pre-tap week:
    the second swap put the dish straight back onto tonight and the second
    clear deleted it — the dish gone from the week, the free night empty,
    its grocery line reversed, and BOTH phones told "it moves to
    Thursday"."""
    plan = _plan()
    _week_with_a_free_night(plan, free_day=THU)
    results = {}
    barrier = threading.Barrier(2)

    def go(tag):
        barrier.wait()
        try:
            results[tag] = _tonight.tonight_night_off(now=AFTERNOON)
        except Exception as exc:  # pragma: no cover - a crash is a failure below
            results[tag] = {"status": "raised", "message": repr(exc)}

    threads = [threading.Thread(target=go, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert {r["status"] for r in results.values()} == {"night_off"}
    # Exactly one of them did the work; the other says so.
    did = [r for r in results.values() if not r["already"]]
    already = [r for r in results.values() if r["already"]]
    assert len(did) == 1 and len(already) == 1
    assert did[0]["moved_to"] == THU
    # The dish is still on the week, exactly once, and tonight is settled.
    assert _dinner_row(THU)["meal"] == DISHES[2]
    assert _dinner_row(TONIGHT)["slot_state"] == "planned_empty"
    assert _dinner_rows_count(TONIGHT) == 1
    audit = tools.audit_plan_slots(plan)
    assert audit["duplicated"] == []
    assert {"date": TONIGHT, "slot": "dinner"} not in audit["missing"]


def test_two_taps_at_once_on_a_week_with_nowhere_to_move_leave_one_empty_row():
    """CATCH. The drop branch's own race: two clears before either insert
    used to leave TWO planned_empty rows on one slot — audit_plan_slots'
    `duplicated`, which CLAUDE.md calls "how a night nobody is home ends up
    with groceries bought for it"."""
    plan = _plan()
    _full_week(plan)
    barrier = threading.Barrier(2)
    results = {}

    def go(tag):
        barrier.wait()
        try:
            results[tag] = _tonight.tonight_night_off(now=AFTERNOON)
        except Exception as exc:  # pragma: no cover
            results[tag] = {"status": "raised", "message": repr(exc)}

    threads = [threading.Thread(target=go, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert {r["status"] for r in results.values()} == {"night_off"}
    assert _dinner_rows_count(TONIGHT) == 1
    assert tools.audit_plan_slots(plan)["duplicated"] == []


# ------------------------------------------- all of it, or none of it

def test_a_failure_part_way_through_a_drop_leaves_the_plan_exactly_as_it_was():
    """CATCH. The toast says "That didn't work — the plan is as it was", and
    that sentence has to be true. Before this the dinner row was already
    deleted and its groceries already reversed when plan_slot_empty failed,
    leaving the day with NO dinner row at all — the one state schema.sql,
    audit_plan_slots and plan_slot_open's own docstring all say cannot
    exist."""
    plan = _plan()
    _full_week(plan)
    before_rows = [(d, _dinner_row(d)["slot_state"], _dinner_row(d)["meal"]) for d in DAYS]
    before_list = _grocery_snapshot()

    from app.tools import weekly_plan as _wp
    real = _wp.plan_slot_empty
    _wp.plan_slot_empty = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced"))
    try:
        with pytest.raises(RuntimeError):
            _tonight.tonight_night_off(now=AFTERNOON)
    finally:
        _wp.plan_slot_empty = real

    assert [(d, _dinner_row(d)["slot_state"], _dinner_row(d)["meal"]) for d in DAYS] == before_rows
    assert _grocery_snapshot() == before_list
    assert {"date": TONIGHT, "slot": "dinner"} not in tools.audit_plan_slots(plan)["missing"]


def test_a_failure_part_way_through_a_move_puts_the_dish_back_too():
    """CATCH. The move and the empty night are one write: before this the
    dish HAD moved, tonight was absent, and tonight_check then answered
    'unplanned' — so Now turned round and asked "Tonight needs a dinner",
    the question just answered."""
    plan = _plan()
    _week_with_a_free_night(plan, free_day=THU)
    before_rows = [(d, _dinner_row(d)["slot_state"], _dinner_row(d)["meal"]) for d in DAYS]
    before_list = _grocery_snapshot()

    from app.tools import weekly_plan as _wp
    real = _wp.plan_slot_empty
    _wp.plan_slot_empty = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced"))
    try:
        with pytest.raises(RuntimeError):
            _tonight.tonight_night_off(now=AFTERNOON)
    finally:
        _wp.plan_slot_empty = real

    assert [(d, _dinner_row(d)["slot_state"], _dinner_row(d)["meal"]) for d in DAYS] == before_rows
    assert _grocery_snapshot() == before_list
    assert tools.tonight_check(now=AFTERNOON)["reason"] != "unplanned"


# ------------------------------------------------------------------- chat

def test_saying_it_in_chat_does_the_same_thing():
    """CATCH. "we're going out tonight" is a thing people say rather than
    tap, so it is a real tool and it is the same write."""
    from app import agent
    assert "take_the_night_off" in agent.TOOL_FUNCTIONS
    assert agent.TOOL_FUNCTIONS["take_the_night_off"] is tools.tonight_night_off
    names = [t["name"] for t in agent.TOOL_DEFINITIONS]
    assert "take_the_night_off" in names
    plan = _plan()
    _full_week(plan)
    out = agent.TOOL_FUNCTIONS["take_the_night_off"](day=TONIGHT)
    assert out["status"] == "night_off"
    assert _dinner_row(TONIGHT)["slot_state"] == "planned_empty"


def test_the_chat_card_points_at_the_plan():
    """CATCH. The tool changes a night of the plan, so Plan is the screen
    that goes stale — and shell.js re-reads Now's card off the same tag."""
    from app import main
    assert "take_the_night_off" in main._WEEK_TOOLS
    assert "refreshTonightFromPlan" in SHELL_JS


# ------------------------------------------------------------- the routes

def test_the_route_settles_tonight_and_says_what_it_did(signed_in):
    """CATCH. One POST, and the card's next read says the night is off."""
    plan = _plan()
    _full_week(plan)
    res = signed_in.post("/api/today/tonight/night-off", json={"date": TONIGHT})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "night_off" and body["dish"] == DISHES[2]
    assert _dinner_row(TONIGHT)["slot_state"] == "planned_empty"


def test_a_refusal_is_a_200_with_a_sentence(signed_in):
    """CATCH. Refusals are answers, not errors — 4xx is for things that are
    genuinely broken."""
    res = signed_in.post("/api/today/tonight/night-off", json={"date": TONIGHT})
    assert res.status_code == 200
    assert res.json()["status"] == "refused"


def test_a_malformed_date_is_a_400(signed_in):
    """CATCH. That one really is broken input."""
    res = signed_in.post("/api/today/tonight/night-off", json={"date": "not-a-date"})
    assert res.status_code == 400


# ------------------------------------------------------------ the screens

def test_the_sheet_offers_the_answer_alongside_the_swaps():
    """CATCH. The row is in both shapes of the sheet — with nights to trade
    with, and with none, since a night off needs nothing to trade with."""
    assert "tonightNightOffRowHtml" in SHELL_JS
    assert "Not tonight — we’re going out" in SHELL_JS
    # Appended to the options stack AND to the nothing-to-offer branch.
    assert SHELL_JS.count("tonightNightOffRowHtml(data)") >= 2
    assert "runTonightNightOff" in SHELL_JS
    assert "/api/today/tonight/night-off" in SHELL_JS
    assert "runTonightNightOff" in SHELL_HTML or "tonight-night-off" in SHELL_JS


def test_the_card_states_the_night_and_asks_nothing_more():
    """CATCH. No buttons on it — "asks nothing more" is the whole point."""
    start = SHELL_JS.index("function renderTonightAsk")
    block = SHELL_JS[start:start + 1400]
    assert "Night off — enjoy." in block
    assert "tonight-off-line" in block
    # The statement card renders before the question's own markup, and
    # carries none of its buttons.
    statement = block[:block.index("if (!data || !data.ask")]
    assert "tonight-yes" not in statement and "tonight-else" not in statement


def test_the_rows_sub_line_and_the_toast_name_the_dish_the_same_way():
    """CATCH. tonightDishName appends " leftovers" for a reheat night, which
    read "Bean Chili leftovers comes off the week" on the row and "Bean
    Chili is off the week" in the toast — one tap, two names, and a verb
    that didn't agree with either. The row uses the plan's own name, which
    is what the server sends back."""
    start = SHELL_JS.index("function tonightNightOffRowHtml")
    block = SHELL_JS[start:start + 1200]
    assert "tonightDishName" not in block
    assert "data.dinner.meal" in block
    # And it prefers the server's refusal sentence over its own promise.
    assert "night_off_blocked_message" in block


def test_the_row_never_writes_its_own_version_of_a_refusal():
    """CATCH. The blocked sentence is the server's, word for word — the
    screen must not paraphrase a rule it doesn't own."""
    start = SHELL_JS.index("function tonightNightOffRowHtml")
    block = SHELL_JS[start:start + 1200]
    assert "also feeds" not in block


def test_the_answer_is_not_a_second_apricot():
    """CATCH. Rule 5 — Now's one apricot is the dock, and the sheet spends
    no accent on its swap rows either."""
    start = SHELL_CSS.index(".tonight-off-row")
    block = SHELL_CSS[start:start + 1200]
    assert "--apricot" not in block
    assert "--celadon-tint" in block


def test_every_colour_in_the_new_rules_goes_through_a_token():
    """CATCH. Rule 9 — a literal hex outside theme.css is a review
    failure."""
    import re
    start = SHELL_CSS.index(".tonight-off-row")
    block = SHELL_CSS[start:SHELL_CSS.index("/* The \"Add something\" sheet")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)
