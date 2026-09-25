"""
The Plan tab's More sheet, mockup "10C" (Emily, approved 2026-09-25): every
row is an icon, a title, and one line saying exactly what happens or where
it goes — replacing the sheet's mix of bare labels and optional sub-lines —
grouped under two eyebrows: the week on screen's own rare moves ("This
draft" / "This week"), then "Everything else" for the ones that aren't
about one particular week.

Three behaviour changes ride with the redesign, each checked below:
  1. "Drop this draft" is renamed "Keep my approved week" and, since an
     approved week is the whole reason the row is an easy yes, it is now
     hidden outright rather than carrying a different line when there is
     no approved week under the draft (data.replaces).
  2. "Adjust your setup" opens Preferences (the gear's sheet, openPrefsSheet)
     — never /meal-setup and never chat. Concurrent work on a different
     branch owns the Preferences sheet's own internals and /meal-setup
     itself; this only ever calls the existing opener.
  3. "Start over" gains a third, independent option — "This week's
     answers" — that clears only the current week's planning-question
     intake (app/tools/week_intake.py), never household setup/onboarding
     (app/tools/reset.py, POST /api/reset, GET /api/reset/preview).

The JS half runs shell.js's own functions under node (tests/nodeharness.py,
the house pattern — see its own docstring for why source-marker tests miss
real bugs here). The server half is ordinary pytest against the tools layer
and the routes.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract, _extract_var

from app import tools
from app.db import get_conn
from app.tools import reset as _reset
from app.tools import week_intake as _week_intake

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the sheet's own renderer"
)


# ---------------------------------------------------------------------------
# JS: the rendered sheet itself
# ---------------------------------------------------------------------------

_HARNESS_TAIL = """
var CALLS = [];
function stub(name) { return function () { CALLS.push([name].concat(Array.prototype.slice.call(arguments))); }; }
var panels = { week: { id: 'week-panel' } };
function closeMealsMoreSheet() { CALLS.push(['closeMealsMoreSheet']); }
var tryAgain = stub('tryAgain');
var startPlanningWeek = stub('startPlanningWeek');
var discardDraft = stub('discardDraft');
var reopenWeek = stub('reopenWeek');
var goMealsStep = stub('goMealsStep');
var openWeekSheet = stub('openWeekSheet');
var openPrefsSheet = stub('openPrefsSheet');
var openMealSetup = stub('openMealSetup');
var openResetDialog = stub('openResetDialog');
var openWeekHelp = stub('openWeekHelp');

function makeRows() {
  var rowsEl = { innerHTML: '', buttons: {} };
  rowsEl.querySelector = function (sel) {
    var id = sel.slice(1);
    if (rowsEl.innerHTML.indexOf('id="' + id + '"') === -1) return null;
    if (!rowsEl.buttons[id]) {
      rowsEl.buttons[id] = { addEventListener: function (t, fn) { rowsEl.buttons[id].handler = fn; } };
    }
    return rowsEl.buttons[id];
  };
  return rowsEl;
}
var ROWS = makeRows();
var document = { getElementById: function (id) { return id === 'meals-more-rows' ? ROWS : null; } };

function tap(id) {
  var btn = ROWS.querySelector('#' + id);
  if (!btn) throw new Error('no row ' + id);
  btn.handler();
}
"""


def _harness() -> str:
    return (
        _extract_var("WK_ICONS", SHELL_JS) + "\n"
        + _extract("escapeHtml", SHELL_JS) + "\n"
        + _extract("mealsMoreRowHtml", SHELL_JS) + "\n"
        + _extract("mealsMoreGroupHtml", SHELL_JS) + "\n"
        + _extract("renderMealsMoreSheet", SHELL_JS) + "\n"
        + _HARNESS_TAIL
    )


def _run(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_a_draft_over_an_approved_week_shows_keep_my_approved_week():
    data = {"weekly_plan_id": 9, "status": "draft", "replaces": {"weekly_plan_id": 3}}
    out = _run(_harness() + f"""
var weekState = {{ data: {json.dumps(data)} }};
renderMealsMoreSheet();
tap('wk-more-discard');
console.log(JSON.stringify({{ html: ROWS.innerHTML, calls: CALLS }}));
""")
    assert 'id="wk-more-discard"' in out["html"]
    assert ">Keep my approved week<" in out["html"]
    assert "Throws this draft away. Your approved week and grocery list stay as they are." in out["html"]
    assert ">This draft<" in out["html"]
    assert out["calls"] == [["closeMealsMoreSheet"], ["discardDraft", {"id": "week-panel"}, data]]


@_needs_node
def test_a_bare_draft_hides_the_row_outright():
    """FAILS ON MAIN: a bare draft (no approved week under it) used to keep
    the row with a different line ("Nothing's on your list from it"); the
    10C design drops the row entirely instead — there is nothing to "keep"
    when nothing is approved."""
    data = {"weekly_plan_id": 9, "status": "draft"}
    out = _run(_harness() + f"""
