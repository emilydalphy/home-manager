"""
"Thaw card on Cook; remove the Today tab (Plan opens first)" — Emily,
2026-11-01: "The only benefit of the today screen at the moment, is having
it tell the user to thaw things. Besides that everything is a repeat of
information that can be found in the other tabs." She moved thawing under
Cook (mockup A4: the old tile "doesn't stand out enough as a step to be
done") and chose Plan as the tab the app opens on.

Pinned here:
  - the bar is Plan · Shop · Kitchen · Cook, no Today key, and '/' (and any
    unknown path, and a caller still naming 'today') lands on Plan;
  - a thaw ticks through the same endpoint Today's tick used
    (check_off_prep_step 'done'). Where it shows on Cook — once, in the
    night-before list — is tests/test_cook_option_a.py's (Option A,
    2026-11-01, replaced the A4 card).
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn
from conftest import household_today
from tests import nodeharness
from tests import test_cook_shelf as shelf

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
MANIFEST = json.loads((REPO / "static" / "manifest.json").read_text(encoding="utf-8"))

_needs_node = shelf._needs_node


def _tabs_block() -> str:
    start = SHELL_JS.index("  var TABS = [")
    return SHELL_JS[start: SHELL_JS.index("  ];", start)]


# --------------------------------------------------------------------------
# No Today tab; Plan opens first
# --------------------------------------------------------------------------

def test_the_bar_is_plan_shop_kitchen_cook_with_no_today():
    block = _tabs_block()
    labels = re.findall(r"label: '([^']+)'", block)
    keys = re.findall(r"key: '([^']+)'", block)
    assert labels == ["Plan", "Shop", "Kitchen", "Cook"]
    assert keys == ["week", "grocery", "home", "kitchen"]
    assert "path: '/'," not in block, "no tab owns '/' any more — it is Plan's address"


@_needs_node
@pytest.mark.parametrize("path,expected", [
    ("/", "week"), ("", "week"), ("/week", "week"), ("/grocery", "grocery"),
    ("/home", "home"), ("/kitchen", "kitchen"), ("/today", "week"), ("/now", "week"),
])
def test_plan_is_where_the_app_opens(path, expected):
    """currentTabKey, run for real against the real TABS block: '/' (the old
    Today path, still the home-screen app's start and the morning text's
    link) and anything unknown is Plan."""
    script = (
        "var ICONS = new Proxy({}, { get: function () { return '<svg></svg>'; } });\n"
        "var window = { location: { pathname: " + json.dumps(path) + " } };\n"
        + _tabs_block() + "  ];\n"
        + shelf._between("  var DEFAULT_TAB = ", "  function escapeHtml(")
        + "console.log(JSON.stringify(currentTabKey()));\n"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip()) == expected


def test_a_caller_still_naming_today_lands_on_plan():
    body = shelf._function("activateTab")
    assert "if (key === 'today' || key === 'now') key = 'week';" in body
    assert "var TODAY_TAB_ALIAS = 'week';" in SHELL_JS
    assert "buildTodayPanel(panel)" not in body, "nothing builds the Today panel any more"
    # The welcome's button, and where it goes.
    assert "Show me today" not in SHELL_JS
    assert "activateTab('today'" not in SHELL_JS


def test_boot_moves_the_root_address_to_plans():
    assert "window.history.replaceState(window.history.state, '', '/week' + window.location.search" in SHELL_JS


def test_the_home_screen_app_starts_on_plan():
    assert MANIFEST["start_url"] == "/week"


def test_root_still_serves_the_shell(signed_in):
    """'/' stays a live route (the iOS wrapper and the morning text open it);
    the shell sends it to Plan."""
    res = signed_in.get("/")
    assert res.status_code == 200
    assert 'id="tab-bar"' in res.text


# --------------------------------------------------------------------------
# The thaw's write
# --------------------------------------------------------------------------
# The A4 thaw card at the top of Cook's root came off with Option A
# (Emily, 2026-11-01, the Cook declutter canvas): her complaint was the thaw
# showing twice, so a fridge move now lives only in the night-before list.
# Its rendering is pinned in tests/test_cook_option_a.py; the write behind
# its box is the same one, pinned here end to end.


def test_ticking_a_due_thaw_on_the_server_marks_the_prep_row_done(signed_in):
    """The write behind the box, end to end: the moves engine lists the
    day's fridge move (window to 22:00), the tick is check_off_prep_step's
    'done', and the move comes back done."""
    tools.add_member("Alex")
    today = household_today()
    plan_id = tools.create_weekly_plan((today - datetime.timedelta(days=2)).isoformat())["weekly_plan_id"]
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, status, task_type) "
        "VALUES (?, ?, ?, ?, ?, 'pending', 'defrost')",
        (tools.household_id(), plan_id, today.isoformat(),
         "Move the chicken thighs to the fridge — for Thursday’s skewers.", "Chicken Skewers"),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()

    moves = signed_in.get("/api/today/moves").json()["moves"]
    fridge = [m for m in moves if m["id"] == f"fridge:{task_id}"][0]
    assert fridge["kind"] == "fridge" and not fridge["done"]
    assert fridge["window_end"].endswith("T22:00:00")

    res = signed_in.post(f"/api/today/moves/fridge:{task_id}/done", json={"done": True})
    assert res.status_code == 200
    after = [m for m in res.json()["moves"] if m["id"] == f"fridge:{task_id}"][0]
    assert after["done"] is True
    conn = get_conn()
    status = conn.execute("SELECT status FROM prep_tasks WHERE id = ?", (task_id,)).fetchone()["status"]
    conn.close()
    assert status == "done"
