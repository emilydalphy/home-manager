"""
Sorting forty things shouldn't take forty screens (Emily, 2026-09-09) —
and since 2026-09-18 sorting has ONE way: "Sort them all" (every unsorted
thing on one screen, one tap each). The chooser that stood in front of it
("Sort them all on one screen" / "Or one at a time" / "Sort them later"),
the one-at-a-time queue behind it and the bulk "Put all 40 at Loblaws"
went that day; tests/test_sort_all_rows_leave.py covers the screen itself.

What this file still covers, because the sorting step touched it and it
is still true:

  * an "Any" answer PERSISTS (grocery_items.store_decided). It used to
    live in a page-view map, so a reload put every skipped item straight
    back into the queue — the known limit recorded in CLAUDE.md.
  * a household with one shop, or none, never sees a sorting step at all.
  * the store-bulk route (one transaction, bounded, never remembering a
    shop per item) — SORT ALL's undo still goes through it, one row at a
    time, because it restores a row EXACTLY (store and never-answered).
  * a row answered "Anywhere" is on the list, on the "Anywhere" card, with
    its ⋯; an unanswered one sits on the same card (2026-09-18 — until
    then it had a "Not sorted yet" card of its own) and is still in the
    queue.

Most of this runs shell.js's own functions under node (tests/shop_harness);
the persistence half is driven over real HTTP against the real routes.
"""
from __future__ import annotations

import pytest

from app import tools
from shop_harness import SHELL_CSS, SHELL_JS, needs_node, run

_needs_node = needs_node
_node = run


# --- 1. the one way in ------------------------------------------------------


@_needs_node
def test_the_sort_row_opens_sort_them_all_whatever_the_count():
    """Three things or forty: the row on the list opens the one screen.
    Until 2026-09-18 a handful went to the queue and six or more to the
    chooser (GRO_FAST_SORT_MIN)."""
    out = _node("""
setUp(40);
click({ gro: 'goto-sort' });
const many = groceryState.step;
setUp(3);
click({ gro: 'goto-sort' });
console.log(JSON.stringify({ many: many, few: groceryState.step }));
""")
    assert out == {"many": "sortall", "few": "sortall"}


def test_the_chooser_the_queue_and_the_bulk_button_are_gone():
    for gone in (
        "function groSortHowHtml(", "function groSortHtml(", "GRO_FAST_SORT_MIN", "GRO_SORT_STEPS",
        'data-gro="goto-sort-one"', 'data-gro="goto-sortall"', 'data-gro="sort-all-at"',
        "gro-howrow-title", "function groBulkAssign(",
        "function groSortAssign(", 'data-gro="assign"', 'data-gro="sort-later"',
    ):
        assert gone not in SHELL_JS, f"{gone!r} should have gone with the chooser (2026-09-18)"
    for gone in (".gro-howcard", ".gro-howrow", ".gro-sortcard", ".gro-sort-item", ".gro-sort-progress", ".gro-sort-later"):
        assert gone not in SHELL_CSS, f"{gone} is dead CSS now"


@_needs_node
def test_the_crumb_is_sort_them_later():
    """"Sort them later" is simply leaving: the crumb goes to the list, and
    the list never opens SORT ALL on its own."""
    out = _node("""
setUp(6);
click({ gro: 'goto-sort' });
const opened = groceryState.step;
click({ gro: 'step-back' });
console.log(JSON.stringify({ opened: opened, back: groceryState.step, head: groHeadFor(groceryState.data, 'sortall') }));
""")
    assert out["opened"] == "sortall"
    assert out["back"] == "list"
    assert out["head"] == {"back": "‹ Shop", "title": "Sort them all", "sub": "6 to sort"}


# --- 2. sort them all on one screen ---------------------------------------


