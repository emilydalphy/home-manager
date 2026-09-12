"""
Plan gets a Meals | Chores toggle showing what's due.

Loop Board "Chores v1: Plan gets a Meals | Chores toggle showing what's
due" (Emily, 2026-09-11 — a grouped LIST, not a week grid: "today, this
week, and then the other ones would likely become monthly, bi-monthly, or
semi-annually"). As one of the adults running the house, open Plan, flip
to Chores, and see what needs doing, who has it, and what's already done,
so neither of you has to hold the list in your head or be the one who
brings it up.

Backend half over the real route (/api/chores/pending) and
tools.get_chores_pending; front-end half runs the shell's own builders
under node (tests/nodeharness.py) — the step machine, the control, the
list, the tick, the refresh wiring — with the DOM faked to the few things
each builder touches. A handful of source-marker tests remain where the
claim IS about the source (the control is .wk-seg; the CSS is tokens
only). Every behavioural test here fails on main before the toggle.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import nodeharness
import pytest

from app import tools
from app.db import get_conn
from app.tools import chores as chores_mod

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

TODAY = datetime.date.today()


def _d(days: int) -> str:
    return (TODAY + datetime.timedelta(days=days)).isoformat()


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


def _house_on():
    _adult("Emily")
    _adult("Vineeth")
    tools.set_chores_enabled(True)


def _rows(body) -> dict:
    return {c["chore"]: c for c in body["chores"]}


# ==========================================================================
# 1. The read: /api/chores/pending
# ==========================================================================

def test_the_route_answers_empty_and_says_so_while_off(signed_in):
    """Same contract as /api/chores/today: 200, empty, enabled false — never a 4xx."""
    _adult("Emily")
    tools.add_chore("Bins", owner_name="Emily")
    tools.schedule_chore_instance("Bins", _d(0))
    res = signed_in.get("/api/chores/pending")
    assert res.status_code == 200
    assert res.json() == {"chores": [], "chores_set_up": False, "enabled": False}


def test_every_chore_is_one_row_grouped_today_week_later(signed_in):
    """
    The list: one row per CHORE (a daily chore's fortnight of rows is one
    line), each under today / week / later, with the chore's rhythm on it
    so the shell can head the later ones by frequency.
    """
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Bins", _d(0))
    tools.add_chore("Wipe counters", frequency="daily", mode="whoever")
    for i in range(14):
        tools.schedule_chore_instance("Wipe counters", _d(i))
    tools.add_chore("Filters", frequency="monthly", owner_name="Vineeth")
    tools.schedule_chore_instance("Filters", _d(20))
    tools.add_chore("Gutters", frequency="quarterly", mode="shared", assignee_names=["Emily", "Vineeth"])
    tools.schedule_chore_instance("Gutters", _d(60))

    body = signed_in.get("/api/chores/pending").json()
    assert body["enabled"] is True and body["chores_set_up"] is True
    names = [c["chore"] for c in body["chores"]]
    assert names.count("Wipe counters") == 1, "a daily chore is one line, not fourteen"
    rows = _rows(body)
    assert rows["Bins"]["group"] == "today" and rows["Bins"]["frequency"] == "weekly"
    assert rows["Wipe counters"]["group"] == "today" and rows["Wipe counters"]["due_date"] == _d(0)
    assert rows["Filters"]["group"] == "later" and rows["Filters"]["frequency"] == "monthly"
    assert rows["Gutters"]["group"] == "later" and rows["Gutters"]["frequency"] == "quarterly"
    # Who has it, composed by the server — the shell never recomputes it.
    assert rows["Bins"]["who_label"] == "Emily"
    assert rows["Wipe counters"]["who_label"] == "either of you"
    assert rows["Gutters"]["who_label"] in ("Emily", "Vineeth")
    # The screen names the week it groups by.
    assert body["week_start"] <= _d(0) <= body["week_end"]
    assert body["week_label"]
    assert (datetime.date.fromisoformat(body["week_end"]) - datetime.date.fromisoformat(body["week_start"])).days == 6


def test_this_week_is_the_households_week_and_beyond_it_is_later(signed_in):
    """
    'week' is the rest of THIS household's week (suggest_planning_period
    with plan_ahead off — the Friday shift that makes an unplanned meal
    week "next week" has no meaning for the bins); the day after it is
    'later', whatever its rhythm.
    """
    _house_on()
    period = tools.suggest_planning_period(plan_ahead=False)
    week_end = datetime.date.fromisoformat(period["start_date"]) + datetime.timedelta(days=6)
    body = signed_in.get("/api/chores/pending").json()
    assert body["week_start"] == period["start_date"]
    assert body["week_end"] == week_end.isoformat()
    assert body["week_label"] == period["label"]

    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    tools.add_chore("Dust", frequency="weekly", owner_name="Vineeth")
    inside = week_end
    beyond = week_end + datetime.timedelta(days=1)
    if inside == TODAY:
        # The last day of the week: nothing can be inside it but today.
        tools.schedule_chore_instance("Hoover", inside.isoformat())
        tools.schedule_chore_instance("Dust", beyond.isoformat())
        rows = _rows(signed_in.get("/api/chores/pending").json())
        assert rows["Hoover"]["group"] == "today"
        assert rows["Dust"]["group"] == "later"
    else:
        tools.schedule_chore_instance("Hoover", inside.isoformat())
        tools.schedule_chore_instance("Dust", beyond.isoformat())
        rows = _rows(signed_in.get("/api/chores/pending").json())
        assert rows["Hoover"]["group"] == "week"
        assert rows["Dust"]["group"] == "later"


def test_the_chores_week_falls_back_to_a_monday_week(monkeypatch):
    """A three-day 'as we go' meal horizon is not a week; the bins live in one."""
    wednesday = datetime.date(2026, 9, 16)
    monkeypatch.setattr(
        chores_mod._weekly_plan, "suggest_planning_period",
        lambda **kw: {"start_date": "2026-09-16", "day_count": 3, "label": "Sep 16–18"},
    )
    assert chores_mod._chores_week(wednesday) == (datetime.date(2026, 9, 14), datetime.date(2026, 9, 20))
    monkeypatch.setattr(
        chores_mod._weekly_plan, "suggest_planning_period",
        lambda **kw: {"start_date": "2026-09-12", "day_count": 7, "label": "Sep 12–18"},
    )
    assert chores_mod._chores_week(wednesday) == (datetime.date(2026, 9, 12), datetime.date(2026, 9, 18))


def test_a_slipped_chore_is_one_row_under_today(signed_in):
    """The no-guilt-pile collapse, on this list: once, due, nothing about how long."""
    _house_on()
    tools.add_chore("Mop", frequency="weekly", owner_name="Emily")
    for days_ago in (21, 14, 7):
        tools.schedule_chore_instance("Mop", _d(-days_ago))
    body = signed_in.get("/api/chores/pending").json()
    mops = [c for c in body["chores"] if c["chore"] == "Mop"]
    assert len(mops) == 1
    assert mops[0]["group"] == "today" and mops[0]["status"] == "pending"
    assert mops[0]["stands_for"] == 3
    text = json.dumps(body).lower()
    for banned in ("overdue", "missed", "days late", "behind"):
        assert banned not in text


def test_within_a_group_the_most_due_comes_first_and_done_sinks(signed_in):
    """
    _due_order_key is the ONE ordering rule: still-to-do before done today,
    the longest waiting first, soonest first in a future group.
    """
    _house_on()
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Hoover", _d(0))
    tools.add_chore("Mop", frequency="weekly", owner_name="Vineeth")
    tools.schedule_chore_instance("Mop", _d(-10))
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    bins = tools.schedule_chore_instance("Bins", _d(-3))["instance_id"]
    tools.set_chore_instance_status(bins, "done")
    tools.add_chore("Gutters", frequency="quarterly", owner_name="Emily")
    tools.schedule_chore_instance("Gutters", _d(70))
    tools.add_chore("Filters", frequency="monthly", owner_name="Emily")
    tools.schedule_chore_instance("Filters", _d(25))

    body = signed_in.get("/api/chores/pending").json()
    today = [c["chore"] for c in body["chores"] if c["group"] == "today"]
    assert today == ["Mop", "Hoover", "Bins"], "longest waiting first, done today last"
    later = [c["chore"] for c in body["chores"] if c["group"] == "later"]
    assert later == ["Filters", "Gutters"], "soonest first"
    # The key itself, so a change to the rule is a change in one place.
    assert chores_mod._due_order_key({"status": "pending", "due_date": "2026-01-01", "id": 9}) < \
        chores_mod._due_order_key({"status": "pending", "due_date": "2026-01-02", "id": 1})
    assert chores_mod._due_order_key({"status": "done", "due_date": "2025-01-01", "id": 1}) > \
        chores_mod._due_order_key({"status": "pending", "due_date": "2026-06-01", "id": 1})


def test_a_tick_stays_visible_ticked_for_the_day_and_once(signed_in):
    """
    Done today wins the chore's one row: the tick keeps its place under
    Today, ticked, and the next occurrence the tick wrote does not appear
    beside it as a second line. Untick lands back on the same row.
    """
    _house_on()
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    bins = tools.schedule_chore_instance("Bins", _d(0))["instance_id"]
    assert signed_in.post(f"/api/chores/{bins}/status", json={"status": "done"}).status_code == 200
    body = signed_in.get("/api/chores/pending").json()
    rows = [c for c in body["chores"] if c["chore"] == "Bins"]
    assert len(rows) == 1
    assert rows[0]["id"] == bins and rows[0]["status"] == "done" and rows[0]["group"] == "today"
    # The tick did write next week's row — it just isn't shown twice.
    conn = get_conn()
    pending = conn.execute("SELECT COUNT(*) FROM chore_instances WHERE status = 'pending'").fetchone()[0]
    conn.close()
    assert pending == 1
    assert signed_in.post(f"/api/chores/{bins}/status", json={"status": "pending"}).status_code == 200
    rows = [c for c in signed_in.get("/api/chores/pending").json()["chores"] if c["chore"] == "Bins"]
    assert len(rows) == 1 and rows[0]["id"] == bins and rows[0]["status"] == "pending"


def test_an_outsourced_row_says_who_and_cannot_be_ticked(signed_in):
    _house_on()
    tools.add_chore("Bathrooms", frequency="weekly", mode="outsourced", outsourced_to="Maria")
    tools.schedule_chore_instance("Bathrooms", _d(0))
    row = _rows(signed_in.get("/api/chores/pending").json())["Bathrooms"]
    assert row["outsourced"] is True and row["completable"] is False
    assert row["who_label"] == "Maria"


def test_a_chore_with_no_row_gets_its_next_one_rather_than_falling_off(signed_in):
    """
    Added in chat with no schedule generated after (or its one row skipped
    from the card): one row, dated by the same _next_due_date everything
    else uses — and only where there was NO pending row, so a household
    with a schedule is left exactly as it was.
    """
    _house_on()
    tools.add_chore("Filters", frequency="monthly", owner_name="Vineeth")   # never scheduled
    tools.add_chore("Bins", frequency="weekly", owner_name="Emily")
    tools.schedule_chore_instance("Bins", _d(2))
    tools.add_chore("Hoover", frequency="weekly", owner_name="Emily")
    skipped = tools.schedule_chore_instance("Hoover", _d(-1))["instance_id"]
    tools.set_chore_instance_status(skipped, "skipped")
    tools.add_chore("Old", frequency="weekly", owner_name="Emily")
    tools.update_chore(tools.list_chore_definitions()[-1]["id"], active=False)
    tools.add_chore("Paint the fence", frequency="once", owner_name="Emily")   # never dated: nothing to invent

    body = signed_in.get("/api/chores/pending").json()
    rows = _rows(body)
    assert set(rows) == {"Filters", "Bins", "Hoover"}
    assert rows["Filters"]["due_date"] == _d(0), "never scheduled starts today, as generate_chore_schedule would"
    assert rows["Bins"]["due_date"] == _d(2), "a chore with a row is untouched"
    assert rows["Hoover"]["due_date"] >= _d(0)
    conn = get_conn()
    counts = {r["name"]: r["n"] for r in conn.execute(
        "SELECT c.name, COUNT(ci.id) AS n FROM chores c LEFT JOIN chore_instances ci "
        "ON ci.chore_id = c.id AND ci.status = 'pending' GROUP BY c.id"
    )}
    conn.close()
    assert counts["Filters"] == 1 and counts["Bins"] == 1 and counts["Hoover"] == 1
    assert counts["Old"] == 0 and counts["Paint the fence"] == 0
    # Read again: nothing more is written.
    signed_in.get("/api/chores/pending")
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM chore_instances WHERE status = 'pending'").fetchone()[0]
    conn.close()
    assert total == 3


def test_a_house_that_never_set_chores_up_is_told_so(signed_in):
    _house_on()
    body = signed_in.get("/api/chores/pending").json()
    assert body["chores"] == [] and body["chores_set_up"] is False and body["enabled"] is True


# ==========================================================================
# 2. The shell — run under node
# ==========================================================================

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own builders"
)


def _function(name: str) -> str:
    """The body of one top-level function in shell.js, to its closing brace."""
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _var(name: str, end_marker: str = "\n  };\n") -> str:
    start = SHELL_JS.index("  var %s =" % name)
    end = SHELL_JS.index(end_marker, start)
    return SHELL_JS[start:end] + end_marker


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# Everything the Chores state's builders lean on, real where the claim is
# about them and stubbed where it is about the rest of the Plan tab.
def _prelude() -> str:
    labels = SHELL_JS[SHELL_JS.index("  var CHORE_GROUP_LABELS ="):SHELL_JS.index("  // The band on the Chores state")]
    return """
