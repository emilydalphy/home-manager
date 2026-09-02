"""
The shell embeds several pages in iframes, and that has a recurring bug.

Every top-level route the shell owns (/, /week, /grocery, /kitchen) serves
shell.html. So an ordinary link to one of them, followed from *inside* an
embedded page, loads a second complete app shell within the frame — two ask
bars and two tab bars stacked on screen. It has been reported three times
and patched three times, each time by hand-copying a
`window.self !== window.top` guard into one more page.

Copying is the actual defect. A page looks completely correct opened on its
own and only breaks inside the shell, so nothing catches a missing guard —
not review, not a browser check of that page, nothing. These tests are the
thing that catches it: a new embedded page, or a new top-level link added to
an existing one, fails here instead of shipping.

Three things below are deliberately derived from the app rather than listed
by hand, because each one is a way this check could quietly stop working:

- **Which routes serve the shell**, including ones that only *redirect* into
  a shell route (/cooker does exactly this). A link to a redirect is just as
  fatal as a link to the destination.
- **Which pages can end up in a frame**, computed as a closure. inventory
  and memory are embeddable only because the Kitchen hub navigates its own
  frame to them — so "what shell.js embeds" is not the whole answer, and it
  was never going to be.
- **Navigation written in JavaScript**, not just in `href`. The Kitchen hub
  navigates *exclusively* by `location.href`, so a check that only read
  anchors would be blind on the one page most likely to grow a new link.

And because a derivation that returns nothing would make every assertion
below pass vacuously, test_the_derivations_are_not_silently_empty pins them.
That is not paranoia: a plain refactor of main.py (hoisting a path into a
variable) empties the route derivation, and was demonstrated to make this
whole file pass with a real bug present.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
MAIN_PY = (REPO / "app" / "main.py").read_text()

# Pages that can end up inside the shell's iframe.
#
# Kept as a literal so the failure message can name it, but
# test_the_embeddable_list_matches_the_app checks it against a closure
# computed from the app, so it cannot fall out of date silently.
EMBEDDABLE = ["grocery", "kitchen", "cooker", "inventory", "memory"]

GUARD_SCRIPT = "/static/embedded-page.js"

# Every way a page can navigate its own frame from script.
_JS_NAV = re.compile(
    r"""(?:location\.href\s*=|location\.assign\(|location\.replace\()\s*['"]([^'"]+)['"]"""
)


def _route_files() -> dict[str, str]:
    """route path -> the static file it serves, read from the app."""
    out = {}
    for chunk in re.split(r"(?m)^@app\.get\(", MAIN_PY)[1:]:
        path = re.match(r'"([^"]+)"', chunk)
        if not path:
            continue
        body = re.split(r"(?m)^@app\.", chunk)[0]
        served = re.search(r'FileResponse\(os\.path\.join\(static_dir,\s*"([^"]+)"\)', body)
        if served:
            out[path.group(1)] = served.group(1)
    return out


def _route_redirects() -> dict[str, str]:
    """route path -> the route it redirects to."""
    out = {}
    for chunk in re.split(r"(?m)^@app\.get\(", MAIN_PY)[1:]:
        path = re.match(r'"([^"]+)"', chunk)
        if not path:
            continue
        body = re.split(r"(?m)^@app\.", chunk)[0]
        target = re.search(r'RedirectResponse\(url="([^"]+)"', body)
        if target:
            out[path.group(1)] = target.group(1)
    return out


def _shell_routes() -> set[str]:
    """
    Routes that land the browser on the whole app shell — directly, or via
    a redirect chain.

    /cooker is the reason the redirect half exists: it 302s to /kitchen,
    which serves shell.html. Following it from inside a frame doubles the
    shell exactly like a direct link would, and static/index.html already
    ships that very link.
    """
    files, redirects = _route_files(), _route_redirects()
    direct = {r for r, f in files.items() if f == "shell.html"}
    resolved = set(direct)
    for route, target in redirects.items():
        seen, hop = set(), target
        while hop in redirects and hop not in seen:
            seen.add(hop)
            hop = redirects[hop]
        if hop in direct:
            resolved.add(route)
    return resolved


def _page_frame_targets(page: str) -> set[str]:
    """
    Everything this page can navigate its own frame to — anchors and
    script-driven navigation alike.
    """
    html = (STATIC / f"{page}.html").read_text()
    targets = set(re.findall(r'<a\b[^>]*\bhref="(/[^"]*)"', html))
    targets |= {m for m in _JS_NAV.findall(html) if m.startswith("/")}
    return targets


def _embeddable_closure() -> set[str]:
    """
    Every page that can end up in the shell's iframe.

    Seeded from what shell.js actually embeds, then followed transitively:
    a page an embedded page navigates its frame to is itself embedded.
    That second step is not decoration — it is the only reason inventory
    and memory are in scope, and both had to be discovered by a bug report
    rather than by anything checking.
    """
    shell_js = (STATIC / "shell.js").read_text()
    files = _route_files()
    frontier = set(re.findall(r"(?:embed|forceEmbedSrc):\s*'/static/([a-z-]+)\.html'", shell_js))
    seen: set[str] = set()
    while frontier:
        page = frontier.pop()
        if page in seen or not (STATIC / f"{page}.html").exists():
            continue
        seen.add(page)
        for target in _page_frame_targets(page):
            if target.startswith("/static/") and target.endswith(".html"):
                frontier.add(target[len("/static/"):-len(".html")])
            elif target in files and files[target] != "shell.html":
                frontier.add(files[target][: -len(".html")])
    return seen


class _BackLinkAudit(HTMLParser):
    """
    Collects every <a href="/..."> in a page, recording whether it sits
    inside (or is) an element carrying data-shell-back.

    Tracks depth rather than just "did we see the attribute", because the
    two existing markup shapes differ: grocery marks a wrapping <div>,
    everything else marks the anchor itself.
    """

    VOID = {"br", "img", "input", "hr", "meta", "link", "source", "area", "col"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.guarded_since: list[int] = []
        self.links: list[tuple[str, bool]] = []
        self.markers: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in self.VOID:
            self.depth += 1
            if "data-shell-back" in attrs:
                self.guarded_since.append(self.depth)
                self.markers.append(attrs["data-shell-back"])
        if tag == "a" and attrs.get("href", "").startswith("/"):
            self.links.append((attrs["href"], bool(self.guarded_since)))

    def handle_endtag(self, tag):
        if self.depth > 0:
            self.depth -= 1
        while self.guarded_since and self.guarded_since[-1] > self.depth:
            self.guarded_since.pop()


def test_the_derivations_are_not_silently_empty():
    """
    The guard on the guard.

    Every other test here filters against a derived set. If a derivation
    returns nothing — because main.py or shell.js was reformatted in a way
    the regex no longer matches — those tests keep passing while checking
    nothing at all. That has been demonstrated, not imagined: hoisting the
    shell path into a local variable in main.py empties _shell_routes()
    and makes this entire file pass with a real doubled-shell bug present.
    """
    routes = _shell_routes()
    assert routes, "no shell routes derived from app/main.py — the regex has gone stale"
    assert "/" in routes, f"'/' must serve the shell; derived {sorted(routes)}"
    assert "/cooker" in routes, (
        "/cooker redirects into /kitchen and so is a shell route; the redirect "
        "resolution has stopped working"
    )
    assert _embeddable_closure(), "no embeddable pages derived from static/shell.js"
    assert _route_files(), "no page routes derived from app/main.py"


@pytest.mark.parametrize("page", EMBEDDABLE)
def test_every_embeddable_page_loads_the_shared_guard(page):
    """
    Including one script is the entire contract for a new embedded page.
    If this fails, that page will show a doubled shell the first time
    someone puts a top-level link on it.
    """
    html = (STATIC / f"{page}.html").read_text()
    assert GUARD_SCRIPT in html, (
        f"static/{page}.html can be embedded in the shell but does not load "
        f'{GUARD_SCRIPT}. Add <script src="{GUARD_SCRIPT}"></script> before </body>.'
    )


@pytest.mark.parametrize("page", EMBEDDABLE)
def test_no_embeddable_page_has_an_unguarded_link_to_a_shell_route(page):
    """
    The bug itself, stated directly.

    A link to a shell route, on a page that can be embedded, without a
    data-shell-back marker, is the doubled-shell bug — whether or not
    anyone has noticed it yet.
    """
    shell_routes = _shell_routes()
    audit = _BackLinkAudit()
    audit.feed((STATIC / f"{page}.html").read_text())

    unguarded = sorted(
        {href for href, guarded in audit.links if href in shell_routes and not guarded}
    )
    assert not unguarded, (
        f"static/{page}.html links to shell route(s) {unguarded} with no "
        f"data-shell-back marker. Inside the shell's iframe those load a "
        f"second copy of the whole app. Mark the link with "
        f'data-shell-back="hide" (the shell tab bar is already the way back) '
        f'or data-shell-back="/static/<page>.html" (to go back within the frame).'
    )


@pytest.mark.parametrize("page", EMBEDDABLE)
def test_no_embeddable_page_navigates_by_script_to_a_shell_route(page):
    """
    The same bug, written in JavaScript instead of markup.

    data-shell-back cannot help here — there is no element to mark — so the
    fix is to navigate to the sibling page directly (/static/<page>.html)
    rather than through a top-level route. This matters most on the Kitchen
    hub, which navigates *only* this way.
    """
    shell_routes = _shell_routes()
    html = (STATIC / f"{page}.html").read_text()
    offenders = sorted({t for t in _JS_NAV.findall(html) if t in shell_routes})
    assert not offenders, (
        f"static/{page}.html navigates its own frame to shell route(s) "
        f"{offenders} from script. Inside the shell that loads a second copy "
        f"of the whole app. Navigate to /static/<page>.html instead, which "
        f"stays inside the frame."
    )


@pytest.mark.parametrize("page", EMBEDDABLE)
def test_shell_back_markers_are_values_the_guard_understands(page):
    """
    A typo here fails silently and looks fine to every other test.

    embedded-page.js treats anything that is not exactly "hide" as a
    replacement href, so data-shell-back="Hide" sets href="Hide" (a 404)
    and an empty value sets href="" (reloads the page inside the frame).
    Both would otherwise pass as "guarded".
    """
    audit = _BackLinkAudit()
    audit.feed((STATIC / f"{page}.html").read_text())
    bad = [m for m in audit.markers if m != "hide" and not (m or "").startswith("/static/")]
    assert not bad, (
        f'static/{page}.html has data-shell-back={bad!r}. It must be exactly '
        f'"hide", or a /static/... path to rewrite the link to. Anything else '
        f"is used verbatim as an href and silently produces a broken link."
    )


def test_the_embeddable_list_matches_the_app():
    """
    Keeps EMBEDDABLE honest.

    The failure this whole file exists to prevent is a new embedded page
    nobody remembered to guard — so a page becoming embeddable without
    appearing here has to be a test failure, not a silent pass. Computed as
    a closure, so a page reached only via the Kitchen hub's own frame
    navigation counts, which is how inventory and memory got here.
    """
    missing = sorted(_embeddable_closure() - set(EMBEDDABLE))
    assert not missing, (
        f"{missing} can now end up inside the shell's iframe but EMBEDDABLE in "
        f"this file does not cover them, so nothing is checking them for the "
        f"doubled-shell bug. Add them to EMBEDDABLE."
    )


def test_the_guard_supports_both_existing_behaviours():
    """
    The two behaviours differ on purpose and must not be flattened into
    one: grocery and cooker HIDE their back link (the shell's tab bar is
    already the way back), while inventory and memory REWRITE theirs to a
    sibling page, because they are pushed views that still need a way out.

    Collapsing them would either strand the Kitchen sub-pages with no back
    link, or leave grocery and cooker showing a redundant one.
    """
    guard = (STATIC / "embedded-page.js").read_text()
    assert "style.display = 'none'" in guard, "the hide behaviour is missing"
    assert "setAttribute('href'" in guard, "the rewrite behaviour is missing"

    for page in ("grocery", "cooker"):
        html = (STATIC / f"{page}.html").read_text()
        assert 'data-shell-back="hide"' in html, f"{page}.html should hide its back link"

    for page in ("inventory", "memory"):
        html = (STATIC / f"{page}.html").read_text()
        assert 'data-shell-back="/static/kitchen.html"' in html, (
            f"{page}.html should rewrite its back link to stay inside the frame"
        )


@pytest.mark.parametrize("page", EMBEDDABLE)
def test_the_hand_copied_guards_are_gone(page):
    """
    The point of the shared script is that there is exactly one copy of
    this logic. A page reintroducing its own inline check has quietly
    forked it again, which is how this became four copies in the first
    place.
    """
    html = (STATIC / f"{page}.html").read_text()
    assert "window.self !== window.top" not in html, (
        f"static/{page}.html has its own inline iframe guard again — use "
        f"data-shell-back and the shared {GUARD_SCRIPT} instead."
    )
