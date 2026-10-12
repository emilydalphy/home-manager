"""
The privacy policy, terms and support pages (Loop Board "App Store: privacy
policy, terms and support pages in the app (drafts for the lawyer)",
2026-10-06).

Apple asks for a privacy policy URL and a support URL before an app can be
listed, and a stranger signing up needs to be able to read what they are
agreeing to before they have an account. So these three pages are PUBLIC
(app/security.py lists them) and read nothing from anyone: every word is in
static/legal/*.html, and the only things put into them at request time are
the version, its date, and the draft banner.

They are DRAFTS. Emily's lawyer reviews them before the App Store listing
goes in, and until then every page says so at the top. One setting turns
the banner off once counsel has signed off:

    LEGAL_PAGES_FINAL=1     the banner goes; anything else (or unset) keeps it

Kept as a setting rather than a code change so the switch is Emily's to
flip in Railway on the day, without a deploy.

LEGAL_VERSION is what sign-up records as accepted (the email sign-up card).
Bump it whenever the substance of the privacy policy or the terms changes,
so a household's recorded acceptance says which words they saw. Whether a
bump should ask existing households to accept again is Emily's call at the
time — nothing here does that on its own.
"""
from __future__ import annotations

import os

# The version every page shows, and the one sign-up records. A date, so a
# person reading "Version 2026-10-06" knows how old the words are.
LEGAL_VERSION = "2026-10-09"
# Said the way a person would read it, for the line under each title.
LEGAL_DATE_LABEL = "October 9, 2026"

PAGES = ("privacy", "terms", "support")

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "legal")

# What the draft banner says — the card's exact words.
DRAFT_BANNER_TEXT = "Draft for legal review, not yet final"
_BANNER_HTML = (
    '<p class="legal-draft" role="note">'
    '<span class="legal-draft-eyebrow">Draft</span>'
    f"<span>{DRAFT_BANNER_TEXT}</span>"
    "</p>"
)

# Placeholders in the templates. Plain str.replace, the same way login.html
# is filled in — so none of these may be written anywhere else in a page,
# comments included, or that copy gets filled in too.
_BANNER_SLOT = "<!--LEGAL_BANNER-->"
_VERSION_SLOT = "__LEGAL_VERSION__"
_DATE_SLOT = "__LEGAL_DATE__"


def is_final() -> bool:
    """Has counsel signed off? Only an explicit "1" says yes — a typo or a
    missing variable keeps the draft banner, which is the safe way to be
    wrong about a legal page."""
    return os.environ.get("LEGAL_PAGES_FINAL", "").strip() == "1"


def render(page: str) -> str:
    """The page's HTML with the version, date and (while a draft) the banner
    filled in. Only the three names above are ever read from disk; anything
    else is a ValueError."""
    if page not in PAGES:
        raise ValueError(page)
    with open(os.path.join(_STATIC_DIR, f"{page}.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace(_BANNER_SLOT, "" if is_final() else _BANNER_HTML)
    html = html.replace(_VERSION_SLOT, LEGAL_VERSION)
    html = html.replace(_DATE_SLOT, LEGAL_DATE_LABEL)
    return html
