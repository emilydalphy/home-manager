"""
A shopper's "24 pack" is one pack of 24, not twenty-four packs (defect hunt,
2026-10-24).

Typed into Shop's add field as "24 pack water" (groParseAddInput hands the
server quantity "24 pack"), the line read "Water · 24 packs", and adding the
same thing again made it "48 packs". quantities._parse_quantity reads a
number and a unit word, and a singular "pack" after a number above one is
the size of a multipack, not a count of packs. A person's typed amount is
now written "24-pack" (quantities.typed_multipack) — the spelling the list
already leaves alone for "6-pack" — on the add and on the row's own
quantity box. A plan's amount is untouched.
"""
from __future__ import annotations

import pytest

from app import tools
from app.db import get_conn
from app.tools import quantities


def _line(item_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT item, quantity FROM grocery_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row)


def _shown(signed_in, item_id: int) -> str:
    sections = signed_in.get("/api/grocery-list").json()["sections"]
    return next(i["quantity"] for s in sections for i in s["items"] if i["id"] == item_id)


@pytest.mark.parametrize("typed, stored", [
    ("24 pack", "24-pack"),
    ("12pk", "12-pack"),
    ("6 Pack", "6-pack"),
    ("24 pack (500 ml)", "24-pack (500 ml)"),
    # Not a multipack: left exactly as before.
    ("1 pack", "1 pack"),
    ("3 packs", "3 packs"),
    ("24-pack", "24-pack"),
    ("2 lbs", "2 lbs"),
])
def test_typed_multipack(typed, stored):
    assert quantities._normalize_grocery_quantity(quantities.typed_multipack(typed)) == stored


def test_a_typed_24_pack_is_one_pack_on_the_list_and_counts_up_once(clean_state, signed_in):
    first = signed_in.post("/api/grocery-list/add", json={"item": "Water", "quantity": "24 pack"}).json()
    assert _shown(signed_in, first["item_id"]) == "24-pack"
    again = signed_in.post("/api/grocery-list/add", json={"item": "water", "quantity": "24 pack"}).json()
    assert again["item_id"] == first["item_id"]
    assert _shown(signed_in, first["item_id"]) == "24-pack ×2"


def test_the_rows_quantity_box_reads_a_multipack_the_same_way(clean_state, signed_in):
    added = tools.add_grocery_item("Sparkling water", quantity="1")
    res = signed_in.post(f"/api/grocery-list/{added['item_id']}/update", json={"quantity": "12 pack"})
    assert res.status_code == 200
    assert _line(added["item_id"])["quantity"] == "12-pack"
    assert _shown(signed_in, added["item_id"]) == "12-pack"
    # Saved again unchanged from the box: nothing moves.
    signed_in.post(f"/api/grocery-list/{added['item_id']}/update", json={"quantity": "12-pack"})
    assert _line(added["item_id"])["quantity"] == "12-pack"


def test_a_plans_amount_is_not_read_as_a_multipack(clean_state):
    plan = tools.create_weekly_plan("2026-10-26", day_count=7)["weekly_plan_id"]
    added = tools.add_grocery_item("Tortillas", quantity="2 pack", source_weekly_plan_id=plan)
    assert _line(added["item_id"])["quantity"] == "2 packs"


# ---------- the Shop add field (static/shell.js groParseAddInput) ----------
# Review, 2026-10-24: "12 pk water", "water 24pk" and "24-pack water" never
# reached the server as an amount — "pk" was not a unit word and the parser
# could not read a hyphen — so the whole text became the item's name.

import json
import shutil
from pathlib import Path

import nodeharness

_SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")


def _parser_source() -> str:
    start = _SHELL_JS.index("  var GRO_ADD_UNIT_WORDS = [")
    end = _SHELL_JS.index("\n  }\n", _SHELL_JS.index("  function groParseAddInput(", start)) + 4
    return _SHELL_JS[start:end]


_ADD_FIELD_CASES = [
    ("12 pk water", {"item": "water", "quantity": "12 pk"}),
    ("water 24pk", {"item": "water", "quantity": "24 pk"}),
    ("24-pack water", {"item": "water", "quantity": "24 pack"}),
    ("Water 24-pack", {"item": "Water", "quantity": "24 pack"}),
    ("24 pack water", {"item": "water", "quantity": "24 pack"}),
    # Unchanged readings.
    ("2 lb carrots", {"item": "carrots", "quantity": "2 lb"}),
    ("milk 2", {"item": "milk", "quantity": "2"}),
    ("7-up", {"item": "7-up", "quantity": ""}),
    ("3-4 apples", {"item": "3-4 apples", "quantity": ""}),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the shell's own parser")
def test_the_add_field_reads_a_multipack_off_either_end():
    typed = [t for t, _ in _ADD_FIELD_CASES]
    script = _parser_source() + f"\nconsole.log(JSON.stringify({json.dumps(typed)}.map(groParseAddInput)));\n"
    res = nodeharness.run_node(script)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout.strip().splitlines()[-1])
    assert got == [want for _, want in _ADD_FIELD_CASES]


def test_what_the_add_field_sends_lands_as_one_pack(clean_state, signed_in):
    for i, quantity in enumerate(["12 pk", "24 pk", "24 pack"]):
        added = signed_in.post("/api/grocery-list/add", json={"item": f"Water {i}", "quantity": quantity}).json()
        assert _line(added["item_id"])["quantity"] == f"{quantity.split()[0]}-pack"
