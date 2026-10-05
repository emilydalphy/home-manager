"""
Move a meal to a day the week holds NO row for (card 7, 2026-10-05).

Gowthami's household, 2026-10-04: "Move function is not working and its
only giving certain days and not sure why it's not recommending all."

MEASURED on a throwaway DB before anything was changed, with a week whose
Thursday had no dinner row, a Friday nobody is home for (planned_empty) and
a Saturday handed back as an open question:

    BEFORE   OFFERED ['Mon', 'Wed', 'Sat', 'Sun']
             BLOCKED ['Thu'  "No dinner planned",
                      'Fri'  "Nobody’s home"]
    AFTER    OFFERED ['Mon', 'Wed', 'Thu', 'Sat', 'Sun']
             BLOCKED ['Fri'  "Nobody’s home for dinner"]

So the empty-day rule really was it, and planned_empty is deliberately
NOT part of the fix: it means nobody is home, or the household asked for
none of that meal, and such a slot must never be offered as a decision.

The day the meal LEAVES is handed back as an `open` question — the
drop_dish_from_day precedent, written inside the move's own transaction so
the gap between re-dating the rows and stating the day is never a
genuinely absent slot. Undo takes that question back off before the meal
returns to it, or the day would hold two rows for one slot.

MUTATIONS RUN, with their measured red counts over this file (16 tests),
each read off the run rather than predicted:

  1. plan_move refuses an empty destination again — main's
     behaviour                                              -> 9 failed
  2. _somebody_home always True (the nobody-home check gone) -> 1 failed
  3. household_size 0 read as nobody home                    -> 1 failed
  4. _open_the_days_left_behind a no-op (the day left
     absent instead of handed back)                          -> 5 failed
  5. the day left behind written planned_empty, not open     -> 6 failed
  6. Undo not taking its open row back off                   -> 1 failed
  7. Undo taking off a row the household answered since      -> 1 failed
  8. move_options writing the reason over the day's LABEL
     again instead of into the line under it                 -> 4 failed

And one that bites somewhere else, said rather than dressed up:

  9. _nobody_home_row comparing _empty_reason's SENTENCE again —
     the shared-string reader this card's own first cut broke —
     reddens NOTHING here and 1 in test_move_meal_between_days.py
     (test_the_picker_lists_every_other_day_and_dims_the_impossible_
     ones_with_a_reason, whose leftovers-land-on-a-nobody-home-day
     assertion is what caught it in the pre-flight).
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import tools
from app.db import get_conn
from app.tools import meal_move, week_intake


def _monday() -> datetime.date:
    from conftest import household_today
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


# Next week, so no day has gone by.
WEEK = (_monday() + datetime.timedelta(days=7)).isoformat()
MON, TUE, WED, THU, FRI, SAT, SUN = tools._week_dates(WEEK)
NAMES = {MON: "Mon", TUE: "Tue", WED: "Wed", THU: "Thu", FRI: "Fri", SAT: "Sat", SUN: "Sun"}


def _recipe(name: str, minutes: int = 30) -> None:
    tools.add_recipe(name, ingredients=[{"item": f"{name} onion", "qty": "1", "category": "produce"}],
                     prep_time_minutes=10, cook_time_minutes=minutes - 10, default_servings=4)


def _row(day: str, slot: str = "dinner") -> dict | None:
    conn = get_conn()
    r = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.open_reason, "
        "mpe.derived_from_json, COALESCE(rc.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes rc ON rc.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id",
        (tools.household_id(), day, slot),
    ).fetchone()
    conn.close()
    if r is None:
        return None
    out = dict(r)
    out["derived"] = json.loads(out.pop("derived_from_json") or "{}")
    return out


def _rows(day: str, slot: str = "dinner") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, slot_state FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ?",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _options(plan: int, entry_id: int) -> dict:
    return {d["date"]: d for d in meal_move.move_options(plan, entry_id)["days"]}


def _delete(entry_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM meal_plan_entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()


def _week(gap_day: str = THU) -> dict:
    """Six dinners and ONE day with no dinner row at all — the shape the
    card asks to be reproduced on."""
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    dishes = ["Chicken Stew", "Tacos", "Pasta Bake", "Salmon", "Curry", "Pizza", "Roast"]
    for d in dishes:
        _recipe(d)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    ids = {}
    for day, dish in zip([MON, TUE, WED, THU, FRI, SAT, SUN], dishes):
        ids[day] = tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)["entry_id"]
    _delete(ids.pop(gap_day))
    return {"plan": plan, "ids": ids}


# ------------------------------------------------- the card's own two tests

def test_an_empty_day_is_a_destination_when_somebody_is_home():
    """The card: "Moving to a day with no meal of that type planned is
    ALLOWED when someone is home for it"."""
    w = _week()
    days = _options(w["plan"], w["ids"][TUE])

    assert days[THU]["ok"] is True
    assert days[THU]["meal"] == "Nothing planned"
    assert days[THU]["reason"] == ""
    # Every other day of the week, the gap day included — which is the
    # tester's whole complaint ("not recommending all").
    assert sorted(NAMES[d] for d, v in days.items() if v["ok"]) == \
        ["Fri", "Mon", "Sat", "Sun", "Thu", "Wed"]


