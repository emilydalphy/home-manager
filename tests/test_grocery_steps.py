"""
Grocery as four steps — LIST -> SORT -> TRIP -> WRAP UP (Emily's approved
design, 2026-09-08, branch `flows-5-grocery-sort-step`).

The tab answers "what do we need, and where?" as steps rather than as three
segments. LIST is one card per store plus a "N TO SORT" badge; SORT is one
unsorted thing at a time; TRIP is one stop at a time; WRAP UP is what didn't
make it into the cart, plus the trip's own count.

These are SOURCE MARKERS, not behaviour tests: shell.js has no JS test
harness in this repo (see tests/test_frontend_restored_2026_09_08.py's
docstring for the full reasoning and for what a marker is worth). They are
cheap tripwires for what each step renders, and for what left the root and
did not come back somewhere else by accident. Where a string is user-facing
copy it is asserted verbatim — if the copy is deliberately reworded, update
the constant here in the same commit and say so; do not delete the test.

No backend change came with this work: every step reuses the
/api/grocery-list* routes and the needed / in_cart / purchased / excluded
statuses exactly as the segmented version did, so there is nothing new to
test on the Python side.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle in haystack, (
        f"{what} is missing from static/{where}.\nExpected to find: {needle!r}\n"
        "This is part of Emily's approved four-step Grocery design "
        "(2026-09-08). If the change is deliberate, update this test in the "
        "same commit and say why."
    )


def _not_in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle not in haystack, (
        f"{what} is back in static/{where}: {needle!r}\n"
        "The four-step Grocery design removed it deliberately. If it is "
        "coming back, update this test in the same commit and say why."
    )


# --- the step machine ----------------------------------------------------

def test_grocery_steps_are_tab_states_not_routes():
    """Four steps of one tab, pushing their own history at /grocery — the
    same shape Meals' goMealsStep uses, wired into the shell's one popstate
    listener so Back means one thing."""
    _in("function goGroceryStep(", SHELL_JS, "the grocery step machine", "shell.js")
    _in("function pushGroceryStepHistory(", SHELL_JS, "its history push", "shell.js")
    _in("function applyGroceryStepFromHistory(", SHELL_JS, "its back gesture", "shell.js")
    _in("applyGroceryStepFromHistory(e && e.state)", SHELL_JS, "the popstate wiring", "shell.js")
    _in("}, '', '/grocery');", SHELL_JS, "the step URL (never a new route)", "shell.js")
    # A refresh lands on LIST because that is where the state starts.
    _in("step: 'list',", SHELL_JS, "the starting step", "shell.js")


def test_the_three_way_segmented_control_is_gone():
    """To buy / Plan stops / Review was the shape this replaces."""
    _not_in("data-gro=\"seg\"", SHELL_JS, "the segmented control", "shell.js")
    _not_in("'Plan stops'", SHELL_JS, "the Plan stops segment", "shell.js")
    _not_in(".gro-seg-btn", SHELL_CSS, "the segment button style", "shell.css")
    _not_in(".gro-hero-action", SHELL_CSS, "the spruce trip hero's action", "shell.css")


# --- LIST ----------------------------------------------------------------

def test_list_renders_one_card_per_store_with_a_peek():
    """One card per store, "Costco · 14", four things and then "+ N more"."""
    _in("function groListHtml(", SHELL_JS, "the LIST step", "shell.js")
    _in("function groStoreCardHtml(", SHELL_JS, "the store card", "shell.js")
    _in("escapeHtml(name) + ' · ' + items.length", SHELL_JS, "the store card's count", "shell.js")
    _in("var GRO_CARD_PEEK = 4;", SHELL_JS, "how many things a card shows", "shell.js")
    _in("'+ ' + hidden + ' more</button>'", SHELL_JS, "the +N more control", "shell.js")
    _in("data-gro=\"expand-store\"", SHELL_JS, "its handler", "shell.js")
    _in(".gro-listrow", SHELL_CSS, "the list row style", "shell.css")


def test_list_subtitle_counts_things_and_stops():
    """"23 things · 2 stops" — the shape of the shop in one line."""
    _in("groPlural(t.needed, 'thing', 'things')", SHELL_JS, "the things count", "shell.js")
    _in("groPlural(stopCount, 'stop', 'stops')", SHELL_JS, "the stops count", "shell.js")


def test_the_way_into_sort_only_exists_when_something_is_unsorted():
    """A row at the top of the list, and the only way into SORT.

    It was an apricot "N TO SORT" badge in the head until the root band
    (2026-09-11), which carries no button — so the control moved into the
    list it is about, in the .kit-row shape, and the badge is gone.
    """
    _in("function groSortRowHtml(", SHELL_JS, "the sort row builder", "shell.js")
    row = SHELL_JS[SHELL_JS.index("function groSortRowHtml("):SHELL_JS.index("function groListHtml(")]
    assert 'data-gro="goto-sort"' in row, "the row's target"
    assert "if (!unsorted || groStoresPromptShouldShow()) return '';" in row, "the row's guard"
    assert "groPlural(unsorted, 'thing', 'things') + ' to sort'" in row, "the row copy"
    _in("html += groSortRowHtml(data);", SHELL_JS, "the row at the top of LIST", "shell.js")
    _not_in("gro-sortbadge", SHELL_JS, "the retired TO SORT badge", "shell.js")
    _not_in(".gro-sortbadge", SHELL_CSS, "the retired badge style", "shell.css")


def test_list_carries_the_screens_one_apricot_and_an_inline_add():
    """One apricot ("Start the trip"), and under it an add row that is a
    plain POST. Adding one thing must never cost a model turn, which is why
    the "Add something" button that opened the ask sheet is gone — see
    test_adding_one_thing_never_costs_a_model_turn below."""
    _in("data-gro=\"start-trip\"", SHELL_JS, "Start the trip", "shell.js")
    _in(">Start the trip<", SHELL_JS, "its copy", "shell.js")
    # The per-tab ask hint ("Add oat milk and lemons…") went with the
    # always-open bar on 2026-09-11 (chat icon, Build 1): one line everywhere.
    _in(".gro-primary", SHELL_CSS, "the primary action style", "shell.css")
    _not_in("data-gro=\"add-something\"", SHELL_JS, "the ask-sheet Add something button", "shell.js")


def test_adding_one_thing_never_costs_a_model_turn():
    """The LIST foot's inline add row POSTs /api/grocery-list/add directly —
    the same route groHandleVoiceCommand's "add oat milk" and the old root's
    "Add an item" card used. The ask bar above the tab bar is still there for
    anything wordier; it just isn't the only way to add a carton of milk."""
    _in("function groAddItem(", SHELL_JS, "the inline add", "shell.js")
    _in("'/api/grocery-list/add'", SHELL_JS, "the add route", "shell.js")
    _in("id=\"gro-add-item\"", SHELL_JS, "the name field", "shell.js")
    _in("id=\"gro-add-qty\"", SHELL_JS, "the optional quantity field", "shell.js")
    _in("data-gro=\"add\"", SHELL_JS, "the Add button", "shell.js")
    _in("case 'add':", SHELL_JS, "its handler", "shell.js")
    # Enter in either field adds, so a list can be filled without reaching
    # for the button.
    _in("e.target.id === 'gro-add-item' || e.target.id === 'gro-add-qty'", SHELL_JS,
        "the Enter-to-add wiring", "shell.js")
    # Spruce, not apricot: LIST's one apricot is the trip (Rule 5).
    add_btn = SHELL_CSS.split(".gro-add-btn {", 1)[1][:400]
    assert "var(--spruce)" in add_btn and "var(--apricot)" not in add_btn, (
        "The inline Add button is spruce — LIST's one apricot is "
        "\"Start the trip\" (DESIGN_SYSTEM.md Rule 5)."
    )


