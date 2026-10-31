"""
"As a household, an 'Add 12 eggs' that's sent twice (a retry after 'couldn't
save') puts 12 eggs in the kitchen — not 24" (2026-10-31, branch
overnight/kitchen-add-once-1031).

static/kitchen-add.js names each confirm with a client_id and re-sends it on
a retap; /api/whats-home/add answers a repeat with the first answer and adds
nothing (app/grocery_add_once.py, table kitchen_add_sends). No id adds as
before.
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

import nodeharness
from app import grocery_add_once, tools
from app.db import get_conn

KA_JS_PATH = Path(__file__).resolve().parent.parent / "static" / "kitchen-add.js"


def _eggs():
    conn = get_conn()
    try:
        return [(r["item"], r["quantity"]) for r in conn.execute(
            "SELECT item, quantity FROM inventory_items WHERE LOWER(item) = 'eggs' ORDER BY id")]
    finally:
        conn.close()


def _add(client, **extra):
    body = {"kind": "type", "items": [{"item": "Eggs", "quantity": "12", "location": "fridge"}], **extra}
    return client.post("/api/whats-home/add", json=body)


def test_the_same_add_sent_twice_changes_the_kitchen_once_and_answers_alike(signed_in):
    first = _add(signed_in, client_id="k-1")
    again = _add(signed_in, client_id="k-1")
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert _eggs() == [("Eggs", "12")]


def test_a_new_add_with_a_new_id_still_adds(signed_in):
    _add(signed_in, client_id="k-1")
    _add(signed_in, client_id="k-2")
    assert _eggs() == [("Eggs", "24")]


def test_an_add_with_no_id_behaves_as_before(signed_in):
    _add(signed_in)
    _add(signed_in)
    assert _eggs() == [("Eggs", "24")]


def test_a_twin_arriving_while_the_first_is_adding_waits_for_its_answer(signed_in, monkeypatch):
    # The second send lands while the first is mid-write: it must not add,
    # and it must answer 200 with the first's answer, not a 409 the sheet
    # would show as "Couldn't save" again.
    started, release = threading.Event(), threading.Event()
    real = tools.add_kitchen_items

    def slow(items, kind):
        started.set()
        release.wait(5)
        return real(items, kind)

    monkeypatch.setattr(tools, "add_kitchen_items", slow)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("first", _add(signed_in, client_id="k-c")))
    t.start()
    assert started.wait(5)
    threading.Timer(0.5, release.set).start()
    again = _add(signed_in, client_id="k-c")
    t.join(10)
    assert out["first"].status_code == again.status_code == 200
    assert out["first"].json() == again.json()
    assert _eggs() == [("Eggs", "12")]


def test_a_failed_add_lets_the_same_id_try_again(signed_in, monkeypatch):
    real = tools.add_kitchen_items
    monkeypatch.setattr(tools, "add_kitchen_items", lambda items, kind: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert _add(signed_in, client_id="k-f").status_code == 500
    monkeypatch.setattr(tools, "add_kitchen_items", real)
    assert _add(signed_in, client_id="k-f").status_code == 200
    assert _eggs() == [("Eggs", "12")]


def test_the_table_name_is_checked():
    with pytest.raises(ValueError):
        grocery_add_once.add_once("x", lambda: {}, table="inventory_items")


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the sheet's own id rule")
def test_the_sheet_reuses_its_id_for_the_same_rows_and_makes_a_new_one_otherwise():
    res = nodeharness.run_node(
        f"const k = require({str(KA_JS_PATH)!r});"
        "const s = {}; const eggs = [{item: 'Eggs', location: 'fridge'}];"
        "const a = k.sendIdFor(s, 'type', eggs);"
        "const retry = k.sendIdFor(s, 'type', [{item: 'Eggs', location: 'fridge'}]);"
        "const other = k.sendIdFor(s, 'type', [{item: 'Milk', location: 'fridge'}]);"
        "s.send = null; const later = k.sendIdFor(s, 'type', eggs);"
        "console.log(JSON.stringify({a, retry, other, later}));",
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    ids = json.loads(res.stdout)
    assert ids["a"] and ids["a"] == ids["retry"]
    assert ids["other"] != ids["a"]
    assert ids["later"] not in (ids["a"], ids["other"])
