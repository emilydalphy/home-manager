"""
Chores is switched on per household, and Now's chores card serves the story.

Two Loop Board cards, built together on 2026-09-12 because the second is
what the first is for:

  "Chores v1: Who sees it — a per-household switch" (Emily): Chores on in
  her own house, off for the tester, so it is validated on real weeks
  before it competes with the meal loop for the tester's attention.
  `households.chores_enabled` (0 by default) replaces the global
  SHOW_CHORES_ON_TODAY constant that hid the card for everyone from
  2026-09-08. It rides on /api/whoami into the shell; while off there is
  no card, no /api/chores/today request, no invitation into
  /chores-setup, the two chores routes answer empty / 403, and every
  chores chat tool declines with one plain sentence instead of
  half-working (nine at the time this file was written; skip_chore and
  move_chore joined the same gate on 2026-09-12 — see
  test_the_chores_tools_are_the_gated_set below). set_chores_enabled.py
  flips it — no admin UI.

  "Chores v1: Turn the 'Your chores' card on Now back on — and make it
  serve the story": today's chores next to tonight's dinner, whole
  household, the owner's first name on each row, an outsourced row with
  who does it and no tick, a tick that marks it done in place, and a
  re-render after a chat turn changes chores.

Backend half over the real routes and the real agent loop (stubbed API
client, never a stubbed loop — see test_observability.py for why); front
end half runs the shell's own builders under node, per the house standard
(tests/nodeharness.py), rather than reading the source for markers where
behaviour is what matters. A few marker tests remain where the claim IS
about the source (the constant is gone; the refresh branch names the
card). Every behavioural test here fails on main before the switch.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import nodeharness
import pytest

from app import agent, households, tools
from app.db import _run_migrations, get_conn

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SNAPSHOT = (REPO / "tests" / "fixtures" / "schema_snapshot.sql").read_text(encoding="utf-8")

TODAY = datetime.date.today().isoformat()


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


def _chore_due_today(name: str, **kwargs) -> int:
    tools.add_chore(name, **kwargs)
    return tools.schedule_chore_instance(name, TODAY)["instance_id"]


# ==========================================================================
# 1. The switch itself
# ==========================================================================

def test_a_fresh_household_has_chores_off():
    """Default OFF for every household, the seeded one included."""
    assert tools.chores_enabled() is False
    new_id = households.create_household("The Testers", "correct-horse-battery-staple")
    with tools.use_household(new_id):
        assert tools.chores_enabled() is False
    conn = get_conn()
    rows = conn.execute("SELECT id, chores_enabled FROM households ORDER BY id").fetchall()
    conn.close()
    assert [r["chores_enabled"] for r in rows] == [0, 0]


def test_the_switch_is_per_household_not_global():
    """
    The whole point: on in one house, off in the other, at the same time.
    A global flag could never say this.
    """
    other = households.create_household("The Testers", "correct-horse-battery-staple")
    tools.set_chores_enabled(True)  # household 1
    assert tools.chores_enabled() is True
    with tools.use_household(other):
        assert tools.chores_enabled() is False
        tools.set_chores_enabled(True)
        assert tools.chores_enabled() is True
    tools.set_chores_enabled(False)
    assert tools.chores_enabled() is False
    with tools.use_household(other):
        assert tools.chores_enabled() is True, "switching house 1 off must not touch house 2"


def test_switching_an_unknown_household_is_refused():
    with pytest.raises(ValueError):
        tools.set_chores_enabled(True, household=404)


def test_whoami_carries_the_switch(signed_in):
    """The shell reads it off /api/whoami at boot (ensureWhoPicked → loadWhoami)."""
    assert signed_in.get("/api/whoami").json()["chores_enabled"] is False
    tools.set_chores_enabled(True)
    assert signed_in.get("/api/whoami").json()["chores_enabled"] is True


def test_the_migration_adds_the_column_to_an_existing_database(tmp_path):
    """
    A live database is never rebuilt from schema.sql — only _MIGRATIONS can
    add a column to a table that already exists. Built from the pinned
    pre-switch snapshot and upgraded the way db.init_db does, the column is
    there and reads 0 for the household the snapshot seeds.
    """
    assert "chores_enabled" not in SNAPSHOT, "the snapshot has caught up — this test needs an older one"
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SNAPSHOT)
        conn.execute("INSERT OR IGNORE INTO households (id, name) VALUES (1, 'Old house')")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(households)")}
        assert "chores_enabled" not in cols
        _run_migrations(conn)
        conn.commit()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(households)")}
        assert "chores_enabled" in cols
        assert conn.execute("SELECT chores_enabled FROM households WHERE id = 1").fetchone()[0] == 0
    finally:
        conn.close()


# ==========================================================================
# 2. The two routes
# ==========================================================================

def test_chores_today_answers_empty_and_says_so_while_off(signed_in):
    """
    Off → 200 with an empty list and enabled: false, EVEN WITH chores rows
    on file. A 200 rather than a 4xx because the shell's loadChores prints
    any non-2xx as "Couldn't load chores right now", and an error line
    about a feature the house doesn't have is worse than a quiet empty
    answer (the route's docstring says the same).
    """
    _adult("Emily")
    _chore_due_today("Bins", owner_name="Emily")
    res = signed_in.get("/api/chores/today")
    assert res.status_code == 200
    assert res.json() == {"chores": [], "chores_set_up": False, "enabled": False}


def test_turning_it_on_shows_the_chores_already_there(signed_in):
    """
    No backfill: the switch decides what Now shows, not what exists. A
    house with chores rows and the switch off (the tester's, say, after a
    chat turn that slipped through before the gate) sees them the moment
    it is switched on.
    """
    _adult("Emily")
    _chore_due_today("Bins", owner_name="Emily")
    _chore_due_today("Bathrooms", mode="outsourced", outsourced_to="Maria")
    assert signed_in.get("/api/chores/today").json()["chores"] == []

    tools.set_chores_enabled(True)
    body = signed_in.get("/api/chores/today").json()
    assert body["enabled"] is True and body["chores_set_up"] is True
    rows = {c["chore"]: c for c in body["chores"]}
    assert set(rows) == {"Bins", "Bathrooms"}
    # The story the card tells: owner's first name; an outsourced row says
    # who and cannot be ticked.
    assert rows["Bins"]["who_label"] == "Emily" and rows["Bins"]["completable"] is True
    assert rows["Bathrooms"]["who_label"] == "Maria"
    assert rows["Bathrooms"]["outsourced"] is True and rows["Bathrooms"]["completable"] is False


def test_a_slipped_chore_reads_as_due_today_on_the_card(signed_in):
    """The no-guilt-pile work: one row, due today, however long it has sat."""
    _adult("Emily")
    tools.set_chores_enabled(True)
    tools.add_chore("Mop", frequency="weekly", owner_name="Emily")
    for days_ago in (21, 14, 7):
        tools.schedule_chore_instance("Mop", (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat())
    body = signed_in.get("/api/chores/today").json()
    assert len(body["chores"]) == 1
    assert body["chores"][0]["chore"] == "Mop" and body["chores"][0]["status"] == "pending"


def test_the_status_route_refuses_while_off_and_changes_nothing(signed_in):
    _adult("Emily")
    instance_id = _chore_due_today("Bins", owner_name="Emily")
    res = signed_in.post(f"/api/chores/{instance_id}/status", json={"status": "done"})
    assert res.status_code == 403
    assert res.json()["detail"] == tools.CHORES_OFF_MESSAGE
    conn = get_conn()
    status = conn.execute("SELECT status FROM chore_instances WHERE id = ?", (instance_id,)).fetchone()["status"]
    conn.close()
    assert status == "pending"

    tools.set_chores_enabled(True)
    assert signed_in.post(f"/api/chores/{instance_id}/status", json={"status": "done"}).status_code == 200
    assert signed_in.get("/api/chores/today").json()["chores"][0]["status"] == "done"


def test_a_refused_tick_is_not_recorded_as_breakage(signed_in):
    """A 403 is an answer. The morning report must not read it as a broken route."""
    _adult("Emily")
    instance_id = _chore_due_today("Bins", owner_name="Emily")
    conn = get_conn()
    before = conn.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
    conn.close()
    signed_in.post(f"/api/chores/{instance_id}/status", json={"status": "done"})
    signed_in.get("/api/chores/today")
    conn = get_conn()
    after = conn.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
    conn.close()
    assert after == before


def test_chores_setup_stays_reachable_by_url_either_way(signed_in):
    """The card that hid it said so: the page exists; the switch decides the link."""
    assert signed_in.get("/chores-setup").status_code == 200
    tools.set_chores_enabled(True)
    assert signed_in.get("/chores-setup").status_code == 200


# ==========================================================================
# 3. The chat tools decline, gently, in one place
# ==========================================================================

class _Usage:
    input_tokens = cache_read_input_tokens = cache_creation_input_tokens = output_tokens = 0


def _text_block(text):
    return types.SimpleNamespace(type="text", text=text)


def _tool_block(name, tool_input, block_id="tu_1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _stub_client(monkeypatch, responses):
    fake = _FakeMessages(responses)
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=fake))
    return fake


_CHORES_TOOL_CALLS = [
    ("get_chores_profile", {}),
    ("set_chores_profile", {"home_type": "House"}),
    ("add_chore", {"name": "Bins", "frequency": "weekly"}),
    ("list_chore_definitions", {}),
    ("update_chore", {"chore_name": "Bins", "mode": "shared"}),
    ("generate_chore_schedule", {"days_ahead": 7}),
    ("schedule_chore_instance", {"chore_name": "Bins", "due_date": TODAY}),
    ("list_chores", {}),
    ("complete_chore", {"instance_id": 1}),
    ("skip_chore", {"chore_name": "Bins"}),
    ("move_chore", {"chore_name": "Bins", "to_date": TODAY}),
]


def test_the_chores_tools_are_the_gated_set():
    """Every chores entry in TOOL_FUNCTIONS is behind the gate, and nothing else is."""
    assert agent.CHORES_TOOLS == {name for name, _ in _CHORES_TOOL_CALLS}
    assert agent.CHORES_TOOLS <= set(agent.TOOL_FUNCTIONS)
    # The entries stay the plain functions — the gate is at dispatch, so
    # the chores-setup routes and tests can call them directly.
    assert agent.TOOL_FUNCTIONS["add_chore"] is tools.add_chore


@pytest.mark.parametrize("name, tool_input", _CHORES_TOOL_CALLS)
def test_each_chores_tool_declines_while_off(monkeypatch, name, tool_input):
    """
    Through the real agent loop: the model calls the tool, the loop hands
    back the one sentence as an is_error tool_result (so a declined
    add_chore is never counted as a write), the tool itself never runs,
    and nothing lands in error_events — a decline is an answer, not a
    crash.
    """
    _adult("Emily")
    ran = []
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, name, lambda **kw: ran.append(name) or {"ok": True})
    fake = _stub_client(monkeypatch, [
        types.SimpleNamespace(content=[_tool_block(name, tool_input)], stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[_text_block("Chores isn't switched on for your house yet.")],
                              stop_reason="end_turn", usage=_Usage()),
    ])
    conn = get_conn()
    before = conn.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
    conn.close()

    reply, conversation = agent.run_agent_turn([], "what about the chores?")

    assert ran == [], f"{name} ran in a house with Chores off"
    assert len(fake.requests) == 2, "the loop went on to a second round with the decline in hand"
    # What the model was handed on round two: the tool_result entry.
    results = [m for m in conversation if m["role"] == "user" and isinstance(m["content"], list)]
    handed = results[-1]["content"][0]
    assert handed["type"] == "tool_result" and handed["is_error"] is True
    result = json.loads(handed["content"])
    assert result["error"] == tools.CHORES_OFF_MESSAGE
    assert result["message"] == tools.CHORES_OFF_MESSAGE
    assert result["status"] == "declined"
    assert "don't bring it up" in result["what_to_do"]
    # Relayed plainly, not rewritten as a retraction or an apology.
    assert reply == "Chores isn't switched on for your house yet."
    conn = get_conn()
    after = conn.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
    conn.close()
    assert after == before, "a declined chores tool was recorded as a broken tool"


def test_a_declined_add_chore_cannot_back_a_change_claim(monkeypatch):
    """
    The reason the decline is is_error: a model that says "I've added it"
    after a decline is caught by verify_change_claim, because the turn
    wrote nothing. (The claim patterns speak the meal loop's language —
    "it", "the list" — which is what a smooth reply would say too.)
    """
    _adult("Emily")
    _stub_client(monkeypatch, [
        types.SimpleNamespace(content=[_tool_block("add_chore", {"name": "Bins", "frequency": "weekly"})],
                              stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[_text_block("Done — I've added it to the list.")],
                              stop_reason="end_turn", usage=_Usage()),
    ])
    reply, _ = agent.run_agent_turn([], "add bins as a weekly chore")
    assert reply == agent.CHANGE_CLAIM_RETRACTION
    assert tools.list_chore_definitions() == []


def test_the_tools_run_as_before_once_on(monkeypatch):
    _adult("Emily")
    tools.set_chores_enabled(True)
    _stub_client(monkeypatch, [
        types.SimpleNamespace(content=[_tool_block("add_chore", {"name": "Bins", "frequency": "weekly"})],
                              stop_reason="tool_use", usage=_Usage()),
        types.SimpleNamespace(content=[_text_block("Done — Bins is on the list, weekly.")],
                              stop_reason="end_turn", usage=_Usage()),
    ])
    reply, _ = agent.run_agent_turn([], "add bins as a weekly chore")
    assert reply == "Done — Bins is on the list, weekly."
    assert [c["name"] for c in tools.list_chore_definitions()] == ["Bins"]


def test_the_chat_no_longer_opens_with_chores_setup_for_an_off_house():
    """
    get_household_setup_status is what the system prompt reads to decide
    whether to open with the chores questions. Before the switch a house
    with people and no chores was "incomplete" forever — for the tester,
    an offer to set up chores at the start of every conversation. Off
    means there is nothing to be incomplete; on keeps the old meaning.
    """
    _adult("Emily")
    status = tools.get_household_setup_status()
    assert status["chores_enabled"] is False
    assert status["onboarding_complete"] is True
    tools.set_chores_enabled(True)
    status = tools.get_household_setup_status()
    assert status["chores_enabled"] is True
    assert status["onboarding_complete"] is False
    assert "chores_enabled" in agent.SYSTEM_PROMPT, "the prompt has to know the field exists"


# ==========================================================================
# 4. The ops script
# ==========================================================================

def _run_script(db_path, *args):
    env = {**os.environ, "DB_PATH": str(db_path)}
    env.pop("RAILWAY_VOLUME_MOUNT_PATH", None)
    return subprocess.run(
        [sys.executable, "set_chores_enabled.py", *args],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )


def _switch(db_path, household_id=1):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT chores_enabled FROM households WHERE id = ?", (household_id,)).fetchone()[0]
    finally:
        conn.close()


def test_the_script_flips_the_switch_and_lists_it(tmp_path):
    """
    The one-line command that goes on the ticket:
      python set_chores_enabled.py on --household 1
    (railway ssh -- python set_chores_enabled.py on --household 1 on Railway.)
    """
    db = tmp_path / "throwaway.db"
    res = _run_script(db, "--list")
    assert res.returncode == 0, res.stderr
    assert "1: " in res.stdout and "off" in res.stdout
    assert _switch(db) == 0

    res = _run_script(db, "on")
    assert res.returncode == 0, res.stderr
    assert "Chores is ON for household 1" in res.stdout
    assert _switch(db) == 1

    res = _run_script(db, "--list")
    assert "ON" in res.stdout

    res = _run_script(db, "off", "--household", "1")
    assert res.returncode == 0, res.stderr
    assert "Nothing was deleted" in res.stdout
    assert _switch(db) == 0


def test_the_script_refuses_a_household_that_does_not_exist(tmp_path):
    db = tmp_path / "throwaway.db"
    res = _run_script(db, "on", "--household", "9")
    assert res.returncode != 0
    assert "no household with id 9" in (res.stdout + res.stderr)


def test_the_script_needs_on_or_off(tmp_path):
    res = _run_script(tmp_path / "throwaway.db")
    assert res.returncode != 0
    assert "say on or off" in res.stderr, "argparse's own refusal, not a missing script"


# ==========================================================================
# 5. The shell — run under node
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


def _node(script: str):
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# The pieces buildTodayPanel leans on, stubbed to the two things this test
# is about: what markup it writes, and which loaders it calls.
def _today_prelude() -> str:
    return """
function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
var PREFS_GEAR_ICON = '<svg></svg>';
var shellWho = { member: null, adults: [], chores_enabled: false, loaded: false };
var CALLS = [];
var FETCHED = [];
function loadPlanWeekNudge(p) { CALLS.push('nudge'); return Promise.resolve(); }
function loadNeedsYou(p) { CALLS.push('needsyou'); return Promise.resolve(); }
function loadTodayMoves(p) { CALLS.push('moves'); return Promise.resolve(); }
function renderCoachCard() { CALLS.push('coach'); }
function fetch(url) { FETCHED.push(url); return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ chores: [], chores_set_up: true, enabled: true }); } }); }
""" + _function("prefsGearHtml") + _function("rootBandHtml") + _function("choresEnabled") + _function("buildTodayPanel")


def _panel_js():
    # A panel that remembers its markup and can find the chores card's
    # three slots inside it.
    return """
