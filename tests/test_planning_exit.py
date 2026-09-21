"""Planning: a way out of the draft that isn't approving it.

Emily, 2026-09-13, on her phone: "once you start trying to plan the draft
for a week you can't select out of that screen. there's no 'back option out
of the plan' you can only exit the process by approving the week."

Two doors were missing, and both are checked here:

  1. /plan-week (the intake questions and the drafting screen) is a page
     outside the shell with no tab bar, and on an installed PWA no browser
     chrome either. Its first screen had no Back at all and no screen had a
     way to the rest of the app — the only button that left the page was
     "Draft my week". It carries a "‹ Plan" crumb now, on every screen.
  2. Since a draft became the Plan tab's root (Build 3, 2026-09-11) the
     draft screen had lost the "More ···" foot, so "Try again" and "Change
     my answers" could not be reached from a draft; Approve was the only
     control on it. The foot is back on the draft root.

And the acceptance criterion that matters most: leaving — at any point —
never approves a week, never adds to the grocery list, never schedules
anything. The draft stays a draft, and Plan shows it with the answers that
made it.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest

from app import agent, tools
from app.db import get_conn
from tests import nodeharness

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. /plan-week has a door on every screen
# --------------------------------------------------------------------------

def _flow_markup() -> str:
    return PAGE[PAGE.index('<main id="flow"'):PAGE.index("</main>")]


def test_the_question_screens_carry_one_named_crumb_out_of_the_flow():
    flow = _flow_markup()
    crumbs = re.findall(r'<button type="button" class="crumb" id="leave">([^<]*)</button>', flow)
    assert crumbs == ["&lsaquo; Plan"], "exactly one crumb, and it names the tab it goes to"
    # Above the first question, outside every step section — so it is on
    # screen for all five questions AND the drafting screen, which hides
    # the sections and the foot.
    assert flow.index('id="leave"') < flow.index('<section id="q1">')
    assert "$('leave').hidden" not in PAGE, "nothing ever hides the way out"
    # Since 2026-09-21 (the onboarding motion) the one crumb is "‹ Plan" on
    # the first step and while drafting, and the stepper's own "‹ Back" on
    # the steps between — one way back per screen, never two.
    assert "$('leave').addEventListener('click', crumbTap);" in PAGE
    tap = _extract("crumbTap", PAGE)
    assert "if (step === 1 || drafting) leaveFlow();" in tap and "else goBack();" in tap
    assert "$('leave').innerHTML = n === 1 ? '&lsaquo; Plan' : '&lsaquo; Back';" in PAGE
    assert "$('leave').innerHTML = '&lsaquo; Plan';" in _extract("showDraftProgress", PAGE)
    assert 'id="back"' not in flow, "no second back beside the crumb"


def test_the_crumb_is_a_plain_navigation_to_plan_never_the_browsers_back():
    fn = _extract("leaveFlow", PAGE)
    code = re.sub(r"//[^\n]*", "", fn)
    assert "location.href = '/week';" in code
    assert "history.back()" not in code


def test_leaving_saves_what_changed_on_the_current_screen_without_waiting():
    """Coming back should carry on from here — but a save that fails, or
    hangs, must never keep the household on the screen they are leaving."""
    fn = _extract("leaveFlow", PAGE)
    assert "saveIntake(payload, true).catch(" in fn      # keepalive, not awaited
    assert "await" not in re.sub(r"//[^\n]*", "", fn)
    assert "keepalive: !!keepalive," in PAGE
    # Nothing changed, nothing written — including a set prefilled from a
    # joined intake or an earlier draft.
    assert "if (answersSnapshot() === answersAtLoad) return null;" in PAGE
    assert "answersAtLoad = answersSnapshot();" in PAGE
    # Since 2026-09-21 the payload is every answer, not the current
    # screen's half — Back never loses one, so leaving from step 2 still
    # carries a mood chosen on step 4.
    payload = _extract("leavePayload", PAGE)
    for key in ("night_tags: answers.night_tags", "packed_lunch_days: answers.packed_lunch_days",
                "moods: answers.moods", "freeform: $('freeform').value.trim()"):
        assert key in payload, key


def test_the_drafting_screen_says_leaving_is_allowed_and_approves_nothing():
    section = PAGE[PAGE.index('<section id="draft-progress"'):PAGE.index("</main>")]
    assert 'class="draft-leave-note"' in section
    # The one quiet line under the card (board C1, 2026-09-21): the whole
    # of what leaving means, said once.
    assert "No need to wait &mdash; the draft lands on Plan when it&rsquo;s done." in section
    # Leaving while drafting has nothing left to save: the answers went
    # with "Draft my week", and the server keeps building on its own thread.
    assert "if (!$('draft-progress').hidden) return null;" in PAGE


# --------------------------------------------------------------------------
# 2. The draft on the Plan tab has its rare actions back
# --------------------------------------------------------------------------

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the screen's own functions"
)


def _extract(name: str, source: str) -> str:
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
    return source[start:j + 1]


def _review_root_html(status: str, root: bool) -> str:
    # The step's own function, with its neighbours stubbed: this test is
    # about what the root form adds around the views, not about the views.
    harness = (
        "var reviewState = { dayIndex: null };\n"
        "function weekSuggestedNoteHtml(){ return ''; }\n"
        "function reviewOpenIndex(){ return 0; }\n"
        "function wkDayMealCount(){ return 1; }\n"
        "function wkDayTabsHtml(){ return '<div class=\"wk-daytabs\"></div>'; }\n"
        "function wkDayCardHtml(){ return '<div class=\"wk-day-card\"></div>'; }\n"
        "function wkDotsHtml(){ return ''; }\n"
        "function wkHelpButtonHtml(){ return '<button id=\"wk-help\"></button>'; }\n"
        "function periodRangeLabel(){ return 'Sep 14–20'; }\n"
        "function reviewDecideHtml(d){ return d.status === 'draft' ? '<div class=\"wk-decide dock\">approve</div>' : ''; }\n"
        + _extract("escapeHtml", SHELL_JS) + "\n"
        + _extract("weekPlanState", SHELL_JS) + "\n"
        + _extract("weekReplacesNote", SHELL_JS) + "\n"
        + _extract("reviewStepHtml", SHELL_JS) + "\n"
        + "console.log(JSON.stringify(reviewStepHtml(%s, [{date:'2026-09-14'}], %s)));\n"
        % (json.dumps({"weekly_plan_id": 3, "status": status, "days": [{"date": "2026-09-14"}]}),
           "true" if root else "false")
    )
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


@_needs_node
def test_a_draft_root_offers_more_above_its_dock():
    """FAILS ON MAIN: the draft root rendered the two views and Approve, and
    nothing else — the More sheet's "Try again" and "Change my answers" rows
    existed but no button on a draft opened the sheet."""
    html = _review_root_html("draft", root=True)
    assert html.count('id="wk-more"') == 1
    assert "More ···" in html
    # Above the dock, like the week root's foot: a sticky strip's flow
    # position has to be the end of the screen.
    assert html.index('id="wk-more"') < html.index('class="wk-decide dock"')


@_needs_node
def test_the_deeper_approved_form_keeps_its_crumb_and_gets_no_foot():
    """One way back per screen: the approved week's "Check the week" step is
    reached FROM More and carries a crumb, so a second More on it would be
    the sheet offering itself."""
    html = _review_root_html("approved", root=False)
    assert 'data-wk-back="week"' in html
    assert 'id="wk-more"' not in html


def test_the_more_button_on_the_draft_is_wired_and_the_sheet_has_the_draft_rows():
    wiring = _extract("wireMealsStep", SHELL_JS)
    assert "var more = steps.querySelector('#wk-more');" in wiring
    assert "openMealsMoreSheet()" in wiring
    sheet = _extract("renderMealsMoreSheet", SHELL_JS)
    assert "hasPlan && data.status !== 'approved'" in sheet
    assert "'wk-more-try-again', 'Try again'" in sheet
    assert "'wk-more-change', 'Change my answers'" in sheet


def test_the_tab_bar_is_never_hidden_by_the_plan_tab():
    """The shell's way out of any branch is the tab bar, and nothing on the
    Plan tab may take it away — the draft and All set included."""
    assert "tabBarEl.hidden" not in SHELL_JS
    assert re.search(r"#tab-bar\b[^{]*\{[^}]*display:\s*none", (REPO / "static" / "shell.css").read_text()) is None


# --------------------------------------------------------------------------
# 3. Leaving never approves a week
# --------------------------------------------------------------------------

def _next_monday() -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7)).isoformat()


def _full_week(start: str, meal: str = "Bean Chili") -> list[dict]:
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False, "reasoning": "fits"}
        for day in tools.period_dates(start, 7)
        for slot in tools.WEEK_SLOTS
    ]


def _plans() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT id, status, week_start_date FROM weekly_plans WHERE household_id = 1 ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count(table: str) -> int:
    conn = get_conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE household_id = 1").fetchone()[0]
    conn.close()
    return n


@pytest.fixture
def recipe():
    tools.add_recipe("Bean Chili", ingredients=[{"item": "black beans", "qty": "2 tins"}],
                     prep_time_minutes=10, cook_time_minutes=20)


def _leave_for_the_rest_of_the_app(signed_in):
    """What the crumb does, and what the tabs do: plain reads, no writes."""
    assert signed_in.get("/week").status_code == 200
    assert signed_in.get("/api/week-menu").status_code == 200       # Plan
    assert signed_in.get("/api/today/moves").status_code == 200     # Now
    assert signed_in.get("/api/grocery-list").status_code == 200    # Shop
    assert signed_in.get("/api/cooker-view").status_code == 200     # Cook


def test_leaving_the_questions_keeps_the_answers_and_makes_no_plan(signed_in):
    week = _next_monday()
    thursday = tools.period_dates(week, 7)[3]
    # The first screen's answers, written the way leaveFlow writes them.
    res = signed_in.post(f"/api/week/{week}/intake", json={
        "night_tags": {thursday: ["out"]}, "guest_counts": {}, "created_by": "Emily", "day_count": 7,
    })
    assert res.status_code == 200

    _leave_for_the_rest_of_the_app(signed_in)

    assert _plans() == []                       # nothing drafted, nothing approved
    assert _count("grocery_items") == 0
    # Coming back carries on from here.
    prefill = signed_in.get(f"/api/week/{week}/intake").json()
    assert prefill["in_flight"] is True
    assert prefill["plan_exists"] is False
    assert prefill["intake"]["night_tags"] == {thursday: ["out"]}


def test_leaving_a_draft_leaves_it_a_draft_with_nothing_bought_or_scheduled(signed_in, recipe, monkeypatch):
    week = _next_monday()
    saved = tools.save_week_intake(week, night_tags={}, guest_counts={}, moods=["Comfort food"], day_count=7)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: _full_week(week))
    draft = agent.generate_weekly_plan(week, intake_id=saved["intake_id"], day_count=7, period_start=week)

    # The household walks off to Now, Shop and Cook, and back to Plan.
    _leave_for_the_rest_of_the_app(signed_in)
    for _ in range(2):
        _leave_for_the_rest_of_the_app(signed_in)

    plans = _plans()
    assert [p["status"] for p in plans] == ["draft"]
    assert not [p for p in plans if p["status"] == "approved"]
    assert _count("grocery_items") == 0          # a draft is not a yes
    assert _count("prep_tasks") == 0             # nothing scheduled either
    # Plan shows the draft, and the way back in is prefilled with the
    # answers that made it — "continue or start again", not "approve".
    menu = signed_in.get(f"/api/week-menu?weekly_plan_id={draft['weekly_plan_id']}").json()
    assert menu["status"] == "draft"
    assert menu["weekly_plan_id"] == draft["weekly_plan_id"]
    prefill = signed_in.get(f"/api/week/{week}/intake").json()
    assert prefill["plan_exists"] is True and prefill["plan_status"] == "draft"
    assert prefill["intake"]["moods"] == ["Comfort food"]


def test_starting_again_from_a_draft_replaces_it_and_still_approves_nothing(signed_in, recipe, monkeypatch):
    """"Try again" and "Change my answers" both end in a fresh draft. Neither
    may approve one on the way through."""
    week = _next_monday()
    saved = tools.save_week_intake(week, night_tags={}, guest_counts={}, day_count=7)
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: _full_week(week))
    first = agent.generate_weekly_plan(week, intake_id=saved["intake_id"], day_count=7, period_start=week)

    # Try again: the same answers through the plain endpoint the More sheet calls.
    res = signed_in.post(f"/api/week/{week}/generate", json={"intake_id": saved["intake_id"], "day_count": 7})
    assert res.status_code == 200
    second = res.json()
    assert second["weekly_plan_id"] != first["weekly_plan_id"]

    # Change my answers: a new revision, then the streaming endpoint the
    # question screens use.
    changed = signed_in.post(f"/api/week/{week}/intake", json={"moods": ["Something warm"], "day_count": 7}).json()
    res = signed_in.post(f"/api/week/{week}/generate/stream", json={"intake_id": changed["intake_id"], "day_count": 7})
    assert res.status_code == 200
    assert "event: done" in res.text

    statuses = [p["status"] for p in _plans()]
    assert "approved" not in statuses
    assert statuses.count("draft") == 1, "one live draft for the week, never a pile"
    assert _count("grocery_items") == 0
