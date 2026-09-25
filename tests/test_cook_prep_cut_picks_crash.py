"""
The Cook screen must render for a meal that has prep cuts to offer.

FOUND IN PRODUCTION, 2026-09-25, by the morning error report — the first
time that report could reach the live app in days. Seven `TypeError`s on one
household in one day, from three entry points (`cookEnterFocus` twice over,
and `loadKitchen` on both /week and /kitchen), every one of them landing on
the same line.

WHAT IT WAS: `cookState.prepCutPicks` was read in two functions and declared
nowhere, so it was `undefined` and the first `[entry_id]` on it threw. The
throw left `cookPrepCutHtml`, went up through `cookRecipeHtml` into
`renderCook`, and nothing caught it — so this is not a missing chip, it is
the whole Cook screen failing to render.

NOT a regression from the 2026-09-25 merge: the same four references and the
same missing declaration are on `b9d6721`, the commit before it. It arrived
with `9330a10` ("Cook: the recipe is the recipe, and the cooker has no
clock").

WHY IT WASN'T CONSTANT: `cookPrepCutHtml` returns early when the meal has no
prep-cut options, so only a meal that HAS some reaches the bad line.

These tests run shell.js's own functions under node rather than reading the
source for a marker — the defect is a read of an undefined property, which
is exactly what a source-marker test cannot see.
"""
from __future__ import annotations

import json
import pathlib

from nodeharness import run_node

SHELL_JS = pathlib.Path(__file__).resolve().parents[1] / "static" / "shell.js"


def _function_source(name: str) -> str:
    """One function's text, brace-matched from its own declaration."""
    src = SHELL_JS.read_text()
    for opener in ("  function %s(" % name, "  async function %s(" % name):
        if opener in src:
            i = src.index(opener)
            break
    else:
        raise AssertionError("no function %s in shell.js" % name)
    depth = 0
    started = False
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
            started = True
        elif src[j] == "}":
            depth -= 1
            if started and depth == 0:
                return src[i : j + 1]
    raise AssertionError("unterminated function %s" % name)


def _cook_state_literal() -> str:
    """`cookState` exactly as shell.js declares it — the thing under test."""
    src = SHELL_JS.read_text()
    i = src.index("  var cookState = {")
    j = src.index("\n  };", i)
    return src[i : j + len("\n  };")]


def _run(body: str) -> str:
    """
    Through tests/nodeharness.py, never `node -e`: that form dies at the
    kernel's 128 KiB per-argument limit, and tests/test_node_harness_size.py
    is a standing guard against reintroducing it. The first version of this
    file used `-e` and duly turned that guard red — the guard working.
    """
    script = "\n".join([_cook_state_literal(), _function_source("cookPrepCutPicks"), body])
    out = run_node(script)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_reading_a_meals_prep_cut_picks_does_not_throw():
    """
    THE CATCH. Red on main with `TypeError: Cannot read properties of
    undefined (reading '7')` — the production error, reproduced exactly.
    """
    got = _run(
        """
        try {
          var picks = cookPrepCutPicks({ entry_id: 7 });
          console.log(JSON.stringify({ ok: true, picks: picks }));
        } catch (e) {
          console.log(JSON.stringify({ ok: false, error: e.constructor.name + ': ' + e.message }));
        }
        """
    )
    result = json.loads(got)
    assert result["ok"], (
        "the Cook screen still crashes on a meal with prep cuts to offer: "
        + result.get("error", "")
    )
    assert result["picks"] == {}, "a meal nobody has ticked yet starts empty"


def test_a_tick_is_remembered_and_two_meals_do_not_share_one():
    """
    CATCH. Red on main for the same reason (it cannot get this far), and it
    pins the thing the declaration is FOR: the picks are per meal, so ticking
    a cut on one dinner must not tick it on another.
    """
    got = _run(
        """
        cookPrepCutPicks({ entry_id: 7 })['Onion'] = true;
        console.log(JSON.stringify({
          seven: cookPrepCutPicks({ entry_id: 7 }),
          nine: cookPrepCutPicks({ entry_id: 9 })
        }));
        """
    )
    result = json.loads(got)
    assert result["seven"] == {"Onion": True}, "the tick was not remembered"
    assert result["nine"] == {}, "two meals shared one set of picks"


def test_the_state_object_declares_it_rather_than_leaving_it_to_be_made():
    """
    GUARD, and the one that would catch this class coming back by another
    route: both readers index `cookState.prepCutPicks` directly and neither
    can create it, so the declaration is what makes them safe. Red on main.
    """
    assert "prepCutPicks: {}," in _cook_state_literal(), (
        "cookState no longer declares prepCutPicks — the two readers "
        "(cookPrepCutPicks, cookAddPrepCuts) index it directly and will throw"
    )


def test_every_reader_of_it_is_one_this_declaration_covers():
    """
    GUARD on the guard. Green on main and on the branch — it exists so that a
    THIRD reader added later is noticed rather than inheriting a promise the
    comment makes about two. If this fails, check the new call site really is
    safe with a plain `{}` and then widen the list.
    """
    lines = SHELL_JS.read_text().splitlines()
    readers = set()
    enclosing = None
    for line in lines:
        stripped = line.lstrip()
        # Comments are skipped BEFORE anything else. The first version of this
        # sweep did not, and duly matched the explaining comment on the
        # declaration itself — attributing it to whatever function happened to
        # be declared above cookState. That is the "a marker its own comment
        # satisfies" trap this repo has recorded being bitten by three times,
        # arriving inside the test written to guard against a different one.
        if stripped.startswith("//"):
            continue
        if stripped.startswith("function ") or stripped.startswith("async function "):
            enclosing = stripped.split("function ", 1)[1].split("(")[0]
        if "cookState.prepCutPicks" in line:
            readers.add(enclosing)
    assert readers == {"cookPrepCutPicks", "cookAddPrepCuts"}, (
        "the set of functions reading cookState.prepCutPicks has changed: %s" % sorted(readers)
    )
