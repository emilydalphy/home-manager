"""
A freezer night goes with its cook when the cook comes off the week
outright (Emily, 2026-10-20).

Monday's Turkey Chili freezes a portion for Friday ("Leftovers from the
freezer — Monday's Turkey Chili") and feeds no other night. The Review
"−" on Monday, marking nobody home on Monday, and taking Monday's night
off (nowhere free to move it) each removed Monday's cook and left Friday
reheating a portion nobody would cook, thaw reminder and all. Now Friday
is handed back as a question too, and the "−"'s and the night off's Undo
put BOTH nights back exactly — the cook, its shopping line, Friday and its
fridge move.

A cook that MOVES keeps its freezer night, re-said for the new day
(tests/test_freezer_night_follows_its_cook.py); pinned again here for the
night off with a free night.
"""
import datetime

from conftest import household_today, on_the_weeks_monday

from app import tools
from app.db import get_conn

pytestmark = on_the_weeks_monday()


def _monday() -> datetime.date:
    today = household_today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


MON, TUE, WED, THU, FRI, SAT, SUN = (_day(i) for i in range(7))

MONDAYS = "Leftovers from the freezer — Monday’s Turkey Chili"


def _week(fill_every_night: bool = False):
    """Monday's chili, whose only later night is Friday's freezer portion
    (four days on, so the chain repair makes it one). `fill_every_night`
    plans every other dinner, so a night off has nowhere free to move it."""
    tools.add_member("Emily")
    tools.add_member("Jamie")
    tools.add_recipe("Turkey Chili", ingredients=[{"item": "Ground turkey", "qty": "1.5 lbs"}],
                     default_servings=2)
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "1 pack"}], default_servings=2)
    tools.add_recipe("Stir Fry", ingredients=[{"item": "Broccoli", "qty": "1 lb"}], default_servings=2)
    plan_id = tools.create_weekly_plan(MON)["weekly_plan_id"]
    cook = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.plan_meal(FRI, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{MON}:dinner"})
    if fill_every_night:
        for d, dish in ((TUE, "Tacos"), (WED, "Stir Fry"), (THU, "Tacos"), (SAT, "Tacos"), (SUN, "Stir Fry")):
            tools.plan_meal(d, dish, slot="dinner", weekly_plan_id=plan_id)
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _dinners(plan_id)[FRI] == ("planned", MONDAYS)
    return plan_id, cook


def _dinners(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner'",
        (plan_id,),
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        assert r["date"] not in out, f"two dinner rows on {r['date']}"
        out[r["date"]] = (r["slot_state"], r["meal"])
    return out


def _state(plan_id):
    """Everything an Undo has to put back: the dinner rows by id, their
    fridge moves, and the shopping list."""
    conn = get_conn()
    entries = [tuple(r) for r in conn.execute(
        "SELECT id, date, slot, slot_state, recipe_id, freeform_meal, derived_from_json, cooked_status "
        "FROM meal_plan_entries WHERE weekly_plan_id = ? ORDER BY id", (plan_id,),
    ).fetchall()]
    prep = [tuple(r) for r in conn.execute(
        "SELECT id, task_date, description, meal_plan_entry_id, status FROM prep_tasks "
        "WHERE weekly_plan_id = ? ORDER BY id", (plan_id,),
    ).fetchall()]
    grocery = [tuple(r) for r in conn.execute(
        "SELECT * FROM grocery_items ORDER BY id",
    ).fetchall()]
    links = [tuple(r) for r in conn.execute(
        "SELECT meal_plan_entry_id, grocery_item_id, quantity FROM meal_plan_grocery_links "
        "ORDER BY meal_plan_entry_id, grocery_item_id",
    ).fetchall()]
    conn.close()
    return {"entries": entries, "prep": prep, "grocery": grocery, "links": links}


def _friday_thaws(plan_id):
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM prep_tasks pt JOIN meal_plan_entries mpe ON mpe.id = pt.meal_plan_entry_id "
        "WHERE pt.weekly_plan_id = ? AND mpe.date = ? AND pt.task_type = 'defrost'", (plan_id, FRI),
    ).fetchone()[0]
    conn.close()
    return n


def _turkey_on_the_list():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM grocery_items WHERE item = 'Ground turkey'").fetchone()[0]
    conn.close()
    return n


def test_the_review_minus_takes_the_freezer_night_with_the_cook_and_undo_puts_both_back():
    plan_id, cook = _week()
    before = _state(plan_id)
    assert _turkey_on_the_list() == 1

    out = tools.drop_dish_from_day(plan_id, cook)

    assert out["status"] == "dropped", out
    week = _dinners(plan_id)
    assert week[MON][0] == "open"
    assert week[FRI][0] == "open", week[FRI]
    assert _friday_thaws(plan_id) == 0
    assert _turkey_on_the_list() == 0
    assert out["can_undo"] and out["undo_entry_id"], out
    assert [d["date"] for d in out["freezer_nights_dropped"]] == [FRI]

    back = tools.drop_dish_undo(plan_id, out["undo_entry_id"])

    assert back["status"] == "restored", back
    assert _state(plan_id) == before


def test_nobody_home_on_the_cook_night_takes_the_freezer_night_with_it():
    plan_id, cook = _week()

    tools.set_slot_attendance(MON, "dinner", present_member_ids=[])

    week = _dinners(plan_id)
    assert week[MON][0] == "planned_empty"
    assert week[FRI][0] == "open", week[FRI]
    assert _friday_thaws(plan_id) == 0

    # Somebody home again hands Monday back as a question, as it always
    # has; with no cook on Monday, Friday stays one too. (There is no
    # Undo on an attendance change to put the chili back.)
    tools.set_slot_attendance(MON, "dinner", present_member_ids=["Emily"])
    week = _dinners(plan_id)
    assert week[MON][0] == "open"
    assert week[FRI][0] == "open"


def test_the_night_off_takes_the_freezer_night_with_the_cook_and_undo_puts_both_back():
    plan_id, cook = _week(fill_every_night=True)
    before = _state(plan_id)

    out = tools.tonight_night_off(day=MON)

    assert out["kind"] == "drop", out
    week = _dinners(plan_id)
    assert week[MON][0] == "planned_empty"
    assert week[FRI][0] == "open", week[FRI]
    assert _friday_thaws(plan_id) == 0
    assert _turkey_on_the_list() == 0
    assert out["can_undo"] is True, out
    assert out["said"].endswith("Friday is yours to fill."), out["said"]

    back = tools.tonight_night_off_undo(day=MON)

    assert back["status"] == "restored", back
    assert _state(plan_id) == before


def test_undo_refuses_once_the_freed_freezer_night_has_been_filled():
    plan_id, cook = _week()
    out = tools.drop_dish_from_day(plan_id, cook)
    tools.resolve_open_slot(plan_id, FRI, "dinner", "Tacos")

    back = tools.drop_dish_undo(plan_id, out["undo_entry_id"])

    assert back["status"] == "refused", back
    assert _dinners(plan_id)[FRI] == ("planned", "Tacos")


def test_a_night_off_that_moves_the_cook_keeps_its_freezer_night():
    """Moved, not removed: Friday follows the cook to Tuesday, as before."""
    plan_id, cook = _week()

    out = tools.tonight_night_off(day=MON)

    assert out["kind"] == "move" and out["moved_to"] == TUE, out
    assert _dinners(plan_id)[FRI] == ("planned", "Leftovers from the freezer — Tuesday’s Turkey Chili")


def test_undo_takes_back_the_use_soon_note_the_night_off_queued():
    """
    A night off that drops a dish already shopped for queues "Use the …
    soon — Turkey Chili came off the plan." on the attention queue, which
    Cook's fold, the morning text and every new chat read. Undo puts the
    chili back on tonight, so the note is no longer true and goes with it
    (defect hunt 2026-10-23: it stayed, and the next morning's text told
    the household to use up the turkey for a dinner still planned).
    """
    plan_id, cook = _week(fill_every_night=True)
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE item = 'Ground turkey'")
    conn.commit()
    conn.close()
    # Another night's note, still true, which Undo must leave alone.
    tools.add_attention_item("use_soon", "Use the salmon soon — Salmon Bowls came off the plan.",
                             {"items": ["salmon"], "dish": "Salmon Bowls"})

    out = tools.tonight_night_off(day=MON)
    assert out["kind"] == "drop" and out["can_undo"] is True, out
    assert out["use_soon"], out
    notes = [i for i in tools.get_attention_items() if i["kind"] == "use_soon"]
    assert [n["detail"]["dish"] for n in notes] == ["Salmon Bowls", "Turkey Chili"], notes

    back = tools.tonight_night_off_undo(day=MON)

    assert back["status"] == "restored", back
    left = [i for i in tools.get_attention_items() if i["kind"] == "use_soon"]
    assert [n["detail"]["dish"] for n in left] == ["Salmon Bowls"], left


def test_undo_leaves_the_same_note_when_an_earlier_answer_queued_it():
    """
    add_attention_item keeps one pending note per sentence. When the same
    "Use the Ground turkey soon — Turkey Chili came off the plan." is
    already pending from an earlier answer (the chili planned twice, its
    other night dropped first), this night off queues nothing new — so its
    Undo has nothing of its own to take back, and the earlier, still-true
    note stays.
    """
    plan_id, cook = _week(fill_every_night=True)
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET status = 'purchased' WHERE item = 'Ground turkey'")
    conn.commit()
    conn.close()
    earlier = tools.add_attention_item(
        "use_soon", "Use the Ground turkey soon — Turkey Chili came off the plan.",
        {"items": ["Ground turkey"], "dish": "Turkey Chili"},
    )["id"]

    out = tools.tonight_night_off(day=MON)
    assert out["kind"] == "drop" and out["can_undo"] is True, out
    notes = [i for i in tools.get_attention_items() if i["kind"] == "use_soon"]
    assert [n["id"] for n in notes] == [earlier], notes      # deduped onto it

    back = tools.tonight_night_off_undo(day=MON)

    assert back["status"] == "restored", back
    left = [i for i in tools.get_attention_items() if i["kind"] == "use_soon"]
    assert [n["id"] for n in left] == [earlier], left
