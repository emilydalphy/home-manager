"""
The Plan tab's More sheet, mockup "10C" (Emily, approved 2026-09-25): every
row is an icon, a title, and one line saying exactly what happens or where
it goes — replacing the sheet's mix of bare labels and optional sub-lines —
grouped under two eyebrows: the week on screen's own rare moves ("This
draft" / "This week"), then "Everything else" for the ones that aren't
about one particular week.

Behaviour changes that ride with the redesign, each checked below:
  1. "Drop this draft" is renamed "Keep my approved week" and, since an
     approved week is the whole reason the row is an easy yes, it is now
     hidden outright rather than carrying a different line when there is
     no approved week under the draft (data.replaces). Its confirm dialog
     matches: "Keep your approved week?" / "This draft goes. {label}
     stays as it is." / "Keep my week", and the toast drops the old
     "Dropped. " prefix.
  2. "Adjust your setup" opens Preferences (the gear's sheet, openPrefsSheet)
     — never /meal-setup and never chat. Concurrent work on a different
     branch owns the Preferences sheet's own internals and /meal-setup
     itself; this only ever calls the existing opener.
  3. "Start over" gains a third, independent option — "This week's
     answers" — that clears the current week's planning-question intake
     (app/tools/week_intake.py's clear_week_intake, via app/tools/reset.py's
     clear_week_answers) and NOTHING else. Never household setup/
     onboarding, never a week whose actual PERIOD overlaps a second live
     plan (date-range overlap, not merely the same week_start_date —
     app/main.py POST /api/reset, GET /api/reset/preview).

     This option briefly ALSO cleared slot_attendance (who's in, guests,
     trips) earlier the same day, then was narrowed straight back on a
     THIRD review: the schema can't support clearing attendance safely —
     one row per (date, slot) with a single last-writer `source` (a later
     write on the same slot loses whichever fact came first), no undo
     helper anywhere for an away stretch's derived 'quick'/'ready_made'
     edge needs, and a hosting holiday writing into BOTH slot_attendance
     and the intake through the very same call chat's own guest-count
     gesture uses — clearing attendance there would half-undo a holiday
     answer that itself stays untouched. See app/tools/reset.py's module
     comment and clear_week_answers's docstring for the full reasoning.
     Holiday answers were never touched either way.
  4. The checkbox behind option 3 never auto-checks (unlike the other
     two), and is disabled with a plain reason rather than offered when
     this week overlaps another live plan.
  5. save_week_intake's revision numbering survives a clear with no
     replacement — the bug that made every later save 500 forever
     (UNIQUE(household_id, week_start, revision) rejecting a reused
     revision 1).
  6. The reset dialog and toast never render an empty or "undefined"
     clause when nothing was actually cleared.
  7. The overlap guard runs BEFORE the meal-plan/grocery-list clears in
     POST /api/reset, so a refusal never leaves a partial reset.

One disclosed, unfixed gap: a hosting holiday's own write into
week_intake (a "guests" night tag plus a guest_count, written by
holidays.py's _set_hosting through the same save_week_intake call a
household's own guests chip would use) is not distinguishable, once
stored, from a guests tag the household typed themselves — there is no
provenance field on an individual night_tags entry. So clearing this
week's intake also removes a holiday-written guests tag while
holiday_answers itself keeps saying "hosting", and the two fall out of
step. Telling them apart cleanly would need a schema change; this is
reported rather than guessed at (app/tools/reset.py's clear_week_answers
docstring carries the same note).

The JS half runs shell.js's own functions under node (tests/nodeharness.py,
the house pattern — see its own docstring for why source-marker tests miss
real bugs here). The server half is ordinary pytest against the tools
layer and the routes.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import nodeharness
from test_week_seven_tiles import _extract, _extract_async, _extract_var

from app import tools
from app.db import get_conn

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
# JS: the "This week's answers" checkbox — never auto-checked, disabled
# with a reason when shared, and a precise line otherwise (review items 2-5)
# ---------------------------------------------------------------------------

def _cb_harness() -> str:
    return (
        _extract("joinWithAnd", SHELL_JS) + "\n"
        + _extract("setResetAnswersOptionState", SHELL_JS) + "\n"
        + """
