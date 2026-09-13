"""
"Start the trip" asks "Where are we headed?" — and both store-pick
screens are made of the same store cards.

Emily, 2026-09-13: "For Start the trip - it should ask me (through a
select screen) which store we're headed to so it knows which grocery store
to start with for the trip - similar to the 'where's next' screen - but a
'where are we headed' instead. And make the design of that one + the
where next screen a bit nicer - add some colour."

Before this, "Start the trip" snapshotted the stops and opened the FIRST
one in list order — whichever shop the list happened to put first — and
the household's own choice of where to drive only began at the second
stop (WHERE NEXT). Now:

  * more than one stop -> the HEADED step: the stops as cards, and the
    card you tap is the stop the trip opens on;
  * one stop -> straight in, as before (no question with one answer);
  * a paused trip is continued, never asked again;
  * WHERE NEXT is the same cards (groStopCardsHtml), so the two match.

Behaviour runs under node against shell.js's own functions, the way
tests/test_shop_trip_exit.py does; the design rules are read off the CSS.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import nodeharness
import pytest
from test_shop_trip_exit import _STUB, _grocery_block

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own functions"
)


def _node(body: str):
    res = nodeharness.run_node(_STUB + _grocery_block() + body, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


# --- 1. the question comes before any list ---------------------------------


@_needs_node
def test_start_the_trip_over_two_stops_asks_where_we_are_headed_first():
    out = _node("""
twoShops();
click({ gro: 'start-trip' });
const s = tripState();
s.posts = POSTS.length;
console.log(JSON.stringify(s));
""")
    assert out["step"] == "headed", "the question, not a list"
    assert out["stops"] is None, "no snapshot until a stop is named"
    assert out["at"] is None
    assert out["posts"] == 0, "asking writes nothing"


@_needs_node
def test_picking_a_stop_starts_the_trip_there():
    """Metro is the SECOND stop in list order; the trip opens on it."""
    out = _node("""
twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Metro' });
console.log(JSON.stringify(tripState()));
""")
    assert out["step"] == "trip"
    assert out["at"] == "Metro"
    assert out["index"] == 1
    assert out["stops"] == ["Costco", "Metro"], "the snapshot is every stop, in list order — only the START moved"
    assert out["done"] == []
    assert out["saved"] is not None, "and the trip is mirrored from its first screen"


@_needs_node
def test_a_one_stop_week_skips_the_question():
    out = _node("""
twoShops();
groceryState.data.stores.Metro.sections = [];
click({ gro: 'start-trip' });
console.log(JSON.stringify(tripState()));
""")
    assert out["step"] == "trip"
    assert out["at"] == "Costco"
    assert out["stops"] == ["Costco"]


@_needs_node
def test_a_paused_trip_is_continued_not_asked_again():
    out = _node("""
twoShops();
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripIndex = 1;
groceryState.tripDone = {};
click({ gro: 'start-trip' });
console.log(JSON.stringify(tripState()));
""")
    assert out["step"] == "trip", "back into the open stop"
    assert out["at"] == "Metro"


@_needs_node
def test_the_crumb_from_the_question_is_the_list_with_no_trip_on():
    out = _node("""
twoShops();
click({ gro: 'start-trip' });
const head = groHeadFor(groceryState.data, 'headed');
click({ gro: 'step-back' });
const s = tripState();
s.crumb = head.back;
console.log(JSON.stringify(s));
""")
    assert out["crumb"] == "‹ Shop"
    assert out["step"] == "list"
    assert out["stops"] is None, "changing your mind on the question leaves no trip behind"


@_needs_node
def test_a_stop_that_left_the_list_while_the_question_was_up_falls_back_to_the_first():
    """The other adult ticks Metro's one thing off while the question is on
    screen: the card is stale. The trip still starts, on a real stop."""
    out = _node("""
twoShops();
click({ gro: 'start-trip' });
groceryState.data.stores.Metro.sections = [];
click({ gro: 'head-for', store: 'Metro' });
console.log(JSON.stringify(tripState()));
""")
    assert out["step"] == "trip"
    assert out["at"] == "Costco"
    assert out["stops"] == ["Costco"]


@_needs_node
def test_the_back_gesture_onto_the_question_never_resnapshots_a_live_trip():
    """HEADED, TRIP and NEXT each push a history entry, so two back
    gestures from WHERE NEXT land on the question with a trip already on.
    Picking a card there used to take a fresh snapshot — Costco no longer
    done, the count of what came home back to zero (found by review).
    Now it reads the card as WHERE NEXT would: into the open stop."""
    out = _node("""
