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
     AND the day-attendance-sheet/guests-chip/away-stretch attendance and
     the holiday answers for this week (app/tools/week_intake.py,
     app/tools/reset.py: clear_week_intake, clear_week_answers), never
     household setup/onboarding, never a standalone attendance toggle or
     a chat correction, and never a week shared with a second live plan
     (app/main.py POST /api/reset, GET /api/reset/preview).
  4. The checkbox behind option 3 never auto-checks (unlike the other
     two), and is disabled with a plain reason rather than offered when
     this week's answers are shared with another live plan.
  5. save_week_intake's revision numbering survives a clear with no
     replacement — the bug that made every later save 500 forever
     (UNIQUE(household_id, week_start, revision) rejecting a reused
     revision 1).
  6. The reset dialog and toast never render an empty or "undefined"
     clause when nothing was actually cleared.

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
from test_week_seven_tiles import _extract, _extract_async, _extract_var

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
# JS: the "This week's answers" checkbox — never auto-checked, disabled
# with a reason when shared, and a precise line otherwise (review items 2-5)
# ---------------------------------------------------------------------------

def _make_cb(checked=True):
    return {"checked": checked}


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
    cb = _make_cb(checked=True)
    out = _run(_cb_harness() + f"""
var cb = makeCb();
var sub = makeSub();
setResetAnswersOptionState(cb, sub, {json.dumps({
        "intake_count": 1, "attendance_count": 0, "holiday_count": 0, "intake_shared": False, "week_label": "Sep 21-27",
    })});
console.log(JSON.stringify({{ checked: cb.checked, disabled: cb.disabled, sub: sub.textContent }}));
""")
    assert out["checked"] is False
    assert out["disabled"] is False
    assert "the planning questions" in out["sub"]


@_needs_node
def test_the_answers_checkbox_is_disabled_with_a_reason_when_shared():
    """FAILS ON MAIN: there was no intake_shared concept at all — a draft
    over an approved week for the same week_start would silently offer
    (and, if checked, wipe) the approved week's own rush caps and away
    days along with the draft's."""
    out = _run(_cb_harness() + f"""
var cb = makeCb();
var sub = makeSub();
setResetAnswersOptionState(cb, sub, {json.dumps({
        "intake_count": 1, "attendance_count": 2, "holiday_count": 0, "intake_shared": True, "week_label": "Sep 21-27",
    })});
console.log(JSON.stringify({{ checked: cb.checked, disabled: cb.disabled, sub: sub.textContent, empty: cb._row.classes['is-empty'] }}));
""")
    assert out["disabled"] is True
    assert out["checked"] is False
    assert "approved week" in out["sub"]
    assert out["empty"] is True


@_needs_node
def test_the_answers_checkbox_names_only_the_categories_that_have_something():
    out = _run(_cb_harness() + f"""
var cb = makeCb();
var sub = makeSub();
setResetAnswersOptionState(cb, sub, {json.dumps({
        "intake_count": 0, "attendance_count": 3, "holiday_count": 1, "intake_shared": False, "week_label": "Sep 21-27",
    })});
console.log(JSON.stringify(sub.textContent));
""")
    assert "the planning questions" not in out
    assert "who's in for meals" in out or "who’s in for meals" in out
    assert "the holiday you answered" in out
    assert "Your household settings stay as they are." in out


