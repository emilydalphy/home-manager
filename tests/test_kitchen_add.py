"""
"Add to the kitchen" (Loop Board C2) and the Kitchen in first-run setup
(C5), Emily 2026-10-26/27. Mockups: mockups-c Kitchen.dc.html screen 2 and
OptionC.dc.html screens 2 and 3.

The rule both cards hang on: NOTHING IS SAVED BEFORE THE REVIEW. A photo, a
receipt or a sentence comes back as a draft; the review's one button sends
only the rows left ticked; items Pomona isn't sure of start unticked.

Every test says whether it is a CATCH (red on the base, inventory/kitchen-tab
b0bc508 — the thing did not exist) or a GUARD.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from app import main
from app.db import get_conn
from app.tools import kitchen_add

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
KA_JS_PATH = STATIC / "kitchen-add.js"
SHELL_JS = (STATIC / "shell.js").read_text()
SHELL_HTML = (STATIC / "shell.html").read_text()
ONBOARDING = (STATIC / "onboarding.html").read_text()

_TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
)
_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the sheet's own builders")


def _ka(expr: str):
    res = nodeharness.run_node(
        f"const k = require({str(KA_JS_PATH)!r}); console.log(JSON.stringify({expr}));", timeout=30
    )
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


def _count() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM inventory_items").fetchone()["c"]
    finally:
        conn.close()


def _rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, item, quantity, location, source, category FROM inventory_items ORDER BY id")]
    finally:
        conn.close()


# ---------- the sheet: four ways in, in this order ----------

@_needs_node
def test_the_sheet_offers_four_ways_in_in_the_mockups_order_and_words():
    """CATCH — "+ Add" opened inventory.html; the sheet did not exist."""
    assert [(o["key"], o["title"], o["line"]) for o in _ka("k.OPTIONS")] == [
        ("snap", "Snap a shelf", "Fridge, freezer or pantry. One photo each."),
        ("receipt", "Scan a receipt", "For a shop you did without the list."),
        ("say", "Say it", "“Two litres of milk and a dozen eggs.”"),
        ("type", "Type it", "One thing at a time."),
    ]
    html = _ka("k.htmlFor({step: 'menu'})")
    assert '<h2 class="ka-title" id="ka-title">Add to the kitchen</h2>' in html
    assert "Shopped with Pomona? It’s already in. This is for everything else." in html
    titles = re.findall(r'<p class="ka-opt-title">([^<]*)</p>', html)
    assert titles == ["Snap a shelf", "Scan a receipt", "Say it", "Type it"]
    assert html.count('stroke-width="2.2"') == 4


def test_the_dock_opens_the_sheet_snap_straight_to_the_photo_step():
    """CATCH — "Snap a shelf" goes to the photo step (mode 'snap') on the
    shelf showing; "+ Add" opens the four ways in (mode 'add')."""
    start = SHELL_JS.index("function openKitchenAdd(")
    body = SHELL_JS[start: SHELL_JS.index("// ---- The item sheet (C3)", start)]
    assert "window.KitchenAdd.open(" in body
    assert "mode: mode === 'snap' ? 'snap' : 'add'" in body
    assert "place: homeSegment(homeState.data)" in body
    assert "refreshHomePanel()" in body and "/api/whats-home/add/undo" in body
    assert "openKitchenSheet(" not in body
    # The shell loads the sheet (and its mic) after api.js, before shell.js.
    i_api = SHELL_HTML.index('src="/static/api.js"')
    i_ka = SHELL_HTML.index('src="/static/kitchen-add.js"')
    assert i_api < SHELL_HTML.index('src="/static/dictation.js"') < i_ka < SHELL_HTML.index('src="/static/shell.js"')


@_needs_node
def test_snap_mode_starts_on_the_photo_step_with_the_shelf_picked():
    """CATCH — the photo step: three shelves, the one showing picked, and
    the camera button names it."""
    html = _ka("k.htmlFor({step: 'snap', place: 'freezer', kind: 'freezer'})")
    assert re.findall(r'data-place="(\w+)" aria-pressed="(\w+)"', html) == [
        ("fridge", "false"), ("freezer", "true"), ("pantry", "false")]
    assert "Take a photo of the freezer" in html
    assert html.count('class="ka-primary"') == 1


# ---------- the review: "Here's what I saw" ----------

@_needs_node
def test_items_pomona_isnt_sure_of_start_unticked_and_the_button_counts_the_rest():
    """CATCH — the review leaves the unsure ones unticked (OptionC screen 3)
    and its one button says how many go where."""
    items = [
        {"item": "Milk", "quantity": "about half", "sure": True},
        {"item": "Eggs", "quantity": "6", "sure": True},
        {"item": "Hot sauce", "quantity": "", "sure": False},
        {"item": "Cheddar", "quantity": "1 block"},  # no flag: ticked
    ]
    rows = _ka(f"k.reviewRows({json.dumps(items)})")
    assert [(r["item"], r["ticked"]) for r in rows] == [
        ("Milk", True), ("Eggs", True), ("Hot sauce", False), ("Cheddar", True)]
    html = _ka(f"k.htmlFor({{step: 'review', kind: 'fridge', rows: k.reviewRows({json.dumps(items)})}})")
    assert "Here’s what <em>I saw</em>" in html
    assert "Untick anything that’s wrong." in html
    assert re.findall(r'aria-pressed="(\w+)"><span class="ka-box">', html) == ["true", "true", "false", "true"]
    assert "Hot sauce?" in html and ">not sure<" in html
    assert html.count('class="ka-primary"') == 1, "one button"
    assert ">Add 3 to the fridge</button>" in html


@_needs_node
def test_the_button_text_follows_the_place_and_the_count():
    """CATCH — "Add 13 to the fridge"; a receipt goes "to the kitchen"."""
    assert _ka("k.addButtonLabel(13, 'fridge')") == "Add 13 to the fridge"
    assert _ka("k.addButtonLabel(1, 'pantry')") == "Add 1 to the pantry"
    assert _ka("k.addButtonLabel(0, 'fridge')") == "Add to the fridge"
    receipt = [{"item": "Milk", "location": ""}, {"item": "Rice", "location": ""}]
    assert _ka(f"k.reviewPlace('receipt', k.reviewRows({json.dumps(receipt)}))") == "kitchen"
    said = [{"item": "milk", "location": "fridge"}, {"item": "eggs", "location": "fridge"}]
    assert _ka(f"k.reviewPlace('say', k.reviewRows({json.dumps(said)}))") == "fridge"
    assert _ka("k.reviewPlace('freezer', [])") == "freezer"
    html = _ka(f"k.htmlFor({{step: 'review', kind: 'receipt', rows: k.reviewRows({json.dumps(receipt)})}})")
    assert ">Add 2 to the kitchen</button>" in html
    # Nothing ticked: the button waits.
    none = _ka("k.htmlFor({step: 'review', kind: 'fridge', rows: k.reviewRows([{item: 'Jar', sure: false}])})")
    assert 'data-ka="save" disabled>Add to the fridge</button>' in none


@_needs_node
def test_the_toast_names_the_thing_or_the_count():
    """CATCH — the house toast pattern."""
    assert _ka("k.savedLine(13, 'fridge', [])") == "13 things were added to the fridge."
    assert _ka("k.savedLine(1, 'pantry', ['rice'])") == "Rice was added to the pantry."
    # Verify item 8: a photo that confirms tracked rows counts them apart.
    assert _ka("k.savedLine(9, 'fridge', [], 4)") == "9 things were added to the fridge, and 4 updated."
    assert _ka("k.savedLine(0, 'fridge', ['Milk', 'Eggs'], 2)") == "2 things in the fridge were updated."
    assert _ka("k.savedLine(0, 'fridge', ['Milk'], 1)") == "Milk in the fridge was updated."


ROWS_14 = json.dumps([{"item": f"Thing {i}", "quantity": "1"} for i in range(14)])


@_needs_node
def test_a_long_review_folds_and_has_its_eyebrow_and_a_way_out():
    """CATCH (verify items 4, 8) — 14 rows show 7 and "+ 7 more" so Add
    stays in view; the mockup's eyebrow; a quiet way out when it's all
    wrong: another photo, say it again, or (setup) skip this shelf."""
    html = _ka(f"k.htmlFor({{step: 'review', kind: 'fridge', rows: k.reviewRows({ROWS_14})}})")
    assert html.count('data-ka="tick"') == 7 and ">+ 7 more</button>" in html
    assert '<p class="ka-eyebrow">Kitchen · fridge photo</p>' in html
    assert 'data-ka="retake">Try another photo</button>' in html
    assert html.index('data-ka="save"') < html.index('data-ka="retake"')
    every = _ka(f"k.htmlFor({{step: 'review', kind: 'fridge', showAll: true, rows: k.reviewRows({ROWS_14})}})")
    assert every.count('data-ka="tick"') == 14 and "more</button>" not in every
    said = _ka("k.htmlFor({step: 'review', kind: 'say', rows: k.reviewRows([{item: 'Milk'}])})")
    assert '<p class="ka-eyebrow">Kitchen · what you said</p>' in said and 'data-ka="again">Say it again</button>' in said
    setup = _ka("k.htmlFor({mode: 'setup', step: 'review', kind: 'freezer', rows: k.reviewRows([{item: 'Peas', sure: false}])})")
    assert 'data-ka="skip">Skip the freezer</button>' in setup
    assert 'data-ka="save" disabled' in setup, "nothing ticked: Skip is the way on"
    left = _ka("k.htmlFor({step: 'review', kind: 'pantry', leftOut: {spices: ['Cumin'], regulars: ['Coffee']}, "
               "rows: k.reviewRows([{item: 'Rice'}])})")
    assert "Left out — spices and regulars live with Staples: Cumin, Coffee" in left


@_needs_node
def test_a_skipped_shelf_does_not_look_finished():
    """CATCH (verify item 5) — a skip is the sand dash, not the celadon tick."""
    html = _ka("k.htmlFor({mode: 'setup', step: 'setup', setup: {fridge: 4, freezer: 'skipped', pantry: null}})")
    steps = re.findall(r'<span class="ka-ic([^"]*)">', html)
    assert steps == [" is-done", " is-skipped", ""]
    assert "is-skipped{background:var(--sand)" in KA_JS_PATH.read_text()


def test_setup_progress_survives_a_close_and_esc_spares_typed_words():
    """CATCH (verify items 4, 8) — closing the three-photo sheet part-way
    keeps finished shelves for the next open; Escape with words typed in
    Say it / Type it doesn't close the sheet (shell.js's courtesy)."""
    js = KA_JS_PATH.read_text()
    assert "if (st.mode === 'setup' && setupMemory) {" in js
    assert js.count("remember();") >= 2
    esc = js[js.index("if (e.key !== 'Escape'"): js.index("close();", js.index("if (e.key !== 'Escape'"))]
    assert "/^(INPUT|TEXTAREA)$/.test(active.tagName)" in esc and "String(active.value || '').trim()) return;" in esc


def test_the_sheet_saves_only_from_the_review_and_only_what_is_ticked():
    """CATCH — the guardrail, in the sheet's own code: the one write is
    post(), called from the review's button and from Type it, and the
    review sends the ticked rows and nothing else. A photo or a sentence
    only ever reaches a read-only route."""
    js = KA_JS_PATH.read_text()
    assert js.count("'/api/whats-home/add'") == 1
    post_calls = re.findall(r"\bpost\(", js)
    assert len(post_calls) == 3  # the definition + saveReview + saveTyped
    save = js[js.index("function saveReview()"): js.index("function saveTyped()")]
    assert "st.rows.filter(function (r) { return r.ticked; })" in save
    scan = js[js.index("function scan("): js.index("function readSaid()")]
    said = js[js.index("function readSaid()"): js.index("function post(")]
    assert "/api/whats-home/add" not in scan and "/api/whats-home/add" not in said
    assert "st.step = 'review'" in scan and "st.step = 'review'" in said
    assert "fetch('/api" not in js, "through static/api.js"


# ---------- the server: scans read, never write ----------

@pytest.mark.parametrize("path,scanner,place", [
    ("/api/inventory/scan-fridge", "scan_fridge_photo", "fridge"),
    ("/api/inventory/scan-freezer", "scan_freezer_photo", "freezer"),
    ("/api/inventory/scan-pantry", "scan_pantry_photo", "pantry"),
    ("/api/inventory/scan-receipt", "scan_receipt_image", ""),
])
def test_a_scan_returns_a_draft_with_unsure_rows_flagged_and_saves_nothing(signed_in, monkeypatch, path, scanner, place):
    """CATCH — every row carries `sure`; a low-confidence, question-marked
    or vague one is not sure. The freezer route is new. Nothing is saved."""
    def fake(b64, media_type):
        return [
            {"item": "Milk", "quantity": "4 L", "category": "dairy", "confidence": "high"},
            {"item": "Hot sauce?", "category": "pantry", "confidence": "high"},
            {"item": "Yogurt", "category": "dairy", "confidence": "low"},
            {"item": "Jar", "category": "other", "confidence": "high"},
            {"item": "Butter", "category": "dairy"},  # no confidence at all
        ]

    monkeypatch.setattr(main, scanner, fake)
    res = signed_in.post(path, files={"photo": ("shelf.jpg", _TINY_JPEG, "image/jpeg")})
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [(i["item"], i["sure"]) for i in items] == [
        ("Milk", True), ("Hot sauce", False), ("Yogurt", False), ("Jar", False), ("Butter", True)]
    if place:
        assert {i["location"] for i in items} == {place}
    assert _count() == 0


def test_is_sure_reads_the_models_flag_then_the_name():
    """CATCH — the "isn't sure of" rule, and its fallback to ticked."""
    assert kitchen_add.is_sure({"item": "Greek yogurt"}) is True
    assert kitchen_add.is_sure({"item": "Greek yogurt", "confidence": "high"}) is True
    assert kitchen_add.is_sure({"item": "Greek yogurt", "confidence": "low"}) is False
    for vague in ("Hot sauce?", "unknown jar", "possibly pesto", "milk or cream", "container", "Sauce", ""):
        assert kitchen_add.is_sure({"item": vague}) is False, vague


def test_the_scan_prompt_asks_for_the_flag_the_review_reads():
    """GUARD — the scan tool's schema requires `confidence`, and says what
    'low' does now."""
    from app import agent
    props = agent._SCAN_ITEMS_TOOL["input_schema"]["properties"]["items"]["items"]
    assert "confidence" in props["required"]
    assert "UNTICKED" in props["properties"]["confidence"]["description"]


def test_say_it_reads_a_sentence_into_a_draft_and_saves_nothing(signed_in):
    """CATCH — "Two litres of milk and a dozen eggs." -> two rows, ticked,
    on the shelf each usually lives on. Nothing is saved."""
    res = signed_in.post("/api/whats-home/read-said", json={"text": "Two litres of milk and a dozen eggs."})
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [(i["item"], i["quantity"], i["sure"]) for i in items] == [
        ("Milk", "2 litres", True), ("Eggs", "12", True)]
    assert items[0]["location"] == "fridge"
    said = kitchen_add.read_said_items("chicken thighs in the freezer, a loaf of bread")
    assert [(i["item"], i["quantity"], i["location"]) for i in said] == [
        ("Chicken thighs", "", "freezer"), ("Bread", "1 loaf", "pantry")]
    assert _count() == 0


# ---------- the server: the one write, and its Undo ----------

def _seed(item, quantity, location="fridge", source="grocery_checkoff", category="dairy"):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, category, location, created_at) "
        "VALUES (1, ?, ?, ?, ?, ?, '2026-10-01 12:00:00')", (item, quantity, source, category, location))
    conn.commit()
    conn.close()
    return cur.lastrowid


def _get(item_id):
    conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def test_a_shelf_photo_counts_what_is_there_and_never_doubles_it(signed_in):
    """CATCH (verify issue 1) — shop-ticked Milk 2 L + a fridge photo of
    Milk 2 L stays 2 L (it used to become 4 L, then 6 L); the row is
    confirmed (counted today), keeps its name and its bought date; a
    scanned "milk 2%" is the same Milk; unmatched rows are added."""
    from conftest import household_today
    milk = _seed("Milk", "2 L")
    photo = [{"item": "milk 2%", "quantity": "2 L", "category": "dairy", "location": "fridge"},
             {"item": "Eggs", "quantity": "6", "category": "dairy", "location": "fridge"}]
    first = signed_in.post("/api/whats-home/add", json={"kind": "fridge", "items": photo}).json()
    assert (first["added"], first["updated"]) == (1, 1)
    again = signed_in.post("/api/whats-home/add", json={"kind": "fridge", "items": photo}).json()
    assert (again["added"], again["updated"]) == (0, 2)
    row = _get(milk)
    assert (row["item"], row["quantity"]) == ("Milk", "2 L")
    assert row["counted_on"] == household_today().isoformat()
    assert row["created_at"] == "2026-10-01 12:00:00" and row["source"] == "grocery_checkoff"
    assert [(r["item"], r["quantity"]) for r in _rows()] == [("Milk", "2 L"), ("Eggs", "6")]
    # A photo that read no amount confirms the row and keeps its amount.
    signed_in.post("/api/whats-home/add", json={"kind": "fridge", "items": [{"item": "Milk", "location": "fridge"}]})
    assert _get(milk)["quantity"] == "2 L"


def test_a_purchase_still_adds_and_lands_on_the_tracked_name(signed_in):
    """CATCH — a receipt / Say it is a purchase: it adds, onto the row
    already tracked even when the scan wrote "milk 2%"."""
    milk = _seed("Milk", "1 L")
    signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [{"item": "milk 2%", "quantity": "1 L", "category": "dairy"}]})
    assert [(r["item"], r["quantity"]) for r in _rows()] == [("Milk", "2 L")]
    assert _get(milk)["quantity"] == "2 L"


def test_add_writes_only_the_rows_sent_and_undo_takes_them_back(signed_in):
    """CATCH — the rows sent land on the photo's shelf (a frozen one in the
    freezer), recorded as that scan; Undo puts a confirmed row back as it
    was and takes the new ones off."""
    _seed("Milk", "1 L", source="chat")
    res = signed_in.post("/api/whats-home/add", json={"kind": "fridge", "items": [
        {"item": "Milk", "quantity": "3 L", "category": "dairy", "location": "fridge"},
        {"item": "Eggs", "quantity": "6", "category": "dairy", "location": ""},
        {"item": "Peas", "quantity": "", "category": "frozen", "location": "freezer"},
    ]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["added"], body["updated"], body["place"]) == (2, 1, "fridge")
    rows = {r["item"]: r for r in _rows()}
    assert rows["Milk"]["quantity"] == "3 L" and rows["Milk"]["source"] == "chat"
    assert rows["Eggs"]["location"] == "fridge" and rows["Eggs"]["source"] == "scan_fridge"
    assert rows["Peas"]["location"] == "freezer"
    assert set(body["undo"]) == {"kind", "receipt_id"}, "the toast holds an id, nothing else"
    undo = signed_in.post("/api/whats-home/add/undo", json={"undo": body["undo"]})
    assert undo.status_code == 200 and undo.json()["undone"] == 3
    assert [(r["item"], r["quantity"], r["source"]) for r in _rows()] == [("Milk", "1 L", "chat")]
    # Once only.
    assert signed_in.post("/api/whats-home/add/undo", json={"undo": body["undo"]}).json()["undone"] == 0


def test_undo_only_accepts_an_id_and_never_a_client_payload(signed_in):
    """CATCH (verify issue 2) — the old payload (rows with fresh/before)
    is refused; an id from another household or a made-up id is refused."""
    rice = _seed("Rice", "1 bag", location="pantry", category="pantry")
    forged = {"kind": "added", "rows": [{"item_id": rice, "fresh": False,
                                         "before": {"quantity": "999 tonnes", "expiration_date": "not-a-date"}}]}
    assert signed_in.post("/api/whats-home/add/undo", json={"undo": forged}).status_code == 400
    assert signed_in.post("/api/whats-home/add/undo", json={"undo": {"kind": "added", "receipt_id": 999}}).status_code == 400
    deleting = {"kind": "added", "rows": [{"item_id": rice, "fresh": True}]}
    assert signed_in.post("/api/whats-home/add/undo", json={"undo": deleting}).status_code == 400
    assert _get(rice)["quantity"] == "1 bag"


def test_type_it_saves_one_thing_on_the_shelf_picked_and_reads_its_amount(signed_in):
    """CATCH (verify issue 7) — Type it saves straight away, and a leading
    amount comes off the name: "2 litres of milk" -> Milk · 2 litres."""
    res = signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "Greek yogurt", "location": "pantry"}]})
    assert res.status_code == 200, res.text
    assert res.json()["place"] == "pantry"
    signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "2 litres of milk", "location": "fridge"}]})
    rows = [(r["item"], r["quantity"], r["location"], r["source"]) for r in _rows()]
    assert rows == [("Greek yogurt", "", "pantry", kitchen_add.TYPED_SOURCE),
                    ("Milk", "2 litres", "fridge", kitchen_add.TYPED_SOURCE)]


def test_a_receipt_and_a_sentence_record_their_shelf_as_a_guess(signed_in):
    """CATCH — a receipt names food, never a shelf, and so does a sentence:
    the freezer check (defrost) must not read either shelf as stated."""
    from app.tools import inventory
    signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [{"item": "Chicken thighs", "category": "meat/seafood"}]})
    signed_in.post("/api/whats-home/add", json={"kind": "say", "items": [{"item": "ground beef", "location": "fridge"}]})
    sources = [r["source"] for r in _rows()]
    assert sources == ["scan", kitchen_add.SAID_SOURCE]
    assert all(s in inventory.GUESSED_LOCATION_SOURCES for s in sources)


@pytest.mark.parametrize("items", [
    [],
    [{"item": "x" * 81}],
    [{"item": {"nested": 1}}],
    [{"item": 42}],
    [{"item": "Milk", "quantity": "-3"}],
    [{"item": "Milk", "quantity": -3}],
    [{"item": "Rice", "quantity": "999999 kg"}],
    [{"item": "Milk", "quantity": "1" * 41}],
    [{"item": "Milk", "quantity": {"a": 1}}],
    [{"item": f"Thing {i}"} for i in range(81)],
])
def test_add_refuses_junk_with_a_400_and_writes_nothing(signed_in, items):
    """CATCH (verify issue 3) — names are strings of at most 80 characters,
    amounts are short and not negative or absurd, at most 80 rows."""
    res = signed_in.post("/api/whats-home/add", json={"kind": "fridge", "items": items})
    assert res.status_code == 400, res.text
    assert _count() == 0


def test_add_refuses_an_unknown_door(signed_in):
    """GUARD — a 400 in words, nothing written."""
    assert signed_in.post("/api/whats-home/add", json={"kind": "garage", "items": [{"item": "Milk"}]}).status_code == 400
    assert signed_in.post("/api/whats-home/add/undo", json={"undo": {"kind": "removed"}}).status_code == 400
    assert _count() == 0


def test_undo_leaves_a_row_somebody_changed_since(signed_in):
    """GUARD — the Undo is for this add, not whatever came after it."""
    body = signed_in.post("/api/whats-home/add", json={"kind": "pantry", "items": [{"item": "Rice"}]}).json()
    conn = get_conn()
    conn.execute("UPDATE inventory_items SET quantity = 'a little' WHERE item = 'Rice'")
    conn.commit()
    conn.close()
    assert signed_in.post("/api/whats-home/add/undo", json={"undo": body["undo"]}).json()["undone"] == 0
    assert _count() == 1


def test_a_pantry_scan_names_the_spices_and_regulars_it_leaves_out(signed_in, monkeypatch):
    """CATCH (verify item 8) — the Kitchen tab never shows spices or the
    household's regulars, so the review leaves them out and SAYS so."""
    from app import tools
    tools.add_staple("Coffee") if hasattr(tools, "add_staple") else None
    monkeypatch.setattr(main, "scan_pantry_photo", lambda b64, mt: [
        {"item": "Rice", "category": "pantry", "confidence": "high"},
        {"item": "cumin", "category": "pantry", "confidence": "high"},
        {"item": "Coffee", "category": "pantry", "confidence": "high"},
    ])
    res = signed_in.post("/api/inventory/scan-pantry", files={"photo": ("p.jpg", _TINY_JPEG, "image/jpeg")}).json()
    assert [i["item"] for i in res["items"]] == ["Rice"]
    assert res["left_out"]["spices"] == ["Cumin"]
    assert res["left_out"]["regulars"] == ["Coffee"]


