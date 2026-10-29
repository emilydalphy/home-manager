"""
Swap's three picks fit the night and don't repeat the week
(overnight hunt, 2026-10-29, branch overnight/hunt-swap-options-1029).

The picks are asked under the night's time cap and told the rest of the
week ("Don't repeat one"), but only a WHOLE-DISH swap held a pick to the
cap in code, and nothing held a pick to the rest of the week. A one-night
Swap on a 30-minute rush night offered a 90-minute braise — and tapping it
put it on the week, since a tapped card is the household's own pick and is
never refused for time. And Friday's Chili came back as an option for
Thursday. A prompt instruction is a request, not a guarantee; the gate
(swap_option_picks._gated) now holds both.
"""
from __future__ import annotations

import importlib

from app import tools

sop = importlib.import_module("app.tools.swap_option_picks")
from test_week1_carousel import DAY1, DAY2, WEEK_START, _asker, _entry_id, week  # noqa: F401


def _opt(name, minutes=25, protein="Cod fillets"):
    return {"meal_name": name, "reason": "Quick, and nothing to thaw.", "minutes": minutes,
            "ingredients": [protein, "Lemons", "Green beans"]}


def _names(out):
    return [o["meal"] for o in out["options"]]


def test_a_pick_longer_than_a_rush_nights_cap_is_not_offered(week):
    tools.save_week_intake(WEEK_START, night_tags={DAY1: ["rush"]})
    ask = _asker(_opt("Slow Beef Braise", 90, "Beef chuck"), _opt("Fish Tacos"),
                 _opt("Veg Curry", 20, "Chickpeas"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert ask.contexts[0]["max_minutes"] == 30
    assert _names(out) == ["Fish Tacos", "Veg Curry"]


def test_an_unrushed_night_still_offers_the_long_one(week):
    tools.save_week_intake(WEEK_START, night_tags={DAY1: ["unrushed"]})
    ask = _asker(_opt("Slow Beef Braise", 90, "Beef chuck"), _opt("Fish Tacos"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert _names(out) == ["Slow Beef Braise", "Fish Tacos"]


def test_a_dish_already_on_the_week_is_not_offered(week):
    # Chili is the other night's dinner (the fixture's DAY2).
    ask = _asker(_opt("chili", protein="Ground beef"), _opt("Fish Tacos"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert ask.contexts[0]["week_other_dishes"], "the model is told the rest of the week"
    assert _names(out) == ["Fish Tacos"]


def test_the_household_can_ask_for_a_dish_already_on_the_week(week):
    ask = _asker(_opt("Chili", protein="Ground beef"), _opt("Fish Tacos"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask, request="chili again please")
    assert _names(out) == ["Chili", "Fish Tacos"]


def test_the_dish_being_swapped_away_on_another_night_does_not_block_it(week):
    # Swapping the Chili night: Pork Chops (DAY1) is the rest of the week,
    # and Chili itself is never offered back (it's `avoid`).
    ask = _asker(_opt("Pork Chops", protein="Pork chops"), _opt("Fish Tacos"))
    out = tools.swap_options(week, _entry_id(week, DAY2), asker=ask)
    assert _names(out) == ["Fish Tacos"]
