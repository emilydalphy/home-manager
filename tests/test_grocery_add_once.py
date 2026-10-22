"""
"As a shopper in a store dead zone, adding '2 milk' once puts 2 on the list,
not 4 when the add is sent again" (2026-10-22, branch
overnight/rate-refresh-and-offline-once).

The add sheet names each add with a client_id before its first send; the
offline queue re-sends a lost-reply add under the same name. The route
answers a repeat with the first answer and adds nothing
(app/grocery_add_once.py). Ids are per household; no id behaves as before.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import grocery_add_once, households, tools
from app.db import get_conn


def _milk_lines():
    conn = get_conn()
    rows = conn.execute(
        "SELECT household_id, quantity FROM grocery_items WHERE LOWER(item) = 'milk'"
    ).fetchall()
    conn.close()
    return [(r["household_id"], r["quantity"]) for r in rows]


def _add(client, **extra):
    body = {"item": "Milk", "quantity": "2", "category": "dairy", **extra}
    return client.post("/api/grocery-list/add", json=body)


def test_the_same_add_sent_twice_changes_the_list_once_and_answers_alike(signed_in):
    first = _add(signed_in, client_id="abc-123")
    again = _add(signed_in, client_id="abc-123")
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert _milk_lines() == [(1, "2")]


def test_two_real_adds_with_different_ids_still_add_up(signed_in):
    _add(signed_in, client_id="one")
    _add(signed_in, client_id="two")
    assert _milk_lines() == [(1, "4")]


def test_an_add_with_no_id_behaves_as_before(signed_in):
    _add(signed_in)
    _add(signed_in)
    assert _milk_lines() == [(1, "4")]


def test_the_store_rides_in_the_remembered_answer(signed_in):
    first = _add(signed_in, client_id="s-1", store="Costco")
    again = _add(signed_in, client_id="s-1", store="Costco")
    assert again.json() == first.json()
    assert again.json()["store"] == "Costco"


def test_ids_are_scoped_to_the_household():
    other = households.create_household("The Beta Testers", "beta-tester-passphrase")

    def add():
        return tools.add_grocery_item("Milk", quantity="2", category="dairy")

    grocery_add_once.add_once("shared-id", add)
    with tools.use_household(other):
        grocery_add_once.add_once("shared-id", add)
    assert sorted(_milk_lines()) == [(1, "2"), (other, "2")]


def test_a_failed_add_leaves_no_receipt_so_the_retry_adds():
    def boom():
        raise RuntimeError("disk hiccup")

    with pytest.raises(RuntimeError):
        grocery_add_once.add_once("retry-me", boom)
    grocery_add_once.add_once(
        "retry-me", lambda: tools.add_grocery_item("Milk", quantity="2", category="dairy")
    )
    assert _milk_lines() == [(1, "2")]


# --- the phone: the queue carries the id it was given, every replay ---

MODULE = Path(__file__).resolve().parent.parent / "static" / "grocery-offline.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the browser code")
def test_the_queue_resends_an_add_under_the_id_it_was_made_with():
    script = f"""
      const M = require({json.dumps(str(MODULE))});
      const mem = {{}};
      const storage = {{ getItem: (k) => (k in mem ? mem[k] : null),
        setItem: (k, v) => {{ mem[k] = String(v); }}, removeItem: (k) => {{ delete mem[k]; }} }};
      const q = M.create({{ storage, householdId: 1 }});
      q.queueAdd({{ item: 'Milk', quantity: '2', category: 'dairy', client_id: 'from-the-sheet' }});
      q.queueAdd({{ item: 'Eggs', quantity: '12', category: 'dairy' }});
      const sent = [];
      let n = 0;
      // First attempt: the request went but the reply was lost (fetch threw).
      const post = (url, body) => {{ sent.push(body.client_id); n += 1;
        return n === 1 ? Promise.reject(new Error('lost')) : Promise.resolve({{ ok: true }}); }};
      q.replay(post).then(() => q.replay(post)).then(() => console.log(JSON.stringify(sent)));
    """
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    sent = json.loads(res.stdout.strip())
    assert sent[0] == sent[1] == "from-the-sheet"
    # An add queued with no id is given one, and it is a different one.
    assert len(sent) == 3 and sent[2] and sent[2] != "from-the-sheet"
