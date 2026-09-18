"""
"Need a hand?" — the sheet behind the round `?` (Loop Board "Help icon:
'Need a hand?' sheet and 'Something not working?'", Emily 2026-09-18,
mockups 09b-help and 09c-not-working).

One file, static/help-sheet.js, loaded by both the Week 1 screen and the
app shell, so the two `?`s open the same thing. Its builders are pure
(markup in, markup out), so they are run under node rather than grepped.

The "Something not working?" half adds one thing to the report, on both
this sheet and the shell's own: which screen the person was on, said on
the form and sent as `screen`. That field is shape-checked and stored, and
printed by observability_report.py --feedback — tested here end to end.
"""
from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.db import _run_migrations, get_conn


STATIC = Path(__file__).resolve().parent.parent / "static"
HELP_JS = STATIC / "help-sheet.js"
SHELL_JS = (STATIC / "shell.js").read_text()
SHELL_HTML = (STATIC / "shell.html").read_text()
ONBOARDING = (STATIC / "onboarding.html").read_text()

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the sheet's own builders")


def _api(expr: str):
    res = nodeharness.run_node(
        f"const h = require({str(HELP_JS)!r}); console.log(JSON.stringify({expr}));", timeout=30
    )
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout)


# ---------- the sheet ----------

@_needs_node
def test_the_sheet_says_its_four_rows_verbatim_and_offers_two_ways_out():
    html = _api("h.helpHtml()")
    assert '<h2 class="help-title" id="help-title">Need a hand?</h2>' in html
    rows = _api("h.HELP_ROWS")
    assert [(r["title"], r["line"]) for r in rows] == [
        ("Swap a meal", "Tap Swap under any meal and I’ll show you three other picks."),
        ("Or just tell me", "“Jamie’s out Thursday.” “Less chicken.” “We already have rice.” I’ll change that one thing, not the whole week."),
        ("Nothing’s locked in", "Approve now, change any day later from the Plan tab."),
        ("If I get it wrong, say so", "Correct me right where it happens and I’ll remember for next week."),
    ]
    assert html.count('class="help-row-icon"') == 4
    assert html.count('stroke-width="2.2"') == 4, "every row's icon is a stroke icon at the one width"
    assert '<button type="button" class="help-quiet" data-help="snw">Something not working? Tell me</button>' in html
    assert '<button type="button" class="help-outline" data-help="close">Got it</button>' in html


@_needs_node
def test_the_not_working_form_is_the_shells_own_plus_the_screen_line():
    html = _api("h.snwFormHtml('Week 1')")
    assert 'placeholder="Even half a sentence helps"' in html
    assert 'id="help-snw-what"' in html and 'id="help-snw-trying"' in html
    assert "What were you trying to do?" in html and ">Optional<" in html
    assert "I’ll include which screen you were on: Week 1." in html
    assert 'class="help-primary" id="help-snw-send" data-help="send" disabled>Send<' in html
    assert 'class="help-quiet" data-help="close">Never mind<' in html
    assert _api("h.SNW_SENT") == "Sent — thanks, Emily reads every one."
    # A screen with no name says nothing about one rather than "on: ."
    assert "which screen" not in _api("h.snwFormHtml('')")


@_needs_node
def test_the_screen_name_is_escaped_into_the_form():
    html = _api("h.snwFormHtml('<b>x</b>')")
    assert "<b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_both_pages_load_the_one_file_and_the_shell_does_not_wire_it_itself():
    assert '<script src="/static/help-sheet.js"></script>' in SHELL_HTML
    assert SHELL_HTML.index("help-sheet.js") < SHELL_HTML.index('src="/static/shell.js"')
    assert '<script src="/static/help-sheet.js"></script>' in ONBOARDING
    # The Plan screen's `?` (openWeekHelp, the "Check the week" card) calls
    # the sheet by name, and nothing in shell.js builds or wires one of its own.
    assert "openHelpSheet({ screenName: screenName });" in SHELL_JS
    assert "function openHelpSheet" not in SHELL_JS and "help-sheet-scrim" not in SHELL_JS
    src = HELP_JS.read_text()
    assert "global.openHelpSheet = openHelpSheet;" in src and "global.closeHelpSheet = closeHelpSheet;" in src
    assert "if (e.key === 'Escape'" in src and "scrimEl.addEventListener('click', closeHelpSheet)" in src
    assert "#" not in src.split("*/", 1)[1].replace("#help-", "").replace("#39;", ""), "a literal colour in the sheet's stylesheet"