// The history path checks for a built panel and re-renders; the harness
// has neither, so both are stood in for (function declarations rebind).
groIsBuilt = function () { return true; };
renderGrocery = function () {};
twoShops();
click({ gro: 'start-trip' });
click({ gro: 'head-for', store: 'Costco' });
click({ gro: 'stop-done' });
settle(function () {
  groceryState.tripBought = 2;
  applyGroceryStepFromHistory({ groStep: 'headed' });
  const onQuestion = groceryState.step;
  click({ gro: 'head-for', store: 'Metro' });
  const s = tripState();
  s.onQuestion = onQuestion;
  s.bought = groceryState.tripBought;
  // …and a finished stop's card resumes (into the open stop, the way
  // "Continue the trip" does) rather than reopening Costco.
  applyGroceryStepFromHistory({ groStep: 'headed' });
  click({ gro: 'head-for', store: 'Costco' });
  s.afterDoneCard = groceryState.step + '@' + groTripStore();
  s.doneAfter = Object.keys(groceryState.tripDone);
  console.log(JSON.stringify(s));
});
""")
    assert out["onQuestion"] == "headed"
    assert out["step"] == "trip" and out["at"] == "Metro"
    assert out["done"] == ["Costco"], "what is behind us stays behind us"
    assert out["bought"] == 2, "and the count of what came home survives"
    assert out["afterDoneCard"] == "trip@Metro", "a finished stop is not reopened from here"
    assert out["doneAfter"] == ["Costco"]


# --- 2. what the screen says --------------------------------------------------


@_needs_node
def test_the_question_has_a_title_a_count_and_no_dock():
    out = _node("""
twoShops();
console.log(JSON.stringify({
  head: groHeadFor(groceryState.data, 'headed'),
  dock: groDockHtml(groceryState.data, 'headed')
}));
""")
    assert out["head"]["title"] == "Where are we headed?"
    assert out["head"]["sub"] == "2 stops · 3 things"
    assert out["head"]["back"] == "‹ Shop"
    assert out["dock"] == "", "the cards are the choice — no dock, no second apricot"


@_needs_node
def test_the_cards_carry_the_store_its_count_and_what_is_only_bought_there():
    out = _node("""
twoShops();
groceryState.itemStorePrefs = { rice: 'Costco', eggs: 'Metro' };
console.log(JSON.stringify(groHeadedHtml(groceryState.data)));
""")
    assert out.count('class="gro-stop"') == 2, "one card per stop"
    assert 'data-gro="head-for" data-store="Costco"' in out
    assert 'data-gro="head-for" data-store="Metro"' in out
    assert "gro-stop-spine" in out and "var(--store-" in out, "the store's own colour, from the palette tokens"
    assert '<span class="gro-stop-name">Costco</span>' in out
    assert '<span class="gro-stop-count">2 things</span>' in out, "before the trip nothing is 'left' yet"
    assert '<span class="gro-stop-count">1 thing</span>' in out
    assert '<span class="gro-stop-only-label">Only here</span>Rice</span>' in out
    assert '<span class="gro-stop-only-label">Only here</span>Eggs</span>' in out
    assert "gro-primary" not in out


@_needs_node
def test_only_here_is_read_off_the_remembered_preferences_not_the_row():
    """Every row on a stop's list has that store; only a remembered
    item -> store preference says the household can't get it elsewhere.
    Nothing remembered, nothing claimed."""
    out = _node("""
twoShops();
groceryState.itemStorePrefs = {};
console.log(JSON.stringify(groHeadedHtml(groceryState.data)));
""")
    assert "Only here" not in out


@_needs_node
def test_only_here_names_three_and_counts_the_rest():
    out = _node("""
twoShops();
groceryState.data.stores.Costco.sections[0].items = [
  { id: 1, item: 'Rice', store: 'Costco', store_decided: 1 },
  { id: 2, item: 'Oats', store: 'Costco', store_decided: 1 },
  { id: 4, item: 'Coffee', store: 'Costco', store_decided: 1 },
  { id: 5, item: 'Butter', store: 'Costco', store_decided: 1 },
  { id: 6, item: 'Salmon', store: 'Costco', store_decided: 1 }
];
groceryState.itemStorePrefs = { rice: 'Costco', oats: 'Costco', coffee: 'Costco', butter: 'Costco', salmon: 'Costco' };
console.log(JSON.stringify(groOnlyHereLine(groOnlyHereItems(groceryState.data, 'Costco'))));
""")
    assert out == "Rice, Oats, Coffee +2"


@_needs_node
def test_where_next_is_the_same_cards():
    out = _node("""
