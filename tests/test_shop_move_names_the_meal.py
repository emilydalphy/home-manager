"""
The shop names the meal it is actually for.

FOUND 2026-09-18 overnight, by driving the app on a throwaway database.
Since 2026-09-16 the shop move's deadline has been the start time of the
SOONEST cook the list is genuinely waiting on — honest, and very often
breakfast or lunch rather than dinner. The title did not move with it: it
said "Shop for tonight" for any waiting cook today. So a household with
one breakfast and one dinner and nothing shopped was texted, at seven in
the morning:

    Shop for tonight — 4 items, by 7:55.

"Tonight" and a 7:55 deadline in one sentence, fifty-five minutes apart.
The morning text goes out at 07:00 by default, which is exactly the hour
today's dinner is the LAST cook still ahead rather than the first — so the
surface built to reach the household outside the app is the surface where
this is most likely, not least. `_shop_move`'s own docstring already says
an invented deadline at seven in the morning is how a morning check-in
stops being believed; the deadline was fixed and the word was not.

Each test says whether it is a CATCH (red against `app/` on 277d854) or a
no-regression GUARD, and a guard names the mutation that pins it.
"""
from __future__ import annotations

import datetime
from datetime import time

from app import tools
from app.tools import cooker as _cooker
from app.tools import digest as _digest
from app.tools import moves as _moves
from conftest import household_today


TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
# Two days back, so today is never the plan's first day — matching the
# sibling file this one extends.
WEEK_START = (TODAY - datetime.timedelta(days=2)).isoformat()


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(TODAY, time(hour, minute))


def _household():
    for n in ("Emily", "Vineeth"):
        tools.add_member(n)
    tools.set_dinner_window("6_8")


def _recipe(name, item, prep=10, cook=15):
    tools.add_recipe(
        name,
        ingredients=[{"item": item, "qty": "1 lb", "category": "meat-seafood"}],
        prep_time_minutes=prep, cook_time_minutes=cook, default_servings=2,
    )


def _shop(now):
    hits = [m for m in tools.today_moves(now=now)["moves"] if m["kind"] == "shop"]
    return hits[0] if hits else None


def _week_with(slots):
    """One plan, one dish per named slot, every ingredient still to buy."""
    _household()
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for slot, (dish, item) in slots.items():
        _recipe(dish, item)
        tools.plan_meal(ISO_TODAY, dish, slot=slot, weekly_plan_id=plan_id,
                        add_ingredients_to_grocery_list=True)
    return plan_id


# ---------- the reported line ----------

def test_at_seven_in_the_morning_the_shop_is_named_after_breakfast():
    """
    CATCH. The measured case: on 277d854 this reads "Shop for tonight"
    over a deadline fifty-five minutes away, in the morning.
    """
    _week_with({"breakfast": ("Overnight Oats", "Rolled oats"),
                "dinner": ("Bean Chili", "Black beans")})
    shop = _shop(_at(7))
    assert shop["title"] == "Shop before breakfast"
    assert shop["detail"].endswith("by 7:35"), shop["detail"]


def test_the_morning_text_says_it_too():
    """
    CATCH, and the half that matters most — this is the channel built to
    reach the household OUT of the app, and it goes out at 07:00.
    """
    _week_with({"breakfast": ("Overnight Oats", "Rolled oats"),
                "dinner": ("Bean Chili", "Black beans")})
    text = _digest.build_morning_text(now_local=_at(7))
    assert "Shop before breakfast" in text
    assert "Shop for tonight" not in text, (
        "the text must not say tonight over a deadline before eight in the morning"
    )