def test_say_it_keeps_named_dishes_whole_and_splits_a_comma_less_list_of_foods():
    """CATCH (verify issue 6) — "mac and cheese" / "fish and chips" stay one
    row; "salt and pepper" goes in the pantry; "milk eggs butter" (dictation
    with no commas) is three rows; anything less certain stays as said."""
    rows = kitchen_add.read_said_items("mac and cheese, fish and chips and salt and pepper")
    assert [r["item"] for r in rows] == ["Mac and cheese", "Fish and chips", "Salt and pepper"]
    assert rows[2]["location"] == "pantry"
    assert [r["item"] for r in kitchen_add.read_said_items("milk eggs butter")] == ["Milk", "Eggs", "Butter"]
    assert [r["item"] for r in kitchen_add.read_said_items("baby spinach")] == ["Baby spinach"]
    assert [r["item"] for r in kitchen_add.read_said_items("leftover thai curry")] == ["Leftover thai curry"]


def test_undo_puts_back_a_row_one_save_touched_twice(signed_in):
    """CATCH (re-verify R1) — Milk 4 L; one Say it save of "Milk" 1 L and
    "milk 2%" 1 L takes it to 6 L; Undo puts it back to 4 L, not 5 L."""
    milk = _seed("Milk", "4 L", source="chat")
    body = signed_in.post("/api/whats-home/add", json={"kind": "say", "items": [
        {"item": "Milk", "quantity": "1 L", "location": "fridge"},
        {"item": "milk 2%", "quantity": "1 L", "location": "fridge"},
    ]}).json()
    assert _get(milk)["quantity"] == "6 L"
    assert signed_in.post("/api/whats-home/add/undo", json={"undo": body["undo"]}).json()["undone"] == 1
    assert (_get(milk)["quantity"], _get(milk)["source"]) == ("4 L", "chat")


