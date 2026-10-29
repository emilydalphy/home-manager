"""
A store's "usually get here" items hold each thing once (defect hunt,
2026-10-29).

Preferences › Stores and chat both add through
POST /api/memory/store-items/add. The merge was on exact text, so "milk"
beside "Milk", or "egg" beside "eggs", was a second chip under the same
store and a second suggestion on Shop for one thing — although removing
one already took both (remove_store_typical_item matches by _merge_key).
"""
from app import tools


def _items(signed_in, store):
    return signed_in.get("/api/memory").json()["store_typical_items"].get(store, [])


def test_the_same_item_in_other_capitals_is_one_item(signed_in):
    tools.edit_preference("usual_stores", ["Farm Boy"])
    signed_in.post("/api/memory/store-items/add", json={"store": "Farm Boy", "item": "Milk"})

    res = signed_in.post("/api/memory/store-items/add", json={"store": "farm boy", "item": "milk "})

    assert res.status_code == 200
    assert _items(signed_in, "Farm Boy") == ["Milk"]


def test_singular_and_plural_are_one_item_and_the_first_spelling_stays(signed_in):
    tools.edit_preference("usual_stores", ["Costco"])
    tools.add_store_typical_items("Costco", ["eggs", "paper towels"])
    tools.add_store_typical_items("Costco", ["Egg", "Paper towel", "coffee"])

    assert _items(signed_in, "Costco") == ["eggs", "paper towels", "coffee"]


def test_removing_it_leaves_nothing_behind(signed_in):
    tools.edit_preference("usual_stores", ["Costco"])
    tools.add_store_typical_items("Costco", ["Milk"])
    tools.add_store_typical_items("Costco", ["milk"])

    signed_in.post("/api/memory/store-items/remove", json={"store": "Costco", "item": "Milk"})

    assert _items(signed_in, "Costco") == []
