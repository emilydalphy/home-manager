"""
"As a household, 'Costco' and 'costco' are one store on Sort, not two
groups" (2026-10-22, branch overnight/store-names-match).

Every writer of a store name stored it exactly as typed, so "costco" or
"Costco " filed beside "Costco": Sort grew a second Costco group, usual
stores held both spellings, and hours saved under one were missed by the
other. stores.canonical_store_name / store_key are now the one identity.
"""
from app import tools
from app.db import get_conn
from app.tools import stores as _stores


def _line_store(item_id: int) -> str:
    conn = get_conn()
    row = conn.execute("SELECT store FROM grocery_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return row["store"]


def _stores_on_sort() -> list[str]:
    return [g["store"] for g in tools.get_grocery_list_by_store()["stores"]]


def test_a_different_capital_or_stray_space_files_under_the_store_already_there():
    first = tools.add_grocery_item("paper towels")
    tools.set_grocery_item_store(first["item_id"], "Costco")
    second = tools.add_grocery_item("oat milk")
    tools.set_grocery_item_store(second["item_id"], "  costco ")
    third = tools.add_grocery_item("batteries")
    tools.set_grocery_items_stores([{"item_id": third["item_id"], "store": "COSTCO"}])

    assert [_line_store(i["item_id"]) for i in (first, second, third)] == ["Costco"] * 3
    assert _stores_on_sort() == ["Costco"]
    assert _stores.is_multi_store_household() is False
    # The remembered store and the Kitchen sheet carry the one spelling too.
    assert tools.get_item_store_preferences()["oat milk"] == "Costco"
    assert list(tools.get_household_memory()["store_typical_items"]) == ["Costco"]


def test_the_households_usual_store_spelling_wins_over_a_line_typed_first():
    tools.add_usual_stores(["Farm Boy"])
    added = tools.add_grocery_item("kombucha")
    tools.set_grocery_item_store(added["item_id"], "farm  boy")
    assert _line_store(added["item_id"]) == "Farm Boy"
    tools.set_item_store("eggs", "FARM BOY")
    assert tools.get_item_store_preferences()["eggs"] == "Farm Boy"


def test_usual_stores_never_hold_two_spellings_of_one_store():
    tools.add_usual_stores(["Costco", "Metro"])
    out = tools.add_usual_stores(["costco ", "No  Frills", "no frills"])
    assert out["usual_stores"] == ["Costco", "Metro", "No Frills"]
    # The whole-list replace (chat's edit, the Grocery card's un-pick) too.
    edited = tools.edit_preference("usual_stores", ["Metro", "metro", " Costco"])
    assert edited["usual_stores"] == ["Metro", "Costco"]


def test_a_new_usual_store_takes_the_spelling_the_list_already_uses():
    added = tools.add_grocery_item("kombucha")
    tools.set_grocery_item_store(added["item_id"], "Trader Joe's")
    assert tools.add_usual_stores(["trader joe's"])["usual_stores"] == ["Trader Joe's"]


def test_hours_and_typical_items_find_the_store_under_any_spelling():
    tools.add_usual_stores(["Farm Boy"])
    saved = tools.set_store_hours("farm  boy", {"days": {"sun": "closed"}})
    assert saved["store"] == "Farm Boy"
    assert _stores.hours_for(" FARM BOY") == {"days": {"sun": "closed"}, "closed_on_holidays": False}

    tools.add_store_typical_items("farm boy", ["bread"])
    tools.remove_store_typical_item("FARM BOY", "bread")
    typical = tools.get_household_memory()["store_typical_items"]
    assert typical == {"Farm Boy": []}, "one key, emptied — not a second entry beside it"
    assert "bread" not in tools.get_item_store_preferences()


def test_exact_names_and_a_different_store_are_unchanged(signed_in):
    added = tools.add_grocery_item("kombucha")
    res = signed_in.post(f"/api/grocery-list/{added['item_id']}/store", json={"store": "Trader Joe's"})
    assert res.status_code == 200 and res.json()["store"] == "Trader Joe's"
    other = tools.add_grocery_item("eggs")
    tools.set_grocery_item_store(other["item_id"], "Costco")
    assert sorted(_stores_on_sort()) == ["Costco", "Trader Joe's"]
    # "No particular store" stays empty, not a store called "".
    blank = tools.add_grocery_item("napkins")
    tools.set_grocery_item_store(blank["item_id"], "   ")
    assert _line_store(blank["item_id"]) == ""


def test_unpicking_one_store_keeps_a_store_saved_with_a_double_space():
    # Review, 2026-10-22: usual_stores was tidied but the prune compared
    # strip().lower(), so "Farm  Boy" read as dropped and lost its hours
    # and typical items when Metro was un-picked.
    conn = get_conn()
    conn.execute(
        "INSERT INTO meal_preferences (household_id, usual_stores_json, store_typical_items_json, store_hours_json) "
        "VALUES (1, ?, ?, ?) ON CONFLICT(household_id) DO UPDATE SET usual_stores_json = excluded.usual_stores_json, "
        "store_typical_items_json = excluded.store_typical_items_json, store_hours_json = excluded.store_hours_json",
        ('["Costco", "Farm  Boy", "Metro"]', '{"Farm  Boy": ["bread"], "Metro": ["eggs"]}',
         '{"Farm  Boy": {"days": {"sun": "closed"}, "closed_on_holidays": false}}'),
    )
    conn.commit()
    conn.close()
    tools.edit_preference("usual_stores", ["Costco", "Farm  Boy"])
    memory = tools.get_household_memory()
    assert memory["store_typical_items"] == {"Farm  Boy": ["bread"]}
    assert _stores.hours_for("Farm Boy") == {"days": {"sun": "closed"}, "closed_on_holidays": False}


def _old_split_lines():
    """Two lines filed before this change, as typed: "costco " then "Costco"."""
    tools.add_usual_stores(["Costco"])
    a = tools.add_grocery_item("oat milk")
    b = tools.add_grocery_item("paper towels")
    conn = get_conn()
    conn.execute("UPDATE grocery_items SET store = 'costco ' WHERE id = ?", (a["item_id"],))
    conn.execute("UPDATE grocery_items SET store = 'Costco' WHERE id = ?", (b["item_id"],))
    conn.commit()
    conn.close()


def test_old_split_lines_read_as_one_group_named_the_households_way():
    _old_split_lines()
    groups = tools.get_grocery_list_by_store()["stores"]
    assert [g["store"] for g in groups] == ["Costco"]


def test_todays_shop_stops_fold_old_split_lines_into_one_stop():
    from app.tools import moves as _moves
    _old_split_lines()
    needed = tools.list_grocery_list(status="needed")
    stops = _moves._shop_stops(needed)
    assert [(s["store"], s["count"]) for s in stops] == [("Costco", 2)]