twoShops();
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripDone = { Costco: true };
groceryState.itemStorePrefs = { eggs: 'Metro' };
console.log(JSON.stringify(groNextHtml(groceryState.data)));
""")
    assert out.count('class="gro-stop"') == 1
    assert 'data-gro="next-stop" data-store="Metro"' in out
    assert '<span class="gro-stop-count">1 thing left</span>' in out, "between stops the count says what is left"
    assert "Only here</span>Eggs" in out


@_needs_node
def test_the_shopless_note_is_a_celadon_tile_under_the_cards_on_both_screens():
    out = _node("""
twoShops();
groceryState.data.stores.Unassigned.sections = [{ section: 'other', items: [
  { id: 9, item: 'Batteries', store: '', store_decided: 1 }
] }];
groceryState.tripStops = ['Costco', 'Metro'];
groceryState.tripDone = { Costco: true };
console.log(JSON.stringify({ headed: groHeadedHtml(groceryState.data), next: groNextHtml(groceryState.data) }));
""")
    for html in (out["headed"], out["next"]):
        assert '<p class="gro-stops-note">1 thing with no shop will come with you.</p>' in html
        assert html.index("gro-stops-note") > html.rindex('class="gro-stop"'), "under the cards, not above them"


# --- 3. the design rules --------------------------------------------------------


def _stops_block() -> str:
    start = SHELL_CSS.index("/* ---------- WHERE ARE WE HEADED / WHERE NEXT: the stop cards ----------")
    end = SHELL_CSS.index("/* A full-width action that is NOT the screen's primary", start)
    return SHELL_CSS[start:end]


def test_the_cards_are_cards_and_use_tokens_only():
    """Rule 9 — no literal colour; §5 — a card is --surface, a hairline and
    --radius-card; §4 — the spine is the joined-tile motif (the card clips
    it, so it is square on the shared edge and round outside)."""
    block = _stops_block()
    for line in block.splitlines():
        code = line.split("/*")[0]
        assert "#" not in code, f"literal colour in the stop-card CSS: {line.strip()}"
    card = block[block.index(".gro-stop {"):block.index("}", block.index(".gro-stop {"))]
    assert "background: var(--surface)" in card
    assert "border: 1.5px solid var(--hairline)" in card
    assert "border-radius: var(--radius-card)" in card
    assert "overflow: hidden" in card, "the spine takes the card's own corner on its outside edge"
    assert "min-height: 60px" in card, "Rule 6 — a real tap target"
    assert "gap: 12px" in block[block.index(".gro-stops {"):], "the card rhythm, not a list's hairlines"


def test_the_colour_is_the_stores_own_and_the_note_is_celadon():
    block = _stops_block()
    spine = block[block.index(".gro-stop-spine {"):block.index("}", block.index(".gro-stop-spine {"))]
    assert "color: var(--on-accent-ink)" in spine, "Rule 1 — dark ink on the light store fill"
    note = block[block.index(".gro-stops-note {"):block.index("}", block.index(".gro-stops-note {"))]
    assert "background: var(--celadon-tint)" in note
    assert "border: 1.5px solid var(--celadon-edge)" in note
    assert "color: var(--ink-on-celadon)" in note
    label = block[block.index(".gro-stop-only-label {"):block.index("}", block.index(".gro-stop-only-label {"))]
    assert "color: var(--apricot-label)" in label, "the micro-label token; never an apricot FILL"
    assert "var(--apricot)" not in block.replace("var(--apricot-label)", ""), "no second apricot on a choice screen (Rule 5)"
    assert "plum" not in block and "violet" not in block, "purple never leads (Rule 2)"


def test_the_old_row_component_is_gone():
    """One component, not two that drift: WHERE NEXT no longer has a row
    class of its own."""
    assert ".gro-nextrow" not in SHELL_CSS
    assert "gro-nextrow" not in SHELL_JS
    assert "function groStopCardsHtml(" in SHELL_JS
    assert "groStopCardsHtml(data, remaining, 'next-stop'" in SHELL_JS
    assert "groStopCardsHtml(data, stops, 'head-for'" in SHELL_JS


def test_the_step_is_documented_with_its_siblings():
    assert "HEADED" in SHELL_JS[SHELL_JS.index("Grocery — four STEPS"):SHELL_JS.index("var GRO_CATEGORY_LABELS")]
    assert "else if (step === 'headed') body.innerHTML = groHeadedHtml(data);" in SHELL_JS
