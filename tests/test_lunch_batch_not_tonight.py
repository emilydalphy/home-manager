"""
Today called a lunch batch "covers tonight" (defect hunt, 2026-10-07).

Two fresh onboardings, Today read: "Turkey Wraps · Start by noon · Cooking
for 4 — covers tonight and leftovers on Friday." leftovers.covers_note
hard-coded "tonight" for any cook dated today, whatever its meal. Now only
a dinner says "tonight"; a breakfast or lunch cook today is named by its
meal ("covers lunch today ..."). Any other day is still the weekday.

The saved make_double_note ("I'll set aside a double batch tonight — ...")
had the same word baked in, and it does reach a screen: it is the Cook
card's and Today's line when the batch can't be counted
(cooker._apply_leftover_chains' fallback). It takes the cook's slot now.

The morning text and the evening nudge never read either sentence: the
text's non-dinner cooks are "Lunch: Turkey Wraps." and the nudge is
dinner-only — so nothing there to change.
"""
from __future__ import annotations

import datetime
import json

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import leftovers as lo
from app.tools import moves as mv
from app.tools import weekly_plan as wp

TODAY = household_today()


def _day(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


D0, D2 = _day(0), _day(2)


def _src(slot, date=D0):
    return {"date": date, "slot": slot, "targets": [{"date": D2, "slot": slot}]}


# ---------------------------------------------------------- covers_note

def test_a_lunch_batch_cooked_today_says_lunch_today_not_tonight():
    said = lo.covers_note(_src("lunch"), 4, today=D0)
    assert said == f"Cooking for 4 — covers lunch today and leftovers on {lo._weekday(D2)}."
    assert "tonight" not in said


def test_a_breakfast_batch_cooked_today_says_breakfast_today():
    said = lo.covers_note(_src("breakfast"), 4, today=D0)
    assert said.startswith("Cooking for 4 — covers breakfast today and leftovers on ")


def test_a_dinner_batch_cooked_today_still_says_tonight():
    said = lo.covers_note(_src("dinner"), 6, today=D0)
    assert said == f"Cooking for 6 — covers tonight and leftovers on {lo._weekday(D2)}."


def test_a_lunch_batch_on_another_day_is_still_named_by_its_weekday():
    other = _day(1)
    said = lo.covers_note(_src("lunch", date=other), 4, today=D0)
    assert said.startswith(f"Cooking for 4 — covers {lo._weekday(other)} and leftovers")


def test_a_freezer_only_lunch_cook_says_lunch_today():
    shape = {"date": D0, "slot": "lunch", "targets": [], "freezer_servings": 2}
    assert lo.covers_note(shape, 6, today=D0) == (
        "Cooking for 6 — covers lunch today, plus 2 for the freezer.")


# ---------------------------------------------------- make_double_note

def test_the_saved_note_names_the_cooks_meal():
    t = [f"{D2}:lunch"]
    day = datetime.date.fromisoformat(D2).strftime("%A")
    assert wp._make_double_note_text(t, "lunch") == (
        f"I’ll set aside a double batch at lunch — {day} eats the leftovers.")
    assert wp._make_double_note_text(t, "dinner") == (
        f"I’ll set aside a double batch tonight — {day} eats the leftovers.")
    # A caller that knows no slot keeps the dinner wording it always had.
    assert wp._make_double_note_text(t) == wp._make_double_note_text(t, "dinner")


# ------------------------------------------------- Today, end to end

def _lunch_chain_week():
    """A household of four whose lunch today cooks double for D2's lunch,
    confirmed by the real repair_leftover_chains (which writes the note)."""
    for name in ("Emily", "Vineeth", "Reid", "Ana"):
        tools.add_member(name)
    plan = tools.create_weekly_plan(D0, day_count=7)["weekly_plan_id"]
    tools.add_recipe(
        "Turkey Wraps", [{"item": "Turkey", "qty": "1 lb", "category": "meat"}],
        instructions=["Warm the wraps.", "Fill and roll."],
        prep_time_minutes=10, cook_time_minutes=15, default_servings=4)
    for d in (D0, D2):
        tools.plan_meal(d, "Turkey Wraps", "lunch", weekly_plan_id=plan,
                        add_ingredients_to_grocery_list=False)
    conn = get_conn()
    rows = {(r["date"], r["slot"]): r["id"] for r in conn.execute(
        "SELECT id, date, slot FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan,))}
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"links_to": f"{D0}:lunch"}), rows[(D2, "lunch")]))
    conn.commit()
    conn.close()
    wp.repair_leftover_chains(plan)
    return plan, rows


def test_todays_lunch_cook_move_does_not_say_tonight():
    plan, rows = _lunch_chain_week()
    conn = get_conn()
    derived = json.loads(conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ?",
        (rows[(D0, "lunch")],)).fetchone()["derived_from_json"])
    conn.close()
    assert derived.get("make_double_note", "").startswith("I’ll set aside a double batch at lunch")

    cook = next(m for m in mv.moves_for_day(D0) if m["id"] == f"cook:{rows[(D0, 'lunch')]}")
    assert cook["reason"].startswith("Cooking for 8 — covers lunch today and leftovers on ")
    assert "tonight" not in cook["reason"]