# ---------- the shell's own form gained the same line ----------

def test_the_shells_form_names_the_screen_and_sends_it():
    form = SHELL_JS[SHELL_JS.index("function snwFormHtml(screenName)"):]
    form = form[: form.index("function openSnwSheet(")]
    assert "I’ll include which screen you were on: ' + escapeHtml(screenName) + '.</span>" in form
    send = SHELL_JS[SHELL_JS.index("function sendSnwReport(whatHappened, tryingToDo, screen)"):]
    send = send[: send.index("// Delegated, so the tile")]
    assert "screen: String(screen || '')," in send
    assert "function snwScreenName(screenName)" in SHELL_JS


# ---------- `screen`, end to end ----------

def _rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT what_happened, screen FROM feedback_reports ORDER BY id")]
    finally:
        conn.close()


def test_the_migration_adds_the_column_once():
    conn = get_conn()
    _run_migrations(conn)
    _run_migrations(conn)
    conn.commit()
    names = [row["name"] for row in conn.execute("PRAGMA table_info(feedback_reports)")]
    conn.close()
    assert names.count("screen") == 1


def test_the_screen_is_stored_with_the_report_and_read_back(signed_in):
    res = signed_in.post("/api/feedback", json={"what_happened": "The swap did nothing.", "screen": "Week 1"})
    assert res.status_code == 204
    assert _rows() == [{"what_happened": "The swap did nothing.", "screen": "Week 1"}]
    assert tools.get_feedback_reports()[0]["screen"] == "Week 1"
    assert signed_in.get("/api/feedback").json()["reports"][0]["screen"] == "Week 1"


def test_a_screen_name_that_is_not_a_screen_name_is_dropped(signed_in):
    """A screen's name is chosen by the app, never typed — so a sentence,
    a URL or anything over a title's length is not stored, the same rule
    the route pattern follows."""
    for bad in ("https://x.example/?token=abc", "Week 1 <script>", "x" * 61, "I typed this"):
        signed_in.post("/api/feedback", json={"what_happened": "hm", "screen": bad})
    assert {r["screen"] for r in _rows()} == {"", "I typed this"}
    signed_in.post("/api/feedback", json={"what_happened": "hm", "screen": "Plan › Check the week"})
    assert _rows()[-1]["screen"] == "Plan › Check the week"


def test_a_report_with_no_screen_still_files(signed_in):
    assert signed_in.post("/api/feedback", json={"what_happened": "Older client."}).status_code == 204
    assert _rows() == [{"what_happened": "Older client.", "screen": ""}]


@pytest.fixture
def local_db_only(monkeypatch):
    """Force the script onto the test database rather than a live app
    (the same fixture tests/test_feedback_reports.py carries)."""
    for var in ("HOME_MANAGER_URL", "HOME_MANAGER_PASSPHRASES", "HOME_MANAGER_PASSWORD", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def test_the_feedback_report_prints_the_screen(monkeypatch, local_db_only):
    import sys
    import observability_report as rep

    tools.record_feedback_report("The swap did nothing.", screen="Week 1")
    monkeypatch.setattr(sys, "argv", ["observability_report.py", "--feedback"])
    out = io.StringIO()
    with redirect_stdout(out):
        rep.main()
    text = out.getvalue()
    assert "screen: Week 1" in text
    assert "| The swap did nothing." in text
