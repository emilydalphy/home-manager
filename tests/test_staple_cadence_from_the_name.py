"""
A regular added by name alone starts on its food's rhythm, not the catch-all.

Defect hunt, 2026-10-19. Before you shop's "Add a regular" and What we
know's Regulars post only {"item": ...}, so the staple arrives as category
"other" — and POST /api/staples/add {"item": "Milk"} answered
cadence_days 21, "about every 3 weeks". The name already files milk under
Fridge basics; it now picks the cadence too (dairy/produce weekly, meat
fortnightly), read by the Staples card's own whole-word classifier so
the rhythm agrees with the section the card shows.
"""
import pytest


@pytest.mark.parametrize("item, days", [
    ("Milk", 7),
    ("Eggs", 7),
    ("Bananas", 7),
    ("Chicken thighs", 14),
    ("Coffee", 21),
    ("Toilet paper", 30),
    # Whole words, the Staples card's own classifier — never a substring:
    # "butter" in Peanut butter, "ham" in Hamburger, "egg" in Eggo.
    ("Peanut butter", 21),
    ("Graham crackers", 21),
    ("Hamburger buns", 21),
    ("Pepperoni", 21),
    ("Cornflakes", 21),
    ("Eggo waffles", 21),
    ("Ginger ale", 21),
])
def test_a_regular_typed_by_name_starts_on_its_foods_rhythm(signed_in, item, days):
    res = signed_in.post("/api/staples/add", json={"item": item})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["cadence_days"], body["cadence_source"]) == (days, "default")
    weekly_section = {7: "fridge", 14: "fridge"}.get(days)
    if weekly_section:
        assert body["section"] == weekly_section


def test_a_category_or_a_told_cadence_still_wins(signed_in):
    told = signed_in.post("/api/staples/add", json={"item": "Milk", "every_days": 10}).json()
    assert told["cadence_days"] == 10
    filed = signed_in.post("/api/staples/add", json={"item": "Cheese", "category": "pantry"}).json()
    assert filed["cadence_days"] == 21