@_needs_node
def test_the_answers_checkbox_disabled_when_nothing_is_on_file():
    out = _run(_cb_harness() + f"""
var cb = makeCb();
var sub = makeSub();
setResetAnswersOptionState(cb, sub, {json.dumps({
        "intake_count": 0, "attendance_count": 0, "holiday_count": 0, "intake_shared": False, "week_label": None,
    })});
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
        "week_answers": {"week_start": "2026-09-21", "intake": None, "attendance_cleared": 0, "holidays_cleared": 0},
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
        "week_answers": {"week_start": "2026-09-21", "intake": {"week_start": "2026-09-21", "cleared": True},
                          "attendance_cleared": 2, "holidays_cleared": 1},
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
# Server: attendance / holiday clearing scope (review item 2)
# ---------------------------------------------------------------------------

IN_WEEK_DATE = "2026-09-22"       # inside WEEK_START's 7-day period
OUT_OF_WEEK_DATE = "2026-09-29"   # the following week — must survive


def _insert_attendance(date_str: str, source: str, slot: str = "dinner", guest_count: int = 0) -> int:
    # UNIQUE(household_id, date, slot): several sources on the SAME date in
    # these tests use different slots so the seed rows don't collide.
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO slot_attendance (household_id, date, slot, absent_member_ids_json, guest_count, source) "
        "VALUES (1, ?, ?, '[]', ?, ?)",
        (date_str, slot, guest_count, source),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def _insert_holiday_answer(date_str: str, answer: str = "just_us") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO holiday_answers (household_id, date, holiday_name, answer) VALUES (1, ?, 'Test Day', ?)",
        (date_str, answer),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def _attendance_ids_left(*ids: int) -> set[int]:
    conn = get_conn()
    rows = conn.execute(
        f"SELECT id FROM slot_attendance WHERE id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    conn.close()
    return {r["id"] for r in rows}


def _holiday_ids_left(*ids: int) -> set[int]:
    conn = get_conn()
    rows = conn.execute(
        f"SELECT id FROM holiday_answers WHERE id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    conn.close()
    return {r["id"] for r in rows}


def test_the_preview_counts_attendance_and_holidays_for_this_week_only():
    """FAILS ON MAIN: get_reset_preview had no idea attendance or holiday
    answers existed — the dialog's "This week's answers" line described
    only the intake, understating what the option was about to touch
    once the underlying clear was widened."""
    plan_id = _seed_intake_and_plan()
    guests_id = _insert_attendance(WEEK_START, "guests", slot="dinner")
    sheet_id = _insert_attendance(IN_WEEK_DATE, "sheet", slot="dinner")
    toggle_id = _insert_attendance(IN_WEEK_DATE, "toggle", slot="lunch")  # NOT a weekly question
    chat_id = _insert_attendance(IN_WEEK_DATE, "chat", slot="breakfast")  # a permanent correction, NOT a weekly question
    outside_id = _insert_attendance(OUT_OF_WEEK_DATE, "sheet", slot="dinner")  # next week, NOT this week's
    _insert_holiday_answer(WEEK_START)

    preview = tools.get_reset_preview(plan_id)
    assert preview["attendance_count"] == 2  # guests + sheet only
    assert preview["holiday_count"] == 1
    assert preview["intake_shared"] is False


def test_clear_week_answers_clears_only_this_weeks_question_sources():
    """FAILS ON MAIN: clear_week_answers/tools.clear_week_intake alone left
    who's-out, guest-hosting and away-stretch attendance, and holiday
    answers, all still on file — generation, grocery scaling and the Days
    tiles kept reading them, so "This week's answers" cleared far less
    than it claimed to."""
    plan_id = _seed_intake_and_plan()
    guests_id = _insert_attendance(WEEK_START, "guests", slot="dinner")
    sheet_id = _insert_attendance(IN_WEEK_DATE, "sheet", slot="dinner")
    away_id = _insert_attendance(IN_WEEK_DATE, "away_stretch", slot="lunch")
    toggle_id = _insert_attendance(IN_WEEK_DATE, "toggle", slot="breakfast")
    chat_id = _insert_attendance(WEEK_START, "chat", slot="lunch")
    outside_id = _insert_attendance(OUT_OF_WEEK_DATE, "sheet", slot="dinner")
    holiday_id = _insert_holiday_answer(WEEK_START)
    outside_holiday_id = _insert_holiday_answer(OUT_OF_WEEK_DATE)

    result = tools.clear_week_answers(plan_id)

    assert result["attendance_cleared"] == 3  # guests + sheet + away_stretch
    assert result["holidays_cleared"] == 1
    assert result["intake"] == {"week_start": WEEK_START, "cleared": True}
    # The weekly-question sources, in this week, are gone.
    assert _attendance_ids_left(guests_id, sheet_id, away_id) == set()
    # A standalone toggle and a chat correction are not "the week's
    # questions" and survive — same for anything outside this week.
    assert _attendance_ids_left(toggle_id, chat_id, outside_id) == {toggle_id, chat_id, outside_id}
    assert _holiday_ids_left(holiday_id) == set()
    assert _holiday_ids_left(outside_holiday_id) == {outside_holiday_id}
    assert tools.get_week_intake(WEEK_START) is None


def test_clear_week_answers_never_touches_household_setup():
    plan_id = _seed_intake_and_plan()
    _insert_attendance(WEEK_START, "guests")
    _insert_holiday_answer(WEEK_START)
    conn = get_conn()
    before = dict(conn.execute("SELECT * FROM meal_preferences WHERE household_id = 1").fetchone() or {})
    conn.close()

    tools.clear_week_answers(plan_id)

    conn = get_conn()
    after = dict(conn.execute("SELECT * FROM meal_preferences WHERE household_id = 1").fetchone() or {})
    conn.close()
    assert after == before


def test_clear_week_answers_with_no_plan_does_nothing_and_does_not_raise():
    result = tools.clear_week_answers()
    assert result == {"week_start": None, "intake": None, "attendance_cleared": 0, "holidays_cleared": 0}


# ---------------------------------------------------------------------------
# Server: a draft sharing its week with an approved plan (review item 4)
# ---------------------------------------------------------------------------

def _make_twin_approved_plan(week_start: str) -> int:
    """A second, APPROVED plan on the very same week_start — the shape
    tools.clear_week_answers and get_reset_preview must refuse to guess
    between (a re-plan over an already-approved week, or the reverse)."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (1, ?, 'approved')",
        (week_start,),
    )
    conn.commit()
    plan_id = cur.lastrowid
    conn.close()
    return plan_id


