"""
"Shop before tomorrow" is said only for a cook that IS tomorrow.

Found on an overnight hunt of Today's Now (2026-10-20). The shop move looks
36 hours ahead for a cook the list is waiting on, and from noon on that
window reaches past tomorrow into the day after. On a Tuesday evening with
nothing planned on Wednesday that needs buying and Thursday's breakfast
waiting on the list, Now's card read "Shop before tomorrow · 2 items · by
tomorrow" with "Go shopping" at half past eleven at night — for a meal with
the whole of Wednesday still in front of it. At half past nine that morning
the same day said nothing about the list at all.

The horizon now stops at the end of tomorrow: a cook the day after is not
today's shop, the same answer a cook three days out has always had.
"""
from __future__ import annotations

import datetime
from datetime import time, timedelta

from app import tools
from conftest import household_today


TODAY = household_today()


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(TODAY, time(hour, minute))


def _seed_breakfast_on(offset_days: int) -> None:
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    tools.add_recipe(
        "Blueberry Pancakes",
        ingredients=[{"item": "Blueberries", "qty": "1 pint", "category": "produce"}],
        prep_time_minutes=10,
        cook_time_minutes=15,
        default_servings=2,
    )
    plan_id = tools.create_weekly_plan(TODAY.isoformat())["weekly_plan_id"]
    day = (TODAY + timedelta(days=offset_days)).isoformat()
    tools.plan_meal(day, "Blueberry Pancakes", slot="breakfast", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)


def _shop(payload: dict) -> dict | None:
    hits = [m for m in payload["moves"] if m["kind"] == "shop"]
    return hits[0] if hits else None


def test_the_day_afters_breakfast_is_not_tomorrows_shop():
    """CATCH. Late evening, nothing tomorrow, the day after's breakfast
    waiting on the list: no "Shop before tomorrow", and it is not the card —
    the same answer the morning of the same day gave."""
    _seed_breakfast_on(2)

    assert _shop(tools.today_moves(now=_at(9, 30))) is None
    for hour, minute in ((20, 30), (23, 30)):
        payload = tools.today_moves(now=_at(hour, minute))
        assert _shop(payload) is None, f"{hour}:{minute:02d}: {_shop(payload)}"
        assert payload["featured"] is None


def test_tomorrows_breakfast_still_sends_you_shopping_tonight():
    """GUARD. A breakfast that really is tomorrow keeps its timed shop, and
    it is still the card late in the evening."""
    _seed_breakfast_on(1)

    payload = tools.today_moves(now=_at(23, 30))
    shop = _shop(payload)
    assert shop is not None
    assert shop["title"] == "Shop before tomorrow"
    assert shop["timed"] is True
    assert payload["featured"] == shop["id"]
