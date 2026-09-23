"""
"Maybe already home" rides on the row it is about, and its buttons say
what they do.

Two Loop Board cards, one branch, because they edit one function:

  3e31f4c0-5231-81d8 (High) — Emily, 2026-09-22, sorting her list with a
  screenshot of Shop: "this suggestion was good, but it was hidden so
  much I didn't even notice it when I was sorting my grocery list. It's
  something we need to bring to the user's attention while they're doing
  through the list."

  3e31f4c0-5231-818c (Medium) — "Buy it anyway" -> "Still need to buy",
  "Drop it" -> "Remove from list", the helper line cut (she confirmed
  2026-09-23), the foot reading "Keep all 2 on the list".

WHAT WAS ACTUALLY WRONG was one step worse than the card describes.
/api/grocery-list and /api/grocery-list/by-store FILTERED every flagged
id out of their "needed" views, so the pinned banner was not merely the
first place the flag appeared — it was the only place the LINE appeared
at all. The flagged carrot was off its store card and off "Sort them
all" entirely, so no row anywhere could have carried the question.

Tests marked CATCH are red on the parent commit; GUARD ones are green
there and pin something the change must not break.
"""
from __future__ import annotations

from pathlib import Path

from shop_harness import needs_node, run
from app import tools

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _need(item: str, qty: str, category: str = "produce") -> int:
    return tools.add_grocery_item(item, quantity=qty, category=category)["item_id"]


def _have(item: str, qty: str, category: str = "produce") -> None:
    tools.update_inventory(item, "add", quantity=qty, category=category)


def _flagged_carrots() -> int:
    """A line the kitchen can be shown to cover, plus one it can't — so
    every test below also proves the stamp lands on the RIGHT row."""
    carrots = _need("Carrots", "2 lbs", "produce")
    _have("Carrots", "2 lbs", "produce")
    _need("Lemons", "3", "produce")
    return carrots


def _rows(payload: dict) -> dict:
    """Every line in a sections payload, by name."""
    return {it["item"]: it for s in payload["sections"] for it in s["items"]}


def _store_rows(payload: dict) -> dict:
    return {
        it["item"]: it
        for store in payload["stores"]
        for s in store["sections"]
        for it in s["items"]
    }


# ------------------------------------------------- the line stays on the list

def test_a_flagged_line_stays_on_the_needed_list_and_carries_its_flag(signed_in):
    """CATCH. Before this, the needed view filtered the id out entirely —
    `on_list` came back without Carrots at all, so no row could have
    carried the flag."""
    _flagged_carrots()

    rows = _rows(signed_in.get("/api/grocery-list?status=needed").json())

    assert set(rows) == {"Carrots", "Lemons"}
    assert rows["Carrots"]["pre_shop"]["sentence"] == "You want 2 lbs. Fridge shows 2 lbs."
    assert "pre_shop" not in rows["Lemons"], "an unflagged line carries no flag"


def test_the_by_store_view_stamps_the_same_flag(signed_in):
    """CATCH — the Shop tab reads by-store, which filtered the same id."""
    _flagged_carrots()

    rows = _store_rows(signed_in.get("/api/grocery-list/by-store?status=needed").json())

    assert set(rows) == {"Carrots", "Lemons"}
    assert rows["Carrots"]["pre_shop"]["sentence"] == "You want 2 lbs. Fridge shows 2 lbs."


def test_the_row_and_the_banner_say_the_same_sentence(signed_in):
    """GUARD on the thing that makes two places safe: both are the same
    get_pre_shop_flags() read, so they cannot drift."""
    _flagged_carrots()

    rows = _rows(signed_in.get("/api/grocery-list?status=needed").json())
    banner = signed_in.get("/api/grocery-list/pre-shop-flags").json()["flags"]

    assert len(banner) == 1
    assert rows["Carrots"]["pre_shop"]["sentence"] == banner[0]["sentence"]
    assert rows["Carrots"]["pre_shop"]["onHandLabel"] == banner[0]["onHandLabel"]
    assert rows["Carrots"]["pre_shop"]["wantedLabel"] == banner[0]["wantedLabel"]


# -------------------------------------------------------- one decision, once

