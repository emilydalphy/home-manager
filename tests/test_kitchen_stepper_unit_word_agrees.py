"""
The Kitchen item sheet's +/- keeps its unit word in agreement with the
number (defect hunt, 2026-11-01): a tin of beans stepped up read "2 can",
a bag of spinach "2 bag", and two bunches of bananas stepped down read
"1 bunches".
"""
import pytest

from app.tools import inventory


def _add(client, item, quantity, location="pantry"):
    res = client.post("/api/inventory/update", json={
        "item": item, "action": "add", "quantity": quantity, "location": location,
    })
    assert res.status_code == 200
    data = client.get("/api/inventory/by-location").json()
    return next(i for loc in data["locations"] for i in loc["items"] if i["item"] == item)


@pytest.mark.parametrize("item,typed,delta,shown", [
    ("Black beans", "1 can", 1, "2 cans"),
    ("Spinach", "1 bag", 1, "2 bags"),
    ("Bananas", "2 bunches", -1, "1 bunch"),
    ("Yogurt", "1 cup", 1, "2 cups"),
])
def test_the_stepper_says_the_unit_the_way_the_number_needs(signed_in, item, typed, delta, shown):
    row = _add(signed_in, item, typed)
    res = signed_in.post(f"/api/inventory/{row['id']}/quantity", json={"delta": delta})
    assert res.status_code == 200
    assert res.json()["quantity"] == shown


@pytest.mark.parametrize("raw,delta,expected", [
    ("1 lb bag", 1, "2 lb bags"),          # the bag is what's counted
    ("2 lbs", -1, "1 lb"),
    ("1 can black beans", 1, "2 cans black beans"),
    ("12 eggs", -11, "1 egg"),
    ("1 cup", -1, "0 cups"),
    # Left as typed: no plural of its own, or not a unit word at all.
    ("1 dozen eggs", 1, "2 dozen eggs"),
    ("3 gal", -1, "2 gal"),
    ("some", 1, "1 some"),
])
def test_only_a_unit_word_changes(raw, delta, expected):
    assert inventory._step_quantity_text(raw, delta) == expected