def test_a_photo_with_no_amount_confirms_but_is_not_a_count(signed_in):
    """CATCH (re-verify R2) — counted_on is what the stock check trusts
    (recipes._KitchenStock), so a photo that SAW the tortillas but read no
    amount keeps the amount and stamps no count; one that read an amount
    is a count."""
    from conftest import household_today
    tortillas = _seed("Tortillas", "1 pack", location="pantry", category="pantry")
    signed_in.post("/api/whats-home/add", json={"kind": "pantry", "items": [{"item": "Tortillas", "location": "pantry"}]})
    row = _get(tortillas)
    assert (row["quantity"], row["counted_on"]) == ("1 pack", None)
    signed_in.post("/api/whats-home/add", json={"kind": "pantry", "items": [{"item": "Tortillas", "quantity": "2 packs"}]})
    row = _get(tortillas)
    assert (row["quantity"], row["counted_on"]) == ("2 packs", household_today().isoformat())


def test_a_photo_with_no_amount_stamps_confirmed_on_where_the_column_exists(signed_in):
    """CATCH (re-verify R2) — with the pre-draft check's confirmed_on column
    present (read through PRAGMA, not assumed), "seen" is stamped there, and
    Undo takes it back."""
    from conftest import household_today
    conn = get_conn()
    conn.execute("ALTER TABLE inventory_items ADD COLUMN confirmed_on TEXT")
    conn.commit()
    conn.close()
    try:
        tortillas = _seed("Tortillas", "1 pack", location="pantry", category="pantry")
        body = signed_in.post("/api/whats-home/add", json={"kind": "pantry", "items": [{"item": "Tortillas"}]}).json()
        row = _get(tortillas)
        assert (row["counted_on"], row["confirmed_on"]) == (None, household_today().isoformat())
        signed_in.post("/api/whats-home/add/undo", json={"undo": body["undo"]})
        assert _get(tortillas)["confirmed_on"] is None
    finally:
        conn = get_conn()
        conn.execute("ALTER TABLE inventory_items DROP COLUMN confirmed_on")
        conn.commit()
        conn.close()


