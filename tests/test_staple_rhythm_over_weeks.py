"""
Staples over several weeks of real shopping (defect hunt, 2026-10-25).

Each test walks every clock (Python's, SQLite's) forward one shopping day at
a time with conftest's _pin, buys things the way a household does — ticking
the line Pomona put on the list, or typing one when it wasn't there — and
checks what the staple has learned.

Two things the walk found:
- a cadence the household TOLD Pomona lasted only until the next purchase,
  when the history from before they said it was learned straight back;
- one top-up two days after a weekly shop, at the moment the rhythm is first
  learned, halved it ("about every 4 days" for weekly milk).
"""

from __future__ import annotations

import datetime as dt

from conftest import _pin

from app import tools
from app.tools import staples as st

START = dt.date(2026, 11, 2)  # a Monday


def on(n: int):
    """Every clock at 10:00 on day n of the walk."""
    return _pin(dt.datetime.combine(START + dt.timedelta(days=n), dt.time(10, 0)))


def iso(n: int) -> str:
    return (START + dt.timedelta(days=n)).isoformat()


def buy(name: str) -> None:
    """Tick the list's line for it (reading the list first, which is what
    puts a due staple there), or type one and tick it."""
    tools.get_grocery_list_by_section("needed")
    for row in tools.list_grocery_list(status="needed"):
        if row["item"].lower() == name.lower():
            tools.mark_grocery_item(row["id"], "purchased")
            return
    tools.mark_grocery_item(tools.add_grocery_item(name)["item_id"], "purchased")


def staple(name: str) -> dict:
    return next(s for s in tools.list_staples() if s["item"] == name)


def on_list(name: str) -> int:
    tools.get_grocery_list_by_section("needed")
    return sum(1 for r in tools.list_grocery_list(status="needed") if r["item"].lower() == name.lower())


# ---------- a told rhythm ----------


def test_a_told_rhythm_outlives_the_next_purchase():
    with on(0):
        tools.add_staple("Paper towels", category="household")
    for n in (30, 60, 90):
        with on(n):
            buy("Paper towels")
    with on(91):
        assert (staple("Paper towels")["cadence_source"], staple("Paper towels")["cadence_days"]) == ("learned", 30)
        tools.add_staple("Paper towels", every_days=60)  # "we only need them every two months"
    with on(150):
        buy("Paper towels")
        s = staple("Paper towels")
        # Was ("learned", 30): the three monthly gaps from before they said
        # it outvoted them on the first purchase after.
        assert (s["cadence_source"], s["cadence_days"]) == ("told", 60)
        assert s["next_due_at"] == iso(210)
    with on(209):
        assert on_list("Paper towels") == 0
    with on(210):
        assert on_list("Paper towels") == 1
        buy("Paper towels")
        # Two gaps since they said it, both two months: now it is theirs.
        s = staple("Paper towels")
        assert (s["cadence_source"], s["cadence_days"]) == ("learned", 60)


def test_a_year_of_old_gaps_does_not_pull_a_told_rhythm_back_once_learned():
    """Review round. Twelve monthly buys, then "every two months": once two
    gaps since make it learned at 60, the next buys must not relearn from
    the whole year and fall back to 30."""
    with on(0):
        tools.add_staple("Paper towels", category="household")
    for n in range(30, 361, 30):
        with on(n):
            buy("Paper towels")
    with on(361):
        assert staple("Paper towels")["cadence_days"] == 30
        tools.add_staple("Paper towels", every_days=60)
    seen = []
    for n in (420, 480, 540, 600):
        with on(n):
            buy("Paper towels")
            s = staple("Paper towels")
            seen.append((s["cadence_source"], s["cadence_days"]))
    assert seen == [("told", 60), ("learned", 60), ("learned", 60), ("learned", 60)]
    with on(600):
        assert staple("Paper towels")["next_due_at"] == iso(660)


def test_purchases_after_the_telling_still_overrule_it():
    """Told every two months, still bought monthly: the shopping wins once it
    has shown two gaps since — the told number is not a lock."""
    with on(0):
        tools.add_staple("Paper towels", category="household")
    for n in (30, 60, 90):
        with on(n):
            buy("Paper towels")
    with on(91):
        tools.add_staple("Paper towels", every_days=60)
    for n in (120, 150):
        with on(n):
            buy("Paper towels")
    with on(150):
        s = staple("Paper towels")
        assert (s["cadence_source"], s["cadence_days"]) == ("learned", 30)
        assert s["next_due_at"] == iso(180)


def test_a_rhythm_told_when_the_staple_is_made_beats_older_history():
    """Three fortnightly purchases on the list's history, then "we drink a
    case of sparkling water a week now": the staple is weekly, not the
    history's fortnight (which used to be learned over their words)."""
    for n in (0, 14, 28):
        with on(n):
            buy("Sparkling water")
    with on(30):
        s = tools.add_staple("Sparkling water", every_days=7, category="other")
        assert s["created"] is True
        assert (s["cadence_source"], s["cadence_days"]) == ("told", 7)
        assert s["last_bought_at"] == iso(28)
        assert s["next_due_at"] == iso(35)


# ---------- one odd purchase ----------


def test_a_top_up_two_days_after_the_shop_is_one_restock():
    with on(0):
        tools.add_staple("Milk", category="dairy")
    for n in (7, 14, 16):  # weekly, and a top-up on the Wednesday after
        with on(n):
            buy("Milk")
    with on(16):
        s = staple("Milk")
        # Was ("learned", 4) due day 20: [7, 2] has a median of 4.5.
        assert (s["cadence_source"], s["cadence_days"]) == ("default", 7)
        assert s["last_bought_at"] == iso(16)  # they did buy: it anchors the next one
        assert s["next_due_at"] == iso(23)
    with on(20):
        assert on_list("Milk") == 0
    with on(21):
        buy("Milk")
        s = staple("Milk")
        assert (s["cadence_source"], s["cadence_days"]) == ("learned", 7)


def test_twice_a_week_is_still_a_rhythm():
    """Three or four days apart is a household that shops twice a week — not
    a top-up — and is learned as it is."""
    with on(0):
        tools.add_staple("Bread", category="bakery")
    for n in (3, 7, 10, 14, 17):
        with on(n):
            buy("Bread")
    with on(17):
        assert staple("Bread")["cadence_days"] == 4  # gaps 4, 3, 4, 3
        assert staple("Bread")["cadence_source"] == "learned"