function makePanel() {
  var panel = { innerHTML: '', _els: {} };
  panel.querySelector = function (sel) {
    if (panel.innerHTML.indexOf(sel.replace('#', 'id="')) === -1 && panel.innerHTML.indexOf(sel.replace('.', 'class="')) === -1) return null;
    if (!panel._els[sel]) panel._els[sel] = { innerHTML: '', textContent: '', className: '', style: {}, querySelectorAll: function () { return []; }, remove: function () { panel._removed = sel; } };
    return panel._els[sel];
  };
  return panel;
}
"""


@_needs_node
def test_off_builds_no_card_and_makes_no_chores_request():
    out = _node(_today_prelude() + _panel_js() + """
shellWho.chores_enabled = false;
var panel = makePanel();
buildTodayPanel(panel).then(function () {
  console.log(JSON.stringify({ html: panel.innerHTML, calls: CALLS, fetched: FETCHED }));
});
""")
    assert "chores-card" not in out["html"]
    assert "chores-setup-link" not in out["html"], "no invitation into chores for a house with the switch off"
    assert "Your chores" not in out["html"]
    assert not any("/api/chores" in u for u in out["fetched"]), "an off house must make no chores request"
    # The rest of Now is untouched.
    assert 'id="today-band"' in out["html"] and 'id="today-next-up"' in out["html"] and 'id="needs-you-band"' in out["html"]
    assert out["calls"] == ["nudge", "needsyou", "moves", "coach"]


@_needs_node
def test_on_builds_the_card_and_loads_it():
    out = _node(_today_prelude() + _panel_js() + _function("loadChores") + """