def test_the_review_shows_what_a_photo_would_change(signed_in, monkeypatch):
    """CATCH (re-verify R3) — a shelf photo replaces a tracked amount, so
    the review says what it was: "Eggs · 1 (was 6)". Same amount, a new
    food, or a receipt: nothing to say."""
    _seed("Eggs", "6")
    _seed("Milk", "2 L")
    monkeypatch.setattr(main, "scan_fridge_photo", lambda b64, mt: [
        {"item": "eggs", "quantity": "1", "category": "dairy", "location": "fridge"},
        {"item": "Milk", "quantity": "2 L", "category": "dairy", "location": "fridge"},
        {"item": "Butter", "quantity": "1 block", "category": "dairy", "location": "fridge"},
    ])
    items = signed_in.post("/api/inventory/scan-fridge", files={"photo": ("f.jpg", _TINY_JPEG, "image/jpeg")}).json()["items"]
    assert [(i["item"], i.get("was")) for i in items] == [("Eggs", "6"), ("Milk", None), ("Butter", None)]
    monkeypatch.setattr(main, "scan_receipt_image", lambda b64, mt: [{"item": "Eggs", "quantity": "12", "category": "dairy"}])
    receipt = signed_in.post("/api/inventory/scan-receipt", files={"photo": ("r.jpg", _TINY_JPEG, "image/jpeg")}).json()["items"]
    assert "was" not in receipt[0]
    if shutil.which("node"):
        html = _ka("k.htmlFor({step: 'review', kind: 'fridge', rows: k.reviewRows([{item: 'Eggs', quantity: '1', was: '6'}])})")
        assert '<span class="ka-row-qty">1 (was 6)</span>' in html


