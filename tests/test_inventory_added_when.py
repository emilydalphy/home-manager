"""
The Inventory sheet's "added …" kicker reads created_at as UTC (defect hunt,
2026-10-29). inventory_items.created_at is SQLite's datetime('now') — UTC
with no zone — and the sheet parsed it as the phone's own time, so the day
was off by the whole UTC offset: in Toronto, a thing added after 8pm on the
last of a month read "added Nov 1" (a day that hasn't come); in Auckland, a
thing added on the morning of the 1st read "added Oct 31".
"""
from __future__ import annotations

import json
import os
import re

from nodeharness import run_node

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


def _added_when(stamp: str, now_utc: str, zone: str) -> str:
    with open(os.path.join(STATIC, "inventory.html"), encoding="utf-8") as f:
        html = f.read()
    fn = re.search(r"function addedWhen\(stamp, now\) \{.*?\n\}\n", html, re.S).group(0)
    script = (
        f"process.env.TZ = {json.dumps(zone)};\n{fn}\n"
        f"process.stdout.write(addedWhen({json.dumps(stamp)}, new Date({json.dumps(now_utc)})));\n"
    )
    out = run_node(script)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_added_at_9pm_on_the_last_of_the_month_in_toronto_is_this_month():
    # 01:00 UTC Nov 1 is 9pm Oct 31 in Toronto; it is still Oct 31 there.
    assert _added_when("2026-11-01 01:00:00", "2026-11-01T01:05:00Z", "America/Toronto") == "added this month"


def test_added_on_the_morning_of_the_first_in_auckland_is_this_month():
    # 20:00 UTC Oct 31 is 9am Nov 1 in Auckland (NZDT).
    assert _added_when("2026-10-31 20:00:00", "2026-10-31T21:00:00Z", "Pacific/Auckland") == "added this month"


def test_an_older_add_names_its_own_local_day():
    # 02:00 UTC Oct 1 is the evening of Sep 30 in Los Angeles.
    out = _added_when("2026-10-01 02:00:00", "2026-10-29T18:00:00Z", "America/Los_Angeles")
    assert out.startswith("added ") and "30" in out and "Sep" in out, out


def test_no_stamp_says_earlier():
    assert _added_when("", "2026-10-29T18:00:00Z", "America/Toronto") == "added earlier"
