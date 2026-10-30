"""
A "½" a person adds is half (defect hunt, 2026-10-30).

The grocery list's parsers read "1/2" and never "½", so a scanned recipe
screenshot's "½ lb butter" stayed a glyph nothing could add to: the next
"1 lb" of butter stood beside it as "½ lb + 1 lb".
"""
from __future__ import annotations

from app import tools


def test_a_scanned_half_pound_adds_up_with_a_typed_pound(signed_in):
    c = signed_in
    c.post("/api/grocery-list/confirm-scan", json={"items": [{"item": "Butter", "quantity": "½ lb"}]})
    out = c.post("/api/grocery-list/add", json={"item": "Butter", "quantity": "1 lb"}).json()
    assert out["merged"] is True
    assert out["quantity"] == "1.5 lbs"


def test_a_whole_and_a_glyph_reads_as_one_amount():
    first = tools.add_grocery_item("Heavy cream", quantity="1½ cups")
    assert "½" not in first["quantity"]
    out = tools.add_grocery_item("Heavy cream", quantity="½ cup")
    assert " + " not in out["quantity"]


def test_the_rows_box_takes_a_glyph_too():
    line = tools.add_grocery_item("Feta", quantity="200 g")
    out = tools.update_grocery_item(line["item_id"], quantity="1 ½ lb")
    assert out["quantity"] == "1 1/2 lb"