def test_a_list_row_keeps_its_quiet_row_action():
    """The per-row ⋯ came back with the three verbs it always had, on the
    routes it always used: quantity via /update, store via /store (the pills,
    with "Any" as the old move / not-this-time and "Somewhere else" on
    /exclude), and /remove with an undo. Quiet — no apricot: the row is never
    what the screen is for."""
    _in("function groRowMenuHtml(", SHELL_JS, "the row menu", "shell.js")
    _in("data-gro=\"row-menu\"", SHELL_JS, "the ⋯ control", "shell.js")
    _in("GRO_ICONS.dots", SHELL_JS, "its ⋯ glyph", "shell.js")
    _in("openRowId", SHELL_JS, "the one-open-at-a-time state", "shell.js")
    # The three verbs, and the endpoints each one actually calls.
    _in("data-gro=\"row-qty\"", SHELL_JS, "edit quantity", "shell.js")
    _in("'/update', { quantity: rowQty }", SHELL_JS, "its update call", "shell.js")
    _in("data-gro=\"row-store\"", SHELL_JS, "change store", "shell.js")
    _in("'/store', { store: rowStore }", SHELL_JS, "its store call", "shell.js")
    _in("data-gro=\"row-remove\"", SHELL_JS, "remove", "shell.js")
    _in("'/remove');", SHELL_JS, "its remove call", "shell.js")
    _in("'Remove</button>'", SHELL_JS, "its copy", "shell.js")
    # The store pills, including the two non-store answers.
    _in("function groPillStores(", SHELL_JS, "the shared store pills", "shell.js")
    _in("data-gro=\"row-exclude\"", SHELL_JS, "the Somewhere else pill", "shell.js")
    # Remove is reversible in the moment — /remove is a hard delete, so the
    # undo puts the line back through /add.
    _in("label: 'Undo',", SHELL_JS, "the remove undo", "shell.js")
    _in(".gro-rowmore", SHELL_CSS, "the ⋯ style", "shell.css")
    _in(".gro-rowmenu", SHELL_CSS, "the row menu style", "shell.css")
    rowmore = SHELL_CSS.split(".gro-rowmore {", 1)[1][:400]
    assert "width: 44px" in rowmore and "height: 44px" in rowmore, (
        "The ⋯ needs a full 44px hit area — it is a thumb target on a phone."
    )
    rowmenu = SHELL_CSS.split(".gro-rowmenu {", 1)[1][:900]
    assert "--apricot" not in rowmenu, (
        "The row menu is quiet — LIST's one apricot is \"Start the trip\"."
    )


