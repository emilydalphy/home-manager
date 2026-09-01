"""
Values every domain module needs.

HOUSEHOLD_ID lives here and nowhere else. V1 is a single household, so it
is still the constant 1 -- but it is now imported from one place instead of
being retyped in every query, which is what makes the multi-household work
a change to this file rather than a sweep of the whole codebase.
"""
from __future__ import annotations

import os


HOUSEHOLD_ID = 1


# The app's own public URL, so a tool can hand back a real, absolute link
# (e.g. for the Eater self-service link) instead of the chat agent having
# to guess/type out a domain itself — which it has no way to know and will
# otherwise hallucinate. Set via Railway (or wherever this is hosted) env
# vars, e.g. PUBLIC_BASE_URL=https://home-manager-production-4949.up.railway.app
# (no trailing slash). Falls back to a relative path if unset (e.g. local
# dev), which still works fine since the app only has one host there.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _absolute_url(path: str) -> str:
    return f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path
