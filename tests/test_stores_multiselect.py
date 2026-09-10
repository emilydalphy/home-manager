"""
Picking your shops is a question you finish, not a tap that ends it
(Emily, 2026-09-09, testing onboarding as a new household).

    "When it brings me to the grocery option for the list, it's good that it
    takes me to the option to select them, but once I click one it brings me
    to a different screen that doesn't have anything. I should be able to
    click all the shops that I want to include in it."

Root cause: the card's own gate was "this household has named no shop and
has not declined the question" (groStoresPromptShouldShow in static/shell.js).
Saving the first shop made the first half false, so the question closed
itself on the first tap — one shop was the most anyone could ever name — and
what replaced it was LIST with nothing tagged to a store yet. There was never
a second screen; the "different screen that doesn't have anything" was the
Grocery list with its stops still empty.

The fix is three things: the chips toggle, a storesPromptOpen flag keeps the
card up while it is being answered, and one button at the foot of it is the
only way out.

Most of this runs shell.js's own functions under node against a small stub
rather than reading the source for a marker — the bug was a piece of
behaviour (a gate going false under its own answer), which is exactly what a
source-marker test cannot see. Source assertions are used only for the
event wiring, which has no pure function to call.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import tools


REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


# --- the node harness -----------------------------------------------------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _grocery_block() -> str:
    """Everything from the Grocery region's icons down to its handlers — the
    renderers and their state, which is all these tests call. The handlers
    below that line are asserted from source instead."""
    start = SHELL_JS.index("  var GRO_ICONS = {")
    end = SHELL_JS.index("  // ---------- Actions ----------", start)
    return SHELL_JS[start:end]


# Small enough to read in one screen. Three things stand in for the browser:
# escapeHtml (the shell's own, defined far above the slice), fetch (recorded,
# always fine — the write path itself is covered over HTTP further down), and
# an empty panel registry so renderGrocery finds no panel and returns without
# touching a DOM.
_STUB = """
function escapeHtml(s){return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const POSTS = [];
function fetch(url, opts) {
  POSTS.push({ url: url, body: JSON.parse((opts && opts.body) || '{}') });
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
}
const panels = {};
function showToast() {}
"""

# Five things needing a shop, none of them tagged to one — a household's list
# the morning after its first week is approved, which is where this card
# appears.
_LIST = """
const data = { stores: { Unassigned: { sections: [{ section: 'other', items: [
  { id: 1, item: 'Oat milk', quantity: '1', store: '' },
  { id: 2, item: 'Lemons', quantity: '1', store: '' },
  { id: 3, item: 'Chicken thighs', quantity: '1', store: '' },
  { id: 4, item: 'Rice', quantity: '1', store: '' },
  { id: 5, item: 'Spinach', quantity: '1', store: '' }
] }], purchased: [], inCart: [] } } };
groceryState.data = data;
function pickedChips() {
  const out = [], re = /data-store="([^"]+)" aria-pressed="true"/g, html = groStoresPromptHtml();
  let m; while ((m = re.exec(html)) !== null) out.push(m[1]);
  return out;
}
function countLine() { return /gro-stores-prompt-count">([^<]*)</.exec(groStoresPromptHtml())[1]; }
function doneLabel() { return /stores-prompt-done">([^<]*)</.exec(groStoresPromptHtml())[1]; }
"""


def _node(body: str):
    script = _STUB + _grocery_block() + _LIST + body
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the question survives being answered ------------------------------

@_needs_node
def test_the_question_is_due_for_a_household_that_has_never_named_a_shop():
    assert _node("console.log(JSON.stringify(groStoresPromptShouldShow()));") is True


@_needs_node
def test_picking_one_shop_does_not_end_the_question():
    """The bug, in one line: this used to go false the moment the first shop
    was saved, and the card the household was still answering vanished."""
    out = _node("""
groceryState.storesPromptOpen = true;
groToggleUsualStore('Costco').then(function () {
  console.log(JSON.stringify({
    stillAsking: groStoresPromptShouldShow(),
    picked: groceryState.usualStores
  }));
});
""")
    assert out["stillAsking"] is True
    assert out["picked"] == ["Costco"]


@_needs_node
def test_a_household_can_pick_every_shop_it_uses():
    out = _node("""
groceryState.storesPromptOpen = true;
groToggleUsualStore('Costco')
  .then(function () { return groToggleUsualStore('No Frills'); })
  .then(function () { return groToggleUsualStore('Metro'); })
  .then(function () {
    console.log(JSON.stringify({
      picked: pickedChips(), stillAsking: groStoresPromptShouldShow()
    }));
  });
""")
    assert out["picked"] == ["Costco", "No Frills", "Metro"]
    assert out["stillAsking"] is True


@_needs_node
def test_tapping_a_picked_shop_again_takes_it_back_off():
    out = _node("""
groceryState.storesPromptOpen = true;
groToggleUsualStore('Costco')
  .then(function () { return groToggleUsualStore('Metro'); })
  .then(function () { return groToggleUsualStore('Costco'); })
  .then(function () { console.log(JSON.stringify(pickedChips())); });
""")
    assert out == ["Metro"]


@_needs_node
def test_un_picking_sends_the_whole_shorter_list_not_a_removal():
    """usual_stores is a set on the server, written whole — so taking a shop
    back out has to be the shorter list, not an append of anything."""
    out = _node("""
groceryState.storesPromptOpen = true;
groToggleUsualStore('Costco')
  .then(function () { return groToggleUsualStore('Metro'); })
  .then(function () { return groToggleUsualStore('Costco'); })
  .then(function () { console.log(JSON.stringify(POSTS)); });
""")
    assert [p["url"] for p in out] == ["/api/memory/edit"] * 3
    assert [p["body"]["value"] for p in out] == [
        ["Costco"], ["Costco", "Metro"], ["Metro"],
    ]
    assert all(p["body"]["field"] == "usual_stores" for p in out)


# --- 2. what the household can see ----------------------------------------

@_needs_node
def test_a_picked_shop_looks_picked():
    out = _node("""
groceryState.usualStores = ['No Frills'];
const html = groStoresPromptHtml();
console.log(JSON.stringify({
  on: /class="gro-pill gro-pill-on" data-gro="stores-prompt-pick" data-store="No Frills" aria-pressed="true"/.test(html),
  off: /class="gro-pill" data-gro="stores-prompt-pick" data-store="Metro" aria-pressed="false"/.test(html)
}));
""")
    assert out["on"] is True
    assert out["off"] is True


@_needs_node
def test_the_card_says_how_many_are_picked():
    out = _node("""
const seen = [countLine()];
groceryState.usualStores = ['Costco'];
seen.push(countLine());
groceryState.usualStores = ['Costco', 'Metro', 'Farm Boy'];
seen.push(countLine());
console.log(JSON.stringify(seen));
""")
    assert out == ["No shops picked yet", "1 shop picked", "3 shops picked"]


@_needs_node
def test_a_shop_the_household_typed_in_becomes_a_chip_it_can_un_pick():
    """"We shop somewhere else" used to be write-only — the typed name went
    into the answer but never into the row of chips, so a typo needed the
    Kitchen sheet to undo."""
    out = _node("""
groceryState.storesPromptOpen = true;
groAddUsualStore('Kim’s Market').then(function () {
  console.log(JSON.stringify({
    picked: pickedChips(),
    stillAsking: groStoresPromptShouldShow(),
    count: countLine()
  }));
});
""")
    assert out["picked"] == ["Kim’s Market"]
    assert out["stillAsking"] is True
    assert out["count"] == "1 shop picked"


@_needs_node
def test_typing_a_shop_that_is_already_picked_does_not_un_pick_it():
    out = _node("""
groceryState.storesPromptOpen = true;
groToggleUsualStore('Costco')
  .then(function () { return groAddUsualStore('Costco'); })
  .then(function () { console.log(JSON.stringify({ picked: pickedChips(), posts: POSTS.length })); });
""")
    assert out["picked"] == ["Costco"]
    assert out["posts"] == 1


# --- 3. moving on is a deliberate act -------------------------------------

@_needs_node
def test_the_only_way_out_of_the_card_is_its_own_button():
    """Every other control on the card writes an answer and stays. If a
    second thing on it ever closes the question, this is the test to read."""
    out = _node("""
const html = groStoresPromptHtml();
const verbs = [], re = /data-gro="([^"]+)"/g;
let m; while ((m = re.exec(html)) !== null) if (verbs.indexOf(m[1]) === -1) verbs.push(m[1]);
console.log(JSON.stringify(verbs));
""")
    assert out == ["stores-prompt-pick", "stores-prompt-add", "stores-prompt-done"]


@_needs_node
def test_the_button_says_what_the_answer_is():
    out = _node("""
