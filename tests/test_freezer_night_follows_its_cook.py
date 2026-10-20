"""
A freezer night names its cook's day — "Leftovers from the freezer —
Monday's Turkey Chili" — and three of the ways a cook moves to another
night left that name on the OLD day.

Overnight, 2026-10-20 (found by the builder of
swap-keeps-the-freezer-cook-first, which made the nights swap re-say it).
Monday's chili cooks double for Wednesday and freezes a portion for
Friday. The Review "−" on Monday, marking nobody home on Monday, and
taking Monday's night off each move the cook onto Wednesday — the first
night it was feeding — through weekly_plan.move_cook_onto_fed_night, and
Friday kept saying "Monday's Turkey Chili" over a Monday that now cooks
nothing. The Move sheet and the swap re-say it; the shared move did not.

And the Undo half: the "−" and the night off put the week back from a
snapshot of the rows they touched, and Friday was not one of them — it
names the cook by entry id (from_freezer), not by the night it moved off.
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
WEDNESDAYS = "Leftovers from the freezer — Wednesday’s Turkey Chili"


def _week(fill_every_night: bool = False):
    """Monday's chili feeds Wednesday (leftovers) and Friday (four days on,
    so the chain repair makes it a freezer night). `fill_every_night`
    plans every other dinner, so a night off has nowhere free to move the
    cook to and cooks it on the fed night instead."""
    tools.add_member("Emily")
    tools.add_member("Jamie")
    tools.add_recipe("Turkey Chili", ingredients=[{"item": "Ground turkey", "qty": "1.5 lbs"}],
                     default_servings=2)
    tools.add_recipe("Tacos", ingredients=[{"item": "Tortillas", "qty": "1 pack"}], default_servings=2)
    tools.add_recipe("Stir Fry", ingredients=[{"item": "Broccoli", "qty": "1 lb"}], default_servings=2)
    plan_id = tools.create_weekly_plan(MON)["weekly_plan_id"]
    cook = tools.plan_meal(MON, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.plan_meal(WED, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{MON}:dinner"})
    tools.plan_meal(FRI, "Turkey Chili", slot="dinner", weekly_plan_id=plan_id,
                    derived_from={"links_to": f"{MON}:dinner"})
    if fill_every_night:
        for d, dish in ((TUE, "Tacos"), (THU, "Stir Fry"), (SAT, "Tacos"), (SUN, "Stir Fry")):
            tools.plan_meal(d, dish, slot="dinner", weekly_plan_id=plan_id)
    tools.repair_leftover_chains(plan_id)
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert _dinners(plan_id)[FRI] == MONDAYS
    return plan_id, cook


def _dinners(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.date, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? AND mpe.slot = 'dinner' "
        "AND mpe.slot_state = 'planned'",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {r["date"]: r["meal"] for r in rows}


def _cook_date(cook):
    conn = get_conn()
    row = conn.execute("SELECT date FROM meal_plan_entries WHERE id = ?", (cook,)).fetchone()
    conn.close()
    return row["date"]


def test_the_review_minus_renames_the_freezer_night_and_undo_puts_it_back():
    plan_id, cook = _week()

    out = tools.drop_dish_from_day(plan_id, cook)

    assert out["status"] == "dropped" and out["moved_to"] == WED, out
    assert _cook_date(cook) == WED
    assert _dinners(plan_id)[FRI] == WEDNESDAYS

    back = tools.drop_dish_undo(plan_id, out["undo_entry_id"])
    assert back["status"] == "restored", back
    assert _cook_date(cook) == MON
    assert _dinners(plan_id)[FRI] == MONDAYS


def test_nobody_home_on_the_cook_night_renames_the_freezer_night():
    plan_id, cook = _week()

    tools.set_slot_attendance(MON, "dinner", present_member_ids=[])

    assert _cook_date(cook) == WED
    assert _dinners(plan_id)[FRI] == WEDNESDAYS


def test_the_night_off_renames_the_freezer_night_and_undo_puts_it_back():
    plan_id, cook = _week(fill_every_night=True)

    out = tools.tonight_night_off(day=MON)

    assert out.get("moved_to") == WED, out
    assert _cook_date(cook) == WED
    assert _dinners(plan_id)[FRI] == WEDNESDAYS

    back = tools.tonight_night_off_undo(day=MON)
    assert back["status"] == "restored", back
    assert _cook_date(cook) == MON
    assert _dinners(plan_id)[FRI] == MONDAYS


def test_undo_puts_back_a_freezer_night_that_names_its_cook_only_by_id():
    """The repair's freezer night happens to keep `original_links_to`
    ("<Monday>:dinner"), which is how the Undo snapshot found it above. The
    freezer nights the lunch, variety and gap passes write name the cook by
    entry id alone — the snapshot has to find those by the cook's id, or
    Undo puts the chili back on Monday under a Friday still saying
    Wednesday's."""
    import json
    plan_id, cook = _week()
    conn = get_conn()
    row = conn.execute(
        "SELECT id, derived_from_json FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? "
        "AND slot = 'dinner'", (plan_id, FRI),
    ).fetchone()
    derived = json.loads(row["derived_from_json"])
    derived.pop("original_links_to", None)
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps(derived), row["id"]))
    conn.commit()
    conn.close()

    out = tools.drop_dish_from_day(plan_id, cook)
    assert _dinners(plan_id)[FRI] == WEDNESDAYS

    assert tools.drop_dish_undo(plan_id, out["undo_entry_id"])["status"] == "restored"
    assert _dinners(plan_id)[FRI] == MONDAYS
