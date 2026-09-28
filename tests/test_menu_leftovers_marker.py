"""
The "What we're eating" leftovers marker (Emily, 2026-09-28, phone-tested
her week): "If one of the meals appears twice under the meals (because
it's leftovers) it should have a little marker that it's leftovers so
it's not confusing why it's there twice."

Before this, wkMenuGroups (shell.js) grouped a menu row by meal type
first, so a dinner's batch eaten the next day as lunch read as two plain,
identical-looking rows — "Garlic Shrimp with Herbed Rice" under DINNERS
*and* under LUNCHES — with nothing to say the second one was the first
one's leftovers. Now a row where every one of its days is a leftover
occurrence (leftover_from) carries a "Leftovers" — or "Made ahead" for a
batch the household chose on purpose — tag beside its name, its days
phrase names the source ("Fri · from Thursday's dinner"), and it offers
no Swap of its own (swapping belongs to the cooked dish; the Meal step's
own Swap still reaches the entry, unaffected by this).

A row that mixes a cook day with a later leftover day of the SAME meal
type (a dinner batch eaten again as dinner two nights later) is left
exactly as it was before this card — one merged row, "Mon, Tue" — since
that is one continuous dish, not the cross-section duplicate Emily
described. See tests/test_plan_rows_tweak_it.py for that case; this file
is a regression guard so a further edit to wkMenuGroups can't quietly
merge the two kinds of leftover row back together.
"""
from __future__ import annotations

import json
import shutil

import pytest

import nodeharness
from test_plan_cards_2026_09_18 import _prelude, _entry, _day, SHELL_JS

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the renderers")

_MON = "2026-09-21"
_TUE = "2026-09-22"


def _run(js: str):
    res = nodeharness.run_node(js, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_a_dinners_leftovers_eaten_as_lunch_carries_the_leftovers_tag_and_names_its_source():
    days = [
        _day(_MON, dinner=_entry("Garlic Shrimp with Herbed Rice", entry_id=13, meta="30 min")),
        _day(_TUE, lunch=_entry("Leftovers — Monday’s Garlic Shrimp with Herbed Rice", entry_id=22, meta="reheat",
                                 source="leftovers",
                                 leftover_from={"date": _MON, "meal": "Garlic Shrimp with Herbed Rice",
                                                "cook_ahead": False, "slot": "dinner"})),
    ]
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkMenuHtml({json.dumps(days)})));")
    # Two separate rows — one per meal-type card — not a silent duplicate.
    assert html.count('class="wk-row-name dish-link"') == 2
    # Group order is breakfast, lunch, dinner, snack (WK_MENU_LABELS) — with
    # only lunch and dinner present, Lunches renders first.
    lunch_row, dinner_row = html.split(">Dinners<", 1)
    assert '<span class="wk-leftover-tag">Leftovers</span>' in lunch_row
    assert "Tuesday · from Monday’s dinner" in lunch_row
    # No Swap on the leftovers row — swapping belongs to the cooked dish.
    assert 'data-wk-swap-sheet="lunch"' not in lunch_row
    # The dinner row (the cook) is untouched: no tag, Swap still offered.
    assert "wk-leftover-tag" not in dinner_row
    assert 'data-wk-swap-sheet="dinner"' in dinner_row


@_needs_node
def test_a_deliberate_batch_reads_made_ahead_not_leftovers():
    days = [
        _day(_MON, breakfast=_entry("Egg White Bites", entry_id=11, meta="15 min")),
        _day(_TUE, lunch=_entry("Made ahead — Monday’s Egg White Bites", entry_id=21, meta="reheat",
                                 source="leftovers",
                                 leftover_from={"date": _MON, "meal": "Egg White Bites",
                                                "cook_ahead": True, "slot": "breakfast"})),
    ]
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkMenuHtml({json.dumps(days)})));")
    assert '<span class="wk-leftover-tag">Made ahead</span>' in html
    assert "wk-leftover-tag\">Leftovers<" not in html
    assert "made ahead Monday’s breakfast" in html


@_needs_node
def test_a_dish_cooked_then_reheated_the_same_meal_type_stays_one_merged_row_untagged():
    """The 2026-09-26 design (test_plan_rows_tweak_it.py): a batch cooked
    for dinner and eaten again as dinner two nights later is one row,
    "Mon, Wed", not a second, tagged row — this only marks a row that is
    ENTIRELY a leftover occurrence."""
    days = [
        _day(_MON, dinner=_entry("Salmon and rice", entry_id=13, meta="30 min")),
        _day(_TUE, dinner=_entry("Made ahead — Monday’s Salmon and rice", entry_id=23, meta="reheat",
                                  source="leftovers",
                                  leftover_from={"date": _MON, "meal": "Salmon and rice",
                                                 "cook_ahead": True, "slot": "dinner"})),
    ]
    html = _run(_prelude() + f"weekState.days = {json.dumps(days)};\n"
                f"console.log(JSON.stringify(wkMenuHtml({json.dumps(days)})));")
    assert html.count('class="wk-row-name dish-link"') == 1, "one merged row, not two"
    assert "wk-leftover-tag" not in html
    assert 'data-wk-swap-sheet="dinner"' in html, "Swap is still offered on the merged cook row"
