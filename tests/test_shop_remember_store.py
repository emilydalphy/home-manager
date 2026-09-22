"""
Adding or sorting an item once remembers its store (Loop Board
3e31f4c0-5231-81ca-a8ce-e7a348c1f570, 2026-09-21).

Every way a thing is put under a store remembers that store as the
item's usual — the add sheet's pick, a "Sort them all" chip, the row's ⋯
— at once and without a "Remember?" toast; next week's list places a
remembered item under its store on its own, so "Sort them all" only ever
shows things with no usual store. The undo of a sort takes the
remembered store back with the row; the row's ⋯ move says "Changes
saved · Put back", and Put back does the same. The one-week-only move
(remember: false on the store route) is untouched: it moves the row and
leaves the usual alone.

HTTP tests run the real routes on the test DB; the screen's half runs
under node against shell.js's own handler (tests/shop_harness).
"""
from __future__ import annotations

from app import tools
from app.db import get_conn
from app.tools._shared import household_id
from shop_harness import needs_node, run


def _plan_id() -> int:
    """A plan row for a plan's own add to hang off (grocery_items.
    source_weekly_plan_id is a foreign key)."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, content_start_date, day_count) "
        "VALUES (?, '2026-09-28', 'approved', '2026-09-28', 7)",
        (household_id(),),
    )
    conn.commit()
    plan_id = cur.lastrowid
    conn.close()
    return plan_id


def _by_store() -> dict[str, list[str]]:
    data = tools.get_grocery_list_by_store()
    return {s["store"]: [it["item"] for sec in s["sections"] for it in sec["items"]] for s in data["stores"]}


# --- 1. the routes ----------------------------------------------------------


def test_a_sorted_item_is_remembered_and_next_weeks_list_is_pre_sorted(signed_in):
    tools.edit_preference("usual_stores", ["Costco", "Loblaws"])
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Cilantro", "quantity": "1 bunch"}).json()["item_id"]
    assert _by_store().get("Unassigned") == ["Cilantro"], "nothing known yet: it waits to be sorted"

    # "Sort them all" writes one row per tap through the single-row route.
    res = signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Loblaws", "remember": True})
    assert res.status_code == 200 and res.json()["remembered"] is True
    assert tools.get_item_store_preferences().get("cilantro") == "Loblaws"

    # The week is bought and the list rebuilt: a plan's own add (the build
    # path every source funnels through, grocery.add_grocery_item) lands
    # it under Loblaws without anyone sorting it.
    tools.remove_grocery_item(item_id)
    tools.add_grocery_item("cilantro", "2 bunches", "produce", source_weekly_plan_id=_plan_id())
    at = _by_store()
    assert at.get("Loblaws") == ["cilantro"]
    assert "cilantro" not in at.get("Unassigned", []), "not on 'Sort them all' — it has a usual store"


def test_the_undo_of_a_sort_clears_the_remembered_store(signed_in):
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Halloumi", "quantity": "1"}).json()["item_id"]
    signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Costco", "remember": True})
    assert tools.get_item_store_preferences().get("halloumi") == "Costco"

    undone = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "", "decided": False}], "remember": True, "forget": True},
    )
    assert undone.status_code == 200 and undone.json()["updated"] == 1
    assert "halloumi" not in tools.get_item_store_preferences()
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert (rows[item_id]["store"], rows[item_id]["store_decided"]) == ("", 0)


def test_a_put_back_to_a_real_store_remembers_that_store_again(signed_in):
    """The ⋯ moved eggs from Costco to Metro; Put back sends the row back
    to Costco AND makes Costco the usual again."""
    item_id = tools.add_grocery_item("eggs", "12", "dairy")["item_id"]
    tools.set_item_store("eggs", "Costco")
    signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Metro"})
    assert tools.get_item_store_preferences().get("eggs") == "Metro"

    signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "Costco", "decided": True}], "remember": True, "forget": True},
    )
    assert tools.get_item_store_preferences().get("eggs") == "Costco"
    assert _by_store().get("Costco") == ["eggs"]


def test_forget_without_anything_remembered_logs_nothing(signed_in):
    item_id = signed_in.post("/api/grocery-list/add", json={"item": "Tahini", "quantity": "1"}).json()["item_id"]

    def events():
        conn = get_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM preference_events WHERE household_id = ? AND field = 'item_store_preference'",
            (household_id(),),
        ).fetchone()["c"]
        conn.close()
        return n

    before = events()
    res = signed_in.post(
        "/api/grocery-list/store-bulk",
        json={"assignments": [{"item_id": item_id, "store": "", "decided": False}], "remember": True, "forget": True},
    )
    assert res.status_code == 200 and res.json()["updated"] == 1
    assert events() == before, "nothing to forget, so no 'delete' event for it"


def test_the_row_menu_move_updates_the_usual_at_once(signed_in):
    item_id = tools.add_grocery_item("eggs", "12", "dairy")["item_id"]
    tools.set_item_store("eggs", "Costco")
    res = signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Metro"})
    assert res.status_code == 200 and res.json()["remembered"] is True
    assert tools.get_item_store_preferences().get("eggs") == "Metro"
    assert _by_store().get("Metro") == ["eggs"], "and the row moved now"


def test_the_one_week_only_move_stays_out_of_the_usual(signed_in):
    item_id = tools.add_grocery_item("eggs", "12", "dairy")["item_id"]
    tools.set_item_store("eggs", "Costco")
    res = signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": "Metro", "remember": False})
    assert res.json()["remembered"] is False
    assert tools.get_item_store_preferences().get("eggs") == "Costco"
    assert _by_store().get("Metro") == ["eggs"]


def test_any_never_touches_the_usual(signed_in):
    item_id = tools.add_grocery_item("eggs", "12", "dairy")["item_id"]
    tools.set_item_store("eggs", "Costco")
    signed_in.post(f"/api/grocery-list/{item_id}/store", json={"store": ""})
    assert tools.get_item_store_preferences().get("eggs") == "Costco"
    rows = {it["id"]: it for it in tools.list_grocery_list()}
    assert (rows[item_id]["store"], rows[item_id]["store_decided"]) == ("", 1)


def test_the_add_sheets_pick_is_remembered_from_the_add_route(signed_in):
    res = signed_in.post("/api/grocery-list/add", json={"item": "Cilantro", "quantity": "", "store": "Costco"})
    assert res.status_code == 200
    body = res.json()
    assert body["store"] == "Costco" and body["remembered"] is True
    assert tools.get_item_store_preferences().get("cilantro") == "Costco"
    assert _by_store().get("Costco") == ["Cilantro"]


# --- 2. the screen: the row's ⋯ ----------------------------------------------


@needs_node
def test_the_row_menu_store_pill_moves_now_and_offers_put_back():
    out = run("""