const seen = [doneLabel()];
groceryState.usualStores = ['Costco', 'Metro'];
seen.push(doneLabel());
console.log(JSON.stringify(seen));
""")
    assert out == ["One list is fine", "That&rsquo;s where we shop"]


@_needs_node
def test_the_answer_recorded_closes_the_question_for_good():
    out = _node("""
groceryState.usualStores = ['Costco'];
groceryState.storesPromptOpen = true;
groceryState.storesPromptDismissed = true;
groceryState.storesPromptOpen = false;
console.log(JSON.stringify(groStoresPromptShouldShow()));
""")
    assert out is False


@_needs_node
def test_no_shops_is_a_real_answer_and_also_closes_it():
    out = _node("""
groceryState.storesPromptDismissed = true;
console.log(JSON.stringify({
  asking: groStoresPromptShouldShow(), picked: groceryState.usualStores
}));
""")
    assert out["asking"] is False
    assert out["picked"] == []


@_needs_node
def test_the_card_owns_the_screens_one_apricot_while_it_is_up():
    """Rule 5. LIST returns the card INSTEAD of its stops, so "Start the
    trip" in the foot would be a second apricot pointing at stores that
    aren't on the screen."""
    out = _node("""
data.stores['Costco'] = { sections: [{ section: 'other', items: [
  { id: 9, item: 'Eggs', quantity: '1', store: 'Costco' } ] }], purchased: [], inCart: [] };
const whileAsking = groFootHtml(data, 'list');
groceryState.storesPromptDismissed = true;
const afterAnswering = groFootHtml(data, 'list');
console.log(JSON.stringify({
  whileAsking: whileAsking.indexOf('start-trip') !== -1,
  afterAnswering: afterAnswering.indexOf('start-trip') !== -1
}));
""")
    assert out["whileAsking"] is False
    assert out["afterAnswering"] is True