function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
var shellWho = { member: null, adults: [], chores_enabled: true, loaded: false };
var panels = {};
var scrollEl = null;
var TOASTS = [];
function showToast(m) { TOASTS.push(m); }
function dayName(dateStr, opts) { return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', opts); }
var LOADS = [];
function loadChores(p) { LOADS.push('now'); }
// The rest of the Plan tab, as far as renderMealsStep reaches into it.
function mealsCurrentDay() { return null; }
function daySlotEntry() { return null; }
function weekPlanState(data) { return (data && data.state) || 'none'; }
function weekBandData(data) { return data; }
function weekBandParts(data, days) { return { id: 'week-band', eyebrow: 'Sep 7–13', title: 'This week', sub: 'a draft, your turn', badge: 'Draft' }; }
function rootBandHtml(parts) { return '<header class="root-band" data-parts=\\'' + JSON.stringify(parts) + '\\'></header>'; }
function weekStepHtml() { return '<div class="wk-week-card">MEALS</div>'; }
function renderPlanWeekEntry() {}
function wireMealsStep() {}
function reviewStepHtml() { return '<div>REVIEW</div>'; }
function mealStepHtml() { return ''; }
function dayStepHtml() { return ''; }
function allSetStepHtml() { return ''; }
function renderAllSetAsks() {}
function ensureCookDataForMeals() {}
""" + _var("TICK_ICON", ";\n") + _var("EMPTY_ICONS") + _var("weekState") + labels + \
        _function("choresEnabled") + _function("emptyMomentHtml") + _function("choreRowHtml") + \
        _function("planChoresBandParts") + _function("planModeSegHtml") + _function("renderPlanModeSeg") + \
        _function("planChoreWhen") + _function("planChoresStepHtml") + _function("planChoresByRhythmHtml") + \
        _function("wirePlanChores") + _function("togglePlanChore") + _function("renderMealsStep") + \
        _function("mealsStepHistoryState") + _function("pushMealsStepHistory") + \
        _function("replaceMealsStepHistory") + _function("goMealsStep") + _function("applyMealsStepFromHistory")


# A panel whose slots remember their markup, and whose #week-steps can
# find the rows and the setup button back inside what was written to it.
def _dom() -> str:
    return """
var HISTORY = [];
var window = { history: {
  pushState: function (s, t, u) { HISTORY.push(['push', s.mealsStep]); },
  replaceState: function (s, t, u) { HISTORY.push(['replace', s.mealsStep]); }
}, location: { href: '' } };
function el() {
  var e = { innerHTML: '', hidden: false, dataset: {}, textContent: '', handlers: {}, clicks: {} };
  e.querySelector = function (sel) {
    if (sel === '#pc-setup' && e.innerHTML.indexOf('id="pc-setup"') !== -1) {
      return { addEventListener: function (_t, fn) { e.clicks.setup = fn; } };
    }
    return null;
  };
  e.querySelectorAll = function (sel) {
    var out = [];
    if (sel === '[data-plan-mode]') {
      var re = /data-plan-mode="(\\w+)"/g, m;
      while ((m = re.exec(e.innerHTML)) !== null) {
        (function (mode) {
          out.push({ getAttribute: function () { return mode; },
                     addEventListener: function (_t, fn) { e.clicks[mode] = fn; } });
        })(m[1]);
      }
    } else if (sel === '.chore-row:not(.is-outsourced)') {
      var re2 = /<div class="chore-row(?! is-outsourced)[^"]*" data-id="(\\d+)">/g, m2;
      while ((m2 = re2.exec(e.innerHTML)) !== null) {
        (function (id) {
          out.push({ dataset: { id: String(id) },
                     querySelector: function () { return { addEventListener: function (_t, fn) { e.handlers[id] = fn; } }; } });
        })(Number(m2[1]));
      }
    }
    return out;
  };
  return e;
}
function makePanel() {
  var slots = { '#week-steps': el(), '#week-approve-row': el(), '#week-band-slot': el(), '#week-mode-slot': el() };
  var panel = { dataset: { built: '1' }, classes: {}, slots: slots };
  panel.classList = { toggle: function (c, on) { panel.classes[c] = !!on; } };
  panel.querySelector = function (sel) { return slots[sel] || null; };
  panels.week = panel;
  return panel;
}
function bandParts(panel) { return JSON.parse(/data-parts='([^']*)'/.exec(panel.slots['#week-band-slot'].innerHTML)[1]); }
"""


_LIST = {
    "chores": [
        {"id": 11, "chore": "Bins", "status": "pending", "who_label": "Emily", "outsourced": False, "completable": True,
         "due_date": "2026-09-12", "frequency": "weekly", "group": "today", "stands_for": 3},
        {"id": 12, "chore": "Kitchen floor", "status": "done", "who_label": "Vineeth", "outsourced": False, "completable": True,
         "due_date": "2026-09-12", "frequency": "weekly", "group": "today", "stands_for": 1},
        {"id": 13, "chore": "Bathrooms", "status": "pending", "who_label": "Maria", "outsourced": True, "completable": False,
         "due_date": "2026-09-15", "frequency": "weekly", "group": "week", "stands_for": 1},
        {"id": 14, "chore": "Hoover", "status": "pending", "who_label": "Either of you", "outsourced": False, "completable": True,
         "due_date": "2026-09-17", "frequency": "weekly", "group": "week", "stands_for": 1},
        {"id": 15, "chore": "Gutters", "status": "pending", "who_label": "Vineeth", "outsourced": False, "completable": True,
         "due_date": "2026-12-01", "frequency": "quarterly", "group": "later", "stands_for": 1},
        {"id": 16, "chore": "Filters", "status": "pending", "who_label": "Emily", "outsourced": False, "completable": True,
         "due_date": "2026-10-02", "frequency": "monthly", "group": "later", "stands_for": 1},
        {"id": 17, "chore": "Sheets", "status": "pending", "who_label": "Emily", "outsourced": False, "completable": True,
         "due_date": "2026-09-24", "frequency": "biweekly", "group": "later", "stands_for": 1},
    ],
    "week_start": "2026-09-07", "week_end": "2026-09-13", "week_label": "Sep 7–13",
    "chores_set_up": True, "enabled": True,
}


@_needs_node
def test_with_the_switch_off_plan_is_exactly_as_it_was():
    out = _node(_prelude() + _dom() + """
