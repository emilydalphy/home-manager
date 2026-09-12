"""
Design hygiene pass (2026-09-12) — Loop Board "Design hygiene: hand-written
colours, two emoji, three legacy pages" (Emily: "go ahead and run the design
hygiene"). Three guardrails so the pass stays true rather than being a
one-time cleanup that quietly rots:

  (a) no literal hex colour outside theme.css and outside a comment, in the
      live front-end files DESIGN_SYSTEM.md §1 rule 9 governs (a literal is
      "a review failure" there — see that file's own grep check),
  (b) no emoji/pictographic code point anywhere in static/ (DESIGN_SYSTEM.md
      §2 rule 7: "Icons are inline stroke SVG, never emoji"),
  (c) the legacy pages this pass deleted (nothing live reached any of
      them — grocery.html/cooker.html/kitchen.html/memory.html went with
      Grocery/Cook/Kitchen/What-we-know going native, index.html with the
      app-shell redesign) never reappear, on disk or as a route.

A small, explicit ALLOWLIST covers exceptions this pass found and left on
purpose rather than silently inventing a token for: `<meta name="theme-color">`
(a meta tag's content attribute cannot reference a CSS var()) and a handful
of literals with no exact-value token twin, each with its own comment at the
call site explaining why (see the report from the pass, or grep the file for
"no exact token twin" / "no token twin" / "flagged for Emily"). Growing this
allowlist should be rare and each addition should carry that same kind of
comment at its call site — it is not a place to dump a new literal instead
of reaching for a token.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
MAIN_PY = (REPO / "app" / "main.py").read_text()

# ---------------------------------------------------------------------------
# (a) literal hex colours
# ---------------------------------------------------------------------------

# The exact files DESIGN_SYSTEM.md's colour-hygiene pass covers. theme.css
# itself is exempt (rule 9's own carve-out: "outside theme.css" is where a
# literal is a review failure — new tokens are defined inside it).
LIVE_FILES = [
    "shell.css", "shell.js", "shell.html", "login.html", "onboarding.html",
    "plan-week.html", "meal-setup.html", "share.html", "member-share.html",
    "chores-setup.html", "inventory.html",
]

# `&#9733;`-style HTML numeric entities (a plain "★" written as an entity)
# are not colours; the negative lookbehind keeps this from matching the
# digits inside one.
HEX_RE = re.compile(r"(?<!&)#[0-9a-fA-F]{3,8}\b")

# Exact literals left in place because they have no exact-value token twin
# (verified against every token in theme.css, including its dark-mode
# redefinitions) — each is commented at its call site. Keyed by filename,
# value is the set of hex strings (as they appear in source, case as
# written) allowed to remain literal in that file.
NO_TOKEN_TWIN = {
    "shell.css": {"#fff", "#D9C9AF"},
    "login.html": {"#2E5240"},
    "plan-week.html": {"#fff"},
    "meal-setup.html": {"#fff"},
    "member-share.html": {"#fff"},
    "chores-setup.html": {"#fff"},
    "share.html": {"#fff"},
    # #inv-toast's color: #FFFDF8 predates this pass and already carries its
    # own "literal on purpose... correct in BOTH modes" comment.
    "inventory.html": {"#FFFDF8"},
}

# Exact literals inside a `<meta name="theme-color" ...>` tag. A meta tag's
# content attribute cannot reference a CSS var(), so these always match
# --spruce's light/dark value verbatim rather than being tokenised.
THEME_COLOR_RE = re.compile(r'<meta\s+name="theme-color"[^>]*>')


def _strip_comments(name: str, text: str) -> str:
    """Best-effort comment stripping, permissive enough not to false-flag
    real code as a comment, strict enough not to hide a real literal inside
    one. CSS/JS block comments and HTML comments are unambiguous; JS/HTML
    `//` line comments are only stripped when not immediately preceded by
    `:`, so a `https://` or `http://` URL earlier on the same line (common
    in these files' <head> <link> tags) is never mistaken for one.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    if name.endswith((".js", ".html")):
        text = re.sub(r"(?<!:)//[^\n]*", "", text)
    return text


def _theme_color_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in THEME_COLOR_RE.finditer(text)]


def _offending_literals(name: str) -> list[str]:
    raw = (STATIC / name).read_text(encoding="utf-8")
    code = _strip_comments(name, raw)
    allowed = NO_TOKEN_TWIN.get(name, set())
    theme_color_spans = _theme_color_spans(code)

    offenders = []
    for m in HEX_RE.finditer(code):
        if any(start <= m.start() < end for start, end in theme_color_spans):
            continue  # <meta name="theme-color">: no var() allowed there
        if m.group(0) in allowed:
            continue
        offenders.append(m.group(0))
    return offenders


@pytest.mark.parametrize("name", LIVE_FILES)
def test_no_unexplained_literal_hex_colour(name):
    offenders = _offending_literals(name)
    assert not offenders, (
        f"static/{name} has literal hex colour(s) {offenders} outside a "
        f"comment, theme.css, the theme-color meta tag, and the "
        f"NO_TOKEN_TWIN allowlist. DESIGN_SYSTEM.md §2 rule 9: point it at "
        f"an existing token, or — if none matches — add a new one to "
        f"theme.css (Tier 2, Emily's call) rather than hardcoding it."
    )


def test_the_allowlist_has_no_dead_entries():
    """Keeps NO_TOKEN_TWIN honest: an entry that no longer matches anything
    in its file (the literal was tokenised, or the rule was deleted) should
    be removed, not left to quietly widen what future literals slip past."""
    stale = []
    for name, hexes in NO_TOKEN_TWIN.items():
        raw = (STATIC / name).read_text(encoding="utf-8")
        code = _strip_comments(name, raw)
        for h in hexes:
            if h not in code:
                stale.append((name, h))
    assert not stale, f"NO_TOKEN_TWIN entries no longer found in their file: {stale}"