def test_a_nobody_home_day_is_still_blocked_and_says_why():
    """The card's second test, and the half the fix must NOT widen into:
    planned_empty must never be offered as a decision."""
    w = _week()
    tools.plan_slot_empty(w["plan"], THU, "dinner", "You're out — nothing planned.",
                          derived_from={"constraint": "nobody_home"})

    days = _options(w["plan"], w["ids"][TUE])
    assert days[THU]["ok"] is False
    assert days[THU]["reason"] == "Nobody’s home for dinner"


# ------------------------------------------------------------- the placement

def test_the_meal_lands_on_the_empty_day_and_the_old_day_is_handed_back():
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], THU)

    assert out["status"] == "moved"
    assert _row(THU)["id"] == w["ids"][TUE]
    left = _row(TUE)
    assert left["slot_state"] == "open"
    assert left["meal"] is None
    assert left["open_reason"] == "You moved the Tacos to Thursday, so this one is yours to fill."
    # Never absent and never doubled: one row, in the one state that is a
    # question rather than a statement.
    assert len(_rows(TUE)) == 1
    assert tools.audit_plan_slots(w["plan"])["duplicated"] == []


def test_the_day_left_behind_is_open_and_never_planned_empty():
    """planned_empty would say "nobody's home" about a day everybody is
    home for, and must never be offered as a decision."""
    w = _week()
    meal_move.move_meal(w["plan"], w["ids"][TUE], THU)
    assert _row(TUE)["slot_state"] == "open"
    assert _row(TUE)["derived"].get("constraint") == meal_move.MOVE_OPENED_CONSTRAINT


def test_a_day_that_already_has_that_meal_still_swaps_the_two():
    """Pinned, as the card asks: the existing behaviour is untouched."""
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], WED)

    assert out["status"] == "moved"
    assert _row(WED)["id"] == w["ids"][TUE]
    assert _row(TUE)["id"] == w["ids"][WED]
    assert _row(TUE)["slot_state"] == "planned"
    # Nothing was handed back: every day still holds a meal.
    assert _row(WED)["slot_state"] == "planned"


