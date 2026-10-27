"""
The Kitchen TAB (Loop Board C1 + C3, Emily, 2026-10-26): what's home, and
what Pomona will do with it; fix one thing in one tap.

NAMING: the tab is labelled "Kitchen" and keyed/pathed `home` (TABS key
'home', /home, /api/whats-home, app/tools/whats_home.py) because `kitchen`
is the Cook tab everywhere in code.

Every test says whether it is a CATCH (red on main — the thing did not
exist) or a GUARD.
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from conftest import household_today

from app import tools
from app.db import get_conn
from app.tools import whats_home

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text()
SHELL_CSS = (REPO / "static" / "shell.css").read_text()
SHELL_HTML = (REPO / "static" / "shell.html").read_text()
INVENTORY_HTML = (REPO / "static" / "inventory.html").read_text()


def _fn(name: str) -> str:
    start = SHELL_JS.index(f"function {name}(")
    nxt = re.search(r"\n  (?:async )?function \w+\(|\n  var \w+ = ", SHELL_JS[start + 10:])
    return SHELL_JS[start: start + 10 + (nxt.start() if nxt else len(SHELL_JS))]


def _row(item, *, days=None, location="fridge", category="produce", source="chat", quantity="1"):
    exp = None if days is None else (household_today() + datetime.timedelta(days=days)).isoformat()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, expiration_date, category, location) "
        "VALUES (1, ?, ?, ?, ?, ?, ?)",
        (item, quantity, source, exp, category, location),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _plan_dinner(day_offset: int, recipe: str, ingredients: list[str], status: str = "approved", slot="dinner"):
    """An approved plan covering today with one meal on today + day_offset."""
    today = household_today()
    conn = get_conn()
    plan = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, content_start_date, day_count, status) "
        "VALUES (1, ?, ?, 7, ?)",
        (today.isoformat(), today.isoformat(), status),
    ).lastrowid
    rid = conn.execute(
        "INSERT INTO recipes (household_id, name, ingredients_json) VALUES (1, ?, ?)",
        (recipe, json.dumps([{"item": i, "qty": "1"} for i in ingredients])),
    ).lastrowid
    entry = conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, recipe_id, weekly_plan_id) VALUES (1, ?, ?, ?, ?)",
        ((today + datetime.timedelta(days=day_offset)).isoformat(), slot, rid, plan),
    ).lastrowid
    conn.commit()
    conn.close()
    return entry


def _needed(item: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item FROM grocery_items WHERE household_id = 1 AND status = 'needed' AND lower(item) = lower(?)",
        (item,),
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# C1: the tab exists and its route serves the shell
# ---------------------------------------------------------------------------

def test_the_bar_reads_today_plan_shop_kitchen_cook():
    """CATCH — four tabs on main; the Kitchen tab sits between Shop and Cook,
    keyed `home` so it never collides with Cook's `kitchen`."""
    tabs = SHELL_JS[SHELL_JS.index("  var TABS = ["): SHELL_JS.index("  ];", SHELL_JS.index("  var TABS = ["))]
    labels = re.findall(r"label: '([^']+)'", tabs)
    keys = re.findall(r"key: '([^']+)'", tabs)
    assert labels == ["Today", "Plan", "Shop", "Kitchen", "Cook"]
    assert keys == ["today", "week", "grocery", "home", "kitchen"]
    assert "{ key: 'home', path: '/home', label: 'Kitchen', icon: ICONS.fridge, home: true }" in tabs
    assert "grid-template-columns: repeat(5, minmax(0, 1fr));" in SHELL_CSS


def test_home_route_serves_the_shell_and_is_a_shell_route_everywhere(signed_in):
    """CATCH — /home was a 404."""
    r = signed_in.get("/home")
    assert r.status_code == 200 and 'id="tab-bar"' in r.text
    sw = (REPO / "static" / "service-worker.js").read_text()
    assert '"/home"' in sw[sw.index("const SHELL_ROUTES"):sw.index("const SHELL_ASSETS")]
    from app import push
    assert "/home" in push.ALLOWED_PATHS
    assert "'/home': 1" in (REPO / "static" / "push.js").read_text()


def test_inventory_leaves_cooks_more_sheet():
    """CATCH — the Inventory row was the fourth row of Cook's More."""
    rows = _fn("cookMoreRowsHtml")
    assert 'data-sheet="inventory"' not in rows and "Inventory" not in rows.replace("No Inventory row", "")
    assert rows.count('class="kit-row"') == 3