var RENDERED = [];
function renderChores(panel, chores, setUp) { RENDERED.push([chores, setUp]); }
shellWho.chores_enabled = true;
var panel = makePanel();
buildTodayPanel(panel).then(function () {
  console.log(JSON.stringify({ html: panel.innerHTML, fetched: FETCHED, rendered: RENDERED }));
});
""")
    html = out["html"]
    assert 'class="shell-card chores-card"' in html
    assert "<h2>Your chores</h2>" in html
    assert 'id="chores-list"' in html and 'id="chores-count"' in html
    assert out["fetched"] == ["/api/chores/today"]
    assert out["rendered"] == [[[], True]]
    # Where it sits: after the needs-you band and the rest of today, inside
    # the body — never above tonight's dinner (#today-next-up), never the
    # dock, and no apricot of its own.
    assert html.index('id="today-next-up"') < html.index('id="needs-you-band"') < html.index('id="today-rest"') < html.index("chores-card")
    assert html.index("chores-card") < html.index('id="today-dock"')
    card = html[html.index("chores-card"):html.index('id="today-empty"')]
    assert "dock" not in card and "apricot" not in card and "btn-primary" not in card
    assert "<button" not in card, "ticks arrive with the rows; the card's shell has no button"


@_needs_node
def test_the_whole_card_goes_if_the_server_says_off_under_an_open_page():
    """The boot read and the load read can disagree if the switch is flipped
    under an open page — the server's word wins, and the card leaves."""
    out = _node(_today_prelude() + _panel_js() + _function("loadChores") + """
function renderChores() { throw new Error('should not render'); }
fetch = function () { return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ chores: [], chores_set_up: false, enabled: false }); } }); };
shellWho.chores_enabled = true;
var panel = makePanel();
buildTodayPanel(panel).then(function () {
  console.log(JSON.stringify({ removed: panel._removed || null }));
});
""")
    assert out["removed"] == ".today-area-chores"


