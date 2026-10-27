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

def test_add_writes_only_the_rows_sent_on_the_photos_shelf_and_undo_takes_them_back(signed_in):
    """CATCH — the review's button: the rows sent land on the shelf the
    photo was of (a frozen one in the freezer), recorded as that scan;
    Undo takes a fresh row off and puts a merged one back as it was."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO inventory_items (household_id, item, quantity, source, category, location) "
        "VALUES (1, 'Milk', '1 L', 'chat', 'dairy', 'fridge')")
    conn.commit()
    conn.close()
    res = signed_in.post("/api/whats-home/add", json={"kind": "fridge", "items": [
        {"item": "Milk", "quantity": "1 L", "category": "dairy", "location": "fridge"},
        {"item": "Eggs", "quantity": "6", "category": "dairy", "location": ""},
        {"item": "Peas", "quantity": "", "category": "frozen", "location": "freezer"},
    ]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["added"] == 3 and body["place"] == "fridge"
    rows = {r["item"]: r for r in _rows()}
    assert rows["Milk"]["quantity"] == "2 L" and rows["Milk"]["source"] == "scan_fridge"
    assert rows["Eggs"]["location"] == "fridge" and rows["Eggs"]["source"] == "scan_fridge"
    assert rows["Peas"]["location"] == "freezer"

    undo = signed_in.post("/api/whats-home/add/undo", json={"undo": body["undo"]})
    assert undo.status_code == 200 and undo.json()["undone"] == 3
    after = _rows()
    assert [(r["item"], r["quantity"], r["source"]) for r in after] == [("Milk", "1 L", "chat")]


def test_type_it_saves_one_thing_on_the_shelf_picked(signed_in):
    """CATCH — Type it is what the person typed: saved straight away."""
    res = signed_in.post("/api/whats-home/add", json={"kind": "type", "items": [{"item": "Greek yogurt", "location": "pantry"}]})
    assert res.status_code == 200, res.text
    assert res.json()["place"] == "pantry"
    [row] = _rows()
    assert (row["item"], row["location"], row["source"]) == ("Greek yogurt", "pantry", kitchen_add.TYPED_SOURCE)


def test_a_receipt_and_a_sentence_record_their_shelf_as_a_guess(signed_in):
    """CATCH — a receipt names food, never a shelf, and so does a sentence:
    the freezer check (defrost) must not read either shelf as stated."""
    from app.tools import inventory
    signed_in.post("/api/whats-home/add", json={"kind": "receipt", "items": [{"item": "Chicken thighs", "category": "meat/seafood"}]})
    signed_in.post("/api/whats-home/add", json={"kind": "say", "items": [{"item": "ground beef", "location": "fridge"}]})
    sources = [r["source"] for r in _rows()]
    assert sources == ["scan", kitchen_add.SAID_SOURCE]
    assert all(s in inventory.GUESSED_LOCATION_SOURCES for s in sources)


def test_add_refuses_nothing_ticked_and_an_unknown_door(signed_in):
    """GUARD — a 400 in words, nothing written."""
    assert signed_in.post("/api/whats-home/add", json={"kind": "fridge", "items": []}).status_code == 400
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


# ---------- C5: the Kitchen in first-run setup ----------

def _intro_markup() -> str:
    start = ONBOARDING.index('<div id="kitchen-intro"')
    return ONBOARDING[start: ONBOARDING.index("<!-- The title claims a week only once one exists.", start)]


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
    done = _ka("k.htmlFor({mode: 'setup', step: 'setup', opts: {doneLabel: 'See my first week'}, "
               "setup: {fridge: 14, freezer: 'skipped', pantry: 3}})")
    assert "Skipped" in done and ">See my first week</button>" in done and "Skip the" not in done


def test_the_kitchen_step_never_holds_up_the_first_plan():
    """CATCH — it is offered as the first week starts drafting, not awaited,
    and only on the path that drafts one (after the yes to Claude)."""
    finish = ONBOARDING[ONBOARDING.index("async function finishSetupAndReveal("):]
    finish = finish[: finish.index("\n}\n")]
    i_reveal = finish.index("showStep('reveal');\n    // The Kitchen step")
    i_offer = finish.index("if (typeof offerKitchenSetup === 'function') offerKitchenSetup();")
    i_gen = finish.index("await generateFirstPlanAndReveal(answers);")
    assert finish.index("if (!plan) return true;") < i_reveal < i_offer < i_gen
    assert "await offerKitchenSetup" not in ONBOARDING
    # The step list is unchanged: it is not a question in front of the week.
    steps = re.search(r"const ALL_STEPS = \[([^\]]*)\]", ONBOARDING).group(1)
    assert "kitchen" not in steps


def test_set_up_later_continues_with_no_penalty():
    """CATCH — "Set up later" only uncovers the reveal: it saves nothing,
    finishes nothing and goes nowhere, so the week carries on as it was."""
    later = ONBOARDING[ONBOARDING.index("document.getElementById('kitchen-intro-later').onclick"):]
    later = later[: later.index("\n")]
    assert later.strip() == "document.getElementById('kitchen-intro-later').onclick = () => { closeKitchenIntro(); };"
    close = ONBOARDING[ONBOARDING.index("function closeKitchenIntro()"):]
    close = close[: close.index("\n}\n")]
    assert "Api." not in close and "location" not in close and "finishSetupAndReveal" not in close
    # The photos open the shared sheet in setup mode; finishing them is the
    # same uncovering.
    start = ONBOARDING[ONBOARDING.index("document.getElementById('kitchen-intro-start').onclick"):]
    start = start[: start.index("\n};\n")]
    assert "mode: 'setup'" in start and "onDone: () => { closeKitchenIntro(); }" in start
    assert ONBOARDING.index('src="/static/api.js"') < ONBOARDING.index('src="/static/kitchen-add.js"')


def test_the_step_is_skipped_for_a_kitchen_that_already_has_things():
    """GUARD — a re-run of setup on a stocked kitchen is not a first run."""
    offer = ONBOARDING[ONBOARDING.index("async function offerKitchenSetup()"):]
    offer = offer[: offer.index("\n}\n")]
    assert "Api.json('/api/whats-home', { quiet: true })" in offer
    assert "if (home && home.count > 0) return;" in offer