def test_the_preview_reports_when_this_week_is_shared_with_another_plan():
    """FAILS ON MAIN: intake_shared did not exist — a draft re-planning a
    week an approved plan already covers would have offered "This week's
    answers" against the draft's own (empty) counts while a clear would
    actually have reached the approved week's rush caps and away days
    too, since week_intake/slot_attendance/holiday_answers are keyed by
    week_start, never by plan id."""
    plan_id = _seed_intake_and_plan()
    _make_twin_approved_plan(WEEK_START)

    preview = tools.get_reset_preview(plan_id)
    assert preview["intake_shared"] is True
    # The dialog must not show real counts as if the option were safe to
    # offer, even though the intake genuinely exists.
    assert preview["intake_count"] == 0
    assert preview["attendance_count"] == 0
    assert preview["holiday_count"] == 0


def test_the_preview_is_not_shared_when_the_other_plan_is_retired():
    plan_id = _seed_intake_and_plan()
    twin = _make_twin_approved_plan(WEEK_START)
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'retired' WHERE id = ?", (twin,))
    conn.commit()
    conn.close()

    preview = tools.get_reset_preview(plan_id)
    assert preview["intake_shared"] is False
    assert preview["intake_count"] == 1


def test_clear_week_answers_refuses_a_shared_week():
    plan_id = _seed_intake_and_plan()
    _make_twin_approved_plan(WEEK_START)

    with pytest.raises(ValueError):
        tools.clear_week_answers(plan_id)
    # Refused outright — nothing moved.
    assert tools.get_week_intake(WEEK_START) is not None


def test_the_route_refuses_a_shared_week_with_a_400(signed_in):
    plan_id = _seed_intake_and_plan()
    _make_twin_approved_plan(WEEK_START)

    res = signed_in.post("/api/reset", json={"week_answers": True, "weekly_plan_id": plan_id})
    assert res.status_code == 400
    assert tools.get_week_intake(WEEK_START) is not None


# ---------------------------------------------------------------------------
# Server: the /api/reset route with the widened week_answers option
# ---------------------------------------------------------------------------

def test_the_route_clears_only_the_answers_when_thats_all_thats_asked(signed_in):
    """FAILS ON MAIN: POST /api/reset had no third option at all."""
    plan_id = _seed_intake_and_plan()
    _insert_attendance(WEEK_START, "guests")
    before_meals = get_conn().execute(
        "SELECT COUNT(*) AS n FROM meal_plan_entries WHERE weekly_plan_id = ?", (plan_id,)
    ).fetchone()["n"]

    res = signed_in.post("/api/reset", json={"week_answers": True, "weekly_plan_id": plan_id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_answers"]["intake"] == {"week_start": WEEK_START, "cleared": True}
    assert body["week_answers"]["attendance_cleared"] == 1
    assert body["meal_plan"] is None and body["grocery_list"] is None
    assert tools.get_week_intake(WEEK_START) is None
    # The plan itself never moved — this option touches intake/attendance/
    # holidays alone.
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
    assert body["week_answers"]["intake"]["cleared"] is True
    assert tools.get_week_intake(WEEK_START) is None
