"""
A started cook stays tonight's until it is ticked done or 4am household
time, whichever comes first (Emily, 2026-10-31, Loop Board "a dinner still
cooking at midnight vanishes from Cook, and Today already calls it
'leftovers from Friday'").

Chili started at 11:47pm with leftovers planned for the next night, not
ticked. At 12:01am Today jumped to the next day ("leftovers from Friday ·
reheat") and the morning card already asked "Chili. Did you have it?".
One rule now (cooker.kitchen_day), read by Today and the morning card.
"""
import datetime

from app import tools
from app.tools import cooker
from conftest import household_pin, household_today

D1 = household_today() + datetime.timedelta(days=14)   # the cooking night
D2 = D1 + datetime.timedelta(days=1)                    # the leftovers night


def _week():
    tools.add_member("Emily")
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=40, default_servings=1)
    plan = tools.create_weekly_plan(D1.isoformat(), day_count=2)["weekly_plan_id"]
    chili = tools.plan_meal(D1.isoformat(), "Chili", slot="dinner", weekly_plan_id=plan)["entry_id"]
    tools.plan_meal(D2.isoformat(), "Chili", slot="dinner", weekly_plan_id=plan,
                    derived_from={"links_to": f"{D1.isoformat()}:dinner"})
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan, approved_by="Emily", confirm_hard_conflicts=True)
    return chili


def _started_late(frozen_today, chili):
    frozen_today(household_pin(23, 47, on=D1))
    tools.start_cooking(chili)


def test_after_midnight_a_started_cook_keeps_today_on_its_day(frozen_today):
    chili = _week()
    _started_late(frozen_today, chili)
    frozen_today(household_pin(0, 1, on=D2))

    assert cooker.kitchen_day() == D1
    assert tools.today_moves()["date"] == D1.isoformat()
    # The morning card asks about the day before the cook, not the chili.
    check = tools.yesterday_check()
    assert check["date"] == (D1 - datetime.timedelta(days=1)).isoformat()
    assert chili not in [m["entry_id"] for m in check["meals"]]


def test_four_am_turns_the_day_over(frozen_today):
    chili = _week()
    _started_late(frozen_today, chili)
    frozen_today(household_pin(4, 0, on=D2))

    assert tools.today_moves()["date"] == D2.isoformat()
    assert chili in [m["entry_id"] for m in tools.yesterday_check()["meals"]]


def test_ticking_it_done_turns_the_day_over(frozen_today):
    chili = _week()
    _started_late(frozen_today, chili)
    frozen_today(household_pin(0, 30, on=D2))
    tools.check_off_meal(chili, "done")

    assert tools.today_moves()["date"] == D2.isoformat()


def test_nothing_started_turns_over_at_midnight(frozen_today):
    _week()
    frozen_today(household_pin(0, 1, on=D2))
    assert cooker.kitchen_day() == D2
    assert tools.today_moves()["date"] == D2.isoformat()
