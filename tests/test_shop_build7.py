"""Shop, Build 7 of the screen-by-screen redesign (Emily, 2026-09-11, decision J):
sorting comes before the list when something has no store."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _fn(name):
    start = SHELL_JS.index("  function %s(" % name)
    return SHELL_JS[start:SHELL_JS.index("\n  }\n", start)]


def test_the_list_lands_on_sort_first_only_from_the_list_and_only_until_later():
    first = _fn("groMaybeSortFirst")
    assert "if (groceryState.step !== 'list' || groceryState.sortDeferred) return;" in first
    assert "if (groStoresPromptShouldShow()) return;" in first
    assert "goGroceryStep(toSort >= GRO_FAST_SORT_MIN ? 'sorthow' : 'sort', { push: false, first: true });" in first
    # "Before the list" is only the automatic landing: any other way into a
    # sort screen, and finishing the queue, is the ordinary screen again
    # (verifier, 2026-09-11).
    assert "if (!opts.first) groceryState.sortFirst = false;" in SHELL_JS
    # "Open the list" after an approval sorts first too.
    assert "goGroceryStep('list');\n    // …unless something has no store yet" in SHELL_JS
    # Runs after the list loads AND after the usual stores arrive (they race).
    assert SHELL_JS.count("groMaybeSortFirst();") >= 2
    # A refill (approval) is a new list: sorting comes first again.
    assert "groceryState.sortDeferred = false;\n    if (groIsBuilt()) loadGrocery();" in SHELL_JS


def test_later_is_a_link_on_both_sort_screens_and_the_crumb_means_later_too():
    assert SHELL_JS.count('data-gro="sort-later">Sort them later</button>') == 2
    assert "case 'sort-later':" in SHELL_JS
    back = SHELL_JS[SHELL_JS.index("case 'step-back':"):]
    assert "if (GRO_SORT_STEPS.indexOf(groceryState.step) !== -1) groceryState.sortDeferred = true;" in back[:1600]


def test_the_first_landing_says_before_the_list():
    head = _fn("groHeadFor")
    assert head.count("groceryState.sortFirst ? 'Before the list' : 'Where does this go?'") == 2


def test_the_head_lost_its_mic_and_refresh():
    assert "var SHOW_GRO_HEADER_TOOLS = false;" in SHELL_JS
    assert "(SHOW_GRO_HEADER_TOOLS ? '' : 'hidden ')" in SHELL_JS
    assert "try the refresh button above" not in SHELL_JS
