"""Settings → Appearance: Auto / Light / Dark (2026-09-24).

The choice is saved per device (localStorage) and applied by one small
script in the <head> of every page that loads theme.css, which puts
data-theme="light" or "dark" on <html> (or nothing, for Auto)
before the first paint. What this file pins:

  - every `@media (prefers-color-scheme: dark)` block is followed by a
    forced-dark twin — the same rules under `[data-theme="dark"]` instead
    of `:not([data-theme="light"])` — and the two never drift;
  - every component rule inside a dark block is guarded so a dark phone
    with Light chosen gets full light (only the token block and the guarded
    selectors may live in there — no bare selectors, no @keyframes);
  - the head script is on every themed page, byte-identical, and not on
    share.html (which stays light for every recipient);
  - the Settings row exists with the approved words;
  - the head script itself, run under node: forced choices set the
    attribute and point the theme-color tags at the forced colour, Match my
    phone restores both, and a throwing localStorage still renders.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

import nodeharness

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"

THEMED_PAGES = [
    "shell.html", "login.html", "onboarding.html", "plan-week.html",
    "meal-setup.html", "chores-setup.html", "member-share.html",
    "not-found.html", "inventory.html",
]
DARK_FILES = ["theme.css", "shell.css", "login.html", "inventory.html"]

MATCH = ':root:not([data-theme="light"])'
FORCED = ':root[data-theme="dark"]'
GUARD = ':where(:root:not([data-theme="light"])) '

HEAD_RE = re.compile(r"<script>\s*\(function \(\) \{\s*var root = document\.documentElement;.*?</script>", re.DOTALL)


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


@pytest.mark.parametrize("name", THEMED_PAGES)
def test_the_head_script_is_on_every_themed_page(name):
    html = _read(name)
    head = html[: html.index("</head>")]
    m = HEAD_RE.search(head)
    assert m, f"{name} is missing the Appearance head script"
    assert m.group(0) == HEAD_RE.search(_read("shell.html")).group(0), f"{name}'s copy has drifted from shell.html's"
    # Before any stylesheet but theme.css can paint over it, and after the
    # theme-color tags it re-points.
    if '<meta name="theme-color"' in head:
        assert head.rindex('<meta name="theme-color"') < m.start()
    assert 'href="/static/theme.css"' in head[: m.start()]


def test_every_page_that_loads_theme_css_is_covered():
    pages = {p.name for p in STATIC.glob("*.html") if "/static/theme.css" in p.read_text(encoding="utf-8")}
    assert pages - {"share.html"} == set(THEMED_PAGES)


def test_share_page_stays_light_with_no_head_script():
    share = _read("share.html")
    assert '<html lang="en" data-theme="light">' in share
    assert "pomonaAppearance" not in share
    assert "pomona-appearance" not in share


def test_the_settings_row():
    js = _read("shell.js")
    assert "appearanceRowHtml() +" in js
    assert "'<span class=\"prefs-row-title\" id=\"prefs-appearance-title\">Appearance</span>'" in js
    labels = re.findall(r"\{ key: '(phone|light|dark)', label: '([^']+)' \}", js)
    assert labels == [("phone", "Auto"), ("light", "Light"), ("dark", "Dark")]
    # The existing segmented control, not a new component.
    assert '<div class="wk-seg" role="radiogroup"' in js
    # Every storage touch is wrapped.
    fn = js[js.index("function appearanceChoice"):js.index("function appearanceRowHtml")]
    assert fn.count("localStorage") == 3 and fn.count("try {") == 2


# ---------- the head script itself, under node ----------

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the head script")


def _head_js() -> str:
    m = HEAD_RE.search(_read("shell.html"))
    return m.group(0)[len("<script>"):-len("</script>")]


def _run(saved, then=None, storage_throws=False):
    harness = """
    function El(attrs) { this.a = Object.assign({}, attrs); }
    El.prototype.setAttribute = function (k, v) { this.a[k] = String(v); };
    El.prototype.getAttribute = function (k) { return k in this.a ? this.a[k] : null; };
    El.prototype.hasAttribute = function (k) { return k in this.a; };
    El.prototype.removeAttribute = function (k) { delete this.a[k]; };
    var html = new El({});
    var metas = [
      new El({ name: 'theme-color', content: '#1B3328', media: '(prefers-color-scheme: light)' }),
      new El({ name: 'theme-color', content: '#22422F', media: '(prefers-color-scheme: dark)' })
    ];
    global.document = { documentElement: html, querySelectorAll: function () { return metas; } };
    var listeners = {};
    global.window = { addEventListener: function (k, f) { listeners[k] = f; } };
    var SAVED = %s, THROWS = %s, THEN = %s;
    Object.defineProperty(global.window, 'localStorage', { get: function () {
      if (THROWS) throw new Error('SecurityError');
      return { getItem: function () { return SAVED; } };
    } });
    %s
    function snap() { return { theme: html.getAttribute('data-theme'), metas: metas.map(function (m) { return m.getAttribute('content'); }) }; }
    var out = { first: snap() };
    if (THEN !== undefined) { window.pomonaAppearance(THEN); out.then = snap(); }
    listeners.storage({ key: 'pomona-appearance', newValue: 'dark' });
    out.storage = snap();
    console.log(JSON.stringify(out));
    """ % (json.dumps(saved), json.dumps(storage_throws), "undefined" if then is None else json.dumps(then), _head_js())
    res = nodeharness.run_node(harness, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip())


@_needs_node
def test_match_my_phone_changes_nothing():
    out = _run(None)
    assert out["first"] == {"theme": None, "metas": ["#1B3328", "#22422F"]}


@_needs_node
def test_forced_dark_and_light_set_the_attribute_and_status_bar():
    assert _run("dark")["first"] == {"theme": "dark", "metas": ["#22422F", "#22422F"]}
    assert _run("light")["first"] == {"theme": "light", "metas": ["#1B3328", "#1B3328"]}


@_needs_node
def test_back_to_match_my_phone_restores_both():
    out = _run("dark", then="phone")
    assert out["then"] == {"theme": None, "metas": ["#1B3328", "#22422F"]}


@_needs_node
def test_another_tab_choosing_carries_over():
    assert _run(None)["storage"] == {"theme": "dark", "metas": ["#22422F", "#22422F"]}


@_needs_node
def test_blocked_storage_still_renders_as_match_my_phone():
    out = _run("dark", storage_throws=True)
    assert out["first"] == {"theme": None, "metas": ["#1B3328", "#22422F"]}


@_needs_node
def test_junk_in_storage_is_match_my_phone():
    assert _run("sepia")["first"]["theme"] is None
