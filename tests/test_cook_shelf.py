"""
Cook's root was "Cook · D · The shelf" (Emily, 2026-09-13): a week shelf,
one spruce Tonight card, get-ready rows, and a dock. On 2026-11-01 she
replaced the shelf, the card and the rows with Option A — one even list of
today's meals (tests/test_cook_option_a.py pins that). What still stands
from the shelf design, and is pinned here:

  - the root's dock (DESIGN_SYSTEM §6 — Cook's root has one primary
    action) and the way it opens a meal on its recipe with the "‹ Cook"
    crumb;
  - Recipes / Add from a link / Add from a cookbook behind one "More ···"
    link, in a sheet (`#cook-more-sheet`);
  - the keys get_cooker_view grew for it (period_start_date, day_count,
    cook_name), and the stale-plan rules at the bottom.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

from app import tools
from tests import nodeharness
from conftest import household_today

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")
# The Decision log moved out of CLAUDE.md on 2026-10-05.
DECISION_LOG = (REPO / "docs" / "DECISION_LOG.md").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _function(name: str, source: str = SHELL_JS) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start : j + 1]


def _between(start_marker: str, end_marker: str) -> str:
    a = SHELL_JS.index(start_marker)
    return SHELL_JS[a : SHELL_JS.index(end_marker, a)]


_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "const GRO_ICONS = { chevRight: '<svg data-icon=\"chev\"></svg>' };\n"
    "const COOK_ICONS = { check: '<svg data-icon=\"check\"></svg>' };\n"
    "const REHEAT_ACTION_LABEL = 'Mark eaten';\n"
    "const REHEAT_UNDO_LABEL = 'Mark not eaten';\n"
    "var cookState = { tonightIdx: null };\n"
    # Real date helpers: the shelf counts nights and names days.
    "function dayName(dateStr, opts){ return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', opts); }\n"
    "function dayNameShort(iso){ return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' }); }\n"
    + _function("addDaysLocal") + "\n"
    + _function("daysBetweenLocal") + "\n"  # kitchenNextCookLine counts the days out (2026-09-15)
    + _function("cookMinutesLabel") + "\n"
    + _function("cookCoversLabel") + "\n"
    + _function("cookFocusPrepTasks") + "\n"
    # cookPreppedAhead — kitchenTodayRows asks it whether a
    # meal was made on an earlier prep day (2026-09-26, prepped lunches on the
    # prep day). One rule, one place; a harness without it gets a named
    # ReferenceError rather than a quiet wrong answer.
    + _function("cookPreppedAhead") + "\n"
    # MOVE_OWNER_WORDS / moveOwnerClause: pulled in whole for the files that
    # import this harness and still read whose move it is.
    + _between("  var MOVE_OWNER_WORDS = {", "  // The two groups' own icons")
    + _between("  function kitchenTodayRows(", "  function cookBandEyebrow(")
    + _function("kitchenNextCookLine") + "\n" + _function("kitchenNextCook") + "\n" + _function("kitchenNextWhen") + "\n"
    + _function("cookSessionOn") + "\n"
    + _function("tileHtml") + "\n"
    + _function("headsUpRowHtml") + "\n" + _function("headsUpHtml") + "\n"
    + _function("recipeTopHtml") + "\n"
    + _function("roundBtnHtml") + "\n"
    + "var RECIPE_ICONS = { chevLeft: '' };\nfunction cookDockHtml(h){ return '<div class=\"dock cook-dock\">' + h + '</div>'; }\n"
    # The whole root block: the even list through the More link.
    + _between("  // ---------- Cook's root: today, in the order it is eaten", "  function renderKitchen()")
)


def _node(script: str):
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


MON = "2026-09-14"  # a Monday
TUE, WED, THU = "2026-09-15", "2026-09-16", "2026-09-17"


_MOVE = {"kind": "cook", "entry_id": 1, "chips": ["30 min", "Start by 6:00"],
         "time_label": "6:30 tonight", "detail": "dinner · 30 min · 6:30"}


def _tonight(meals, moves, data, today, tonight_idx=None):
    """Cook's root under the band for these meals and moves — cookRootParts,
    {html, dock}. Kept under its old name (the Tonight card it used to draw
    went with Option A, 2026-11-01) for the files that import it;
    `tonight_idx` is ignored: the root's "next" is the first meal not done."""
    full = {"meals": meals, "prep_sessions": [], "prep_tasks": []}
    full.update(data or {})
    return _node(
        "var parts = cookRootParts(" + json.dumps(full) + ", " + json.dumps(moves) + ", "
        + json.dumps(today) + ", null);\n"
        "console.log(JSON.stringify(parts));\n"
    )