# renderChores/toggleChore under node, with a list element that can read
# its own rows back and remember the tick handlers it was given.
def _render_prelude() -> str:
    return """
function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
""" + SHELL_JS[SHELL_JS.index("  var TICK_ICON ="):SHELL_JS.index("  function moveTickHtml(")] + """
var POSTS = [];
var FAIL_NEXT = false;
function fetch(url, opts) {
  POSTS.push({ url: url, body: JSON.parse(opts.body) });
  if (FAIL_NEXT) { FAIL_NEXT = false; return Promise.resolve({ ok: false }); }
  return Promise.resolve({ ok: true });
}
function makeList() {
  var el = { innerHTML: '', handlers: {} };
  el.querySelectorAll = function (sel) {
    var out = [], re = /<div class="chore-row(?! is-outsourced)[^"]*" data-id="(\\d+)">/g, m;
    while ((m = re.exec(el.innerHTML)) !== null) {
      (function (id) {
        out.push({ dataset: { id: String(id) },
                   querySelector: function () { return { addEventListener: function (_e, fn) { el.handlers[id] = fn; } }; } });
      })(Number(m[1]));
    }
    return out;
  };
  return el;
}
function makePanel() {
  var list = makeList(), count = { textContent: '', className: '' }, link = { style: { display: 'none' } };
  var panel = { list: list, count: count, link: link };
  panel.querySelector = function (sel) {
    return sel === '#chores-list' ? list : sel === '#chores-count' ? count : sel === '#chores-setup-link' ? link : null;
  };
  return panel;
}
""" + _function("renderChores") + _function("toggleChore")