# --- 4. the shops picked here are the shops offered later -----------------

@_needs_node
def test_sorting_offers_exactly_the_shops_the_household_picked():
    out = _node("""
groceryState.storesPromptOpen = true;
groToggleUsualStore('Costco')
  .then(function () { return groToggleUsualStore('Metro'); })
  .then(function () { return groAddUsualStore('Kim’s Market'); })
  .then(function () { return groToggleUsualStore('Metro'); })
  .then(function () {
    const sort = groSortHtml(data), pills = [], re = /data-gro="assign"[^>]*data-store="([^"]*)"/g;
    let m; while ((m = re.exec(sort)) !== null) pills.push(m[1]);
    console.log(JSON.stringify({ offered: groPillStores(data), sortPills: pills }));
  });
""")
    assert out["offered"] == ["Costco", "Kim’s Market"]
    # The sort card's own chips, in order, ending on the store-less "Any".
    assert out["sortPills"] == ["Costco", "Kim’s Market", ""]


# --- 5. the wiring, which has no function to call -------------------------

def _case_body(verb: str) -> str:
    start = SHELL_JS.index("      case '%s':" % verb)
    end = SHELL_JS.index("        return;", start)
    return SHELL_JS[start:end]


def test_tapping_a_shop_toggles_it_and_holds_the_card_open():
    body = _case_body("stores-prompt-pick")
    assert "groToggleUsualStore(el.dataset.store)" in body
    assert "groceryState.storesPromptOpen = true;" in body


