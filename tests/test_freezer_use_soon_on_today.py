"""
Loop Board "Freezer portions from a night off get planned into a later week
(with a defrost reminder)", criterion 3: "If it isn't used within ~4 weeks,
it surfaces once on Today as a use-soon item."

The server half already existed (freezer_portions.sweep_use_soon queues the
note once ever, on the attention queue, read by Cook's fold and the morning
text). What was missing was Today. These pin the join: the REAL queue's
payload for an aged portion is what Today's filter picks out and draws, a
night off's own use-soon note (same kind, no inventory id) is not, and Done
resolves the row on the queue every surface reads — so "once" holds.
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import nodeharness
import pytest

from conftest import household_today
from app import tools
from app.tools import freezer_portions as _fp
from app.tools import tonight as _tonight
from test_freezer_portion_planned import _a_frozen_portion, household  # noqa: F401 (fixture)

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the shell's own builders"
)


def _function(name: str) -> str:
    marker = "  async function %s(" % name
    if marker not in SHELL_JS:
        marker = "  function %s(" % name
    start = SHELL_JS.index(marker)
    end = SHELL_JS.index("\n  }\n", start)
    return SHELL_JS[start:end] + "\n  }\n"


def _queue_payload() -> list[dict]:
    """What GET /api/attention answers (the route returns exactly this)."""
    return json.loads(json.dumps(tools.get_attention_items(), default=str))


def _run(items: list[dict], click: bool = False) -> dict:
    script = """
function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
var ITEMS = %s;
var POSTED = [];
var TOASTS = [];
var cookState = { attention: ITEMS.slice() };
function savedName(t){ return String(t || '').trim(); }
function savedLine(t, v){ return savedName(t) + ' was ' + v; }
function toastSaved(s){ TOASTS.push(s); }
function showToast(s){ TOASTS.push(s); }
var Api = { json: function (path, opts) {
  if (opts && opts.body) {
    POSTED.push({ path: path, body: opts.body });
    var id = path.split('/')[3];
    ITEMS = ITEMS.filter(function (it) { return String(it.id) !== id; });
  }
  return Promise.resolve({ items: ITEMS });
} };
var BUTTONS = [];
var slot = { innerHTML: '', querySelectorAll: function () {
  var m = slot.innerHTML.match(/data-usesoon-done="(\\d+)"/g) || [];
  return m.map(function (a) {
    var id = a.match(/\\d+/)[0];
    var b = { disabled: false, getAttribute: function () { return id; },
              addEventListener: function (ev, fn) { b.fn = fn; } };
    BUTTONS.push(b);
    return b;
  });
} };
var panel = { querySelector: function () { return slot; } };
""" % json.dumps(items)
    script += _function("isFreezerUseSoon") + "  var useSoonState = { items: [] };\n"
    script += _function("loadUseSoon") + _function("renderUseSoon") + _function("resolveUseSoon")
    script += """
loadUseSoon(panel).then(function () {
  var first = slot.innerHTML;
  var after = Promise.resolve();
  if (%s && BUTTONS.length) { BUTTONS[0].fn(); after = new Promise(function (r) { setTimeout(r, 10); }); }
  return after.then(function () {
    console.log(JSON.stringify({ first: first, after: slot.innerHTML, posted: POSTED,
                                 toasts: TOASTS, cook: cookState.attention.length }));
  });
});
""" % ("true" if click else "false")
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@_needs_node
def test_an_aged_portion_is_on_today_and_done_takes_it_off_everywhere(household):
    """CATCH — criterion 3 on Today, off the queue's real payload."""
    frozen_on = (household_today() - datetime.timedelta(days=_fp.USE_SOON_AFTER_DAYS)).isoformat()
    _a_frozen_portion("Kofte", frozen_on=frozen_on)
    items = _queue_payload()
    out = _run(items, click=True)
    assert "Use soon" in out["first"]
    assert "Kofte has been in the freezer 4 weeks" in out["first"]
    item_id = [i["id"] for i in items if i["kind"] == _tonight.USE_SOON_KIND][0]
    assert out["posted"] == [{"path": "/api/attention/%d/resolve" % item_id,
                              "body": {"status": "resolved"}}]
    assert out["after"] == "", "the card goes once it is marked done"
    assert out["toasts"] == ["Kofte was marked handled"]
    assert out["cook"] == 0, "Cook's fold is told too, so it does not show a settled row"

    # And on the server it is settled for good: the sweep never re-queues it.
    tools.resolve_attention_item(item_id, "resolved")
    assert [i for i in tools.get_attention_items() if i["kind"] == _tonight.USE_SOON_KIND] == []


@_needs_node
def test_a_fresh_portion_and_a_night_offs_note_draw_nothing(household):
    """CATCH on the filter: a portion under four weeks is not queued, and a
    night off's own use-soon note (same kind, no inventory id) is not this
    card's — it was said on Today at the moment of the night off."""
    frozen_on = (household_today() - datetime.timedelta(days=_fp.USE_SOON_AFTER_DAYS - 1)).isoformat()
    _a_frozen_portion("Kofte", frozen_on=frozen_on)
    tools.add_attention_item(_tonight.USE_SOON_KIND, "Use the spinach soon.",
                             {"items": ["spinach"], "dish": "Saag"})
    items = _queue_payload()
    assert [i["summary"] for i in items if i["kind"] == _tonight.USE_SOON_KIND] == ["Use the spinach soon."]
    out = _run(items)
    assert out["first"] == ""
