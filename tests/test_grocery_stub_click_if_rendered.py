"""
The Grocery node harness can only press what the screen actually drew.

Before 2026-09-21 the shared stub's `click()` fabricated an element and
dispatched straight into `onGroceryClick`, with nothing checking that any
renderer had ever drawn such a control. So a test could "tap" a button
the screen under test does not have, and the handler would happily run:
every test on that stub was testing the HANDLER, not the SCREEN. An
end-to-end walk could pass against a broken screen — reviewer, 2026-09-17,
who took the two-shops walk, removed the one line that read the dock
before tapping it, ran it against the broken file that branch existed to
fix, and got "Bought 2 of 2 / Trip finished — 2 things home."

`clickIfRendered` closes it: it builds the current step's HTML out of the
region's own renderers and refuses to dispatch a control that screen does
not draw. `clickHandlerDirectly` is the old spelling, kept under a name
that says what it is, for the two taps that genuinely mean "call the
handler directly".

The first four tests here are the reproduction, kept as the guard: each
one is a tap that used to run and now raises.

What the sweep found, 2026-09-21, converting all 48 call sites across the
nine files that run the Grocery region: FIVE tests were pressing a control
the screen does not render at that step. Three were harness artifacts and
are fixed in place, each with a note saying what moved — a set-aside chip
tapped inside a closed ⋯, a "Put it on the list" tapped for a row whose
field was never opened, and two spice boxes tapped through a fold that is
closed by default. Two are genuine "call the handler directly" cases and
keep the old spelling under its own name: a deliberate stray tap on a
screen a one-shop household is never shown, and a double-tap on a row that
has left groUnsorted but is still on screen collapsing, which a harness
with no DOM cannot draw. NO REAL SCREEN DEFECT was hiding behind any of
them — said plainly, because that is the answer the sweep gave and not the
one it was hoping for.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from shop_harness import SHELL_JS, needs_node, run

TESTS = Path(__file__).resolve().parent

# A refused tap throws, which in node is a crash: catch it so the test can
# read the sentence it was refused with.
_TRIED = """
function tried(fn) { try { fn(); return null; } catch (e) { return String(e.message); } }
"""

# A real screen to press: one shop with a bought row and an unbought one,
# plus a loose thing. Lifted from the checklist file's own mockup so these
# are taps on a screen the app really renders, not a toy.
_MOCKUP = _TRIED + """
function mockup() {
  groceryState.data = { stores: {
    Unassigned: { sections: [{ section: 'other', items: [
      { id: 20, item: 'Foil', quantity: '', store: '', store_decided: 0, category: 'other', status: 'needed' }] }], purchased: [], inCart: [] },
    Costco: { sections: [{ section: 'pantry', items: [
        { id: 3, item: 'Orzo', quantity: '1 box', store: 'Costco', store_decided: 1, category: 'pantry', status: 'needed' }] }],
      purchased: [
        { id: 1, item: 'Chicken thighs', quantity: '2 lb', store: 'Costco', store_decided: 1, category: 'meat/seafood', status: 'purchased' }],
      inCart: [] }
  } };
  groceryState.usualStores = ['Costco'];
  groceryState.storesPromptDismissed = true;
  groceryState.step = 'list';
  return groceryState.data;
}
"""



# --- 1. the reproduction, kept as the guard ---------------------------------


@needs_node
def test_an_action_the_step_renders_nowhere_is_refused_by_name():
    """CARRY's keep/drop pair is not on the LIST screen at all. The old
    click() POSTed a carried-over decision from it anyway."""
    out = run(_MOCKUP + """
mockup();
const rendered = groListHtml(groceryState.data).indexOf('data-gro="carry-decide"') !== -1;
const said = tried(function () {
  clickIfRendered({ gro: 'carry-decide', decision: 'drop', id: '3', name: 'Orzo' });
});
console.log(JSON.stringify({ rendered: rendered, said: said, posts: POSTS.length }));
""")
    assert out["rendered"] is False, "the screen under test draws no such control"
    assert out["said"] == 'clickIfRendered: the LIST screen renders no [data-gro="carry-decide"] at all'
    assert out["posts"] == 0, "and nothing was written"


@needs_node
def test_the_old_spelling_would_have_run_it():
    """The same tap, straight into the handler: this is what every call
    site used to do, and it writes."""
    out = run(_MOCKUP + """
