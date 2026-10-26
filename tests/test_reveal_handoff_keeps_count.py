"""
The Week 1 reveal's Approve lands on the list saying how many things to buy
(defect hunt, 2026-10-24).

onboarding.html approves the first week and hands off to
/grocery?after=approve&drafted=<week>&added=<groceries_added>. The shell
opens Plan for that (activateTab), and Plan's buildWeekPanel scrubs the
URL to /week with replaceState and THEN showed the toast — reading `added`
off a URL it had just emptied. Every first week was told "Week approved."
with the count gone, while Plan's own Approve says "Week approved · 19
things to buy." Measured in Chromium against a real server on a throwaway
DB, before and after.

A source-order pin: the count is read before the scrub, and the toast
reads that.
"""
import re
from pathlib import Path

SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")


def _build_week_panel() -> str:
    start = SHELL_JS.index("var afterApprove = window.location.search.indexOf('after=approve') !== -1;\n    // The reveal's count")
    end = SHELL_JS.index("if (!afterApprove) maybeStartFirstTour();", start)
    return SHELL_JS[start:end]


def test_the_count_is_read_before_the_url_is_scrubbed():
    body = _build_week_panel()
    read = body.index("var approvedAdded = new URLSearchParams(window.location.search).get('added');")
    scrub = body.index("window.history.replaceState({ tab: 'week' }, '', afterApprove ? '/week' : window.location.pathname);")
    toast = body.index("showToast(weekApprovedLine(approvedAdded));")
    assert read < scrub < toast


def test_nothing_after_the_scrub_reads_the_url_for_the_count():
    body = _build_week_panel()
    after_scrub = body[body.index("window.history.replaceState({ tab: 'week' }"):]
    assert not re.search(r"get\('added'\)", after_scrub), (
        "the URL is /week by then; `added` is always null there"
    )