def test_still_need_to_buy_from_a_row_clears_the_flag_everywhere(signed_in):
    """The keep path. Same write from either door; afterwards the line is
    an ordinary row and the banner has nothing left to show. CATCH on the
    row half (the row did not exist), GUARD on the banner half."""
    carrots = _flagged_carrots()

    res = signed_in.post(f"/api/grocery-list/{carrots}/pre-shop", json={"decision": "keep", "author": "user"})
    assert res.status_code == 200

    rows = _rows(signed_in.get("/api/grocery-list?status=needed").json())
    assert set(rows) == {"Carrots", "Lemons"}, "kept means kept — still to buy"
    assert "pre_shop" not in rows["Carrots"], "never asked twice"
    assert signed_in.get("/api/grocery-list/pre-shop-flags").json()["flags"] == []


def test_remove_from_list_from_a_row_takes_the_line_and_the_flag(signed_in):
    carrots = _flagged_carrots()

    res = signed_in.post(f"/api/grocery-list/{carrots}/pre-shop", json={"decision": "drop", "author": "user"})
    assert res.status_code == 200

    assert set(_rows(signed_in.get("/api/grocery-list?status=needed").json())) == {"Lemons"}
    assert signed_in.get("/api/grocery-list/pre-shop-flags").json()["flags"] == []


def test_undo_puts_the_line_back_without_asking_again(signed_in):
    """GUARD — undo_pre_shop_drop marks the line reviewed, so the row
    comes back unflagged rather than re-flagged the moment it lands."""
    carrots = _flagged_carrots()
    signed_in.post(f"/api/grocery-list/{carrots}/pre-shop", json={"decision": "drop", "author": "user"})

    res = signed_in.post(f"/api/grocery-list/{carrots}/pre-shop-undo")
    assert res.status_code == 200

    rows = _rows(signed_in.get("/api/grocery-list?status=needed").json())
    assert set(rows) == {"Carrots", "Lemons"}
    assert "pre_shop" not in rows["Carrots"]
    assert signed_in.get("/api/grocery-list/pre-shop-flags").json()["flags"] == []


def test_keep_all_clears_every_row_stamp_in_one_write(signed_in):
    _flagged_carrots()
    _need("Butter", "1 stick", "dairy")
    _have("Butter", "2 sticks", "dairy")
    assert len(signed_in.get("/api/grocery-list/pre-shop-flags").json()["flags"]) == 2

    signed_in.post("/api/grocery-list/pre-shop/keep-all")

    rows = _rows(signed_in.get("/api/grocery-list?status=needed").json())
    assert set(rows) == {"Carrots", "Lemons", "Butter"}
    assert all("pre_shop" not in r for r in rows.values())


# ------------------------------------------------------------- what draws it

_FLAGGED_ROW = """
const FLAG = { sentence: 'You want 2 lbs. Fridge shows 2 lbs.',
  wantedLabel: '2 lbs', onHandLabel: '2 lbs', onHandLocation: 'fridge' };
function flaggedRow(id, name, extra) {
  const row = { id: id, item: name || 'Carrots', quantity: '2 lbs', store: '',
    store_decided: 0, category: 'produce', status: 'needed', pre_shop: FLAG };
  Object.keys(extra || {}).forEach(function (k) { row[k] = extra[k]; });
  return row;
}
"""


@needs_node
def test_the_store_card_row_carries_the_flag_with_both_buttons():
    """CATCH — groLineHtml drew nothing but the tick, the name and the ⋯."""
    out = run(_FLAGGED_ROW + """
groceryState.data = { stores: { Unassigned: { sections: [], purchased: [], inCart: [] },
  Costco: { sections: [{ section: 'produce', items: [flaggedRow(7)] }], purchased: [], inCart: [] } } };
groceryState.usualStores = ['Costco', 'Loblaws'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'list';
console.log(JSON.stringify({ list: groListHtml(groceryState.data) }));
""")
    html = out["list"]
    assert 'class="gro-athome" data-athome-for="7"' in html
    assert "You want 2 lbs. Fridge shows 2 lbs." in html
    assert "Maybe already home" in html
    assert 'data-gro="ps-decide" data-decision="keep" data-id="7"' in html
    assert 'data-gro="ps-decide" data-decision="drop" data-id="7"' in html
    assert "Still need to buy" in html and "Remove from list" in html
    # It rides along, it does not gate: the row is still the tick.
    assert 'class="gro-row gro-line' in html and 'data-gro="line-tick" data-id="7"' in html