_ROWS = [
    {"id": 11, "chore": "Bins", "status": "pending", "who_label": "Emily", "outsourced": False, "completable": True},
    {"id": 12, "chore": "Kitchen floor", "status": "done", "who_label": "Vineeth", "outsourced": False, "completable": True},
    {"id": 13, "chore": "Bathrooms", "status": "pending", "who_label": "Maria", "outsourced": True, "completable": False},
]


@_needs_node
def test_rows_carry_the_name_the_tick_and_the_outsourced_shape():
    out = _node(_render_prelude() + """
var panel = makePanel();
renderChores(panel, %s, true);
console.log(JSON.stringify({ html: panel.list.innerHTML, count: panel.count.textContent, cls: panel.count.className, link: panel.link.style.display, handlers: Object.keys(panel.list.handlers) }));
""" % json.dumps(_ROWS))
    html = out["html"]
    # Whole household, owner's first name on each row.
    for name in ("Emily", "Vineeth", "Maria"):
        assert '<span class="chore-who">%s</span>' % name in html
    # The tick is the system's own .tick — 44px target, TICK_ICON glyph,
    # dark ink via .tick.is-done — and a done row is drawn done.
    assert html.count('class="tick chore-tick"') == 1
    assert html.count('class="tick chore-tick is-done"') == 1
    assert 'stroke="#fff"' not in html
    assert 'aria-pressed="false" aria-label="Tick it off"' in html
    assert 'aria-pressed="true" aria-label="Put it back on the list"' in html
    # An outsourced row: who does it, "Not us", and no tick — a spacer instead.
    outsourced = html[html.index('chore-row is-outsourced'):]
    assert "Not us" in outsourced and "Maria" in outsourced
    assert "chore-tick" not in outsourced and 'class="tick tick-empty"' in outsourced
    assert "<button" not in outsourced
    # The count leaves the outsourced one out; the two ours are 1 of 2.
    assert out["count"] == "1 of 2" and out["cls"] == "chores-count"
    # Set up → no invitation; handlers only on the rows that have a tick.
    assert out["link"] == "none"
    assert sorted(out["handlers"]) == ["11", "12"]
    # No emoji, no apricot, nothing hand-coloured.
    assert "#" not in html.replace("&#", "") and "apricot" not in html


