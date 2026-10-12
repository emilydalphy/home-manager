"""
Plan offers no "Done" on a day still ahead (Loop Board bug, found
2026-10-09 driving an approved week: Saturday — tomorrow on the household's
clock — had a Done button on its dinner).

A tick is a record of something that happened. Today and the days already
gone keep it (wkMealRowHtml's own comment argues for the past); a day still
ahead has nothing to record yet — the same rule leftoversEatenNow already
applies to a reheat night's "Mark eaten". "Ahead" is read off the day's
isToday / isPast flags, which come from the HOUSEHOLD's clock
(get_week_menu's is_today / is_past), never the phone's.

The one exception: a row on a day ahead that is ALREADY ticked (chat can
tick one — check_off_meal has no date guard) keeps its button, pressed, so
the plan still has a way to put it back.
"""
from __future__ import annotations

import json

from test_plan_cards_2026_09_18 import (
    _MON, _TUE, _WED, _day, _entry, _needs_node, _prelude, _run,
)


def _cards(*days):
    js = (_prelude() + f"weekState.days = {json.dumps(list(days))};\n"
          "console.log(JSON.stringify(weekState.days.map(function (d, i) {"
          " return wkDayCardHtml(d, i, { done: true, swapLabel: 'Swap' }); })));")
    return _run(js)


@_needs_node
def test_a_day_ahead_has_no_done_and_today_and_a_past_day_keep_it():
    past = _day(_MON, past=True, dinner=_entry("Lemon chicken & orzo", entry_id=13))
    today = _day(_TUE, iso_today=True, dinner=_entry("Black bean tacos", entry_id=23))
    ahead = _day(_WED, dinner=_entry("Chicken skewers", entry_id=33),
                 lunch=_entry("Leftover skewers", entry_id=32, meta="reheat", source="leftovers"))
    past_html, today_html, ahead_html = _cards(past, today, ahead)
    assert 'data-wk-done="dinner"' in past_html
    assert 'data-wk-done="dinner"' in today_html
    assert "data-wk-done" not in ahead_html and ">Done</button>" not in ahead_html
    # The rest of the row on a day ahead is untouched: Swap is still how
    # you change tomorrow's dinner.
    assert 'data-wk-swap-sheet="dinner"' in ahead_html
    assert 'data-wk-swap-sheet="lunch"' in ahead_html


@_needs_node
def test_a_day_ahead_already_ticked_keeps_its_pressed_button_to_put_it_back():
    ahead = _day(_WED, dinner=_entry("Chicken skewers", entry_id=33, cooked=True))
    (html,) = _cards(ahead)
    assert 'data-wk-done="dinner" aria-pressed="true"' in html
    assert 'aria-label="Not cooked yet — Chicken skewers"' in html
