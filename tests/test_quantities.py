"""
Direct coverage of app.tools.quantities' container-unit pluralization,
which until now was only ever exercised indirectly (see
tests/test_attendance.py's former "2 tin" known-gap assertion).

_CONTAINER_UNIT_PLURALS drives every household's grocery list, every week
-- see quantities.py's own comment on the table -- so it's worth testing
directly rather than only through whatever public tool function happens
to touch it this week.
"""
import pytest

from app.tools import quantities


@pytest.mark.parametrize("unit,plural", [
    ("bag", "bags"), ("box", "boxes"), ("can", "cans"), ("jar", "jars"),
    ("bottle", "bottles"), ("block", "blocks"), ("bunch", "bunches"),
    ("head", "heads"), ("pint", "pints"), ("clove", "cloves"),
    ("tub", "tubs"), ("container", "containers"), ("pack", "packs"),
    ("loaf", "loaves"), ("stick", "sticks"),
    # Added 2026-09-03 -- previously missing (displayed unpluralized,
    # e.g. "2 tin"), surfaced more often once attendance scaling started
    # multiplying quantities.
    ("tin", "tins"), ("carton", "cartons"), ("punnet", "punnets"),
    ("sachet", "sachets"), ("bar", "bars"), ("roll", "rolls"),
    ("sprig", "sprigs"), ("slice", "slices"),
])
def test_container_unit_pluralizes_above_one(unit, plural):
    assert quantities._format_quantity(1, unit) == f"1 {unit}"
    assert quantities._format_quantity(2, unit) == f"2 {plural}"


@pytest.mark.parametrize("unit,plural", [
    ("tin", "tins"), ("jar", "jars"), ("carton", "cartons"),
])
def test_a_size_prefixed_container_pluralizes_too(unit, plural):
    """
    A compound unit like "1 lb bag" (a number + amount-unit + container
    word) is the two-word shape _format_quantity's rpartition handles --
    the trailing word is what gets pluralized, whatever comes before it.
    """
    assert quantities._format_quantity(1, f"lb {unit}") == f"1 lb {unit}"
    assert quantities._format_quantity(3, f"lb {unit}") == f"3 lb {plural}"


def test_a_pluralized_container_re_parses_to_the_same_unit_as_singular():
    """
    _normalize_container_word has to undo _format_quantity's own
    pluralization on the way back in, or re-parsing an already-merged
    "2 tins" to add a third "1 tin" sees "tins" != "tin" and fails to
    match -- the exact bug _normalize_container_word exists to prevent
    for the older container words, now covered for the newly-added ones.
    """
    for plural, singular in [("tins", "tin"), ("cartons", "carton")]:
        assert quantities._normalize_container_word(plural) == singular
        assert quantities._parse_quantity(f"1 {plural}") == quantities._parse_quantity(f"1 {singular}")


def test_container_word_not_in_the_table_still_displays_unpluralized():
    """
    Documents the current boundary of the table rather than silently
    letting an unlisted word regress -- if this starts failing because
    someone added "wedge" to the table, update this test to a word that's
    still genuinely absent.
    """
    assert quantities._format_quantity(2, "wedge") == "2 wedge"


def test_a_size_descriptor_alone_carries_no_unit_to_pluralize():
    """
    "Large"/"medium"/"small" etc. are size descriptors, not container
    words -- _UNIT_ALIASES normalizes them to no unit at all (see that
    table's own comment), specifically so "1 large" and a bare "1/2"
    merge into a single count instead of concatenating literally. That
    means a grocery item like "onion" with quantity "1 large" never has
    "onion" itself pass through this module to be pluralized: the item
    name and the quantity string are two separate fields everywhere in
    the app (grocery_items.item vs .quantity; a recipe ingredient's
    {"item": ..., "qty": ...}), so quantities.py never sees "large onion"
    as one string. A real container word combined with a size descriptor
    ("1 large jar") already pluralizes correctly today, because the
    container word is what's trailing and _CONTAINER_UNIT_PLURALS matches
    on that -- see test_a_size_prefixed_container_pluralizes_too.
    """
    assert quantities._parse_quantity("1 large") == (1.0, None)
    assert quantities._format_quantity(2, None) == "2"
    # The size-plus-container case behaves as a shopper would expect:
    assert quantities._format_quantity(2, "large jar") == "2 large jars"