def test_two_rows_of_the_same_thing_get_one_quiet_line():
    """Review's "Possible duplicate" flag card moved here whole: the same
    grouping key, the same Merge, said as a line above the store cards rather
    than as a card of its own."""
    _in("function groDuplicateGroups(", SHELL_JS, "the duplicate detection", "shell.js")
    _in("(it.item || '').trim().toLowerCase()", SHELL_JS, "its grouping key", "shell.js")
    _in("return g.length > 1;", SHELL_JS, "what counts as a duplicate", "shell.js")
    _in("function groDuplicatesHtml(", SHELL_JS, "the line", "shell.js")
    _in("' rows of '", SHELL_JS, "its copy — \"Two rows of spinach\"", "shell.js")
    _in("function groCountWord(", SHELL_JS, "the number as a word", "shell.js")
    _in("html += groDuplicatesHtml(data);", SHELL_JS, "it rendering at the top of LIST", "shell.js")
    _in("data-gro=\"merge\"", SHELL_JS, "the Merge control", "shell.js")
    _in("case 'merge':", SHELL_JS, "the old merge handler", "shell.js")
    _in(".gro-dupe", SHELL_CSS, "its style", "shell.css")


def test_the_unsorted_line_is_gone_rather_than_repaired():
    """
    This test used to guard a line reading "Everything on the list still
    needs a store — tap N TO SORT above", and specifically that it said
    `unsorted.length` rather than `unsorted`, which would have printed
    "[object Object],…" into the copy.

    That line is GONE as of 2026-09-09 (Emily's call), so the assertion is
    inverted rather than deleted — per this file's own rule. The line was
    correct about its number and wrong about its existence: for a household
    with no store named it pointed at a badge that opened a step with
    nothing to sort into, while the items themselves stayed off screen. The
    fix shows the items; see the no-store tests at the end of this file.

    The lesson the old test encoded still stands anywhere a count reaches
    copy: pass the length, never the array.
    """
    _not_in("TO SORT above", SHELL_JS, "the sort nag", "shell.js")
    # And the count that IS still in copy — the sort row's own — stays a
    # number (groSortRowHtml reads .length before it reaches the words).
    _in("var unsorted = groUnsorted(data).length;\n    if (!unsorted || groStoresPromptShouldShow()) return '';",
        SHELL_JS, "the row count (unsorted is already a number here)", "shell.js")


