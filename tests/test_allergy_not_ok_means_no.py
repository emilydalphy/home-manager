"""
"Not ok" in a must-avoid note means no (review of the allergy-note branch,
2026-10-29).

_FINE_RE read the bare word, so "Sam is allergic to tree nuts, almonds are
not ok either" made the almonds an EXCEPTION and almond flour passed the
allergy check — the opposite of what was typed. A fine-word now counts only
without a "not / never / isn't…" just before it; a plain "fine" still makes
an exception, as before.
"""
from __future__ import annotations

import pytest

from app import tools
from app.tools import allergen_gate, coordination


@pytest.fixture
def sam():
    tools.add_member("Sam")


@pytest.mark.parametrize("fact", [
    "Sam is allergic to tree nuts, almonds are not ok either",
    "Sam is allergic to tree nuts but almonds aren't ok either",
    "Sam is allergic to tree nuts, almonds are never okay",
])
def test_not_ok_holds_the_food_back(sam, fact):
    tools.add_fact("people", fact, hard=True)

    assert allergen_gate.hard_clashes("Almond Cake", [{"item": "almond flour", "qty": "2 cups"}]), fact
    assert allergen_gate.hard_clashes("Walnut Salad", [{"item": "walnuts", "qty": "1 cup"}]), fact


@pytest.mark.parametrize("fact, dish, item", [
    # Review, 2026-10-29: a "not ok" food OUTSIDE the first avoidance's
    # family — the filler words used to ride along in the phrase and match
    # nothing.
    ("Sam is allergic to peanuts, sesame is not ok either", "Sesame Noodles", "sesame oil"),
    ("Sam is allergic to dairy, eggs are not ok", "Omelette", "eggs"),
    ("Sam is allergic to peanuts but sesame isn't ok either", "Hummus", "tahini"),
])
def test_not_ok_holds_a_food_from_another_family(sam, fact, dish, item):
    tools.add_fact("people", fact, hard=True)

    assert allergen_gate.hard_clashes(dish, [{"item": item, "qty": "1"}]), fact


def test_a_plain_fine_is_still_an_exception(sam):
    tools.add_fact("people", "Sam is allergic to tree nuts, almonds are fine", hard=True)

    assert allergen_gate.hard_clashes("Almond Cake", [{"item": "almond flour", "qty": "2 cups"}]) == []
    assert allergen_gate.hard_clashes("Walnut Salad", [{"item": "walnuts", "qty": "1 cup"}])


def test_says_fine_reads_the_negation():
    assert coordination.says_fine("oat milk is fine")
    assert coordination.says_fine("almonds are ok")
    assert not coordination.says_fine("almonds are not ok either")
    assert not coordination.says_fine("almonds aren’t okay")
    assert not coordination.says_fine("we love peppers")
