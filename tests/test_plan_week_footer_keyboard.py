"""
The button at the bottom of Plan a week stays above the phone keyboard
(Emily, 2026-09-27).

`.footer` is fixed to bottom: 0, and iOS Safari / the installed app keep
the layout viewport full height when the keyboard opens, so the keyboard
covered Continue while "Anything else?" was being typed. The page now lifts
the footer by the gap between the visual viewport's bottom and the layout
viewport's (liftFooter), only while a text box has focus.

Fails on main 8e046a6: no visualViewport handling anywhere on the page.
"""
from __future__ import annotations

import json
import shutil
import nodeharness
from pathlib import Path

import pytest

from test_same_as_last_week import _extract

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the page's own functions")


@_needs_node
def test_the_lift_is_the_keyboards_height_and_only_while_typing():
    script = _extract("isTextEntry") + "\n" + _extract("keyboardLift") + """
console.log(JSON.stringify({
  keyboard: keyboardLift(812, { height: 476, offsetTop: 0 }, true),
  scrolled: keyboardLift(812, { height: 476, offsetTop: 120 }, true),
  noKeyboard: keyboardLift(812, { height: 812, offsetTop: 0 }, true),
  notTyping: keyboardLift(812, { height: 476, offsetTop: 0 }, false),
  android: keyboardLift(476, { height: 476, offsetTop: 0 }, true),
  noApi: keyboardLift(812, undefined, true),
  textarea: isTextEntry({ tagName: 'TEXTAREA' }),
  text: isTextEntry({ tagName: 'INPUT', type: 'text' }),
  time: isTextEntry({ tagName: 'INPUT', type: 'time' }),
  button: isTextEntry({ tagName: 'BUTTON' }),
  none: isTextEntry(null)
}));"""
    out = json.loads(nodeharness.run_node(script).stdout)
    assert out == {
        "keyboard": 336, "scrolled": 216, "noKeyboard": 0, "notTyping": 0, "android": 0, "noApi": 0,
        "textarea": True, "text": True, "time": False, "button": False, "none": False,
    }


def test_it_listens_to_the_visual_viewport_and_restores_on_blur():
    assert "window.visualViewport.addEventListener('resize', liftFooter);" in PAGE
    assert "window.visualViewport.addEventListener('scroll', liftFooter);" in PAGE
    assert "document.addEventListener('focusin', liftFooter);" in PAGE
    assert "document.addEventListener('focusout', function () { setTimeout(liftFooter, 0); });" in PAGE
    lift = _extract("liftFooter")
    assert "footer.classList.toggle('is-lifted', lift > 0);" in lift
    css = PAGE[PAGE.index("  .footer.is-lifted {"):]
    css = css[:css.index("}")]
    assert "bottom: var(--kb-lift, 0px);" in css
