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
      app-shell redesign) never reappear, on disk or as a route,
  (d) one stroke width for the whole icon set (Emily, 2026-09-13, Identity
      Round Q4 = B): every inline `<svg` with stroke="currentColor" in the
      live files carries stroke-width="2.2" — except the Pomona mark, a
      logo rather than an icon, which keeps the weight it was drawn at.

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
    # Emptied 2026-09-13: the "#fff on spruce", trip-checkbox rim and sign-in
    # field rim literals became --on-spruce-ink, --hairline-deep and
    # --on-spruce-edge (Emily's call on the hygiene card).
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
# (d) one stroke width for the icon set
# ---------------------------------------------------------------------------

ICON_STROKE = "2.2"

# The mark's first path. An SVG that draws the mark is a logo, not an icon
# (DESIGN_SYSTEM.md §2 rule 7: "the mark is 1.8"), so it is exempt from the
# one-width rule: 1.8 in the root band and on the chat button, 1.7 on the
# sign-in plaque and the desktop field mark, 1.6 on the welcome screens'
# large glyphs — each as drawn. shell.js builds its copy of the mark from
# the MARK_PATHS constant (markSvg), so that tag's contents are the
# constant's name rather than the path — allowed by the same token.
MARK_FIRST_PATH = "M12 20.4c-3.1"
MARK_PATHS_TOKEN = "MARK_PATHS"

SVG_TAG_RE = re.compile(r"<svg\b[^>]*>")


def _stroke_svgs(name: str):
    """Every inline stroke SVG in a live file: (line, opening tag, contents)."""
    raw = (STATIC / name).read_text(encoding="utf-8")
    found = []
    for m in SVG_TAG_RE.finditer(raw):
        tag = m.group(0)
        if 'stroke="currentColor"' not in tag:
            continue  # a fill-only glyph (the mic) — no stroke to weigh
        end = raw.find("</svg>", m.end())
        inner = raw[m.end():end] if end != -1 else ""
        found.append((raw.count("\n", 0, m.start()) + 1, tag, inner))
    return found


def _is_mark(inner: str) -> bool:
    return MARK_FIRST_PATH in inner or MARK_PATHS_TOKEN in inner


@pytest.mark.parametrize("name", LIVE_FILES)
def test_every_stroke_icon_is_drawn_at_the_one_width(name):
    if name == "shell.css":
        return  # the CSS draws no inline SVG (stroke-width never appears in it)
    offenders = []
    for line, tag, inner in _stroke_svgs(name):
        if _is_mark(inner):
            continue
        m = re.search(r'stroke-width="([^"]*)"', tag)
        width = m.group(1) if m else None
        if width != ICON_STROKE:
            offenders.append(f"line {line}: stroke-width={width!r}")
        # And the caps and joins are round, so the set is one set.
        if 'stroke-linecap="round"' not in tag or 'stroke-linejoin="round"' not in tag:
            offenders.append(f"line {line}: caps/joins are not round")
    assert not offenders, (
        f"static/{name}: {offenders}. DESIGN_SYSTEM.md §2 rule 7 — one "
        f"{ICON_STROKE}px stroke for the whole icon set since 2026-09-13; "
        f"only the Pomona mark (a logo) keeps its own weight."
    )


def test_the_mark_keeps_its_own_weight_and_is_the_only_exception():
    """The exemption stays what it says: the tags it lets through all draw
    the mark, and the mark is never re-weighted to the icon width."""
    marks = []
    for name in LIVE_FILES:
        if name == "shell.css":
            continue
        for line, tag, inner in _stroke_svgs(name):
            if _is_mark(inner):
                m = re.search(r'stroke-width="([^"]*)"', tag)
                marks.append((name, line, m.group(1) if m else None))
    assert marks, "the mark should be drawn somewhere (sign-in, the band, the chat button)"
    for name, line, width in marks:
        assert width in {"1.6", "1.7", "1.8"}, f"{name}:{line} draws the mark at {width!r}, not a logo weight"
    # The band and the chat button draw it at 1.8 (Identity Round, 2026-09-13).
    assert ("shell.js", "1.8") in {(n, w) for n, _, w in marks}
    assert ("shell.html", "1.8") in {(n, w) for n, _, w in marks}


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
