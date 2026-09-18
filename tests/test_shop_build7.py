"""Shop, Build 7 of the screen-by-screen redesign (Emily, 2026-09-11, decision J):
sorting came before the list when something had no store.

Rewritten 2026-09-15 (Loop Board, "Shop: the list hides behind the store
question and the sort screen"): the list is always the first screen now, and
SORT is only ever reached from LIST's "N things to sort" row. The three
tests below used to pin the automatic landing (groMaybeSortFirst, the
sortDeferred / sortFirst pair, the "Before the list" title); they now pin
its absence, so a regression back to sort-first fails here. The behaviour
itself is exercised under node in tests/test_shop_list_first.py. The
"later" link and the crumb are kept as SORT's ways out — nothing traps
you, so they no longer have to remember anything.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_list_never_lands_on_sort_by_itself():
    """What groMaybeSortFirst did is gone: the load path asks about last
    week's leftovers (groMaybeCarryFirst) and nothing else. No caller opens
    a sort step with push:false, which was the automatic landing's shape."""
    assert "function groMaybeSortFirst(" not in SHELL_JS
    assert "sortDeferred" not in SHELL_JS.replace("(sortDeferred / sortFirst", "")
    assert "sortFirst" not in SHELL_JS.replace("(sortDeferred / sortFirst", "")
    first = _fn("groMaybeCarryFirst")
    assert "if (groceryState.step !== 'list') return;" in first
    assert "if (groStoresPromptShouldShow()) return;" in first
    assert "goGroceryStep('carry', { push: false });" in first
    assert "'sort'" not in first and "'sorthow'" not in first
    # Runs after the list loads AND after the usual stores arrive (they race).
    assert SHELL_JS.count("groMaybeCarryFirst();") >= 2
    # "Open the list" after an approval lands on the list.
    shim = SHELL_JS.split("function groSetScreen(", 1)[1].split("\n  }", 1)[0]
    assert "goGroceryStep('list');" in shim and "groMaybeCarryFirst();" in shim
    # A refill (the Approve button, Start over) is a new list: the leftovers
    # are asked about again, and the tab may land on CARRY. Unchanged as a
    # rule — what moved on 2026-09-17 is that it is the REFILL callers'
    # behaviour rather than every caller's, because a background re-read
    # arriving from chat, or from a Plan-tab tap, was navigating people off
    # LIST mid-scroll (see tests/test_shop_stale_after_chat.py).
    assert ("    var refill = !!(opts && opts.refill);\n"
            "    if (refill) groceryState.carryDeferred = false;\n"
            "    if (groIsBuilt()) loadGrocery({ background: !refill });") in SHELL_JS
    # The two entry points that mean it, by name rather than by a count.
    # (This was `count(...) == 2` for one day and broke the moment
    # refreshGrocerySurfaces started forwarding `opts` instead of hard-coding
    # refill — a count over a literal is a claim about how the callers are
    # SPELLED, not about which of them mean a new list.)
    assert "refreshGrocerySurfaces({ refill: true });" in SHELL_JS   # Approve
    assert "refreshGroceryPanel({ refill: true })" in SHELL_JS       # Start over
    # ...and nothing else asks for one. Comments stripped first: this
    # function's own prose names the argument it is explaining, and an
    # assertion a comment can satisfy is not an assertion (the same trap
    # tests/test_refresh_policy.py's _strip_comments exists for — and the
    # first draft of THIS line walked straight into it).
    code = "\n".join(l for l in SHELL_JS.splitlines()
                     if not l.lstrip().startswith("//"))
    callers = re.findall(r"(\w+)\(\{ refill: true \}\)", code)
    assert sorted(callers) == ["refreshGroceryPanel", "refreshGrocerySurfaces"], callers
    # The only way into the sort screen is the list's own row (one way to
    # sort since 2026-09-18: SORT ALL).
    assert "case 'goto-sort':\n        goGroceryStep('sortall');" in SHELL_JS
    assert "goGroceryStep('sortall', { push: false" not in SHELL_JS


def test_later_is_the_crumb_and_it_still_leaves():
    """"Sort them later" was a link on the sort screens; since 2026-09-18
    it is simply leaving by the crumb."""
    assert 'data-gro="sort-later"' not in SHELL_JS
    back = SHELL_JS[SHELL_JS.index("case 'step-back':"):]
    assert "goGroceryStep('list');" in back[:1200]
    assert "if (groceryState.step === 'carry') groceryState.carryDeferred = true;" in back[:1200]
    assert "sortDeferred" not in back[:1200]


def test_sort_only_ever_asks_its_own_question():
    """"Before the list" was the automatic landing's title; "Where does
    this go?" the queue's. SORT ALL is named for what it does."""
    head = _fn("groHeadFor")
    assert "'Before the list'" not in head, "the old title is a comment now, never copy"
    assert "title: 'Where does this go?'" not in head
    assert head.count("title: 'Sort them all'") == 1


def test_the_head_lost_its_mic_and_refresh():
    assert "var SHOW_GRO_HEADER_TOOLS = false;" in SHELL_JS
    assert "(SHOW_GRO_HEADER_TOOLS ? '' : 'hidden ')" in SHELL_JS
    assert "try the refresh button above" not in SHELL_JS
