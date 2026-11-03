"""
A bunch, bag or net of produce is many pieces, not one (defect hunt,
2026-11-03). The list's weight-per-piece table (quantities._POUNDS_EACH)
turned a household's "bananas 6" and later "banana 1 bunch" into
"7 bunches", and "potatoes 5 lbs" + "1 bag" into "11 bags": the bare count
and the pounds were added onto whatever word the other amount used.
"""
import pytest

from app import tools
from app.tools import quantities as q


@pytest.mark.parametrize("item,raw", [
    ("bananas", "6 + 1 bunch"),
    ("bananas", "1 bunch + 2 lbs"),
    ("carrots", "1 lb + 1 bunch"),
    ("onions", "2 + 1 bag"),
    ("potatoes", "5 lbs + 1 bag"),
    ("apples", "1 bag + 3"),
])
def test_a_bunch_or_bag_keeps_both_amounts(item, raw):
    assert q.tidy_display_quantity(item, raw) == raw


@pytest.mark.parametrize("item,raw,expected", [
    ("Carrots", "16 + 1.25 lbs", "21"),        # pieces and pounds are still one count
    ("carrots", "3 carrots + 1 lb", "7 carrots"),
    ("apple", "2 each + 1 lb", "5 each"),
    ("onions", "2 pcs + 1 lb", "4 pcs"),       # abbreviations are pieces too (review)
    ("onions", "2 ea + 1 lb", "4 ea"),
])
def test_pieces_and_pounds_still_read_as_one_count(item, raw, expected):
    assert q.tidy_display_quantity(item, raw) == expected


def test_the_shop_list_shows_six_bananas_and_a_bunch():
    tools.add_grocery_item("bananas", "6")
    tools.add_grocery_item("banana", "1 bunch")
    rows = [r for r in tools.list_grocery_list() if r["item"] == "bananas"]
    assert [r["quantity"] for r in rows] == ["6 + 1 bunch"]
