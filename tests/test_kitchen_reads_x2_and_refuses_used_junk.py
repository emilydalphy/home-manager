"""
Kitchen reads "x2" as 2 (as the grocery box does) and refuses a junk "used"
amount; the add sheets show the server's amount sentence (Emily, 2026-11-01,
branch cloud/kitchen-amounts-1101).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from app import tools
from app.tools import grocery
import nodeharness

REFUSED = "That amount doesn't look right."
STATIC = Path(__file__).resolve().parent.parent / "static"


def _qty(item):
    rows = [r for r in tools.get_inventory() if r["item"].lower() == item.lower()]
    return rows[0]["quantity"] if rows else None


@pytest.mark.parametrize("typed", ["x2", "X2", "×2", "x 2", "2x", "2 ×"])
def test_kitchen_reads_x2_as_two_whichever_way_it_is_typed(signed_in, typed):
    # Exactly the forms the grocery box reads.
    assert grocery.typed_amount(typed) == "2"
    res = signed_in.post("/api/inventory/update", json={"item": "Lemons", "action": "add", "quantity": typed})
    assert res.status_code == 200, res.text
    assert _qty("Lemons") == "2"


def test_kitchen_set_and_the_chat_tool_read_x2_too(signed_in):
    tools.update_inventory("Limes", "add", quantity="x3")
    assert _qty("Limes") == "3"
    tools.update_inventory("Limes", "set", quantity="x5")
    assert _qty("Limes") == "5"


def test_x2_on_the_review_sheets_route_reads_as_two(signed_in):
    res = signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "Apples", "quantity": "x2", "location": "fridge"}]})
    assert res.status_code == 200, res.text
    assert _qty("Apples") == "2"


@pytest.mark.parametrize("amount", ["2 lb", "1/2 cup", "some", "", "1 dozen", "1-2 cups", "half a bag", "1 ½ cups"])
def test_ordinary_amounts_save_exactly_as_before(signed_in, amount):
    res = signed_in.post("/api/inventory/update", json={"item": "Rice", "action": "add", "quantity": amount})
    assert res.status_code == 200, res.text
    assert _qty("Rice") == amount


@pytest.mark.parametrize("amount", ["-2 lb", "-2", "nan", "inf", "-inf", "99999999999999999999"])
def test_used_junk_is_refused_and_changes_nothing(signed_in, amount):
    tools.update_inventory("Ground beef", "add", quantity="3 lb")
    res = signed_in.post("/api/inventory/update", json={"item": "Ground beef", "action": "use", "quantity": amount})
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == REFUSED
    assert _qty("Ground beef") == "3 lb"


def test_used_still_works_and_reads_x2(signed_in):
    tools.update_inventory("Eggs", "add", quantity="12")
    res = signed_in.post("/api/inventory/update", json={"item": "Eggs", "action": "use", "quantity": "x2"})
    assert res.status_code == 200, res.text
    assert _qty("Eggs") == "10"
    signed_in.post("/api/inventory/update", json={"item": "Eggs", "action": "use", "quantity": "1"})
    assert _qty("Eggs") == "9"


def test_chat_tool_refuses_used_junk_clearly():
    tools.update_inventory("Flour", "add", quantity="4 cups")
    with pytest.raises(ValueError, match=REFUSED):
        tools.update_inventory("Flour", "use", quantity="-2 cups")
    with pytest.raises(ValueError, match=REFUSED):
        tools.update_inventory("Flour", "use", quantity=float("nan"))
    out = tools.update_inventory_items([{"item": "Flour", "action": "use", "quantity": "-1 cup"}])
    assert out["updated"] == [{"item": "Flour", "error": REFUSED}]
    assert _qty("Flour") == "4 cups"


def test_zero_still_means_run_out_and_a_scan_zero_is_skipped(signed_in):
    tools.update_inventory("Milk", "add", quantity="1 gallon")
    assert signed_in.post("/api/inventory/update", json={"item": "Milk", "action": "set", "quantity": "0"}).status_code == 200
    assert _qty("Milk") == "0"
    out = tools.update_inventory_items([{"item": "Salt", "action": "add", "quantity": "0"}])
    assert _qty("Salt") is None and out["updated"][0].get("error") == REFUSED


# ---- the sheets' words ----

def _run(fn_src: str, call: str):
    res = nodeharness.run_node(f"{fn_src}\nconsole.log(JSON.stringify({call}));")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node")
def test_review_sheet_shows_the_amount_sentence_only_for_that_400():
    js = (STATIC / "kitchen-add.js").read_text()
    start = js.index('  var AMOUNT_REFUSED')
    body = js[start:js.index("  // The review's one button")]
    src = "var COPY = {saveFailed: 'GENERIC'};\n" + body
    assert _run(src, "saveFailedLine({status: 400, detail: \"That amount doesn't look right.\"})") == REFUSED
    for err in ({"status": 0, "detail": ""}, {"status": 500, "detail": "Server error: x"},
                {"status": 400, "detail": "Nothing was ticked."}, {"status": 409, "detail": REFUSED}):
        assert _run(src, "saveFailedLine(%s)" % json.dumps(err)) == "GENERIC"


def test_inventory_html_add_sheet_shows_the_sentence_and_keeps_the_generic_one():
    html = (STATIC / "inventory.html").read_text()
    block = html[html.index("document.getElementById('inv-add-btn')"):html.index("// ---------- Photo capture")]
    assert "Api.json('/api/inventory/update'" in block and "quiet: true" in block
    assert "err.status === 400 && err.detail === \"That amount doesn't look right.\"" in block
    assert "Couldn't add that — try again." in block