@needs_node
def test_sort_them_all_carries_the_flag_above_the_store_chips():
    """CATCH — the exact moment Emily was deciding, and the row said
    nothing. Above the chips, because the kitchen's answer is worth
    reading before choosing a shop."""
    out = run(_FLAGGED_ROW + """
groceryState.data = { stores: { Unassigned: { sections: [{ section: 'produce',
  items: [flaggedRow(7), { id: 8, item: 'Lemons', quantity: '3', store: '',
    store_decided: 0, category: 'produce', status: 'needed' }] }], purchased: [], inCart: [] } } };
groceryState.usualStores = ['Costco', 'Loblaws'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'sortall';
const html = groSortAllHtml(groceryState.data);
const carrots = /data-row-for="7"[\\s\\S]*?data-row-for="8"/.exec(html)[0];
console.log(JSON.stringify({ html: html, carrots: carrots }));
""")
    carrots = out["carrots"]
    assert 'class="gro-athome" data-athome-for="7"' in carrots
    assert "You want 2 lbs. Fridge shows 2 lbs." in carrots
    assert "Still need to buy" in carrots and "Remove from list" in carrots
    # Above the chips, and the chips are untouched — nothing gates sorting.
    assert carrots.index("gro-athome") < carrots.index("gro-sortall-pills")
    assert 'data-store="Costco"' in carrots and 'data-store="Loblaws"' in carrots
    # The unflagged line beside it stays an ordinary row.
    assert 'data-athome-for="8"' not in out["html"]


@needs_node
def test_a_bought_row_asks_nothing():
    """It is in the trolley; a struck row is a receipt, not a question."""
    out = run(_FLAGGED_ROW + """
const bought = flaggedRow(7, 'Carrots', { status: 'purchased' });
const lemons = { id: 8, item: 'Lemons', quantity: '3', store: 'Costco', store_decided: 1,
  category: 'produce', status: 'needed' };
groceryState.data = { stores: { Unassigned: { sections: [], purchased: [], inCart: [] },
  Costco: { sections: [{ section: 'produce', items: [lemons] }], purchased: [bought], inCart: [] } } };
groceryState.usualStores = ['Costco', 'Loblaws'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'list';
console.log(JSON.stringify({ list: groListHtml(groceryState.data) }));
""")
    assert "gro-athome" not in out["list"]
    assert 'data-id="7"' in out["list"], "the bought row is still drawn, just without the question"


@needs_node
def test_deciding_from_a_row_is_the_banners_own_write():
    """One decision, one place. The row's button posts exactly what the
    banner's button posts — and the toast's Undo is the same undo."""
    out = run(_FLAGGED_ROW + """
groceryState.data = { stores: { Unassigned: { sections: [], purchased: [], inCart: [] },
  Costco: { sections: [{ section: 'produce', items: [flaggedRow(7)] }], purchased: [], inCart: [] } } };
groceryState.usualStores = ['Costco', 'Loblaws'];
groceryState.storesPromptDismissed = true;
groceryState.step = 'list';
clickIfRendered({ gro: 'ps-decide', decision: 'drop', id: '7', name: 'Carrots' });
settle(function () {
  tapUndo();
  settle(function () {
    console.log(JSON.stringify({ posts: POSTS.map(function (p) { return p.url; }),
      body: POSTS[0].body, toast: lastToast() }));
  });
});
""")
    assert out["posts"][0] == "/api/grocery-list/7/pre-shop"
    assert out["body"] == {"decision": "drop", "author": "user"}
    assert "/api/grocery-list/7/pre-shop-undo" in out["posts"]
    assert out["toast"]["msg"] == "Carrots off the list — you have enough"
    assert out["toast"]["action"] == "Undo"


