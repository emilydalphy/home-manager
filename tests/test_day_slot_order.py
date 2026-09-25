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


def _a_legacy_row_on_an_unknown_slot(plan_id: int, day: str = DAY) -> None:
    """
    A row on a slot the app has never heard of, written the way one can only
    exist now: straight into the table. Since 2026-09-25 the three functions
    that write a meal refuse anything outside DAY_SLOTS, and rows already on
    disk were deliberately left alone rather than migrated — so this is not a
    contrivance, it is the shape of the thing the sort below has to cope with.
    """
    from app.db import get_conn
    from app.tools._shared import household_id

    conn = get_conn()
    conn.execute(
        "INSERT INTO meal_plan_entries "
        "(household_id, date, slot, freeform_meal, weekly_plan_id, slot_state) "
        "VALUES (?, ?, 'brunch', 'Eggs benedict', ?, 'planned')",
        (household_id(), day, plan_id),
    )
    conn.commit()
    conn.close()


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

    WHAT MOVED, 2026-09-25: this used to write the row with plan_meal. It
    cannot any more — plan_meal, plan_slot_open and plan_slot_empty all
    refuse a slot outside DAY_SLOTS now (weekly_plan.InvalidSlot), because a
    row on a slot no screen builds showed on Cook and on Today's moves and
    was invisible on Plan. The CLAIM here is unchanged and still worth
    having: rows like this exist on disk from before that guard, and were
    deliberately not migrated, so the sort still has to put them somewhere
    sensible rather than alphabetically in the middle of the day. It is
    written the only way such a row can now come about — straight into the
    table, which is exactly what a legacy row is.
    """
    plan_id = _plan()
    _scrambled_day(plan_id)
    _a_legacy_row_on_an_unknown_slot(plan_id)

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


def test_the_menu_view_puts_every_slot_of_a_scrambled_day_under_its_own_name():
    """
    Named for what it can actually catch. The day dict's KEY order comes
    from _build_day_based_menu's own dict literal, not from the query, so
    this one cannot fail on an alphabetical regression however the rows
    arrive — it is a no-clobber guard, not an ordering guard: each of the
    four meals lands under its own key and none of them overwrites another.
    The ordering assertions live in the tests above, which read the flat
    `meals` list, where the query's order is the only order there is.
    """
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