var weekState = {{ data: {json.dumps(data)} }};
renderMealsMoreSheet();
console.log(JSON.stringify(ROWS.innerHTML));
""")
    assert 'id="wk-more-discard"' not in out
    assert "Keep my approved week" not in out
    assert "Nothing's on your list from it" not in out
    # Try again and Change my answers are still there, under "This draft".
    assert ">This draft<" in out
    assert 'id="wk-more-try-again"' in out and 'id="wk-more-change"' in out


@_needs_node
def test_an_approved_week_shows_reopen_and_check_under_this_week():
    data = {"weekly_plan_id": 9, "status": "approved"}
    out = _run(_harness() + f"""
var weekState = {{ data: {json.dumps(data)} }};
renderMealsMoreSheet();
tap('wk-more-reopen');
console.log(JSON.stringify({{ html: ROWS.innerHTML, calls: CALLS }}));
""")
    assert ">This week<" in out["html"]
    assert 'id="wk-more-reopen"' in out["html"] and 'id="wk-more-check"' in out["html"]
    # Never both eyebrows' week-scoped rows at once.
    assert "wk-more-try-again" not in out["html"] and "wk-more-discard" not in out["html"]
    assert out["calls"] == [["closeMealsMoreSheet"], ["reopenWeek", {"id": "week-panel"}, data]]


@_needs_node
def test_setup_opens_preferences_never_meal_setup():
    """FAILS ON MAIN: 'Adjust your setup' opened /meal-setup
    (window.location.href) — Emily's call for 10C is that it has to reach
    the household settings screen, the Preferences sheet behind the gear,
    without leaving the page."""
    out = _run(_harness() + """
var weekState = { data: {} };
renderMealsMoreSheet();
tap('wk-more-setup');
console.log(JSON.stringify(CALLS));
""")
    assert out == [["closeMealsMoreSheet"], ["openPrefsSheet"]]


@_needs_node
def test_everything_else_is_one_group_in_order_with_its_lines():
    out = _run(_harness() + """
var weekState = { data: {} };
renderMealsMoreSheet();
console.log(JSON.stringify(ROWS.innerHTML));
""")
    # No plan at all: neither week-scoped group has anything to show.
    assert ">This draft<" not in out and ">This week<" not in out
    assert ">Everything else<" in out
    order = ["wk-more-whole-week", "wk-more-setup", "wk-more-reset", "wk-more-help"]
    positions = [out.index('id="' + i + '"') for i in order]
    assert positions == sorted(positions), "See the whole week, Adjust your setup, Start over, Need a hand?"
    assert "Every meal on one page, with a link you can send." in out
    assert "Opens your household settings: meal counts, cooking time, kitchen." in out
    assert "Clears the meal plan, the grocery list or this week’s answers. You pick, and confirm first." in out
    assert "Tips for changing the week, and a way to send Emily a note." in out


@_needs_node
def test_every_row_carries_an_icon_and_a_chevron():
    data = {"weekly_plan_id": 9, "status": "draft", "replaces": {"weekly_plan_id": 3}}
    out = _run(_harness() + f"""