def test_a_lunch_moves_onto_an_empty_lunch_day_too():
    tools.add_member("Emily")
    for n in ("Lentil Soup", "Chicken Pancake"):
        _recipe(n, 20)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    soup = tools.plan_meal(MON, "Lentil Soup", slot="lunch", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(WED, "Chicken Pancake", slot="lunch", weekly_plan_id=plan)

    days = _options(plan, soup)
    assert days[TUE]["ok"] is True and days[TUE]["meal"] == "Nothing planned"
    assert meal_move.move_meal(plan, soup, TUE)["status"] == "moved"
    assert _row(TUE, "lunch")["id"] == soup
    assert _row(MON, "lunch")["slot_state"] == "open"


def test_a_dinner_whose_leftovers_night_would_land_empty_moves_too():
    """The chain's own night landing where the week holds no row is the
    same answer: it goes there, and the night it left is handed back."""
    tools.add_member("Emily")
    for n in ("Chicken Stew", "Gnocchi", "Tacos"):
        _recipe(n)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    stew = tools.plan_meal(MON, "Chicken Stew", slot="dinner", weekly_plan_id=plan)["entry_id"]
    left = tools.plan_meal(TUE, "Chicken Stew", slot="dinner", weekly_plan_id=plan,
                           derived_from={"links_to": f"{MON}:dinner"})["entry_id"]
    tools.plan_meal(WED, "Gnocchi", slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)

    # Thu and Fri both empty: the stew moves to Thu, its leftovers to Fri.
    out = meal_move.move_meal(plan, stew, THU)
    assert out["status"] == "moved"
    assert _row(THU)["id"] == stew and _row(FRI)["id"] == left
    assert _row(MON)["slot_state"] == "open" and _row(TUE)["slot_state"] == "open"
    chains = tools.plan_leftover_chains(plan)
    assert chains["leftovers"][left]["source"]["date"] == THU


def test_an_empty_day_nobody_is_home_for_is_refused_with_its_reason():
    """A position the week holds no row for AND nobody is home at: the
    attendance read is what catches it, since there is no row to say so."""
    w = _week()
    tools.set_slot_attendance(THU, "dinner", present_member_ids=[])
    # Attendance writes its own planned_empty row; take it off so the
    # position really is rowless, which is the case this check is for.
    gone = _row(THU)
    if gone is not None:
        _delete(gone["id"])

    days = _options(w["plan"], w["ids"][TUE])
    assert days[THU]["ok"] is False
    assert days[THU]["reason"] == "Nobody’s home for dinner"
    assert meal_move.move_meal(w["plan"], w["ids"][TUE], THU)["status"] == "refused"


def test_a_household_with_nobody_on_record_still_gets_its_empty_days():
    """household_size 0 is "nobody has said who lives here", not "nobody
    is home" — reading it as a refusal blocks every empty day for a
    household mid-onboarding."""
    dishes = ["Chicken Stew", "Tacos", "Pasta Bake"]
    for d in dishes:
        _recipe(d)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tacos = tools.plan_meal(TUE, "Tacos", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(MON, "Chicken Stew", slot="dinner", weekly_plan_id=plan)

    days = _options(plan, tacos)
    assert days[THU]["ok"] is True
    assert meal_move.move_meal(plan, tacos, THU)["status"] == "moved"


# -------------------------------------------------------------------- undo

def test_undo_puts_the_meal_back_and_takes_the_question_off():
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], THU)
    u = meal_move.undo_meal_move(w["plan"], out["move_id"])

    assert u["status"] == "restored"
    assert _row(TUE)["id"] == w["ids"][TUE]
    assert _row(TUE)["slot_state"] == "planned"
    assert len(_rows(TUE)) == 1, "the open row has to come off, or the day holds two"
    assert _row(THU) is None
    assert tools.audit_plan_slots(w["plan"])["duplicated"] == []


def test_undo_refuses_once_the_household_has_answered_that_question():
    """Putting the old meal back on top of a day they have since filled
    would throw their answer away — the same answer this Undo already
    gives a row that moved again since."""
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], THU)
    _recipe("Soup")
    tools.resolve_open_slot(w["plan"], TUE, "dinner", "Soup")

    with pytest.raises(ValueError, match="answered since"):
        meal_move.undo_meal_move(w["plan"], out["move_id"])
    # Nothing moved.
    assert _row(THU)["id"] == w["ids"][TUE]


