"""
Bright Dusk, stage 4 (Emily approved design_handoff_bright_dusk/, 2026-11-05):
the welcome, sign-in, setup, Settings and every remaining page.

The boards are the source of truth. This pins what a board settles and a
later edit could quietly undo: her words, the shape of Settings and where
every item from the old list went. Older files pin the behaviour.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
ONBOARDING = (STATIC / "onboarding.html").read_text(encoding="utf-8")
LOGIN = (STATIC / "login.html").read_text(encoding="utf-8")
SHELL_JS = (STATIC / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (STATIC / "shell.css").read_text(encoding="utf-8")


def _render_prefs() -> str:
    start = SHELL_JS.index("function renderPrefsRows()")
    return SHELL_JS[start:SHELL_JS.index("// ---------- Household name", start)]


# ---------- the welcome ----------


@pytest.mark.parametrize("word,line", [
    ("Plan", "Leave making your weekly plans to me"),
    ("Shop", "One shopping list generated from your custom plan"),
    ("Kitchen", "Keeping track of what needs to be used"),
    ("Cook", "Step-by-step recipe guides"),
])
def test_the_welcome_says_her_words_exactly(word, line):
    assert re.search(rf"<b>{word}</b></div><p>{re.escape(line)}</p>", ONBOARDING)


def test_the_welcomes_plates_come_from_the_library():
    for key in ("chicken-jalfrezi", "beef-chow-fun", "albondigas-soup", "pork-tacos-with-salsa-verde"):
        assert f'src="/static/plates/{key}.svg"' in ONBOARDING
        assert (STATIC / "plates" / f"{key}.svg").exists()


# ---------- sign-in ----------


def test_the_sign_in_is_z_signin():
    assert "This one&rsquo;s just for your household" not in LOGIN, "Emily asked for this to go"
    assert "signin-plaque" not in LOGIN
    assert '<h1 class="signin-wordmark">Pomona</h1>' in LOGIN
    assert LOGIN.count('class="btn-main"') == 3, "email, code, passphrase: one apricot each"
    assert 'class="field"' in LOGIN
    assert "By continuing you agree to the" in LOGIN


def test_sign_in_keeps_the_two_placeholders_once_each():
    assert LOGIN.count("<!--ERROR-->") == 1
    assert LOGIN.count("__NEXT__") == 1


# ---------- setup ----------


def test_every_question_has_its_bar_before_its_title():
    assert ONBOARDING.count('class="q-progress"') == ONBOARDING.count('class="q-eyebrow"')
    assert ONBOARDING.count('class="round-btn back-link"') >= 20
    assert 'class="back-link' not in ONBOARDING


def test_the_second_cook_ahead_option_reads_as_drawn():
    assert ('<span class="q-opt-title">Yes, I have a prep day</span>'
            '<span class="q-opt-desc">I&rsquo;ll plan a batch cooking schedule.</span>') in ONBOARDING


def test_setup_has_no_old_colours_italics_or_caps():
    css = ONBOARDING[ONBOARDING.index("<style>"):ONBOARDING.index("</style>")]
    for gone in ("--spruce", "--ivory-ink", "--celadon", "italic", "text-transform", "var(--sand"):
        assert gone not in css, gone
    sizes = {int(n) for n in re.findall(r"font-size:\s*(\d+)px", css)}
    assert sizes <= {32, 26, 16, 14, 13, 11}, sizes


# ---------- Settings ----------


def test_settings_is_six_tiles_and_help():
    render = _render_prefs()
    hub = re.findall(r"prefsHubTileHtml\('([^']+)', '([^']+)'", render)
    assert hub == [
        ("Household", "Who’s here, name, what I’m holding"),
        ("Food", "How you eat, regulars"),
        ("Your week", "Rhythm, prep days, calendar"),
        ("Stores", "Where you shop"),
        ("Reminders", "Morning text"),
        ("Account", "Email, sharing, sign out"),
        ("Help", "Support, privacy, terms"),
    ]


@pytest.mark.parametrize("needle", [
    "prefsSectionTileHtml('people', 'Who’s here')",
    "householdNameRowHtml()",
    "prefsSectionTileHtml('holding', 'Holding for you')",
    "prefsSectionTileHtml('taste', 'How you eat')",
    "prefsSectionTileHtml('wont-eat', 'Won’t eat')",
    "prefsSectionTileHtml('regulars', 'Regulars')",
    "recipesPrefsRowHtml()",
    "prefsSectionTileHtml('rhythm', 'Your rhythm')",
    "prefsSectionTileHtml('prep-days', 'Prep days')",
    "name: 'Your calendar'",
    "data-section=\"stores\"",
    "name: 'Morning text'",
    "whoPrefsRowHtml()",
    "accountEmailRowHtml()",
    "aiConsentPrefsRowHtml()",
    "name: 'Sign out'",
    "name: 'Delete your household'",
    "snwTile()",
    "name: 'How to ask me for things'",
    "name: 'Show me around again'",
    "prefsLinkTileHtml('/support', 'Help and support')",
    "prefsLinkTileHtml('/privacy', 'Privacy policy')",
    "prefsLinkTileHtml('/terms', 'Terms of use')",
])
def test_everything_from_the_live_list_is_still_reachable(needle):
    assert needle in _render_prefs()


def test_appearance_and_add_to_home_screen_are_gone():
    render = _render_prefs()
    assert "Appearance" not in render and "home screen" not in render.lower()


def test_a_section_from_settings_is_its_own_screen():
    assert "wwkState.only = fromSettings ? section : null;" in SHELL_JS
    assert "kitSheetEl.classList.toggle('is-screen', fromSettings);" in SHELL_JS


def test_settings_stays_under_what_opens_from_it_and_goes_when_the_stack_does():
    assert "stays: true," in SHELL_JS[SHELL_JS.index("prefs: {"):][:200]
    forget = SHELL_JS[SHELL_JS.index("function forgetSheetLevels()"):][:400]
    assert "level.close()" in forget


def test_nothing_uses_a_bare_confirm_any_more():
    assert "window.confirm(" not in SHELL_JS
    assert "function openConfirmSheet(title, action, onYes, line)" in SHELL_JS


def test_the_sheets_one_look_rule_is_not_lost_to_a_stray_comment():
    """A doubled comment ending swallowed the shared sheet rule's selector."""
    assert "*/SHEETS" not in SHELL_CSS


# ---------- small fixes and the cleanup ----------


def test_asks_mic_is_44_and_its_field_shows_focus():
    mic = SHELL_CSS[SHELL_CSS.rindex(".ask-composer-mic {"):]
    assert "width: 44px" in mic[:mic.index("}")] and "height: 44px" in mic[:mic.index("}")]
    assert ".ask-composer-field:focus-within { outline: 2px solid var(--dusk);" in SHELL_CSS


@pytest.mark.parametrize("gone", ["iconTileHtml", "slotIconTileHtml", "prepSectionTileHtml"])
def test_the_dead_builders_are_gone(gone):
    assert gone not in SHELL_JS


@pytest.mark.parametrize("page", ["goodbye.html", "not-found.html", "join.html", "share.html", "member-share.html", "legal.css"])
def test_the_remaining_pages_are_on_the_six_sizes(page):
    text = (STATIC / page).read_text(encoding="utf-8")
    css = text[text.index("<style>"):text.index("</style>")] if page.endswith(".html") else text
    sizes = {int(n) for n in re.findall(r"font-size:\s*(\d+)px", css)}
    assert sizes <= {32, 26, 16, 14, 13, 11}, sizes
    assert "rem;" not in css, "sizes are px on the scale"
    assert "text-transform: uppercase" not in css