def test_the_tab_never_shows_the_in_development_label_or_line():
    """CATCH — the tab is a real feature: no pill, and the add/scan page it
    opens hides its "still being built" line when opened from the tab."""
    block = SHELL_JS[SHELL_JS.index("// ---------- The Kitchen TAB: what's home"):
                     SHELL_JS.index("// ---------- Kitchen root: today's cooks ----------")]
    assert "In development" not in block and "INVENTORY_IN_DEVELOPMENT" not in block
    assert "kitchenAdd: { title: 'Add to the kitchen', src: '/static/inventory.html?from=kitchen' }" in SHELL_JS
    assert "if (INVENTORY_IN_DEVELOPMENT && !FROM_KITCHEN_TAB)" in INVENTORY_HTML


def test_the_dock_has_a_seam_for_card_c2():
    """CATCH — both dock buttons go through one function a later branch
    replaces; until then they open the working scan/add page."""
    build = _fn("buildHomePanel")
    assert 'data-home="snap">' in build and "Snap a shelf" in build
    assert 'data-home="add">+ Add</button>' in build
    click = _fn("onHomeClick")
    assert "openKitchenAdd('snap')" in click and "openKitchenAdd('add')" in click
    assert "openKitchenSheet('kitchenAdd')" in _fn("openKitchenAdd")


def test_inventory_changes_refresh_the_tab():
    """CATCH — the panel builds once; chat inventory tools, chat ticks and
    week changes re-read it, and every showing re-reads quietly."""
    refresh = _fn("refreshStaleTabsFromActions")
    assert refresh.count("refreshHomeOnce();") >= 4
    activate = _fn("activateTab")
    assert "buildHomePanel(panel)" in activate and "loadHome(true)" in activate
    assert "activateTab('home', true)" in _fn("followActionHref")


def test_item_sheet_markup_and_escape():
    """CATCH — the C3 sheet exists and Escape closes it like a tap outside."""
    assert 'id="home-item-sheet"' in SHELL_HTML and 'id="home-item-scrim"' in SHELL_HTML
    assert "'home-item-scrim'" in SHELL_JS[SHELL_JS.index("var ESCAPE_CLOSES_SCRIMS"):]
    html = _fn("homeItemSheetHtml")
    for word in ("How much is left?", "Used it up", "Freeze it", "Threw it out", "Move to "):
        assert word in html, word
    for chip in ("'Plenty'", "'About half'", "'Almost gone'"):
        assert chip in SHELL_JS[SHELL_JS.index("var HOME_AMOUNTS"):]


# ---------------------------------------------------------------------------
# C1: the read
# ---------------------------------------------------------------------------

def test_count_excludes_staples_spices_and_empty_rows(signed_in):
    """CATCH — "41 things home" counts what the segments show: never a
    regular (staples) or a spice."""
    conn = get_conn()
    conn.execute("INSERT INTO staples (household_id, item, next_due_at) VALUES (1, 'Coffee', '2099-01-01')")
    conn.commit()
    conn.close()
    _row("Coffee", category="pantry", location="pantry")
    _row("Smoked paprika", category="pantry", location="pantry")
    _row("Empty jar", quantity="0", location="pantry")
    a = _row("Eggs", category="dairy")
    b = _row("Rice", category="pantry", location="pantry")
    c = _row("Peas", category="frozen", location="freezer")
    r = signed_in.get("/api/whats-home")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 3
    assert data["counts"] == {"fridge": 1, "freezer": 1, "pantry": 1}
    shown = {row["id"] for loc in data["locations"].values() for row in loc}
    assert shown == {a, b, c}


def test_use_soon_is_soonest_first_at_most_three_with_dots_and_plans():
    """CATCH — Use soon leads: three rows, terracotta today, apricot soon,
    each with its plan or a Freeze it offer."""
    later = _row("Feta", days=3, category="dairy")
    _row("Far-off carrots", days=6)  # past USE_SOON_DAYS
    spinach = _row("Baby spinach", days=2)
    yogurt = _row("Greek yogurt", days=0, category="dairy")
    cabbage = _row("Half a cabbage", days=1)
    _row("Old milk", days=-1, category="dairy")  # past its date: the check's, not Use soon's
    _plan_dinner(1, "Spinach pasta", ["baby spinach", "pasta"])
    soon = whats_home.get_whats_home()["use_soon"]
    assert [s["id"] for s in soon] == [yogurt, cabbage, spinach]
    assert len(soon) == whats_home.USE_SOON_MAX and later not in [s["id"] for s in soon]
    assert soon[0]["dot"] == "today" and soon[1]["dot"] == "soon"
    # Yogurt doesn't freeze and nothing plans it: "today".
    assert soon[0]["line"] == "today" and soon[0]["freeze_ok"] is False
    # Cabbage: nothing planned, freezes -> the pill.
    assert soon[1]["freeze_ok"] is True and soon[1]["line"] == ""
    # Spinach is in tomorrow's dinner.
    dow = (household_today() + datetime.timedelta(days=1)).strftime("%a")
    assert soon[2]["line"] == f"in {dow} dinner" and soon[2]["freeze_ok"] is False


