"""
Run a browser-code harness under node, without handing the OS a 748 KB
command-line argument.

Nineteen test files in this repo execute `static/shell.js`'s own functions
under node rather than reading the source for markers — the house standard,
because most of the bugs these files were written for are behaviour a
source-marker test cannot see. Each builds a string of prelude + a sliced
region of shell.js and runs it.

They all used to pass that string to node as `-e <script>`, and that
stopped working by GROWTH rather than by an edit: **Linux caps a single
command-line argument at 128 KiB** (MAX_ARG_STRLEN, a kernel constant —
not the far larger ARG_MAX that governs the whole list). shell.js is now
748 KB, so every harness slicing a region past that limit was rejected by
the operating system before node read a character of it:

    OSError: [Errno 7] Argument list too long: 'node'

44 tests were in that state on main when this was found (2026-09-12) —
not failing an assertion, never reaching one. Measured: a 131,000-byte
argument runs, a 140,000-byte one raises.

Worth knowing if this ever looks environment-shaped again: macOS allows a
much larger single argument, so these pass on a Mac and fail on Linux,
which includes CI.

The fix is to put the script in a temp file and point node at the path.
No size limit applies to a path, and nothing about what any test asserts
changes. `tests/test_node_harness_size.py` guards against a new `-e` call
site bringing the ceiling back.
"""
from __future__ import annotations

import os
import subprocess
import tempfile


# Deliberately just the one function. A run_node_json wrapper was written
# first and had no callers: every file keeps its own returncode assertion
# and its own json.loads, because the assertion message names that file's
# harness and is what a failure reads as. Absorbing two lines at the cost
# of a worse failure message is not a trade worth making.
def run_node(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """
    The raw form, for the few callers that read stderr or a non-zero exit
    themselves. The temp file is always removed, including on a timeout —
    node's own failure modes must not leave scripts behind in /tmp.
    """
    # One hazard the `-e` form did not have: node decides CommonJS vs ESM
    # from the nearest package.json, and these harnesses are CommonJS (they
    # `require`). A package.json carrying "type": "module" in the temp
    # directory — or TMPDIR pointed inside a project that has one — would
    # make node parse every harness as ESM and break all of them at once.
    # Not worth guarding against here; worth recognising if that is ever
    # the symptom.
    handle, path = tempfile.mkstemp(suffix=".js", prefix="home-manager-harness-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(script)
        return subprocess.run(
            ["node", path], capture_output=True, text=True, timeout=timeout
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