def _meal(entry_id, date, meal, slot="dinner", **extra):
    m = {"entry_id": entry_id, "date": date, "slot": slot, "meal": meal,
         "cooked_status": "pending", "is_leftovers": False,
         "prep_time_minutes": 10, "cook_time_minutes": 20, "advance_prep_notes": ""}
    m.update(extra)
    return m


# --------------------------------------------------------------------------
# The dock, the More sheet, the wiring
# --------------------------------------------------------------------------

def test_start_cooking_enters_cook_focus_for_tonight_from_the_root():
    handler = _function("onCookClick")
    assert "if (what === 'start-tonight') {" in handler
    block = handler.split("if (what === 'start-tonight') {", 1)[1][:400]
    assert "cookState.focusOrigin = null;" in block, "opened from Cook, so the crumb says Cook"
    assert "cookEnterFocus(parseInt(el.getAttribute('data-idx'), 10));" in block
    assert "return (origin && origin.label) ? origin.label : 'Cook';" in _function("cookBackLabel")
    # The dock element is last in the root view and pinned to its foot.
    build = _function("buildKitchenPanel")
    assert '<div class="dock cook-root-dock" id="kit-dock" hidden></div>' in build
    assert build.index('id="kit-body"') < build.index('id="kit-dock"')
    assert "#kit-root-view .cook-root-dock { margin-top: auto; }" in SHELL_CSS
    assert ".kitchen-content.has-dock { padding-bottom: 0; }" in SHELL_CSS
    root = _function("renderKitchen")
    assert "setDock(parts.dock);" in root
    assert "content.classList.toggle('has-dock', !!html);" in root


def test_recipes_add_from_a_link_and_inventory_are_behind_one_more_link():
    root = _function("renderKitchen")
    # The round ··· in the head, so the root, the no-plan state and the error
    # state all carry it (2026-11-05).
    build = _function("buildKitchenPanel")
    assert 'data-kit="more"' in build and "label: 'More'" in build
    assert "cookMoreLinkHtml" not in SHELL_JS
    rows = _function("cookMoreRowsHtml")
    # Three rows since 2026-10-26: Inventory left for its own tab (Kitchen,
    # TABS key 'home' — Loop Board C1; tests/test_kitchen_tab.py).
    assert rows.count("tileHtml(") == 3  # + "Add from a cookbook" (recipe photo import, 2026-09-13)
    for needle in ('data-kit="recipes"', "name: 'Ask about our recipes'", 'data-kit="recipe-link"',
                   "name: 'Add from a link'",
                   'data-kit="recipe-photo"', "name: 'Add from a cookbook'"):
        assert needle in rows, needle
    assert 'data-sheet="inventory"' not in rows
    # "Add from a link" says "In development" while
    # RECIPE_LINK_IN_DEVELOPMENT is on; Inventory's row is gone.
    assert "sub: RECIPE_LINK_IN_DEVELOPMENT ? 'In development' : ''" in rows
    assert "dock-primary" not in rows and "btn-primary" not in rows
    assert 'id="cook-more-sheet"' in SHELL_HTML and 'id="cook-more-scrim"' in SHELL_HTML
    assert 'id="cook-more-rows"' in SHELL_HTML
    assert "openSheet(cookMoreSheet, cookMoreScrim);" in _function("openCookMoreSheet")
    assert "if (what === 'more') openCookMoreSheet();" in _function("onKitchenClick")
    # Same geometry and the same three motion rules as Meals' More.
    assert "#meals-more-sheet, #cook-more-sheet {" in SHELL_CSS
    assert "#cook-more-sheet.is-open" in SHELL_CSS and "#cook-more-scrim.is-open" in SHELL_CSS
    # The old rows never render on the root.
    assert "function kitchenTilesHtml(" not in SHELL_JS
    assert "kit-rows" not in root