var weekState = {{ data: {json.dumps(data)} }};
renderMealsMoreSheet();
console.log(JSON.stringify(ROWS.innerHTML));
""")
    # This draft: try again, change my answers, keep my approved week (3).
    # Everything else: whole week, setup, start over, need a hand (4).
    assert out.count('class="wk-more-icon"') == 7
    assert out.count('class="wk-more-chev"') == 7
    # Every icon inside a --celadon-label tile is a stroke-2.2 SVG (DESIGN_SYSTEM §icons).
    assert 'stroke-width="2.2"' in out


def test_the_icons_are_all_stroke_2point2_never_a_literal_hex():
    """DESIGN_SYSTEM.md: icons are inline stroke SVG at one weight (2.2),
    and every colour goes through a token — never a literal hex."""
    import re
    icons_block = _extract_var("WK_ICONS", SHELL_JS)
    new_icons = ["redo", "pencil", "bin", "unlock", "checklist", "calendar", "resetArrow"]
    for name in new_icons:
        assert (name + ": '<svg") in icons_block, name
    for svg in re.findall(r"<svg[^>]*>.*?</svg>", icons_block):
        if "fill=\"currentColor\" stroke=\"none\"" in svg:
            continue  # the filled dot/mark exceptions predate this change
        assert 'stroke-width="2.2"' in svg, svg
        assert re.search(r"#[0-9a-fA-F]{3,6}\b", svg) is None, svg


def test_the_row_and_group_css_use_tokens_and_meet_the_44px_rule():
    block = SHELL_CSS[SHELL_CSS.index(".wk-more-rows {"):SHELL_CSS.index(".wk-more-rows .week-period-picker")]
    assert "min-height: 56px" in block  # comfortably over the 44px floor
    assert "var(--celadon-tint)" in block and "var(--celadon-label)" in block
    import re
    assert re.search(r"#[0-9a-fA-F]{3,6}\b", block) is None, "every colour goes through a token"


# ---------------------------------------------------------------------------
# Server: "This week's answers" — the third Start over option
# ---------------------------------------------------------------------------

WEEK_START = "2026-09-21"


def _seed_intake_and_plan():
    tools.add_member("Emily")
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.save_week_intake(WEEK_START, night_tags={WEEK_START: ["normal"]}, created_by="Emily")
    return plan_id


def test_clear_week_intake_supersedes_the_current_revision_with_no_replacement():
    _seed_intake_and_plan()
    assert tools.get_week_intake(WEEK_START) is not None

    result = tools.clear_week_intake(WEEK_START)

    assert result == {"week_start": WEEK_START, "cleared": True}
    # "Nobody has started" — not a new empty revision, and get_week_intake
    # answers exactly what it answers for a week nobody has touched yet.
    assert tools.get_week_intake(WEEK_START) is None
    # Append-only held: the old revision is still on the table, superseded.
    history = tools.get_week_intake_history(WEEK_START)
    assert len(history) == 1
    conn = get_conn()
    row = conn.execute(
        "SELECT superseded_at FROM week_intake WHERE household_id = 1 AND week_start = ?", (WEEK_START,)
    ).fetchone()
    conn.close()
    assert row["superseded_at"] is not None


def test_clear_week_intake_on_a_week_nobody_answered_does_nothing():
    assert tools.get_week_intake(WEEK_START) is None
    result = tools.clear_week_intake(WEEK_START)
    assert result == {"week_start": WEEK_START, "cleared": False}


def test_clear_week_intake_never_touches_household_setup():
    """The narrow line the task drew: household setup (Preferences: meal
    counts, dislikes, kitchen kit...) is a different table entirely and was
    never at risk, but this pins it so a future change can't blur the two."""
    _seed_intake_and_plan()
    conn = get_conn()
    before = dict(conn.execute("SELECT * FROM meal_preferences WHERE household_id = 1").fetchone() or {})
    conn.close()

    tools.clear_week_intake(WEEK_START)

    conn = get_conn()
    after = dict(conn.execute("SELECT * FROM meal_preferences WHERE household_id = 1").fetchone() or {})
    conn.close()
    assert after == before


def test_the_preview_reports_whether_this_week_has_answers_on_file():
    plan_id = _seed_intake_and_plan()

    preview = tools.get_reset_preview(plan_id)
    assert preview["intake_count"] == 1

    tools.clear_week_intake(WEEK_START)
    preview_after = tools.get_reset_preview(plan_id)
    assert preview_after["intake_count"] == 0


def test_the_preview_answers_zero_with_no_plan_on_file():
    """No plan means no week_start_date to check an intake against — the
    third option has nothing to name, same as the other two counts
    answering 0 with nothing to clear."""
    preview = tools.get_reset_preview()
    assert preview["weekly_plan_id"] is None
    assert preview["intake_count"] == 0


def test_the_route_clears_only_the_answers_when_thats_all_thats_asked(signed_in):
    """FAILS ON MAIN: POST /api/reset had no third option at all."""
    plan_id = _seed_intake_and_plan()
    before_meals = get_conn().execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)
    ).fetchone()["n"]

    res = signed_in.post("/api/reset", json={"week_answers": True, "weekly_plan_id": plan_id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_answers"] == {"week_start": WEEK_START, "cleared": True}
    assert body["meal_plan"] is None and body["grocery_list"] is None
    assert tools.get_week_intake(WEEK_START) is None
    # The plan itself never moved — this option touches intake alone.
    after_meals = get_conn().execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)
    ).fetchone()["n"]
    assert after_meals == before_meals


def test_the_route_still_rejects_an_empty_request(signed_in):
    res = signed_in.post("/api/reset", json={})
    assert res.status_code == 400


def test_the_route_can_combine_all_three(signed_in):
    plan_id = _seed_intake_and_plan()
    tools.plan_meal(WEEK_START, "Whatever's around", slot="dinner", weekly_plan_id=plan_id)

    res = signed_in.post(
        "/api/reset",
        json={"meal_plan": True, "grocery_list": True, "week_answers": True, "weekly_plan_id": plan_id},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["meal_plan"]["weekly_plan_id"] == plan_id
    assert body["week_answers"]["cleared"] is True
    assert tools.get_week_intake(WEEK_START) is None