function makeCb() {
  var row = { classes: {} };
  row.classList = { toggle: function (name, on) { row.classes[name] = !!on; } };
  var cb = { checked: true, disabled: false, closest: function () { return row; } };
  cb._row = row;
  return cb;
}
function makeSub() { return { textContent: '' }; }
"""
    )


def _run(harness: str):
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_join_with_and_never_produces_a_dangling_and():
    """FAILS ON MAIN: the old inline join (parts.slice(0,-1).join(', ') +
    ' and ' + parts[parts.length-1]) reads parts[-1] as undefined on an
    empty array, rendering "Cleared  and undefined." when nothing was
    actually cleared."""
    out = _run(_extract("joinWithAnd", SHELL_JS) + """
console.log(JSON.stringify({
  zero: joinWithAnd([]),
  one: joinWithAnd(['a']),
  two: joinWithAnd(['a', 'b']),
  three: joinWithAnd(['a', 'b', 'c'])
}));
""")
    assert out == {"zero": "", "one": "a", "two": "a and b", "three": "a, b and c"}


@_needs_node
def test_the_answers_checkbox_never_auto_checks_when_something_is_on_file():
    """FAILS ON MAIN: setResetOptionState (reused for all three options)
    checks the box whenever count > 0 — right for the plan and the list,
    wrong for typed answers, which the review called out as needing a
    deliberate second tap."""
    out = _run(_cb_harness() + f"""
var cb = makeCb();
var sub = makeSub();
setResetAnswersOptionState(cb, sub, {json.dumps({"intake_count": 1, "intake_shared": False, "week_label": "Sep 21-27"})});
console.log(JSON.stringify({{ checked: cb.checked, disabled: cb.disabled, sub: sub.textContent }}));
""")
    assert out["checked"] is False
    assert out["disabled"] is False
    assert out["sub"] == "Clears your answers to this week's questions. Who's home, guests and trips stay as they are."


@_needs_node
def test_the_answers_checkbox_is_disabled_with_a_reason_when_shared():
    """FAILS ON MAIN: there was no intake_shared concept at all — a draft
    over an approved week overlapping the same days would silently offer
    (and, if checked, wipe) the approved week's own on-file answers."""
    out = _run(_cb_harness() + f"""
var cb = makeCb();
var sub = makeSub();
setResetAnswersOptionState(cb, sub, {json.dumps({"intake_count": 1, "intake_shared": True, "week_label": "Sep 21-27"})});
console.log(JSON.stringify({{ checked: cb.checked, disabled: cb.disabled, sub: sub.textContent, empty: cb._row.classes['is-empty'] }}));
""")
    assert out["disabled"] is True
    assert out["checked"] is False
    assert "on the same days" in out["sub"] or "another plan" in out["sub"]
    assert out["empty"] is True


@_needs_node
def test_the_answers_checkbox_disabled_when_nothing_is_on_file():
    out = _run(_cb_harness() + f"""
var cb = makeCb();
var sub = makeSub();
setResetAnswersOptionState(cb, sub, {json.dumps({"intake_count": 0, "intake_shared": False, "week_label": None})});
console.log(JSON.stringify({{ disabled: cb.disabled, sub: sub.textContent }}));
""")
    assert out["disabled"] is True
    assert "haven't answered" in out["sub"]


# ---------------------------------------------------------------------------
# JS: runReset's toast never dangles on an empty/undefined clause
# ---------------------------------------------------------------------------

_RUN_RESET_STUBS = """
var resetSubmitting = false;
var resetConfirmBtn = { textContent: '' };
var resetMealCb = { checked: false };
var resetGroceryCb = { checked: false };
var resetAnswersCb = { checked: true };
var resetPlanId = 42;
var CALLS = [];
function syncResetConfirmBtn() { CALLS.push(['sync']); }
function closeResetDialog() { CALLS.push(['closeResetDialog']); }
function refreshAfterReset(a, b) { CALLS.push(['refreshAfterReset', a, b]); }
var TOAST = null;
function showToast(m) { TOAST = m; }
function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
"""