mockup();
clickHandlerDirectly({ gro: 'carry-decide', decision: 'drop', id: '3', name: 'Orzo' });
settle(function () { console.log(JSON.stringify(POSTS.map(function (p) { return p.url; }))); });
""")
    assert out == ["/api/grocery-list/3/carried-over"], (
        "the hole, reproduced — kept so the escape hatch is known to still be an escape hatch"
    )


@needs_node
def test_a_row_that_is_not_on_this_list_is_refused_too():
    """The action is rendered; this row is not. The old click() ticked it
    'purchased' and toasted Changes saved."""
    out = run(_MOCKUP + """
mockup();
const said = tried(function () { clickIfRendered({ gro: 'line-tick', id: '999', bought: '0' }); });
console.log(JSON.stringify({ said: said, posts: POSTS.length }));
""")
    # Six, for three rows: the row and its box both carry the action, the
    # way shell.js says they do.
    assert out["said"].startswith('clickIfRendered: the LIST screen renders 6 [data-gro="line-tick"], none of them')
    assert 'data-id="999"' in out["said"] and 'data-bought="0"' in out["said"]
    assert out["posts"] == 0


@needs_node
def test_a_row_in_the_wrong_state_is_refused():
    """Orzo is not bought, so its box says data-bought="0". "Put it back"
    is not a control the screen is offering for that row."""
    out = run(_MOCKUP + """
mockup();
const said = tried(function () { clickIfRendered({ gro: 'line-tick', id: '3', bought: '1' }); });
console.log(JSON.stringify(said));
""")
    assert 'none of them data-id="3" data-bought="1"' in out


@needs_node
def test_a_real_tap_goes_through_untouched():
    out = run(_MOCKUP + """
mockup();
clickIfRendered({ gro: 'line-tick', id: '3', bought: '0' });
clickIfRendered({ gro: 'line-tick', id: '1', bought: '1' });
settle(function () {
  console.log(JSON.stringify(POSTS.map(function (p) { return [p.url, p.body.status]; })));
});
""")
    assert out == [
        ["/api/grocery-list/3/status", "purchased"],
        ["/api/grocery-list/1/status", "needed"],
    ]


@needs_node
def test_a_key_the_markup_does_not_carry_is_not_held_against_the_tap():
    """`name` is a display string the handler reads off the dataset for a
    toast. Some controls carry data-name and some don't; a key nothing
    with this action expresses is the caller's business, not a claim about
    the screen."""
    out = run(_MOCKUP + """
mockup();
const said = tried(function () {
  clickIfRendered({ gro: 'line-tick', id: '3', bought: '0', name: 'a name no box carries' });
});
console.log(JSON.stringify({ said: said, posts: POSTS.length }));
""")
    assert out["said"] is None
    assert out["posts"] == 1


# --- 2. the screen the helper is reading ------------------------------------


@needs_node
def test_the_crumb_counts_as_rendered_on_a_deeper_step_and_not_on_the_root():
    """The crumb lives in the panel scaffold rather than in a step
    renderer, and renderGrocery hides it on the root. Both halves."""
    out = run(_TRIED + """
setUp(6);
const onRoot = tried(function () { clickIfRendered({ gro: 'step-back' }); });
groceryState.step = 'sortall';
const onSortAll = tried(function () { clickIfRendered({ gro: 'step-back' }); });
console.log(JSON.stringify({ onRoot: onRoot, onSortAll: onSortAll, landedOn: groceryState.step }));
""")
    assert out["onRoot"] == 'clickIfRendered: the LIST screen renders no [data-gro="step-back"] at all'
    assert out["onSortAll"] is None
    assert out["landedOn"] == "list", "…and the crumb really is the way back"


@needs_node
def test_a_step_that_stopped_making_sense_is_read_as_the_root():
    """renderGrocery falls back to the list when SORT ALL has nothing left
    to sort; the helper has to read the same screen, or it would complain
    about the wrong one."""
    out = run(_TRIED + """
