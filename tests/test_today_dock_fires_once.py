"""
One tap on Today's dock runs its action once (overnight hunt, 2026-10-21).

renderTodayDock binds the dock's own button, and renderTodayMoves then bound
every `[data-move-action]` in the PANEL — the dock is in the panel, so its
button got a second listener. A dock "Done" on a fridge move (or "Mark
eaten" on a reheat) ran toggleTodayMove twice from one tap: the first call
set the move done optimistically, the second read that and posted an untick,
and the two requests raced (driven at 390px: one tap sent `{"done":true}`
and `{"done":false}`).

Executes renderTodayMoves and renderTodayDock under node with a panel whose
querySelectorAll sees the dock's button, the way a real DOM does.
"""
from __future__ import annotations

import shutil

import pytest

from test_today_shop_cook import _day, _node, _payload, _prelude

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node runs shell.js's own render")


def test_the_dock_button_gets_one_click_listener():
    script = _prelude() + """
function realPanel() {
  var panel = makePanel();
  var dock = panel.querySelector('#today-dock');
  var html = '';
  dock.buttons = [];
  Object.defineProperty(dock, 'innerHTML', {
    get: function () { return html; },
    set: function (v) {
      html = v;
      dock.btn = null;
      if (v.indexOf('data-move-action') !== -1) {
        dock.btn = { clicks: 0, getAttribute: function () { return 'fridge:4'; },
                     closest: function (sel) { return sel === '#today-dock' ? dock : null; },
                     addEventListener: function (t) { if (t === 'click') this.clicks++; } };
        dock.buttons.push(dock.btn);
      }
    }
  });
  dock.querySelector = function (sel) { return sel === '[data-move-action]' ? dock.btn : null; };
  panel.querySelectorAll = function (sel) {
    return sel === '[data-move-action]' && dock.btn ? [dock.btn] : [];
  };
  return panel;
}
var panel = realPanel();
renderTodayMoves(panel, DATA);
var dock = panel.querySelector('#today-dock');
console.log(JSON.stringify({html: dock.innerHTML, clicks: dock.btn ? dock.btn.clicks : null}));
""".replace("DATA", __import__("json").dumps(_payload("fridge:4", _day())))
    out = _node(script)
    assert 'data-move-action="fridge:4"' in out["html"]
    assert out["clicks"] == 1