def _run_reset_harness() -> str:
    return (
        _extract("joinWithAnd", SHELL_JS) + "\n"
        + _RUN_RESET_STUBS
        + _extract_async("runReset", SHELL_JS) + "\n"
    )


@_needs_node
def test_runreset_toast_when_nothing_was_actually_cleared():
    """FAILS ON MAIN: with only week_answers checked and the server
    reporting nothing to clear (e.g. a stale race where the box was
    checked just as the count went to zero), the old code pushed
    "this week's answers" onto parts whenever data.week_answers was
    truthy at all — a plain object, always truthy — then joined a
    single-element array by reading parts[-1], landing on "Cleared
    this week's answers. Fresh start." even though nothing moved. The
    fix checks the actual sub-fields cleared, not the container's
    truthiness, and this pins the true empty case: the container
    present, every sub-count zero."""
    reply = {
        "meal_plan": None, "grocery_list": None,
        "week_answers": {"week_start": "2026-09-21", "intake": None},
    }
    out = _run(_run_reset_harness() + f"""
function fetch(url, opts) {{ return Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve({json.dumps(reply)}); }} }}); }}
(async function () {{
  await runReset();
  console.log(JSON.stringify({{ toast: TOAST }}));
}})();
""")
    assert "undefined" not in out["toast"]
    assert out["toast"] == "There was nothing to clear."


@_needs_node
def test_runreset_toast_composes_all_three_when_all_three_cleared():
    reply = {
        "meal_plan": {"meals_cleared": 4},
        "grocery_list": {"removed_count": 9},
        "week_answers": {"week_start": "2026-09-21", "intake": {"week_start": "2026-09-21", "cleared": True}},
    }
    out = _run(_run_reset_harness() + f"""
function fetch(url, opts) {{ return Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve({json.dumps(reply)}); }} }}); }}
(async function () {{
  await runReset();
  console.log(JSON.stringify({{ toast: TOAST }}));
}})();
""")
    assert out["toast"] == "Cleared 4 meals, the grocery list and this week's answers. Fresh start."


# ---------------------------------------------------------------------------
# JS: the "Keep your approved week?" confirm (renamed from "Drop this draft")
# ---------------------------------------------------------------------------

def test_the_static_html_and_toast_carry_the_new_words_not_the_old():
    assert 'id="discard-draft-title">Keep your approved week?<' in SHELL_HTML
    assert 'id="discard-draft-confirm">Keep my week<' in SHELL_HTML
    assert "Drop this draft?" not in SHELL_HTML
    assert ">Drop it<" not in SHELL_HTML
    drop = _extract("discardDraft", SHELL_JS)
    assert "out.approved_week_label + ' is still your week.'" in drop
    assert "'Dropped. '" not in drop
    ask = _extract("askAboutDroppingDraft", SHELL_JS)
    assert "discard-draft-note" in ask
    assert "stays as it is." in ask


# ---------------------------------------------------------------------------
# Server: clear_week_intake and the save-after-clear revision bug (BLOCKER)
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


def test_saving_again_after_a_clear_does_not_500(signed_in):
    """BLOCKER, FAILS ON MAIN: save_week_intake computed
    revision = current["revision"] + 1 OR 1 if current is None.
    clear_week_intake supersedes the only row with no replacement, so
    current is None on the very next save and it tries revision 1 again —
    which the UNIQUE(household_id, week_start, revision) index already
    holds (superseded, but still a row) — an IntegrityError on every
    retry, raised after 5 attempts. Every later POST
    /api/week/{week_start}/intake for this week 500s forever after one
    clear. The fix takes the next revision from MAX(revision) ever
    written for the week, not from the current unsuperseded row."""
    _seed_intake_and_plan()
    tools.clear_week_intake(WEEK_START)

    # The tool call itself must not raise.
    saved = tools.save_week_intake(WEEK_START, night_tags={WEEK_START: ["rush"]}, created_by="Emily")
    assert saved["revision"] == 2  # one past the highest revision EVER written, superseded or not
    assert tools.get_week_intake(WEEK_START)["night_tags"] == {WEEK_START: ["rush"]}

    # And the route a real save goes through must not 500 either.
    tools.clear_week_intake(WEEK_START)
    res = signed_in.post(
        f"/api/week/{WEEK_START}/intake",
        json={"night_tags": {WEEK_START: ["normal"]}, "created_by": "Emily", "day_count": 7},
    )
    assert res.status_code == 200, res.text
    assert res.json()["revision"] == 3


