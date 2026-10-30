"""
A staple paused by three "Not this trip"s comes back once it is bought
(defect hunt, 2026-10-30). The auto-pause stands for "they may not buy this
any more"; buying it says they do. Before, coffee skipped three weeks
running and then typed and ticked stayed paused for good — never on the
list again. A pause a person chose stays theirs.
"""

from __future__ import annotations

import datetime as dt

from conftest import _pin

from app import tools

START = dt.date(2026, 11, 2)  # a Monday


def on(n: int):
    return _pin(dt.datetime.combine(START + dt.timedelta(days=n), dt.time(10, 0)))


def iso(n: int) -> str:
    return (START + dt.timedelta(days=n)).isoformat()


def staple(name: str) -> dict:
    return next(s for s in tools.list_staples() if s["item"] == name)


def lines(name: str) -> list[dict]:
    tools.get_grocery_list_by_section("needed")
    return [r for r in tools.list_grocery_list(status="needed") if r["item"].lower() == name.lower()]


def type_and_buy(name: str) -> int:
    item_id = tools.add_grocery_item(name)["item_id"]
    tools.mark_grocery_item(item_id, "purchased")
    return item_id


def skip_three_weeks(name: str) -> None:
    for n in (7, 14, 21):
        with on(n):
            tools.decide_staple_line(lines(name)[0]["id"], "skip")


def test_buying_a_staple_three_skips_paused_puts_it_back_on_its_rhythm():
    with on(0):
        tools.add_staple("Coffee", every_days=7)
    skip_three_weeks("Coffee")
    with on(21):
        assert staple("Coffee")["paused"] is True
    with on(25):
        type_and_buy("Coffee")
        s = staple("Coffee")
        # Was paused: True — and coffee never came back to the list.
        assert s["paused"] is False
        assert s["next_due_at"] == iso(32)
    with on(31):
        assert lines("Coffee") == []
    with on(32):
        assert len(lines("Coffee")) == 1


def test_an_untick_the_same_day_puts_the_auto_pause_back():
    with on(0):
        tools.add_staple("Coffee", every_days=7)
    skip_three_weeks("Coffee")
    with on(25):
        item_id = type_and_buy("Coffee")
        assert staple("Coffee")["paused"] is False
        tools.mark_grocery_item(item_id, "needed")
        assert staple("Coffee")["paused"] is True
        # And a real purchase after still lifts it.
        tools.mark_grocery_item(item_id, "purchased")
        assert staple("Coffee")["paused"] is False


def test_a_pause_a_person_chose_outlasts_a_purchase():
    with on(0):
        s = tools.add_staple("Coffee", every_days=7)
        tools.pause_staple(s["id"], paused=True)
    with on(5):
        type_and_buy("Coffee")
        assert staple("Coffee")["paused"] is True
