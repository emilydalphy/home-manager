"""
Grocery as three states of one tab — LIST, CARRY, SORT ALL (Emily's
approved design: four steps on 2026-09-08, the list as the checklist on
2026-09-18, branch `shop-checklist`).

The tab answers "what do we need, where — and what have we got?" on one
screen. LIST is the band, the add row, one card per store with a tickable
row per thing and a final "Anywhere" card; CARRY is last week's leftovers,
keep or drop; SORT ALL is every unsorted thing with a shop chip. The trip
(HEADED / TRIP / NEXT / WRAP UP), the paused-trip dock and the sort
chooser + one-at-a-time queue are gone.

These are SOURCE MARKERS, not behaviour tests: cheap tripwires for what
each state renders, and for what left the tab and did not come back
somewhere else by accident. Behaviour is in tests/test_shop_checklist.py,
tests/test_shop_list_first.py and tests/test_sort_all_rows_leave.py, run
under node. Where a string is user-facing copy it is asserted verbatim —
if the copy is deliberately reworded, update the constant here in the
same commit and say so; do not delete the test.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle in haystack, (
        f"{what} is missing from static/{where}.\nExpected to find: {needle!r}\n"
        "This is part of Emily's approved Shop design (the list is the "
        "checklist, 2026-09-18). If the change is deliberate, update this "
        "test in the same commit and say why."
    )


def _not_in(needle: str, haystack: str, what: str, where: str) -> None:
    assert needle not in haystack, (
        f"{what} is back in static/{where}: {needle!r}\n"
        "The checklist design removed it deliberately. If it is coming "
        "back, update this test in the same commit and say why."
    )


# --- the step machine ----------------------------------------------------

def test_grocery_steps_are_tab_states_not_routes():
    """Three states of one tab, pushing their own history at /grocery — the
    same shape Meals' goMealsStep uses, wired into the shell's one popstate
    listener so Back means one thing."""
    _in("function goGroceryStep(", SHELL_JS, "the grocery step machine", "shell.js")
    _in("function pushGroceryStepHistory(", SHELL_JS, "its history push", "shell.js")
    _in("function applyGroceryStepFromHistory(", SHELL_JS, "its back gesture", "shell.js")
    _in("applyGroceryStepFromHistory(e && e.state)", SHELL_JS, "the popstate wiring", "shell.js")
    _in("}, '', '/grocery');", SHELL_JS, "the step URL (never a new route)", "shell.js")
    # A refresh lands on LIST because that is where the state starts.
    _in("step: 'list',", SHELL_JS, "the starting step", "shell.js")
    # Anything but the three states folds to the root.
    _in("if (groceryState.step !== 'carry' && groceryState.step !== 'sortall') groceryState.step = 'list';",
        SHELL_JS, "the fold to the root", "shell.js")
    _not_in("groTripIndex", SHELL_JS, "the trip's history state", "shell.js")


def test_the_three_way_segmented_control_is_gone():
    """To buy / Plan stops / Review was the shape the four steps replaced."""
    _not_in("data-gro=\"seg\"", SHELL_JS, "the segmented control", "shell.js")
    _not_in("'Plan stops'", SHELL_JS, "the Plan stops segment", "shell.js")
    _not_in(".gro-seg-btn", SHELL_CSS, "the segment button style", "shell.css")
    _not_in(".gro-hero-action", SHELL_CSS, "the spruce trip hero's action", "shell.css")


# --- LIST ----------------------------------------------------------------

def test_list_renders_one_card_per_store_with_a_tick_per_row():
    """One card per store in stop order, "Costco" and "N of M" in the head,
    one tickable row per thing (groLineHtml), a final "Anywhere" card for
    the loose things — the mockup (board 16-shopping-list)."""
    _in("function groListHtml(", SHELL_JS, "the LIST step", "shell.js")
    _in("function groStoreCardHtml(", SHELL_JS, "the store card", "shell.js")
    _in("function groAnywhereCardHtml(", SHELL_JS, "the Anywhere card", "shell.js")
    _in("function groCardHtml(", SHELL_JS, "the one card builder", "shell.js")
    _in("'<span class=\"gro-store-count\">' + bought + ' of ' + items.length + '</span>'", SHELL_JS,
        "the card's \"N of M\"", "shell.js")
    _in("function groLineHtml(", SHELL_JS, "the row", "shell.js")
    _in("data-gro=\"line-tick\"", SHELL_JS, "the tick", "shell.js")
    _in("function groStoresOnList(", SHELL_JS, "the cards in stop order", "shell.js")
    _in("function groStoreLineItems(", SHELL_JS, "a card's rows", "shell.js")
    _in(".gro-line {", SHELL_CSS, "the row style", "shell.css")
    _in(".gro-store-count {", SHELL_CSS, "the count style", "shell.css")
    # What went: the aisle eyebrows, the store avatar, the peek, the two
    # loose cards.
    _not_in("function groAisleGroupHtml(", SHELL_JS, "the aisle grouping", "shell.js")
    _not_in("GRO_CATEGORY_LABELS", SHELL_JS, "the aisle labels", "shell.js")
    _not_in("gro-store-avatar", SHELL_JS, "the store avatar", "shell.js")
    _not_in("GRO_STORE_PALETTE", SHELL_JS, "the store palette", "shell.js")
    _not_in("function groLooseCardHtml(", SHELL_JS, "the headingless loose card", "shell.js")
    _not_in("function groUnsortedCardHtml(", SHELL_JS, "the Not sorted yet card", "shell.js")
    _not_in("GRO_CARD_PEEK", SHELL_JS, "the four-row peek", "shell.js")
    _not_in(".gro-listrow", SHELL_CSS, "the old list row style", "shell.css")
    _not_in(".gro-aisle", SHELL_CSS, "the aisle style", "shell.css")


def test_a_done_card_says_so_in_celadon():
    _in("'Done at ' + name", SHELL_JS, "\"Done at Costco\"", "shell.js")
    _in("var GRO_ANYWHERE_DONE = 'All bought';", SHELL_JS, "the Anywhere card's done line", "shell.js")
    _in("var GRO_ONE_LIST_DONE = 'Done shopping';", SHELL_JS, "the one-list card's done line", "shell.js")
    _in(".gro-store.is-done .gro-store-name, .gro-store.is-done .gro-store-count { color: var(--celadon-label); }",
        SHELL_CSS, "the done head's ink", "shell.css")
    _in(".gro-line.done .gro-line-qty { color: var(--ink-done); }", SHELL_CSS, "a struck row's amount", "shell.css")


def test_the_band_says_this_week_and_counts_the_list():
    """"This week · 14 things, two stores." — the eyebrow and the line from
    the mockup, counted from the data."""
    _in("var GRO_BAND_EYEBROW = 'This week';", SHELL_JS, "the eyebrow", "shell.js")
    _in("function groBandLine(", SHELL_JS, "the line", "shell.js")
    _in("groPlural(t.all, 'thing', 'things')", SHELL_JS, "the things count", "shell.js")
    _in("(stores === 1 ? 'store' : 'stores')", SHELL_JS, "the stores count", "shell.js")
    _not_in("function groTripPausedLine(", SHELL_JS, "the paused-trip line", "shell.js")


def test_the_root_has_no_dock_and_the_tick_is_the_action():
    """Nothing single to do on the root (nav rule 2): no "Start the trip",
    no "See the week" link. "Go to Plan" under the empty moment is the one
    exception."""
    _not_in("data-gro=\"start-trip\"", SHELL_JS, "Start the trip", "shell.js")
    _not_in(">Start the trip<", SHELL_JS, "its copy", "shell.js")
    _not_in("data-gro=\"see-week\"", SHELL_JS, "See the week", "shell.js")
    _not_in("function groTripPausedDockHtml(", SHELL_JS, "the paused-trip dock", "shell.js")
    _in("data-gro=\"goto-plan\">Go to Plan</button>", SHELL_JS, "the empty moment's next step", "shell.js")
    _in("function groTickLine(", SHELL_JS, "the tick", "shell.js")
    _in("case 'line-tick':", SHELL_JS, "its handler", "shell.js")
    _in("groTick(id, next);", SHELL_JS, "the tick going through the offline path", "shell.js")
    _in("label: bought ? 'Undo' : 'Put back',", SHELL_JS, "the toast's way back", "shell.js")
    _in("function groRecordStopDone(", SHELL_JS, "the stop record on the last tick", "shell.js")
    _in("groPostJson('/api/shopping-trips/close'", SHELL_JS, "the same route the trip closed a stop with", "shell.js")


def test_the_add_row_opens_the_list():
    """The inline add row POSTs /api/grocery-list/add directly — the same
    route groHandleVoiceCommand's "add oat milk" uses. It is the first
    thing under the band now (the mockup), not the foot of the list."""
    _in("function groAddRowHtml(", SHELL_JS, "the add row", "shell.js")
    _in("var html = groAddRowHtml() + groPreShopHtml();", SHELL_JS, "the add row opening LIST", "shell.js")
    _not_in("function groFootHtml(", SHELL_JS, "the foot", "shell.js")
    _not_in("id=\"gro-foot\"", SHELL_JS, "the foot's element", "shell.js")
    _in("function groAddItem(", SHELL_JS, "the inline add", "shell.js")
    _in("function groParseAddInput(", SHELL_JS, "the typed-quantity parser", "shell.js")
    _in("'/api/grocery-list/add'", SHELL_JS, "the add route", "shell.js")
    _in("id=\"gro-add-item\"", SHELL_JS, "the name field", "shell.js")
    _in("data-gro=\"add\"", SHELL_JS, "the Add button", "shell.js")
    _in("case 'add':", SHELL_JS, "its handler", "shell.js")
    _in("e.target.id === 'gro-add-item'", SHELL_JS, "the Enter-to-add wiring", "shell.js")
    _in("id=\"gro-scan-btn\"", SHELL_JS, "the camera", "shell.js")
    _in(".gro-add-field {", SHELL_CSS, "the field wrapping the camera button", "shell.css")
    field = SHELL_CSS.split(".gro-add-field {", 1)[1][:200]
    assert "position: relative" in field
    add_btn = SHELL_CSS.split(".gro-add-btn {", 1)[1][:400]
    assert "var(--spruce)" in add_btn and "var(--apricot)" not in add_btn, "the Add button is spruce (Rule 5)"
    # A re-render must not eat a half-typed "oat milk": the add row is
    # captured and restored across the body's re-render.
    _in("var addRow = groCaptureAddRow(body);", SHELL_JS, "the add row captured before a re-render", "shell.js")
    _in("groRestoreAddRow(body, addRow);", SHELL_JS, "and restored after", "shell.js")


def test_a_list_row_keeps_its_quiet_row_action():
    """The per-row ⋯ with the three verbs it always had, on the routes it
    always used: quantity via /update, store via /store (the pills, with
    "Any" as the old move / not-this-time and "Getting it elsewhere" on
    /exclude), and /remove with an undo. Quiet — no apricot."""
    _in("function groRowMenuHtml(", SHELL_JS, "the row menu", "shell.js")
    _in("data-gro=\"row-menu\"", SHELL_JS, "the ⋯ control", "shell.js")
    _in("GRO_ICONS.dots", SHELL_JS, "its ⋯ glyph", "shell.js")
    _in("openRowId", SHELL_JS, "the one-open-at-a-time state", "shell.js")
    _in("data-gro=\"row-qty\"", SHELL_JS, "edit quantity", "shell.js")
    _in("'/update', { quantity: rowQty }", SHELL_JS, "its update call", "shell.js")
    _in("data-gro=\"row-store\"", SHELL_JS, "change store", "shell.js")
    _in("'/store', { store: rowStore }", SHELL_JS, "its store call", "shell.js")
    _in("data-gro=\"row-remove\"", SHELL_JS, "remove", "shell.js")
    _in("'/remove');", SHELL_JS, "its remove call", "shell.js")
    _in("'Remove</button>'", SHELL_JS, "its copy", "shell.js")
    _in("function groPillStores(", SHELL_JS, "the shared store pills", "shell.js")
    _in("groElsewherePillHtml('row-exclude', it)", SHELL_JS, "the Getting it elsewhere pill", "shell.js")
    _in("case 'row-exclude':", SHELL_JS, "its handler", "shell.js")
    _in("label: 'Undo',", SHELL_JS, "the remove undo", "shell.js")
    _in("function groOfferRememberToast(", SHELL_JS, "\"usually here\" — the remember-this-store toast", "shell.js")
    _in(".gro-rowmore", SHELL_CSS, "the ⋯ style", "shell.css")
    _in(".gro-rowmenu", SHELL_CSS, "the row menu style", "shell.css")
    rowmore = SHELL_CSS.split(".gro-rowmore {", 1)[1][:400]
    assert "width: 44px" in rowmore and "height: 44px" in rowmore
    rowmenu = SHELL_CSS.split(".gro-rowmenu {", 1)[1][:900]
    assert "--apricot" not in rowmenu


def test_two_rows_of_the_same_thing_get_one_quiet_line():
    _in("function groDuplicateGroups(", SHELL_JS, "the duplicate detection", "shell.js")
    _in("(it.item || '').trim().toLowerCase()", SHELL_JS, "its grouping key", "shell.js")
    _in("return g.length > 1;", SHELL_JS, "what counts as a duplicate", "shell.js")
    _in("function groDuplicatesHtml(", SHELL_JS, "the line", "shell.js")
    _in("' rows of '", SHELL_JS, "its copy — \"Two rows of spinach\"", "shell.js")
    _in("html += groDuplicatesHtml(data);", SHELL_JS, "it rendering at the top of LIST", "shell.js")
    _in("data-gro=\"merge\"", SHELL_JS, "the Merge control", "shell.js")
    _in("case 'merge':", SHELL_JS, "the old merge handler", "shell.js")
    _in(".gro-dupe", SHELL_CSS, "its style", "shell.css")


def test_open_the_list_lands_on_the_list():
    _in("function groSetScreen(", SHELL_JS, "the compatibility shim", "shell.js")
    shim = SHELL_JS.split("function groSetScreen(", 1)[1].split("\n  }", 1)[0]
    assert "goGroceryStep('list')" in shim and "'sort" not in shim


def test_the_stores_prompt_sits_on_top_of_the_list():
    _in("function groStoresPromptShouldShow(", SHELL_JS, "the stores prompt guard", "shell.js")
    _in("function groStoresPromptHtml(", SHELL_JS, "the stores prompt", "shell.js")
    _in("function groAddUsualStore(", SHELL_JS, "the usual-store add", "shell.js")
    _in("html += groStoresPromptHtml();", SHELL_JS, "the prompt at the top of the list", "shell.js")
    _not_in("return html + groStoresPromptHtml();", SHELL_JS, "the prompt standing in for the cards", "shell.js")


def test_the_quiet_sections_keep_their_places():
    """Pre-shop check, Getting elsewhere, Already had on hand, Spices,
    Staples — where they were."""
    _in("function groPreShopHtml(", SHELL_JS, "the pre-shop check", "shell.js")
    _in("function groElsewhereHtml(", SHELL_JS, "the set-aside foot", "shell.js")
    _in("function groNotNeededHtml(", SHELL_JS, "the not-needed foot (the wrap-up's confirmation half)", "shell.js")
    _in("data-gro=\"undo-already-have\"", SHELL_JS, "its way back", "shell.js")
    _in("function groSpicesHtml(", SHELL_JS, "the spices section", "shell.js")
    _in("function groStaplesHtml(", SHELL_JS, "the staples card", "shell.js")
    _in("return groElsewhereHtml() + groNotNeededHtml() + groSpicesHtml() + groStaplesHtml();", SHELL_JS,
        "the foot in one place", "shell.js")
    _not_in("function groAlreadyHaveHtml(", SHELL_JS, "the old two-part confirmation", "shell.js")


# --- the finished moment -------------------------------------------------

def test_the_finished_moment_is_the_lists_not_a_screen():
    """What the wrap-up used to hand over to (groShopDoneHtml) stands at the
    top of the list once every card is done — read off the data
    (groListDone), not a page-view flag."""
    _in("function groListDone(", SHELL_JS, "the finished predicate", "shell.js")
    _in("if (groListDone(data)) {", SHELL_JS, "LIST reading it", "shell.js")
    _in("function groShopDoneHtml(", SHELL_JS, "the finished moment", "shell.js")
    _in("That’s the shopping done.", SHELL_JS, "its line", "shell.js")
    _in("data-gro=\"shop-done-tonight\"", SHELL_JS, "its way to tonight", "shell.js")
    _in("data-gro=\"shop-done-later\"", SHELL_JS, "its way to fold", "shell.js")
    _not_in("justFinishedTrip", SHELL_JS, "the page-view flag", "shell.js")


# --- CARRY ---------------------------------------------------------------

def test_carry_is_still_the_one_screen_the_tab_opens_on_by_itself():
    _in("function groCarryHtml(", SHELL_JS, "the CARRY step", "shell.js")
    _in("function groMaybeCarryFirst(", SHELL_JS, "the carry-first rule", "shell.js")
    _in("goGroceryStep('carry', { push: false });", SHELL_JS, "the tab opening on it", "shell.js")
    _in("data-gro=\"carry-decide\"", SHELL_JS, "its answers", "shell.js")
    _in("data-gro=\"carry-later\"", SHELL_JS, "its way out", "shell.js")


# --- SORT ALL ------------------------------------------------------------

def test_sorting_has_one_way():
    """The "N things to sort" row opens SORT ALL — every unsorted thing on
    one screen, one tap each. No chooser, no queue."""
    _in("function groSortRowHtml(", SHELL_JS, "the sort row builder", "shell.js")
    row = SHELL_JS[SHELL_JS.index("function groSortRowHtml("):SHELL_JS.index("function groListHtml(")]
    assert 'data-gro="goto-sort"' in row, "the row's target"
    assert "if (!unsorted || groStoresPromptShouldShow()) return '';" in row, "the row's guard"
    assert "groPlural(unsorted, 'thing', 'things') + ' to sort'" in row, "the row copy"
    _in("case 'goto-sort':\n        goGroceryStep('sortall');", SHELL_JS, "the row opening SORT ALL", "shell.js")
    _in("function groSortAllHtml(", SHELL_JS, "the SORT ALL step", "shell.js")
    _in("function groSortAllRender(", SHELL_JS, "its row-by-row render", "shell.js")
    _in("data-gro=\"sortall-pick\"", SHELL_JS, "its chips", "shell.js")
    _in("title: 'Sort them all'", SHELL_JS, "its title", "shell.js")
    _in("emptyMomentHtml('bag', 'All sorted.')", SHELL_JS, "its finish", "shell.js")
    for gone in ("function groSortHowHtml(", "function groSortHtml(", "data-gro=\"assign\"",
                 "data-gro=\"goto-sort-one\"", "data-gro=\"goto-sortall\"", "data-gro=\"sort-all-at\"",
                 "data-gro=\"sort-later\"", "GRO_FAST_SORT_MIN", "GRO_SORT_STEPS", "showToast('All sorted.');",
                 "function groSortAssign(", "function groBulkAssign(", "data-gro=\"triage-exclude\""):
        _not_in(gone, SHELL_JS, "the chooser / the queue", "shell.js")
    _in("function groCanSort(data) { return groPillStores(data).length > 1; }", SHELL_JS,
        "the sort gate", "shell.js")
    _in("if (!groCanSort(data)) return [];", SHELL_JS, "the gate applied to the queue itself", "shell.js")


# --- what left with the trip --------------------------------------------

def test_the_trip_screens_and_their_state_are_gone():
    for gone in (
        "function groTripHtml(", "function groHeadedHtml(", "function groNextHtml(", "function groWrapHtml(",
        "function groStopCardsHtml(", "function groTripRowHtml(", "function groDoneRowHtml(",
        "function groFinishStore(", "function groFinishTrip(", "function groFinishAnyRemainingCarts(",
        "function groStartTrip(", "function groBeginTrip(", "function groResumeTrip(",
        "function groTripSnapshot(", "function groSaveTrip(", "function groRestoreTrip(", "function groDropStaleTrip(",
        "function groTripPaused(", "function groStopDoneLabel(", "function groRemainingStops(", "function groStopRemaining(",
        "tripStops", "tripIndex", "tripDone", "tripBought", "wrapKept", "wrapMoved", "wrapElseId", "inCartOpen",
        "GRO_TRIP_KEY", "GRO_TRIP_KEEP_MS",
        "data-gro=\"head-for\"", "data-gro=\"stop-done\"", "data-gro=\"next-stop\"", "data-gro=\"trip-end\"",
        "data-gro=\"finish-trip\"", "data-gro=\"trip-pause\"", "data-gro=\"trip-resume\"", "data-gro=\"trip-finish-now\"",
        "data-gro=\"trip-toggle\"", "data-gro=\"uncheck\"", "data-gro=\"toggle-incart\"",
        "'Trip finished — '", "Finish later", "Continue the trip", "Skip the rest",
    ):
        _not_in(gone, SHELL_JS, "the trip", "shell.js")
    for gone in (".gro-trip-body", ".gro-done {", ".gro-stops", ".gro-stop {", ".gro-secondary {", ".gro-wrap-row",
                 ".gro-allclear", ".gro-foot"):
        _not_in(gone, SHELL_CSS, "the trip's style", "shell.css")
    # The big meal's two-trip headings inside a card stay (Holidays slice 2).
    _in(".gro-trip {", SHELL_CSS, "the big meal's heading style", "shell.css")
    _in("shop_timing === 'early'", SHELL_JS, "the big meal's grouping", "shell.js")


def test_the_bought_rows_come_from_the_bought_view():
    """The rows ticked off THIS list, not the household's whole buying
    history — see tools.list_grocery_list('bought')."""
    _in("fetch('/api/grocery-list?status=bought')", SHELL_JS, "the bought view", "shell.js")
    _not_in("fetch('/api/grocery-list?status=purchased')", SHELL_JS, "the lifetime purchased view", "shell.js")
    _not_in("fetch('/api/grocery-list?status=in_cart')", SHELL_JS, "the separate in_cart view", "shell.js")
    _in("function groBoughtItems(", SHELL_JS, "the bought reader", "shell.js")
    _in("function groIsBought(it) { return it.status === 'purchased' || it.status === 'in_cart'; }", SHELL_JS,
        "both buckets reading as bought", "shell.js")


def test_the_empty_line_is_gated_on_the_union_not_on_unsorted_alone():
    _in("if (!stops.length && !loose.length) {", SHELL_JS,
        "the empty state gated on the union", "shell.js")
    _not_in("if (!stops.length && !unsorted.length) {", SHELL_JS,
            "the old empty-state gate", "shell.js")
    _not_in("Everything on the list still needs a store", SHELL_JS, "the sort nag", "shell.js")
    _not_in("TO SORT above", SHELL_JS, "the sort nag", "shell.js")