def test_the_old_lists_are_gone():
    for gone in ("function cookRestOfWeekHtml(", "function cookRestDayRowHtml(", "function cookPrepSessionsHtml(",
                 "function kitchenPrepTodoHtml(", "KITCHEN_REST_VISIBLE", 'data-cook="rest-more"',
                 "'#kit-prep-todo'", "'#kit-prep-sessions'"):
        assert gone not in SHELL_JS, f"{gone} is back"
    for dead in (".cook-day-row", ".cook-day-dishes", ".cook-day-dish", ".cook-day-min", ".cook-week-name.is-quiet"):
        assert not re.search(r"(^|[\s,}])%s\s*[,{:\[]" % re.escape(dead), SHELL_CSS, re.M), (
            f"{dead} still has a CSS rule — the shelf replaced that list"
        )
    assert "var prepEl = rootView.querySelector('#kit-get-ready');" in _function("renderCook")


# --------------------------------------------------------------------------
# The doc moved with the code (Tier 2: the dock on Cook's root)
# --------------------------------------------------------------------------

def test_design_system_and_the_decision_log_say_what_cooks_root_dock_holds():
    assert "Cook's root has no dock on a normal day" in DESIGN
    assert "\"Start cooking\" for dinner" in DESIGN
    # Nav rule 2 no longer names Cook's root as the screen with no dock.
    rule2 = DESIGN.split("- **One dock**", 1)[1][:1200]
    assert "Cook's root is the live example" not in rule2
    # The empty-moment row: still no dock on an empty night.
    assert "no dock on a night with nothing to cook" in DESIGN
    assert "2026-09-13 — Cook's root is the shelf" in DECISION_LOG


# --------------------------------------------------------------------------
# The backend half: three keys the shelf and the card read
# --------------------------------------------------------------------------

def _week_start() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday())).isoformat()