def test_once_breakfast_is_behind_you_it_is_tonight_again():
    """
    GUARD, green either way — main says "Shop for tonight" here too, and
    for once it is right. It is here because the word has to FOLLOW the
    deadline rather than merely differ from it: same household, four hours
    later, when the soonest cook the list is waiting on really is
    tonight's. Pinned by mutation: make `_shop_title` answer anything but
    "Shop for tonight" for dinner and this fails.
    """
    _week_with({"breakfast": ("Overnight Oats", "Rolled oats"),
                "dinner": ("Bean Chili", "Black beans")})
    shop = _shop(_at(11))
    assert shop["title"] == "Shop for tonight"
    assert shop["detail"].endswith("by 6:35"), shop["detail"]


def test_a_lunch_is_named_as_lunch():
    """CATCH. The third slot a deadline can belong to."""
    _week_with({"lunch": ("Turkey Sandwiches", "Sliced turkey"),
                "dinner": ("Bean Chili", "Black beans")})
    assert _shop(_at(9))["title"] == "Shop before lunch"


def test_a_snack_says_today_rather_than_naming_a_half_of_the_day():
    """
    CATCH. There is no "before" a person would recognise for a snack, and
    naming the wrong half of the day is the thing being fixed — so the
    honest answer is the smaller one.
    """
    _week_with({"snack": ("Apple Slices", "Apples"),
                "dinner": ("Bean Chili", "Black beans")})
    assert _shop(_at(9))["title"] == "Shop for today"


# ---------- nothing else moved ----------

def test_a_dinner_only_household_reads_exactly_as_before():
    """
    GUARD, green either way — the overwhelmingly common shape, and the
    string four other test files pin. Pinned by mutation: make
    `_shop_title` answer anything but "Shop for tonight" for dinner and
    this fails.
    """
    _week_with({"dinner": ("Bean Chili", "Black beans")})
    assert _shop(_at(7))["title"] == "Shop for tonight"
    assert _shop(_at(15))["title"] == "Shop for tonight"


def test_tomorrows_cook_still_says_before_tomorrow():
    """
    GUARD, green either way. The not-today branch is untouched and must
    stay untouched: naming tomorrow's MEAL would claim a precision the
    "by tomorrow" wording deliberately does not. Pinned by mutation: make
    the title call `_shop_title` on the else branch too and this fails.
    """
    _household()
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    _recipe("Bean Chili", "Black beans")
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    tools.plan_meal(tomorrow, "Bean Chili", slot="breakfast", weekly_plan_id=plan_id,
                    add_ingredients_to_grocery_list=True)
    shop = _shop(_at(20))
    assert shop is not None and shop["title"] == "Shop before tomorrow"


def test_a_list_with_no_cook_waiting_on_it_still_has_no_deadline_and_no_meal_word():
    """
    GUARD, green either way — the 2026-09-16 rule this builds on, which
    must not be loosened by giving the untimed line a meal name it cannot
    stand behind. Pinned by mutation: drop the `soonest is None` branch
    and this fails.
    """
    _week_with({"dinner": ("Bean Chili", "Black beans")})
    for row in tools.list_grocery_list(status="needed"):
        tools.mark_grocery_item(row["id"], "purchased")
    tools.add_grocery_item("Dish soap", quantity="1", category="household")
    shop = _shop(_at(9))
    assert shop is not None
    assert shop["timed"] is False
    assert "Shop" not in shop["title"], shop["title"]
    assert "by" not in shop["detail"]


def test_the_title_reads_the_soonest_waiting_cooks_slot_and_not_the_first_meal_of_the_day():
    """
    CATCH in substance, and the one that stops an easier wrong fix: the
    word must come from the meal the DEADLINE belongs to, not from
    whatever the day's earliest slot happens to be. Here breakfast is
    already bought, so the list is waiting on lunch alone — and the title
    has to say lunch even though breakfast is still ahead.
    """
    _week_with({"breakfast": ("Overnight Oats", "Rolled oats"),
                "lunch": ("Turkey Sandwiches", "Sliced turkey")})
    for row in tools.list_grocery_list(status="needed"):
        if row["item"].lower().startswith("rolled oats"):
            tools.mark_grocery_item(row["id"], "purchased")
    assert _shop(_at(6))["title"] == "Shop before lunch"