def test_open_the_list_lands_on_the_list():
    """The receipt's grocery segment, its toast twin and the ask sheet's
    chip all mean "open the list". They land on LIST every time — an unsorted
    item is not a reason to drop somebody into a one-at-a-time queue they
    didn't ask for. The TO SORT badge is how you get to SORT."""
    _in("function groSetScreen(", SHELL_JS, "the compatibility shim", "shell.js")
    shim = SHELL_JS.split("function groSetScreen(", 1)[1].split("\n  }", 1)[0]
    assert "goGroceryStep('list')" in shim and "'sort'" not in shim, (
        "groSetScreen must land on LIST, never SORT — the badge is the way "
        "into SORT. Found:\n" + shim
    )


def test_the_stores_prompt_still_stands_in_for_the_store_cards():
    """Loop Board 19a's just-in-time question, unchanged in behaviour: when
    the household has no stores yet it renders INSTEAD of the store cards.
    (Also guarded by tests/test_frontend_restored_2026_09_08.py.)"""
    _in("function groStoresPromptShouldShow(", SHELL_JS, "the stores prompt guard", "shell.js")
    _in("function groStoresPromptHtml(", SHELL_JS, "the stores prompt", "shell.js")
    _in("function groAddUsualStore(", SHELL_JS, "the usual-store add", "shell.js")
    _in("return html + groStoresPromptHtml();", SHELL_JS, "the prompt standing in for the cards", "shell.js")


# --- SORT ----------------------------------------------------------------

def test_sort_asks_about_one_thing_at_a_time():
    _in("function groSortHtml(", SHELL_JS, "the SORT step", "shell.js")
    _in("Where does this go?", SHELL_JS, "the SORT title", "shell.js")
    _in("' to sort'", SHELL_JS, "the SORT subtitle", "shell.js")
    _in("var it = unsorted[0];", SHELL_JS, "one thing at a time", "shell.js")
    _in("position + ' of ' + total", SHELL_JS, "the progress line", "shell.js")
    # Renamed with the tab on 2026-09-09 (Emily): Grocery -> Shop. The back
    # link names its parent, so it follows the parent's name.
    _in("‹ Shop", SHELL_JS, "the SORT back link", "shell.js")
    _in(".gro-sortcard", SHELL_CSS, "the sort card style", "shell.css")


