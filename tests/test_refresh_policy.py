"""
The shell builds each tab's panel once and does not refetch on a tab switch
(`panel.dataset.built`, static/shell.js). That is deliberate — rebuilding on
every tap would refetch on every tap, which on a phone in a grocery store is
slow and burns data. The cost of it is that anything changing a panel's data
by some *other* route leaves that panel showing stale content until a reload.

`refreshStaleTabsFromActions()` is what closes that gap for chat-driven
changes: the backend tags every write tool with the surface it changed
(`_categorize_tool` in app/main.py), and the shell refreshes whatever the
assistant just touched.

The defect this file exists to catch is that the two halves are joined by
nothing but memory. The backend can grow a new category, or repoint an
existing one, and the shell simply has no branch for it — no error, no
console warning, no failing test. The screen just quietly goes on showing
the old data. That has already happened twice: Grocery and Kitchen were both
tagged by the backend for a long time while this function had no branch for
either, and both were found by a person noticing wrong numbers rather than by
anything automated.

This is the same shape as the iframe-guard bug that tests/test_embedded_pages
was written for, and it gets the same treatment: derive both sides from the
app, and fail here rather than shipping.

Both derivations are pinned against being silently empty
(test_the_derivations_are_not_silently_empty). Without that, an ordinary
refactor — renaming the function, or moving the category constants — would
make every assertion below pass while covering nothing at all.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app import main as app_main

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text()

REFRESH_FN = "refreshStaleTabsFromActions"


def _tool_sets() -> dict[str, frozenset]:
    """The `*_TOOLS` constants _categorize_tool dispatches on.

    frozenset is accepted as well as set: swapping one for the other is a
    behaviour-identical "constants should be immutable" cleanup, and an
    earlier version of this file silently stopped covering Grocery when that
    was done to `_GROCERY_TOOLS`. It kept passing, which is the failure mode
    this whole file exists to prevent.
    """
    return {
        name: frozenset(getattr(app_main, name))
        for name in dir(app_main)
        if name.endswith("_TOOLS") and isinstance(getattr(app_main, name), (set, frozenset))
    }


def _returned_surfaces() -> tuple[set[str], set[str]]:
    """(tabs, hrefs) read from _categorize_tool's own `return` statements.

    Calling the function with harvested tool names only ever discovers
    branches keyed on those sets. A branch written any other way — a
    `startswith("store_")` prefix, a regex, an inline condition — is
    invisible to that approach, and it is exactly as capable of introducing
    a tab the shell has no branch for. So the source is read as well.
    """
    src = inspect.getsource(app_main._categorize_tool)
    tabs, hrefs = set(), set()
    # return "<category>", "<tab>"|None, "<href>"|None
    for tab, href in re.findall(
        r"return\s+['\"][^'\"]+['\"]\s*,\s*(['\"][^'\"]*['\"]|None)\s*,\s*(['\"][^'\"]*['\"]|None)",
        src,
    ):
        for raw, bucket in ((tab, tabs), (href, hrefs)):
            if raw != "None":
                value = raw.strip("'\"")
                if value:
                    bucket.add(value)
    return tabs, hrefs


def _emittable_actions() -> tuple[set[str], set[str]]:
    """(tabs, hrefs) a chat action card can actually carry, asked of the app.

    Two independent derivations, unioned, because each is blind to something
    the other sees: calling the function covers set-keyed branches without
    caring how they are written, reading the source covers branches that are
    not keyed on a set at all.
    """
    tools = set()
    for members in _tool_sets().values():
        tools |= members
    # The catch-all branch. A tool name that is in no explicit set still
    # produces an action card, and that card still points somewhere.
    tools.add("some_tool_nobody_has_categorised_yet")

    tabs, hrefs = _returned_surfaces()
    for tool in tools:
        _category, tab, href = app_main._categorize_tool(tool)
        if tab:
            tabs.add(tab)
        if href:
            hrefs.add(href)
    return tabs, hrefs


def _refresh_body() -> str:
    """The source of refreshStaleTabsFromActions, by brace matching.

    Read from the app rather than pasted here so that renaming or gutting the
    function is a failure in this file, not a silent loss of coverage.
    """
    start = SHELL_JS.find("function %s(" % REFRESH_FN)
    if start == -1:
        return ""
    open_brace = SHELL_JS.find("{", start)
    if open_brace == -1:
        return ""
    depth = 0
    for i in range(open_brace, len(SHELL_JS)):
        if SHELL_JS[i] == "{":
            depth += 1
        elif SHELL_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return SHELL_JS[open_brace : i + 1]
    return ""


def _strip_comments(js: str) -> str:
    """Drop whole-line `//` comments and `/* */` blocks.

    This function's body is more comment than code, and every assertion below
    would otherwise be satisfiable by prose. Deleting the `/memory` branch and
    leaving its explanatory comment behind passed an earlier version of this
    file — the comment still said "href", so the check still matched.

    Deliberately only whole-line comments, never a trailing `//` on a code
    line: shell.js is full of `https://` inside string literals, and cutting
    at the first `//` would corrupt them.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))


def _refresh_code() -> str:
    """The function's actual code, with its prose removed."""
    return _strip_comments(_refresh_body())