shellWho.chores_enabled = false;
var panel = makePanel();
weekState.data = { state: 'set' };
weekState.step = 'chores';           // left in history from before the switch went off
renderMealsStep(panel);
console.log(JSON.stringify({ step: weekState.step, slotHidden: panel.slots['#week-mode-slot'].hidden,
  slot: panel.slots['#week-mode-slot'].innerHTML, steps: panel.slots['#week-steps'].innerHTML, band: bandParts(panel) }));
""")
    assert out["step"] == "week", "a chores step with the switch off folds back to the week"
    assert out["slotHidden"] is True and out["slot"] == ""
    assert "MEALS" in out["steps"]
    assert out["band"]["badge"] == "Draft" and out["band"]["sub"] == "a draft, your turn"


@_needs_node
def test_with_the_switch_on_the_control_and_the_state_are_there():
    out = _node(_prelude() + _dom() + """
var panel = makePanel();
weekState.data = { state: 'set' };
weekState.chores = %s;
renderMealsStep(panel);
var onMeals = { slotHidden: panel.slots['#week-mode-slot'].hidden, slot: panel.slots['#week-mode-slot'].innerHTML,
  steps: panel.slots['#week-steps'].innerHTML, band: bandParts(panel), approveHidden: panel.slots['#week-approve-row'].hidden,
  fills: !!panel.classes['is-chores'] };