def test_adding_a_typed_shop_holds_the_card_open_too():
    body = SHELL_JS[SHELL_JS.index("      case 'stores-prompt-add': {"):]
    body = body[: body.index("      case 'stores-prompt-done':")]
    assert "groceryState.storesPromptOpen = true;" in body
    assert "groAddUsualStore(typedStore)" in body
    # And it never empties the field itself: a saved name comes back blank
    # with the re-rendered card, and a FAILED save has to leave the typing
    # where the household can try it again.
    assert "storesPromptInput.value = ''" not in body


def test_the_button_is_the_one_thing_that_records_the_question_as_answered():
    body = _case_body("stores-prompt-done")
    assert "/api/memory/stores-prompt-dismiss" in body
    assert "groceryState.storesPromptDismissed = true;" in body
    assert "groceryState.storesPromptOpen = false;" in body
    # And nothing else in the file writes that dismissal.
    assert SHELL_JS.count("'/api/memory/stores-prompt-dismiss'") == 1


def test_a_picked_chip_is_coloured_from_tokens_only():
    """Design system rule 9 — a literal hex outside theme.css is a review
    failure. Celadon rather than apricot is rule 5's half of it: a picked
    shop is a settled fact, not nine more primaries."""
    rule = re.search(r"^\.gro-pill-on \{([^}]*)\}", SHELL_CSS, re.M)
    assert rule, ".gro-pill-on is what says a shop is picked"
    assert "#" not in rule.group(1)
    for token in ("--celadon-tint", "--celadon-label", "--celadon-edge"):
        assert token in rule.group(1)


# --- 6. the write path, over HTTP -----------------------------------------

def test_the_shops_field_can_shrink_as_well_as_grow(signed_in):
    """Un-picking is the shorter list arriving on the same field. Nothing in
    the app had ever sent one before, so this is worth pinning."""
    signed_in.post("/api/memory/edit",
                   json={"field": "usual_stores", "value": ["Costco", "Metro", "Farm Boy"]})
    assert signed_in.get("/api/memory").json()["usual_stores"] == ["Costco", "Metro", "Farm Boy"]

    signed_in.post("/api/memory/edit",
                   json={"field": "usual_stores", "value": ["Costco", "Farm Boy"]})
    assert signed_in.get("/api/memory").json()["usual_stores"] == ["Costco", "Farm Boy"]

    signed_in.post("/api/memory/edit", json={"field": "usual_stores", "value": []})
    assert signed_in.get("/api/memory").json()["usual_stores"] == []


def test_answering_the_question_keeps_the_shops_and_stops_the_asking(signed_in):
    signed_in.post("/api/memory/edit",
                   json={"field": "usual_stores", "value": ["Costco", "No Frills"]})
    signed_in.post("/api/memory/stores-prompt-dismiss")
    memory = signed_in.get("/api/memory").json()
    assert memory["usual_stores"] == ["Costco", "No Frills"]
    assert memory["stores_prompt_dismissed"] is True


def test_the_shops_reach_the_thing_that_sorts_the_list():
    """The card's whole promise. set_grocery_item_store takes any string, so
    what matters is that the names the household picked are the names the
    household is offered — which is what get_household_memory hands back."""
    tools.add_usual_stores(["Costco", "No Frills"])
    added = tools.add_grocery_item("Oat milk", quantity="1")
    assert tools.get_household_memory()["usual_stores"] == ["Costco", "No Frills"]
    tools.set_grocery_item_store(added["item_id"], "No Frills")
    by_store = {s["store"] for s in tools.get_grocery_list_by_store()["stores"]}
    assert "No Frills" in by_store
