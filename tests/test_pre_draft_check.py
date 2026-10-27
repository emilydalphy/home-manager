"""
The two steps before the draft (Loop Board A4 + B1, 2026-10-26, branch
inventory/pre-draft-check):

  A4 "Quick check: still have these?" — at most five kitchen rows Pomona is
     least sure of (tools.inventory_check_candidates, GET/POST
     /api/inventory/check); tapped = gone (row removed), untapped =
     confirmed (inventory_items.confirmed_on).
  B1 "First, what's home" — use_up.candidates as chips, all on; the ones
     switched off reach week_intake.use_up_off on the drafting save.

Every test says whether it is a CATCH (red on main — the thing did not
exist) or a GUARD.
"""
from __future__ import annotations

import datetime
import json
import shutil

import pytest

import nodeharness
from conftest import household_today
from test_same_as_last_week import PAGE, _extract

from app import tools
from app.db import get_conn
from app.tools import inventory_check

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the page's own functions")


def _row(item, *, bought_days_ago=20, location="fridge", category="produce", quantity="1",
         exp_days=None, source="grocery_checkoff", confirmed_days_ago=None):
    today = household_today()
    stamp = (today - datetime.timedelta(days=bought_days_ago)).isoformat() + " 12:00:00"
    exp = None if exp_days is None else (today + datetime.timedelta(days=exp_days)).isoformat()
    confirmed = None if confirmed_days_ago is None else (today - datetime.timedelta(days=confirmed_days_ago)).isoformat()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location, "
        "created_at, updated_at, confirmed_on) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item, quantity, source, exp, category, location, stamp, stamp, confirmed),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _recipe(*ingredients):
    conn = get_conn()
    conn.execute(
        "INSERT INTO recipes (household_id, name, ingredients_json) VALUES (1, 'Test dish', ?)",
        (json.dumps([{"item": i, "qty": "1"} for i in ingredients]),),
    )
    conn.commit()
    conn.close()


