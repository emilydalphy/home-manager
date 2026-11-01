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
  - the thaw card renders to the A4 spec off the moves engine's fridge
    moves, one card for several, and ticks through the same endpoint
    Today's tick used (check_off_prep_step 'done');
  - a thaw on the card is never also a get-ready row.
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
# The thaw card (mockup A4)
# --------------------------------------------------------------------------

TUE = "2026-09-15"
WED = "2026-09-16"


def _fridge(task_id, title="Move the chicken thighs to the fridge", entry_id=7, **extra):
    m = {"id": f"fridge:{task_id}", "kind": "fridge", "title": title, "done": False,
         "tickable": True, "task_id": task_id, "entry_id": entry_id, "related_meal": "Chicken Skewers",
         "reason": "for Wednesday’s skewers", "window_end": f"{TUE}T22:00:00", "too_late_note": ""}
    m.update(extra)
    return m


def _card(moves, meals, today=TUE, hour=9):
    return shelf._node(
        "var due = cookThawsDue(" + json.dumps(moves) + ");\n"
        "var html = cookThawCardHtml(due, " + json.dumps(meals) + ", " + json.dumps(today) + ", " + json.dumps(hour) + ");\n"
        "console.log(JSON.stringify({ n: due.length, html: html }));\n"
    )


@_needs_node
def test_one_thaw_is_one_card_to_the_a4_spec():
    meals = [shelf._meal(7, WED, "Chicken Skewers")]
    out = _card([_fridge(12)], meals)
    html = out["html"]
    assert out["n"] == 1
    assert 'class="cook-thaw"' in html
    assert '<span id="kit-thaw-count">1 thing to do</span>' in html
    assert '<span class="cook-thaw-by">By 10 pm</span>' in html
    assert '<span class="cook-thaw-title">Move the chicken thighs to the fridge</span>' in html
    assert '<span class="cook-thaw-for">For Wednesday’s dinner</span>' in html
    assert 'type="checkbox" class="cook-thaw-input" data-move-id="fridge:12"' in html
    assert "Done, they’re in the fridge" in html
    # The A4 glyph, decorative.
    assert 'viewBox="0 0 32 32" width="38" height="38"' in html and 'aria-hidden="true"' in html
    assert "M11 3v16M4 7l14 8M18 7 4 15M8 4.5l3 2 3-2M8 17.5l3-2 3 2" in html
    assert "M18 22h10M24 18l4 4-4 4" in html


@_needs_node
def test_two_thaws_are_one_card_with_a_box_each():
    meals = [shelf._meal(7, WED, "Chicken Skewers"), shelf._meal(8, TUE, "Beef Chili")]
    out = _card([_fridge(12), _fridge(13, title="Move the beef to the fridge", entry_id=8)], meals)
    html = out["html"]
    assert html.count('class="cook-thaw"') == 1
    assert "2 things to do" in html
    assert html.count('class="cook-thaw-input"') == 2
    assert "For tonight’s dinner" in html
    assert "Done, it’s in the fridge" in html


@_needs_node
def test_nothing_due_means_no_card_and_done_or_too_late_moves_are_left_off():
    out = _card([_fridge(12, done=True), _fridge(13, too_late_note="Too late to thaw safely."),
                 {"id": "prep:4", "kind": "prep", "title": "Marinate", "done": False}], [])
    assert out["n"] == 0 and out["html"] == ""


@_needs_node
def test_the_deadline_says_tonight_once_the_time_has_gone_or_there_is_none():
    assert "Tonight</span>" in _card([_fridge(12)], [], hour=22)["html"]
    assert "Tonight</span>" in _card([_fridge(12, window_end=None)], [], hour=9)["html"]


@_needs_node
def test_a_thaw_on_the_card_is_never_also_a_get_ready_row():
    meals = [shelf._meal(7, WED, "Chicken Skewers")]
    data = {"prep_tasks": [{"id": 12, "task_type": "defrost", "status": "pending", "task_date": TUE,
                            "description": "Move the chicken thighs to the fridge — for Wednesday’s skewers.",
                            "meal_plan_entry_id": 7}],
            "prep_sessions": [], "meals": meals}
    out = shelf._node(
        "var data = " + json.dumps(data) + ";\n"
        "var before = cookGetReadyMoves(data, data.meals, " + json.dumps(TUE) + ", null);\n"
        "var after = cookGetReadyMoves(data, data.meals, " + json.dumps(TUE) + ", null, cookThawCardTaskIds([{ task_id: 12 }]));\n"
        "console.log(JSON.stringify({ before: before.length, after: after.length }));\n"
    )
    assert out == {"before": 1, "after": 0}
    root = shelf._function("renderKitchen")
    assert "cookThawCardHtml(thawsDue" in root
    assert "cookThawCardTaskIds(thawsDue)" in root
    # First on the root, above the shelf.
    assert root.index("cookThawCardHtml(") < root.index("cookShelfHtml(")


def test_the_tick_posts_the_same_done_today_did():
    body = shelf._function("onCookThawChange")
    assert "'/api/today/moves/' + encodeURIComponent(moveId) + '/done'" in body
    assert "body: { done: true }, quiet: true" in body
    assert "panel.addEventListener('change', onCookThawChange);" in SHELL_JS


def test_the_card_is_drawn_in_tokens_only():
    start = SHELL_CSS.index("/* ---------- Cook's thaw card")
    block = SHELL_CSS[start: SHELL_CSS.index("\n/* ----", start + 10)]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "a literal colour in the thaw card's CSS"
    for need in ("border: 2px solid var(--spruce)", "background: var(--surface)", "color: var(--apricot-light)",
                 "border: 1.5px solid var(--hairline-deep)", "min-height: 52px", "letter-spacing: .16em"):
        assert need in block, need


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
