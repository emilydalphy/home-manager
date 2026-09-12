"""
No test may hand node a script as a command-line argument.

Loop Board "44 front-end tests stopped running as shell.js grew", found
2026-09-12. Linux caps a SINGLE command-line argument at 128 KiB
(MAX_ARG_STRLEN — a kernel constant, and a different, far smaller limit
than the ARG_MAX that governs the whole argument list). Nineteen test
files build a harness of prelude + a sliced region of static/shell.js and
run it under node; while they passed that harness as `-e <script>`, every
one whose slice grew past 128 KiB was rejected by the operating system
before node read a character of it, with

    OSError: [Errno 7] Argument list too long: 'node'

44 tests were in that state on main — not failing an assertion, never
reaching one. They pass on their merits once actually run.

The failure arrived by growth rather than by an edit, which is why it went
unnoticed: nothing about those tests changed, shell.js simply crossed a
line. And macOS allows a much larger single argument, so this passes on a
Mac and fails on Linux, CI included.

tests/nodeharness.py is the fix — the script goes in a temp file and node
is pointed at the path.

WHAT THESE TWO TESTS DO AND DO NOT DO. The first scans every .py under
tests/, the helper included. An earlier version globbed test_*.py only,
which excluded tests/nodeharness.py — the one file where going back to
`-e` would undo the whole fix — and an independent review proved the hole:
reverting the helper left both of these green while 38 other tests went
red. It now catches that, and a new call site in either quote style, with
-e or --eval or -p, on one line or split across several. What it cannot
see is a script assembled at runtime into a variable that happens to hold
"-e"; nothing here does that, and the second test is the backstop for the
guard rotting generally.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
SHELL_JS = REPO / "static" / "shell.js"

# The kernel's per-argument limit, and the whole reason this file exists.
MAX_ARG_STRLEN = 128 * 1024

# node accepts -e, --eval and -p; any of them takes the script as an argument.
NODE_FLAG = re.compile(r"""["'](?:-e|--eval|-p|--print)["']""")


def test_no_test_passes_a_script_to_node_as_an_argument():
    """
    The catch. A new `node -e` call site reintroduces a ceiling that is
    already lower than the file these harnesses slice from, so it would
    not merely risk the bug — on any harness of real size it would be it.
    """
    offenders = []
    # Every .py under tests/, not just test_*.py — an independent review
    # pointed out that the first version excluded tests/nodeharness.py
    # itself, the one file where reverting to `-e` would undo the whole
    # fix. Verified: with the narrow glob, putting `-e` back in the helper
    # left both of these green while 38 other tests went red.
    for path in sorted(TESTS.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            # Both quote styles and both spellings node accepts, and the
            # flag need not be on the same line as "node" — a reformat that
            # split the list across lines used to slip past.
            if re.search(r"""["']node["']""", line) or NODE_FLAG.search(line):
                window = "\n".join(source.splitlines()[max(0, number - 3):number + 2])
                if re.search(r"""["']node["']""", window) and NODE_FLAG.search(window):
                    offenders.append(f"{path.name}:{number}")
    offenders = sorted(set(offenders))
    assert offenders == [], (
        "these hand node a script as an argument, which dies at 128 KiB — "
        f"use tests/nodeharness.py instead: {offenders}"
    )


def test_the_limit_this_guards_is_smaller_than_the_file_being_sliced():
    """
    Keeps the guard above from going vacuous. If shell.js were ever small
    enough that no harness could reach 128 KiB, the rule would still be
    right but would stop being load-bearing — and a reader deserves to
    know which of the two is true. Today shell.js is ~748 KB, so any
    harness slicing a substantial region of it is over the line.
    """
    assert SHELL_JS.stat().st_size > MAX_ARG_STRLEN, (
        "static/shell.js is now smaller than the per-argument limit; the "
        "guard above is no longer load-bearing and its docstring should say so"
    )