@_needs_node
def test_the_one_screen_shows_every_unsorted_thing_with_a_shop_on_it():
    """Since 2026-09-13 ("a row leaves the moment I sort it") no chip starts
    lit: a lit chip that had not been written was the screen's lie, and
    every row now needs its one tap."""
    out = _node("""
setUp(40);
const html = groSortAllHtml(groceryState.data);
console.log(JSON.stringify({
  rows: (html.match(/gro-sortall-row/g) || []).length,
  firstPick: pickedChip(html, '1'),
  lastPick: pickedChip(html, '40'),
  hasAny: /data-store="" aria-pressed="false"/.test(html)
}));
""")
    assert out["rows"] == 40, "every unsorted thing, one row each"
    assert out["firstPick"] is None, "nothing starts lit — a tap is a write now"
    assert out["lastPick"] is None
    assert out["hasAny"] is True, "'Any' is still an answer on every row"


# --- 3. a household with one shop, or none --------------------------------


@_needs_node
def test_one_shop_means_no_sorting_step_at_all():
    """Sorting is a question with more than one answer, or it is not a
    question."""
    out = _node("""
setUp(40, [], ['Loblaws']);
console.log(JSON.stringify({
  canSort: groCanSort(groceryState.data),
  toSort: groUnsorted(groceryState.data).length,
  row: groSortRowHtml(groceryState.data)
}));
""")
    assert out == {"canSort": False, "toSort": 0, "row": ""}


@_needs_node
def test_no_shop_at_all_means_no_sorting_step_either():
    out = _node("""
setUp(40, [], []);
console.log(JSON.stringify(groUnsorted(groceryState.data).length));
""")
    assert out == 0


@_needs_node
def test_two_shops_still_leaves_the_untagged_things_to_be_sorted():
    out = _node("""
setUp(6);
console.log(JSON.stringify({
  toSort: groUnsorted(groceryState.data).length,
  row: groSortRowHtml(groceryState.data).indexOf('6 things to sort') !== -1
}));
""")
    assert out == {"toSort": 6, "row": True}


# --- 4. "Any" persists ----------------------------------------------------


def test_saying_no_particular_shop_is_remembered(signed_in):
    """The known limit CLAUDE.md recorded: "Any" was a page-view map, so a
    reload put the item straight back into the to-sort queue."""
    added = signed_in.post("/api/grocery-list/add", json={"item": "Batteries", "quantity": "1"}).json()
    item_id = added["item_id"]

    before = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert before["store_decided"] == 0, "nobody has answered yet"

    signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": ""})

    after = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert after["store"] == "", "still no particular shop"
    assert after["store_decided"] == 1, "but the question has been answered"


def test_the_answer_reaches_the_screen_that_has_to_read_it(signed_in):
    added = signed_in.post("/api/grocery-list/add", json={"item": "Foil", "quantity": "1"}).json()
    signed_in.post(f"/api/grocery-list/{added['item_id']}/store", json={"store": ""})

    payload = signed_in.get("/api/grocery-list/by-store?status=needed").json()
    rows = [
        it
        for s in payload["stores"]
        for sec in s["sections"]
        for it in sec["items"]
        if it["id"] == added["item_id"]
    ]
    assert rows and rows[0]["store_decided"] == 1


# --- 5. the bulk route, which the undo still rides ------------------------


def test_many_rows_are_assigned_in_one_request(signed_in):
    ids = [
        signed_in.post("/api/grocery-list/add", json={"item": name, "quantity": "1"}).json()["item_id"]
        for name in ("Leeks", "Barley", "Yoghurt")
    ]
    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": i, "store": "Loblaws"} for i in ids]},
    )
    assert res.status_code == 200
    assert res.json()["updated"] == 3
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    for i in ids:
        assert rows[i]["store"] == "Loblaws"
        assert rows[i]["store_decided"] == 1