@_needs_node
def test_nothing_due_is_one_plain_line_and_a_new_house_gets_the_invitation_alone():
    out = _node(_render_prelude() + """
var a = makePanel(); renderChores(a, [], true);
var b = makePanel(); renderChores(b, [], false);
console.log(JSON.stringify({ setUp: a.list.innerHTML, setUpLink: a.link.style.display, setUpCount: a.count.textContent,
                             fresh: b.list.innerHTML, freshLink: b.link.style.display }));
""")
    assert out["setUp"] == '<div class="empty-row">No chores today.</div>'
    assert out["setUpLink"] == "none" and out["setUpCount"] == ""
    # Never set up: the invitation, and nothing above it saying the same thing.
    assert out["fresh"] == "" and out["freshLink"] == "block"


def test_the_empty_line_is_in_the_apps_voice():
    """No exclamation mark, no guilt, and the invitation copy is unchanged."""
    body = _function("renderChores")
    assert "No chores today." in body
    # The strings the card prints (single-quoted JS literals), not its
    # comments — and only the words, since "!" is also JS's not-operator
    # inside the markup builders (`!!`, `!c.outsourced`).
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))
    literals = re.findall(r"'((?:[^'\\]|\\.)*)'", code)
    printed = " ".join(l for l in literals if "<" not in l and "=" not in l)
    for banned in ("!", "overdue", "missed", "behind", "late"):
        assert banned not in printed, banned
    assert "Want help with chores too? Set them up" in _function("buildTodayPanel")