def _handled_tabs() -> set[str]:
    """Tab names refreshStaleTabsFromActions actually branches on.

    This reads the literal form `action.tab === 'name'`. Rewriting those
    branches as a `switch`, or hoisting `var tab = action.tab` first, is a
    behaviour-preserving refactor that this regex cannot follow — so
    test_the_derivations_are_not_silently_empty asserts this comes back
    non-empty. Without that, such a refactor fails the coverage tests with a
    message accusing you of a stale-tab bug you did not introduce. If you are
    reading this because of that failure: the branches are fine, the pattern
    here needs updating.
    """
    return set(re.findall(r"action\.tab\s*===\s*['\"]([^'\"]+)['\"]", _refresh_code()))


def _href_sheet_keys() -> set[str]:
    """Hrefs the shell knows how to open in place, from HREF_AS_SHEET."""
    match = re.search(r"HREF_AS_SHEET\s*=\s*\{([^}]*)\}", SHELL_JS)
    if not match:
        return set()
    return set(re.findall(r"['\"]([^'\"]+)['\"]\s*:", match.group(1)))


def _require_parsed_tabs() -> set[str]:
    """Fail with an accurate message when the *parser*, not the shell, broke.

    Without this the coverage test below reports "the shell has no branch for
    any of these tabs" after a purely cosmetic refactor of those branches —
    an accusation of a bug that does not exist, which is a worse failure than
    no test at all.
    """
    handled = _handled_tabs()
    assert handled, (
        "Parsed no `action.tab === '...'` branches out of "
        f"{REFRESH_FN}, so nothing below can be checked. Almost certainly the "
        "branches were refactored into a form this file cannot read (a "
        "switch, or a hoisted `var tab = action.tab`) rather than deleted. "
        "Update _handled_tabs() here — check shell.js only if the branches "
        "really are gone."
    )
    return handled


def test_every_tab_the_backend_tags_has_a_refresh_branch():
    """A write tool tagged with a tab whose panel nobody refreshes is a
    stale screen: the change lands in the database and the tab goes on
    showing what it read at build time."""
    _require_parsed_tabs()
    emittable, _ = _emittable_actions()
    missing = emittable - _handled_tabs()
    assert not missing, (
        "app/main.py's _categorize_tool can tag a chat action with "
        f"{sorted(missing)}, but static/shell.js's {REFRESH_FN} has no branch "
        "for it. A change made through chat will leave that tab showing stale "
        f"data until a full reload. Add a branch there (see the 'grocery' and "
        "'kitchen' ones, which were both missing for exactly this reason)."
    )


def test_every_href_the_backend_tags_is_handled_in_the_shell():
    """The household/preferences tools carry no tab at all — only
    href: '/memory'. The shell reads the href instead, so that mapping is
    load-bearing in exactly the same way a tab branch is."""
    _, emittable = _emittable_actions()
    missing = emittable - _href_sheet_keys()
    assert not missing, (
        f"_categorize_tool can tag a chat action with href {sorted(missing)}, "
        "which static/shell.js's HREF_AS_SHEET does not map to a surface it "
        "can refresh in place. followActionHref would navigate the whole page "
        "out of the shell, and nothing behind it would be refreshed."
    )


def test_the_refresh_function_reads_the_href_not_only_the_tab():
    """The memory category is tab-less by design. A refactor that reduced this
    function to a switch on action.tab would drop that whole category without
    failing either test above."""
    assert "hrefSheetKey" in _refresh_code(), (
        f"{REFRESH_FN} no longer routes on action.href via hrefSheetKey(). "
        "Household and preferences writes carry no tab — only href: '/memory' "
        "— so they would silently stop refreshing the Kitchen hub behind them."
    )


def test_the_derivations_are_not_silently_empty():
    """Each of these returning nothing would make the assertions above pass
    while checking nothing. Renaming the function, or moving the category
    constants out of main.py, does exactly that."""
    tabs, hrefs = _emittable_actions()
    assert tabs, "No tabs derived from _categorize_tool — the derivation broke."
    assert hrefs, "No hrefs derived from _categorize_tool — the derivation broke."
    # All-or-nothing emptiness is not enough: moving *some* of the constants
    # out of main.py shrinks coverage silently while leaving these non-empty.
    missing_sets = {
        "_CHORE_TOOLS", "_WEEK_TOOLS", "_KITCHEN_TOOLS", "_GROCERY_TOOLS", "_MEMORY_HREF_TOOLS",
    } - set(_tool_sets())
    assert not missing_sets, (
        f"app/main.py no longer exposes {sorted(missing_sets)} as a set/frozenset. "
        "If these moved or were renamed, this file's derivation is now covering "
        "less than it claims — update _tool_sets() and this list together."
    )
    assert _handled_tabs(), (
        "Parsed no `action.tab === '...'` branches out of "
        f"{REFRESH_FN}. The coverage tests below would then blame the shell "
        "for missing every tab. Far more likely: those branches were "
        "refactored into a form this file's regex cannot read (a switch, or a "
        "hoisted local). Update _handled_tabs(), not shell.js."
    )
    assert _refresh_body(), (
        f"Could not find {REFRESH_FN} in static/shell.js. If it was renamed, "
        "update REFRESH_FN here; every assertion in this file is vacuous "
        "until you do."
    )
    assert _href_sheet_keys(), (
        "Could not read HREF_AS_SHEET from static/shell.js — the href "
        "assertion above is vacuous until this parses again."
    )


@pytest.mark.parametrize("tab", ["today", "week", "kitchen", "grocery"])
def test_the_known_tabs_are_still_covered(tab):
    """The four tabs that exist today, pinned by name as well as by
    derivation — so that a derivation which starts returning a *smaller* set
    (rather than an empty one) still fails here."""
    assert tab in _require_parsed_tabs()
