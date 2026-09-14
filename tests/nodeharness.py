"""
Run a browser-code harness under node, without handing the OS a 748 KB
command-line argument.

48 test files in this repo execute `static/shell.js`'s own functions
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


# ---------------------------------------------------------------------------
# The clock, on the other side of the process boundary
# ---------------------------------------------------------------------------
# freezegun pins Python. node is a SEPARATE PROCESS and never hears about it,
# so on a `pytest --today=...` run the harness files had Python building
# "tomorrow" from the pinned date and shell.js's own `new Date()` answering
# with the real one. Nine tests failed on a one-day pin for exactly that, all
# of them shaped like a genuine bug ("show me tomorrow opened today") and none
# of them being one.
#
# So a pinned run prepends this: `Date` is replaced by a subclass whose
# no-argument constructor and `now()` answer the pinned instant, and which
# defers to the real Date for everything else — `new Date(iso)`, `Date.parse`,
# `Date.UTC`, every getter, `instanceof`. A subclass rather than a hand-built
# stand-in because the real class then keeps answering every question except
# the one word being pinned, which is the same bargain tests/sqlite_clock.py
# makes with SQLite.
#
# ONE INSTANT, not a ticking clock: a harness runs in well under a second, and
# a fixed instant is the more reproducible answer.
#
# Nothing here is a class that can be CALLED without `new` — checked: every
# call site in static/ is `new Date(...)`, and a bare `Date()` would throw.
# If one ever appears, this shim is where it will show up.
_pin_provider = None
_pin_depth = 0


def pin_clock(provider):
    """Point node's `Date` at `provider()` (epoch seconds, as time.time gives
    them). Counted, so a @pytest.mark.today inside a --today run unwinds to the
    session's pin rather than to the real clock."""
    global _pin_provider, _pin_depth
    _pin_depth += 1
    _pin_provider = provider


def unpin_clock():
    global _pin_provider, _pin_depth
    _pin_depth = max(0, _pin_depth - 1)
    if _pin_depth == 0:
        _pin_provider = None


def _clock_prelude() -> str:
    if _pin_provider is None:
        return ""
    at_ms = int(_pin_provider() * 1000)
    return (
        "globalThis.Date = class extends Date {\n"
        "  constructor(...a) { if (a.length === 0) { super(%d); } else { super(...a); } }\n"
        "  static now() { return %d; }\n"
        "};\n"
    ) % (at_ms, at_ms)


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
            # Prepended, not appended: shell.js regions run at top level, so
            # the pin has to be in place before the first line of the harness.
            # Empty on an ordinary unpinned run, so nothing is prepended and
            # the byte-for-byte script every existing test runs is unchanged.
            f.write(_clock_prelude() + script)
        return subprocess.run(
            ["node", path], capture_output=True, text=True, timeout=timeout
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