def test_the_undo_puts_every_row_back_the_way_it_was(signed_in):
    """Not "everything to unsorted": the row that was already answered "Any"
    has to come back answered, and the one that was never asked has to come
    back unasked."""
    answered = signed_in.post("/api/grocery-list/add", json={"item": "Cling film", "quantity": "1"}).json()["item_id"]
    untouched = signed_in.post("/api/grocery-list/add", json={"item": "Paprika", "quantity": "1"}).json()["item_id"]
    signed_in.post(f"/api/grocery-list/{answered}/store", json={"store": ""})

    previous = [
        {"item_id": answered, "store": "", "decided": True},
        {"item_id": untouched, "store": "", "decided": False},
    ]
    signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": i, "store": "Costco"} for i in (answered, untouched)]},
    )
    signed_in.post("/api/grocery-list/store-bulk", json={"assignments": previous})

    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert (rows[answered]["store"], rows[answered]["store_decided"]) == ("", 1)
    assert (rows[untouched]["store"], rows[untouched]["store_decided"]) == ("", 0)


def test_a_bulk_assign_does_not_remember_a_shop_for_every_item(signed_in):
    """One request covering many rows must not become many remembered
    opinions about where each of those things is usually bought."""
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Tahini", "quantity": "1"}).json()["item_id"]
    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "Costco"}]},
    )
    assert res.status_code == 200 and res.json()["updated"] == 1, "the write really happened"
    prefs = {p["item"].lower(): p["store"] for p in tools.get_item_store_preferences()}
    assert "tahini" not in prefs
    # The single-row route, which IS a deliberate one-at-a-time choice, still
    # offers to remember it — that etiquette is untouched.
    assert signed_in.post(
        f"/api/grocery-list/{item_id}/store", json={"store": "Costco"}
    ).json()["needs_confirmation"] is True


def test_a_bulk_assign_that_fails_part_way_writes_nothing(signed_in, monkeypatch):
    """A failure on row 3 of 4 used to leave rows 1 and 2 written and return
    a 500 — a half-applied list, with the toast's Undo chip already spent
    on it."""
    from app.tools import stores as stores_mod

    ids = [
        signed_in.post("/api/grocery-list/add", json={"item": n, "quantity": "1"}).json()["item_id"]
        for n in ("Anchovies", "Bay leaves", "Cardamom", "Dill")
    ]
    real = stores_mod._stage_grocery_item_store
    calls = {"n": 0}

    def flaky(conn, item_id, store, remember, decided):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("connection dropped")
        return real(conn, item_id, store, remember, decided)

    monkeypatch.setattr(stores_mod, "_stage_grocery_item_store", flaky)

    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": i, "store": "Costco"} for i in ids]},
    )
    assert res.status_code == 500

    rows = {it["id"]: it for it in tools.list_grocery_list()}
    for i in ids:
        assert (rows[i]["store"], rows[i]["store_decided"]) == ("", 0), (
            "a failed batch must leave every row exactly as it was"
        )


def test_a_batch_bigger_than_a_grocery_list_is_refused(signed_in):
    """Unbounded, one connection per row, it was 5000 connections and 3.3s
    in one request. Now it is a 400 before anything is written."""
    item_id = signed_in.post(
        "/api/grocery-list/add", json={"item": "Sumac", "quantity": "1"}
    ).json()["item_id"]
    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "Costco"}] * 501},
    )
    assert res.status_code == 400
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert rows[item_id]["store"] == "", "and nothing was written on the way to refusing"


@_needs_node
def test_a_failed_undo_keeps_its_payload_so_there_is_an_again():
    """The payload was cleared on the tap, so a failed undo took the only
    record of the previous state with it — a row at a shop nobody chose, a
    toast saying "try again", and no again."""
    out = _node("""
groceryState.bulkUndo = [{ item_id: 1, store: '', decided: false }];
FAIL_ON = 1;
groRunBulkUndo();
settle(function () {
  console.log(JSON.stringify({
    payloadKept: groceryState.bulkUndo !== null,
    offeredAgain: lastToast().action
  }));
});
""")
    assert out["payloadKept"] is True, "a failed undo must stay undoable"
    assert out["offeredAgain"] == "Undo", "and the chip has to come back"


