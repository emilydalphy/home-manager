"""
The suite means the same thing whatever the environment holds.

`observability_report.py` reads HOME_MANAGER_URL, REPORT_TOKEN and
HOME_MANAGER_PASSPHRASES to decide whether to ask the LIVE app or a local
database file. All three are legitimately set in the overnight environment
so the morning error check can run — and with them set, fourteen tests that
exercise the local-file, half-configured and refused-passphrase paths went
red, because they assumed the variables were absent rather than saying so.

Measured 2026-09-25 on `main`: 14 failed / 6934 passed with the variables
set, all green with `env -u HOME_MANAGER_URL -u REPORT_TOKEN`. Nothing was
wrong with the app; the test was wrong about the world it runs in.

This is the same class as the TZ work in CLAUDE.md (the 2026-09-14
moves-household-clock entry and the `straddle` job): a test whose result
depends on who ran it is not a test. conftest clears the three at module
scope; these two pin that it keeps doing so.
"""
from __future__ import annotations

import os
import subprocess
import sys

REPORT_VARS = ("HOME_MANAGER_URL", "REPORT_TOKEN", "HOME_MANAGER_PASSPHRASES")


def test_the_report_variables_are_cleared_for_every_test():
    """
    The rule, stated directly. Red the moment conftest stops clearing them
    AND the runner has one set — which is the overnight routine's own
    environment, so it is not a hypothetical.
    """
    for var in REPORT_VARS:
        assert var not in os.environ, (
            f"{var} reached a test from the process environment. "
            "conftest clears these so the suite does not change meaning "
            "depending on who ran it; a test that wants one sets it itself "
            "with monkeypatch.setenv."
        )


def test_a_test_that_sets_one_itself_still_gets_it(monkeypatch):
    """
    The other half, and the reason this is a clear rather than a refusal:
    every test that is genuinely about these variables sets them explicitly
    (test_morning_report_token.py, test_health_report.py, test_evening_nudge
    .py all do). Clearing the inherited value must not take that away.
    """
    monkeypatch.setenv("REPORT_TOKEN", "set-by-this-test")
    assert os.environ["REPORT_TOKEN"] == "set-by-this-test"


def test_conftest_clears_them_before_anything_under_app_is_imported():
    """
    Ordering, pinned the way conftest's own docstring explains DB_PATH: the
    pop has to run at MODULE scope, above the imports, not in a fixture. A
    module under app/ that read one of these at import time would otherwise
    already have the inherited value by the time any fixture ran.

    Read off the file rather than asserted at runtime, because by the time a
    test executes there is nothing left to observe.
    """
    src = (
        subprocess.run(
            [sys.executable, "-c", "import pathlib,sys; sys.stdout.write(pathlib.Path(sys.argv[1]).read_text())",
             os.path.join(os.path.dirname(__file__), "conftest.py")],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    pop_at = src.index("os.environ.pop(_report_var")
    first_app_import = min(
        src.index(line) for line in ("import pytest", "from app") if line in src
    )
    assert pop_at < first_app_import, (
        "the report variables must be cleared above the imports, not in a fixture"
    )
