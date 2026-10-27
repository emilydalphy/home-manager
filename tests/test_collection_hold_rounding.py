"""
A run that isn't near midnight must not hold collection on a pinned clock
(2026-10-27). conftest's hold compared the start instant against itself
round-tripped through a datetime, which drops sub-microsecond digits, so it
read "earlier" in about a third of runs and froze collection for nothing.
Any module keeping `TODAY = datetime.date.today` then held freezegun's fake
function and failed every test once the hold let go.
"""
from __future__ import annotations

import datetime
import random

from freezegun.api import real_datetime

import conftest


def test_the_round_trip_alone_never_counts_as_drawn_back():
    rng = random.Random(20261027)
    for _ in range(20000):
        epoch = 1_790_000_000 + rng.random() * 10_000_000
        held = real_datetime.fromtimestamp(epoch)
        assert not conftest._drawn_back(held, epoch), epoch


def test_a_real_margin_still_counts():
    epoch = 1_793_000_000.5
    held = real_datetime.fromtimestamp(epoch - conftest._MIDNIGHT_MARGIN_SECONDS)
    assert conftest._drawn_back(held, epoch)


def test_this_run_collected_on_the_real_clock():
    """Not near midnight, collection was not held, so a module-level
    `datetime.date.today` here is the real one."""
    if conftest._COLLECTION_CLOCK is None:
        return
    epoch = conftest._COLLECTION_CLOCK[0]
    if conftest._drawn_back(conftest._held_to_the_starting_day(), epoch):
        return  # genuinely started inside the margin; the hold is right
    assert _TODAY_AT_IMPORT.__self__ is datetime.date


_TODAY_AT_IMPORT = datetime.date.today
