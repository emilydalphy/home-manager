"""
The notification bell refreshes on its own — step 0 of "Reach me before the
moment" (Loop Board, 2026-09-11; the 2026-09-08 overnight finding).

Before this, static/shell.js fetched /api/notifications once per page load
(inside checkOnboarding) and again only after a dismissal. An installed PWA
is left open for days, so its bell was stale until someone reloaded.

These are SOURCE MARKERS, not behaviour tests — shell.js has no JS test
harness in this repo (see tests/test_frontend_restored_2026_09_08.py's
docstring for what a marker is worth). Each one is a tripwire for a piece
of the wiring that must not quietly disappear.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def test_the_bell_refetches_when_the_page_comes_back_into_view():
    assert "document.addEventListener('visibilitychange'" in SHELL_JS
    assert "if (document.visibilityState === 'visible') refreshNotificationsIfDue(false);" in SHELL_JS


def test_the_bell_refetches_when_the_window_regains_focus():
    assert "window.addEventListener('focus', function () { refreshNotificationsIfDue(false); });" in SHELL_JS


def test_the_bell_refetches_on_a_quiet_interval_while_visible():
    assert "var NOTIF_REFRESH_INTERVAL_MS = 5 * 60 * 1000;" in SHELL_JS
    assert "setInterval(function () { refreshNotificationsIfDue(false); }, NOTIF_REFRESH_INTERVAL_MS);" in SHELL_JS
    # A hidden page never fetches — the interval is a courtesy while the
    # app is being looked at, not a heartbeat.
    assert "if (document.visibilityState === 'hidden') return;" in SHELL_JS


def test_two_triggers_on_one_return_cost_one_fetch():
    """focus and visibilitychange both fire when a tab comes back; the gap
    guard collapses them into a single request."""
    assert "var NOTIF_REFRESH_MIN_GAP_MS = 15 * 1000;" in SHELL_JS
    assert "if (!force && now - notifLastRefreshAt < NOTIF_REFRESH_MIN_GAP_MS) return;" in SHELL_JS


def test_the_refresh_is_inert_while_the_bell_is_hidden():
    """SHOW_NOTIF_BELL is false on main (2026-09-08). The gate in
    refreshNotificationsIfDue comes before anything else, and
    loadNotifications keeps its own — so no request is made either way."""
    body = re.search(
        r"function refreshNotificationsIfDue\(force\) \{(.*?)\n  \}", SHELL_JS, re.S
    ).group(1)
    assert body.strip().startswith("if (!SHOW_NOTIF_BELL) return;")
    load = re.search(r"async function loadNotifications\(\) \{(.*?)try \{", SHELL_JS, re.S).group(1)
    assert "if (!SHOW_NOTIF_BELL) return;" in load


def test_the_first_read_goes_through_the_same_gate():
    """checkOnboarding used to call loadNotifications() directly."""
    boot = re.search(r"async function checkOnboarding\(\) \{(.*?)\}\)\(\);", SHELL_JS, re.S).group(1)
    assert "refreshNotificationsIfDue(true);" in boot
    assert "loadNotifications();" not in boot