def test_say_it_splits_a_comma_less_piece_after_an_and_and_keeps_two_word_foods():
    """CATCH (re-verify minor) — "milk eggs and butter" is three rows;
    "orange juice" and "cream cheese" stay one food each."""
    assert [r["item"] for r in kitchen_add.read_said_items("milk eggs and butter")] == ["Milk", "Eggs", "Butter"]
    assert [r["item"] for r in kitchen_add.read_said_items("orange juice and cream cheese")] == ["Orange juice", "Cream cheese"]
    assert [r["item"] for r in kitchen_add.read_said_items("chicken thighs, baby spinach")] == ["Chicken thighs", "Baby spinach"]


# ---------- C5: the Kitchen in first-run setup ----------

def _intro_markup() -> str:
    start = ONBOARDING.index('<div id="kitchen-intro"')
    return ONBOARDING[start: ONBOARDING.index('<div id="step-reveal"', start)]


def test_onboarding_introduces_the_kitchen_with_set_up_later():
    """CATCH — one screen in the welcome-flow look that says what the
    Kitchen does, a photo-first primary, and a clear "Set up later"."""
    html = _intro_markup()
    assert 'class="intro-screen"' in html and 'class="intro-title is-lead"' in html
    assert "I&rsquo;ll keep track of what&rsquo;s home." in html
    assert "plan around whatever needs using up first" in html
    assert '<button class="intro-next" id="kitchen-intro-start" type="button">Take three photos</button>' in html
    assert '<button class="kitchen-intro-later" id="kitchen-intro-later" type="button">Set up later</button>' in html
    assert html.count('class="intro-next"') == 1, "one apricot"