@_needs_node
def test_a_successful_undo_spends_its_payload_exactly_once():
    out = _node("""
groceryState.bulkUndo = [{ item_id: 1, store: '', decided: false }];
groRunBulkUndo();
settle(function () {
  const postsAfterUndo = POSTS.length;
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({
      cleared: groceryState.bulkUndo === null,
      secondTapWroteNothing: POSTS.length === postsAfterUndo
    }));
  });
});
""")
    assert out == {"cleared": True, "secondTapWroteNothing": True}


@_needs_node
def test_an_undo_that_works_on_the_second_try_replaces_the_failure_line():
    """The failure toast holds for its full window, so a retry that worked
    otherwise leaves "tap Undo to try again" sitting over a list that has
    already been put back."""
    out = _node("""
groceryState.bulkUndo = [{ item_id: 1, store: 'Costco', decided: true }];
FAIL_ON = 1;
groRunBulkUndo();
settle(function () {
  const afterFailure = lastToast();
  FAIL_ON = 0;
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({
      afterFailure: afterFailure,
      afterRetry: lastToast(),
      payloadSpent: groceryState.bulkUndo === null
    }));
  });
});
""")
    assert "try again" in out["afterFailure"]["msg"]
    assert out["afterFailure"]["action"] == "Undo", "a failure keeps the chip"
    assert "try again" not in out["afterRetry"]["msg"], "the retry replaces the failure line"
    assert out["afterRetry"]["action"] is None, "and offers nothing more to undo"
    assert out["payloadSpent"], "a successful undo spends its payload"


# --- 6. every writer of `store` maintains `store_decided` ----------------


def test_clearing_a_store_preference_re_opens_the_question(signed_in):
    """_apply_store_to_matching_rows is the second writer of the column, and
    it used to write only half of it: a row cleared in chat kept
    store_decided = 1 and was then permanently "answered" with no shop on
    it."""
    item_id = signed_in.post(
        "/api/grocery-list/add", json={"item": "Halloumi", "quantity": "1"}
    ).json()["item_id"]
    signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Costco"})
    assert [it for it in tools.list_grocery_list() if it["id"] == item_id][0]["store_decided"] == 1

    tools.set_item_store("Halloumi", "")

    row = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert row["store"] == ""
    assert row["store_decided"] == 0, "no opinion about the shop re-opens the question"


def test_applying_a_remembered_store_counts_as_answered(signed_in):
    item_id = signed_in.post(
        "/api/grocery-list/add", json={"item": "Fennel", "quantity": "1"}
    ).json()["item_id"]
    tools.set_item_store("Fennel", "Farm Boy")
    row = [it for it in tools.list_grocery_list() if it["id"] == item_id][0]
    assert (row["store"], row["store_decided"]) == ("Farm Boy", 1)


# --- 7. "Anywhere" is a place, not a disappearance ------------------------
# Once "Any" persisted, a row answered that way was on NO screen for a
# multi-shop household: out of the badge (it is answered), out of every
# store card (it has no store), and so out of reach of the row ⋯ that is
# the only way to change it.


@_needs_node
def test_a_row_answered_anywhere_is_still_on_the_list():
    out = _node("""
setUp(0, [{ store: 'Costco', items: [{ id: 1, item: 'Eggs', quantity: '1', store: 'Costco', status: 'needed' }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 2, item: 'Milk', quantity: '1', store: '', store_decided: 1, status: 'needed' }
];
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  showsMilk: html.indexOf('Milk') !== -1,
  showsEggs: html.indexOf('Eggs') !== -1,
  cards: cards(html),
  inTheQueue: groUnsorted(groceryState.data).length
}));
""")
    assert out["showsEggs"] is True
    assert out["showsMilk"] is True, "a row answered 'Any' must still be readable"
    assert out["cards"] == ["Costco", "Anywhere"]
    assert out["inTheQueue"] == 0, "and it must not be asked about again"


