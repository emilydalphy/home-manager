"""
"Use soon" must mean the same thing everywhere on the Inventory screen.

The screen shows a count ("3 to use soon"), a flag on each qualifying row,
and — since that count became a filter — a filtered list. All three have to
agree, or the screen contradicts itself: a count that says three, a filter
that shows two, and a badge on a fourth. That is the specific way this kind
of feature goes wrong, and it goes wrong quietly.

The threshold used to be written out separately at each call site, plus a
fourth time as `tools.get_expiring_soon(days=4)`'s default. It is now
defined once in the page as USE_SOON_DAYS with an isUseSoon() helper, and
these tests run that real helper under node rather than checking the source
for a number.

`node` is present on the GitHub runner the workflow uses; if it ever isn't,
these skip rather than fail.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import tools


INVENTORY = Path(__file__).resolve().parent.parent / "static" / "inventory.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the page's own helper"
)


def _iso(days: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _extract(name: str, source: str) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _is_use_soon(expiration_dates: list) -> list[bool]:
    """Run the page's real isUseSoon over a list of expiry dates."""
    src = INVENTORY.read_text()
    threshold = re.search(r"const USE_SOON_DAYS = (\d+);", src)
    assert threshold, "USE_SOON_DAYS is gone — the threshold has been inlined again"
    items = [{"expiration_date": d} for d in expiration_dates]
    harness = (
        f"const USE_SOON_DAYS = {threshold.group(1)};\n"
        + _extract("daysUntil", src)
        + "\n"
        + _extract("isUseSoon", src)
        + "\n"
        + f"console.log(JSON.stringify({json.dumps(items)}.map(isUseSoon)));\n"
    )
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def test_the_boundary_is_where_the_badge_says_it_is():
    """Today, tomorrow and four days out qualify; five days out does not."""
    got = _is_use_soon([_iso(0), _iso(1), _iso(4), _iso(5), _iso(30)])
    assert got == [True, True, True, False, False], got


def test_something_already_past_its_date_still_counts():
    """
    Expired is the most urgent case there is. A rule written as "within the
    next four days" that quietly excludes anything already past would hide
    exactly the items you most need to see.
    """
    assert _is_use_soon([_iso(-1), _iso(-10)]) == [True, True]


def test_an_item_with_no_expiry_date_is_never_use_soon():
    """
    Most rows have no date — they arrive from checking off the grocery
    list. Treating a missing date as urgent would flag the whole kitchen.
    """
    assert _is_use_soon([None, ""]) == [False, False]


def test_the_screen_and_the_backend_agree_on_the_threshold():
    """
    The page counts and filters in the browser; `get_expiring_soon` answers
    the same question on the server, and the Kitchen hub's summary uses the
    server's answer. If the two numbers drift, the hub and the screen show
    different counts for the same kitchen.
    """
    src = INVENTORY.read_text()
    page_days = int(re.search(r"const USE_SOON_DAYS = (\d+);", src).group(1))

    tools.update_inventory("Milk", action="add", quantity="1", expiration_date=_iso(page_days))
    tools.update_inventory("Rice", action="add", quantity="1", expiration_date=_iso(page_days + 1))

    names = {i["item"] for i in tools.get_expiring_soon(days=page_days)}
    assert "Milk" in names, (
        f"the page treats {page_days} days out as 'use soon' but get_expiring_soon "
        f"does not — the Inventory screen and the Kitchen hub would disagree"
    )
    assert "Rice" not in names, "the backend is more generous than the page"
