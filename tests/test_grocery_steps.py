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


def test_the_to_sort_badge_only_exists_when_something_is_unsorted():
    """Apricot, in the head, and the only way into SORT."""
    _in("id=\"gro-sortbadge\"", SHELL_JS, "the TO SORT badge", "shell.js")
    _in("data-gro=\"goto-sort\"", SHELL_JS, "the badge's target", "shell.js")
    _in("unsorted + ' TO SORT'", SHELL_JS, "the badge copy", "shell.js")
    _in("step === 'list' && unsorted > 0", SHELL_JS, "the badge's guard", "shell.js")
    _in(".gro-sortbadge", SHELL_CSS, "the badge style", "shell.css")
    # Rule 1: an apricot fill carries --on-accent-ink, never ivory.
    assert "color: var(--on-accent-ink);" in SHELL_CSS.split(".gro-sortbadge {", 1)[1][:600], (
        "The TO SORT badge is an apricot fill, so its ink must be "
        "--on-accent-ink (DESIGN_SYSTEM.md Rule 1)."
    )


def test_list_carries_the_screens_one_apricot_and_a_quiet_add():
    _in("data-gro=\"start-trip\"", SHELL_JS, "Start the trip", "shell.js")
    _in(">Start the trip<", SHELL_JS, "its copy", "shell.js")
    _in("data-gro=\"add-something\"", SHELL_JS, "Add something", "shell.js")
    _in(">Add something<", SHELL_JS, "its copy", "shell.js")
    _in("openAskSheet('Add ');", SHELL_JS, "the ask-sheet prefill", "shell.js")
    _in("grocery: 'Add oat milk and lemons", SHELL_JS, "the Grocery ask hint", "shell.js")
    _in(".gro-primary", SHELL_CSS, "the primary action style", "shell.css")


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
    _in("‹ Grocery", SHELL_JS, "the SORT back link", "shell.js")
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
    # "Any" saves an empty store and is remembered for this page view only.
    _in("groceryState.anyStoreIds[id] = true;", SHELL_JS, "the Any memory", "shell.js")


def test_the_last_sort_choice_returns_to_the_list_with_a_toast():
    _in("function groAdvanceSort(", SHELL_JS, "the sort advance", "shell.js")
    _in("showToast('All sorted.');", SHELL_JS, "the all-sorted toast", "shell.js")


# --- TRIP ----------------------------------------------------------------

def test_trip_shows_one_store_at_a_time():
    _in("function groTripHtml(", SHELL_JS, "the TRIP step", "shell.js")
    _in("function groTripSections(", SHELL_JS, "the stop's aisles", "shell.js")
    _in("‹ Pause the trip", SHELL_JS, "the pause link", "shell.js")
    _in("'Stop ' + (groceryState.tripIndex + 1) + ' of '", SHELL_JS, "the stop counter", "shell.js")
    _in("' left'", SHELL_JS, "the things-left count", "shell.js")
    # The stops are snapshotted, so finishing one can't renumber the rest.
    _in("groceryState.tripStops = stops;", SHELL_JS, "the snapshotted stops", "shell.js")


def test_trip_ticks_into_the_cart_and_can_put_things_back():
    _in("data-gro=\"trip-toggle\"", SHELL_JS, "the tick", "shell.js")
    _in("{ status: 'in_cart' }", SHELL_JS, "what a tick writes", "shell.js")
    _in("In your cart · ' + inCart.length", SHELL_JS, "the cart group", "shell.js")
    _in("data-gro=\"toggle-incart\"", SHELL_JS, "its toggle", "shell.js")
    _in("function groDoneRowHtml(", SHELL_JS, "the put-back row", "shell.js")
    _in("data-gro=\"uncheck\"", SHELL_JS, "the put-back", "shell.js")


def test_trip_advances_to_the_next_stop_and_then_to_wrap_up():
    _in("'Done at ' + here + ' → ' + next", SHELL_JS, "the advance button", "shell.js")
    _in("'Done shopping'", SHELL_JS, "the last stop's button", "shell.js")
    _in("data-gro=\"stop-done\"", SHELL_JS, "its handler", "shell.js")
    _in("goGroceryStep('wrap');", SHELL_JS, "the hop to WRAP UP", "shell.js")
    # Any-store things ride along with the first stop.
    _in("if (groceryState.tripIndex === 0)", SHELL_JS, "the Any items on stop one", "shell.js")


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
    _in("Stop saved — I’ll remember what you bought where", SHELL_JS, "the receipt toast", "shell.js")
