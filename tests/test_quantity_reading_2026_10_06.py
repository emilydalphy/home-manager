"""
How a grocery quantity and a duration READ (Loop Board, 2026-10-06
walkthrough): "1 ¼ Lemon", "4 ⅛ cups beef broth", "Carrots 16 + 1.25 lbs",
"260 min". Each test is red on main (the functions did not exist / the list
returned the raw decimal).
"""
from __future__ import annotations

import pytest

from app.tools import grocery, moves
from app.tools import quantities as q


@pytest.mark.parametrize("raw,expected", [
    ("1.25", "2"), ("1.25 lemon", "2 lemon"), ("1 1/4 lemon", "2 lemon"),
    ("1.5 onions", "2 onions"), ("2 onions", "2 onions"),
])
def test_count_items_round_up_to_whole_numbers(raw, expected):
    assert q.tidy_display_quantity("Lemon", raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("4.125 cups", "4 cups"),      # no eighths
    ("4.4 cups", "4.33 cups"),     # thirds are allowed
    ("2.5 cups", "2.5 cups"), ("0.75 cup", "0.75 cup"), ("2.1 cups", "2 cups"),
])
def test_volumes_land_on_quarters_thirds_and_halves(raw, expected):
    assert q.tidy_display_quantity("beef broth", raw) == expected


def test_one_item_never_shows_two_units():
    assert q.tidy_display_quantity("Carrots", "16 + 1.25 lbs") == "21"
    assert " + " not in q.tidy_display_quantity("Lemons", "2 lemon + 0.5 lb")


def test_freeform_text_is_left_alone():
    assert q.tidy_display_quantity("Cilantro", "a bunch") == "a bunch"
    assert q.tidy_display_quantity("Cilantro", "") == ""


@pytest.mark.parametrize("minutes,expected", [
    (5, "5 min"), (45, "45 min"), (60, "1 hr"), (90, "1 hr 30 min"), (260, "4 hr 20 min"),
])
def test_durations_of_an_hour_or_more_read_in_hours(minutes, expected):
    assert q.format_duration(minutes) == expected


def test_the_list_shows_the_tidied_amount(monkeypatch):
    class _Row(dict):
        pass
    rows = [{"item": "Lemon", "quantity": "1.25"}]
    monkeypatch.setattr(grocery, "get_conn", lambda: type("C", (), {
        "execute": lambda self, *a, **k: type("R", (), {"fetchall": lambda s: rows})(),
        "close": lambda self: None})())
    assert grocery.list_grocery_list(status="needed")[0]["quantity"] == "2"