def test_sort_keeps_the_existing_chips_and_their_semantics():
    """Store pills, "Any" (no store, advances), "Have it", and "Somewhere
    else" (the /exclude route) — the chips the triage row already had."""
    _in("data-gro=\"assign\"", SHELL_JS, "the store pills", "shell.js")
    _in(">Any</button>", SHELL_JS, "the Any pill", "shell.js")
    _in("gro-pill-else", SHELL_JS, "the Somewhere else chip class", "shell.js")
    _in(">Somewhere else</button>", SHELL_JS, "the Somewhere else chip", "shell.js")
    _in("data-gro=\"triage-exclude\"", SHELL_JS, "its handler", "shell.js")
    _in("/exclude'", SHELL_JS, "the exclude route", "shell.js")
    _in("gro-pill-have", SHELL_JS, "the Have it chip class", "shell.js")
    # "Any" saves an empty store. Where the memory of that lives CHANGED on
    # 2026-09-09 (branch overnight/grocery-fast-sort): it used to be
    # groceryState.anyStoreIds, a page-view map, so a reload put the item
    # straight back into the queue. It is now a column the server writes
    # (grocery_items.store_decided), read back through groItemDecided —
    # tests/test_grocery_fast_sort.py covers that it survives the reload.
    _in("function groItemDecided(", SHELL_JS, "the Any memory", "shell.js")


def test_the_last_sort_choice_returns_to_the_list_with_a_toast():
    _in("function groAdvanceSort(", SHELL_JS, "the sort advance", "shell.js")
    _in("showToast('All sorted.');", SHELL_JS, "the all-sorted toast", "shell.js")


# --- TRIP ----------------------------------------------------------------

def test_trip_shows_one_store_at_a_time():
    _in("function groTripHtml(", SHELL_JS, "the TRIP step", "shell.js")
    _in("function groTripSections(", SHELL_JS, "the stop's aisles", "shell.js")
    _in("‹ Pause the trip", SHELL_JS, "the pause link", "shell.js")
    # The counter counts stops BEHIND you, not this stop's place in the
    # snapshot — changed 2026-09-09 with the WHERE NEXT step, because the
    # household picks its own order and the snapshot index would have said
    # "Stop 3 of 3" with two shops still waiting.
    _in("'Stop ' + (done + 1) + ' of '", SHELL_JS, "the stop counter", "shell.js")
    _in("' left'", SHELL_JS, "the things-left count", "shell.js")
    # The stops are snapshotted, so finishing one can't renumber the rest.
    _in("groceryState.tripStops = stops;", SHELL_JS, "the snapshotted stops", "shell.js")


def test_trip_ticks_into_the_cart_and_can_put_things_back():
    _in("data-gro=\"trip-toggle\"", SHELL_JS, "the tick", "shell.js")
    # A tick writes in_cart through groTick since 2026-09-11 (grocery
    # offline): on screen at once, queued when there is no signal, and sent
    # to the same /status route as before — see tests/test_grocery_offline.py.
    _in("groTick(id, 'in_cart');", SHELL_JS, "what a tick writes", "shell.js")
    _in("In your cart · ' + inCart.length", SHELL_JS, "the cart group", "shell.js")
    _in("data-gro=\"toggle-incart\"", SHELL_JS, "its toggle", "shell.js")
    _in("function groDoneRowHtml(", SHELL_JS, "the put-back row", "shell.js")
    _in("data-gro=\"uncheck\"", SHELL_JS, "the put-back", "shell.js")


def test_finishing_a_stop_ends_that_stop_and_asks_where_next():
    """Was test_trip_advances_to_the_next_stop_and_then_to_wrap_up, and the
    rename is the change: finishing a stop used to march straight into the
    next one in snapshot order, so the button named it ("Done at Costco →
    Metro"). Emily, 2026-09-09 — the household says where it is actually
    driving. The last stop still drops into WRAP UP, because there is
    nothing left to choose between. The two ride-along assertions moved with
    the rule they described (things with no shop follow the shopper now
    rather than being pinned to stop one) — both are in
    tests/test_grocery_fast_sort.py."""
    _in("'Done at ' + (groTripStore()", SHELL_JS, "the stop's own button", "shell.js")
    _in("data-gro=\"stop-done\"", SHELL_JS, "its handler", "shell.js")
    _in("goGroceryStep(stillToGo.length ? 'next' : 'wrap');", SHELL_JS, "where it goes next", "shell.js")
    _in("I&rsquo;m done shopping for today", SHELL_JS, "the way to end the trip", "shell.js")
    _in("data-gro=\"next-stop\"", SHELL_JS, "picking the next stop", "shell.js")