def test_rows_say_amounts_as_words_or_their_plan_never_numbers():
    """CATCH — amounts are "plenty" / "about half" / "almost gone"."""
    _row("Milk", quantity="half a carton", category="dairy")
    _row("Eggs", quantity="12", category="dairy")
    _row("Cheddar", quantity="a little", category="dairy")
    _row("Red peppers", quantity="3")
    _plan_dinner(2, "Fajitas", ["red peppers"])
    lines = {r["item"]: r["line"] for r in whats_home.get_whats_home()["locations"]["fridge"]}
    dow = (household_today() + datetime.timedelta(days=2)).strftime("%a")
    assert lines == {"Milk": "about half", "Eggs": "plenty", "Cheddar": "almost gone",
                     "Red peppers": f"in {dow} dinner"}


def test_freezer_lists_oldest_first_even_without_frozen_on():
    """CATCH — found in the browser check: a row frozen before frozen_on
    existed sorted after one frozen today."""
    old = _row("Ground beef", location="freezer", category="meat/seafood")
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET created_at = datetime('now', '-20 days') WHERE id = ?", (old,))
    conn.commit()
    conn.close()
    new = _row("Half a cabbage", days=2)
    tools.freeze_item(new)
    assert [r["id"] for r in whats_home.get_whats_home()["locations"]["freezer"]] == [old, new]


def test_a_draft_plans_nothing_and_a_done_dinner_plans_nothing():
    """GUARD — only an approved, uneaten meal is a plan."""
    _row("Red peppers")
    _plan_dinner(1, "Fajitas", ["red peppers"], status="draft")
    entry = _plan_dinner(2, "Pepper soup", ["red peppers"])
    conn = get_conn()
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (entry,))
    conn.commit()
    conn.close()
    assert whats_home.get_whats_home()["locations"]["fridge"][0]["line"] == "plenty"