@_needs_node
def test_a_tick_marks_it_done_in_place_and_rolls_back_if_the_save_fails():
    out = _node(_render_prelude() + """
var panel = makePanel();
var rows = %s;
renderChores(panel, rows, true);
var after = [];
function snap() { after.push({ done: /chore-row done" data-id="11"/.test(panel.list.innerHTML), count: panel.count.textContent }); }
panel.list.handlers['11']();          // tick Bins
snap();                               // optimistic, before the save lands
Promise.resolve().then(function () { return Promise.resolve(); }).then(function () {
  snap();
  FAIL_NEXT = true;
  panel.list.handlers['12']();        // untick Kitchen floor, and the save fails
  return Promise.resolve().then(function () { return Promise.resolve(); }).then(function () { return Promise.resolve(); });
}).then(function () {
  var floorDone = /chore-row done" data-id="12"/.test(panel.list.innerHTML);
  console.log(JSON.stringify({ posts: POSTS, after: after, floorDone: floorDone, count: panel.count.textContent }));
});
""" % json.dumps(_ROWS))
    assert out["posts"][0] == {"url": "/api/chores/11/status", "body": {"status": "done"}}
    assert out["after"][0] == {"done": True, "count": "2 of 2"}, "the row flips before the server answers"
    assert out["after"][1] == {"done": True, "count": "2 of 2"}
    # The failed untick is put back the way it was, and said so quietly
    # (console.warn) rather than left half-flipped.
    assert out["posts"][1] == {"url": "/api/chores/12/status", "body": {"status": "pending"}}
    assert out["floorDone"] is True and out["count"] == "2 of 2"


# ==========================================================================
# 6. What the source has to say for itself
# ==========================================================================

def test_the_global_constant_is_gone_from_the_shell():
    live = "\n".join(l for l in SHELL_JS.splitlines() if not l.strip().startswith("//"))
    assert "SHOW_CHORES_ON_TODAY" not in live, "the switch is per household — no global constant"
    assert "shellWho.chores_enabled = !!data.chores_enabled" in _function("loadWhoami")


def test_a_chat_turn_that_changes_chores_refreshes_the_card():
    """
    The chore tools are tagged tab: 'today' (app/main.py's _CHORE_TOOLS),
    and refreshStaleTabsFromActions' today branch has to re-read the card
    — the same way kitchen and grocery refresh Now's moves.
    """
    body = _function("refreshStaleTabsFromActions")
    today = body[body.index("action.tab === 'today'"):]
    assert "loadChores(panels.today);" in today
    # And loadChores is safe on a panel with no card (the switch off).
    load = _function("loadChores")
    assert "if (!listEl || !countEl) return;" in load


def test_the_card_uses_tokens_only_and_the_shared_tick():
    """DESIGN_SYSTEM: no retired alias, no literal colour, the system's tick."""
    start = SHELL_CSS.index("/* Your chores */")
    end = SHELL_CSS.index(".empty-row {", start)
    block = SHELL_CSS[start:end]
    for alias in ("--rule", "--plum-ink", "--muted", "--faded", "--ink-done-soft"):
        assert "var(%s)" % alias not in block, f"{alias} is a retired alias"
    rules = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    assert "#" not in rules, "a literal colour in the chores card"
    assert ".chore-checkbox {" not in SHELL_CSS, "the 26px square is gone; the rows use .tick"
    assert "1.5px solid var(--hairline)" in block
    assert "apricot" not in rules
