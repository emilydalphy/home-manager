"""
Pomona loads no third-party resources: the App Store privacy label and the
privacy policy both say so. Fonts used to come from Google's servers, which
told Google the IP address of everyone who opened any page (sign-in and the
public share pages included). They are self-hosted under static/fonts/ now;
this fails if a Google font host, or any other off-site stylesheet, script,
font or @import, creeps back into a page we ship.
"""
import re
from pathlib import Path

from app import security
from app.main import app  # noqa: F401  (imported so the app's routes load)

ROOT = Path(__file__).resolve().parent.parent
SCANNED = [ROOT / "static", ROOT / "app"]
TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".py", ".webmanifest"}
BANNED_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")
# An off-site load: a <link>/<script>/<img>/<source>/<iframe> pointing at
# http(s)://, an @import, or a CSS url() to another host.
OFFSITE = re.compile(
    r"""(?:<(?:link|script|img|source|iframe|video|audio)\b[^>]*?\b(?:href|src)\s*=\s*["']\s*(?:https?:)?//"""
    r"""|@import\s+(?:url\()?\s*["']?(?:https?:)?//"""
    r"""|url\(\s*["']?(?:https?:)?//)""",
    re.I,
)


def _files():
    for base in SCANNED:
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts:
                yield path


def test_no_static_or_app_file_names_a_google_font_host():
    hits = [
        f"{p.relative_to(ROOT)}: {host}"
        for p in _files()
        for host in BANNED_HOSTS
        if host in p.read_text(errors="ignore")
    ]
    assert not hits, "third-party font host referenced again:\n" + "\n".join(hits)


def test_no_page_loads_an_offsite_stylesheet_script_font_or_image():
    hits = []
    for p in _files():
        text = p.read_text(errors="ignore")
        for m in OFFSITE.finditer(text):
            hits.append(f"{p.relative_to(ROOT)}: {m.group(0)[:80]}")
    assert not hits, "off-site resource loads:\n" + "\n".join(hits)


def test_every_font_file_fonts_css_points_at_exists():
    css = (ROOT / "static/fonts/fonts.css").read_text()
    urls = re.findall(r"url\((/static/fonts/[^)]+)\)", css)
    assert urls, "fonts.css declares no files"
    for u in urls:
        assert (ROOT / u.lstrip("/")).is_file(), f"fonts.css points at a missing file: {u}"
    assert "font-display" in css
    assert (ROOT / "static/fonts/LICENSE.md").is_file()


def test_every_html_page_uses_the_local_fonts_stylesheet():
    for p in (ROOT / "static").glob("*.html"):
        text = p.read_text()
        if "font-family" in text or "theme.css" in text or "fonts.css" in text:
            assert "/static/fonts/fonts.css" in text, f"{p.name} does not load the self-hosted fonts"


def test_fonts_are_public_so_sign_in_can_use_them_without_a_session():
    for path in ("/static/fonts/fonts.css", "/static/fonts/figtree-normal-latin.woff2"):
        assert security.is_public_path(path), path
    assert not security.is_public_path("/static/fonts/../shell.js")