def test_up_to_date_line_from_the_last_tick_and_the_last_dinner():
    """CATCH — "Up to date after Saturday's shop and last night's dinner."
    and nothing at all when neither is known."""
    _row("Eggs", category="dairy")
    assert whats_home.get_whats_home()["up_to_date"] == ""
    today = household_today()
    conn = get_conn()
    conn.execute(
        "INSERT INTO grocery_items (household_id, item, status, inventory_added_at) VALUES (1, 'Eggs', 'purchased', ?)",
        (datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.execute(
        "INSERT INTO meal_plan_entries (household_id, date, slot, freeform_meal, cooked_status, inventory_depleted_at) "
        "VALUES (1, ?, 'dinner', 'Tacos', 'done', datetime('now'))",
        ((today - datetime.timedelta(days=1)).isoformat(),),
    )
    conn.commit()
    conn.close()
    assert whats_home.get_whats_home()["up_to_date"] == "Up to date after today's shop and last night's dinner."


def test_empty_kitchen_has_a_friendly_empty_state():
    """CATCH — a new household's Kitchen points to the first shop or a photo."""
    data = whats_home.get_whats_home()
    assert data["count"] == 0 and data["use_soon"] == []
    empty = _fn("homeEmptyHtml")
    assert "Tick things off on the list" in empty and "snap a shelf" in empty


def test_the_item_sheet_line_reads_like_the_mockup():
    """CATCH — "Fridge · bought Saturday · best by Thursday · in Tuesday's dinner"."""
    item = _row("Baby spinach", days=3, source="grocery_checkoff")
    _plan_dinner(1, "Spinach pasta", ["baby spinach"])
    view = tools.get_whats_home_item(item)
    tomorrow = (household_today() + datetime.timedelta(days=1)).strftime("%A")
    assert view["detail"].startswith("Fridge · bought today · best by ")
    assert view["detail"].endswith(f" · in {tomorrow}'s dinner")


# ---------------------------------------------------------------------------
# C3: the amount chip, the four actions, Undo
# ---------------------------------------------------------------------------

def test_amount_chip_saves_and_shows(signed_in):
    """CATCH — "How much is left?" is stored and read back as words."""
    item = _row("Milk", quantity="2 L", category="dairy")
    r = signed_in.post(f"/api/whats-home/{item}/amount", json={"level": "half"})
    assert r.status_code == 200 and r.json()["amount_word"] == "about half"
    assert tools.get_whats_home_item(item)["line"] == "about half"
    assert signed_in.post(f"/api/whats-home/{item}/amount", json={"level": "lots"}).status_code == 400
    # A re-buy merging into the row retires the chip.
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET quantity = '4 L' WHERE id = ?", (item,))
    conn.commit()
    conn.close()
    assert tools.get_whats_home_item(item)["amount"] == "plenty"


def test_almost_gone_on_food_a_dinner_needs_goes_on_the_list_once():
    """CATCH — and never a duplicate line."""
    item = _row("Lemons", quantity="2")
    _plan_dinner(1, "Lemon chicken", ["lemons", "chicken thighs"])
    first = tools.set_amount_left(item, "almost_gone")
    assert first["added_to_list"] is True and first["needed_for"]
    again = tools.set_amount_left(item, "almost_gone")
    assert again["added_to_list"] is False
    assert len(_needed("Lemons")) == 1
    # Undo of the first tap takes its line back off.
    tools.restore_kitchen_item(first["undo"])
    assert _needed("Lemons") == []


def test_almost_gone_on_food_nothing_needs_stays_off_the_list():
    """GUARD — only food a planned meal needs."""
    item = _row("Mustard", category="pantry", location="pantry")
    out = tools.set_amount_left(item, "almost_gone")
    assert out["added_to_list"] is False and _needed("Mustard") == []


def test_almost_gone_when_already_on_the_list_adds_nothing():
    """GUARD — a line already waiting is enough."""
    item = _row("Lemons")
    _plan_dinner(1, "Lemon chicken", ["lemons"])
    tools.add_grocery_item("lemons", "3", "produce")
    assert tools.set_amount_left(item, "almost_gone")["added_to_list"] is False
    assert len(_needed("lemons")) == 1


def test_used_it_up_and_threw_it_out_with_undo(signed_in):
    """CATCH — both take it off; Undo puts the same row back."""
    for path, how in (("used-up", "used"), ("thrown-out", "thrown")):
        item = _row("Raspberries", days=1, quantity="1 box")
        r = signed_in.post(f"/api/whats-home/{item}/{path}")
        assert r.status_code == 200 and r.json()["item"] == "Raspberries"
        assert get_conn().execute("SELECT 1 FROM inventory_items WHERE id = ?", (item,)).fetchone() is None
        rec = get_conn().execute("SELECT how FROM inventory_removals WHERE inventory_item_id = ?", (item,)).fetchone()
        assert rec["how"] == how
        undo = r.json()["undo"]
        assert "row" not in undo  # the row is kept on the server, not sent round
        back = signed_in.post("/api/whats-home/undo", json={"undo": undo})
        assert back.status_code == 200 and back.json()["undone"] is True
        row = get_conn().execute("SELECT item, quantity FROM inventory_items WHERE id = ?", (item,)).fetchone()
        assert row["item"] == "Raspberries" and row["quantity"] == "1 box"
        # A second Undo does nothing.
        assert signed_in.post("/api/whats-home/undo", json={"undo": undo}).json()["undone"] is False
        get_conn().execute("DELETE FROM inventory_items WHERE id = ?", (item,)).connection.commit()


def test_freeze_it_moves_it_to_the_freezer_dated_today_with_undo(signed_in):
    """CATCH (for the tab) — Freeze it is inventory.freeze_item's door."""
    item = _row("Half a cabbage", days=2)
    r = signed_in.post(f"/api/inventory/{item}/freeze")
    assert r.status_code == 200
    data = signed_in.get("/api/whats-home").json()
    frozen = [row for row in data["locations"]["freezer"] if row["id"] == item]
    assert frozen and frozen[0]["detail"].startswith("Freezer · frozen today")
    back = signed_in.post("/api/inventory/unfreeze", json={"undo": r.json()["undo"]})
    assert back.json()["undone"] is True
    assert signed_in.get("/api/whats-home").json()["counts"]["fridge"] == 1
    assert "/api/inventory/' + id + '/freeze'" in _fn("homeFreeze")


def test_move_to_pantry_and_back_with_undo(signed_in):
    """CATCH — "Move to pantry" (fridge rows) / "Move to fridge"."""
    item = _row("Onions")
    assert tools.get_whats_home_item(item)["move_to"] == "pantry"
    r = signed_in.post(f"/api/whats-home/{item}/move", json={"location": "pantry"})
    assert r.status_code == 200 and r.json()["location"] == "pantry"
    assert tools.get_whats_home_item(item)["location"] == "pantry"
    assert tools.get_whats_home_item(item)["move_to"] == "fridge"
    assert signed_in.post(f"/api/whats-home/{item}/move", json={"location": "freezer"}).status_code == 400
    back = signed_in.post("/api/whats-home/undo", json={"undo": r.json()["undo"]})
    assert back.json()["undone"] is True
    assert tools.get_whats_home_item(item)["location"] == "fridge"


def test_every_action_toasts_the_thing_with_undo():
    """CATCH — the house toast: `<thing> was <verbed>`, Undo."""
    act = _fn("homeItemAct")
    assert "' was ' + verb + '.'" in act
    for verb in ("'used up'", "'thrown out'", "'moved to the '"):
        assert verb in act
    assert "' was frozen.'" in _fn("homeFreeze")
    assert "label: 'Undo'" in _fn("homeToastWithUndo")


def test_undo_cannot_reach_another_households_removal(signed_in):
    """GUARD — the Undo looks the removal up by its id AND the household."""
    from app import households

    other = households.create_household("Kitchen Tab Neighbours", "kitchen-tab-neighbours-passphrase")
    with tools.use_household(other):
        theirs = get_conn()
        cur = theirs.execute(
            "INSERT INTO inventory_items (household_id, item, quantity, location) VALUES (?, 'Their raspberries', '1', 'fridge')",
            (other,),
        )
        theirs.commit()
        theirs.close()
        out = tools.used_up_item(cur.lastrowid)
    r = signed_in.post("/api/whats-home/undo", json={"undo": out["undo"]})
    assert r.status_code == 400
    assert get_conn().execute("SELECT 1 FROM inventory_items WHERE id = ?", (out["item_id"],)).fetchone() is None


# ---------------------------------------------------------------------------
# Verify round, 2026-10-27 (VERIFY-inventory-kitchen-tab.md items 1-8)
# ---------------------------------------------------------------------------

def test_undo_after_a_rebuy_does_not_make_a_second_row(signed_in):
    """CATCH — verify #1: Threw out Milk, bought Milk again, Undo -> two Milks."""
    old = _row("Milk", quantity="half a jug", category="dairy")
    out = signed_in.post(f"/api/whats-home/{old}/thrown-out").json()
    new = _row("Milk", quantity="2 L", category="dairy", source="grocery_checkoff")
    r = signed_in.post("/api/whats-home/undo", json={"undo": out["undo"]})
    assert r.status_code == 200
    assert r.json()["undone"] is False and r.json()["already_back"] is True and r.json()["back_as"] == new
    data = whats_home.get_whats_home()
    assert [x["id"] for x in data["locations"]["fridge"]] == [new] and data["count"] == 1
    # The token is spent.
    assert signed_in.post("/api/whats-home/undo", json={"undo": out["undo"]}).json()["undone"] is False


def test_an_undo_token_runs_out_after_a_day(signed_in):
    """CATCH — verify #1: a removal token was good for ever."""
    item = _row("Raspberries")
    out = tools.used_up_item(item)
    conn = get_conn()
    conn.execute("UPDATE inventory_removals SET removed_at = datetime('now', '-25 hours') WHERE id = ?",
                 (out["undo"]["removal_id"],))
    conn.commit()
    conn.close()
    assert signed_in.post("/api/whats-home/undo", json={"undo": out["undo"]}).json()["undone"] is False
    assert get_conn().execute("SELECT 1 FROM inventory_items WHERE id = ?", (item,)).fetchone() is None


def test_the_sheet_offers_freeze_by_the_same_rule_as_use_soon():
    """CATCH — verify #2: Greek yogurt's sheet offered Freeze it."""
    yogurt = _row("Greek yogurt", days=1, category="dairy")
    cabbage = _row("Half a cabbage", days=1)
    peas = _row("Peas", location="freezer", category="frozen")
    assert tools.get_whats_home_item(yogurt)["freeze_ok"] is False
    assert tools.get_whats_home_item(cabbage)["freeze_ok"] is True
    assert tools.get_whats_home_item(peas)["freeze_ok"] is False


def test_chat_inventory_cards_open_the_kitchen_tab():
    """CATCH — verify #3: update_inventory & co. were tagged `kitchen` (Cook),
    a dead end once Inventory left Cook's More."""
    from app import main as app_main

    for tool in ("update_inventory", "update_inventory_items", "remove_inventory_item"):
        assert app_main._categorize_tool(tool) == ("home", "home", None), tool
    assert app_main._categorize_tool("check_off_meal") == ("kitchen", "kitchen", None)
    refresh = _fn("refreshStaleTabsFromActions")
    home = refresh[refresh.index("action.tab === 'home'"):refresh.index("action.tab === 'grocery'")]
    for call in ("refreshHomeOnce();", "refreshKitchenPanel();", "refreshTodayMoves();"):
        assert call in home, call


def test_use_soon_reads_naturally_for_today():
    """CATCH — verify #5: "today · tonight's dinner"."""
    spinach = _row("Baby spinach", days=0)
    yogurt = _row("Greek yogurt", days=0, category="dairy")
    _plan_dinner(0, "Spinach pasta", ["baby spinach"])
    _plan_dinner(0, "Yogurt bowl", ["greek yogurt"], slot="snack")
    lines = {s["id"]: s["line"] for s in whats_home.get_whats_home()["use_soon"]}
    assert lines[spinach] == "in tonight's dinner"
    assert lines[yogurt] == "today · snacks"


def test_a_row_gone_under_an_open_sheet_says_so_in_the_house_voice(signed_in):
    """CATCH — verify #8: "No inventory item with id 12."."""
    for path, body in (("used-up", None), ("thrown-out", None), ("amount", {"level": "half"}),
                       ("move", {"location": "pantry"})):
        r = signed_in.post(f"/api/whats-home/987654/{path}", json=body)
        assert r.status_code == 404 and r.json()["detail"] == "That's already gone from the kitchen.", path


def test_almost_gone_closes_the_sheet_and_the_pill_is_not_apricot():
    """CATCH — verify #6 (toast over the sheet's buttons) and #7 (Rule 5:
    the dock's Snap a shelf is the one apricot)."""
    act = _fn("homeItemAct")
    amount = act[act.index("if (what === 'amount')"):act.index("} else {")]
    assert "closeHomeItemSheet();" in amount
    pill = SHELL_CSS[SHELL_CSS.index(".home-freeze-pill {"):]
    pill = pill[:pill.index("}")]
    assert "var(--apricot)" not in pill and "var(--celadon-tint)" in pill


def test_snap_a_shelf_keeps_one_line_at_320():
    """CATCH — verify #6 (320px): the label wrapped to two lines."""
    assert ".home-snap span { white-space: nowrap; }" in SHELL_CSS
    at = SHELL_CSS.index("@media (max-width: 359px) {\n  /* 320px: 236px of dock")
    narrow = SHELL_CSS[at:]
    narrow = narrow[:narrow.index("\n}\n")]
    assert ".home-dock-row .home-snap" in narrow and ".home-add" in narrow
    # Re-verify R2: the narrow rules have to come AFTER the base ones they
    # override, or the base `.home-add { flex: 1 1 0 }` wins at 320px.
    assert SHELL_CSS.index("\n.home-add {") < at
    assert SHELL_CSS.index("\n.home-dock-row .home-snap {") < at


def test_undo_still_restores_when_an_older_batch_shares_the_shelf():
    """CATCH — re-verify R1: two freezer bags of chicken thighs; use up the
    older one, Undo -> "already back" and the older bag was lost."""
    older = _row("Chicken thighs", location="freezer", category="meat/seafood")
    newer = _row("Chicken thighs", location="freezer", category="meat/seafood")
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET created_at = datetime('now', '-29 days'), "
                 "updated_at = datetime('now', '-29 days') WHERE id = ?", (older,))
    conn.execute("UPDATE inventory_items SET created_at = datetime('now', '-7 days'), "
                 "updated_at = datetime('now', '-7 days') WHERE id = ?", (newer,))
    conn.commit()
    conn.close()
    out = tools.used_up_item(older)
    back = tools.restore_kitchen_item(out["undo"])
    assert back["undone"] is True and not back.get("already_back")
    ids = sorted(r["id"] for r in whats_home.get_whats_home()["locations"]["freezer"])
    assert ids == sorted([older, newer])