# --- WRAP UP -------------------------------------------------------------

def test_wrap_up_asks_how_it_went_and_finishes_the_trip():
    _in("function groWrapHtml(", SHELL_JS, "the WRAP UP step", "shell.js")
    _in("How did it go?", SHELL_JS, "the WRAP UP title", "shell.js")
    _in("Couldn’t find it", SHELL_JS, "the keep-it-needed answer", "shell.js")
    _in("data-gro=\"wrap-keep\"", SHELL_JS, "its handler", "shell.js")
    _in("data-gro=\"wrap-else\"", SHELL_JS, "the somewhere-else answer", "shell.js")
    _in("Bought ' + bought + ' of ' + total", SHELL_JS, "the summary line", "shell.js")
    _in(">Finish the trip<", SHELL_JS, "the finish button", "shell.js")
    _in("data-gro=\"finish-trip\"", SHELL_JS, "its handler", "shell.js")
    _in(".gro-wrap-summary", SHELL_CSS, "the summary style", "shell.css")


def test_wrap_up_keeps_reviews_confirmation_logic():
    """Review's "Already sorted this week" card, with both undos, is the one
    half of that segment the design keeps."""
    _in("function groAlreadyHaveHtml(", SHELL_JS, "the confirmation card", "shell.js")
    _in("Already sorted this week", SHELL_JS, "its title", "shell.js")
    _in("'undo-already-have', 'Actually, I need it'", SHELL_JS, "the already-have undo", "shell.js")
    _in("'undo-elsewhere', 'Actually, get it here'", SHELL_JS, "the elsewhere undo", "shell.js")
    _in("case 'undo-already-have':", SHELL_JS, "its handler", "shell.js")
    _in("case 'undo-elsewhere':", SHELL_JS, "its handler", "shell.js")
    _in("html += groAlreadyHaveHtml();", SHELL_JS, "it rendering inside WRAP UP", "shell.js")


def test_finishing_writes_purchases_and_lands_back_on_the_list():
    """The existing trip-finish logic: in_cart -> purchased (which is what
    writes the kitchen's inventory), the trip row closed, back to LIST with
    the receipt toast the app already showed."""
    _in("function groFinishStore(", SHELL_JS, "the stop finish", "shell.js")
    _in("{ status: 'purchased' }", SHELL_JS, "the purchase write", "shell.js")
    _in("'/api/shopping-trips/close'", SHELL_JS, "the trip row", "shell.js")
    _in("function groFinishAnyRemainingCarts(", SHELL_JS, "the stranded-cart sweep", "shell.js")


def test_the_finish_toast_is_about_the_trip_not_a_stop():
    """"Finish the trip" ends the whole trip, so the line says so. It used to
    say "Stop saved — I'll remember what you bought where", which was the
    per-stop line and undersold what had just happened. The count is
    tripBought — THIS trip — never groTotals().done, which sums every
    purchase the household has ever made."""
    _in("'Trip finished — ' + groPlural(home, 'thing', 'things') + ' home.'", SHELL_JS,
        "the trip-level finish toast", "shell.js")
    _in("var home = groceryState.tripBought;", SHELL_JS, "what it counts", "shell.js")
    _not_in("showToast('Stop saved", SHELL_JS, "the old per-stop toast", "shell.js")
    # Calm, not cheery (DESIGN_SYSTEM.md §8): no exclamation mark.
    assert "Trip finished!" not in SHELL_JS, "The finish line takes no exclamation mark."


# --- a household with no store named (Emily's call, 2026-09-09) -----------
#
# The bug this closes: LIST drew only store cards, and an item with no store
# belonged to no card. A household that tapped "One list is fine" therefore
# had an invisible grocery list — the header counted "3 things" over an empty
# screen, and answering "Any" in SORT moved the items from one invisible
# bucket ("not sorted") to another ("sorted, no store"), at which point LIST
# said "Nothing on the list yet" over three real rows. Naming any store fixed
# it instantly, which is why every earlier test — all of which name one —
# missed it entirely.

