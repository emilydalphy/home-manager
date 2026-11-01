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
so a household's recorded acceptance says which words they saw.

A bump asks everyone to agree again (Emily, 2026-11-01: "when the terms
change to a new version, everyone is asked to agree again"). Agreement is
recorded per adult (member_legal_acceptances), and the shell puts an agree
screen up before anything else for an adult whose latest agreement is older
than LEGAL_VERSION — `needs_agreement`. Including the adults who never
agreed to anything (signed in by passphrase, or before the join screen
asked): they are taken to have agreed to REASK_FROM, the version that was
live when re-asking was built, so shipping this asked nobody; the first
bump past it asks them. Versions are ISO dates, so "newer" is a plain
string comparison.
"""
from __future__ import annotations

import os

from . import backup

# The version every page shows, and the one sign-up records. A date, so a
# person reading "Version 2026-10-06" knows how old the words are.
LEGAL_VERSION = "2026-10-09"
# Said the way a person would read it, for the line under each title.
LEGAL_DATE_LABEL = "October 9, 2026"

# The version live on 2026-11-01, when re-asking was built. Nobody has to
# agree again to THIS one (or anything older): adults with no agreement on
# record are taken to have agreed to it. Never change it — it is a fact
# about the past; bump LEGAL_VERSION instead.
REASK_FROM = "2026-10-09"


def needs_agreement(agreed_version: str | None) -> bool:
    """Should an adult whose latest agreement is `agreed_version` (None for
    never) be asked to agree to LEGAL_VERSION before using the app?"""
    return LEGAL_VERSION > max(agreed_version or "", REASK_FROM)

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
# How long the daily backups keep a deleted household (app/backup.py), so
# the privacy policy and the support page can't drift from the code that
# prunes them. Two numbers on purpose: each copy is kept RETENTION_DAYS, and
# a copy taken on the day a household is deleted is pruned on the morning
# RETENTION_DAYS + 1 days later (prune_backups keeps a copy dated exactly
# at the cutoff) — "up to 15 days" is that second number, not a typo.
_BACKUP_KEEP_SLOT = "__BACKUP_KEEP_DAYS__"
_BACKUP_GONE_SLOT = "__BACKUP_GONE_WITHIN_DAYS__"


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
    html = html.replace(_BACKUP_KEEP_SLOT, str(backup.RETENTION_DAYS))
    html = html.replace(_BACKUP_GONE_SLOT, str(backup.RETENTION_DAYS + 1))
    return html
