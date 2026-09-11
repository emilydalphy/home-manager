"""The chat is an icon, not a bar (Emily, 2026-09-11; DESIGN_SYSTEM §2b S2).

Build 1 of the screen-by-screen redesign. The always-open ask bar above the
tab bar is gone; one round button opens the sheet; the example prompts and
the "?" live inside the sheet; the bell moved beside each root's gear.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
DESIGN = (REPO / "DESIGN_SYSTEM.md").read_text(encoding="utf-8")


def test_the_bar_is_gone_and_one_icon_opens_the_sheet():
    assert 'id="ask-bar"' not in SHELL_HTML
    assert 'class="ask-placeholder"' not in SHELL_HTML
    assert SHELL_HTML.count('id="chat-fab"') == 1
    # The icon is wired to the same open path the bar used.
    assert "document.getElementById('chat-fab')" in SHELL_JS
    assert "askBar.addEventListener('click', function () { openAskSheet(); });" in SHELL_JS


def test_the_icon_floats_bottom_right_and_the_dock_clears_it():
    fab = SHELL_CSS[SHELL_CSS.index(".chat-fab {"):]
    fab = fab[:fab.index("}")]
    assert "position: absolute;" in fab and "right: 16px;" in fab and "bottom: 12px;" in fab
    assert "width: 54px;" in fab and "height: 54px;" in fab  # over the 44px floor
    dock = SHELL_CSS[SHELL_CSS.index(".dock {"):]
    dock = dock[:dock.index("}")]
    assert "padding: 8px 84px 14px 20px;" in dock
    # Desktop hides the icon (the Ask column replaces the sheet) and takes the
    # clearance back.
    desk = SHELL_CSS[SHELL_CSS.rindex("@media (min-width: 1024px) {"):]
    assert ".dock { padding-right: 20px; }" in desk


def test_one_line_for_the_composer_on_every_tab():
    assert "_default: 'What\\u2019s on your mind?'" in SHELL_JS
    for old in ("today: 'Ask me anything", "week: 'Tweak this week", "kitchen: 'What", "grocery: 'Add oat milk"):
        assert old not in SHELL_JS, old
    assert SHELL_HTML.count('placeholder="What&rsquo;s on your mind?"') == 1
    assert 'placeholder="What&rsquo;s on your mind?" autocomplete="off"' in SHELL_JS  # the desktop column
    assert 'Ask me anything about today&hellip;' not in SHELL_JS
    # The sheet's title says the same thing.
    assert '<div class="ask-sheet-title">What&rsquo;s on your mind?</div>' in SHELL_HTML
    assert "Tell me what to change" not in SHELL_HTML


def test_prompts_and_tips_live_inside_the_sheet():
    sheet = SHELL_HTML[SHELL_HTML.index('id="ask-sheet"'):SHELL_HTML.index('id="week-sheet-scrim"')]
    assert 'id="ask-examples"' in sheet
    assert 'id="ask-tips-btn"' in sheet
    dock = SHELL_HTML[SHELL_HTML.index('id="ask-bar-dock"'):SHELL_HTML.index('id="tab-bar"')]
    assert 'ask-examples' not in dock and 'ask-tips-btn' not in dock and 'bell-home-dock' not in dock
    # In the sheet the examples yield to the named intents at both widths,
    # not only in the desktop column.
    assert "el.id === 'today-ask-examples' ? 'today-ask-chips' : 'ask-chips'" in SHELL_JS


def test_the_bell_has_a_slot_beside_every_gear():
    assert '<span class="bell-slot" data-bell-slot></span>' in SHELL_JS
    assert "'#shell-scroll .tab-panel.active [data-bell-slot]'" in SHELL_JS
    assert "'bell-home-dock'" not in SHELL_JS
    assert ".bell-slot .notif-bell {" in SHELL_CSS


def test_the_design_system_describes_the_icon_not_the_bar():
    assert "The chat is one icon on every screen" in DESIGN
    assert "not yet built" not in DESIGN