def test_theme_css_still_defines_the_tokens_the_allowlist_reasons_about():
    """A sanity check on the allowlist's own reasoning, not a live rule:
    if these tokens ever change value, re-verify NO_TOKEN_TWIN's comments
    (each says why the literal can't just point at the nearest token)."""
    theme = (STATIC / "theme.css").read_text(encoding="utf-8")
    for token in ("--ivory-ink", "--urgent-ink", "--celadon-edge", "--spruce"):
        assert f"{token}:" in theme, f"{token} is gone from theme.css — re-check NO_TOKEN_TWIN"


# ---------------------------------------------------------------------------
# (b) emoji / pictographic code points
# ---------------------------------------------------------------------------

# Real emoji/pictographic Unicode blocks — deliberately NOT the arrow block
# (2190-21FF; "Plan the week →" and friends are plain typographic arrows,
# not emoji) and not Mathematical Operators (a minus sign, an ellipsis).
EMOJI_RANGES = [
    (0x1F000, 0x1FFFF),  # emoji & symbols supplementary planes
    (0x2600, 0x26FF),    # misc symbols
    (0x2700, 0x27BF),    # dingbats
    (0x1F1E6, 0x1F1FF),  # regional indicators (flags)
    (0xFE0F, 0xFE0F),    # variation selector-16 (emoji presentation)
    (0x200D, 0x200D),    # ZWJ (emoji sequences)
]

# Two plain dingbat characters already in the app, reviewed as part of this
# pass and NOT emoji in the sense rule 7 means (colourful pictographs) —
# a tick mark (aria-hidden decorative span) and a code comment's "★". Any
# OTHER character in the ranges above still fails.
EMOJI_ALLOWLIST = {"✓", "★"}  # ✓ ★

# The two entities the Loop Board card named as already removed
# (2026-09-12, the desktop rail). Kept as an explicit regression check
# rather than folded into the general scan, since an HTML entity's code
# point (127823, 9998) doesn't appear as those literal digits anywhere
# else innocently.
NAMED_REMOVED_ENTITIES = ("&#127823;", "&#9998;")


def _is_emoji(ch: str) -> bool:
    if ch in EMOJI_ALLOWLIST:
        return False
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in EMOJI_RANGES)


def test_no_emoji_anywhere_in_static():
    offenders = []
    for path in sorted(STATIC.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue  # binary asset (icon, font, image) — not source
        for i, line in enumerate(text.splitlines(), 1):
            bad = [ch for ch in line if _is_emoji(ch)]
            if bad:
                offenders.append(f"{path.relative_to(STATIC)}:{i} {bad!r}")
    assert not offenders, (
        "emoji/pictographic code point(s) found — DESIGN_SYSTEM.md §2 rule "
        f"7, 'never emoji': {offenders}"
    )


def test_the_named_removed_emoji_entities_do_not_come_back():
    for path in sorted(STATIC.rglob("*.html")) + sorted(STATIC.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for entity in NAMED_REMOVED_ENTITIES:
            assert entity not in text, f"{entity} is back in {path.relative_to(STATIC)}"


# ---------------------------------------------------------------------------
# (c) the deleted legacy pages stay deleted
# ---------------------------------------------------------------------------

DELETED_PAGES = ["grocery.html", "cooker.html", "kitchen.html", "memory.html", "index.html"]
# inventory.html is the one page that STAYS embedded (deferred beta feature,
# Emily's call) — a negative-control so this test would catch itself
# deleting the wrong thing.
KEPT_PAGES = ["inventory.html", "share.html", "member-share.html"]


@pytest.mark.parametrize("name", DELETED_PAGES)
def test_deleted_legacy_page_does_not_reappear(name):
    assert not (STATIC / name).exists(), (
        f"static/{name} is back. It was deleted in the design hygiene pass "
        f"(2026-09-12) because nothing live reached it — re-check the same "
        f"grep sweep (static/, app/, tests/, *.md for the filename and any "
        f"route) before restoring it."
    )


@pytest.mark.parametrize("name", KEPT_PAGES)
def test_kept_page_was_not_swept_up_by_mistake(name):
    assert (STATIC / name).exists(), f"static/{name} should still exist — see DELETED_PAGES above"


def test_the_memory_and_cooker_routes_are_gone():
    """/memory served memory.html directly; /cooker redirected into the
    shell purely as an old-bookmark shim for the now-deleted cooker.html.
    Both routes are gone. /grocery, /kitchen, /week and / are NOT in this
    list on purpose: despite the name collision with the deleted pages,
    those four are live SPA deep-link routes the shell itself pushState()s
    to (TABS in shell.js, SHELL_ROUTES in service-worker.js) and always
    served shell.html, never the legacy files — deleting them would break
    reloading the Shop/Cook/Plan tabs, a real regression this test must not
    cause by being overzealous."""
    assert '@app.get("/memory")' not in MAIN_PY
    assert '@app.get("/cooker")' not in MAIN_PY
    for still_live in ('@app.get("/")', '@app.get("/week")', '@app.get("/grocery")', '@app.get("/kitchen")'):
        assert still_live in MAIN_PY, f"{still_live} should still be a live shell route"


def test_inventory_route_still_serves_the_kept_page():
    assert '@app.get("/inventory")' in MAIN_PY
    idx = MAIN_PY.index('@app.get("/inventory")')
    body = MAIN_PY[idx: idx + 300]
    assert 'inventory.html' in body