def test_the_kitchen_step_asks_for_no_typing():
    """CATCH — no field on the step, and its photos are the setup mode of
    the same sheet: three shelves, each skippable, no text box."""
    html = _intro_markup()
    assert "<input" not in html and "<textarea" not in html
    js = KA_JS_PATH.read_text()
    setup = js[js.index("function setupHtml()"): js.index("function render()")]
    assert "<input" not in setup and "<textarea" not in setup
    assert ">Skip the ' + next" in setup and "Take a photo of the ' + next" in setup


@_needs_node
def test_the_setup_sheet_is_the_mockups_three_photos():
    """CATCH — OptionC screen 2: Fridge done with its count, Freezer up
    next with the camera, Pantry next; Skip the freezer; the receipt line."""
    html = _ka("k.htmlFor({mode: 'setup', step: 'setup', setup: {fridge: 14, freezer: null, pantry: null}})")
    assert "Kitchen · getting started" in html
    assert "Show me <em>your kitchen</em>" in html and "Three photos. About two minutes." in html
    assert re.findall(r'<p class="ka-step-title">(\w+)</p><p class="ka-step-line">([^<]*)</p>', html) == [
        ("Fridge", "14 things added"), ("Freezer", "Open the door and take one photo"), ("Pantry", "Next")]
    assert "Take a photo of the freezer" in html and "Skip the freezer" in html
    assert "Or scan your last receipt instead." not in html  # only before the first shelf
    first = _ka("k.htmlFor({mode: 'setup', step: 'setup'})")
    assert "Or scan your last receipt instead." in first
    done = _ka("k.htmlFor({mode: 'setup', step: 'setup', opts: {doneLabel: 'Draft my first week'}, "
               "setup: {fridge: 14, freezer: 'skipped', pantry: 3}})")
    assert "Skipped" in done and ">Draft my first week</button>" in done and "Skip the" not in done


