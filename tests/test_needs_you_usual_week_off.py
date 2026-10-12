"""
"Tonight needs a dinner" is not asked on a night the usual week has off.

Bug, found 2026-10-09 by walking a brand-new email sign-up through
onboarding on a Friday. On "Who's eating, and when?" the household tapped
Friday's dinner to "Don't plan"; the reveal agreed ("15 meals · no weekday
lunch · no Friday dinner") and the first week, starting Saturday, left its
Friday empty on purpose. Then Today, that same Friday, led with an urgent

    DINNER
    Tonight needs a dinner          Pick

— the one night they had just said not to plan. get_needs_you_items asks
about any tonight/tomorrow with no dinner row, and a night no plan covers
has no row; the usual week was never read. (Inside a plan the same night
is a planned_empty row and was already quiet — only the days around a
plan were wrong: before the first week, and between weeks.)

A night the usual week has off, or dinner switched off for the whole week,
is handled now, the same as a planned_empty row: no card for it, and the
band goes on to ask about tomorrow when tomorrow is on.
"""
import datetime

from app import tools
from app.tools.usual_week import WEEKDAYS
from conftest import household_today


def _d(offset_days: int = 0) -> str:
    return (household_today() + datetime.timedelta(days=offset_days)).isoformat()


def _weekday(offset_days: int = 0) -> str:
    return WEEKDAYS[(household_today() + datetime.timedelta(days=offset_days)).weekday()]


def _a_recipe():
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}],
                     prep_time_minutes=10, cook_time_minutes=20)


def _dinner_cards() -> list[dict]:
    return [i for i in tools.get_needs_you_items() if i["type"] in ("dinner_decision", "dinner_open")]


def test_tonight_off_in_the_usual_week_is_not_asked_and_tomorrow_is():
    _a_recipe()
    tools.save_usual_week(grid={"dinner": {_weekday(0): "off"}})

    cards = _dinner_cards()

    assert [c["title"] for c in cards] == ["Tomorrow needs a dinner"]
    assert cards[0]["date"] == _d(1)


def test_dinner_switched_off_all_week_is_never_asked():
    # Edge: every night off. usual_week.off_slots_on leaves a meal that is
    # off EVERY day to the zero-count pass, so this is the other half.
    _a_recipe()
    tools.save_usual_week(grid={"dinner": {d: "off" for d in WEEKDAYS}})

    assert _dinner_cards() == []


def test_a_night_the_usual_week_has_on_is_still_asked():
    # Guard, green before the fix: only the DINNERS they turned off go
    # quiet — tonight's lunch off says nothing about tonight's dinner.
    _a_recipe()
    tools.save_usual_week(grid={"dinner": {_weekday(2): "off"}, "lunch": {_weekday(0): "off"}})

    assert [c["title"] for c in _dinner_cards()] == ["Tonight needs a dinner"]


def test_a_household_that_never_answered_the_usual_week_is_still_asked():
    # Guard, green before the fix: no usual week saved is no night off.
    _a_recipe()

    assert [c["title"] for c in _dinner_cards()] == ["Tonight needs a dinner"]