def _exists(item_id):
    conn = get_conn()
    row = conn.execute("SELECT confirmed_on FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return row


# ---------- the list ----------

def test_never_more_than_five_least_sure_first():
    """CATCH — there was no check list at all."""
    names = ["Hummus", "Apples", "Crackers", "Grapes", "Cheddar", "Salsa", "Pickles", "Olives"]
    ids = [_row(name, bought_days_ago=10 + n) for n, name in enumerate(names)]
    items = tools.inventory_check_candidates()
    assert len(items) == 5
    # Longest since bought first.
    assert [c["id"] for c in items] == list(reversed(ids))[:5]


def test_staples_and_spices_are_never_asked_about():
    """CATCH — the check keeps off staples and spices."""
    conn = get_conn()
    conn.execute("INSERT INTO staples (household_id, item, next_due_at) VALUES (1, 'Milk', '2099-01-01')")
    conn.commit()
    conn.close()
    _row("Milk", category="dairy", bought_days_ago=40)
    _row("Smoked paprika", location="pantry", category="pantry", bought_days_ago=90)
    keep = _row("Hummus", category="dairy", bought_days_ago=12)
    assert [c["id"] for c in tools.inventory_check_candidates()] == [keep]


def test_recent_rows_and_recently_confirmed_rows_are_not_asked_about():
    """CATCH — Pomona is sure of last weekend's shop and of what was just confirmed."""
    _row("Yesterday's bread", bought_days_ago=1)
    _row("Confirmed cheddar", category="dairy", bought_days_ago=30, confirmed_days_ago=3)
    _row("Freezer peas", location="freezer", category="frozen", bought_days_ago=20)
    old_confirm = _row("Old confirm eggs", category="dairy", bought_days_ago=40, confirmed_days_ago=20)
    assert [c["id"] for c in tools.inventory_check_candidates()] == [old_confirm]


def test_past_date_first_then_no_recipe_things_ahead_of_recipe_things():
    """CATCH — a row past its date is asked first; a snack no recipe uses
    outranks a recipe ingredient bought the same day."""
    _recipe("jasmine rice", "baby spinach")
    rice = _row("Rice", location="pantry", category="pantry", bought_days_ago=15)
    apples = _row("Apples", bought_days_ago=15)
    yogurt = _row("Greek yogurt", category="dairy", bought_days_ago=8, exp_days=-1)
    items = tools.inventory_check_candidates()
    assert [c["id"] for c in items] == [yogurt, apples, rice]
    assert items[0]["reason"] == "past_date"
    assert items[1]["recipe_uses"] is False and items[2]["recipe_uses"] is True


def test_chip_label_uses_amount_words_and_never_invents_a_number():
    """CATCH — "Half a bag of rice", and a numeric amount is left off."""
    assert inventory_check.label_for("Rice", "half a bag") == "Half a bag of rice"
    assert inventory_check.label_for("Eggs", "6") == "Eggs"
    assert inventory_check.label_for("Cheddar", "200 g") == "Cheddar"
    assert inventory_check.label_for("Cheddar", "") == "Cheddar"
    assert inventory_check.label_for("Milk", "a little") == "Milk"


def test_location_narrows_it_for_the_kitchen_freezer_segment():
    """CATCH — C4 asks the same function for the freezer only."""
    _row("Hummus", category="dairy", bought_days_ago=20)
    beef = _row("Ground beef", location="freezer", category="meat/seafood", bought_days_ago=60)
    assert [c["id"] for c in tools.inventory_check_candidates("freezer")] == [beef]


def test_empty_kitchen_means_nothing_to_ask(signed_in):
    """CATCH — the route exists and answers an empty list, which skips the step."""
    client = signed_in
    res = client.get("/api/inventory/check")
    assert res.status_code == 200
    assert res.json() == {"items": []}


# ---------- the answer ----------

def test_gone_removes_the_row_and_untapped_ones_are_confirmed(signed_in):
    """CATCH — Next takes the tapped ones out and stamps the rest."""
    hummus = _row("Hummus", category="dairy", bought_days_ago=20)
    cheddar = _row("Cheddar", category="dairy", bought_days_ago=25)
    client = signed_in
    res = client.post("/api/inventory/check", json={"gone": [hummus], "confirmed": [cheddar, 99999]})
    assert res.status_code == 200
    body = res.json()
    assert body["removed"] == [{"id": hummus, "item": "Hummus"}]
    assert body["confirmed"] == [cheddar]
    assert _exists(hummus) is None
    assert _exists(cheddar)["confirmed_on"] == household_today().isoformat()
    # Answered once, it isn't asked again — wherever the next check is.
    assert client.get("/api/inventory/check").json() == {"items": []}


def test_an_answer_for_a_row_already_gone_is_not_an_error():
    """GUARD — answering in either place answers both."""
    hummus = _row("Hummus", category="dairy", bought_days_ago=20)
    tools.answer_inventory_check(gone=[hummus])
    assert tools.answer_inventory_check(gone=[hummus], confirmed=[hummus]) == {"removed": [], "confirmed": []}


# ---------- B1: switched off reaches the intake ----------

def test_switched_off_reaches_week_intake_on_the_drafting_save(signed_in):
    """CATCH (against main: no use_up_off on the intake) — what the page
    sends on Plan the week is what B2's generation reads."""
    today = household_today()
    spinach = _row("Baby spinach", bought_days_ago=2, exp_days=2)
    feta = _row("Feta", category="dairy", bought_days_ago=2, exp_days=3)
    week = (today + datetime.timedelta(days=1)).isoformat()
    client = signed_in
    offered = client.get(f"/api/week/{week}/use-up").json()
    assert {c["id"] for c in offered["items"]} >= {spinach, feta}
    res = client.post(f"/api/week/{week}/intake", json={"use_up_off": [feta], "day_count": 7})
    assert res.status_code == 200, res.text
    intake = tools.get_week_intake(week)
    assert intake["use_up_off"] == [feta]
    assert [c["id"] for c in tools.use_up_selected(intake, week, 7)] == [spinach]


# ---------- the page ----------

def _page_harness(check_items, use_up):
    """preDraftStep with the page's DOM and calls stubbed: which step shows."""
    fns = "\n".join("async " + _extract(n) if n in ("openInventoryCheck", "answerInventoryCheck", "openUseUp", "preDraftStep")
                    else _extract(n) for n in ("openInventoryCheck", "answerInventoryCheck", "openUseUp", "preDraftStep"))
    return """
var weekStart = '2099-01-05', dayCount = 7, preStep = '', shown = [], posted = [];
var preCheck = { items: [], gone: [] }, preUseUp = { shown: false, items: [], off: [] };
var PRE_COPY = { saveFailed: 'x' };
function renderCheckChips() {} function renderUseUpChips() {}
function showPreSection(w) { preStep = w; shown.push(w); }
function alert() {}
var Api = { json: async function (path, opts) {
  if (opts && opts.method === 'POST') { posted.push(opts.body); return {}; }
  if (path.indexOf('/api/inventory/check') === 0) return { items: %s };
  return %s;
} };
%s
(async function () {
  var first = await preDraftStep();
  var second = first ? await preDraftStep() : null;
  console.log(JSON.stringify({ first: first, second: second, shown: shown, posted: posted, useUpShown: preUseUp.shown }));
})();
""" % (json.dumps(check_items), json.dumps(use_up), fns)


@_needs_node
def test_page_skips_both_steps_when_nothing_to_ask():
    """CATCH — with nothing unsure and nothing going off, Draft my week drafts as today."""
    out = json.loads(nodeharness.run_node(_page_harness([], {"items": [], "off": []})).stdout.strip().splitlines()[-1])
    assert out == {"first": False, "second": None, "shown": [], "posted": [], "useUpShown": False}


@_needs_node
def test_page_check_then_use_up_and_next_posts_gone_and_confirmed():
    """CATCH — check first, Next answers it, then Use these up."""
    items = [{"id": 1, "label": "Hummus"}, {"id": 2, "label": "Cheddar"}]
    use = {"items": [{"id": 7, "label": "Baby spinach · Thu"}], "off": []}
    script = _page_harness(items, use).replace("var first = await preDraftStep();",
                                                "var first = await preDraftStep(); preCheck.gone = [1];")
    out = json.loads(nodeharness.run_node(script).stdout.strip().splitlines()[-1])
    assert out["first"] is True and out["second"] is True
    assert out["shown"] == ["check", "useup"]
    assert out["posted"] == [{"gone": [1], "confirmed": [2]}]
    assert out["useUpShown"] is True


def test_page_sends_use_up_off_only_when_the_step_showed():
    """CATCH — the drafting save carries the whole off list from B1."""
    assert "if (preUseUp.shown) payload.use_up_off = preUseUp.off.slice();" in PAGE
    assert "if (await preDraftStep()) return;" in PAGE
    for words in ("Quick check: still have these?", "Tap anything that&rsquo;s gone.",
                  "what&rsquo;s home", "I&rsquo;ll build the week around these.", "Use these up",
                  "Tap any you&rsquo;d rather not use.",
                  "From your last shop and what you&rsquo;ve cooked since. Something wrong? Tap it."):
        assert words in PAGE, words
    assert "plan: 'Plan the week'" in PAGE and "next: 'Next'" in PAGE and "gone: ' · gone'" in PAGE
