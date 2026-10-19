"""
The first-open tour (Loop Board "Build the in-app tour (Option A)", Emily,
2026-10-18, branch overnight/first-open-tour).

Six stops right after "Here's your week." on the household's first draft,
ending back on it. Skippable, never shown again once done or skipped —
stored per adult on the server (the household with nobody picked), not in
the browser. "Show me around again" in Preferences replays it. No tour for a
household that already has an approved week. See app/tools/tour.py and
static/tour.js.
"""
from __future__ import annotations

import json
from pathlib import Path

import nodeharness
from fastapi.testclient import TestClient

from app import tools
from app.db import get_conn
from app.main import app

REPO = Path(__file__).resolve().parent.parent
TOUR_JS = (REPO / "static" / "tour.js").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


def _plan(status: str = "draft", approved: bool = False) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status, approved_at) "
        "VALUES (1, '2026-10-19', ?, " + ("datetime('now')" if approved else "NULL") + ")",
        (status,),
    )
    conn.commit()
    conn.close()


def _device(member_id: int | None = None) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False).status_code == 303
    if member_id is not None:
        assert c.post("/api/whoami/pick", json={"member_id": member_id}).status_code == 200
    return c


# ---------- the server's half: shows once, skip is for good ----------

def test_a_first_draft_shows_it_once_and_finishing_ends_it_on_every_device():
    _adult("Emily")
    _plan()
    phone = _device()
    assert phone.get("/api/tour").json()["show"] is True
    assert phone.post("/api/tour/done", json={"skipped": False}).json()["show"] is False
    laptop = _device()
    assert laptop.get("/api/tour").json() == {"show": False, "ended": "done", "has_approved_week": False}


def test_skip_stops_it_for_good_and_a_replay_can_not_rewrite_it():
    _adult("Emily")
    _plan()
    c = _device()
    assert c.post("/api/tour/done", json={"skipped": True}).json()["ended"] == "skipped"
    # A replay from Preferences finished to the end later: the first
    # ending is the one kept, and it still never starts on its own.
    assert c.post("/api/tour/done", json={"skipped": False}).json()["ended"] == "skipped"
    assert c.get("/api/tour").json()["show"] is False


def test_it_is_per_adult_the_other_adult_still_gets_it():
    emily, vineeth = _adult("Emily"), _adult("Vineeth")
    _plan()
    _device(emily).post("/api/tour/done", json={"skipped": True})
    assert _device(emily).get("/api/tour").json()["show"] is False
    assert _device(vineeth).get("/api/tour").json()["show"] is True


def test_nobody_picked_keeps_it_on_the_household():
    _adult("Emily"), _adult("Vineeth")
    _plan()
    c = _device()  # two adults, no "Who's this?" answer yet
    c.post("/api/tour/done", json={"skipped": True})
    conn = get_conn()
    row = conn.execute("SELECT tour_ended_how FROM households WHERE id = 1").fetchone()
    members = conn.execute("SELECT tour_ended_how FROM members").fetchall()
    conn.close()
    assert row["tour_ended_how"] == "skipped"
    assert {m["tour_ended_how"] for m in members} == {""}


def test_no_tour_on_a_household_that_already_has_an_approved_week():
    _adult("Emily")
    _plan("approved", approved=True)
    _plan()  # re-planning next week: a draft, but not their first
    assert _device().get("/api/tour").json() == {"show": False, "ended": "", "has_approved_week": True}


def test_a_week_approved_then_retired_still_counts():
    _adult("Emily")
    _plan("retired", approved=True)
    _plan()
    assert _device().get("/api/tour").json()["show"] is False


