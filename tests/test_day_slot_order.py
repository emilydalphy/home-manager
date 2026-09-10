"""
A day reads in the order it is eaten: breakfast, lunch, dinner, snack.

`meal_plan_entries.slot` is a TEXT column, so the `ORDER BY mpe.slot` that
get_weekly_plan used to end with was an ALPHABETICAL sort — breakfast,
dinner, lunch, snack. Dinner printed before lunch on every day the app
showed, everywhere that function feeds (Loop Board "A day's meals list in
alphabetical order, so dinner prints before lunch").

The order was written down in weekly_plan.DAY_SLOTS the whole time; the
query just never asked for it. These tests insert a day's meals in a
deliberately SCRAMBLED order so an alphabetical regression fails here
rather than passing quietly.
"""
from app import tools
from app.tools import plan_quality as _plan_quality
from app.tools import weekly_plan as _weekly_plan


WEEK = "2026-09-07"  # a Monday
DAY = "2026-09-08"


def _plan() -> int:
    return tools.create_weekly_plan(WEEK, content_start_date=WEEK, day_count=7)["weekly_plan_id"]


def _slots_on(plan_id: int, day: str = DAY) -> list[str]:
    return [m["slot"] for m in tools.get_weekly_plan(plan_id)["meals"] if m["date"] == day]


def _meals_on(plan_id: int, day: str = DAY) -> list[str]:
    return [m["meal"] for m in tools.get_weekly_plan(plan_id)["meals"] if m["date"] == day]


def _scrambled_day(plan_id: int, day: str = DAY) -> None:
    """One full day, written in an order no day is ever eaten in."""
    tools.plan_meal(day, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(day, "Apple slices", slot="snack", weekly_plan_id=plan_id)
    tools.plan_meal(day, "Porridge", slot="breakfast", weekly_plan_id=plan_id)
    tools.plan_meal(day, "Soup", slot="lunch", weekly_plan_id=plan_id)


def test_a_day_lists_its_meals_in_the_order_they_are_eaten():
    """The bug itself: dinner used to print before lunch, every day."""
    plan_id = _plan()
    _scrambled_day(plan_id)

    assert _slots_on(plan_id) == ["breakfast", "lunch", "dinner", "snack"]
    assert _meals_on(plan_id) == ["Porridge", "Soup", "Chili", "Apple slices"]


def test_the_order_is_day_slots_and_not_a_second_hard_coded_list():
    """
    If the app ever gains a slot, one edit — to DAY_SLOTS — has to change
    both the list of slots a day can hold and the order they come back in.
    """
    plan_id = _plan()
    _scrambled_day(plan_id)

    assert _slots_on(plan_id) == list(tools.DAY_SLOTS)

    fragment = _weekly_plan.slot_order_sql("mpe.slot")
    for position, slot in enumerate(_weekly_plan.DAY_SLOTS):
        assert f"WHEN '{slot}' THEN {position}" in fragment
    assert f"ELSE {len(_weekly_plan.DAY_SLOTS)}" in fragment


def test_days_still_come_back_oldest_first():
    """Sorting within a day must not disturb the sort between days."""
    plan_id = _plan()
    tools.plan_meal("2026-09-09", "Tacos", slot="dinner", weekly_plan_id=plan_id)
    _scrambled_day(plan_id)
    tools.plan_meal("2026-09-07", "Pasta", slot="dinner", weekly_plan_id=plan_id)

    dates = [m["date"] for m in tools.get_weekly_plan(plan_id)["meals"]]
    assert dates == sorted(dates)


def test_a_slot_the_app_does_not_know_sorts_last_rather_than_disappearing():
    """
    An unexpected slot is still somebody's food. It goes to the end of the
    day — alphabetically 'brunch' would have landed between breakfast and
    dinner, which is a confident wrong answer.
    """
    plan_id = _plan()
    _scrambled_day(plan_id)
    tools.plan_meal(DAY, "Eggs benedict", slot="brunch", weekly_plan_id=plan_id)

    assert _slots_on(plan_id) == ["breakfast", "lunch", "dinner", "snack", "brunch"]
    assert "Eggs benedict" in _meals_on(plan_id)

    # ...and coming last must not cost the day its real dinner in the
    # day-by-day view, which folds an unknown slot into dinner.
    day = next(d for d in tools.get_weekly_plan(plan_id)["menu"] if d["date"] == DAY)
    assert day["dinner"] == "Chili"


def test_both_of_a_day_s_snacks_come_back_in_a_stable_order():
    """
    A day can hold more than one snack (see resolve_snacks_per_day). They
    tie on date AND on slot, so without the id as the last word SQLite is
    free to hand them back either way round — and `snack`, the single key
    the menu carries, is whichever came first.
    """
    plan_id = _plan()
    _scrambled_day(plan_id)
    tools.plan_meal(DAY, "Cheese and crackers", slot="snack", weekly_plan_id=plan_id)

    assert _slots_on(plan_id) == ["breakfast", "lunch", "dinner", "snack", "snack"]
    assert _meals_on(plan_id)[-2:] == ["Apple slices", "Cheese and crackers"]

    again = _meals_on(plan_id)
    assert again == _meals_on(plan_id), "the same week must read the same way twice"

    day = next(d for d in tools.get_weekly_plan(plan_id)["menu"] if d["date"] == DAY)
    assert day["snacks"] == ["Apple slices", "Cheese and crackers"]
    assert day["snack"] == "Apple slices"


def test_the_menu_view_carries_a_day_s_slots_in_eating_order():
    """The day-by-day view is built from the same rows, and reads the same."""
    plan_id = _plan()
    _scrambled_day(plan_id)

    day = next(d for d in tools.get_weekly_plan(plan_id)["menu"] if d["date"] == DAY)
    assert [s for s in tools.DAY_SLOTS if day.get(s)] == list(tools.DAY_SLOTS)
    assert [day[s] for s in tools.DAY_SLOTS] == ["Porridge", "Soup", "Chili", "Apple slices"]


def test_the_quality_pass_reads_a_day_in_eating_order_too():
    """
    plan_quality's own loader had the identical alphabetical ORDER BY, and
    snack_clashes reads that order straight through — it names the first
    thing a snack repeats, and the snack repair then acts on that record.
    """
    plan_id = _plan()
    _scrambled_day(plan_id)

    entries = _plan_quality._load_plan_entries(plan_id)
    assert [e["slot"] for e in entries if e["date"] == DAY] == list(tools.DAY_SLOTS)