def test_undo_of_an_ordinary_swap_still_opens_nothing():
    """A move between two days that both hold a meal is a permutation and
    has no question to hand back — before or after this card."""
    w = _week()
    out = meal_move.move_meal(w["plan"], w["ids"][TUE], WED)
    meal_move.undo_meal_move(w["plan"], out["move_id"])
    assert _row(TUE)["id"] == w["ids"][TUE] and _row(WED)["id"] == w["ids"][WED]
    assert _row(TUE)["slot_state"] == "planned" and _row(WED)["slot_state"] == "planned"


# ----------------------------------------------------------- what the sheet says

def test_every_blocked_day_says_its_reason_in_the_line_under_it():
    """The card: "each shows its reason as a visible line under the day,
    NOT only on tap". One place, one rule — it used to be written OVER the
    day's label for an empty or nobody-home day and UNDER it for every
    other, so which line a household had to read depended on why."""
    w = _week()
    tools.plan_slot_empty(w["plan"], THU, "dinner", "Out.",
                          derived_from={"constraint": "nobody_home"})
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?",
                 (w["ids"][SAT],))
    conn.commit()
    conn.close()

    days = _options(w["plan"], w["ids"][TUE])
    assert days[THU]["reason"] == "Nobody’s home for dinner"
    assert days[THU]["meal"] == "Not planned"          # the day's own label stays the label
    assert days[SAT]["reason"] == "Already cooked"
    # And the four reasons the card names are all sentences about a meal.
    for d in days.values():
        assert d["reason"] == "" or not d["reason"].endswith(".")


def test_the_dishs_own_leftovers_night_still_needs_no_line():
    """The row already reads "Chicken Stew leftovers", which is the
    reason — the one blocked day that deliberately says nothing twice."""
    tools.add_member("Emily")
    for n in ("Chicken Stew", "Gnocchi"):
        _recipe(n)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    stew = tools.plan_meal(MON, "Chicken Stew", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(TUE, "Chicken Stew", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{MON}:dinner"})
    tools.plan_meal(WED, "Gnocchi", slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)

    days = _options(plan, stew)
    assert days[TUE]["ok"] is False and days[TUE]["reason"] == ""
    assert days[TUE]["meal"] == "Chicken Stew leftovers"


def test_a_planned_empty_day_the_household_skipped_is_not_a_nobody_home_day():
    """Two different answers, two different sentences — and the
    discriminator is the CONSTRAINT, never _empty_reason's own wording."""
    w = _week()
    tools.plan_slot_empty(
        w["plan"], THU, "dinner", "No dinner that day.",
        derived_from={"constraint": week_intake.SKIPPED_DAY_CONSTRAINT},
    )
    days = _options(w["plan"], w["ids"][TUE])
    assert days[THU]["ok"] is False
    assert days[THU]["reason"] == "No dinner planned that day"

    # Read off the CONSTRAINT: _nobody_home_row takes a raw row, the shape
    # the snapshot hands it.
    raw = {"derived_from_json": json.dumps({"constraint": week_intake.SKIPPED_DAY_CONSTRAINT})}
    assert meal_move._nobody_home_row(raw) is False
    assert meal_move._nobody_home_row({"derived_from_json": json.dumps({"constraint": "nobody_home"})}) is True


def test_the_grocery_list_is_untouched_by_a_move_onto_an_empty_day():
    """A move re-dates rows in place, so their grocery links ride along —
    unchanged by this card, and worth pinning now that a move can leave a
    day with no meal on it."""
    w = _week()
    tools.approve_weekly_plan(w["plan"])
    conn = get_conn()
    before = [tuple(r) for r in conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? ORDER BY item, quantity",
        (tools.household_id(),)).fetchall()]
    conn.close()

    assert meal_move.move_meal(w["plan"], w["ids"][TUE], THU)["status"] == "moved"

    conn = get_conn()
    after = [tuple(r) for r in conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? ORDER BY item, quantity",
        (tools.household_id(),)).fetchall()]
    conn.close()
    assert after == before