def test_a_list_with_no_stops_still_renders_its_items():
    """The union of both Unassigned halves, drawn as one plain section."""
    _in("function groLooseItems(", SHELL_JS, "the no-store item list", "shell.js")
    _in("function groLooseCardHtml(", SHELL_JS, "the section that draws them", "shell.js")
    _in("var loose = groLooseItems(data);", SHELL_JS, "LIST reading the union", "shell.js")
    _in("html += groLooseCardHtml(data, loose);", SHELL_JS, "LIST rendering it", "shell.js")


def test_the_empty_line_is_gated_on_the_union_not_on_unsorted_alone():
    """"Nothing on the list yet" over a full list was the whole defect: the
    old branch asked about `unsorted`, which excludes anything answered
    "Any", so a finished sort emptied the screen."""
    _in("if (!stops.length && !loose.length) {", SHELL_JS,
        "the empty state gated on the union", "shell.js")
    _not_in("if (!stops.length && !unsorted.length) {", SHELL_JS,
            "the old empty-state gate", "shell.js")


def test_the_sort_nag_is_gone_because_the_items_are_on_screen_now():
    """It pointed at a badge that, for a store-less household, opened a step
    with nothing to sort into. Replaced by showing the things themselves."""
    _not_in("Everything on the list still needs a store", SHELL_JS,
            "the sort nag", "shell.js")


def test_sorting_is_only_offered_once_there_is_a_store_to_sort_into():
    """The badge would otherwise count things that are already fully on
    screen, and open a step whose only possible answer is "Any".

    The gate MOVED on 2026-09-09 (branch overnight/grocery-fast-sort) and
    got stricter with it: it used to be "the household named at least one
    shop", checked here at the badge, and it is now "there is more than one
    shop to choose between", checked inside groUnsorted so the badge, the
    step and its fast paths all go quiet together. Emily: a household with
    one shop, or none, must never see a sorting step. The behaviour is
    covered by tests/test_grocery_fast_sort.py, which runs it rather than
    reading for it."""
    _in("function groCanSort(data) { return groPillStores(data).length > 1; }", SHELL_JS,
        "the badge's store gate", "shell.js")
    _in("if (!groCanSort(data)) return [];", SHELL_JS,
        "the gate applied to the queue itself", "shell.js")


def test_the_no_store_section_has_no_heading_and_a_key_that_cannot_be_a_store():
    """No avatar and no name, because there is no store to name — and its
    expanded-state key is bracketed so it can never collide with a household
    that really does shop somewhere called "Everything"."""
    _in("var GRO_LOOSE_KEY = '<no-store>';", SHELL_JS, "the reserved key", "shell.js")
    # It lands in an HTML attribute, so it goes through the same escape every
    # store name does.
    _in("escapeHtml(GRO_LOOSE_KEY)", SHELL_JS, "the key being escaped", "shell.js")
    # The heading belongs to the CARDS. This section is the entire list of a
    # household with no stops at all ("One list is fine"), so there is
    # nothing a heading could distinguish it from.
    #
    # The slice now ends at groAnywhereCardHtml, added 2026-09-09 between the
    # two. THAT one carries a heading, and correctly: it only ever appears
    # alongside store cards, where "these have no shop" is exactly what the
    # reader needs told. Both rules are still true — see
    # tests/test_grocery_fast_sort.py section 8.
    loose = SHELL_JS[SHELL_JS.index("function groLooseCardHtml("):SHELL_JS.index("function groAnywhereCardHtml(")]
    assert "gro-card-head" not in loose, (
        "The no-store section drew a store heading. It has no store to name — "
        "that is the entire difference between it and groStoreCardHtml."
    )
    assert "gro-store-avatar" not in loose, "Same: no avatar without a store."