def _onb_fn(name: str, is_async: bool = False) -> str:
    head = ("async function " if is_async else "function ") + name + "("
    start = ONBOARDING.index(head)
    return ONBOARDING[start: ONBOARDING.index("\n}\n", start) + 3]


# The finish and the Kitchen step, run under node with every save, the
# draft and the DOM stood in for: what is recorded is the ORDER things
# happen in, which is the claim.
def _finish_harness(scenario: str, kitchen_count: int = 0, plan: bool = True):
    script = "\n".join([
        """
const LOG = [];
const ELS = {};
function el(id) { return ELS[id] || (ELS[id] = { id: id, hidden: true, focus() {}, onclick: null }); }
const document = { getElementById: el };
const window = { KitchenAdd: { open(o) { LOG.push('sheet:' + o.mode); window._opts = o; } }, scrollTo() {} };
const Api = { json(path) { LOG.push('GET ' + path); return Promise.resolve({ count: %d }); } };
function currentMembers() { return [{ name: 'Emily' }]; }
function showStep(k) { LOG.push('show:' + k); }
function setKitRepeatsBusy() {}
async function saveHouseholdMembers() { LOG.push('save'); }
async function saveRhythmAnswers() {}
async function saveOnboardingAnswers() { return {}; }
async function savePlanTheWeekAnswers() {}
function clearOnboardingDraft() {}
async function generateFirstPlanAndReveal() { LOG.push('draft'); }
function alert() {}
""" % kitchen_count,
        "const KITCHEN_CHECK_WAIT_MS = 3000;",
        "let kitchenStepOffered = false;",
        "let kitchenStepResolve = null;",
        _onb_fn("kitchenStepBeforeDraft"),
        _onb_fn("closeKitchenIntro"),
        "let setupRun = 'idle';",
        _onb_fn("finishSetupAndReveal", is_async=True),
        """
const tick = () => new Promise(r => setTimeout(r, 0));
(async () => {
  const run = finishSetupAndReveal(%s);
  for (let i = 0; i < 5; i++) await tick();
  const shownWhileWaiting = !el('kitchen-intro').hidden;
  const beforeTap = LOG.slice();
  %s
  await run;
  console.log(JSON.stringify({ shownWhileWaiting, beforeTap, log: LOG, hiddenAfter: el('kitchen-intro').hidden }));
})();
""" % ("{ plan: false }" if not plan else "", scenario),
    ])
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@_needs_node
def test_the_draft_starts_only_after_the_kitchen_step_is_set_up_later():
    """CATCH — the step sits right before the first week is drafted, so the
    week can use what's home: nothing is saved and no week is asked for
    while it shows; "Set up later" goes straight on to saving and drafting."""
    out = _finish_harness("closeKitchenIntro();")
    assert out["shownWhileWaiting"] is True
    assert out["beforeTap"] == ["GET /api/whats-home"], "no save, no draft before the step is answered"
    assert out["log"] == ["GET /api/whats-home", "save", "show:reveal", "draft"]
    assert out["hiddenAfter"] is True