setUp(0, [
  { store: 'Costco', items: [{ id: 7, item: 'Eggs', quantity: '12', store: 'Costco', store_decided: 1, category: 'dairy', status: 'needed' }] }
]);
groceryState.itemStorePrefs = { eggs: 'Costco' };
groceryState.openRowId = '7';
clickIfRendered({ gro: 'row-store', id: '7', store: 'Loblaws' });
settle(function () {
  const moved = posts('/api/grocery-list/7/store').map(function (p) { return p.body; });
  const toast = lastToast();
  const prefs = JSON.parse(JSON.stringify(groceryState.itemStorePrefs));
  tapUndo();
  settle(function () {
    const undo = posts('/api/grocery-list/store-bulk').map(function (p) { return p.body; });
    console.log(JSON.stringify({ moved: moved, toast: toast, prefs: prefs, undo: undo, after: groceryState.itemStorePrefs }));
  });
});
""")
    assert out["moved"] == [{"store": "Loblaws"}], "the route's default remembers; no remember:false"
    assert out["toast"] == {"msg": "Changes saved", "action": "Put back", "hold": 8000}
    assert out["prefs"] == {"eggs": "Loblaws"}, "the screen's copy of the usual follows at once"
    assert out["undo"] == [{
        "assignments": [{"item_id": 7, "item": "Eggs", "store": "Costco", "decided": True}],
        "remember": True, "forget": True,
    }], "Put back restores the row AND the usual"
    assert out["after"] == {"eggs": "Costco"}


@needs_node
def test_the_remember_toast_is_gone():
    from shop_harness import SHELL_JS

    assert "groOfferRememberToast" not in SHELL_JS
    assert "/store/confirm" not in SHELL_JS
