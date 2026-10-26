"""
docs/APP_STORE_LISTING.md: the words that go into App Store Connect.
Light checks only — the words are Emily's to approve, not this file's.
"""
import os
import re

import create_demo_household as demo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LISTING = os.path.join(ROOT, "docs", "APP_STORE_LISTING.md")


def _text():
    with open(LISTING, encoding="utf-8") as f:
        return f.read()


def _section(text, heading_start):
    start = text.index(heading_start)
    nxt = re.search(r"^## ", text[start + 3:], re.M)
    return text[start:start + 3 + nxt.start()] if nxt else text[start:]


def test_testflight_section_exists_and_says_it_uses_claude():
    tf = _section(_text(), "## 11. TestFlight")
    assert "DRAFT" in tf and "Emily approves" in tf
    assert "Beta App Description" in tf and "What to Test" in tf
    # Matches the in-app "Sharing with Claude" screen.
    assert "I use Claude, an AI made by Anthropic" in tf
    assert "[feedback email" in tf


def test_review_notes_match_the_real_sign_in():
    notes = _section(_text(), "## 9. App Review notes")
    with open(os.path.join(ROOT, "static", "login.html"), encoding="utf-8") as f:
        login = f.read()
    for label in ("Sign in with a household passphrase", "Come in"):
        assert label in login, f"login.html no longer says {label!r}"
        assert label in notes
    assert f'tap "{demo._ADULT_NAMES[0]}"' in notes