@_needs_node
def test_the_draft_starts_after_the_photos_are_done():
    """CATCH — "Take three photos" opens the setup sheet; its finish (onDone)
    is what lets the draft start, so the photos are home before the week."""
    out = _finish_harness("window.KitchenAdd.open({mode: 'setup'}); "
                          "LOG.push('photos added'); closeKitchenIntro();")
    assert out["log"].index("photos added") < out["log"].index("draft")
    wiring = ONBOARDING[ONBOARDING.index("document.getElementById('kitchen-intro-start').onclick"):]
    wiring = wiring[: wiring.index("\n};\n")]
    assert "mode: 'setup'" in wiring and "onDone: () => { closeKitchenIntro(); }" in wiring
    assert "doneLabel: 'Draft my first week'" in wiring


@_needs_node
def test_the_step_is_skipped_for_a_stocked_kitchen_and_on_not_now():
    """CATCH/GUARD — a kitchen with things in it goes straight to the draft;
    "Not now" to Claude drafts nothing and never shows the step."""
    stocked = _finish_harness("", kitchen_count=4)
    assert stocked["shownWhileWaiting"] is False
    assert stocked["log"] == ["GET /api/whats-home", "save", "show:reveal", "draft"]
    not_now = _finish_harness("", plan=False)
    assert not_now["shownWhileWaiting"] is False
    assert not_now["log"] == ["save"]


def test_set_up_later_only_moves_on():
    """CATCH — "Set up later" saves nothing of its own and goes nowhere but
    on: it hides the screen and lets the finish carry on."""
    later = ONBOARDING[ONBOARDING.index("document.getElementById('kitchen-intro-later').onclick"):]
    later = later[: later.index("\n")]
    assert later.strip() == "document.getElementById('kitchen-intro-later').onclick = () => { closeKitchenIntro(); };"
    close = _onb_fn("closeKitchenIntro")
    assert "Api." not in close and "location" not in close and "finishSetupAndReveal" not in close
    # It is not a question step, and the finish keeps its four callers.
    steps = re.search(r"const ALL_STEPS = \[([^\]]*)\]", ONBOARDING).group(1)
    assert "kitchen" not in steps
    assert ONBOARDING.count("finishSetupAndReveal(") == 4
    assert ONBOARDING.index('src="/static/api.js"') < ONBOARDING.index('src="/static/kitchen-add.js"')
    # Its markup is outside the reveal, which is hidden while it shows.
    assert ONBOARDING.index('<div id="kitchen-intro"') < ONBOARDING.index('<div id="step-reveal"')
    assert "Your first week can use what&rsquo;s already here." in _intro_markup()
