"""
A big pot keeps its cups (defect hunt, 2026-10-28).

recipes._easiest_volume says a scaled amount in the fewest scoops within 5%.
5% of a big pot is more than a cup, so a soup's "12 cups water" doubled on
the servings stepper said "23 cups" — one scoop fewer than 24, a whole cup
short — and 10 cups doubled said "19". The slack is now also capped at a
quarter cup. Every amount up to 5 cups says exactly what it said before.

And the same function listed every count from zero up and paired them, so
its time and memory grew with the square of the amount: a stepper asked for
a million servings ate 10 GB and took the server down.
"""
from __future__ import annotations

import time

import pytest

from app import tools
from app.tools import recipes


def _total_tsp(terms):
    return sum(n * recipes._VOLUME_TSP[u] for n, u in terms)


@pytest.mark.parametrize("cups", [20, 23, 24, 30, 48, 100])
def test_whole_cups_stay_whole_cups(cups):
    assert recipes._easiest_volume(cups * 48.0) == [(float(cups), "cup")]


def test_never_off_by_more_than_a_quarter_cup():
    for quarter_tsp in range(240 * 4, 1200 * 4, 7):   # 5 to 25 cups
        tsp = quarter_tsp / 4
        got = _total_tsp(recipes._easiest_volume(tsp))
        assert abs(got - tsp) <= 12 + 1e-6, (tsp, recipes._easiest_volume(tsp))


def test_a_doubled_soup_says_24_cups_on_the_list_and_in_the_steps(signed_in):
    tools.add_recipe(
        "Big Pot Soup",
        ingredients=[
            {"item": "water", "qty": "12 cups", "category": "pantry"},
            {"item": "carrots", "qty": "4", "category": "produce"},
        ],
        instructions=["Bring 12 cups water to a boil.", "Add the carrots."],
        default_servings=8,
    )
    res = signed_in.get("/api/recipes/scale", params={"name": "Big Pot Soup", "servings": 16})
    assert res.status_code == 200
    body = res.json()
    water = next(i for i in body["scaled_ingredients"] if i["item"] == "water")
    assert water["qty"] == "24 cups"
    assert "24 cups water" in body["scaled_instructions"][0]


def test_a_huge_serving_count_answers_instead_of_eating_the_server(signed_in):
    tools.add_recipe(
        "Pancakes Big",
        ingredients=[
            {"item": "flour", "qty": "2 cups", "category": "pantry"},
            {"item": "milk", "qty": "1 1/2 cups", "category": "dairy"},
        ],
        instructions=["Whisk 2 cups flour with 1 1/2 cups milk."],
        default_servings=4,
    )
    started = time.monotonic()
    res = signed_in.get("/api/recipes/scale", params={"name": "Pancakes Big", "servings": 1_000_000})
    assert res.status_code == 200
    assert time.monotonic() - started < 5
    flour = next(i for i in res.json()["scaled_ingredients"] if i["item"] == "flour")
    assert flour["qty"] == "500000 cups"
