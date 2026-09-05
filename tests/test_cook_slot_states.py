"""
The Cook view shows only slots there is something to cook.

A slot is one of three states (meal_plan_entries.slot_state). Two of them
have no meal behind them, and both were reaching the Cook view because
get_cooker_view never asked for the column:

- 'planned_empty' — nobody is home. schema.sql calls this "the only
  deliberately empty slot in a week" and says it "must NEVER be offered to
  the household as one".
- 'open' — a decision handed back, carrying open_reason. There is nothing
  to cook; it belongs to the Plan screen.

The symptom was worse than a long list. A planned_empty entry has neither
recipe_id nor freeform_meal, so it rendered as a row with a checkbox and an
empty name — and if it fell on today's dinner it became the Cook hero,
headlined "Dinner", captioned with the reason nobody is eating, offering to
start cooking it.

The frontend needs no guard of its own: it renders whatever `meals`
contains, so filtering here fixes the list, the hero and the "N of M
cooked" counter together.
"""
import datetime

from app import tools


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _week_start() -> str:
    return _monday().isoformat()


def _day(offset_from_monday: int) -> str:
    """
    A date inside this week's plan period, counted from Monday.

    Deliberately not "today plus N": create_weekly_plan gives the plan a
    seven-day Monday-to-Sunday period, and plan_meal rejects a date outside
    it. Anchoring on today meant `today + 1` fell outside the period every
    Sunday -- which is exactly the day the Plan-the-Week flow is used, so
    the suite would have gone red at the worst possible moment. Caught by
    review, not by a run.
    """
    return (_monday() + datetime.timedelta(days=offset_from_monday)).isoformat()


def _today() -> str:
    return datetime.date.today().isoformat()


def _plan_with_one_real_dinner():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_today(), "Soup", slot="dinner", weekly_plan_id=plan_id)
    return plan_id


def test_a_nobody_home_slot_is_not_offered_as_something_to_cook():
    plan_id = _plan_with_one_real_dinner()
    tools.plan_slot_empty(
        plan_id, _day(4), "dinner", "You're out — I've planned nothing and bought nothing.",
    )

    view = tools.get_cooker_view(plan_id)

    assert [m["meal"] for m in view["meals"]] == ["Soup"]


def test_an_open_slot_is_not_offered_as_something_to_cook():
    plan_id = _plan_with_one_real_dinner()
    tools.plan_slot_open(
        plan_id, _day(4), "dinner", "You said you'd decide this one nearer the time.",
    )

    view = tools.get_cooker_view(plan_id)

    assert [m["meal"] for m in view["meals"]] == ["Soup"]


def test_tonights_empty_dinner_leaves_nothing_for_the_hero_to_pick():
    """
    The worst shape of this bug. The Cook hero picks the first uncooked
    meal for TODAY, so a nobody-home dinner tonight became a headline
    reading "Dinner", captioned with the reason nobody is eating, offering
    to start cooking it. With nothing returned for today there is nothing
    to promote, and the hero falls to its existing "nothing to cook
    tonight" branch.
    """
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.plan_slot_empty(
        plan_id, _today(), "dinner", "You're out — I've planned nothing and bought nothing.",
    )

    view = tools.get_cooker_view(plan_id)

    assert [m for m in view["meals"] if m["date"] == _today()] == []


def test_empty_and_open_slots_are_not_counted_in_the_cooked_progress():
    """
    "2 of 5 cooked" must count meals, not nights nobody is home -- a total
    nobody can ever reach reads as being permanently behind.
    """
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_day(0), "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_meal(_day(1), "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_slot_empty(plan_id, _day(3), "dinner", "Nobody home.")
    tools.plan_slot_open(plan_id, _day(6), "dinner", "Deciding nearer the time.")

    view = tools.get_cooker_view(plan_id)

    assert view["meals_total"] == 2
    assert view["meals_done"] == 0


def test_plan_progress_applies_the_same_rule():
    """
    get_plan_progress has no screen, but it is the chat tool the system
    prompt names for "what's left to cook this week". Unfiltered it gave
    the assistant a total nobody could reach, plus nameless entries
    carrying real entry_ids it could hand to check_off_meal. It has to
    agree with get_cooker_view about the same plan.
    """
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Soup", ingredients=[{"item": "stock", "qty": "1 L"}])
    tools.plan_meal(_day(0), "Soup", slot="dinner", weekly_plan_id=plan_id)
    tools.plan_slot_empty(plan_id, _day(3), "dinner", "Nobody home.")
    tools.plan_slot_open(plan_id, _day(6), "dinner", "Deciding nearer the time.")

    progress = tools.get_plan_progress(plan_id)

    assert progress["meals_total"] == 1
    assert [m["meal"] for m in progress["meals"]] == ["Soup"]
    # No nameless rows, so nothing to offer or check off by mistake.
    assert all(m["meal"] for m in progress["meals"])
    assert progress["meals_total"] == tools.get_cooker_view(plan_id)["meals_total"]


def test_a_real_meal_still_carries_its_slot_state():
    """
    Forwarded so a reader of one meal can see its state rather than infer
    it. Note this cannot describe the skipped nights -- everything that
    survives the filter is cookable by construction.
    """
    plan_id = _plan_with_one_real_dinner()

    meal = tools.get_cooker_view(plan_id)["meals"][0]

    assert meal["slot_state"] == "planned"


def test_a_plan_of_only_empty_slots_is_empty_rather_than_full_of_blanks():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    for offset in range(3):
        tools.plan_slot_empty(plan_id, _day(offset), "dinner", "Away all week.")

    view = tools.get_cooker_view(plan_id)

    assert view["meals"] == []
    assert view["meals_total"] == 0