def test_a_clear_save_clear_save_cycle_keeps_climbing():
    """Two clears in a row, each followed by a save — the revision number
    only ever goes up, never collides, however many times a week is wiped
    and re-answered."""
    _seed_intake_and_plan()  # revision 1
    tools.clear_week_intake(WEEK_START)
    r2 = tools.save_week_intake(WEEK_START, night_tags={WEEK_START: ["rush"]})
    assert r2["revision"] == 2
    tools.clear_week_intake(WEEK_START)
    r3 = tools.save_week_intake(WEEK_START, night_tags={WEEK_START: ["normal"]})
    assert r3["revision"] == 3
    history = tools.get_week_intake_history(WEEK_START)
    assert [h["revision"] for h in history] == [1, 2, 3]



# ---------------------------------------------------------------------------
# Server: the shared-week guard is a date-range OVERLAP, not equal
# week_start_date (review items C and D); attendance/holiday clearing was
# tried and then removed entirely on a third review (see the module
# docstring and app/tools/reset.py's clear_week_answers)
# ---------------------------------------------------------------------------

def _make_overlapping_draft(period_start: str, day_count: int) -> int:
    """A second, live plan whose actual PERIOD overlaps another plan's
    days without sharing its week_start_date — exactly the shape item C
    named: a mid-week re-plan starts on a different day but still
    overlaps the approved week underneath it."""
    plan = tools.create_weekly_plan(period_start, content_start_date=period_start, day_count=day_count)
    return plan["weekly_plan_id"]


def _approve(plan_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


def test_the_preview_reports_overlap_even_when_week_start_dates_differ():
    """FAILS ON MAIN (item C): comparing week_start_date for equality
    misses exactly this shape — a mid-week draft (week_start_date
    "2026-09-24") overlapping the SAME days ("2026-09-24".."2026-09-27")
    as an already-approved plan whose week_start_date is "2026-09-21".
    Equal-week_start_date would have called this pair unrelated and
    offered the option against the approved week's real, on-file answer."""
    approved_id = _seed_intake_and_plan()
    _approve(approved_id)
    draft_id = _make_overlapping_draft("2026-09-24", 4)

    # From the approved plan's own side: a real answer exists, but the
    # overlap hides it rather than offering it as if it were safe.
    preview = tools.get_reset_preview(approved_id)
    assert preview["intake_shared"] is True
    assert preview["intake_count"] == 0
    assert tools.get_week_intake(WEEK_START) is not None  # the answer genuinely exists

    # And from the mid-week draft's own side, the same overlap is seen.
    preview_from_draft = tools.get_reset_preview(draft_id)
    assert preview_from_draft["intake_shared"] is True


def test_the_preview_is_not_shared_once_the_overlapping_plan_is_retired():
    approved_id = _seed_intake_and_plan()
    _approve(approved_id)
    draft_id = _make_overlapping_draft("2026-09-24", 4)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'retired' WHERE id = ?", (draft_id,))
    conn.commit()
    conn.close()

    preview = tools.get_reset_preview(approved_id)
    assert preview["intake_shared"] is False
    assert preview["intake_count"] == 1


def test_clear_week_answers_refuses_an_overlapping_week():
    approved_id = _seed_intake_and_plan()
    _approve(approved_id)
    _make_overlapping_draft("2026-09-24", 4)

    with pytest.raises(ValueError):
        tools.clear_week_answers(approved_id)
    # Refused outright — nothing moved.
    assert tools.get_week_intake(WEEK_START) is not None


def test_the_route_refuses_an_overlapping_week_with_a_400(signed_in):
    approved_id = _seed_intake_and_plan()
    _approve(approved_id)
    _make_overlapping_draft("2026-09-24", 4)

    res = signed_in.post("/api/reset", json={"week_answers": True, "weekly_plan_id": approved_id})
    assert res.status_code == 400
    assert tools.get_week_intake(WEEK_START) is not None


def test_the_route_refuses_the_whole_request_before_touching_anything(signed_in):
    """FAILS ON MAIN (item F): the overlap guard used to run only inside
    clear_week_answers, called LAST in the route — so a request
    combining all three cleared the meal plan and the grocery list,
    committed, and only then hit the guard and 400'd on week_answers,
    leaving a partial reset behind the very refusal that was supposed to
    stop it. tools.check_week_answers_clearable now runs first, before
    meal_plan or grocery_list touch anything."""
    approved_id = _seed_intake_and_plan()
    tools.add_recipe("Boiled eggs", ingredients=[{"item": "Eggs", "qty": "4"}], default_servings=2)
    tools.plan_meal(
        WEEK_START, "Boiled eggs", slot="dinner", weekly_plan_id=approved_id,
        add_ingredients_to_grocery_list=True,
    )
    _approve(approved_id)
    _make_overlapping_draft("2026-09-24", 4)
    before_meals = get_conn().execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?", (approved_id,)
    ).fetchone()["n"]
    before_grocery = len(tools.list_grocery_list(status="needed"))
    assert before_meals > 0 and before_grocery > 0

    res = signed_in.post(
        "/api/reset",
        json={"meal_plan": True, "grocery_list": True, "week_answers": True, "weekly_plan_id": approved_id},
    )

    assert res.status_code == 400
    after_meals = get_conn().execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?", (approved_id,)
    ).fetchone()["n"]
    assert after_meals == before_meals
    assert len(tools.list_grocery_list(status="needed")) == before_grocery