def test_approve_then_reopen_does_not_bring_it_back():
    # Reopening clears weekly_plans.approved_at; the household's own
    # first_approved_at is what remembers the yes.
    _adult("Emily")
    conn = get_conn()
    plan_id = conn.execute(
        "INSERT INTO weekly_plans (household_id, week_start_date, status) VALUES (1, '2026-10-19', 'draft')"
    ).lastrowid
    conn.commit()
    conn.close()
    c = _device()
    assert c.get("/api/tour").json()["show"] is True
    tools.approve_weekly_plan(plan_id)
    tools.reopen_weekly_plan(plan_id)
    conn = get_conn()
    row = conn.execute("SELECT status, approved_at FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    assert row["status"] == "draft" and row["approved_at"] is None
    assert c.get("/api/tour").json() == {"show": False, "ended": "", "has_approved_week": True}


# ---------- the shell's half ----------

def test_the_shell_loads_it_and_starts_it_only_on_a_draft_off_the_approve_hand_off():
    assert SHELL_HTML.index('src="/static/tour.js"') < SHELL_HTML.index('src="/static/shell.js"')
    assert "if (!afterApprove) maybeStartFirstTour();" in SHELL_JS
    fn = SHELL_JS[SHELL_JS.index("function maybeStartFirstTour()"):SHELL_JS.index("function replayTour()")]
    assert "weekPlanState(weekState.data) !== 'draft'" in fn
    assert "Api.json('/api/tour'" in fn and "tourBlocked()" in fn


def test_showing_an_already_built_plan_checks_too_but_only_one_start_per_load():
    # The second adult's welcome holds the tour off while Plan is built;
    # the check runs again whenever Plan is shown.
    assert "if (tab.week && panel.dataset.built && weekState.data && typeof maybeStartFirstTour === 'function') maybeStartFirstTour();" in SHELL_JS
    fn = SHELL_JS[SHELL_JS.index("function maybeStartFirstTour()"):SHELL_JS.index("function replayTour()")]
    assert "tourStartedThisLoad || tourAsking" in fn
    assert "tourStartedThisLoad = window.PomonaTour.start(" in fn


def test_show_me_around_again_lives_in_preferences_and_replays_it():
    assert "Show me around again" in SHELL_JS
    assert "if (what === 'tour') return replayTour();" in SHELL_JS
    fn = SHELL_JS[SHELL_JS.index("function replayTour()"):]
    fn = fn[:fn.index("\n  }\n")]
    assert "closePrefsSheet()" in fn and "window.PomonaTour.start(tourHost, { replay: true })" in fn


# ---------- tour.js itself, run under node with a small fake page ----------

FAKE_DOM = r"""
var handlers = {};
function El(id) {
  this.id = id || ''; this.hidden = false; this.textContent = ''; this.style = {};
  this.isConnected = true; this.offsetHeight = 150; this._on = {};
  var cls = {}; this.classList = {
    add: function (c) { cls[c] = 1; }, remove: function (c) { delete cls[c]; },
    contains: function (c) { return !!cls[c]; }
  };
  this._kids = {};
}
El.prototype.setAttribute = function () {};
El.prototype.addEventListener = function (t, f) { this._on[t] = f; };
El.prototype.click = function () { this._on.click && this._on.click({}); };
El.prototype.focus = function () { document.activeElement = this; };
El.prototype.querySelector = function (sel) {
  return this._kids[sel] || (this._kids[sel] = new El(sel.replace('#', '')));
};
El.prototype.closest = function () { return null; };
El.prototype.getClientRects = function () { return this._rect ? [1] : []; };
El.prototype.getBoundingClientRect = function () { return this._rect || { width: 0, height: 0 }; };
El.prototype.scrollIntoView = function () {};
var targets = {};
var created = [];
var document = {
  activeElement: null,
  body: { appendChild: function () {} },
  createElement: function () { var e = new El(); created.push(e); return e; },
  addEventListener: function (t, f) { handlers[t] = f; },
  querySelectorAll: function (sel) { return targets[sel] ? [targets[sel]] : []; }
};
var window = { innerHeight: 844, innerWidth: 390, addEventListener: function () {} };
function setTimeout(f) { f(); }
"""


def _run(body: str) -> dict:
    script = FAKE_DOM + TOUR_JS + "\nvar Tour = window.PomonaTour;\n" + body
    res = nodeharness.run_node(script)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_six_stops_at_most_with_the_copy_from_the_mockup():
    out = _run("""
      console.log(JSON.stringify(Tour.STOPS.map(function (s) { return [s.tab, s.title]; })));
    """)
    assert out == [
        ["week", "Swap it or tweak it"], ["week", "Move it, or pick it"], ["week", "Or just tell me"],
        ["grocery", "Shop"], ["kitchen", "Cook"], ["today", "Today"],
    ]
    mockup = (REPO / "mockups" / "first-open-tour.html").read_text(encoding="utf-8")
    for line in ("Swap gives you a few other dishes to choose from.", "Skip the tour", "Back to my week",
                 "Show me around again"):
        assert line in mockup


def test_next_through_every_stop_finishes_on_the_draft_and_esc_skips():
    out = _run("""
      var tabs = [], ended = [];
      var host = { goTab: function (k) { tabs.push(k); }, onEnd: function (s) { ended.push(s); } };
      Tour.start(host);
      var next = document.activeElement;   // focus moved into the card, on Next
      var focusedNext = next.id === 'tour-next';
      var labels = [];
      for (var i = 0; i < 6; i++) { labels.push(next.textContent); next.click(); }
      var done = { ended: ended.slice(), tabs: tabs.slice(), open: Tour.isOpen() };
      Tour.start(host);
      var esc = { key: 'Escape', preventDefault: function () {}, stopPropagation: function () {} };
      handlers.keydown(esc);
      console.log(JSON.stringify({ focusedNext: focusedNext, labels: labels, done: done,
        afterEsc: ended, openAfterEsc: Tour.isOpen() }));
    """)
    assert out["focusedNext"] is True
    assert out["labels"] == ["Next"] * 5 + ["Back to my week"]
    assert out["done"]["ended"] == [False] and out["done"]["open"] is False
    assert out["done"]["tabs"] == ["week", "week", "week", "grocery", "kitchen", "today", "week"]
    assert out["afterEsc"] == [False, True] and out["openAfterEsc"] is False


def test_a_stop_with_nothing_to_ring_still_reads_as_a_centred_card():
    # Stop 1 finds no Swap on screen (a week with no rows): the card keeps
    # its words and sits centred, no ring. Stop 2 finds Move: ringed, and
    # the card goes beside it.
    out = _run("""
      var move = new El('m');
      move._rect = { left: 300, top: 400, bottom: 444, right: 360, width: 60, height: 44 };
      targets['[data-wk-move]'] = move;
      Tour.start({ goTab: function () {}, onEnd: function () {} });
      var root = created[0], card = root.querySelector('#tour-card');
      var hole = root.querySelector('#tour-hole');
      var one = { centred: card.classList.contains('is-centred'), ring: !hole.classList.contains('is-empty'),
                  title: root.querySelector('#tour-title').textContent };
      document.activeElement.click();
      var two = { centred: card.classList.contains('is-centred'), ring: !hole.classList.contains('is-empty'),
                  top: card.style.top, holeTop: hole.style.top };
      console.log(JSON.stringify({ one: one, two: two }));
    """)
    assert out["one"] == {"centred": True, "ring": False, "title": "Swap it or tweak it"}
    assert out["two"]["centred"] is False and out["two"]["ring"] is True
    assert out["two"]["holeTop"] == "396px" and out["two"]["top"] == "460px"


def test_a_target_off_to_the_side_is_not_ringed_and_cook_reads_by_what_is_ringed():
    # Schedule's carousel: Saturday's Pick is laid out at x=2070. Not on
    # screen, so stop 2 rings Move instead. And Cook on a draft rings the
    # tab and says so.
    out = _run("""
      var pick = new El('p');
      pick._rect = { left: 2070, top: 400, bottom: 444, right: 2130, width: 60, height: 44 };
      targets['[data-wk-pick]'] = pick;
      var move = new El('m');
      move._rect = { left: 300, top: 500, bottom: 544, right: 360, width: 60, height: 44 };
      targets['[data-wk-move]'] = move;
      var tab = new El('t');
      tab._rect = { left: 290, top: 770, bottom: 840, right: 390, width: 100, height: 70 };
      targets['.tab-btn[data-tab="kitchen"]'] = tab;
      Tour.start({ goTab: function () {}, onEnd: function () {} });
      var root = created[0], next = document.activeElement;
      next.click();
      var holeTop = root.querySelector('#tour-hole').style.top;
      next.click(); next.click(); next.click();
      console.log(JSON.stringify({ holeTop: holeTop, cook: root.querySelector('#tour-body').textContent,
        ask: Tour.STOPS[2].body }));
    """)
    assert out["holeTop"] == "496px"
    assert out["cook"].startswith("Once the week’s approved, tonight’s dinner lives here")
    assert out["ask"].startswith("This is where you ask:") and "Tap here" not in out["ask"]
