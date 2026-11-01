"""
The add toast leads with the verb — "Added Lemons" — so no was/were is
guessed from a food's name (Emily, 2026-10-31: "Lemons was added" read
wrong; "Swiss chard" and "hummus" defeat any plural rule).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
KITCHEN_ADD_JS = (REPO / "static" / "kitchen-add.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed")


def _run(script: str):
    prelude = (
        "var SAVED_PLAIN = 'Saved'; var SAVED_NAME_MAX = 44;\n"
        + "".join(_extract(n, SHELL_JS) + "\n" for n in (
            "savedName", "savedLine", "savedCount", "addedLine", "addedCount", "bsNamesSaid", "groAddedMessage"))
    )
    res = nodeharness.run_node(prelude + script, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_plural_and_singular_names_read_right_with_no_guess():
    out = _run(
        "console.log(JSON.stringify([groAddedMessage('Lemons'), groAddedMessage('Swiss chard'),"
        " groAddedMessage(''), addedLine('Hummus', 'to your regulars'), addedLine(''),"
        " bsNamesSaid(['Coffee'], 'added'), bsNamesSaid(['Coffee', 'Eggs'], 'added'),"
        " bsNamesSaid(['a', 'b', 'c'], 'added'), addedCount(1),"
        " bsNamesSaid(['Coffee', 'Eggs'], 'moved to Already had on hand')]));"
    )
    assert out == [
        "Added Lemons", "Added Swiss chard", "Added to the list", "Added Hummus to your regulars", "Added",
        "Added Coffee", "Added Coffee and Eggs", "Added 3 things", "Added 1 thing",
        # Not an add: the house pattern stays as it was.
        "Coffee and Eggs were moved to Already had on hand",
    ]


def test_no_add_toast_still_guesses_was_or_were():
    for name, src in (("shell.js", SHELL_JS), ("kitchen-add.js", KITCHEN_ADD_JS)):
        code = "\n".join(line for line in src.splitlines() if not line.strip().startswith(("//", "*")))
        hits = re.findall(r"""['"][^'"\n]*\b(?:was|were) added\b[^'"\n]*['"]""", code)
        hits = [h for h in hits if "Nothing was added" not in h]
        assert not hits, f"{name}: {hits}"
        assert not re.search(r"savedLine\([^)\n]*'added'\)", code), name