weekState.step = 'chores';
renderMealsStep(panel);
var onChores = { slotHidden: panel.slots['#week-mode-slot'].hidden, slot: panel.slots['#week-mode-slot'].innerHTML,
  steps: panel.slots['#week-steps'].innerHTML, band: bandParts(panel), approveHidden: panel.slots['#week-approve-row'].hidden,
  bandHidden: panel.slots['#week-band-slot'].hidden, fills: panel.classes['is-chores'], mealsFilled: onMeals.fills };
onMeals.fills = false;
weekState.step = 'review';           // a deeper step (the approved week's check)
renderMealsStep(panel);
var onDay = { slotHidden: panel.slots['#week-mode-slot'].hidden, bandHidden: panel.slots['#week-band-slot'].hidden };
console.log(JSON.stringify({ onMeals: onMeals, onChores: onChores, onDay: onDay }));
""" % json.dumps(_LIST))
    meals, chores, day = out["onMeals"], out["onChores"], out["onDay"]
    # The control: .wk-seg, two words, no icons, Meals selected.
    assert meals["slotHidden"] is False
    assert 'class="wk-seg plan-mode-seg"' in meals["slot"]
    assert meals["slot"].count("wk-seg-btn") == 2 and "<svg" not in meals["slot"]
    assert re.search(r'class="wk-seg-btn is-on"[^>]*data-plan-mode="meals">Meals<', meals["slot"])
    assert re.search(r'class="wk-seg-btn"[^>]*data-plan-mode="chores">Chores<', meals["slot"])
    assert "MEALS" in meals["steps"] and meals["approveHidden"] is False
    # Meals' band is untouched.
    assert meals["band"] == {"id": "week-band", "eyebrow": "Sep 7–13", "title": "This week", "sub": "a draft, your turn", "badge": "Draft"}
    # Chores: same frame — band shown, gear with it — the meal plan's chip
    # and line gone, the control flipped, no crumb and no head of its own.
    assert chores["bandHidden"] is False and chores["slotHidden"] is False
    assert chores["band"] == {"id": "week-band", "eyebrow": "Sep 7–13", "title": "This week", "sub": "", "badge": ""}
    assert re.search(r'class="wk-seg-btn is-on"[^>]*data-plan-mode="chores">Chores<', chores["slot"])
    assert chores["approveHidden"] is True
    assert "crumb" not in chores["steps"] and "wk-head" not in chores["steps"] and "MEALS" not in chores["steps"]
    assert 'data-pc-group="today"' in chores["steps"]
    # Only the Chores state grows the plan view (.is-chores) — Meals' layout is untouched.
    assert chores["fills"] is True and chores["mealsFilled"] is False
    assert ".tab-panel.is-chores #week-plan-view { flex: 1 0 auto; }" in SHELL_CSS
    # Deeper steps keep neither the band nor the control.
    assert day["slotHidden"] is True and day["bandHidden"] is True


@_needs_node
def test_chores_is_a_step_of_the_plan_tab_with_history_and_the_back_gesture():
    out = _node(_prelude() + _dom() + """