setUp(4, [], ['Costco']);
groceryState.step = 'sortall';
console.log(JSON.stringify(tried(function () {
  clickIfRendered({ gro: 'sortall-pick', id: '1', store: 'Costco' });
})));
""")
    assert out.startswith("clickIfRendered: the LIST screen"), (
        "one shop: nothing is ever to sort, so this household is on the list"
    )


# --- 3. the mirror, pinned against renderGrocery's own source ---------------


def _fn_source(name: str) -> str:
    start = SHELL_JS.index("  function " + name + "(")
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_helper_mirrors_render_grocerys_own_dispatch():
    """screenHtml() builds the step's HTML the way renderGrocery does.
    There is no DOM in the harness, so it mirrors that dispatch rather
    than running it — and a fourth step renderer has to be added to the
    mirror, not quietly left unguarded. These are the lines the mirror is
    a copy of; if one of them moves, come and move the mirror with it."""
    render = _fn_source("renderGrocery")
    for line in (
        "if (step === 'carry') body.innerHTML = groCarryHtml(data);",
        "else if (step === 'sortall') groSortAllRender(body, data);",
        "else body.innerHTML = groListHtml(data);",
        "dock.innerHTML = groDockHtml(data, step);",
        "back.hidden = !headFor.back;",
    ):
        assert line in render, f"renderGrocery no longer says {line!r} — the mirror is stale"
    for fallback in (
        "if (groceryState.step === 'sortall' && !groUnsorted(data).length && !groceryState.sortAllDone) {",
        "if (groceryState.step === 'carry' && !groceryState.carried.length) groceryState.step = 'list';",
        "if (groceryState.step !== 'carry' && groceryState.step !== 'sortall') groceryState.step = 'list';",
    ):
        assert fallback in render, f"renderGrocery's step fallback moved: {fallback!r}"
    # The card-less path of the sortall renderer is the HTML the mirror uses.
    assert "body.innerHTML = groSortAllHtml(data);" in _fn_source("groSortAllRender")


def test_the_head_tools_are_still_hidden_so_the_mirror_may_leave_them_out():
    """The mic and refresh buttons sit in the step head beside the crumb.
    The mirror does not model them because the flag renders them `hidden`,
    so nothing can tap them. Flip the flag and they need a home in
    screenHtml()."""
    assert "var SHOW_GRO_HEADER_TOOLS = false;" in SHELL_JS


# --- 4. the two spellings, at the call sites --------------------------------


def _grocery_test_files():
    for path in sorted(TESTS.glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        if "clickIfRendered(" in src or "clickHandlerDirectly(" in src:
            yield path, src


def test_nothing_reaches_for_the_old_unguarded_spelling():
    """`click(` meant "press this" and did "call the handler". The name is
    gone so nobody can reach for it out of habit."""
    for path, src in _grocery_test_files():
        # The call shape, not the word: these files talk about the old
        # `click()` in their prose, which is the point of the prose.
        assert not re.search(r"(?<![A-Za-z_.])click\(\s*\{", src), (
            f"{path.name} still reaches for the old unguarded spelling — say clickIfRendered, or clickHandlerDirectly with a reason"
        )
    assert not re.search(r"(?<![A-Za-z_.])function click\(", (TESTS / "shop_harness.py").read_text(encoding="utf-8"))


def test_every_direct_handler_call_says_why():
    """The escape hatch is only honest if each use carries its reason. A
    comment immediately above it is what a reader has to go on."""
    found = 0
    here = Path(__file__).name
    for path, src in _grocery_test_files():
        # This file's own direct call IS the reproduction, explained at
        # length in the test around it; shop_harness defines the thing.
        if path.name in (here, "shop_harness.py"):
            continue
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "clickHandlerDirectly(" not in line:
                continue
            found += 1
            above = [l.strip() for l in lines[max(0, i - 8):i]]
            assert any(l.startswith("//") for l in above), (
                f"{path.name}:{i + 1} calls the handler directly with no reason above it"
            )
    assert found >= 2, "the two known direct-handler taps are still there to be explained"


def test_the_harness_is_a_module_and_not_collected():
    """shop_harness.py holds the helper; pytest must not try to run it."""
    tree = ast.parse((TESTS / "shop_harness.py").read_text(encoding="utf-8"))
    assert not [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
