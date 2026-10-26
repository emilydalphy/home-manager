"""
The privacy policy, terms and support pages (Loop Board "App Store: privacy
policy, terms and support pages in the app (drafts for the lawyer)",
2026-10-06). app/legal.py, static/legal/.

What the card asks a test to hold: the pages answer without a session, the
draft banner follows LEGAL_PAGES_FINAL, and the links to them are where
people will look (sign-in, Preferences → About).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import legal, security
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["/privacy", "/terms", "/support"]


def _remote() -> TestClient:
    # A client that does NOT look like localhost: "testclient" is in
    # security._LOCAL_HOSTS, and a local request is let through when no
    # passphrase is set. A real phone signed out of nothing is a remote
    # client with no cookie, which is what this is.
    return TestClient(app, base_url="http://pomona.test", client=("203.0.113.9", 5555))


@pytest.mark.parametrize("path", PAGES)
def test_each_page_answers_without_a_session(path):
    res = _remote().get(path, headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 200, res.text[:200]
    assert res.headers["content-type"].startswith("text/html")
    assert security.is_public_path(path)
    # Filled in, every placeholder: a page that shows "__LEGAL_VERSION__" to
    # Apple's reviewer is worse than one with no version at all.
    assert "__LEGAL" not in res.text and "LEGAL_BANNER" not in res.text
    assert f"Version {legal.LEGAL_VERSION}" in res.text


def test_the_pages_stylesheet_and_fonts_load_without_a_session():
    client = _remote()
    assert client.get("/static/legal.css").status_code == 200
    assert client.get("/static/theme.css").status_code == 200


def test_a_signed_out_app_route_still_asks_for_sign_in():
    """The new public paths open three pages, not the app around them."""
    res = _remote().get("/api/whoami", follow_redirects=False)
    assert res.status_code == 401


@pytest.mark.parametrize("final, shown", [(None, True), ("0", True), ("yes", True), ("1", False)])
def test_the_draft_banner_follows_the_setting(monkeypatch, final, shown):
    if final is None:
        monkeypatch.delenv("LEGAL_PAGES_FINAL", raising=False)
    else:
        monkeypatch.setenv("LEGAL_PAGES_FINAL", final)
    for path in PAGES:
        text = _remote().get(path).text
        assert (legal.DRAFT_BANNER_TEXT in text) is shown, (path, final)


def test_terms_carry_the_allergy_sentence():
    """The Legal card: 'this sentence matters'."""
    text = _remote().get("/terms").text
    assert "The allergy check is a best-effort aid, not a guarantee." in text
    assert "not medical, nutritional or dietary advice" in text


def test_privacy_names_what_the_card_requires():
    text = _remote().get("/privacy").text.replace("&rsquo;", "'")
    for must in ("Anthropic", "taps Allow", "No ads", "when your home is empty",
                 "Delete your household", "PIPEDA", "California", "Children", "served from its own server"):
        assert must in text, must


def test_sign_in_links_to_all_three():
    html = _remote().get("/login").text
    for path in PAGES:
        assert f'href="{path}"' in html, path


def test_preferences_about_links_to_all_three_and_shows_the_version(signed_in):
    shell = (ROOT / "static" / "shell.js").read_text(encoding="utf-8")
    start = shell.index("function prefsAboutHtml()")
    about = shell[start:start + 1200]
    for path in PAGES:
        assert f"href=\"{path}\"" in about, path
    assert "prefsAboutHtml()" in shell[:start], "the About block is never drawn"
    assert signed_in.get("/api/whoami").json()["legal_version"] == legal.LEGAL_VERSION


def test_only_the_three_pages_are_ever_read():
    with pytest.raises(ValueError):
        legal.render("../login")


def test_the_backup_days_are_the_ones_the_backups_keep(monkeypatch, tmp_path):
    """The privacy policy and the support page say how long a deleted
    household lingers in the daily backups; that is app/backup.py's
    RETENTION_DAYS, and a copy taken on the day of deleting goes one day
    after that (prune_backups keeps a copy dated at the cutoff)."""
    from datetime import datetime, timedelta

    from app import backup

    privacy, support = legal.render("privacy"), legal.render("support")
    assert f"keep each copy for {backup.RETENTION_DAYS} days" in privacy
    assert f"for up to {backup.RETENTION_DAYS + 1} days" in privacy
    assert f"keep a copy for up to {backup.RETENTION_DAYS + 1} days" in support
    assert "__BACKUP_" not in privacy + support

    # And that second number is what pruning actually does.
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path))
    taken = datetime(2026, 10, 1, 3, 0)
    name = backup.backup_path_for(taken)

    def survives(days_later):
        Path(name).write_bytes(b"x")
        backup.prune_backups(now=taken + timedelta(days=days_later))
        return Path(name).exists()
    assert survives(backup.RETENTION_DAYS) and not survives(backup.RETENTION_DAYS + 1)


# --- The privacy page matches Apple (Loop Board B3, 2026-10-26) -------------

import html as _html
import re as _re

# The blanks only Emily can fill: who runs Pomona and how to reach them. The
# no-blanks test below SKIPS while exactly these remain, naming them, and
# goes quiet on its own the day they're filled in.
COMPANY_BLANKS = {
    "[Company legal name]",
    "[registered address]",
    "[privacy contact email]",
    "[support email]",
}
# The lawyer's notes, which stay in the draft on purpose: anything starting
# "[Lawyer", plus these. A new bracket that is neither of these lists is a
# blank somebody forgot, and fails.
DRAFT_NOTES = {
    "[Confirm the hosting region.]",
    "[Name the provider.]",
    "[18, or the age of majority where you live]",
    "[Pricing and subscription terms go here once a price is set: what it costs, how billing works through Apple, cancelling, and refunds.]",
    "[amount, for example what you paid us in the last 12 months]",
}
SERVED = PAGES + ["/login"]


def _visible_text(page_html: str) -> str:
    page_html = _re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page_html)
    page_html = _re.sub(r"(?s)<!--.*?-->", " ", page_html)
    return _html.unescape(_re.sub(r"(?s)<[^>]+>", "", page_html))


def _blanks(page_html: str) -> list[str]:
    return _re.findall(r"\[[^\[\]]+\]", _visible_text(page_html))


def test_no_company_blank_is_left_in_the_served_pages():
    """CATCH. A reviewer never reads "[Company legal name]". Skips (naming
    what Emily must supply) while only the known company blanks remain;
    fails on any other blank, and on any blank not drawn as a legal-fill."""
    waiting: dict[str, list[str]] = {}
    for path in SERVED:
        raw = _remote().get(path).text
        found = _blanks(raw)
        unknown = [b for b in found
                   if b not in COMPANY_BLANKS and b not in DRAFT_NOTES
                   and not b.startswith("[Lawyer")]
        assert not unknown, (path, unknown)
        # Every blank is drawn as one, so nobody mistakes it for real copy.
        filled = _html.unescape(" ".join(_re.findall(r'<span class="legal-fill">(\[[^<]*\])</span>', raw)))
        assert sorted(found) == sorted(_re.findall(r"\[[^\[\]]+\]", filled)), path
        left = sorted({b for b in found if b in COMPANY_BLANKS})
        if left:
            waiting[path] = left
    if waiting:
        pytest.skip("Waiting on Emily for who runs Pomona: "
                    + "; ".join(f"{p} {', '.join(b)}" for p, b in waiting.items()))


def test_the_no_blanks_check_fails_on_a_blank_nobody_listed():
    """GUARD. The skip above is for the company blanks only, not any bracket."""
    page = '<p>Ask <span class="legal-fill">[Pet name]</span>.</p><script>a[i]</script>'
    assert _blanks(page) == ["[Pet name]"]
    assert "[Pet name]" not in COMPANY_BLANKS | DRAFT_NOTES


def test_privacy_and_support_say_what_the_app_store_label_declares():
    """CATCH. The label and the manifest declare Product Interaction for
    Analytics (app/tools/usage.py's counts), so the pages say "No
    third-party analytics", never "No analytics", and say what's counted."""
    for path in ("/privacy", "/support"):
        text = _visible_text(_remote().get(path).text)
        assert "no third-party analytics" in text.lower(), path
        assert not _re.search(r"(?i)\bno analytics\b", text), path
    privacy = _visible_text(_remote().get("/privacy").text)
    assert "Pomona counts its own use (chat turns, meals cooked) to see what’s working." in privacy

    manifest = (ROOT / "ios-app" / "ios" / "App" / "App" / "PrivacyInfo.xcprivacy").read_text()
    pi = manifest.index("NSPrivacyCollectedDataTypeProductInteraction</string>")
    assert "NSPrivacyCollectedDataTypePurposeAnalytics" in manifest[pi:pi + 500]
    assert _re.search(r"<key>NSPrivacyTracking</key>\s*<false/>", manifest)
    listing = (ROOT / "docs" / "APP_STORE_LISTING.md").read_text()
    assert "| Usage Data | Product Interaction | Counts of chat turns and meals cooked" in listing
    assert listing.split("| Usage Data | Product Interaction |", 1)[1].split("\n", 1)[0].rstrip().endswith("| Analytics |")


def test_the_consent_screens_read_more_lands_on_the_claude_section():
    """CATCH. "Sharing with Claude" points at Pomona's own policy, at the
    section about Anthropic — both copies (shell and onboarding)."""
    for f, decl in (("shell.js", "var AI_CONSENT_COPY = "), ("onboarding.html", "const AI_CONSENT_COPY = ")):
        src = (ROOT / "static" / f).read_text(encoding="utf-8")
        block = src[src.index(decl):src.index("};", src.index(decl))]
        assert "linkUrl: '/privacy#claude'," in block, f
        assert "privacy.claude.com" not in block, f
    assert '<h3 id="claude">Anthropic (the AI)</h3>' in _remote().get("/privacy").text
