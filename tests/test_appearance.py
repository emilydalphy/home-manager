"""Light mode only for now (Emily, 2026-11-05), and dark ready to come back.

Settings → Appearance is gone. Every page that loads theme.css carries
data-theme="light" on its <html>, in the markup, so no script has to run
before first paint. That one attribute is what switches dark off, because
every dark rule is guarded by it. What this file pins:

  - every themed page is light, with one cream theme-color and no leftover
    Appearance script or control;
  - every `@media (prefers-color-scheme: dark)` block is still followed by a
    forced-dark twin, the same rules under `[data-theme="dark"]` instead of
    `:not([data-theme="light"])`, and the two never drift;
  - every component rule inside a dark block is guarded, so a dark phone gets
    full light (only the token block and the guarded selectors may live in
    there: no bare selectors, no @keyframes).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"

THEMED_PAGES = [
    "shell.html", "login.html", "onboarding.html", "plan-week.html",
    "chores-setup.html", "member-share.html",
    "not-found.html", "inventory.html", "join.html", "goodbye.html",
    "legal/privacy.html", "legal/terms.html", "legal/support.html",
]
# onboarding.html: its "Who's eating, and when?" grid's "some of you" cell (2026-09-30).
DARK_FILES = ["theme.css", "shell.css", "inventory.html"]

MATCH = ':root:not([data-theme="light"])'
FORCED = ':root[data-theme="dark"]'
GUARD = ':where(:root:not([data-theme="light"])) '


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _norm(css: str) -> str:
    return re.sub(r"\s+", " ", _strip_comments(css)).strip()


def _close(text: str, open_brace: int) -> int:
    depth = 0
    for j in range(open_brace, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    raise AssertionError("unbalanced braces")


def _dark_blocks(text: str):
    """(inner, rest-after-block) for every prefers-color-scheme: dark block."""
    out = []
    for m in re.finditer(r"@media \(prefers-color-scheme: dark\) \{", text):
        end = _close(text, m.end() - 1)
        out.append((text[m.end():end], text[end + 1:]))
    return out


def test_every_dark_file_is_known():
    """A new `prefers-color-scheme: dark` style block anywhere in static/
    has to come through here and get its twin."""
    found = set()
    for p in STATIC.iterdir():
        if p.suffix in (".css", ".html") and "@media (prefers-color-scheme: dark)" in p.read_text(encoding="utf-8"):
            found.add(p.name)
    assert found == set(DARK_FILES)


@pytest.mark.parametrize("name", DARK_FILES)
def test_every_dark_block_has_a_forced_dark_twin(name):
    text = _read(name)
    blocks = _dark_blocks(text)
    assert blocks, f"{name} has no dark block"
    for inner, rest in blocks:
        expected = _norm(inner.replace(MATCH, FORCED))
        assert expected, f"empty dark block in {name}"
        assert _norm(rest).startswith(expected), (
            f"static/{name}: a dark-mode block isn't followed by its forced-dark "
            f"twin (the same rules with {FORCED} in place of {MATCH}). Change "
            f"both together."
        )


def test_the_token_twin_carries_every_dark_token():
    theme = _read("theme.css")
    inner, _ = _dark_blocks(theme)[0]
    decls = re.findall(r"(--[\w-]+|color-scheme):\s*([^;]+);", _strip_comments(inner))
    twin_at = theme.index(FORCED + " {")
    twin = theme[twin_at:_close(theme, theme.index("{", twin_at)) + 1]
    assert re.findall(r"(--[\w-]+|color-scheme):\s*([^;]+);", twin) == decls
    assert ("color-scheme", "dark") in decls


@pytest.mark.parametrize("name", DARK_FILES)
def test_dark_rules_are_guarded_against_forced_light(name):
    """A phone in dark with Light chosen must be fully light: every
    selector in a dark block carries the :not([data-theme="light"]) guard."""
    for inner, _ in _dark_blocks(_read(name)):
        body = _strip_comments(inner)
        assert "@keyframes" not in body, f"{name}: a @keyframes can't be guarded — name a dark one outside the block"
        for sel in re.findall(r"([^{}@]+)\{[^{}]*\}", body):
            for part in sel.split(","):
                part = part.strip()
                if name == "theme.css":
                    assert part == MATCH, f"{name}: unguarded dark selector {part!r}"
                else:
                    assert part.startswith(GUARD), f"{name}: unguarded dark selector {part!r}"


@pytest.mark.parametrize("name", THEMED_PAGES + ["share.html"])
def test_every_page_is_light_in_its_markup(name):
    html = _read(name)
    assert '<html lang="en" data-theme="light">' in html, name
    assert "pomona-appearance" not in html and "pomonaAppearance" not in html, name
    head = html[: html.index("</head>")]
    assert 'media="(prefers-color-scheme' not in head, f"{name}: one theme-color, not a light and a dark one"
    if '<meta name="theme-color"' in head:
        assert '<meta name="theme-color" content="#FFFAF3" />' in head, f"{name}: the status bar is cream, no band"


def test_every_page_that_loads_theme_css_is_covered():
    pages = {p.relative_to(STATIC).as_posix() for p in [*STATIC.glob("*.html"), *STATIC.glob("legal/*.html")] if "/static/theme.css" in p.read_text(encoding="utf-8")}
    assert pages - {"share.html"} == set(THEMED_PAGES)


def test_the_appearance_control_is_gone():
    js = _read("shell.js")
    for dead in ("appearanceRowHtml", "setAppearance", "APPEARANCE_OPTIONS", "pomona-appearance", "pomonaAppearance", "data-appearance"):
        assert dead not in js, dead
    assert 'id="prefs-appearance-title"' not in js


def test_light_mode_is_stated_where_dark_would_come_back():
    theme = _read("theme.css")
    assert "LIGHT MODE ONLY FOR NOW" in theme
    assert "color-scheme: light;" in theme


def test_the_installed_app_is_cream_too():
    manifest = _read("manifest.json")
    assert '"background_color": "#FFFAF3"' in manifest and '"theme_color": "#FFFAF3"' in manifest
    capacitor = (REPO / "ios-app" / "capacitor.config.json").read_text(encoding="utf-8")
    assert "#1B3328" not in capacitor and '"style": "LIGHT"' in capacitor