# ---------------------------------------------------------------------------
# Server: the /api/reset route with the (narrow, intake-only) week_answers
# option
# ---------------------------------------------------------------------------

def test_the_route_clears_only_the_answers_when_thats_all_thats_asked(signed_in):
    """FAILS ON MAIN: POST /api/reset had no third option at all."""
    plan_id = _seed_intake_and_plan()
    before_meals = get_conn().execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)
    ).fetchone()["n"]

    res = signed_in.post("/api/reset", json={"week_answers": True, "weekly_plan_id": plan_id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_answers"] == {"week_start": WEEK_START, "intake": {"week_start": WEEK_START, "cleared": True}}
    assert body["meal_plan"] is None and body["grocery_list"] is None
    assert tools.get_week_intake(WEEK_START) is None
    # The plan itself never moved — this option touches the intake alone.
    after_meals = get_conn().execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)
    ).fetchone()["n"]
    assert after_meals == before_meals


def test_the_route_never_touches_attendance_or_holidays():
    """Pins the narrowing itself: even with real attendance and a real
    holiday answer on file this week, week_answers's clear leaves both
    completely alone — only week_intake moves."""
    plan_id = _seed_intake_and_plan()
    tools.set_day_attendance(WEEK_START, {"lunch": {"absent": ["Emily"]}})
    conn = get_conn()
    conn.execute(
        "INSERT INTO holiday_answers (household_id, date, holiday_name, answer) VALUES (1, ?, 'Test Day', 'just_us')",
        (WEEK_START,),
    )
    conn.commit()
    conn.close()

    tools.clear_week_answers(plan_id)

    conn = get_conn()
    attendance_row = conn.execute(
        "SELECT source FROM slot_attendance WHERE household_id = 1 AND date = ? AND slot = 'lunch'", (WEEK_START,)
    ).fetchone()
    holiday_row = conn.execute(
        "SELECT * FROM holiday_answers WHERE household_id = 1 AND date = ?", (WEEK_START,)
    ).fetchone()
    conn.close()
    assert attendance_row is not None and attendance_row["source"] == "sheet"
    assert holiday_row is not None


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
    assert body["week_answers"]["intake"]["cleared"] is True
    assert tools.get_week_intake(WEEK_START) is None
