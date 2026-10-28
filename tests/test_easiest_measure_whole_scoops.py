"""
A big batch on the servings stepper says the whole amount, and says it fast.

Found driving cook mode (defect hunt, 2026-10-28) with a 4-serving soup
(8 cups stock, 4 cups water) on the stepper:

  * at 10 it said "19 cups stock", at 20 "38 cups stock and 19 cups water".
    recipes._easiest_volume takes the fewest scoops within 5%, and from 20
    cups up 5% is a whole cup — so leaving one out "won". Same below a cup's
    size: 2 cups + 2 tbsp read "2 cups + 1 tbsp".
  * the search paired every first measure with every top-up, quadratic in
    the amount: about a second a tap at 20 servings of that soup, and
    servings=1000 on /api/recipes/scale never came back.
"""
from __future__ import annotations

import time

import pytest

from app import tools
from app.tools import recipes


def _total(terms):
    return sum(n * recipes._VOLUME_TSP[u] for n, u in terms)


@pytest.mark.parametrize("tsp, expected", [
    (20 * 48.0, [(20.0, "cup")]),
    (24 * 48.0, [(24.0, "cup")]),
    (40 * 48.0, [(40.0, "cup")]),
    (102.0, [(2.0, "cup"), (2.0, "tbsp")]),     # not "2 cups + 1 tbsp"
    (90.0, [(1.75, "cup"), (2.0, "tbsp")]),
    # The rounding Emily asked for is untouched: under one of the measure.
    (8.0, [(2.0, "tbsp"), (2.0, "tsp")]),
    (12.0, [(0.25, "cup")]),
    (54.0, [(1.0, "cup"), (2.0, "tbsp")]),
])
def test_never_a_whole_measure_short(tsp, expected):
    got = recipes._easiest_volume(tsp)
    assert [(round(n, 4), u) for n, u in got] == [(round(n, 4), u) for n, u in expected]


def test_never_off_by_a_whole_one_of_its_smallest_measure():
    for cups_eighths in range(8, 8 * 60, 3):       # 1 .. 60 cups, in odd steps
        tsp = cups_eighths * 6.0
        terms = recipes._easiest_volume(tsp)
        smallest = recipes._VOLUME_TSP[terms[-1][1]]
        assert abs(_total(terms) - tsp) < smallest, (tsp, terms)


def test_the_stepper_says_the_full_soup_and_answers_quickly(signed_in):
    tools.add_recipe(
        "Big Soup",
        ingredients=[{"item": "Chicken stock", "qty": "8 cups"}, {"item": "Water", "qty": "4 cups"}],
        instructions=["Add 8 cups stock and 4 cups water."],
        default_servings=4,
    )
    res = signed_in.get("/api/recipes/scale", params={"name": "Big Soup", "servings": 20})
    assert res.status_code == 200
    body = res.json()
    assert {i["item"]: i["qty"] for i in body["scaled_ingredients"]} == {
        "Chicken stock": "40 cups", "Water": "20 cups",
    }
    assert body["scaled_instructions"] == ["Add 40 cups stock and 20 cups water."]

    started = time.monotonic()
    big = signed_in.get("/api/recipes/scale", params={"name": "Big Soup", "servings": 400})
    assert big.status_code == 200
    assert time.monotonic() - started < 5, "linear in the amount, not quadratic"
