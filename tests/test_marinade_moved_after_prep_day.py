"""
Meat marinated in the fridge on a prep day that has gone by, then its meal
moved later (defect hunt, 2026-11-03; branch overnight/hunt-a-prep-1103).

Monday's list said "Marinate the chicken thighs — keeps in the fridge until
Tuesday". On Tuesday the skewers moved to Friday. Before: Monday's list
re-read itself off the new day ("Freeze it in the bag"), a Thursday "Move the
marinated chicken thighs to the fridge" was booked for chicken that had been
in the fridge since Monday, and the move said nothing. Now a gone-by prep day
is what happened: frozen only if its thaw was booked then, the move says when
the chicken needs cooking by, and nothing is booked.
"""
import datetime

from conftest import household_pin

from app import db, tools
from app.tools import meal_move, prep_list, prep_sessions

MON = "2026-11-02"


def _d(n: int) -> str:
    return (datetime.date.fromisoformat(MON) + datetime.timedelta(days=n)).isoformat()


def _on(frozen_today, day: str) -> None:
    frozen_today(household_pin(9, 0, on=datetime.date.fromisoformat(day)))


def _week(frozen_today):
    """Prep on Monday; skewers Tuesday (fridge), steak tacos Saturday (frozen
    in the bag). The list is read ON Monday, as the Cook tab does."""
    _on(frozen_today, MON)
    tools.add_member("Alex")
    tools.set_prep_days([{"weekday": "monday", "minutes": 120}])
    for name, meat, verb in (("Chicken Skewers", "Boneless skinless chicken thighs", "chicken"),
                             ("Steak Tacos", "Flank steak", "steak")):
        tools.add_recipe(name, ingredients=[{"item": meat, "qty": "1 lb", "category": "meat/seafood"}],
                         instructions=[f"Marinate the {verb} in lime and garlic.", "Sear in a hot skillet."],
                         default_servings=2, prep_time_minutes=15, cook_time_minutes=15)
    plan_id = tools.create_weekly_plan(MON, content_start_date=MON, day_count=7)["weekly_plan_id"]
    skewers = tools.plan_meal(_d(1), "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tacos = tools.plan_meal(_d(5), "Steak Tacos", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    prep_sessions.prep_sessions_for_plan(plan_id)
    return plan_id, skewers, tacos


def _thaws():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT meal_plan_entry_id, task_date FROM prep_tasks WHERE task_type = 'defrost' "
        "AND json_extract(detail_json, '$.kind') = ? ORDER BY id", (prep_list.MARINADE_THAW_KIND,),
    ).fetchall()
    conn.close()
    return [(r["meal_plan_entry_id"], r["task_date"]) for r in rows]


def _monday(plan_id):
    [session] = [s for s in prep_sessions.prep_sessions_for_plan(plan_id) if s["date"] == MON]
    return session


def test_fridge_marinade_moved_past_its_keep_is_said_and_never_given_a_thaw(frozen_today):
    plan_id, skewers, tacos = _week(frozen_today)
    assert _thaws() == [(tacos, _d(4))]

    _on(frozen_today, _d(1))
    out = meal_move.move_meal(plan_id, skewers, _d(4))

    assert out["status"] == "moved"
    assert "The chicken thighs were marinated yesterday, so they need cooking by tomorrow." in out["thaw_notes"]
    # No "move the marinated chicken to the fridge": it never left it.
    assert _thaws() == [(tacos, _d(4))]
    items = _monday(plan_id)["items"]
    assert not any(i["key"].startswith(f"freeze:{skewers}:") for i in items)
    [mar] = [i for i in items if i["key"].startswith(f"marinate:{skewers}:")]
    assert mar["keeps"] == "Keeps in the fridge until Wednesday"


def test_a_bag_frozen_on_the_prep_day_stays_frozen_when_its_meal_moves_closer(frozen_today):
    plan_id, skewers, tacos = _week(frozen_today)

    _on(frozen_today, _d(1))
    out = meal_move.move_meal(plan_id, tacos, _d(3))

    assert not any("marinated" in n and "cooking by" in n for n in out["thaw_notes"])
    items = _monday(plan_id)["items"]
    assert any(i["key"].startswith(f"freeze:{tacos}:") for i in items), "the bag is in the freezer"
    assert _thaws() == [(tacos, _d(2))]


def test_a_fridge_marinade_moved_within_its_two_days_says_nothing(frozen_today):
    plan_id, skewers, tacos = _week(frozen_today)

    _on(frozen_today, _d(1))
    out = meal_move.move_meal(plan_id, skewers, _d(2))

    assert out["status"] == "moved"
    assert out["thaw_notes"] == []
    assert _thaws() == [(tacos, _d(4))]