def test_the_cooker_view_carries_the_period_and_the_cook():
    plan_id = tools.create_weekly_plan(_week_start())["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(household_today().isoformat(), "Chili", slot="dinner", weekly_plan_id=plan_id)
    view = tools.get_cooker_view()
    assert view["period_start_date"] == _week_start()
    assert view["day_count"] == 7
    assert view["cook_name"] is None, "nobody named, nobody guessed"
    tools.set_cooking_role("one_person", who="Emily")
    assert tools.get_cooker_view()["cook_name"] == "Emily"
    tools.set_cooking_role("turns")
    assert tools.get_cooker_view()["cook_name"] is None


def test_no_plan_at_all_still_answers_with_the_keys():
    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None
    assert view["period_start_date"] is None and view["day_count"] == 0
    assert view["cook_name"] is None
    assert view["last_planned_label"] is None


# --------------------------------------------------------------------------
# Bug (Loop Board, 2026-09-13): a household whose last approved week was
# weeks ago saw THAT week under a heading that says "this week", because
# get_weekly_plan (via _current_weekly_plan_row) falls back to the newest
# non-retired plan on file when none covers today — right for a chat
# answer, wrong for "what am I cooking right now". get_cooker_view now
# refuses that fallback for itself when no weekly_plan_id was asked for.
# --------------------------------------------------------------------------

def _month_ago_monday() -> str:
    today = household_today()
    start = today - datetime.timedelta(days=today.weekday() + 28)
    return start.isoformat()


def test_a_stale_last_weeks_plan_is_not_offered_as_this_week():
    stale_start = _month_ago_monday()
    plan_id = tools.create_weekly_plan(stale_start)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(stale_start, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None, "the stale plan must not be handed back as current"
    assert view["is_current_plan"] is False
    assert view["meals"] == [], "August's dinner must not appear under a 'this week' view"
    assert view["period_start_date"] is None and view["day_count"] == 0
    from app.tools import weekly_plan as _wp
    expected_label = _wp._format_period_range(stale_start, 7)
    assert view["last_planned_label"] == expected_label, "the last plan's dates, for an honest line"

    # Asking for that plan BY ID still gets it, stale or not.
    direct = tools.get_cooker_view(plan_id)
    assert direct["weekly_plan_id"] == plan_id
    assert len(direct["meals"]) == 1 and direct["meals"][0]["meal"] == "Chili"
    assert direct["last_planned_label"] is None, "only the no-id fallback sets this"


def test_a_stale_plan_does_not_hide_a_real_loose_meal_ahead():
    """A one-off chat-planned dinner for today has nothing to do with last
    month's plan and must still show, even though the stale plan gets
    dropped from the view (unplanned_meals_ahead, weekly_plan.py)."""
    stale_start = _month_ago_monday()
    plan_id = tools.create_weekly_plan(stale_start)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(stale_start, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    tools.add_recipe("Tacos", ingredients=[{"item": "tortillas", "qty": "1 pack"}])
    today = household_today().isoformat()
    tools.plan_meal(today, "Tacos", slot="dinner")  # no weekly_plan_id: a loose meal

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None, "still no CURRENT plan, just a loose meal on top"
    names = [m["meal"] for m in view["meals"]]
    assert names == ["Tacos"], "the loose meal shows; the stale plan's Chili does not"
    from app.tools import weekly_plan as _wp
    assert view["last_planned_label"] == _wp._format_period_range(stale_start, 7)


def test_today_moves_do_not_read_a_stale_plan_either():
    """The Now tab's today_moves is built off the same get_cooker_view()
    call with no id — same bug, same fix, checked from that side too."""
    from app.tools import moves as _moves

    stale_start = _month_ago_monday()
    plan_id = tools.create_weekly_plan(stale_start)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(stale_start, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    payload = _moves.today_moves()
    assert payload["week_state"] == "none", "the badge must not read 'set' off a month-old plan"
    kinds = [m["kind"] for m in payload["moves"]]
    assert "cook" not in kinds, "no cook move should be built off August's dinner"


def test_a_plan_ending_exactly_today_is_not_stale():
    """Boundary case: period_end_date == today must NOT trip the stale
    check (`period_end < today`, strictly less-than) — a plan whose last
    day is today is still this week's plan."""
    start = (household_today() - datetime.timedelta(days=6)).isoformat()  # a 7-day plan starting 6 days ago ends today
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(start, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] == plan_id
    assert view["last_planned_label"] is None
    assert [m["meal"] for m in view["meals"]] == ["Chili"]


def test_a_plan_ending_exactly_yesterday_is_stale():
    """Boundary case: period_end_date == yesterday must trip the stale
    check — one day past the end is enough."""
    start = (household_today() - datetime.timedelta(days=7)).isoformat()  # a 7-day plan starting 7 days ago ended yesterday
    plan_id = tools.create_weekly_plan(start)["weekly_plan_id"]
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.plan_meal(start, "Chili", slot="dinner", weekly_plan_id=plan_id)
    tools.approve_weekly_plan(plan_id)

    view = tools.get_cooker_view()
    assert view["weekly_plan_id"] is None
    assert view["meals"] == []
    from app.tools import weekly_plan as _wp
    assert view["last_planned_label"] == _wp._format_period_range(start, 7)