@_needs_node
def test_an_anywhere_row_can_still_be_changed_back_to_a_shop():
    """The ⋯ is the only control that can move a row, so the card is only
    worth having if its rows carry one."""
    out = _node("""
setUp(0, [{ store: 'Costco', items: [{ id: 1, item: 'Eggs', quantity: '1', store: 'Costco', status: 'needed' }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 2, item: 'Milk', quantity: '1', store: '', store_decided: 1, status: 'needed' }
];
groceryState.openRowId = '2';
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  hasRowMenuButton: html.indexOf('data-gro="row-menu" data-id="2"') !== -1,
  offersAShop: /data-gro="row-store" data-id="2" data-store="Costco"/.test(html)
}));
""")
    assert out["hasRowMenuButton"] is True
    assert out["offersAShop"] is True


@_needs_node
def test_a_one_shop_household_still_sees_its_list_exactly_once():
    """Its loose pile is inside its shop's card; a second 'Anywhere' card
    would print the same rows twice."""
    out = _node("""
setUp(3, [], ['Loblaws']);
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  thing1: (html.match(/Thing 1</g) || []).length,
  cards: cards(html),
  counts: counts(html)
}));
""")
    assert out["thing1"] == 1
    assert out["cards"] == ["Loblaws"]
    assert out["counts"] == ["0 of 3"]


@_needs_node
def test_rows_still_waiting_to_be_sorted_sit_on_the_anywhere_card_and_in_the_queue():
    """An unanswered row is on the list once, on the "Anywhere" card beside
    the "Any" ones (2026-09-18 — the "Not sorted yet" card folded into it),
    and it is still in the queue the sort row counts."""
    out = _node("""
setUp(0, [{ store: 'Costco', items: [{ id: 1, item: 'Eggs', quantity: '1', store: 'Costco', status: 'needed' }] }]);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 2, item: 'Milk', quantity: '1', store: '', store_decided: 0, status: 'needed' },
  { id: 3, item: 'Oats', quantity: '1', store: '', store_decided: 1, status: 'needed' }
];
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({
  milkRows: (html.match(/Milk</g) || []).length,
  cards: cards(html),
  counts: counts(html),
  notSortedYet: html.indexOf('Not sorted yet') !== -1,
  sortRow: html.indexOf('1 thing to sort') !== -1,
  inTheQueue: groUnsorted(groceryState.data).length
}));
""")
    assert out["milkRows"] == 1, "on the list exactly once"
    assert out["cards"] == ["Costco", "Anywhere"]
    assert out["counts"] == ["0 of 1", "0 of 2"]
    assert out["notSortedYet"] is False
    assert out["sortRow"] is True
    assert out["inTheQueue"] == 1, "and still to be asked about"


@_needs_node
def test_two_shops_with_nothing_tagged_have_only_the_anywhere_card():
    """A household with two shops and everything loose: no shop has a card
    about nothing — the "Anywhere" card is the truth of its list, and the
    band counts no store."""
    out = _node("""
setUp(0, [], ['Loblaws', 'Costco']);
groceryState.data.stores.Unassigned.sections[0].items = [
  { id: 1, item: 'Milk', quantity: '1', store: '', store_decided: 1, status: 'needed' },
  { id: 2, item: 'Foil', quantity: '1', store: '', store_decided: 1, status: 'needed' }
];
const html = groListHtml(groceryState.data);
console.log(JSON.stringify({ cards: cards(html), band: groBandLine(groceryState.data), dock: groDockHtml(groceryState.data, 'list') }));
""")
    assert out["cards"] == ["Anywhere"]
    assert out["band"] == "2 things."
    assert out["dock"] == ""