var FETCHES = [];
function loadPlanChores(p) { FETCHES.push(weekState.step); }
var panel = makePanel();
weekState.data = { state: 'set' };
weekState.chores = %s;
renderMealsStep(panel);
var slot = panel.slots['#week-mode-slot'];
slot.clicks.chores();                          // Meals → Chores
var afterTap = { step: weekState.step, history: HISTORY.slice(), on: /is-on"[^>]*data-plan-mode="chores"/.test(slot.innerHTML) };
slot.clicks.meals();                           // Chores → Meals
var afterBack = { step: weekState.step, history: HISTORY.slice() };
applyMealsStepFromHistory({ tab: 'week', mealsStep: 'chores' });   // the back gesture landing on a chores entry
var fromHistory = { step: weekState.step, steps: panel.slots['#week-steps'].innerHTML };
applyMealsStepFromHistory({ tab: 'week', mealsStep: 'week' });
console.log(JSON.stringify({ afterTap: afterTap, afterBack: afterBack, fromHistory: fromHistory, finalStep: weekState.step, fetches: FETCHES }));
""" % json.dumps(_LIST))
    assert out["afterTap"]["step"] == "chores" and out["afterTap"]["on"] is True
    assert out["afterTap"]["history"] == [["push", "chores"]], "Meals → Chores pushes one entry, so Back returns to Meals"
    assert out["afterBack"]["step"] == "week"
    assert out["afterBack"]["history"] == [["push", "chores"], ["replace", "week"]], "Chores → Meals replaces, never stacks"
    assert out["fromHistory"]["step"] == "chores" and 'data-pc-group="today"' in out["fromHistory"]["steps"]
    assert out["finalStep"] == "week"
    # Arriving on Chores reads again, quietly; leaving it does not.
    assert out["fetches"] == ["chores", "chores"]


@_needs_node
def test_the_list_is_grouped_by_when_then_by_rhythm_and_says_who():
    out = _node(_prelude() + _dom() + """
weekState.chores = %s;
console.log(JSON.stringify({ html: planChoresStepHtml() }));
""" % json.dumps(_LIST))
    html = out["html"]
    # The three groups, in order, headed in the household's words.
    groups = re.findall(r'data-pc-group="(\w+)"', html)
    assert groups == ["today", "week", "later"]
    labels = re.findall(r'<div class="rv-group-label">([^<]*)</div>', html)
    assert labels == ["Today", "This week", "Coming up"]
    # Coming up breaks down by rhythm, shortest rhythm first.
    rhythms = re.findall(r'data-pc-rhythm="(\w+)"', html)
    assert rhythms == ["biweekly", "monthly", "quarterly"]
    sub = re.findall(r'<div class="pc-rhythm-label">([^<]*)</div>', html)
    assert sub == ["Every two weeks", "Every month", "Every few months"]
    # Server order kept inside each group (no re-sorting in the browser).
    names = re.findall(r'<span class="chore-name">([^<]*)</span>', html)
    assert names == ["Bins", "Kitchen floor", "Bathrooms", "Hoover", "Sheets", "Filters", "Gutters"]
    # Who has it, as the server said it; the day word only off today.
    assert '<span class="chore-who">Emily</span>' in html
    assert '<span class="chore-who">Either of you · Thursday</span>' in html
    assert '<span class="chore-who">Emily · Oct 2</span>' in html
    assert '<span class="chore-who">Vineeth · Dec 1</span>' in html
    # A slipped chore is one row, and its stands_for is never printed.
    text = re.sub(r"<[^>]+>", " ", html)
    assert html.count("Bins") == 1 and "stands_for" not in html and " 3 " not in text and "3 " not in text.strip()
    # Done today is drawn done, in place.
    assert re.search(r'chore-row done" data-id="12"', html)
    # The rows are Now's rows: same classes, same tick, same tag.
    assert html.count('class="tick chore-tick"') == 5
    assert html.count('class="tick chore-tick is-done"') == 1
    # No dock and no apricot when there are rows — ticking is the action.
    assert "dock" not in html and "dock-primary" not in html and "apricot" not in html
    text = re.sub(r"<[^>]+>", " ", html).lower()
    for banned in ("overdue", "missed", "late", "behind"):
        assert not re.search(r"\b%s\b" % banned, text), banned


@_needs_node
def test_an_outsourced_row_has_the_tag_and_no_tick():
    out = _node(_prelude() + _dom() + """
weekState.chores = %s;
var steps = el();
steps.innerHTML = planChoresStepHtml();
wirePlanChores(makePanel(), steps);
console.log(JSON.stringify({ html: steps.innerHTML, handlers: Object.keys(steps.handlers) }));
""" % json.dumps(_LIST))
    html = out["html"]
    row = html[html.index('chore-row is-outsourced'):]
    row = row[:row.index("</div>") + 6]
    assert '<span class="pill pill-neutral chore-tag">Not us</span>' in row
    assert "Maria" in row and "Tuesday" in row
    assert "chore-tick" not in row and "<button" not in row
    assert 'class="tick tick-empty"' in row
    # Handlers only on rows with a tick: never the outsourced one.
    assert sorted(out["handlers"]) == ["11", "12", "14", "15", "16", "17"]


@_needs_node
def test_a_tick_is_done_in_place_and_a_failed_save_puts_it_back_and_says_so():
    out = _node(_prelude() + _dom() + """
var POSTS = [];
var FAIL_NEXT = false;
function fetch(url, opts) {
  POSTS.push({ url: url, body: JSON.parse(opts.body) });
  if (FAIL_NEXT) { FAIL_NEXT = false; return Promise.resolve({ ok: false }); }
  return Promise.resolve({ ok: true });
}
panels.today = { dataset: { built: '1' } };
var panel = makePanel();
weekState.data = { state: 'set' };
weekState.chores = %s;
weekState.step = 'chores';
renderMealsStep(panel);
var steps = panel.slots['#week-steps'];
function state() {
  var names = steps.innerHTML.match(/<span class="chore-name">[^<]*<\\/span>/g).map(function (s) { return s.replace(/<[^>]+>/g, ''); });
  return { binsDone: /chore-row done" data-id="11"/.test(steps.innerHTML), order: names.slice(0, 2) };
}
var snaps = [];
steps.handlers['11']();                         // tick Bins
snaps.push(state());                            // before the save lands
Promise.resolve().then(function () { return Promise.resolve(); }).then(function () {
  snaps.push(state());
  FAIL_NEXT = true;
  steps.handlers['11']();                       // untick, and the save fails
  return Promise.resolve().then(function () { return Promise.resolve(); }).then(function () { return Promise.resolve(); });
}).then(function () {
  snaps.push(state());
  console.log(JSON.stringify({ posts: POSTS, snaps: snaps, toasts: TOASTS, loads: LOADS }));
});
""" % json.dumps(_LIST))
    assert out["posts"][0] == {"url": "/api/chores/11/status", "body": {"status": "done"}}
    assert out["snaps"][0] == {"binsDone": True, "order": ["Bins", "Kitchen floor"]}, "flipped before the server answers, and not moved"
    assert out["snaps"][1] == {"binsDone": True, "order": ["Bins", "Kitchen floor"]}
    # The failed untick is put back — still done — and said so.
    assert out["posts"][1] == {"url": "/api/chores/11/status", "body": {"status": "pending"}}
    assert out["snaps"][2]["binsDone"] is True
    assert out["toasts"] == ["That didn’t save. Try it again in a moment."]
    # The successful tick told Now's card; the failed one did not.
    assert out["loads"] == ["now"]


@_needs_node
def test_the_two_empty_states_and_the_one_dock():
    out = _node(_prelude() + _dom() + """
weekState.chores = { chores: [], chores_set_up: false, enabled: true, week_label: 'Sep 7–13' };
var fresh = el(); fresh.innerHTML = planChoresStepHtml();
wirePlanChores(makePanel(), fresh);
fresh.clicks.setup();
var freshHref = window.location.href;
weekState.chores = { chores: [], chores_set_up: true, enabled: true, week_label: 'Sep 7–13' };
var quiet = planChoresStepHtml();
weekState.chores = null;
var loading = planChoresStepHtml();
weekState.choresTrouble = true;
var trouble = planChoresStepHtml();
console.log(JSON.stringify({ fresh: fresh.innerHTML, freshHref: freshHref, quiet: quiet, loading: loading, trouble: trouble }));
""")
    fresh = out["fresh"]
    assert "Chores aren’t set up yet." in fresh
    assert "Want me to suggest a list from what I already know about your place?" in fresh
    assert fresh.count("dock-primary") == 1 and ">Set up chores<" in fresh
    assert 'class="dock pc-dock"' in fresh
    assert out["freshHref"] == "/chores-setup"
    assert "empty-moment-icon" in fresh and "<svg" in fresh
    quiet = out["quiet"]
    assert "Nothing’s due." in quiet and "The house is fine." in quiet
    assert "dock" not in quiet and "<button" not in quiet
    assert "Loading your chores" in out["loading"]
    assert "pull to refresh" in out["trouble"]


@_needs_node
def test_a_today_tagged_action_re_reads_the_list_even_with_now_unbuilt():
    out = _node("""
var CALLS = [];
var panels = { week: { dataset: { built: '1' } }, today: { dataset: {} } };
function loadNeedsYou() { CALLS.push('needsyou'); }
function loadTodayMoves() { CALLS.push('moves'); }
function loadChores() { CALLS.push('chores-now'); }
function loadPlanChores(p) { CALLS.push('chores-plan'); }
function loadWeekMenu() { CALLS.push('week'); }
function refreshDishIndex() { CALLS.push('dish'); }
function refreshKitchenPanel() { CALLS.push('kitchen'); }
function refreshTodayMoves() { CALLS.push('today-moves'); }
function refreshGroceryPanel() { CALLS.push('grocery'); }
function hrefSheetKey() { return null; }
function prefsInvalidate() { CALLS.push('prefs'); }
""" + _function("refreshStaleTabsFromActions") + """
refreshStaleTabsFromActions([{ tab: 'today', tool: 'complete_chore' }]);
var a = CALLS.slice(); CALLS.length = 0;
panels.today.dataset.built = '1';
refreshStaleTabsFromActions([{ tab: 'today', tool: 'some_future_chore_tool' }]);
var b = CALLS.slice(); CALLS.length = 0;
delete panels.week.dataset.built;
refreshStaleTabsFromActions([{ tab: 'today' }, { tab: 'grocery' }]);
console.log(JSON.stringify({ a: a, b: b, c: CALLS }));
""")
    assert out["a"] == ["chores-plan"], "Plan's list re-reads even when Now was never opened"
    assert out["b"] == ["needsyou", "moves", "chores-now", "chores-plan"], "the tab is the contract, not the tool's name"
    assert out["c"] == ["needsyou", "moves", "chores-now", "grocery", "today-moves"]


@_needs_node
def test_a_tick_on_now_tells_plan_chores():
    render = SHELL_JS[SHELL_JS.index("  var TICK_ICON ="):SHELL_JS.index("  function moveTickHtml(")]
    out = _node("""
function escapeHtml(s){return String(s);}
var CALLS = [];
var panels = { week: { dataset: { built: '1' } } };
function loadPlanChores(p) { CALLS.push(p === panels.week); }
function fetch() { return Promise.resolve({ ok: true }); }
function makePanel() {
  var list = { innerHTML: '', querySelectorAll: function () { return []; } };
  return { querySelector: function (sel) { return sel === '#chores-list' ? list : sel === '#chores-count' ? { textContent: '', className: '' } : null; } };
}
""" + render + _function("choreRowHtml") + _function("renderChores") + _function("toggleChore") + """
var rows = [{ id: 11, chore: 'Bins', status: 'pending', who_label: 'Emily' }];
var panel = makePanel();
toggleChore(panel, { dataset: { id: '11' } }, rows);
Promise.resolve().then(function () { return Promise.resolve(); }).then(function () {
  console.log(JSON.stringify({ calls: CALLS, status: rows[0].status }));
});
""")
    assert out["calls"] == [True] and out["status"] == "done"


@_needs_node
def test_the_read_updates_the_cache_and_redraws_only_on_chores():
    out = _node(_prelude() + _dom() + "var planChoresFetching = null;\n" + _function("loadPlanChores") + """
var RENDERS = [];
var realRender = renderMealsStep;
renderMealsStep = function (p) { RENDERS.push(weekState.step); };
var NEXT = %s;
function fetch(url) { return Promise.resolve({ ok: true, json: function () { return Promise.resolve(NEXT); } }); }
var panel = makePanel();
weekState.step = 'week';
loadPlanChores(panel).then(function () {
  var onMeals = { renders: RENDERS.slice(), cached: weekState.chores && weekState.chores.chores.length };
  weekState.step = 'chores';
  return loadPlanChores(panel).then(function () {
    var onChores = { renders: RENDERS.slice() };
    NEXT = { chores: [], chores_set_up: false, enabled: false };
    return loadPlanChores(panel).then(function () {
      console.log(JSON.stringify({ onMeals: onMeals, onChores: onChores, off: { step: weekState.step, enabled: shellWho.chores_enabled, cached: weekState.chores, history: HISTORY } }));
    });
  });
});
""" % json.dumps(_LIST))
    assert out["onMeals"] == {"renders": [], "cached": 7}, "on Meals the read only fills the cache"
    assert out["onChores"]["renders"] == ["chores"]
    # The server saying "off" under an open page takes the state away.
    off = out["off"]
    assert off["enabled"] is False and off["cached"] is None and off["step"] == "week"
    assert off["history"] == [["replace", "week"]]


# ==========================================================================
# 3. What the source has to say for itself
# ==========================================================================

def test_the_control_is_the_review_steps_segmented_control_not_a_new_one():
    """.wk-seg / .wk-seg-btn, copied; the retired .meals-seg stays gone."""
    seg = _function("planModeSegHtml")
    assert 'class="wk-seg plan-mode-seg"' in seg and "wk-seg-btn" in seg
    live_css = re.sub(r"/\*.*?\*/", "", SHELL_CSS, flags=re.DOTALL)
    assert ".meals-seg" not in live_css
    live_js = "\n".join(l for l in SHELL_JS.splitlines() if not l.strip().startswith("//"))
    assert "meals-seg" not in live_js


def test_the_copy_is_in_the_apps_voice():
    """Contractions, no dashboard labels, nothing about being behind."""
    body = "".join(_function(n) for n in ("planChoresStepHtml", "planChoresByRhythmHtml", "togglePlanChore", "planModeSegHtml"))
    body += SHELL_JS[SHELL_JS.index("  var CHORE_GROUP_LABELS ="):SHELL_JS.index("  // The band on the Chores state")]
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))
    literals = re.findall(r"'((?:[^'\\]|\\.)*)'", code)
    printed = " ".join(l for l in literals if "<" not in l and "=" not in l and "/" not in l)
    assert "!" not in printed
    for banned in ("overdue", "missed", "behind", "late", "Action required", "Task", "Completed", "Overview"):
        assert not re.search(r"\b%s\b" % banned, printed), banned
    for line in ("Chores aren’t set up yet.", "Nothing’s due.", "The house is fine.",
                 "That didn’t save. Try it again in a moment."):
        assert line in printed, line
    assert '>Set up chores</button>' in body


def test_the_css_is_tokens_only_and_borrows_rather_than_forks():
    start = SHELL_CSS.index("/* ---------- Plan | Chores (a state of the Plan root) ----------")
    end = SHELL_CSS.index(".rv-body {", start)
    block = SHELL_CSS[start:end]
    rules = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    assert "#" not in re.sub(r"#[a-z][\w-]*", "", rules), "a literal colour (ids are not colours)"
    for alias in ("--rule", "--plum-ink", "--muted", "--faded", "--card"):
        assert "var(%s)" % alias not in rules, f"{alias} is a retired alias"
    assert "apricot" not in rules
    # The rows are Now's rows, stacked by one rule — not a second row class.
    assert ".pc-list .chore-name { flex: 1 1 100%; }" in rules
    assert ".chore-main {" in SHELL_CSS
    assert ".pc-row" not in SHELL_CSS and ".plan-chore-row" not in SHELL_CSS
    # The group grammar is the review step's.
    step = _function("planChoresStepHtml")
    assert 'class="rv-group-label"' in step and 'class="shell-card rv-group-card pc-list"' in step


def test_the_headings_are_the_supported_rhythms():
    """One heading per frequency chores.py supports — no more, no fewer."""
    labels = SHELL_JS[SHELL_JS.index("  var CHORE_RHYTHM_LABELS ="):SHELL_JS.index("  var CHORE_RHYTHM_ORDER")]
    named = set(re.findall(r"^\s+(\w+):", labels, flags=re.M))
    assert named == set(tools._FREQUENCY_DAYS)