@needs_node
def test_the_header_count_takes_the_flagged_row_in_and_drops_it_on_a_remove():
    """GUARD (the arithmetic is unchanged; what changed is that a flagged
    row now reaches the client at all, which the HTTP tests above cover).
    The band counts what is on the list. A flagged line IS on the list
    until somebody removes it — which is the whole point of putting it
    back on a card — so it counts, and a remove takes it back off."""
    out = run(_FLAGGED_ROW + """
function withCarrots(rows) {
  return { stores: { Unassigned: { sections: [], purchased: [], inCart: [] },
    Costco: { sections: [{ section: 'produce', items: rows }], purchased: [], inCart: [] } } };
}
const lemons = { id: 8, item: 'Lemons', quantity: '3', store: 'Costco', store_decided: 1,
  category: 'produce', status: 'needed' };
const before = withCarrots([flaggedRow(7), lemons]);
const after = withCarrots([lemons]);
console.log(JSON.stringify({ before: groBandLine(before), after: groBandLine(after),
  beforeAll: groTotals(before).all, afterAll: groTotals(after).all }));
""")
    assert out["beforeAll"] == 2 and out["afterAll"] == 1
    assert out["before"] == "2 things, one store."
    assert out["after"] == "1 thing, one store."


# ------------------------------------------------------- the words she chose

@needs_node
def test_the_banners_buttons_say_what_they_do():
    """Emily's words, verbatim (card 3e31f4c0-5231-818c)."""
    out = run("""
groceryState.preShopFlags = [
  { itemId: 7, name: 'Carrots', sentence: 'You want 2 lbs. Fridge shows 2 lbs.',
    wantedLabel: '2 lbs', onHandLabel: '2 lbs', onHandLocation: 'fridge' },
  { itemId: 8, name: 'Garlic', sentence: 'You want 2 bulbs. Fridge shows 3 bulbs.',
    wantedLabel: '2 bulbs', onHandLabel: '3 bulbs', onHandLocation: null }
];
groceryState.preShopOpen = true;
console.log(JSON.stringify({ html: groPreShopHtml() }));
""")
    html = out["html"]
    assert html.count("Still need to buy") == 2
    assert html.count("Remove from list") == 2
    assert "Keep all 2 on the list" in html


def test_the_old_words_are_gone_from_the_app():
    """Words only — and nowhere a rewrite could have missed."""
    for text, rel in ((SHELL_JS, "static/shell.js"), (SHELL_CSS, "static/shell.css")):
        assert "Buy it anyway" not in text, rel
    # The helper line is gone, markup and all — only the comment saying
    # what it used to read survives, which is why this looks for the class
    # rather than the words.
    assert "gro-ps-helper" not in SHELL_JS
    # ">Drop it<" rather than "Drop it": shell.html's discard-draft dialog
    # is a different button on a different screen and keeps its words.
    assert ">Drop it</button>" not in SHELL_JS


def test_one_pair_of_labels_serves_both_doors():
    """The banner and the row read the same two names, so a copy change
    cannot land in one place and not the other."""
    assert "var GRO_PS_KEEP_LABEL = 'Still need to buy';" in SHELL_JS
    assert "var GRO_PS_DROP_LABEL = 'Remove from list';" in SHELL_JS
    assert SHELL_JS.count("GRO_PS_KEEP_LABEL") == 3  # the declaration, the banner, the row
    assert SHELL_JS.count("GRO_PS_DROP_LABEL") == 3


def test_the_row_flag_uses_tokens_only():
    """DESIGN_SYSTEM rule 9 — no literal colour in new work."""
    block = SHELL_CSS[SHELL_CSS.index('.gro-athome {'):SHELL_CSS.index('.gro-sortall-row .gro-athome')]
    assert "#" not in block, "every colour goes through a token"
    assert "var(--celadon-tint)" in block and "var(--celadon-edge)" in block
    # No apricot on it: ticking the row is still the screen's one action.
    assert "apricot" not in block


def test_the_flag_never_gates_the_row():
    """Emily's own line on the card: the flag rides along with the row, it
    does not gate it. Nothing about the flag can stop a tick or a sort —
    groFlagHtml only ever returns markup."""
    start = SHELL_JS.index("  function groFlagHtml(it) {")
    end = SHELL_JS.index("\n  }\n", start)
    body = SHELL_JS[start:end]
    assert "renderGrocery" not in body and "fetch(" not in body
    assert "return ''" in body, "an unflagged or bought row draws nothing"
